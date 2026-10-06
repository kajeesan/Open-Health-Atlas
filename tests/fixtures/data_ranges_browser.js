const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const flush = () => new Promise(resolve => setImmediate(resolve));

function page() {
  const requests = [];
  const elements = new Map();
  const handlers = {};
  const events = {};
  const options = {};
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      id, value: 'all', textContent: '', innerHTML: '',
      addEventListener(name, fn) { this[name] = fn; },
      closest() { return null; },
    });
    return elements.get(id);
  }
  const context = vm.createContext({
    Date: class extends Date {
      constructor(...args) { super(...(args.length ? args : ['2026-10-06T12:00:00'])); }
    },
    window: {
      HermesUI: { onRange(name, fn) { handlers[name] = fn; } },
      addEventListener(name, fn) { events[name] = fn; },
    },
    document: { documentElement: {}, getElementById: element, querySelectorAll() { return []; } },
    getComputedStyle() { return { getPropertyValue() { return '#345634'; } }; },
    echarts: { init(el) { return {
      setOption(option) { options[el.id] = option; }, dispose() {}, resize() {},
    }; } },
    fetch(url) {
      if (url === '/api/log/recent') return Promise.resolve({ ok: true, json: async () => ({ items: [] }) });
      return new Promise((resolve, reject) => requests.push({ url, resolve, reject }));
    },
  });
  for (const name of ['charts-common.js', 'data.js']) {
    vm.runInContext(fs.readFileSync(path.join(root, 'app/static/js', name), 'utf8'), context);
  }
  function settle(url, body) {
    const index = requests.findIndex(request => request.url === url);
    assert.notEqual(index, -1, `Missing request: ${url}`);
    requests.splice(index, 1)[0].resolve({ ok: true, json: async () => body });
  }
  function fixedHistory() {
    settle('/api/dash/subjective?days=all', { rows: [{ date: '2024-01-03', mood: 4, day_rating: 3 }] });
    settle('/api/dash/safety?days=all', { vitals: [{ date: '2024-01-03', systolic: 120 }], doses: [] });
    settle('/api/nutrition/water?days=all', { rows: [{ date: '2024-01-03', water_ml: 1600 }] });
  }
  function range(days, value, error) {
    const date = '2026-10-05';
    const bodies = {
      metrics: { rows: [{ date, source: 'apple', resting_hr: value }] },
      body: { rows: [{ date, weight_kg: value }] },
      sleep: { rows: [{ date, time_asleep_hours: value }] },
      workouts: { rows: [{ date, type: 'walking', minutes: value }] },
      'hevy-volume': { rows: [{ date, volume_kg: value, sets: 1 }] },
      env: { weather_history: [{ date, temp_min_c: value }], air_history: [] },
    };
    if (error) {
      const index = requests.findIndex(request => request.url === `/api/dash/metrics?days=${days}`);
      assert.notEqual(index, -1, 'Missing metrics request');
      requests.splice(index, 1)[0].reject(new Error(error));
      delete bodies.metrics;
    }
    for (const [endpoint, body] of Object.entries(bodies)) settle(`/api/dash/${endpoint}?days=${days}`, body);
  }
  return { options, events, element, fixedHistory, range, select: value => handlers.range(value) };
}

function displayedRangeValues(browser) {
  return ['ch-rhr', 'ch-body', 'ch-timeasleep', 'ch-workouts', 'ch-hevyvol', 'ch-weather']
    .map(id => {
      const value = browser.options[id].series?.[0].data[0];
      return Array.isArray(value) ? value[1] : value;
    });
}

test('newest selected range survives reversed response ordering for every dataset', async () => {
  const browser = page();
  browser.fixedHistory(); browser.range(90, 90);
  await flush();

  browser.select('Year'); browser.select('Week');
  browser.range(7, 7); await flush();
  browser.range(365, 365); await flush();

  assert.deepEqual(displayedRangeValues(browser), [7, 7, 7, 7, 7, 7]);
});

test('All retains subjective, safety and hydration records older than a year', async () => {
  const browser = page();
  browser.fixedHistory(); browser.range(90, 90);
  await flush();

  browser.select('All'); browser.range('all', 7); await flush();

  assert.equal(browser.options['ch-mood'].series[0].data[0][0], '2024-01-03');
  assert.equal(browser.options['ch-safety'].series[0].data[0][0], '2024-01-03');
  assert.equal(browser.options['ch-water'].series[0].data[0].value, 1600);
});

for (const initialError of [undefined, 'Initial range offline']) {
  test(`initial load cannot overwrite a newer selection (${initialError || 'success'})`, async () => {
    const browser = page();
    browser.fixedHistory();

    browser.select('Week'); browser.range(7, 7); await flush();
    browser.range(90, 90, initialError); await flush();

    assert.deepEqual(displayedRangeValues(browser), [7, 7, 7, 7, 7, 7]);
  });
}

test('a superseded failure does not replace the newest successful charts', async () => {
  const browser = page();
  browser.fixedHistory(); browser.range(90, 90);
  await flush();

  browser.select('Year'); browser.select('Week');
  browser.range(7, 7); await flush();
  browser.range(365, null, 'Year offline'); await flush();

  assert.deepEqual(displayedRangeValues(browser), [7, 7, 7, 7, 7, 7]);
});

test('the latest failure remains visible after a superseded success', async () => {
  const browser = page();
  browser.fixedHistory(); browser.range(90, 90);
  await flush();

  browser.select('Year'); browser.select('Week');
  browser.range(7, null, 'Week offline'); await flush();
  browser.range(365, 365); await flush();

  assert.match(browser.options['ch-rhr'].graphic.style.text, /Week offline/);
});

test('late initial history cannot erase the current range failure', async () => {
  const browser = page();
  browser.range(90, 90); await flush();

  browser.select('Week'); browser.range(7, null, 'Week offline'); await flush();
  browser.fixedHistory(); await flush();

  assert.match(browser.options['ch-rhr'].graphic.style.text, /Week offline/);
});

test('theme and source changes retain a current range failure', async () => {
  const browser = page();
  browser.fixedHistory(); browser.range(90, 90);
  await flush();

  browser.select('Week'); browser.range(7, null, 'Week offline'); await flush();
  browser.events.themechange();
  browser.element('source-filter').change({ target: { value: 'apple' } });

  assert.match(browser.options['ch-rhr'].graphic.style.text, /Week offline/);
});

test('a later successful range clears the previous failure', async () => {
  const browser = page();
  browser.fixedHistory(); browser.range(90, null, 'Initial range offline');
  await flush();

  browser.select('Week'); browser.range(7, 7); await flush();

  assert.deepEqual(displayedRangeValues(browser), [7, 7, 7, 7, 7, 7]);
});
