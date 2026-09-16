"""§3f V-taper: WCR from body_metrics + the Hevy body_measurements import.

WCR = waist ÷ chest from ONE row (same tape session); target ≤0.7 (Garza 2017,
product-approved — explicitly NOT the 1.618 golden ratio). import-hevy-body is
source-scoped like every Hevy reload: manual rows always survive.
"""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "health.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return p


def run(db, *args, expect_ok=True):
    r = subprocess.run([sys.executable, str(HEALTH), *args],
                       env={**os.environ, "HEALTH_DB": str(db)},
                       capture_output=True, text=True)
    if expect_ok:
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout) if r.stdout.strip() else {}
    assert r.returncode != 0
    return r.stderr or r.stdout


def body_row(db, date, **cols):
    con = sqlite3.connect(db)
    keys = ",".join(cols)
    ph = ",".join("?" * len(cols))
    con.execute(f"INSERT INTO body_metrics(date,{keys}) VALUES(?,{ph})",
                (date, *cols.values()))
    con.commit()
    con.close()


def payload(tmp_path, items):
    p = tmp_path / "body.json"
    p.write_text(json.dumps({"body_measurements": items}))
    return str(p)


# ── vtaper read ──────────────────────────────────────────────────────────────

def test_vtaper_insufficient_until_paired(db):
    r = run(db, "vtaper")
    assert r["insufficient_data"] and r["target_wcr"] == 0.7


def test_vtaper_requires_same_row_pairing(db):
    # waist one day, chest another — NOT a ratio; must stay insufficient
    body_row(db, "2026-07-01", waist_cm=88)
    body_row(db, "2026-07-05", chest_cm=110)
    assert run(db, "vtaper")["insufficient_data"]


def test_vtaper_current_trend_and_target(db):
    body_row(db, "2026-06-01", waist_cm=90, chest_cm=100)     # 0.9
    body_row(db, "2026-07-01", waist_cm=84, chest_cm=105)     # 0.8
    r = run(db, "vtaper")
    assert r["current"]["wcr"] == 0.8 and r["current"]["date"] == "2026-07-01"
    assert r["at_target"] is False and r["delta_to_target"] == 0.1
    assert r["trend"] == -0.1 and r["n_measurements"] == 2
    assert "Garza" in r["cite"] and "1.618" in r["note"]


def test_vtaper_at_target_and_latest_row_per_date_wins(db):
    body_row(db, "2026-07-01", waist_cm=80, chest_cm=100)     # earlier row (0.8)
    body_row(db, "2026-07-01", waist_cm=70, chest_cm=100)     # corrected tape (0.7)
    r = run(db, "vtaper")
    assert r["current"]["wcr"] == 0.7 and r["at_target"] is True
    assert r["trend"] is None                                  # one date = no trend


# ── import-hevy-body ─────────────────────────────────────────────────────────

def test_import_maps_hevy_field_names(db, tmp_path):
    # exact OpenAPI field names (BodyMeasurement schema, verified 2026-07-10):
    # bare waist/hips/fat_percent, suffixed chest_cm/neck_cm, plain ISO date
    run(db, "import-hevy-body", payload(tmp_path, [
        {"date": "2026-07-01", "waist": 88.5, "chest_cm": 110.0,
         "weight_kg": 93.2, "hips": 101.0, "fat_percent": 22.5}]))
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    row = dict(con.execute("SELECT * FROM body_metrics").fetchone())
    assert row["waist_cm"] == 88.5 and row["chest_cm"] == 110.0
    assert row["hip_cm"] == 101.0 and row["body_fat_pct"] == 22.5
    assert row["source"] == "hevy"
    wcr = run(db, "vtaper")
    assert wcr["current"]["wcr"] == 0.805


def test_import_never_deletes_updates_in_place(db, tmp_path):
    """Law #3: body_metrics is a log table — the sync must UPDATE its own rows,
    never delete-and-reload (the hevy_sets exception does not extend here)."""
    body_row(db, "2026-06-15", waist_cm=90, chest_cm=100)      # manual tape
    run(db, "import-hevy-body", payload(tmp_path, [{"date": "2026-07-01", "waist": 85, "chest_cm": 108}]))
    con = sqlite3.connect(db)
    first_id = con.execute("SELECT id FROM body_metrics WHERE source='hevy'").fetchone()[0]
    con.close()
    # nightly re-sync: corrected value for the same date + one new date
    r = run(db, "import-hevy-body", payload(tmp_path, [
        {"date": "2026-07-01", "waist": 86, "chest_cm": 108},
        {"date": "2026-07-02", "waist": 84, "chest_cm": 108}]))
    assert r["updated"] == 1 and r["inserted"] == 1
    con = sqlite3.connect(db)
    manual = con.execute("SELECT COUNT(*) FROM body_metrics WHERE source='manual'").fetchone()[0]
    hevy = con.execute("SELECT id, waist_cm FROM body_metrics WHERE source='hevy' ORDER BY date").fetchall()
    assert manual == 1
    assert len(hevy) == 2
    assert hevy[0][0] == first_id and hevy[0][1] == 86          # same row, refreshed value
    # empty payload is a harmless no-op, not a wipe
    r = run(db, "import-hevy-body", payload(tmp_path, []))
    assert r["ok"] and r["dates"] == 0
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM body_metrics").fetchone()[0] == 3


def test_vtaper_manual_row_beats_hevy_same_day(db, tmp_path):
    """A manual tape entry is the owner's correction — the nightly sync must
    not shadow it, whatever the row ids say."""
    run(db, "import-hevy-body", payload(tmp_path, [{"date": "2026-07-01", "waist": 90, "chest_cm": 100}]))
    body_row(db, "2026-07-01", waist_cm=80, chest_cm=100)       # manual correction
    run(db, "import-hevy-body", payload(tmp_path, [{"date": "2026-07-01", "waist": 90, "chest_cm": 100}]))
    assert run(db, "vtaper")["current"]["wcr"] == 0.8           # manual wins


def test_import_skips_out_of_range_values(db, tmp_path):
    r = run(db, "import-hevy-body", payload(tmp_path, [
        {"date": "2026-07-01", "waist": 8800, "chest_cm": 110}]))   # waist absurd
    assert r["skipped_values_out_of_range"] == 1
    con = sqlite3.connect(db)
    w, ch = con.execute("SELECT waist_cm, chest_cm FROM body_metrics").fetchone()
    assert w is None and ch == 110                              # kept the sane field


def test_import_accepts_alternate_spellings(db, tmp_path):
    # tolerance beyond the spec: a rename (chest_cm->chest, waist->waist_cm)
    # must degrade to nothing worse than business as usual
    run(db, "import-hevy-body", payload(tmp_path, [
        {"date": "2026-07-03", "waist_cm": 86.0, "chest": 108.0}]))
    assert run(db, "vtaper")["current"]["wcr"] == 0.796


def test_import_and_vtaper_refuse_missing_source_without_ddl(db, tmp_path):
    """Both paths defer guarded-column ownership to explicit Migration 001."""
    con = sqlite3.connect(db)
    con.executescript("""
        DROP TABLE body_metrics;
        CREATE TABLE body_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT,
          weight_kg REAL, waist_cm REAL, chest_cm REAL, arm_cm REAL, thigh_cm REAL,
          hip_cm REAL, neck_cm REAL, body_fat_pct REAL, photo_ref TEXT, notes TEXT,
          ingested_at TEXT DEFAULT (datetime('now')));
        INSERT INTO body_metrics(date, waist_cm, chest_cm) VALUES('2026-07-01', 88, 110);
    """)
    con.commit(); con.close()
    assert "schema_migration_required" in run(db, "vtaper", expect_ok=False)
    assert "schema_migration_required" in run(
        db, "import-hevy-body",
        payload(tmp_path, [{"date": "2026-07-02", "waist": 87, "chest_cm": 110}]),
        expect_ok=False,
    )
    con = sqlite3.connect(db)
    cols = {row[1] for row in con.execute("PRAGMA table_info(body_metrics)")}
    assert "source" not in cols
    assert con.execute("SELECT COUNT(*) FROM body_metrics").fetchone()[0] == 1
    con.close()
