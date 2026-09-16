/* §labs bloodwork view — read-only, Hi-Fi design (v-labs). One .ltab table
   with panel-header rows + a per-test trend chart with the reference band and
   low/high markLines. All numbers + range flags come from health.py via
   /api/labs; the page never computes a value. in_range stays BINARY
   (true/false/null) — the design's "borderline" state has no engine source
   and is deliberately not rendered (product contract). */
(function () {
  const { fetchJSON, pal, tip, isoDaysAgo } = window.HermesCharts;
  const esc = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; };

  // measured serum ↔ nutrition intake pairing (visual only — intake and
  // serum level don't map linearly, so NO computed relationship; §labs decision)
  const NUTRITION_LINK = {
    "Vitamin D": { label: "Vitamin D intake", href: "/nutrition" },
    "Vitamin B12": { label: "B12 intake", href: "/nutrition" },
    "Ferritin": { label: "Iron intake", href: "/nutrition" },
    "Magnesium": { label: "Magnesium intake", href: "/nutrition" },
  };
  const RANGE_DAYS = { Day: 1, Week: 7, Month: 30, Year: 365, All: null };

  const panelsEl = document.getElementById("lab-panels");
  const chartEl = document.getElementById("lab-chart");
  const trendName = document.getElementById("lab-trend-name");
  const trendNote = document.getElementById("lab-trend-note");
  const chipsEl = document.getElementById("lab-chips");
  const resultsTitle = document.getElementById("lab-results-title");
  let chart = null;
  let selected = null;          // { canonical (encoded), name }
  let lastTrend = null;         // cached /api/labs/test response for re-renders

  function fmtRef(lo, hi) {
    if (lo == null && hi == null) return "—";
    if (lo == null) return "< " + hi;
    if (hi == null) return "> " + lo;
    return lo + "–" + hi;
  }

  function flagCell(inRange) {
    if (inRange === true) return '<span class="flag in">in</span>';
    if (inRange === false) return '<span class="flag out">out</span>';
    return '<span class="muted micro">no range</span>';
  }

  function renderPanels(data) {
    if (data.insufficient_data) {
      panelsEl.innerHTML =
        '<p class="muted note-txt">No lab results yet. Send a report photo/PDF to the coach to ingest one.</p>';
      return;
    }
    const allTests = data.panels.flatMap((p) => p.tests);
    resultsTitle.textContent = "Results — newest per test";
    const histN = allTests.filter((t) => t.historical).length;
    const lead = histN
      ? `<p class="muted note-txt m0 mb-2">${histN} of ${allTests.length} results are ` +
        `older than ${Math.round((data.stale_after_days || 365) / 30)} months — ` +
        `shown as <span class="chip">historical</span>, not current status.</p>`
      : "";

    const rows = data.panels.map((p) => {
      const body = p.tests.map((t) => {
        const link = NUTRITION_LINK[t.canonical];
        const linkHtml = link
          ? ` <a class="chip" href="${esc(link.href)}" title="intake → measured status">↔ ${esc(link.label)}</a>`
          : "";
        // current status = the latest reading only; if it's >12mo old it's
        // historical (shown, but not read as current)
        const histBadge = t.historical ? ' <span class="chip">historical</span>' : "";
        return `<tr class="lab-row ${t.historical ? "historical" : ""}" data-test="${encodeURIComponent(t.canonical)}" data-name="${esc(t.display)}">
            <td class="strong">${esc(t.display)}${linkHtml}${histBadge}</td>
            <td class="num ${t.in_range === false ? "oor" : ""}">${esc(t.value)} <span class="muted micro">${esc(t.unit || "")}</span></td>
            <td class="muted">${esc(fmtRef(t.reference_low, t.reference_high))}</td>
            <td class="muted">${esc(t.date)}</td>
            <td>${flagCell(t.in_range)}</td>
          </tr>`;
      }).join("");
      return `<tr class="panelhd"><td colspan="5">${esc(p.panel)}</td></tr>${body}`;
    }).join("");

    panelsEl.innerHTML = lead +
      `<div class="table-wrap"><table class="ltab"><thead><tr>
        <th>Test</th><th>Value</th><th>Reference</th><th>Date</th><th>Flag</th>
      </tr></thead><tbody>${rows}</tbody></table></div>`;

    panelsEl.querySelectorAll(".lab-row").forEach((r) => {
      r.style.cursor = "pointer";
      r.addEventListener("click", () => loadTrend(r.dataset.test, r.dataset.name));
    });

    // quick-pick chips: out-of-range tests first, then the rest, capped at 8
    const ordered = [...allTests].sort((a, b) =>
      (a.in_range === false ? 0 : 1) - (b.in_range === false ? 0 : 1));
    chipsEl.innerHTML = ordered.slice(0, 8).map((t) =>
      `<button class="mchip" type="button" data-test="${encodeURIComponent(t.canonical)}" data-name="${esc(t.display)}">${esc(t.display)}</button>`
    ).join("");
    chipsEl.querySelectorAll(".mchip").forEach((c) =>
      c.addEventListener("click", () => loadTrend(c.dataset.test, c.dataset.name)));

    // preselect the most interesting test: first out-of-range, else first
    const first = ordered[0];
    if (first) loadTrend(encodeURIComponent(first.canonical), first.display);
  }

  function markChips() {
    chipsEl.querySelectorAll(".mchip").forEach((c) =>
      c.classList.toggle("on", selected && c.dataset.test === selected.canonical));
  }

  /* Trend window follows the range dropdown on REAL dates (no fixed point
     counts): if fewer than 2 readings fall inside the window we say so. */
  function windowed(series) {
    const days = RANGE_DAYS[window.HermesUI.range("labs") || "Year"];
    if (days == null) return series;
    const cut = isoDaysAgo(days);   // LOCAL date — see charts-common note
    return series.filter((s) => s.date >= cut);
  }

  function drawTrend() {
    if (!lastTrend) return;
    const data = lastTrend;
    const P = pal();
    const series = windowed(data.series);
    if (series.length < 2) {
      if (chart) chart.clear();
      trendNote.textContent = series.length + " result in this window — need at least 2 for a trend. Widen the range.";
      return;
    }
    if (!chart) chart = window.echarts.init(chartEl, null, { renderer: "svg" });
    const lo = data.ref_low, hi = data.ref_high;
    const mainSeries = {
      type: "line", smooth: 0.4, symbol: "circle", symbolSize: 7,
      lineStyle: { color: P.accent, width: 2.4, cap: "round" },
      emphasis: { focus: "series" },
      data: series.map((s) => ({
        value: [s.date, s.value],
        itemStyle: {
          color: s.in_range === false ? P.bad : P.good,
          borderColor: P.card, borderWidth: 2,
        },
      })),
    };
    // reference band when both bounds exist; markLines per existing bound
    if (lo != null && hi != null) {
      mainSeries.markArea = {
        silent: true,
        itemStyle: { color: "color-mix(in srgb," + P.good + " 15%,transparent)" },
        label: { show: true, position: "insideTopRight", formatter: "reference range",
                 color: P.good, fontSize: 9, fontWeight: 600 },
        data: [[{ yAxis: lo }, { yAxis: hi }]],
      };
    }
    const mlData = [];
    if (lo != null) mlData.push({ yAxis: lo, label: { show: true, formatter: "low " + lo, color: P.muted, fontSize: 9, position: "end" } });
    if (hi != null) mlData.push({ yAxis: hi, label: { show: true, formatter: "high " + hi, color: P.muted, fontSize: 9, position: "end" } });
    if (mlData.length) {
      mainSeries.markLine = {
        silent: true, symbol: "none",
        lineStyle: { color: P.good, type: "dashed", opacity: 0.7 },
        data: mlData,
      };
    }
    chart.setOption({
      grid: { left: 10, right: 44, top: 22, bottom: 22, containLabel: true },
      tooltip: Object.assign(tip(P), {
        trigger: "axis",
        valueFormatter: (v) => v + (data.unit ? " " + data.unit : ""),
      }),
      xAxis: { type: "category", data: series.map((s) => s.date), boundaryGap: false,
        axisLine: { lineStyle: { color: P.grid } },
        axisLabel: { color: P.muted, fontSize: 10, hideOverlap: true },
        axisTick: { show: false } },
      yAxis: { type: "value", scale: true, splitNumber: 4,
        splitLine: { lineStyle: { color: P.grid } },
        axisLabel: { color: P.muted, fontSize: 10 } },
      series: [mainSeries],
    }, true);

    let note = "Reference " + fmtRef(lo, hi) + (data.unit ? " " + data.unit : "") +
      " · green in range, red out of range.";
    if (data.historical) {
      const yrs = (data.latest_age_days / 365).toFixed(1);
      note += ` · latest reading is ~${yrs} yr old — historical, not current status.`;
    }
    trendNote.textContent = note;
  }

  async function loadTrend(encodedCanonical, name) {
    selected = { canonical: encodedCanonical, name };
    trendName.textContent = name + " — trend";
    trendNote.textContent = "";
    markChips();
    let data;
    try {
      data = await fetchJSON("/api/labs/test/" + encodedCanonical);
    } catch (e) {
      trendNote.textContent = "Could not load trend: " + e.message;
      return;
    }
    if (data.insufficient_data) {
      lastTrend = null;
      if (chart) chart.clear();
      trendNote.textContent =
        "Only " + (data.n || 0) + " result so far — need at least 2 for a trend.";
      return;
    }
    lastTrend = data;
    drawTrend();
  }

  window.HermesUI.onRange("labs", drawTrend);
  window.addEventListener("themechange", drawTrend);
  window.addEventListener("resize", () => chart && chart.resize());

  fetchJSON("/api/labs").then(renderPanels).catch((e) => {
    panelsEl.innerHTML = '<p class="muted note-txt">Could not load labs: ' + esc(e.message) + "</p>";
  });
})();
