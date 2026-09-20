'use strict';
(() => {
  let configText = '';
  const status = document.getElementById('connection-status');
  fetch('/desktop/api/workspaces', {credentials: 'same-origin'})
    .then(response => { if (!response.ok) throw new Error(); return response.json(); })
    .then(data => {
      const label = document.getElementById('connection-workspace');
      if (!data.current) {
        label.textContent = 'No workspace is open yet. Choose one to get started.';
        return;
      }
      label.textContent = data.current.label + ' · ' + data.current.timezone + (data.current.fictional ? ' · fictional records' : ' · personal records');
      document.getElementById('connection-controls').hidden = false;
    }).catch(() => {
      document.getElementById('connection-workspace').textContent = 'Your workspace could not be checked.';
      status.textContent = 'Reopen the app, then return here to get your connection settings.';
    });
  async function loadConfig() {
    if (configText) return configText;
    const response = await fetch('/desktop/api/mcp-config', {credentials: 'same-origin'});
    if (!response.ok) throw new Error();
    configText = JSON.stringify(await response.json(), null, 2);
    document.getElementById('mcp-config').textContent = configText;
    return configText;
  }
  async function copyText(value) {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(value);
        return;
      } catch (_) { /* Native WebKit may deny clipboard access; retain text selection. */ }
    }
    const text = document.createElement('textarea');
    text.value = value;
    text.setAttribute('aria-label', 'Text to copy');
    const focused = document.activeElement;
    document.body.appendChild(text);
    try {
      text.select();
      if (!document.execCommand('copy')) throw new Error();
    } finally {
      text.remove();
      focused?.focus({preventScroll: true});
    }
  }
  document.getElementById('connection-details').addEventListener('toggle', event => {
    if (event.target.open) loadConfig().catch(() => {
      document.getElementById('mcp-config').textContent = 'Settings could not be loaded. Reopen the app and try again.';
      status.textContent = 'Settings are unavailable. Reopen the app and try again.';
    });
  });
  document.getElementById('copy-mcp').addEventListener('click', async () => {
    try {
      await copyText(await loadConfig());
      status.textContent = 'Settings copied. Add them in your AI app’s local MCP settings.';
    } catch (_) { status.textContent = 'Copy didn’t work. Try Download settings, or select the text under View connection settings.'; }
  });
  document.querySelectorAll('[data-copy]').forEach(button => {
    button.addEventListener('click', async () => {
      const feedback = button.parentElement.querySelector('[role="status"]');
      try {
        await copyText(document.getElementById(button.dataset.copy).textContent.trim());
        feedback.textContent = 'Copied. Ready to paste into your AI app.';
      } catch (_) { feedback.textContent = 'Copy didn’t work. Select and copy the text above.'; }
    });
  });
  document.getElementById('diagnostics').addEventListener('toggle', async event => {
    if (!event.target.open) return;
    const report = document.getElementById('diagnostic-report');
    try {
      const response = await fetch('/desktop/api/diagnostics', {credentials: 'same-origin'});
      if (!response.ok) throw new Error();
      report.textContent = JSON.stringify(await response.json(), null, 2);
    } catch (_) { report.textContent = 'App information could not be loaded. Reopen the app to try again.'; }
  });
})();
