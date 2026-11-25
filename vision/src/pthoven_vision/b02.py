"""B-02 · 실시간 관측과 가상 연주 입력 (S-05 detect_finger_cycle, S-06 map_virtual_note).

- S-05는 B-01이 발급한 손가락 템플릿으로 ``pthoven_tap.PoseTapDetector``(상대 기준 모드)를
  실행한다. 판정 수학을 이 패키지에 다시 구현하지 않는다(PRD 8.1: 판정 수학 중복 금지).
- S-06은 후보의 복귀 시각과 **같은 frame_id**에 저장된 손끝 좌표만 사용해 가상 건반에
  매핑한다. 출력 시점 좌표·다른 손가락·기대 음으로 대체하지 않는다(TECHNICAL_SPEC 4.3, 10.2).
- Python은 ``emitted_ms``를 만들지 않는다. 결과는 proposal이며, 브라우저가 첫 표시 시각을
  ``/events/ack``로 보내야 최종 ``virtual-input.v1`` 사건이 된다(CONTRACTS 6.2).
- 모든 결과는 ``kind=pose_tap_candidate``, ``contact_verified=false``다.
"""
from __future__ import annotations

from collections import OrderedDict, deque
import copy
import math

from pthoven_tap import PoseTapDetector, ENGINE_REVISION as TAP_ENGINE_REVISION
from pthoven_tap.geometry import FINGERS

from .geometry import source_to_display

DETECTOR_MODE = "local_baseline"
TIP_ID = {finger: ids[-1] for finger, ids in FINGERS.items()}
B02_POLICY_VERSION = "b02-policy.v1"


class CoordinateBuffer:
    """후보 좌표 버퍼: 최근 5초·최대 600 관측 중 먼저 도달한 상한(TECHNICAL_SPEC 10.2)."""

    def __init__(self, max_age_ms=5000, capacity=600):
        self.max_age_ms, self.capacity = max_age_ms, capacity
        self.rows = OrderedDict()  # t_ms -> row

    def add(self, row):
        self.rows[row["t_ms"]] = row
        while len(self.rows) > self.capacity:
            self.rows.popitem(last=False)
        while self.rows and next(iter(self.rows)) < row["t_ms"] - self.max_age_ms:
            self.rows.popitem(last=False)

    def at(self, t_ms):
        return self.rows.get(t_ms)


def _key_for_point(geometry, xy_norm):
    """정규화 원본 좌표 → (status, key, key_space_xy, reasons). 경계·영역 밖은 보류."""
    x, y = source_to_display(geometry, xy_norm)
    rect = geometry["keyboard_rect"]
    kx, ky = x - rect["left"], y - rect["top"]
    key_space = [kx, ky]
    keys = [k for k in geometry["key_regions"] if k["enabled"]]
    margin = keys[0]["boundary_margin_css"] if keys else 0.
    if not (0 <= kx <= rect["width"] and 0 <= ky <= rect["height"]):
        return "withheld", None, key_space, ["OUTSIDE_KEYBOARD"]
    # 건반 바깥 테두리와 키 사이 경계에 가까우면 어느 쪽으로도 정하지 않는다.
    if min(ky, rect["height"] - ky) < margin:
        return "withheld", None, key_space, ["KEY_BOUNDARY"]
    for key in keys:
        left, right = key["polygon_css"][0][0], key["polygon_css"][1][0]
        if left <= x < right or (key is keys[-1] and x == right):
            if min(x - left, right - x) < margin:
                return "withheld", None, key_space, ["KEY_BOUNDARY"]
            return "accepted_provisional", key, key_space, []
    return "withheld", None, key_space, ["OUTSIDE_KEYBOARD"]


def map_virtual_note(candidate, buffer, geometry):
    """S-06: 후보 frame의 손끝 좌표를 가상 흰 건반에 매핑해 VirtualInputProposal을 만든다."""
    base = {"schema_version": "virtual-input-proposal.v1", "event_id": "evt-" + candidate["candidate_id"][4:],
            "candidate_id": candidate["candidate_id"], "session_id": candidate["session_id"],
            "clock_id": candidate["clock_id"], "t_ms": candidate["t_ms"], "emitted_ms": None,
            "candidate_frame_id": candidate["candidate_frame_id"], "source": "camera_pose",
            "track_id": candidate["track_id"], "finger": candidate["finger"],
            "kind": "pose_tap_candidate", "contact_verified": False,
            "calibration_version": candidate["calibration_version"], "geometry_version": candidate["geometry_version"],
            "config_version": candidate["config_version"], "command_epoch": candidate["command_epoch"],
            "model_ref": candidate["model_ref"], "policy_version": B02_POLICY_VERSION,
            "image_xy_normalized": None, "key_space_xy": None, "virtual_key": None, "midi_note": None,
            "input_status": "withheld", "quality_flags": [],
            "evidence_refs": [candidate["candidate_id"], candidate.get("observation_ref"), f"geometry-{candidate['geometry_version']}"]}
    row = buffer.at(candidate["t_ms"])
    if row is None or row["frame_id"] != candidate["candidate_frame_id"] or candidate["finger"] not in row["tips"]:
        base["quality_flags"] = ["COORDINATE_UNAVAILABLE"]
        return base
    if geometry is None or row["geometry_version"] != geometry["geometry_version"] or candidate["geometry_version"] != geometry["geometry_version"]:
        base["quality_flags"] = ["STALE_GEOMETRY"]
        return base
    xy = row["tips"][candidate["finger"]]
    base["image_xy_normalized"] = list(xy)
    status, key, key_space, reasons = _key_for_point(geometry, xy)
    base["key_space_xy"] = key_space
    base["input_status"], base["quality_flags"] = status, reasons
    if key is not None:
        base["virtual_key"], base["midi_note"] = key["key_id"], key["midi_note"]
    return base


class InputRun:
    """세션의 한 입력 실행(start~stop). epoch·버전이 바뀌면 새 실행을 만들어야 한다."""

    def __init__(self, *, session_id, clock_id, templates, epoch, calibration_version, geometry_version,
                 config_version, model_ref, started_t_ms=None, geometry=None):
        self.session_id, self.clock_id = session_id, clock_id
        self.templates = copy.deepcopy(templates)
        self.epoch, self.calibration_version, self.geometry_version = epoch, calibration_version, geometry_version
        self.config_version, self.model_ref = config_version, model_ref
        self.started_t_ms = started_t_ms
        self.geometry = copy.deepcopy(geometry)  # 시작 시점 기하 스냅샷(기록·재생용)
        self.buffer = CoordinateBuffer()
        self.track_id = None
        self.detector = None
        self.sequence = {f: 0 for f in self.templates}
        self.candidates = deque(maxlen=2000)
        self.proposals = OrderedDict()      # event_id -> proposal (immutable after creation)
        self.decisions = OrderedDict()      # decision_ref -> [event_id]
        self.final_events = OrderedDict()   # event_id -> final event
        self.decision_counter = 0
        self.last_states = {}
        self.counts = {"candidates": 0, "accepted_provisional": 0, "withheld": 0, "detector_resets": 0}
        # landmark replay용 원본 관측(누락 포함). 영상 바이트는 보관하지 않는다.
        self.observations = deque(maxlen=3600)

    def _new_detector(self):
        self.detector = PoseTapDetector(copy.deepcopy(self.templates), mode=DETECTOR_MODE)
        self.counts["detector_resets"] += 1

    def observe(self, *, frame_id, t_ms, track_id, raw, geometry, observation_ref):
        """S-05+S-06: 관측 한 개를 처리하고 이번 프레임의 proposal 목록을 돌려준다.

        raw가 None(손 없음·모호·추론 실패)이면 판정기에 무효 관측을 넣어 진행 주기를 폐기한다.
        추적 ID가 바뀌면 판정기를 새로 만들어 두 반쪽 동작을 한 타건으로 합치지 않는다(INV-08).
        """
        self.observations.append({"frame_id": frame_id, "t_ms": t_ms, "track_id": track_id if raw else None,
                                  "world_landmarks": raw["world_landmarks"] if raw else None,
                                  "landmarks": raw["landmarks"] if raw else None})
        if raw is not None and track_id != self.track_id:
            self.track_id = track_id
            self._new_detector()
        if self.detector is None:
            self.last_states = {f: {"phase": "WAIT_REST", "code": "hand_not_tracked"} for f in self.templates}
            return []
        if raw is not None:
            tips = {}
            for finger in self.templates:
                xy = raw["landmarks"][TIP_ID[finger]]
                if all(isinstance(v, (int, float)) and math.isfinite(v) for v in xy[:2]):
                    tips[finger] = [float(xy[0]), float(xy[1])]
            self.buffer.add({"t_ms": t_ms, "frame_id": frame_id, "tips": tips, "track_id": track_id,
                             "geometry_version": self.geometry_version, "calibration_version": self.calibration_version,
                             "command_epoch": self.epoch})
            output = self.detector.update(t_ms, raw["world_landmarks"], raw["landmarks"])
        else:
            output = self.detector.update(t_ms, None, None)
        self.last_states = {f: {"phase": s.get("phase"), "code": (s.get("decision") or {}).get("code"),
                                "blockers": (s.get("decision") or {}).get("blockers", []),
                                "tip_score": (s.get("relative_scores") or {}).get("tip"),
                                "angle_score": (s.get("relative_scores") or {}).get("angles")}
                            for f, s in output["signals"].items()}
        proposals = []
        for event in output["events"]:
            finger = event["finger"]
            self.sequence[finger] += 1
            row = self.buffer.at(event["t_ms"])
            candidate = {"schema_version": "tap-candidate.v1",
                         "candidate_id": f"tap-{self.session_id}-e{self.epoch}-{self.track_id}-{finger}-{self.sequence[finger]}",
                         "session_id": self.session_id, "clock_id": self.clock_id, "track_id": self.track_id,
                         "finger": finger, "cycle_sequence": self.sequence[finger], "t_ms": event["t_ms"],
                         "candidate_frame_id": row["frame_id"] if row else None, "decision_frame_id": frame_id,
                         "decision_t_ms": t_ms, "emitted_ms": None,
                         "calibration_version": self.calibration_version, "geometry_version": self.geometry_version,
                         "config_version": self.config_version, "command_epoch": self.epoch,
                         "model_ref": self.model_ref, "policy_version": B02_POLICY_VERSION,
                         "detector": {"engine_revision": TAP_ENGINE_REVISION, "mode": DETECTOR_MODE},
                         "motion": {k: event.get(k) for k in ("tip_excursion", "tip_path_normalized", "tip_return_speed",
                                                              "angle_peak", "decision_latency_ms")},
                         "observation_ref": observation_ref, "withheld_reasons": [],
                         "kind": "pose_tap_candidate", "contact_verified": False}
            self.candidates.append(candidate)
            self.counts["candidates"] += 1
            proposal = map_virtual_note(candidate, self.buffer, geometry)
            if proposal["event_id"] in self.proposals:
                continue  # 같은 후보 ID는 한 번만 발행한다(INV-05, B02-08).
            self.proposals[proposal["event_id"]] = proposal
            self.counts[proposal["input_status"]] += 1
            proposals.append(copy.deepcopy(proposal))
        if proposals:
            self.decision_counter += 1
            ref = f"decision-{self.session_id}-e{self.epoch}-{self.decision_counter}"
            self.decisions[ref] = [p["event_id"] for p in proposals]
            for p in proposals:
                p["decision_ref"] = ref
                self.proposals[p["event_id"]]["decision_ref"] = ref
        return proposals

    def acknowledge(self, decision_ref, events, clock_id):
        """브라우저 출력 시각을 결합해 최종 사건을 만든다. 같은 사건은 최초 emitted_ms를 유지한다."""
        if clock_id != self.clock_id:
            raise ValueError("CLOCK_MISMATCH")
        allowed = self.decisions.get(decision_ref)
        if allowed is None:
            raise KeyError("DECISION_NOT_FOUND")
        receipts = []
        for item in events:
            event_id, emitted = item["event_id"], item["emitted_ms"]
            if event_id not in allowed:
                raise KeyError("EVENT_NOT_IN_DECISION")
            if event_id in self.final_events:
                receipts.append(copy.deepcopy(self.final_events[event_id]))
                continue
            proposal = self.proposals[event_id]
            if not (isinstance(emitted, (int, float)) and not isinstance(emitted, bool) and math.isfinite(emitted)) or emitted < proposal["t_ms"]:
                raise ValueError("EMITTED_BEFORE_CANDIDATE")
            final = {**copy.deepcopy(proposal), "schema_version": "virtual-input.v1", "emitted_ms": float(emitted)}
            final.pop("decision_ref", None)
            self.final_events[event_id] = final
            receipts.append(copy.deepcopy(final))
        return receipts

    def summary(self):
        pending = [eid for eid in self.proposals if eid not in self.final_events]
        return {"detector": {"engine_revision": TAP_ENGINE_REVISION, "mode": DETECTOR_MODE},
                "counts": dict(self.counts), "finger_states": copy.deepcopy(self.last_states),
                "pending_publication": len(pending), "published": len(self.final_events),
                "versions": {"command_epoch": self.epoch, "calibration_version": self.calibration_version,
                             "geometry_version": self.geometry_version, "config_version": self.config_version},
                "contact_verified": False}

    def export(self):
        return {"schema_version": "b02-input-run.v1",
                "start": {"templates": copy.deepcopy(self.templates), "session_id": self.session_id, "clock_id": self.clock_id,
                          "command_epoch": self.epoch, "calibration_version": self.calibration_version,
                          "geometry_version": self.geometry_version, "config_version": self.config_version,
                          "model_ref": self.model_ref, "started_t_ms": self.started_t_ms,
                          "geometry": copy.deepcopy(self.geometry)},
                "observations": list(copy.deepcopy(self.observations)),
                "observations_limit": self.observations.maxlen,
                "candidates": list(copy.deepcopy(self.candidates)),
                "proposals": list(copy.deepcopy(self.proposals).values()),
                "virtual_inputs": list(copy.deepcopy(self.final_events).values()),
                "summary": self.summary()}
