"""TIP 들림→복귀 궤적과 관절 변화를 함께 요구하는 손가락별 상태 머신."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, asdict
import math
import numpy as np

from .calibration import ALGORITHM, measure
from .geometry import FINGERS, TIP_INDICES, ANGLE_INDICES, extract_features
from .temporal import CausalFeatureMedian


@dataclass(frozen=True)
class TapOptions:
    lift: float = .55
    release: float = .20
    angle_lift: float = .55
    angle_release: float = .20
    min_lift_ms: float = 60
    settle_ms: float = 40
    cooldown_ms: float = 100
    max_gap_ms: float = 200
    max_cycle_ms: float = 2500
    min_tip_excursion: float = .5
    min_tip_path: float = .65

    def __post_init__(self):
        values = asdict(self)
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or v < 0 for v in values.values()) or not self.release < self.lift or not self.angle_release < self.angle_lift or self.max_gap_ms <= 0:
            raise ValueError("상태 머신 설정값이 올바르지 않습니다.")


def validate_model(model, finger=None):
    if not isinstance(model, dict) or model.get("algorithm") != ALGORITHM or model.get("finger") not in FINGERS or (finger and model["finger"] != finger):
        raise ValueError("Python TIP·관절 보정 모델이 아닙니다. Python에서 다시 보정하세요.")
    reference = np.asarray(model.get("length_reference"), dtype=float)
    palm = model.get("palm_reference")
    if reference.shape != (3,) or not np.isfinite(reference).all() or np.min(reference) <= 0 or not isinstance(palm, (float, int)) or not math.isfinite(palm) or palm <= 0:
        raise ValueError("보정 길이 기준이 올바르지 않습니다.")
    for group, allowed in [("tip", TIP_INDICES), ("angles", ANGLE_INDICES)]:
        g = model.get("groups", {}).get(group, {})
        arrays = {k: np.asarray(g.get(k), dtype=float) for k in ("weights", "base", "delta")}
        if any(a.shape != (8,) or not np.isfinite(a).all() for a in arrays.values()):
            raise ValueError("보정 특징은 유한한 8차원 벡터여야 합니다.")
        w, d = arrays["weights"], arrays["delta"]
        if np.min(w) < 0 or any(w[i] > 0 for i in range(8) if i not in allowed) or float(np.sum(w * d * d)) <= 0:
            raise ValueError("TIP·관절 가중치가 올바르지 않습니다.")
    return model


def observation_problems(observation):
    """품질 탈락을 구체화한다. 판정 임계값이나 관측값은 바꾸지 않는다."""
    if "geometry" not in observation:
        return ["landmarks_unavailable"]
    reasons = list(observation["geometry"].get("reasons", []))
    if not observation.get("image_visibility", {"valid": True})["valid"]:
        reasons.append("target_out_of_frame")
    for group in ("tip", "angles"):
        g = observation[group]
        if g["score"] is None:
            reasons.append(f"{group}_features_missing")
        elif not g["valid"]:
            reasons.append(f"{group}_outside_calibration")
    return reasons or ["observation_invalid"]


class FingerMachine:
    def __init__(self, options=None):
        self.options = options or TapOptions(); self.last_time = None; self.last_event = -math.inf
        self.status_counts = Counter(); self.interruption_counts = Counter(); self.last_interruption = None
        self.reset()

    def reset(self):
        # 한 동작의 상태만 지운다. 누적 진단은 세션이 끝날 때까지 보존한다.
        self.phase = "REST"; self.ready = False; self.lift_at = None; self.return_at = None
        self.previous = None; self.previous_vector = None; self.tip_peak = 0.; self.angle_peak = 0.
        self.tip_path = 0.; self.return_speed = 0.; self.minimum_quality = 1.; self.rest_tip = 0.

    def interrupt(self, t_ms, code):
        if self.phase != "REST":
            self.interruption_counts[code] += 1
            self.last_interruption = {"t_ms": t_ms, "code": code, "phase": self.phase}
        self.reset()

    def report(self, t_ms, code, message, temporal, *, blockers=(), metrics=None):
        self.status_counts[code] += 1
        return {**temporal, "phase": self.phase, "decision": {
            "code": code, "message": message, "blockers": list(blockers), "ready": self.ready,
            "thresholds": asdict(self.options), "status_counts": dict(self.status_counts),
            "interruption_counts": dict(self.interruption_counts), "last_interruption": self.last_interruption,
            "metrics": metrics or {"tip_excursion": self.tip_peak - self.rest_tip,
                                    "tip_path": self.tip_path, "return_speed": self.return_speed,
                                    "lift_elapsed_ms": None if self.lift_at is None else t_ms-self.lift_at,
                                    "settle_elapsed_ms": None if self.return_at is None else t_ms-self.return_at}}}

    def update(self, t_ms, observation):
        if isinstance(t_ms, bool) or not isinstance(t_ms, (float, int)) or not math.isfinite(t_ms) or t_ms < 0 or (self.last_time is not None and t_ms <= self.last_time):
            raise ValueError("프레임 시각은 유한한 비음수이며 단조 증가해야 합니다.")
        gap = None if self.last_time is None else t_ms - self.last_time
        self.last_time = t_ms; o = self.options
        temporal = {"tip_velocity": None, "angle_velocity": None}
        if not observation.get("valid") or (gap is not None and gap > o.max_gap_ms):
            blockers = observation_problems(observation) if not observation.get("valid") else []
            if gap is not None and gap > o.max_gap_ms:
                blockers.append("tracking_gap")
            code = blockers[0]
            self.interrupt(t_ms, code)
            return None, self.report(t_ms, code, "관측 보류로 동작 기록 초기화 · 기준 자세부터 다시 확인", temporal, blockers=blockers)
        if observation.get("neutral_pending"):
            self.reset()
            return None, self.report(t_ms, "waiting_for_neutral", "동작 직전의 안정된 자세 관측 중", temporal)
        tip, angle = observation["tip"]["score"], observation["angles"]["score"]
        vector = np.asarray(observation["tip_vector"])
        velocity = (tip - self.previous[0]) * 1000 / gap if self.previous is not None and gap else 0.
        angular_velocity = (angle - self.previous[1]) * 1000 / gap if self.previous is not None and gap else 0.
        step = float(np.linalg.norm(vector - self.previous_vector)) if self.previous_vector is not None else 0.
        self.previous, self.previous_vector = (tip, angle), vector
        temporal = {"tip_velocity": velocity, "angle_velocity": angular_velocity}
        resting, lifted = tip <= o.release and angle <= o.angle_release, tip >= o.lift and angle >= o.angle_lift
        if not self.ready:
            if resting:
                self.ready = True; self.rest_tip = tip
            return None, self.report(t_ms, "rest_armed" if resting else "waiting_for_rest",
                "기준 자세 확인 · 들림 대기" if resting else "TIP·관절 모두 복귀 기준 이하가 되어야 시작", temporal)
        if self.lift_at is not None and t_ms - self.lift_at > o.max_cycle_ms:
            self.interrupt(t_ms, "cycle_timeout")
            return None, self.report(t_ms, "cycle_timeout", "들림 후 복귀 시간 초과 · 새 동작 대기", temporal)
        code, message, blockers = "waiting_for_lift", "들림 대기", []
        if self.phase == "REST":
            if resting:
                # 들림 직전 복귀 경계에 가까운 마지막 점으로 바닥을 덮지 않는다.
                # 관측한 기준 구간의 최저 TIP부터 최고점까지의 이동폭을 측정한다.
                self.rest_tip = min(self.rest_tip, tip)
            if lifted and t_ms - self.last_event >= o.cooldown_ms:
                self.phase = "LIFTED"; self.lift_at = t_ms
                self.tip_peak, self.angle_peak, self.tip_path = tip, angle, step
                self.minimum_quality = observation["confidence"]
                code, message = "lift_started", "들림 확인 · 복귀 대기"
            elif lifted:
                code, message = "cooldown", "연속 입력 간격 대기"
            else:
                blockers = (["tip_below_lift"] if tip < o.lift else []) + (["angles_below_lift"] if angle < o.angle_lift else [])
                message = "들림 대기 · TIP·관절 모두 들림 기준 이상 필요"
        else:
            self.tip_path += step; self.tip_peak = max(self.tip_peak, tip); self.angle_peak = max(self.angle_peak, angle)
            self.minimum_quality = min(self.minimum_quality, observation["confidence"])
            self.return_speed = max(self.return_speed, -velocity)
            code, message = "waiting_for_return", "복귀 대기 · TIP·관절 모두 복귀 기준 이하 필요"
            blockers = (["tip_above_release"] if tip > o.release else []) + (["angles_above_release"] if angle > o.angle_release else [])
            if self.phase == "LIFTED" and resting:
                if t_ms - self.lift_at < o.min_lift_ms:
                    elapsed = t_ms-self.lift_at
                    self.interrupt(t_ms, "lift_too_short")
                    return None, self.report(t_ms, "lift_too_short", f"관측된 들림 {elapsed:.0f}ms · 최소 {o.min_lift_ms:.0f}ms 미달", temporal)
                self.phase = "RETURNED"; self.return_at = t_ms
                code, message, blockers = "return_started", "복귀 확인 · 유지 시간 확인 중", []
            elif self.phase == "RETURNED":
                if not resting:
                    self.phase = "LIFTED"; self.return_at = None
                    code, message = "return_unstable", "복귀 유지 중 기준을 벗어남 · 복귀 다시 확인"
                elif t_ms - self.return_at >= o.settle_ms:
                    blockers = (["tip_excursion_short"] if self.tip_peak - self.rest_tip < o.min_tip_excursion else []) + (["tip_path_short"] if self.tip_path < o.min_tip_path else []) + (["tip_return_missing"] if self.return_speed <= 0 else [])
                    enough_motion = not blockers
                    metrics = {"tip_excursion": self.tip_peak-self.rest_tip, "tip_path": self.tip_path,
                               "return_speed": self.return_speed, "lift_elapsed_ms": self.return_at-self.lift_at,
                               "settle_elapsed_ms": t_ms-self.return_at}
                    event = {"t_ms": self.return_at, "emitted_ms": t_ms, "decision_latency_ms": t_ms - self.return_at,
                             "tip_excursion": self.tip_peak - self.rest_tip, "tip_path_normalized": self.tip_path,
                             "tip_return_speed": self.return_speed, "angle_peak": self.angle_peak,
                             "confidence": self.minimum_quality, "confidence_type": "heuristic_not_probability",
                             "kind": "vision_tap_candidate", "contact_verified": False} if enough_motion else None
                    self.last_event = t_ms if event else self.last_event
                    if not event:
                        self.interruption_counts["insufficient_tip_motion"] += 1
                        self.last_interruption = {"t_ms": t_ms, "code": "insufficient_tip_motion", "phase": self.phase}
                    self.reset(); self.ready = True; self.rest_tip = tip
                    return event, self.report(t_ms, "candidate_emitted" if event else "insufficient_tip_motion",
                        "타건 후보 1회" if event else "TIP 이동 궤적·복귀 속도 조건 미달", temporal, blockers=blockers, metrics=metrics)
                else:
                    code, message, blockers = "settling", "복귀 유지 시간 확인 중", []
        return None, self.report(t_ms, code, message, temporal, blockers=blockers)


class PoseTapDetector:
    def __init__(self, calibration=None, options=None, mode="fixed"):
        if mode not in ("fixed", "local_baseline"):
            raise ValueError("지원하지 않는 판정 방식입니다.")
        self.options = options or TapOptions(); self.models = {}; self.machines = {}; self.filters = {}; self.last_time = None; self.mode = mode
        for finger, model in (calibration or {}).items():
            self.models[finger] = validate_model(model, finger)
            if mode == "local_baseline":
                from .local_baseline import LocalBaselineMachine
                self.machines[finger] = LocalBaselineMachine(self.options, angle_noise=model.get("diagnostics", {}).get("phases", {}).get("rest", {}).get("score_noise", {}).get("angles", .22))
                self.filters[finger] = CausalFeatureMedian()
            else:
                self.machines[finger] = FingerMachine(self.options)

    def update(self, t_ms, world_landmarks, landmarks=None):
        if isinstance(t_ms, bool) or not isinstance(t_ms, (float, int)) or not math.isfinite(t_ms) or t_ms < 0 or (self.last_time is not None and t_ms <= self.last_time):
            raise ValueError("프레임 시각은 단조 증가해야 합니다.")
        self.last_time = t_ms; signals, events = {}, []
        for finger, model in self.models.items():
            try:
                feature = extract_features(world_landmarks, finger, landmarks); observation = measure(feature, model)
                if finger in self.filters:
                    raw = observation
                    # 평가 손가락 화면 이탈·심한 국소 붕괴·극단 배율은 과거 값으로 덮지 않는다.
                    hard_invalid = not raw['image_visibility']['valid'] or raw['geometry']['shape_error'] > .5 or not .25 <= raw['geometry']['relative_scale'] <= 3.6
                    if hard_invalid:
                        self.filters[finger].clear()
                    else:
                        filtered, meta = self.filters[finger].update(t_ms, feature)
                        observation = measure(filtered, model)
                        observation['feature_filter'] = {**meta, 'raw_valid': raw['valid'],
                            'raw_tip_score': raw['tip']['score'], 'raw_angle_score': raw['angles']['score'],
                            'raw_geometry_reasons': raw['geometry']['reasons']}
                        if not raw['valid']:
                            observation['confidence'] *= .8
            except (ValueError, TypeError, IndexError, KeyError) as error:
                if finger in self.filters:
                    self.filters[finger].clear()
                observation = {"valid": False, "reason": str(error), "confidence": 0., "score": None}
            event, temporal = self.machines[finger].update(t_ms, observation)
            signals[finger] = {**observation, **temporal}
            if event:
                events.append({**event, "finger": finger, "tip": observation["tip_image"],
                               "feature_filter_age_ms": observation.get('feature_filter', {}).get('age_ms', 0)})
        return {"t_ms": t_ms, "signals": signals, "events": events}
