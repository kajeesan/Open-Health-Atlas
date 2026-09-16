"""Phase 8: nutrition endpoints — menu/eat/log-food via stubbed bridge, today via db_read."""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Seed on the CANONICAL canonical timezone day: SQLite date-now is UTC and points
# at yesterday for ~2h after local midnight, while the routes query TEST_TZ-today.
LOCAL_TODAY = datetime.now(ZoneInfo("Europe/Paris")).date().isoformat()

from app import auth as auth_mod
from app import bridge, create_app

SCHEMA = (pathlib.Path(__file__).resolve().parent.parent / "toolkit" / "SCHEMA.sql").read_text()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO recipes(recipe_id, name, batch_grams) VALUES('sample-stew', 'Sample Stew', 2000)")
    con.execute("INSERT INTO nutrition_log(date, recipe_id, food_name, grams, kcal, protein_g, carbs_g, fat_g)"
                " VALUES('" + LOCAL_TODAY + "', 'sample-stew', 'Sample Stew', 400, 600, 50, 36, 24)")
    con.commit(); con.close()
    calls = []
    monkeypatch.setattr(bridge, "run", lambda sub, *a, **k: (calls.append((sub, list(a))) or {"ok": True}))
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(hdb)})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    c.calls = calls
    return c


def test_today_totals(client):
    d = client.get("/api/nutrition/today").get_json()
    assert d["totals"]["kcal"] == 600 and d["totals"]["protein_g"] == 50
    assert d["entries"][0]["food_name"] == "Sample Stew"
    # T45: fiber_g joins the totals (the base fixture's Sample Stew row sets none,
    # so it sums to the honest 0.0 rather than being absent) and `micros` is
    # an empty dict — nutrient_daily has zero rows on this fixture DB.
    assert d["totals"]["fiber_g"] == 0.0
    assert d["micros"] == {}


def test_today_micros_shape_and_source_provenance(tmp_path):
    """T45: /api/nutrition/today's `micros` reads nutrient_daily, keyed by
    nutrient, taking the per-nutrient MAX across sources (PK is
    date/nutrient/source) rather than a silent cross-source merge — same
    provenance rule as daily_metrics elsewhere in the app. Also covers the
    Fibre fallback: nutrition_log.fiber_g summed into totals for rows with no
    nutrient_daily alias."""
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    # Migration 001 owns nutrient_daily and the final schema includes it.
    con.execute("INSERT INTO nutrition_log(date, food_name, grams, fiber_g) VALUES(?, 'Oats', 100, 8.5)",
                (LOCAL_TODAY,))
    # two sources for vitamin_d today -> the higher (apple-style provenance
    # rule) wins, never a silent average/merge
    con.execute("INSERT INTO nutrient_daily(date, nutrient, amount, unit, source)"
                " VALUES(?, 'vitamin_d', 5.0, 'ug', 'cronometer-a')", (LOCAL_TODAY,))
    con.execute("INSERT INTO nutrient_daily(date, nutrient, amount, unit, source)"
                " VALUES(?, 'vitamin_d', 9.0, 'ug', 'cronometer-b')", (LOCAL_TODAY,))
    con.execute("INSERT INTO nutrient_daily(date, nutrient, amount, unit, source)"
                " VALUES(?, 'zinc', 3.0, 'mg', 'cronometer-a')", (LOCAL_TODAY,))
    # an old day must not leak into today's dict
    con.execute("INSERT INTO nutrient_daily(date, nutrient, amount, unit, source)"
                " VALUES(date('now', '-5 day'), 'iron', 2.0, 'mg', 'cronometer-a')")
    con.commit(); con.close()
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(hdb)})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    d = c.get("/api/nutrition/today").get_json()
    assert d["totals"]["fiber_g"] == 8.5
    assert d["micros"] == {
        "vitamin_d": {"amount": 9.0, "unit": "ug"},
        "zinc": {"amount": 3.0, "unit": "mg"},
    }


def test_today_micros_fall_back_to_logged_recipe_portions(tmp_path):
    """Recipe totals are scaled by grams eaten; EPA+DHA are combined while
    the broader Omega-3 value is ignored."""
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute(
        "INSERT INTO recipes(recipe_id, name, batch_grams)"
        " VALUES('sample-stew', 'Sample Vegetable Stew', 2000)")
    con.execute(
        "INSERT INTO nutrition_log(date, recipe_id, food_name, grams)"
        " VALUES(?, 'sample-stew', 'Sample Vegetable Stew', 400)", (LOCAL_TODAY,))
    for nutrient, unit, total in (
            ("Vitamin D", "IU", 800),
            ("Magnesium", "mg", 500),
            ("Iron", "g", 0.04),
            ("Zinc", "mg", 30),
            ("EPA", "g", 1.0),
            ("DHA", "g", 0.5),
            ("Omega-3", "g", 99.0),
            ("Water", "g", 1200)):
        con.execute(
            "INSERT INTO recipe_nutrients(recipe_id, nutrient, unit, per_gram)"
            " VALUES('sample-stew', ?, ?, ?)", (nutrient, unit, total))
    con.commit()
    con.close()
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False, "PANEL_DB": str(tmp_path / "p.db"),
        "PANEL_COOKIE_SECURE": False, "HEALTH_DB": str(hdb),
    })
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())

    assert c.get("/api/nutrition/today").get_json()["micros"] == {
        "iron": {"amount": 8.0, "unit": "mg"},
        "magnesium": {"amount": 100.0, "unit": "mg"},
        "omega3_epa_dha": {"amount": 300.0, "unit": "mg"},
        "vitamin_d": {"amount": 4.0, "unit": "ug"},
        "zinc": {"amount": 6.0, "unit": "mg"},
    }


def test_today_micros_cronometer_wins_per_nutrient(tmp_path):
    """A daily import replaces, rather than adds to, a recipe-derived value;
    nutrients absent from that import still use the recipe fallback."""
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute(
        "INSERT INTO recipes(recipe_id, name, batch_grams)"
        " VALUES('sample-stew', 'Sample Vegetable Stew', 1000)")
    con.execute(
        "INSERT INTO nutrition_log(date, recipe_id, food_name, grams)"
        " VALUES(?, 'sample-stew', 'Sample Vegetable Stew', 500)", (LOCAL_TODAY,))
    con.execute(
        "INSERT INTO recipe_nutrients(recipe_id, nutrient, unit, per_gram)"
        " VALUES('sample-stew', 'Vitamin D', 'ug', 8)")
    con.execute(
        "INSERT INTO recipe_nutrients(recipe_id, nutrient, unit, per_gram)"
        " VALUES('sample-stew', 'Magnesium', 'mg', 200)")
    con.execute(
        "INSERT INTO nutrient_daily(date, nutrient, amount, unit, source)"
        " VALUES(?, 'vitamin_d', 9, 'ug', 'cronometer')", (LOCAL_TODAY,))
    con.commit()
    con.close()
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False, "PANEL_DB": str(tmp_path / "p.db"),
        "PANEL_COOKIE_SECURE": False, "HEALTH_DB": str(hdb),
    })
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())

    assert c.get("/api/nutrition/today").get_json()["micros"] == {
        "magnesium": {"amount": 100.0, "unit": "mg"},
        "vitamin_d": {"amount": 9.0, "unit": "ug"},
    }


def test_recipes_list(client):
    r = client.get("/api/nutrition/recipes").get_json()["recipes"][0]
    assert r["recipe_id"] == "sample-stew"
    # T47: meal_type rides along (NULL until the coach runs recipe-tag)
    assert r["meal_type"] is None


def test_recipes_list_tolerates_legacy_schema_without_meal_type(tmp_path):
    """T47: recipes.meal_type is lazily migrated by the recipe-tag WRITER; a
    pre-T47 DB doesn't have the column and this READ must degrade to
    meal_type=None — never DDL, never a 503."""
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.execute("CREATE TABLE recipes (recipe_id TEXT PRIMARY KEY, name TEXT,"
                " batch_grams REAL, grams_per_portion REAL, portions INTEGER,"
                " notes TEXT, source TEXT DEFAULT 'cronometer',"
                " ingested_at TEXT DEFAULT (datetime('now')))")
    con.execute("INSERT INTO recipes(recipe_id, name, batch_grams) VALUES('sample-stew', 'Sample Stew', 2000)")
    con.commit(); con.close()
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(hdb)})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    d = c.get("/api/nutrition/recipes").get_json()
    assert d["recipes"][0]["meal_type"] is None
    # the read-only connection cannot have migrated anything
    con = sqlite3.connect(hdb)
    cols = [row[1] for row in con.execute("PRAGMA table_info(recipes)")]
    con.close()
    assert "meal_type" not in cols


# ---- T47: /api/nutrition/recipe-gaps (read-composition: bridge targets +
# db_read consumed/recipes — the athletic-detail precedent, no new subcommand)

# A realistic T44 nutrition-targets payload for the fake bridge (only the
# fields the route consumes, plus fibre's null target which must be ignored).
GAP_TARGETS = {
    "status": "ok",
    "targets": {
        "kcal": {"target": 3000, "band_low": 2700, "band_high": 3300},
        "protein_g": {"target": 150},
        "carbs_g": {"target": 400}, "fat_g": {"target": 83},
        "water_ml": {"target": 2800},
        "micros": [
            {"nutrient": "magnesium", "unit": "mg", "target": 300.0},
            {"nutrient": "vitamin_d", "unit": "ug", "target": 12.0},
            {"nutrient": "iron", "unit": "mg", "target": 12.0},
            {"nutrient": "fibre", "unit": "g", "target": None},
        ],
    },
}


@pytest.fixture()
def gap_env(tmp_path, monkeypatch):
    """Seeded recipes + today's partial intake, fake bridge answering
    nutrition-targets with GAP_TARGETS. Returns (client, health_db_path) so a
    test can log more food and watch the deficits shrink."""
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO recipes(recipe_id, name, batch_grams, grams_per_portion)"
                " VALUES('sample-stew', 'Sample Vegetable Stew', 2000, 400)")
    con.execute("INSERT INTO recipes(recipe_id, name, batch_grams, grams_per_portion)"
                " VALUES('salad', 'Salad', 1000, 250)")
    con.execute("INSERT INTO recipes(recipe_id, name) VALUES('nobatch', 'No Batch Yet')")
    # per_gram holds RECIPE TOTALS (health.py convention)
    for rid, nutrient, unit, total in (
            ("sample-stew", "Energy", "kcal", 3000), ("sample-stew", "Protein", "g", 250),
            ("sample-stew", "Carbs", "g", 180),                    # unmapped -> ignored
            ("sample-stew", "Magnesium", "mg", 500), ("sample-stew", "Iron", "mg", 40),
            ("salad", "Energy", "kcal", 800), ("salad", "Protein", "g", 60),
            ("nobatch", "Energy", "kcal", 1000)):
        con.execute("INSERT INTO recipe_nutrients(recipe_id, nutrient, unit, per_gram)"
                    " VALUES(?,?,?,?)", (rid, nutrient, unit, total))
    # today so far: 2000 kcal, 100 g protein logged; 200 mg magnesium imported
    con.execute("INSERT INTO nutrition_log(date, food_name, grams, kcal, protein_g)"
                " VALUES(?, 'Lunch', 500, 2000, 100)", (LOCAL_TODAY,))
    con.execute("INSERT INTO nutrient_daily(date, nutrient, amount, unit)"
                " VALUES(?, 'magnesium', 200.0, 'mg')", (LOCAL_TODAY,))
    con.commit(); con.close()
    monkeypatch.setattr(bridge, "run",
                        lambda sub, *a, **k: GAP_TARGETS if sub == "nutrition-targets" else {"ok": True})
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(hdb)})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    return c, hdb


def test_recipe_gaps_ranking_hand_computed(gap_env):
    """Deficits: kcal 1000, protein 50, magnesium 100, vitamin_d 12, iron 12.
    Sample Stew / portion: 600 kcal, 50 g protein, 100 mg Mg, 8 mg Fe ->
      600/3000 + 50/150 + 100/300 + 8/12 = 1.533
    Salad / portion: 200 kcal, 15 g protein -> 200/3000 + 15/150 = 0.167.
    'nobatch' has no batch weight -> per-portion math can't run -> excluded
    (honest omission, never a fabricated portion)."""
    c, _ = gap_env
    r = c.get("/api/nutrition/recipe-gaps")
    assert r.status_code == 200
    res = r.get_json()["result"]
    assert res["status"] == "ok"
    assert [x["recipe_id"] for x in res["recipes"]] == ["sample-stew", "salad"]
    stew, salad = res["recipes"]
    assert stew["score"] == 1.533
    assert stew["closes"] == ["iron", "magnesium", "protein"]
    assert salad["score"] == 0.167
    assert salad["closes"] == ["protein", "kcal"]


def test_recipe_gaps_deficit_shrinks_after_logging(gap_env):
    """Once protein is fully eaten its
    deficit hits 0, so it drops out of every score and closes-list."""
    c, hdb = gap_env
    con = sqlite3.connect(hdb)
    con.execute("INSERT INTO nutrition_log(date, food_name, grams, protein_g)"
                " VALUES(?, 'Shake', 300, 60)", (LOCAL_TODAY,))   # protein 100 -> 160 >= 150
    con.commit(); con.close()
    res = c.get("/api/nutrition/recipe-gaps").get_json()["result"]
    stew = next(x for x in res["recipes"] if x["recipe_id"] == "sample-stew")
    assert stew["score"] == 1.2
    assert stew["closes"] == ["iron", "magnesium", "kcal"]
    salad = next(x for x in res["recipes"] if x["recipe_id"] == "salad")
    assert salad["score"] == 0.067 and salad["closes"] == ["kcal"]


def test_recipe_gaps_insufficient_targets_passthrough(tmp_path, monkeypatch):
    """No profile -> the honest insufficient_data shape, never a fake ranking."""
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.commit(); con.close()
    monkeypatch.setattr(bridge, "run", lambda *a, **k: {
        "status": "insufficient_data",
        "reason": "user profile incomplete — profile-set height_cm/sex/dob"})
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(hdb)})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    r = c.get("/api/nutrition/recipe-gaps")
    assert r.status_code == 200
    res = r.get_json()["result"]
    assert res["status"] == "insufficient_data"
    assert "profile" in res["reason"]
    assert "recipes" not in res


def test_recipe_gaps_bridge_error(tmp_path, monkeypatch):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.commit(); con.close()
    monkeypatch.setattr(bridge, "run",
                        lambda *a, **k: (_ for _ in ()).throw(bridge.BridgeError("broker down")))
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(hdb)})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    r = c.get("/api/nutrition/recipe-gaps")
    assert r.status_code == 502
    assert r.get_json() == {"ok": False, "error": "broker down"}


def test_menu_via_bridge(client):
    assert client.get("/api/nutrition/menu").status_code == 200
    assert client.calls[-1] == ("menu", [])


def test_targets_passthrough_ok(client):
    # T45: the fixture's fake bridge answers every subcommand with
    # {"ok": True} — this just proves the route calls "nutrition-targets"
    # with zero flags and echoes the engine JSON back untouched under
    # ok/result, same shape as insights.py's _engine().
    r = client.get("/api/nutrition/targets")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["result"] == {"ok": True}
    assert client.calls[-1] == ("nutrition-targets", [])


def test_targets_passthrough_bridge_error(tmp_path, monkeypatch):
    # An insufficient_data reply is NOT a bridge error (it's a normal 200
    # passthrough, covered above) — this is a genuine bridge/broker failure.
    monkeypatch.setattr(bridge, "run",
                        lambda *a, **k: (_ for _ in ()).throw(bridge.BridgeError("broker down")))
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    r = c.get("/api/nutrition/targets")
    assert r.status_code == 502
    assert r.get_json() == {"ok": False, "error": "broker down"}


def test_coverage_passthrough_ok(client):
    # T46: the fixture's fake bridge answers every subcommand with
    # {"ok": True} — this proves the route calls "nutrition-coverage" with
    # the default --days 7 and echoes the engine JSON back untouched under
    # ok/result, same shape as /api/nutrition/targets.
    r = client.get("/api/nutrition/coverage")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and body["result"] == {"ok": True}
    assert client.calls[-1] == ("nutrition-coverage", ["--days", "7"])


def test_coverage_days_whitelist(client):
    assert client.get("/api/nutrition/coverage?days=30").status_code == 200
    assert client.calls[-1] == ("nutrition-coverage", ["--days", "30"])
    assert client.get("/api/nutrition/coverage?days=200").status_code == 400
    assert client.get("/api/nutrition/coverage?days=evil").status_code == 400


def test_coverage_passthrough_bridge_error(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "run",
                        lambda *a, **k: (_ for _ in ()).throw(bridge.BridgeError("broker down")))
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    r = c.get("/api/nutrition/coverage")
    assert r.status_code == 502
    assert r.get_json() == {"ok": False, "error": "broker down"}


def test_eat_validation_and_mapping(client):
    assert client.post("/api/nutrition/eat", json={"recipe": "sample-stew", "portions": 2}).status_code == 200
    assert client.calls[-1] == ("eat", ["sample-stew", "--portions", "2", "--source", "panel-ui"])
    assert client.post("/api/nutrition/eat", json={"recipe": "sample-stew", "portions": 99}).status_code == 400
    assert client.post("/api/nutrition/eat", json={"recipe": "--evil"}).status_code == 400


def test_log_food_validation_and_mapping(client):
    assert client.post("/api/nutrition/log-food", json={"recipe": "sample-stew", "grams": 350}).status_code == 200
    assert client.calls[-1] == ("log-food", ["sample-stew", "--grams", "350.0", "--source", "panel-ui"])
    assert client.post("/api/nutrition/log-food", json={"recipe": "sample-stew", "grams": 0}).status_code == 400


def test_food_occurrence_metadata_validation_and_mapping(client):
    body = {"recipe": "sample-stew", "portions": 1, "date": "2026-07-22",
            "time": "07:05", "meal_type": "breakfast"}
    assert client.post("/api/nutrition/eat", json=body).status_code == 200
    assert client.calls[-1] == (
        "eat", ["sample-stew", "--portions", "1", "--date", "2026-07-22",
                "--time", "07:05", "--meal-type", "breakfast", "--source", "panel-ui"],
    )
    for patch in ({"time": "7:05"}, {"meal_type": "brunch"},
                  {"date": "22-07-2026"}, {"extra": "no"}):
        invalid = {"recipe": "sample-stew", "grams": 100, **patch}
        assert client.post("/api/nutrition/log-food", json=invalid).status_code == 400


def test_water_empty_by_default(client):
    # fixture seeds no intake rows -> the Water card must be honest-empty,
    # not a 500 (same precedent as /api/dash/sleep's empty-by-default test)
    r = client.get("/api/nutrition/water")
    assert r.status_code == 200
    assert r.get_json()["rows"] == []


def test_water_shape_and_days_param(tmp_path):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO intake(date, water_ml) VALUES(?, ?)", (LOCAL_TODAY, 1500))
    con.execute("INSERT INTO intake(date, water_ml) VALUES(date('now', '-100 day'), 900)")
    con.commit(); con.close()
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(hdb)})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    # default window is 7d: the -100 day row falls outside it
    rows = c.get("/api/nutrition/water").get_json()["rows"]
    assert len(rows) == 1 and rows[0]["water_ml"] == 1500
    # "all" drops the lower bound -> both rows
    rows_all = c.get("/api/nutrition/water?days=all").get_json()["rows"]
    assert len(rows_all) == 2
    # 365 also spans the -100 day row
    assert len(c.get("/api/nutrition/water?days=365").get_json()["rows"]) == 2
    # whitelist rejects anything off-list (incl. a bare int that used to pass)
    assert c.get("/api/nutrition/water?days=200").status_code == 400
    assert c.get("/api/nutrition/water?days=evil").status_code == 400
    assert c.get("/api/nutrition/water?days=0").status_code == 400


def test_calories_trend_grouped_and_days_param(tmp_path):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    # two foods logged today -> one summed daily point; one old row out of 7d
    con.execute("INSERT INTO nutrition_log(date, food_name, grams, kcal) VALUES(?, 'A', 100, 300)", (LOCAL_TODAY,))
    con.execute("INSERT INTO nutrition_log(date, food_name, grams, kcal) VALUES(?, 'B', 100, 250)", (LOCAL_TODAY,))
    con.execute("INSERT INTO nutrition_log(date, food_name, grams, kcal) VALUES(date('now', '-100 day'), 'Old', 100, 999)")
    con.commit(); con.close()
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(hdb)})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    # default 7d: today's two foods summed into one point, old row excluded
    rows = c.get("/api/nutrition/calories").get_json()["rows"]
    assert len(rows) == 1 and rows[0]["kcal"] == 550 and rows[0]["date"] == LOCAL_TODAY
    # "all" includes the old row -> two daily points
    assert len(c.get("/api/nutrition/calories?days=all").get_json()["rows"]) == 2
    # whitelist enforced, same as /water
    assert c.get("/api/nutrition/calories?days=200").status_code == 400
    assert c.get("/api/nutrition/calories?days=evil").status_code == 400
    assert c.get("/api/nutrition/calories?days=0").status_code == 400


def test_calories_default_window(client):
    # client fixture seeds one nutrition_log row today (600 kcal) -> one point
    d = client.get("/api/nutrition/calories").get_json()
    assert d["rows"][-1]["kcal"] == 600


def test_supplements_empty_by_default(client):
    # fixture seeds no supplement_products/supplements_log rows -> honest-empty
    r = client.get("/api/nutrition/supplements")
    assert r.status_code == 200
    assert r.get_json() == {"products": [], "today": []}


def test_supplements_shape(tmp_path):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO supplement_products(name, brand, dose, unit, form, schedule, active, notes)"
                " VALUES('Creatine', 'BrandX', 5, 'g', 'powder', 'morning', 1, 'take with water')")
    con.execute("INSERT INTO supplement_products(name, active) VALUES('Retired', 0)")
    sid = con.execute("SELECT supplement_id FROM supplement_products WHERE name='Creatine'").fetchone()[0]
    con.execute("INSERT INTO supplements_log(date, supplement_id, taken) VALUES(?, ?, 1)", (LOCAL_TODAY, sid))
    con.commit(); con.close()
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(hdb)})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    d = c.get("/api/nutrition/supplements").get_json()
    # the inactive 'Retired' product is filtered out
    assert len(d["products"]) == 1 and d["products"][0]["name"] == "Creatine"
    assert d["products"][0]["notes"] == "take with water"
    assert d["today"][0]["supplement_id"] == sid and d["today"][0]["taken"] == 1


def test_supplements_adherence_empty_no_active_products(client):
    # fixture seeds no supplement_products at all -> nothing to divide by
    d = client.get("/api/nutrition/supplements/adherence").get_json()
    assert d == {"rows": [], "active_products": 0}


def test_supplement_observations_preserve_unknown_zero_and_any_intake(client):
    """CLI observations retain nullable meaning in both supplement read models."""
    health = pathlib.Path(__file__).resolve().parents[1] / "toolkit" / "health.py"

    def log(table, *fields):
        result = subprocess.run(
            [sys.executable, str(health), "log", table, *fields],
            env={**os.environ, "HEALTH_DB": client.application.config["HEALTH_DB"]},
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr or result.stdout

    for name in ("Unknown", "Skipped", "Taken"):
        log("supplement_products", f"name={name}", "active=1")
    products = client.get("/api/nutrition/supplements").get_json()["products"]
    ids = {product["name"]: product["supplement_id"] for product in products}
    day = datetime.fromisoformat(LOCAL_TODAY)
    skipped_day = (day - timedelta(days=1)).date().isoformat()
    unknown_day = (day - timedelta(days=2)).date().isoformat()
    old_day = (day - timedelta(days=400)).date().isoformat()
    for name, value in (("Unknown", None), ("Skipped", 0), ("Taken", 1),
                        ("Taken", 0), ("Taken", None)):
        fields = [f"date={LOCAL_TODAY}", f"supplement_id={ids[name]}"]
        if value is not None:
            fields.append(f"taken={value}")
        log("supplements_log", *fields)
    log("supplements_log", f"date={skipped_day}", f"supplement_id={ids['Skipped']}", "taken=0")
    log("supplements_log", f"date={unknown_day}", f"supplement_id={ids['Unknown']}")
    log("supplements_log", f"date={old_day}", f"supplement_id={ids['Skipped']}", "taken=0")

    today = client.get("/api/nutrition/supplements").get_json()["today"]
    assert {row["supplement_id"]: row["taken"] for row in today} == {
        ids["Unknown"]: None, ids["Skipped"]: 0, ids["Taken"]: 1,
    }
    expected = [{"date": skipped_day, "pct": 0.0}, {"date": LOCAL_TODAY, "pct": 33.3}]
    assert client.get("/api/nutrition/supplements/adherence").get_json()["rows"] == expected
    assert client.get("/api/nutrition/supplements/adherence?days=all").get_json()["rows"] == [
        {"date": old_day, "pct": 0.0}, *expected,
    ]


def test_supplements_adherence_formula_and_days_param(tmp_path):
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    # two active products -> denominator 2; one inactive product must not count
    con.execute("INSERT INTO supplement_products(supplement_id, name, active) VALUES(1, 'Creatine', 1)")
    con.execute("INSERT INTO supplement_products(supplement_id, name, active) VALUES(2, 'Vitamin D', 1)")
    con.execute("INSERT INTO supplement_products(supplement_id, name, active) VALUES(3, 'Retired', 0)")
    # today: both active supplements taken -> 100%
    con.execute("INSERT INTO supplements_log(date, supplement_id, taken) VALUES(?, 1, 1)", (LOCAL_TODAY,))
    con.execute("INSERT INTO supplements_log(date, supplement_id, taken) VALUES(?, 2, 1)", (LOCAL_TODAY,))
    # an old day (outside the 7d default window) taking only one -> 50%, and a
    # taken=0 row that must not count toward the numerator
    con.execute("INSERT INTO supplements_log(date, supplement_id, taken)"
                " VALUES(date('now', '-100 day'), 1, 1)")
    con.execute("INSERT INTO supplements_log(date, supplement_id, taken) VALUES(?, 3, 0)", (LOCAL_TODAY,))
    con.commit(); con.close()
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(hdb)})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    # default 7d window: only today's 2/2 taken point -> 100%, old row excluded
    d = c.get("/api/nutrition/supplements/adherence").get_json()
    assert d["active_products"] == 2
    assert d["rows"] == [{"date": LOCAL_TODAY, "pct": 100.0}]
    # "all" includes the old -100d row (1/2 active taken that day -> 50%)
    rows_all = c.get("/api/nutrition/supplements/adherence?days=all").get_json()["rows"]
    assert len(rows_all) == 2
    assert {r["pct"] for r in rows_all} == {50.0, 100.0}
    # whitelist enforced, same as /water and /calories
    assert c.get("/api/nutrition/supplements/adherence?days=200").status_code == 400


def test_supplements_adherence_excludes_inactive_product_from_numerator(tmp_path):
    # Regression: a retired product's taken=1 rows must NOT inflate the pct
    # against the active-only denominator. On '-2 day' ONLY the inactive
    # product was taken (no active product logged) -> that day must not
    # appear in rows at all (a buggy un-joined query would show it as 50%).
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO supplement_products(supplement_id, name, active) VALUES(1, 'Creatine', 1)")
    con.execute("INSERT INTO supplement_products(supplement_id, name, active) VALUES(2, 'Vitamin D', 1)")
    con.execute("INSERT INTO supplement_products(supplement_id, name, active) VALUES(3, 'Retired', 0)")
    con.execute("INSERT INTO supplements_log(date, supplement_id, taken)"
                " VALUES(date('now', '-2 day'), 3, 1)")
    # today: one active supplement taken, so today legitimately shows 50%
    con.execute("INSERT INTO supplements_log(date, supplement_id, taken) VALUES(?, 1, 1)", (LOCAL_TODAY,))
    con.commit(); con.close()
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(hdb)})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    d = c.get("/api/nutrition/supplements/adherence?days=7").get_json()
    assert d["active_products"] == 2
    # only today's row appears; the retired-product-only day is absent entirely
    assert d["rows"] == [{"date": LOCAL_TODAY, "pct": 50.0}]
    assert c.get("/api/nutrition/supplements/adherence?days=evil").status_code == 400


def test_prep_validation_and_mapping(client):
    # portions maps through; optional batch_grams appends --batch-grams
    assert client.post("/api/nutrition/prep", json={"recipe": "sample-stew", "portions": 3}).status_code == 200
    assert client.calls[-1] == ("prep", ["sample-stew", "--portions", "3"])
    assert client.post("/api/nutrition/prep",
                       json={"recipe": "sample-stew", "portions": 1, "batch_grams": 2000}).status_code == 200
    assert client.calls[-1] == ("prep", ["sample-stew", "--portions", "1", "--batch-grams", "2000.0"])
    # out-of-range portions + a flag-injection recipe are rejected before the bridge
    assert client.post("/api/nutrition/prep", json={"recipe": "sample-stew", "portions": 99}).status_code == 400
    assert client.post("/api/nutrition/prep", json={"recipe": "--evil", "portions": 1}).status_code == 400
    assert client.post("/api/nutrition/prep", json={"recipe": "sample-stew", "portions": 1,
                                                    "batch_grams": "x"}).status_code == 400


@pytest.fixture()
def rec_client(tmp_path, monkeypatch):
    """A recipe with a full batch weight + per-portion nutrients + freezer
    stock, so the detail endpoint's real per-portion math is exercised."""
    hdb = tmp_path / "health.db"
    con = sqlite3.connect(hdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO recipes(recipe_id, name, batch_grams, grams_per_portion, portions)"
                " VALUES('sample-stew', 'Sample Vegetable Stew', 2000, 400, 5)")
    # per_gram holds the RECIPE TOTAL (health.py convention) -> per portion =
    # total / batch_grams * grams_per_portion
    for nutrient, unit, total in (("Energy", "kcal", 3000), ("Protein", "g", 250),
                                  ("Carbs", "g", 180), ("Fat", "g", 120), ("Iron", "mg", 40)):
        con.execute("INSERT INTO recipe_nutrients(recipe_id, nutrient, unit, per_gram)"
                    " VALUES('sample-stew', ?, ?, ?)", (nutrient, unit, total))
    con.execute("INSERT INTO meal_inventory(recipe_id, portions_remaining, grams_per_portion, prepped_on)"
                " VALUES('sample-stew', 3, 400, '2026-07-14')")
    con.commit(); con.close()
    recipe_dir = tmp_path / "personal" / "recipes"
    recipe_dir.mkdir(parents=True)
    (recipe_dir / "sample-stew.ingredients.json").write_text(json.dumps({
        "recipe_id": "sample-stew",
        "recipe_name": "Sample Vegetable Stew",
        "ingredients": [
            {"name": "Chicken breast", "amount": 1800, "unit": "g"},
            {"name": "Spice mix", "amount": 2, "unit": "tbsp", "weight_g": 30},
        ],
        "updated_at": "2026-07-24T12:00:00+02:00",
    }))
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False,
                      "HEALTH_DB": str(hdb), "VAULT_DIR": str(tmp_path)})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    return c


def test_recipe_detail_shape(rec_client):
    d = rec_client.get("/api/nutrition/recipe/sample-stew").get_json()
    assert d["recipe"]["name"] == "Sample Vegetable Stew" and d["recipe"]["portions"] == 5
    assert d["portions_remaining"] == 3
    assert d["kcal_per_portion"] == 600         # 3000 / 2000 * 400
    macros = {m["name"]: m["amount"] for m in d["macros"]}
    assert macros == {"Protein": 50.0, "Carbs": 36.0, "Fat": 24.0}
    # macros ordered Protein, Carbs, Fat for the bars
    assert [m["name"] for m in d["macros"]] == ["Protein", "Carbs", "Fat"]
    # a non-macro nutrient is a real micronutrient (amount per portion, with unit)
    assert d["micronutrients"] == [{"name": "Iron", "amount": 8.0, "unit": "mg"}]
    assert d["ingredients_status"] == "ok"
    assert d["ingredients"] == [
        {"name": "Chicken breast", "amount": 1800, "unit": "g"},
        {"name": "Spice mix", "amount": 2, "unit": "tbsp", "weight_g": 30},
    ]


def test_recipe_detail_missing_ingredients_is_honest_empty(client):
    d = client.get("/api/nutrition/recipe/sample-stew").get_json()
    assert d["ingredients_status"] == "missing"
    assert d["ingredients"] == []


def test_recipe_detail_macros_pending_without_batch(client):
    # the base fixture's recipe has batch_grams but no grams_per_portion ->
    # per-portion math can't run -> honest null amounts, not fabricated zeros
    d = client.get("/api/nutrition/recipe/sample-stew").get_json()
    assert d["kcal_per_portion"] is None
    assert all(m["amount"] is None for m in d["macros"]) or d["macros"] == []


def test_recipe_detail_404(rec_client):
    assert rec_client.get("/api/nutrition/recipe/nope").status_code == 404


def test_recipe_detail_page_404_and_ok(rec_client):
    # the page-route validates the id against real recipes (clean 404, no shell)
    assert rec_client.get("/nutrition/recipe/nope").status_code == 404
    assert rec_client.get("/nutrition/recipe/sample-stew").status_code == 200


def test_requires_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p2.db"), "PANEL_COOKIE_SECURE": False})
    c = app.test_client()
    assert c.post("/api/nutrition/eat", json={"recipe": "x"}).status_code == 401
    assert c.post("/api/nutrition/prep", json={"recipe": "x", "portions": 1}).status_code == 401
    assert c.get("/api/nutrition/recipe/sample-stew").status_code == 401
    assert c.get("/api/nutrition/supplements").status_code == 401
    assert c.get("/api/nutrition/supplements/adherence").status_code == 401
    assert c.get("/api/nutrition/targets").status_code == 401
    assert c.get("/api/nutrition/coverage").status_code == 401
    assert c.get("/api/nutrition/recipe-gaps").status_code == 401
