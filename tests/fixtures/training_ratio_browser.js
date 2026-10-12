const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

class Element {
  constructor(id='') { this.id=id; this.children=[]; this.listeners={}; this.style={}; this.hidden=false; this.value=''; this.textContent=''; }
  replaceChildren(...children) { this.children=children; }
  appendChild(child) { this.children.push(child); }
  append(...children) { this.children.push(...children); }
  addEventListener(name, callback) { this.listeners[name]=callback; }
}

async function main() {
  const nodes=new Map(), charts=new Map(), tabs=new Map();
  const node=id=>{ if(!nodes.has(id)) nodes.set(id,new Element(id)); return nodes.get(id); };
  const ratio={key:'hq',num_label:'Hamstring',den_label:'Quadriceps',per_side:true,
    evidence:'descriptive',cite:'Fictional <source>',band:null,ideal:null,
    side_gap:0.375,side_gap_flag:'descriptive',side_gap_limitations:['setup_unknown'],
    sides:[
      {side:'left',ratio:0.5,num_value:50,den_value:100,flag:'trend_only',protocol_valid:true,limitations:['setup_unknown']},
      {side:'right',ratio:0.8,num_value:80,den_value:100,flag:'outside_protocol_range',protocol_valid:false,num_reps:8,den_reps:15,limitations:['outside_protocol_range']},
    ]};
  const missing={key:'elbow',num_label:'Elbow flexors',den_label:'Elbow extensors',per_side:true,
    sides:[{side:'left',status:'insufficient_data',limitations:['zero_denominator']},
           {side:'right',status:'insufficient_data'}]};
  const mcgill={key:'mcgill-sbe',num_label:'Side bridge',den_label:'Trunk extensors',per_side:true,
    evidence:'descriptive',cite:'Fictional hold reference',
    reference:{typical:0.65,range:[0.55,0.75],evidence:'strong',measured_with:'a fictional <hold> protocol'},sides:[
      {side:'left',num_side:'left',den_side:'bilateral',ratio:0.6,num_value:60,den_value:100,limitations:[],parent_ids:['left-hold','bilateral-hold'],reference_position:'within_research_range'},
      {side:'right',num_side:'right',den_side:'bilateral',ratio:0.8,num_value:80,den_value:100,limitations:[],parent_ids:['right-hold','bilateral-hold'],reference_position:'above_research_range'},
    ]};
  async function fetch(url) {
    if(url==='/api/training/exercises') return {status:200,ok:true,json:async()=>({exercises:[]})};
    if(url==='/api/training/strength-ratios?view=tested') return {status:200,ok:true,json:async()=>({result:{rows:[ratio,missing,mcgill],caveat:'Descriptive comparisons only.'}})};
    if(url.startsWith('/api/training/muscle-map?')) return {status:200,ok:true,json:async()=>({result:{lens:'strength-balance',regions:{},unrepresentable:[]}})};
    return {status:503,ok:false,json:async()=>({error:'Fictional unrelated panel unavailable'})};
  }
  const context={
    document:{documentElement:{},getElementById:node,createElement:()=>new Element()},
    window:{HermesCharts:{pal:()=>({}),tip:()=>({}),lineOpt:()=>({}),gaugeOpt:()=>({}),isoDaysAgo:()=>''},
      HermesUI:{range:()=>null,onRange(){},onTab:(name,callback)=>tabs.set(name,callback)},
      MuscleFigure:{create:()=>({update(){}}),legend(){}},addEventListener(){}},
    echarts:{init:element=>{ const chart={setOption:options=>charts.set(element.id,options),dispose(){},resize(){}}; return chart; }},
    getComputedStyle:()=>({getPropertyValue:name=>name}),fetch,location:{assign(){}},console,
  };
  vm.runInNewContext(fs.readFileSync('app/static/js/training.js','utf8'),context);
  for(let n=0;n<100;n++) await Promise.resolve();

  const options=charts.get('ch-ratios');
  const tooltip=index=>options.tooltip.formatter([{dataIndex:index}]);
  assert.equal(options.series[1].label.formatter({dataIndex:0}),'0.5 (recorded)');
  assert.equal(options.series[1].label.formatter({dataIndex:1}),'0.8 (recorded)');
  assert.equal(options.series[1].label.formatter({dataIndex:2}),'ratio unavailable');
  assert.equal(options.series[1].label.formatter({dataIndex:3}),'needs test');
  assert.equal(options.series[0].data[0].value,-1/3);
  assert.equal(options.series[0].data[0].itemStyle.color,'--ch-1');
  assert.equal(options.series[0].data[1].itemStyle.color,'--ch-1');
  assert.match(tooltip(0),/ratio 0.5 · no validated target range/);
  assert.match(tooltip(0),/L\/R gap 37.5% \(descriptive\)/);
  assert.match(tooltip(0),/setup unknown/);
  assert.match(tooltip(0),/Fictional &lt;source&gt;/);
  assert.match(tooltip(1),/outside preferred 6–8 reps/);
  assert.match(tooltip(2),/zero denominator/);
  assert.equal(options.yAxis.data[4],'left Side bridge / bilateral Trunk extensors');
  assert.equal(options.yAxis.data[5],'right Side bridge / bilateral Trunk extensors');
  assert.match(tooltip(4),/ratio 0.6/);
  assert.match(tooltip(4),/one bilateral measurement shared across side ratios/);
  assert.match(tooltip(5),/not independent left\/right measurements/);
  // Published research context is shown, labelled and never coloured as a verdict.
  assert.equal(options.series[1].label.formatter({dataIndex:4}),'0.6 (recorded) · research ≈0.65');
  assert.match(tooltip(4),/Research: typical 0.65 · range 0.55–0.75 · strong evidence/);
  assert.match(tooltip(4),/Your ratio is within the research range/);
  assert.match(tooltip(5),/Your ratio is above the research range/);
  assert.match(tooltip(4),/a fictional &lt;hold&gt; protocol\. Context, not a personal target/);
  assert.doesNotMatch(tooltip(4),/no validated target range/);
  assert.equal(options.series[0].data[4].itemStyle.color,'--ch-1');
  assert.equal(options.series[0].data[5].itemStyle.color,'--ch-1');
  const marker=options.series[2];
  assert.equal(marker.type,'scatter');
  assert.equal(JSON.stringify(marker.data),JSON.stringify([[-0.65/1.65,4],[-0.65/1.65,5]]));
  assert.match(node('ratio-note').textContent,/◇ marks the typical ratio in published research \(context, not a target\)/);
  assert.equal(tabs.has('mflens'),true);
  assert.equal(tabs.has('mfside'),true);
  assert.match(node('mf-note').textContent,/shared regions are not independent measurements/);
  console.log('PASS descriptive training ratios');
}
main().catch(error=>{ console.error(error); process.exitCode=1; });
