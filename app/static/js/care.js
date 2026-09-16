/* Configurable care routines over
   skincare_log + skincare_products via /api/dash/care — SQL aggregates are
   server-side (care activity streak via gaps-and-islands, per-day done/used counts),
   the client only windows + renders. skincare_products has no stock column,
   so the design's "Products low" KPI is honestly omitted. The demo DB seeds
   no skincare rows: every card renders its own explicit honest empty state. */
(function () {
  if (!window.HermesCharts) return;
  const { fetchJSON, pal, isoDaysAgo } = window.HermesCharts;
  const RANGE_DAYS = { Day: 1, Week: 7, Month: 30, Year: 365, All: null };
  const RANGE_CAP = { Day: "today", Week: "this week", Month: "this month",
                      Year: "this year", All: "all time" };
  // adherence calendar reuses the shared mode-aware calendar (task-26); a day's
  // routine adherence maps onto the same ordered domain as day ratings
  // (lower = worse): 1 missed / 2 partial / 3 done.
  const CAL_MODE = { Day: "day", Week: "week", Month: "month", Year: "year", All: "all" };
  const CARE_WORD = { 1: "missed", 2: "partial", 3: "done" };
  const state = { products: [], lastUsed: {}, todayUsed: {}, daily: [],
                  streak: { days: 0, current: false }, error: null };

  function cardEmpty(el, msg) {
    el.replaceChildren();
    const p = document.createElement("p");
    p.className = "muted note-txt m0";
    p.textContent = msg;
    el.appendChild(p);
  }

  /* ------- KPIs ------- */
  function kpiStreak() {
    const val = document.getElementById("careStreak");
    const flag = document.getElementById("careStreakFlag");
    flag.hidden = false;
    if (state.streak.current && state.streak.days > 0) {
      val.textContent = String(state.streak.days);
      flag.className = "flag in mt-1";
      flag.textContent = "on time";
    } else {
      val.textContent = "—";
      flag.className = "chip muted mt-1";
      flag.textContent = state.error ? "unavailable" : "no care activity logged yet";
    }
  }

  function kpiAdherence() {
    const val = document.getElementById("careAdh");
    const cap = document.getElementById("careAdhCap");
    const range = window.HermesUI.range("care") || "Week";
    const days = RANGE_DAYS[range];
    const cut = days == null ? "" : isoDaysAgo(days - 1);
    const rows = state.daily.filter((r) => r.date >= cut);
    let total = 0, used = 0;
    rows.forEach((r) => { total += r.total || 0; used += r.used || 0; });
    if (!total) {
      val.textContent = "—";
      cap.textContent = state.error ? "unavailable" : "no routine logs yet";
      cap.className = "chip muted mt-1";
      return;
    }
    val.textContent = String(Math.round((used / total) * 100));
    cap.textContent = `${used} of ${total} steps · ${RANGE_CAP[range]}`;
    cap.className = "chip mt-1";
  }

  /* ------- Per-slot routine cards ------- */
  function slotCard(containerId, slot) {
    const el = document.getElementById(containerId);
    const prods = state.products.filter(
      (p) => (p.slot || "").toLowerCase() === slot && p.active !== 0);
    if (state.error) return cardEmpty(el, "Unavailable: " + state.error);
    if (!prods.length) {
      return cardEmpty(el, `No ${slot} care items on file yet — configured rows build this card.`);
    }
    el.replaceChildren();
    prods.forEach((p) => {
      const row = document.createElement("div");
      row.className = "prod";
      const done = state.todayUsed[p.product_id] === true;
      const dot = document.createElement("div");
      dot.className = "dotbtn" + (done ? " done" : "");
      dot.textContent = done ? "✓" : "";
      const name = document.createElement("div");
      name.className = "grow note-txt strong";
      name.textContent = p.product_name;
      const st = document.createElement("span");
      st.className = "muted micro";
      st.textContent = done ? "done" : "due";
      row.append(dot, name, st);
      el.appendChild(row);
    });
  }
  function slots() {
    slotCard("careMorning", "morning");
    slotCard("careEvening", "evening");
    slotCard("careWeekly", "weekly");
  }

  /* ------- adherence calendar (mode follows the dropdown, task-26) -------
     Shared mode-aware calendar (HermesCharts.ratingCal); a day's adherence is
     bucketed into 1 missed / 2 partial / 3 done, which ratingCal aggregates
     (majority per week/month) exactly like day ratings. */
  function heat() {
    if (state.error) return window.HermesCharts.calEmpty("careHeat", "Unavailable: " + state.error);
    const rows = state.daily.filter((r) => r.total > 0);
    if (!rows.length) {
      return window.HermesCharts.calEmpty("careHeat", "Nothing logged yet — skincare_log rows paint this calendar (done / partial / missed).");
    }
    const P = pal();
    const colr = { 1: P.bad, 2: P.warn, 3: P.good };
    const rating = (r) => {
      const used = r.used || 0;
      return used >= r.total ? 3 : used > 0 ? 2 : 1;
    };
    const byDate = new Map(rows.map((r) => [r.date, r]));
    window.HermesCharts.ratingCal("careHeat", {
      mode: CAL_MODE[window.HermesUI.range("care")] || "week",
      entries: rows.map((r) => ({ date: r.date, value: rating(r) })),
      colorFor: (v) => colr[v] || null,
      wordFor: (v) => CARE_WORD[v] || "?",
      titleFor: (iso, v) => {
        const r = byDate.get(iso);
        return `${iso} — ${CARE_WORD[v] || "?"}` + (r ? ` (${r.used || 0} of ${r.total})` : "");
      },
    });
  }

  /* ------- Streaks & product log ------- */
  function productLog() {
    const el = document.getElementById("careLog");
    if (state.error) return cardEmpty(el, "Unavailable: " + state.error);
    el.replaceChildren();
    if (state.streak.current && state.streak.days > 0) {
      const row = document.createElement("div");
      row.className = "row";
      const tile = document.createElement("div");
      tile.className = "habit-tile disp strong";
      tile.textContent = state.streak.days;
      const txt = document.createElement("div");
      const name = document.createElement("div");
      name.className = "note-txt strong";
      name.textContent = "Care activity";
      const sub = document.createElement("div");
      sub.className = "muted micro";
      sub.textContent = `${state.streak.days}-day streak`;
      txt.append(name, sub);
      row.append(tile, txt);
      el.appendChild(row);
    }
    if (!state.products.length) {
      return cardEmpty(el, "No care items on file yet — configured rows build this log.");
    }
    state.products.forEach((p) => {
      const row = document.createElement("div");
      row.className = "prod";
      const name = document.createElement("div");
      name.className = "grow";
      const n = document.createElement("div");
      n.className = "note-txt strong";
      n.textContent = p.product_name;
      const sub = document.createElement("div");
      sub.className = "muted micro";
      const last = state.lastUsed[p.product_id];
      sub.textContent = (p.brand ? p.brand + " · " : "") +
        (last ? "last used " + last : "never used yet") +
        (p.active === 0 ? " · retired" : "");
      name.append(n, sub);
      row.appendChild(name);
      el.appendChild(row);
    });
  }

  function render() {
    kpiStreak();
    kpiAdherence();
    slots();
    heat();
    productLog();
  }

  async function boot() {
    try {
      const d = await fetchJSON("/api/dash/care");
      state.products = d.products || [];
      state.lastUsed = d.last_used || {};
      state.todayUsed = d.today_used || {};
      state.daily = d.daily || [];
      state.streak = d.routine_streak || state.streak;
    } catch (e) {
      state.error = e.message;
    }
    render();
    window.HermesUI.onRange("care", () => { kpiAdherence(); heat(); });
  }

  window.addEventListener("themechange", () => heat());
  boot();
})();
