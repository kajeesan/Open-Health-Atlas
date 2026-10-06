"""Sessions, enrollment tokens, and WebAuthn bookkeeping.

Design:
- Sessions are SERVER-SIDE and revocable: the cookie holds a random token,
  the DB stores only its sha256. 12-hour absolute expiry.
- Enrollment is ALWAYS gated by a one-time token generated through the trusted
  administrative shell (`flask enroll-token`).
- Sign-count regression is rejected (cloned-authenticator detection). Apple
  platform authenticators always report 0, so the check only bites when a
  counter is actually in use.
"""
import base64
import hashlib
import secrets
import time
from collections.abc import Callable

from flask import current_app, g, request

from app.panel_db import get_db

SESSION_COOKIE = "panel_session"
SESSION_TTL_SECONDS = 12 * 3600
ENROLL_TTL_SECONDS = 15 * 60
CHALLENGE_TTL_SECONDS = 5 * 60


class SignCountRegression(Exception):
    pass


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def from_b64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def audit(event: str, detail: str = "") -> None:
    db = get_db()
    db.execute("INSERT INTO audit_log(ts, event, detail) VALUES(?,?,?)",
               (int(time.time()), event, detail))
    db.commit()


# ---------------------------------------------------------------- sessions
def create_session() -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    db = get_db()
    db.execute(
        "INSERT INTO sessions(token_hash, created_at, expires_at, last_seen_at) VALUES(?,?,?,?)",
        (_hash(token), now, now + SESSION_TTL_SECONDS, now),
    )
    db.commit()
    return token


def current_session():
    """Row for the request's valid session, or None. Cached per request."""
    if "panel_session_row" in g:
        return g.panel_session_row
    row = None
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        now = int(time.time())
        db = get_db()
        row = db.execute(
            "SELECT * FROM sessions WHERE token_hash=? AND revoked=0 AND expires_at>?",
            (_hash(token), now),
        ).fetchone()
        if row and now - (row["last_seen_at"] or 0) > 60:
            db.execute("UPDATE sessions SET last_seen_at=? WHERE token_hash=?",
                       (now, row["token_hash"]))
            db.commit()
    g.panel_session_row = row
    return row


def revoke_current_session() -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db = get_db()
        db.execute("UPDATE sessions SET revoked=1 WHERE token_hash=?", (_hash(token),))
        db.commit()
    g.pop("panel_session_row", None)


def set_session_cookie(resp, token: str):
    resp.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=current_app.config["PANEL_COOKIE_SECURE"],
        samesite="Strict",
        path="/",
    )
    return resp


def clear_session_cookie(resp):
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


# ---------------------------------------------------------- enrollment tokens
def new_enroll_token() -> str:
    token = secrets.token_urlsafe(24)
    db = get_db()
    db.execute("INSERT INTO enroll_tokens(token_hash, expires_at) VALUES(?,?)",
               (_hash(token), int(time.time()) + ENROLL_TTL_SECONDS))
    db.commit()
    return token


def enroll_token_valid(token: str) -> bool:
    row = get_db().execute(
        "SELECT 1 FROM enroll_tokens WHERE token_hash=? AND used=0 AND expires_at>?",
        (_hash(token), int(time.time())),
    ).fetchone()
    return row is not None


def new_challenge(challenge: bytes, ceremony: str, *, now: int,
                  enrollment_token: str = "", previous_handle: str | None = None) -> str:
    """Persist a short-lived ceremony and return its opaque browser handle.

    Replacing a pending ceremony invalidates its previous handle server-side.
    """
    handle = secrets.token_urlsafe(32)
    db = get_db()
    with db:
        db.execute("DELETE FROM auth_challenges WHERE expires_at<=?", (now,))
        if previous_handle:
            db.execute("DELETE FROM auth_challenges WHERE token_hash=?",
                       (_hash(previous_handle),))
        db.execute(
            "INSERT INTO auth_challenges(token_hash,ceremony,challenge,"
            "enrollment_token_hash,expires_at) VALUES(?,?,?,?,?)",
            (_hash(handle), ceremony, challenge, _hash(enrollment_token),
             now + CHALLENGE_TTL_SECONDS),
        )
    return handle


def consume_challenge(handle: str | None, ceremony: str, *, clock: Callable[[], float],
                      enrollment_token: str = "") -> bytes | None:
    """Return a matching unexpired challenge once, including across workers.

    An attempted ceremony consumes the handle even when verification later fails.
    """
    if not handle:
        return None
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        now = int(clock())
        row = db.execute(
            "SELECT challenge FROM auth_challenges WHERE token_hash=? "
            "AND ceremony=? AND enrollment_token_hash=? AND expires_at>?",
            (_hash(handle), ceremony, _hash(enrollment_token), now),
        ).fetchone()
        db.execute("DELETE FROM auth_challenges WHERE token_hash=?", (_hash(handle),))
    return row["challenge"] if row is not None else None


# ------------------------------------------------------------- credentials
def enroll_credential(token: str, credential_id: bytes, public_key: bytes,
                      sign_count: int, transports: str, label: str, *,
                      clock: Callable[[], float]) -> bool:
    """Store a credential only if its enrollment token can be consumed atomically.

    Return False for a used or expired token. An insertion failure rolls back
    token consumption and propagates the database error.
    """
    db = get_db()
    with db:
        db.execute("BEGIN IMMEDIATE")
        now = int(clock())
        consumed = db.execute(
            "UPDATE enroll_tokens SET used=1 WHERE token_hash=? AND used=0 AND expires_at>?",
            (_hash(token), now),
        )
        if consumed.rowcount != 1:
            return False
        db.execute(
            "INSERT INTO credentials(credential_id, public_key, sign_count, transports, label, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (credential_id, public_key, sign_count, transports, label, now),
        )
    return True


def all_credentials():
    return get_db().execute("SELECT * FROM credentials").fetchall()


def find_credential(credential_id: bytes):
    return get_db().execute(
        "SELECT * FROM credentials WHERE credential_id=?", (credential_id,)
    ).fetchone()


def validate_sign_count(stored: int, new: int) -> None:
    """Raise on counter regression. A counter of 0 on both sides means the
    authenticator doesn't use one (Apple passkeys) — nothing to check then."""
    if (stored > 0 or new > 0) and new <= stored:
        raise SignCountRegression(f"sign count went {stored} -> {new}")


def update_sign_count(credential_id: bytes, new: int) -> None:
    db = get_db()
    db.execute("UPDATE credentials SET sign_count=? WHERE credential_id=?",
               (new, credential_id))
    db.commit()
