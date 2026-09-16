"""T48 — `readiness` engine: equal-weight composite reusing scores()'s own
sleep/hrv/rhr math (factored, never forked), the 7-group muscle_recovery
snapshot, and soreness_note ingestion. Same conventions as the other suites
(real CLI, structure from SCHEMA.sql, then the explicit development-v6
migration path)."""
import json
import os
import pathlib
import re
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
CODE_VERSION = "d" * 40

GROUPS = ["Chest", "Back", "Arms", "Shoulders", "Legs", "Core", "Glutes"]


def run(db, *args, expect_ok=True):
    r = subprocess.run([sys.executable, str(HEALTH), *args],
                       env={**os.environ, "HEALTH_DB": str(db),
                            "HERMES_CODE_VERSION": CODE_VERSION},
                       capture_output=True, text=True, timeout=60)
    if expect_ok:
        assert r.returncode == 0, f"{args}: {r.stderr or r.stdout}"
        return json.loads(r.stdout)
    assert r.returncode != 0
    return r.stderr or r.stdout


def _schema_only_database(path):
    """Create structure only; the ledger deliberately remains empty."""

    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return path


def _development_v6_database(path):
    """Initialize an ordinary fixture through the supported schema contract.

    ``SCHEMA.sql`` is structure-only.  Development-v6 Recovery fixtures must
    explicitly run the migration owner from ledger version 0 through 6;
    readiness itself remains a read-only consumer and never performs DDL.
    """

    _schema_only_database(path)
    migrated = run(
        path, "migrate", "--to", "6", "--expected-from", "0",
    )
    assert migrated["to_version"] == 6
    assert [row["version"] for row in migrated["applied"]] == list(range(1, 7))
    return path


@pytest.fixture()
def db(tmp_path):
    return _development_v6_database(tmp_path / "health.db")



def test_migrated_v7_readiness_uses_distinct_schema_without_mutating_snapshot(db):
    migrated = run(db, "migrate", "--to", "7", "--expected-from", "6")
    assert migrated["to_version"] == 7
    assert [row["version"] for row in migrated["applied"]] == [7]
    before = db.read_bytes()
    result = readiness(db, anchor="2026-06-30")
    assert result["evidence"]["snapshot_integrity"]["schema_version"] == 7
    assert db.read_bytes() == before


def readiness(db, anchor=None):
    args = ["readiness"]
    if anchor is not None:
        args += ["--anchor", anchor]
    return run(db, *args)


def seed(db, sql, rows):
    con = sqlite3.connect(db)
    con.executemany(sql, rows)
    con.commit()
    con.close()


def rows(db, sql):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


def day(back=0):
    return (datetime.now(TEST_TZ).date() - timedelta(days=back)).isoformat()


def test_insufficient_data_on_empty_db(db):
    r = readiness(db)
    assert r["status"] == "insufficient_data"
    assert "score" not in r and "band" not in r and "drag" not in r
    assert r["components"] == []
    assert r["soreness"] is None
    assert r["disclaimer"] == ("Transparent heuristic over your own baselines — "
                               "not medical advice.")
    # muscle_recovery is independent of the composite -> still a full 7-row
    # honest "never logged" snapshot, not omitted
    mr = {row["group"]: row for row in r["muscle_recovery"]}
    assert set(mr) == set(GROUPS)
    for g in GROUPS:
        assert mr[g]["days_since"] is None
        assert mr[g]["note"] == "never logged"
        assert mr[g]["sets_7d"] == 0.0
        assert mr[g]["sore"] is False


def test_single_component_is_still_insufficient(db):
    # sleep only -> 1 component, still short of the >=2 gate
    seed(db, "INSERT INTO daily_metrics(date, source, sleep_hours) VALUES(?,?,?)",
         [(day(0), "apple", 6.0)])
    r = readiness(db)
    assert r["status"] == "insufficient_data"
    assert len(r["components"]) == 1
    assert r["components"][0]["key"] == "sleep"
    assert "needs >= 2" in r["reason"]


def test_components_reuse_scores_math_and_drag_arithmetic(db):
    """Pins the T48 reuse rule end-to-end: readiness's sleep/hrv components
    must equal scores()'s OWN sleep/recovery formulas on the identical DB —
    not a re-derivation. Numbers chosen so the drag math is EXACT (no
    rounding fuzz): sleep 7.2h/8h -> 90; hrv 54 vs a 50-baseline ->
    50+250*(54-50)/50 = 70. mean=80, drag = (100-90)/2=5.0 and
    (100-70)/2=15.0, summing exactly to 100-80."""
    hrv_baseline_rows = [(day(b), "apple", 50.0) for b in range(1, 21)]
    seed(db, "INSERT INTO daily_metrics(date, source, hrv_ms) VALUES(?,?,?)",
         hrv_baseline_rows)
    seed(db, "INSERT INTO daily_metrics(date, source, sleep_hours, hrv_ms) VALUES(?,?,?,?)",
         [(day(0), "apple", 7.2, 54.0)])

    r = readiness(db)
    assert r["status"] == "ok"
    by_key = {c["key"]: c for c in r["components"]}
    assert set(by_key) == {"sleep", "hrv"}          # rhr never seeded -> omitted, never guessed
    assert by_key["sleep"]["score"] == 90
    assert by_key["hrv"]["score"] == 70

    # cross-check against scores() on the SAME db — the reuse pin
    s = run(db, "scores")["scores"]
    assert s["sleep"]["score"] == 90
    # scores()'s combined recovery mean of rhr+hrv would differ (rhr absent
    # here so recovery is insufficient) -> confirm hrv's OWN formula matches
    # by re-deriving it independently is redundant with the hand-computation
    # above; the important invariant is s["sleep"]["score"] == component here.

    assert r["score"] == 80 and r["band"] == "good"
    drag_by_key = {d["key"]: d for d in r["drag"]}
    assert drag_by_key["sleep"]["points"] == 5.0
    assert drag_by_key["hrv"]["points"] == 15.0
    assert drag_by_key["sleep"]["label"] == "sleep — 5.0 pts"
    assert round(sum(d["points"] for d in r["drag"]), 4) == 100 - r["score"]
    # sorted worst-first
    assert [d["key"] for d in r["drag"]] == ["hrv", "sleep"]


def test_readiness_never_builds_hrv_baseline_across_source_semantics(db):
    """Fitbit RMSSD cannot borrow Apple SDNN rows to clear the 14-day gate."""
    seed(
        db,
        "INSERT INTO daily_metrics(date,source,resting_hr,hrv_ms) VALUES(?,?,?,?)",
        [(day(back), "apple", 60.0, 50.0) for back in range(1, 21)],
    )
    seed(
        db,
        "INSERT INTO daily_metrics(date,source,resting_hr,hrv_ms) VALUES(?,?,?,?)",
        [(day(back), "fitbit", 58.0 if back == 0 else 60.0,
          54.0 if back == 0 else 50.0)
         for back in range(0, 11)],
    )
    seed(
        db,
        "INSERT INTO sleep_log(date,time_asleep_hours,quality,source) VALUES(?,?,?,?)",
        [(day(0), 7.2, 4, "synthetic-fixture")],
    )

    result = readiness(db)
    assert result["status"] == "insufficient_data"
    assert {item["key"] for item in result["components"]} == {"sleep"}

    evidence = result["evidence"]
    assert evidence["contract"] == "readiness-evidence-v2"
    assert evidence["snapshot_integrity"] == {
        "contract": "openhealthatlas-readiness-ancestry-v2",
        "status": "verified",
        "scope": "current_snapshot_integrity_and_reproducibility",
        "schema_version": 6,
        "external_provider_sync": "not_performed",
        "training_ancestry": "manifested",
    }
    for key in ("hrv", "rhr"):
        component = next(
            item for item in evidence["components"] if item["key"] == key
        )
        assert component["status"] == "excluded"
        assert component["reason_code"] == "insufficient_same_source_baseline"
        assert component["current"] == {
            "table": "daily_metrics",
            "locator": f"daily_metrics:{day(0)}:fitbit",
            "source_label": "fitbit",
            "observed_at": day(0),
        }
        assert component["baseline"]["observation_count"] == 10
        assert len(component["baseline"]["locators"]) == 10
        assert all(
            locator.endswith(":fitbit")
            for locator in component["baseline"]["locators"]
        )
        warning = next(
            item for item in evidence["warnings"] if item["component"] == key
        )
        assert warning == {
            "code": "insufficient_same_source_baseline",
            "component": key,
            "source_label": "fitbit",
            "required": 14,
            "observed": 10,
            "other_source_observations_excluded": 20,
        }


def test_readiness_evidence_fingerprints_exact_same_source_inputs(db):
    """An accepted baseline exposes only Fitbit locators and fingerprints values."""
    seed(
        db,
        "INSERT INTO daily_metrics(date,source,resting_hr,hrv_ms) VALUES(?,?,?,?)",
        [(day(back), "apple", 60.0, 50.0) for back in range(1, 21)],
    )
    seed(
        db,
        "INSERT INTO daily_metrics(date,source,resting_hr,hrv_ms) VALUES(?,?,?,?)",
        [
            (day(back), "fitbit", 58.0 if back == 0 else 60.0,
             54.0 if back == 0 else 50.0)
            for back in range(0, 15)
        ],
    )
    seed(
        db,
        "INSERT INTO sleep_log(date,time_asleep_hours,quality,source) VALUES(?,?,?,?)",
        [(day(0), 7.2, 4, "synthetic-fixture")],
    )
    soreness_text = "fictional sore legs"
    seed(
        db,
        "INSERT INTO subjective_daily(date,soreness_note,source) VALUES(?,?,?)",
        [(day(0), soreness_text, "synthetic-fixture")],
    )

    first = readiness(db)
    assert first["status"] == "ok"
    evidence = first["evidence"]
    assert evidence["policy"]["meaning"] == "non_diagnostic_readiness_heuristic"
    assert evidence["policy"]["minimum_same_source_baseline_observations"] == 14
    assert evidence["policy"]["sleep"] == {
        "target_hours": 8.0,
        "method": "hours_target_70pct_plus_optional_quality_30pct",
        "quality_scale_max": 5,
    }
    assert evidence["policy"]["hrv"] == {
        "method": "same_source_median_deviation",
        "deviation_coefficient": 250,
        "higher_is_better": True,
    }
    assert evidence["policy"]["resting_hr"] == {
        "method": "same_source_median_deviation",
        "deviation_coefficient": 500,
        "lower_is_better": True,
    }
    assert evidence["policy"]["bands"] == {
        "bad_below": 40,
        "warn_below": 70,
        "good_at_or_above": 70,
    }
    assert evidence["policy"]["training"] == {
        "effective_sets_window_days": 7,
        "performance_window_days": 28,
        "e1rm_method": "epley-v1",
        "performance_comparison": (
            "latest_session_mean_vs_window_working_set_median"
        ),
        "below_median_rule": "signed_percentage_delta_lt_zero",
        "muscle_mapping": "authored_then_coarse-v2.8",
    }
    assert evidence["policy"]["soreness"]["derivation"] == (
        "case_insensitive_group_or_synonym_substring-v1"
    )
    assert re.fullmatch(
        r"sha256:[0-9a-f]{64}",
        evidence["policy"]["soreness"]["synonym_map_sha256"],
    )
    assert evidence["policy"]["soreness"]["meaning"] == "display_flag_only"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", evidence["input_fingerprint"])
    assert readiness(db)["evidence"]["input_fingerprint"] == evidence["input_fingerprint"]

    for key in ("hrv", "rhr"):
        component = next(
            item for item in evidence["components"] if item["key"] == key
        )
        assert component["status"] == "included"
        assert component["current"]["source_label"] == "fitbit"
        assert component["baseline"]["source_label"] == "fitbit"
        assert component["baseline"]["observation_count"] == 14
        assert all(
            locator.endswith(":fitbit")
            for locator in component["baseline"]["locators"]
        )

    soreness = evidence["soreness"]
    assert soreness["source_label"] == "synthetic-fixture"
    assert soreness["locator"] == f"subjective_daily:{day(0)}"
    assert "content_fingerprint" not in soreness
    assert soreness_text not in json.dumps(soreness)
    assert evidence["remaining_ancestry_gaps"] == []
    assert re.fullmatch(
        r"sha256:[0-9a-f]{64}", evidence["public_evidence_identity"],
    )

    con = sqlite3.connect(db)
    con.execute(
        "UPDATE daily_metrics SET hrv_ms=? WHERE date=? AND source='fitbit'",
        (51.0, day(1)),
    )
    con.commit()
    con.close()
    assert (
        readiness(db)["evidence"]["input_fingerprint"]
        != evidence["input_fingerprint"]
    )


def test_muscle_recovery_days_since_sets_and_e1rm_delta(db):
    """Bench maps 1:1 onto Chest via MUSCLE_TO_GROUP (same seed vocabulary
    test_scores.py uses). Two sessions in the trailing 28d: an older one
    (10d ago, 100kg x5 -> e1rm 116.7) and the most recent (2d ago, 110kg x5
    -> e1rm 128.3). last-session mean = 128.3; trailing-28d median across
    all 6 sets = (116.7+128.3)/2 = 122.5 -> delta = +4.7%, above median."""
    seed(db, "INSERT INTO exercise_muscles(exercise_title, muscle, weight) VALUES(?,?,?)",
         [("Bench", "chest", 1.0)])
    seed(db, "INSERT INTO hevy_sets(date, exercise_title, set_index, weight_kg, reps,"
             " set_type) VALUES(?,?,?,?,?,'normal')",
         [(day(10), "Bench", i, 100, 5) for i in range(1, 4)] +
         [(day(2), "Bench", i, 110, 5) for i in range(1, 4)])
    r = readiness(db)
    mr = {row["group"]: row for row in r["muscle_recovery"]}
    chest = mr["Chest"]
    assert chest["days_since"] == 2
    assert chest["sets_7d"] == 3.0                  # only the day(2) session is within 7d
    assert chest["exercise"] == "Bench"
    assert chest["e1rm_delta_pct"] == 4.7
    assert chest["below_median"] is False
    # untouched groups stay honestly "never logged"
    assert mr["Back"]["days_since"] is None
    assert mr["Back"]["note"] == "never logged"


def test_soreness_note_matches_groups_via_synonyms_and_own_name(db):
    """'sore quads and lower back' -> Legs (via the 'quad' synonym) AND Back
    (the group's own name is a literal substring of 'lower back'), the
    task-48 brief's worked example. A DISPLAY AID (documented substring
    match), not NLP."""
    seed(db, "INSERT INTO subjective_daily(date, soreness_note) VALUES(?,?)",
         [(day(0), "sore quads and lower back")])
    r = readiness(db)
    assert r["soreness"] == {"date": day(0), "note": "sore quads and lower back"}
    mr = {row["group"]: row for row in r["muscle_recovery"]}
    assert mr["Legs"]["sore"] is True
    assert mr["Back"]["sore"] is True
    assert mr["Chest"]["sore"] is False
    assert mr["Glutes"]["sore"] is False


def test_soreness_falls_back_to_yesterday_then_absent(db):
    seed(db, "INSERT INTO subjective_daily(date, soreness_note) VALUES(?,?)",
         [(day(1), "sore glutes")])
    r = readiness(db)
    assert r["soreness"] == {"date": day(1), "note": "sore glutes"}
    mr = {row["group"]: row for row in r["muscle_recovery"]}
    assert mr["Glutes"]["sore"] is True

    # neither today nor yesterday -> honest null, nothing flagged sore
    con = sqlite3.connect(db)
    con.execute("DELETE FROM subjective_daily")
    con.commit(); con.close()
    r2 = readiness(db)
    assert r2["soreness"] is None
    assert all(row["sore"] is False for row in r2["muscle_recovery"])


def test_log_soreness_note_is_loggable_and_readiness_picks_it_up(db):
    got = run(db, "log", "subjective_daily", "soreness_note=sore hamstrings")
    assert got["ok"] is True and got["inserted"]["soreness_note"] == "sore hamstrings"
    assert rows(db, "SELECT soreness_note FROM subjective_daily")[0]["soreness_note"] \
        == "sore hamstrings"
    r = readiness(db)
    assert r["soreness"]["note"] == "sore hamstrings"
    mr = {row["group"]: row for row in r["muscle_recovery"]}
    assert mr["Legs"]["sore"] is True


def test_log_soreness_note_upserts_same_day_with_other_fields(db):
    run(db, "log", "subjective_daily", "focus=3")
    run(db, "log", "subjective_daily", "soreness_note=sore back")
    got = rows(db, "SELECT focus, soreness_note FROM subjective_daily")
    assert len(got) == 1
    assert got[0]["focus"] == 3 and got[0]["soreness_note"] == "sore back"


def _legacy_subjective_db(tmp_path):
    """A pre-T48 DB: every OTHER base table is the real current schema (a
    legacy DB has daily_metrics/hevy_sets/etc already — T48 only adds one
    column), but subjective_daily is rebuilt in its pre-T48 shape, with no
    soreness_note column (mirrors test_health.py's _legacy_recipes_db
    pattern for T47's meal_type)."""
    p = _development_v6_database(tmp_path / "legacy.db")
    con = sqlite3.connect(p)
    con.execute("DROP TABLE subjective_daily")
    con.execute("CREATE TABLE subjective_daily (date TEXT PRIMARY KEY, focus INTEGER,"
                " energy INTEGER, mood INTEGER, emotional_regulation INTEGER,"
                " anxiety INTEGER, motivation INTEGER, stress INTEGER, caffeine_mg REAL,"
                " alcohol_units REAL, brain_dump TEXT, notes TEXT,"
                " source TEXT DEFAULT 'manual', ingested_at TEXT DEFAULT (datetime('now')),"
                " day_rating INTEGER)")
    con.commit(); con.close()
    return p


def _subjective_columns(p):
    con = sqlite3.connect(p)
    try:
        return [r[1] for r in con.execute("PRAGMA table_info(subjective_daily)")]
    finally:
        con.close()


def test_readiness_read_never_migrates_legacy_db(tmp_path):
    """A valid v6 ledger exposes the missing-column migration error first."""
    p = _legacy_subjective_db(tmp_path)
    result = run(p, "readiness", expect_ok=False)
    assert json.loads(result)["error"]["code"] == "schema_migration_required"
    assert "soreness_note" not in _subjective_columns(p)


def test_log_refuses_legacy_soreness_shape_without_ddl(tmp_path):
    """The generic writer cannot bypass Migration 001 ownership."""
    p = _legacy_subjective_db(tmp_path)
    result = run(p, "log", "subjective_daily", "soreness_note=sore quads", expect_ok=False)
    assert json.loads(result)["error"]["code"] == "schema_migration_required"
    assert "soreness_note" not in _subjective_columns(p)


def _legacy_daily_metrics_db(tmp_path):
    """A pre-2026-07-19 DB: every OTHER base table is the real current
    schema, but daily_metrics is rebuilt in its pre-rename shape — hrv_sdnn
    instead of hrv_ms (mirrors _legacy_subjective_db's pattern, this time for
    task-58's HRV rename)."""
    p = _development_v6_database(tmp_path / "legacy_hrv.db")
    con = sqlite3.connect(p)
    con.execute("DROP TABLE daily_metrics")
    con.execute("""CREATE TABLE "daily_metrics" (
        date TEXT, source TEXT NOT NULL DEFAULT 'apple',
        "resting_hr" REAL, "hrv_sdnn" REAL, "hr_min" REAL, "hr_avg" REAL,
        "hr_max" REAL, "steps" REAL, "active_energy_kcal" REAL,
        "basal_energy_kcal" REAL, "exercise_min" REAL, "distance_km" REAL,
        "flights" REAL, "respiratory_rate" REAL, "spo2_pct" REAL,
        "walking_hr_avg" REAL, "sleep_hours" REAL, PRIMARY KEY (date, source))""")
    con.commit(); con.close()
    return p


def _daily_metrics_columns(p):
    con = sqlite3.connect(p)
    try:
        return [r[1] for r in con.execute("PRAGMA table_info(daily_metrics)")]
    finally:
        con.close()


def test_readiness_refuses_legacy_hrv_column_no_ddl(tmp_path):
    """The HRV compatibility copy belongs only to explicit Migration 001."""
    p = _legacy_daily_metrics_db(tmp_path)
    hrv_baseline_rows = [(day(b), "apple", 50.0) for b in range(1, 21)]
    seed(p, "INSERT INTO daily_metrics(date, source, hrv_sdnn) VALUES(?,?,?)",
         hrv_baseline_rows)
    seed(p, "INSERT INTO daily_metrics(date, source, sleep_hours, hrv_sdnn) VALUES(?,?,?,?)",
         [(day(0), "apple", 7.2, 54.0)])
    cols_before = _daily_metrics_columns(p)
    result = run(p, "readiness", expect_ok=False)
    assert _daily_metrics_columns(p) == cols_before
    assert "hrv_ms" not in cols_before
    assert json.loads(result)["error"]["code"] == "schema_migration_required"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing", "schema_ledger_missing"),
        ("empty", "schema_ledger_invalid"),
        ("malformed", "schema_ledger_invalid"),
        ("non_contiguous", "schema_ledger_invalid"),
    ],
)
def test_readiness_fails_closed_on_untrusted_schema_ledger(
    tmp_path, mutation, expected_code,
):
    """Only an explicit contiguous ledger can select development-v6."""

    p = _development_v6_database(tmp_path / f"{mutation}.db")
    con = sqlite3.connect(p)
    if mutation == "missing":
        con.execute("DROP TABLE schema_migrations")
    elif mutation == "empty":
        con.execute("DELETE FROM schema_migrations")
    elif mutation == "malformed":
        con.execute("DROP TABLE schema_migrations")
        con.execute("CREATE TABLE schema_migrations(version TEXT)")
        con.execute("INSERT INTO schema_migrations VALUES('not-a-version')")
    else:
        con.execute("DELETE FROM schema_migrations WHERE version=5")
    con.commit()
    con.close()

    result = json.loads(run(p, "readiness", expect_ok=False))
    assert result["error"]["code"] == expected_code


def test_untrusted_ledger_precedes_legacy_column_diagnostics(tmp_path):
    """Schema identity is established before a legacy shape is diagnosed."""

    p = _legacy_subjective_db(tmp_path)
    con = sqlite3.connect(p)
    con.execute("DELETE FROM schema_migrations WHERE version=5")
    con.commit()
    con.close()

    result = json.loads(run(p, "readiness", expect_ok=False))
    assert result["error"]["code"] == "schema_ledger_invalid"
    assert "soreness_note" not in _subjective_columns(p)


def test_readiness_takes_zero_flags(db):
    r = run(db, "readiness", "--days", "7", expect_ok=False)
    assert "unrecognized arguments" in r or "error" in r.lower()


def test_readiness_explicit_anchor_is_reproducible_for_a_fictional_fixture(db):
    anchor = datetime(2026, 6, 30).date()
    anchor_text = anchor.isoformat()
    baseline = [
        ((anchor - timedelta(days=back)).isoformat(), "apple", 60.0, 50.0)
        for back in range(1, 21)
    ]
    seed(
        db,
        "INSERT INTO daily_metrics(date,source,resting_hr,hrv_ms) VALUES(?,?,?,?)",
        baseline,
    )
    seed(
        db,
        "INSERT INTO daily_metrics(date,source,resting_hr,hrv_ms,sleep_hours) "
        "VALUES(?,?,?,?,?)",
        [(anchor_text, "apple", 58.0, 54.0, 7.2)],
    )
    seed(db, "INSERT INTO exercise_muscles(exercise_title,muscle,weight) VALUES(?,?,?)",
         [("Bench", "chest", 1.0)])
    seed(
        db,
        "INSERT INTO hevy_sets(date,exercise_title,set_index,weight_kg,reps,set_type) "
        "VALUES(?,?,?,?,?,'normal')",
        [((anchor - timedelta(days=2)).isoformat(), "Bench", i, 100, 5)
         for i in range(1, 4)],
    )
    seed(db, "INSERT INTO subjective_daily(date,soreness_note) VALUES(?,?)",
         [(anchor_text, "mild chest soreness")])

    result = readiness(db, anchor_text)
    assert result["status"] == "ok"
    assert result["anchor_date"] == anchor_text
    assert {item["key"] for item in result["components"]} == {"sleep", "hrv", "rhr"}
    chest = next(row for row in result["muscle_recovery"] if row["group"] == "Chest")
    assert chest["days_since"] == 2
    assert chest["sets_7d"] == 3.0
    assert chest["sore"] is True
    assert result["soreness"]["date"] == anchor_text


def test_readiness_rejects_invalid_explicit_anchor(db):
    error = run(db, "readiness", "--anchor", "not-a-date", expect_ok=False)
    assert "--anchor must be ISO YYYY-MM-DD" in error


def test_readiness_explicit_range_does_not_read_older_training(db):
    anchor = datetime(2026, 6, 30).date()
    seed(db, "INSERT INTO exercise_muscles(exercise_title,muscle,weight) VALUES(?,?,?)",
         [("Bench", "chest", 1.0)])
    seed(
        db,
        "INSERT INTO hevy_sets(date,exercise_title,set_index,weight_kg,reps,set_type) "
        "VALUES(?,?,?,?,?,'normal')",
        [((anchor - timedelta(days=20)).isoformat(), "Bench", 1, 100, 5)],
    )
    result = run(
        db, "readiness", "--from", "2026-06-20", "--anchor", "2026-06-30",
    )
    assert result["range_from"] == "2026-06-20"
    chest = next(row for row in result["muscle_recovery"] if row["group"] == "Chest")
    assert chest["days_since"] is None
    assert chest["note"] == "never logged"
