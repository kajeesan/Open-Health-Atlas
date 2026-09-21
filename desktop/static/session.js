'use strict';
(() => {
  const button = document.getElementById('reopen-workspace');
  const status = document.getElementById('session-status');
  button.addEventListener('click', () => {
    const handler = window.webkit?.messageHandlers?.oha;
    if (!handler) {
      status.textContent = 'Quit and reopen Open Health Atlas to open your workspace.';
      return;
    }
    button.disabled = true;
    status.textContent = 'Opening your workspace…';
    handler.postMessage({action: 'reopenWorkspace'});
  });
})();
