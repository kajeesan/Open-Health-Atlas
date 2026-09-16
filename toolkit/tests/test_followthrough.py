"""Follow-through layer: commitments, the nightly kept/partly/broke word log,
timed check-ins, and the deterministic adherence stats."""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()
TEST_TZ = ZoneInfo("Europe/Paris")


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
                       capture_output=True, text=True, timeout=60)
    if expect_ok:
        assert r.returncode == 0, f"{args}: {r.stderr or r.stdout}"
    return r


def jout(r):
    return json.loads(r.stdout)


def days_back(n):
    t = datetime.now(TEST_TZ).date()
    return [(t - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def test_commitment_set_upsert_and_list(db):
    r = jout(run(db, "commitment-set", "protein first",
                 "--identity", "I fuel my training",
                 "--trigger", "when I plate dinner, protein goes on first",
                 "--floor", "one protein shake", "--reward", "tick the app"))
    assert r["was_new"] is True and r["commitment"]["active"] == 1
    r2 = jout(run(db, "commitment-set", "protein first", "--active", "0"))
    assert r2["was_new"] is False
    assert r2["commitment"]["identity"] == "I fuel my training"  # kept on partial edit
    assert r2["commitment"]["active"] == 0
    assert jout(run(db, "commitment-list"))["commitments"] == []
    assert len(jout(run(db, "commitment-list", "--all"))["commitments"]) == 1


def test_log_commitment_whole_day_upserts(db):
    run(db, "log-commitment", "broke", "--why", "doomscrolled past bedtime")
    r = jout(run(db, "log-commitment", "partly"))          # re-log same day: update
    assert r["scope"] == "whole-day" and r["status"] == "partly"
    con = sqlite3.connect(db)
    rows = con.execute("SELECT status, why FROM commitments_log").fetchall()
    con.close()
    # status changed without a new why -> the old why is dropped, not carried
    # over to a day it no longer describes (review finding)
    assert rows == [("partly", None)]


def test_log_commitment_by_id_requires_existing(db):
    r = run(db, "log-commitment", "kept", "--id", "99", expect_ok=False)
    assert r.returncode != 0
    cid = jout(run(db, "commitment-set", "gym"))["commitment"]["id"]
    r2 = jout(run(db, "log-commitment", "kept", "--id", str(cid)))
    assert r2["scope"] == "gym"


def test_log_commitment_rejects_bad_status(db):
    r = run(db, "log-commitment", "meh", expect_ok=False)
    assert r.returncode != 0


def test_checkin_validates_and_defaults_time(db):
    r = jout(run(db, "checkin", "energy", "4"))
    assert r["kind"] == "energy" and r["value"] == 4 and ":" in r["time"]
    assert run(db, "checkin", "energy", "9", expect_ok=False).returncode != 0
    assert run(db, "checkin", "vibes", "3", expect_ok=False).returncode != 0
    assert run(db, "checkin", "focus", "3", "--time", "25:99",
               expect_ok=False).returncode != 0


def test_capture_source_is_optional_validated_and_persisted(db):
    legacy = jout(run(db, "day-rating", "green", "--date", "2026-07-01"))
    assert "source" not in legacy
    tagged = jout(run(db, "day-rating", "yellow", "--date", "2026-07-01",
                      "--source", "panel-ui"))
    word = jout(run(db, "log-commitment", "kept", "--date", "2026-07-01",
                    "--source", "chat-panel"))
    mood = jout(run(db, "checkin", "mood", "4", "--date", "2026-07-01",
                    "--time", "20:10", "--source", "chat-telegram"))
    assert (tagged["source"], word["source"], mood["source"]) == (
        "panel-ui", "chat-panel", "chat-telegram")
    con = sqlite3.connect(db)
    assert con.execute("SELECT source FROM subjective_daily WHERE date='2026-07-01'").fetchone()[0] == "panel-ui"
    assert con.execute("SELECT source FROM commitments_log WHERE date='2026-07-01'").fetchone()[0] == "chat-panel"
    assert con.execute("SELECT source FROM checkins WHERE date='2026-07-01'").fetchone()[0] == "chat-telegram"
    con.close()
    for cmd in (("day-rating", "red"), ("log-commitment", "broke"),
                ("checkin", "mood", "3")):
        assert run(db, *cmd, "--source", "scheduler", expect_ok=False).returncode != 0


def test_legacy_capture_defaults_and_payloads_remain_unchanged(db):
    day = jout(run(db, "day-rating", "green", "--date", "2026-07-02"))
    word = jout(run(db, "log-commitment", "kept", "--date", "2026-07-02"))
    mood = jout(run(db, "checkin", "mood", "3", "--date", "2026-07-02", "--time", "19:00"))
    assert "source" not in day and "source" not in word and "source" not in mood
    con = sqlite3.connect(db)
    assert con.execute("SELECT source FROM subjective_daily WHERE date='2026-07-02'").fetchone()[0] == "manual"
    assert con.execute("SELECT source FROM commitments_log WHERE date='2026-07-02'").fetchone()[0] == "ui"
    assert con.execute("SELECT source FROM checkins WHERE date='2026-07-02'").fetchone()[0] == "ui"
    con.close()


def test_feedback_status_missing_only_and_read_only(db):
    d = "2026-07-20"
    con = sqlite3.connect(db)
    before = con.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    con.close()
    result = jout(run(db, "feedback-status", "--date", d))
    assert result["prompt_fields"] == ["day_rating", "whole_day_word", "mood"]
    assert result["complete_for_prompt"] is False
    assert result["checkins"]["energy"]["missing_today"] is True
    assert result["checkins"]["focus"]["missing_today"] is True
    con = sqlite3.connect(db)
    after = con.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    con.close()
    assert after == before  # read path must not run lazy CREATE TABLE statements


def test_feedback_status_date_validation_is_structured(db):
    result = run(db, "feedback-status", "--date", "tomorrowish", expect_ok=False)
    assert result.returncode == 2
    payload = jout(result)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "validation_error"


def test_feedback_status_mood_and_pain_completeness_semantics(db):
    d = "2026-07-20"
    con = sqlite3.connect(db)
    for values in (("2026-07-19", "knee", "left", 4),
                   ("2026-07-18", "ankle", "right", 7),
                   ("2026-07-17", "hip", "right", 7),
                   ("2026-07-01", "back", "central", 9)):
        con.execute("INSERT INTO pain_log(date,region,side,intensity) VALUES(?,?,?,?)", values)
    con.commit(); con.close()
    run(db, "day-rating", "green", "--date", d)
    run(db, "log-commitment", "kept", "--date", d)
    run(db, "checkin", "energy", "2", "--date", d, "--time", "08:00")
    run(db, "checkin", "mood", "4", "--date", d, "--time", "20:00")
    status = jout(run(db, "feedback-status", "--date", d))
    assert status["prompt_fields"] == ["pain_change"]
    assert [(p["region"], p["side"]) for p in status["pain_followup_due"]] == [
        ("ankle", "right"), ("hip", "right")]
    assert status["checkins"]["energy"] == {
        "count": 1, "latest_time": "08:00", "latest_value": 2, "missing_today": False}
    assert status["checkins"]["focus"]["missing_today"] is True
    con = sqlite3.connect(db)
    con.execute("INSERT INTO pain_log(date,region,side,intensity) VALUES(?,?,?,?)",
                (d, "ankle", "right", 6))
    con.execute("INSERT INTO pain_log(date,region,side,intensity) VALUES(?,?,?,?)",
                (d, "hip", "right", 0))
    con.execute("INSERT INTO pain_log(date,region,side,intensity) VALUES(?,?,?,?)",
                (d, "knee", "left", 4))
    con.commit(); con.close()
    complete = jout(run(db, "feedback-status", "--date", d))
    assert complete["pain_followup_due"] == []
    assert complete["prompt_fields"] == [] and complete["complete_for_prompt"] is True


def test_feedback_status_lists_active_commitments_without_reasking_energy_focus(db):
    d = "2026-07-20"
    gym = jout(run(db, "commitment-set", "gym"))["commitment"]["id"]
    run(db, "commitment-set", "protein")
    run(db, "log-commitment", "kept", "--id", str(gym), "--date", d)
    status = jout(run(db, "feedback-status", "--date", d))
    assert status["active_commitments_missing_log"] == [
        {"id": gym + 1, "name": "protein"}]
    assert status["prompt_fields"] == ["day_rating", "whole_day_word", "mood"]


def test_checkins_bucket_into_frame(db):
    d = days_back(1)[0]
    run(db, "checkin", "energy", "2", "--time", "09:30", "--date", d)
    run(db, "checkin", "energy", "4", "--time", "14:00", "--date", d)
    run(db, "checkin", "energy", "4", "--time", "15:00", "--date", d)
    frame = jout(run(db, "build-daily-frame", "--days", "7"))
    row = [r for r in frame["days"] if r["date"] == d][0]
    assert row["chk_energy_am"] == 2 and row["chk_energy_pm"] == 4


def test_adherence_rate_streak_and_conditions(db):
    dates = days_back(12)
    con = sqlite3.connect(db)
    for i, d in enumerate(dates):
        kept = i % 2 == 1                                  # odd days kept -> today kept
        con.execute("INSERT INTO daily_metrics(date, source, sleep_hours) VALUES(?,?,?)",
                    (d, "apple", 8.0 if kept else 5.0))    # kept days follow good sleep
    con.commit(); con.close()
    for i, d in enumerate(dates):
        run(db, "log-commitment", "kept" if i % 2 == 1 else "broke", "--date", d)
    res = jout(run(db, "adherence", "--days", "30"))
    assert res["days_logged"] == 12
    assert res["rate"] == 0.5
    assert res["counts"] == {"kept": 6, "partly": 0, "broke": 6}
    assert res["streak_non_broke"] == 1                    # today kept, yesterday broke
    sleep_cond = [c for c in res["conditions"] if c["field"] == "sleep_hours"][0]
    assert sleep_cond["kept"]["mean"] == 8.0 and sleep_cond["broke"]["mean"] == 5.0


def test_adherence_honest_when_no_data(db):
    res = jout(run(db, "adherence"))
    assert res["insufficient_data"] is True


def test_adherence_partly_counts_half_and_is_excluded_from_contrast(db):
    dates = days_back(4)
    for d, s in zip(dates, ("kept", "partly", "partly", "kept")):
        run(db, "log-commitment", s, "--date", d)
    res = jout(run(db, "adherence", "--days", "30"))
    assert res["rate"] == 0.75
    assert isinstance(res["conditions"], str)              # <3 broke days -> honest refusal


def test_word_kept_feeds_correlate_pillar(db):
    """The integrity pillar joins the engine: word_kept must be correlatable."""
    dates = days_back(20)
    con = sqlite3.connect(db)
    for i, d in enumerate(dates):
        con.execute("INSERT INTO daily_metrics(date, source, sleep_hours) VALUES(?,?,?)",
                    (d, "apple", 8.0 if i % 2 == 0 else 5.0))
    con.commit(); con.close()
    for i, d in enumerate(dates):
        run(db, "log-commitment", "kept" if i % 2 == 0 else "broke", "--date", d)
    res = jout(run(db, "correlate", "--days", "30", "--min-n", "10", "--top", "500"))
    hits = [p for p in res["pairs"] if {p["a"], p["b"]} == {"sleep_hours", "word_kept"}]
    assert hits and hits[0]["spearman"] > 0.9
    assert set(hits[0]["pillars"]) == {"sleep", "integrity"}


# ------------------------------------------------- review-finding regressions

def test_relog_status_change_drops_stale_why(db):
    """MAJOR: a 'broke' excuse must never annotate a later 'kept' day."""
    run(db, "log-commitment", "broke", "--why", "was exhausted, skipped gym")
    run(db, "log-commitment", "kept")                      # status change, no why
    con = sqlite3.connect(db)
    row = con.execute("SELECT status, why FROM commitments_log").fetchone()
    con.close()
    assert row == ("kept", None)


def test_relog_same_status_keeps_why(db):
    run(db, "log-commitment", "kept", "--why", "gym before work")
    run(db, "log-commitment", "kept")                      # same status: why survives
    con = sqlite3.connect(db)
    assert con.execute("SELECT why FROM commitments_log").fetchone()[0] == "gym before work"
    con.close()


def test_adherence_days_all_sees_word_logs(db):
    """MAJOR: --days 0 (all history) must find commitments_log even when no
    other probed table has rows."""
    for d, s in zip(days_back(5), ("kept", "kept", "broke", "kept", "kept")):
        run(db, "log-commitment", s, "--date", d)
    res = jout(run(db, "adherence", "--days", "0"))
    assert res["days_logged"] == 5


def test_streak_breaks_on_calendar_gap(db):
    dates = days_back(10)
    for d in (dates[0], dates[8], dates[9]):               # gap between 0 and 8
        run(db, "log-commitment", "kept", "--date", d)
    res = jout(run(db, "adherence", "--days", "30"))
    assert res["streak_non_broke"] == 2                    # the two consecutive days only


def test_write_commands_reject_non_iso_dates(db):
    for args in (("log-commitment", "kept", "--date", "07/07/2026"),
                 ("checkin", "energy", "3", "--date", "tomorrowish"),
                 ("day-rating", "green", "--date", "2026-13-40"),
                 ("log-set", "Squat", "--reps", "5", "--date", "next tuesday")):
        r = run(db, *args, expect_ok=False)
        assert r.returncode != 0, args
