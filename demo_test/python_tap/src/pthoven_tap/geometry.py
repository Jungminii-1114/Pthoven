"""RGB 모델이 추정한 랜드마크의 기하 특징. 물리 높이·접촉 측정이 아니다."""
from __future__ import annotations

import numpy as np

FINGERS = {"thumb": (1, 2, 3, 4), "index": (5, 6, 7, 8),
           "middle": (9, 10, 11, 12), "ring": (13, 14, 15, 16), "pinky": (17, 18, 19, 20)}
FEATURE_NAMES = ["TIP 상대 x", "TIP 상대 y", "TIP 상대 z", "뿌리 각도", "중간 각도", "끝 관절각", "영상 TIP 상대 x", "영상 TIP 상대 y"]
TIP_INDICES = (0, 1, 2, 6, 7)
ANGLE_INDICES = (3, 4, 5)


def points(value, dimensions=3):
    if not isinstance(value, (list, tuple, np.ndarray)) or len(value) != 21:
        raise ValueError("21개 손 랜드마크가 필요합니다.")
    rows = [[p[k] for k in ("x", "y", "z")[:dimensions]] if isinstance(p, dict) else list(p)[:dimensions] for p in value]
    out = np.asarray(rows, dtype=float)
    if out.shape != (21, dimensions) or not np.isfinite(out).all():
        raise ValueError("랜드마크 좌표는 유한한 숫자여야 합니다.")
    return out


def unit(value):
    length = float(np.linalg.norm(value))
    if length < 1e-8:
        raise ValueError("손바닥 기준축을 구성할 수 없습니다.")
    return value / length


def joint_angle(a, b, c):
    u, v = a - b, c - b
    denominator = float(np.linalg.norm(u) * np.linalg.norm(v))
    if denominator < 1e-12:
        raise ValueError("관절 구간이 겹쳐 각도를 계산할 수 없습니다.")
    return float(np.arccos(np.clip(np.dot(u, v) / denominator, -1, 1)) / np.pi)


def unpack_frame(frame):
    """이전 진단의 21×3 배열과 새 {world_landmarks, landmarks} 형식을 지원."""
    if isinstance(frame, dict):
        return frame.get("world_landmarks", frame.get("world")), frame.get("landmarks")
    return frame, None


def extract_features(world, finger, image=None):
    if finger not in FINGERS:
        raise ValueError("지원하지 않는 손가락입니다.")
    p = points(world)
    width = float(np.linalg.norm(p[5] - p[17]))
    x = unit(p[5] - p[17])
    along = p[9] - p[0]
    y = unit(along - x * np.dot(along, x))
    z = unit(np.cross(x, y))
    # 판정 특징은 손바닥 로컬 좌표: 손 전체 이동·회전을 타건으로 세지 않는다.
    local = (p - p[0]) @ np.stack([x, y, z]).T / width
    a, b, c, d = FINGERS[finger]
    tip = local[d] - local[a]
    angles = [joint_angle(local[0], local[a], local[b]),
              joint_angle(local[a], local[b], local[c]), joint_angle(local[b], local[c], local[d])]
    # 길이 검사는 로컬 좌표가 아니라 원래 3D 좌표의 인접 구간을 사용한다.
    # MCP→TIP 직선거리는 굽힘에 따라 정상적으로 달라지므로 길이 제약에 쓰지 않는다.
    lengths = np.asarray([np.linalg.norm(p[b] - p[a]), np.linalg.norm(p[c] - p[b]), np.linalg.norm(p[d] - p[c])])
    if np.min(lengths) < 1e-8:
        raise ValueError("손가락 구간 길이가 0입니다.")
    vector = np.asarray([*tip, *angles, 0., 0.])
    present = np.asarray([True] * 6 + [False, False])
    tip_image = None
    image_visibility = {"valid": True, "available": False, "outside_ids": [], "required_outside_ids": []}
    if image is not None:
        q = points(image, 2)
        # 정규화된 추정 좌표는 프레임 밖에서도 반환될 수 있다. 좌표를 자르지 않는다.
        # 손목·다른 손가락 하나가 벗어났다고 손 전체를 폐기하지 않는다.
        # 평가 손가락의 네 관절과 손바닥 MCP 축이 벗어나면 해당 손가락만 보류한다.
        outside = np.flatnonzero(((q < 0) | (q > 1)).any(axis=1)).tolist()
        required = set((5, 9, 17, a, b, c, d))
        required_outside = sorted(required.intersection(outside))
        image_visibility = {"valid": not required_outside, "available": True,
                            "outside_ids": outside, "required_outside_ids": required_outside}
        ix = unit(q[5] - q[17]); forward = q[9] - q[0]
        iy = unit(forward - ix * np.dot(forward, ix))
        delta = q[d] - q[a]; scale = np.linalg.norm(q[5] - q[17])
        vector[6:] = [np.dot(delta, ix) / scale, np.dot(delta, iy) / scale]
        present[6:] = True; tip_image = q[d].tolist()
    return {"vector": vector, "present": present, "lengths": lengths, "palm_width": width,
            "tip_local": tip.tolist(), "tip_image": tip_image, "angles": angles,
            "image_visibility": image_visibility,
            "reach": float(np.linalg.norm(p[d] - p[a]))}


def length_quality(lengths, reference, palm_width, reference_palm, *, shape_limit=.30, scale_min=.5, scale_max=1.8):
    """공통 배율과 국소 변형을 분리한다."""
    values, base = np.asarray(lengths, dtype=float), np.asarray(reference, dtype=float)
    if values.shape != (3,) or base.shape != (3,) or not np.isfinite(values).all() or not np.isfinite(base).all() or min(values.min(), base.min(), palm_width, reference_palm) <= 0:
        raise ValueError("길이 검사 입력이 올바르지 않습니다.")
    ratios = values / base
    # 공통 배율 alpha: 세 구간이 함께 34.8% 변해도 국소 왜곡과 구분한다.
    alpha = float(np.median(ratios))
    shape_error = float(np.max(np.abs(ratios / alpha - 1)))
    palm_scale = float(palm_width / reference_palm)
    relative_scale = alpha / palm_scale
    valid = shape_error <= shape_limit and scale_min <= relative_scale <= scale_max
    reasons = ([] if shape_error <= shape_limit else ["local_bone_distortion"]) + ([] if scale_min <= relative_scale <= scale_max else ["extreme_finger_scale"])
    # 점수는 센서 확률이 아닌 품질 휴리스틱이며 기하 오류 판정을 대체하지 않는다.
    confidence = max(0., 1 - shape_error / max(shape_limit, 1e-9)) * min(relative_scale, 1 / relative_scale)
    return {"valid": valid, "common_scale": alpha, "palm_scale": palm_scale, "relative_scale": relative_scale,
            "shape_error": shape_error, "raw_error": float(np.max(np.abs(ratios / palm_scale - 1))),
            "segment_ratios": ratios.tolist(), "aligned_ratios": (ratios / alpha).tolist(),
            "segment_errors": np.abs(ratios / alpha - 1).tolist(),
            "confidence": float(confidence), "reasons": reasons}
