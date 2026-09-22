"""Dashboard score domain calculations with explicit collaborators."""
import statistics as st
from datetime import date, timedelta

from . import calculations, migrations, muscles, nutrition, runtime
from .catalogs import MUSCLE_GROUP_AXES
from .nutrition import (
    NUTRITION_WEIGHTS, day_values as _nutrition_day_values,
    day_score as _nutrition_day_score,
)
from .importers.cronometer import MICRO_CITATION_STATUS
from .score_contracts import band as _band, clamp100 as _clamp100
from .daily_frames import _rnd

_table_exists = runtime.table_exists

def _today(clock):
    return runtime.today(clock=clock)

def _days_ago(n, clock):
    return runtime.days_ago(n, clock=clock)

def _daily_metrics_has_hrv_ms(c):
    migrations.require_table(c, "daily_metrics", ("hrv_ms",))
    return True

def _score(v, **inputs):
    v = _clamp100(v)
    return {"score": v, "band": _band(v), "inputs": inputs}

def _no_data(reason, **inputs):
    d = {"insufficient_data": True, "reason": reason}
    if inputs: d["inputs"] = inputs
    return d

def _dev_score(value, baseline_vals, coef, invert):
    return calculations._dev_score(value, baseline_vals, coef, invert)

SLEEP_TARGET_H = 8.0
SCORES_DEFAULT_DAYS = 90

def _sleep_score(c, anchor=None, *, clock, include_ancestry=False):
    """Sleep component: most recent night within 2 days (sleep_log first,
    else daily_metrics fitbit-preferred fallback), 70% hours-vs-target + 30%
    quality/5 when quality exists, else hours only. ONE function — scores()'s
    sleep component AND readiness()'s sleep component both call this (T48:
    factor, don't fork). Returns a _score()/_no_data() shape."""
    night = None
    for back in (0, 1):
        d = ((anchor - timedelta(days=back)).isoformat()
             if anchor is not None else _days_ago(back, clock))
        r = c.execute(
            "SELECT time_asleep_hours h, quality q, source FROM sleep_log WHERE date=?",
            (d,),
        ).fetchone() if _table_exists(c, "sleep_log") else None
        if r and r["h"] is not None:
            night = {"date": d, "hours": r["h"], "quality": r["q"], "source": "sleep_log"}
            if include_ancestry:
                night["ancestry"] = {
                    "table": "sleep_log",
                    "locator": f"sleep_log:{d}",
                    "source_label": r["source"],
                    "observed_at": d,
                }
            break
        rows = {(x["source"] or "").lower(): x for x in c.execute(
            "SELECT source, sleep_hours FROM daily_metrics WHERE date=?", (d,))}
        for srcname in ["fitbit"] + sorted(k for k in rows if k != "fitbit"):
            selected = rows.get(srcname)
            if selected is not None and selected["sleep_hours"] is not None:
                night = {
                    "date": d,
                    "hours": selected["sleep_hours"],
                    "quality": None,
                    "source": srcname,
                }
                if include_ancestry:
                    night["ancestry"] = {
                        "table": "daily_metrics",
                        "locator": f"daily_metrics:{d}:{selected['source']}",
                        "source_label": selected["source"],
                        "observed_at": d,
                    }
                break
        if night: break
    if not night:
        return _no_data("no sleep data for today or yesterday")
    hours_part = min(night["hours"] / SLEEP_TARGET_H, 1.0) * 100
    if night["quality"] is not None:
        val = 0.7 * hours_part + 0.3 * (night["quality"] / 5 * 100)
    else:
        val = hours_part
    return _score(val, **night, target_h=SLEEP_TARGET_H)
def _recovery_baseline_rows(c, days, anchor=None, start=None, *, clock):
    """date-deduped daily_metrics rows (fitbit-preferred provenance — the
    user's merge rule) carrying resting_hr/hrv_ms plus each selected source,
    over the trailing
    `days` window. ONE query — shared by scores()'s combined recovery
    component and readiness()'s separate hrv/rhr components (T48: factor,
    don't fork). Rows with neither value are dropped. Read path — resolves
    the migration-owned hrv_ms via a no-DDL guard."""
    hrv_col = "hrv_ms" if _daily_metrics_has_hrv_ms(c) else "hrv_sdnn"
    by_date = {}
    if anchor is None:
        query = (f"""SELECT date, source, resting_hr, {hrv_col} AS hrv_ms FROM daily_metrics
                    WHERE date>=? AND date<=? ORDER BY date""", (_days_ago(days, clock), _today(clock)))
    else:
        lower = anchor - timedelta(days=days)
        if start is not None:
            lower = max(lower, start)
        query = (f"""SELECT date, source, resting_hr, {hrv_col} AS hrv_ms FROM daily_metrics
                    WHERE date>=? AND date<=? ORDER BY date""",
                 (lower.isoformat(), anchor.isoformat()))
    for r in c.execute(*query):
        by_date.setdefault(r["date"], {})[(r["source"] or "").lower()] = r
    def _pick(srcs, f):
        for sname in ["fitbit"] + sorted(k for k in srcs if k != "fitbit"):
            if sname in srcs and srcs[sname][f] is not None:
                return srcs[sname][f], srcs[sname]["source"], sname
        return None, None, None
    def _available(srcs, f):
        return {
            sname: {
                "source_label": row["source"],
                "value": row[f],
            }
            for sname, row in sorted(srcs.items())
            if row[f] is not None
        }
    base_rows = []
    for d, srcs in sorted(by_date.items()):
        resting_hr, resting_hr_source, resting_hr_source_key = _pick(
            srcs, "resting_hr"
        )
        hrv_ms, hrv_ms_source, hrv_ms_source_key = _pick(srcs, "hrv_ms")
        base_rows.append({
            "date": d,
            "resting_hr": resting_hr,
            "resting_hr_source": resting_hr_source,
            "resting_hr_source_key": resting_hr_source_key,
            "resting_hr_sources": _available(srcs, "resting_hr"),
            "hrv_ms": hrv_ms,
            "hrv_ms_source": hrv_ms_source,
            "hrv_ms_source_key": hrv_ms_source_key,
            "hrv_ms_sources": _available(srcs, "hrv_ms"),
        })
    return [r for r in base_rows if r["resting_hr"] is not None or r["hrv_ms"] is not None]
def scores(c, days, *, clock, timezone, nutrition_config=None):
    days = max(int(days), 14)
    lo = _days_ago(days, clock)
    out_scores = {}

    # -- consistency (hero) --------------------------------------------------
    if _table_exists(c, "commitments_log"):
        logged = [(r["date"], {"kept": 1.0, "partly": 0.5, "broke": 0.0}[r["status"]])
                  for r in c.execute(
                      """SELECT date, status FROM commitments_log
                         WHERE commitment_id=0 AND date>=? ORDER BY date""",
                      (_days_ago(30, clock),))]
        if len(logged) >= 3:
            rate = st.mean(v for _, v in logged)
            # a streak is only a streak if it reaches the present: the run must
            # include today or yesterday (tonight's answer may not exist yet)
            streak, prev = 0, None
            if logged[-1][0] >= _days_ago(1, clock):
                for d, v in reversed(logged):
                    if v == 0.0: break
                    if prev is not None and (date.fromisoformat(prev) - date.fromisoformat(d)).days != 1:
                        break
                    streak += 1; prev = d
            out_scores["consistency"] = _score(rate * 100,
                days_logged=len(logged),
                kept=sum(1 for _, v in logged if v == 1.0),
                partly=sum(1 for _, v in logged if v == 0.5),
                broke=sum(1 for _, v in logged if v == 0.0),
                streak=streak, window_days=30)
        else:
            out_scores["consistency"] = _no_data(
                "needs >= 3 evening kept/partly/broke answers", days_logged=len(logged))
    else:
        out_scores["consistency"] = _no_data("no follow-through data logged yet")

    # -- sleep ----------------------------------------------------------------
    # T48: factored into _sleep_score — readiness()'s sleep component calls
    # the SAME function, never a re-derivation.
    out_scores["sleep"] = _sleep_score(c, clock=clock)

    # -- recovery (personal-baseline relative) --------------------------------
    # T48: the baseline query (_recovery_baseline_rows) and the 50±k mapping
    # (_dev_score) are factored out — readiness()'s separate hrv/rhr
    # components call the SAME two helpers. This block's gating/output is
    # byte-identical to the pre-T48 inline version (known-answer tests pin it).
    base_rows = _recovery_baseline_rows(c, days, clock=clock)
    today_row = next((r for b in (0, 1) for r in base_rows if r["date"] == _days_ago(b, clock)), None)
    rhr_base = [r["resting_hr"] for r in base_rows if r["resting_hr"] is not None
                and (not today_row or r["date"] != today_row["date"])]
    hrv_base = [r["hrv_ms"] for r in base_rows if r["hrv_ms"] is not None
                and (not today_row or r["date"] != today_row["date"])]
    if today_row and (len(rhr_base) >= 14 or len(hrv_base) >= 14):
        parts, inputs = [], {"date": today_row["date"]}
        if today_row["resting_hr"] is not None:
            r = _dev_score(today_row["resting_hr"], rhr_base, 500, True)
            if r:
                sc, b = r
                parts.append(sc)
                inputs.update(rhr=today_row["resting_hr"], rhr_baseline=_rnd(b, 1))
        if today_row["hrv_ms"] is not None:
            r = _dev_score(today_row["hrv_ms"], hrv_base, 250, False)
            if r:
                sc, b = r
                parts.append(sc)
                inputs.update(hrv=today_row["hrv_ms"], hrv_baseline=_rnd(b, 1))
        if parts:
            out_scores["recovery"] = _score(st.mean(parts), **inputs,
                                            baseline_days=max(len(rhr_base), len(hrv_base)))
        else:
            out_scores["recovery"] = _no_data("no RHR/HRV reading today or yesterday")
    else:
        out_scores["recovery"] = _no_data(
            "needs a current RHR/HRV reading + >= 14 baseline days",
            baseline_days=max(len(rhr_base), len(hrv_base)))

    # -- muscle balance (7d logged vs planned, on the radar's 7-group map) -----
    # validated behavior 2026-07-09: the score and the radar share ONE rollup —
    # since v2.8 that is _rollup7 (authored-first cited sub-muscle map, coarse
    # Hevy-tag fallback), so the card's "X of Y groups" agrees with the 7-axis
    # radar (Y <= 7). A group counts as PLANNED by presence (any scheduled
    # exercise touches it), not by volume — routine-set without --sets leaves
    # target_sets NULL and must not silently shrink the denominator. Unmapped
    # muscle names are echoed in inputs so dropped volume is diagnosable.
    lg7 = muscles.rollup(c, "logged", 7, clock=clock)
    pl7 = muscles.rollup(c, "planned", clock=clock)
    logged_v = lg7["groups"]
    planned_groups = [g for g in MUSCLE_GROUP_AXES if g in pl7["present"]]
    unmapped = sorted(lg7["unmapped"] | pl7["unmapped"])
    unmapped_ex = sorted(set(lg7["unmatched"]) | set(pl7["unmatched"]))
    if sum(logged_v.values()) >= 3 and planned_groups:
        covered = sum(1 for g in planned_groups if logged_v.get(g, 0) >= 2)
        coverage = covered / len(planned_groups)
        vals = [logged_v.get(g, 0.0) for g in planned_groups]
        mean_v = st.mean(vals)
        cv = (st.pstdev(vals) / mean_v) if mean_v > 0 else 1.0
        evenness = max(0.0, 1.0 - min(cv, 1.0))
        out_scores["muscle_balance"] = _score(60 * coverage + 40 * evenness,
            groups_planned=len(planned_groups), groups_covered=covered,
            evenness=_rnd(evenness, 2), window_days=7, unmapped=unmapped,
            unmapped_exercises=unmapped_ex)
    else:
        reason = ("no scheduled exercise maps to the 7 muscle groups — check "
                  "the routine/schedule and the muscle map" if not planned_groups
                  else "needs logged sets this week")
        out_scores["muscle_balance"] = _no_data(
            reason, effective_sets_7d=_rnd(sum(logged_v.values()), 1),
            unmapped=unmapped, unmapped_exercises=unmapped_ex)

    # -- water ------------------------------------------------------------
    # T46: target now comes from the T44 compute engine (_compute_targets ->
    # _water_target: configured baseline + exercise/weather heuristic) instead of the
    # flat WATER_TARGET_ML constant, so the dashboard ring and the Nutrition
    # page's Water surfaces agree on one number. WATER_TARGET_ML remains the
    # engine's own documented no-weight fallback (see _water_target) — both
    # the "ok" and "insufficient_data" shapes of _compute_targets always
    # carry targets.water_ml, so this doesn't need its own profile gate.
    tg = nutrition.compute_targets(c, clock=clock, config=nutrition_config)
    water_target = tg["targets"]["water_ml"]["target"]
    w = c.execute("SELECT water_ml FROM intake WHERE date=?", (_today(clock),)).fetchone()
    if w and w["water_ml"] is not None:
        out_scores["water"] = _score(w["water_ml"] / water_target * 100,
                                     water_ml=w["water_ml"], target_ml=water_target)
    else:
        out_scores["water"] = _no_data("no water logged today", target_ml=water_target)

    # -- nutrition (§4a composite: protein 25 + kcal 15 + top-10 micros 60) ----
    # T46: ONE shared scorer (_nutrition_day_score) and ONE targets source
    # (_compute_targets, the T44 engine — the same one nutrition-coverage and
    # the Nutrition page's "% of target" card use) replace the old
    # Cronometer-data + manually-set nutrition_targets-rows gate. A legacy
    # nutrition_targets override (nutrition-target-set) still applies, but
    # now layers ON TOP OF a complete profile rather than substituting for
    # one — see _compute_targets. Weights/credit shapes are UNCHANGED
    # (NUTRITION_WEIGHTS, ±10%→0-at-±25% kcal band, 80%-low-micro flag);
    # only WHERE the targets come from changed.
    nut_day = None
    for back in (0, 1):
        dd = _days_ago(back, clock)
        if _nutrition_day_values(c, dd):
            nut_day = dd
            break
    if nut_day is None:
        out_scores["nutrition"] = _no_data(
            "no logged nutrition or Cronometer data for today/yesterday",
            citation_status=MICRO_CITATION_STATUS)
    elif tg["status"] != "ok":
        out_scores["nutrition"] = _no_data(tg["reason"], date=nut_day,
                                           citation_status=MICRO_CITATION_STATUS)
    else:
        res = _nutrition_day_score(c, nut_day, tg["targets"])
        comp = res["components"]
        out_scores["nutrition"] = _score(
            res["score"], date=nut_day, protein_g=comp["protein_g"],
            protein_target=comp["protein_target"], kcal=comp["kcal"],
            kcal_target=comp["kcal_target"], micros=comp["micros"],
            low_micros=comp["low_micros"], weights=NUTRITION_WEIGHTS,
            citation_status=MICRO_CITATION_STATUS)

    # -- mind (T49: stated-weights composite over WHATEVER of these has data —
    #    equal-weight mean, weights stated in output. NO sentiment analysis on
    #    brain_dump text (determinism law); it only feeds a streak count.
    #    anxiety is deliberately NOT included in v1 (symptom-direction field,
    #    owner didn't list it in the spec). A `social` component would join
    #    here once social logging exists in the schema — not emitted as a
    #    null placeholder in the meantime.) ------------------------------
    lo30 = _days_ago(30, clock)
    mind_components = []
    if _table_exists(c, "subjective_daily"):
        for key in ("mood", "focus", "emotional_regulation"):
            vals = [r[0] for r in c.execute(
                f"SELECT {key} FROM subjective_daily WHERE date>=? AND {key} IS NOT NULL",
                (lo30,))]
            if vals:
                mv = st.mean(vals)
                mind_components.append({"key": key, "score": _clamp100(mv * 20),
                    "basis": f"30d mean {mv:.1f}/5 over {len(vals)} day(s)"})
        bd_dates = [r[0] for r in c.execute(
            "SELECT date FROM subjective_daily WHERE date>=?"
            " AND TRIM(COALESCE(brain_dump,'')) != '' ORDER BY date", (lo30,))]
        if bd_dates:
            streak, prev = 0, None
            if bd_dates[-1] >= _days_ago(1, clock):    # only a run reaching today/yesterday counts
                for d in reversed(bd_dates):
                    if prev is not None and (date.fromisoformat(prev) - date.fromisoformat(d)).days != 1:
                        break
                    streak += 1; prev = d
            capped = min(streak, 14)           # 14-day cap: presentation scaling for
                                                # the 0-100 score, not a clinical claim
            mind_components.append({"key": "brain_dump_streak",
                "score": _clamp100(capped / 14 * 100),
                "basis": f"{streak}-day brain-dump streak (14-day cap for scoring)"})
    if _table_exists(c, "habits_log"):
        hrows = c.execute("SELECT done FROM habits_log WHERE date>=?", (lo30,)).fetchall()
        if hrows:
            done_n = sum(1 for r in hrows if r["done"])
            pct = 100 * done_n / len(hrows)
            mind_components.append({"key": "habit_consistency", "score": _clamp100(pct),
                "basis": f"{done_n}/{len(hrows)} habits_log rows done ({_rnd(pct, 1)}%)"})
    if len(mind_components) < 2:
        have = [mc["key"] for mc in mind_components]
        missing = [k for k in ("mood", "focus", "emotional_regulation",
                                "brain_dump_streak", "habit_consistency") if k not in have]
        out_scores["mind"] = _no_data(
            "needs >= 2 of mood/focus/emotional_regulation/brain_dump_streak/"
            f"habit_consistency logged in the last 30 days (have {len(have)}: "
            f"{', '.join(have) or 'none'}; missing {', '.join(missing)})",
            components=mind_components, window_days=30)
    else:
        mind_val = st.mean(mc["score"] for mc in mind_components)
        out_scores["mind"] = _score(mind_val, components=mind_components,
                                    weights="equal", window_days=30)

    # -- external care (T49: 30-day skincare-routine adherence) ---------------
    # ONE definition, two readers: done = used=1 rows, expected = logged rows,
    # joined to skincare_products with BOTH sides of the fraction filtered to
    # active=1 — the T34 lesson (supplements adherence once filtered only the
    # denominator to active products, inflating/deflating the score off a
    # retired product's historical rows; fixed there by filtering both sides
    # together, applied proactively here from day one). app/routes/dash.py's
    # care() route was aligned to this same active-only semantic in T55
    # (owner flag 1), closing the discrepancy T49 had documented and parked.
    care_lo = _days_ago(30, clock)
    care_row = None
    if _table_exists(c, "skincare_log") and _table_exists(c, "skincare_products"):
        care_row = c.execute(
            "SELECT COUNT(*) AS expected, SUM(sl.used) AS done FROM skincare_log sl"
            " JOIN skincare_products sp ON sp.product_id = sl.product_id"
            " WHERE sp.active = 1 AND sl.date >= ?", (care_lo,)).fetchone()
    if care_row and care_row["expected"]:
        expected, done = care_row["expected"], care_row["done"] or 0
        out_scores["care"] = _score(100 * done / expected,
                                    done=done, expected=expected, window_days=30)
    else:
        out_scores["care"] = _no_data(
            "no skincare_log rows for an active product in the last 30 days",
            window_days=30)

    # -- habits & streaks (for the habits card; streak = consecutive days done,
    #    ending at the habit's most recent log; active = logged in last 21 days)
    habits = []
    if _table_exists(c, "habits_log"):
        names = [r["h"] for r in c.execute(
            "SELECT DISTINCT habit h FROM habits_log WHERE date>=? ORDER BY habit",
            (_days_ago(21, clock),))]
        for h in names:
            rows = c.execute(
                """SELECT date, MAX(done) done FROM habits_log WHERE habit=? AND date>=?
                   GROUP BY date ORDER BY date""", (h, lo)).fetchall()
            streak, prev = 0, None
            if rows and rows[-1]["date"] >= _days_ago(1, clock):   # current runs only
                for r in reversed(rows):
                    if not r["done"]: break
                    if prev is not None and (date.fromisoformat(prev) - date.fromisoformat(r["date"])).days != 1:
                        break
                    streak += 1; prev = r["date"]
            habits.append({"habit": h, "streak": streak,
                           "last_done": next((r["date"] for r in reversed(rows) if r["done"]), None),
                           "done_7d": sum(1 for r in rows if r["done"] and r["date"] >= _days_ago(7, clock))})
    return {"meta": {"tz": str(timezone), "date": _today(clock), "window_days": days,
                  "note": "all scores computed here; bands engine-assigned; "
                          "missing inputs -> insufficient_data, never a guess"},
         "scores": out_scores, "habits": habits}
