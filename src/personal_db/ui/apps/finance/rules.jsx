import React, { useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';

function formObject(form) {
  return Object.fromEntries(
    Array.from(new FormData(form).entries()).map(([key, value]) => [key, String(value)])
  );
}

function colorLabel(value) {
  if (!value) return 'None';
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function FinanceRulesIsland({ section }) {
  const requestJson = window.pdbApp?.requestJson;
  const [pending, setPending] = useState('');
  const [error, setError] = useState('');
  const [saved, setSaved] = useState('');

  const submitColor = useCallback(
    async (form) => {
      if (!requestJson) {
        form.submit();
        return;
      }
      const payload = formObject(form);
      const button = form.querySelector('button[type="submit"], button:not([type])');
      const originalText = button ? button.textContent : '';
      if (button) {
        button.disabled = true;
        button.textContent = 'saving';
      }
      setPending('Saving bucket...');
      setSaved('');
      setError('');
      try {
        const result = await requestJson(form.action, { data: payload });
        const row = form.closest('tr');
        if (row?.children?.[0]) row.children[0].textContent = result.emoji || payload.emoji || '';
        if (row?.children?.[2]) row.children[2].textContent = colorLabel(result.color || payload.color || '');
        setPending('');
        setSaved('Saved bucket.');
        if (button) {
          button.disabled = false;
          button.textContent = 'saved';
          window.setTimeout(() => {
            button.textContent = originalText || 'save';
          }, 900);
        }
      } catch (err) {
        setPending('');
        setError(err.message || 'Save failed');
        if (button) {
          button.disabled = false;
          button.textContent = 'retry';
        }
      }
    },
    [requestJson]
  );

  useEffect(() => {
    const onSubmit = (event) => {
      const form = event.target?.closest?.('.burn-bucket-color-form');
      if (!form || !section.contains(form)) return;
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') {
        event.stopImmediatePropagation();
      }
      submitColor(form);
    };
    section.addEventListener('submit', onSubmit, true);
    return () => section.removeEventListener('submit', onSubmit, true);
  }, [section, submitColor]);

  return (
    <>
      {pending && <span>{pending}</span>}
      {error && <span>{error}</span>}
      {!pending && !error && saved && <span>{saved}</span>}
    </>
  );
}

function mount(section) {
  if (section.dataset.rulesReactReady === '1') return;
  const status = section.querySelector('[data-finance-rules-status]');
  if (!status) return;
  section.dataset.rulesReactReady = '1';
  createRoot(status).render(<FinanceRulesIsland section={section} />);
}

if (window.pdbApp?.registerIsland) {
  window.pdbApp.registerIsland('finance-rules', mount);
  window.pdbApp.mountIslands();
}
