/* Recovery drill: every displayed readiness fact comes from one governed
   /api/recovery/readiness result. Hermes can interpret that same result, but
   the dashboard only renders its deterministic score, components, muscle
   recovery, soreness, scope, and evidence fingerprint. */
(function () {
  if (!window.HermesCharts) return;
  const { fetchJSON, pal, gaugeOpt, barsOpt } = window.HermesCharts;
  const charts = {};
  let readinessData = null;
  let readinessEvidence = null;

  function chart(id) {
    if (typeof echarts === "undefined") return null;
    const el = document.getElementById(id);
    if (!el) return null;
    if (charts[id]) charts[id].dispose();
    charts[id] = echarts.init(el);
    return charts[id];
  }
  function emptyChart(id, label) {
    const ch = chart(id);
    if (!ch) return;
    ch.setOption({ graphic: { type: "text", left: "center", top: "middle",
      style: { text: label, fill: pal().muted, fontSize: 13 } } });
  }

  /* ------- header: score chip + subtitle ------- */
  function renderHeader(r) {
    const sub = document.getElementById("ry-sub");
    const chip = document.getElementById("ry-chip");
    if (!r || r.status !== "ok") {
      sub.textContent = r
        ? `${r.status} · ${r.reason || "readiness unavailable right now"}`
        : "readiness unavailable right now";
      chip.hidden = true;
      return;
    }
    sub.textContent = "equal-weight mean of your available signals";
    chip.hidden = false;
    chip.className = "chip";
    chip.textContent = `${r.score} · ${r.band}`;
  }

  function renderEvidence(evidence) {
    const el = document.getElementById("ry-evidence");
    if (!evidence || evidence.data_class !== "fictional") {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    const identity = (evidence.public_evidence_identity || "")
      .replace("sha256:", "").slice(0, 12);
    const policy = evidence.policy || {};
    const integrity = evidence.snapshot_integrity || {};
    el.textContent = `Fictional acceptance fixture · ${evidence.fixture_id} · `
      + `${evidence.range_from} to ${evidence.anchor_date} · public evidence ${identity} · `
      + `policy ${policy.policy_id || "unavailable"} v${policy.version || "?"} `
      + `${(policy.policy_sha256 || "").replace("sha256:", "").slice(0, 12)} · `
      + `snapshot ${integrity.status || "unverified"} (${integrity.scope || "scope unavailable"})`;
    el.hidden = false;
  }

  /* ------- gauge card ------- */
  function renderGauge(r) {
    if (!r || r.status !== "ok") {
      emptyChart("ry-gauge", (r && r.reason) || "not enough data yet");
      return;
    }
    const P = pal();
    const gOpt = gaugeOpt({
      value: r.score, min: 0, max: 100, width: 14,
      color: P[r.band] || P.accent, detailSize: 30,
      fmt: () => `Readiness: ${r.score} / 100 (${r.band})`,
    });
    gOpt.animation = false;
    const ch = chart("ry-gauge");
    if (ch) ch.setOption(gOpt);
  }

  /* ------- "what's dragging it down" bars + per-component basis lines ------- */
  function renderDrag(r) {
    const basisEl = document.getElementById("ry-drag-basis");
    basisEl.replaceChildren();
    if (!r || r.status !== "ok") {
      emptyChart("ry-drag", (r && r.reason) || "not enough data yet");
      return;
    }
    if (!r.drag.length) {
      emptyChart("ry-drag", "Nothing dragging it down");
      return;
    }
    const P = pal();
    const cats = r.drag.map((d) => d.label);
    const vals = r.drag.map((d) => d.points);
    const ch = chart("ry-drag");
    if (ch) ch.setOption(Object.assign(barsOpt(cats, vals, P.bad, true), { animation: false }));

    const byKey = {};
    (r.components || []).forEach((c) => { byKey[c.key] = c; });
    r.drag.forEach((d) => {
      const comp = byKey[d.key];
      const row = document.createElement("div");
      row.className = "prod";
      const text = document.createElement("div");
      text.className = "grow";
      const name = document.createElement("div");
      name.className = "note-txt strong";
      name.textContent = d.key;
      const sub = document.createElement("div");
      sub.className = "muted micro";
      sub.textContent = (comp && comp.basis) || "";
      text.append(name, sub);
      row.appendChild(text);
      basisEl.appendChild(row);
    });
  }

  /* ------- Muscle recovery table (.ltab, no new CSS) ------- */
  function fmtLast(m) {
    if (m.days_since == null) return m.note || "never logged";
    if (m.days_since === 0) return "today";
    if (m.days_since === 1) return "yesterday";
    return `${m.days_since}d ago`;
  }
  function fmtE1rm(m) {
    if (m.e1rm_delta_pct == null) return "—";
    const sign = m.e1rm_delta_pct > 0 ? "+" : "";
    return `${m.exercise}: ${sign}${m.e1rm_delta_pct}%`;
  }
  function renderMuscle(r) {
    const tbody = document.querySelector("#ry-muscle-table tbody");
    tbody.replaceChildren();
    const rows = (r && r.muscle_recovery) || [];
    rows.forEach((m) => {
      const tr = document.createElement("tr");
      const g = document.createElement("td");
      g.textContent = m.group;
      if (m.sore) {
        g.appendChild(document.createTextNode(" "));
        const flag = document.createElement("span");
        flag.className = "flag bd";
        flag.textContent = "sore";
        g.appendChild(flag);
      }
      const last = document.createElement("td");
      last.textContent = fmtLast(m);
      const sets = document.createElement("td");
      sets.textContent = String(m.sets_7d);
      const delta = document.createElement("td");
      delta.textContent = fmtE1rm(m);
      tr.append(g, last, sets, delta);
      tbody.appendChild(tr);
    });
  }

  /* ------- Soreness summary card (raw qualitative note stays private) ------- */
  function renderSoreness(r) {
    const el = document.getElementById("ry-soreness");
    el.replaceChildren();
    const s = r && r.soreness;
    if (!s) {
      const p = document.createElement("p");
      p.className = "muted note-txt m0";
      p.textContent = "No soreness summary is available for this fictional snapshot.";
      el.appendChild(p);
      return;
    }
    const d = document.createElement("div");
    d.className = "muted micro";
    d.textContent = s.date || "date unavailable";
    const note = document.createElement("p");
    note.className = "note-txt m0 mt-1";
    note.textContent = "Soreness is recorded for this fictional snapshot. "
      + "The raw note stays outside the browser projection.";
    el.append(d, note);
  }

  function renderReadiness() {
    renderHeader(readinessData);
    renderEvidence(readinessEvidence);
    renderGauge(readinessData);
    renderDrag(readinessData);
    renderMuscle(readinessData);
    renderSoreness(readinessData);
  }

  const COMPONENT_LABELS = { sleep: "Sleep", hrv: "HRV", rhr: "Resting HR" };

  function renderBreakdown(r) {
    const body = document.getElementById("ry-breakdown");
    body.replaceChildren();
    const rows = (r && r.components) || [];
    const evidence = (r && r.evidence) || {};
    const warnings = {};
    (evidence.warnings || []).forEach((warning) => {
      warnings[warning.component] = warning;
    });
    const excluded = (evidence.components || []).filter(
      (component) => component.status === "excluded",
    );
    if (!rows.length && !excluded.length) {
      const p = document.createElement("p");
      p.className = "muted note-txt m0";
      p.textContent = (r && r.reason) || "No scored recovery components available.";
      body.appendChild(p);
      return;
    }
    rows.forEach((component) => {
      const row = document.createElement("div");
      row.className = "prod";
      const text = document.createElement("div");
      text.className = "grow";
      const name = document.createElement("div");
      name.className = "note-txt strong";
      name.textContent = COMPONENT_LABELS[component.key] || component.key;
      const sub = document.createElement("div");
      sub.className = "muted micro";
      sub.textContent = component.basis || "";
      text.append(name, sub);
      row.appendChild(text);
      const val = document.createElement("span");
      val.className = "note-txt strong";
      val.textContent = `${component.score}/100`;
      row.appendChild(val);
      body.appendChild(row);
    });
    excluded.forEach((component) => {
      const warning = warnings[component.key] || {};
      const row = document.createElement("div");
      row.className = "prod";
      const text = document.createElement("div");
      text.className = "grow";
      const name = document.createElement("div");
      name.className = "note-txt strong";
      name.textContent = `${COMPONENT_LABELS[component.key] || component.key} excluded`;
      const sub = document.createElement("div");
      sub.className = "muted micro";
      sub.textContent = `${warning.observed} same-source ${warning.source_label} `
        + `baseline observations; ${warning.required} required. `
        + `${warning.other_source_observations_excluded} other-source observations excluded.`;
      const val = document.createElement("span");
      val.className = "note-txt strong";
      val.textContent = `${warning.observed}/${warning.required}`;
      text.append(name, sub);
      row.append(text, val);
      body.appendChild(row);
    });
  }

  function renderFacts(r) {
    const body = document.getElementById("ry-advice");
    body.replaceChildren();
    const facts = [];
    const components = (r && r.components) || [];
    if (components.length) {
      const lowest = components.reduce((a, b) => a.score <= b.score ? a : b);
      facts.push(`Lowest component: ${COMPONENT_LABELS[lowest.key] || lowest.key} `
        + `${lowest.score}/100 — ${lowest.basis}.`);
    }
    const muscles = (r && r.muscle_recovery) || [];
    const sore = muscles.filter((m) => m.sore).map((m) => m.group);
    if (sore.length) facts.push(`Soreness recorded for: ${sore.join(", ")}.`);
    const trained = muscles.filter((m) => m.days_since != null);
    if (trained.length) {
      const nearest = Math.min(...trained.map((m) => m.days_since));
      facts.push(`Most recent training exposure: ${nearest === 0 ? "today" : `${nearest} day${nearest === 1 ? "" : "s"} ago`}.`);
    }
    if (!facts.length) {
      const p = document.createElement("p");
      p.className = "muted note-txt m0";
      p.textContent = "No deterministic detail available.";
      body.appendChild(p);
      return;
    }
    const ul = document.createElement("ul");
    ul.className = "plain-list";
    facts.forEach((f) => {
      const li = document.createElement("li");
      li.className = "note-txt";
      li.textContent = "• " + f;
      ul.appendChild(li);
    });
    body.appendChild(ul);
  }

  async function boot() {
    try {
      const response = await fetchJSON("/api/recovery/readiness");
      readinessData = response.result || null;
      readinessEvidence = response.evidence || null;
    } catch (_) {
      readinessData = null;
      readinessEvidence = null;
    }
    renderReadiness();
    renderBreakdown(readinessData);
    renderFacts(readinessData);
  }

  window.addEventListener("themechange", () => { renderGauge(readinessData); renderDrag(readinessData); });
  window.addEventListener("resize", () => Object.values(charts).forEach((c) => c && c.resize()));
  boot();
})();
