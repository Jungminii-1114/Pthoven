"""현재·과거 특징만 사용하는 인과적 중앙값 필터. 접촉·실제 깊이 추정이 아니다."""
from collections import deque
import math
import numpy as np


class CausalFeatureMedian:
    def __init__(self, max_frames=5, max_span_ms=100):
        self.max_frames = max_frames; self.max_span_ms = max_span_ms
        self.rows = deque(maxlen=max_frames); self.last_time = None

    def clear(self):
        self.rows.clear(); self.last_time = None

    def update(self, t_ms, feature):
        if not isinstance(t_ms, (float, int)) or isinstance(t_ms, bool) or not math.isfinite(t_ms) or (self.last_time is not None and t_ms <= self.last_time):
            raise ValueError("필터 프레임 시각은 유한하며 단조 증가해야 합니다.")
        if self.last_time is not None and t_ms-self.last_time > self.max_span_ms:
            self.rows.clear()
        if self.rows and not np.array_equal(feature['present'], self.rows[-1][1]['present']):
            self.rows.clear()
        self.last_time = t_ms
        self.rows.append((t_ms, feature))
        while self.rows and t_ms-self.rows[0][0] > self.max_span_ms:
            self.rows.popleft()
        result = dict(feature)
        for key in ('vector', 'lengths', 'palm_width'):
            result[key] = np.median(np.stack([row[key] for _, row in self.rows]), axis=0)
        result['palm_width'] = float(result['palm_width'])
        result['tip_local'] = result['vector'][:3].tolist()
        result['angles'] = result['vector'][3:6].tolist()
        meta = {'name': 'causal_feature_median', 'frames': len(self.rows), 'max_frames': self.max_frames,
                'span_ms': t_ms-self.rows[0][0], 'age_ms': float(t_ms-np.median([t for t, _ in self.rows]))}
        return result, meta
