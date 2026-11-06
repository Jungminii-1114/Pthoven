# 전처리 출력 데이터 계약

[전체 안내](README.md)

## 최상위 구조

```text
schema_version / preprocessor_version / kind
source        원본 데이터셋·경로·hash·출처
runtime       실제 파서 버전
reference     수정하지 않은 MIDI 기준 데이터와 구조화 정보
practice      별도의 연습 버전·변환 이력·화면/비교용 데이터
performances  nASAP에만 존재: 기존 연주와 원본 정렬
```

`kind=pthoven_practice_score`, schema_version=1이다. 실제 JSON은 allow_nan=false로 저장해 NaN/Infinity를 금지한다. version_id는 전처리기 버전·출처·옵션으로 만든 식별자다. 실행 환경 전체나 사용자의 학습 실력에 대한 인증은 아니다.

## reference

| 필드 | 내용 |
| --- | --- |
| ticks_per_quarter | PPQN. 4분음표 1개당 MIDI tick 수 |
| notes | 안정적인 id, track/channel, pitch/velocity, 원본 onset/offset tick·초, quarter, 마디 연결 |
| tempo_map | 변경 tick·quarter, microseconds_per_quarter, quarter_bpm |
| time_signatures | 변경 tick·quarter와 numerator/denominator |
| key_signatures | MIDI 조표 이벤트. 음조·장단조 전체 분석이 아님 |
| controls | 페달 등을 포함한 원본 control change. tick 시각을 유지 |
| tracks | 원본 track 번호·이름 |
| measures | 생성 순번·원본 마디 라벨·quarter 범위·경계 출처 |
| onset_groups | 같은 onset에 있는 음표 ID·pitch와 마디 ID |
| rests | 모든 선택 음이 없는 시간. 성부별 기보 쉼표와 다름 |
| diagnostics | 미종료 음표·잘못된 note_off·FIFO 겹침·기본 박자 가정 등 |

음표 ID는 `t1_n0`처럼 원본 track과 note_on 순번을 사용한다. nASAP MusicXML ID는 별도로 보존한다. XML과 MIDI note ID가 원래부터 동일하다고 가정하지 않는다.

## practice

| 필드 | 내용 |
| --- | --- |
| options | 음역·흰 건반·BPM·구간·이조·선택 정책 |
| transformations | 적용한 편곡과 제외/경계 절단 음표 목록 |
| origin_source_quarter | 연습 시작점이 원본의 어느 위치인가 |
| ready / rejection_reasons | 설정한 음역·동시음·1초 밀도 제한 통과 여부 |
| notes | source_note_id/source_pitch/source tick과 연습용 pitch·quarter·초·key_id |
| onset_groups | 화면에서 동시에 안내할 음표와 예상 onset |
| segments | 반복 단위의 마디 ID·quarter/초 범위·onset 그룹 ID |
| keyboard | 가상 건반의 pitch, key_id, rect, is_black, z_order |
| difficulty | 음표 수·최대 유지 동시음·음역·평균/1초 최대 밀도·최상음 간 도약 |
| evaluation_policy | onset 비교만 활성화, duration/velocity 비교 비활성화 |

keyboard.rect는 `[x,y,width,height]`의 0~1 상대 좌표다. black key는 높은 z_order로 그린다. 카메라·책상 좌표가 아니며 화면의 선택 영역에 비례 배치한다. 기본 키보드는 검은 건반도 포함한다. C4~G4에서 --white-only를 쓰면 정확히 C4/D4/E4/F4/G4 5개가 된다.

## 원본 시각과 연습 시각

모든 `*_quarter`는 4분음표 단위다. 6/8의 1마디는 3 quarter이며 6 quarter가 아니다. `denominator_beats_from_measure_start`는 분모 음표 기준 위치로, 복합박의 지휘 박 수와 다를 수 있다.

원본 MIDI는 트랙별 delta tick을 누적하고 tempo 변경 구간을 적분해 초로 바꾼다. 연습 고정 BPM은 **4분음표 기준**이다.

```text
60 BPM: onset_quarter=0,1,2 → onset_seconds=0,1,2
120 BPM: 같은 quarter 위치 → onset_seconds=0,0.5,1
```

--original-tempo이면 선택한 원본 구간의 tempo map을 적분하고 연습 시작을 0초로 옮긴다. 사용자가 입력한 실제 연주 시각을 이 예상 시각에 강제로 맞추는 기능은 없다.

## 구간 경계와 변환

- 시작 전에 이미 눌려 있던 음은 새 타건으로 만들지 않고 excluded_carry_in_note_ids에 남긴다.
- 끝을 넘어가는 음은 practice 길이만 자르고 clipped_duration_note_ids에 남긴다.
- 부분 선택·트랙 선택·타악기 제외·highest·drop의 영향을 기록한다.
- 같은 onset에서 고른 최상음은 진짜 오른손/선율 라벨이 아니다.
- MIDI velocity와 페달을 RGB의 실제 압력·접촉 정답으로 사용하지 않는다.

## 화면과 Agent 연결 예시

```text
practice.onset_groups의 다음 그룹
    → note_ids로 practice.notes 조회
    → key_id로 practice.keyboard의 rect 조회
    → onset_seconds/quarter로 가이드 시각 결정

실제 Vision 입력의 pitch와 관측 시각
    → 같은 연습 버전의 예상 pitch/onset과 비교
    → segment ID·원본 마디 ID와 분석 결과를 Agent에 전달
```

렌더러는 ready=false 데이터를 연습 가능한 카탈로그로 자동 등록하지 않는다. ready=true도 악보 내용·교육적 난이도를 사람이 검수한 상태라는 뜻은 아니다. Agent는 전처리 난이도 지표만으로 사용자의 실제 취약점을 단정하지 않는다.

## 알려진 범위

일반 MIDI만으로 기보의 반복 기호·성부·정확한 마디 번호·못갖춘마디를 항상 복원할 수 없다. nASAP의 검수 downbeat·MusicXML을 사용할 때와 Mutopia의 MIDI meter 추정은 boundary_source로 구분한다. 동일 pitch의 겹치는 note_on은 FIFO 정책을 쓰고 진단으로 남긴다.

nASAP XML↔MIDI 교차 매핑은 유일한 pitch/onset 대응만 연결한다. 나머지는 버리지 않고 별도 상태로 남긴다. 모든 데이터셋의 모든 곡을 완벽히 매핑했다고 주장하지 않는다.
