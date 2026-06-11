import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';

function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formObject(form) {
  return Object.fromEntries(
    Array.from(new FormData(form).entries()).map(([key, value]) => [key, String(value)])
  );
}

function setGridRows(grid, rows) {
  if (!grid?.__pdbGridApi) return;
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

function FinanceCategorizeIsland({ section }) {
  const statusEl = section.querySelector('[data-finance-categorize-status]');
  const grids = useMemo(
    () => ({
      transactions: section.querySelector('.finance-categorize-transactions [data-pdb-grid]'),
      parentDraws: section.querySelector('.finance-categorize-parent-draws [data-pdb-grid]'),
      recurring: section.querySelector('.finance-categorize-recurring [data-pdb-grid]'),
    }),
    [section]
  );
  const requestJson = window.pdbApp?.requestJson;
  const [state, setState] = useState(null);
  const [pending, setPending] = useState('');
  const [error, setError] = useState('');
  const [saved, setSaved] = useState('');

  const refresh = useCallback(async () => {
    if (!requestJson || !section.dataset.categorizeStateUrl) return;
    setState(await requestJson(section.dataset.categorizeStateUrl));
  }, [requestJson, section]);

  const categoryOptions = useMemo(
    () =>
      (state?.category_presets || [])
        .map((category) => `<option value="${escapeHtml(category)}"></option>`)
        .join(''),
    [state]
  );

  const categoryControls = useCallback(
    (row) => {
      const setAction = escapeHtml(state?.actions?.set_category || '');
      const clearAction = escapeHtml(state?.actions?.clear_category || '');
      const id = escapeHtml(row.finance_transaction_id || '');
      const current = escapeHtml(row.app_category || '');
      const setForm =
        `<form class="category-action" method="post" action="${setAction}">` +
        `<input type="hidden" name="finance_transaction_id" value="${id}">` +
        `<input type="text" name="category" value="${current}" list="finance-category-presets" placeholder="category">` +
        '<button type="submit">save</button></form>';
      const clearForm = row.app_category
        ? `<form class="category-action" method="post" action="${clearAction}">` +
          `<input type="hidden" name="finance_transaction_id" value="${id}">` +
          '<button type="submit">clear</button></form>'
        : '';
      return `<div class="category-actions">${setForm}${clearForm}</div>`;
    },
    [state]
  );

  const reviewControls = useCallback(
    (row) => {
      const markAction = escapeHtml(state?.actions?.mark_reviewed || '');
      const clearAction = escapeHtml(state?.actions?.clear_review || '');
      const key = escapeHtml(row.review_key || '');
      const kind = escapeHtml(row.kind || '');
      const reviewed =
        `<form class="review-action" method="post" action="${markAction}">` +
        `<input type="hidden" name="review_key" value="${key}">` +
        `<input type="hidden" name="kind" value="${kind}">` +
        '<input type="hidden" name="status" value="reviewed">' +
        '<button type="submit">reviewed</button></form>';
      const ignored =
        `<form class="review-action" method="post" action="${markAction}">` +
        `<input type="hidden" name="review_key" value="${key}">` +
        `<input type="hidden" name="kind" value="${kind}">` +
        '<input type="hidden" name="status" value="ignored">' +
        '<button type="submit">ignore</button></form>';
      const clear = row.status
        ? `<form class="review-action" method="post" action="${clearAction}">` +
          `<input type="hidden" name="review_key" value="${key}">` +
          '<button type="submit">clear</button></form>'
        : '';
      return `<div class="review-actions">${reviewed}${ignored}${clear}</div>`;
    },
    [state]
  );

  const transactionRows = useMemo(
    () =>
      (state?.transactions || []).map((row) => ({
        c0: row.date || '',
        c1: row.merchant || '',
        c2: row.owner || '',
        c3: row.source || '',
        c4: row.amount_display || '',
        c5: row.source_category || '',
        c6: row.app_category || '',
        c7: categoryControls(row),
      })),
    [categoryControls, state]
  );

  const parentRows = useMemo(
    () =>
      (state?.parent_draws || []).map((row) => ({
        c0: row.date || '',
        c1: row.institution || '',
        c2: row.account_name || '',
        c3: row.merchant || '',
        c4: row.amount_display || '',
        c5: row.category || '',
        c6: row.status_label || 'Needs review',
        c7: reviewControls(row),
      })),
    [reviewControls, state]
  );

  const recurringRows = useMemo(
    () =>
      (state?.recurring || []).map((row) => ({
        c0: row.merchant || '',
        c1: row.owner || '',
        c2: String(row.txn_count || ''),
        c3: row.avg_amount_display || '',
        c4: row.first_seen || '',
        c5: row.last_seen || '',
        c6: row.category || '',
        c7: row.status_label || 'Needs review',
        c8: reviewControls(row),
      })),
    [reviewControls, state]
  );

  const applyGridRows = useCallback(() => {
    if (!state) return;
    setGridRows(grids.transactions, transactionRows);
    setGridRows(grids.parentDraws, parentRows);
    setGridRows(grids.recurring, recurringRows);
  }, [grids, parentRows, recurringRows, state, transactionRows]);

  useEffect(() => {
    refresh().catch((err) => setError(err.message || 'Failed to load categorization state'));
  }, [refresh]);

  useEffect(() => {
    applyGridRows();
    const datalist = document.getElementById('finance-category-presets');
    if (datalist) datalist.innerHTML = categoryOptions;
  }, [applyGridRows, categoryOptions]);

  useEffect(() => {
    const ready = () => applyGridRows();
    Object.values(grids).forEach((grid) => grid?.addEventListener('pdb-grid-ready', ready));
    return () => {
      Object.values(grids).forEach((grid) => grid?.removeEventListener('pdb-grid-ready', ready));
    };
  }, [applyGridRows, grids]);

  const submitInlineAction = useCallback(
    async (form) => {
      if (!requestJson) {
        form.submit();
        return;
      }
      const isCategory = form.classList.contains('category-action');
      const payload = formObject(form);
      const input = form.querySelector('input[name="category"]');
      if (input && !String(payload.category || '').trim()) {
        input.focus();
        return;
      }
      const button = form.querySelector('button[type="submit"], button:not([type])');
      const originalText = button ? button.textContent : '';
      if (button) {
        button.disabled = true;
        button.textContent = 'saving';
      }
      setPending(isCategory ? 'Saving category...' : 'Saving review...');
      setSaved('');
      setError('');
      try {
        await requestJson(form.action, { data: payload });
        await refresh();
        setPending('');
        setSaved(isCategory ? 'Saved category.' : 'Saved review.');
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
    [refresh, requestJson]
  );

  useEffect(() => {
    const onSubmit = (event) => {
      const form = event.target?.closest?.('.category-action, .review-action');
      if (!form || !section.contains(form)) return;
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') {
        event.stopImmediatePropagation();
      }
      submitInlineAction(form);
    };
    section.addEventListener('submit', onSubmit, true);
    return () => section.removeEventListener('submit', onSubmit, true);
  }, [section, submitInlineAction]);

  if (!statusEl) return null;

  return (
    <>
      {pending && <span>{pending}</span>}
      {error && <span>{error}</span>}
      {!pending && !error && saved && <span>{saved}</span>}
    </>
  );
}

function mount(section) {
  if (section.dataset.categorizeReactReady === '1') return;
  const status = section.querySelector('[data-finance-categorize-status]');
  if (!status) return;
  section.dataset.categorizeReactReady = '1';
  createRoot(status).render(<FinanceCategorizeIsland section={section} />);
}

if (window.pdbApp?.registerIsland) {
  window.pdbApp.registerIsland('finance-categorize', mount);
  window.pdbApp.mountIslands();
}
