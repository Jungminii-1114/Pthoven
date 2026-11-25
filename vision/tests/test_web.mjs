/**
 * Retained version of the 20 B01 browser-adapter mock scenarios.
 *
 * Run from the vision project root: node --test tests/test_web.mjs
 * Uses Node built-ins only: no browser, camera, SDK, server or temporary files.
 * Assets resolve relative to this test, so the entire vision folder is portable.
 *
 * These scenarios intentionally retain the original sequential fixture: each
 * generator step runs its assertions inside a named node:test subtest. Later
 * steps exercise results produced by earlier queue/capture/session operations.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

async function* scenarioSteps() {
  const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
  const html = fs.readFileSync(new URL("../web/index.html", import.meta.url), "utf8");
  let now = 100, frameCallback;
  class El {
    constructor(tag="div"){Object.assign(this,{tagName:tag,children:[],dataset:{},listeners:{},style:{setProperty(k,v){this[k]=v}},value:"",textContent:"",disabled:false,checked:false,open:false,className:"",clientWidth:800,clientHeight:450})}
    append(...xs){this.children.push(...xs)} add(x){this.append(x)} replaceChildren(...xs){this.children=xs} addEventListener(k,fn){this.listeners[k]=fn} setAttribute(k,v){this[k]=v}
    querySelector(s){let el=this.children.find(x=>s[0]==="."?x.className===s.slice(1):x.tagName===s);if(!el&&s==="span"){el=new El("span");this.append(el)}return el}
    getContext(){return{clearRect(){},setTransform(){},drawImage(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},arc(){},fill(){}}} toDataURL(){return"data:image/jpeg;base64,aW1hZ2U="} click(){} remove(){}
  }
  const els={}, inputs=[];
  for(const m of html.matchAll(/<([a-z-]+)[^>]*\bid="([^"]+)"[^>]*>/g)){const e=els[m[2]]=new El(m[1]);e.value=m[0].match(/\bvalue="([^"]*)"/)?.[1]||""}
  for(const m of html.matchAll(/<input[^>]*name="(selected-hand|active-finger)"[^>]*>/g)){const e=new El("input");Object.assign(e,{name:m[1],value:m[0].match(/value="([^"]+)"/)[1],checked:m[0].includes(" checked")});inputs.push(e)}
  const video=els["camera-video"];
  Object.assign(video,{readyState:2,videoWidth:1280,videoHeight:720,currentTime:0,play:async()=>{},pause(){},requestVideoFrameCallback(fn){frameCallback=fn;return 1},cancelVideoFrameCallback(){frameCallback=null}});
  let handler=async path=>({runtime:"python",model:{state:"not_loaded"}});
  const requests=[], stream={getTracks:()=>[{stop(){}}],getVideoTracks:()=>[]};
  const c={document:{getElementById:id=>els[id],createElement:tag=>new El(tag),querySelector:()=>inputs.find(e=>e.name==="selected-hand"&&e.checked),querySelectorAll:s=>inputs.filter(e=>s.includes("selected-hand")?true:e.name==="active-finger"&&e.checked),body:new El("body")},navigator:{mediaDevices:{enumerateDevices:async()=>[{kind:"videoinput",deviceId:"chosen",label:""},{kind:"videoinput",deviceId:"iphone",label:"iPhone"}],addEventListener(){}}},performance:{now:()=>now},crypto:{randomUUID:()=>"test-clock"},Option:class extends El{constructor(label,value){super("option");this.value=value;this.textContent=label}},ResizeObserver:class{observe(){}},window:{devicePixelRatio:2,addEventListener(){}},AbortController,Blob,URL,setInterval(){},setTimeout:(fn,ms)=>ms===15000?setTimeout(fn,ms).unref():{fn,ms},clearTimeout:x=>{if(!x?.fn)clearTimeout(x)},requestAnimationFrame:fn=>{frameCallback=fn;return 1},cancelAnimationFrame(){},console};
  c.fetch=async(path,opt)=>{const body=opt.body?JSON.parse(opt.body):undefined;requests.push({path,method:opt.method,body});const data=await handler(path,opt,body);return{ok:true,status:200,json:async()=>JSON.parse(JSON.stringify(data))}};
  let src=fs.readFileSync(new URL("../web/app.js", import.meta.url), "utf8");
  src=src.replace(/\}\)\(\);\s*$/, "globalThis.test={state,geometry,deviceSignature,startFrameLoop,enqueueFrame,startCapture,updateCapture,checkReady,syncDeviceAndGeometry,connectCamera,modelStatus,prepareModel,pollSession,changeSelection,stopCamera,handleInput,startInput,stopInput,renderInput,checkReady};})();");
  vm.runInNewContext(src,c);
  const t=c.test,s=t.state;
  const settle=async()=>{for(let i=0;i<12;i++)await new Promise(setImmediate)};
  const result=(data,outcome="result_available",diagnostics={})=>({status:"success",data,outcome,diagnostics});
  const snap=(extra={})=>({session_id:"s1",command_epoch:2,geometry_version:1,calibration_version:0,lifecycle_phase:"previewing",device_state:{input:"blocked",calibration:"missing"},calibrated_fingers:[],baseline_ref:null,...extra});
  const configure=()=>{Object.assign(s,{generation:s.generation+1,session:snap(),stream,camera:"receiving",tracker:"ready",deviceId:"chosen",deviceLabel:"chosen label",geometryPending:false,syncBlocked:false,geometrySignature:JSON.stringify(t.geometry()),deviceSignature:t.deviceSignature(),capture:null,captureToken:s.captureToken+1,controlBusy:false,connectionBusy:false,finishing:false,latest:null,inFlight:null,frameSequence:0,skipped:0,lastMediaTime:-1,lastPresented:-1,received:0,processed:0});s.deviceSignature=t.deviceSignature()};
  
  await settle();
  assert.equal(s.flipX,false);assert.equal(s.flipY,false);assert.deepEqual(Array.from(s.activeFingers),["index"]);assert.equal(els["camera-select"].value,"");
  // Global model preparation must work before camera permission or preview.
  s.camera = "disconnected";
  s.stream = null;
  assert.equal(els["prepare-model"].disabled, false);
  let preparationRequests = 0;
  handler = async (path, options, body) => {
    if (path.endsWith("/model/prepare")) {
      preparationRequests++;
      assert.deepEqual(body, {});
      return result({state:"loading", model_ref:"mock-model", error:null}, "model_preparation_requested");
    }
    if (path.endsWith("/health")) return {runtime:"python",model:{state:"ready",model_ref:"mock-model",error:null}};
    throw Error(path);
  };
  await t.prepareModel();
  assert.equal(preparationRequests, 1);
  assert.equal(s.tracker, "ready");
  assert.equal(s.stream, null);
  assert.equal(els["capture-rest"].disabled, true);

  yield "defaults and blank camera labels";
  configure(); const pending=[];let active=0,maxActive=0;
  handler=async(path,opt,body)=>{if(path.endsWith("/frames")){active++;maxActive=Math.max(maxActive,active);return new Promise(resolve=>pending.push(()=>{active--;resolve(result({session:snap(),observation:{hands:[]},quality:{},capture:null}))}))}throw Error(path)};
  t.startFrameLoop(s.generation);now=1000;frameCallback(now,{mediaTime:1,presentedFrames:1});now=1010;frameCallback(now,{mediaTime:1,presentedFrames:1});now=1020;frameCallback(now,{mediaTime:2,presentedFrames:2});now=1030;frameCallback(now,{mediaTime:3,presentedFrames:3});
  assert.equal(s.received,3);assert.equal(s.skipped,1);assert.equal(active,1);assert.equal(s.latest.packet.t_ms,1030);yield "new-frame gate and bounded latest queue";
  pending.shift()();await settle();assert.equal(maxActive,1);assert.equal(active,1);const sent=requests.filter(r=>r.path.endsWith("/frames"));assert.deepEqual(sent.map(r=>r.body.frame_id),[1,3]);assert.equal(sent[1].body.t_ms,1030);assert.deepEqual(Object.keys(sent[1].body).sort(),["calibration_version","clock_id","command_epoch","frame_id","geometry_version","image","t_ms"]);pending.shift()();await settle();yield "one HTTP flight and strict frame fields";
  now=1100;t.enqueueFrame(now);now=1110;t.enqueueFrame(now);s.session=snap({command_epoch:3});s.captureToken++;pending.shift()();await settle();assert.equal(s.session.command_epoch,3);assert.equal(s.inFlight,null);assert.equal(s.latest,null);yield "stale frame and pending epoch rejection";
  configure();let finishes=0,readies=0,captureNumber=0,mode="failed";
  handler=async(path,opt,body)=>{
    if(path.endsWith("/calibration/captures"))return result({capture_id:"c"+(++captureNumber),stage:"preparing",session:structuredClone(s.session)},"capture_started");
    if(path.endsWith("/finish")){finishes++;return mode==="failed"?result({session:structuredClone(s.session),capture:{capture_id:s.capture.capture_id,stage:"failed"}},"INSUFFICIENT_SEPARATION",{fit:{reason:"두 자세 분리 부족"}}):result({session:snap({command_epoch:s.session.command_epoch+1,calibration_version:1,baseline_ref:"baseline",device_state:{input:"acquiring_rest",calibration:"missing"}}),capture:{capture_id:s.capture.capture_id,stage:"passed"},calibration:{baseline_ref:"baseline"}},"rest_saved")}
    if(path.endsWith("/frames"))return result({session:structuredClone(s.session),observation:{hands:[]},quality:{},capture:null});
    if(path.endsWith("/ready")){readies++;return result({session:snap({command_epoch:s.session.command_epoch,calibration_version:1,baseline_ref:"baseline",calibrated_fingers:["index"],lifecycle_phase:"ready",device_state:{input:"ready",calibration:"valid"}}),calibration:{}},"ready_for_observation")}
    throw Error(path);
  };
  await t.startCapture("rest","index");assert.equal(s.capture.capture_id,"c1");assert.equal(els["hand-fieldset"].disabled,true);assert.equal(els["flip-x"].disabled,true);yield "capture start and input lock";
  t.updateCapture({capture_id:"c1",stage:"collecting",elapsed_ms:4500,accepted_count:20,input_count:40,ready_to_finish:false},s.captureToken);await settle();assert.equal(finishes,0);yield "no early finish on insufficient frames";
  t.updateCapture({capture_id:"c1",stage:"collecting",elapsed_ms:6000,accepted_count:20,input_count:55,ready_to_finish:true},s.captureToken);await settle();
  assert.equal(finishes,1);assert.equal(s.capture.stage,"failed");assert.match(els.notice.textContent,/두 자세 분리 부족/);assert.equal(els["hand-fieldset"].disabled,false);yield "business failure remains visible and retryable";
  mode="passed";await t.startCapture("rest","index");t.updateCapture({capture_id:"c2",stage:"collecting",elapsed_ms:4000,accepted_count:50,input_count:55,ready_to_finish:true},s.captureToken);await settle();assert.equal(s.capture.stage,"passed");assert.equal(s.session.command_epoch,3);assert.equal(s.session.calibration_version,1);assert.equal(readies,0);yield "finish advances epoch and never automatically readies";
  now=8000;t.enqueueFrame(now);await settle();const after=requests.filter(r=>r.path.endsWith("/frames")).at(-1).body;assert.equal(after.command_epoch,3);assert.equal(after.calibration_version,1);assert.equal(Object.hasOwn(after,"capture_id"),false);yield "post-calibration frames use returned epoch";
  await t.startCapture("lift","index");t.updateCapture({capture_id:"c3",stage:"failed",reason_counts:{TRACKING_GAP:1}},s.captureToken);await settle();assert.equal(s.capture.stage,"failed");assert.equal(els["hand-fieldset"].disabled,false);assert.equal(finishes,2);yield "failed frame progress unlocks controls";
  s.session.calibrated_fingers=["index"];await t.checkReady();assert.equal(readies,1);assert.equal(s.readyConfirmed,true);yield "explicit ready action";
  t.modelStatus({state:"ready"},50);t.modelStatus({state:"loading"},49);assert.equal(s.tracker,"ready");yield "stale model response ordering";
  configure();els["preview-stage"].clientWidth=800;els["preview-stage"].clientHeight=700;s.deviceSignature="";s.geometrySignature="";s.geometryPending=true;
  handler=async(path,opt,body)=>{if(path.endsWith("/device")){assert.equal(Object.hasOwn(body,"tracker"),false);return result(structuredClone(s.session))}if(path.endsWith("/geometry")){const g=body.geometry,top=125;assert.ok(g.keyboard_rect.top>=top);assert.ok(g.keyboard_rect.top+g.keyboard_rect.height<=700-top);return result(snap({command_epoch:3,geometry_version:2}))}throw Error(path)};
  await t.syncDeviceAndGeometry();assert.equal(s.geometryPending,false);assert.equal(s.session.command_epoch,3);yield "device runtime truth and contain keyboard bounds";
  configure();const calls=[];els["camera-select"].value="iphone";c.navigator.mediaDevices.getUserMedia=async constraints=>{calls.push(constraints);throw Object.assign(new Error("selected camera unavailable"),{name:"NotReadableError"})};
  handler=async path=>path.endsWith("/close")?result(snap({lifecycle_phase:"closed"})):result(snap());
  await t.connectCamera();assert.equal(calls.length,1);assert.equal(calls[0].video.deviceId.exact,"iphone");assert.equal(s.stream,null);assert.equal(s.session,null);yield "exact chosen camera failure without fallback";
  
  // A read that started before a mutation cannot restore the old same-epoch snapshot.
  configure();
  let resolveRead;
  handler = async (path, opt, body) => {
    if (opt.method === "GET") return new Promise(resolve => { resolveRead = resolve; });
    if (path.endsWith("/calibration/captures")) return result({capture_id:"read-guard",stage:"preparing",session:snap({lifecycle_phase:"calibrating",device_state:{input:"blocked",calibration:"collecting"}})});
    throw Error(path);
  };
  const read = t.pollSession();
  await settle();
  await t.startCapture("rest","index");
  resolveRead(result(snap()));
  await read;
  assert.equal(s.session.lifecycle_phase,"calibrating");
  assert.equal(s.capture.capture_id,"read-guard");
  yield "same-epoch poll cannot overwrite a newer control result";

  configure();
  const queued = [];
  handler = async (path,opt,body) => new Promise(resolve=>queued.push({body,resolve}));
  s.capture={capture_id:"old-capture",stage:"collecting"}; now=9000;t.enqueueFrame(now);now=9010;t.enqueueFrame(now);
  s.session=snap({command_epoch:3});s.capture={capture_id:"new-capture",stage:"collecting"};s.captureToken++;
  now=9020;t.enqueueFrame(now);
  queued[0].resolve(result({session:snap(),observation:{hands:[]},quality:{},capture:null}));await settle();
  assert.equal(queued.length,2);assert.equal(queued[1].body.capture_id,"new-capture");assert.equal(queued[1].body.command_epoch,3);assert.equal(queued[1].body.t_ms,9020);
  assert.equal(queued[0].body.capture_id,"old-capture");
  queued[1].resolve(result({session:structuredClone(s.session),observation:{hands:[]},quality:{},capture:null}));await settle();
  yield "queued pixels never inherit another capture ID or epoch";

  configure();
  const keep = s.stream;
  t.modelStatus({state:"error",error:"model unavailable"},100);
  assert.equal(s.stream,keep);assert.equal(s.camera,"receiving");assert.match(els.notice.textContent,/model unavailable/);
  yield "model failure preserves camera preview";

  configure();
  const order=[], tracks={}, sessions=new Map([["s1",snap()]]);
  let sessionCount=0;
  s.stream={getTracks:()=>[{stop(){order.push("old-stream-stopped")}}]};
  els["camera-select"].value="iphone";
  const newTrack={label:"iPhone Continuity Camera",getSettings:()=>({deviceId:"iphone"}),addEventListener(name,fn){tracks[name]=fn},stop(){order.push("new-stream-stopped")}};
  const nextStream={getTracks:()=>[newTrack],getVideoTracks:()=>[newTrack]};
  c.navigator.mediaDevices.getUserMedia=async options=>{assert.equal(options.video.deviceId.exact,"iphone");order.push("new-stream-opened");return nextStream};
  handler=async(path,opt,body)=>{
    if(path.endsWith("/sessions")&&opt.method==="POST"){const ss=snap({session_id:"created-"+(++sessionCount),command_epoch:1,geometry_version:0,...body});sessions.set(ss.session_id,ss);return result(ss)}
    const id=path.split("/sessions/")[1]?.split("/")[0];
    if(path.endsWith("/close")){order.push("closed-"+id);const ss=sessions.get(id);ss.lifecycle_phase="closed";return result(ss)}
    if(opt.method==="GET")return result(sessions.get(id));
    throw Error(path);
  };
  await t.connectCamera();
  assert.deepEqual(order.slice(0,3),["old-stream-stopped","closed-s1","new-stream-opened"]);
  assert.equal(s.session.session_id,"created-1");assert.equal(s.stream,nextStream);
  yield "reconnect closes the prior stream and session before opening exact choice";
  const middle=inputs.find(e=>e.value==="middle");middle.checked=true;
  await t.changeSelection();
  assert.equal(s.session.session_id,"created-2");assert.deepEqual(Array.from(s.session.active_fingers),["index","middle"]);assert.equal(s.capture,null);assert.equal(s.readyConfirmed,false);
  assert.ok(order.includes("closed-created-1"));
  yield "finger selection creates a clean session and closes its predecessor";
  tracks.ended();await settle();
  assert.equal(s.stream,null);assert.equal(s.session,null);assert.ok(order.includes("closed-created-2"));
  yield "stream ended still closes the session after selection changes";
  // B-02: proposal → one display/sound → one ack with browser emitted_ms; duplicates and stale epochs are ignored.
  configure(); s.readyConfirmed=true; s.session=snap({lifecycle_phase:"ready",device_state:{input:"ready",calibration:"valid"},calibrated_fingers:["index"]});
  const acks=[];let startCalls=0;
  handler=async(path,opt,body)=>{
    if(path.endsWith("/start")){startCalls++;return result({session:snap({lifecycle_phase:"running",device_state:{input:"running",calibration:"valid"},calibrated_fingers:["index"],input_run:{counts:{candidates:0,accepted_provisional:0,withheld:0},pending_publication:0,finger_states:{}}})},"input_running")}
    if(path.endsWith("/events/ack")){acks.push(body);return result({events:body.events,summary:{counts:{candidates:1,accepted_provisional:1,withheld:0},pending_publication:0,finger_states:{index:{code:"waiting_for_lift"}}}},"published")}
    if(path.endsWith("/pause"))return result({session:snap({command_epoch:3}),last_run:{counts:{candidates:1,accepted_provisional:1,withheld:0}}},"input_stopped");
    throw Error(path)};
  t.renderInput(); assert.equal(els["input-start"].disabled,false);
  await t.startInput(); await settle();
  assert.equal(startCalls,1); assert.equal(s.session.lifecycle_phase,"running"); assert.equal(els["input-stop"].disabled,false);
  const ctx={generation:s.generation,sessionId:"s1",epoch:2,geometry:1,calibration:0,controlRevision:s.controlRevision};
  const proposal={event_id:"evt-1",candidate_id:"tap-1",decision_ref:"d1",t_ms:900,finger:"index",input_status:"accepted_provisional",virtual_key:"E4",midi_note:64,quality_flags:[]};
  now=950; t.handleInput(ctx,{proposals:[proposal],summary:{counts:{candidates:1}}}); t.handleInput(ctx,{proposals:[proposal],summary:{counts:{candidates:1}}}); await settle();
  assert.equal(acks.length,1); assert.deepEqual(JSON.parse(JSON.stringify(acks[0])),{command_epoch:2,decision_ref:"d1",clock_id:"browser-test-clock",events:[{event_id:"evt-1",emitted_ms:950}]});
  assert.equal(Object.keys(acks[0]).includes("midi_note"),false);
  t.handleInput({...ctx,epoch:1},{proposals:[{...proposal,event_id:"evt-old",decision_ref:"d0"}],summary:{}}); await settle();
  assert.equal(acks.length,1);
  t.renderInput(); assert.equal(els["input-last"].textContent.includes("E4"),true);
  t.handleInput(ctx,{proposals:[{...proposal,event_id:"evt-2",decision_ref:"d2",input_status:"withheld",virtual_key:null,midi_note:null,quality_flags:["KEY_BOUNDARY"]}],summary:{counts:{}}}); await settle();
  assert.equal(acks.length,2); t.renderInput(); assert.equal(els["input-last"].textContent.includes("건반 경계"),true);
  await t.stopInput(); await settle(); assert.equal(s.session.command_epoch,3); assert.equal(els["input-stop"].disabled,true);
  yield "B-02 input check publishes each proposal once with browser emitted_ms";


}

const scenarioNames = [
  "defaults and blank camera labels",
  "new-frame gate and bounded latest queue",
  "one HTTP flight and strict frame fields",
  "stale frame and pending epoch rejection",
  "capture start and input lock",
  "no early finish on insufficient frames",
  "business failure remains visible and retryable",
  "finish advances epoch and never automatically readies",
  "post-calibration frames use returned epoch",
  "failed frame progress unlocks controls",
  "explicit ready action",
  "stale model response ordering",
  "device runtime truth and contain keyboard bounds",
  "exact chosen camera failure without fallback",
  "same-epoch poll cannot overwrite a newer control result",
  "queued pixels never inherit another capture ID or epoch",
  "model failure preserves camera preview",
  "reconnect closes the prior stream and session before opening exact choice",
  "finger selection creates a clean session and closes its predecessor",
  "stream ended still closes the session after selection changes",
  "B-02 input check publishes each proposal once with browser emitted_ms"
];

test("B01 browser adapter: 20 retained mock scenarios + B-02 input check", { timeout: 10000 }, async (suite) => {
  const steps = scenarioSteps();
  try {
    for (const name of scenarioNames) {
      await suite.test(name, { timeout: 2000 }, async () => {
        const step = await steps.next();
        assert.equal(step.done, false, "Scenario generator ended before " + name);
        assert.equal(step.value, name);
      });
    }
    assert.equal((await steps.next()).done, true);
  } finally {
    await steps.return();
  }
});

