"""Bounded, read-only OpenHealthAtlas capabilities for an external Hermes.

The module contains no model and writes no data.  Hermes decides which
capability to call and how to interpret the returned deterministic evidence.
Requests arrive as one exact JSON object on stdin and responses are one
canonical JSON object on stdout.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
from typing import Any, Iterable, Mapping

from . import associations, exact_cache, runtime
from .contracts import DateRange, FeatureDefinition, canonical_json
from .frame import (
    ADAPTER_MODULE_NAMES,
    adapter_status,
    build_feature_frame,
    load_adapters,
)
from .provenance import sha256_id
from .registry import build_registry, serialize_registry


SURFACE_CONTRACT = "openhealthatlas-hermes-surface-v1"
EVIDENCE_REF_CONTRACT = "openhealthatlas-evidence-ref-v1"
OPERATIONS = frozenset({
    "health_catalog", "health_query", "health_analyze", "health_evidence",
})
REPLAYABLE_OPERATIONS = frozenset(OPERATIONS - {"health_evidence"})
STATUSES = frozenset({"ok", "insufficient_data", "unsupported", "refused"})
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
FEATURE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,159}$")
FIXTURE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
MAX_STDIN_BYTES = 131_072
MAX_RESPONSE_BYTES = 1_048_576
MAX_FEATURE_KEYS = 32
MAX_SERIES_ROWS = 512
MAX_EVIDENCE_REFS = 20
MAX_EVIDENCE_WORK_UNITS = 500
MAX_EVIDENCE_ANALYSIS_REFS = 1
MAX_RANGE_DAYS = 366
MAX_CATALOG_LIMIT = 50
MAX_TEXT_VALUE = 160
ALLOWED_MODES = frozenset(associations.MODE_VALUES)
DEFAULT_AGGREGATES = ("count", "min", "max", "mean", "median")
ALLOWED_AGGREGATES = frozenset({
    "count", "min", "max", "mean", "median", "sum", "stddev",
})
FORBIDDEN_REQUEST_KEYS = frozenset({
    "sql", "query_sql", "table", "tables", "column", "columns", "join",
    "expression", "database", "database_path", "health_db", "vault",
    "vault_path", "file", "filename", "path", "private_data", "write",
    "insert", "update", "delete", "raw_note", "note",
})


class SurfaceError(ValueError):
    """A closed-contract refusal that is safe to return to Hermes."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SurfaceBinding:
    """Caller-owned database and response identity, never request arguments."""

    database: str
    contract: str
    data_class: str
    identity_key: str
    identity_value: str

    def envelope(self) -> dict[str, str]:
        return {"contract": self.contract, "data_class": self.data_class,
                self.identity_key: self.identity_value}


def _canonical(value: Any) -> str:
    return canonical_json(value)


def _sha(value: Any) -> str:
    return sha256_id(value)


def _closed_mapping(
    value: object,
    *,
    allowed: Iterable[str],
    required: Iterable[str] = (),
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SurfaceError("validation_error", f"{label} must be an object")
    allowed_set = set(allowed)
    required_set = set(required)
    actual = set(value)
    if not required_set <= actual or not actual <= allowed_set:
        raise SurfaceError(
            "validation_error", f"{label} has unsupported or missing fields",
        )
    return dict(value)


def _reject_forbidden_keys(value: object, path: str = "arguments") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise SurfaceError("validation_error", f"{path} has a non-text key")
            if key.lower() in FORBIDDEN_REQUEST_KEYS:
                raise SurfaceError(
                    "validation_error", f"{path}.{key} is outside the read-only surface",
                )
            _reject_forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, f"{path}[{index}]")


def _bounded_text(
    value: object, label: str, *, maximum: int = 160, optional: bool = False,
) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise SurfaceError("validation_error", f"{label} must be text")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise SurfaceError("validation_error", f"{label} is empty or too long")
    return normalized


def _unique_strings(
    value: object,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = MAX_FEATURE_KEYS,
    pattern: re.Pattern[str] | None = None,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise SurfaceError(
            "validation_error", f"{label} must contain {minimum} to {maximum} values",
        )
    result: list[str] = []
    for item in value:
        text = _bounded_text(item, label, maximum=160)
        assert text is not None
        if pattern is not None and pattern.fullmatch(text) is None:
            raise SurfaceError("validation_error", f"{label} contains an invalid value")
        result.append(text)
    if len(result) != len(set(result)):
        raise SurfaceError("validation_error", f"{label} values must be unique")
    return sorted(result)


def _date_range(value: object) -> tuple[dict[str, str], DateRange]:
    raw = _closed_mapping(
        value, allowed={"from", "to"}, required={"from", "to"}, label="range",
    )
    try:
        start = date.fromisoformat(raw["from"])
        end = date.fromisoformat(raw["to"])
    except (TypeError, ValueError) as exc:
        raise SurfaceError("validation_error", "range must use canonical ISO dates") from exc
    if start.isoformat() != raw["from"] or end.isoformat() != raw["to"]:
        raise SurfaceError("validation_error", "range must use canonical ISO dates")
    if start > end:
        raise SurfaceError("validation_error", "range.from must not follow range.to")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise SurfaceError("validation_error", "range is too large")
    return {"from": start.isoformat(), "to": end.isoformat()}, DateRange(start, end)


def _identity() -> tuple[str, str]:
    data_class = os.environ.get("OPENHEALTHATLAS_DATA_CLASS", "fictional")
    fixture_id = os.environ.get(
        "OPENHEALTHATLAS_FIXTURE_ID", "standalone-fictional",
    )
    if data_class != "fictional":
        raise SurfaceError("refused", "the current surface is fictional-only")
    if FIXTURE_ID_RE.fullmatch(fixture_id) is None:
        raise SurfaceError("refused", "server fixture identity is invalid")
    return data_class, fixture_id


def _engine_manifest() -> list[dict[str, str]]:
    root = Path(__file__).resolve().parent
    toolkit_root = root.parent
    names = [
        "health.py",
        "hermes_insights/__init__.py",
        "hermes_insights/_native_stats.py",
        "hermes_insights/native/rank_products.c",
        "hermes_insights/adapters/__init__.py",
        "hermes_insights/hermes_surface.py",
        "hermes_insights/exact_cache.py",
        "hermes_insights/runtime.py",
        "hermes_insights/cli.py",
        "hermes_insights/command_context.py",
        "hermes_insights/commands/__init__.py",
        "hermes_insights/commands/hevy.py",
        "hermes_insights/importers/__init__.py",
        "hermes_insights/importers/hevy_csv.py",
        "hermes_insights/commands/cronometer.py",
        "hermes_insights/commands/google_health.py",
        "hermes_insights/commands/lab_catalog.py",
        "hermes_insights/commands/recipes.py",
        "hermes_insights/commands/submuscle_map.py",
        "hermes_insights/importers/common.py",
        "hermes_insights/importers/hevy_json.py",
        "hermes_insights/importers/cronometer.py",
        "hermes_insights/importers/google_health.py",
        "hermes_insights/importers/lab_catalog.py",
        "hermes_insights/importers/recipes.py",
        "hermes_insights/importers/submuscle_map.py",
        "hermes_insights/body_contracts.py",
        "hermes_insights/fitness_contracts.py",
        "hermes_insights/routine_history.py",
        "hermes_insights/capture_contracts.py",
        "hermes_insights/food.py",
        "hermes_insights/nutrition.py",
        "hermes_insights/score_contracts.py",
        "hermes_insights/commands/food.py",
        "hermes_insights/commands/nutrition.py",
        "hermes_insights/fitness.py",
        "hermes_insights/muscles.py",
        "hermes_insights/figure_contracts.py",
        "hermes_insights/physio.py",
        "hermes_insights/muscle_figure.py",
        "hermes_insights/body_measurements.py",
        "hermes_insights/commands/training.py",
        "hermes_insights/commands/fitness.py",
        "hermes_insights/commands/muscles.py",
        "hermes_insights/commands/physio.py",
        "hermes_insights/daily_frames.py",
        "hermes_insights/scores.py",
        "hermes_insights/recovery.py",
        "hermes_insights/labs.py",
        "hermes_insights/commands/daily_frames.py",
        "hermes_insights/commands/scores.py",
        "hermes_insights/commands/recovery.py",
        "hermes_insights/commands/labs.py",
        "hermes_insights/commands/collectors.py",
        "hermes_insights/commands/daily_capture.py",
        "hermes_insights/commands/followthrough.py",
        "hermes_insights/commands/notes.py",
        "hermes_insights/commands/schedules.py",
        "hermes_insights/commands/analytical.py",
        "hermes_insights/commands/schema.py",
        "hermes_insights/commands/events.py",
        "hermes_insights/commands/features.py",
        "hermes_insights/commands/associations.py",
        "hermes_insights/commands/analysis_jobs.py",
        "hermes_insights/commands/ledger.py",
        "hermes_insights/commands/synthesis.py",
        "hermes_insights/commands/orchestration.py",
        "hermes_insights/commands/scheduled_analysis.py",
        "hermes_insights/followthrough.py",
        "hermes_insights/importers/open_meteo.py",
        "hermes_insights/schedules.py",
        "hermes_insights/vault_notes.py",
        "hermes_insights/catalogs.py",
        "hermes_insights/calculations.py",
        "hermes_insights/contracts.py",
        "hermes_insights/events.py",
        "hermes_insights/goals.py",
        "hermes_insights/registry.py",
        "hermes_insights/frame.py",
        "hermes_insights/readiness.py",
        "hermes_insights/readiness_ancestry.py",
        "hermes_insights/associations.py",
        "hermes_insights/interactions.py",
        "hermes_insights/_ledger_contracts.py",
        "hermes_insights/_ledger_validation.py",
        "hermes_insights/ledger.py",
        "hermes_insights/migrations.py",
        "hermes_insights/normalize.py",
        "hermes_insights/orchestrator.py",
        "hermes_insights/provenance.py",
        "hermes_insights/stats.py",
        "hermes_insights/settings.py",
        "hermes_insights/synthesis.py",
        *(
            f"hermes_insights/adapters/{name}.py"
            for name in ADAPTER_MODULE_NAMES
        ),
    ]
    manifest: list[dict[str, str]] = []
    for name in sorted(names):
        content = (toolkit_root / name).read_bytes()
        manifest.append({
            "file": name,
            "sha256": hashlib.sha256(content).hexdigest(),
        })
    return manifest


def _engine_id() -> str:
    return _sha(_engine_manifest())


def _registry_id(definitions: Iterable[FeatureDefinition]) -> str:
    value = serialize_registry(definitions)["registry_sha256"]
    if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
        return f"sha256:{value}"
    if isinstance(value, str) and SHA256_RE.fullmatch(value):
        return value
    raise SurfaceError("engine_error", "registry identity is invalid")


def _result_payload(
    *,
    operation: str,
    binding: SurfaceBinding,
    status: str,
    range_value: dict[str, str] | None,
    registry_id: str,
    engine_id: str,
    coverage: object,
    facts: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    limitations: list[dict[str, Any]],
    normalized_arguments: dict[str, Any],
    evidence_ref_findings: Iterable[str] = (),
    include_refs: bool = True,
) -> dict[str, Any]:
    if status not in STATUSES:
        raise SurfaceError("engine_error", "surface status is invalid")
    preimage = {
        **binding.envelope(),
        "operation": operation,
        "status": status,
        "range": range_value,
        "registry_id": registry_id,
        "engine_id": engine_id,
        "coverage": coverage,
        "facts": facts,
        "findings": findings,
        "limitations": limitations,
    }
    result_id = _sha(preimage)
    evidence_refs: list[dict[str, Any]] = []
    if include_refs and operation in REPLAYABLE_OPERATIONS:
        evidence_refs.append({
            "contract": EVIDENCE_REF_CONTRACT,
            "operation": operation,
            "arguments": normalized_arguments,
            "result_id": result_id,
        })
        for finding_id in evidence_ref_findings:
            evidence_refs.append({
                "contract": EVIDENCE_REF_CONTRACT,
                "operation": operation,
                "arguments": normalized_arguments,
                "result_id": result_id,
                "finding_id": finding_id,
            })
    response = {**preimage, "result_id": result_id, "evidence_refs": evidence_refs}
    if len(_canonical(response).encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise SurfaceError("response_too_large", "bounded response limit exceeded")
    return response


def _definition_descriptor(
    definition: FeatureDefinition, connected: bool,
) -> dict[str, Any]:
    operations = ["query"]
    if "outcome" in definition.roles:
        operations.append("analyze_as_outcome")
    if "exposure" in definition.roles and definition.candidate_enabled:
        operations.append("analyze_as_exposure")
    return {
        "key": definition.key,
        "display_name": definition.display_name,
        "pillar": definition.pillar,
        "unit": definition.unit,
        "value_kind": definition.value_kind,
        "temporal_type": definition.temporal_type,
        "direction": definition.direction,
        "roles": list(definition.roles),
        "implemented": definition.implemented,
        "candidate_enabled": definition.candidate_enabled,
        "adapter": definition.adapter,
        "availability": "connected" if connected else "not_connected",
        "supported_operations": operations,
    }


def _normalize_catalog(arguments: object) -> dict[str, Any]:
    raw = _closed_mapping(
        arguments,
        allowed={"search", "domains", "roles", "availability", "cursor", "limit"},
        label="health_catalog arguments",
    )
    search = _bounded_text(
        raw.get("search"), "search", maximum=80, optional=True,
    )
    domains = _unique_strings(raw.get("domains", []), "domains", maximum=12)
    roles = _unique_strings(raw.get("roles", []), "roles", maximum=8)
    availability = _unique_strings(
        raw.get("availability", []), "availability", maximum=4,
    )
    if not set(availability) <= {
        "implemented", "not_implemented", "connected", "not_connected",
    }:
        raise SurfaceError("validation_error", "availability filter is unsupported")
    cursor_raw = raw.get("cursor", "0")
    if cursor_raw is None:
        cursor_raw = "0"
    if (
        not isinstance(cursor_raw, str)
        or not cursor_raw.isascii()
        or not cursor_raw.isdigit()
        or len(cursor_raw) > 9
    ):
        raise SurfaceError("validation_error", "cursor is invalid")
    cursor = int(cursor_raw)
    limit = raw.get("limit", 20)
    if type(limit) is not int or not 1 <= limit <= MAX_CATALOG_LIMIT:
        raise SurfaceError("validation_error", "catalog limit is outside the supported range")
    return {
        "search": search,
        "domains": domains,
        "roles": roles,
        "availability": availability,
        "cursor": str(cursor),
        "limit": limit,
    }


def _health_catalog(
    arguments: object,
    definitions: tuple[FeatureDefinition, ...],
    engine_id: str,
    *,
    binding: SurfaceBinding,
    include_refs: bool = True,
) -> dict[str, Any]:
    normalized = _normalize_catalog(arguments)
    status_by_adapter = adapter_status(definitions, load_adapters())
    known_domains = {item.pillar for item in definitions}
    known_roles = {role for item in definitions for role in item.roles}
    unknown_domains = sorted(set(normalized["domains"]) - known_domains)
    unknown_roles = sorted(set(normalized["roles"]) - known_roles)
    if unknown_domains or unknown_roles:
        return _result_payload(
            binding=binding,
            operation="health_catalog",
            status="unsupported",
            range_value=None,
            registry_id=_registry_id(definitions),
            engine_id=engine_id,
            coverage={"registry_features": len(definitions), "matched": 0, "returned": 0},
            facts=[],
            findings=[],
            limitations=[{
                "code": "unknown_catalog_filter",
                "unknown_domains": unknown_domains,
                "unknown_roles": unknown_roles,
            }],
            normalized_arguments=normalized,
            include_refs=include_refs,
        )
    tokens = (normalized["search"] or "").casefold().split()
    matched: list[FeatureDefinition] = []
    for definition in definitions:
        connected = bool(status_by_adapter[definition.adapter]["connected"])
        searchable = " ".join((
            definition.key,
            definition.display_name,
            definition.pillar,
            definition.unit,
            " ".join(definition.roles),
        )).casefold()
        if tokens and not all(token in searchable for token in tokens):
            continue
        if normalized["domains"] and definition.pillar not in normalized["domains"]:
            continue
        if normalized["roles"] and not set(normalized["roles"]) <= set(definition.roles):
            continue
        states = {
            "implemented" if definition.implemented else "not_implemented",
            "connected" if connected else "not_connected",
        }
        if normalized["availability"] and not set(normalized["availability"]) <= states:
            continue
        matched.append(definition)
    offset = int(normalized["cursor"])
    page = matched[offset: offset + normalized["limit"]]
    facts = [
        _definition_descriptor(
            item, bool(status_by_adapter[item.adapter]["connected"]),
        )
        for item in page
    ]
    next_offset = offset + len(page)
    return _result_payload(
        binding=binding,
        operation="health_catalog",
        status="ok" if facts else "unsupported",
        range_value=None,
        registry_id=_registry_id(definitions),
        engine_id=engine_id,
        coverage={
            "registry_features": len(definitions),
            "matched": len(matched),
            "returned": len(page),
            "cursor": str(offset),
            "next_cursor": str(next_offset) if next_offset < len(matched) else None,
        },
        facts=facts,
        findings=[],
        limitations=([] if facts else [{
            "code": "no_registered_feature_match",
            "message": "No registered feature matched the deterministic filters.",
        }]),
        normalized_arguments=normalized,
        include_refs=include_refs,
    )


def _normalize_query(arguments: object) -> tuple[dict[str, Any], DateRange]:
    raw = _closed_mapping(
        arguments,
        allowed={"feature_keys", "range", "view", "grain", "aggregates"},
        required={"feature_keys", "range", "view"},
        label="health_query arguments",
    )
    feature_keys = _unique_strings(
        raw["feature_keys"], "feature_keys", minimum=1, pattern=FEATURE_KEY_RE,
    )
    range_value, requested = _date_range(raw["range"])
    view = _bounded_text(raw["view"], "view", maximum=32)
    if view not in {"latest", "series", "summary", "period_compare"}:
        raise SurfaceError("validation_error", "query view is unsupported")
    grain = raw.get("grain", "day")
    if grain is None:
        grain = "day"
    if grain != "day":
        raise SurfaceError(
            "unsupported", "the first bounded surface supports day grain only",
        )
    aggregates = _unique_strings(
        raw.get("aggregates", list(DEFAULT_AGGREGATES)),
        "aggregates",
        maximum=len(ALLOWED_AGGREGATES),
    )
    if not set(aggregates) <= ALLOWED_AGGREGATES:
        raise SurfaceError("validation_error", "query aggregate is unsupported")
    normalized = {
        "feature_keys": feature_keys,
        "range": range_value,
        "view": view,
        "grain": "day",
        "aggregates": aggregates,
    }
    return normalized, requested


def _public_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return round(value, 8)
    if isinstance(value, str) and len(value) <= MAX_TEXT_VALUE:
        return value
    raise SurfaceError("unsupported", "a selected feature produced an unsupported value")


def _public_observation(row: Mapping[str, Any]) -> dict[str, Any]:
    source = row.get("source")
    if source is not None and (
        not isinstance(source, str) or len(source) > MAX_TEXT_VALUE
    ):
        source = None
    return {
        "feature_key": row.get("feature_key"),
        "observed_at": row.get("observed_at"),
        "state": row.get("state"),
        "value": _public_value(row.get("value")),
        "unit": row.get("unit"),
        "source": source,
    }


def _summary(
    definition: FeatureDefinition,
    rows: list[dict[str, Any]],
    aggregates: list[str],
) -> dict[str, Any]:
    states = Counter(str(row.get("state")) for row in rows)
    observed = [
        row for row in rows
        if row.get("state") in {"observed", "structural_zero"}
        and row.get("value") is not None
    ]
    observed.sort(key=lambda item: (str(item.get("observed_at")), str(item.get("source") or "")))
    numeric = [
        float(row["value"])
        for row in observed
        if isinstance(row.get("value"), (int, float))
        and not isinstance(row.get("value"), bool)
        and math.isfinite(float(row["value"]))
    ]
    result: dict[str, Any] = {
        "feature_key": definition.key,
        "display_name": definition.display_name,
        "pillar": definition.pillar,
        "unit": definition.unit,
        "value_kind": definition.value_kind,
        "state_counts": dict(sorted(states.items())),
        "observed_count": len(observed),
        "first_observed_at": observed[0]["observed_at"] if observed else None,
        "last_observed_at": observed[-1]["observed_at"] if observed else None,
        "latest": _public_observation(observed[-1]) if observed else None,
        "aggregates": {},
    }
    calculated: dict[str, Any] = {}
    if "count" in aggregates:
        calculated["count"] = len(observed)
    if numeric and len(numeric) == len(observed):
        if "min" in aggregates:
            calculated["min"] = round(min(numeric), 8)
        if "max" in aggregates:
            calculated["max"] = round(max(numeric), 8)
        if "mean" in aggregates:
            calculated["mean"] = round(statistics.fmean(numeric), 8)
        if "median" in aggregates:
            calculated["median"] = round(statistics.median(numeric), 8)
        if "sum" in aggregates:
            calculated["sum"] = round(math.fsum(numeric), 8)
        if "stddev" in aggregates:
            calculated["stddev"] = (
                round(statistics.pstdev(numeric), 8) if len(numeric) > 1 else 0.0
            )
    else:
        counts = Counter(_canonical(_public_value(row["value"])) for row in observed)
        result["value_counts"] = [
            {"value": json.loads(encoded), "count": count}
            for encoded, count in sorted(
                counts.items(), key=lambda item: (-item[1], item[0])
            )[:20]
        ]
    result["aggregates"] = calculated
    return result


def _split_rows(
    rows: list[dict[str, Any]], start: date, end: date,
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        observed_at = row.get("observed_at")
        if not isinstance(observed_at, str):
            continue
        try:
            day = date.fromisoformat(observed_at[:10])
        except ValueError:
            continue
        if start <= day <= end:
            result.append(row)
    return result


def _health_query(
    arguments: object,
    definitions: tuple[FeatureDefinition, ...],
    connection: Any,
    context: Any,
    engine_id: str,
    *,
    binding: SurfaceBinding,
    include_refs: bool = True,
) -> dict[str, Any]:
    normalized, requested = _normalize_query(arguments)
    by_key = {item.key: item for item in definitions}
    unknown = sorted(set(normalized["feature_keys"]) - set(by_key))
    if unknown:
        return _result_payload(
            binding=binding,
            operation="health_query",
            status="unsupported",
            range_value=normalized["range"],
            registry_id=_registry_id(definitions),
            engine_id=engine_id,
            coverage={"requested_features": len(normalized["feature_keys"]), "returned_features": 0},
            facts=[],
            findings=[],
            limitations=[{"code": "unregistered_feature_keys", "feature_keys": unknown}],
            normalized_arguments=normalized,
            include_refs=include_refs,
        )
    selected = [by_key[key] for key in normalized["feature_keys"]]
    frame = build_feature_frame(
        connection, selected, requested, context, include_provenance=False,
    )
    rows = [_public_observation(row) for row in frame["observations"]]
    rows_by_key = {
        key: [row for row in rows if row["feature_key"] == key]
        for key in normalized["feature_keys"]
    }
    facts: list[dict[str, Any]]
    limitations: list[dict[str, Any]] = []
    if normalized["view"] == "series":
        if len(rows) > MAX_SERIES_ROWS:
            return _result_payload(
                binding=binding,
                operation="health_query",
                status="refused",
                range_value=normalized["range"],
                registry_id=_registry_id(definitions),
                engine_id=engine_id,
                coverage={
                    "requested_features": len(selected),
                    "observation_rows": len(rows),
                    "maximum_rows": MAX_SERIES_ROWS,
                },
                facts=[], findings=[],
                limitations=[{
                    "code": "series_too_large",
                    "message": "Use fewer feature keys or a shorter range.",
                }],
                normalized_arguments=normalized,
                include_refs=include_refs,
            )
        facts = rows
    elif normalized["view"] == "latest":
        facts = []
        for definition in selected:
            observed = [
                row for row in rows_by_key[definition.key]
                if row["state"] in {"observed", "structural_zero"}
                and row["value"] is not None
            ]
            observed.sort(key=lambda item: (str(item["observed_at"]), str(item.get("source") or "")))
            facts.append({
                "feature_key": definition.key,
                "display_name": definition.display_name,
                "unit": definition.unit,
                "latest": observed[-1] if observed else None,
            })
    elif normalized["view"] == "summary":
        facts = [
            _summary(item, rows_by_key[item.key], normalized["aggregates"])
            for item in selected
        ]
    else:
        assert requested.start is not None and requested.end is not None
        total_days = (requested.end - requested.start).days + 1
        if total_days < 2:
            raise SurfaceError(
                "validation_error", "period_compare requires at least two days",
            )
        baseline_days = total_days // 2
        baseline_end = requested.start + timedelta(days=baseline_days - 1)
        current_start = baseline_end + timedelta(days=1)
        facts = []
        for item in selected:
            baseline = _summary(
                item,
                _split_rows(rows_by_key[item.key], requested.start, baseline_end),
                normalized["aggregates"],
            )
            current = _summary(
                item,
                _split_rows(rows_by_key[item.key], current_start, requested.end),
                normalized["aggregates"],
            )
            comparison: dict[str, Any] = {
                "feature_key": item.key,
                "baseline_range": {
                    "from": requested.start.isoformat(), "to": baseline_end.isoformat(),
                },
                "current_range": {
                    "from": current_start.isoformat(), "to": requested.end.isoformat(),
                },
                "baseline": baseline,
                "current": current,
                "delta": {},
            }
            for aggregate in normalized["aggregates"]:
                left = baseline["aggregates"].get(aggregate)
                right = current["aggregates"].get(aggregate)
                if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                    comparison["delta"][aggregate] = round(right - left, 8)
            facts.append(comparison)
    observed_count = sum(
        1 for row in rows
        if row["state"] in {"observed", "structural_zero"}
        and row["value"] is not None
    )
    if not observed_count:
        limitations.append({
            "code": "insufficient_data",
            "message": "No selected feature has an observed value in the requested range.",
        })
    return _result_payload(
        binding=binding,
        operation="health_query",
        status="ok" if observed_count else "insufficient_data",
        range_value=normalized["range"],
        registry_id=_registry_id(definitions),
        engine_id=engine_id,
        coverage={
            "requested_features": len(selected),
            "observation_rows": len(rows),
            "observed_values": observed_count,
            "feature_states": {
                key: frame["feature_states"].get(key) for key in normalized["feature_keys"]
            },
            "observation_states": frame["meta"]["observation_states"],
        },
        facts=facts,
        findings=[],
        limitations=limitations,
        normalized_arguments=normalized,
        include_refs=include_refs,
    )


def _normalize_analyze(arguments: object) -> tuple[dict[str, Any], DateRange]:
    raw = _closed_mapping(
        arguments,
        allowed={
            "outcome_key", "exposure_keys", "range", "mode",
            "interactions", "min_n", "top",
        },
        required={
            "outcome_key", "exposure_keys", "range", "mode",
            "interactions", "min_n", "top",
        },
        label="health_analyze arguments",
    )
    outcome_key = _bounded_text(raw["outcome_key"], "outcome_key")
    assert outcome_key is not None
    if FEATURE_KEY_RE.fullmatch(outcome_key) is None:
        raise SurfaceError("validation_error", "outcome_key is invalid")
    exposure_keys = _unique_strings(
        raw["exposure_keys"], "exposure_keys", minimum=1, pattern=FEATURE_KEY_RE,
    )
    if outcome_key in exposure_keys:
        raise SurfaceError("validation_error", "outcome cannot also be an exposure")
    range_value, requested = _date_range(raw["range"])
    mode = _bounded_text(raw["mode"], "mode", maximum=32)
    if mode not in ALLOWED_MODES:
        raise SurfaceError("validation_error", "analysis mode is unsupported")
    interactions = raw["interactions"]
    if type(interactions) is not bool:
        raise SurfaceError("validation_error", "interactions must be true or false")
    min_n = raw["min_n"]
    if type(min_n) is not int or not 30 <= min_n <= 500:
        raise SurfaceError("validation_error", "min_n must be between 30 and 500")
    top = raw["top"]
    if type(top) is not int or not 1 <= top <= 20:
        raise SurfaceError("validation_error", "top must be between 1 and 20")
    normalized = {
        "outcome_key": outcome_key,
        "exposure_keys": exposure_keys,
        "range": range_value,
        "mode": mode,
        "interactions": interactions,
        "min_n": min_n,
        "top": top,
    }
    if _analysis_work_units(
        range_value, exposure_keys, interactions,
    ) > MAX_EVIDENCE_WORK_UNITS:
        raise SurfaceError(
            "work_budget_exceeded",
            "analysis is too large to replay through mandatory evidence; "
            "use fewer exposures or a shorter range",
        )
    return normalized, requested


def _analysis_projection(result: Mapping[str, Any]) -> tuple[object, list[dict[str, Any]], list[dict[str, Any]]]:
    raw_findings = list(result.get("findings", []))
    if not all(isinstance(item, dict) for item in raw_findings):
        raise SurfaceError("engine_error", "analysis findings are invalid")
    # The engine deliberately returns suppressed candidates for auditability.
    # They are not findings Hermes may rank or interpret.  Only candidates
    # that passed the deterministic hypothesis gate cross this public surface.
    findings = [
        item for item in raw_findings
        if isinstance(item.get("quality"), Mapping)
        and item["quality"].get("eligible_for_hypothesis") is True
    ]
    meta = result.get("meta") if isinstance(result.get("meta"), Mapping) else {}
    facts = [{
        "outcome": meta.get("outcome"),
        "requested_range": meta.get("requested_range"),
        "analysis_range": meta.get("analysis_range"),
        "baseline_range": meta.get("baseline_range"),
        "modes": meta.get("modes"),
        "min_n": meta.get("min_n"),
        "input_fingerprint": meta.get("input_fingerprint"),
        "suppression_counts": result.get("suppression_counts", {}),
        "warnings": result.get("warnings", []),
        "returned_candidates": len(raw_findings),
        "eligible_findings": len(findings),
    }]
    coverage = result.get("coverage", {})
    return coverage, facts, findings


def _health_analyze(
    arguments: object,
    definitions: tuple[FeatureDefinition, ...],
    connection: Any,
    context: Any,
    engine_id: str,
    *,
    binding: SurfaceBinding,
    include_refs: bool = True,
) -> dict[str, Any]:
    normalized, requested = _normalize_analyze(arguments)
    by_key = {item.key: item for item in definitions}
    requested_keys = {normalized["outcome_key"], *normalized["exposure_keys"]}
    unknown = sorted(requested_keys - set(by_key))
    if unknown:
        return _result_payload(
            binding=binding,
            operation="health_analyze",
            status="unsupported",
            range_value=normalized["range"],
            registry_id=_registry_id(definitions), engine_id=engine_id,
            coverage={"requested_features": len(requested_keys)},
            facts=[], findings=[],
            limitations=[{"code": "unregistered_feature_keys", "feature_keys": unknown}],
            normalized_arguments=normalized, include_refs=include_refs,
        )
    outcome = by_key[normalized["outcome_key"]]
    invalid_exposures = sorted(
        key for key in normalized["exposure_keys"]
        if "exposure" not in by_key[key].roles
        or not by_key[key].implemented
        or not by_key[key].candidate_enabled
    )
    if "outcome" not in outcome.roles or not outcome.implemented:
        return _result_payload(
            binding=binding,
            operation="health_analyze", status="unsupported",
            range_value=normalized["range"],
            registry_id=_registry_id(definitions), engine_id=engine_id,
            coverage={"requested_features": len(requested_keys)}, facts=[], findings=[],
            limitations=[{"code": "unsupported_outcome", "feature_key": outcome.key}],
            normalized_arguments=normalized, include_refs=include_refs,
        )
    if invalid_exposures:
        return _result_payload(
            binding=binding,
            operation="health_analyze", status="unsupported",
            range_value=normalized["range"],
            registry_id=_registry_id(definitions), engine_id=engine_id,
            coverage={"requested_features": len(requested_keys)}, facts=[], findings=[],
            limitations=[{
                "code": "unsupported_exposure_keys", "feature_keys": invalid_exposures,
            }],
            normalized_arguments=normalized, include_refs=include_refs,
        )
    selected = [outcome] + [by_key[key] for key in normalized["exposure_keys"]]
    try:
        result = exact_cache.cached_compute(
            binding.database, {"operation": "health_analyze", "arguments": normalized,
                         "engine_id": engine_id},
            lambda: associations.analyze_outcome(
                connection, selected, requested, context,
                outcome_key=normalized["outcome_key"], mode=normalized["mode"],
                min_n=normalized["min_n"],
                interactions="pairwise" if normalized["interactions"] else "none",
                top=normalized["top"],
            ), connection=connection, context=context, definitions=selected,
        )
    except exact_cache.AnalysisBusy as exc:
        raise SurfaceError("analysis_busy", str(exc)) from exc
    except associations.AssociationError as exc:
        if exc.validation:
            raise SurfaceError("validation_error", str(exc)) from exc
        raise
    coverage, facts, findings = _analysis_projection(result)
    limitations: list[dict[str, Any]] = []
    if not findings:
        limitations.append({
            "code": "insufficient_data",
            "message": "No eligible deterministic finding passed the analysis gates.",
        })
    finding_ids = [
        item["finding_id"] for item in findings
        if isinstance(item.get("finding_id"), str)
        and SHA256_RE.fullmatch(item["finding_id"])
    ]
    return _result_payload(
        binding=binding,
        operation="health_analyze",
        status="ok" if findings else "insufficient_data",
        range_value=normalized["range"],
        registry_id=_registry_id(definitions), engine_id=engine_id,
        coverage=coverage, facts=facts, findings=findings,
        limitations=limitations,
        normalized_arguments=normalized,
        evidence_ref_findings=finding_ids,
        include_refs=include_refs,
    )


def _normalize_evidence_ref(value: object) -> dict[str, Any]:
    raw = _closed_mapping(
        value,
        allowed={"contract", "operation", "arguments", "result_id", "finding_id"},
        required={"contract", "operation", "arguments", "result_id"},
        label="evidence reference",
    )
    if raw["contract"] != EVIDENCE_REF_CONTRACT:
        raise SurfaceError("validation_error", "evidence reference contract is invalid")
    if raw["operation"] not in REPLAYABLE_OPERATIONS:
        raise SurfaceError("validation_error", "evidence reference operation is invalid")
    if not isinstance(raw["arguments"], Mapping):
        raise SurfaceError("validation_error", "evidence reference arguments are invalid")
    for key in ("result_id", "finding_id"):
        if key in raw and (
            not isinstance(raw[key], str) or SHA256_RE.fullmatch(raw[key]) is None
        ):
            raise SurfaceError("validation_error", f"evidence reference {key} is invalid")
    normalized = {
        "contract": EVIDENCE_REF_CONTRACT,
        "operation": raw["operation"],
        "arguments": dict(raw["arguments"]),
        "result_id": raw["result_id"],
    }
    if "finding_id" in raw:
        normalized["finding_id"] = raw["finding_id"]
    return normalized


def _analysis_work_units(
    range_value: Mapping[str, Any],
    exposure_keys: Iterable[object],
    interactions: bool,
) -> int:
    if not isinstance(range_value, Mapping):
        raise SurfaceError("validation_error", "evidence reference range is invalid")
    try:
        start = date.fromisoformat(range_value["from"])
        end = date.fromisoformat(range_value["to"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SurfaceError("validation_error", "evidence reference range is invalid") from exc
    periods = max(1, math.ceil(((end - start).days + 1) / 30))
    exposures = list(exposure_keys)
    return 100 + periods * max(1, len(exposures)) * 10 + (
        100 if interactions else 0
    )


def _evidence_work_units(reference: Mapping[str, Any]) -> int:
    operation = reference["operation"]
    arguments = reference["arguments"]
    if operation == "health_catalog":
        return 1
    range_value = arguments.get("range")
    if not isinstance(range_value, Mapping):
        raise SurfaceError("validation_error", "evidence reference range is invalid")
    try:
        start = date.fromisoformat(range_value["from"])
        end = date.fromisoformat(range_value["to"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SurfaceError("validation_error", "evidence reference range is invalid") from exc
    periods = max(1, math.ceil(((end - start).days + 1) / 30))
    if operation == "health_query":
        keys = arguments.get("feature_keys", [])
        return periods * max(1, len(keys))
    return _analysis_work_units(
        range_value,
        arguments.get("exposure_keys", []),
        arguments.get("interactions") is True,
    )


def _normalize_evidence(arguments: object) -> dict[str, Any]:
    raw = _closed_mapping(
        arguments,
        allowed={"evidence_refs", "detail"},
        required={"evidence_refs", "detail"},
        label="health_evidence arguments",
    )
    refs = raw["evidence_refs"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= MAX_EVIDENCE_REFS:
        raise SurfaceError(
            "validation_error", "evidence_refs must contain one to 20 references",
        )
    normalized_refs = [_normalize_evidence_ref(item) for item in refs]
    identities = [_canonical(item) for item in normalized_refs]
    if len(identities) != len(set(identities)):
        raise SurfaceError("validation_error", "evidence references must be unique")
    analysis_refs = sum(
        item["operation"] == "health_analyze" for item in normalized_refs
    )
    if analysis_refs > MAX_EVIDENCE_ANALYSIS_REFS:
        raise SurfaceError(
            "work_budget_exceeded", "evidence permits at most one analysis replay",
        )
    work_units = sum(_evidence_work_units(item) for item in normalized_refs)
    if work_units > MAX_EVIDENCE_WORK_UNITS:
        raise SurfaceError(
            "work_budget_exceeded", "evidence replay exceeds the bounded work budget",
        )
    detail = raw["detail"]
    if detail not in {"summary", "lineage"}:
        raise SurfaceError("validation_error", "evidence detail is unsupported")
    return {
        "evidence_refs": normalized_refs,
        "detail": detail,
        "work_units": work_units,
    }


def _replay_operation(
    operation: str,
    arguments: dict[str, Any],
    definitions: tuple[FeatureDefinition, ...],
    connection: Any,
    context: Any,
    engine_id: str,
    *,
    binding: SurfaceBinding,
) -> dict[str, Any]:
    if operation == "health_catalog":
        return _health_catalog(arguments, definitions, engine_id, binding=binding, include_refs=False)
    if operation == "health_query":
        return _health_query(
            arguments, definitions, connection, context, engine_id, binding=binding, include_refs=False,
        )
    if operation == "health_analyze":
        return _health_analyze(
            arguments, definitions, connection, context, engine_id, binding=binding, include_refs=False,
        )
    raise SurfaceError("validation_error", "evidence operation is unsupported")


def _deterministic_delivery_summary(replayed: Mapping[str, Any]) -> dict[str, Any]:
    """Project small deterministic facts the gateway can render verbatim."""

    operation = replayed["operation"]
    summary: dict[str, Any] = {
        "operation": operation,
        "status": replayed["status"],
        "range": replayed["range"],
        "rows": [],
        "total_rows": 0,
    }
    if operation == "health_catalog":
        rows = [
            {
                "feature_key": item.get("key"),
                "display_name": item.get("display_name"),
                "pillar": item.get("pillar"),
                "unit": item.get("unit"),
                "availability": item.get("availability"),
            }
            for item in replayed["facts"]
            if isinstance(item, Mapping)
        ]
    elif operation == "health_query":
        rows = []
        for item in replayed["facts"]:
            if not isinstance(item, Mapping):
                continue
            row: dict[str, Any] = {
                "feature_key": item.get("feature_key"),
            }
            for key in ("display_name", "unit", "observed_count", "observed_at", "state", "value"):
                if key in item:
                    row[key] = item.get(key)
            latest = item.get("latest")
            if isinstance(latest, Mapping):
                row["latest"] = {
                    key: latest.get(key)
                    for key in ("observed_at", "value", "unit", "state")
                }
            aggregates = item.get("aggregates")
            if isinstance(aggregates, Mapping):
                row["aggregates"] = {
                    key: aggregates[key]
                    for key in sorted(aggregates)
                    if key in ALLOWED_AGGREGATES
                }
            delta = item.get("delta")
            if isinstance(delta, Mapping):
                row["delta"] = {
                    key: delta[key] for key in sorted(delta)
                    if key in ALLOWED_AGGREGATES
                }
            for section_name in ("baseline", "current"):
                section = item.get(section_name)
                if not isinstance(section, Mapping):
                    continue
                compact: dict[str, Any] = {
                    "observed_count": section.get("observed_count"),
                }
                section_aggregates = section.get("aggregates")
                if isinstance(section_aggregates, Mapping):
                    compact["aggregates"] = {
                        key: section_aggregates[key]
                        for key in sorted(section_aggregates)
                        if key in ALLOWED_AGGREGATES
                    }
                section_latest = section.get("latest")
                if isinstance(section_latest, Mapping):
                    compact["latest"] = {
                        key: section_latest.get(key)
                        for key in ("observed_at", "value", "unit", "state")
                    }
                row[section_name] = compact
                if "display_name" not in row and section.get("display_name") is not None:
                    row["display_name"] = section.get("display_name")
                if "unit" not in row and section.get("unit") is not None:
                    row["unit"] = section.get("unit")
            rows.append(row)
    else:
        rows = []
        for finding in replayed["findings"]:
            if not isinstance(finding, Mapping):
                continue
            provenance = finding.get("provenance")
            exposure = finding.get("exposure")
            components = (
                exposure.get("components", []) if isinstance(exposure, Mapping) else []
            )
            rows.append({
                "finding_id": finding.get("finding_id"),
                "outcome": finding.get("outcome"),
                "exposure_components": [
                    {
                        key: component.get(key)
                        for key in (
                            "exposure_key", "display", "lag_days", "window_days",
                            "transform", "unit", "temporal_direction",
                        )
                    }
                    for component in components
                    if isinstance(component, Mapping)
                ],
                "effect": finding.get("effect"),
                "sample": finding.get("sample"),
                "testing": finding.get("testing"),
                "stability": finding.get("stability"),
                "quality": finding.get("quality"),
                "warnings": finding.get("warnings", []),
                "evidence_fingerprint": (
                    provenance.get("evidence_fingerprint")
                    if isinstance(provenance, Mapping) else None
                ),
            })
    summary["total_rows"] = len(rows)
    summary["rows"] = rows[:10] if operation != "health_analyze" else rows[:3]
    summary["shown_rows"] = len(summary["rows"])
    return summary


def _health_evidence(
    arguments: object,
    definitions: tuple[FeatureDefinition, ...],
    connection: Any,
    context: Any,
    engine_id: str,
    *,
    binding: SurfaceBinding,
) -> dict[str, Any]:
    normalized = _normalize_evidence(arguments)
    facts: list[dict[str, Any]] = []
    ranges: list[dict[str, str] | None] = []
    status_counts: Counter[str] = Counter()
    limitations: list[dict[str, Any]] = []
    for reference in normalized["evidence_refs"]:
        replayed = _replay_operation(
            reference["operation"],
            reference["arguments"],
            definitions,
            connection,
            context,
            engine_id,
            binding=binding,
        )
        if replayed["result_id"] != reference["result_id"]:
            raise SurfaceError(
                "stale_evidence", "evidence result identity no longer matches replay",
            )
        finding = None
        if "finding_id" in reference:
            finding = next(
                (
                    item for item in replayed["findings"]
                    if item.get("finding_id") == reference["finding_id"]
                ),
                None,
            )
            if finding is None:
                raise SurfaceError(
                    "stale_evidence", "evidence finding is absent from replay",
                )
        item: dict[str, Any] = {
            "operation": reference["operation"],
            "result_id": reference["result_id"],
            "verified": True,
            "status": replayed["status"],
            "range": replayed["range"],
            "deterministic_summary": _deterministic_delivery_summary(replayed),
        }
        if finding is not None:
            item["finding"] = finding
        elif normalized["detail"] == "lineage":
            item["coverage"] = replayed["coverage"]
            item["facts"] = replayed["facts"]
            item["findings"] = replayed["findings"]
            item["limitations"] = replayed["limitations"]
        else:
            item["coverage"] = replayed["coverage"]
            item["limitations"] = replayed["limitations"]
        facts.append(item)
        ranges.append(replayed["range"])
        status_counts[replayed["status"]] += 1
        if replayed["status"] != "ok" or replayed["limitations"]:
            limitations.append({
                "operation": replayed["operation"],
                "result_id": replayed["result_id"],
                "status": replayed["status"],
                "limitations": replayed["limitations"],
            })
    common_range = ranges[0] if all(item == ranges[0] for item in ranges) else None
    bundle_status = "ok"
    for candidate in ("refused", "unsupported", "insufficient_data"):
        if status_counts[candidate]:
            bundle_status = candidate
            break
    return _result_payload(
        binding=binding,
        operation="health_evidence",
        status=bundle_status,
        range_value=common_range,
        registry_id=_registry_id(definitions),
        engine_id=engine_id,
        coverage={
            "requested_references": len(facts),
            "verified_references": len(facts),
            "referenced_statuses": dict(sorted(status_counts.items())),
            "work_units": normalized["work_units"],
        },
        facts=facts,
        findings=[], limitations=limitations,
        normalized_arguments=normalized,
        include_refs=False,
    )


def execute_bound(
    request: object, *, binding: SurfaceBinding, context: Any, engine_id: str | None = None,
) -> dict[str, Any]:
    """Execute one exact read-only request against one coherent DB snapshot."""

    raw = _closed_mapping(
        request,
        allowed={"operation", "arguments"},
        required={"operation", "arguments"},
        label="request",
    )
    operation = raw["operation"]
    if operation not in OPERATIONS:
        raise SurfaceError("validation_error", "operation is unavailable")
    if not isinstance(raw["arguments"], Mapping):
        raise SurfaceError("validation_error", "arguments must be an object")
    _reject_forbidden_keys(raw["arguments"])
    runtime.require_analytical_schema(binding.database)
    connection = runtime.connect_read_only(binding.database)
    try:
        connection.execute("BEGIN")
        definitions = build_registry(connection, context)
        engine_id = _engine_id() if engine_id is None else engine_id
        if operation == "health_catalog":
            result = _health_catalog(raw["arguments"], definitions, engine_id, binding=binding)
        elif operation == "health_query":
            result = _health_query(
                raw["arguments"], definitions, connection, context, engine_id, binding=binding,
            )
        elif operation == "health_analyze":
            result = _health_analyze(
                raw["arguments"], definitions, connection, context, engine_id, binding=binding,
            )
        else:
            result = _health_evidence(
                raw["arguments"], definitions, connection, context, engine_id, binding=binding,
            )
        connection.rollback()
        return result
    finally:
        connection.close()


def execute(request: object) -> dict[str, Any]:
    """Keep the existing fictional Hermes entry point and environment contract."""
    data_class, fixture_id = _identity()
    binding = SurfaceBinding(runtime.DB, SURFACE_CONTRACT, data_class,
                             "fixture_id", fixture_id)
    return execute_bound(request, binding=binding, context=runtime.adapter_context())


def _refused(operation: object, error: SurfaceError) -> dict[str, Any]:
    data_class, fixture_id = _identity()
    safe_operation = operation if operation in OPERATIONS else "health_catalog"
    engine_id = _engine_id()
    preimage = {
        "contract": SURFACE_CONTRACT,
        "operation": safe_operation,
        "data_class": data_class,
        "fixture_id": fixture_id,
        "status": "refused",
        "range": None,
        "registry_id": _sha({"registry": "unavailable"}),
        "engine_id": engine_id,
        "coverage": {},
        "facts": [],
        "findings": [],
        "limitations": [{"code": error.code, "message": str(error)}],
    }
    return {**preimage, "result_id": _sha(preimage), "evidence_refs": []}


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    operation: object = None
    try:
        if not raw or len(raw) > MAX_STDIN_BYTES:
            raise SurfaceError("validation_error", "stdin is empty or too large")
        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SurfaceError("validation_error", "stdin must be valid UTF-8 JSON") from exc
        if isinstance(request, Mapping):
            operation = request.get("operation")
        result = execute(request)
        print(_canonical(result))
        return 0
    except SurfaceError as exc:
        result = _refused(operation, exc)
        print(_canonical(result))
        return 0
    except Exception:
        # Never serialize exception text here: SQLite paths or other local
        # deployment details are not model-facing evidence.
        print(_canonical({
            "contract": SURFACE_CONTRACT,
            "status": "refused",
            "error": "deterministic surface failed closed",
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
