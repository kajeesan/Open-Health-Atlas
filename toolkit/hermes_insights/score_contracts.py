"""Shared score rounding and band thresholds."""

SCORE_BAD_CUTOFF = 40
SCORE_GOOD_CUTOFF = 70


def band(v):
    return (
        "bad" if v < SCORE_BAD_CUTOFF
        else "warn" if v < SCORE_GOOD_CUTOFF
        else "good"
    )


def clamp100(v):
    return int(round(max(0.0, min(100.0, v))))
