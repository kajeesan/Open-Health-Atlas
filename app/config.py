"""Configuration. Secrets come from an external environment or secret manager.
Nothing secret belongs in this repository.

Required for a production deployment (normally supplied by an external
environment file):
  PANEL_SECRET_KEY   persistent — signs the Flask session (WebAuthn challenges, CSRF)
  PANEL_RP_ID        the WebAuthn relying-party hostname
  PANEL_ORIGIN       https://<PANEL_RP_ID>
  PANEL_DB           an external panel-state database path
Local development just works with the defaults below (http://localhost:5111).
"""
import os
import secrets

from toolkit.hermes_insights.settings import resolve_vault_root


class Config:
    # Ephemeral fallback is fine for local dev; the server MUST set a
    # persistent key or every restart invalidates pending logins + CSRF.
    SECRET_KEY = os.environ.get("PANEL_SECRET_KEY") or secrets.token_hex(32)

    # WebAuthn relying party — localhost defaults for development.
    PANEL_RP_ID = os.environ.get("PANEL_RP_ID", "localhost")
    PANEL_ORIGIN = os.environ.get("PANEL_ORIGIN", "http://localhost:5111")

    # Stable single-user WebAuthn identity and presentation. Deployments must
    # keep PANEL_USER_ID stable after enrolling a passkey.
    PANEL_USER_ID = os.environ.get("PANEL_USER_ID", "hermes-user")
    PANEL_USER_NAME = os.environ.get("PANEL_USER_NAME", "hermes-user")
    HERMES_DISPLAY_NAME = os.environ.get("HERMES_DISPLAY_NAME", "OpenHealthAtlas user")

    # The civil timezone is deployment configuration, not product code.
    HERMES_TIMEZONE = os.environ.get("HERMES_TIMEZONE", "UTC")

    # Medication tracking is installation-owned configuration. The public
    # project ships only a neutral placeholder; deployments choose the exact
    # label(s) they use in meds_log.
    HERMES_PRIMARY_MEDICATION = os.environ.get(
        "HERMES_PRIMARY_MEDICATION", "medication"
    )
    HERMES_MEDICATION_ALIASES = os.environ.get(
        "HERMES_MEDICATION_ALIASES", ""
    )

    # Product data defaults outside the source checkout.
    HERMES_DATA_DIR = os.environ.get(
        "HERMES_DATA_DIR",
        os.path.join(os.path.expanduser("~"), ".local", "share", "hermes"),
    )

    # Panel state DB also defaults outside the product checkout.
    PANEL_DB = os.environ.get(
        "PANEL_DB", os.path.join(HERMES_DATA_DIR, "panel.db")
    )

    # The HEALTH database is read-only from the panel. Initialize it with the
    # provided setup command or point at a synthetic/user-owned database.
    HEALTH_DB = os.environ.get(
        "HEALTH_DB", os.path.join(HERMES_DATA_DIR, "health.db")
    )

    # Unix socket of the hermes-bridge write broker (app/bridge.py). The broker
    # runs as hermes outside the panel's sandbox — see docs/ARCHITECTURE.md.
    # Tests/dev override this with a stub socket server.
    BRIDGE_SOCKET = os.environ.get("BRIDGE_SOCKET", "/run/hermes-bridge/bridge.sock")

    # Optional server-owned scope for the isolated fictional Recovery display.
    # Every mode still uses the governed privacy-safe evidence projection; this
    # scope additionally pins the accepted fixture lane and corrected result.
    # Request parameters can never select a database, fixture, or range.
    RECOVERY_FIXED_SCOPE = None

    # Optional server-owned scope for the isolated fictional Green-day
    # dashboard acceptance.  Production uses the governed engine's trailing
    # 365-day window.  The local acceptance server may inject one exact
    # fictional fixture range; browser input can never select it.
    DASHBOARD_GREEN_DAYS_FIXED_SCOPE = None
    DASHBOARD_GREEN_DAYS_BRIDGE_SOCKET = None

    # Shared with CLI writes: explicit vault, data-dir/vault, or the legacy
    # DB-adjacent layout when only a database path is configured.
    VAULT_DIR = resolve_vault_root(HEALTH_DB)

    # Secure cookies on by default; PANEL_COOKIE_SECURE=0 for http://localhost dev.
    PANEL_COOKIE_SECURE = os.environ.get("PANEL_COOKIE_SECURE", "1") == "1"

    # Flask session cookie (holds WebAuthn challenges + CSRF token only).
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict"
    SESSION_COOKIE_SECURE = os.environ.get("PANEL_COOKIE_SECURE", "1") == "1"

    # Superseded narrative/analytics routes are retained only as explicit test
    # fixtures while their supported replacements settle. This is deliberately
    # not environment-configurable: a normal panel process must return 410 and
    # cannot silently reactivate an all-time legacy chat, unvalidated vault
    # prose, or the old correlation endpoint.
    ENABLE_LEGACY_COMPATIBILITY_REFERENCE_ROUTES = False
