"""보정 품질을 판정기와 같은 인과적 중앙값 필터로 평가하는 변경의 회귀 테스트.

합성 손은 수학적 검증용이다. 실제 약지 보정 비교는 docs/2026-11-25-tap-test-log.md를 본다.
"""
import unittest
import numpy as np

from pthoven_tap import calibrate, CalibrationError, PoseTapDetector
from pthoven_tap.examples import analytic_hand


def frames(lift, noise, seed, timed=True, n=97, finger="ring"):
    rng = np.random.default_rng(seed); out = []
    for i in range(n):
        world = (np.asarray(analytic_hand({finger: lift})) + rng.normal(0, noise, (21, 3))).tolist()
        out.append({"t_ms": i * 16.7, "world_landmarks": world} if timed else {"world_landmarks": world})
    return out


class CalibrationQualityFilterTests(unittest.TestCase):
    def test_small_lift_with_frame_jitter_passes_only_with_runtime_filter(self):
        # 들림 폭이 작고 프레임 떨림이 큰 손가락: 원본 프레임 기준으로는 기준 동작 신호가 70% 미만.
        with self.assertRaises(CalibrationError) as legacy:
            calibrate(frames(0, .01, 1, timed=False), frames(.2, .01, 2, timed=False), "ring")
        self.assertEqual(legacy.exception.code, "rest_motion_not_separable")
        self.assertEqual(legacy.exception.diagnostics["phases"]["rest"]["quality_filter"], "none")
        m = calibrate(frames(0, .01, 1), frames(.2, .01, 2), "ring")
        rest = m["diagnostics"]["phases"]["rest"]
        self.assertEqual(rest["quality_filter"], "causal_feature_median")
        self.assertLess(rest["raw_ready_ratio"], .7); self.assertGreaterEqual(rest["ready_ratio"], .7)

    def test_filter_does_not_change_model_weights(self):
        timed = calibrate(frames(0, .004, 1), frames(.5, .004, 2), "ring")
        legacy = calibrate(frames(0, .004, 1, timed=False), frames(.5, .004, 2, timed=False), "ring")
        for g in ("tip", "angles"):
            np.testing.assert_allclose(timed["groups"][g]["weights"], legacy["groups"][g]["weights"])
            np.testing.assert_allclose(timed["groups"][g]["delta"], legacy["groups"][g]["delta"])

    def test_unobservable_lift_is_still_rejected(self):
        with self.assertRaises(CalibrationError) as e:
            calibrate(frames(0, .012, 1), frames(.1, .012, 2), "ring")
        self.assertTrue(e.exception.code.endswith("_unobservable"))

    def test_non_monotonic_times_fall_back_to_raw_quality(self):
        rest = frames(0, .004, 1); rest[10]["t_ms"] = rest[9]["t_ms"]
        m = calibrate(rest, frames(.5, .004, 2), "ring")
        self.assertEqual(m["diagnostics"]["phases"]["rest"]["quality_filter"], "none")
        self.assertEqual(m["diagnostics"]["phases"]["lift"]["quality_filter"], "causal_feature_median")

    def test_small_lift_model_counts_cycles_and_ignores_static_jitter(self):
        m = calibrate(frames(0, .01, 1), frames(.2, .01, 2), "ring")
        rng = np.random.default_rng(3); d = PoseTapDetector({"ring": m}, mode="local_baseline"); events = []; t = 0
        for _ in range(6):
            for lift, n in [(0, 30), (.2, 12)]:
                for _ in range(n):
                    t += 17; events += d.update(t, (np.asarray(analytic_hand({"ring": lift})) + rng.normal(0, .003, (21, 3))).tolist())["events"]
        self.assertEqual(len(events), 5)
        d = PoseTapDetector({"ring": m}, mode="local_baseline"); static = []
        for i in range(600):
            static += d.update(i * 17, (np.asarray(analytic_hand()) + rng.normal(0, .01, (21, 3))).tolist())["events"]
        self.assertFalse(static)


if __name__ == "__main__":
    unittest.main()
