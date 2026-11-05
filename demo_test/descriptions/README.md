# 실험 로직 description 목차

[실험 코드 안내](../README.md)

이 문서들은 **현재 배포하는 알고리즘이 무엇을 입력받아 어떻게 계산하는지**를 설명한다. 기존 팀 설계 문서의 Big/Small Node 실행 계획과는 목적이 다르다. 성능 수치 대신 계산식, 오류 처리, 검증 범위를 설명한다.

| 순서 | 설명 | 주요 함수·클래스 |
| --- | --- | --- |
| [01 · 손 기준 좌표·관절 특징](01-pose-features.md) | 전역 손 이동을 제거하고 손가락 특징을 구성 | `features`, `angle` |
| [02 · 개인별 관절 보정](02-pose-calibration.md) | 기준·들림 차이와 이상치로 점수·유효성을 계산 | `calibrate`, `PoseTapDetector`, `poseSignal` |
| [03 · 손가락별 상태 머신](03-finger-state-machine.md) | 충분한 들림 뒤 복귀한 동작을 한 번만 출력 | `TapMachine` |
| [04 · 손등 영상 특징](04-dorsal-skin-features.md) | Optical Flow·국소 변형·밝기 변화 추출 | `skin_motion`, `analyze_skin.py` |
| [05 · Depth 모델 추론](05-depth-inference.md) | 고정 모델 로딩·전처리·추론·원본 크기 복원 | `DepthService`, `model_input_shape` |
| [06 · 책상 대비 깊이 신호](06-depth-signal.md) | 깊이 평면 보정·정규화·기준/들림 보정 | `extract_signals`, `calibrateDepth`, `depthSignal` |
| [07 · 관절·깊이 결합 비교](07-pose-depth-comparison.md) | 동일 프레임에서 세 종류의 점수와 FSM 비교 | `DepthComparison` |
| [08 · 이벤트 평가·재생](08-evaluation-and-replay.md) | 정답 일대일 매칭·불명 구간·조건별 집계 | `evaluateEvents`, `evaluateTrial`, CLI |
| [09 · 데이터 계약](09-data-contracts.md) | 좌표·시각·보정·v1/v2 기록의 연결 | 전체 모듈 |

읽는 순서: **01→02→03**으로 관절 기준선을 이해하고, **05→06→07**에서 깊이 비교를 읽는다. **04**는 현재 판정과 독립적인 특징 추출 실험이다. 실제 비교 결과를 해석하기 전에는 **08·09**를 확인한다.

공통 원칙:

- 출력 후보와 실제 물리 접촉을 구분한다.
- 정답 미지정은 무타건이 아니다.
- 모델의 보류를 사용자 실수와 섞지 않는다.
- 합성 데이터 검증과 실촬영 정확도를 구분한다.
- 모든 임계값은 초기 실험값이며 검증된 최적값으로 표현하지 않는다.
