const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const tick = async () => { for (let n = 0; n < 30; n++) await Promise.resolve(); };

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.listeners = {}; this.value = '';
    this.disabled = false; this.hidden = false; this._text = '';
  }
  set textContent(text) { this._text = String(text); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(' '); }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(event, listener) { this.listeners[event] = listener; }
  focus() { this.focused = true; }
  set innerHTML(value) { throw new Error('Unsafe HTML rendering: ' + value); }
}

function visibleText(node) {
  if (node.hidden) return '';
  const children = node.tagName === 'details' && !node.open
    ? node.children.filter(child => child.tagName === 'summary') : node.children;
  return node._text + children.map(visibleText).join(' ');
}

const definitions = [
  {key: 'subjective.energy', display_name: 'Energy', unit: 'score', roles: ['outcome'], candidate_enabled: false},
  {key: 'sleep.duration_hours', display_name: 'Sleep <script>unsafe()</script>', unit: 'hours', roles: ['exposure'], candidate_enabled: true},
  {key: 'wearable.steps', display_name: 'Steps', unit: 'steps', roles: ['exposure'], candidate_enabled: true},
  {key: 'nutrition.logged.kcal', display_name: 'Logged calories', unit: 'kcal', roles: ['exposure'], candidate_enabled: true},
].map(item => ({...item, implemented: true, value_kind: 'continuous', temporal_type: 'daily'}));
const selectedRange = {kind: 'bounded', from: '2026-06-24', to: '2026-06-30'};

function setup(measurements = definitions) {
  const nodes = new Map(), requests = [];
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, new Element());
    return nodes.get(id);
  };
  node('descriptive-lag').value = '1';
  const context = {
    document: {getElementById: node, querySelector: () => ({content: 'fictional-csrf'}), createElement: tag => new Element(tag)},
    window: {}, AbortController, location: {assign() {}},
    fetch(url, options = {}) {
      return new Promise((resolve, reject) => requests.push({url, options, resolve, reject}));
    },
  };
  vm.runInNewContext(fs.readFileSync('app/static/js/descriptions.js', 'utf8'), context);
  const reply = (request, result, status = 200) => request.resolve({status, ok: status === 200, json: async () => ({ok: status === 200, ...(status === 200 ? {result} : {error: {message: result}})})});
  async function ready() {
    reply(requests.shift(), {facts: measurements, coverage: {next_cursor: null}});
    await tick();
    context.window.HermesDescriptions.setRange(selectedRange);
  }
  async function add(key) {
    node('descriptive-exposure').value = key;
    node('descriptive-exposure').listeners.change();
    node('descriptive-add').listeners.click();
    await tick();
  }
  function submit() { return node('descriptive-form').listeners.submit({preventDefault() {}}); }
  return {node, requests, ready, add, submit, reply, controller: context.window.HermesDescriptions};
}

function result() {
  return {status: 'ok', evidence_refs: [{result_id: 'fictional-replay-id'}], facts: [{
    contract: 'descriptive-observations-v1', inference_status: 'not_assessed', eligible_for_hypothesis: false,
    lag_days: 1, loaded_range: {from: '2026-06-23', to: '2026-06-30'}, requested_range: selectedRange,
    features: [{feature_key: 'subjective.energy', display_name: 'Energy', unit: 'score', observed_days: 2,
      range_days: 7, days_without_usable_observation: 5, observed_units: 2, unit_type: 'day',
      summary: {n: 2, mean: 5, median: 5, min: 2, max: 8},
      period_compare: {status: 'observed_difference', split_date: '2026-06-27', baseline: {n: 1, mean: 2}, current: {n: 1, mean: 8}, mean_difference: 6},
      source_row_ids: ['fictional-source-row'], sample: [{date: '2026-06-25', value: 2, source: 'subjective_daily'}]}],
    comparisons: [{outcome_key: 'subjective.energy', exposure_key: 'sleep.duration_hours', outcome_unit: 'score', exposure_unit: 'hours',
      paired_units: 2, eligible_outcome_units: 3, missing_exposure_units: 1,
      grouping: 'at_or_below_vs_above_observed_median', exposure_threshold: 7,
      groups: [{label: 'lower', outcome: {n: 1, mean: 2}}, {label: 'higher', outcome: {n: 1, mean: 8}}],
      pattern_status: 'observed_difference', mean_difference: 6,
      counterexamples: {lower_group: 0, higher_group: 0, definition: "Units opposite the other group's mean."}}],
    limitations: [{message: 'Missing is unknown.'}],
  }]};
}

async function render(h, payload, exposure = 'sleep.duration_hours') {
  await h.add(exposure);
  const completion = h.submit();
  const request = h.requests.shift();
  assert.equal(request.url, '/api/insights/descriptions');
  assert.equal(request.options.headers['X-CSRFToken'], 'fictional-csrf');
  const body = JSON.parse(request.options.body);
  assert.deepEqual(body.range, {from: '2026-06-24', to: '2026-06-30'});
  assert.deepEqual(body.exposure_keys, [exposure]);
  assert.equal(body.lag_days, 1);
  h.reply(request, payload);
  await completion;
}

async function run() {
  const scenario = process.argv[2];
  const h = setup(scenario === 'units' ? definitions.map(item => ({
    ...item, unit: item.key === 'subjective.energy' ? 'ordinal_1_5' : item.unit,
  })) : definitions);
  await h.ready();
  if (scenario === 'sparse') {
    await render(h, result());
    const text = h.node('descriptive-results').textContent;
    assert.ok(text.includes('2 of 7 days'));
    assert.ok(text.includes('5 days are unknown'));
    assert.ok(text.includes('2 paired units out of 3 outcome units'));
    assert.ok(text.includes('6 score'));
    assert.ok(text.includes('Counterexamples: 0'));
    assert.ok(text.includes('Statistical confidence: not assessed'));
    assert.ok(text.includes('fictional-source-row'));
    assert.ok(text.includes('fictional-replay-id'));
    assert.ok(text.includes('<script>unsafe()</script>'));
    assert.equal(h.node('descriptive-results').focused, true);
    assert.equal(h.node('descriptive-results')['aria-busy'], 'false');
  } else if (scenario === 'no_pattern') {
    const payload = result(), comparison = payload.facts[0].comparisons[0];
    comparison.pattern_status = 'no_clear_pattern';
    comparison.mean_difference = 0;
    comparison.counterexamples = {lower_group: null, higher_group: null};
    await render(h, payload);
    const text = h.node('descriptive-results').textContent;
    assert.ok(text.includes('No clear pattern: the observed group means are equal'));
    assert.ok(text.includes('Directional counterexamples are not assessed'));
    assert.ok(!text.includes('Counterexamples: 0'));
  } else if (scenario === 'source_change') {
    const payload = result(), comparison = payload.facts[0].comparisons[0];
    Object.assign(comparison, {pattern_status: 'insufficient_comparison', reason: 'source_incompatible', mean_difference: null, groups: [], counterexamples: undefined});
    await render(h, payload);
    const text = h.node('descriptive-results').textContent;
    assert.ok(text.includes('Not comparable: The records use different sources'));
    assert.ok(!text.includes('No clear pattern:'));
  } else if (scenario === 'source_groups') {
    const payload = result(), comparison = payload.facts[0].comparisons[0];
    const common = {...comparison, eligible_outcome_units: null, missing_exposure_units: null,
      denominator_basis: 'complete_pairs_with_known_sources', independent_corroboration: false};
    const manual = {...common, source_id: 'fictional-manual-source-group', paired_units: 26,
      source_identity: {outcome_sources: ['subjective_daily'], exposure_sources: ['manual']},
      exposure_threshold: 6, mean_difference: 1,
      groups: [{label: 'lower', outcome: {n: 13, mean: 3}}, {label: 'higher', outcome: {n: 13, mean: 4}}]};
    const wearable = {...common, source_id: 'fictional-wearable-source-group', paired_units: 1,
      source_identity: {outcome_sources: ['subjective_daily'], exposure_sources: ['wearable']},
      pattern_status: 'insufficient_comparison', reason: 'no_exposure_contrast', mean_difference: null,
      groups: [], counterexamples: undefined};
    Object.assign(comparison, {pattern_status: 'insufficient_comparison', reason: 'source_incompatible',
      paired_units: 27, eligible_outcome_units: 28, missing_exposure_units: 1,
      mean_difference: null, groups: [], counterexamples: undefined,
      source_comparisons: [manual, wearable], source_comparisons_total: 2, source_comparisons_shown: 2});
    await render(h, payload);
    const text = h.node('descriptive-results').textContent;
    assert.ok(text.includes('27 paired units out of 28 outcome units'));
    assert.ok(text.includes('No pooled comparison:'));
    assert.ok(text.includes('26 paired units in this source group'));
    assert.ok(text.includes('1 paired units in this source group'));
    assert.ok(!text.includes('26 paired units out of 28'));
    assert.ok(!text.includes('null outcome units'));
    assert.ok(text.includes('Unpaired outcome records cannot be assigned'));
    assert.ok(text.includes('Source group: manual'));
    assert.ok(text.includes('Source group: wearable'));
    assert.ok(text.includes('At or below 6 hours'));
    assert.ok(text.includes('Observed difference within this source group: higher-group minus lower-group outcome mean is 1 score'));
    assert.ok(!text.includes('Observed difference:'));
    assert.ok(text.includes('Not comparable: The available comparison values do not form two different groups'));
    assert.ok(text.includes('Showing 2 of 2 source groups'));
    assert.ok(text.includes('These groups are not independent corroboration'));
    assert.ok(text.includes('fictional-manual-source-group'));
  } else if (scenario === 'partial') {
    const payload = result(), comparison = payload.facts[0].comparisons[0];
    payload.facts[0].features.push({feature_key: 'nutrition.logged.kcal', display_name: 'Logged calories',
      unit: 'kcal', observed_days: 0, range_days: 7, days_without_usable_observation: 7,
      observed_units: 0, unit_type: 'day', summary: {n: 0, mean: null, median: null, min: null, max: null},
      period_compare: {status: 'insufficient_comparison', split_date: '2026-06-27'},
      recorded_observation_rows: 1, partial_recorded_observation_rows: 1, partial_recorded_facts_shown: 1,
      partial_recorded_facts: [{observation_id: 'fictional-lunch', observed_at: '2026-06-25', value: 500,
        unit: 'kcal', source: 'manual', source_row_ids: ['fictional-lunch-source'], value_scope: 'recorded_subtotal',
        completeness: 'full_day_coverage_unconfirmed', candidate_eligible: false, eligible_for_hypothesis: false}]});
    Object.assign(comparison, {exposure_key: 'nutrition.logged.kcal', exposure_unit: 'kcal',
      paired_units: 0, eligible_outcome_units: 2, missing_exposure_units: 2,
      pattern_status: 'insufficient_comparison', reason: 'no_paired_observations',
      mean_difference: null, groups: [], counterexamples: undefined});
    await render(h, payload, 'nutrition.logged.kcal');
    const root = h.node('descriptive-results');
    const feature = root.children.find(child => child.children[0]?.textContent === 'Logged calories');
    const text = visibleText(feature);
    assert.ok(text.includes('500 kcal recorded on 2026-06-25'), 'The known subtotal must be visible without opening evidence');
    assert.ok(text.includes('Recorded subtotal; full-day coverage unknown'));
    assert.ok(text.includes('not daily intake totals or inputs to comparisons'));
    assert.ok(text.includes('0 of 7 days have comparison-usable records'));
    assert.ok(text.includes('Showing 1 of 1 recorded subtotals'));
    assert.ok(!text.includes('7 days are unknown'));
    assert.ok(!text.includes('No usable recorded values'));
    assert.ok(!text.includes('Mean 500'));
    assert.ok(visibleText(root).includes('0 paired units out of 2 outcome units'));
    assert.ok(visibleText(root).includes('Not comparable: No outcome records have a usable comparison record'));
    assert.ok(root.textContent.includes('fictional-lunch-source'));
  } else if (scenario === 'protocol') {
    const payload = result(), feature = payload.facts[0].features[0], comparison = payload.facts[0].comparisons[0];
    feature.summary = null;
    feature.comparability = {status: 'protocol_incompatible', protocols: [{equipment: 'fictional-a'}, {equipment: 'fictional-b'}]};
    feature.source_summaries = [{source: 'manual', n: 2, mean: null, median: null, min: null, max: null}];
    feature.period_compare = {status: 'protocol_incompatible', split_date: '2026-06-27'};
    Object.assign(comparison, {pattern_status: 'insufficient_comparison', reason: 'protocol_unavailable', mean_difference: null, groups: [], counterexamples: undefined});
    await render(h, payload);
    const text = h.node('descriptive-results').textContent;
    assert.ok(text.includes('The measurement protocols differ'));
    assert.ok(text.includes('The measurement protocol is unknown'));
    assert.ok(text.includes('2 recorded units; a comparable summary is not available'));
    assert.ok(!text.includes('one or both periods have no usable records'));
    assert.ok(!text.includes('No clear pattern:'));
  } else if (scenario === 'units') {
    const payload = result(), feature = payload.facts[0].features[0], comparison = payload.facts[0].comparisons[0];
    feature.unit = 'ordinal_1_5';
    feature.summary = {n: 2, mean: 3, median: 3, min: 2, max: 4};
    feature.period_compare.current.mean = 4;
    feature.period_compare.mean_difference = 2;
    comparison.outcome_unit = 'ordinal_1_5';
    comparison.groups[1].outcome.mean = 4;
    comparison.mean_difference = 2;
    await render(h, payload);
    assert.ok(h.node('descriptive-outcome').textContent.includes('Energy (1–5 scale)'));
    assert.ok(!h.node('descriptive-outcome').textContent.includes('ordinal_1_5'));
    const text = h.node('descriptive-results').textContent;
    assert.ok(text.includes('Mean 3 (1–5 scale)'));
    assert.ok(text.includes('Outcome mean 4 (1–5 scale)'));
    assert.ok(text.includes('"unit": "ordinal_1_5"'), 'Evidence must preserve the canonical unit');
  } else if (scenario === 'race') {
    const old = h.submit(), first = h.requests.shift();
    assert.equal(h.node('descriptive-explore').textContent, 'Stop waiting');
    h.controller.setPending(true);
    assert.equal(first.options.signal.aborted, true);
    assert.equal(h.node('descriptive-explore').disabled, true);
    h.controller.setRange({...selectedRange, from: '2026-07-01', to: '2026-07-31'});
    h.controller.setPending(false);
    const current = h.submit(), second = h.requests.shift();
    h.reply(first, result());
    await old;
    assert.equal(h.node('descriptive-results').children.length, 0);
    assert.equal(h.node('descriptive-results')['aria-busy'], 'true');
    h.reply(second, {status: 'unsupported', limitations: [{code: 'new-scope'}]});
    await current;
    assert.ok(h.node('descriptive-results').textContent.includes('new-scope'));
    assert.ok(!h.node('descriptive-results').textContent.includes('2 of 7 days'));
    const stopped = h.submit(), third = h.requests.shift();
    await h.submit();
    assert.equal(third.options.signal.aborted, true);
    h.reply(third, result());
    await stopped;
    assert.equal(h.node('descriptive-results').children.length, 0);
  } else if (scenario === 'errors') {
    const failed = h.submit();
    h.reply(h.requests.shift(), 'Database unavailable', 503);
    await failed;
    assert.ok(h.node('descriptive-status').textContent.includes('Records unavailable: Database unavailable'));
    assert.ok(!h.node('descriptive-status').textContent.includes('No usable records'));
    assert.equal(h.node('descriptive-explore').disabled, false);
    const empty = result();
    empty.status = 'insufficient_data';
    empty.facts[0].features = [];
    empty.facts[0].comparisons = [];
    await render(h, empty);
    assert.ok(h.node('descriptive-status').textContent.includes('No usable records'));
  } else if (scenario === 'scope') {
    h.controller.setRange({kind: 'all'});
    assert.equal(h.node('descriptive-explore').disabled, true);
    assert.ok(h.node('descriptive-window').textContent.includes('up to 366 days'));
    await h.submit();
    assert.equal(h.requests.length, 0);
    h.controller.setRange(selectedRange);
    await render(h, result());
    h.node('descriptive-lag').value = '3';
    h.node('descriptive-lag').listeners.change();
    assert.equal(h.node('descriptive-results').children.length, 0);
    const removed = h.node('descriptive-selected').children[0].children[1];
    removed.listeners.click();
    assert.equal(h.node('descriptive-selected').children.length, 0);
  } else throw new Error('Unknown scenario: ' + scenario);
}
run().catch(error => { console.error(error); process.exitCode = 1; });
