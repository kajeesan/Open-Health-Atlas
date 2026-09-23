"""Focused contract checks for the ChatGPT Desktop setup wizard."""
import json
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

from desktop.server import build_app
from desktop.workspaces import WorkspaceManager


ROOT = Path(__file__).resolve().parents[1]


def _client(tmp_path, *, kind="demo"):
    data_root = tmp_path / "Open Health Atlas test data"
    manager = WorkspaceManager(data_root, ROOT, "a" * 40)
    workspace = manager.create(kind, "Europe/Paris")
    app = build_app(manager, workspace, tmp_path / "wizard.sock", "wizard-launch",
                    threading.Event(), "a" * 40)
    app.config["RATELIMIT_ENABLED"] = False
    client = app.test_client()
    assert client.post("/desktop/session", headers={"X-OHA-Launch-Token": "wizard-launch"}).status_code == 303
    return client, workspace


def test_chatgpt_wizard_preserves_help_route_and_exact_workspace_config(tmp_path, monkeypatch):
    client, workspace = _client(tmp_path)
    page = client.get("/desktop/help").text
    assert page.count('data-wizard-step=') == 6
    assert "Connect ChatGPT Desktop" in page
    assert "Other AI apps / setup agent" in page
    assert "wizard-select-workspace" in page
    assert "https://learn.chatgpt.com/docs/extend/mcp" in page
    assert "Connected successfully" not in page

    executable = tmp_path / "Open Health Atlas.app" / "Contents" / "MacOS" / "openhealthatlas-mcp"
    executable.parent.mkdir(parents=True)
    executable.write_text("fictional executable")
    executable.chmod(0o700)
    monkeypatch.setattr("desktop.mcp_config.bundled_executable", lambda: executable)
    response = client.get("/desktop/api/mcp-config")
    assert response.status_code == 200
    server = response.get_json()["mcpServers"]["openhealthatlas"]
    assert server["command"] == str(executable)
    assert server["args"] == ["--workspace", str(workspace.directory), "--timezone", "Europe/Paris"]
    assert "Open Health Atlas test data" in server["args"][1]


def test_first_run_wizard_exposes_workspace_recovery_link(tmp_path):
    data_root = tmp_path / "empty data"
    manager = WorkspaceManager(data_root, ROOT, "a" * 40)
    app = build_app(manager, None, tmp_path / "wizard.sock", "wizard-launch",
                    threading.Event(), "a" * 40)
    app.config["RATELIMIT_ENABLED"] = False
    client = app.test_client()
    assert client.post("/desktop/session", headers={"X-OHA-Launch-Token": "wizard-launch"}).status_code == 303
    page = client.get("/desktop/help").text
    assert 'id="wizard-select-workspace"' in page
    assert 'href="/desktop/setup"' in page


def test_help_wizard_javascript_advances_without_false_verification(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the browser behavior regression")
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');

class Element {
  constructor(id = '') {
    this.id = id; this.dataset = {}; this.children = []; this.listeners = {};
    this.hidden = false; this.disabled = false; this.checked = false;
    this.style = {}; this.classList = {toggle() {}}; this._text = '';
    this.parentElement = null;
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent || '').join(''); }
  append(...children) { children.forEach(child => { child.parentElement = this; this.children.push(child); }); }
  appendChild(child) { this.append(child); return child; }
  replaceChildren(...children) { this._text = ''; this.children = []; this.append(...children); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  querySelector(selector) {
    if (selector === 'h2') return this.children.find(child => child.tagName === 'H2') || new Element('heading');
    if (selector === 'summary') return this.children.find(child => child.tagName === 'SUMMARY') || new Element('summary');
    if (selector === '[role="status"]') return this.children.find(child => child.role === 'status') || new Element('feedback');
    return null;
  }
  focus() { this.focused = true; }
  select() {}
  remove() {}
}

function deferredTick() { return new Promise(resolve => setTimeout(resolve, 0)); }
async function run(fictional, options = {}) {
  const nodes = new Map();
  const get = id => { if (!nodes.has(id)) nodes.set(id, new Element(id)); return nodes.get(id); };
  get('chatgpt-wizard'); get('wizard-progress-label'); get('wizard-progress-bar');
  get('wizard-load-status'); get('wizard-retry'); get('wizard-select-workspace');
  get('wizard-workspace-summary'); get('wizard-privacy-note'); get('wizard-privacy-check');
  get('wizard-privacy-ack'); get('wizard-back'); get('wizard-next'); get('wizard-finish-status');
  get('wizard-copy-prompt'); get('wizard-validation-prompt'); get('wizard-full-config');
  get('wizard-config-fields'); get('copy-setup'); get('connection-status'); get('connection-instructions');
  get('wizard-workspace-context'); get('wizard-agent-copy'); get('wizard-agent-connected');
  get('wizard-agent-help'); get('wizard-agent-help-panel'); get('wizard-agent-local-settings');
  get('wizard-agent-copy-status'); get('wizard-agent-prompt');
  get('copy-setup').disabled = true; get('wizard-copy-prompt').disabled = true;
  const panels = [];
  for (let n = 1; n <= 6; n++) { const panel = new Element(`step-${n}`); panel.dataset.wizardStep = String(n); panel.tagName = 'SECTION'; panel.children.push(Object.assign(new Element(`heading-${n}`), {tagName: 'H2'})); panels.push(panel); }
  const agentPanel = new Element('agent-panel'); agentPanel.dataset.wizardMode = 'agent';
  get('wizard-agent-help-panel').children.push(Object.assign(new Element('agent-summary'), {tagName: 'SUMMARY'}));
  const copies = [];
  let failConfig = Boolean(options.failConfig);
  const clipboardWorks = options.clipboardWorks !== false;
  const config = {mcpServers: {openhealthatlas: {
    command: '/Applications/Open Health Atlas.app/Contents/MacOS/openhealthatlas-mcp',
    args: ['--workspace', '/private/tmp/openhealthatlas wizard data/workspaces/abc', '--timezone', 'Europe/Paris'],
  }}};
  const document = {
    getElementById: get,
    querySelector: selector => selector === '[data-wizard-mode="agent"]' ? agentPanel : null,
    querySelectorAll: selector => selector === '[data-wizard-step]' ? panels : [],
    createElement: () => new Element('created'),
    body: {appendChild() {}},
    execCommand: () => clipboardWorks,
    activeElement: null,
  };
  const fetch = async path => {
    if (path === '/desktop/api/workspaces') return {ok: true, json: async () => ({current: {label: fictional ? 'Fictional sample' : 'My health data', timezone: 'Europe/Paris', fictional}})};
    if (path === '/desktop/api/mcp-config') {
      if (failConfig) return {ok: false, json: async () => ({})};
      return {ok: true, json: async () => config};
    }
    throw new Error(`unexpected request ${path}`);
  };
  const context = {document, navigator: {clipboard: {writeText: async value => { if (!clipboardWorks) throw new Error(); copies.push(value); }}}, fetch, console, setTimeout, Promise};
  vm.runInNewContext(source, context, {filename: 'help.js'});
  for (let tick = 0; tick < 10; tick++) await deferredTick();
  const next = get('wizard-next'), back = get('wizard-back'), ack = get('wizard-privacy-ack');
  assert.equal(next.hidden, true, 'agent-first view hides the manual Next control');
  assert.equal(get('wizard-agent-connected').disabled, Boolean(options.failConfig));
  if (!options.failConfig) {
    await get('wizard-agent-connected').listeners.click();
    if (fictional) {
      assert.equal(get('wizard-progress-label').textContent, 'Step 6 of 6');
    } else {
      assert.equal(get('wizard-progress-label').textContent, 'Step 2 of 6');
      assert.equal(next.disabled, true);
      ack.checked = true;
      await ack.listeners.change();
      await next.listeners.click();
      assert.equal(get('wizard-progress-label').textContent, 'Step 6 of 6');
    }
    await back.listeners.click();
    assert.equal(get('wizard-progress-label').textContent, 'Start here');
  }
  await get('wizard-agent-help').listeners.click();
  assert.equal(get('wizard-agent-help-panel').hidden, false);
  await get('wizard-agent-local-settings').listeners.click();
  assert.equal(get('wizard-progress-label').textContent, 'Step 2 of 6');
  if (options.failConfig) {
    assert.equal(get('wizard-retry').hidden, false);
    assert.equal(next.disabled, true);
    failConfig = false;
    await get('wizard-retry').listeners.click();
    for (let tick = 0; tick < 10; tick++) await deferredTick();
    assert.equal(get('wizard-retry').hidden, true);
    assert.equal(get('copy-setup').disabled, false);
  }
  if (options.clipboardWorks !== false) {
    await get('copy-setup').listeners.click();
    assert.match(copies.at(-1), /Back up the AI app/);
    assert.match(copies.at(-1), /health_catalog/);
    assert.match(copies.at(-1), /complete evidence reference/);
    assert.match(copies.at(-1), /background tasks/);
    assert.match(copies.at(-1), /how to disconnect/);
  }
  assert.equal(next.disabled, fictional ? false : true, 'personal data must require acknowledgment');
  if (!fictional) { ack.checked = true; await ack.listeners.change(); }
  await next.listeners.click(); await next.listeners.click();
  assert.equal(get('wizard-progress-label').textContent, 'Step 4 of 6');
  const configFields = get('wizard-config-fields');
  const commandCopy = configFields.children[1].children[1];
  await commandCopy.listeners.click();
  if (options.clipboardWorks === false) {
    assert.match(configFields.children[1].children[2].textContent, /Copy didn’t work/);
    assert.match(get('connection-instructions').textContent, /mcpServers/);
    return;
  }
  assert.equal(copies.at(-1), config.mcpServers.openhealthatlas.command);
  const argsCopy = configFields.children[2].children[1];
  await argsCopy.listeners.click();
  assert.equal(copies.at(-1), config.mcpServers.openhealthatlas.args[0]);
  const pathCopy = configFields.children[3].children[1];
  await pathCopy.listeners.click();
  assert.equal(copies.at(-1), config.mcpServers.openhealthatlas.args[1]);
  await next.listeners.click(); await next.listeners.click();
  assert.equal(get('wizard-progress-label').textContent, 'Step 6 of 6');
  await get('wizard-copy-prompt').listeners.click();
  assert.equal(get('wizard-progress-label').textContent, 'Step 6 of 6', 'copy must not auto-advance');
  for (const tool of ['health_catalog', 'health_query', 'health_analyze', 'health_evidence', 'health_task_status']) {
    assert.ok(copies.at(-1).includes(tool), `validation message must name ${tool}`);
  }
  assert.match(copies.at(-1), /stop/i);
  await next.listeners.click();
  assert.match(get('wizard-finish-status').textContent, /^User-reported:/);
  assert.doesNotMatch(get('wizard-finish-status').textContent, /verified automatically/);
  await back.listeners.click();
  assert.equal(get('wizard-progress-label').textContent, 'Step 5 of 6');
  assert.equal(get('wizard-full-config').textContent.includes('openhealthatlas wizard data'), true);
}

async function clipboardFallback() {
  await run(true, {clipboardWorks: false});
}
(async () => { await run(true); await run(false); await run(true, {failConfig: true}); await clipboardFallback(); })().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run([node, "-e", script, str(ROOT / "desktop/static/help.js")],
                   cwd=ROOT, check=True, capture_output=True, text=True, timeout=20)
