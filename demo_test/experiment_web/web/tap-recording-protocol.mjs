// Visual collection cues only. Cue times are not ground-truth tap labels.
export const RECORDING_PROTOCOL=Object.freeze({name:'prepare-play-return-v1',prepare_ms:1000,play_ms:10000,return_ms:1000,total_ms:12000});
export function recordingCue(elapsedMs){
  const p=RECORDING_PROTOCOL;
  if(elapsedMs<p.prepare_ms)return {phase:'prepare',text:`준비 ${((p.prepare_ms-elapsedMs)/1000).toFixed(1)}초 · 손을 편하게 내려놓으세요.`};
  if(elapsedMs<p.prepare_ms+p.play_ms)return {phase:'play',text:`연주 ${((elapsedMs-p.prepare_ms)/1000).toFixed(1)} / 10초`};
  if(elapsedMs<p.total_ms)return {phase:'return',text:`마무리 ${((p.total_ms-elapsedMs)/1000).toFixed(1)}초 · 마지막 손가락을 내려놓고 유지하세요.`};
  return {phase:'complete',text:'기록 완료'};
}
