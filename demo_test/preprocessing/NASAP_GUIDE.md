# nASAP 전처리 수행 가이드

[전체 안내](README.md) · [코드](preprocess_nasap.py)

## 입력 준비

[공식 nASAP 저장소](https://github.com/CPJKU/asap-dataset)의 파일 구조를 유지한다. 원래 ASAP에는 없는 note_alignments 열이 필요하므로 nASAP 확장 버전을 사용한다.

```bash
git clone https://github.com/CPJKU/asap-dataset.git data/nasap
```

예상 구조:

```text
data/nasap/
├── metadata.csv
├── asap_annotations.json
└── Bach/...
    ├── midi_score.mid
    ├── xml_score.musicxml
    ├── performance.mid
    └── .../note_alignment.tsv
```

실제 연주 파일 이름은 곡마다 다르며 metadata의 경로를 그대로 사용한다. 오디오와 MAESTRO 전체를 받을 필요는 없다. 재현성을 위해 다운로드한 nASAP의 Git commit도 실험 노트에 기록한다. 코드는 선택 파일·metadata·주석의 SHA256을 출력한다.

## 첫 실행

명령은 `demo_test` 폴더에서 실행한다.

```bash
source .venv-preprocess/bin/activate
python -m pip install -r preprocessing/requirements-nasap.txt

python preprocessing/preprocess_nasap.py \
  --root data/nasap \
  --output outputs/nasap_first \
  --limit 1 \
  --low 48 --high 84 --bpm 60 \
  --max-polyphony 5 --max-density 12
```

먼저 한 연주를 읽어 파일 배치와 매핑을 확인하는 예시다. 넓은 음역·높은 제한은 검수용이며, 초보자용 설정을 뜻하지 않는다. `--limit`은 **악보 수가 아닌 metadata의 performance 행 수**다.

```bash
python preprocessing/preprocess_nasap.py \
  --root data/nasap \
  --output outputs/nasap_bach \
  --composer Bach --title Fugue \
  --limit 3 --start-measure 1 --end-measure 4 \
  --low 60 --high 72 --bpm 60 \
  --melody highest --max-polyphony 2
```

필터는 부분 문자열 비교다. 해당 곡에 존재하는 마디 범위를 선택해야 한다. 곡별 생성 마디는 reference.measures를 확인한다. 실제 초보자용 곡 선정과 편곡 검수는 별도다.

## 품질 필터

기본값은 robust_note_alignment가 1 또는 1.0인 행만 처리한다. CSV의 문자열 `"1.0"`을 참으로 인식한다. 나머지를 포함하려면 `--include-nonrobust`를 명시한다.

품질 플래그가 1이어도 모든 교차 매핑이 완전하다는 뜻은 아니다. catalog의 xml_mapping과 원본 정렬을 확인한다. 같은 악보 MIDI/MusicXML 쌍은 한 JSON으로 합치고, 여러 performance는 performances 배열에 별도로 보존한다.

## nASAP에 적용하는 단계

1. **score MIDI**에서 원본 음표·템포·박자를 정규화한다. performance MIDI는 정답 악보 대신 쓰지 않는다.
2. 검수된 midi_score_downbeats의 **초**를 tempo map으로 **quarter**에 역변환해 마디를 구성한다. 없으면 MIDI meter 기반으로 추정한다.
3. MusicXML을 Partitura로 읽고 반복을 펼친다. `n2-1` 같은 음표 ID를 보존한다.
4. XML과 runtime MIDI 음표는 pitch·quarter onset이 유일하게 일치할 때 연결한다. 한 tick 이하의 수치 오차를 허용한다. 모호한 unison·꾸밈음·반복 차이는 unresolved/ambiguous로 남긴다.
5. performance MIDI를 Partitura로 읽어 기존 midi_id를 보존하고, note_alignment.tsv의 match/insertion/deletion을 연결한다.
6. 원본 reference를 보존한 상태에서 지원 음역·템포·구간에 맞는 practice를 생성한다.

MusicXML의 staff/voice는 메타데이터로 보존되지만 여기서 자동 오른손·왼손 정답으로 단정하지 않는다. Partitura track 번호와 raw MIDI track 번호가 다를 수 있어 임의로 track으로 조인하지 않는다.

## 결과 해석

- `reference.musicxml.mapping_counts`: runtime MIDI와 유일/모호/미해결로 연결된 XML 음표 수.
- `performances[].notes`: 기존 연주의 실제 시각·길이·velocity.
- `performances[].alignment`: 원본 score ID ↔ performance ID, 원본 runtime score ID 연결.
- `performances[].applies_to`: unmodified_reference_only.

practice에서 이조하거나 음을 생략했다면 기존 performance alignment가 편곡의 정답이 되지 않는다. 원본 악보 매칭 알고리즘을 검증하는 자료로 분리해서 사용한다. 이는 우리 RGB 카메라의 실제 접촉 라벨이 아니다.

파일 누락·XML 해석 실패는 catalog.failures에 남는다. 한 악보에 최소 한 연주가 정상 처리되어야 그 악보 JSON을 출력한다. 전체 실행 전에 --limit으로 일부를 확인하고, 선택 데이터의 [라이선스](https://github.com/CPJKU/asap-dataset/blob/main/LICENSE.md)도 검수한다.
