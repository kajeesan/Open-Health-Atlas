"""Fictional import-to-conversation scenarios through the real command boundary."""

from datetime import date
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from hermes_insights import migrations


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "training_conversation.json"


def cli(db, *args, ok=True, quarterly=False):
    result = subprocess.run([sys.executable, str(ROOT / "health.py"), *args],
        env={**os.environ, "HEALTH_DB": str(db), "HERMES_HEVY_QUARTERLY_CONFIG":
             str(ROOT.parent / "config/hevy-quarterly.example.json") if quarterly else ""},
        capture_output=True, text=True, timeout=30)
    if not ok:
        return result
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "health.db"
    with sqlite3.connect(path) as conn:
        conn.executescript((ROOT / "SCHEMA.sql").read_text())
    migrations.migrate(str(path), 8, 0, "fictional-test-revision")
    cli(path, "import-hevy-json", str(FIXTURE))
    cli(path, "import-hevy-templates", str(FIXTURE))
    return path


def evidence(db):
    return cli(db, "training-evidence", "--from", "2026-06-01", "--to", "2026-06-30")


def answer(db, kind, subject, key, value=None, **options):
    args = ["training-context-log", "--subject-kind", kind, "--subject-key", subject, "--context-key", key, "--capture-id",
            options.get("capture", f"fictional-{kind}-{subject}-{key}"),
            "--effective-from", options.get("start", "2026-06-01")]
    if value is not None:
        args += ["--value", value]
    else:
        args += ["--status", "skipped"]
    if "end" in options:
        args += ["--effective-to", options["end"]]
    if "supersedes" in options:
        args += ["--supersedes-id", str(options["supersedes"])]
    return cli(db, *args)


def test_imported_evidence_is_useful_without_anatomy_questionnaire(db):
    packet = evidence(db)

    assert len(packet["training_records"]) == 3
    extension = packet["training_records"][0]
    assert extension["e1rm"]["value"] == 60
    assert extension["exercise_definition"]["movement_pattern"] == "knee-extension"
    assert extension["exercise_definition"]["exact_angles"] is None
    assert extension["side"] is None
    assert packet["next_questions"][0]["context_key"] == "setup"
    assert packet["conversation_policy"]["default_questions_per_turn"] == 1
    assert packet["real_model_invoked"] is False


def test_duplicate_imports_keep_source_parents_stable(db):
    before = evidence(db)
    cli(db, "import-hevy-json", str(FIXTURE))

    after = evidence(db)

    assert [row["id"] for row in before["training_records"]] == [row["id"] for row in after["training_records"]]
    assert len(after["training_records"]) == 3
    assert before["evidence_id"] == after["evidence_id"]


def test_pagination_repeats_do_not_multiply_exposure(db, tmp_path):
    payload = json.loads(FIXTURE.read_text())
    payload["workouts"].append(payload["workouts"][0])
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(json.dumps(payload))

    result = cli(db, "import-hevy-json", str(duplicate))

    assert result["imported_sets"] == 3


def test_conflicting_import_is_atomic(db, tmp_path):
    payload = json.loads(FIXTURE.read_text())
    changed = json.loads(json.dumps(payload["workouts"][0]))
    changed["exercises"][0]["sets"][0]["weight_kg"] = 99
    payload["workouts"].append(changed)
    duplicate = tmp_path / "conflict.json"
    duplicate.write_text(json.dumps(payload))

    result = cli(db, "import-hevy-json", str(duplicate), ok=False)

    assert result.returncode != 0
    assert evidence(db)["training_records"][0]["record"]["weight_kg"] == 50


def test_same_setup_progress_has_independent_expected_result(db):
    answer(db, "exercise", "fictional-extension", "setup", "gym-machine-A")
    answer(db, "exercise", "fictional-extension", "side", "bilateral")
    answer(db, "exercise", "fictional-extension", "pain_limited", "no")

    episode = evidence(db)["training_exposure"][-1]

    assert episode["best_e1rm"] == 66
    assert episode["personal_baseline"]["change"] == 6
    assert episode["personal_baseline"]["percent_change"] == 10
    assert len(episode["personal_baseline"]["parent_ids"]) == 2


def test_changed_machine_keeps_old_setup_history(db):
    answer(db, "exercise", "fictional-extension", "setup", "machine-A", end="2026-06-07")
    answer(db, "exercise", "fictional-extension", "setup", "machine-B", start="2026-06-08", capture="new-machine")
    answer(db, "exercise", "fictional-extension", "side", "bilateral")
    answer(db, "exercise", "fictional-extension", "pain_limited", "no")

    episodes = evidence(db)["training_exposure"]

    assert episodes[0]["setup"] == "machine-A"
    assert episodes[-1]["setup"] == "machine-B"
    assert episodes[-1]["personal_baseline"] is None


def test_pain_limited_effort_is_not_a_strength_decline(db):
    answer(db, "exercise", "fictional-extension", "setup", "machine-A")
    answer(db, "exercise", "fictional-extension", "side", "bilateral")
    answer(db, "exercise", "fictional-extension", "pain_limited", "yes")

    assert evidence(db)["training_exposure"][-1]["personal_baseline"] is None


def test_skipped_question_stays_unknown_without_repeating(db):
    answer(db, "exercise", "fictional-extension", "setup")

    packet = evidence(db)

    assert packet["training_exposure"][-1]["setup"] is None
    assert "setup" not in [q["context_key"] for q in packet["next_questions"]]


def test_correction_retains_history_and_changes_current_evidence(db):
    old = answer(db, "exercise", "fictional-extension", "setup", "machine-A")
    answer(db, "exercise", "fictional-extension", "setup", "machine-B", capture="correction", supersedes=old["id"])

    packet = evidence(db)

    assert packet["training_exposure"][-1]["setup"] == "machine-B"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM training_context_revisions").fetchone()[0] == 2


def test_repeated_answer_is_idempotent(db):
    first = answer(db, "exercise", "fictional-extension", "setup", "machine-A")

    again = answer(db, "exercise", "fictional-extension", "setup", "machine-A")

    assert again["id"] == first["id"]
    assert again["unchanged"] is True


def test_knee_pain_question_precedes_setup_question(db):
    cli(db, "pain-log", "anterior-knee", "--intensity", "4", "--side", "left", "--date", "2026-06-10",
        "--reported-onset-date", "2026-06-09", "--onset-precision", "approximate")

    packet = evidence(db)

    assert packet["next_questions"][0]["context_key"] == "location"
    assert packet["training_before_symptoms"][0]["anchor"]["onset_precision"] == "approximate"
    assert packet["training_before_symptoms"][0]["windows"][1]["to"] == "2026-06-08"


def test_symptom_answer_is_attached_to_report(db):
    cli(db, "pain-log", "anterior-knee", "--intensity", "4", "--side", "left", "--date", "2026-06-10")
    answer(db, "pain", "1", "location", "outside of left knee", start="2026-06-10")

    packet = evidence(db)

    assert packet["next_questions"][0]["context_key"] == "provoking_movement"
    assert packet["pain_reports"][0]["reported_context"]["location"]["value"] == "outside of left knee"


def test_missing_logging_is_not_zero(db):
    assert evidence(db)["coverage"][1]["state"] == "unknown"


def test_custom_exercise_is_not_anatomically_inferred_from_title(db, tmp_path):
    payload = json.loads(FIXTURE.read_text())
    payload["exercise_templates"][0]["is_custom"] = True
    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps(payload))
    cli(db, "import-hevy-templates", str(custom))

    definition = evidence(db)["training_records"][0]["exercise_definition"]

    assert definition["movement_pattern"] is None
    assert definition["muscles"] == []


@pytest.mark.parametrize("dates", [("2026-07-01", "2026-06-01"), ("2024-01-01", "2026-06-01")])
def test_unbounded_or_reversed_evidence_range_rejected(db, dates):
    result = cli(db, "training-evidence", "--from", dates[0], "--to", dates[1], ok=False)
    assert result.returncode != 0


def test_mcgill_side_bridge_uses_bilateral_extensor(db):
    cli(db, "fitness-test-log", "mcgill-side-bridge", "--side", "left", "--seconds", "65", "--date", "2026-06-15")
    cli(db, "fitness-test-log", "mcgill-side-bridge", "--side", "right", "--seconds", "60", "--date", "2026-06-15")
    cli(db, "fitness-test-log", "mcgill-extensor", "--seconds", "100", "--date", "2026-06-15")

    ratio = next(row for row in evidence(db)["opposing_muscle_ratios"] if row["key"] == "mcgill-sbe")

    assert [row["ratio"] for row in ratio["sides"]] == [0.65, 0.60]
    assert ratio["sides"][0]["parent_ids"][1] == ratio["sides"][1]["parent_ids"][1]


@pytest.mark.parametrize("den_date,expected", [("2026-06-02", "mixed_sessions"), ("2026-06-01", "setup_unknown")])
def test_ratios_expose_comparison_limitations(db, den_date, expected):
    cli(db, "fitness-test-log", "leg-curl", "--side", "left", "--load", "3", "--reps", "6", "--date", "2026-06-01")
    cli(db, "fitness-test-log", "leg-extension", "--side", "left", "--load", "5", "--reps", "6", "--date", den_date)

    ratio = next(row for row in evidence(db)["opposing_muscle_ratios"] if row["key"] == "hq")

    assert ratio["sides"][0]["ratio"] == 0.6
    assert ratio["sides"][0]["flag"] == "trend_only"
    assert expected in ratio["sides"][0]["limitations"]
    assert ratio["reference_eligible"] is False
    assert ratio["reference"]["typical"] == 0.6
    assert ratio["reference_use"] == "research_context_only"
    assert ratio["sides"][0]["reference_position"] == "within_research_range"
    assert ratio["sides"][1]["status"] == "insufficient_data"


def test_context_storage_is_append_only(db):
    answer(db, "history", "personal", "history_note", "Fictional old knee injury")
    with sqlite3.connect(db) as conn, pytest.raises(sqlite3.IntegrityError, match="append_only"):
        conn.execute("DELETE FROM training_context_revisions")


def test_correction_to_old_setup_does_not_replace_later_change(db):
    old = answer(db, "exercise", "fictional-extension", "setup", "machine-A")
    answer(db, "exercise", "fictional-extension", "setup", "machine-B", start="2026-06-08", capture="new-machine")
    answer(db, "exercise", "fictional-extension", "setup", "corrected-A", capture="correction", supersedes=old["id"])

    episodes = evidence(db)["training_exposure"]

    assert episodes[0]["setup"] == "corrected-A"
    assert episodes[-1]["setup"] == "machine-B"


def test_duplicate_set_identity_is_rejected(db, tmp_path):
    payload = json.loads(FIXTURE.read_text())
    sets = payload["workouts"][0]["exercises"][0]["sets"]
    sets.append(sets[0])
    duplicate = tmp_path / "duplicate-set.json"
    duplicate.write_text(json.dumps(payload))

    result = cli(db, "import-hevy-json", str(duplicate), ok=False)

    assert result.returncode != 0
    assert len(evidence(db)["training_records"]) == 3


def test_same_pain_episode_remembers_answer_on_later_report(db):
    cli(db, "pain-log", "anterior-knee", "--intensity", "4", "--side", "left", "--date", "2026-06-10")
    answer(db, "pain", "1", "location", "outside of knee", start="2026-06-10")
    cli(db, "pain-log", "anterior-knee", "--intensity", "5", "--side", "left", "--date", "2026-06-12")

    packet = evidence(db)

    assert packet["pain_reports"][-1]["reported_context"]["location"]["value"] == "outside of knee"
    assert packet["next_questions"][0]["context_key"] == "provoking_movement"


def test_migration_preserves_case_sensitive_source_identity(tmp_path):
    db = tmp_path / "health.db"
    with sqlite3.connect(db) as conn:
        conn.executescript((ROOT / "SCHEMA.sql").read_text())
    migrations.migrate(str(db), 7, 0, "fictional-test-revision")
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE exercise_source_revisions")
        conn.execute(migrations.TRAINING_CONTEXT_DDL["exercise_source_revisions"].replace(
            "source_key TEXT NOT NULL", "source_key TEXT COLLATE NOCASE NOT NULL"))

    with pytest.raises(migrations.SchemaError, match="SQL definition differs"):
        migrations.schema_plan(str(db), 8)


def test_quarterly_source_parent_survives_skipped_invalid_set(db, tmp_path):
    payload = json.loads(FIXTURE.read_text())
    workout = payload["workouts"][0]
    workout["routine_id"] = "example-routine-lower"
    exercise = workout["exercises"][0]
    exercise["exercise_template_id"] = "example-template-leg-extension"
    exercise["sets"] = [
        {"index": 0, "type": "normal", "weight_kg": 1001, "reps": 6},
        {"index": 1, "type": "normal", "weight_kg": 40, "reps": 6},
        {"index": 2, "type": "normal", "weight_kg": 30, "reps": 6}]
    source = tmp_path / "quarterly.json"
    source.write_text(json.dumps(payload))
    cli(db, "import-hevy-json", str(source), quarterly=True)

    packet = evidence(db)

    test = packet["fitness_measurements"][0]
    assert test["record"]["load_kg"] == 40
    parent = next(row for row in packet["supporting_source_records"] if row["id"] == test["source_parent_ids"][0])
    assert parent["payload"]["set"]["weight_kg"] == 40


def test_ratio_packet_includes_parents_before_requested_range(db):
    cli(db, "fitness-test-log", "leg-curl", "--side", "left", "--load", "30", "--reps", "6", "--date", "2026-05-20")
    cli(db, "fitness-test-log", "leg-extension", "--side", "left", "--load", "50", "--reps", "6", "--date", "2026-06-02")

    packet = evidence(db)

    ratio = next(row for row in packet["opposing_muscle_ratios"] if row["key"] == "hq")
    parents = {row["id"]: row for row in packet["fitness_measurements"]}
    assert set(ratio["sides"][0]["parent_ids"]) <= set(parents)
    assert parents[ratio["sides"][0]["parent_ids"][0]]["supporting_lookback"] is True


def test_side_gap_unknown_context_is_not_comparison_eligible(db):
    cli(db, "fitness-test-log", "mcgill-side-bridge", "--side", "left", "--seconds", "65", "--date", "2026-06-15")
    cli(db, "fitness-test-log", "mcgill-side-bridge", "--side", "right", "--seconds", "60", "--date", "2026-06-15")
    cli(db, "fitness-test-log", "mcgill-extensor", "--seconds", "100", "--date", "2026-06-15")

    ratio = next(row for row in evidence(db)["opposing_muscle_ratios"] if row["key"] == "mcgill-sbe")

    assert ratio["side_gap_comparison_eligible"] is False
    assert "setup_unknown" in ratio["side_gap_limitations"]


def test_stale_parent_cannot_keep_side_gap_current(db):
    cli(db, "fitness-test-log", "mcgill-side-bridge", "--side", "left", "--seconds", "65", "--date", "2026-02-15")
    cli(db, "fitness-test-log", "mcgill-side-bridge", "--side", "right", "--seconds", "60", "--date", "2026-05-15")
    cli(db, "fitness-test-log", "mcgill-extensor", "--seconds", "100", "--date", "2026-05-15")

    ratio = next(row for row in evidence(db)["opposing_muscle_ratios"] if row["key"] == "mcgill-sbe")

    assert "side_gap" not in ratio


def test_new_zero_denominator_invalidates_current_ratio(db):
    cli(db, "fitness-test-log", "leg-curl", "--side", "left", "--load", "30", "--reps", "6", "--date", "2026-06-01")
    cli(db, "fitness-test-log", "leg-extension", "--side", "left", "--load", "50", "--reps", "6", "--date", "2026-06-01")
    cli(db, "fitness-test-log", "leg-extension", "--side", "left", "--load", "0", "--reps", "6", "--date", "2026-06-15")

    ratio = next(row for row in evidence(db)["opposing_muscle_ratios"] if row["key"] == "hq")

    assert ratio["sides"][0]["status"] == "insufficient_data"
    assert ratio["sides"][0]["limitations"] == ["zero_denominator"]


def test_limb_comparison_does_not_require_opposing_muscle_test(db):
    cli(db, "fitness-test-log", "leg-extension", "--side", "left", "--load", "40", "--reps", "6", "--date", "2026-06-15")
    cli(db, "fitness-test-log", "leg-extension", "--side", "right", "--load", "50", "--reps", "6", "--date", "2026-06-15")

    comparison = evidence(db)["left_right_comparisons"][0]

    assert comparison["gap_fraction"] == pytest.approx(0.2)
    assert comparison["values"] == {"left": 48, "right": 60}


def test_evidence_retrieval_does_not_mutate_database(db):
    before = db.read_bytes()

    evidence(db)

    assert db.read_bytes() == before


def test_recovery_context_retains_distinct_source_identities(db):
    with sqlite3.connect(db) as conn:
        conn.executemany("INSERT INTO daily_metrics(date,source,hrv_ms) VALUES(?,?,?)",
                         [("2026-06-10", "apple", 40), ("2026-06-10", "fitbit", 30)])

    records = evidence(db)["recovery"]["daily"]

    assert len({row["id"] for row in records}) == 2
    assert [row["hrv_ms"] for row in records] == [40, 30]


def test_conflicting_same_day_symptoms_remain_visible(db):
    cli(db, "pain-log", "anterior-knee", "--side", "left", "--intensity", "5", "--date", "2026-06-10")
    cli(db, "pain-log", "anterior-knee", "--side", "left", "--intensity", "0", "--date", "2026-06-10")

    packet = evidence(db)

    assert [row["intensity"] for row in packet["pain_reports"]] == [5, 0]
    assert len({row["evidence_id"] for row in packet["pain_reports"]}) == 2


def test_explicit_empty_day_is_distinct_from_missing_logging(db):
    cli(db, "capture-completeness-set", "--date", "2026-06-02", "--scope", "activity",
        "--state", "complete", "--explicit-none", "--source", "manual")

    coverage = evidence(db)["coverage"]

    assert coverage[1]["state"] == "explicit_none"
    assert coverage[1]["parent_ids"]
    assert coverage[2]["state"] == "unknown"


def test_bodyweight_test_retains_body_mass_parent(db, tmp_path):
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO body_metrics(date,weight_kg,source) VALUES('2026-05-31',70,'manual')")
    payload = json.loads(FIXTURE.read_text())
    workout = payload["workouts"][0]
    workout["routine_id"] = "example-routine-lower"
    exercise = workout["exercises"][0]
    exercise["exercise_template_id"] = "example-template-calf-raise"
    exercise["sets"][0]["weight_kg"] = None
    source = tmp_path / "bodyweight.json"
    source.write_text(json.dumps(payload))
    cli(db, "import-hevy-json", str(source), quarterly=True)

    packet = evidence(db)

    test = packet["fitness_measurements"][0]
    assert test["record"]["load_kg"] == 70
    assert len(test["source_parent_ids"]) == 2
    parent = next(row for row in packet["supporting_source_records"] if row["source_key"].startswith("body_metrics:"))
    assert parent["payload"]["date"] == "2026-05-31"
    assert parent["payload"]["weight_kg"] == 70


def test_context_retry_conflict_does_not_overwrite_answer(db):
    answer(db, "exercise", "fictional-extension", "setup", "machine-A", capture="same-capture")

    result = cli(db, "training-context-log", "--subject-kind", "exercise", "--subject-key", "fictional-extension", "--context-key", "setup",
        "--capture-id", "same-capture", "--effective-from", "2026-06-01", "--value", "machine-B", ok=False)

    assert result.returncode != 0
    assert evidence(db)["training_exposure"][0]["setup"] == "machine-A"


def test_unnamed_legacy_set_remains_visible_without_inventing_identity(db):
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO hevy_sets(date,weight_kg,reps,source)
            VALUES('2026-06-15',20,6,'manual')""")

    packet = evidence(db)

    record = packet["training_records"][-1]
    assert record["template_id"] is None
    assert record["record"]["exercise_title"] is None
    assert record["e1rm"]["value"] == 24


def test_migration_rejects_conflicting_table_before_writing(tmp_path):
    db = tmp_path / "health.db"
    with sqlite3.connect(db) as conn:
        conn.executescript((ROOT / "SCHEMA.sql").read_text())
    migrations.migrate(str(db), 7, 0, "fictional-test-revision")
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE training_context_revisions")
        conn.execute("CREATE TABLE training_context_revisions(id INTEGER)")

    with pytest.raises(migrations.SchemaError, match="columns differ"):
        migrations.migrate(str(db), 8, 7, "fictional-test-revision")

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 7


def test_excessive_context_refuses_instead_of_truncating_correction_history(db):
    from hermes_insights.training_evidence import MAX_INPUT_ROWS
    with sqlite3.connect(db) as conn:
        conn.executemany("""INSERT INTO training_context_revisions
            (capture_id,subject_kind,subject_key,context_key,effective_from,status,value)
            VALUES(?,'history','personal','history_note','2026-06-01','answered','Fictional note')""",
            [(f"history-{index}",) for index in range(MAX_INPUT_ROWS + 1)])

    result = cli(db, "training-evidence", "--from", "2026-06-01", "--to", "2026-06-30", ok=False)

    assert result.returncode != 0
    assert json.loads(result.stdout)["error"]["code"] == "evidence_too_large"


def test_csv_reimport_retains_content_identity_without_inventing_template(db, tmp_path):
    source = tmp_path / "workout.csv"
    source.write_text("title,start_time,exercise_title,set_index,set_type,weight_kg,reps\n"
                      "Fictional workout,2026-06-01,Leg Extension (Machine),1,normal,50,6\n")
    cli(db, "import-hevy", str(source))
    before = evidence(db)

    cli(db, "import-hevy", str(source))
    after = evidence(db)

    assert before["evidence_id"] == after["evidence_id"]
    assert after["training_records"][0]["template_id"] is None
    assert after["training_records"][0]["source_payload"]["identity_quality"] == "content_only"


def test_recovery_timing_preserves_clock_times_without_inventing_event_order(db):
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO sleep_log
            (date,bedtime,wake_time,time_asleep_hours,source,ingested_at)
            VALUES('2026-06-10','23:40','07:10',7.5,'fictional-device','2026-06-11T09:00:00Z')""")

    recovery = evidence(db)["recovery"]

    assert recovery["sleep"][0]["bedtime"] == "23:40"
    assert recovery["sleep"][0]["wake_time"] == "07:10"
    assert recovery["sleep"][0]["ingested_at"] == "2026-06-11T09:00:00Z"
    assert recovery["timing"]["same_day_ordering"] == "unknown_without_event_timestamps"


def test_provider_template_fields_cannot_forge_canonical_revision_identity(db, tmp_path):
    payload = json.loads(FIXTURE.read_text())
    payload["exercise_templates"][0]["revision_id"] = "provider-controlled"
    source = tmp_path / "template.json"
    source.write_text(json.dumps(payload))
    cli(db, "import-hevy-templates", str(source))

    record = evidence(db)["training_records"][0]

    assert isinstance(record["template"]["revision_id"], int)


def test_noncanonical_context_subject_id_cannot_create_an_unresolvable_answer(db):
    cli(db, "pain-log", "anterior-knee", "--intensity", "4", "--side", "left", "--date", "2026-06-10")

    result = cli(db, "training-context-log", "--subject-kind", "pain", "--subject-key", "01",
                 "--context-key", "location", "--capture-id", "bad-subject", "--effective-from", "2026-06-10",
                 "--value", "Fictional location", ok=False)

    assert result.returncode != 0
    assert json.loads(result.stdout)["error"]["code"] == "invalid_context_identity"


def test_absent_pre_symptom_logs_do_not_become_zero_exposure_or_a_counterexample(db):
    cli(db, "pain-log", "anterior-knee", "--intensity", "4", "--side", "left", "--date", "2026-06-01")

    packet = evidence(db)

    contrast = packet["training_before_symptoms"][0]
    assert [window["logged_working_sets"] for window in contrast["windows"]] == [None, None]
    assert contrast["logged_set_change"] is None
    assert packet["evidence_checks"][0]["conflicting_evidence"] == []
    assert packet["evidence_checks"][0]["limitations"] == ["logging_completeness_unknown"]


def test_reordered_provider_exercise_blocks_preserve_evidence_replay(db, tmp_path):
    from hermes_insights.local_surface import LocalSurface
    surface = LocalSurface(db)
    request = {"operation": "health_training_evidence", "arguments": {
        "range": {"from": "2026-06-01", "to": "2026-06-30"}, "region": "knee",
    }}
    before = surface.execute(request)
    with sqlite3.connect(db) as conn:
        source_rows = conn.execute("SELECT * FROM exercise_source_revisions ORDER BY id").fetchall()
    payload = json.loads(FIXTURE.read_text())
    payload["workouts"][0]["exercises"].reverse()
    source = tmp_path / "reordered-exercises.json"
    source.write_text(json.dumps(payload))

    cli(db, "import-hevy-json", str(source))
    after = surface.execute(request)

    assert after["facts"] == before["facts"]
    assert after["result_id"] == before["result_id"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM exercise_source_revisions ORDER BY id").fetchall() == source_rows
    replay = surface.execute({"operation": "health_evidence", "arguments": {
        "evidence_refs": before["evidence_refs"], "detail": "lineage",
    }})
    assert replay["facts"][0]["facts"] == before["facts"]


def test_rejected_hevy_set_cannot_create_a_parentless_quarterly_observation(db, tmp_path):
    payload = json.loads(FIXTURE.read_text())
    workout = payload["workouts"][0]
    workout["routine_id"] = "example-routine-lower"
    exercise = workout["exercises"][0]
    exercise["exercise_template_id"] = "example-template-leg-extension"
    exercise["sets"][0]["rpe"] = 99
    source = tmp_path / "invalid-quarterly-rpe.json"
    source.write_text(json.dumps(payload))

    result = cli(db, "import-hevy-json", str(source), quarterly=True)

    assert result["skipped_sets"] == 1
    assert result["quarterly_tests_inserted"] == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM fitness_tests").fetchone()[0] == 0


def test_rounded_zero_baseline_remains_visible_without_dividing_by_zero(db, tmp_path):
    payload = json.loads(FIXTURE.read_text())
    payload["workouts"][0]["exercises"][0]["sets"][0]["weight_kg"] = 0.01
    source = tmp_path / "rounded-zero.json"
    source.write_text(json.dumps(payload))
    cli(db, "import-hevy-json", str(source))
    answer(db, "exercise", "fictional-extension", "setup", "fictional-machine")
    answer(db, "exercise", "fictional-extension", "side", "bilateral")
    answer(db, "exercise", "fictional-extension", "pain_limited", "no")

    packet = evidence(db)

    assert packet["training_records"][0]["e1rm"]["value"] == 0
    assert packet["training_exposure"][-1]["recorded_change"]["prior_value"] == 0
    assert packet["training_exposure"][-1]["recorded_change"]["change"] == 66
    assert packet["training_exposure"][-1]["personal_baseline"] is None
    assert "zero_baseline" in packet["training_exposure"][-1]["personal_baseline_limitations"]


def test_changed_provider_measurement_invalidates_training_evidence_replay(db, tmp_path):
    from hermes_insights.hermes_surface import SurfaceError
    from hermes_insights.local_surface import LocalSurface
    surface = LocalSurface(db)
    request = {"operation": "health_training_evidence", "arguments": {
        "range": {"from": "2026-06-01", "to": "2026-06-30"}, "region": "knee",
    }}
    before = surface.execute(request)
    payload = json.loads(FIXTURE.read_text())
    payload["workouts"][0]["exercises"][0]["sets"][0]["weight_kg"] = 51
    source = tmp_path / "changed-measurement.json"
    source.write_text(json.dumps(payload))

    cli(db, "import-hevy-json", str(source))
    after = surface.execute(request)

    assert after["facts"][0]["training_records"][0]["record"]["weight_kg"] == 51
    assert after["facts"][0]["evidence_id"] != before["facts"][0]["evidence_id"]
    with pytest.raises(SurfaceError) as failure:
        surface.execute({"operation": "health_evidence", "arguments": {
            "evidence_refs": before["evidence_refs"], "detail": "lineage",
        }})
    assert failure.value.code == "stale_evidence"


def test_changed_reported_context_invalidates_training_evidence_replay(db):
    from hermes_insights.hermes_surface import SurfaceError
    from hermes_insights.local_surface import LocalSurface
    surface = LocalSurface(db)
    request = {"operation": "health_training_evidence", "arguments": {
        "range": {"from": "2026-06-01", "to": "2026-06-30"}, "region": "knee",
    }}
    before = surface.execute(request)

    answer(db, "exercise", "fictional-extension", "setup", "fictional-machine")
    after = surface.execute(request)

    assert after["facts"][0]["evidence_id"] != before["facts"][0]["evidence_id"]
    with pytest.raises(SurfaceError) as failure:
        surface.execute({"operation": "health_evidence", "arguments": {
            "evidence_refs": before["evidence_refs"], "detail": "lineage",
        }})
    assert failure.value.code == "stale_evidence"
