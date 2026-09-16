# Relative subregion strength inference

OpenHealthAtlas exposes two different quantities on each muscle-group detail page:

1. **Exposure** is weighted working-set volume from the authored exercise map.
   It answers which subregions were trained, not which are strong.
2. **Relative strength theory** is a bounded personal-history estimate. It can
   suggest that one subregion may currently have more relative capacity than
   another, but it is not direct activation measurement, absolute strength,
   injury risk, or diagnosis.

## Evidence gate

A subregion needs observations on at least five distinct dates within the last
365 days. At least two subregions in the muscle group must meet that gate before
OpenHealthAtlas shows a ranking. Below the gate, the API and UI report insufficient
evidence and emit no ranked conclusion.

The gate counts recurrence, not repeated sets on one day. A single workout with
many sets therefore cannot create a theory.

## Signals

- **Authored activation weights:** the cited map assigns a relative involvement
  tier and an EMG-anchored or biomechanical confidence flag. Isometric rows are
  down-weighted for dynamic exposure.
- **Normalized exercise performance:** estimated performance is compared only
  with earlier values for the same exercise. The engine combines within-history
  mid-rank percentile and change from the earliest three to the latest three
  observations. Raw kilograms from different exercises are never compared.
- **Direct fitness tests:** repeated test protocols are normalized within that
  protocol and mapped through the same authored anatomy configuration.
- **Exposure support:** weighted set recurrence contributes a small supporting
  term. It is always labelled `exposure_proxy`, never direct strength.

When present, normalized performance contributes 60% of the signal weight,
direct tests 25%, and exposure support 15%. Missing performance or direct-test
signals are omitted and the remaining signal weights are renormalized; the
result remains visibly low-confidence when evidence coverage is thin.

## Uncertainty and imbalance wording

Confidence reflects recurring dates, map evidence quality, performance
coverage, and direct-test coverage. A relative score is comparable only with
other qualified subregions in the same muscle group and the same payload.

OpenHealthAtlas labels a **possible imbalance** only when the top-to-bottom relative
score gap is at least 15 points. The wording remains probabilistic. Smaller gaps
are reported as no clear imbalance, not proof of balance.

The public map is configuration knowledge, not a health record. It is seeded by
`scripts/init_hermes.py` and `scripts/make_demo_db.py`; unused map rows do not
create exposure or theories because calculations still require logged data.
