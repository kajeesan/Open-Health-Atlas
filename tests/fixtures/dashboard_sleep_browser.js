const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');

async function render(rows, fail = false) {
  const elements = {};
  for (const id of ['dash-sleep', 'dash-sleep-average', 'dash-sleep-coverage']) {
    elements[id] = { textContent: '', clientWidth: 400, setAttribute(key, value) { this[key] = value; } };
  }
  const events = {};
  let option;
  const context = vm.createContext({
    window: { addEventListener(name, fn) { events[name] = fn; } },
    document: { documentElement: {}, querySelector() { return { dataset: { sleepThrough: '2026-10-02' } }; },
      getElementById(id) { return elements[id]; } },
    getComputedStyle() { return { getPropertyValue() { return '#345634'; } }; },
    echarts: { init() { return { setOption(value) { option = value; }, resize() {} }; } },
    fetch: async () => { if (fail) throw new Error('offline'); return { ok: true, json: async () => ({ rows }) }; },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'app/static/js/charts-common.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(root, 'app/static/js/dashboard-sleep.js'), 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  return { elements, events, get option() { return option; } };
}

test('average counts recorded dates once and respects Recovery source preference', async () => {
  const result = await render([
    { date: '2026-09-19', sleep_hours: 6, source: 'fitbit' },
    { date: '2026-10-02', sleep_hours: 10, source: 'fitbit' },
    { date: '2026-10-02', sleep_hours: 8, source: 'apple' },
    { date: '2026-10-02', sleep_hours: 11, source: 'fitbit' },
    { date: '2026-09-18', sleep_hours: 20, source: 'apple' },
    { date: '2026-10-03', sleep_hours: 20, source: 'apple' },
    { date: '2026-09-22', sleep_hours: null, source: 'apple' },
    { date: '2026-09-23', sleep_hours: '8', source: 'apple' },
    { date: '2026-09-24', sleep_hours: -1, source: 'apple' },
    { date: '2026-09-25', sleep_hours: 25, source: 'apple' },
  ]);
  assert.equal(result.elements['dash-sleep-average'].textContent, '7h 00m');
  assert.match(result.elements['dash-sleep-coverage'].textContent, /2 of 14/);
  assert.equal(result.option.series[0].data.length, 14);
  assert.equal(result.option.series[0].data[0].value, 6);
  assert.equal(result.option.series[0].data[1].value, null);
  assert.equal(result.option.series[0].data[13].value, 8);
  assert.match(result.elements['dash-sleep']['aria-label'], /2026-09-20: not recorded/);
  result.events.themechange();
  assert.equal(result.elements['dash-sleep-average'].textContent, '7h 00m');
});

test('zero hours is a recorded value', async () => {
  const result = await render([{ date: '2026-10-02', sleep_hours: 0, source: 'apple' }]);
  assert.equal(result.elements['dash-sleep-average'].textContent, '0h 00m');
  assert.match(result.elements['dash-sleep-coverage'].textContent, /1 of 14/);
});

test('empty records show an honest empty state', async () => {
  const result = await render([]);
  assert.equal(result.elements['dash-sleep-average'].textContent, 'No sleep recorded');
  assert.match(result.elements['dash-sleep-coverage'].textContent, /0 of 14/);
});

test('failed request remains unavailable when the theme changes', async () => {
  const result = await render([], true);
  result.events.themechange();
  assert.equal(result.elements['dash-sleep-average'].textContent, 'Unavailable');
  assert.match(result.elements['dash-sleep-coverage'].textContent, /Reload/);
});
