"""Fictional snapshot and corruption regressions for private analysis reuse."""

from dataclasses import replace
from datetime import date, datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from hermes_insights import exact_cache, runtime
from hermes_insights.contracts import AdapterContext


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "fictional.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE observations(id INTEGER PRIMARY KEY, value);
            INSERT INTO observations VALUES(1, 7.5);
            CREATE TABLE duplicate_rows(value);
            INSERT INTO duplicate_rows VALUES('same'), ('same');
        """)
    return path


@pytest.fixture
def context():
    return AdapterContext(today=date(2026, 7, 1), timezone="UTC",
                          constants={"target": 8}, functions={})


def _identity(database, context):
    return exact_cache.current_identity(database, context=context)


def _compute(database, context, function, *, request=None):
    connection = runtime.connect_read_only(database)
    try:
        connection.execute("BEGIN")
        return exact_cache.cached_compute(
            database, request or {"command": "fictional-test"}, function,
            connection=connection, context=context,
        )
    finally:
        connection.close()


def test_identical_request_reuses_result_and_database_remains_untouched(database, context):
    before = database.read_bytes()
    calls = []

    def calculate():
        calls.append(True)
        return {"value": 7.5, "evidence": ["fictional"]}

    first = _compute(database, context, calculate)
    # A caller mutating its first result must not mutate the persisted result.
    first["evidence"].append("caller-edit")
    assert _compute(database, context, calculate) == {
        "value": 7.5, "evidence": ["fictional"],
    }
    assert calls == [True]
    assert database.read_bytes() == before
    assert stat.S_IMODE(exact_cache.cache_root(database).stat().st_mode) == 0o700
    assert stat.S_IMODE(exact_cache.store_path(database).stat().st_mode) == 0o600


@pytest.mark.parametrize("mutation", [
    "UPDATE observations SET value=6.5 WHERE id=1",
    "UPDATE observations SET value='7.5' WHERE id=1",
    "DELETE FROM duplicate_rows WHERE rowid=1",
    "CREATE INDEX observations_value ON observations(value)",
    "CREATE TABLE newly_supported_source(value)",
])
def test_logical_identity_covers_values_types_duplicates_and_schema(database, context, mutation):
    before = _identity(database, context)
    previous_stat = database.stat()
    with sqlite3.connect(database) as connection:
        connection.execute(mutation)
    # Metadata shortcuts cannot excuse serving an obsolete calculation.
    os.utime(database, ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns))
    assert _identity(database, context) != before


@pytest.mark.parametrize("columns,hidden", [
    ("value TEXT", "rowid"),
    ('"_ROWID_" TEXT, value TEXT', "rowid"),
    ('"_ROWID_" TEXT, "RoWiD" TEXT, value TEXT', "oid"),
])
def test_changed_implicit_row_identity_invalidates_even_when_all_columns_match(
    database, context, columns, hidden,
):
    with sqlite3.connect(database) as writer:
        writer.execute("CREATE TABLE implicit_evidence(" + columns + ")")
        writer.execute("INSERT INTO implicit_evidence(value) VALUES('fictional')")
        original_columns = writer.execute("SELECT * FROM implicit_evidence").fetchall()
    before = _identity(database, context)
    with sqlite3.connect(database) as writer:
        writer.execute("UPDATE implicit_evidence SET " + hidden + "=42")
        assert writer.execute("SELECT * FROM implicit_evidence").fetchall() == original_columns
    assert _identity(database, context) != before


@pytest.mark.parametrize("definition", [
    "CREATE TABLE special_rows(key TEXT PRIMARY KEY, value TEXT) WITHOUT ROWID",
    "CREATE TABLE special_rows(rowid TEXT, _rowid_ TEXT, oid TEXT, value TEXT)",
])
def test_without_rowid_and_fully_shadowed_aliases_support_reuse_and_invalidation(
    database, context, definition,
):
    with sqlite3.connect(database) as writer:
        writer.execute(definition)
        if "WITHOUT ROWID" in definition:
            writer.execute("INSERT INTO special_rows(key,value) VALUES('fictional','before')")
        else:
            writer.execute("INSERT INTO special_rows(value) VALUES('before')")
    calls = []

    def calculate():
        calls.append(True)
        with sqlite3.connect(database) as reader:
            return {"value": reader.execute("SELECT value FROM special_rows").fetchone()[0]}

    assert _compute(database, context, calculate) == {"value": "before"}
    assert _compute(database, context, calculate) == {"value": "before"}
    assert calls == [True]
    with sqlite3.connect(database) as writer:
        writer.execute("UPDATE special_rows SET value='after'")
    assert _compute(database, context, calculate) == {"value": "after"}
    assert calls == [True, True]


def test_wal_update_invalidates_result_even_when_main_file_unchanged(database, context):
    writer = sqlite3.connect(database)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        calls = []

        def calculate():
            calls.append(True)
            return {"value": writer.execute("SELECT value FROM observations").fetchone()[0]}

        assert _compute(database, context, calculate) == {"value": 7.5}
        main_before = database.read_bytes()
        writer.execute("UPDATE observations SET value=6.5")
        writer.commit()
        assert database.read_bytes() == main_before
        assert _compute(database, context, calculate) == {"value": 6.5}
        assert len(calls) == 2
    finally:
        writer.close()


def test_identity_and_calculation_share_one_read_snapshot(database, context):
    writer = sqlite3.connect(database)
    writer.execute("PRAGMA journal_mode=WAL")
    reader = runtime.connect_read_only(database)
    try:
        reader.execute("BEGIN")
        before = exact_cache.current_identity(database, connection=reader, context=context)
        writer.execute("UPDATE observations SET value=6.5")
        writer.commit()
        assert exact_cache.current_identity(database, connection=reader, context=context) == before
        result = exact_cache.cached_compute(
            database, {"command": "snapshot-test"},
            lambda: {"value": reader.execute("SELECT value FROM observations").fetchone()[0]},
            connection=reader, context=context,
        )
        assert result == {"value": 7.5}
        assert _identity(database, context) != before
        assert _compute(database, context, lambda: {"value": 6.5},
                        request={"command": "snapshot-test"}) == {"value": 6.5}
    finally:
        reader.close()
        writer.close()


@pytest.mark.parametrize("change", [
    {"today": date(2026, 7, 2)},
    {"timezone": "Europe/Paris"},
    {"constants": {"target": 9}},
])
def test_effective_clock_timezone_and_configuration_are_inputs(database, context, change):
    assert _identity(database, context) != _identity(database, replace(context, **change))


def test_engine_and_effective_catalog_changes_invalidate_identity(
    database, context, tmp_path, monkeypatch,
):
    toolkit = tmp_path / "fictional-toolkit"
    package = toolkit / "hermes_insights"
    package.mkdir(parents=True)
    engine = package / "formula.py"
    engine.write_text("MULTIPLIER = 1\n")
    (toolkit / "SCHEMA.sql").write_text("CREATE TABLE fictional(value);\n")
    native = package / "native/rank_products.c"
    native.parent.mkdir()
    native.write_text("/* original native source */\n")
    monkeypatch.setattr(exact_cache, "__file__", str(package / "exact_cache.py"))
    before = _identity(database, context)
    engine.write_text("MULTIPLIER = 2\n")
    after_engine = _identity(database, context)
    assert after_engine != before
    monkeypatch.setattr(exact_cache.catalogs, "FICTIONAL_CACHE_TARGET", 42, raising=False)
    assert _identity(database, context) != after_engine
    before_native = _identity(database, context)
    native.write_text("/* changed native source */\n")
    assert _identity(database, context) != before_native


def test_lock_contention_refuses_duplicate_heavy_work_and_ignores_unrelated_fd(
    database, context, tmp_path, monkeypatch,
):
    calls = []
    unrelated = tmp_path / "unrelated.lock"
    with unrelated.open("w") as descriptor:
        with exact_cache.computation_lock(database):
            monkeypatch.setenv("OHA_ANALYSIS_LOCK_FD", str(descriptor.fileno()))
            with pytest.raises(exact_cache.AnalysisBusy):
                _compute(database, context, lambda: calls.append(True) or {"value": 7.5})
    assert calls == []


@pytest.mark.parametrize("damage", ["hash_mismatch", "invalid_json", "blob"])
def test_damaged_cached_payload_recomputes(database, context, damage):
    calls = []

    def calculate():
        calls.append(True)
        return {"value": 7.5}

    assert _compute(database, context, calculate) == {"value": 7.5}
    with sqlite3.connect(exact_cache.store_path(database)) as store:
        if damage == "hash_mismatch":
            store.execute("UPDATE exact_results SET result_json='{}'")
        else:
            raw = b"invalid-json" if damage == "invalid_json" else b'{"value":0}'
            stored = raw.decode() if damage == "invalid_json" else sqlite3.Binary(raw)
            store.execute(
                "UPDATE exact_results SET result_json=?, result_sha256=?, result_bytes=?",
                (stored, hashlib.sha256(raw).hexdigest(), len(raw)),
            )
    assert _compute(database, context, calculate) == {"value": 7.5}
    assert len(calls) == 2


def test_unreadable_store_and_unrepresentable_context_do_not_break_analysis(
    database, context,
):
    calls = []

    def calculate():
        calls.append(True)
        return {"value": 7.5}

    _compute(database, context, calculate)
    exact_cache.store_path(database).write_bytes(b"not a sqlite database")
    assert _compute(database, context, calculate) == {"value": 7.5}
    assert _compute(database, replace(context, constants={"unsupported": object()}), calculate) == {
        "value": 7.5,
    }
    assert len(calls) == 3


@pytest.mark.parametrize("unavailable", ["damaged_store", "unsupported_context"])
def test_optional_cache_fallback_still_respects_shared_execution_slot(
    database, context, unavailable,
):
    _compute(database, context, lambda: {"value": 7.5})
    if unavailable == "damaged_store":
        exact_cache.store_path(database).write_bytes(b"not a sqlite database")
    else:
        context = replace(context, constants={"unsupported": object()})
    calls = []
    with exact_cache.computation_lock(database):
        with pytest.raises(exact_cache.AnalysisBusy):
            _compute(database, context, lambda: calls.append(True) or {"value": 7.5})
    assert calls == []


def test_eviction_is_bounded_and_keeps_recently_used_result(database, context, monkeypatch):
    monkeypatch.setattr(exact_cache, "MAX_CACHE_ROWS", 2)
    calls = []

    def query(number):
        return _compute(database, context, lambda: calls.append(number) or {"value": number},
                        request={"number": number})

    query(1)
    query(2)
    query(1)
    query(3)
    query(1)
    assert calls == [1, 2, 3]
    with sqlite3.connect(exact_cache.store_path(database)) as store:
        assert store.execute("SELECT count(*) FROM exact_results").fetchone()[0] == 2
    query(2)
    assert calls == [1, 2, 3, 2]


def test_byte_limit_and_oversized_results_do_not_accumulate(database, context, monkeypatch):
    monkeypatch.setattr(exact_cache, "MAX_CACHE_BYTES", 40)
    monkeypatch.setattr(exact_cache, "MAX_RESULT_BYTES", 30)
    for number in range(5):
        result = {"text": str(number) * 12}
        assert _compute(database, context, lambda: result,
                        request={"number": number}) == result
    large = {"text": "x" * 50}
    assert _compute(database, context, lambda: large, request={"number": 100}) == large
    with sqlite3.connect(exact_cache.store_path(database)) as store:
        count, total = store.execute(
            "SELECT count(*),coalesce(sum(result_bytes),0) FROM exact_results"
        ).fetchone()
    assert count == 1
    assert total <= 40


def test_inherited_lock_can_be_reused_without_releasing_parent_ownership(database, monkeypatch):
    monkeypatch.delenv("OHA_ANALYSIS_LOCK_FD", raising=False)
    toolkit = str(Path(__file__).resolve().parents[1])
    script = """
import sys
from hermes_insights.exact_cache import computation_lock
with computation_lock(sys.argv[1]):
    print('inherited')
"""
    with exact_cache.computation_lock(database) as descriptor:
        environ = dict(os.environ, PYTHONPATH=toolkit, OHA_ANALYSIS_LOCK_FD=str(descriptor))
        child = subprocess.run(
            [sys.executable, "-c", script, str(database)], env=environ,
            pass_fds=(descriptor,), capture_output=True, text=True, timeout=10,
        )
        assert child.returncode == 0, child.stderr
        assert child.stdout.strip() == "inherited"
        # Child exit must not unlock the shared open-file description.
        with pytest.raises(exact_cache.AnalysisBusy):
            with exact_cache.computation_lock(database):
                pass
    with exact_cache.computation_lock(database):
        pass


@pytest.mark.parametrize("failure", [PermissionError("source unavailable"),
                                     FileNotFoundError("missing input"),
                                     OSError(errno.EROFS, "read-only input")])
def test_uncached_calculation_errors_are_not_retried(database, context, monkeypatch, failure):
    monkeypatch.setenv("OPENHEALTHATLAS_DISABLE_ANALYSIS_CACHE", "1")
    calls = []

    def calculate():
        calls.append(True)
        raise failure

    with pytest.raises(type(failure)) as raised:
        _compute(database, context, calculate)
    assert raised.value is failure
    assert calls == [True]


@pytest.mark.parametrize("failure", [PermissionError("cache unavailable"),
                                     OSError(errno.EROFS, "read-only installation")])
def test_unwritable_private_workspace_retains_stateless_reads(database, context, monkeypatch, failure):
    def unavailable(_path):
        raise failure

    monkeypatch.setattr(exact_cache, "_private_directory", unavailable)
    calls = []
    assert _compute(database, context, lambda: calls.append(True) or {"value": 7.5}) == {
        "value": 7.5,
    }
    assert calls == [True]


@pytest.mark.parametrize("filename", ["analysis.sqlite3", "execution.lock"])
def test_private_store_and_lock_reject_hardlinks(database, tmp_path, filename):
    with exact_cache.computation_lock(database):
        pass
    target = exact_cache.cache_root(database) / filename
    outside = tmp_path / "other-file"
    outside.write_bytes(b"fictional outsider")
    outside.chmod(0o600)
    if target.exists():
        target.unlink()
    target.hardlink_to(outside)
    with pytest.raises(PermissionError):
        if filename == "analysis.sqlite3":
            exact_cache.connect_store(database)
        else:
            with exact_cache.computation_lock(database):
                pass
    assert outside.read_bytes() == b"fictional outsider"


_READINESS_NOW = datetime(2026, 7, 23, 12, tzinfo=timezone.utc)


def _readiness_clock():
    # Like the runtime clock, advancing wall time does not change function code.
    return _READINESS_NOW


def test_warm_result_refreshes_same_day_collector_freshness_without_recomputing_statistics(
    tmp_path, monkeypatch,
):
    from hermes_insights.contracts import DateRange
    from hermes_insights.readiness import build_readiness
    from hermes_insights.registry import build_registry

    database = tmp_path / "readiness.db"
    with sqlite3.connect(database) as writer:
        writer.executescript((Path(__file__).resolve().parents[1] / "SCHEMA.sql").read_text())
        writer.execute("INSERT INTO weather(date,location,temp_max_c,source) "
                       "VALUES('2026-07-23','fictional location',24,'fixture')")
        writer.execute("INSERT INTO source_sync_runs(source,started_at,completed_at,status,coverage_from,coverage_to) "
                       "VALUES('weather','2026-07-21T11:00:00+00:00','2026-07-21T12:00:00+00:00',"
                       "'success','2026-07-21','2026-07-23')")
    monkeypatch.setattr(sys.modules[__name__], "_READINESS_NOW",
                        datetime(2026, 7, 23, 12, tzinfo=timezone.utc))
    context = runtime.adapter_context(clock=_readiness_clock)
    reader = runtime.connect_read_only(database)
    try:
        reader.execute("BEGIN")
        definitions = tuple(item for item in build_registry(reader, context)
                            if (item.key.startswith("weather.") and item.key.endswith(".temp_max_c"))
                            or item.key == "subjective.day_rating")
        calls = []

        def calculate():
            calls.append(True)
            return {
                "meta": {"requested_range": {"kind": "bounded", "from": "2026-07-23", "to": "2026-07-23"},
                         "outcome": "subjective.day_rating"},
                "findings": [{"fictional_statistic": 7.5}],
                "readiness": build_readiness(
                    reader, definitions, DateRange(context.today, context.today), context,
                    outcome="subjective.day_rating",
                ),
            }

        initial = exact_cache.cached_compute(database, {"command": "readiness-fixture"}, calculate,
                                             connection=reader, context=context, definitions=definitions)
        weather = lambda value: next(row for row in value["readiness"]["features"]
                                     if row["feature_key"].startswith("weather."))
        assert weather(initial)["factors"]["collector_freshness"]["status"] == "late"
        monkeypatch.setattr(sys.modules[__name__], "_READINESS_NOW",
                            datetime(2026, 7, 23, 13, tzinfo=timezone.utc))
        warm = exact_cache.cached_compute(database, {"command": "readiness-fixture"}, calculate,
                                          connection=reader, context=context, definitions=definitions)
        fresh = exact_cache.refresh_readiness(database, warm, connection=reader,
                                              context=context, definitions=definitions)
        assert calls == [True]
        assert fresh["findings"] == initial["findings"]
        assert weather(fresh)["factors"]["collector_freshness"]["status"] == "stale"
        assert weather(fresh)["state"] == "stale"
        assert weather(initial)["factors"]["collector_freshness"]["status"] == "late"
    finally:
        reader.close()


def test_cli_outcome_with_real_default_context_reuses_exact_calculation(
    tmp_path, monkeypatch, capsys,
):
    import health
    from hermes_insights import associations, migrations

    database = tmp_path / "small-cli.db"
    with sqlite3.connect(database) as writer:
        writer.executescript((Path(__file__).resolve().parents[1] / "SCHEMA.sql").read_text())
    migrations.migrate(str(database), 3, 0, code_version="a" * 40)
    before = database.read_bytes()
    monkeypatch.setattr(health, "DB", str(database))
    original = associations.analyze_outcome
    calls = []

    def calculate(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(associations, "analyze_outcome", calculate)
    options = SimpleNamespace(outcome="subjective.energy", mode="ordinal", min_n=30,
                              interactions="none", top=1, from_date="2026-06-24",
                              to_date="2026-06-30", days=None, all_dates=False)
    health.outcome_associations_cmd(options)
    first = json.loads(capsys.readouterr().out)
    health.outcome_associations_cmd(options)
    second = json.loads(capsys.readouterr().out)
    assert calls == [True]
    assert second == first
    assert database.read_bytes() == before
    with sqlite3.connect(exact_cache.store_path(database)) as store:
        assert store.execute("SELECT count(*) FROM exact_results").fetchone()[0] == 1


def test_cli_process_restart_reuses_persisted_result(tmp_path):
    from hermes_insights import migrations

    toolkit = Path(__file__).resolve().parents[1]
    database = tmp_path / "restarted-cli.db"
    with sqlite3.connect(database) as writer:
        writer.executescript((toolkit / "SCHEMA.sql").read_text())
    migrations.migrate(str(database), 3, 0, code_version="a" * 40)
    command = [sys.executable, str(toolkit / "health.py"), "outcome-associations",
               "--outcome", "subjective.energy", "--mode", "ordinal", "--min-n", "30",
               "--interactions", "none", "--top", "1", "--from", "2026-06-24", "--to", "2026-06-30"]
    environment = dict(os.environ, HEALTH_DB=str(database), HERMES_DATA_DIR=str(tmp_path))
    environment.pop("OHA_ANALYSIS_LOCK_FD", None)
    before = database.read_bytes()
    outputs, persisted = [], []
    for _ in range(2):
        completed = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
        assert completed.returncode == 0, completed.stderr
        outputs.append(json.loads(completed.stdout))
        with sqlite3.connect(exact_cache.store_path(database)) as store:
            rows = store.execute("SELECT cache_key,created_at,accessed_at FROM exact_results").fetchall()
        assert len(rows) == 1
        persisted.append(rows[0])
    assert outputs[0] == outputs[1]
    assert persisted[0][:2] == persisted[1][:2]
    assert persisted[1][2] > persisted[0][2]
    assert database.read_bytes() == before
