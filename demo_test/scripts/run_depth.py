"""Run the fixed Depth Anything model on one request file without a web server."""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models.depth import DepthService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--load-timeout", type=float, default=180)
    args = parser.parse_args()
    payload = json.loads(args.request.read_text())
    if not isinstance(payload, dict):
        raise ValueError("요청 JSON 객체가 필요합니다.")
    if "image_path" in payload:
        from PIL import Image
        source = (args.request.resolve().parent / payload.pop("image_path")).resolve()
        with Image.open(source) as image:
            if min(image.size) < 32 or max(image.size) > 1280:
                raise ValueError("이미지 크기는 각 변 32~1280 픽셀이어야 합니다.")
            buf = io.BytesIO()
            image.convert("RGB").save(buf, format="JPEG", quality=90)
        payload["image"] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    service = DepthService()
    service.prepare()
    deadline = time.monotonic() + args.load_timeout
    while service.state == "loading":
        if time.monotonic() >= deadline:
            raise RuntimeError("모델 로딩 시간 초과. 캐시·네트워크 또는 --load-timeout을 확인하세요.")
        time.sleep(0.1)
    if service.state != "ready":
        raise RuntimeError(service.error)
    result = service.infer(payload)
    if args.preview and result.get("preview"):
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        args.preview.write_bytes(base64.b64decode(result.pop("preview").split(",", 1)[1]))
        result["preview_file"] = str(args.preview)
    result["evidence_type"] = payload.get("evidence_type", "unverified_image_inference")
    result["accuracy_evaluated"] = False
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "model": result["model"],
                      "inference_ms": result["inference_ms"], "accuracy_evaluated": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
