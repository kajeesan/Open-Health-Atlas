const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const tick = async () => { for (let index = 0; index < 80; index++) await Promise.resolve(); };

class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.listeners = {}; this.hidden = false; this.disabled = false; this.value = ''; this._text = ''; }
  set textContent(text) { this._text = String(text); this.children = []; }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(''); }
  set innerHTML(_text) { throw new Error('Imported content must not be interpreted as HTML'); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this._text = ''; this.children = items; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(name, value) { this[name] = value; }
  focus() { this.focused = true; }
}

function setup({ empty = false, readError = false, saveFailure = null, modifyPacket = null } = {}) {
  const payload = JSON.parse(fs.readFileSync('tests/fixtures/recovery_context_packet.json', 'utf8'));
  if (modifyPacket) modifyPacket(payload.result);
  if (empty) {
    for (const field of ['training_records', 'training_exposure', 'pain_reports', 'training_before_symptoms', 'next_questions']) payload.result[field] = [];
    payload.result.recovery = { daily: [], sleep: [], reported: [] };
    payload.result.coverage.forEach((row) => { row.state = 'unknown'; });
  }
  const nodes = new Map(), reads = [], writes = [], handlers = {};
  const get = (id) => { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); };
  get('rEvidenceRegion').value = 'knee';
  const fetch = async (url, options = {}) => {
    if (options.method !== 'POST') {
      reads.push(url);
      if (readError) throw new Error('fictional service offline');
      const params = new URL(url, 'http://localhost').searchParams;
      if (params.has('from')) payload.result.range = { from: params.get('from'), to: params.get('to') };
      return { ok: true, status: 200, json: async () => structuredClone(payload) };
    }
    const body = JSON.parse(options.body);
    writes.push({ url, body, headers: options.headers, credentials: options.credentials });
    if (writes.length === 1 && saveFailure === 'network') throw new Error('fictional network interruption');
    if (writes.length === 1 && saveFailure === 'validation') return { ok: false, status: 400, json: async () => ({ ok: false, error: 'invalid context answer' }) };
    const row = { ...body, id: writes.length, value: body.value ?? null, effective_to: body.effective_to ?? null };
    const target = payload.result.pain_reports.find((item) => String(item.id) === body.subject_key);
    if (target) target.reported_context[body.context_key] = row;
    payload.result.next_questions = payload.result.next_questions.filter((question) => question.context_key !== body.context_key);
    return { ok: true, status: 200, json: async () => ({ ok: true, result: { ok: true, id: row.id } }) };
  };
  vm.runInNewContext(fs.readFileSync('app/static/js/recovery-context.js', 'utf8'), {
    document: { getElementById: get, createElement: (tag) => new Element(tag), querySelector: () => ({ content: 'fictional-csrf' }) },
    window: { HermesUI: { onRange: (_name, callback) => { handlers.range = callback; } } },
    fetch, crypto, URLSearchParams, console,
  });
  return { get, nodes, reads, writes, handlers };
}

async function scopedEvidence() {
  const h = setup();
  await tick();

  assert.equal(h.get('rEvidenceFrom').value, '2026-06-24');
  assert.match(h.get('rEvidenceScope').textContent, /Fictional records/);
  assert.match(h.get('rEvidenceCoverage').textContent, /known on 1 of 7 dates/);
  assert.match(h.get('rContextQuestion').textContent, /^Where around the knee do you feel it\? · 2026-06-28 · lateral-knee · left$/);
  assert.equal(h.get('rContextFrom').value, '2026-06-28');
  assert.equal([...h.nodes.values()].some((item) => item.textContent.includes('UNRELATED CONTEXT')), false);
  h.get('rEvidenceSource').value = '1'; h.get('rEvidenceSource').listeners.change();
  assert.match(h.get('rEvidenceSourceRecord').textContent, /<img src=x onerror=fictional\(\)>/);
}

async function emptyIsNotError() {
  const h = setup({ empty: true });
  await tick();
  assert.equal(h.get('rEvidenceReadError').hidden, true);
  assert.equal(h.get('rEvidenceBody').hidden, false);
  assert.match(h.get('rEvidenceFacts').textContent, /No symptom reports/);
  assert.equal(h.get('rContextForm').hidden, true);
}

async function failedReadIsNotEmpty() {
  const h = setup({ readError: true });
  await tick();
  assert.equal(h.get('rEvidenceBody').hidden, true);
  assert.match(h.get('rEvidenceReadError').textContent, /offline/);
  assert.equal(h.get('rEvidenceReload').hidden, false);
}

async function retryKeepsCaptureIdentity() {
  const h = setup({ saveFailure: 'network' });
  await tick();
  h.get('rContextValue').value = 'Outside the left knee';
  h.get('rContextForm').listeners.submit({ preventDefault() {} });
  await tick();
  assert.equal(h.get('rContextValue').disabled, true);
  assert.equal(h.get('rContextSave').disabled, false);
  assert.equal(h.get('rContextSave').textContent, 'Retry save');

  h.get('rContextForm').listeners.submit({ preventDefault() {} });
  await tick();

  assert.deepEqual(h.writes[0].body, h.writes[1].body);
  assert.equal(h.writes[0].headers['X-CSRFToken'], 'fictional-csrf');
  assert.equal(h.writes[0].credentials, 'same-origin');
  assert.match(h.get('rContextStatus').textContent, /Saved your report/);
  assert.match(h.get('rContextQuestion').textContent, /^Which movement brings it on\? · 2026-06-28 · lateral-knee · left$/);
}

async function skipRemainsUnknown() {
  const h = setup(); await tick();
  h.get('rContextSkip').listeners.click(); await tick();
  assert.equal(h.writes[0].body.status, 'skipped');
  assert.equal(Object.hasOwn(h.writes[0].body, 'value'), false);
  assert.match(h.get('rContextSavedList').textContent, /unknown \(skipped\)/);
}

async function savedAnswerCanBeCorrected() {
  const h = setup(); await tick();
  h.get('rContextValue').value = 'Outside knee';
  h.get('rContextForm').listeners.submit({ preventDefault() {} }); await tick();
  const correct = h.get('rContextSavedList').children[0].children.find((item) => item.tagName === 'button');
  correct.listeners.click();
  h.get('rContextValue').value = 'Outside kneecap';

  h.get('rContextForm').listeners.submit({ preventDefault() {} }); await tick();

  assert.equal(h.writes[1].body.supersedes_id, 1);
  assert.notEqual(h.writes[0].body.capture_id, h.writes[1].body.capture_id);
  assert.equal(h.writes[1].body.effective_from, '2026-06-28');
  assert.match(h.get('rContextSavedList').textContent, /Outside kneecap/);
}

async function rejectedAnswerCanBeEdited() {
  const h = setup({ saveFailure: 'validation' }); await tick();
  h.get('rContextValue').value = 'Rejected fictional answer';
  h.get('rContextForm').listeners.submit({ preventDefault() {} }); await tick();
  assert.equal(h.get('rContextValue').disabled, false);
  assert.match(h.get('rContextStatus').textContent, /Not saved/);
  h.get('rContextValue').value = 'Corrected fictional answer';

  h.get('rContextForm').listeners.submit({ preventDefault() {} }); await tick();

  assert.notEqual(h.writes[0].body.capture_id, h.writes[1].body.capture_id);
  assert.equal(h.writes[1].body.value, 'Corrected fictional answer');
}

async function allTimeHasExplicitBound() {
  const h = setup(); await tick();
  h.handlers.range('All'); await tick();
  const params = new URL(h.reads.at(-1), 'http://localhost').searchParams;
  assert.equal(params.get('from'), '2025-06-30');
  assert.equal(params.get('to'), '2026-06-30');
  assert.match(h.get('rEvidenceScope').textContent, /limited to the latest 366 days/);
}

async function identicalSidedAnswersRetainTheirCorrectionTarget() {
  const h = setup({ modifyPacket(packet) {
    const report = packet.pain_reports[0];
    packet.pain_reports = ['left', 'right'].map((side, index) => ({
      ...report, id: 31 + index, evidence_id: `pain_log:${31 + index}`, side,
      reported_context: { location: {
        id: 81 + index, subject_kind: 'pain', subject_key: String(31 + index), context_key: 'location',
        effective_from: '2026-06-28', effective_to: null, status: 'answered', value: 'Outside knee',
      } },
    }));
    packet.next_questions = [];
  } });
  await tick();
  const rows = h.get('rContextSavedList').children;
  assert.match(rows[0].textContent, /2026-06-28 · lateral-knee · left/);
  assert.match(rows[1].textContent, /2026-06-28 · lateral-knee · right/);
  rows[1].children.find((item) => item.tagName === 'button').listeners.click();
  assert.match(h.get('rContextQuestion').textContent, /lateral-knee · right/);
  h.get('rContextValue').value = 'Outside the right kneecap';

  h.get('rContextForm').listeners.submit({ preventDefault() {} }); await tick();

  assert.equal(h.writes[0].body.subject_key, '32');
  assert.equal(h.writes[0].body.supersedes_id, 82);
  assert.match(h.get('rContextSavedList').children[0].textContent, /Symptom location: Outside knee/);
  assert.match(h.get('rContextSavedList').children[1].textContent, /Outside the right kneecap/);
}

async function unresolvableTargetCannotBeCorrected() {
  const h = setup({ modifyPacket(packet) {
    packet.pain_reports[0].reported_context.location = {
      id: 91, subject_kind: 'pain', subject_key: 'unavailable-record', context_key: 'location',
      effective_from: '2026-06-28', status: 'skipped', value: null,
    };
  } });
  await tick();

  const row = h.get('rContextSavedList').children[0];
  assert.match(row.textContent, /Target details unavailable/);
  assert.match(row.textContent, /pain · unavailable-record/);
  assert.equal(row.children.find((item) => item.tagName === 'button').disabled, true);
}

async function exerciseAndFitnessTargetsKeepTheirRecordedIdentity() {
  const h = setup({ modifyPacket(packet) {
    const exercise = packet.training_records[0];
    exercise.template_id = 'fictional-extension-template';
    exercise.reported_context.setup = {
      id: 101, subject_kind: 'exercise', subject_key: 'fictional-extension-template', context_key: 'setup',
      effective_from: '2026-06-25', status: 'answered', value: 'Seat 3',
    };
    packet.training_records.push({ ...exercise, id: 'hevy_sets:2', record: { ...exercise.record, date: '2026-06-26' } });
    packet.fitness_measurements.push({
      id: 'fitness_tests:7', record: { id: 7, date: '2026-06-27', movement: 'single-leg-extension', side: 'right' },
      reported_context: { pain_limited: {
        id: 102, subject_kind: 'fitness', subject_key: '7', context_key: 'pain_limited',
        effective_from: '2026-06-27', status: 'answered', value: 'no',
      } },
    });
  } });
  await tick();

  const rows = h.get('rContextSavedList').children;
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent, /Fictional leg extension · template fictional-extension-template · setup: Seat 3/);
  assert.match(rows[1].textContent, /2026-06-27 · single-leg-extension · right/);
  rows[1].children.find((item) => item.tagName === 'button').listeners.click();
  assert.match(h.get('rContextQuestion').textContent, /single-leg-extension · right/);
}

async function firstQuestionNamesTheSideItWillSaveTo() {
  const h = setup({ modifyPacket(packet) {
    const report = packet.pain_reports[0];
    packet.pain_reports = ['left', 'right'].map((side, index) => ({
      ...report, id: 31 + index, evidence_id: `pain_log:${31 + index}`, side, reported_context: {},
    }));
    packet.next_questions = [{ ...packet.next_questions[0], subject_key: '32', parent_ids: ['pain_log:32'] }];
  } });
  await tick();
  assert.match(h.get('rContextQuestion').textContent, /^Where around the knee do you feel it\? · 2026-06-28 · lateral-knee · right$/);
  h.get('rContextValue').value = 'Outside the right knee';

  h.get('rContextForm').listeners.submit({ preventDefault() {} }); await tick();

  assert.equal(h.writes[0].body.subject_key, '32');
  assert.equal(Object.hasOwn(h.writes[0].body, 'supersedes_id'), false);
}

const cases = {
  scoped_evidence: scopedEvidence, empty: emptyIsNotError, read_error: failedReadIsNotEmpty,
  stable_retry: retryKeepsCaptureIdentity, skip: skipRemainsUnknown,
  correction: savedAnswerCanBeCorrected, rejected_edit: rejectedAnswerCanBeEdited, bounded_all: allTimeHasExplicitBound,
  sided_targets: identicalSidedAnswersRetainTheirCorrectionTarget, unknown_target: unresolvableTargetCannotBeCorrected,
  exercise_fitness_targets: exerciseAndFitnessTargetsKeepTheirRecordedIdentity,
  first_question_target: firstQuestionNamesTheSideItWillSaveTo,
};
Promise.resolve().then(() => cases[process.argv[2]]()).then(() => console.log(`PASS ${process.argv[2]}`))
  .catch((error) => { console.error(error); process.exitCode = 1; });
