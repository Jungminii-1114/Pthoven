// Experimental pose-event detector. Scores are not distances or contact probabilities.
export const FINGERS = {thumb:[1,2,3,4], index:[5,6,7,8], middle:[9,10,11,12], ring:[13,14,15,16], pinky:[17,18,19,20]};
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
export function features(landmarks) {
  if(!Array.isArray(landmarks)||landmarks.length!==21) throw Error('21 world landmarks required');
  const p=landmarks.map(q=>Array.isArray(q)?q:[q.x,q.y,q.z]);
  if(p.some(q=>q.length!==3||q.some(v=>!Number.isFinite(v)))) throw Error('Non-finite landmark');
  const width=norm(sub(p[5],p[17])),x=unit(sub(p[5],p[17]));
  const along=sub(p[9],p[0]);
  const y=unit(sub(along,x.map(v=>v*dot(along,x)))),z=unit(cross(x,y));
  const local=p.map(q=>[x,y,z].map(axis=>dot(sub(q,p[0]),axis)/width));
  const result={};
  for(const [finger,ids] of Object.entries(FINGERS)) {
    const [a,b,c,d]=ids.map(i=>local[i]);
    result[finger]={
      vector:[d[2]-a[2], angle(local[0],a,b)/Math.PI, angle(a,b,c)/Math.PI, angle(b,c,d)/Math.PI, norm(sub(d,a))],
      lengths:[norm(sub(b,a)),norm(sub(c,b)),norm(sub(d,c))],
    };
  }
  return result;
}

export function calibrate(restFrames,liftFrames,finger) {
  if(!FINGERS[finger]||restFrames.length<5||liftFrames.length<5) throw Error('At least 5 valid rest and lift frames required');
  const rest=restFrames.map(p=>features(p)[finger]),lift=liftFrames.map(p=>features(p)[finger]);
  const base=mean(rest.map(f=>f.vector)),raised=mean(lift.map(f=>f.vector)),delta=sub(raised,base);
  if(norm(delta)<0.06) throw Error('Rest and lift too similar: raise selected finger visibly and recapture');
  const jitter=Math.sqrt(rest.reduce((s,f)=>s+dot(sub(f.vector,base),sub(f.vector,base)),0)/rest.length);
  if(jitter>norm(delta)*0.3) throw Error('Rest capture unstable: hold palm steady and recapture');
  return {base,delta,lengths:mean(rest.map(f=>f.lengths)),jitter,signal:norm(delta)};
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
    let frame;try{frame=features(landmarks);}catch{frame=null;}
    const events=[],signals={};
    for(const [finger,model] of Object.entries(this.models)) {
      const f=frame?.[finger];
      const lengthError=f?Math.max(...f.lengths.map((v,i)=>Math.abs(v/model.lengths[i]-1))):Infinity;
      const valid=!!f&&lengthError<=0.30;
      const score=valid?dot(sub(f.vector,model.base),model.delta)/dot(model.delta,model.delta):null;
      const residual=valid?norm(sub(sub(f.vector,model.base),model.delta.map(v=>v*score)))/model.signal:null;
      const accepted=valid&&residual<=0.8&&score>=-0.5&&score<=2;
      const event=this.machines[finger].update(t,score,accepted);
      signals[finger]={score,valid:accepted,length_error:Number.isFinite(lengthError)?lengthError:null,residual,phase:this.machines[finger].phase};
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
