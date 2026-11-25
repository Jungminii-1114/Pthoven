import {FINGERS,PythonTapClient,RawCalibrationCapture,tapAPI} from './python-tap-client.mjs';
import {RECORDING_PROTOCOL,recordingCue} from './tap-recording-protocol.mjs';
import {loadTapMode,saveTapMode,TAP_MODE_LABELS} from './tap-mode-settings.mjs';
import {CameraSession,isIPhone,cameraError} from './camera-session.mjs';
import {SkinObserver} from './skin-observer.mjs';
import {LiveTapTrace,formatSignals,formatThresholds,selectHandInput,formatTraceHealth} from './tap-live-diagnostics.mjs';
const $=id=>document.getElementById(id);
const video=$('video'),canvas=$('preview'),ctx=canvas.getContext('2d');
let activeMode=loadTapMode();$('tapMode').value=activeMode;
function showTapMode(){$('modeStatus').textContent=`선택한 판정 방식: ${TAP_MODE_LABELS[activeMode]}`;}
showTapMode();
let stream=null,tracker=null,raf=0,lastVideo=-1,busy=false,connecting=false;
let connectionRequest=0,trackerRequest=0,trackerLoading=false,trackerError=null,lastFrameAt=0,frameCount=0,statsAt=0,statsCount=0,previewFps=0;
let models={},restFrames=[],capture=null,trial=null,trials=[],total=0,calibrationEpoch=0;
let calibrationAttempts=[],lastCaptureUI=0;
let lastStatusRender=-Infinity,lastTrackerRender=-Infinity;
const liveTrace=new LiveTapTrace();let lastSignalRender=-Infinity;
const names={thumb:'엄지',index:'검지',middle:'중지',ring:'약지',pinky:'소지'};
const pythonClient=new PythonTapClient({onStatus:text=>{$('pythonState').textContent=text;},
  onError:error=>{$('pythonState').textContent=`Python 연결 문제: ${error.message}`;if(trial)trial.processing_errors.push({t_ms:performance.now()-trial.started_ms,error:error.message});message(`Python 판정 보류: ${error.message}`);},
  onFrame:(observed,target,transport)=>{
    if(capture||busy)return;
    liveTrace.add(transport.input_frame,observed);
    if(performance.now()-lastSignalRender>=200||observed.events.length){lastSignalRender=performance.now();$('signals').textContent=formatSignals(observed.signals,names);$('windowCount').textContent=liveTrace.candidateCount;$('traceHealth').textContent=formatTraceHealth(liveTrace.summary);drawTapTrend();}
    const events=observed.events.map(e=>({...e,decision_frame_ms:e.emitted_ms,received_ms:transport.received_ms,latency_ms:transport.received_ms-e.t_ms}));
    if(events.length){total+=events.length;$('count').textContent=total;$('lastEvent').textContent=events.map(e=>`${names[e.finger]} · TIP 복귀 + 관절 · 출력 ${e.latency_ms.toFixed(0)}ms`).join(' · ');}
    if(target&&target===trial&&observed.t_ms>=target.started_ms&&observed.t_ms<=target.started_ms+(target.duration_ms??Infinity)){
      target.predictions.push(...events.map(e=>({...e,t_ms:e.t_ms-target.started_ms,emitted_ms:e.emitted_ms-target.started_ms,received_ms:e.received_ms-target.started_ms})));
    }
  }});
const cameraSession=new CameraSession(navigator.mediaDevices,onCameraChange);
let skinHistory=[];
const skinObserver=new SkinObserver({
  send:async payload=>{
    const response=await fetch('/api/skin/observe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:AbortSignal.timeout(3000)});
    const result=await response.json();if(!response.ok)throw Error(result.error||`HTTP ${response.status}`);return result;
  },
  onStatus:text=>{$('skinSignals').textContent=text;},
  onResult:(result,target,pair)=>{
    const emitted=performance.now();
    $('skinPreview').src=pair.patch;$('skinPreview').hidden=false;
    $('skinSignals').textContent=result.valid?
      `국소 움직임  ${result.local_flow_px.toFixed(4)} px/쌍\n겉보기 변형  ${result.apparent_strain.toFixed(4)}\n밝기 변화    ${result.brightness_shift.toFixed(2)}\n프레임 간격  ${result.dt_ms.toFixed(0)} ms · 응답 ${(emitted-result.t_ms).toFixed(0)} ms`:
      `분석 보류: ${result.reason==='insufficient_texture'?'패치의 질감이 부족합니다.':result.reason}`;
    skinHistory.push(result.valid?result.local_flow_px:null);skinHistory=skinHistory.slice(-80);drawSkinTrend();
    if(target&&trial===target&&result.previous_t_ms>=target.started_ms){
      target.skin_observations.push({...result,previous_t_ms:result.previous_t_ms-target.started_ms,t_ms:result.t_ms-target.started_ms,emitted_ms:emitted-target.started_ms,
        ...(target.capture_skin?{previous_patch:pair.previous_patch,patch:pair.patch}:{})});
    }
  }
});
function drawSkinTrend(){
  const c=$('skinTrend'),k=c.getContext('2d');k.clearRect(0,0,c.width,c.height);k.fillStyle='#090d15';k.fillRect(0,0,c.width,c.height);
  const max=Math.max(.05,...skinHistory.filter(x=>x!==null));k.fillStyle='#aab7cb';k.font='12px system-ui';k.fillText(`0 – ${max.toFixed(3)} px/쌍`,8,16);
  k.strokeStyle='#a4e2d0';k.lineWidth=2;k.beginPath();let connected=false;
  skinHistory.forEach((v,i)=>{if(v===null){connected=false;return;}const x=8+i*(c.width-16)/79,y=c.height-8-v/max*(c.height-30);if(connected)k.lineTo(x,y);else k.moveTo(x,y);connected=true;});k.stroke();
}
function setStatusText(id,text){
  const value=['activeCamera','videoState','trackerState'].includes(id)?text.replace(/^(현재 장치|영상 수신|손 추적):\s*/,''):text;
  const el=$(id);if(el.textContent===value)return;el.textContent=value;el.title=value;
}
function message(text){setStatusText('status',text);}
async function resetPython(calibration={}){const requestedMode=$('tapMode').value;liveTrace.clear();lastSignalRender=-Infinity;$('signals').textContent='Python 판정 대기';$('windowCount').textContent='0';$('traceHealth').textContent=formatTraceHealth(liveTrace.summary);drawTapTrend();const ready=await pythonClient.reset(calibration,{mode:requestedMode});if(ready&&requestedMode===$('tapMode').value){activeMode=requestedMode;saveTapMode(activeMode);showTapMode();}return ready;}
function restartDetector(){return resetPython(models);}
function reset(){calibrationEpoch++;skinObserver.reset();if(capture||busy)cancelCapture();if(trial)void stopTrial();models={};void resetPython();restFrames=[];total=0;$('count').textContent='0';$('calibrated').textContent='보정된 손가락: 없음';$('calibrationProgress').textContent='기준 자세부터 저장하세요.';}
function clearPreview(){skinObserver.reset();cancelAnimationFrame(raf);video.srcObject=null;ctx.clearRect(0,0,canvas.width,canvas.height);if(capture||busy)cancelCapture();if(trial)void stopTrial();$('cameraPlaceholder').hidden=false;}
function onCameraChange(state){
  stream=state.stream;
  $('camera').disabled=state.phase==='opening';$('useIPhone').disabled=state.phase==='opening';
  if(!stream)clearPreview();
  setStatusText('cameraState',state.phase==='streaming'?'카메라 열림 · 영상 확인 중':state.phase==='opening'?'카메라 권한·장치 응답 대기':'카메라 미연결');
  if(!stream){setStatusText('activeCamera','없음');setStatusText('videoState','대기');setStatusText('trackerState','대기');$('cameraStatus').dataset.state='waiting';}
  if(state.error){setStatusText('cameraState','카메라 연결 문제');message(cameraError(state.error));}
}
function stopCamera(){connectionRequest++;connecting=false;$('connect').disabled=false;cameraSession.stop();}
async function refreshCameraList(){
  try{
    const cameras=await cameraSession.refresh(),select=$('camera');
    const active=stream?.getVideoTracks()[0]?.getSettings().deviceId;
    const previous=active||select.value;
    const options=cameras.map((c,i)=>new Option(c.label?`${isIPhone(c)?'📱 ':''}${c.label}`:`권한 전 카메라 ${i+1}`,c.deviceId));
    select.replaceChildren(new Option('기본 카메라',''),...options);
    select.value=cameras.some(c=>c.deviceId===previous)?previous:'';
    const named=cameras.filter(c=>c.label),iphone=cameras.find(isIPhone);
    $('cameraInventory').textContent=iphone?`장치 ${cameras.length}개 · iPhone 연속성 카메라 발견`:
      named.length?`장치 ${cameras.length}개 · ${named.map(c=>c.label).join(' · ')}`:
      `장치 ${cameras.length}개 · 카메라 연결로 권한을 허용하면 이름을 확인할 수 있습니다.`;
    $('cameraHelp').textContent=iphone?'📱 iPhone 연결을 누르거나 목록에서 iPhone을 선택하세요.':
      'iPhone이 없으면 잠금을 해제해 Mac 가까이 두고, 필요하면 USB로 연결한 뒤 장치를 다시 찾아주세요. 목록에 없는 장치를 웹에서 강제로 열 수는 없습니다.';
    return cameras;
  }catch(error){$('cameraInventory').textContent=cameraError(error);return [];}
}
async function connect(deviceId=$('camera').value){
  if(connecting)return;const request=++connectionRequest;connecting=true;$('connect').disabled=true;
  reset();
  try{
    message('카메라 권한을 허용해주세요. 영상과 손 추적 상태는 아래에서 각각 확인할 수 있습니다.');
    const acquired=await cameraSession.connect(deviceId);
    if(!acquired||request!==connectionRequest)return;
    video.srcObject=acquired;video.muted=true;
    // Display native video immediately, even if model loading or inference fails.
    video.play().catch(error=>{if(request===connectionRequest){video.controls=true;message(`영상 재생 버튼을 눌러주세요: ${error.message}`);}});
    lastVideo=-1;lastFrameAt=performance.now();frameCount=0;statsAt=performance.now();statsCount=0;previewFps=0;lastStatusRender=-Infinity;lastTrackerRender=-Infinity;
    const track=acquired.getVideoTracks()[0];
    setStatusText('activeCamera',`${isIPhone(track)?'📱 ':''}${track.label||'이름 없는 카메라'}`);
    message(`카메라 연결: ${track.label||'이름 없는 카메라'} · 영상 수신을 확인하고 있습니다.`);
    loop();await refreshCameraList();if(request!==connectionRequest)return;
    void loadTracker();
  }catch(error){if(request===connectionRequest)message(cameraError(error));}
  finally{if(request===connectionRequest){connecting=false;$('connect').disabled=false;}}
}
async function connectIPhone(){
  if(connecting)return;
  const request=connectionRequest;
  let cameras=await refreshCameraList(),iphone=cameras.find(isIPhone);
  if(request!==connectionRequest)return;
  if(!iphone&&!stream){
    const expected=connectionRequest+1;await connect('');
    if(connectionRequest!==expected||!stream)return;
    cameras=await refreshCameraList();if(connectionRequest!==expected)return;iphone=cameras.find(isIPhone);
  }
  if(!iphone){message('iPhone이 장치 목록에 아직 없습니다. 현재 연결 장치 이름을 확인하고, iPhone 연결 후 장치 다시 찾기를 눌러주세요.');return;}
  $('camera').value=iphone.deviceId;
  if(stream?.getVideoTracks()[0]?.getSettings().deviceId!==iphone.deviceId)await connect(iphone.deviceId);
  else message(`이미 iPhone에 연결되어 있습니다: ${stream.getVideoTracks()[0].label}`);
}
async function loadTracker(){
  if(tracker||trackerLoading)return;
  const request=++trackerRequest;trackerLoading=true;trackerError=null;$('retryTracker').disabled=true;
  setStatusText('trackerState','모델 로딩 중 · 영상은 계속 표시됩니다.');
  let timer;
  const job=(async()=>{
      const {FilesetResolver,HandLandmarker}=await import('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/+esm');
      const vision=await FilesetResolver.forVisionTasks('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm');
      const options={baseOptions:{modelAssetPath:'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task',delegate:'GPU'},runningMode:'VIDEO',numHands:2};
      try{return await HandLandmarker.createFromOptions(vision,options);}
      catch{options.baseOptions.delegate='CPU';return await HandLandmarker.createFromOptions(vision,options);}
  })();
  job.then(value=>{if(request!==trackerRequest)value.close();},()=>{});
  try{
    tracker=await Promise.race([job,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('모델 로딩 시간 초과')),25000);})]);
    setStatusText('trackerState','준비 완료 · 손을 보여주세요.');
  }catch(error){trackerRequest++;trackerError=error;tracker=null;setStatusText('trackerState',`사용 불가 (${error.message}) · 영상 연결은 유지됩니다.`);}
  finally{clearTimeout(timer);trackerLoading=false;$('retryTracker').disabled=false;}
}
function updateVideoStatus(t){
  // Refresh text at 2 Hz, independently of camera/inference frame rate.
  if(t-lastStatusRender<500)return;lastStatusRender=t;
  if(t-statsAt>=500){previewFps=(frameCount-statsCount)*1000/(t-statsAt);statsAt=t;statsCount=frameCount;}
  const track=stream?.getVideoTracks()[0];
  const live=frameCount>0&&!track?.muted&&t-lastFrameAt<2500;
  $('cameraStatus').dataset.state=live?'live':'waiting';
  setStatusText('cameraState',live?'● 영상 수신 중':document.hidden?'백그라운드 · 영상 상태 확인 대기':'카메라 열림 · 새 프레임 대기');
  setStatusText('videoState',frameCount?`${video.videoWidth}×${video.videoHeight} · ${previewFps.toFixed(0)} FPS · 누적 ${frameCount.toLocaleString('ko-KR')}프레임${live?'':' · 영상이 멈췄다면 iPhone을 확인해주세요.'}`:'대기 · 검은 화면이 계속되면 iPhone과 다른 카메라 사용 앱을 확인해주세요.');
}
function applyOrientation(){
  const transform=`scale(${$('mirror').checked?-1:1},${$('flip').checked?-1:1})`;
  video.style.transform=transform;canvas.style.transform=transform;
  for(const [input,button,label] of [['mirror','mirrorToggle','↔ 좌우 반전'],['flip','flipToggle','↕ 상하 반전']]){
    $(button).setAttribute('aria-pressed',String($(input).checked));
    $(button).textContent=`${label} · ${$(input).checked?'켜짐':'꺼짐'}`;
  }
}
function collect(kind){
  if(!stream)return message('먼저 카메라를 연결하세요.');
  if(!tracker)return message('영상은 연결되어 있습니다. 손 추적이 준비된 후 보정하세요. 필요하면 손 추적 다시 시도를 눌러주세요.');
  if(busy||trial)return message('현재 기록이 끝난 뒤 보정하세요.');
  if(kind==='lift'&&!restFrames.length)return message('기준 자세부터 저장하세요.');
  busy=true;calibrationEpoch++;capture=new RawCalibrationCapture({kind,finger:$('finger').value,start:performance.now()});
  capture.hand_side=$('handSide').value;lockCapture(true);lastCaptureUI=0;
  message(kind==='rest'?'준비 시간에 손가락을 편하게 내려놓으세요. 수집이 시작되면 자세를 유지하세요.':'준비 시간에 선택 손가락만 들어 올리세요. 반복 탭하지 말고 들린 자세를 유지하세요.');
  updateCapture(performance.now());
}
function lockCapture(locked){for(const id of ['rest','lift','handSide','finger','reset','record','recordQuick','tapMode','importCaptureModel'])$(id).disabled=locked;$('cancelCalibration').disabled=!locked;}
function cancelCapture(){calibrationEpoch++;capture=null;busy=false;lockCapture(false);$('calibrationProgress').textContent='보정을 취소했습니다. 기존에 저장한 기준·보정은 유지됩니다.';}
function updateCapture(t){
  if(!capture)return;const s=capture.status(t);
  if(s.phase==='complete'||s.phase==='timeout'){void finishCapture(s);return;}
  if(t-lastCaptureUI<100)return;lastCaptureUI=t;
  $('calibrationProgress').dataset.state='waiting';
  $('calibrationProgress').textContent=s.phase==='preparing'?`준비 ${(s.prepare_remaining_ms/1000).toFixed(1)}초 · ${capture.kind==='rest'?'손가락을 내려놓기':names[capture.finger]+'만 들고 유지하기'}`:
    `수집 중 · ${s.frames}/${s.min_frames}프레임 이상 · 유지 ${(Math.min(s.span_ms,1600)/1000).toFixed(1)}/1.6초 · 추적 누락 ${s.rejected}회 · 유효성은 Python에서 검사`;
}
async function finishCapture(state){
  const c=capture;if(!c)return;capture=null;const token=calibrationEpoch;
  const attempt={kind:c.kind,finger:c.finger,hand_side:c.hand_side,created_at:new Date().toISOString(),capture:state,frames:c.frames,rest_frames:c.kind==='lift'?restFrames:undefined};
  try{
    if(state.phase==='timeout')throw Error(`연속 관측 부족 · ${state.frames}프레임. 선택한 손을 화면에 유지하세요.`);
    $('calibrationProgress').textContent='Python에서 길이·TIP·관절 특징을 검사 중입니다.';
    const result=c.kind==='rest'?await tapAPI('reference',{frames:c.frames,finger:c.finger}):await tapAPI('calibrate',{rest_frames:restFrames,lift_frames:c.frames,finger:c.finger});
    if(token!==calibrationEpoch)return;
    attempt.diagnostics=result.diagnostics;attempt.diagnostic_text=result.diagnostic_text;
    if(!result.ok){const e=Error(result.error);e.code=result.code;throw e;}
    if(c.kind==='rest'){
      models={};await resetPython();if(token!==calibrationEpoch)return;restFrames=c.frames;total=0;$('count').textContent='0';$('calibrated').textContent='보정된 손가락: 없음';
      message('Python 기준 저장 완료. 자연스러운 연주 자세에서 선택 손가락을 들어 유지하세요.');
    }else{
      const next={...models,[c.finger]:result.model};if(!await resetPython(next)||token!==calibrationEpoch)return;models=next;
      $('calibrated').textContent=`보정된 손가락: ${Object.keys(models).map(f=>names[f]).join(', ')}`;
      message('Python 보정 완료. TIP의 들림·복귀와 관절 변화가 함께 관측되면 후보를 표시합니다.');
    }
    attempt.success=true;$('calibrationProgress').textContent=c.kind==='rest'?'Python 기준 저장 완료':'Python TIP + 관절 보정 완료';$('calibrationProgress').dataset.state='live';
  }catch(error){
    if(token!==calibrationEpoch)return;attempt.success=false;attempt.error={code:error.code||'python_calibration_error',message:error.message};
    message(`보정 보류: ${error.message}`);$('calibrationProgress').textContent=`보정 보류 · ${error.message}`;$('calibrationProgress').dataset.state='waiting';$('calibrationDetails').open=true;
  }finally{if(token===calibrationEpoch){busy=false;lockCapture(false);}}
  if(token!==calibrationEpoch)return;
  $('calibrationDiagnostics').textContent=`${attempt.success?'저장 완료':attempt.error.code+' · '+attempt.error.message}\n${attempt.diagnostic_text||''}\n수집 ${state.frames}프레임 · 추적 누락 ${state.rejected}회`;
  calibrationAttempts.push(attempt);calibrationAttempts=calibrationAttempts.slice(-10);
}
function dorsalPatch(points){
  // Palm-anchored affine crop removes planar translation/rotation/scale only.
  if([0,5,17].some(i=>!points[i]||points[i].x<.02||points[i].x>.98||points[i].y<.02||points[i].y>.98))return null;
  const p=[points[0],points[5],points[17]].map(q=>[q.x*video.videoWidth,q.y*video.videoHeight]);
  const [o,a,b]=p,ux=a[0]-o[0],uy=a[1]-o[1],vx=b[0]-o[0],vy=b[1]-o[1],det=ux*vy-vx*uy;
  if(Math.abs(det)<20)return null;
  const c=document.createElement('canvas');c.width=c.height=48;const k=c.getContext('2d');
  const m00=(-16*vy-16*uy)/det,m01=(16*vx+16*ux)/det;
  const m10=(-36*vy+36*uy)/det,m11=(36*vx-36*ux)/det;
  k.setTransform(m00,m10,m01,m11,24-m00*o[0]-m01*o[1],44-m10*o[0]-m11*o[1]);
  k.drawImage(video,0,0);return c.toDataURL('image/jpeg',.8);
}
function loop(){
  if(!stream)return;
  const t=performance.now();
  const decoded=video.getVideoPlaybackQuality?.().totalVideoFrames;
  const frameKey=decoded>0?decoded:video.currentTime;
  if(video.readyState>=2&&video.videoWidth>0&&frameKey!==lastVideo){
    lastVideo=frameKey;lastFrameAt=t;frameCount++;$('cameraPlaceholder').hidden=true;
    if(canvas.width!==video.videoWidth||canvas.height!==video.videoHeight){canvas.width=video.videoWidth;canvas.height=video.videoHeight;}
    ctx.clearRect(0,0,canvas.width,canvas.height);
    try{
      let result={handedness:[],worldLandmarks:[],landmarks:[]};
      if(tracker){
        try{result=tracker.detectForVideo(video,t);}
        catch(error){const failed=tracker;tracker=null;trackerError=error;try{failed.close();}catch{}setStatusText('trackerState',`처리 실패 (${error.message}) · 영상은 유지됩니다. 다시 시도하세요.`);}
      }
      const {world,points,tracking}=selectHandInput(result,$('handSide').value,{ready:!!tracker,error:trackerError});
      if(!capture&&!busy&&!trial?.stopping)pythonClient.offer({t_ms:t,world_landmarks:world,landmarks:points,tracking},trial&&!trial.stopping?trial:null);
      if(points)skinObserver.offer(()=>dorsalPatch(points),t,trial&&!trial.stopping?trial:null);
      else if(skinObserver.enabled){skinObserver.reset();$('skinSignals').textContent='선택한 손이 보이지 않습니다. 손등을 보여주세요.';}
      if(points)for(const ids of Object.values(FINGERS)){
        ctx.beginPath();ids.forEach((id,j)=>{const q=points[id];j?ctx.lineTo(q.x*canvas.width,q.y*canvas.height):ctx.moveTo(q.x*canvas.width,q.y*canvas.height);});
        ctx.strokeStyle='#a4e2d0';ctx.lineWidth=3;ctx.stroke();
      }
      if(points){
        const finger=capture?.finger||$('finger').value,p=points[FINGERS[finger][3]],x=p.x*canvas.width,y=p.y*canvas.height;
        ctx.beginPath();ctx.arc(x,y,9,0,Math.PI*2);ctx.strokeStyle='#ffd097';ctx.lineWidth=4;ctx.stroke();
      }
      if(tracker&&t-lastTrackerRender>=250){lastTrackerRender=t;setStatusText('trackerState',points?`${$('handSide').value==='Right'?'오른손':'왼손'} 감지 · ${result.landmarks.length}개 손 관측`:
        `준비 완료 · ${$('handSide').value==='Right'?'오른손':'왼손'}이 보이지 않습니다. 반대 손이면 추적할 손을 바꾸세요.`);}
      if(capture)capture.observe(t,world,points);
      if(capture)$('signals').textContent='Python 보정 입력 수집 중';
      if(trial&&!trial.stopping){
        const relative=t-trial.started_ms;
        trial.frames.push({t_ms:relative,world_landmarks:world,landmarks:points,tracking,cue_phase:recordingCue(relative).phase,skin_patch:trial.capture_skin&&points?dorsalPatch(points):null});
        message(`${recordingCue(relative).text} · 후보 ${trial.predictions.length}회`);
        if(relative>=RECORDING_PROTOCOL.total_ms)stopTrial();
      }
    }catch(error){if(trial){trial.processing_errors.push({t_ms:t-trial.started_ms,error:error.message});trial.frames.push({t_ms:t-trial.started_ms,world_landmarks:null,skin_patch:null});}message(`프레임 처리 보류: ${error.message}`);}
  }
  // Preparation/progress and timeout also advance if video frames stop.
  updateCapture(t);
  if(trial&&t-trial.started_ms>=RECORDING_PROTOCOL.total_ms+500)stopTrial();
  updateVideoStatus(t);
  raf=requestAnimationFrame(loop);
}
async function startTrial(){
  if(!stream||!tracker)return message('먼저 카메라를 연결하세요.');
  if(!Object.keys(models).length&&!skinObserver.enabled)return message('Python에서 최소 한 손가락을 보정하거나 손등 관찰을 켜세요.');
  if(busy||trial)return;
  busy=true;lockCapture(true);const token=calibrationEpoch;
  try{if(!await restartDetector()||token!==calibrationEpoch)return;skinObserver.reset();trial={person_id:$('person').value,session_id:$('session').value,scenario:$('scenario').value,selected_finger:$('finger').value,hand_side:$('handSide').value,
    started_ms:performance.now(),duration_ms:null,recording_protocol:{...RECORDING_PROTOCOL},practice_window_ms:{start:RECORDING_PROTOCOL.prepare_ms,end:RECORDING_PROTOCOL.prepare_ms+RECORDING_PROTOCOL.play_ms},capture_skin:$('skin').checked,live_skin:skinObserver.enabled,skin_observations:[],calibration:structuredClone(models),algorithm:'python_tip_angles_v1',engine_revision:pythonClient.engineRevision,detector_mode:$('tapMode').value,frames:[],predictions:[],processing_errors:[],truth:null};
  }catch(error){message(`Python 기록 준비 실패: ${error.message}`);}
  finally{if(token===calibrationEpoch){busy=false;lockCapture(false);$('record').disabled=!!trial;$('recordQuick').disabled=!!trial;}}
}
async function stopTrial(){
  if(!trial||trial.stopping)return;const active=trial;active.stopping=true;active.duration_ms=performance.now()-active.started_ms;
  try{await pythonClient.drain();}catch(error){active.processing_errors.push({error:error.message});}
  if(trial!==active)return;active.dropped_transport_frames=pythonClient.dropped;trials.push(active);trial=null;$('record').disabled=false;$('recordQuick').disabled=false;$('trials').textContent=`저장한 시행: ${trials.length}`;message('연주 기록을 완료했습니다. 준비·연주·마무리 좌표를 모두 보존했습니다. JSON을 저장하세요.');
}
async function exportData(){
  if(trial){await stopTrial();while(trial)await new Promise(r=>setTimeout(r,20));}if(!trials.length)return message('저장할 시행이 없습니다.');
  const payload={schema_version:2,kind:'pthoven_python_tap_capture',evidence_type:'real_landmark_capture',created_at:new Date().toISOString(),engine:'python',algorithm:'python_tip_angles_v1',contact_verified:false,trials};
  const url=URL.createObjectURL(new Blob([JSON.stringify(payload)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='pthoven-python-tap-capture.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function drawTapTrend(){
  const c=$('tapTrend'),k=c.getContext('2d'),rows=liveTrace.rows.slice(-240),finger=$('finger').value;
  k.clearRect(0,0,c.width,c.height);k.fillStyle='#090d15';k.fillRect(0,0,c.width,c.height);
  if(!rows.length)return;
  const y=v=>c.height-12-(Math.max(-.5,Math.min(1.5,v))+.5)*(c.height-28)/2;
  const last=rows.at(-1).observed.signals[finger],o=last?.decision?.thresholds;
  if(o)for(const [v,label] of [[o.lift,'TIP 들림'],[o.release,'복귀'],[o.angle_lift,'관절 들림']]){k.strokeStyle='#53617a';k.setLineDash([4,4]);k.beginPath();k.moveTo(0,y(v));k.lineTo(c.width,y(v));k.stroke();}
  k.setLineDash([]);if(o){k.fillStyle='#aab7cb';k.font='11px system-ui';k.fillText(formatThresholds(o),8,12);}
  for(const [group,color] of [['tip','#a4e2d0'],['angles','#ffd097']]){
    k.strokeStyle=color;k.lineWidth=2;k.beginPath();let connected=false;
    rows.forEach((r,i)=>{const signal=r.observed.signals[finger],v=signal?.relative_scores?.[group]??signal?.[group]?.score;if(!signal?.valid||!Number.isFinite(v)){connected=false;return;}const x=i*c.width/Math.max(1,rows.length-1);if(connected)k.lineTo(x,y(v));else k.moveTo(x,y(v));connected=true;});k.stroke();
  }
}
$('tapMode').onchange=async()=>{
  if(capture||busy||trial){$('tapMode').value=activeMode;return message('보정·기록 종료 후 판정 방식을 바꾸세요.');}
  try{if(!await restartDetector())return;message(`${TAP_MODE_LABELS[activeMode]}로 변경했습니다. 안정된 자세로 잠깐 기다린 후 들었다 내려보세요.`);}
  catch(error){$('tapMode').value=activeMode;showTapMode();message(error.message);}
};
$('importCaptureModel').onchange=async event=>{
  try{
    if(capture||busy||trial)throw Error('보정·기록 종료 후 불러오세요.');
    const file=event.target.files[0];if(!file)return;if(file.size>20_000_000)throw Error('20MB 이하 기록을 선택하세요.');
    const data=JSON.parse(await file.text()),source=data.trials?.find(t=>t.calibration&&Object.keys(t.calibration).length);
    if(!source)throw Error('보정 모델을 포함한 연주 기록이 아닙니다.');
    if(!await resetPython(source.calibration))return;
    calibrationEpoch++;models=source.calibration;restFrames=[];total=0;$('count').textContent='0';
    if(['Left','Right'].includes(source.hand_side))$('handSide').value=source.hand_side;
    if(source.selected_finger&&FINGERS[source.selected_finger])$('finger').value=source.selected_finger;
    $('calibrated').textContent=`보정된 손가락: ${Object.keys(models).map(f=>names[f]||f).join(', ')} · 저장 기록에서 복원`;
    message(`기록의 보정을 불러왔습니다. 현재 판정 방식: ${TAP_MODE_LABELS[activeMode]}. 촬영 배치가 달라졌으면 다시 보정하세요.`);
  }catch(error){message(`보정 불러오기 실패: ${error.message}`);}
};
$('exportRecent').onclick=async()=>{
  try{await pythonClient.drain();const payload=liveTrace.export(models);for(const row of payload.trials){row.displayed_cumulative_candidates=total;row.hand_side=$('handSide').value;row.selected_finger=$('finger').value;row.engine_revision=pythonClient.engineRevision;row.detector_mode=$('tapMode').value;row.dropped_transport_frames=pythonClient.dropped;}const url=URL.createObjectURL(new Blob([JSON.stringify(payload)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='pthoven-live-tap-diagnostics.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);message(payload.trials.length>1?'최근 구간과 마지막 손 관측 구간을 함께 저장했습니다. 각 구간은 JSON의 trials에 따로 들어 있습니다.':payload.trials[0].input_summary.landmark_frames?'최근 10초 원본 좌표·Python 판정을 저장했습니다.':'저장한 최근 10초에 손 좌표가 없습니다. 추적 상태를 확인하고 10초 고정 기록을 사용하세요.');}
  catch(error){message(error.message);}
};
$('connect').onclick=()=>connect();$('disconnect').onclick=()=>{stopCamera();message('카메라 연결을 종료했습니다.');};
$('camera').onchange=()=>connect();$('refreshCameras').onclick=refreshCameraList;$('useIPhone').onclick=connectIPhone;$('retryTracker').onclick=()=>loadTracker();
$('mirror').onchange=applyOrientation;$('flip').onchange=applyOrientation;
$('mirrorToggle').onclick=()=>{$('mirror').checked=!$('mirror').checked;applyOrientation();};
$('flipToggle').onclick=()=>{$('flip').checked=!$('flip').checked;applyOrientation();};
$('rest').onclick=()=>collect('rest');$('lift').onclick=()=>collect('lift');$('reset').onclick=reset;
$('cancelCalibration').onclick=()=>{cancelCapture();message('보정을 취소했습니다.');};
$('exportCalibration').onclick=()=>{
  if(!calibrationAttempts.length)return message('먼저 보정을 시도하세요. 성공·실패 진단을 저장할 수 있습니다.');
  const payload={schema_version:1,kind:'pthoven_calibration_diagnostics',created_at:new Date().toISOString(),algorithm:'python_tip_angles_v1',contact_verified:false,attempts:calibrationAttempts};
  const url=URL.createObjectURL(new Blob([JSON.stringify(payload)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='pthoven-calibration-diagnostics.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};
$('importCalibration').onchange=async event=>{
  try{
    if(capture||trial)throw Error('보정·기록 종료 후 진단 파일을 다시 분석하세요.');
    const file=event.target.files[0];if(!file)return;if(file.size>20_000_000)throw Error('20MB 이하 진단 JSON을 선택하세요.');
    const data=JSON.parse(await file.text());
    if(data.kind!=='pthoven_calibration_diagnostics'||!Array.isArray(data.attempts))throw Error('보정 진단 JSON이 아닙니다.');
    const source=[...data.attempts].reverse().find(a=>FINGERS[a.finger]&&Array.isArray(a.rest_frames)&&Array.isArray(a.frames)&&a.rest_frames.length>=5&&a.frames.length>=5);
    if(!source)throw Error('기준·들림 랜드마크가 함께 있는 보정 기록이 없습니다.');
    const attempt={kind:'offline_reanalysis',finger:source.finger,source_algorithm:data.algorithm,created_at:new Date().toISOString(),frames:source.frames,rest_frames:source.rest_frames};
    const result=await tapAPI('calibrate',{rest_frames:source.rest_frames,lift_frames:source.frames,finger:source.finger});
    attempt.success=result.ok;attempt.diagnostics=result.diagnostics;attempt.diagnostic_text=result.diagnostic_text;
    if(!result.ok)attempt.error={code:result.code,message:result.error};
    calibrationAttempts.push(attempt);calibrationAttempts=calibrationAttempts.slice(-10);
    $('calibrationDetails').open=true;$('calibrationDiagnostics').textContent=`저장 기록 재분석 · ${attempt.success?'Python 품질 조건 통과':attempt.error.code+' · '+attempt.error.message}\n${attempt.diagnostic_text||''}\n현재 카메라의 보정은 변경하지 않았습니다.`;
    message('저장한 기록을 Python에서 재분석했습니다. 현재 카메라의 보정은 변경하지 않았습니다.');
  }catch(error){message(`진단 재분석 보류: ${error.message}`);}
};
$('recordQuick').onclick=startTrial;$('exportTrial').onclick=exportData;
$('handSide').onchange=reset;$('record').onclick=startTrial;$('stop').onclick=stopTrial;$('export').onclick=exportData;
$('liveSkin').onchange=()=>{if(trial)stopTrial();skinObserver.enable($('liveSkin').checked);skinHistory=[];drawSkinTrend();$('skinPreview').hidden=true;$('skinSignals').textContent=skinObserver.enabled?'선택한 손의 손등을 카메라에 보여주세요.':'관찰 꺼짐';};
window.addEventListener('beforeunload',stopCamera);
navigator.mediaDevices?.addEventListener?.('devicechange',()=>void refreshCameraList());
applyOrientation();void refreshCameraList();
drawSkinTrend();
if(new URLSearchParams(location.search).get('mode')==='skin'){$('liveSkin').checked=true;$('liveSkin').onchange();$('skinCard').scrollIntoView({block:'start'});}

void tapAPI('status').then(d=>{$('pythonState').textContent=`Python 엔진 준비 · ${d.engine_revision??d.algorithm}`;}).catch(e=>{$('pythonState').textContent=`Python 엔진 연결 필요: ${e.message}`;});
