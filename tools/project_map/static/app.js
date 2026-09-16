"use strict";

const state = {
  data: null,
  viewId: null,
  selectedNodeId: null,
};

const elements = {
  tabs: document.getElementById("view-tabs"),
  panel: document.getElementById("view-panel"),
  title: document.getElementById("view-title"),
  summary: document.getElementById("view-summary"),
  priority: document.getElementById("view-priority"),
  canvas: document.getElementById("map-canvas"),
  statusKey: document.getElementById("status-key-items"),
  auditState: document.getElementById("audit-state"),
  detailEmpty: document.getElementById("detail-empty"),
  detailContent: document.getElementById("detail-content"),
  toast: document.getElementById("toast"),
};

const statusClass = {
  "Working": "working",
  "Broken": "broken",
  "Missing": "missing",
  "Partially built": "partial",
  "Test-only": "test",
  "Synthetic-only": "synthetic",
  "Duplicated": "duplicated",
  "Unverified": "unverified",
};

function escapeHTML(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function statusBadge(status) {
  const kind = statusClass[status] || "test";
  return `<span class="status-badge status-${kind}">${escapeHTML(status)}</span>`;
}

function currentView() {
  return state.data.views.find((view) => view.id === state.viewId);
}

function nodeById(nodeId) {
  return state.data.nodes[nodeId];
}

function setSourceState() {
  const context = state.data.liveContext;
  const auditChecks = Object.values(context.auditFiles || {});
  const allAudits = auditChecks.length === 6 && auditChecks.every(Boolean);
  const graphReady = Boolean(context.graphify?.available);
  const coverage = context.coverage;

  elements.auditState.classList.remove("is-ready", "is-warning");
  if (allAudits && graphReady && coverage?.rows === 120) {
    elements.auditState.textContent = "6 audit files + Graphify navigation snapshot loaded";
    elements.auditState.classList.add("is-ready");
  } else {
    const unavailable = [];
    if (!allAudits) unavailable.push("audit files");
    if (!graphReady) unavailable.push("Graphify data");
    elements.auditState.textContent = `${unavailable.join(" and ")} unavailable; showing embedded map`;
    elements.auditState.classList.add("is-warning");
  }
}

function renderStatusKey() {
  elements.statusKey.innerHTML = state.data.statuses
    .map((status) => statusBadge(status))
    .join("");
}

function renderTabs() {
  elements.tabs.innerHTML = state.data.views
    .map((view) => {
      const selected = view.id === state.viewId;
      return `
        <button id="view-tab-${escapeHTML(view.id)}" class="view-tab" type="button" role="tab"
          aria-controls="view-panel" aria-selected="${selected}" tabindex="${selected ? "0" : "-1"}"
          data-view-id="${escapeHTML(view.id)}">
          ${escapeHTML(view.label)}
        </button>`;
    })
    .join("");

  elements.tabs.querySelectorAll("[data-view-id]").forEach((button) => {
    button.addEventListener("click", () => selectView(button.dataset.viewId, true));
    button.addEventListener("keydown", handleTabKeydown);
  });
  elements.panel.setAttribute("aria-labelledby", `view-tab-${state.viewId}`);
}

function handleTabKeydown(event) {
  const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
  if (!keys.includes(event.key)) return;
  event.preventDefault();
  const currentIndex = state.data.views.findIndex((view) => view.id === state.viewId);
  let nextIndex = currentIndex;
  if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + state.data.views.length) % state.data.views.length;
  if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % state.data.views.length;
  if (event.key === "Home") nextIndex = 0;
  if (event.key === "End") nextIndex = state.data.views.length - 1;
  selectView(state.data.views[nextIndex].id, true);
}

function renderView() {
  const view = currentView();
  elements.title.textContent = view.title;
  elements.summary.textContent = view.summary;
  elements.priority.hidden = !view.priority;
  elements.priority.textContent = view.priority || "";

  const svg = `
    <svg class="edge-layer" viewBox="0 0 1000 650" preserveAspectRatio="none"
      role="img" aria-label="Connections between the components in this view">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5"
          markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"></path>
        </marker>
      </defs>
      <g id="edge-paths"></g>
    </svg>`;

  const nodes = view.nodeIds
    .map((nodeId) => {
      const node = nodeById(nodeId);
      const position = view.positions[nodeId];
      const selected = state.selectedNodeId === nodeId;
      return `
        <button type="button" class="map-node" data-node-id="${escapeHTML(nodeId)}"
          style="left:${position[0]}%;top:${position[1]}%"
          aria-pressed="${selected}"
          aria-label="${escapeHTML(node.label)}, ${escapeHTML(node.status)}">
          <span class="node-label">${escapeHTML(node.label)}</span>
          ${statusBadge(node.status)}
        </button>`;
    })
    .join("");

  const connectionList = `
    <div class="mobile-connection-list" aria-label="Connections">
      <strong>Connections in this view</strong>
      ${view.edges.map((edge) => {
        const from = nodeById(edge.from)?.label || edge.from;
        const to = nodeById(edge.to)?.label || edge.to;
        const note = edge.label ? ` — ${edge.label}` : "";
        return `<span>${escapeHTML(from)} → ${escapeHTML(to)}${escapeHTML(note)}</span>`;
      }).join("")}
    </div>`;

  elements.canvas.innerHTML = svg + nodes + connectionList;
  renderEdges();
  elements.canvas.querySelectorAll("[data-node-id]").forEach((button) => {
    button.addEventListener("click", () => selectNode(button.dataset.nodeId));
  });
}

function renderEdges() {
  const view = currentView();
  const edgeGroup = document.getElementById("edge-paths");
  if (!edgeGroup) return;

  edgeGroup.innerHTML = view.edges.map((edge) => {
    const from = view.positions[edge.from];
    const to = view.positions[edge.to];
    const x1 = from[0] * 10;
    const y1 = from[1] * 6.5;
    const x2 = to[0] * 10;
    const y2 = to[1] * 6.5;
    const offset = edge.labelOffset || [0, 0];
    const mx = (x1 + x2) / 2 + offset[0];
    const my = (y1 + y2) / 2 + offset[1];
    const kind = statusClass[edge.status || "Working"] || "test";
    const highlighted = state.selectedNodeId &&
      (edge.from === state.selectedNodeId || edge.to === state.selectedNodeId);
    const pathClass = `edge-path is-${kind}${highlighted ? " is-highlighted" : ""}`;
    const label = edge.label || "";
    const labelWidth = Math.min(260, Math.max(90, label.length * 6.2 + 20));
    const labelMarkup = label ? `
      <g class="edge-label${edge.stop ? " is-stop" : ""}"
        transform="translate(${mx - labelWidth / 2} ${my - 13})">
        <rect width="${labelWidth}" height="26"></rect>
        <text x="${labelWidth / 2}" y="17" text-anchor="middle">${escapeHTML(label)}</text>
      </g>` : "";
    return `
      <path class="${pathClass}" d="M ${x1} ${y1} L ${x2} ${y2}"
        marker-end="url(#arrow)"></path>
      ${labelMarkup}`;
  }).join("");
}

function selectView(viewId, focusTab = false) {
  if (viewId === state.viewId) return;
  state.viewId = viewId;
  state.selectedNodeId = null;
  renderTabs();
  renderView();
  resetDetail();
  if (focusTab) {
    elements.tabs.querySelector(`[data-view-id="${viewId}"]`).focus();
  }
}

function selectNode(nodeId) {
  state.selectedNodeId = nodeId;
  renderView();
  renderDetail(nodeId);
  elements.detailContent.focus({ preventScroll: true });
  if (window.innerWidth <= 820) {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    elements.detailContent.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  }
}

function graphifyEvidence(nodeId) {
  const graph = state.data.liveContext.graphify;
  const matches = graph?.matches?.[nodeId] || [];
  if (!graph?.available || matches.length === 0) return "";

  const sources = matches.map((match) => {
    const examples = match.nodes
      .map((node) => `${node.label}${node.location ? ` (${node.location})` : ""}`)
      .join(", ");
    return `<li class="evidence-item">
      <span class="evidence-kind">Graphify</span>
      <span class="evidence-ref">${escapeHTML(match.source)}</span>
      <span class="evidence-note">Matched graph nodes: ${escapeHTML(examples)}.</span>
    </li>`;
  }).join("");

  return `${sources}<li class="graphify-note">
    Graphify snapshot: ${escapeHTML(graph.snapshot)}. ${escapeHTML(graph.warning)}
  </li>`;
}

function renderDetail(nodeId) {
  const node = nodeById(nodeId);
  const view = currentView();
  const evidence = node.evidence.map((item) => `
    <li class="evidence-item">
      <span class="evidence-kind">${escapeHTML(item.kind)}</span>
      <span class="evidence-ref">${escapeHTML(item.ref)}</span>
      <span class="evidence-note">${escapeHTML(item.note)}</span>
    </li>`).join("");

  elements.detailEmpty.hidden = true;
  elements.detailContent.hidden = false;
  elements.detailContent.setAttribute("aria-labelledby", "detail-heading");
  elements.detailContent.innerHTML = `
    <div class="detail-inner">
      <div class="detail-heading">
        <div>
          ${statusBadge(node.status)}
          <h2 id="detail-heading">${escapeHTML(node.label)}</h2>
        </div>
        <button type="button" class="detail-close" aria-label="Close details">Close</button>
      </div>
      <section class="detail-section">
        <h3>What this means</h3>
        <p>${escapeHTML(node.explanation)}</p>
      </section>
      <section class="detail-section">
        <h3>Why it matters</h3>
        <p>${escapeHTML(node.why)}</p>
      </section>
      <section class="detail-section">
        <h3>Evidence</h3>
        <ul class="evidence-list">
          ${evidence}
          ${graphifyEvidence(nodeId)}
        </ul>
      </section>
      <label class="question-label" for="focused-question" hidden>Focused Codex question</label>
      <textarea id="focused-question" class="question-output" rows="9" readonly hidden></textarea>
      <button type="button" class="ask-action">Ask about this</button>
    </div>`;

  elements.detailContent.querySelector(".detail-close").addEventListener("click", closeDetail);
  elements.detailContent.querySelector(".ask-action").addEventListener("click", () => {
    copyQuestion(node, view);
  });
}

function resetDetail() {
  elements.detailContent.hidden = true;
  elements.detailContent.innerHTML = "";
  elements.detailContent.removeAttribute("aria-labelledby");
  elements.detailEmpty.hidden = false;
}

function closeDetail() {
  const nodeId = state.selectedNodeId;
  state.selectedNodeId = null;
  resetDetail();
  if (state.data) {
    renderView();
    const restoredNode = elements.canvas.querySelector(`[data-node-id="${nodeId}"]`);
    if (restoredNode) restoredNode.focus();
  }
}

function buildQuestion(node, view) {
  const evidence = node.evidence
    .map((item) => `- [${item.kind}] ${item.ref}: ${item.note}`)
    .join("\n");
  const graph = state.data.liveContext.graphify;
  const graphLine = graph?.available
    ? `\nGraphify context: ${graph.snapshot}; ${graph.warning}`
    : "";

  return `Help me understand this OpenHealthAtlas project-map item.\n\n` +
    `View: ${view.label}\n` +
    `Item: ${node.label}\n` +
    `Status: ${node.status}\n\n` +
    `Explanation: ${node.explanation}\n\n` +
    `Why it matters: ${node.why}\n\n` +
    `Evidence:\n${evidence}${graphLine}\n\n` +
    `Please explain what this evidence establishes, what it does not establish, ` +
    `and the smallest next verification or decision. Do not treat code presence, ` +
    `Graphify edges, or passing unit tests as end-to-end product proof.`;
}

async function copyQuestion(node, view) {
  const question = buildQuestion(node, view);
  const output = elements.detailContent.querySelector(".question-output");
  elements.detailContent.querySelector(".question-label").hidden = false;
  output.value = question;
  output.hidden = false;
  let copied = false;
  try {
    await navigator.clipboard.writeText(question);
    copied = true;
  } catch (error) {
    const textarea = document.createElement("textarea");
    textarea.value = question;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "absolute";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    copied = document.execCommand("copy");
    textarea.remove();
  }
  if (copied) {
    showToast("Focused question copied. Paste it into Codex.");
  } else {
    output.focus();
    output.select();
    showToast("Copy was unavailable. The complete question is selected below.");
  }
}

let toastTimer;
function showToast(message) {
  window.clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  toastTimer = window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 2800);
}

function showLoadError() {
  elements.canvas.innerHTML = `
    <div class="error-state">
      <strong>The project map could not load.</strong>
      <span>Check that the Flask server can read tools/project_map/project_map.json.</span>
    </div>`;
  elements.auditState.textContent = "Map data unavailable";
  elements.auditState.classList.add("is-warning");
}

async function init() {
  try {
    const response = await fetch("/api/map", { headers: { "Accept": "application/json" } });
    if (!response.ok) throw new Error(`Map API returned ${response.status}`);
    state.data = await response.json();
    state.viewId = state.data.defaultView;
    setSourceState();
    renderStatusKey();
    renderTabs();
    renderView();
  } catch (error) {
    console.error(error);
    showLoadError();
  }
}

init();
