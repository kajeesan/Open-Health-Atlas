"""Generic lab catalog import, validation, and collector-only boundaries."""
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
SYNTHETIC_CATALOG = ROOT / "tests" / "fixtures" / "lab_catalog.synthetic.md"


def make_db(tmp_path):
    p = tmp_path / "health.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return p


@pytest.fixture()
def db(tmp_path):
    return make_db(tmp_path)


def run(db, *args, expect_ok=True):
    r = subprocess.run(
        [sys.executable, str(HEALTH), *args],
        env={**os.environ, "HEALTH_DB": str(db)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    if expect_ok:
        assert r.returncode == 0, f"{args}: {r.stderr or r.stdout}"
    return r


def jout(r):
    return json.loads(r.stdout)


def rows(db, sql):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


FIXTURE_MD = """# Synthetic catalog

## Catalog

### Hematology

- Hemoglobin · Hemoglobin · g/dL · ref 12.0-17.5 · plaus 3-30 · delta 4.0 · [TEST] · aliases: Hgb
- White blood cells · White blood cells · 10^9/L · ref 4.0-11.0 · plaus 0.5-100 · delta 5.0 · [TEST]

### Lipids

- LDL cholesterol · LDL cholesterol · mmol/L · ref <3.0 · plaus 0.3-15 · delta 1.5 · [TEST]

### Vitamins

- Vitamin D · Vitamin D · nmol/L · ref 50-160 · plaus 5-400 · delta 100f · [TEST]
- Creatinine · Creatinine · µmol/L · ref 60-110 · plaus 10-2000 · delta 50 · [TEST]
"""


def write_md(tmp_path, text=FIXTURE_MD, name="cat.md"):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def test_dry_run_is_default_and_writes_nothing(db, tmp_path):
    o = jout(run(db, "import-lab-catalog", write_md(tmp_path)))
    assert o["dry_run"] is True
    by = {p["canonical"]: p for p in o["proposed"]}
    hgb = by["Hemoglobin"]
    assert hgb["panel"] == "Hematology"
    assert hgb["display"] == "Hemoglobin"
    assert hgb["unit"] == "g/dL"
    assert hgb["ref_low"] == 12.0 and hgb["ref_high"] == 17.5
    assert hgb["plaus_low"] == 3.0 and hgb["plaus_high"] == 30.0
    assert hgb["max_delta"] == 4.0 and hgb["delta_kind"] == "abs"
    assert hgb["aliases"] == ["Hgb"]
    assert hgb["source"] == "synthetic-test-fixture"
    ldl = by["LDL cholesterol"]
    assert ldl["ref_low"] is None and ldl["ref_high"] == 3.0
    vitd = by["Vitamin D"]
    assert vitd["max_delta"] == 100.0 and vitd["delta_kind"] == "frac"
    assert by["Creatinine"]["unit"] == "µmol/L"
    assert o["totals"] == {"tests": 5, "panels": 3}
    assert rows(db, "SELECT * FROM lab_catalog") == []


def test_seed_writes_and_is_idempotent(db, tmp_path):
    f = write_md(tmp_path)
    o = jout(run(db, "import-lab-catalog", f, "--seed"))
    assert o["dry_run"] is False and o["seeded_tests"] == 5
    got = {r["canonical"]: r for r in rows(db, "SELECT * FROM lab_catalog")}
    assert set(got) == {
        "Hemoglobin", "White blood cells", "LDL cholesterol", "Vitamin D", "Creatinine"
    }
    assert got["Hemoglobin"]["confidence"] == "cited"
    assert json.loads(got["Hemoglobin"]["aliases"]) == ["Hgb"]
    jout(run(db, "import-lab-catalog", f, "--seed"))
    assert len(rows(db, "SELECT * FROM lab_catalog")) == 5


def test_seed_preserves_user_confirmed_rows_absent_from_md(db, tmp_path):
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO lab_catalog(canonical, display, panel, unit, plaus_low,"
        " plaus_high, source, confidence) VALUES"
        "('Homocysteine','Homocysteine','Other','µmol/L',2,100,"
        "'user-confirmed','user-confirmed')"
    )
    con.commit()
    con.close()
    jout(run(db, "import-lab-catalog", write_md(tmp_path), "--seed"))
    got = {r["canonical"] for r in rows(db, "SELECT canonical FROM lab_catalog")}
    assert "Homocysteine" in got and len(got) == 6


def test_reports_replacements(db, tmp_path):
    f = write_md(tmp_path)
    jout(run(db, "import-lab-catalog", f, "--seed"))
    o = jout(run(db, "import-lab-catalog", f))
    assert all(p["replaces"] for p in o["proposed"])


def test_unknown_citation_key_is_refused(db, tmp_path):
    bad = FIXTURE_MD.replace("[TEST] · aliases: Hgb", "[UNAPPROVED] · aliases: Hgb")
    r = run(db, "import-lab-catalog", write_md(tmp_path, bad), expect_ok=False)
    assert r.returncode != 0 and "UNAPPROVED" in (r.stderr + r.stdout)


def test_missing_citation_is_refused(db, tmp_path):
    bad = FIXTURE_MD.replace(" · [TEST] · aliases: Hgb", "")
    r = run(db, "import-lab-catalog", write_md(tmp_path, bad), expect_ok=False)
    assert r.returncode != 0


def test_plaus_bounds_required_and_ordered(db, tmp_path):
    bad = FIXTURE_MD.replace("plaus 3-30", "plaus 30-3")
    r = run(db, "import-lab-catalog", write_md(tmp_path, bad), expect_ok=False)
    assert r.returncode != 0 and "plaus" in (r.stderr + r.stdout).lower()


def test_malformed_row_too_few_fields_is_refused(db, tmp_path):
    row = "- White blood cells · White blood cells · 10^9/L · ref 4.0-11.0 · plaus 0.5-100 · delta 5.0 · [TEST]"
    bad = FIXTURE_MD.replace(row, "- White blood cells · 10^9/L · [TEST]")
    r = run(db, "import-lab-catalog", write_md(tmp_path, bad), expect_ok=False)
    assert r.returncode != 0


def test_duplicate_canonical_is_refused(db, tmp_path):
    bad = FIXTURE_MD + (
        "\n### Duplicate\n\n- Hemoglobin · Hgb again · g/dL · "
        "ref 12-18 · plaus 3-30 · delta 4.0 · [TEST]\n"
    )
    r = run(db, "import-lab-catalog", write_md(tmp_path, bad), expect_ok=False)
    assert r.returncode != 0 and "Hemoglobin" in (r.stderr + r.stdout)


def test_bad_delta_spec_is_refused(db, tmp_path):
    bad = FIXTURE_MD.replace("delta 5.0", "delta soon")
    r = run(db, "import-lab-catalog", write_md(tmp_path, bad), expect_ok=False)
    assert r.returncode != 0


def test_import_is_not_bridge_allowlisted(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT.parent / "deploy"))
    import types

    broker = types.ModuleType("broker")
    src = (ROOT.parent / "deploy" / "hermes-bridge").read_text()
    exec(compile(src, "hermes-bridge", "exec"), broker.__dict__)
    assert "import-lab-catalog" not in broker.ALLOWED
    assert "import-lab-catalog" not in (ROOT.parent / "app" / "bridge.py").read_text()


def test_bundled_synthetic_catalog_parses(db):
    o = jout(run(db, "import-lab-catalog", str(SYNTHETIC_CATALOG)))
    assert o["totals"]["tests"] >= 15
    cans = {p["canonical"] for p in o["proposed"]}
    for must in ("Vitamin D", "Ferritin", "Vitamin B12", "TSH"):
        assert must in cans, f"{must} missing from catalog"
    for p in o["proposed"]:
        assert p["plaus_low"] < p["plaus_high"]
        assert p["source"] == "synthetic-test-fixture"
    by = {p["canonical"]: p for p in o["proposed"]}
    assert "Hgb" in by["Hemoglobin"]["aliases"]
    assert "WBC" in by["White blood cells"]["aliases"]
