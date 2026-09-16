"""T48 — /api/recovery/readiness deterministic display boundary."""
from copy import deepcopy
import hashlib
import json

import pytest

from app import auth as auth_mod
from app import bridge, create_app


FIXED_SCOPE = {
    "data_class": "fictional",
    "fixture_id": "comprehensive-persona-v1",
    "range_from": "2026-03-02",
    "anchor_date": "2026-06-30",
    "readiness_fixture_lane": "accepted-v5",
}
def _sha256(value):
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _public_evidence():
    evidence = {
    "contract": "readiness-evidence-v2",
    "input_fingerprint": "sha256:" + "a" * 64,
    "fingerprint_scope": [
        "calculation_relevant_projection",
        "calculation_policy",
        "range",
    ],
    "policy": {
        "policy_id": "openhealthatlas-readiness-policy",
        "version": "1.0.0",
        "meaning": "non_diagnostic_readiness_heuristic",
    },
    "snapshot_integrity": {
        "contract": "openhealthatlas-readiness-ancestry-v2",
        "status": "verified",
        "scope": "current_snapshot_integrity_and_reproducibility",
        "schema_version": 5,
        "external_provider_sync": "not_performed",
        "training_ancestry": "manifested",
    },
        "components": [
            {
                "key": "sleep", "status": "included", "reason_code": None,
                "ancestry_state": "source_row_identified",
                "transformation": "sleep-score-v1", "current": None,
                "baseline": None,
            },
            {
                "key": "hrv", "status": "excluded",
                "reason_code": "insufficient_same_source_baseline",
                "ancestry_state": "source_rows_identified",
                "transformation": "same-source-baseline-deviation-hrv-v1",
                "current": None, "baseline": None,
            },
            {
                "key": "rhr", "status": "excluded",
                "reason_code": "insufficient_same_source_baseline",
                "ancestry_state": "source_rows_identified",
                "transformation": "same-source-baseline-deviation-rhr-v1",
                "current": None, "baseline": None,
            },
        ],
        "soreness": {
            "status": "present",
            "table": "subjective_daily",
            "locator": "subjective_daily:2026-06-30",
            "source_label": "fictional-demo",
            "observed_at": "2026-06-30",
        },
        "warnings": [
            {
                "code": "insufficient_same_source_baseline",
                "component": key,
                "source_label": "fitbit",
                "required": 14,
                "observed": 10,
                "other_source_observations_excluded": 86,
            }
            for key in ("hrv", "rhr")
        ],
        "remaining_ancestry_gaps": [],
    }
    evidence["policy_sha256"] = _sha256(evidence["policy"])
    evidence["public_evidence_identity"] = _sha256(evidence)
    return evidence


PUBLIC_EVIDENCE = _public_evidence()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    calls = []
    public_evidence = deepcopy(PUBLIC_EVIDENCE)
    public_evidence["snapshot_integrity"]["schema_version"] = 6
    public_evidence.pop("public_evidence_identity")
    public_evidence["public_evidence_identity"] = _sha256(public_evidence)
    public_evidence["private_sidecar_path"] = "/private/fictional-sidecar.json"
    result = {
        "status": "insufficient_data",
        "anchor_date": "2026-06-30",
        "range_from": "2026-03-02",
        "reason": "fewer than 2 eligible recovery components",
        "components": [
            {"key": "sleep", "score": 92, "value": 7.09, "basis": "fixture"},
        ],
        "soreness": {
            "date": "2026-06-30",
            "note": "DEFAULT MODE RAW NOTE CANARY",
        },
        "evidence": public_evidence,
    }
    monkeypatch.setattr(
        bridge,
        "run",
        lambda sub, *a, **k: (calls.append((sub, list(a))) or result),
    )
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    c.calls = calls
    return c


def test_readiness_default_mode_is_governed_and_privacy_projected(client):
    r = client.get("/api/recovery/readiness")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["result"]["status"] == "insufficient_data"
    assert body["result"]["soreness"]["present"] is True
    assert "DEFAULT MODE RAW NOTE CANARY" not in str(body)
    assert "private_sidecar_path" not in str(body)
    assert client.calls[-1] == ("readiness", [])
    assert body["evidence"]["args"] == []
    assert body["evidence"]["snapshot_integrity"]["schema_version"] == 6


@pytest.mark.parametrize("schema_version, lane", [(5, "accepted-v5"), (6, "development-v6"), (7, "development-v7")])
def test_fixed_fictional_scope_is_server_owned_and_evidence_bearing(
    tmp_path, monkeypatch, schema_version, lane,
):
    calls = []
    public_evidence = deepcopy(PUBLIC_EVIDENCE)
    public_evidence["snapshot_integrity"]["schema_version"] = schema_version
    public_evidence.pop("public_evidence_identity")
    public_evidence["public_evidence_identity"] = _sha256(public_evidence)
    fixed_scope = {**FIXED_SCOPE, "readiness_fixture_lane": lane}
    result = {
        "status": "insufficient_data",
        "anchor_date": "2026-06-30",
        "range_from": "2026-03-02",
        "reason": "fewer than 2 eligible recovery components",
        "components": [
            {"key": "sleep", "score": 92, "value": 7.09, "basis": "fixture"},
        ],
        "soreness": {
            "date": "2026-06-30",
            "note": "PRIVATE FICTIONAL NOTE SENTINEL",
        },
        "evidence": public_evidence,
    }
    monkeypatch.setattr(
        bridge,
        "run",
        lambda sub, *args, **kwargs: (
            calls.append((sub, list(args))) or result
        ),
    )
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"),
        "PANEL_COOKIE_SECURE": False,
        "RECOVERY_FIXED_SCOPE": fixed_scope,
    })
    client = app.test_client()
    with app.app_context():
        client.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())

    response = client.get("/api/recovery/readiness")
    assert response.status_code == 200
    body = response.get_json()
    assert calls == [(
        "readiness",
        ["--from", "2026-03-02", "--anchor", "2026-06-30"],
    )]
    assert body["result"]["status"] == "insufficient_data"
    assert body["result"]["components"] == result["components"]
    assert body["result"]["soreness"] == {
        "present": True,
        "date": "2026-06-30",
        "source_label": "fictional-demo",
        "source_locator": "subjective_daily:2026-06-30",
    }
    assert "PRIVATE FICTIONAL NOTE SENTINEL" not in str(body)
    assert body["evidence"]["data_class"] == "fictional"
    assert body["evidence"]["fixture_id"] == "comprehensive-persona-v1"
    assert body["evidence"]["public_evidence_identity"] == (
        public_evidence["public_evidence_identity"]
    )
    assert body["evidence"]["policy"] == {
        "policy_id": "openhealthatlas-readiness-policy",
        "version": "1.0.0",
        "policy_sha256": PUBLIC_EVIDENCE["policy_sha256"],
    }
    assert body["evidence"]["snapshot_integrity"]["status"] == "verified"
    assert "score" not in body["result"]
    # A valid payload cannot be relabelled as another schema lane.
    wrong_lane = "development-v7" if schema_version != 7 else "development-v6"
    app.config["RECOVERY_FIXED_SCOPE"] = {**fixed_scope, "readiness_fixture_lane": wrong_lane}
    assert client.get("/api/recovery/readiness").status_code == 502


@pytest.mark.parametrize(
    "mutation", ["missing", "policy_hash", "range", "status", "sleep_score"],
)
def test_fixed_fictional_scope_rejects_unverified_or_drifted_result(
    tmp_path, monkeypatch, mutation,
):
    result = {
        "status": "insufficient_data",
        "anchor_date": FIXED_SCOPE["anchor_date"],
        "range_from": FIXED_SCOPE["range_from"],
        "reason": "fewer than 2 eligible recovery components",
        "components": [
            {"key": "sleep", "score": 92, "value": 7.09, "basis": "fixture"},
        ],
        "evidence": deepcopy(PUBLIC_EVIDENCE),
    }
    if mutation == "missing":
        result.pop("evidence")
    elif mutation == "policy_hash":
        result["evidence"]["policy_sha256"] = "sha256:" + "f" * 64
    elif mutation == "range":
        result["range_from"] = "2026-03-03"
    elif mutation == "status":
        result["status"] = "ok"
        result["score"] = 58
        result["band"] = "warn"
    else:
        result["components"][0]["score"] = 58
    monkeypatch.setattr(bridge, "run", lambda *args, **kwargs: result)
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"),
        "PANEL_COOKIE_SECURE": False,
        "RECOVERY_FIXED_SCOPE": FIXED_SCOPE,
    })
    client = app.test_client()
    with app.app_context():
        client.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())

    response = client.get("/api/recovery/readiness")
    assert response.status_code == 502
    assert response.get_json() == {
        "ok": False,
        "error": "governed recovery result failed validation",
    }


def test_fixed_scope_fails_closed_and_query_cannot_override_it(
    tmp_path, monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        bridge, "run", lambda *args, **kwargs: calls.append(args),
    )
    bad_scope = {**FIXED_SCOPE, "anchor_date": "not-a-date"}
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "panel.db"),
        "PANEL_COOKIE_SECURE": False,
        "RECOVERY_FIXED_SCOPE": bad_scope,
    })
    client = app.test_client()
    with app.app_context():
        client.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())

    assert client.get("/api/recovery/readiness").status_code == 503
    assert client.get(
        "/api/recovery/readiness?anchor=2026-06-30"
    ).status_code == 400
    assert calls == []


def test_readiness_passthrough_bridge_error(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "run",
                        lambda *a, **k: (_ for _ in ()).throw(bridge.BridgeError("broker down")))
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p.db"), "PANEL_COOKIE_SECURE": False})
    c = app.test_client()
    with app.app_context():
        c.set_cookie(auth_mod.SESSION_COOKIE, auth_mod.create_session())
    r = c.get("/api/recovery/readiness")
    assert r.status_code == 502
    assert r.get_json() == {"ok": False, "error": "broker down"}


def test_requires_auth(tmp_path):
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
                      "PANEL_DB": str(tmp_path / "p2.db"), "PANEL_COOKIE_SECURE": False})
    c = app.test_client()
    assert c.get("/api/recovery/readiness").status_code == 401


def test_recovery_frontend_preserves_insufficient_state_and_visible_policy():
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    source = (
        root
        / "app" / "static" / "js" / "readiness_detail.js"
    ).read_text(encoding="utf-8")
    assert 'r.status !== "ok"' in source
    assert '${r.status} · ${r.reason' in source
    assert "policy.policy_id" in source
    assert "policy.policy_sha256" in source
    assert "public_evidence_identity" in source
    assert "snapshot ${integrity.status" in source
    assert "raw note stays outside the browser projection" in source
    assert "No soreness summary is available for this fictional snapshot." in source
    assert "subjective_daily.soreness_note" not in source
    assert "tell the coach" not in source
    assert 'component.status === "excluded"' in source
    assert "same-source ${warning.source_label}" in source
    assert "other-source observations excluded" in source
    assert "58/100" not in source
    template = (root / "app" / "templates" / "readiness_detail.html").read_text(
        encoding="utf-8"
    )
    assert "fictional-fixture" in template
    assert "Soreness summary" in template
    assert "Soreness note" not in template
    assert "your own baselines" not in template
