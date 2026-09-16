/* Nutrition pillar (design v-nutrition). Intake tab (owner-review r4, mocks
   36/37): reordered to the mock (KPI tile row → %-of-target → food quality →
   Calories/Water two-up → Logged today). Task 45 wires the T44 `nutrition-
   targets` engine (app/routes/nutrition.py `/api/nutrition/targets`, a
   bridge passthrough) into every "no target yet" surface: loadTargets()
   fetches it ONCE per page load (shared, cached promise — see below) and
   loadToday/loadWater/loadTargetCard/loadCalories/loadGaps all consume that
   same object. The demo DB ships with no `owner_profile` row, so the engine
   replies `insufficient_data` locally by default and every dependent surface
   shows its honest "profile not set" state — that IS the page's correct
   local default, not a bug (populated states are proven separately, see the
   T45 report's screenshots).
   - Food quality (T46): loadFoodQuality() fetches /api/nutrition/coverage
     (the shared T44-targets-sourced scorer, same as the dashboard ring's
     nutrition score) windowed by its own dd->days mapping (see
     COVERAGE_RANGE_DAYS below — the coverage engine only accepts
     7/30/90/365, not this page's 1/"all"). Chip = latest row's
     "score · band"; chart = the score trend; honest-empty otherwise.
   - Calories: real logged kcal always; the gauge/trend/band/phase go real
     once the targets engine is ok. See loadCalories()'s own comment for the
     gauge's one documented deviation (no drawn 100%-marker on the round
     gauge — echarts' gaugeOpt has no linear-track primitive for it).
   - Water: fully real, engine-target-driven now — see loadWater()'s comment
     for why `targets.water_ml.target` is used INSTEAD OF
     scores.water.inputs.target_ml on this page only, and why it stays real
     even when the engine's overall `status` is `insufficient_data`.
   - bandFor(pct, lo, hi) (task 45) is the ONE display-band rule every banded
     value on this page uses (previously triplicated as ad hoc `bandColor`
     copies): good inside [lo,hi]; warn within 12.5 points outside either
     bound; else bad. kcal/carbs/fat use the engine's ±10% maintenance band
     (as a %, derived once from kcal's band_low/band_high ÷ kcal target —
     carbs_g/fat_g carry no band of their own); protein/micros are floors
     (lo=100, hi=Infinity — never "too much"); water keeps its shipped 80/112
     rule, now through the same helper. bandFor returns the band KEY;
     bandVar(...) wraps it as a var() string for DOM fills (fillBar, the
     shared 0–130%-of-target track+dashed-marker renderer, the T41 .tline
     idiom), while chart contexts (the gauges) resolve it via pal() instead —
     a var() string isn't a valid canvas color.
   Header range dd (design `.dd`/HermesUI.range, same as consistency): drives
   loadWater + loadCalories via a whitelisted `days` value the server
   validates; the food-quality window is fixed text until its engine exists.
   The onRange handler only touches Intake elements and is additive — the
   Recipes/Supplements tabs (T33/T34) may subscribe their own onRange later.
   Recipes tab (owner-review r3; T47 pills + real meal tags): freezer as
   recipe cards with a −/+ portion stepper (− eats via /eat, + preps one
   portion via /prep — both existing validated bridge writes), a real
   Today's-gaps banner (loadGaps — real kcal/protein/water gap lines once
   the engine is ok, one honest sentence otherwise; see its own comment), a
   meal-type dd filtering on the REAL recipes.meal_type column (tagged via
   the coach's recipe-tag; untagged → "All meals" only, honest empty per
   untagged category), a filter-pills row (All · Closes today's gaps · High
   protein — see renderMenu's comment; the gaps ranking comes from
   /api/nutrition/recipe-gaps, refetched on each selection so mid-day
   deficits stay fresh), and a "+ log a batch" prep form. The Log-by-grams
   card was removed by owner order in T47 (the /api/nutrition/log-food
   route + subcommand stay — validated path, agent-reachable; only the UI
   control went). The page range dd does NOT window this tab (freezer =
   current state, not a series).
   Supplements tab (owner-review r3): three cards — the product list
   (unchanged, current-state, does NOT subscribe to the page dd), a real
   Adherence trend from the new /api/nutrition/supplements/adherence
   (windowed by the header dd, same as Water/Calories), and Interactions &
   notes rendering each product's real `notes` column (never the mock's
   fabricated interaction pairs).
   "% of personal target — today" (task 39 scaffold, wired live in T45): see
   loadTargetCard()'s own comment for the full breakdown — every row (macros,
   the six micros, Water, the PHASE indicator) is now real once the targets
   engine is ok, honestly pending otherwise. */
(function () {
  if (typeof echarts === "undefined" || !window.HermesCharts) return;
  const { fetchJSON, pal, gaugeOpt, lineOpt } = window.HermesCharts;
  // dd label -> the whitelisted `days` the intake endpoints accept, and a
  // short window caption. "All" maps to the server's all-time branch.
  const RANGE_DAYS = { Day: "1", Week: "7", Month: "30", Year: "365", All: "all" };
  const WINDOW_LABEL = { Day: "past day", Week: "last 7 days", Month: "last 30 days",
    Year: "last year", All: "all time" };
  // Food quality's own dd->days mapping (T46): /api/nutrition/coverage only
  // accepts the whitelist 7/30/90/365 (not RANGE_DAYS' 1/"all"). Day falls
  // back to 7 (a 1-day trend line has nothing to trend); All clamps to this
  // endpoint's max window (365) rather than a true all-time query.
  const COVERAGE_RANGE_DAYS = { Day: "7", Week: "7", Month: "30", Year: "365", All: "365" };
  const range = () => window.HermesUI.range("nutrition") || "Week";
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const toastWrap = document.getElementById("toast-wrap");
  const esc = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; };
  const charts = {};

  function toast(msg, kind) {
    const el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = msg;
    toastWrap.appendChild(el);
    requestAnimationFrame(() => el.classList.add("show"));
    setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 250); }, 3400);
  }
  async function postJSON(url, body) {
    const r = await fetch(url, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify(body) });
    if (r.status === 401) { location.assign("/login"); throw new Error("session expired"); }
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) throw new Error(d.error || "HTTP " + r.status);
    return d;
  }

  function chart(id) {
    const el = document.getElementById(id);
    if (charts[id]) charts[id].dispose();
    charts[id] = echarts.init(el);
    return charts[id];
  }
  function empty(id, label) {
    chart(id).setOption({ graphic: { type: "text", left: "center", top: "middle",
      style: { text: label, fill: pal().muted, fontSize: 12 } } });
  }

  /* ------- T45: the targets engine, shared/cached, and the display rules
     every consumer bands its numbers with. ------- */

  // Exact honest wording (T45 brief, verbatim) for when the targets engine
  // reports anything other than status "ok" — most commonly
  // insufficient_data because no owner_profile row exists yet (the demo DB's
  // default). LONG is the one-time banner text; SHORT is reused everywhere
  // compact (tile sub-lines, goal-row values, captions).
  const PROFILE_MISSING_LONG = "Targets need your profile — height, sex & birth date are " +
    "set via the coach (profile-set).";
  const PROFILE_MISSING_SHORT = "profile not set";
  const titleCase = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  // user's fixed meal-log schedule (Q13) — a plain client constant, not a
  // schema field or engine output, so it never claims to be measured data.
  const LOG_SCHEDULE = 3;

  // One fetch of /api/nutrition/targets per page load, shared by
  // loadToday/loadWater/loadTargetCard/loadCalories/loadGaps. Cached (never
  // re-fetched on themechange, only re-rendered); a bridge error resolves to
  // a synthetic {status:"error"} so every consumer's `status !== "ok"` check
  // renders the honest state instead of throwing.
  let targetsPromise = null;
  function loadTargets() {
    if (!targetsPromise) {
      targetsPromise = fetchJSON("/api/nutrition/targets")
        .then((d) => d.result)
        .catch(() => ({ status: "error" }));
    }
    return targetsPromise;
  }

  // Task 61 fix C: same cached-promise shape as loadTargets above, for the
  // two other endpoints multiple loaders on this page each fetched their own
  // copy of on a single page load (/api/nutrition/today up to 4x,
  // /api/dash/today up to 2x). Unlike loadTargets, these do NOT catch errors
  // here — every existing caller below has its own try/catch or
  // Promise.allSettled handling tuned to how IT renders a failure, so the
  // wrapper just shares one raw (possibly-rejecting) promise and lets each
  // caller's existing handling fire exactly as before. Reset after an
  // eat/prep write (see step()/bp-btn below) so the post-write refresh reads
  // the new totals instead of page-load-stale data.
  let nutritionTodayPromise = null;
  function loadNutritionToday() {
    if (!nutritionTodayPromise) nutritionTodayPromise = fetchJSON("/api/nutrition/today");
    return nutritionTodayPromise;
  }
  let dashTodayPromise = null;
  function loadDashToday() {
    if (!dashTodayPromise) dashTodayPromise = fetchJSON("/api/dash/today");
    return dashTodayPromise;
  }
  function resetTodayCaches() { nutritionTodayPromise = null; dashTodayPromise = null; }

  // The ONE display-band rule every banded value on this page uses (task 45
  // — replaces three near-identical ad hoc copies): good inside [lo, hi];
  // warn within 12.5 points outside either bound; else bad. These are
  // DISPLAY bands echoing the engine's maintenance band / floor targets —
  // not a clinical claim. Returns the band KEY ("good"/"warn"/"bad"), not a
  // resolved color, because this page draws bands in two different contexts
  // that need two different color forms: bandVar() below for DOM fills
  // (CSSOM var() strings auto-recolor on theme change, no re-render needed)
  // and pal()[bandFor(...)] for ECharts canvases (a var() string is not a
  // valid canvas fillStyle — those already re-render on "themechange").
  function bandFor(pct, lo, hi) {
    if (pct >= lo && pct <= hi) return "good";
    if (pct >= lo - 12.5 && pct <= hi + 12.5) return "warn";
    return "bad";
  }
  function bandVar(pct, lo, hi) { return "var(--" + bandFor(pct, lo, hi) + ")"; }

  // The one shared 0–130%-of-target track + dashed 100%-marker renderer (the
  // T41 .tline idiom) — every banded row/tile on this page draws through
  // this instead of hand-rolling the same three DOM nodes. pct === null
  // clears the track to the honest empty state (no fill, no marker) rather
  // than drawing a fabricated 0%.
  function fillBar(barId, pct, color) {
    const bg = document.getElementById(barId);
    if (!bg) return;
    bg.replaceChildren();
    if (pct == null) return;
    const fill = document.createElement("div"); fill.className = "barfill";
    fill.style.width = Math.max(0, Math.min(130, pct)) / 130 * 100 + "%";
    fill.style.background = color;
    bg.appendChild(fill);
    const tl = document.createElement("div"); tl.className = "tline";
    tl.style.left = (100 / 130 * 100) + "%";
    bg.appendChild(tl);
  }

  /* ------- Food quality (T46): target-coverage score -- % of macro+micro
     targets hit that day, from the SAME scorer/targets source as the
     dashboard ring's nutrition score (/api/nutrition/coverage ->
     health.py's _nutrition_day_score). Chip = today's (latest row's)
     "score · band"; chart = the per-day score trend over this card's own
     dd window (COVERAGE_RANGE_DAYS). Honest-empty when the engine reports
     insufficient_data (no profile, or no nutrition data in the window) --
     one message covers both, matching the brief's exact wording; it does
     NOT distinguish "no profile" vs "no data" the way the %-of-target card
     does, since this card has no per-field rows to hang a second message
     on. */
  // T55 flag 4: engine band -> the existing .flag band classes (the same
  // good/warn/bad -> in/bd/out mapping every banded chip uses — see
  // consistency_habits.js FLAG_CLASS); no new CSS classes.
  const FQ_FLAG = { good: "in", warn: "bd", bad: "out" };
  async function loadFoodQuality() {
    const r = range();
    const chip = document.getElementById("nFqChip");
    let d;
    try { d = await fetchJSON(`/api/nutrition/coverage?days=${COVERAGE_RANGE_DAYS[r]}`); }
    catch (_) {
      chip.className = "chip muted"; chip.textContent = "—";
      empty("nFoodQual", "Food-quality trend unavailable"); return;
    }
    const cov = d.result || {};
    if (cov.status !== "ok" || !cov.rows || !cov.rows.length) {
      chip.className = "chip muted"; chip.textContent = "—";
      empty("nFoodQual", "No nutrition data in this window yet — the coverage score starts " +
        "when you log a recipe or import a Cronometer day.");
      return;
    }
    const rows = cov.rows;
    const latest = rows[rows.length - 1];
    // band-colored chip (T55 flag 4): unknown band falls back to the neutral
    // chip rather than an uncolored .flag
    chip.className = FQ_FLAG[latest.band] ? "flag " + FQ_FLAG[latest.band] : "chip muted";
    chip.textContent = `${latest.score} · ${latest.band}`;
    chart("nFoodQual").setOption(lineOpt(
      rows.map((r2) => r2.date.slice(5)),
      [{ n: "score", d: rows.map((r2) => r2.score), c: pal().accent, area: true }],
      0, 100, { animation: false }));
  }

  /* ------- Intake: today's kcal (Calories card meta) + KPI tile row +
     logged-today list. All today-scoped, so this doesn't re-run on a range
     change. Calories/Protein tiles go real once the targets engine is ok
     (band-colored fill + dashed 100% marker via fillBar/bandFor); Water is
     handled entirely by loadWater (same fetch feeds both). ------- */
  async function loadToday() {
    const t = await loadTargets();
    const ok = t.status === "ok";
    const phaseLabel = ok ? titleCase(t.phase.phase) : "";
    try {
      const d = await loadNutritionToday();
      const kcal = Math.round(d.totals.kcal || 0);
      const protein = Math.round(d.totals.protein_g || 0);
      document.getElementById("nCalMeta").textContent = ok
        ? `${kcal.toLocaleString()} kcal today · of ${Math.round(t.targets.kcal.target).toLocaleString()} kcal · ${phaseLabel}`
        : `${kcal.toLocaleString()} kcal today · ${PROFILE_MISSING_SHORT}`;

      document.getElementById("nKpiKcal").textContent = kcal.toLocaleString();
      document.getElementById("nKpiProtein").textContent = protein.toLocaleString();
      if (ok) {
        const kt = t.targets.kcal, pt = t.targets.protein_g;
        const loPct = kt.band_low / kt.target * 100, hiPct = kt.band_high / kt.target * 100;
        const kcalPct = kcal / kt.target * 100;
        fillBar("nKpiKcalBar", kcalPct, bandVar(kcalPct, loPct, hiPct));
        document.getElementById("nKpiKcalSub").textContent =
          `of ${Math.round(kt.target).toLocaleString()} kcal · ${phaseLabel}`;
        const proteinPct = protein / pt.target * 100;
        fillBar("nKpiProteinBar", proteinPct, bandVar(proteinPct, 100, Infinity));
        document.getElementById("nKpiProteinSub").textContent = `of ${Math.round(pt.target)} g · ${phaseLabel}`;
      } else {
        fillBar("nKpiKcalBar", null, null);
        document.getElementById("nKpiKcalSub").textContent = PROFILE_MISSING_SHORT;
        fillBar("nKpiProteinBar", null, null);
        document.getElementById("nKpiProteinSub").textContent = PROFILE_MISSING_SHORT;
      }

      // Log coverage: real count vs the user's fixed schedule constant —
      // plain 0–100% fill, no band judgment (there's no "too much" here).
      const nItems = d.entries.length;
      document.getElementById("nKpiItems").textContent = nItems;
      const itemsBar = document.getElementById("nKpiItemsBar");
      itemsBar.replaceChildren();
      const itemsFill = document.createElement("div"); itemsFill.className = "barfill";
      itemsFill.style.width = Math.min(1, nItems / LOG_SCHEDULE) * 100 + "%";
      itemsFill.style.background = "var(--accent)";
      itemsBar.appendChild(itemsFill);

      const ul = document.getElementById("nEntries");
      if (!d.entries.length) { ul.innerHTML = '<li class="dim">Nothing logged today.</li>'; return; }
      ul.replaceChildren(...d.entries.map((e) => {
        const li = document.createElement("li");
        li.textContent = `${e.food_name} — ${Math.round(e.grams)} g` +
          (e.kcal != null ? ` · ${Math.round(e.kcal)} kcal · ${Math.round(e.protein_g || 0)} g protein` : "");
        return li;
      }));
    } catch (_) { /* db unavailable -> leave the em dash */ }
  }

  /* ------- Calories: real gauge/trend once the targets engine is ok; the
     honest no-target state otherwise. Gauge = 0–130% of target, band-colored
     via bandFor, detail text = real kcal/target pair, plus a drawn 100%
     target tick (T55 flag 4 — the second-series marker below). Trend = real
     daily intake with a maintenance-band shaded region (markArea across
     band_low..band_high) + a target markLine, once ok. ------- */
  async function loadCalories() {
    const r = range();
    const P = pal();
    const t = await loadTargets();
    const ok = t.status === "ok";
    const kt = ok ? t.targets.kcal : null;
    let kcalToday = null;
    try { kcalToday = Math.round((await loadNutritionToday()).totals.kcal || 0); } catch (_) { /* gauge falls back honest */ }

    if (ok && kcalToday != null) {
      const pct = Math.round(kcalToday / kt.target * 100);
      const loPct = kt.band_low / kt.target * 100, hiPct = kt.band_high / kt.target * 100;
      const gOpt = gaugeOpt({
        value: Math.min(130, pct), min: 0, max: 130, width: 12,
        color: P[bandFor(pct, loPct, hiPct)],
        detail: `${kcalToday.toLocaleString()} / ${Math.round(kt.target).toLocaleString()}`, detailSize: 14,
        fmt: () => `${kcalToday} / ${Math.round(kt.target)} kcal (${pct}%)`,
      });
      // Drawn 100%-of-target marker (T55 flag 4): gaugeOpt's circular arc has
      // no linear-track primitive for the .tline dashed marker the KPI tiles
      // use, so a second, static, silent gauge series adapts that idiom — its
      // axisLine is transparent except a thin ink sliver at exactly 100 of
      // the same 0–130 axis, painting a tick ON the arc that stays visible
      // when the progress arc passes the target (like .tline over a filled
      // bar). pal()-resolved color; re-rendered on themechange with the rest.
      gOpt.series.push({
        type: "gauge", startAngle: 210, endAngle: -30, radius: "98%",
        min: 0, max: 130, silent: true,
        progress: { show: false }, pointer: { show: false },
        axisLine: { lineStyle: { width: 12, color: [
          [99.4 / 130, "transparent"], [100.6 / 130, P.ink], [1, "transparent"],
        ] } },
        axisTick: { show: false }, splitLine: { show: false },
        axisLabel: { show: false }, anchor: { show: false },
        detail: { show: false }, title: { show: false },
        data: [{ value: 100 }],
      });
      gOpt.animation = false;
      chart("nCalMeter").setOption(gOpt);
    } else {
      const gOpt = gaugeOpt({ value: 0, min: 0, max: 100, width: 12,
        color: P.track, detail: "—", detailSize: 22, fmt: () => PROFILE_MISSING_LONG });
      gOpt.animation = false;
      chart("nCalMeter").setOption(gOpt);
    }
    document.getElementById("nCalCap").textContent = ok
      ? `daily intake · ${WINDOW_LABEL[r]} · band = maintenance ±10%`
      : `daily intake · ${WINDOW_LABEL[r]} · ${PROFILE_MISSING_SHORT}`;

    let rows = [];
    try { rows = (await fetchJSON(`/api/nutrition/calories?days=${RANGE_DAYS[r]}`)).rows || []; }
    catch (_) { empty("nCalTrend", "Intake unavailable"); return; }
    if (!rows.length) {
      empty("nCalTrend", "No food logged in this window — appears once you log meals");
      return;
    }
    const opt = lineOpt(
      rows.map((r2) => r2.date.slice(5)),
      [{ n: "kcal", d: rows.map((r2) => r2.kcal), c: P.accent, area: true }],
      0, null, { animation: false });
    if (ok) {
      // maintenance band, soft "good"-toned fill (same reference-range idiom
      // labs.js uses) + a dashed target line in the .tline/.legend-dash ink.
      opt.series[0].markArea = {
        silent: true,
        itemStyle: { color: "color-mix(in srgb," + P.good + " 15%, transparent)" },
        data: [[{ yAxis: kt.band_low }, { yAxis: kt.band_high }]],
      };
      opt.series[0].markLine = {
        silent: true, symbol: "none",
        lineStyle: { color: P.ink, type: "dashed", opacity: .6 },
        data: [{ yAxis: kt.target }],
      };
    }
    chart("nCalTrend").setOption(opt);
  }

  /* ------- Water: value + engine target + gauge % + real trend. T45: the
     target now comes from the targets engine's `targets.water_ml.target`
     (configured baseline + exercise/weather heuristic, see toolkit/health.py) INSTEAD
     OF scores.water.inputs.target_ml. Since T46, scores.water computes from
     the SAME engine target, so every water surface (this page, dashboard,
     consistency) reads one number. Crucially, `water_ml` is the one target T44 always computes even
     when the rest of the engine reports insufficient_data (weight-based, or
     a flat fallback with no weight at all) — so Water stays live here
     regardless of `status`, unlike every other metric on this page, which is
     why this doesn't gate on `t.status === "ok"`. Feeds BOTH the standalone
     Water card and the KPI tile (same fetches, no duplicate requests). ------- */
  async function loadWater() {
    const r = range();
    const [todayR, waterR] = await Promise.allSettled([
      loadDashToday(),
      fetchJSON(`/api/nutrition/water?days=${RANGE_DAYS[r]}`),
    ]);
    const t = await loadTargets();
    const waterMl = todayR.status === "fulfilled" ? todayR.value.water_ml : null;
    const targetMl = t.targets && t.targets.water_ml ? t.targets.water_ml.target : null;
    const rows = waterR.status === "fulfilled" ? waterR.value.rows : [];

    const meta = document.getElementById("nWaterMeta");
    const capEl = document.getElementById("nWaterCap");
    const P = pal();
    capEl.textContent = `hydration vs target · ${WINDOW_LABEL[r]}`;
    if (waterMl == null || targetMl == null) {
      meta.textContent = targetMl != null
        ? (waterMl == null ? `not logged today · ${targetMl} ml target` : "")
        : "";
      empty("nWaterMeter", "no data");
    } else {
      const pct = Math.round((waterMl / targetMl) * 100);
      meta.textContent = `${Math.round(waterMl).toLocaleString()} / ${targetMl.toLocaleString()} ml · ` +
        (pct >= 100 ? "target met" : `${(targetMl - Math.round(waterMl)).toLocaleString()} ml short`);
      // animation off: this chart is populated by an async fetch well after
      // page load, so ECharts' ~1s entrance animation has nothing to sync
      // against and just delays the gauge/line reaching their final state.
      const gOpt = gaugeOpt({
        value: Math.min(130, pct), min: 0, max: 130, width: 12,
        color: P[bandFor(pct, 80, 112)], detail: pct + "%", detailSize: 18,
        fmt: () => `${Math.round(waterMl)} / ${targetMl} ml (${pct}%)`,
      });
      gOpt.animation = false;
      chart("nWaterMeter").setOption(gOpt);
    }

    // Water KPI tile (r4/T45): fully real — value in L, engine target, band
    // fill + dashed 100% marker, all from the fetches above.
    const kWater = document.getElementById("nKpiWater");
    const kWaterU = document.getElementById("nKpiWaterU");
    const kWaterSub = document.getElementById("nKpiWaterSub");
    if (waterMl == null || targetMl == null) {
      kWater.textContent = "—";
      kWaterU.textContent = "";
      kWaterSub.textContent = targetMl != null
        ? `not logged today · ${(targetMl / 1000).toFixed(1)} L target`
        : "hydration vs target";
      fillBar("nKpiWaterBar", null, null);
    } else {
      const pct = Math.round((waterMl / targetMl) * 100);
      kWater.textContent = (waterMl / 1000).toFixed(1);
      kWaterU.textContent = `/ ${(targetMl / 1000).toFixed(1)} L`;
      kWaterSub.textContent = pct >= 100
        ? `${pct}% · target met`
        : `${pct}% · ${((targetMl - waterMl) / 1000).toFixed(1)} L short`;
      fillBar("nKpiWaterBar", pct, bandVar(pct, 80, 112));
    }

    if (!rows.length) {
      empty("nWaterTrend", "No water logged yet — appears once you log water");
    } else {
      chart("nWaterTrend").setOption(lineOpt(
        rows.map((r2) => r2.date.slice(5)),
        [{ n: "ml", d: rows.map((r2) => r2.water_ml), c: P.accent, area: true }],
        0, null, { animation: false }));
    }
  }

  /* ------- "% of personal target — today" (task 39 scaffold, wired in
     T45): every row real once the targets engine is ok; a single honest
     banner (#nTgtBanner) + "profile not set" per row otherwise — except
     Water, which stays real regardless (see loadWater's comment for why).
     Micro rows use a Cronometer daily total when available and otherwise a
     deterministic total from today's logged recipe portions. Fibre has no
     configured numeric target yet. This is "today", not a range series, so
     unlike Water/Calories it does NOT subscribe to the header dd. */
  function macroRow(prefix, name, amount, unit, ok, pct, lo, hi, target) {
    const l = document.getElementById(prefix + "Label");
    const v = document.getElementById(prefix);
    if (!l || !v) return;
    l.textContent = amount != null ? `${name} — ${Math.round(amount)} ${unit} logged` : name;
    if (!ok) { v.textContent = PROFILE_MISSING_SHORT; fillBar(prefix + "Bar", null, null); return; }
    v.textContent = `${Math.round(pct)}% · target ${Math.round(target)}${unit}`;
    fillBar(prefix + "Bar", pct, bandVar(pct, lo, hi));
  }
  function microRow(prefix, label, amount, unit, ok, target, targetUnit) {
    const l = document.getElementById(prefix + "Label");
    const v = document.getElementById(prefix);
    if (!l || !v) return;
    l.textContent = amount != null ? `${label} — ${Math.round(amount * 10) / 10} ${unit} logged` : label;
    if (!ok) { v.textContent = PROFILE_MISSING_SHORT; fillBar(prefix + "Bar", null, null); return; }
    if (target == null) { v.textContent = "target not configured"; fillBar(prefix + "Bar", null, null); return; }
    if (amount == null) { v.textContent = "no nutrient data today"; fillBar(prefix + "Bar", null, null); return; }
    const pct = amount / target * 100;
    v.textContent = `${Math.round(pct)}% · target ${target}${targetUnit}`;
    fillBar(prefix + "Bar", pct, bandVar(pct, 100, Infinity));
  }
  async function loadTargetCard() {
    const t = await loadTargets();
    const ok = t.status === "ok";
    const banner = document.getElementById("nTgtBanner");
    if (banner) banner.hidden = ok;

    let totals = {}, micros = {};
    try {
      const d = await loadNutritionToday();
      totals = d.totals || {}; micros = d.micros || {};
    } catch (_) { /* rows fall back to their honest-pending state below */ }

    if (ok) {
      const kt = t.targets.kcal, pt = t.targets.protein_g, ct = t.targets.carbs_g, ft = t.targets.fat_g;
      const loPct = kt.band_low / kt.target * 100, hiPct = kt.band_high / kt.target * 100;
      macroRow("nTgtCal", "Calories", totals.kcal, "kcal", true, totals.kcal / kt.target * 100, loPct, hiPct, kt.target);
      macroRow("nTgtProtein", "Protein", totals.protein_g, "g", true, totals.protein_g / pt.target * 100, 100, Infinity, pt.target);
      macroRow("nTgtCarbs", "Carbs", totals.carbs_g, "g", true, totals.carbs_g / ct.target * 100, loPct, hiPct, ct.target);
      macroRow("nTgtFat", "Fat", totals.fat_g, "g", true, totals.fat_g / ft.target * 100, loPct, hiPct, ft.target);
    } else {
      macroRow("nTgtCal", "Calories", totals.kcal, "kcal", false);
      macroRow("nTgtProtein", "Protein", totals.protein_g, "g", false);
      macroRow("nTgtCarbs", "Carbs", totals.carbs_g, "g", false);
      macroRow("nTgtFat", "Fat", totals.fat_g, "g", false);
    }

    const microTargets = {};
    if (ok) (t.targets.micros || []).forEach((m) => { microTargets[m.nutrient] = m; });
    [["nTgtVitD", "Vit D", "vitamin_d"], ["nTgtMg", "Magnesium", "magnesium"],
     ["nTgtFe", "Iron", "iron"], ["nTgtZn", "Zinc", "zinc"],
     ["nTgtOmega", "Omega-3", "omega3_epa_dha"]].forEach(([prefix, label, key]) => {
      const logged = micros[key];
      const mt = microTargets[key];
      microRow(prefix, label, logged ? logged.amount : null, logged ? logged.unit : "",
        ok, mt ? mt.target : null, mt ? mt.unit : "");
    });
    // Fibre is carried directly on nutrition_log and has no configured
    // numeric target yet.
    const fibreLogged = micros.fibre ? micros.fibre.amount : totals.fiber_g;
    const fibreUnit = micros.fibre ? micros.fibre.unit : "g";
    microRow("nTgtFibre", "Fibre", fibreLogged, fibreUnit, ok,
      microTargets.fibre ? microTargets.fibre.target : null, "g");

    // Water: engine target, real even when the rest of the engine is
    // insufficient_data (see loadWater's comment).
    const waterTarget = t.targets && t.targets.water_ml ? t.targets.water_ml.target : null;
    let waterMl = null;
    try { waterMl = (await loadDashToday()).water_ml; } catch (_) { /* honest dash below */ }
    const waterVal = document.getElementById("nTgtWater");
    if (waterMl != null && waterTarget != null) {
      const pct = Math.round((waterMl / waterTarget) * 100);
      fillBar("nTgtWaterBar", pct, bandVar(pct, 80, 112));
      waterVal.textContent = pct + "%";
    } else {
      fillBar("nTgtWaterBar", null, null);
      waterVal.textContent = waterTarget != null ? "not logged today" : "—";
    }

    // PHASE indicator (read-only — see nutrition.html: the buttons are
    // permanently non-interactive). Only toggles which one reads "pressed"
    // + updates the caption; nothing here is a live control.
    const seg = { cut: document.getElementById("nPhaseCut"),
                  maintain: document.getElementById("nPhaseMaintain"),
                  bulk: document.getElementById("nPhaseBulk") };
    Object.entries(seg).forEach(([key, btn]) => {
      if (!btn) return;
      const active = ok && t.phase.phase === key;
      btn.classList.toggle("on", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
    const captionEl = document.getElementById("nPhaseCaption");
    if (captionEl) {
      captionEl.textContent = !ok
        ? "Phase is set via the coach (profile-set / phase-set)."
        : t.phase.set
          ? `Phase is set via the coach — currently ${titleCase(t.phase.phase)} (since ${t.phase.started}).`
          : "No phase set — treating as Maintain.";
    }
  }

  /* ------- Recipes: Today's-gaps banner (task 45: gated on the targets
     engine instead of scores.nutrition/scores.water). Real kcal/protein/
     water gap lines once the engine is ok, computed from today's logged vs
     the real targets. Without a profile (the demo DB's default — no
     owner_profile row) the water line STAYS real (T55 flag 5 — its target
     is always computed) and one honest sentence covers the pending macro
     gaps. We never claim micro gaps and never re-sort recipes: both would
     need more than this task builds. */
  async function loadGaps() {
    const banner = document.getElementById("nGaps");
    const txt = document.getElementById("nGapsTxt");
    const t = await loadTargets();
    if (t.status !== "ok") {
      // T55 flag 5: water_ml is the one target the engine ALWAYS computes,
      // even when the rest reports insufficient_data (see loadWater's
      // comment) — so the real water-shortfall line still renders here,
      // above the honest line; only the macro gaps are pending on profile.
      const waterTarget = t.targets && t.targets.water_ml ? t.targets.water_ml.target : null;
      let waterMl = null;
      try { waterMl = (await loadDashToday()).water_ml; } catch (_) { /* honest line below */ }
      const gaps = [];
      if (waterTarget != null) {
        if (waterMl == null) gaps.push("water not logged today");
        else if (waterMl < waterTarget) gaps.push(`water (${Math.round(waterTarget - waterMl)} ml short)`);
      }
      banner.classList.toggle("has-gaps", gaps.length > 0);
      const pending = "Protein and calorie gaps need your profile (set via the coach).";
      txt.innerHTML = (gaps.length
        ? `<b class="ink">Today's gaps:</b> ${esc(gaps.join(", "))}.<br>` : "") + esc(pending);
      return;
    }
    const [todayR, nutR] = await Promise.allSettled([
      loadDashToday(), loadNutritionToday(),
    ]);
    const waterMl = todayR.status === "fulfilled" ? todayR.value.water_ml : null;
    const totals = nutR.status === "fulfilled" ? nutR.value.totals : null;

    const gaps = [];
    const waterTarget = t.targets.water_ml.target;
    if (waterMl == null) {
      gaps.push("water not logged today");
    } else if (waterMl < waterTarget) {
      gaps.push(`water (${Math.round(waterTarget - waterMl)} ml short)`);
    }
    if (totals) {
      const pt = t.targets.protein_g, kt = t.targets.kcal;
      if (totals.protein_g != null && totals.protein_g < pt.target) {
        gaps.push(`${Math.round(pt.target - totals.protein_g)} g protein short today`);
      }
      if (totals.kcal != null && totals.kcal < kt.target) {
        gaps.push(`${Math.round(kt.target - totals.kcal)} kcal short today`);
      }
    }
    banner.classList.toggle("has-gaps", gaps.length > 0);
    if (gaps.length) {
      txt.innerHTML = `<b class="ink">Today's gaps:</b> ${esc(gaps.join(", "))}.`;
    } else {
      txt.innerHTML = '<b class="ink">No gaps visible today</b> — on target across the board.';
    }
  }

  /* ------- Recipes: freezer as recipe cards + −/+ stepper -------
     − eats one portion (existing /eat write); + preps one portion (existing
     allowlisted `prep`, via /prep). The details link needs a recipe_id, which
     the bridge `menu` doesn't carry — nameToId (from /recipes) supplies it. */
  let lastMenu = [];                 // most recent /menu inventory items (unfiltered)
  let recipeCatalog = [];            // every recipe definition, including zero stock
  const nameToId = new Map();        // recipe name -> recipe_id (for the drill link)
  // T47 filter pills: "all" (freezer order) | "gaps" (server ranking) |
  // "protein" (client sort by real per-portion protein).
  let activePill = "all";
  // Last /api/nutrition/recipe-gaps result. Refetched every time the gaps
  // pill is selected AND after every eat/prep/batch write while it's active —
  // the ranking is deliberately per-request server-side so deficits shrink
  // as meals are logged through the day (owner wants it honest mid-day).
  let gapsRank = null;

  async function refreshGapsRank() {
    try {
      const d = await fetchJSON("/api/nutrition/recipe-gaps");
      gapsRank = d.result || { status: "error" };
    } catch (_) { gapsRank = { status: "error" }; }
  }

  async function loadMenu() {
    const d = await fetchJSON("/api/nutrition/menu").catch((e) => ({ _err: e.message }));
    lastMenu = (d.result && d.result.menu) || [];
    if (d._err) { document.getElementById("nMenu").innerHTML = `<p class="dim">${esc(d._err)}</p>`; return; }
    if (activePill === "gaps") await refreshGapsRank();   // post-write deficits changed
    renderMenu();
  }

  // dd label -> the real recipes.meal_type value recipe-tag writes.
  const MEAL_TAG = { Breakfast: "breakfast", Lunch: "lunch", Dinner: "dinner", Snacks: "snack" };

  function renderMenu() {
    const list = document.getElementById("nMenu");
    // /menu intentionally contains only positive freezer inventory. Merge it
    // with /recipes so the Recipes page remains a food vault: a valid recipe
    // never disappears merely because its current prepared count is zero.
    const inventoryByName = new Map(lastMenu.map((it) => [it.recipe, it]));
    const catalogItems = recipeCatalog.map((r) => ({
      recipe: r.name,
      portions_available: 0,
      grams_per_portion: r.grams_per_portion,
      kcal_per_portion: null,
      protein_g: null,
      ...(inventoryByName.get(r.name) || {}),
      // The recipe table is authoritative for the explicitly assigned tag.
      meal_type: r.meal_type,
    }));
    const catalogNames = new Set(recipeCatalog.map((r) => r.name));
    const allRecipes = catalogItems.concat(lastMenu.filter((it) => !catalogNames.has(it.recipe)));
    // Meal-type filter on the REAL recipes.meal_type column (T47): untagged
    // recipes (meal_type null) only appear under "All meals" — never guessed
    // from names; an untagged category renders the honest empty below.
    const meal = window.HermesUI.range("mealtype") || "All meals";
    let items = meal === "All meals" ? allRecipes
      : allRecipes.filter((it) => it.meal_type === MEAL_TAG[meal]);
    if (!allRecipes.length) {
      list.innerHTML = '<p class="dim">No recipes yet. Import one from Cronometer via the coach.</p>';
      return;
    }
    // "Closes today's gaps": server-ranked (a PRESENTATION ranking — see the
    // route's docstring). Honest state when the targets engine can't rank:
    // the pill stays selectable, the list is replaced by one plain sentence —
    // never a dead control, never a fake ranking.
    let rank = null;
    if (activePill === "gaps") {
      if (!gapsRank || gapsRank.status !== "ok") {
        list.innerHTML = gapsRank && gapsRank.status === "insufficient_data"
          ? '<p class="dim">Gap ranking needs your profile (set via the coach).</p>'
          : '<p class="dim">Gap ranking unavailable right now.</p>';
        return;
      }
      rank = new Map(gapsRank.recipes.map((g) => [g.recipe_id, g]));
      items = [...items].sort((a, b) => {
        const ga = rank.get(nameToId.get(a.recipe)), gb = rank.get(nameToId.get(b.recipe));
        return (gb ? gb.score : -1) - (ga ? ga.score : -1);   // unranked (no batch weight) last
      });
    } else if (activePill === "protein") {
      // real per-portion protein desc; recipes without computed macros last
      items = [...items].sort((a, b) => (b.protein_g ?? -1) - (a.protein_g ?? -1));
    }
    if (!items.length) {
      list.innerHTML = `<p class="dim">No recipes tagged ${esc(meal.toLowerCase())} yet — ` +
        "tag via the coach (<code>recipe-tag</code>).</p>";
      return;
    }
    list.replaceChildren(...items.map((it) => {
      const g = rank ? rank.get(nameToId.get(it.recipe)) : null;
      return recipeCard(it, g && g.closes && g.closes.length ? g.closes : null);
    }));
  }

  function recipeCard(it, closes) {
    const n = it.portions_available;
    const low = n > 0 && n <= 2;
    const rid = nameToId.get(it.recipe);
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML =
      `<div class="row between wrap"><span class="strong">${esc(it.recipe)}</span>` +
      `<span class="flag ${n === 0 ? "out" : low ? "bd" : "in"}">${n === 0 ? "out" : n + " left"}</span></div>` +
      `<p class="muted micro m0 mt-1">` +
      (it.kcal_per_portion != null ? `${Math.round(it.kcal_per_portion)} kcal · ` : "") +
      (it.grams_per_portion ? `${Math.round(it.grams_per_portion)} g` : "") +
      (it.protein_g != null ? ` · ${Math.round(it.protein_g)} g protein` : "") +
      `</p>` +
      // gaps pill only: which deficits this recipe closes most (top 3, from
      // the server ranking — real, per-request data, never guessed)
      (closes ? `<p class="muted micro m0 mt-1">closes: ${esc(closes.join(", "))}</p>` : "") +
      `<div class="row between wrap mt-2"><div class="row gap-2">` +
      `<button class="step" data-act="minus" type="button" aria-label="eat one portion"${n === 0 ? " disabled" : ""}>−</button>` +
      `<span class="pcount">${n}</span>` +
      `<button class="step" data-act="plus" type="button" aria-label="prep one portion">+</button>` +
      `<span class="muted micro">portions in freezer</span></div>` +
      (rid ? `<a class="muted micro" href="/nutrition/recipe/${encodeURIComponent(rid)}">details →</a>` : "") +
      `</div>`;
    card.querySelector('[data-act="minus"]').addEventListener("click", (ev) => step(ev, it, "/api/nutrition/eat"));
    card.querySelector('[data-act="plus"]').addEventListener("click", (ev) => step(ev, it, "/api/nutrition/prep"));
    return card;
  }

  async function step(ev, it, url) {
    const card = ev.target.closest(".card");
    card.querySelectorAll(".step").forEach((b) => (b.disabled = true));
    try {
      const r = await postJSON(url, { recipe: it.recipe, portions: 1 });
      const res = r.result || {};
      if (url.endsWith("/eat")) {
        const macros = res.kcal != null
          ? `${Math.round(res.kcal)} kcal, ${Math.round(res.protein_g || 0)} g protein`
          : "macros pending — set batch weight";
        toast(`${it.recipe}: ${macros} · ${res.portions_left} left` +
              (res.restock_alert ? " — restock soon!" : ""), "good");
      } else {
        toast(`${it.recipe}: +1 portion in the freezer`, "good");
      }
      await loadMenu();   // rebuild cards (fresh counts) before re-click
      resetTodayCaches();  // eat/prep changed today's totals — don't reuse page-load-stale data
      loadToday(); loadTargetCard(); loadCalories(); loadFoodQuality(); loadGaps();
    } catch (e) {
      toast("Couldn't update: " + e.message, "bad");
      card.querySelectorAll(".step").forEach((b) => (b.disabled = false));
    }
  }

  /* ------- Recipes: recipe list → the prep select + the name→id map -------
     (T47: the Log-by-grams card and its select are gone — see the header
     comment; log-food itself remains a validated agent-reachable path.) */
  async function loadRecipes() {
    try {
      const d = await fetchJSON("/api/nutrition/recipes");
      recipeCatalog = d.recipes;
      nameToId.clear();
      d.recipes.forEach((r) => nameToId.set(r.name, r.recipe_id));
      renderMenu();   // includes zero-stock recipes and supplies detail links
      document.getElementById("bp-recipe").replaceChildren(...d.recipes.map((r) => {
        const o = document.createElement("option"); o.value = r.recipe_id; o.textContent = r.name; return o;
      }));
    } catch (_) {}
  }

  /* ------- Recipes: "+ log a batch" → prep write (adds freezer portions) ------- */
  document.getElementById("nBatchBtn").addEventListener("click", () => {
    const f = document.getElementById("nBatchForm");
    f.hidden = !f.hidden;
    if (!f.hidden) document.getElementById("bp-portions").focus();
  });
  document.getElementById("bp-btn").addEventListener("click", async () => {
    const recipe = document.getElementById("bp-recipe").value;
    const portions = document.getElementById("bp-portions").value.trim();
    const grams = document.getElementById("bp-grams").value.trim();
    if (!recipe || !portions) { toast("Pick a recipe and enter portions", "bad"); return; }
    const btn = document.getElementById("bp-btn");
    btn.disabled = true;
    try {
      const body = { recipe, portions };
      if (grams) body.batch_grams = grams;
      const r = await postJSON("/api/nutrition/prep", body);
      const res = r.result || {};
      toast(`${res.prepped || recipe}: +${res.portions ?? portions} portions in the freezer ✓`, "good");
      document.getElementById("bp-portions").value = "";
      document.getElementById("bp-grams").value = "";
      document.getElementById("nBatchForm").hidden = true;
      resetTodayCaches();  // prep touches freezer stock, not today's totals, but stay consistent
      loadMenu(); loadGaps();
    } catch (e) { toast("Couldn't log batch: " + e.message, "bad"); }
    finally { btn.disabled = false; }
  });
  // Meal-type dd re-filters the already-loaded freezer (no refetch needed).
  window.HermesUI.onRange("mealtype", renderMenu);

  // T47 filter pills: selecting "Closes today's gaps" refetches the ranking
  // (deficits are per-request server-side and shrink as meals are logged);
  // the other pills reorder the already-loaded freezer client-side.
  document.querySelectorAll("#nPills .pill").forEach((b) => {
    b.addEventListener("click", async () => {
      activePill = b.dataset.pill;
      document.querySelectorAll("#nPills .pill").forEach((x) => {
        x.classList.toggle("active", x === b);
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      if (activePill === "gaps") await refreshGapsRank();
      renderMenu();
    });
  });

  /* ------- Supplements: honest-empty list + notes card -------
     One fetch feeds both the list (current-state, no dd) and the
     Interactions & notes card (each product's own real `notes` text). */
  function renderSuppNotes(products) {
    const el = document.getElementById("nSuppNotes");
    const noted = products.filter((p) => (p.notes || "").trim());
    if (!noted.length) {
      el.innerHTML = '<p class="dim">No interaction or timing notes captured yet — log one on a ' +
        "supplement (coach: supplement_products.notes) and it appears here.</p>";
      return;
    }
    el.replaceChildren(...noted.map((p) => {
      const row = document.createElement("div");
      row.className = "row gap-2";
      row.innerHTML = `<span class="gap-dot"></span>` +
        `<span><b class="ink">${esc(p.name)}</b> — ${esc(p.notes)}</span>`;
      return row;
    }));
  }

  async function loadSupplements() {
    const el = document.getElementById("nSupps");
    try {
      const d = await fetchJSON("/api/nutrition/supplements");
      if (!d.products.length) {
        el.innerHTML = '<p class="dim">No supplements configured yet — add one to supplement_products ' +
          "(coach) and today's log will show up here.</p>";
        renderSuppNotes([]);
        return;
      }
      const takenBySupp = new Map(d.today.map((r) => [r.supplement_id, r.taken]));
      el.replaceChildren(...d.products.map((p) => {
        const row = document.createElement("div");
        row.className = "prod";
        const taken = takenBySupp.has(p.supplement_id) ? takenBySupp.get(p.supplement_id) : null;
        const dot = document.createElement("div");
        dot.className = "dotbtn" + (taken ? " done" : "");
        dot.textContent = taken ? "✓" : "";
        const mid = document.createElement("div");
        mid.className = "grow";
        mid.innerHTML = `<div class="strong">${esc(p.name)}</div>` +
          `<div class="muted micro">${esc(p.dose != null ? p.dose + " " + (p.unit || "") : "")}` +
          `${p.schedule ? " · " + esc(p.schedule) : ""}</div>`;
        const chip = document.createElement("span");
        chip.className = "chip muted";
        chip.textContent = taken == null
          ? (takenBySupp.has(p.supplement_id) ? "intake not confirmed today" : "not logged today")
          : taken ? "taken today" : "not taken today";
        row.append(dot, mid, chip);
        return row;
      }));
      renderSuppNotes(d.products);
    } catch (e) { el.innerHTML = `<p class="dim">${esc(e.message)}</p>`; renderSuppNotes([]); }
  }

  /* ------- Supplements: Adherence trend (real, windowed by header dd) -------
     pct = distinct active supplements logged taken that day / count of
     currently-active supplement_products (formula documented server-side in
     nutrition.py). Honest-empty when there are no active products or no log
     rows in the window (both true today on the demo DB). */
  async function loadSupplementsAdherence() {
    const r = range();
    document.getElementById("nAdhWindow").textContent = WINDOW_LABEL[r];
    document.getElementById("nAdhCap").textContent = `taken vs planned · ${WINDOW_LABEL[r]}`;
    let d;
    try { d = await fetchJSON(`/api/nutrition/supplements/adherence?days=${RANGE_DAYS[r]}`); }
    catch (_) { empty("nSuppAdh", "Adherence unavailable"); return; }
    if (!d.active_products) {
      empty("nSuppAdh", "No active supplements configured yet — adherence appears once you add some.");
      return;
    }
    if (!d.rows.length) {
      empty("nSuppAdh", "No taken/not-taken confirmations in this window.");
      return;
    }
    chart("nSuppAdh").setOption(lineOpt(
      d.rows.map((r2) => r2.date.slice(5)),
      [{ n: "%", d: d.rows.map((r2) => r2.pct), c: pal().accent, area: true }],
      0, 100, { animation: false }));
  }

  window.addEventListener("themechange", () => {
    loadWater(); loadCalories(); loadFoodQuality(); loadSupplementsAdherence();
  });
  window.addEventListener("resize", () => Object.values(charts).forEach((c) => c && c.resize()));
  // header range dd -> re-window the range-capable Intake trends (Water/
  // Calories/Food quality) + the Supplements tab's Adherence card. The list
  // card is current-state and deliberately does not subscribe (see
  // nutrition.html comment).
  window.HermesUI.onRange("nutrition", () => {
    loadWater(); loadCalories(); loadFoodQuality(); loadSupplementsAdherence();
  });
  // returning to a tab: its charts may have been re-rendered while hidden
  // (0-width) by a range change on another tab — resize re-reads the box.
  window.HermesUI.onTab("nutri", (tab) => {
    if (tab === "intake" || tab === "supp") Object.values(charts).forEach((c) => c && c.resize());
  });

  loadToday(); loadCalories(); loadWater(); loadFoodQuality(); loadTargetCard();
  loadGaps(); loadMenu(); loadRecipes(); loadSupplements(); loadSupplementsAdherence();
})();
