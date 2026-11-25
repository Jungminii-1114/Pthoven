import test from 'node:test';
import assert from 'node:assert/strict';
import {calibrateDepth,depthSignal,evaluateTrial,DepthComparison} from '../models/depth-detector.mjs';
import {calibrate} from '../models/tap-detector.mjs';
import {hand} from './tap-fixtures.mjs';

const row=contrast=>({depth:{fingers:{index:{valid:true,contrast}}}});
test('depth calibration learns direction without treating scores as metric height',()=>{
  const c=calibrateDepth(Array.from({length:8},()=>row(.4)),Array.from({length:8},()=>row(.1)),'index');
  assert.equal(depthSignal(row(.1).depth,c,'index').score,1);
  assert.ok(Math.abs(depthSignal(row(.4).depth,c,'index').score)<1e-12);
  assert.throws(()=>calibrateDepth(Array(8).fill(row(.4)),Array(8).fill(row(.401)),'index'),/구분/);
});
test('fusion withholds when depth is unavailable, while depth alone uses only its signal',()=>{
  const e=new DepthComparison({index:{pose:null,depth:{base:0,delta:1}}});
  const phases=[[0,0],[50,0],[100,1],[200,0],[250,0]];
  const found=[];
  for(const [t,s] of phases){const o=e.update(t,null,row(s).depth,null,t+80);found.push(...o.events.depth);assert.equal(o.events.fusion.length,0);}
  assert.equal(found.length,1);assert.equal(found[0].t_ms,200);assert.equal(found[0].emitted_ms,330);
  assert.equal(found[0].contact_verified,false);
});
test('missing truth is not a negative trial and unknown periods do not become errors',()=>{
  const t={duration_ms:1000,truth:null,predictions:{depth:[{finger:'index',t_ms:500,emitted_ms:600}]},depth_frames:[]};
  assert.equal(evaluateTrial(t).methods.depth.f1,null);
  t.truth=[];t.annotation_confirmed=true;assert.equal(evaluateTrial(t).methods.depth.fp,1);
  t.unknown_intervals=[[450,550]];assert.equal(evaluateTrial(t).methods.depth.fp,0);
  assert.equal(evaluateTrial(t).known_duration_ms,900);
});
test('labelled events use one-to-one matching rather than duplicate successes',()=>{
  const t={duration_ms:1000,truth:[{finger:'index',t_ms:500}],annotation_confirmed:true,predictions:{depth:[{finger:'index',t_ms:490,emitted_ms:600},{finger:'index',t_ms:510,emitted_ms:620}]}};
  const x=evaluateTrial(t).methods.depth;assert.equal(x.tp,1);assert.equal(x.fp,1);assert.equal(x.timing_mae_ms,10);
});
test('matched pose, depth and fusion detect a full ideal cycle and replays do not double count',()=>{
  const pose=calibrate(Array.from({length:8},()=>hand()),Array.from({length:8},()=>hand({index:1})),'index');
  const e=new DepthComparison({index:{pose,depth:{base:0,delta:1}}});
  const found={pose_matched:[],depth:[],fusion:[]};
  for(const [i,s] of [0,0,1,1,0,0,0,0].entries()){
    const o=e.update(i*100,hand({index:s}),row(s).depth,null,i*100+50);
    for(const m of Object.keys(found))found[m].push(...o.events[m]);
  }
  for(const events of Object.values(found)){assert.equal(events.length,1);assert.equal(events[0].t_ms,400);}
});
