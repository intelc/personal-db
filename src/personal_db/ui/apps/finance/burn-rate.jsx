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

function bucketLabel(bucket) {
  return bucket?.display_label || bucket?.label || bucket?.bucket || 'selected';
}

function BurnRateIsland({ section }) {
  const grid = section.querySelector('.burn-rate-tx-grid[data-pdb-grid]');
  const status = section.querySelector('[data-burn-rate-status]');
  const [burnRate, setBurnRate] = useState(null);
  const [activeBucket, setActiveBucket] = useState('');
  const [pending, setPending] = useState('');
  const [error, setError] = useState('');
  const [undo, setUndo] = useState(null);
  const requestJson = window.pdbApp?.requestJson;

  const buckets = burnRate?.buckets || [];
  const activeBucketState = useMemo(
    () => buckets.find((item) => item.bucket === activeBucket) || null,
    [activeBucket, buckets]
  );
  const activeLabel = bucketLabel(activeBucketState);

  const renderBucketSelect = useCallback(
    (current) => {
      const bucketOptions = (burnRate?.bucket_options || []).map((option) => {
        const selected = option.value === current ? ' selected' : '';
        return `<option value="${escapeHtml(option.value)}"${selected}>${escapeHtml(option.label)}</option>`;
      });
      return `<select name="bucket">${bucketOptions.join('')}</select>`;
    },
    [burnRate]
  );

  const renderScopeSelect = useCallback(
    () =>
      `<select name="scope">${(burnRate?.scope_options || [])
        .map((option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`)
        .join('')}</select>`,
    [burnRate]
  );

  const classificationForm = useCallback(
    (row) =>
      `<form class="burn-action" method="post" action="${escapeHtml(burnRate?.actions?.classify || '')}">` +
      `<input type="hidden" name="finance_transaction_id" value="${escapeHtml(row.finance_transaction_id)}">` +
      `<input type="hidden" name="merchant" value="${escapeHtml(row.merchant)}">` +
      `<input type="hidden" name="source_category" value="${escapeHtml(row.category)}">` +
      `<input type="hidden" name="old_bucket" value="${escapeHtml(row.bucket)}">` +
      renderBucketSelect(row.bucket) +
      renderScopeSelect() +
      '<button type="submit">save</button>' +
      '</form>',
    [burnRate, renderBucketSelect, renderScopeSelect]
  );

  const gridRows = useMemo(() => {
    const bucketLabels = new Map(buckets.map((bucket) => [bucket.bucket, bucketLabel(bucket)]));
    return (burnRate?.rows || []).map((row) => ({
      __burnBucket: row.bucket,
      bucket: bucketLabels.get(row.bucket) || row.bucket,
      date: row.date || '',
      merchant: row.merchant || '',
      amount: row.amount_display || '',
      source_category: row.category || '',
      matched_rule: row.reason || '',
      classify: classificationForm(row),
    }));
  }, [buckets, burnRate, classificationForm]);

  const applyGridRows = useCallback(() => {
    if (!grid?.__pdbGridApi || !burnRate) return;
    if (typeof grid.__pdbGridApi.setGridOption === 'function') {
      grid.__pdbGridApi.setGridOption('rowData', gridRows);
    } else if (typeof grid.__pdbGridApi.applyTransaction === 'function') {
      const existing = [];
      if (typeof grid.__pdbGridApi.forEachNode === 'function') {
        grid.__pdbGridApi.forEachNode((node) => existing.push(node.data));
      }
      if (existing.length) grid.__pdbGridApi.applyTransaction({ remove: existing });
      if (gridRows.length) grid.__pdbGridApi.applyTransaction({ add: gridRows });
    }
    if (typeof grid.__pdbGridApi.onFilterChanged === 'function') {
      grid.__pdbGridApi.onFilterChanged();
    }
  }, [burnRate, grid, gridRows]);

  const updateStatus = useCallback(() => {
    if (!status) return;
    if (!activeBucket) {
      status.textContent = pending || error || 'Showing all burn-rate transactions';
      return;
    }
    const count = activeBucketState?.count;
    const suffix = count == null ? '' : ` (${count} txns)`;
    const prefix = pending || error || `Showing ${activeLabel} transactions`;
    status.textContent = `${prefix}${pending || error ? '' : suffix}`;
  }, [activeBucket, activeBucketState, activeLabel, error, pending, status]);

  const refresh = useCallback(async () => {
    if (!requestJson || !section.dataset.burnRateStateUrl) return;
    setError('');
    setBurnRate(await requestJson(section.dataset.burnRateStateUrl));
  }, [requestJson, section]);

  useEffect(() => {
    refresh().catch((err) => setError(err.message || 'Failed to load burn rate'));
  }, [refresh]);

  useEffect(() => {
    applyGridRows();
    updateStatus();
  }, [applyGridRows, updateStatus]);

  useEffect(() => {
    if (!grid) return undefined;
    const onReady = () => applyGridRows();
    grid.addEventListener('pdb-grid-ready', onReady);
    return () => grid.removeEventListener('pdb-grid-ready', onReady);
  }, [applyGridRows, grid]);

  useEffect(() => {
    if (!grid) return undefined;
    if (activeBucket) {
      grid.dataset.pdbBucketFilter = activeBucket;
    } else {
      delete grid.dataset.pdbBucketFilter;
    }
    if (grid.__pdbGridApi?.onFilterChanged) {
      grid.__pdbGridApi.onFilterChanged();
    }
    updateStatus();
    return undefined;
  }, [activeBucket, grid, updateStatus]);

  const submitClassification = useCallback(
    async (form) => {
      if (!requestJson) {
        form.submit();
        return;
      }
      const payload = formObject(form);
      const oldBucket = payload.old_bucket || '';
      delete payload.old_bucket;
      const select = form.querySelector('select[name="bucket"]');
      const nextLabel =
        select && select.selectedOptions.length ? select.selectedOptions[0].textContent : payload.bucket;
      const button = form.querySelector('button[type="submit"], button:not([type])');
      if (button) {
        button.disabled = true;
        button.textContent = 'saving';
      }
      setPending(`Saving ${nextLabel || 'selected'}...`);
      setError('');
      try {
        const result = await requestJson(form.action, { data: payload });
        if (result?.burn_rate) {
          setBurnRate(result.burn_rate);
        } else {
          await refresh();
        }
        setUndo({
          transactionId: payload.finance_transaction_id || '',
          oldBucket,
          newBucket: payload.bucket || '',
        });
        setPending('');
        if (button) {
          button.disabled = false;
          button.textContent = 'saved';
          window.setTimeout(() => {
            button.textContent = 'save';
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
      const form = event.target?.closest?.('.burn-action');
      if (!form || !section.contains(form)) return;
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') {
        event.stopImmediatePropagation();
      }
      submitClassification(form);
    };
    section.addEventListener('submit', onSubmit, true);
    return () => section.removeEventListener('submit', onSubmit, true);
  }, [section, submitClassification]);

  async function addBucket(event) {
    event.preventDefault();
    if (!requestJson || !burnRate?.actions?.create_bucket) {
      event.currentTarget.submit();
      return;
    }
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"], button:not([type])');
    if (button) button.textContent = 'adding';
    setPending('Adding category...');
    setError('');
    try {
      const result = await requestJson(burnRate.actions.create_bucket, { data: formObject(form) });
      if (result?.burn_rate) setBurnRate(result.burn_rate);
      setPending('');
    } catch (err) {
      setPending('');
      setError(err.message || 'Add failed');
      if (button) button.textContent = 'retry';
    }
  }

  async function undoLast() {
    if (!undo?.transactionId || !undo.oldBucket || !requestJson || !burnRate?.actions?.classify) return;
    setPending('Undoing...');
    setError('');
    try {
      const result = await requestJson(burnRate.actions.classify, {
        data: {
          finance_transaction_id: undo.transactionId,
          bucket: undo.oldBucket,
          scope: 'transaction',
        },
      });
      if (result?.burn_rate) setBurnRate(result.burn_rate);
      setUndo(null);
      setPending('');
    } catch (err) {
      setPending('');
      setError(err.message || 'Undo failed');
    }
  }

  function colorOptions() {
    return (burnRate?.color_options || [{ value: '', label: 'None' }]).map((option) => (
      <option key={option.value} value={option.value}>
        {option.label}
      </option>
    ));
  }

  return (
    <>
      {buckets.map((bucket) => {
        const active = activeBucket === bucket.bucket;
        const style = bucket.color ? { '--burn-bucket-color': bucket.color } : undefined;
        return (
          <button
            aria-pressed={active ? 'true' : 'false'}
            className={`burn-rate-card${bucket.color ? ' has-color' : ''}${active ? ' active' : ''}`}
            data-burn-bucket={bucket.bucket}
            data-burn-label={bucketLabel(bucket)}
            key={bucket.bucket}
            onClick={() => setActiveBucket(active ? '' : bucket.bucket)}
            style={style}
            type="button"
          >
            <span>{bucketLabel(bucket)}</span>
            <strong>{bucket.monthly_display || '$0'}</strong>
            <small>
              smoothed / mo - {Number(bucket.count || 0)} txns / {burnRate?.evidence_days || 90}d
            </small>
          </button>
        );
      })}
      <div className="burn-rate-add" data-burn-add>
        <button
          aria-label="Add burn category"
          className="burn-rate-card burn-rate-add-button"
          data-burn-add-button
          onClick={(event) => {
            event.currentTarget.hidden = true;
            const form = event.currentTarget.parentElement?.querySelector('[data-burn-add-form]');
            if (form) {
              form.hidden = false;
              form.querySelector('input[name="label"]')?.focus();
            }
          }}
          type="button"
        >
          +
        </button>
        <form
          action={burnRate?.actions?.create_bucket || section.dataset.burnCreateBucketAction || ''}
          className="burn-rate-add-form"
          data-burn-add-form
          hidden
          method="post"
          onSubmit={addBucket}
        >
          <input maxLength="12" name="emoji" placeholder="Emoji" type="text" />
          <input maxLength="40" name="label" placeholder="New category" required type="text" />
          <select aria-label="Bucket color" name="color">
            {colorOptions()}
          </select>
          <button type="submit">add</button>
        </form>
      </div>
      {(pending || error || undo) && (
        <div className="burn-rate-feedback">
          {pending && <span>{pending}</span>}
          {error && <span>{error}</span>}
          {!pending && !error && undo && (
            <>
              <span>Saved.</span>
              <button onClick={undoLast} type="button">
                undo
              </button>
            </>
          )}
        </div>
      )}
    </>
  );
}

function mount(section) {
  if (section.dataset.burnReactReady === '1') return;
  const cards = section.querySelector('[data-burn-rate-cards]');
  if (!cards) return;
  section.dataset.burnReactReady = '1';
  createRoot(cards).render(<BurnRateIsland section={section} />);
}

if (window.pdbApp?.registerIsland) {
  window.pdbApp.registerIsland('finance-burn-rate', mount);
  window.pdbApp.mountIslands();
}
