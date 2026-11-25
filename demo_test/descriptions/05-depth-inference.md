# 05 · Depth Anything V2 Small 로딩과 단일 프레임 추론

[목차](README.md) · [모델 코드](../models/depth.py) · [웹 없는 실행 CLI](../scripts/run_depth.py)

## 역할

일반 RGB 이미지에서 상대 깊이를 추정하고, 원본 크기의 깊이 맵에서 손끝·책상 특징을 계산한다. 손가락의 물리 접촉을 바로 분류하는 모델이나 자체 학습 모델이 아니다.

## 모델과 실행 환경

| 항목 | 설정 |
| --- | --- |
| 모델 | `depth-anything/Depth-Anything-V2-Small-hf` |
| revision | `5426e4f0f36572d16453bbda7a8389317b1bef99` |
| 로더 | `AutoImageProcessor`, `AutoModelForDepthEstimation` |
| Transformers | `4.46.3` |
| 가중치 | `use_safetensors=True` |
| 캐시 | `demo_test/.cache/depth` 또는 `PTOVEN_DEPTH_CACHE` |
| 장치 | 자동 MPS/CPU, 환경 변수로 CPU/MPS/CUDA 명시 가능 |

원격 사용자 코드를 실행하는 옵션은 사용하지 않는다. 모델 가중치는 배포 폴더에 넣지 않고 최초 실행 시 받는다. 모델 출처·라이선스는 [공식 모델 카드](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf)에서 확인한다.

## 로딩 생명주기

```text
not_loaded → prepare() → loading → ready
                               └→ error
```

`prepare()`는 별도 스레드에서 로드하고 즉시 현재 상태를 반환한다. loading/ready에서 반복 호출해도 중복 로드하지 않는다. error에서는 다시 준비할 수 있다.

`status()`는 모델 ID·revision·장치·상태·오류를 반환한다. status의 input_size=294는 기본 설정이다. **개별 추론 응답의 `model.input_size`와 `processed_shape`가 실제 요청의 처리 크기**를 나타낸다.

모델을 전용 스레드에서 로딩하는 것은 입력 처리와 준비 상태를 분리하기 위한 장치다. 추론은 동기 함수이며 동시에 하나만 수행하도록 lock을 사용한다. 처리 중 새 요청은 오류를 반환하고 대기열을 누적하지 않는다.

## 요청 계약

`infer(payload)`의 필수 항목:

| 필드 | 조건 |
| --- | --- |
| `frame_id` | 정수 프레임 식별자 |
| `t_ms` | 유한한 밀리초 관측 시각 |
| `image` | JPEG data URI, 문자열 길이 2,000,000 이하 |
| `landmarks` | 같은 이미지의 21개 정규화 영상 `[x,y]`, 각 축 0~1 |
| `anchors` | 손 밖의 책상 기준점 3~8개 |
| `input_size` | 긴 변 기준 294 또는 518, 생략 시 294 |

이미지는 각 변 32~1280픽셀 범위다. 좌표는 회전·반전·크롭이 모두 반영된 해당 이미지에 대응해야 한다. 서로 다른 시각의 MediaPipe 결과와 이미지를 섞지 않는다.

## 전처리와 텐서 크기

입력의 긴 변을 선택한 크기로 조절하고 각 변을 14의 배수로 반올림한다.

```text
factor = input_size / max(width,height)
tensor_height = max(14, round(height×factor/14)×14)
tensor_width  = max(14, round(width ×factor/14)×14)
```

예를 들어 640×360 이미지는 294 선택 시 294×168, 518 선택 시 518×294가 된다. 반올림에 따른 작은 종횡비 변화는 남는다.

Transformers DPT의 자동 최소 리사이즈는 서로 다른 정사각형 size 요청을 같은 사각형 크기로 만들 수 있다. 이를 피하려고 `model_input_shape()`에서 실제 사각형 크기를 계산한 뒤 `keep_aspect_ratio=False`로 정확한 텐서 크기를 전달한다. 색상·평균·표준편차 정규화는 고정 revision의 processor 설정을 사용한다.

## 추론과 출력

1. RGB로 변환하고 processor로 텐서를 만든다.
2. 선택 장치에서 `eval()`과 `torch.inference_mode()`로 예측한다.
3. 예측 맵을 bicubic interpolation으로 원본 이미지 크기로 복원한다.
4. float 깊이 맵으로 [06의 특징](06-depth-signal.md)을 계산한다.
5. 별도로 2~98 분위 범위를 8bit로 색칠해 JPEG 미리보기를 만든다.

응답은 프레임 ID·관측 시각을 유지하고, 손가락별 contrast·valid, 기준점 값, 평면 계수, 정규화 범위, 추론 시간, 모델 메타데이터, preview를 포함한다. 특징 계산에는 색칠된 8bit 이미지가 아닌 float 깊이 맵을 사용한다.

`inference_ms`는 서버/모델 함수 내부 처리 시간이다. 모델 다운로드·초기 로딩과 카메라 전송 전체 지연을 뜻하지 않는다. 워밍업·장치·입력 크기에 따라 달라지므로 실제 조건에서 별도 측정한다.

## CLI와 실패 처리

```bash
python scripts/run_depth.py data/request.json --output outputs/depth.json --preview outputs/depth.jpg
```

CLI는 `image_path`를 JPEG data URI로 변환하고, prepare→ready 대기→infer를 호출한다. 출력 부모 디렉터리를 생성하며 입력의 evidence_type을 보존한다. 출처가 없으면 `unverified_image_inference`다. 단일 프레임 실행은 `accuracy_evaluated=false`다.

준비 미완료, 라이브러리·네트워크 문제, 장치 미지원, 잘못된 이미지·좌표, 추론 중복은 오류로 처리한다. 실패 시 합성 깊이를 만들어 대체하지 않는다.

## 검증과 한계

기본 테스트는 실제 가중치 없이 텐서 크기 계산·깊이 특징·실패 처리를 확인한다. 실제 모델 연결은 CLI에서 확인한다. `make_synthetic_inputs.py`의 그림은 연결 검증용이며 실제 손 정확도를 평가할 수 없다.

출력은 상대 inverse depth다. mm 단위 높이, 손가락 아랫면의 접촉, 힘·MIDI velocity를 보장하지 않는다. 정지 영상에서 그럴듯한 깊이가 나온다는 사실과 빠른 타건을 구분한다는 사실은 별도의 검증 대상이다.
