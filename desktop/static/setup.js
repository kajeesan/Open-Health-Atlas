'use strict';
(() => {
  const form = document.getElementById('workspace-form');
  if (!form) return;
  const status = document.getElementById('setup-status');
  const submit = document.getElementById('open-workspace');
  const timezone = document.getElementById('timezone');
  const importDetails = document.getElementById('import-details');
  const filePath = document.getElementById('source-database');
  let busy = false;

  const setStatus = (message, error = false) => {
    status.textContent = message;
    status.classList.toggle('error', error);
  };
  const setBusy = (value) => {
    busy = value;
    form.setAttribute('aria-busy', String(value));
    document.querySelectorAll('button, input').forEach(input => { input.disabled = value; });
  };
  const post = async (url, data) => {
    const response = await fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content},
      body: JSON.stringify(data),
    });
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw new Error(payload.error || 'Setup could not finish. Try again or open Help.');
    return payload;
  };
  const open = async (url, data) => {
    if (busy) return;
    setBusy(true);
    setStatus(data.kind === 'demo' ? 'Preparing your fictional example…' : 'Preparing your workspace…');
    try {
      const result = await post(url, data);
      setStatus('Opening your workspace…');
      if (!result.restarting) window.location.assign('/');
    } catch (error) {
      setBusy(false);
      setStatus(error.message || 'The app lost its connection. Quit and reopen it to try again.', true);
    }
  };

  try { timezone.value = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch (_) { /* UTC stays explicit. */ }
  form.addEventListener('change', () => {
    const kind = form.elements.kind.value;
    importDetails.hidden = kind !== 'import';
    submit.textContent = {demo: 'Explore fictional data', personal: 'Create my workspace', import: 'Copy and open my data'}[kind];
  });
  document.getElementById('choose-database').addEventListener('click', () => {
    const handler = window.webkit?.messageHandlers?.oha;
    if (handler) handler.postMessage({action: 'chooseDatabase'});
    else setStatus('File selection is available in the Open Health Atlas app window.', true);
  });
  window.addEventListener('oha-database-selected', event => {
    if (typeof event.detail?.path === 'string') {
      filePath.value = event.detail.path;
      setStatus('The original file will stay unchanged.');
    }
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    const kind = form.elements.kind.value;
    if (kind === 'import' && !filePath.value) {
      setStatus('Choose your Open Health Atlas database first.', true);
      document.getElementById('choose-database').focus();
      return;
    }
    open('/desktop/api/workspaces', {kind, timezone: timezone.value.trim(), ...(kind === 'import' ? {source_database: filePath.value} : {})});
  });
  fetch('/desktop/api/workspaces', {credentials: 'same-origin'})
    .then(response => { if (!response.ok) throw new Error(); return response.json(); })
    .then(data => {
      if (!data.workspaces?.length) return;
      document.getElementById('existing-workspaces').hidden = false;
      const list = document.getElementById('workspace-list');
      data.workspaces.forEach(workspace => {
        if (workspace.unavailable) {
          const notice = document.createElement('p');
          notice.textContent = workspace.label + '. Open Help for recovery guidance.';
          list.appendChild(notice);
          return;
        }
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = workspace.label + (workspace.fictional ? ' · fictional' : '') + ' · ' + workspace.timezone;
        button.addEventListener('click', () => open('/desktop/api/select', {id: workspace.id}));
        list.appendChild(button);
      });
    }).catch(() => setStatus('Existing workspaces could not be listed. Reopen the app before creating another workspace.', true));
})();
