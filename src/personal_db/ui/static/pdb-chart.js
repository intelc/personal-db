(() => {
  function money(value) {
    const n = Number(value || 0);
    const sign = n < 0 ? '-' : '';
    return `${sign}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  }

  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function monthName(value) {
    const match = /^(?:\d{4}-)?(\d{2})-(\d{2})$/.exec(String(value || ''));
    if (!match) return null;
    const month = Number(match[1]);
    return MONTHS[month - 1] || null;
  }

  function parseDate(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''));
    if (!match) return null;
    const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function isoDate(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  }

  function monthDay(date) {
    return `${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  }

  function startOfWeek(date) {
    const out = new Date(date);
    const offset = (out.getDay() + 6) % 7;
    out.setDate(out.getDate() - offset);
    return out;
  }

  function monthNameFromDate(date) {
    return MONTHS[date.getMonth()] || null;
  }

  function groupInfo(date, mode) {
    if (mode === 'month') {
      const start = new Date(date.getFullYear(), date.getMonth(), 1);
      return {
        key: `${start.getFullYear()}-${String(start.getMonth() + 1).padStart(2, '0')}`,
        date: isoDate(start),
        label: `${MONTHS[start.getMonth()]} ${start.getFullYear()}`,
      };
    }
    if (mode === 'week') {
      const start = startOfWeek(date);
      return {
        key: isoDate(start),
        date: isoDate(start),
        label: monthDay(start),
      };
    }
    return null;
  }

  function aggregationModes(rawOptions) {
    const aggregation = rawOptions.pdbAggregation || {};
    const modes = Array.isArray(aggregation.modes) ? aggregation.modes : ['day', 'week', 'month'];
    return aggregation.enabled ? modes.filter((mode) => ['day', 'week', 'month'].includes(mode)) : [];
  }

  function aggregateData(rawOptions, data, mode) {
    const aggregation = rawOptions.pdbAggregation || {};
    if (!aggregation.enabled || mode === 'day') return data;
    const dateKey = aggregation.dateKey || 'date';
    const sumKeys = Array.isArray(aggregation.sumKeys) ? aggregation.sumKeys : ['net'];
    const groups = new Map();
    for (const row of data) {
      const date = parseDate(row[dateKey]);
      if (!date) return data;
      const info = groupInfo(date, mode);
      if (!info) return data;
      if (!groups.has(info.key)) {
        const grouped = { x: info.label, [dateKey]: info.date };
        for (const key of sumKeys) grouped[key] = 0;
        groups.set(info.key, grouped);
      }
      const grouped = groups.get(info.key);
      for (const key of sumKeys) {
        const value = Number(row[key]);
        if (Number.isFinite(value)) grouped[key] += value;
      }
    }
    const out = Array.from(groups.values());
    if (aggregation.deriveGainLoss) {
      for (const row of out) {
        const value = Number(row.net || 0);
        row.gain = value > 0 ? value : 0;
        row.loss = value < 0 ? value : 0;
      }
    }
    return out;
  }

  function applyFormatters(options) {
    if (options.valueFormat !== 'usd') return options;
    const out = { ...options };
    out.axes = Object.fromEntries(
      Object.entries(out.axes || {}).map(([position, axis]) => [
        position,
        axis.type === 'number'
          ? { ...axis, label: { ...(axis.label || {}), formatter: ({ value }) => money(value) } }
          : axis,
      ])
    );
    out.series = (out.series || []).map((series) => ({
      ...series,
      tooltip: series.type === 'pie'
        ? series.tooltip
        : {
            ...(series.tooltip || {}),
            renderer: ({ datum, xKey, yKey, yName }) => ({
              title: yName || yKey,
              data: [`${datum[xKey]}: ${money(datum[yKey])}`],
            }),
          },
      sectorLabel: series.type === 'pie'
        ? { ...(series.sectorLabel || {}), formatter: ({ value }) => money(value) }
        : series.sectorLabel,
    }));
    delete out.valueFormat;
    return out;
  }

  function percentile(values, q) {
    if (!values.length) return null;
    const sorted = [...values].sort((a, b) => a - b);
    const index = (sorted.length - 1) * q;
    const low = Math.floor(index);
    const high = Math.ceil(index);
    if (low === high) return sorted[low];
    return sorted[low] + (sorted[high] - sorted[low]) * (index - low);
  }

  function numericValues(options, data) {
    const yKeys = (options.series || [])
      .filter((series) => series.type !== 'pie' && series.yKey)
      .map((series) => series.yKey);
    return data.flatMap((row) => (
      yKeys.map((key) => Number(row[key])).filter((value) => Number.isFinite(value))
    ));
  }

  function focusedDomain(rawOptions, data) {
    const scale = rawOptions.pdbScale || {};
    if (!scale.enabled || data.length < 12) return null;
    const values = numericValues(rawOptions, data);
    if (values.length < 12) return null;

    const fullMin = Math.min(...values);
    const fullMax = Math.max(...values);
    const fullRange = fullMax - fullMin;
    if (!Number.isFinite(fullRange) || fullRange <= 0) return null;

    const lower = percentile(values, Number(scale.lowerQuantile ?? 0.05));
    const upper = percentile(values, Number(scale.upperQuantile ?? 0.95));
    if (lower == null || upper == null || upper <= lower) return null;

    const robustRange = upper - lower;
    if (fullRange < robustRange * 1.8) return null;

    const padding = Math.max(robustRange * 0.1, fullRange * 0.01, 1);
    return { min: lower - padding, max: upper + padding };
  }

  function monthMarkers(rawOptions, data) {
    const markers = rawOptions.pdbTimeMarkers || {};
    if (!markers.enabled || !markers.monthBoundaries || data.length < 2) return null;
    const xKey = markers.xKey || 'x';
    const dateKey = markers.dateKey || null;
    const boundaries = [];
    let previousMonth = null;
    for (const row of data) {
      const value = row[xKey];
      const date = dateKey ? parseDate(row[dateKey]) : null;
      const month = date ? monthNameFromDate(date) : monthName(value);
      if (!month) return null;
      if (month !== previousMonth) {
        boundaries.push({ value, month, first: previousMonth == null });
        previousMonth = month;
      }
    }
    if (boundaries.length < 2) return null;
    return boundaries;
  }

  function applyTimeMarkers(rawOptions, data, options) {
    const boundaries = monthMarkers(rawOptions, data);
    if (!boundaries || !options.axes || !options.axes.bottom) return options;

    const monthLabels = Object.fromEntries(boundaries.map(({ value, month }) => [value, month]));
    const crossLines = boundaries
      .filter((boundary) => !boundary.first)
      .map(({ value }) => ({
        type: 'line',
        value,
        stroke: '#b8bec4',
        strokeWidth: 1,
        lineDash: [3, 3],
      }));
    return {
      ...options,
      axes: {
        ...options.axes,
        bottom: {
          ...options.axes.bottom,
          label: {
            ...(options.axes.bottom.label || {}),
            formatter: ({ value }) => monthLabels[value] || String(value),
          },
          crossLines,
        },
      },
    };
  }

  function stripAxes(options) {
    if (!options.axes) return options;
    const { axes, ...out } = options;
    return out;
  }

  function chartOptions(rawOptions, data, scaleMode, groupMode) {
    const formatted = applyFormatters({ ...rawOptions, data });
    let { pdbZoom, pdbScale, pdbTimeMarkers, pdbAggregation, valueFormat, ...options } = formatted;
    if (scaleMode === 'focus') {
      const domain = focusedDomain(rawOptions, data);
      if (domain && options.axes && options.axes.left) {
        options.axes = {
          ...options.axes,
          left: { ...options.axes.left, min: domain.min, max: domain.max },
        };
      }
    }
    if (groupMode !== 'month') {
      options = applyTimeMarkers(rawOptions, data, options);
    }
    return stripAxes(options);
  }

  function zoomWindows(rawOptions) {
    const data = Array.isArray(rawOptions.data) ? rawOptions.data : [];
    const zoom = rawOptions.pdbZoom || {};
    if (!zoom.enabled || data.length < 2) return [];
    const configured = Array.isArray(zoom.windows) ? zoom.windows : [365, 180, 90, 30, 7];
    return configured
      .map((value) => Number(value))
      .filter((value, index, all) => (
        Number.isFinite(value)
        && value > 0
        && value < data.length
        && all.indexOf(value) === index
      ));
  }

  function zoomLabel(value) {
    if (value >= 365 && value % 365 === 0) return `${value / 365}Y`;
    if (value >= 30 && value % 30 === 0) return `${value / 30}M`;
    return `${value}`;
  }

  function hasScaleFocus(rawOptions, data) {
    return Boolean(focusedDomain(rawOptions, data));
  }

  function renderChartToolbar(rawOptions, state, render) {
    const windows = zoomWindows(rawOptions);
    const data = Array.isArray(rawOptions.data) ? rawOptions.data : [];
    const showScale = hasScaleFocus(rawOptions, data);
    const groups = aggregationModes(rawOptions);
    const showGroups = groups.length > 1;
    if (!windows.length && !showScale && !showGroups) return null;

    const toolbar = document.createElement('div');
    toolbar.className = 'pdb-chart-toolbar';
    toolbar.setAttribute('aria-label', 'Chart controls');

    const choices = [{ label: 'All', value: null }].concat(
      windows.map((value) => ({ label: zoomLabel(value), value }))
    );
    const defaultWindow = windows.includes(Number(rawOptions.pdbZoom && rawOptions.pdbZoom.defaultWindow))
      ? Number(rawOptions.pdbZoom.defaultWindow)
      : null;
    state.window = defaultWindow;
    const zoomButtons = choices.map((choice) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = choice.label;
      button.dataset.window = choice.value == null ? 'all' : String(choice.value);
      button.addEventListener('click', () => {
        state.window = choice.value;
        zoomButtons.forEach((other) => other.classList.toggle('active', other === button));
        render();
      });
      toolbar.appendChild(button);
      return button;
    });
    const activeZoom = zoomButtons.find((button) => (
      button.dataset.window === (defaultWindow == null ? 'all' : String(defaultWindow))
    ));
    if (activeZoom) activeZoom.classList.add('active');

    if (showGroups) {
      const divider = document.createElement('span');
      divider.className = 'pdb-chart-toolbar-divider';
      toolbar.appendChild(divider);

      const labels = { day: 'Day', week: 'Week', month: 'Month' };
      const defaultGroup = groups.includes(rawOptions.pdbAggregation && rawOptions.pdbAggregation.defaultMode)
        ? rawOptions.pdbAggregation.defaultMode
        : groups[0];
      state.group = defaultGroup;
      const groupButtons = groups.map((mode) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = labels[mode] || mode;
        button.dataset.group = mode;
        button.addEventListener('click', () => {
          state.group = mode;
          groupButtons.forEach((other) => other.classList.toggle('active', other === button));
          render();
        });
        toolbar.appendChild(button);
        return button;
      });
      const activeGroup = groupButtons.find((button) => button.dataset.group === defaultGroup);
      if (activeGroup) activeGroup.classList.add('active');
    }

    if (showScale) {
      const divider = document.createElement('span');
      divider.className = 'pdb-chart-toolbar-divider';
      toolbar.appendChild(divider);

      const full = document.createElement('button');
      full.type = 'button';
      full.textContent = 'Full';
      const focus = document.createElement('button');
      focus.type = 'button';
      focus.textContent = 'Focus';
      function setScale(mode) {
        state.scale = mode;
        full.classList.toggle('active', mode === 'full');
        focus.classList.toggle('active', mode === 'focus');
        render();
      }
      full.addEventListener('click', () => setScale('full'));
      focus.addEventListener('click', () => setScale('focus'));
      toolbar.appendChild(full);
      toolbar.appendChild(focus);
      const configuredScale = rawOptions.pdbScale && rawOptions.pdbScale.defaultMode;
      const defaultScale = ['full', 'focus'].includes(configuredScale)
        ? configuredScale
        : rawOptions.pdbScale && rawOptions.pdbScale.mode === 'auto'
          ? 'focus'
          : 'full';
      state.scale = defaultScale;
      full.classList.toggle('active', defaultScale === 'full');
      focus.classList.toggle('active', defaultScale === 'focus');
    }
    return toolbar;
  }

  function initChart(el) {
    if (el.dataset.pdbChartReady === '1') return;
    const script = document.querySelector(
      `script[data-pdb-chart-options="${CSS.escape(el.id)}"]`
    );
    if (!script) return;
    const api = window.agCharts && window.agCharts.AgCharts;
    if (!api || !api.create) {
      el.textContent = 'AG Charts failed to load';
      el.classList.add('pdb-chart-error');
      return;
    }
    const rawOptions = JSON.parse(script.textContent || '{}');
    let chart = null;
    const state = { window: null, scale: 'full', group: 'day' };
    function visibleData() {
      const data = Array.isArray(rawOptions.data) ? rawOptions.data : [];
      return state.window == null ? data : data.slice(-state.window);
    }
    function render() {
      const data = aggregateData(rawOptions, visibleData(), state.group);
      const options = chartOptions(rawOptions, data, state.scale, state.group);
      const next = { ...options, container: el };
      if (chart && api.update) {
        api.update(chart, next);
      } else {
        if (chart && chart.destroy) chart.destroy();
        chart = api.create(next);
        el.__pdbChart = chart;
      }
    }

    const toolbar = renderChartToolbar(rawOptions, state, render);
    if (toolbar) el.parentElement.insertBefore(toolbar, el);
    render();
    el.dataset.pdbChartReady = '1';
  }

  function initAll() {
    document.querySelectorAll('[data-pdb-chart]').forEach(initChart);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
})();
