import test from 'node:test';
import assert from 'node:assert/strict';
import {LiveTapTrace,formatSignals,formatThresholds,selectHandInput,formatTraceHealth} from '../web/tap-live-diagnostics.mjs';
import {PythonTapClient} from '../web/python-tap-client.mjs';

test('recent trace is bounded by timestamp and count, and preserves raw Python responses',()=>{
  const t=new LiveTapTrace({windowMs:100,maxFrames:3});
  for(let i=0;i<6;i++)t.add({t_ms:i*40,world_landmarks:[i]}, {t_ms:i*40,events:[],signals:{}});
  assert.deepEqual(t.rows.map(r=>r.frame.t_ms),[120,160,200]);
  const models={index:{algorithm:'test'}};const data=t.export(models);
  assert.equal(data.trials[0].truth,null);assert.equal(data.trials[0].frames[0].t_ms,120);
  assert.deepEqual(data.trials[0].observations[0],t.rows[0].observed);
  models.index.algorithm='changed';assert.equal(data.trials[0].calibration.index.algorithm,'test');
  t.clear();assert.throws(()=>t.export(models),/기록/);
});

test('transport sends exact source frame alongside its matching Python decision',async()=>{
  const rows=[];const client=new PythonTapClient({send:async(path,p)=>path==='session'?{session_id:'s'}:path==='close'?{}:{frames:p.frames.map(f=>({t_ms:f.t_ms,events:[],signals:{}}))},onFrame:(o,c,meta)=>rows.push({o,c,meta})});
  await client.reset({index:{}});const frame={t_ms:100,world_landmarks:[[1,2,3]]};client.offer(frame,'ctx');await client.drain();
  assert.equal(rows.length,1);assert.equal(rows[0].meta.input_frame,frame);assert.equal(rows[0].c,'ctx');await client.reset();
});

test('display retains Python thresholds and last quality interruption without deciding',()=>{
  const text=formatSignals({index:{phase:'REST',valid:true,tip:{score:.8},angles:{score:.3},geometry:{valid:true,shape_error:.1},decision:{message:'들림 대기',thresholds:{lift:.55,release:.2},last_interruption:{code:'local_bone_distortion'},interruption_counts:{local_bone_distortion:2}}}},{index:'검지'});
  assert.match(text,/들림 TIP ≥0.55/);assert.match(text,/관절 0.30/);assert.match(text,/국소 길이 왜곡 2회/);
});

test('experimental mode must be acknowledged by Python, never silently ignored',async()=>{
  const client=new PythonTapClient({send:async path=>path==='session'?{session_id:'old-server'}:{ok:true}});
  await assert.rejects(()=>client.reset({index:{}},{mode:'local_baseline'}),/서버를 재시작/);
  assert.equal(client.session,null);
});

test('local mode stays labelled local when current coordinates are missing',()=>{
  const text=formatSignals({index:{phase:'REST',valid:false,decision:{reference:{mode:'local_baseline',state:'recovering',recoveries:2,updates:17,held_quality_frames:89}}}},{index:'검지'});
  assert.match(text,/상대 기준/);assert.match(text,/복구 2회/);assert.doesNotMatch(text,/고정 기준/);
});

test('window candidate count expires with old frames and matches exported interval',()=>{
  const t=new LiveTapTrace({windowMs:100});
  t.add({t_ms:0},{t_ms:0,events:[{finger:'index'}],signals:{}});
  t.add({t_ms:40},{t_ms:40,events:[],signals:{}});
  assert.equal(t.candidateCount,1);
  t.add({t_ms:120},{t_ms:120,events:[],signals:{}});
  assert.equal(t.candidateCount,0);
  assert.equal(t.export({index:{}}).trials[0].window_candidate_count,0);
});

test('graph threshold label uses short rounded numbers',()=>{
  assert.equal(formatThresholds({lift:.55,angle_lift:.323143536269,release:.2}),'들림 TIP 0.55 / 관절 0.32 · 복귀 0.20');
});

const hand=()=>Array.from({length:21},()=>({x:.5,y:.5,z:0}));
const row=(t,present,valid=true)=>[{t_ms:t,world_landmarks:present?hand():null,landmarks:present?hand():null,
  tracking:{reason:present?'ok':'no_hand_detected'}},{t_ms:t,signals:{index:{valid}},events:[]}];

test('hand observation window survives a later empty window without hiding failed frames',()=>{
  const trace=new LiveTapTrace({windowMs:100,maxFrames:10,lossTailMs:20});
  for(let t=0;t<=80;t+=20)trace.add(...row(t,true,t!==60));
  for(let t=100;t<=400;t+=20)trace.add(...row(t,false));
  const payload=trace.export({index:{}}),[current,saved]=payload.trials;
  assert.equal(current.capture_window,'current');assert.equal(current.input_summary.landmark_frames,0);
  assert.equal(saved.capture_window,'last_hand_observed');
  assert.ok(saved.input_summary.landmark_frames>0);assert.ok(saved.input_summary.missing_landmark_frames>0);
  assert.equal(saved.frames.at(-1).t_ms,100);
  assert.equal(saved.observations.find(r=>r.t_ms===60).signals.index.valid,false);
  assert.equal(saved.frames.find(r=>r.t_ms===60).world_landmarks.length,21);
  assert.match(formatTraceHealth(trace.summary),/별도 보존/);
});

test('a large camera gap preserves the previous observed window before trimming current rows',()=>{
  const trace=new LiveTapTrace({windowMs:100,lossTailMs:20});
  trace.add(...row(0,true));trace.add(...row(40,true));trace.add(...row(1000,false));
  const payload=trace.export({index:{}});
  assert.equal(payload.trials[0].input_summary.landmark_frames,0);
  assert.equal(payload.trials[1].frames.at(-1).t_ms,40);
  trace.add(...row(1020,true));assert.equal(trace.summary.retained,null);
  trace.clear();assert.equal(trace.summary.last_observed_ms,null);assert.equal(trace.summary.retained,null);
});

test('an entirely missing session is labelled missing and does not fabricate a hand window',()=>{
  const trace=new LiveTapTrace();trace.add(...row(0,false));trace.add(...row(20,false));
  const payload=trace.export({index:{}});
  assert.equal(payload.trials.length,1);assert.equal(payload.trials[0].input_summary.landmark_frames,0);
  assert.equal(payload.last_hand_observed_ms,null);assert.match(formatTraceHealth(trace.summary),/0\/2/);
});

test('hand selection metadata distinguishes missing hands, opposite side, and tracker error',()=>{
  const empty={handedness:[],landmarks:[],worldLandmarks:[]};
  assert.equal(selectHandInput(empty,'Right').tracking.reason,'no_hand_detected');
  const left={handedness:[[{categoryName:'Left',score:.9}]],landmarks:[hand()],worldLandmarks:[hand()]};
  const wrong=selectHandInput(left,'Right');assert.equal(wrong.world,null);
  assert.equal(wrong.tracking.reason,'selected_hand_not_found');assert.equal(wrong.tracking.detected_hands[0].side,'Left');
  const selected=selectHandInput(left,'Left');assert.equal(selected.tracking.reason,'ok');assert.equal(selected.world,left.worldLandmarks[0]);
  assert.equal(selectHandInput(empty,'Right',{ready:false,error:Error('failed')}).tracking.reason,'tracker_error');
  assert.equal(selectHandInput(empty,'Right',{ready:false}).tracking.reason,'tracker_loading');
});
