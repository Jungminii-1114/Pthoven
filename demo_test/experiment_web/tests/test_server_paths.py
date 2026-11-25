import io
import unittest
from server.app import PTOVENHandler, WEB_ROOT


class ServerPathsTests(unittest.TestCase):
    def handler(self, path):
        h=object.__new__(PTOVENHandler);h.path=path;h.errors=[];h.served=[]
        h.send_error=lambda status: h.errors.append(int(status))
        return h

    def test_root_opens_experiment_selector(self):
        h=self.handler('/');h._serve_file=lambda path:h.served.append(path)
        h.do_GET();self.assertEqual(h.served,[WEB_ROOT/'experiment-lab.html'])

    def test_private_paths_and_python_models_are_not_public(self):
        for path in ['/../README.md','/models/depth.py','/.env','/server/app.py']:
            with self.subTest(path=path):
                h=self.handler(path);h.do_GET();self.assertTrue(h.errors)

    def test_read_body_rejects_negative_or_oversized_requests(self):
        for length in [-1,8_000_001]:
            h=self.handler('/api/tap/frames');h.headers={'Content-Length':str(length)};h.rfile=io.BytesIO(b'{}')
            with self.assertRaises(ValueError):h._read_body()

    def test_read_json_requires_object(self):
        h=self.handler('/api/tap/frames');h.headers={'Content-Length':'2'};h.rfile=io.BytesIO(b'[]')
        with self.assertRaises(ValueError):h._read_json()
