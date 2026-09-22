"""Google Health collector payload decoding and field normalization."""

import json
import math
import re

from .common import stdin_text, valid_date


GH_METRIC_CLAMPS = {
    "resting_hr": (20, 250),          # bpm
    "hrv_ms": (1, 300),               # ms
    "steps": (0, 200_000),
    "active_energy_kcal": (0, 10_000),
    "respiratory_rate": (4, 60),      # breaths/min
    "spo2_pct": (50, 100),
    "sleep_hours": (0, 24),
}


GH_SLEEP_CLAMPS = {
    "time_asleep_hours": (0, 24), "time_in_bed_hours": (0, 24),
    "deep_min": (0, 1440), "rem_min": (0, 1440),
    "light_min": (0, 1440), "awake_min": (0, 1440),
    "awakenings": (0, 100),
}


GH_SLEEP_TEXT = ("bedtime", "wake_time")   # "HH:MM" strings, validated


GH_DAY_KEYS = {"date", "metrics", "sleep", "weight_kg"}


def clamped(fields, clamps, skipped, *, parse_number):
    """Reject unknown fields and skip implausible values without guessing replacements."""
    vals = {}
    for k, v in fields.items():
        if k in GH_SLEEP_TEXT and clamps is GH_SLEEP_CLAMPS:
            if not re.match(r"^\d{2}:\d{2}$", str(v or "")):
                skipped.append(k)
                continue
            vals[k] = v
            continue
        if k not in clamps:
            raise SystemExit(f"unknown field {k!r} — allowed: "
                     f"{', '.join(sorted(clamps) + (list(GH_SLEEP_TEXT) if clamps is GH_SLEEP_CLAMPS else []))}")
        fv = parse_number(v)
        lo, hi = clamps[k]
        if fv is None or not math.isfinite(fv) or not lo <= fv <= hi:
            skipped.append(k)
            continue
        vals[k] = fv
    return vals


def load_json(path, *, stdin):
    """Read a file or explicit input stream and require the collector payload shape."""
    if path == "-":
        try:
            data = json.loads(stdin_text(stdin, "google-health payload"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"stdin is not valid JSON: {e}")
    else:
        with open(path) as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path} is not valid JSON: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("days"), list):
        raise SystemExit('payload must be {"days": [...]}')
    if set(data) - {"days"}:
        raise SystemExit(f"unknown top-level key(s): {', '.join(sorted(set(data) - {'days'}))}")

    return data


def day_date(day):
    """Validate one day object and return its canonical ISO date."""
    if not isinstance(day, dict):
        raise SystemExit("each day must be an object")
    if set(day) - GH_DAY_KEYS:
        raise SystemExit(f"unknown day key(s): {', '.join(sorted(set(day) - GH_DAY_KEYS))}"
                 f" — allowed: {', '.join(sorted(GH_DAY_KEYS))}")
    d = valid_date(day.get("date"), "date")
    return d


def metrics_for_day(day, skipped, *, parse_number):
    """Normalize the legacy HRV spelling before validating daily metrics."""
    raw_metrics = dict(day.get("metrics") or {})
    legacy_hrv = raw_metrics.pop("hrv_sdnn", None)
    if legacy_hrv is not None and "hrv_ms" not in raw_metrics:
        raw_metrics["hrv_ms"] = legacy_hrv
    metrics = clamped(raw_metrics, GH_METRIC_CLAMPS, skipped, parse_number=parse_number)
    return metrics
