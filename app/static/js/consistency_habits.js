/* task-35 Habit-ledger drill sub-page. Renders /api/dash/habits-ledger-detail
   verbatim: no client-side math beyond display formatting (percent rounding,
   the 21-day progress-bar fraction) — the status classification and the
   "ready for a new habit?" gate are both computed server-side (dash.py
   habits_ledger_detail; see that route's docstring for the exact rule set),
   so this file has nothing to get wrong. No echarts here (no charts on this
   page), so no vendor dependency beyond charts-common's fetchJSON. */
(function () {
  if (!window.HermesCharts) return;
  const { fetchJSON } = window.HermesCharts;

  // status -> the design's .flag colour class (green/amber/red, same
  // grammar as labs/nutrition/care); Rebuilding and Fragile are both
  // red-toned per the design (image 11) — wording stays no-guilt either way.
  const FLAG_CLASS = { Established: "in", Building: "bd", Rebuilding: "out", Fragile: "out" };
  const BAR_VAR = { Established: "--good", Building: "--warn", Rebuilding: "--bad", Fragile: "--bad" };

  function habitCard(h) {
    const card = document.createElement("div");
    card.className = "card habit-card";

    const top = document.createElement("div");
    top.className = "row-top";
    const tile = document.createElement("div");
    tile.className = "habit-tile big disp strong";
    tile.textContent = h.current_streak;

    const body = document.createElement("div");
    body.className = "grow";
    const head = document.createElement("div");
    head.className = "row between";
    const name = document.createElement("span");
    name.className = "strong";
    name.textContent = h.habit;
    const chip = document.createElement("span");
    chip.className = "flag " + (FLAG_CLASS[h.status] || "");
    chip.textContent = h.status;
    head.append(name, chip);
    const sub = document.createElement("div");
    sub.className = "muted micro mt-1";
    sub.textContent = `started ${h.first_date} · best ${h.best_streak}d · ` +
      `${Math.round(h.adherence_30 * 100)}% adherence (30d)`;
    body.append(head, sub);
    top.append(tile, body);

    const barWrap = document.createElement("div");
    barWrap.className = "mt-3";
    const barHead = document.createElement("div");
    barHead.className = "row between mb-1";
    const barLabel = document.createElement("span");
    barLabel.className = "muted micro";
    barLabel.textContent = "TO ESTABLISHED (21D)";
    const barVal = document.createElement("span");
    barVal.className = "muted micro";
    const toEst = Math.min(h.current_streak, 21);
    barVal.textContent = `${toEst}/21`;
    barHead.append(barLabel, barVal);
    const bg = document.createElement("div");
    bg.className = "barbg";
    const fill = document.createElement("div");
    fill.className = "barfill";
    fill.style.width = (toEst / 21 * 100) + "%";
    fill.style.background = `var(${BAR_VAR[h.status] || "--ink"})`;   // var() string → auto-recolors on theme change
    bg.appendChild(fill);
    barWrap.append(barHead, bg);

    card.append(top, barWrap);
    return card;
  }

  function renderGate(gate, rows) {
    const chip = document.getElementById("hl-gate-chip");
    const advice = document.getElementById("hl-gate-advice");
    const stats = document.getElementById("hl-gate-stats");
    if (!rows.length) {
      chip.hidden = true;
      stats.hidden = true;
      advice.textContent = "No habits logged yet — nothing to gate on.";
      return;
    }
    chip.hidden = false;
    chip.className = "flag " + (gate.ready ? "in" : "bd");
    chip.textContent = gate.ready ? "ready" : "not yet";
    advice.textContent = gate.advice || "";
    stats.hidden = false;
    document.getElementById("hl-n-established").textContent =
      rows.filter((r) => r.status === "Established").length;
    // BUILDING/NEW is the design's combined bucket for "still climbing to 21"
    // (Building = first time, Rebuilding = climbing again after a break) —
    // the per-card chip keeps the precise 4-way status; this overview count
    // is deliberately coarser, matching the 3-box layout in image 11.
    document.getElementById("hl-n-building").textContent =
      rows.filter((r) => r.status === "Building" || r.status === "Rebuilding").length;
    document.getElementById("hl-n-fragile").textContent =
      rows.filter((r) => r.status === "Fragile").length;
  }

  async function boot() {
    const grid = document.getElementById("habits-grid");
    const emptyEl = document.getElementById("habits-empty");
    let data;
    try {
      data = await fetchJSON("/api/dash/habits-ledger-detail");
    } catch (e) {
      document.getElementById("hl-gate-advice").textContent = "Ledger unavailable: " + e.message;
      document.getElementById("hl-gate-chip").hidden = true;
      document.getElementById("hl-gate-stats").hidden = true;
      emptyEl.textContent = "Ledger unavailable: " + e.message;
      emptyEl.hidden = false;
      return;
    }
    const rows = data.rows || [];
    renderGate(data.gate || {}, rows);
    if (!rows.length) {
      emptyEl.textContent = "No habit logs yet — the evening check-in builds this page.";
      emptyEl.hidden = false;
      return;
    }
    rows.forEach((h) => grid.appendChild(habitCard(h)));
  }
  boot();
})();
