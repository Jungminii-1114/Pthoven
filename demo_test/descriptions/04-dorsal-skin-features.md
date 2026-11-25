# 04 · 손등 Optical Flow와 영상상 변형 특징

[목차](README.md) · [특징 코드](../models/skin_features.py) · [기록 처리 CLI](../scripts/analyze_skin.py)

## 실험 목적

관절 좌표가 놓치는 작은 손등 움직임을 추가 관측할 수 있는지 확인한다. 손끝이 가려질 때 손등의 피부·힘줄 주변 변화가 단서가 될 가능성을 탐색한다.

현재 구현은 **특징 추출기**다. 손가락 분류기·접촉 분류기·피부/관절 결합 학습 모델은 포함하지 않는다.

## 입력과 전처리 책임

`skin_motion(previous,current)`에 같은 크기의 2D grayscale 패치 두 장을 전달한다.

- 각 변 최소 16픽셀, 동일 shape, 유한한 픽셀값.
- 0~255로 제한한 뒤 uint8로 변환한다.
- 손등 영역 검출·잘라내기·손 기준 정렬은 상위 수집기의 책임이다. 이 함수가 원본 전체 영상에서 손등을 자동 검출하지 않는다.
- 정렬된 48×48 등 같은 영역을 기록하는 것이 권장된다. 단순 정렬로 3D 자세·원근 변화가 완전히 사라지지는 않는다.

## 계산 과정

1. 두 패치 중 어느 하나라도 밝기 표준편차가 5 미만이면 텍스처 부족으로 보류한다.
2. Farneback dense Optical Flow를 계산한다.
3. 전체 흐름의 성분별 중앙값을 전역 이동으로 추정한다.
4. 전역 흐름을 뺀 residual flow에서 국소 이동과 공간 미분을 계산한다.
5. 영상상 변형 크기와 평균 밝기 변화량을 반환한다.

Farneback 설정은 `pyr_scale=0.5`, `levels=3`, `winsize=15`, `iterations=3`, `poly_n=5`, `poly_sigma=1.2`, `flags=0`이다.

```text
global_flow = componentwise median(flow)
residual = flow-global_flow
shear = (∂u/∂y + ∂v/∂x) / 2
apparent_strain = mean(sqrt((∂u/∂x)² + (∂v/∂y)² + 2×shear²))
local_flow_px = mean(||residual||)
brightness_shift = mean(current)-mean(previous)
```

## 출력

| 필드 | 의미 |
| --- | --- |
| `valid` | 충분한 영상 텍스처가 있었는가 |
| `global_flow_px` | 패치 전체의 대표적인 영상 이동 |
| `local_flow_px` | 전역 이동 제거 후 평균 국소 이동 |
| `apparent_strain` | 잔여 흐름의 공간 변화량 |
| `brightness_shift` | 노출·조명 변화 등이 섞인 평균 밝기 변화 |
| `contact_verified` | 항상 false |

텍스처 부족은 `{valid:false, reason:"insufficient_texture"}`를 반환한다. 잘못된 크기·비유한 값은 예외다. invalid 결과에는 모든 수치 필드가 있다고 가정하지 않는다.

**이동량은 프레임 쌍당 값이며 속도가 아니다.** FPS·dt가 다른 기록을 그대로 비교하지 않는다. CLI는 `dt_ms`를 함께 기록하지만 속도로 자동 정규화하지 않는다. apparent_strain도 실제 피부의 물리 변형률·힘 측정값이 아니다.

## 파일 단위 분석

```bash
python scripts/analyze_skin.py data/skin-capture.json --output outputs/skin.json
```

schema v1의 각 프레임에서 `skin_patch` JPEG data URI를 읽는다. 이미지가 없거나 디코딩할 수 없으면 연결을 끊는다. 인접 샘플의 간격이 0보다 크고 150ms 이하인 경우만 계산한다. 조건·사용자·세션별 특징을 JSON으로 저장한다. 출력의 evidence_type은 `skin_feature_extraction_only`다.

## 검증과 다음 실험

`tests/test_skin.py`는 고정 텍스처의 작은 흐름, 무텍스처 보류, 합성 비강체 변형의 특징 증가, 크기 오류를 검사한다. 실제 피부 변화나 타건 분류 정확도 검증이 아니다.

추가 모델을 학습하려면 동일 사용자·세션 분할에서 **관절만 / 피부만 / 둘의 결합**을 비교해야 한다. 이때 원본 정렬·조명 변화·패치 품질을 함께 기록한다. 특징 추출 성공만으로 정확도가 개선되었다고 판단하지 않는다.

## 참고 연구와 차용 범위

- [Opisthenar](https://juyounglee.net/projects/opisthenar)
- [Back-Hand-Pose / DorsalNet](https://www.vogue.cs.titech.ac.jp/projects/digitalsports/backhandpose)
- [DeltaDorsal](https://yangzhang.dev/research/DeltaDorsal/DeltaDorsal.pdf)

이 연구들은 손등 영상 활용의 참고다. 촬영 시점·센서·과제가 다르며 논문의 네트워크를 이 코드에서 재현한 것은 아니다. 실제 계산은 [OpenCV Farneback](https://docs.opencv.org/4.x/d4/dee/tutorial_optical_flow.html)을 이용한다.
