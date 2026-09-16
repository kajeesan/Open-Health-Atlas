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
from hashlib import sha256

import pytest
from tests.support import csrf_from
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

import cbor2
from app import auth as auth_mod
from app import create_app

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
