"""HTTP와 독립된 로컬 서비스. 보정·재분석·상태 머신은 모두 Python에서 실행."""
from __future__ import annotations

import copy
import math
import threading
import time
import uuid

from . import ALGORITHM, ENGINE_REVISION, CalibrationError, calibrate, diagnostic_text, PoseTapDetector, FINGERS, FEATURE_NAMES
from .calibration import summarize


class TapService:
    def __init__(self, max_sessions=64):
        self.sessions = {}; self.lock = threading.Lock(); self.max_sessions = max_sessions

    def status(self):
        return {"engine": "python", "algorithm": ALGORITHM, "engine_revision": ENGINE_REVISION, "modes": ["fixed", "local_baseline"], "fingers": FINGERS, "feature_names": FEATURE_NAMES, "contact_verified": False}

    def reference(self, payload):
        try:
            summary = summarize(payload.get("frames"), payload.get("finger"))
            return {"ok": True, "diagnostics": {"algorithm": ALGORITHM, "rest": summary["stats"]},
                    "diagnostic_text": diagnostic_text({"rest": summary["stats"]})}
        except CalibrationError as error:
            return {"ok": False, "code": error.code, "error": str(error), "diagnostics": error.diagnostics,
                    "diagnostic_text": str(error)}

    def calibrate(self, payload):
        try:
            model = calibrate(payload.get("rest_frames"), payload.get("lift_frames"), payload.get("finger"))
            return {"ok": True, "model": model, "diagnostics": model["diagnostics"], "diagnostic_text": diagnostic_text(model["diagnostics"])}
        except CalibrationError as error:
            return {"ok": False, "code": error.code, "error": str(error), "diagnostics": error.diagnostics,
                    "diagnostic_text": diagnostic_text(error.diagnostics)}

    def create_session(self, payload):
        calibration = payload.get("calibration", {})
        if not isinstance(calibration, dict) or len(calibration) > 5:
            raise ValueError("손가락별 Python 보정 모델이 필요합니다.")
        detector = PoseTapDetector(copy.deepcopy(calibration), mode=payload.get("mode", "fixed"))
        now = time.monotonic(); key = uuid.uuid4().hex
        with self.lock:
            expired = [k for k, s in self.sessions.items() if now - s["used_at"] > 1800]
            for k in expired:
                del self.sessions[k]
            if len(self.sessions) >= self.max_sessions:
                raise RuntimeError("사용 중인 실험 세션이 많습니다. 잠시 후 다시 시도하세요.")
            self.sessions[key] = {"detector": detector, "used_at": now, "sequence": -1, "lock": threading.Lock()}
        return {"session_id": key, "algorithm": ALGORITHM, "engine_revision": ENGINE_REVISION, "mode": detector.mode}

    def frames(self, payload):
        key, frames, sequence = payload.get("session_id"), payload.get("frames"), payload.get("sequence")
        if not isinstance(key, str) or not isinstance(frames, list) or not 1 <= len(frames) <= 128 or isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("session_id, 순번, 1~128개 프레임이 필요합니다.")
        with self.lock:
            session = self.sessions.get(key)
        if session is None:
            raise ValueError("Python 판정 세션이 만료되었습니다. 다시 보정하거나 기록을 재시작하세요.")
        with session["lock"]:
            if sequence <= session["sequence"]:
                raise ValueError("중복되거나 역순인 요청입니다.")
            # 배치 전체의 시각을 먼저 확인하여 잘못된 배치가 일부만 적용되지 않게 한다.
            last = session["detector"].last_time
            for frame in frames:
                t = frame.get("t_ms") if isinstance(frame, dict) else None
                if isinstance(t, bool) or not isinstance(t, (float, int)) or not math.isfinite(t) or t < 0 or (last is not None and t <= last):
                    raise ValueError("프레임 시각은 단조 증가해야 합니다.")
                last = t
            started = time.perf_counter()
            result = [session["detector"].update(f["t_ms"], f.get("world_landmarks"), f.get("landmarks")) for f in frames]
            session["sequence"] = sequence; session["used_at"] = time.monotonic()
            return {"frames": result, "processing_ms": (time.perf_counter() - started) * 1000, "algorithm": ALGORITHM}

    def close(self, payload):
        key = payload.get("session_id")
        if not isinstance(key, str):
            raise ValueError("session_id가 필요합니다.")
        with self.lock:
            self.sessions.pop(key, None)
        return {"ok": True}


TAP = TapService()
