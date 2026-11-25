import test from 'node:test';
import assert from 'node:assert/strict';
import {SkinObserver} from '../web/skin-observer.mjs';
const settle=()=>new Promise(r=>setImmediate(r));

test('old camera or trial response is discarded and no overlapping request is queued',async()=>{
  let resolve;const calls=[],results=[];
  const s=new SkinObserver({send:p=>{calls.push(p);return new Promise(r=>resolve=r);},onResult:r=>results.push(r),onStatus:()=>{}});
  s.enable(true);s.offer(()=>'a',100);s.offer(()=>'b',220);await settle();
  s.offer(()=>'c',350);assert.equal(calls.length,1);
  s.reset();resolve({t_ms:220});await settle();assert.equal(results.length,0);
  s.offer(()=>'d',500);s.offer(()=>'e',620);await settle();
  assert.equal(calls[1].previous_t_ms,500);resolve({t_ms:620});await settle();assert.equal(results.length,1);
});

test('long observation gaps and missing patches restart pair collection',async()=>{
  const calls=[];const s=new SkinObserver({send:async p=>{calls.push(p);return {};},onResult:()=>{},onStatus:()=>{}});
  s.enable(true);s.offer(()=>'a',100);s.offer(()=>'b',500);await settle();assert.equal(calls.length,0);
  s.offer(()=>null,620);s.offer(()=>'c',750);s.offer(()=>'d',870);await settle();
  assert.equal(calls.length,1);assert.equal(calls[0].previous_t_ms,750);
  s.enable(false);s.offer(()=>'e',1000);await settle();assert.equal(calls.length,1);
});

test('backend failure is contained and next pair starts fresh',async()=>{
  const statuses=[];let calls=0;
  const s=new SkinObserver({send:async()=>{calls++;throw Error('offline');},onResult:()=>{},onStatus:t=>statuses.push(t)});
  s.enable(true);s.offer(()=>'a',100);s.offer(()=>'b',220);await settle();
  assert.ok(statuses.some(t=>t.includes('offline')));assert.equal(s.pending,false);
  s.offer(()=>'c',400);await settle();assert.equal(calls,1);
});
