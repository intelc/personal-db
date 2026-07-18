(() => {
  const SELF_ONLY_KEY = 'pdb.finance.selfOnly';

  function savedSelfOnly() {
    try {
      const value = window.localStorage.getItem(SELF_ONLY_KEY);
      return value == null ? true : value !== '0';
    } catch (_error) {
      return true;
    }
  }

  function persistSelfOnly(value) {
    try {
      window.localStorage.setItem(SELF_ONLY_KEY, value ? '1' : '0');
    } catch (_error) {
      // The checkbox still works for this page if storage is unavailable.
    }
  }

  function initFinancePage(page) {
    const toggle = page.querySelector('[data-finance-self-only]');
    if (!toggle) return;
    if (page.dataset.financeReady === '1') return;
    page.dataset.financeReady = '1';
    toggle.checked = savedSelfOnly();

    function sync() {
      page.classList.toggle('finance-self-only', toggle.checked);
      toggle.setAttribute('aria-pressed', toggle.checked ? 'true' : 'false');
      persistSelfOnly(toggle.checked);
    }

    toggle.addEventListener('change', sync);
    sync();
  }

  function initBurnRate(section) {
    if (section.dataset.burnReady === '1') return;
    section.dataset.burnReady = '1';
    const cardsContainer = section.querySelector('[data-burn-rate-cards]');
    const grid = section.querySelector('.burn-rate-tx-grid[data-pdb-grid]');
    const status = section.querySelector('[data-burn-rate-status]');
    if (!cardsContainer || !grid) return;
    let activeBucket = '';
    let activeLabel = '';
    const store =
      window.pdbApp && typeof window.pdbApp.createStore === 'function'
        ? window.pdbApp.createStore({ activeBucket: '', activeLabel: '', burnRate: null })
        : null;

    function setIslandState(partial) {
      if (!store) return null;
      return store.setState((state) => ({ ...state, ...partial }));
    }

    function burnRateState() {
      return store ? store.getState().burnRate : null;
    }

    function activeBucketState() {
      const state = burnRateState();
      if (!state || !Array.isArray(state.buckets) || !activeBucket) return null;
      return state.buckets.find((item) => item.bucket === activeBucket) || null;
    }

    function escapeHtml(value) {
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function displayedCount() {
      return grid.__pdbGridApi && typeof grid.__pdbGridApi.getDisplayedRowCount === 'function'
        ? grid.__pdbGridApi.getDisplayedRowCount()
        : null;
    }

    function updateStatus() {
      if (!status) return;
      const bucketState = activeBucketState();
      const count = bucketState ? bucketState.count : displayedCount();
      const suffix = count == null ? '' : ` (${count} txns)`;
      status.textContent = activeBucket
        ? `Showing ${activeLabel || 'selected'} transactions${suffix}`
        : 'Showing all burn-rate transactions';
    }

    function colorOptions(selected) {
      return ['', 'red', 'orange', 'yellow', 'green', 'blue', 'purple', 'pink']
        .map((color) => {
          const label = color ? color[0].toUpperCase() + color.slice(1) : 'None';
          return `<option value="${escapeHtml(color)}"${color === selected ? ' selected' : ''}>${label}</option>`;
        })
        .join('');
    }

    function renderAddCard() {
      const action = escapeHtml(section.dataset.burnCreateBucketAction || '');
      return (
        '<div class="burn-rate-add" data-burn-add>' +
        '<button type="button" class="burn-rate-card burn-rate-add-button" data-burn-add-button ' +
        'aria-label="Add burn category">+</button>' +
        `<form class="burn-rate-add-form" method="post" action="${action}" data-burn-add-form hidden>` +
        '<input type="text" name="emoji" placeholder="Emoji" maxlength="12">' +
        '<input type="text" name="label" placeholder="New category" maxlength="40" required>' +
        `<select name="color" aria-label="Bucket color">${colorOptions('')}</select>` +
        '<button type="submit">add</button>' +
        '</form>' +
        '</div>'
      );
    }

    function renderCards(burnRate) {
      if (!burnRate || !Array.isArray(burnRate.buckets)) return;
      const days = burnRate.evidence_days || 90;
      const cardsHtml = burnRate.buckets
        .map((bucket) => {
          const color = bucket.color || '';
          const colorClass = color ? ' has-color' : '';
          const colorStyle = color ? ` style="--burn-bucket-color:${escapeHtml(color)}"` : '';
          const active = activeBucket && bucket.bucket === activeBucket;
          return (
            `<button type="button" class="burn-rate-card${colorClass}${active ? ' active' : ''}" ` +
            `data-burn-bucket="${escapeHtml(bucket.bucket)}" ` +
            `data-burn-label="${escapeHtml(bucket.display_label || bucket.label || bucket.bucket)}" ` +
            `aria-pressed="${active ? 'true' : 'false'}"${colorStyle}>` +
            `<span>${escapeHtml(bucket.display_label || bucket.label || bucket.bucket)}</span>` +
            `<strong>${escapeHtml(bucket.monthly_display || '$0')}</strong>` +
            `<small>smoothed / mo - ${Number(bucket.count || 0)} txns / ${days}d</small>` +
            '</button>'
          );
        })
        .join('');
      cardsContainer.innerHTML = cardsHtml + renderAddCard();
    }

    function renderBucketSelect(current, buckets) {
      const options = (buckets || [])
        .map((bucket) => {
          const value = bucket.bucket || '';
          const label = bucket.display_label || bucket.label || value;
          return `<option value="${escapeHtml(value)}"${value === current ? ' selected' : ''}>${escapeHtml(label)}</option>`;
        })
        .join('');
      const exclude = `<option value="exclude"${current === 'exclude' ? ' selected' : ''}>Exclude</option>`;
      return `<select name="bucket">${options}${exclude}</select>`;
    }

    function classificationForm(row, buckets) {
      const action = escapeHtml(section.dataset.burnClassificationAction || '');
      return (
        `<form class="burn-action" method="post" action="${action}">` +
        `<input type="hidden" name="finance_transaction_id" value="${escapeHtml(row.finance_transaction_id)}">` +
        `<input type="hidden" name="merchant" value="${escapeHtml(row.merchant)}">` +
        `<input type="hidden" name="source_category" value="${escapeHtml(row.category)}">` +
        renderBucketSelect(row.bucket, buckets) +
        '<select name="scope">' +
        '<option value="transaction">this txn</option>' +
        '<option value="merchant">merchant</option>' +
        '<option value="category">category</option>' +
        '</select>' +
        '<button type="submit">save</button>' +
        '</form>'
      );
    }

    function gridRows(burnRate) {
      const bucketLabels = new Map(
        (burnRate.buckets || []).map((bucket) => [
          bucket.bucket,
          bucket.display_label || bucket.label || bucket.bucket,
        ])
      );
      return (burnRate.rows || []).map((row) => ({
        __burnBucket: row.bucket,
        bucket: bucketLabels.get(row.bucket) || row.bucket,
        date: row.date || '',
        merchant: row.merchant || '',
        amount: row.amount_display || '',
        source_category: row.category || '',
        matched_rule: row.reason || '',
        classify: classificationForm(row, burnRate.buckets || []),
      }));
    }

    function renderGridRows(burnRate) {
      if (!grid.__pdbGridApi || !burnRate || !Array.isArray(burnRate.rows)) return;
      const rows = gridRows(burnRate);
      if (typeof grid.__pdbGridApi.setGridOption === 'function') {
        grid.__pdbGridApi.setGridOption('rowData', rows);
      } else if (typeof grid.__pdbGridApi.applyTransaction === 'function') {
        const existing = [];
        if (typeof grid.__pdbGridApi.forEachNode === 'function') {
          grid.__pdbGridApi.forEachNode((node) => existing.push(node.data));
        }
        if (existing.length) grid.__pdbGridApi.applyTransaction({ remove: existing });
        if (rows.length) grid.__pdbGridApi.applyTransaction({ add: rows });
      }
    }

    function applyBurnRateState(burnRate) {
      if (!burnRate || !Array.isArray(burnRate.buckets)) return;
      setIslandState({ burnRate });
      renderCards(burnRate);
      renderGridRows(burnRate);
      if (grid.__pdbGridApi && typeof grid.__pdbGridApi.onFilterChanged === 'function') {
        grid.__pdbGridApi.onFilterChanged();
      }
      updateStatus();
    }

    async function refreshBurnRateState() {
      if (!section.dataset.burnRateStateUrl || !window.pdbApp || !window.pdbApp.requestJson) {
        return;
      }
      try {
        applyBurnRateState(await window.pdbApp.requestJson(section.dataset.burnRateStateUrl));
      } catch (_error) {
        // Keep the server-rendered state if the richer state endpoint is unavailable.
      }
    }

    function updateCardCount(bucket, delta) {
      if (!bucket || !delta) return;
      const card = cardsContainer.querySelector(`[data-burn-bucket="${CSS.escape(bucket)}"]`);
      const small = card ? card.querySelector('small') : null;
      if (!small) return;
      small.textContent = small.textContent.replace(/(\d+)( txns \/ 90d)/, (_match, value, suffix) => {
        const next = Math.max(0, Number(value || 0) + delta);
        return `${next}${suffix}`;
      });
    }

    function showBucket(bucket, label) {
      const selected = bucket || '';
      activeBucket = selected;
      activeLabel = label || '';
      setIslandState({ activeBucket, activeLabel });
      cardsContainer.querySelectorAll('.burn-rate-card[data-burn-bucket]').forEach((card) => {
        const active = selected && card.dataset.burnBucket === selected;
        card.classList.toggle('active', Boolean(active));
        card.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      if (selected) {
        grid.dataset.pdbBucketFilter = selected;
      } else {
        delete grid.dataset.pdbBucketFilter;
      }
      if (grid.__pdbGridApi && typeof grid.__pdbGridApi.onFilterChanged === 'function') {
        grid.__pdbGridApi.onFilterChanged();
      }
      updateStatus();
    }

    cardsContainer.addEventListener('click', (event) => {
      const addButton = event.target.closest('[data-burn-add-button]');
      if (addButton) {
        const addForm = cardsContainer.querySelector('[data-burn-add-form]');
        addButton.hidden = true;
        if (addForm) {
          addForm.hidden = false;
          const input = addForm.querySelector('input[name="label"]');
          if (input) input.focus();
        }
        return;
      }
      const card = event.target.closest('.burn-rate-card[data-burn-bucket]');
      if (card) {
        const active = card.classList.contains('active');
        showBucket(active ? '' : card.dataset.burnBucket, card.dataset.burnLabel || 'selected');
      }
    });
    cardsContainer.addEventListener('submit', async (event) => {
      const form = event.target.closest('[data-burn-add-form]');
      if (!form) return;
      event.preventDefault();
      if (!window.pdbApp || !window.pdbApp.requestJson) {
        form.submit();
        return;
      }
      const button = form.querySelector('button[type="submit"], button:not([type])');
      if (button) button.textContent = 'adding';
      try {
        const data = Object.fromEntries(
          Array.from(new FormData(form).entries()).map(([key, value]) => [key, String(value)])
        );
        const result = await window.pdbApp.requestJson(form.action, { data });
        if (result && result.burn_rate) applyBurnRateState(result.burn_rate);
      } catch (_error) {
        if (button) button.textContent = 'retry';
      }
    });
    grid.addEventListener('pdb-burn-classified', (event) => {
      const detail = event.detail || {};
      const burnRate = detail.actionResult && detail.actionResult.burn_rate;
      if (burnRate) {
        applyBurnRateState(burnRate);
      } else if (detail.oldBucket !== detail.newBucket) {
        updateCardCount(detail.oldBucket, -1);
        updateCardCount(detail.newBucket, 1);
        refreshBurnRateState();
      }
      if (grid.__pdbGridApi && typeof grid.__pdbGridApi.onFilterChanged === 'function') {
        grid.__pdbGridApi.onFilterChanged();
      }
      updateStatus();
    });
    refreshBurnRateState();
  }

  function initAll() {
    document.querySelectorAll('[data-finance-page]').forEach((controls) => {
      const page = controls.closest('.app-page');
      if (page) initFinancePage(page);
    });
    if (window.pdbApp && typeof window.pdbApp.registerIsland === 'function') {
      if (!window.pdbApp.hasIsland || !window.pdbApp.hasIsland('finance-burn-rate')) {
        window.pdbApp.registerIsland('finance-burn-rate', initBurnRate);
      }
      window.pdbApp.mountIslands();
    } else {
      document.querySelectorAll('[data-burn-rate]').forEach(initBurnRate);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
  document.addEventListener('pdb:navigate', initAll);
})();
