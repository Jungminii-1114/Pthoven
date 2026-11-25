# Pthoven · 팀 실험 웹

팀원이 자기 컴퓨터에서 카메라를 연결하고 타건 후보·관측 품질을 비교하며 학습/평가용 기록을 수집하는 로컬 웹입니다. H1의 핵심 판정은 옆 폴더의 [Python 엔진](../python_tap/README.md)을 사용합니다.

## 빠른 실행

Python 3.10 이상이 필요합니다. 아래 명령은 저장소를 clone한 폴더에서 시작합니다. Python 3.10 환경에서 검증했습니다.

### macOS / Linux

```bash
git clone https://github.com/Jungminii-1114/Pthoven.git
cd Pthoven/demo_test/experiment_web
python3 -m venv .venv
.venv/bin/python -m pip install -e ../python_tap -r requirements.txt
.venv/bin/python app.py --port 8768
```

### Windows PowerShell

```powershell
git clone https://github.com/Jungminii-1114/Pthoven.git
cd Pthoven/demo_test/experiment_web
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ../python_tap -r requirements.txt
.venv\Scripts\python.exe app.py --port 8768
```

이미 clone했다면 해당 저장소에서 `git pull origin master` 후 `demo_test/experiment_web`로 이동합니다. `pip` 설치 명령은 이 폴더에서 실행해야 `../python_tap` 경로가 맞습니다.

- 실험 선택: http://127.0.0.1:8768/experiment-lab.html
- H1 타건 / H2 손등: http://127.0.0.1:8768/tap-lab.html
- H3·H4 깊이 비교: http://127.0.0.1:8768/depth-lab.html

서버 터미널을 열어둔 상태로 사용하고 종료는 `Ctrl+C`입니다. 포트가 이미 사용 중이면 `--port 8769`로 실행하고 주소의 포트도 바꿉니다. 각 팀원이 자기 컴퓨터의 localhost에서 실행합니다. GitHub Pages만으로 Python 판정 서버가 실행되지는 않습니다.

## 제공하는 실험

| 실험 | 역할 | 설치 |
|---|---|---|
| H1 TIP + 관절각 | Python 특징·보정·길이 검사·타건 후보·기준 복구 | 기본 |
| H2 손등 표면 | 정렬한 작은 패치의 광학 흐름·국소 변형 관찰 | 기본 |
| H3 상대 깊이 | Depth Anything V2 Small의 상대 역깊이 비교 | 선택 |
| H4 관절 + 깊이 | 동일 시행의 단독/결합 방식 비교 및 원본 검수 | 선택 |

H1은 선택한 한 손의 엄지·검지·중지·약지·소지를 손가락별 보정과 상태로 처리합니다. 현재 개선 모드는 `상대 기준 · 개선 모드`이며, 비교용 고정 모드도 선택할 수 있습니다. 선택은 브라우저에 저장됩니다. H3·H4의 비교용 브라우저 판정은 레거시 JavaScript 구현을 포함하며 H1 Python 엔진과 구분합니다. H2는 학습된 접촉 판정기가 아닙니다.

이 패키지는 실험·수집 환경이며 게임 화면, Agent 보고서, 사용자 실험 원본, 학습된 자체 타건 모델을 배포하는 패키지는 아닙니다. 카메라 영상은 브라우저에서 사용하고 H2/H3 선택 시 패치/프레임을 실행 중인 로컬 서버로 보냅니다. 서버는 원본을 자동 저장하지 않습니다.

## 첫 테스트

1. 실험 화면에서 카메라 권한을 허용하고 iPhone 연속성 카메라 또는 웹캠을 선택합니다. iPhone은 먼저 OS의 카메라 장치로 보여야 합니다. 좌우·상하 반전은 화면에서 선택합니다.
2. 추적할 손과 `상대 기준 · 개선 모드`를 확인합니다. 손목·손바닥·손가락이 보이도록 고정하고 기준 자세를 저장합니다.
3. 손가락을 선택해 들림 보정을 추가합니다. 다섯 손가락을 쓸 때는 기준을 한 번 저장한 뒤 각각 들림을 저장합니다. 기준 재저장/손 변경은 기존 보정을 초기화합니다.
4. 익명 사용자 ID와 세션 ID를 서로 구분합니다(예: p01/s01, p02/s01). 정상 탭, 정지, 옆 끌기, 이웃 손가락, 공중 탭, 가림을 별도 시행으로 수집합니다.
5. `준비 후 10초 연주 기록`을 누릅니다. 준비 1초 → 연주 10초 → 마무리 1초를 저장합니다. 마지막에는 손가락을 내려놓고 유지합니다.
6. `고정 기록 JSON 저장`으로 저장하고 실제 동작 수를 별도로 적습니다. 완료된 시행은 손을 뺀 뒤에도 유지됩니다.

최근 진단 저장은 현재 창과 마지막 손 관측 창을 구분해 저장할 수 있습니다. 창이 겹칠 수 있으므로 후보 수를 단순 합산하지 않습니다. 기존 JSON을 보정 불러오기로 읽으면 그 사람의 손가락 보정값을 가져옵니다. 다른 사람이 테스트할 때는 자기 손으로 새로 보정합니다.

## 학습·평가 데이터로 사용할 때

기록에는 원본 랜드마크·보정 모델·엔진 버전·판정 모드·후보·추적 실패 이유가 들어갑니다. 선택한 조건에 따라 손등 패치나 깊이 실험 영상도 저장할 수 있습니다. 실제 타건 시각과 손가락 정답은 별도 검수가 필요합니다. 화면의 동작 지시, 기억한 동작 수, 모델이 낸 후보는 정답 라벨이 아닙니다. 불명확한 구간과 관측 실패를 구분하고 사람/세션을 나눠 평가합니다.

JSON은 브라우저 다운로드 폴더에 저장합니다. 로컬에서 모을 경우 이 폴더 아래 `captures/` 또는 `outputs/`를 사용하세요. 이 경로는 Git에서 제외됩니다. 개인 원본 기록·영상·가중치·환경 파일은 이 배포에 포함하지 않습니다.

## 선택: Depth 실험 설치

기본 H1/H2 실행에는 PyTorch가 필요하지 않습니다. Depth 실험을 할 때만 같은 가상환경에 설치합니다.

```bash
.venv/bin/python -m pip install -r requirements-depth.txt
```

Windows는 `.venv\Scripts\python.exe -m pip install -r requirements-depth.txt`를 사용합니다. 서버를 재시작한 뒤 깊이 화면의 모델 준비 버튼을 누릅니다. 최초 실행에는 인터넷으로 모델을 받아 `.cache/depth/`에 보관합니다. 브라우저 손 추적 SDK/모델도 CDN에서 로드하므로 첫 실행에는 인터넷이 필요합니다. Depth의 기본 장치 선택은 MPS 또는 CPU이며, CUDA 환경은 `PTOVEN_DEPTH_DEVICE=cuda`를 지정할 수 있습니다.

깊이 출력은 상대 역깊이이며 실제 mm 높이나 접촉 정답이 아닙니다. 키보드 타건·음원 입력·물리 피아노 인식을 대체하는 장치 측정값으로 해석하지 않습니다.

## 코드 위치

| 폴더 | 내용 |
|---|---|
| `web/` | 카메라·보정·기록·실험 화면 |
| `server/` | 실험용 HTTP API 및 정적 파일 제공 |
| `models/` | 손등/깊이 분석 어댑터와 깊이 비교용 브라우저 코드 |
| `../python_tap/` | H1 Python 판정 엔진의 단일 소스 |
| `tests/` | 웹 연결·모드 저장·기록 구간·분석 어댑터 테스트 |

약지 보정의 길이 불안정 문제와 수집 시간 확대는 별도 검토 중입니다. 이 배포에는 보류된 임시 수정이 포함되지 않습니다.

## 검증 명령

이 폴더에서 실행합니다. Node.js는 웹 로직 테스트에만 필요합니다.

```bash
.venv/bin/python -m unittest discover -s ../python_tap/tests -v
.venv/bin/python -m unittest discover -s tests -v
node --test tests/*.mjs
```

테스트의 합성 손·합성 깊이 통과는 실제 사용자 정확도를 뜻하지 않습니다. 현재 다른 손가락의 실제 촬영 성능은 팀 실험으로 추가 검증해야 합니다.
