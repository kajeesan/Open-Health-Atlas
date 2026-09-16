"""Deterministic, fictional deployment settings for toolkit tests."""

import os
from pathlib import Path
import sys


TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
if str(TOOLKIT_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLKIT_ROOT))
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
os.environ.setdefault(
    "HERMES_HEVY_QUARTERLY_CONFIG",
    str(TOOLKIT_ROOT.parent / "config" / "hevy-quarterly.example.json"),
)
