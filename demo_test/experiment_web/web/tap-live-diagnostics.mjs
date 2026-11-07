// Display and raw-input recording only. All thresholds/decisions come from Python.
export function selectHandInput(result,selectedSide,{ready=true,error=null}={}){
  const hands=(result.handedness||[]).map(labels=>({side:labels[0]?.categoryName??'Unknown',score:labels[0]?.score??null}));
  const index=hands.findIndex(h=>h.side===selectedSide);
  const world=index>=0?result.worldLandmarks?.[index]??null:null,points=index>=0?result.landmarks?.[index]??null:null;
  const reason=!ready?(error?'tracker_error':'tracker_loading'):index<0?(hands.length?'selected_hand_not_found':'no_hand_detected'):!world||!points?'selected_landmarks_missing':'ok';
  return {world,points,tracking:{selected_side:selectedSide,selected_index:index,detected_hands:hands,
    detected_hand_count:(result.landmarks||[]).length,reason,tracker_error:error?.message??null}};
}
const hasInput=row=>Array.isArray(row.frame.world_landmarks)&&row.frame.world_landmarks.length===21;
const countCandidates=rows=>rows.reduce((n,r)=>n+r.observed.events.length,0);
function inputSummary(rows){
  const reasons={};let present=0;
  for(const row of rows){if(hasInput(row))present++;const reason=row.frame.tracking?.reason??(hasInput(row)?'ok':'selected_landmarks_missing');reasons[reason]=(reasons[reason]||0)+1;}
  return {frames:rows.length,landmark_frames:present,missing_landmark_frames:rows.length-present,tracking_reason_counts:reasons};
}
export class LiveTapTrace {
  constructor({windowMs=10000,maxFrames=1200,lossTailMs=1000}={}){Object.assign(this,{windowMs,maxFrames,lossTailMs});this.clear();}
  clear(){this.rows=[];this.retainedRows=null;this.lastObservedAt=null;this.previousHadInput=false;}
  get candidateCount(){return countCandidates(this.rows);}
  get summary(){return {current:inputSummary(this.rows),retained:this.retainedRows?inputSummary(this.retainedRows):null,
    last_observed_ms:this.lastObservedAt,retained_age_ms:this.lastObservedAt===null||!this.rows.length?null:this.rows.at(-1).frame.t_ms-this.lastObservedAt};}
  add(frame,observed){
    const row={frame,observed},present=hasInput(row);
    // Freeze the window around the last observed hand, including a short loss tail.
    // Keep the current missing-input window too; never replace or filter failure frames.
    if(!present&&this.previousHadInput)this.retainedRows=this.rows.slice();
    this.rows.push(row);const before=frame.t_ms-this.windowMs;
    while(this.rows.length&&(this.rows[0].frame.t_ms<before||this.rows.length>this.maxFrames))this.rows.shift();
    if(present){this.lastObservedAt=frame.t_ms;this.retainedRows=null;}
    else if(this.lastObservedAt!==null&&frame.t_ms-this.lastObservedAt<=this.lossTailMs)this.retainedRows=this.rows.slice();
    this.previousHadInput=present;
  }
  export(calibration){
    if(!this.rows.length)throw Error('보정 후 손가락을 움직여 Python 판정 기록을 먼저 쌓아주세요.');
    const trial=(rows,window)=>({scenario:'live_debug',capture_window:window,calibration:structuredClone(calibration),truth:null,
      started_ms:rows[0].frame.t_ms,duration_ms:rows.at(-1).frame.t_ms-rows[0].frame.t_ms,
      input_summary:inputSummary(rows),window_candidate_count:countCandidates(rows),frames:rows.map(r=>r.frame),observations:rows.map(r=>r.observed)});
    const trials=[trial(this.rows,'current')];
    if(this.retainedRows?.length&&this.retainedRows.at(-1).frame.t_ms!==this.rows.at(-1).frame.t_ms)
      trials.push(trial(this.retainedRows,'last_hand_observed'));
    return {schema_version:2,kind:'pthoven_python_tap_capture',evidence_type:'real_landmark_capture',
      engine:'python',algorithm:'python_tip_angles_v1',created_at:new Date().toISOString(),contact_verified:false,
      clock:'browser_performance_ms',boundary_note:'최근 구간과 마지막 손 관측 구간을 별도 시행으로 저장합니다. 구간이 겹칠 수 있으며 상태를 복원하는 체크포인트는 아닙니다.',
      last_hand_observed_ms:this.lastObservedAt,trials};
  }
}
export function formatTraceHealth(summary){
  const c=summary.current,r=summary.retained;
  if(!c.frames)return '아직 입력 기록이 없습니다. 카메라를 연결하고 보정한 손을 보여주세요.';
  const current=`최근 10초 손 좌표 ${c.landmark_frames}/${c.frames}프레임`;
  const retained=r?` · 마지막 손 관측 구간 ${r.landmark_frames}/${r.frames}프레임 별도 보존`:'';
  return current+retained+(c.landmark_frames?'':' · 연주 좌표가 있는지 확인 후 저장하세요.');
}
const reasons={target_out_of_frame:'평가 손가락의 화면 이탈',quality_hold_timeout:'짧은 품질 유예 시간 초과',local_bone_distortion:'국소 길이 왜곡',extreme_finger_scale:'손바닥 대비 배율 이상',landmarks_unavailable:'손 좌표 누락',tracking_gap:'관측 공백',
  tip_outside_calibration:'TIP이 보정 궤적 범위를 벗어남',angles_outside_calibration:'관절이 보정 궤적 범위를 벗어남',
  tip_features_missing:'TIP 특징 누락',angles_features_missing:'관절 특징 누락',cycle_timeout:'복귀 시간 초과',
  lift_too_short:'들림 시간이 짧음',insufficient_tip_motion:'TIP 궤적 부족'};
export function formatThresholds(o){
  const n=v=>Number.isFinite(v)?v.toFixed(2):'—';
  return `들림 TIP ${n(o.lift)} / 관절 ${n(o.angle_lift??o.lift)} · 복귀 ${n(o.release)}`;
}
export function formatSignals(signals,names){
  const n=v=>Number.isFinite(v)?v.toFixed(2):'—';
  return Object.entries(signals).map(([f,s])=>{
    const d=s.decision||{},o=d.thresholds||{},m=d.metrics||{},rel=s.relative_scores,local=d.reference?.mode==='local_baseline';
    const tip=local?rel?.tip:s.tip?.score,angle=local?rel?.angles:s.angles?.score;
    const mode=local?(d.reference.state==='recovering'?'상대 기준 · 안정 자세 재확인 중':'동작 직전 자세 대비'):'고정 기준';
    const lines=[`${names[f]||f} · ${s.phase} · ${d.message||'서버 재시작 후 상세 진단 가능'}`,
      `TIP ${n(tip)} / 관절 ${n(angle)} · ${mode}\n들림 TIP ≥${n(o.lift)} / 관절 ≥${n(o.angle_lift??o.lift)} · 복귀 ≤${n(o.release)}`,
      `길이 ${s.geometry?.valid?'통과':'보류'} · 국소 왜곡 ${s.geometry?n(s.geometry.shape_error*100)+'%':'—'} · ${s.valid?'관측 유효':'관측 보류'}`,
      `TIP 이동폭 ${n(m.tip_excursion)} / ${n(o.min_tip_excursion)} · 경로 ${n(m.tip_path)} / ${n(o.min_tip_path)}`];
    if(s.feature_filter)lines.push(`특징 중앙값 ${s.feature_filter.frames}프레임 · 기준 시각 지연 ${s.feature_filter.age_ms.toFixed(0)}ms · 원본 품질 ${s.feature_filter.raw_valid?'유효':'보류'}`);
    if(d.reference)lines.push(`짧은 품질 유예 ${d.reference.held_quality_frames}프레임 · 기준 갱신 ${d.reference.updates}회 · 새 기준 복구 ${d.reference.recoveries??0}회 · 이전 기준 재확인 ${d.reference.reuses??0}회`);
    if(s.image_visibility?.outside_ids?.includes(0))lines.push('화면 밖 손목 좌표는 추정값입니다. 손바닥까지 보이도록 배치하면 더 안정적입니다.');
    if(d.last_interruption)lines.push(`최근 동작 중단: ${reasons[d.last_interruption.code]||d.last_interruption.code}`);
    const resets=Object.entries(d.interruption_counts||{}).map(([r,k])=>`${reasons[r]||r} ${k}회`);
    if(resets.length)lines.push(`누적 동작 중단: ${resets.join(' · ')}`);
    return lines.join('\n');
  }).join('\n\n')||'Python 보정 대기';
}
