"""B-04/B-05 contracts from docs/plan/05-contracts (no product policy defaults)."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import Field, model_validator

from .contracts import Contract, Counter, Number, Text

Action = Literal['keep_current', 'observe_more', 'recalibrate', 'intervene', 'pause', 'resume', 'finish']


class Versions(Contract):
    config_version: Counter
    calibration_version: Counter
    geometry_version: Counter
    command_epoch: Counter


class AppliedPracticeConfig(Contract):
    bpm: Number = Field(gt=0)
    start_index: Counter
    end_index: Counter
    hint_level: Counter
    config_version: Counter

    @model_validator(mode='after')
    def ordered_range(self):
        if self.start_index > self.end_index:
            raise ValueError('Invalid inclusive note range')
        return self


class PracticeSnapshot(Contract):
    snapshot_id: Text
    state_version: Counter
    session_id: Text
    attempt_id: Text
    exercise_ref: Text
    clock_id: Text
    actual_config: AppliedPracticeConfig
    calibration_version: Counter
    geometry_version: Counter
    command_epoch: Counter
    observation_status: Text
    contact_verified: Literal[False]
    metrics: dict[str, Any]
    denominators: dict[str, Any]
    evidence_refs: list[Text]
    unknown_intervals: list[dict[str, Any]]

    def versions(self):
        return Versions(config_version=self.actual_config.config_version,
                        calibration_version=self.calibration_version,
                        geometry_version=self.geometry_version, command_epoch=self.command_epoch)


class CoachingTrigger(Contract):
    trigger_id: Text
    kind: Literal['user_request', 'segment_end', 'quality_degraded', 'reevaluation', 'session_end']
    session_id: Text
    attempt_id: Text
    clock_id: Text
    t_ms: Number


class RunPolicy(Contract):
    policy_version: Text
    cooldown_ms: Number
    max_runs: Counter
    max_model_calls: Counter = Field(gt=0)
    max_tool_calls: Counter
    max_tokens: Counter = Field(gt=0)
    max_context_bytes: Counter = Field(gt=0)
    max_tool_result_bytes: Counter = Field(gt=0)
    total_time_ms: Number = Field(gt=0)
    max_revisions: Counter


class ActionPolicy(Contract):
    policy_version: Text
    allowed_tools: list[Text]
    min_bpm: Number = Field(gt=0)
    max_bpm: Number = Field(gt=0)
    max_bpm_delta: Number
    note_count: Counter = Field(gt=0)
    hint_levels: list[Counter]
    command_ttl_ms: Number = Field(gt=0)
    ack_timeout_ms: Number = Field(gt=0)
    apply_at: Literal['next_segment', 'paused']

    @model_validator(mode='after')
    def bpm_bounds(self):
        if self.min_bpm > self.max_bpm:
            raise ValueError('Invalid BPM policy')
        return self


class LiveControlState(Contract):
    session_id: Text
    attempt_id: Text
    exercise_ref: Text
    clock_id: Text
    phase: Text
    actual_config: AppliedPracticeConfig
    versions: Versions
    observation_status: Text
    calibration_valid: bool
    user_resume_requested: bool
    finish_authorized: bool

    @model_validator(mode='after')
    def actual_version(self):
        if self.actual_config.config_version != self.versions.config_version:
            raise ValueError('Actual configuration version mismatch')
        return self


class CoachRequest(Contract):
    request_id: Text
    trigger_ref: Text
    snapshot_ref: Text
    priority: Literal['quality', 'user', 'normal']
    allowed_action_classes: list[Action]
    budget_ref: Text


class Evidence(Contract):
    evidence_ref: Text
    session_id: Text
    attempt_id: Text
    exercise_ref: Text
    versions: Versions
    kind: Text
    values: dict[str, Any]


class Claim(Contract):
    evidence_ref: Text
    field: Text
    value: Any
    unit: Text | None


class CoachContext(Contract):
    context_id: Text
    snapshot_ref: Text
    instruction_version: Text
    allowed_tool_ids: list[Text]
    evidence_refs: list[Text]
    history_refs: list[Text]
    remaining_budget: dict[str, Any]
    snapshot: PracticeSnapshot
    evidence: list[Evidence]
    history: list[dict[str, Any]]
    tool_definitions: list[dict[str, Any]]
    instructions: str


class ActionProposal(Contract):
    proposal_id: Text
    context_id: Text
    tool_call_id: Text
    operation: Text
    arguments: dict[str, Any]
    snapshot_ref: Text
    lifecycle: Literal['staged', 'selected', 'discarded'] = 'staged'
    actual_setting_changed: Literal[False] = False


class CoachPlan(Contract):
    decision_id: Text
    snapshot_ref: Text
    evidence_refs: list[Text]
    action: Action
    proposal_refs: list[Text]
    rationale: dict[Literal['observation', 'hypothesis', 'reason'], str]
    review_criteria: list[dict[str, Any]]
    user_message: Text
    claims: list[Claim]


class ValidatedPlan(Contract):
    validation_id: Text
    plan_ref: Text
    session_id: Text
    attempt_ref: Text
    exercise_ref: Text
    clock_id: Text
    snapshot_ref: Text
    proposal_refs: list[Text]
    change_set: list[ActionProposal]
    expected_versions: Versions
    expected_phase: Text
    apply_at: Literal['next_segment', 'paused']
    expires_at: Number
    validation_findings: list[str]


class CommandTicket(Contract):
    command_id: Text
    decision_id: Text
    session_id: Text
    attempt_ref: Text
    exercise_ref: Text
    clock_id: Text
    snapshot_ref: Text
    proposal_refs: list[Text]
    change_set: list[ActionProposal]
    expected_versions: Versions
    expected_phase: Text
    command_epoch: Counter
    apply_at: Literal['next_segment', 'paused']
    issued_at: Number
    expires_at: Number
    deadline: Number
    lifecycle: Literal['queued'] = 'queued'


class ApplicationAck(Contract):
    session_id: Text
    command_id: Text
    result: Literal['applied', 'rejected', 'application_unknown']
    applied_values: dict[str, Any] | None
    actual_versions: Versions
    applied_at_ms: Number | None
    reason_code: Text
    client_clock_id: Text
    effective_attempt_ref: Text | None


class ClientStateProbe(Contract):
    session_id: Text
    command_id: Text
    client_clock_id: Text
    # A current value alone does not prove which command changed it.
    command_ack: ApplicationAck | None


class InterventionRecord(Contract):
    intervention_id: Text
    command_ref: Text
    requested_values: list[dict[str, Any]]
    actual_values: dict[str, Any] | None
    lifecycle: Literal['confirmed_applied', 'confirmed_not_applied', 'reconciliation_required']
    effective_attempt_ref: Text | None
    reconciliation_reason: Text
    actual_versions: Versions | None
    applied_at_ms: Number | None
