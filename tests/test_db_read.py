"""Phase 3: the read-only database boundary."""
import pathlib
import sqlite3

import pytest

from app import create_app, db_read

SCHEMA = (pathlib.Path(__file__).resolve().parent.parent / "toolkit" / "SCHEMA.sql").read_text()


@pytest.fixture()
def health_app(tmp_path):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO daily_metrics(date, source, resting_hr, steps)"
                " VALUES(date('now'), 'apple', 61.5, 9000)")
    con.execute("INSERT INTO daily_metrics(date, source, resting_hr, steps)"
                " VALUES(date('now'), 'fitbit', 59.0, 9100)")
    con.execute("INSERT INTO subjective_daily(date, focus, day_rating) VALUES(date('now'), 4, 3)")
    con.execute("INSERT INTO vitals(date, time, systolic, diastolic) VALUES(date('now'), '08:00', 124, 80)")
    con.execute("INSERT INTO meds_log(date, drug, dose_mg) VALUES(date('now'), 'medication', 5)")
    con.execute("INSERT INTO meds_log(date, drug, dose_mg) VALUES(date('now'), 'medication', 5)")
    con.execute("INSERT INTO weather(date, location, condition, temp_max_c) VALUES(date('now'), 'X', 'clear', 20)")
    con.commit()
    con.close()
    return create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"),
        "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(hdb),
    })


def test_selects_and_views_work(health_app):
    with health_app.app_context():
        rows = db_read.query("SELECT resting_hr FROM daily_metrics WHERE source='apple'")
        assert rows == [{"resting_hr": 61.5}]
        assert db_read.query("SELECT condition FROM weather_brief")[0]["condition"] == "clear"
        # CTEs must pass the authorizer too
        assert db_read.query("WITH t AS (SELECT 1 AS one) SELECT one FROM t") == [{"one": 1}]


@pytest.mark.parametrize("sql", [
    "INSERT INTO daily_metrics(date, source) VALUES('2020-01-01', 'x')",
    "UPDATE daily_metrics SET resting_hr=1",
    "DELETE FROM daily_metrics",
    "DROP TABLE daily_metrics",
    "CREATE TABLE evil(x)",
    "PRAGMA journal_mode=wal",
    "ATTACH DATABASE ':memory:' AS evil",
])
def test_writes_and_escapes_denied(health_app, sql):
    with health_app.app_context():
        with pytest.raises(sqlite3.DatabaseError):
            db_read.query(sql)
