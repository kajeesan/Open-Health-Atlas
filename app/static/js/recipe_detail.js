/* Recipe detail sub-page (task 33; T47 adds real "% of daily target").
   Loads /api/nutrition/recipe/<rid> and renders real data only:
   - stats (calories / portion / in-freezer) + MACROS/PORTION bars from the
     per-portion nutrient math health.py owns;
   - a Micronutrients card listing whatever non-macro nutrients genuinely
     exist as AMOUNTS (primary) plus, since T47, each nutrient's real
     "% of daily target" from the T44 targets engine (/api/nutrition/targets
     — the same engine the Nutrition page uses; this page has no nutrition.js
     so one small local fetch is fine). The % is band-colored with T45's
     floor semantics (micros are floors: good ≥100%, warn ≥87.5%, bad below —
     never "too much"). Honest "—" whenever the engine is insufficient
     (no profile) or the nutrient has no matching target/unit — never a
     fabricated %.
   The rid comes from the URL path so no inline script is needed (CSP). Bars
   are pure CSSOM (element.style.width/background) — the established no-inline
   pattern (see training.js goalRow). */
(function () {
  const rid = decodeURIComponent(location.pathname.split("/").pop());
  const esc = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; };

  function bar(label, amount, unit, maxAmount, color, pctText) {
    const row = document.createElement("div"); row.className = "goal-row";
    const l = document.createElement("span"); l.className = "goal-label"; l.textContent = label;
    const bg = document.createElement("div"); bg.className = "barbg";
    const fill = document.createElement("div"); fill.className = "barfill";
    // width is proportional to the largest value in the group — a purely visual
    // relative scale (amounts across different units aren't comparable as %).
    fill.style.width = (maxAmount > 0 ? Math.round((amount / maxAmount) * 100) : 0) + "%";
    fill.style.background = color;
    bg.appendChild(fill);
    const v = document.createElement("span"); v.className = "goal-val";
    v.textContent = amount != null
      ? `${amount} ${unit}` + (pctText ? ` · ${pctText}` : "")
      : "—";
    row.append(l, bg, v);
    return row;
  }

  /* ------- T47: % of daily target (micros card) ------- */

  // T45's floor-band rule for micros/protein (good inside [100, ∞); warn
  // within 12.5 points below; else bad) — kept as a tiny local copy since
  // this page doesn't load nutrition.js.
  function floorBand(pct) { return pct >= 100 ? "good" : pct >= 87.5 ? "warn" : "bad"; }

  async function fetchTargets() {
    try {
      const r = await fetch("/api/nutrition/targets", { credentials: "same-origin" });
      if (r.status === 401) { location.assign("/login"); throw new Error("session expired"); }
      const d = await r.json();
      return (d && d.result) || { status: "error" };
    } catch (_) { return { status: "error" }; }
  }

  // recipe_nutrients stores Cronometer display names ("Vitamin D"); the
  // engine's targets.micros speak MICRO_SEED keys ("vitamin_d"). Normalize
  // the name, then convert units where a safe factor exists (mirrors
  // health.py MICRO_SEED's unit tables) — anything unmatched honestly gets
  // no %, never a wrong-unit fabrication.
  const KEY_ALIAS = { b12: "vitamin_b12", folate_dfe: "folate" };
  const keyFor = (name) => {
    const k = (name || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
    return KEY_ALIAS[k] || k;
  };
  function unitFactor(key, unit, targetUnit) {
    const norm = (u) => { u = (u || "").trim().toLowerCase(); return (u === "µg" || u === "mcg") ? "ug" : u; };
    const u = norm(unit), t = norm(targetUnit);
    if (u === t) return 1;
    if (u === "g" && t === "mg") return 1000;
    if (key === "vitamin_d" && u === "iu" && t === "ug") return 0.025; // 40 IU = 1 µg
    return null;
  }

  function renderIngredients(d) {
    const el = document.getElementById("rdIngredients");
    const items = d.ingredients || [];
    if (d.ingredients_status === "invalid") {
      el.innerHTML = '<p class="muted note-txt m0">The saved ingredient list is invalid. ' +
        "Ask OpenHealthAtlas to capture it again.</p>";
      return;
    }
    if (!items.length) {
      el.innerHTML = '<p class="muted note-txt m0">No ingredients captured yet. ' +
        "Send the ingredient names and batch amounts to OpenHealthAtlas.</p>";
      return;
    }
    const list = document.createElement("div");
    list.className = "vstack";
    items.forEach((it) => {
      const row = document.createElement("div");
      row.className = "row between wrap";
      const name = document.createElement("span");
      name.className = "strong";
      name.textContent = it.name;
      const details = [];
      if (it.amount != null) details.push(`${it.amount} ${it.unit || ""}`.trim());
      if (it.weight_g != null
          && !(it.unit === "g" && Number(it.amount) === Number(it.weight_g))) {
        details.push(`${it.weight_g} g total`);
      }
      if (it.note) details.push(it.note);
      const amount = document.createElement("span");
      amount.className = "muted note-txt";
      amount.textContent = details.join(" · ") || "amount not specified";
      row.append(name, amount);
      list.appendChild(row);
    });
    el.replaceChildren(list);
  }

  async function load() {
    let d;
    try {
      const r = await fetch("/api/nutrition/recipe/" + encodeURIComponent(rid), { credentials: "same-origin" });
      if (r.status === 401) { location.assign("/login"); return; }
      if (r.status === 404) { fail("This recipe no longer exists."); return; }
      d = await r.json();
      if (!r.ok) throw new Error(d.error || "HTTP " + r.status);
    } catch (e) { fail(esc(e.message)); return; }

    const rec = d.recipe || {};
    document.getElementById("rdTitle").textContent = rec.name || rid;
    if (rec.portions != null) {
      const chip = document.getElementById("rdMakes");
      chip.textContent = `makes ${rec.portions}`;
      chip.hidden = false;
    }
    document.getElementById("rdKcal").textContent =
      d.kcal_per_portion != null ? Math.round(d.kcal_per_portion) + " kcal" : "—";
    document.getElementById("rdPortion").textContent =
      rec.grams_per_portion != null ? Math.round(rec.grams_per_portion) + " g" : "—";
    document.getElementById("rdFreezer").textContent = Math.round(d.portions_remaining || 0);
    renderIngredients(d);

    // macros
    const macroEl = document.getElementById("rdMacros");
    const macros = d.macros || [];
    if (!macros.length || macros.every((m) => m.amount == null)) {
      macroEl.innerHTML = '<p class="muted note-txt m0">Macros pending — this recipe has no batch ' +
        "weight set yet, so per-portion amounts can't be computed. Set it via the coach's " +
        "<code>set-batch</code>.</p>";
    } else {
      // var() strings, not resolved colors — the browser re-themes them automatically
      const colors = { Protein: "var(--ink)", Carbs: "var(--good)", Fat: "var(--warn)" };
      const max = Math.max(...macros.map((m) => m.amount || 0));
      macroEl.replaceChildren(...macros.map((m) => bar(m.name, m.amount, m.unit || "g", max, colors[m.name] || "var(--accent)")));
    }

    // micronutrients — real amounts (primary) + real % of daily target (T47)
    const microEl = document.getElementById("rdMicros");
    const micros = (d.micronutrients || []).filter((m) => m.amount != null);
    if (!micros.length) {
      microEl.innerHTML = '<p class="muted note-txt m0">No micronutrient data captured for this ' +
        "recipe yet — the importer stored macros only. When micros are captured they'll show here " +
        "as amounts per portion with their % of your daily target.</p>";
      return;
    }
    const t = await fetchTargets();
    const microTargets = {};
    if (t.status === "ok") {
      (t.targets.micros || []).forEach((m) => { if (m.target) microTargets[m.nutrient] = m; });
    }
    const max = Math.max(...micros.map((m) => m.amount));
    const list = document.createElement("div"); list.className = "goals-list";
    list.append(...micros.map((m) => {
      const mt = microTargets[keyFor(m.name)];
      const f = mt ? unitFactor(keyFor(m.name), m.unit, mt.unit) : null;
      if (mt == null || f == null) {
        // engine insufficient, no target for this nutrient, or no safe unit
        // conversion — honest "—", neutral fill, never a fabricated %.
        return bar(m.name, m.amount, m.unit || "", max, "var(--accent)", "—");
      }
      const pct = Math.round((m.amount * f) / mt.target * 100);
      return bar(m.name, m.amount, m.unit || "", max, `var(--${floorBand(pct)})`, `${pct}%`);
    }));
    const note = document.createElement("p");
    note.className = "muted note-txt mt-2 m0";
    note.textContent = t.status === "ok"
      ? "Amounts per portion · % = share of your daily target (targets engine); — = no target for that nutrient."
      : "Amounts per portion. % of daily target needs your profile (set via the coach).";
    microEl.replaceChildren(list, note);
  }

  function fail(msg) {
    document.getElementById("rdMacros").innerHTML = `<p class="dim">${msg}</p>`;
    document.getElementById("rdMicros").innerHTML = `<p class="dim">${msg}</p>`;
    document.getElementById("rdIngredients").innerHTML = `<p class="dim">${msg}</p>`;
  }

  load();
})();
