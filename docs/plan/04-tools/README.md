# Agent Tool · 도구 목록과 공통 실행 의미

Tool은 Agent가 호출 가능한 인터페이스다. Small Node와 1:1 대응하지 않는다. 하나의 조회 도구가 여러 세부 단계를 재사용할 수 있고, 내부 함수는 Tool로 자동 노출되지 않는다.

## 실행 모드

- `read`: 상태·근거를 읽거나 계산한다. 실제 게임 설정은 변경하지 않는다.
- `stage`: 변경 제안을 준비한다. 호출 성공은 `staged`이며 실제 적용이 아니다. S-12 검증 후 S-13~S-15에서 변경 묶음을 발행·적용·확인한다.
- `optional_read`: 선택적인 시각 진단이다. 비활성일 때 기본 코칭은 그대로 진행한다.

변경 제안 단계의 side effect는 해당 코칭 실행에 한정된 ProposalStore 기록이다. 실제 사용자 설정 변경은 별도 승인된 change_set 실행에서만 발생한다. 도구 반환을 모델에 전달해도 staged 값을 actual로 바꾸지 않는다.

| ID | 호출명 | 모드 | 목적 |
| --- | --- | --- | --- |
| [T-01](T-01.md) | `get_practice_state` | read | 현재 판단에 연결된 불변 스냅샷과 실제 설정·진행 단계를 조회한다. |
| [T-02](T-02.md) | `get_segment_evidence` | read | 특정 시도·음표 구간에서 어떤 입력·정렬·평가 보류가 발생했는지 조회한다. |
| [T-03](T-03.md) | `get_tracking_summary` | read | 카메라·추적·보정·손 연결·시간 공백의 상태와 평가 제외 이유를 조회한다. |
| [T-04](T-04.md) | `get_attempt_history` | read | 현재 목적과 관련 있는 이전 시도와 실제 적용·거절·불명 개입을 조회한다. |
| [T-05](T-05.md) | `compare_attempts` | read | 두 시도의 비교 가능 조건과 계산된 변화량을 조회하여 다음 연습 선택의 근거로 사용한다. |
| [T-06](T-06.md) | `get_reference_excerpt` | read | 현재 연습곡의 요청 범위에 있는 기대 음·박자·마디 표기와 지원 기능을 조회한다. |
| [T-07](T-07.md) | `set_tempo` | stage | 다음 구간의 BPM 변경을 근거가 연결된 ActionProposal로 준비한다. |
| [T-08](T-08.md) | `set_practice_range` | stage | 어려움이 관측된 음표 범위를 다음 연습의 반복 구간으로 준비한다. |
| [T-09](T-09.md) | `set_hint_level` | stage | 지원되는 화면 가이드 단계 중 하나를 다음 연습에 적용하도록 준비한다. |
| [T-10](T-10.md) | `request_recalibration` | stage | 좌표·자세 기준이 유효하지 않을 때 사용자 보정 절차로 전환하도록 준비한다. |
| [T-11](T-11.md) | `pause_practice` | stage | 설명·사용자 요청·복구를 위해 연습 진행을 일시정지하도록 준비한다. |
| [T-12](T-12.md) | `resume_practice` | stage | 유효한 기준과 사용자 재개 의사가 있는 정지 세션을 다시 진행하도록 준비한다. |
| [T-13](T-13.md) | `finish_practice` | stage | 사용자가 종료하거나 사전 정의한 종료 조건이 충족되면 세션 마감과 보고서 생성을 준비한다. |
| [T-14](T-14.md) | `inspect_visual_context` | optional_read | 구조화 지표로 설명이 부족한 카메라 배치·잘림·가림을 제한된 대표 프레임으로 점검한다. |

## 공통 요청·반환

모델이 생성하는 `arguments`와 하네스가 부여하는 `session_id`, `context_id`, `tool_call_id`, `snapshot_ref`, 제어 버전, 예산은 분리한다. 모델 인자로 임의 세션·서버 경로·실행 코드를 받지 않는다.

반환은 [공통 결과 계약](../05-contracts/common-result.md)의 네 필드를 사용한다. 형식상 성공·업무상 보류·명령 적용 상태를 구분한다. 변경 제안의 반환 타입은 `ActionProposal`이며 실제 실행 결과는 `CommandTicket`과 `ApplicationAck`로 따로 확인한다.

## 기본 호출 범위

- 코치: 근거 조회와 허용된 변경 제안 도구를 사용한다.
- 하네스: 도구 목록·세션 범위·실행 예산·명령 발행을 제어한다.
- CV: 프레임별 처리를 LLM Tool 호출로 만들지 않는다.
- 사용자: 수동 정지·종료·장치 변경은 Agent보다 우선한다.

`set_hand_mode`, `play_reference`, 자체 모델 학습, CV 임계값 변경, 접촉 정답 수정, 임의 파일·쉘 실행은 기본 도구로 노출하지 않는다. 실제 제품 기능과 검증 필요가 생기면 별도 계약을 만든다.

[전체 계획](../01-overall-plan.md) · [명령 프로토콜](../05-contracts/command-protocol.md) · [하네스·평가](../06-harness-and-evaluation.md)
