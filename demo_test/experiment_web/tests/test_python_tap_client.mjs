import test from 'node:test';
import assert from 'node:assert/strict';
import {PythonTapClient} from '../web/python-tap-client.mjs';
const settle=()=>new Promise(r=>setImmediate(r));

test('Python transport uses one ordered request and discards old-session replies',async()=>{
  let answer;const seen=[],calls=[];
  const client=new PythonTapClient({send:async(path,payload)=>{
    calls.push({path,payload});if(path==='session')return {session_id:'s'+calls.length};if(path==='close')return {ok:true};
    return new Promise(r=>answer=()=>r({frames:payload.frames.map(f=>({t_ms:f.t_ms,events:[],signals:{}}))}));
  },onFrame:r=>seen.push(r)});
  await client.reset({index:{}});
  for(let t=1;t<=3;t++)client.offer({t_ms:t},'old');await settle();
  client.offer({t_ms:4});assert.equal(calls.filter(c=>c.path==='frames').length,1);
  await client.reset({index:{}});answer();await settle();assert.equal(seen.length,0);
  for(let t=5;t<=7;t++)client.offer({t_ms:t});await settle();answer();await settle();
  assert.deepEqual(seen.map(r=>r.t_ms),[5,6,7]);await client.reset();
});

test('backend errors stop Python session without a browser decision fallback',async()=>{
  const errors=[];const client=new PythonTapClient({send:async path=>{if(path==='session')return {session_id:'s'};throw Error('backend unavailable');},onError:e=>errors.push(e.message)});
  await client.reset({index:{}});client.offer({t_ms:1});await client.flush();
  assert.equal(client.session,null);assert.deepEqual(errors,['backend unavailable']);await client.reset();
});

test('queue is bounded and dropped frames are visible while server is pending',async()=>{
  let answer;const client=new PythonTapClient({send:async(path,payload)=>path==='session'?{session_id:'s'}:path==='close'?{}:new Promise(r=>answer=()=>r({frames:payload.frames.map(f=>({t_ms:f.t_ms,signals:{},events:[]}))}))});
  await client.reset({index:{}});for(let t=1;t<=100;t++)client.offer({t_ms:t});
  assert.equal(client.queue.length,64);assert.equal(client.dropped,33);
  await client.reset();answer();await settle();
});
