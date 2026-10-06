const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');

async function render(file, payload) {
  const elements = {};
  const options = {};
  const element = id => elements[id] ??= {
    id, textContent: '', hidden: false, children: [],
    append(...items) { this.children.push(...items); },
    appendChild(item) { this.children.push(item); },
    replaceChildren() { this.children = []; },
  };
  const context = vm.createContext({
    window: {
      addEventListener() {},
      HermesUI: { range() { return 'All'; }, onRange() {} },
    },
    location: { pathname: '/recovery/hrv' },
    document: { documentElement: {}, getElementById: element, createElement: tag => ({
      tag, children: [], append(...items) { this.children.push(...items); },
      appendChild(item) { this.children.push(item); },
    }) },
    getComputedStyle() { return { getPropertyValue() { return '#345634'; } }; },
    echarts: { graphic: { LinearGradient: function () {} }, init(el) { return { setOption(value) { options[el.id] = value; }, resize() {}, dispose() {} }; } },
    fetch: async url => ({ ok: true, json: async () =>
      url.includes('recovery-detail') ? payload : url.includes('/metrics?') ? { rows: payload } : {} }),
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'app/static/js/charts-common.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(root, `app/static/js/${file}.js`), 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  return { elements, options };
}

const transition = [
  { date: '2026-08-01', source: 'apple', hrv_ms: 60 },
  { date: '2026-08-02', source: 'apple', hrv_ms: 60 },
  { date: '2026-08-03', source: 'apple', hrv_ms: 60 },
  { date: '2026-08-04', source: 'fitbit', hrv_ms: 30 },
  { date: '2026-08-05', source: 'fitbit', hrv_ms: 30 },
  { date: '2026-08-06', source: 'fitbit', hrv_ms: 30 },
];

test('overview HRV summary and trend do not treat a device change as a physiological change', async () => {
  const { elements, options } = await render('recovery', transition);
  assert.equal(elements.rkHrv.textContent, '30');
  assert.equal(elements.rkHrvFlag.textContent, 'steady vs avg');
  assert.match(elements.rkHrvScope.textContent, /Fitbit RMSSD.*3 days/);
  const series = options.rHrv.series;
  assert.deepEqual(Array.from(series, line => line.name), ['Apple SDNN', 'Fitbit RMSSD', 'RHR']);
  assert.deepEqual(Array.from(series[0].data), [60, 60, 60, null, null, null]);
  assert.deepEqual(Array.from(series[1].data), [null, null, null, 30, 30, 30]);
});

test('overview keeps unknown HRV readings but withholds a directional comparison', async () => {
  const { elements, options } = await render('recovery', [
    { date: '2026-08-04', source: 'other', hrv_ms: 50 },
    { date: '2026-08-05', source: 'other', hrv_ms: 60 },
    { date: '2026-08-06', source: 'other', hrv_ms: 70 },
  ]);
  assert.equal(elements.rkHrv.textContent, '60');
  assert.equal(elements.rkHrvFlag.hidden, true);
  assert.match(elements.rkHrvScope.textContent, /method unknown/);
  assert.equal(options.rHrv.series[0].name, 'other (method unknown)');
});

test('drill baseline belongs only to the selected source and carries its method label', async () => {
  const { options } = await render('recovery_detail', {
    metric: 'hrv', label: 'HRV', empty: false, baseline: 30,
    baseline_label: 'Fitbit RMSSD 60-day median', comparison: { source: 'fitbit' },
    series: { apple: [['2026-08-01', 60]], fitbit: [['2026-08-02', 30]], other: [['2026-08-01', 90]] },
    breakdown: [], advice: [],
  });
  const series = options['rd-chart'].series;
  assert.deepEqual(Array.from(series, line => line.name), ['Apple SDNN', 'Fitbit RMSSD', 'other (method unknown)']);
  assert.equal(series[0].markLine, undefined);
  assert.equal(series[1].markLine.data[0].yAxis, 30);
  assert.equal(series[1].markLine.label.formatter, 'Fitbit RMSSD 60-day median');
});
