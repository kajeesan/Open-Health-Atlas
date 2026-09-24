const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = 'app/static/js/nutrition.js';
const tick = async () => { for (let n=0;n<60;n++) await Promise.resolve(); };
class Element {
  constructor(id='') { this.id=id; this.children=[]; this.dataset={}; this.listeners={}; this.style={}; this.disabled=false; this.hidden=false; this.value=''; this._text=''; this._html=''; this.classList={toggle(){},add(){},remove(){}}; }
  set textContent(value) { this._text=String(value); this._html=''; this.children=[]; }
  get textContent() { return this._text+this.children.map(item=>item.textContent||'').join(''); }
  set innerHTML(value) { this._html=String(value); this._text=''; this.children=[]; }
  get innerHTML() { return this._html || this._text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.children.push(node); return node; }
  replaceChildren(...nodes) { this._text=''; this._html=''; this.children=nodes; }
  setAttribute(key,value) { this[key]=value; }
  addEventListener(key,handler) { this.listeners[key]=handler; }
  focus() { this.focused=true; }
  remove() {}
}
function setup(targets, {transportError=null, batchError=null}={}) {
  const nodes=new Map(), reads=[], writes=[], events={};
  const node=id=>{ if(!nodes.has(id)) nodes.set(id,new Element(id)); return nodes.get(id); };
  const pills=['all','gaps','protein'].map(name=>{ const item=new Element(); item.dataset.pill=name; return item; });
  const endpoints={
    '/api/nutrition/today':{totals:{kcal:600,protein_g:50,carbs_g:36,fat_g:24,fiber_g:8},micros:{},entries:[{food_name:'Fictional stew',grams:400,kcal:600,protein_g:50}]},
    '/api/dash/today':{water_ml:1500},
    '/api/nutrition/water?days=7':{rows:[]},
    '/api/nutrition/calories?days=7':{rows:[]},
    '/api/nutrition/coverage?days=7':{result:{status:'insufficient_data',rows:[]}},
    '/api/nutrition/menu':{result:{menu:[]}},
    '/api/nutrition/recipes':{recipes:[]},
    '/api/nutrition/supplements':{products:[],today:[]},
    '/api/nutrition/supplements/adherence?days=7':{active_products:0,rows:[]},
  };
  async function fetchJSON(url) {
    reads.push(url);
    if(url==='/api/nutrition/targets') {
      if(transportError) throw new Error(transportError);
      return {result:targets};
    }
    assert.ok(Object.hasOwn(endpoints,url),`Unexpected read: ${url}`);
    return endpoints[url];
  }
  async function fetch(url,options) {
    writes.push({url,body:JSON.parse(options.body)});
    return {status:400,ok:false,json:async()=>({ok:false,error:batchError})};
  }
  const context={
    document:{getElementById:node,querySelector:()=>({content:'csrf'}),querySelectorAll:selector=>selector==='#nPills .pill'?pills:[],createElement:()=>new Element()},
    window:{HermesCharts:{fetchJSON,pal:()=>({}),gaugeOpt:options=>({series:[{}],options}),lineOpt:()=>({series:[{}]})},HermesUI:{range:key=>key==='nutrition'?'Week':'All meals',onRange(){},onTab(){}},addEventListener:(name,handler)=>{events[name]=handler;}},
    echarts:{init:()=>({setOption(){},dispose(){},resize(){}})},fetch,
    location:{assign(){}},requestAnimationFrame:handler=>handler(),setTimeout:()=>0,console,
  };
  vm.runInNewContext(fs.readFileSync(source,'utf8'),context,{filename:source});
  return {node,nodes,reads,writes,events};
}
async function unavailableTargets(reason) {
  const h=setup({status:'insufficient_data',reason,targets:{water_ml:{target:3000}}});

  await tick();

  assert.equal(h.node('nTgtBanner').hidden,false);
  assert.equal(h.node('nTgtReason').textContent,reason);
  assert.equal(h.node('nTgtReasonLabel').textContent,'Engine reason');
  assert.equal(h.node('nTgtReload').hidden,true);
  assert.equal(h.node('nKpiKcal').textContent,'600');
  assert.equal(h.node('nKpiProtein').textContent,'50');
  assert.equal(h.node('nTgtCalLabel').textContent,'Calories — 600 kcal logged');
  assert.equal(h.node('nTgtCal').textContent,'—');
  assert.equal(h.node('nTgtCalBar').children.length,0);
  assert.equal(h.node('nTgtWater').textContent,'50%');
  assert.equal(h.node('nKpiWaterU').textContent,'/ 3.0 L');
  assert.ok(!h.node('nTgtReason').innerHTML.includes('<script>'));
  assert.equal(h.reads.filter(url=>url==='/api/nutrition/targets').length,1);
}
async function targetTransportError() {
  const h=setup(null,{transportError:'Fictional broker is unavailable'});

  await tick();

  assert.equal(h.node('nTgtBanner').hidden,false);
  assert.equal(h.node('nTgtReasonLabel').textContent,'Request error');
  assert.equal(h.node('nTgtReason').textContent,'Fictional broker is unavailable');
  assert.equal(h.node('nTgtReload').hidden,false);
  assert.equal(h.node('nKpiKcal').textContent,'600');
  assert.equal(h.node('nTgtWater').textContent,'—');
  assert.equal(h.node('nTgtWaterBar').children.length,0);
}
async function readyTargets() {
  const h=setup({status:'ok',phase:{phase:'maintain',set:true,started:'2026-01-01'},targets:{kcal:{target:2000,band_low:1800,band_high:2200},protein_g:{target:120},carbs_g:{target:200},fat_g:{target:70},water_ml:{target:3000},micros:[]}});

  await tick();

  assert.equal(h.node('nTgtBanner').hidden,true);
  assert.equal(h.node('nTgtCal').textContent,'30% · target 2000kcal');
  assert.equal(h.node('nTgtWater').textContent,'50%');
  assert.equal(h.node('nPhaseMaintain')['aria-pressed'],'true');
  assert.ok(h.node('nPhaseCaption').textContent.includes('2026-01-01'));
}
async function batchErrorPersists() {
  const h=setup({status:'insufficient_data',reason:'no weight_kg in body_metrics — log a weight first',targets:{water_ml:{target:3000}}},{batchError:'Fictional portions validation failed'});
  await tick();
  h.node('nBatchForm').hidden=false;
  h.node('bp-recipe').value='fictional-stew';
  h.node('bp-portions').value='invalid';
  h.node('bp-grams').value='1500';

  await h.node('bp-btn').listeners.click();
  h.events.themechange();
  await tick();

  assert.equal(h.node('bp-status').hidden,false);
  assert.ok(h.node('bp-status').textContent.includes('Fictional portions validation failed'));
  assert.equal(h.node('bp-portions').value,'invalid');
  assert.equal(h.node('bp-grams').value,'1500');
  assert.equal(h.node('nBatchForm').hidden,false);
  assert.equal(h.node('bp-btn').disabled,false);
  assert.deepEqual(h.writes,[{url:'/api/nutrition/prep',body:{recipe:'fictional-stew',portions:'invalid',batch_grams:'1500'}}]);
}
const scenarios={
  missing_profile:()=>unavailableTargets('user profile incomplete — profile-set height_cm/sex/dob'),
  missing_weight:()=>unavailableTargets('no weight_kg in body_metrics — log a weight first'),
  missing_configuration:()=>unavailableTargets('nutrient targets are not configured <script>fictional()</script>'),
  transport_error:targetTransportError,
  ready:readyTargets,
  batch_error:batchErrorPersists,
};
const scenario=process.argv[2];
Promise.resolve().then(()=>scenarios[scenario]()).then(()=>console.log(`PASS ${scenario}`)).catch(error=>{console.error(error);process.exitCode=1;});
