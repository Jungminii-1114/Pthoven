"""Synthetic geometry/math tests, not camera accuracy or contact evidence.

Run with pthoven-tap installed editable and b01-session/src on PYTHONPATH:
    python -B -m unittest discover -s b01-session/tests -p test_pose.py
"""
import copy
import json
import math
import unittest
from unittest.mock import patch

import numpy as np

from pthoven_tap.calibration import measure
from pthoven_tap.detector import validate_model
from pthoven_tap.examples import analytic_hand
from pthoven_tap.geometry import (
    ANGLE_INDICES, FEATURE_NAMES, FINGERS, TIP_INDICES, extract_features,
)
from pthoven_vision import pose
from pthoven_vision.pose import PoseError, assess_pose, fit_template, validate_rest


def frame(finger="index", lift=0.):
    world = analytic_hand({finger: lift})
    return {"world_landmarks": world,
            "landmarks": [[.5 + p[0] * .12, .6 - p[1] * .12] for p in world]}


def capture(finger="index", lift=0., count=45):
    return [frame(finger, lift) for _ in range(count)]


def stretch(value, finger="index", scale=1., segment=None):
    """Change chain length while retaining joint directions/angles."""
    result = copy.deepcopy(value)
    points = np.asarray(result["world_landmarks"])
    ids = FINGERS[finger]
    original = points.copy()
    for i in range(3):
        factor = scale if segment is None or segment == i else 1.
        points[ids[i + 1]] = points[ids[i]] + (original[ids[i + 1]] - original[ids[i]]) * factor
    result["world_landmarks"] = points.tolist()
    return result


class PoseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = {finger: fit_template(capture(finger), capture(finger, 1), finger, {})
                      for finger in FINGERS}

    def assertPoseError(self, code, call, *args):
        with self.assertRaises(PoseError) as caught:
            call(*args)
        error = caught.exception
        self.assertEqual(error.code, code)
        self.assertEqual(str(error), error.message)
        self.assertIsInstance(error.diagnostics, dict)
        json.dumps(error.diagnostics, allow_nan=False)
        return error

    def test_error_exposes_code_message_and_diagnostics(self):
        error = PoseError("example", "message", {"count": 2})
        self.assertIsInstance(error, ValueError)
        self.assertEqual((error.code, error.message, str(error)), ("example", "message", "message"))
        self.assertEqual(error.diagnostics, {"count": 2})
        self.assertEqual(PoseError("example", "message").diagnostics, {})

    def test_all_fingers_keep_legacy_model_and_measure_compatibility(self):
        for finger, model in self.models.items():
            with self.subTest(finger=finger):
                self.assertEqual(model["algorithm"], "python_tip_angles_v1")
                self.assertEqual(model["finger"], finger)
                self.assertEqual(model["landmark_ids"], list(FINGERS[finger]))
                self.assertFalse(model["contact_verified"])
                validate_model(json.loads(json.dumps(model, allow_nan=False)))
                for level in (0, 1):
                    sample = frame(finger, level)
                    feature = extract_features(sample["world_landmarks"], finger, sample["landmarks"])
                    legacy = measure(feature, model)
                    result = assess_pose(sample, model, {})
                    self.assertTrue(result["valid"], result)
                    self.assertEqual(result["geometry"], legacy["geometry"])
                    self.assertEqual(result["image_visibility"], legacy["image_visibility"])
                    for group in ("tip", "angles"):
                        self.assertAlmostEqual(result[group]["score"], level)
                        self.assertEqual(result[group], legacy[group])
                    np.testing.assert_allclose(result["tip_vector"], legacy["tip_vector"], atol=1e-14)
                    json.dumps(result, allow_nan=False)

    def test_group_weights_are_independent_and_noise_based(self):
        model = self.models["index"]
        for group, allowed in (("tip", TIP_INDICES), ("angles", ANGLE_INDICES)):
            weights = model["groups"][group]["weights"]
            self.assertTrue(any(weights))
            for i, weight in enumerate(weights):
                if i not in allowed:
                    self.assertEqual(weight, 0)
                elif weight:
                    self.assertAlmostEqual(weight, 10000.)
                    self.assertGreaterEqual(model["diagnostics"]["features"][FEATURE_NAMES[i]]["snr"], 2.5)

    def test_robust_noise_uses_mad_iqr_and_epsilon(self):
        # Constant / symmetric two clusters / 5 zeros + 3 fours.
        values = np.asarray([[7] * 8, [-2] * 4 + [2] * 4, [0] * 5 + [4] * 3]).T
        center, noise = pose._robust(values)
        np.testing.assert_array_equal(center, [7, 0, 0])
        np.testing.assert_allclose(noise, [.01, 2 * 1.4826, 4 / 1.349], rtol=1e-12)

    def test_template_uses_medians_without_discarding_extractable_outliers(self):
        rest = capture(count=50) + capture(lift=.12, count=10)
        model = fit_template(rest, capture(lift=1, count=60), "index", {})
        expected = extract_features(frame()["world_landmarks"], "index", frame()["landmarks"])["vector"]
        for group in ("tip", "angles"):
            np.testing.assert_allclose(model["groups"][group]["base"], expected)
        stats = model["diagnostics"]["rest"]
        self.assertEqual(stats["used_frames"], 60)
        self.assertEqual(stats["outlier_frames"], 0)

    def test_identical_poses_never_force_a_template_for_any_finger(self):
        for finger in FINGERS:
            with self.subTest(finger=finger):
                self.assertPoseError("indistinguishable_pose", fit_template,
                                     capture(finger), capture(finger), finger, {})

    def test_feature_snr_and_group_separation_policy_are_both_enforced(self):
        for key in ("min_snr", "min_separation"):
            with self.subTest(key=key):
                self.assertPoseError("indistinguishable_pose", fit_template,
                                     capture(), capture(lift=1), "index", {key: 100000.})
        # Selecting features alone does not waive the group separation check.
        error = self.assertPoseError("indistinguishable_pose", fit_template,
                                    capture(), capture(lift=1), "index", {"min_separation": 100000.})
        self.assertTrue(error.diagnostics["groups"]["tip"]["selected"])
        self.assertTrue(error.diagnostics["groups"]["angles"]["selected"])

    def test_one_observable_group_cannot_substitute_for_the_other(self):
        # Uniform finger scaling changes the TIP but keeps every joint angle.
        scaled = [stretch(frame(), scale=1.348) for _ in range(45)]
        error = self.assertPoseError("indistinguishable_pose", fit_template, capture(), scaled, "index", {})
        self.assertTrue(error.diagnostics["groups"]["tip"]["selected"])
        self.assertFalse(error.diagnostics["groups"]["angles"]["selected"])

    def test_default_minimum_valid_frame_count_is_45_per_phase(self):
        for rest_count, lift_count in ((44, 45), (45, 44), (0, 45)):
            with self.subTest(rest=rest_count, lift=lift_count):
                self.assertPoseError("insufficient_samples", fit_template,
                                     capture(count=rest_count), capture(lift=1, count=lift_count), "index", {})
        model = fit_template(capture(count=5), capture(lift=1, count=5), "index", {"min_valid_frames": 5})
        self.assertEqual(model["diagnostics"]["rest"]["valid_frames"], 5)

    def test_missing_samples_preserve_counts_and_default_70_percent_gate(self):
        # Exactly 70/100 passes; 69/100 fails even though >=45 are extractable.
        model = fit_template(capture(count=70) + [None] * 30,
                             capture(lift=1, count=70) + [None] * 30, "index", {})
        self.assertEqual(model["diagnostics"]["rest"]["rejected_frames"], 30)
        self.assertEqual(model["diagnostics"]["phases"]["rest"]["ready_ratio"], .7)
        error = self.assertPoseError("insufficient_samples", fit_template,
                                    capture(count=69) + [None] * 31, capture(lift=1), "index", {})
        self.assertEqual(error.diagnostics["rest"]["valid_ratio"], .69)

    def test_ready_ratio_uses_all_attempted_frames(self):
        with self.assertRaises(PoseError) as caught:
            fit_template(capture() + [None] * 15, capture(lift=1), "index", {"min_ready_ratio": .8})
        self.assertEqual(caught.exception.diagnostics["phases"]["rest"]["ready_ratio"], .75)
        self.assertEqual(caught.exception.code, "rest_landmarks_unavailable")

    def test_rest_readiness_matches_absolute_neutral_gate(self):
        observation = assess_pose(frame(), self.models["index"], {})
        # A score below -release remains in the model range but is not neutral.
        observation["tip"]["score"] = observation["angles"]["score"] = -.4
        stats = pose._phase([observation], 1, "index", pose._policy({}), target=0)
        self.assertEqual(stats["motion_ready_ratio"], 0.)

    def test_rest_and_lift_score_noise_are_checked_separately(self):
        stable_rest, stable_lift = capture(count=60), capture(lift=1, count=60)
        noisy_rest = capture(count=45) + capture(lift=1, count=15)
        noisy_lift = capture(lift=1, count=45) + capture(count=15)
        for rest, lift, phase, code in (
            (noisy_rest, stable_lift, "rest", "unstable_baseline"),
            (stable_rest, noisy_lift, "lift", "unstable_lift"),
        ):
            with self.subTest(phase=phase):
                error = self.assertPoseError(code, fit_template, rest, lift, "index", {})
                noise = error.diagnostics["phases"][phase]["score_noise"]
                for group in ("tip", "angles"):
                    self.assertAlmostEqual(noise[group], .5)
                fit_template(rest, lift, "index", {"max_score_noise": .51})

    def test_stable_signal_with_geometry_failures_reports_segment_diagnostics(self):
        lift = [stretch(frame(lift=1), scale=1.31 if i % 3 == 0 else 1.25, segment=2)
                for i in range(100)]
        error = self.assertPoseError("lift_geometry_unstable", fit_template,
                                    capture(count=100), lift, "index", {})
        stats = error.diagnostics["phases"]["lift"]
        self.assertEqual(stats["motion_ready_ratio"], 1)
        self.assertEqual(stats["ready_ratio"], .66)
        self.assertAlmostEqual(stats["segments"]["DIP–TIP"]["rejected_ratio"], .34)
        self.assertLess(stats["shape_error"], .30)
        self.assertGreater(stats["shape_error_p95"], .30)
        fit_template(capture(count=100), lift, "index", {"shape_limit": .32})

    def test_common_finger_scale_is_distinct_from_local_shape_distortion(self):
        model = fit_template(capture(), [stretch(frame(lift=1), scale=1.348) for _ in range(45)], "index", {})
        geometry = model["diagnostics"]["phases"]["lift"]
        self.assertAlmostEqual(geometry["common_scale"], 1.348)
        self.assertAlmostEqual(geometry["shape_error"], 0)
        result = assess_pose(stretch(frame(), scale=1.348, segment=2), self.models["index"], {})
        self.assertFalse(result["valid"])
        self.assertIn("local_bone_distortion", result["reasons"])
        self.assertAlmostEqual(result["geometry"]["segment_errors"][2], .348)

    def test_scale_policy_is_used_in_fit_and_assessment(self):
        lift = [stretch(frame(lift=1), scale=1.348) for _ in range(45)]
        self.assertPoseError("lift_geometry_unstable", fit_template, capture(), lift, "index", {"scale_max": 1.3})
        for scale, valid in ((.49, False), (.51, True), (1.79, True), (1.81, False)):
            with self.subTest(scale=scale):
                result = assess_pose(stretch(frame(), scale=scale), self.models["index"], {})
                self.assertEqual(result["geometry"]["valid"], valid)
        result = assess_pose(stretch(frame(), scale=1.348), self.models["index"], {"scale_max": 1.3})
        self.assertIn("extreme_finger_scale", result["reasons"])

    def test_scale_endpoints_are_inclusive(self):
        model = copy.deepcopy(self.models["index"])
        model.update(length_reference=[1., 1., 1.], palm_reference=1.)
        feature = extract_features(frame()["world_landmarks"], "index", frame()["landmarks"])
        feature["palm_width"] = 1.
        for scale in (.5, 1.8):
            feature["lengths"] = np.asarray([scale] * 3)
            with self.subTest(scale=scale), patch.object(pose, "extract_features", return_value=feature):
                self.assertTrue(assess_pose(frame(), model, {})["geometry"]["valid"])

    def test_pose_is_invariant_to_whole_hand_world_motion(self):
        sample = frame(lift=.7)
        original = assess_pose(sample, self.models["index"], {})
        theta = .6
        rotation = np.asarray([[math.cos(theta), -math.sin(theta), 0],
                               [math.sin(theta), math.cos(theta), 0], [0, 0, 1]])
        sample["world_landmarks"] = (np.asarray(sample["world_landmarks"]) @ rotation.T * 2.4 + [5, 7, 9]).tolist()
        result = assess_pose(sample, self.models["index"], {})
        self.assertEqual(result["valid"], original["valid"])
        for group in ("tip", "angles"):
            self.assertAlmostEqual(result[group]["score"], original[group]["score"])
        self.assertAlmostEqual(result["geometry"]["common_scale"], 2.4)
        self.assertAlmostEqual(result["geometry"]["relative_scale"], 1.)

    def test_visibility_preserves_raw_tip_and_is_local_to_target(self):
        outside = frame()
        outside["landmarks"][8][1] = 1.15
        result = assess_pose(outside, self.models["index"], {})
        self.assertFalse(result["valid"])
        self.assertIn("target_out_of_frame", result["reasons"])
        self.assertEqual(result["image_visibility"]["required_outside_ids"], [8])
        self.assertEqual(result["tip_image"][1], 1.15)
        other = assess_pose(outside, self.models["middle"], {})
        self.assertTrue(other["valid"])
        self.assertEqual(other["image_visibility"]["outside_ids"], [8])
        outside = frame()
        outside["landmarks"][0][1] = 1.15
        wrist = assess_pose(outside, self.models["index"], {})
        self.assertTrue(wrist["image_visibility"]["valid"])
        self.assertEqual(wrist["image_visibility"]["outside_ids"], [0])

    def test_out_of_frame_capture_cannot_be_silently_removed(self):
        rest = capture(count=60)
        for sample in rest[:20]:
            sample["landmarks"][8][1] = 1.15
        with self.assertRaises(PoseError) as caught:
            fit_template(rest, capture(lift=1), "index", {})
        diagnostics = caught.exception.diagnostics
        self.assertEqual(diagnostics["rest"]["used_frames"], 60)
        self.assertLess(diagnostics["phases"]["rest"]["visibility_ready_ratio"], .7)
        self.assertIn("rest_out_of_frame", diagnostics["problems"])

    def test_malformed_frames_are_unusable_with_explicit_scores(self):
        bad = [None, [], {}, {"world_landmarks": analytic_hand()},
               {"world_landmarks": [], "landmarks": []},
               {"world_landmarks": [[0., 0., 0.]] * 21, "landmarks": [[.5, .5]] * 21}]
        for key, index, number in (("world_landmarks", 2, float("nan")),
                                   ("landmarks", 1, float("inf")),
                                   ("world_landmarks", 2, 1e308)):
            sample = frame()
            sample[key][8][index] = number
            bad.append(sample)
        for sample in bad:
            with self.subTest(sample=str(sample)[:80]):
                result = assess_pose(sample, self.models["index"], {})
                self.assertFalse(result["valid"])
                self.assertIsNone(result["tip"]["score"])
                self.assertIsNone(result["angles"]["score"])
                json.dumps(result, allow_nan=False)

    def test_release_policy_affects_rest_readiness(self):
        # Majority defines the baseline; modest minority is below default .2.
        rest = capture(count=40) + capture(lift=.08, count=20)
        policy = {"min_snr": .1, "min_separation": .1}
        fit_template(rest, capture(lift=1, count=60), "index", policy)
        with self.assertRaises(PoseError) as caught:
            fit_template(rest, capture(lift=1, count=60), "index", {**policy, "release": 0.})
        self.assertIn("rest_motion_not_separable", caught.exception.diagnostics["problems"])

    def test_validate_rest_returns_diagnostics_only_for_active_fingers(self):
        result = validate_rest(capture(), list(FINGERS), {"policy_version": "fixture-v1"})
        self.assertTrue(result["valid"])
        self.assertEqual(result["policy_version"], "fixture-v1")
        self.assertEqual(set(result["fingers"]), set(FINGERS))
        self.assertNotIn("frames", result)
        self.assertNotIn("length_reference", result)
        for stats in result["fingers"].values():
            self.assertEqual(stats["used_frames"], 45)
            self.assertEqual(stats["quality"]["ready_ratio"], 1.)
            self.assertNotIn("center", stats)
        self.assertIn("CMC–MCP", result["fingers"]["thumb"]["quality"]["segments"])
        json.dumps(result, allow_nan=False)

    def test_validate_rest_requires_samples_and_reports_missing_frames(self):
        error = self.assertPoseError("insufficient_samples", validate_rest,
                                    capture(count=44) + [None], ["index"], {})
        self.assertEqual(error.diagnostics["fingers"]["index"]["rejected_frames"], 1)
        self.assertPoseError("insufficient_samples", validate_rest, [], ["index"], {})

    def test_validate_rest_is_finger_local_for_visibility(self):
        samples = capture()
        for sample in samples:
            sample["landmarks"][8][1] = 1.1
        self.assertTrue(validate_rest(samples, ["middle"], {})["valid"])
        self.assertPoseError("rest_out_of_frame", validate_rest, samples, ["middle", "index"], {})

    def test_validate_rest_checks_geometry_without_a_lift_template(self):
        samples = capture(count=66) + [stretch(frame(), scale=1.5, segment=2) for _ in range(34)]
        error = self.assertPoseError("rest_geometry_unstable", validate_rest, samples, ["index"], {})
        stats = error.diagnostics["fingers"]["index"]["quality"]
        self.assertEqual(stats["geometry_ready_ratio"], .66)
        self.assertGreater(stats["segments"]["DIP–TIP"]["p95_error"], .3)

    def test_policy_and_input_errors_use_pose_error(self):
        policies = [None, {"min_valid_frames": 0}, {"min_valid_frames": True},
                    {"min_valid_frames": 2.5}, {"min_valid_ratio": 1.1},
                    {"min_ready_ratio": 0}, {"min_snr": float("nan")},
                    {"max_score_noise": float("inf")}, {"shape_limit": "bad"},
                    {"scale_min": 2, "scale_max": 1}, {"release": .55},
                    {"min_snr": 10 ** 400}, {"policy_version": []}]
        for policy in policies:
            with self.subTest(policy=str(policy)[:50]):
                self.assertPoseError("invalid_policy", fit_template, capture(), capture(lift=1), "index", policy)
        for finger in ("unsupported", None, []):
            self.assertPoseError("invalid_finger", fit_template, [], [], finger, {})
        for fingers in ([], ["index", "index"], "index"):
            self.assertPoseError("invalid_fingers", validate_rest, [], fingers, {})
        self.assertPoseError("invalid_frames", fit_template, (), capture(lift=1), "index", {})

    def test_invalid_templates_raise_instead_of_becoming_quality_failures(self):
        models = [None, {}, {"algorithm": "different"}]
        for field, value in (("weights", [0.] * 8), ("weights", [-1.] * 8),
                             ("delta", [float("nan")] * 8), ("base", [0.] * 7)):
            model = copy.deepcopy(self.models["index"])
            model["groups"]["tip"][field] = value
            models.append(model)
        model = copy.deepcopy(self.models["index"])
        model["groups"]["tip"]["weights"][3] = 1.
        models.append(model)
        for model in models:
            self.assertPoseError("invalid_template", assess_pose, frame(), model, {})

    def test_public_calls_do_not_mutate_caller_inputs(self):
        rest, lift, policy = capture(), capture(lift=1), {"policy_version": "immutable"}
        before = copy.deepcopy((rest, lift, policy))
        model = fit_template(rest, lift, "index", policy)
        model_before = copy.deepcopy(model)
        assess_pose(rest[0], model, policy)
        validate_rest(rest, ["index"], policy)
        self.assertEqual((rest, lift, policy), before)
        self.assertEqual(model, model_before)


if __name__ == "__main__":
    unittest.main()
