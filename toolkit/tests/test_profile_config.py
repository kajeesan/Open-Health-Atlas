"""T43 — owner_profile config foundation: profile-set + phase-set.

Config store for the T44 targets engine (Mifflin-St Jeor + phase-relative
kcal). Toolkit-only, whitelist-and-validate like nutrition-target-set /
athletic-target-set — deliberately NOT in the bridge allowlists.
"""
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTH = ROOT / "health.py"
SCHEMA = (ROOT / "SCHEMA.sql").read_text()


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "health.db"
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return p


def run(db, *args, expect_ok=True):
    r = subprocess.run([sys.executable, str(HEALTH), *args],
                       env={**os.environ, "HEALTH_DB": str(db)},
                       capture_output=True, text=True)
    if expect_ok:
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout) if r.stdout.strip() else {}
    assert r.returncode != 0
    return r.stderr


def profile_rows(db):
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    rows = {r["key"]: (r["value"], r["updated_at"])
            for r in con.execute("SELECT key, value, updated_at FROM owner_profile")}
    con.close()
    return rows


@pytest.fixture()
def today():
    # Collection can precede execution by enough time to cross midnight.
    return datetime.now(ZoneInfo(os.environ["HERMES_TIMEZONE"])).date().isoformat()


# ── profile-set: valid keys ─────────────────────────────────────────────────

def test_profile_set_height_cm(db, today):
    r = run(db, "profile-set", "height_cm", "165")
    assert r["ok"] and r["key"] == "height_cm" and r["value"] == "165"
    rows = profile_rows(db)
    assert rows["height_cm"] == ("165", today)


def test_profile_set_sex(db, today):
    r = run(db, "profile-set", "sex", "female")
    assert r["ok"] and r["key"] == "sex" and r["value"] == "female"
    assert profile_rows(db)["sex"] == ("female", today)


def test_profile_set_dob(db, today):
    r = run(db, "profile-set", "dob", "2001-01-15")
    assert r["ok"] and r["value"] == "2001-01-15"
    assert profile_rows(db)["dob"] == ("2001-01-15", today)


def test_profile_set_activity_fallback(db, today):
    r = run(db, "profile-set", "activity_fallback", "moderate")
    assert r["ok"] and r["value"] == "moderate"
    assert profile_rows(db)["activity_fallback"] == ("moderate", today)


# ── profile-set: upsert ──────────────────────────────────────────────────────

def test_profile_set_upserts_same_key(db, today):
    run(db, "profile-set", "height_cm", "160")
    run(db, "profile-set", "height_cm", "165")
    rows = profile_rows(db)
    assert rows["height_cm"] == ("165", today)
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM owner_profile WHERE key='height_cm'").fetchone()[0]
    assert n == 1


# ── profile-set: rejections ──────────────────────────────────────────────────

def test_profile_set_rejects_unknown_key(db):
    err = run(db, "profile-set", "shoe_size", "42", expect_ok=False)
    assert "key must be one of" in err


@pytest.mark.parametrize("bad", ["99", "251", "not-a-number"])
def test_profile_set_rejects_bad_height(db, bad):
    err = run(db, "profile-set", "height_cm", bad, expect_ok=False)
    assert "height_cm" in err


def test_profile_set_rejects_bad_sex(db):
    err = run(db, "profile-set", "sex", "other-string", expect_ok=False)
    assert "sex" in err


@pytest.mark.parametrize("bad", ["not-a-date", "2099-01-01", "1899-12-31"])
def test_profile_set_rejects_bad_dob(db, bad):
    err = run(db, "profile-set", "dob", bad, expect_ok=False)
    assert "dob" in err


def test_profile_set_rejects_bad_activity_fallback(db):
    err = run(db, "profile-set", "activity_fallback", "lazy", expect_ok=False)
    assert "activity_fallback" in err


# ── phase-set ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phase", ["cut", "maintain", "bulk"])
def test_phase_set_valid(db, phase, today):
    r = run(db, "phase-set", phase)
    assert r["ok"] and r["phase"] == phase and r["phase_started"] == today
    rows = profile_rows(db)
    assert rows["phase"] == (phase, today)
    assert rows["phase_started"] == (today, today)


def test_phase_set_rejects_unknown_phase(db):
    err = run(db, "phase-set", "shred", expect_ok=False)
    assert "phase must be one of" in err


# ── isolation: nothing else in the DB is touched ────────────────────────────

def test_does_not_touch_unrelated_table(db, today):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO intake(date, water_ml) VALUES(?, ?)", (today, 500))
    con.commit()
    before = con.execute("SELECT COUNT(*) FROM intake").fetchone()[0]
    con.close()
    run(db, "profile-set", "height_cm", "165")
    run(db, "phase-set", "cut")
    con = sqlite3.connect(db)
    after = con.execute("SELECT COUNT(*) FROM intake").fetchone()[0]
    con.close()
    assert before == after == 1
