"""Bounded event-time coverage, with explicit intervals for unobserved time."""
from collections import deque


class QualityTimeline:
    def __init__(self, max_age_ms=200, capacity=1200):
        self.max_age = max_age_ms
        self.intervals = deque(maxlen=capacity)
        self.last_t = None
        self.last_valid = False
        self.last_reasons = []
        self.processed = self.usable_count = 0

    def observe(self, t_ms, valid, reasons):
        if self.last_t is not None:
            if t_ms <= self.last_t:
                raise ValueError("품질 시각은 증가해야 합니다.")
            split = min(t_ms, self.last_t+self.max_age) if self.last_valid else self.last_t
            if split > self.last_t:
                self.intervals.append({"start_ms": self.last_t, "end_ms": split, "usable": True, "reasons": []})
            if t_ms > split:
                self.intervals.append({"start_ms": split, "end_ms": t_ms, "usable": False,
                                       "reasons": ["TRACKING_GAP"] if self.last_valid else self.last_reasons[:]})
        self.last_t, self.last_valid, self.last_reasons = t_ms, valid, list(reasons)
        self.processed += 1
        self.usable_count += int(valid)

    def snapshot(self, *, stalled=False):
        records = list(self.intervals)
        if stalled and self.last_t is not None:
            if self.last_valid:
                records.append({"start_ms": self.last_t, "end_ms": self.last_t+self.max_age, "usable": True, "reasons": []})
            records.append({"start_ms": self.last_t+self.max_age if self.last_valid else self.last_t,
                            "end_ms": None, "usable": False, "reasons": ["FRAME_STALLED"]})
        usable = [{k: r[k] for k in ["start_ms", "end_ms"]} for r in records if r["usable"]]
        unknown = [{k: r[k] for k in ["start_ms", "end_ms", "reasons"]} for r in records if not r["usable"]]
        duration = lambda flag: sum(r["end_ms"]-r["start_ms"] for r in records if r["usable"] == flag and r["end_ms"] is not None)
        return {"usable_intervals": usable, "unknown_intervals": unknown,
                "denominators": {"processed_frame_count": self.processed,
                    "usable_observation_count": self.usable_count,
                    "usable_duration_ms": duration(True), "unknown_duration_ms": duration(False),
                    "duration_scope": "retained_intervals", "count_scope": "session"}}
