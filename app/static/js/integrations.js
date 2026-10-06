/* Integrations & privacy (task-36). Renders /api/integrations/sources
   verbatim: every status/date/chip is computed server-side (see that
   route's docstring for the exact "connected/stale/none" rules), so this
   file only builds row text/markup — no status math here. */
(function () {
  if (!window.HermesCharts) return;
  const { fetchJSON } = window.HermesCharts;

  const FLAG_CLASS = { connected: "in", stale: "bd", none: "bd", not_running: "out", unknown: "bd" };

  function row(name, chipText, flagClass, sub) {
    const el = document.createElement("div");
    el.className = "src-row";
    const left = document.createElement("div");
    const nameEl = document.createElement("div");
    nameEl.className = "strong";
    nameEl.textContent = name;
    left.appendChild(nameEl);
    if (sub) {
      const subEl = document.createElement("div");
      subEl.className = "muted micro mt-1";
      subEl.textContent = sub;
      left.appendChild(subEl);
    }
    const chip = document.createElement("span");
    chip.className = "flag " + flagClass;
    chip.textContent = chipText;
    el.append(left, chip);
    return el;
  }

  function syncChip(status, date) {
    if (status === "connected") return `Recent data · latest record ${date}`;
    if (status === "stale") return `Stale data · latest record ${date}`;
    return "No data yet";
  }

  function telegramRow(t) {
    t = t || {};
    const chipText = { connected: "Running", not_running: "Not running" }[t.status] || "Status unknown";
    let sub;
    if (t.status === "connected") sub = [t.model, t.provider].filter(Boolean).join(" · ") || null;
    else sub = t.error || null;
    return row("Hermes gateway", chipText, FLAG_CLASS[t.status] || "bd", sub);
  }

  function hevyRow(h) {
    h = h || {};
    return row("Hevy (training)", syncChip(h.status, h.latest_date), FLAG_CLASS[h.status] || "bd");
  }

  function wearableRows(w) {
    w = w || {};
    const apple = w.apple || {};
    const fitbit = w.fitbit || {};
    return [
      row("Apple Health", syncChip(apple.status, apple.latest_date), FLAG_CLASS[apple.status] || "bd"),
      row("Fitbit / Google Health", syncChip(fitbit.status, fitbit.latest_date),
        FLAG_CLASS[fitbit.status] || "bd", `Token status: ${fitbit.auth_status || "unknown"}`),
    ];
  }

  function labsRow(l) {
    l = l || {};
    const sub = l.latest_date ? `latest result: ${l.latest_date}` : "no results logged yet";
    return row("Bloodwork lab", "Manual upload", "bd", sub);
  }

  async function boot() {
    const list = document.getElementById("src-list");
    let data;
    try {
      data = await fetchJSON("/api/integrations/sources");
    } catch (e) {
      const p = document.createElement("p");
      p.className = "muted note-txt";
      p.textContent = "Sources unavailable: " + e.message;
      list.appendChild(p);
      return;
    }
    list.append(
      telegramRow(data.telegram),
      hevyRow(data.hevy),
      ...wearableRows(data.wearable),
      labsRow(data.labs),
    );
  }
  boot();
})();
