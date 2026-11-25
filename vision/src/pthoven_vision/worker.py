"""MediaPipe lives in a child so a native SDK abort cannot kill the web server."""
from __future__ import annotations
import multiprocessing
import time
from .contracts import B01Error


def _run(connection, model_path):
    try:
        import mediapipe as mp
        import numpy as np
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=model_path, delegate=mp.tasks.BaseOptions.Delegate.CPU),
            running_mode=mp.tasks.vision.RunningMode.VIDEO, num_hands=2,
            min_hand_detection_confidence=.5, min_hand_presence_confidence=.5, min_tracking_confidence=.5)
        model = mp.tasks.vision.HandLandmarker.create_from_options(options)
        connection.send({'ready': True})
        with model:
            while True:
                message = connection.recv()
                if message is None:
                    break
                rgb, timestamp = message
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
                observed = model.detect_for_video(image, timestamp)
                result = [{'handedness': side[0].category_name.lower(), 'handedness_score': side[0].score,
                           'image_xy': [[p.x,p.y] for p in image_points],
                           'world_landmarks': [[p.x,p.y,p.z] for p in world]}
                          for side,image_points,world in zip(observed.handedness,observed.hand_landmarks,observed.hand_world_landmarks)]
                connection.send({'hands': result})
    except (Exception, EOFError) as exc:
        try:
            connection.send({'error': f'{type(exc).__name__}: {exc}'})
        except (OSError, EOFError):
            pass
    finally:
        connection.close()


class ProcessTracker:
    def __init__(self, path, timeout=15):
        context = multiprocessing.get_context('spawn')
        self.connection, child = context.Pipe()
        self.process = context.Process(target=_run,args=(child,str(path)),daemon=True)
        self.last_ms = -1
        self.timeout = timeout
        self.process.start()
        child.close()
        try:
            response = self._receive()
            if not response.get('ready'):
                raise B01Error('INFERENCE_FAILED',response.get('error','추론 worker 초기화 실패'),503)
        except Exception:
            self.close()
            raise

    def _receive(self):
        end=time.monotonic()+self.timeout
        while time.monotonic()<end:
            if self.connection.poll(.05):
                try:
                    response=self.connection.recv()
                except (EOFError,OSError) as exc:
                    raise B01Error('INFERENCE_FAILED','Python 추론 프로세스가 종료됐습니다. SDK·OS 환경을 확인하세요.',503) from exc
                if 'error' in response:
                    raise B01Error('INFERENCE_FAILED',response['error'],503)
                return response
            if not self.process.is_alive():
                raise B01Error('INFERENCE_FAILED','Python 추론 프로세스가 종료됐습니다. SDK·OS 환경을 확인하세요.',503)
        raise B01Error('INFERENCE_TIMEOUT','Python 추론 응답 시간이 초과됐습니다.',503)

    def infer(self,rgb,timestamp):
        if timestamp<=self.last_ms:
            raise B01Error('DUPLICATE_MODEL_TIMESTAMP','같은 정수 ms 프레임은 다시 추론하지 않습니다.')
        try:
            self.connection.send((rgb,timestamp))
        except (BrokenPipeError,EOFError,OSError) as exc:
            raise B01Error('INFERENCE_FAILED','추론 프로세스 연결이 끊겼습니다.',503) from exc
        self.last_ms=timestamp
        return self._receive()['hands']

    def close(self):
        try:
            if self.process.is_alive():self.connection.send(None)
        except (OSError,EOFError):pass
        self.process.join(timeout=.5)
        if self.process.is_alive():
            self.process.terminate();self.process.join(timeout=1)
        self.connection.close()
