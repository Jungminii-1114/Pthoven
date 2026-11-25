"""기준 자세 구간 길이 기준 적응(이중 길이 기준).

실제 기록(2026-11-25 검지 반복 탭)에서 시행이 반복될수록 탭 사이 기준 자세의
MCP–PIP 정렬비가 보정 기준보다 1.15→1.21→1.33배로 커졌다. 그 결과 기준 자세 프레임이
국소 왜곡(>30%)으로 보류돼 상대 기준을 다시 잡지 못했고, 한 시행은 10회 동작에 후보 0개였다.

이 모듈은 상태 머신이 REST일 때의 과거 프레임만 사용해 구간별 정렬비 중앙값으로
길이 기준을 다시 추정한다. 매 프레임 원래 보정 기준과 적응 기준 중 국소 왜곡이 작은
쪽을 사용한다(이중 기준). 적응 기준만 쓰면 들림 자세의 길이가 기준 자세와 반대로
움직이는 기록에서 오히려 보류가 늘어났기 때문이다.

- 미래 프레임을 쓰지 않는다. 들림·복귀 중에는 갱신하지 않는다.
- 길이 허용치(30%), 배율 범위(0.5~1.8), 판정 임계값은 바꾸지 않는다.
- 적응 폭은 구간별 0.6~1.6배로 제한해 추적 붕괴를 기준으로 흡수하지 않는다.
- 실제 뼈 길이 변화가 아니라 RGB 3D 추정의 자세 의존 편향을 보정하는 휴리스틱이다.
"""
from collections import deque
from dataclasses import dataclass

import numpy as np

from .geometry import length_quality


@dataclass(frozen=True)
class RestLengthOptions:
    window_ms: float = 1000
    min_frames: int = 8
    rest_score_max: float = .5
    accept_shape: float = .8
    ratio_min: float = .6
    ratio_max: float = 1.6


class RestLengthAdapter:
    def __init__(self, model, options=None):
        self.options = options or RestLengthOptions()
        self.base_reference = np.asarray(model["length_reference"], dtype=float)
        self.palm_reference = model["palm_reference"]
        self.buffer = deque()
        self.ratios = None
        self.updates = 0
        self.adapted_frames = 0

    def observe(self, t_ms, feature, base_observation, phase):
        """현재 프레임까지의 기준 자세로 적응 기준을 갱신하고 이번 프레임에 쓸 기준을 고른다."""
        o = self.options
        g = base_observation["geometry"]
        tip, angle = base_observation["tip"]["score"], base_observation["angles"]["score"]
        if (phase == "REST" and tip is not None and angle is not None and tip <= o.rest_score_max and angle <= o.rest_score_max
                and .5 <= g["relative_scale"] <= 1.8 and g["shape_error"] <= o.accept_shape
                and base_observation.get("image_visibility", {"valid": True})["valid"]):
            self.buffer.append((t_ms, np.asarray(g["aligned_ratios"], dtype=float)))
        while self.buffer and self.buffer[0][0] < t_ms - o.window_ms:
            self.buffer.popleft()
        if len(self.buffer) >= o.min_frames:
            self.ratios = np.clip(np.median(np.stack([b[1] for b in self.buffer]), axis=0), o.ratio_min, o.ratio_max)
            self.updates += 1
        info = {"source": "calibration", "adapted_ratios": None if self.ratios is None else self.ratios.tolist(),
                "rest_frames": len(self.buffer), "calibration_shape_error": g["shape_error"], "adapted_shape_error": None}
        if self.ratios is None:
            return None, info
        adapted = self.base_reference * self.ratios
        alt = length_quality(feature["lengths"], adapted, feature["palm_width"], self.palm_reference)
        info["adapted_shape_error"] = alt["shape_error"]
        if alt["shape_error"] < g["shape_error"]:
            info["source"] = "rest_adapted"; self.adapted_frames += 1
            return adapted.tolist(), info
        return None, info
