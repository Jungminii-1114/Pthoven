"""착지 순간 짧은 품질 탈락 유예(v6) 회귀 테스트. 합성 손은 수학적 검증용이다."""
import unittest
import numpy as np

from pthoven_tap import calibrate, PoseTapDetector
from pthoven_tap.examples import analytic_hand


def model():
    return calibrate([analytic_hand() for _ in range(12)], [analytic_hand({"index": 1}) for _ in range(12)], "index")


def stretch(points, k):
    p = np.asarray(points, float).copy(); d = (p[6] - p[5]) * (k - 1); p[6:9] += d
    return p.tolist()


def run(glitch_k=None, glitch_frames=1, cycles=5, dt=20, mode="local_baseline"):
    d = PoseTapDetector({"index": model()}, mode=mode); t = 0; events = []
    for _ in range(cycles):
        for _ in range(25):
            t += dt; events += d.update(t, analytic_hand())["events"]
        for lift in [.3, .7, 1, 1, 1, 1, .7, .3]:
            t += dt; events += d.update(t, analytic_hand({"index": lift}))["events"]
        # 착지 직후: 손가락이 기준 자세로 돌아온 첫 프레임들에 랜드마크가 튄다.
        for _ in range(glitch_frames if glitch_k else 0):
            t += dt; events += d.update(t, stretch(analytic_hand(), glitch_k))["events"]
    for _ in range(25):
        t += dt; events += d.update(t, analytic_hand())["events"]
    return events


class LandingHoldTests(unittest.TestCase):
    def test_clean_cycles_baseline(self):
        self.assertEqual(len(run()), 5)

    def test_single_landing_distortion_no_longer_cancels_the_tap(self):
        # 국소 왜곡 약 0.65: v5까지는 사이클이 취소돼 후보가 사라졌다.
        self.assertEqual(len(run(glitch_k=1.65)), 5)

    def test_severe_landing_collapse_still_cancels(self):
        self.assertEqual(len(run(glitch_k=2.2)), 0)

    def test_glitch_longer_than_80ms_still_cancels(self):
        self.assertEqual(len(run(glitch_k=1.65, glitch_frames=6)), 0)

    def test_glitch_during_rest_never_emits(self):
        d = PoseTapDetector({"index": model()}, mode="local_baseline"); events = []
        for i in range(300):
            world = stretch(analytic_hand(), 1.65) if i % 7 == 0 else analytic_hand()
            events += d.update(i * 20, world)["events"]
        self.assertFalse(events)


if __name__ == "__main__":
    unittest.main()
