/* Models & spend: status, safe model switch (auto-rollback server-side),
   budget cap with projected-spend badge, Fitbit token state. */
(function () {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const toastWrap = document.getElementById("toast-wrap");
  const $ = (id) => document.getElementById(id);

  function toast(msg, kind) {
    const el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = msg;
    toastWrap.appendChild(el);
    requestAnimationFrame(() => el.classList.add("show"));
    setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 250); }, 4200);
  }
  async function req(url, opts) {
    const r = await fetch(url, { credentials: "same-origin", ...opts });
    if (r.status === 401) { location.assign("/login"); throw new Error("session expired"); }
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) throw new Error(d.error || "HTTP " + r.status);
    return d;
  }
  const post = (url, body) => req(url, { method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify(body) });
  const usd = (v) => v == null ? "—" : "$" + Number(v).toFixed(2);

  async function loadStatus() {
    try {
      const d = await req("/api/models/status");
      const a = d.agent || {};
      $("mo-model").textContent = a.model || a.error || "—";
      $("mo-provider").textContent = a.provider || "—";
      $("mo-gateway").textContent = a.gateway_active ? "active ✓" : (a.error ? "?" : "NOT RUNNING");
      $("mo-gateway").style.color = a.gateway_active ? "var(--good)" : "var(--bad)";

      const s = d.spend || {};
      if (s.error) { $("sp-badge").textContent = "unavailable"; $("sp-usage").textContent = s.error; return; }
      $("sp-usage").textContent = usd(s.usage_usd);
      $("sp-recent").textContent = `${usd(s.usage_daily_usd)} / ${usd(s.usage_weekly_usd)}`;
      $("sp-remaining").textContent = usd(s.remaining_usd) + (s.limit_usd != null ? ` of ${usd(s.limit_usd)}` : "");
      const budget = parseFloat(d.budget_monthly_usd);
      if (budget) $("sp-budget").value = budget;
      // projected month: monthly if the API gives it, else weekly * 4.35
      const projected = s.usage_monthly_usd != null ? s.usage_monthly_usd
        : (s.usage_weekly_usd != null ? s.usage_weekly_usd * 4.35 : null);
      const badge = $("sp-badge");
      const bar = $("sp-bar");
      if (budget && projected != null) {
        const over = projected > budget;
        badge.textContent = `projected ${usd(projected)} / ${usd(budget)}`;
        badge.style.color = over ? "var(--bad)" : "var(--good)";
        badge.style.background = over ? "rgba(239,68,68,0.12)" : "";
        // Budget-cap bar (design v-models headline): projected vs your cap.
        bar.style.width = Math.max(0, Math.min(100, (projected / budget) * 100)) + "%";
        bar.style.background = over ? "var(--bad)" : "var(--good)";
      } else {
        badge.textContent = projected != null ? `projected ${usd(projected)}/mo` : "set a budget";
        // No budget set: show key-limit consumption if the API exposes it.
        if (s.limit_usd && s.remaining_usd != null) {
          bar.style.width = Math.max(0, Math.min(100, ((s.limit_usd - s.remaining_usd) / s.limit_usd) * 100)) + "%";
          bar.style.background = "var(--accent)";
        } else {
          bar.style.width = "0%";
        }
      }
    } catch (e) { $("mo-model").textContent = e.message; }
  }

  $("mo-set").addEventListener("click", async () => {
    const model = $("mo-new").value.trim();
    if (!model) { toast("Enter a model id", "bad"); return; }
    const cur = $("mo-model").textContent;
    if (!confirm(`Switch the coach from ${cur} to ${model}?\n\nThe Telegram gateway restarts; auto-rollback if unhealthy.`)) return;
    const btn = $("mo-set");
    btn.disabled = true; btn.textContent = "Switching… (up to 1 min)";
    try {
      const d = await post("/api/models/set", { model });
      const r = d.result || {};
      toast(r.changed ? `Model switched to ${r.model} ✓ (gateway healthy)` : (r.note || "No change"), "good");
      loadStatus();
    } catch (e) {
      // hermesctl reports rollback in the error payload
      toast(/rolled back/.test(e.message) ? e.message + " — coaching stayed alive" : "Switch failed: " + e.message, "bad");
      loadStatus();
    } finally { btn.disabled = false; btn.textContent = "Switch model"; }
  });

  $("sp-save").addEventListener("click", async () => {
    try {
      const d = await post("/api/models/budget", { monthly_usd: $("sp-budget").value });
      toast(`Budget set: ${usd(d.budget_monthly_usd)}/month ✓`, "good");
      loadStatus();
    } catch (e) { toast("Couldn't save: " + e.message, "bad"); }
  });

  (async function loadFitbit() {
    try {
      const d = await req("/api/models/fitbit");
      const r = d.result || {};
      $("fb-status").textContent = "Status: " + (r.auth_status || "unknown");
      $("fb-steps").replaceChildren(...(r.reauth_steps || []).map(s => {
        const li = document.createElement("li"); li.textContent = s; return li;
      }));
    } catch (e) { $("fb-status").textContent = e.message; }
  })();

  loadStatus();
})();
