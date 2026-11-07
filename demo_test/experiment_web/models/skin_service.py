"""Local HTTP adapter for paired dorsal crops; never a contact classifier."""
from __future__ import annotations

import base64
import binascii
import math

import cv2
import numpy as np

from models.skin_features import skin_motion


def _patch(value: object) -> np.ndarray:
    if not isinstance(value, str) or len(value) > 100_000:
        raise ValueError("손등 패치는 100KB 이하의 이미지 data URI여야 합니다.")
    prefix, sep, encoded = value.partition(",")
    if not sep or prefix not in {"data:image/jpeg;base64", "data:image/png;base64"}:
        raise ValueError("JPEG 또는 PNG 손등 패치가 필요합니다.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("이미지 base64가 올바르지 않습니다.") from exc
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE) if data else None
    if image is None or not 16 <= min(image.shape) <= max(image.shape) <= 128:
        raise ValueError("손등 패치는 각 변이 16~128픽셀이어야 합니다.")
    return image


def observe_skin(payload: dict) -> dict:
    times = [payload.get("previous_t_ms"), payload.get("t_ms")]
    if any(isinstance(t, bool) or not isinstance(t, (float, int)) or not math.isfinite(t) or t < 0 for t in times):
        raise ValueError("유한한 비음수 프레임 시각이 필요합니다.")
    previous_t, t = times
    dt = t - previous_t
    if not 15 <= dt <= 250:
        raise ValueError("비교 프레임 간격은 15~250ms여야 합니다. 긴 공백 뒤에는 새 쌍을 수집하세요.")
    result = skin_motion(_patch(payload.get("previous_patch")), _patch(payload.get("patch")))
    return {
        **result,
        "previous_t_ms": previous_t,
        "t_ms": t,
        "dt_ms": dt,
        "kind": "dorsal_motion_observation",
        "method": "palm_aligned_farneback_residual_v1",
        "contact_verified": False,
        "tap_classification": None,
    }
