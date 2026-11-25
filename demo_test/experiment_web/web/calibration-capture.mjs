import {features} from '../models/tap-detector.mjs';

// Capture preparation is excluded; a long tracking gap restarts the window.
export class CalibrationCapture {
  constructor({kind,finger,start,prepareMs=1500,durationMs=1600,maxSamplingMs=6500,minFrames=12,maxGapMs=500}){
    Object.assign(this,{kind,finger,start,prepareMs,durationMs,maxSamplingMs,minFrames,maxGapMs});
    this.frames=[];this.firstAt=null;this.lastAt=null;this.lastInput=-Infinity;this.rejected=0;this.restarts=0;this.observed=0;
  }
  observe(t,world){
    if(t<this.start+this.prepareMs||t<=this.lastInput)return;
    this.lastInput=t;this.observed++;
    try{features(world,{finger:this.finger});}catch{this.rejected++;return;}
    if(this.lastAt!==null&&t-this.lastAt>this.maxGapMs){this.frames=[];this.firstAt=null;this.restarts++;}
    if(this.firstAt===null)this.firstAt=t;
    this.lastAt=t;this.frames.push(world);
  }
  status(t){
    const preparing=t<this.start+this.prepareMs;
    const span=this.firstAt===null?0:Math.max(0,this.lastAt-this.firstAt);
    const fresh=this.lastAt!==null&&t-this.lastAt<=this.maxGapMs;
    const complete=!preparing&&fresh&&this.frames.length>=this.minFrames&&span>=this.durationMs;
    return {phase:preparing?'preparing':complete?'complete':t>=this.start+this.prepareMs+this.maxSamplingMs?'timeout':'collecting',
      prepare_remaining_ms:Math.max(0,this.start+this.prepareMs-t),frames:this.frames.length,min_frames:this.minFrames,
      observed:this.observed,rejected:this.rejected,restarts:this.restarts,span_ms:span,elapsed_ms:t-this.start};
  }
}
