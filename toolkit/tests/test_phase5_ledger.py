"""Phase 5 ledger persistence and Section 11.6 state-machine tests.

All rows live in disposable in-memory Migration 004 schemas.  The fixtures
model the public Phase 4 contract; no real database or owner data is opened.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import sqlite3

import pytest

from hermes_insights import ledger, migrations, synthesis
from hermes_insights.contracts import REGISTRY_VERSION, canonical_json
from hermes_insights.provenance import (
    ANALYSIS_CONTRACT_VERSION,
    ANALYSIS_VERSION,
    candidate_key,
    engine_sha256,
    evidence_fingerprint,
    finding_id,
    sha256_id,
)


OUTCOME = "subjective.day_rating"
MODE = "green-vs-non-green"
REGISTRY_HASH = sha256_id({"fixture": "registry"})
CREATED = "2026-01-01T12:00:00+00:00"
SCHEMA_APPLIED = "2026-01-01T00:00:00+00:00"
SCHEMA_SQL = (Path(__file__).resolve().parents[1] / "SCHEMA.sql").read_text(
    encoding="utf-8"
)


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    conn.executemany(
        """INSERT INTO schema_migrations(
             version,name,checksum_sha256,applied_at,code_version)
           VALUES(?,?,?,?,?)""",
        [
            (
                migration.version,
                migration.name,
                migration.checksum,
                SCHEMA_APPLIED,
                "test",
            )
            for migration in migrations.MIGRATIONS
        ],
    )
    try:
        yield conn
    finally:
        conn.close()


def component(exposure_key="event.social.occurred"):
    return {
        "exposure_key": exposure_key,
        "lag_days": 1,
        "window_days": 1,
        "transform": "count",
        "display": "Social event",
        "unit": "count",
        "temporal_type": "event_occurrence",
        "direction": "higher_better",
        "merge_rule": "event_identity",
        "zero_semantics": "structural_zero_if_complete",
        "temporal_direction": "exposure_precedes_outcome",
    }


def phase4_result(
    *,
    start="2026-01-01",
    end="2026-03-31",
    requested_kind="bounded",
    input_tag="initial",
    effect=0.20,
    ci=(0.05, 0.35),
    quality="exploratory_unreplicated",
    eligible=True,
    eligible_n=90,
    complete_n=None,
    confounder_sensitive=False,
    exposure_key="event.social.occurred",
    finding=True,
):
    input_fp = sha256_id({"fixture-input": input_tag})
    requested = {
        "kind": requested_kind,
        "from": start if requested_kind == "bounded" else None,
        "to": end if requested_kind == "bounded" else None,
    }
    actual = {
        "kind": requested_kind,
        "from": start,
        "to": end,
    }
    baseline = {"kind": "bounded", "from": start, "to": end}
    meta = {
        "analysis_version": ANALYSIS_VERSION,
        "registry_version": REGISTRY_VERSION,
        "engine_sha256": engine_sha256(),
        "registry_sha256": REGISTRY_HASH,
        "input_fingerprint": input_fp,
        "generated_at": "2026-04-01T00:00:00+02:00",
        "timezone": "Europe/Paris",
        "requested_range": requested,
        "analysis_range": actual,
        "baseline_range": baseline,
        "outcome": OUTCOME,
        "modes": [MODE],
        "min_n": 30,
        "interactions": "pairwise",
        "top": 100,
        "candidate_family_sizes": {
            MODE: 1 if finding and effect is not None else 0
        },
        "interaction_family_sizes": {MODE: 0},
    }
    findings = []
    if finding:
        comp = component(exposure_key)
        identity = {
            key: comp[key]
            for key in ("exposure_key", "lag_days", "window_days", "transform")
        }
        key = candidate_key(OUTCOME, MODE, [identity])
        fid = finding_id(
            candidate_key=key,
            analysis_range=actual,
            baseline_range=baseline,
            input_fingerprint=input_fp,
        )
        complete_n = eligible_n if complete_n is None else complete_n
        base_pass = effect is not None
        exposed_n = min(30, complete_n)
        unexposed_n = complete_n - exposed_n
        unexposed_rate = (
            round(0.5 * unexposed_n) / unexposed_n
            if base_pass and unexposed_n
            else 0.5
        )
        exposed_rate = (
            round((unexposed_rate + effect) * exposed_n) / exposed_n
            if base_pass and exposed_n
            else None
        )
        engine_effect = (
            exposed_rate - unexposed_rate if base_pass else None
        )
        baseline_rate = (
            (
                exposed_rate * exposed_n
                + unexposed_rate * unexposed_n
            )
            / complete_n
            if base_pass and complete_n
            else None
        )
        p = 0.01 if quality == "exploratory_unreplicated" else 0.20
        q = p
        if not base_pass:
            stability_status = "insufficient"
            first_half = None
            second_half = None
        elif engine_effect == 0:
            stability_status = "insufficient"
            first_half = engine_effect
            second_half = engine_effect
        elif quality == "exploratory_screen":
            stability_status = "unstable"
            first_half = engine_effect
            second_half = -engine_effect
        else:
            stability_status = "stable"
            first_half = engine_effect
            second_half = engine_effect
        evidence = {
            "finding_id": fid,
            "candidate_key": key,
            "outcome": {
                "key": OUTCOME,
                "display": "Day rating",
                "unit": "ordinal_1_3",
                "temporal_type": "outcome",
                "direction": "higher_better",
                "merge_rule": "one-row-per-date",
                "zero_semantics": "invalid",
                "mode": MODE,
            },
            "exposure": {"components": [comp]},
            "sample": {
                "eligible_n": eligible_n,
                "complete_n": complete_n,
                "missing_n": eligible_n - complete_n,
                "exposed_n": exposed_n,
                "unexposed_n": unexposed_n,
                "outcome_positive_n": (
                    round(baseline_rate * complete_n)
                    if base_pass
                    else complete_n // 2
                ),
                "outcome_negative_n": (
                    complete_n - round(baseline_rate * complete_n)
                    if base_pass
                    else complete_n - complete_n // 2
                ),
                "source_completeness": {
                    "from": start,
                    "to": end,
                    "complete": True,
                },
            },
            "rates": (
                {
                    "baseline": baseline_rate,
                    "exposed": exposed_rate,
                    "unexposed": unexposed_rate,
                    "risk_difference": engine_effect,
                    "risk_ratio": exposed_rate / unexposed_rate,
                    "oriented_risk_difference": engine_effect,
                }
                if base_pass
                else None
            ),
            "effect": {
                "method": "risk_difference" if base_pass else None,
                "estimate": engine_effect,
                "oriented_estimate": engine_effect,
                "ci95": list(ci) if ci is not None else None,
                "oriented_ci95": list(ci) if ci is not None else None,
            },
            "testing": {
                "p": p if base_pass else None,
                "q": q if base_pass else None,
                "family_size": 1 if base_pass else None,
                "method": "fisher_exact" if base_pass else None,
                "seed": 123 if base_pass else None,
                "permutation_iterations": 0,
                "bootstrap_iterations": 0,
            },
            "stability": {
                "status": stability_status,
                "full": engine_effect,
                "first_half": first_half,
                "second_half": second_half,
            },
            "confounders": {
                "checked": (
                    [
                        {
                            "key": "weekend",
                            "weighted_effect": 0.05,
                            "sensitive": True,
                        }
                    ]
                    if confounder_sensitive
                    else []
                ),
                "unchecked": [],
                "sensitive_to": ["weekend"] if confounder_sensitive else [],
                "weighted_effect": 0.05 if confounder_sensitive else None,
            },
            "evidence_for": (
                [
                    {"code": "sample_gate_pass", "value": complete_n, "threshold": 30},
                    {"code": "effect_gate_pass", "value": engine_effect, "threshold": 0.10},
                ]
                if base_pass
                else []
            ),
            "evidence_against": (
                [
                    {
                        "code": "confounder_attenuation",
                        "value": 0.05,
                        "threshold": 0.12,
                        "detail": "weekend",
                    }
                ]
                if confounder_sensitive
                else []
            ),
            "warnings": [
                "association_not_causation",
                *(["confounder_sensitive"] if confounder_sensitive else []),
            ],
            "quality": {
                "tier": quality,
                "eligible_for_hypothesis": eligible,
            },
            "provenance": {
                "analysis_version": ANALYSIS_VERSION,
                "registry_version": REGISTRY_VERSION,
                "engine_sha256": engine_sha256(),
                "registry_sha256": REGISTRY_HASH,
                "input_fingerprint": input_fp,
                "dependencies": {},
                "evidence_fingerprint": "sha256:" + "0" * 64,
            },
        }
        evidence["provenance"]["evidence_fingerprint"] = evidence_fingerprint(evidence)
        findings.append(evidence)
    return {
        "ok": True,
        "contract_version": ANALYSIS_CONTRACT_VERSION,
        "meta": meta,
        "coverage": {
            "outcome_eligible_n": eligible_n,
            "modes": {
                MODE: {
                    "outcome_eligible_n": eligible_n,
                    "generated_candidates": 1 if finding else 0,
                    "tested_candidates": 1 if finding and effect is not None else 0,
                    "insufficient_candidates": (
                        1 if finding and effect is None else 0
                    ),
                    "family_size": (
                        1 if finding and effect is not None else 0
                    ),
                }
            },
            "source_manifests": [],
            "dependencies": {},
        },
        "readiness": {"ok": True, "items": []},
        "findings": findings,
        "suppression_counts": {},
        "warnings": [],
    }


def context_only_result(**kwargs):
    result = phase4_result(**kwargs)
    finding = result["findings"][0]
    finding["quality"]["eligible_for_hypothesis"] = False
    finding["evidence_against"].append({
        "code": "autoregressive_lineage",
        "value": True,
        "threshold": False,
    })
    finding["warnings"].append("autoregressive_lineage")
    finding["warnings"] = sorted(set(finding["warnings"]))
    finding["provenance"]["evidence_fingerprint"] = evidence_fingerprint(finding)
    return result


def phase4_pair_result():
    result = phase4_result(
        input_tag="pair",
        quality="exploratory_screen",
        eligible=True,
    )
    single = deepcopy(result["findings"][0])
    finding = result["findings"][0]
    second = component("nutrition.protein_g")
    second.update(
        {
            "lag_days": 3,
            "window_days": 7,
            "transform": "mean",
            "display": "Protein",
            "unit": "g",
            "temporal_type": "daily_aggregate",
            "merge_rule": "one-row-per-date",
            "zero_semantics": "missing_if_absent",
        }
    )
    finding["exposure"]["components"].append(second)
    finding["sample"]["exposed_n"] = None
    finding["sample"]["unexposed_n"] = None
    finding["sample"]["interaction_cells"] = {
        "neither": 25,
        "a_only": 20,
        "b_only": 20,
        "both": 25,
    }
    finding["rates"] = {
        "baseline": 0.4,
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
    finding["effect"] = {
        "method": "incremental_risk_difference",
        "estimate": 0.25,
        "oriented_estimate": 0.25,
        "ci95": [0.05, 0.45],
        "oriented_ci95": [0.05, 0.45],
    }
    finding["testing"]["method"] = "source_era_stratified_permutation"
    finding["testing"]["p"] = 0.01
    finding["testing"]["q"] = 0.01
    finding["testing"]["permutation_iterations"] = (
        ledger.associations.PERMUTATION_ITERATIONS
    )
    finding["testing"]["bootstrap_iterations"] = (
        ledger.associations.BOOTSTRAP_ITERATIONS
    )
    result["meta"]["interaction_family_sizes"] = {MODE: 1}
    finding["sample"]["outcome_positive_n"] = 51
    finding["sample"]["outcome_negative_n"] = 39
    finding["stability"] = {
        "status": "not_applicable",
        "full": 0.25,
        "first_half": None,
        "second_half": None,
    }
    finding["quality"] = {
        "tier": "exploratory_unreplicated",
        "eligible_for_hypothesis": True,
    }
    identities = [
        {
            key: item[key]
            for key in ("exposure_key", "lag_days", "window_days", "transform")
        }
        for item in finding["exposure"]["components"]
    ]
    finding["candidate_key"] = candidate_key(OUTCOME, MODE, identities)
    finding["finding_id"] = finding_id(
        candidate_key=finding["candidate_key"],
        analysis_range=result["meta"]["analysis_range"],
        baseline_range=result["meta"]["baseline_range"],
        input_fingerprint=result["meta"]["input_fingerprint"],
    )
    finding["provenance"]["evidence_fingerprint"] = evidence_fingerprint(finding)
    result["findings"].append(single)
    return result


def fixture_definitions(result):
    descriptors = {}
    for finding in result["findings"]:
        for item in [finding["outcome"], *finding["exposure"]["components"]]:
            key = item.get("key", item.get("exposure_key"))
            descriptors[key] = {
                "key": key,
                "display_name": item["display"],
                "unit": item["unit"],
                "temporal_type": item["temporal_type"],
                "direction": item["direction"],
                "source": {"merge": item["merge_rule"]},
                "zero_semantics": item["zero_semantics"],
            }
    return tuple(descriptors[key] for key in sorted(descriptors))


def seal_analysis(result, *, definitions=None):
    return ledger._seal_engine_analysis_output(
        result,
        expected_registry_sha256=REGISTRY_HASH,
        definitions=definitions or fixture_definitions(result),
    )


def persist_fixture(
    conn,
    result,
    *,
    run_kind="manual",
    anchor=None,
    role="primary",
    completed_at=CREATED,
):
    meta = result["meta"]
    anchor = anchor or meta["analysis_range"]["to"]
    batch = ledger.create_analysis_batch(
        conn,
        run_kind=run_kind,
        anchor_date=anchor,
        initiator_key=f"fixture/{meta['input_fingerprint']}",
        outcome_selection="explicit_set",
        outcomes=[(OUTCOME, MODE)],
        ranges=[
            {
                "range_role": role,
                "requested_range_kind": meta["requested_range"]["kind"],
                "requested_from": meta["requested_range"]["from"],
                "requested_to": meta["requested_range"]["to"],
            }
        ],
        registry_sha256_value=REGISTRY_HASH,
        started_at=CREATED,
    )
    selected_range = next(
        item for item in batch["ranges"] if item["range_role"] == role
    )
    run = ledger.start_analysis_run(
        conn,
        batch_id=batch["batch_id"],
        range_id=selected_range["range_id"],
        outcome_key=OUTCOME,
        outcome_mode=MODE,
        batch_outcomes=[(OUTCOME, MODE)],
        started_at=CREATED,
    )
    terminal = ledger.persist_analysis_run(
        conn,
        run_id=run["run_id"],
        verified=seal_analysis(result),
        completed_at=completed_at,
    )
    ledger.finalize_analysis_batch(
        conn, batch_id=batch["batch_id"], completed_at=completed_at
    )
    return batch, terminal


def sealed_replay(result, *, index=0):
    finding = deepcopy(result["findings"][index])
    finding["source_references"] = []
    finding["source_manifests"] = []
    return ledger._seal_engine_finding_output(
        {
            "ok": True,
            "contract_version": ANALYSIS_CONTRACT_VERSION,
            "meta": deepcopy(result["meta"]),
            "finding": finding,
        },
        expected_registry_sha256=REGISTRY_HASH,
        definitions=fixture_definitions(result),
    )


def persist_legacy_version_fixture(
    conn,
    result,
    *,
    analysis_version,
    registry_version=REGISTRY_VERSION,
    anchor,
    run_kind="manual",
):
    """Model an immutable run written by another semantic engine version.

    The public verifier intentionally accepts only the running engine version.
    This disposable schema fixture first exercises that verifier/write path,
    then coherently retags every stored version-dependent identity so the
    state-machine's cross-version behavior can be tested without weakening the
    production verifier.
    """

    revised = deepcopy(result)
    batch, terminal = persist_fixture(
        conn, revised, anchor=anchor, run_kind=run_kind,
    )
    run_id = terminal["run_id"]
    stored_ids = [
        row[0]
        for row in conn.execute(
            "SELECT finding_id FROM analysis_findings WHERE run_id=?",
            (run_id,),
        )
    ]
    for stored_id in stored_ids:
        conn.execute(
            "DELETE FROM analysis_finding_components WHERE finding_id=?",
            (stored_id,),
        )
        conn.execute(
            "DELETE FROM analysis_findings WHERE finding_id=?",
            (stored_id,),
        )

    revised["meta"]["analysis_version"] = analysis_version
    revised["meta"]["registry_version"] = registry_version
    for finding in revised["findings"]:
        finding["provenance"]["analysis_version"] = analysis_version
        finding["provenance"]["registry_version"] = registry_version
        finding["finding_id"] = finding_id(
            candidate_key=finding["candidate_key"],
            analysis_range=revised["meta"]["analysis_range"],
            baseline_range=revised["meta"]["baseline_range"],
            input_fingerprint=revised["meta"]["input_fingerprint"],
            analysis_version=analysis_version,
        )
        finding["provenance"]["evidence_fingerprint"] = (
            ledger._evidence_fingerprint_for(finding, analysis_version)
        )

    result_json = canonical_json(revised)
    result_sha256 = sha256_id(
        {
            "contract_version": ledger.LEDGER_CONTRACT_VERSION,
            "kind": "analysis_result",
            "result": revised,
        }
    )
    conn.execute(
        """UPDATE analysis_batches
           SET analysis_version=?,registry_version=?
           WHERE batch_id=?""",
        (analysis_version, registry_version, batch["batch_id"]),
    )
    conn.execute(
        """UPDATE analysis_runs
           SET result_json=?,result_sha256=?
           WHERE run_id=?""",
        (result_json, result_sha256, run_id),
    )
    for finding in revised["findings"]:
        ledger._persist_finding(conn, run_id=run_id, finding=finding)
    return (
        dict(
            conn.execute(
                "SELECT * FROM analysis_batches WHERE batch_id=?",
                (batch["batch_id"],),
            ).fetchone()
        ),
        dict(
            conn.execute(
                "SELECT * FROM analysis_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
        ),
    )


def latest_eval(conn, hypothesis_id):
    return dict(
        conn.execute(
            """SELECT * FROM hypothesis_evaluations
               WHERE hypothesis_id=? ORDER BY id DESC LIMIT 1""",
            (hypothesis_id,),
        ).fetchone()
    )


def create_initial(conn, **kwargs):
    result = phase4_result(**kwargs)
    batch, run = persist_fixture(conn, result)
    refresh = ledger.refresh_batch_hypotheses(
        conn, batch_id=batch["batch_id"], created_at=CREATED
    )
    hypothesis_id = conn.execute(
        "SELECT hypothesis_id FROM hypotheses"
    ).fetchone()[0]
    return hypothesis_id, batch, run, refresh


def create_explicit_screen(conn):
    result = phase4_result(
        quality="exploratory_screen",
        eligible=True,
        effect=0.20,
        ci=(0.01, 0.39),
        input_tag="initial-screen",
    )
    batch, run = persist_fixture(conn, result)
    promoted = ledger.promote_verified_finding(
        conn,
        run_id=run["run_id"],
        verified=sealed_replay(result),
        explicit=True,
        created_at=CREATED,
    )
    return promoted["hypothesis_id"]


def create_rejected(conn):
    hypothesis_id, *_ = create_initial(conn)
    opposite = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="terminal-seed",
        effect=-0.22,
        ci=(-0.38, -0.06),
    )
    batch, _ = persist_fixture(conn, opposite, anchor="2026-06-30")
    ledger.refresh_batch_hypotheses(
        conn, batch_id=batch["batch_id"], created_at=CREATED
    )
    assert latest_eval(conn, hypothesis_id)["status"] == "rejected"
    return hypothesis_id


def test_batch_range_run_and_finding_persistence_are_deterministic(db):
    result = phase4_result()
    result["readiness"] = {
        "ok": True,
        "features": [{
            "feature_key": "event.social.occurred",
            "state": "sufficient",
            "factors": {
                "historical_staleness_basis": False,
                "stale": False,
            },
        }],
    }
    batch, run = persist_fixture(db, result)
    again = ledger.create_analysis_batch(
        db,
        run_kind="manual",
        anchor_date="2026-03-31",
        initiator_key=f"fixture/{result['meta']['input_fingerprint']}",
        outcome_selection="explicit_set",
        outcomes=[(OUTCOME, MODE), (OUTCOME, MODE)],
        ranges=[
            {
                "range_role": "primary",
                "requested_range_kind": "bounded",
                "requested_from": "2026-01-01",
                "requested_to": "2026-03-31",
            }
        ],
        registry_sha256_value=REGISTRY_HASH,
        started_at="2026-07-01T00:00:00+00:00",
    )
    assert again["batch_id"] == batch["batch_id"]
    assert again["created"] is False
    assert run["status"] == "completed"
    engine_hash_before = engine_sha256()
    stored = dict(db.execute("SELECT * FROM analysis_findings").fetchone())
    assert stored["evidence_json"] == canonical_json(result["findings"][0])
    assert stored["eligible_for_hypothesis"] == 1
    assert db.execute("SELECT COUNT(*) FROM analysis_finding_components").fetchone()[0] == 1
    final = dict(
        db.execute(
            "SELECT status,run_count,completed_count FROM analysis_batches"
        ).fetchone()
    )
    assert final == {"status": "completed", "run_count": 1, "completed_count": 1}
    next_day = deepcopy(result)
    next_day["meta"]["generated_at"] = "2026-04-02T00:00:00+02:00"
    next_day["findings"][0]["warnings"].append("historical_only")
    next_day["findings"][0]["provenance"]["evidence_fingerprint"] = (
        evidence_fingerprint(next_day["findings"][0])
    )
    next_day["warnings"].append("historical_only")
    next_day["readiness"]["features"][0]["factors"][
        "historical_staleness_basis"
    ] = True
    next_day["readiness"]["features"][0].update({
        "state": "stale",
        "needed": "Refresh stale collector data.",
    })
    next_day["readiness"]["features"][0]["factors"].update({
        "collector_freshness": {
            "status": "stale",
            "freshness_days": 2.041667,
        },
        "collector_stale_applied": True,
        "stale": True,
    })
    next_day["readiness"]["state_counts"] = {"stale": 1}
    before_retry = db.total_changes
    retried = ledger.persist_analysis_run(
        db,
        run_id=run["run_id"],
        verified=seal_analysis(next_day),
        completed_at="2026-04-02T01:00:00+02:00",
    )
    assert retried["persisted"] is False
    assert db.total_changes == before_retry
    stored_after = db.execute(
        "SELECT result_json,result_sha256 FROM analysis_runs WHERE run_id=?",
        (run["run_id"],),
    ).fetchone()
    assert stored_after["result_json"] == canonical_json(result)
    assert engine_sha256() == engine_hash_before

    changed_evidence = phase4_result(
        input_tag="initial",
        effect=0.30,
        ci=(0.10, 0.50),
    )
    with pytest.raises(ledger.LedgerError) as changed:
        ledger.persist_analysis_run(
            db,
            run_id=run["run_id"],
            verified=seal_analysis(changed_evidence),
            completed_at="2026-04-02T01:00:00+02:00",
        )
    assert changed.value.code == "terminal_run_conflict"


def test_cross_batch_day_rollover_reuses_global_immutable_finding(db):
    result = phase4_result()
    first_batch, first_run = persist_fixture(
        db, result, run_kind="manual",
    )
    next_day = deepcopy(result)
    next_day["meta"]["generated_at"] = "2026-04-02T00:00:00+02:00"
    next_day["findings"][0]["warnings"].append("historical_only")
    next_day["findings"][0]["provenance"]["evidence_fingerprint"] = (
        evidence_fingerprint(next_day["findings"][0])
    )
    next_day["warnings"].append("historical_only")
    second_batch, second_run = persist_fixture(
        db, next_day, run_kind="weekly",
    )
    assert second_batch["batch_id"] != first_batch["batch_id"]
    assert second_run["run_id"] != first_run["run_id"]
    assert db.execute("SELECT COUNT(*) FROM analysis_findings").fetchone()[0] == 1
    stored_second = json.loads(db.execute(
        "SELECT result_json FROM analysis_runs WHERE run_id=?",
        (second_run["run_id"],),
    ).fetchone()[0])
    stored_first = json.loads(db.execute(
        "SELECT result_json FROM analysis_runs WHERE run_id=?",
        (first_run["run_id"],),
    ).fetchone()[0])
    assert stored_second["findings"] == stored_first["findings"]
    assert "historical_only" not in stored_second["warnings"]
    finding_id_value = result["findings"][0]["finding_id"]
    assert ledger.finding_belongs_to_run(
        db,
        run_id=second_run["run_id"],
        finding_id_value=finding_id_value,
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=first_batch["batch_id"], created_at=CREATED,
    )
    refreshed = ledger.refresh_batch_hypotheses(
        db, batch_id=second_batch["batch_id"], created_at=CREATED,
    )
    assert refreshed["same_fingerprint_noops"] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM hypothesis_evaluations",
    ).fetchone()[0] == 1


def test_promotion_replay_accepts_only_clock_derived_historical_warning(db):
    result = phase4_result()
    _batch, run = persist_fixture(db, result)
    replay = deepcopy(result)
    replay["meta"]["generated_at"] = "2026-04-02T00:00:00+02:00"
    replay["findings"][0]["warnings"].append("historical_only")
    replay["findings"][0]["provenance"]["evidence_fingerprint"] = (
        evidence_fingerprint(replay["findings"][0])
    )
    promoted = ledger.promote_verified_finding(
        db,
        run_id=run["run_id"],
        verified=sealed_replay(replay),
        explicit=True,
        created_at=CREATED,
    )
    assert promoted["evaluation"]["created"] is True

    changed = phase4_result(
        input_tag="initial",
        effect=0.30,
        ci=(0.10, 0.50),
    )
    with pytest.raises(ledger.LedgerError) as stale:
        ledger.promote_verified_finding(
            db,
            run_id=run["run_id"],
            verified=sealed_replay(changed),
            explicit=True,
            created_at=CREATED,
        )
    assert stale.value.code == "stale_finding"


@pytest.mark.parametrize(
    "drift_sql",
    [
        "DROP INDEX analysis_runs_lookup_idx",
        "DROP TRIGGER hypotheses_no_update",
        "DROP TABLE source_sync_runs",
    ],
)
def test_ledger_schema_guard_requires_exact_v4_inventory_before_writes(db, drift_sql):
    db.execute(drift_sql)
    with pytest.raises(ledger.LedgerError) as incompatible:
        ledger.create_analysis_batch(
            db,
            run_kind="manual",
            anchor_date="2026-03-31",
            outcome_selection="explicit_set",
            outcomes=[(OUTCOME, MODE)],
            ranges=[
                {
                    "range_role": "primary",
                    "requested_range_kind": "bounded",
                    "requested_from": "2026-01-01",
                    "requested_to": "2026-03-31",
                }
            ],
            registry_sha256_value=REGISTRY_HASH,
            started_at=CREATED,
        )
    assert incompatible.value.code == "incompatible_schema"
    assert db.execute("SELECT COUNT(*) FROM analysis_batches").fetchone()[0] == 0


def test_ledger_schema_guard_requires_fk_enforcement_and_exact_recorded_version(db):
    db.commit()
    db.execute("PRAGMA foreign_keys=OFF")
    with pytest.raises(ledger.LedgerError) as disabled:
        ledger.require_ledger_schema(db)
    assert disabled.value.code == "foreign_keys_required"
    db.execute("PRAGMA foreign_keys=ON")
    db.execute(
        "DELETE FROM schema_migrations WHERE version=?",
        (migrations.AUTONOMOUS_SCHEMA_VERSION,),
    )
    with pytest.raises(ledger.LedgerError) as version:
        ledger.require_ledger_schema(db)
    assert version.value.code == "schema_migration_required"


def test_ledger_timestamps_are_normalized_to_utc(db):
    batch = ledger.create_analysis_batch(
        db,
        run_kind="manual",
        anchor_date="2026-03-31",
        outcome_selection="explicit_set",
        outcomes=[(OUTCOME, MODE)],
        ranges=[
            {
                "range_role": "primary",
                "requested_range_kind": "bounded",
                "requested_from": "2026-01-01",
                "requested_to": "2026-03-31",
            }
        ],
        registry_sha256_value=REGISTRY_HASH,
        started_at="2026-01-01T14:00:00+02:00",
    )
    assert batch["started_at"] == "2026-01-01T12:00:00+00:00"


def test_pair_findings_persist_two_ordered_components_and_hypothesis_identity(db):
    result = phase4_pair_result()
    batch, run = persist_fixture(db, result)
    finding = dict(
        db.execute(
            """SELECT * FROM analysis_findings
               WHERE run_id=? AND candidate_kind='pair'""",
            (run["run_id"],),
        ).fetchone()
    )
    assert finding["candidate_kind"] == "pair"
    components = [
        tuple(row)
        for row in db.execute(
            """SELECT position,exposure_key,lag_days,window_days,transform
               FROM analysis_finding_components
               WHERE finding_id=? ORDER BY position""",
            (finding["finding_id"],),
        )
    ]
    assert components == [
        (1, "event.social.occurred", 1, 1, "count"),
        (2, "nutrition.protein_g", 3, 7, "mean"),
    ]

    refresh = ledger.refresh_batch_hypotheses(
        db, batch_id=batch["batch_id"], created_at=CREATED
    )
    assert refresh["hypotheses_created"] == 1
    assert [
        tuple(row)
        for row in db.execute(
            """SELECT position,exposure_key,lag_days,window_days,transform
               FROM hypothesis_components ORDER BY position"""
        )
    ] == components


def test_identical_global_finding_is_reused_and_run_membership_comes_from_result(db):
    result = phase4_result()
    first_batch, first_run = persist_fixture(db, result, run_kind="manual")
    second_batch, second_run = persist_fixture(db, result, run_kind="weekly")
    finding_id_value = result["findings"][0]["finding_id"]

    assert db.execute("SELECT COUNT(*) FROM analysis_findings").fetchone()[0] == 1
    origin_run = db.execute(
        "SELECT run_id FROM analysis_findings WHERE finding_id=?",
        (finding_id_value,),
    ).fetchone()[0]
    assert origin_run == first_run["run_id"]
    assert second_run["run_id"] != origin_run
    assert ledger.finding_belongs_to_run(
        db,
        run_id=second_run["run_id"],
        finding_id_value=finding_id_value,
    )

    ledger.refresh_batch_hypotheses(
        db, batch_id=first_batch["batch_id"], created_at=CREATED
    )
    second_refresh = ledger.refresh_batch_hypotheses(
        db, batch_id=second_batch["batch_id"], created_at=CREATED
    )
    assert second_refresh["same_fingerprint_noops"] == 1
    assert db.execute("SELECT COUNT(*) FROM hypothesis_evaluations").fetchone()[0] == 1

    promoted = ledger.promote_verified_finding(
        db,
        run_id=second_run["run_id"],
        verified=sealed_replay(result),
        explicit=True,
        created_at=CREATED,
    )
    assert promoted["evaluation"]["created"] is False
    db.execute(
        "UPDATE analysis_runs SET outcome_mode='ordinal' WHERE run_id=?",
        (second_run["run_id"],),
    )
    with pytest.raises(ledger.LedgerError) as forged_membership:
        ledger.finding_belongs_to_run(
            db,
            run_id=second_run["run_id"],
            finding_id_value=finding_id_value,
        )
    assert forged_membership.value.code == "ledger_corrupt"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["meta"].update({"outcome": "forged.outcome"}),
        lambda payload: payload["meta"].update({"modes": ["forged-mode"]}),
        lambda payload: payload["meta"].update(
            {"input_fingerprint": sha256_id({"forged": "input"})}
        ),
        lambda payload: payload["meta"].update({"analysis_version": "forged-v9"}),
        lambda payload: payload["meta"].update({"registry_version": "forged-v9"}),
        lambda payload: payload["meta"].update(
            {"engine_sha256": sha256_id({"forged": "engine"})}
        ),
        lambda payload: payload["meta"].update(
            {"registry_sha256": sha256_id({"forged": "registry"})}
        ),
        lambda payload: payload["meta"]["requested_range"].update(
            {"from": "2025-12-31"}
        ),
        lambda payload: payload["meta"]["analysis_range"].update(
            {"from": "2025-12-31"}
        ),
        lambda payload: payload["meta"]["baseline_range"].update(
            {"from": "2025-12-30"}
        ),
        lambda payload: payload["coverage"].update({"outcome_eligible_n": 0}),
    ],
)
def test_run_membership_binds_authenticated_result_metadata_to_rows(db, mutate):
    result = phase4_result()
    _batch, run = persist_fixture(db, result)
    finding_id_value = result["findings"][0]["finding_id"]
    payload = json.loads(
        db.execute(
            "SELECT result_json FROM analysis_runs WHERE run_id=?",
            (run["run_id"],),
        ).fetchone()[0]
    )
    mutate(payload)
    result_json = canonical_json(payload)
    result_sha256 = sha256_id(
        {
            "contract_version": ledger.LEDGER_CONTRACT_VERSION,
            "kind": "analysis_result",
            "result": payload,
        }
    )
    db.execute(
        """UPDATE analysis_runs SET result_json=?,result_sha256=?
           WHERE run_id=?""",
        (result_json, result_sha256, run["run_id"]),
    )
    with pytest.raises(ledger.LedgerError) as corrupt:
        ledger.finding_belongs_to_run(
            db,
            run_id=run["run_id"],
            finding_id_value=finding_id_value,
        )
    assert corrupt.value.code == "ledger_corrupt"


def test_batch_declared_outcomes_are_authenticated_before_finalization(db):
    declared = [(OUTCOME, MODE), ("subjective.energy", "ordinal")]
    batch = ledger.create_analysis_batch(
        db,
        run_kind="manual",
        anchor_date="2026-03-31",
        outcome_selection="explicit_set",
        outcomes=declared,
        ranges=[{
            "range_role": "primary",
            "requested_range_kind": "bounded",
            "requested_from": "2026-01-01",
            "requested_to": "2026-03-31",
        }],
        registry_sha256_value=REGISTRY_HASH,
        started_at=CREATED,
    )
    ledger.start_analysis_run(
        db,
        batch_id=batch["batch_id"],
        range_id=batch["ranges"][0]["range_id"],
        outcome_key=OUTCOME,
        outcome_mode=MODE,
        batch_outcomes=declared,
        started_at=CREATED,
    )
    with pytest.raises(ledger.LedgerError) as incomplete:
        ledger.finalize_analysis_batch(
            db, batch_id=batch["batch_id"], completed_at=CREATED,
        )
    assert incomplete.value.code == "batch_incomplete"

    with pytest.raises(ledger.LedgerError) as undeclared:
        ledger.start_analysis_run(
            db,
            batch_id=batch["batch_id"],
            range_id=batch["ranges"][0]["range_id"],
            outcome_key="subjective.mood",
            outcome_mode="ordinal",
            batch_outcomes=declared,
            started_at=CREATED,
        )
    assert undeclared.value.code == "undeclared_outcome"


def test_batch_requires_every_range_outcome_cartesian_run(db):
    batch = ledger.create_analysis_batch(
        db,
        run_kind="manual",
        anchor_date="2026-03-31",
        outcome_selection="explicit_set",
        outcomes=[(OUTCOME, MODE)],
        ranges=[
            {
                "range_role": "primary",
                "requested_range_kind": "bounded",
                "requested_from": "2026-01-01",
                "requested_to": "2026-03-31",
            },
            {
                "range_role": "recent",
                "requested_range_kind": "bounded",
                "requested_from": "2026-03-01",
                "requested_to": "2026-03-31",
            },
        ],
        registry_sha256_value=REGISTRY_HASH,
        started_at=CREATED,
    )
    primary = next(
        item for item in batch["ranges"] if item["range_role"] == "primary"
    )
    ledger.start_analysis_run(
        db,
        batch_id=batch["batch_id"],
        range_id=primary["range_id"],
        outcome_key=OUTCOME,
        outcome_mode=MODE,
        batch_outcomes=[(OUTCOME, MODE)],
        started_at=CREATED,
    )
    with pytest.raises(ledger.LedgerError) as incomplete:
        ledger.finalize_analysis_batch(
            db, batch_id=batch["batch_id"], completed_at=CREATED,
        )
    assert incomplete.value.code == "batch_incomplete"


def test_terminal_batch_allows_exact_retries_but_no_new_children(db):
    result = phase4_result()
    batch, run = persist_fixture(db, result)
    primary = batch["ranges"][0]
    retried_range = ledger.add_analysis_range(
        db,
        batch_id=batch["batch_id"],
        range_role="primary",
        requested_range_kind="bounded",
        requested_from="2026-01-01",
        requested_to="2026-03-31",
    )
    assert retried_range["created"] is False
    retried_run = ledger.start_analysis_run(
        db,
        batch_id=batch["batch_id"],
        range_id=primary["range_id"],
        outcome_key=OUTCOME,
        outcome_mode=MODE,
        batch_outcomes=[(OUTCOME, MODE)],
        started_at=CREATED,
    )
    assert retried_run["created"] is False
    assert retried_run["run_id"] == run["run_id"]
    before_retry = db.total_changes
    finalized_retry = ledger.finalize_analysis_batch(
        db, batch_id=batch["batch_id"], completed_at="2026-12-31T00:00:00+00:00",
    )
    assert finalized_retry["changed"] is False
    assert db.total_changes == before_retry

    db.execute(
        "UPDATE analysis_batches SET run_count=99 WHERE batch_id=?",
        (batch["batch_id"],),
    )
    with pytest.raises(ledger.LedgerError) as drift:
        ledger.finalize_analysis_batch(
            db, batch_id=batch["batch_id"], completed_at=CREATED,
        )
    assert drift.value.code == "ledger_corrupt"
    db.execute(
        "UPDATE analysis_batches SET run_count=1 WHERE batch_id=?",
        (batch["batch_id"],),
    )

    with pytest.raises(ledger.LedgerError) as new_range:
        ledger.add_analysis_range(
            db,
            batch_id=batch["batch_id"],
            range_role="historical",
            requested_range_kind="bounded",
            requested_from="2025-10-01",
            requested_to="2025-12-31",
        )
    assert new_range.value.code == "terminal_batch"

    injected_range = sha256_id({"fixture": "terminal-new-range"})
    db.execute(
        """INSERT INTO analysis_range_requests(
             range_id,batch_id,range_role,requested_range_kind,
             requested_from,requested_to)
           VALUES(?,?,'historical','bounded','2025-10-01','2025-12-31')""",
        (injected_range, batch["batch_id"]),
    )
    with pytest.raises(ledger.LedgerError) as new_run:
        ledger.start_analysis_run(
            db,
            batch_id=batch["batch_id"],
            range_id=injected_range,
            outcome_key=OUTCOME,
            outcome_mode=MODE,
            batch_outcomes=[(OUTCOME, MODE)],
            started_at=CREATED,
        )
    assert new_run.value.code == "terminal_batch"


def test_monthly_refresh_applies_state_machine_only_to_primary_range(db):
    results = {
        "primary": phase4_result(
            requested_kind="all",
            start="2025-01-01",
            end="2026-07-22",
            input_tag="monthly-primary",
            eligible_n=400,
        ),
        "recent": phase4_result(
            start="2026-04-24",
            end="2026-07-22",
            input_tag="monthly-recent",
            eligible_n=90,
        ),
        "historical": phase4_result(
            start="2026-01-24",
            end="2026-04-23",
            input_tag="monthly-historical",
            eligible_n=90,
        ),
    }
    batch = ledger.create_analysis_batch(
        db,
        run_kind="monthly",
        anchor_date="2026-07-22",
        initiator_key="fixture/monthly-three-range-review",
        outcome_selection="explicit_set",
        outcomes=[(OUTCOME, MODE)],
        ranges=[
            {
                "range_role": "primary",
                "requested_range_kind": "all",
                "requested_from": None,
                "requested_to": None,
            },
            {
                "range_role": "recent",
                "requested_range_kind": "bounded",
                "requested_from": "2026-04-24",
                "requested_to": "2026-07-22",
            },
            {
                "range_role": "historical",
                "requested_range_kind": "bounded",
                "requested_from": "2026-01-24",
                "requested_to": "2026-04-23",
            },
        ],
        registry_sha256_value=REGISTRY_HASH,
        started_at=CREATED,
    )
    run_ids = {}
    for range_row in batch["ranges"]:
        role = range_row["range_role"]
        run = ledger.start_analysis_run(
            db,
            batch_id=batch["batch_id"],
            range_id=range_row["range_id"],
            outcome_key=OUTCOME,
            outcome_mode=MODE,
            batch_outcomes=[(OUTCOME, MODE)],
            started_at=CREATED,
        )
        terminal = ledger.persist_analysis_run(
            db,
            run_id=run["run_id"],
            verified=seal_analysis(results[role]),
            completed_at=CREATED,
        )
        run_ids[role] = terminal["run_id"]
    ledger.finalize_analysis_batch(
        db, batch_id=batch["batch_id"], completed_at=CREATED,
    )

    refreshed = ledger.refresh_batch_hypotheses(
        db, batch_id=batch["batch_id"], created_at=CREATED,
    )
    assert refreshed["hypotheses_created"] == 1
    assert refreshed["evaluations_created"] == 1
    evaluations = list(db.execute(
        "SELECT run_id FROM hypothesis_evaluations ORDER BY id",
    ))
    assert [row["run_id"] for row in evaluations] == [run_ids["primary"]]
    assert db.execute(
        "SELECT COUNT(*) FROM analysis_runs WHERE batch_id=?",
        (batch["batch_id"],),
    ).fetchone()[0] == 3


def test_direct_engine_path_persists_all_findings_beyond_display_top_and_strips_internal(
    db,
    monkeypatch,
):
    result = phase4_result()
    template = result["findings"][0]
    findings = []
    for index in range(101):
        finding = deepcopy(template)
        exposure_key = f"event.synthetic.{index:03d}"
        finding["exposure"]["components"][0]["exposure_key"] = exposure_key
        identity = {
            key: finding["exposure"]["components"][0][key]
            for key in ("exposure_key", "lag_days", "window_days", "transform")
        }
        finding["candidate_key"] = candidate_key(OUTCOME, MODE, [identity])
        finding["finding_id"] = finding_id(
            candidate_key=finding["candidate_key"],
            analysis_range=result["meta"]["analysis_range"],
            baseline_range=result["meta"]["baseline_range"],
            input_fingerprint=result["meta"]["input_fingerprint"],
        )
        finding["testing"]["family_size"] = 101
        finding["provenance"]["evidence_fingerprint"] = evidence_fingerprint(
            finding
        )
        finding["_definition"] = object()
        finding["_rows"] = [{"private": True}]
        findings.append(finding)
    result["findings"] = findings
    result["meta"]["candidate_family_sizes"] = {MODE: 101}
    result["coverage"]["modes"][MODE].update(
        {
            "generated_candidates": 101,
            "tested_candidates": 101,
            "family_size": 101,
        }
    )

    observed = {}

    def fake_checksum(definitions):
        observed["checksum_definitions"] = tuple(definitions)
        return REGISTRY_HASH.removeprefix("sha256:")

    def fake_analyze(conn, definitions, requested, context, **kwargs):
        observed["engine_definitions"] = tuple(definitions)
        observed["kwargs"] = kwargs
        return result

    monkeypatch.setattr(ledger, "registry_content_checksum", fake_checksum)
    monkeypatch.setattr(ledger.associations, "analyze_outcome", fake_analyze)
    definitions = (value for value in fixture_definitions(result))
    verified = ledger.compute_verified_analysis(
        db,
        definitions,
        None,
        None,
        outcome_key=OUTCOME,
        outcome_mode=MODE,
    )
    assert observed["checksum_definitions"] == observed["engine_definitions"]
    assert observed["kwargs"]["include_internal"] is True
    assert observed["kwargs"]["return_all_internal"] is True

    batch = ledger.create_analysis_batch(
        db,
        run_kind="manual",
        anchor_date="2026-03-31",
        outcome_selection="explicit_set",
        outcomes=[(OUTCOME, MODE)],
        ranges=[
            {
                "range_role": "primary",
                "requested_range_kind": "bounded",
                "requested_from": "2026-01-01",
                "requested_to": "2026-03-31",
            }
        ],
        registry_sha256_value=REGISTRY_HASH,
        started_at=CREATED,
    )
    run = ledger.start_analysis_run(
        db,
        batch_id=batch["batch_id"],
        range_id=batch["ranges"][0]["range_id"],
        outcome_key=OUTCOME,
        outcome_mode=MODE,
        batch_outcomes=[(OUTCOME, MODE)],
        started_at=CREATED,
    )
    terminal = ledger.persist_analysis_run(
        db,
        run_id=run["run_id"],
        verified=verified,
        completed_at=CREATED,
    )
    assert db.execute("SELECT COUNT(*) FROM analysis_findings").fetchone()[0] == 101
    assert len(json.loads(terminal["result_json"])["findings"]) == 101
    assert "_definition" not in terminal["result_json"]


def test_run_writer_rejects_plain_browser_payload_and_terminal_conflict(db):
    result = phase4_result()
    batch = ledger.create_analysis_batch(
        db,
        run_kind="manual",
        anchor_date="2026-03-31",
        outcome_selection="explicit_set",
        outcomes=[(OUTCOME, MODE)],
        ranges=[
            {
                "range_role": "primary",
                "requested_range_kind": "bounded",
                "requested_from": "2026-01-01",
                "requested_to": "2026-03-31",
            }
        ],
        registry_sha256_value=REGISTRY_HASH,
        started_at=CREATED,
    )
    run = ledger.start_analysis_run(
        db,
        batch_id=batch["batch_id"],
        range_id=batch["ranges"][0]["range_id"],
        outcome_key=OUTCOME,
        outcome_mode=MODE,
        batch_outcomes=[(OUTCOME, MODE)],
        started_at=CREATED,
    )
    with pytest.raises(ledger.LedgerError, match="direct Phase 4 engine seal") as exc:
        ledger.persist_analysis_run(db, run_id=run["run_id"], verified=result)
    assert exc.value.code == "unverified_engine_output"
    verified = seal_analysis(result)
    first = ledger.persist_analysis_run(
        db, run_id=run["run_id"], verified=verified, completed_at=CREATED
    )
    retry = ledger.persist_analysis_run(
        db, run_id=run["run_id"], verified=verified, completed_at=CREATED
    )
    assert first["persisted"] is True
    assert retry["persisted"] is False
    changed = phase4_result(input_tag="changed")
    with pytest.raises(ledger.LedgerError) as conflict:
        ledger.persist_analysis_run(
            db,
            run_id=run["run_id"],
            verified=seal_analysis(changed),
            completed_at=CREATED,
        )
    assert conflict.value.code == "terminal_run_conflict"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda value: value.update({"browser_effect": 999}), "invalid_engine_output"),
        (
            lambda value: value["findings"][0]["effect"].update(
                {"oriented_estimate": math.nan}
            ),
            "nonfinite_engine_output",
        ),
        (
            lambda value: value["meta"].update(
                {"engine_sha256": sha256_id({"stale": True})}
            ),
            "stale_engine_output",
        ),
        (
            lambda value: value["findings"][0]["sample"].update({"complete_n": 999}),
            "mismatched_engine_output",
        ),
        (
            lambda value: value["findings"][0]["provenance"].update(
                {"input_fingerprint": sha256_id({"other": True})}
            ),
            "mismatched_engine_output",
        ),
    ],
)
def test_private_engine_sealing_fails_closed(mutate, code):
    value = phase4_result()
    mutate(value)
    with pytest.raises(ledger.LedgerError) as exc:
        seal_analysis(value)
    assert exc.value.code == code


@pytest.mark.parametrize(
    "mutate",
    [
        lambda finding: finding["sample"].update(
            {"complete_n": 91, "missing_n": -1}
        ),
        lambda finding: finding["rates"].update({"risk_difference": 0.99}),
        lambda finding: finding["effect"].update({"ci95": [0.4, -0.1]}),
        lambda finding: finding["testing"].update({"p": 1.01}),
        lambda finding: finding["testing"].update({"q": 0.9}),
        lambda finding: finding["testing"].update({"p": 0.4, "q": 0.1}),
        lambda finding: finding["testing"].update({"family_size": 2}),
        lambda finding: finding["testing"].update(
            {"p": None, "q": None, "seed": None}
        ),
        lambda finding: (
            finding["effect"].update({"method": "bogus"}),
            finding.update({"rates": None}),
        ),
        lambda finding: finding["sample"].update(
            {"outcome_positive_n": 1, "outcome_negative_n": 89}
        ),
        lambda finding: finding["stability"].update({"full": 0.99}),
    ],
)
def test_private_engine_boundary_rejects_impossible_numerical_payloads(mutate):
    result = phase4_result()
    mutate(result["findings"][0])
    result["findings"][0]["provenance"]["evidence_fingerprint"] = (
        evidence_fingerprint(result["findings"][0])
    )
    with pytest.raises(ledger.LedgerError) as invalid:
        seal_analysis(result)
    assert invalid.value.code in {"invalid_engine_output", "mismatched_engine_output"}


@pytest.mark.parametrize(
    ("family_size", "q_value"),
    [(1, 0.02), (3, 0.04)],
)
def test_private_engine_boundary_enforces_bh_family_upper_bound(
    family_size,
    q_value,
):
    result = phase4_result()
    finding = result["findings"][0]
    finding["testing"].update(
        {"p": 0.01, "q": q_value, "family_size": family_size}
    )
    result["meta"]["candidate_family_sizes"] = {MODE: family_size}
    finding["provenance"]["evidence_fingerprint"] = evidence_fingerprint(
        finding
    )
    with pytest.raises(
        ledger.LedgerError, match="maximum possible family adjustment"
    ):
        seal_analysis(result)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["coverage"]["modes"][MODE].update(
            {"family_size": 2}
        ),
        lambda value: value["coverage"]["modes"][MODE].update(
            {"generated_candidates": 0}
        ),
        lambda value: value["coverage"]["modes"][MODE].update(
            {"insufficient_candidates": 1}
        ),
        lambda value: value["coverage"]["modes"][MODE].update(
            {"outcome_eligible_n": 89}
        ),
        lambda value: value["meta"].update(
            {"interaction_family_sizes": {}}
        ),
    ],
)
def test_private_engine_boundary_reconciles_family_coverage_and_finding_counts(
    mutate,
):
    result = phase4_result()
    mutate(result)
    with pytest.raises(ledger.LedgerError):
        seal_analysis(result)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda stability: stability.update({"status": "unstable"}),
        lambda stability: stability.update(
            {"status": "stable", "second_half": -stability["full"]}
        ),
        lambda stability: stability.update({"status": "insufficient"}),
    ],
)
def test_private_engine_boundary_recomputes_stability_status(mutate):
    result = phase4_result()
    finding = result["findings"][0]
    mutate(finding["stability"])
    finding["provenance"]["evidence_fingerprint"] = evidence_fingerprint(
        finding
    )
    with pytest.raises(
        ledger.LedgerError, match="chronological-half gates"
    ):
        seal_analysis(result)


def test_private_engine_boundary_rejects_invalid_generated_timestamp():
    result = phase4_result()
    result["meta"]["generated_at"] = "not-a-timestamp"
    with pytest.raises(ledger.LedgerError):
        seal_analysis(result)


def test_private_engine_boundary_validates_all_oriented_pair_rate_fields():
    result = phase4_pair_result()
    result["findings"][0]["rates"]["oriented_component_a_difference"] = (
        "not-a-number"
    )
    result["findings"][0]["provenance"]["evidence_fingerprint"] = (
        evidence_fingerprint(result["findings"][0])
    )
    with pytest.raises(ledger.LedgerError) as invalid:
        seal_analysis(result)
    assert invalid.value.code == "invalid_engine_output"


def test_private_engine_boundary_binds_descriptors_to_supplied_registry():
    result = phase4_result()
    definitions = fixture_definitions(result)
    result["findings"][0]["exposure"]["components"][0]["merge_rule"] = (
        "forged_merge"
    )
    result["findings"][0]["provenance"]["evidence_fingerprint"] = (
        evidence_fingerprint(result["findings"][0])
    )
    with pytest.raises(ledger.LedgerError) as mismatch:
        seal_analysis(result, definitions=definitions)
    assert mismatch.value.code == "mismatched_engine_output"


def test_private_engine_boundary_rejects_fractional_binary_group_successes():
    result = phase4_result()
    finding = result["findings"][0]
    finding["rates"].update(
        {
            "baseline": 0.51,
            "exposed": 0.73,
            "unexposed": 0.4,
            "risk_difference": 0.33,
            "risk_ratio": 0.73 / 0.4,
            "oriented_risk_difference": 0.33,
        }
    )
    finding["effect"].update(
        {"estimate": 0.33, "oriented_estimate": 0.33}
    )
    finding["stability"].update(
        {"full": 0.33, "first_half": 0.33, "second_half": 0.33}
    )
    finding["sample"].update(
        {"outcome_positive_n": 46, "outcome_negative_n": 44}
    )
    finding["provenance"]["evidence_fingerprint"] = evidence_fingerprint(finding)
    with pytest.raises(ledger.LedgerError, match="fractional outcome count"):
        seal_analysis(result)


def test_private_engine_boundary_rejects_unknown_stability_status():
    result = phase4_result(
        quality="exploratory_screen",
        eligible=True,
        effect=0.20,
        ci=(0.01, 0.39),
    )
    finding = result["findings"][0]
    finding["stability"]["status"] = "banana"
    finding["provenance"]["evidence_fingerprint"] = evidence_fingerprint(finding)
    with pytest.raises(ledger.LedgerError, match="stability.status"):
        seal_analysis(result)


def test_private_engine_boundary_rejects_invalid_stability_nullability():
    result = phase4_result()
    finding = result["findings"][0]
    finding["stability"]["first_half"] = None
    finding["provenance"]["evidence_fingerprint"] = evidence_fingerprint(finding)
    with pytest.raises(ledger.LedgerError, match="chronological-half"):
        seal_analysis(result)

    insufficient = phase4_result(
        effect=None,
        ci=None,
        quality="insufficient",
        eligible=False,
    )
    insufficient_finding = insufficient["findings"][0]
    insufficient_finding["stability"]["full"] = 0.0
    insufficient_finding["provenance"]["evidence_fingerprint"] = (
        evidence_fingerprint(insufficient_finding)
    )
    with pytest.raises(ledger.LedgerError, match="null insufficient"):
        seal_analysis(insufficient)


def test_private_engine_boundary_rejects_pair_stability_kind_mismatch():
    result = phase4_pair_result()
    finding = result["findings"][0]
    finding["stability"].update(
        {"status": "stable", "first_half": 0.25, "second_half": 0.25}
    )
    finding["provenance"]["evidence_fingerprint"] = evidence_fingerprint(finding)
    with pytest.raises(ledger.LedgerError, match="interaction stability"):
        seal_analysis(result)


@pytest.mark.parametrize(
    ("lag_days", "temporal_direction"),
    [
        (1, "same_day_or_order_unknown"),
        (0, "exposure_precedes_outcome"),
        (0, "same_day_ordered"),
    ],
)
def test_private_engine_boundary_binds_temporal_direction_to_lag(
    lag_days,
    temporal_direction,
):
    result = phase4_result()
    finding = result["findings"][0]
    component_value = finding["exposure"]["components"][0]
    component_value["lag_days"] = lag_days
    component_value["temporal_direction"] = temporal_direction
    identity = {
        key: component_value[key]
        for key in ("exposure_key", "lag_days", "window_days", "transform")
    }
    finding["candidate_key"] = candidate_key(OUTCOME, MODE, [identity])
    finding["finding_id"] = finding_id(
        candidate_key=finding["candidate_key"],
        analysis_range=result["meta"]["analysis_range"],
        baseline_range=result["meta"]["baseline_range"],
        input_fingerprint=result["meta"]["input_fingerprint"],
    )
    finding["provenance"]["evidence_fingerprint"] = evidence_fingerprint(finding)
    with pytest.raises(ledger.LedgerError, match="temporal_direction"):
        seal_analysis(result)


def test_zero_effect_accepts_consistent_negative_orientation_from_asymmetric_ci():
    result = phase4_result(
        effect=0.0,
        ci=(-0.1, 0.2),
        quality="insufficient",
        eligible=False,
    )
    finding = result["findings"][0]
    finding["outcome"]["direction"] = "lower_better"
    finding["effect"]["oriented_ci95"] = [-0.2, 0.1]
    finding["rates"].update(
        {
            "baseline": 0.5,
            "exposed": 0.5,
            "unexposed": 0.5,
            "risk_difference": 0.0,
            "risk_ratio": 1.0,
            "oriented_risk_difference": 0.0,
        }
    )
    finding["sample"]["outcome_positive_n"] = 45
    finding["sample"]["outcome_negative_n"] = 45
    finding["stability"].update(
        {"full": 0.0, "first_half": 0.0, "second_half": 0.0}
    )
    finding["provenance"]["evidence_fingerprint"] = evidence_fingerprint(finding)
    sealed = seal_analysis(result)
    assert sealed.payload["findings"][0]["effect"]["oriented_ci95"] == [-0.2, 0.1]


def test_no_public_arbitrary_json_sealer_or_stored_finding_promotion_api():
    for name in (
        "verify_phase4_analysis_output",
        "verify_phase4_finding_output",
        "VerifiedPhase4Analysis",
        "VerifiedPhase4Finding",
        "promote_finding",
    ):
        assert not hasattr(ledger, name)
        assert name not in ledger.__all__


def test_exploratory_screen_requires_explicit_promotion(db):
    result = phase4_result(
        quality="exploratory_screen",
        eligible=True,
        effect=0.20,
        ci=(0.01, 0.39),
    )
    batch, run = persist_fixture(db, result)
    auto = ledger.refresh_batch_hypotheses(
        db, batch_id=batch["batch_id"], created_at=CREATED
    )
    assert auto["hypotheses_created"] == 0
    promoted = ledger.promote_verified_finding(
        db,
        run_id=run["run_id"],
        verified=sealed_replay(result),
        explicit=True,
        created_at=CREATED,
    )
    evaluation = promoted["evaluation"]["evaluation"]
    assert evaluation["evidence_class"] == "initial_exploratory"
    assert evaluation["status"] == "exploratory"
    assert evaluation["confidence"] == "low"
    assert {
        (row["polarity"], row["evidence_kind"])
        for row in db.execute(
            """SELECT polarity,evidence_kind FROM hypothesis_evidence_items
               WHERE evaluation_id=?""",
            (evaluation["id"],),
        )
    } == {
        ("for", "same_direction_effect"),
        ("against", "weak_or_unstable"),
    }


def test_context_only_discovery_persists_without_auto_creating_hypothesis(db):
    result = context_only_result(input_tag="context-only-new")
    batch, run = persist_fixture(db, result)
    refreshed = ledger.refresh_batch_hypotheses(
        db, batch_id=batch["batch_id"], created_at=CREATED,
    )
    assert run["status"] == "completed"
    assert db.execute("SELECT COUNT(*) FROM analysis_findings").fetchone()[0] == 1
    assert refreshed["hypotheses_created"] == 0
    assert refreshed["evaluations_created"] == 0
    assert db.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0] == 0


def test_context_only_discovery_cannot_transition_existing_hypothesis(db):
    hypothesis_id, *_ = create_initial(db)
    before = latest_eval(db, hypothesis_id)
    result = context_only_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="context-only-existing",
    )
    batch, _ = persist_fixture(db, result, anchor="2026-06-30")
    refreshed = ledger.refresh_batch_hypotheses(
        db, batch_id=batch["batch_id"], created_at=CREATED,
    )
    after = latest_eval(db, hypothesis_id)
    assert refreshed["skipped_context_only"] == 1
    assert refreshed["evaluations_created"] == 0
    assert after["id"] == before["id"]
    assert after["status"] == "candidate"


@pytest.mark.parametrize(
    ("first_start", "first_end", "first_n", "next_start", "next_end"),
    [
        ("2026-01-01", "2026-04-30", 120, "2026-05-01", "2026-07-31"),
        ("2026-04-01", "2026-06-30", 90, "2026-07-01", "2026-09-30"),
    ],
)
def test_first_discovery_after_screen_is_candidate_before_any_replication(
    db,
    first_start,
    first_end,
    first_n,
    next_start,
    next_end,
):
    hypothesis_id = create_explicit_screen(db)
    first = phase4_result(
        start=first_start,
        end=first_end,
        eligible_n=first_n,
        input_tag=f"first-discovery-{first_end}",
    )
    first_batch, _ = persist_fixture(db, first, anchor=first_end)
    ledger.refresh_batch_hypotheses(
        db, batch_id=first_batch["batch_id"], created_at=CREATED
    )
    first_eval = latest_eval(db, hypothesis_id)
    assert first_eval["evidence_class"] == "initial_discovery_pass"
    assert first_eval["status"] == "candidate"
    assert first_eval["confidence"] == "low"

    second = phase4_result(
        start=next_start,
        end=next_end,
        input_tag=f"second-discovery-{next_end}",
    )
    second_batch, _ = persist_fixture(db, second, anchor=next_end)
    ledger.refresh_batch_hypotheses(
        db, batch_id=second_batch["batch_id"], created_at=CREATED
    )
    assert latest_eval(db, hypothesis_id)["status"] == "replicated"


def test_opposite_discovery_after_explicit_screen_rejects_with_against_evidence(db):
    hypothesis_id = create_explicit_screen(db)
    opposite = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="opposite-after-explicit-screen",
        effect=-0.22,
        ci=(-0.38, -0.06),
    )
    batch, _ = persist_fixture(db, opposite, anchor="2026-06-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=batch["batch_id"], created_at=CREATED,
    )
    evaluation = latest_eval(db, hypothesis_id)
    assert evaluation["evidence_class"] == "opposite_pass"
    assert evaluation["status"] == "rejected"
    assert evaluation["confidence"] == "insufficient"
    assert {
        (row["polarity"], row["evidence_kind"])
        for row in db.execute(
            """SELECT polarity,evidence_kind FROM hypothesis_evidence_items
               WHERE evaluation_id=?""",
            (evaluation["id"],),
        )
    } == {("against", "opposite_direction_effect")}


def test_nonpassing_opposite_after_screen_is_against_caveat_not_for_evidence(db):
    hypothesis_id = create_explicit_screen(db)
    opposite = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="uncertain-opposite-after-screen",
        effect=-0.22,
        ci=(-0.40, 0.05),
    )
    batch, _ = persist_fixture(db, opposite, anchor="2026-06-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=batch["batch_id"], created_at=CREATED,
    )
    evaluation = latest_eval(db, hypothesis_id)
    assert evaluation["evidence_class"] == "same_exploratory"
    assert evaluation["status"] == "exploratory"
    assert evaluation["confidence"] == "low"
    assert evaluation["transition_applied"] == 0
    assert {
        (row["polarity"], row["evidence_kind"])
        for row in db.execute(
            """SELECT polarity,evidence_kind FROM hypothesis_evidence_items
               WHERE evaluation_id=?""",
            (evaluation["id"],),
        )
    } == {("against", "weak_or_unstable")}


def test_screen_only_history_cannot_supply_a_weakening_reference(db):
    hypothesis_id = create_explicit_screen(db)
    weak = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="weak-before-discovery",
        effect=0.05,
        ci=(-0.05, 0.15),
        quality="insufficient",
        eligible=False,
    )
    batch, _ = persist_fixture(db, weak, anchor="2026-06-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=batch["batch_id"], created_at=CREATED
    )
    evaluation = latest_eval(db, hypothesis_id)
    assert evaluation["evidence_class"] == "same_exploratory"
    assert evaluation["status"] == "exploratory"
    assert evaluation["transition_applied"] == 0


def test_initial_discovery_and_same_fingerprint_are_idempotent(db):
    hypothesis_id, batch, _run, refresh = create_initial(db)
    assert refresh["hypotheses_created"] == 1
    evaluation = latest_eval(db, hypothesis_id)
    assert evaluation["evidence_class"] == "initial_discovery_pass"
    assert evaluation["status"] == "candidate"
    assert evaluation["confidence"] == "low"
    again = ledger.refresh_batch_hypotheses(
        db, batch_id=batch["batch_id"], created_at=CREATED
    )
    assert again["same_fingerprint_noops"] == 1
    assert (
        db.execute(
            "SELECT COUNT(*) FROM hypothesis_evaluations WHERE hypothesis_id=?",
            (hypothesis_id,),
        ).fetchone()[0]
        == 1
    )


def test_historical_backfill_cannot_create_forward_pointing_audit_chain(db):
    hypothesis_id, *_ = create_initial(db)
    later = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="completed-later",
    )
    later_batch, _ = persist_fixture(
        db,
        later,
        anchor="2026-06-30",
        completed_at="2026-07-01T00:00:00+00:00",
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=later_batch["batch_id"], created_at=CREATED
    )
    before = latest_eval(db, hypothesis_id)

    backfill = phase4_result(
        start="2026-01-15",
        end="2026-04-15",
        input_tag="completed-before-latest",
        eligible_n=91,
    )
    backfill_batch, _ = persist_fixture(
        db,
        backfill,
        anchor="2026-04-15",
        completed_at="2026-04-16T00:00:00+00:00",
    )
    with pytest.raises(ledger.LedgerError) as historical:
        ledger.refresh_batch_hypotheses(
            db, batch_id=backfill_batch["batch_id"], created_at=CREATED
        )
    assert historical.value.code == "historical_evaluation_order"
    assert latest_eval(db, hypothesis_id)["id"] == before["id"]


def test_overlapping_new_observations_strengthen_but_do_not_replicate(db):
    hypothesis_id, *_ = create_initial(
        db, start="2026-01-01", end="2026-03-31", eligible_n=90
    )
    next_result = phase4_result(
        start="2026-01-01",
        end="2026-04-30",
        input_tag="overlap",
        eligible_n=120,
        effect=0.22,
        ci=(0.08, 0.36),
    )
    batch, _ = persist_fixture(db, next_result, anchor="2026-04-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=batch["batch_id"], created_at="2026-05-01T00:00:00+00:00"
    )
    evaluation = latest_eval(db, hypothesis_id)
    assert evaluation["evidence_class"] == "same_pass_overlap"
    assert evaluation["status"] == "strengthening"
    assert evaluation["confidence"] == "moderate"
    assert evaluation["new_eligible_observations"] == 30


def test_later_nonoverlap_pass_replicates_and_sensitive_result_caps_confidence(db):
    hypothesis_id, *_ = create_initial(
        db, start="2026-01-01", end="2026-03-31"
    )
    result = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="held-out",
        effect=0.21,
        ci=(0.04, 0.38),
        confounder_sensitive=True,
    )
    batch, _ = persist_fixture(db, result, anchor="2026-06-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=batch["batch_id"], created_at="2026-07-01T00:00:00+00:00"
    )
    evaluation = latest_eval(db, hypothesis_id)
    assert evaluation["evidence_class"] == "same_pass_nonoverlap"
    assert evaluation["status"] == "replicated"
    assert evaluation["confidence"] == "moderate"
    kinds = {
        (row["polarity"], row["evidence_kind"])
        for row in db.execute(
            """SELECT polarity,evidence_kind FROM hypothesis_evidence_items
               WHERE evaluation_id=?""",
            (evaluation["id"],),
        )
    }
    assert kinds == {
        ("for", "same_direction_effect"),
        ("for", "nonoverlap_replication"),
        ("against", "confounder_sensitivity"),
    }


def test_same_direction_exploratory_screen_adds_caveat_without_transition(db):
    hypothesis_id, *_ = create_initial(db)
    result = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="same-screen",
        effect=0.18,
        ci=(0.03, 0.33),
        quality="exploratory_screen",
        eligible=True,
    )
    batch, _ = persist_fixture(db, result, anchor="2026-06-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=batch["batch_id"], created_at=CREATED
    )
    evaluation = latest_eval(db, hypothesis_id)
    assert evaluation["evidence_class"] == "same_exploratory"
    assert evaluation["status"] == "candidate"
    assert evaluation["confidence"] == "low"
    assert evaluation["transition_applied"] == 0
    assert {
        (row["polarity"], row["evidence_kind"])
        for row in db.execute(
            """SELECT polarity,evidence_kind FROM hypothesis_evidence_items
               WHERE evaluation_id=?""",
            (evaluation["id"],),
        )
    } == {
        ("for", "same_direction_effect"),
        ("against", "weak_or_unstable"),
    }


def test_overlapping_weak_window_weakens(db):
    hypothesis_id, *_ = create_initial(db)
    result = phase4_result(
        start="2026-02-01",
        end="2026-04-30",
        input_tag="weak",
        effect=0.05,
        ci=(-0.05, 0.15),
        quality="insufficient",
        eligible=False,
    )
    batch, _ = persist_fixture(db, result, anchor="2026-04-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=batch["batch_id"], created_at="2026-05-01T00:00:00+00:00"
    )
    evaluation = latest_eval(db, hypothesis_id)
    assert evaluation["evidence_class"] == "weakening_window"
    assert evaluation["status"] == "weakened"
    assert evaluation["confidence"] == "low"


def test_two_consecutive_disjoint_nulls_reject(db):
    hypothesis_id, *_ = create_initial(
        db, start="2026-01-01", end="2026-03-31"
    )
    for index, (start, end) in enumerate(
        [("2026-04-01", "2026-06-30"), ("2026-07-01", "2026-09-30")],
        1,
    ):
        result = phase4_result(
            start=start,
            end=end,
            input_tag=f"null-{index}",
            effect=0.02,
            ci=(-0.08, 0.12),
            quality="insufficient",
            eligible=False,
        )
        batch, _ = persist_fixture(db, result, anchor=end)
        ledger.refresh_batch_hypotheses(
            db,
            batch_id=batch["batch_id"],
            created_at=f"2026-{6 + 3 * index:02d}-01T00:00:00+00:00",
        )
    evaluations = [
        dict(row)
        for row in db.execute(
            """SELECT evidence_class,status,confidence,change_reason
               FROM hypothesis_evaluations WHERE hypothesis_id=? ORDER BY id""",
            (hypothesis_id,),
        )
    ]
    assert [item["evidence_class"] for item in evaluations] == [
        "initial_discovery_pass",
        "null_nonoverlap",
        "null_nonoverlap",
    ]
    assert evaluations[-2]["status"] == "weakened"
    assert evaluations[-1]["status"] == "rejected"
    assert evaluations[-1]["confidence"] == "insufficient"


def test_compatible_discovery_pass_resets_disjoint_null_sequence(db):
    hypothesis_id, *_ = create_initial(
        db, start="2026-01-01", end="2026-03-31",
    )
    first_null = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="reset-first-null",
        effect=0.02,
        ci=(-0.08, 0.12),
        quality="insufficient",
        eligible=False,
    )
    first_batch, _ = persist_fixture(
        db,
        first_null,
        anchor="2026-06-30",
        completed_at="2026-06-30T23:00:00+00:00",
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=first_batch["batch_id"], created_at=CREATED,
    )
    assert latest_eval(db, hypothesis_id)["change_reason"] == "first_nonoverlap_null"

    same_window_pass = phase4_result(
        start="2026-01-01",
        end="2026-03-31",
        input_tag="same-window-pass-resets-null",
        eligible_n=90,
    )
    pass_batch, _ = persist_fixture(
        db, same_window_pass, anchor="2026-03-31",
        completed_at="2026-07-01T00:00:00+00:00",
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=pass_batch["batch_id"], created_at=CREATED,
    )
    middle = latest_eval(db, hypothesis_id)
    assert middle["evidence_class"] == "same_exploratory"
    assert (
        json.loads(middle["effect_summary_json"])["quality"]["tier"]
        == "exploratory_unreplicated"
    )
    assert {
        row["evidence_kind"]
        for row in db.execute(
            """SELECT evidence_kind FROM hypothesis_evidence_items
               WHERE evaluation_id=?""",
            (middle["id"],),
        )
    } == {"same_direction_effect"}

    later_null = phase4_result(
        start="2026-07-01",
        end="2026-09-30",
        input_tag="reset-second-null",
        effect=0.01,
        ci=(-0.09, 0.11),
        quality="insufficient",
        eligible=False,
    )
    later_batch, _ = persist_fixture(
        db,
        later_null,
        anchor="2026-09-30",
        completed_at="2026-09-30T23:00:00+00:00",
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=later_batch["batch_id"], created_at=CREATED,
    )
    final = latest_eval(db, hypothesis_id)
    assert final["evidence_class"] == "null_nonoverlap"
    assert final["change_reason"] == "first_nonoverlap_null"
    assert final["status"] == "weakened"


def test_zero_new_discovery_pass_becomes_latest_weakening_reference(db):
    hypothesis_id, *_ = create_initial(db)
    stronger_pass = phase4_result(
        start="2026-01-01",
        end="2026-03-31",
        input_tag="latest-reference-stronger-pass",
        effect=0.40,
        ci=(0.20, 0.60),
        eligible_n=90,
    )
    pass_batch, _ = persist_fixture(
        db,
        stronger_pass,
        anchor="2026-03-31",
        completed_at="2026-06-01T00:00:00+00:00",
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=pass_batch["batch_id"], created_at=CREATED,
    )
    reference = latest_eval(db, hypothesis_id)
    assert reference["evidence_class"] == "same_exploratory"

    later_screen = phase4_result(
        start="2026-02-01",
        end="2026-04-30",
        input_tag="latest-reference-weaker-screen",
        effect=0.15,
        ci=(0.02, 0.28),
        quality="exploratory_screen",
        eligible=True,
        eligible_n=90,
    )
    screen_batch, _ = persist_fixture(
        db,
        later_screen,
        anchor="2026-04-30",
        completed_at="2026-07-01T00:00:00+00:00",
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=screen_batch["batch_id"], created_at=CREATED,
    )
    weakened = latest_eval(db, hypothesis_id)
    assert weakened["evidence_class"] == "weakening_window"
    assert weakened["change_reason"] == "effect_below_half_reference"
    assert weakened["comparison_evaluation_id"] == reference["id"]
    assert weakened["status"] == "weakened"


@pytest.mark.parametrize("origin_n", [90, 60])
def test_narrowed_discovery_window_cannot_launder_prior_coverage_into_replication(
    db,
    origin_n,
):
    hypothesis_id, *_ = create_initial(
        db,
        start="2026-01-01",
        end="2026-06-30",
        eligible_n=origin_n,
        input_tag=f"coverage-origin-{origin_n}",
    )
    original = latest_eval(db, hypothesis_id)
    narrowed = phase4_result(
        start="2026-01-01",
        end="2026-03-31",
        eligible_n=90,
        input_tag=f"coverage-narrowed-{origin_n}",
    )
    narrowed_batch, _ = persist_fixture(
        db, narrowed, anchor="2026-03-31",
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=narrowed_batch["batch_id"], created_at=CREATED,
    )
    middle = latest_eval(db, hypothesis_id)
    assert middle["evidence_class"] == (
        "same_exploratory" if origin_n == 90 else "same_pass_overlap"
    )
    assert middle["new_eligible_observations"] == (
        0 if origin_n == 90 else 30
    )

    reused = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        eligible_n=91,
        input_tag=f"coverage-reused-{origin_n}",
    )
    reused_batch, _ = persist_fixture(db, reused, anchor="2026-06-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=reused_batch["batch_id"], created_at=CREATED,
    )
    final = latest_eval(db, hypothesis_id)
    assert final["evidence_class"] != "same_pass_nonoverlap"
    assert final["status"] != "replicated"
    assert final["comparison_evaluation_id"] == original["id"]


def test_same_range_density_growth_is_provably_new_strengthening_evidence(db):
    hypothesis_id, *_ = create_initial(
        db,
        start="2026-01-01",
        end="2026-03-31",
        eligible_n=60,
        input_tag="same-range-sparse",
    )
    denser = phase4_result(
        start="2026-01-01",
        end="2026-03-31",
        eligible_n=90,
        input_tag="same-range-denser",
    )
    denser_batch, _ = persist_fixture(
        db, denser, anchor="2026-03-31",
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=denser_batch["batch_id"], created_at=CREATED,
    )
    evaluation = latest_eval(db, hypothesis_id)
    assert evaluation["evidence_class"] == "same_pass_overlap"
    assert evaluation["status"] == "strengthening"
    assert evaluation["confidence"] == "moderate"
    assert evaluation["new_eligible_observations"] == 30


def test_initial_screen_coverage_cannot_be_laundered_by_narrower_discovery(db):
    screen = phase4_result(
        start="2026-01-01",
        end="2026-06-30",
        input_tag="screen-coverage-origin",
        quality="exploratory_screen",
        eligible=True,
        eligible_n=90,
    )
    screen_batch, screen_run = persist_fixture(
        db, screen, anchor="2026-06-30",
    )
    promoted = ledger.promote_verified_finding(
        db,
        run_id=screen_run["run_id"],
        verified=sealed_replay(screen),
        explicit=True,
        created_at=CREATED,
    )
    hypothesis_id = promoted["hypothesis_id"]
    discovery = phase4_result(
        start="2026-01-01",
        end="2026-03-31",
        input_tag="screen-narrow-discovery",
        eligible_n=90,
    )
    discovery_batch, _ = persist_fixture(
        db, discovery, anchor="2026-03-31",
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=discovery_batch["batch_id"], created_at=CREATED,
    )
    assert latest_eval(db, hypothesis_id)["evidence_class"] == (
        "initial_discovery_pass"
    )

    reused = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="screen-reused-covered-window",
        eligible_n=91,
    )
    reused_batch, _ = persist_fixture(db, reused, anchor="2026-06-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=reused_batch["batch_id"], created_at=CREATED,
    )
    final = latest_eval(db, hypothesis_id)
    assert final["evidence_class"] != "same_pass_nonoverlap"
    assert final["status"] != "replicated"


def test_equal_end_narrowing_cannot_manufacture_overlap_novelty(db):
    hypothesis_id, *_ = create_initial(
        db,
        start="2026-01-01",
        end="2026-12-31",
        eligible_n=365,
        input_tag="full-year-coverage",
    )
    narrowed = phase4_result(
        start="2026-10-03",
        end="2026-12-31",
        eligible_n=90,
        input_tag="equal-end-narrowing",
    )
    narrowed_batch, _ = persist_fixture(
        db, narrowed, anchor="2026-12-31",
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=narrowed_batch["batch_id"], created_at=CREATED,
    )
    assert latest_eval(db, hypothesis_id)["new_eligible_observations"] == 0

    contained = phase4_result(
        start="2026-07-01",
        end="2026-12-31",
        eligible_n=184,
        input_tag="equal-end-contained",
    )
    contained_batch, _ = persist_fixture(
        db, contained, anchor="2026-12-31",
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=contained_batch["batch_id"], created_at=CREATED,
    )
    final = latest_eval(db, hypothesis_id)
    assert final["new_eligible_observations"] == 0
    assert final["evidence_class"] == "same_exploratory"
    assert final["status"] == "candidate"


def test_discovery_overlapping_prior_null_is_not_replication(db):
    hypothesis_id, *_ = create_initial(
        db, start="2026-01-01", end="2026-03-31",
    )
    null = phase4_result(
        start="2026-07-01",
        end="2026-09-30",
        input_tag="coverage-null-window",
        effect=0.02,
        ci=(-0.08, 0.12),
        quality="insufficient",
        eligible=False,
        eligible_n=92,
    )
    null_batch, _ = persist_fixture(db, null, anchor="2026-09-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=null_batch["batch_id"], created_at=CREATED,
    )
    assert latest_eval(db, hypothesis_id)["evidence_class"] == "null_nonoverlap"

    reused = phase4_result(
        start="2026-07-01",
        end="2026-09-30",
        input_tag="coverage-pass-over-null",
        eligible_n=92,
    )
    reused_batch, _ = persist_fixture(db, reused, anchor="2026-09-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=reused_batch["batch_id"], created_at=CREATED,
    )
    final = latest_eval(db, hypothesis_id)
    assert final["evidence_class"] == "same_exploratory"
    assert final["status"] == "weakened"


def test_pass_overlapping_discovery_strengthens_even_when_furthest_window_is_null(
    db,
):
    hypothesis_id, *_ = create_initial(
        db, start="2026-01-01", end="2026-03-31", eligible_n=90,
    )
    discovery = latest_eval(db, hypothesis_id)
    null = phase4_result(
        start="2026-07-01",
        end="2026-09-30",
        input_tag="furthest-null-before-overlap",
        effect=0.02,
        ci=(-0.08, 0.12),
        quality="insufficient",
        eligible=False,
        eligible_n=92,
    )
    null_batch, _ = persist_fixture(db, null, anchor="2026-09-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=null_batch["batch_id"], created_at=CREATED,
    )
    assert latest_eval(db, hypothesis_id)["evidence_class"] == "null_nonoverlap"

    overlapping_pass = phase4_result(
        start="2026-02-01",
        end="2026-06-30",
        input_tag="overlap-discovery-before-null",
        eligible_n=150,
    )
    pass_batch, _ = persist_fixture(
        db, overlapping_pass, anchor="2026-06-30",
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=pass_batch["batch_id"], created_at=CREATED,
    )
    final = latest_eval(db, hypothesis_id)
    assert final["evidence_class"] == "same_pass_overlap"
    assert final["status"] == "strengthening"
    assert final["confidence"] == "moderate"
    assert final["new_eligible_observations"] == 91
    assert final["comparison_evaluation_id"] == discovery["id"]


def test_overlapping_null_does_not_increment_disjoint_null_count(db):
    hypothesis_id, *_ = create_initial(db)
    overlapping = phase4_result(
        start="2026-02-01",
        end="2026-04-30",
        input_tag="overlapping-null",
        effect=0.02,
        ci=(-0.08, 0.12),
        quality="insufficient",
        eligible=False,
    )
    overlap_batch, _ = persist_fixture(db, overlapping, anchor="2026-04-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=overlap_batch["batch_id"], created_at=CREATED
    )
    assert latest_eval(db, hypothesis_id)["evidence_class"] == "weakening_window"

    held_out = phase4_result(
        start="2026-05-01",
        end="2026-07-31",
        input_tag="first-true-null",
        effect=0.01,
        ci=(-0.09, 0.11),
        quality="insufficient",
        eligible=False,
    )
    held_batch, _ = persist_fixture(db, held_out, anchor="2026-07-31")
    ledger.refresh_batch_hypotheses(
        db, batch_id=held_batch["batch_id"], created_at=CREATED
    )
    evaluation = latest_eval(db, hypothesis_id)
    assert evaluation["evidence_class"] == "null_nonoverlap"
    assert evaluation["change_reason"] == "first_nonoverlap_null"
    assert evaluation["status"] == "weakened"


@pytest.mark.parametrize("interruption", ["dormancy", "incompatible"])
def test_dormancy_and_incompatibility_do_not_reset_consecutive_nulls(
    db,
    interruption,
):
    hypothesis_id, *_ = create_initial(db)
    first_null = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag=f"null-before-{interruption}",
        effect=0.02,
        ci=(-0.08, 0.12),
        quality="insufficient",
        eligible=False,
    )
    first_batch, _ = persist_fixture(db, first_null, anchor="2026-06-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=first_batch["batch_id"], created_at=CREATED
    )
    first_null_evaluation = latest_eval(db, hypothesis_id)

    if interruption == "dormancy":
        no_finding = phase4_result(
            start="2026-07-01",
            end="2026-09-30",
            input_tag="dormant-between-nulls",
            effect=None,
            ci=None,
            quality="insufficient",
            eligible=False,
            finding=False,
        )
        middle_batch, _ = persist_fixture(
            db,
            no_finding,
            run_kind="monthly",
            anchor="2026-09-30",
        )
        ledger.refresh_batch_hypotheses(
            db,
            batch_id=middle_batch["batch_id"],
            dormancy_reason="dormant_no_eligible_data",
            created_at=CREATED,
        )
    else:
        incompatible = phase4_result(
            start="2026-07-01",
            end="2026-09-30",
            input_tag="incompatible-between-nulls",
        )
        middle_batch, _ = persist_legacy_version_fixture(
            db,
            incompatible,
            analysis_version="outcome-v2",
            anchor="2026-09-30",
        )
        ledger.refresh_batch_hypotheses(
            db, batch_id=middle_batch["batch_id"], created_at=CREATED
        )
        assert latest_eval(db, hypothesis_id)["evidence_class"] == "incompatible_version"

    second_null = phase4_result(
        start="2026-10-01",
        end="2026-12-31",
        input_tag=f"null-after-{interruption}",
        effect=0.01,
        ci=(-0.09, 0.11),
        quality="insufficient",
        eligible=False,
    )
    second_batch, _ = persist_fixture(db, second_null, anchor="2026-12-31")
    ledger.refresh_batch_hypotheses(
        db, batch_id=second_batch["batch_id"], created_at=CREATED
    )
    final = latest_eval(db, hypothesis_id)
    assert final["evidence_class"] == "null_nonoverlap"
    assert final["change_reason"] == "second_consecutive_nonoverlap_null"
    assert final["status"] == "rejected"
    assert final["comparison_evaluation_id"] == first_null_evaluation["id"]
    assert (
        json.loads(final["change_conditions"])["prior_null_evaluation_id"]
        == first_null_evaluation["id"]
    )


def test_discovery_pass_resets_consecutive_null_count(db):
    hypothesis_id, *_ = create_initial(
        db, start="2026-01-01", end="2026-03-31"
    )
    windows = [
        ("2026-04-01", "2026-06-30", "null-a", 0.02, (-0.08, 0.12), "insufficient", False),
        ("2026-07-01", "2026-09-30", "pass-reset", 0.21, (0.05, 0.37), "exploratory_unreplicated", True),
        ("2026-10-01", "2026-12-31", "null-b", 0.01, (-0.09, 0.11), "insufficient", False),
    ]
    for start, end, tag, effect, ci, quality, eligible in windows:
        result = phase4_result(
            start=start,
            end=end,
            input_tag=tag,
            effect=effect,
            ci=ci,
            quality=quality,
            eligible=eligible,
        )
        batch, _ = persist_fixture(db, result, anchor=end)
        ledger.refresh_batch_hypotheses(
            db, batch_id=batch["batch_id"], created_at=CREATED
        )
    evaluation = latest_eval(db, hypothesis_id)
    assert evaluation["evidence_class"] == "null_nonoverlap"
    assert evaluation["status"] == "weakened"
    assert evaluation["change_reason"] == "first_nonoverlap_null"


def test_significantly_opposite_pass_rejects_and_rejected_is_terminal(db):
    hypothesis_id, *_ = create_initial(db)
    opposite = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="opposite",
        effect=-0.22,
        ci=(-0.38, -0.06),
    )
    batch, _ = persist_fixture(db, opposite, anchor="2026-06-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=batch["batch_id"], created_at=CREATED
    )
    assert latest_eval(db, hypothesis_id)["status"] == "rejected"
    recovery = phase4_result(
        start="2026-07-01",
        end="2026-09-30",
        input_tag="would-recover",
        effect=0.30,
        ci=(0.12, 0.48),
    )
    batch2, _ = persist_fixture(db, recovery, anchor="2026-09-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=batch2["batch_id"], created_at=CREATED
    )
    terminal = latest_eval(db, hypothesis_id)
    assert terminal["status"] == "rejected"
    assert terminal["change_reason"] == "later_nonoverlap_replication"
    assert terminal["transition_applied"] == 0


@pytest.mark.parametrize(
    ("form", "expected_class", "expected_reason", "expected_items"),
    [
        (
            "same_pass",
            "same_pass_nonoverlap",
            "later_nonoverlap_replication",
            {
                ("for", "same_direction_effect"),
                ("for", "nonoverlap_replication"),
                ("against", "confounder_sensitivity"),
            },
        ),
        (
            "screen",
            "same_exploratory",
            "exploratory_caveat_only",
            {
                ("for", "same_direction_effect"),
                ("against", "weak_or_unstable"),
            },
        ),
        (
            "weakening",
            "weakening_window",
            "effect_below_half_reference",
            {
                ("for", "same_direction_effect"),
                ("against", "weak_or_unstable"),
            },
        ),
        (
            "null",
            "null_nonoverlap",
            "first_nonoverlap_null",
            {("against", "null_window")},
        ),
        (
            "opposite",
            "opposite_pass",
            "significantly_opposite_pass",
            {("against", "opposite_direction_effect")},
        ),
        (
            "incompatible",
            "incompatible_version",
            "semantic_version_incompatible",
            {("against", "method_incompatibility")},
        ),
        (
            "dormant",
            "dormant_stale_prerequisite",
            "dormant_stale_prerequisite",
            set(),
        ),
    ],
)
def test_rejected_terminal_preserves_true_reason_and_evidence_mapping(
    db,
    form,
    expected_class,
    expected_reason,
    expected_items,
):
    hypothesis_id = create_rejected(db)
    if form == "dormant":
        result = phase4_result(
            start="2026-07-01",
            end="2026-09-30",
            input_tag="post-rejection-dormant",
            effect=None,
            ci=None,
            quality="insufficient",
            eligible=False,
            finding=False,
        )
        batch, _ = persist_fixture(
            db, result, run_kind="monthly", anchor="2026-09-30"
        )
        ledger.refresh_batch_hypotheses(
            db,
            batch_id=batch["batch_id"],
            dormancy_reason="dormant_stale_prerequisite",
            created_at=CREATED,
        )
    else:
        options = {
            "same_pass": {
                "start": "2026-07-01",
                "end": "2026-09-30",
                "effect": 0.22,
                "ci": (0.06, 0.38),
                "confounder_sensitive": True,
            },
            "screen": {
                "start": "2026-07-01",
                "end": "2026-09-30",
                "effect": 0.18,
                "ci": (0.03, 0.33),
                "quality": "exploratory_screen",
                "eligible": True,
            },
            "weakening": {
                "start": "2026-02-01",
                "end": "2026-04-30",
                "effect": 0.05,
                "ci": (-0.05, 0.15),
                "quality": "insufficient",
                "eligible": False,
            },
            "null": {
                "start": "2026-07-01",
                "end": "2026-09-30",
                "effect": 0.02,
                "ci": (-0.08, 0.12),
                "quality": "insufficient",
                "eligible": False,
            },
            "opposite": {
                "start": "2026-07-01",
                "end": "2026-09-30",
                "effect": -0.24,
                "ci": (-0.40, -0.08),
            },
            "incompatible": {
                "start": "2026-07-01",
                "end": "2026-09-30",
                "effect": 0.22,
                "ci": (0.06, 0.38),
            },
        }[form]
        result = phase4_result(
            input_tag=f"post-rejection-{form}",
            **options,
        )
        if form == "incompatible":
            batch, _ = persist_legacy_version_fixture(
                db,
                result,
                analysis_version="outcome-v2",
                anchor=options["end"],
            )
        else:
            batch, _ = persist_fixture(db, result, anchor=options["end"])
        ledger.refresh_batch_hypotheses(
            db, batch_id=batch["batch_id"], created_at=CREATED
        )

    evaluation = latest_eval(db, hypothesis_id)
    assert evaluation["evidence_class"] == expected_class
    assert evaluation["change_reason"] == expected_reason
    assert evaluation["status"] == "rejected"
    assert evaluation["confidence"] == "insufficient"
    assert evaluation["transition_applied"] == 0
    conditions = json.loads(evaluation["change_conditions"])
    assert conditions["rejected_terminal"] is True
    items = {
        (row["polarity"], row["evidence_kind"])
        for row in db.execute(
            """SELECT polarity,evidence_kind FROM hypothesis_evidence_items
               WHERE evaluation_id=?""",
            (evaluation["id"],),
        )
    }
    assert items == expected_items


def test_incompatible_version_retains_status_then_forms_new_lineage(db):
    hypothesis_id, *_ = create_initial(db)
    first = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="v2-first",
    )
    batch, _ = persist_legacy_version_fixture(
        db,
        first,
        analysis_version="outcome-v2",
        anchor="2026-06-30",
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=batch["batch_id"], created_at=CREATED
    )
    incompatible = latest_eval(db, hypothesis_id)
    assert incompatible["evidence_class"] == "incompatible_version"
    assert incompatible["status"] == "candidate"
    assert incompatible["compatible_with_prior"] == 0

    second = phase4_result(
        start="2026-07-01",
        end="2026-09-30",
        input_tag="v2-second",
    )
    batch2, _ = persist_legacy_version_fixture(
        db,
        second,
        analysis_version="outcome-v2",
        anchor="2026-09-30",
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=batch2["batch_id"], created_at=CREATED
    )
    compatible = latest_eval(db, hypothesis_id)
    assert compatible["evidence_class"] == "same_pass_nonoverlap"
    assert compatible["status"] == "replicated"
    assert compatible["compatible_with_prior"] == 1


def test_incompatible_no_finding_has_priority_over_monthly_dormancy(db):
    hypothesis_id, *_ = create_initial(db)
    before = latest_eval(db, hypothesis_id)
    no_finding = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="v2-no-finding",
        effect=None,
        ci=None,
        quality="insufficient",
        eligible=False,
        finding=False,
    )
    batch, _ = persist_legacy_version_fixture(
        db,
        no_finding,
        analysis_version="outcome-v2",
        anchor="2026-06-30",
        run_kind="monthly",
    )
    refreshed = ledger.refresh_batch_hypotheses(
        db,
        batch_id=batch["batch_id"],
        dormancy_reason="dormant_no_eligible_data",
        created_at=CREATED,
    )
    after = latest_eval(db, hypothesis_id)
    assert refreshed["incompatible_version_noops"] == 1
    assert refreshed["dormant_created"] == 0
    assert refreshed["evaluations_created"] == 0
    assert after["id"] == before["id"]
    assert after["status"] == "candidate"
    assert after["confidence"] == "low"


def test_synthesis_replays_incompatible_version_finding_with_stored_version(db):
    hypothesis_id, *_ = create_initial(db)
    changed = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="v2-synthesis-replay",
    )
    batch, run = persist_legacy_version_fixture(
        db,
        changed,
        analysis_version="outcome-v2",
        anchor="2026-06-30",
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=batch["batch_id"], created_at=CREATED,
    )
    evaluation = latest_eval(db, hypothesis_id)
    assert evaluation["evidence_class"] == "incompatible_version"
    fingerprint = synthesis.synthesis_evidence_fingerprint(
        db,
        analysis_batch_id=batch["batch_id"],
        run_refs=[{"run_id": run["run_id"], "purpose": "primary"}],
        finding_refs=[{
            "finding_id": evaluation["finding_id"],
            "role": "primary",
        }],
        hypothesis_refs=[{
            "hypothesis_id": hypothesis_id,
            "evaluation_id": evaluation["id"],
            "role": "changed",
        }],
    )
    assert fingerprint.startswith("sha256:")


def test_registry_semantics_require_canonical_audited_compatibility_proof(db):
    hypothesis_id, *_ = create_initial(db)
    proof = ledger.canonical_semantic_compatibility_proof(
        from_analysis_version=ANALYSIS_VERSION,
        from_registry_version=REGISTRY_VERSION,
        to_analysis_version=ANALYSIS_VERSION,
        to_registry_version="feature-registry-v2",
        reason_code="frozen_semantics_comparison",
        evidence_sha256=sha256_id({"compatibility": "reviewed"}),
    )
    compatible_result = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="registry-v2",
    )
    compatible_batch, _ = persist_legacy_version_fixture(
        db,
        compatible_result,
        analysis_version=ANALYSIS_VERSION,
        registry_version="feature-registry-v2",
        anchor="2026-06-30",
    )
    ledger.refresh_batch_hypotheses(
        db,
        batch_id=compatible_batch["batch_id"],
        compatibility_proofs=[proof],
        created_at=CREATED,
    )
    evaluation = latest_eval(db, hypothesis_id)
    assert evaluation["evidence_class"] == "same_pass_nonoverlap"
    audit = json.loads(evaluation["change_conditions"])["semantic_compatibility"]
    assert audit["proof"] == proof
    assert audit["from"]["registry_version"] == REGISTRY_VERSION
    assert audit["to"]["registry_version"] == "feature-registry-v2"

    changed_again = phase4_result(
        start="2026-07-01",
        end="2026-09-30",
        input_tag="registry-v3-no-proof",
    )
    changed_batch, _ = persist_legacy_version_fixture(
        db,
        changed_again,
        analysis_version=ANALYSIS_VERSION,
        registry_version="feature-registry-v3",
        anchor="2026-09-30",
    )
    ledger.refresh_batch_hypotheses(
        db, batch_id=changed_batch["batch_id"], created_at=CREATED
    )
    assert latest_eval(db, hypothesis_id)["evidence_class"] == "incompatible_version"

    tampered = {**proof, "reason_code": "different"}
    with pytest.raises(ledger.LedgerError) as invalid:
        ledger.refresh_batch_hypotheses(
            db,
            batch_id=changed_batch["batch_id"],
            compatibility_proofs=[tampered],
            created_at=CREATED,
        )
    assert invalid.value.code == "validation_error"


@pytest.mark.parametrize(
    "dormancy_reason",
    ["dormant_no_eligible_data", "dormant_stale_prerequisite"],
)
def test_monthly_dormancy_is_not_negative_evidence_and_future_uses_prior_substantive(
    db,
    dormancy_reason,
):
    hypothesis_id, *_ = create_initial(db)
    no_finding = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="no-eligible",
        effect=None,
        ci=None,
        quality="insufficient",
        eligible=False,
        finding=False,
    )
    batch, _ = persist_fixture(
        db, no_finding, run_kind="monthly", anchor="2026-06-30"
    )
    refresh = ledger.refresh_batch_hypotheses(
        db,
        batch_id=batch["batch_id"],
        dormancy_reason=dormancy_reason,
        created_at=CREATED,
    )
    assert refresh["dormant_created"] == 1
    dormant = latest_eval(db, hypothesis_id)
    assert dormant["evidence_class"] == dormancy_reason
    assert dormant["status"] == "dormant"
    assert dormant["confidence"] == "insufficient"
    dormant_provenance = json.loads(dormant["effect_summary_json"])["provenance"]
    assert dormant_provenance == {
        "analysis_version": ANALYSIS_VERSION,
        "registry_version": REGISTRY_VERSION,
        "engine_sha256": engine_sha256(),
        "registry_sha256": REGISTRY_HASH,
        "input_fingerprint": no_finding["meta"]["input_fingerprint"],
    }
    assert (
        db.execute(
            "SELECT COUNT(*) FROM hypothesis_evidence_items WHERE evaluation_id=?",
            (dormant["id"],),
        ).fetchone()[0]
        == 0
    )
    later = phase4_result(
        start="2026-07-01",
        end="2026-09-30",
        input_tag="post-dormancy",
        effect=0.23,
        ci=(0.06, 0.40),
    )
    batch2, _ = persist_fixture(db, later, anchor="2026-09-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=batch2["batch_id"], created_at=CREATED
    )
    assert latest_eval(db, hypothesis_id)["status"] == "replicated"


def test_dormant_insufficient_finding_preserves_real_sample_and_provenance(db):
    hypothesis_id, *_ = create_initial(db)
    insufficient = phase4_result(
        start="2026-04-01",
        end="2026-06-30",
        input_tag="dormant-insufficient-audit",
        effect=None,
        ci=None,
        quality="insufficient",
        eligible=False,
        finding=True,
    )
    batch, _ = persist_fixture(
        db,
        insufficient,
        run_kind="monthly",
        anchor="2026-06-30",
    )
    ledger.refresh_batch_hypotheses(
        db,
        batch_id=batch["batch_id"],
        dormancy_reason="dormant_stale_prerequisite",
        created_at=CREATED,
    )
    evaluation = latest_eval(db, hypothesis_id)
    assert evaluation["finding_id"] is None
    assert json.loads(evaluation["sample_size_json"])["eligible_n"] == 90
    summary = json.loads(evaluation["effect_summary_json"])
    assert summary["effect"]["method"] is None
    assert summary["provenance"] == {
        "analysis_version": ANALYSIS_VERSION,
        "registry_version": REGISTRY_VERSION,
        "engine_sha256": engine_sha256(),
        "registry_sha256": REGISTRY_HASH,
        "input_fingerprint": insufficient["meta"]["input_fingerprint"],
    }
    conditions = json.loads(evaluation["change_conditions"])
    assert conditions["semantic_compatibility"]["compatible"] is True


def test_evidence_and_hypothesis_tables_are_physically_append_only(db):
    hypothesis_id, *_ = create_initial(db)
    for table in (
        "hypotheses",
        "hypothesis_components",
        "hypothesis_evaluations",
        "hypothesis_evidence_items",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="append_only_table"):
            db.execute(f"UPDATE {table} SET rowid=rowid")
        with pytest.raises(sqlite3.IntegrityError, match="append_only_table"):
            db.execute(f"DELETE FROM {table}")
    triggers = {
        row[0]
        for row in db.execute(
            """SELECT name FROM sqlite_master
               WHERE type='trigger' AND name LIKE '%_no_%'"""
        )
    }
    assert len(triggers) == 10
    assert f"hypotheses_no_update" in triggers
    assert hypothesis_id


def test_annotations_are_derived_append_only_and_supersede_only_current_leaf(db):
    hypothesis_id, *_ = create_initial(db)
    first = ledger.append_annotation(
        db,
        hypothesis_id=hypothesis_id,
        annotation_kind="owner_note",
        content="Track whether the pattern survives travel.",
        source="owner",
        created_at=CREATED,
    )
    retry = ledger.append_annotation(
        db,
        hypothesis_id=hypothesis_id,
        annotation_kind="owner_note",
        content="Track whether the pattern survives travel.",
        source="owner",
        created_at="2026-01-02T00:00:00+00:00",
    )
    assert retry["annotation_id"] == first["annotation_id"]
    assert retry["created"] is False
    second = ledger.append_annotation(
        db,
        hypothesis_id=hypothesis_id,
        annotation_kind="owner_note",
        content="Travel check is now the next priority.",
        source="owner",
        supersedes_id=first["annotation_id"],
        created_at="2026-01-03T00:00:00+00:00",
    )
    second_retry = ledger.append_annotation(
        db,
        hypothesis_id=hypothesis_id,
        annotation_kind="owner_note",
        content="Travel check is now the next priority.",
        source="owner",
        supersedes_id=first["annotation_id"],
        created_at="2026-01-04T00:00:00+00:00",
    )
    assert second_retry["annotation_id"] == second["annotation_id"]
    assert second_retry["created"] is False
    with pytest.raises(ledger.LedgerError) as stale:
        ledger.append_annotation(
            db,
            hypothesis_id=hypothesis_id,
            annotation_kind="owner_note",
            content="Invalid second branch.",
            source="owner",
            supersedes_id=first["annotation_id"],
            created_at="2026-01-05T00:00:00+00:00",
        )
    assert stale.value.code == "stale_annotation"
    brief = ledger.hypothesis_brief(db, hypothesis_id)
    assert [item["annotation_id"] for item in brief["latest_annotations"]] == [
        second["annotation_id"]
    ]
    with pytest.raises(sqlite3.IntegrityError, match="append_only_table"):
        db.execute("UPDATE hypothesis_annotations SET content='changed'")


def test_annotation_rejects_model_provenance_or_numeric_override_shape(db):
    hypothesis_id, *_ = create_initial(db)
    with pytest.raises(ledger.LedgerError) as oversized:
        ledger.append_annotation(
            db,
            hypothesis_id=hypothesis_id,
            evaluation_id=10**100,
            annotation_kind="owner_note",
            content="This row id must fail before SQLite binding.",
            source="owner",
            created_at=CREATED,
        )
    assert oversized.value.code == "validation_error"
    with pytest.raises(ledger.LedgerError, match="valid UTF-8"):
        ledger.append_annotation(
            db,
            hypothesis_id=hypothesis_id,
            annotation_kind="owner_note",
            content="\ud800",
            source="owner",
            created_at=CREATED,
        )
    with pytest.raises(ledger.LedgerError, match="context_version 2"):
        ledger.append_annotation(
            db,
            hypothesis_id=hypothesis_id,
            annotation_kind="mechanism",
            content="A proposal, not evidence.",
            source="glm_synthesis",
            synthesis_id=sha256_id({"s": 1}),
            context_version="v1",
            prompt_sha256=sha256_id({"p": 1}),
            model_id="other/model",
            provider="openrouter",
            created_at=CREATED,
        )
    assert (
        "effect" not in ledger.append_annotation.__annotations__
        and "confidence" not in ledger.append_annotation.__annotations__
        and "status" not in ledger.append_annotation.__annotations__
    )


def insert_synthesis(
    db,
    *,
    batch_id,
    synthesis_id,
    status="completed",
    context_version="2",
    prompt_sha256=None,
    hypothesis_id=None,
    evaluation_id=None,
):
    prompt_sha256 = prompt_sha256 or sha256_id({"prompt": synthesis_id})
    db.execute(
        """INSERT INTO synthesis_runs(
             synthesis_id,analysis_batch_id,cadence,reason_code,cutoff_date,
             evidence_fingerprint,context_version,prompt_sha256,model_id,provider,
             finding_ids_json,hypothesis_ids_json,narrative_md,rendered_md,status,
             no_message_reason_code,created_at,completed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            synthesis_id,
            batch_id,
            "manual",
            "manual_review",
            "2026-03-31",
            sha256_id({"evidence": synthesis_id}),
            context_version,
            prompt_sha256,
            "z-ai/glm-5.2",
            "openrouter",
            "[]",
            "[]",
            "Interpretation only.",
            "Rendered.",
            status,
            None,
            CREATED,
            CREATED if status == "completed" else None,
        ),
    )
    if hypothesis_id is not None:
        evaluation_id = evaluation_id or latest_eval(db, hypothesis_id)["id"]
        db.execute(
            """INSERT INTO synthesis_hypothesis_refs(
                 synthesis_id,hypothesis_id,evaluation_id,role)
               VALUES(?,?,?,'active')""",
            (synthesis_id, hypothesis_id, evaluation_id),
        )
    return prompt_sha256


def test_glm_annotation_requires_completed_exact_synthesis_provenance(db):
    hypothesis_id, batch, *_ = create_initial(db)
    referenced_evaluation = latest_eval(db, hypothesis_id)["id"]
    synthesis_id = sha256_id({"synthesis": "completed"})
    prompt = insert_synthesis(
        db,
        batch_id=batch["batch_id"],
        synthesis_id=synthesis_id,
        hypothesis_id=hypothesis_id,
        evaluation_id=referenced_evaluation,
    )
    identity = ledger.canonical_annotation_identity(
        hypothesis_id=hypothesis_id,
        evaluation_id=referenced_evaluation,
        annotation_kind="mechanism",
        content="A bounded proposed mechanism.",
        source="glm_synthesis",
        synthesis_id=synthesis_id,
        context_version="2",
        prompt_sha256=prompt,
        model_id="z-ai/glm-5.2",
        provider="openrouter",
        supersedes_id=None,
    )
    annotation = ledger.append_annotation(
        db,
        hypothesis_id=hypothesis_id,
        evaluation_id=referenced_evaluation,
        annotation_kind="mechanism",
        content="A bounded proposed mechanism.",
        source="glm_synthesis",
        synthesis_id=synthesis_id,
        context_version="2",
        prompt_sha256=prompt,
        model_id="z-ai/glm-5.2",
        provider="openrouter",
        annotation_id=identity["annotation_id"],
        created_at=CREATED,
    )
    assert annotation["input_sha256"] == identity["input_sha256"]

    with pytest.raises(ledger.LedgerError) as missing_evaluation:
        ledger.append_annotation(
            db,
            hypothesis_id=hypothesis_id,
            annotation_kind="alternative",
            content="Missing the exact referenced evaluation.",
            source="glm_synthesis",
            synthesis_id=synthesis_id,
            context_version="2",
            prompt_sha256=prompt,
            model_id="z-ai/glm-5.2",
            provider="openrouter",
            created_at=CREATED,
        )
    assert missing_evaluation.value.code == "mismatched_synthesis_reference"

    with pytest.raises(ledger.LedgerError) as model_owner_note:
        ledger.append_annotation(
            db,
            hypothesis_id=hypothesis_id,
            evaluation_id=referenced_evaluation,
            annotation_kind="owner_note",
            content="A model cannot impersonate an owner note.",
            source="glm_synthesis",
            synthesis_id=synthesis_id,
            context_version="2",
            prompt_sha256=prompt,
            model_id="z-ai/glm-5.2",
            provider="openrouter",
            created_at=CREATED,
        )
    assert model_owner_note.value.code == "validation_error"

    later = phase4_result(
        start="2026-01-01",
        end="2026-04-30",
        eligible_n=120,
        input_tag="annotation-later-evaluation",
    )
    later_batch, _ = persist_fixture(db, later, anchor="2026-04-30")
    ledger.refresh_batch_hypotheses(
        db, batch_id=later_batch["batch_id"], created_at=CREATED
    )
    unreferenced_evaluation = latest_eval(db, hypothesis_id)["id"]
    with pytest.raises(ledger.LedgerError) as wrong_evaluation:
        ledger.append_annotation(
            db,
            hypothesis_id=hypothesis_id,
            evaluation_id=unreferenced_evaluation,
            annotation_kind="alternative",
            content="Not grounded in the referenced evaluation.",
            source="glm_synthesis",
            synthesis_id=synthesis_id,
            context_version="2",
            prompt_sha256=prompt,
            model_id="z-ai/glm-5.2",
            provider="openrouter",
            created_at=CREATED,
        )
    assert wrong_evaluation.value.code == "mismatched_synthesis_reference"

    with pytest.raises(ledger.LedgerError) as mismatch:
        ledger.append_annotation(
            db,
            hypothesis_id=hypothesis_id,
            annotation_kind="alternative",
            content="Mismatched prompt.",
            source="glm_synthesis",
            synthesis_id=synthesis_id,
            context_version="2",
            prompt_sha256=sha256_id({"other": "prompt"}),
            model_id="z-ai/glm-5.2",
            provider="openrouter",
            created_at=CREATED,
        )
    assert mismatch.value.code == "mismatched_synthesis_provenance"

    running_id = sha256_id({"synthesis": "running"})
    running_prompt = insert_synthesis(
        db,
        batch_id=batch["batch_id"],
        synthesis_id=running_id,
        status="running",
    )
    with pytest.raises(ledger.LedgerError) as incomplete:
        ledger.append_annotation(
            db,
            hypothesis_id=hypothesis_id,
            annotation_kind="alternative",
            content="Too early.",
            source="glm_synthesis",
            synthesis_id=running_id,
            context_version="2",
            prompt_sha256=running_prompt,
            model_id="z-ai/glm-5.2",
            provider="openrouter",
            created_at=CREATED,
        )
    assert incomplete.value.code == "incomplete_synthesis"


def test_list_and_brief_are_json_safe_and_evidence_comes_from_immutable_findings(db):
    hypothesis_id, *_ = create_initial(db)
    listing = ledger.list_hypotheses(
        db, status="candidate", outcome_key=OUTCOME, limit=10
    )
    assert listing["items"][0]["hypothesis_id"] == hypothesis_id
    brief = ledger.hypothesis_brief(db, hypothesis_id)
    assert brief["hypothesis"]["latest_evaluation"]["status"] == "candidate"
    assert brief["evidence_for"][0]["finding"]["sample"]["eligible_n"] == 90
    encoded = canonical_json(brief)
    assert json.loads(encoded)["contract_version"] == ledger.LEDGER_CONTRACT_VERSION


def test_hypothesis_cursor_is_absent_at_exact_end_after_status_filtering(db):
    first_id, *_ = create_initial(db)
    exact = ledger.list_hypotheses(db, status="candidate", limit=1)
    assert [item["hypothesis_id"] for item in exact["items"]] == [first_id]
    assert exact["next_before"] is None

    second = phase4_result(
        input_tag="pagination-second-candidate",
        exposure_key="nutrition.protein_g",
    )
    second_batch, _ = persist_fixture(db, second)
    ledger.refresh_batch_hypotheses(
        db, batch_id=second_batch["batch_id"], created_at=CREATED,
    )
    screen = phase4_result(
        input_tag="pagination-exploratory",
        exposure_key="training.session.occurred",
        quality="exploratory_screen",
        eligible=True,
    )
    _screen_batch, screen_run = persist_fixture(db, screen)
    ledger.promote_verified_finding(
        db,
        run_id=screen_run["run_id"],
        verified=sealed_replay(screen),
        explicit=True,
        created_at=CREATED,
    )

    expected = ledger.list_hypotheses(
        db, status="candidate", limit=100,
    )["items"]
    assert len(expected) == 2
    page_one = ledger.list_hypotheses(db, status="candidate", limit=1)
    assert page_one["items"] == expected[:1]
    assert page_one["next_before"] == expected[0]["hypothesis_id"]
    page_two = ledger.list_hypotheses(
        db,
        status="candidate",
        limit=1,
        before=page_one["next_before"],
    )
    assert page_two["items"] == expected[1:]
    assert page_two["next_before"] is None


def test_all_time_no_data_and_bounded_sparse_ranges_are_honest(db):
    all_result = phase4_result(
        start=None,
        end=None,
        requested_kind="all",
        input_tag="all-empty",
        eligible_n=0,
        effect=None,
        ci=None,
        quality="insufficient",
        eligible=False,
        finding=False,
    )
    # The fixture baseline needs the exact all-no-data null range shape.
    all_result["meta"]["analysis_range"] = {"kind": "all", "from": None, "to": None}
    all_result["meta"]["baseline_range"] = {"kind": "all", "from": None, "to": None}
    batch, run = persist_fixture(
        db, all_result, anchor="2026-01-01"
    )
    assert run["status"] == "no_data"
    assert run["range_resolution"] == "all_no_data"
    assert run["analysis_from"] is None and run["analysis_to"] is None
    assert batch

    sparse = phase4_result(
        start="2026-01-01",
        end="2026-01-10",
        input_tag="bounded-sparse",
        eligible_n=10,
        effect=None,
        ci=None,
        quality="insufficient",
        eligible=False,
        finding=False,
    )
    _, sparse_run = persist_fixture(db, sparse, anchor="2026-01-10")
    assert sparse_run["status"] == "insufficient_data"
    assert sparse_run["range_resolution"] == "bounded_exact"
    assert (sparse_run["analysis_from"], sparse_run["analysis_to"]) == (
        "2026-01-01",
        "2026-01-10",
    )
