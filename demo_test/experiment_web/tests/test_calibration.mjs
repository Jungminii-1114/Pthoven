import test from 'node:test';
import assert from 'node:assert/strict';
import {hand,transform} from './tap-fixtures.mjs';
import {calibrate,summarizeCalibration,features,PoseTapDetector} from '../models/tap-detector.mjs';
import {CalibrationCapture} from '../web/calibration-capture.mjs';
const frames=(n,lift=0)=>Array.from({length:n},()=>hand({index:lift}));

test('preparation movement is excluded from recorded calibration frames',()=>{
  const c=new CalibrationCapture({kind:'lift',finger:'index',start:0});
  for(let t=0;t<1500;t+=50)c.observe(t,hand({index:t/1500}));
  assert.equal(c.frames.length,0);assert.equal(c.status(1000).phase,'preparing');
  for(let t=1500;t<=3100;t+=50)c.observe(t,hand({index:1}));
  assert.equal(c.status(3100).phase,'complete');
  assert.deepEqual(c.frames[0],hand({index:1}));
});
test('slow camera can collect beyond one second without fabricated frames',()=>{
  const c=new CalibrationCapture({kind:'lift',finger:'index',start:0});
  for(let t=1500;t<=3500;t+=250)c.observe(t,hand({index:1}));
  assert.equal(c.status(3500).phase,'collecting');
  for(let t=3750;t<=4250;t+=250)c.observe(t,hand({index:1}));
  assert.equal(c.status(4250).phase,'complete');assert.equal(c.frames.length,12);
});
test('tracking loss restarts the window; frozen camera cannot complete',()=>{
  const c=new CalibrationCapture({kind:'rest',finger:'index',start:0});
  for(let t=1500;t<2200;t+=50)c.observe(t,hand());
  assert.equal(c.status(4000).phase,'collecting');
  c.observe(4000,hand());assert.equal(c.frames.length,1);assert.equal(c.restarts,1);
  assert.equal(c.status(8000).phase,'timeout');
});
test('missing hands and degenerate frames time out with diagnostic counts',()=>{
  const c=new CalibrationCapture({kind:'rest',finger:'index',start:0});
  c.observe(1500,null);c.observe(1550,[]);
  assert.equal(c.status(8000).phase,'timeout');assert.equal(c.rejected,2);assert.equal(c.frames.length,0);
});
test('isolated tracking spikes are trimmed while separation thresholds remain enforced',()=>{
  const clean=frames(24),withSpike=[...clean,hand({index:2}),null];
  const model=calibrate(withSpike,frames(24,1),'index');
  assert.equal(model.quality.rest.used_frames,24);assert.equal(model.quality.rest.outlier_frames,1);
  assert.equal(model.quality.rest.rejected_frames,1);assert.equal(model.quality.min_signal,.06);
  assert.throws(()=>calibrate(clean,clean,'index'),e=>e.code==='signal_too_small'&&e.diagnostics.signal===0);
});
test('widespread tracking errors cannot be hidden by choosing a tiny clean subset',()=>{
  assert.throws(()=>summarizeCalibration([...frames(6),...Array(8).fill(null)],'index'),e=>e.code==='rest_quality_low');
});
test('mixed rest and moving finger cannot become a stable baseline',()=>{
  const mix=Array.from({length:24},(_,i)=>hand({index:i%2?1:0}));
  assert.throws(()=>calibrate(mix,frames(24,1),'index'),e=>e.diagnostics.problems.some(p=>p.code==='rest_unstable'));
  assert.throws(()=>calibrate(frames(24),mix,'index'),e=>e.diagnostics.problems.some(p=>p.code==='lift_unstable'));
});
test('other finger joint degeneracy does not invalidate selected finger',()=>{
  const p=hand();p[20]=[...p[19]];
  assert.throws(()=>features(p));assert.ok(features(p,{finger:'index'}).index);
  const model=calibrate(Array(12).fill(p),frames(12,1),'index');
  const detector=new PoseTapDetector();detector.setCalibration('index',model);
  assert.equal(detector.update(0,p).signals.index.valid,true);
});
test('entire palm lift remains indistinguishable after local normalization',()=>{
  assert.throws(()=>calibrate(frames(24),frames(24).map(p=>transform(p,{shift:[0,0,3]})),'index'),e=>e.code==='signal_too_small');
});
