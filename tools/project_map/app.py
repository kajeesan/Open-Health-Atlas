#!/usr/bin/env python3
"""Local-only project map for the audited OpenHealthAtlas repository."""

from __future__ import annotations

import csv
import json
import os
from collections import Counter
from copy import deepcopy
from pathlib import Path

try:
    from flask import Flask, jsonify, render_template
except ModuleNotFoundError as exc:  # pragma: no cover - startup guidance
    raise SystemExit(
        "Flask is not installed in this Python environment. From the repository "
        "root run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    ) from exc


TOOL_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOL_DIR.parents[1]
DATA_PATH = TOOL_DIR / "project_map.json"
DEFAULT_AUDIT_DIR = TOOL_DIR / "forensic-audit"
AUDIT_FILENAMES = (
    "EXECUTIVE_ASSESSMENT.md",
    "COVERAGE_LEDGER.tsv",
    "ARCHITECTURE_AND_JOURNEYS.md",
    "COLLISION_REGISTER.md",
    "COMPLEXITY_REGISTER.md",
    "RECOVERY_SEQUENCE.md",
)

app = Flask(__name__)
app.config.update(JSON_SORT_KEYS=False)


def _audit_dir() -> Path:
    configured = os.environ.get("OHA_FORENSIC_AUDIT_DIR")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_AUDIT_DIR


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _coverage_summary(audit_dir: Path) -> dict:
    ledger_path = audit_dir / "COVERAGE_LEDGER.tsv"
    if not ledger_path.is_file():
        return {"available": False, "rows": 0, "classifications": {}}

    classifications: Counter[str] = Counter()
    journey_classifications: Counter[str] = Counter()
    rows = 0
    journeys = 0
    with ledger_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            rows += 1
            classification = row.get("Primary classification", "unverified")
            classifications[classification] += 1
            if row.get("ID", "").startswith("J"):
                journeys += 1
                journey_classifications[classification] += 1
    return {
        "available": True,
        "rows": rows,
        "journeys": journeys,
        "classifications": dict(classifications),
        "journeyClassifications": dict(journey_classifications),
    }


def _graph_context(node_sources: dict[str, list[str]]) -> dict:
    graph_path = REPO_ROOT / "graphify-out" / "graph.json"
    if not graph_path.is_file():
        return {"available": False, "nodes": 0, "links": 0, "matches": {}}

    graph = _load_json(graph_path)
    graph_nodes = graph.get("nodes", [])
    selected_sources = {source for sources in node_sources.values() for source in sources}
    nodes_by_source: dict[str, list[dict]] = {source: [] for source in selected_sources}

    for node in graph_nodes:
        source = node.get("source_file")
        if source in nodes_by_source and len(nodes_by_source[source]) < 4:
            nodes_by_source[source].append(
                {
                    "label": node.get("label", node.get("id", "Unknown node")),
                    "location": node.get("source_location", ""),
                    "confidence": node.get("_origin", "unknown"),
                }
            )

    matches = {
        node_id: [
            {
                "source": source,
                "nodes": nodes_by_source.get(source, []),
            }
            for source in sources
            if nodes_by_source.get(source)
        ]
        for node_id, sources in node_sources.items()
    }
    return {
        "available": True,
        "nodes": len(graph_nodes),
        "links": len(graph.get("links", [])),
        "snapshot": "main @ f17025dfcf47 (32 audited HEAD files differ)",
        "warning": "Navigation evidence only; current source and runtime evidence decide status.",
        "matches": matches,
    }


def build_payload() -> dict:
    payload = deepcopy(_load_json(DATA_PATH))
    audit_dir = _audit_dir()
    payload["liveContext"] = {
        "auditDirectory": str(audit_dir),
        "auditFiles": {
            filename: (audit_dir / filename).is_file() for filename in AUDIT_FILENAMES
        },
        "coverage": _coverage_summary(audit_dir),
        "graphify": _graph_context(payload.get("graphSources", {})),
    }
    return payload


@app.after_request
def add_local_security_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    )
    return response


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/map")
def map_data():
    return jsonify(build_payload())


@app.get("/healthz")
def healthz():
    return jsonify(ok=True, service="openhealthatlas-project-map")


def _port() -> int:
    raw = os.environ.get("OHA_PROJECT_MAP_PORT", "5127")
    try:
        port = int(raw)
    except ValueError as exc:
        raise SystemExit("OHA_PROJECT_MAP_PORT must be an integer.") from exc
    if not 1024 <= port <= 65535:
        raise SystemExit("OHA_PROJECT_MAP_PORT must be between 1024 and 65535.")
    return port


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=_port(), debug=False)
