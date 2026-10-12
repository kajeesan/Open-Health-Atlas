(function () {
  "use strict";
  const root = document.getElementById("rEvidence");
  if (!root) return;
  const el = (id) => document.getElementById(id);
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const state = { packet: null, display: null, question: null, pending: null, saving: false, request: 0, sources: [], limitNotice: false };
  const labels = { setup: "Exercise setup", side: "Exercise side", pain_limited: "Pain-limited effort", location: "Symptom location", provoking_movement: "Provoking movement" };

  function node(tag, text, className) {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    if (className) item.className = className;
    return item;
  }

  async function readJSON(url, options) {
    const response = await fetch(url, { credentials: "same-origin", ...options });
    let body;
    try { body = await response.json(); }
    catch (_error) { throw new Error("The server returned an unreadable response. Retry the request."); }
    if (!response.ok || body.ok !== true) {
      const error = new Error(typeof body.error === "string" ? body.error : "The request failed. Please retry.");
      error.httpStatus = response.status;
      throw error;
    }
    return body;
  }

  function sourceRecords(packet) {
    const records = [
      ...packet.training_records.map((item) => ({ id: item.id, label: `${item.record.date} · ${item.record.exercise_title}`, record: item })),
      ...packet.pain_reports.map((item) => ({ id: item.evidence_id, label: `${item.date} · ${item.region} · ${item.side}`, record: item })),
      ...packet.fitness_measurements.map((item) => ({ id: item.id, label: `${item.record.date} · ${item.record.movement}`, record: item })),
      ...[...packet.recovery.daily, ...packet.recovery.sleep, ...packet.recovery.reported]
        .map((item) => ({ id: item.id, label: `${item.date} · ${item.source || "reported recovery"}`, record: item })),
    ];
    return [...new Map(records.map((item) => [item.id, item])).values()];
  }

  function table(title, columns, rows, empty) {
    const section = node("div");
    section.append(node("h4", title, "card-title mt-2 mb-1"));
    if (rows.length === 0) { section.append(node("p", empty, "muted micro")); return section; }
    const wrapper = node("div", undefined, "table-wrap");
    const tableElement = node("table", undefined, "data-table");
    const head = node("thead"), heading = node("tr"), body = node("tbody");
    columns.forEach((label) => { const cell = node("th", label); cell.scope = "col"; heading.append(cell); });
    head.append(heading);
    rows.forEach((values) => { const row = node("tr"); values.forEach((value) => row.append(node("td", value ?? "unknown"))); body.append(row); });
    tableElement.append(head, body); wrapper.append(tableElement); section.append(wrapper);
    return section;
  }

  function renderFacts(packet) {
    const facts = el("rEvidenceFacts");
    facts.replaceChildren(
      table("Recent training", ["Date", "Exercise", "Working sets", "Estimated 1RM, kg"],
        packet.training_exposure.slice(-5).map((item) => [item.date, item.exercise_title, item.logged_working_sets,
          item.best_e1rm == null ? null : Number(item.best_e1rm.toFixed(1))]),
        "No training records in these dates. This does not establish that no training occurred."),
      table("Symptom reports", ["Date", "Location / side", "Pain, 0–10", "Reported onset"],
        packet.pain_reports.slice(-5).map((item) => [item.date, `${item.region} / ${item.side}`, item.intensity,
          item.reported_onset_date ? `${item.reported_onset_date} (${item.onset_precision})` : "unknown"]),
        "No symptom reports for this location in these dates. Missing reports do not mean pain-free."),
      table("Recovery readings", ["Date", "Source", "Sleep, h", "HRV, ms", "Resting HR"],
        packet.recovery.daily.slice(-5).map((item) => [item.date, item.source, item.sleep_hours, item.hrv_ms, item.resting_hr]),
        "No daily recovery readings in these dates. Other reported recovery records are available under sources when present."),
    );
    const contrast = packet.training_before_symptoms.at(-1);
    if (contrast && contrast.windows.length === 2) {
      facts.append(node("h4", "Training before the latest symptom report", "card-title mt-2 mb-1"));
      contrast.windows.forEach((window) => facts.append(node("p",
        `${window.from} to ${window.to}: ${window.logged_working_sets} logged working sets; ${window.unknown_dates} of 7 dates unknown.`, "micro")));
      facts.append(node("p", "Differences describe logged sets only. Gaps prevent a conclusion about total activity or the cause of symptoms.", "muted micro"));
    }
  }

  function setQuestion(question, correction = null) {
    const targetLabel = question ? (correction ? question.target_label : questionTargetLabel(question)) : null;
    state.question = question ? { ...question, target_label: targetLabel } : null;
    state.pending = null;
    const form = el("rContextForm");
    form.hidden = !question;
    if (!question) return;
    el("rContextQuestion").textContent = correction ? `Correct ${labels[question.context_key] || question.context_key}: ${targetLabel}` : `${question.question} · ${targetLabel || "Target details unavailable"}`;
    el("rContextReason").textContent = correction ? "The previous answer remains in the history; this saves a dated correction." : question.why_answer_changes_interpretation;
    if (!targetLabel) el("rContextReason").textContent += ` Subject: ${question.subject_kind} · ${question.subject_key}. Reload evidence to identify the target before answering.`;
    el("rContextValue").value = correction && correction.status === "answered" ? correction.value : "";
    el("rContextValue").placeholder = question.context_key === "side" ? "left, right or bilateral" : question.context_key === "pain_limited" ? "yes or no" : "";
    el("rContextFrom").value = question.effective_from;
    el("rContextTo").value = correction?.effective_to || "";
    el("rContextFrom").max = state.display.anchor_date;
    el("rContextTo").max = state.display.anchor_date;
    el("rContextSave").textContent = correction ? "Save correction" : "Save answer";
    el("rContextCancel").hidden = !correction;
    controls(!targetLabel);
  }

  function contextTargetLabel(item, parent, kind) {
    if (item.subject_kind !== kind) return null;
    if (kind === "exercise") {
      if (!parent.template_id || String(parent.template_id) !== item.subject_key || !parent.record?.exercise_title) return null;
      const setup = parent.reported_context?.setup;
      return `${parent.record.exercise_title} · template ${parent.template_id}${setup?.status === "answered" ? ` · setup: ${setup.value}` : " · setup unreported"}`;
    }
    if (kind === "pain") {
      const anchor = parent.episode_anchor;
      const day = String(parent.id) === item.subject_key ? parent.date :
        anchor && String(anchor.id) === item.subject_key ? anchor.date : null;
      return day && parent.region && parent.side ? `${day} · ${parent.region} · ${parent.side}` : null;
    }
    const record = parent.record;
    return record && String(record.id) === item.subject_key && record.date && record.movement && record.side ?
      `${record.date} · ${record.movement} · ${record.side}` : null;
  }

  function questionTargetLabel(question) {
    const references = new Set(question.parent_ids || []);
    const targets = new Set();
    for (const [kind, parents] of [["exercise", state.packet.training_records], ["pain", state.packet.pain_reports], ["fitness", state.packet.fitness_measurements]]) {
      for (const parent of parents) {
        if (references.size && !references.has(parent.evidence_id || parent.id)) continue;
        const label = contextTargetLabel(question, parent, kind);
        if (label) targets.add(label);
      }
    }
    return [...targets].join("; ") || null;
  }

  function renderSaved(packet) {
    const saved = new Map();
    for (const [kind, parents] of [["exercise", packet.training_records], ["pain", packet.pain_reports], ["fitness", packet.fitness_measurements]]) {
      for (const parent of parents) {
        for (const item of Object.values(parent.reported_context || {})) {
          if (!saved.has(item.id)) saved.set(item.id, { item, targets: new Set() });
          const target = contextTargetLabel(item, parent, kind);
          if (target) saved.get(item.id).targets.add(target);
        }
      }
    }
    el("rContextSaved").hidden = saved.size === 0;
    const list = el("rContextSavedList"); list.replaceChildren();
    saved.forEach(({ item, targets }) => {
      const targetLabel = [...targets].join("; ");
      const line = node("div", undefined, "mt-2");
      line.append(node("p", targetLabel || "Target details unavailable", "micro evidence-line"));
      line.append(node("p", `${labels[item.context_key] || item.context_key}: ${item.status === "skipped" ? "unknown (skipped)" : item.value}`, "micro m0"));
      line.append(node("p", `Applies ${item.effective_from}${item.effective_to ? ` through ${item.effective_to}` : " onward"}. Source: your explicit report.`, "muted micro"));
      const identity = node("details", undefined, "evidence-details");
      identity.append(node("summary", "Record identity"),
        node("p", `${item.subject_kind} · ${item.subject_key} · context revision ${item.id}`, "muted micro evidence-line"));
      line.append(identity);
      const button = node("button", "Correct", "btn quiet"); button.type = "button";
      button.disabled = targets.size === 0;
      button.addEventListener("click", () => {
        if (targets.size === 0) return;
        if (state.pending || state.saving) { el("rContextStatus").textContent = "Retry the pending save before starting a correction."; return; }
        setQuestion({ ...item, supersedes_id: item.id, target_label: targetLabel }, item);
        el("rContextValue").focus();
      });
      line.append(button); list.append(line);
    });
  }

  function render(body) {
    state.packet = body.result; state.display = body.display;
    const packet = body.result;
    el("rEvidenceFrom").value = packet.range.from; el("rEvidenceTo").value = packet.range.to;
    el("rEvidenceFrom").max = body.display.anchor_date; el("rEvidenceTo").max = body.display.anchor_date;
    el("rEvidenceScope").textContent = `${body.display.data_class === "fictional" ? "Fictional records · " : ""}${packet.range.from} through ${packet.range.to} · inclusive dates${state.limitNotice ? " · evidence is limited to the latest 366 days" : ""}`;
    const recoveryCount = packet.recovery.daily.length + packet.recovery.sleep.length + packet.recovery.reported.length;
    el("rEvidenceCounts").textContent = `${packet.training_records.length} training set records · ${packet.pain_reports.length} symptom reports · ${recoveryCount} recovery records`;
    const known = packet.coverage.filter((item) => item.state !== "unknown").length;
    el("rEvidenceCoverage").textContent = `Training capture known on ${known} of ${packet.coverage.length} dates. Remaining dates are unknown. Showing the latest five rows per table.`;
    renderFacts(packet); renderSaved(packet);
    state.sources = sourceRecords(packet);
    el("rEvidenceSources").hidden = state.sources.length === 0;
    el("rEvidenceSourceSummary").textContent = `Inspect ${state.sources.length} source records`;
    el("rEvidenceIdentity").textContent = `Evidence packet: ${packet.evidence_id}`;
    const select = el("rEvidenceSource"); select.replaceChildren();
    state.sources.forEach((item, index) => { const option = node("option", `${item.label} · ${item.id}`); option.value = String(index); select.append(option); });
    select.value = "0";
    showSource();
    setQuestion(packet.next_questions[0] || null);
    el("rEvidenceBody").hidden = false;
  }

  function showSource() {
    const source = state.sources[Number(el("rEvidenceSource").value)];
    el("rEvidenceSourceRecord").textContent = source ? JSON.stringify(source.record, null, 2) : "No source selected.";
  }

  function selectedURL() {
    const params = new URLSearchParams();
    if (el("rEvidenceFrom").value || el("rEvidenceTo").value) {
      params.set("from", el("rEvidenceFrom").value); params.set("to", el("rEvidenceTo").value);
    }
    params.set("region", el("rEvidenceRegion").value);
    if (el("rEvidenceSide").value) params.set("side", el("rEvidenceSide").value);
    return `/api/recovery/training-evidence?${params}`;
  }

  async function load() {
    if (state.pending || state.saving) { el("rContextStatus").textContent = "Retry the pending save before changing evidence dates."; return; }
    const serial = ++state.request;
    root.setAttribute("aria-busy", "true");
    el("rEvidenceApply").disabled = true; el("rEvidenceReadError").hidden = true; el("rEvidenceReload").hidden = true;
    el("rEvidenceBody").hidden = true;
    try {
      const body = await readJSON(selectedURL());
      if (serial === state.request) render(body);
    } catch (error) {
      if (serial === state.request) {
        el("rEvidenceReadError").textContent = `Evidence could not be loaded: ${error.message}`;
        el("rEvidenceReadError").hidden = false; el("rEvidenceReload").hidden = false;
      }
    } finally {
      if (serial === state.request) { root.setAttribute("aria-busy", "false"); el("rEvidenceApply").disabled = false; }
    }
  }

  function controls(locked) {
    ["rContextValue", "rContextFrom", "rContextTo", "rContextSkip", "rContextCancel"].forEach((id) => { el(id).disabled = locked; });
    el("rContextSave").disabled = state.saving || !state.question?.target_label;
  }

  async function save(status) {
    if (state.saving || !state.question?.target_label) return;
    if (!state.pending) {
      const question = state.question;
      state.pending = {
        capture_id: `panel:${crypto.randomUUID()}`, subject_kind: question.subject_kind,
        subject_key: question.subject_key, context_key: question.context_key,
        effective_from: el("rContextFrom").value, status,
      };
      if (el("rContextTo").value) state.pending.effective_to = el("rContextTo").value;
      if (status === "answered") state.pending.value = el("rContextValue").value.trim();
      if (question.supersedes_id) state.pending.supersedes_id = question.supersedes_id;
    }
    state.saving = true; controls(true);
    el("rContextStatus").textContent = "Saving your report…";
    try {
      await readJSON("/api/recovery/training-context", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify(state.pending),
      });
      const skipped = state.pending.status === "skipped";
      state.pending = null; state.saving = false;
      el("rContextStatus").textContent = skipped ? "Skipped. The answer remains unknown." : "Saved your report. It will be reused for the applicable dates.";
      await load();
    } catch (error) {
      state.saving = false;
      if (error.httpStatus === 400 || error.httpStatus === 409) {
        state.pending = null; controls(false);
        el("rContextStatus").textContent = `Not saved: ${error.message} Check the answer and dates, or reload evidence before correcting an older answer.`;
        el("rContextSave").textContent = "Save answer";
      } else {
        controls(true);
        el("rContextStatus").textContent = `Save not confirmed: ${error.message} Retry sends the same answer without duplicating it.`;
        el("rContextSave").textContent = "Retry save";
      }
    }
  }

  el("rEvidenceRange").addEventListener("submit", (event) => { event.preventDefault(); state.limitNotice = false; void load(); });
  el("rEvidenceReload").addEventListener("click", () => { void load(); });
  el("rEvidenceSource").addEventListener("change", showSource);
  el("rContextForm").addEventListener("submit", (event) => { event.preventDefault(); void save("answered"); });
  el("rContextSkip").addEventListener("click", () => { void save("skipped"); });
  el("rContextCancel").addEventListener("click", () => setQuestion(state.packet?.next_questions[0] || null));
  window.HermesUI?.onRange("recovery", (range) => {
    if (!state.display || state.pending || state.saving) return;
    state.limitNotice = range === "All";
    const days = { Day: 1, Week: 7, Month: 30, Year: 365, All: 366 }[range] || 7;
    const end = new Date(`${state.display.anchor_date}T00:00:00Z`);
    end.setUTCDate(end.getUTCDate() - days + 1);
    el("rEvidenceFrom").value = end.toISOString().slice(0, 10);
    el("rEvidenceTo").value = state.display.anchor_date;
    void load();
  });
  void load();
})();
