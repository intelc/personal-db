(() => {
  const state = {
    open: false,
    sessionId: null,
    socket: null,
    term: null,
    fit: null,
    cliType: 'claude',
    busy: false,
    startPromise: null,
    pendingInputs: [],
    delayNextPendingFlush: false,
    flushTimer: null,
    askTooltip: null,
    askAnchor: null,
    askMode: false,
    askButton: null,
    noteButton: null,
    hoverOutline: null,
    hoverTarget: null,
    providerMenu: null,
  };

  const NEW_SESSION_ASK_DELAY_MS = 2500;

  function $(selector, root = document) {
    return root.querySelector(selector);
  }

  function $all(selector, root = document) {
    return Array.from(root.querySelectorAll(selector));
  }

  async function requestJson(url, options = {}) {
    if (window.pdbApp && typeof window.pdbApp.requestJson === 'function') {
      return window.pdbApp.requestJson(url, options);
    }
    const request = { ...options };
    if (Object.prototype.hasOwnProperty.call(options, 'data')) {
      request.method = request.method || 'POST';
      request.headers = {
        ...(request.headers || {}),
        'Content-Type': 'application/json',
      };
      request.body = JSON.stringify(options.data);
      delete request.data;
    }
    const response = await fetch(url, request);
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    return response.json();
  }

  function truncate(value, max = 500) {
    const text = String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
    return text.length > max ? `${text.slice(0, max)}...` : text;
  }

  function columnLabel(col) {
    return col?.headerName || col?.field || col?.colId || '';
  }

  function isHtmlColumn(col) {
    return col?.cellRenderer === 'html';
  }

  function looksLikeHtml(value) {
    return typeof value === 'string' && /<\/?[a-z][\s\S]*>/i.test(value);
  }

  function askContextColumns(columns) {
    return columns.filter((col) => !isHtmlColumn(col));
  }

  function sanitizeGridRowForAsk(row, columns) {
    if (!row || typeof row !== 'object') return row;
    const htmlFields = new Set(
      columns.filter(isHtmlColumn).map((col) => col.field || col.colId).filter(Boolean)
    );
    return Object.fromEntries(
      Object.entries(row).filter(([key, value]) => (
        !key.startsWith('__') && !htmlFields.has(key) && !looksLikeHtml(value)
      ))
    );
  }

  function readJsonForElement(el, scriptAttr) {
    if (!el?.id) return null;
    const script = document.querySelector(`script[${scriptAttr}="${CSS.escape(el.id)}"]`);
    if (!script) return null;
    try {
      return JSON.parse(script.textContent || '{}');
    } catch (_error) {
      return null;
    }
  }

  function readJsonScript(selector, scriptAttr) {
    const out = [];
    $all(selector).forEach((el) => {
      const id = el.id;
      if (!id) return;
      const script = document.querySelector(`script[${scriptAttr}="${CSS.escape(id)}"]`);
      if (!script) return;
      try {
        out.push({ id, options: JSON.parse(script.textContent || '{}') });
      } catch (_error) {}
    });
    return out;
  }

  function collectDomContext() {
    const metrics = $all('.metric-card').slice(0, 30).map((card) => ({
      label: truncate($('.metric-label', card)?.textContent || '', 120),
      value: truncate($('strong', card)?.textContent || '', 120),
      hint: truncate($('.metric-hint', card)?.textContent || '', 160),
    }));
    const grids = readJsonScript('[data-pdb-grid]', 'data-pdb-grid-options').slice(0, 12).map((entry) => ({
      id: entry.id,
      columns: askContextColumns(entry.options.columnDefs || []).map(columnLabel).filter(Boolean),
      rowCount: Array.isArray(entry.options.rowData) ? entry.options.rowData.length : null,
      sampleRows: Array.isArray(entry.options.rowData)
        ? entry.options.rowData.slice(0, 5).map((row) => sanitizeGridRowForAsk(row, entry.options.columnDefs || []))
        : [],
    }));
    const charts = readJsonScript('[data-pdb-chart]', 'data-pdb-chart-options').slice(0, 12).map((entry) => ({
      id: entry.id,
      series: (entry.options.series || []).map((series) => series.yName || series.yKey || series.type).filter(Boolean),
      dataCount: Array.isArray(entry.options.data) ? entry.options.data.length : null,
      valueFormat: entry.options.valueFormat || null,
    }));
    return {
      browser: {
        url: window.location.href,
        path: `${window.location.pathname}${window.location.search}`,
        title: document.title,
      },
      visibleText: {
        headings: $all('h1, h2').slice(0, 20).map((el) => truncate(el.textContent, 180)),
        activeNav: truncate($('.masthead nav a.active')?.textContent || ''),
        activeAppTab: truncate($('.app-tabs a.active')?.textContent || ''),
      },
      metrics,
      grids,
      charts,
    };
  }

  async function collectContext() {
    const path = `${window.location.pathname}${window.location.search}`;
    let backend = {};
    try {
      backend = await requestJson(`/api/agent/context?path=${encodeURIComponent(path)}`);
    } catch (error) {
      backend = { error: error.message || String(error) };
    }
    return {
      backend,
      page: collectDomContext(),
    };
  }

  function setStatus(text) {
    const el = $('[data-pdb-agent-status]');
    if (el) el.textContent = text;
  }

  function setOpen(open) {
    state.open = open;
    document.body.classList.toggle('pdb-agent-open', open);
    const drawer = $('#pdb-agent-drawer');
    const toggle = $('#pdb-agent-toggle');
    if (drawer) drawer.setAttribute('aria-hidden', open ? 'false' : 'true');
    if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) {
      window.setTimeout(() => {
        fitTerminal();
        state.term?.focus();
      }, 50);
    }
  }

  function fitTerminal() {
    if (!state.term || !state.fit) return;
    try {
      state.fit.fit();
      sendResize();
    } catch (_error) {}
  }

  function sendResize() {
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN || !state.term) return;
    state.socket.send(JSON.stringify({ type: 'resize', cols: state.term.cols, rows: state.term.rows }));
  }

  function writeLine(text) {
    if (state.term) {
      state.term.writeln(text);
    } else {
      const mount = $('#pdb-agent-terminal');
      if (mount) mount.textContent += `${text}\n`;
    }
  }

  function terminalPromptInput(prompt) {
    const text = String(prompt || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
    if (!text) return '';
    return `\x1b[200~${text}\x1b[201~\r`;
  }

  function sendTerminalInput(item) {
    const data = typeof item === 'string' ? item : item?.data;
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN || !data) return false;
    state.socket.send(JSON.stringify({ type: 'input', data }));
    return true;
  }

  function flushPendingInputs() {
    if (!state.pendingInputs.length) return;
    if (!state.socket || state.socket.readyState !== WebSocket.OPEN) return;
    if (state.flushTimer) {
      window.clearTimeout(state.flushTimer);
      state.flushTimer = null;
    }
    const now = Date.now();
    const next = state.pendingInputs[0];
    const notBefore = typeof next === 'string' ? 0 : next.notBefore || 0;
    if (notBefore > now) {
      state.flushTimer = window.setTimeout(flushPendingInputs, notBefore - now);
      return;
    }
    while (state.pendingInputs.length) {
      const item = state.pendingInputs[0];
      const itemNotBefore = typeof item === 'string' ? 0 : item.notBefore || 0;
      if (itemNotBefore > Date.now()) {
        state.flushTimer = window.setTimeout(flushPendingInputs, itemNotBefore - Date.now());
        break;
      }
      sendTerminalInput(state.pendingInputs.shift());
    }
    state.term?.focus();
  }

  function ensureTerminal() {
    if (state.term) return true;
    const mount = $('#pdb-agent-terminal');
    if (!mount) return false;
    mount.textContent = '';
    if (!window.Terminal || !window.FitAddon || !window.FitAddon.FitAddon) {
      mount.textContent = 'xterm.js failed to load';
      return false;
    }
    state.term = new window.Terminal({
      cursorBlink: true,
      convertEol: true,
      fontFamily: "'IBM Plex Mono', Menlo, Monaco, Consolas, monospace",
      fontSize: 12,
      theme: {
        background: '#050505',
        foreground: '#f6f6f6',
        cursor: '#ffffff',
        selectionBackground: '#333333',
      },
    });
    state.fit = new window.FitAddon.FitAddon();
    state.term.loadAddon(state.fit);
    state.term.open(mount);
    state.term.onData((data) => {
      if (!state.socket || state.socket.readyState !== WebSocket.OPEN) return;
      state.socket.send(JSON.stringify({ type: 'input', data }));
    });
    state.term.onResize(sendResize);
    fitTerminal();
    return true;
  }

  async function startSession() {
    if (state.startPromise) return state.startPromise;
    state.busy = true;
    setStatus('capturing context');
    state.startPromise = (async () => {
      try {
        ensureTerminal();
        state.term?.reset();
        writeLine(`starting ${state.cliType} with current page context...`);
        const context = await collectContext();
        const body = {
          cli_type: state.cliType,
          context,
          cols: state.term?.cols || 100,
          rows: state.term?.rows || 30,
        };
        const result = await requestJson('/api/agent/sessions', {
          method: 'POST',
          data: body,
        });
        state.sessionId = result.session.id;
        connectSocket();
        setStatus(`${result.session.cli_type} - ${result.session.id}`);
        return result.session;
      } catch (error) {
        writeLine(`failed to start: ${error.message || error}`);
        setStatus('failed');
        return null;
      } finally {
        state.busy = false;
        state.startPromise = null;
      }
    })();
    return state.startPromise;
  }

  function connectSocket() {
    if (!state.sessionId) return;
    if (state.socket) state.socket.close();
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    state.socket = new WebSocket(`${protocol}//${window.location.host}/api/agent/sessions/${state.sessionId}/terminal`);
    state.socket.addEventListener('open', () => {
      setStatus(`${state.cliType} - connected`);
      sendResize();
      if (state.delayNextPendingFlush && state.pendingInputs.length) {
        const notBefore = Date.now() + NEW_SESSION_ASK_DELAY_MS;
        state.pendingInputs = state.pendingInputs.map((item) => (
          typeof item === 'string' ? { data: item, notBefore } : { ...item, notBefore: Math.max(item.notBefore || 0, notBefore) }
        ));
        state.delayNextPendingFlush = false;
      }
      flushPendingInputs();
    });
    state.socket.addEventListener('message', (event) => {
      let message = null;
      try {
        message = JSON.parse(event.data);
      } catch (_error) {
        return;
      }
      if (message.type === 'output' && message.data) {
        state.term?.write(message.data);
      } else if (message.type === 'exit') {
        writeLine(`\r\n[process exited${message.code == null ? '' : `: ${message.code}`}]`);
        setStatus('exited');
      }
    });
    state.socket.addEventListener('close', () => {
      if (state.sessionId) setStatus('disconnected');
    });
    state.socket.addEventListener('error', () => setStatus('socket error'));
  }

  async function askAgent(prompt, details = {}) {
    const text = typeof prompt === 'string' ? prompt : String(prompt || '');
    const input = terminalPromptInput(text);
    if (!input) return { ok: false, reason: 'empty prompt' };
    const needsNewSession = !state.sessionId && !state.startPromise;
    setOpen(true);
    ensureTerminal();
    state.pendingInputs.push({ data: input, notBefore: 0 });
    if (state.socket && state.socket.readyState === WebSocket.OPEN) {
      flushPendingInputs();
      return { ok: true, session_id: state.sessionId, details };
    }
    if (state.sessionId && state.socket && state.socket.readyState <= WebSocket.CONNECTING) {
      return { ok: true, queued: true, session_id: state.sessionId, details };
    }
    if (needsNewSession) state.delayNextPendingFlush = true;
    await startSession();
    return { ok: true, queued: true, session_id: state.sessionId, details };
  }

  async function stopSession() {
    if (!state.sessionId) return;
    const id = state.sessionId;
    try {
      await requestJson(`/api/agent/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' });
    } catch (_error) {}
  }

  async function newSession(cliType = state.cliType) {
    setCliType(cliType);
    setProviderMenuOpen(false);
    await stopSession();
    if (state.socket) state.socket.close();
    state.sessionId = null;
    state.socket = null;
    await startSession();
  }

  function setCliType(cliType) {
    state.cliType = cliType === 'codex' ? 'codex' : 'claude';
    const label = $('[data-pdb-agent-provider]');
    if (label) label.textContent = state.cliType;
    $all('[data-pdb-agent-cli]').forEach((button) => {
      const active = button.dataset.pdbAgentCli === state.cliType;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }

  function setProviderMenuOpen(open) {
    if (!state.providerMenu) return;
    state.providerMenu.hidden = !open;
    $('[data-pdb-agent-new]')?.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  function metricPrompt(card) {
    const label = truncate($('.metric-label', card)?.textContent || 'metric', 160);
    const value = truncate($('strong', card)?.textContent || '', 160);
    const hint = truncate($('.metric-hint', card)?.textContent || '', 240);
    const headings = $all('h1, h2').slice(0, 4).map((el) => truncate(el.textContent, 120)).filter(Boolean);
    return [
      'Tell me more about this personal_db value.',
      `Page: ${document.title}`,
      `Path: ${window.location.pathname}${window.location.search}`,
      headings.length ? `Visible section: ${headings.join(' > ')}` : '',
      `Metric: ${label}`,
      `Value: ${value}`,
      hint ? `Hint/context: ${hint}` : '',
      '',
      'Please explain what this value likely means, what underlying personal_db data or query you would inspect next, and whether anything looks surprising.',
    ].filter(Boolean).join('\n');
  }

  function visibleHeadingsNear(element) {
    const headings = [];
    let node = element;
    while (node && node !== document.body && headings.length < 4) {
      const heading = node.querySelector?.('h1, h2, h3');
      if (heading) headings.push(truncate(heading.textContent, 120));
      node = node.parentElement;
    }
    if (!headings.length) {
      headings.push(...$all('h1, h2').slice(0, 4).map((el) => truncate(el.textContent, 120)));
    }
    return headings.filter(Boolean);
  }

  function dataAttrs(element) {
    return Object.fromEntries(
      Array.from(element.attributes || [])
        .filter((attr) => attr.name.startsWith('data-'))
        .slice(0, 12)
        .map((attr) => [attr.name, truncate(attr.value, 240)])
    );
  }

  function elementSummary(element) {
    return {
      tag: element.tagName?.toLowerCase() || '',
      id: element.id || '',
      classes: Array.from(element.classList || []).slice(0, 10),
      role: element.getAttribute?.('role') || '',
      ariaLabel: element.getAttribute?.('aria-label') || '',
      title: element.getAttribute?.('title') || '',
      href: element.getAttribute?.('href') || '',
      text: truncate(element.innerText || element.textContent || '', 900),
      data: dataAttrs(element),
    };
  }

  function anchorRect(anchor) {
    if (anchor.rect) return anchor.rect;
    const element = anchor.element;
    if (!element || !element.isConnected) return null;
    return element.getBoundingClientRect();
  }

  function gridAnchor(target) {
    const cell = target.closest?.('.ag-cell');
    const grid = target.closest?.('[data-pdb-grid]');
    if (!grid) return null;
    const options = readJsonForElement(grid, 'data-pdb-grid-options') || {};
    const columns = Array.isArray(options.columnDefs) ? options.columnDefs : [];
    const rows = Array.isArray(options.rowData) ? options.rowData : [];
    if (cell) {
      const rowEl = cell.closest('.ag-row');
      const colId = cell.getAttribute('col-id') || '';
      const visualRowIndex = Number(rowEl?.getAttribute('row-index'));
      const rowNode = Number.isFinite(visualRowIndex)
        ? grid.__pdbGridApi?.getDisplayedRowAtIndex?.(visualRowIndex)
        : null;
      const rowData = sanitizeGridRowForAsk(rowNode?.data || rows[visualRowIndex] || null, columns);
      const colDef = columns.find((col) => (
        col.field === colId || col.colId === colId || col.headerName === colId
      )) || {};
      const contextColumns = askContextColumns(columns);
      return {
        kind: 'ag-grid-cell',
        element: cell,
        label: colDef.headerName || colDef.field || colId || 'grid cell',
        rect: cell.getBoundingClientRect(),
        payload: {
          gridId: grid.id,
          column: colDef.headerName || colDef.field || colId,
          field: colDef.field || colId,
          displayedValue: truncate(cell.innerText || cell.textContent || '', 300),
          visualRowIndex: Number.isFinite(visualRowIndex) ? visualRowIndex : null,
          row: rowData,
          columns: contextColumns.map(columnLabel).filter(Boolean),
          rowCount: rows.length,
        },
      };
    }
    return {
      kind: 'ag-grid',
      element: grid,
      label: 'grid',
      payload: {
        gridId: grid.id,
        columns: askContextColumns(columns).map(columnLabel).filter(Boolean),
        rowCount: rows.length,
        sampleRows: rows.slice(0, 8).map((row) => sanitizeGridRowForAsk(row, columns)),
      },
    };
  }

  function chartAnchor(target) {
    const chart = target.closest?.('[data-pdb-chart]');
    if (!chart) return null;
    const options = readJsonForElement(chart, 'data-pdb-chart-options') || {};
    return {
      kind: 'chart',
      element: chart,
      label: 'chart',
      payload: {
        chartId: chart.id,
        series: (options.series || []).map((series) => ({
          type: series.type,
          xKey: series.xKey,
          yKey: series.yKey,
          yName: series.yName,
          angleKey: series.angleKey,
          labelKey: series.labelKey,
        })),
        axes: options.axes || null,
        valueFormat: options.valueFormat || null,
        dataCount: Array.isArray(options.data) ? options.data.length : null,
        sampleData: Array.isArray(options.data) ? options.data.slice(0, 8) : [],
      },
    };
  }

  function metricAnchor(target) {
    const card = target.closest?.('.metric-card');
    if (!card) return null;
    return {
      kind: 'metric',
      element: card,
      label: truncate($('.metric-label', card)?.textContent || 'metric', 160),
      payload: {
        label: truncate($('.metric-label', card)?.textContent || '', 160),
        value: truncate($('strong', card)?.textContent || '', 160),
        hint: truncate($('.metric-hint', card)?.textContent || '', 240),
      },
    };
  }

  function genericAnchor(target) {
    const element = target.closest?.('button, a, input, select, textarea, [role], .app-section, .app-page, article, section, form, label, div, span');
    if (!element || element === document.body || element === document.documentElement) return null;
    return {
      kind: 'element',
      element,
      label: truncate(
        element.getAttribute('aria-label') || element.getAttribute('title') || element.innerText || element.textContent || element.tagName,
        120
      ),
      payload: {
        element: elementSummary(element),
      },
    };
  }

  function buildAskAnchor(target) {
    if (!target?.closest) return null;
    if (target.closest('#pdb-agent-drawer, #pdb-agent-toggle, #pdb-agent-ask-toggle, #pdb-note-toggle, .pdb-agent-ask-chip, .pdb-agent-hover-outline')) {
      return null;
    }
    return metricAnchor(target) || gridAnchor(target) || chartAnchor(target) || genericAnchor(target);
  }

  function elementPrompt(anchor) {
    const headings = visibleHeadingsNear(anchor.element);
    const payload = JSON.stringify(anchor.payload || {}, null, 2);
    return [
      'Tell me more about this selected personal_db page element.',
      `Page: ${document.title}`,
      `Path: ${window.location.pathname}${window.location.search}`,
      headings.length ? `Visible section: ${headings.join(' > ')}` : '',
      `Selection kind: ${anchor.kind}`,
      anchor.label ? `Selection label: ${anchor.label}` : '',
      '',
      'Selected element context JSON:',
      payload.length > 5000 ? `${payload.slice(0, 5000)}\n...<truncated>` : payload,
      '',
      'Please explain what this element likely represents, what underlying personal_db data or query powers it, and what you would inspect next.',
    ].filter(Boolean).join('\n');
  }

  function anchorPrompt(anchor) {
    if (anchor.kind === 'metric') return metricPrompt(anchor.element);
    return elementPrompt(anchor);
  }

  function hideAskTooltip() {
    if (!state.askTooltip) return;
    state.askTooltip.hidden = true;
    state.askAnchor = null;
  }

  function selectionMetricCard() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) return null;
    const text = truncate(selection.toString(), 240);
    if (!text) return null;
    const range = selection.getRangeAt(0);
    const container = range.commonAncestorContainer;
    const element = container.nodeType === Node.ELEMENT_NODE ? container : container.parentElement;
    const card = element?.closest?.('.metric-card');
    if (!card) return null;
    const value = $('strong', card);
    if (!value || !selection.containsNode(value, true)) return null;
    const rects = Array.from(range.getClientRects()).filter((rect) => rect.width && rect.height);
    const rect = rects[rects.length - 1] || range.getBoundingClientRect();
    if (!rect || (!rect.width && !rect.height)) return null;
    return {
      kind: 'metric',
      element: card,
      label: truncate($('.metric-label', card)?.textContent || 'metric', 160),
      rect,
      source: 'selection',
      payload: {
        label: truncate($('.metric-label', card)?.textContent || '', 160),
        value: truncate($('strong', card)?.textContent || '', 160),
        hint: truncate($('.metric-hint', card)?.textContent || '', 240),
      },
    };
  }

  function showAskTooltip(anchor) {
    if (!state.askTooltip || !anchor) return;
    state.askAnchor = anchor;
    state.askTooltip.hidden = false;
    positionAskTooltip();
  }

  function positionAskTooltip() {
    if (!state.askTooltip || state.askTooltip.hidden) return;
    const selection = state.askAnchor?.rect ? null : selectionMetricCard();
    if (selection) state.askAnchor = selection;
    const rect = anchorRect(state.askAnchor || {});
    if (!rect || (!rect.width && !rect.height)) {
      hideAskTooltip();
      return;
    }
    const tooltip = state.askTooltip;
    const margin = 8;
    const top = Math.max(margin, rect.top - tooltip.offsetHeight - 6);
    const left = Math.min(
      window.innerWidth - tooltip.offsetWidth - margin,
      Math.max(margin, rect.left + (rect.width - tooltip.offsetWidth) / 2)
    );
    tooltip.style.top = `${top}px`;
    tooltip.style.left = `${left}px`;
  }

  function updateAskTooltip() {
    if (!state.askTooltip) return;
    if (state.askAnchor && state.askAnchor.source === 'lens') {
      positionAskTooltip();
      return;
    }
    const selection = selectionMetricCard();
    if (!selection) {
      hideAskTooltip();
      return;
    }
    showAskTooltip(selection);
  }

  function ensureAskTooltip() {
    if (state.askTooltip) return state.askTooltip;
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'pdb-agent-ask-chip';
    chip.dataset.pdbAgentAsk = 'selection';
    chip.textContent = 'ask';
    chip.hidden = true;
    chip.setAttribute('aria-label', 'Ask the agent about this highlighted value');
    chip.addEventListener('mousedown', (event) => event.preventDefault());
    document.body.appendChild(chip);
    state.askTooltip = chip;
    return chip;
  }

  function handleAskTarget(target) {
    const askTarget = target.closest?.('.pdb-agent-ask-chip[data-pdb-agent-ask="selection"]');
    if (askTarget) {
      const anchor = state.askAnchor;
      if (!anchor) return false;
      askAgent(anchorPrompt(anchor), { kind: anchor.kind });
      hideAskTooltip();
      setAskMode(false);
      return true;
    }
    return false;
  }

  function ensureHoverOutline() {
    if (state.hoverOutline) return state.hoverOutline;
    const outline = document.createElement('div');
    outline.className = 'pdb-agent-hover-outline';
    outline.hidden = true;
    document.body.appendChild(outline);
    state.hoverOutline = outline;
    return outline;
  }

  function positionHoverOutline(element) {
    const outline = ensureHoverOutline();
    if (!element || !element.isConnected) {
      outline.hidden = true;
      return;
    }
    const rect = element.getBoundingClientRect();
    if (!rect.width && !rect.height) {
      outline.hidden = true;
      return;
    }
    outline.hidden = false;
    outline.style.top = `${rect.top}px`;
    outline.style.left = `${rect.left}px`;
    outline.style.width = `${rect.width}px`;
    outline.style.height = `${rect.height}px`;
  }

  function setAskMode(enabled) {
    state.askMode = Boolean(enabled);
    document.body.classList.toggle('pdb-agent-ask-mode', state.askMode);
    state.askButton?.classList.toggle('active', state.askMode);
    state.askButton?.setAttribute('aria-pressed', state.askMode ? 'true' : 'false');
    if (!state.askMode) {
      state.hoverTarget = null;
      if (state.hoverOutline) state.hoverOutline.hidden = true;
    }
  }

  function setNoteMode(enabled) {
    if (!window.pdbNotes || typeof window.pdbNotes.setMode !== 'function') return false;
    window.pdbNotes.setMode(Boolean(enabled));
    state.noteButton?.classList.toggle('active', Boolean(enabled));
    state.noteButton?.setAttribute('aria-pressed', enabled ? 'true' : 'false');
    state.noteButton && (state.noteButton.textContent = enabled ? 'notes on' : 'note');
    return true;
  }

  function toggleNoteMode() {
    if (!window.pdbNotes) return;
    const next = typeof window.pdbNotes.isActive === 'function' ? !window.pdbNotes.isActive() : true;
    setNoteMode(next);
  }

  function updateAskHover(event) {
    if (!state.askMode) return;
    const anchor = buildAskAnchor(event.target);
    const element = anchor?.element || null;
    if (element === state.hoverTarget) return;
    state.hoverTarget = element;
    positionHoverOutline(element);
  }

  function selectAskHover(event) {
    if (!state.askMode) return false;
    const anchor = buildAskAnchor(event.target);
    if (!anchor) return false;
    event.preventDefault();
    event.stopPropagation();
    if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
    window.getSelection()?.removeAllRanges();
    anchor.rect = anchor.element.getBoundingClientRect();
    anchor.source = 'lens';
    showAskTooltip(anchor);
    setAskMode(false);
    return true;
  }

  async function agentTerminalEnabled() {
    // The dashboard has no session yet when this runs, so this hits the
    // token-authenticated /api/agent/context route the same way the rest of
    // the page's own fetches do (cookie set at page load). A failed/blocked
    // request is treated as "disabled" -- fail closed, not open.
    try {
      const context = await requestJson('/api/agent/context?path=/');
      return Boolean(context.agent_terminal_enabled);
    } catch (_error) {
      return false;
    }
  }

  async function mount() {
    // "note" is an independent feature (delegates to window.pdbNotes) that
    // happens to share this file/mount() for convenience -- it mounts
    // unconditionally regardless of the agent terminal's enabled state.
    const noteToggle = document.getElementById('pdb-note-toggle') || document.createElement('button');
    noteToggle.id = 'pdb-note-toggle';
    noteToggle.className = 'pdb-note-toggle';
    noteToggle.type = 'button';
    noteToggle.setAttribute('aria-pressed', 'false');
    noteToggle.title = 'Add or view notes on this page';
    noteToggle.textContent = 'note';
    if (!noteToggle.isConnected && window.pdbNotes) document.body.appendChild(noteToggle);
    noteToggle.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
      toggleNoteMode();
    });
    state.noteButton = noteToggle;

    // Everything below is the agent terminal itself (drawer, "agent"/"ask"
    // toggles, the PTY websocket) -- gated behind config.yaml:
    // agent_terminal.enabled (default off). Hidden entirely rather than
    // shown-then-403'd so a disabled terminal doesn't leave broken-looking
    // affordances on the page.
    if (!(await agentTerminalEnabled())) return;

    const toggle = document.createElement('button');
    toggle.id = 'pdb-agent-toggle';
    toggle.className = 'pdb-agent-toggle';
    toggle.type = 'button';
    toggle.setAttribute('aria-controls', 'pdb-agent-drawer');
    toggle.setAttribute('aria-expanded', 'false');
    toggle.textContent = 'agent';

    const askToggle = document.createElement('button');
    askToggle.id = 'pdb-agent-ask-toggle';
    askToggle.className = 'pdb-agent-ask-toggle';
    askToggle.type = 'button';
    askToggle.setAttribute('aria-pressed', 'false');
    askToggle.title = 'Select something on the page to ask about';
    askToggle.textContent = 'ask';

    const drawer = document.createElement('aside');
    drawer.id = 'pdb-agent-drawer';
    drawer.className = 'pdb-agent-drawer';
    drawer.setAttribute('aria-hidden', 'true');
    drawer.innerHTML = `
      <header class="pdb-agent-head">
        <div>
          <strong>CLI agent</strong>
          <span data-pdb-agent-status>idle</span>
        </div>
        <button type="button" class="pdb-agent-close" title="Hide drawer" aria-label="Hide drawer">&rsaquo;</button>
      </header>
      <div class="pdb-agent-toolbar">
        <span>using <strong data-pdb-agent-provider>claude</strong></span>
        <button type="button" data-pdb-agent-new aria-expanded="false" aria-controls="pdb-agent-provider-menu">new</button>
        <div id="pdb-agent-provider-menu" class="pdb-agent-provider-menu" hidden>
          <span>choose</span>
          <button type="button" data-pdb-agent-cli="claude" class="active" aria-pressed="true">Claude</button>
          <button type="button" data-pdb-agent-cli="codex" aria-pressed="false">Codex</button>
        </div>
      </div>
      <div id="pdb-agent-terminal" class="pdb-agent-terminal" aria-label="Agent terminal"></div>
    `;
    document.body.appendChild(askToggle);
    document.body.appendChild(toggle);
    document.body.appendChild(drawer);
    ensureAskTooltip();
    ensureHoverOutline();

    toggle.addEventListener('click', async () => {
      setOpen(!state.open);
      if (state.open && !state.sessionId) await startSession();
    });
    askToggle.addEventListener('click', () => {
      hideAskTooltip();
      setAskMode(!state.askMode);
    });
    state.askButton = askToggle;
    state.providerMenu = $('#pdb-agent-provider-menu', drawer);
    $('.pdb-agent-close', drawer)?.addEventListener('click', () => setOpen(false));
    $('[data-pdb-agent-new]', drawer)?.addEventListener('click', () => {
      setProviderMenuOpen(state.providerMenu?.hidden !== false);
    });
    $all('[data-pdb-agent-cli]', drawer).forEach((button) => {
      button.addEventListener('click', () => newSession(button.dataset.pdbAgentCli));
    });
    document.addEventListener('click', (event) => {
      if (selectAskHover(event)) return;
      if (handleAskTarget(event.target)) event.preventDefault();
    }, true);
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      if (handleAskTarget(event.target)) {
        event.preventDefault();
      }
    });
    document.addEventListener('selectionchange', () => {
      window.requestAnimationFrame(updateAskTooltip);
    });
    document.addEventListener('mouseup', () => {
      window.setTimeout(updateAskTooltip, 0);
    });
    document.addEventListener('keyup', (event) => {
      if (event.key.startsWith('Arrow') || event.key === 'Shift' || event.key === 'Meta') updateAskTooltip();
    });
    document.addEventListener('scroll', positionAskTooltip, true);
    document.addEventListener('mousemove', updateAskHover, true);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && state.askMode) {
        event.preventDefault();
        setAskMode(false);
      }
    });
    window.addEventListener('resize', fitTerminal);
    window.addEventListener('resize', positionAskTooltip);
    window.pdbAgent = {
      ask: askAgent,
      collectContext,
      open: () => setOpen(true),
      hide: () => setOpen(false),
    };
    setCliType('claude');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
