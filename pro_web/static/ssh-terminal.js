(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const dialog = $('ssh-terminal-dialog');
  const host = $('ssh-terminal-host');
  const status = $('ssh-terminal-status');
  const openButton = $('web-ssh-terminal');

  if (!dialog || !host || !status || !openButton) return;

  let terminal = null;
  let socket = null;
  let serverId = 0;
  let resizeObserver = null;
  let intentionalClose = false;

  function setStatus(value, state = 'idle') {
    status.textContent = value;
    status.dataset.state = state;
  }

  function send(message) {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(message));
      return true;
    }
    return false;
  }

  function createTerminal() {
    if (terminal) return terminal;
    if (typeof window.Terminal !== 'function') {
      throw new Error('Terminal emulator failed to load. Reload the page and try again.');
    }
    if (window.fit && typeof window.Terminal.applyAddon === 'function') {
      window.Terminal.applyAddon(window.fit);
    }
    terminal = new window.Terminal({
      cols: 100,
      rows: 30,
      cursorBlink: true,
      scrollback: 10000,
      fontSize: 13,
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      convertEol: false
    });
    terminal.open(host);
    terminal.on('data', data => send({ type: 'input', data }));
    if (typeof terminal.attachCustomKeyEventHandler === 'function') {
      terminal.attachCustomKeyEventHandler(event => {
        if (event.type !== 'keydown' || !event.ctrlKey || !event.shiftKey) return true;
        const key = String(event.key || '').toLowerCase();
        if (key === 'c') {
          copySelection();
          return false;
        }
        if (key === 'v') {
          pasteClipboard();
          return false;
        }
        return true;
      });
    }
    return terminal;
  }

  function fitTerminal() {
    if (!terminal || !isDialogOpen()) return;
    try {
      if (typeof terminal.fit === 'function') terminal.fit();
      send({ type: 'resize', cols: terminal.cols || 100, rows: terminal.rows || 30 });
    } catch (_) {}
  }

  function isDialogOpen() {
    return dialog.hasAttribute('open') || dialog.open === true;
  }

  function showDialog() {
    if (typeof dialog.showModal === 'function') {
      dialog.showModal();
    } else {
      // Older Telegram Android WebViews do not implement HTMLDialogElement.showModal.
      dialog.setAttribute('open', '');
      dialog.classList.add('ssh-terminal-fallback-open');
    }
  }

  function hideDialog() {
    disconnect();
    if (typeof dialog.close === 'function' && dialog.open) dialog.close();
    else dialog.removeAttribute('open');
    dialog.classList.remove('ssh-terminal-fallback-open');
  }

  async function csrfProof() {
    const current = window.SonarWebAuth?.getCsrf?.() || '';
    if (current) return current;

    const response = await fetch('/api/web/csrf', {
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Accept': 'application/json' }
    });
    if (!response.ok) {
      throw new Error(response.status === 401
        ? 'The secure panel session has expired. Sign in again.'
        : 'Could not obtain a secure terminal authorization token.');
    }
    const body = await response.json();
    if (!body || typeof body.csrf !== 'string' || body.csrf.length < 28) {
      throw new Error('The terminal authorization token is invalid.');
    }
    // /api/web/csrf rotates the server-side CSRF hash. Keep the panel's next
    // POST requests synchronized with the newly issued token.
    window.SonarWebAuth?.setCsrf?.(body.csrf);
    return body.csrf;
  }

  async function connectTerminal(id) {
    if (socket && socket.readyState < WebSocket.CLOSING) {
      socket.close(1000, 'reconnect');
    }
    intentionalClose = false;
    setStatus('Authorizing…', 'connecting');
    terminal = createTerminal();
    terminal.clear();
    terminal.writeln('\x1b[36mSONAR RADAR | SSH TERMINAL\x1b[0m');
    terminal.writeln('Connecting to the selected server…\r\n');

    const initData = window.Telegram?.WebApp?.initData || '';
    let csrf = window.SonarWebAuth?.getCsrf?.() || '';
    if (!csrf && !initData) csrf = await csrfProof();

    const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${scheme}//${window.location.host}/api/terminal/${id}`);
    socket = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'auth', csrf, initData }));
      setStatus('Authenticating SSH session…', 'connecting');
    };
    ws.onmessage = event => {
      let message;
      try { message = JSON.parse(event.data); } catch (_) { return; }
      if (message.type === 'ready') {
        setStatus('Connected | ' + (message.server || 'SSH'), 'online');
        terminal.writeln('\x1b[32mSSH connection established.\x1b[0m\r\n');
        fitTerminal();
        terminal.focus();
      } else if (message.type === 'output' && typeof message.data === 'string') {
        terminal.write(message.data);
      } else if (message.type === 'error') {
        setStatus('Connection error', 'error');
        terminal.writeln('\r\n\x1b[31m' + String(message.message || 'Unknown error') + '\x1b[0m\r\n');
      } else if (message.type === 'closed') {
        setStatus('Session ended', 'idle');
      }
    };
    ws.onerror = () => {
      setStatus('WebSocket transport error', 'error');
    };
    ws.onclose = event => {
      if (!intentionalClose && event.code !== 1000) {
        const detail = event.code === 1006
          ? 'Connection lost (1006). Check the Nginx WebSocket upgrade configuration and the web service logs.'
          : 'Connection closed (code ' + event.code + ').';
        setStatus(detail, 'error');
        if (terminal) terminal.writeln('\r\n\x1b[31m' + detail + '\x1b[0m');
      } else if (status.dataset.state !== 'error') {
        setStatus('Disconnected', 'idle');
      }
    };
  }

  function disconnect() {
    intentionalClose = true;
    if (socket && socket.readyState < WebSocket.CLOSING) {
      try { send({ type: 'close' }); } catch (_) {}
      socket.close(1000, 'user closed');
    }
    socket = null;
    setStatus('Disconnected', 'idle');
  }

  async function copySelection() {
    const value = terminal?.getSelection?.() || '';
    if (!value) {
      setStatus('Select terminal text before copying.', 'idle');
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      setStatus('Selection copied.', 'online');
    } catch (_) {
      window.prompt('Copy the selected text:', value);
    }
  }

  async function pasteClipboard() {
    try {
      const value = await navigator.clipboard.readText();
      if (value) send({ type: 'input', data: value });
      terminal?.focus?.();
    } catch (_) {
      const value = window.prompt('Enter text to paste:');
      if (value) send({ type: 'input', data: value });
    }
  }

  openButton.addEventListener('click', async () => {
    const id = Number(window.SonarConsole?.currentServerId?.());
    if (!Number.isSafeInteger(id) || id < 1) {
      window.alert('Select a server from the server list first.');
      return;
    }
    serverId = id;
    $('ssh-terminal-server').textContent = 'Server #' + id;
    try {
      if (window.SonarStepUp) await window.SonarStepUp();
      if (!isDialogOpen()) showDialog();
      createTerminal();
      if (!resizeObserver && typeof ResizeObserver === 'function') {
        resizeObserver = new ResizeObserver(() => fitTerminal());
        resizeObserver.observe(host);
      }
      // Wait for the mobile WebView to lay out the full-screen dialog.
      requestAnimationFrame(() => requestAnimationFrame(fitTerminal));
      await connectTerminal(serverId);
    } catch (error) {
      setStatus('Unable to start terminal', 'error');
      if (terminal) terminal.writeln('\r\n\x1b[31m' + String(error.message || error) + '\x1b[0m');
    }
  });

  $('ssh-terminal-copy')?.addEventListener('click', copySelection);
  $('ssh-terminal-paste')?.addEventListener('click', pasteClipboard);
  $('ssh-terminal-clear')?.addEventListener('click', () => terminal?.clear?.());
  $('ssh-terminal-fit')?.addEventListener('click', fitTerminal);
  $('ssh-terminal-disconnect')?.addEventListener('click', disconnect);
  $('ssh-terminal-close')?.addEventListener('click', hideDialog);
  dialog.addEventListener('close', disconnect);
  dialog.addEventListener('keydown', event => {
    if (event.key === 'Escape' && dialog.classList.contains('ssh-terminal-fallback-open')) {
      event.preventDefault();
      hideDialog();
    }
  });
  window.addEventListener('resize', fitTerminal);
  window.addEventListener('orientationchange', () => setTimeout(fitTerminal, 150));
})();
