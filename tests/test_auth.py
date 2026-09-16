"""Phase 2 acceptance tests: the gate, sessions, enrollment tokens, sign-count
regression, CSRF. The WebAuthn ceremony itself requires a real authenticator
and is verified on-device after deploy (see deploy/server-setup.md)."""
import pytest

from app import auth as auth_mod
from app import create_app
from app.panel_db import get_db


def test_unauthenticated_pages_redirect_to_login(client):
    for path in ("/", "/training", "/mind", "/recovery", "/export"):
        r = client.get(path)
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]


def test_unauthenticated_post_gets_401(client):
    assert client.post("/logout").status_code == 401


def test_public_endpoints_open(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/enroll?token=nope").status_code == 200  # renders the error state


def test_enroll_page_flags_bad_token(client):
    html = client.get("/enroll?token=nope").get_data(as_text=True)
    assert "invalid, used, or expired" in html


def test_expired_session_rejected(app, authed):
    with app.app_context():
        db = get_db()
        db.execute("UPDATE sessions SET expires_at=0")
        db.commit()
    assert authed.get("/").status_code == 302


def test_logout_revokes_session(authed):
    assert authed.get("/").status_code == 200
    r = authed.post("/logout")
    assert r.status_code == 302
    assert authed.get("/").status_code == 302  # session gone server-side


def test_login_options_without_credentials_is_helpful(client):
    r = client.post("/api/auth/login/options")
    assert r.status_code == 400
    assert "enroll" in r.get_json()["error"].lower()


def test_login_verify_without_challenge(client):
    r = client.post("/api/auth/login/verify", json={"credential": "{}"})
    assert r.status_code == 400


def test_register_options_requires_valid_token(app, client):
    r = client.post("/api/auth/register/options", json={"token": "bogus"})
    assert r.status_code == 403

    with app.app_context():
        good = auth_mod.new_enroll_token()
    r = client.post("/api/auth/register/options", json={"token": good})
    assert r.status_code == 200
    opts = r.get_json()
    assert opts["rp"]["id"] == "localhost"
    assert opts["challenge"]

    # single use: consuming it kills it
    with app.app_context():
        auth_mod.consume_enroll_token(good)
    r = client.post("/api/auth/register/options", json={"token": good})
    assert r.status_code == 403


@pytest.mark.parametrize("stored,new,ok", [
    (0, 0, True),    # Apple passkeys: no counter in use
    (0, 1, True),    # counter starts moving
    (5, 6, True),    # normal increment
    (5, 5, False),   # replay
    (5, 0, False),   # cloned authenticator reset
])
def test_sign_count_regression(stored, new, ok):
    if ok:
        auth_mod.validate_sign_count(stored, new)
    else:
        with pytest.raises(auth_mod.SignCountRegression):
            auth_mod.validate_sign_count(stored, new)


def test_csrf_over_https_works_with_same_origin_referer(tmp_path):
    """Regression test for the live enrollment failure (2026-07-06): over HTTPS,
    Flask-WTF additionally requires a same-origin Referer. Our Referrer-Policy
    must therefore be same-origin (no-referrer broke every secure POST)."""
    from tests.support import csrf_from

    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": True,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "https-panel.db"),
        "PANEL_COOKIE_SECURE": False,
    })
    client = app.test_client()
    html = client.get("/login", base_url="https://localhost").get_data(as_text=True)
    csrf = csrf_from(html)

    # Browser behavior under Referrer-Policy: same-origin → Referer present:
    # CSRF passes, request reaches the view (bad enroll token → 403, not 400).
    r = client.post("/api/auth/register/options", json={"token": "bogus"},
                    base_url="https://localhost",
                    headers={"X-CSRFToken": csrf, "Referer": "https://localhost/enroll"})
    assert r.status_code == 403

    # No Referer → Flask-WTF's HTTPS check rejects, and the error is JSON.
    r = client.post("/api/auth/register/options", json={"token": "bogus"},
                    base_url="https://localhost",
                    headers={"X-CSRFToken": csrf})
    assert r.status_code == 400
    assert "referrer" in r.get_json()["error"].lower()


def test_csrf_enforced_when_enabled(tmp_path):
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": True,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "csrf-panel.db"),
        "PANEL_COOKIE_SECURE": False,
    })
    client = app.test_client()
    with app.app_context():
        token = auth_mod.create_session()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    # No CSRF token supplied -> rejected before reaching the view.
    assert client.post("/logout").status_code == 400
    assert client.post("/api/auth/login/options").status_code == 400
