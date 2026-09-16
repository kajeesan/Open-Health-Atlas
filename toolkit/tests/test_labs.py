"""Labs §2/§3: lab-capture (raw preservation), lab-ingest (parse + validate +
tiered write), labs (read). Offline; all three writers are collector-only —
only `labs` (read) is bridge-exposed.

Determinism rules under test:
- Raw photo/PDF preserved verbatim in raw/labs/, refuse-on-exists (immutable).
- Generic parse: dot decimals, µmol/L, 10^9/L, open/closed reference intervals.
- Pre-validation is structural + physically-possible-plausibility + delta;
  unit-match ALONE never auto-passes (plausibility + delta always run).
- Tiers: known+matching+plausible+reasonable-delta = AUTO (always shown);
  new/unit-mismatch/implausible/large-delta = BLOCKED (needs --confirm).
- A real OUT-OF-RANGE but plausible value AUTO-writes (flagged red in view) —
  the tier trusts the digits, the range flag is clinical status.
- Nothing deletes a labs row.
"""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()
CATALOG = ROOT / "tests" / "fixtures" / "lab_catalog.synthetic.md"


def make_db(tmp_path):
    p = tmp_path / "health.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return p


@pytest.fixture()
def db(tmp_path):
    """DB seeded with the bundled synthetic catalog."""
    p = make_db(tmp_path)
    run(p, "import-lab-catalog", str(CATALOG), "--seed")
    return p


def run(db, *args, expect_ok=True, stdin=None):
    r = subprocess.run([sys.executable, str(HEALTH), *args],
                       env={**os.environ, "HEALTH_DB": str(db)},
                       input=stdin, capture_output=True, text=True, timeout=30)
    if expect_ok:
        assert r.returncode == 0, f"{args}: {r.stderr or r.stdout}"
    return r


def run_bytes(db, *args, stdin=b"", expect_ok=True):
    r = subprocess.run([sys.executable, str(HEALTH), *args],
                       env={**os.environ, "HEALTH_DB": str(db)},
                       input=stdin, capture_output=True, timeout=30)
    if expect_ok:
        assert r.returncode == 0, f"{args}: {r.stderr or r.stdout}"
    return r


def jout(r):
    return json.loads(r.stdout if isinstance(r.stdout, str) else r.stdout.decode())


def rows(db, sql):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


# ── lab-capture ─────────────────────────────────────────────────────────────

def test_capture_preserves_bytes_verbatim(db, tmp_path):
    blob = b"\x89PNG\r\n\x1a\n binary photo bytes \xff\xd8"
    o = jout(run_bytes(db, "lab-capture", "sample-report.png", stdin=blob))
    assert o["ok"] is True and o["file"] == "raw/labs/sample-report.png"
    saved = (tmp_path / "raw" / "labs" / "sample-report.png").read_bytes()
    assert saved == blob and o["bytes"] == len(blob)


def test_capture_refuses_overwrite(db, tmp_path):
    run_bytes(db, "lab-capture", "dup.pdf", stdin=b"first")
    r = run_bytes(db, "lab-capture", "dup.pdf", stdin=b"second", expect_ok=False)
    assert r.returncode != 0
    assert (tmp_path / "raw" / "labs" / "dup.pdf").read_bytes() == b"first"


def test_capture_rejects_path_escape(db):
    r = run_bytes(db, "lab-capture", "../secret.pdf", stdin=b"x", expect_ok=False)
    assert r.returncode != 0


def test_capture_rejects_empty(db):
    r = run_bytes(db, "lab-capture", "empty.pdf", stdin=b"", expect_ok=False)
    assert r.returncode != 0


# ── lab-ingest: parse + validate ────────────────────────────────────────────

REPORT = """Synthetic laboratory report   Date: 2026-03-14
Test                    Result    Unit      Reference          Flag
Hemoglobin              14.2      g/dL      12.0 - 17.5       Normal
Potassium               4.2       mmol/L    3.5 - 5.2         Normal
Sodium                  140       mmol/L    135 - 145         Normal
Creatinine              78        µmol/L    60 - 110          Normal
LDL cholesterol         3.1       mmol/L    < 3.0             High
"""


def test_ingest_dry_run_parses_generic_report_and_auto_accepts(db):
    o = jout(run(db, "lab-ingest", "--date", "2026-03-14", stdin=REPORT))
    assert o["dry_run"] is True and o["date"] == "2026-03-14"
    by = {r["canonical"]: r for r in o["rows"]}
    # canonical + panel resolved from the synthetic catalog
    hgb = by["Hemoglobin"]
    assert hgb["value"] == 14.2 and hgb["unit"] == "g/dL"
    assert hgb["panel"] == "Hematology" and hgb["tier"] == "auto"
    assert hgb["in_range"] is True
    # the report's OWN interval is parsed and wins for the stored row
    assert hgb["reference_low"] == 12.0 and hgb["reference_high"] == 17.5
    # µ unit + integer value
    assert by["Creatinine"]["value"] == 78.0 and by["Creatinine"]["unit"] == "µmol/L"
    # out-of-range but PLAUSIBLE → still auto (trust the digits), flagged not-in-range
    ldl = by["LDL cholesterol"]
    assert ldl["tier"] == "auto" and ldl["in_range"] is False
    assert ldl["reference_high"] == 3.0 and ldl["reference_low"] is None
    assert all(r["tier"] == "auto" for r in o["rows"])
    assert o["counts"]["auto"] == 5 and o["counts"]["blocked"] == 0
    # nothing written on a dry run
    assert rows(db, "SELECT * FROM labs") == []


def test_ingest_blocks_unknown_test(db):
    txt = REPORT + "Homocysteine           12.0      µmol/L    5 - 15            Normal\n"
    o = jout(run(db, "lab-ingest", "--date", "2026-03-14", stdin=txt))
    hc = next(r for r in o["rows"] if r["canonical"] == "Homocysteine" or
              r["raw_name"] == "Homocysteine")
    assert hc["tier"] == "blocked" and "new_test" in hc["reasons"]


def test_ingest_blocks_unit_mismatch(db):
    """Unit ≠ catalog → BLOCK; never auto-convert (could be a different assay)."""
    txt = "Hemoglobin              142       g/L       120 - 175         Normal\n"
    o = jout(run(db, "lab-ingest", "--date", "2026-03-14", stdin=txt))
    r = o["rows"][0]
    assert r["tier"] == "blocked" and "unit_mismatch" in r["reasons"]


def test_ingest_blocks_implausible_value(db):
    """An impossible value blocks even when its unit matches."""
    txt = "Hemoglobin              89        g/dL      12.0 - 17.5       Normal\n"
    o = jout(run(db, "lab-ingest", "--date", "2026-03-14", stdin=txt))
    r = o["rows"][0]
    assert r["tier"] == "blocked" and "implausible_value" in r["reasons"]


def test_ingest_blocks_large_delta_even_when_plausible(db):
    """Creatinine 78 then 320: both physically possible, but a large jump trips
    the per-test delta gate → BLOCK for a look."""
    run(db, "lab-ingest", "--date", "2026-01-01",
        "--commit", stdin="Creatinine              78        µmol/L    60 - 110          Normal\n")
    o = jout(run(db, "lab-ingest", "--date", "2026-03-14",
                 stdin="Creatinine              320       µmol/L    60 - 110          Normal\n"))
    r = o["rows"][0]
    assert r["tier"] == "blocked" and "large_delta" in r["reasons"]
    assert r["last_value"] == 78.0


def test_ingest_first_ever_result_skips_delta(db):
    """No prior result → delta check can't run; a known+plausible value still
    auto-accepts on structural + plausibility alone."""
    o = jout(run(db, "lab-ingest", "--date", "2026-03-14",
                 stdin="Ferritin                250       µg/L      30 - 400          Normal\n"))
    r = o["rows"][0]
    assert r["tier"] == "auto" and r["checks"]["delta"] == "no_prior"


# ── lab-ingest: commit + catalog growth ─────────────────────────────────────

def test_commit_writes_only_auto_rows(db):
    txt = REPORT + "Homocysteine           12.0      µmol/L    5 - 15            Normal\n"
    o = jout(run(db, "lab-ingest", "--date", "2026-03-14", "--commit", stdin=txt))
    assert o["dry_run"] is False
    got = {r["test_name"]: r for r in rows(db, "SELECT * FROM labs")}
    assert set(got) == {"Hemoglobin", "Potassium", "Sodium",
                        "Creatinine", "LDL cholesterol"}
    assert o["committed"] == 5 and o["skipped_blocked"] == 1
    # the blocked new test was NOT written and NOT added to the catalog
    assert rows(db, "SELECT * FROM lab_catalog WHERE canonical='Homocysteine'") == []
    # stored row keeps the report interval + raw name/value in notes
    ldl = got["LDL cholesterol"]
    assert ldl["reference_high"] == 3.0 and ldl["flag"] == "High"
    assert ldl["source"] == "labs-ocr"


def test_confirm_writes_blocked_row_and_grows_catalog(db):
    txt = "Homocysteine           12.0      µmol/L    5 - 15            Normal\n"
    o = jout(run(db, "lab-ingest", "--date", "2026-03-14", "--commit",
                 "--confirm", "Homocysteine", stdin=txt))
    assert o["committed"] == 1 and o["confirmed"] == ["Homocysteine"]
    assert rows(db, "SELECT test_name FROM labs")[0]["test_name"] == "Homocysteine"
    cat = rows(db, "SELECT * FROM lab_catalog WHERE canonical='Homocysteine'")
    assert len(cat) == 1
    # catalog row learned unit + reference interval from the report itself
    assert cat[0]["unit"] == "µmol/L" and cat[0]["ref_low"] == 5.0
    assert cat[0]["ref_high"] == 15.0 and cat[0]["confidence"] == "user-confirmed"
    # and now the SAME test flows automatically next time
    o2 = jout(run(db, "lab-ingest", "--date", "2026-04-01", stdin=txt))
    assert o2["rows"][0]["tier"] == "auto"


def test_confirm_ignored_for_still_missing_test(db):
    """--confirm names a test not in the report: it's a no-op, not a crash."""
    o = jout(run(db, "lab-ingest", "--date", "2026-03-14", "--commit",
                 "--confirm", "Example Unlisted Test", stdin=REPORT))
    assert o["committed"] == 5


def test_ingest_is_not_bridge_allowlisted(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT.parent / "deploy"))
    import types
    broker = types.ModuleType("broker")
    src = (ROOT.parent / "deploy" / "hermes-bridge").read_text()
    exec(compile(src, "hermes-bridge", "exec"), broker.__dict__)
    for cmd in ("lab-ingest", "lab-capture"):
        assert cmd not in broker.ALLOWED
        assert cmd not in (ROOT.parent / "app" / "bridge.py").read_text()


# ── labs (read) ─────────────────────────────────────────────────────────────

def test_labs_read_empty_is_insufficient_data(db):
    o = jout(run(db, "labs"))
    assert o["insufficient_data"] is True


def test_labs_read_groups_by_panel_newest_first_with_flags(db):
    run(db, "lab-ingest", "--date", "2026-01-01", "--commit", stdin=REPORT)
    run(db, "lab-ingest", "--date", "2026-03-14", "--commit",
        stdin="Hemoglobin              14.8      g/dL      12.0 - 17.5       Normal\n")
    o = jout(run(db, "labs"))
    assert o.get("insufficient_data") is not True
    panels = {p["panel"]: p for p in o["panels"]}
    assert "Hematology" in panels and "Lipids" in panels
    hgb = next(t for t in panels["Hematology"]["tests"] if t["canonical"] == "Hemoglobin")
    # latest result wins in the table
    assert hgb["value"] == 14.8 and hgb["date"] == "2026-03-14"
    assert hgb["in_range"] is True
    ldl = next(t for t in panels["Lipids"]["tests"] if t["canonical"] == "LDL cholesterol")
    assert ldl["in_range"] is False        # 3.1 > report ref high 3.0


def test_labs_read_single_test_trend(db):
    run(db, "lab-ingest", "--date", "2026-01-01", "--commit",
        stdin="Creatinine              78        µmol/L    60 - 110          Normal\n")
    run(db, "lab-ingest", "--date", "2026-03-14", "--commit",
        stdin="Creatinine              84        µmol/L    60 - 110          Normal\n")
    o = jout(run(db, "labs", "--test", "Creatinine"))
    assert o["canonical"] == "Creatinine"
    series = o["series"]
    assert [s["value"] for s in series] == [78.0, 84.0]
    assert [s["date"] for s in series] == ["2026-01-01", "2026-03-14"]


def test_labs_read_marks_stale_latest_as_historical(db):
    """Current status = the latest reading only; if that latest reading is
    older than ~12 months it's flagged historical, so it isn't read as current
    (and, per the rule, current suggestions ignore it). Older readings are
    never blended into the current value."""
    from datetime import date, timedelta
    old = (date.today() - timedelta(days=800)).isoformat()
    recent = (date.today() - timedelta(days=20)).isoformat()
    run(db, "lab-ingest", "--date", old, "--commit",
        stdin="Creatinine              88        µmol/L    60 - 110          Normal\n")
    run(db, "lab-ingest", "--date", recent, "--commit",
        stdin="Hemoglobin              14.5      g/dL      12.0 - 17.5       Normal\n")
    o = jout(run(db, "labs"))
    assert o["stale_after_days"] == 365
    flat = {t["canonical"]: t for p in o["panels"] for t in p["tests"]}
    assert flat["Creatinine"]["historical"] is True
    assert flat["Creatinine"]["age_days"] >= 700
    assert flat["Hemoglobin"]["historical"] is False


def test_labs_trend_meta_flags_stale_latest(db):
    from datetime import date, timedelta
    d1 = (date.today() - timedelta(days=900)).isoformat()
    d2 = (date.today() - timedelta(days=800)).isoformat()
    run(db, "lab-ingest", "--date", d1, "--commit",
        stdin="Creatinine              80        µmol/L    60 - 110          Normal\n")
    run(db, "lab-ingest", "--date", d2, "--commit",
        stdin="Creatinine              84        µmol/L    60 - 110          Normal\n")
    o = jout(run(db, "labs", "--test", "Creatinine"))
    # both points are old → the CURRENT status (latest) is historical
    assert o["historical"] is True and o["latest_date"] == d2


def test_labs_report_interval_beats_differing_catalog(db):
    """Audit gap LM4: when a report prints its OWN interval that DIFFERS from the
    catalog, that stored interval must win at READ time. Ingest Potassium 5.3 with a
    printed 3.5-5.4 (the catalog is 3.5-5.2). 5.3 is OUT under the catalog but IN
    under the report — the read must show reference_high 5.4 and in_range True,
    proving the row's stored interval beat the (different) catalog, not just that
    the two happened to agree."""
    run(db, "lab-ingest", "--date", "2026-07-01", "--commit",
        stdin="Potassium               5.3       mmol/L    3.5 - 5.4         Normal\n")
    o = jout(run(db, "labs"))
    k = {t["canonical"]: t for p in o["panels"] for t in p["tests"]}["Potassium"]
    assert k["reference_high"] == 5.4         # report interval, not catalog 5.2
    assert k["in_range"] is True              # 5.3 <= 5.4; catalog 5.2 would be out
    assert rows(db, "SELECT ref_high FROM lab_catalog WHERE canonical='Potassium'") == [{"ref_high": 5.2}]


def test_labs_staleness_boundary_is_strictly_over_365(db):
    """Audit gap LM4: pin the staleness threshold at the boundary (impl is
    `age > LABS_STALE_DAYS`, 365). A latest reading exactly 365 d old is NOT
    historical; 366 d old IS. Dates are computed in Europe/Paris to match
    health.py's _now().date(), so a UTC runner near midnight can't skew the age."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    local_today = datetime.now(ZoneInfo("Europe/Paris")).date()
    d365 = (local_today - timedelta(days=365)).isoformat()   # boundary: still current
    d366 = (local_today - timedelta(days=366)).isoformat()   # one past: historical
    run(db, "lab-ingest", "--date", d365, "--commit",
        stdin="Hemoglobin              14.8      g/dL      12.0 - 17.5       Normal\n")
    run(db, "lab-ingest", "--date", d366, "--commit",
        stdin="Creatinine              90        µmol/L    60 - 110          Normal\n")
    flat = {t["canonical"]: t for p in jout(run(db, "labs"))["panels"] for t in p["tests"]}
    assert flat["Hemoglobin"]["age_days"] == 365 and flat["Hemoglobin"]["historical"] is False
    assert flat["Creatinine"]["age_days"] == 366 and flat["Creatinine"]["historical"] is True


def test_labs_read_single_test_one_point_is_insufficient_trend(db):
    run(db, "lab-ingest", "--date", "2026-03-14", "--commit",
        stdin="Magnesium               0.85      mmol/L    0.70 - 0.95       Normal\n")
    o = jout(run(db, "labs", "--test", "Magnesium"))
    assert o["insufficient_data"] is True and o["n"] == 1
