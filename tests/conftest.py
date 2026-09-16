import os

os.environ.setdefault("HERMES_TIMEZONE", "Europe/Paris")
os.environ.setdefault("HERMES_PRIMARY_MEDICATION", "medication")
os.environ.setdefault(
    "HERMES_MEDICATION_ALIASES", "medication-alias,medication-alias-2"
)
os.environ.setdefault(
    "HERMES_NUTRIENT_TARGETS_JSON",
    '{"vitamin_d":12,"magnesium":300,"omega3_epa_dha":200,'
    '"zinc":10,"iron":12,"vitamin_b12":3,"calcium":800,'
    '"potassium":3000,"vitamin_c":90,"folate":300}',
)
os.environ.setdefault("HERMES_SIT_REACH_NORMAL_CM", "22")
os.environ.setdefault(
    "HERMES_PHASE_OFFSETS_JSON", '{"cut":-300,"maintain":0,"bulk":300}'
)
os.environ.setdefault(
    "HERMES_PROTEIN_PER_KG_JSON", '{"cut":1.8,"maintain":1.5,"bulk":1.6}'
)
os.environ.setdefault("HERMES_WATER_FALLBACK_ML", "1800")
os.environ.setdefault("HERMES_WATER_ML_PER_KG", "30")
os.environ.setdefault("HERMES_WATER_ML_PER_EXERCISE_HOUR", "400")
os.environ.setdefault("HERMES_WATER_HOT_DAY_BONUS_ML", "250")
os.environ.setdefault("HERMES_WATER_HOT_DAY_TEMP_C", "28")

import pytest

from app import auth as auth_mod
from app import create_app
from tests.support import csrf_from


@pytest.fixture()
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,   # re-enabled in the dedicated CSRF test
        "RATELIMIT_ENABLED": False,  # limiter storage is process-wide; keep tests independent
        "PANEL_DB": str(tmp_path / "panel.db"),
        "PANEL_COOKIE_SECURE": False,
    })


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def authed(app, client):
    """Client with a valid server-side session (bypasses the WebAuthn ceremony;
    the full ceremony is covered by tests/test_passkey_roundtrip.py)."""
    with app.app_context():
        token = auth_mod.create_session()
    client.set_cookie(auth_mod.SESSION_COOKIE, token)
    return client
