"""Recovery policy, calculation context and deterministic readiness result."""
from datetime import date, timedelta
import statistics as st

from . import calculations, muscles, provenance, readiness_ancestry, runtime
from .catalogs import (
    MUSCLE_GROUP_AXES, MUSCLE_TO_GROUP, MOBILITY_EXERCISES, NON_VOLUME_EXERCISES,
)
from .capture_contracts import require_soreness_note
from .daily_frames import _rnd
from .score_contracts import SCORE_BAD_CUTOFF, SCORE_GOOD_CUTOFF, band as _band, clamp100 as _clamp100
from .scores import (
    _daily_metrics_has_hrv_ms, _dev_score, _no_data, _recovery_baseline_rows,
    _score, _sleep_score, SLEEP_TARGET_H, SCORES_DEFAULT_DAYS,
)

_table_exists = runtime.table_exists

def _today(clock):
    return runtime.today(clock=clock)

def _days_ago(n, clock):
    return runtime.days_ago(n, clock=clock)

def _group_weight_maps(c):
    return calculations._group_weight_maps(c, muscle_to_group=MUSCLE_TO_GROUP)

def _basis_weights(title, authored, coarse):
    return calculations._basis_weights(
        title, authored, coarse, mobility_exercises=MOBILITY_EXERCISES,
        non_volume_exercises=NON_VOLUME_EXERCISES,
    )

def e1rm(load, reps):
    return calculations.e1rm(load, reps)

READINESS_DISCLAIMER = ("Transparent heuristic over your own baselines — "
                        "not medical advice.")
READINESS_EVIDENCE_CONTRACT = "readiness-evidence-v2"
READINESS_MINIMUM_SAME_SOURCE_BASELINE = 14
READINESS_HRV_DEVIATION_COEFFICIENT = 250
READINESS_RHR_DEVIATION_COEFFICIENT = 500
READINESS_TRAINING_VOLUME_DAYS = 7
READINESS_TRAINING_PERFORMANCE_DAYS = 28
SORENESS_SYNONYMS = {
    "Legs":      ["quad", "hamstring", "calv", "thigh"],
    "Glutes":    ["glute", "booty"],
    "Back":      ["lat", "trap", "spine"],
    "Chest":     ["pec"],
    "Shoulders": ["delt"],
    "Arms":      ["bicep", "tricep", "forearm"],
    "Core":      ["abs", "abdominal", "oblique"],
}
READINESS_POLICY = {
    "policy_id": "openhealthatlas-readiness-policy",
    "version": "1.0.0",
    "meaning": "non_diagnostic_readiness_heuristic",
    "baseline_days": SCORES_DEFAULT_DAYS,
    "minimum_same_source_baseline_observations": (
        READINESS_MINIMUM_SAME_SOURCE_BASELINE
    ),
    "component_keys": ["sleep", "hrv", "rhr"],
    "composite": "equal_weight_mean_of_available_components",
    "minimum_components": 2,
    "sleep": {
        "target_hours": SLEEP_TARGET_H,
        "method": "hours_target_70pct_plus_optional_quality_30pct",
        "quality_scale_max": 5,
    },
    "hrv": {
        "method": "same_source_median_deviation",
        "deviation_coefficient": READINESS_HRV_DEVIATION_COEFFICIENT,
        "higher_is_better": True,
    },
    "resting_hr": {
        "method": "same_source_median_deviation",
        "deviation_coefficient": READINESS_RHR_DEVIATION_COEFFICIENT,
        "lower_is_better": True,
    },
    "bands": {
        "bad_below": SCORE_BAD_CUTOFF,
        "warn_below": SCORE_GOOD_CUTOFF,
        "good_at_or_above": SCORE_GOOD_CUTOFF,
    },
    "training": {
        "effective_sets_window_days": READINESS_TRAINING_VOLUME_DAYS,
        "performance_window_days": READINESS_TRAINING_PERFORMANCE_DAYS,
        "e1rm_method": "epley-v1",
        "performance_comparison": (
            "latest_session_mean_vs_window_working_set_median"
        ),
        "below_median_rule": "signed_percentage_delta_lt_zero",
        "muscle_mapping": "authored_then_coarse-v2.8",
    },
    "soreness": {
        "derivation": "case_insensitive_group_or_synonym_substring-v1",
        "synonym_map_sha256": provenance.sha256_id(SORENESS_SYNONYMS),
        "meaning": "display_flag_only",
    },
}
READINESS_POLICY_SHA256 = provenance.sha256_id(READINESS_POLICY)
def _readiness_calculation_context():
    """Private code-owned inputs that can change Recovery interpretation.

    This complete context is bound inside the private snapshot sidecar and the
    deterministic input fingerprint.  Its mapping details are not copied into
    the public evidence projection.
    """

    static_mapping_policy = {
        "muscle_group_axes": list(MUSCLE_GROUP_AXES),
        "muscle_to_group": MUSCLE_TO_GROUP,
        "mobility_exercises": sorted(MOBILITY_EXERCISES),
        "non_volume_exercises": sorted(NON_VOLUME_EXERCISES),
    }
    return {
        "policy": READINESS_POLICY,
        "policy_sha256": READINESS_POLICY_SHA256,
        "static_mapping_policy": static_mapping_policy,
        "soreness_synonyms": SORENESS_SYNONYMS,
    }
def _muscle_recovery(c, anchor=None, start=None, *, clock):
    """Per-7-group (MUSCLE_GROUP_AXES) recovery snapshot for the readiness
    drill — days since a mapped exercise was last trained (ALL-TIME,
    non-warmup hevy_sets), effective sets in the trailing 7d (the SAME
    _rollup7 rollup the radar/muscle_balance score share — never a rival
    count), and for the group's most-frequently-SET exercise over the
    trailing 28d: last-session mean e1RM vs the trailing-28d median e1RM as a
    signed %% delta. `below_median` is SIGN ONLY (delta < 0) — no invented
    magnitude threshold; e1rm() is the one shared formula, never re-derived.
    Groups with no mapped exercise EVER trained get an honest
    days_since=None + note='never logged' row rather than being omitted."""
    authored, coarse = _group_weight_maps(c)

    def _groups_for(title):
        gmap, basis = _basis_weights(title, authored, coarse)
        return set(gmap) if gmap else set()

    if anchor is None:
        last_query = ("""SELECT exercise_title, MAX(date) last FROM hevy_sets
                          WHERE COALESCE(set_type,'normal')!='warmup'
                          GROUP BY exercise_title""", ())
        window_query = ("""SELECT exercise_title, date, weight_kg, reps FROM hevy_sets
            WHERE date >= ? AND COALESCE(set_type,'normal')!='warmup'
            ORDER BY date""", (_days_ago(READINESS_TRAINING_PERFORMANCE_DAYS, clock),))
        reference_day = date.fromisoformat(_today(clock))
    else:
        last_query = ("""SELECT exercise_title, MAX(date) last FROM hevy_sets
                          WHERE date>=? AND date<=?
                            AND COALESCE(set_type,'normal')!='warmup'
                          GROUP BY exercise_title""",
                      ((start or date.min).isoformat(), anchor.isoformat()))
        window_start = anchor - timedelta(days=READINESS_TRAINING_PERFORMANCE_DAYS)
        if start is not None:
            window_start = max(window_start, start)
        window_query = ("""SELECT exercise_title, date, weight_kg, reps FROM hevy_sets
            WHERE date >= ? AND date <= ?
              AND COALESCE(set_type,'normal')!='warmup'
            ORDER BY date""",
            (window_start.isoformat(), anchor.isoformat()))
        reference_day = anchor
    last_by_ex = {r["exercise_title"]: r["last"] for r in c.execute(*last_query)}
    sets7 = muscles.rollup(
        c, "logged", READINESS_TRAINING_VOLUME_DAYS, anchor=anchor, clock=clock,
    )["groups"]

    win_rows = c.execute(*window_query).fetchall()
    by_group_ex = {g: {} for g in MUSCLE_GROUP_AXES}
    for r in win_rows:
        for g in _groups_for(r["exercise_title"]):
            by_group_ex[g].setdefault(r["exercise_title"], []).append(r)

    rows_out = []
    for g in MUSCLE_GROUP_AXES:
        last_dates = [d for t, d in last_by_ex.items() if d and g in _groups_for(t)]
        days_since = ((reference_day - date.fromisoformat(max(last_dates))).days
                      if last_dates else None)
        entry = {"group": g, "days_since": days_since, "sets_7d": sets7.get(g, 0.0),
                 "exercise": None, "e1rm_delta_pct": None, "below_median": None,
                 "sore": False}
        if days_since is None:
            entry["note"] = "never logged"
            rows_out.append(entry)
            continue
        exs = by_group_ex.get(g, {})
        if exs:
            # most-frequently-SET exercise (by working-set count in the 28d
            # window), deterministic alpha tiebreak
            top_ex, ex_rows = sorted(exs.items(), key=lambda kv: (-len(kv[1]), kv[0]))[0]
            entry["exercise"] = top_ex
            last_date = max(r["date"] for r in ex_rows)
            last_vals = [v for v in (e1rm(r["weight_kg"], r["reps"]) for r in ex_rows
                                     if r["date"] == last_date) if v is not None]
            all_vals = [v for v in (e1rm(r["weight_kg"], r["reps"]) for r in ex_rows) if v is not None]
            if last_vals and all_vals:
                median28 = st.median(all_vals)
                if median28 > 0:
                    delta = round(100 * (st.mean(last_vals) - median28) / median28, 1)
                    entry["e1rm_delta_pct"] = delta
                    entry["below_median"] = delta < 0
        rows_out.append(entry)
    return rows_out
def _readiness_metric_evidence(base_rows, today_row, key):
    """Return one same-source baseline plus bounded, non-value ancestry.

    ``daily_metrics`` permits one row per date and source.  The existing merge
    rule still chooses Fitbit first for each metric on each date, but a
    baseline may use only rows selected from the current observation's source.
    This is essential for HRV, whose stored algorithm differs by source, and
    keeps the same integrity rule for resting HR.
    """

    field = "hrv_ms" if key == "hrv" else "resting_hr"
    source_field = f"{field}_source"
    source_key_field = f"{field}_source_key"
    transformation = (
        "same-source-baseline-deviation-hrv-v1"
        if key == "hrv"
        else "same-source-baseline-deviation-rhr-v1"
    )
    current_value = today_row.get(field) if today_row else None
    current_source = today_row.get(source_field) if today_row else None
    current_source_key = today_row.get(source_key_field) if today_row else None
    if current_value is None or current_source is None:
        ancestry = {
            "key": key,
            "status": "missing",
            "reason_code": "no_current_observation",
            "ancestry_state": "missing",
            "current": None,
            "baseline": None,
            "transformation": transformation,
        }
        return ancestry, [], {"key": key, "status": "missing"}, None

    current = {
        "table": "daily_metrics",
        "locator": f"daily_metrics:{today_row['date']}:{current_source}",
        "source_label": current_source,
        "observed_at": today_row["date"],
    }
    available_field = f"{field}_sources"
    baseline_rows = [
        row for row in base_rows if row["date"] != today_row["date"]
    ]
    same_source = [
        (row, row[available_field][current_source_key])
        for row in baseline_rows
        if current_source_key in row[available_field]
    ]
    baseline = {
        "source_label": current_source,
        "observation_count": len(same_source),
        "range_from": same_source[0][0]["date"] if same_source else None,
        "range_to": same_source[-1][0]["date"] if same_source else None,
        "locators": [
            f"daily_metrics:{row['date']}:{selected['source_label']}"
            for row, selected in same_source
        ],
    }
    required = READINESS_MINIMUM_SAME_SOURCE_BASELINE
    baseline_values = [selected["value"] for _row, selected in same_source]
    enough = len(same_source) >= required
    positive = enough and st.median(baseline_values) > 0
    reason_code = (
        None
        if positive
        else (
            "insufficient_same_source_baseline"
            if not enough
            else "nonpositive_same_source_baseline"
        )
    )
    ancestry = {
        "key": key,
        "status": "included" if positive else "excluded",
        "reason_code": reason_code,
        "ancestry_state": "source_rows_identified",
        "current": current,
        "baseline": baseline,
        "transformation": transformation,
    }
    fingerprint_input = {
        "key": key,
        "status": ancestry["status"],
        "reason_code": ancestry["reason_code"],
        "current": {**current, "value": current_value},
        "baseline": [
            {
                "locator": f"daily_metrics:{row['date']}:{selected['source_label']}",
                "source_label": selected["source_label"],
                "observed_at": row["date"],
                "value": selected["value"],
            }
            for row, selected in same_source
        ],
        "transformation": transformation,
    }
    warning = None
    if not positive:
        warning = {
            "code": reason_code,
            "component": key,
            "source_label": current_source,
            "required": required,
            "observed": len(same_source),
            "other_source_observations_excluded": (
                sum(
                    1
                    for row in baseline_rows
                    for source_key in row[available_field]
                    if source_key != current_source_key
                )
            ),
        }
    return ancestry, baseline_values, fingerprint_input, warning
def _readiness_result(c, *, anchor, range_start, snapshot_attestation, clock):
    """T48 readiness engine: a transparent equal-weight-mean composite of the
    SAME sleep/hrv/rhr math scores() uses — sleep via _sleep_score, hrv/rhr
    via _recovery_baseline_rows + _dev_score (factored, never forked; same
    candidate window via SCORES_DEFAULT_DAYS and same constants). Readiness
    additionally requires every HRV/RHR baseline to match the selected current
    source. `drag[i].points` =
    (100-component)/n, the EXACT arithmetic shortfall each included
    component contributes under that equal-weight mean — deterministic, no
    invented per-component weights. Fewer than 2 available components ->
    insufficient_data (muscle_recovery + soreness are independent of the
    composite and are still returned)."""
    components = []

    evidence_components = []
    evidence_warnings = []
    fingerprint_components = []

    sleep_c = _sleep_score(
        c, anchor=anchor,
        include_ancestry=True, clock=clock,
    )
    if not sleep_c.get("insufficient_data"):
        i = sleep_c["inputs"]
        q = f" · quality {i['quality']}/5" if i.get("quality") is not None else ""
        components.append({"key": "sleep", "score": sleep_c["score"], "value": i["hours"],
            "basis": f"{i['hours']:.1f}h{q} vs {SLEEP_TARGET_H:.0f}h target"})
        sleep_ancestry = i["ancestry"]
        evidence_components.append({
            "key": "sleep",
            "status": "included",
            "reason_code": None,
            "ancestry_state": (
                "source_row_identified"
                if sleep_ancestry.get("source_label")
                else "row_identified_source_missing"
            ),
            "current": sleep_ancestry,
            "baseline": None,
            "transformation": "sleep-score-v1",
        })
        fingerprint_components.append({
            "key": "sleep",
            "status": "included",
            "current": {
                **sleep_ancestry,
                "hours": i["hours"],
                "quality": i.get("quality"),
            },
            "target_hours": SLEEP_TARGET_H,
            "transformation": "sleep-score-v1",
        })
    else:
        evidence_components.append({
            "key": "sleep",
            "status": "missing",
            "reason_code": "no_current_observation",
            "ancestry_state": "missing",
            "current": None,
            "baseline": None,
            "transformation": "sleep-score-v1",
        })
        fingerprint_components.append({"key": "sleep", "status": "missing"})

    bounded_anchor = anchor
    base_rows = _recovery_baseline_rows(
        c, SCORES_DEFAULT_DAYS, anchor=bounded_anchor, start=range_start, clock=clock,
    )
    current_dates = [(anchor - timedelta(days=b)).isoformat() for b in (0, 1)]
    today_row = next((r for d in current_dates for r in base_rows if r["date"] == d), None)
    for key, field, coef, invert, unit in (
        (
            "hrv", "hrv_ms", READINESS_HRV_DEVIATION_COEFFICIENT,
            False, "ms",
        ),
        (
            "rhr", "resting_hr", READINESS_RHR_DEVIATION_COEFFICIENT,
            True, "bpm",
        ),
    ):
        ancestry, baseline_values, fingerprint_input, warning = (
            _readiness_metric_evidence(base_rows, today_row, key)
        )
        evidence_components.append(ancestry)
        fingerprint_components.append(fingerprint_input)
        if warning is not None:
            evidence_warnings.append(warning)
        if ancestry["status"] != "included":
            continue
        dev = _dev_score(today_row[field], baseline_values, coef, invert)
        if dev:
            sc, b = dev
            components.append({
                "key": key,
                "score": _clamp100(sc),
                "value": today_row[field],
                "basis": (
                    f"{today_row[field]:.0f} {unit} vs {b:.0f} {unit} "
                    "same-source baseline (14+ day median)"
                ),
            })

    muscle_recovery = _muscle_recovery(
        c, anchor=bounded_anchor, start=range_start, clock=clock,
    )

    # soreness: today's, else yesterday's, subjective_daily.soreness_note
    # VERBATIM inside the private deterministic result. The field is owned by
    # Migration 001: this read requires it and never performs compatibility
    # DDL. With a trusted ledger, a legacy column shape therefore reports the
    # explicit schema_migration_required error.
    soreness = None
    soreness_source = None
    if _table_exists(c, "subjective_daily") and require_soreness_note(c):
        for back in (0, 1):
            d = (anchor - timedelta(days=back)).isoformat()
            row = c.execute(
                "SELECT soreness_note, source FROM subjective_daily WHERE date=?",
                (d,),
            ).fetchone()
            if row and row["soreness_note"]:
                soreness = {"date": d, "note": row["soreness_note"]}
                soreness_source = row["source"]
                break
    if soreness:
        note_l = soreness["note"].lower()
        for entry in muscle_recovery:
            terms = [entry["group"].lower()] + SORENESS_SYNONYMS.get(entry["group"], [])
            entry["sore"] = any(t in note_l for t in terms)

    if soreness:
        soreness_evidence = {
            "status": "present",
            "table": "subjective_daily",
            "locator": f"subjective_daily:{soreness['date']}",
            "source_label": soreness_source,
            "observed_at": soreness["date"],
        }
        soreness_fingerprint_input = {
            **soreness_evidence,
            "sore_groups": sorted(
                entry["group"] for entry in muscle_recovery if entry["sore"]
            ),
        }
    else:
        soreness_evidence = {
            "status": "missing",
            "table": "subjective_daily",
            "locator": None,
            "source_label": None,
            "observed_at": None,
        }
        soreness_fingerprint_input = {"status": "missing"}

    fingerprint_payload = {
        "contract": READINESS_EVIDENCE_CONTRACT,
        "policy_sha256": READINESS_POLICY_SHA256,
        "range": {
            "from": range_start.isoformat() if range_start else None,
            "anchor": anchor.isoformat(),
        },
        "components": fingerprint_components,
        "muscle_recovery": muscle_recovery,
        "soreness": soreness_fingerprint_input,
    }
    # The private sidecar separately binds exact row bytes, including the raw
    # note.  Neither that digest nor the raw note enters this public identity:
    # equivalent wording with the same bounded sore-group flags has the same
    # calculation identity.
    evidence = {
        "contract": READINESS_EVIDENCE_CONTRACT,
        "input_fingerprint": provenance.sha256_id(fingerprint_payload),
        "fingerprint_scope": [
            "calculation_relevant_projection", "calculation_policy", "range",
        ],
        "policy": READINESS_POLICY,
        "policy_sha256": READINESS_POLICY_SHA256,
        "snapshot_integrity": dict(snapshot_attestation.public_summary),
        "components": evidence_components,
        "soreness": soreness_evidence,
        "warnings": evidence_warnings,
        "remaining_ancestry_gaps": [],
    }
    evidence["public_evidence_identity"] = provenance.sha256_id(evidence)

    if len(components) < 2:
        return {"status": "insufficient_data", "anchor_date": anchor.isoformat(),
                "range_from": range_start.isoformat() if range_start else None,
                "reason": f"needs >= 2 of sleep/HRV/resting HR (have {len(components)})",
                "components": components, "muscle_recovery": muscle_recovery,
                "soreness": soreness, "evidence": evidence,
                "disclaimer": READINESS_DISCLAIMER}

    n = len(components)
    score = _clamp100(st.mean(comp["score"] for comp in components))
    drag = sorted(({"key": comp["key"],
                    "points": round((100 - comp["score"]) / n, 1),
                    "label": f"{comp['key']} — {round((100 - comp['score']) / n, 1)} pts"}
                   for comp in components), key=lambda d: -d["points"])
    return {"status": "ok", "anchor_date": anchor.isoformat(),
            "range_from": range_start.isoformat() if range_start else None,
            "score": score, "band": _band(score),
            "components": components, "drag": drag,
            "muscle_recovery": muscle_recovery, "soreness": soreness,
            "evidence": evidence, "disclaimer": READINESS_DISCLAIMER}
