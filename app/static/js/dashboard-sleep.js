(function () {
  const card = document.querySelector('[data-sleep-through]');
  if (!card || !window.HermesCharts || typeof echarts === 'undefined') return;
  const { fetchJSON, barsOpt, pal } = window.HermesCharts;
  const mount = document.getElementById('dash-sleep');
  const average = document.getElementById('dash-sleep-average');
  const coverage = document.getElementById('dash-sleep-coverage');
  const chart = echarts.init(mount);
  const through = card.dataset.sleepThrough;
  // Advance civil date labels in UTC so daylight-saving changes cannot skip a night.
  const dates = Array.from({ length: 14 }, (_, i) => {
    const date = new Date(through + 'T12:00:00Z');
    date.setUTCDate(date.getUTCDate() - 13 + i);
    return date.toISOString().slice(0, 10);
  });
  let nights = new Map();
  let failed = false;

  function duration(hours) {
    const minutes = Math.round(hours * 60);
    return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`;
  }

  function render() {
    const P = pal();
    if (failed || !nights.size) {
      average.textContent = failed ? 'Unavailable' : 'No sleep recorded';
      coverage.textContent = failed ? 'Reload to try again' : '0 of 14 nights recorded';
      const message = failed ? 'Sleep data could not be loaded' : 'Sleep appears after you record or sync it';
      mount.setAttribute('aria-label', message);
      chart.setOption({ graphic: { type: 'text', left: 'center', top: 'middle',
        style: { text: message, fill: P.muted, fontSize: 12, width: mount.clientWidth - 24, overflow: 'break' } } }, true);
      return;
    }
    const mean = [...nights.values()].reduce((sum, row) => sum + row.sleep_hours, 0) / nights.size;
    average.textContent = duration(mean);
    coverage.textContent = `Average · ${nights.size} of 14 nights recorded`;
    const values = dates.map(date => nights.get(date)?.sleep_hours ?? null);
    mount.setAttribute('aria-label', dates.map((date, i) =>
      `${date}: ${values[i] === null ? 'not recorded' : duration(values[i])}`).join('; '));
    const options = barsOpt(dates.map(date => date.slice(5)), values, P.good, false);
    options.yAxis.min = 0;
    options.yAxis.axisLabel.formatter = '{value}h';
    options.tooltip.confine = true;
    options.tooltip.valueFormatter = value => value == null ? 'Not recorded' : duration(value);
    options.series[0].name = 'Sleep duration';
    chart.setOption(options, true);
  }

  async function load() {
    try {
      const { rows } = await fetchJSON('/api/dash/metrics?days=30');
      for (const row of rows) {
        if (!dates.includes(row.date) || !Number.isFinite(row.sleep_hours) || row.sleep_hours < 0 || row.sleep_hours > 24) continue;
        const current = nights.get(row.date);
        // Match Recovery: Apple takes precedence over another source on the same date.
        if (!current || (current.source !== 'apple' && row.source === 'apple')) nights.set(row.date, row);
      }
    } catch (_) {
      failed = true;
    }
    render();
  }
  window.addEventListener('themechange', render);
  window.addEventListener('resize', () => chart.resize());
  load();
})();
