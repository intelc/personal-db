(() => {
  function initDashboard(dashboard) {
    const toggle = dashboard.querySelector('[data-finance-self-only]');
    if (!toggle) return;
    if (dashboard.dataset.financeReady === '1') return;
    dashboard.dataset.financeReady = '1';

    function sync() {
      dashboard.classList.toggle('finance-self-only', toggle.checked);
      toggle.setAttribute('aria-pressed', toggle.checked ? 'true' : 'false');
    }

    toggle.addEventListener('change', sync);
    sync();
  }

  function initBurnRate(section) {
    if (section.dataset.burnReady === '1') return;
    section.dataset.burnReady = '1';
    const cards = Array.from(section.querySelectorAll('.burn-rate-card[data-burn-bucket]'));
    const grid = section.querySelector('.burn-rate-tx-grid[data-pdb-grid]');
    const status = section.querySelector('[data-burn-rate-status]');
    const addButton = section.querySelector('[data-burn-add-button]');
    const addForm = section.querySelector('[data-burn-add-form]');
    if (!cards.length || !grid) return;
    let activeBucket = '';
    let activeLabel = '';

    function displayedCount() {
      return grid.__pdbGridApi && typeof grid.__pdbGridApi.getDisplayedRowCount === 'function'
        ? grid.__pdbGridApi.getDisplayedRowCount()
        : null;
    }

    function updateStatus() {
      if (!status) return;
      const count = displayedCount();
      const suffix = count == null ? '' : ` (${count} txns)`;
      status.textContent = activeBucket
        ? `Showing ${activeLabel || 'selected'} transactions${suffix}`
        : 'Showing all burn-rate transactions';
    }

    function updateCardCount(bucket, delta) {
      if (!bucket || !delta) return;
      const card = cards.find((item) => item.dataset.burnBucket === bucket);
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
      cards.forEach((card) => {
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

    cards.forEach((card) => {
      card.addEventListener('click', () => {
        const active = card.classList.contains('active');
        showBucket(active ? '' : card.dataset.burnBucket, card.dataset.burnLabel || 'selected');
      });
    });
    if (addButton && addForm) {
      addButton.addEventListener('click', () => {
        addButton.hidden = true;
        addForm.hidden = false;
        const input = addForm.querySelector('input[name="label"]');
        if (input) input.focus();
      });
    }
    grid.addEventListener('pdb-burn-classified', (event) => {
      const detail = event.detail || {};
      if (detail.oldBucket !== detail.newBucket) {
        updateCardCount(detail.oldBucket, -1);
        updateCardCount(detail.newBucket, 1);
      }
      if (grid.__pdbGridApi && typeof grid.__pdbGridApi.onFilterChanged === 'function') {
        grid.__pdbGridApi.onFilterChanged();
      }
      updateStatus();
    });
  }

  function initAll() {
    document.querySelectorAll('[data-finance-dashboard]').forEach(initDashboard);
    document.querySelectorAll('[data-burn-rate]').forEach(initBurnRate);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
})();
