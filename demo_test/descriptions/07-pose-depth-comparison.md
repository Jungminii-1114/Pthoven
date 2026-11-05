# 07 · 관절·깊이·결합 비교 실험

[목차](README.md) · [코드](../models/depth-detector.mjs)

## 비교하려는 질문

1. 관절 기준선에 깊이 신호를 추가하면 공중 탭·손 전체 이동의 오입력이 줄어드는가?
2. 관절에서 놓친 정상 탭을 깊이로 더 찾을 수 있는가?
3. 개선처럼 보이는 결과가 낮은 처리 FPS·과도한 보류 때문은 아닌가?

이 구현은 위 가설을 비교할 장치다. 가중 결합이 단독 모델보다 우수하다는 결과를 전제하지 않는다.

## 네 가지 경로

| method | 관측 | 상태 머신의 허용 공백 | 해석 |
| --- | --- | --- | --- |
| `pose_full` | 모든 처리된 관절 프레임 | 150ms | 실제 관절 기준선 |
| `pose_matched` | 깊이와 같은 프레임의 관절 | 기본 600ms | 깊이/결합과 비교할 기준선 |
| `depth` | 같은 프레임의 깊이 특징 | 기본 600ms | 깊이 스칼라의 판정력 |
| `fusion` | 같은 프레임의 관절·깊이 점수 | 기본 600ms | 가중 결합 가설 |

`depth`도 손 위치·좌우 손 선택을 위한 MediaPipe 좌표를 사용한다. MediaPipe와 완전히 독립된 손 검출 모델을 뜻하지 않는다.

## 결합 방식

관절 점수를 `p`, 깊이 점수를 `d`, 관절 비중을 `w`라고 하면:

```text
fusion_valid = pose_valid AND depth_valid
fusion_score = w×p + (1-w)×d
```

기본 w=0.5다. 두 보정값이 개인별 기준≈0, 들림≈1을 갖도록 만든 뒤 결합한다. 둘 중 하나가 invalid면 결합도 invalid이며 한쪽 값을 0으로 대체하지 않는다.

이 방식은 **가중 점수에 대한 별도 FSM**이다. 두 모델이 각각 타건 이벤트를 만들고 합의하는 방식, 관절 후보를 깊이로 나중에 승인하는 방식, 확률적 sensor fusion과는 다르다. 한쪽 점수가 커서 임계값을 넘는 경우가 가능하므로 모든 오탐을 차단하지 않는다.

## 보정값과 초기화

```javascript
const engine = new DepthComparison({
  index: {pose: poseCalibration, depth: depthCalibration}
}, {poseWeight: 0.5, maxGapMs: 600});
```

손가락마다 관절 보정과 깊이 보정을 함께 보관한다. 깊이 보정이 실패하면 depth=null과 이유를 남길 수 있으며 관절 경로만 동작한다. 설정을 바꾸거나 새 시행을 시작할 때는 새 인스턴스로 이전 동작 주기를 제거한다.

`DepthComparison.calibrate()`는 `{world,depth}` 형식의 기준·들림 기록에서 두 보정을 구성하는 편의 함수다. 개별 `calibrate()`와 `calibrateDepth()`를 호출해도 된다.

## 입력 호출 규칙

```javascript
// 관절 전체 프레임 경로
const full = engine.update(tMs, world, null, points, emittedMs, true);

// 깊이 입력에 사용한 바로 그 프레임의 관절·깊이·영상 좌표
const matched = engine.update(tMs, world, depthResult, points, receivedMs, false);
```

- world는 21개 world landmarks, points는 같은 프레임의 `{x,y,z}` 정규화 영상 좌표다.
- 각 경로 안에서 tMs가 증가해야 한다. 같은 경로에 중복 응답을 전달하지 않는다.
- 관절 프레임이 계속 들어오더라도 matched 경로는 요청 당시 관절을 사용한다. 응답 시점의 최신 관절로 바꾸지 않는다.
- 새 세션의 응답과 이전 세션의 늦은 응답을 구분하는 책임은 상위 수집기에 있다.
- 너무 늦은 관측·관절 누락은 상태 머신을 재준비시킨다. 비동기 처리를 쓰더라도 무한 요청 대기열을 만들지 않는다.

## 출력

`events[method]`는 finger·method·tip·후보 시각·출력 시각을 가진 배열이다. `signals[method][finger]`는 score·valid·phase다. tip은 가상 건반 좌표 매핑에 사용할 수 있지만 이 패키지는 게임·악보·소리 출력을 구현하지 않는다.

`receivedMs`로 emitted_ms를 갱신해 비동기 처리 지연을 포함한다. 후보 시각은 이전 복귀 관측 시각이므로, 단순히 현재 프레임의 추론 시간과 같지 않다. 실제 접촉 대비 지연에는 독립 정답이 필요하다.

## 검증과 실험 설계

테스트는 관절 없이 깊이만 유효한 경우 결합 보류, 이상적 완전 주기에서 각 경로의 1회 후보, 기준·들림이 구분되지 않는 깊이 보정을 검사한다. 합성 관절·수치 깊이 신호를 사용하며 실제 카메라 정확도가 아니다.

실촬영에서는 동일 시행에서 네 경로를 기록한다. 깊이의 추가 정보를 보려면 우선 pose_matched와 비교한다. 제품 채택은 pose_full과의 누락·지연 차이까지 확인한다. 깊이 관측이 거의 없어서 오입력이 0인 결과를 개선으로 판단하지 않는다.

개발 데이터에서 w·임계값을 정하고 평가 데이터에서는 고정한다. 정상/반복/정지/공중/끌기/전체 손 이동 조건과 새 사용자에서 [08의 평가](08-evaluation-and-replay.md)를 수행한다.
