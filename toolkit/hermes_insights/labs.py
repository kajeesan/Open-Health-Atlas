"""Laboratory parsing, validation and read-model calculations."""
from datetime import date
import json
import re

from . import calculations, migrations, runtime
from .catalogs import _SUP
from .importers.lab_catalog import LAB_CATALOG_NOTE

def _ensure_lab_tables(c):
    migrations.require_table(c, "lab_catalog")

def _norm_lab_unit(u):
    return calculations._norm_lab_unit(u, superscripts=_SUP)

def _lab_in_range(value, low, high):
    return calculations._lab_in_range(value, low, high)

def _today(clock):
    return runtime.today(clock=clock)

def _days_ago(n, clock):
    return runtime.days_ago(n, clock=clock)

def _lab_report_ref(s):
    """A report's own reference cell → (low, high).

    Dot or comma decimals and open-ended bounds are accepted generically; the
    report's interval wins over the catalog.
    """
    s = (s or "").strip().replace(",", ".")
    if not s:
        return (None, None)
    if s.startswith("<"):
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return (None, float(m.group()) if m else None)
    if s.startswith(">"):
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return (float(m.group()) if m else None, None)
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)", s)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    return (None, None)
def _parse_lab_report(text):
    """Flat OCR/pdftotext text → candidate rows. Tabular reports are
    column-aligned, so we split each line on runs of 2+
    spaces: [name, value, unit, reference?, flag?]. A line whose 2nd column is
    not a number is a header/blank and is skipped. Messy single-spaced OCR that
    yields too few columns is surfaced (skipped), not mis-parsed — the owner
    reviews the dry-run and the raw file is preserved."""
    rows, skipped = [], []
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = [c.strip() for c in re.split(r"\s{2,}", line.strip()) if c.strip()]
        if len(cols) < 3 or not re.fullmatch(r"-?\d+(?:[.,]\d+)?", cols[1]):
            skipped.append(line.strip())
            continue
        name, val_s, unit = cols[0], cols[1], cols[2]
        ref_low, ref_high = _lab_report_ref(cols[3]) if len(cols) >= 4 else (None, None)
        flag = cols[4] if len(cols) >= 5 else (cols[3] if len(cols) == 4
                                               and ref_low is None and ref_high is None
                                               else None)
        rows.append({"raw_name": name, "value": float(val_s.replace(",", ".")),
                     "unit": unit, "reference_low": ref_low,
                     "reference_high": ref_high, "flag": flag})
    return rows, skipped
def _lab_catalog_index(c):
    """canonical + alias (both lower-cased) → catalog row, for name resolution."""
    idx = {}
    for r in c.execute("SELECT * FROM lab_catalog"):
        d = dict(r)
        idx[d["canonical"].lower()] = d
        for al in json.loads(d["aliases"] or "[]"):
            idx.setdefault(al.lower(), d)
    return idx
def _validate_lab_row(row, cat, last_value):
    """Structural + physically-possible-plausibility + delta. Returns the row
    enriched with canonical/panel/checks/reasons/tier. `cat` is the matched
    catalog dict or None; `last_value` the most recent prior result or None."""
    reasons, checks = [], {}
    if cat is None:
        checks["structural"] = "new_test"
        reasons.append("new_test")
        row.update({"canonical": row["raw_name"], "panel": None,
                    "src_low": row["reference_low"], "src_high": row["reference_high"],
                    "checks": checks, "reasons": reasons, "tier": "blocked",
                    "last_value": last_value})
        return row
    canonical = cat["canonical"]
    unit_ok = _norm_lab_unit(row["unit"]) == _norm_lab_unit(cat["unit"])
    checks["structural"] = "ok" if unit_ok else "unit_mismatch"
    if not unit_ok:
        reasons.append("unit_mismatch")
        checks["plausibility"] = checks["delta"] = "skipped_unit"   # bounds are unit-specific
    else:
        v = row["value"]
        if v < cat["plaus_low"] or v > cat["plaus_high"]:
            checks["plausibility"] = "implausible"
            reasons.append("implausible_value")
        else:
            checks["plausibility"] = "ok"
        if row.get("semi_quant"):
            checks["delta"] = "semi_quant"     # a bound can't be delta-compared
        elif cat["max_delta"] is None:
            checks["delta"] = "no_gate"
        elif last_value is None:
            checks["delta"] = "no_prior"
        else:
            change = abs(v - last_value)
            if cat["delta_kind"] == "frac":
                change = change / abs(last_value) if last_value else float("inf")
            if change > cat["max_delta"]:
                checks["delta"] = "large_delta"
                reasons.append("large_delta")
            else:
                checks["delta"] = "ok"
    # The source's OWN interval is what we STORE (None for the overview matrix,
    # which prints no reference). The EFFECTIVE interval used for the in/out
    # flag is the source's if present, else the catalog's — computed here for
    # display and RE-COMPUTED at read time. That's deliberate: because matrix
    # rows store no reference, correcting a catalog range re-flags every stored
    # row without a re-ingest (the report's own printed interval always wins).
    src_low, src_high = row["reference_low"], row["reference_high"]
    if src_low is None and src_high is None:
        eff_low, eff_high = cat["ref_low"], cat["ref_high"]
    else:
        eff_low, eff_high = src_low, src_high
    row["src_low"], row["src_high"] = src_low, src_high        # stored (report-only)
    row["reference_low"], row["reference_high"] = eff_low, eff_high  # effective: display + in_range
    row.update({"canonical": canonical, "panel": cat["panel"], "checks": checks,
                "reasons": reasons, "tier": "auto" if not reasons else "blocked",
                "last_value": last_value})
    return row
LABS_STALE_DAYS = 365

def lab_ingest(c, a, text, *, report_date):
    """Parse extracted lab text, pre-validate each row against lab_catalog, and
    (with --commit) write the AUTO rows. DRY-RUN IS THE DEFAULT. Tiers:
    known+unit-match+plausible+reasonable-delta → auto (always shown);
    new/unit-mismatch/implausible/large-delta → blocked. A blocked row is
    written only if the owner names it in --confirm (which, for a NEW test,
    also grows the catalog from the report itself). Collector-only, NOT
    bridge-exposed — the sandboxed panel never writes labs."""
    d = report_date
    fmt, (parsed, skipped) = "detail", _parse_lab_report(text)
    _ensure_lab_tables(c)
    idx = _lab_catalog_index(c)
    confirmed = set(a.confirm or [])
    result = []
    for row in parsed:
        row["date"] = row.get("date") or d
        cat = idx.get(row["raw_name"].lower())
        last = None
        if cat is not None:
            pr = c.execute(
                "SELECT value FROM labs WHERE test_name=? AND value IS NOT NULL"
                " ORDER BY date DESC, id DESC LIMIT 1", (cat["canonical"],)).fetchone()
            last = pr["value"] if pr else None
        r = _validate_lab_row(row, cat, last)
        r["in_range"] = _lab_in_range(r["value"], r["reference_low"], r["reference_high"])
        result.append(r)
    counts = {"auto": sum(r["tier"] == "auto" for r in result),
              "blocked": sum(r["tier"] == "blocked" for r in result)}
    base = {"dry_run": not a.commit, "date": d, "format": fmt, "rows": result,
            "counts": counts, "skipped_lines": skipped, "confirm": sorted(confirmed),
            "note": LAB_CATALOG_NOTE}
    if not a.commit:
        return base
    panel_default = a.panel or "Uncategorized"
    committed, confirmed_written, skipped_blocked = 0, [], 0
    for r in result:
        is_confirmed = r["canonical"] in confirmed or r["raw_name"] in confirmed
        if r["tier"] == "blocked" and not is_confirmed:
            skipped_blocked += 1
            continue
        # a confirmed NEW test grows the catalog from the report itself so the
        # same test flows automatically next time (validated once, per §labs)
        if "new_test" in r["reasons"]:
            confirmed_written.append(r["canonical"])
            c.execute(
                "INSERT OR REPLACE INTO lab_catalog(canonical, display, panel,"
                " unit, ref_low, ref_high, plaus_low, plaus_high, max_delta,"
                " delta_kind, aliases, source, confidence) VALUES"
                "(?,?,?,?,?,?,?,?,?, 'abs', '[]', 'user-confirmed', 'user-confirmed')",
                (r["canonical"], r["canonical"], a.panel or panel_default, r["unit"],
                 r["reference_low"], r["reference_high"], 0.0,
                 max(r["value"], r["reference_high"] or 0) * 1000 + 1000, None))
        # keep the raw name + (semi-quant) comparator in notes so an
        # auto-accepted row is always re-checkable against the source
        notes = f"ocr:{r['raw_name']}={r.get('comparator') or ''}{r['value']}{r['unit']}"
        c.execute(
            "INSERT INTO labs(date, panel, test_name, value, unit, reference_low,"
            " reference_high, flag, notes, source) VALUES(?,?,?,?,?,?,?,?,?, 'labs-ocr')",
            (r["date"], r["panel"] or a.panel or panel_default, r["canonical"], r["value"],
             r["unit"], r.get("src_low"), r.get("src_high"), r["flag"], notes))
        committed += 1
    return {**base, "committed": committed, "skipped_blocked": skipped_blocked,
         "confirmed": confirmed_written}
def _lab_age_days(date_str, *, clock):
    try:
        return (date.fromisoformat(_today(clock)) - date.fromisoformat(date_str)).days
    except (TypeError, ValueError):
        return None

def labs(c, a, *, clock):
    """Read-only labs view feed. Default: latest result per test, grouped by
    panel (newest report first is a per-test 'latest wins' — CURRENT status is
    the most recent reading only, never blended with older ones). --test
    <canonical>: that test's series over time for a trend (insufficient_data
    until 2 points). Range flags come from the stored (report) interval, catalog
    as fallback. A latest reading older than LABS_STALE_DAYS is flagged
    `historical`. This is the ONLY bridge-exposed labs command."""
    _ensure_lab_tables(c)
    cat = {r["canonical"]: dict(r) for r in c.execute("SELECT * FROM lab_catalog")}
    days = int(a.days) if a.days else None
    lo = _days_ago(days, clock) if days else None

    def _row_range(r):
        low, high = r["reference_low"], r["reference_high"]
        if low is None and high is None and r["test_name"] in cat:
            low, high = cat[r["test_name"]]["ref_low"], cat[r["test_name"]]["ref_high"]
        return low, high

    if a.test:
        q = ("SELECT * FROM labs WHERE test_name=?"
             + (" AND date>=?" if lo else "") + " ORDER BY date, id")
        params = (a.test, lo) if lo else (a.test,)
        # collapse exact re-ingest duplicates: for one (date, value) the newest
        # row (max id, seen last) wins — a corrected re-ingest supersedes the
        # older row in the view while the deletion law keeps it in the table.
        by_dv = {}
        for r in c.execute(q, params):
            low, high = _row_range(r)
            by_dv[(r["date"], r["value"])] = {
                "date": r["date"], "value": r["value"], "flag": r["flag"],
                "in_range": _lab_in_range(r["value"], low, high)}
        series = sorted(by_dv.values(), key=lambda s: s["date"])
        meta = cat.get(a.test, {})
        latest_date = series[-1]["date"] if series else None
        age = _lab_age_days(latest_date, clock=clock) if latest_date else None
        base = {"canonical": a.test, "display": meta.get("display"),
                "unit": meta.get("unit"), "ref_low": meta.get("ref_low"),
                "ref_high": meta.get("ref_high"), "n": len(series),
                "latest_date": latest_date, "latest_age_days": age,
                "historical": age is not None and age > LABS_STALE_DAYS,
                "stale_after_days": LABS_STALE_DAYS}
        if len(series) < 2:
            return {**base, "insufficient_data": True, "series": series}
        return {**base, "series": series}

    # latest row per test (max date, then id) — the table's headline values
    q = ("SELECT * FROM labs" + (" WHERE date>=?" if lo else "")
         + " ORDER BY test_name, date DESC, id DESC")
    latest = {}
    for r in c.execute(q, (lo,) if lo else ()):
        if r["test_name"] not in latest:
            latest[r["test_name"]] = dict(r)
    if not latest:
        return {"insufficient_data": True,
             "reason": "no lab results ingested yet"}
    panels = {}
    for name, r in latest.items():
        low, high = _row_range(r)
        meta = cat.get(name, {})
        panel = r["panel"] or meta.get("panel") or "Uncategorized"
        age = _lab_age_days(r["date"], clock=clock)
        panels.setdefault(panel, []).append({
            "canonical": name, "display": meta.get("display") or name,
            "value": r["value"], "unit": r["unit"], "date": r["date"],
            "reference_low": low, "reference_high": high, "flag": r["flag"],
            "in_range": _lab_in_range(r["value"], low, high),
            "age_days": age,
            "historical": age is not None and age > LABS_STALE_DAYS})
    if a.panel:
        panels = {k: v for k, v in panels.items() if k == a.panel}
    # out-of-range tests sort to the top of each panel
    out_panels = [{"panel": p, "tests": sorted(
        ts, key=lambda t: (t["in_range"] is not False, t["display"]))}
        for p, ts in sorted(panels.items())]
    return {"panels": out_panels, "n_tests": len(latest),
         "stale_after_days": LABS_STALE_DAYS}
