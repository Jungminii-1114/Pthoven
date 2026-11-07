import test from 'node:test';
import assert from 'node:assert/strict';
import {CameraSession,isIPhone,cameraError} from '../web/camera-session.mjs';
const delay=ms=>new Promise(r=>setTimeout(r,ms));
function fakeStream(label='MacBook Camera',id='mac'){
  const listeners={};
  const track={label,readyState:'live',stopped:0,getSettings:()=>({deviceId:id}),stop(){this.stopped++;this.readyState='ended';},addEventListener:(type,cb)=>listeners[type]=cb};
  return {getTracks:()=>[track],getVideoTracks:()=>[track],track,listeners};
}
test('default camera requests video only at soft ideal resolution',async()=>{
  const stream=fakeStream();let constraints;
  const session=new CameraSession({getUserMedia:async c=>(constraints=c,stream)});
  assert.equal(await session.connect(),stream);assert.equal(session.phase,'streaming');
  assert.equal(constraints.audio,false);assert.equal(constraints.video.width.ideal,1920);assert.equal(constraints.video.deviceId,undefined);
  session.stop();assert.equal(stream.track.stopped,1);
});
test('explicit iPhone selection uses exact device and stops previous camera',async()=>{
  const mac=fakeStream(),phone=fakeStream('iPhone Camera','phone');let n=0,requested;
  const session=new CameraSession({getUserMedia:async c=>{requested=c;return n++?phone:mac;}});
  await session.connect();await session.connect('phone');
  assert.equal(requested.video.deviceId.exact,'phone');assert.equal(mac.track.stopped,1);assert.equal(session.stream,phone);session.stop();
});
test('selected device failure is reported, never silently replaced with MacBook',async()=>{
  let count=0;const session=new CameraSession({getUserMedia:async()=>{count++;throw Object.assign(Error(),{name:'OverconstrainedError'});}});
  await assert.rejects(session.connect('missing'),{name:'OverconstrainedError'});assert.equal(count,1);assert.equal(session.phase,'error');
});
test('stop during pending permission disposes late stream without reopening',async()=>{
  let resolve;const stream=fakeStream();const session=new CameraSession({getUserMedia:()=>new Promise(r=>resolve=r)});
  const pending=session.connect();session.stop();resolve(stream);assert.equal(await pending,null);assert.equal(session.stream,null);assert.equal(stream.track.stopped,1);
});
test('timeout rejects and releases stream that arrives later',async()=>{
  let resolve;const stream=fakeStream();const session=new CameraSession({getUserMedia:()=>new Promise(r=>resolve=r)},()=>{},10);
  await assert.rejects(session.connect(),{name:'TimeoutError'});resolve(stream);await delay(0);
  assert.equal(stream.track.stopped,1);assert.equal(session.stream,null);assert.equal(session.phase,'error');
});
test('device refresh failure does not close working stream',async()=>{
  const stream=fakeStream();const session=new CameraSession({getUserMedia:async()=>stream,enumerateDevices:async()=>{throw Error('enumeration failed');}});
  await session.connect();await assert.rejects(session.refresh());assert.equal(stream.track.stopped,0);assert.equal(session.stream,stream);session.stop();
});
test('track ended transitions to a recoverable disconnection state',async()=>{
  const stream=fakeStream();const session=new CameraSession({getUserMedia:async()=>stream});await session.connect();
  stream.listeners.ended();assert.equal(session.phase,'ended');assert.equal(session.stream,null);assert.ok(session.lastError.message.includes('다시 연결'));
});
test('labels and actionable permission errors are exposed',()=>{
  assert.equal(isIPhone({label:'정민의 iPhone 카메라'}),true);assert.equal(isIPhone({label:'FaceTime HD Camera'}),false);
  assert.match(cameraError({name:'NotAllowedError'}),/권한/);assert.match(cameraError({name:'NotReadableError'}),/다른 앱/);
});
