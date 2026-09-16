/* Body page (design v-body; formerly Training, analysis-only): 7-axis
   muscle-balance radar (Current strengths default, Planned and Logged 7d),
   athletic radar, cited strength-ratio pyramid, V-taper gauge, weight trend,
   quarter battery, per-lift progression/PR. Set capture lives in the Telegram
   loop. All numbers come from health.py endpoints — nothing computed here. */
(function () {
  if (typeof echarts === "undefined") return;
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const { pal, tip, lineOpt, gaugeOpt, isoDaysAgo } = window.HermesCharts;
  const charts = {};

  /* task-37 global period dd. Only the time-series trend cards subscribe
     (Weight, Progression, Weight-to-strength): each fetches its FULL series
     once, then view-slices to the selected window client-side. Slicing (not
     server re-windowing) keeps the overall-index baseline honest — "100 = your
     first logged week" stays anchored to the true first week even when the
     window hides it; the range only changes what's on screen, never the maths.
     The radars / ratios / V-taper / quarter / goals are current-state or
     engine-fixed-window analyses with no time axis and don't subscribe. */
  const RANGE_DAYS = { Day: 1, Week: 7, Month: 30, Year: 365, All: null };
  const WINDOW_LABEL = { Day: "past day", Week: "last 7 days", Month: "last 30 days",
    Year: "last year", All: "all time" };
  const bodyRange = () => window.HermesUI.range("body") || "Year";
  function sliceRange(arr, key) {
    const days = RANGE_DAYS[bodyRange()];
    if (days == null) return arr;
    const cut = isoDaysAgo(days);
    // p.week (and other date keys here) is a full ISO YYYY-MM-DD date, so this
    // is a plain string >= comparison — matches the server payload's contract.
    return (arr || []).filter((p) => p[key] >= cut);
  }
  async function getJSON(url) {
    const r = await fetch(url, { credentials: "same-origin" });
    if (r.status === 401) { location.assign("/login"); throw new Error("unauthenticated"); }
    if (!r.ok) { let d = ""; try { d = (await r.json()).error || ""; } catch (_) {} throw new Error(d || "HTTP " + r.status); }
    return r.json();
  }
  /* ---------- progression card ---------- */
  // The picker's first option is the combined view; every other value is a
  // real exercise title. Sentinel can't collide with a lift name.
  const OVERALL = "__overall__";
  function renderProgression(v) { (v === OVERALL) ? loadOverall() : loadProgression(v); }

  // Combined index + weight:strength ratio share one endpoint; fetch once and
  // reuse across the two cards + theme re-renders (same cache pattern as the
  // radar payload below).
  let overallData = null;
  async function fetchOverall() {
    if (!overallData) overallData = await getJSON("/api/training/overall-progress");
    return overallData;
  }

  // Overall strength index across ALL lifts (default Progression view).
  async function loadOverall() {
    const el = document.getElementById("ch-progression");
    const cap = document.getElementById("prog-cap");
    if (charts.prog) charts.prog.dispose();
    charts.prog = echarts.init(el);
    let d;
    try { d = await fetchOverall(); }
    catch (e) { charts.prog.setOption(emptyOpt(e.message)); return; }
    if (!d.index.length) {
      charts.prog.setOption(emptyOpt("Log sets across your lifts to see overall progress"));
      cap.textContent = "Combined strength index across all logged lifts.";
      return;
    }
    const idx = sliceRange(d.index, "week");
    if (!idx.length) {
      charts.prog.setOption(emptyOpt(`No lifts logged in the ${WINDOW_LABEL[bodyRange()]}`));
      cap.textContent = "Combined strength index across all logged lifts.";
      return;
    }
    const P = pal();
    charts.prog.setOption({
      grid: { left: 44, right: 16, top: 26, bottom: 26 },
      tooltip: Object.assign(tip(P), { trigger: "axis", confine: true,
        valueFormatter: (v) => (v == null ? "—" : v) }),
      xAxis: { type: "time", axisLine: { lineStyle: { color: P.grid } },
               axisLabel: { color: P.muted, fontSize: 10, hideOverlap: true }, axisTick: { show: false } },
      yAxis: { type: "value", scale: true, name: "index", splitNumber: 4,
               nameTextStyle: { color: P.muted, fontSize: 10 },
               splitLine: { lineStyle: { color: P.grid } }, axisLabel: { color: P.muted, fontSize: 10 } },
      series: [{ name: "strength index", type: "line", smooth: true, symbolSize: 6,
        data: idx.map(p => [p.week, p.value]),
        lineStyle: { width: 2.4, color: P.accent, cap: "round" }, itemStyle: { color: P.accent },
        areaStyle: { color: P.accent, opacity: 0.14 } }],
    });
    cap.textContent = `Overall strength index across ${d.exercise_count} lift`
      + `${d.exercise_count === 1 ? "" : "s"}. 100 = your starting strength — each lift is `
      + "normalized to its own first week, then averaged (best e1RM per week, Epley).";
  }

  // Weight-to-strength ratio: combined e1RM ÷ body weight, its own card.
  async function loadWSR() {
    const el = document.getElementById("ch-wsr");
    const cap = document.getElementById("wsr-cap");
    if (charts.wsr) charts.wsr.dispose();
    charts.wsr = echarts.init(el);
    let d;
    try { d = await fetchOverall(); }
    catch (e) { charts.wsr.setOption(emptyOpt(e.message)); return; }
    if (!d.has_weight) {
      charts.wsr.setOption(emptyOpt("No body weight logged yet — relative strength needs your weight"));
      cap.textContent = "Relative strength = combined e1RM ÷ body weight.";
      return;
    }
    if (!d.wsr.length) {
      charts.wsr.setOption(emptyOpt("No lifts overlap a body-weight record yet"));
      cap.textContent = "Relative strength = combined e1RM ÷ body weight.";
      return;
    }
    const wsr = sliceRange(d.wsr, "week");
    if (!wsr.length) {
      charts.wsr.setOption(emptyOpt(`No records in the ${WINDOW_LABEL[bodyRange()]}`));
      cap.textContent = "Relative strength = combined e1RM ÷ body weight.";
      return;
    }
    const P = pal();
    charts.wsr.setOption({
      grid: { left: 44, right: 16, top: 26, bottom: 26 },
      tooltip: Object.assign(tip(P), { trigger: "axis", confine: true }),
      xAxis: { type: "time", axisLine: { lineStyle: { color: P.grid } },
               axisLabel: { color: P.muted, fontSize: 10, hideOverlap: true }, axisTick: { show: false } },
      yAxis: { type: "value", scale: true, name: "e1RM ÷ kg", splitNumber: 4,
               nameTextStyle: { color: P.muted, fontSize: 10 },
               splitLine: { lineStyle: { color: P.grid } }, axisLabel: { color: P.muted, fontSize: 10 } },
      series: [{ name: "weight:strength", type: "line", smooth: true, symbolSize: 6,
        data: wsr.map(p => [p.week, p.value]),
        lineStyle: { width: 2.4, color: P.good, cap: "round" }, itemStyle: { color: P.good },
        areaStyle: { color: P.good, opacity: 0.14 } }],
    });
    const last = wsr[wsr.length - 1];
    cap.textContent = `Latest ${last.value}× bodyweight — ${last.total_e1rm} kg combined e1RM `
      + `÷ ${last.weight_kg} kg. Higher = stronger for your size.`;
  }

  /* ---------- Goals card (real Body targets only — no mock rows) ---------- */
  const round1 = (n) => Math.round(n * 10) / 10;
  function goalRow(label, pct, value, color) {
    const row = document.createElement("div"); row.className = "goal-row";
    const l = document.createElement("span"); l.className = "goal-label"; l.textContent = label;
    const bg = document.createElement("div"); bg.className = "barbg";
    const fill = document.createElement("div"); fill.className = "barfill";
    fill.style.width = Math.max(0, Math.min(100, pct)) + "%";
    fill.style.background = color;   // CSS var → auto-recolors on theme change
    bg.appendChild(fill);
    const v = document.createElement("span"); v.className = "goal-val"; v.textContent = value;
    row.append(l, bg, v);
    return row;
  }
  async function loadGoals() {
    const list = document.getElementById("goals-list");
    if (!list) return;
    const rows = [];
    // WCR toward the engine's target (≤ sense: bar full when current ≤ target).
    try {
      const v = (await getJSON("/api/training/vtaper")).result;
      if (v && !v.insufficient_data && v.current && typeof v.current.wcr === "number"
          && typeof v.target_wcr === "number") {
        const cur = v.current.wcr, tgt = v.target_wcr;
        const pct = cur <= tgt ? 100 : (tgt / cur) * 100;
        rows.push(goalRow("WCR → target · waist ÷ chest", pct,
          `${cur} → ${tgt}`, v.at_target ? "var(--good)" : "var(--accent)"));
      }
    } catch (_) {}
    // Athletic axes that have a real owner-set target (score > 0).
    try {
      const a = (await getJSON("/api/training/athletic-radar")).result;
      const label = { strength: "Strength", endurance: "Endurance", speed: "Speed",
                      balance: "Balance", flexibility: "Flexibility" };
      (a.axes || []).forEach(ax => {
        const s = (a.scores || {})[ax] || {};
        if (typeof s.score === "number" && s.score > 0) {
          rows.push(goalRow(`${label[ax] || ax} → target`, s.score,
            `${s.score} / 100`, "var(--accent)"));
        }
      });
    } catch (_) {}
    // Weekly muscle volume vs plan (only when a plan exists).
    try {
      const m = (await getJSON("/api/training/muscle-radar")).result;
      const axes = m.axes || [];
      const logged = axes.reduce((t, g) => t + (m.combined[g] || 0), 0);
      const planned = axes.reduce((t, g) => t + (m.planned[g] || 0), 0);
      if (planned > 0) {
        rows.push(goalRow("Weekly volume vs plan", (logged / planned) * 100,
          `${round1(logged)} / ${round1(planned)} sets`, "var(--good)"));
      }
    } catch (_) {}
    list.replaceChildren();
    if (!rows.length) {
      const p = document.createElement("p");
      p.className = "muted note-txt m0";
      p.textContent = "No body targets set yet — targets come from the engine config.";
      list.appendChild(p);
      return;
    }
    rows.forEach(r => list.appendChild(r));
  }

  /* ---------- per-lift progression chart ---------- */
  const progCache = {};   // exercise → full payload (view-sliced per range)
  async function loadProgression(exercise) {
    const el = document.getElementById("ch-progression");
    const cap = document.getElementById("prog-cap");
    if (charts.prog) charts.prog.dispose();
    charts.prog = echarts.init(el);
    cap.textContent = "Top-set weight and estimated 1-rep max (Epley). ★ marks a new e1RM best.";
    let data = progCache[exercise];
    if (!data) {
      try { data = await getJSON("/api/training/progression?exercise=" + encodeURIComponent(exercise)); }
      catch (e) { charts.prog.setOption(emptyOpt(e.message)); return; }
      progCache[exercise] = data;
    }
    if (!data.points.length) { charts.prog.setOption(emptyOpt("No logged sets for this lift yet")); return; }
    const points = sliceRange(data.points, "date");
    if (!points.length) { charts.prog.setOption(emptyOpt(`No sets for this lift in the ${WINDOW_LABEL[bodyRange()]}`)); return; }
    const e1rm = points.map(p => [p.date, p.e1rm]);
    const top = points.map(p => [p.date, p.top_weight]);
    const prs = points.filter(p => p.pr).map(p => ({ coord: [p.date, p.e1rm], value: "★" }));
    const P = pal();
    charts.prog.setOption({
      grid: { left: 44, right: 16, top: 26, bottom: 26 },
      legend: { top: 0, right: 0, textStyle: { color: P.muted, fontSize: 10 } },
      tooltip: Object.assign(tip(P), { trigger: "axis", confine: true }),
      xAxis: { type: "time", axisLine: { lineStyle: { color: P.grid } },
               axisLabel: { color: P.muted, fontSize: 10, hideOverlap: true }, axisTick: { show: false } },
      yAxis: { type: "value", scale: true, name: "kg", splitNumber: 4,
               splitLine: { lineStyle: { color: P.grid } }, axisLabel: { color: P.muted, fontSize: 10 } },
      series: [
        { name: "e1RM", type: "line", data: e1rm, smooth: true, symbolSize: 6,
          lineStyle: { width: 2.4, color: P.accent, cap: "round" }, itemStyle: { color: P.accent },
          markPoint: { symbol: "pin", symbolSize: 26, data: prs,
            itemStyle: { color: P.warn }, label: { color: "#000", fontSize: 11 } } },
        { name: "top set", type: "line", data: top, smooth: true, symbolSize: 5,
          lineStyle: { width: 1.5, type: "dashed", color: P.good }, itemStyle: { color: P.good } },
      ],
    });
  }

  /* ---------- clickable radar axis names (owner round 2) ----------
     Vendored ECharts 5.6.0 supports `radar.triggerEvent`: turning it on makes
     each indicator's axisName label non-silent (echarts_min.js: isLabelSilent
     = silent || !(triggerEvent || tooltip.show)), so it participates in hit
     testing. zrender's Displayable default is `cursor:"pointer"`, and nothing
     in the radar axisName renderer overrides it, so hover already gets a
     pointer cursor for free — no CSS/DOM needed. click/mouseover/mouseout
     fire on the chart with params.componentType==="radar",
     params.targetType==="axisName", params.name===<label text>. This hits
     ONLY the label glyphs (their actual bounding box), which is more precise
     than the design's wireRadarDrill (a whole-chart DOM angle hit-test) and
     matches "the axis labels themselves are the click target, no new chrome."
     Hover recolors just that one axis's name via the indicator's own `color`
     override (radar.indicator[i].color) — a native per-axis text-style knob,
     patched through chart.setOption (canvas draw, CSP-safe, no inline style).
     hrefFor(axisName) returning null/undefined means "no destination" — in
     that case we skip wiring entirely so hover/cursor never implies a click
     that goes nowhere (never ship a dead label). */
  function wireAxisLabels(chart, indicator, hrefFor) {
    if (!chart || !indicator.some(ind => hrefFor(ind.name))) return;
    let hovered = null;
    const recolor = () => {
      const P = pal();
      chart.setOption({ radar: { indicator: indicator.map(ind => Object.assign(
        {}, ind, { color: ind.name === hovered ? P.accent : P.muted })) } });
    };
    chart.on("mouseover", (p) => {
      if (p.componentType !== "radar" || p.targetType !== "axisName") return;
      if (!hrefFor(p.name) || hovered === p.name) return;
      hovered = p.name; recolor();
    });
    chart.on("mouseout", (p) => {
      if (p.componentType !== "radar" || p.targetType !== "axisName") return;
      if (hovered === null) return;
      hovered = null; recolor();
    });
    chart.on("click", (p) => {
      if (p.componentType !== "radar" || p.targetType !== "axisName") return;
      const href = hrefFor(p.name);
      if (href) location.assign(href);
    });
  }

  /* ---------- muscle balance (7 fixed axes) ---------- */
  let radarData = null;          // engine payload, cached across theme switches
  const muscleSelected = {
    "Current strengths": true,
    "Planned": true,
    "Logged 7d": true,
  };

  async function loadMuscle() {
    const el = document.getElementById("ch-muscle");
    const note = document.getElementById("muscle-note");
    if (charts.muscle) charts.muscle.dispose();
    charts.muscle = echarts.init(el);
    if (!radarData) {
      try { radarData = (await getJSON("/api/training/muscle-radar")).result; }
      catch (e) { charts.muscle.setOption(emptyOpt(e.message)); return; }
    }
    const d = radarData;
    const current = d.current_strengths || {};
    const profiles = [
      {
        name: "Current strengths",
        values: current.distribution_pct || {},
        color: pal().accent,
        width: 2.6,
        opacity: 0.16,
      },
      {
        name: "Planned",
        values: d.planned_distribution_pct || {},
        color: pal().muted,
        width: 1.8,
        opacity: 0.03,
        dash: "dashed",
      },
      {
        name: "Logged 7d",
        values: d.logged_distribution_pct || {},
        color: pal().good,
        width: 2.2,
        opacity: 0.09,
      },
    ];
    const bits = [];
    if (d.unmapped && d.unmapped.length) {
      bits.push("Not on the radar (unmapped muscles): " + d.unmapped.join(", "));
    }
    if (d.unmapped_exercises && d.unmapped_exercises.length) {
      // volume the engine fully dropped (in neither the authored map nor
      // Hevy's tags) — must never be silently invisible
      bits.push("Uncounted exercises (no muscle map yet): " + d.unmapped_exercises.join(", "));
    }
    let strengthStatus;
    if (current.status === "complete") {
      strengthStatus = `latest completed quarterly test: ${current.completed_on} (${current.quarter})`;
    } else if (current.status === "partial") {
      const tested = (current.tested_groups || []).join(", ") || "none";
      const untested = (current.untested_groups || []).join(", ") || "none";
      strengthStatus = `partial ${current.quarter} baseline: ${current.completed_movements}`
        + `/${current.total_movements} priority strength movements complete; `
        + `tested groups: ${tested}; untested groups stay at the centre: ${untested}`;
    } else {
      strengthStatus = current.reason || "no quarterly strength tests yet";
    }
    note.textContent =
      "Each web is relative to its own highest group (100), so the shapes are comparable. " +
      `Current strengths = ${strengthStatus}; Planned = the distribution the current plan ` +
      "is designed to produce; Logged 7d = recent effective sets." +
      (bits.length ? " " + bits.join(" · ") : "");

    const axes = d.axes || [];
    if (!profiles.some(p => axes.some(g => (p.values[g] || 0) > 0))) {
      charts.muscle.setOption(emptyOpt("No muscle-balance data yet"));
      return;
    }
    const indicator = axes.map(g => ({ name: g, max: 100 }));
    const P = pal();
    // Rebind on every ECharts instance; the selected-state object survives
    // theme-driven disposal/recreation.
    charts.muscle.on("legendselectchanged", (event) => {
      Object.assign(muscleSelected, event.selected || {});
    });
    charts.muscle.setOption({
      legend: {
        top: 0, right: 0,
        data: profiles.map(p => p.name),
        selected: muscleSelected,
        textStyle: { color: P.muted, fontSize: 10 },
      },
      tooltip: Object.assign(tip(P), { confine: true }),
      radar: {
        indicator,
        radius: "62%", center: ["50%", "56%"], splitNumber: 4,
        triggerEvent: true,
        // triggerEvent makes the axis-name labels non-silent, which ALSO
        // activates their built-in component-item tooltip (a themed box
        // echoing the label — exactly the "chrome around the label" the
        // owner forbade). Suppress it here: the tooltip handler resolves
        // show from [itemOption, componentModel(radar), …] and the axisName
        // itemOption sets no `show`, so this component-level show:false
        // wins for axis-name hits only — series-point tooltips resolve via
        // the series model (separate code path) and keep working.
        tooltip: { show: false },
        axisName: { color: P.muted, fontSize: 10, fontWeight: 600 },
        splitLine: { lineStyle: { color: P.grid } },
        splitArea: { areaStyle: { color: ["transparent"] } },
        axisLine: { lineStyle: { color: P.grid } },
      },
      series: [{
        type: "radar",
        symbol: "circle", symbolSize: 5,
        data: profiles.map(p => ({
          name: p.name,
          value: axes.map(g => p.values[g] || 0),
          lineStyle: { width: p.width, type: p.dash || "solid", color: p.color },
          itemStyle: { color: p.color },
          areaStyle: { color: p.color, opacity: p.opacity },
        })),
      }],
    });
    // §3b drill-down: axis names navigate to the same /training/muscle/<group>
    // page the removed chip row used to link to (identical destinations).
    wireAxisLabels(charts.muscle, indicator,
      (name) => "/training/muscle/" + encodeURIComponent(name));
  }

  function emptyOpt(msg) {
    return { graphic: { type: "text", left: "center", top: "middle",
      style: { text: msg, fill: cssVar("--text-dim"), fontSize: 13 } } };
  }

  /* ---------- §3c athletic radar (5 axes, 0–100 vs target) ---------- */
  async function loadAthletic() {
    const el = document.getElementById("ch-athletic");
    const note = document.getElementById("athletic-note");
    if (charts.ath) charts.ath.dispose();
    charts.ath = echarts.init(el);
    let d;
    try { d = (await getJSON("/api/training/athletic-radar")).result; }
    catch (e) { charts.ath.setOption(emptyOpt(e.message)); return; }
    const axes = d.axes || [];
    const label = { strength: "Strength", endurance: "Endurance", speed: "Speed",
                    balance: "Balance", flexibility: "Flexibility" };
    const scores = axes.map(a => {
      const s = d.scores[a] || {};
      return (typeof s.score === "number") ? s.score : 0;   // insufficient → 0 (gap)
    });
    const pending = axes.filter(a => typeof (d.scores[a] || {}).score !== "number")
                        .map(a => label[a]);
    const stale = axes.filter(a => (d.scores[a] || {}).stale).map(a => label[a]);
    if (!scores.some(v => v > 0)) {
      charts.ath.setOption(emptyOpt("No athletic tests scored yet.\nSet a target + log a test."));
    } else {
      const P = pal();
      const indicator = axes.map(a => ({ name: label[a], max: 100 }));
      // label text → axis key, so a clicked axis name resolves to its drill URL.
      const keyOf = {}; axes.forEach(a => { keyOf[label[a]] = a; });
      charts.ath.setOption({
        legend: { top: 0, right: 0, textStyle: { color: P.muted, fontSize: 10 } },
        tooltip: Object.assign(tip(P), { confine: true,
          formatter: () => axes.map(a => {
            const s = d.scores[a] || {};
            if (typeof s.score !== "number") return label[a] + ": —";
            return `${label[a]}: ${s.score} / target 100` + (s.stale ? " (stale)" : "");
          }).join("<br>") }),
        radar: {
          indicator,
          radius: "62%", center: ["50%", "56%"], splitNumber: 4,
          // triggerEvent makes the axis-name labels clickable (they now HAVE a
          // destination: the /training/athletic/<axis> drill page this task
          // ships — the round-2 owner flag about inert labels). tooltip.show:
          // false suppresses the component-item tooltip that triggerEvent would
          // otherwise attach to those labels (same pattern as Muscle Balance).
          triggerEvent: true,
          tooltip: { show: false },
          axisName: { color: P.muted, fontSize: 10, fontWeight: 600 },
          splitLine: { lineStyle: { color: P.grid } },
          splitArea: { areaStyle: { color: ["transparent"] } },
          axisLine: { lineStyle: { color: P.grid } },
        },
        series: [{ type: "radar", symbol: "circle", symbolSize: 5,
          data: [
            { name: "current", value: scores,
              lineStyle: { width: 2.4, color: P.accent },
              itemStyle: { color: P.accent },
              areaStyle: { color: P.accent, opacity: 0.22 } },
            // Planned target overlay: the engine's scores are already
            // %-of-target (capped at 100), so per-axis numeric targets live on
            // different unit scales and can't share this 0–100 web. The honest
            // overlay is therefore the uniform target REFERENCE — a 100 ring
            // labelled "target" (100 = at your owner-set target on every axis).
            // Never a fabricated numeric target series.
            // task-50 item 4: this uniform-100 target ring is the "planned"
            // series for this radar — muted dashed, same convention as
            // Muscle Balance's planned overlay above.
            { name: "target", value: axes.map(() => 100),
              lineStyle: { width: 1.5, type: "dashed", color: P.muted },
              itemStyle: { color: P.muted },
              areaStyle: { color: P.muted, opacity: 0.05 } },
          ] }],
      });
      // §3c drill-down: axis names navigate to the per-axis breakdown page.
      wireAxisLabels(charts.ath, indicator,
        (name) => keyOf[name] ? "/training/athletic/" + encodeURIComponent(keyOf[name]) : null);
    }
    const bits = [];
    if (pending.length) bits.push("Awaiting setup: " + pending.join(", "));
    if (stale.length) bits.push("Stale (retest): " + stale.join(", "));
    note.textContent = bits.join(" · ") ||
      "Five test-backed axes · solid = current score, dashed ring = your target (100). "
      + "Click an axis for its breakdown.";
  }

  /* ---------- §3d strength ratios (diverging opposing-bar) ---------- */
  let ratioView = "tested";
  const FLAG_COLOR = () => ({
    in_range: cssVar("--good"), out_of_range: cssVar("--bad"),
    asymmetry: cssVar("--bad"), watch: cssVar("--warn"),
    trend_only: cssVar("--text-dim"), outside_protocol_range: cssVar("--warn"),
    insufficient_data: cssVar("--card-edge"),
  });
  async function loadRatios() {
    const el = document.getElementById("ch-ratios");
    const note = document.getElementById("ratio-note");
    if (charts.ratio) charts.ratio.dispose();
    charts.ratio = echarts.init(el);
    let d;
    try { d = (await getJSON("/api/training/strength-ratios?view=" + ratioView)).result; }
    catch (e) { charts.ratio.setOption(emptyOpt(e.message)); return; }
    const rows = ratioView === "tested" ? ratioRowsTested(d.rows)
                                        : ratioRowsEveryday(d.rows);
    note.textContent = d.caveat || "";
    if (!rows.length) {
      charts.ratio.setOption(emptyOpt(ratioView === "tested"
        ? "No isolation tests logged yet (log the quarterly battery)."
        : "No compound lifts logged yet."));
      return;
    }
    const cats = rows.map(r => r.label);
    const colors = FLAG_COLOR();
    // Population-pyramid: numerator to the LEFT (negative), denominator RIGHT,
    // each as its share of the pair so a row always spans the same width and the
    // split reads as balance. Ratio + target + flag ride on the right label.
    const left = rows.map(r => ({ value: r.ok ? -r.numShare : 0,
      itemStyle: { color: r.ok ? (colors[r.flag] || cssVar("--ch-1")) : cssVar("--card-edge") } }));
    const right = rows.map(r => ({ value: r.ok ? r.denShare : 0,
      itemStyle: { color: cssVar("--ch-2"), opacity: r.ok ? 0.85 : 0.3 } }));
    const P = pal();
    charts.ratio.setOption({
      grid: { left: 150, right: 132, top: 8, bottom: 20 },
      tooltip: Object.assign(tip(P), { trigger: "axis", axisPointer: { type: "shadow" }, confine: true,
        formatter: (p) => { const r = rows[p[0].dataIndex]; return r.tip; } }),
      xAxis: { type: "value", min: -1, max: 1, axisLabel: { show: false },
        splitLine: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
      yAxis: { type: "category", data: cats, inverse: true,
        axisLabel: { color: P.muted, fontSize: 11, width: 142, overflow: "truncate" },
        axisLine: { lineStyle: { color: P.grid } }, axisTick: { show: false } },
      series: [
        { name: "num", type: "bar", stack: "x", data: left, barWidth: "55%",
          itemStyle: { borderRadius: 3 } },
        { name: "den", type: "bar", stack: "x", data: right, barWidth: "55%",
          itemStyle: { borderRadius: 3 },
          label: { show: true, position: "right", color: P.muted, fontSize: 11,
            formatter: (p) => rows[p.dataIndex].rightLabel } },
      ],
    });
  }
  function ratioRowsTested(rows) {
    const out = [];
    (rows || []).forEach(r => {
      // one visual row per side (unilateral pairs give left+right)
      r.sides.forEach(s => {
        // side FIRST so the L/R disambiguator is never truncated off the end
        const label = r.per_side ? `${s.side[0].toUpperCase()} · ${r.num_label} : ${r.den_label}`
                                 : `${r.num_label} : ${r.den_label}`;
        if (s.status === "insufficient_data") {
          out.push({ label, ok: false, flag: "insufficient_data",
            rightLabel: "needs test", tip: `${label}<br>insufficient data` });
          return;
        }
        const tot = s.num_value + s.den_value;
        // band/ideal are null on trend-only pairs (hip flex:ext — no defensible target)
        const protocolValid = s.protocol_valid !== false;
        const repTxt = [s.num_reps, s.den_reps].filter(x => x !== null && x !== undefined)
          .join(" / ");
        const bandTxt = !protocolValid ? "recorded outside preferred 6–8 reps"
          : (r.band ? `${r.band[0] ?? "–"}–${r.band[1] ?? "∞"}` : "trend only");
        const gap = (r.per_side && r.side_gap_flag === "asymmetry")
          ? ` · L/R gap ${Math.round(r.side_gap * 100)}%` : "";
        out.push({ label, ok: true, flag: s.flag,
          numShare: s.num_value / tot, denShare: s.den_value / tot,
          rightLabel: `${s.ratio} (${protocolValid ? (r.ideal ?? "trend") : "baseline*"})`,
          tip: `${label}<br>ratio ${s.ratio} · target ${bandTxt} · <b>${s.flag}</b>`
             + (!protocolValid && repTxt
               ? `<br>e1RM adjusted for reps (${repTxt}); lower-confidence comparison, retest optional`
               : "")
             + `${gap}<br><span class="muted">${r.evidence} — ${r.cite}</span>` });
      });
    });
    return out;
  }
  function ratioRowsEveryday(rows) {
    return (rows || []).map(r => {
      if (r.status === "insufficient_data") {
        return { label: r.label, ok: false, flag: "insufficient_data",
          rightLabel: "no data", tip: `${r.label}<br>insufficient data (heuristic)` };
      }
      const tot = r.num_e1rm + r.den_e1rm;
      const tgt = r.target === null ? "trend only" : `~${r.target}`;
      return { label: r.label, ok: true, flag: r.flag,
        numShare: r.num_e1rm / tot, denShare: r.den_e1rm / tot,
        rightLabel: `${r.ratio} (${tgt})`,
        tip: `${r.label}<br>ratio ${r.ratio} · target ${tgt} · <b>${r.flag}</b>`
           + `<br><span class="muted">heuristic — pattern level, not muscle-specific</span>` };
    });
  }

  /* ---------- §3e quarter-progress strip ---------- */
  async function loadQuarter() {
    const strip = document.getElementById("quarter-strip");
    const miss = document.getElementById("quarter-missing");
    let d;
    try { d = (await getJSON("/api/training/fitness-tests")).result; }
    catch (e) { strip.textContent = e.message; return; }
    strip.textContent = `${d.priority_covered} / ${d.priority_total} priority tests logged in ${d.quarter || "this quarter"}`;
    const names = (d.priority_missing || []).map(m => m.name);
    miss.textContent = names.length ? "Still needed: " + names.join(", ") : "Battery complete — nice.";
  }

  /* ---------- §3f V-taper (WCR gauge — engine-computed, no local math) ---------- */
  async function loadVtaper() {
    const cur = document.getElementById("vtaper-current");
    const note = document.getElementById("vtaper-note");
    const el = document.getElementById("ch-vtaper");
    let d;
    try { d = (await getJSON("/api/training/vtaper")).result; }
    catch (e) { cur.textContent = e.message; return; }
    if (d.insufficient_data) {
      // honest empty state: no gauge over a value that doesn't exist
      if (charts.vtaper) { charts.vtaper.dispose(); charts.vtaper = null; }
      cur.textContent = "No paired waist + chest measurement yet";
      note.textContent = d.note || "";
      return;
    }
    const c = d.current;
    const P = pal();
    if (charts.vtaper) charts.vtaper.dispose();
    charts.vtaper = echarts.init(el);
    // gauge span 0.5–1.0 covers the plausible WCR range; color = engine's
    // own at_target verdict (real target is ≤, from Garza 2017 — never the
    // design mock's "≥ 0.78")
    charts.vtaper.setOption(gaugeOpt({
      value: c.wcr, min: 0.5, max: 1.0, width: 14,
      color: d.at_target ? P.good : P.warn,
      title: "WCR", detailSize: 30,
      fmt: () => `WCR ${c.wcr} · target ≤ ${d.target_wcr}`,
    }));
    const trend = d.trend === null ? "" :
      ` · trend ${d.trend > 0 ? "+" : ""}${d.trend} over ${d.n_measurements} measurements`;
    cur.textContent = `WCR ${c.wcr} · target ≤ ${d.target_wcr}`
      + ` — waist ${c.waist_cm} cm / chest ${c.chest_cm} cm, ${c.date}${trend}`;
    note.textContent = (d.at_target ? "At target. " : "") + d.note;
  }

  /* ---------- weight trend (design v-body Weight card; /api/dash/body) ---------- */
  let weightRows = null;   // full days=all series; view-sliced per range
  async function loadWeight() {
    const el = document.getElementById("ch-weight");
    const cap = document.getElementById("weight-cap");
    if (charts.weight) charts.weight.dispose();
    charts.weight = echarts.init(el);
    if (!weightRows) {
      try { weightRows = (await getJSON("/api/dash/body?days=all")).rows || []; }
      catch (e) { charts.weight.setOption(emptyOpt(e.message)); return; }
    }
    if (!weightRows.some(r => r.weight_kg != null)) {
      charts.weight.setOption(emptyOpt("No weight logged yet"));
      cap.textContent = "";
      return;
    }
    const pts = sliceRange(weightRows.filter(r => r.weight_kg != null), "date");
    if (!pts.length) {
      charts.weight.setOption(emptyOpt(`No weight logged in the ${WINDOW_LABEL[bodyRange()]}`));
      cap.textContent = "";
      return;
    }
    const P = pal();
    charts.weight.setOption(lineOpt(
      pts.map(r => r.date),
      [{ n: "kg", d: pts.map(r => r.weight_kg), c: P.accent, area: true }],
      null, null,
      { grid: { left: 6, right: 10, top: 12, bottom: 20, containLabel: true } }));
    const last = pts[pts.length - 1], first = pts[0];
    const delta = Math.round((last.weight_kg - first.weight_kg) * 10) / 10;
    cap.textContent = `${last.weight_kg} kg · ${delta > 0 ? "+" : ""}${delta} kg / ${pts.length > 1 ? WINDOW_LABEL[bodyRange()] : "1 point"}`;
  }

  /* ---------- muscle figure (§3f) ---------- */
  // Paints health.py `muscle-map` verbatim through the shared MuscleFigure
  // component. Colors are CSS-class driven, so themechange needs no rebuild.
  let mfFront = null, mfBack = null;
  let mfLens = "activation", mfSide = "combined";
  let mfSeq = 0;   // fetch token: rapid lens/side toggling must never let an
                   // out-of-order response paint stale data (review finding)
  function mfEmpty(el, msg) {
    const p = document.createElement("p");
    p.className = "muted note-txt m0"; p.textContent = msg;
    el.replaceChildren(p);
  }
  function mfClearChrome() {
    // a failed load must not leave the PREVIOUS lens's chrome beside the error
    // message (review finding) — that includes the pain lens's loop card, which
    // a failed pain RE-fetch (re-clicking the active Pain tab) would otherwise
    // strand next to the figure error
    ["mf-legend-f", "mf-legend-b"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.replaceChildren();
    });
    const note = document.getElementById("mf-note");
    if (note) note.hidden = true;
    const loopCard = document.getElementById("mf-loop");
    if (loopCard) loopCard.hidden = true;
    const mobCard = document.getElementById("mf-mobility");
    if (mobCard) mobCard.hidden = true;
  }
  // §3g/§3h — the pain physio-loop + mobility READ-ONLY renderers moved to the
  // shared js/physio-render.js (window.HermesPhysio) so the Body cockpit AND the
  // Pain/Mobility workspace pages paint the same engine payload from ONE source
  // (Task C). Call sites below use window.HermesPhysio.renderPhysioLoop/Mobility.

  async function loadMuscleMap() {
    const wrapF = document.getElementById("mf-front");
    const wrapB = document.getElementById("mf-back");
    if (!wrapF || !wrapB) return;
    if (!window.MuscleFigure) {
      // vendored body-muscles failed to load/parse — say so, never an
      // eternal "Loading…" (review: every other card surfaces failures)
      [wrapF, wrapB].forEach((el) => mfEmpty(el, "Body-map component failed to load (vendored asset missing?)."));
      return;
    }
    // the Combined|L/R sub-toggle belongs to the strength-balance lens only;
    // the physio-loop card belongs to the pain lens only
    const sideRow = document.getElementById("mf-side");
    if (sideRow) sideRow.hidden = mfLens !== "strength-balance";
    const loopCard = document.getElementById("mf-loop");
    if (loopCard && mfLens !== "pain") loopCard.hidden = true;
    const mobCard = document.getElementById("mf-mobility");
    if (mobCard && mfLens !== "mobility") mobCard.hidden = true;
    const seq = ++mfSeq;
    let data;
    try {
      let url = "/api/training/muscle-map?lens=" + encodeURIComponent(mfLens);
      if (mfLens === "strength-balance") url += "&side=" + encodeURIComponent(mfSide);
      data = (await getJSON(url)).result;
    } catch (e) {
      if (seq !== mfSeq) return;      // a newer toggle superseded this fetch
      [wrapF, wrapB].forEach((el) => mfEmpty(el, e.message));
      mfClearChrome();
      return;
    }
    if (seq !== mfSeq) return;        // stale response — never paint it
    // Region click is LENS-DEPENDENT (Task C): EXPLORE lenses (activation,
    // strength-balance) keep drilling into the muscle group's sub-page; REPORT
    // lenses (pain, mobility) open the dedicated workspace pre-selected to the
    // clicked SVG region. mfLens is read at CLICK time (module scope), so the
    // once-created closure always branches on the CURRENT lens. Joints (pain,
    // group:null) route by id too — pain is often joint pain.
    const onRegionClick = (id, name, reg) => {
      // Mouse listeners exist on every drawn path, including neutral
      // silhouette parts. Only a region in the current payload may navigate,
      // matching the role/tabindex gate used for keyboard interaction.
      if ((mfLens === "pain" || mfLens === "mobility") && reg) {
        location.assign("/training/" + mfLens + "?region=" + encodeURIComponent(id));
      } else if (reg && reg.group) {
        location.assign("/training/muscle/" + encodeURIComponent(reg.group));
      }
    };
    // Under REPORT lenses every painted region is actionable (so any spot can be
    // pointed at, incl. joints with no group); EXPLORE lenses keep the "only a
    // region with a group is a button" rule. Governs role/tabindex + cursor in
    // muscle-figure.js; the mouse click listener fires regardless.
    const actionable = (reg, payload) =>
      (payload.lens === "pain" || payload.lens === "mobility") ? true : !!(reg && reg.group);
    if (!mfFront) mfFront = window.MuscleFigure.create(wrapF, { view: "front", interactive: true, onRegionClick, actionable });
    if (!mfBack) mfBack = window.MuscleFigure.create(wrapB, { view: "back", interactive: true, onRegionClick, actionable });
    mfFront.update(data);
    mfBack.update(data);
    window.MuscleFigure.legend(document.getElementById("mf-legend-f"), data);
    window.MuscleFigure.legend(document.getElementById("mf-legend-b"), data);
    const note = document.getElementById("mf-note");
    const bits = [];
    if (data.lens === "pain") {
      window.HermesPhysio.renderPhysioLoop(data);
      bits.push("color = max logged pain (0–10 NRS) · last " + data.window_days + "d · joints included");
    } else if (data.lens === "mobility") {
      window.HermesPhysio.renderMobility(data);
      bits.push("color = cited mobility-norm status · last " + data.window_days + "d · grey = untested");
    } else if (data.lens === "strength-balance") {
      bits.push("cited tested ratios only · grey = untested (quarterly isolation battery)");
      // honest gaps, engine-worded: e.g. the ER:IR internal rotators are
      // undrawn and can never be colored (design rule: never on the chest)
      (data.unrepresentable || []).forEach((u) => bits.push(u.note));
    } else {
      bits.push("effective sets · last " + data.window_days + "d · authored map first");
      if ((data.coarse_fallback || []).length)
        bits.push("coarse-tag approx: " + data.coarse_fallback.join(", "));
      if ((data.mobility_excluded || []).length)
        bits.push("mobility (not volume): " + data.mobility_excluded.join(", "));
      if ((data.unmapped_exercises || []).length)
        bits.push("not counted (no map): " + data.unmapped_exercises.join(", "));
      if ((data.unrepresented_sub_regions || []).length)
        bits.push("no drawn region: " + data.unrepresented_sub_regions.join(", "));
    }
    note.textContent = bits.join(" — ");
    note.hidden = false;
  }

  /* ---------- boot ---------- */
  async function boot() {
    // independent of the exercises fetch below — start it first so the
    // figure's paint isn't serialized behind an unrelated round-trip
    loadMuscleMap();
    const picker = document.getElementById("prog-exercise");
    let others = [];
    try { others = (await getJSON("/api/training/exercises")).exercises || []; } catch (_) {}
    const all = [...new Set(others)];
    // Build options with the DOM, not innerHTML — an exercise title with a
    // quote would break out of an <option value="…"> attribute (review
    // finding); textContent/value are safe. "Overall — all lifts" leads and is
    // the default (owner request): the combined index, then each single lift.
    const overallOpt = document.createElement("option");
    overallOpt.value = OVERALL; overallOpt.textContent = "Overall — all lifts";
    picker.replaceChildren(overallOpt, ...all.map(x => {
      const o = document.createElement("option"); o.value = x; o.textContent = x; return o;
    }));
    picker.value = OVERALL;
    picker.addEventListener("change", () => renderProgression(picker.value));
    renderProgression(OVERALL);
    loadWSR();
    loadGoals();

    window.HermesUI.onTab("ratio", (tab) => { ratioView = tab; loadRatios(); });
    // lens selector is live from birth (review: a tab must never toggle
    // without changing the data) — one lens today, more land per phase
    window.HermesUI.onTab("mflens", (tab) => { mfLens = tab; loadMuscleMap(); });
    window.HermesUI.onTab("mfside", (tab) => { mfSide = tab; loadMuscleMap(); });
    // range dd re-windows only the time-series trend cards (data is cached, so
    // this re-slices, no refetch). Radars/ratios/gauge/quarter/goals don't
    // subscribe — current-state or engine-fixed windows, no time axis.
    window.HermesUI.onRange("body", () => {
      renderProgression(document.getElementById("prog-exercise").value || OVERALL);
      loadWSR();
      loadWeight();
    });
    loadMuscle();
    loadAthletic();
    loadRatios();
    loadQuarter();
    loadVtaper();
    loadWeight();
  }

  window.addEventListener("themechange", () => {
    renderProgression(document.getElementById("prog-exercise").value || OVERALL);
    loadWSR();
    // Goals bars are CSS-var colored (CSSOM) and re-theme themselves — no rebuild.
    loadMuscle();
    loadAthletic();
    loadRatios();
    loadVtaper();
    loadWeight();
  });
  window.addEventListener("resize", () => Object.values(charts).forEach(c => c && c.resize()));
  boot();
})();
