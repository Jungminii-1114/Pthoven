# 변경 제안·실제 적용·ACK 프로토콜

[계약 목록](README.md) · [B-05](../02-big-nodes/B-05.md) · [Tool](../04-tools/README.md)

## 1. 왜 단계를 나누는가

모델이 set_tempo를 요청한 시각, 서버가 요청을 수락한 시각, 화면에 실제 BPM이 바뀐 시각은 다르다. 적용되지 않은 설정으로 다음 연습을 설명하거나 늦은 응답이 새 사용자 선택을 덮어쓰지 않도록 분리한다.

## 2. 두 수명주기

```text
ActionProposal:
  staged → selected 또는 discarded

CommandTicket / Intervention:
  queued → applied / rejected / expired_before_apply / reconciliation_required
  reconciliation_required → confirmed_applied / confirmed_not_applied / unresolved
```

만료는 미적용 증거가 아니다. 클라이언트가 적용 전 만료를 확인했을 때만 expired_before_apply가 된다. 서버의 ACK 대기 시간이 끝났다면 reconciliation_required로 두고 실제 상태를 확인한다.

## 3. 처리 순서

1. S-11이 조회 도구로 정보를 얻는다.
2. 변경 도구는 ActionProposal을 stage하고 모델에 제안 영수증을 반환한다. 화면은 그대로다.
3. 모델이 최종 CoachPlan에 선택한 proposal_refs를 연결한다.
4. S-12가 전체 계획·근거·최종 예상 설정·버전·사용자 의사를 검증한다.
5. S-13이 변경을 한 change_set으로 발행하고 queued 기록을 남긴다.
6. S-14가 적용 경계·전제 조건을 다시 확인한 뒤 전체를 적용하거나 전부 거절한다.
7. S-14가 실제 값과 시각을 포함한 ACK를 보낸다.
8. S-15가 요청·실제 값·버전·epoch를 대조하고 실제 개입을 확정한다.
9. 다음 시도의 조건은 확인된 실제 값에 연결한다.

유지·추가 관측 계획처럼 변경 묶음이 비어 있으면 S-13 명령을 발행하지 않는다. 최종 계획이 선택하지 않은 staged 제안도 실제로 실행하지 않고 폐기한다.

## 4. 변경 묶음과 충돌

set_tempo, set_practice_range, set_hint_level은 하나의 설정 change_set으로 묶을 수 있다. 같은 필드를 서로 다른 값으로 바꾸는 제안은 최종 선택을 요구한다.

request_recalibration, pause_practice, resume_practice, finish_practice 같은 제어 전환은 서로 또는 설정 변경 묶음과 혼합하지 않는다. 보정 요청 적용은 보정 화면·평가 보류로 전환했다는 뜻이며, 실제 새 보정 완료는 S-03의 별도 결과다.

## 5. 요청 메타데이터

command_id, decision_id, selected proposal_refs, session_id, attempt_ref, snapshot_ref, expected_config_version, expected calibration·geometry 버전, command_epoch, apply_at, 유효 시간·발행 시각을 하네스가 부여한다. 모델이 session_id·버전을 조작해 요청을 새 상태로 보이게 만들 수 없다.

apply_at은 next_segment 또는 paused 등 지원되는 경계만 허용한다. 즉시 품질 보류는 시스템 경로에서 수행하고 평가 타이머·입력 누락 계산도 함께 정지한다.

## 6. 원자 적용

클라이언트는 명령 전체를 검사하고 적용 전 상태를 보관한 뒤 한 번에 새 설정으로 전환한다. 일부 인자가 무효면 아무것도 바꾸지 않는다. 묶음 단위 원자 적용이 불가능한 UI 기능은 이 계약에 노출하기 전에 별도 보상·실패 의미를 정의한다.

config_version은 실제 적용 때 갱신한다. 서버는 ACK·상태 조정으로 같은 버전을 확인한다. 한 세션은 확인되지 않은 상충 명령을 병렬로 실행하지 않는다.

## 7. 중복·오래된 판단

- context_id + tool_call_id는 같은 proposal_id를 재사용한다.
- session_id + decision_id는 같은 command_id를 재사용한다.
- 클라이언트는 적용한 command_id와 원래 ACK를 보존하고 재전송에 다시 적용하지 않는다.
- 장치 재연결, 사용자 수동 변경, 즉시 품질 보류, 보정 전환은 command_epoch를 갱신해 이전 명령을 차단한다.
- 적용 전 실제 설정·보정·phase·유효 시간이 달라지면 거절하거나 새 코칭 사건으로 재판단한다.

## 8. ACK와 불명 상태

ACK에는 command_id, result, actual_values, actual config·calibration·geometry 버전, command_epoch, applied_at_ms, client_clock_id, reason_code를 포함한다. 모델의 ‘적용했다’ 문장은 ACK가 아니다.

ACK가 누락되면 같은 ID로 조회·재전송하여 실제 상태를 조정한다. 불명 상태가 풀리기 전 다른 설정을 덮어쓰지 않는다. 늦은 ACK는 원래 명령에만 연결하고 요청 값과 다르면 충돌로 기록한다.

## 9. 사용자 제어·종료

사용자의 수동 정지·종료·장치 선택이 우선이다. resume 도구의 reason은 재개 의사를 증명하지 않으며 실제 UI 사건 또는 명시한 정책을 확인한다. finish도 종료 사건·정책에 연결한다.

세션 마감 시 진행 중 시도·중단 범위·미해결 명령을 보존한다. 불명 상태는 미적용 또는 성공으로 덮어쓰지 않고 보고서 제한에 반영한다.

## 10. 전달 방식과 검증

HTTP 요청·조회로 먼저 구현할 수 있으며 푸시가 필요할 때 WebSocket을 선택할 수 있다. 전달 방식은 계약과 독립적이다.

검증 사례는 재전송, 지연 ACK, 적용 후 ACK 유실, 적용 전 만료, 재연결 이전 명령, 수동 변경 충돌, 부분 설정 무효, 모델 장애, 클라이언트 재시작을 포함한다. 최종 사용자 문구는 제안·대기·실제 적용·불명 상태와 일치해야 한다.
