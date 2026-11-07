"""실험: 안정된 동작 직전 자세를 기준으로 TIP/관절의 상대 왕복을 검사한다.

고정 보정의 방향·스케일과 길이/잔차 검사를 유지한다. 안정 구간의 의미는
물리 접촉이 아니다. 들림 중에는 기준을 갱신하지 않아 움직임을 기준에 흡수하지 않는다.
"""
from collections import deque
from dataclasses import replace
import math
import numpy as np

from .detector import FingerMachine, TapOptions, observation_problems


class LocalBaselineMachine:
    def __init__(self, options=None, angle_noise=.22):
        # TIP은 주요 왕복 신호, 관절은 같은 방향 변화의 보조 증거다.
        # 관절 최소 변화는 보정 시 기준 점수 노이즈의 2.5배 이상이며,
        # 작은 흔들림을 받지 않도록 최소 .25를 함께 요구한다.
        self.machine = FingerMachine(replace(options or TapOptions(), angle_lift=max(.25, 2.5*angle_noise)))
        self.reference = None
        self.window = deque()
        self.last_time = None
        self.reference_updates = 0
        self.last_valid_time = None
        self.hold_since = None
        self.held_frames = 0
        self.cycle_held_frames = 0
        self.reference_recoveries = 0
        self.reference_updated_ms = None
        self.neutral_skipped_frames = 0
        self.reference_reuses = 0
        self.known_rest_started_ms = None
        self.known_rest_frames = 0

    @staticmethod
    def soft_quality_failure(observation):
        if "geometry" not in observation or not observation.get("image_visibility", {"valid": True})["valid"]:
            return False
        g = observation["geometry"]
        if g["shape_error"] > .5 or not .5 <= g["relative_scale"] <= 1.8:
            return False
        return all(r["score"] is not None and -.5 <= r["score"] <= 2 and r["residual"] <= 1.2
                   for r in [observation["tip"], observation["angles"]])

    def update(self, t_ms, observation):
        if isinstance(t_ms, bool) or not isinstance(t_ms, (float, int)) or not math.isfinite(t_ms) or t_ms < 0 or (self.last_time is not None and t_ms <= self.last_time):
            raise ValueError("프레임 시각은 단조 증가해야 합니다.")
        gap = None if self.last_time is None else t_ms-self.last_time
        self.last_time = t_ms
        reacquired = False
        reused = False
        valid = observation.get("valid")
        if not valid or (gap is not None and gap > self.machine.options.max_gap_ms):
            self.known_rest_started_ms = None; self.known_rest_frames = 0
        raw = np.asarray([observation["tip"]["score"], observation["angles"]["score"]]) if valid else None
        # 손 좌표·평가 손가락이 관측되지만 품질이 잠깐 경계를 넘으면 최대 80ms 유예.
        # 탈락 프레임은 점수/궤적/판정에 넣지 않는다. 손 소실·프레임 이탈·심한 왜곡은
        # 즉시 취소한다. 유예 중 후보를 발행하지 않으며 복귀 유지 시간은 다시 확인한다.
        if not valid and not self.machine.ready and self.window and self.last_valid_time is not None and self.soft_quality_failure(observation) and t_ms-self.last_valid_time <= 80:
            # 초기 기준 수집에서도 짧은 품질 탈락 때문에 이미 관측한 안정 구간을
            # 매번 버리지 않는다. 탈락 좌표 자체는 수집하지 않고 시간만 진행한다.
            self.neutral_skipped_frames += 1
            self.machine.last_time = t_ms; self.machine.reset()
            event = None
            temporal = self.machine.report(t_ms, "neutral_quality_skip", "기준 수집 중 짧은 품질 탈락 · 좌표 제외 후 재관측", {"tip_velocity": None, "angle_velocity": None}, blockers=observation_problems(observation))
        elif not valid and self.machine.ready and self.reference is not None and self.last_valid_time is not None and self.soft_quality_failure(observation) and t_ms-self.last_valid_time <= 80:
            self.window.clear()
            self.hold_since = self.last_valid_time
            self.held_frames += 1; self.cycle_held_frames += 1
            if self.machine.lift_at is not None and gap is not None:
                self.machine.lift_at += gap
            if self.machine.phase == "RETURNED":
                self.machine.phase = "LIFTED"; self.machine.return_at = None
            self.machine.last_time = t_ms
            self.machine.previous = None; self.machine.previous_vector = None
            event = None
            temporal = self.machine.report(t_ms, "quality_hold", "짧은 품질 탈락 · 최대 80ms 판정 유예", {"tip_velocity": None, "angle_velocity": None})
        elif not valid or (gap is not None and gap > self.machine.options.max_gap_ms):
            self.window.clear()
            event, temporal = self.machine.update(t_ms, observation)
            self.hold_since = None; self.cycle_held_frames = 0; self.last_valid_time = None
        else:
            if self.hold_since is not None and t_ms-self.hold_since > 80:
                self.machine.interrupt(t_ms, "quality_hold_timeout")
                self.cycle_held_frames = 0
            elif self.hold_since is not None and self.machine.lift_at is not None and gap is not None:
                self.machine.lift_at += gap
            self.hold_since = None
            self.last_valid_time = t_ms
            # 추적 실패로 ready=False가 된 뒤에는 예전 기준의 복귀 한계로
            # 새 기준의 수집까지 막으면 영구 대기 상태가 된다. 이때는 유효한
            # 안정 구간을 새로 요구하고, 이전 동작을 버린 뒤 기준부터 다시 시작한다.
            needs_reference = self.reference is None or not self.machine.ready
            stable = False
            accepted_reference = False
            # 이전 기준과 양쪽 점수가 모두 가까운 유효 자세는 새로 학습할 필요가 없다.
            # 최소 3프레임·복귀 유지 시간만큼 다시 확인한 뒤 이전 기준을 재사용한다.
            # 중단된 들림은 폐기하며 이 확인 자체에서는 타건을 만들지 않는다.
            same_rest = needs_reference and self.reference is not None and np.all(np.abs(raw-self.reference) <= [self.machine.options.release,self.machine.options.angle_release])
            if same_rest:
                self.known_rest_started_ms = t_ms if self.known_rest_started_ms is None else self.known_rest_started_ms
                self.known_rest_frames += 1
                if self.known_rest_frames >= 3 and t_ms-self.known_rest_started_ms >= self.machine.options.settle_ms:
                    reused = True; accepted_reference = True; self.reference_reuses += 1
                    self.machine.reset(); self.cycle_held_frames = 0; self.window.clear()
            else:
                self.known_rest_started_ms = None; self.known_rest_frames = 0
            # 보정 점수 0=기준, 1=들림이다. 수집을 다시 시작할 때는 적어도
            # 보정 기준 쪽에 가까운 TIP 구간이어야 하며, 들림 유지 중의 정지를
            # 새로운 기준으로 오인하지 않는다. 기존 기준보다 높은 자세의 복구는 허용한다.
            neutral_limit = max(.5, (self.reference[0]+self.machine.options.release) if self.reference is not None else .5)
            eligible = not reused and self.machine.phase == "REST" and (raw[0] <= neutral_limit if needs_reference else raw[0] <= self.reference[0]+self.machine.options.release)
            if eligible:
                self.window.append((t_ms, raw))
                while len(self.window) > 1 and self.window[1][0] <= t_ms-160:
                    self.window.popleft()
                values = np.stack([r[1] for r in self.window])
                center = np.median(values, axis=0)
                mad = 1.4826*np.median(np.abs(values-center), axis=0)
                iqr = (np.percentile(values, 75, axis=0)-np.percentile(values, 25, axis=0))/1.349
                # 두 강건 척도의 큰 값을 사용해 단발 관절 점프는 분리하되
                # 두 자세가 섞이거나 지속 이동하는 구간을 안정 구간으로 받지 않는다.
                variation = np.maximum(mad, iqr)
                stable = len(values) >= 5 and t_ms-self.window[0][0] >= 160 and np.max(variation/[.10, .15]) <= 1 and abs(values[-1,0]-values[0,0]) <= .2
                can_arm = raw[0]-center[0] <= self.machine.options.release and raw[1]-center[1] <= self.machine.options.angle_release
                if stable and can_arm:
                    reacquired = self.reference is not None and needs_reference
                    self.reference_recoveries += int(reacquired)
                    self.reference = np.median(values, axis=0)
                    self.reference_updates += 1
                    self.reference_updated_ms = t_ms
                    self.machine.reset()
                    self.cycle_held_frames = 0
                    self.window.clear()
                    accepted_reference = True
            else:
                self.window.clear()
            adjusted = dict(observation)
            if self.reference is None or (needs_reference and not accepted_reference):
                adjusted["neutral_pending"] = True
            else:
                adjusted["tip"] = {**observation["tip"], "score": float(raw[0]-self.reference[0])}
                adjusted["angles"] = {**observation["angles"], "score": float(raw[1]-self.reference[1])}
            event, temporal = self.machine.update(t_ms, adjusted)
            if reused:
                temporal['decision']['message'] = '이전 기준 자세 재확인 완료 · 새 들림 대기'
                self.known_rest_started_ms = None; self.known_rest_frames = 0
            if event:
                event["quality_hold_frames"] = self.cycle_held_frames
                event["confidence"] *= .8 if self.cycle_held_frames else 1
                self.cycle_held_frames = 0
        temporal["decision"]["reference"] = {"mode": "local_baseline", "experimental": True,
            "tip": None if self.reference is None else float(self.reference[0]),
            "angles": None if self.reference is None else float(self.reference[1]),
            "raw_tip": None if raw is None else float(raw[0]), "raw_angles": None if raw is None else float(raw[1]),
            "updates": self.reference_updates, "held_quality_frames": self.held_frames,
            "recoveries": self.reference_recoveries, "reacquired": reacquired,
            "updated_ms": self.reference_updated_ms,
            "state": "acquiring" if self.reference is None else "recovering" if not self.machine.ready else "active" if self.machine.phase != "REST" else "ready",
            "stable_window_ms": 0 if not self.window else t_ms-self.window[0][0]}
        temporal["decision"]["reference"]["neutral_skipped_frames"] = self.neutral_skipped_frames
        temporal["decision"]["reference"]["reuses"] = self.reference_reuses
        temporal["decision"]["reference"]["reused"] = reused
        if raw is not None and self.reference is not None:
            temporal["relative_scores"] = {"tip": float(raw[0]-self.reference[0]), "angles": float(raw[1]-self.reference[1])}
        return event, temporal
