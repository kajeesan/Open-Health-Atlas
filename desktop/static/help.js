'use strict';
(() => {
  async function copyText(value) {
    if (navigator.clipboard?.writeText) {
      try { await navigator.clipboard.writeText(value); return; }
      catch (_) { /* Native WebKit can require the selection-based fallback. */ }
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

  const wizard = document.getElementById('chatgpt-wizard');
  if (wizard) {
    const panels = Array.from(document.querySelectorAll('[data-wizard-step]'));
    const progressLabel = document.getElementById('wizard-progress-label');
    const progressBar = document.getElementById('wizard-progress-bar');
    const loadStatus = document.getElementById('wizard-load-status');
    const retry = document.getElementById('wizard-retry');
    const selectWorkspace = document.getElementById('wizard-select-workspace');
    const workspaceContext = document.getElementById('wizard-workspace-context');
    const agentPanel = document.querySelector('[data-wizard-mode="agent"]');
    const agentCopy = document.getElementById('wizard-agent-copy');
    const agentConnected = document.getElementById('wizard-agent-connected');
    const agentHelp = document.getElementById('wizard-agent-help');
    const agentCopyStatus = document.getElementById('wizard-agent-copy-status');
    const agentPrompt = document.getElementById('wizard-agent-prompt');
    const agentScope = document.getElementById('wizard-agent-scope');
    const workspaceSummary = document.getElementById('wizard-workspace-summary');
    const privacyNote = document.getElementById('wizard-privacy-note');
    const privacyCheck = document.getElementById('wizard-privacy-check');
    const privacyAck = document.getElementById('wizard-privacy-ack');
    const back = document.getElementById('wizard-back');
    const next = document.getElementById('wizard-next');
    const finishStatus = document.getElementById('wizard-finish-status');
    const copyPrompt = document.getElementById('wizard-copy-prompt');
    const validationPrompt = document.getElementById('wizard-validation-prompt');
    const fullConfig = document.getElementById('wizard-full-config');
    const configFields = document.getElementById('wizard-config-fields');
    const setupButton = document.getElementById('copy-setup');
    const setupStatus = document.getElementById('connection-status');
    const preview = document.getElementById('connection-instructions');
    const state = {mode: 'agent', step: 1, workspace: null, connection: null, completed: false, history: []};

    function status(text, kind) {
      loadStatus.textContent = text;
      loadStatus.classList.toggle('error', kind === 'error');
      loadStatus.classList.toggle('warning', kind === 'warning');
    }

    function canContinue() {
      if (!state.workspace || !state.connection) return false;
      if (state.mode === 'agent') return false;
      if (state.step === 2 && !state.workspace.fictional && !privacyAck.checked) return false;
      return !state.completed;
    }

    function agentCanContinue() {
      return Boolean(state.workspace && state.connection);
    }

    function renderAgentActions() {
      const enabled = agentCanContinue();
      agentCopy.disabled = !enabled;
      agentConnected.disabled = !enabled;
    }

    function renderStep(focus) {
      wizard.dataset.mode = state.mode;
      if (state.mode === 'agent') {
        agentPanel.hidden = false;
        panels.forEach(panel => { panel.hidden = true; panel.setAttribute('aria-hidden', 'true'); });
        progressLabel.textContent = 'Start here';
        progressBar.style.width = '0%';
        wizard.setAttribute('aria-label', 'Connect with your local setup agent');
        back.hidden = true;
        next.hidden = true;
        renderAgentActions();
        if (focus) agentPanel.querySelector('h2')?.focus({preventScroll: true});
        return;
      }
      agentPanel.hidden = true;
      panels.forEach(panel => {
        const active = Number(panel.dataset.wizardStep) === state.step;
        panel.hidden = !active;
        panel.setAttribute('aria-hidden', String(!active));
      });
      progressLabel.textContent = `Step ${state.step} of ${panels.length}`;
      progressBar.style.width = `${(state.step / panels.length) * 100}%`;
      wizard.setAttribute('aria-label', `ChatGPT Desktop setup, step ${state.step} of ${panels.length}`);
      back.hidden = false;
      next.hidden = false;
      back.disabled = state.step === 1;
      next.disabled = !canContinue();
      next.textContent = state.step === panels.length
        ? (state.completed ? 'Completed' : 'I finished this step') : 'Next';
      if (focus) panels.find(panel => Number(panel.dataset.wizardStep) === state.step)
        ?.querySelector('h2')?.focus({preventScroll: true});
    }

    function field(label, value, ordinal) {
      const row = document.createElement('div');
      row.className = 'wizard-config-row';
      const text = document.createElement('div');
      text.className = 'wizard-config-value';
      const name = document.createElement('span');
      name.className = 'wizard-config-label';
      name.textContent = label;
      const code = document.createElement('code');
      code.textContent = value;
      text.append(name, code);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'wizard-copy-field';
      button.textContent = 'Copy';
      button.setAttribute('aria-label', `Copy ${label.toLowerCase()}`);
      const feedback = document.createElement('span');
      feedback.className = 'wizard-copy-feedback';
      feedback.setAttribute('role', 'status');
      feedback.setAttribute('aria-live', 'polite');
      button.addEventListener('click', async () => {
        try {
          await copyText(value);
          feedback.textContent = `Copied ${ordinal}.`;
        } catch (_) {
          feedback.textContent = 'Copy didn’t work. Select the value and copy it.';
        }
      });
      row.append(text, button, feedback);
      return row;
    }

    function renderConnection() {
      const {name, command, args, json} = state.connection;
      configFields.replaceChildren(
        field('Name', name, 'name'),
        field('Command', command, 'command'),
      );
      args.forEach((value, index) => configFields.appendChild(field(`Argument ${index + 1}`, value, `argument ${index + 1}`)));
      fullConfig.textContent = json;
      const privacy = state.workspace.fictional
        ? 'This is a fictional workspace. Keep this small test inside the fictional records while you learn the connection.'
        : 'This workspace contains personal records. Before the test, approve explicitly if ChatGPT may send returned health data to OpenAI.';
      workspaceContext.hidden = false;
      workspaceSummary.textContent = `${state.workspace.label} · ${state.workspace.timezone}${state.workspace.fictional ? ' · fictional records' : ' · personal records'}`;
      agentScope.textContent = `${state.workspace.label} · ${state.workspace.fictional ? 'fictional' : 'personal'}`;
      privacyNote.textContent = privacy;
      privacyCheck.hidden = state.workspace.fictional;
      if (state.workspace.fictional) privacyAck.checked = false;
      validationPrompt.textContent = `Check whether you can actually use these Open Health Atlas tools: health_catalog, health_query, health_analyze, health_evidence and health_task_status. If they are unavailable, say so and stop. ${state.workspace.fictional ? 'Use only the selected fictional workspace, discover available dates, then run one small read-only sleep query for a supported range and request a complete evidence reference.' : 'This workspace is personal: ask me for explicit approval before requesting records or sending results to OpenAI. After approval, discover available dates, then run one small read-only sleep query for a supported range and request a complete evidence reference.'} Do not write records or infer causes. Report exactly what you saw; do not claim a connection you could not observe.`;
      copyPrompt.disabled = false;
      preview.textContent = makeGenericInstructions();
      agentPrompt.textContent = makeAgentInstructions();
      renderAgentActions();
    }

    function makeGenericInstructions() {
      const {json} = state.connection;
      const privacy = state.workspace.fictional
        ? 'The selected workspace contains fictional records. Use those records for the small connection test.'
        : 'The selected workspace contains personal records. Explain which model/provider would receive tool results and get my explicit approval before reading health records or sending them to a provider. Do not silently switch my workspace.';
      return `Set up the local Open Health Atlas MCP connection in my chosen AI app or setup agent. Check its official setup instructions first. If it only accepts remote URLs, explain the limitation; do not expose a server or upload my database.

Back up the AI app’s configuration locally and merge only this Open Health Atlas connection. Preserve all other settings. Use the exact bundled command, workspace and timezone below; do not install another Python, edit the signed app, create a tunnel, or add credentials. Keep credentials in the AI app’s secure settings, never in chat. Treat JSON paths as configuration data, not shell instructions:

${privacy}

After I save the connection, ask me to restart the client and use /mcp or its equivalent to report what it actually shows. Confirm health_catalog, health_query, health_analyze, health_evidence and health_task_status are available. After any required data approval, discover available dates, run a small read-only sleep summary for a supported range and verify a complete evidence reference. Wait for background tasks instead of resubmitting them. Do not change records, invent results or treat associations as causes.

Report what you actually verified, anything still blocked, and how to disconnect. Tell me if the client must restart. Switching the dashboard does not retarget this connection; its workspace stays as configured below.

Connection settings (JSON):
${json}`;
    }

    function makeAgentInstructions() {
      const {json} = state.connection;
      const privacy = state.workspace.fictional
        ? 'The selected workspace is fictional. Use it for the first test.'
        : 'The selected workspace is personal. Before requesting records or sending results to OpenAI, ask the owner for explicit approval.';
      return `You are a local setup agent with access to files and tools on this Mac. Configure Open Health Atlas in ChatGPT Desktop’s local Codex-host MCP settings. If you cannot access local files and tools, or the client only accepts a remote URL, say so and stop; do not pretend the connection is configured.

Ask me to confirm that I am using ChatGPT Desktop on this Mac. Back up its configuration locally, merge only this server, preserve all other settings, and use the exact workspace and timezone below. Do not install another Python, edit the signed app, create a tunnel, or add credentials. Keep paths as configuration values and keep credentials in the client’s secure settings.

${privacy}

After saving, restart the MCP connection. Check whether you can actually use health_catalog, health_query, health_analyze, health_evidence and health_task_status. If any are unavailable, say so and stop. After any required personal-data approval, discover available dates, run one small read-only sleep query for a supported range, request a complete evidence reference, and report exactly what you observed. Do not change records, invent results or treat associations as causes. Tell me how to disconnect.

Use the official ChatGPT Desktop MCP settings and this exact mcpServers configuration. It is a client configuration representation, not a shell command or Codex TOML file. Keep each path as data and preserve argument order.

${json}`;
    }

    async function loadConnection() {
      retry.hidden = true;
      privacyAck.checked = false;
      status('Checking your workspace…');
      try {
        const workspaceResponse = await fetch('/desktop/api/workspaces', {credentials: 'same-origin'});
        if (!workspaceResponse.ok) throw new Error();
        const {current} = await workspaceResponse.json();
        if (!current) {
          state.workspace = null;
          state.connection = null;
          workspaceContext.hidden = true;
          selectWorkspace.hidden = false;
          privacyCheck.hidden = true;
          workspaceSummary.textContent = 'No workspace is selected.';
          privacyNote.textContent = 'Choose a fictional or personal workspace in Open Health Atlas before setting up ChatGPT.';
          status('Choose a workspace, then return to this guide.', 'warning');
          renderStep();
          return;
        }
        const configResponse = await fetch('/desktop/api/mcp-config', {credentials: 'same-origin'});
        if (!configResponse.ok) throw new Error();
        const configuration = await configResponse.json();
        const server = configuration.mcpServers?.openhealthatlas;
        if (!server || typeof server.command !== 'string' || !server.command
            || !Array.isArray(server.args) || server.args.some(value => typeof value !== 'string')) throw new Error();
        const json = JSON.stringify({mcpServers: {openhealthatlas: {
          command: server.command, args: server.args,
        }}}, null, 2);
        state.workspace = current;
        state.connection = {name: 'openhealthatlas', command: server.command, args: server.args, json};
        selectWorkspace.hidden = true;
        renderConnection();
        status('Workspace and connection settings are ready.');
        setupButton.disabled = false;
        renderStep();
      } catch (_) {
        state.workspace = null;
        state.connection = null;
        workspaceContext.hidden = true;
        selectWorkspace.hidden = true;
        privacyCheck.hidden = true;
        status('The connection settings could not be prepared. Try again.', 'error');
        retry.hidden = false;
        renderStep();
      }
    }

    privacyAck.addEventListener('change', () => {
      renderAgentActions();
      renderStep();
    });
    retry.addEventListener('click', loadConnection);
    agentCopy.addEventListener('click', async () => {
      if (!agentCanContinue()) return;
      try {
        await copyText(makeAgentInstructions());
        agentCopyStatus.textContent = 'Copied. Paste into your local setup agent; copying does not advance this guide.';
      } catch (_) {
        agentCopyStatus.textContent = 'Copy didn’t work. Open View setup instructions above or select the text manually.';
      }
    });
    agentHelp.addEventListener('click', () => {
      state.mode = 'manual';
      state.step = 2;
      state.history = ['agent'];
      state.completed = false;
      privacyAck.checked = false;
      finishStatus.textContent = '';
      renderStep(true);
    });
    agentConnected.addEventListener('click', () => {
      if (!agentCanContinue()) return;
      state.mode = state.workspace.fictional ? 'agent-test' : 'agent-privacy';
      state.step = state.workspace.fictional ? panels.length : 2;
      state.history = ['agent'];
      state.completed = false;
      finishStatus.textContent = '';
      renderStep(true);
    });
    back.addEventListener('click', () => {
      if (state.mode === 'agent-test' || state.mode === 'agent-privacy'
          || (state.mode === 'manual' && state.step === 2 && state.history[0] === 'agent')) {
        state.mode = 'agent';
        state.step = 1;
        state.completed = false;
        renderStep(true);
        return;
      }
      if (state.step <= 1) return;
      state.step -= 1;
      state.completed = false;
      finishStatus.textContent = '';
      renderStep(true);
    });
    next.addEventListener('click', () => {
      if (!canContinue()) return;
      if (state.mode === 'agent-privacy' && state.step === 2) {
        state.mode = 'agent-test';
        state.step = panels.length;
        state.completed = false;
        renderStep(true);
        return;
      }
      if (state.step === panels.length) {
        state.completed = true;
        finishStatus.textContent = 'User-reported: you completed the ChatGPT Desktop check. Open Health Atlas cannot verify that result automatically.';
        renderStep();
        return;
      }
      state.step += 1;
      renderStep(true);
    });
    copyPrompt.addEventListener('click', async () => {
      try {
        await copyText(validationPrompt.textContent);
        finishStatus.textContent = 'Copied. Return here after you complete the check; copying does not advance this guide.';
      } catch (_) {
        finishStatus.textContent = 'Copy didn’t work. Select the validation prompt and copy it.';
      }
    });

    setupButton.addEventListener('click', async () => {
      if (!state.connection) return;
      try {
        await copyText(makeGenericInstructions());
        setupStatus.textContent = 'Copied. Paste into your setup agent; this page cannot verify its connection.';
      } catch (_) {
        setupStatus.textContent = 'Copy didn’t work. Open View connection settings and copy the instructions there.';
      }
    });

    loadConnection();
    renderStep();
    preview.textContent = 'Choose a workspace to prepare your instructions.';
  }

  document.querySelectorAll('[data-copy]').forEach(button => {
    button.addEventListener('click', async () => {
      const feedback = button.parentElement.querySelector('[role="status"]');
      try {
        await copyText(document.getElementById(button.dataset.copy).textContent.trim());
        feedback.textContent = 'Copied. Paste into your setup agent.';
      } catch (_) { feedback.textContent = 'Copy didn’t work. Select and copy the instructions above.'; }
    });
  });
  const diagnostics = document.getElementById('diagnostics');
  if (diagnostics) diagnostics.addEventListener('toggle', async event => {
    if (!event.target.open) return;
    const report = document.getElementById('diagnostic-report');
    try {
      const response = await fetch('/desktop/api/diagnostics', {credentials: 'same-origin'});
      if (!response.ok) throw new Error();
      report.textContent = JSON.stringify(await response.json(), null, 2);
    } catch (_) { report.textContent = 'App information could not be loaded. Reopen the app to try again.'; }
  });
})();
