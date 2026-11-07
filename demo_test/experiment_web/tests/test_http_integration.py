"""Exercise the standalone server on a temporary localhost port (no camera)."""
import base64
import json
import re
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from urllib.parse import urljoin, urlsplit

import cv2
import numpy as np
from pthoven_tap.examples import analytic_cycle, analytic_hand
from server.app import PTOVENHandler, WEB_ROOT


class QuietHandler(PTOVENHandler):
    def log_message(self, *args):
        pass


class LocalHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, path, payload=None):
        connection = HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request('GET' if payload is None else 'POST', path,
                               None if payload is None else json.dumps(payload),
                               {} if payload is None else {'Content-Type': 'application/json'})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def api(self, path, payload=None):
        status, body = self.request(path, payload)
        self.assertEqual(status, 200, body[:300])
        return json.loads(body)

    def test_local_pages_and_module_dependencies_are_served(self):
        self.assertEqual(self.request('/')[0], 200)
        files = list(WEB_ROOT.glob('*')) + list((WEB_ROOT.parent / 'models').glob('*.mjs'))
        for file in files:
            if file.suffix not in {'.html', '.css', '.mjs'}:
                continue
            path = '/' + (('models/' if file.parent.name == 'models' else '') + file.name)
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 200)
            source = file.read_text()
            refs = re.findall(r'(?:src|href)=[\"\']([^\"\']+)[\"\']', source) if file.suffix == '.html' else []
            refs += re.findall(r'(?:from\s*|import\s*)[\"\']([./][^\"\']+)[\"\']', source)
            for ref in refs:
                if ref.startswith(('https:', 'http:', '#', 'blob:')):
                    continue
                with self.subTest(source=path, reference=ref):
                    self.assertEqual(self.request(urlsplit(urljoin('http://localhost' + path, ref)).path)[0], 200)

    def test_tap_calibration_session_frames_and_close(self):
        self.assertTrue(self.api('/api/health')['ok'])
        self.assertEqual(self.api('/api/tap/status')['engine'], 'python')
        rest = [analytic_hand()] * 16
        self.assertTrue(self.api('/api/tap/reference', {'finger': 'index', 'frames': rest})['ok'])
        calibration = self.api('/api/tap/calibrate', {
            'finger': 'index', 'rest_frames': rest,
            'lift_frames': [analytic_hand({'index': 1})] * 16})
        self.assertTrue(calibration['ok'])
        session = self.api('/api/tap/session', {'calibration': {'index': calibration['model']}, 'mode': 'fixed'})
        result = self.api('/api/tap/frames', {'session_id': session['session_id'], 'sequence': 0, 'frames': analytic_cycle()})
        events = [event for frame in result['frames'] for event in frame['events']]
        self.assertEqual([e['finger'] for e in events], ['index'])
        self.assertFalse(events[0]['contact_verified'])
        self.assertTrue(self.api('/api/tap/close', {'session_id': session['session_id']})['ok'])

    def test_skin_pair_and_unloaded_optional_depth(self):
        patch = np.random.default_rng(2).integers(20, 230, (48, 48), dtype=np.uint8)
        ok, data = cv2.imencode('.png', patch)
        self.assertTrue(ok)
        encoded = 'data:image/png;base64,' + base64.b64encode(data).decode()
        result = self.api('/api/skin/observe', {'previous_patch': encoded, 'patch': encoded, 'previous_t_ms': 100, 't_ms': 220})
        self.assertTrue(result['valid'])
        self.assertFalse(result['contact_verified'])
        depth = self.api('/api/depth/status')
        self.assertEqual(depth['state'], 'not_loaded')
        self.assertFalse(depth['contact_verified'])

    def test_non_public_paths_and_invalid_api_inputs(self):
        for path in ['/models/depth.py', '/server/app.py', '/../README.md', '/.env']:
            with self.subTest(path=path):
                self.assertIn(self.request(path)[0], [403, 404])
        self.assertEqual(self.request('/api/tap/frames', [1])[0], 400)
        self.assertEqual(self.request('/api/unknown', {})[0], 404)
