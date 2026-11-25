# 09 · 모델 입력·보정·실험 기록 계약

[목차](README.md)

이 문서는 현재 함수와 CLI가 읽는 필드를 설명한다. 완전한 JSON Schema 검증기가 구현되어 있다는 뜻은 아니다. 수집기에서 배열 길이·유한값·시각순·선택 손의 일관성을 확인한다.

## 좌표를 구분하기

| 이름 | 형태 | 사용하는 곳 |
| --- | --- | --- |
| world landmarks | 21×3 배열 또는 21개 `{x,y,z}` | 관절 특징·보정 |
| 영상 landmarks | 21개 `{x,y,z}` | 후보 tip 위치, 비교 기록 |
| Depth 요청 landmarks | 21×2 `[x,y]`, 각 축 0~1 | 깊이 패치 위치·손 hull |
| anchors | 3~8개의 `[x,y]`, 각 축 0~1 | 책상 깊이 기준 |
| depth map | 원본 크기의 float 2D 배열 | `extract_signals()` |

world와 영상 좌표는 같은 뜻이 아니다. world 좌표의 원점·단위는 입력 모델 정의를 따른다. 한 이미지의 영상 좌표에 다른 시각·회전·크롭의 깊이를 연결하지 않는다. 반전된 미리보기만 보고 좌표를 뒤집어 저장하면 오차가 생길 수 있다.

## 시각

- 밀리초이며 각 상태 머신의 호출 안에서 엄격히 증가해야 한다.
- 실시간 입력은 세션의 단조 증가 시계를 사용한다.
- 저장된 trial의 프레임·예측·정답·불명 구간은 모두 **시행 시작을 0으로 한 상대 시각**이다.
- emitted_ms도 같은 시계와 원점이다. epoch 시각·비디오 재생 초·프레임 번호를 그대로 섞지 않는다.
- 원본 영상의 시작 오프셋과 동기화 오차를 별도 기록한다. 이 패키지는 영상 녹화·시계 동기화를 구현하지 않는다.

## 관절 보정값

```text
{
  base: [5개 특징 평균],
  delta: [5개 들림-기준 차이],
  lengths: [3개 기준 마디 길이],
  jitter: 숫자,
  signal: 숫자
}
```

깊이 비교용 calibration은 손가락 이름을 키로 한다.

```text
{
  index: {
    pose: 관절 보정값,
    depth: {base, delta, noise, rest_samples, lift_samples} 또는 null,
    depth_error: 보류 이유 또는 null
  }
}
```

## 단일 이미지 Depth 요청

`scripts/run_depth.py`는 JSON의 image_path 또는 JPEG data URI image를 받는다. image_path는 요청 파일 위치 기준이다. 예시는 타입을 설명하는 의사 데이터이며 landmark의 21개 값은 실제 입력으로 채워야 한다.

```text
{
  frame_id: 0,
  t_ms: 0,
  input_size: 294,
  image_path: "frame.jpg",
  evidence_type: "annotated_capture 또는 실제 출처",
  landmarks: [[x0,y0], ... 21개],
  anchors: [[0.08,0.08], [0.92,0.08], [0.08,0.92], [0.92,0.92]]
}
```

즉시 실행할 수 있는 완전한 합성 요청은 `python scripts/make_synthetic_inputs.py`로 생성한다. 이 생성기는 MediaPipe 출력이나 실제 사람 영상을 만들지 않는다.

## v1 · 관절/손등 패치 기록

```text
{
  schema_version: 1,
  evidence_type: "실촬영/합성 등 원본 출처",
  trials: [{
    person_id, session_id, scenario, duration_ms,
    calibration: {index: 관절 보정값},
    frames: [{t_ms, world_landmarks: 21개 좌표 또는 null,
              skin_patch: JPEG data URI 또는 null}],
    truth: null 또는 [{finger: "index", t_ms: 200}]
  }]
}
```

- check_tap은 calibration·frames·duration_ms·truth를 사용한다.
- analyze_skin은 frames의 t_ms·skin_patch와 조건 메타데이터만 사용한다. 따라서 합성 피부 패치 예제에는 관절 보정값이 필요하지 않다.
- v1에는 v2의 독립 검수 확인 플래그·불명 마스크가 자동 적용되지 않는다.

## v2 · 관절/깊이 비교 기록

최상위 필드는 다음과 같다.

```text
{
  schema_version: 2,
  kind: "pthoven_depth_comparison",
  evidence_type: "browser_rgb_landmark_depth_capture 또는 합성 등 원본 출처",
  contact_verified: false,
  trials: [ ... ]
}
```

시행의 주요 필드:

| 필드 | 내용 |
| --- | --- |
| id | 중복되지 않는 시행 ID |
| person_id / session_id | 익명 사용자·촬영 세션 |
| scenario / split | 동작 조건과 dev/test |
| duration_ms | 양수 시행 길이 |
| calibration / options | 해당 시행의 고정 보정·FSM·결합 설정 |
| model | 모델 revision·입력 크기·장치 등 |
| request_interval_ms | 깊이 요청 목표 간격. 실제 dt와 같다고 가정하지 않음 |
| pose_frames | 전체 관절 프레임 |
| depth_frames | 깊이와 같은 프레임의 관절·깊이·signals |
| predictions | method별 후보 배열 |
| truth | null, 확인된 무타건 [], 또는 검수 이벤트 배열 |
| annotation_confirmed | 독립 검수를 사람이 확인했는가 |
| unknown_intervals | 정답이 불명인 상대 시각 구간 |

`pose_frames` 원소는 t_ms, emitted_ms, world_landmarks, landmarks, signals를 가진다.

`depth_frames` 원소는 t_ms, world_landmarks, landmarks, depth, roundtrip_ms, signals를 가진다. depth는 [06](06-depth-signal.md)의 응답이며 손가락별 contrast·valid를 포함한다. 오류 프레임에서는 world/landmarks/depth가 null 또는 생략될 수 있다. 없는 관측을 0 좌표로 채우지 않는다.

재생할 수 있으려면 world_landmarks와 depth 특징을 보존해야 한다. signals만 있는 파일은 기존 결과 평가에는 쓸 수 있지만 관절 특징을 처음부터 재생할 수 없다.

predictions의 키는 pose_full, pose_matched, depth, fusion이다. 각 이벤트의 기본 형태:

```json
{
  "finger": "index",
  "method": "fusion",
  "kind": "pose_tap_candidate",
  "t_ms": 400,
  "emitted_ms": 450,
  "latency_ms": 50,
  "contact_verified": false
}
```

정답은 finger와 t_ms만 있으면 된다. finger는 thumb/index/middle/ring/pinky 중 하나다. 수집기 설정상 보정되지 않은 손가락도 정답에 존재하면 그 손가락의 누락이 생길 수 있으므로 실험 범위를 사전에 정한다.

## 출처와 데이터 보관

합성 해석적 관절, 합성 그림, 실제 촬영, 실제 촬영의 라벨링, 저장 특징의 재생을 서로 구분한다. `make_depth_fixture.mjs`는 synthetic_analytic_fixture를 표시하고 truth=null로 둔다.

단일 이미지의 추론 성공은 accuracy_evaluated=false다. contact_verified=false는 자동 물리 접촉 검증이 없다는 뜻이다. 사람의 annotation_confirmed와 혼동하지 않는다.

실제 영상·패치·익명 ID도 실험 데이터로 관리한다. 이 저장소에는 data/, outputs/, .cache/와 가중치를 올리지 않는다. 버전 관리할 것은 코드·설정의 설명·검증 절차다.
