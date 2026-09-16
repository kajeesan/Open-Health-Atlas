/* task-30 recovery drill sub-page (shared by sleep / hrv / rhr). Fetches
   /api/dash/recovery-detail?metric=&days= and renders three cards from REAL
   daily_metrics data. Determinism law: every number (baseline median, deltas,
   min/max, chips) is computed server-side in dash.py — this file only draws
   what it's handed. Provenance: apple + fitbit come back as separate series and
   chart as separate lines (apple solid, fitbit dashed), never merged. */
(function () {
  if (typeof echarts === "undefined" || !window.HermesCharts) return;
  const { fetchJSON, pal, lineOpt } = window.HermesCharts;
  const RANGE_DAYS = { Day: "1", Week: "7", Month: "30", Year: "365", All: "all" };
  const charts = {};
  let metric = "";
  let state = null;

  function chart(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    if (charts[id]) charts[id].dispose();
    charts[id] = echarts.init(el);
    return charts[id];
  }

  function showEmpty(msg) {
    document.getElementById("rd-grid").hidden = true;
    document.getElementById("rd-empty").hidden = false;
    document.getElementById("rd-empty-body").textContent = msg;
  }

  /* ---------- trend: apple solid, fitbit dashed (provenance) ---------- */
  function renderChart() {
    const P = pal();
    const series = state.series || {};
    const apple = series.apple || [];
    const fitbit = series.fitbit || [];
    if (!apple.length && !fitbit.length) {
      chart("rd-chart").setOption({ graphic: { type: "text", left: "center", top: "middle",
        style: { text: "No readings in this range", fill: P.muted, fontSize: 13 } } });
      return;
    }
    const dates = [...new Set([...apple, ...fitbit].map((p) => p[0]))].sort();
    const aMap = new Map(apple);
    const fMap = new Map(fitbit);
    const lines = [];
    if (apple.length) {
      lines.push({ n: "Apple", c: P.accent, area: fitbit.length === 0,
        d: dates.map((d) => (aMap.has(d) ? aMap.get(d) : null)) });
    }
    if (fitbit.length) {
      lines.push({ n: "Fitbit", c: P.good, dash: true,
        d: dates.map((d) => (fMap.has(d) ? fMap.get(d) : null)) });
    }
    const extra = lines.length > 1
      ? { legend: { show: true, top: 0, textStyle: { color: P.muted, fontSize: 10 } },
          grid: { left: 10, right: 16, top: 26, bottom: 22, containLabel: true } }
      : {};
    const opt = lineOpt(dates.map((d) => d.slice(5)), lines, null, null, extra);
    // baseline reference line (personal 60-day median) where one exists
    if (state.baseline != null && opt.series && opt.series[0]) {
      opt.series[0].markLine = {
        silent: true, symbol: "none",
        lineStyle: { type: "dashed", color: P.muted, width: 1.2 },
        label: { show: true, position: "insideEndTop", formatter: state.baseline_label,
                 color: P.muted, fontSize: 10 },
        data: [{ yAxis: state.baseline }],
      };
    }
    const c = chart("rd-chart");
    c.setOption(opt);
    // this trend sits in a full-width card; echarts.init can capture a stale
    // (half) container width before layout settles, clipping the line at the
    // midpoint — resize() recomputes against the true width once it has.
    c.resize();
    document.getElementById("rd-trend-note").textContent =
      fitbit.length ? "apple solid · fitbit dashed" : "";
  }

  /* ---------- component breakdown rows ---------- */
  function renderBreakdown() {
    const body = document.getElementById("rd-breakdown");
    body.replaceChildren();
    const rows = state.breakdown || [];
    if (!rows.length) {
      const p = document.createElement("p");
      p.className = "muted note-txt m0";
      p.textContent = "No breakdown available yet.";
      body.appendChild(p);
      return;
    }
    rows.forEach((r) => {
      const row = document.createElement("div");
      row.className = "prod";
      const text = document.createElement("div");
      text.className = "grow";
      const name = document.createElement("div");
      name.className = "note-txt strong";
      name.textContent = r.name;
      const sub = document.createElement("div");
      sub.className = "muted micro";
      sub.textContent = r.sub || "";
      text.append(name, sub);
      row.appendChild(text);
      const val = document.createElement("span");
      val.className = "note-txt strong";
      val.textContent = r.value;
      row.appendChild(val);
      if (r.chip) {
        const chip = document.createElement("span");
        chip.className = "flag " + r.chip.cls;
        chip.textContent = r.chip.text;
        row.appendChild(chip);
      }
      body.appendChild(row);
    });
  }

  /* ---------- what stands out (deterministic facts) ---------- */
  function renderAdvice() {
    const body = document.getElementById("rd-advice");
    body.replaceChildren();
    const facts = state.advice || [];
    if (!facts.length) {
      const p = document.createElement("p");
      p.className = "muted note-txt m0";
      p.textContent = "Nothing notable vs your baseline in this range.";
      body.appendChild(p);
      return;
    }
    const ul = document.createElement("ul");
    ul.className = "plain-list";
    facts.forEach((f) => {
      const li = document.createElement("li");
      li.className = "note-txt";
      li.textContent = "• " + f;
      ul.appendChild(li);
    });
    body.appendChild(ul);
  }

  function render() {
    if (!state) return;
    if (state.empty) {
      showEmpty("No " + (state.label || metric) + " readings are logged yet. This page "
        + "fills in once daily_metrics has rows for this metric.");
      return;
    }
    document.getElementById("rd-empty").hidden = true;
    document.getElementById("rd-grid").hidden = false;
    document.getElementById("rd-title").textContent = state.label + " — recovery breakdown";
    document.getElementById("rd-trend-title").textContent = state.label + " — trend";
    const chipEl = document.getElementById("rd-header-chip");
    if (state.header_chip) {
      chipEl.hidden = false;
      chipEl.className = "flag " + (state.header_chip.cls || "");
      chipEl.textContent = state.header_chip.text;
    } else {
      chipEl.hidden = true;
    }
    renderChart();
    renderBreakdown();
    renderAdvice();
  }

  async function load() {
    const days = RANGE_DAYS[window.HermesUI.range("rdrange") || "Month"] || "30";
    try {
      state = await fetchJSON("/api/dash/recovery-detail?metric="
        + encodeURIComponent(metric) + "&days=" + days);
    } catch (e) {
      showEmpty(e.message);
      return;
    }
    render();
  }

  function boot() {
    try {
      metric = decodeURIComponent(location.pathname.split("/").pop() || "");
    } catch (e) {
      showEmpty(e.message);
      return;
    }
    load();
    window.HermesUI.onRange("rdrange", load);
  }

  window.addEventListener("themechange", render);
  window.addEventListener("resize", () => Object.values(charts).forEach((c) => c && c.resize()));
  boot();
})();
