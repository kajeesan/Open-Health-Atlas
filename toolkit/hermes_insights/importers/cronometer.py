"""Cronometer daily-summary decoding, units and column matching."""

import csv
import io
import math
import re
from datetime import date

from ..catalogs import MICRO_SEED, CONFIGURED_MICRO_TARGETS


MICRO_CITATION_STATUS = ("user-configured" if CONFIGURED_MICRO_TARGETS
                         else "not-configured")


MACRO_ALIASES = {"energy_kcal": ("energy",), "protein_g": ("protein",)}


MACRO_UNITS = {"energy_kcal": {"kcal": 1.0}, "protein_g": {"g": 1.0}}


def parse_header(h):
    """Split a Cronometer nutrient header into normalized name and unit."""
    m = re.match(r"^\s*(.*?)\s*\(([^)]*)\)\s*$", h or "")
    if m:
        return m.group(1).strip().lower(), m.group(2).strip().lower()
    return (h or "").strip().lower(), ""


def read_csv(path, *, parse_number):
    """Decode and match columns, returning lazy daily values and their import report."""
    specs = {}          # header-alias -> (key, factor)
    for m in MICRO_SEED:
        for al in m["aliases"]:
            specs[al] = (m["key"], m["units"])
    for key, aliases in MACRO_ALIASES.items():
        for al in aliases:
            specs[al] = (key, MACRO_UNITS[key])
    # BOM-tolerant decode up front (Excel re-saves add one and it would mask
    # the Date column); a non-UTF-8 export fails CLEAN before any row lands.
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise SystemExit("CSV is not UTF-8 — re-export it from Cronometer (or convert) first")
    unmatched = []
    rd = csv.DictReader(io.StringIO(text, newline=""))
    fields = rd.fieldnames or []
    # csv.DictReader keeps only the LAST of exactly-duplicated headers — a
    # hand-edited export could silently lose a column, so surface them.
    dup_cols = sorted({h for h in fields if fields.count(h) > 1})
    cols = {}
    for h in fields:
        name, unit = parse_header(h)
        if name == "date":
            cols[h] = ("__date__", 1.0)
        elif name in ("epa", "dha") and unit in ("mg", "g"):
            # keyed per NAME (last column wins, like every other nutrient) —
            # summing raw columns would double-count an EPA(mg)+EPA(g) pair
            cols[h] = (f"__{name}__", 1.0 if unit == "mg" else 1000.0)
        elif name in specs:
            key, units = specs[name]
            if unit in units:
                cols[h] = (key, units[unit])
            else:
                unmatched.append(h)
        else:
            unmatched.append(h)
    if not any(k == "__date__" for k, _ in cols.values()):
        raise SystemExit("no Date column — is this a Cronometer daily-summary export?")
    summary = {"days": 0, "skipped_rows_bad_date": 0,
               "skipped_values_unparseable": 0, "duplicate_columns": dup_cols,
               "unmatched_columns": sorted(unmatched),
               "citation_status": MICRO_CITATION_STATUS}
    return _daily_rows(rd, cols, summary, parse_number=parse_number), summary


def _daily_rows(rd, cols, summary, *, parse_number):
    for row in rd:
        d = None
        vals, epa_dha = {}, {}
        row_skipped = 0
        for h, (key, factor) in cols.items():
            raw = (row.get(h) or "").strip()
            if key == "__date__":
                try:
                    d = date.fromisoformat(raw[:10]).isoformat()
                except ValueError:
                    d = None
                continue
            if not raw:
                continue
            v = parse_number(raw)
            # nan passes `< 0` and then binds as NULL in sqlite (NOT NULL
            # crash mid-import); inf scores 100% forever — both are garbage
            if v is None or not math.isfinite(v) or v < 0:
                row_skipped += 1
                continue
            if key in ("__epa__", "__dha__"):
                epa_dha[key] = v * factor           # per-name, last wins
            else:
                vals[key] = round(v * factor, 3)
        if epa_dha:
            vals["omega3_epa_dha"] = round(sum(epa_dha.values()), 3)
        summary["skipped_values_unparseable"] += row_skipped
        if d is None:
            # unparseable/missing date: the day is LOST — count it, don't hide it
            if vals or row_skipped:
                summary["skipped_rows_bad_date"] += 1
            continue
        if not vals:
            continue
        summary["days"] += 1
        yield d, vals
