import test from 'node:test';
import assert from 'node:assert/strict';
import {calibrate,PoseTapDetector} from '../models/tap-detector.mjs';
import {hand,transform} from './tap-fixtures.mjs';

// Analytic lateral joint perturbation; not a recording of human hand tremor.
function lateral(lift,yaw){
  const p=hand({index:lift}),o=p[5];
  for(const i of [6,7,8]){
    const [x,y,z]=p[i].map((v,j)=>v-o[j]);
    p[i]=[o[0]+x*Math.cos(yaw)-y*Math.sin(yaw),o[1]+x*Math.sin(yaw)+y*Math.cos(yaw),o[2]+z];
  }
  return p;
}
const noisy=(lift,amplitude)=>Array.from({length:60},(_,i)=>lateral(lift,amplitude*Math.sin(i*Math.PI/10)));

test('orthogonal variation is judged separately from actual event score noise',()=>{
  const rest=noisy(.0,.2),lift=noisy(.2,.2),model=calibrate(rest,lift,'index',{method:'projection_v3'});
  assert.ok(model.quality.rest_noise_ratio>.5,'this case failed the old total-noise gate');
  assert.ok(model.quality.rest_axis.score_noise<.15);
  assert.ok(model.quality.rest_axis.orthogonal_noise<.8);
  assert.ok(model.quality.rest_axis.state_ready_ratio>=.7);
  assert.equal(model.quality.method,'score_axis_quality_v3');
  const d=new PoseTapDetector();d.setCalibration('index',model);
  const events=rest.flatMap((p,i)=>d.update(i*50,p).events);
  assert.equal(events.length,0,'off-axis resting variation must not create taps');
});

test('unstable rest and lift are both reported, rather than stopping at rest',()=>{
  const rest=Array.from({length:24},(_,i)=>hand({index:i%2?1:0}));
  const lift=Array.from({length:24},(_,i)=>hand({index:i%2?2:1}));
  assert.throws(()=>calibrate(rest,lift,'index',{method:'projection_v3'}),error=>{
    assert.ok(error.diagnostics.problems.some(p=>p.code==='rest_unstable'));
    assert.ok(error.diagnostics.problems.some(p=>p.code==='lift_unstable'));
    return true;
  });
});

test('large off-axis variation is not approved just because projection noise is small',()=>{
  assert.throws(()=>calibrate(noisy(0,.8),noisy(.2,.8),'index',{method:'projection_v3'}));
});

test('axis diagnostics retain translation rotation and scale invariance',()=>{
  const rest=noisy(0,.2),lift=noisy(.2,.2);
  const a=calibrate(rest,lift,'index',{method:'projection_v3'}),change=p=>transform(p,{angle:.7,scale:3,shift:[8,10,3]});
  const b=calibrate(rest.map(change),lift.map(change),'index',{method:'projection_v3'});
  assert.ok(Math.abs(a.quality.rest_axis.score_noise-b.quality.rest_axis.score_noise)<1e-10);
  assert.ok(Math.abs(a.quality.lift_axis.orthogonal_noise-b.quality.lift_axis.orthogonal_noise)<1e-10);
});
