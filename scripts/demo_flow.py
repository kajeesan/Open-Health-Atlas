#!/usr/bin/env python3
"""Run a complete isolated Hermes flow with fictional data only.

The flow initializes empty databases, exercises a retained importer and
validated CLI writes, runs deterministic intelligence plus its durable ledger,
and reads the UI/API through an authenticated Flask test client. No network
or external service is used.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import random
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TOOLKIT = ROOT / "toolkit"
HEALTH = TOOLKIT / "health.py"
ANCHOR = date(2026, 6, 30)
DAY_COUNT = 45
FIXTURE_ID = "green-days-v1"
ACTIONABLE_FIXTURE_ID = "green-days-actionable-v1"
MARKER_ONLY_FIXTURE_ID = "green-days-marker-only-v1"
FIXTURE_IDS = (
    FIXTURE_ID,
    ACTIONABLE_FIXTURE_ID,
    MARKER_ONLY_FIXTURE_ID,
)
FIXTURE_CONTRACT = "openhealthatlas-fictional-fixture-v1"
EXPECTED_SEED_SHA256 = (
    "sha256:2b77c73323a7f0656b845c0c3cdac21f"
    "e08e47dc1fda4c3882c680e57f8bd9be"
)
EXPECTED_SEED_SHA256_BY_ID = {
    FIXTURE_ID: EXPECTED_SEED_SHA256,
    ACTIONABLE_FIXTURE_ID: (
        "sha256:01e9f7d900fbcc2b0531f3f4d5cf141a"
        "64727916d7b018362d0fabee3f2ca361"
    ),
    MARKER_ONLY_FIXTURE_ID: (
        "sha256:31b85ea572cfff1df0aeb3567c6acae8"
        "79d8423c125a200e62942c9e6c3393c6"
    ),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument(
        "--fixture-id",
        choices=FIXTURE_IDS,
        default=FIXTURE_ID,
        help="named fictional Green-day acceptance fixture",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--seed-only",
        action="store_true",
        help="stop after creating the reproducible Green-day fixture",
    )
    mode.add_argument(
        "--verify-fixture",
        type=Path,
        metavar="MANIFEST",
        help="verify the database against a trusted seed receipt and audit chain",
    )
    parser.add_argument(
        "--audit",
        type=Path,
        help="adapter audit used when verifying an evolved fixture database",
    )
    return parser


def _json_command(argv: list[str], *, environment: dict[str, str], stdin=None):
    result = subprocess.run(
        argv,
        input=stdin,
        env=environment,
        text=True,
        capture_output=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"synthetic command failed ({Path(argv[1]).name if len(argv) > 1 else argv[0]} "
            f"{argv[2] if len(argv) > 2 else ''}): exit {result.returncode}"
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("synthetic command returned non-JSON output") from exc
    if not isinstance(value, dict):
        raise RuntimeError("synthetic command returned the wrong JSON shape")
    return value


def _health_command(environment: dict[str, str], *args: str, stdin=None):
    return _json_command(
        [sys.executable, str(HEALTH), *args],
        environment=environment,
        stdin=stdin,
    )


def _cycle_fixture() -> dict:
    days = []
    ratings = []
    for offset in range(DAY_COUNT):
        current = ANCHOR - timedelta(days=DAY_COUNT - 1 - offset)
        band = offset % 3
        if band == 0:
            rating, sleep, steps, hrv, resting = "green", 8.1, 11200, 54.0, 52
        elif band == 1:
            rating, sleep, steps, hrv, resting = "yellow", 7.0, 7600, 43.0, 58
        else:
            rating, sleep, steps, hrv, resting = "red", 5.8, 3600, 31.0, 66
        days.append({
            "date": current.isoformat(),
            "metrics": {
                "resting_hr": resting,
                "hrv_ms": hrv,
                "steps": steps,
                "active_energy_kcal": 300 + steps / 25,
                "respiratory_rate": 13.0 + band,
                "spo2_pct": 97.0 - band * 0.5,
                "sleep_hours": sleep,
            },
            "sleep": {
                "time_asleep_hours": sleep,
                "time_in_bed_hours": sleep + 0.6,
                "deep_min": 95 - band * 15,
                "rem_min": 105 - band * 10,
                "light_min": 270,
                "awake_min": 25 + band * 15,
                "awakenings": 2 + band,
                "bedtime": "22:45",
                "wake_time": "06:45",
            },
            "weight_kg": 80.0 + offset / 100,
        })
        ratings.append((current.isoformat(), rating))
    return {
        "days": days,
        "ratings": ratings,
        "training_sets": [],
        "caffeine_mg": {},
    }


def _actionable_fixture() -> dict:
    """Imperfect upstream pattern with a marker, competitor, and counterexamples."""

    count = 45
    first = ANCHOR - timedelta(days=count - 1)
    generator = random.Random(20260810)
    recovery_setups = [generator.random() < 0.56 for _ in range(count + 1)]

    days: list[dict] = []
    ratings: list[tuple[str, str]] = []
    training_sets: list[dict] = []
    caffeine_mg: dict[str, float] = {}
    for offset in range(count):
        current = first + timedelta(days=offset)
        day = current.isoformat()
        prior_setup = recovery_setups[offset]
        counterexample = offset in {7, 23, 38}
        green = prior_setup != counterexample
        rating = "green" if green else ("yellow" if offset % 2 else "red")

        marker_counterexample = offset in {11, 31}
        marker_green = green != marker_counterexample
        resting = (50 if marker_green else 62) + (offset % 5)

        next_setup = recovery_setups[offset + 1]
        bedtime = "22:35" if next_setup else (
            "00:20" if offset % 2 else "21:10"
        )
        sleep = 7.2
        days.append({
            "date": day,
            "metrics": {
                "resting_hr": resting,
                "hrv_ms": 45.0,
                "steps": 7600,
                "active_energy_kcal": 520,
                "respiratory_rate": 14.0,
                "spo2_pct": 97.0,
                "sleep_hours": sleep,
            },
            "sleep": {
                "time_asleep_hours": sleep,
                "time_in_bed_hours": sleep + 0.55,
                "deep_min": 85,
                "rem_min": 100,
                "light_min": 265,
                "awake_min": 30,
                "awakenings": 2,
                "bedtime": bedtime,
                "wake_time": "06:45",
            },
            "weight_kg": 80.0 + offset / 200,
        })
        ratings.append((day, rating))

        # A competing direct behavior that co-moves only partially with the
        # recovery setup and deliberately disagrees on fixed counterexample days.
        low_caffeine = next_setup != (offset in {6, 17, 32, 40})
        caffeine_mg[day] = (
            60.0 + (offset % 5) * 12
            if low_caffeine
            else 190.0 + (offset % 5) * 18
        )
    return {
        "days": days,
        "ratings": ratings,
        "training_sets": training_sets,
        "caffeine_mg": caffeine_mg,
    }


def _marker_only_fixture() -> dict:
    """A strong physiological marker with no varying upstream behavior."""

    count = 45
    first = ANCHOR - timedelta(days=count - 1)
    generator = random.Random(20260811)
    days: list[dict] = []
    ratings: list[tuple[str, str]] = []
    for offset in range(count):
        current = first + timedelta(days=offset)
        day = current.isoformat()
        green = generator.random() < 0.45
        rating = "green" if green else ("yellow" if offset % 2 else "red")
        marker_counterexample = offset in {7, 24, 41}
        marker_green = green != marker_counterexample
        resting = (50 if marker_green else 63) + (offset % 5)
        days.append({
            "date": day,
            "metrics": {
                "resting_hr": resting,
                "hrv_ms": 45.0,
                "steps": 7500,
                "active_energy_kcal": 500,
                "respiratory_rate": 14.0,
                "spo2_pct": 97.0,
                "sleep_hours": 7.2,
            },
            "sleep": {
                "time_asleep_hours": 7.2,
                "time_in_bed_hours": 7.75,
                "deep_min": 85,
                "rem_min": 100,
                "light_min": 265,
                "awake_min": 30,
                "awakenings": 2,
                "bedtime": "22:45",
                "wake_time": "06:45",
            },
            "weight_kg": 80.0,
        })
        ratings.append((day, rating))
    return {
        "days": days,
        "ratings": ratings,
        "training_sets": [],
        "caffeine_mg": {},
    }


def _fixture_seed(fixture_id: str) -> dict:
    builders = {
        FIXTURE_ID: _cycle_fixture,
        ACTIONABLE_FIXTURE_ID: _actionable_fixture,
        MARKER_ONLY_FIXTURE_ID: _marker_only_fixture,
    }
    try:
        return builders[fixture_id]()
    except KeyError as exc:
        raise RuntimeError(f"unknown fictional fixture: {fixture_id}") from exc


def _synthetic_days() -> tuple[list[dict], list[tuple[str, str]]]:
    """Backward-compatible access to the original exact-cycle fixture."""

    seed = _cycle_fixture()
    return seed["days"], seed["ratings"]


def _seed_identity_payload(fixture_id: str, seed: dict) -> dict:
    if fixture_id == FIXTURE_ID:
        return {"days": seed["days"], "ratings": seed["ratings"]}
    return seed


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _fixture_receipt(
    data_dir: Path,
    health_db: Path,
    fixture_id: str,
    seed: dict,
) -> dict:
    days = seed["days"]
    ratings = seed["ratings"]
    seed_sha256 = "sha256:" + hashlib.sha256(
        _canonical(_seed_identity_payload(fixture_id, seed)).encode("utf-8")
    ).hexdigest()
    if seed_sha256 != EXPECTED_SEED_SHA256_BY_ID[fixture_id]:
        raise RuntimeError("fictional Green-day seed identity drifted")
    with sqlite3.connect(f"file:{health_db}?mode=ro", uri=True) as connection:
        analysis_runs = connection.execute(
            "SELECT COUNT(*) FROM analysis_runs"
        ).fetchone()[0]
        synthesis_runs = connection.execute(
            "SELECT COUNT(*) FROM synthesis_runs"
        ).fetchone()[0]
    receipt = {
        "contract": FIXTURE_CONTRACT,
        "data_class": "fictional",
        "fixture_id": fixture_id,
        "range_from": ratings[0][0],
        "range_to": ratings[-1][0],
        "day_count": len(days),
        "green_count": sum(rating == "green" for _, rating in ratings),
        "green_dates": [
            day_value for day_value, rating in ratings if rating == "green"
        ],
        "day_rating_source": "manual",
        "seed_sha256": seed_sha256,
        "database_sha256": _file_sha256(health_db),
        "health_db": "health.db",
        "health_vault": "vault",
        "analysis_runs": analysis_runs,
        "synthesis_runs": synthesis_runs,
        "external_services_used": False,
    }
    manifest = data_dir / "fixture-manifest.json"
    manifest.write_text(_canonical(receipt) + "\n", encoding="utf-8")
    os.chmod(manifest, 0o600)
    return receipt


def _verify_fixture(
    data_dir: Path,
    manifest_path: Path,
    audit_path: Path | None,
    fixture_id: str,
) -> dict:
    requested_data_dir = data_dir.expanduser()
    requested_manifest = manifest_path.expanduser()
    try:
        data_dir = requested_data_dir.resolve(strict=True)
        manifest_path = requested_manifest.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("fictional fixture paths are unavailable") from exc
    database = data_dir / "health.db"
    if (
        requested_data_dir.is_symlink()
        or data_dir != requested_data_dir.absolute()
        or requested_manifest.is_symlink()
        or manifest_path != requested_manifest.absolute()
        or not data_dir.is_dir()
        or not database.is_file()
        or database.is_symlink()
        or not manifest_path.is_file()
    ):
        raise RuntimeError("fictional fixture paths are unavailable or symbolic links")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("fictional fixture manifest is unreadable") from exc
    expected_keys = {
        "contract", "data_class", "fixture_id", "range_from", "range_to",
        "day_count", "green_count", "green_dates", "day_rating_source",
        "seed_sha256", "database_sha256", "health_db", "health_vault",
        "analysis_runs", "synthesis_runs", "external_services_used",
    }
    seed = _fixture_seed(fixture_id)
    ratings = seed["ratings"]
    expected_green_dates = [
        day_value for day_value, rating in ratings if rating == "green"
    ]
    if not isinstance(manifest, dict) or set(manifest) != expected_keys or any((
        manifest["contract"] != FIXTURE_CONTRACT,
        manifest["data_class"] != "fictional",
        manifest["fixture_id"] != fixture_id,
        manifest["range_from"] != ratings[0][0],
        manifest["range_to"] != ratings[-1][0],
        type(manifest["day_count"]) is not int or manifest["day_count"] != len(ratings),
        type(manifest["green_count"]) is not int or manifest["green_count"] != len(expected_green_dates),
        manifest["green_dates"] != expected_green_dates,
        manifest["day_rating_source"] != "manual",
        manifest["seed_sha256"] != EXPECTED_SEED_SHA256_BY_ID[fixture_id],
        manifest["health_db"] != "health.db",
        manifest["health_vault"] != "vault",
        type(manifest["analysis_runs"]) is not int or manifest["analysis_runs"] != 0,
        type(manifest["synthesis_runs"]) is not int or manifest["synthesis_runs"] != 0,
        manifest["external_services_used"] is not False,
    )):
        raise RuntimeError("fictional fixture manifest failed its contract")

    current = _file_sha256(database)
    if current != manifest["database_sha256"]:
        requested_audit = audit_path.expanduser() if audit_path is not None else None
        if requested_audit is not None:
            try:
                resolved_audit = requested_audit.resolve(strict=True)
            except OSError:
                resolved_audit = None
        else:
            resolved_audit = None
        if (
            requested_audit is None
            or requested_audit.is_symlink()
            or resolved_audit != requested_audit.absolute()
            or not resolved_audit.is_file()
        ):
            raise RuntimeError(
                "fictional fixture database does not match its seed receipt"
            )
        audit_path = resolved_audit
        rows = []
        try:
            lines = audit_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise RuntimeError("fictional fixture audit is unreadable") from exc
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(row, dict)
                and row.get("contract") == "openhealthatlas-hermes-tool-v1"
                and row.get("phase") == "result"
                and row.get("fixture_id") == fixture_id
            ):
                rows.append(row)
        if (
            not rows
            or rows[0].get("database_before_sha256")
            != manifest["database_sha256"]
        ):
            raise RuntimeError(
                "fictional fixture audit is not anchored to the seed receipt"
            )
        if any(
            row.get("data_class") != "fictional" or row.get("returncode") != 0
            for row in rows
        ):
            raise RuntimeError(
                "fictional fixture audit contains a failed or non-fictional result"
            )
        for previous, following in zip(rows, rows[1:]):
            if previous.get("database_after_sha256") != following.get(
                "database_before_sha256"
            ):
                raise RuntimeError("fictional fixture audit chain is discontinuous")
        if rows[-1].get("database_after_sha256") != current:
            raise RuntimeError(
                "fictional fixture database is not the last audited state"
            )
    return {
        "ok": True,
        "fixture_id": manifest["fixture_id"],
        "seed_sha256": manifest["seed_sha256"],
        "database_sha256": current,
    }


def _exercise_ui_api(health_db: Path, panel_db: Path) -> dict:
    sys.path.insert(0, str(ROOT))
    from app import auth as auth_mod
    from app import create_app

    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(panel_db),
        "HEALTH_DB": str(health_db),
        "PANEL_COOKIE_SECURE": False,
    })
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    page = client.get("/")
    metrics = client.get("/api/dash/metrics?days=all")
    subjective = client.get("/api/dash/subjective?days=all")
    if page.status_code != 200 or metrics.status_code != 200 or subjective.status_code != 200:
        raise RuntimeError("synthetic UI/API verification failed")
    return {
        "dashboard_status": page.status_code,
        "dashboard_title_present": b"Dashboard" in page.data,
        "metrics_status": metrics.status_code,
        "metric_rows": len(metrics.get_json()["rows"]),
        "subjective_rows": len(subjective.get_json()["rows"]),
    }


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    requested_data_dir = args.data_dir.expanduser()
    if args.audit is not None and args.verify_fixture is None:
        parser.error("--audit requires --verify-fixture")
    if args.verify_fixture is not None:
        try:
            result = _verify_fixture(
                requested_data_dir,
                args.verify_fixture,
                args.audit,
                args.fixture_id,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, sort_keys=True))
        return 0
    data_dir = requested_data_dir.resolve()
    health_db = data_dir / "health.db"
    panel_db = data_dir / "panel.db"
    initialized = _json_command(
        [sys.executable, str(ROOT / "scripts/init_hermes.py"), "--data-dir", str(data_dir)],
        environment=os.environ.copy(),
    )
    environment = {
        **os.environ,
        "HERMES_DATA_DIR": str(data_dir),
        "HEALTH_DB": str(health_db),
        "HEALTH_VAULT": str(data_dir / "vault"),
        "HERMES_TIMEZONE": "UTC",
        "HERMES_CODE_VERSION": "0" * 40,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    seed = _fixture_seed(args.fixture_id)
    days = seed["days"]
    ratings = seed["ratings"]
    imported = _health_command(
        environment,
        "import-google-health",
        "-",
        stdin=json.dumps({"days": days}, sort_keys=True),
    )
    for day_value, rating in ratings:
        _health_command(
            environment,
            "day-rating",
            rating,
            "--date",
            day_value,
            "--source",
            "manual",
        )
    if seed["training_sets"] or seed["caffeine_mg"]:
        with sqlite3.connect(health_db) as connection:
            for item in seed["training_sets"]:
                for set_index in range(1, item["set_count"] + 1):
                    connection.execute(
                        """INSERT INTO hevy_sets(
                             date,workout_title,exercise_title,set_index,
                             set_type,weight_kg,reps,rpe,source)
                           VALUES(?, 'Fixture training', 'Fixture lift', ?,
                                  'normal', ?, ?, ?, 'hevy')""",
                        (
                            item["date"],
                            set_index,
                            item["weight_kg"],
                            item["reps"],
                            item["rpe"],
                        ),
                    )
            if seed["training_sets"]:
                connection.execute(
                    """INSERT INTO source_sync_runs(
                         source,started_at,completed_at,status,coverage_from,
                         coverage_to,rows_seen,rows_written,error_code,details_json)
                       VALUES('hevy','2026-04-01T00:00:00+00:00',
                              '2026-07-01T00:00:00+00:00','success',?,?,?, ?,
                              NULL,'{\"warning_codes\":[]}')""",
                    (
                        ratings[0][0],
                        ratings[-1][0],
                        sum(item["set_count"] for item in seed["training_sets"]),
                        sum(item["set_count"] for item in seed["training_sets"]),
                    ),
                )
            for day_value, caffeine in seed["caffeine_mg"].items():
                connection.execute(
                    "UPDATE subjective_daily SET caffeine_mg=? WHERE date=?",
                    (caffeine, day_value),
                )
            connection.commit()
    fixture = _fixture_receipt(
        data_dir, health_db, args.fixture_id, seed,
    )
    if args.seed_only:
        print(json.dumps({"ok": True, **fixture}, sort_keys=True))
        return 0
    first_day = ratings[0][0]
    final_day = ratings[-1][0]
    readiness = _health_command(
        environment,
        "data-readiness",
        "--from",
        first_day,
        "--to",
        final_day,
        "--outcome",
        "subjective.day_rating",
    )
    associations = _health_command(
        environment,
        "outcome-associations",
        "--outcome",
        "subjective.day_rating",
        "--from",
        first_day,
        "--to",
        final_day,
        "--mode",
        "ordinal",
        "--min-n",
        "30",
        "--top",
        "10",
    )
    ledger = _health_command(
        environment,
        "analysis-refresh",
        "--kind",
        "manual",
        "--outcome",
        "subjective.day_rating",
        "--from",
        first_day,
        "--to",
        final_day,
        "--anchor",
        final_day,
    )
    report = {
        "ok": True,
        "fictional": True,
        "external_services_used": False,
        "fixture": fixture,
        "initialization": {
            "schema_version": initialized["schema_version"],
            "empty_before_import": initialized["empty"],
        },
        "synthetic_import": {
            "format": "google-health-v4",
            "days": imported["days"],
            "validated_day_ratings": len(ratings),
        },
        "deterministic_intelligence": {
            "readiness_states": readiness.get("summary", {}),
            "finding_count": len(associations.get("findings", [])),
            "input_fingerprint": associations.get("meta", {}).get("input_fingerprint"),
            "ledger_status": ledger["batch"]["status"],
            "ledger_runs": len(ledger["runs"]),
        },
        "ui_api": _exercise_ui_api(health_db, panel_db),
    }
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
