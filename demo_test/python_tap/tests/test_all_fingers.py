"""Five-finger logic checks only: analytic chains are not real camera accuracy data."""
import unittest
from collections import Counter
import numpy as np

from pthoven_tap import FINGERS, calibrate, PoseTapDetector
from pthoven_tap.examples import analytic_hand


class AllFingerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = {finger: calibrate([analytic_hand()]*12, [analytic_hand({finger: 1})]*12, finger)
                      for finger in FINGERS}

    @staticmethod
    def cycle(finger):
        return [{finger: level} for level in [.2, .6, 1, 1, 1, .6, .2, 0, 0, 0]]

    def replay(self, poses):
        detector = PoseTapDetector(self.models, mode='local_baseline')
        return [detector.update(i*40, None if pose is None else analytic_hand(pose))
                for i, pose in enumerate(poses)]

    def test_each_finger_calibrates_and_repeats_with_independent_models(self):
        for finger in FINGERS:
            with self.subTest(finger=finger):
                self.assertEqual(self.models[finger]['finger'], finger)
                for group in ['tip', 'angles']:
                    self.assertTrue(any(self.models[finger]['groups'][group]['weights']))
                rows = self.replay([{}]*12+self.cycle(finger)+[{}]*5+self.cycle(finger)+[{}]*8)
                self.assertEqual([e['finger'] for row in rows for e in row['events']], [finger, finger])

    def test_simultaneous_five_finger_cycle_is_not_collapsed_to_one_event(self):
        poses = [{finger: level for finger in FINGERS} for level in [.2, .6, 1, 1, 1, .6, .2, 0, 0, 0]]
        rows = self.replay([{}]*12+poses+[{}]*8)
        counts = Counter(e['finger'] for row in rows for e in row['events'])
        self.assertEqual(counts, Counter({finger: 1 for finger in FINGERS}))

    def test_each_finger_recovers_after_loss_then_requires_a_new_cycle(self):
        for finger in FINGERS:
            with self.subTest(finger=finger):
                rows = self.replay([{}]*12+[None]+[{}]*4+self.cycle(finger)+[{}]*8)
                self.assertTrue(all(not row['events'] for row in rows[:17]))
                self.assertEqual([e['finger'] for row in rows for e in row['events']], [finger])
                self.assertGreaterEqual(rows[-1]['signals'][finger]['decision']['reference']['reuses'], 1)

    def test_static_and_whole_hand_translation_do_not_emit_any_finger(self):
        for moving in [False, True]:
            with self.subTest(moving=moving):
                detector = PoseTapDetector(self.models, mode='local_baseline')
                events = []
                for i in range(100):
                    world = np.asarray(analytic_hand())
                    if moving:
                        world += [i*.01, np.sin(i*.1), 0]
                    events.extend(detector.update(i*40, world.tolist())['events'])
                self.assertFalse(events)

    def test_each_finger_rejects_a_malformed_landmark_frame(self):
        detector = PoseTapDetector(self.models, mode='local_baseline')
        for i in range(12):
            detector.update(i*40, analytic_hand())
        result = detector.update(480, None)
        self.assertFalse(result['events'])
        for finger in FINGERS:
            self.assertFalse(result['signals'][finger]['valid'])
            self.assertFalse(result['signals'][finger]['decision']['ready'])


if __name__ == '__main__':
    unittest.main()
