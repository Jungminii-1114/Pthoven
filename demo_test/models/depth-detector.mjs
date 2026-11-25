import {FINGERS,features,calibrate,TapMachine,evaluateEvents} from './tap-detector.mjs';

export const METHODS=['pose_full','pose_matched','depth','fusion'];
export const DEFAULT_OPTIONS={lift:0.55,release:0.2,minLiftMs:40,settleMs:30,cooldownMs:90,maxGapMs:600,maxCycleMs:1800,poseWeight:0.5};
const mean=a=>a.reduce((s,v)=>s+v,0)/a.length;
const median=a=>{const b=[...a].sort((x,y)=>x-y);return b.length%2?b[b.length>>1]:(b[b.length/2-1]+b[b.length/2])/2;};
const dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0);
const sub=(a,b)=>a.map((v,i)=>v-b[i]);
const finite=v=>typeof v==='number'&&Number.isFinite(v);

export function calibrateDepth(rest,lift,finger){
  const values=rows=>rows.map(r=>r.depth?.fingers?.[finger]).filter(x=>x?.valid&&finite(x.contrast)).map(x=>x.contrast);
  const a=values(rest),b=values(lift);
  if(a.length<5||b.length<5)throw Error('깊이 기준·들림 각각 유효 샘플 5개 이상 필요합니다. 손 밖의 기준점과 추론 속도를 확인하세요.');
  const base=median(a),raised=median(b),delta=raised-base;
  const noise=Math.max(median(a.map(v=>Math.abs(v-base))),median(b.map(v=>Math.abs(v-raised))));
  if(Math.abs(delta)<Math.max(0.035,noise*4))throw Error('깊이 기준과 들림이 충분히 구분되지 않습니다. 이 조건에서 깊이 판정은 보류합니다.');
  return {base,delta,noise,rest_samples:a.length,lift_samples:b.length};
}

export function poseSignal(world,model,finger){
  if(!model)return {valid:false,score:null};
  try{
    const f=features(world)[finger];
    const lengthError=Math.max(...f.lengths.map((v,i)=>Math.abs(v/model.lengths[i]-1)));
    const diff=sub(f.vector,model.base),score=dot(diff,model.delta)/dot(model.delta,model.delta);
    const residual=Math.hypot(...sub(diff,model.delta.map(v=>v*score)))/model.signal;
    return {score,valid:finite(score)&&lengthError<=0.3&&residual<=0.8&&score>=-0.5&&score<=2};
  }catch{return {valid:false,score:null};}
}

export function depthSignal(depth,model,finger){
  const d=depth?.fingers?.[finger];
  if(!model||!d?.valid||!finite(d.contrast))return {valid:false,score:null};
  const score=(d.contrast-model.base)/model.delta;
  return {score,valid:finite(score)&&score>=-0.5&&score<=2};
}

export class DepthComparison{
  constructor(calibration={},options={}){
    this.calibration=structuredClone(calibration);this.options={...DEFAULT_OPTIONS,...options};this.machines={};
    for(const method of METHODS){this.machines[method]={};for(const f of Object.keys(calibration)){
      this.machines[method][f]=new TapMachine({...this.options,maxGapMs:method==='pose_full'?150:this.options.maxGapMs});
    }}
  }
  static calibrate(rest,lift,finger,existing={}){
    const pose=calibrate(rest.filter(r=>r.world).map(r=>r.world),lift.filter(r=>r.world).map(r=>r.world),finger);
    let depth=null,depth_error=null;
    try{depth=calibrateDepth(rest,lift,finger);}catch(error){depth_error=error.message;}
    return {...existing,[finger]:{pose,depth,depth_error}};
  }
  update(t,world,depth,points,emitted=t,full=false){
    const methods=full?['pose_full']:['pose_matched','depth','fusion'],events={},signals={};
    for(const method of methods){events[method]=[];signals[method]={};}
    for(const [finger,c] of Object.entries(this.calibration)){
      const p=poseSignal(world,c.pose,finger),d=depthSignal(depth,c.depth,finger);
      const w=this.options.poseWeight;
      const fusion={valid:p.valid&&d.valid,score:p.valid&&d.valid?w*p.score+(1-w)*d.score:null};
      for(const method of methods){
        const s=method==='depth'?d:method==='fusion'?fusion:p;
        const event=this.machines[method][finger].update(t,s.score,s.valid);
        signals[method][finger]={...s,phase:this.machines[method][finger].phase};
        if(event){const tip=points?.[FINGERS[finger][3]];
          events[method].push({...event,finger,method,emitted_ms:emitted,latency_ms:emitted-event.t_ms,
            tip:tip?[tip.x,tip.y]:null,contact_verified:false});}
      }
    }
    return {events,signals};
  }
}

export function mergeMasks(masks=[],duration){
  const sorted=masks.map(x=>[Math.max(0,x[0]),Math.min(duration,x[1])]).filter(x=>finite(x[0])&&finite(x[1])&&x[1]>x[0]).sort((a,b)=>a[0]-b[0]);
  const out=[];for(const x of sorted){const last=out.at(-1);if(last&&x[0]<=last[1])last[1]=Math.max(last[1],x[1]);else out.push([...x]);}return out;
}

export function evaluateTrial(trial,toleranceMs=100){
  const duration=trial.duration_ms;
  if(!finite(duration)||duration<=0)throw Error('양수 duration_ms가 필요합니다.');
  const masks=mergeMasks(trial.unknown_intervals||[],duration),inside=t=>masks.some(([a,b])=>t>=a&&t<=b);
  const included=e=>finite(e.t_ms)&&e.t_ms>=0&&e.t_ms<=duration&&!inside(e.t_ms);
  const knownDuration=duration-masks.reduce((s,[a,b])=>s+b-a,0);
  const labelled=Array.isArray(trial.truth)&&trial.annotation_confirmed===true;
  const truth=labelled?trial.truth.filter(included):null;
  if(truth?.some(e=>!FINGERS[e.finger]))throw Error('정답 라벨의 손가락 이름을 확인하세요.');
  const out={};
  for(const method of METHODS){
    const raw=trial.predictions?.[method]||[],pred=raw.filter(included);
    const observed=(method==='pose_full'?trial.pose_frames:trial.depth_frames)||[];
    let validMs=0;
    for(let i=1;i<observed.length;i++){
      const a=observed[i-1],b=observed[i],dt=b.t_ms-a.t_ms;
      if(dt>0&&dt<=(method==='pose_full'?150:trial.options?.maxGapMs||600)&&Object.values(a.signals?.[method]||{}).some(s=>s.valid)){
        const start=Math.max(0,a.t_ms),end=Math.min(duration,b.t_ms);
        validMs+=Math.max(0,end-start);
      }
    }
    out[method]={...evaluateEvents(pred,truth,{toleranceMs,durationMs:knownDuration}),
      coverage_ratio:Math.min(1,validMs/duration),excluded_predictions:raw.length-pred.length,
      mean_emit_latency_ms:pred.length?mean(pred.map(e=>e.emitted_ms-e.t_ms)):null};
  }
  return {labelled,known_duration_ms:knownDuration,unknown_intervals:masks,tolerance_ms:toleranceMs,methods:out};
}
