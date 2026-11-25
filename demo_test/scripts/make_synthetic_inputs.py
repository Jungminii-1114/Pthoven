"""Generate non-human image/patch fixtures for infrastructure checks only."""
import argparse
import base64
import json
from pathlib import Path

import cv2
import numpy as np

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output-dir", type=Path, default=Path("outputs/fixtures"))
args = parser.parse_args()
args.output_dir.mkdir(parents=True, exist_ok=True)
image = np.full((360, 640, 3), (90, 120, 150), dtype=np.uint8)
for x in range(0, 640, 30):
    cv2.line(image, (x, 0), (x, 359), (80, 105, 130), 2)
cv2.ellipse(image, (320, 220), (65, 55), 0, 0, 360, (150, 180, 220), -1)
for i in range(5):
    cv2.rectangle(image, (255+i*24, 90+i*4), (273+i*24, 200), (155, 185, 225), -1)
cv2.imwrite(str(args.output_dir / "depth-input.jpg"), image)
request = {"frame_id": 0, "t_ms": 0, "input_size": 294,
           "image_path": "depth-input.jpg", "evidence_type": "synthetic_image_fixture",
           "landmarks": [[.43+(i % 5)*.03, .35+(i//5)*.04] for i in range(21)],
           "anchors": [[.08,.08],[.92,.08],[.08,.92],[.92,.92]]}
(args.output_dir / "depth-request.json").write_text(json.dumps(request, indent=2) + "\n")
rng = np.random.default_rng(42)
patch = cv2.GaussianBlur(rng.integers(20, 230, (48, 48), dtype=np.uint8), (3, 3), 0)
y, x = np.mgrid[:48, :48].astype(np.float32)
frames = []
for i in range(8):
    current = cv2.remap(patch, x + i*.2*np.sin(y/10), y, cv2.INTER_LINEAR)
    ok, encoded = cv2.imencode(".jpg", current)
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    frames.append({"t_ms": i*50, "skin_patch": "data:image/jpeg;base64," + base64.b64encode(encoded).decode()})
capture = {"schema_version": 1, "evidence_type": "synthetic_patch_fixture", "trials": [
    {"person_id": "synthetic", "session_id": "fixture", "scenario": "analytic_patch_warp", "frames": frames, "truth": None}]}
(args.output_dir / "skin-capture.json").write_text(json.dumps(capture, indent=2) + "\n")
print(f"Synthetic infrastructure fixtures: {args.output_dir}")
