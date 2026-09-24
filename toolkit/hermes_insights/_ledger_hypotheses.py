"""Hypothesis evaluation, annotation and read coordination.

Connections and clocks are caller-owned. Verified promotion enters through
the ledger facade; this module does not create or accept engine seals.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable, Iterable, Mapping, Sequence

from .contracts import canonical_json
from .provenance import sha256_id
from ._ledger_contracts import (
    HYPOTHESIS_STATUSES,
    LEDGER_CONTRACT_VERSION,
    SQLITE_MAX_ROWID,
    TransitionDecision,
    _ANNOTATION_KINDS,
    _EVIDENCE_KINDS,
    _MODEL_PROVENANCE_RE,
    _enum,
    _error,
    _evidence_fingerprint_for,
    _feature,
    _integer,
    _normalized_compatibility_proofs,
    _sha,
    _text,
    _timestamp,
    canonical_annotation_identity,
)
from ._ledger_store import (
    _assert_existing,
    _assert_finding_run_semantics,
    _fetch_one,
    _findings_for_run,
    _load_finding,
    _row_dict,
    assert_terminal_batch_integrity,
    finding_belongs_to_run,
    require_ledger_schema,
)
from ._ledger_transitions import (
    _evaluation_semantics,
    _finding_state,
    _is_context_only_finding,
    _run_semantics,
    _semantic_compatibility,
    _transition,
)


def _hypothesis_dedupe(finding: Mapping[str, Any]) -> str:
    try:
        parsed = json.loads(finding["candidate_key"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise _error("ledger_corrupt", "candidate key is not JSON") from exc
    if canonical_json(parsed) != finding["candidate_key"]:
        raise _error("ledger_corrupt", "candidate key is not canonical")
    return sha256_id(
        {
            "contract_version": LEDGER_CONTRACT_VERSION,
            "kind": "hypothesis",
            "candidate": parsed,
        }
    )


def _ensure_hypothesis(
    conn: sqlite3.Connection,
    *,
    run: Mapping[str, Any],
    finding: Mapping[str, Any],
    explicit: bool,
    created_at: str,
) -> tuple[dict[str, Any], bool]:
    quality = finding["quality_tier"]
    eligible = bool(finding["eligible_for_hypothesis"])
    if not eligible or quality == "insufficient":
        raise _error(
            "finding_not_eligible",
            "finding is not eligible for a hypothesis",
            validation=True,
        )
    if quality == "exploratory_screen" and not explicit:
        raise _error(
            "manual_promotion_required",
            "an exploratory screen cannot be auto-created",
            validation=True,
        )
    if quality not in {"exploratory_screen", "exploratory_unreplicated"}:
        raise _error(
            "finding_not_eligible",
            "finding quality cannot create a v1 hypothesis",
            validation=True,
        )
    dedupe_key = _hypothesis_dedupe(finding)
    hypothesis_id = sha256_id(
        {
            "contract_version": LEDGER_CONTRACT_VERSION,
            "kind": "hypothesis_id",
            "dedupe_key": dedupe_key,
        }
    )
    first_seen = run["analysis_from"] or run["analysis_to"]
    if first_seen is None:
        raise _error("ledger_invariant", "a finding cannot have a null analysis range")
    expected = {
        "hypothesis_id": hypothesis_id,
        "dedupe_key": dedupe_key,
        "outcome_key": finding["outcome_key"],
        "outcome_mode": finding["outcome_mode"],
        "candidate_kind": finding["candidate_kind"],
        "initial_direction": finding["direction"],
        "first_seen": first_seen,
        "created_at": created_at,
        "created_by_run": run["run_id"],
        "created_by_finding": finding["finding_id"],
    }
    existing = _fetch_one(
        conn, "SELECT * FROM hypotheses WHERE dedupe_key=?", (dedupe_key,)
    )
    created = existing is None
    if existing is None:
        conn.execute(
            """INSERT INTO hypotheses(
                 hypothesis_id,dedupe_key,outcome_key,outcome_mode,candidate_kind,
                 initial_direction,first_seen,created_at,created_by_run,
                 created_by_finding)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            tuple(expected.values()),
        )
        for component in finding["components"]:
            conn.execute(
                """INSERT INTO hypothesis_components(
                     hypothesis_id,position,exposure_key,lag_days,window_days,transform)
                   VALUES(?,?,?,?,?,?)""",
                (
                    hypothesis_id,
                    component["position"],
                    component["exposure_key"],
                    component["lag_days"],
                    component["window_days"],
                    component["transform"],
                ),
            )
        existing = expected
    else:
        _assert_existing(
            existing,
            expected,
            name="hypothesis",
            ignored=frozenset(
                {
                    "initial_direction",
                    "first_seen",
                    "created_at",
                    "created_by_run",
                    "created_by_finding",
                }
            ),
        )
        stored_components = [
            tuple(row)
            for row in conn.execute(
                """SELECT position,exposure_key,lag_days,window_days,transform
                   FROM hypothesis_components
                   WHERE hypothesis_id=? ORDER BY position""",
                (hypothesis_id,),
            )
        ]
        current_components = [
            (
                item["position"],
                item["exposure_key"],
                item["lag_days"],
                item["window_days"],
                item["transform"],
            )
            for item in finding["components"]
        ]
        if stored_components != current_components:
            raise _error("ledger_invariant", "hypothesis component identity drifted")
    return dict(existing), created


def _parse_canonical(text: Any, name: str) -> Any:
    if not isinstance(text, str):
        raise _error("ledger_corrupt", f"{name} is not text")
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise _error("ledger_corrupt", f"{name} is invalid JSON") from exc
    if canonical_json(value) != text:
        raise _error("ledger_corrupt", f"{name} is not canonical JSON")
    return value


def _load_evaluations(
    conn: sqlite3.Connection,
    hypothesis_id: str,
) -> list[dict[str, Any]]:
    cursor = conn.execute(
        """SELECT * FROM hypothesis_evaluations
           WHERE hypothesis_id=? ORDER BY tested_at,id""",
        (hypothesis_id,),
    )
    columns = [item[0] for item in cursor.description]
    result: list[dict[str, Any]] = []
    for raw in cursor.fetchall():
        item = _row_dict(raw, columns)
        for field in (
            "evidence_for_json",
            "evidence_against_json",
            "confounders_json",
            "sample_size_json",
            "effect_summary_json",
            "stability_json",
            "change_conditions",
        ):
            item[field.removesuffix("_json")] = _parse_canonical(
                item[field], f"evaluation.{field}"
            )
        result.append(item)
    return result


def _evaluation_payloads(
    *,
    finding: Mapping[str, Any] | None,
    audit_finding: Mapping[str, Any] | None,
    decision: TransitionDecision,
    run: Mapping[str, Any],
) -> dict[str, str]:
    if finding is None:
        empty: list[Any] = []
        if audit_finding is not None:
            evidence = audit_finding["evidence"]
            return {
                "evidence_for_json": canonical_json(empty),
                "evidence_against_json": canonical_json(empty),
                "confounders_json": canonical_json(evidence["confounders"]),
                "sample_size_json": canonical_json(evidence["sample"]),
                "effect_summary_json": canonical_json(
                    {
                        "effect": evidence["effect"],
                        "rates": evidence["rates"],
                        "testing": evidence["testing"],
                        "quality": evidence["quality"],
                        "direction": audit_finding["direction"],
                        "provenance": {
                            key: evidence["provenance"].get(key)
                            for key in (
                                "analysis_version",
                                "registry_version",
                                "engine_sha256",
                                "registry_sha256",
                                "input_fingerprint",
                            )
                        },
                    }
                ),
                "stability_json": canonical_json(evidence["stability"]),
            }
        return {
            "evidence_for_json": canonical_json(empty),
            "evidence_against_json": canonical_json(empty),
            "confounders_json": canonical_json(
                {"checked": [], "unchecked": [], "sensitive_to": [], "weighted_effect": None}
            ),
            "sample_size_json": canonical_json(
                {
                    "eligible_n": 0,
                    "complete_n": 0,
                    "missing_n": 0,
                    "reason": decision.evidence_class,
                }
            ),
            "effect_summary_json": canonical_json(
                {
                    "effect": None,
                    "rates": None,
                    "testing": None,
                    "quality": None,
                    "direction": "unknown",
                    "provenance": {
                        key: run[key]
                        for key in (
                            "analysis_version",
                            "registry_version",
                            "engine_sha256",
                            "registry_sha256",
                            "input_fingerprint",
                        )
                    },
                }
            ),
            "stability_json": canonical_json({"status": "not_evaluated"}),
        }
    evidence = finding["evidence"]
    return {
        "evidence_for_json": finding["evidence_for_json"],
        "evidence_against_json": finding["evidence_against_json"],
        "confounders_json": canonical_json(evidence["confounders"]),
        "sample_size_json": canonical_json(evidence["sample"]),
        "effect_summary_json": canonical_json(
            {
                "effect": evidence["effect"],
                "rates": evidence["rates"],
                "testing": evidence["testing"],
                "quality": evidence["quality"],
                "direction": finding["direction"],
                "provenance": {
                    key: evidence["provenance"].get(key)
                    for key in (
                        "analysis_version",
                        "registry_version",
                        "engine_sha256",
                        "registry_sha256",
                    )
                },
            }
        ),
        "stability_json": canonical_json(evidence["stability"]),
    }


def _insert_evidence_items(
    conn: sqlite3.Connection,
    *,
    hypothesis_id: str,
    evaluation_id: int,
    finding: Mapping[str, Any] | None,
    decision: TransitionDecision,
    analysis_version: str,
    range_from: str | None,
    range_to: str | None,
    created_at: str,
) -> list[str]:
    if finding is None or not decision.evidence_kinds:
        return []
    if range_from is None or range_to is None:
        raise _error("ledger_invariant", "finding evidence requires a concrete range")
    identifiers: list[str] = []
    for polarity, evidence_kind in decision.evidence_kinds:
        if polarity not in {"for", "against"} or evidence_kind not in _EVIDENCE_KINDS:
            raise _error("ledger_invariant", "state machine emitted an unknown evidence mapping")
        evidence_item_id = sha256_id(
            {
                "contract_version": LEDGER_CONTRACT_VERSION,
                "kind": "hypothesis_evidence_item",
                "hypothesis_id": hypothesis_id,
                "finding_id": finding["finding_id"],
                "evidence_kind": evidence_kind,
            }
        )
        expected = {
            "evidence_item_id": evidence_item_id,
            "hypothesis_id": hypothesis_id,
            "evaluation_id": evaluation_id,
            "finding_id": finding["finding_id"],
            "polarity": polarity,
            "evidence_kind": evidence_kind,
            "evidence_fingerprint": finding["evidence_fingerprint"],
            "source_analysis_version": analysis_version,
            "range_from": range_from,
            "range_to": range_to,
            "created_at": created_at,
        }
        existing = _fetch_one(
            conn,
            """SELECT * FROM hypothesis_evidence_items
               WHERE hypothesis_id=? AND finding_id=? AND evidence_kind=?""",
            (hypothesis_id, finding["finding_id"], evidence_kind),
        )
        if existing is None:
            conn.execute(
                """INSERT INTO hypothesis_evidence_items(
                     evidence_item_id,hypothesis_id,evaluation_id,finding_id,
                     polarity,evidence_kind,evidence_fingerprint,
                     source_analysis_version,range_from,range_to,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(expected.values()),
            )
        else:
            _assert_existing(
                existing, expected, name="hypothesis evidence item"
            )
        identifiers.append(evidence_item_id)
    return identifiers


def _append_evaluation(
    conn: sqlite3.Connection,
    *,
    hypothesis: Mapping[str, Any],
    run: Mapping[str, Any],
    finding: Mapping[str, Any] | None,
    explicit: bool,
    dormancy_reason: str | None,
    compatibility_proofs: Mapping[
        tuple[str, str, str, str],
        Mapping[str, str],
    ],
    created_at: str,
    audit_finding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if run["status"] not in {"completed", "insufficient_data", "no_data"}:
        raise _error("run_not_terminal", "hypothesis evaluation requires a terminal data run")
    input_fingerprint_value = run["input_fingerprint"]
    if input_fingerprint_value is None:
        raise _error("ledger_invariant", "terminal data run lacks an input fingerprint")
    analysis_version = run["analysis_version"]
    current_semantics = _run_semantics(run)
    tested_at = _timestamp(run["completed_at"] or created_at, "tested_at")
    duplicate = _fetch_one(
        conn,
        """SELECT * FROM hypothesis_evaluations
           WHERE hypothesis_id=? AND input_fingerprint=?
             AND source_analysis_version=?""",
        (
            hypothesis["hypothesis_id"],
            input_fingerprint_value,
            analysis_version,
        ),
    )
    if duplicate is not None:
        duplicate_evaluation = next(
            item
            for item in _load_evaluations(conn, hypothesis["hypothesis_id"])
            if item["id"] == duplicate["id"]
        )
        if (
            _evaluation_semantics(duplicate_evaluation)["registry_version"]
            != current_semantics["registry_version"]
        ):
            raise _error(
                "ledger_invariant",
                "one input fingerprint cannot span registry semantic versions",
            )
        return {
            "evaluation": duplicate,
            "created": False,
            "no_op_reason": "same_fingerprint_version",
            "evidence_item_ids": [],
        }
    same_run = _fetch_one(
        conn,
        """SELECT id FROM hypothesis_evaluations
           WHERE hypothesis_id=? AND run_id=?""",
        (hypothesis["hypothesis_id"], run["run_id"]),
    )
    if same_run is not None:
        raise _error("ledger_invariant", "hypothesis run already has a different evaluation")

    evaluations = _load_evaluations(conn, hypothesis["hypothesis_id"])
    latest = evaluations[-1] if evaluations else None
    if latest is not None and tested_at < latest["tested_at"]:
        raise _error(
            "historical_evaluation_order",
            "hypothesis evaluations cannot be inserted before the latest audit event",
        )
    substantive = [
        item
        for item in evaluations
        if item["evidence_class"]
        not in {"dormant_no_eligible_data", "dormant_stale_prerequisite"}
    ]
    prior_substantive = substantive[-1] if substantive else None
    if prior_substantive is None:
        compatible = True
        compatibility_audit: dict[str, Any] = {
            "from": None,
            "to": dict(current_semantics),
            "semantic_versions_equal": True,
            "proof": None,
            "compatible": True,
        }
    else:
        compatible, _proof, compatibility_audit = _semantic_compatibility(
            prior_substantive,
            current=current_semantics,
            proofs=compatibility_proofs,
        )
    if compatible:
        lineage = [
            item
            for item in evaluations
            if _semantic_compatibility(
                item,
                current=current_semantics,
                proofs=compatibility_proofs,
            )[0]
            and item["evidence_class"]
            not in {"dormant_no_eligible_data", "dormant_stale_prerequisite"}
        ]
        if not lineage and prior_substantive is not None:
            lineage = [prior_substantive]
    else:
        # A first result from a changed semantic version starts a separate
        # comparison lineage.  Its retained-status incompatible evaluation is
        # available as the reference for later same-version windows.
        lineage = [
            item
            for item in evaluations
            if (
                _evaluation_semantics(item)["analysis_version"]
                == current_semantics["analysis_version"]
                and _evaluation_semantics(item)["registry_version"]
                == current_semantics["registry_version"]
            )
            and item["finding_id"] is not None
        ]
        if lineage:
            compatible = True
            compatibility_audit = {
                "from": _evaluation_semantics(lineage[-1]),
                "to": dict(current_semantics),
                "semantic_versions_equal": True,
                "proof": None,
                "compatible": True,
                "separate_lineage": True,
            }

    current = _finding_state(finding) if finding is not None else None
    decision = _transition(
        current=current,
        latest=latest,
        prior_substantive=prior_substantive,
        lineage=lineage,
        compatible=compatible,
        explicit=explicit,
        dormancy_reason=dormancy_reason,
        range_from=run["analysis_from"],
        range_to=run["analysis_to"],
    )
    decision = TransitionDecision(
        evidence_class=decision.evidence_class,
        status=decision.status,
        confidence=decision.confidence,
        change_reason=decision.change_reason,
        transition_applied=decision.transition_applied,
        comparison_evaluation_id=decision.comparison_evaluation_id,
        compatible_with_prior=decision.compatible_with_prior,
        new_eligible_observations=decision.new_eligible_observations,
        evidence_kinds=decision.evidence_kinds,
        conditions={
            **decision.conditions,
            "semantic_compatibility": compatibility_audit,
        },
    )
    if finding is None and decision.evidence_class == "incompatible_version":
        # Migration 004 intentionally permits a null finding only for dormant
        # evaluations, while method-incompatibility evidence must reference an
        # immutable finding.  A no-finding run therefore cannot truthfully
        # append either class.  Retain the prior conclusion as an audited
        # analysis-run no-op instead of letting lower-priority dormancy win.
        return {
            "evaluation": latest or prior_substantive,
            "created": False,
            "no_op_reason": "incompatible_version_without_finding",
            "evidence_item_ids": [],
        }
    payloads = _evaluation_payloads(
        finding=finding,
        audit_finding=audit_finding,
        decision=decision,
        run=run,
    )
    evidence_fp = (
        finding["evidence_fingerprint"]
        if finding is not None
        else sha256_id(
            {
                "contract_version": LEDGER_CONTRACT_VERSION,
                "kind": "dormant_evidence",
                "hypothesis_id": hypothesis["hypothesis_id"],
                "run_id": run["run_id"],
                "input_fingerprint": input_fingerprint_value,
                "reason": decision.evidence_class,
                "analysis_version": run["analysis_version"],
                "registry_version": run["registry_version"],
                "engine_sha256": run["engine_sha256"],
                "registry_sha256": run["registry_sha256"],
                "range_from": run["analysis_from"],
                "range_to": run["analysis_to"],
                "audit_finding_evidence_fingerprint": (
                    audit_finding["evidence_fingerprint"]
                    if audit_finding is not None
                    else None
                ),
            }
        )
    )
    previous_status = latest["status"] if latest is not None else None
    cursor = conn.execute(
        """INSERT INTO hypothesis_evaluations(
             hypothesis_id,run_id,finding_id,tested_at,range_from,range_to,
             evidence_class,previous_status,evidence_for_json,
             evidence_against_json,confounders_json,sample_size_json,
             effect_summary_json,stability_json,confidence,status,
             change_reason,change_conditions,source_analysis_version,
             input_fingerprint,evidence_fingerprint,previous_evaluation_id,
             comparison_evaluation_id,new_eligible_observations,
             compatible_with_prior,transition_applied,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            hypothesis["hypothesis_id"],
            run["run_id"],
            finding["finding_id"] if finding is not None else None,
            tested_at,
            run["analysis_from"],
            run["analysis_to"],
            decision.evidence_class,
            previous_status,
            payloads["evidence_for_json"],
            payloads["evidence_against_json"],
            payloads["confounders_json"],
            payloads["sample_size_json"],
            payloads["effect_summary_json"],
            payloads["stability_json"],
            decision.confidence,
            decision.status,
            decision.change_reason,
            canonical_json(decision.conditions),
            analysis_version,
            input_fingerprint_value,
            evidence_fp,
            latest["id"] if latest is not None else None,
            decision.comparison_evaluation_id,
            decision.new_eligible_observations,
            int(decision.compatible_with_prior),
            int(decision.transition_applied),
            created_at,
        ),
    )
    evaluation_id = int(cursor.lastrowid)
    evidence_ids = _insert_evidence_items(
        conn,
        hypothesis_id=hypothesis["hypothesis_id"],
        evaluation_id=evaluation_id,
        finding=finding,
        decision=decision,
        analysis_version=analysis_version,
        range_from=run["analysis_from"],
        range_to=run["analysis_to"],
        created_at=created_at,
    )
    evaluation = _fetch_one(
        conn, "SELECT * FROM hypothesis_evaluations WHERE id=?", (evaluation_id,)
    )
    assert evaluation is not None
    return {
        "evaluation": evaluation,
        "created": True,
        "no_op_reason": None,
        "evidence_item_ids": evidence_ids,
    }


def _promote_stored_finding(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    finding_id_value: str,
    explicit: bool = True,
    created_at: str | None = None,
    compatibility_proofs: Mapping[
        tuple[str, str, str, str],
        Mapping[str, str],
    ] | None = None,
    clock: Callable[[], str],
    timezone_name: str,
) -> dict[str, Any]:
    """Internal automatic primitive for one already persisted finding.

    ``explicit=False`` is the automatic path and accepts only a Phase 4
    discovery pass.  ``explicit=True`` also permits an eligible exploratory
    screen.  No numerical or status override is accepted.
    """

    require_ledger_schema(conn)
    run_id = _sha(run_id, "run_id")
    finding_id_value = _sha(finding_id_value, "finding_id")
    created = _timestamp(created_at or clock(), "created_at")
    if type(explicit) is not bool:
        raise _error("validation_error", "explicit must be boolean", validation=True)
    run = _fetch_one(
        conn,
        """SELECT r.*,b.analysis_version,b.registry_version,b.engine_sha256,
                  b.registry_sha256,b.run_kind
           FROM analysis_runs r
           JOIN analysis_batches b ON b.batch_id=r.batch_id
           WHERE r.run_id=?""",
        (run_id,),
    )
    if run is None:
        raise _error("unknown_run", "analysis run does not exist", validation=True)
    assert_terminal_batch_integrity(conn, batch_id=run["batch_id"])
    finding = _load_finding(conn, finding_id_value)
    if not finding_belongs_to_run(
        conn,
        run_id=run_id,
        finding_id_value=finding_id_value,
        timezone_name=timezone_name,
    ):
        raise _error("mismatched_finding", "finding does not belong to the run")
    _assert_finding_run_semantics(finding, run)
    hypothesis, hypothesis_created = _ensure_hypothesis(
        conn,
        run=run,
        finding=finding,
        explicit=explicit,
        created_at=created,
    )
    evaluation = _append_evaluation(
        conn,
        hypothesis=hypothesis,
        run=run,
        finding=finding,
        explicit=explicit,
        dormancy_reason=None,
        compatibility_proofs=compatibility_proofs or {},
        created_at=created,
    )
    return {
        "ok": True,
        "contract_version": LEDGER_CONTRACT_VERSION,
        "hypothesis_id": hypothesis["hypothesis_id"],
        "hypothesis_created": hypothesis_created,
        "evaluation": evaluation,
    }


def refresh_batch_hypotheses(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    dormancy_reason: str | None = None,
    created_at: str | None = None,
    compatibility_proofs: Iterable[Mapping[str, Any]] | None = None,
    clock: Callable[[], str],
    timezone_name: str,
) -> dict[str, Any]:
    """Process the state-bearing terminal runs through the Section 11.6 ledger.

    Discovery-pass findings auto-create hypotheses.  Existing conceptual
    hypotheses are reevaluated from the matching finding.  A monthly batch
    applies transitions only from its all-history primary run; its recent and
    historical runs remain immutable comparison evidence.  Monthly callers
    may pass one exact dormancy class for hypotheses with no evaluable finding;
    other callers leave them unchanged.
    """

    require_ledger_schema(conn)
    batch_id = _sha(batch_id, "batch_id")
    created = _timestamp(created_at or clock(), "created_at")
    proof_index = _normalized_compatibility_proofs(compatibility_proofs)
    if dormancy_reason is not None:
        _enum(
            dormancy_reason,
            frozenset(
                {"dormant_no_eligible_data", "dormant_stale_prerequisite"}
            ),
            "dormancy_reason",
        )
    batch = _fetch_one(
        conn, "SELECT * FROM analysis_batches WHERE batch_id=?", (batch_id,)
    )
    if batch is None:
        raise _error("unknown_batch", "analysis batch does not exist", validation=True)
    assert_terminal_batch_integrity(conn, batch_id=batch_id)
    if dormancy_reason is not None and batch["run_kind"] != "monthly":
        raise _error(
            "validation_error",
            "only a monthly review may apply dormancy",
            validation=True,
        )
    run_cursor = conn.execute(
        """SELECT r.*,b.analysis_version,b.registry_version,b.engine_sha256,
                  b.registry_sha256,b.run_kind
           FROM analysis_runs r
           JOIN analysis_batches b ON b.batch_id=r.batch_id
           JOIN analysis_range_requests q
             ON q.range_id=r.range_id AND q.batch_id=r.batch_id
           WHERE r.batch_id=?
             AND r.status IN ('completed','insufficient_data','no_data')
             AND (b.run_kind<>'monthly' OR q.range_role='primary')
           ORDER BY COALESCE(r.analysis_from,''),COALESCE(r.analysis_to,''),
                    r.outcome_key,r.outcome_mode,r.run_id""",
        (batch_id,),
    )
    columns = [item[0] for item in run_cursor.description]
    runs = [_row_dict(item, columns) for item in run_cursor.fetchall()]
    summary = {
        "hypotheses_created": 0,
        "evaluations_created": 0,
        "same_fingerprint_noops": 0,
        "incompatible_version_noops": 0,
        "dormant_created": 0,
        "skipped_insufficient": 0,
        "skipped_context_only": 0,
    }
    for run in runs:
        touched: set[str] = set()
        findings = sorted(
            _findings_for_run(conn, run, timezone_name=timezone_name),
            key=lambda item: item["finding_id"],
        )
        for finding in findings:
            _assert_finding_run_semantics(finding, run)
        by_dedupe = {_hypothesis_dedupe(item): item for item in findings}
        # Automatic creation is deliberately limited to a complete first pass.
        for finding in findings:
            if (
                finding["quality_tier"] != "exploratory_unreplicated"
                or not bool(finding["eligible_for_hypothesis"])
            ):
                continue
            hypothesis, was_created = _ensure_hypothesis(
                conn,
                run=run,
                finding=finding,
                explicit=False,
                created_at=created,
            )
            if was_created:
                summary["hypotheses_created"] += 1
            result = _append_evaluation(
                conn,
                hypothesis=hypothesis,
                run=run,
                finding=finding,
                explicit=False,
                dormancy_reason=None,
                compatibility_proofs=proof_index,
                created_at=created,
            )
            touched.add(hypothesis["hypothesis_id"])
            if result["created"]:
                summary["evaluations_created"] += 1
            elif result["no_op_reason"] == "incompatible_version_without_finding":
                summary["incompatible_version_noops"] += 1
            else:
                summary["same_fingerprint_noops"] += 1

        hypothesis_cursor = conn.execute(
            """SELECT * FROM hypotheses
               WHERE outcome_key=? AND outcome_mode=? ORDER BY hypothesis_id""",
            (run["outcome_key"], run["outcome_mode"]),
        )
        hypothesis_columns = [item[0] for item in hypothesis_cursor.description]
        hypotheses = [
            _row_dict(item, hypothesis_columns)
            for item in hypothesis_cursor.fetchall()
        ]
        for hypothesis in hypotheses:
            if hypothesis["hypothesis_id"] in touched:
                continue
            finding = by_dedupe.get(hypothesis["dedupe_key"])
            audit_finding = None
            effective_dormancy = dormancy_reason
            if finding is not None:
                if _is_context_only_finding(finding):
                    if dormancy_reason is None:
                        summary["skipped_context_only"] += 1
                        continue
                    audit_finding = finding
                    finding = None
                else:
                    state = _finding_state(finding)
                    if not state["base_pass"]:
                        if dormancy_reason is None:
                            summary["skipped_insufficient"] += 1
                            continue
                        audit_finding = finding
                        finding = None
            elif dormancy_reason is None:
                continue
            result = _append_evaluation(
                conn,
                hypothesis=hypothesis,
                run=run,
                finding=finding,
                explicit=False,
                dormancy_reason=effective_dormancy if finding is None else None,
                compatibility_proofs=proof_index,
                created_at=created,
                audit_finding=audit_finding,
            )
            touched.add(hypothesis["hypothesis_id"])
            if result["created"]:
                summary["evaluations_created"] += 1
                if finding is None:
                    summary["dormant_created"] += 1
            elif result["no_op_reason"] == "incompatible_version_without_finding":
                summary["incompatible_version_noops"] += 1
            else:
                summary["same_fingerprint_noops"] += 1
    return {
        "ok": True,
        "contract_version": LEDGER_CONTRACT_VERSION,
        "batch_id": batch_id,
        **summary,
    }


def append_annotation(
    conn: sqlite3.Connection,
    *,
    hypothesis_id: str,
    annotation_kind: str,
    content: str,
    source: str,
    evaluation_id: int | None = None,
    synthesis_id: str | None = None,
    context_version: str | None = None,
    prompt_sha256: str | None = None,
    model_id: str | None = None,
    provider: str | None = None,
    supersedes_id: str | None = None,
    annotation_id: str | None = None,
    created_at: str | None = None,
    clock: Callable[[], str],
) -> dict[str, Any]:
    """Append one owner/model prose annotation without accepting evidence fields."""

    require_ledger_schema(conn)
    hypothesis_id = _sha(hypothesis_id, "hypothesis_id")
    annotation_kind = _enum(
        annotation_kind, _ANNOTATION_KINDS, "annotation_kind"
    )
    limit = 2000 if annotation_kind in {"mechanism", "owner_note"} else 1000
    content = _text(content.strip(), "content", maximum=limit)
    source = _enum(
        source,
        frozenset({"owner", "glm_synthesis", "hermes_synthesis"}),
        "source",
    )
    created = _timestamp(created_at or clock(), "created_at")
    if _fetch_one(
        conn,
        "SELECT hypothesis_id FROM hypotheses WHERE hypothesis_id=?",
        (hypothesis_id,),
    ) is None:
        raise _error("unknown_hypothesis", "hypothesis does not exist", validation=True)
    if evaluation_id is not None:
        evaluation_id = _integer(
            evaluation_id,
            "evaluation_id",
            minimum=1,
            maximum=SQLITE_MAX_ROWID,
        )
        linked = _fetch_one(
            conn,
            """SELECT id FROM hypothesis_evaluations
               WHERE id=? AND hypothesis_id=?""",
            (evaluation_id, hypothesis_id),
        )
        if linked is None:
            raise _error(
                "mismatched_evaluation",
                "evaluation does not belong to hypothesis",
                validation=True,
            )
    if source == "owner":
        if any(
            value is not None
            for value in (
                synthesis_id,
                context_version,
                prompt_sha256,
                model_id,
                provider,
            )
        ):
            raise _error(
                "validation_error",
                "owner annotation cannot claim synthesis/model provenance",
                validation=True,
            )
    else:
        if annotation_kind == "owner_note":
            raise _error(
                "validation_error",
                "Hermes synthesis cannot create an owner_note annotation",
                validation=True,
            )
        synthesis_id = _sha(synthesis_id, "synthesis_id")
        context_version = _text(
            context_version, "context_version", maximum=120, token=True
        )
        if context_version != "2":
            raise _error(
                "validation_error",
                "Hermes synthesis annotation requires context_version 2",
                validation=True,
            )
        prompt_sha256 = _sha(prompt_sha256, "prompt_sha256")
        if (
            not isinstance(model_id, str)
            or _MODEL_PROVENANCE_RE.fullmatch(model_id) is None
            or not isinstance(provider, str)
            or _MODEL_PROVENANCE_RE.fullmatch(provider) is None
        ):
            raise _error(
                "validation_error",
                "Hermes synthesis provenance identifiers are not canonical",
                validation=True,
            )
        if source == "glm_synthesis" and (
            model_id != "z-ai/glm-5.2" or provider != "openrouter"
        ):
            raise _error(
                "validation_error",
                "legacy GLM synthesis provenance must use "
                "z-ai/glm-5.2 through openrouter",
                validation=True,
            )
        synthesis = _fetch_one(
            conn,
            """SELECT synthesis_id,status,context_version,prompt_sha256,
                      model_id,provider
               FROM synthesis_runs WHERE synthesis_id=?""",
            (synthesis_id,),
        )
        if synthesis is None:
            raise _error(
                "unknown_synthesis",
                "synthesis run does not exist",
                validation=True,
            )
        if synthesis["status"] != "completed":
            raise _error(
                "incomplete_synthesis",
                "Hermes annotation requires a completed synthesis",
                validation=True,
            )
        for field, supplied in (
            ("context_version", context_version),
            ("prompt_sha256", prompt_sha256),
            ("model_id", model_id),
            ("provider", provider),
        ):
            if synthesis[field] != supplied:
                raise _error(
                    "mismatched_synthesis_provenance",
                    f"annotation {field} does not match synthesis",
                    validation=True,
                )
        if evaluation_id is None:
            raise _error(
                "mismatched_synthesis_reference",
                "Hermes synthesis annotation requires its exact referenced evaluation",
                validation=True,
            )
        synthesis_evaluations = {
            int(row[0])
            for row in conn.execute(
                """SELECT evaluation_id FROM synthesis_hypothesis_refs
                   WHERE synthesis_id=? AND hypothesis_id=?""",
                (synthesis_id, hypothesis_id),
            )
        }
        if not synthesis_evaluations:
            raise _error(
                "mismatched_synthesis_reference",
                "synthesis does not reference the annotated hypothesis",
                validation=True,
            )
        if evaluation_id not in synthesis_evaluations:
            raise _error(
                "mismatched_synthesis_reference",
                "synthesis does not reference the annotated evaluation",
                validation=True,
            )
    superseding_child: dict[str, Any] | None = None
    if supersedes_id is not None:
        supersedes_id = _sha(supersedes_id, "supersedes_id")
        prior = _fetch_one(
            conn,
            "SELECT * FROM hypothesis_annotations WHERE annotation_id=?",
            (supersedes_id,),
        )
        if prior is None:
            raise _error(
                "unknown_annotation",
                "superseded annotation does not exist",
                validation=True,
            )
        if (
            prior["hypothesis_id"] != hypothesis_id
            or prior["annotation_kind"] != annotation_kind
        ):
            raise _error(
                "mismatched_annotation",
                "superseded annotation must share hypothesis and kind",
                validation=True,
            )
        superseding_child = _fetch_one(
            conn,
            "SELECT * FROM hypothesis_annotations WHERE supersedes_id=?",
            (supersedes_id,),
        )
    identity = canonical_annotation_identity(
        hypothesis_id=hypothesis_id,
        evaluation_id=evaluation_id,
        annotation_kind=annotation_kind,
        content=content,
        source=source,
        synthesis_id=synthesis_id,
        context_version=context_version,
        prompt_sha256=prompt_sha256,
        model_id=model_id,
        provider=provider,
        supersedes_id=supersedes_id,
    )
    input_hash = identity["input_sha256"]
    derived_id = identity["annotation_id"]
    if annotation_id is not None and _sha(annotation_id, "annotation_id") != derived_id:
        raise _error(
            "mismatched_annotation",
            "annotation ID must match canonical input",
            validation=True,
        )
    annotation_id = derived_id
    if (
        superseding_child is not None
        and superseding_child["annotation_id"] != annotation_id
    ):
        raise _error(
            "stale_annotation",
            "only the current annotation leaf may be superseded",
            validation=True,
        )
    expected = {
        "annotation_id": annotation_id,
        "hypothesis_id": hypothesis_id,
        "evaluation_id": evaluation_id,
        "annotation_kind": annotation_kind,
        "content": content,
        "source": source,
        "synthesis_id": synthesis_id,
        "context_version": context_version,
        "prompt_sha256": prompt_sha256,
        "model_id": model_id,
        "provider": provider,
        "supersedes_id": supersedes_id,
        "input_sha256": input_hash,
        "created_at": created,
    }
    existing = _fetch_one(
        conn,
        "SELECT * FROM hypothesis_annotations WHERE annotation_id=?",
        (annotation_id,),
    )
    if existing is not None:
        _assert_existing(
            existing,
            expected,
            name="hypothesis annotation",
            ignored=frozenset({"created_at"}),
        )
        return {**existing, "created": False}
    conn.execute(
        """INSERT INTO hypothesis_annotations(
             annotation_id,hypothesis_id,evaluation_id,annotation_kind,content,
             source,synthesis_id,context_version,prompt_sha256,model_id,provider,
             supersedes_id,input_sha256,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        tuple(expected.values()),
    )
    return {**expected, "created": True}


def _evaluation_public(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in item.items()
        if not key.endswith("_json")
    }


def _latest_annotations(
    annotations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    superseded = {
        item["supersedes_id"]
        for item in annotations
        if item["supersedes_id"] is not None
    }
    return [
        dict(item)
        for item in annotations
        if item["annotation_id"] not in superseded
    ]


def list_hypotheses(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    outcome_key: str | None = None,
    limit: int = 50,
    before: str | None = None,
) -> dict[str, Any]:
    """Return JSON-safe cursor-paginated hypotheses with latest evaluations."""

    require_ledger_schema(conn)
    if status is not None:
        status = _enum(status, HYPOTHESIS_STATUSES, "status")
    if outcome_key is not None:
        outcome_key = _feature(outcome_key, "outcome_key")
    limit = _integer(limit, "limit", minimum=1, maximum=100)
    before_created: str | None = None
    before_id: str | None = None
    if before is not None:
        before = _sha(before, "before")
        cursor_row = _fetch_one(
            conn,
            "SELECT hypothesis_id,created_at FROM hypotheses WHERE hypothesis_id=?",
            (before,),
        )
        if cursor_row is None:
            raise _error("invalid_cursor", "hypothesis cursor does not exist", validation=True)
        before_created, before_id = cursor_row["created_at"], cursor_row["hypothesis_id"]
    clauses: list[str] = []
    params: list[Any] = []
    if outcome_key is not None:
        clauses.append("h.outcome_key=?")
        params.append(outcome_key)
    if before_created is not None:
        clauses.append("(h.created_at<? OR (h.created_at=? AND h.hypothesis_id<?))")
        params.extend((before_created, before_created, before_id))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    cursor = conn.execute(
        f"""SELECT h.* FROM hypotheses h{where}
            ORDER BY h.created_at DESC,h.hypothesis_id DESC""",
        params,
    )
    columns = [item[0] for item in cursor.description]
    items: list[dict[str, Any]] = []
    for raw in cursor.fetchall():
        hypothesis = _row_dict(raw, columns)
        evaluations = _load_evaluations(conn, hypothesis["hypothesis_id"])
        latest = evaluations[-1] if evaluations else None
        if status is not None and (latest is None or latest["status"] != status):
            continue
        components = [
            _row_dict(item, ["position", "exposure_key", "lag_days", "window_days", "transform"])
            for item in conn.execute(
                """SELECT position,exposure_key,lag_days,window_days,transform
                   FROM hypothesis_components
                   WHERE hypothesis_id=? ORDER BY position""",
                (hypothesis["hypothesis_id"],),
            )
        ]
        items.append(
            {
                **hypothesis,
                "components": components,
                "latest_evaluation": (
                    _evaluation_public(latest) if latest is not None else None
                ),
            }
        )
        if len(items) == limit + 1:
            break
    has_more = len(items) > limit
    visible = items[:limit]
    next_before = visible[-1]["hypothesis_id"] if has_more else None
    return {
        "ok": True,
        "contract_version": LEDGER_CONTRACT_VERSION,
        "items": visible,
        "next_before": next_before,
    }


def hypothesis_brief(
    conn: sqlite3.Connection,
    hypothesis_id: str,
) -> dict[str, Any]:
    """Return one fully audited hypothesis, evaluation and immutable evidence history."""

    require_ledger_schema(conn)
    hypothesis_id = _sha(hypothesis_id, "hypothesis_id")
    hypothesis = _fetch_one(
        conn, "SELECT * FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,)
    )
    if hypothesis is None:
        raise _error("unknown_hypothesis", "hypothesis does not exist", validation=True)
    components = [
        _row_dict(item, ["position", "exposure_key", "lag_days", "window_days", "transform"])
        for item in conn.execute(
            """SELECT position,exposure_key,lag_days,window_days,transform
               FROM hypothesis_components WHERE hypothesis_id=? ORDER BY position""",
            (hypothesis_id,),
        )
    ]
    evaluations = _load_evaluations(conn, hypothesis_id)
    evidence_cursor = conn.execute(
        """SELECT e.*,f.evidence_json
           FROM hypothesis_evidence_items e
           JOIN analysis_findings f ON f.finding_id=e.finding_id
           WHERE e.hypothesis_id=?
           ORDER BY e.created_at,e.evidence_item_id""",
        (hypothesis_id,),
    )
    evidence_columns = [item[0] for item in evidence_cursor.description]
    evidence_items: list[dict[str, Any]] = []
    for raw in evidence_cursor.fetchall():
        item = _row_dict(raw, evidence_columns)
        evidence = _parse_canonical(item.pop("evidence_json"), "finding.evidence_json")
        version = evidence.get("provenance", {}).get("analysis_version")
        if (
            not isinstance(version, str)
            or _evidence_fingerprint_for(evidence, version)
            != item["evidence_fingerprint"]
        ):
            raise _error("ledger_corrupt", "evidence item fingerprint is invalid")
        evidence_items.append({**item, "finding": evidence})
    annotation_cursor = conn.execute(
        """SELECT * FROM hypothesis_annotations
           WHERE hypothesis_id=? ORDER BY created_at,annotation_id""",
        (hypothesis_id,),
    )
    annotation_columns = [item[0] for item in annotation_cursor.description]
    annotations = [
        _row_dict(item, annotation_columns)
        for item in annotation_cursor.fetchall()
    ]
    latest = evaluations[-1] if evaluations else None
    return {
        "ok": True,
        "contract_version": LEDGER_CONTRACT_VERSION,
        "hypothesis": {
            **hypothesis,
            "components": components,
            "latest_evaluation": (
                _evaluation_public(latest) if latest is not None else None
            ),
        },
        "evaluations": [_evaluation_public(item) for item in evaluations],
        "evidence_for": [
            item for item in evidence_items if item["polarity"] == "for"
        ],
        "evidence_against": [
            item for item in evidence_items if item["polarity"] == "against"
        ],
        "annotations": annotations,
        "latest_annotations": _latest_annotations(annotations),
    }
