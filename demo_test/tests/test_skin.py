import unittest
import cv2
import numpy as np
from models.skin_features import skin_motion


class SkinFeatureTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(42)
        self.patch = cv2.GaussianBlur(rng.integers(20, 230, (64, 64), dtype=np.uint8), (3, 3), 0)

    def test_static_texture_has_near_zero_flow(self):
        result = skin_motion(self.patch, self.patch)
        self.assertTrue(result["valid"])
        self.assertLess(result["local_flow_px"], .02)

    def test_no_texture_is_not_confident_contact(self):
        patch = np.zeros((64, 64), dtype=np.uint8)
        self.assertFalse(skin_motion(patch, patch)["valid"])

    def test_nonrigid_warp_produces_local_deformation(self):
        y, x = np.mgrid[:64, :64].astype(np.float32)
        warped = cv2.remap(self.patch, x + 2 * np.sin(y / 12), y, cv2.INTER_LINEAR)
        result = skin_motion(self.patch, warped)
        self.assertGreater(result["local_flow_px"], .1)
        self.assertGreater(result["apparent_strain"], .01)

    def test_bad_shapes_rejected(self):
        with self.assertRaises(ValueError):
            skin_motion(self.patch, self.patch[:20])
