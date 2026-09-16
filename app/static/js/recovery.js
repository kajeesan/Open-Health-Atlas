/* Recovery pillar (design v-recovery). KPI cards + sleep/HRV/RHR trends —
   real, from daily_metrics via /api/dash/metrics, apple-preferred per date
   (perDateEntries copied from dashboard.js, same as mind.js's CV-safety
   strip). HR/RHR live HERE by product contract (dropped from Body). task-48:
   the Readiness KPI is now real too — /api/recovery/readiness (the T48
   engine), rendered verbatim (score + band chip, or the engine's own
   insufficient_data reason). task-30 added a "›" drill link on each KPI
   label to /recovery/<metric>; this file only owns the KPI values and
   trends, the drill pages are their own template + JS. Sleep quality/stages
   come from sleep_log (/api/dash/sleep) — zero rows on the demo DB (Fitbit
   sleep-stage sync awaits); honest-empty, and lights up on its own once
   real rows land. Cross-links card is an honest placeholder — no
   cross-pillar engine yet. Owner-review r2: the dashboard's Sleep score
   ring moved here — a "score N" chip on the Sleep KPI card, from the same
   /api/dash/scores.sleep the dashboard used, honest-hidden when
   insufficient_data. */
(function () {
  if (typeof echarts === "undefined" || !window.HermesCharts) return;
  const { fetchJSON, pal, tip, lineOpt, barsOpt, isoDaysAgo } = window.HermesCharts;
  const RANGE_DAYS = { Day: 1, Week: 7, Month: 30, Year: 365, All: null };
  const charts = {};
  const state = { metrics: [], sleepLog: [], scores: null, readiness: null, readinessEvidence: null };

  function chart(id) {
    const el = document.getElementById(id);
    if (charts[id]) charts[id].dispose();
    charts[id] = echarts.init(el);
    return charts[id];
  }
  function empty(id, label) {
    chart(id).setOption({ graphic: { type: "text", left: "center", top: "middle",
      style: { text: label, fill: pal().muted, fontSize: 13 } } });
  }

  /* one row per DATE, prefer apple over fitbit — same provenance rule as
     the dashboard's vitals stat cards and Mind's CV-safety strip. */
  function perDateEntries(rows, field) {
    const byDate = new Map();
    for (const r of rows) {
      if (r[field] == null) continue;
      const cur = byDate.get(r.date);
      if (!cur || (cur.source !== "apple" && r.source === "apple")) byDate.set(r.date, r);
    }
    return [...byDate.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([d, r]) => [d, r[field]]);
  }

  /* latest value vs the mean of the rest of the window — a plain delta, not
     a clinical judgment (matches Mind's cvCell/trendFlag). */
  function trendFlag(vals, goodWhenDown) {
    if (vals.length < 3) return { text: "", cls: "" };
    const latest = vals[vals.length - 1];
    const priors = vals.slice(0, -1);
    const mean = priors.reduce((a, b) => a + b, 0) / priors.length;
    const diff = latest - mean;
    if (Math.abs(diff) < Math.abs(mean) * 0.02) return { text: "steady vs avg", cls: "in" };
    const up = diff > 0;
    const good = goodWhenDown ? !up : up;
    const d = Math.round(Math.abs(diff) * 10) / 10;
    return { text: `${up ? "↑" : "↓"}${d} vs avg`, cls: good ? "in" : "bd" };
  }

  function windowedRows(rows) {
    const days = RANGE_DAYS[window.HermesUI.range("recovery") || "Week"];
    if (days == null) return rows;
    let cut = isoDaysAgo(days);
    if (state.readiness && state.readiness.anchor_date) {
      const anchor = new Date(state.readiness.anchor_date + "T00:00:00Z");
      anchor.setUTCDate(anchor.getUTCDate() - days);
      cut = anchor.toISOString().slice(0, 10);
    }
    return rows.filter((r) => r.date >= cut);
  }

  function kpiCell(valId, flagId, entries, fmt, goodWhenDown) {
    const valEl = document.getElementById(valId);
    const flagEl = document.getElementById(flagId);
    if (!entries.length) {
      valEl.textContent = "—";
      flagEl.hidden = true;
      return;
    }
    const vals = entries.map((e) => e[1]);
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
    valEl.textContent = fmt(avg);
    const flag = trendFlag(vals, goodWhenDown);
    if (flag.text) {
      flagEl.hidden = false;
      flagEl.className = "flag mt-1 " + flag.cls;
      flagEl.textContent = flag.text;
    } else {
      flagEl.hidden = true;
    }
  }

  function kpis(rows) {
    kpiCell("rkSleep", "rkSleepFlag", perDateEntries(rows, "sleep_hours"), (v) => v.toFixed(1), false);
    kpiCell("rkHrv", "rkHrvFlag", perDateEntries(rows, "hrv_ms"), (v) => String(Math.round(v)), false);
    kpiCell("rkRhr", "rkRhrFlag", perDateEntries(rows, "resting_hr"), (v) => String(Math.round(v)), true);
  }

  /* engine sleep score chip (verbatim, honest-hidden when insufficient) */
  function sleepScoreChip() {
    const el = document.getElementById("rkSleepScore");
    const s = (state.scores || {}).sleep;
    if (!s || s.insufficient_data) { el.hidden = true; return; }
    el.hidden = false;
    el.textContent = `score ${s.score}`;
  }

  /* T48 readiness KPI: real score + band, or the engine's own
     insufficient_data reason verbatim (never hidden — an honest state, not
     a blank one, per the brief). */
  function readinessKpi() {
    const val = document.getElementById("rkReadiness");
    const chip = document.getElementById("rkReadinessChip");
    const scope = document.getElementById("rkReadinessScope");
    const r = state.readiness;
    const evidence = state.readinessEvidence;
    if (evidence && evidence.data_class === "fictional") {
      scope.textContent = `fictional · ${evidence.anchor_date}`;
      scope.hidden = false;
    } else {
      scope.hidden = true;
      scope.textContent = "";
    }
    if (!r) { val.textContent = "—"; chip.hidden = true; return; }
    if (r.status !== "ok") {
      val.textContent = "—";
      chip.hidden = false;
      chip.className = "chip muted mt-1";
      chip.textContent = r.reason || "insufficient data";
      return;
    }
    val.textContent = String(r.score);
    chip.hidden = false;
    chip.className = "chip mt-1";
    chip.textContent = r.band;
  }

  /* ------- Sleep hours & quality ------- */
  function sleepChart(rows, sleepLog) {
    const P = pal();
    const note = document.getElementById("rSleepNote");
    const hours = perDateEntries(rows, "sleep_hours");
    if (!hours.length) {
      note.textContent = "";
      return empty("rSleep", "Sleep hours appear once daily_metrics has sleep_hours rows");
    }
    const cats = hours.map((h) => h[0].slice(5));
    const hrs = hours.map((h) => h[1]);
    // sleep_log.quality is a 1-5 rating (same RATING family as mood/focus/etc,
    // health.py toolkit) — currently unseeded (Fitbit sleep-stage sync awaits).
    const qualByDate = new Map(sleepLog.filter((r) => r.quality != null).map((r) => [r.date, r.quality]));
    if (!qualByDate.size) {
      note.textContent = "quality — not logged yet (sleep_log awaits Fitbit)";
      return chart("rSleep").setOption(barsOpt(cats, hrs, P.accent, false));
    }
    note.textContent = "";
    chart("rSleep").setOption({
      grid: { left: 10, right: 16, top: 26, bottom: 22, containLabel: true },
      tooltip: Object.assign(tip(P), { trigger: "axis" }),
      legend: { show: true, top: 0, textStyle: { color: P.muted, fontSize: 10 } },
      xAxis: { type: "category", data: cats,
        axisLine: { lineStyle: { color: P.grid } },
        axisLabel: { color: P.muted, fontSize: 9 }, axisTick: { show: false } },
      yAxis: [
        { type: "value", min: 0, splitLine: { lineStyle: { color: P.grid } },
          axisLabel: { color: P.muted, fontSize: 9 } },
        { type: "value", min: 0, max: 5, show: false },
      ],
      series: [
        { name: "Hours", type: "bar", data: hrs, itemStyle: { color: P.accent, borderRadius: 4 }, barWidth: "46%" },
        { name: "Quality", yAxisIndex: 1, type: "line", smooth: 0.4, symbol: "circle", symbolSize: 5,
          data: hours.map((h) => (qualByDate.has(h[0]) ? qualByDate.get(h[0]) : null)),
          lineStyle: { color: P.good, width: 2.2 }, itemStyle: { color: P.good } },
      ],
    });
  }

  /* ------- Sleep stages ------- */

  /* task-60: Deep/REM/Awake keep pal()'s accent/good/bad roles — pairwise
     distinct in every theme. Light normally reuses warn, but the ember and
     canyon themes define --accent and --warn as the SAME hex (panel.css),
     so Deep and Light rendered as identical colors (owner-reported bug).
     Detect that collision at render time (P re-resolves per theme, same as
     every other pal() call in this file) and derive Light by rotating the
     resolved warn hue to blue — a hue none of accent/good/warn/bad occupy
     in any current theme — landing lightness/saturation in a band that
     passed the dataviz skill's validate_palette.js for both collision
     themes (ember light-mode, canyon dark-mode; see task-60 report for the
     resolved-color table and validator output). */
  function hexToHsl(hex) {
    const n = parseInt(hex.slice(1), 16);
    const r = ((n >> 16) & 255) / 255, g = ((n >> 8) & 255) / 255, b = (n & 255) / 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
    let h = 0, s = 0;
    const d = max - min;
    if (d) {
      s = d / (1 - Math.abs(2 * l - 1));
      if (max === r) h = ((g - b) / d) % 6;
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60; if (h < 0) h += 360;
    }
    return { h, s, l };
  }
  function hslToHex(h, s, l) {
    const c = (1 - Math.abs(2 * l - 1)) * s;
    const x = c * (1 - Math.abs((h / 60) % 2 - 1));
    const m = l - c / 2;
    let [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x]
      : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
    const toHex = (v) => Math.round((v + m) * 255).toString(16).padStart(2, "0");
    return "#" + toHex(r) + toHex(g) + toHex(b);
  }
  function stageLightColor(P) {
    if (P.warn.toLowerCase() !== P.accent.toLowerCase()) return P.warn;
    const isDark = getComputedStyle(document.documentElement).colorScheme.includes("dark");
    const { s } = hexToHsl(P.warn);
    return hslToHex(210, Math.max(s, 0.70), isDark ? 0.58 : 0.45);
  }

  function stagesChart(sleepLog) {
    const rows = sleepLog.filter((r) =>
      r.deep_min != null || r.rem_min != null || r.light_min != null || r.awake_min != null);
    if (!rows.length) {
      return empty("rStages", "Sleep stages appear once Fitbit sleep-stage sync lands (sleep_log deep/REM/light/awake minutes)");
    }
    const P = pal();
    const cats = rows.map((r) => r.date.slice(5));
    const stages = [["Deep", "deep_min", P.accent], ["REM", "rem_min", P.good],
                     ["Light", "light_min", stageLightColor(P)], ["Awake", "awake_min", P.bad]];
    chart("rStages").setOption({
      grid: { left: 10, right: 16, top: 26, bottom: 22, containLabel: true },
      tooltip: Object.assign(tip(P), { trigger: "axis", axisPointer: { type: "shadow" },
        valueFormatter: (v) => (v == null ? "—" : (v / 60).toFixed(1) + " h") }),
      legend: { show: true, top: 0, textStyle: { color: P.muted, fontSize: 10 } },
      xAxis: { type: "category", data: cats,
        axisLine: { lineStyle: { color: P.grid } },
        axisLabel: { color: P.muted, fontSize: 10, hideOverlap: true }, axisTick: { show: false } },
      yAxis: { type: "value", splitNumber: 4, splitLine: { lineStyle: { color: P.grid } },
        axisLabel: { color: P.muted, fontSize: 10, formatter: (v) => {
          // 1-decimal max, whole hours bare — matches the tooltip's toFixed(1) rounding
          const h = (v / 60).toFixed(1);
          return (h.endsWith(".0") ? h.slice(0, -2) : h) + "h";
        } } },
      series: stages.map(([name, field, color], i) => ({
        name, type: "bar", stack: "x", data: rows.map((r) => r[field]),
        itemStyle: { color, borderRadius: i === stages.length - 1 ? [3, 3, 0, 0] : 0 },
        barWidth: "46%", emphasis: { focus: "series" },
      })),
    });
  }

  /* ------- HRV & resting HR ------- */
  function hrvChart(rows) {
    const hrv = perDateEntries(rows, "hrv_ms");
    const rhr = perDateEntries(rows, "resting_hr");
    if (!hrv.length && !rhr.length) {
      return empty("rHrv", "HRV / resting HR appear once daily_metrics has readings");
    }
    const dates = [...new Set([...hrv.map((e) => e[0]), ...rhr.map((e) => e[0])])].sort();
    const hrvByDate = new Map(hrv);
    const rhrByDate = new Map(rhr);
    const P = pal();
    chart("rHrv").setOption(lineOpt(
      dates.map((d) => d.slice(5)),
      [{ n: "HRV", d: dates.map((d) => (hrvByDate.has(d) ? hrvByDate.get(d) : null)), c: P.accent, area: true },
       { n: "RHR", d: dates.map((d) => (rhrByDate.has(d) ? rhrByDate.get(d) : null)), c: P.bad, dash: true }],
      null, null,
      { legend: { show: true, top: 0, textStyle: { color: P.muted, fontSize: 10 } } }));
  }

  function render() {
    const rows = windowedRows(state.metrics);
    const sleepLog = windowedRows(state.sleepLog);
    kpis(rows);
    sleepScoreChip();
    readinessKpi();
    sleepChart(rows, sleepLog);
    stagesChart(sleepLog);
    hrvChart(rows);
  }

  async function loadState() {
    // days=all: the range dropdown includes "All time", so the superset the
    // client re-slices must be unbounded — a 365-day fetch would silently
    // cap "All time" at a year (review finding).
    const [metrics, sleepLog, scores, readiness] = await Promise.allSettled([
      fetchJSON("/api/dash/metrics?days=all"),
      fetchJSON("/api/dash/sleep?days=all"),
      fetchJSON("/api/dash/scores"),
      fetchJSON("/api/recovery/readiness"),
    ]);
    if (metrics.status === "fulfilled") state.metrics = metrics.value.rows || [];
    if (sleepLog.status === "fulfilled") state.sleepLog = sleepLog.value.rows || [];
    if (scores.status === "fulfilled") state.scores = (scores.value.result || {}).scores || {};
    if (readiness.status === "fulfilled") {
      state.readiness = readiness.value.result || null;
      state.readinessEvidence = readiness.value.evidence || null;
    }
  }

  async function boot() {
    await loadState();
    render();
    window.HermesUI.onRange("recovery", render);
  }

  window.addEventListener("themechange", render);
  window.addEventListener("resize", () => Object.values(charts).forEach((c) => c && c.resize()));
  boot();
})();
