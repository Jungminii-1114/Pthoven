"""Initial PRD policy values, not claims of measured accuracy."""
from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class Policy:
    preparation_ms: float = 1000
    min_collect_ms: float = 3000
    max_collect_ms: float = 5000
    min_valid_frames: int = 45
    max_capture_frames: int = 400
    min_valid_ratio: float = .70
    min_snr: float = 2.5
    min_separation: float = 3.0
    max_score_noise: float = .30
    min_ready_ratio: float = .70
    shape_limit: float = .30
    scale_min: float = .50
    scale_max: float = 1.80
    max_observation_age_ms: float = 200
    ready_stable_ms: float = 160
    ready_observations: int = 3
    release: float = .20

    def __post_init__(self):
        if any(isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError("정책값은 양의 유한 숫자여야 합니다.")
        if not self.min_collect_ms <= self.max_collect_ms or self.scale_min >= self.scale_max:
            raise ValueError("수집 시간/배율 범위가 잘못되었습니다.")
        if self.max_capture_frames < self.min_valid_frames:
            raise ValueError("수집 프레임 한도가 최소 표본보다 작습니다.")
        if any(getattr(self, k) > 1 for k in ["min_valid_ratio", "min_ready_ratio", "max_score_noise", "shape_limit", "release"]):
            raise ValueError("비율 정책은 0~1 범위여야 합니다.")

    def as_dict(self):
        return asdict(self)
