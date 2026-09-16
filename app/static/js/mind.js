/* Mind pillar (design v-mind). Medication tab: installation-configured dose
   sums (/api/dash/safety), vitals strip = real BP (/api/dash/safety)
   + RHR/HRV (/api/dash/metrics) with a plain arithmetic delta-vs-window-mean
   flag (no clinical thresholds invented — this app never diagnoses), mood/
   focus + user-defined assessments + brain-dump from real database rows.
   Social tab has no schema yet — it's static honest scaffolding in mind.html
   (no JS, no charts to draw). General tab (90-day
   mood + notes) loads lazily on first tab-open since its chart container is
   hidden at boot (display:none — ECharts can't size into that). */
(function () {
  if (typeof echarts === "undefined" || !window.HermesCharts) return;
  const { fetchJSON, pal, lineOpt, sparkOpt, isoDaysAgo, streakToToday } = window.HermesCharts;
  const charts = {};
  const state = { safety: { medication: "medication", vitals: [], doses: [] }, metrics: [], subjective: [], assessments: [] };
  let genLoaded = false;

  /* task-37 global period dd (same label→days map + client-side windowing as
     recovery/labs): every range-capable card slices the fully-fetched (days=all)
     superset to the selected window, so "All time" is genuinely unbounded and
     no card ever shows a wrong-range slice silently. */
  const RANGE_DAYS = { Day: 1, Week: 7, Month: 30, Year: 365, All: null };
  const WINDOW_LABEL = { Day: "past day", Week: "last 7 days", Month: "last 30 days",
    Year: "last year", All: "all time" };
  const range = () => window.HermesUI.range("mind") || "Year";
  function windowed(rows) {
    const days = RANGE_DAYS[range()];
    if (days == null) return rows;
    const cut = isoDaysAgo(days);
    return rows.filter((r) => r.date >= cut);
  }

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
  function sparkEmpty(id) {
    chart(id).setOption({ graphic: { type: "text", left: "center", top: "middle",
      style: { text: "no data", fill: pal().muted, fontSize: 10 } } });
  }

  /* one row per DATE, prefer apple over fitbit — same provenance rule as
     the dashboard's vitals stat cards (comparisons are between days, never
     silently across devices). */
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
     a clinical "borderline"/"out of range" judgment (this app never assesses
     that; only a prescriber can). */
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

  function cvCell(id, vals, valText, goodWhenDown) {
    const valEl = document.getElementById(id + "Val");
    const flagEl = document.getElementById(id + "Flag");
    if (!valText) {
      valEl.textContent = "—";
      flagEl.hidden = true;
      return sparkEmpty(id);
    }
    valEl.textContent = valText;
    const flag = trendFlag(vals, goodWhenDown);
    const P = pal();
    if (flag.text) {
      flagEl.hidden = false;
      flagEl.className = "flag mt-1 " + flag.cls;
      flagEl.textContent = flag.text;
    } else {
      flagEl.hidden = true;
    }
    const color = flag.cls === "bd" ? P.warn : flag.cls === "in" ? P.good : P.accent;
    chart(id).setOption(sparkOpt(vals.slice(-14), color));
  }

  function noteRows(entries, containerId) {
    const el = document.getElementById(containerId);
    el.replaceChildren();
    entries.forEach((e) => {
      const div = document.createElement("div");
      div.className = "note-card";
      const d = document.createElement("div");
      d.className = "muted micro mb-1";
      d.textContent = e.date;
      const t = document.createElement("div");
      t.textContent = e.text;
      div.append(d, t);
      el.appendChild(div);
    });
  }
  function noteEmpty(containerId, msg) {
    const el = document.getElementById(containerId);
    el.replaceChildren();
    const p = document.createElement("p");
    p.className = "muted note-txt m0";
    p.textContent = msg;
    el.appendChild(p);
  }

  /* ------- Medication tab ------- */
  function doseHistory() {
    const P = pal();
    const doses = windowed(state.safety.doses || []);
    const chip = document.getElementById("mDoseHistoryChip");
    if (!doses.length) {
      chip.textContent = "not logged yet";
      return empty("mDoseHistory", `No configured medication doses in the ${WINDOW_LABEL[range()]}.`);
    }
    chip.textContent = `current · ${doses[doses.length - 1].dose_total_mg} mg/day`;
    chart("mDoseHistory").setOption(lineOpt(
      doses.map((d) => d.date.slice(5)),
      [{ n: "Dose mg/day", d: doses.map((d) => d.dose_total_mg), c: P.accent, step: "end", smooth: false, area: true }],
      0, null));
  }

  function cvSafety() {
    const metrics = windowed(state.metrics);
    const bpRows = windowed(state.safety.vitals || []).filter((v) => v.systolic != null && v.diastolic != null);
    const last = bpRows[bpRows.length - 1];
    cvCell("mBp", bpRows.map((v) => v.systolic), last ? `${last.systolic}/${last.diastolic}` : null, true);

    const rhr = perDateEntries(metrics, "resting_hr");
    cvCell("mRhr", rhr.map((e) => e[1]), rhr.length ? String(Math.round(rhr[rhr.length - 1][1] * 10) / 10) : null, true);

    const hrv = perDateEntries(metrics, "hrv_ms");
    cvCell("mHrv", hrv.map((e) => e[1]), hrv.length ? String(Math.round(hrv[hrv.length - 1][1] * 10) / 10) : null, false);
  }

  function moodFocus() {
    const P = pal();
    const winEl = document.getElementById("mMoodWin");
    if (winEl) winEl.textContent = WINDOW_LABEL[range()];
    const rows = windowed(state.subjective).filter((r) => r.mood != null || r.focus != null);
    if (!rows.length) return empty("mMood", "Mood & focus appear after evening check-ins");
    chart("mMood").setOption(lineOpt(
      rows.map((r) => r.date.slice(5)),
      [{ n: "Mood", d: rows.map((r) => r.mood), c: P.accent, area: true },
       { n: "Focus", d: rows.map((r) => r.focus), c: P.warn, dash: true }],
      1, 5,
      { legend: { show: true, top: 0, right: 0, textStyle: { color: P.muted, fontSize: 10 } } }));
  }

  function assessmentChart() {
    const P = pal();
    const rows = windowed(state.assessments);
    if (!rows.length) return empty("mAssessment", "No user-defined assessments in this range.");
    const latestScale = rows[rows.length - 1].scale;
    const selected = rows.filter((r) => r.scale === latestScale);
    const maxima = selected.map((r) => r.max_score).filter((value) => value != null);
    chart("mAssessment").setOption(lineOpt(
      selected.map((r) => r.date.slice(5)),
      [{ n: latestScale, d: selected.map((r) => r.score), c: P.good, area: true }],
      0, maxima.length ? Math.max(...maxima) : null));
  }

  function brainDump() {
    const entries = state.subjective.filter((r) => r.brain_dump && r.brain_dump.trim())
      .sort((a, b) => (a.date < b.date ? 1 : -1));
    // design chip = current streak (consecutive days with an entry, reaching
    // today/yesterday); 0 shows "none yet" rather than a hollow "0-day streak".
    const streak = streakToToday(entries.map((e) => e.date));
    document.getElementById("bdChip").textContent = streak ? `${streak}-day streak` : "none yet";
    if (!entries.length) {
      return noteEmpty("braindump", "No brain-dumps logged yet — the evening check-in captures the 30-sec voice/text dump.");
    }
    noteRows(entries.slice(0, 3).map((e) => ({ date: e.date, text: e.brain_dump })), "braindump");
  }

  /* ------- General tab (lazy: hidden container at boot) ------- */
  function generalMood() {
    const P = pal();
    const winEl = document.getElementById("mGenWin");
    if (winEl) winEl.textContent = WINDOW_LABEL[range()];
    const rows = windowed(state.subjective).filter((r) => r.mood != null);
    if (!rows.length) return empty("mGen", "Overall mood appears after evening check-ins");
    chart("mGen").setOption(lineOpt(
      rows.map((r) => r.date.slice(5)),
      [{ n: "Mood", d: rows.map((r) => r.mood), c: P.accent, area: true }],
      1, 5));
  }

  function genNotesList() {
    const entries = state.subjective.filter((r) => r.notes && r.notes.trim())
      .sort((a, b) => (a.date < b.date ? 1 : -1)).slice(0, 5);
    if (!entries.length) return noteEmpty("genNotes", "No notes logged yet.");
    noteRows(entries.map((e) => ({ date: e.date, text: e.notes })), "genNotes");
  }

  async function loadState() {
    // days=all supersets: the range dd windows every card client-side, so the
    // fetched set must span the widest option ("All time") — never a fixed cap.
    const [safety, metrics, subjective, assessments] = await Promise.allSettled([
      fetchJSON("/api/dash/safety?days=all"),
      fetchJSON("/api/dash/metrics?days=all"),
      fetchJSON("/api/dash/subjective?days=all"),
      fetchJSON("/api/dash/assessments"),
    ]);
    if (safety.status === "fulfilled") state.safety = safety.value;
    if (metrics.status === "fulfilled") state.metrics = metrics.value.rows || [];
    if (subjective.status === "fulfilled") state.subjective = subjective.value.rows || [];
    if (assessments.status === "fulfilled") state.assessments = assessments.value.rows || [];
  }

  async function boot() {
    await loadState();
    doseHistory(); cvSafety(); moodFocus(); assessmentChart(); brainDump();
    window.HermesUI.onTab("mind", (tab) => {
      if (tab === "gen" && !genLoaded) { genLoaded = true; generalMood(); genNotesList(); }
    });
    // range dd re-windows the range-capable charts; brain-dump/notes are
    // recency lists (latest N + live streak) and don't subscribe (T26 rule).
    window.HermesUI.onRange("mind", () => {
      doseHistory(); cvSafety(); moodFocus(); assessmentChart();
      if (genLoaded) generalMood();
    });
  }

  window.addEventListener("themechange", () => {
    doseHistory(); cvSafety(); moodFocus(); assessmentChart();
    if (genLoaded) generalMood();
  });
  window.addEventListener("resize", () => Object.values(charts).forEach((c) => c && c.resize()));
  boot();
})();
