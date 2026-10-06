"""§2b regression tests: the full WebAuthn ceremony + proxy-scheme handling.

SoftPasskey emulates what Safari/Touch ID does in production: EC P-256 key,
'none' attestation, and — critically — the user-verification flag set, since
the server requires UV on both ceremonies (soft-webauthn on PyPI never sets UV,
which is why this helper exists). Built on cryptography + cbor2, both already
installed as py_webauthn dependencies — no new packages.

Production shape being pinned down:
- tailscale serve terminates TLS and forwards plain HTTP + X-Forwarded-Proto.
- Flask must see those requests as secure (ProxyFix), otherwise Flask-WTF
  silently skips its HTTPS referrer check and url scheme/cookie logic is wrong.
"""
import json
import os
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import pytest
from tests.support import csrf_from
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

import cbor2
from app import auth as auth_mod
from app import create_app
from app.panel_db import get_db
from app.panel_db import init_db
from app.routes import auth as auth_routes

ORIGIN = "https://localhost"


class SoftPasskey:
    """Software authenticator holding one credential; UV always performed."""

    AAGUID = b"\x00" * 16

    def __init__(self):
        self.credential_id = os.urandom(32)
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.sign_count = 0
        self.rp_id = None

    def _cose_public_key(self) -> bytes:
        pub = self.key.public_key().public_numbers()
        # COSE EC2 / ES256 on P-256 (RFC 9053).
        return cbor2.dumps({1: 2, 3: -7, -1: 1,
                            -2: pub.x.to_bytes(32, "big"),
                            -3: pub.y.to_bytes(32, "big")})

    def register(self, options_json: str, origin: str = ORIGIN) -> str:
        """navigator.credentials.create(): options JSON in, wire-format payload
        out (same shape auth.js sends)."""
        opts = json.loads(options_json)
        self.rp_id = opts["rp"]["id"]
        client_data = json.dumps({"type": "webauthn.create",
                                  "challenge": opts["challenge"],
                                  "origin": origin}).encode()
        flags = b"\x45"  # user-present | user-verified | attested-data
        auth_data = (sha256(self.rp_id.encode()).digest() + flags
                     + struct.pack(">I", self.sign_count)
                     + self.AAGUID
                     + struct.pack(">H", len(self.credential_id))
                     + self.credential_id + self._cose_public_key())
        att_obj = cbor2.dumps({"fmt": "none", "attStmt": {},
                               "authData": auth_data})
        return json.dumps({
            "id": auth_mod.b64u(self.credential_id),
            "rawId": auth_mod.b64u(self.credential_id),
            "type": "public-key",
            "clientExtensionResults": {},
            "response": {"clientDataJSON": auth_mod.b64u(client_data),
                         "attestationObject": auth_mod.b64u(att_obj),
                         "transports": ["internal"]},
        })

    def authenticate(self, options_json: str, origin: str = ORIGIN) -> str:
        """navigator.credentials.get(): assertion in wire format."""
        opts = json.loads(options_json)
        assert opts["rpId"] == self.rp_id
        self.sign_count += 1
        client_data = json.dumps({"type": "webauthn.get",
                                  "challenge": opts["challenge"],
                                  "origin": origin}).encode()
        flags = b"\x05"  # user-present | user-verified
        auth_data = (sha256(self.rp_id.encode()).digest() + flags
                     + struct.pack(">I", self.sign_count))
        signature = self.key.sign(auth_data + sha256(client_data).digest(),
                                  ec.ECDSA(hashes.SHA256()))
        return json.dumps({
            "id": auth_mod.b64u(self.credential_id),
            "rawId": auth_mod.b64u(self.credential_id),
            "type": "public-key",
            "clientExtensionResults": {},
            "response": {"authenticatorData": auth_mod.b64u(auth_data),
                         "clientDataJSON": auth_mod.b64u(client_data),
                         "signature": auth_mod.b64u(signature),
                         "userHandle": None},
        })


@pytest.fixture()
def https_app(tmp_path):
    """Production-shape app: CSRF on, secure cookies, https origin."""
    return create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": True,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "roundtrip-panel.db"),
        "PANEL_COOKIE_SECURE": True,
        "SESSION_COOKIE_SECURE": True,
        "PANEL_RP_ID": "localhost",
        "PANEL_ORIGIN": ORIGIN,
    })


def _session_cookie(resp):
    for line in resp.headers.getlist("Set-Cookie"):
        if line.startswith(auth_mod.SESSION_COOKIE + "="):
            return line
    return None


def _enroll(app, client, device, label="pytest softpasskey"):
    """Run the full enrollment ceremony. Returns (enroll_token, csrf_headers,
    verify_response) so tests can assert on the pieces they care about."""
    with app.app_context():
        enroll = auth_mod.new_enroll_token()
    page = client.get(f"/enroll?token={enroll}",
                      base_url=ORIGIN).get_data(as_text=True)
    hdrs = {"X-CSRFToken": csrf_from(page), "Referer": ORIGIN + "/enroll"}
    r = client.post("/api/auth/register/options", json={"token": enroll},
                    base_url=ORIGIN, headers=hdrs)
    assert r.status_code == 200
    payload = device.register(r.get_data(as_text=True))
    r = client.post("/api/auth/register/verify",
                    json={"token": enroll, "credential": payload, "label": label},
                    base_url=ORIGIN, headers=hdrs)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    return enroll, hdrs, r


def test_passkey_register_then_login_roundtrip(https_app):
    """The full ceremony end-to-end: enroll-token → register → auto-login →
    logout → login with the same passkey — CSRF enforced throughout."""
    client = https_app.test_client()
    device = SoftPasskey()
    enroll, hdrs, r = _enroll(https_app, client, device)
    cookie = _session_cookie(r)
    assert cookie and "Secure" in cookie and "HttpOnly" in cookie \
        and "SameSite=Strict" in cookie

    # Enrollment logs the device straight in.
    assert client.get("/", base_url=ORIGIN).status_code == 200

    # The enroll token is single-use.
    r = client.post("/api/auth/register/options", json={"token": enroll},
                    base_url=ORIGIN, headers=hdrs)
    assert r.status_code == 403

    # Logout revokes the session.
    assert client.post("/logout", base_url=ORIGIN,
                       headers=hdrs).status_code == 302
    assert client.get("/", base_url=ORIGIN).status_code == 302

    # Fresh login ceremony with the same passkey.
    page = client.get("/login", base_url=ORIGIN).get_data(as_text=True)
    hdrs = {"X-CSRFToken": csrf_from(page), "Referer": ORIGIN + "/login"}
    r = client.post("/api/auth/login/options", base_url=ORIGIN, headers=hdrs)
    assert r.status_code == 200
    assertion = device.authenticate(r.get_data(as_text=True))
    r = client.post("/api/auth/login/verify", json={"credential": assertion},
                    base_url=ORIGIN, headers=hdrs)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert client.get("/", base_url=ORIGIN).status_code == 200


def test_cloned_passkey_sign_count_regression_rejected(https_app):
    """A rewound sign counter (cloned authenticator) is rejected through the
    full stack, not just the unit check."""
    client = https_app.test_client()
    device = SoftPasskey()
    _, hdrs, _ = _enroll(https_app, client, device, label="d")

    # Legitimate login moves the counter to 1.
    r = client.post("/api/auth/login/options", base_url=ORIGIN, headers=hdrs)
    client.post("/api/auth/login/verify",
                json={"credential": device.authenticate(r.get_data(as_text=True))},
                base_url=ORIGIN, headers=hdrs)

    # A clone re-using an old counter value must be rejected.
    device.sign_count = 0  # authenticate() bumps it to 1 == stored → regression
    r = client.post("/api/auth/login/options", base_url=ORIGIN, headers=hdrs)
    r = client.post("/api/auth/login/verify",
                    json={"credential": device.authenticate(r.get_data(as_text=True))},
                    base_url=ORIGIN, headers=hdrs)
    assert r.status_code == 403


def test_forwarded_proto_activates_https_csrf_layer(tmp_path):
    """Behind `tailscale serve` the app receives plain HTTP plus
    X-Forwarded-Proto: https. Without ProxyFix, request.is_secure is False and
    Flask-WTF silently SKIPS its HTTPS referrer check in production. With
    ProxyFix the forwarded scheme is trusted and the check is enforced (§2b)."""
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": True,
        "RATELIMIT_ENABLED": False,
        "PANEL_DB": str(tmp_path / "proxyfix-panel.db"),
        "PANEL_COOKIE_SECURE": False,
    })
    client = app.test_client()
    fwd = {"X-Forwarded-Proto": "https"}
    page = client.get("/login", headers=fwd).get_data(as_text=True)
    csrf = csrf_from(page)

    # Valid CSRF token but NO Referer: on a (forwarded-)https request this must
    # be rejected by the referrer check, never reach the view.
    r = client.post("/api/auth/register/options", json={"token": "bogus"},
                    headers={**fwd, "X-CSRFToken": csrf})
    assert r.status_code == 400
    assert "referrer" in r.get_json()["error"].lower()

    # With a same-origin Referer the request reaches the view (bad token → 403).
    r = client.post("/api/auth/register/options", json={"token": "bogus"},
                    headers={**fwd, "X-CSRFToken": csrf,
                             "Referer": "https://localhost/enroll"})
    assert r.status_code == 403


def test_zero_counter_login_cannot_replay_captured_cookie_after_logout(https_app):
    client = https_app.test_client()
    device = SoftPasskey()
    _, headers, _ = _enroll(https_app, client, device)
    client.post("/logout", base_url=ORIGIN, headers=headers)
    options = client.post("/api/auth/login/options", base_url=ORIGIN, headers=headers)
    pending_cookie = client.get_cookie("session").value
    device.sign_count = -1
    assertion = device.authenticate(options.get_data(as_text=True))
    first = client.post("/api/auth/login/verify", json={"credential": assertion},
                        base_url=ORIGIN, headers=headers)
    assert first.status_code == 200
    assert client.post("/logout", base_url=ORIGIN, headers=headers).status_code == 302

    client.set_cookie("session", pending_cookie)
    replay = client.post("/api/auth/login/verify", json={"credential": assertion},
                         base_url=ORIGIN, headers=headers)

    assert replay.status_code == 400
    assert _session_cookie(replay) is None
    with https_app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM sessions WHERE revoked=0").fetchone()[0] == 0


def test_concurrent_enrollment_consumes_one_token_once(https_app, monkeypatch):
    with https_app.app_context():
        token = auth_mod.new_enroll_token()
    prepared = []
    for _ in range(2):
        client, device = https_app.test_client(), SoftPasskey()
        page = client.get("/enroll?token=" + token, base_url=ORIGIN).get_data(as_text=True)
        headers = {"X-CSRFToken": csrf_from(page), "Referer": ORIGIN + "/enroll"}
        options = client.post("/api/auth/register/options", json={"token": token},
                              base_url=ORIGIN, headers=headers)
        prepared.append((client, headers, device.register(options.get_data(as_text=True))))
    real_verify = auth_routes.verify_registration_response
    barrier = threading.Barrier(2)

    def synchronized_verify(**kwargs):
        verified = real_verify(**kwargs)
        barrier.wait(timeout=5)
        return verified

    def finish(item):
        client, headers, credential = item
        return client.post("/api/auth/register/verify",
                           json={"token": token, "credential": credential},
                           base_url=ORIGIN, headers=headers)

    monkeypatch.setattr(auth_routes, "verify_registration_response", synchronized_verify)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(finish, prepared))

    assert sorted(response.status_code for response in responses) == [200, 403]
    assert sum(_session_cookie(response) is not None for response in responses) == 1
    with https_app.app_context():
        assert len(auth_mod.all_credentials()) == 1
        assert not auth_mod.enroll_token_valid(token)


@pytest.mark.parametrize("elapsed,status,has_session", [(299, 200, True), (300, 400, False)])
def test_login_challenge_five_minute_boundary(https_app, monkeypatch, elapsed, status, has_session):
    client = https_app.test_client()
    device = SoftPasskey()
    _, headers, _ = _enroll(https_app, client, device)
    client.post("/logout", base_url=ORIGIN, headers=headers)
    now = int(auth_mod.time.time())
    monkeypatch.setattr(auth_mod.time, "time", lambda: now)
    options = client.post("/api/auth/login/options", base_url=ORIGIN, headers=headers)
    assertion = device.authenticate(options.get_data(as_text=True))

    monkeypatch.setattr(auth_mod.time, "time", lambda: now + elapsed)
    response = client.post("/api/auth/login/verify", json={"credential": assertion},
                           base_url=ORIGIN, headers=headers)

    assert response.status_code == status
    assert (_session_cookie(response) is not None) == has_session


def test_concurrent_login_uses_pending_cookie_once(https_app, monkeypatch):
    client = https_app.test_client()
    device = SoftPasskey()
    _, headers, _ = _enroll(https_app, client, device)
    client.post("/logout", base_url=ORIGIN, headers=headers)
    options = client.post("/api/auth/login/options", base_url=ORIGIN, headers=headers)
    duplicate = https_app.test_client()
    duplicate.set_cookie("session", client.get_cookie("session").value)
    device.sign_count = -1
    assertion = device.authenticate(options.get_data(as_text=True))
    real_consume = auth_mod.consume_challenge
    barrier = threading.Barrier(2)

    def synchronized_consume(*args, **kwargs):
        barrier.wait(timeout=5)
        return real_consume(*args, **kwargs)

    def finish(browser):
        return browser.post("/api/auth/login/verify", json={"credential": assertion},
                            base_url=ORIGIN, headers=headers)

    monkeypatch.setattr(auth_mod, "consume_challenge", synchronized_consume)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(finish, [client, duplicate]))

    assert sorted(response.status_code for response in responses) == [200, 400]
    assert sum(_session_cookie(response) is not None for response in responses) == 1
    with https_app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM sessions WHERE revoked=0").fetchone()[0] == 1


def test_failed_signature_consumes_login_even_with_cookie_restored(https_app):
    client = https_app.test_client()
    device = SoftPasskey()
    _, headers, _ = _enroll(https_app, client, device)
    client.post("/logout", base_url=ORIGIN, headers=headers)
    options = client.post("/api/auth/login/options", base_url=ORIGIN, headers=headers)
    pending_cookie = client.get_cookie("session").value
    assertion = device.authenticate(options.get_data(as_text=True))
    invalid = json.loads(assertion)
    invalid["response"]["signature"] = auth_mod.b64u(b"invalid signature")
    rejected = client.post("/api/auth/login/verify", json={"credential": json.dumps(invalid)},
                           base_url=ORIGIN, headers=headers)
    assert rejected.status_code == 403

    client.set_cookie("session", pending_cookie)
    retried = client.post("/api/auth/login/verify", json={"credential": assertion},
                          base_url=ORIGIN, headers=headers)

    assert retried.status_code == 400
    assert _session_cookie(retried) is None


def test_login_assertion_requires_originating_browser_cookie(https_app):
    client = https_app.test_client()
    device = SoftPasskey()
    _, headers, _ = _enroll(https_app, client, device)
    client.post("/logout", base_url=ORIGIN, headers=headers)
    options = client.post("/api/auth/login/options", base_url=ORIGIN, headers=headers)
    assertion = device.authenticate(options.get_data(as_text=True))
    stranger = https_app.test_client()
    page = stranger.get("/login", base_url=ORIGIN).get_data(as_text=True)
    stranger_headers = {"X-CSRFToken": csrf_from(page), "Referer": ORIGIN + "/login"}

    rejected = stranger.post("/api/auth/login/verify", json={"credential": assertion},
                             base_url=ORIGIN, headers=stranger_headers)
    accepted = client.post("/api/auth/login/verify", json={"credential": assertion},
                           base_url=ORIGIN, headers=headers)

    assert rejected.status_code == 400
    assert _session_cookie(rejected) is None
    assert accepted.status_code == 200


def test_new_login_options_invalidate_superseded_cookie(https_app):
    client = https_app.test_client()
    device = SoftPasskey()
    _, headers, _ = _enroll(https_app, client, device)
    client.post("/logout", base_url=ORIGIN, headers=headers)
    old_options = client.post("/api/auth/login/options", base_url=ORIGIN, headers=headers)
    old_cookie = client.get_cookie("session").value
    assertion = device.authenticate(old_options.get_data(as_text=True))
    client.post("/api/auth/login/options", base_url=ORIGIN, headers=headers)

    client.set_cookie("session", old_cookie)
    response = client.post("/api/auth/login/verify", json={"credential": assertion},
                           base_url=ORIGIN, headers=headers)

    assert response.status_code == 400
    assert _session_cookie(response) is None


def _pending_registration(app, token, device):
    client = app.test_client()
    page = client.get("/enroll?token=" + token, base_url=ORIGIN).get_data(as_text=True)
    headers = {"X-CSRFToken": csrf_from(page), "Referer": ORIGIN + "/enroll"}
    options = client.post("/api/auth/register/options", json={"token": token},
                          base_url=ORIGIN, headers=headers)
    assert options.status_code == 200
    return client, headers, device.register(options.get_data(as_text=True))


def test_registration_cannot_substitute_another_enrollment_token(https_app):
    with https_app.app_context():
        original = auth_mod.new_enroll_token()
        substitute = auth_mod.new_enroll_token()
    client, headers, credential = _pending_registration(https_app, original, SoftPasskey())

    response = client.post("/api/auth/register/verify",
                           json={"token": substitute, "credential": credential},
                           base_url=ORIGIN, headers=headers)

    assert response.status_code == 400
    assert _session_cookie(response) is None
    with https_app.app_context():
        assert auth_mod.all_credentials() == []
        assert auth_mod.enroll_token_valid(original)
        assert auth_mod.enroll_token_valid(substitute)


def test_registration_handle_cannot_authorize_login_ceremony(https_app):
    with https_app.app_context():
        token = auth_mod.new_enroll_token()
    client, headers, credential = _pending_registration(https_app, token, SoftPasskey())
    with client.session_transaction() as cookie:
        cookie["login_ceremony"] = cookie["register_ceremony"]

    response = client.post("/api/auth/login/verify", json={"credential": credential},
                           base_url=ORIGIN, headers=headers)

    assert response.status_code == 400
    assert "no pending login" in response.get_json()["error"]
    assert _session_cookie(response) is None


def test_registration_challenge_expires_before_enrollment_token(https_app, monkeypatch):
    with https_app.app_context():
        token = auth_mod.new_enroll_token()
    now = int(auth_mod.time.time())
    monkeypatch.setattr(auth_mod.time, "time", lambda: now)
    client, headers, credential = _pending_registration(https_app, token, SoftPasskey())

    monkeypatch.setattr(auth_mod.time, "time", lambda: now + 300)
    response = client.post("/api/auth/register/verify",
                           json={"token": token, "credential": credential},
                           base_url=ORIGIN, headers=headers)

    assert response.status_code == 400
    with https_app.app_context():
        assert auth_mod.all_credentials() == []
        assert auth_mod.enroll_token_valid(token)


def test_enrollment_expiry_is_rechecked_after_cryptographic_verification(https_app, monkeypatch):
    now = int(auth_mod.time.time())
    monkeypatch.setattr(auth_mod.time, "time", lambda: now)
    with https_app.app_context():
        token = auth_mod.new_enroll_token()
    client, headers, credential = _pending_registration(https_app, token, SoftPasskey())
    real_verify = auth_routes.verify_registration_response

    def delayed_verification(**kwargs):
        result = real_verify(**kwargs)
        monkeypatch.setattr(auth_mod.time, "time", lambda: now + 900)
        return result

    monkeypatch.setattr(auth_routes, "verify_registration_response", delayed_verification)
    response = client.post("/api/auth/register/verify",
                           json={"token": token, "credential": credential},
                           base_url=ORIGIN, headers=headers)

    assert response.status_code == 403
    assert _session_cookie(response) is None
    with https_app.app_context():
        assert auth_mod.all_credentials() == []


def test_failed_credential_insert_rolls_back_token_consumption(https_app):
    with https_app.app_context():
        token = auth_mod.new_enroll_token()
        db = get_db()
        db.execute("CREATE TRIGGER reject_credential BEFORE INSERT ON credentials "
                   "BEGIN SELECT RAISE(ABORT, 'fictional insertion failure'); END")
        db.commit()
    client, headers, credential = _pending_registration(https_app, token, SoftPasskey())
    pending_cookie = client.get_cookie("session").value

    response = client.post("/api/auth/register/verify",
                           json={"token": token, "credential": credential},
                           base_url=ORIGIN, headers=headers)

    assert response.status_code == 503
    assert _session_cookie(response) is None
    with https_app.app_context():
        db = get_db()
        assert auth_mod.enroll_token_valid(token)
        assert auth_mod.all_credentials() == []
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        db.execute("DROP TRIGGER reject_credential")
        db.commit()
    client.set_cookie("session", pending_cookie)
    retry = client.post("/api/auth/register/verify",
                        json={"token": token, "credential": credential},
                        base_url=ORIGIN, headers=headers)
    assert retry.status_code == 400
    fresh, fresh_headers, fresh_credential = _pending_registration(https_app, token, SoftPasskey())
    recovered = fresh.post("/api/auth/register/verify",
                           json={"token": token, "credential": fresh_credential},
                           base_url=ORIGIN, headers=fresh_headers)
    assert recovered.status_code == 200


@pytest.mark.parametrize("ceremony,legacy_key", [
    ("login", "auth_challenge"), ("register", "reg_challenge"),
])
def test_legacy_cookie_challenge_fails_closed(https_app, ceremony, legacy_key):
    client = https_app.test_client()
    page = client.get("/login", base_url=ORIGIN).get_data(as_text=True)
    headers = {"X-CSRFToken": csrf_from(page), "Referer": ORIGIN + "/login"}
    with https_app.app_context():
        token = auth_mod.new_enroll_token()
    with client.session_transaction() as cookie:
        cookie[legacy_key] = auth_mod.b64u(b"legacy challenge")

    response = client.post(f"/api/auth/{ceremony}/verify",
                           json={"token": token, "credential": "{}"},
                           base_url=ORIGIN, headers=headers)

    assert response.status_code == 400
    assert "no pending" in response.get_json()["error"]
    assert _session_cookie(response) is None


def test_additive_panel_upgrade_preserves_enrolled_passkey(https_app):
    client = https_app.test_client()
    device = SoftPasskey()
    _, headers, _ = _enroll(https_app, client, device)
    with https_app.app_context():
        db = get_db()
        db.execute("DROP TABLE auth_challenges")
        db.commit()

    init_db(https_app)
    init_db(https_app)
    options = client.post("/api/auth/login/options", base_url=ORIGIN, headers=headers)
    response = client.post("/api/auth/login/verify",
                           json={"credential": device.authenticate(options.get_data(as_text=True))},
                           base_url=ORIGIN, headers=headers)

    assert response.status_code == 200
    with https_app.app_context():
        db = get_db()
        assert len(auth_mod.all_credentials()) == 1
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
