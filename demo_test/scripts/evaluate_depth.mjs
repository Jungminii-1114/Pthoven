// Usage: node scripts/evaluate_depth.mjs capture.json [capture2.json ...]
import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {METHODS,evaluateTrial} from '../models/depth-detector.mjs';
const paths=process.argv.slice(2);if(!paths.length){console.error('Usage: node scripts/evaluate_depth.mjs capture.json [...]');process.exit(1);}
const trials=paths.flatMap(path=>{const d=JSON.parse(fs.readFileSync(path,'utf8'));if(d.schema_version!==2||d.kind!=='pthoven_depth_comparison')throw Error(`Invalid capture: ${path}`);return d.trials;});
const devPeople=new Set(trials.filter(t=>t.split==='dev').map(t=>t.person_id));
const overlaps=[...new Set(trials.filter(t=>t.split==='test'&&devPeople.has(t.person_id)).map(t=>t.person_id))];
const results=trials.map(t=>({id:t.id,person_id:t.person_id,session_id:t.session_id,scenario:t.scenario,split:t.split,
  config_id:createHash('sha256').update(JSON.stringify({options:t.options,model_revision:t.model?.revision,input_size:t.model?.input_size,interval:t.request_interval_ms})).digest('hex').slice(0,12),result:evaluateTrial(t)}));
const cohorts={};
function aggregate(selected){
  const group={labelled_trials:selected.length,methods:{}};
  for(const method of METHODS){let tp=0,fp=0,fn=0,duration=0;const coverage=[];
    for(const x of selected){const m=x.result.methods[method];tp+=m.tp;fp+=m.fp;fn+=m.fn;duration+=x.result.known_duration_ms;coverage.push(m.coverage_ratio);}
    group.methods[method]={tp,fp,fn,f1:2*tp+fp+fn?2*tp/(2*tp+fp+fn):null,false_events_per_minute:duration>0?fp/(duration/60000):null,mean_coverage:coverage.length?coverage.reduce((a,b)=>a+b,0)/coverage.length:null};
  }
  return group;
}
for(const split of ['dev','test']){
  const selected=results.filter(r=>r.split===split&&r.result.labelled);
  cohorts[split]={...aggregate(selected),by_scenario:{},by_configuration:{}};
  for(const scenario of new Set(selected.map(r=>r.scenario)))cohorts[split].by_scenario[scenario]=aggregate(selected.filter(r=>r.scenario===scenario));
  for(const config of new Set(selected.map(r=>r.config_id)))cohorts[split].by_configuration[config]=aggregate(selected.filter(r=>r.config_id===config));
}
console.log(JSON.stringify({contact_verified:false,source_types:paths.map(path=>JSON.parse(fs.readFileSync(path,'utf8')).evidence_type),user_overlap_between_dev_test:overlaps,
  note:overlaps.length?'사용자 일반화 평가로 해석하지 마세요. 개발/평가 사용자 ID가 겹칩니다.':'미관측 사용자 평가에는 사용자와 촬영 세션을 분리하고 설정을 고정하세요.',
  cohorts,trials:results},null,2));
