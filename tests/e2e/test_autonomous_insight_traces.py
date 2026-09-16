"""Phase 8 deterministic scenario trace replay and boundary validation."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from app import auth as auth_mod
from app import bridge, create_app
from app.routes import chat as chat_routes

from .autonomous_trace_harness import (
    FIXTURE_ROOT,
    TRACE_CONTRACT,
    assert_no_sensitive_fixture_text,
    build_trace,
    fixture_paths,
    load_fixture,
    sha256_json,
)


def test_exact_ten_canonical_secret_free_scenario_fixtures():
    paths = fixture_paths()
    assert [path.name.split("-", 1)[0] for path in paths] == [
        f"{index:02d}" for index in range(1, 11)
    ]
    assert len(paths) == 10
    scenario_ids = set()
    statuses = set()
    for path in paths:
        fixture = load_fixture(path)
        assert fixture["scenario_id"] not in scenario_ids
        scenario_ids.add(fixture["scenario_id"])
        statuses.add(fixture["expected_status"])
        assert fixture["range"]["from"] <= fixture["range"]["to"]
        assert_no_sensitive_fixture_text(fixture)
    assert {
        "exploratory",
        "supported",
        "insufficient",
        "no_novelty",
        "logic_not_implemented",
    } <= statuses


@pytest.mark.parametrize("fixture_path", fixture_paths(), ids=lambda path: path.stem)
def test_each_trace_replays_exactly(
    fixture_path: Path,
    tmp_path: Path,
):
    fixture = load_fixture(fixture_path)
    trace = build_trace(fixture, tmp_path / f"{fixture['scenario_id']}.db")
    assert trace["trace_contract"] == TRACE_CONTRACT
    assert trace["result"]["status"] == fixture["expected_status"]
    assert trace["model_boundary"]["invoked"] is False
    assert trace["model_boundary"]["fixture_narrative"]["origin"] == (
        "fixture_generated/not_live_model"
    )
    assert trace["model_boundary"]["fixture_narrative"][
        "numeric_or_status_authority"
    ] is False
    assert trace["notification_boundary"]["sender_invoked"] is False
    assert all(
        item["invoked"] is True
        for item in trace["execution"]["actual_contract_calls"]
    )
    assert all(
        item["invoked_as_cli_or_subprocess"] is False
        for item in trace["execution"][
            "canonical_contract_references_not_invoked_as_cli"
        ]
    )
    assert all(
        item["equivalent_real_contracts_recorded"] is False
        and item["not_invoked_reason"]
        for item in trace["execution"][
            "canonical_contract_references_not_invoked_as_cli"
        ]
    )
    assert all(
        item["executed"] is True
        for item in trace["execution"]["declared_api_references"]
    )
    assert trace["execution"]["actual_api_exchanges"]
    assert trace["panel_proof"]["actual_screenshot_review"] is False
    assert trace["panel_proof"]["screenshot_review"] is False
    assert trace["panel_proof"]["static_structure_is_visual_review"] is False
    assert trace["panel_proof"]["server_rendered_proof"] is False
    assert (
        trace["panel_proof"]["canonical_api_replay"][
            "canonical_response_sha256"
        ].startswith("sha256:")
    )
    assert trace["contracts"]["analysis_range_plan_version"] == (
        "analysis-range-plan-v1"
    )
    assert trace["contracts"]["cadence_range_plan_version"] == (
        "cadence-range-plan-v1"
    )
    assert trace["contracts"]["analysis_contract_version"] == (
        "outcome-associations-v1"
    )
    assert trace["contracts"]["analysis_version"] == "outcome-v1"
    assert trace["contracts"]["synthesis_context_version"] == "2"
    assert trace["contracts"]["orchestration_context_version"] == "2"
    assert trace["contracts"]["panel_conversation_context_version"] == 1
    assert trace["ledger_boundary"]["ledger_rows_sha256"].startswith("sha256:")
    assert trace["rollback_state"]["database"]["integrity"] == {
        "quick_check": "ok",
        "foreign_key_violations": 0,
    }
    assert trace["rollback_state"]["panel_database"]["integrity"] == {
        "quick_check": "ok",
        "foreign_key_violations": 0,
    }
    assert trace["rollback_state"]["panel_database"][
        "state_sha256"
    ].startswith("sha256:")
    assert trace["rollback_state"]["temporary_database_count"] == 2
    assert trace["rollback_state"]["recovery"] == (
        "discard both temporary databases"
    )
    assert trace["rollback_state"]["external_mutations"] == []
    assert set(trace["narrative"]) == {
        "Hypothesis",
        "Outcome",
        "Exposure/pattern",
        "Direction/timing",
        "Observed result",
        "Sample size/coverage",
        "Evidence for",
        "Evidence against",
        "Alternative explanations",
        "Stability",
        "Confidence",
        "What changes conclusion",
        "One cheap next experiment",
        "Safety/causality note",
    }
    result = trace["result"]
    key = fixture["scenario_key"]
    panel_replay = trace["panel_proof"]["canonical_api_replay"]
    if key != "conversation_isolation":
        assert panel_replay[
            "all_results_match_exact_subcommand_payloads"
        ] is True
        exact_call = next(
            item
            for item in trace["execution"]["actual_contract_calls"]
            if item["arguments"].get("purpose")
            == "exact_panel_bridge_payload"
        )
        assert exact_call["invoked"] is True
        assert exact_call["result_digest_scope"] == "full_return_value"
        panel_result = trace["execution"]["actual_api_exchanges"][0][
            "response"
        ]["result"]
        assert sha256_json(panel_result) != sha256_json(result)
        if key in {
            "food_green_day",
            "social_mood",
            "workout_next_day",
            "food_social_pair",
            "quarterly_asymmetry",
        }:
            assert panel_result["contract_version"] == (
                "outcome-associations-v1"
            )
            assert panel_result["meta"]["min_n"] == 30
            assert panel_result["meta"]["interactions"] == "none"
            assert panel_result["meta"]["top"] == 30
            assert exact_call["arguments"]["full_registry"] is True
        elif key in {"running_restart_pain", "insufficient_data"}:
            assert panel_result["meta"]["readiness_version"] == (
                "data-readiness-v1"
            )
            assert exact_call["arguments"]["full_registry"] is True
    if key in {"food_green_day", "social_mood", "workout_next_day"}:
        discovery = result["registry_discovery"]["full_registry_analysis"]
        assert discovery["caller_exposure_filter"] is None
        assert discovery["caller_adapter_filter"] is None
        assert discovery["caller_source_table_filter"] is None
    if key in {"workout_next_day", "running_restart_pain"}:
        manifest_sync = next(
            row
            for row in trace["fixture"]["input_manifest"]["rows"]
            if row["table"] == "source_sync_runs"
        )
        assert set(manifest_sync) == {
            "table",
            "source",
            "started_at",
            "completed_at",
            "status",
            "coverage_from",
            "coverage_to",
            "rows_seen",
            "rows_written",
            "details_json",
        }
        assert {
            field: value
            for field, value in manifest_sync.items()
            if field != "table"
        } == trace["source_provenance_completeness_freshness"][
            "source_sync_rows"
        ][0]
    if key == "food_green_day":
        assert result["analysis"]["null_relationship_control"][
            "hypothesis_eligible"
        ] is False
        assert result["registry_discovery"][
            "identity_normalization_control"
        ]["normalized_to_one_identity"] is True
        assert result["analysis"]["source_transition_control"][
            "warning_present"
        ] is True
        assert trace["source_provenance_completeness_freshness"][
            "day_rating_three_way"
        ]["all_three_preserved"] is True
    elif key == "food_social_pair":
        assert result["pair_gate_proof"]["every_cell_strictly_over_10"] is True
        assert result["sparse_negative_control"]["status"] == "suppressed"
        assert result["notification_outbox_proof"]["external_sender_calls"] == []
        scope = trace["panel_proof"]["result_scope"]
        assert scope["status"] == "suppressed"
        assert scope["reason_code"] == (
            "canonical_panel_route_interactions_none"
        )
        assert scope["actual_api_contains_pair_finding"] is False
        assert scope["actual_api_interactions_argument"] == "none"
        assert scope["static_structure_reachable_from_current_pair_api"] is False
        assert scope["static_structure_is_rendered_review"] is False
        assert scope["pair_ui_rendering_claimed"] is False
        assert all(
            scope["static_multi_component_card_structure"][field]
            for field in (
                "joins_component_names_with_plus",
                "iterates_every_component",
            )
        )
    elif key == "running_restart_pain":
        assert result["missing_rows_do_not_prove_break"] is True
        assert result["missing_coverage_observed_markers"] == []
        scope = trace["panel_proof"]["result_scope"]
        assert scope["status"] == "suppressed"
        assert scope["actual_api_contains_running_pain_timeline"] is False
        assert scope["timeline_ui_rendering_claimed"] is False
    elif key == "quarterly_asymmetry":
        assert set(result["measurement_distinctions"]) == {
            "measured_strength",
            "measured_control",
            "measured_mobility",
            "training_exposure",
            "body_measurement",
        }
        assert result["muscle_size_claimed_from_strength_or_exposure"] is False
        assert trace["execution"]["actual_api_exchanges"][0]["request"][
            "path_and_query"
        ].startswith(
            "/api/insights/outcomes?"
            "outcome=pain.nrs.anterior-knee.left&mode=ordinal"
        )
    elif key == "insufficient_data":
        assert result["findings"] == []
        assert result["measurement_recommendation_only"] is True
        assert trace["execution"]["actual_api_exchanges"][0]["bridge_call"][
            "args"
        ][-2:] == ["--outcome", "pain.nrs.anterior-knee.left"]
    elif key == "reversal":
        assert [
            item["evidence_class"] for item in result["ledger_evaluations"]
        ] == [
            "initial_discovery_pass",
            "same_pass_nonoverlap",
            "opposite_pass",
        ]
        assert result["overwrite_permitted"] is False
        assert trace["model_boundary"]["eligible_preparation_available"] is True
        assert trace["model_boundary"]["input_sha256"] == (
            trace["model_boundary"]["preparation_provenance"][
                "preparation_sha256"
            ]
        )
        assert trace["model_boundary"]["input"]["structured_slots"][
            "numeric_authority"
        ] == "stored_structured_evidence_only"
        assert panel_result["contract_version"] == "hypothesis-ledger-v1"
        assert panel_result["hypothesis"]["hypothesis_id"] == (
            result["hypothesis_id"]
        )
    elif key == "weekly_no_novelty":
        assert result["first_baseline"]["reason_code"] == (
            "bootstrap_baseline_required"
        )
        assert result["later_unchanged"]["reason_code"] == (
            "no_eligible_novel_evidence"
        )
        assert result["outbox_rows"] == []
        assert result["manual_baseline"]["narrative_section_headings"] == [
            "Hypothesis",
            "Outcome",
            "Exposure/pattern",
            "Direction/timing",
            "Observed result",
            "Sample size/coverage",
            "Evidence for",
            "Evidence against",
            "Alternative explanations",
            "Stability",
            "Confidence",
            "What changes conclusion",
            "One cheap next experiment",
            "Safety/causality note",
        ]
        assert [
            item["request"]["path_and_query"]
            for item in trace["execution"]["actual_api_exchanges"]
        ] == [
            "/api/insights/syntheses?limit=10",
            "/api/insights/runs?limit=10",
        ]
        assert trace["model_boundary"]["eligible_preparation_available"] is True
        assert trace["model_boundary"]["input_sha256"] == (
            trace["model_boundary"]["preparation_provenance"][
                "preparation_sha256"
            ]
        )
        assert trace["model_boundary"]["input"]["structured_slots"][
            "numeric_authority"
        ] == "stored_structured_evidence_only"
        assert trace["execution"]["actual_api_exchanges"][0]["response"][
            "result"
        ]["contract"] == "synthesis-v1"
        assert trace["execution"]["actual_api_exchanges"][1]["response"][
            "result"
        ]["contract_version"] == "insight-orchestrator-v1"
        assert {
            row["created_at"]
            for row in trace["execution"]["actual_api_exchanges"][0][
                "response"
            ]["result"]["syntheses"]
        } == {
            "2026-07-04T08:00:00+00:00",
            "2026-07-12T08:00:00+00:00",
            "2026-07-18T08:00:00+00:00",
        }
    elif key == "conversation_isolation":
        assert result["sessions"]["distinct"] is True
        assert result["telegram_used"] is False
        assert result["ambiguous_delivery"]["external_attempts"] == 1
        labels = [
            item["label"]
            for item in trace["execution"]["actual_api_exchanges"]
        ]
        assert labels == [
            "create_general",
            "create_pain",
            "send_general",
            "send_pain",
            "idempotent_complete_retry",
            "uncertain_first_attempt",
            "uncertain_retry_blocked",
            "archive_pain",
            "resume_pain",
            "general_message_history",
            "pain_message_history",
        ]
        assert result["message_histories"]["general"]["messages"]
        assert result["message_histories"]["pain"]["messages"]
        assert {
            item["turn_id"]
            for item in result["message_histories"]["general"]["messages"]
        }.isdisjoint({
            item["turn_id"]
            for item in result["message_histories"]["pain"]["messages"]
        })
        assert trace["rollback_state"]["panel_database"]["row_counts"] == {
            "chat_conversations": 2,
            "chat_log": 5,
            "settings": 1,
            "sessions": 1,
        }
        assert trace["rollback_state"]["panel_database"][
            "delivery_status_counts"
        ] == {"complete": 4, "uncertain": 1}
        assert trace["rollback_state"]["panel_database"]["lens_counts"] == {
            "general": 1,
            "pain": 1,
        }
        assert trace["rollback_state"]["panel_database"][
            "conversation_origin_counts"
        ] == {
            "legacy_seeded_by_panel_schema": 0,
            "fixture_generated": 2,
        }
        assert trace["rollback_state"]["panel_database"][
            "archived_counts"
        ] == {"active": 2}
    if key not in {"reversal", "weekly_no_novelty"}:
        assert trace["model_boundary"]["eligible_preparation_available"] is False
        assert trace["model_boundary"]["input"] is None
        assert trace["model_boundary"]["input_sha256"] is None
    assert_no_sensitive_fixture_text(trace)
    assert sha256_json(trace) == fixture["expected_trace_sha256"]


def test_trace_manifest_pins_fixture_and_trace_digests():
    manifest_path = FIXTURE_ROOT / "manifest.json"
    raw = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(raw)
    assert json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ) + "\n" == raw
    assert manifest["trace_contract"] == TRACE_CONTRACT
    assert len(manifest["scenarios"]) == 10
    by_file = {item["fixture_file"]: item for item in manifest["scenarios"]}
    assert set(by_file) == {path.name for path in fixture_paths()}
    for path in fixture_paths():
        fixture = load_fixture(path)
        pinned = by_file[path.name]
        assert pinned["fixture_sha256"] == (
            "sha256:" + __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        )
        assert pinned["trace_sha256"] == fixture["expected_trace_sha256"]
        assert set(pinned) == {
            "fixture_file", "fixture_sha256", "status", "trace_sha256"
        }
        assert pinned["status"] == fixture["expected_status"]


def _authed_conversation_client(tmp_path, monkeypatch):
    calls = []
    ids = iter(("1" * 32, "2" * 32))
    updated = iter(range(1_800_000_000_000, 1_800_000_000_100))

    def conversation_id_hex(length):
        assert length == 16
        return next(ids)

    monkeypatch.setattr(
        chat_routes,
        "secrets",
        SimpleNamespace(token_hex=conversation_id_hex),
    )
    monkeypatch.setattr(chat_routes, "_next_updated_at", lambda _db: next(updated))

    def runner(subcmd, *args, **kwargs):
        calls.append((subcmd, args, kwargs))
        return {"ok": True, "reply": "Synthetic scoped reply"}

    monkeypatch.setattr(bridge, "run", runner)
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"),
        "PANEL_COOKIE_SECURE": False,
    })
    with app.app_context():
        token = auth_mod.create_session()
    client = app.test_client()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    return app, client, calls


def test_conversation_trace_uses_distinct_sessions_ranges_and_immutable_turn_context(
    tmp_path,
    monkeypatch,
):
    app, client, calls = _authed_conversation_client(tmp_path, monkeypatch)
    all_context = {
        "version": 1,
        "range": {"kind": "all"},
        "selected_region_ids": [],
    }
    bounded_context = {
        "version": 1,
        "range": {
            "kind": "bounded",
            "from": "2026-07-01",
            "to": "2026-07-23",
        },
        "selected_region_ids": ["knee-left"],
    }
    general = client.post(
        "/api/chat/conversations",
        json={"lens": "general", "context": all_context},
    ).get_json()["conversation"]
    pain = client.post(
        "/api/chat/conversations",
        json={"lens": "pain", "context": bounded_context},
    ).get_json()["conversation"]
    assert general["id"] != pain["id"]
    assert "hermes_session_id" not in general
    assert "hermes_session_id" not in pain
    session_connection = sqlite3.connect(app.config["PANEL_DB"])
    try:
        sessions = [
            row[0]
            for row in session_connection.execute(
                """SELECT hermes_session_id FROM chat_conversations
                    WHERE id IN (?,?) ORDER BY id""",
                (general["id"], pain["id"]),
            )
        ]
    finally:
        session_connection.close()
    assert len(sessions) == 2
    assert sessions[0] != sessions[1]
    assert general["context"]["range"] == {"kind": "all"}
    assert pain["context"]["range"] == bounded_context["range"]

    general_turn = "00000000-0000-4000-8000-000000000001"
    pain_turn = "00000000-0000-4000-8000-000000000002"
    general_response = client.post(
        f"/api/chat/conversations/{general['id']}/send",
        json={
            "message": "Synthetic general fixture question",
            "turn_id": general_turn,
            "context": all_context,
        },
    )
    pain_response = client.post(
        f"/api/chat/conversations/{pain['id']}/send",
        json={
            "message": "Synthetic pain fixture question",
            "turn_id": pain_turn,
            "context": bounded_context,
        },
    )
    assert general_response.status_code == pain_response.status_code == 200
    retry = client.post(
        f"/api/chat/conversations/{general['id']}/send",
        json={
            "message": "Synthetic general fixture question",
            "turn_id": general_turn,
            "context": all_context,
        },
    )
    assert retry.status_code == 200
    assert retry.get_json()["idempotent"] is True
    assert len(calls) == 2
    assert all(call[0] == "hermes-chat" for call in calls)
    assert calls[0][1][1] != calls[1][1][1]

    archive = client.patch(
        f"/api/chat/conversations/{pain['id']}",
        json={"archived": True},
    )
    assert archive.status_code == 200
    resume = client.patch(
        f"/api/chat/conversations/{pain['id']}",
        json={"archived": False},
    )
    assert resume.status_code == 200

    connection = sqlite3.connect(app.config["PANEL_DB"])
    connection.row_factory = sqlite3.Row
    try:
        rows = [
            dict(row)
            for row in connection.execute(
                """SELECT conversation_id,turn_id,role,context_json,delivery_status
                     FROM chat_log
                    WHERE turn_id IN (?,?)
                    ORDER BY conversation_id,id""",
                (general_turn, pain_turn),
            )
        ]
    finally:
        connection.close()
    assert len(rows) == 4
    by_turn = {}
    for row in rows:
        by_turn.setdefault(row["turn_id"], set()).add(row["context_json"])
        assert row["delivery_status"] == "complete"
    assert all(len(contexts) == 1 for contexts in by_turn.values())
    assert next(iter(by_turn[general_turn])) != next(iter(by_turn[pain_turn]))


def test_conversation_ambiguous_boundary_is_uncertain_and_never_retried(
    tmp_path,
    monkeypatch,
):
    calls = []

    def ambiguous(*args, **kwargs):
        calls.append((args, kwargs))
        raise bridge.BridgeError("Synthetic timeout after dispatch boundary.")

    monkeypatch.setattr(bridge, "run", ambiguous)
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"),
        "PANEL_COOKIE_SECURE": False,
    })
    with app.app_context():
        token = auth_mod.create_session()
    client = app.test_client()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    context = {
        "version": 1,
        "range": {"kind": "all"},
        "selected_region_ids": [],
    }
    conversation = client.post(
        "/api/chat/conversations",
        json={"lens": "general", "context": context},
    ).get_json()["conversation"]
    turn_id = "00000000-0000-4000-8000-000000000003"
    body = {
        "message": "Synthetic uncertain boundary",
        "turn_id": turn_id,
        "context": context,
    }
    first = client.post(
        f"/api/chat/conversations/{conversation['id']}/send",
        json=body,
    )
    second = client.post(
        f"/api/chat/conversations/{conversation['id']}/send",
        json=body,
    )
    assert first.status_code == 502
    assert second.status_code == 409
    assert second.get_json()["code"] == "turn_uncertain"
    assert len(calls) == 1
