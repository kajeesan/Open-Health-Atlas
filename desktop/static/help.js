'use strict';
(() => {
  let configText = '';
  const status = document.getElementById('connection-status');
  fetch('/desktop/api/workspaces', {credentials: 'same-origin'})
    .then(response => { if (!response.ok) throw new Error(); return response.json(); })
    .then(data => {
      if (!data.current) return;
      document.getElementById('connection-workspace').textContent = 'Connect to: ' + data.current.label + ' · ' + data.current.timezone + (data.current.fictional ? ' · fictional records only' : ' · personal records');
      document.getElementById('connection-controls').hidden = false;
    }).catch(() => { status.textContent = 'Workspace information could not be loaded. Reopen the app to try again.'; });
  async function loadConfig() {
    if (configText) return configText;
    const response = await fetch('/desktop/api/mcp-config', {credentials: 'same-origin'});
    if (!response.ok) throw new Error();
    configText = JSON.stringify(await response.json(), null, 2);
    document.getElementById('mcp-config').textContent = configText;
    return configText;
  }
  document.querySelector('#connection-controls details').addEventListener('toggle', event => {
    if (event.target.open) loadConfig().catch(() => { status.textContent = 'Connection settings could not be loaded. Try again after reopening the app.'; });
  });
  document.getElementById('copy-mcp').addEventListener('click', async () => {
    try {
      const value = await loadConfig();
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value);
      else {
        const text = document.createElement('textarea');
        text.value = value;
        text.setAttribute('aria-label', 'Connection settings');
        document.body.appendChild(text);
        text.select();
        const copied = document.execCommand('copy');
        text.remove();
        if (!copied) throw new Error();
      }
      status.textContent = 'Copied. Paste these settings into your compatible AI client.';
    } catch (_) { status.textContent = 'Copy was unavailable. Use Download settings or select the text under View connection settings.'; }
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
