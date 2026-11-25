// Recompute fixed-policy predictions from recorded features; no network or relabelling.
import fs from 'node:fs';
import {DepthComparison,METHODS,evaluateTrial} from '../models/depth-detector.mjs';
const file=process.argv[2];if(!file){console.error('Usage: node scripts/replay_depth.mjs capture.json');process.exit(1);}
const input=JSON.parse(fs.readFileSync(file,'utf8'));
if(input.schema_version!==2||input.kind!=='pthoven_depth_comparison')throw Error('Expected depth comparison schema v2');
const rows=input.trials.map(t=>{
  const engine=new DepthComparison(t.calibration,t.options),predictions=Object.fromEntries(METHODS.map(m=>[m,[]]));
  const pose_frames=[],depth_frames=[];
  for(const f of t.pose_frames||[]){const r=engine.update(f.t_ms,f.world_landmarks,null,f.landmarks,f.emitted_ms??f.t_ms,true);predictions.pose_full.push(...r.events.pose_full);pose_frames.push({...f,signals:r.signals});}
  for(const f of t.depth_frames||[]){const r=engine.update(f.t_ms,f.world_landmarks,f.depth,f.landmarks,f.t_ms+(f.roundtrip_ms||0));for(const m of ['pose_matched','depth','fusion'])predictions[m].push(...r.events[m]);depth_frames.push({...f,signals:r.signals});}
  return {id:t.id,evidence_type:'recorded_feature_replay',original_source:input.evidence_type,model:t.model,options:t.options,
    latency_source:'reconstructed_from_frame_roundtrip_not_new_contact_measurement',predictions,evaluation:evaluateTrial({...t,predictions,pose_frames,depth_frames})};
});
console.log(JSON.stringify({contact_verified:false,trials:rows},null,2));
