/* Shared chart plumbing for pages with ECharts.
   Exposes window.HermesCharts — colors always read from CSS variables at
   render time so the Hi-Fi theme system themes every chart identically.
   The design-system helpers (pal/lineOpt/barsOpt/radarOpt/gaugeOpt) are
   ported from the approved "Hermes Hi-Fi" design file; baseAxes remains for
   not-yet-migrated pages. */
(function () {
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  /* LOCAL date string — never toISOString(): that's UTC, and between local
     midnight and ~02:00 in the configured timezone it points at yesterday (review finding). */
  function localISO(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") +
           "-" + String(d.getDate()).padStart(2, "0");
  }
  function isoDaysAgo(n) {
    const d = new Date(); d.setDate(d.getDate() - n);
    return localISO(d);
  }

  /* Current streak (deterministic) = the run of consecutive calendar days,
     counting back from the most recent logged day, that each appear in
     `dates` — but ONLY if that run still reaches the present (its newest day
     is today or yesterday; yesterday still counts since today's entry may not
     be in yet). A lapsed run reports 0, never its old length (no-guilt tone) —
     mirrors the care-streak rule in dash.py /care. `dates` = ISO date strings
     that have an entry (dupes fine). Used by the Mind brain-dump card + page. */
  function streakToToday(dates) {
    const set = new Set(dates);
    const t = new Date(); t.setHours(0, 0, 0, 0);
    const y = new Date(t); y.setDate(y.getDate() - 1);
    let cur = set.has(localISO(t)) ? t : (set.has(localISO(y)) ? y : null);
    if (!cur) return 0;
    let n = 0;
    while (set.has(localISO(cur))) { n++; cur.setDate(cur.getDate() - 1); }
    return n;
  }

  async function fetchJSON(url) {
    const res = await fetch(url, { credentials: "same-origin" });
    if (res.status === 401) { location.assign("/login"); throw new Error("unauthenticated"); }
    if (!res.ok) {
      let detail = ""; try { detail = (await res.json()).error || ""; } catch (_) {}
      throw new Error(detail || ("HTTP " + res.status));
    }
    return res.json();
  }

  /* ---------- Hi-Fi design palette + option factories ---------- */

  /* Snapshot of the active theme's chart colors (design pal()). Read at
     render time; re-render on "themechange". */
  function pal() {
    const g = cssVar;
    return {
      ink: g("--ink"), muted: g("--muted"),
      good: g("--good"), warn: g("--warn"), bad: g("--bad"),
      accent: g("--accent"), track: g("--track"), grid: g("--grid"),
      card: g("--card-border"),
      cardBg: g("--card-bg"), hero: g("--hero-label"),
    };
  }

  /* Design tooltip chrome, shared by every chart type. */
  function tip(P, extra) {
    return Object.assign({
      backgroundColor: P.card, borderColor: P.grid, borderWidth: 1,
      padding: [6, 10], textStyle: { color: P.ink, fontSize: 11 },
    }, extra || {});
  }

  /* Line chart (design lineOpt): x = category labels, series = array of
     {n: name, d: data, c: color, w?, dash?, area?, smooth?, symbol?, step?}. */
  function lineOpt(x, series, ymin, ymax, extra) {
    const P = pal();
    return Object.assign({
      grid: { left: 10, right: 16, top: 26, bottom: 22, containLabel: true },
      tooltip: Object.assign(tip(P), {
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: P.grid, type: "dashed" } },
      }),
      xAxis: { type: "category", data: x, boundaryGap: false,
        axisLine: { lineStyle: { color: P.grid } },
        axisLabel: { color: P.muted, fontSize: 10, hideOverlap: true },
        axisTick: { show: false } },
      yAxis: { type: "value", min: ymin, max: ymax, scale: ymin == null, splitNumber: 4,
        splitLine: { lineStyle: { color: P.grid } },
        axisLabel: { color: P.muted, fontSize: 10 } },
      series: series.map((s) => ({
        type: "line", name: s.n, data: s.d,
        smooth: s.smooth !== false ? 0.4 : false,
        symbol: s.symbol || "none", symbolSize: 6, step: s.step || false,
        emphasis: { focus: "series" },
        lineStyle: { color: s.c, width: s.w || 2.4, type: s.dash ? "dashed" : "solid", cap: "round" },
        itemStyle: { color: s.c },
        areaStyle: s.area ? {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1,
            [{ offset: 0, color: s.c }, { offset: 1, color: "transparent" }]),
          opacity: 0.22,
        } : null,
      })),
    }, extra || {});
  }

  /* Bar chart (design barsOpt): colors = one color or per-bar array. */
  function barsOpt(cats, vals, colors, horiz, extra) {
    const P = pal();
    const cat = { type: "category", data: cats,
      axisLine: { lineStyle: { color: P.grid } },
      axisLabel: { color: P.muted, fontSize: 10, hideOverlap: true },
      axisTick: { show: false } };
    const val = { type: "value", splitNumber: 4,
      splitLine: { lineStyle: { color: P.grid } },
      axisLabel: { color: P.muted, fontSize: 10 } };
    return Object.assign({
      grid: { left: 10, right: 16, top: 22, bottom: 18, containLabel: true },
      tooltip: Object.assign(tip(P), { trigger: "axis", axisPointer: { type: "shadow" } }),
      xAxis: horiz ? val : cat,
      yAxis: horiz ? cat : val,
      series: [{
        type: "bar",
        data: vals.map((v, i) => ({
          value: v,
          itemStyle: { color: Array.isArray(colors) ? colors[i] : colors, borderRadius: 4 },
        })),
        barWidth: horiz ? "56%" : "52%",
        emphasis: { itemStyle: { opacity: 0.85 } },
      }],
    }, extra || {});
  }

  /* Radar (design radarOpt): ind = [{name, max}], vals = numbers, name = series label. */
  function radarOpt(ind, vals, name) {
    const P = pal();
    return {
      tooltip: Object.assign(tip(P), {
        trigger: "item",
        formatter: (o) => "<b>" + name + "</b><br>" +
          ind.map((x, i) => x.name + ": " + o.value[i]).join("<br>"),
      }),
      legend: { show: true, top: 0, data: [name], textStyle: { color: P.muted, fontSize: 10 } },
      radar: {
        indicator: ind, radius: "62%", center: ["50%", "56%"], splitNumber: 4,
        axisName: { color: P.muted, fontSize: 10, fontWeight: 600, triggerEvent: true },
        splitLine: { lineStyle: { color: P.grid } },
        splitArea: { areaStyle: { color: ["transparent"] } },
        axisLine: { lineStyle: { color: P.grid } },
      },
      series: [{
        type: "radar", data: [{ value: vals, name }],
        symbol: "circle", symbolSize: 5,
        lineStyle: { color: P.accent, width: 2.4 },
        areaStyle: { color: P.accent, opacity: 0.22 },
        itemStyle: { color: P.accent },
        emphasis: { lineStyle: { width: 3 } },
      }],
    };
  }

  /* Meter gauge (design nCalMeter/bTaper): value on a 210→-30 arc, no pointer,
     centered detail. opts: {value, min, max, color, detail, title, fmt, width}. */
  function gaugeOpt(opts) {
    const P = pal();
    return {
      tooltip: Object.assign(tip(P), {
        show: true,
        formatter: opts.fmt || ((p) => (opts.title ? opts.title + ": " : "") + p.value),
      }),
      series: [{
        type: "gauge", startAngle: opts.startAngle != null ? opts.startAngle : 210,
        endAngle: opts.endAngle != null ? opts.endAngle : -30,
        radius: "98%", min: opts.min || 0, max: opts.max == null ? 100 : opts.max,
        progress: { show: true, width: opts.width || 10, roundCap: true,
                    itemStyle: { color: opts.color || P.accent } },
        pointer: { show: false },
        axisLine: { lineStyle: { width: opts.width || 10, color: [[1, P.track]] } },
        axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false },
        anchor: { show: false },
        detail: { valueAnimation: true, offsetCenter: [0, "-6%"],
                  formatter: opts.detail || "{value}",
                  fontSize: opts.detailSize || 26, fontWeight: 700,
                  fontFamily: "Spectral", color: P.ink },
        title: { offsetCenter: [0, "30%"], fontSize: 11, fontWeight: 700, color: P.muted },
        data: [{ value: opts.value, name: opts.title || "" }],
      }],
    };
  }

  /* Tiny sparkline (design spark). */
  function sparkOpt(data, color) {
    return {
      grid: { left: 2, right: 2, top: 6, bottom: 2 },
      xAxis: { type: "category", show: false, data: data.map((_, i) => i) },
      yAxis: { type: "value", show: true, scale: true,
               splitLine: { show: false }, axisLabel: { show: false } },
      series: [{ type: "line", data, smooth: 0.4, symbol: "none",
                 lineStyle: { color, width: 2 }, areaStyle: { color, opacity: 0.14 } }],
    };
  }

  /* Day-ratings calendar heatmap (owner-review r2 — rounded cells + real
     gaps). Built as a DOM CSS grid, not ECharts: ECharts' calendar
     coordinate system only fakes a gap via a card-bg-colored cell border,
     which can't hit the design's flat, evenly-gapped look — the approved
     design file (docs/design/hermes-hi-fi.html calYear()) renders this same
     widget as a DOM grid too. CSP is style-src 'self' (no inline style=""),
     so every dynamic value here goes through CSSOM (el.style.x =), never an
     HTML string with a style attribute; static geometry lives in panel.css
     (.daycal family). Tradeoff: ECharts' styled tooltip chrome is dropped in
     favor of the native `title` attribute on rated cells.
     orient 'vertical'  = weeks stacked, 7 day-of-week columns (Week/Month
                           windows — matches the design's month grid).
     orient 'horizontal' = weeks as columns, 7 day-of-week rows (Year/
                           multi-month windows — GitHub-contribution style).
     opts: {start, end, orient, cellHeight, gap, valueFor(iso), colorFor(value),
            titleFor(iso,value), onClick(iso,value), weekStart, showDowLabels,
            showMonthLabels, isSelected(iso)}. gap (px, default 6) scales down
     the geometry together (cell gap + radius) for dense many-week horizontal
     windows (e.g. a full year) without changing the vertical month/week look,
     which is pinned to the design's exact 6px gap / 6px radius.
     isSelected (T51, additive, optional) — a rated cell for which it returns
     true gets the `.cal-sel` outline class. Purely a paint-time predicate; the
     caller owns the selection state and re-invokes dayCal to repaint it. */
  function dayCal(containerId, opts) {
    const el = document.getElementById(containerId);
    if (!el) return;
    const {
      start, end, orient = "vertical", cellHeight = 28, gap = 6,
      // Sunday-first (S M T W T F S) — matches the owner's target image and
      // the design file's calYear() month branch (raw Date.getDay()).
      valueFor, colorFor, titleFor, onClick, weekStart = 0,
      showDowLabels = true, showMonthLabels = false, emptyColor, isSelected,
    } = opts;

    el.style.height = "auto";
    el.classList.add("daycal");
    el.replaceChildren();

    const gapPx = gap + "px";
    const radiusPx = Math.max(2, Math.round(gap)) + "px";
    const DOW = ["S", "M", "T", "W", "T", "F", "S"];
    const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul",
                 "Aug", "Sep", "Oct", "Nov", "Dec"];
    const orderedDow = Array.from({ length: 7 }, (_, i) => (weekStart + i) % 7);
    const toDate = (iso) => new Date(iso + "T00:00:00");
    const startDate = toDate(start);
    const endDate = toDate(end);
    const offset = (startDate.getDay() - weekStart + 7) % 7;
    const totalDays = Math.round((endDate - startDate) / 86400000) + 1;
    const weeks = Math.ceil((offset + totalDays) / 7);

    let body, monthsHead;
    if (orient === "vertical") {
      if (showDowLabels) {
        const head = document.createElement("div");
        head.className = "daycal-head";
        head.style.gap = gapPx;
        orderedDow.forEach((d) => {
          const s = document.createElement("span");
          s.textContent = DOW[d];
          head.appendChild(s);
        });
        el.appendChild(head);
      }
      body = document.createElement("div");
      body.className = "daycal-grid vert";
      body.style.gap = gapPx;
      body.style.gridAutoRows = cellHeight + "px";
      el.appendChild(body);
    } else {
      if (showMonthLabels) {
        monthsHead = document.createElement("div");
        monthsHead.className = "daycal-months";
        monthsHead.style.gap = gapPx;
        monthsHead.style.gridTemplateColumns = `repeat(${weeks}, 1fr)`;
        el.appendChild(monthsHead);
      }
      const hz = document.createElement("div");
      hz.className = "daycal-hz";
      hz.style.gap = gapPx;
      if (showDowLabels) {
        const rl = document.createElement("div");
        rl.className = "daycal-rowlabels";
        rl.style.gap = gapPx;
        rl.style.gridTemplateRows = `repeat(7, ${cellHeight}px)`;
        orderedDow.forEach((d) => {
          const s = document.createElement("span");
          s.textContent = DOW[d];
          rl.appendChild(s);
        });
        hz.appendChild(rl);
      }
      body = document.createElement("div");
      body.className = "daycal-grid horiz";
      body.style.gap = gapPx;
      body.style.gridAutoFlow = "column";
      body.style.gridTemplateRows = `repeat(7, ${cellHeight}px)`;
      body.style.gridTemplateColumns = `repeat(${weeks}, 1fr)`;
      hz.appendChild(body);
      el.appendChild(hz);
    }

    for (let i = 0; i < offset; i++) {
      const blank = document.createElement("div");
      blank.className = "daycal-cell blank";
      body.appendChild(blank);
    }

    for (let i = 0; i < totalDays; i++) {
      const d = new Date(startDate);
      d.setDate(d.getDate() + i);
      const iso = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") +
                  "-" + String(d.getDate()).padStart(2, "0");
      const value = valueFor ? valueFor(iso) : null;
      const cell = document.createElement("div");
      cell.className = "daycal-cell";
      cell.style.borderRadius = radiusPx;
      if (value != null) {
        const color = colorFor ? colorFor(value) : null;
        if (color) cell.style.background = color;
        if (titleFor) cell.title = titleFor(iso, value);
        if (onClick) {
          cell.classList.add("clickable");
          cell.addEventListener("click", () => onClick(iso, value));
        }
        if (isSelected && isSelected(iso)) cell.classList.add("cal-sel");
      } else if (emptyColor) {
        // in-range but unlogged day → neutral track box (distinguishable from
        // a rated one), so the grid reads as a solid shape not floating cells.
        cell.style.background = emptyColor;
      }
      body.appendChild(cell);

      if (monthsHead && (d.getDate() === 1 || i === 0)) {
        const col = Math.floor((offset + i) / 7);
        const span = document.createElement("span");
        span.textContent = MON[d.getMonth()];
        span.style.gridColumn = String(col + 1);
        monthsHead.appendChild(span);
      }
    }
  }

  /* Honest empty state for a dayCal container (mirrors the echarts `empty()`
     graphic-text pattern used elsewhere, but for the DOM grid). */
  function calEmpty(containerId, msg) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.style.height = "auto";
    el.classList.remove("daycal");
    el.replaceChildren();
    const p = document.createElement("p");
    p.className = "muted note-txt m0";
    p.textContent = msg;
    el.appendChild(p);
  }

  /* ---------- mode-aware rating calendar (owner-review r3) ----------
     ONE shared implementation of the day/week/month/year/all-time calendar
     the owner asked for globally (task-26). Pages supply only their data
     (entries) + colorFor/wordFor/titleFor/onClick; ALL windowing and the
     week/month aggregation live here so no page re-implements it.

     entries: [{date:'YYYY-MM-DD', value:<key>}] — value is a small ORDERED
       rating key where LOWER = WORSE (day ratings 1 hard / 2 ok / 3 good;
       care 1 missed / 2 partial / 3 done). colorFor(value) maps it to a color.
     modes:
       day   — one big box for the most-recent logged day (label TODAY when
               that is today, else the ISO date).
       week  — 7 day boxes, one row (delegates to dayCal, one week window).
       month — the classic month grid (delegates to dayCal, this-month window).
       year  — JAN..DEC columns; each column a stack of that month's weeks,
               each box the MAJORITY rating of the week's logged days. Weeks
               break on weekStart AND on month boundaries (a week belongs to
               the month it starts in — matches the design's per-month stacks).
       all   — one row per year that has data, one box per month = the month's
               majority rating.
     Majority rule (deterministic): count logged days per rating in the bucket;
     highest count wins; ties break toward the WORSE (lowest) value so rough
     patches are never hidden. Buckets with zero logged days render neutral
     (track-colored), visibly distinct from rated ones.
     Returns {logged, label}: logged-day count in the visible window + a short
     span label, so a page can caption without recomputing the window.
     isSelected(iso) (T51, additive, optional) — forwarded to dayCal for the
     week/month leaf grids; for the custom day/year/all box grids it's checked
     against each box's own click-iso (the day, or the week/month's start
     date for year/all — same iso onClick already fires with). */
  function startOfWeek(d, weekStart) {
    const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    x.setDate(x.getDate() - ((x.getDay() - weekStart + 7) % 7));
    return x;
  }
  function monthWeeks(year, month, weekStart) {
    const last = new Date(year, month + 1, 0).getDate();
    const weeks = []; let cur = [];
    for (let dd = 1; dd <= last; dd++) {
      const date = new Date(year, month, dd);
      if (date.getDay() === weekStart && cur.length) { weeks.push(cur); cur = []; }
      cur.push(date);
    }
    if (cur.length) weeks.push(cur);
    return weeks;
  }
  function majorityValue(vals) {
    if (!vals.length) return null;
    const counts = new Map();
    vals.forEach((v) => counts.set(v, (counts.get(v) || 0) + 1));
    let best = null, bestC = -1;
    counts.forEach((c, v) => { if (c > bestC || (c === bestC && v < best)) { best = v; bestC = c; } });
    return best;
  }

  function ratingCal(containerId, opts) {
    const el = document.getElementById(containerId);
    if (!el) return null;
    const {
      mode = "month", entries = [], anchor,
      colorFor, wordFor, titleFor, onClick, weekStart = 0, emptyColor, isSelected,
    } = opts;
    const track = emptyColor || cssVar("--track");
    const byDate = new Map();
    entries.forEach((e) => { if (e && e.value != null) byDate.set(e.date, e.value); });
    const todayISO = localISO(new Date());
    const anchorDate = anchor ? new Date(anchor + "T00:00:00") : new Date();
    const valueFor = (iso) => (byDate.has(iso) ? byDate.get(iso) : null);
    const leafTitle = (iso, v) => (titleFor ? titleFor(iso, v)
      : iso + (wordFor ? " — " + wordFor(v) : ""));

    // ----- leaf grids (week / month) delegate to dayCal -----
    if (mode === "week" || mode === "month") {
      let start, end;
      if (mode === "week") {
        const s = startOfWeek(anchorDate, weekStart);
        const e2 = new Date(s); e2.setDate(e2.getDate() + 6);
        start = localISO(s); end = localISO(e2);
      } else {
        const y = anchorDate.getFullYear(), m = anchorDate.getMonth();
        start = localISO(new Date(y, m, 1)); end = localISO(new Date(y, m + 1, 0));
      }
      dayCal(containerId, {
        start, end, orient: "vertical", weekStart,
        cellHeight: mode === "week" ? 60 : 32, gap: 6,
        showDowLabels: true, emptyColor: track,
        valueFor, colorFor, titleFor: leafTitle, onClick, isSelected,
      });
      let n = 0; byDate.forEach((_, d) => { if (d >= start && d <= end) n++; });
      return { logged: n, label: mode === "week" ? "this week" : "this month" };
    }

    // ----- custom modes: day / year / all -----
    el.style.height = "auto";
    el.classList.add("daycal");
    el.replaceChildren();

    const mkBox = (value, title, clickIso, heightPx, radiusPx) => {
      const c = document.createElement("div");
      c.className = "daycal-cell";
      c.style.height = heightPx + "px";
      c.style.borderRadius = radiusPx + "px";
      c.style.background = value != null && colorFor ? colorFor(value) : track;
      if (title) c.title = title;
      if (value != null && isSelected && clickIso && isSelected(clickIso)) c.classList.add("cal-sel");
      if (value != null && onClick && clickIso) {
        c.classList.add("clickable");
        c.addEventListener("click", () => onClick(clickIso, value));
      }
      return c;
    };

    if (mode === "day") {
      let day = null;
      byDate.forEach((_, d) => { if (!day || d > day) day = d; });
      const lbl = document.createElement("div");
      lbl.className = "ratingcal-lbl";
      lbl.textContent = day ? (day === todayISO ? "TODAY" : day) : "TODAY";
      el.appendChild(lbl);
      const v = day ? byDate.get(day) : null;
      el.appendChild(mkBox(v, day ? leafTitle(day, v) : null, day, 150, 8));
      if (v == null) {
        const p = document.createElement("p");
        p.className = "muted note-txt m0 mt-1";
        p.textContent = "No rating logged for this day yet.";
        el.appendChild(p);
      }
      return { logged: v == null ? 0 : 1, label: day && day !== todayISO ? day : "today" };
    }

    const MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL",
                 "AUG", "SEP", "OCT", "NOV", "DEC"];
    const labels = document.createElement("div");
    labels.className = "ratingcal-collabels";
    MON.forEach((m) => { const s = document.createElement("span"); s.textContent = m; labels.appendChild(s); });
    el.appendChild(labels);

    if (mode === "year") {
      const Y = anchorDate.getFullYear();
      const cols = document.createElement("div");
      cols.className = "ratingcal-cols";
      let n = 0;
      for (let m = 0; m < 12; m++) {
        const stack = document.createElement("div");
        stack.className = "ratingcal-colstack";
        monthWeeks(Y, m, weekStart).forEach((wk) => {
          const vals = [];
          wk.forEach((dt) => { const v = byDate.get(localISO(dt)); if (v != null) { vals.push(v); n++; } });
          const maj = majorityValue(vals);
          const wkStart = localISO(wk[0]);
          const title = MON[m] + " " + Y + " · wk of " + wkStart +
            (maj != null ? " — mostly " + (wordFor ? wordFor(maj) : maj) +
              " (" + vals.length + " logged)" : " — no logs");
          stack.appendChild(mkBox(maj, title, wkStart, 16, 4));
        });
        cols.appendChild(stack);
      }
      el.appendChild(cols);
      return { logged: n, label: String(Y) };
    }

    // mode === "all": one row per year with data, one box per month
    const years = new Set();
    byDate.forEach((_, d) => years.add(d.slice(0, 4)));
    let n = 0;
    Array.from(years).sort().reverse().forEach((yr) => {
      const row = document.createElement("div");
      row.className = "ratingcal-row";
      for (let m = 0; m < 12; m++) {
        const prefix = yr + "-" + String(m + 1).padStart(2, "0");
        const vals = [];
        byDate.forEach((v, d) => { if (d.slice(0, 7) === prefix) { vals.push(v); n++; } });
        const maj = majorityValue(vals);
        const title = MON[m] + " " + yr +
          (maj != null ? " — mostly " + (wordFor ? wordFor(maj) : maj) +
            " (" + vals.length + " logged)" : " — no logs");
        row.appendChild(mkBox(maj, title, prefix + "-01", 26, 6));
      }
      el.appendChild(row);
    });
    return { logged: n, label: "all time" };
  }

  /* legacy (pre-redesign pages) */
  function baseAxes() {
    return {
      xAxis: { type: "time",
        axisLine: { lineStyle: { color: cssVar("--card-edge") } },
        axisLabel: { color: cssVar("--text-dim"), hideOverlap: true } },
      yAxis: { type: "value", scale: true,
        splitLine: { lineStyle: { color: cssVar("--card-edge") } },
        axisLabel: { color: cssVar("--text-dim") } },
      grid: { left: 44, right: 14, top: 16, bottom: 26 },
      tooltip: { trigger: "axis", confine: true },
    };
  }

  window.HermesCharts = { cssVar, localISO, isoDaysAgo, streakToToday, fetchJSON,
                          baseAxes, pal, tip, lineOpt, barsOpt, radarOpt, gaugeOpt,
                          sparkOpt, dayCal, ratingCal, calEmpty };
})();
