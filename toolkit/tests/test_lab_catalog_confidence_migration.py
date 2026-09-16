"""Explicit, lossless migration of the two known lab confidence lineages."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hermes_insights import migrations as m
from test_provider_neutral_synthesis_migration import (
    _insert_legacy_synthesis, _legacy_phase5_schema,
)

CODE = '7' * 40
CURRENT_SCHEMA = (ROOT / 'SCHEMA.sql').read_text()


def connection(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def replace_catalog(conn, ddl):
    # Fixture construction only; production changes use migrate().
    conn.execute('DROP TABLE lab_catalog')
    conn.execute(ddl)


def database(tmp_path, *, legacy=False, version=6):
    path = tmp_path / 'health.db'
    schema = _legacy_phase5_schema(CURRENT_SCHEMA)
    schema, count = re.subn(
        r'CREATE TABLE lab_catalog \(.*?\);',
        m.LAZY_TABLE_DDL['lab_catalog'], schema, count=1, flags=re.S,
    )
    assert count == 1
    conn = connection(path)
    conn.executescript(schema)
    if version < 5:
        conn.execute('DROP TABLE recipe_restock_state')
    conn.commit(); conn.close()
    m.migrate(str(path), version, 0, CODE)
    conn = connection(path)
    if legacy:
        replace_catalog(conn, m.LEGACY_LAB_CATALOG_DDL)
        conn.execute('UPDATE schema_migrations SET checksum_sha256=? WHERE version=1',
                     (m.LEGACY_MIGRATION_001_CHECKSUM,))
    conn.commit(); conn.close()
    return path


def seed_catalog(conn, confidence):
    row = ('Fictional marker', 'Fictional display', 'Fixture panel', 'mg/L',
           None, 15.0, 0.0, 100.0, 5.0, 'abs', '["Fixture alias"]',
           confidence, confidence, '2026-01-01T00:00:00Z')
    assert len(row) == 14
    conn.execute('INSERT INTO lab_catalog VALUES(' + ','.join('?' * 14) + ')', row)
    conn.commit()


def rows_by_table(conn):
    return {row[0]: Counter(tuple(item) for item in conn.execute(
        'SELECT * FROM "' + row[0].replace('"', '""') + '"'
    )) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def test_frozen_migrations_and_exact_legacy_identity():
    expected = (
        '77c63d3fa7aa130a54733d0e65cd992a499999227022b16bc17c335df92dd118',
        '864a5620b88f75dd41da260d16bda5fedbd626d6ade675879977ff0440f54644',
        'e1fdada7243d4a0810d52b4156444e4a1c9efe8530a6ba6eebf1856599767770',
        '381006f8a4ea255062a9d8f922d7e30c0bd5dbfc94d47e031c2f107989718229',
        '51033f434a50a6169d619bf00c6702dee158accd21cf94310a4a034a68622545',
        '55137d10cf7e860df8d09b917eb738f01049cb113259db62b8d7275042319c34',
    )
    assert tuple(m.MIGRATION_BY_VERSION[v].checksum for v in range(1, 7)) == expected
    legacy = m.MIGRATION_001_SQL.replace(
        "CHECK(confidence IN ('cited','user-confirmed'))",
        "CHECK(confidence IN ('cited','owner-confirmed'))",
    )
    assert hashlib.sha256(legacy.encode()).hexdigest() == m.LEGACY_MIGRATION_001_CHECKSUM
    assert m.MIGRATION_BY_VERSION[7].name == '007_lab_catalog_confidence_history'
    assert m.MIGRATION_BY_VERSION[7].checksum == 'e6ef8e95a8021cf60ad46427e8eab1820ae8027cb41532e5b8084c3306295f0d'


@pytest.mark.parametrize('legacy,version', [(True, 4), (False, 6)])
def test_upgrade_preserves_all_rows_and_ledger_then_accepts_current_ingest(tmp_path, legacy, version):
    path = database(tmp_path, legacy=legacy, version=version)
    conn = connection(path)
    seed_catalog(conn, 'owner-confirmed' if legacy else 'user-confirmed')
    _insert_legacy_synthesis(conn)
    before = rows_by_table(conn); conn.close()
    assert m.schema_status(str(path))['current_version'] == version
    result = m.migrate(str(path), 7, version, CODE)
    assert [item['version'] for item in result['applied']] == list(range(version + 1, 8))
    conn = connection(path); after = rows_by_table(conn)
    for table, rows in before.items():
        if table == 'schema_migrations':
            assert Counter(row for row in after[table].elements() if row[0] <= version) == rows
        else:
            assert after[table] == rows
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='lab_catalog_v7'").fetchall()
    for label in ('cited', 'owner-confirmed', 'user-confirmed'):
        conn.execute('INSERT INTO lab_catalog(canonical,unit,plaus_low,plaus_high,source,confidence) VALUES(?,?,?,?,?,?)',
                     ('allowed-' + label, 'mg/L', 0, 100, 'fixture', label))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO lab_catalog(canonical,unit,plaus_low,plaus_high,source,confidence) VALUES('bad','mg/L',0,100,'fixture','unverified')")
    conn.rollback(); conn.close()
    report = 'Homocysteine           12.0      µmol/L    5 - 15            Normal\n'
    completed = subprocess.run(
        [sys.executable, str(ROOT/'health.py'), 'lab-ingest', '--date', '2026-03-14', '--commit', '--confirm', 'Homocysteine'],
        input=report, text=True, capture_output=True, timeout=30,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1', 'HEALTH_DB':str(path), 'HERMES_DATA_DIR':str(tmp_path)},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)['committed'] == 1
    conn = connection(path)
    assert tuple(conn.execute("SELECT source,confidence FROM lab_catalog WHERE canonical='Homocysteine'").fetchone()) == ('user-confirmed','user-confirmed')
    conn.close()


@pytest.mark.parametrize('target', [1, 5, 6])
def test_fresh_latest_schema_can_intentionally_stop_before_seven(tmp_path, target):
    path = tmp_path/'fresh.db'; conn = connection(path)
    conn.executescript(CURRENT_SCHEMA); conn.close()
    m.migrate(str(path), target, 0, CODE)
    status = m.schema_status(str(path))
    assert status['current_version'] == target
    assert status['status'] == 'pending'
    assert 7 in [item['version'] for item in status['pending']]
    conn = connection(path); seed_catalog(conn, 'owner-confirmed')
    before = rows_by_table(conn); conn.close()
    m.migrate(str(path), 7, target, CODE)
    conn = connection(path)
    assert rows_by_table(conn)['lab_catalog'] == before['lab_catalog']
    conn.close()
    assert m.schema_status(str(path))['status'] == 'up_to_date'


@pytest.mark.parametrize('variant', ['unknown_hash', 'wrong_name', 'legacy_with_current', 'legacy_with_latest', 'canonical_with_legacy', 'v7_with_current', 'v7_with_legacy', 'canonical_literal_case', 'canonical_default_case', 'v7_literal_case'])
def test_unknown_or_mismatched_profiles_fail_before_writes(tmp_path, variant):
    path = database(tmp_path)
    if variant.startswith('v7_'):
        m.migrate(str(path), 7, 6, CODE)
    conn = connection(path)
    if variant == 'unknown_hash':
        conn.execute("UPDATE schema_migrations SET checksum_sha256=? WHERE version=1", ('0'*64,))
    elif variant == 'wrong_name':
        conn.execute("UPDATE schema_migrations SET name='unrecognized' WHERE version=1")
    elif variant in {'canonical_literal_case', 'canonical_default_case', 'v7_literal_case'}:
        ddl = m.LAB_CATALOG_V7_DDL if variant.startswith('v7_') else m.LAZY_TABLE_DDL['lab_catalog']
        ddl = ddl.replace("DEFAULT 'cited'", "DEFAULT 'CITED'") if variant.endswith('default_case') else ddl.replace("'user-confirmed'", "'USER-CONFIRMED'")
        replace_catalog(conn, ddl)
    elif variant.startswith('legacy_'):
        conn.execute('UPDATE schema_migrations SET checksum_sha256=? WHERE version=1', (m.LEGACY_MIGRATION_001_CHECKSUM,))
        if variant.endswith('latest'):
            replace_catalog(conn, m.LAB_CATALOG_V7_DDL)
    else:
        replace_catalog(conn, m.LAZY_TABLE_DDL['lab_catalog'] if variant.endswith('current') else m.LEGACY_LAB_CATALOG_DDL)
    conn.commit(); conn.close(); before = path.read_bytes()
    with pytest.raises(m.SchemaError):
        m.schema_status(str(path))
    with pytest.raises(m.SchemaError):
        m.migrate(str(path), 7, 7 if variant.startswith('v7_') else 6, CODE)
    assert path.read_bytes() == before


@pytest.mark.parametrize('ddl', [
    'CREATE INDEX custom_catalog_index ON lab_catalog(display)',
    "CREATE TRIGGER custom_catalog_trigger AFTER INSERT ON lab_catalog BEGIN SELECT 1; END",
    'CREATE VIEW custom_catalog_view AS SELECT canonical FROM lab_catalog',
    'CREATE TABLE custom_reference(marker TEXT REFERENCES lab_catalog(canonical))',
    'CREATE TABLE custom_reference(marker TEXT REFERENCES LAB_CATALOG(canonical) ON DELETE CASCADE)',
    'CREATE TABLE lab_catalog_v7(unrelated TEXT)',
    'CREATE TABLE LAB_CATALOG_V7(unrelated TEXT)',
])
def test_unowned_dependencies_are_not_dropped(tmp_path, ddl):
    path = database(tmp_path); conn = connection(path)
    seed_catalog(conn, 'user-confirmed')
    conn.execute(ddl)
    if ddl.startswith('CREATE TABLE custom_reference'):
        conn.execute("INSERT INTO custom_reference VALUES('Fictional marker')")
    conn.commit(); conn.close(); before = path.read_bytes()
    with pytest.raises(m.SchemaError, match='lab catalog'):
        m.migrate(str(path), 7, 6, CODE)
    assert path.read_bytes() == before


@pytest.mark.parametrize('legacy', [False, True])
def test_failed_rebuild_restores_table_records_and_ledger(tmp_path, monkeypatch, legacy):
    path = database(tmp_path, legacy=legacy); conn = connection(path)
    seed_catalog(conn, 'owner-confirmed' if legacy else 'user-confirmed')
    conn.close(); before = path.read_bytes(); apply = m._apply_007
    def fail_after_rebuild(conn):
        apply(conn)
        raise RuntimeError('injected migration007 failure')
    monkeypatch.setattr(m, '_apply_007', fail_after_rebuild)
    with pytest.raises(RuntimeError, match='injected migration007 failure'):
        m.migrate(str(path), 7, 6, CODE)
    assert path.read_bytes() == before
    assert m.schema_status(str(path))['current_version'] == 6
