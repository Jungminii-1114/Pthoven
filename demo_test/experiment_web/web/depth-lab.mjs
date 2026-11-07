import {CameraSession,isIPhone,cameraError} from './camera-session.mjs';
import {FINGERS,calibrate} from '../models/tap-detector.mjs';
import {DepthComparison,calibrateDepth,METHODS,evaluateTrial} from '../models/depth-detector.mjs';

const $=id=>document.getElementById(id),video=$('video'),overlay=$('overlay'),ctx=overlay.getContext('2d');
const frame=document.createElement('canvas'),fc=frame.getContext('2d');
const names={thumb:'엄지',index:'검지',middle:'중지',ring:'약지',pinky:'소지'};
const methodNames={pose_full:'관절·전체',pose_matched:'관절·동일 프레임',depth:'깊이',fusion:'관절+깊이'};
let stream=null,tracker=null,trackerLoading=false,connection=0,lastVideo=-1,raf=0,epoch=0,frameId=0;
let anchors=[],calibration={},engine=new DepthComparison(),rest=null,capture=null,pending=false,lastRequest=0;
let model={state:'not_loaded'},trial=null,trials=[],stopping=false,recorder=null,recordChunks=[],recordBlob=null;
let poseSignals={},matchedSignals={},counts=Object.fromEntries(METHODS.map(m=>[m,0])),audio=null;
let mirror=false,flip=false,latestPoints=null,reviewURLs=new Map(),lastInfer=0;
const noteNames=['도','레','미','파','솔','라','시'],notes=[60,62,64,65,67,69,71];
const notify=t=>$('message').textContent=t;
const options=()=>({poseWeight:Number($('weight').value),lift:Number($('liftThreshold').value),release:Number($('returnThreshold').value),maxGapMs:600});
const session=new CameraSession(navigator.mediaDevices,state=>{
  stream=state.stream;
  if(!stream){video.srcObject=null;cancelAnimationFrame(raf);$('placeholder').hidden=false;$('cameraStatus').textContent='카메라 미연결';if(trial)stopTrial();if(capture){capture=null;setLocked(false);}}
  if(state.error)notify(cameraError(state.error));
});

async function api(path,body,timeout=20000){
  const response=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(timeout)});
  const data=await response.json();if(!response.ok)throw Error(data.error||`HTTP ${response.status}`);return data;
}
async function refresh(){
  try{const cameras=await session.refresh(),selected=$('camera').value;
    $('camera').replaceChildren(new Option('기본 카메라',''),...cameras.map((c,i)=>new Option(c.label||`카메라 ${i+1}`,c.deviceId)));
    $('camera').value=cameras.some(c=>c.deviceId===selected)?selected:'';return cameras;
  }catch(error){notify(cameraError(error));return [];}
}
function rebuild(clear=true){epoch++;engine=new DepthComparison(calibration,options());if(clear){counts=Object.fromEntries(METHODS.map(m=>[m,0]));poseSignals={};matchedSignals={};}renderCalibration();}
function invalidate(){if(trial)stopTrial();capture=null;rest=null;calibration={};rebuild();if(!trial)setLocked(false);}
async function connect(id=$('camera').value){
  const request=++connection;$('connect').disabled=true;invalidate();
  try{const acquired=await session.connect(id);if(!acquired||request!==connection)return;
    video.srcObject=acquired;await video.play();lastVideo=-1;
    $('cameraStatus').textContent=`카메라: ${acquired.getVideoTracks()[0].label}`;cancelAnimationFrame(raf);loop();await refresh();void loadTracker();
  }catch(error){notify(cameraError(error));}finally{if(request===connection)$('connect').disabled=false;}
}
async function loadTracker(){
  if(tracker||trackerLoading)return;trackerLoading=true;$('trackerStatus').textContent='손 추적 모델 로딩 중';
  try{const {FilesetResolver,HandLandmarker}=await import('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/+esm');
    const vision=await FilesetResolver.forVisionTasks('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm');
    const o={baseOptions:{modelAssetPath:'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task',delegate:'GPU'},runningMode:'VIDEO',numHands:2};
    try{tracker=await HandLandmarker.createFromOptions(vision,o);}catch{o.baseOptions.delegate='CPU';tracker=await HandLandmarker.createFromOptions(vision,o);}
    $('trackerStatus').textContent='손 추적 준비 완료';
  }catch(error){$('trackerStatus').textContent=`손 추적 실패: ${error.message} · 영상은 유지됩니다.`;}finally{trackerLoading=false;}
}
async function pollModel(){
  try{model=await api('/api/depth/status');$('depthStatus').textContent=`Depth: ${model.state} · ${model.device||'장치 대기'}${model.error?' · '+model.error:''}`;$('prepare').disabled=model.state==='loading';}
  catch(error){$('depthStatus').textContent=`Depth 서버 연결 실패: ${error.message}`;}
}
function orientation(){for(const el of [video,overlay,$('depthImage')])el.style.transform=`scale(${mirror?-1:1},${flip?-1:1})`;$('mirror').setAttribute('aria-pressed',String(mirror));$('flip').setAttribute('aria-pressed',String(flip));}
function inputLocked(){return !!trial||!!capture;}
function updateAnchors(){ $('anchorStatus').textContent=`책상 기준점 ${anchors.length}개 / 최소 3개`;$('anchorsMode').checked=anchors.length<4; }
$('overlay').onclick=e=>{
  if(!$('anchorsMode').checked)return;if(inputLocked())return notify('기록·보정·추론이 끝난 뒤 기준점을 변경하세요.');
  const r=overlay.getBoundingClientRect(),s=Math.min(r.width/overlay.width,r.height/overlay.height);
  let x=(e.clientX-r.left-(r.width-overlay.width*s)/2)/(overlay.width*s),y=(e.clientY-r.top-(r.height-overlay.height*s)/2)/(overlay.height*s);
  if(mirror)x=1-x;if(flip)y=1-y;if(x<0||x>1||y<0||y>1)return;
  if(anchors.length>=8)return notify('기준점은 최대 8개입니다.');anchors.push([x,y]);invalidate();updateAnchors();
};
function draw(points){
  overlay.width=frame.width;overlay.height=frame.height;ctx.clearRect(0,0,overlay.width,overlay.height);
  const w=overlay.width,h=overlay.height,y=Number($('keyboardY').value)*h;
  for(let i=0;i<7;i++){ctx.fillStyle=i%2?'#e7edf32a':'#ffffff20';ctx.fillRect((.1+i*.8/7)*w,y,.8*w/7,.18*h);ctx.strokeStyle='#b8d6e8';ctx.strokeRect((.1+i*.8/7)*w,y,.8*w/7,.18*h);ctx.fillStyle='#eef2fa';ctx.font='14px system-ui';ctx.fillText(noteNames[i],(.12+i*.8/7)*w,y+.16*h);}
  anchors.forEach(([x,y],i)=>{ctx.beginPath();ctx.arc(x*w,y*h,5,0,Math.PI*2);ctx.fillStyle='#ffd097';ctx.fill();ctx.fillText(String(i+1),x*w+8,y*h);});
  if(points)for(const ids of Object.values(FINGERS)){ctx.beginPath();ids.forEach((id,i)=>{const p=points[id];i?ctx.lineTo(p.x*w,p.y*h):ctx.moveTo(p.x*w,p.y*h);});ctx.strokeStyle='#a4e2d0';ctx.lineWidth=2;ctx.stroke();}
}
function play(event){
  if($('audition').value!==event.method||!event.tip)return;
  const [x,y]=event.tip,top=Number($('keyboardY').value);if(x<.1||x>=.9||y<top||y>top+.18)return;
  try{audio??=new AudioContext();void audio.resume();const osc=audio.createOscillator(),gain=audio.createGain(),now=audio.currentTime;
    osc.type='triangle';osc.frequency.value=440*2**((notes[Math.floor((x-.1)/.8*7)]-69)/12);gain.gain.setValueAtTime(.12,now);gain.gain.exponentialRampToValueAtTime(.001,now+.25);osc.connect(gain).connect(audio.destination);osc.start();osc.stop(now+.26);
  }catch{}
}
function consume(observed,active,t){
  for(const [method,events] of Object.entries(observed.events))for(const e of events){counts[method]++;play(e);$('lastEvent').textContent=`${methodNames[method]} · ${names[e.finger]} · ${Math.round(e.latency_ms)}ms 지연 / 접촉 미확인`;
    if(active&&trial===active&&t>=active.started_ms&&t<=active.started_ms+(active.duration_ms??Infinity))active.predictions[method].push({...e,t_ms:e.t_ms-active.started_ms,emitted_ms:e.emitted_ms-active.started_ms});}
}
function renderSignals(){
  $('signals').textContent=METHODS.map(m=>`${methodNames[m]}: ${counts[m]}회\n`+Object.keys(calibration).map(f=>{const s=(m==='pose_full'?poseSignals:matchedSignals)?.[m]?.[f];return `  ${names[f]} ${s?.valid?s.score.toFixed(2)+' '+s.phase:'관측 보류'}`;}).join('\n')).join('\n');
}
function loop(){
  if(!stream)return;const t=performance.now();
  if(video.readyState>=2&&video.videoWidth&&video.currentTime!==lastVideo){
    lastVideo=video.currentTime;$('placeholder').hidden=true;
    frame.width=Math.min(640,video.videoWidth);frame.height=Math.round(video.videoHeight*frame.width/video.videoWidth);fc.drawImage(video,0,0,frame.width,frame.height);
    let world=null,points=null;
    if(tracker)try{const result=tracker.detectForVideo(frame,t),i=result.handedness.findIndex(x=>x[0]?.categoryName===$('hand').value);if(i>=0){world=result.worldLandmarks[i];points=result.landmarks[i];}$('trackerStatus').textContent=points?'손 추적: 선택한 손 관측 중':'손 추적: 선택한 손이 보이지 않습니다.';}
    catch(error){$('trackerStatus').textContent=`손 추적 보류: ${error.message}`;}
    latestPoints=points;draw(points);
    const active=trial&&!stopping?trial:null,emitted=performance.now(),obs=engine.update(t,world,null,points,emitted,true);poseSignals=obs.signals;consume(obs,active,t);
    if(active)active.pose_frames.push({t_ms:t-active.started_ms,emitted_ms:emitted-active.started_ms,world_landmarks:world,landmarks:points,signals:obs.signals});
    if(capture&&world)capture.world.push(world);
    if(model.state==='ready'&&anchors.length>=3&&!pending&&t-lastRequest>=Number($('interval').value)&&!stopping){
      lastRequest=t;const image=frame.toDataURL('image/jpeg',.8);void inferFrame({t,world,points,image,active,captureJob:capture,token:epoch});
    }
    renderSignals();
  }
  if(capture&&t-capture.start>=3000&&!pending)finishCapture();
  if(trial&&!stopping){$('trialStatus').textContent=`기록 ${(t-trial.started_ms).toFixed(0)} / 10000ms`;if(t-trial.started_ms>=10000)stopTrial();}
  raf=requestAnimationFrame(loop);
}
async function inferFrame(job){
  pending=true;const {t,world,points,image,active,captureJob,token}=job;let depth=null;
  try{
    if(points&&points.every(p=>p.x>=0&&p.x<=1&&p.y>=0&&p.y<=1)){
      const started=performance.now(),id=++frameId;depth=await api('/api/depth/frame',{frame_id:id,t_ms:t,image,input_size:Number($('inputSize').value),landmarks:points.map(p=>[p.x,p.y]),anchors});
      if(depth.frame_id!==id||depth.t_ms!==t)throw Error('깊이 응답의 프레임·시각이 입력과 다릅니다.');
      lastInfer=performance.now()-started;$('speed').textContent=`왕복 ${Math.round(lastInfer)}ms · 추론 ${depth.inference_ms}ms · 最大約 ${(1000/Math.max(lastInfer,Number($('interval').value))).toFixed(1)} 깊이 FPS${lastInfer>180?' · 빠른 반복 탭 비교에는 표본 속도가 부족할 수 있습니다.':''}`;
    }
    if(token!==epoch)return;
    const emitted=performance.now(),obs=engine.update(t,world,depth,points,emitted);matchedSignals=obs.signals;consume(obs,active,t);
    if(depth?.preview)$('depthImage').src=depth.preview;
    if(captureJob&&capture===captureJob)captureJob.paired.push({world,depth});
    if(active&&trial===active){const {preview,...signals}=depth||{};
      active.depth_frames.push({t_ms:t-active.started_ms,frame_id:depth?.frame_id??null,world_landmarks:world,landmarks:points,depth:depth?signals:null,signals:obs.signals,roundtrip_ms:depth?lastInfer:null,evidence_image:active.record_frames?image:null});}
    if(depth&&!depth.valid)notify(`깊이 관측 보류: ${depth.reason}`);
  }catch(error){
    if(token===epoch){const obs=engine.update(t,null,null,null,performance.now());matchedSignals=obs.signals;
      if(active&&trial===active){active.depth_frames.push({t_ms:t-active.started_ms,depth:null,signals:obs.signals,evidence_image:active.record_frames?image:null});active.processing_errors.push({t_ms:t-active.started_ms,error:error.message});}notify(`깊이 처리 보류: ${error.message}`);}
  }finally{pending=false;if(stopping)finalizeTrial();if(capture&&performance.now()-capture.start>=3000)finishCapture();}
}
function renderCalibration(){
  $('calibration').textContent=Object.entries(calibration).map(([f,c])=>`${names[f]}: 관절 준비 / ${c.depth?'깊이 준비':c.depth_error||'깊이 보류'}`).join('\n')||'보정 대기';
}
function startCapture(kind){
  if(!['weight','interval','liftThreshold','returnThreshold'].every(id=>$(id).checkValidity()))return notify('설정값을 표시된 허용 범위 안으로 입력하세요.');
  if(inputLocked())return notify('현재 처리가 끝난 뒤 보정하세요.');
  if(!stream||!tracker||model.state!=='ready'||anchors.length<3)return notify('카메라·손 추적·깊이 모델과 책상 기준점 3개 이상을 먼저 준비하세요.');
  if(kind==='lift'&&!rest)return notify('기준 자세부터 저장하세요.');
  capture={kind,finger:$('finger').value,world:[],paired:[],start:performance.now()};setLocked(true);notify(kind==='rest'?'기준 자세를 3초 유지하세요.':'선택 손가락만 들어 올리고 3초 유지하세요.');
}
function finishCapture(){
  const c=capture;if(!c)return;capture=null;
  try{if(c.kind==='rest'){if(c.world.length<5||c.paired.length<5)throw Error('유효 샘플이 부족합니다. 깊이 속도와 손 추적을 확인하세요.');rest=c;calibration={};rebuild();notify(`기준 저장: 깊이 샘플 ${c.paired.length}개. 선택 손가락을 들어 들림을 저장하세요.`);}
    else{const pose=calibrate(rest.world,c.world,c.finger);let depth=null,depth_error=null;
      try{depth=calibrateDepth(rest.paired,c.paired,c.finger);}catch(error){depth_error=error.message;}
      calibration[c.finger]={pose,depth,depth_error};rebuild();notify(depth?'관절·깊이 보정 완료. 손가락을 내렸다가 탭하세요.':`관절만 보정됨. ${depth_error}`);}
  }catch(error){notify(`보정 실패: ${error.message}`);}finally{setLocked(false);}
}
const lockIDs=['hand','inputSize','finger','rest','lift','reset','clearAnchors','interval','weight','liftThreshold','returnThreshold','keyboardY','person','session','scenario','split','recordVideo','recordFrames','connect','camera','iphone','refresh'];
function setLocked(locked){for(const id of lockIDs)$(id).disabled=locked;$('record').disabled=locked;}
function startTrial(){
  if(!['weight','interval','liftThreshold','returnThreshold'].every(id=>$(id).checkValidity()))return notify('설정값을 표시된 허용 범위 안으로 입력하세요.');
  if(inputLocked())return notify('현재 추론이 끝난 뒤 다시 눌러주세요.');
  if(!stream||!tracker||model.state!=='ready'||anchors.length<3||!Object.keys(calibration).length)return notify('카메라·모델·책상 기준점·손가락 보정을 먼저 완료하세요.');
  if(!(options().release<options().lift))return notify('복귀 임계값은 들림 임계값보다 작아야 합니다.');
  rebuild();recordBlob=null;recordChunks=[];stopping=false;
  trial={id:crypto.randomUUID(),person_id:$('person').value,session_id:$('session').value,split:$('split').value,scenario:$('scenario').value,selected_finger:$('finger').value,hand_side:$('hand').value,
    started_ms:performance.now(),duration_ms:null,options:{...engine.options},request_interval_ms:Number($('interval').value),calibration:structuredClone(calibration),anchors:structuredClone(anchors),model:{...structuredClone(model),input_size:Number($('inputSize').value)},keyboard_y:Number($('keyboardY').value),orientation:{mirror,flip},camera:stream.getVideoTracks()[0].getSettings(),record_frames:$('recordFrames').checked,
    pose_frames:[],depth_frames:[],predictions:Object.fromEntries(METHODS.map(m=>[m,[]])),processing_errors:[],truth:null,unknown_intervals:[],annotation_confirmed:false,video:null};
  if($('recordVideo').checked){
    try{if(!window.MediaRecorder)throw Error('이 브라우저는 영상 기록을 지원하지 않습니다.');
      const mime=['video/webm;codecs=vp8','video/webm','video/mp4'].find(x=>MediaRecorder.isTypeSupported(x));
      recorder=new MediaRecorder(stream,mime?{mimeType:mime}:{});const active=trial;
      recorder.ondataavailable=e=>{if(e.data.size)recordChunks.push(e.data);};
      recorder.onstop=()=>{recordBlob=new Blob(recordChunks,{type:recorder.mimeType});active.video={...active.video,filename:`pthoven-${active.id}.${recorder.mimeType.includes('mp4')?'mp4':'webm'}`,mime_type:recorder.mimeType};reviewURLs.set(active.id,URL.createObjectURL(recordBlob));recorder=null;if(stopping&&!pending)finalizeTrial();};
      recorder.start();trial.video={time_origin_ms:performance.now()-trial.started_ms,timing:'browser_start_approximation'};
    }catch(error){notify(`영상 기록 실패: ${error.message} · 샘플 이미지 기록을 사용합니다.`);trial.record_frames=true;recorder=null;}
  }
  setLocked(true);notify('기록 시작. 지시 신호는 정답 라벨로 저장하지 않습니다.');
}
function stopTrial(){if(!trial||stopping)return;trial.duration_ms=performance.now()-trial.started_ms;stopping=true;if(recorder&&recorder.state!=='inactive')recorder.stop();if(!pending&&!recorder)finalizeTrial();}
function finalizeTrial(){if(!trial||!stopping||pending||recorder)return;trials.push(trial);trial=null;stopping=false;setLocked(false);$('trialStatus').textContent=`기록 ${trials.length}개`;refreshReview(trials.length-1);notify('기록 완료. 원본을 검수하고 정답을 라벨링하세요.');}
function selectedTrial(){return trials[Number($('reviewTrial').value)];}
function refreshReview(index=0){$('reviewTrial').replaceChildren(...trials.map((t,i)=>new Option(`${i+1}. ${t.person_id}/${t.session_id} · ${t.scenario}`,i)));$('reviewTrial').value=String(index);showReview();}
function showReview(){const t=selectedTrial();if(!t)return;const url=reviewURLs.get(t.id);if(url)$('reviewVideo').src=url;else $('reviewVideo').removeAttribute('src');$('frameSlider').max=Math.max(0,t.depth_frames.length-1);$('frameSlider').value=0;$('confirmLabels').checked=t.annotation_confirmed===true;showFrame();renderLabels();$('metrics').replaceChildren();}
function showFrame(){const t=selectedTrial(),f=t?.depth_frames[Number($('frameSlider').value)];if(!f)return;$('reviewFrame').hidden=!f.evidence_image;if(f.evidence_image)$('reviewFrame').src=f.evidence_image;$('labelTime').value=Math.round(f.t_ms);$('reviewTime').textContent=`입력 프레임 시각 ${Math.round(f.t_ms)}ms / 예측 표시 없음`;}
function renderLabels(){const t=selectedTrial();if(!t)return;$('labels').replaceChildren();
  if(t.truth===null)$('labels').textContent='정답 미지정 · F1 계산 안 함';else if(!t.truth.length)$('labels').textContent='무타건으로 지정됨 · 독립 검수 확인 필요';
  for(const [i,e] of (t.truth||[]).entries()){const b=document.createElement('button');b.textContent=`${names[e.finger]} ${Math.round(e.t_ms)}ms ×`;b.onclick=()=>{t.truth.splice(i,1);t.annotation_confirmed=false;$('confirmLabels').checked=false;renderLabels();};$('labels').append(b);}
  $('unknowns').replaceChildren();for(const [i,r] of (t.unknown_intervals||[]).entries()){const b=document.createElement('button');b.textContent=`불명 ${r[0]}~${r[1]}ms ×`;b.onclick=()=>{t.unknown_intervals.splice(i,1);renderLabels();};$('unknowns').append(b);}
}
function evaluate(){const t=selectedTrial();if(!t)return notify('검수할 시행을 선택하세요.');t.annotation_confirmed=$('confirmLabels').checked;
  try{const result=evaluateTrial(t,Number($('tolerance').value));t.evaluation=result;const table=document.createElement('table');
    const rows=[['방식','후보','F1','FP/분','시각 MAE','관측률','출력 지연'],...METHODS.map(m=>{const x=result.methods[m];const fmt=(v,s='')=>v===null||v===undefined?'—':v.toFixed(2)+s;return [methodNames[m],x.predicted??((x.tp||0)+(x.fp||0)),fmt(x.f1),fmt(x.false_events_per_minute),fmt(x.timing_mae_ms,'ms'),fmt(x.coverage_ratio*100,'%'),fmt(x.mean_emit_latency_ms,'ms')];})];
    rows.forEach((row,i)=>{const tr=document.createElement('tr');row.forEach(value=>{const cell=document.createElement(i?'td':'th');cell.textContent=value;tr.append(cell);});table.append(tr);});$('metrics').replaceChildren(table);
    const p=document.createElement('p');p.className='tiny';p.textContent=result.labelled?`라벨 검수됨 · 불명 제외 ${Math.round(result.known_duration_ms)}ms. 깊이/결합은 관절·동일 프레임과 비교하세요.`:'독립 정답 라벨 미확인 · F1을 계산하지 않았습니다.';$('metrics').append(p);
  }catch(error){notify(error.message);}
}
function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function exportData(){if(trial){stopTrial();while(trial)await new Promise(r=>setTimeout(r,50));}if(!trials.length)return notify('먼저 시행을 기록하세요.');download(new Blob([JSON.stringify({schema_version:2,kind:'pthoven_depth_comparison',evidence_type:'browser_rgb_landmark_depth_capture',created_at:new Date().toISOString(),contact_verified:false,trials})],{type:'application/json'}),'pthoven-depth-capture.json');}

$('connect').onclick=()=>connect();$('camera').onchange=()=>connect();$('refresh').onclick=refresh;
$('iphone').onclick=async()=>{let devices=await refresh(),phone=devices.find(isIPhone);if(!phone&&!stream){await connect('');devices=await refresh();phone=devices.find(isIPhone);}if(phone){$('camera').value=phone.deviceId;await connect(phone.deviceId);}else notify('iPhone이 장치 목록에 없습니다. 잠금 해제·연속성 카메라·USB 연결을 확인하고 장치를 다시 찾아주세요.');};
$('disconnect').onclick=()=>{connection++;invalidate();session.stop();};
$('prepare').onclick=async()=>{try{model=await api('/api/depth/prepare',{});await pollModel();}catch(error){notify(error.message);}};
$('retryTracker').onclick=loadTracker;$('mirror').onclick=()=>{mirror=!mirror;orientation();};$('flip').onclick=()=>{flip=!flip;orientation();};$('heatmap').onchange=()=>{$('depthImage').hidden=!$('heatmap').checked;};
$('clearAnchors').onclick=()=>{if(inputLocked())return;anchors=[];invalidate();updateAnchors();};$('hand').onchange=invalidate;
$('inputSize').onchange=invalidate;
$('rest').onclick=()=>startCapture('rest');$('lift').onclick=()=>startCapture('lift');$('reset').onclick=()=>{if(!inputLocked())invalidate();};
for(const id of ['weight','liftThreshold','returnThreshold'])$(id).onchange=()=>{if(!inputLocked())rebuild();};
$('record').onclick=startTrial;$('stop').onclick=stopTrial;$('export').onclick=exportData;$('reviewTrial').onchange=showReview;$('frameSlider').oninput=showFrame;
$('reviewVideo').ontimeupdate=()=>{const t=selectedTrial();if(t)$('labelTime').value=Math.round($('reviewVideo').currentTime*1000+(t.video?.time_origin_ms||0));};
$('addLabel').onclick=()=>{const t=selectedTrial();if(!t)return;const n=Number($('labelTime').value);if(!Number.isFinite(n)||n<0||n>t.duration_ms)return notify('시행 범위 안의 정답 시각을 입력하세요.');t.truth??=[];t.truth.push({finger:$('labelFinger').value,t_ms:n});t.truth.sort((a,b)=>a.t_ms-b.t_ms);t.annotation_confirmed=false;$('confirmLabels').checked=false;renderLabels();};
$('negative').onclick=()=>{const t=selectedTrial();if(t){t.truth=[];t.annotation_confirmed=false;$('confirmLabels').checked=false;renderLabels();}};
$('clearLabels').onclick=()=>{const t=selectedTrial();if(t){t.truth=null;t.annotation_confirmed=false;$('confirmLabels').checked=false;renderLabels();}};
$('addUnknown').onclick=()=>{const t=selectedTrial();if(!t)return;const a=Number($('unknownStart').value),b=Number($('unknownEnd').value);if(a<0||b<=a||b>t.duration_ms)return notify('시행 범위 안의 불명 시작·끝을 입력하세요.');t.unknown_intervals??=[];t.unknown_intervals.push([a,b]);renderLabels();};
$('confirmLabels').onchange=()=>{const t=selectedTrial();if(t)t.annotation_confirmed=$('confirmLabels').checked;};$('evaluate').onclick=evaluate;
$('downloadVideo').onclick=()=>{const t=selectedTrial(),url=t&&reviewURLs.get(t.id);if(!url)return notify('이 시행의 영상을 기록하지 않았거나 다시 열지 않았습니다.');const a=document.createElement('a');a.href=url;a.download=t.video?.filename||'pthoven-evidence.webm';a.click();};
$('importJSON').onchange=async e=>{try{const f=e.target.files[0];if(!f)return;if(f.size>100_000_000)throw Error('100MB 이하 JSON을 사용하세요.');const data=JSON.parse(await f.text());if(data.schema_version!==2||data.kind!=='pthoven_depth_comparison'||!Array.isArray(data.trials)||!data.trials.length)throw Error('Depth 비교 실험 JSON이 아닙니다.');if(trial||capture)throw Error('기록·보정 종료 후 불러오세요.');trials=data.trials;refreshReview();$('trialStatus').textContent=`불러온 시행 ${trials.length}개`;}catch(error){notify(error.message);}};
$('importVideo').onchange=e=>{const t=selectedTrial(),f=e.target.files[0];if(!t||!f)return;const old=reviewURLs.get(t.id);if(old)URL.revokeObjectURL(old);reviewURLs.set(t.id,URL.createObjectURL(f));$('reviewVideo').src=reviewURLs.get(t.id);};
window.addEventListener('beforeunload',()=>{session.stop();for(const url of reviewURLs.values())URL.revokeObjectURL(url);});
navigator.mediaDevices?.addEventListener?.('devicechange',()=>void refresh());
orientation();updateAnchors();void refresh();void pollModel();setInterval(()=>void pollModel(),2500);
