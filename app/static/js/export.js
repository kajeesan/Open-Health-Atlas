/* Export: render the table multi-select from the server whitelist, track the
   chosen format + date range, and trigger the selective bundle / full-DB
   download. No table names are hardcoded — the choices come from
   /api/export/tables, which is the server's export whitelist. */
(function () {
  const $ = (id) => document.getElementById(id);
  let fmt = "csv";

  function selected() {
    return [...document.querySelectorAll('#ex-tables input[type="checkbox"]:checked')]
      .map((c) => c.value);
  }

  function refresh() {
    const n = selected().length;
    const btn = $("ex-generate");
    btn.disabled = n === 0;
    const status = $("ex-status");
    if (n === 0) status.textContent = "Select at least one table.";
    else if (n === 1) status.textContent = fmt === "csv"
      ? "1 table → one CSV file." : "1 table → one Excel sheet.";
    else status.textContent = fmt === "csv"
      ? `${n} tables → a ZIP of ${n} CSV files.`
      : `${n} tables → one Excel workbook, ${n} sheets.`;
  }

  (async function loadTables() {
    try {
      const r = await fetch("/api/export/tables", { credentials: "same-origin" });
      if (r.status === 401) { location.assign("/login"); return; }
      const d = await r.json();
      const dated = new Set(d.dated || []);
      const box = $("ex-tables");
      box.replaceChildren(...(d.tables || []).map((t) => {
        const row = document.createElement("label");
        row.className = "tbl-opt";
        const cb = document.createElement("input");
        cb.type = "checkbox"; cb.value = t;
        cb.addEventListener("change", refresh);
        const name = document.createElement("span");
        name.className = "tbl-name"; name.textContent = t;
        row.append(cb, name);
        if (!dated.has(t)) {
          const tag = document.createElement("span");
          tag.className = "tbl-tag"; tag.textContent = "full table";
          tag.title = "no date column — the range doesn't apply; exported whole";
          row.append(tag);
        }
        return row;
      }));
      refresh();
    } catch (_) {
      $("ex-tables").textContent = "Could not load tables.";
    }
  })();

  // Format segmented control.
  $("ex-fmt").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-fmt]");
    if (!b) return;
    fmt = b.dataset.fmt;
    $("ex-fmt").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    refresh();
  });

  $("ex-all").addEventListener("click", () => {
    document.querySelectorAll('#ex-tables input[type="checkbox"]').forEach((c) => { c.checked = true; });
    refresh();
  });
  $("ex-none").addEventListener("click", () => {
    document.querySelectorAll('#ex-tables input[type="checkbox"]').forEach((c) => { c.checked = false; });
    refresh();
  });

  $("ex-generate").addEventListener("click", () => {
    const names = selected();
    if (!names.length) return;
    const p = new URLSearchParams();
    p.set("tables", names.join(","));
    p.set("format", fmt);
    const from = $("ex-from").value, to = $("ex-to").value;
    if (from) p.set("from", from);
    if (to) p.set("to", to);
    // GET download via a hidden anchor (same-origin session cookie carries auth)
    const a = document.createElement("a");
    a.href = "/api/export/bundle?" + p.toString();
    a.download = "";
    document.body.appendChild(a); a.click(); a.remove();
  });
})();
