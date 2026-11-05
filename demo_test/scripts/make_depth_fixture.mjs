// Analytic scores and skeletons; no camera, learned depth or physical-contact truth.
import fs from 'node:fs';
import path from 'node:path';
import {hand} from '../tests/tap-fixtures.mjs';
import {calibrate} from '../models/tap-detector.mjs';
import {DepthComparison,METHODS} from '../models/depth-detector.mjs';
const output=process.argv[2]||'outputs/depth-fixture.json';
const pose=calibrate(Array.from({length:8},()=>hand()),Array.from({length:8},()=>hand({index:1})),'index');
const calibration={index:{pose,depth:{base:0,delta:1,noise:0}}},engine=new DepthComparison(calibration);
const trial={id:'analytic-depth-fixture',person_id:'synthetic',session_id:'fixture',scenario:'analytic_cycle',split:'dev',
  duration_ms:1000,calibration,options:engine.options,request_interval_ms:100,
  model:{revision:'not_a_learned_depth_output',input_size:null},pose_frames:[],depth_frames:[],
  predictions:Object.fromEntries(METHODS.map(m=>[m,[]])),truth:null,annotation_confirmed:false,unknown_intervals:[]};
for(const [i,s] of [0,0,1,1,0,0,0,0].entries()){
  const t=i*100,world=hand({index:s}),depth={fingers:{index:{valid:true,contrast:s}}};
  const p=engine.update(t,world,null,null,t,true),d=engine.update(t,world,depth,null,t+30);
  trial.pose_frames.push({t_ms:t,emitted_ms:t,world_landmarks:world,landmarks:null,signals:p.signals});
  trial.depth_frames.push({t_ms:t,world_landmarks:world,landmarks:null,depth,roundtrip_ms:30,signals:d.signals});
  trial.predictions.pose_full.push(...p.events.pose_full);
  for(const m of ['pose_matched','depth','fusion'])trial.predictions[m].push(...d.events[m]);
}
fs.mkdirSync(path.dirname(output),{recursive:true});
fs.writeFileSync(output,JSON.stringify({schema_version:2,kind:'pthoven_depth_comparison',
  evidence_type:'synthetic_analytic_fixture',contact_verified:false,trials:[trial]},null,2)+'\n');
console.log(`Saved synthetic fixture (truth=null): ${output}`);
