(() => {
  function htmlCellRenderer(params) {
    const span = document.createElement('span');
    span.innerHTML = params.value == null ? '' : String(params.value);
    wireInlineForms(span);
    return span;
  }

  function wireInlineForms(root) {
    root.querySelectorAll('.category-action input[name="category"]').forEach((input) => {
      input.addEventListener('click', (event) => event.stopPropagation());
      input.addEventListener('keydown', (event) => {
        event.stopPropagation();
        if (event.key !== 'Enter' || event.isComposing) return;
        event.preventDefault();
        const form = input.closest('form');
        if (!form || !input.value.trim()) return;
        if (typeof form.requestSubmit === 'function') {
          form.requestSubmit();
        } else {
          form.submit();
        }
      });
    });
    root.querySelectorAll('.category-action button, .review-action button').forEach((button) => {
      button.addEventListener('click', (event) => event.stopPropagation());
    });
  }

  function groupCellRenderer(params) {
    const span = document.createElement('span');
    span.className = 'pdb-grid-group-label';
    span.textContent = params.data && params.data.__groupText ? params.data.__groupText : '';
    return span;
  }

  function normalizeColumn(col) {
    const out = { ...col };
    if (out.cellRenderer === 'html') out.cellRenderer = htmlCellRenderer;
    return out;
  }

  function initGrid(el) {
    if (el.dataset.pdbGridReady === '1') return;
    const script = document.querySelector(
      `script[data-pdb-grid-options="${CSS.escape(el.id)}"]`
    );
    if (!script) return;
    if (!window.agGrid || !window.agGrid.createGrid) {
      el.textContent = 'AG Grid failed to load';
      el.classList.add('pdb-grid-error');
      return;
    }

    const raw = JSON.parse(script.textContent || '{}');
    const grouped = Boolean(raw.grouped);
    const options = {
      ...raw,
      columnDefs: (raw.columnDefs || []).map(normalizeColumn),
      defaultColDef: {
        sortable: true,
        filter: true,
        resizable: true,
        minWidth: 110,
        flex: 1,
        ...(raw.defaultColDef || {}),
      },
      suppressCellFocus: true,
      animateRows: false,
      getRowClass: grouped
        ? (params) => (params.data && params.data.__pdbGroup ? 'pdb-grid-group-row' : '')
        : undefined,
      isFullWidthRow: grouped
        ? (params) => Boolean(params.rowNode.data && params.rowNode.data.__pdbGroup)
        : undefined,
      fullWidthCellRenderer: grouped ? groupCellRenderer : undefined,
    };
    delete options.grouped;
    window.agGrid.createGrid(el, options);
    el.dataset.pdbGridReady = '1';
  }

  function initAll() {
    document.querySelectorAll('[data-pdb-grid]').forEach(initGrid);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
})();
