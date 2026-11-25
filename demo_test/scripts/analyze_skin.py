"""Extract dorsal optical-flow signals from recorded grayscale patch pairs."""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from models.skin_features import skin_motion


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.capture.read_text())
    if payload.get("schema_version") != 1:
        raise ValueError("Expected tap-lab schema_version 1")
    trials = []
    for trial in payload["trials"]:
        previous = None
        previous_t = None
        rows = []
        for frame in trial["frames"]:
            encoded = frame.get("skin_patch")
            if not encoded:
                previous = None
                continue
            raw = base64.b64decode(encoded.split(",", 1)[1], validate=True)
            current = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
            if current is None:
                previous = None
                continue
            t = frame["t_ms"]
            if previous is not None and previous_t is not None and 0 < t - previous_t <= 150:
                rows.append({"t_ms": t, "dt_ms": t - previous_t, **skin_motion(previous, current)})
            previous, previous_t = current, t
        trials.append({"scenario": trial["scenario"], "person_id": trial["person_id"], "session_id": trial["session_id"], "signals": rows})
    result = {"evidence_type": "skin_feature_extraction_only", "contact_verified": False,
              "note": "No trained skin model or ablation accuracy. Apparent flow/strain is not force or contact.", "trials": trials}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"Saved {sum(len(t['signals']) for t in trials)} feature pairs: {args.output}")


if __name__ == "__main__":
    main()
