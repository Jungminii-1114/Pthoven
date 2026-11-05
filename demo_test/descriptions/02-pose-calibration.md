# 02 · 개인별 관절 보정과 품질 판정

[목차](README.md) · [기준선 코드](../models/tap-detector.mjs) · [비교 코드](../models/depth-detector.mjs)

## 목적과 가설

사람마다 손 크기·기준 자세·들림 모양이 다르므로, 하나의 고정 관절각 대신 개인별 기준→들림 방향에 대한 변화량을 사용한다. 이는 두 자세의 통계를 저장하는 보정이며 신경망 학습이나 접촉 확률 추정이 아니다.

## 입력과 기준 저장

`calibrate(restFrames, liftFrames, finger)`에 같은 손의 기준 자세와 선택 손가락의 들림 자세를 각각 5프레임 이상 제공한다. 실제 수집에서는 충분히 안정된 짧은 구간을 사용하고, 자세가 바뀌기 전후를 섞지 않는다.

각 프레임에서 [01의 5차원 특징](01-pose-features.md)을 계산한다.

```text
base = mean(기준 특징)
raised = mean(들림 특징)
delta = raised-base
signal = ||delta||
jitter = sqrt(mean(||기준 특징-base||²))
```

마디 길이는 기준 프레임들의 평균을 저장한다.

## 보정 실패 기준

| 조건 | 처리 |
| --- | --- |
| 잘못된 손가락 이름·각 자세 5프레임 미만 | 보정 예외 |
| `signal < 0.06` | 두 자세가 너무 유사하므로 보정 거부 |
| `jitter > 0.3 × signal` | 기준 자세가 불안정하므로 보정 거부 |
| 특징 계산 실패 | 보정 예외 |

0.06과 0.3은 초기 실험 임계값이다. 실제 사용자 보정 성공률·오탐을 확인한 뒤 개발 세션에서 조정한다.

## 실행 중 점수

현재 특징을 `f`라고 할 때 기준→들림 방향에 대한 투영을 계산한다.

```text
score = ((f-base)·delta) / (delta·delta)
residual = ||(f-base)-score×delta|| / signal
length_error = max(|현재 마디 길이 / 기준 길이 - 1|)
```

`score≈0`은 저장한 기준 자세 부근, `score≈1`은 저장한 들림 부근을 뜻한다. 다른 방향의 움직임은 residual로 검사한다. score는 확률이 아니며 mm 단위도 아니다.

관측이 다음 조건을 모두 만족해야 유효하다.

- 좌표·특징 계산 성공
- `length_error ≤ 0.30`
- `residual ≤ 0.80`
- `-0.5 ≤ score ≤ 2`

벡터를 손 기준 좌표로 계산해도 손가락 ID 오류·가림·이웃 손가락의 동반 움직임이 남을 수 있다. 길이·잔차 검사는 이런 오류의 일부만 걸러낸다.

## 두 코드 경로

`PoseTapDetector`는 보정값·손가락별 상태 머신을 보관하고 `update(t,world)`에서 점수와 이벤트를 함께 반환한다.

`poseSignal(world,model,finger)`는 비교 실험을 위해 같은 점수·품질 판정을 분리한다. 실제 이벤트는 `DepthComparison`의 상태 머신이 만든다.

```javascript
const model = calibrate(restFrames, liftFrames, 'index');
const detector = new PoseTapDetector();
detector.setCalibration('index', model);
const {events, signals} = detector.update(tMs, worldLandmarks);
```

`signals.index`의 score, valid, length_error, residual, phase를 확인한다. 비교용 `poseSignal`은 score와 valid만 제공한다.

## 검증과 운영 규칙

테스트는 유사 자세 거부, 불안정 기준 거부, 마디 길이 이상치, 다섯 손가락·동시 동작을 확인한다. 실제 MediaPipe에서의 정확도나 사용자 간 일반화를 입증하지 않는다.

카메라 시점·선택한 손·사용자의 기준 자세가 바뀌면 다시 보정한다. 가림 구간을 기준 프레임에 넣지 않는다. 보정에 사용한 데이터와 평가 시도의 이벤트 정답은 구분한다. 다음 단계는 [03의 상태 머신](03-finger-state-machine.md)이다.
