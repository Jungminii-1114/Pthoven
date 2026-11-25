"""Run the PTOVEN browser demo with Python's standard HTTP server."""

from __future__ import annotations

import argparse
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from models.depth import DEPTH
from models.skin_service import observe_skin
from pthoven_tap.service import TAP


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"


class PTOVENHandler(BaseHTTPRequestHandler):
    server_version = "PTOVEN/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 <= length <= 8_000_000:
            raise ValueError("요청 본문이 너무 큽니다.")
        return self.rfile.read(length)

    def _read_json(self) -> dict[str, Any]:
        body = self._read_body()
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("올바른 JSON 요청이 아닙니다.") from exc
        if not isinstance(parsed, dict):
            raise ValueError("JSON 객체가 필요합니다.")
        return parsed

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            self._send_json({"ok": True, "purpose": "vision_experiments"})
            return
        if path == "/api/depth/status":
            self._send_json(DEPTH.status())
            return
        if path == "/api/tap/status":
            self._send_json(TAP.status())
            return
        # Only this shared browser module is public; Python model files stay private.
        if path in {"/models/tap-detector.mjs", "/models/depth-detector.mjs"}:
            self._serve_file(ROOT / "models" / Path(path).name)
            return

        if path in {"/", "/index.html"}:
            self._serve_file(WEB_ROOT / "experiment-lab.html")
            return

        relative = path.removeprefix("/")
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in target.parents:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self._serve_file(target)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            tap_routes = {"/api/tap/reference": TAP.reference, "/api/tap/calibrate": TAP.calibrate,
                          "/api/tap/session": TAP.create_session, "/api/tap/frames": TAP.frames, "/api/tap/close": TAP.close}
            if path in tap_routes:
                self._send_json(tap_routes[path](self._read_json()))
                return
            if path == "/api/skin/observe":
                self._send_json(observe_skin(self._read_json()))
                return
            if path == "/api/depth/prepare":
                self._send_json(DEPTH.prepare(), HTTPStatus.ACCEPTED)
                return
            if path == "/api/depth/frame":
                self._send_json(DEPTH.infer(self._read_json()))
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
        except Exception as exc:  # pragma: no cover - final server boundary
            self._send_json(
                {"error": f"서버 처리 중 오류가 발생했습니다: {exc}"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _serve_file(self, path: Path) -> None:
        if path.suffix not in {".html", ".css", ".js", ".mjs"} or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        content_type, _ = mimetypes.guess_type(path.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PTOVEN demo server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), PTOVENHandler)
    print(f"Pthoven experiments: http://{args.host}:{args.port}/experiment-lab.html", flush=True)
    print("종료: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버를 종료합니다.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
