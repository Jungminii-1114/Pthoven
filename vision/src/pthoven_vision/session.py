"""B-01 orchestrator (S-01 session, S-03 calibration, shared S-04 quality) and B-02 input run (S-05/S-06).

Source timestamps stay in the browser clock. Server monotonic time is used only
for freshness; it is never substituted for an observation timestamp.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import copy
import threading
import time
import uuid

import numpy as np
from pthoven_tap.geometry import extract_features, FINGERS

from .contracts import B01Error, StartRequest, result
from .policy import Policy
from .pose import PoseError, fit_template, assess_pose, validate_rest
from .geometry import build_geometry
from .quality import QualityTimeline
from .b02 import InputRun


def uid(prefix):
    return prefix + "-" + uuid.uuid4().hex


@dataclass
class Capture:
    capture_id: str
    epoch: int
    phase: str
    finger: str
    baseline_ref: str | None
    stage: str = "preparing"
    start_ms: float | None = None
    last_ms: float | None = None
    frames: list = field(default_factory=list)
    attempted: int = 0
    accepted: int = 0
    reason_counts: dict = field(default_factory=dict)
    final_result: dict | None = None

    def progress(self, p):
        elapsed = 0 if self.start_ms is None else max(0, (self.last_ms or self.start_ms) - self.start_ms)
        collecting = max(0, elapsed - p.preparation_ms)
        return {"capture_id": self.capture_id, "phase": self.phase, "finger": self.finger,
                "stage": self.stage, "baseline_ref": self.baseline_ref, "input_count": self.attempted, "accepted_count": self.accepted,
                "elapsed_ms": elapsed, "preparation_ms": p.preparation_ms,
                "min_collect_ms": p.min_collect_ms, "max_collect_ms": p.max_collect_ms,
                "ready_to_finish": self.stage == "collecting" and
                (collecting >= p.max_collect_ms or (collecting >= p.min_collect_ms and self.accepted >= p.min_valid_frames)),
                "reason_counts": dict(self.reason_counts)}


class Session:
    def __init__(self, request, policy, now):
        self.lock = threading.RLock()
        self.inference_lock = threading.Lock()
        self.hold_active = False
        self.recalibration_requested = False
        self.request = request
        self.session_id = uid("session")
        self.policy = policy
        self.epoch = 1
        self.geometry_version = 0
        self.calibration_version = 0
        self.geometry = None
        self.baseline_ref = None
        self.baseline_frames = []
        self.templates = {}
        self.phase = "awaiting_permission"
        self.device = {"permission": "unknown", "stream": "disconnected", "tracker": "not_loaded",
                       "selected_hand": "absent", "calibration": "missing", "input": "blocked"}
        self.active_capture = None
        self.captures = {}
        self.last_frame_id = -1
        self.last_t_ms = None
        self.last_received = None
        self.last_valid_received = None
        self.latest_frame = None
        self.latest_observation = None
        self.latest_quality = None
        self.ready_since = None
        self.ready_count = 0
        self.track_id = None
        self.track_center = None
        self.created = now()
        self.now = now
        self.closed = False
        self.trace = deque(maxlen=600)
        self.timeline = QualityTimeline(policy.max_observation_age_ms)
        self.run = None            # B-02 InputRun while lifecycle_phase == "running"
        self.run_history = deque(maxlen=8)

    def context(self):
        return {"schema_version": "b01.v1", "session_id": self.session_id,
                **self.request.model_dump(), "lifecycle_phase": self.phase, "config_version": 1,
                "command_epoch": self.epoch, "geometry_version": self.geometry_version,
                "calibration_version": self.calibration_version, "device_state": copy.deepcopy(self.device),
                "baseline_ref": self.baseline_ref, "calibrated_fingers": list(self.templates),
                "model_ref": getattr(self, "model_ref", None),
                "input_run": self.run.summary() if self.run else None, "contact_verified": False}

    def check(self, epoch):
        if self.closed:
            raise B01Error("SESSION_CLOSED", "종료된 세션입니다.")
        if epoch != self.epoch:
            raise B01Error("STALE_EPOCH", "오래된 작업입니다. 세션 상태를 다시 확인하세요.")

    def end_run(self, reason):
        if self.run is not None:
            self.run_history.append({**self.run.export(), "ended_reason": reason})
            self.run = None

    def invalidate(self, *, pose=False, geometry=False, reason="invalidated"):
        # B-02: 세대가 바뀌면 진행 중 주기·미게시 proposal을 새 세대에 넘기지 않는다.
        self.end_run(reason)
        self.epoch += 1
        if self.active_capture:
            self.active_capture.stage = "cancelled"
            self.active_capture = None
        self.ready_since, self.ready_count = None, 0
        self.latest_frame = None
        self.last_valid_received = None
        self.device["input"] = "blocked"
        if pose:
            self.templates.clear()
            self.baseline_frames = []
            self.baseline_ref = None
            self.device["calibration"] = "invalidated"
        if geometry:
            self.geometry = None
        self.phase = "previewing"


class B01:
    def __init__(self, policy=None, *, now=time.monotonic):
        self.policy = policy or Policy()
        self.now = now
        self.sessions = {}
        self.lock = threading.Lock()

    def create(self, data):
        request = data if isinstance(data, StartRequest) else StartRequest.model_validate(data)
        with self.lock:
            # Reclaim a closed history entry before consuming an active-session slot.
            if len(self.sessions) >= 32:
                closed = next((k for k,v in self.sessions.items() if v.closed),None)
                if closed is not None:
                    del self.sessions[closed]
            if len(self.sessions) >= 32:
                raise B01Error("SESSION_LIMIT", "세션이 많습니다. 서버를 재시작하거나 세션을 정리하세요.", 503)
            s = Session(request, self.policy, self.now)
            self.sessions[s.session_id] = s
        return result(s.context(), "waiting_for_permission")

    def get(self, sid):
        with self.lock:
            s = self.sessions.get(sid)
        if s is None:
            raise B01Error("SESSION_NOT_FOUND", "세션을 찾을 수 없습니다.", 404)
        return s

    def snapshot(self, sid):
        s = self.get(sid)
        with s.lock:
            self.watchdog(s)
            return result({**s.context(), "quality": copy.deepcopy(s.latest_quality)})

    def device(self, sid, data, tracker):
        s = self.get(sid)
        with s.lock:
            s.check(data.command_epoch)
            old_size = (s.device.get("width"), s.device.get("height"))
            known_size = (s.geometry['source_size']['width'],s.geometry['source_size']['height']) if s.geometry else old_size
            changed = bool(data.width and data.height and known_size[0] and known_size != (data.width,data.height))
            lost = data.stream in {"ended", "disconnected"} or data.permission == "denied"
            if changed or lost:
                s.invalidate(pose=True, geometry=True)
            if data.stream == "stalled" and not s.hold_active:
                s.invalidate()
                s.hold_active = True
                s.phase = "hold"
            s.device.update(data.model_dump(exclude={"command_epoch"}))
            s.device["tracker"] = tracker["state"]
            s.model_ref = tracker.get("model_ref")
            if lost or data.stream == "stalled":
                s.phase = "hold"
            elif s.phase == "awaiting_permission" and data.permission == "granted":
                s.phase = "previewing"
            return result(s.context(), "ready_for_observation" if tracker["state"] == "ready" and not lost else "waiting_for_permission")

    def set_geometry(self, sid, data):
        s = self.get(sid)
        with s.lock:
            s.check(data.command_epoch)
            if data.expected_geometry_version != s.geometry_version:
                raise B01Error("STALE_GEOMETRY", "좌표 버전이 변경되었습니다.")
            # Pure validation before any mutation; failed geometry leaves state intact.
            geometry = build_geometry(data.geometry)
            size = data.geometry["source_size"]
            changed_source = s.geometry and s.geometry.get("source_size") != size
            s.invalidate(pose=bool(changed_source))
            s.geometry_version += 1
            geometry["geometry_version"] = s.geometry_version
            s.geometry = geometry
            return result(s.context(), "geometry_updated")

    def start_capture(self, sid, data):
        s = self.get(sid)
        with s.lock:
            s.check(data.command_epoch)
            self.watchdog(s)
            if s.geometry is None or s.device["stream"] != "receiving" or s.last_received is None:
                raise B01Error("OBSERVATION_NOT_READY", "좌표 설정과 새 영상 수신부터 확인하세요.")
            if data.finger not in s.request.active_fingers:
                raise B01Error("INACTIVE_FINGER", "선택하지 않은 손가락입니다.", 422)
            if s.phase == "running":
                raise B01Error("INPUT_RUNNING", "입력 점검을 먼저 종료하세요.")
            if data.phase == "lift" and s.baseline_ref is None:
                raise B01Error("BASELINE_MISSING", "기준 자세부터 저장하세요.")
            if s.active_capture:
                raise B01Error("CAPTURE_IN_PROGRESS", "진행 중 수집을 마치거나 취소하세요.")
            if len(s.captures) >= 64:
                raise B01Error("CAPTURE_LIMIT", "수집 요청 한도입니다. 새 세션을 시작하세요.")
            cap = Capture(uid("capture"), s.epoch, data.phase, data.finger, s.baseline_ref)
            s.captures[cap.capture_id] = cap
            s.active_capture = cap
            s.phase = "calibrating"
            s.device.update(calibration="collecting", input="blocked")
            return result({**cap.progress(s.policy), "session": s.context()}, "capture_started")

    def cancel_capture(self, sid, cid, epoch):
        s = self.get(sid)
        with s.lock:
            s.check(epoch)
            cap = s.captures.get(cid)
            if cap is None:
                raise B01Error("CAPTURE_NOT_FOUND", "수집 요청을 찾을 수 없습니다.", 404)
            if cap.stage in {"preparing", "collecting", "validating"}:
                cap.stage = "cancelled"
                if s.active_capture is cap:
                    s.active_capture = None
                s.epoch += 1
                s.ready_count, s.ready_since = 0, None
                s.phase = "previewing"
                s.device["calibration"] = "valid" if s.templates else "missing"
            return result({"session": s.context(), "capture": cap.progress(s.policy)}, cap.stage)

    def finish_capture(self, sid, cid, epoch):
        s = self.get(sid)
        with s.lock:
            cap = s.captures.get(cid)
            if cap and cap.final_result and not s.closed and s.epoch in {cap.epoch, cap.epoch+1} and epoch in {cap.epoch, s.epoch}:
                return copy.deepcopy(cap.final_result)
            s.check(epoch)
            if cap is None or cap is not s.active_capture:
                raise B01Error("CAPTURE_NOT_ACTIVE", "활성 수집 요청이 아닙니다.")
            if not cap.progress(s.policy)["ready_to_finish"]:
                return result({"session": s.context(), "capture": cap.progress(s.policy)}, "needs_more_samples")
            cap.stage = "validating"
            rest, frames, base, capture_epoch = copy.deepcopy(s.baseline_frames), copy.deepcopy(cap.frames), s.baseline_ref, s.epoch
        try:
            if cap.phase == "rest":
                fitted = validate_rest(frames, s.request.active_fingers, s.policy.as_dict())
            else:
                fitted = fit_template(rest, frames, cap.finger, s.policy.as_dict())
            business_error = None
        except PoseError as exc:
            fitted, business_error = None, exc
        with s.lock:
            s.check(capture_epoch)
            if s.active_capture is not cap or cap.stage != "validating" or base != s.baseline_ref:
                raise B01Error("STALE_CALIBRATION_REQUEST", "취소되거나 기준이 변경된 보정 결과입니다.")
            s.active_capture = None
            if business_error:
                cap.stage = "failed"
                s.phase = "previewing"
                s.device["calibration"] = "valid" if s.templates else "failed"
                response = result({"session": s.context(), "capture": cap.progress(s.policy)}, business_error.code,
                                  diagnostics={"fit_finger_templates": {"status": "success", "reason": str(business_error),
                                               "error_code": business_error.code, "details": business_error.diagnostics}})
            else:
                # New baseline invalidates all dependent lifts. A lift replaces only its finger.
                if cap.phase == "rest":
                    s.baseline_frames = frames
                    s.recalibration_requested = False
                    s.baseline_ref = uid("baseline")
                    s.templates = {}
                else:
                    fitted["baseline_ref"] = s.baseline_ref
                    fitted["feature_space"] = "source_image_and_palm_world"
                    fitted["model_ref"] = s.model_ref
                    fitted["policy_version"] = "b01-policy.v1"
                    s.templates[cap.finger] = fitted
                s.calibration_version += 1
                s.epoch += 1
                cap.stage = "passed"
                s.ready_count, s.ready_since = 0, None
                s.phase = "previewing"
                s.device.update(calibration="valid" if s.templates else "missing", input="acquiring_rest")
                response = result({"session": s.context(), "capture": cap.progress(s.policy), "calibration": self.bundle(s),
                                   "fit_diagnostics": fitted.get("diagnostics", fitted)},
                                  "rest_saved" if cap.phase == "rest" else "calibrated")
            cap.final_result = copy.deepcopy(response)
            return response

    def bundle(self, s):
        return {"schema_version": "calibration-bundle.v1", "calibration_version": s.calibration_version,
                "geometry_version": s.geometry_version, "model_ref": getattr(s, "model_ref", None),
                "policy_version": "b01-policy.v1", "policy": s.policy.as_dict(), "feature_schema_version": "palm-tip-angles.v1",
                "baseline_ref": s.baseline_ref, "selected_hand": s.request.selected_hand,
                "per_finger_templates": copy.deepcopy(s.templates), "transform_ref": f"geometry-{s.geometry_version}",
                "key_regions": copy.deepcopy((s.geometry or {}).get("key_regions", [])),
                "validity_conditions": {"device_id": s.request.selected_device_id, "source_size": (s.geometry or {}).get("source_size"),
                                        "feature_space": "source_image_and_palm_world", "contact_verified": False}}

    def process_observation(self, sid, frame, hands, *, tracker_state="ready", model_ref="test-fixture", inference_error=None):
        """Adapter boundary, also used by explicitly labelled offline replay tests."""
        s = self.get(sid)
        with s.lock:
            s.check(frame.command_epoch)
            if frame.clock_id != s.request.clock_id:
                raise B01Error("INVALID_CLOCK", "브라우저 시계가 일치하지 않습니다.")
            if frame.geometry_version != s.geometry_version or frame.calibration_version != s.calibration_version:
                raise B01Error("STALE_VERSION", "기하 또는 보정 버전이 변경되었습니다.")
            if frame.frame_id <= s.last_frame_id or (s.last_t_ms is not None and frame.t_ms <= s.last_t_ms):
                raise B01Error("NON_MONOTONIC_FRAME", "중복·역순 프레임입니다.")
            if s.device["permission"] != "granted" or s.device["stream"] in {"ended", "disconnected"}:
                raise B01Error("DEVICE_NOT_READY", "카메라 권한과 연결 상태를 확인하세요.")
            gap = s.last_t_ms is not None and frame.t_ms - s.last_t_ms > s.policy.max_observation_age_ms
            if gap:
                s.track_id, s.track_center = None, None
                s.ready_count, s.ready_since = 0, None
            s.last_frame_id, s.last_t_ms, s.last_received = frame.frame_id, frame.t_ms, s.now()
            s.device["stream"], s.device["tracker"] = "receiving", tracker_state
            s.hold_active = False
            s.model_ref = model_ref
            selected = [h for h in hands if h.get("handedness") == s.request.selected_hand]
            reasons = []
            raw = None
            if inference_error:
                reasons = ["INFERENCE_FAILED"]
            elif len(selected) > 1:
                reasons = ["HAND_IDENTITY_AMBIGUOUS"]
            elif not selected:
                reasons = ["HAND_NOT_OBSERVED"]
            else:
                h = selected[0]
                try:
                    image = np.asarray(h["image_xy"], dtype=float)
                    world = np.asarray(h["world_landmarks"], dtype=float)
                    if image.shape != (21, 2) or world.shape != (21, 3) or not np.isfinite(image).all() or not np.isfinite(world).all():
                        raise ValueError("invalid landmarks")
                    center = image[[5, 9, 17]].mean(axis=0)
                    if s.track_center is not None and np.linalg.norm(center-s.track_center) > .25:
                        reasons = ["HAND_IDENTITY_AMBIGUOUS"]
                    else:
                        if s.track_id is None:
                            s.track_id = uid("hand")
                        s.track_center = center
                        raw = {"world_landmarks": world.tolist(), "landmarks": image.tolist()}
                except (ValueError, TypeError, KeyError):
                    reasons = ["INVALID_LANDMARKS"]
            if reasons:
                s.track_id, s.track_center = None, None
            per_finger = {}
            structural = {}
            if raw:
                for finger in s.request.active_fingers:
                    try:
                        features = extract_features(raw["world_landmarks"], finger, raw["landmarks"])
                        valid = features["image_visibility"]["valid"]
                        structural[finger] = valid
                        if finger in s.templates:
                            per_finger[finger] = assess_pose(raw, s.templates[finger], s.policy.as_dict())
                        else:
                            per_finger[finger] = {"valid": False, "reason_codes": ["CALIBRATION_MISSING"]}
                    except (ValueError, TypeError, PoseError):
                        structural[finger] = False
                        per_finger[finger] = {"valid": False, "reason_codes": ["INVALID_LANDMARKS"]}
                if any(structural.values()):
                    s.last_valid_received = s.now()
            all_valid = bool(raw) and all(v.get("valid", False) for v in per_finger.values()) and bool(per_finger)
            neutral = all_valid and all(abs(v.get("tip", {}).get("score", 99)) <= s.policy.release and
                                       abs(v.get("angles", {}).get("score", 99)) <= s.policy.release for v in per_finger.values())
            running = s.phase == "running" and s.run is not None
            if running:
                pass  # B-02: 손가락별 품질은 판정기가 주기 단위로 처리한다. 전역 hold는 watchdog·장치 변경이 담당한다.
            elif neutral and not gap:
                s.ready_since = frame.t_ms if s.ready_since is None else s.ready_since
                s.ready_count += 1
            else:
                s.ready_since, s.ready_count = None, 0
                s.device["input"] = "blocked"
                if s.phase == "ready" and not all_valid:
                    s.phase = "hold"
                    s.hold_active = True
                    s.epoch += 1
            s.device["selected_hand"] = "observed" if raw else "ambiguous" if "HAND_IDENTITY_AMBIGUOUS" in reasons else "absent"
            observation = {"schema_version": "observation.v1", "observation_id": uid("observation"), "frame_id": frame.frame_id,
                           "t_ms": frame.t_ms, "clock_id": frame.clock_id, "model_ref": model_ref,
                           "geometry_version": frame.geometry_version, "command_epoch": frame.command_epoch,
                           "hands": [{"track_id": s.track_id, "handedness": s.request.selected_hand,
                                      "image_xy": raw["landmarks"], "world_landmarks": raw["world_landmarks"]}] if raw else [],
                           "hand_association": "observed" if raw else "ambiguous" if "HAND_IDENTITY_AMBIGUOUS" in reasons else "absent"}
            quality = {"quality_id": uid("quality"), "clock_id": frame.clock_id, "t_ms": frame.t_ms,
                       "quality_state": "usable_provisional" if all_valid else "hold_required",
                       "reason_codes": reasons or ([] if all_valid else ["CALIBRATION_OR_POSE_NOT_READY"]),
                       "per_finger": per_finger, "policy_version": "b01-policy.v1", "contact_verified": False}
            s.timeline.observe(frame.t_ms, all_valid, quality["reason_codes"])
            quality.update(s.timeline.snapshot())
            cap = s.active_capture
            if cap and cap.stage in {"preparing", "collecting"}:
                if frame.capture_id != cap.capture_id:
                    pass  # A queued frame from before the button click is not a calibration sample.
                elif gap and cap.start_ms is not None:
                    cap.stage = "failed"
                    cap.reason_counts["TRACKING_GAP"] = cap.reason_counts.get("TRACKING_GAP", 0)+1
                    s.active_capture = None
                    s.phase = "previewing"
                else:
                    cap.start_ms = frame.t_ms if cap.start_ms is None else cap.start_ms
                    cap.last_ms = frame.t_ms
                    elapsed = frame.t_ms-cap.start_ms
                    if s.policy.preparation_ms <= elapsed <= s.policy.preparation_ms+s.policy.max_collect_ms:
                        cap.stage = "collecting"
                        if len(cap.frames) < s.policy.max_capture_frames:
                            cap.attempted += 1
                            good = bool(raw) and (all(structural.get(f, False) for f in s.request.active_fingers) if cap.phase == "rest" else structural.get(cap.finger, False))
                            cap.frames.append({**(copy.deepcopy(raw) if good else {"world_landmarks": None, "landmarks": None}),
                                "t_ms": frame.t_ms, "frame_id": frame.frame_id, "clock_id": frame.clock_id,
                                "command_epoch": frame.command_epoch, "geometry_version": frame.geometry_version,
                                "calibration_version": frame.calibration_version,
                                "track_id": s.track_id, "reason_codes": reasons if not good else []})
                            cap.accepted += int(good)
                            if not good:
                                reason = reasons[0] if reasons else "TARGET_OUT_OF_FRAME"
                                cap.reason_counts[reason] = cap.reason_counts.get(reason, 0)+1
                    elif elapsed > s.policy.preparation_ms+s.policy.max_collect_ms:
                        cap.stage = "collecting"  # Finish with finite collected samples, never silently extend.
            proposals = []
            if running:
                proposals = s.run.observe(frame_id=frame.frame_id, t_ms=frame.t_ms, track_id=s.track_id if raw else None,
                                          raw=raw, geometry=s.geometry, observation_ref=observation["observation_id"])
            s.latest_frame, s.latest_observation, s.latest_quality = raw, observation, quality
            s.trace.append({"observation": observation, "quality": quality})
            payload = {"session": s.context(), "observation": observation, "quality": quality,
                       "capture": cap.progress(s.policy) if cap else None,
                       "input": {"proposals": proposals, "summary": s.run.summary()} if running else None}
            if inference_error:
                return result(None, "inference_failed", status="error", diagnostics={"run_hand_landmarker": {
                    "status": "failed", "error_code": "INFERENCE_FAILED", "reason": inference_error, "retryable": False}})
            return result(payload, "observed" if raw else "no_hand")

    def watchdog(self, s):
        if s.closed or s.last_received is None:
            return
        if (s.now()-s.last_received)*1000 > s.policy.max_observation_age_ms:
            s.device["stream"] = "stalled"
            if not s.hold_active:
                s.invalidate(reason="FRAME_STALLED")
                s.hold_active = True
                s.phase = "hold"
            s.latest_quality = {"quality_state": "hold_required", "reason_codes": ["FRAME_STALLED"],
                                "clock_id": s.request.clock_id, "contact_verified": False, **s.timeline.snapshot(stalled=True)}

    def request_recalibration(self, sid, epoch):
        s = self.get(sid)
        with s.lock:
            s.check(epoch)
            s.invalidate()
            s.recalibration_requested = True
            s.phase = "calibrating"
            return result({"session": s.context()}, "recalibration_requested")

    def ready(self, sid, epoch):
        s = self.get(sid)
        with s.lock:
            s.check(epoch)
            self.watchdog(s)
            if s.phase == "running":
                return result({"session": s.context()}, "input_running")
            missing = sorted(set(s.request.active_fingers)-set(s.templates))
            stable = s.ready_since is not None and s.last_t_ms-s.ready_since >= s.policy.ready_stable_ms and s.ready_count >= s.policy.ready_observations
            if s.recalibration_requested or missing or s.geometry is None or s.device["stream"] != "receiving" or s.device["tracker"] != "ready" or not stable or s.active_capture:
                return result({"session": s.context(), "missing_fingers": missing}, "hold_required")
            s.phase, s.device["input"] = "ready", "ready"
            s.hold_active = False
            return result({"session": s.context(), "calibration": self.bundle(s), "quality": copy.deepcopy(s.latest_quality)}, "ready_for_observation")

    # ---- B-02 ----------------------------------------------------------
    def start_input(self, sid, epoch):
        """B01-10: 입력 점검을 통과한(ready) 활성 부분집합으로 B-02를 시작한다."""
        s = self.get(sid)
        with s.lock:
            s.check(epoch)
            self.watchdog(s)
            if s.phase != "ready" or s.device["input"] != "ready" or s.geometry is None:
                return result({"session": s.context()}, "hold_required",
                              diagnostics={"start_input": {"status": "success", "reason": "입력 준비 확인을 먼저 통과해야 합니다.",
                                                           "retryable": False}})
            s.run = InputRun(session_id=s.session_id, clock_id=s.request.clock_id, templates=s.templates,
                             epoch=s.epoch, calibration_version=s.calibration_version,
                             geometry_version=s.geometry_version, config_version=1, model_ref=getattr(s, "model_ref", None),
                             started_t_ms=s.last_t_ms, geometry=s.geometry)
            s.phase, s.device["input"] = "running", "running"
            return result({"session": s.context()}, "input_running")

    def stop_input(self, sid, epoch):
        """입력 실행 종료: 진행 주기를 폐기하고 새 입력을 차단한다. 재시작은 준비 확인부터."""
        s = self.get(sid)
        with s.lock:
            s.check(epoch)
            if s.phase != "running":
                return result({"session": s.context()}, "not_running")
            s.invalidate(reason="user_stop")
            return result({"session": s.context(), "last_run": copy.deepcopy(s.run_history[-1]["summary"])}, "input_stopped")

    def acknowledge(self, sid, data):
        """CONTRACTS 6.2: decision_ref + event별 브라우저 emitted_ms → 최종 사건. 재전송은 같은 결과."""
        s = self.get(sid)
        with s.lock:
            s.check(data.command_epoch)
            if s.run is None or s.phase != "running":
                raise B01Error("INPUT_NOT_RUNNING", "입력 실행 중이 아닙니다.")
            try:
                receipts = s.run.acknowledge(data.decision_ref, [e.model_dump() for e in data.events], data.clock_id)
            except KeyError as exc:
                raise B01Error(str(exc.args[0]), "게시할 결정을 찾을 수 없습니다.", 404)
            except ValueError as exc:
                raise B01Error(str(exc), "출력 시각이 계약을 만족하지 않습니다.", 422)
            return result({"events": receipts, "summary": s.run.summary()}, "published")

    def close(self, sid, epoch):
        s = self.get(sid)
        with s.lock:
            if not s.closed:
                s.check(epoch)
                s.invalidate()
                s.closed = True
                s.phase = "closed"
                s.device.update(stream="ended", input="blocked")
            return result(s.context(), "closed")

    def export(self, sid, epoch):
        s = self.get(sid)
        with s.lock:
            s.check(epoch)
            return result({"schema_version": "b01-export.v1", "source": "real_capture", "session": s.context(),
                           "calibration": self.bundle(s), "geometry": copy.deepcopy(s.geometry),
                           "rest_frames": copy.deepcopy(s.baseline_frames), "recent_observations": list(s.trace),
                           "captures": [{**c.progress(s.policy), "frames": copy.deepcopy(c.frames),
                               "published_baseline_ref": c.final_result["data"]["session"]["baseline_ref"] if c.stage == "passed" and c.final_result else None}
                               for c in s.captures.values()],
                           "input_runs": [*copy.deepcopy(list(s.run_history)), *([{**s.run.export(), "ended_reason": None}] if s.run else [])],
                           "contact_verified": False, "contains_video": False, "trace_limit": 600}, "export_available")
