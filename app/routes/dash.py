"""Dashboard data endpoints (GET, JSON, auth-gated by the app-level gate).

All queries run through db_read (read-only, three layers). Ranges are
whitelisted, never interpolated from raw input. `daily_metrics` is
provenance-tagged with PK (date, source) — rows for Apple and Fitbit coexist
per day, so every row carries `source` and filtering happens client-side.
"""
import statistics

from flask import Blueprint, current_app, jsonify, request

from app import bridge, canon, db_read

bp = Blueprint("dash", __name__, url_prefix="/api/dash")

def _medication_names():
    """Return installation-owned medication labels for the dose chart."""
    primary = str(current_app.config.get(
        "HERMES_PRIMARY_MEDICATION", "medication"
    )).strip().lower()
    aliases = str(current_app.config.get(
        "HERMES_MEDICATION_ALIASES", ""
    )).split(",")
    names = {primary or "medication"}
    names.update(value.strip().lower() for value in aliases if value.strip())
    return tuple(sorted(names))

# "1" (single day) was added in owner-review r3: the range dropdowns now offer
# a "Day" option globally (task-26), so every days-windowed endpoint must honor
# a 1-day window rather than 400 on a live dropdown value.
ALLOWED_DAYS = {"1", "7", "30", "90", "365", "all"}

# Recovery drill sub-pages (task-30). The three daily_metrics columns that get
# their own /recovery/<metric> breakdown page. Each is charted over the page
# range and compared to a PERSONAL BASELINE = the trailing-60-day median of the
# apple-preferred-per-date value (documented, deterministic — never a target we
# invented, never an average across sources). `good_high` = a higher reading is
# the healthier direction. The readiness drill reuses these three as its
# component rows (the roll-up score itself comes from the toolkit `readiness`
# engine via /api/recovery/readiness since round 5).
RECOVERY_METRICS = {
    # "field" for hrv is resolved per-call by _hrv_column_expr() (below) since
    # the physical column depends on DB state (legacy hrv_sdnn-only, migrated
    # both-columns, fresh hrv_ms-only) — "hrv_ms" here is just the canonical
    # documentation name, not a literal SQL identifier used verbatim.
    "hrv":   {"field": "hrv_ms",      "unit": "ms",  "label": "HRV",        "good_high": True,  "dp": 0},
    "rhr":   {"field": "resting_hr",  "unit": "bpm", "label": "Resting HR", "good_high": False, "dp": 0},
    "sleep": {"field": "sleep_hours", "unit": "h",   "label": "Sleep",      "good_high": True,  "dp": 1},
}
# ok/watch/off chips are only defensible for metrics with a clear healthy
# direction vs the personal baseline — HRV (higher better) and RHR (lower
# better). Sleep has no invented target, so it gets descriptive stats, no chip.
CHIP_METRICS = ("hrv", "rhr")
BASELINE_DAYS = 60      # trailing window the personal-baseline median is taken over
BASELINE_MIN_N = 7      # below this the baseline is honestly withheld (too few points)


def _range_or_none():
    v = request.args.get("days", "90")
    return v if v in ALLOWED_DAYS else None


def _date_filter(v):
    """WHERE clause for a whitelisted range. Both branches are fixed literals —
    no user input ever reaches the SQL string. Anchored on the canonical
    configured canonical day (app.canon), never SQLite's UTC date('now')."""
    if v == "all":
        return "", ()
    return "WHERE date >= ?", (canon.days_ago_iso(int(v)),)


def _guard():
    if not db_read.available():
        return jsonify(error="health database not available"), 503
    return None


def _hrv_column_expr():
    """SQL expression for daily_metrics' HRV column, resolved fresh against
    whichever schema layout the live DB actually has: legacy hrv_sdnn-only
    (the demo DB, and any not-yet-migrated server DB), migrated both-columns,
    or fresh hrv_ms-only. COALESCE prefers hrv_ms when both exist (the
    google-health writer's backfill makes them equal for old rows; only new
    fitbit rows populate hrv_ms going forward). db_read.columns() is the
    sanctioned read-only way to introspect a table — the panel's authorizer
    denies PRAGMA — same idiom as body()'s source-column check above."""
    cols = db_read.columns("daily_metrics")
    has_ms, has_sdnn = "hrv_ms" in cols, "hrv_sdnn" in cols
    if has_ms and has_sdnn:
        return "COALESCE(hrv_ms, hrv_sdnn)"
    return "hrv_ms" if has_ms else "hrv_sdnn"


@bp.get("/metrics")
def metrics():
    if (resp := _guard()) is not None:
        return resp
    rng = _range_or_none()
    if rng is None:
        return jsonify(error="days must be one of 1, 7, 30, 90, 365, all"), 400
    where, params = _date_filter(rng)
    # task-52: the Data page's raw-data gallery charts every daily_metrics
    # column, not just the four the original dashboard cards used — adding
    # columns here is additive (existing callers key off resting_hr/hrv_ms/
    # sleep_hours/steps by name and ignore the rest, per review of recovery.js/
    # mind.js/dashboard.js before this change).
    # HRV: resolved to whichever physical column(s) exist, always aliased
    # "hrv_ms" — one stable JSON contract regardless of DB layout (see
    # _hrv_column_expr).
    rows = db_read.query(
        f"SELECT date, source, resting_hr, {_hrv_column_expr()} AS hrv_ms, hr_min, hr_avg, hr_max,"
        "       sleep_hours, steps, active_energy_kcal, basal_energy_kcal,"
        "       exercise_min, distance_km, flights, respiratory_rate,"
        "       spo2_pct, walking_hr_avg"
        f" FROM daily_metrics {where} ORDER BY date",
        params,
    )
    return jsonify(rows=rows)


@bp.get("/body")
def body():
    if (resp := _guard()) is not None:
        return resp
    rng = _range_or_none()
    if rng is None:
        return jsonify(error="days must be one of 1, 7, 30, 90, 365, all"), 400
    where, params = _date_filter(rng)
    # ONE row per date: since the Hevy body sync (§3f) landed, manual and
    # hevy rows can coexist on a day — two same-x points would zigzag the
    # chart. Deterministic pick: manual beats hevy (a manual tape is the
    # owner's correction), then the day's latest row. Same provenance rule
    # as the daily_metrics merge, applied at read time. The panel is
    # read-only and cannot migrate a legacy table, so the source tie-break
    # only applies once the column exists.
    src = ("CASE WHEN COALESCE(source,'manual')='hevy' THEN 1 ELSE 0 END, "
           if "source" in db_read.columns("body_metrics") else "")
    # task-52: added the tape-measurement columns + body_fat_pct for the Data
    # page's "Measurements" and "Body fat %" cards — same one-row-per-date
    # dedup as weight/waist above, so a manual and a Hevy row on the same day
    # never zigzag any of the new lines either.
    rows = db_read.query(
        "SELECT date, weight_kg, waist_cm, chest_cm, arm_cm, thigh_cm,"
        "       hip_cm, neck_cm, body_fat_pct FROM ("
        "  SELECT date, weight_kg, waist_cm, chest_cm, arm_cm, thigh_cm,"
        "         hip_cm, neck_cm, body_fat_pct,"
        f"         ROW_NUMBER() OVER (PARTITION BY date ORDER BY {src}id DESC) rn"
        f"  FROM body_metrics {where}"
        ") WHERE rn = 1 ORDER BY date",
        params,
    )
    return jsonify(rows=rows)


@bp.get("/subjective")
def subjective():
    """Serves the mood/energy/focus trends (sliced client-side), the day-rating
    calendar heatmap, and — since the Mind page (Hi-Fi redesign) — the remaining
    subjective_daily fields it charts. Plain read-only SELECT through db_read.

    `days` is optional and whitelisted (same style as /sleep). A bare call keeps
    the pre-r3 default 365-day window (trends + Mind/Data pages rely on it). The
    range calendars (task-26) pass `days=all` so their "All time" mode is
    genuinely unbounded, not silently capped at a year."""
    if (resp := _guard()) is not None:
        return resp
    rng = request.args.get("days", "365")
    if rng not in ALLOWED_DAYS:
        return jsonify(error="days must be one of 1, 7, 30, 90, 365, all"), 400
    where, params = _date_filter(rng)
    rows = db_read.query(
        "SELECT date, focus, energy, mood, day_rating,"
        "       emotional_regulation, anxiety, motivation, stress,"
        "       brain_dump, notes"
        f" FROM subjective_daily {where} ORDER BY date",
        params,
    )
    return jsonify(rows=rows)


@bp.get("/assessments")
def assessments():
    """User-defined assessment trends for the Mind page.

    Hermes does not ship a named screening instrument or a built-in clinical
    interpretation. Installations decide which scale, if any, they log.
    """
    if (resp := _guard()) is not None:
        return resp
    rows = db_read.query(
        "SELECT date, scale, score, max_score FROM assessments"
        " ORDER BY date, scale",
    )
    return jsonify(rows=rows)


@bp.get("/sleep")
def sleep():
    """Sleep quality/stages for the Recovery page's sleep cards — sleep_log
    is a separate table from daily_metrics.sleep_hours (wearable duration);
    this one holds quality + stage minutes and currently has zero rows on
    the demo DB (Fitbit sleep-stage sync isn't wired up yet). Honest-empty
    until it is, same shape as /api/dash/assessments. Takes the same whitelisted
    `days` range as /metrics (default 365) so the page's "All time" range
    is genuinely unbounded, not silently capped at a year.
    task-52 fix wave: also selects time_asleep_hours (the actual-asleep
    duration, distinct from quality/stages above) for the Data page's own
    line chart — additive column, existing consumers (recovery.js) that
    read fields by name are unaffected."""
    if (resp := _guard()) is not None:
        return resp
    rng = request.args.get("days", "365")
    if rng not in ALLOWED_DAYS:
        return jsonify(error="days must be one of 1, 7, 30, 90, 365, all"), 400
    where, params = _date_filter(rng)
    rows = db_read.query(
        "SELECT date, quality, deep_min, rem_min, light_min, awake_min,"
        "       time_asleep_hours"
        f" FROM sleep_log {where} ORDER BY date",
        params,
    )
    return jsonify(rows=rows)


# ── recovery drill sub-pages (task-30) ──────────────────────────────────────
# Deterministic descriptive stats only (median / min / max / std dev / counts).
# NO scoring engine is invented here — the readiness roll-up score lives in the
# toolkit `readiness` engine (served via /api/recovery/readiness, round 5);
# this module only serves the component rows. All reads are SELECT-only via db_read.

def _fmt(v, dp):
    """Round to the metric's decimal places (0 → int) for display strings."""
    return round(v, dp) if dp else int(round(v))


def _signed(delta, dp):
    """A signed delta string ("+8", "−1.2"), or None when it rounds to zero
    (so callers can say "in line with" instead of a meaningless "−0")."""
    r = _fmt(delta, dp)
    if r == 0:
        return None
    return f"{'+' if r > 0 else '−'}{abs(r)}"


def _metric_rows(field):
    """All non-null (date, source, value) rows for one daily_metrics column,
    date-ordered. Apple and Fitbit rows for the same day are BOTH returned —
    callers keep the two sources as separate chart lines (provenance) and only
    apple-prefer for scalar stats; the two are never averaged into a phantom
    value. `field` is a fixed RECOVERY_METRICS entry — except hrv, resolved by
    _hrv_column_expr() to a guard-derived constant — never user input."""
    return db_read.query(
        f"SELECT date, source, {field} AS v FROM daily_metrics"
        f" WHERE {field} IS NOT NULL ORDER BY date", ())


def _apple_preferred(rows):
    """One value per date, Apple winning over Fitbit — the same deterministic
    per-date pick recovery.js/dashboard use. NOT an average across sources."""
    by = {}
    for r in rows:
        cur = by.get(r["date"])
        if cur is None or (cur[1] != "apple" and r["source"] == "apple"):
            by[r["date"]] = (r["v"], r["source"])
    return [(d, by[d][0]) for d in sorted(by)]


def _window(rows, rng):
    if rng == "all":
        return rows
    cut = canon.days_ago_iso(int(rng))
    return [r for r in rows if r["date"] >= cut]


def _baseline(rows):
    """Personal baseline = median of the trailing-60-day apple-preferred values.
    Returns (median|None, sample_size). Withheld (None) below BASELINE_MIN_N —
    honest: we don't quote a baseline off two data points."""
    cut = canon.days_ago_iso(BASELINE_DAYS)
    vals = [v for d, v in _apple_preferred(rows) if d >= cut]
    if len(vals) < BASELINE_MIN_N:
        return None, len(vals)
    return statistics.median(vals), len(vals)


def _chip(latest, baseline, good_high):
    """ok/watch/off vs the personal baseline: within 2% either way = ok, up to
    10% on the worse side = watch, beyond = off. Deterministic and symmetric."""
    if latest is None or not baseline:
        return None
    worse = (baseline - latest) / baseline if good_high else (latest - baseline) / baseline
    if worse <= 0.02:
        return {"text": "ok", "cls": "in"}
    if worse <= 0.10:
        return {"text": "watch", "cls": "bd"}
    return {"text": "off", "cls": "out"}


def _metric_core(key):
    """Shared per-metric read + baseline used by both the metric drill and the
    readiness component rows."""
    cfg = RECOVERY_METRICS[key]
    # hrv's physical column depends on DB state, so it's resolved per-call
    # rather than read off the (static) cfg dict — see _hrv_column_expr.
    field = _hrv_column_expr() if key == "hrv" else cfg["field"]
    rows = _metric_rows(field)
    pref = _apple_preferred(rows)
    baseline, n_base = _baseline(rows)
    latest = pref[-1][1] if pref else None
    latest_date = pref[-1][0] if pref else None
    chip = _chip(latest, baseline, cfg["good_high"]) if key in CHIP_METRICS else None
    return {"cfg": cfg, "rows": rows, "baseline": baseline, "n_base": n_base,
            "latest": latest, "latest_date": latest_date, "chip": chip}


def _metric_facts(key, core, win):
    """Deterministic factual sentences — deltas and counts, no coaching. `win`
    is the range-windowed rows (for the range-scoped sleep line)."""
    cfg = core["cfg"]; dp = cfg["dp"]; unit = cfg["unit"]
    latest = core["latest"]; baseline = core["baseline"]
    facts = []
    if latest is None:
        return facts
    if baseline is not None:
        s = _signed(latest - baseline, dp)
        if s:
            facts.append(
                f"{cfg['label']} latest is {s} {unit} vs your 60-day median "
                f"({_fmt(latest, dp)} vs {_fmt(baseline, dp)} {unit}).")
        else:
            facts.append(
                f"{cfg['label']} latest is in line with your 60-day median "
                f"({_fmt(latest, dp)} {unit}).")
        last7 = _apple_preferred([r for r in core["rows"]
                                  if r["date"] >= canon.days_ago_iso(7)])
        if last7:
            worse_n = sum(1 for _d, v in last7
                          if (v < baseline if cfg["good_high"] else v > baseline))
            side = "below" if cfg["good_high"] else "above"
            facts.append(
                f"It was {side} your median on {worse_n} of the last {len(last7)} days.")
    if key == "sleep":
        wv = [v for _d, v in _apple_preferred(win)]
        if len(wv) > 1:
            under = sum(1 for v in wv if v < 7)
            facts.append(f"{under} of the {len(wv)} nights in range were under 7 h.")
    return facts


def _neutral_line():
    return "A change from your personal baseline to discuss with your prescriber."


def _metric_detail(key, rng):
    core = _metric_core(key)
    cfg = core["cfg"]; dp = cfg["dp"]; unit = cfg["unit"]
    win = _window(core["rows"], rng)
    # provenance: each source is its own chart line, never merged
    series = {}
    for src in ("apple", "fitbit"):
        pts = [[r["date"], r["v"]] for r in win if r["source"] == src]
        if pts:
            series[src] = pts
    wvals = [v for _d, v in _apple_preferred(win)]
    baseline = core["baseline"]; latest = core["latest"]; chip = core["chip"]
    breakdown = []
    if latest is not None:
        breakdown.append({"name": "Latest", "sub": core["latest_date"] or "",
                          "value": f"{_fmt(latest, dp)} {unit}", "chip": chip})
    if baseline is not None:
        val = f"{_fmt(baseline, dp)} {unit}"
        if latest is not None:
            s = _signed(latest - baseline, dp)
            val += f"  ({s})" if s else "  (in line)"
        breakdown.append({"name": "vs your 60-day median",
                          "sub": f"{core['n_base']}-day sample", "value": val, "chip": None})
    elif latest is not None:
        breakdown.append({"name": "vs your 60-day median",
                          "sub": f"only {core['n_base']} of 60 days logged",
                          "value": "not enough data yet", "chip": None})
    if wvals:
        best, worst = (max(wvals), min(wvals)) if cfg["good_high"] else (min(wvals), max(wvals))
        breakdown.append({"name": "Range average", "sub": f"{len(wvals)} days",
                          "value": f"{_fmt(sum(wvals) / len(wvals), dp)} {unit}", "chip": None})
        breakdown.append({"name": "Best / worst in range", "sub": "apple-preferred",
                          "value": f"{_fmt(best, dp)} / {_fmt(worst, dp)} {unit}", "chip": None})
        if len(wvals) > 1:
            breakdown.append({"name": "Variability", "sub": "std dev in range",
                              "value": f"±{_fmt(statistics.pstdev(wvals), dp)} {unit}", "chip": None})
    advice = _metric_facts(key, core, win)
    if chip and chip["text"] in ("watch", "off"):
        advice.append(_neutral_line())
    header = None
    if latest is not None:
        txt = f"{_fmt(latest, dp)} {unit}"
        if chip:
            txt += " · " + chip["text"]
        header = {"text": txt, "cls": chip["cls"] if chip else ""}
    return {"metric": key, "label": cfg["label"], "unit": unit,
            "empty": latest is None,
            "baseline": (_fmt(baseline, dp) if baseline is not None else None),
            "baseline_label": "60-day median", "series": series,
            "header_chip": header, "breakdown": breakdown, "advice": advice}


def _readiness_detail():
    """Readiness component payload. Since round 5 the roll-up score, gauge and
    'what's dragging it down' chart are REAL and come from the toolkit
    `readiness` engine (/api/recovery/readiness); this endpoint keeps serving
    the component breakdown + facts cards (the same HRV/RHR/sleep math the
    metric drills use). `engine: False` here means THIS payload carries no
    score — the drill fetches the score separately."""
    breakdown = []
    advice = []
    flagged = False
    names = {"hrv": "HRV", "rhr": "Resting HR", "sleep": "Sleep duration"}
    for key in ("hrv", "rhr", "sleep"):
        core = _metric_core(key)
        cfg = core["cfg"]; dp = cfg["dp"]; unit = cfg["unit"]
        if core["latest"] is None:
            continue
        sub = (f"vs 60-day median {_fmt(core['baseline'], dp)} {unit}"
               if core["baseline"] is not None else "no baseline yet")
        breakdown.append({"name": names[key], "sub": sub,
                          "value": f"{_fmt(core['latest'], dp)} {unit}", "chip": core["chip"]})
        advice.extend(_metric_facts(key, core, _window(core["rows"], "30")))
        if core["chip"] and core["chip"]["text"] in ("watch", "off"):
            flagged = True
    if flagged:
        advice.append(_neutral_line())
    return {"metric": "readiness", "engine": False,
            "breakdown": breakdown, "advice": advice}


@bp.get("/recovery-detail")
def recovery_detail():
    """Data for the /recovery/<metric> drill sub-pages (task-30). Composes
    SELECT-only db_read reads of daily_metrics into a deterministic breakdown —
    NO bridge subcommand, NO engine. `metric` is whitelisted; `days` windows the
    trend + range stats (the 60-day baseline is fixed, range-independent). The
    readiness metric returns an honest skeleton (score engine pending)."""
    if (resp := _guard()) is not None:
        return resp
    metric = (request.args.get("metric") or "").strip().lower()
    rng = _range_or_none()
    if rng is None:
        return jsonify(error="days must be one of 1, 7, 30, 90, 365, all"), 400
    if metric == "readiness":
        return jsonify(_readiness_detail())
    if metric not in RECOVERY_METRICS:
        return jsonify(error="unknown metric"), 400
    return jsonify(_metric_detail(metric, rng))


def _habits_ledger_rows(rng):
    """Shared SQL behind /habits-ledger and /habits-ledger-detail (task-35):
    best/logged/done/first/last aggregated over the given whitelisted range,
    with current_streak merged in from the always-unwindowed latest-row
    query (it's a live fact, not a trend — see /habits-ledger docstring).
    Also merges last_done_date (the most recent done=1 row, unwindowed) —
    task-50 item 1: this is what the classifier below uses to tell a live
    streak from an abandoned one; it does not change what current_streak
    displays."""
    where, params = _date_filter(rng)
    rows = db_read.query(
        "SELECT habit,"
        "       MAX(streak)                          AS best_streak,"
        "       COUNT(*)                             AS days_logged,"
        "       SUM(CASE WHEN done THEN 1 ELSE 0 END) AS days_done,"
        "       MIN(date)                            AS first_date,"
        "       MAX(date)                            AS last_date"
        f" FROM habits_log {where} GROUP BY habit ORDER BY habit",
        params,
    )
    current = db_read.query(
        "SELECT habit, streak AS current_streak FROM ("
        "  SELECT habit, streak,"
        "         ROW_NUMBER() OVER (PARTITION BY habit ORDER BY date DESC, id DESC) rn"
        "  FROM habits_log) WHERE rn = 1",
    )
    cur = {r["habit"]: r["current_streak"] for r in current}
    last_done = db_read.query(
        "SELECT habit, MAX(date) AS last_done_date FROM habits_log"
        " WHERE done = 1 GROUP BY habit",
    )
    ld = {r["habit"]: r["last_done_date"] for r in last_done}
    for r in rows:
        r["current_streak"] = cur.get(r["habit"], 0)
        r["last_done_date"] = ld.get(r["habit"])
    return rows


@bp.get("/habits-ledger")
def habits_ledger():
    """Per-habit ledger for the Consistency page: aggregates computed in SQL
    (deterministic, server-side), rendered verbatim by the page. Current
    streak is the newest row's streak value (health.py writes it) and is
    NEVER windowed — it's a live fact ("how long is the run right now"), not
    a trend, so it stays correct regardless of the selected range. The rest
    of the row (best/done/logged/since/last) DOES take the same whitelisted
    `days` range as /metrics (owner-review r2: Consistency page's missing
    range dropdown) — a client can't re-aggregate what this endpoint already
    summed in SQL, so the window has to be applied here, server-side.
    A bare call (no `days`) keeps this endpoint's pre-r2 behavior: ALL rows,
    no WHERE — the param is additive, not a silent default change (review
    finding; note this differs from _range_or_none()'s 90d default)."""
    if (resp := _guard()) is not None:
        return resp
    rng = request.args.get("days", "all")
    if rng not in ALLOWED_DAYS:
        return jsonify(error="days must be one of 1, 7, 30, 90, 365, all"), 400
    return jsonify(rows=_habits_ledger_rows(rng))


# task-35: deterministic per-habit status classification for the Habit-ledger
# drill sub-page (/consistency/habits) — implemented here (not client-side) so
# it's covered by the pytest suite rather than only eyeballed in a browser.
# Exact rule set from the brief, checked in this order (Fragile takes
# precedence over Building/Rebuilding, but never over Established):
#   Established: current_streak >= 21.
#   Fragile:     current_streak == 0 OR adherence_30 < 60%.
#   Rebuilding:  current_streak >= 1 and best_streak >= 21 (had a full run
#                before, lost it, is climbing again).
#   Building:    current_streak >= 1 and best_streak < 21 (still climbing
#                for the first time) — the fallback once none of the above match.
# adherence_30 = days_done / days_logged over a FIXED 30-day window,
# independent of any page range control (this sub-page has no range dd — see
# consistency_habits.html). A habit with zero logs in that window (absent
# from the windowed query entirely) gets adherence_30 = 0.0 — honest "no
# recent signal", never a fabricated None.
# `current_streak` passed in here is the EFFECTIVE (lapse-aware) streak from
# _effective_streak below, not necessarily the raw stored value — see there
# (task-50 item 1).
HABIT_ESTABLISHED_STREAK = 21
HABIT_READY_STREAK = 14          # established product threshold
HABIT_ADHERENCE_FLOOR = 0.6


def _classify_habit(current_streak, best_streak, adherence_30):
    if current_streak >= HABIT_ESTABLISHED_STREAK:
        return "Established"
    if current_streak == 0 or adherence_30 < HABIT_ADHERENCE_FLOOR:
        return "Fragile"
    if best_streak >= HABIT_ESTABLISHED_STREAK:
        return "Rebuilding"
    return "Building"


def _effective_streak(current_streak, last_done_date, today):
    """Lapse-aware streak used ONLY for status classification (task-50 item
    1 / r3 flag 5): health.py's stored `streak` column is a running counter
    that only resets on an explicit done=0 row, so a habit nobody has
    logged AT ALL in weeks keeps its last-written streak forever — an
    abandoned habit could read "Established" indefinitely. A streak only
    counts as live if its most recent done=1 row is today or yesterday
    (same today-or-yesterday grace /care's routine_streak already uses —
    "yesterday" still counts since today's log may not have happened yet);
    otherwise the effective streak for classification is 0. This does NOT
    change what current_streak/best_streak/last_date display — those stay
    the honest stored history, per the brief."""
    if last_done_date in (today, canon.days_ago_iso(1)):
        return current_streak
    return 0


@bp.get("/habits-ledger-detail")
def habits_ledger_detail():
    """Composed data for the /consistency/habits drill sub-page (task-35):
    the same per-habit ledger as /habits-ledger (all-time window, for
    started/best/current), plus a fixed 30-day adherence figure and the
    deterministic status classification above, plus a "ready for a new
    habit?" gate (mirrors the vault's ~14-day phase-set rule). All db_read,
    no bridge, no new tables. No `days` param — the page has no range dd,
    both windows it needs (all-time + 30d) are fixed by the rules, not
    user-selectable."""
    if (resp := _guard()) is not None:
        return resp
    today = canon.today_iso()
    all_rows = {r["habit"]: r for r in _habits_ledger_rows("all")}
    win30 = {r["habit"]: r for r in _habits_ledger_rows("30")}
    rows = []
    eff_streaks = {}   # habit -> effective streak, reused below by the gate
    for habit, r in all_rows.items():
        w = win30.get(habit)
        adherence_30 = (w["days_done"] / w["days_logged"]) if w and w["days_logged"] else 0.0
        eff_streak = _effective_streak(r["current_streak"], r["last_done_date"], today)
        eff_streaks[habit] = eff_streak
        rows.append({
            "habit": habit,
            "current_streak": r["current_streak"],
            "best_streak": r["best_streak"],
            "first_date": r["first_date"],
            "last_date": r["last_date"],
            "adherence_30": adherence_30,
            "status": _classify_habit(eff_streak, r["best_streak"], adherence_30),
        })
    rows.sort(key=lambda r: r["habit"])

    if not rows:
        gate = {"ready": False, "advice": None, "weakest": None}
    else:
        # task-50 fix wave: the streak leg must use the same lapse-aware
        # effective streak as the classifier above, not the raw stored
        # current_streak — otherwise an abandoned habit (streak frozen at
        # its last value, classified Fragile) could still read ready:true
        # here. The adherence leg is unaffected — it's already windowed.
        failing = [r for r in rows if not (eff_streaks[r["habit"]] >= HABIT_READY_STREAK
                                            and r["adherence_30"] >= HABIT_ADHERENCE_FLOOR)]
        if not failing:
            gate = {"ready": True, "weakest": None,
                    "advice": "Every active habit is past ~2 weeks and above 60% adherence — "
                              "a good time to add one."}
        else:
            # weakest = furthest from the gate: lowest effective streak
            # first, lowest adherence_30 as tie-break, habit name last so
            # the pick stays fully deterministic even on an exact tie.
            weakest = sorted(failing, key=lambda r: (eff_streaks[r["habit"]], r["adherence_30"], r["habit"]))[0]
            gate = {"ready": False, "weakest": weakest["habit"],
                    "advice": f"Stabilise {weakest['habit']} first — new habits stick best when "
                              "current ones are past ~2 weeks and above 60% adherence."}

    return jsonify(rows=rows, gate=gate)


@bp.get("/care")
def care():
    """Configurable care routines over the legacy skincare_log and
    skincare_products storage tables. Aggregates are computed in SQL (GROUP BY date, and a
    gaps-and-islands activity streak), same spirit as /habits-ledger — there's
    no precomputed streak column on this table (unlike habits_log, health.py
    never writes one here) so the current care streak is derived from active
    used=1 rows rather than read back. skincare_products has no stock/opened column, so
    a "products low" KPI is honestly omitted rather than fabricated (task-13
    brief)."""
    if (resp := _guard()) is not None:
        return resp
    # product_id order is the configured routine sequence; there is no
    # explicit order column to sort by.
    products = db_read.query(
        "SELECT product_id, slot, brand, product_name, active"
        " FROM skincare_products ORDER BY product_id",
    )
    last_used = db_read.query(
        "SELECT product_id, MAX(date) AS last_used FROM skincare_log"
        " WHERE used = 1 GROUP BY product_id",
    )
    today = canon.today_iso()
    today_log = db_read.query(
        "SELECT product_id, MAX(used) AS used FROM skincare_log"
        " WHERE date = ? GROUP BY product_id", (today,),
    )
    # one row per date: total logged slots that day vs. how many were used —
    # the JS derives both the windowed adherence % and the 3-month calendar
    # (done / partial / missed) from this, client-side, same pattern as
    # Recovery's windowedRows over a fully-fetched series. T55 (owner flag 1):
    # ACTIVE products only, on BOTH sides of the fraction — the exact
    # definition the round-5 engine care score uses (toolkit/health.py
    # scores(), T34 lesson), so the dashboard ring and this page agree; a
    # retired product's historical rows no longer skew the adherence.
    daily = db_read.query(
        "SELECT sl.date AS date, COUNT(*) AS total, SUM(sl.used) AS used"
        " FROM skincare_log sl"
        " JOIN skincare_products sp ON sp.product_id = sl.product_id"
        " WHERE sp.active = 1"
        " GROUP BY sl.date ORDER BY sl.date",
    )
    activity = db_read.query(
        "WITH care_days AS ("
        "  SELECT DISTINCT sl.date FROM skincare_log sl"
        "  JOIN skincare_products sp ON sp.product_id = sl.product_id"
        "  WHERE sl.used = 1 AND sp.active = 1"
        "), islands AS ("
        "  SELECT date, julianday(date) - ROW_NUMBER() OVER (ORDER BY date) AS grp"
        "  FROM care_days"
        ")"
        " SELECT MAX(date) AS streak_end, COUNT(*) AS streak_len"
        " FROM islands GROUP BY grp ORDER BY streak_end DESC LIMIT 1",
    )
    streak_end = activity[0]["streak_end"] if activity else None
    streak_len = activity[0]["streak_len"] if activity else 0
    # a streak only counts if it reaches the present (today or yesterday —
    # yesterday still counts since today's application may not be logged
    # yet); a lapsed run reports 0, not its old length (no-guilt tone).
    current = streak_end in (today, canon.days_ago_iso(1))
    return jsonify(
        products=products,
        last_used={r["product_id"]: r["last_used"] for r in last_used},
        today_used={r["product_id"]: bool(r["used"]) for r in today_log},
        daily=daily,
        routine_streak={"days": streak_len if current else 0, "current": current},
    )


@bp.get("/safety")
def safety():
    """BP + resting-HR readings and configured medication dose totals.

    The medication label is installation configuration, and the dose/vitals
    join remains read-only here.

    `days` is optional and whitelisted (same style as /subjective). A bare call
    keeps the pre-r3 default 365-day window (the Data page + the dashboard rely
    on it). The Mind page passes `days=all` so its "All
    time" mode is genuinely unbounded, not silently capped at a year."""
    if (resp := _guard()) is not None:
        return resp
    rng = request.args.get("days", "365")
    if rng not in ALLOWED_DAYS:
        return jsonify(error="days must be one of 1, 7, 30, 90, 365, all"), 400
    lo = None if rng == "all" else canon.days_ago_iso(int(rng))
    vwhere = "" if lo is None else " WHERE date >= ?"
    vparams = () if lo is None else (lo,)
    vitals = db_read.query(
        "SELECT date, time, systolic, diastolic, resting_hr FROM vitals"
        f"{vwhere} ORDER BY date, time", vparams,
    )
    # meds_log accepts free text. Only installation-configured labels are
    # combined; unrelated medications must never become a phantom dose spike.
    medication_names = _medication_names()
    ph = ",".join("?" * len(medication_names))
    dwhere = "" if lo is None else " AND date >= ?"
    dparams = (*medication_names,) if lo is None else (*medication_names, lo)
    doses = db_read.query(
        "SELECT date, ROUND(SUM(dose_mg), 1) AS dose_total_mg FROM meds_log"
        f" WHERE LOWER(drug) IN ({ph}){dwhere}"
        " GROUP BY date ORDER BY date", dparams,
    )
    return jsonify(
        medication=current_app.config.get(
            "HERMES_PRIMARY_MEDICATION", "medication"
        ),
        vitals=vitals,
        doses=doses,
    )


@bp.get("/env")
def env():
    """Latest weather + air/pollen brief (compact views maintained by health.py)
    for the Data page's "Today outside" card, PLUS (task-52) a windowed
    history of both so the same endpoint can also feed the page's weather/
    air-quality trend charts — one round trip, same `days` whitelist as
    /metrics. `days` is optional (default 365, matching /subjective and
    /safety's pre-existing default) so old callers that never passed it keep
    getting the same latest-only shape they always did, just with two extra
    (never-breaking) history keys."""
    if (resp := _guard()) is not None:
        return resp
    rng = request.args.get("days", "365")
    if rng not in ALLOWED_DAYS:
        return jsonify(error="days must be one of 1, 7, 30, 90, 365, all"), 400
    where, params = _date_filter(rng)
    weather = db_read.query("SELECT * FROM weather_brief ORDER BY date DESC LIMIT 1")
    air = db_read.query("SELECT * FROM air_brief ORDER BY date DESC LIMIT 1")
    weather_history = db_read.query(
        "SELECT date, temp_min_c, temp_max_c, uv_index_max"
        f" FROM weather {where} ORDER BY date", params,
    )
    air_history = db_read.query(
        "SELECT date, european_aqi_mean, european_aqi_max"
        f" FROM air_quality {where} ORDER BY date", params,
    )
    return jsonify(weather=weather[0] if weather else None,
                   air=air[0] if air else None,
                   weather_history=weather_history, air_history=air_history)


@bp.get("/workouts")
def workouts():
    """Cardio/other workouts (Apple Health `workouts` table — distinct from
    Hevy's strength `hevy_sets`) for the Data page's per-type minutes chart.
    Raw rows, windowed by the same whitelisted `days` as /metrics; the JS
    groups by `type` client-side (same split-by-category spirit as the
    provenance apple/fitbit split, just on a different column). Honestly
    empty (rows=[]) until Apple Health workout sync lands — currently unseeded
    on the demo DB, same status as sleep_log/intake."""
    if (resp := _guard()) is not None:
        return resp
    rng = _range_or_none()
    if rng is None:
        return jsonify(error="days must be one of 1, 7, 30, 90, 365, all"), 400
    where, params = _date_filter(rng)
    # `source` is dropped: today there's exactly one workout source (Apple
    # Health), so rows are unambiguous by (date, type). If a second source
    # is ever wired up, this SELECT must add source back and the Data page's
    # per-type grouping must not silently merge two sources into one bar.
    rows = db_read.query(
        "SELECT date, type, minutes, kcal, km FROM workouts"
        f" {where} ORDER BY date", params,
    )
    return jsonify(rows=rows)


@bp.get("/hevy-volume")
def hevy_volume():
    """Daily strength-training volume for the Data page's hevy_sets chart:
    Sigma(weight_kg * reps) per day plus the day's set count, aggregated in
    SQL (same server-side-aggregate style as /habits-ledger — a client can't
    re-derive a SUM the endpoint didn't return). NULL weight/reps (e.g. a
    timed/distance set) contribute 0 to volume_kg but still count toward
    sets, so a day of pure cardio-in-Hevy sets isn't silently dropped."""
    if (resp := _guard()) is not None:
        return resp
    rng = _range_or_none()
    if rng is None:
        return jsonify(error="days must be one of 1, 7, 30, 90, 365, all"), 400
    where, params = _date_filter(rng)
    rows = db_read.query(
        "SELECT date,"
        "       ROUND(SUM(COALESCE(weight_kg, 0) * COALESCE(reps, 0)), 1) AS volume_kg,"
        "       COUNT(*) AS sets"
        f" FROM hevy_sets {where} GROUP BY date ORDER BY date",
        params,
    )
    return jsonify(rows=rows)


@bp.get("/scores")
def scores():
    """Goal scores for the v2 score cards — engine JSON passes through
    verbatim (numbers, bands and insufficient_data states are all
    health.py's; the client only renders)."""
    try:
        return jsonify(ok=True, result=bridge.run("scores", "--days", "90"))
    except bridge.BridgeError as exc:
        return jsonify(ok=False, error=str(exc)), 502


@bp.get("/today")
def today():
    """What's already logged today — so the quick-log seeds itself: the water
    field starts at today's stored total (the +N buttons then accumulate onto
    the real value instead of clobbering it), and the day-rating button shows
    what was already chosen. MUST use the configured canonical day: health.py
    stamps writes with CANON_TZ, so a UTC date('now') read here would seed
    from YESTERDAY's row for ~2h after local midnight — and the water +N
    buttons would then accumulate yesterday's total into today."""
    if (resp := _guard()) is not None:
        return resp
    t = canon.today_iso()
    subj = db_read.query(
        "SELECT day_rating, focus, energy, mood FROM subjective_daily WHERE date = ?", (t,))
    water = db_read.query("SELECT water_ml FROM intake WHERE date = ?", (t,))
    # commitments_log is created lazily by health.py on the first word log —
    # probe for it so a fresh vault doesn't 500 the dashboard.
    word = None
    if db_read.query("SELECT 1 FROM sqlite_master WHERE type='table'"
                     " AND name='commitments_log'"):
        rows = db_read.query(
            "SELECT status FROM commitments_log WHERE date = ? AND commitment_id = 0", (t,))
        word = rows[0]["status"] if rows else None
    return jsonify(
        day_rating=(subj[0]["day_rating"] if subj else None),
        subjective=(subj[0] if subj else None),
        water_ml=(water[0]["water_ml"] if water else None),
        word=word,
    )
