"""Daily-frame, legacy statistics and coverage domain calculations.

This module owns the legacy frame contract; commands compose connections and
serialize its returned values.
"""
import itertools
import re
import statistics as st
import sys
from datetime import date, timedelta

from . import calculations, migrations, runtime
from .catalogs import DM_METRICS, DM_PRIMARY, MEDICATION_ALIASES

_table_exists = runtime.table_exists

def _clock_now(clock):
    return clock()

def _days_ago(n, clock):
    return runtime.days_ago(n, clock=clock)

PILLARS = {
  "sleep_hours": "sleep", "sl_asleep_h": "sleep", "sl_quality": "sleep",
  "sl_bedtime_min": "sleep", "sl_awakenings": "sleep", "sl_deep_min": "sleep", "sl_rem_min": "sleep",
  "resting_hr": "recovery", "hrv_ms": "recovery", "respiratory_rate": "recovery",
  "spo2_pct": "recovery", "hr_avg": "recovery", "hr_min": "recovery", "hr_max": "recovery",
  "walking_hr_avg": "recovery",
  "steps": "activity", "active_energy_kcal": "activity", "basal_energy_kcal": "activity",
  "exercise_min": "activity", "distance_km": "activity", "flights": "activity",
  "cardio_min": "activity", "cardio_km": "activity", "cardio_kcal": "activity",
  "tr_sets": "training", "tr_volume_kg": "training", "tr_session": "training", "tr_mean_rpe": "training",
  "day_rating": "subjective", "focus": "subjective", "energy": "subjective", "mood": "subjective",
  "emotional_regulation": "subjective", "anxiety": "subjective", "stress": "subjective",
  "motivation": "subjective",
  "caffeine_mg": "substances", "alcohol_units": "substances",
  "medication_dose_mg": "meds", "medication_first_dose_min": "meds", "medication_n_doses": "meds", "medication_rebound": "meds",
  "bp_sys": "vitals", "bp_dia": "vitals", "cuff_hr": "vitals",
  "nut_kcal": "nutrition", "nut_protein_g": "nutrition", "water_ml": "nutrition",
  "weight_kg": "body", "waist_cm": "body",
  "wx_temp_max_c": "environment", "wx_sunshine_h": "environment", "wx_daylight_h": "environment",
  "wx_precip_mm": "environment", "wx_uv_max": "environment",
  "air_aqi": "environment", "air_pm2_5": "environment", "air_grass_pollen": "environment",
  "air_birch_pollen": "environment",
  "word_kept": "integrity", "commit_kept_rate": "integrity", "habits_done": "integrity",
}
PILLARS.update({
    f"chk_{k}_{b}": "subjective"
    for k in ("energy", "focus", "mood")
    for b in ("am", "pm", "eve")
})
FLIPPED = {"anxiety", "stress", "medication_rebound", "sl_awakenings"}
FEATURE_BASE = ("sleep_hours", "resting_hr", "hrv_ms", "steps", "mood", "energy",
                "focus", "anxiety", "stress", "tr_volume_kg", "nut_protein_g",
                "nut_kcal", "medication_dose_mg", "word_kept")
LAG1_FIELDS = ("tr_volume_kg", "tr_session", "medication_dose_mg", "alcohol_units",
               "caffeine_mg", "cardio_min", "sl_bedtime_min", "sleep_hours", "word_kept")
CONDITION_FIELDS = ("medication_dose_mg", "medication_first_dose_min", "sleep_hours",
                    "sl_bedtime_min", "mood", "energy", "stress", "anxiety",
                    "tr_session", "is_weekend", "habits_done", "steps",
                    "caffeine_mg", "alcohol_units", "word_kept_r7")
def _hhmm_min(s):
    return calculations._hhmm_min(s)
def _bedtime_min(s):
    """Bedtime as minutes from the PREVIOUS noon, so 23:30 (690) < 00:30 (750)
    orders correctly across midnight and correlates monotonically with 'late'."""
    v = _hhmm_min(s)
    if v is None: return None
    return v + 1440 - 720 if v < 720 else v - 720
def _rnd(v, nd=4):
    return round(v, nd) if isinstance(v, float) else v
def _daily_frame(c, days, *, clock, medication_aliases=None):
    """One record per calendar day joining every table that exists. Missing
    tables are skipped (and reported), never fabricated. Returns (dates,
    rows: {date: {field: value}}, coverage)."""
    cov = {"missing_tables": [], "table_rows": {}, "dm_source_counts": {},
           "other_medications": [], "unparsed_times": 0}
    if medication_aliases is None:
        medication_aliases = MEDICATION_ALIASES
    end = _clock_now(clock).date()

    def _tbl(name):
        if _table_exists(c, name): return True
        cov["missing_tables"].append(name); return False

    # window start: --days N back from today, or the earliest row anywhere (0 = all)
    if days:
        start = end - timedelta(days=days - 1)
    else:
        mins = []
        for t in ("daily_metrics", "subjective_daily", "workouts", "hevy_sets",
                  "meds_log", "sleep_log", "nutrition_log", "vitals",
                  "commitments_log", "checkins"):
            if _table_exists(c, t):
                r = c.execute(f"SELECT MIN(date) m FROM {t}").fetchone()
                if r and r["m"]: mins.append(r["m"])
        start = date.fromisoformat(min(mins)) if mins else end
    lo = start.isoformat()
    dates = [(start + timedelta(days=i)).isoformat()
             for i in range((end - start).days + 1)]
    rows = {d: {} for d in dates}

    def put(d, field, value):
        if d in rows and value is not None:
            rows[d][field] = _rnd(value)

    def zero_fill_era(seen_dates, fields):
        """Hevy exports / Apple workout history are COMPLETE within the period
        they cover, so between the first and last logged date a day with NO
        rows is a genuine rest day (0), not missing data. Days that DO have a
        row keep their per-field nulls (a 45-min run with unknown km must stay
        km=null, never 0 — review finding), and outside the era we honestly
        don't know, so everything stays null."""
        if not seen_dates: return
        first, last = min(seen_dates), max(seen_dates)
        seen = set(seen_dates)
        for d in dates:
            if first <= d <= last and d not in seen:
                for f in fields: rows[d][f] = 0

    # -- daily_metrics: per-metric primary source with fallback --------------
    if _tbl("daily_metrics"):
        by_date = {}
        n = 0
        for raw in c.execute("SELECT * FROM daily_metrics WHERE date>=?", (lo,)):
            # DM_METRICS indexes rows by "hrv_ms" (the canonical name) — a
            # legacy DB's SELECT * carries "hrv_sdnn" instead (no "hrv_ms" key
            # at all), which would KeyError below. Read path — never DDL, just
            # a Python-side COALESCE: prefer hrv_ms, fall back to hrv_sdnn when
            # hrv_ms is absent or (defensively) null. See _daily_metrics_has_hrv_ms
            # for the writer-side migration; nothing mutates the DB here.
            r = dict(raw)
            if r.get("hrv_ms") is None:
                r["hrv_ms"] = r.get("hrv_sdnn")
            by_date.setdefault(r["date"], {})[(r["source"] or "").lower()] = r
            n += 1
        cov["table_rows"]["daily_metrics"] = n
        counts = {}
        for d, srcs in by_date.items():
            for m in DM_METRICS:
                prio = [DM_PRIMARY[m]] + sorted(s for s in srcs if s != DM_PRIMARY[m])
                for s in prio:
                    r = srcs.get(s)
                    if r is not None and r[m] is not None:
                        put(d, m, r[m])
                        counts.setdefault(m, {}).setdefault(s, 0)
                        counts[m][s] += 1
                        break
        cov["dm_source_counts"] = counts

    # -- subjective_daily -----------------------------------------------------
    if _tbl("subjective_daily"):
        n = 0
        for r in c.execute("SELECT * FROM subjective_daily WHERE date>=?", (lo,)):
            n += 1
            for f in ("day_rating", "focus", "energy", "mood", "emotional_regulation",
                      "anxiety", "stress", "motivation", "caffeine_mg", "alcohol_units"):
                put(r["date"], f, r[f])
        cov["table_rows"]["subjective_daily"] = n

    # -- vitals: day means (several cuff readings per day are normal) --------
    if _tbl("vitals"):
        n = 0
        for r in c.execute("""SELECT date, AVG(systolic) s, AVG(diastolic) d,
                              AVG(resting_hr) h, COUNT(*) n FROM vitals
                              WHERE date>=? GROUP BY date""", (lo,)):
            n += r["n"]
            put(r["date"], "bp_sys", r["s"]); put(r["date"], "bp_dia", r["d"])
            put(r["date"], "cuff_hr", r["h"])
        cov["table_rows"]["vitals"] = n

    # -- meds_log: configured medication only; unrelated rows never summed --
    if _tbl("meds_log"):
        n = 0; others = set()
        agg = {}
        for r in c.execute("SELECT * FROM meds_log WHERE date>=?", (lo,)):
            n += 1
            drug = (r["drug"] or "").strip().lower()
            if drug not in medication_aliases:
                if drug: others.add(drug)
                continue
            a = agg.setdefault(r["date"], {"mg": 0.0, "n": 0, "times": [], "reb": None})
            if r["dose_mg"] is not None:
                a["mg"] += r["dose_mg"]; a["n"] += 1
            t = _hhmm_min(r["time_taken"])
            if t is not None: a["times"].append(t)
            elif r["time_taken"]: cov["unparsed_times"] += 1
            if r["rebound"] is not None:
                a["reb"] = max(a["reb"] or 0, 1 if r["rebound"] else 0)
        for d, a in agg.items():
            if a["n"]:
                put(d, "medication_dose_mg", a["mg"]); put(d, "medication_n_doses", a["n"])
            if a["times"]: put(d, "medication_first_dose_min", min(a["times"]))
            if a["reb"] is not None: put(d, "medication_rebound", a["reb"])
        cov["table_rows"]["meds_log"] = n
        cov["other_medications"] = sorted(others)

    # -- sleep_log (manual/coach-logged sleep; wearable sleep_hours is separate)
    if _tbl("sleep_log"):
        n = 0
        for r in c.execute("SELECT * FROM sleep_log WHERE date>=?", (lo,)):
            n += 1
            put(r["date"], "sl_asleep_h", r["time_asleep_hours"])
            put(r["date"], "sl_quality", r["quality"])
            put(r["date"], "sl_awakenings", r["awakenings"])
            put(r["date"], "sl_deep_min", r["deep_min"])
            put(r["date"], "sl_rem_min", r["rem_min"])
            put(r["date"], "sl_bedtime_min", _bedtime_min(r["bedtime"]))
        cov["table_rows"]["sleep_log"] = n

    # -- hevy_sets: strength session summary ---------------------------------
    # A warmup-only day is still a training day (tr_session=1): warmups are
    # excluded only from the working-set count/volume, not from "did I train".
    if _tbl("hevy_sets"):
        n = 0; seen = []
        for r in c.execute("""SELECT date, COUNT(*) all_sets,
              SUM(CASE WHEN COALESCE(set_type,'normal')!='warmup' THEN 1 ELSE 0 END) sets,
              SUM(CASE WHEN COALESCE(set_type,'normal')!='warmup'
                  THEN COALESCE(weight_kg,0)*COALESCE(reps,0) ELSE 0 END) vol,
              AVG(CASE WHEN COALESCE(set_type,'normal')!='warmup' THEN rpe END) rpe
              FROM hevy_sets WHERE date>=? AND date IS NOT NULL
              GROUP BY date""", (lo,)):
            n += r["all_sets"]; seen.append(r["date"])
            put(r["date"], "tr_sets", r["sets"])
            put(r["date"], "tr_volume_kg", r["vol"])
            put(r["date"], "tr_session", 1)
            put(r["date"], "tr_mean_rpe", r["rpe"])
        zero_fill_era(seen, ("tr_sets", "tr_volume_kg", "tr_session"))
        cov["table_rows"]["hevy_sets"] = n

    # -- workouts (cardio history) -------------------------------------------
    if _tbl("workouts"):
        n = 0; seen = []
        for r in c.execute("""SELECT date, SUM(minutes) m, SUM(km) km, SUM(kcal) k,
                              COUNT(*) n FROM workouts WHERE date>=? GROUP BY date""", (lo,)):
            n += r["n"]; seen.append(r["date"])
            put(r["date"], "cardio_min", r["m"]); put(r["date"], "cardio_km", r["km"])
            put(r["date"], "cardio_kcal", r["k"])
        zero_fill_era(seen, ("cardio_min", "cardio_km", "cardio_kcal"))
        cov["table_rows"]["workouts"] = n

    # -- nutrition / intake ----------------------------------------------------
    if _tbl("nutrition_log"):
        n = 0
        for r in c.execute("""SELECT date, SUM(kcal) k, SUM(protein_g) p, COUNT(*) n
                              FROM nutrition_log WHERE date>=? GROUP BY date""", (lo,)):
            n += r["n"]
            put(r["date"], "nut_kcal", r["k"]); put(r["date"], "nut_protein_g", r["p"])
        cov["table_rows"]["nutrition_log"] = n
    if _tbl("intake"):
        n = 0
        for r in c.execute("SELECT date, water_ml FROM intake WHERE date>=?", (lo,)):
            n += 1; put(r["date"], "water_ml", r["water_ml"])
        cov["table_rows"]["intake"] = n

    # -- habits ---------------------------------------------------------------
    if _tbl("habits_log"):
        n = 0
        for r in c.execute("""SELECT date, SUM(CASE WHEN done THEN 1 ELSE 0 END) d,
                              COUNT(*) n FROM habits_log WHERE date>=? GROUP BY date""", (lo,)):
            n += r["n"]; put(r["date"], "habits_done", r["d"])
        cov["table_rows"]["habits_log"] = n

    # -- body -----------------------------------------------------------------
    if _tbl("body_metrics"):
        n = 0
        for r in c.execute("""SELECT date, weight_kg, waist_cm FROM body_metrics
                              WHERE date>=? ORDER BY date, id""", (lo,)):
            n += 1
            put(r["date"], "weight_kg", r["weight_kg"]); put(r["date"], "waist_cm", r["waist_cm"])
        cov["table_rows"]["body_metrics"] = n

    # -- environment ----------------------------------------------------------
    if _tbl("weather"):
        n = 0
        for r in c.execute("SELECT * FROM weather WHERE date>=?", (lo,)):
            n += 1
            put(r["date"], "wx_temp_max_c", r["temp_max_c"])
            put(r["date"], "wx_sunshine_h", r["sunshine_hours"])
            put(r["date"], "wx_daylight_h", r["daylight_hours"])
            put(r["date"], "wx_precip_mm", r["precipitation_mm"])
            put(r["date"], "wx_uv_max", r["uv_index_max"])
        cov["table_rows"]["weather"] = n
    if _tbl("air_quality"):
        n = 0
        for r in c.execute("SELECT * FROM air_quality WHERE date>=?", (lo,)):
            n += 1
            put(r["date"], "air_aqi", r["european_aqi_mean"])
            put(r["date"], "air_pm2_5", r["pm2_5_ugm3"])
            put(r["date"], "air_grass_pollen", r["grass_pollen"])
            put(r["date"], "air_birch_pollen", r["birch_pollen"])
        cov["table_rows"]["air_quality"] = n

    # -- follow-through layer (created by Part C; tolerate absence) ----------
    if _table_exists(c, "commitments_log"):
        n = 0
        stat = {"kept": 1.0, "partly": 0.5, "broke": 0.0}
        day_word = {}; per_commit = {}
        for r in c.execute("SELECT * FROM commitments_log WHERE date>=?", (lo,)):
            n += 1
            v = stat.get(r["status"])
            if v is None: continue
            if not r["commitment_id"]:             # 0 (or legacy NULL) = whole-day
                day_word[r["date"]] = v
            else:
                per_commit.setdefault(r["date"], []).append(v)
        for d, v in day_word.items(): put(d, "word_kept", v)
        for d, vs in per_commit.items():
            put(d, "commit_kept_rate", sum(vs) / len(vs))
        cov["table_rows"]["commitments_log"] = n
    else:
        cov["missing_tables"].append("commitments_log")

    # -- timed check-ins, bucketed into day parts -----------------------------
    if _table_exists(c, "checkins"):
        n = 0; agg = {}
        for r in c.execute("SELECT date, time, kind, value FROM checkins WHERE date>=?", (lo,)):
            n += 1
            t = _hhmm_min(r["time"])
            if t is None:
                cov["unparsed_times"] += 1; continue
            bucket = "am" if t < 720 else "pm" if t < 1020 else "eve"
            agg.setdefault((r["date"], r["kind"], bucket), []).append(r["value"])
        for (d, kind, bucket), vs in agg.items():
            put(d, f"chk_{kind}_{bucket}", sum(vs) / len(vs))
        cov["table_rows"]["checkins"] = n
    else:
        cov["missing_tables"].append("checkins")

    # calendar context
    for d in dates:
        wd = date.fromisoformat(d).weekday()        # 0=Mon
        rows[d]["weekday"] = wd
        rows[d]["is_weekend"] = 1 if wd >= 5 else 0

    cov["window"] = {"from": lo, "to": end.isoformat(), "days": len(dates)}
    return dates, rows, cov
def _meta(cov, timezone="UTC"):
    return {"tz": str(timezone), "window": cov["window"], "coverage": cov,
            "flipped_in_correlations": sorted(FLIPPED),
            "note": "deterministic output; correlation is not causation; "
                    "all numbers computed by health.py, none by the LLM"}
def _sparse(dates, rows):
    return [dict(date=d, **{k: v for k, v in rows[d].items() if v is not None})
            for d in dates]
def build_daily_frame(c, days, *, clock, timezone="UTC", medication_aliases=None):
    dates, rows, cov = _daily_frame(
        c, days, clock=clock, medication_aliases=medication_aliases,
    )
    return {"meta": _meta(cov, timezone), "days": _sparse(dates, rows)}

def _add_features(dates, rows):
    """Derived fields raw graphs hide: trailing rolling means (r3 needs >=2,
    r7 needs >=3 non-null days), day-over-day deltas, yesterday's value
    (_lag1), and the configured medication dose-regime run each day belongs to."""
    series = {f: [rows[d].get(f) for d in dates]
              for f in set(FEATURE_BASE) | set(LAG1_FIELDS)}
    for f in FEATURE_BASE:
        vals = series[f]
        for i, d in enumerate(dates):
            for w, mn, tag in ((3, 2, "_r3"), (7, 3, "_r7")):
                win = [v for v in vals[max(0, i - w + 1):i + 1] if v is not None]
                if len(win) >= mn:
                    rows[d][f + tag] = _rnd(sum(win) / len(win))
            if i and vals[i] is not None and vals[i - 1] is not None:
                rows[d][f + "_d1"] = _rnd(vals[i] - vals[i - 1])
    for f in LAG1_FIELDS:
        vals = series[f]
        for i, d in enumerate(dates):
            if i and vals[i - 1] is not None:
                rows[d][f + "_lag1"] = vals[i - 1]
    # dose regime: consecutive run of the same daily total (dosed days only)
    prev_total, run = None, 0
    for d in dates:
        mg = rows[d].get("medication_dose_mg")
        if mg is None: continue
        run = run + 1 if mg == prev_total else 1
        prev_total = mg
        label = int(mg) if float(mg).is_integer() else _rnd(mg)
        rows[d]["medication_regime"] = f"medication:{label}"
        rows[d]["medication_regime_day"] = run
    return rows
def features(c, days, *, clock, timezone="UTC", medication_aliases=None):
    dates, rows, cov = _daily_frame(
        c, days, clock=clock, medication_aliases=medication_aliases,
    )
    _add_features(dates, rows)
    return {"meta": _meta(cov, timezone), "days": _sparse(dates, rows)}

def _pillar_of(field):
    base = re.sub(r"_(r3|r7|d1|lag1)$", "", field)
    return PILLARS.get(base), base
def _pearson(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0: return None
    return sxy / (sxx * syy) ** 0.5
def _ranks(vs):
    order = sorted(range(len(vs)), key=lambda i: vs[i])
    ranks = [0.0] * len(vs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vs[order[j + 1]] == vs[order[i]]:
            j += 1
        r = (i + j) / 2 + 1                          # average rank for ties
        for k in range(i, j + 1): ranks[order[k]] = r
        i = j + 1
    return ranks
def _spearman(xs, ys):
    return _pearson(_ranks(xs), _ranks(ys))
def _strength(rho):
    a = abs(rho)
    return ("negligible" if a < 0.1 else "weak" if a < 0.3 else
            "moderate" if a < 0.5 else "strong" if a < 0.7 else "very strong")
def correlate(c, days, *, min_n, top, clock, timezone="UTC", medication_aliases=None):
    """Cross-PILLAR pairwise associations. Pearson + Spearman with n, direction
    and lag tags; pairs under --min-n are suppressed (counted, never shown as
    findings). Data only — interpretation belongs to the coach workflow."""
    if min_n < 3: sys.exit("--min-n must be >= 3")
    dates, rows, cov = _daily_frame(
        c, days, clock=clock, medication_aliases=medication_aliases,
    )
    _add_features(dates, rows)
    fields = sorted({f for d in dates for f in rows[d]
                     if _pillar_of(f)[0] and isinstance(rows[d][f], (int, float))})
    pairs, suppressed, constant = [], 0, 0
    for fa, fb in itertools.combinations(fields, 2):
        pa, ba = _pillar_of(fa); pb, bb = _pillar_of(fb)
        if pa == pb or ba == bb:                    # between-pillar, never self-vs-derived
            continue
        xs, ys = [], []
        for d in dates:
            x, y = rows[d].get(fa), rows[d].get(fb)
            if x is not None and y is not None:
                xs.append(x); ys.append(y)
        if len(xs) < min_n:
            suppressed += 1; continue
        r, rho = _pearson(xs, ys), _spearman(xs, ys)
        if r is None or rho is None:
            constant += 1; continue
        flip = (-1 if ba in FLIPPED else 1) * (-1 if bb in FLIPPED else 1)
        lag = ("_lag1" in fa and fa) or ("_lag1" in fb and fb) or None
        pairs.append({"a": fa, "b": fb, "pillars": [pa, pb], "n": len(xs),
                      "pearson": _rnd(r * flip, 3), "spearman": _rnd(rho * flip, 3),
                      "strength": _strength(rho),
                      "lag": f"{lag} is yesterday's value" if lag else None})
    pairs.sort(key=lambda p: (-abs(p["spearman"]), p["a"], p["b"]))
    return {"meta": _meta(cov, timezone), "min_n": min_n,
         "pairs": pairs[:top], "pairs_total": len(pairs),
         "suppressed_below_min_n": suppressed, "skipped_constant": constant}
def day_signature(c, days, *, min_days, clock, timezone="UTC", medication_aliases=None):
    """Deterministic contrast of green (day_rating 3) vs red (1) days: mean/
    median of every numeric feature on each side, so 'what makes a good day'
    is computed, not eyeballed. Honest refusal when either side is too thin."""
    if min_days < 1: sys.exit("--min-days must be >= 1")
    dates, rows, cov = _daily_frame(
        c, days, clock=clock, medication_aliases=medication_aliases,
    )
    _add_features(dates, rows)
    green = [d for d in dates if rows[d].get("day_rating") == 3]
    red = [d for d in dates if rows[d].get("day_rating") == 1]
    yellow = [d for d in dates if rows[d].get("day_rating") == 2]
    base = {"meta": _meta(cov, timezone), "green_days": len(green), "red_days": len(red),
            "yellow_days_excluded": len(yellow), "min_days_per_side": min_days}
    if len(green) < min_days or len(red) < min_days:
        return {**base, "insufficient_data": True,
             "needed": f">= {min_days} green AND >= {min_days} red rated days"}
    fields = sorted({f for d in green + red for f in rows[d]
                     if f != "day_rating" and _pillar_of(f)[0]
                     and isinstance(rows[d][f], (int, float))})
    sig = []
    for f in fields:
        gv = [rows[d][f] for d in green if rows[d].get(f) is not None]
        rv = [rows[d][f] for d in red if rows[d].get(f) is not None]
        if len(gv) < min_days or len(rv) < min_days:
            continue
        delta = st.mean(gv) - st.mean(rv)
        pooled = st.pstdev(gv + rv)
        sig.append({"field": f, "pillar": _pillar_of(f)[0],
                    "green": {"n": len(gv), "mean": _rnd(st.mean(gv)), "median": _rnd(st.median(gv))},
                    "red": {"n": len(rv), "mean": _rnd(st.mean(rv)), "median": _rnd(st.median(rv))},
                    "delta_mean": _rnd(delta),
                    "effect": _rnd(delta / pooled, 3) if pooled > 0 else None})
    sig.sort(key=lambda s: -(abs(s["effect"]) if s["effect"] is not None else 0))
    return {**base, "signature": sig}
def adherence(c, days, *, clock, timezone="UTC", medication_aliases=None):
    """Follow-through rate + trend + the CONDITIONS that predict a kept vs
    broken day. All computed here; 'partly' counts 0.5 toward the rate and is
    excluded from the kept/broke contrast (it is neither side's evidence)."""
    if not _table_exists(c, "commitments_log"):
        return {"insufficient_data": True, "reason": "no follow-through data logged yet"}
    dates, rows, cov = _daily_frame(
        c, days, clock=clock, medication_aliases=medication_aliases,
    )
    _add_features(dates, rows)
    logged = [(d, rows[d]["word_kept"]) for d in dates if "word_kept" in rows[d]]
    base = {"meta": _meta(cov, timezone), "days_logged": len(logged),
            "window_days": len(dates)}
    if len(logged) < 3:
        return {**base, "insufficient_data": True,
             "needed": ">= 3 whole-day kept/partly/broke logs"}
    vals = [v for _, v in logged]
    counts = {"kept": vals.count(1.0), "partly": vals.count(0.5), "broke": vals.count(0.0)}
    half = len(logged) // 2
    trend = {"first_half_rate": _rnd(st.mean(v for _, v in logged[:half]), 3),
             "second_half_rate": _rnd(st.mean(v for _, v in logged[half:]), 3)} if half >= 2 else None
    # streak: consecutive CALENDAR days ending at the most recent logged day —
    # a gap in logging breaks it (a streak with holes isn't a streak), and so
    # does a 'broke' day.
    streak = 0
    prev = None
    for d, v in reversed(logged):
        if v == 0.0: break
        if prev is not None:
            gap = (date.fromisoformat(prev) - date.fromisoformat(d)).days
            if gap != 1: break
        streak += 1
        prev = d
    # per-commitment rates over the SAME window as the frame
    per = [dict(r) for r in c.execute(
        """SELECT cl.commitment_id, cm.name, COUNT(*) n,
           ROUND(AVG(CASE cl.status WHEN 'kept' THEN 1.0 WHEN 'partly' THEN 0.5
                     ELSE 0.0 END), 3) rate
           FROM commitments_log cl JOIN commitments cm ON cm.id=cl.commitment_id
           WHERE cl.commitment_id != 0 AND cl.date >= ?
           GROUP BY cl.commitment_id ORDER BY rate""", (dates[0],))]
    # conditions: contrast kept vs broke days across the engine's features
    kept_d = [d for d, v in logged if v == 1.0]
    broke_d = [d for d, v in logged if v == 0.0]
    conditions = None
    if len(kept_d) >= 3 and len(broke_d) >= 3:
        conditions = []
        for f in CONDITION_FIELDS:
            kv = [rows[d][f] for d in kept_d if rows[d].get(f) is not None]
            bv = [rows[d][f] for d in broke_d if rows[d].get(f) is not None]
            if len(kv) < 3 or len(bv) < 3: continue
            delta = st.mean(kv) - st.mean(bv)
            pooled = st.pstdev(kv + bv)
            conditions.append({"field": f,
                "kept": {"n": len(kv), "mean": _rnd(st.mean(kv))},
                "broke": {"n": len(bv), "mean": _rnd(st.mean(bv))},
                "delta_mean": _rnd(delta),
                "effect": _rnd(delta / pooled, 3) if pooled > 0 else None})
        conditions.sort(key=lambda s: -(abs(s["effect"]) if s["effect"] is not None else 0))
    return {**base, "rate": _rnd(st.mean(vals), 3), "counts": counts,
         "streak_non_broke": streak, "trend": trend,
         "per_commitment": per,
         "conditions": conditions if conditions is not None else
             f"insufficient data (need >= 3 kept AND >= 3 broke days; have {len(kept_d)}/{len(broke_d)})"}
COVERAGE_CATALOG = [
  {"field": "day_rating", "target_pct": 90, "how": "one tap, dashboard/Telegram, evenings", "effort": 1, "weight": 5,
   "unlocks": "the green-vs-red day signature — without rated days 'what makes a good day' cannot be computed at all"},
  {"field": "word_kept", "target_pct": 90, "how": "one tap: kept/partly/broke, evenings", "effort": 1, "weight": 5,
   "unlocks": "follow-through rate + the conditions that predict keeping your word (the self-integrity goal is unmeasurable without it)"},
  {"field": "medication_dose_mg", "target_pct": 80, "how": "tell the coach dose+time when you take it", "effort": 1, "weight": 5,
   "unlocks": "dose/timing vs focus, rebound, and sleep for discussion with a clinician"},
  {"field": "bp_sys", "target_pct": 40, "how": "cuff reading, ~3x/week (quick-log)", "effort": 2, "weight": 5,
   "unlocks": "descriptive blood-pressure context alongside a configured medication"},
  {"field": "focus", "target_pct": 90, "how": "quick-log 1-5, evenings", "effort": 1, "weight": 4,
   "unlocks": "sleep→focus and dose→focus links for a user-selected outcome"},
  {"field": "chk_energy_pm", "target_pct": 60, "how": "one tap 'energy now' in the afternoon", "effort": 1, "weight": 4,
   "unlocks": "wear-off/afternoon-crash detection — daily ratings average it away"},
  {"field": "sl_bedtime_min", "target_pct": 90, "how": "log bedtime (or wearable sleep import)", "effort": 1, "weight": 4,
   "unlocks": "bedtime vs next-day everything — the most controllable upstream lever"},
  {"field": "tr_volume_kg", "target_pct": 90, "how": "log sets in the Training tab / Hevy", "effort": 1, "weight": 4,
   "unlocks": "training→mood/sleep lags and progression vs recovery"},
  {"field": "tr_mean_rpe", "target_pct": 40, "how": "add RPE when logging sets", "effort": 1, "weight": 3,
   "unlocks": "effort-adjusted training load — distinguishes a heavy day from a long one"},
  {"field": "nut_protein_g", "target_pct": 90, "how": "eat/log-food from the freezer menu", "effort": 2, "weight": 4,
   "unlocks": "protein vs recovery/strength — the recomposition pillar's only input"},
  {"field": "caffeine_mg", "target_pct": 90, "how": "mention coffees in the evening check-in", "effort": 1, "weight": 3,
   "unlocks": "separates caffeine from configured-medication effects"},
  {"field": "alcohol_units", "target_pct": 90, "how": "mention drinks in the evening check-in", "effort": 1, "weight": 3,
   "unlocks": "alcohol vs sleep quality/HRV — a classic hidden confounder"},
  {"field": "weight_kg", "target_pct": 14, "how": "scale, ~weekly", "effort": 2, "weight": 3,
   "unlocks": "recomposition trend context (waist/photos stay the headline)"},
  {"field": "water_ml", "target_pct": 90, "how": "+250/+500 taps", "effort": 1, "weight": 2,
   "unlocks": "hydration vs headaches/energy (weak signal, cheapest capture)"},
]
def data_coverage(c, days, *, clock, timezone="UTC", medication_aliases=None):
    """Rank the data NOT being captured by insight-per-effort. Coverage %s are
    computed from the live frame; weights/effort are editorial constants (see
    COVERAGE_CATALOG). score = (1 - coverage) * weight / effort."""
    dates, rows, cov = _daily_frame(
        c, days, clock=clock, medication_aliases=medication_aliases,
    )
    n_days = len(dates)
    ranked = []
    for item in COVERAGE_CATALOG:
        f = item["field"]
        have = sum(1 for d in dates if rows[d].get(f) is not None)
        coverage = have / n_days if n_days else 0.0
        # attainment: coverage relative to the signal's own target cadence, so
        # a perfect weekly weigh-in (14%) reads as done, not forever-missing.
        attainment = min(1.0, coverage / (item["target_pct"] / 100))
        ranked.append({**item, "days_with_data": have, "window_days": n_days,
                       "coverage_pct": _rnd(100 * coverage, 1),
                       "attainment_pct": _rnd(100 * attainment, 1),
                       "score": _rnd((1 - attainment) * item["weight"] / item["effort"], 3)})
    ranked.sort(key=lambda r: (-r["score"], r["field"]))
    return {"meta": _meta(cov, timezone), "window_days": n_days, "ranked": ranked,
         "note": "score = (1-attainment) * weight / effort; attainment = "
                 "coverage vs the signal's target cadence; weight/effort/"
                 "target are editorial constants, coverage is measured"}
def summary(c, days, *, clock):
    lo = _days_ago(days, clock)
    migrations.require_table(c, "daily_metrics", ("hrv_ms",))
    hrv_col = "hrv_ms"
    q = f"""SELECT ROUND(AVG(resting_hr),1) resting_hr, ROUND(AVG({hrv_col}),1) hrv,
                  ROUND(AVG(sleep_hours),2) sleep_h, ROUND(AVG(steps),0) steps
           FROM daily_metrics WHERE date >= ?"""
    row = dict(c.execute(q, (lo,)).fetchone())
    subj = c.execute("""SELECT ROUND(AVG(focus),1) focus, ROUND(AVG(mood),1) mood, ROUND(AVG(energy),1) energy
            FROM subjective_daily WHERE date >= ?""", (lo,)).fetchone()
    row.update({k: subj[k] for k in subj.keys()})
    return {"window_days": days, "averages": row}

def bp_brief(c, days, drug, *, clock, medication_aliases=None):
    aliases = MEDICATION_ALIASES if medication_aliases is None else medication_aliases
    lo = _days_ago(days, clock)
    vitals = [dict(r) for r in c.execute(
        """SELECT date, time, systolic, diastolic, resting_hr FROM vitals
           WHERE date >= ? ORDER BY date, time""", (lo,))]
    raw_drug = (drug or "").strip().lower()
    names = sorted(aliases) if raw_drug in aliases else [raw_drug]
    ph = ",".join("?" * len(names))
    doses = [dict(r) for r in c.execute(
        f"""SELECT date, ROUND(SUM(dose_mg), 1) AS dose_total_mg FROM meds_log
            WHERE LOWER(drug) IN ({ph}) AND date >= ?
            GROUP BY date ORDER BY date""", (*names, lo))]
    return {"days": days, "drug": drug, "matched_names": names,
            "vitals": vitals, "doses": doses}
