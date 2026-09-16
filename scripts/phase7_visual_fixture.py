"""Local-only deterministic Phase 7 visual-review server.

It uses a temporary panel database, bypasses passkeys only in this process,
and stubs every Hermes/engine call. It never contacts a model or live service.
"""
import os
import hashlib
import json
from pathlib import Path
import re
import sys

from app import auth, bridge, create_app

FINDING = {
    "finding_id": "sha256:" + "d" * 64,
    "outcome": {
        "key": "subjective.day_rating", "display": "Day rating",
        "mode": "green-vs-non-green",
    },
    "exposure": {"components": [{
        "exposure_key": "sleep.duration_hours", "display": "Sleep duration",
        "unit": "hours", "temporal_type": "outcome",
        "direction": "target_range", "lag_days": 1, "window_days": 1,
        "transform": "point",
        "merge_rule": "sleep_log_then_selected_daily_metrics",
        "zero_semantics": "invalid",
        "temporal_direction": "exposure_precedes_outcome",
    }]},
    "sample": {
        "eligible_n": 90, "complete_n": 72, "missing_n": 18,
        "exposed_n": 31, "unexposed_n": 41,
    },
    "effect": {"estimate": 0.18, "ci95": [0.04, 0.31]},
    "testing": {"q": 0.08},
    "stability": {
        "status": "stable", "full": 0.18,
        "first_half": 0.15, "second_half": 0.2,
    },
    "confounders": {
        "checked": ["weekend", "training day"],
        "sensitive_to": ["training day"], "weighted_effect": 0.11,
    },
    "warnings": ["association_not_causation", "confounder_sensitive"],
    "quality": {
        "tier": "exploratory_unreplicated", "eligible_for_hypothesis": True,
    },
    "evidence_for": [
        {"code": "effect_gate_pass", "value": 0.18, "threshold": 0.1},
    ],
    "evidence_against": [
        {"code": "confounder_attenuation", "detail": "training day"},
    ],
    "provenance": {
        "analysis_version": "outcome-v1",
        "registry_version": "feature-registry-v1",
        "engine_sha256": (
            "sha256:46afb3a71875217d43ecd2bfda7e1268c"
            "cab1da86b1e3a69eb74a927984e5723"
        ),
        "registry_sha256": "sha256:" + "2" * 64,
        "input_fingerprint": "sha256:" + "e" * 64,
        "dependencies": {},
    },
}


_visual_jobs = {}


def fake_bridge(subcommand, *args, **_kwargs):
    if subcommand == "analysis-job-start":
        descriptor = json.loads(_kwargs["stdin"])
        job_id = hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()[:32]
        _visual_jobs[job_id] = descriptor
        return {"ok": True, "job_id": job_id, "status": "queued", "request": descriptor}
    if subcommand == "analysis-job-status":
        descriptor = _visual_jobs.get(args[0])
        if descriptor is None:
            raise bridge.BridgeError("Fictional job not found.", code="job_not_found")
        return {"ok": True, "job_id": args[0], "status": "completed", "request": descriptor,
                "result": fake_bridge(descriptor["command"], *descriptor["args"])}
    if subcommand == "outcome-associations":
        return {
            "ok": True, "contract_version": "outcome-associations-v1",
            "meta": {
                "analysis_version": "outcome-v1",
                "registry_version": "feature-registry-v1",
                "engine_sha256": (
                    "sha256:46afb3a71875217d43ecd2bfda7e1268c"
                    "cab1da86b1e3a69eb74a927984e5723"
                ),
                "registry_sha256": "sha256:" + "2" * 64,
                "input_fingerprint": "sha256:" + "e" * 64,
                "analysis_range": {
                    "kind": "bounded", "from": "2026-07-01", "to": "2026-07-23",
                },
                "baseline_range": {
                    "kind": "bounded", "from": "2026-05-28", "to": "2026-07-23",
                },
            },
            "coverage": {
                "source_manifests": [{
                    "table": "sleep_log",
                    "adapter": "daily",
                    "merge_rule": "sleep_log_then_selected_daily_metrics",
                    "source_labels": ["Fitbit"],
                    "natural_key_scheme": "table-prefixed-natural-key-v1",
                    "row_count": 72,
                    "date_from": "2026-05-28",
                    "date_to": "2026-07-23",
                    "digest": "sha256:" + "3" * 64,
                }],
            },
            "findings": [FINDING], "warnings": [],
        }
    if subcommand == "data-readiness":
        states = [
            "logic_not_implemented", "present_not_connected",
            "implemented_never_logged", "stale",
            "too_sparse_for_analysis", "sufficient",
        ]
        return {
            "ok": True,
            "meta": {
                "range": {
                    "kind": "bounded", "from": "2026-07-01", "to": "2026-07-23",
                },
                "predicate_order": states,
                "state_counts": {state: index for index, state in enumerate(states)},
            },
            "features": [{"key": "sleep.duration_hours", "state": "sufficient"}],
        }
    if subcommand == "hypotheses":
        return {
            "ok": True, "contract_version": "hypothesis-ledger-v1",
            "items": [{
                "hypothesis_id": "sha256:" + "a" * 64,
                "outcome_key": "subjective.day_rating",
                "created_at": "2026-07-18T08:10:00+02:00",
                "components": [{
                    "exposure_key": "sleep.duration_hours",
                    "lag_days": 1, "window_days": 1,
                }],
                "latest_evaluation": {"status": "strengthening"},
            }],
            "next_before": None,
        }
    if subcommand == "hypothesis-brief":
        return {
            "ok": True, "contract_version": "hypothesis-ledger-v1",
            "hypothesis": {
                "hypothesis_id": "sha256:" + "a" * 64,
                "components": [{
                    "exposure_key": "sleep.duration_hours",
                    "lag_days": 1, "window_days": 1,
                }],
                "latest_evaluation": {
                    "status": "strengthening", "evidence_class": "supportive",
                    "confidence": "exploratory",
                    "range_from": "2026-04-01", "range_to": "2026-07-18",
                    "sample_size": {"complete_n": 72, "missing_n": 18},
                    "effect_summary": {
                        "direction": "positive", "effect": FINDING["effect"],
                        "testing": FINDING["testing"], "quality": FINDING["quality"],
                        "provenance": FINDING["provenance"],
                    },
                    "stability": FINDING["stability"],
                    "confounders": FINDING["confounders"],
                },
            },
            "evaluations": [], "evidence_for": [{
                "range_from": "2026-04-01", "range_to": "2026-07-18",
                "finding": FINDING,
            }],
            "evidence_against": [], "annotations": [],
            "latest_annotations": [
                {
                    "annotation_kind": "alternative",
                    "content": "Training load may explain part of the pattern.",
                },
                {
                    "annotation_kind": "next_experiment",
                    "content": "Keep logging day rating after comparable training days.",
                },
            ],
        }
    if subcommand == "synthesis-history":
        return {
            "ok": True, "contract": "synthesis-v1",
            "syntheses": [{
                "synthesis_id": "sha256:" + "b" * 64, "cadence": "weekly",
                "created_at": "2026-07-20T08:05:00+02:00",
                "cutoff_date": "2026-07-19",
                "run_refs": [
                    {"purpose": "analysis"}, {"purpose": "wider baseline"},
                ],
                "rendered_md": (
                    "What changed\n\nSleep duration remains an exploratory candidate. "
                    "The wider baseline is consistent, but training-day sensitivity remains."
                ),
            }],
            "next_before": None,
        }
    if subcommand == "insight-run-status":
        return {
            "ok": True, "contract_version": "insight-orchestrator-v1",
            "read_only": True,
            "batches": [{
                "run_kind": "nightly", "status": "no-novelty",
                "anchor_date": "2026-07-22",
            }],
            "triggers": [],
            "notifications": [{
                "status": "suppressed", "reason": "delivery_disabled",
            }],
        }
    if subcommand == "hypothesis-promote":
        return {
            "ok": True, "contract_version": "hypothesis-ledger-v1",
            "status": "created", "hypothesis_id": "sha256:" + "a" * 64,
        }
    if subcommand == "hermes-chat":
        return {"ok": True, "reply": "This is a deterministic visual-review reply."}
    if subcommand == "muscle-map":
        lens = args[args.index("--lens") + 1] if "--lens" in args else "pain"
        common = {
            "window_days": 90,
            "non_muscle": ["head", "face", "hand-left", "hand-right"],
        }
        if lens == "pain":
            return {
                **common, "lens": "pain",
                "regions": {
                    "knee-left": {
                        "status": "moderate", "group": None,
                        "tips": ["latest explicit NRS 4"],
                    },
                    "hip-flexor-left": {
                        "status": "mild", "group": "Legs", "tips": [],
                    },
                },
                "legend": [
                    {"status": "none", "label": "none logged"},
                    {"status": "mild", "label": "mild"},
                    {"status": "moderate", "label": "moderate"},
                    {"status": "severe", "label": "severe"},
                ],
                "loop": [], "cv_note": "Pain evidence is descriptive, not diagnostic.",
                "boundary_note": "",
            }
        return {
            **common, "lens": "mobility",
            "regions": {
                "calves-soleus-left": {
                    "status": "restricted", "group": "Legs", "tips": [],
                },
                "hip-flexor-left": {
                    "status": "normal", "group": "Legs", "tips": [],
                },
            },
            "legend": [
                {"status": "restricted", "label": "restricted"},
                {"status": "normal", "label": "within norm"},
                {"status": "untested", "label": "untested"},
            ],
            "tests": [], "flexibility_note": "A screen, not a diagnosis.",
        }
    return {"ok": True}


auth.current_session = lambda: {"token_hash": "visual-fixture"}
bridge.run = fake_bridge
database = os.environ.get("HERMES_PHASE7_VISUAL_DB", "/private/tmp/hermes-phase7-visual.db")
try:
    os.unlink(database)
except FileNotFoundError:
    pass
application = create_app({
    "PANEL_DB": database, "PANEL_COOKIE_SECURE": False,
    "WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False,
})


STATIC_CSRF_MARKER = "PHASE7_STATIC_FIXTURE_CSRF_TOKEN"


def normalize_static_security_fields(html):
    """Remove only request-unique CSRF signatures from local proof artifacts.

    Production rendering and CSRF generation have already happened. This
    post-render normalization is restricted to the static fixture output and
    uses an intentionally invalid marker that cannot authenticate a request.
    Unexpected template structure fails closed instead of broadening the
    normalization to other security fields.
    """
    html, meta_count = re.subn(
        r'(<meta name="csrf-token" content=")[^"]+(">)',
        rf"\g<1>{STATIC_CSRF_MARKER}\2",
        html,
    )
    html, input_count = re.subn(
        r'(<input type="hidden" name="csrf_token" value=")[^"]+(">)',
        rf"\g<1>{STATIC_CSRF_MARKER}\2",
        html,
    )
    if meta_count != 1 or input_count != 2:
        raise RuntimeError(
            "unexpected CSRF field inventory in Phase 7 static fixture"
        )
    return html


def render_static_fallback():
    """Render actual Jinja templates/assets with a deterministic fetch shim."""
    root = Path(__file__).resolve().parents[1]
    static_root = (root / "app" / "static").as_uri() + "/"
    fixture = (root / "tests" / "fixtures" / "phase7_fetch_fixture.js").as_uri()
    output_root = Path(
        os.environ.get("HERMES_PHASE7_VISUAL_OUTPUT_DIR", "/private/tmp")
    )
    output_root.mkdir(parents=True, exist_ok=True)
    targets = {
        "/insights": output_root / "hermes-phase7-insights.html",
        "/insights/conversations": output_root / "hermes-phase7-conversations.html",
        "/training/pain": output_root / "hermes-phase7-pain.html",
        "/training/mobility": output_root / "hermes-phase7-mobility.html",
    }
    client = application.test_client()
    for route, destination in targets.items():
        response = client.get(route)
        if response.status_code != 200:
            raise RuntimeError(f"{route} render failed: {response.status_code}")
        html = response.get_data(as_text=True)
        html = html.replace('href="/static/', f'href="{static_root}')
        html = html.replace('src="/static/', f'src="{static_root}')
        html = html.replace(
            "</head>",
            f'<script src="{fixture}" defer></script>\n</head>',
        )
        html = normalize_static_security_fields(html)
        destination.write_text(html, encoding="utf-8")
        print(destination)


if __name__ == "__main__":
    if sys.argv[1:] == ["--render-static"]:
        render_static_fallback()
    else:
        application.run(host="127.0.0.1", port=5107, debug=False, use_reloader=False)
