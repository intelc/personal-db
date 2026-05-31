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

    function applyBurnRateState(burnRate) {
      if (!burnRate || !Array.isArray(burnRate.buckets)) return;
      setIslandState({ burnRate });
      burnRate.buckets.forEach((bucketState) => {
        const card = cards.find((item) => item.dataset.burnBucket === bucketState.bucket);
        if (!card) return;
        const strong = card.querySelector('strong');
        const small = card.querySelector('small');
        if (strong && bucketState.monthly_display) strong.textContent = bucketState.monthly_display;
        if (small) {
          const days = burnRate.evidence_days || 90;
          small.textContent = `smoothed / mo - ${bucketState.count || 0} txns / ${days}d`;
        }
      });
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
      setIslandState({ activeBucket, activeLabel });
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
    document.querySelectorAll('[data-finance-dashboard]').forEach(initDashboard);
    if (window.pdbApp && typeof window.pdbApp.registerIsland === 'function') {
      window.pdbApp.registerIsland('finance-burn-rate', initBurnRate);
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
})();
