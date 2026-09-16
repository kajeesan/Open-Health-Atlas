"""Phase D: the Insights endpoints — engine JSON passes through untouched;
the brief comes from the newest personal/patterns/ note."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from copy import deepcopy

import pytest

from app import auth as auth_mod
from app import bridge, create_app


LEGACY_ENGINE_RESULT = {
    "pairs": [], "suppressed_below_min_n": 3, "min_n": 10,
}
OUTCOME_ENGINE_RESULT = {
    "ok": True,
    "contract_version": "outcome-associations-v1",
    "meta": {
        "analysis_version": "outcome-v1",
        "registry_version": "feature-registry-v1",
        "engine_sha256": "sha256:" + "1" * 64,
        "registry_sha256": "sha256:" + "2" * 64,
        "input_fingerprint": "sha256:" + "3" * 64,
        "generated_at": "2026-07-23T00:00:00+02:00",
        "timezone": "Europe/Paris",
        "requested_range": {"kind": "all", "from": None, "to": None},
        "analysis_range": {"kind": "all", "from": None, "to": None},
        "baseline_range": {"kind": "all", "from": None, "to": None},
        "outcome": "subjective.day_rating",
        "modes": [
            "ordinal", "green-vs-non-green", "red-vs-non-red",
        ],
        "min_n": 30,
        "interactions": "none",
        "top": 30,
        "candidate_family_sizes": {
            "ordinal": 0,
            "green-vs-non-green": 0,
            "red-vs-non-red": 0,
        },
    },
    "coverage": {
        "outcome_eligible_n": 0,
        "modes": {
            mode: {
                "outcome_eligible_n": 0,
                "generated_candidates": 0,
                "tested_candidates": 0,
                "insufficient_candidates": 0,
                "family_size": 0,
            }
            for mode in (
                "ordinal", "green-vs-non-green", "red-vs-non-red",
            )
        },
        "source_manifests": [],
        "dependencies": {
            "completeness_revision_ids": [],
            "source_sync_interval_ids": [],
            "alias_revision_ids": [],
            "alias_revision_digest": "sha256:" + "4" * 64,
            "training_plan_revision_ids": [],
            "goal_revision_ids": [],
            "availability": {},
        },
    },
    "readiness": None,
    "findings": [],
    "suppression_counts": {
        reason: 0
        for reason in (
            "aligned_n",
            "autoregressive_lineage",
            "candidate_disabled",
            "composite_root",
            "exposure_prevalence",
            "exposure_variation",
            "forbidden_relation",
            "high_missingness",
            "interaction_aligned_n",
            "interaction_bootstrap_undefined",
            "interaction_ci_includes_zero",
            "interaction_component_gate",
            "interaction_increment_effect",
            "interaction_ineligible_single",
            "interaction_outcome_class",
            "interaction_q",
            "interaction_same_feature",
            "interaction_sparse_cell",
            "mechanical_tautology",
            "no_observations",
            "outcome_class_gate",
            "outcome_variation",
            "overlapping_running_week",
            "pain_self_derivation",
            "redundant_equivalent",
            "requested_min_n",
            "same_feature",
            "same_session_e1rm",
            "slow_episode_no_generic_alignment",
            "source_specific_hrv",
            "temporal_boundary",
            "timing_unavailable",
            "undefined_statistic",
            "unknown_absence",
            "unsupported_transform",
        )
    },
    "warnings": [],
}
READINESS_ENGINE_RESULT = {
    "ok": True,
    "meta": {
        "readiness_version": "data-readiness-v1",
        "registry_version": "feature-registry-v1",
        "registry_sha256": "2" * 64,
        "timezone": "Europe/Paris",
        "range": {"kind": "all"},
        "goal": None,
        "outcome": None,
        "feature_count": 0,
        "state_counts": {},
        "predicate_order": [
            "logic_not_implemented",
            "present_not_connected",
            "implemented_never_logged",
            "stale",
            "too_sparse_for_analysis",
            "sufficient",
        ],
    },
    "features": [],
}
HYPOTHESES_RESULT = {
    "ok": True,
    "contract_version": "hypothesis-ledger-v1",
    "items": [],
    "next_before": None,
}
HYPOTHESIS_BRIEF_RESULT = {
    "ok": True,
    "contract_version": "hypothesis-ledger-v1",
    "hypothesis": {"hypothesis_id": "sha256:" + "a" * 64},
    "evaluations": [],
    "evidence_for": [],
    "evidence_against": [],
    "annotations": [],
}
SYNTHESIS_HISTORY_RESULT = {
    "ok": True,
    "contract": "synthesis-v1",
    "syntheses": [],
    "next_before": None,
}
INSIGHT_RUN_STATUS_RESULT = {
    "ok": True,
    "contract_version": "insight-orchestrator-v1",
    "read_only": True,
    "batches": [],
    "triggers": [],
    "notifications": [],
}
PROMOTION_RESULT = {
    "ok": True,
    "contract_version": "hypothesis-ledger-v1",
    "status": "created",
    "hypothesis_id": "sha256:" + "a" * 64,
}
OUTCOME_READINESS_ENGINE_RESULT = deepcopy(READINESS_ENGINE_RESULT)
OUTCOME_READINESS_ENGINE_RESULT["meta"]["outcome"] = "subjective.day_rating"
OUTCOME_ENGINE_RESULT["readiness"] = OUTCOME_READINESS_ENGINE_RESULT


@pytest.fixture()
def client(tmp_path, monkeypatch):
    calls = []
    call_kwargs = []

    def fake_run(subcmd, *args, **kw):
        calls.append((subcmd, list(args)))
        call_kwargs.append(dict(kw))
        if subcmd == "outcome-associations":
            return OUTCOME_ENGINE_RESULT
        if subcmd == "data-readiness":
            return READINESS_ENGINE_RESULT
        if subcmd == "hypotheses":
            return HYPOTHESES_RESULT
        if subcmd == "hypothesis-brief":
            return HYPOTHESIS_BRIEF_RESULT
        if subcmd == "synthesis-history":
            return SYNTHESIS_HISTORY_RESULT
        if subcmd == "insight-run-status":
            return INSIGHT_RUN_STATUS_RESULT
        if subcmd == "hypothesis-promote":
            return PROMOTION_RESULT
        return LEGACY_ENGINE_RESULT

    monkeypatch.setattr(bridge, "run", fake_run)
    patterns = tmp_path / "vault" / "personal" / "patterns"
    patterns.mkdir(parents=True)
    app = create_app({
        "TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"), "PANEL_COOKIE_SECURE": False,
        "VAULT_DIR": str(tmp_path / "vault"),
        "ENABLE_LEGACY_COMPATIBILITY_REFERENCE_ROUTES": True,
    })
    c = app.test_client()
    with app.app_context():
        token = auth_mod.create_session()
    c.set_cookie(auth_mod.SESSION_COOKIE, token)
    c.calls = calls
    c.call_kwargs = call_kwargs
    c.patterns = patterns
    return c


def test_correlations_pass_through_engine_json(client):
    r = client.get("/api/insights/correlations")
    assert r.status_code == 200
    body = r.get_json()
    assert body["result"]["suppressed_below_min_n"] == 3   # untouched engine JSON
    assert client.calls[-1][0] == "correlate"


def test_engine_endpoints_use_expected_subcommands(client):
    client.get("/api/insights/signature")
    client.get("/api/insights/adherence")
    assert [c[0] for c in client.calls[-2:]] == ["day-signature", "adherence"]


def test_outcomes_all_range_maps_exactly_and_passes_engine_json_through(client):
    r = client.get(
        "/api/insights/outcomes"
        "?outcome=subjective.day_rating&mode=all&range=all"
    )
    assert r.status_code == 200
    assert r.get_json()["result"] == OUTCOME_ENGINE_RESULT
    assert client.calls[-1] == (
        "outcome-associations",
        ["--outcome", "subjective.day_rating", "--mode", "all", "--all"],
    )
    assert client.call_kwargs[-1]["timeout"] == 580.0


def test_outcome_job_ack_scope_and_terminal_results(client, monkeypatch):
    query = "?outcome=subjective.day_rating&mode=all&range=all"
    job_id = "a" * 32
    response = {"ok": True, "job_id": job_id, "status": "queued", "request": {
        "command": "outcome-associations",
        "args": ["--all", "--mode", "all", "--outcome", "subjective.day_rating"],
    }}
    calls = []

    def run(command, *args, **kwargs):
        calls.append((command, args, kwargs))
        return deepcopy(response)

    monkeypatch.setattr(bridge, "run", run)
    ack = client.post("/api/insights/outcome-jobs" + query, json={})
    assert ack.status_code == 202
    assert ack.get_json()["job"] == {"job_id": job_id, "status": "queued"}
    assert calls[0][0] == "analysis-job-start"
    assert json.loads(calls[0][2]["stdin"])["command"] == "outcome-associations"
    response.update(status="completed", result=OUTCOME_ENGINE_RESULT)
    completed = client.get("/api/insights/outcome-jobs/" + job_id + query)
    assert completed.status_code == 200
    assert completed.get_json()["job"]["result"] == OUTCOME_ENGINE_RESULT
    assert calls[-1][0:2] == ("analysis-job-status", (job_id,))
    different = client.get("/api/insights/outcome-jobs/" + job_id + query.replace("mode=all", "mode=ordinal"))
    assert different.status_code == 409
    assert "job" not in different.get_json()
    for state in ("stale", "failed"):
        response.update(status=state, error={"code": "stale_finding", "message": "Evidence changed."})
        terminal = client.get("/api/insights/outcome-jobs/" + job_id + query).get_json()["job"]
        assert terminal["status"] == state
        assert terminal["error"]["code"] == "stale_finding"
        assert "result" not in terminal
    before = len(calls)
    assert client.post("/api/insights/outcome-jobs" + query, json={"socket_path": "/tmp/other"}).status_code == 400
    assert client.get("/api/insights/outcome-jobs/not-an-id" + query).status_code == 400
    assert len(calls) == before


def test_finding_job_preserves_exact_replay_identifiers(client, monkeypatch):
    finding = "sha256:" + "b" * 64
    fingerprint = "sha256:" + "c" * 64
    seen = []

    def run(command, *args, **kwargs):
        descriptor = json.loads(kwargs["stdin"])
        seen.append(descriptor)
        return {"ok": True, "job_id": "d" * 32, "status": "queued", "request": descriptor}

    monkeypatch.setattr(bridge, "run", run)
    response = client.post("/api/insights/finding-jobs", query_string={
        "outcome": "subjective.day_rating", "finding_id": finding,
        "input_fingerprint": fingerprint, "range": "bounded",
        "from": "2026-06-01", "to": "2026-06-30",
    }, json={})
    assert response.status_code == 202
    assert seen == [{"command": "finding-evidence", "args": [
        "--outcome", "subjective.day_rating", "--finding-id", finding,
        "--input-fingerprint", fingerprint, "--from", "2026-06-01", "--to", "2026-06-30",
    ]}]


def test_browser_job_wait_resume_reload_and_stale_retry():
    """Execute the shipped helper through abort/ack races and current-data checks."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the browser job regression")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const stored = new Map();
const calls = [];
let responder;
const environment = {
  window: {}, document: {querySelector: () => ({content: 'csrf'})},
  location: {assign() {}}, DOMException, AbortController,
  localStorage: {getItem: k => stored.get(k), setItem: (k,v) => stored.set(k,v), removeItem: k => stored.delete(k)},
  setTimeout: cb => setTimeout(cb, 0), clearTimeout,
  fetch: async (url, options) => {calls.push({url, options}); return responder(url, options);},
};
vm.runInNewContext(fs.readFileSync('app/static/js/analysis_jobs.js', 'utf8'), environment);
const api = environment.window.HermesAnalysisJobs;
const id = 'a'.repeat(32);
const completed = {job_id: id, status: 'completed', result: {findings: ['private-result']}};
const response = job => ({ok: true, status: 200, json: async () => ({ok: true, job})});
const options = {startURL: '/jobs?scope=A', statusURL: id => '/jobs/' + id + '?scope=A', storageKey: 'job'};
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  let acknowledge;
  responder = () => new Promise(resolve => {acknowledge = resolve;});
  const stopped = new AbortController();
  const first = api.wait({...options, signal: stopped.signal});
  const firstRejected = assert.rejects(first, error => error.name === 'AbortError');
  await tick();
  stopped.abort();
  const resumed = api.wait(options);
  await tick();
  assert.equal(calls.length, 1, 'rapid resume shares pending POST');
  assert.equal(calls[0].options.headers['X-CSRFToken'], 'csrf');
  acknowledge(response(completed));
  await firstRejected;
  assert.equal((await resumed).job_id, id);
  assert.deepEqual([...stored.values()], [id], 'no health results or query scope persisted');
  responder = () => response(completed);
  const reload = await api.wait({...options, resumeOnly: true});
  assert.equal(reload.job_id, id);
  assert.equal(calls.filter(call => call.options.method === 'POST').length, 1);
  responder = () => response({job_id: id, status: 'stale', error: {code: 'stale_job'}});
  await assert.rejects(api.wait(options), /Records or analysis settings changed/);
  assert.equal(stored.size, 0);
  const states = [];
  let polls = 0;
  responder = (url, opts) => response(opts.method === 'POST'
    ? {job_id: id, status: 'queued'} : ++polls === 1
    ? {job_id: id, status: 'running'} : completed);
  await api.wait({...options, onStatus: status => states.push(status)});
  assert.deepEqual(states, ['queued', 'running', 'completed']);
  const postCount = calls.filter(call => call.options.method === 'POST').length;
  responder = () => ({ok: false, status: 409, json: async () => ({ok: false, error: {code: 'job_scope_mismatch'}})});
  assert.equal(await api.wait({...options, resumeOnly: true}), null);
  assert.equal(stored.size, 0);
  assert.equal(calls.filter(call => call.options.method === 'POST').length, postCount,
    'reload of another scope does not start an analysis');
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    subprocess.run([node, "-e", script], cwd=Path(__file__).parents[1],
                   check=True, capture_output=True, text=True, timeout=20)


def test_dashboard_green_days_uses_governed_default_range_and_evidence(client):
    response = client.get("/api/insights/dashboard-green-days")
    assert response.status_code == 200
    body = response.get_json()
    assert body["result"] == OUTCOME_ENGINE_RESULT
    assert client.calls[-1] == (
        "outcome-associations",
        [
            "--outcome", "subjective.day_rating",
            "--mode", "green-vs-non-green",
            "--days", "365",
            "--min-n", "30",
            "--interactions", "none",
            "--top", "3",
        ],
    )
    assert client.call_kwargs[-1]["timeout"] == 580.0
    evidence = body["evidence"]
    assert evidence["command"] == "outcome-associations"
    canonical = json.dumps(
        OUTCOME_ENGINE_RESULT,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert evidence["result_sha256"] == (
        "sha256:" + hashlib.sha256(canonical).hexdigest()
    )


def test_dashboard_green_days_fixed_scope_is_server_owned(
    tmp_path, monkeypatch,
):
    calls = []
    result = deepcopy(OUTCOME_ENGINE_RESULT)

    def fake_run(subcmd, *args, **kwargs):
        calls.append((subcmd, list(args), dict(kwargs)))
        return result

    monkeypatch.setattr(bridge, "run", fake_run)
    fixed_scope = {
        "data_class": "fictional",
        "fixture_id": "green-days-actionable-v1",
        "range_from": "2026-05-17",
        "range_to": "2026-06-30",
        "seed_sha256": "sha256:" + "a" * 64,
        "database_sha256": "sha256:" + "b" * 64,
    }
    fixture_socket = "/tmp/openhealthatlas-test-green-days.sock"
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "fixed-panel.db"),
        "PANEL_COOKIE_SECURE": False,
        "DASHBOARD_GREEN_DAYS_FIXED_SCOPE": fixed_scope,
        "DASHBOARD_GREEN_DAYS_BRIDGE_SOCKET": fixture_socket,
    })
    scoped = app.test_client()
    with app.app_context():
        scoped.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())

    response = scoped.get("/api/insights/dashboard-green-days")
    assert response.status_code == 200
    assert calls == [(
        "outcome-associations",
        [
            "--outcome", "subjective.day_rating",
            "--mode", "green-vs-non-green",
            "--from", "2026-05-17", "--to", "2026-06-30",
            "--min-n", "30",
            "--interactions", "none",
            "--top", "3",
        ],
        {"timeout": 580.0, "socket_path": fixture_socket},
    )]
    evidence = response.get_json()["evidence"]
    assert {key: evidence[key] for key in fixed_scope} == fixed_scope


def test_dashboard_green_days_rejects_query_and_bad_server_scope(
    tmp_path, monkeypatch,
):
    calls = []
    monkeypatch.setattr(bridge, "run", lambda *args, **kwargs: calls.append(args))
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "bad-fixed-panel.db"),
        "PANEL_COOKIE_SECURE": False,
        "DASHBOARD_GREEN_DAYS_FIXED_SCOPE": {
            "data_class": "fictional",
            "fixture_id": "green-days-actionable-v1",
            "range_from": "2026-06-30",
            "range_to": "2026-05-17",
            "seed_sha256": "sha256:" + "a" * 64,
            "database_sha256": "sha256:" + "b" * 64,
        },
        "DASHBOARD_GREEN_DAYS_BRIDGE_SOCKET": "/tmp/unused-green-days.sock",
    })
    scoped = app.test_client()
    with app.app_context():
        scoped.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())

    assert scoped.get(
        "/api/insights/dashboard-green-days?days=30"
    ).status_code == 400
    assert scoped.get("/api/insights/dashboard-green-days").status_code == 503
    assert calls == []


def test_dashboard_green_days_fixed_scope_requires_dedicated_socket(
    tmp_path, monkeypatch,
):
    calls = []
    monkeypatch.setattr(bridge, "run", lambda *args, **kwargs: calls.append(args))
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "missing-socket.db"),
        "PANEL_COOKIE_SECURE": False,
        "DASHBOARD_GREEN_DAYS_FIXED_SCOPE": {
            "data_class": "fictional",
            "fixture_id": "green-days-actionable-v1",
            "range_from": "2026-05-17",
            "range_to": "2026-06-30",
            "seed_sha256": "sha256:" + "a" * 64,
            "database_sha256": "sha256:" + "b" * 64,
        },
    })
    scoped = app.test_client()
    with app.app_context():
        scoped.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())

    response = scoped.get("/api/insights/dashboard-green-days")
    assert response.status_code == 503
    assert response.get_json()["error"] == (
        "dashboard Green-day fixture broker is unavailable"
    )
    assert calls == []


def test_dashboard_jobs_keep_trusted_fixture_socket_scope_and_evidence(client, monkeypatch):
    scope = {
        "data_class": "fictional", "fixture_id": "job-fixture",
        "range_from": "2026-05-17", "range_to": "2026-06-30",
        "seed_sha256": "sha256:" + "a" * 64,
        "database_sha256": "sha256:" + "b" * 64,
    }
    client.application.config.update(
        DASHBOARD_GREEN_DAYS_FIXED_SCOPE=scope,
        DASHBOARD_GREEN_DAYS_BRIDGE_SOCKET="/tmp/test-job-fixture.sock",
    )
    calls = []
    saved = {}

    def run(command, *args, **kwargs):
        calls.append((command, args, kwargs))
        if command == "analysis-job-start":
            saved.update(json.loads(kwargs["stdin"]))
        return {"ok": True, "job_id": "a" * 32, "status": "completed",
                "request": saved, "result": OUTCOME_ENGINE_RESULT}

    monkeypatch.setattr(bridge, "run", run)
    started = client.post("/api/insights/dashboard-green-days/jobs", json={})
    polled = client.get("/api/insights/dashboard-green-days/jobs/" + "a" * 32)
    assert started.status_code == polled.status_code == 200
    assert all(call[2]["socket_path"] == "/tmp/test-job-fixture.sock" for call in calls)
    assert saved == {"command": "outcome-associations", "args": [
        "--outcome", "subjective.day_rating", "--mode", "green-vs-non-green",
        "--from", "2026-05-17", "--to", "2026-06-30",
        "--min-n", "30", "--interactions", "none", "--top", "3",
    ]}
    job = polled.get_json()["job"]
    assert job["result"] == OUTCOME_ENGINE_RESULT
    assert {key: job["evidence"][key] for key in scope} == scope
    canonical = json.dumps(OUTCOME_ENGINE_RESULT, ensure_ascii=False,
                           sort_keys=True, separators=(",", ":")).encode()
    assert job["evidence"]["result_sha256"] == "sha256:" + hashlib.sha256(canonical).hexdigest()
    saved["args"][-1] = "100"
    rejected = client.get("/api/insights/dashboard-green-days/jobs/" + "a" * 32)
    assert rejected.status_code == 409
    assert "job" not in rejected.get_json()
    before = len(calls)
    assert client.post("/api/insights/dashboard-green-days/jobs?from=2026-01-01", json={}).status_code == 400
    assert len(calls) == before


@pytest.mark.parametrize(
    "mode",
    ("all", "ordinal", "green-vs-non-green", "red-vs-non-red"),
)
def test_outcomes_bounded_range_and_every_mode_map_exactly(client, mode):
    r = client.get(
        "/api/insights/outcomes"
        f"?outcome=subjective.day_rating&mode={mode}"
        "&range=bounded&from=2026-01-01&to=2026-06-30"
    )
    assert r.status_code == 200
    assert client.calls[-1] == (
        "outcome-associations",
        [
            "--outcome", "subjective.day_rating", "--mode", mode,
            "--from", "2026-01-01", "--to", "2026-06-30",
        ],
    )


@pytest.mark.parametrize(
    "query",
    (
        "",
        "outcome=subjective.day_rating&mode=all",
        "outcome=subjective.day_rating&mode=all&range=bounded&from=2026-01-01",
        "outcome=subjective.day_rating&mode=all&range=bounded&to=2026-06-30",
        "outcome=subjective.day_rating&mode=all&range=bounded"
        "&from=2026-06-30&to=2026-01-01",
        "outcome=subjective.day_rating&mode=all&range=bounded"
        "&from=2026-02-30&to=2026-06-30",
        "outcome=subjective.day_rating&mode=all&range=bounded"
        "&from=2026-1-01&to=2026-06-30",
        "outcome=subjective.day_rating&mode=all&range=all&from=2026-01-01",
        "outcome=subjective.day_rating&mode=all&range=all&to=2026-06-30",
        "outcome=subjective.day_rating&mode=all&range=all&min-n=30",
        "outcome=subjective.day_rating&mode=all&range=all&source=fitbit",
        "outcome=subjective.day_rating&range=all",
        "mode=all&range=all",
        "outcome=Subjective.day_rating&mode=all&range=all",
        "outcome=subjective.day_rating&mode=unknown&range=all",
        "outcome=subjective.day_rating&outcome=sleep.duration_hours"
        "&mode=all&range=all",
        "outcome=subjective.day_rating&mode=all&mode=ordinal&range=all",
        "outcome=subjective.day_rating&mode=all&range=all&range=all",
    ),
)
def test_outcomes_rejects_noncanonical_or_extra_query_before_bridge(client, query):
    before = list(client.calls)
    suffix = f"?{query}" if query else ""
    r = client.get("/api/insights/outcomes" + suffix)
    assert r.status_code == 400
    assert client.calls == before


def test_readiness_all_and_bounded_context_map_exactly(client):
    r = client.get("/api/insights/readiness?range=all")
    assert r.status_code == 200
    assert r.get_json()["result"] == READINESS_ENGINE_RESULT
    assert client.calls[-1] == ("data-readiness", ["--all"])

    r = client.get(
        "/api/insights/readiness"
        "?range=bounded&from=2026-01-01&to=2026-06-30"
        "&goal=green_days&outcome=subjective.day_rating"
    )
    assert r.status_code == 200
    assert client.calls[-1] == (
        "data-readiness",
        [
            "--from", "2026-01-01", "--to", "2026-06-30",
            "--goal", "green_days", "--outcome", "subjective.day_rating",
        ],
    )


@pytest.mark.parametrize(
    "query",
    (
        "",
        "range=bounded&from=2026-01-01",
        "range=bounded&from=2026-06-30&to=2026-01-01",
        "range=all&from=2026-01-01",
        "range=all&goal=green_days&goal=follow_through",
        "range=all&goal=Green_days",
        "range=all&outcome=Subjective.day_rating",
        "range=all&mode=all",
        "range=all&days=30",
    ),
)
def test_readiness_rejects_noncanonical_or_extra_query_before_bridge(client, query):
    before = list(client.calls)
    suffix = f"?{query}" if query else ""
    r = client.get("/api/insights/readiness" + suffix)
    assert r.status_code == 400
    assert client.calls == before


def test_adherence_default_days_unchanged(client):
    # Insight Explorer's own adherence card sends no params — must keep the
    # pre-existing 90d default exactly (additive: nothing else changes)
    client.get("/api/insights/adherence")
    assert client.calls[-1] == ("adherence", ["--days", "90"])


def test_adherence_days_param_maps_and_validates(client):
    # owner-review r2: the Consistency page's range dropdown genuinely
    # re-windows this card via a whitelisted `days` value, same
    # whitelist-and-validate style as /api/dash/sleep
    # "1" (single day) added in owner-review r3 for the global "Day" dropdown
    client.get("/api/insights/adherence?days=1")
    assert client.calls[-1] == ("adherence", ["--days", "1"])
    client.get("/api/insights/adherence?days=7")
    assert client.calls[-1] == ("adherence", ["--days", "7"])
    client.get("/api/insights/adherence?days=30")
    assert client.calls[-1] == ("adherence", ["--days", "30"])
    # "90" must be explicitly whitelisted too — the endpoint's own no-param
    # default is 90d, so an explicit `?days=90` must not 400 (review finding)
    client.get("/api/insights/adherence?days=90")
    assert client.calls[-1] == ("adherence", ["--days", "90"])
    client.get("/api/insights/adherence?days=365")
    assert client.calls[-1] == ("adherence", ["--days", "365"])
    # "all" maps to health.py's own days=0 "all-time, from the earliest
    # logged row" sentinel (_daily_frame) — genuinely unbounded
    client.get("/api/insights/adherence?days=all")
    assert client.calls[-1] == ("adherence", ["--days", "0"])
    assert client.get("/api/insights/adherence?days=12").status_code == 400
    assert client.get("/api/insights/adherence?days=evil").status_code == 400


def test_timing_endpoint_passes_through(client):
    r = client.get("/api/insights/timing")
    assert r.status_code == 200 and r.get_json()["ok"]
    assert client.calls[-1] == ("timing-adherence", [])   # engine owns the window default


def test_brief_returns_newest_pattern_note(client):
    (client.patterns / "old.md").write_text("old finding")
    time.sleep(0.02)
    (client.patterns / "new.md").write_text("new finding")
    os.utime(client.patterns / "new.md")
    r = client.get("/api/insights/brief")
    assert r.get_json()["brief"]["file"] == "new.md"
    assert r.get_json()["brief"]["content"] == "new finding"


def test_brief_none_when_no_patterns(client):
    assert client.get("/api/insights/brief").get_json()["brief"] is None


def test_legacy_correlation_and_vault_brief_are_quarantined_by_default(
    tmp_path, monkeypatch,
):
    calls = []
    monkeypatch.setattr(bridge, "run", lambda *args, **kwargs: calls.append(args))
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "quarantined.db"),
        "PANEL_COOKIE_SECURE": False,
        "VAULT_DIR": str(tmp_path / "missing-vault"),
    })
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())

    for path in (
        "/api/insights/correlations",
        "/api/insights/brief",
        "/api/insights/signature",
    ):
        response = c.get(path)
        assert response.status_code == 410
        assert response.get_json()["error"]["code"] == (
            "legacy_insight_route_quarantined"
        )
    assert calls == []


def test_legacy_insight_override_cannot_activate_in_normal_process(tmp_path):
    app = create_app({
        "TESTING": False,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "normal.db"),
        "PANEL_COOKIE_SECURE": False,
        "ENABLE_LEGACY_COMPATIBILITY_REFERENCE_ROUTES": True,
    })
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    assert c.get("/api/insights/brief").status_code == 410


def test_hypotheses_list_maps_filters_and_cursor_without_transform(client):
    before = "sha256:" + "b" * 64
    response = client.get(
        "/api/insights/hypotheses"
        "?status=strengthening&outcome=subjective.day_rating"
        f"&limit=20&before={before}"
    )
    assert response.status_code == 200
    assert response.get_json()["result"] == HYPOTHESES_RESULT
    assert client.calls[-1] == (
        "hypotheses",
        [
            "--status", "strengthening",
            "--outcome", "subjective.day_rating",
            "--limit", "20",
            "--before", before,
        ],
    )


def test_hypothesis_detail_is_identifier_only_and_passes_through(client):
    hypothesis_id = "sha256:" + "a" * 64
    response = client.get(f"/api/insights/hypotheses/{hypothesis_id}")
    assert response.status_code == 200
    assert response.get_json()["result"] == HYPOTHESIS_BRIEF_RESULT
    assert client.calls[-1] == ("hypothesis-brief", [hypothesis_id])
    assert client.get("/api/insights/hypotheses/not-an-id").status_code == 400


def test_synthesis_history_maps_only_bounded_pagination(client):
    before = "sha256:" + "c" * 64
    response = client.get(f"/api/insights/syntheses?limit=7&before={before}")
    assert response.status_code == 200
    assert response.get_json()["result"] == SYNTHESIS_HISTORY_RESULT
    assert client.calls[-1] == (
        "synthesis-history", ["--limit", "7", "--before", before],
    )


def test_run_status_route_allows_only_bounded_limit_and_passes_through(client):
    response = client.get("/api/insights/runs?limit=7")
    assert response.status_code == 200
    assert response.get_json()["result"] == INSIGHT_RUN_STATUS_RESULT
    assert client.calls[-1] == ("insight-run-status", ["--limit", "7"])
    assert client.get("/api/insights/runs").status_code == 200
    assert client.calls[-1] == ("insight-run-status", [])
    for invalid in (
        "/api/insights/runs?limit=0",
        "/api/insights/runs?limit=101",
        "/api/insights/runs?limit=1&limit=2",
        "/api/insights/runs?before=sha256:" + "a" * 64,
    ):
        assert client.get(invalid).status_code == 400


def test_identifier_only_promotion_recomputes_exact_range(client):
    finding_id = "sha256:" + "d" * 64
    fingerprint = "sha256:" + "e" * 64
    response = client.post(
        "/api/insights/hypotheses/promote",
        json={
            "outcome": "subjective.day_rating",
            "finding_id": finding_id,
            "input_fingerprint": fingerprint,
            "range": {
                "kind": "bounded",
                "from": "2026-01-01",
                "to": "2026-06-30",
            },
        },
    )
    assert response.status_code == 200
    assert response.get_json()["result"] == PROMOTION_RESULT
    assert client.calls[-1] == (
        "hypothesis-promote",
        [
            "--outcome", "subjective.day_rating",
            "--finding-id", finding_id,
            "--input-fingerprint", fingerprint,
            "--from", "2026-01-01", "--to", "2026-06-30",
        ],
    )
    assert client.call_kwargs[-1]["timeout"] == 190.0


def test_stale_promotion_remains_machine_readable(client, monkeypatch):
    def stale(*_args, **_kwargs):
        raise bridge.BridgeError(
            "input fingerprint changed", code="stale_finding",
        )

    monkeypatch.setattr(bridge, "run", stale)
    response = client.post(
        "/api/insights/hypotheses/promote",
        json={
            "outcome": "subjective.day_rating",
            "finding_id": "sha256:" + "d" * 64,
            "input_fingerprint": "sha256:" + "e" * 64,
            "range": {"kind": "all"},
        },
    )
    assert response.status_code == 409
    assert response.get_json() == {
        "ok": False,
        "error": {
            "code": "stale_finding",
            "message": "input fingerprint changed",
        },
    }


@pytest.mark.parametrize(
    "forbidden", ("effect", "narrative", "status", "confidence", "q"),
)
def test_promotion_rejects_browser_evidence_and_ledger_fields(client, forbidden):
    body = {
        "outcome": "subjective.day_rating",
        "finding_id": "sha256:" + "d" * 64,
        "input_fingerprint": "sha256:" + "e" * 64,
        "range": {"kind": "all"},
        forbidden: 1,
    }
    before = len(client.calls)
    response = client.post("/api/insights/hypotheses/promote", json=body)
    assert response.status_code == 400
    assert len(client.calls) == before


@pytest.mark.parametrize(
    "path",
    (
        "/api/insights/hypotheses?status=promoted",
        "/api/insights/hypotheses?limit=0",
        "/api/insights/hypotheses?limit=101",
        "/api/insights/hypotheses?limit=" + "9" * 5000,
        "/api/insights/hypotheses?before=bad",
        "/api/insights/syntheses?extra=1",
        "/api/insights/syntheses?limit=1&limit=2",
        "/api/insights/syntheses?limit=" + "9" * 5000,
    ),
)
def test_ledger_routes_reject_invalid_or_extra_query_before_bridge(client, path):
    before = len(client.calls)
    response = client.get(path)
    assert response.status_code == 400
    assert len(client.calls) == before


def test_insights_requires_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    unauthenticated = app.test_client()
    assert unauthenticated.get("/api/insights/correlations").status_code == 401
    assert unauthenticated.get(
        "/api/insights/outcomes"
        "?outcome=subjective.day_rating&mode=all&range=all"
    ).status_code == 401
    assert unauthenticated.get(
        "/api/insights/readiness?range=all"
    ).status_code == 401
    assert unauthenticated.get("/api/insights/hypotheses").status_code == 401
    assert unauthenticated.get("/api/insights/syntheses").status_code == 401
    assert unauthenticated.get("/api/insights/runs").status_code == 401
    for path in ("outcome-jobs", "finding-jobs", "dashboard-green-days/jobs"):
        assert unauthenticated.post("/api/insights/" + path, json={}).status_code == 401
        assert unauthenticated.get("/api/insights/" + path + "/" + "a" * 32).status_code == 401
    assert unauthenticated.post(
        "/api/insights/hypotheses/promote", json={},
    ).status_code == 401


def test_phase5_promotion_keeps_csrf_csp_and_rate_limit(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        bridge,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)) or PROMOTION_RESULT,
    )
    csrf_app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": True,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "csrf-phase5.db"),
        "PANEL_COOKIE_SECURE": False,
    })
    csrf_client = csrf_app.test_client()
    with csrf_app.app_context():
        token = auth_mod.create_session()
    csrf_client.set_cookie(auth_mod.SESSION_COOKIE, token)
    body = {
        "outcome": "subjective.day_rating",
        "finding_id": "sha256:" + "d" * 64,
        "input_fingerprint": "sha256:" + "e" * 64,
        "range": {"kind": "all"},
    }
    rejected = csrf_client.post(
        "/api/insights/hypotheses/promote", json=body,
    )
    assert rejected.status_code == 400
    assert "CSRF" in rejected.get_json()["error"]
    for path in ("outcome-jobs", "finding-jobs", "dashboard-green-days/jobs"):
        rejected_job = csrf_client.post("/api/insights/" + path, json={})
        assert rejected_job.status_code == 400
        assert "CSRF" in rejected_job.get_json()["error"]
    assert calls == []
    assert "default-src 'self'" in rejected.headers["Content-Security-Policy"]

    limited_app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": True,
        "PANEL_DB": str(tmp_path / "limit-phase5.db"),
        "PANEL_COOKIE_SECURE": False,
    })
    limited_client = limited_app.test_client()
    with limited_app.app_context():
        token = auth_mod.create_session()
    limited_client.set_cookie(auth_mod.SESSION_COOKIE, token)
    statuses = [
        limited_client.post(
            "/api/insights/hypotheses/promote",
            json=body,
            environ_overrides={"REMOTE_ADDR": "198.51.100.77"},
        ).status_code
        for _ in range(7)
    ]
    assert statuses[:6] == [200] * 6
    assert statuses[6] == 429


def test_browser_analysis_stays_with_its_conversation():
    """Delayed analyses and context replies cannot populate another conversation."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the browser context regression")
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = 'app/static/js/insights.js';
const script = fs.readFileSync(source, 'utf8');
const tick = async () => { for (let n=0;n<30;n++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise=new Promise((a,b)=>{resolve=a;reject=b;}); return {promise,resolve,reject}; };
class Element {
  constructor(id='') { this.id=id; this.children=[]; this.dataset={}; this.listeners={}; this.disabled=false; this.hidden=false; this.value=''; this._text=''; this.classList={toggle(){}}; }
  set textContent(s) { this._text=String(s); this.children=[]; }
  get textContent() { return this._text+this.children.map(x=>x.textContent||'').join(''); }
  get childNodes() { return this.children; }
  append(...xs) { this.children.push(...xs); }
  appendChild(x) { this.children.push(x); return x; }
  replaceChildren(...xs) { this._text=''; this.children=xs; }
  setAttribute(k,v) { this[k]=v; }
  addEventListener(k,f) { this.listeners[k]=f; }
  focus() {}
  remove() {}
}
function setup() {
  const nodes=new Map(), storage=new Map(), requests=[], jobs=[];
  const node=id=>{ if(!nodes.has(id)) nodes.set(id,new Element(id)); return nodes.get(id); };
  node('ins-granularity').value='month';
  const people=Object.fromEntries(['A','B','C'].map((id,i)=>[id,{id,title:`Conversation ${id}`,lens:'general',archived:false,context:{version:1,range:{kind:'bounded',from:`2026-0${i+1}-01`,to:`2026-0${i+1}-28`},selected_region_ids:[]}}]));
  function fetch(url,opts={}) { const d=deferred(); requests.push({url,opts,d}); return d.promise; }
  const context={document:{getElementById:node,querySelector:()=>({content:'csrf'}),querySelectorAll:()=>[],createElement:()=>new Element()},Node:Element,fetch,localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},location:{assign(){}},window:{HermesAnalysisJobs:{remembered:()=>null,wait:options=>{const d=deferred();jobs.push({options,d});return d.promise;}}},AbortController,DOMException,URLSearchParams,crypto:require('node:crypto').webcrypto,console};
  vm.runInNewContext(script,context,{filename:source});
  function reply(url,body) { const index=requests.findIndex(x=>x.url===url); assert.notEqual(index,-1,`Expected request ${url}; have ${requests.map(x=>x.url)}`); const [r]=requests.splice(index,1); r.d.resolve({status:200,ok:true,json:async()=>({ok:true,...body})}); return r; }
  async function boot() { reply('/api/chat/conversations?archived=0&limit=100',{conversations:Object.values(people)}); await tick(); reply('/api/chat/conversations/A',{conversation:people.A}); await tick(); reply('/api/chat/conversations/A/messages?limit=100',{messages:[]}); await tick(); }
  function change(id) { node('ins-conversation').value=id; return node('ins-conversation').listeners.change({target:{value:id}}); }
  function analyze() { return node('ins-analyze').listeners.click({target:node('ins-analyze')}); }
  function result(id) { const range=people[id].context.range; return {status:'completed',result:{meta:{analysis_range:range,baseline_range:range},coverage:{},findings:[{finding_id:`finding-${id}`,outcome:{mode:'ordinal'},exposure:{components:[{display:`ONLY-${id}`}]}}]}}; }
  return {node,people,requests,jobs,reply,boot,change,analyze,result,storage};
}
async function analysisSwitch() {
  const h=setup(); await h.boot();
  const analysis=h.analyze(); assert.equal(h.jobs.length,1);
  const readiness=h.requests.find(r=>r.url.startsWith('/api/insights/readiness?'));
  h.reply(readiness.url,{result:{meta:{range:h.people.A.context.range},features:[]}}); await tick();
  const switching=h.change('B');
  assert.equal(h.node('ins-analyze').disabled,true);
  await h.analyze(); assert.equal(h.jobs.length,1,'Analyze must not start while switching');
  h.reply('/api/chat/conversations/B',{conversation:h.people.B}); await tick();
  h.jobs[0].d.resolve(h.result('A')); await tick(); await analysis;
  assert.ok(!h.node('finding-groups').textContent.includes('ONLY-A'),'Late A analysis leaked into B');
  assert.ok(h.node('ins-window').textContent.includes('2026-02-01'));
  assert.equal(h.node('ins-analyze').disabled,true,'Analyze must wait for conversation messages');
  h.reply('/api/chat/conversations/B/messages?limit=100',{messages:[]}); await switching; await tick();
  assert.equal(h.node('ins-analyze').disabled,false);
  assert.equal(h.node('finding-groups').children.length,0);
}
async function detailReordering() {
  const h=setup(); await h.boot();
  const b=h.change('B'), c=h.change('C');
  h.reply('/api/chat/conversations/C',{conversation:h.people.C}); await tick();
  h.reply('/api/chat/conversations/C/messages?limit=100',{messages:[{role:'assistant',content:'ONLY-C-MESSAGE'}]}); await c; await tick();
  h.reply('/api/chat/conversations/B',{conversation:h.people.B}); await b; await tick();
  assert.equal(h.storage.get('hermes.insight.conversation'),'C');
  assert.ok(h.node('ins-window').textContent.includes('2026-03-01'));
  assert.ok(h.node('ins-chat').textContent.includes('ONLY-C-MESSAGE'));
  assert.equal(h.requests.length,0,'Stale B detail must not fetch B messages');
  assert.equal(h.node('ins-analyze').disabled,false);
}
async function messageReordering() {
  const h=setup(); await h.boot();
  const b=h.change('B'); h.reply('/api/chat/conversations/B',{conversation:h.people.B}); await tick();
  const c=h.change('C'); h.reply('/api/chat/conversations/C',{conversation:h.people.C}); await tick();
  h.reply('/api/chat/conversations/C/messages?limit=100',{messages:[{role:'assistant',content:'ONLY-C-MESSAGE'}]}); await c; await tick();
  h.reply('/api/chat/conversations/B/messages?limit=100',{messages:[{role:'assistant',content:'FORBIDDEN-B-MESSAGE'}]}); await b; await tick();
  assert.equal(h.storage.get('hermes.insight.conversation'),'C');
  assert.ok(h.node('ins-chat').textContent.includes('ONLY-C-MESSAGE'));
  assert.ok(!h.node('ins-chat').textContent.includes('FORBIDDEN-B-MESSAGE'));
  assert.equal(h.node('ins-analyze').disabled,false);
}
async function currentAnalysisRenders() {
  const h=setup(); await h.boot(); const analysis=h.analyze();
  const readiness=h.requests.find(r=>r.url.startsWith('/api/insights/readiness?'));
  h.reply(readiness.url,{result:{meta:{range:h.people.A.context.range},features:[]}});
  h.jobs[0].d.resolve(h.result('A')); await analysis; await tick();
  assert.ok(h.node('finding-groups').textContent.includes('ONLY-A'));
  assert.equal(h.node('ins-scope-status').textContent,'Analysis ready.');
}
(async()=>{for (const test of [analysisSwitch,detailReordering,messageReordering,currentAnalysisRenders]) { await test(); console.log(`PASS ${test.name}`); }})().catch(e=>{console.error(e);process.exitCode=1;});

"""
    subprocess.run([node, "-e", script], cwd=Path(__file__).parents[1],
                   check=True, capture_output=True, text=True, timeout=20)
