/* §3c drill-down (round-3 redesign): one shared renderer for all five athletic
   axes. Fetches /api/training/athletic-detail?axis= and renders the four design
   cards — every SCORE comes from the engine (athletic-radar) verbatim; this file
   only draws the values it is handed (determinism law: math lives in health.py /
   the route, never re-scored here). Honest single-card empty state until a
   target is set AND a test is logged (the demo DB seeds neither). */
(function () {
  if (typeof echarts === "undefined" || !window.HermesCharts) return;
  const { fetchJSON, pal, tip, barsOpt, lineOpt } = window.HermesCharts;
  const charts = {};
  const state = { data: null, error: null };

  function chart(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    if (charts[id]) charts[id].dispose();
    charts[id] = echarts.init(el);
    return charts[id];
  }

  function cardEmpty(container, msg) {
    container.replaceChildren();
    const p = document.createElement("p");
    p.className = "muted note-txt m0";
    p.textContent = msg;
    container.appendChild(p);
  }

  // score band → theme color (bar fill), matching the engine's %-of-target
  // semantics: good = at/above target, warn = within reach, bad = well short.
  function bandColor(band) {
    const P = pal();
    return band === "good" ? P.good : band === "warn" ? P.warn : P.bad;
  }

  // chip text → the design's .flag colour class (green / amber / red).
  const FLAG_CLASS = { "on target": "in", improving: "bd", below: "out" };

  /* ---------- Sub-tests — score vs target ---------- */
  function renderSubtests() {
    const body = document.getElementById("ad-sub-body");
    const subs = state.data.sub_tests || [];
    if (!subs.length) {
      cardEmpty(body, "No scored sub-tests yet for this axis.");
      return;
    }
    body.replaceChildren();
    const chartEl = document.createElement("div");
    chartEl.id = "ad-sub-chart";
    chartEl.className = "h230";
    body.appendChild(chartEl);
    const note = document.createElement("p");
    note.className = "muted note-txt m0 mt-1";
    note.textContent = state.data.target_caption || "bar = your score · dashed line = target (100)";
    body.appendChild(note);

    // reverse so the FIRST sub-test reads at the top (echarts puts category 0
    // at the bottom of a horizontal chart).
    const ordered = [...subs].reverse();
    const cats = ordered.map((s) => s.name);
    const vals = ordered.map((s) => s.score);
    const colors = ordered.map((s) => bandColor(s.band));
    const opt = barsOpt(cats, vals, colors, true);
    const P = pal();
    // fixed 0–100 scale: scores are engine %-of-target, so the axis IS the
    // target scale and the dashed line at 100 is the target itself.
    opt.xAxis.min = 0;
    opt.xAxis.max = 100;
    opt.series[0].markLine = {
      silent: true, symbol: "none",
      lineStyle: { type: "dashed", color: P.ink, width: 1.5 },
      label: { show: true, position: "start", formatter: "target",
               color: P.muted, fontSize: 10 },
      data: [{ xAxis: 100 }],
    };
    chart("ad-sub-chart").setOption(opt);
  }

  /* ---------- Field tests driving this axis ---------- */
  function renderFields() {
    const body = document.getElementById("ad-field-body");
    const fields = state.data.field_tests || [];
    if (!fields.length) {
      cardEmpty(body, "No tests logged yet for this axis.");
      return;
    }
    body.replaceChildren();
    fields.forEach((f) => {
      const row = document.createElement("div");
      row.className = "prod";
      const text = document.createElement("div");
      text.className = "grow";
      const name = document.createElement("div");
      name.className = "note-txt strong";
      name.textContent = f.name;
      const val = document.createElement("div");
      val.className = "muted micro";
      val.textContent = f.value_txt;
      text.append(name, val);
      row.appendChild(text);
      if (f.chip && FLAG_CLASS[f.chip]) {
        const chip = document.createElement("span");
        chip.className = "flag " + FLAG_CLASS[f.chip];
        chip.textContent = f.chip;
        row.appendChild(chip);
      }
      body.appendChild(row);
    });
  }

  /* ---------- Progress — last 5 tests ---------- */
  function renderProgress() {
    const body = document.getElementById("ad-prog-body");
    const prog = state.data.progress || {};
    const series = (prog.series || []).filter((s) => s.points && s.points.length);
    if (!series.length) {
      cardEmpty(body, "Log at least one test to chart progress.");
      return;
    }
    body.replaceChildren();
    const chartEl = document.createElement("div");
    chartEl.id = "ad-prog-chart";
    chartEl.className = "h230";
    body.appendChild(chartEl);
    const note = document.createElement("p");
    note.className = "muted note-txt m0 mt-1";
    note.textContent = prog.caption || "";
    body.appendChild(note);

    const P = pal();
    const cycle = [P.accent, P.good, P.warn, P.ink, P.bad];
    // shared, sorted date axis so multi-lift (strength) series align; a series
    // missing a date charts a gap (null) rather than shifting.
    const dates = [...new Set(series.flatMap((s) => s.points.map((p) => p.date)))].sort();
    const lineSeries = series.map((s, i) => {
      const byDate = new Map(s.points.map((p) => [p.date, p.value]));
      return { n: s.name, c: cycle[i % cycle.length],
               d: dates.map((dt) => (byDate.has(dt) ? byDate.get(dt) : null)),
               area: series.length === 1, symbol: "circle" };
    });
    const extra = series.length > 1
      ? { legend: { top: 0, right: 0, textStyle: { color: P.muted, fontSize: 10 } },
          grid: { left: 10, right: 16, top: 26, bottom: 22, containLabel: true } }
      : {};
    chart("ad-prog-chart").setOption(lineOpt(dates, lineSeries, null, null, extra));
  }

  /* ---------- Where you're at · what to improve ---------- */
  function renderSummary() {
    const body = document.getElementById("ad-summary-body");
    const sum = state.data.summary || {};
    body.replaceChildren();
    const status = document.createElement("p");
    status.className = "note-txt m0";
    status.textContent = sum.status_txt || "";
    body.appendChild(status);
    const bullets = sum.bullets || [];
    if (bullets.length) {
      const ul = document.createElement("ul");
      ul.className = "plain-list mt-2";
      bullets.forEach((b) => {
        const li = document.createElement("li");
        li.className = "muted note-txt";
        li.textContent = "• " + b;
        ul.appendChild(li);
      });
      body.appendChild(ul);
    }
  }

  function render() {
    if (!state.data) return;
    const grid = document.getElementById("ad-grid");
    const empty = document.getElementById("ad-empty");
    if (state.data.status !== "ok") {
      grid.hidden = true;
      empty.hidden = false;
      document.getElementById("ad-empty-title").textContent =
        state.data.label + " — no data yet";
      document.getElementById("ad-empty-body").textContent =
        (state.data.reason || "No data for this axis yet.").replace(/\.?$/, ".") +
        " Once an owner target is set and a test is logged, this axis breaks " +
        "down into sub-tests, field values, and a progress trend.";
      return;
    }
    empty.hidden = true;
    grid.hidden = false;
    renderSubtests();
    renderFields();
    renderProgress();
    renderSummary();
  }

  async function boot() {
    let axis;
    try {
      axis = decodeURIComponent(location.pathname.split("/").pop() || "");
    } catch (e) {
      cardEmpty(document.getElementById("ad-empty-body"), e.message);
      document.getElementById("ad-empty").hidden = false;
      document.getElementById("ad-grid").hidden = true;
      return;
    }
    try {
      state.data = (await fetchJSON(
        "/api/training/athletic-detail?axis=" + encodeURIComponent(axis))).result;
    } catch (e) {
      document.getElementById("ad-empty").hidden = false;
      document.getElementById("ad-grid").hidden = true;
      cardEmpty(document.getElementById("ad-empty-body"), e.message);
      return;
    }
    document.getElementById("ad-title").textContent = state.data.label + " — athletic breakdown";
    render();
  }

  window.addEventListener("themechange", render);
  window.addEventListener("resize", () => Object.values(charts).forEach((c) => c && c.resize()));

  boot();
})();
