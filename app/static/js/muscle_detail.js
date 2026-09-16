/* §3b drill-down: renders health.py `muscle-detail` verbatim — exercises,
   exposure rollup, and the explicitly probabilistic relative-strength
   theory. Nothing is computed here except plain lookups
   over the engine's own arrays (determinism law: math lives in health.py).
   The left/right card draws from the existing /api/training/fitness-tests
   engine JSON (unchanged) — per-side barbell volume doesn't exist, but a
   movement in this group logged on both sides via a fitness test does. */
(function () {
  if (typeof echarts === "undefined" || !window.HermesCharts) return;
  const { fetchJSON, pal, tip, barsOpt } = window.HermesCharts;
  const charts = {};
  const state = { data: null, fitness: null, fitnessError: null };

  function chart(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    if (charts[id]) charts[id].dispose();
    charts[id] = echarts.init(el);
    return charts[id];
  }

  /* ---------- Relative strength theory (distinct from exposure) ---------- */
  function renderStrengthTheory() {
    const body = document.getElementById("md-theory-body");
    const status = document.getElementById("md-theory-status");
    const theory = state.data.strength_theories || {};
    if (theory.status !== "theory") {
      status.textContent = "Insufficient evidence";
      const reason = theory.reason || "The recurring-evidence threshold has not been met.";
      cardEmpty(body, `${reason}. OpenHealthAtlas does not rank sub-regions below the ` +
        `${theory.minimum_observations || 5}-date threshold.`);
      return;
    }

    const rows = theory.theories || [];
    status.textContent = theory.imbalance && theory.imbalance.status === "possible_imbalance"
      ? "Possible imbalance" : "Theory available";
    body.replaceChildren();

    const lead = document.createElement("p");
    lead.className = "note-txt m0 mb-1";
    lead.textContent = (theory.imbalance && theory.imbalance.theory) ||
      "Recurring evidence supports a relative-capacity theory.";
    body.appendChild(lead);

    rows.forEach((r) => {
      const row = document.createElement("div");
      row.className = "prod";
      const text = document.createElement("div");
      text.className = "grow";
      const name = document.createElement("div");
      name.className = "note-txt strong";
      name.textContent = cap(r.sub_region);
      const evidence = document.createElement("div");
      evidence.className = "muted micro";
      const signals = (r.signals || []).map((s) => ({
        exposure_proxy: "exposure support",
        normalized_performance: "personal performance",
        direct_fitness_tests: "direct tests",
      }[s] || s));
      evidence.textContent = `${r.observations} recurring dates · ${r.confidence} confidence` +
        (signals.length ? ` · ${signals.join(" + ")}` : "");
      text.append(name, evidence);
      const chipEl = document.createElement("span");
      chipEl.className = "chip";
      chipEl.textContent = `${r.relative_strength_score}/100 relative`;
      row.append(text, chipEl);
      body.appendChild(row);
    });

    const note = document.createElement("p");
    note.className = "muted micro m0 mt-1";
    note.textContent = "Exposure bars above show training dose. These scores are a separate, " +
      "uncertain theory using within-exercise progress and repeated direct tests; raw kilograms " +
      "from different exercises are never compared.";
    body.appendChild(note);
  }

  function cardEmpty(container, msg) {
    container.replaceChildren();
    const p = document.createElement("p");
    p.className = "muted note-txt m0";
    p.textContent = msg;
    container.appendChild(p);
  }

  function cap(s) {
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
  }

  /* ---------- Sub-region development ---------- */
  function renderSubregions() {
    const body = document.getElementById("md-sub-body");
    const sub = state.data.sub_regions || {};
    if (sub.status !== "ok") {
      cardEmpty(body, sub.note || "Sub-region map isn't seeded yet.");
      return;
    }
    const regions = sub.regions || [];
    if (!regions.length) {
      cardEmpty(body, "No sub-region rows logged for this group yet.");
      return;
    }
    body.replaceChildren();
    const chartEl = document.createElement("div");
    chartEl.id = "md-sub-chart";
    chartEl.className = "h230";
    body.appendChild(chartEl);
    const note = document.createElement("p");
    note.className = "muted note-txt m0 mt-1";
    const approx = regions.some((r) => r.approx);
    note.textContent = `bar length = effective sets over the last ${state.data.window_days}d — ` +
      "no balanced target is defined yet" + (approx ? " · ≈ = mechanism-based estimate" : "");
    body.appendChild(note);

    // engine sorts regions by -eff_sets (highest first); reverse so the
    // highest bar renders at the TOP of a horizontal chart (echarts places
    // category index 0 at the bottom).
    const ordered = [...regions].reverse();
    const cats = ordered.map((r) => cap(r.sub_region) + (r.approx ? " ≈" : "") +
      (r.laterality !== "bilateral" ? ` (${r.laterality})` : ""));
    const vals = ordered.map((r) => r.eff_sets);
    chart("md-sub-chart").setOption(barsOpt(cats, vals, pal().accent, true));
  }

  /* ---------- Exercises driving it ---------- */
  function renderExercises() {
    const body = document.getElementById("md-ex-body");
    const d = state.data;
    document.getElementById("md-window").textContent = `effective sets, ${d.window_days}d window`;
    const exs = d.exercises || [];
    if (!exs.length) {
      cardEmpty(body, "No logged sets touched this group in the window.");
      return;
    }
    // reverse-index: exercise -> [sub-region names] from the engine's own
    // sub_regions.regions[].exercises lists — only populated once sub.status
    // is "ok"; never guessed when it isn't.
    const subIdx = new Map();
    const sub = d.sub_regions || {};
    if (sub.status === "ok") {
      (sub.regions || []).forEach((r) => {
        (r.exercises || []).forEach((ex) => {
          const list = subIdx.get(ex) || [];
          list.push(cap(r.sub_region));
          subIdx.set(ex, list);
        });
      });
    }
    body.replaceChildren();
    exs.forEach((e) => {
      const row = document.createElement("div");
      row.className = "prod";
      const text = document.createElement("div");
      text.className = "grow";
      const name = document.createElement("div");
      name.className = "note-txt strong";
      name.textContent = e.exercise + (e.basis === "coarse" ? " †" : "");
      const subLine = document.createElement("div");
      subLine.className = "muted micro";
      const subs = subIdx.get(e.exercise);
      subLine.textContent = subs && subs.length ? subs.join(" · ") : "sub-region not yet mapped";
      text.append(name, subLine);
      const chipEl = document.createElement("span");
      chipEl.className = "chip";
      chipEl.textContent = `${e.sets} sets/${d.window_days}d`;
      row.append(text, chipEl);
      body.appendChild(row);
    });
    if ((d.pending_source || []).length) {
      const p = document.createElement("p");
      p.className = "muted micro m0 mt-1";
      p.textContent = "† coarse Hevy tag only — pending an authored cited source: " +
        d.pending_source.join(", ");
      body.appendChild(p);
    }
  }

  /* ---------- Left / right — per exercise ---------- */
  function renderLR() {
    const body = document.getElementById("md-lr-body");
    const note = document.getElementById("md-lr-note");
    if (state.fitnessError) {
      note.textContent = "";
      cardEmpty(body, "Fitness-test data unavailable: " + state.fitnessError);
      return;
    }
    const group = state.data.group;
    const tests = (state.fitness && state.fitness.tests) || [];
    const bySide = new Map();   // movement name -> {left, right}
    tests.forEach((t) => {
      if (t.group !== group || (t.side !== "left" && t.side !== "right")) return;
      const cur = bySide.get(t.name) || {};
      cur[t.side] = t.value;
      bySide.set(t.name, cur);
    });
    const pairs = [...bySide.entries()].filter(([, v]) => v.left != null && v.right != null);
    if (!pairs.length) {
      note.textContent = "";
      cardEmpty(body, "Left/right split isn't derivable from barbell sets — Hevy doesn't tag a " +
        "side. It will populate once both sides of a movement in this group are logged via " +
        "single-side fitness tests.");
      return;
    }
    note.textContent = "from single-side fitness tests · each bar in its own test's native unit";
    body.replaceChildren();
    const chartEl = document.createElement("div");
    chartEl.id = "md-lr-chart";
    chartEl.className = "h230";
    body.appendChild(chartEl);
    const P = pal();
    const ordered = [...pairs].reverse();
    const cats = ordered.map(([name]) => name);
    const left = ordered.map(([, v]) => v.left);
    const right = ordered.map(([, v]) => v.right);
    chart("md-lr-chart").setOption({
      grid: { left: 10, right: 16, top: 30, bottom: 18, containLabel: true },
      tooltip: Object.assign(tip(P), { trigger: "axis", axisPointer: { type: "shadow" } }),
      legend: { top: 0, data: ["Left", "Right"], textStyle: { color: P.muted, fontSize: 10 } },
      xAxis: { type: "value", splitLine: { lineStyle: { color: P.grid } },
        axisLabel: { color: P.muted, fontSize: 10 } },
      yAxis: { type: "category", data: cats,
        axisLine: { lineStyle: { color: P.grid } },
        axisLabel: { color: P.muted, fontSize: 10 }, axisTick: { show: false } },
      series: [
        { type: "bar", name: "Left", data: left, barGap: "20%",
          itemStyle: { color: P.ink, borderRadius: 4 } },
        { type: "bar", name: "Right", data: right,
          itemStyle: { color: P.good, borderRadius: 4 } },
      ],
    });
  }

  /* ---------- On the body (§3f, scoped) ---------- */
  // Same engine payload as the Body page's figure; only this group's regions
  // stay colored (scopeGroup dims the rest). Hover titles only — the full
  // lens cockpit lives on the Body page.
  let mfFront = null, mfBack = null, mfFetch = null;
  function fetchMuscleMap() {
    // memoize the PROMISE, not the result: a themechange re-render during
    // the initial load must not issue a duplicate fetch (review). boot()
    // also calls this early so the figure isn't serialized behind the
    // page's other engine round-trips.
    mfFetch = mfFetch || fetchJSON("/api/training/muscle-map?lens=activation");
    return mfFetch;
  }
  async function renderFigure() {
    const wrapF = document.getElementById("mf-front");
    const wrapB = document.getElementById("mf-back");
    if (!wrapF || !wrapB) return;
    if (!window.MuscleFigure) {
      cardEmpty(wrapF, "Body-map component failed to load (vendored asset missing?).");
      cardEmpty(wrapB, "Body-map component failed to load (vendored asset missing?).");
      return;
    }
    if (mfFront) return;  // painted once; CSS-var colors re-theme themselves
    let map;
    try {
      map = (await fetchMuscleMap()).result;
    } catch (e) {
      mfFetch = null;     // allow a retry on the next render
      cardEmpty(wrapF, e.message); cardEmpty(wrapB, e.message);
      return;
    }
    if (mfFront) return;  // a concurrent call won the await race
    const group = state.data.group;
    mfFront = window.MuscleFigure.create(wrapF, { view: "front", interactive: false });
    mfBack = window.MuscleFigure.create(wrapB, { view: "back", interactive: false });
    mfFront.update(map, { scopeGroup: group });
    mfBack.update(map, { scopeGroup: group });
    window.MuscleFigure.legend(document.getElementById("mf-legend-f"), map);
    window.MuscleFigure.legend(document.getElementById("mf-legend-b"), map);
    ["mf-scope-f", "mf-scope-b"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.textContent = group + " highlighted · rest dimmed";
    });
  }

  function render() {
    if (!state.data) return;
    renderSubregions();
    renderExercises();
    renderStrengthTheory();
    renderLR();
    renderFigure();
  }

  async function boot() {
    // decode inside the try: a malformed %-escape throws URIError and must
    // surface as an error message, not an eternal "Loading…"
    let group;
    try {
      group = decodeURIComponent(location.pathname.split("/").pop() || "");
    } catch (e) {
      ["md-sub-body", "md-ex-body", "md-theory-body", "md-lr-body"].forEach((id) =>
        cardEmpty(document.getElementById(id), e.message));
      return;
    }
    // start the figure fetch now, in parallel with the fetches below —
    // renderFigure() awaits the same memoized promise. The stray .catch
    // only silences the unhandled-rejection warning when boot aborts first;
    // renderFigure's own await still sees the error.
    fetchMuscleMap().catch(() => {});
    try {
      state.data = (await fetchJSON("/api/training/muscle-detail?group=" + encodeURIComponent(group))).result;
    } catch (e) {
      ["md-sub-body", "md-ex-body", "md-theory-body", "md-lr-body", "mf-front", "mf-back"].forEach((id) =>
        cardEmpty(document.getElementById(id), e.message));
      return;
    }
    document.getElementById("md-title").textContent = state.data.group + " — sub-regions";
    try {
      state.fitness = (await fetchJSON("/api/training/fitness-tests")).result;
    } catch (e) {
      state.fitnessError = e.message;
    }
    render();
  }

  window.addEventListener("themechange", render);
  window.addEventListener("resize", () => Object.values(charts).forEach((c) => c && c.resize()));

  boot();
})();
