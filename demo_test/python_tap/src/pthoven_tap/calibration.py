"""개인별 기준·들림 보정과 TIP/관절 독립 신호 구성."""
from __future__ import annotations

import numpy as np
from .geometry import FINGERS, FEATURE_NAMES, TIP_INDICES, ANGLE_INDICES, unpack_frame, extract_features, length_quality

ALGORITHM = "python_tip_angles_v1"


class CalibrationError(ValueError):
    def __init__(self, code, message, diagnostics=None):
        super().__init__(message); self.code = code; self.diagnostics = diagnostics or {}


def summarize(frames, finger):
    if not isinstance(frames, list) or len(frames) > 5000:
        raise ValueError("보정에는 최대 5,000개의 프레임을 사용합니다.")
    rows = []
    for frame in frames:
        try:
            world, image = unpack_frame(frame); rows.append(extract_features(world, finger, image))
        except (ValueError, TypeError, KeyError, IndexError):
            pass
    if len(rows) < 5 or len(rows) < len(frames) * .7:
        raise CalibrationError("landmarks_missing", "유효한 관절 프레임이 부족합니다.", {"input_frames": len(frames), "valid_frames": len(rows)})
    matrix = np.stack([r["vector"] for r in rows])
    present = np.stack([r["present"] for r in rows]).all(axis=0)
    # 순간적인 외곽치만 제거: 안정적인 소수만 골라 보정을 통과시키지 않는다.
    center = np.median(matrix, axis=0)
    deviation = np.linalg.norm((matrix - center)[:, present], axis=1)
    mid = np.median(deviation); mad = np.median(np.abs(deviation - mid))
    kept = [r for r, d in zip(rows, deviation) if d <= max(.03, mid + 3.5 * 1.4826 * mad)]
    if len(kept) < max(5, len(frames) * .7):
        raise CalibrationError("capture_quality_low", "추적 이상 프레임이 너무 많습니다.")
    matrix = np.stack([r["vector"] for r in kept])
    return {"rows": kept, "mean": matrix.mean(axis=0), "std": matrix.std(axis=0), "present": present,
            "lengths": np.median(np.stack([r["lengths"] for r in kept]), axis=0),
            "palm_width": float(np.median([r["palm_width"] for r in kept])),
            "stats": {"input_frames": len(frames), "valid_frames": len(rows), "used_frames": len(kept), "outlier_frames": len(rows) - len(kept)}}


def group_signal(feature, model, group):
    g = model["groups"][group]
    weights, delta = np.asarray(g["weights"]), np.asarray(g["delta"])
    selected = weights > 0
    if not selected.any() or not np.asarray(feature["present"])[selected].all():
        return {"valid": False, "score": None, "residual": None}
    diff = feature["vector"] - np.asarray(g["base"])
    energy = float(np.sum(weights * delta ** 2))
    if energy <= 0:
        return {"valid": False, "score": None, "residual": None}
    score = float(np.sum(weights * diff * delta) / energy)
    residual = float(np.sqrt(np.sum(weights * (diff - delta * score) ** 2) / energy))
    return {"valid": bool(-.5 <= score <= 2 and residual <= .8), "score": score, "residual": residual}


def measure(feature, model):
    geometry = length_quality(feature["lengths"], model["length_reference"], feature["palm_width"], model["palm_reference"])
    tip, angles = group_signal(feature, model, "tip"), group_signal(feature, model, "angles")
    image_visibility = feature.get("image_visibility", {"valid": True})
    valid = geometry["valid"] and tip["valid"] and angles["valid"] and image_visibility["valid"]
    score = (tip["score"] + angles["score"]) / 2 if tip["score"] is not None and angles["score"] is not None else None
    return {"valid": bool(valid), "score": score, "tip": tip, "angles": angles, "geometry": geometry,
            "image_visibility": image_visibility,
            "tip_local": feature["tip_local"], "tip_image": feature["tip_image"],
            "tip_vector": ((feature["vector"] - np.asarray(model["groups"]["tip"]["base"])) * np.sqrt(model["groups"]["tip"]["weights"]) /
                           max(float(np.sqrt(np.sum(np.asarray(model["groups"]["tip"]["weights"]) * np.asarray(model["groups"]["tip"]["delta"]) ** 2))), 1e-9)).tolist(),
            "confidence": float(geometry["confidence"] / (1 + (tip["residual"] or 0) + (angles["residual"] or 0)))}


def calibrate(rest_frames, lift_frames, finger):
    if finger not in FINGERS:
        raise ValueError("지원하지 않는 손가락입니다.")
    rest, lift = summarize(rest_frames, finger), summarize(lift_frames, finger)
    delta = lift["mean"] - rest["mean"]
    noise = np.maximum(np.maximum(rest["std"], lift["std"]), .01)
    snr = np.abs(delta) / noise
    present = rest["present"] & lift["present"]
    model = {"algorithm": ALGORITHM, "finger": finger, "length_reference": rest["lengths"].tolist(),
             "palm_reference": rest["palm_width"], "groups": {}, "contact_verified": False}
    diagnostics = {"algorithm": ALGORITHM, "finger": finger, "rest": rest["stats"], "lift": lift["stats"], "groups": {}, "phases": {}, "problems": []}
    for name, indices in [("tip", TIP_INDICES), ("angles", ANGLE_INDICES)]:
        selected = np.zeros(8, dtype=bool)
        for i in indices:
            selected[i] = bool(present[i] and snr[i] >= 2)
        weights = np.where(selected, 1 / noise ** 2, 0)
        separation = float(np.sqrt(np.sum(weights * delta ** 2)))
        model["groups"][name] = {"weights": weights.tolist(), "base": rest["mean"].tolist(), "delta": delta.tolist()}
        diagnostics["groups"][name] = {"selected": [FEATURE_NAMES[i] for i in np.flatnonzero(selected)], "separation": separation,
                                       "snr": {FEATURE_NAMES[i]: float(snr[i]) for i in indices if present[i]}}
        if not selected.any() or separation < 2.5:
            diagnostics["problems"].append(f"{name}_unobservable")
    if not diagnostics["problems"]:
        for phase, summary in [("rest", rest), ("lift", lift)]:
            observed = [measure(r, model) for r in summary["rows"]]
            target = 0 if phase == "rest" else 1
            noise_scores = {g: float(np.sqrt(np.mean([(r[g]["score"] - target) ** 2 for r in observed]))) for g in ("tip", "angles")}
            motion_ready = [r["tip"]["valid"] and r["angles"]["valid"] and
                            (max(r["tip"]["score"], r["angles"]["score"]) <= .2 if phase == "rest" else min(r["tip"]["score"], r["angles"]["score"]) >= .55) for r in observed]
            ready = [motion and r["geometry"]["valid"] and r["image_visibility"]["valid"] for motion, r in zip(motion_ready, observed)]
            geometry = [r["geometry"] for r in observed]
            segment_errors = np.asarray([g["segment_errors"] for g in geometry])
            segment_names = ["CMC–MCP", "MCP–IP", "IP–TIP"] if finger == "thumb" else ["MCP–PIP", "PIP–DIP", "DIP–TIP"]
            diagnostics["phases"][phase] = {"score_noise": noise_scores, "ready_ratio": float(np.mean(ready)),
                "motion_ready_ratio": float(np.mean(motion_ready)),
                "geometry_ready_ratio": float(np.mean([g["valid"] for g in geometry])),
                "visibility_ready_ratio": float(np.mean([r["image_visibility"]["valid"] for r in observed])),
                "valid_motion_rejected_by_geometry_ratio": float(np.mean([motion and not r["geometry"]["valid"] for motion, r in zip(motion_ready, observed)])),
                "length_rejected_ratio": float(np.mean([not g["valid"] for g in geometry])),
                "shape_rejected_ratio": float(np.mean([g["shape_error"] > .3 for g in geometry])),
                "scale_rejected_ratio": float(np.mean([g["relative_scale"] < .5 or g["relative_scale"] > 1.8 for g in geometry])),
                "common_scale": float(np.median([g["common_scale"] for g in geometry])),
                "relative_scale": float(np.median([g["relative_scale"] for g in geometry])),
                "shape_error": float(np.median([g["shape_error"] for g in geometry])),
                "shape_error_p95": float(np.percentile([g["shape_error"] for g in geometry], 95)),
                "shape_limit": .3,
                "segments": {name: {"median_error": float(np.median(segment_errors[:, i])),
                                    "p95_error": float(np.percentile(segment_errors[:, i], 95)),
                                    "rejected_ratio": float(np.mean(segment_errors[:, i] > .3))}
                             for i, name in enumerate(segment_names)},
                "old_normalized_length_error": float(np.median([g["raw_error"] for g in geometry]))}
            if max(noise_scores.values()) > .3:
                diagnostics["problems"].append(f"{phase}_score_unstable")
            if np.mean(ready) < .7:
                if diagnostics["phases"][phase]["visibility_ready_ratio"] < .7:
                    diagnostics["problems"].append(f"{phase}_out_of_frame")
                elif np.mean(motion_ready) >= .7 and diagnostics["phases"][phase]["geometry_ready_ratio"] < .7:
                    diagnostics["problems"].append(f"{phase}_geometry_unstable")
                elif np.mean(motion_ready) < .7:
                    diagnostics["problems"].append(f"{phase}_motion_not_separable")
                else:
                    diagnostics["problems"].append(f"{phase}_combined_quality_low")
    if diagnostics["problems"]:
        raise CalibrationError(diagnostics["problems"][0], "TIP·관절 또는 길이 관측이 보정 조건을 만족하지 않습니다. 상세 진단을 확인하세요.", diagnostics)
    model["diagnostics"] = diagnostics
    return model


def diagnostic_text(d):
    lines = ["Python 판정 · TIP 궤적 + 관절각 · 공통 스케일/국소 왜곡 분리"]
    for k, label in [("rest", "기준"), ("lift", "들림")]:
        if k in d:
            s = d[k]; lines.append(f"{label}: {s.get('used_frames', 0)}/{s.get('input_frames', 0)}프레임 사용")
    for group, label in [("tip", "TIP"), ("angles", "관절각")]:
        if group in d.get("groups", {}):
            g = d["groups"][group]; lines.append(f"{label}: {', '.join(g['selected']) or '구분 가능한 특징 없음'} · 자세 차이 {g['separation']:.2f}")
    for phase, p in d.get("phases", {}).items():
        label = "기준" if phase == "rest" else "들림"
        lines.extend([f"{label} 점수 변동: TIP {p['score_noise']['tip']:.1%} / 관절 {p['score_noise']['angles']:.1%}",
                      f"{label} 동작 신호 통과 {p.get('motion_ready_ratio', p['ready_ratio']):.0%} · 길이 통과 {p.get('geometry_ready_ratio', 1-p['length_rejected_ratio']):.0%}",
                      f"{label} 판정 가능 {p['ready_ratio']:.0%} · 길이 보류 {p['length_rejected_ratio']:.0%}",
                      f"  국소 왜곡 보류 {p['shape_rejected_ratio']:.0%} · 극단 배율 보류 {p['scale_rejected_ratio']:.0%}",
                      f"{label} 공통 배율 {p['common_scale']:.3f} · 손바닥 대비 배율 {p['relative_scale']:.3f}",
                      f"{label} 국소 길이 왜곡 {p['shape_error']:.1%} · 이전 방식 길이 오차 {p['old_normalized_length_error']:.1%}"])
        if "shape_error_p95" in p:
            lines.append(f"  국소 왜곡 중앙값 {p['shape_error']:.1%} / 95백분위 {p['shape_error_p95']:.1%} · 프레임별 허용 {p['shape_limit']:.0%}")
        for name, s in p.get("segments", {}).items():
            lines.append(f"  {name}: 중앙 오차 {s['median_error']:.1%} · 95백분위 {s['p95_error']:.1%} · 기준 초과 {s['rejected_ratio']:.0%}")
    if d.get("problems"):
        reasons = []
        for problem in d["problems"]:
            phase = "기준" if problem.startswith("rest_") else "들림"
            if problem.endswith("geometry_unstable"):
                reasons.append(f"{problem} · {phase}의 TIP·관절 신호는 구분되지만 길이 검사를 통과한 프레임이 부족합니다.")
            elif problem.endswith("motion_not_separable"):
                reasons.append(f"{problem} · {phase}의 TIP·관절 동작 신호를 안정적으로 구분하지 못합니다.")
            else:
                reasons.append(problem)
        lines.append("보류 사유: " + " / ".join(reasons))
    lines.append("물리 접촉·실제 높이는 확인하지 않습니다.")
    return "\n".join(lines)
