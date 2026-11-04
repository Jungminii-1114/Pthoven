# 업무 데이터·좌표·시간·근거 계약

[계약 목록](README.md) · [명령 적용](command-protocol.md)

## 1. 범위와 식별자

| 필드 | 정의 |
| --- | --- |
| session_id | 한 사용자의 연속된 체험 범위 |
| attempt_id | 고정된 실제 설정으로 수행한 한 번의 연습 시도 |
| exercise_ref | 곡·편곡·음표 정의 버전 |
| event_id / frame_id | 입력 사건·프레임 식별자 |
| snapshot_id / state_version | 코칭 경계에서 고정된 불변 근거와 순번 |
| config_version | 확인된 실제 연습 설정 버전 |
| calibration_version | 자세 기준 버전 |
| geometry_version | 영상→화면→가상 건반 좌표 정의 버전 |
| command_epoch | 재연결·수동 제어·즉시 보류 등으로 오래된 명령을 차단하는 제어 세대 |
| clock_id | 시각이 속한 단조 시계의 범위 |
| evidence_ref | 접근 범위·유형·버전·위치를 해석할 수 있는 불변 근거 참조 |

ID는 데이터가 무엇인지 연결하는 용도다. 모델이 만든 임의의 ID를 검증 없이 허용하지 않는다. session_id와 epoch 등 실행 메타데이터는 하네스가 부여한다.

## 2. 세부 단계의 산출물

| 타입 | 필수 의미·필드 | 계약 |
| --- | --- | --- |
| SessionContext | session_id, input_source, exercise_ref, clock_id, lifecycle_phase, config_version, command_epoch, device_state | [S-01](../03-small-nodes/S-01.md) |
| ObservationBatch | frame_id, t_ms, clock_id, hands[], image_xy, world_landmarks, hand_association, diagnostics | [S-02](../03-small-nodes/S-02.md) |
| CalibrationBundle | calibration_version, geometry_version, per_finger_templates, transform_ref, key_regions, validity_conditions | [S-03](../03-small-nodes/S-03.md) |
| QualityAssessment | quality_id, quality_state, usable_intervals, unknown_intervals, denominators, reason_codes, policy_version | [S-04](../03-small-nodes/S-04.md) |
| TapCandidateBatch | candidates[], updated_cycle_state, withheld_reasons; 후보별 t_ms, emitted_ms, finger, contact_verified=false | [S-05](../03-small-nodes/S-05.md) |
| VirtualInputBatch | events[], source, event_id, t_ms, emitted_ms, virtual_key, midi_note, input_status, quality_flags | [S-06](../03-small-nodes/S-06.md) |
| AlignmentResult | alignment_id, pairs[], matched/wrong/extra/missed/unknown, evaluable_count, excluded_count, timing_samples | [S-07](../03-small-nodes/S-07.md) |
| PracticeSnapshot | snapshot_id, state_version, metrics, denominators, observation_status, actual_config, evidence_refs | [S-08](../03-small-nodes/S-08.md) |
| CoachRequest | request_id, trigger_ref, snapshot_ref, priority, allowed_action_classes, budget_ref | [S-09](../03-small-nodes/S-09.md) |
| CoachContext | context_id, snapshot_ref, instruction_version, allowed_tool_ids, evidence_refs, history_refs, remaining_budget | [S-10](../03-small-nodes/S-10.md) |
| CoachPlan | decision_id, snapshot_ref, evidence_refs, action, proposal_refs, rationale, review_criteria, user_message | [S-11](../03-small-nodes/S-11.md) |
| ValidatedPlan | validation_id, plan_ref, change_set, expected_versions, apply_at, expires_at, validation_findings | [S-12](../03-small-nodes/S-12.md) |
| CommandTicket | command_id, decision_id, change_set, expected_versions, command_epoch, apply_at, deadline, lifecycle=queued | [S-13](../03-small-nodes/S-13.md) |
| ApplicationAck | command_id, result, applied_values, actual_versions, applied_at_ms, reason_code, client_clock_id | [S-14](../03-small-nodes/S-14.md) |
| InterventionRecord | intervention_id, command_ref, requested_values, actual_values, lifecycle, effective_attempt_ref, reconciliation_reason | [S-15](../03-small-nodes/S-15.md) |
| ComparabilityReport | comparison_scope, comparable_axes, condition_differences, excluded_axes, caveats, evidence_refs | [S-16](../03-small-nodes/S-16.md) |
| InterventionComparison | metric_deltas, criterion_results, comparison_scope, limitations, next_review_signal, evidence_refs | [S-17](../03-small-nodes/S-17.md) |
| ReportDraft | summary, observations[], uncertainties[], actual_actions[], changes[], next_practice[], claims[], evidence_refs | [S-18](../03-small-nodes/S-18.md) |
| VerifiedReport | report_id, verified_claims, omitted_claims, verification_findings, rendered_sections, evidence_refs, delivery_state | [S-19](../03-small-nodes/S-19.md) |

보조 타입은 SessionStartRequest, SessionPolicy, FramePacket, ExerciseExcerpt, AppliedPracticeConfig, EvidenceBundle, ActionProposal, VisualInspection이다. ExerciseExcerpt에는 기대 음·박자·곡 버전·인덱스·표기·지원 capability를 둔다. EvidenceBundle은 원문을 새로 생성하지 않고 허용된 관측·정렬·지표·개입·비교 참조를 묶는다.

## 3. 시간과 좌표

- 프레임·동작·가상 음은 같은 browser clock_id를 사용한다. 서버 수신 시각은 별도다.
- AttemptContext는 같은 시계의 start_ms·end_ms를 가진다. 화면의 시도 경과 시간은 t_ms-start_ms로 계산하고, 질의·근거의 시각과 섞지 않는다. 내보내기를 시도 상대 시각으로 변환하면 time_origin_ms와 원래 clock_id를 함께 기록한다.
- t_ms는 복귀 후보 시각, emitted_ms는 후보 출력 시각이다. 센서 촬영 시각·물리 접촉 시각과 동일하다고 가정하지 않는다.
- 후보 시각에 대응하는 좌표는 시각별 버퍼에서 해석한다. 좌표가 없으면 음 매핑을 보류한다.
- image_xy_normalized는 원본 프레임 기준의 [x,y]다. 리사이즈·미러·상하 반전·여백을 명시적인 변환으로 처리한다.
- 손바닥 기준 상대 3D와 책상 기준 물리 높이를 구분한다.
- 시간 구간은 [start_ms,end_ms), 음표 인덱스 범위는 [start_index,end_index]로 정의한다.
- 화면의 마디 번호와 내부 음표 인덱스는 별개다.

## 4. 가상 입력 예시

```json
{
  "schema_version": "virtual-input.v1",
  "event_id": "evt-42",
  "session_id": "s01",
  "attempt_id": "a03",
  "clock_id": "browser-s01",
  "t_ms": 1180.0,
  "emitted_ms": 1213.3,
  "source": "camera_pose",
  "finger": "index",
  "image_xy_normalized": [0.43, 0.72],
  "kind": "pose_tap_candidate",
  "contact_verified": false,
  "input_status": "accepted_provisional",
  "virtual_key": "E4",
  "midi_note": 64,
  "calibration_version": 3,
  "geometry_version": 2,
  "quality_flags": []
}
```

보류된 입력은 input_status=withheld, virtual_key=null, midi_note=null과 사유를 반환한다. MIDI 음번호는 가상 음높이 표현이며 물리 MIDI 장치나 실제 음향을 관측했다는 뜻이 아니다. 손가락 이름만으로 midi_note를 정하지 않는다.

## 5. 품질과 지표

QualityAssessment는 quality_id, quality_state, 정책 버전, 분모가 있는 관측 수·시간, usable_intervals와 unknown_intervals를 포함한다. 손 분류 점수·관절 특징·유효 프레임 비율을 접촉 확률로 합치지 않는다.

지표마다 이름, 값, 단위, 산식 버전, 평가 가능한 분모·표본 수, 제외 수, 적용 조건, 근거를 둔다.

- matched_note_ratio = matched_note_count / evaluable_note_count. 분모 0이면 null.
- timing_mae_ms는 정렬·시간 정의가 유효한 표본의 평균 절대 오차다. 표본 수와 오프셋 정책을 함께 기록한다.
- 관측 불가 구간의 미입력은 unknown이다. missed로 자동 집계하지 않는다.
- 음 길이를 정의할 수 없으면 duration_mae_ms=null이다.
- 모든 지표에 input_source를 연결하고 카메라·키보드·리플레이 결과를 혼합하지 않는다.

## 6. 코치 상태 예시

```json
{
  "schema_version": "practice-snapshot.v1",
  "snapshot_id": "snap-a03",
  "state_version": 12,
  "session_id": "s01",
  "attempt_id": "a03",
  "actual_config": {"bpm": 80, "start_index": 0, "end_index": 7, "hint_level": 2, "config_version": 4},
  "calibration_version": 3,
  "geometry_version": 2,
  "command_epoch": 2,
  "observation_status": "usable_provisional",
  "contact_verified": false,
  "metrics": {
    "evaluable_note_count": 8,
    "excluded_note_count": 0,
    "matched_note_count": 6,
    "matched_note_ratio": 0.75,
    "timing_mae_ms": 180.0,
    "timing_sample_count": 6,
    "duration_mae_ms": null
  },
  "evidence_refs": ["align-a03", "quality-a03", "metrics-a03"]
}
```

state_version은 매 프레임 상태 번호가 아니다. 새로운 스냅샷이 생겼다는 이유만으로 모든 실행을 무조건 폐기하지 않고, 곡·시도·실제 설정·보정·epoch·유효 시간·단계의 전제 조건을 검사한다.

## 7. 계획과 근거 계약

CoachPlan은 decision_id, snapshot_ref, action, proposal_refs, evidence_refs, 짧은 observation·hypothesis·reason, review_criteria, user_message를 포함한다. action은 keep_current / observe_more / recalibrate / intervene / pause / resume / finish 중 정책이 허용한 값이다.

ActionProposal은 해당 모델 실행에서 stage한 도구·인자와 snapshot_ref, proposal_id, lifecycle=staged를 가진다. 실제 설정 변경은 없다. 최종 계획이 선택하지 않은 제안은 폐기한다.

숫자·설정·개입 상태 주장은 근거 ID뿐 아니라 값·단위·범위·버전이 일치해야 한다. 관측·가설·추천 문장을 구분한다. 결과를 본 뒤 성공 기준을 유리하게 바꾸지 않도록 review_criteria를 실행 전에 기록한다.

## 8. 근거의 종류와 보존

관측 프레임·관절, 동작 후보, 가상 음, 정렬, 품질 구간, 지표, 실제 설정·ACK, 개입 비교, 보고서 주장 검증을 서로 다른 근거 유형으로 관리한다.

프레임·시퀀스 보관은 선택 정책이다. 참조만 남기고 원본을 보관하지 않는 경우 시각 재검토가 불가능함을 표시한다. 단순 관절 JSON만으로는 독립적인 물리 접촉 라벨을 만들 수 있다고 가정하지 않는다.

실험 근거는 real_capture / annotated_capture / synthetic_fixture / replay를 구분한다. replay는 원본 출처와 라벨 존재 여부를 함께 보존한다. 실제 촬영했다는 사실만으로 contact_verified=true가 되지 않는다.
