import {FINGERS} from '../models/tap-detector.mjs';

// Analytic 3D landmark fixtures, NOT MediaPipe output or human recordings.
export function hand(lifts={}) {
  const p=Array.from({length:21},()=>[0,0,0]);p[0]=[0,-1,0];
  const roots={thumb:[-1,-.35,0],index:[-.7,.15,0],middle:[-.25,.3,0],ring:[.2,.22,0],pinky:[.65,.03,0]};
  for(const [finger,ids] of Object.entries(FINGERS)) {
    p[ids[0]]=roots[finger];
    const lift=lifts[finger]||0;
    [.42,.28,.21].forEach((length,j)=>{
      const a=[.1,.2,.3][j]+lift*[.8,.5,.1][j];
      const before=p[ids[j]];
      p[ids[j+1]]=[before[0],before[1]+length*Math.cos(a),before[2]+length*Math.sin(a)];
    });
  }
  return p;
}
export function transform(points,{scale=1,angle=0,shift=[0,0,0]}={}) {
  return points.map(([x,y,z])=>[(x*Math.cos(angle)-y*Math.sin(angle))*scale+shift[0],
    (x*Math.sin(angle)+y*Math.cos(angle))*scale+shift[1],z*scale+shift[2]]);
}
export function cycle(finger='index',start=0) {
  return [0,0,.2,.6,1,1,.7,.3,0,0,0].map((s,i)=>({t_ms:start+i*25,landmarks:hand({[finger]:s})}));
}
