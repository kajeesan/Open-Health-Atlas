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
function setup(targets, {transportError=null, batchError=null, profileError=null, saveError=null, afterSaveTargets=null, targetReads=null, profileReads=null}={}) {
  const nodes=new Map(), reads=[], writes=[], events={};
  const node=id=>{ if(!nodes.has(id)) nodes.set(id,new Element(id)); return nodes.get(id); };
  const pills=['all','gaps','protein'].map(name=>{ const item=new Element(); item.dataset.pill=name; return item; });
  const endpoints={
    '/api/nutrition/profile':{result:{profile:{height_cm:null,sex:null,dob:null},weight:null,canonical_today:'2026-01-15'}},
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
    if(url==='/api/nutrition/profile' && profileError) throw new Error(profileError);
    if(url==='/api/nutrition/profile' && profileReads) return new Promise(resolve=>profileReads.push(value=>resolve({result:value})));
    if(url==='/api/nutrition/targets') {
      if(transportError) throw new Error(transportError);
      if(targetReads) return new Promise(resolve=>targetReads.push(value=>resolve({result:value})));
      return {result:targets};
    }
    assert.ok(Object.hasOwn(endpoints,url),`Unexpected read: ${url}`);
    return endpoints[url];
  }
  async function fetch(url,options) {
    writes.push({url,body:JSON.parse(options.body)});
    if(url==='/api/nutrition/profile' || url==='/api/log/body') {
      assert.equal(options.headers['X-CSRFToken'],'csrf');
      assert.equal(options.credentials,'same-origin');
      if(saveError) return {status:400,ok:false,json:async()=>({ok:false,error:saveError})};
      if(url==='/api/nutrition/profile') endpoints[url].result.profile=JSON.parse(options.body);
      if(url==='/api/log/body') endpoints['/api/nutrition/profile'].result.weight={weight_kg:JSON.parse(options.body).weight_kg,date:'2026-01-15'};
      if(afterSaveTargets) targets=afterSaveTargets;
      return {status:200,ok:true,json:async()=>({ok:true,result:{ok:true}})};
    }
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
function readyProfileTargets(kcal=2000) {
  return {status:'ok',phase:{phase:'maintain',set:false},targets:{kcal:{target:kcal,band_low:kcal*0.9,band_high:kcal*1.1},protein_g:{target:120},carbs_g:{target:200},fat_g:{target:70},water_ml:{target:3000},micros:[]}};
}
async function profileSaveRefreshesTargets() {
  const h=setup({status:'insufficient_data',reason:'profile missing',targets:{water_ml:{target:1800}}},{afterSaveTargets:readyProfileTargets()});
  await tick();
  assert.equal(h.node('nutrition-profile-editor').open,true);
  assert.equal(h.node('nProfileHeight').value,'');
  assert.equal(h.node('nProfileSex').value,'');
  assert.equal(h.node('nProfileDob').value,'');
  assert.equal(h.node('nProfileDob').max,'2026-01-15');
  h.node('nProfileHeight').value='165';
  h.node('nProfileSex').value='female';
  h.node('nProfileDob').value='2001-01-15';

  await h.node('nProfileForm').listeners.submit({preventDefault(){}});

  assert.deepEqual(h.writes,[{url:'/api/nutrition/profile',body:{height_cm:'165',sex:'female',dob:'2001-01-15'}}]);
  assert.equal(h.reads.filter(url=>url==='/api/nutrition/targets').length,2);
  assert.equal(h.node('nTgtBanner').hidden,true);
  assert.equal(h.node('nProfileSave').disabled,false);
  assert.match(h.node('nProfileStatus').textContent,/Profile saved/);
  assert.equal(h.node('nKpiKcal').textContent,'600');
}
async function failedProfileSavePreservesEntries() {
  const h=setup({status:'insufficient_data',reason:'profile missing',targets:{water_ml:{target:1800}}},{saveError:'Fictional broker unavailable'});
  await tick();
  h.node('nProfileHeight').value='165';
  h.node('nProfileSex').value='female';
  h.node('nProfileDob').value='2001-01-15';

  await h.node('nProfileForm').listeners.submit({preventDefault(){}});
  h.events.themechange();
  await tick();

  assert.match(h.node('nProfileStatus').textContent,/save was not confirmed/);
  assert.equal(h.node('nProfileHeight').value,'165');
  assert.equal(h.node('nProfileSex').value,'female');
  assert.equal(h.node('nProfileDob').value,'2001-01-15');
  assert.equal(h.node('nProfileSave').disabled,false);
  assert.equal(h.node('nProfileRetry').hidden,false);
  assert.equal(h.reads.filter(url=>url==='/api/nutrition/targets').length,1);
}
async function failedProfileLoadBlocksOverwrite() {
  const h=setup(readyProfileTargets(),{profileError:'Fictional read unavailable'});
  await tick();

  await h.node('nProfileForm').listeners.submit({preventDefault(){}});

  assert.equal(h.node('nProfileSave').disabled,true);
  assert.equal(h.node('nProfileRetry').hidden,false);
  assert.equal(h.node('nProfileRetry').disabled,false);
  assert.equal(h.node('nProfileWeightSave').disabled,false);
  assert.match(h.node('nProfileStatus').textContent,/Could not load/);
  assert.deepEqual(h.writes,[]);
}
async function weightLoggingRequiresAnExplicitMeasurement() {
  const h=setup({status:'insufficient_data',reason:'weight missing',targets:{water_ml:{target:1800}}},{afterSaveTargets:readyProfileTargets()});
  await tick();
  assert.equal(h.node('nProfileWeightInput').value,'');
  assert.deepEqual(h.writes,[]);
  h.node('nProfileHeight').value='169';
  h.node('nProfileWeightInput').value='62.5';

  await h.node('nProfileWeightForm').listeners.submit({preventDefault(){}});

  assert.deepEqual(h.writes,[{url:'/api/log/body',body:{weight_kg:62.5}}]);
  assert.equal(h.node('nProfileWeightInput').value,'');
  assert.equal(h.node('nProfileHeight').value,'169');
  assert.match(h.node('nProfileWeight').textContent,/62.5 kg/);
  assert.match(h.node('nProfileWeightStatus').textContent,/Weight logged/);
  assert.equal(h.reads.filter(url=>url==='/api/nutrition/targets').length,2);
}
async function profileAndWeightSavesAreSerialized(firstForm, secondForm, secondButton) {
  const targetReads=[];
  const h=setup(readyProfileTargets(),{targetReads});
  await tick();
  targetReads[0](readyProfileTargets());
  await tick();
  h.node('nProfileHeight').value='165';
  h.node('nProfileSex').value='female';
  h.node('nProfileDob').value='2001-01-15';
  h.node('nProfileWeightInput').value='62.5';

  const firstSave=h.node(firstForm).listeners.submit({preventDefault(){}});
  await tick();
  assert.equal(targetReads.length,2);
  assert.equal(h.node(secondButton).disabled,true);
  await h.node(secondForm).listeners.submit({preventDefault(){}});
  assert.equal(h.writes.length,1);
  targetReads[1](readyProfileTargets(2200));
  await firstSave;
  const secondSave=h.node(secondForm).listeners.submit({preventDefault(){}});
  await tick();
  assert.equal(targetReads.length,3);
  targetReads[2](readyProfileTargets(2400));
  await secondSave;

  assert.equal(h.writes.length,2);
  assert.match(h.node('nTgtCal').textContent,/target 2400kcal/);
  assert.equal(h.node('nProfileSave').disabled,false);
  assert.equal(h.node('nProfileWeightSave').disabled,false);
}
async function staleInitialTargetsCannotOverwriteSavedTargets() {
  const targetReads=[];
  const h=setup(readyProfileTargets(),{targetReads});
  await tick();
  h.node('nProfileHeight').value='165';
  h.node('nProfileSex').value='female';
  h.node('nProfileDob').value='2001-01-15';

  const save=h.node('nProfileForm').listeners.submit({preventDefault(){}});
  await tick();
  assert.equal(targetReads.length,2);
  targetReads[1](readyProfileTargets(2400));
  await save;
  assert.match(h.node('nTgtCal').textContent,/target 2400kcal/);
  targetReads[0](readyProfileTargets(2000));
  await tick();

  assert.match(h.node('nTgtCal').textContent,/target 2400kcal/);
  assert.match(h.node('nKpiKcalSub').textContent,/2,400/);
  assert.match(h.node('nProfileStatus').textContent,/Profile saved/);
}
async function weightSaveWaitsForInitialProfileRead() {
  const profileReads=[];
  const h=setup(readyProfileTargets(),{profileReads});
  await tick();
  h.node('nProfileWeightInput').value='72';

  const blocked=h.node('nProfileWeightForm').listeners.submit({preventDefault(){}});
  await tick();

  assert.equal(h.writes.length,0);
  await blocked;
  assert.equal(h.node('nProfileWeightSave').disabled,true);
  assert.equal(h.node('nProfileWeightInput').value,'72');
  profileReads[0]({profile:{height_cm:165,sex:'female',dob:'2001-01-15'},weight:null,canonical_today:'2026-01-15'});
  await tick();
  const save=h.node('nProfileWeightForm').listeners.submit({preventDefault(){}});
  await tick();
  assert.equal(profileReads.length,2);
  profileReads[1]({profile:{height_cm:165,sex:'female',dob:'2001-01-15'},weight:{weight_kg:72,date:'2026-01-15'},canonical_today:'2026-01-15'});
  await save;

  assert.equal(h.writes.length,1);
  assert.match(h.node('nProfileWeight').textContent,/Latest weight used: 72 kg/);
  assert.match(h.node('nProfileWeightStatus').textContent,/Weight logged/);
  assert.equal(h.node('nProfileHeight').value,165);
  assert.equal(h.node('nProfileWeightSave').disabled,false);
}
const scenarios={
  missing_profile:()=>unavailableTargets('user profile incomplete — profile-set height_cm/sex/dob'),
  missing_weight:()=>unavailableTargets('no weight_kg in body_metrics — log a weight first'),
  missing_configuration:()=>unavailableTargets('nutrient targets are not configured <script>fictional()</script>'),
  transport_error:targetTransportError,
  ready:readyTargets,
  batch_error:batchErrorPersists,
  profile_save:profileSaveRefreshesTargets,
  profile_error:failedProfileSavePreservesEntries,
  profile_load_error:failedProfileLoadBlocksOverwrite,
  weight_save:weightLoggingRequiresAnExplicitMeasurement,
  profile_weight_serialized:()=>profileAndWeightSavesAreSerialized('nProfileForm','nProfileWeightForm','nProfileWeightSave'),
  weight_profile_serialized:()=>profileAndWeightSavesAreSerialized('nProfileWeightForm','nProfileForm','nProfileSave'),
  stale_initial_targets:staleInitialTargetsCannotOverwriteSavedTargets,
  stale_initial_profile:weightSaveWaitsForInitialProfileRead,
};
const scenario=process.argv[2];
Promise.resolve().then(()=>scenarios[scenario]()).then(()=>console.log(`PASS ${scenario}`)).catch(error=>{console.error(error);process.exitCode=1;});
