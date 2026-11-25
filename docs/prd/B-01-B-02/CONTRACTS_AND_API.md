# B-01·B-02 데이터·API 계약

[PRD](PRD.md) · [기술 상세](TECHNICAL_SPEC.md) · [시험 계획](ACCEPTANCE_TEST_PLAN.md)

> 아래 타입·경로는 구현할 인터페이스 제안이다. 요청·응답은 strict schema로 검증하고 모든 타입에 schema_version을 둔다. 필수 의미는 기존 [공통 데이터](../../plan/05-contracts/data-models.md)와 동일하다.

## 1. 계약 공통 규칙

- `status`는 success / partial / error, `outcome`은 업무 결과다. `no_hand`·`no_cycle`·`hold_required`는 정상 판단이면 success다.
- null은 정보 없음/사용 불가, 빈 배열은 정상적으로 0건임을 뜻한다. 누락 필수 필드를 0·빈 배열로 자동 보완하지 않는다.
- ms는 유한 float, ID는 비어 있지 않은 문자열, 버전/순번은 0 이상 정수다. bool을 숫자로 허용하지 않는다.
- JSON의 NaN·Infinity, 알 수 없는 enum/주요 추가 필드, 음수 시각, 세션 밖 참조를 거절한다.
- 원본 model 좌표의 범위 이탈은 진단에 보존할 수 있다. 최종 키 매핑 좌표의 범위 검사와 구분한다.
- root 응답은 정확히 `status`, `data`, `diagnostics`, `outcome`이다. request_id·run_id·서버 시간은 바깥 실행 로그와 HTTP/WS envelope에 둔다.

```json
{
  "status": "success",
  "data": {
    "quality_state": "hold_required",
    "reason_codes": ["HAND_NOT_OBSERVED"],
    "contact_verified": false
  },
  "diagnostics": {
    "check_observation_validity": {
      "status": "success",
      "reason": "선택 손이 관측되지 않음",
      "retryable": false
    }
  },
  "outcome": "hold_required"
}
```

진단의 retryable은 동일 요청의 실행 재시도 힌트다. 새 손 관측을 기다리는 것을 동일 no_hand 요청의 무한 재시도로 만들지 않는다.

## 2. 식별자와 시계

| 필드 | 타입 | 발급/소유자와 검증 |
|---|---|---|
| session_id | str | 서버 세션 생성. 다른 세션 자료 참조 금지 |
| clock_id | str | 브라우저 문서 수명의 단조 시계 식별. 세션에 바인딩, 새 문서 재시작 시 변경 |
| attempt_id | str 또는 null | 연습 제어 계층. 보정/입력 점검 중에는 null 가능 |
| exercise_ref | str | 곡/편곡 버전 참조. 기대 음의 자동 대체 근거가 아님 |
| frame_id | int | 브라우저의 세션/epoch 내 증가 순번 |
| observation_id / quality_id | str | 처리 결과와 구간 근거 참조 |
| track_id | str | 연결된 손의 일시적 ID. 손실 후 새 ID, 사람의 신원 아님 |
| candidate_id / event_id | str | 세션·epoch·손·손가락·주기 순번으로 유일 |
| calibration_version | int | 0은 미보정. 유효 발급 시 증가 |
| geometry_version / config_version | int | 표시 기하/실제 연습 설정. 서로 다른 범위 |
| command_epoch | int | 전역 hold·재연결·수동 제어 등 제어 세대 |
| model_ref / policy_version | str | 모델 해시/옵션·정책 선택을 해석하는 불변 참조 |
| request_id / decision_ref | str | 요청 재전송/서버 산출물 게시 확인용 |

`t_ms`, `emitted_ms`, unknown 구간은 browser clock이다. `server_received_at`·`decision_ready_server_ms`는 서버 시계이며 빼서 클라이언트 지연을 계산하지 않는다. `emitted_ms>=t_ms`를 검증한다. 처리 시각을 센서 촬영 시각이라고 부르지 않는다.

## 3. 주요 타입 필드

### 3.1 SessionStartRequest와 SessionContext

| 타입 | 필수 필드·의미 |
|---|---|
| SessionStartRequest | schema_version, input_source, exercise_ref, selected_device_id, selected_hand(left/right), active_fingers[], flip_x, flip_y, clock_id, policy_version |
| SessionContext | schema_version, session_id, input_source, exercise_ref, clock_id, lifecycle_phase, config_version, command_epoch, device_state, selected_hand, active_fingers, model_ref, policy_version |

`selected_device_id`는 camera_pose에서 필수이며 실행 범위에 바인딩한다. 브라우저마다 달라질 수 있어 영구 장치 ID로 간주하지 않는다. active_fingers는 thumb/index/middle/ring/pinky의 중복 없는 비어 있지 않은 부분집합이다. replay는 별도 원본 출처를 필수로 요구한다.

### 3.2 FramePacket과 ObservationBatch

| 타입 | 필드 | 제약 |
|---|---|---|
| FramePacket | frame_id, t_ms, clock_id, session_id, command_epoch, geometry_version | 새 프레임/세션/시계 일치 |
| FramePacket | source_width/height, encoded_width/height, format, byte_length, resize_transform | 양수 크기·허용 압축 포맷·디코딩 후 크기 일치 |
| FramePacket | source_media_time_s, presented_frames | 수집 가능할 때만, browser t_ms와 혼용 금지 |
| ObservationBatch | observation_id, frame_id, t_ms, clock_id, model_ref, geometry_version, command_epoch, hands[], hand_association | 모델 실패와 hands=[] 구분 |
| HandObservation | track_id, handedness, handedness_score, image_xy, world_landmarks, association_state | image_xy는 21×2, world는 21×3 유한 수 |
| HandObservation | coordinate_definition, world_available, missing_reason | SDK 추정 좌표의 정의·존재 여부 |

P0 기본 tracker는 world를 요구한다. 없는 경우 성공한 빈 손으로 변환하지 말고 해당 손을 `WORLD_LANDMARKS_UNAVAILABLE`로 보류한다. P1 2D 전용 어댑터는 별도 feature schema·보정·검증이 필요하다. SDK 미제공 관절별 confidence는 null이다.

### 3.3 DisplayGeometry와 CalibrationBundle

| 타입 | 필드 |
|---|---|
| DisplayGeometry | geometry_version, source_size, inference_to_source, display_rect_css, object_fit, object_position, flip_x/y, device_pixel_ratio, source_to_display_matrix, display_to_key_matrix, key_regions[] |
| KeyRegion | key_id, midi_note(0..127), polygon_css, layer, enabled, boundary_margin_css |
| CalibrationBundle | schema_version, calibration_version, geometry_version, model_ref, feature_schema_version, policy_version, selected_hand, baseline_ref, per_finger_templates, transform_ref, key_regions, validity_conditions |
| FingerTemplate | finger, baseline_ref, feature_space, groups.tip/angles, length_reference[3], palm_reference, sample_refs, diagnostics, created_from_request_id |
| GroupTemplate | feature_names, present_mask, baseline, lift_delta, noise, selected_mask, weights, separation, score_noise, thresholds |

모든 벡터의 길이·순서가 feature_names와 일치해야 한다. weights는 유한 비음수, 그룹 energy는 양수다. 길이/손바닥 폭은 양수다. 템플릿의 finger와 딕셔너리 key가 다르면 거절한다.

### 3.4 QualityAssessment

필수: `quality_id`, `quality_state`, `policy_version`, `clock_id`, session/epoch/버전, `scope`, `usable_intervals`, `unknown_intervals`, `denominators`, `reason_codes`, `contact_verified=false`.

`scope`는 session / hand / finger와 대상 ID를 갖는다. 프레임·손·손가락의 보류 범위를 명확히 한다. 구간 원소는 start_ms, end_ms 또는 null, scope, reasons, evidence_refs를 갖는다.

```json
{
  "window": {"start_ms": 1000.0, "end_ms": 2000.0},
  "denominators": {
    "received_frame_count": 21,
    "processed_frame_count": 18,
    "usable_observation_count": 15,
    "dropped_frame_count": 3,
    "usable_duration_ms": 700.0,
    "unknown_duration_ms": 300.0
  }
}
```

위 예시는 품질 요약의 부분 필드다. duration과 프레임 수의 비율이 같아야 한다는 뜻이 아니다. 관측 분모 0이면 비율은 null과 사유로 남긴다.

### 3.5 TapCandidate와 VirtualInputEvent

| 필드 | TapCandidate | VirtualInputEvent |
|---|---|---|
| identity | candidate_id, track_id, finger, cycle_sequence | event_id, candidate_id, source=camera_pose |
| time | candidate_frame_id, t_ms, clock_id, emitted_ms | 같은 후보 시각·시계, 확정된 emitted_ms |
| coordinate | candidate_image_xy, coordinate_ref | image_xy_normalized, key_space_xy 또는 null |
| versions | session, epoch, calibration, geometry, config, model/policy | 동일한 버전 스냅샷 |
| decision | motion_score_tip/angles, path/excursion, withheld_reasons | input_status, virtual_key, midi_note, quality_flags |
| evidence | observation/quality/baseline refs | candidate/geometry/quality refs |
| physical meaning | kind=pose_tap_candidate, contact_verified=false | 같은 kind와 contact_verified=false |

위 두 타입의 최종 emitted_ms는 브라우저가 확정한다. Python wire 단계는 내부 `CandidateProposal`/`VirtualInputProposal`로 구분하며 emitted_ms는 null이다. 미확정 proposal을 최종 사건 형식으로 B-03에 발행하지 않는다.

최종 수용 사건 예시:

```json
{
  "schema_version": "virtual-input.v1",
  "event_id": "evt-s01-e2-h1-index-7",
  "candidate_id": "tap-s01-e2-h1-index-7",
  "session_id": "s01",
  "attempt_id": "a03",
  "clock_id": "browser-doc01",
  "t_ms": 1180.0,
  "emitted_ms": 1260.0,
  "candidate_frame_id": 42,
  "source": "camera_pose",
  "track_id": "h1",
  "finger": "index",
  "image_xy_normalized": [0.43, 0.72],
  "key_space_xy": [140.0, 70.0],
  "kind": "pose_tap_candidate",
  "contact_verified": false,
  "input_status": "accepted_provisional",
  "virtual_key": "E4",
  "midi_note": 64,
  "calibration_version": 3,
  "geometry_version": 2,
  "config_version": 4,
  "command_epoch": 2,
  "model_ref": "hand-model-sha256-ref",
  "policy_version": "vision-policy.v1",
  "quality_flags": [],
  "evidence_refs": ["obs-42", "quality-18", "geometry-2"]
}
```

withheld 사건은 `virtual_key=null,midi_note=null`을 동시에 요구한다. 좌표가 없으면 관련 좌표도 null이다. `input_status`와 `quality_flags`의 조합을 검증한다. 수용 사건의 MIDI와 virtual_key는 같은 KeyRegion 정의와 일치해야 한다.

## 4. 내부 Python 진입점

| S-ID | 함수 | 입력 | 결과 |
|---|---|---|---|
| S-01 | prepare_session | SessionStartRequest, SessionPolicy | NodeResult[SessionContext] |
| S-02 | estimate_hand_observation | FramePacket, SessionContext | NodeResult[ObservationBatch] |
| S-03 | calibrate_geometry_and_pose | CalibrationRequest, observations, geometry | NodeResult[CalibrationBundle] |
| S-04 | assess_observation_quality | observations, calibration, QualityPolicy | NodeResult[QualityAssessment] |
| S-05 | detect_finger_cycle | observation, calibration, quality, state | NodeResult[TapCandidateBatch] |
| S-06 | map_virtual_note | candidates, coordinate_buffer, geometry | NodeResult[VirtualInputBatch] |

Python WS 어댑터는 서버 시계만 존재하는 proposal과 위 최종 domain 결과를 구분한다. 순수 함수 시험은 명시적으로 주입한 emission clock을 사용할 수 있다. 실제 API는 임의 서버 시간을 browser emitted_ms로 주입하지 않는다.

## 5. 제어 REST API

기본 prefix 제안은 `/api/vision/v1`이다. 모든 변경 요청은 session_id/command_epoch/기대 버전을 검증한다. 같은 request_id+동일 payload는 동일 결과를 반환하고, 다른 payload 재사용은 409다. 사용자 취소·세대 변경 뒤 같은 요청의 재실행으로 상태를 복원하지 않는다.

| 메서드·경로 | 입력·결과 | 주의점 |
|---|---|---|
| POST `/sessions` | SessionStartRequest → SessionContext | OS 카메라 권한을 서버가 승인했다고 표시하지 않음 |
| GET `/sessions/{id}` | 현재 lifecycle/device/actual versions | 다른 세션의 원본 데이터 노출 금지 |
| PUT `/sessions/{id}/geometry` | 예상 geometry_version + 새 표시 정의 → 새 기하 | 원자 변경, 구 버전 결과 차단 |
| POST `/sessions/{id}/calibration/captures` | phase(rest/lift), finger, baseline_ref, capture policy → capture_id | rest 요청에도 대표 finger가 있으면 명시, 공유 기준 scope 보존 |
| POST `/sessions/{id}/calibration/captures/{cid}/finish` | capture 종료·검증 → result/model/진단 | 이미 끝난 요청은 결과 재조회, 자동 재수집 아님 |
| DELETE `/sessions/{id}/calibration/captures/{cid}` | 취소 → cancelled | 늦은 완료 결과 게시 금지 |
| POST `/sessions/{id}/start` | 활성 손가락·기대 버전 → running/hold | 실제 유효 조건 재검사 |
| POST `/sessions/{id}/pause` | 사용자 사건·사유 → paused | 진행 주기 초기화, 새 입력 차단 |
| POST `/sessions/{id}/resume` | 명시 재개 사건·기대 버전 → acquiring_rest | resume만으로 준비 완료 선언 금지 |
| POST `/sessions/{id}/events/ack` | decision_ref + event별 emitted_ms → 최종 사건 영수증 | 서버 결정 payload는 수정 불가 |
| POST `/sessions/{id}/close` | 종료 사건 → closed | 열린 unknown 종료·자원 정리·늦은 결과 차단 |
| POST `/sessions/{id}/exports` | 범위·포함 자료 → export_ref | 원본 영상 기본 제외 |

실제 보정 샘플은 같은 프레임 스트림에서 capture_id를 붙여 수집한다. REST 요청마다 전체 영상을 JSON base64로 중복 전송하는 것을 기본으로 하지 않는다.

HTTP 상태는 200=정상 계산/정상 보류, 201=세션/수집 생성, 400=형식 오류, 409=버전/순번/ID 충돌, 413=크기 상한, 422=의미상 잘못된 설정, 503=필수 모델/worker 불가를 제안한다. HTTP 성공과 `outcome=calibrated`는 같은 의미가 아니다.

## 6. 프레임과 사건 게시

### 6.1 WebSocket

연결: `/api/vision/v1/sessions/{id}/stream`. 핸드셰이크에서 schema·clock·epoch·model·policy·geometry·calibration을 확인한다. 재연결은 새 epoch로 시작하고 구 연결의 결과를 받지 않는다.

전송 제안: 작은 JSON header 1개와 즉시 이어지는 encoded binary payload 1개를 한 frame_id로 묶는다. 연결당 한 쌍만 처리 중일 수 있다. header 없이 binary, header 중첩, byte_length 불일치, timeout된 짝은 거절한다. 헤더/이미지 짝을 다른 연결과 공유하지 않는다. MessagePack 등으로 바꿔도 의미 계약은 동일하다.

```json
{
  "type": "frame.header",
  "request_id": "frame-request-42",
  "session_id": "s01",
  "clock_id": "browser-doc01",
  "frame_id": 42,
  "t_ms": 1180.0,
  "command_epoch": 2,
  "geometry_version": 2,
  "calibration_version": 3,
  "format": "image/jpeg",
  "byte_length": 28100,
  "source_width": 1920,
  "source_height": 1080,
  "encoded_width": 640,
  "encoded_height": 360
}
```

위 예시는 별도 필수 resize transform을 생략한 설명용 header다. 전체 구현 fixture에는 모든 필수 메타데이터를 넣고 source/inference 공간 변환을 검증한다. 압축 byte 상한 1MB·디코딩 상한 1920×1080·header 상한 16KB를 초기 제안으로 사용하고 모든 값은 정책화한다. 디코더는 선언 크기를 맹신하지 않는다.

서버 메시지: observation.result, calibration.progress/result, quality.changed, input.proposed, input.blocked, stream.error. NodeResult를 envelope.data에 넣고 request_id/frame_id/epoch와 연결한다. 순서가 바뀌거나 오래된 epoch의 결과는 표시·상태 변경·오디오에 사용하지 않는다.

### 6.2 출력 시각 확정과 B-03 전달

1. Python은 `decision_ref`와 immutable input proposals를 보관한다. 원래 후보 시각·좌표·키·품질은 이미 Python에서 결정한다.
2. 브라우저는 버전/세션을 확인한 첫 수신 시각을 browser emitted_ms로 붙이고 가상 키 표시·오디오를 한 번 실행한다.
3. 브라우저는 `/events/ack`에 decision_ref, event_id, emitted_ms, clock_id, epoch를 전송한다. 음번호·좌표·contact_verified는 인자로 받지 않는다.
4. 서버는 보관한 결정에 출력 시각만 결합하고 최종 사건을 한 번 B-03에 게시한다. 재전송은 같은 결과를 반환한다.
5. 게시 확인이 실패하면 같은 emitted_ms로 유한 재전송한다. 계속 실패하면 `publication_pending/failed`를 표시하고 분석 입력 누락을 진단한다. 사용자에게 이미 난 소리를 다시 재생하지 않는다.

이 ACK는 B-05 연습 설정 변경 ACK와 다른 프로토콜이다. server decision 자체의 실행 실패와 수신 확인 실패를 구분한다. offline replay는 기록된 emitted_ms를 보존하거나 replay emission임을 표시하고 실제 온라인 지연 지표에서 제외한다.

```json
{
  "request_id": "publish-42",
  "decision_ref": "decision-42",
  "clock_id": "browser-doc01",
  "command_epoch": 2,
  "events": [
    {"event_id": "evt-s01-e2-h1-index-7", "emitted_ms": 1260.0}
  ]
}
```

emitted_ms를 물리 접촉 정답으로 쓰지 않는다. finalization 자체를 늦게 해도 candidate t_ms는 바뀌지 않는다. 실제 소리 시작 정밀 측정은 별도 장치/오디오 측정 과제다.

## 7. 오류·보류 코드와 UX

| 코드 | 종류 | 주기/입력 처리 | 사용자 안내 |
|---|---|---|---|
| CAMERA_PERMISSION_DENIED | 장치 준비 실패 | 시작 차단 | 카메라 권한·다시 연결 |
| CAMERA_NOT_AVAILABLE | 장치 준비 실패 | 자동 장치 대체 금지 | 선택 장치를 사용할 수 없음 |
| TRACKER_NOT_READY / INFERENCE_FAILED | 필수 실행 실패 | 입력 차단, 프리뷰 유지 가능 | 모델 준비/추론 실패와 재시도 |
| FRAME_STALLED / TRACKING_GAP | 품질 | 전역 hold·주기 초기화 | 영상 중단·연결 확인 |
| HAND_NOT_OBSERVED | 품질 | 해당 손 unknown·주기 초기화 | 선택한 손을 보여 달라는 안내 |
| HAND_IDENTITY_AMBIGUOUS | 품질 | 주기 연결 금지 | 손 분리·위치 확인 |
| TARGET_OUT_OF_FRAME | 국소 품질 | 해당 손가락 보류 | 대상 손가락/손바닥 범위 안내 |
| LOCAL_BONE_DISTORTION / EXTREME_SCALE | 국소 품질 | 주기 초기화, 구간 진단 | 가림·추정 불안정 점검 |
| CALIBRATION_MISSING / INVALIDATED | 준비 | 해당 손가락 입력 차단 | 기준/들림 저장 안내 |
| STALE_EPOCH / STALE_GEOMETRY / STALE_CALIBRATION | 계약/수명 | 오래된 결과 폐기 | 필요한 경우 재준비, 반복 토스트 금지 |
| DUPLICATE_FRAME / NON_MONOTONIC_TIME | 계약 | 상태 전진 금지 | 개발 진단, 사용자 타건 실패로 기록 금지 |
| COORDINATE_UNAVAILABLE / KEY_BOUNDARY / OUTSIDE_KEYBOARD | 음 매핑 | withheld·음번호 null | 경계/영역/좌표 사유 |
| PUBLICATION_PENDING | 전달 | B-03 게시 대기, 오디오 중복 금지 | 연결 상태 안내 |

인수 시 코드·reason·scope·evidence가 모두 보존되는지 확인한다. UI 설명을 바꾸더라도 기계 처리 코드의 의미를 바꾸지 않는다.

## 8. 계약 검증·버전 업그레이드

- 최소 fixture: 정상 세션, 권한 대기, 정상 관측/손 없음/추론 실패, 보정 성공/불가분/길이 실패, 정상 후보/없음, 수용/경계 보류, 열린/닫힌 unknown, 중복 게시.
- 이벤트의 입력 출처·finger·시계·버전·원본 후보 참조를 누락하면 소비자가 거절한다.
- 필드 이름/단위/의미를 바꾸면 schema major를 검토한다. optional 진단 추가와 정책 수치 변경을 구분한다.
- 이전 schema는 명시한 adapter에서만 변환한다. `world`/`world_landmarks`처럼 이름이 비슷하다는 이유로 자동 추측하지 않는다.
- 계약 fixture는 웹/Python/B-03에서 공동 사용한다. 동등한 변환·이벤트 순서·null 의미를 확인한다.
