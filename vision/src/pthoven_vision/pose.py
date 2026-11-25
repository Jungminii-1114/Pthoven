"""Stateless B-01 pose math over the installed ``pthoven-tap`` geometry.

Policy defaults are experimental thresholds, not measured camera accuracy.
No camera, clock, collector, filtering, or physical contact inference lives here.
The caller owns frame freshness, capture duration, and request invalidation.
"""
from __future__ import annotations

from collections import Counter
import math

import numpy as np

from pthoven_tap.geometry import (
    ANGLE_INDICES, FEATURE_NAMES, FINGERS, TIP_INDICES,
    extract_features, length_quality,
)

ALGORITHM = "python_tip_angles_v1"
_EPSILON = .01
_GROUPS = {"tip": TIP_INDICES, "angles": ANGLE_INDICES}
_DEFAULTS = {
    "min_valid_frames": 45, "min_valid_ratio": .7,
    "min_snr": 2.5, "min_separation": 3.,
    "max_score_noise": .30, "min_ready_ratio": .7, "shape_limit": .30,
    "scale_min": .5, "scale_max": 1.8, "release": .2,
}
_FRAME_ERRORS = (ValueError, TypeError, KeyError, IndexError, OverflowError,
                 FloatingPointError)


class PoseError(ValueError):
    """Contract/calibration failure with JSON-safe structured diagnostics."""

    def __init__(self, code: str, message: str, diagnostics: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.diagnostics = dict(diagnostics) if diagnostics is not None else {}


def _policy(policy):
    if not isinstance(policy, dict):
        raise PoseError("invalid_policy", "policy must be a dictionary")
    result = {}
    for name, default in _DEFAULTS.items():
        value = policy.get(name, default)
        if name == "min_valid_frames":
            valid = type(value) is int and 1 <= value <= 5000
        else:
            try:
                valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                         and math.isfinite(value) and value >= 0)
            except OverflowError:
                valid = False
            if name != "release":
                valid = valid and value > 0
            if name in ("min_valid_ratio", "min_ready_ratio"):
                valid = valid and value <= 1
        if not valid:
            raise PoseError("invalid_policy", f"invalid {name}", {"field": name})
        result[name] = value
    if result["scale_min"] > result["scale_max"] or result["release"] >= .55:
        raise PoseError("invalid_policy", "require scale_min <= scale_max and release < .55")
    version = policy.get("policy_version")
    if version is not None and (type(version) not in (str, int)):
        raise PoseError("invalid_policy", "policy_version must be a string or integer")
    result.update(epsilon=_EPSILON, policy_version=version)
    return result


def _finger(finger):
    if not isinstance(finger, str) or finger not in FINGERS:
        raise PoseError("invalid_finger", "unsupported finger")


def _extract(frame, finger):
    if not isinstance(frame, dict):
        raise ValueError("frame must contain world_landmarks and landmarks")
    world, image = frame.get("world_landmarks"), frame.get("landmarks")
    if world is None or image is None:
        raise ValueError("both world_landmarks and landmarks are required")
    with np.errstate(all="raise"):
        feature = extract_features(world, finger, image)
    if not all(np.isfinite(feature[key]).all()
               for key in ("vector", "lengths", "palm_width")):
        raise ValueError("nonfinite extracted geometry")
    return feature


def _robust(matrix):
    center = np.median(matrix, axis=0)
    mad = np.median(np.abs(matrix - center), axis=0) * 1.4826
    q1, q3 = np.percentile(matrix, [25, 75], axis=0)
    return center, np.maximum(np.maximum(mad, (q3 - q1) / 1.349), _EPSILON)


def _summarize(frames, finger):
    if not isinstance(frames, list) or len(frames) > 5000:
        raise PoseError("invalid_frames", "capture must be a list of at most 5000 frames")
    rows, rejected = [], Counter()
    for frame in frames:
        try:
            rows.append(_extract(frame, finger))
        except _FRAME_ERRORS:
            rejected["landmarks_unavailable"] += 1
    count, attempted = len(rows), len(frames)
    summary = {"rows": rows, "stats": {
        "attempted_frames": attempted, "input_frames": attempted,
        "valid_frames": count, "used_frames": count,
        "rejected_frames": attempted - count, "valid_ratio": count / attempted if attempted else 0.,
        "rejection_reasons": dict(rejected), "outlier_frames": 0,
        "outlier_rule": "none; retain every extractable frame for quality checks",
    }}
    if rows:
        summary["center"], summary["noise"] = _robust(np.stack([r["vector"] for r in rows]))
        summary["present"] = np.stack([r["present"] for r in rows]).all(axis=0)
        summary["lengths"] = np.median(np.stack([r["lengths"] for r in rows]), axis=0)
        summary["palm"] = float(np.median([r["palm_width"] for r in rows]))
    return summary


def _enough(summary, policy):
    stats = summary["stats"]
    return (stats["valid_frames"] >= policy["min_valid_frames"]
            and stats["valid_ratio"] >= policy["min_valid_ratio"])


def _geometry(feature, reference, policy):
    return length_quality(
        feature["lengths"], reference["length_reference"],
        feature["palm_width"], reference["palm_reference"],
        shape_limit=policy["shape_limit"],
        scale_min=policy["scale_min"], scale_max=policy["scale_max"],
    )


def _group(feature, group):
    weights, delta, base = (np.asarray(group[k], dtype=float)
                            for k in ("weights", "delta", "base"))
    if not np.asarray(feature["present"])[weights > 0].all():
        return {"valid": False, "score": None, "residual": None}
    energy = float(np.sum(weights * delta ** 2))
    diff = feature["vector"] - base
    score = float(np.sum(weights * diff * delta) / energy)
    residual = float(np.sqrt(np.sum(weights * (diff - delta * score) ** 2) / energy))
    return {"valid": bool(-.5 <= score <= 2 and residual <= .8),
            "score": score, "residual": residual}


def _assess(feature, template, policy):
    geometry = _geometry(feature, template, policy)
    groups = {name: _group(feature, template["groups"][name]) for name in _GROUPS}
    visibility = feature["image_visibility"]
    reasons = list(geometry["reasons"])
    if not visibility["valid"]:
        reasons.append("target_out_of_frame")
    for name, group in groups.items():
        if not group["valid"]:
            reasons.append(f"{name}_features_missing" if group["score"] is None
                           else f"{name}_outside_calibration")
    scores = [g["score"] for g in groups.values()]
    g = template["groups"]["tip"]
    weights, delta = np.asarray(g["weights"]), np.asarray(g["delta"])
    tip_vector = ((feature["vector"] - np.asarray(g["base"])) * np.sqrt(weights)
                  / np.sqrt(np.sum(weights * delta ** 2)))
    return {
        "valid": not reasons, "reasons": reasons,
        "score": sum(scores) / 2 if all(s is not None for s in scores) else None,
        **groups, "geometry": geometry, "image_visibility": visibility,
        "tip_local": feature["tip_local"], "tip_image": feature["tip_image"],
        "tip_vector": tip_vector.tolist(), "contact_verified": False,
        "confidence": float(geometry["confidence"] /
                            (1 + sum(g["residual"] or 0 for g in groups.values()))),
    }


def _validate_template(template):
    try:
        if not isinstance(template, dict) or template.get("algorithm") != ALGORITHM:
            raise ValueError("unsupported algorithm")
        _finger(template.get("finger"))
        lengths = np.asarray(template["length_reference"], dtype=float)
        palm = template["palm_reference"]
        if (lengths.shape != (3,) or not np.isfinite(lengths).all() or lengths.min() <= 0
                or isinstance(palm, bool) or not isinstance(palm, (int, float))
                or not math.isfinite(palm) or palm <= 0):
            raise ValueError("invalid length reference")
        with np.errstate(all="raise"):
            for name, indices in _GROUPS.items():
                group = template["groups"][name]
                weights, base, delta = (np.asarray(group[k], dtype=float)
                                        for k in ("weights", "base", "delta"))
                if any(a.shape != (8,) or not np.isfinite(a).all()
                       for a in (weights, base, delta)):
                    raise ValueError("groups require finite eight-dimensional vectors")
                if (weights.min() < 0 or any(weights[i] > 0 for i in range(8) if i not in indices)
                        or float(np.sum(weights * delta ** 2)) <= 1e-12):
                    raise ValueError("invalid group weights or energy")
    except _FRAME_ERRORS as error:
        raise PoseError("invalid_template", "invalid pose template") from error


def _phase(rows, attempted, finger, policy, target=None):
    """All rates use attempted frames; missing frames cannot improve readiness."""
    geometry = [r["geometry"] for r in rows]
    visible = [r["image_visibility"]["valid"] for r in rows]
    motion = [True] * len(rows)
    scores = {}
    if target is not None:
        for name in _GROUPS:
            values = np.asarray([r[name]["score"] for r in rows])
            scores[name] = float(np.sqrt(np.mean((values - target) ** 2)))
        motion = [r["tip"]["valid"] and r["angles"]["valid"] and
                  (max(abs(r["tip"]["score"]), abs(r["angles"]["score"])) <= policy["release"] if target == 0
                   else min(r["tip"]["score"], r["angles"]["score"]) >= .55)
                  for r in rows]
    ready = [m and v and g["valid"] for m, v, g in zip(motion, visible, geometry)]
    segment_names = (["CMC–MCP", "MCP–IP", "IP–TIP"] if finger == "thumb"
                     else ["MCP–PIP", "PIP–DIP", "DIP–TIP"])
    errors = np.asarray([g["segment_errors"] for g in geometry])
    return {
        "score_noise": scores, "ready_ratio": sum(ready) / attempted,
        "observed_ratio": len(rows) / attempted,
        "motion_ready_ratio": sum(motion) / attempted,
        "geometry_ready_ratio": sum(g["valid"] for g in geometry) / attempted,
        "visibility_ready_ratio": sum(visible) / attempted,
        "valid_motion_rejected_by_geometry_ratio": sum(
            m and not g["valid"] for m, g in zip(motion, geometry)) / attempted,
        "length_rejected_ratio": sum(not g["valid"] for g in geometry) / attempted,
        "shape_rejected_ratio": sum(g["shape_error"] > policy["shape_limit"] for g in geometry) / attempted,
        "scale_rejected_ratio": sum(not policy["scale_min"] <= g["relative_scale"] <= policy["scale_max"]
                                    for g in geometry) / attempted,
        "common_scale": float(np.median([g["common_scale"] for g in geometry])),
        "relative_scale": float(np.median([g["relative_scale"] for g in geometry])),
        "shape_error": float(np.median([g["shape_error"] for g in geometry])),
        "shape_error_p95": float(np.percentile([g["shape_error"] for g in geometry], 95)),
        "shape_limit": policy["shape_limit"],
        "old_normalized_length_error": float(np.median([g["raw_error"] for g in geometry])),
        "segments": {name: {
            "median_error": float(np.median(errors[:, i])),
            "p95_error": float(np.percentile(errors[:, i], 95)),
            "rejected_ratio": float(np.count_nonzero(errors[:, i] > policy["shape_limit"]) / attempted),
        } for i, name in enumerate(segment_names)},
    }


def _quality_problem(phase, stats, policy):
    if stats["ready_ratio"] >= policy["min_ready_ratio"]:
        return None
    if stats["observed_ratio"] < policy["min_ready_ratio"]:
        return f"{phase}_landmarks_unavailable"
    if stats["visibility_ready_ratio"] < policy["min_ready_ratio"]:
        return f"{phase}_out_of_frame"
    if (stats["motion_ready_ratio"] >= policy["min_ready_ratio"]
            and stats["geometry_ready_ratio"] < policy["min_ready_ratio"]):
        return f"{phase}_geometry_unstable"
    if stats["motion_ready_ratio"] < policy["min_ready_ratio"]:
        return f"{phase}_motion_not_separable"
    return f"{phase}_combined_quality_low"


def fit_template(rest_frames: list, lift_frames: list, finger: str, policy: dict) -> dict:
    """Fit a median/MAD/IQR template compatible with pthoven_tap.measure.

    Raise PoseError on invalid inputs or failed calibration. No statistical
    outlier trimming is performed: every extractable sample affects quality.
    """
    _finger(finger)
    policy = _policy(policy)
    rest, lift = (_summarize(frames, finger) for frames in (rest_frames, lift_frames))
    diagnostics = {"algorithm": ALGORITHM, "finger": finger,
                   "policy_version": policy["policy_version"], "thresholds": dict(policy),
                   "rest": rest["stats"], "lift": lift["stats"],
                   "groups": {}, "phases": {}, "problems": []}
    if not all(_enough(s, policy) for s in (rest, lift)):
        diagnostics["problems"].append("insufficient_samples")
        raise PoseError("insufficient_samples", "not enough valid capture frames", diagnostics)
    delta = lift["center"] - rest["center"]
    noise = np.maximum(rest["noise"], lift["noise"])
    snr = np.abs(delta) / noise
    present = rest["present"] & lift["present"]
    model = {"algorithm": ALGORITHM, "finger": finger, "groups": {},
             "length_reference": rest["lengths"].tolist(), "palm_reference": rest["palm"],
             "contact_verified": False, "policy_version": policy["policy_version"],
             "feature_space": "palm_local_world+source_image", "angle_unit": "angle/pi",
             "landmark_ids": list(FINGERS[finger])}
    diagnostics["features"] = {
        name: {"rest_median": float(rest["center"][i]), "lift_median": float(lift["center"][i]),
               "delta": float(delta[i]), "rest_noise": float(rest["noise"][i]),
               "lift_noise": float(lift["noise"][i]), "noise": float(noise[i]), "snr": float(snr[i])}
        for i, name in enumerate(FEATURE_NAMES)
    }
    for name, indices in _GROUPS.items():
        selected = np.zeros(8, dtype=bool)
        selected[list(indices)] = present[list(indices)] & (snr[list(indices)] >= policy["min_snr"])
        weights = np.where(selected, 1 / noise ** 2, 0.)
        separation = float(np.sqrt(np.sum(weights * delta ** 2)))
        model["groups"][name] = {"base": rest["center"].tolist(),
                                 "delta": delta.tolist(), "weights": weights.tolist()}
        diagnostics["groups"][name] = {
            "selected": [FEATURE_NAMES[i] for i in np.flatnonzero(selected)],
            "separation": separation, "weights": weights.tolist(),
            "snr": {FEATURE_NAMES[i]: float(snr[i]) for i in indices},
        }
        if not selected.any() or separation < policy["min_separation"] or separation ** 2 <= 1e-12:
            diagnostics["problems"].append("indistinguishable_pose")
    if not diagnostics["problems"]:
        for phase, summary, target in (("rest", rest, 0), ("lift", lift, 1)):
            observations = [_assess(row, model, policy) for row in summary["rows"]]
            stats = _phase(observations, summary["stats"]["attempted_frames"], finger, policy, target)
            diagnostics["phases"][phase] = stats
            if max(stats["score_noise"].values()) > policy["max_score_noise"]:
                diagnostics["problems"].append("unstable_baseline" if phase == "rest" else "unstable_lift")
            problem = _quality_problem(phase, stats, policy)
            if problem:
                diagnostics["problems"].append(problem)
    diagnostics["problems"] = list(dict.fromkeys(diagnostics["problems"]))
    if diagnostics["problems"]:
        raise PoseError(diagnostics["problems"][0], "capture does not satisfy pose quality thresholds", diagnostics)
    model["diagnostics"] = diagnostics
    return model


def assess_pose(frame: dict, template: dict, policy: dict) -> dict:
    """Return frame quality/signals; malformed frames are unusable observations.

    Invalid policy/template raises PoseError. Quality failures retain finite raw
    coordinates and scores; no clamping or smoothing can conceal a failure.
    """
    policy = _policy(policy)
    _validate_template(template)
    try:
        feature = _extract(frame, template["finger"])
        with np.errstate(all="raise"):
            return _assess(feature, template, policy)
    except _FRAME_ERRORS:
        return {"valid": False, "reasons": ["landmarks_unavailable"], "score": None,
                "tip": {"valid": False, "score": None, "residual": None},
                "angles": {"valid": False, "score": None, "residual": None},
                "confidence": 0., "contact_verified": False}


def validate_rest(frames: list, active_fingers: list, policy: dict) -> dict:
    """Validate shared rest capture and return JSON-safe diagnostics only.

    Sample, visibility, and length gates apply independently to every active
    finger. Score stability/separation require a lift capture and are evaluated
    by fit_template, not inferred from a rest-only recording.
    """
    policy = _policy(policy)
    if not isinstance(active_fingers, list) or not active_fingers:
        raise PoseError("invalid_fingers", "active_fingers must be a nonempty list")
    for finger in active_fingers:
        _finger(finger)
    if len(set(active_fingers)) != len(active_fingers):
        raise PoseError("invalid_fingers", "active_fingers must be unique")
    diagnostics = {"policy_version": policy["policy_version"], "thresholds": dict(policy),
                   "fingers": {}, "problems": []}
    for finger in active_fingers:
        summary = _summarize(frames, finger)
        stats = diagnostics["fingers"][finger] = dict(summary["stats"])
        if not _enough(summary, policy):
            diagnostics["problems"].append("insufficient_samples")
            continue
        reference = {"length_reference": summary["lengths"].tolist(),
                     "palm_reference": summary["palm"]}
        stats["features"] = {
            name: {"median": float(summary["center"][i]), "noise": float(summary["noise"][i])}
            for i, name in enumerate(FEATURE_NAMES)
        }
        observations = [{"geometry": _geometry(row, reference, policy),
                         "image_visibility": row["image_visibility"]} for row in summary["rows"]]
        stats["quality"] = _phase(observations, len(frames), finger, policy)
        problem = _quality_problem("rest", stats["quality"], policy)
        if problem:
            diagnostics["problems"].append(problem)
    diagnostics["problems"] = list(dict.fromkeys(diagnostics["problems"]))
    if diagnostics["problems"]:
        raise PoseError(diagnostics["problems"][0], "rest capture is not ready", diagnostics)
    diagnostics.update(valid=True, contact_verified=False)
    return diagnostics
