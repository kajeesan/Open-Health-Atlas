"""Adversarial Phase 4 boundaries not covered by the happy-path fixtures."""

from __future__ import annotations

from datetime import date, timedelta
import pathlib
import sqlite3
import sys
from types import SimpleNamespace

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import health
import hermes_insights.associations as associations_module
from hermes_insights.associations import (
    _base_gate,
    _candidate_lineage_status,
    _component_lineage_reason,
    _component_value,
    _known_unexposed,
    _lineage_reason,
    _outcome_rows,
    _relation_allowed,
    _risk_effect,
    _strict_boundary,
    analyze_outcome,
)
from hermes_insights.contracts import DateRange
from hermes_insights.frame import build_feature_frame
from hermes_insights.registry import build_registry


def _registry_fixture():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "SCHEMA.sql").read_text())
    context = health._phase3_context()
    definitions = build_registry(conn, context)
    return conn, context, definitions


def _candidate(rows, *, eligible_n=None):
    eligible = len(rows) if eligible_n is None else eligible_n
    return {
        "rows": rows,
        "eligible_n": eligible,
        "complete_n": len(rows),
        "missing_n": eligible - len(rows),
        "missing_rate": (eligible - len(rows)) / eligible,
    }


def _binary_row(index, exposure, outcome, states):
    return {
        "unit_key": f"{index:03d}",
        "date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
        "exposure": float(exposure),
        "outcome": float(outcome),
        "outcome_raw": float(outcome),
        "exposure_states": list(states),
    }


def test_incomplete_manual_nutrition_total_is_not_an_analytical_value():
    conn, context, definitions = _registry_fixture()
    nutrition = next(
        item for item in definitions if item.key == "nutrition.logged.kcal"
    )
    day = date(2026, 1, 10)
    conn.execute(
        "INSERT INTO nutrition_log(date,kcal,source) VALUES(?,?,?)",
        (day.isoformat(), 640.0, "manual"),
    )
    conn.commit()

    frame = build_feature_frame(
        conn,
        [nutrition],
        DateRange(day, day),
        context,
        include_provenance=True,
    )
    row = next(
        item for item in frame["observations"]
        if item["feature_key"] == nutrition.key
    )
    assert row["provenance"]["candidate_eligible"] is False
    value, _references, _states = _component_value(
        nutrition,
        {
            "exposure_key": nutrition.key,
            "lag_days": 0,
            "window_days": 1,
            "transform": "point",
        },
        day.isoformat(),
        {day.isoformat(): [row]},
    )
    assert value is None

    conn.execute(
        """INSERT INTO capture_completeness_revisions(
             date,scope,state,source)
           VALUES(?,?,?,?)""",
        (day.isoformat(), "nutrition_total", "complete", "manual"),
    )
    conn.commit()
    complete_frame = build_feature_frame(
        conn,
        [nutrition],
        DateRange(day, day),
        context,
        include_provenance=True,
    )
    complete_row = next(
        item for item in complete_frame["observations"]
        if item["feature_key"] == nutrition.key
    )
    assert complete_row["provenance"]["candidate_eligible"] is True
    value, _references, _states = _component_value(
        nutrition,
        {
            "exposure_key": nutrition.key,
            "lag_days": 0,
            "window_days": 1,
            "transform": "point",
        },
        day.isoformat(),
        {day.isoformat(): [complete_row]},
    )
    assert value == 640.0
    conn.close()


def test_binary_unexposed_units_require_explicit_or_complete_zero_state():
    known = [
        _binary_row(index, index < 15, index % 3 == 0, ["observed"])
        for index in range(15)
    ] + [
        _binary_row(index, False, index % 3 == 0, ["structural_zero"])
        for index in range(15, 30)
    ]
    assert _base_gate(
        _candidate(known), "green-vs-non-green", 30
    ) is None

    unknown = [
        _binary_row(index, index < 15, index % 3 == 0, ["observed"])
        for index in range(15)
    ] + [
        _binary_row(index, False, index % 3 == 0, ["missing"])
        for index in range(15, 30)
    ]
    assert _base_gate(
        _candidate(unknown), "green-vs-non-green", 30
    ) == "unknown_absence"


def test_structural_zero_semantics_accepts_mixed_known_zero_interval_only():
    definition = SimpleNamespace(
        zero_semantics="structural_zero_if_complete",
    )
    known = {
        "exposure_states": ["observed", "structural_zero", "structural_zero"],
        "exposure_observations": [
            {"state": "observed"},
            {"state": "structural_zero"},
            {"state": "structural_zero"},
        ],
    }
    assert _known_unexposed(known, definition)
    assert not _known_unexposed(
        {
            **known,
            "exposure_states": ["observed", "missing", "structural_zero"],
        },
        definition,
    )
    assert not _known_unexposed(
        {
            **known,
            "exposure_states": [
                "observed", "candidate_ineligible", "structural_zero",
            ],
        },
        definition,
    )
    assert not _known_unexposed(
        {**known, "exposure_observations": []},
        definition,
    )


def test_delta_baseline_and_days_since_history_exclude_future_rows(monkeypatch):
    conn, context, definitions = _registry_fixture()
    selected = [
        item for item in definitions
        if item.key in {"subjective.day_rating", "training.session"}
    ]
    requested = DateRange(date(2026, 7, 1), date(2026, 7, 15))
    old_positive = date(2024, 1, 1)
    future_positive = date(2027, 1, 1)
    bounded_ranges = []
    include_future = {"value": False}

    def observation(key, day, value, source):
        return {
            "feature_key": key,
            "observed_at": day.isoformat(),
            "value": float(value),
            "state": "observed",
            "unit": "binary",
            "source": source,
            "provenance": {
                "adapter": "fixture",
                "table": "fixture_rows",
                "natural_key": f"fixture_rows:{key}:{day.isoformat()}",
            },
        }

    outcomes = [
        observation(
            "subjective.day_rating",
            requested.start + timedelta(days=offset),
            offset % 3 + 1,
            "outcome-fixture",
        )
        for offset in range(15)
    ]

    def fake_frame(
        _conn, frame_definitions, date_range, _context,
        *, include_provenance=False,
    ):
        assert include_provenance
        keys = {item.key for item in frame_definitions}
        if date_range.kind == "all":
            assert keys == {"training.session"}
            rows = [
                observation(
                    "training.session", old_positive, 1, "historical-fixture",
                )
            ]
            if include_future["value"]:
                rows.append(observation(
                    "training.session", future_positive, 1, "future-fixture",
                ))
        else:
            bounded_ranges.append(date_range)
            rows = outcomes
        return {"observations": rows}

    monkeypatch.setattr(
        associations_module, "build_feature_frame", fake_frame,
    )
    monkeypatch.setattr(
        associations_module,
        "build_readiness",
        lambda *_args, **_kwargs: {"ok": True},
    )

    first = analyze_outcome(
        conn,
        selected,
        requested,
        context,
        outcome_key="subjective.day_rating",
        mode="ordinal",
        top=100,
        include_internal=True,
        return_all_internal=True,
    )
    # lag 7 plus two adjacent 365-day windows requires 736 prior dates.
    assert bounded_ranges[-1].start == requested.start - timedelta(days=736)
    assert first["meta"]["baseline_range"]["from"] == old_positive.isoformat()
    historical = next(
        finding
        for finding in first["findings"]
        if (
            finding["exposure"]["components"][0]["exposure_key"]
            == "training.session"
            and finding["exposure"]["components"][0]["lag_days"] == 0
            and finding["exposure"]["components"][0]["window_days"] == 1
            and finding["exposure"]["components"][0]["transform"] == "days_since"
        )
    )
    assert historical["sample"]["complete_n"] == 15
    assert [row["exposure"] for row in historical["_rows"]] == [
        float((requested.start + timedelta(days=offset) - old_positive).days)
        for offset in range(15)
    ]

    include_future["value"] = True
    second = analyze_outcome(
        conn,
        selected,
        requested,
        context,
        outcome_key="subjective.day_rating",
        mode="ordinal",
        top=100,
        include_internal=True,
        return_all_internal=True,
    )
    assert second["meta"]["input_fingerprint"] == first["meta"][
        "input_fingerprint"
    ]
    assert second["coverage"]["source_manifests"] == first["coverage"][
        "source_manifests"
    ]
    conn.close()


def test_missingness_only_signal_is_suppressed_before_testing():
    # The complete subset is perfectly separated, but it represents only 40%
    # of eligible outcome units and therefore must never enter a BH family.
    rows = [
        _binary_row(
            index,
            index < 20,
            index < 20,
            ["observed"] if index < 20 else ["structural_zero"],
        )
        for index in range(40)
    ]
    assert _base_gate(
        _candidate(rows, eligible_n=100),
        "green-vs-non-green",
        30,
    ) == "high_missingness"


def test_null_and_reversed_binary_effects_remain_raw_and_explicit():
    null_rows = []
    reversed_rows = []
    for index in range(40):
        exposed = index < 20
        null_positive = index % 4 in {0, 1}
        reversed_positive = (
            15 <= index < 20 if exposed else 20 <= index < 35
        )
        null_rows.append({"exposure": float(exposed), "outcome": float(null_positive)})
        reversed_rows.append({
            "exposure": float(exposed),
            "outcome": float(reversed_positive),
        })
    assert _risk_effect(null_rows)["estimate"] == pytest.approx(0.0)
    reversed_effect = _risk_effect(reversed_rows)
    assert reversed_effect["estimate"] == pytest.approx(-0.5)
    assert reversed_effect["rates"]["risk_difference"] == pytest.approx(-0.5)


@pytest.fixture(scope="module")
def timing_and_transition_analysis():
    conn, context, definitions = _registry_fixture()
    selected = [
        item
        for item in definitions
        if item.key in {"subjective.day_rating", "sleep.duration_hours"}
    ]
    start = date(2026, 1, 1)
    for offset in range(90):
        day = start + timedelta(days=offset)
        sleep = 5.0 + (offset % 30) / 4.0
        rating = 1 if sleep < 6.5 else 2 if sleep < 9.0 else 3
        source = "manual-era" if offset < 45 else "wearable-era"
        conn.execute(
            """INSERT INTO sleep_log(
                 date,time_asleep_hours,time_in_bed_hours,source)
               VALUES(?,?,?,?)""",
            (day.isoformat(), sleep, 10.0, source),
        )
        conn.execute(
            "INSERT INTO subjective_daily(date,day_rating,source) VALUES(?,?,?)",
            (day.isoformat(), rating, "manual"),
        )
    conn.commit()
    result = analyze_outcome(
        conn,
        selected,
        DateRange(start, start + timedelta(days=89)),
        context,
        outcome_key="subjective.day_rating",
        mode="ordinal",
        top=100,
    )
    conn.close()
    return result


def test_same_day_unknown_order_and_source_transition_are_visible(
    timing_and_transition_analysis,
):
    finding = next(
        item
        for item in timing_and_transition_analysis["findings"]
        if item["exposure"]["components"][0]["lag_days"] == 0
        and item["exposure"]["components"][0]["window_days"] == 1
        and item["exposure"]["components"][0]["transform"] == "point"
    )
    component = finding["exposure"]["components"][0]
    assert component["temporal_direction"] == "same_day_or_order_unknown"
    assert "timing_unknown" in finding["warnings"]
    assert "source_transition" in finding["warnings"]


def test_slow_episode_is_not_forward_filled_or_duplicated_into_n():
    start = date(2026, 1, 1)
    observation = {
        "feature_key": "fitness.test.balance.left.value",
        "observed_at": (start + timedelta(days=15)).isoformat(),
        "value": 42.0,
        "state": "observed",
        "source": "fixture",
        "provenance": {"natural_key": "fitness_tests:1"},
    }
    by_feature = {
        observation["feature_key"]: {
            observation["observed_at"]: [observation, dict(observation)],
        }
    }
    rows = _outcome_rows(
        by_feature,
        observation["feature_key"],
        DateRange(start, start + timedelta(days=59)),
        SimpleNamespace(
            temporal_type="slow_measurement",
            completeness_profile="SLOW_EPISODE",
        ),
    )
    assert len(rows) == 1
    assert rows[0]["unit_key"] == "fitness_tests:1"


def test_all_frozen_lineage_and_temporal_boundaries_fail_closed():
    outcome = SimpleNamespace(
        key="subjective.day_rating",
        lineage_roots=("subjective.day_rating",),
        pillar="subjective",
    )
    assert _lineage_reason(outcome, outcome) == "same_feature"

    wcr = SimpleNamespace(
        key="body.wcr",
        lineage_roots=("body.waist_cm", "body.chest_cm"),
        pillar="body",
    )
    waist = SimpleNamespace(
        key="body.waist_cm",
        lineage_roots=("body.waist_cm",),
        pillar="body",
    )
    assert _lineage_reason(waist, wcr) is None
    assert _candidate_lineage_status(
        waist,
        wcr,
        [{
            "outcome_observations": [{
                "observed_at": "2026-07-01",
                "provenance": {
                    "parent_natural_keys": [
                        "body_metrics:1", "body_metrics:2",
                    ],
                },
            }],
            "exposure_observations": [{
                "observed_at": "2026-07-01",
                "provenance": {"natural_key": "body_metrics:1"},
            }],
        }],
    ) == "composite_root"

    e1rm = SimpleNamespace(
        key="training.exercise.squat.best_e1rm_kg",
        lineage_roots=("training.squat.session",),
        pillar="training",
    )
    training = SimpleNamespace(
        key="training.load.total",
        lineage_roots=("training.squat.session",),
        pillar="training",
    )
    assert _component_lineage_reason(
        training,
        e1rm,
        {
            "exposure_key": training.key,
            "lag_days": 0,
            "window_days": 1,
            "transform": "point",
        },
    ) == "same_session_e1rm"

    pain_outcome = SimpleNamespace(
        key="pain.nrs.knee.left",
        lineage_roots=("pain.knee.left",),
        pillar="pain",
    )
    rehab = SimpleNamespace(
        key="rehab.knee.exercise",
        lineage_roots=("pain.knee.left",),
        pillar="rehab",
    )
    assert _lineage_reason(rehab, pain_outcome) == "pain_self_derivation"

    recovery = SimpleNamespace(
        key="recovery.hrv_score.apple",
        lineage_roots=("hrv.apple.sdnn",),
        pillar="recovery",
    )
    other_recovery = SimpleNamespace(
        key="recovery.hrv_score.oura",
        lineage_roots=("hrv.oura.rmssd",),
        pillar="recovery",
    )
    assert _lineage_reason(other_recovery, recovery) == "source_specific_hrv"

    assert _strict_boundary(
        "running.progress.easy",
        {
            "exposure_key": "running.distance",
            "lag_days": 6,
            "window_days": 1,
            "transform": "point",
        },
        training,
    ) == "overlapping_running_week"
    assert not _relation_allowed(rehab, outcome)


def _all_mapping_keys(value):
    keys = set()
    if isinstance(value, dict):
        keys.update(value)
        for child in value.values():
            keys.update(_all_mapping_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_all_mapping_keys(child))
    return keys


def test_section12_metadata_and_source_manifest_are_structurally_complete(
    timing_and_transition_analysis,
):
    finding = timing_and_transition_analysis["findings"][0]
    assert {
        "display",
        "unit",
        "temporal_type",
        "direction",
    } <= set(finding["outcome"])
    component = finding["exposure"]["components"][0]
    assert {
        "display",
        "unit",
        "temporal_type",
        "direction",
        "merge_rule",
        "zero_semantics",
    } <= set(component)
    assert "evidence_fingerprint" in finding["provenance"]

    manifests = timing_and_transition_analysis["coverage"]["source_manifests"]
    assert manifests
    assert all(
        {
            "table",
            "source_labels",
            "natural_key_scheme",
            "row_count",
            "date_from",
            "date_to",
            "adapter",
            "merge_rule",
            "digest",
        } <= set(item)
        for item in manifests
    )
    response_keys = _all_mapping_keys(timing_and_transition_analysis)
    assert {
        "source_completeness",
    } <= response_keys or {"completeness_interval"} <= response_keys
