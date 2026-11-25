"""Local B-01·B-02 API. HTTP latest-frame transport; B-02 proposals are finalized by /events/ack."""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import sys
import threading

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .contracts import (AckRequest, B01Error, CaptureRequest, DeviceRequest, EpochRequest, FrameRequest,
                        GeometryRequest, StartRequest, failure, result)
from .session import B01
from .tracker import TrackerManager, decode_image

PREFIX = "/api/vision/v1"


def create_app(engine=None, tracker=None):
    engine, tracker = engine or B01(), tracker or TrackerManager()
    cache = {}
    mutation_lock = threading.RLock()

    @asynccontextmanager
    async def lifespan(app):
        stop = threading.Event()
        def watchdog():
            while not stop.wait(.1):
                with engine.lock:
                    sessions = list(engine.sessions.values())
                for session in sessions:
                    with session.lock:
                        engine.watchdog(session)
        worker = threading.Thread(target=watchdog, daemon=True)
        worker.start()
        yield
        stop.set()
        worker.join(timeout=1)
        tracker.close_all()

    app = FastAPI(title="Pthoven B-01·B-02", lifespan=lifespan)
    app.state.engine, app.state.tracker = engine, tracker

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        length = request.headers.get("content-length")
        try:
            if length is not None and not 0 <= int(length) <= 2_000_000:
                return JSONResponse(failure("BODY_TOO_LARGE", "요청 본문 한도는 2MB입니다."), status_code=413)
        except ValueError:
            return JSONResponse(failure("INVALID_LENGTH", "요청 길이가 잘못되었습니다."), status_code=400)
        # Count received bytes too: chunked requests do not have Content-Length.
        if request.method in {"POST", "PUT", "PATCH"}:
            actual = 0
            chunks = []
            async for chunk in request.stream():
                actual += len(chunk)
                if actual > 2_000_000:
                    return JSONResponse(failure("BODY_TOO_LARGE", "요청 본문 한도는 2MB입니다."), status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        # No public deployment or cross-origin camera frame ingestion by default.
        origin = request.headers.get("origin")
        if origin and origin not in {f"http://{request.headers.get('host')}", f"https://{request.headers.get('host')}"}:
            return JSONResponse(failure("CROSS_ORIGIN_DENIED", "같은 로컬 페이지에서 요청하세요."), status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(B01Error)
    async def domain_error(request, exc):
        return JSONResponse(failure(exc.code, str(exc)), status_code=exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(failure("INVALID_REQUEST", str(exc)), status_code=422)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse(failure("INVALID_VALUE", str(exc)), status_code=422)

    def mutate(route, sid, data, request_id, function):
        if request_id and (len(request_id) > 128 or not request_id.strip()):
            raise B01Error("INVALID_REQUEST_ID", "요청 ID가 잘못되었습니다.", 422)
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()
        key = (route, sid, request_id)
        with mutation_lock:
            if request_id and key in cache:
                old = cache[key]
                if old[0] != fingerprint:
                    raise B01Error("REQUEST_ID_CONFLICT", "같은 요청 ID에 다른 내용이 전달되었습니다.")
                stored_session = engine.get(old[2])
                actual = stored_session.epoch
                old_capture = old[1].get("data", {}).get("capture_id") if route == "capture" else None
                inactive_capture = old_capture and (stored_session.active_capture is None or stored_session.active_capture.capture_id != old_capture)
                if actual != old[3] or inactive_capture:
                    raise B01Error("STALE_CONTEXT", "요청 뒤 세션이 변경되었습니다.")
                return old[1]
            if request_id and len(cache) >= 4096:
                raise B01Error("REQUEST_CACHE_LIMIT", "요청 기록 한도에 도달했습니다.", 503)
            response = function()
            if request_id:
                actual_sid = sid or response["data"]["session_id"]
                cache[key] = (fingerprint, response, actual_sid, engine.get(actual_sid).epoch)
            return response

    @app.get(PREFIX+"/health")
    def health():
        return {"runtime": "python", "scope": "B-01+B-02", "model": tracker.status(),
                "transport": "http_latest_frame", "contact_verified": False}

    @app.post(PREFIX+"/model/prepare")
    def prepare():
        return result(tracker.prepare(), "model_preparation_requested")

    @app.post(PREFIX+"/sessions")
    def create(data: StartRequest, x_request_id: str | None = Header(default=None)):
        return mutate("create", None, data.model_dump(), x_request_id, lambda: engine.create(data))

    @app.get(PREFIX+"/sessions/{sid}")
    def snapshot(sid: str):
        return engine.snapshot(sid)

    @app.post(PREFIX+"/sessions/{sid}/device")
    def device(sid: str, data: DeviceRequest):
        old_epoch = engine.get(sid).epoch
        response = engine.device(sid, data, tracker.status())
        if engine.get(sid).epoch != old_epoch:
            tracker.close(sid)
        return response

    @app.put(PREFIX+"/sessions/{sid}/geometry")
    def geometry(sid: str, data: GeometryRequest, x_request_id: str | None = Header(default=None)):
        return mutate("geometry", sid, data.model_dump(), x_request_id, lambda: engine.set_geometry(sid, data))

    @app.post(PREFIX+"/sessions/{sid}/frames")
    def frame(sid: str, data: FrameRequest):
        s = engine.get(sid)
        if not s.inference_lock.acquire(blocking=False):
            raise B01Error("FRAME_BUSY", "이 세션의 이전 프레임이 처리 중입니다.", 429)
        try:
            with s.lock:
                s.check(data.command_epoch)
                if data.clock_id != s.request.clock_id or data.frame_id <= s.last_frame_id or (s.last_t_ms is not None and data.t_ms <= s.last_t_ms):
                    raise B01Error("NON_MONOTONIC_FRAME", "시계·프레임 순서를 확인하세요.")
                if data.geometry_version != s.geometry_version or data.calibration_version != s.calibration_version:
                    raise B01Error("STALE_VERSION", "기하·보정 버전이 변경되었습니다.")
                if s.device["permission"] != "granted" or s.device["stream"] in {"ended", "disconnected"}:
                    raise B01Error("DEVICE_NOT_READY", "카메라 권한과 연결 상태를 확인하세요.")
            rgb = decode_image(data.image)
            with s.lock:
                s.check(data.command_epoch)
                if s.geometry:
                    source = s.geometry["source_size"]
                    h,w = rgb.shape[:2]
                    if abs(w/h-source["width"]/source["height"]) > 1/h+1/source["height"]:
                        s.invalidate(pose=True,geometry=True)
                        s.phase = "hold"
                        raise B01Error("FRAME_GEOMETRY_MISMATCH", "영상 비율이 변경되었습니다. 장치·좌표·보정을 다시 준비하세요.")
            # Heavy inference is outside the state lock. Watchdog/cancel can run.
            try:
                hands = tracker.infer(sid, rgb, data.t_ms)
            except B01Error as exc:
                if exc.code.startswith("INFERENCE"):
                    with s.lock:
                        if not s.closed and s.epoch == data.command_epoch:
                            engine.process_observation(sid, data, [], tracker_state="failed", model_ref=tracker.status()["model_ref"], inference_error=str(exc))
                raise
            with s.lock:
                s.check(data.command_epoch)
                return engine.process_observation(sid, data, hands, tracker_state=tracker.status()["state"], model_ref=tracker.status()["model_ref"])
        finally:
            s.inference_lock.release()

    @app.post(PREFIX+"/sessions/{sid}/calibration/captures")
    def start_capture(sid: str, data: CaptureRequest, x_request_id: str | None = Header(default=None)):
        return mutate("capture", sid, data.model_dump(), x_request_id, lambda: engine.start_capture(sid, data))

    @app.post(PREFIX+"/sessions/{sid}/calibration/captures/{cid}/finish")
    def finish(sid: str, cid: str, data: EpochRequest, x_request_id: str | None = Header(default=None)):
        return mutate("finish:"+cid, sid, data.model_dump(), x_request_id, lambda: engine.finish_capture(sid, cid, data.command_epoch))

    @app.delete(PREFIX+"/sessions/{sid}/calibration/captures/{cid}")
    def cancel(sid: str, cid: str, command_epoch: int):
        return engine.cancel_capture(sid, cid, command_epoch)

    @app.post(PREFIX+"/sessions/{sid}/recalibrate")
    def recalibrate(sid: str, data: EpochRequest):
        return engine.request_recalibration(sid, data.command_epoch)

    @app.post(PREFIX+"/sessions/{sid}/ready")
    def ready(sid: str, data: EpochRequest):
        return engine.ready(sid, data.command_epoch)

    @app.post(PREFIX+"/sessions/{sid}/start")
    def start_input(sid: str, data: EpochRequest):
        return engine.start_input(sid, data.command_epoch)

    @app.post(PREFIX+"/sessions/{sid}/pause")
    def pause_input(sid: str, data: EpochRequest):
        return engine.stop_input(sid, data.command_epoch)

    @app.post(PREFIX+"/sessions/{sid}/events/ack")
    def ack(sid: str, data: AckRequest):
        return engine.acknowledge(sid, data)

    @app.post(PREFIX+"/sessions/{sid}/exports")
    def export(sid: str, data: EpochRequest):
        return engine.export(sid, data.command_epoch)

    @app.post(PREFIX+"/sessions/{sid}/close")
    def close(sid: str, data: EpochRequest):
        response = engine.close(sid, data.command_epoch)
        tracker.close(sid)
        return response

    root = Path(__file__).resolve().parents[2] / "web"
    if not root.is_dir():
        root = Path(sys.prefix) / "share/pthoven-vision/web"
    app.mount("/", StaticFiles(directory=root, html=True), name="web")
    return app


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description="Pthoven B-01 세션 준비·보정")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    print(f"B-01: http://127.0.0.1:{args.port}/", flush=True)
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
