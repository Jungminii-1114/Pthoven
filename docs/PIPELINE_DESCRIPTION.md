# PTOVEN · CV–Agent Pipeline Description

프토벤의 전체 흐름, 상위·세부 단계와 Agent 도구를 계층별로 나누어 설명하는 팀 설계 문서입니다. 코드 경로·구현 진척을 공유하는 문서가 아니라, 팀이 함께 구현할 책임·계약·동작·평가 기준을 다룹니다.

| 순서 | 문서 | 내용 |
| --- | --- | --- |
| 시작 | [문서 안내](plan/README.md) | 읽는 순서와 문서 책임 |
| 1 | [Overall Plan](plan/01-overall-plan.md) | 사용자 경험과 전체 CV–Agent 파이프라인 |
| 2 | [Big Node](plan/02-big-nodes/README.md) | 상위 단계 7개의 실행 순서·분기·종료 |
| 3 | [Small Node](plan/03-small-nodes/README.md) | 세부 단계 19개의 함수·입출력·실패·검증 |
| 4 | [Agent Tool](plan/04-tools/README.md) | 도구 14개의 스키마·예시·실행·재시도 |
| 공통 | [데이터·반환·명령 계약](plan/05-contracts/README.md) | 상태·시간·좌표·근거·적용 확인 |
| 실행·평가 | [하네스와 평가](plan/06-harness-and-evaluation.md) | 호출 제어·기록·비교·데모 |
| 팀 합의 | [결정 항목](plan/07-decision-points.md) | 정책·모델·제품 선택 기준 |

기본 흐름은 **관측 → 가상 입력 → 정렬·상태 → Agent 조회·선택 → 검증·실제 적용 → 재평가 → 보고서**입니다. 이미지는 CV가 처리하고 기본 Agent는 구조화된 결과를 사용합니다.

[참고한 ReportAnchor JS 문서 구조](plan/references.md)
