/* Consistency pillar (design v-consistency). Engine output rendered verbatim:
   kept-word gauge = scores.consistency, streaks = engine habits, follow-through
   = insights adherence (first-half vs second-half rates), ledger = SQL
   aggregates from /api/dash/habits-ledger. No client math.

   Header range dd (owner-review r2/r3, design `.dd`/HermesUI.range pattern —
   same as recovery/labs): genuinely re-windows the cards whose numbers are
   SQL/engine aggregates over a date range — follow-through trend
   (/api/insights/adherence?days=), the habit ledger
   (/api/dash/habits-ledger?days=), and (owner-review r3, task-26) the
   day-ratings calendar, which now FOLLOWS the dd via the shared mode-aware
   HermesCharts.ratingCal (day/week/month/year/all-time). Both range endpoints
   validate the whitelisted `days` value server-side (400 on anything else),
   same style as /api/dash/sleep; the "Day" option added in r3 needs the "1"
   value both whitelists now accept.

   The kept-word gauge and the Streaks list are deliberately NOT wired to
   the dd: scores.consistency computes its kept/streak numbers over a fixed
   rolling 30-day window inside health.py regardless of any `--days` you
   pass it (toolkit/health.py `scores()` — window_days=30 is hardcoded for
   that one score), so a dropdown "controlling" it would be decorative, not
   real. Wiring it up would mean changing the shared scores() engine command
   (used by the dashboard hub too) — out of scope for "add the missing
   dropdown". The design's own mock doesn't re-render Streaks per range
   either. */
(function () {
  if (typeof echarts === "undefined" || !window.HermesCharts) return;
  const { fetchJSON, pal, tip, gaugeOpt, barsOpt } = window.HermesCharts;
  const charts = {};
  // dd label -> the whitelisted `days` query value both endpoints accept
  // (dash.py's ALLOWED_DAYS strings; insights.py's ADHERENCE_ALLOWED_DAYS
  // mirrors the same four keys).
  const RANGE_DAYS = { Day: "1", Week: "7", Month: "30", Year: "365", All: "all" };
  const CAL_MODE = { Day: "day", Week: "week", Month: "month", Year: "year", All: "all" };
  const RATE_WORD = { 1: "hard", 2: "ok", 3: "good" };

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

  let scoresData = null;

  async function ring() {
    const cap = document.getElementById("cons-ring-cap");
    if (!scoresData) {
      try { scoresData = (await fetchJSON("/api/dash/scores")).result || {}; }
      catch (e) { cap.textContent = "Scores unavailable: " + e.message; return; }
    }
    const s = (scoresData.scores || {}).consistency;
    const chips = document.getElementById("cons-chips");
    if (s && !s.insufficient_data) {
      const i = s.inputs || {};
      chart("cons-ring").setOption(gaugeOpt({
        value: s.score, min: 0, max: 100, width: 14,
        startAngle: 220, endAngle: -40, color: pal().accent,
        title: "CONSISTENCY", detailSize: 34,
        fmt: () => `Consistency: ${s.score} / 100`,
      }));
      cap.innerHTML = "";
      cap.append("Kept your word ");
      const b = document.createElement("b");
      b.textContent = `${i.kept} of ${i.days_logged}`;
      cap.append(b, " logged days");
      const prevStreak = chips.querySelector(".streak-chip");
      if (prevStreak) prevStreak.remove();
      if (i.streak > 0) {
        const c = document.createElement("span");
        c.className = "chip streak-chip";
        c.textContent = `🔥 ${i.streak}-day run`;
        chips.prepend(c);
      }
    } else {
      empty("cons-ring", "—");
      cap.textContent = (s && s.reason) || "The evening check-in unlocks this";
    }
    // streak rows (engine habits)
    const list = document.getElementById("cons-streaks");
    const habits = scoresData.habits || [];
    if (habits.length) {
      list.replaceChildren();
      habits.sort((a, b) => b.streak - a.streak);
      habits.forEach((h) => {
        const row = document.createElement("div");
        row.className = "row";
        const tile = document.createElement("div");
        tile.className = "habit-tile disp strong";
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
      });
    }
  }

  async function adherence() {
    const note = document.getElementById("cons-adh-note");
    const days = RANGE_DAYS[window.HermesUI.range("consistency") || "Year"];
    let result;
    try { result = (await fetchJSON(`/api/insights/adherence?days=${days}`)).result; }
    catch (e) { empty("cons-adh", "Engine unavailable: " + e.message); return; }
    if (result.insufficient_data) {
      empty("cons-adh", "Not enough word logs yet — the nightly kept/partly/broke tap builds this.");
      document.getElementById("adh-meta2").textContent = `${result.days_logged ?? 0} days logged`;
      return;
    }
    document.getElementById("adh-meta2").textContent =
      `${Math.round(result.rate * 100)}% over ${result.days_logged} logged days`;
    const P = pal();
    if (result.trend) {
      const a = Math.round(result.trend.first_half_rate * 100);
      const b = Math.round(result.trend.second_half_rate * 100);
      chart("cons-adh").setOption(barsOpt(
        ["first half", "second half"], [a, b],
        [b >= a ? P.accent : P.warn, b >= a ? P.good : P.bad], false,
        { yAxis: { type: "value", min: 0, max: 100, splitNumber: 4,
                   splitLine: { lineStyle: { color: P.grid } },
                   axisLabel: { color: P.muted, fontSize: 10 } },
          tooltip: Object.assign(tip(P), { trigger: "axis",
            valueFormatter: (v) => v + "% follow-through" }) }));
      note.textContent =
        `kept ${result.counts.kept} · partly ${result.counts.partly} · broke ${result.counts.broke}` +
        ` · window halves computed by the engine`;
    } else {
      empty("cons-adh", `Follow-through ${Math.round(result.rate * 100)}% — trend appears once the window has enough days.`);
    }
  }

  async function ledger() {
    const range = window.HermesUI.range("consistency") || "Year";
    const days = RANGE_DAYS[range];
    const meta = document.getElementById("ledger-meta");
    const emptyEl = document.getElementById("ledger-empty");
    const table = document.getElementById("ledger-table");
    const tbody = table.querySelector("tbody");
    // reset — this now re-runs on every range change, not just once at boot
    tbody.replaceChildren();
    emptyEl.hidden = true;
    table.hidden = true;
    meta.textContent = "aggregates computed in SQL" +
      (days === "all" ? " · all time" : ` · last ${days} days`) +
      " · streaks written by health.py";
    let rows;
    try { rows = (await fetchJSON(`/api/dash/habits-ledger?days=${days}`)).rows || []; }
    catch (e) {
      emptyEl.textContent = "Ledger unavailable: " + e.message;
      emptyEl.hidden = false;
      return;
    }
    if (!rows.length) {
      emptyEl.textContent =
        "No habit logs yet — the coach loop's evening check-in writes them.";
      emptyEl.hidden = false;
      return;
    }
    rows.forEach((r) => {
      const tr = document.createElement("tr");
      [r.habit, `${r.current_streak}d`, `${r.best_streak}d`,
       `${r.days_done} / ${r.days_logged}`, r.first_date, r.last_date]
        .forEach((c, i) => {
          const td = document.createElement("td");
          td.textContent = c == null ? "–" : String(c);
          if (i === 0) td.className = "strong";
          tr.appendChild(td);
        });
      tbody.appendChild(tr);
    });
    table.hidden = false;
  }

  let subjectiveRows = null;   // cached /api/dash/subjective payload (all-time)
  async function heat() {
    if (!subjectiveRows) {
      // days=all so "All time" mode is genuinely unbounded, not capped at 365.
      try { subjectiveRows = (await fetchJSON("/api/dash/subjective?days=all")).rows || []; }
      catch (e) { window.HermesCharts.calEmpty("cons-heat", e.message); return; }
    }
    const rated = subjectiveRows.filter((r) => r.day_rating != null);
    if (!rated.length) return window.HermesCharts.calEmpty("cons-heat", "Day ratings appear here (green / yellow / red)");
    const colr = { 1: pal().bad, 2: pal().warn, 3: pal().good };
    // shared mode-aware calendar; mode follows the header dd (owner-review r3).
    window.HermesCharts.ratingCal("cons-heat", {
      mode: CAL_MODE[window.HermesUI.range("consistency")] || "year",
      entries: rated.map((r) => ({ date: r.date, value: r.day_rating })),
      colorFor: (v) => colr[v] || null,
      wordFor: (v) => RATE_WORD[v] || "?",
    });
  }

  function boot() { ring(); adherence(); ledger(); heat(); }
  window.addEventListener("themechange", () => { ring(); adherence(); heat(); });
  window.addEventListener("resize", () => Object.values(charts).forEach((c) => c && c.resize()));
  window.HermesUI.onRange("consistency", () => { adherence(); ledger(); heat(); });
  boot();
})();
