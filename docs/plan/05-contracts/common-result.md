# 공통 반환 계약 · NodeResult[T]

[계약 목록](README.md) · [Small Node](../03-small-nodes/README.md)

## 1. 네 필드

| 키 | 타입 | 의미 |
| --- | --- | --- |
| status | success / partial / error | 필수·선택 처리 단계의 실행 상태 |
| data | T 또는 null | 안전하게 사용할 수 있는 업무 산출물 |
| diagnostics | 내부 단계명→진단 객체 | 실행·생략·실패 위치와 이유 |
| outcome | 문자열 또는 null | 실행 성공 여부와 별개인 업무 결과 |

모든 Small Node와 Tool의 반환 블록에 적용한다. run_id·시각·세션 같은 호출 메타데이터는 바깥 실행 기록에 연결한다. 반환과 저장은 별개이며 하네스가 필요한 기록을 남긴다.

## 2. 실행 상태

- `success`: 필수·활성 처리 단계가 정상 완료되었다. 정상적인 빈 목록·보류 판단·정보 부족 결과도 가능하다.
- `partial`: 일부 실행이 실패했으나 범위가 명확한 안전한 산출물을 반환할 수 있다. unknown 표본이 있다는 이유만으로 partial을 쓰지 않는다.
- `error`: 필수 처리 실패나 범위·버전 검증 실패로 안전한 결과를 제공할 수 없다. 원칙적으로 data=null이다.

비활성 선택 기능의 skipped는 실행 실패가 아니다. 모델·저장소 오류를 ‘사용자 입력 없음’이나 ‘오류 없음’으로 변환하지 않는다.

## 3. 서로 다른 상태 축

| 축 | 예시 | 무엇을 말하는가 |
| --- | --- | --- |
| status | success | 함수가 계약대로 실행되었는가 |
| outcome | hold_required / no_cycle / action_staged | 업무적으로 어떤 결과인가 |
| data.quality_state | usable_provisional / hold_required | 관측을 평가에 사용할 수 있는가 |
| data.input_status | accepted_provisional / withheld | 가상 입력을 수용했는가 |
| data.lifecycle | staged / queued / applied 등 | 제안·명령이 어느 단계인가 |
| contact_verified | false | 실제 물리 접촉이 독립적으로 확인되었는가 |

예를 들어 도구가 정상 실행되어 변경 제안을 저장하면 status=success, outcome=action_staged이다. 이는 실제 적용 성공을 뜻하지 않는다. 유효한 관측에 후보가 없으면 success/no_cycle이지 탐지기 오류가 아니다.

## 4. 단계별 진단

각 내부 함수명 아래에 `status: success / failed / skipped`, `error_code`, `reason`, `retryable`, 선택적 `duration_ms`, `count`를 둔다. 모델·런타임·정책·시각·근거 참조는 해당 실행 기록에 연결한다.

오류 재시도 가능성은 힌트다. 하네스가 남은 예산·동일한 전제 조건·중복 부작용 여부를 다시 확인한다.

## 5. 반환 예시

### 정상 관측, 후보 없음

```json
{
  "status": "success",
  "data": {"candidates": [], "contact_verified": false},
  "diagnostics": {"advance_finger_machine": {"status": "success", "count": 0}},
  "outcome": "no_cycle"
}
```

### 관측 보류를 정상적으로 판정

```json
{
  "status": "success",
  "data": {
    "quality_state": "hold_required",
    "unknown_intervals": [{"start_ms": 1000, "end_ms": 1500, "reason": "HAND_NOT_OBSERVED"}]
  },
  "diagnostics": {"check_observation_validity": {"status": "success"}},
  "outcome": "hold_required"
}
```

### 필수 모델 실행 실패

```json
{
  "status": "error",
  "data": null,
  "diagnostics": {"call_text_model": {"status": "failed", "error_code": "MODEL_TIMEOUT", "retryable": true}},
  "outcome": "model_unavailable"
}
```

## 6. 빈 값과 근거 부족

- `[]`: 그 범위에서 정상 처리한 결과가 빈 목록임을 뜻한다. 다른 사실의 부재를 입증하지 않는다.
- `null`: 계산·관측·접근할 수 없는 값이다. 수치 0과 다르다.
- `unknown_intervals`: 평가가 불가능한 시간 범위다. 실수 없는 구간이라는 뜻이 아니다.
- `truth: []`: 독립 검토에서 접촉 정답 이벤트가 없음을 확인한 시행이다.
- `truth: null`: 정답 라벨이 제공되지 않은 시행이다.

부분 라벨 시행은 known·unknown 구간을 별도 정의하고 known 구간에서만 성능을 계산한다. 모델 예측과 안내 시각을 정답으로 복사하지 않는다.

## 7. 공통 오류와 검증

범위 위반, 시계 불일치, 버전 불일치, 오래된 epoch, 미지원 도구, 데이터 손상은 폴백 결과로 숨기지 않는다. 원시 비밀값·불필요한 프레임을 오류 문자열에 넣지 않는다.

검증은 네 키 존재, 허용 status, data 타입, 진단 위치, outcome 의미, 근거·단위·버전을 따로 확인한다. JSON Schema 통과는 문장 의미나 실제 접촉 정확도를 입증하지 않는다.
