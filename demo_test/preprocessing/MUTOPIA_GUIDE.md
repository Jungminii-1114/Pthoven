# Mutopia 전처리 수행 가이드

[전체 안내](README.md) · [코드](preprocess_mutopia.py)

## 입력 준비

[Mutopia](https://www.mutopiaproject.org/)에서 선택한 곡의 **MIDI 파일**을 내려받는다. 가능하면 같은 곡의 LilyPond `.ly`도 함께 보관한다. PDF만으로는 실행할 수 없다. 이 코드는 LilyPond를 컴파일하거나 PDF를 악보로 인식하지 않는다.

```text
demo_test/data/mutopia/
├── piece_a.mid
├── piece_a.ly
├── piece_b.midi
└── manifest.json
```

공식 소스 저장소나 `.ly` 파일만 가지고 있다면, 웹사이트가 제공하는 생성 MIDI를 추가로 준비해야 한다. 기본 실행은 input 폴더 아래 `.mid/.midi`를 재귀 검색한다. `--input data/mutopia/piece_a.mid`로 한 파일만 처리할 수도 있다.

## 선택적 manifest

다음 JSON 배열을 `data/mutopia/manifest.json`에 저장한다. 경로는 `--input` 폴더 기준이다. manifest는 검색 대상을 제한하지 않고 해당 파일의 메타데이터를 보완한다.

```json
[
  {
    "midi_path": "piece_a.mid",
    "lilypond_path": "piece_a.ly",
    "title": "선택한 곡 제목",
    "composer": "작곡가",
    "license": "선택한 곡 페이지의 라이선스",
    "source_url": "선택한 곡의 Mutopia 페이지 주소",
    "metadata_verified": false
  }
]
```

`metadata_verified`는 직접 검수한 뒤에만 true로 바꾼다. 이는 법적 사용 가능성을 자동 판정하는 필드가 아니다. metadata가 없더라도 MIDI 처리는 가능하지만 unknown/미검수 출처로 남는다.

단순 문자열 LilyPond header에서 title/composer/license/copyright 등을 읽고 manifest가 있으면 우선한다. markup·변수·include를 실행하지 않는다. source_url은 수동 manifest로 보존한다. 라이선스는 [곡별 안내](https://www.mutopiaproject.org/legal.html)를 확인한다.

## 실행

명령은 `demo_test` 폴더에서 실행한다.

```bash
source .venv-preprocess/bin/activate
python -m pip install -r preprocessing/requirements-mutopia.txt

python preprocessing/preprocess_mutopia.py \
  --input data/mutopia \
  --output outputs/mutopia \
  --manifest data/mutopia/manifest.json \
  --low 60 --high 67 --white-only \
  --bpm 60 --segment-measures 2
```

이 설정의 지원 건반은 C4·D4·E4·F4·G4다. 악보에 이외의 음이 있으면 기본적으로 ready=false가 된다. 초보자용 구간을 골라 다시 실행하거나, 의미를 검토한 뒤 명시적 편곡 옵션을 적용한다.

```bash
python preprocessing/preprocess_mutopia.py \
  --input data/mutopia/piece_a.mid \
  --output outputs/mutopia_excerpt \
  --start-measure 1 --end-measure 4 \
  --tracks 1 --melody highest \
  --low 60 --high 72 --bpm 60 \
  --max-polyphony 2 --max-density 6
```

트랙 1이 선율이라고 확인한 경우의 예시다. 곡마다 트랙 구성이 다르므로 `reference.tracks`와 PDF를 검수한다. highest는 같은 시각의 최상음 선택일 뿐 완전한 선율 분리가 아니다.

## 못갖춘마디

단순 `\\partial 4.` 등은 읽을 수 있다. 복잡한 변수·배수 표현이 나오면 수동 검수가 필요하다는 오류를 내며, 다음처럼 4분음표 단위로 지정한다.

```bash
python preprocessing/preprocess_mutopia.py \
  --input data/mutopia/piece_a.mid \
  --output outputs/mutopia_pickup \
  --pickup-quarters 1.5
```

여러 곡의 못갖춘마디 길이가 다르면 manifest의 각 항목에 `pickup_quarters`를 넣는다. CLI 값은 모든 곡에 일괄 적용되므로 주의한다. 정보가 없으면 첫 마디 시작=0으로 추정하고 boundary_source=midi_meter_inferred를 남긴다. MIDI만으로 기보의 마디 번호·반복 기호·성부 쉼표를 완전히 복원하는 것은 아니다.

## 결과 확인

`outputs/mutopia/catalog.json`의 ready/reasons/failures를 읽고, 곡 JSON에서 다음을 확인한다.

1. title/composer/license/source URL과 파일 hash가 맞는가.
2. 원본 reference와 별도 practice가 분리되어 있는가.
3. 선택 마디, 템포, 음역, 제외 음표가 의도와 같은가.
4. 건반이 적거나 unsupported pitch가 있는데 ready=true로 오해하지 않았는가.

목적에 맞게 선택된 쉬운 구간을 사람이 검수한 뒤 서비스에 등록한다. 데이터셋 전체를 자동으로 초보자용 곡으로 승인하지 않는다.
