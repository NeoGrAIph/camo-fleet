import RFB from 'https://unpkg.com/@novnc/novnc@1.5.0/core/rfb.js';

function byId(id) {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Missing required element #${id}`);
  }
  return element;
}

const statusEl = byId('connection-status');
const screenEl = byId('screen');

function updateStatus(kind, message) {
  statusEl.textContent = message;
  statusEl.className = `status status-${kind}`;
}

function buildWebSocketUrl(identifier) {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = window.location.host;
  const path = `/websockify?token=${encodeURIComponent(identifier)}`;
  return `${protocol}//${host}${path}`;
}

function init() {
  const identifier = window.__VNC_ID__;
  if (!identifier) {
    updateStatus('error', 'VNC identifier was not provided.');
    return;
  }

  const wsUrl = buildWebSocketUrl(identifier);
  updateStatus('pending', `Connecting to ${identifier}…`);

  const rfb = new RFB(screenEl, wsUrl, {
    shared: true,
  });
  rfb.viewOnly = false;
  rfb.scaleViewport = true;
  rfb.resizeSession = true;
  rfb.focusOnClick = true;

  rfb.addEventListener('connect', () => {
    updateStatus('ok', 'Connected');
    screenEl.focus({ preventScroll: true });
  });

  rfb.addEventListener('disconnect', (event) => {
    const detail = event.detail || {};
    const clean = detail.clean ?? false;
    const reason = detail.reason ? `: ${detail.reason}` : '';
    updateStatus('error', clean ? 'Disconnected' : `Connection lost${reason}`);
  });

  rfb.addEventListener('securityfailure', (event) => {
    const detail = event.detail || {};
    const status = detail.status || 'unknown error';
    updateStatus('error', `Security failure (${status}).`);
  });

  rfb.addEventListener('credentialsrequired', () => {
    updateStatus('error', 'Password authentication is not supported for this session.');
  });

  window.addEventListener('beforeunload', () => {
    try {
      rfb.disconnect();
    } catch (error) {
      console.warn('Failed to disconnect cleanly', error);
    }
  });
}

init();
