"""Same-session body measurements and the retained V-taper report."""

from . import migrations, runtime

# WCR (waist ÷ chest) is THE tracked V-taper metric — the peer-reviewed one
# (Garza et al. 2017, Evolutionary Psychology, WCR ≈0.7). The 1.618 "golden
# ratio" is an aesthetics-community convention, deliberately NOT the goal
# (validated behavior in fable-master-prd.md §3f). Lower WCR = more taper, so a
# reading is "at target" when it is AT OR BELOW 0.7.
VTAPER_TARGET_WCR = 0.7


def vtaper(c, days, *, clock):
    """§3f: WCR = waist_cm / chest_cm from body_metrics, SAME-ROW pairing only
    (a waist taped this week against a chest taped last month is not a ratio —
    both tapes must come from one measurement session/row). Latest row per
    date wins; honest insufficient_data until a paired measurement exists."""
    migrations.require_table(c, "body_metrics", ("source",))
    lo = runtime.days_ago(max(int(days), 1), clock=clock)
    per_date = {}
    # Tie-break within a date: a MANUAL tape entry beats a hevy-synced one (a
    # manual row is the user's deliberate correction and must not be shadowed
    # by the nightly sync), then the higher id wins within the same source.
    for r in c.execute("""SELECT id, date, waist_cm, chest_cm, source FROM body_metrics
        WHERE waist_cm IS NOT NULL AND chest_cm IS NOT NULL AND chest_cm > 0
          AND date >= ?
        ORDER BY date, CASE WHEN source='hevy' THEN 0 ELSE 1 END, id""", (lo,)):
        per_date[r["date"]] = r      # last processed wins: manual > hevy, then id
    series = [{"date": d, "waist_cm": r["waist_cm"], "chest_cm": r["chest_cm"],
               "wcr": round(r["waist_cm"] / r["chest_cm"], 3), "source": r["source"]}
              for d, r in sorted(per_date.items())]
    base = {"target_wcr": VTAPER_TARGET_WCR, "days": int(days),
            "cite": "Garza et al. 2017, Evolutionary Psychology (WCR ≈0.7)",
            "note": ("waist ÷ chest, lower is more taper; the 1.618 golden ratio is "
                     "deliberately not the goal. Measure the same landmarks, relaxed, "
                     "same time of day — consistency makes the trend real.")}
    if not series:
        return {**base, "insufficient_data": True,
             "reason": "no measurement with BOTH waist_cm and chest_cm in the window "
                       "— log a tape session (or Hevy measurements once synced)"}
        return
    cur = series[-1]
    trend = round(cur["wcr"] - series[0]["wcr"], 3) if len(series) >= 2 else None
    return {**base, "current": cur, "at_target": cur["wcr"] <= VTAPER_TARGET_WCR,
         "delta_to_target": round(cur["wcr"] - VTAPER_TARGET_WCR, 3),
         "trend": trend, "n_measurements": len(series), "series": series}
