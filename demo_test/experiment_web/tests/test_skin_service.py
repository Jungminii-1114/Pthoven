import base64
import io
import json
import unittest
import cv2
import numpy as np
from models.skin_service import observe_skin
from server.app import PTOVENHandler


def encoded(image):
    ok, data = cv2.imencode('.png', image)
    assert ok
    return 'data:image/png;base64,' + base64.b64encode(data).decode()


class SkinServiceTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(12)
        self.patch = cv2.GaussianBlur(rng.integers(20, 230, (48, 48), dtype=np.uint8), (3, 3), 0)
        self.payload = dict(previous_patch=encoded(self.patch), patch=encoded(self.patch), previous_t_ms=100, t_ms=220)

    def test_real_image_pair_has_exact_times_and_no_contact_claim(self):
        r = observe_skin(self.payload)
        self.assertTrue(r['valid'])
        self.assertEqual((r['previous_t_ms'], r['t_ms'], r['dt_ms']), (100, 220, 120))
        self.assertLess(r['local_flow_px'], .02)
        self.assertIsNone(r['tap_classification'])
        self.assertFalse(r['contact_verified'])

    def test_bad_pair_time_cannot_cross_tracking_gap(self):
        for t in [99, 100, 110, 351, float('nan'), True]:
            with self.subTest(t=t), self.assertRaises(ValueError):
                observe_skin({**self.payload, 't_ms': t})

    def test_invalid_images_and_mismatched_shapes_are_rejected(self):
        for patch in ['', 'data:image/jpeg;base64,@@', encoded(np.zeros((200, 200), dtype=np.uint8)), encoded(self.patch[:32])]:
            with self.subTest(patch=patch[:30]), self.assertRaises(ValueError):
                observe_skin({**self.payload, 'patch': patch})

    def test_no_texture_is_withheld(self):
        patch = encoded(np.zeros((48, 48), dtype=np.uint8))
        r = observe_skin({**self.payload, 'patch': patch, 'previous_patch': patch})
        self.assertFalse(r['valid'])
        self.assertFalse(r['contact_verified'])

    def test_post_route_parses_real_json_and_returns_measurement(self):
        handler = object.__new__(PTOVENHandler)
        handler.path = '/api/skin/observe'
        body = json.dumps(self.payload).encode()
        handler.headers = {'Content-Length': str(len(body))}
        handler.rfile = io.BytesIO(body)
        sent = []
        handler._send_json = lambda result, status=200: sent.append((result, status))
        handler.do_POST()
        self.assertEqual(sent[0][1], 200)
        self.assertEqual(sent[0][0]['kind'], 'dorsal_motion_observation')
        self.assertTrue(sent[0][0]['valid'])
