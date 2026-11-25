"""웹 없이 실행: python -m pthoven_tap demo/calibrate/replay."""
import argparse
import json
from pathlib import Path

from . import ALGORITHM, ENGINE_REVISION, CalibrationError, calibrate, PoseTapDetector
from .examples import analytic_hand, analytic_cycle
from .diagnostics import summarize_outputs


def calibration_input(data):
    if data.get("kind") == "pthoven_calibration_diagnostics":
        for a in reversed(data.get("attempts", [])):
            if isinstance(a.get("rest_frames"), list) and isinstance(a.get("frames"), list):
                return a["rest_frames"], a["frames"], a["finger"]
        raise ValueError("기준·들림 프레임이 있는 진단 기록이 없습니다.")
    return data["rest_frames"], data["lift_frames"], data.get("finger", "index")


def main():
    parser = argparse.ArgumentParser(description="Python TIP 궤적·관절각 타건 후보 실험")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="합성 손으로 길이 검사와 타건 후보 검증")
    demo.add_argument("--output", type=Path)
    cal = sub.add_parser("calibrate", help="기존 진단 JSON 또는 기준·들림 JSON 보정")
    cal.add_argument("--input", type=Path, required=True); cal.add_argument("--output", type=Path, required=True)
    replay = sub.add_parser("replay", help="Python 보정 모델로 랜드마크 기록 재생")
    replay.add_argument("--input", type=Path, required=True); replay.add_argument("--model", type=Path); replay.add_argument("--output", type=Path, required=True)
    replay.add_argument("--trial-index", type=int, default=0, help="여러 시행 중 재생할 0부터 시작하는 번호")
    replay.add_argument("--mode", choices=["fixed", "local_baseline"], help="기록의 판정 방식을 재생하거나 다른 방식으로 비교")
    args = parser.parse_args()
    if args.command == "demo":
        model = calibrate([analytic_hand() for _ in range(20)], [analytic_hand({"index": 1}) for _ in range(20)], "index")
        d = PoseTapDetector({"index": model}); outputs = [d.update(f["t_ms"], f["world_landmarks"]) for f in analytic_cycle()]
        result = {"algorithm": ALGORITHM, "evidence_type": "synthetic_logic_only", "human_accuracy_evaluated": False,
                  "contact_verified": False, "events": [e for r in outputs for e in r["events"]], "frames": outputs}
    elif args.command == "calibrate":
        data = json.loads(args.input.read_text()); rest, lift, finger = calibration_input(data)
        try:
            model = calibrate(rest, lift, finger); result = {"ok": True, "model": model, "diagnostics": model["diagnostics"]}
        except CalibrationError as e:
            result = {"ok": False, "error": str(e), "code": e.code, "diagnostics": e.diagnostics}
    else:
        data = json.loads(args.input.read_text())
        trial = data.get("trials", [])[args.trial_index] if data.get("trials") else data
        if args.model:
            saved = json.loads(args.model.read_text()); model = saved.get("model", saved)
            calibration = {model["finger"]: model}
        else:
            calibration = trial.get("calibration")
            if not calibration:
                raise ValueError("기록 안에 보정 모델이 없습니다. --model을 지정하세요.")
        mode = args.mode or trial.get("detector_mode", "fixed")
        detector = PoseTapDetector(calibration, mode=mode)
        frames = data.get("frames")
        if frames is None:
            frames = trial.get("frames")
        if not isinstance(frames, list):
            raise ValueError("시각과 랜드마크를 담은 frames 배열이 필요합니다.")
        result = {"algorithm": ALGORITHM, "contact_verified": False, "ground_truth_evaluated": False,
                  "frames": [detector.update(f["t_ms"], f.get("world_landmarks"), f.get("landmarks")) for f in frames]}
        result["diagnostic_summary"] = summarize_outputs(result["frames"])
        result["detector_mode"] = mode
    result['engine_revision'] = ENGINE_REVISION
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(text + "\n"); print(f"저장: {args.output}")
    else:
        print(text)
    return 2 if result.get("ok") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
