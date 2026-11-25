"""기록의 누락 원인 집계. 정답 시각이 없으면 인식률을 계산하지 않는다."""
from collections import Counter


def summarize_outputs(frames):
    fingers = {}
    for frame in frames:
        for finger, signal in frame.get("signals", {}).items():
            row = fingers.setdefault(finger, {"frames": 0, "valid_frames": 0,
                "status_counts": Counter(), "blocker_frames": Counter(), "cycle_interruptions": Counter(),
                "candidate_count": 0})
            row["frames"] += 1
            row["valid_frames"] += int(bool(signal.get("valid")))
            decision = signal.get("decision", {})
            code = decision.get("code", "unknown")
            row["status_counts"][code] += 1
            row["blocker_frames"].update(decision.get("blockers", []))
            interruption = decision.get("last_interruption")
            if interruption and interruption["t_ms"] == frame["t_ms"]:
                row["cycle_interruptions"][interruption["code"]] += 1
        for event in frame.get("events", []):
            if event.get("finger") in fingers:
                fingers[event["finger"]]["candidate_count"] += 1
    for row in fingers.values():
        row["valid_ratio"] = row["valid_frames"] / row["frames"]
        for key in ("status_counts", "blocker_frames", "cycle_interruptions"):
            row[key] = dict(row[key])
    return {"fingers": fingers, "ground_truth_evaluated": False,
            "note": "횟수·보류 프레임 집계이며 탐지 정확도나 미탐률이 아닙니다."}
