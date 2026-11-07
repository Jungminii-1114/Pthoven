import test from 'node:test';
import assert from 'node:assert/strict';
import {features,selectReliableFeatures,calibrate,PoseTapDetector,poseFeatureSignal} from '../models/tap-detector.mjs';
import {poseSignal} from '../models/depth-detector.mjs';
import {hand,transform} from './tap-fixtures.mjs';

test('user supplied aggregate statistics select the two stable angles without raw-data claims',()=>{
  const d=[-.2159,.0762,.0262,.0592,-.0445],r=[.1372,.0081,.0107,.0079,.1348],l=[.1043,.0118,.0063,.0076,.1011];
  const s=selectReliableFeatures(d,r,l);
  assert.deepEqual(s.selected,[false,true,false,true,false]);
  assert.ok(s.rest_score_noise_bound<.12);assert.ok(s.lift_score_noise_bound<.15);
  assert.ok(s.normalized_separation>3);assert.ok(Math.abs(s.contributions.reduce((a,b)=>a+b,0)-1)<1e-12);
});

// Rotate joints around wrist→MCP and scale phalanges. Joint angles remain
// constant while predicted z/reach vary. This is analytic, not human footage.
function perturb(lift,yaw,scale){
  const p=hand({index:lift}),o=p[5],axis=p[5].map((x,i)=>x-p[0][i]),n=Math.hypot(...axis),a=axis.map(x=>x/n);
  for(const i of [6,7,8]){
    const v=p[i].map((x,j)=>(x-o[j])*scale),dot=v.reduce((s,x,j)=>s+x*a[j],0);
    const cross=[a[1]*v[2]-a[2]*v[1],a[2]*v[0]-a[0]*v[2],a[0]*v[1]-a[1]*v[0]];
    p[i]=v.map((x,j)=>o[j]+x*Math.cos(yaw)+cross[j]*Math.sin(yaw)+a[j]*dot*(1-Math.cos(yaw)));
  }
  return p;
}
const samples=lift=>Array.from({length:80},(_,i)=>perturb(lift,.5*Math.sin(i*Math.PI/10),1+.2*Math.sin(i*Math.PI/9)));

test('varying z/reach do not dominate stable joint-angle evidence or emit taps at rest',()=>{
  const rest=samples(0),lift=samples(.7),model=calibrate(rest,lift,'index');
  assert.ok(model.feature_weights.some(w=>w===0));assert.equal(model.quality.method,'adaptive_feature_quality_v4');
  assert.ok(model.quality.rest_axis.score_noise<.3);assert.ok(model.quality.lift_axis.score_noise<.3);
  const d=new PoseTapDetector();d.setCalibration('index',model);
  const events=rest.flatMap((p,i)=>d.update(i*25,p).events);assert.equal(events.length,0);
});

test('live H1 and Depth pose paths use the exact same learned weights',()=>{
  const model=calibrate(samples(0),samples(.7),'index'),p=perturb(.4,.35,1.1),d=new PoseTapDetector();
  d.setCalibration('index',model);const h1=d.update(0,p).signals.index,h3=poseSignal(p,model,'index');
  assert.equal(h1.score,h3.score);assert.equal(h1.residual,h3.residual);assert.equal(h1.valid,h3.valid);
  const restored=JSON.parse(JSON.stringify(model));assert.equal(poseSignal(p,restored,'index').score,h1.score);
});

test('weights cannot turn a large bone-length error into an accepted frame',()=>{
  const model=calibrate(samples(0),samples(.7),'index'),p=perturb(.7,0,2);
  assert.equal(poseFeatureSignal(features(p,{finger:'index'}).index,model).valid,false);
});

test('v3 recordings without learned weights retain their old projection score',()=>{
  const rest=Array.from({length:12},()=>hand()),lift=Array.from({length:12},()=>hand({index:1}));
  const model=calibrate(rest,lift,'index',{method:'projection_v3'});assert.equal(model.feature_weights,undefined);
  const f=features(hand({index:.4}),{finger:'index'}).index;
  const expected=f.vector.reduce((s,x,i)=>s+(x-model.base[i])*model.delta[i],0)/model.delta.reduce((s,x)=>s+x*x,0);
  assert.ok(Math.abs(poseFeatureSignal(f,model).score-expected)<1e-12);
});

test('no reliable feature does not force calibration approval',()=>{
  const rest=Array.from({length:40},(_,i)=>hand({index:i%2?1:0}));
  assert.throws(()=>calibrate(rest,Array.from({length:40},()=>hand({index:1})),'index'),e=>e.code==='features_unreliable');
  assert.throws(()=>selectReliableFeatures([0,0,0,0,0],[0,0,NaN,0,0],[0,0,0,0,0]));
});

test('adaptive feature selection is invariant to global hand translation rotation and scale',()=>{
  const r=samples(0),l=samples(.7),t=p=>transform(p,{angle:.5,scale:2,shift:[9,2,4]});
  const a=calibrate(r,l,'index'),b=calibrate(r.map(t),l.map(t),'index');
  assert.deepEqual(a.quality.feature_selection.selected,b.quality.feature_selection.selected);
  assert.ok(Math.abs(a.quality.rest_axis.score_noise-b.quality.rest_axis.score_noise)<1e-10);
});
