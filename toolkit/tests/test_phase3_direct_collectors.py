"""Environmental normalization and collector provenance without live transport."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from hermes_insights import goals, migrations
from hermes_insights.command_context import CommandContext
from hermes_insights.commands import collectors
from hermes_insights.importers import open_meteo


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def context(tmp_path):
    path = tmp_path / "provider.db"
    with sqlite3.connect(path) as connection:
        connection.executescript((ROOT / "SCHEMA.sql").read_text())
    return CommandContext(
        database=str(path), clock=lambda: datetime(2026, 7, 22, 4, 5, tzinfo=timezone.utc),
        timezone="UTC", vault=str(tmp_path / "vault"), cli_path=str(ROOT / "health.py"),
    )


@pytest.fixture
def migrated_context(context):
    migrations.migrate(context.database, 3, 0,
                       code_version="6bcb72109ed81aabc8d301994522f2b26fb307e8")
    return context


def _args():
    return SimpleNamespace(
        date="2026-07-22", lat="55.6761", lon="12.5683", location="Fictional",
    )


def _transport(fixture_name):
    return lambda url, timeout: (ROOT / "tests" / "fixtures" / fixture_name).open(encoding="utf-8")


def _recorded(context):
    with sqlite3.connect(context.database) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM source_sync_runs")]


@pytest.mark.parametrize("command,payload,source,table", [
    (collectors.fetch_weather, 'open_meteo_weather_complete.synthetic.json', "weather", "weather"),
    (collectors.fetch_air, 'open_meteo_air_complete.synthetic.json', "air", "air_quality"),
])
def test_direct_collector_success_is_recorded_after_primary_output(
    migrated_context, command, payload, source, table,
):
    outputs = []
    provenance_visible_at_output = []
    primary_rows_visible_at_output = []

    def output(value):
        outputs.append(value)
        provenance_visible_at_output.extend(_recorded(migrated_context))
        with sqlite3.connect(migrated_context.database) as connection:
            primary_rows_visible_at_output.append(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    result = command(migrated_context, _args(), open_url=_transport(payload), output=output)

    record, = _recorded(migrated_context)
    assert outputs == [result]
    assert provenance_visible_at_output == []
    assert primary_rows_visible_at_output == [1]
    assert record["source"] == source
    assert record["status"] == "success"
    assert record["coverage_from"] == record["coverage_to"] == "2026-07-22"
    assert record["started_at"] == record["completed_at"] == "2026-07-22T04:05:00+00:00"
    assert record["rows_seen"] == record["rows_written"] == 1
    assert record["error_code"] is None
    assert json.loads(record["details_json"]) == {"warning_codes": []}


@pytest.mark.parametrize("command,payload,source", [
    (collectors.fetch_weather, 'open_meteo_weather_partial.synthetic.json', "weather"),
    (collectors.fetch_air, 'open_meteo_air_partial.synthetic.json', "air"),
])
def test_direct_collector_missing_provider_fields_are_partial(
    migrated_context, command, payload, source,
):
    outputs = []

    result = command(migrated_context, _args(), open_url=_transport(payload), output=outputs.append)

    record, = _recorded(migrated_context)
    assert outputs == [result]
    assert record["source"] == source
    assert record["status"] == "partial"
    assert record["error_code"] is None
    assert json.loads(record["details_json"]) == {"warning_codes": ["missing_provider_fields"]}


@pytest.mark.parametrize("command,source", [
    (collectors.fetch_weather, "weather"), (collectors.fetch_air, "air"),
])
def test_direct_collector_failure_persists_only_a_bounded_code(migrated_context, command, source):
    secret_detail = "https://provider.invalid/?token=TOPSECRET"
    original_error = RuntimeError(secret_detail)
    outputs = []

    def fail(url, timeout):
        raise original_error

    with pytest.raises(RuntimeError, match="TOPSECRET") as error:
        command(migrated_context, _args(), open_url=fail, output=outputs.append)

    record, = _recorded(migrated_context)
    assert error.value is original_error
    assert outputs == []
    assert record["source"] == source
    assert record["status"] == "failed"
    assert record["error_code"] == "collection_failed"
    assert record["rows_seen"] == record["rows_written"] == 0
    assert "TOPSECRET" not in json.dumps(record)
    assert "provider.invalid" not in json.dumps(record)


def test_missing_provider_fields_checks_expected_values_only():
    assert open_meteo.missing_provider_fields(
        {"present": 0, "missing": None, "unrelated": None}, {"present", "missing"},
    ) == ["missing"]


def test_weather_normalizes_metric_values_and_replaces_existing_row(context):
    with sqlite3.connect(context.database) as connection:
        connection.execute("INSERT INTO weather(date,location,condition,precipitation_mm) VALUES(?,?,?,?)",
                           ("2026-07-22", "old", "rain", 7))
    outputs = []

    result = collectors.fetch_weather(
        context, _args(), open_url=_transport('open_meteo_weather_partial.synthetic.json'), output=outputs.append)

    with sqlite3.connect(context.database) as connection:
        rows = connection.execute("SELECT date,location,condition,temp_min_c,daylight_hours,sunshine_hours,humidity_mean_pct,pressure_mean_hpa,cloud_cover_mean_pct,precipitation_mm,rain_mm,source FROM weather").fetchall()
    assert rows == [("2026-07-22", "Fictional", "clear", 0, 12.5, 2.5, 50, 1000.5, 5, 0, None, "open-meteo")]
    assert result["collected_fields"] == 27
    assert outputs == [result]


def test_air_normalizes_hourly_values_and_replaces_existing_row(context):
    with sqlite3.connect(context.database) as connection:
        connection.execute("INSERT INTO air_quality(date,location,pm2_5_ugm3) VALUES(?,?,?)",
                           ("2026-07-22", "old", 100))
    outputs = []

    result = collectors.fetch_air(context, _args(), open_url=_transport('open_meteo_air_partial.synthetic.json'), output=outputs.append)

    with sqlite3.connect(context.database) as connection:
        rows = connection.execute("SELECT date,location,european_aqi_mean,european_aqi_max,pm2_5_ugm3,pm10_ugm3,grass_pollen,birch_pollen,source FROM air_quality").fetchall()
    assert rows == [("2026-07-22", "Fictional", 15, 20, 1.5, 2.5, 3, None, "open-meteo-aqi")]
    assert result["collected_fields"] == 17
    assert outputs == [result]


def test_canonical_weather_commit_survives_provenance_failure(migrated_context, monkeypatch):
    outputs = []

    def fail_record(connection, payload):
        connection.execute("INSERT INTO intake(date,water_ml) VALUES('2026-07-22',123)")
        raise RuntimeError("synthetic provenance failure")

    monkeypatch.setattr(goals, "record_collector_run", fail_record)

    result = collectors.fetch_weather(
        migrated_context, _args(), open_url=_transport('open_meteo_weather_partial.synthetic.json'), output=outputs.append)

    with sqlite3.connect(migrated_context.database) as connection:
        assert connection.execute("SELECT condition FROM weather").fetchall() == [("clear",)]
        assert connection.execute("SELECT water_ml FROM intake").fetchall() == []
        assert connection.execute("SELECT id FROM source_sync_runs").fetchall() == []
    assert outputs == [result]


@pytest.mark.parametrize("command,payload,table", [
    (collectors.fetch_weather, 'open_meteo_weather_partial.synthetic.json', "weather"),
    (collectors.fetch_air, 'open_meteo_air_partial.synthetic.json', "air_quality"),
])
def test_rejected_primary_write_keeps_database_unchanged(migrated_context, command, payload, table):
    with sqlite3.connect(migrated_context.database) as connection:
        connection.execute(f"CREATE TRIGGER reject_primary BEFORE INSERT ON {table} "
                           "BEGIN SELECT RAISE(ABORT,'primary blocked'); END")
        before = list(connection.iterdump())
    outputs = []

    with pytest.raises(sqlite3.IntegrityError, match="primary blocked"):
        command(migrated_context, _args(), open_url=_transport(payload), output=outputs.append)

    with sqlite3.connect(migrated_context.database) as connection:
        assert list(connection.iterdump()) == before
    assert outputs == []
