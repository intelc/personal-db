(() => {
  function htmlCellRenderer(params) {
    const span = document.createElement('span');
    span.innerHTML = params.value == null ? '' : String(params.value);
    wireInlineForms(span, params);
    return span;
  }

  function formPayload(form) {
    return Object.fromEntries(Array.from(new FormData(form).entries()).map(([key, value]) => [key, String(value)]));
  }

  function burnRuleReason(scope) {
    if (scope === 'merchant') return 'user merchant rule';
    if (scope === 'category') return 'user category rule';
    return 'transaction override';
  }

  async function submitBurnAction(form, params) {
    const payload = formPayload(form);
    const bucket = payload.bucket || '';
    const scope = payload.scope || 'transaction';
    const select = form.querySelector('select[name="bucket"]');
    const bucketLabel = select && select.selectedOptions.length ? select.selectedOptions[0].textContent : bucket;
    const previousBucket = params.data && params.data.__burnBucket ? params.data.__burnBucket : '';
    const button = form.querySelector('button[type="submit"], button:not([type])');
    const previousText = button ? button.textContent : '';
    if (button) {
      button.disabled = true;
      button.textContent = 'saving';
    }
    form.dispatchEvent(
      new CustomEvent('pdb-burn-classifying', {
        bubbles: true,
        detail: {
          oldBucket: previousBucket,
          newBucket: bucket,
          bucketLabel: bucketLabel || bucket,
          transactionId: payload.finance_transaction_id || '',
        },
      })
    );
    try {
      const response = await fetch(form.action, {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const message = await response.text();
        throw new Error(message || `Save failed (${response.status})`);
      }
      const actionResult = await response.json();
      const grid = form.closest('[data-pdb-grid]');
      if (params.data) {
        params.data.__burnBucket = bucket;
        params.data.bucket = bucketLabel || bucket;
        params.data.matched_rule = burnRuleReason(scope);
        if (params.api && typeof params.api.applyTransaction === 'function') {
          params.api.applyTransaction({ update: [params.data] });
        } else if (params.api && typeof params.api.refreshCells === 'function') {
          params.api.refreshCells({ force: true });
        }
      }
      if (params.api && typeof params.api.onFilterChanged === 'function') {
        params.api.onFilterChanged();
      }
      (grid || form).dispatchEvent(
        new CustomEvent('pdb-burn-classified', {
          bubbles: true,
          detail: {
            oldBucket: previousBucket,
            newBucket: bucket,
            bucketLabel: bucketLabel || bucket,
            transactionId: payload.finance_transaction_id || '',
            actionResult,
          },
        })
      );
    } catch (error) {
      form.dataset.error = error && error.message ? error.message : 'Save failed';
      form.dispatchEvent(
        new CustomEvent('pdb-burn-classify-error', {
          bubbles: true,
          detail: {
            message: form.dataset.error,
            oldBucket: previousBucket,
            newBucket: bucket,
            transactionId: payload.finance_transaction_id || '',
          },
        })
      );
      if (button) {
        button.disabled = false;
        button.textContent = 'retry';
      }
      return;
    }
    form.dataset.saved = '1';
    if (button) {
      button.disabled = false;
      button.textContent = 'saved';
      window.setTimeout(() => {
        button.textContent = previousText || 'save';
        delete form.dataset.saved;
      }, 900);
    }
  }

  function wireInlineForms(root, params) {
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
    root
      .querySelectorAll('.category-action button, .review-action button, .burn-action button')
      .forEach((button) => {
        button.addEventListener('click', (event) => event.stopPropagation());
      });
    root.querySelectorAll('.burn-action select').forEach((select) => {
      select.addEventListener('click', (event) => event.stopPropagation());
      select.addEventListener('keydown', (event) => event.stopPropagation());
    });
    root.querySelectorAll('.burn-action').forEach((form) => {
      form.addEventListener('submit', (event) => {
        event.preventDefault();
        event.stopPropagation();
        submitBurnAction(form, params);
      });
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
    if (out.cellRenderer === 'html') {
      out.cellRenderer = htmlCellRenderer;
    } else if (out.tooltipField === undefined && out.tooltipValueGetter === undefined) {
      // Plain-text columns: show the full cell value on hover so truncated
      // dates/long strings (e.g. "2026-07-0…") stay readable without
      // widening every column.
      out.tooltipField = out.field;
    }
    if (out.headerTooltip === undefined && out.headerName) {
      // Header text gets truncated at narrow widths (e.g. "TIMEZON…");
      // hovering reveals the full header name.
      out.headerTooltip = out.headerName;
    }
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
        ...(raw.defaultColDef || {}),
      },
      // Size columns to fit their header/cell content on first render instead
      // of the old equal-width flex layout, which is what was truncating long
      // headers ("TIMEZON…") and ISO date cells ("2026-07-0…"). AG Grid does
      // not honor per-column width from autoSizeStrategy on flex columns, so
      // this replaces the flex:1 default above rather than combining with it.
      autoSizeStrategy: raw.autoSizeStrategy || { type: 'fitCellContents' },
      suppressCellFocus: true,
      animateRows: false,
      getRowClass: grouped
        ? (params) => (params.data && params.data.__pdbGroup ? 'pdb-grid-group-row' : '')
        : undefined,
      isFullWidthRow: grouped
        ? (params) => Boolean(params.rowNode.data && params.rowNode.data.__pdbGroup)
        : undefined,
      fullWidthCellRenderer: grouped ? groupCellRenderer : undefined,
      isExternalFilterPresent: () => Boolean(el.dataset.pdbBucketFilter),
      doesExternalFilterPass: (node) =>
        !el.dataset.pdbBucketFilter ||
        (node.data && node.data.__burnBucket === el.dataset.pdbBucketFilter),
    };
    delete options.grouped;
    el.__pdbGridApi = window.agGrid.createGrid(el, options);
    el.dataset.pdbGridReady = '1';
    el.dispatchEvent(new CustomEvent('pdb-grid-ready', { bubbles: true }));
  }

  function initAll() {
    document.querySelectorAll('[data-pdb-grid]').forEach(initGrid);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
  document.addEventListener('pdb:navigate', initAll);
})();
