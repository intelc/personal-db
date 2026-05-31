(() => {
  function initDashboard(dashboard) {
    const toggle = dashboard.querySelector('[data-finance-self-only]');
    if (!toggle) return;
    function sync() {
      dashboard.classList.toggle('finance-self-only', toggle.checked);
      toggle.setAttribute('aria-pressed', toggle.checked ? 'true' : 'false');
    }
    toggle.addEventListener('change', sync);
    sync();
  }

  function initAll() {
    document.querySelectorAll('[data-finance-dashboard]').forEach(initDashboard);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
})();
