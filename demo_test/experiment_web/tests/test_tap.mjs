import test from 'node:test';
import assert from 'node:assert/strict';
import {angle,features,calibrate,TapMachine,PoseTapDetector,evaluateEvents,FINGERS} from '../models/tap-detector.mjs';
import {hand,transform,cycle} from './tap-fixtures.mjs';
const rest=Array.from({length:8},()=>hand());
function detector(){const d=new PoseTapDetector();for(const f of Object.keys(FINGERS))d.setCalibration(f,calibrate(rest,Array.from({length:8},()=>hand({[f]:1})),f));return d;}
function feed(machine,scores,start=0){return scores.flatMap((s,i)=>{const e=machine.update(start+i*25,s);return e?[e]:[];});}

test('joint angle recovers known 90-degree geometry',()=>assert.ok(Math.abs(angle([1,0,0],[0,0,0],[0,1,0])-Math.PI/2)<1e-10));
test('palm-local features invariant to translation, rotation and scale',()=>{
  const a=features(hand({index:.7}));const b=features(transform(hand({index:.7}),{angle:1.2,scale:2,shift:[10,-20,3]}));
  for(const f of Object.keys(FINGERS))a[f].vector.forEach((v,i)=>assert.ok(Math.abs(v-b[f].vector[i])<1e-10));
});
test('no real height: global palm lift is removed by local coordinates',()=>{
  const a=features(hand());const b=features(transform(hand(),{shift:[0,0,5]}));
  assert.ok(a.index.vector.every((v,i)=>Math.abs(v-b.index.vector[i])<1e-10));
});
test('invalid and degenerate landmarks rejected',()=>{
  assert.throws(()=>features([]));assert.throws(()=>features(Array.from({length:21},()=>[0,0,0])));
  const p=hand();p[8][2]=NaN;assert.throws(()=>features(p));
});
test('calibration rejects indistinguishable rest/lift and unstable rest',()=>{
  assert.throws(()=>calibrate(rest,rest,'index'));
  assert.throws(()=>calibrate([hand(),hand({index:1}),hand(),hand({index:1}),hand()],Array.from({length:8},()=>hand({index:1})),'index'));
});
test('rest and low jitter do not trigger events',()=>{
  assert.equal(feed(new TapMachine(),Array.from({length:120},(_,i)=>.05*Math.sin(i))).length,0);
});
test('one full cycle triggers once and hold does not repeat',()=>{
  assert.equal(feed(new TapMachine(),[0,0,.7,1,1,.4,.1,0,0,0,0,0,0]).length,1);
});
test('repeat cycles on same finger rearm without lateral exit',()=>{
  assert.equal(feed(new TapMachine(),[0,0,1,1,.1,0,0,0,0,0,1,1,.1,0,0]).length,2);
});
test('short high spike is rejected',()=>assert.equal(feed(new TapMachine(),[0,0,1,0,0,0]).length,0));
test('no confirmed lift means no event',()=>assert.equal(feed(new TapMachine(),[0,0,.4,.3,0,0]).length,0));
test('tracking loss requires a new rest and full lift cycle',()=>{
  const m=new TapMachine();feed(m,[0,0,1,1]);m.update(100,null,false);
  assert.equal(feed(m,[1,1,0,0,0],125).length,0);
  assert.equal(feed(m,[0,1,1,0,0,0],300).length,1);
});
test('large frame gap invalidates cycle',()=>{
  const m=new TapMachine();feed(m,[0,0,1,1]);assert.equal(m.update(500,0),null);assert.equal(m.update(550,0),null);
});
test('timestamps cannot move backwards or repeat',()=>{const m=new TapMachine();m.update(1,0);assert.throws(()=>m.update(1,0));});
test('five fingers are independently identifiable on ideal skeletons',()=>{
  for(const f of Object.keys(FINGERS)) {
    const d=detector(),events=cycle(f).flatMap(r=>d.update(r.t_ms,r.landmarks).events);
    assert.equal(events.length,1,f);assert.equal(events[0].finger,f);
  }
});
test('two simultaneous fingers can generate separate candidates',()=>{
  const d=detector();const events=cycle().flatMap((r,i)=>d.update(r.t_ms,hand({index:[0,0,.2,.6,1,1,.7,.3,0,0,0][i],middle:[0,0,.2,.6,1,1,.7,.3,0,0,0][i]})).events);
  assert.deepEqual(events.map(e=>e.finger).sort(),['index','middle']);
});
test('bone-length outlier suppresses unstable pose',()=>{
  const d=detector();d.update(0,hand());d.update(25,hand());
  const p=hand({index:1});p[8]=[50,50,50];const r=d.update(50,p);
  assert.equal(r.signals.index.valid,false);assert.equal(r.events.length,0);
});
test('air tap with identical observed pose is indistinguishable: known limit',()=>{
  const physical=detector(),air=detector();const a=[],b=[];
  for(const row of cycle()){a.push(...physical.update(row.t_ms,row.landmarks).events);b.push(...air.update(row.t_ms,row.landmarks).events);}
  assert.deepEqual(a,b);assert.equal(b.length,1);
  assert.equal(evaluateEvents(b,[],{durationMs:1000}).fp,1);
});
test('event matching is per-finger, one-to-one and missing truth is not perfect accuracy',()=>{
  assert.equal(evaluateEvents([],null).f1,null);
  const r=evaluateEvents([{finger:'index',t_ms:100},{finger:'index',t_ms:110},{finger:'middle',t_ms:200}],
    [{finger:'index',t_ms:105},{finger:'ring',t_ms:200}],{toleranceMs:30,durationMs:1000});
  assert.equal(r.tp,1);assert.equal(r.fp,2);assert.equal(r.fn,1);
  assert.equal(evaluateEvents([],[],{}).f1,null);
});
