/* task-31 brain-dump drill sub-page. Lists every subjective_daily.brain_dump
   entry newest-first from /api/dash/subjective?days=all (no new endpoint), plus
   a deterministic current-streak chip (charts-common streakToToday). Entries
   are rendered VERBATIM — this is a mental-health surface, never summarized or
   scored. No charts here, so no echarts dependency (guards on HermesCharts). */
(function () {
  if (!window.HermesCharts) return;
  const { fetchJSON, streakToToday } = window.HermesCharts;

  /* "Today" for today's entry, else "Jul 14" — the design's date label. Parse
     the ISO date as LOCAL (split, not Date(str) which is UTC) so the label
     never slips a day near midnight. */
  function dateLabel(iso, today) {
    if (iso === today) return "Today";
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString(undefined,
      { month: "short", day: "numeric" });
  }

  function entryRow(iso, text, today) {
    const row = document.createElement("div");
    row.className = "note-card row-top between";
    const body = document.createElement("div");
    body.className = "grow";
    body.textContent = text;              // verbatim, autoescaped by textContent
    const when = document.createElement("span");
    when.className = "muted micro";
    when.textContent = dateLabel(iso, today) + " ›";
    row.append(body, when);
    return row;
  }

  function render(rows) {
    const list = document.getElementById("bd-list");
    list.replaceChildren();
    const entries = rows
      .filter((r) => r.brain_dump && r.brain_dump.trim())
      .sort((a, b) => (a.date < b.date ? 1 : -1));

    const streak = streakToToday(entries.map((e) => e.date));
    const chip = document.getElementById("bd-streak");
    if (streak) {
      chip.hidden = false;
      chip.textContent = streak + "-day streak";
    }

    if (!entries.length) {
      const p = document.createElement("p");
      p.className = "muted note-txt m0";
      p.textContent = "No brain-dumps logged yet — the evening check-in captures the "
        + "30-sec voice/text dump, and every one lands here.";
      list.appendChild(p);
      return;
    }
    const today = window.HermesCharts.isoDaysAgo(0);
    entries.forEach((e) => list.appendChild(entryRow(e.date, e.brain_dump.trim(), today)));
  }

  async function boot() {
    try {
      const data = await fetchJSON("/api/dash/subjective?days=all");
      render(data.rows || []);
    } catch (e) {
      const list = document.getElementById("bd-list");
      list.replaceChildren();
      const p = document.createElement("p");
      p.className = "muted note-txt m0";
      p.textContent = "Couldn't load entries: " + e.message;
      list.appendChild(p);
    }
  }
  boot();
})();
