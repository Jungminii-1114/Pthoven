"""Exploratory dorsal-patch measurements, not a trained tap/contact classifier."""
from __future__ import annotations

import cv2
import numpy as np


def skin_motion(previous: np.ndarray, current: np.ndarray) -> dict:
    if previous.shape != current.shape or previous.ndim != 2 or min(previous.shape) < 16:
        raise ValueError("Same-size grayscale patches of at least 16x16 required")
    if not np.isfinite(previous).all() or not np.isfinite(current).all():
        raise ValueError("Non-finite pixels")
    previous = np.clip(previous, 0, 255).astype(np.uint8)
    current = np.clip(current, 0, 255).astype(np.uint8)
    if min(float(previous.std()), float(current.std())) < 5:
        return {"valid": False, "reason": "insufficient_texture"}
    flow = cv2.calcOpticalFlowFarneback(previous, current, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    global_flow = np.median(flow.reshape(-1, 2), axis=0)
    residual = flow - global_flow
    du_dy, du_dx = np.gradient(residual[:, :, 0])
    dv_dy, dv_dx = np.gradient(residual[:, :, 1])
    shear = (du_dy + dv_dx) / 2
    strain = np.sqrt(du_dx**2 + dv_dy**2 + 2 * shear**2)
    return {
        "valid": True,
        "global_flow_px": [float(v) for v in global_flow],
        "local_flow_px": float(np.linalg.norm(residual, axis=2).mean()),
        "apparent_strain": float(strain.mean()),
        "brightness_shift": float(current.mean() - previous.mean()),
        "contact_verified": False,
    }
