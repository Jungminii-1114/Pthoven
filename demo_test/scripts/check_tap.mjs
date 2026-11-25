import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {PoseTapDetector,calibrate,FINGERS,evaluateEvents} from '../models/tap-detector.mjs';
import {hand,cycle,transform} from '../tests/tap-fixtures.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const args=process.argv.slice(2),arg=name=>{const i=args.indexOf(name);return i<0?null:args[i+1];};
function fresh(models){const d=new PoseTapDetector();for(const [f,m] of Object.entries(models))d.setCalibration(f,m);return d;}
const models=Object.fromEntries(Object.keys(FINGERS).map(f=>[f,calibrate(Array.from({length:8},()=>hand()),Array.from({length:8},()=>hand({[f]:1})),f)]));
function replay(frames,calibration){const d=fresh(calibration);return frames.flatMap(r=>d.update(r.t_ms,r.world_landmarks??r.landmarks).events);}
function stressCases(){
  const rows=[];
  for(const fps of [20,30,60])for(const jitter of [0,.01,.04])for(const dropout of [0,.1,.3]){
    let seed=42;const random=()=>{seed=(Math.imul(1664525,seed)+1013904223)>>>0;return seed/4294967296;};
    const frames=[];
    for(let i=0;i<Math.ceil(3000*fps/1000);i++){
      const t=i*1000/fps,phase=t%800;
      const lift=phase<200||phase>=400?0:Math.sin(Math.PI*(phase-200)/200);
      const landmarks=hand({index:lift}).map(p=>p.map(v=>v+(random()-.5)*jitter*2));
      frames.push({t_ms:t,landmarks:random()<dropout?null:landmarks});
    }
    const predictions=replay(frames,models);
    rows.push({fps,jitter_in_analytic_palm_units:jitter,dropout,metrics:evaluateEvents(predictions,[400,1200,2000,2800].map(t_ms=>({finger:'index',t_ms})),{durationMs:3000}),
      emitted_delay_ms:predictions.length?predictions.reduce((s,e)=>s+e.latency_ms,0)/predictions.length:null});
  }
  return rows;
}
let report;
if(arg('--capture')){
  const input=JSON.parse(fs.readFileSync(arg('--capture'),'utf8'));
  if(input.schema_version!==1||!Array.isArray(input.trials))throw Error('Expected tap-lab schema_version 1');
  report={evidence_type:input.evidence_type==='real_landmark_capture'?'real_landmark_replay':'synthetic_or_unverified_replay',contact_verified:false,created_at:new Date().toISOString(),trials:input.trials.map(t=>{
    const predictions=replay(t.frames,t.calibration);
    return {person_id:t.person_id,session_id:t.session_id,scenario:t.scenario,frame_count:t.frames.length,
      predictions,metrics:evaluateEvents(predictions,t.truth,{durationMs:t.duration_ms}),
      ground_truth_note:'truth must contain independently reviewed {finger,t_ms} events; cues and predicted events are not labels'};
  })};
}else{
  const stationary=Array.from({length:80},(_,i)=>({t_ms:i*25,landmarks:hand()}));
  const scenario=(name,frames,truth,note='ideal analytic skeleton; not a camera evaluation')=>{
    const predictions=replay(frames,models);
    return {name,note,predictions,metrics:evaluateEvents(predictions,truth,{durationMs:frames.at(-1).t_ms+25})};
  };
  const tap=cycle();
  report={evidence_type:'synthetic_logic_only',contact_verified:false,created_at:new Date().toISOString(),
    scope:'Analytic 3D skeletons bypass MediaPipe, RGB ambiguity, skin, exposure and camera latency. Do not report these results as human accuracy.',
    scenarios:[
      scenario('single_tap',tap,[{finger:'index',t_ms:200}]),
      scenario('stationary_fingers',stationary,[]),
      scenario('repeat_same_finger',[...tap,...cycle('index',400)],[{finger:'index',t_ms:200},{finger:'index',t_ms:600}]),
      scenario('lateral_slide',stationary.map((r,i)=>({...r,landmarks:transform(r.landmarks,{shift:[i*.01,0,0]})})),[]),
      scenario('whole_hand_rotation',stationary.map((r,i)=>({...r,landmarks:transform(r.landmarks,{angle:i*.02})})),[]),
      scenario('neighbor_moves',cycle('ring'),[{finger:'ring',t_ms:200}]),
      scenario('occlusion_mid_cycle',tap.map((r,i)=>({...r,landmarks:i>=4&&i<=7?null:r.landmarks})),[]),
      scenario('identical_pose_air_tap',tap,[],'KNOWN LIMIT: same observed pose as physical tap; expected false positive'),
      scenario('whole_hand_vertical_translation',stationary.map((r,i)=>({...r,landmarks:transform(r.landmarks,{shift:[0,0,Math.sin(i*.2)]})})),[],
        'Local coordinates intentionally discard global height: no absolute desk-height estimate'),
    ],
    stress_cases:stressCases(),
    pending:['real_tap_precision_recall','air_tap_rejection_in_RGB','per_finger_latency_on_camera','skin_vs_pose_ablation','lighting','held_out_people_and_sessions'],
  };
}
const output=arg('--output')||path.join(root,'outputs','tap-synthetic.json');
fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));console.log(`Saved: ${output}`);
