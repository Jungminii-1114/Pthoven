# Pthoven B-01·B-02 · 세션 준비·보정과 가상 연주 입력

카메라 선택 → 원본 RGB 영상 → **Python MediaPipe 관측** → 표시 기하 → 기준/들림 보정 → 품질 확인 → `SessionContext + CalibrationBundle + QualityAssessment`를 제공한다.

B-01(세션 준비·보정)과 B-02(실시간 관측→손가락 주기→가상 건반 입력)의 로컬 실행 환경이다. score following·Agent는 B-03 이후 책임이다. 보정 통과와 가상 입력은 실제 타건 정확도나 물리 접촉 검증을 뜻하지 않는다(`contact_verified=false`). B-02 구현 기록은 [docs/2026-11-25-b02-implementation.md](docs/2026-11-25-b02-implementation.md)에 있다.

[B-01 설계](../docs/plan/02-big-nodes/B-01.md) · [B-01·B-02 PRD](../docs/prd/B-01-B-02/PRD.md)

## 설치와 실행

저장소의 `vision` 폴더에서 Python 3.10으로 설치하는 것을 기준으로 한다. MediaPipe 0.10.21 wheel을 지원하는 OS/Python 조합이 필요하다.

```bash
cd vision   # 저장소 루트 기준
python3.10 -m venv .venv
.venv/bin/python -m pip install -e ../demo_test/python_tap -e '.[test]'
.venv/bin/python -m pthoven_vision.api --port 8770
```

브라우저: **http://127.0.0.1:8770/**. 종료는 Ctrl+C. 포트 충돌 시 `--port 8771`로 바꾸고 접속 주소도 바꾼다. 각 팀원은 자기 컴퓨터에서 실행한다. 위 `cd`는 저장소 루트에서 실행한다.

Windows PowerShell에서는 `py -3.10 -m venv .venv`, `.venv\Scripts\python.exe -m pip install -e ../demo_test/python_tap -e ".[test]"`, `.venv\Scripts\python.exe -m pthoven_vision.api --port 8770`을 사용한다. Windows/Linux의 실제 카메라·모델 실행은 별도 확인이 필요하다.

다음은 macOS / Linux의 기본 설치 명령이다.

```bash
python3.10 -m venv .venv
.venv/bin/python -m pip install -e ../demo_test/python_tap -e ".[test]"
.venv/bin/python -m pthoven_vision.api --port 8770
```

검증 당시 macOS arm64 / Python 3.10 환경 버전은 `requirements-tested-macos-py310.txt`에 기록했다. 동일 환경 재현 시 해당 파일을 먼저 설치하고 로컬 두 패키지를 설치한다. 다른 OS에서는 MediaPipe wheel 지원 여부를 먼저 확인한다.

## 첫 실행 순서

1. **추적 모델 준비**를 누른다. 최초 한 번 Google 공식 모델 약 7.8MB를 다운로드한다. 고정 모델 SHA256을 확인하고 준비 상태를 표시한다. 인터넷이 필요하다.
2. **카메라 권한 허용** 후 목록에서 사용할 iPhone 연속성 카메라 또는 웹캠을 선택해 연결한다. iPhone은 먼저 OS의 카메라 목록에 보여야 한다. 선택한 장치가 실패하면 다른 장치로 자동 바꾸지 않는다.
3. 사용할 신체의 좌/우 손과 손가락을 고른다. 기본은 오른손 검지다. 여러 손가락을 쓰려면 기준 수집 전에 활성 부분집합을 선택한다.
4. 프리뷰 방향과 가상 건반 가이드를 확인한다. 좌우·상하 반전은 기본 꺼짐이다. 표시 반전은 원본 추론 영상에 적용하지 않는다.
5. 손을 자연스럽게 내려놓고 **기준 자세 수집**을 누른다. 준비 1초 후 3초 수집, 표본 부족 시 최대 5초까지 수집한다. 최소 유효 45프레임·유효 비율 70%를 포함한 정책으로 검사한다.
6. 대상 손가락을 자연스럽게 들고 **들림 수집**을 수행한다. 각 활성 손가락에서 반복한다. 보정 실패 진단을 확인하고 다시 수집할 수 있다.
7. 보정한 손가락을 기준 자세로 안정되게 둔 뒤 **입력 준비 확인**을 누른다. 모든 활성 손가락의 템플릿·신선한 관측·안정 기준이 있어야 ready가 된다.
8. **입력 점검 시작**(B-02)을 누르고 손가락을 들었다 내린다. 복귀 순간의 손끝이 놓인 가상 건반이 반응하고 짧은 음이 난다. 경계·영역 밖은 음 없이 보류로 표시한다. **입력 점검 종료** 후 다시 시작하려면 입력 준비 확인부터 다시 한다.
9. **보정 기록 JSON 저장**으로 진단·모델·수집 원본 관절·최근 품질 구간을 내려받는다. 영상 이미지는 저장하지 않는다.

기준 자세 재저장은 같은 기준에 연결된 기존 들림 템플릿을 무효화한다. 손/활성 손가락/카메라를 바꾸면 웹에서 새 세션을 시작한다. 표시 크기·반전 변경은 새 geometry/epoch를 발급하고 진행 중 수집을 취소한다.

## 개발 구조

| 위치 | 역할 |
|---|---|
| `src/pthoven_vision/contracts.py`, `policy.py` | 엄격한 요청·결과·정책 |
| `session.py` | B-01 오케스트레이터, 수집·취소·원자 보정 발급·준비 확인 |
| `geometry.py` | contain/cover·반전·키 영역·양방향 변환 |
| `pose.py` | robust 보정·특징 선택·TIP/관절 투영·길이/가시 범위 검사 |
| `tracker.py`, `worker.py` | 공식 모델·Python VIDEO 추론·별도 프로세스 격리 |
| `quality.py` | freshness·유효/unknown 시간 구간·분모 |
| `b02.py` | B-02 S-05(손가락 주기, `pthoven_tap` 상대 기준 판정기 재사용)·S-06(후보 프레임 좌표→가상 건반)·중복 억제·출력 시각 결합 |
| `api.py` | 로컬 REST·최신 프레임 HTTP 전송·watchdog |
| `replay.py` | 내보낸 보정 자료의 재계산 |
| `web/` | 장치/권한·새 프레임 운반·고정 프리뷰·진단 |
| `tests/` | 수학·계약·수집·세션·API 검증 |

기존 `demo_test/python_tap`의 Python 특징 추출·길이 수학을 의존성으로 재사용한다. 기존 실험 엔진 파일과 임계값을 변경하지 않는다. 새 보정은 `pose.py`의 robust 중앙값/MAD/IQR·SNR 정책을 사용하며 모델 형식은 기존 measure와 비교 가능한 형식이다.

## API

공통 prefix는 `/api/vision/v1`이며 `/docs`에서 실제 OpenAPI를 확인할 수 있다.

| 요청 | 역할 |
|---|---|
| GET `/health`, POST `/model/prepare` | 준비 상태 조회·모델 준비 |
| POST `/sessions`, GET `/sessions/{id}` | 세션 생성·실제 상태 |
| POST `/sessions/{id}/device` | 선택 장치의 실제 상태 보고. 클라이언트 tracker-ready 주장은 받지 않음 |
| PUT `/sessions/{id}/geometry` | 좌표 검증·geometry/epoch 갱신 |
| POST `/sessions/{id}/frames` | JPEG 원본 프레임 + browser 시각/버전 → Python 관측·품질·수집 진행 |
| POST `/sessions/{id}/calibration/captures` | rest/lift 수집 요청 |
| POST `.../captures/{capture_id}/finish` | 수집 검증·안전한 결과 발급 |
| DELETE `.../captures/{capture_id}?command_epoch=...` | 수집 취소 |
| POST `/sessions/{id}/recalibrate` | 평가 보류·새 기준 요청. 보정 완료를 뜻하지 않음 |
| POST `/sessions/{id}/ready` | 유효 템플릿·신선한 기준 자세 검사 |
| POST `/sessions/{id}/start` | B-02 입력 실행 시작(ready 상태에서만). 프레임 응답의 `input.proposals`로 가상 입력 제안 반환 |
| POST `/sessions/{id}/pause` | 입력 실행 종료. 진행 주기·미게시 제안 폐기, epoch 증가 |
| POST `/sessions/{id}/events/ack` | `decision_ref` + 사건별 브라우저 `emitted_ms` → 최종 `virtual-input.v1` 사건. 재전송은 최초 값 유지 |
| POST `/sessions/{id}/exports`, POST `/sessions/{id}/close` | JSON 내보내기·종료 |

기하·보정 성공 등의 변경 결과에서 새 command_epoch를 받아 이후 요청에 사용한다. 과거 epoch·시계·중복/역순 frame은 거절한다. 수집 시작 전 대기 중이던 프레임에는 새 capture_id를 소급해 붙이지 않는다.

생성·기하·수집 시작·수집 완료 요청에 `X-Request-ID`를 사용하면 같은 요청 ID/본문의 재전송을 중복 적용하지 않는다. 다른 본문 재사용은 충돌이다. 요청 뒤 세션 세대가 변경되면 오래된 결과를 재적용하지 않는다.

## 기록·재생

```bash
.venv/bin/python -m pthoven_vision.replay ~/Downloads/b01-calibration-세션ID.json --output outputs/replayed.json
```

기록에 `input_runs`(B-02 실행)가 있으면 같은 명령이 각 실행의 원본 관절(누락 포함)을 같은 순서로 다시 판정한다(landmark replay). 결과의 `same_candidates`·`same_mapping`으로 후보 시각·건반 매핑의 재현성을 확인한다. 실행당 관측은 최대 3,600개를 보관하며, 상한을 넘은 실행은 비교 결과를 `null`로 둔다.

`세션ID`는 내려받은 파일명의 실제 ID로 바꾼다. 실제 파일명은 `b01-calibration-session-…json` 형식이며 브라우저 저장 결과를 따른다. `b01-export.v1`만 입력으로 받는다. 기존 `pthoven-python-tap-capture*.json`는 실험용 스키마이므로 자동 추측해서 읽지 않는다. 새 수집 파일에는 phase·finger·baseline_ref·frame_id·browser t_ms·원본 관절·실패 사유가 들어 있다.

재생은 현재 기준에 연결된 성공한 들림 시행을 찾아 같은 정책으로 다시 fit한다. `same_model_parameters`는 수치 재현성 확인이며 실제 타건 정확도가 아니다. 최근 관측 로그는 최대 600개, 각 수집은 최대 400개로 제한한다. 세션은 메모리에 있으므로 서버 재시작 전 필요한 JSON을 저장한다.

## 테스트

```bash
.venv/bin/python -m unittest discover -s tests -v
node --check web/app.js
node --test tests/test_web.mjs
```

수학/합성 테스트 통과, 빈 영상의 실제 모델 호출 성공, 실제 사람의 보정 성공률은 서로 다른 검증이다. 카메라·손가락별 현장 성능은 별도 테스트해야 한다.

## PRD와 구현 범위

- B01-01~12에 대응하는 세션·장치 상태·기하·수집·보정·취소·준비 결과를 구현한다. 브라우저 장치 선택/권한은 사용자 OS/브라우저가 수행한다.
- S-02·S-04는 B-01과 B-02가 공유한다. B-02의 S-05·S-06·사건 게시 ACK는 `b02.py`와 `/start`·`/pause`·`/events/ack`로 구현했다. Agent는 구현 범위에 넣지 않는다.
- 연결은 REST + JPEG HTTP 한 요청 처리 중/최신 대기 방식이다. PRD의 WebSocket 전송은 후속 범위이며, 의미 계약(proposal → 브라우저 emitted_ms → 최종 사건)은 REST로 동일하게 지킨다. B-03 소비자 연결은 아직 없고 최종 사건은 세션 기록(`input_runs[].virtual_inputs`)에 보존한다.
- 연습곡은 계약 시험용 `builtin-ceg.v1` 하나만 허용한다. 곡 카탈로그·nASAP/Mutopia 로더는 후속 어댑터다.
- 세션당 수집 64개, 동시 보유 세션 32개 등 메모리 상한을 둔다. 서버 재시작은 메모리 세션을 지운다. 실제 사용자 운영용 영속 저장·원격 서버 인증은 후속 범위다.
- 학습된 접촉 모델이 아니다. 모든 결과는 `contact_verified=false`다. root→TIP 거리를 뼈 길이로 취급하지 않고 공통 배율·구간 왜곡을 구분한다.

## 모델 준비 오류

macOS의 제한된 실행 환경에서는 CPU 추론을 선택해도 MediaPipe 전처리 그래픽 서비스가 필요할 수 있다. 그래픽 서비스 접근이 차단되면 준비 오류를 반환하고 프리뷰는 유지한다. 모델은 별도 프로세스에서 실행하므로 네이티브 SDK 실패가 서버를 종료시키지 않는다. 일반 로그인 세션의 터미널에서 위 서버 명령으로 실행한다.

`PTHOVEN_MODEL_PATH`로 모델 경로를 지정할 수 있지만 고정 SHA256 검사는 유지한다. 모델 파일·가상환경·원본 관절 기록은 Git에서 제외한다.
