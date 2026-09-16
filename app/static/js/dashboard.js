/* Dashboard (design v-dashboard): greeting + coverage nudge, hero engine
   day-signature, scores strip — a big Consistency gauge arc (open at the
   bottom, owner-review r3) plus five pillar rings (Body · Mind · Nutrition
   · Recovery · External care), mini radars (muscle balance has Current
   strengths / Planned / Logged 7d source toggles), mood/focus trend, habits &
   kept-word.
   Engine JSON is rendered VERBATIM — score, band and insufficient_data all
   come from health.py; all five pillars are live engine scores as of T49
   (Mind, External care) — a pillar still shows an honest dashed "pending"
   ring, never a number, whenever its own insufficient_data is true (not
   enough logged data yet), same as any other ring. Sleep and Water score
   rings moved to Recovery/Nutrition; the vitals stat row was removed
   entirely — both owner-review r2 calls. */
(function () {
  if (typeof echarts === "undefined" || !window.HermesCharts) return;
  const { cssVar, isoDaysAgo, fetchJSON, pal, tip, lineOpt, gaugeOpt } = window.HermesCharts;

  const state = { subjective: [], scores: null, habits: [] };
  const charts = {};

  function chart(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    if (charts[id]) charts[id].dispose();
    charts[id] = echarts.init(el);
    return charts[id];
  }

  function empty(id, label) {
    const c = chart(id);
    if (c) c.setOption({ graphic: { type: "text", left: "center", top: "middle",
      style: { text: label || "No data yet", fill: cssVar("--muted"), fontSize: 13 } } });
  }

  /* ------- greeting + real streak chip ------- */
  (function greet() {
    const h = new Date().getHours();
    const word = h < 5 ? "Good night" : h < 11 ? "Good morning"
               : h < 18 ? "Good afternoon" : "Good evening";
    const name = document.body.dataset.displayName || "OpenHealthAtlas user";
    document.getElementById("dash-greet").textContent = word + ", " + name;
  })();

  function renderChips() {
    const box = document.getElementById("dash-chips");
    box.replaceChildren();
    const s = (state.scores || {}).consistency;
    const streak = s && !s.insufficient_data && (s.inputs || {}).streak;
    if (streak > 0) {
      const c = document.createElement("span");
      c.className = "chip";
      c.textContent = `🔥 ${streak}-day run`;
      box.appendChild(c);
    }
    // XP / levels: no engine yet — deliberately absent rather than fabricated
  }

  /* ------- coverage nudge (real: today's gaps + top coverage item) ------- */
  async function loadNudge() {
    const bits = [];
    try {
      const t = await fetchJSON("/api/dash/today");
      if (t.day_rating == null && !t.subjective) bits.push("today's day rating");
      if (t.water_ml == null) bits.push("water");
    } catch (_) {}
    try {
      const { result } = await fetchJSON("/api/insights/coverage");
      const top = (result.ranked || []).filter((r) => r.attainment_pct < 100)[0];
      if (top) bits.push(`${top.field.replace(/_/g, " ")} (${top.attainment_pct}% of target)`);
    } catch (_) {}
    if (!bits.length) return;
    document.getElementById("nudge-txt").textContent = bits.join(" · ");
    document.getElementById("nudge-card").hidden = false;
  }

  /* ------- hero insight: governed Green-vs-non-Green findings ------- */
  const trunc = (v) => (typeof v === "number" && isFinite(v))
    ? String(parseFloat(v.toPrecision(4))) : String(v);

  const shortHash = (value) => (typeof value === "string" && value.startsWith("sha256:"))
    ? value.slice(7, 19) : "unavailable";

  function findingTiming(component) {
    const lag = component.lag_days || 0;
    const window = component.window_days || 1;
    if (lag === 1 && window === 1) return "Previous day";
    if (lag > 0 && window === 1) return `${lag} days earlier`;
    if (window > 1 && component.transform === "mean") {
      return `${window}-day mean${lag ? ` · lag ${lag}d` : ""}`;
    }
    return lag === 0 ? "Same day" : `Lag ${lag}d`;
  }

  function findingRow(finding) {
    const component = (((finding || {}).exposure || {}).components || [])[0] || {};
    const effect = finding.effect || {};
    const sample = finding.sample || {};
    const estimate = effect.oriented_estimate == null ? effect.estimate : effect.oriented_estimate;
    const interval = effect.oriented_ci95 || effect.ci95 || [];
    const row = document.createElement("div");
    row.className = "green-finding";
    const summary = document.createElement("div");
    summary.className = "green-finding-summary";
    const name = document.createElement("div");
    name.className = "strong note-txt";
    const direction = estimate < 0 ? "Lower" : "Higher";
    name.textContent = `${direction} ${component.display || component.exposure_key || "value"}`;
    const timing = document.createElement("div");
    timing.className = "muted micro";
    timing.textContent = `${findingTiming(component)} · ${component.exposure_key || "registered feature"}`;
    summary.append(name, timing);
    const stats = document.createElement("div");
    stats.className = "green-finding-stats micro";
    const ci = interval.length === 2 ? ` · 95% CI ${trunc(interval[0])} to ${trunc(interval[1])}` : "";
    stats.textContent = `Spearman ${trunc(estimate)}${ci} · n=${sample.complete_n ?? "—"}`;
    const quality = document.createElement("div");
    quality.className = "green-finding-quality micro";
    quality.textContent = `q=${trunc((finding.testing || {}).q)} · ${(finding.stability || {}).status || "stability unverified"}`;
    const evidence = document.createElement("div");
    evidence.className = "muted micro green-finding-evidence";
    const fingerprint = (finding.provenance || {}).evidence_fingerprint;
    evidence.textContent = `Evidence ${shortHash(fingerprint)}`;
    evidence.title = fingerprint || "No evidence fingerprint returned";
    row.append(summary, stats, quality, evidence);
    return row;
  }

  let heroController = null;
  let heroGeneration = 0;
  const heroAction = document.getElementById("hero-analysis-action");
  const heroStatus = document.getElementById("hero-job-status");

  async function loadHero() {
    if (heroController) {
      heroController.abort();
      heroController = null;
      heroGeneration += 1;
      heroAction.textContent = "Resume analysis";
      heroStatus.textContent = "Stopped waiting. The server keeps the analysis; resume whenever you are ready.";
      return;
    }
    const generation = ++heroGeneration;
    heroController = new AbortController();
    heroAction.textContent = "Stop waiting";
    heroStatus.textContent = "Checking analysis…";
    document.getElementById("hero-analysis").hidden = true;
    document.getElementById("hero-empty").hidden = true;
    try {
      const { result, evidence } = await window.HermesAnalysisJobs.wait({
        startURL: "/api/insights/dashboard-green-days/jobs",
        statusURL: (id) => "/api/insights/dashboard-green-days/jobs/" + encodeURIComponent(id),
        storageKey: "hermes.dashboard.analysis-job", signal: heroController.signal,
        onStatus: (status) => {
          if (generation !== heroGeneration) return;
          if (status === "queued" || status === "running") {
            document.getElementById("hero-meta").textContent = status === "queued" ? "queued" : "analyzing…";
            heroStatus.textContent = status === "queued"
              ? "Analysis queued. You can keep using the dashboard."
              : "Analysis is running. You can stop waiting or reload; this job will be kept.";
          }
        },
      });
      if (generation !== heroGeneration) return;
      heroStatus.textContent = "Analysis ready.";
      heroAction.textContent = "Refresh analysis";
      const readiness = result.readiness || {};
      const readinessFeatures = readiness.features || readiness.items || [];
      const outcomeReadiness = readinessFeatures.find((item) => item.feature_key === "subjective.day_rating") || {};
      const dayGate = ((outcomeReadiness.factors || {}).day_rating || {});
      const green = dayGate.green;
      const nonGreen = (dayGate.yellow == null || dayGate.red == null)
        ? null : dayGate.yellow + dayGate.red;
      document.getElementById("hero-meta").textContent =
        green == null || nonGreen == null ? "Green vs non-Green" : `${green} Green · ${nonGreen} non-Green`;
      const eligible = (result.findings || []).filter(
        (finding) => (finding.quality || {}).eligible_for_hypothesis
      );
      const box = document.getElementById("hero-findings");
      box.replaceChildren();
      eligible.forEach((finding) => box.appendChild(findingRow(finding)));
      const empty = document.getElementById("hero-empty");
      const analysis = document.getElementById("hero-analysis");
      if (!eligible.length) {
        analysis.hidden = true;
        empty.textContent = dayGate.green_contrast_pass === false
          ? "There are not yet enough aligned Green and non-Green ratings for this comparison. Keep rating whole days; missing evidence stays missing."
          : "No eligible deterministic association was returned for this window.";
        empty.hidden = false;
        return;
      }
      analysis.hidden = false;
      empty.hidden = true;
      const readinessLine = document.getElementById("hero-readiness");
      if (dayGate.green_contrast_pass && outcomeReadiness.state !== "sufficient") {
        readinessLine.textContent = `This Green vs non-Green comparison passed. Overall day-rating readiness remains ${String(outcomeReadiness.state || "unverified").replace(/_/g, " ")} because the separate Red comparison has only ${dayGate.red ?? "an unverified number of"} Red days.`;
      } else {
        readinessLine.textContent = `Readiness: ${String(outcomeReadiness.state || "unverified").replace(/_/g, " ")}.`;
      }
      const meta = result.meta || {};
      const scope = evidence && evidence.fixture_id
        ? `Fictional fixture ${evidence.fixture_id} · ${evidence.range_from} to ${evidence.range_to} · database ${shortHash(evidence.database_sha256)}`
        : "Server-selected trailing 365 days";
      const resultHash = evidence ? evidence.result_sha256 : null;
      const evidenceLine = document.getElementById("hero-evidence");
      evidenceLine.textContent = `${scope} · input ${shortHash(meta.input_fingerprint)} · result ${shortHash(resultHash)}`;
      evidenceLine.title = `Input ${meta.input_fingerprint || "unavailable"} · Result ${resultHash || "unavailable"}`;
    } catch (e) {
      if (generation !== heroGeneration || e.name === "AbortError") return;
      document.getElementById("hero-meta").textContent = "unavailable";
      document.getElementById("hero-analysis").hidden = true;
      const el = document.getElementById("hero-empty");
      el.textContent = "Green-day analysis is unavailable: " + e.message;
      el.hidden = false;
      heroStatus.textContent = "Analysis needs attention. Retry when you are ready.";
      heroAction.textContent = "Retry analysis";
    } finally {
      if (generation === heroGeneration) heroController = null;
    }
  }
  heroAction.addEventListener("click", loadHero);

  /* ------- hero arc: Consistency, big gauge cut open at the bottom -------
     owner-review r3: Consistency pulled out of the uniform ring grid into
     its own hero per the design brief — same gaugeOpt() arc the
     standalone Consistency page uses for its own ring (consistency.js
     `ring()`), just bigger and, unlike that page's fixed accent color,
     colored by the engine band here so it stays part of the good/warn/bad
     ring family the five satellites use. */
  function heroArc() {
    const s = (state.scores || {}).consistency;
    const missing = !s || !!s.insufficient_data;
    const P = pal();
    chart("hero-arc").setOption(gaugeOpt({
      value: missing ? 0 : s.score, min: 0, max: 100, width: 22,
      startAngle: 220, endAngle: -40,
      color: missing ? P.track : (P[s.band] || P.accent),
      title: "CONSISTENCY", detailSize: 44,
      detail: missing ? () => "—" : "{value}",
      fmt: () => missing ? "Consistency: pending" : `Consistency: ${s.score} / 100`,
    }));
    const cap = missing ? ((s && s.reason) || "no data yet")
                         : `kept your word ${s.inputs.kept} of ${s.inputs.days_logged} days`;
    document.getElementById("hero-arc-cap").textContent = cap + " · open →";
  }

  /* ------- rings: five pillar satellites, one uniform ring visual family ------- */
  const SAT_DEFS = [
    // [key, label, href]  key null = pending score (honest placeholder)
    // task-60: captions removed from the rings — the pillar page at href
    // already explains the score/insufficiency in full; the ring itself
    // still signals insufficient_data non-textually (dashed .ring-wrap.is-empty).
    ["muscle_balance", "Body", "/training"],
    ["mind", "Mind", "/mind"],
    ["nutrition", "Nutrition", "/nutrition"],
    ["recovery", "Recovery", "/recovery"],
    ["care", "External care", "/care"],
  ];

  function satEl(label, href, s) {
    const a = document.createElement("a");
    a.className = "sat";
    a.href = href;
    const missing = !s || !!s.insufficient_data;
    const wrap = document.createElement("div");
    wrap.className = "ring-wrap" + (missing ? " is-empty" : "");
    const ring = document.createElement("div");
    ring.className = "ring" + (missing ? " empty" : " band-" + s.band);
    if (!missing) ring.style.setProperty("--score", s.score);   // verbatim engine value
    const center = document.createElement("div");
    center.className = "ring-center";
    const num = document.createElement("div");
    num.className = "ring-score";
    num.textContent = missing ? "—" : s.score;
    center.appendChild(num);
    wrap.append(ring, center);
    const name = document.createElement("div");
    name.className = "sat-name " + (missing ? "pending" : "band-" + s.band);
    name.textContent = label;
    a.append(wrap, name);
    return a;
  }

  function renderRings() {
    const scores = (state.scores || {});
    const sats = document.getElementById("sats");
    sats.replaceChildren();
    for (const [key, label, href] of SAT_DEFS) {
      sats.appendChild(satEl(label, href, key ? scores[key] : null));
    }
    heroArc();
  }

  /* ------- mini radars (same real endpoints as the Body page) ------- */
  let dashMuscleData = null;
  const dashMuscleSelected = {
    "Current strengths": true,
    "Planned": true,
    "Logged 7d": true,
  };

  async function loadRadars() {
    const P = pal();
    const frame = (ind) => ({
      radar: { indicator: ind, radius: "62%", center: ["50%", "55%"], splitNumber: 4,
        axisName: { color: P.muted, fontSize: 9, fontWeight: 600 },
        splitLine: { lineStyle: { color: P.grid } },
        splitArea: { areaStyle: { color: ["transparent"] } },
        axisLine: { lineStyle: { color: P.grid } } },
      tooltip: Object.assign(tip(P), { confine: true }),
    });
    try {
      if (!dashMuscleData) {
        dashMuscleData = (await fetchJSON("/api/training/muscle-radar")).result;
      }
      const d = dashMuscleData;
      const axes = d.axes || [];
      const current = d.current_strengths || {};
      const profiles = [
        { name: "Current strengths", values: current.distribution_pct || {},
          color: P.accent, width: 2.4, opacity: 0.15 },
        { name: "Planned", values: d.planned_distribution_pct || {},
          color: P.muted, width: 1.7, opacity: 0.03, dash: "dashed" },
        { name: "Logged 7d", values: d.logged_distribution_pct || {},
          color: P.good, width: 2.0, opacity: 0.08 },
      ];
      const note = document.getElementById("dash-muscle-note");
      note.textContent = "Relative distribution · click legend to show/hide · " +
        (current.status === "complete"
          ? `strength test ${current.quarter}`
          : "strength test pending");
      if (!profiles.some(p => axes.some(g => (p.values[g] || 0) > 0))) {
        empty("dash-radar-muscle", "No muscle-balance data yet");
      } else {
        const muscleChart = chart("dash-radar-muscle");
        muscleChart.on("legendselectchanged", (event) => {
          Object.assign(dashMuscleSelected, event.selected || {});
        });
        muscleChart.setOption(Object.assign(frame(axes.map(g => ({ name: g, max: 100 }))), {
          legend: {
            top: 0, right: 0,
            data: profiles.map(p => p.name),
            selected: dashMuscleSelected,
            textStyle: { color: P.muted, fontSize: 9 },
          },
          series: [{ type: "radar", symbol: "circle", symbolSize: 4,
            data: profiles.map(p => ({
              name: p.name,
              value: axes.map(g => p.values[g] || 0),
              lineStyle: { width: p.width, type: p.dash || "solid", color: p.color },
              itemStyle: { color: p.color },
              areaStyle: { color: p.color, opacity: p.opacity },
            })) }],
        }));
      }
    } catch (e) { empty("dash-radar-muscle", e.message); }
    try {
      const d = (await fetchJSON("/api/training/athletic-radar")).result;
      const axes = d.axes || [];
      const label = { strength: "Strength", endurance: "Endurance", speed: "Speed",
                      balance: "Balance", flexibility: "Flexibility" };
      const scores = axes.map(a => (typeof (d.scores[a] || {}).score === "number") ? d.scores[a].score : 0);
      if (!scores.some(v => v > 0)) empty("dash-radar-ath", "No athletic tests scored yet");
      else chart("dash-radar-ath").setOption(Object.assign(frame(axes.map(a => ({ name: label[a], max: 100 }))), {
        series: [{ type: "radar", symbol: "circle", symbolSize: 4,
          data: [{ name: "score", value: scores,
            lineStyle: { width: 2.2, color: P.accent }, itemStyle: { color: P.accent },
            areaStyle: { color: P.accent, opacity: 0.22 } }] }],
      }));
    } catch (e) { empty("dash-radar-ath", e.message); }
  }

  /* ------- mood & focus trend (real subjective rows, last 14 days) ------- */
  function trendChart() {
    const cut = isoDaysAgo(14);
    const rows = state.subjective.filter(r => r.date >= cut &&
      (r.mood != null || r.focus != null));
    if (!rows.length) return empty("dash-trend", "Mood & focus appear after evening check-ins");
    const P = pal();
    chart("dash-trend").setOption(lineOpt(
      rows.map(r => r.date.slice(5)),
      [{ n: "Mood", d: rows.map(r => r.mood), c: P.accent, area: true },
       { n: "Focus", d: rows.map(r => r.focus), c: P.warn, dash: true }],
      1, 5,
      { legend: { show: true, right: 0, top: 0, textStyle: { color: P.muted, fontSize: 10 } } }));
  }

  /* ------- day-ratings calendar (mode follows the dropdown) -------
     Shared mode-aware calendar (HermesCharts.ratingCal): day/week/month/year/
     all-time, aggregation lives in charts-common (owner-review r3, task-26).
     Day ratings are 1 hard / 2 ok / 3 good (lower = worse), which is exactly
     the ordered domain ratingCal aggregates on. */
  const CAL_MODE = { Day: "day", Week: "week", Month: "month", Year: "year", All: "all" };
  const RATE_WORD = { 1: "hard", 2: "ok", 3: "good" };
  function heatChart() {
    const mode = CAL_MODE[window.HermesUI.range("dashboard")] || "month";
    const rated = state.subjective.filter(r => r.day_rating != null);
    if (!rated.length) return window.HermesCharts.calEmpty("ch-heat", "Day ratings appear here (green / yellow / red)");
    const colr = { 1: pal().bad, 2: pal().warn, 3: pal().good };
    window.HermesCharts.ratingCal("ch-heat", {
      mode,
      entries: rated.map(r => ({ date: r.date, value: r.day_rating })),
      colorFor: (v) => colr[v] || null,
      wordFor: (v) => RATE_WORD[v] || "?",
    });
  }

  /* ------- habits & kept-word (engine verbatim) ------- */
  function renderHabits() {
    const list = document.getElementById("habit-list");
    const habits = state.habits;
    if (habits.length) {
      list.replaceChildren();
      habits.sort((a, b) => b.streak - a.streak);
      for (const h of habits) {
        const row = document.createElement("div");
        row.className = "row";
        const tile = document.createElement("div");
        tile.className = "disp strong center habit-tile";
        tile.textContent = h.streak > 0 ? h.streak : "·";
        const txt = document.createElement("div");
        const name = document.createElement("div");
        name.className = "note-txt strong";
        name.textContent = h.habit;
        const sub = document.createElement("div");
        sub.className = "muted micro";
        sub.textContent = h.streak > 0 ? `${h.streak}-day streak` : "a fresh run starts now";
        txt.append(name, sub);
        row.append(tile, txt);
        list.appendChild(row);
      }
    }
    const cons = (state.scores || {}).consistency;
    if (cons && !cons.insufficient_data) {
      const i = cons.inputs || {};
      document.getElementById("kept-chip").textContent = `${i.kept} of ${i.days_logged}`;
      const bar = document.getElementById("kept-bar");
      bar.style.width = cons.score + "%";        // engine score verbatim
      bar.style.background = "var(--good)";
      document.getElementById("kept-cap").textContent =
        `kept your word ${i.kept} of ${i.days_logged} logged days · per-commitment ledger on the Consistency page`;
      document.getElementById("kept-word").hidden = false;
    }
  }

  /* ------- strength-balance body maps (§3f Phase 2) ------- */
  // Read-only glance, lens FIXED to strength-balance (product contract: no
  // activation fallback on the dashboard). One fetch feeds both views; the
  // whole card links to /training where the full lens picker lives.
  async function loadMuscleFigure() {
    const wrapF = document.getElementById("dash-mf-front");
    const wrapB = document.getElementById("dash-mf-back");
    if (!wrapF || !wrapB) return;
    const fail = (msg) => [wrapF, wrapB].forEach((el) => {
      const p = document.createElement("p");
      p.className = "muted note-txt m0"; p.textContent = msg;
      el.replaceChildren(p);
    });
    if (!window.MuscleFigure) {
      fail("Body-map component failed to load (vendored asset missing?).");
      return;
    }
    let data;
    try {
      data = (await fetchJSON("/api/training/muscle-map?lens=strength-balance")).result;
    } catch (e) { fail(e.message); return; }
    window.MuscleFigure.create(wrapF, { view: "front" }).update(data);
    window.MuscleFigure.create(wrapB, { view: "back" }).update(data);
    window.MuscleFigure.legend(document.getElementById("dash-mf-legend"), data);
  }

  async function boot() {
    const [subj, scores] = await Promise.allSettled([
      // days=all: the calendar's "All time" mode aggregates whatever exists,
      // never a silent 365-day cap; the 14-day trend still slices client-side.
      fetchJSON("/api/dash/subjective?days=all"),
      fetchJSON("/api/dash/scores"),
    ]);
    if (subj.status === "fulfilled") state.subjective = subj.value.rows;
    if (scores.status === "fulfilled") {
      state.scores = (scores.value.result || {}).scores || {};
      state.habits = (scores.value.result || {}).habits || [];
    }
    renderChips();
    renderRings();
    trendChart();
    heatChart();
    renderHabits();
    loadRadars();
    loadHero();
    loadNudge();
    loadMuscleFigure();
    if (subj.status === "rejected") window.HermesCharts.calEmpty("ch-heat", subj.reason.message);
  }

  window.HermesUI.onRange("dashboard", heatChart);
  window.addEventListener("themechange", () => {
    renderRings(); trendChart(); heatChart(); loadRadars();
  });
  window.addEventListener("resize", () => Object.values(charts).forEach(c => c.resize()));
  boot();
})();
