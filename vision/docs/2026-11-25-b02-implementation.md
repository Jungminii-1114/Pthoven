# 2026-11-25 B-02 구현 기록 · 실시간 관측과 가상 연주 입력

기준 문서: [B-02 설계](../../docs/plan/02-big-nodes/B-02.md) · [PRD](../../docs/prd/B-01-B-02/PRD.md) · [계약](../../docs/prd/B-01-B-02/CONTRACTS_AND_API.md) · [기술 상세](../../docs/prd/B-01-B-02/TECHNICAL_SPEC.md)

## 1. 요약

- B-01이 `ready`(입력 준비 확인 통과)가 된 뒤 **입력 점검**을 시작하면 다음 순서로 처리한다.
  1. 새 프레임마다 S-02(손 관측)와 S-04(품질)를 B-01과 같은 코드로 수행한다.
  2. S-05: 손가락별 들림→복귀 주기를 판정한다.
  3. S-06: 복귀 후보 프레임의 손끝 좌표를 가상 흰 건반 C4~C5에 매핑한다.
  4. 결과를 `input.proposals`로 브라우저에 돌려준다.
- 브라우저는 제안 하나에 대해 건반 표시와 음을 **한 번만** 실행한다. 그 시각을 `emitted_ms`로 붙여 `/events/ack`로 보내면 서버가 최종 `virtual-input.v1` 사건을 만든다.
- S-05의 판정은 `demo_test/python_tap`의 `PoseTapDetector`(상대 기준 모드, `engine_revision=2026-11-25-landing-hold-v6`)를 그대로 쓴다. PRD 8.1의 "판정 수학을 두 곳에 구현하지 않는다"는 원칙 때문이다. 이 판정기는 같은 날 실기록으로 검증했다. 검지 30/30, 중지 12/20, 정지·공중 탭 오검출 0이었다.
- 모든 사건은 `kind=pose_tap_candidate`, `contact_verified=false`다. 실제 책상 접촉을 확인한 것이 아니다.

## 2. 변경 파일

| 파일 | 변경 |
|---|---|
| `src/pthoven_vision/b02.py` (신규) | `CoordinateBuffer`(최근 5초·600관측), `map_virtual_note`(S-06), `InputRun`(S-05 실행·후보 ID·중복 억제·decision_ref·ACK 결합·요약·기록) |
| `src/pthoven_vision/session.py` | 세션에 `run`·`run_history` 추가. `start_input`/`stop_input`/`acknowledge` 추가. 프레임 처리 중 `running`이면 S-05·S-06을 실행하고 응답에 `input`을 넣는다. `invalidate()`(epoch 변경)는 진행 중 실행을 종료·보관한다. `running` 중에는 수집 시작을 거부한다(`INPUT_RUNNING`). `ready()`는 실행 중 상태를 건드리지 않는다. 기록에 `input_runs`를 포함한다. |
| `src/pthoven_vision/contracts.py` | `AckEvent`, `AckRequest`(음번호·좌표·접촉 여부는 받지 않고 `event_id`·`emitted_ms`만 받음) |
| `src/pthoven_vision/api.py` | `POST /sessions/{id}/start`, `/pause`, `/events/ack`. health `scope="B-01+B-02"` |
| `src/pthoven_vision/replay.py` | `replay_input_run()`: 기록된 관절로 같은 판정을 다시 실행(landmark replay). CLI가 `input_runs`를 자동 재생 |
| `web/index.html`, `web/app.js`, `web/style.css` | 7번 카드 **입력 점검**. 시작·종료 버튼과 후보·건반 입력·보류·게시 대기 수, 최근 입력, 손가락 상태를 표시한다. 건반 하이라이트, WebAudio 음, ACK 게시(유한 재전송), 이미 처리한 사건과 오래된 epoch 사건 무시 |
| `tests/test_b02.py` (신규, 16개) | 아래 4장 |
| `tests/test_web.mjs` | B-02 브라우저 시나리오 1개 추가 |
| `tests/test_api.py` | health scope 기대값 갱신 |

## 3. 설계 결정과 이유

| 결정 | 이유 | 버린 대안 |
|---|---|---|
| S-05에 `pthoven_tap.PoseTapDetector(mode="local_baseline")` 재사용 | 실기록으로 검증된 판정기다. B-01 템플릿이 같은 특징 형식(`python_tip_angles_v1`)이라 변환 없이 사용할 수 있다. | B-02 전용 상태 머신 신규 작성: 판정 수학이 중복되고 검증 이력이 없다. |
| 후보 좌표는 **복귀 시작 프레임과 같은 t_ms·frame_id**의 손끝만 사용 | TECHNICAL_SPEC 4.3·10.2. 출력 시점 좌표로 과거 후보의 음을 바꾸지 않는다(B02-06). | 최근 좌표·보간: 금지(P0). |
| 경계 완충 = 키 폭 5%, 건반 위·아래 가장자리에도 적용 | 경계점의 임의 좌·우 선택 금지. 가장자리 근처도 위치 오차가 크면 오음이 되기 때문에 보수적으로 보류한다. | 가장자리 무시: 건반 밖 손끝을 끝 키로 판정할 위험이 있다. |
| 손 추적 ID가 바뀌면 판정기를 새로 만든다 | INV-08. 공백(>200ms)이나 손 연결이 끊긴 뒤 두 반쪽 동작을 한 타건으로 합치지 않는다. | 기존 기준 유지: 다른 손이나 재진입 손에 이전 주기가 이어질 수 있다. |
| 실행 중 손가락 품질 실패는 판정기가 주기 단위로 처리하고, epoch는 바꾸지 않는다 | B-01의 `ready` 단계는 한 프레임만 무효여도 hold와 epoch 증가를 한다. 연주 중 착지 순간의 튐마다 이렇게 하면 입력이 계속 끊긴다. 영상 정지(watchdog)·장치·기하 변경·재보정·종료는 여전히 epoch를 올리고 실행을 끝낸다. | 연주 중에도 B-01 규칙 적용: 실기록 기준 탭마다 끊긴다. |
| `/pause` 뒤 재시작은 `ready` 확인부터 | PRD 표의 resume 의미("resume만으로 준비 완료 선언 금지")를 지킨다. | 바로 재시작: 기준 자세 확인 없이 오래된 기준으로 판정한다. |
| proposal에는 `emitted_ms=null`, ACK에서만 결합 | CONTRACTS 6.2. 서버 시계를 브라우저 시계로 섞지 않는다. 재전송은 최초 `emitted_ms`를 유지하고, `emitted_ms<t_ms`는 거부한다. | 서버 시각 주입: 계약 위반이다. |
| 전송은 기존 REST 유지 | B-01 웹·API·테스트를 그대로 재사용한다. 의미 계약은 같다. | WebSocket: 후속 범위로 남긴다. |

## 4. 요구사항별 검증(합성·계약 테스트)

`tests/test_b02.py`는 수학적으로 만든 손을 쓴다. 사람·카메라 정확도의 근거가 아니다.

| ID | 내용 | 테스트 |
|---|---|---|
| B01-10 | ready 통과 전 입력 시작 금지 | `test_start_requires_ready_and_calibration`, `test_pause_discards_run_and_restart_needs_ready` |
| B02-01 | 중복·역순 프레임 거부 | 기존 B-01 프레임 검사 경로 공유(`test_session`·`test_api`) |
| B02-05 | 기준→들림→복귀만 후보, 정지·흔들림은 후보 아님 | `test_tap_cycles_emit_one_provisional_note_each_with_candidate_frame_coordinates`(5주기→5후보), `test_static_and_jittered_rest_never_emits`(300프레임 0) |
| B02-06 | 후보 frame 좌표 정확 일치 | 위 테스트의 `candidate_frame_id` 대조, `test_map_requires_exact_candidate_frame` |
| B02-07 | 영역 밖·경계·좌표 없음은 `midi_note=null` | `test_outside_keyboard_is_withheld_without_note`, `test_key_boundary_is_withheld`, `test_map_requires_exact_candidate_frame`(`COORDINATE_UNAVAILABLE`·`STALE_GEOMETRY`) |
| INV-07 | 반전도 같은 기하 계약 | `test_flip_x_maps_through_same_geometry` |
| B02-08 | 한 번만 게시, 재전송은 최초 값 | `test_ack_finalizes_once_and_keeps_first_emitted_ms`, 웹 시나리오(같은 제안 2회 수신 → ACK 1회, 오래된 epoch 무시) |
| B02-09 | watchdog 정지 → 실행 종료·epoch 증가·오래된 프레임 거부 | `test_frame_stall_ends_run_and_blocks_old_epoch` |
| B02-02·INV-03·INV-08 | 손 없음·공백 사이의 반쪽 동작 미결합 | `test_hand_loss_mid_lift_discards_cycle`, `test_tracking_gap_does_not_join_two_half_cycles` |
| B02-11 | 동시 후보 독립 | `test_two_active_fingers_keep_independent_candidates` |
| B02-12·INV-05 | 기록·재생 재현성, 누락 프레임 보존 | `test_export_and_landmark_replay_reproduce_candidates` |
| B01-12 | 재보정 요청 시 입력 중지 | `test_recalibration_request_stops_input` |

실행 결과(2026-11-25):
- `vision`: Python 102개 통과(기존 86 + B-02 16), 웹 22개 시나리오 통과
- `demo_test/python_tap`: 78개 통과
- 로컬 서버(8770)에서 페이지 로드, 신규 경로 3개의 OpenAPI 노출, 콘솔 오류 없음을 확인했다.

## 5. 검증하지 않은 것(사람 확인 필요)

- **실제 카메라에서의 B-02 입력:** 이번 작업에서는 카메라와 손으로 실행하지 않았다. 실제 동작은 사용자가 8770 화면에서 확인해야 한다. B-01 보정은 `pose.py`의 별도 수학으로 만든다. 그래서 Tap Lab 보정(v5 품질 필터)과 결과가 다를 수 있다. 예를 들어 약지 보정 통과 여부가 달라질 수 있다.
- **NFR-01~05(처리 FPS, `emitted_ms - t_ms` p95, 영상 정지 안내 시간, 보정 시간, 10분 안정성):** 측정하지 않았다. 응답의 `t_ms`와 ACK의 `emitted_ms`가 기록되므로 실제 실행 기록에서 계산할 수 있다.
- **B-03 소비자 연결:** 최종 사건은 세션 기록 `input_runs[].virtual_inputs`에 보존만 한다. B-03 게시 경로와 `publication_pending/failed` 상태 화면은 아직 없다. 브라우저는 ACK 실패를 내부 카운터에만 남긴다.
- **unknown 구간의 손가락 단위 범위:** B-01의 `QualityTimeline`(세션 단위)을 그대로 쓴다. 손가락별 unknown 구간 분리는 후속이다.
- **좌우 손 교차, 두 손 동시:** P0 범위(한 손) 밖이다. 같은 handedness 손이 둘 이상이면 기존처럼 `HAND_IDENTITY_AMBIGUOUS`로 보류한다.

## 6. 실행 방법

```bash
cd vision
.venv/bin/python -m pthoven_vision.api --port 8770
```

http://127.0.0.1:8770/ 에서 진행한다. 순서는 카메라 연결 → 모델 준비 → 기준 자세 → 손가락 들림 → **입력 준비 확인** → **입력 점검 시작**이다. 손가락을 들었다 내리면 손끝 아래 가상 건반이 반응한다. 종료 후 **보정 기록 JSON 저장**을 누르면 `input_runs`(관절·후보·제안·최종 사건)가 포함된다. 같은 파일을 `python -m pthoven_vision.replay <파일>`로 재생하면 후보 재현성이 나온다.

## 7. 실사용 중 수정

- **B-01 웹 동기화 재시도(2026-11-25):** 손가락 선택을 바꿔 새 세션이 생긴 직후, 장치·기하 동기화가 바쁜 상태에서 한 번 건너뛰는 경우가 있었다. 그러면 재시도가 없어 새 세션에 `/device`·`/geometry`가 전송되지 않았다(서버 로그상 세션 생성 뒤 프레임 0건). 그 결과 `기준 자세 수집` 버튼이 잠겼다. `app.js`의 200ms 렌더 주기에서 아래 조건을 모두 만족하면 동기화를 다시 예약하도록 했다.
  - 세션과 스트림이 있다.
  - 영상을 수신 중이다.
  - 수집 중이 아니고, 연결·제어 작업이 진행 중이 아니다.
  - 기하 적용이 대기 중이거나 장치 서명이 바뀌었다.

  `syncBlocked`(서버가 거절한 기하)는 기존처럼 사용자가 다시 적용해야 한다.
