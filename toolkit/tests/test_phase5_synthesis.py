"""Phase 5 closed synthesis, deterministic rendering, and vault-write boundary."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
HEALTH = ROOT / "health.py"
sys.path.insert(0, str(ROOT))

from hermes_insights.contracts import canonical_json
from hermes_insights import orchestrator
from hermes_insights import synthesis as synthesis_module
from hermes_insights.ledger import (
    LEDGER_CONTRACT_VERSION,
    LedgerError,
    append_annotation,
    canonical_annotation_identity,
)
from hermes_insights.migrations import AUTONOMOUS_SCHEMA_VERSION, migrate
from hermes_insights.provenance import (
    candidate_key,
    evidence_fingerprint as finding_evidence_fingerprint,
    finding_id as canonical_finding_id,
    sha256_id,
)
from hermes_insights.synthesis import (
    LEGACY_MODEL_ID,
    LEGACY_PROVIDER,
    SYNTHESIS_CONTEXT_VERSION,
    SynthesisError,
    annotation_input_sha256,
    parse_synthesis_json,
    read_synthesis,
    record_synthesis,
    render_synthesis_markdown,
    synthesis_evidence_fingerprint,
    synthesis_history,
    validate_synthesis_record,
    write_synthesis_markdown,
)


SCHEMA = (ROOT / "SCHEMA.sql").read_text(encoding="utf-8")
CODE_VERSION = "a" * 40

BATCH_ID = "sha256:" + "1" * 64
RANGE_ID = "sha256:" + "2" * 64
RUN_ID = "sha256:" + "3" * 64
HYPOTHESIS_ID = "sha256:" + "5" * 64
SYNTHESIS_ID = "sha256:" + "6" * 64
INPUT_FINGERPRINT = "sha256:" + "8" * 64
PROMPT_SHA256 = "sha256:" + "a" * 64
ENGINE_SHA256 = "sha256:" + "b" * 64
REGISTRY_SHA256 = "sha256:" + "c" * 64
OUTCOME_SET_SHA256 = sha256_id({
    "contract_version": LEDGER_CONTRACT_VERSION,
    "outcomes": [{
        "outcome_key": "subjective.day_rating",
        "outcome_mode": "green-vs-non-green",
    }],
})

COMPONENT = {
    "exposure_key": "food.named.i_" + "d" * 64 + ".occurred",
    "lag_days": 1,
    "window_days": 1,
    "transform": "point",
}
CANDIDATE_KEY = candidate_key(
    "subjective.day_rating", "green-vs-non-green", [COMPONENT],
)
FINDING_ID = canonical_finding_id(
    candidate_key=CANDIDATE_KEY,
    analysis_range={
        "kind": "bounded", "from": "2026-01-01", "to": "2026-07-22",
    },
    baseline_range={
        "kind": "bounded", "from": "2025-12-31", "to": "2026-07-22",
    },
    input_fingerprint=INPUT_FINGERPRINT,
)


def _finding() -> tuple[dict, str]:
    component = {
        **COMPONENT,
        "display": "Avocado",
        "unit": "occurrence",
        "temporal_type": "event_occurrence",
        "direction": "higher_better",
        "merge_rule": "identity",
        "zero_semantics": "structural_zero_if_complete",
        "temporal_direction": "exposure_precedes_outcome",
    }
    finding = {
        "finding_id": FINDING_ID,
        "candidate_key": CANDIDATE_KEY,
        "outcome": {
            "key": "subjective.day_rating",
            "display": "Day rating",
            "unit": "ordinal_1_3",
            "temporal_type": "outcome",
            "direction": "higher_better",
            "merge_rule": "one-row-per-date",
            "zero_semantics": "invalid",
            "mode": "green-vs-non-green",
        },
        "exposure": {"components": [component]},
        "sample": {
            "eligible_n": 220,
            "complete_n": 180,
            "missing_n": 40,
            "exposed_n": 18,
            "unexposed_n": 162,
            "outcome_positive_n": 79,
            "outcome_negative_n": 101,
            "source_completeness": {
                "from": "2025-12-31",
                "to": "2026-07-22",
                "completeness_revision_ids": [],
                "source_sync_interval_ids": [],
            },
        },
        "rates": {
            "baseline": 0.439,
            "exposed": 0.611,
            "unexposed": 0.420,
            "risk_difference": 0.191,
            "risk_ratio": 1.4551234567899,
        },
        "effect": {
            "method": "risk_difference",
            "estimate": 0.191,
            "oriented_estimate": 0.191,
            "ci95": [0.031, 0.351],
            "oriented_ci95": [0.031, 0.351],
        },
        "testing": {
            "p": 0.021,
            "q": 0.087,
            "family_size": 184,
            "method": "fisher_exact",
            "seed": 123,
            "permutation_iterations": None,
            "bootstrap_iterations": None,
        },
        "stability": {
            "status": "stable",
            "full": 0.191,
            "first_half": 0.170,
            "second_half": 0.207,
        },
        "confounders": {
            "checked": [],
            "unchecked": [],
            "sensitive_to": ["training.day"],
            "weighted_effect": 0.103,
        },
        "evidence_for": [
            {"code": "effect_gate_pass", "value": 0.191, "threshold": 0.10},
            {"code": "multiplicity_gate_pass", "value": 0.087, "threshold": 0.10},
        ],
        "evidence_against": [
            {
                "code": "confounder_attenuation",
                "value": 0.103,
                "threshold": 0.115,
                "detail": "training.day",
            }
        ],
        "warnings": ["multiple_testing", "association_not_causation"],
        "quality": {
            "tier": "exploratory_unreplicated",
            "eligible_for_hypothesis": True,
        },
    }
    fingerprint = finding_evidence_fingerprint(finding)
    finding["provenance"] = {
        "analysis_version": "outcome-v1",
        "registry_version": "feature-registry-v1",
        "engine_sha256": ENGINE_SHA256,
        "registry_sha256": REGISTRY_SHA256,
        "input_fingerprint": INPUT_FINGERPRINT,
        "evidence_fingerprint": fingerprint,
    }
    assert finding_evidence_fingerprint(finding) == fingerprint
    return finding, fingerprint


def _insufficient_audit_finding(
    input_fingerprint: str,
) -> tuple[dict, str]:
    finding, _unused = _finding()
    finding = deepcopy(finding)
    finding["finding_id"] = canonical_finding_id(
        candidate_key=CANDIDATE_KEY,
        analysis_range={
            "kind": "bounded", "from": "2026-01-01", "to": "2026-07-22",
        },
        baseline_range={
            "kind": "bounded", "from": "2025-12-31", "to": "2026-07-22",
        },
        input_fingerprint=input_fingerprint,
    )
    finding["sample"] = {
        "eligible_n": 20,
        "complete_n": 20,
        "missing_n": 0,
        "exposed_n": None,
        "unexposed_n": None,
        "outcome_positive_n": None,
        "outcome_negative_n": None,
        "source_completeness": {
            "from": "2025-12-31",
            "to": "2026-07-22",
            "completeness_revision_ids": [],
            "source_sync_interval_ids": [],
        },
    }
    finding["rates"] = None
    finding["effect"] = {
        "method": None,
        "estimate": None,
        "oriented_estimate": None,
        "ci95": None,
        "oriented_ci95": None,
    }
    finding["testing"] = {
        "p": None,
        "q": None,
        "family_size": None,
        "method": None,
        "seed": None,
        "permutation_iterations": 0,
        "bootstrap_iterations": 0,
    }
    finding["stability"] = {
        "status": "insufficient",
        "full": None,
        "first_half": None,
        "second_half": None,
    }
    finding["confounders"] = {
        "checked": [],
        "unchecked": [
            {"key": key, "reason": "not_checked_base_gate"}
            for key in (
                "weekend",
                "calendar_quarter",
                "illness",
                "travel",
                "medication_regime",
                "training_phase",
                "source_era",
                "source_transition",
            )
        ],
        "sensitive_to": [],
        "weighted_effect": None,
    }
    finding["evidence_for"] = []
    finding["evidence_against"] = [{
        "code": "aligned_n_below_gate",
        "value": 20,
        "threshold": None,
    }]
    finding["warnings"] = ["association_not_causation"]
    finding["quality"] = {
        "tier": "insufficient",
        "eligible_for_hypothesis": False,
    }
    finding["provenance"] = {
        "analysis_version": "outcome-v1",
        "registry_version": "feature-registry-v1",
        "engine_sha256": ENGINE_SHA256,
        "registry_sha256": REGISTRY_SHA256,
        "input_fingerprint": input_fingerprint,
        "dependencies": {},
        "evidence_fingerprint": "sha256:" + "0" * 64,
    }
    fingerprint = finding_evidence_fingerprint(finding)
    finding["provenance"]["evidence_fingerprint"] = fingerprint
    return finding, fingerprint


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    path = tmp_path / "synthesis.db"
    initial = sqlite3.connect(path)
    initial.executescript(SCHEMA)
    initial.commit()
    initial.close()
    migrate(
        str(path), AUTONOMOUS_SCHEMA_VERSION, 0,
        code_version=CODE_VERSION,
    )

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    finding, fingerprint = _finding()
    connection.execute(
        """
        INSERT INTO analysis_batches(
          batch_id,dedupe_key,run_kind,anchor_date,initiator_key,
          range_plan_version,outcome_selection,outcome_set_sha256,
          analysis_version,registry_version,engine_sha256,registry_sha256,
          status,status_reason_code,run_count,completed_count,insufficient_count,
          no_data_count,failed_count,started_at,completed_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            BATCH_ID, "batch-dedupe", "manual", "2026-07-22", "fixture",
            "analysis-range-plan-v1", "explicit_set", OUTCOME_SET_SHA256,
            "outcome-v1", "feature-registry-v1", ENGINE_SHA256,
            REGISTRY_SHA256, "completed", None, 1, 1, 0, 0, 0,
            "2026-07-23T08:00:00+00:00", "2026-07-23T08:00:01+00:00",
        ),
    )
    connection.execute(
        """
        INSERT INTO analysis_range_requests(
          range_id,batch_id,range_role,requested_range_kind,requested_from,
          requested_to
        ) VALUES(?,?,?,?,?,?)
        """,
        (RANGE_ID, BATCH_ID, "primary", "bounded", "2026-01-01", "2026-07-22"),
    )
    result = {
        "ok": True,
        "contract_version": "outcome-associations-v1",
        "meta": {
            "analysis_version": "outcome-v1",
            "registry_version": "feature-registry-v1",
            "engine_sha256": ENGINE_SHA256,
            "registry_sha256": REGISTRY_SHA256,
            "input_fingerprint": INPUT_FINGERPRINT,
            "generated_at": "2026-07-23T08:00:01+00:00",
            "timezone": "Europe/Paris",
            "requested_range": {
                "kind": "bounded",
                "from": "2026-01-01",
                "to": "2026-07-22",
            },
            "analysis_range": {
                "kind": "bounded",
                "from": "2026-01-01",
                "to": "2026-07-22",
            },
            "baseline_range": {
                "kind": "bounded",
                "from": "2025-12-31",
                "to": "2026-07-22",
            },
            "outcome": "subjective.day_rating",
            "modes": ["green-vs-non-green"],
            "min_n": 30,
            "interactions": "none",
            "top": 100,
            "candidate_family_sizes": {"green-vs-non-green": 1},
            "interaction_family_sizes": {},
        },
        "coverage": {
            "outcome_eligible_n": 220,
            "modes": {
                "green-vs-non-green": {
                    "outcome_eligible_n": 220,
                    "generated_candidates": 1,
                    "tested_candidates": 1,
                    "insufficient_candidates": 0,
                    "family_size": 1,
                },
            },
            "source_manifests": [],
            "dependencies": {},
        },
        "readiness": {
            "ok": True,
            "meta": {
                "readiness_version": "data-readiness-v1",
                "registry_version": "feature-registry-v1",
                "registry_sha256": REGISTRY_SHA256,
                "timezone": "Europe/Paris",
                "range": {
                    "kind": "bounded",
                    "from": "2026-01-01",
                    "to": "2026-07-22",
                },
                "goal": None,
                "outcome": "subjective.day_rating",
                "feature_count": 0,
                "state_counts": {},
                "predicate_order": [],
            },
            "features": [],
        },
        "findings": [finding],
        "suppression_counts": {},
        "warnings": ["association_not_causation"],
    }
    result_json = canonical_json(result)
    result_sha256 = sha256_id({
        "contract_version": LEDGER_CONTRACT_VERSION,
        "kind": "analysis_result",
        "result": result,
    })
    connection.execute(
        """
        INSERT INTO analysis_runs(
          run_id,batch_id,range_id,outcome_key,outcome_mode,range_resolution,
          analysis_from,analysis_to,baseline_from,baseline_to,input_fingerprint,
          status,status_reason_code,result_json,result_sha256,started_at,completed_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            RUN_ID, BATCH_ID, RANGE_ID, "subjective.day_rating",
            "green-vs-non-green", "bounded_exact", "2026-01-01", "2026-07-22",
            "2025-12-31", "2026-07-22", INPUT_FINGERPRINT, "completed", None,
            result_json, result_sha256, "2026-07-23T08:00:00+00:00",
            "2026-07-23T08:00:01+00:00",
        ),
    )
    connection.execute(
        """
        INSERT INTO analysis_findings(
          finding_id,run_id,candidate_key,candidate_kind,outcome_key,outcome_mode,
          direction,quality_tier,eligible_for_hypothesis,evidence_json,
          evidence_for_json,evidence_against_json,evidence_fingerprint
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            FINDING_ID, RUN_ID, finding["candidate_key"], "single",
            "subjective.day_rating", "green-vs-non-green", "positive",
            "exploratory_unreplicated", 1, canonical_json(finding),
            canonical_json(finding["evidence_for"]),
            canonical_json(finding["evidence_against"]), fingerprint,
        ),
    )
    component = finding["exposure"]["components"][0]
    connection.execute(
        """
        INSERT INTO analysis_finding_components(
          finding_id,position,exposure_key,lag_days,window_days,transform,
          temporal_direction
        ) VALUES(?,?,?,?,?,?,?)
        """,
        (
            FINDING_ID, 1, component["exposure_key"], component["lag_days"],
            component["window_days"], component["transform"],
            component["temporal_direction"],
        ),
    )
    connection.execute(
        """
        INSERT INTO hypotheses(
          hypothesis_id,dedupe_key,outcome_key,outcome_mode,candidate_kind,
          initial_direction,first_seen,created_at,created_by_run,
          created_by_finding
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            HYPOTHESIS_ID, "hypothesis-dedupe", "subjective.day_rating",
            "green-vs-non-green", "single", "positive", "2026-01-01",
            "2026-07-23T08:00:02+00:00", RUN_ID, FINDING_ID,
        ),
    )
    connection.execute(
        """
        INSERT INTO hypothesis_components(
          hypothesis_id,position,exposure_key,lag_days,window_days,transform
        ) VALUES(?,?,?,?,?,?)
        """,
        (
            HYPOTHESIS_ID, 1, component["exposure_key"], component["lag_days"],
            component["window_days"], component["transform"],
        ),
    )
    connection.execute(
        """
        INSERT INTO hypothesis_evaluations(
          id,hypothesis_id,run_id,finding_id,tested_at,range_from,range_to,
          evidence_class,previous_status,evidence_for_json,evidence_against_json,
          confounders_json,sample_size_json,effect_summary_json,stability_json,
          confidence,status,change_reason,change_conditions,
          source_analysis_version,input_fingerprint,evidence_fingerprint,
          previous_evaluation_id,comparison_evaluation_id,new_eligible_observations,
          compatible_with_prior,transition_applied,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            1, HYPOTHESIS_ID, RUN_ID, FINDING_ID,
            "2026-07-23T08:00:01+00:00", "2026-01-01", "2026-07-22",
            "initial_discovery_pass", None,
            canonical_json(finding["evidence_for"]),
            canonical_json(finding["evidence_against"]),
            canonical_json(finding["confounders"]),
            canonical_json(finding["sample"]),
            canonical_json({
                "effect": finding["effect"],
                "rates": finding["rates"],
                "testing": finding["testing"],
                "quality": finding["quality"],
                "direction": "positive",
                "provenance": {
                    key: finding["provenance"][key]
                    for key in (
                        "analysis_version",
                        "registry_version",
                        "engine_sha256",
                        "registry_sha256",
                    )
                },
            }),
            canonical_json(finding["stability"]),
            "low", "candidate", "first discovery pass",
            "new non-overlapping data could replicate or weaken this result",
            "outcome-v1", INPUT_FINGERPRINT, fingerprint, None, None, 180, 1, 1,
            "2026-07-23T08:00:02+00:00",
        ),
    )
    for polarity, evidence_kind in (
        ("for", "same_direction_effect"),
        ("against", "confounder_sensitivity"),
    ):
        evidence_item_id = sha256_id({
            "contract_version": LEDGER_CONTRACT_VERSION,
            "kind": "hypothesis_evidence_item",
            "hypothesis_id": HYPOTHESIS_ID,
            "finding_id": FINDING_ID,
            "evidence_kind": evidence_kind,
        })
        connection.execute(
            """
            INSERT INTO hypothesis_evidence_items(
              evidence_item_id,hypothesis_id,evaluation_id,finding_id,polarity,
              evidence_kind,evidence_fingerprint,source_analysis_version,
              range_from,range_to,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                evidence_item_id, HYPOTHESIS_ID, 1, FINDING_ID, polarity,
                evidence_kind, fingerprint, "outcome-v1", "2026-01-01",
                "2026-07-22", "2026-07-23T08:00:02+00:00",
            ),
        )
    connection.commit()
    try:
        yield connection
    finally:
        connection.close()


def _refs() -> tuple[list[dict], list[dict], list[dict]]:
    return (
        [{"run_id": RUN_ID, "purpose": "primary"}],
        [{"finding_id": FINDING_ID, "role": "primary"}],
        [{
            "hypothesis_id": HYPOTHESIS_ID,
            "evaluation_id": 1,
            "role": "changed",
        }],
    )


def _payload(
    conn: sqlite3.Connection,
    *,
    synthesis_id: str = SYNTHESIS_ID,
    narrative: str | None = (
        "This is an exploratory association. A small repeatable logging "
        "experiment could test whether the pattern persists."
    ),
    status: str = "completed",
    annotations: list[dict] | None = None,
    no_message_reason: str | None = None,
) -> dict:
    run_refs, finding_refs, hypothesis_refs = _refs()
    fingerprint = synthesis_evidence_fingerprint(
        conn,
        analysis_batch_id=BATCH_ID,
        run_refs=run_refs,
        finding_refs=finding_refs,
        hypothesis_refs=hypothesis_refs,
    )
    prompt = PROMPT_SHA256 if status == "completed" else None
    model = LEGACY_MODEL_ID if status == "completed" else None
    provider = LEGACY_PROVIDER if status == "completed" else None
    result = {
        "synthesis_id": synthesis_id,
        "analysis_batch_id": BATCH_ID,
        "cadence": "manual",
        "reason_code": "fixture_review",
        "cutoff_date": "2026-07-22",
        "evidence_fingerprint": fingerprint,
        "context_version": SYNTHESIS_CONTEXT_VERSION,
        "prompt_sha256": prompt,
        "model_id": model,
        "provider": provider,
        "run_refs": run_refs,
        "finding_refs": finding_refs,
        "hypothesis_refs": hypothesis_refs,
        "narrative_md": narrative,
        "rendered_md": None,
        "status": status,
        "no_message_reason_code": no_message_reason,
        "annotations": annotations or [],
        "notification": None,
    }
    return result


def _insert_second_batch_with_reused_finding(
    connection: sqlite3.Connection,
) -> tuple[str, str]:
    batch_id = "sha256:" + "d" * 64
    range_id = "sha256:" + "9" * 64
    run_id = "sha256:" + "4" * 64
    original = connection.execute(
        "SELECT result_json,result_sha256 FROM analysis_runs WHERE run_id=?",
        (RUN_ID,),
    ).fetchone()
    connection.execute(
        """
        INSERT INTO analysis_batches(
          batch_id,dedupe_key,run_kind,anchor_date,initiator_key,
          range_plan_version,outcome_selection,outcome_set_sha256,
          analysis_version,registry_version,engine_sha256,registry_sha256,
          status,status_reason_code,run_count,completed_count,insufficient_count,
          no_data_count,failed_count,started_at,completed_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            batch_id, "batch-dedupe-reused", "manual", "2026-07-22",
            "fixture-retry", "analysis-range-plan-v1", "explicit_set",
            OUTCOME_SET_SHA256, "outcome-v1", "feature-registry-v1",
            ENGINE_SHA256, REGISTRY_SHA256, "completed", None, 1, 1, 0, 0, 0,
            "2026-07-23T09:00:00+00:00", "2026-07-23T09:00:01+00:00",
        ),
    )
    connection.execute(
        """
        INSERT INTO analysis_range_requests(
          range_id,batch_id,range_role,requested_range_kind,requested_from,
          requested_to
        ) VALUES(?,?,?,?,?,?)
        """,
        (range_id, batch_id, "primary", "bounded", "2026-01-01", "2026-07-22"),
    )
    connection.execute(
        """
        INSERT INTO analysis_runs(
          run_id,batch_id,range_id,outcome_key,outcome_mode,range_resolution,
          analysis_from,analysis_to,baseline_from,baseline_to,input_fingerprint,
          status,status_reason_code,result_json,result_sha256,started_at,completed_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id, batch_id, range_id, "subjective.day_rating",
            "green-vs-non-green", "bounded_exact", "2026-01-01", "2026-07-22",
            "2025-12-31", "2026-07-22", INPUT_FINGERPRINT, "completed", None,
            original["result_json"], original["result_sha256"],
            "2026-07-23T09:00:00+00:00", "2026-07-23T09:00:01+00:00",
        ),
    )
    return batch_id, run_id


def _insert_authenticated_batch(
    connection: sqlite3.Connection,
    *,
    label: str,
    result: dict,
    new_findings: list[dict],
    anchor_date: str = "2026-07-22",
) -> tuple[str, str]:
    batch_id = sha256_id({"fixture-batch": label})
    range_id = sha256_id({"fixture-range": label})
    run_id = sha256_id({"fixture-run": label})
    meta = result["meta"]
    connection.execute(
        """
        INSERT INTO analysis_batches(
          batch_id,dedupe_key,run_kind,anchor_date,initiator_key,
          range_plan_version,outcome_selection,outcome_set_sha256,
          analysis_version,registry_version,engine_sha256,registry_sha256,
          status,status_reason_code,run_count,completed_count,insufficient_count,
          no_data_count,failed_count,started_at,completed_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            batch_id, f"batch-dedupe-{label}", "manual", anchor_date,
            f"fixture-{label}", "analysis-range-plan-v1", "explicit_set",
            OUTCOME_SET_SHA256, meta["analysis_version"], meta["registry_version"],
            meta["engine_sha256"], meta["registry_sha256"], "completed", None,
            1, 1, 0, 0, 0, "2026-07-23T11:00:00+00:00",
            "2026-07-23T11:00:01+00:00",
        ),
    )
    requested = meta["requested_range"]
    connection.execute(
        """
        INSERT INTO analysis_range_requests(
          range_id,batch_id,range_role,requested_range_kind,requested_from,
          requested_to
        ) VALUES(?,?,?,?,?,?)
        """,
        (
            range_id, batch_id, "primary", requested["kind"],
            requested["from"], requested["to"],
        ),
    )
    result_json = canonical_json(result)
    result_sha256 = sha256_id({
        "contract_version": LEDGER_CONTRACT_VERSION,
        "kind": "analysis_result",
        "result": result,
    })
    analysis_range = meta["analysis_range"]
    baseline_range = meta["baseline_range"]
    connection.execute(
        """
        INSERT INTO analysis_runs(
          run_id,batch_id,range_id,outcome_key,outcome_mode,range_resolution,
          analysis_from,analysis_to,baseline_from,baseline_to,input_fingerprint,
          status,status_reason_code,result_json,result_sha256,started_at,completed_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id, batch_id, range_id, meta["outcome"], meta["modes"][0],
            "bounded_exact", analysis_range["from"], analysis_range["to"],
            baseline_range["from"], baseline_range["to"],
            meta["input_fingerprint"], "completed", None, result_json,
            result_sha256, "2026-07-23T11:00:00+00:00",
            "2026-07-23T11:00:01+00:00",
        ),
    )
    for finding in new_findings:
        components = finding["exposure"]["components"]
        oriented = finding["effect"]["oriented_estimate"]
        direction = (
            "positive" if oriented > 0
            else "negative" if oriented < 0
            else "unknown"
        )
        connection.execute(
            """
            INSERT INTO analysis_findings(
              finding_id,run_id,candidate_key,candidate_kind,outcome_key,
              outcome_mode,direction,quality_tier,eligible_for_hypothesis,
              evidence_json,evidence_for_json,evidence_against_json,
              evidence_fingerprint
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                finding["finding_id"], run_id, finding["candidate_key"],
                "single" if len(components) == 1 else "pair",
                finding["outcome"]["key"], finding["outcome"]["mode"], direction,
                finding["quality"]["tier"],
                int(finding["quality"]["eligible_for_hypothesis"]),
                canonical_json(finding), canonical_json(finding["evidence_for"]),
                canonical_json(finding["evidence_against"]),
                finding["provenance"]["evidence_fingerprint"],
            ),
        )
        for position, component in enumerate(components, 1):
            connection.execute(
                """
                INSERT INTO analysis_finding_components(
                  finding_id,position,exposure_key,lag_days,window_days,
                  transform,temporal_direction
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    finding["finding_id"], position, component["exposure_key"],
                    component["lag_days"], component["window_days"],
                    component["transform"], component["temporal_direction"],
                ),
            )
    return batch_id, run_id


def _result_for_range(
    connection: sqlite3.Connection,
    *,
    label: str,
    start: str,
    end: str,
) -> tuple[dict, dict]:
    result = json.loads(connection.execute(
        "SELECT result_json FROM analysis_runs WHERE run_id=?",
        (RUN_ID,),
    ).fetchone()[0])
    finding = deepcopy(result["findings"][0])
    input_fingerprint = sha256_id({"fixture-input": label})
    requested = {"kind": "bounded", "from": start, "to": end}
    result["meta"]["requested_range"] = dict(requested)
    result["meta"]["analysis_range"] = dict(requested)
    result["meta"]["baseline_range"] = dict(requested)
    result["meta"]["input_fingerprint"] = input_fingerprint
    finding["finding_id"] = canonical_finding_id(
        candidate_key=finding["candidate_key"],
        analysis_range=requested,
        baseline_range=requested,
        input_fingerprint=input_fingerprint,
    )
    finding["sample"]["source_completeness"]["from"] = start
    finding["sample"]["source_completeness"]["to"] = end
    finding["provenance"]["input_fingerprint"] = input_fingerprint
    finding["provenance"]["evidence_fingerprint"] = (
        finding_evidence_fingerprint(finding)
    )
    result["findings"] = [finding]
    readiness_meta = result.get("readiness", {}).get("meta")
    if isinstance(readiness_meta, dict):
        readiness_meta["range"] = dict(requested)
    return result, finding


def _append_hypothesis_evaluation(
    connection: sqlite3.Connection,
    *,
    evaluation_id: int,
    run_id: str,
    finding: dict,
    previous_evaluation_id: int,
) -> None:
    run = connection.execute(
        "SELECT completed_at,analysis_from,analysis_to FROM analysis_runs "
        "WHERE run_id=?",
        (run_id,),
    ).fetchone()
    effect_summary = {
        "effect": finding["effect"],
        "rates": finding["rates"],
        "testing": finding["testing"],
        "quality": finding["quality"],
        "direction": "positive",
        "provenance": {
            key: finding["provenance"][key]
            for key in (
                "analysis_version",
                "registry_version",
                "engine_sha256",
                "registry_sha256",
            )
        },
    }
    connection.execute(
        """
        INSERT INTO hypothesis_evaluations(
          id,hypothesis_id,run_id,finding_id,tested_at,range_from,range_to,
          evidence_class,previous_status,evidence_for_json,evidence_against_json,
          confounders_json,sample_size_json,effect_summary_json,stability_json,
          confidence,status,change_reason,change_conditions,
          source_analysis_version,input_fingerprint,evidence_fingerprint,
          previous_evaluation_id,comparison_evaluation_id,new_eligible_observations,
          compatible_with_prior,transition_applied,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            evaluation_id, HYPOTHESIS_ID, run_id, finding["finding_id"],
            run["completed_at"], run["analysis_from"], run["analysis_to"],
            "same_exploratory", "candidate",
            canonical_json(finding["evidence_for"]),
            canonical_json(finding["evidence_against"]),
            canonical_json(finding["confounders"]),
            canonical_json(finding["sample"]),
            canonical_json(effect_summary),
            canonical_json(finding["stability"]),
            "low", "candidate", "same_pass_without_new_eligible_observations",
            canonical_json({"new_eligible_observations": 0}),
            finding["provenance"]["analysis_version"],
            finding["provenance"]["input_fingerprint"],
            finding["provenance"]["evidence_fingerprint"],
            previous_evaluation_id, previous_evaluation_id, 0, 1, 0,
            "2026-07-23T12:00:02+00:00",
        ),
    )
    evidence_item_id = sha256_id({
        "contract_version": LEDGER_CONTRACT_VERSION,
        "kind": "hypothesis_evidence_item",
        "hypothesis_id": HYPOTHESIS_ID,
        "finding_id": finding["finding_id"],
        "evidence_kind": "same_direction_effect",
    })
    connection.execute(
        """
        INSERT INTO hypothesis_evidence_items(
          evidence_item_id,hypothesis_id,evaluation_id,finding_id,polarity,
          evidence_kind,evidence_fingerprint,source_analysis_version,
          range_from,range_to,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            evidence_item_id, HYPOTHESIS_ID, evaluation_id,
            finding["finding_id"], "for", "same_direction_effect",
            finding["provenance"]["evidence_fingerprint"],
            finding["provenance"]["analysis_version"],
            run["analysis_from"], run["analysis_to"],
            "2026-07-23T12:00:02+00:00",
        ),
    )


def _insert_pair_batch(
    connection: sqlite3.Connection,
) -> tuple[str, str, dict]:
    result = json.loads(connection.execute(
        "SELECT result_json FROM analysis_runs WHERE run_id=?",
        (RUN_ID,),
    ).fetchone()[0])
    pair = deepcopy(result["findings"][0])
    second = {
        **pair["exposure"]["components"][0],
        "exposure_key": "nutrition.protein_g",
        "display": "Protein",
        "unit": "g",
        "temporal_type": "daily_aggregate",
        "direction": "higher_better",
        "merge_rule": "one-row-per-date",
        "zero_semantics": "missing_if_absent",
        "lag_days": 3,
        "window_days": 7,
        "transform": "mean",
        "temporal_direction": "exposure_precedes_outcome",
    }
    pair["exposure"]["components"].append(second)
    identities = [{
        key: component[key]
        for key in ("exposure_key", "lag_days", "window_days", "transform")
    } for component in pair["exposure"]["components"]]
    pair["candidate_key"] = candidate_key(
        "subjective.day_rating", "green-vs-non-green", identities,
    )
    pair["finding_id"] = canonical_finding_id(
        candidate_key=pair["candidate_key"],
        analysis_range=result["meta"]["analysis_range"],
        baseline_range=result["meta"]["baseline_range"],
        input_fingerprint=INPUT_FINGERPRINT,
    )
    pair["sample"]["exposed_n"] = None
    pair["sample"]["unexposed_n"] = None
    pair["sample"]["interaction_cells"] = {
        "neither": 40, "a_only": 45, "b_only": 45, "both": 50,
    }
    pair["rates"] = {
        "baseline": 0.439,
        "neither": 0.4,
        "a_only": 0.5,
        "b_only": 0.55,
        "both": 0.8,
        "component_a_difference": 0.1,
        "component_b_difference": 0.15,
        "incremental_risk_difference": 0.25,
        "oriented_incremental_risk_difference": 0.25,
        "oriented_component_a_difference": 0.1,
        "oriented_component_b_difference": 0.15,
    }
    pair["effect"] = {
        "method": "incremental_risk_difference",
        "estimate": 0.25,
        "oriented_estimate": 0.25,
        "ci95": [0.05, 0.45],
        "oriented_ci95": [0.05, 0.45],
    }
    pair["stability"] = {
        "status": "not_applicable",
        "full": 0.25,
        "first_half": None,
        "second_half": None,
    }
    pair["provenance"]["evidence_fingerprint"] = (
        finding_evidence_fingerprint(pair)
    )
    result["meta"]["interactions"] = "pairwise"
    result["meta"]["interaction_family_sizes"] = {
        "green-vs-non-green": 1,
    }
    result["findings"].insert(0, pair)
    batch_id, run_id = _insert_authenticated_batch(
        connection, label="pair-render", result=result, new_findings=[pair],
    )
    return batch_id, run_id, pair


def _append_followup_evaluation_with_evidence(
    connection: sqlite3.Connection,
) -> tuple[int, str]:
    result = json.loads(connection.execute(
        "SELECT result_json FROM analysis_runs WHERE run_id=?",
        (RUN_ID,),
    ).fetchone()[0])
    finding = deepcopy(result["findings"][0])
    input_fingerprint = sha256_id({"fixture-input": "future-evaluation"})
    result["meta"]["input_fingerprint"] = input_fingerprint
    result["meta"]["generated_at"] = "2026-07-23T11:00:01+00:00"
    finding["finding_id"] = canonical_finding_id(
        candidate_key=finding["candidate_key"],
        analysis_range=result["meta"]["analysis_range"],
        baseline_range=result["meta"]["baseline_range"],
        input_fingerprint=input_fingerprint,
    )
    finding["provenance"]["input_fingerprint"] = input_fingerprint
    finding["provenance"]["evidence_fingerprint"] = (
        finding_evidence_fingerprint(finding)
    )
    result["findings"] = [finding]
    _batch_id, run_id = _insert_authenticated_batch(
        connection,
        label="future-evaluation",
        result=result,
        new_findings=[finding],
    )
    evaluation_id = 2
    effect_summary = {
        "effect": finding["effect"],
        "rates": finding["rates"],
        "testing": finding["testing"],
        "quality": finding["quality"],
        "direction": "positive",
        "provenance": {
            key: finding["provenance"][key]
            for key in (
                "analysis_version",
                "registry_version",
                "engine_sha256",
                "registry_sha256",
            )
        },
    }
    connection.execute(
        """
        INSERT INTO hypothesis_evaluations(
          id,hypothesis_id,run_id,finding_id,tested_at,range_from,range_to,
          evidence_class,previous_status,evidence_for_json,evidence_against_json,
          confounders_json,sample_size_json,effect_summary_json,stability_json,
          confidence,status,change_reason,change_conditions,
          source_analysis_version,input_fingerprint,evidence_fingerprint,
          previous_evaluation_id,comparison_evaluation_id,new_eligible_observations,
          compatible_with_prior,transition_applied,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            evaluation_id, HYPOTHESIS_ID, run_id, finding["finding_id"],
            "2026-07-23T11:00:01+00:00", "2026-01-01", "2026-07-22",
            "same_exploratory", "candidate",
            canonical_json(finding["evidence_for"]),
            canonical_json(finding["evidence_against"]),
            canonical_json(finding["confounders"]),
            canonical_json(finding["sample"]),
            canonical_json(effect_summary),
            canonical_json(finding["stability"]),
            "low", "candidate", "same_pass_without_new_eligible_observations",
            canonical_json({"new_eligible_observations": 0}),
            "outcome-v1", input_fingerprint,
            finding["provenance"]["evidence_fingerprint"],
            1, 1, 0, 1, 0, "2026-07-23T11:00:02+00:00",
        ),
    )
    evidence_item_id = sha256_id({
        "contract_version": LEDGER_CONTRACT_VERSION,
        "kind": "hypothesis_evidence_item",
        "hypothesis_id": HYPOTHESIS_ID,
        "finding_id": finding["finding_id"],
        "evidence_kind": "same_direction_effect",
    })
    connection.execute(
        """
        INSERT INTO hypothesis_evidence_items(
          evidence_item_id,hypothesis_id,evaluation_id,finding_id,polarity,
          evidence_kind,evidence_fingerprint,source_analysis_version,
          range_from,range_to,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            evidence_item_id, HYPOTHESIS_ID, evaluation_id,
            finding["finding_id"], "for", "same_direction_effect",
            finding["provenance"]["evidence_fingerprint"], "outcome-v1",
            "2026-01-01", "2026-07-22", "2026-07-23T11:00:02+00:00",
        ),
    )
    return evaluation_id, evidence_item_id


def _insert_dormant_batch(
    connection: sqlite3.Connection,
    evidence_class: str,
    *,
    include_audit_finding: bool = False,
) -> tuple[str, str, int, str]:
    batch_id = "sha256:" + "d" * 64
    range_id = "sha256:" + "9" * 64
    run_id = "sha256:" + "4" * 64
    input_fingerprint = "sha256:" + "7" * 64
    audit_finding, audit_fingerprint = (
        _insufficient_audit_finding(input_fingerprint)
        if include_audit_finding
        else (None, None)
    )
    outcome_eligible_n = 90 if audit_finding is not None else 0
    connection.execute(
        """
        INSERT INTO analysis_batches(
          batch_id,dedupe_key,run_kind,anchor_date,initiator_key,
          range_plan_version,outcome_selection,outcome_set_sha256,
          analysis_version,registry_version,engine_sha256,registry_sha256,
          status,status_reason_code,run_count,completed_count,insufficient_count,
          no_data_count,failed_count,started_at,completed_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            batch_id, "batch-dedupe-dormant", "manual", "2026-07-22",
            "fixture-dormancy", "analysis-range-plan-v1", "explicit_set",
            OUTCOME_SET_SHA256, "outcome-v1", "feature-registry-v1",
            ENGINE_SHA256, REGISTRY_SHA256, "completed", None, 1, 0, 1, 0, 0,
            "2026-07-23T10:00:00+00:00", "2026-07-23T10:00:01+00:00",
        ),
    )
    connection.execute(
        """
        INSERT INTO analysis_range_requests(
          range_id,batch_id,range_role,requested_range_kind,requested_from,
          requested_to
        ) VALUES(?,?,?,?,?,?)
        """,
        (range_id, batch_id, "primary", "bounded", "2026-01-01", "2026-07-22"),
    )
    result = {
        "ok": True,
        "contract_version": "outcome-associations-v1",
        "meta": {
            "analysis_version": "outcome-v1",
            "registry_version": "feature-registry-v1",
            "engine_sha256": ENGINE_SHA256,
            "registry_sha256": REGISTRY_SHA256,
            "input_fingerprint": input_fingerprint,
            "generated_at": "2026-07-23T10:00:01+00:00",
            "timezone": "Europe/Paris",
            "requested_range": {
                "kind": "bounded", "from": "2026-01-01", "to": "2026-07-22",
            },
            "analysis_range": {
                "kind": "bounded", "from": "2026-01-01", "to": "2026-07-22",
            },
            "baseline_range": {
                "kind": "bounded", "from": "2025-12-31", "to": "2026-07-22",
            },
            "outcome": "subjective.day_rating",
            "modes": ["green-vs-non-green"],
            "min_n": 30,
            "interactions": "none",
            "top": 100,
            "candidate_family_sizes": {"green-vs-non-green": 0},
            "interaction_family_sizes": {},
        },
        "coverage": {
            "outcome_eligible_n": outcome_eligible_n,
            "modes": {
                "green-vs-non-green": {
                    "outcome_eligible_n": outcome_eligible_n,
                    "generated_candidates": (
                        1 if audit_finding is not None else 0
                    ),
                    "tested_candidates": 0,
                    "insufficient_candidates": (
                        1 if audit_finding is not None else 0
                    ),
                    "family_size": 0,
                },
            },
            "source_manifests": [],
            "dependencies": {},
        },
        "readiness": {
            "ok": True,
            "meta": {
                "readiness_version": "data-readiness-v1",
                "registry_version": "feature-registry-v1",
                "registry_sha256": REGISTRY_SHA256,
                "timezone": "Europe/Paris",
                "range": {
                    "kind": "bounded",
                    "from": "2026-01-01",
                    "to": "2026-07-22",
                },
                "goal": None,
                "outcome": "subjective.day_rating",
                "feature_count": 0,
                "state_counts": {},
                "predicate_order": [],
            },
            "features": [],
        },
        "findings": [audit_finding] if audit_finding is not None else [],
        "suppression_counts": {},
        "warnings": [],
    }
    result_json = canonical_json(result)
    result_sha256 = sha256_id({
        "contract_version": LEDGER_CONTRACT_VERSION,
        "kind": "analysis_result",
        "result": result,
    })
    connection.execute(
        """
        INSERT INTO analysis_runs(
          run_id,batch_id,range_id,outcome_key,outcome_mode,range_resolution,
          analysis_from,analysis_to,baseline_from,baseline_to,input_fingerprint,
          status,status_reason_code,result_json,result_sha256,started_at,completed_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id, batch_id, range_id, "subjective.day_rating",
            "green-vs-non-green", "bounded_exact", "2026-01-01", "2026-07-22",
            "2025-12-31", "2026-07-22", input_fingerprint,
            "insufficient_data",
            (
                "no_testable_candidates"
                if audit_finding is not None
                else "insufficient_outcome_data"
            ),
            result_json,
            result_sha256, "2026-07-23T10:00:00+00:00",
            "2026-07-23T10:00:01+00:00",
        ),
    )
    if audit_finding is not None:
        connection.execute(
            """
            INSERT INTO analysis_findings(
              finding_id,run_id,candidate_key,candidate_kind,outcome_key,
              outcome_mode,direction,quality_tier,eligible_for_hypothesis,
              evidence_json,evidence_for_json,evidence_against_json,
              evidence_fingerprint
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                audit_finding["finding_id"],
                run_id,
                audit_finding["candidate_key"],
                "single",
                "subjective.day_rating",
                "green-vs-non-green",
                "unknown",
                "insufficient",
                0,
                canonical_json(audit_finding),
                canonical_json(audit_finding["evidence_for"]),
                canonical_json(audit_finding["evidence_against"]),
                audit_fingerprint,
            ),
        )
        audit_component = audit_finding["exposure"]["components"][0]
        connection.execute(
            """
            INSERT INTO analysis_finding_components(
              finding_id,position,exposure_key,lag_days,window_days,transform,
              temporal_direction
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                audit_finding["finding_id"],
                1,
                audit_component["exposure_key"],
                audit_component["lag_days"],
                audit_component["window_days"],
                audit_component["transform"],
                audit_component["temporal_direction"],
            ),
        )
    evidence_fingerprint = sha256_id({
        "contract_version": LEDGER_CONTRACT_VERSION,
        "kind": "dormant_evidence",
        "hypothesis_id": HYPOTHESIS_ID,
        "run_id": run_id,
        "input_fingerprint": input_fingerprint,
        "reason": evidence_class,
        "analysis_version": "outcome-v1",
        "registry_version": "feature-registry-v1",
        "engine_sha256": ENGINE_SHA256,
        "registry_sha256": REGISTRY_SHA256,
        "range_from": "2026-01-01",
        "range_to": "2026-07-22",
        "audit_finding_evidence_fingerprint": audit_fingerprint,
    })
    provenance = {
        "analysis_version": "outcome-v1",
        "registry_version": "feature-registry-v1",
        "engine_sha256": ENGINE_SHA256,
        "registry_sha256": REGISTRY_SHA256,
        "input_fingerprint": input_fingerprint,
    }
    effect_summary = (
        {
            "effect": audit_finding["effect"],
            "rates": audit_finding["rates"],
            "testing": audit_finding["testing"],
            "quality": audit_finding["quality"],
            "direction": "unknown",
            "provenance": provenance,
        }
        if audit_finding is not None
        else {
            "effect": None,
            "rates": None,
            "testing": None,
            "quality": None,
            "direction": "unknown",
            "provenance": provenance,
        }
    )
    evaluation_confounders = (
        audit_finding["confounders"]
        if audit_finding is not None
        else {
            "checked": [],
            "unchecked": [],
            "sensitive_to": [],
            "weighted_effect": None,
        }
    )
    evaluation_sample = (
        audit_finding["sample"]
        if audit_finding is not None
        else {
            "eligible_n": 0,
            "complete_n": 0,
            "missing_n": 0,
            "reason": evidence_class,
        }
    )
    evaluation_stability = (
        audit_finding["stability"]
        if audit_finding is not None
        else {"status": "not_evaluated"}
    )
    evaluation_id = 2
    connection.execute(
        """
        INSERT INTO hypothesis_evaluations(
          id,hypothesis_id,run_id,finding_id,tested_at,range_from,range_to,
          evidence_class,previous_status,evidence_for_json,evidence_against_json,
          confounders_json,sample_size_json,effect_summary_json,stability_json,
          confidence,status,change_reason,change_conditions,
          source_analysis_version,input_fingerprint,evidence_fingerprint,
          previous_evaluation_id,comparison_evaluation_id,new_eligible_observations,
          compatible_with_prior,transition_applied,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            evaluation_id, HYPOTHESIS_ID, run_id, None,
            "2026-07-23T10:00:01+00:00", "2026-01-01", "2026-07-22",
            evidence_class, "candidate", "[]", "[]",
            canonical_json(evaluation_confounders),
            canonical_json(evaluation_sample),
            canonical_json(effect_summary),
            canonical_json(evaluation_stability),
            "insufficient", "dormant", evidence_class,
            canonical_json({"reason": evidence_class}), "outcome-v1",
            input_fingerprint, evidence_fingerprint, 1, None, 0, 1, 1,
            "2026-07-23T10:00:01+00:00",
        ),
    )
    return batch_id, run_id, evaluation_id, evidence_fingerprint


def _payload_for_refs(
    connection: sqlite3.Connection,
    *,
    synthesis_id: str,
    batch_id: str,
    run_refs: list[dict],
    finding_refs: list[dict],
    hypothesis_refs: list[dict],
    cutoff_date: str = "2026-07-22",
) -> dict:
    return {
        "synthesis_id": synthesis_id,
        "analysis_batch_id": batch_id,
        "cadence": "manual",
        "reason_code": "integration_review",
        "cutoff_date": cutoff_date,
        "evidence_fingerprint": synthesis_evidence_fingerprint(
            connection,
            analysis_batch_id=batch_id,
            run_refs=run_refs,
            finding_refs=finding_refs,
            hypothesis_refs=hypothesis_refs,
        ),
        "context_version": SYNTHESIS_CONTEXT_VERSION,
        "prompt_sha256": PROMPT_SHA256,
        "model_id": LEGACY_MODEL_ID,
        "provider": LEGACY_PROVIDER,
        "run_refs": run_refs,
        "finding_refs": finding_refs,
        "hypothesis_refs": hypothesis_refs,
        "narrative_md": "Interpretation remains separate from the evidence tables.",
        "rendered_md": None,
        "status": "completed",
        "no_message_reason_code": None,
        "annotations": [],
        "notification": None,
    }


def _annotation(
    payload: dict,
    *,
    content: str = "A meal-routine marker is an alternative.",
    evaluation_id: int | None = 1,
    source: str = "glm_synthesis",
    supersedes_id: str | None = None,
) -> dict:
    value = {
        "annotation_id": "",
        "hypothesis_id": HYPOTHESIS_ID,
        "evaluation_id": evaluation_id,
        "annotation_kind": "alternative",
        "content": content,
        "source": source,
        "synthesis_id": payload["synthesis_id"],
        "context_version": payload["context_version"],
        "prompt_sha256": payload["prompt_sha256"],
        "model_id": payload["model_id"],
        "provider": payload["provider"],
        "supersedes_id": supersedes_id,
        "input_sha256": "",
    }
    identity = canonical_annotation_identity(
        hypothesis_id=value["hypothesis_id"],
        evaluation_id=value["evaluation_id"],
        annotation_kind=value["annotation_kind"],
        content=value["content"],
        source=value["source"],
        synthesis_id=value["synthesis_id"],
        context_version=value["context_version"],
        prompt_sha256=value["prompt_sha256"],
        model_id=value["model_id"],
        provider=value["provider"],
        supersedes_id=value["supersedes_id"],
    )
    value.update(identity)
    assert value["input_sha256"] == annotation_input_sha256(
        synthesis_id=value["synthesis_id"],
        evidence_fingerprint=payload["evidence_fingerprint"],
        context_version=value["context_version"],
        prompt_sha256=value["prompt_sha256"],
        hypothesis_id=value["hypothesis_id"],
        evaluation_id=value["evaluation_id"],
        annotation_kind=value["annotation_kind"],
        content=value["content"],
        source=value["source"],
        model_id=value["model_id"],
        provider=value["provider"],
        supersedes_id=value["supersedes_id"],
    )
    return value


def test_synthesis_json_rejects_duplicate_keys_at_every_object_depth():
    with pytest.raises(SynthesisError, match="duplicate JSON key.*synthesis_id"):
        parse_synthesis_json(
            '{"synthesis_id":"sha256:'
            + "a" * 64
            + '","synthesis_id":"sha256:'
            + "b" * 64
            + '"}'
        )
    with pytest.raises(SynthesisError, match="duplicate JSON key.*run_id"):
        parse_synthesis_json(
            '{"run_refs":[{"run_id":"sha256:'
            + "a" * 64
            + '","run_id":"sha256:'
            + "b" * 64
            + '"}]}'
        )
    with pytest.raises(SynthesisError, match="numeric literal exceeds"):
        parse_synthesis_json(
            '{"hypothesis_refs":[{"evaluation_id":' + "9" * 5000 + "}]}"
        )
    with pytest.raises(SynthesisError, match="valid UTF-8"):
        parse_synthesis_json('{"narrative_md":"\\ud800"}')
    deeply_nested = '{"unknown":' + "[" * 200 + "0" + "]" * 200 + "}"
    with pytest.raises(SynthesisError, match="nesting exceeds"):
        parse_synthesis_json(deeply_nested)


def test_synthesis_resolves_reused_finding_from_authenticated_later_run(
    conn: sqlite3.Connection,
):
    batch_id, run_id = _insert_second_batch_with_reused_finding(conn)
    run_refs = [{"run_id": run_id, "purpose": "primary"}]
    finding_refs = [{"finding_id": FINDING_ID, "role": "primary"}]
    payload = _payload_for_refs(
        conn,
        synthesis_id="sha256:" + "7" * 64,
        batch_id=batch_id,
        run_refs=run_refs,
        finding_refs=finding_refs,
        hypothesis_refs=[],
    )
    result = record_synthesis(conn, payload, now="2026-07-23T11:00:00+00:00")
    assert result["created"] is True
    stored = read_synthesis(conn, payload["synthesis_id"])
    assert stored["finding_ids"] == [FINDING_ID]
    assert conn.execute(
        "SELECT run_id FROM analysis_findings WHERE finding_id=?",
        (FINDING_ID,),
    ).fetchone()[0] == RUN_ID

    result_json = json.loads(conn.execute(
        "SELECT result_json FROM analysis_runs WHERE run_id=?", (run_id,),
    ).fetchone()[0])
    result_json["findings"] = []
    encoded = canonical_json(result_json)
    digest = sha256_id({
        "contract_version": LEDGER_CONTRACT_VERSION,
        "kind": "analysis_result",
        "result": result_json,
    })
    conn.execute(
        "UPDATE analysis_runs SET result_json=?,result_sha256=? WHERE run_id=?",
        (encoded, digest, run_id),
    )
    with pytest.raises(SynthesisError, match="absent from every referenced run"):
        read_synthesis(conn, payload["synthesis_id"])


def test_hypothesis_created_from_reused_finding_uses_membership_not_origin(
    conn: sqlite3.Connection,
):
    batch_id, run_id = _insert_second_batch_with_reused_finding(conn)
    trigger_rows = conn.execute(
        """SELECT name,sql FROM sqlite_master
             WHERE type='trigger' AND name IN (
               'hypotheses_no_update','hypothesis_evaluations_no_update'
             ) ORDER BY name"""
    ).fetchall()
    for row in trigger_rows:
        conn.execute(f'DROP TRIGGER "{row["name"]}"')
    conn.execute(
        "UPDATE hypotheses SET created_by_run=?,created_at=? WHERE hypothesis_id=?",
        (run_id, "2026-07-23T09:00:02+00:00", HYPOTHESIS_ID),
    )
    conn.execute(
        """UPDATE hypothesis_evaluations
              SET run_id=?,tested_at=?,created_at=?
            WHERE id=? AND hypothesis_id=?""",
        (
            run_id, "2026-07-23T09:00:01+00:00",
            "2026-07-23T09:00:02+00:00", 1, HYPOTHESIS_ID,
        ),
    )
    for row in trigger_rows:
        conn.execute(row["sql"])

    run_refs = [{"run_id": run_id, "purpose": "primary"}]
    finding_refs = [{"finding_id": FINDING_ID, "role": "primary"}]
    hypothesis_refs = [{
        "hypothesis_id": HYPOTHESIS_ID,
        "evaluation_id": 1,
        "role": "changed",
    }]
    payload = _payload_for_refs(
        conn,
        synthesis_id="sha256:" + "f" * 64,
        batch_id=batch_id,
        run_refs=run_refs,
        finding_refs=finding_refs,
        hypothesis_refs=hypothesis_refs,
    )
    result = record_synthesis(conn, payload, now="2026-07-23T11:30:00+00:00")
    assert result["created"] is True
    assert read_synthesis(conn, payload["synthesis_id"])["hypothesis_ids"] == [
        HYPOTHESIS_ID
    ]


@pytest.mark.parametrize(
    "evidence_class",
    ("dormant_no_eligible_data", "dormant_stale_prerequisite"),
)
def test_synthesis_validates_full_dormant_provenance_and_range_fingerprint(
    conn: sqlite3.Connection,
    evidence_class: str,
):
    batch_id, run_id, evaluation_id, dormant_fingerprint = _insert_dormant_batch(
        conn, evidence_class,
    )
    run_refs = [{"run_id": run_id, "purpose": "primary"}]
    hypothesis_refs = [{
        "hypothesis_id": HYPOTHESIS_ID,
        "evaluation_id": evaluation_id,
        "role": "changed",
    }]
    payload = _payload_for_refs(
        conn,
        synthesis_id=(
            "sha256:" + ("7" if evidence_class.endswith("data") else "f") * 64
        ),
        batch_id=batch_id,
        run_refs=run_refs,
        finding_refs=[],
        hypothesis_refs=hypothesis_refs,
    )
    result = record_synthesis(conn, payload, now="2026-07-23T12:00:00+00:00")
    assert result["created"] is True
    stored = read_synthesis(conn, payload["synthesis_id"])
    assert stored["hypothesis_ids"] == [HYPOTHESIS_ID]
    evaluation = conn.execute(
        "SELECT effect_summary_json,evidence_fingerprint FROM hypothesis_evaluations "
        "WHERE id=?",
        (evaluation_id,),
    ).fetchone()
    assert evaluation["evidence_fingerprint"] == dormant_fingerprint
    assert set(json.loads(evaluation["effect_summary_json"])["provenance"]) == {
        "analysis_version",
        "registry_version",
        "engine_sha256",
        "registry_sha256",
        "input_fingerprint",
    }


def test_synthesis_validates_dormant_insufficient_audit_finding_material(
    conn: sqlite3.Connection,
):
    batch_id, run_id, evaluation_id, dormant_fingerprint = _insert_dormant_batch(
        conn,
        "dormant_stale_prerequisite",
        include_audit_finding=True,
    )
    run_refs = [{"run_id": run_id, "purpose": "primary"}]
    hypothesis_refs = [{
        "hypothesis_id": HYPOTHESIS_ID,
        "evaluation_id": evaluation_id,
        "role": "changed",
    }]
    payload = _payload_for_refs(
        conn,
        synthesis_id="sha256:" + "4" * 64,
        batch_id=batch_id,
        run_refs=run_refs,
        finding_refs=[],
        hypothesis_refs=hypothesis_refs,
    )
    assert record_synthesis(
        conn, payload, now="2026-07-23T12:30:00+00:00"
    )["created"] is True
    stored = read_synthesis(conn, payload["synthesis_id"])
    assert stored["hypothesis_ids"] == [HYPOTHESIS_ID]
    evaluation = conn.execute(
        """SELECT sample_size_json,effect_summary_json,evidence_fingerprint
             FROM hypothesis_evaluations WHERE id=?""",
        (evaluation_id,),
    ).fetchone()
    assert evaluation["evidence_fingerprint"] == dormant_fingerprint
    assert json.loads(evaluation["sample_size_json"])["eligible_n"] == 20
    summary = json.loads(evaluation["effect_summary_json"])
    assert summary["effect"]["method"] is None
    assert summary["quality"] == {
        "eligible_for_hypothesis": False,
        "tier": "insufficient",
    }
    assert set(summary["provenance"]) == {
        "analysis_version",
        "registry_version",
        "engine_sha256",
        "registry_sha256",
        "input_fingerprint",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(effect={"estimate": 99}), "unknown"),
        (lambda value: value.update(sample_size={"n": 999}), "unknown"),
        (lambda value: value.update(confidence="high"), "unknown"),
        (lambda value: value.update(hypothesis_status="replicated"), "unknown"),
        (lambda value: value.update(rendered_md="| Injected | 99 |"), "deterministic"),
        (lambda value: value.update(context_version="1"), "approved version"),
        (
            lambda value: value["finding_refs"][0].update(effect=1.0),
            "unknown finding reference",
        ),
    ],
)
def test_closed_model_boundary_rejects_numeric_state_and_render_attempts(
    conn: sqlite3.Connection,
    mutation,
    message: str,
):
    payload = _payload(conn)
    mutation(payload)
    with pytest.raises(SynthesisError, match=message):
        validate_synthesis_record(payload)
    assert conn.execute("SELECT COUNT(*) FROM synthesis_runs").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("model_id", "provider"),
    [
        ("gpt-5.6-sol", "openai-codex"),
        ("claude-sonnet-4.5", "anthropic"),
    ],
)
def test_provider_neutral_external_hermes_synthesis_records_and_reads(
    conn: sqlite3.Connection,
    model_id: str,
    provider: str,
):
    payload = _payload(conn)
    payload.update(model_id=model_id, provider=provider)
    payload["annotations"] = [
        _annotation(payload, source="hermes_synthesis")
    ]

    result = record_synthesis(conn, payload, now="2026-07-23T09:00:00+00:00")
    assert result["created"] is True
    stored = read_synthesis(conn, payload["synthesis_id"])
    assert stored["model_id"] == model_id
    assert stored["provider"] == provider
    assert stored["annotations"][0]["source"] == "hermes_synthesis"
    assert stored["evidence_fingerprint"] == payload["evidence_fingerprint"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model_id", " model"),
        ("model_id", "model with spaces"),
        ("provider", "provider@host"),
        ("provider", ""),
    ],
)
def test_provider_neutral_provenance_identifiers_remain_canonical(
    conn: sqlite3.Connection,
    field: str,
    value: str,
):
    payload = _payload(conn)
    payload[field] = value
    with pytest.raises(
        SynthesisError,
        match="canonical external model identifier|must not be blank",
    ):
        validate_synthesis_record(payload)


def test_provider_neutral_provenance_remains_all_or_none(
    conn: sqlite3.Connection,
):
    payload = _payload(conn)
    payload["provider"] = None
    with pytest.raises(SynthesisError, match="all set or all null"):
        validate_synthesis_record(payload)


def test_nested_annotation_is_closed_bounded_and_hash_verified(
    conn: sqlite3.Connection,
):
    payload = _payload(conn)
    annotation = _annotation(payload)
    payload["annotations"] = [annotation]
    record_synthesis(conn, payload, now="2026-07-23T09:00:00+00:00")
    stored = dict(conn.execute(
        "SELECT * FROM hypothesis_annotations WHERE annotation_id=?",
        (annotation["annotation_id"],),
    ).fetchone())
    assert stored["content"] == annotation["content"]
    assert stored["source"] == "glm_synthesis"
    assert stored["model_id"] == LEGACY_MODEL_ID
    assert stored["provider"] == LEGACY_PROVIDER

    for change, match in (
        ({"confidence": "high"}, "unknown annotation"),
        ({"status": "replicated"}, "unknown annotation"),
        ({"effect": 0.9}, "unknown annotation"),
    ):
        other = _payload(conn, synthesis_id="sha256:" + "f" * 64)
        bad = _annotation(other)
        bad.update(change)
        other["annotations"] = [bad]
        with pytest.raises(SynthesisError, match=match):
            validate_synthesis_record(other)

    fresh = _payload(conn, synthesis_id="sha256:" + "e" * 64)
    bad_hash = _annotation(fresh)
    bad_hash["input_sha256"] = "sha256:" + "0" * 64
    fresh["annotations"] = [bad_hash]
    with pytest.raises(SynthesisError, match="input_sha256"):
        validate_synthesis_record(fresh)

    oversized = _payload(conn, synthesis_id="sha256:" + "d" * 64)
    too_long = _annotation(oversized, content="x" * 1001)
    oversized["annotations"] = [too_long]
    with pytest.raises(SynthesisError, match="exceeds 1000"):
        validate_synthesis_record(oversized)


def test_refs_are_normalized_and_evidence_fingerprint_is_canonical(
    conn: sqlite3.Connection,
):
    payload = _payload(conn)
    payload["finding_refs"] = [
        {"finding_id": FINDING_ID, "role": "supporting"},
        {"finding_id": FINDING_ID, "role": "primary"},
    ]
    payload["evidence_fingerprint"] = synthesis_evidence_fingerprint(
        conn,
        analysis_batch_id=BATCH_ID,
        run_refs=payload["run_refs"],
        finding_refs=payload["finding_refs"],
        hypothesis_refs=payload["hypothesis_refs"],
    )
    normalized = validate_synthesis_record(payload)
    assert [item["role"] for item in normalized["finding_refs"]] == [
        "primary", "supporting",
    ]
    result = record_synthesis(conn, payload)
    stored = conn.execute(
        "SELECT finding_ids_json,hypothesis_ids_json FROM synthesis_runs",
    ).fetchone()
    assert stored["finding_ids_json"] == canonical_json([FINDING_ID])
    assert stored["hypothesis_ids_json"] == canonical_json([HYPOTHESIS_ID])
    assert result["evidence_fingerprint"] == payload["evidence_fingerprint"]


def test_deterministic_markdown_numbers_match_stored_evidence_json(
    conn: sqlite3.Connection,
):
    payload = _payload(conn)
    recorded = record_synthesis(
        conn, payload, now="2026-07-23T09:00:00+00:00",
    )
    brief = render_synthesis_markdown(conn, SYNTHESIS_ID)
    assert "| 180 | 18 | 162 | 79 | 101 | risk_difference | 0.191 | [0.031, 0.351] | 0.021 | 0.087 | stable |" in brief
    assert "| 0.439 | 0.611 | 0.42 | 0.191 | 1.46 |" in brief
    assert "effect_gate_pass=0.191 (gate 0.1)" in brief
    assert '"risk_ratio":1.45512345679' in brief
    assert '"estimate":0.191' in brief
    assert '"complete_n":180' in brief
    assert recorded["rendered_sha256"] == "sha256:" + hashlib.sha256(
        brief.encode("utf-8")
    ).hexdigest()
    loaded = read_synthesis(conn, SYNTHESIS_ID)
    assert loaded["rendered_md"] == brief
    assert loaded["narrative_md"] == payload["narrative_md"]
    assert synthesis_history(conn, limit=1)["syntheses"][0]["rendered_md"] == brief


def test_pair_markdown_renders_both_component_rates_and_incremental_effect(
    conn: sqlite3.Connection,
):
    batch_id, run_id, pair = _insert_pair_batch(conn)
    synthesis_id = "sha256:" + "9" * 64
    payload = _payload_for_refs(
        conn,
        synthesis_id=synthesis_id,
        batch_id=batch_id,
        run_refs=[{"run_id": run_id, "purpose": "primary"}],
        finding_refs=[{
            "finding_id": pair["finding_id"],
            "role": "primary",
        }],
        hypothesis_refs=[],
    )
    record_synthesis(conn, payload)
    rendered = render_synthesis_markdown(conn, synthesis_id)
    assert "### Interaction component rates" in rendered
    assert (
        "| 40 | 45 | 45 | 50 | 0.4 | 0.5 | 0.55 | 0.8 | 0.1 | 0.15 | "
        "0.25 | 0.1 | 0.15 | 0.25 |"
    ) in rendered
    for key in (
        "component_a_difference",
        "component_b_difference",
        "incremental_risk_difference",
        "oriented_component_a_difference",
        "oriented_component_b_difference",
        "oriented_incremental_risk_difference",
    ):
        assert str(pair["rates"][key]) in rendered


def test_model_narrative_remains_separate_from_official_tables(
    conn: sqlite3.Connection,
):
    narrative = (
        "### My unofficial table\n\n"
        "| Claim | Value |\n|---|---:|\n| fabricated | 999 |\n\n"
        "This remains interpretation, not evidence."
    )
    payload = _payload(conn, narrative=narrative)
    record_synthesis(conn, payload)
    loaded = read_synthesis(conn, SYNTHESIS_ID)
    brief = loaded["rendered_md"]
    marker = "## Hermes interpretation (model-generated; not numerical authority)"
    deterministic, interpreted = brief.split(marker, 1)
    assert "Complete n" in deterministic
    assert "fabricated" not in deterministic
    assert "fabricated | 999" in interpreted
    assert loaded["narrative_md"] == narrative
    stored = conn.execute(
        "SELECT narrative_md,rendered_md FROM synthesis_runs WHERE synthesis_id=?",
        (SYNTHESIS_ID,),
    ).fetchone()
    assert stored["narrative_md"] == narrative
    assert stored["rendered_md"] == brief


def test_no_message_status_is_valid_without_model_or_pattern_file(
    conn: sqlite3.Connection,
    tmp_path: Path,
):
    payload = _payload(
        conn,
        narrative=None,
        status="insufficient_data",
        no_message_reason="aligned_n_below_gate",
    )
    result = record_synthesis(conn, payload)
    assert result["message_eligible"] is False
    assert result["no_message_reason_code"] == "aligned_n_below_gate"
    loaded = read_synthesis(conn, SYNTHESIS_ID)
    assert loaded["rendered_md"] is None
    with pytest.raises(SynthesisError, match="no pattern brief"):
        write_synthesis_markdown(
            conn, SYNTHESIS_ID, vault_root=(tmp_path / "vault").resolve(),
        )

    invalid = _payload(
        conn,
        synthesis_id="sha256:" + "c" * 64,
        narrative=None,
        status="no_novelty",
        no_message_reason=None,
    )
    with pytest.raises(SynthesisError, match="requires no_message_reason_code"):
        validate_synthesis_record(invalid)


def test_pattern_brief_uses_explicit_temp_vault_and_never_overwrites(
    conn: sqlite3.Connection,
    tmp_path: Path,
):
    record_synthesis(conn, _payload(conn))
    vault = (tmp_path / "isolated-vault").resolve()
    target = write_synthesis_markdown(
        conn, SYNTHESIS_ID, vault_root=vault,
    )
    assert target == (
        vault
        / "personal"
        / "patterns"
        / f"autonomous-insights-2026-07-22-{SYNTHESIS_ID}.md"
    )
    original = target.read_bytes()
    assert original == render_synthesis_markdown(conn, SYNTHESIS_ID).encode("utf-8")
    assert target.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.rglob("*.md")) == [target]

    assert write_synthesis_markdown(
        conn, SYNTHESIS_ID, vault_root=vault,
    ) == target
    assert target.read_bytes() == original
    assert not list(target.parent.glob(".*.tmp-*"))


def test_synthesis_id_is_append_only_and_stale_evidence_is_rejected(
    conn: sqlite3.Connection,
):
    payload = _payload(conn)
    first = record_synthesis(conn, payload)
    second = record_synthesis(conn, payload)
    assert first["created"] is True
    assert second["created"] is False
    assert conn.execute("SELECT COUNT(*) FROM synthesis_runs").fetchone()[0] == 1

    stale = _payload(conn, synthesis_id="sha256:" + "b" * 64)
    stale["evidence_fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(SynthesisError, match="does not match"):
        record_synthesis(conn, stale)
    assert conn.execute("SELECT COUNT(*) FROM synthesis_runs").fetchone()[0] == 1


def test_historical_synthesis_is_pinned_before_future_evidence_appends(
    conn: sqlite3.Connection,
):
    record_synthesis(conn, _payload(conn))
    before = read_synthesis(conn, SYNTHESIS_ID)
    before_rendered = render_synthesis_markdown(conn, SYNTHESIS_ID)
    before_fingerprint = before["evidence_fingerprint"]

    evaluation_id, evidence_item_id = _append_followup_evaluation_with_evidence(
        conn,
    )
    assert evaluation_id == 2
    assert conn.execute(
        """SELECT COUNT(*) FROM hypothesis_evidence_items
           WHERE evidence_item_id=?""",
        (evidence_item_id,),
    ).fetchone()[0] == 1

    after = read_synthesis(conn, SYNTHESIS_ID)
    assert after == before
    assert after["evidence_fingerprint"] == before_fingerprint
    assert render_synthesis_markdown(conn, SYNTHESIS_ID) == before_rendered
    assert synthesis_history(conn, limit=1)["syntheses"][0] == before


def test_read_detects_renderer_or_structured_evidence_tampering(
    conn: sqlite3.Connection,
):
    record_synthesis(conn, _payload(conn))
    conn.execute(
        "UPDATE synthesis_runs SET rendered_md='tampered' WHERE synthesis_id=?",
        (SYNTHESIS_ID,),
    )
    with pytest.raises(SynthesisError, match="rendered Markdown drift"):
        read_synthesis(conn, SYNTHESIS_ID)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(synthesis_id="synthesis:" + "1" * 64),
        lambda value: value.update(analysis_batch_id="batch:" + "1" * 64),
        lambda value: value["run_refs"][0].update(run_id="run:" + "1" * 64),
        lambda value: value["finding_refs"][0].update(
            finding_id="finding:" + "1" * 64,
        ),
        lambda value: value["hypothesis_refs"][0].update(
            hypothesis_id="hypothesis:" + "1" * 64,
        ),
    ],
)
def test_every_reference_id_requires_canonical_prefixed_sha256(
    conn: sqlite3.Connection,
    mutation,
):
    payload = _payload(conn)
    mutation(payload)
    with pytest.raises(SynthesisError, match="canonical sha256"):
        validate_synthesis_record(payload)


def test_annotation_id_supersedes_and_cursor_are_canonical_sha256(
    conn: sqlite3.Connection,
):
    payload = _payload(conn)
    annotation = _annotation(payload)
    annotation["annotation_id"] = "annotation:" + "1" * 64
    payload["annotations"] = [annotation]
    with pytest.raises(SynthesisError, match="canonical sha256"):
        validate_synthesis_record(payload)

    record_synthesis(conn, _payload(conn))
    with pytest.raises(SynthesisError, match="canonical sha256"):
        synthesis_history(conn, before="synthesis:" + "1" * 64)


def test_annotation_requires_the_exact_referenced_evaluation(
    conn: sqlite3.Connection,
):
    payload = _payload(conn)
    payload["annotations"] = [_annotation(payload, evaluation_id=None)]
    with pytest.raises(SynthesisError, match="exactly appear"):
        validate_synthesis_record(payload)

    payload = _payload(conn)
    payload["annotations"] = [_annotation(payload, evaluation_id=2)]
    with pytest.raises(SynthesisError, match="exactly appear"):
        validate_synthesis_record(payload)

    payload = _payload(conn)
    payload["annotations"] = [_annotation(payload, evaluation_id=10**100)]
    with pytest.raises(SynthesisError, match="positive integer"):
        validate_synthesis_record(payload)


def test_ledger_glm_writer_cannot_append_forms_that_break_synthesis_reads(
    conn: sqlite3.Connection,
):
    payload = _payload(conn)
    record_synthesis(conn, payload)
    before = read_synthesis(conn, SYNTHESIS_ID)
    common = {
        "hypothesis_id": HYPOTHESIS_ID,
        "content": "Bounded interpretation only.",
        "source": "glm_synthesis",
        "synthesis_id": SYNTHESIS_ID,
        "context_version": SYNTHESIS_CONTEXT_VERSION,
        "prompt_sha256": payload["prompt_sha256"],
        "model_id": LEGACY_MODEL_ID,
        "provider": LEGACY_PROVIDER,
        "created_at": "2026-07-23T09:00:01+00:00",
    }
    with pytest.raises(LedgerError) as missing:
        append_annotation(
            conn,
            annotation_kind="alternative",
            evaluation_id=None,
            **common,
        )
    assert getattr(missing.value, "code", None) == "mismatched_synthesis_reference"
    with pytest.raises(LedgerError) as owner_note:
        append_annotation(
            conn,
            annotation_kind="owner_note",
            evaluation_id=1,
            **common,
        )
    assert getattr(owner_note.value, "code", None) == "validation_error"
    assert read_synthesis(conn, SYNTHESIS_ID) == before


@pytest.mark.parametrize(
    ("tamper", "match"),
    [
        (
            lambda connection: connection.execute(
                "UPDATE analysis_runs SET result_sha256=? WHERE run_id=?",
                ("sha256:" + "0" * 64, RUN_ID),
            ),
            "result hash drift",
        ),
        (
            lambda connection: connection.execute(
                "UPDATE analysis_finding_components SET window_days=3 "
                "WHERE finding_id=? AND position=1",
                (FINDING_ID,),
            ),
            "component ancestry drift",
        ),
        (
            lambda connection: connection.execute(
                "UPDATE analysis_batches SET engine_sha256=? WHERE batch_id=?",
                ("sha256:" + "0" * 64, BATCH_ID),
            ),
            "engine_sha256 ancestry drift",
        ),
        (
            lambda connection: connection.execute(
                "UPDATE analysis_findings SET direction='negative' WHERE finding_id=?",
                (FINDING_ID,),
            ),
            "direction/effect drift",
        ),
        (
            lambda connection: connection.execute(
                "UPDATE analysis_batches SET completed_count=0 WHERE batch_id=?",
                (BATCH_ID,),
            ),
            "terminal counts drift",
        ),
    ],
)
def test_run_result_finding_provenance_and_components_fail_closed(
    conn: sqlite3.Connection,
    tamper,
    match: str,
):
    tamper(conn)
    with pytest.raises(SynthesisError, match=match):
        _payload(conn)


def test_noncanonical_result_json_fails_closed(
    conn: sqlite3.Connection,
):
    row = conn.execute(
        "SELECT result_json FROM analysis_runs WHERE run_id=?", (RUN_ID,),
    ).fetchone()
    conn.execute(
        "UPDATE analysis_runs SET result_json=? WHERE run_id=?",
        (" " + row["result_json"], RUN_ID),
    )
    with pytest.raises(SynthesisError, match="not canonical JSON"):
        _payload(conn)


def test_finding_provenance_tamper_fails_closed(
    conn: sqlite3.Connection,
):
    row = conn.execute(
        "SELECT evidence_json FROM analysis_findings WHERE finding_id=?",
        (FINDING_ID,),
    ).fetchone()
    evidence = json.loads(row["evidence_json"])
    evidence["provenance"]["input_fingerprint"] = "sha256:" + "0" * 64
    conn.execute(
        "UPDATE analysis_findings SET evidence_json=? WHERE finding_id=?",
        (canonical_json(evidence), FINDING_ID),
    )
    with pytest.raises(SynthesisError, match="input_fingerprint ancestry drift"):
        _payload(conn)


def test_every_evaluation_field_participates_in_synthesis_fingerprint(
    conn: sqlite3.Connection,
):
    record_synthesis(conn, _payload(conn))
    trigger_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' "
        "AND name='hypothesis_evaluations_no_update'",
    ).fetchone()["sql"]
    conn.execute("DROP TRIGGER hypothesis_evaluations_no_update")
    conn.execute(
        "UPDATE hypothesis_evaluations SET change_reason=? WHERE id=1",
        ("tampered transition reason",),
    )
    conn.execute(trigger_sql)
    with pytest.raises(SynthesisError, match="evidence fingerprint drift"):
        read_synthesis(conn, SYNTHESIS_ID)


def test_immutable_hypothesis_evidence_mapping_is_verified(
    conn: sqlite3.Connection,
):
    trigger_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' "
        "AND name='hypothesis_evidence_items_no_update'",
    ).fetchone()["sql"]
    conn.execute("DROP TRIGGER hypothesis_evidence_items_no_update")
    conn.execute(
        "UPDATE hypothesis_evidence_items SET polarity='against' "
        "WHERE evidence_kind='same_direction_effect'",
    )
    conn.execute(trigger_sql)
    with pytest.raises(SynthesisError, match="polarity drift"):
        _payload(conn)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda connection, payload: connection.execute(
                "UPDATE analysis_batches SET status='running',completed_at=NULL "
                "WHERE batch_id=?",
                (BATCH_ID,),
            ),
            "non-terminal batch",
        ),
        (
            lambda connection, payload: connection.execute(
                "UPDATE analysis_runs SET status='running' WHERE run_id=?",
                (RUN_ID,),
            ),
            "running run",
        ),
        (
            lambda connection, payload: payload.update(cadence="weekly"),
            "cadence does not match",
        ),
        (
            lambda connection, payload: payload.update(cutoff_date="2026-07-21"),
            "cutoff does not match",
        ),
    ],
)
def test_terminal_batch_run_cadence_cutoff_and_range_ancestry(
    conn: sqlite3.Connection,
    mutation,
    match: str,
):
    payload = _payload(conn)
    mutation(conn, payload)
    with pytest.raises(SynthesisError, match=match):
        record_synthesis(conn, payload)


def test_manual_synthesis_rejects_direct_run_range_after_cutoff(
    conn: sqlite3.Connection,
):
    result, finding = _result_for_range(
        conn,
        label="future-direct-range",
        start="2026-07-01",
        end="2026-09-30",
    )
    batch_id, run_id = _insert_authenticated_batch(
        conn,
        label="future-direct-range",
        result=result,
        new_findings=[finding],
        anchor_date="2026-06-30",
    )
    payload = _payload_for_refs(
        conn,
        synthesis_id=sha256_id({"synthesis": "future-direct-range"}),
        batch_id=batch_id,
        run_refs=[{"run_id": run_id, "purpose": "primary"}],
        finding_refs=[{
            "finding_id": finding["finding_id"],
            "role": "primary",
        }],
        hypothesis_refs=[],
        cutoff_date="2026-06-30",
    )
    with pytest.raises(SynthesisError, match="range exceeds its cutoff"):
        record_synthesis(conn, payload)


def test_synthesis_rejects_future_hypothesis_ancestor_after_cutoff(
    conn: sqlite3.Connection,
):
    result, finding = _result_for_range(
        conn,
        label="backfill-before-future-ancestor",
        start="2026-04-01",
        end="2026-06-30",
    )
    batch_id, run_id = _insert_authenticated_batch(
        conn,
        label="backfill-before-future-ancestor",
        result=result,
        new_findings=[finding],
        anchor_date="2026-06-30",
    )
    _append_hypothesis_evaluation(
        conn,
        evaluation_id=2,
        run_id=run_id,
        finding=finding,
        previous_evaluation_id=1,
    )
    payload = _payload_for_refs(
        conn,
        synthesis_id=sha256_id({"synthesis": "future-ancestor"}),
        batch_id=batch_id,
        run_refs=[{"run_id": run_id, "purpose": "primary"}],
        finding_refs=[{
            "finding_id": finding["finding_id"],
            "role": "primary",
        }],
        hypothesis_refs=[{
            "hypothesis_id": HYPOTHESIS_ID,
            "evaluation_id": 2,
            "role": "changed",
        }],
        cutoff_date="2026-06-30",
    )
    with pytest.raises(SynthesisError, match="range exceeds its cutoff"):
        record_synthesis(conn, payload)


def test_manual_synthesis_rejects_nonprimary_range_purpose(
    conn: sqlite3.Connection,
):
    payload = _payload(conn)
    payload["run_refs"][0]["purpose"] = "recent"
    payload["evidence_fingerprint"] = synthesis_evidence_fingerprint(
        conn,
        analysis_batch_id=BATCH_ID,
        run_refs=payload["run_refs"],
        finding_refs=payload["finding_refs"],
        hypothesis_refs=payload["hypothesis_refs"],
    )
    with pytest.raises(SynthesisError, match="manual synthesis requires"):
        record_synthesis(conn, payload)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-07-23T09:00:00",
        "2026-07-23T09:00:00Z",
        "2026-07-23 09:00:00+00:00",
    ],
)
def test_record_timestamp_must_be_timezone_aware_and_canonical(
    conn: sqlite3.Connection,
    timestamp: str,
):
    with pytest.raises(SynthesisError, match="timezone|canonical"):
        record_synthesis(conn, _payload(conn), now=timestamp)
    assert conn.execute("SELECT COUNT(*) FROM synthesis_runs").fetchone()[0] == 0


def test_stored_synthesis_timestamp_tamper_fails_closed(
    conn: sqlite3.Connection,
):
    record_synthesis(conn, _payload(conn))
    conn.execute(
        "UPDATE synthesis_runs SET created_at='2026-07-23T09:00:00' "
        "WHERE synthesis_id=?",
        (SYNTHESIS_ID,),
    )
    with pytest.raises(SynthesisError, match="timezone-aware timestamp"):
        read_synthesis(conn, SYNTHESIS_ID)


def test_hypothesis_only_markdown_carries_immutable_for_and_against_evidence(
    conn: sqlite3.Connection,
):
    payload = _payload(conn)
    payload["finding_refs"] = []
    payload["evidence_fingerprint"] = synthesis_evidence_fingerprint(
        conn,
        analysis_batch_id=BATCH_ID,
        run_refs=payload["run_refs"],
        finding_refs=[],
        hypothesis_refs=payload["hypothesis_refs"],
    )
    record_synthesis(conn, payload)
    deterministic = render_synthesis_markdown(conn, SYNTHESIS_ID).split(
        "## Hermes interpretation", 1,
    )[0]
    assert "Evidence for" in deterministic
    assert "Evidence against" in deterministic
    assert "effect_gate_pass" in deterministic
    assert "confounder_attenuation" in deterministic
    assert "same_direction_effect" in deterministic
    assert "confounder_sensitivity" in deterministic


def test_exact_record_retry_is_noop_but_same_id_mismatch_fails_closed(
    conn: sqlite3.Connection,
):
    payload = _payload(conn)
    first = record_synthesis(conn, payload, now="2026-07-23T09:00:00+00:00")
    retry = record_synthesis(conn, payload, now="2026-07-24T09:00:00+00:00")
    assert first["created"] is True
    assert retry["created"] is False
    assert retry["rendered_sha256"] == first["rendered_sha256"]

    mismatch = _payload(conn, narrative="Different immutable interpretation.")
    with pytest.raises(SynthesisError, match="different immutable content"):
        record_synthesis(conn, mismatch)
    assert conn.execute("SELECT COUNT(*) FROM synthesis_runs").fetchone()[0] == 1


def test_existing_pattern_file_requires_exact_bytes_regular_type_and_mode_0600(
    conn: sqlite3.Connection,
    tmp_path: Path,
):
    record_synthesis(conn, _payload(conn))
    vault = (tmp_path / "vault").resolve()
    target = write_synthesis_markdown(conn, SYNTHESIS_ID, vault_root=vault)
    assert write_synthesis_markdown(conn, SYNTHESIS_ID, vault_root=vault) == target

    original = target.read_bytes()
    target.write_bytes(b"tampered")
    with pytest.raises(SynthesisError, match="different append-only content"):
        write_synthesis_markdown(conn, SYNTHESIS_ID, vault_root=vault)
    target.write_bytes(original)
    target.chmod(0o644)
    with pytest.raises(SynthesisError, match="mode 0600"):
        write_synthesis_markdown(conn, SYNTHESIS_ID, vault_root=vault)


def test_vault_symlink_components_and_target_cannot_escape_or_mutate(
    conn: sqlite3.Connection,
    tmp_path: Path,
):
    record_synthesis(conn, _payload(conn))
    outside = (tmp_path / "outside").resolve()
    outside.mkdir()
    vault = (tmp_path / "vault").resolve()
    vault.mkdir()
    (vault / "personal").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SynthesisError, match="symlink"):
        write_synthesis_markdown(conn, SYNTHESIS_ID, vault_root=vault)
    assert list(outside.iterdir()) == []

    safe_vault = (tmp_path / "safe-vault").resolve()
    patterns = safe_vault / "personal" / "patterns"
    patterns.mkdir(parents=True)
    outside_file = outside / "brief.md"
    outside_file.write_text("outside", encoding="utf-8")
    target = patterns / (
        f"autonomous-insights-2026-07-22-{SYNTHESIS_ID}.md"
    )
    target.symlink_to(outside_file)
    with pytest.raises(SynthesisError, match="symlink"):
        write_synthesis_markdown(conn, SYNTHESIS_ID, vault_root=safe_vault)
    assert outside_file.read_text(encoding="utf-8") == "outside"


def test_synthesis_history_uses_stable_canonical_cursor_pagination(
    conn: sqlite3.Connection,
):
    ids = [
        "sha256:" + "6" * 64,
        "sha256:" + "7" * 64,
        "sha256:" + "9" * 64,
    ]
    for hour, synthesis_id in enumerate(ids, 9):
        record_synthesis(
            conn,
            _payload(conn, synthesis_id=synthesis_id),
            now=f"2026-07-23T{hour:02d}:00:00+00:00",
        )
    first = synthesis_history(conn, limit=2)
    assert [item["synthesis_id"] for item in first["syntheses"]] == [
        ids[2], ids[1],
    ]
    assert first["next_before"] == ids[1]
    second = synthesis_history(conn, limit=2, before=first["next_before"])
    assert [item["synthesis_id"] for item in second["syntheses"]] == [ids[0]]
    assert second["next_before"] is None


def _run_synthesis_cli(database, vault, payload):
    environment = {
        **os.environ,
        "HEALTH_DB": str(database),
        "HEALTH_VAULT": str(vault),
        "HERMES_TIMEZONE": "Europe/Paris",
        "TZ": "Europe/Paris",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        [sys.executable, str(HEALTH), "synthesis-record", "--stdin"],
        input=canonical_json(payload),
        text=True,
        capture_output=True,
        env=environment,
        check=False,
        timeout=30,
    )


def test_cli_synthesis_retry_after_vault_append_failure_is_exactly_once(
    conn: sqlite3.Connection,
    tmp_path: Path,
):
    database = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    blocked_vault = tmp_path / "vault-file"
    blocked_vault.write_text("a file cannot be used as the vault root", encoding="utf-8")
    payload = _payload(conn)

    failed = _run_synthesis_cli(database, blocked_vault, payload)

    assert failed.returncode == 1
    assert json.loads(failed.stdout)["error"]["code"] == "unsafe_path"
    assert conn.execute("SELECT COUNT(*) FROM synthesis_runs").fetchone()[0] == 1

    blocked_vault.unlink()
    blocked_vault.mkdir()
    retried = _run_synthesis_cli(database, blocked_vault, payload)

    assert retried.returncode == 0
    result = json.loads(retried.stdout)
    assert result["created"] is False
    target = Path(result["markdown_path"])
    assert target.is_file()
    assert list(target.parent.glob("*.md")) == [target]
    stored = conn.execute(
        "SELECT rendered_md FROM synthesis_runs WHERE synthesis_id=?",
        (payload["synthesis_id"],),
    ).fetchone()[0]
    assert target.read_bytes() == stored.encode("utf-8")
    assert result["rendered_sha256"] == (
        "sha256:" + hashlib.sha256(stored.encode("utf-8")).hexdigest()
    )
    assert conn.execute("SELECT COUNT(*) FROM synthesis_runs").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM insight_notification_outbox"
    ).fetchone()[0] == 0


def test_cli_synthesis_precommit_validation_failure_leaves_no_record_or_file(
    conn: sqlite3.Connection,
    tmp_path: Path,
):
    database = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    vault = tmp_path / "vault"
    vault.mkdir()
    payload = _payload(conn)
    payload["notification"] = {
        "channel_class": "telegram",
        "destination_class": "owner_primary",
        "payload": {"wrong": "payload"},
        "dedupe_key": "sha256:" + "0" * 64,
        "not_before": "2026-07-23T12:00:00+00:00",
        "idempotency_mode": "none",
        "provider_idempotency_key": None,
    }

    failed = _run_synthesis_cli(database, vault, payload)

    assert failed.returncode == 2
    assert json.loads(failed.stdout)["error"]["code"] == "identity_mismatch"
    assert conn.execute("SELECT COUNT(*) FROM synthesis_runs").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM insight_notification_outbox"
    ).fetchone()[0] == 0
    assert list(vault.rglob("*")) == []


def test_phase6_completed_synthesis_enqueues_exact_outbox_atomically(
    conn: sqlite3.Connection,
):
    payload = _payload(conn)
    validated = validate_synthesis_record(payload)
    material = synthesis_module._resolve_material(
        conn,
        analysis_batch_id=validated["analysis_batch_id"],
        run_refs=validated["run_refs"],
        finding_refs=validated["finding_refs"],
        hypothesis_refs=validated["hypothesis_refs"],
    )
    rendered = synthesis_module._render_markdown(validated, material)
    evidence_fingerprints = [
        item["row"]["evidence_fingerprint"] for item in material["findings"]
    ] + [
        item["evaluation"]["evidence_fingerprint"]
        for item in material["hypotheses"]
    ]
    dedupe = orchestrator.notification_dedupe_key(
        cadence=payload["cadence"],
        evidence_fingerprints=evidence_fingerprints,
        destination_class="owner_primary",
        context_version=payload["context_version"],
    )
    payload["notification"] = {
        "channel_class": "telegram",
        "destination_class": "owner_primary",
        "payload": {
            "contract_version": orchestrator.NOTIFICATION_CONTRACT_VERSION,
            "synthesis_id": payload["synthesis_id"],
            "rendered_md": rendered,
        },
        "dedupe_key": dedupe,
        "not_before": "2026-07-23T12:00:00+00:00",
        "idempotency_mode": "none",
        "provider_idempotency_key": None,
    }
    conn.execute("BEGIN IMMEDIATE")
    result = record_synthesis(
        conn, payload, now="2026-07-23T11:00:00+00:00",
    )
    conn.commit()
    assert result["created"] is True
    outbox = dict(conn.execute(
        "SELECT * FROM insight_notification_outbox WHERE synthesis_id=?",
        (payload["synthesis_id"],),
    ).fetchone())
    assert outbox["state"] == "pending"
    assert outbox["payload_json"] == canonical_json(
        payload["notification"]["payload"]
    )
    assert conn.execute(
        "SELECT event_kind FROM insight_notification_events"
    ).fetchone()[0] == "enqueued"
    # Exact retry is a no-op for the synthesis and cannot duplicate the message.
    assert record_synthesis(
        conn, payload, now="2026-07-23T11:05:00+00:00",
    )["created"] is False
    assert conn.execute(
        "SELECT COUNT(*) FROM insight_notification_outbox"
    ).fetchone()[0] == 1


def test_phase6_outbox_failure_rolls_back_synthesis_and_normalized_refs(
    conn: sqlite3.Connection,
):
    payload = _payload(conn)
    payload["notification"] = {
        "channel_class": "telegram",
        "destination_class": "owner_primary",
        "payload": {"wrong": "payload"},
        "dedupe_key": "sha256:" + "0" * 64,
        "not_before": "2026-07-23T12:00:00+00:00",
        "idempotency_mode": "none",
        "provider_idempotency_key": None,
    }
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(SynthesisError, match="dedupe_key"):
        record_synthesis(conn, payload, now="2026-07-23T11:00:00+00:00")
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM synthesis_runs").fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM synthesis_analysis_runs"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM insight_notification_outbox"
    ).fetchone()[0] == 0
