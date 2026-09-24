/* Phase 7 Insight Explorer. Every displayed analytical value comes from the
   engine response. Browser code selects scope and renders strings safely; it
   performs no statistics and never derives dates from the browser timezone. */
(function () {
  "use strict";
  const root = document.getElementById("insight-root");
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const MODES = ["ordinal", "green-vs-non-green", "red-vs-non-red"];
  const READINESS_STATES = {
    logic_not_implemented: ["Not implemented", "Requires implementation work before records can support this feature."],
    present_not_connected: ["Data present, integration missing", "Requires integration work to connect existing records."],
    implemented_never_logged: ["No records yet", "Check the recording or configuration prerequisites below."],
    stale: ["Out of date", "Check which measurements or source updates the engine needs."],
    too_sparse_for_analysis: ["Too little aligned data", "Check the missing observations and analysis gates below."],
    sufficient: ["Ready for this query", "Meets this query’s data requirements; this does not establish clinical reliability."],
  };
  let conversations = [];
  let current = null;
  let canonicalControl = null;
  let refreshToken = 0;
  let contextGeneration = 0;
  let analysisController = null;
  const analysisStorageKey = "hermes.insight.analysis-job";
  let selectedFindings = [];

  function errorText(value) {
    if (typeof value === "string") return value;
    if (value && typeof value.message === "string") return value.message;
    return "That request could not be completed.";
  }

  async function requestJSON(url, options) {
    const response = await fetch(url, Object.assign(
      { credentials: "same-origin" }, options || {}
    ));
    if (response.status === 401) {
      location.assign("/login");
      throw new Error("Please sign in again.");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(errorText(data.error) || ("HTTP " + response.status));
    }
    return data;
  }

  async function mutate(url, method, body) {
    return requestJSON(url, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf,
      },
      body: JSON.stringify(body),
    });
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function human(value) {
    return String(value == null ? "Not available" : value).replace(/_/g, " ");
  }

  function raw(value) {
    if (value === null || value === undefined || value === "") return "Not available";
    if (Array.isArray(value)) return value.length ? value.map(raw).join(", ") : "None";
    if (typeof value === "object") {
      return Object.entries(value).map(([key, item]) => `${human(key)}: ${raw(item)}`).join(" · ");
    }
    return String(value);
  }

  function formatWindow(windowValue) {
    if (!windowValue || typeof windowValue !== "object") return "Not available";
    if (windowValue.from && windowValue.to) {
      return `${windowValue.from} through ${windowValue.to}`;
    }
    if (windowValue.kind === "all") return "All available data";
    return "Not available";
  }

  function contextLabel(conversation) {
    if (!conversation) return "No conversation selected";
    return `${human(conversation.lens)} · ${formatWindow(conversation.context.range)}`;
  }

  function rangeQuery(range) {
    if (range.kind === "all") return "range=all";
    return "range=bounded&from=" + encodeURIComponent(range.from)
      + "&to=" + encodeURIComponent(range.to);
  }

  function detailRow(label, value) {
    const row = el("div", "finding-detail");
    row.append(el("span", "label", label), el("span", "", raw(value)));
    return row;
  }

  function section(title, content) {
    const block = el("div", "finding-section");
    block.appendChild(el("div", "label", title));
    if (content instanceof Node) block.appendChild(content);
    else block.appendChild(el("div", "note-txt", raw(content)));
    return block;
  }

  function arrayLines(items) {
    const list = el("ul", "plain-list");
    (items || []).forEach((item) => list.appendChild(el("li", "", raw(item))));
    if (!list.childNodes.length) list.appendChild(el("li", "muted", "None recorded"));
    return list;
  }

  function evidenceDetails(title) {
    const details = el("details", "evidence-details");
    details.appendChild(el("summary", "", title));
    return details;
  }

  function evidenceFields(value) {
    const fields = el("div", "finding-details mt-2");
    Object.entries(value || {}).forEach(([key, item]) => {
      fields.appendChild(detailRow(human(key), item));
    });
    return fields;
  }

  function sharedEvidenceLink() {
    const link = el("a", "note-txt", "Shared analysis evidence");
    link.href = "#analysis-evidence";
    link.addEventListener("click", (event) => {
      event.preventDefault();
      $("analysis-evidence").open = true;
      const summary = $("analysis-evidence-summary");
      summary.focus();
      summary.scrollIntoView({ block: "nearest" });
    });
    return link;
  }

  function renderAnalysisEvidence(result) {
    const details = $("analysis-evidence");
    const summary = el("summary", "", "Shared analysis evidence");
    summary.id = "analysis-evidence-summary";
    details.replaceChildren(summary);
    details.open = false;
    details.hidden = false;
    details.append(
      section("Analysis metadata", evidenceFields(result.meta)),
      section("Coverage and source manifests", evidenceFields(result.coverage)),
      section("Suppression counts", evidenceFields(result.suppression_counts)),
      detailRow("Contract", result.contract_version)
    );
    const warnings = $("analysis-warnings");
    warnings.replaceChildren();
    warnings.hidden = !(result.warnings || []).length;
    if (!warnings.hidden) warnings.appendChild(section("Analysis warnings", arrayLines(result.warnings)));
  }

  function exposureName(finding) {
    const components = (((finding || {}).exposure || {}).components || []);
    if (!components.length) return "Unnamed engine finding";
    return components.map((component) => component.display || component.key || component.exposure_key)
      .filter(Boolean).join(" + ") || "Unnamed engine finding";
  }

  function compactEvidenceId(value) {
    const text = String(value || "");
    return text.length > 24 ? text.slice(0, 20) + "…" : text;
  }

  function evidenceBlock(evidence) {
    if (!evidence || evidence.contract !== "openhealthatlas-chat-evidence-v1") return null;
    const block = el("div", "message-evidence");
    block.appendChild(el(
      "strong", "message-evidence-title",
      evidence.kind === "verified_receipt"
        ? "Verified OpenHealthAtlas evidence"
        : "Selected deterministic evidence"
    ));
    block.appendChild(el("span", "micro muted", formatWindow(evidence.range)));
    const refs = evidence.kind === "verified_receipt"
      ? (evidence.evidence_refs || [])
      : (evidence.selected_findings || []).map((item) => item.finding_id);
    refs.forEach((value) => {
      const ref = el("code", "message-evidence-ref", compactEvidenceId(value));
      ref.title = String(value);
      block.appendChild(ref);
    });
    const commands = (evidence.tool_receipts || []).map((item) => item.command).filter(Boolean);
    if (commands.length) {
      block.appendChild(el("span", "micro muted", `Tools: ${commands.join(" → ")}`));
    }
    return block;
  }

  function paintSelectedFindings() {
    const status = $("ins-selected-evidence");
    status.hidden = selectedFindings.length === 0;
    status.replaceChildren();
    if (selectedFindings.length) {
      status.append(
        el("strong", "", `${selectedFindings.length} finding${selectedFindings.length === 1 ? "" : "s"} selected for Hermes`),
        el("span", "micro muted", "OpenHealthAtlas must replay each identifier in this exact window before Hermes may interpret it.")
      );
    }
    document.querySelectorAll("[data-evidence-finding]").forEach((button) => {
      const on = selectedFindings.some((item) => item.finding_id === button.dataset.evidenceFinding);
      button.textContent = on ? "Selected for Hermes" : "Ask Hermes about this";
      button.classList.toggle("on", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function clearSelectedFindings() {
    selectedFindings = [];
    paintSelectedFindings();
  }

  function toggleSelectedFinding(finding, meta) {
    if (!current || current.context.range.kind !== "bounded") {
      $("ins-scope-status").textContent = "Choose a day, week, month, or year before selecting evidence for Hermes.";
      return;
    }
    const findingId = finding.finding_id;
    const inputFingerprint = (finding.provenance || {}).input_fingerprint
      || meta.input_fingerprint;
    if (!findingId || !inputFingerprint) {
      $("ins-scope-status").textContent = "This finding has no replayable evidence identity.";
      return;
    }
    const existing = selectedFindings.findIndex((item) => item.finding_id === findingId);
    if (existing >= 0) {
      selectedFindings.splice(existing, 1);
    } else if (selectedFindings.length >= 3) {
      $("ins-scope-status").textContent = "Choose no more than three findings for one Hermes turn.";
      return;
    } else {
      selectedFindings.push({
        outcome: "subjective.day_rating",
        finding_id: findingId,
        input_fingerprint: inputFingerprint,
      });
    }
    paintSelectedFindings();
  }

  function findingCard(finding, meta, options) {
    const card = el("article", "finding-card");
    const head = el("div", "row between wrap gap-2");
    const titleWrap = el("div");
    titleWrap.append(
      el("h4", "finding-title", exposureName(finding)),
      el("div", "muted micro", `${human((finding.outcome || {}).mode)} association`)
    );
    const quality = (finding.quality || {}).tier || "unrated";
    head.append(titleWrap, el("span", "chip", human(quality)));
    card.appendChild(head);

    const components = (((finding || {}).exposure || {}).components || []);
    const summary = el("div", "finding-details mt-2");
    components.forEach((component) => {
      const timing = el("div", "finding-component mt-2");
      if (components.length > 1) timing.appendChild(el("strong", "note-txt", component.display || component.exposure_key));
      timing.appendChild(evidenceFields({
        direction: component.direction,
        lag: component.lag_days == null ? null : `${component.lag_days} day(s)`,
        window: component.window_days == null ? null : `${component.window_days} day(s)`,
      }));
      card.appendChild(timing);
    });
    const sample = finding.sample || {};
    const effect = finding.effect || {};
    summary.append(
      detailRow("Eligible / complete / missing", [sample.eligible_n, sample.complete_n, sample.missing_n]),
      detailRow(effect.method ? `Effect (${human(effect.method)})` : "Effect", effect.estimate),
      detailRow("95% interval", effect.ci95)
    );
    card.appendChild(summary);
    card.append(
      section("Warnings and limits", arrayLines(finding.warnings)),
      section("Evidence against", arrayLines(finding.evidence_against))
    );
    const confounders = finding.confounders || {};
    if ((confounders.sensitive_to || []).length) {
      card.appendChild(section("Sensitive to confounders", arrayLines(confounders.sensitive_to)));
    }
    if ((confounders.unchecked || []).length) {
      card.appendChild(section("Unchecked confounders", confounders.unchecked.map((item) => item.key || raw(item))));
    }
    if (finding.alternatives !== undefined) {
      card.appendChild(section("Alternatives", finding.alternatives));
    }
    if (finding.next_measurement !== undefined) {
      card.appendChild(section("Next measurement", finding.next_measurement));
    }
    const details = evidenceDetails("Evidence details");
    details.append(
      detailRow("Finding ID", finding.finding_id),
      detailRow("Candidate key", finding.candidate_key),
      section("Outcome", evidenceFields(finding.outcome)),
      section("Exposure and source semantics", arrayLines(components)),
      section("Sample", evidenceFields(sample)),
      section("Effect", evidenceFields(effect)),
      section("Rates", finding.rates),
      section("Testing", evidenceFields(finding.testing)),
      section("Stability", evidenceFields(finding.stability)),
      section("Quality", evidenceFields(finding.quality)),
      section("Confounders", evidenceFields(finding.confounders)),
      section("Evidence for", arrayLines(finding.evidence_for))
    );
    const sharedProvenance = (options && options.sharedProvenance) || {};
    const ownProvenance = Object.fromEntries(Object.entries(finding.provenance || {})
      .filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(sharedProvenance[key])));
    if (Object.keys(ownProvenance).length) {
      details.appendChild(section("Finding provenance", evidenceFields(ownProvenance)));
    }
    details.appendChild(sharedEvidenceLink());
    card.appendChild(details);
    if (options && options.promote && (finding.quality || {}).eligible_for_hypothesis) {
      const button = el("button", "btn quiet mt-2", "Track as a hypothesis");
      button.type = "button";
      button.addEventListener("click", () => promoteFinding(finding, meta, button));
      card.appendChild(button);
    }
    if (finding.finding_id) {
      const ask = el("button", "btn quiet mt-2", "Ask Hermes about this");
      ask.type = "button";
      ask.dataset.evidenceFinding = finding.finding_id;
      ask.setAttribute("aria-pressed", "false");
      ask.addEventListener("click", () => toggleSelectedFinding(finding, meta));
      card.appendChild(ask);
    }
    return card;
  }

  async function promoteFinding(finding, meta, button) {
    if (!current) return;
    button.disabled = true;
    button.textContent = "Saving…";
    try {
      await mutate("/api/insights/hypotheses/promote", "POST", {
        outcome: (finding.outcome || {}).key,
        finding_id: finding.finding_id,
        input_fingerprint: (finding.provenance || {}).input_fingerprint
          || meta.input_fingerprint,
        range: current.context.range,
      });
      button.textContent = "Tracked";
      await loadHypotheses();
    } catch (error) {
      button.disabled = false;
      button.textContent = "Try tracking again";
      $("findings-empty").hidden = false;
      $("findings-empty").textContent = error.message;
    }
  }

  function bubble(role, text, pending, evidence) {
    const node = el("div", "msg " + role + (pending ? " pending" : ""));
    node.appendChild(el("div", "message-content", text));
    const receipt = evidenceBlock(evidence);
    if (receipt) node.appendChild(receipt);
    $("ins-chat").appendChild(node);
    $("ins-chat").scrollTop = $("ins-chat").scrollHeight;
    return node;
  }

  async function loadConversationMessages() {
    const generation = contextGeneration;
    const conversationId = current && current.id;
    const log = $("ins-chat");
    log.replaceChildren();
    if (!current) {
      bubble("assistant", "Choose or start a conversation.");
      return;
    }
    try {
      const data = await requestJSON(
        `/api/chat/conversations/${encodeURIComponent(current.id)}/messages?limit=100`
      );
      if (generation !== contextGeneration || !current || current.id !== conversationId) return;
      if (!data.messages.length) {
        bubble("assistant", "Ask a question about this investigation. I will keep its lens and date window with each message.");
      } else {
        data.messages.forEach((message) => {
          const node = bubble(message.role, message.content, false, message.evidence);
          if (message.role === "user" && message.delivery_status === "uncertain") {
            node.title = "Delivery was uncertain; this message will not be sent again automatically.";
          }
        });
      }
      $("ins-chat-state").textContent = current.archived ? "archived" : "";
      $("ins-input").disabled = current.archived;
      $("ins-send").disabled = current.archived;
    } catch (error) {
      if (generation !== contextGeneration) return;
      bubble("assistant", "Conversation unavailable: " + error.message);
    }
  }

  async function sendMessage(message) {
    if (!current || current.archived) return;
    const conversation = current;
    const generation = contextGeneration;
    const isCurrentTurn = () => generation === contextGeneration
      && current && current.id === conversation.id;
    const turnSelection = selectedFindings.map((item) => Object.assign({}, item));
    const selectionEvidence = turnSelection.length ? {
      contract: "openhealthatlas-chat-evidence-v1",
      kind: "selection",
      range: conversation.context.range,
      selected_findings: turnSelection,
    } : null;
    bubble("user", message, false, selectionEvidence);
    const pending = bubble("assistant", "OpenHealthAtlas is thinking…", true);
    $("ins-input").disabled = true;
    $("ins-send").disabled = true;
    try {
      const data = await mutate(
        `/api/chat/conversations/${encodeURIComponent(conversation.id)}/send`,
        "POST",
        {
          message,
          turn_id: crypto.randomUUID().toLowerCase(),
          context: conversation.context,
          ...(turnSelection.length ? { selected_findings: turnSelection } : {}),
        }
      );
      pending.remove();
      if (!isCurrentTurn()) return;
      bubble("assistant", data.reply, false, data.evidence);
      clearSelectedFindings();
      await reloadConversation(conversation.id, generation);
    } catch (error) {
      pending.remove();
      if (!isCurrentTurn()) return;
      bubble("assistant", "Could not send: " + error.message);
    } finally {
      if (isCurrentTurn()) {
        $("ins-input").disabled = current.archived;
        $("ins-send").disabled = current.archived;
        $("ins-input").focus();
      }
    }
  }

  function paintConversationPicker() {
    const picker = $("ins-conversation");
    picker.replaceChildren();
    conversations.forEach((conversation) => {
      const option = el(
        "option",
        "",
        `${conversation.title} · ${human(conversation.lens)}`
      );
      option.value = conversation.id;
      option.selected = Boolean(current && conversation.id === current.id);
      picker.appendChild(option);
    });
    $("ins-context-chip").textContent = contextLabel(current);
    $("ins-chat-subtitle").textContent = current
      ? `${current.title} · panel conversation only — never mixed with Telegram.`
      : "Panel conversation only — never mixed with Telegram.";
  }

  async function reloadConversation(id, generation = contextGeneration) {
    const detail = await requestJSON(
      `/api/chat/conversations/${encodeURIComponent(id)}`
    );
    if (generation !== contextGeneration) return false;
    current = detail.conversation;
    const index = conversations.findIndex((item) => item.id === id);
    if (index >= 0) conversations[index] = current;
    else conversations.unshift(current);
    localStorage.setItem("hermes.insight.conversation", id);
    canonicalControl = current.context.range.kind === "all"
      ? { granularity: "all", anchor: null, range: current.context.range }
      : {
          granularity: $("ins-granularity").value === "all"
            ? "month" : $("ins-granularity").value,
          anchor: current.context.range.to,
          range: current.context.range,
        };
    $("ins-granularity").value = canonicalControl.granularity;
    paintConversationPicker();
    paintRange();
    await loadConversationMessages();
    return generation === contextGeneration;
  }

  async function loadConversations() {
    const data = await requestJSON("/api/chat/conversations?archived=0&limit=100");
    conversations = data.conversations || [];
    const remembered = localStorage.getItem("hermes.insight.conversation");
    current = conversations.find((item) => item.id === remembered)
      || conversations[0] || null;
    if (!current) {
      const created = await mutate("/api/chat/conversations", "POST", {
        lens: "general",
        context: { version: 1, range: { kind: "all" }, selected_region_ids: [] },
      });
      current = created.conversation;
      conversations = [current];
    }
    await reloadConversation(current.id);
  }

  function paintRange() {
    if (!canonicalControl) return;
    $("ins-window").textContent = formatWindow(canonicalControl.range);
    $("ins-context-chip").textContent = contextLabel(current);
    const all = canonicalControl.granularity === "all";
    $("ins-range-prev").disabled = all;
    $("ins-range-next").disabled = all;
    $("ins-range-today").disabled = all;
  }

  async function setCanonicalWindow(granularity, shift, useCurrentAnchor) {
    const generation = beginContextChange("Updating the investigation window…");
    const conversation = current;
    const query = new URLSearchParams({ granularity, shift: String(shift) });
    if (useCurrentAnchor && canonicalControl && canonicalControl.anchor && granularity !== "all") {
      query.set("anchor", canonicalControl.anchor);
    }
    try {
      const data = await requestJSON("/api/insights/range?" + query.toString());
      if (generation !== contextGeneration) return;
      canonicalControl = data.result;
      const context = {
        version: 1,
        range: canonicalControl.range,
        selected_region_ids: conversation.context.selected_region_ids,
      };
      const patched = await mutate(
        `/api/chat/conversations/${encodeURIComponent(conversation.id)}`,
        "PATCH",
        { context }
      );
      if (generation !== contextGeneration) return;
      current = patched.conversation;
      clearSelectedFindings();
      const index = conversations.findIndex((item) => item.id === current.id);
      if (index >= 0) conversations[index] = current;
      paintRange();
      paintConversationPicker();
      markAnalysisIdle("Window updated. Analyze it when you are ready.");
    } catch (error) {
      if (generation !== contextGeneration) return;
      $("ins-scope-status").textContent = "Could not update the window: " + error.message;
    } finally {
      finishContextChange(generation);
    }
  }

  function analysisMatches(token, conversationId, range) {
    return token === refreshToken && current && current.id === conversationId
      && JSON.stringify(current.context.range) === JSON.stringify(range);
  }

  async function loadOutcomes(token, conversationId, range, signal, resumeOnly) {
    const query = "?outcome=subjective.day_rating&mode=all&" + rangeQuery(range);
    const job = await window.HermesAnalysisJobs.wait({
      startURL: "/api/insights/outcome-jobs" + query,
      statusURL: (id) => "/api/insights/outcome-jobs/" + encodeURIComponent(id) + query,
      storageKey: analysisStorageKey, signal, resumeOnly,
      onStatus: (status) => {
        if (!analysisMatches(token, conversationId, range)) return;
        if (status === "queued" || status === "running") {
          $("findings-meta").textContent = status === "queued" ? "Queued" : "Analyzing…";
          $("ins-scope-status").textContent = status === "queued"
            ? "Analysis queued. You can stop waiting and return later."
            : "Analysis is running. You can stop waiting or reload; this job will be kept.";
        }
      },
    });
    if (!analysisMatches(token, conversationId, range)) return;
    if (!job) return false;
    const result = job.result;
    const meta = result.meta || {};
    const sharedProvenance = Object.assign({}, meta);
    if ((result.coverage || {}).dependencies !== undefined) {
      sharedProvenance.dependencies = result.coverage.dependencies;
    }
    renderAnalysisEvidence(result);
    $("analysis-window").textContent = formatWindow(meta.analysis_range);
    $("baseline-window").textContent = formatWindow(meta.baseline_range);
    const groups = $("finding-groups");
    groups.replaceChildren();
    const findings = result.findings || [];
    $("findings-meta").textContent = `${findings.length} engine finding${findings.length === 1 ? "" : "s"}`;
    MODES.forEach((mode) => {
      const group = el("section", "finding-group");
      group.appendChild(el("h3", "finding-group-title", human(mode)));
      const matching = findings.filter((finding) => (finding.outcome || {}).mode === mode);
      if (!matching.length) {
        group.appendChild(el("p", "muted note-txt m0", "No eligible finding in this group."));
      } else {
        matching.forEach((finding) => group.appendChild(
          findingCard(finding, meta, {
            promote: true,
            sharedProvenance,
          })
        ));
      }
      groups.appendChild(group);
    });
    $("findings-empty").hidden = findings.length !== 0;
    if (!findings.length) {
      const warnings = result.warnings || [];
      $("findings-empty").textContent = warnings.length
        ? raw(warnings)
        : "No eligible association is available for this window. Sparse evidence stays quiet.";
    }
    return true;
  }

  async function loadReadiness(token, conversationId, range, signal) {
    const result = (await requestJSON(
      "/api/insights/readiness?outcome=subjective.day_rating&"
      + rangeQuery(range),
      { signal }
    )).result;
    if (!analysisMatches(token, conversationId, range)) return;
    const meta = result.meta || {};
    $("readiness-window").textContent = formatWindow(meta.range);
    const stateCounts = meta.state_counts;
    const features = result.features || result.items || [];
    const states = new Set([
      ...Object.keys(READINESS_STATES), ...Object.keys(stateCounts || {}),
      ...features.map((feature) => feature.state),
    ]);
    const grid = $("readiness-states");
    grid.replaceChildren();
    states.forEach((state) => {
      const [label, help] = Object.prototype.hasOwnProperty.call(READINESS_STATES, state)
        ? READINESS_STATES[state]
        : [human(state), "An additional engine state; inspect its returned details below."];
      const count = stateCounts && Object.prototype.hasOwnProperty.call(stateCounts, state)
        ? stateCounts[state] : null;
      const card = el("details", "readiness-state");
      const summary = el("summary");
      summary.append(el("strong", "", label), el("span", "muted micro", `Count: ${raw(count)}`));
      card.append(summary, detailRow("Engine state", state), el("p", "note-txt", help));
      features.filter((feature) => feature.state === state).forEach((feature) => {
        const item = el("div", "readiness-feature");
        item.append(
          el("h4", "finding-title", feature.feature_key || feature.key || "Unnamed feature"),
          section("What is needed", feature.needed),
          section("Prerequisites", arrayLines(feature.prerequisites)),
          section("Gate failures", arrayLines((feature.factors || {}).gate_failures)),
          evidenceFields({
            observations: feature.observations,
            aligned_n: feature.aligned_n,
            latest_at: feature.latest_at,
            stale_after_days: feature.stale_after_days,
            staleness_anchor: (feature.factors || {}).staleness_anchor,
          })
        );
        const allFields = evidenceDetails("All readiness fields");
        allFields.appendChild(evidenceFields(feature));
        item.appendChild(allFields);
        card.appendChild(item);
      });
      grid.appendChild(card);
    });
    $("readiness-empty").hidden = features.length !== 0;
    if (!features.length) {
      $("readiness-empty").textContent = "No readiness details were returned for this window.";
    }
  }

  function resetAnalysisPanels(message) {
    $("findings-meta").textContent = "Not loaded";
    $("analysis-window").textContent = "—";
    $("baseline-window").textContent = "—";
    $("finding-groups").replaceChildren();
    $("analysis-evidence").hidden = true;
    $("analysis-evidence").replaceChildren();
    $("analysis-warnings").hidden = true;
    $("analysis-warnings").replaceChildren();
    $("finding-groups").setAttribute("aria-busy", "false");
    $("findings-empty").hidden = false;
    $("findings-empty").textContent = message;
    $("readiness-window").textContent = "Not loaded";
    $("readiness-states").replaceChildren();
    $("readiness-states").setAttribute("aria-busy", "false");
    $("readiness-empty").hidden = false;
    $("readiness-empty").textContent = message;
    $("ins-analyze").textContent = "Analyze this window";
  }

  function markAnalysisIdle(message) {
    clearSelectedFindings();
    if (analysisController) analysisController.abort();
    analysisController = null;
    refreshToken += 1;
    resetAnalysisPanels(
      message || "Not loaded yet. Choose “Analyze this window” when you need it."
    );
    $("ins-scope-status").textContent = message
      || "Ready. Analysis runs only when you ask for it.";
  }

  function beginContextChange(message) {
    const generation = ++contextGeneration;
    markAnalysisIdle(message);
    $("ins-analyze").disabled = true;
    return generation;
  }

  function finishContextChange(generation) {
    if (generation === contextGeneration) $("ins-analyze").disabled = false;
  }

  function cancelScopedAnalysis() {
    if (!analysisController) return;
    analysisController.abort();
    analysisController = null;
    refreshToken += 1;
    resetAnalysisPanels("Stopped waiting. The analysis may still finish in the background.");
    $("ins-analyze").textContent = "Resume analysis";
    $("ins-scope-status").textContent = "Stopped waiting. Resume to check the same analysis; the server keeps the job.";
  }

  async function loadScopedAnalysis(resumeOnly = false) {
    if (!current || $("ins-analyze").disabled) return;
    if (analysisController) {
      cancelScopedAnalysis();
      return;
    }
    analysisController = new AbortController();
    clearSelectedFindings();
    const controller = analysisController;
    const range = Object.assign({}, current.context.range);
    const conversationId = current.id;
    const token = ++refreshToken;
    $("findings-meta").textContent = "Loading…";
    $("finding-groups").replaceChildren();
    $("analysis-evidence").hidden = true;
    $("analysis-evidence").replaceChildren();
    $("analysis-warnings").hidden = true;
    $("analysis-warnings").replaceChildren();
    $("finding-groups").setAttribute("aria-busy", "true");
    $("findings-empty").hidden = true;
    $("readiness-states").replaceChildren();
    $("readiness-states").setAttribute("aria-busy", "true");
    $("readiness-empty").hidden = true;
    $("ins-analyze").textContent = "Stop waiting";
    $("ins-scope-status").textContent = "Checking this analysis… You can stop waiting and keep using the page.";
    const results = await Promise.allSettled([
      loadOutcomes(token, conversationId, range, controller.signal, resumeOnly),
      loadReadiness(token, conversationId, range, controller.signal),
    ]);
    if (!analysisMatches(token, conversationId, range)) return;
    analysisController = null;
    if (results[0].status === "fulfilled" && results[0].value === false) {
      markAnalysisIdle();
      return;
    }
    $("finding-groups").setAttribute("aria-busy", "false");
    $("readiness-states").setAttribute("aria-busy", "false");
    $("ins-analyze").textContent = "Refresh analysis";
    if (results[0].status === "rejected") {
      $("findings-meta").textContent = "unavailable";
      $("findings-empty").hidden = false;
      $("findings-empty").textContent = "Findings unavailable: " + errorText(results[0].reason);
    }
    if (results[1].status === "rejected") {
      $("readiness-empty").hidden = false;
      $("readiness-empty").textContent = "Readiness unavailable: " + errorText(results[1].reason);
    }
    const failed = results.filter((result) => result.status === "rejected").length;
    $("ins-scope-status").textContent = failed
      ? "Analysis needs attention. Use “Retry analysis” to try again."
      : "Analysis ready.";
    if (failed) $("ins-analyze").textContent = "Retry analysis";
  }

  function hypothesisHeader(item) {
    const card = el("article", "hypothesis-card");
    const head = el("div", "row between wrap gap-2");
    const components = (item.components || []).map((component) => component.exposure_key)
      .filter(Boolean).join(" + ");
    const left = el("div");
    left.append(
      el("h3", "finding-title", components || human(item.outcome_key || "Hypothesis")),
      el("p", "muted micro m0 mt-1", `Created ${item.created_at || "—"}`)
    );
    head.append(
      left,
      el("span", "chip", human((item.latest_evaluation || {}).status || "candidate"))
    );
    card.appendChild(head);
    return card;
  }

  async function loadHypothesisDetail(item, card, button, detailRoot) {
    button.disabled = true;
    button.textContent = "Loading evidence…";
    detailRoot.replaceChildren();
    try {
      const brief = (await requestJSON(
        `/api/insights/hypotheses/${encodeURIComponent(item.hypothesis_id)}`
      )).result;
      const hypothesis = brief.hypothesis || {};
      const evaluation = hypothesis.latest_evaluation || {};
      const effectSummary = evaluation.effect_summary || {};
      const components = hypothesis.components || [];
      const evidenceFor = brief.evidence_for || [];
      const evidenceAgainst = brief.evidence_against || [];
      const evidenceItems = evidenceFor.concat(evidenceAgainst);
      const evidenceView = (entry) => {
        const finding = entry.finding || {};
        return {
          window: entry.range_from && entry.range_to
            ? `${entry.range_from} through ${entry.range_to}` : null,
          exposure: exposureName(finding),
          direction: (finding.effect || {}).estimate,
          interval: (finding.effect || {}).ci95,
          q: (finding.testing || {}).q,
          warnings: finding.warnings,
        };
      };
      const warnings = evidenceItems.flatMap(
        (entry) => ((entry.finding || {}).warnings || [])
      );
      const details = el("div", "finding-details mt-2");
      details.append(
        detailRow("Status", evaluation.status),
        detailRow("Evidence class", evaluation.evidence_class),
        detailRow("Confidence", evaluation.confidence),
        detailRow(
          "Evidence window",
          evaluation.range_from && evaluation.range_to
            ? `${evaluation.range_from} through ${evaluation.range_to}` : null
        ),
        detailRow("Provenance", effectSummary.provenance),
        detailRow("Direction", effectSummary.direction),
        detailRow("Lag / window", components.map((component) => ({
          exposure: component.exposure_key,
          lag_days: component.lag_days,
          window_days: component.window_days,
        }))),
        detailRow("Counts / missingness", evaluation.sample_size),
        detailRow("Effect / interval", effectSummary.effect),
        detailRow("q / testing", effectSummary.testing),
        detailRow("Stability", evaluation.stability),
        detailRow("Confounders", evaluation.confounders),
        detailRow("Quality", effectSummary.quality),
        detailRow("Warnings", warnings)
      );
      detailRoot.append(
        details,
        section("Evidence for", evidenceFor.length
          ? arrayLines(evidenceFor.map(evidenceView))
          : arrayLines(evaluation.evidence_for)),
        section("Evidence against", evidenceAgainst.length
          ? arrayLines(evidenceAgainst.map(evidenceView))
          : arrayLines(evaluation.evidence_against))
      );
      const annotations = brief.latest_annotations || [];
      const annotationContent = (kind) => annotations
        .filter((entry) => entry.annotation_kind === kind)
        .map((entry) => entry.content);
      detailRoot.append(
        section("Alternatives", arrayLines(annotationContent("alternative"))),
        section("Next measurement", arrayLines(annotationContent("next_experiment")))
      );
      button.remove();
    } catch (error) {
      detailRoot.replaceChildren(
        el("p", "muted note-txt m0 mt-2", "Detail unavailable: " + error.message)
      );
      button.disabled = false;
      button.textContent = "Try evidence again";
    }
  }

  function renderHypothesis(item) {
    const card = hypothesisHeader(item);
    const button = el("button", "btn quiet mt-2", "Show evidence");
    button.type = "button";
    const detailRoot = el("div");
    button.addEventListener("click", () => {
      loadHypothesisDetail(item, card, button, detailRoot);
    });
    card.append(button, detailRoot);
    return card;
  }

  async function loadHypotheses() {
    const list = $("hypothesis-list");
    const button = $("load-hypotheses");
    button.disabled = true;
    button.textContent = "Loading…";
    list.setAttribute("aria-busy", "true");
    list.replaceChildren(el("p", "muted note-txt m0", "Loading hypothesis summaries…"));
    try {
      const result = (await requestJSON("/api/insights/hypotheses?limit=20")).result;
      const items = result.items || [];
      $("hypotheses-meta").textContent = `${items.length} shown`;
      list.replaceChildren();
      if (!items.length) {
        list.appendChild(el("p", "muted note-txt m0", "No tracked hypotheses yet. Eligible findings can be tracked above."));
        button.textContent = "Refresh";
        return;
      }
      const cards = items.map(renderHypothesis);
      cards.forEach((card) => list.appendChild(card));
      button.textContent = "Refresh";
    } catch (error) {
      list.replaceChildren(el("p", "muted note-txt m0", "Hypotheses unavailable: " + error.message));
      button.textContent = "Try again";
    } finally {
      button.disabled = false;
      list.setAttribute("aria-busy", "false");
    }
  }

  function synthesisWindow(item) {
    const ranges = item.ranges || item.range_plan || item.evidence_windows;
    if (ranges) return raw(ranges);
    if (item.analysis_range || item.baseline_range) {
      return `analysis ${formatWindow(item.analysis_range)} · baseline ${formatWindow(item.baseline_range)}`;
    }
    const purposes = (item.run_refs || []).map((entry) => entry.purpose).filter(Boolean);
    if (item.cutoff_date || purposes.length) {
      return [
        item.cutoff_date ? `cutoff ${item.cutoff_date}` : null,
        purposes.length ? `evidence roles ${purposes.join(", ")}` : null,
      ].filter(Boolean).join(" · ");
    }
    return "Evidence window is recorded in the synthesis below";
  }

  async function loadSyntheses() {
    const list = $("synthesis-list");
    const button = $("load-syntheses");
    button.disabled = true;
    button.textContent = "Loading…";
    list.setAttribute("aria-busy", "true");
    list.replaceChildren(el("p", "muted note-txt m0", "Loading synthesis history…"));
    try {
      const result = (await requestJSON("/api/insights/syntheses?limit=3")).result;
      const items = result.syntheses || [];
      $("synthesis-meta").textContent = `${items.length} shown`;
      list.replaceChildren();
      if (!items.length) {
        list.appendChild(el("p", "muted note-txt m0", "No synthesis has been recorded yet."));
        button.textContent = "Refresh";
        return;
      }
      items.forEach((item) => {
        const card = el("article", "history-card");
        card.append(
          el("div", "label", `${human(item.cadence || item.run_kind || "Synthesis")} · ${item.created_at || "—"}`),
          el("div", "muted micro mt-1", synthesisWindow(item)),
          el("pre", "brief mt-2", item.rendered_md || item.narrative_md || "No owner-facing summary was produced.")
        );
        list.appendChild(card);
      });
      button.textContent = "Refresh";
    } catch (error) {
      list.replaceChildren(el("p", "muted note-txt m0", "Synthesis history unavailable: " + error.message));
      button.textContent = "Try again";
    } finally {
      button.disabled = false;
      list.setAttribute("aria-busy", "false");
    }
  }

  async function loadRunAudit() {
    const rootNode = $("run-audit");
    const button = $("load-run-audit");
    button.disabled = true;
    button.textContent = "Loading…";
    rootNode.setAttribute("aria-busy", "true");
    rootNode.replaceChildren(el("p", "muted note-txt m0", "Loading audit history…"));
    try {
      const result = (await requestJSON("/api/insights/runs?limit=10")).result;
      rootNode.replaceChildren();
      const sections = [
        ["Runs", result.batches || []],
        ["Triggers", result.triggers || []],
        ["Notifications", result.notifications || []],
      ];
      sections.forEach(([label, items]) => {
        const block = el("div", "audit-block");
        block.appendChild(el("div", "label", label));
        if (!items.length) {
          block.appendChild(el("p", "muted micro m0 mt-1", "None recorded."));
        } else {
          items.slice(0, 5).forEach((item) => block.appendChild(
            el("div", "audit-row", raw(item))
          ));
        }
        rootNode.appendChild(block);
      });
      button.textContent = "Refresh";
    } catch (error) {
      rootNode.replaceChildren(el("p", "muted note-txt m0", "Run audit unavailable: " + error.message));
      button.textContent = "Try again";
    } finally {
      button.disabled = false;
      rootNode.setAttribute("aria-busy", "false");
    }
  }

  $("ins-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("ins-input");
    const message = input.value.trim();
    if (!message) return;
    input.value = "";
    sendMessage(message);
  });

  $("ins-conversation").addEventListener("change", async (event) => {
    const generation = beginContextChange("Opening conversation…");
    try {
      if (!await reloadConversation(event.target.value, generation)) return;
      markAnalysisIdle("Conversation opened. Analyze its window when you are ready.");
    } catch (error) {
      if (generation !== contextGeneration) return;
      $("ins-scope-status").textContent = "Could not open conversation: " + error.message;
    } finally {
      finishContextChange(generation);
    }
  });

  $("ins-new-conversation").addEventListener("click", async () => {
    $("ins-new-conversation").disabled = true;
    const generation = beginContextChange("Starting a separate investigation…");
    try {
      const range = canonicalControl ? canonicalControl.range : { kind: "all" };
      const created = await mutate("/api/chat/conversations", "POST", {
        lens: $("ins-new-lens").value,
        context: { version: 1, range, selected_region_ids: [] },
      });
      if (generation !== contextGeneration) return;
      conversations.unshift(created.conversation);
      if (!await reloadConversation(created.conversation.id, generation)) return;
      markAnalysisIdle("New conversation ready. Analysis has not started.");
    } catch (error) {
      if (generation !== contextGeneration) return;
      $("ins-scope-status").textContent = "Could not start a conversation: " + error.message;
    } finally {
      $("ins-new-conversation").disabled = false;
      finishContextChange(generation);
    }
  });

  $("ins-granularity").addEventListener("change", (event) => {
    setCanonicalWindow(event.target.value, 0, false);
  });
  $("ins-range-prev").addEventListener("click", () => {
    setCanonicalWindow($("ins-granularity").value, -1, true);
  });
  $("ins-range-next").addEventListener("click", () => {
    setCanonicalWindow($("ins-granularity").value, 1, true);
  });
  $("ins-range-today").addEventListener("click", () => {
    setCanonicalWindow($("ins-granularity").value, 0, false);
  });
  $("ins-analyze").addEventListener("click", () => loadScopedAnalysis());
  $("load-hypotheses").addEventListener("click", loadHypotheses);
  $("load-syntheses").addEventListener("click", loadSyntheses);
  $("load-run-audit").addEventListener("click", loadRunAudit);

  (async function boot() {
    try {
      await loadConversations();
      markAnalysisIdle();
      if (window.HermesAnalysisJobs.remembered(analysisStorageKey)) {
        await loadScopedAnalysis(true);
      }
    } catch (error) {
      $("ins-scope-status").textContent = "Insight Explorer could not start: " + error.message;
    }
  })();
})();
