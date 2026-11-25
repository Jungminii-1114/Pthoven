"""Strict transport contracts. Device claims never imply tracker readiness."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator

Finger = Literal["thumb", "index", "middle", "ring", "pinky"]
Number = Annotated[FiniteFloat, Field(ge=0, strict=True)]
Counter = Annotated[int, Field(ge=0, strict=True)]
Text = Annotated[str, Field(min_length=1, max_length=256, strict=True)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class StartRequest(Contract):
    clock_id: Text
    selected_device_id: Text
    selected_hand: Literal["left", "right"] = "right"
    active_fingers: list[Finger] = Field(default_factory=lambda: ["index"], min_length=1, max_length=5)
    input_source: Literal["camera_pose"] = "camera_pose"
    exercise_ref: Literal["builtin-ceg.v1"] = "builtin-ceg.v1"
    policy_version: Literal["b01-policy.v1"] = "b01-policy.v1"

    @field_validator("active_fingers")
    @classmethod
    def unique_fingers(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("활성 손가락은 중복될 수 없습니다.")
        return values


class EpochRequest(Contract):
    command_epoch: Counter


class DeviceRequest(EpochRequest):
    permission: Literal["unknown", "requesting", "granted", "denied"]
    stream: Literal["disconnected", "connecting", "receiving", "stalled", "ended"]
    device_label: str = Field(default="", max_length=256)
    width: Counter = 0
    height: Counter = 0


class GeometryRequest(EpochRequest):
    expected_geometry_version: Counter
    geometry: dict


class CaptureRequest(EpochRequest):
    phase: Literal["rest", "lift"]
    finger: Finger = "index"


class FrameRequest(EpochRequest):
    geometry_version: Counter
    calibration_version: Counter
    clock_id: Text
    frame_id: Counter
    t_ms: Number
    image: str = Field(min_length=1, max_length=1_500_000)
    capture_id: Text | None = None


class AckEvent(Contract):
    event_id: Text
    emitted_ms: Number


class AckRequest(EpochRequest):
    """CONTRACTS 6.2: 음번호·좌표·contact_verified는 받지 않는다. 출력 시각만 결합한다."""
    decision_ref: Text
    clock_id: Text
    events: list[AckEvent] = Field(min_length=1, max_length=64)


class B01Error(Exception):
    def __init__(self, code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.code, self.http_status = code, http_status


def result(data=None, outcome="result_available", *, status="success", diagnostics=None):
    return {"status": status, "data": data, "diagnostics": diagnostics or {}, "outcome": outcome}


def failure(code, message):
    return result(None, code.lower(), status="error", diagnostics={"boundary": {
        "status": "failed", "error_code": code, "reason": message, "retryable": False}})
