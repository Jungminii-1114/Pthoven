# Mutopia / nASAP 악보 전처리

두 데이터셋을 **각각 독립 실행**하고, 프토벤의 화면 가이드·악보 비교에 쓸 동일한 JSON 구조로 변환한다. 웹·카메라·Agent 실행은 필요하지 않다. 원본 데이터 파일은 수정하지 않는다.

```text
원본 악보 MIDI
  → 음표·템포·박자 정보 정규화
  → 마디·동시 발음·쉼·연습 구간 구성
  → 지원 음역·난이도에 맞는 연습 버전
  → 가상 건반 좌표·예상 입력 시각·비교용 데이터
```

이 순서의 세부 주석은 [common.py](common.py)의 `read_midi`, `make_measures`, `group_notes`, `build_document`, `keyboard`에 있다. 데이터셋별 차이는 각 진입 파일의 주석에 적었다.

| 파일 | 역할 |
| --- | --- |
| [preprocess_mutopia.py](preprocess_mutopia.py) | 로컬 `.mid/.midi`, 선택적 `.ly`·manifest 처리 |
| [preprocess_nasap.py](preprocess_nasap.py) | metadata.csv, score MIDI/MusicXML, performance MIDI·음표 정렬 처리 |
| [common.py](common.py) | 공통 정규화·구간화·연습 변환·화면 데이터 |
| [make_examples.py](make_examples.py) | 다운로드 없이 실행해 볼 합성 입력 생성 |
| [MUTOPIA_GUIDE.md](MUTOPIA_GUIDE.md) | Mutopia 수행 가이드 |
| [NASAP_GUIDE.md](NASAP_GUIDE.md) | nASAP 수행 가이드 |
| [OUTPUT_FORMAT.md](OUTPUT_FORMAT.md) | 출력 구조·시각·ID·변환 정책 |

## 1. 설치

아래 명령은 저장소의 `demo_test` 폴더에서 실행한다. Python 3.10 이상을 사용한다.

```bash
python3 -m venv .venv-preprocess
source .venv-preprocess/bin/activate
```

**Mutopia만** 처리하려면:

```bash
python -m pip install -r preprocessing/requirements-mutopia.txt
```

**nASAP 또는 두 데이터셋 모두** 처리하려면:

```bash
python -m pip install -r preprocessing/requirements-nasap.txt
```

Mutopia는 Mido 1.3.3만 사용한다. nASAP는 Partitura 1.7.0을 추가해 MusicXML의 반복·음표 ID와 performance MIDI의 ID를 읽는다. 깊이 모델·Torch는 필요하지 않다.

## 2. 다운로드 없이 먼저 실행하기

```bash
python preprocessing/make_examples.py

python preprocessing/preprocess_mutopia.py \
  --input outputs/preprocessing_examples/mutopia \
  --output outputs/mutopia_example \
  --low 60 --high 67 --white-only --bpm 60

python preprocessing/preprocess_nasap.py \
  --root outputs/preprocessing_examples/nasap \
  --output outputs/nasap_example \
  --low 60 --high 67 --white-only --bpm 60
```

합성 도·미·솔 악보가 공통 JSON으로 저장된다. 실제 Mutopia/nASAP 데이터나 실제 연주 성능 검증 자료가 아니다. 생성 파일은 `outputs/` 아래에만 둔다.

## 3. 실제 데이터 처리

[Mutopia 가이드](MUTOPIA_GUIDE.md)의 MIDI·LilyPond·manifest 준비 또는 [nASAP 가이드](NASAP_GUIDE.md)의 저장소 준비를 따른다. 두 전처리기는 서로의 파일을 요구하지 않는다.

공통 옵션:

| 옵션 | 기본값 | 의미 |
| --- | --- | --- |
| `--low / --high` | 60 / 72 | 지원 MIDI 음역, 60=C4 |
| `--white-only` | 꺼짐 | 범위 안에서도 흰 건반만 허용 |
| `--bpm` | 60 | **4분음표 기준** 고정 연습 속도 |
| `--original-tempo` | 꺼짐 | 고정 BPM 대신 원본 tempo map 사용 |
| `--start-measure / --end-measure` | 1 / 끝 | 생성한 마디 순번 범위, 1부터 시작 |
| `--segment-measures` | 2 | 반복 연습 구간당 마디 수 |
| `--tracks 1,2` | 전체 비타악기 트랙 | MIDI 트랙 번호, 0부터 시작 |
| `--transpose` | 0 | 명시적인 반음 이조, reference는 불변 |
| `--melody highest` | all | 같은 onset의 최상음 하나 선택 |
| `--out-of-range drop` | reject | 지원하지 않는 음을 명시적으로 생략 |
| `--max-polyphony` | 1 | 유지 중인 음까지 포함한 최대 동시음 제한 |
| `--max-density` | 4 | 1초 창의 최대 onset 음표 수 제한 |

`--melody highest`는 완전한 선율/오른손 분리 모델이 아니다. 서로 다른 onset의 음이 길게 겹치면 여전히 다성일 수 있다. `--tracks`도 오른손을 자동 판별하지 않는다.

모든 음이 범위 안에 들어가도록 음역을 넓히거나 맞는 구간을 먼저 고른다. 이조·생략·최상음 선택은 편곡이므로 원본과 다른 연습 버전으로 관리한다. 원본 음을 가까운 건반으로 강제로 붙이는 처리는 없다.

## 4. 결과 확인

출력 폴더의 `catalog.json`부터 확인한다.

- `scores`: 성공적으로 변환한 파일과 `ready`, 이유, 출처.
- `failures`: 읽기·구조·경로·라이브러리 오류. 파일별로 검수한다.
- 곡 JSON의 `reference`: 원본 음표·tempo/meter·마디·쉼·그룹.
- 곡 JSON의 `practice`: 변환된 음표·구간·건반·시각·난이도·변환 이력.

**ready=false는 변환 실패와 다르다.** 변환은 성공했지만 음역·동시음·밀도 제한을 초과한 경우다. `ready=true`도 초보자에게 교육적으로 적합함이나 악보의 완전한 정확성을 보장하지 않는다.

프로세스 종료 코드는 0=변환 성공 및 처리 오류 없음, 1=부분 성공·일부 처리 오류, 2=변환한 곡 없음이다. catalog의 readiness는 별도로 확인한다. 같은 출력 폴더에서 재실행하면 catalog는 이번 실행 결과로 갱신된다. 곡 파일 이름에는 출처와 변환 설정의 버전 ID가 포함되며, 이전 실행의 곡 파일이 자동 삭제되지는 않는다.

## 5. 검증

두 데이터셋의 의존성을 설치한 환경에서:

```bash
python -m unittest discover -s preprocessing/tests -p test_preprocessing.py -v
```

테스트는 템포 변화, track-local 시간, note_on velocity=0, 비정상 음표, 박자/못갖춘마디, 원본 보존, 이조·음역·흰 건반 정책, 음표 주석 연결과 독립 CLI를 확인한다. Vision 타건 탐지 정확도 테스트가 아니다.

## 해석 범위

MIDI의 길이는 키 release 기준으로 보존하고 페달 control은 별도 기록한다. 이것을 실제 음향 지속 길이나 RGB 관측의 누름 유지 정답으로 단정하지 않는다. 출력 정책은 onset 비교만 활성화하고 duration/velocity 평가는 비활성화한다.

nASAP의 기존 연주 정렬은 **원본 악보에 대한 정렬**이다. 편곡·이조한 practice에 기존 performance를 그대로 정답으로 적용하지 않는다. Mutopia는 곡별 라이선스·출처를 검수하고, nASAP는 선택 버전의 라이선스를 확인한다. 실제 데이터·생성 결과·가상환경은 Git에 포함하지 않는다.
