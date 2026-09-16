"""Phase 3: dashboard endpoints — auth-gated, validated, provenance-aware."""
import pathlib
import sqlite3

import pytest

from datetime import datetime
from zoneinfo import ZoneInfo

# Seed on the CANONICAL canonical timezone day: SQLite date-now is UTC and points
# at yesterday for ~2h after local midnight, while the routes query TEST_TZ-today.
LOCAL_TODAY = datetime.now(ZoneInfo("Europe/Paris")).date().isoformat()

from app import auth as auth_mod
from app import create_app

SCHEMA = (pathlib.Path(__file__).resolve().parent.parent / "toolkit" / "SCHEMA.sql").read_text()


@pytest.fixture()
def dash_client(tmp_path):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO daily_metrics(date, source, resting_hr, hrv_ms, sleep_hours, steps)"
                " VALUES('" + LOCAL_TODAY + "', 'apple', 60.0, 45.5, 6.8, 8000)")
    con.execute("INSERT INTO daily_metrics(date, source, resting_hr, hrv_ms, sleep_hours, steps)"
                " VALUES('" + LOCAL_TODAY + "', 'fitbit', 58.0, 51.0, 7.1, 8200)")
    con.execute("INSERT INTO daily_metrics(date, source, resting_hr)"
                " VALUES(date('now', '-100 day'), 'apple', 64.0)")
    con.execute("INSERT INTO subjective_daily(date, focus, energy, mood, day_rating)"
                " VALUES('" + LOCAL_TODAY + "', 4, 3, 4, 3)")
    con.execute("INSERT INTO vitals(date, time, systolic, diastolic, resting_hr)"
                " VALUES('" + LOCAL_TODAY + "', '08:00', 126, 82, 62)")
    con.execute("INSERT INTO meds_log(date, drug, dose_mg) VALUES('" + LOCAL_TODAY + "', 'medication', 5)")
    con.execute("INSERT INTO meds_log(date, drug, dose_mg) VALUES('" + LOCAL_TODAY + "', 'medication-alias', 10)")
    # unrelated medication rows must never inflate the configured series
    con.execute("INSERT INTO meds_log(date, drug, dose_mg) VALUES('" + LOCAL_TODAY + "', 'other-medication', 50)")
    con.execute("INSERT INTO weather(date, location, condition, temp_max_c, uv_index_max)"
                " VALUES('" + LOCAL_TODAY + "', 'X', 'overcast', 19.5, 3.1)")
    con.execute("INSERT INTO air_quality(date, location, european_aqi_mean, grass_pollen)"
                " VALUES('" + LOCAL_TODAY + "', 'X', 22.5, 14.0)")
    con.commit()
    con.close()
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"),
        "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(hdb),
    })
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    return client


def test_endpoints_require_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    r = app.test_client().get("/api/dash/metrics?days=30")
    assert r.status_code == 401


def test_metrics_provenance_and_range(dash_client):
    rows = dash_client.get("/api/dash/metrics?days=30").get_json()["rows"]
    assert {r["source"] for r in rows} == {"apple", "fitbit"}
    apple = next(r for r in rows if r["source"] == "apple")
    assert apple["resting_hr"] == 60.0 and apple["steps"] == 8000
    # the -100 day row is outside a 30d window but inside 365d
    assert len(rows) == 2
    rows365 = dash_client.get("/api/dash/metrics?days=365").get_json()["rows"]
    assert len(rows365) == 3
    rows_all = dash_client.get("/api/dash/metrics?days=all").get_json()["rows"]
    assert len(rows_all) == 3


def test_days_whitelist(dash_client):
    assert dash_client.get("/api/dash/metrics?days=12").status_code == 400
    assert dash_client.get("/api/dash/metrics?days=evil").status_code == 400
    assert dash_client.get("/api/dash/metrics?days=all").status_code == 200
    assert dash_client.get("/api/dash/body?days=all").status_code == 200
    # "1" (single day) added in owner-review r3 for the global "Day" dropdown
    assert dash_client.get("/api/dash/metrics?days=1").status_code == 200
    assert dash_client.get("/api/dash/body?days=1").status_code == 200


def test_subjective_and_safety_shapes(dash_client):
    subj = dash_client.get("/api/dash/subjective").get_json()["rows"]
    assert subj[0]["day_rating"] == 3 and subj[0]["focus"] == 4
    safety = dash_client.get("/api/dash/safety").get_json()
    assert safety["vitals"][0]["systolic"] == 126
    # 5 + 10 configured medication only; the unrelated row is excluded
    assert safety["doses"][0]["dose_total_mg"] == 15.0


def test_subjective_days_window(tmp_path):
    # subjective gained an optional whitelisted `days` (owner-review r3) so the
    # range calendars' "All time" mode is genuinely unbounded, not 365-capped.
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO subjective_daily(date, day_rating) VALUES('" + LOCAL_TODAY + "', 3)")
    con.execute("INSERT INTO subjective_daily(date, day_rating) VALUES(date('now', '-400 day'), 1)")
    con.commit()
    con.close()
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(hdb),
    })
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    # bare call keeps the pre-r3 default 365-day window: the -400 day row is out
    assert len(client.get("/api/dash/subjective").get_json()["rows"]) == 1
    # days=all is unbounded — picks up the 400-day-old row too
    assert len(client.get("/api/dash/subjective?days=all").get_json()["rows"]) == 2
    # same whitelist-and-validate style as /metrics and /sleep
    assert client.get("/api/dash/subjective?days=1").status_code == 200
    assert client.get("/api/dash/subjective?days=evil").status_code == 400


def test_safety_days_window(tmp_path):
    # safety gained an optional whitelisted `days` (owner-review r3) so the Mind
    # page range dd's "All time" mode is genuinely unbounded, not 365-capped —
    # same whitelist-and-validate style as /subjective. A bare call keeps the
    # pre-r3 default 365-day window.
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO vitals(date, time, systolic, diastolic, resting_hr)"
                " VALUES('" + LOCAL_TODAY + "', '08:00', 126, 82, 62)")
    con.execute("INSERT INTO vitals(date, time, systolic, diastolic, resting_hr)"
                " VALUES(date('now', '-400 day'), '08:00', 130, 85, 64)")
    con.execute("INSERT INTO meds_log(date, drug, dose_mg) VALUES('" + LOCAL_TODAY + "', 'medication', 10)")
    con.execute("INSERT INTO meds_log(date, drug, dose_mg) VALUES(date('now', '-400 day'), 'medication', 5)")
    con.commit()
    con.close()
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(hdb),
    })
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    # bare call keeps the pre-r3 default 365-day window: the -400 day rows are out
    bare = client.get("/api/dash/safety").get_json()
    assert len(bare["vitals"]) == 1 and len(bare["doses"]) == 1
    # days=all is unbounded — picks up the 400-day-old vitals + dose rows too
    allw = client.get("/api/dash/safety?days=all").get_json()
    assert len(allw["vitals"]) == 2 and len(allw["doses"]) == 2
    # same whitelist-and-validate style as /metrics and /subjective
    assert client.get("/api/dash/safety?days=1").status_code == 200
    assert client.get("/api/dash/safety?days=evil").status_code == 400


def test_assessments_empty_by_default(dash_client):
    # fixture seeds no assessment rows — the Mind page must be
    # honest-empty, not a 500/404
    r = dash_client.get("/api/dash/assessments")
    assert r.status_code == 200
    assert r.get_json()["rows"] == []


def test_assessments_are_user_defined(tmp_path):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO assessments(date, scale, part, score, max_score)"
                " VALUES('" + LOCAL_TODAY + "', 'Example wellbeing scale', 'A', 3, 10)")
    con.execute("INSERT INTO assessments(date, scale, part, score, max_score)"
                " VALUES('" + LOCAL_TODAY + "', 'Another example scale', 'full', 7, 20)")
    con.commit()
    con.close()
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(hdb),
    })
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    rows = client.get("/api/dash/assessments").get_json()["rows"]
    assert len(rows) == 2
    assert {row["scale"] for row in rows} == {
        "Example wellbeing scale", "Another example scale"
    }


def test_sleep_empty_by_default(dash_client):
    # fixture seeds no sleep_log rows — the Recovery page's quality/stages
    # cards must be honest-empty, not a 500/404 (Fitbit sleep-stage sync awaits)
    r = dash_client.get("/api/dash/sleep")
    assert r.status_code == 200
    assert r.get_json()["rows"] == []


def test_sleep_log_shape(tmp_path):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO sleep_log(date, quality, deep_min, rem_min, light_min, awake_min,"
                " time_asleep_hours)"
                " VALUES('" + LOCAL_TODAY + "', 4, 78, 96, 216, 27, 7.4)")
    con.execute("INSERT INTO sleep_log(date, quality) VALUES(date('now', '-400 day'), 3)")
    con.commit()
    con.close()
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(hdb),
    })
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    rows = client.get("/api/dash/sleep").get_json()["rows"]
    # default window is 365d: the -400 day row falls outside it
    assert len(rows) == 1
    assert rows[0]["quality"] == 4 and rows[0]["deep_min"] == 78
    # task-52 fix wave: time_asleep_hours rides along for the Data page's
    # own line chart (review finding — it was fetched by nothing before)
    assert rows[0]["time_asleep_hours"] == 7.4
    # days=all is genuinely unbounded — "All time" on the Recovery page must
    # not be silently capped at a year (review finding)
    rows_all = client.get("/api/dash/sleep?days=all").get_json()["rows"]
    assert len(rows_all) == 2
    # the -400 day row never set time_asleep_hours — must read back null,
    # not error or a fabricated 0 (honest-empty per row/field)
    assert rows_all[0]["time_asleep_hours"] is None
    # same whitelist as /metrics
    assert client.get("/api/dash/sleep?days=evil").status_code == 400


def test_care_empty_by_default(dash_client):
    # fixture seeds no skincare rows — the External care page must render
    # per-card honest empty states, never a 500/404 (demo DB is unseeded too)
    r = dash_client.get("/api/dash/care")
    assert r.status_code == 200
    d = r.get_json()
    assert d["products"] == []
    assert d["daily"] == []
    assert d["last_used"] == {}
    assert d["today_used"] == {}
    assert d["routine_streak"] == {"days": 0, "current": False}


def _care_client(tmp_path, seed):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    seed(con)
    con.commit()
    con.close()
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(hdb),
    })
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    return client


def _iso_days_ago(n):
    from datetime import timedelta
    d = datetime.now(ZoneInfo("Europe/Paris")).date() - timedelta(days=n)
    return d.isoformat()


def test_care_shaped(tmp_path):
    yday = _iso_days_ago(1)

    def seed(con):
        con.execute("INSERT INTO skincare_products(product_id, slot, brand, product_name)"
                    " VALUES(1, 'morning', 'Example brand', 'Sample care item A')")
        con.execute("INSERT INTO skincare_products(product_id, slot, brand, product_name)"
                    " VALUES(2, 'evening', 'Example brand', 'Sample care product')")
        con.execute("INSERT INTO skincare_products(product_id, slot, brand, product_name)"
                    " VALUES(3, 'weekly', NULL, 'Sample weekly task')")
        # Care activity yesterday + today = current 2-day streak; an isolated
        # day 10 days back is an older island and must NOT extend it
        for d in (LOCAL_TODAY, yday, _iso_days_ago(10)):
            con.execute("INSERT INTO skincare_log(date, slot, product_id, used)"
                        " VALUES(?, 'morning', 1, 1)", (d,))
        # today: evening step skipped, weekly step done -> 3 steps, 2 used
        con.execute("INSERT INTO skincare_log(date, slot, product_id, used)"
                    " VALUES(?, 'evening', 2, 0)", (LOCAL_TODAY,))
        con.execute("INSERT INTO skincare_log(date, slot, product_id, used)"
                    " VALUES(?, 'weekly', 3, 1)", (LOCAL_TODAY,))

    d = _care_client(tmp_path, seed).get("/api/dash/care").get_json()
    assert d["routine_streak"] == {"days": 2, "current": True}
    assert {p["product_name"] for p in d["products"]} == {
        "Sample care item A", "Sample care product", "Sample weekly task"
    }
    assert d["last_used"]["1"] == LOCAL_TODAY
    assert d["today_used"] == {"1": True, "2": False, "3": True}
    today_row = next(r for r in d["daily"] if r["date"] == LOCAL_TODAY)
    assert today_row["total"] == 3 and today_row["used"] == 2
    yday_row = next(r for r in d["daily"] if r["date"] == yday)
    assert yday_row["total"] == 1 and yday_row["used"] == 1


def test_care_daily_counts_active_products_only(tmp_path):
    # T55 flag-1: the page's daily done/expected aggregate mirrors the
    # round-5 engine care score (toolkit/health.py scores(), T34 lesson) —
    # BOTH sides of the fraction consider ACTIVE products only. A retired
    # product's in-window rows must neither inflate (its used=1 rows) nor
    # deflate (its used=0 rows) the page adherence, so ring and page agree
    # on the same fixture.
    def seed(con):
        con.execute("INSERT INTO skincare_products(product_id, slot, product_name, active)"
                    " VALUES(1, 'morning', 'Sample care item A', 1)")
        con.execute("INSERT INTO skincare_products(product_id, slot, product_name, active)"
                    " VALUES(2, 'evening', 'Retired sample product', 0)")
        # active product missed today; retired product logged used=1 —
        # pre-T55 this read 1/2 (inflated by the retired row's used=1)
        con.execute("INSERT INTO skincare_log(date, slot, product_id, used)"
                    " VALUES(?, 'morning', 1, 0)", (LOCAL_TODAY,))
        con.execute("INSERT INTO skincare_log(date, slot, product_id, used)"
                    " VALUES(?, 'evening', 2, 1)", (LOCAL_TODAY,))

    d = _care_client(tmp_path, seed).get("/api/dash/care").get_json()
    today_row = next(r for r in d["daily"] if r["date"] == LOCAL_TODAY)
    assert today_row["total"] == 1 and today_row["used"] == 0


def test_care_lapsed_streak_reports_zero(tmp_path):
    # a run that ended 3 days ago is not a current streak — no-guilt zero,
    # never a stale "5 days" that would fabricate ongoing adherence
    def seed(con):
        con.execute("INSERT INTO skincare_products(product_id, slot, product_name)"
                    " VALUES(1, 'morning', 'Sample care item A')")
        for n in (3, 4, 5):
            con.execute("INSERT INTO skincare_log(date, slot, product_id, used)"
                        " VALUES(?, 'morning', 1, 1)", (_iso_days_ago(n),))

    d = _care_client(tmp_path, seed).get("/api/dash/care").get_json()
    assert d["routine_streak"] == {"days": 0, "current": False}


def test_habits_ledger_empty_by_default(dash_client):
    # fixture seeds no habits_log rows — the Consistency page's ledger card
    # must be honest-empty, not a 500/404
    r = dash_client.get("/api/dash/habits-ledger")
    assert r.status_code == 200
    assert r.get_json()["rows"] == []


def test_habits_ledger_shaped_and_windowed(tmp_path):
    # owner-review r2: the Consistency page's range dropdown must genuinely
    # re-window the ledger's SQL aggregates (best/logged/done/since/last),
    # while current_streak — a live fact, not a trend — stays the newest
    # row's value regardless of the window.
    def seed(con):
        for n, streak in [(0, 3), (1, 2), (2, 1)]:
            con.execute("INSERT INTO habits_log(date, habit, done, streak)"
                        " VALUES(?, 'evening-checkin', 1, ?)", (_iso_days_ago(n), streak))
        # well outside a 7-day window; only visible once the window widens
        con.execute("INSERT INTO habits_log(date, habit, done, streak)"
                    " VALUES(?, 'evening-checkin', 1, 1)", (_iso_days_ago(100),))

    client = _care_client(tmp_path, seed)
    week = client.get("/api/dash/habits-ledger?days=7").get_json()["rows"]
    assert len(week) == 1
    row = week[0]
    assert row["habit"] == "evening-checkin"
    assert row["current_streak"] == 3
    assert row["best_streak"] == 3
    assert row["days_logged"] == 3
    assert row["days_done"] == 3
    assert row["first_date"] == _iso_days_ago(2)
    assert row["last_date"] == _iso_days_ago(0)

    allrows = client.get("/api/dash/habits-ledger?days=all").get_json()["rows"]
    assert allrows[0]["days_logged"] == 4    # widening the window picks up the 100d-back row
    assert allrows[0]["current_streak"] == 3  # unchanged — not windowed

    # a bare call must keep the endpoint's pre-existing behavior: ALL rows
    # (the days param is additive — no silent default-window change)
    bare = client.get("/api/dash/habits-ledger").get_json()["rows"]
    assert bare == allrows


def test_habits_ledger_days_whitelist(dash_client):
    # same whitelist-and-validate style as /metrics and /sleep
    assert dash_client.get("/api/dash/habits-ledger?days=evil").status_code == 400
    assert dash_client.get("/api/dash/habits-ledger?days=12").status_code == 400
    assert dash_client.get("/api/dash/habits-ledger?days=all").status_code == 200
    # "Day" dropdown option (owner-review r3) needs a 1-day window
    assert dash_client.get("/api/dash/habits-ledger?days=1").status_code == 200


def test_habits_ledger_detail_empty(dash_client):
    # dash_client fixture seeds no habits_log rows — honest empty ledger AND
    # an honest "nothing to gate on" gate, not a 500/404 or a fabricated gate.
    d = dash_client.get("/api/dash/habits-ledger-detail").get_json()
    assert d["rows"] == []
    assert d["gate"] == {"ready": False, "advice": None, "weakest": None}


def test_habits_ledger_detail_classification(tmp_path):
    """task-35: exercises all four statuses + the readiness gate against the
    exact rule set (dash.py _classify_habit docstring):
      protein    -> continuous 25-day streak            -> Established
      sample     -> continuous 10-day streak, best < 21 -> Building
      dose       -> old 21-day run (>30d back, broken)
                    + a fresh 11-day run                -> Rebuilding
      brain-dump -> mostly missed in the last 30 days,
                    but a live 2-day streak              -> Fragile (adherence)
      water      -> a broken 5-day run, missed today     -> Fragile (streak==0)
    """
    def seed(con):
        for n in range(24, -1, -1):
            con.execute("INSERT INTO habits_log(date, habit, done, streak)"
                        " VALUES(?, 'protein', 1, ?)", (_iso_days_ago(n), 25 - n))
        for n in range(9, -1, -1):
            con.execute("INSERT INTO habits_log(date, habit, done, streak)"
                        " VALUES(?, 'sample', 1, ?)", (_iso_days_ago(n), 10 - n))
        for n in range(60, 39, -1):    # 40-60 days back: outside the 30d window
            con.execute("INSERT INTO habits_log(date, habit, done, streak)"
                        " VALUES(?, 'dose', 1, ?)", (_iso_days_ago(n), 61 - n))
        for n in range(10, -1, -1):
            con.execute("INSERT INTO habits_log(date, habit, done, streak)"
                        " VALUES(?, 'dose', 1, ?)", (_iso_days_ago(n), 11 - n))
        for n in range(24, 1, -1):
            con.execute("INSERT INTO habits_log(date, habit, done, streak)"
                        " VALUES(?, 'brain-dump', 0, 0)", (_iso_days_ago(n),))
        con.execute("INSERT INTO habits_log(date, habit, done, streak)"
                    " VALUES(?, 'brain-dump', 1, 1)", (_iso_days_ago(1),))
        con.execute("INSERT INTO habits_log(date, habit, done, streak)"
                    " VALUES(?, 'brain-dump', 1, 2)", (_iso_days_ago(0),))
        for n in range(5, 0, -1):
            con.execute("INSERT INTO habits_log(date, habit, done, streak)"
                        " VALUES(?, 'water', 1, ?)", (_iso_days_ago(n), 6 - n))
        con.execute("INSERT INTO habits_log(date, habit, done, streak)"
                    " VALUES(?, 'water', 0, 0)", (_iso_days_ago(0),))

    data = _care_client(tmp_path, seed).get("/api/dash/habits-ledger-detail").get_json()
    rows = {r["habit"]: r for r in data["rows"]}

    assert rows["protein"]["status"] == "Established"
    assert rows["protein"]["current_streak"] == 25

    assert rows["sample"]["status"] == "Building"
    assert rows["sample"]["best_streak"] == 10

    assert rows["dose"]["status"] == "Rebuilding"
    assert rows["dose"]["current_streak"] == 11
    assert rows["dose"]["best_streak"] == 21   # all-time, not windowed to 30d

    assert rows["brain-dump"]["status"] == "Fragile"
    assert rows["brain-dump"]["current_streak"] == 2      # nonzero: adherence is the trigger
    assert rows["brain-dump"]["adherence_30"] < 0.6

    assert rows["water"]["status"] == "Fragile"
    assert rows["water"]["current_streak"] == 0           # zero-streak trigger
    assert rows["water"]["adherence_30"] >= 0.6            # adherence alone wouldn't flag it

    gate = data["gate"]
    assert gate["ready"] is False
    # weakest = lowest current_streak among habits failing the 14d/60% gate;
    # water's current_streak (0) is the lowest of the four failing habits.
    assert gate["weakest"] == "water"
    assert gate["advice"] == (
        "Stabilise water first — new habits stick best when current ones "
        "are past ~2 weeks and above 60% adherence.")


def test_habits_ledger_detail_ready_gate(tmp_path):
    def seed(con):
        for n in range(14, -1, -1):     # 15-day unbroken streak
            con.execute("INSERT INTO habits_log(date, habit, done, streak)"
                        " VALUES(?, 'gym', 1, ?)", (_iso_days_ago(n), 15 - n))

    data = _care_client(tmp_path, seed).get("/api/dash/habits-ledger-detail").get_json()
    assert data["rows"][0]["status"] == "Building"   # current_streak 15 < 21
    gate = data["gate"]
    assert gate["ready"] is True
    assert gate["weakest"] is None
    assert gate["advice"] == (
        "Every active habit is past ~2 weeks and above 60% adherence — "
        "a good time to add one.")


def test_habits_ledger_detail_lapse_decay(tmp_path):
    """task-50 item 1 (r3 flag 5): the stored `streak` column never resets
    itself if nothing gets logged at all, so an abandoned habit's classifier
    input must decay from the DATES, not the stale stored number.
      abandoned -> 25-day streak that stopped 10 days ago  -> NOT Established
                   (current_streak still DISPLAYS 25 — honest history)
      fresh     -> a live 5-day streak, last done=1 today   -> unaffected
      boundary  -> a 21-day streak whose last done=1 row is
                   yesterday (not today)                    -> still Established
    """
    def seed(con):
        for n in range(34, 9, -1):     # 25 continuous days, ending 10 days ago
            con.execute("INSERT INTO habits_log(date, habit, done, streak)"
                        " VALUES(?, 'abandoned', 1, ?)", (_iso_days_ago(n), 35 - n))
        for n in range(4, -1, -1):     # 5 continuous days, ending today
            con.execute("INSERT INTO habits_log(date, habit, done, streak)"
                        " VALUES(?, 'fresh', 1, ?)", (_iso_days_ago(n), 5 - n))
        for n in range(21, 0, -1):     # 21 continuous days, ending yesterday
            con.execute("INSERT INTO habits_log(date, habit, done, streak)"
                        " VALUES(?, 'boundary', 1, ?)", (_iso_days_ago(n), 22 - n))

    data = _care_client(tmp_path, seed).get("/api/dash/habits-ledger-detail").get_json()
    rows = {r["habit"]: r for r in data["rows"]}

    # a lapsed streak of 0 always trips the Fragile "current_streak == 0"
    # branch (before the Established/Rebuilding checks even see best_streak)
    assert rows["abandoned"]["status"] == "Fragile"
    assert rows["abandoned"]["status"] != "Established"
    assert rows["abandoned"]["current_streak"] == 25   # display stays honest, not zeroed
    assert rows["abandoned"]["best_streak"] == 25

    assert rows["fresh"]["status"] == "Building"
    assert rows["fresh"]["current_streak"] == 5

    assert rows["boundary"]["status"] == "Established"
    assert rows["boundary"]["current_streak"] == 21


def test_habits_ledger_detail_gate_uses_effective_streak(tmp_path):
    """task-50 fix wave: reachable contradiction the gate previously allowed
    — a habit with a stored streak >=14 (raw current_streak) but whose most
    recent done=1 row is 10 days back reads Fragile (lapse-aware classifier),
    yet the old gate used raw current_streak and would still call it ready.
    The gate's ready-streak leg must use the same effective streak."""
    def seed(con):
        for n in range(34, 9, -1):     # 25 continuous days, ending 10 days ago
            con.execute("INSERT INTO habits_log(date, habit, done, streak)"
                        " VALUES(?, 'abandoned', 1, ?)", (_iso_days_ago(n), 35 - n))

    data = _care_client(tmp_path, seed).get("/api/dash/habits-ledger-detail").get_json()
    rows = {r["habit"]: r for r in data["rows"]}
    assert rows["abandoned"]["status"] == "Fragile"
    assert rows["abandoned"]["current_streak"] == 25   # raw display stays honest

    gate = data["gate"]
    assert gate["ready"] is False
    assert gate["weakest"] == "abandoned"


def test_env_latest(dash_client):
    env = dash_client.get("/api/dash/env").get_json()
    assert env["weather"]["condition"] == "overcast"
    assert env["air"]["european_aqi_mean"] == 22.5


def test_today_seeds_rating_and_water(dash_client):
    # fixture logged a day_rating=3 subjective row for today; no intake yet
    t = dash_client.get("/api/dash/today").get_json()
    assert t["day_rating"] == 3
    assert t["water_ml"] is None  # nothing logged -> field seeds empty, buttons start at 0


def test_healthdb_error_returns_json_503(tmp_path):
    """A corrupt/locked health DB must degrade to JSON 503 (per-card error in
    the UI), never a generic HTML 500 (review finding)."""
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"this is not a sqlite file, sorry")
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(bad)})
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    r = client.get("/api/dash/metrics?days=30")
    assert r.status_code == 503
    assert "unavailable" in r.get_json()["error"]


def test_healthdb_missing_is_graceful(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(tmp_path / "missing.db")})
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    r = client.get("/api/dash/metrics?days=30")
    assert r.status_code == 503


def _recovery_client(tmp_path, seed):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    seed(con)
    con.commit()
    con.close()
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(hdb),
    })
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    return client


def test_recovery_detail_requires_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    r = app.test_client().get("/api/dash/recovery-detail?metric=hrv")
    assert r.status_code == 401


def test_recovery_detail_whitelists_days_and_metric(dash_client):
    # bad days → 400 (same whitelist-and-validate style as /metrics)
    assert dash_client.get("/api/dash/recovery-detail?metric=hrv&days=12").status_code == 400
    assert dash_client.get("/api/dash/recovery-detail?metric=hrv&days=evil").status_code == 400
    # unknown metric → 400 (the API guards too; the /recovery/<metric> PAGE 404s)
    assert dash_client.get("/api/dash/recovery-detail?metric=bogus").status_code == 400
    assert dash_client.get("/api/dash/recovery-detail?metric=hrv&days=all").status_code == 200


def test_recovery_detail_shape_and_baseline(tmp_path):
    # 40 apple days of HRV so the 60-day baseline (median) is defined; the
    # latest day is deliberately low so the deterministic chip flags it.
    def seed(con):
        for n in range(1, 41):
            con.execute("INSERT INTO daily_metrics(date, source, hrv_ms, resting_hr, sleep_hours)"
                        " VALUES(date('now', '-' || ? || ' day'), 'apple', 50.0, 55.0, 7.5)", (n,))
        # today, apple: HRV crashes to 30 (well below the 50 median) -> "off"
        con.execute("INSERT INTO daily_metrics(date, source, hrv_ms, resting_hr, sleep_hours)"
                    " VALUES(date('now'), 'apple', 30.0, 55.0, 7.5)")

    d = _recovery_client(tmp_path, seed).get(
        "/api/dash/recovery-detail?metric=hrv&days=all").get_json()
    assert d["metric"] == "hrv" and d["label"] == "HRV" and d["unit"] == "ms"
    assert d["empty"] is False
    assert d["baseline"] == 50            # trailing-60d median of the 50.0 rows
    assert d["header_chip"]["cls"] == "out"   # 30 vs 50 median -> off
    # the breakdown leads with the latest value and the vs-median row
    names = [r["name"] for r in d["breakdown"]]
    assert "Latest" in names and "vs your 60-day median" in names
    # deterministic facts, no fabricated coaching
    assert any("60-day median" in f for f in d["advice"])


def test_recovery_detail_provenance_not_merged(tmp_path):
    # apple + fitbit on the SAME day must stay two separate series, and scalar
    # stats must apple-PREFER (55), never average the two sources (57.5).
    def seed(con):
        con.execute("INSERT INTO daily_metrics(date, source, resting_hr)"
                    " VALUES(date('now'), 'apple', 55.0)")
        con.execute("INSERT INTO daily_metrics(date, source, resting_hr)"
                    " VALUES(date('now'), 'fitbit', 60.0)")

    d = _recovery_client(tmp_path, seed).get(
        "/api/dash/recovery-detail?metric=rhr&days=all").get_json()
    assert set(d["series"].keys()) == {"apple", "fitbit"}
    assert d["series"]["apple"][-1][1] == 55.0
    assert d["series"]["fitbit"][-1][1] == 60.0
    latest_row = next(r for r in d["breakdown"] if r["name"] == "Latest")
    assert latest_row["value"] == "55 bpm"   # apple-preferred, not 57 (the mean)


def test_recovery_detail_baseline_withheld_when_sparse(dash_client):
    # the fixture seeds a single hrv row today: the metric renders (not empty)
    # but the 60-day baseline is honestly withheld below BASELINE_MIN_N points,
    # never quoted off one or two readings.
    d = dash_client.get("/api/dash/recovery-detail?metric=hrv&days=all").get_json()
    assert d["empty"] is False
    assert d["baseline"] is None


def test_recovery_readiness_is_honest_skeleton(tmp_path):
    # readiness has NO engine — the endpoint returns engine:false + REAL
    # component rows (HRV/RHR/sleep), never a fabricated score.
    def seed(con):
        con.execute("INSERT INTO daily_metrics(date, source, hrv_ms, resting_hr, sleep_hours)"
                    " VALUES(date('now'), 'apple', 45.0, 58.0, 6.5)")

    d = _recovery_client(tmp_path, seed).get(
        "/api/dash/recovery-detail?metric=readiness").get_json()
    assert d["engine"] is False
    assert "score" not in d
    got = {r["name"] for r in d["breakdown"]}
    assert {"HRV", "Resting HR", "Sleep duration"} <= got


def test_scores_passes_engine_json_through(dash_client, monkeypatch):
    from app import bridge
    monkeypatch.setattr(bridge, "run", lambda *a, **k: {
        "scores": {"water": {"insufficient_data": True, "reason": "x"}}, "habits": []})
    r = dash_client.get("/api/dash/scores")
    assert r.status_code == 200
    assert r.get_json()["result"]["scores"]["water"]["insufficient_data"] is True


# ── task-52: Data page raw-data gallery — extended/new endpoints ───────────

def test_metrics_extended_columns_shaped(tmp_path):
    # /metrics grew the remaining daily_metrics columns (task-52) so the Data
    # page can chart hr range/energy/exercise/distance/flights/resp/spo2/
    # walking-HR without a second endpoint. Provenance is NEVER merged here —
    # apple + fitbit rows for the same day both come back, each with its own
    # value, exactly like the pre-existing resting_hr/hrv_ms columns.
    def seed(con):
        con.execute(
            "INSERT INTO daily_metrics(date, source, hr_min, hr_avg, hr_max,"
            " active_energy_kcal, basal_energy_kcal, exercise_min, distance_km,"
            " flights, respiratory_rate, spo2_pct, walking_hr_avg)"
            " VALUES('" + LOCAL_TODAY + "', 'apple', 52, 68, 140,"
            " 480.5, 1650.0, 35, 5.2, 9, 14.5, 97.0, 88)")
        con.execute(
            "INSERT INTO daily_metrics(date, source, active_energy_kcal)"
            " VALUES('" + LOCAL_TODAY + "', 'fitbit', 510.0)")

    rows = _care_client(tmp_path, seed).get("/api/dash/metrics?days=7").get_json()["rows"]
    apple = next(r for r in rows if r["source"] == "apple")
    fitbit = next(r for r in rows if r["source"] == "fitbit")
    assert apple["hr_min"] == 52 and apple["hr_avg"] == 68 and apple["hr_max"] == 140
    assert apple["active_energy_kcal"] == 480.5
    assert apple["basal_energy_kcal"] == 1650.0
    assert apple["exercise_min"] == 35 and apple["distance_km"] == 5.2 and apple["flights"] == 9
    assert apple["respiratory_rate"] == 14.5 and apple["spo2_pct"] == 97.0 and apple["walking_hr_avg"] == 88
    # the two sources' active_energy_kcal stay two separate numbers (480.5 /
    # 510.0), never summed or averaged into one merged reading
    assert fitbit["active_energy_kcal"] == 510.0
    assert apple["active_energy_kcal"] != fitbit["active_energy_kcal"]


def test_metrics_extended_columns_empty_by_default(dash_client):
    # the dash_client fixture never sets hr_min/active_energy_kcal/etc — the
    # new daily_metrics columns must come back None, not KeyError/500, so the
    # Data page's per-field charts render their honest-empty state.
    rows = dash_client.get("/api/dash/metrics?days=30").get_json()["rows"]
    assert rows and rows[0]["hr_min"] is None and rows[0]["active_energy_kcal"] is None


def test_body_extended_columns_shaped_and_deduped(tmp_path):
    # /body grew the tape-measurement + body-fat columns (task-52); the
    # existing one-row-per-date de-dup (manual beats hevy) must still apply
    # to the new columns too, same as weight/waist.
    def seed(con):
        con.execute(
            "INSERT INTO body_metrics(date, weight_kg, chest_cm, arm_cm, thigh_cm,"
            " hip_cm, neck_cm, body_fat_pct, source)"
            " VALUES('" + LOCAL_TODAY + "', 63.0, 88.0, 27.0, 49.0, 91.0, 32.0, 22.0, 'hevy')")
        con.execute(
            "INSERT INTO body_metrics(date, weight_kg, chest_cm, source)"
            " VALUES('" + LOCAL_TODAY + "', 63.2, 88.5, 'manual')")

    rows = _care_client(tmp_path, seed).get("/api/dash/body?days=7").get_json()["rows"]
    assert len(rows) == 1   # one row per date even with 2 sources logged
    row = rows[0]
    assert row["chest_cm"] == 88.5   # manual wins over hevy, same as weight_kg
    assert row["weight_kg"] == 63.2


def test_body_extended_columns_empty_by_default(dash_client):
    rows = dash_client.get("/api/dash/body?days=30").get_json()["rows"]
    assert rows == []


def test_env_history_windowed(tmp_path):
    # task-52: /env grew an optional `days` window that ALSO returns
    # weather_history/air_history for the Data page's trend charts, while
    # weather/air (latest row, for the "Today outside" card) stay unwindowed.
    def seed(con):
        con.execute("INSERT INTO weather(date, location, condition, temp_min_c, temp_max_c, uv_index_max)"
                    " VALUES('" + LOCAL_TODAY + "', 'X', 'sunny', 12.0, 19.5, 3.1)")
        con.execute("INSERT INTO weather(date, location, condition, temp_min_c, temp_max_c)"
                    " VALUES(date('now', '-400 day'), 'X', 'rain', 2.0, 6.0)")
        con.execute("INSERT INTO air_quality(date, location, european_aqi_mean, european_aqi_max)"
                    " VALUES('" + LOCAL_TODAY + "', 'X', 22.5, 30.0)")
        con.execute("INSERT INTO air_quality(date, location, european_aqi_mean, european_aqi_max)"
                    " VALUES(date('now', '-400 day'), 'X', 55.0, 70.0)")

    client = _care_client(tmp_path, seed)
    bare = client.get("/api/dash/env").get_json()
    # bare call keeps the pre-existing default 365-day window for history —
    # the -400 day rows are out, but weather/air (latest) are unaffected
    assert bare["weather"]["condition"] == "sunny"
    assert len(bare["weather_history"]) == 1
    assert bare["weather_history"][0]["temp_max_c"] == 19.5
    assert len(bare["air_history"]) == 1

    allw = client.get("/api/dash/env?days=all").get_json()
    assert len(allw["weather_history"]) == 2
    assert len(allw["air_history"]) == 2

    assert client.get("/api/dash/env?days=evil").status_code == 400
    assert client.get("/api/dash/env?days=1").status_code == 200


def test_workouts_empty_by_default(dash_client):
    # workouts (Apple Health cardio/other — distinct from Hevy strength sets)
    # is unseeded on the demo DB; the Data page's per-type chart must render
    # honest-empty, not a 500/404.
    r = dash_client.get("/api/dash/workouts")
    assert r.status_code == 200
    assert r.get_json()["rows"] == []


def test_workouts_shaped_and_windowed(tmp_path):
    def seed(con):
        con.execute("INSERT INTO workouts(date, type, minutes, kcal, km)"
                    " VALUES('" + LOCAL_TODAY + "', 'run', 32.0, 310.0, 5.1)")
        con.execute("INSERT INTO workouts(date, type, minutes, kcal, km)"
                    " VALUES(date('now', '-100 day'), 'cycle', 45.0, 400.0, 18.0)")

    client = _care_client(tmp_path, seed)
    week = client.get("/api/dash/workouts?days=30").get_json()["rows"]
    assert len(week) == 1 and week[0]["type"] == "run" and week[0]["minutes"] == 32.0
    # task-52 fix wave: kcal/km ride along too — the Data page charts all
    # three dimensions, not just minutes (review finding)
    assert week[0]["kcal"] == 310.0 and week[0]["km"] == 5.1
    allw = client.get("/api/dash/workouts?days=all").get_json()["rows"]
    assert len(allw) == 2
    # allw is date-ordered: the -100 day 'cycle' row comes first
    assert allw[0]["type"] == "cycle" and allw[0]["kcal"] == 400.0 and allw[0]["km"] == 18.0
    assert client.get("/api/dash/workouts?days=evil").status_code == 400


def test_hevy_volume_empty_by_default(dash_client):
    r = dash_client.get("/api/dash/hevy-volume")
    assert r.status_code == 200
    assert r.get_json()["rows"] == []


def test_hevy_volume_aggregated_shaped(tmp_path):
    # daily volume = SUM(weight_kg * reps), aggregated server-side (a client
    # can't re-derive this without every set row) + a plain set count.
    def seed(con):
        con.execute("INSERT INTO hevy_sets(date, exercise_title, weight_kg, reps)"
                    " VALUES('" + LOCAL_TODAY + "', 'Sample lift A', 20.0, 5)")
        con.execute("INSERT INTO hevy_sets(date, exercise_title, weight_kg, reps)"
                    " VALUES('" + LOCAL_TODAY + "', 'Sample lift A', 20.0, 5)")
        con.execute("INSERT INTO hevy_sets(date, exercise_title, weight_kg, reps)"
                    " VALUES('" + LOCAL_TODAY + "', 'Plank', NULL, NULL)")  # timed set, no load
        con.execute("INSERT INTO hevy_sets(date, exercise_title, weight_kg, reps)"
                    " VALUES(date('now', '-100 day'), 'Sample lift C', 15.0, 8)")

    client = _care_client(tmp_path, seed)
    week = client.get("/api/dash/hevy-volume?days=30").get_json()["rows"]
    assert len(week) == 1
    assert week[0]["volume_kg"] == 200.0    # 2 x (20*5); the NULL-load set contributes 0
    assert week[0]["sets"] == 3              # but still counts toward the set total
    allw = client.get("/api/dash/hevy-volume?days=all").get_json()["rows"]
    assert len(allw) == 2
    assert client.get("/api/dash/hevy-volume?days=evil").status_code == 400


# ── task-58: hrv_sdnn -> hrv_ms rename — panel must tolerate both DB layouts

def _legacy_hrv_client(tmp_path, seed):
    """Same shape as _recovery_client, but daily_metrics is rebuilt in its
    pre-2026-07-19 shape (hrv_sdnn only, no hrv_ms) — the layout the tracked
    demo DB (and any not-yet-migrated server DB) still has. Uses its own
    subdirectory so a test can build both a legacy and a new-schema DB
    without the two sqlite files colliding."""
    tmp_path = tmp_path / "legacy"
    tmp_path.mkdir()
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("DROP TABLE daily_metrics")
    con.execute("""CREATE TABLE "daily_metrics" (
        date TEXT, source TEXT NOT NULL DEFAULT 'apple',
        "resting_hr" REAL, "hrv_sdnn" REAL, "hr_min" REAL, "hr_avg" REAL,
        "hr_max" REAL, "steps" REAL, "active_energy_kcal" REAL,
        "basal_energy_kcal" REAL, "exercise_min" REAL, "distance_km" REAL,
        "flights" REAL, "respiratory_rate" REAL, "spo2_pct" REAL,
        "walking_hr_avg" REAL, "sleep_hours" REAL, PRIMARY KEY (date, source))""")
    seed(con)
    con.commit()
    con.close()
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "HEALTH_DB": str(hdb),
    })
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    return client


def test_metrics_hrv_key_stable_across_legacy_and_new_schema(tmp_path):
    # legacy DB (hrv_sdnn only): the JSON key is still "hrv_ms" — one stable
    # contract for the JS regardless of which physical column the DB has.
    def seed(con):
        con.execute("INSERT INTO daily_metrics(date, source, hrv_sdnn)"
                    " VALUES('" + LOCAL_TODAY + "', 'apple', 45.5)")
    legacy_rows = _legacy_hrv_client(tmp_path, seed).get(
        "/api/dash/metrics?days=30").get_json()["rows"]
    assert legacy_rows[0]["hrv_ms"] == 45.5
    assert "hrv_sdnn" not in legacy_rows[0]

    # new-schema DB (hrv_ms only, e.g. a fresh install or post-migration server)
    def seed2(con):
        con.execute("INSERT INTO daily_metrics(date, source, hrv_ms)"
                    " VALUES('" + LOCAL_TODAY + "', 'apple', 47.0)")
    new_dir = tmp_path / "new"; new_dir.mkdir()
    new_rows = _recovery_client(new_dir, seed2).get(
        "/api/dash/metrics?days=30").get_json()["rows"]
    assert new_rows[0]["hrv_ms"] == 47.0


def test_recovery_detail_hrv_works_on_legacy_and_new_schema(tmp_path):
    def seed(con):
        for n in range(1, 11):
            con.execute("INSERT INTO daily_metrics(date, source, hrv_sdnn)"
                        " VALUES(date('now', '-' || ? || ' day'), 'apple', 50.0)", (n,))
    d = _legacy_hrv_client(tmp_path, seed).get(
        "/api/dash/recovery-detail?metric=hrv&days=all").get_json()
    assert d["empty"] is False
    names = [r["name"] for r in d["breakdown"]]
    assert "Latest" in names

    def seed2(con):
        for n in range(1, 11):
            con.execute("INSERT INTO daily_metrics(date, source, hrv_ms)"
                        " VALUES(date('now', '-' || ? || ' day'), 'apple', 50.0)", (n,))
    new_dir = tmp_path / "new"; new_dir.mkdir()
    d2 = _recovery_client(new_dir, seed2).get(
        "/api/dash/recovery-detail?metric=hrv&days=all").get_json()
    assert d2["empty"] is False
    names2 = [r["name"] for r in d2["breakdown"]]
    assert "Latest" in names2
