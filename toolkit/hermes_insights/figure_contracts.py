"""Exact vendored SVG vocabulary and retained lens display conventions."""

# Display mapping from the engine's two muscle vocabularies onto the 89 region
# ids of the vendored body-muscles SVG (app/static/vendor/body-muscles/,
# sha-pinned, consumed read-only for its path data). This is an ANATOMICAL
# DISPLAY mapping — definitional, not a research claim — documented in
# docs/muscle-figure-map.md. Deep or undrawn muscles render on the overlying
# drawn region flagged approx=True (e.g. glute min → the glute-med region);
# never silently, never invented. Region ids are stored side-less here and
# expanded to -left/-right at build time (every muscle region in the SVG is
# sided; central non-muscle ids live in FIGURE_NON_MUSCLE).

# svg base id -> the 7-axis group it belongs to for scoping/drill-down.
# lower-back rows count as Core to match MUSCLE_TO_GROUP ("erectors are
# posterior core in this system").
_FIG_BASE_GROUP = {
    "chest-upper": "Chest", "chest-lower": "Chest",
    "lats-upper": "Back", "lats-mid": "Back", "lats-lower": "Back",
    "traps-upper": "Back", "traps-mid": "Back", "traps-lower": "Back",
    "lower-back-erectors": "Core", "lower-back-ql": "Core",
    "abs-upper": "Core", "abs-lower": "Core", "obliques": "Core",
    "shoulder-front": "Shoulders", "shoulder-side": "Shoulders",
    "deltoid-rear": "Shoulders", "serratus-anterior": "Shoulders",
    "biceps": "Arms", "triceps-long": "Arms", "triceps-lateral": "Arms",
    "forearm": "Arms", "forearm-flexors": "Arms", "forearm-extensors": "Arms",
    "quads": "Legs", "hamstrings-medial": "Legs", "hamstrings-lateral": "Legs",
    "adductors": "Legs", "calves-gastroc-medial": "Legs",
    "calves-gastroc-lateral": "Legs", "calves-soleus": "Legs",
    "tibialis-anterior": "Legs", "hip-flexor": "Legs",
    "gluteus-maximus": "Glutes", "gluteus-medius": "Glutes",
}

FIGURE_REGION_GROUP = {f"{b}-{s}": g for b, g in _FIG_BASE_GROUP.items()
                       for s in ("left", "right")}

# These regions are neutral except for joints explicitly projected by the pain lens.
FIGURE_NON_MUSCLE = [
    "head", "face", "neck-left", "neck-right", "head-back", "nape", "spine",
    "elbow-left", "elbow-right", "hand-left", "hand-right",
    "hand-back-left", "hand-back-right", "knee-left", "knee-right",
    "knee-back-left", "knee-back-right", "foot-left", "foot-right",
    "foot-back-left", "foot-back-right",
]

# authored sub_region (docs/authored-submuscle-map.md, matched lowercased) ->
# (svg base ids, display_approx). display_approx=True = the muscle has no
# drawn region of its own and is shown on the overlying/nearest drawn region.
FIGURE_SUB_SVG = {
    "quads": (("quads",), False),
    "hamstrings": (("hamstrings-medial", "hamstrings-lateral"), False),
    "adductor magnus": (("adductors",), False),
    "hip flexors": (("hip-flexor",), False),   # doc: "hip flexors (iliopsoas)"
    "gastrocnemius": (("calves-gastroc-medial", "calves-gastroc-lateral"), False),
    "soleus": (("calves-soleus",), False),
    "tibialis anterior": (("tibialis-anterior",), False),
    "glute max": (("gluteus-maximus",), False),   # incl. "(upper fibres)" rows
    "glute med": (("gluteus-medius",), False),
    "glute min": (("gluteus-medius",), True),          # deep to glute med
    "tfl": (("hip-flexor",), True),                    # no TFL region drawn
    "erector spinae": (("lower-back-erectors",), False),
    "rectus abdominis": (("abs-upper", "abs-lower"), False),
    "obliques": (("obliques",), False),
    "transverse abdominis": (("abs-lower",), True),    # deep abdominal wall
    "lats": (("lats-upper", "lats-mid", "lats-lower"), False),
    "upper traps": (("traps-upper",), False),
    "mid traps": (("traps-mid",), False),
    "lower traps": (("traps-lower",), False),
    "rhomboids": (("traps-mid",), True),               # deep to mid traps
    "teres major": (("lats-upper",), True),            # no drawn region
    "front delt": (("shoulder-front",), False),
    "side delt": (("shoulder-side",), False),
    "rear delt": (("deltoid-rear",), False),
    "serratus anterior": (("serratus-anterior",), False),
    # doc: "external rotators (infraspinatus/teres minor)" — no drawn region
    "external rotators": (("deltoid-rear",), True),
    "pec clavicular": (("chest-upper",), False),
    "pec sternal": (("chest-lower",), False),
    "biceps": (("biceps",), False),
    "triceps": (("triceps-long", "triceps-lateral"), False),
    "brachialis": (("biceps",), True),                 # deep to biceps
    "brachioradialis": (("forearm",), True),           # front forearm mass
    "forearm flexors": (("forearm-flexors",), False),
    "anconeus": (("triceps-lateral",), True),          # no drawn region
}

# coarse Hevy tag (MUSCLE_TO_GROUP keys) -> svg base ids, used only for
# coarse-fallback exercises; inherently approximate (basis='coarse').
FIGURE_COARSE_SVG = {
    "chest": ("chest-upper", "chest-lower"), "pecs": ("chest-upper", "chest-lower"),
    "back": ("lats-upper", "lats-mid", "lats-lower", "traps-mid", "traps-lower"),
    "back/lats": ("lats-upper", "lats-mid", "lats-lower"),
    "lats": ("lats-upper", "lats-mid", "lats-lower"),
    "upper back": ("traps-upper", "traps-mid"),
    "upper_back": ("traps-upper", "traps-mid"),
    "upper back/posture": ("traps-upper", "traps-mid"),
    "traps": ("traps-upper", "traps-mid", "traps-lower"),
    "rhomboids": ("traps-mid",),
    "biceps": ("biceps",),
    "triceps": ("triceps-long", "triceps-lateral"),
    "forearms": ("forearm", "forearm-flexors", "forearm-extensors"),
    "grip/forearms": ("forearm", "forearm-flexors", "forearm-extensors"),
    "shoulders": ("shoulder-front", "shoulder-side", "deltoid-rear"),
    "delts": ("shoulder-front", "shoulder-side", "deltoid-rear"),
    "front delts": ("shoulder-front",), "side delts": ("shoulder-side",),
    "rear delts": ("deltoid-rear",),
    "quads": ("quads",), "quadriceps": ("quads",),
    "hamstrings": ("hamstrings-medial", "hamstrings-lateral"),
    "calves": ("calves-gastroc-medial", "calves-gastroc-lateral", "calves-soleus"),
    "adductors": ("adductors",), "tibialis": ("tibialis-anterior",),
    "core": ("abs-upper", "abs-lower", "obliques"),
    "core/abs": ("abs-upper", "abs-lower", "obliques"),
    "abs": ("abs-upper", "abs-lower"), "abdominals": ("abs-upper", "abs-lower"),
    "obliques": ("obliques",),
    "lower back": ("lower-back-erectors", "lower-back-ql"),
    "lower_back": ("lower-back-erectors", "lower-back-ql"),
    "lower back/erectors": ("lower-back-erectors", "lower-back-ql"),
    "glutes": ("gluteus-maximus", "gluteus-medius"),
    "glute max": ("gluteus-maximus",), "glute med": ("gluteus-medius",),
    "glute med/abductors": ("gluteus-medius",), "abductors": ("gluteus-medius",),
}

# Display bucketing for the activation lens (effective sets over the window →
# heat level 0–5). Buckets are half-open [lo, hi); the legend labels are
# DERIVED from the same edges so text and code cannot drift, and each label
# names the true boundary (review finding: "2–4" would misread a 4.0).
# Display convention only — the top bucket sits at the common ~10 weekly-set
# volume landmark; NOT a cited claim.
FIGURE_LEVEL_EDGES = (2, 4, 7, 10)

FIGURE_LEVEL_LEGEND = (
    [{"level": 0, "label": "0 sets"},
     {"level": 1, "label": f"<{FIGURE_LEVEL_EDGES[0]}"}]
    + [{"level": i + 2, "label": f"{lo}–{hi - 0.1:g}"}
       for i, (lo, hi) in enumerate(zip(FIGURE_LEVEL_EDGES, FIGURE_LEVEL_EDGES[1:]))]
    + [{"level": len(FIGURE_LEVEL_EDGES) + 1, "label": f"{FIGURE_LEVEL_EDGES[-1]}+"}]
)

# RATIO_SEED key -> the figure region bases its numerator/denominator muscles
# draw on. ANATOMICAL DISPLAY mapping, definitional like FIGURE_SUB_SVG
# (docs/muscle-figure-map.md); every judgement (band, flag, gap) comes only
# from RATIO_SEED + _tested_ratios(). An empty side is allowed ONLY with an
# `unrepresentable` reason — surfaced in the payload, never guessed onto a
# wrong region (the ER:IR internal rotators are subscapularis-deep; coloring
# the chest would be anatomically wrong). `approx` marks a side whose muscle
# is itself undrawn and shown on the nearest drawn region (ER external
# rotators → the rear-delt region, matching FIGURE_SUB_SVG).
FIGURE_RATIO_MAP = {
    "hq":       {"num": ("hamstrings-medial", "hamstrings-lateral"),
                 "den": ("quads",)},
    "elbow":    {"num": ("biceps",), "den": ("triceps-long", "triceps-lateral")},
    "reardelt": {"num": ("deltoid-rear",), "den": ("shoulder-front",)},
    "erir":     {"num": ("deltoid-rear",), "den": (), "approx": ("num",),
                 "unrepresentable": "internal rotators (subscapularis) have no "
                 "drawn region — an IR result cannot be colored and is "
                 "surfaced here instead"},
    "ankle":    {"num": ("tibialis-anterior",),
                 "den": ("calves-gastroc-medial", "calves-gastroc-lateral",
                         "calves-soleus")},
    "hip":      {"num": ("hip-flexor",), "den": ("gluteus-maximus",)},
    "hip-abadd": {"num": ("gluteus-medius",), "den": ("adductors",)},
    "pushpull-isolation": {
        "num": ("chest-upper", "chest-lower"),
        "den": ("lats-upper", "lats-mid", "traps-mid")},
    "wrist": {"num": ("forearm-flexors",), "den": ("forearm-extensors",)},
    "core-strength": {"num": ("abs-upper", "abs-lower"),
                      "den": ("lower-back-erectors",)},
    "mcgill-fe":  {"num": ("abs-upper", "abs-lower"),
                   "den": ("lower-back-erectors",)},
    "mcgill-sbe": {"num": ("obliques", "lower-back-ql"),
                   "den": ("lower-back-erectors",)},
}

# The four region statuses, worst first. deficient = the weak side of an
# out-of-band cited ratio (red means exactly this, nothing else); balanced =
# tested (green — in band, or the tested-not-weak partner of a flagged pair;
# the tooltip always shows the ratio + band); trend_only = the pair has no
# cited band, amber pattern signal (design rule 2026-07-19 — NOT grey, data
# exists); untested = the honest grey default until the isolation battery
# lands. The legend is served with the payload so labels can't drift from
# the statuses the engine emits.
FIGURE_BALANCE_LEGEND = [
    {"status": "deficient", "label": "weak link — worth focus"},
    {"status": "balanced", "label": "balanced / not the weak link"},
    {"status": "trend_only",
     "label": "measured trend only — no valid target range"},
    {"status": "untested", "label": "untested"},
]

_BALANCE_RANK = {"untested": 0, "balanced": 1, "trend_only": 2, "deficient": 3}

# Between-limb gap threshold (Grygorowicz 2010) — the same 0.15 literal
# _tested_ratios pins in side_gap_flag and strength_ratios reports as
# asymmetry_threshold; change all three together or the views disagree.
RATIO_GAP_THRESHOLD = 0.15

# Keep a quarterly screen visible beyond the general fitness window.
MOBILITY_STALE_DAYS = 180

# window so a quarterly screen doesn't silently vanish
MOBILITY_STATUSES = ("restricted", "normal", "untested")

FIGURE_MOBILITY_LEGEND = [
    {"status": "restricted", "label": "restricted — below the cited norm"},
    {"status": "normal", "label": "within the cited norm"},
    {"status": "untested", "label": "untested / no cited cutoff"},
]

# worst wins on a shared region (mirrors the balance lens rank)
_MOBILITY_RANK = {"untested": 0, "normal": 1, "restricted": 2}

FLEX_OVERLAP_NOTE = ("Sit-and-reach also feeds the Athletic radar's Flexibility "
                     "axis (one rolled-up 0–100 score); here the SAME test is "
                     "shown as posterior-chain range detail per region — one test "
                     "at two altitudes, not double-counted.")
