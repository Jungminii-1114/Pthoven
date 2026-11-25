"""Local relative-depth experiment. No metric height or contact verification."""
from __future__ import annotations

import base64
import io
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np

MODEL_ID = "depth-anything/Depth-Anything-V2-Small-hf"
REVISION = "5426e4f0f36572d16453bbda7a8389317b1bef99"
ROOT = Path(__file__).resolve().parents[1]


def model_input_shape(width, height, long_edge):
    """Preserve aspect ratio up to patch rounding, with an explicit long edge.

    DPT's keep_aspect_ratio 'minimal resize' can map two square requests to the
    same rectangle. Compute the rectangle first so 294 and 518 differ reliably.
    """
    factor = long_edge / max(width, height)
    return (max(14, round(height * factor / 14) * 14),
            max(14, round(width * factor / 14) * 14))


def validate_points(points, count=None):
    if not isinstance(points, list) or (count is not None and len(points) != count):
        raise ValueError("좌표 배열 형식이 올바르지 않습니다.")
    array = np.asarray(points, dtype=float)
    if array.ndim != 2 or array.shape[1] != 2 or not np.isfinite(array).all():
        raise ValueError("유한한 x, y 좌표가 필요합니다.")
    if (array < 0).any() or (array > 1).any():
        raise ValueError("좌표 범위는 0~1입니다.")
    return array


def extract_signals(depth, landmarks, anchors):
    """Subtract an image-space desk inverse-depth plane; divide by robust range.

    This is a scale/shift-resistant heuristic, NOT a Euclidean plane or mm height.
    Fingertip skin thickness is handled only by empirical per-finger calibration.
    """
    depth = np.asarray(depth, dtype=np.float32)
    if depth.ndim != 2 or not np.isfinite(depth).all():
        raise ValueError("깊이 맵이 올바르지 않습니다.")
    points = validate_points(landmarks, 21)
    desk = validate_points(anchors)
    if not 3 <= len(desk) <= 8:
        raise ValueError("손 밖의 책상 기준점을 3~8개 선택하세요.")
    design = np.c_[desk, np.ones(len(desk))]
    if np.linalg.matrix_rank(design) < 3 or np.linalg.cond(design) > 100:
        raise ValueError("책상 기준점을 한 직선에 두지 마세요.")
    h, w = depth.shape

    def patch(p, radius=3):
        x, y = np.rint(p * [w - 1, h - 1]).astype(int)
        return float(np.median(depth[max(0, y-radius):min(h, y+radius+1),
                                     max(0, x-radius):min(w, x+radius+1)]))

    hull = cv2.convexHull(points.astype(np.float32))
    blocked = any(cv2.pointPolygonTest(hull, tuple(p), True) >= -0.035 for p in desk)
    values = np.array([patch(p) for p in desk])
    plane, *_ = np.linalg.lstsq(design, values, rcond=None)
    lo, hi = np.percentile(depth, [5, 95])
    scale = float(hi - lo)
    valid = not blocked and scale > 1e-5
    reason = "desk_anchor_occluded" if blocked else "flat_depth_map" if not valid else None
    fingers = {}
    for name, tip in zip(["thumb", "index", "middle", "ring", "pinky"], [4, 8, 12, 16, 20]):
        p = points[tip]
        tip_valid = valid and bool(((p > 0.01) & (p < 0.99)).all())
        fingers[name] = {"valid": tip_valid, "reason": reason if not valid else None,
                         "contrast": float((patch(p) - np.dot([*p, 1], plane)) / scale) if tip_valid else None,
                         "tip": p.tolist()}
    return {"fingers": fingers, "desk_anchor_values": values.tolist(),
            "desk_plane_coefficients": plane.tolist(), "normalization_range": [float(lo), float(hi)],
            "desk_fit_residual": float(np.mean(np.abs(design @ plane - values))),
            "valid": valid, "reason": reason, "contact_verified": False}


class DepthService:
    def __init__(self):
        self.state = "not_loaded"
        self.error = None
        self.device = None
        self.lock = threading.Lock()
        self.load_lock = threading.Lock()
        self.model = self.processor = None

    def status(self):
        return {"state": self.state, "error": self.error, "model_id": MODEL_ID,
                "revision": REVISION, "device": self.device, "input_size": 294,
                "depth_type": "relative_inverse_depth", "contact_verified": False}

    def prepare(self):
        with self.load_lock:
            if self.state in {"loading", "ready"}:
                return self.status()
            self.state, self.error = "loading", None
            threading.Thread(target=self._load, daemon=True).start()
        return self.status()

    def _load(self):
        try:
            import torch
            from transformers import AutoImageProcessor, AutoModelForDepthEstimation
            cache = os.environ.get("PTOVEN_DEPTH_CACHE", str(ROOT / ".cache" / "depth"))
            self.processor = AutoImageProcessor.from_pretrained(MODEL_ID, revision=REVISION,
                                                               cache_dir=cache, use_fast=False)
            self.model = AutoModelForDepthEstimation.from_pretrained(
                MODEL_ID, revision=REVISION, cache_dir=cache, use_safetensors=True)
            requested = os.environ.get("PTOVEN_DEPTH_DEVICE", "auto")
            self.device = ("mps" if torch.backends.mps.is_available() else "cpu") if requested == "auto" else requested
            if self.device not in {"cpu", "mps", "cuda"}:
                raise ValueError("PTOVEN_DEPTH_DEVICE는 auto/cpu/mps/cuda 중 하나여야 합니다.")
            torch.set_num_threads(min(4, os.cpu_count() or 1))
            self.model.to(self.device).eval()
            self.state = "ready"
        except Exception as exc:
            self.error = f"모델 준비 실패: {type(exc).__name__}: {exc}. requirements-depth.txt 설치와 네트워크를 확인하세요."
            self.state = "error"

    def infer(self, payload):
        if self.state != "ready":
            raise RuntimeError("깊이 모델 준비 버튼을 누르고 준비 완료를 기다려주세요.")
        frame_id = payload.get("frame_id")
        input_size = payload.get("input_size", 294)
        if input_size not in (294, 518):
            raise ValueError("input_size는 294 또는 518이어야 합니다.")
        t_ms = payload.get("t_ms")
        if not isinstance(frame_id, int) or isinstance(frame_id, bool) or not isinstance(t_ms, (int, float)) or not np.isfinite(t_ms):
            raise ValueError("frame_id와 유한한 t_ms가 필요합니다.")
        landmarks, anchors = payload.get("landmarks"), payload.get("anchors")
        validate_points(landmarks, 21)
        desk = validate_points(anchors)
        if not 3 <= len(desk) <= 8:
            raise ValueError("책상 기준점 3~8개가 필요합니다.")
        encoded = payload.get("image", "")
        if not isinstance(encoded, str) or not encoded.startswith("data:image/jpeg;base64,") or len(encoded) > 2_000_000:
            raise ValueError("크기가 제한된 JPEG 이미지가 필요합니다.")
        raw = base64.b64decode(encoded.split(",", 1)[1], validate=True)
        from PIL import Image
        image = Image.open(io.BytesIO(raw))
        if image.width > 1280 or image.height > 1280 or min(image.size) < 32:
            raise ValueError("이미지 크기는 각 변 32~1280 픽셀이어야 합니다.")
        image = image.convert("RGB")
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("깊이 추론이 처리 중입니다. 프레임을 누적하지 말고 다음 프레임을 사용하세요.")
        try:
            import torch
            started = time.perf_counter()
            input_h, input_w = model_input_shape(image.width, image.height, input_size)
            inputs = self.processor(images=image, size={"height": input_h, "width": input_w},
                                    keep_aspect_ratio=False, return_tensors="pt")
            inputs = {k: v.to(self.device) for k, v in inputs.items()}
            with torch.inference_mode():
                prediction = self.model(**inputs).predicted_depth
                resized = torch.nn.functional.interpolate(prediction.unsqueeze(1),
                    size=(image.height, image.width), mode="bicubic", align_corners=False)[0, 0]
            depth = resized.float().cpu().numpy()
            signals = extract_signals(depth, landmarks, anchors)
            lo, hi = np.percentile(depth, [2, 98])
            preview = (np.clip((depth-lo)/max(float(hi-lo), 1e-6), 0, 1)*255).astype(np.uint8)
            preview = cv2.applyColorMap(preview, cv2.COLORMAP_INFERNO)
            ok, png = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 70])
            return {**signals, "frame_id": frame_id, "t_ms": t_ms,
                    "model": {**self.status(), "input_size": input_size, "processed_shape": [input_h, input_w]},
                    "inference_ms": round((time.perf_counter()-started)*1000, 2),
                    "preview": "data:image/jpeg;base64," + base64.b64encode(png).decode() if ok else None}
        finally:
            self.lock.release()


DEPTH = DepthService()
