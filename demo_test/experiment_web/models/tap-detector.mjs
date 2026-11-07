// Experimental pose-event detector. Scores are not distances or contact probabilities.
export const FINGERS = {thumb:[1,2,3,4], index:[5,6,7,8], middle:[9,10,11,12], ring:[13,14,15,16], pinky:[17,18,19,20]};
export const FEATURE_LABELS=['추정 상대 z','뿌리 각도','중간 각도','끝 관절각','끝점 거리'];
const sub=(a,b)=>a.map((v,i)=>v-b[i]);
const dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0);
const norm=a=>Math.hypot(...a);
const unit=a=>{const n=norm(a); if(n<1e-8) throw Error('Degenerate palm'); return a.map(v=>v/n);};
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const mean=rows=>rows[0].map((_,i)=>rows.reduce((s,r)=>s+r[i],0)/rows.length);
export function angle(a,b,c) {
  const u=sub(a,b),v=sub(c,b),d=norm(u)*norm(v);
  if(d<1e-10) throw Error('Degenerate joint');
  return Math.acos(Math.max(-1,Math.min(1,dot(u,v)/d)));
}
export function features(landmarks, {finger: selected=null}={}) {
  if(!Array.isArray(landmarks)||landmarks.length!==21) throw Error('21 world landmarks required');
  const p=landmarks.map(q=>Array.isArray(q)?q:[q.x,q.y,q.z]);
  if(p.some(q=>q.length!==3||q.some(v=>!Number.isFinite(v)))) throw Error('Non-finite landmark');
  const width=norm(sub(p[5],p[17])),x=unit(sub(p[5],p[17]));
  const along=sub(p[9],p[0]);
  const y=unit(sub(along,x.map(v=>v*dot(along,x)))),z=unit(cross(x,y));
  const local=p.map(q=>[x,y,z].map(axis=>dot(sub(q,p[0]),axis)/width));
  const result={};
  for(const [finger,ids] of Object.entries(FINGERS)) {
    if(selected&&finger!==selected)continue;
    const [a,b,c,d]=ids.map(i=>local[i]);
    result[finger]={
      vector:[d[2]-a[2], angle(local[0],a,b)/Math.PI, angle(a,b,c)/Math.PI, angle(b,c,d)/Math.PI, norm(sub(d,a))],
      lengths:[norm(sub(b,a)),norm(sub(c,b)),norm(sub(d,c))],
    };
  }
  return result;
}

export class CalibrationError extends Error {
  constructor(code,message,diagnostics={}){super(message);this.name='CalibrationError';this.code=code;this.diagnostics=diagnostics;}
}
const median=values=>{const a=[...values].sort((x,y)=>x-y),m=a.length>>1;return a.length%2?a[m]:(a[m-1]+a[m])/2;};

export function summarizeCalibration(frames,finger,phase='rest') {
  if(!FINGERS[finger])throw new CalibrationError('invalid_finger','보정할 손가락을 선택하세요.');
  const rows=[];
  for(const p of frames||[])try{rows.push(features(p,{finger})[finger]);}catch{}
  const stats={input_frames:frames?.length||0,valid_frames:rows.length,rejected_frames:(frames?.length||0)-rows.length};
  const label=phase==='rest'?'기준':'들림';
  if(rows.length<5)throw new CalibrationError(`${phase}_frames_missing`,`${label} 자세의 유효 프레임이 ${rows.length}개입니다. 손 전체와 선택한 손가락이 보이는지 확인하고 다시 저장하세요.`,{[phase]:stats});
  const center=rows[0].vector.map((_,i)=>median(rows.map(f=>f.vector[i])));
  const distances=rows.map(f=>norm(sub(f.vector,center))),mid=median(distances),mad=median(distances.map(v=>Math.abs(v-mid)));
  // Remove isolated tracking spikes, never choose a tiny convenient subset.
  const cutoff=Math.max(.02,mid+3.5*1.4826*mad);
  const kept=rows.filter((_,i)=>distances[i]<=cutoff);
  stats.used_frames=kept.length;stats.outlier_frames=rows.length-kept.length;
  if(kept.length<Math.max(5,Math.ceil(stats.input_frames*.7)))throw new CalibrationError(`${phase}_quality_low`,`${label} 자세에서 추적 불량·급격한 변화가 너무 많습니다. 손가락 끝이 가리지 않게 하고 같은 자세로 다시 저장하세요.`,{[phase]:stats});
  const base=mean(kept.map(f=>f.vector)),jitter=Math.sqrt(kept.reduce((s,f)=>s+dot(sub(f.vector,base),sub(f.vector,base)),0)/kept.length);
  const featureStd=base.map((_,i)=>Math.sqrt(kept.reduce((s,f)=>s+(f.vector[i]-base[i])**2,0)/kept.length));
  return {base,lengths:mean(kept.map(f=>f.lengths)),jitter,samples:kept,stats:{...stats,jitter,feature_std:featureStd}};
}

export function selectReliableFeatures(delta,restStd,liftStd,{noiseFloor=.01,minSnr=2.5}={}){
  if([delta,restStd,liftStd].some(a=>!Array.isArray(a)||a.length!==5||a.some(x=>!Number.isFinite(x)))||restStd.some(x=>x<0)||liftStd.some(x=>x<0)||!(noiseFloor>0)||!(minSnr>0))throw Error('유효한 5차원 특징·변동 값이 필요합니다.');
  const noise=restStd.map((s,i)=>Math.max(s,liftStd[i],noiseFloor)),snr=delta.map((v,i)=>Math.abs(v)/noise[i]);
  const weights=snr.map((v,i)=>v>=minSnr?1/noise[i]**2:0),energy=weights.reduce((s,w,i)=>s+w*delta[i]**2,0);
  return {weights,selected:weights.map(w=>w>0),snr,noise,noise_floor:noiseFloor,min_snr:minSnr,
    normalized_separation:Math.sqrt(energy),contributions:weights.map((w,i)=>energy?w*delta[i]**2/energy:0),
    // Cauchy/triangle bound: does not assume independent feature noise.
    rest_score_noise_bound:energy?weights.reduce((s,w,i)=>s+Math.abs(w*delta[i])*restStd[i],0)/energy:null,
    lift_score_noise_bound:energy?weights.reduce((s,w,i)=>s+Math.abs(w*delta[i])*liftStd[i],0)/energy:null};
}

export function poseFeatureSignal(feature,model){
  if(!feature||!model)return {valid:false,score:null,residual:null,length_error:null};
  const weights=model.feature_weights||[1,1,1,1,1];
  if(weights.length!==5||weights.some(w=>!Number.isFinite(w)||w<0))return {valid:false,score:null,residual:null,length_error:null};
  const energy=weights.reduce((s,w,i)=>s+w*model.delta[i]**2,0);
  if(!(energy>0))return {valid:false,score:null,residual:null,length_error:null};
  const diff=sub(feature.vector,model.base),score=weights.reduce((s,w,i)=>s+w*diff[i]*model.delta[i],0)/energy;
  const residual=Math.sqrt(weights.reduce((s,w,i)=>s+w*(diff[i]-model.delta[i]*score)**2,0)/energy);
  const lengthError=Math.max(...feature.lengths.map((v,i)=>Math.abs(v/model.lengths[i]-1)));
  return {score,residual,length_error:Number.isFinite(lengthError)?lengthError:null,
    valid:Number.isFinite(score)&&Number.isFinite(residual)&&lengthError<=.3&&residual<=.8&&score>=-.5&&score<=2};
}

function scoreAxisNoise(summary,model,phase){
  const scores=[],residuals=[];let observable=0,stateReady=0,lengthRejected=0,residualRejected=0,scoreRejected=0;
  for(const sample of summary.samples){
    const result=poseFeatureSignal(sample,model),{score,residual,valid}=result;
    if(result.length_error===null||result.length_error>.3)lengthRejected++;
    if(residual===null||residual>.8)residualRejected++;
    if(score===null||score<-.5||score>2)scoreRejected++;
    scores.push(score-(phase==='rest'?0:1));residuals.push(residual);
    if(valid){observable++;if(phase==='rest'?score<=.2:score>=.55)stateReady++;}
  }
  return {score_noise:Math.sqrt(scores.reduce((s,v)=>s+v*v,0)/scores.length),
    orthogonal_noise:Math.sqrt(residuals.reduce((s,v)=>s+v*v,0)/residuals.length),
    observable_ratio:observable/scores.length,state_ready_ratio:stateReady/scores.length,
    length_rejected_ratio:lengthRejected/scores.length,residual_rejected_ratio:residualRejected/scores.length,score_rejected_ratio:scoreRejected/scores.length};
}

export function calibrate(restFrames,liftFrames,finger,{method='adaptive'}={}) {
  if(!['adaptive','projection_v3'].includes(method))throw Error('지원하지 않는 보정 방식입니다.');
  const rest=summarizeCalibration(restFrames,finger,'rest'),lift=summarizeCalibration(liftFrames,finger,'lift');
  const base=rest.base,delta=sub(lift.base,base),signal=norm(delta),jitter=rest.jitter;
  const legacyModel={base,delta,lengths:rest.lengths,signal};
  // Keep the minimum geometric separation gate before any whitening/selection.
  if(signal<.06)throw new CalibrationError('signal_too_small','기준과 들림의 관측 차이가 작습니다. 손바닥을 둔 채 선택한 손가락만 들어 유지하세요.',{rest:rest.stats,lift:lift.stats,signal,min_signal:.06,feature_delta:delta});
  const selection=selectReliableFeatures(delta,rest.stats.feature_std,lift.stats.feature_std);
  const active=method==='adaptive'&&selection.selected.some(Boolean);
  const model={...legacyModel,...(active?{feature_weights:selection.weights}:{})};
  const rawAxes={rest:scoreAxisNoise(rest,legacyModel,'rest'),lift:scoreAxisNoise(lift,legacyModel,'lift')};
  const diagnostics={rest:rest.stats,lift:lift.stats,signal,min_signal:.06,max_noise_ratio:.3,
    rest_noise_ratio:jitter/Math.max(signal,1e-9),lift_noise_ratio:lift.jitter/Math.max(signal,1e-9),
    rest_axis:scoreAxisNoise(rest,model,'rest'),lift_axis:scoreAxisNoise(lift,model,'lift'),
    max_orthogonal_noise:.8,min_state_ready_ratio:.7,feature_delta:delta,
    method:method==='adaptive'?'adaptive_feature_quality_v4':'score_axis_quality_v3',
    ...(method==='adaptive'?{feature_selection:selection,min_normalized_separation:3,
      score_space:active?'selected_features':'raw_diagnostic_only',
      baseline_v3:{rest_axis:rawAxes.rest,lift_axis:rawAxes.lift}}:{})};
  // The event score uses projection onto delta. Orthogonal coordinate noise is
  // checked separately using the same residual bound as live inference.
  const problems=[];
  if(method==='adaptive'&&(!active||selection.normalized_separation<3))problems.push({code:'features_unreliable',message:'안정적으로 구분되는 특징이 부족합니다. 선택 가능한 특징과 길이 관측 품질을 진단에서 확인하세요.'});
  for(const [phase,label] of [['rest','기준'],['lift','들림']]){
    const a=diagnostics[`${phase}_axis`];
    if(a.score_noise>.3)problems.push({code:`${phase}_unstable`,message:`${label} 자세의 타건 방향 점수가 불안정합니다 (${(a.score_noise*100).toFixed(1)}%, 허용 30%). 실제 움직임과 추적 좌표 노이즈를 이 수치만으로 구분할 수 없습니다.`});
    else if(a.orthogonal_noise>.8)problems.push({code:`${phase}_off_axis`,message:`${label} 자세의 타건 방향 밖 변동이 큽니다. 손가락 가림과 손바닥 기준 좌표의 안정성을 확인하세요.`});
    else if(a.state_ready_ratio<.7)problems.push({code:`${phase}_overlap`,message:`${label} 자세로 판정 가능한 프레임이 ${(a.state_ready_ratio*100).toFixed(0)}%입니다. 기준과 들림을 안정적으로 구분하지 못해 저장을 보류합니다.`});
  }
  const lengthError=Math.max(...lift.lengths.map((v,i)=>Math.abs(v/rest.lengths[i]-1)));
  diagnostics.length_error=lengthError;
  if(lengthError>.3)problems.push({code:'length_inconsistent',message:'기준과 들림에서 추정 손가락 길이가 크게 달라졌습니다. 손가락 가림·손 위치를 확인하고 기준부터 다시 저장하세요.'});
  diagnostics.problems=problems;
  if(problems.length)throw new CalibrationError(problems[0].code,problems.map(p=>p.message).join(' '),diagnostics);
  return {...model,jitter,quality:diagnostics};
}

export class TapMachine {
  constructor(options={}) {
    this.options={lift:0.55,release:0.2,minLiftMs:40,settleMs:30,cooldownMs:90,maxGapMs:150,maxCycleMs:1800,...options};
    this.reset(); this.ready=false; this.lastTime=-Infinity; this.lastEvent=-Infinity;
  }
  reset(){this.phase='REST';this.liftAt=null;this.settleAt=null;this.minScore=Infinity;this.peak=0;}
  update(t,score,valid=true) {
    if(!Number.isFinite(t)||t<=this.lastTime) throw Error('Timestamps must strictly increase');
    const gap=t-this.lastTime;this.lastTime=t;
    if(!valid||!Number.isFinite(score)||gap>this.options.maxGapMs){this.reset();this.ready=false;return null;}
    const o=this.options;
    if(!this.ready){if(score<=o.release)this.ready=true;return null;}
    if(this.liftAt!==null&&t-this.liftAt>o.maxCycleMs)this.reset();
    if(this.phase==='REST') {
      if(score>=o.lift&&t-this.lastEvent>=o.cooldownMs){this.phase='LIFTED';this.liftAt=t;this.peak=score;}
    } else if(this.phase==='LIFTED') {
      this.peak=Math.max(this.peak,score);
      if(score<=o.release){
        if(t-this.liftAt<o.minLiftMs){this.reset();return null;}
        this.phase='RETURNED';this.settleAt=t;this.minScore=score;
      }
    } else if(this.phase==='RETURNED') {
      if(score>o.release){this.phase='LIFTED';this.settleAt=null;}
      else {
        this.minScore=Math.min(this.minScore,score);
        if(t-this.settleAt>=o.settleMs){
          const event={t_ms:this.settleAt,emitted_ms:t,latency_ms:t-this.settleAt,peak:this.peak,kind:'pose_tap_candidate',contact_verified:false};
          this.lastEvent=t;this.reset();return event;
        }
      }
    }
    return null;
  }
}

export class PoseTapDetector {
  constructor(){this.models={};this.machines={};}
  setCalibration(finger,model){this.models[finger]=model;this.machines[finger]=new TapMachine();}
  resetTracking(){for(const machine of Object.values(this.machines)){machine.reset();machine.ready=false;}}
  update(t,landmarks) {
    const events=[],signals={};
    for(const [finger,model] of Object.entries(this.models)) {
      let f;try{f=features(landmarks,{finger})[finger];}catch{f=null;}
      const signal=poseFeatureSignal(f,model);
      const event=this.machines[finger].update(t,signal.score,signal.valid);
      signals[finger]={...signal,phase:this.machines[finger].phase};
      if(event)events.push({...event,finger});
    }
    return {events,signals};
  }
}

// Maximum-cardinality, minimum-time-error one-to-one alignment, per finger.
export function evaluateEvents(predictions,truth,{toleranceMs=100,durationMs=null}={}) {
  if(!Array.isArray(truth))return {status:'ground_truth_missing',predicted:predictions.length,f1:null};
  let tp=0,totalError=0,fp=0,fn=0;
  for(const finger of Object.keys(FINGERS)) {
    const a=predictions.filter(e=>e.finger===finger).sort((x,y)=>x.t_ms-y.t_ms);
    const b=truth.filter(e=>e.finger===finger).sort((x,y)=>x.t_ms-y.t_ms);
    const dp=Array.from({length:a.length+1},()=>Array.from({length:b.length+1},()=>({count:0,error:0})));
    const best=(x,y)=>x.count>y.count||(x.count===y.count&&x.error<=y.error)?x:y;
    for(let i=1;i<=a.length;i++)for(let j=1;j<=b.length;j++) {
      let v=best(dp[i-1][j],dp[i][j-1]);
      const error=Math.abs(a[i-1].t_ms-b[j-1].t_ms);
      if(error<=toleranceMs){const prior=dp[i-1][j-1];v=best(v,{count:prior.count+1,error:prior.error+error});}
      dp[i][j]=v;
    }
    const m=dp[a.length][b.length];tp+=m.count;totalError+=m.error;fp+=a.length-m.count;fn+=b.length-m.count;
  }
  return {status:'annotated',tp,fp,fn,precision:tp+fp?tp/(tp+fp):null,recall:tp+fn?tp/(tp+fn):null,
    f1:2*tp+fp+fn?2*tp/(2*tp+fp+fn):null,timing_mae_ms:tp?totalError/tp:null,
    false_events_per_minute:durationMs>0?fp/(durationMs/60000):null};
}
