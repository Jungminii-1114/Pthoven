# 06 · 책상 대비 상대 깊이 특징과 개인별 보정

[목차](README.md) · [특징 코드](../models/depth.py) · [보정·점수 코드](../models/depth-detector.mjs)

## 목적과 가설

전역 손 기준 좌표가 제거해버리는 손·책상 관계를 깊이 모델의 보조 신호로 관찰한다. 서로 다른 프레임의 상대 깊이 raw 값을 직접 비교하지 않고, 같은 프레임의 책상 기준과 깊이 범위로 정규화한다.

이 값은 실제 책상 평면까지의 수직 거리나 접촉 확률이 아니다. 충분한 구분력이 있는지는 실제 손의 정상·공중 탭에서 실험해야 한다.

## 입력과 기준점 유효성

`extract_signals(depth,landmarks,anchors)`는 유한한 2D float 깊이 맵과 21개 정규화 영상 좌표, 책상 기준점 3~8개를 받는다.

- 기준점은 손이 지나가지 않는 책상의 넓은 영역에 배치한다. 4개 이상을 권장한다.
- `[u,v,1]` 행렬의 rank가 3 미만이거나 condition number가 100을 넘으면 거부한다.
- 손 관절의 2D convex hull에 기준점이 들어가거나 정규화 거리 0.035 이내로 가까우면 기준점 가림으로 보류한다.

가림 검사는 관절 hull 기반 휴리스틱이다. 실제 손 영역 segmentation이 아니므로 가림을 놓치거나 과도하게 보류할 수 있다. 정규화 x/y의 거리라 영상 종횡비 영향도 받는다.

## 영상 좌표에서 깊이 평면 맞추기

각 기준점 주변 반경 3픽셀 패치의 깊이 중앙값을 구한다. 패치는 원본 크기로 복원한 맵에서 최대 7×7이며 경계에서는 잘린다.

기준점의 영상 위치 `(uᵢ,vᵢ)`와 깊이 중앙값 `dᵢ`를 이용해 최소제곱으로 다음 식을 맞춘다.

```text
d_desk(u,v) = a×u + b×v + c
[a,b,c] = least_squares([[uᵢ,vᵢ,1]], [dᵢ])
```

이는 **영상상의 inverse-depth 평면**이다. 카메라 내부 파라미터를 이용해 복원한 Euclidean 3D 평면이 아니다. `desk_fit_residual`은 맞춤 잔차를 기록하며, 현재 잔차 자체에 별도 거부 임계값을 적용하지 않는다.

## 손끝 contrast

손가락 TIP 인덱스 4/8/12/16/20의 패치 중앙값을 사용한다.

```text
scale = percentile95(depth)-percentile5(depth)
contrast = (depth_tip-d_desk(u_tip,v_tip)) / scale
```

scale≤1e-5 또는 기준점 가림이면 invalid다. 손끝이 영상 경계 1% 안쪽에 충분히 들어와 있지 않으면 해당 손가락을 보류한다.

전체 맵이 `D' = αD+β (α>0)`처럼 바뀌는 이상적 경우에는 평면과 깊이 차이도 같은 scale/shift를 받아 contrast가 유지된다. 이 성질을 테스트하지만 공간적으로 다르게 왜곡되는 모델 오류까지 제거하지는 않는다.

손가락의 윗면은 접촉해도 책상보다 높다. 따라서 contrast=0을 접촉 기준으로 두지 않고 개인별 두 자세를 보정한다.

## 개인별 깊이 보정

`calibrateDepth(rest,lift,finger)`는 각 기록의 `depth.fingers[finger]`에서 valid하고 유한한 contrast만 선택한다. 각 자세에서 5개 이상 필요하다.

```text
base = median(rest)
raised = median(lift)
delta = raised-base
noise = max(median(|rest-base|), median(|lift-raised|))
```

`abs(delta) < max(0.035,4×noise)`이면 두 자세가 충분히 분리되지 않는 것으로 보고 보정을 거부한다. noise는 스케일 보정하지 않은 MAD 형태이며 표준편차나 확률이 아니다.

실행 중에는 `score=(contrast-base)/delta`로 계산한다. delta의 부호를 유지하므로 들림에서 contrast가 줄어드는 경우도 처리한다. `-0.5≤score≤2`를 벗어나면 보류한다.

## 출력과 결합

`extract_signals`는 손가락별 contrast·tip·valid, 평면 계수, 기준점 값, 깊이 분위 범위, 잔차와 보류 이유를 반환한다. `depthSignal`은 보정된 score·valid만 반환한다.

깊이 보정에 실패해도 관절 기준선은 실행할 수 있다. 이때 depth·fusion은 유효한 입력으로 간주하지 않는다. 실제 이벤트는 [07의 공통 상태 머신](07-pose-depth-comparison.md)이 만든다.

## 검증과 실패 사례

양의 scale/shift 불변성, 기준점 가림, 일직선 기준점, 비유한 맵, 기준/들림 구분 실패, delta 부호 처리를 검사한다.

작은 손끝 영역의 경계 혼합, 낮은 입력 해상도, 책상 반사·그림자, 모델의 프레임 간 왜곡, 손 전체 이동이 실험 위험 요인이다. 294/518 해상도를 바꿀 때는 새로 보정하고 속도와 품질을 함께 기록한다. 이 정규화가 실제 접촉 정확도를 향상시켰다는 결론은 독립 라벨 평가 후에만 낸다.
