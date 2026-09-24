const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = 'app/static/js/insights.js';
const script = fs.readFileSync(source, 'utf8');
const tick = async () => { for (let n=0;n<30;n++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise=new Promise((a,b)=>{resolve=a;reject=b;}); return {promise,resolve,reject}; };
class Element {
  constructor(id='', tagName='div') { this.id=id; this.tagName=tagName; this.open=false; this.children=[]; this.dataset={}; this.listeners={}; this.disabled=false; this.hidden=false; this.value=''; this._text=''; this.classList={toggle(){}}; }
  set textContent(s) { this._text=String(s); this.children=[]; }
  get textContent() { return this._text+this.children.map(x=>x.textContent||'').join(''); }
  get childNodes() { return this.children; }
  append(...xs) { this.children.push(...xs); }
  appendChild(x) { this.children.push(x); return x; }
  replaceChildren(...xs) { this._text=''; this.children=xs; }
  setAttribute(k,v) { this[k]=v; }
  addEventListener(k,f) { this.listeners[k]=f; }
  focus() { this.focused=true; }
  scrollIntoView() { this.scrolled=true; }
  remove() {}
  set innerHTML(value) { throw new Error('Unsafe HTML rendering: '+value); }
}
function descendants(node) { return [node, ...node.children.flatMap(descendants)]; }
function visibleText(node) {
  if (node.hidden) return '';
  const children=node.tagName==='details' && !node.open ? node.children.filter(item=>item.tagName==='summary') : node.children;
  return node._text+children.map(visibleText).join(' ');
}
function setup() {
  const nodes=new Map(), storage=new Map(), requests=[], jobs=[];
  const node=id=>{ const rendered=[...nodes.values()].flatMap(descendants).find(item=>item.id===id); if(rendered) return rendered; if(!nodes.has(id)) nodes.set(id,new Element(id)); return nodes.get(id); };
  node('ins-granularity').value='month';
  const people=Object.fromEntries(['A','B','C'].map((id,i)=>[id,{id,title:`Conversation ${id}`,lens:'general',archived:false,context:{version:1,range:{kind:'bounded',from:`2026-0${i+1}-01`,to:`2026-0${i+1}-28`},selected_region_ids:[]}}]));
  function fetch(url,opts={}) { const d=deferred(); requests.push({url,opts,d}); return d.promise; }
  const context={document:{getElementById:node,querySelector:()=>({content:'csrf'}),querySelectorAll:selector=>[...nodes.values()].flatMap(descendants).filter(item=>(selector==='[data-evidence-finding]' && item.dataset.evidenceFinding) || (selector==='[data-insufficient-group]' && item.dataset.insufficientGroup)),createElement:tag=>new Element('',tag)},Node:Element,fetch,localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},location:{assign(){}},window:{HermesAnalysisJobs:{remembered:()=>null,wait:options=>{const d=deferred();jobs.push({options,d});return d.promise;}}},AbortController,DOMException,URLSearchParams,crypto:require('node:crypto').webcrypto,console};
  vm.runInNewContext(script,context,{filename:source});
  function reply(url,body) { const index=requests.findIndex(x=>x.url===url); assert.notEqual(index,-1,`Expected request ${url}; have ${requests.map(x=>x.url)}`); const [r]=requests.splice(index,1); r.d.resolve({status:200,ok:true,json:async()=>({ok:true,...body})}); return r; }
  async function boot() { reply('/api/chat/conversations?archived=0&limit=100',{conversations:Object.values(people)}); await tick(); reply('/api/chat/conversations/A',{conversation:people.A}); await tick(); reply('/api/chat/conversations/A/messages?limit=100',{messages:[]}); await tick(); }
  function change(id) { node('ins-conversation').value=id; return node('ins-conversation').listeners.change({target:{value:id}}); }
  function analyze() { return node('ins-analyze').listeners.click({target:node('ins-analyze')}); }
  function result(id) { const range=people[id].context.range; return {status:'completed',result:{meta:{analysis_range:range,baseline_range:range},coverage:{},findings:[{finding_id:`finding-${id}`,outcome:{mode:'ordinal'},exposure:{components:[{display:`ONLY-${id}`}]}}]}}; }
  return {node,people,requests,jobs,reply,boot,change,analyze,result,storage};
}
async function analysisSwitch() {
  const h=setup(); await h.boot();
  const analysis=h.analyze(); assert.equal(h.jobs.length,1);
  const readiness=h.requests.find(r=>r.url.startsWith('/api/insights/readiness?'));
  h.reply(readiness.url,{result:{meta:{range:h.people.A.context.range},features:[]}}); await tick();
  const switching=h.change('B');
  assert.equal(h.node('ins-analyze').disabled,true);
  await h.analyze(); assert.equal(h.jobs.length,1,'Analyze must not start while switching');
  h.reply('/api/chat/conversations/B',{conversation:h.people.B}); await tick();
  h.jobs[0].d.resolve(h.result('A')); await tick(); await analysis;
  assert.ok(!h.node('finding-groups').textContent.includes('ONLY-A'),'Late A analysis leaked into B');
  assert.ok(h.node('ins-window').textContent.includes('2026-02-01'));
  assert.equal(h.node('ins-analyze').disabled,true,'Analyze must wait for conversation messages');
  h.reply('/api/chat/conversations/B/messages?limit=100',{messages:[]}); await switching; await tick();
  assert.equal(h.node('ins-analyze').disabled,false);
  assert.equal(h.node('finding-groups').children.length,0);
}
async function detailReordering() {
  const h=setup(); await h.boot();
  const b=h.change('B'), c=h.change('C');
  h.reply('/api/chat/conversations/C',{conversation:h.people.C}); await tick();
  h.reply('/api/chat/conversations/C/messages?limit=100',{messages:[{role:'assistant',content:'ONLY-C-MESSAGE'}]}); await c; await tick();
  h.reply('/api/chat/conversations/B',{conversation:h.people.B}); await b; await tick();
  assert.equal(h.storage.get('hermes.insight.conversation'),'C');
  assert.ok(h.node('ins-window').textContent.includes('2026-03-01'));
  assert.ok(h.node('ins-chat').textContent.includes('ONLY-C-MESSAGE'));
  assert.equal(h.requests.length,0,'Stale B detail must not fetch B messages');
  assert.equal(h.node('ins-analyze').disabled,false);
}
async function messageReordering() {
  const h=setup(); await h.boot();
  const b=h.change('B'); h.reply('/api/chat/conversations/B',{conversation:h.people.B}); await tick();
  const c=h.change('C'); h.reply('/api/chat/conversations/C',{conversation:h.people.C}); await tick();
  h.reply('/api/chat/conversations/C/messages?limit=100',{messages:[{role:'assistant',content:'ONLY-C-MESSAGE'}]}); await c; await tick();
  h.reply('/api/chat/conversations/B/messages?limit=100',{messages:[{role:'assistant',content:'FORBIDDEN-B-MESSAGE'}]}); await b; await tick();
  assert.equal(h.storage.get('hermes.insight.conversation'),'C');
  assert.ok(h.node('ins-chat').textContent.includes('ONLY-C-MESSAGE'));
  assert.ok(!h.node('ins-chat').textContent.includes('FORBIDDEN-B-MESSAGE'));
  assert.equal(h.node('ins-analyze').disabled,false);
}
async function currentAnalysisRenders() {
  const h=setup(); await h.boot(); const analysis=h.analyze();
  const readiness=h.requests.find(r=>r.url.startsWith('/api/insights/readiness?'));
  h.reply(readiness.url,{result:{meta:{range:h.people.A.context.range},features:[]}});
  h.jobs[0].d.resolve(h.result('A')); await analysis; await tick();
  assert.ok(h.node('finding-groups').textContent.includes('ONLY-A'));
  assert.equal(h.node('ins-scope-status').textContent,'Analysis ready.');
}

function evidenceResult(h) {
  const range=h.people.A.context.range;
  const fingerprint='sha256:fictional-analysis-fingerprint';
  return {
    meta:{analysis_range:range,baseline_range:{kind:'bounded',from:'2025-12-01',to:range.to},input_fingerprint:fingerprint,engine_sha256:'sha256:fictional-engine'},
    coverage:{source_manifests:[{table:'sleep_log',digest:'sha256:fictional-source-manifest'}],dependencies:{goal_revision_ids:['fictional-goal-revision']}},
    warnings:['Result-level limitation'],
    findings:[{
      finding_id:'fictional-sleep-finding',candidate_key:'fictional-sleep-candidate',
      outcome:{key:'subjective.day_rating',mode:'ordinal'},
      exposure:{components:[{display:'Sleep <script>unsafe()</script>',exposure_key:'sleep.duration_hours',lag_days:1,window_days:7,direction:'target_range',merge_rule:'fictional-source-rule'}]},
      sample:{eligible_n:50,complete_n:40,missing_n:10},
      effect:{method:'spearman',estimate:0,ci95:[-0.2,0.4]},
      testing:{q:0.23,p:0.12,seed:'fictional-testing-seed'},
      stability:{status:'unstable'},quality:{tier:'exploratory_unreplicated',eligible_for_hypothesis:true},
      warnings:['association_not_causation'],confounders:{sensitive_to:['training day'],unchecked:[{key:'illness',reason:'missing illness records'}]},
      evidence_for:[{code:'sample_gate_pass',value:40}],evidence_against:[{code:'interval_includes_zero'}],
      provenance:{input_fingerprint:fingerprint,engine_sha256:'sha256:fictional-engine',evidence_fingerprint:'sha256:fictional-evidence',dependencies:{goal_revision_ids:['fictional-goal-revision']}},
    }],
  };
}

async function render(h, result, readiness={meta:{range:h.people.A.context.range},features:[]}) {
  const analysis=h.analyze();
  const request=h.requests.find(r=>r.url.startsWith('/api/insights/readiness?'));
  h.reply(request.url,{result:readiness});
  h.jobs.at(-1).d.resolve({status:'completed',result});
  await analysis;
  await tick();
}

async function readinessDetails() {
  const h=setup(); await h.boot();
  const readiness={
    meta:{range:h.people.A.context.range,state_counts:{logic_not_implemented:1,present_not_connected:0,implemented_never_logged:0,stale:1,too_sparse_for_analysis:1,sufficient:0,future_engine_state:1}},
    features:[
      {feature_key:'unimplemented.feature',state:'logic_not_implemented',needed:'Owner-approved computation contract'},
      {feature_key:'stale.feature',state:'stale',needed:'Refresh the protocol-valid record',latest_at:'2025-12-03',stale_after_days:30,prerequisites:['paired.record'],factors:{gate_failures:[],staleness_anchor:'2026-01-28'}},
      {feature_key:'sparse.feature',state:'too_sparse_for_analysis',needed:'Collect aligned protocol-valid observations',observations:12,aligned_n:8,prerequisites:['outcome.record'],factors:{gate_failures:['aligned_n<30'],staleness_anchor:'2026-01-28'}},
      {feature_key:'future.feature',state:'future_engine_state',needed:'Future requirement <script>unsafe()</script>',factors:{new_gate:'fictional-new-gate'}},
    ],
  };

  await render(h,evidenceResult(h),readiness);
  const states=h.node('readiness-states').children;
  const sparse=states.find(item=>item.textContent.includes('sparse.feature'));
  sparse.open=true;
  const future=states.find(item=>item.textContent.includes('future.feature'));
  future.open=true;

  assert.equal(states.length,7);
  assert.ok(visibleText(states[1]).includes('Count: 0'));
  assert.ok(!visibleText(states[0]).includes('logic_not_implemented'));
  assert.ok(states[0].textContent.includes('implementation work'));
  assert.ok(visibleText(sparse).includes('aligned_n<30'));
  assert.ok(visibleText(sparse).includes('outcome.record'));
  assert.ok(visibleText(sparse).includes('2026-01-28'));
  assert.ok(visibleText(future).includes('<script>unsafe()</script>'));
  assert.ok(future.textContent.includes('fictional-new-gate'));
  assert.ok(states.find(item=>item.textContent.includes('stale.feature')).textContent.includes('2025-12-03'));
}

async function findingSummary() {
  const h=setup(); await h.boot();
  const result=evidenceResult(h);
  result.findings.push({...result.findings[0],finding_id:'fictional-insufficient-finding',quality:{tier:'insufficient'},effect:{estimate:null,ci95:null}});

  await render(h,result);
  const cards=descendants(h.node('finding-groups')).filter(item=>item.tagName==='article');
  const card=cards[0];
  const details=card.children.find(item=>item.tagName==='details');
  const before=visibleText(card);
  details.open=true;
  const expanded=visibleText(card);
  details.open=false;

  assert.ok(before.includes('Sleep <script>unsafe()</script>'));
  assert.ok(before.includes('50, 40, 10'));
  assert.ok(before.includes('-0.2, 0.4'));
  assert.ok(before.includes('association_not_causation'));
  assert.ok(before.includes('interval_includes_zero'));
  assert.ok(before.includes('training day'));
  assert.ok(before.includes('illness'));
  assert.ok(!before.includes('fictional-testing-seed'));
  assert.ok(expanded.includes('fictional-testing-seed'));
  assert.ok(expanded.includes('fictional-source-rule'));
  assert.ok(expanded.includes('fictional-sleep-finding'));
  assert.ok(!visibleText(card).includes('fictional-testing-seed'));
  assert.ok(visibleText(cards[1]).includes('Not available'));
  assert.equal(cards.length,2,'Every returned finding remains inspectable');
}

async function sharedMetadata() {
  const h=setup(); await h.boot();
  const result=evidenceResult(h);
  result.findings.push({...result.findings[0],finding_id:'fictional-distinct-finding',provenance:{...result.findings[0].provenance,input_fingerprint:'sha256:fictional-distinct-input',analysis_range:{from:'2025-11-01',to:'2025-11-30'}}});

  await render(h,result);
  const cards=descendants(h.node('finding-groups')).filter(item=>item.tagName==='article');
  const link=descendants(cards[0]).find(item=>item.tagName==='a');
  let defaultPrevented=false;
  link.listeners.click({preventDefault(){ defaultPrevented=true; }});

  assert.equal(h.node('analysis-evidence').open,true);
  assert.equal(h.node('analysis-evidence-summary').focused,true);
  assert.equal(h.node('analysis-evidence-summary').scrolled,true);
  assert.equal(defaultPrevented,true,'Fragment navigation must not override disclosure focus');
  assert.ok(h.node('analysis-evidence').textContent.includes('sha256:fictional-source-manifest'));
  assert.ok(h.node('analysis-evidence').textContent.includes('sha256:fictional-analysis-fingerprint'));
  assert.ok(!cards[0].textContent.includes('sha256:fictional-analysis-fingerprint'));
  assert.ok(!cards[0].textContent.includes('fictional-goal-revision'));
  assert.ok(cards[0].textContent.includes('sha256:fictional-evidence'));
  assert.ok(cards[1].textContent.includes('sha256:fictional-distinct-input'));
  assert.ok(cards[1].textContent.includes('2025-11-01'));
  assert.ok(visibleText(h.node('analysis-warnings')).includes('Result-level limitation'));
  assert.ok(h.node('analysis-window').textContent.includes('2026-01-01'));
  assert.ok(h.node('baseline-window').textContent.includes('2025-12-01'));
}

async function selection() {
  const h=setup(); await h.boot();
  const result=evidenceResult(h);
  await render(h,result);
  const ask=descendants(h.node('finding-groups')).find(item=>item.dataset.evidenceFinding);

  ask.listeners.click();
  const selected=ask['aria-pressed'];
  h.node('ins-input').value='Explain this evidence';
  h.node('ins-form').listeners.submit({preventDefault(){}});
  const request=h.requests.find(item=>item.url.endsWith('/send'));
  const body=JSON.parse(request.opts.body);

  assert.equal(selected,'true');
  assert.deepEqual(body.selected_findings,[{outcome:'subjective.day_rating',finding_id:'fictional-sleep-finding',input_fingerprint:'sha256:fictional-analysis-fingerprint'}]);
  assert.deepEqual(body.context.range,h.people.A.context.range);
  assert.equal(body.findings,undefined);
}

async function promotion() {
  const h=setup(); await h.boot();
  const result=evidenceResult(h);
  result.findings.push({...result.findings[0],finding_id:'fictional-ineligible-finding',quality:{tier:'insufficient',eligible_for_hypothesis:false}});
  await render(h,result);
  const actions=descendants(h.node('finding-groups')).filter(item=>item.tagName==='button' && item.textContent==='Track as a hypothesis');

  actions[0].listeners.click();
  const request=h.requests.find(item=>item.url.endsWith('/promote'));
  const body=JSON.parse(request.opts.body);

  assert.equal(actions.length,1);
  assert.deepEqual(body,{outcome:'subjective.day_rating',finding_id:'fictional-sleep-finding',input_fingerprint:'sha256:fictional-analysis-fingerprint',range:h.people.A.context.range});
}

async function contextClearsEvidence() {
  const h=setup(); await h.boot();
  await render(h,evidenceResult(h));
  h.node('analysis-evidence').open=true;

  h.change('B');

  assert.equal(h.node('analysis-evidence').hidden,true);
  assert.equal(h.node('analysis-evidence').textContent,'');
  assert.equal(h.node('analysis-warnings').hidden,true);
  assert.equal(h.node('analysis-warnings').textContent,'');
  assert.equal(h.node('readiness-states').textContent,'');
}

async function unknownReadinessStates() {
  const h=setup(); await h.boot();
  const readiness={meta:{state_counts:{constructor:1}},features:[
    {feature_key:'future.constructor-feature',state:'constructor',needed:'Fictional requirement'},
    {feature_key:'future.prototype-feature',state:'__proto__',needed:'Another fictional requirement'},
  ]};

  await render(h,evidenceResult(h),readiness);
  const states=h.node('readiness-states').children;
  const constructorState=states.find(item=>item.textContent.includes('future.constructor-feature'));
  const prototypeState=states.find(item=>item.textContent.includes('future.prototype-feature'));

  assert.ok(visibleText(constructorState).includes('Count: 1'));
  assert.ok(visibleText(prototypeState).includes('Count: Not available'));
  assert.ok(visibleText(states[0]).includes('Count: Not available'));
  assert.ok(!visibleText(states[0]).includes('Count: 0'));
  assert.ok(prototypeState.textContent.includes('__proto__'));
  assert.ok(constructorState.textContent.includes('Fictional requirement'));
}

async function lateSendResponse(response) {
  const h=setup(); await h.boot();
  h.node('ins-input').value='A-only question';
  h.node('ins-form').listeners.submit({preventDefault(){}});
  const switching=h.change('B');
  h.reply('/api/chat/conversations/B',{conversation:h.people.B}); await tick();
  h.reply('/api/chat/conversations/B/messages?limit=100',{messages:[{role:'assistant',content:'B conversation history'}]}); await switching;
  const result=evidenceResult(h);
  result.meta.analysis_range=h.people.B.context.range;
  result.findings[0].finding_id='fictional-B-finding';
  await render(h,result,{meta:{range:h.people.B.context.range},features:[]});
  const selected=descendants(h.node('finding-groups')).find(item=>item.dataset.evidenceFinding);
  selected.listeners.click();
  h.node('ins-input').value='B draft';
  h.node('ins-chat-state').textContent='B local status';

  h.reply('/api/chat/conversations/A/send',response);
  await tick();

  assert.equal(h.node('ins-chat').textContent,'B conversation history');
  assert.equal(h.node('ins-input').value,'B draft');
  assert.equal(h.node('ins-chat-state').textContent,'B local status');
  assert.equal(h.node('ins-input').focused,undefined);
  assert.equal(selected['aria-pressed'],'true');
  assert.equal(h.node('ins-selected-evidence').hidden,false);
  assert.equal(h.requests.length,0,'A late response must not reload B');
}

async function currentSendReloadsConversation() {
  const h=setup(); await h.boot();
  h.node('ins-input').value='Question in the current window';
  h.node('ins-form').listeners.submit({preventDefault(){}});

  h.reply('/api/chat/conversations/A/send',{reply:'Stored answer'}); await tick();
  h.reply('/api/chat/conversations/A',{conversation:{...h.people.A,title:'Updated conversation title'}}); await tick();
  h.reply('/api/chat/conversations/A/messages?limit=100',{messages:[{role:'assistant',content:'Stored answer'}]}); await tick();

  assert.equal(h.node('ins-chat').textContent,'Stored answer');
  assert.ok(h.node('ins-chat-subtitle').textContent.includes('Updated conversation title'));
  assert.equal(h.node('ins-input').disabled,false);
  assert.equal(h.node('ins-input').focused,true);
}

async function groupedInsufficientFindings() {
  const h=setup(); await h.boot();
  const result=evidenceResult(h);
  const finding=result.findings[0];
  result.findings=[
    {...finding,finding_id:'sparse-first',exposure:{components:[{display:'Sparse first'}]},quality:{tier:'insufficient'},evidence_against:[{code:'aligned_n'}]},
    {...finding,finding_id:'conflicting',exposure:{components:[{display:'Conflicting evidence'}]},effect:{estimate:-0.2,ci95:[-0.3,-0.1]},evidence_against:[{code:'opposite_direction'}]},
    {...finding,finding_id:'sparse-second',exposure:{components:[{display:'Sparse second'}]},quality:{tier:'insufficient'},evidence_against:[{code:'outcome_variation'}]},
    {...finding,finding_id:'sparse-green',outcome:{key:'subjective.day_rating',mode:'green-vs-non-green'},exposure:{components:[{display:'Sparse Green'}]},quality:{tier:'insufficient'},evidence_against:[{code:'aligned_n'}]},
  ];

  await render(h,result);
  const groups=descendants(h.node('finding-groups')).filter(item=>item.dataset.insufficientGroup);
  const collapsedText=visibleText(h.node('finding-groups'));
  h.node('findings-show-all').listeners.click();
  const expandedText=visibleText(h.node('finding-groups'));
  const asks=descendants(groups[0]).filter(item=>item.dataset.evidenceFinding);
  asks[1].listeners.click();
  groups[0].open=false;

  assert.ok(collapsedText.includes('Conflicting evidence'));
  assert.ok(collapsedText.includes('opposite_direction'));
  assert.ok(collapsedText.includes('aligned n (1)'));
  assert.ok(collapsedText.includes('outcome variation (1)'));
  assert.ok(!collapsedText.includes('Sparse first'));
  assert.ok(groups[0].children[0].textContent.includes('2 findings'));
  assert.deepEqual(asks.map(item=>item.dataset.evidenceFinding),['sparse-first','sparse-second']);
  assert.ok(expandedText.includes('Sparse first'));
  assert.ok(expandedText.includes('Sparse second'));
  assert.ok(expandedText.includes('Sparse Green'));
  assert.equal(asks[1]['aria-pressed'],'true');
  assert.ok(!visibleText(h.node('finding-groups')).includes('Sparse first'));
  assert.ok(visibleText(h.node('finding-groups')).includes('Conflicting evidence'));
}

async function allInsufficientPeriod() {
  const h=setup(); await h.boot();
  const result=evidenceResult(h);
  result.findings[0].quality={tier:'insufficient',eligible_for_hypothesis:false};
  result.findings[0].evidence_against=[];

  await render(h,result);

  assert.equal(h.node('findings-status').hidden,false);
  assert.ok(h.node('findings-status').textContent.includes('All findings in this window have insufficient evidence'));
  assert.ok(visibleText(h.node('finding-groups')).includes('No reason returned'));
  assert.equal(h.node('findings-show-all').hidden,false);
}

const scenarios={
  context:async()=>{for(const test of [analysisSwitch,detailReordering,messageReordering,currentAnalysisRenders]) await test();},
  readiness_details:readinessDetails,
  finding_summary:findingSummary,
  shared_metadata:sharedMetadata,
  selection,
  promotion,
  context_clears_evidence:contextClearsEvidence,
  unknown_readiness_states:unknownReadinessStates,
  late_send_success:()=>lateSendResponse({reply:'A-only reply'}),
  late_send_error:()=>lateSendResponse({ok:false,error:'A-only error'}),
  current_send:currentSendReloadsConversation,
  insufficient_groups:groupedInsufficientFindings,
  all_insufficient:allInsufficientPeriod,
};
const scenario=process.argv[2];
Promise.resolve().then(()=>scenarios[scenario]()).then(()=>console.log(`PASS ${scenario}`)).catch(error=>{console.error(error);process.exitCode=1;});
