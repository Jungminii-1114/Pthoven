# Small Node · 세부 단계

각 문서는 단일 책임, 필요한 내부 함수, 입력·출력 계약, 상태·실패 처리, 검증 사례를 정의한다. 상위 실행 분기는 [Big Node](../02-big-nodes/README.md)가 관리한다. 내부 함수는 별도의 Tiny Node로 세지 않는다.

| ID | 함수명 | 역할 | 실행 성격 | 주요 담당 |
| --- | --- | --- | --- | --- |
| [S-01](S-01.md) | `prepare_session` | 세션과 입력 환경 준비 | 결정론적 | 웹·서버 |
| [S-02](S-02.md) | `estimate_hand_observation` | 손 관측과 시간 정보 추출 | CV 추론 | CV·웹 |
| [S-03](S-03.md) | `calibrate_geometry_and_pose` | 가상 건반과 손가락 기준 보정 | 결정론적 | CV·웹 |
| [S-04](S-04.md) | `assess_observation_quality` | 관측 유효성과 평가 보류 판정 | 결정론적 | CV·평가 |
| [S-05](S-05.md) | `detect_finger_cycle` | 손가락별 들림·복귀 주기 탐지 | 시계열 상태 머신 | CV |
| [S-06](S-06.md) | `map_virtual_note` | 가상 건반 매핑과 입력 수용 | 결정론적 | CV·게임 |
| [S-07](S-07.md) | `align_practice_events` | 악보 정렬과 평가 구간 정의 | 결정론적 | 분석·서버 |
| [S-08](S-08.md) | `build_practice_snapshot` | 연습 지표와 근거 스냅샷 구성 | 결정론적 | 분석·서버 |
| [S-09](S-09.md) | `route_coaching_trigger` | 코칭 호출 조건과 우선순위 결정 | 결정론적 | Agent·서버 |
| [S-10](S-10.md) | `build_coach_context` | 모델 컨텍스트와 도구 범위 구성 | 결정론적 | Agent |
| [S-11](S-11.md) | `decide_practice_strategy` | 조회와 연습 전략 선택 | LLM + 제한된 도구 루프 | Agent |
| [S-12](S-12.md) | `validate_coach_plan` | 계획·근거·변경 조건 검증 | 결정론적 우선 | Agent·서버 |
| [S-13](S-13.md) | `issue_practice_command` | 변경 묶음 발행과 대기 상태 기록 | 결정론적 | 서버 |
| [S-14](S-14.md) | `apply_command_and_ack` | 클라이언트 적용과 ACK 생성 | 결정론적 | 웹·게임 |
| [S-15](S-15.md) | `reconcile_intervention` | 실제 적용 결과와 상태 동기화 | 결정론적 | 서버·웹 |
| [S-16](S-16.md) | `compare_attempt_conditions` | 두 시도의 비교 가능성 검사 | 결정론적 | 분석·평가 |
| [S-17](S-17.md) | `evaluate_intervention` | 개입 이후 변화 계산 | 결정론적 | 분석·Agent |
| [S-18](S-18.md) | `compose_grounded_report` | 근거 기반 보고서 초안 생성 | 텍스트 생성 | Agent·디자인 |
| [S-19](S-19.md) | `verify_and_deliver_report` | 보고서 검증과 사용자 전달 | 규칙 검증 + 선택적 의미 검토 | 평가·서버·디자인 |

모든 반환은 [공통 결과 계약](../05-contracts/common-result.md)을 사용한다. 업무 데이터 필드의 정본은 [데이터 모델](../05-contracts/data-models.md)이다.
