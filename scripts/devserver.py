"""LOCAL DEVELOPMENT ONLY — never deploy this session-bypass entrypoint.

Runs the panel against the synthetic demo DB with the real broker on a scratch
socket (so bridge writes work end-to-end), plus a /dev-login route that mints a
session without a passkey. The bypass exists only in this script: the deployed
app factory has no such route, and this file lives outside app/ so it cannot
ship into gunicorn's import path by accident.

Usage:  .venv/bin/python scripts/devserver.py   (serves http://localhost:5111,
open /dev-login once to get a session)
"""
import os
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = pathlib.Path(os.environ.get(
    "HERMES_DEV_DATA_DIR",
    pathlib.Path(os.environ.get(
        "HERMES_DATA_DIR",
        pathlib.Path.home() / ".local" / "share" / "hermes",
    )) / "dev",
)).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEMO_DB = DATA_DIR / "health-demo.db"
PANEL_DB = DATA_DIR / "panel-dev.db"
AUDIT_LOG = DATA_DIR / "dev-audit.log"
SOCK = f"/tmp/hermes-dev-{os.getuid()}.sock"  # AF_UNIX path must stay short
GREEN_FIXTURE_ID = "green-days-actionable-v1"
GREEN_DATA_DIR = DATA_DIR / GREEN_FIXTURE_ID
GREEN_DB = GREEN_DATA_DIR / "health.db"
GREEN_MANIFEST = GREEN_DATA_DIR / "fixture-manifest.json"
GREEN_AUDIT_LOG = DATA_DIR / "dev-green-days-audit.log"
GREEN_SOCK = f"/tmp/hermes-green-dev-{os.getuid()}.sock"

if not DEMO_DB.exists():
    subprocess.run([
        sys.executable,
        str(REPO / "scripts" / "make_demo_db.py"),
        "--output", str(DEMO_DB),
    ], check=True)

# Existing development databases keep their actual lane; starting the panel
# does not migrate or relabel them. schema-status validates the ledger read-only.
schema_check = subprocess.run([
    sys.executable, str(REPO / "toolkit" / "health.py"), "schema-status",
], env={**os.environ, "HEALTH_DB": str(DEMO_DB)},
   check=True, capture_output=True, text=True)
demo_schema_version = json.loads(schema_check.stdout)["current_version"]
if demo_schema_version not in {6, 7}:
    raise RuntimeError("development panel requires an initialized schema-v6 or schema-v7 fixture")
DEMO_READINESS_LANE = {6: "development-v6", 7: "development-v7"}[demo_schema_version]

# Seed once, then verify the manifest and database digest on every startup.
# An incomplete or changed fixture fails closed instead of being relabeled.
if not GREEN_DATA_DIR.exists():
    seeded = subprocess.run([
        sys.executable,
        str(REPO / "scripts" / "demo_flow.py"),
        "--data-dir", str(GREEN_DATA_DIR),
        "--fixture-id", GREEN_FIXTURE_ID,
        "--seed-only",
    ], check=True, capture_output=True, text=True)
    json.loads(seeded.stdout)
elif not GREEN_DB.is_file() or not GREEN_MANIFEST.is_file():
    raise RuntimeError("fictional Green-day fixture is incomplete")

verified = subprocess.run([
    sys.executable,
    str(REPO / "scripts" / "demo_flow.py"),
    "--data-dir", str(GREEN_DATA_DIR),
    "--fixture-id", GREEN_FIXTURE_ID,
    "--verify-fixture", str(GREEN_MANIFEST),
], check=True, capture_output=True, text=True)
green_identity = json.loads(verified.stdout)
green_manifest = json.loads(GREEN_MANIFEST.read_text(encoding="utf-8"))
if any((
    green_identity.get("fixture_id") != GREEN_FIXTURE_ID,
    green_identity.get("database_sha256") != green_manifest.get("database_sha256"),
    green_identity.get("seed_sha256") != green_manifest.get("seed_sha256"),
)):
    raise RuntimeError("fictional Green-day fixture identity is inconsistent")

for socket_path in (SOCK, GREEN_SOCK):
    if os.path.exists(socket_path):
        os.unlink(socket_path)

broker = subprocess.Popen(
    [sys.executable, str(REPO / "deploy" / "hermes-bridge")],
    env={**os.environ, "BRIDGE_SOCK": SOCK, "BRIDGE_ALLOWED_UID": str(os.getuid()),
         "HEALTH_DB": str(DEMO_DB), "BRIDGE_HEALTH": str(REPO / "toolkit" / "health.py"),
         "BRIDGE_AUDIT": str(AUDIT_LOG)},
)
green_broker = subprocess.Popen(
    [sys.executable, str(REPO / "deploy" / "hermes-bridge")],
    env={
        **os.environ,
        "BRIDGE_PROFILE": "dashboard-green-days-readonly",
        "BRIDGE_SOCK": GREEN_SOCK,
        "BRIDGE_ALLOWED_UID": str(os.getuid()),
        "HEALTH_DB": str(GREEN_DB),
        "BRIDGE_HEALTH": str(REPO / "toolkit" / "health.py"),
        "BRIDGE_AUDIT": str(GREEN_AUDIT_LOG),
    },
)

os.environ["HEALTH_DB"] = str(DEMO_DB)
os.environ["BRIDGE_SOCKET"] = SOCK
os.environ.setdefault("PANEL_DB", str(PANEL_DB))
sys.path.insert(0, str(REPO))

import app as app_pkg                      # noqa: E402
from app import auth as auth_mod          # noqa: E402
from app import create_app                # noqa: E402

app = create_app({
    "PANEL_COOKIE_SECURE": False,
    "RATELIMIT_ENABLED": False,
    "RECOVERY_FIXED_SCOPE": {
        "data_class": "fictional",
        "fixture_id": "comprehensive-persona-v1",
        "range_from": "2026-03-02",
        "anchor_date": "2026-06-30",
        "readiness_fixture_lane": DEMO_READINESS_LANE,
    },
    "DASHBOARD_GREEN_DAYS_FIXED_SCOPE": {
        key: green_manifest[key]
        for key in (
            "data_class", "fixture_id", "range_from", "range_to",
            "seed_sha256", "database_sha256",
        )
    },
    "DASHBOARD_GREEN_DAYS_BRIDGE_SOCKET": GREEN_SOCK,
})
app_pkg.PUBLIC_ENDPOINTS.add("dev_login")  # local-only passkey-gate bypass


@app.before_request
def _dev_no_passkey_page():
    """The dev instance has no enrolled passkeys, so its /login page is a
    dead end that LOOKS like the production panel ("No passkeys enrolled
    yet" confusion). Route it straight to the dev session instead."""
    from flask import redirect, request
    if request.path == "/login":
        return redirect("/dev-login")


@app.get("/dev-login")
def dev_login():
    """?next=/labs lands on that page after minting the session — lets a
    single headless-Chrome run screenshot any page (local-only, path-only)."""
    from flask import make_response, redirect, request
    with app.app_context():
        token = auth_mod.create_session()
    nxt = request.args.get("next", "/")
    if not (nxt.startswith("/") and not nxt.startswith("//")):
        nxt = "/"
    resp = make_response(redirect(nxt))
    resp.set_cookie(auth_mod.SESSION_COOKIE, token, httponly=True)
    return resp


try:
    app.run(port=5111, debug=False)
finally:
    for process in (broker, green_broker):
        process.terminate()
    for process in (broker, green_broker):
        process.wait(timeout=10)
    for socket_path in (SOCK, GREEN_SOCK):
        pathlib.Path(socket_path).unlink(missing_ok=True)
