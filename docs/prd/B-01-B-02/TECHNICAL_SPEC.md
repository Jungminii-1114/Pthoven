# B-01·B-02 기술 상세

[PRD](PRD.md) · [계약·API](CONTRACTS_AND_API.md) · [시험 계획](ACCEPTANCE_TEST_PLAN.md)

## 1. 실행 구조와 기술 선택

### 1.1 기본 데이터 경로

```text
브라우저 getUserMedia: 사용자가 선택한 카메라
  → 새 프레임 확인 + browser t_ms + frame_id
  → 유한 크기 전송 큐
  → Python frame decoder / RGB 변환
  → S-02 Hand Landmarker adapter / hand association
  → S-04 quality gate
  → 보정 중: S-03 calibration collector / fitter
  → 연주 중: S-05 finger cycle → S-06 key mapping
  → 브라우저: 오래된 결과 차단 / emitted_ms 확정 / 키·소리 표시
  → 사건 게시 확인 → B-03 입력 + quality interval
```

기본 배포는 브라우저와 로컬 Python 서버를 같은 컴퓨터에서 실행한다. 프리뷰를 그리는 루프는 서버 응답을 기다리지 않는다. 프레임 처리와 화면 그리기·진단 UI 갱신 주기를 분리한다. localhost 밖 배포는 인증·전송 보호·세션 격리까지 별도 범위로 검토한다.

### 1.2 기술 스택 제안

| 영역 | 기본 선택·역할 | 버전 관리 |
|---|---|---|
| 실행 언어 | Python 3.10 이상 중 MediaPipe wheel이 지원되는 버전으로 고정 | 운영체제별 설치 확인 후 lock에 정확 버전 기록 |
| 손 추론 | MediaPipe Tasks Hand Landmarker, Python `VIDEO` 모드 | SDK·모델 파일 해시·옵션·실행 장치 기록 |
| 수치 연산 | NumPy, float64 기하 계산 | 라이브러리 버전 고정 |
| 프레임 디코딩 | OpenCV, 디코딩 후 BGR→RGB를 명시 | 압축 포맷·resize 기록 |
| 서버 | FastAPI/ASGI 제안, REST 제어 + WebSocket 프레임/사건 | 제품 도입 시 정확 버전 고정 |
| 계약 | Pydantic v2 또는 동등한 엄격 검증 | schema_version과 라이브러리 버전 구분 |
| 웹 | TypeScript 권장, HTML video + Canvas/Web Audio | npm lockfile, 브라우저별 기능 검사 |
| 저장 | 세션별 JSONL + manifest + 선택적 영상 | 형식 버전·정책·모델 해시 보존 |
| 검증 | pytest, Python fixture/replay CLI, 웹 좌표/장치 모의시험 | 실제 카메라 시험은 별도 절차 |

이는 기술 선택 제안이다. 특정 프레임워크의 기본 기능이 멱등성·품질 게이트·사건 정확성을 자동 보장한다고 가정하지 않는다. [공식 Hand Landmarker 문서](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker?tab=Python)는 이미지/추정 world 관절과 handedness 출력을 구분한다. 해당 출력만으로 책상 평면이나 실제 접촉을 얻는 것은 아니다.

Python worker는 초기에는 한 세션당 직렬 `detect_for_video` 호출을 사용한다. 모델에 전달하는 정수 timestamp는 `floor(t_ms)`이며 같은 정수 ms가 다시 나오면 새 시간으로 조작하지 말고 해당 프레임을 생략한다. 원래 float `t_ms`는 따로 보존한다. 비동기 LIVE_STREAM 어댑터를 추가하면 모델 내부 드롭과 앱 드롭을 분리해 기록한다.

### 1.3 계획 모듈과 함수

아래 경로는 구현할 구조의 제안이며 파일의 현재 존재를 뜻하지 않는다.

```text
src/pthoven_vision/
  contracts.py               # 데이터 검증, NodeResult, enum
  policy.py                  # 버전 있는 정책과 교차 필드 검증
  session.py                 # SessionContext, 권한/장치/세대 상태
  frame_ingest.py            # 프레임 검증, 유한 큐, 단조 시각
  hand_adapter.py            # MediaPipe 입출력 변환
  hand_association.py        # track ID, handedness, ambiguous
  geometry.py                # 표시 변환/역변환, key regions
  pose_features.py           # 손바닥 상대 TIP, 관절각, 구간 길이
  length_quality.py          # 공통 배율/국소 왜곡 분리
  calibration.py             # 샘플 수집·robust 통계·그룹 투영
  quality.py                 # 손가락별 게이트·시간 구간·watchdog
  cycle.py                   # 손가락별 상태 머신
  coordinate_buffer.py       # 후보 시각 원본 좌표 조회
  note_mapping.py            # 좌표→가상 음, 보류, 중복 억제
  pipeline.py                # B-01/B-02 오케스트레이션
  event_store.py             # 사건·버전·근거·게시 확인
  recording.py               # 기록·내보내기
  replay.py                  # 시간 기반 오프라인 검증
  api/                       # REST/WS 어댑터
```

오케스트레이터의 단계 호출만 위 구조를 연결한다. 테스트는 순수 함수와 상태 전이를 API 없이도 실행할 수 있어야 한다. 모델 객체와 카메라 권한 객체를 순수 기하 함수에 주입하지 않는다.

## 2. 세션·장치 상태

### 2.1 수명주기

`created → awaiting_permission → previewing → calibrating → ready → running`

운영 중에는 `running → hold → ready`, 사용자 동작으로 `paused`, 종료 후 `closed`로 전이할 수 있다. 추론 모듈의 필수 실패는 `faulted`로 표시하되 프리뷰 수명과 구분한다. `hold → running` 자동 전이는 하지 않고 재획득·기준 확인 후 `ready`를 거친다. 일시적 품질 회복의 재개 정책은 사용자 설정으로 고정한다.

`device_state`는 단일 ready 불리언 대신 다음 축을 가진다.

- permission: unknown / requesting / granted / denied.
- stream: disconnected / connecting / receiving / stalled / ended.
- tracker: not_loaded / loading / ready / failed.
- selected_hand: absent / observed / ambiguous.
- calibration: missing / collecting / valid / invalidated / failed.
- input: blocked / acquiring_rest / ready / running.

### 2.2 장치 처리

1. 사용자의 연결 동작에서 권한을 요청하고 지원 장치 목록을 갱신한다.
2. 선택 `deviceId`를 명시해 연결한다. 실패 시 오류와 다시 선택 UI를 반환한다.
3. 실제 활성 트랙의 label·해상도·frameRate·설정 정보를 기록한다. 요청값과 실제값을 구분한다.
4. 새 프레임 수신을 확인한 뒤 tracker를 준비한다. 로딩 동안에도 프리뷰는 표시한다.
5. 장치 변경/트랙 종료/재연결에는 이전 요청을 취소하고 새 입력 세대를 만든다.

iPhone 연속성 카메라는 OS가 영상 장치로 제공해야 한다. 브라우저 안에서 USB/무선 연결 자체를 대신 구성하지 않는다. 노출되는 줌/포커스 설정은 요청·실제 적용값을 구분하고, SDK에서 제공하지 않는 줌 잠금 기능이 있다고 가정하지 않는다.

## 3. 버전과 무효화

카운터는 세션 내부 단조 증가 정수다. 최초 보정 전 `calibration_version=0`; 유효 bundle 발급 후 1 이상이다. frame/관측/후보/사건은 생성 당시 버전을 그대로 갖는다.

| 변경 | geometry | calibration | command_epoch | 필수 처리 |
|---|---|---|---|---|
| 카메라/실제 해상도/추론 resize·crop 정책 변경 | 증가 | 무효화 후 새 발급 | 증가 | 큐·버퍼·주기·기준 폐기 |
| 손 선택/보정 대상 사용자 교체 | 유지 가능 | 무효화 | 증가 | hand ID·주기·개인 템플릿 폐기 |
| 기준 자세 새로 수집/발급 | 유지 | 증가 | 증가 | 같은 기준을 공유한 lift 템플릿 무효화 |
| 한 손가락 lift 템플릿 교체 | 유지 | bundle 증가 | 증가 | P0는 전체 진행 주기 초기화, 타 손가락 템플릿 내용은 유지 |
| CSS 크기·object-fit·좌우/상하 반전·키 배치 변경 | 증가 | 아래 조건 적용 | 증가 | 미완료 주기·구 기하 결과·대기 매핑 폐기 |
| 모델 revision/특징 정의/수치 정책 변경 | 필요 시 증가 | 무효화 | 증가 | 동일 모델이라고 가정하지 않음 |
| B-05에서 BPM/힌트만 실제 적용 | 유지 | 유지 | 명령 계층 기준 | `config_version` 변경, 시도 경계 분리 |
| 웹소켓 재연결/수동 정지/전역 품질 보류 진입 | 유지 가능 | 유효 조건 재검사 | 증가 | 오래된 응답/명령 차단, 기준 재획득 |

표시 변경 시 원본 image 좌표와 손바닥 상대 world 특징만 사용하는 템플릿은 재사용할 수 있다. display 좌표를 특징으로 포함했다면 재사용하지 않는다. 모든 템플릿에 feature_space와 의존성을 기록한다. 표시만 반전했다고 MediaPipe 입력 픽셀을 반전시키지 않는 것이 기본 정책이다.

같은 hold가 유지되는 동안 매 watchdog tick마다 epoch를 증가시키지 않는다. 전역 hold 진입 한 번에 한 번 증가시킨다. 손가락 하나의 국소 품질 실패는 해당 주기를 초기화하고 관련 범위만 제외한다. 전역 준비 조건을 깨는 경우에만 전체 hold/epoch 전환한다.

## 4. 좌표와 가상 건반

### 4.1 좌표계

| 이름 | 정의 |
|---|---|
| source image | 반전/표시 crop 전 원본 영상. `[u,v]`는 정규화, 좌상단 원점 |
| inference image | 원본을 동일 종횡비로 축소한 추론 영상. crop/letterbox가 있으면 역변환 메타데이터 필수 |
| display | 원본을 CSS video 영역에 표시한 좌표, CSS px |
| key space | display에서 keyboard 영역의 로컬 좌표, CSS px |
| palm local | 모델 추정 world 관절을 손바닥 기준축·손 크기로 정규화한 무차원 특징 |

`world_landmarks`가 SDK에서 미터 단위를 쓰더라도 이를 카메라로 측정한 책상 기준 높이로 사용하지 않는다. 추정값과 모델 해시를 보존한다. image 좌표가 0~1 밖이면 값 자체는 진단에 남기되 매핑에서는 보류한다. 강제 clamp로 화면 가장자리 건반을 치게 만들지 않는다.

### 4.2 표시 변환 수식

원본 `(W,H)`, video 컨테이너 `(Cw,Ch)`, 가운데 정렬이라면:

```text
contain: s = min(Cw/W, Ch/H)
cover:   s = max(Cw/W, Ch/H)
ox = (Cw - s*W)/2
oy = (Ch - s*H)/2

u' = flip_x ? 1-u : u
v' = flip_y ? 1-v : v
x_display = ox + s*W*u'
y_display = oy + s*H*v'
x_key = x_display - keyboard_left
y_key = y_display - keyboard_top
```

object-position을 가운데 이외로 지원하면 offset 정의까지 확장한다. video와 overlay의 회전/반전 원점도 고정한다. 위 합성을 3×3 행렬과 역행렬로 저장해 UI와 Python이 동일 fixture로 검증한다. DPR은 Canvas 해상도 배율일 뿐 CSS 키 좌표 배율로 중복 적용하지 않는다.

가상 건반은 영상 위 사용자가 정한 표시 영역이다. 책상 평면과 실물 건반을 알아냈다는 뜻이 아니다. P0는 `contain`과 수동 keyboard rect, 동일 폭 흰 건반을 기본으로 한다. `cover` 지원은 매핑 테스트를 먼저 통과한다.

### 4.3 키 영역과 경계

- 각 키는 `key_id`, MIDI 음번호, 다각형, layer, 활성 여부를 갖는다. 흰 건반의 MIDI는 60,62,64,65,67,69,71,72처럼 명시한다. 키 인덱스를 그대로 반음 번호에 더하지 않는다.
- 완충 영역은 기본 key 폭의 5%를 제안한다. 경계에 가까우면 withheld다. 정확히 같은 경계점의 임의 왼쪽/오른쪽 선택을 금지한다.
- P1 검은 건반은 명시한 렌더링 layer와 동일한 hit-test 순서를 적용한다. 의도된 검은/흰 영역 가림과 설계 오류인 임의 다각형 중첩을 구분한다.
- 후보 좌표는 버퍼의 **후보 frame_id 정확 일치**가 우선이다. P0에서 매칭 프레임이 없으면 보류한다. P1 nearest/interpolation은 허용 시간·양쪽 유효성·버전을 명시한 정책으로만 추가한다.
- 서버 결과가 늦게 도착해 손끝이 다른 키 위에 있어도 과거 후보 음은 변경하지 않는다.

## 5. 관측과 손 연결

S-02는 디코딩 실패·RGB 포맷·손 추론 실패·선택 손 없음·모호한 연결을 다른 진단으로 반환한다. 관절은 21개 유한 좌표를 요구한다. SDK가 관절별 신뢰도를 제공하지 않으면 해당 필드는 null이며 존재하지 않는 신뢰도를 만들어내지 않는다.

손 연결 비용은 이전 손바닥 중심 이동·정규화 관절 거리·handedness 일관성 등을 사용한다. 비용·차선 후보와의 margin·상한을 정책화하고 2개의 손이 구분되지 않으면 ambiguous로 보류한다. handedness score 하나만으로 사람이나 손의 동일성을 보장하지 않는다. 추적 소실 후 새 track_id를 발급하고 진행 중 주기를 연결하지 않는다.

손 선택 UI는 신체의 좌/우를 뜻한다. CSS 미러가 바뀌어도 handedness 라벨을 단순 뒤집지 않는다. 카메라/SDK의 미러 전제는 장치 시험으로 확인해 adapter에 명시한다.

## 6. TIP·관절 특징과 길이 검사

### 6.1 손가락별 landmark

| 손가락 | 순서 |
|---|---|
| 엄지 | CMC(1) → MCP(2) → IP(3) → TIP(4) |
| 검지 | MCP(5) → PIP(6) → DIP(7) → TIP(8) |
| 중지 | MCP(9) → PIP(10) → DIP(11) → TIP(12) |
| 약지 | MCP(13) → PIP(14) → DIP(15) → TIP(16) |
| 소지 | MCP(17) → PIP(18) → DIP(19) → TIP(20) |

엄지를 DIP/PIP로 잘못 이름 붙이지 않는다. 뿌리 관절 특징은 손바닥 기준점과 첫 관절 구간, 중간·끝 특징은 연속 세 관절로 정의하고 사용한 landmark IDs를 모델에 보존한다. 표면 영상에서 보이지 않는 손목 뼈·근육 움직임을 관측 필드로 만들지 않는다.

### 6.2 특징 계산

1. 손바닥 MCP 점들로 좌우축·전방축·법선축을 직교화한다. 축의 길이가 epsilon 이하이거나 방향이 불안정하면 보류한다.
2. 대상 손가락 root→TIP 벡터를 축에 투영하고 손바닥 폭으로 나누어 상대 x/y/z를 얻는다.
3. 원본 image에서도 root→TIP의 손바닥 상대 x/y를 얻는다. 세계 좌표·영상 특징을 구분해 결측 마스크와 함께 보존한다.
4. 세 관절점 `a,b,c`의 각도는 `acos(clip(dot(a-b,c-b)/(norm(a-b)*norm(c-b)),-1,1))`이다. 내부 radian 또는 π로 나눈 무차원값 중 하나를 고정하고 문서화한다. 이 명세의 투영 특징은 `angle/π`다.
5. TIP 그룹과 관절 그룹을 따로 유지한다. 두 그룹 중 어느 것도 실제 지면 높이 센서가 아니다.

### 6.3 공통 배율과 국소 왜곡

연속 세 구간의 추정 길이를 `l_i`, 보정 기준 길이를 `b_i`, 손바닥 폭을 `p`, 기준 폭을 `p0`라 한다.

```text
r_i = l_i / b_i
a = median(r_1,r_2,r_3)                 # 손가락 공통 배율
local_error_i = abs(r_i/a - 1)          # 구간별 불일치
shape_error = max(local_error_i)
palm_relative_scale = a / (p/p0)
```

P0의 초기 허용 제안은 `shape_error ≤0.30`, `0.5≤palm_relative_scale≤1.8`이다. 모든 구간이 같은 비율로 늘어난 것과 MCP–PIP 한 구간만 길어지는 것을 분리한다. 이 계산은 ‘진짜 뼈 길이’를 측정하는 절차가 아니라 모델 추정의 일관성 검사다.

root→TIP 직선 거리는 손가락을 펴면서 정상적으로 달라지므로 뼈 길이 불변 조건으로 사용하지 않는다. 국소 왜곡 검사를 낮춰 약지를 통과시키는 대신, 어느 구간의 오차가 지속되는지 중앙값·p95·초과 비율을 보고한다.

### 6.4 시간 필터

현재와 과거 최대 5프레임, 최대 100ms의 causal median을 초기 제안으로 사용한다. 미래 프레임을 보지 않는다. 각 출력에 사용 프레임 수·시간 폭·중앙 관측의 age를 기록한다. 필터 지연을 접촉 시각으로 역보정하지 않는다.

필수 관절 이탈·비유한 값·심각한 국소 왜곡 등 원본 hard failure를 평활화로 숨기지 않는다. 손 ID·특징 존재 마스크·버전 변경·긴 공백에는 필터 이력을 비운다. 원본과 필터 후 품질을 각각 기록한다.

## 7. 보정 알고리즘과 실패 진단

### 7.1 수집 정책

준비 1초 후 수집 3초, 표본이 부족하면 최대 5초까지 연장하는 초기 정책을 제안한다. 최소 유효 45프레임·유효 비율 70%를 함께 요구한다. 60FPS에서 온 180개와 15FPS의 45개가 같은 시간 범위를 나타내도록 시간과 수를 모두 기록한다. 프레임 수를 맞추기 위한 복제는 금지한다.

수집 상태는 `idle → preparing → collecting → validating → passed/failed/cancelled`다. 기준과 들림 사이 또는 수집 중 손/카메라가 바뀌면 해당 요청을 무효화한다. 요청 취소 이후 도착한 결과는 게시하지 않는다.

### 7.2 통계·투영

기준/들림의 각 특징에 robust 중앙값과 `max(1.4826*MAD, IQR/1.349, epsilon)` 잡음을 계산한다. 제외한 outlier의 규칙과 개수를 남긴다. 임의로 두 자세가 잘 갈리는 프레임만 고르는 것은 금지한다.

```text
mu0_j = 기준 중앙값
mu1_j = 들림 중앙값
d_j = mu1_j - mu0_j
sigma_j = max(기준 잡음, 들림 잡음, epsilon)
signal_noise_j = abs(d_j)/sigma_j
w_j = 1/sigma_j²  (선택 기준을 만족하는 특징만; 나머지 0)

group_score(x) = sum(w_j*(x_j-mu0_j)*d_j) / sum(w_j*d_j²)
group_residual = sqrt(sum(w_j*((x_j-mu0_j)-score*d_j)²) / sum(w_j*d_j²))
```

TIP/관절 그룹마다 위 계산을 수행한다. 분모가 epsilon 이하이거나 필수 그룹의 선택 특징이 없으면 보정 실패다. 원본 좌표가 다른 단위여도 잡음 정규화와 선택 정책을 일관되게 적용한다.

초기 제안값은 특징별 SNR≥2.5, 그룹 정규화 분리도 `sqrt(sum(w*d²))≥3`, 기준·들림 점수 잡음 각각 ≤0.30, 각 자세의 길이/신호 결합 통과 비율 ≥70%다. 이들은 검증된 보편 기준이 아니다. 배포 정책에는 최종 채택값·근거 실험을 남긴다.

### 7.3 실패 이유와 조치

| 코드 | 판단 근거 | 사용자·개발 조치 |
|---|---|---|
| insufficient_samples | 유효 수·시간·유효 비율 부족 | 추적 상태를 안내하고 최대 시간 안에서 연장/재시도 |
| indistinguishable_pose | TIP 또는 관절 그룹 분리 부족 | 대상 손가락·손 전체 이동 여부 확인, 자연스러운 두 자세 재수집 |
| unstable_baseline | 기준 점수 변동 과다 | 좌표/추정 잡음과 실제 움직임을 단정하지 말고 수치 제공 |
| unstable_lift | 들림 점수 변동 과다 | 들림 유지 자세·가림·영상 흔들림 점검 |
| lift_geometry_unstable | 신호는 구분되나 길이 통과 비율 미달 | 구간별 왜곡 중앙값/p95/초과 비율 확인 |
| stale_calibration_request | 요청 중 손/장치/epoch 변경 | 결과 폐기, 새 요청 시작 |

진단 필드에는 attempted/used/rejected 프레임 수, 추적 누락, 시간 공백, 특징별 차이/잡음/SNR/선택 가중치, 그룹 점수 분산, 구간별 길이, 통과율, threshold, policy_version을 포함한다. ‘사용자가 흔들었다’처럼 원인이 확정되지 않은 설명을 내지 않는다.

### 7.4 관절 가중치와 약지

끝 관절에 고정 가중치를 크게 주는 가설은 실험으로 비교한다. 기본안은 특징별 SNR·안정성으로 가중치를 결정한다. root 각도만으로 결과를 내거나 distal 신호가 가려졌는데도 높은 가중치를 강제하지 않는다. 엄지·약지·소지는 각 보정값을 사용하되 약한 움직임을 자동 정답으로 만드는 손가락별 예외 규칙은 넣지 않는다.

## 8. 품질 게이트와 unknown

### 8.1 검사 순서

`세션/버전 → 시간/프레임 freshness → 선택 손 연결 → 필수 관절 유한성/가시 범위 → 손바닥 축 → 국소 길이/배율 → 그룹 관측 → 주기 가능 여부`

필수 영상 좌표는 해당 손가락의 네 점과 손바닥 기준축 점들이다. 화면 밖 손목 하나만으로 다른 필수 점이 정상인 손가락 전체를 폐기하지 않는다. 다만 손목이 필요한 특징을 실제 계산하는 설정이라면 그 의존성을 검사해야 한다.

### 8.2 시간 구간

관측 수 비율과 시간 coverage는 별도다. 처리된 프레임의 100%가 유효해도 프레임이 1초간 멈췄다면 정상으로 평가하지 않는다. 마지막 유효 관측이 무한히 유효한 것으로 연장되지 않게 한다.

P0는 각 관측이 다음 관측 또는 `t + max_observation_age_ms`까지만 유효하다고 가정한다. max age 이후는 unknown이다. 무효 관측은 그 시각부터 unknown이며 즉시 주기를 초기화한다. watchdog이 새 프레임 없이도 열린 unknown을 시작/연장한다.

구간은 `[start_ms,end_ms)`이고 열린 구간의 end는 null이다. 보고 창 종료 시 창 끝으로 clip한 스냅샷을 별도 생성한다. 겹치는 이유의 구간은 시간 길이를 중복 합산하지 않는다. 원인 목록은 보존한다. `usable_duration + unknown_duration = 관측 창 길이`여야 하며 시작 전/종료 후 구간을 포함하지 않는다.

초기 `max_observation_age_ms=200`, watchdog tick 100ms, UI stall 경고 500ms를 제안한다. 임계값 사이 구간도 평가 가능성을 정확히 구분한다. B-03용 경계 확장은 별도 analysis exclusion 구간이며 원본 관측 품질을 사후 덮어쓰지 않는다.

## 9. 손가락별 주기 상태 머신

### 9.1 상태·전이

| 상태 | 진입/전이 | 후보 생성 |
|---|---|---|
| WAIT_REST | 시작·손실·버전 변경 후 기준 자세를 안정적으로 확인 | 없음 |
| REST | 유효한 기준, TIP와 관절이 복귀 범위 | 없음 |
| LIFTING | TIP≥lift, 관절≥angle_lift가 함께 관측됨 | 없음 |
| LIFTED | 들림 최소 유지 시간을 만족 | 없음 |
| RETURNING | 두 점수가 복귀 한계 이하로 내려옴 | 최초 복귀 시각과 좌표를 저장, 아직 없음 |
| COOLDOWN | 복귀 유지·이동폭·누적 경로를 확인하고 후보 1개 생성 | 진입 시 한 번만 |

COOLDOWN 후 유효 기준이 유지되면 REST로 돌아간다. 너무 짧은 들림은 REST 또는 WAIT_REST로 돌아가며 누락 이유를 남긴다. 복귀 확인 도중 다시 들리면 RETURNING의 이전 후보 시각을 폐기하고 유효한 들림 구간으로 되돌아간다. 최대 주기 시간 초과·관측 실패 시 WAIT_REST로 초기화한다.

### 9.2 초기 파라미터 제안

| 파라미터 | 제안값 | 의미 |
|---|---:|---|
| tip_lift | 0.55 | 들림 진입 |
| angle_lift | 고정 모드 0.55 | 관절 들림 진입 |
| release / angle_release | 0.20 / 0.20 | 복귀 히스테리시스 |
| min_lift_ms | 60ms | 한 프레임 튐 억제 |
| settle_ms | 40ms | 복귀 유지 확인 |
| cooldown_ms | 100ms | 한 주기 중복 출력 억제 |
| max_gap_ms | 200ms | 공백 시 주기 폐기 |
| max_cycle_ms | 2500ms | 너무 오래 열린 주기 폐기 |
| min_tip_excursion / path | 0.50 / 0.65 | 보정 스케일의 이동폭/누적 경로 |

파라미터 관계(`release<lift`, `angle_release<angle_lift`, 비음수 시간, 유한 수)를 시작 시 검증한다. 인접 프레임 수가 아닌 경과 ms로 시간을 판정한다. 60FPS와 15FPS에 같은 프레임 개수 기준을 쓰지 않는다.

### 9.3 고정 기준과 상대 기준

고정 모드는 저장한 기준의 투영 점수를 그대로 사용한다. 상대 기준 모드는 현재 안정된 기준 점수를 `b_tip,b_angle`로 획득해 `q_tip=score_tip-b_tip`, `q_angle=score_angle-b_angle`을 사용한다. 드리프트·복구 허용은 별도 정책이며 상대 기준이라는 이유로 모든 정지 자세를 기준으로 받지 않는다.

기준 재획득은 유효하고 안정된 ‘기준 쪽’ 관측 구간을 필요로 한다. 들림 유지 자세를 새 기준으로 오인하지 않도록 보정 범위와 잔차를 검사한다. P0의 안정 구간 제안은 160ms·최소 3관측이다. 기준 갱신 자체는 이벤트를 만들지 않는다. 알려진 기준 재사용 최적화는 독립 부정 사례를 통과한 뒤 추가한다.

상대 모드 관절 lift는 예를 들어 `max(0.25, 2.5*기준 angle noise)`를 후보로 비교할 수 있다. 실제 적용값은 calibration/policy에 저장하고 그래프·replay와 동일하게 사용한다. 비교 중에는 모드를 명시하고 시행 중 자동 전환하지 않는다.

## 10. 시각·큐·버퍼·사건

### 10.1 프레임 처리량

- browser clock은 `performance.now()` 계열 단조 시계다. `requestVideoFrameCallback`의 새 프레임 사건을 기본으로 쓰고, 미지원 시 `currentTime` 변경을 확인하는 대체 게이트를 사용한다. [공식 API 설명](https://developer.mozilla.org/en-US/docs/Web/API/HTMLVideoElement/requestVideoFrameCallback)에 따라 compositor 시각과 미디어 시간은 별도 필드로 기록한다.
- 원본 센서 촬영 시각을 아는 것처럼 표시하지 않는다. JPEG 인코딩·네트워크·추론·확정 지연을 분해해 기록한다.
- 앱 큐는 in-flight 1개 + 최신 대기 1개다. 새 프레임이 오면 이전 대기를 교체하고 dropped count를 증가시킨다. 대기 수를 무제한 늘리지 않는다.
- 서버는 같은 세션·tracker를 동시에 실행하지 않는다. 처리 순번을 직렬화한다. timeout 뒤 늦은 응답은 epoch·요청 ID를 검사해 폐기한다.
- 해상도 축소는 원본 종횡비를 유지한다. 기본 추론 입력은 640×480 범위 내 크기로 제안하며 실제 값·축척·원본 크기를 기록한다.

### 10.2 좌표 버퍼

시간 기준 5초, 최대 600관측 중 먼저 도달한 상한을 사용한다. 각 항목은 source frame ID, browser 시각, hand track/finger, image 좌표, geometry/calibration/epoch, 품질을 보존한다. 이미지 바이트는 필수 보관이 아니다.

버퍼 prune 후 후보 프레임이 없으면 `COORDINATE_UNAVAILABLE`이다. 마지막 좌표·다른 손가락·기대 건반으로 대체하지 않는다. P0에서는 좌표 보간을 하지 않는다.

### 10.3 두 종류의 출력 시간

Python에서 계산한 server monotonic 시간은 browser `clock_id`에 직접 넣을 수 없다. 서버는 후보의 `t_ms`·`candidate_frame_id`와 `decision_ready_server_ms`를 반환한다. 브라우저가 유효한 결과를 수신·게시할 때 `emitted_ms=performance.now()`를 한 번 붙인다. 이는 표시/출력 가능 시각이지 접촉 시각이 아니다.

이 시간 확정은 웹 전송 어댑터 책임이며 후보 계산을 JS로 옮기는 것이 아니다. 최종 B-02 사건은 브라우저가 게시 확인한 뒤 B-03에 전달한다. 서버 결정 내용은 cached decision_ref로 검증하고 브라우저가 음번호·품질·후보 시각을 바꾸게 하지 않는다. 동일 사건 재전송은 최초 emitted_ms를 유지한다. [사건 게시 API](CONTRACTS_AND_API.md#6-프레임과-사건-게시)를 따른다.

### 10.4 사건 ID

`candidate_id`는 세션·epoch·track_id·finger·cycle_sequence에 의해 유일하다. `event_id`는 candidate_id로부터 결정적으로 발급하고 음 매핑 결과가 withheld여도 유지한다. 중복 방지 집합은 세션 사건 저장소에 둔다. 사용자의 시행 종료/세션 종료를 기록하고 정책상 보존 기간이 끝나기 전에 임의 삭제하지 않는다.

## 11. 기록과 리플레이

```text
session-export/
  manifest.json       # schema/model/policy/SDK/장치/출처/파일 해시
  session.json
  calibration.json
  geometry.json
  observations.jsonl  # 누락·실패 포함, 원본 좌표와 시각
  quality.jsonl
  candidates.jsonl
  virtual_inputs.jsonl
  labels.jsonl        # 실제 독립 검수한 경우에만 생성
  video.*            # 사용자가 명시적으로 선택한 경우
```

기억한 타건 횟수는 `reported_action_count`로 기록하고 정답 이벤트로 복제하지 않는다. 데이터 출처는 real_capture / annotated_capture / synthetic_fixture / replay로 구분한다. 보정·학습·튜닝·검증 데이터를 사람/세션 기준으로 구분한다.

리플레이는 원래 간격과 누락을 유지해 event-time 기준으로 실행한다. 재생 배속은 화면 속도에만 적용하고 탐지용 t_ms를 바꾸지 않는다. 동일 입력을 고정/상대 정책으로 비교할 때 각 결과를 별도 artifact로 저장한다. 모델 추론을 다시 돌리는 영상 replay와 이미 추정된 관절을 사용하는 landmark replay를 분리한다.

## 12. 오류 경계와 중단

세션 외부 입력·NaN·무한대·잘못된 행렬·지원하지 않는 스키마는 계약 오류다. 관측상 보류와 섞지 않는다. JSON 직렬화는 NaN을 허용하지 않는다. 필수 처리 실패는 `status=error,data=null`; 의미 있는 빈 결과·관측 보류의 정상 판정은 success다.

모델 로딩은 프레임마다 재시도하지 않는다. 일시 오류는 실행 정책의 유한 재시도 안에서 처리하고, 실패 시 사용자가 다시 시도할 수 있게 한다. WS 단절 동안 프리뷰는 유지할 수 있지만 신규 입력은 차단한다. 종료 시 큐·모델 세션·타이머·오디오·열린 파일을 정리하고 이전 응답을 새 세션에 적용하지 않는다.
