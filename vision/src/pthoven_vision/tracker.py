"""Pinned official model, lazy preparation and per-session Python VIDEO tracker."""
from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import math
import io
import os
from pathlib import Path
import threading
import urllib.request

import numpy as np
from .contracts import B01Error

MODEL_URL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
MODEL_SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"


def decode_image(value):
    import cv2
    if not value.startswith("data:image/jpeg;base64,"):
        raise B01Error("INVALID_IMAGE", "JPEG 데이터 URL이 필요합니다.", 400)
    try:
        raw = base64.b64decode(value.split(",", 1)[1], validate=True)
        if len(raw) > 1_000_000:
            raise ValueError("image limit")
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as header:
            if header.format != "JPEG" or header.width*header.height > 1920*1080:
                raise ValueError("invalid decoded dimensions")
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.ndim != 3 or image.shape[0]*image.shape[1] > 1920*1080:
            raise ValueError("invalid image size")
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    except (ValueError, TypeError, OSError, cv2.error) as exc:
        raise B01Error("INVALID_IMAGE", "이미지 디코딩 또는 크기 검사가 실패했습니다.", 400) from exc


class TrackerManager:
    def __init__(self, cache=None):
        root = Path(__file__).resolve().parents[2]
        self.path = Path(cache or os.environ.get("PTHOVEN_MODEL_PATH", root / ".cache/hand_landmarker.task"))
        self.lock = threading.RLock()
        self.state = "not_loaded"
        self.error = None
        self.workers = {}
        self.timestamps = {}

    def status(self):
        try:
            sdk = importlib.metadata.version("mediapipe")
        except importlib.metadata.PackageNotFoundError:
            sdk = None
        with self.lock:
            return {"state": self.state, "error": self.error, "model_ref": "sha256:"+MODEL_SHA256,
                    "sdk": sdk, "inference_location": "python", "contact_verified": False}

    def prepare(self):
        with self.lock:
            if self.state not in {"loading", "ready"}:
                self.state, self.error = "loading", None
                threading.Thread(target=self._prepare, daemon=True).start()
        return self.status()

    def _prepare(self):
        try:
            mpl_cache = self.path.parent / "matplotlib"
            mpl_cache.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("MPLCONFIGDIR", str(mpl_cache))
            if importlib.metadata.version("mediapipe") != "0.10.21":
                raise ValueError("검증 대상 MediaPipe 0.10.21을 설치하세요.")
            if not self.path.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with urllib.request.urlopen(MODEL_URL, timeout=30) as response:
                    content = response.read(32_000_001)
                if len(content) > 32_000_000 or hashlib.sha256(content).hexdigest() != MODEL_SHA256:
                    raise ValueError("공식 모델 SHA256이 일치하지 않습니다.")
                temporary = self.path.with_suffix(".download")
                temporary.write_bytes(content)
                temporary.replace(self.path)
            if hashlib.sha256(self.path.read_bytes()).hexdigest() != MODEL_SHA256:
                raise ValueError("모델 캐시 SHA256이 일치하지 않습니다. 캐시를 확인하세요.")
            from .worker import ProcessTracker
            probe = ProcessTracker(self.path)
            try:
                probe.infer(np.zeros((240,320,3),dtype=np.uint8),0)
            finally:
                probe.close()
            with self.lock:
                self.state = "ready"
        except Exception as exc:
            with self.lock:
                self.state, self.error = "error", f"{type(exc).__name__}: {exc}"

    def infer(self, session_id, rgb, t_ms):
        from .worker import ProcessTracker
        with self.lock:
            if self.state != "ready":
                raise B01Error("TRACKER_NOT_READY", self.error or "먼저 Python 모델을 준비하세요.", 503)
            worker=self.workers.get(session_id)
        try:
            if worker is None:
                worker=ProcessTracker(self.path)
                with self.lock:
                    self.workers[session_id]=worker
            return worker.infer(rgb,math.floor(t_ms))
        except B01Error as exc:
            if exc.code.startswith("INFERENCE"):
                with self.lock:
                    failed=self.workers.pop(session_id,None)
                    self.state,self.error="error",str(exc)
                if failed:failed.close()
            raise

    def close(self, session_id):
        with self.lock:
            worker = self.workers.pop(session_id, None)
            if worker:
                worker.close()

    def close_all(self):
        for sid in list(self.workers):
            self.close(sid)
