"""Pain capture vocabulary and migration-owned schema requirements."""

from . import migrations

# Cited provocation and loading patterns organize evidence; they do not diagnose.
PAIN_RED_FLAGS = {
    # Signals genuinely OUTSIDE a mechanical-pattern model → an honest
    # model-boundary line, surfaced ONCE (never a reflexive "see a doctor").
    "radiating", "numbness", "night-pain", "progressive", "trauma",
    "systemic", "cauda-equina"}

PAIN_QUALITY = {"ache", "sharp", "burning", "stiff", "throb"}

PAIN_SIDES = {"left", "right", "central"}

PAIN_PATTERN = {"load", "rest", "morning", "night", "constant", "intermittent"}

TEST_RESULTS = {"positive", "negative", "equivocal"}

TRIAL_RESPONSES = {"better", "same", "worse"}

PAIN_NRS_CLAMP = (0, 10)

PAIN_STALE_DAYS = 120

# NRS bands (0–10): none / mild 1–3 / moderate 4–6 / severe 7–10 — the standard
# clinical mild/moderate/severe pain bands. Half-open [lo, hi); pinned by tests.
PAIN_BAND_EDGES = (1, 4, 7)

FIGURE_PAIN_LEGEND = [
    {"status": "none", "label": "no pain logged"},
    {"status": "mild", "label": "mild (NRS 1–3)"},
    {"status": "moderate", "label": "moderate (NRS 4–6)"},
    {"status": "severe", "label": "severe (NRS 7–10)"},
]

_PAIN_STATUSES = ("none", "mild", "moderate", "severe")

# The configured-medication vitals line stays visible on the physio surface
# note): cardiac symptoms are urgent and NOT a mechanical-pattern problem.
CV_NOTE = ("Cardiac symptoms — chest pain, fainting, a racing/irregular heart — "
           "are urgent and not part of this pattern model: stop and seek care now.")

BOUNDARY_NOTE = ("Some logged pain carries a signal outside what this "
                 "mechanical-pattern tool can reason about (radiating pain, "
                 "numbness/tingling, night pain, progressive/unremitting pain, "
                 "trauma, systemic illness, or saddle/bladder change). That's a "
                 "model boundary, not an alarm — worth raising with a clinician.")


def pain_band(nrs):
    if nrs is None or nrs <= 0:
        return "none"
    for i, edge in enumerate(PAIN_BAND_EDGES):
        if nrs < edge:
            return _PAIN_STATUSES[i]
    return _PAIN_STATUSES[-1]


def require_physio_tables(c):
    """Idempotent (§3g). Append-only + soft-void; mirrors _ensure_fitness_tables.
    The region/test/drill vocabularies live in code, never in the DB."""
    migrations.require_table(c, "pain_log", ("reported_onset_date", "onset_precision"))
    migrations.require_table(c, "self_test_log")
    migrations.require_table(c, "exercise_trial_log")
