"""기준 자세 길이 적응(이중 길이 기준) 회귀 테스트.

합성 손은 수학적 검증용이며 사람·카메라 정확도 근거가 아니다.
실제 기록 비교는 docs/2026-11-25-tap-test-log.md를 본다.
"""
import unittest
import numpy as np

from pthoven_tap import calibrate, PoseTapDetector
from pthoven_tap.examples import analytic_hand
from pthoven_tap.rest_length import RestLengthAdapter
from pthoven_tap.service import TapService


def model(finger="index"):
    return calibrate([analytic_hand() for _ in range(12)], [analytic_hand({finger: 1}) for _ in range(12)], finger)


def stretch(points, k, ids=(5, 6)):
    """MCP–PIP 구간만 k배로 늘린다(뒤쪽 관절은 같이 평행 이동)."""
    p = np.asarray(points, float).copy(); d = (p[ids[1]] - p[ids[0]]) * (k - 1); p[ids[1]:ids[1] + 3] += d
    return p.tolist()


def run_cycles(detector, k=1.0, cycles=6, rest_frames=30, lift_frames=10):
    t, events = 0, []
    for _ in range(cycles):
        for _ in range(rest_frames):
            t += 20; events += detector.update(t, stretch(analytic_hand(), k))["events"]
        for _ in range(lift_frames):
            t += 20; events += detector.update(t, stretch(analytic_hand({"index": 1}), k))["events"]
    return events


class RestLengthAdaptationTests(unittest.TestCase):
    def test_default_on_for_local_baseline_and_off_for_fixed(self):
        self.assertTrue(PoseTapDetector({"index": model()}, mode="local_baseline").length_adaptation)
        self.assertFalse(PoseTapDetector({"index": model()}, mode="fixed").length_adaptation)
        self.assertFalse(PoseTapDetector({"index": model()}, mode="local_baseline", length_adaptation=False).length_adaptation)

    def test_drifted_rest_mcp_pip_recovers_taps_that_were_all_lost(self):
        # 실제 capture-6 3번째 시행: 기준 자세 MCP–PIP 1.33배 → 10회 동작에 후보 0개.
        off = run_cycles(PoseTapDetector({"index": model()}, mode="local_baseline", length_adaptation=False), k=1.35)
        on = run_cycles(PoseTapDetector({"index": model()}, mode="local_baseline"), k=1.35)
        self.assertEqual(len(off), 0)
        self.assertEqual(len(on), 5)  # 첫 사이클은 기준 수집으로 쓰여 후보가 되지 않는다.

    def test_no_drift_keeps_previous_candidate_count(self):
        off = run_cycles(PoseTapDetector({"index": model()}, mode="local_baseline", length_adaptation=False))
        on = run_cycles(PoseTapDetector({"index": model()}, mode="local_baseline"))
        self.assertEqual(len(on), len(off))

    def test_drifted_static_noisy_hand_never_emits(self):
        rng = np.random.default_rng(7); d = PoseTapDetector({"index": model()}, mode="local_baseline"); events = []
        for i in range(400):
            world = (np.asarray(stretch(analytic_hand(), 1.35)) + rng.normal(0, .002, (21, 3))).tolist()
            events += d.update(i * 20, world)["events"]
        self.assertFalse(events)

    def test_adapter_does_not_learn_from_lift_or_unrelated_phases(self):
        m = model(); a = RestLengthAdapter(m)
        d = PoseTapDetector({"index": m}, mode="local_baseline", length_adaptation=False)
        from pthoven_tap.geometry import extract_features
        from pthoven_tap.calibration import measure
        for i in range(20):
            f = extract_features(stretch(analytic_hand(), 1.35), "index"); a.observe(i * 20, f, measure(f, m), "LIFTED")
        self.assertIsNone(a.ratios)
        for i in range(20, 40):
            f = extract_features(stretch(analytic_hand(), 1.35), "index"); a.observe(i * 20, f, measure(f, m), "REST")
        self.assertIsNotNone(a.ratios); self.assertGreater(a.ratios[0], 1.2)

    def test_extreme_collapse_is_not_learned_and_still_rejected(self):
        # MCP–PIP 2.5배 붕괴(원래 기준 왜곡 150%)는 기준 자세로 수집하지 않고 보류를 유지한다.
        d = PoseTapDetector({"index": model()}, mode="local_baseline"); last = None
        for i in range(60):
            last = d.update(i * 20, stretch(analytic_hand(), 2.5))
        self.assertIsNone(last["signals"]["index"]["rest_length"]["adapted_ratios"])
        self.assertFalse(last["signals"]["index"]["valid"])
        self.assertIn("local_bone_distortion", last["signals"]["index"]["geometry"]["reasons"])

    def test_adaptation_ratio_is_clamped(self):
        d = PoseTapDetector({"index": model()}, mode="local_baseline"); last = None
        for i in range(60):
            last = d.update(i * 20, stretch(analytic_hand(), 1.75))
        ratios = last["signals"]["index"]["rest_length"]["adapted_ratios"]
        self.assertIsNotNone(ratios); self.assertAlmostEqual(max(ratios), 1.6)

    def test_diagnostics_report_reference_source(self):
        d = PoseTapDetector({"index": model()}, mode="local_baseline"); sources = set()
        for i in range(60):
            sources.add(d.update(i * 20, stretch(analytic_hand(), 1.35))["signals"]["index"]["rest_length"]["source"])
        self.assertEqual(sources, {"calibration", "rest_adapted"})

    def test_service_option_is_explicit_and_validated(self):
        s = TapService()
        on = s.create_session({"calibration": {"index": model()}, "mode": "local_baseline"})
        off = s.create_session({"calibration": {"index": model()}, "mode": "local_baseline", "length_adaptation": False})
        self.assertTrue(on["length_adaptation"]); self.assertFalse(off["length_adaptation"])
        with self.assertRaises(ValueError):
            s.create_session({"calibration": {"index": model()}, "mode": "local_baseline", "length_adaptation": "yes"})


if __name__ == "__main__":
    unittest.main()
