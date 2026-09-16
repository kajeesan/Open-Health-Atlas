#!/usr/bin/env python3
"""Test-only external Hermes substitute for the fictional Green-day journey.

This executable performs the expected tool-selection script and emits synthetic
interpretation prose. It is acceptance wiring, never genuine model evidence.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys


def _json_run(command: str, *args: str, stdin: str | None = None) -> dict:
    completed = subprocess.run(
        [sys.executable, os.environ["OPENHEALTHATLAS_TEST_TOOL"], command, *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=260,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stdout + completed.stderr)
    value = json.loads(completed.stdout)
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise RuntimeError("OpenHealthAtlas test tool returned a failed result")
    return value


def _context_range(prompt: str) -> tuple[str, str]:
    matched = re.search(
        r"<HERMES_TRUSTED_PANEL_CONTEXT>(.*?)</HERMES_TRUSTED_PANEL_CONTEXT>",
        prompt,
    )
    if matched is None:
        raise RuntimeError("trusted panel context is missing")
    range_value = json.loads(matched.group(1))["range"]
    return range_value["from"], range_value["to"]


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] != "-z":
        raise RuntimeError("unexpected simulated Hermes invocation")
    start, end = _context_range(sys.argv[2])
    frame = _json_run(
        "feature-frame", "--from", start, "--to", end,
        "--family", "subjective", "--include-provenance",
    )
    green = [
        item for item in frame["result"]["observations"]
        if item["feature_key"] == "subjective.day_rating"
        and item["state"] == "observed"
        and item["value"] == 3
    ]
    readiness = _json_run(
        "data-readiness", "--from", start, "--to", end,
        "--outcome", "subjective.day_rating",
    )
    analysis = _json_run(
        "analysis-refresh", "--kind", "manual",
        "--outcome", "subjective.day_rating",
        "--mode", "green-vs-non-green",
        "--from", start, "--to", end, "--anchor", end,
    )
    run = analysis["result"]["runs"][0]
    deterministic = json.loads(run["result_json"])
    findings = [
        finding for finding in deterministic["findings"]
        if finding["quality"]["eligible_for_hypothesis"]
    ]
    registry = _json_run("feature-registry") if findings else None
    definitions = {
        item["key"]: item
        for item in (registry or {}).get("result", {}).get("features", [])
    }

    def classification(finding: dict) -> str:
        component = finding["exposure"]["components"][0]
        definition = definitions[component["exposure_key"]]
        if definition["pillar"] in {"wearable", "recovery", "vitals"}:
            return "physiological_marker"
        if definition["confounder_role"] not in {"none", "outcome"} and (
            definition["actionability"] == "context"
        ):
            return "context_or_confounder"
        if (
            definition["actionability"] == "direct"
            and component["temporal_direction"] == "exposure_precedes_outcome"
        ):
            return "actionable_upstream_contributor"
        if (
            definition["actionability"] == "indirect"
            and component["temporal_direction"] == "exposure_precedes_outcome"
        ):
            return "indirect_contributor"
        if component["temporal_direction"] == "same_day_or_order_unknown":
            return "possible_consequence_or_unknown_order"
        return "unknown"

    classified = [
        {"finding": finding, "classification": classification(finding)}
        for finding in findings
    ]
    actionable = [
        item for item in classified
        if item["classification"] == "actionable_upstream_contributor"
    ]
    markers = [
        item for item in classified
        if item["classification"] == "physiological_marker"
    ]
    selected_findings = []
    for item in [*markers[:1], *actionable[:2], *classified[:2]]:
        finding = item["finding"]
        if finding["finding_id"] not in {
            prior["finding_id"] for prior in selected_findings
        }:
            selected_findings.append(finding)
    replayed = [
        _json_run(
            "finding-evidence",
            "--outcome", "subjective.day_rating",
            "--finding-id", finding["finding_id"],
            "--input-fingerprint", run["input_fingerprint"],
            "--from", start, "--to", end,
        )
        for finding in selected_findings
    ]
    preparation = _json_run(
        "synthesis-prepare", "--batch-id",
        analysis["result"]["batch"]["batch_id"],
    )
    prepared = preparation["result"]
    assessed = prepared["assessment_state"] == "assessed"
    recorded = None

    capture = {
        "range": [start, end],
        "green": green,
        "readiness": readiness["result"],
        "analysis": deterministic,
        "registry": (registry or {}).get("result"),
        "classified": [{
            "finding_id": item["finding"]["finding_id"],
            "exposure_key": item["finding"]["exposure"]["components"][0][
                "exposure_key"
            ],
            "classification": item["classification"],
            "raw_statistical_rank": deterministic["findings"].index(
                item["finding"]
            ) + 1,
        } for item in classified],
        "replayed": replayed,
        "prepared": prepared,
        "recorded": recorded,
    }
    capture_path = Path(os.environ["OPENHEALTHATLAS_TEST_CAPTURE"])
    with capture_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(capture, sort_keys=True) + "\n")

    evidence_id = (
        replayed[0]["result"]["finding"]["finding_id"]
        if replayed else prepared["analysis_batch_id"]
    )
    dates = ", ".join(item["observed_at"] for item in green) or "none"
    outcome_readiness = next(
        item for item in readiness["result"]["features"]
        if item["feature_key"] == "subjective.day_rating"
    )
    coverage = (
        f"aligned_n={outcome_readiness['aligned_n']}, "
        f"eligible_units={outcome_readiness['eligible_units']}, "
        f"missing_rate={outcome_readiness['missing_rate']}"
    )
    limitations = ", ".join(deterministic.get("warnings", [])) or "none"
    if assessed:
        raw_lines = []
        for rank, finding in enumerate(findings[:5], start=1):
            component = finding["exposure"]["components"][0]
            raw_lines.append(
                f"{rank}. `{component['exposure_key']}` "
                f"(lag={component['lag_days']}d, effect="
                f"{finding['effect']['oriented_estimate']}, "
                f"q={finding['testing']['q']}, n="
                f"{finding['sample']['complete_n']}, "
                f"stability={finding['stability']['status']}) "
                f"[`{finding['finding_id']}`]"
            )
        raw_ranking = "\n".join(raw_lines)
        marker = markers[0]["finding"] if markers else None
        marker_id = marker["finding_id"] if marker else evidence_id
        if actionable:
            upstream = actionable[0]["finding"]
            upstream_component = upstream["exposure"]["components"][0]
            did = (
                "1. **Lower prior-day caffeine exposure** is the leading "
                "plausible actionable contributor (moderate confidence). "
                f"It preceded the outcome by {upstream_component['lag_days']} "
                f"day and remained an imperfect observational association "
                f"[`{upstream['finding_id']}`]. Weakening evidence: fixed "
                "counterexamples, multiple testing, and unchecked context mean "
                "this is not a proven cause. The sleep-timing result is a "
                "competing indirect/order-uncertain explanation, not hidden."
            )
        elif frame["fixture_id"] == "green-days-v1":
            did = (
                "No actionable behavior can be ranked. The exact repeating "
                "three-day Green-Yellow-Red fixture structure is the dominant "
                "limitation, so these associations cannot answer what the user did."
            )
        else:
            did = (
                "Lower resting heart rate was associated with Green days, but "
                "this is a physiological marker. The available evidence does "
                "not show what behavior produced it, so I cannot determine what "
                f"the user did [`{marker_id}`]."
            )
        body = (
            "Resting heart rate is the strongest raw physiological marker, not "
            f"an action or established cause [`{marker_id}`]. It may support an "
            "upstream hypothesis when one exists, may merely co-vary with Green "
            "days, or could be a consequence of the same unmeasured state."
        )
        reply = (
            "## Deterministic result\n"
            f"Green dates: {dates}. OpenHealthAtlas returned and replayed "
            f"finding `{evidence_id}`. Coverage: {coverage}. Provenance: every "
            f"listed Green row has source=`manual`. Limitations: {limitations}.\n\n"
            "Raw deterministic statistical ranking (not the actionable ranking):\n"
            f"{raw_ranking}\n\n"
            "## Hermes interpretation\n"
            "### What the user did\n"
            f"{did}\n\n"
            "### What the body showed\n"
            f"{body}\n\n"
            "### What remains unknown\n"
            "Illness, travel, training phase, source changes, and other exposures "
            "remain alternatives where unchecked. A disjoint replication and "
            "more explicit behavior logging could change the ranking. This is "
            "not a diagnosis and does not prove causation."
        )
    else:
        reply = (
            "## Deterministic result\n"
            f"Green dates: {dates}. OpenHealthAtlas returned insufficient data "
            f"for batch `{evidence_id}` and no eligible finding. Coverage: "
            f"{coverage}. Provenance: logged ratings use source=`manual`. "
            f"Limitations: {limitations}.\n\n"
            "## Hermes interpretation\n"
            "### What the user did\n"
            f"Insufficient data for batch `{evidence_id}`: I cannot rank a leading "
            "actionable explanation.\n\n"
            "### What the body showed\n"
            "No eligible physiological marker can be interpreted from this short "
            "window.\n\n"
            "### What remains unknown\n"
            "The short window lacks comparison evidence. Follow-up: log at least "
            "30 days with both Green and non-Green outcomes."
        )
    print(reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
