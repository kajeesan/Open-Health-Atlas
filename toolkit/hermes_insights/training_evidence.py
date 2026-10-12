"""Database-backed, deterministic context for an external training/pain conversation."""

from collections import defaultdict
from datetime import date, timedelta
import json

from . import migrations, training_context
from .adapters import natural_key, effective_completeness, complete_dates, interval_dates, source_sync_intervals
from .adapters import pain, quarterly, training
from .contracts import DateRange
from .catalogs import PAIN_CAUSE_MAP
from .provenance import sha256_id


CONTRACT = "training-evidence-v1"
DEFINITION_VERSION = "exercise-context-v1"
MAX_INPUT_ROWS = 10_000
MAX_SOURCE_BYTES = 2 * 1024 * 1024
# These describe the exercise category, never the performed technique or joint angles.
EXERCISE_DEFINITIONS = {
    "Leg Extension": ("knee-extension", ["quadriceps"]),
    "Leg Extension (Machine)": ("knee-extension", ["quadriceps"]),
    "Seated Leg Curl": ("knee-flexion", ["hamstrings"]),
    "Lying Leg Curl": ("knee-flexion", ["hamstrings"]),
    "Leg Curl (Machine)": ("knee-flexion", ["hamstrings"]),
    "Seated Leg Curl (Machine)": ("knee-flexion", ["hamstrings"]),
    "Lying Leg Curl (Machine)": ("knee-flexion", ["hamstrings"]),
    "Leg Press (Machine)": ("squat", ["quadriceps", "glutes"]),
    "Squat (Barbell)": ("squat", ["quadriceps", "glutes"]),
    "Romanian Deadlift (Barbell)": ("hinge", ["hamstrings", "glutes"]),
    "Standing Calf Raise (Machine)": ("plantarflexion", ["calves"]),
}


def _check_read_budget(conn, start, end):
    # Refuse before materializing histories; partial correction chains could restore superseded answers.
    scopes = [
        ("training_context_revisions", "effective_from<=?", (end,)),
        ("fitness_tests", "voided=0 AND date<=?", (end,)),
        ("exercise_template_sources", "1=1", ()),
        ("pain_log", "voided=0", ()),
        ("self_test_log", "voided=0", ()),
        ("exercise_trial_log", "voided=0", ()),
    ]
    scopes.extend((table, "date>=? AND date<=?", (start, end))
                  for table in ("hevy_sets", "daily_metrics", "sleep_log", "subjective_daily"))
    for table, condition, params in scopes:
        count = conn.execute(
            f"SELECT count(*) FROM (SELECT 1 FROM {table} WHERE {condition} LIMIT ?)",
            (*params, MAX_INPUT_ROWS + 1),
        ).fetchone()[0]
        if count > MAX_INPUT_ROWS:
            raise training_context.TrainingError("evidence_too_large")
    byte_queries = (
        ("""SELECT sum(length(CAST(r.payload_json AS BLOB))) FROM exercise_template_sources t
            JOIN exercise_source_revisions r ON r.id=t.revision_id""", ()),
        ("""SELECT sum(length(CAST(r.payload_json AS BLOB))) FROM hevy_set_sources l
            JOIN exercise_source_revisions r ON r.id=l.revision_id
            JOIN hevy_sets s ON s.id=l.set_id WHERE s.date>=? AND s.date<=?""", (start, end)),
        ("""SELECT sum(length(CAST(r.payload_json AS BLOB))) FROM fitness_source_revisions f
            JOIN exercise_source_revisions r ON r.id=f.source_revision_id OR r.id=f.body_mass_revision_id
            JOIN fitness_tests t ON t.id=f.fitness_id WHERE t.voided=0 AND t.date<=?""", (end,)),
    )
    if sum(conn.execute(sql, params).fetchone()[0] or 0 for sql, params in byte_queries) > MAX_SOURCE_BYTES:
        raise training_context.TrainingError("evidence_too_large")


def _read(conn, table, start, end, fields="*", extra="", params=()):
    rows = [dict(row) for row in conn.execute(
        f"SELECT {fields} FROM {table} WHERE date>=? AND date<=? {extra} ORDER BY date",
        (start, end, *params))]
    return sorted(rows, key=lambda row: (row["date"], row.get("id", 0), row.get("source") or ""))


def _answer(context, key):
    item = context.get(key, {})
    return item.get("value") if item.get("status") == "answered" else None


def _question(kind, key, field, day, text, reason, parents):
    return {"id": sha256_id([kind, key, field, day]), "subject_kind": kind,
            "subject_key": str(key), "context_key": field, "effective_from": day,
            "question": text, "why_answer_changes_interpretation": reason,
            "parent_ids": parents}


def _training_record_order(entry):
    record, source = entry["record"], entry["source_payload"]
    return (
        record["date"], source.get("workout_start") or record.get("start_time") or "",
        entry["workout_id"] or "", source.get("exercise_index", -1),
        entry["template_id"] or "", record["set_index"] if record["set_index"] is not None else -1,
        entry["source_key"] or entry["id"], entry["source_fingerprint"] or "",
    )


def _training(conn, start, end, contexts, e1rm):
    templates = {}
    for row in conn.execute("""SELECT t.template_id,r.* FROM exercise_template_sources t
        JOIN exercise_source_revisions r ON r.id=t.revision_id"""):
        templates[row["template_id"]] = {**json.loads(row["payload_json"]),
                                          "revision_id": row["id"]}
    linked = {row["set_id"]: dict(row) for row in conn.execute("""SELECT l.set_id,r.*
        FROM hevy_set_sources l JOIN exercise_source_revisions r ON r.id=l.revision_id
        JOIN hevy_sets s ON s.id=l.set_id WHERE s.date>=? AND s.date<=?""", (start, end))}
    raw, sessions, questions = [], defaultdict(list), []
    for row in _read(conn, "hevy_sets", start, end):
        identity = linked.get(row["id"])
        imported = json.loads(identity["payload_json"]) if identity else {}
        template_id = imported.get("template_id")
        template = templates.get(template_id, {})
        definition = EXERCISE_DEFINITIONS.get(template.get("title") or row["exercise_title"])
        if template.get("is_custom") is not False:
            definition = None
        reported = training_context.resolve_context(contexts, "exercise", template_id, row["date"])
        parent = ("exercise_source_revisions:" + str(identity["id"]) if identity
                  else natural_key("hevy_sets", row))
        value = e1rm(row["weight_kg"], row["reps"]) if (
            row["weight_kg"] is not None and row["weight_kg"] > 0
            and row["reps"] is not None and 1 <= row["reps"] <= 12) else None
        entry = {"id": parent, "source_key": identity["source_key"] if identity else None,
                 "source_fingerprint": identity["fingerprint"] if identity else None,
                 "classification": "imported" if row["source"] == "hevy" else "user_reported",
                 "record": row, "template_id": template_id, "template": template,
                 "workout_id": imported.get("workout_id"), "source_payload": imported,
                 "exercise_definition": {
                     "version": DEFINITION_VERSION, "classification": "estimated",
                     "movement_pattern": definition[0] if definition else None,
                     "muscles": definition[1] if definition else [],
                     "equipment_category": template.get("equipment_category"),
                     "primary_muscle_group": template.get("primary_muscle_group"),
                     "secondary_muscle_groups": template.get("secondary_muscle_groups"),
                     "exact_angles": None, "range_of_motion": None, "technique": None,
                 }, "reported_context": reported,
                 "side": _answer(reported, "side"),
                 "e1rm": {"value": value, "unit": "kg_e1rm", "classification": "estimated",
                          "formula_version": "epley-v1", "parent_ids": [parent],
                          "limitation": "exercise_load_estimate_not_muscle_force"}}
        if identity:
            entry["record"] = {**row, "id": parent, "ingested_at": identity["ingested_at"]}
        raw.append(entry)
    raw.sort(key=_training_record_order)
    record_order = {entry["id"]: _training_record_order(entry) for entry in raw}
    for entry in raw:
        row, imported = entry["record"], entry["source_payload"]
        if row["set_type"] == "warmup":
            continue
        key = (entry["template_id"] or "unidentified:" + (row["exercise_title"] or entry["id"]),
               imported.get("workout_id") or row["start_time"] or row["date"])
        sessions[key].append(entry)

    def episode_order(episode):
        return (episode["date"], str(episode["session"]),
                min(record_order[parent] for parent in episode["parent_ids"]))

    episodes, histories = [], defaultdict(list)
    for (exercise, session), entries in sessions.items():
        entry = entries[-1]
        setup = _answer(entry["reported_context"], "setup")
        notes = sorted({item["record"]["exercise_notes"] or "" for item in entries})
        candidates = [item for item in entries if item["e1rm"]["value"] is not None]
        best = max(candidates, key=lambda item: item["e1rm"]["value"]) if candidates else None
        volumes = [item["record"]["weight_kg"] * item["record"]["reps"]
                   for item in entries if item["record"]["weight_kg"] is not None
                   and item["record"]["reps"] is not None]
        limited = _answer(entry["reported_context"], "pain_limited")
        episode = {"date": entry["record"]["date"], "template_id": entry["template_id"],
                   "exercise_title": entry["record"]["exercise_title"], "session": session,
                   "setup": setup, "side": entry["side"], "notes": notes,
                   "logged_working_sets": len(entries),
                   "logged_volume_kg_reps": sum(volumes) if volumes else None,
                   "volume_complete_for_logged_sets": len(volumes) == len(entries),
                   "best_e1rm": best["e1rm"]["value"] if best else None,
                   "best_e1rm_parent_ids": [best["id"]] if best else [],
                   "parent_ids": [item["id"] for item in entries],
                   "pain_limited": limited, "classification": "calculated",
                   "formula_version": "logged-exposure-v1", "personal_baseline": None,
                   "personal_baseline_limitations": []}
        histories[exercise].append(episode)
        episodes.append(episode)
    for exercise, series in histories.items():
        previous = {}
        preceding = None
        for episode in sorted(series, key=episode_order):
            episode["recorded_change"] = None
            if preceding and preceding["best_e1rm"] is not None and episode["best_e1rm"] is not None:
                episode["recorded_change"] = {
                    "prior_date": preceding["date"], "prior_value": preceding["best_e1rm"],
                    "change": episode["best_e1rm"] - preceding["best_e1rm"],
                    "unit": "kg_e1rm", "comparison_eligible": False,
                    "limitation": "recorded_load_change_may_reflect_setup_side_or_effort",
                    "parent_ids": preceding["best_e1rm_parent_ids"] + episode["best_e1rm_parent_ids"]}
            key = (episode["setup"], episode["side"], tuple(episode["notes"]))
            prior = previous.get(key)
            eligible = (episode["setup"] is not None and episode["side"] is not None
                        and episode["pain_limited"] == "no" and episode["best_e1rm"] is not None)
            if eligible and prior and (date.fromisoformat(episode["date"]) - date.fromisoformat(prior["date"])).days <= 120:
                old = prior["best_e1rm"]
                if old > 0:
                    episode["personal_baseline"] = {
                        "prior_date": prior["date"], "prior_value": old,
                        "change": episode["best_e1rm"] - old,
                        "percent_change": 100 * (episode["best_e1rm"] - old) / old,
                        "parent_ids": prior["best_e1rm_parent_ids"] + episode["best_e1rm_parent_ids"],
                        "formula_version": "same-setup-progress-v1",
                        "limitation": "reported_setup_not_verified_technique"}
                else:
                    episode["personal_baseline_limitations"].append("zero_baseline")
            if eligible:
                previous[key] = episode
            preceding = episode
        latest = max(series, key=episode_order)
        known = training_context.resolve_context(contexts, "exercise", exercise, latest["date"])
        if len(series) > 1 and latest["template_id"] and "setup" not in known:
            questions.append(_question("exercise", exercise, "setup", latest["date"],
                f"Was {latest['exercise_title']} on the same equipment as before?",
                "Equipment changes can explain a load change; record a short setup label and its dates.", latest["parent_ids"]))
        elif len(series) > 1 and latest["template_id"]:
            for field, wording, reason in (
                ("side", f"Was {latest['exercise_title']} performed with both limbs or one side?",
                 "A unilateral load cannot be compared with a bilateral load."),
                ("pain_limited", "Did pain limit the effort in these sets?",
                 "Pain-limited effort is not evidence of lost strength."),
            ):
                if field not in known:
                    questions.append(_question("exercise", exercise, field, latest["date"], wording, reason, latest["parent_ids"]))
    return raw, sorted(episodes, key=episode_order), questions


def build(conn, *, start, end, region, side, context, catalog, ratio_seeds, e1rm):
    """Build one bounded evidence snapshot; hypotheses and conversation remain external.

    The caller opens a read transaction. Missing dates are unknown unless the
    existing capture-completeness contract proves an explicitly empty day.
    """
    migrations.require_version(conn, 8)
    if not isinstance(start, str) or not isinstance(end, str):
        raise training_context.TrainingError("invalid_evidence_range")
    try:
        lo, hi = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as exc:
        raise training_context.TrainingError("invalid_evidence_range") from exc
    if lo.isoformat() != start or hi.isoformat() != end:
        raise training_context.TrainingError("invalid_evidence_range")
    if lo > hi or (hi - lo).days > 365 or hi > context.today:
        raise training_context.TrainingError("invalid_evidence_range")
    if not isinstance(region, str) or region not in {"knee", *PAIN_CAUSE_MAP}:
        raise training_context.TrainingError("unknown_pain_region")
    if side is not None and (not isinstance(side, str) or side not in {"left", "right", "central"}):
        raise training_context.TrainingError("invalid_evidence_side")
    _check_read_budget(conn, start, end)
    contexts = training_context.context_rows(conn, end)
    raw, exposure, questions = _training(conn, start, end, contexts, e1rm)
    pain_history = _read(conn, "pain_log", "0001-01-01", end, extra="AND voided=0")
    episode_anchors = {}
    for row in pain_history:
        key = (row["region"], row["side"])
        if row["intensity"] == 0:
            episode_anchors.pop(key, None)
        elif key not in episode_anchors:
            episode_anchors[key] = row
        row["episode_anchor"] = {"id": episode_anchors.get(key, row)["id"],
                                 "date": episode_anchors.get(key, row)["date"]}
    pain_rows = [row for row in pain_history if row["date"] >= start]
    pain_rows = [row for row in pain_rows if row["region"] == region or
                 (region == "knee" and row["region"].endswith("-knee"))]
    if side:
        pain_rows = [row for row in pain_rows if row["side"] in {side, "central"}]
    for row in pain_rows:
        row["evidence_id"] = natural_key("pain_log", row)
        row["reported_context"] = training_context.resolve_context(contexts, "pain", row["episode_anchor"]["id"], row["date"])
        row["reported_context"].update(training_context.resolve_context(contexts, "pain", row["id"], row["date"]))
    if pain_rows:
        latest = pain_rows[-1]
        symptom_questions = []
        if latest["intensity"] > 0:
            for key, wording, reason in (
                ("location", "Where around the knee do you feel it?" if region == "knee" else f"Where around the {region} do you feel it?",
                 "The location changes which explanations need to be considered."),
                ("provoking_movement", "Which movement brings it on?",
                 "A movement-specific symptom may conflict with a general training-load explanation."),
            ):
                if key not in latest["reported_context"] and not (key == "provoking_movement" and latest.get("pattern")):
                    symptom_questions.append(_question("pain", latest["episode_anchor"]["id"], key,
                        latest["date"], wording, reason, [latest["evidence_id"]]))
        questions = symptom_questions + questions
    measurements = quarterly.fitness_items(conn, catalog, e1rm, end)
    ratios = quarterly.tested_ratio_summary(conn, catalog, ratio_seeds, e1rm, hi)
    left_right = quarterly.left_right_summary(measurements, hi)
    referenced = {parent for ratio in ratios for item in ratio["sides"] for parent in item.get("parent_ids", [])}
    referenced.update(parent for ratio in ratios for parent in ratio.get("side_gap_parent_ids", []))
    referenced.update(parent for item in left_right for parent in item["parent_ids"])
    tests = [{"id": natural_key("fitness_tests", item["row"]), "record": item["row"],
              "value": item["value"], "unit": item["unit"], "protocol": item["protocol"],
              "classification": "estimated" if item["spec"]["kind"] == "strength" else "user_reported",
              "supporting_lookback": item["date"] < start,
              "reported_context": item["reported_context"], "source_parent_ids": []}
             for item in measurements if item["date"] >= start or natural_key("fitness_tests", item["row"]) in referenced]
    sources = {}
    for test in tests:
        parents = conn.execute("""SELECT r.* FROM exercise_source_revisions r
            JOIN fitness_source_revisions f ON (f.source_revision_id=r.id OR f.body_mass_revision_id=r.id)
            WHERE f.fitness_id=? ORDER BY r.id""",
            (test["record"]["id"],)).fetchall()
        for parent in parents:
            parent_id = "exercise_source_revisions:" + str(parent["id"])
            sources[parent_id] = {"id": parent_id, "source_key": parent["source_key"],
                                  "fingerprint": parent["fingerprint"],
                                  "payload": json.loads(parent["payload_json"])}
            test["source_parent_ids"].append(parent_id)
    recovery = {
        "daily": _read(conn, "daily_metrics", start, end,
                       "date,source,sleep_hours,resting_hr,hrv_ms,steps,exercise_min"),
        "sleep": _read(conn, "sleep_log", start, end,
                       "date,bedtime,wake_time,time_asleep_hours,quality,source,provenance,ingested_at"),
        "reported": _read(conn, "subjective_daily", start, end,
                          "date,source,ingested_at,energy,stress,soreness_note"),
        "timing": {"daily_date_is": "source_civil_date", "sleep_time_is": "reported_clock_time",
                   "same_day_ordering": "unknown_without_event_timestamps",
                   "ingestion_time_is_not_event_time": True},
        "limitation": "HRV methods differ by source; sleep sources may overlap; do not count them independently."}
    for table, name in (("daily_metrics", "daily"), ("sleep_log", "sleep"), ("subjective_daily", "reported")):
        for row in recovery[name]:
            row["id"] = natural_key(table, row, "date", "source") if table == "daily_metrics" else natural_key(table, row)
    date_range = DateRange(start=lo, end=hi, kind="bounded")
    completeness = complete_dates(effective_completeness(conn, date_range), "activity")
    synchronized = interval_dates(source_sync_intervals(conn, {"hevy"}), date_range)
    logged_dates = {item["record"]["date"] for item in raw}
    coverage = []
    for offset in range((hi - lo).days + 1):
        day = (lo + timedelta(days=offset)).isoformat()
        proof = completeness.get(day)
        zero = bool(proof is not None and proof["explicit_none"] and day not in logged_dates)
        state = ("logged" if day in logged_dates else "explicit_none" if zero else
                 "source_proven_zero" if day in synchronized else "unknown")
        coverage.append({"date": day, "state": state,
                         "parent_ids": [natural_key("capture_completeness_revisions", dict(proof))] if zero else
                         [f"source_sync_runs:{run}" for run in synchronized.get(day, [])]})
    daily_sets = {item["date"]: 0 for item in coverage if item["state"] != "unknown"}
    for episode in exposure:
        daily_sets[episode["date"]] += episode["logged_working_sets"]
    load_windows = [{"formula_version": "logged-set-window-v1", "unit": "sets",
                     **training.window_summary(daily_sets, hi, window),
                     "parent_ids": [item["id"] for item in raw
                                    if item["record"]["date"] >= (hi - timedelta(days=2 * window - 1)).isoformat()],
                     "coverage_parent_ids": sorted({parent for day in coverage
                         if day["date"] >= (hi - timedelta(days=2 * window - 1)).isoformat()
                         for parent in day["parent_ids"]}),
                     "limitation": "logged_training_not_all_physical_activity"}
                    for window in (7, 28, 90)]
    pain_context = pain.supplemental(conn, date_range, context, include_provenance=True)
    pre_observation = [item for item in pain_context["pre_observation_summaries"]
                       if (item["region"] == region or (region == "knee" and item["region"].endswith("-knee")))
                       and (side is None or item["side"] in {side, "central"})]
    self_tests = _read(conn, "self_test_log", start, end, extra="AND voided=0")
    trials = _read(conn, "exercise_trial_log", start, end, extra="AND voided=0")
    timeline = pain.pain_timeline(pain_rows, self_tests, trials, [
        {"date": item["date"], "kind": "training_session", "id": sha256_id(item["parent_ids"]),
         "parent_ids": item["parent_ids"], "logged_working_sets": item["logged_working_sets"]}
        for item in exposure])
    anchors = [{"id": row["evidence_id"], "observation_date": row["date"],
                "onset_date": row.get("reported_onset_date"), "onset_precision": row.get("onset_precision")}
               for row in pain_rows]
    contrasts = []
    for anchor in anchors:
        event = anchor["onset_date"] or anchor["observation_date"]
        event_day = date.fromisoformat(event)
        windows = []
        for low, high in ((event_day - timedelta(days=14), event_day - timedelta(days=8)),
                          (event_day - timedelta(days=7), event_day - timedelta(days=1))):
            rows = [item for item in exposure if low.isoformat() <= item["date"] <= high.isoformat()]
            observed = [item for item in coverage if low.isoformat() <= item["date"] <= high.isoformat()]
            known_dates = sum(item["state"] != "unknown" for item in observed)
            windows.append({"from": low.isoformat(), "to": high.isoformat(),
                            "logged_working_sets": sum(item["logged_working_sets"] for item in rows) if known_dates else None,
                            "known_dates": known_dates, "unknown_dates": 7 - known_dates,
                            "parent_ids": [parent for item in rows for parent in item["parent_ids"]]})
        counts = [window["logged_working_sets"] for window in windows]
        contrasts.append({"anchor": anchor, "windows": windows,
                          "logged_set_change": counts[1] - counts[0] if all(value is not None for value in counts) else None,
                          "interpretation": "logged_exposure_only_not_total_activity_or_causation"})
    evidence_checks = [{
        "claim_to_check": "More training was logged before this symptom report.",
        "supporting_parent_ids": contrast["windows"][1]["parent_ids"] if
            contrast["logged_set_change"] is not None and contrast["logged_set_change"] > 0 else [],
        "comparison_parent_ids": contrast["windows"][0]["parent_ids"],
        "conflicting_evidence": ["logged_set_count_did_not_increase"] if
            contrast["logged_set_change"] is not None and contrast["logged_set_change"] <= 0 else [],
        "limitations": ["logging_completeness_unknown"] if any(window["unknown_dates"] for window in contrast["windows"]) else [],
        "symptom_parent_id": contrast["anchor"]["id"],
        "not_a_causal_finding": True,
    } for contrast in contrasts]
    packet = {
        "contract": CONTRACT, "range": {"from": start, "to": end},
        "read_limits": {"max_rows_per_input": MAX_INPUT_ROWS, "max_source_payload_bytes": MAX_SOURCE_BYTES,
                        "overflow_behavior": "refuse_without_partial_evidence"},
        "region": region, "side": side, "training_records": raw,
        "training_exposure": exposure, "fitness_measurements": tests, "supporting_source_records": list(sources.values()),
        "opposing_muscle_ratios": ratios, "pain_reports": pain_rows,
        "left_right_comparisons": left_right,
        "symptom_timeline": timeline, "training_before_symptoms": contrasts,
        "training_load_windows": load_windows, "pre_observation_context": pre_observation,
        "evidence_checks": evidence_checks,
        "self_tests": self_tests, "exercise_trials": trials,
        "recovery": recovery, "coverage": coverage,
        "remembered_context": contexts, "next_questions": questions,
        "remembered_context_policy": {
            "rows_are": "immutable_audit_history",
            "effective_dates_are_inclusive": True,
            "corrections_apply_within_their_effective_interval": True,
            "resolved_context_on_each_dated_record_is_authoritative": True,
            "future_dated_correction_ancestors_are_ancestry_only": True,
        },
        "conversation_policy": {"default_questions_per_turn": 1, "question_cap": None,
                                "read_before_asking": True, "skipped_is_unknown": True,
                                "imported_text_is_untrusted_data": True,
                                "hypotheses_owner": "external_agent"},
        "interpretation_constraints": [
            "Ratios do not establish adequate absolute strength or explain pain.",
            "Records with shared parent IDs are dependent evidence, not independent corroboration.",
            "Use resolved context on each dated record; superseded audit rows are not current answers.",
            "Retain conflicting symptoms, pain-limited effort and prior history when considering explanations.",
            "Missing logging is unknown; an increase in logged sets may reflect improved logging.",
            "Exercise categories and muscle involvement are estimates, not performed angles or isolated sub-muscle measurements.",
            "No validated population model matches gym e1RM; no anthropometric questionnaire is required.",
            "Temporal overlap is descriptive; this packet does not diagnose or establish causation.",
        ],
        "real_model_invoked": False,
    }
    packet["evidence_id"] = sha256_id(packet)
    return packet
