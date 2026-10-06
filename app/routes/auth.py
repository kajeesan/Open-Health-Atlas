"""WebAuthn login + enrollment endpoints.

Ceremonies expire and are consumed server-side. The signed Flask cookie holds
only opaque handles so restoring an old cookie cannot replay a ceremony.
"""
import json
import time

from flask import (Blueprint, current_app, jsonify, redirect, render_template,
                   request, session, url_for)
from webauthn import (generate_authentication_options,
                      generate_registration_options, options_to_json,
                      verify_authentication_response,
                      verify_registration_response)
from webauthn.helpers.exceptions import (InvalidAuthenticationResponse,
                                         InvalidRegistrationResponse)
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
                                      PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement,
                                      UserVerificationRequirement)

from app import auth, limiter

bp = Blueprint("auth", __name__)


def _rp_id() -> str:
    return current_app.config["PANEL_RP_ID"]


def _origin() -> str:
    return current_app.config["PANEL_ORIGIN"]


def _user_id() -> bytes:
    return current_app.config["PANEL_USER_ID"].encode("utf-8")


def _user_name() -> str:
    return current_app.config["PANEL_USER_NAME"]


def _safe_next() -> str:
    nxt = request.args.get("next", "/")
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"


# ------------------------------------------------------------------- pages
@bp.get("/login")
def login_page():
    if auth.current_session():
        return redirect("/")
    return render_template("login.html", next=_safe_next())


@bp.get("/enroll")
def enroll_page():
    token = request.args.get("token", "")
    return render_template("enroll.html",
                           token=token,
                           token_ok=auth.enroll_token_valid(token))


@bp.post("/logout")
def logout():
    auth.audit("auth.logout")
    auth.revoke_current_session()
    return auth.clear_session_cookie(redirect(url_for("auth.login_page")))


# ------------------------------------------------------------------- login
@bp.post("/api/auth/login/options")
@limiter.limit("10/minute")
def login_options():
    creds = auth.all_credentials()
    if not creds:
        return jsonify(error="No passkeys enrolled yet — use an enrollment link "
                             "(see README: passkey enrollment)."), 400
    options = generate_authentication_options(
        rp_id=_rp_id(),
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=c["credential_id"]) for c in creds
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    session["login_ceremony"] = auth.new_challenge(
        options.challenge, "login", now=int(time.time()),
        previous_handle=session.get("login_ceremony"),
    )
    return current_app.response_class(options_to_json(options),
                                      mimetype="application/json")


@bp.post("/api/auth/login/verify")
@limiter.limit("10/minute")
def login_verify():
    challenge = auth.consume_challenge(
        session.pop("login_ceremony", None), "login", clock=time.time,
    )
    if challenge is None:
        return jsonify(error="no pending login challenge"), 400
    body = request.get_json(force=True, silent=True) or {}
    cred_json = body.get("credential")
    if not cred_json:
        return jsonify(error="missing credential"), 400

    try:
        credential_id = auth.from_b64u(json.loads(cred_json)["rawId"])
    except (ValueError, KeyError):
        return jsonify(error="malformed credential"), 400
    stored = auth.find_credential(credential_id)
    if stored is None:
        auth.audit("auth.login_failed", "unknown credential")
        return jsonify(error="unknown credential"), 403

    try:
        verification = verify_authentication_response(
            credential=cred_json,
            expected_challenge=challenge,
            expected_rp_id=_rp_id(),
            expected_origin=_origin(),
            credential_public_key=stored["public_key"],
            credential_current_sign_count=stored["sign_count"],
            require_user_verification=True,
        )
        auth.validate_sign_count(stored["sign_count"], verification.new_sign_count)
    except (InvalidAuthenticationResponse, auth.SignCountRegression) as exc:
        auth.audit("auth.login_failed", str(exc))
        return jsonify(error="verification failed"), 403

    auth.update_sign_count(credential_id, verification.new_sign_count)
    auth.audit("auth.login", stored["label"] or "")
    token = auth.create_session()
    resp = jsonify(ok=True)
    return auth.set_session_cookie(resp, token)


# -------------------------------------------------------------- enrollment
@bp.post("/api/auth/register/options")
@limiter.limit("10/minute")
def register_options():
    body = request.get_json(force=True, silent=True) or {}
    token = body.get("token", "")
    if not auth.enroll_token_valid(token):
        return jsonify(error="invalid or expired enrollment token"), 403
    options = generate_registration_options(
        rp_id=_rp_id(),
        rp_name="OpenHealthAtlas",
        user_id=_user_id(),
        user_name=_user_name(),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=c["credential_id"])
            for c in auth.all_credentials()
        ],
    )
    session["register_ceremony"] = auth.new_challenge(
        options.challenge, "register", now=int(time.time()), enrollment_token=token,
        previous_handle=session.get("register_ceremony"),
    )
    return current_app.response_class(options_to_json(options),
                                      mimetype="application/json")


@bp.post("/api/auth/register/verify")
@limiter.limit("10/minute")
def register_verify():
    body = request.get_json(force=True, silent=True) or {}
    token = body.get("token", "")
    if not auth.enroll_token_valid(token):
        return jsonify(error="invalid or expired enrollment token"), 403
    challenge = auth.consume_challenge(
        session.pop("register_ceremony", None), "register", clock=time.time,
        enrollment_token=token,
    )
    if challenge is None:
        return jsonify(error="no pending registration challenge"), 400
    cred_json = body.get("credential")
    if not cred_json:
        return jsonify(error="missing credential"), 400

    try:
        verification = verify_registration_response(
            credential=cred_json,
            expected_challenge=challenge,
            expected_rp_id=_rp_id(),
            expected_origin=_origin(),
            require_user_verification=True,
        )
    except InvalidRegistrationResponse as exc:
        auth.audit("auth.enroll_failed", str(exc))
        return jsonify(error="registration verification failed"), 403

    label = (body.get("label") or "").strip()[:60] or "passkey"
    transports = ",".join(json.loads(cred_json).get("response", {})
                          .get("transports", []) or [])
    enrolled = auth.enroll_credential(
        token=token,
        credential_id=verification.credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        transports=transports,
        label=label,
        clock=time.time,
    )
    if not enrolled:
        return jsonify(error="invalid or expired enrollment token"), 403
    auth.audit("auth.enroll", label)
    # The device just proved presence + verification — log it straight in.
    session_token = auth.create_session()
    resp = jsonify(ok=True)
    return auth.set_session_cookie(resp, session_token)
