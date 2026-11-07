"""Pthoven Python 타건 후보 판정 엔진."""
ENGINE_REVISION = "2026-11-07-known-rest-v3"
from .geometry import FINGERS, FEATURE_NAMES, extract_features, length_quality
from .calibration import ALGORITHM, CalibrationError, calibrate, diagnostic_text
from .detector import PoseTapDetector, TapOptions

__all__ = ["FINGERS", "FEATURE_NAMES", "ALGORITHM", "ENGINE_REVISION", "CalibrationError", "calibrate", "diagnostic_text",
           "extract_features", "length_quality", "PoseTapDetector", "TapOptions"]
