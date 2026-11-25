import copy
import json
import unittest
import numpy as np

from pthoven_tap import calibrate, PoseTapDetector, length_quality, extract_features, CalibrationError
from pthoven_tap.detector import FingerMachine, TapOptions, validate_model
from pthoven_tap.examples import analytic_hand, analytic_cycle
from pthoven_tap.service import TapService
from pthoven_tap.diagnostics import summarize_outputs
from pthoven_tap.local_baseline import LocalBaselineMachine
from pthoven_tap.temporal import CausalFeatureMedian


def model(finger="index"):
    return calibrate([analytic_hand() for _ in range(12)], [analytic_hand({finger: 1}) for _ in range(12)], finger)


def signal(tip, angle):
    return {"valid": True, "tip": {"score": tip}, "angles": {"score": angle}, "tip_vector": [tip, 0.], "confidence": .8}


class GeometryTests(unittest.TestCase):
    def test_offscreen_wrist_does_not_discard_visible_target_finger(self):
        world = analytic_hand(); image = [{"x": .5+p[0]*.12, "y": .6-p[1]*.12} for p in world]
        image[0]["y"] = 1.15
        f = extract_features(world, "index", image)
        self.assertTrue(f["image_visibility"]["valid"])
        self.assertEqual(f["image_visibility"]["outside_ids"], [0])
        self.assertTrue(np.isfinite(f["vector"]).all())
        image[8]["y"] = 1.1
        f = extract_features(world, "index", image)
        self.assertFalse(f["image_visibility"]["valid"])
        self.assertEqual(f["image_visibility"]["required_outside_ids"], [8])
    def test_normal_bending_changes_reach_but_not_bone_lengths(self):
        a = extract_features(analytic_hand(), "index"); b = extract_features(analytic_hand({"index": 1}), "index")
        self.assertNotAlmostEqual(a["reach"], b["reach"])
        np.testing.assert_allclose(a["lengths"], b["lengths"], rtol=1e-12)
        self.assertTrue(length_quality(b["lengths"], a["lengths"], b["palm_width"], a["palm_width"])["valid"])

    def test_common_34_8_percent_scale_is_not_local_bone_distortion(self):
        r = length_quality([1.348, .674, .337], [1, .5, .25], 1, 1)
        self.assertTrue(r["valid"]); self.assertAlmostEqual(r["common_scale"], 1.348)
        self.assertAlmostEqual(r["shape_error"], 0); self.assertAlmostEqual(r["raw_error"], .348)

    def test_single_segment_34_8_percent_distortion_is_still_rejected(self):
        r = length_quality([1, .5, .337], [1, .5, .25], 1, 1)
        self.assertFalse(r["valid"]); self.assertIn("local_bone_distortion", r["reasons"])
        np.testing.assert_allclose(r["segment_errors"], [0, 0, .348])

    def test_global_hand_scale_is_separate_from_finger_scale(self):
        self.assertTrue(length_quality([3, 1.5, .75], [1, .5, .25], 3, 1)["valid"])
        self.assertFalse(length_quality([3, 1.5, .75], [1, .5, .25], 1, 1)["valid"])

    def test_missing_and_nonfinite_landmarks_never_form_features(self):
        for p in [None, [], [[0, 0, 0]] * 21]:
            with self.subTest(p=str(p)[:20]), self.assertRaises((ValueError, TypeError)):
                extract_features(p, "index")
        p = analytic_hand(); p[8][2] = float("nan")
        with self.assertRaises(ValueError): extract_features(p, "index")

    def test_features_invariant_to_global_translation_rotation_and_scale(self):
        p = np.asarray(analytic_hand({"index": .7})); theta = .6
        rotation = np.asarray([[np.cos(theta), -np.sin(theta), 0], [np.sin(theta), np.cos(theta), 0], [0, 0, 1]])
        q = (p @ rotation.T * 2.4 + [5, 7, 9]).tolist()
        np.testing.assert_allclose(extract_features(p.tolist(), "index")["vector"], extract_features(q, "index")["vector"], atol=1e-12)


class CalibrationTests(unittest.TestCase):
    def test_calibration_has_distinct_tip_and_angle_groups(self):
        m = model(); self.assertEqual(m["algorithm"], "python_tip_angles_v1")
        for group in ["tip", "angles"]:
            self.assertTrue(any(m["groups"][group]["weights"]))
        validate_model(json.loads(json.dumps(m)))

    def test_static_identical_pose_does_not_force_calibration(self):
        with self.assertRaises(CalibrationError) as e:
            calibrate([analytic_hand()] * 12, [analytic_hand()] * 12, "index")
        self.assertIn("unobservable", e.exception.code)

    def test_minimum_valid_frames_and_rejected_frames(self):
        with self.assertRaises(CalibrationError): calibrate([analytic_hand()] * 3, [analytic_hand({"index": 1})] * 12, "index")
        with self.assertRaises(CalibrationError): calibrate([None] * 8 + [analytic_hand()] * 6, [analytic_hand({"index": 1})] * 12, "index")

    def test_lift_common_scale_can_pass_but_local_distortion_cannot(self):
        rest = analytic_hand(); lift = analytic_hand({"index": 1}); origin = np.asarray(lift[5])
        scaled = copy.deepcopy(lift)
        for i in [6, 7, 8]: scaled[i] = (origin + (np.asarray(lift[i]) - origin) * 1.348).tolist()
        m = calibrate([rest] * 16, [scaled] * 16, "index")
        d = m["diagnostics"]["phases"]["lift"]
        self.assertAlmostEqual(d["old_normalized_length_error"], .348); self.assertEqual(d["shape_rejected_ratio"], 0)
        distorted = copy.deepcopy(lift); distorted[8] = (np.asarray(lift[7]) + (np.asarray(lift[8]) - lift[7]) * 1.8).tolist()
        with self.assertRaises(CalibrationError): calibrate([rest] * 16, [distorted] * 16, "index")

    def test_legacy_raw_diagnostic_frames_are_supported(self):
        m = calibrate([{"world_landmarks": analytic_hand()}] * 12, [analytic_hand({"index": 1})] * 12, "index")
        self.assertTrue(m["groups"]["tip"]["weights"][2])

    def test_stable_motion_with_34_percent_length_rejection_is_labelled_geometry(self):
        lift = []
        for i in range(100):
            p = analytic_hand({"index": 1})
            p[8] = (np.asarray(p[7]) + (np.asarray(p[8])-p[7]) * (1.31 if i % 3 == 0 else 1.25)).tolist()
            lift.append(p)
        with self.assertRaises(CalibrationError) as caught:
            calibrate([analytic_hand()] * 100, lift, "index")
        self.assertEqual(caught.exception.code, "lift_geometry_unstable")
        phase = caught.exception.diagnostics["phases"]["lift"]
        self.assertEqual(phase["motion_ready_ratio"], 1)
        self.assertAlmostEqual(phase["ready_ratio"], .66)
        self.assertAlmostEqual(phase["geometry_ready_ratio"], .66)
        self.assertLess(phase["shape_error"], .3)
        self.assertGreater(phase["shape_error_p95"], .3)
        self.assertAlmostEqual(phase["segments"]["DIP–TIP"]["rejected_ratio"], .34)

    def test_2d_tip_tracks_can_be_selected_when_world_tip_is_noisy(self):
        rest, lift = [], []
        for i in range(24):
            a, b = analytic_hand(), analytic_hand({"index": 1})
            for world, target in [(a, rest), (b, lift)]:
                image = [{"x": .5 + p[0] * .12, "y": .6 - p[1] * .12} for p in world]
                target.append({"world_landmarks": world, "landmarks": image})
        m = calibrate(rest, lift, "index")
        self.assertTrue(any(m["groups"]["tip"]["weights"][6:]))


class StateTests(unittest.TestCase):
    def feed(self, rows):
        m = FingerMachine(); events = []
        for i, (tip, angle) in enumerate(rows):
            e, _ = m.update(i * 40, signal(tip, angle))
            if e: events.append(e)
        return events

    def test_static_slide_or_angle_only_cannot_emit_fused_candidate(self):
        cycle = [0, 0, 1, 1, 1, 0, 0, 0]
        self.assertFalse(self.feed([(x, 0) for x in cycle]))
        self.assertFalse(self.feed([(0, x) for x in cycle]))
        self.assertFalse(self.feed([(0, 0)] * 80))

    def test_joint_tip_cycle_emits_once_with_temporal_evidence(self):
        e = self.feed([(x, x) for x in [0, 0, 1, 1, 1, 0, 0, 0]])
        self.assertEqual(len(e), 1); self.assertGreater(e[0]["tip_path_normalized"], 1)
        self.assertGreater(e[0]["tip_return_speed"], 0); self.assertFalse(e[0]["contact_verified"])

    def test_single_frame_spike_is_not_a_tap(self):
        self.assertFalse(self.feed([(x, x) for x in [0, 0, 1, 0, 0, 0]]))

    def test_excursion_uses_observed_rest_trough_instead_of_last_release_boundary(self):
        events = self.feed([(x,x) for x in [0, .1, .19, .6, .6, .6, .19, .19, .19]])
        self.assertEqual(len(events),1)
        self.assertAlmostEqual(events[0]['tip_excursion'],.6)

    def test_tracking_gap_requires_new_rest_lift_return(self):
        m = FingerMachine()
        for t, x in [(0, 0), (40, 0), (80, 1), (120, 1)]: m.update(t, signal(x, x))
        m.update(500, signal(0, 0)); e, _ = m.update(540, signal(0, 0)); self.assertIsNone(e)
        for t, x in [(580, 1), (620, 1), (660, 0), (700, 0)]: e, _ = m.update(t, signal(x, x))
        self.assertIsNotNone(e)

    def test_invalid_observation_or_timestamp_cannot_advance_cycle(self):
        m = FingerMachine(); m.update(0, signal(0, 0)); m.update(40, {"valid": False})
        with self.assertRaises(ValueError): m.update(40, signal(1, 1))
        with self.assertRaises(ValueError): TapOptions(release=.8)


class ReplayTests(unittest.TestCase):
    def test_full_python_cycle_repeat_and_neighbour_fingers(self):
        calibration = {f: model(f) for f in ["index", "middle", "ring"]}; d = PoseTapDetector(calibration)
        frames = analytic_cycle() + analytic_cycle(start=600) + analytic_cycle("ring", 1200)
        events = [e for f in frames for e in d.update(f["t_ms"], f["world_landmarks"])["events"]]
        self.assertEqual([e["finger"] for e in events], ["index", "index", "ring"])

    def test_global_hand_motion_does_not_emit(self):
        d = PoseTapDetector({"index": model()}); events = []
        for i in range(80):
            p = (np.asarray(analytic_hand()) + [i * .01, 0, np.sin(i * .1)]).tolist()
            events.extend(d.update(i * 40, p)["events"])
        self.assertFalse(events)

    def test_identical_pose_air_tap_is_an_explicit_unresolved_case(self):
        models = {"index": model()}
        def run():
            d = PoseTapDetector(models)
            return [e for f in analytic_cycle() for e in d.update(f["t_ms"], f["world_landmarks"])["events"]]
        self.assertEqual(run(), run()); self.assertEqual(len(run()), 1)

    def test_model_version_and_no_nan_round_trip(self):
        m = model(); json.dumps(m, allow_nan=False)
        with self.assertRaises(ValueError): PoseTapDetector({"index": {"algorithm": "javascript_v4"}})


class LiveDiagnosticsTests(unittest.TestCase):
    def test_lift_and_rest_mismatch_is_visible_without_counting_a_tap(self):
        m = FingerMachine()
        _, s = m.update(0, signal(.3, .1))
        self.assertEqual(s["decision"]["code"], "waiting_for_rest")
        m.update(40, signal(0, 0))
        event, s = m.update(80, signal(.8, .3))
        self.assertIsNone(event)
        self.assertIn("angles_below_lift", s["decision"]["blockers"])
        self.assertEqual(s["phase"], "REST")

    def test_quality_reset_reason_remains_visible_after_valid_frames_resume(self):
        m = FingerMachine(); m.update(0, signal(0, 0)); m.update(40, signal(1, 1))
        bad = {"valid": False, "geometry": {"reasons": ["local_bone_distortion"]},
               "tip": {"valid": True, "score": 1}, "angles": {"valid": True, "score": 1}}
        event, s = m.update(80, bad)
        self.assertIsNone(event); self.assertEqual(s["decision"]["code"], "local_bone_distortion")
        _, s = m.update(120, signal(0, 0))
        self.assertEqual(s["decision"]["interruption_counts"], {"local_bone_distortion": 1})
        self.assertEqual(s["decision"]["last_interruption"]["t_ms"], 80)
        self.assertEqual(s["decision"]["last_interruption"]["phase"], "LIFTED")

    def test_fast_return_is_reported_instead_of_silently_lost(self):
        m = FingerMachine(); m.update(0, signal(0, 0)); m.update(20, signal(1, 1))
        event, s = m.update(40, signal(0, 0))
        self.assertIsNone(event); self.assertEqual(s["decision"]["code"], "lift_too_short")

    def test_return_threshold_mismatch_and_settling_are_reported(self):
        m = FingerMachine(); m.update(0, signal(0, 0)); m.update(40, signal(1, 1))
        _, s = m.update(120, signal(0, .3))
        self.assertIn("angles_above_release", s["decision"]["blockers"])
        m.update(160, signal(0, 0))
        _, s = m.update(180, signal(0, 0))
        self.assertEqual(s["decision"]["code"], "settling")
        event, s = m.update(200, signal(0, 0))
        self.assertIsNotNone(event); self.assertEqual(s["decision"]["code"], "candidate_emitted")
        self.assertGreater(s["decision"]["metrics"]["tip_path"], 1)

    def test_summary_counts_real_observed_interruptions_once_without_accuracy(self):
        d = PoseTapDetector({"index": model()}); cycle = analytic_cycle()
        cycle[5]["world_landmarks"] = None
        outputs = [d.update(f["t_ms"], f["world_landmarks"]) for f in cycle]
        summary = summarize_outputs(outputs)
        self.assertFalse(summary["ground_truth_evaluated"])
        row = summary["fingers"]["index"]
        self.assertEqual(row["candidate_count"], 0)
        self.assertEqual(row["cycle_interruptions"], {"landmarks_unavailable": 1})
        self.assertNotIn("accuracy", summary)
        json.dumps(outputs, allow_nan=False)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.api = TapService(); self.session = self.api.create_session({"calibration": {"index": model()}})["session_id"]

    def test_actual_python_pipeline_and_close(self):
        r = self.api.frames({"session_id": self.session, "sequence": 1, "frames": analytic_cycle()})
        self.assertEqual(sum(len(f["events"]) for f in r["frames"]), 1)
        self.api.close({"session_id": self.session})
        with self.assertRaises(ValueError): self.api.frames({"session_id": self.session, "sequence": 2, "frames": analytic_cycle()})

    def test_invalid_batch_is_atomic_and_duplicate_request_rejected(self):
        frames = analytic_cycle(); bad = [frames[0], frames[0]]
        with self.assertRaises(ValueError): self.api.frames({"session_id": self.session, "sequence": 1, "frames": bad})
        self.api.frames({"session_id": self.session, "sequence": 1, "frames": frames})
        with self.assertRaises(ValueError): self.api.frames({"session_id": self.session, "sequence": 1, "frames": frames})

    def test_diagnostic_failure_is_returned_without_fabricating_model(self):
        r = self.api.calibrate({"rest_frames": [analytic_hand()] * 12, "lift_frames": [analytic_hand()] * 12, "finger": "index"})
        self.assertFalse(r["ok"]); self.assertNotIn("model", r)

    def test_experimental_mode_is_explicit_and_unknown_mode_is_rejected(self):
        s = self.api.create_session({"calibration": {"index": model()}, "mode": "local_baseline"})
        self.assertEqual(s["mode"], "local_baseline")
        with self.assertRaises(ValueError): self.api.create_session({"mode": "unrecognised"})


class LocalBaselineTests(unittest.TestCase):
    def test_known_reference_rearms_after_fresh_confirmation_without_rebasing(self):
        m=self.ready_machine();reference=m.reference.copy()
        m.update(260,{'valid':False})
        for t in [280,300]:
            event,state=m.update(t,signal(.1,.4));self.assertIsNone(event);self.assertFalse(state['decision']['ready'])
        event,state=m.update(320,signal(.1,.4))
        self.assertIsNone(event);self.assertTrue(state['decision']['ready'])
        self.assertTrue(state['decision']['reference']['reused']);self.assertEqual(state['decision']['reference']['reuses'],1)
        np.testing.assert_allclose(m.reference,reference)

    def test_invalid_input_breaks_known_reference_confirmation(self):
        m=self.ready_machine();m.update(260,{'valid':False});m.update(280,signal(.1,.4));m.update(300,signal(.1,.4));m.update(310,{'valid':False})
        for t in [320,340]:
            event,state=m.update(t,signal(.1,.4));self.assertIsNone(event);self.assertFalse(state['decision']['ready'])
        event,state=m.update(360,signal(.1,.4));self.assertIsNone(event);self.assertTrue(state['decision']['ready'])

    def test_known_reference_cannot_be_reused_when_only_one_signal_matches(self):
        for tip,angle in [(1.1,.4),(.1,1.4)]:
            m=self.ready_machine();m.update(260,{'valid':False})
            for i in range(3):event,state=m.update(280+i*20,signal(tip,angle))
            self.assertIsNone(event);self.assertEqual(state['decision']['reference']['reuses'],0)
    def test_held_lift_cannot_become_initial_neutral_reference(self):
        m=LocalBaselineMachine()
        for i in range(20):
            event,state=m.update(i*40,signal(1,1.2))
            self.assertIsNone(event);self.assertIsNone(m.reference)
        for i in range(7):event,state=m.update(800+i*40,signal(.2,.3))
        self.assertTrue(state['decision']['ready']);self.assertIsNone(event)

    def test_acquisition_skips_soft_failure_without_counting_or_collecting_its_coordinates(self):
        m=LocalBaselineMachine()
        for t in [0,40,80]:m.update(t,signal(.1,.4))
        event,state=m.update(120,self.soft_bad())
        self.assertIsNone(event);self.assertEqual(state['decision']['code'],'neutral_quality_skip')
        for t in [160,200,240,280]:event,state=m.update(t,signal(.1,.4))
        self.assertIsNone(event);self.assertTrue(state['decision']['ready'])
        np.testing.assert_allclose(m.reference,[.1,.4])

    def test_single_angle_outlier_does_not_destroy_otherwise_stable_neutral_window(self):
        m=LocalBaselineMachine()
        for i,angle in enumerate([.4,.4,1.2,.4,.4,.4]):event,state=m.update(i*40,signal(.1,angle))
        self.assertIsNone(event);self.assertTrue(m.machine.ready)
        np.testing.assert_allclose(m.reference,[.1,.4])
    @staticmethod
    def soft_bad():
        return {"valid": False, "geometry": {"shape_error": .35, "relative_scale": 1, "reasons": ["local_bone_distortion"]},
                "tip": {"valid": True, "score": .9, "residual": .1}, "angles": {"valid": True, "score": .9, "residual": .1}}

    def ready_machine(self):
        m = LocalBaselineMachine()
        for t in range(0, 241, 40): m.update(t, signal(.1, .4))
        self.assertIsNotNone(m.reference)
        return m

    def test_static_posture_and_tip_or_angle_alone_never_emit(self):
        for kind in ['static', 'tip_only', 'angle_only']:
            m = self.ready_machine(); events = []
            for i, x in enumerate([0, .3, 1, 1, 1, .3, 0, 0, 0]):
                event, _ = m.update(280+i*40, signal(.1+(x if kind=='tip_only' else 0), .4+(x if kind=='angle_only' else 0)))
                if event: events.append(event)
            self.assertFalse(events)

    def test_offset_pose_can_complete_joint_tip_cycle_without_fixed_zero(self):
        m = self.ready_machine(); events = []
        for i, x in enumerate([0, .3, 1, 1, 1, .3, 0, 0, 0]):
            event, _ = m.update(280+i*40, signal(.1+x, .4+x))
            if event: events.append(event)
        self.assertEqual(len(events), 1)

    def test_stale_lower_reference_recovers_only_after_fresh_stable_pose(self):
        m = self.ready_machine()
        m.update(260, {'valid': False})
        m.reference = np.asarray([-.14, .43])
        # 실제 기록의 REST/ready=False/고정된 낮은 기준 상태.
        events = []
        for t in [280, 320, 360, 400]:
            event, state = m.update(t, signal(.30, .60))
            self.assertIsNone(event)
            self.assertFalse(state['decision']['ready'])
            self.assertEqual(state['decision']['code'], 'waiting_for_neutral')
        event, state = m.update(440, signal(.30, .60))
        self.assertIsNone(event); self.assertTrue(state['decision']['ready'])
        self.assertTrue(state['decision']['reference']['reacquired'])
        self.assertEqual(state['decision']['reference']['recoveries'], 1)
        np.testing.assert_allclose(m.reference, [.30, .60])
        for i, x in enumerate([0, .3, 1, 1, 1, .3, 0, 0, 0]):
            event, _ = m.update(480+i*40, signal(.30+x, .60+x))
            if event: events.append(event)
        self.assertEqual(len(events), 1)

    def test_recovery_discards_interrupted_cycle_and_never_counts_first_descent(self):
        m = self.ready_machine(); m.update(280, signal(1.1, 1.4))
        m.update(320, {'valid': False})
        events = []
        for i, x in enumerate([1, .8, .5, .2, 0, 0, 0, 0, 0, 0, 0]):
            event, _ = m.update(360+i*40, signal(.3+x, .6+x))
            if event: events.append(event)
        self.assertFalse(events)

        self.assertTrue(m.machine.ready)

    def test_reference_does_not_move_during_active_lift(self):
        m = self.ready_machine(); reference = m.reference.copy()
        for t in range(280, 841, 40):
            event, _ = m.update(t, signal(1.1, 1.4))
            self.assertIsNone(event)
            np.testing.assert_allclose(m.reference, reference)
        self.assertEqual(m.machine.phase, 'LIFTED')

    def test_unstable_recovery_frames_do_not_rearm_or_emit(self):
        m = self.ready_machine(); m.machine.reset()
        for i in range(30):
            event, state = m.update(280+i*40, signal(.3+(i%2)*1.0, .6+(i%2)*1.0))
            self.assertIsNone(event)
            self.assertFalse(state['decision']['ready'])

    def test_full_landmark_pipeline_recovers_after_loss_at_higher_neutral_pose(self):
        d = PoseTapDetector({'index': model()}, mode='local_baseline')
        levels = [0]*10 + [None] + [.4]*10 + [.4, .7, 1.4, 1.4, 1.4, .7, .4, .4, .4]
        rows = [d.update(i*40, None if level is None else analytic_hand({'index': level})) for i, level in enumerate(levels)]
        events = [event for row in rows for event in row['events']]
        self.assertEqual(len(events), 1)
        self.assertGreaterEqual(rows[-1]['signals']['index']['decision']['reference']['recoveries'], 1)
        self.assertTrue(all(not row['events'] for row in rows[:21]))

    def test_short_quality_dropout_is_held_but_cannot_emit_during_invalid_input(self):
        m = self.ready_machine(); m.update(280, signal(1, 1.4)); m.update(320, signal(1, 1.4))
        event, s = m.update(340, self.soft_bad())
        self.assertIsNone(event); self.assertEqual(s['decision']['code'], 'quality_hold')
        for t, x in [(360, 1), (400, 1), (440, 0), (480, 0)]: event, _ = m.update(t, signal(.1+x, .4+x))
        self.assertIsNotNone(event); self.assertEqual(event['quality_hold_frames'], 1)

    def test_long_dropout_and_real_tracking_loss_still_cancel_cycle(self):
        for kind in ['tracking_loss', 'long_quality']:
            m = self.ready_machine(); m.update(280, signal(1, 1.4))
            if kind == 'tracking_loss': m.update(320, {"valid": False})
            else:
                for t in [320, 360, 400]: m.update(t, self.soft_bad())
            events = []
            for t in [440, 480, 520]:
                event, _ = m.update(t, signal(.1, .4))
                if event: events.append(event)
            self.assertFalse(events)

    def test_quality_hold_does_not_serve_as_return_settle_time(self):
        m = self.ready_machine(); m.update(280, signal(1, 1.4)); m.update(360, signal(1, 1.4)); m.update(400, signal(.1, .4))
        m.update(420, self.soft_bad())
        event, _ = m.update(440, signal(.1, .4)); self.assertIsNone(event)
        event, _ = m.update(480, signal(.1, .4)); self.assertIsNotNone(event)

    def test_hand_translation_and_other_finger_motion_do_not_emit(self):
        m = model(); events = []
        for scenario in ['translation', 'neighbor']:
            d = PoseTapDetector({'index': m}, mode='local_baseline')
            for i in range(140):
                p = np.asarray(analytic_hand({'ring': (np.sin(i*.12)+1)/2 if scenario=='neighbor' else 0}))
                if scenario == 'translation': p += [i*.01, 0, np.sin(i*.1)]
                events.extend(d.update(i*40, p.tolist())['events'])
        self.assertFalse(events)


class TemporalFeatureTests(unittest.TestCase):
    def test_severe_current_geometry_failure_is_not_hidden_by_earlier_valid_frames(self):
        d=PoseTapDetector({'index':model()},mode='local_baseline')
        for i in range(20):d.update(i*20,analytic_hand())
        for i in range(20,28):d.update(i*20,analytic_hand({'index':1}))
        bad=analytic_hand({'index':1});bad[8]=(np.asarray(bad[7])+(np.asarray(bad[8])-bad[7])*3).tolist()
        r=d.update(560,bad)
        self.assertFalse(r['signals']['index']['valid']);self.assertFalse(r['events'])
        self.assertNotIn('feature_filter',r['signals']['index'])
        for i in range(29,45):self.assertFalse(d.update(i*20,analytic_hand())['events'])
    def test_past_only_median_rejects_single_feature_jump_and_reports_age(self):
        f=CausalFeatureMedian();rest=extract_features(analytic_hand(),'index');lift=extract_features(analytic_hand({'index':1}),'index')
        for i in range(5):filtered,meta=f.update(i*20,rest)
        filtered,meta=f.update(100,lift)
        np.testing.assert_allclose(filtered['vector'],rest['vector'])
        self.assertEqual(meta['frames'],5);self.assertEqual(meta['age_ms'],40)
        for t in [120,140]:filtered,meta=f.update(t,lift)
        np.testing.assert_allclose(filtered['vector'],lift['vector'])

    def test_long_gap_or_feature_mask_change_does_not_reuse_old_features(self):
        f=CausalFeatureMedian();rest=extract_features(analytic_hand(),'index');f.update(0,rest)
        newer=extract_features(analytic_hand({'index':1}),'index');filtered,meta=f.update(500,newer)
        self.assertEqual(meta['frames'],1);np.testing.assert_allclose(filtered['vector'],newer['vector'])
        image=[{'x':.5+p[0]*.12,'y':.6-p[1]*.12} for p in analytic_hand()]
        filtered,meta=f.update(520,extract_features(analytic_hand(),'index',image))
        self.assertEqual(meta['frames'],1)

    def test_noisy_static_hand_has_no_candidates_in_full_local_pipeline(self):
        rng=np.random.default_rng(117);d=PoseTapDetector({'index':model()},mode='local_baseline');events=[]
        for i in range(400):
            world=(np.asarray(analytic_hand())+rng.normal(0,.002,(21,3))).tolist()
            events.extend(d.update(i*20,world)['events'])
        self.assertFalse(events)



if __name__ == "__main__":
    unittest.main()
