"""Shared code-owned catalogs and installation configuration.

The deterministic CLI and adapter runtime consume the same definitions. This
module opens no database and does not import command handlers.
"""

import json
import math
import os


# §2a radar: the 7 fixed axes and the canonical muscle→group rollup map.
# Volume for a muscle name not in this map is surfaced in `unmapped`, never
# guessed into a group — extend the map deliberately instead (Hevy's template
# tags land here with §5b). Keys are matched lowercased/stripped.
MUSCLE_GROUP_AXES = ["Chest", "Back", "Arms", "Shoulders", "Legs", "Core", "Glutes"]

MUSCLE_TO_GROUP = {
    "chest": "Chest", "pecs": "Chest",
    "back": "Back", "back/lats": "Back", "lats": "Back", "upper back": "Back",
    "upper_back": "Back", "upper back/posture": "Back", "traps": "Back",
    "rhomboids": "Back",
    "biceps": "Arms", "triceps": "Arms", "forearms": "Arms",
    "grip/forearms": "Arms",
    "shoulders": "Shoulders", "delts": "Shoulders", "front delts": "Shoulders",
    "side delts": "Shoulders", "rear delts": "Shoulders",
    "quads": "Legs", "quadriceps": "Legs", "hamstrings": "Legs",
    "calves": "Legs", "adductors": "Legs", "tibialis": "Legs",
    "core": "Core", "abs": "Core", "abdominals": "Core", "obliques": "Core",
    "core/abs": "Core",
    # Erectors count as posterior core in this system (the quarterly protocol
    # tests back extension as posterior core), not as "Back".
    "lower back": "Core", "lower_back": "Core", "lower back/erectors": "Core",
    "glutes": "Glutes", "glute max": "Glutes", "glute med": "Glutes",
    "glute med/abductors": "Glutes", "abductors": "Glutes",
}

KIND_BETTER = {"strength": "higher", "hold": "higher", "distance": "higher",
               "control": "higher", "timed": "lower", "rom": "higher",
               "binary": "higher"}

def _ft(name, kind, group=None, ref=None, unilateral=0, priority=0):
    return {"name": name, "kind": kind, "unilateral": unilateral,
            "group": group, "ref": ref, "priority": priority}

CATALOG = {
    # Session 1 — lower (strength, unilateral)
    "leg-extension":  _ft("Seated leg extension", "strength", "Legs", "#1-2", 1, 1),
    "leg-curl":       _ft("Seated/lying leg curl", "strength", "Legs", "#3-4", 1, 1),
    "hip-extension":  _ft("Cable/machine hip extension", "strength", "Glutes", "#5-6", 1, 1),
    "hip-flexion":    _ft("Cable hip flexion", "strength", "Legs", "#7-8", 1, 1),
    "hip-abduction":  _ft("Hip abduction", "strength", "Glutes", "#9-10", 1, 1),
    "hip-adduction":  _ft("Hip adduction", "strength", "Legs", "#11-12", 1, 0),
    "calf-raise":     _ft("Single-leg standing calf raise", "strength", "Legs", "#13-14", 1, 1),
    "tibialis-raise": _ft("Tibialis raise / banded dorsiflexion", "strength", "Legs", "#15-16", 1, 0),
    "balance-stand":  _ft("Single-leg balance, eyes closed", "hold", "Legs", "#17-18", 1, 1),
    # Session 2 — upper (strength, unilateral)
    "shoulder-er":    _ft("Cable/DB shoulder external rotation", "strength", "Shoulders", "#19-20", 1, 1),
    "shoulder-ir":    _ft("Cable/DB shoulder internal rotation", "strength", "Shoulders", "#21-22", 1, 1),
    "lateral-raise":  _ft("DB lateral raise, single-arm", "strength", "Shoulders", "#23-24", 1, 1),
    "rear-delt-fly":  _ft("Rear-delt reverse fly, single-arm", "strength", "Shoulders", "#25-26", 1, 1),
    "front-raise":    _ft("Front raise, single-arm", "strength", "Shoulders", "#27-28", 1, 0),
    "chest-fly":      _ft("Cable chest fly / single-arm press", "strength", "Chest", "#29-30", 1, 1),
    "cable-row":      _ft("Single-arm cable row", "strength", "Back", "#31-32", 1, 1),
    "lat-pulldown":   _ft("Single-arm lat pulldown", "strength", "Back", "P1-P2", 1, 1),
    "face-pull":      _ft("Face pull / row-to-upper-chest", "strength", "Back", "P3-P4", 1, 1),
    "lower-trap-raise": _ft("Prone lower-trap raise (Y-raise)", "strength", "Back", "P5-P6", 1, 1),
    # Session 3 — arms / core / grip
    "biceps-curl":     _ft("Biceps curl, single-arm", "strength", "Arms", "#33-34", 1, 1),
    "triceps-extension": _ft("Cable triceps extension, single-arm", "strength", "Arms", "#35-36", 1, 1),
    "wrist-flexion":   _ft("Wrist flexion", "strength", "Arms", "#37-38", 1, 0),
    "wrist-extension": _ft("Wrist extension", "strength", "Arms", "#39-40", 1, 0),
    "cable-crunch":    _ft("Cable crunch / weighted trunk flexion", "strength", "Core", "#41", 0, 1),
    "back-extension":  _ft("Weighted back extension", "strength", "Core", "#42", 0, 1),
    "pallof-press":    _ft("Pallof press anti-rotation hold", "hold", "Core", "#43-44", 1, 0),
    "neck-flexion":    _ft("Neck flexion strength", "strength", None, "#46", 0, 0),
    "neck-extension":  _ft("Neck extension strength", "strength", None, "#47", 0, 0),
    "grip-hold":       _ft("Grip / farmer hold, single-hand", "hold", "Arms", "#49-50", 1, 0),
    # Stabilizer & endurance battery (hold=time; control=1–3 quality)
    "mcgill-flexor":       _ft("McGill trunk-flexor endurance hold", "hold", "Core", "S1", 0, 1),
    "mcgill-extensor":     _ft("Biering-Sørensen extensor endurance hold", "hold", "Core", "S2", 0, 1),
    "mcgill-side-bridge":  _ft("Side-bridge endurance (= timed side plank #45)", "hold", "Core", "S3-S4", 1, 1),
    "neck-flexor-endurance": _ft("Deep neck-flexor endurance hold", "hold", None, "S5", 0, 1),
    "single-leg-control":  _ft("Single-leg-stance / Trendelenburg quality", "control", "Glutes", "S6-S7", 1, 0),
    "scap-control":        _ft("Serratus / scapular control quality", "control", "Shoulders", "S8", 0, 0),
    # Athletic-radar field tests (not in the isolation battery)
    "run-3k":        _ft("3 km benchmark run", "timed", None, "athletic", 0, 0),
    "sprint-30m":    _ft("30 m sprint", "timed", None, "athletic", 0, 0),
    "sit-and-reach": _ft("Sit-and-reach", "distance", None, "athletic", 0, 0),
    # Phase 4 mobility (design rule 5) — per-side ROM/length screens. priority=0:
    # they never enter the isolation-battery quarterly coverage count. Cited norms
    # live in MOBILITY_NORM (like RATIO_SEED), not on the catalog entry.
    "thomas":              _ft("Thomas test — hip-flexor length (pass = thigh to horizontal)", "binary", "Legs", "mobility", 1, 0),
    "ankle-df-wall":       _ft("Ankle dorsiflexion — knee-to-wall (weight-bearing lunge)", "rom", "Legs", "mobility", 1, 0),
    "shoulder-flexion-rom": _ft("Shoulder flexion (forward elevation) ROM", "rom", "Shoulders", "mobility", 1, 0),
    "shoulder-er-rom":     _ft("Shoulder external-rotation ROM (at 90° abduction)", "rom", "Shoulders", "mobility", 1, 0),
}

# §3c athletic radar: axis -> which test feeds it. Targets are USER config
# (athletic_targets table), not seeded — norms are too protocol-variable to
# assert as cited. insufficient_data until a target is set AND a test logged.
#   agg: how the axis reduces its test result(s):
#     'e1rm_lift'  Strength — best e1RM per configured lift (Hevy working sets)
#     'min_side'   Balance — weakest side (also feeds §3a left/right)
#     'latest'     single latest value
ATHLETIC_AXES = {
    "strength":    {"test": None,            "agg": "e1rm_lift"},
    "endurance":   {"test": "run-3k",        "agg": "latest"},
    "speed":       {"test": "sprint-30m",    "agg": "latest"},
    "balance":     {"test": "balance-stand", "agg": "min_side"},
    "flexibility": {"test": "sit-and-reach", "agg": "latest"},
}

ATHLETIC_STALE_DAYS = 120   # quarterly cadence + grace; score kept but flagged

# §3d strength ratios — TESTED view. Each pair's target is cited in the
# bundled public configuration. `band` = [lo, hi] for the in-range flag (null = open end);
# `ideal` is the display anchor. Ratios are numerator:denominator of e1RM
# (strength kind) or hold seconds (endurance kind, McGill). Values that are
# grip-/position-dependent carry a note. NEVER flag a compound-derived number
# against these — tested view only.
RATIO_SEED = [
    {"key": "hq", "kind": "strength", "num": "leg-curl", "den": "leg-extension",
     "num_label": "Hamstring", "den_label": "Quadriceps", "per_side": True,
     "ideal": 0.60, "band": [0.55, 0.65], "evidence": "strong",
     "method": "seated leg curl : leg extension 6–8RM e1RM, per side",
     "cite": "Aagaard 1998 Am J Sports Med; Grygorowicz 2010 Biol Sport 27(1):47–51"},
    {"key": "elbow", "kind": "strength", "num": "biceps-curl", "den": "triceps-extension",
     "num_label": "Elbow flexors", "den_label": "Elbow extensors", "per_side": True,
     "ideal": 1.19, "band": [1.01, 1.37], "evidence": "moderate",
     "method": "biceps curl (SUPINATED grip) : triceps extension e1RM, per side",
     "cite": "Lategan & Krüger 2007, SA J Res Sport Phys Educ Recreat 29(2):67–74 (supinated ≈1.19)"},
    {"key": "reardelt", "kind": "strength", "num": "rear-delt-fly", "den": "front-raise",
     "num_label": "Rear delt", "den_label": "Front delt", "per_side": True,
     "ideal": 0.50, "band": [0.40, 0.60], "evidence": "moderate",
     "method": "reverse fly : front raise e1RM — directional proxy for horiz. abd:add",
     "cite": "Ivey 1985 Arch Phys Med Rehabil 66(6):384–386 (add:abd ≈2:1 → rear:front ≈0.5)"},
    {"key": "erir", "kind": "strength", "num": "shoulder-er", "den": "shoulder-ir",
     "num_label": "External rot.", "den_label": "Internal rot.", "per_side": True,
     "ideal": 0.66, "band": [0.66, None], "evidence": "moderate",
     "method": "cable ER : IR e1RM, per side (≥0.66 is the target floor)",
     "cite": "IJSPT 2021 art.22162 (overhead-athlete review, ER:IR ≈0.66)"},
    {"key": "ankle", "kind": "strength", "num": "tibialis-raise", "den": "calf-raise",
     "num_label": "Dorsiflexors", "den_label": "Plantarflexors", "per_side": True,
     "ideal": None, "band": None, "evidence": "tracking",
     "method": "tibialis raise : single-leg calf raise e1RM, per side — trend-only",
     "cite": "Quarterly protocol tracking pair; the dynamometry ratio in Hussain & Frey-Law 2016 is not a target for unlike gym-test modalities"},
    # TREND-ONLY contract: the IJSPT 2024 population SD is
    # ±0.61 around a 1.36 mean — far too wide to support a target band or an
    # out-of-range flag. ideal/band None → the ratio is shown and trended,
    # never flagged. The >15% between-limb gap flag still applies (that
    # threshold is Grygorowicz-validated, independent of the ratio target).
    {"key": "hip", "kind": "strength", "num": "hip-flexion", "den": "hip-extension",
     "num_label": "Hip flexors", "den_label": "Hip extensors", "per_side": True,
     "ideal": None, "band": None, "evidence": "moderate",
     "method": "standing cable hip flexion : extension e1RM, per side — trend-only (population SD ±0.61 too wide for a target)",
     "cite": "IJSPT 2024 art.124117 Table 3 (standing, male mean 1.36±0.61 — context, not a target)"},
    {"key": "hip-abadd", "kind": "strength", "num": "hip-abduction", "den": "hip-adduction",
     "num_label": "Hip abductors", "den_label": "Hip adductors", "per_side": True,
     "ideal": None, "band": None, "evidence": "tracking",
     "method": "hip abduction : adduction e1RM, per side — trend-only",
     "cite": "Quarterly protocol tracking pair; no diagnostic target band asserted"},
    {"key": "pushpull-isolation", "kind": "strength", "num": "chest-fly", "den": "cable-row",
     "num_label": "Horizontal push", "den_label": "Horizontal pull", "per_side": True,
     "ideal": None, "band": None, "evidence": "tracking",
     "method": "single-arm chest fly/press : cable row e1RM, per side — trend-only",
     "cite": "Quarterly protocol tracking pair; exercise-specific loads are not interchangeable norms"},
    {"key": "wrist", "kind": "strength", "num": "wrist-flexion", "den": "wrist-extension",
     "num_label": "Wrist flexors", "den_label": "Wrist extensors", "per_side": True,
     "ideal": None, "band": None, "evidence": "tracking",
     "method": "wrist flexion : extension e1RM, per side — trend-only",
     "cite": "Quarterly protocol tracking pair; no diagnostic target band asserted"},
    {"key": "core-strength", "kind": "strength", "num": "cable-crunch", "den": "back-extension",
     "num_label": "Anterior core", "den_label": "Posterior core", "per_side": False,
     "ideal": None, "band": None, "evidence": "tracking",
     "method": "cable crunch : weighted back extension e1RM — trend-only",
     "cite": "Quarterly protocol tracking pair; no diagnostic target band asserted"},
    {"key": "mcgill-fe", "kind": "hold", "num": "mcgill-flexor", "den": "mcgill-extensor",
     "num_label": "Trunk flexors", "den_label": "Trunk extensors", "per_side": False,
     "ideal": 0.66, "band": [None, 1.00], "evidence": "strong",
     "method": "flexor : Sørensen extensor endurance hold (seconds) — >1.0 = extensor-weak red flag",
     "cite": "McGill 1999 (male mean flexor:extensor ≈0.66)"},
    {"key": "mcgill-sbe", "kind": "hold", "num": "mcgill-side-bridge", "den": "mcgill-extensor",
     "num_label": "Side bridge", "den_label": "Trunk extensors", "per_side": True,
     "ideal": 0.65, "band": [0.55, 0.75], "evidence": "strong",
     "method": "side-bridge : extensor endurance hold (seconds), per side",
     "cite": "McGill 1999 (male mean side-bridge:extensor ≈0.65)"},
]

# Medication grouping is deployment-owned configuration. The public default is
# intentionally neutral and cannot reveal or assume a user's treatment.
PRIMARY_MEDICATION = (
    os.environ.get("HERMES_PRIMARY_MEDICATION", "medication").strip().lower()
    or "medication"
)

MEDICATION_ALIASES = {PRIMARY_MEDICATION}

MEDICATION_ALIASES.update(
    value.strip().lower()
    for value in os.environ.get("HERMES_MEDICATION_ALIASES", "").split(",")
    if value.strip()
)

# hrv_ms (T55 owner flag 2): renamed from hrv_sdnn 2026-07-19 now that the
# Apple pipeline is retired by product contract — HRV in ms, but the
# algorithm still varies by source: apple-era rows (<=2026-05-31) = SDNN
# (HealthKit), fitbit rows = RMSSD (Google Health API daily HRV). Comparable
# only WITHIN a source — never average across sources (the DM_PRIMARY /
# readiness fitbit-preferred pick keeps one source dominant per metric, so
# those baselines stay semantics-safe). Legacy hrv_sdnn is never dropped;
# Migration 001 performs the sole compatibility copy into hrv_ms.
DM_METRICS = ("resting_hr", "hrv_ms", "hr_min", "hr_avg", "hr_max", "steps",
              "active_energy_kcal", "basal_energy_kcal", "exercise_min",
              "distance_km", "flights", "respiratory_rate", "spo2_pct",
              "walking_hr_avg", "sleep_hours")

# daily_metrics rows coexist per day keyed (date, source). Owner-decided merge
# (2026-07-07): per-metric primary with fallback — Fitbit for physiology,
# Apple for GPS distance; if the primary is null that day use the other source.
# Which source actually supplied values is tallied in coverage.dm_source_counts.
DM_PRIMARY = {m: "fitbit" for m in DM_METRICS}

DM_PRIMARY["distance_km"] = "apple"

# Exact provider values authorized for the Phase 3 running adapter.  Matching
# is normalized-label equality only; title substrings and punctuation guesses
# are deliberately excluded.
RUN_TYPE_KEYS = (
    "running", "outdoor running", "indoor running", "treadmill running",
)

# ---- §4a configurable nutrition micros -------------------------------------
# Aliases and units drive defensive import matching. Targets are installation
# configuration because appropriate values vary by person and jurisdiction;
# a fresh install assumes none.
MICRO_KEYS = {
    "vitamin_d", "magnesium", "omega3_epa_dha", "zinc", "iron",
    "vitamin_b12", "calcium", "potassium", "vitamin_c", "folate",
}

def _configured_micro_targets(*, micro_keys=None):
    if micro_keys is None:
        micro_keys = MICRO_KEYS
    raw = os.environ.get("HERMES_NUTRIENT_TARGETS_JSON", "").strip()
    if not raw:
        return {}
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("HERMES_NUTRIENT_TARGETS_JSON must be valid JSON") from exc
    if not isinstance(values, dict) or set(values) - micro_keys:
        raise RuntimeError("HERMES_NUTRIENT_TARGETS_JSON has unknown nutrient keys")
    configured = {}
    for key, value in values.items():
        if isinstance(value, bool):
            raise RuntimeError("configured nutrient targets must be positive numbers")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("configured nutrient targets must be positive numbers") from exc
        if not math.isfinite(number) or number <= 0:
            raise RuntimeError("configured nutrient targets must be positive numbers")
        configured[key] = number
    return configured

CONFIGURED_MICRO_TARGETS = _configured_micro_targets()

MICRO_SEED = [
    {"key": "vitamin_d", "name": "Vitamin D", "unit": "ug", "target": CONFIGURED_MICRO_TARGETS.get("vitamin_d"),
     "basis": "configured", "cite": "user-configured target",
     "aliases": ("vitamin d",), "units": {"ug": 1.0, "µg": 1.0, "mcg": 1.0, "iu": 0.025}},
    {"key": "magnesium", "name": "Magnesium", "unit": "mg", "target": CONFIGURED_MICRO_TARGETS.get("magnesium"),
     "basis": "configured", "cite": "user-configured target",
     "aliases": ("magnesium",), "units": {"mg": 1.0}},
    {"key": "omega3_epa_dha", "name": "Omega-3 (EPA+DHA)", "unit": "mg", "target": CONFIGURED_MICRO_TARGETS.get("omega3_epa_dha"),
     "basis": "configured", "cite": "user-configured target",
     "aliases": (), "units": {"mg": 1.0, "g": 1000.0}},   # filled from EPA+DHA columns
    {"key": "zinc", "name": "Zinc", "unit": "mg", "target": CONFIGURED_MICRO_TARGETS.get("zinc"),
     "basis": "configured", "cite": "user-configured target",
     "aliases": ("zinc",), "units": {"mg": 1.0}},
    {"key": "iron", "name": "Iron", "unit": "mg", "target": CONFIGURED_MICRO_TARGETS.get("iron"),
     "basis": "configured", "cite": "user-configured target",
     "aliases": ("iron",), "units": {"mg": 1.0}},
    {"key": "vitamin_b12", "name": "Vitamin B12", "unit": "ug", "target": CONFIGURED_MICRO_TARGETS.get("vitamin_b12"),
     "basis": "configured", "cite": "user-configured target",
     "aliases": ("vitamin b12", "b12"), "units": {"ug": 1.0, "µg": 1.0, "mcg": 1.0}},
    {"key": "calcium", "name": "Calcium", "unit": "mg", "target": CONFIGURED_MICRO_TARGETS.get("calcium"),
     "basis": "configured", "cite": "user-configured target",
     "aliases": ("calcium",), "units": {"mg": 1.0, "g": 1000.0}},
    {"key": "potassium", "name": "Potassium", "unit": "mg", "target": CONFIGURED_MICRO_TARGETS.get("potassium"),
     "basis": "configured", "cite": "user-configured target",
     "aliases": ("potassium",), "units": {"mg": 1.0, "g": 1000.0}},
    {"key": "vitamin_c", "name": "Vitamin C", "unit": "mg", "target": CONFIGURED_MICRO_TARGETS.get("vitamin_c"),
     "basis": "configured", "cite": "user-configured target",
     "aliases": ("vitamin c",), "units": {"mg": 1.0}},
    {"key": "folate", "name": "Folate", "unit": "ug", "target": CONFIGURED_MICRO_TARGETS.get("folate"),
     "basis": "configured", "cite": "user-configured target",
     "aliases": ("folate", "folate dfe"), "units": {"ug": 1.0, "µg": 1.0, "mcg": 1.0}},
]

# cited provocation/pattern self-tests. name = the discriminating feature;
# cite = where the feature/protocol is described. NOT diagnostic in isolation.
SELF_TEST_CATALOG = {
    "decline-squat-pain": {
        "name": "Single-leg decline squat reproduces localised inferior-pole pain",
        "cite": "Rio 2015 BJSM; Malliaras 2015 JOSPT (patellar tendon load test)"},
    "prolonged-sitting-pain": {
        "name": "Pain after prolonged knee flexion / sitting (‘theatre sign’)",
        "cite": "Crossley 2016 BJSM PFP consensus"},
    "repeated-extension-preference": {
        "name": "Repeated lumbar extension centralises/eases symptoms (directional preference)",
        "cite": "Long 2004 Spine (directional preference RCT)"},
    "single-leg-stance-30s": {
        "name": "30s single-leg stance reproduces lateral hip pain",
        "cite": "Grimaldi 2017 BJSM (gluteal tendinopathy)"},
    "sitting-hamstring-load": {
        "name": "Deep-hip-flexion / sitting reproduces proximal hamstring pain",
        "cite": "Goom 2016 JOSPT (proximal hamstring tendinopathy)"},
    "painful-arc": {
        "name": "Painful arc ~60–120° abduction",
        "cite": "Hegedus 2012 BJSM (shoulder special-test review — modest accuracy)"},
    "passive-er-loss": {
        "name": "Marked loss of PASSIVE external rotation",
        "cite": "Kelley 2013 JOSPT frozen-shoulder CPG"},
}

# cited first-line LOADING drills. dose = a starting protocol; cite = source.
REHAB_CATALOG = {
    "patellar-isometrics": {
        "name": "Heavy isometric holds (analgesic)", "dose": "5×45s, ~70% MVIC",
        "cite": "Rio 2015 BJSM (isometric analgesia)"},
    "patellar-hsr": {
        "name": "Heavy-slow-resistance knee-extension loading", "dose": "3–4 sets, 6–15RM, slow tempo, 3×/wk",
        "cite": "Kongsgaard 2009 Scand J Med Sci Sports"},
    "pfp-hip-knee-loading": {
        "name": "Hip-abductor + quad progressive loading", "dose": "progressive, 6–12 wk",
        "cite": "Barton 2015 BJSM (best-practice guide); Lack 2015 BJSM (proximal-muscle-rehab meta-analysis)"},
    "mckenzie-directional": {
        "name": "Directional-preference (repeated-movement) exercise", "dose": "into the easing direction, frequent low-load reps",
        "cite": "Long 2004 Spine"},
    "mcgill-big3": {
        "name": "McGill big-3 (curl-up, side-bridge, bird-dog)", "dose": "isometric, reps-in-reserve, daily",
        "cite": "McGill 2007 (Low Back Disorders)"},
    "glute-abductor-load": {
        "name": "Progressive abductor loading, avoid compression/crossing", "dose": "progressive, education + load, 8–12 wk",
        "cite": "Mellor 2018 BMJ (LEAP trial)"},
    "hamstring-progressive-load": {
        "name": "Progressive proximal-hamstring loading", "dose": "isometric → heavy load → energy storage",
        "cite": "Goom 2016 JOSPT"},
    "rotator-cuff-scapular-load": {
        "name": "Progressive rotator-cuff + scapular loading", "dose": "progressive resisted, 12 wk",
        "cite": "Ketola 2013 (exercise ≈ acromioplasty at 5 yr); CSAW/Beard 2018 Lancet (placebo-surgery anchor)"},
}

# pain region -> {svg: base ids it colors (MUSCLE or JOINT bases — the pain
# lens may color joints, design rule 3), causes: cited candidates}. Each cause's
# `tests` say what result of each discriminating self-test points TOWARD it
# (expect); the opposite result points against. `drills` are cited first-line
# loads. v1 seeds the user's known regions (knee/low-back/glute/hamstring/
# shoulder); a region absent here logs pain fine but the loop says so.
PAIN_CAUSE_MAP = {
    "anterior-knee": {
        "svg": ("knee",),   # joint region (FIGURE_NON_MUSCLE) — pain-lens colored
        "causes": [
            {"cause": "Patellofemoral pain",
             "cite": "Crossley 2016 BJSM; Lack 2015 BJSM",
             "tests": [{"key": "prolonged-sitting-pain", "expect": "positive"},
                       {"key": "decline-squat-pain", "expect": "negative"}],
             "drills": ["pfp-hip-knee-loading"]},
            {"cause": "Patellar tendinopathy",
             "cite": "Rio 2015 BJSM; Malliaras 2015 JOSPT",
             "tests": [{"key": "decline-squat-pain", "expect": "positive"}],
             "drills": ["patellar-isometrics", "patellar-hsr"]},
        ]},
    "low-back": {
        "svg": ("lower-back-erectors", "lower-back-ql"),   # muscle (posterior core)
        "causes": [
            {"cause": "Mechanical low-back pain (directional preference)",
             "cite": "Long 2004 Spine; McGill 2007",
             "tests": [{"key": "repeated-extension-preference", "expect": "positive"}],
             "drills": ["mckenzie-directional", "mcgill-big3"]},
        ]},
    "lateral-hip": {
        "svg": ("gluteus-medius",),
        "causes": [
            {"cause": "Gluteal tendinopathy",
             "cite": "Grimaldi 2017 BJSM; Mellor 2018 BMJ (LEAP)",
             "tests": [{"key": "single-leg-stance-30s", "expect": "positive"}],
             "drills": ["glute-abductor-load"]},
        ]},
    "hamstring": {
        "svg": ("hamstrings-medial", "hamstrings-lateral"),
        "causes": [
            {"cause": "Proximal hamstring tendinopathy",
             "cite": "Goom 2016 JOSPT",
             "tests": [{"key": "sitting-hamstring-load", "expect": "positive"}],
             "drills": ["hamstring-progressive-load"]},
        ]},
    "shoulder": {
        "svg": ("deltoid-rear", "shoulder-front", "shoulder-side"),
        "causes": [
            {"cause": "Rotator-cuff-related (subacromial) shoulder pain",
             "cite": "Hegedus 2012 BJSM; Ketola 2013",
             "tests": [{"key": "painful-arc", "expect": "positive"},
                       {"key": "passive-er-loss", "expect": "negative"}],
             "drills": ["rotator-cuff-scapular-load"]},
            {"cause": "Frozen shoulder (adhesive capsulitis)",
             "cite": "Kelley 2013 JOSPT CPG",
             "tests": [{"key": "passive-er-loss", "expect": "positive"}],
             "drills": []},   # loading is not first-line — honest empty, no push
        ]},
}

_sit_reach_raw = os.environ.get("HERMES_SIT_REACH_NORMAL_CM", "").strip()
try:
    SIT_REACH_NORMAL_CM = float(_sit_reach_raw) if _sit_reach_raw else None
except ValueError as exc:
    raise RuntimeError("HERMES_SIT_REACH_NORMAL_CM must be a number") from exc

# movement slug -> cited norm. `normal_at` is the cutoff (better=higher for all
# five — more range / a pass is better): value >= normal_at is `normal`, below is
# `restricted`; a None cutoff is an honest `untested` (never a fabricated band).
# `svg` = the drawn muscle regions the test's tissue maps to. `unit` is display
# only (deg/cm/pass-fail). `flexibility_axis` marks the test the Athletic radar's
# Flexibility axis ALSO consumes (design rule 6 — reused, not forked). `caveat`
# ships verbatim (each screen's honest limits).
MOBILITY_NORM = {
    "thomas": {
        "label": "Thomas test — hip-flexor length",
        "svg": ("hip-flexor",), "unit": "pass-fail", "better": "higher",
        "normal_at": 1,   # passed==1 → normal; a positive test (0) = restricted
        "cite": "Harvey 1998 BJSM 32(1):68–70; Kendall (Muscles: Testing & Function)",
        "caveat": "screen only — not valid for precise hip-extension ROM unless "
                  "pelvic tilt is controlled (Vigotsky 2016 PeerJ)"},
    "ankle-df-wall": {
        "label": "Ankle dorsiflexion — knee-to-wall",
        "svg": ("calves-soleus", "calves-gastroc-medial", "calves-gastroc-lateral"),
        "unit": "deg", "better": "higher", "normal_at": 30,
        "cite": "Searle 2018 J Foot Ankle Res 11:62 (<30° cutoff); Bennell 1998 "
                "Aust J Physiother 44(3):175–180 (method reliability)",
        "caveat": "the 30° cutoff was validated in older/diabetic adults — a "
                  "screen, not a precise line for a healthy adult"},
    "shoulder-flexion-rom": {
        "label": "Shoulder flexion (forward elevation) ROM",
        "svg": ("shoulder-front", "shoulder-side"),
        "unit": "deg", "better": "higher", "normal_at": 150,
        # AAOS/Norkin define the NORMAL (180°); the <150° flag is OUR chosen
        # threshold below normal, not a value AAOS attributes (owner honesty fix).
        "cite": "normal 180° flexion per AAOS Joint Motion / Norkin & White, "
                "Measurement of Joint Motion 5th ed. 2016; the <150° restriction "
                "flag is a CHOSEN threshold below normal, not an AAOS value",
        "caveat": "active vs passive elevation differ; norms decline with age"},
    "shoulder-er-rom": {
        "label": "Shoulder external-rotation ROM",
        "svg": ("deltoid-rear",),   # external rotators shown on the rear-delt region
        "unit": "deg", "better": "higher", "normal_at": 70,
        "cite": "normal 90° ER (at 90° abduction) per AAOS Joint Motion / Norkin "
                "& White 2016; the <70° restriction flag is a CHOSEN threshold "
                "below normal, not an AAOS value",
        "caveat": "measure at 90° abduction — ER norms depend heavily on arm position"},
    "sit-and-reach": {
        "label": "Sit-and-reach — posterior-chain flexibility",
        "svg": ("hamstrings-medial", "hamstrings-lateral", "lower-back-erectors"),
        "unit": "cm", "better": "higher", "normal_at": SIT_REACH_NORMAL_CM,
        "flexibility_axis": True,
        "cite": "installation-configured comparison threshold",
        "caveat": "No demographic threshold is assumed by default. Configure a "
                  "comparison appropriate to the user's protocol; different box "
                  "or floor protocols are not comparable."},
}

# Mobility drills: posture/ROM work, NOT loaded strength volume — they must
# never seed volume rows. Titles are generic examples; installations can
# provide their own map.
MOBILITY_EXERCISES = {"sample mobility drill a", "sample mobility drill b"}

# Performance tests with Hevy sets that are measurements, not strength-volume
# work. They must not inflate the activation map or weekly-volume radar merely
# because Hevy assigns broad muscle tags to the custom exercise.
NON_VOLUME_EXERCISES = {"Quarterly Test — Single-Leg Balance (Eyes Closed)"}

# unit folding: report ('10⁹/L', '× 10⁻³ IU/L', 'μmol/L') and catalog
# ('10^9/L', '10^-3 IU/L', 'µmol/L') are compared through the SAME normaliser
# so ASCII vs unicode never causes a spurious mismatch.
_SUP = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6",
        "⁷": "7", "⁸": "8", "⁹": "9", "⁻": "-"}

# Micro targets use the configured values on MICRO_SEED. Fibre has no importer
# alias and therefore remains explicitly unconfigured.
MICRO_TARGET_EXTRA = [
    {"key": "fibre", "name": "Fibre", "unit": "g", "target": None,
     "cite": "no target emitted — nutrient absent from the Cronometer import "
             "(no MICRO_SEED alias), and a target nothing can score against "
             "would be fabrication",
     "note": "not in Cronometer export"},
]
