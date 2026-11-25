"""Re-fit exported B-01 captures without camera/model; not an accuracy test."""
import argparse
import json
from pathlib import Path
from .pose import PoseError, fit_template, validate_rest


def replay_export(data):
    if data.get("schema_version") != "b01-export.v1":
        raise ValueError("b01-export.v1 기록이 필요합니다.")
    bundle = data["calibration"]
    policy = bundle["policy"]
    baseline = bundle["baseline_ref"]
    output = {"source": "replay", "original_source": data.get("source"),
              "contact_verified": False, "baseline_ref": baseline, "results": {}}
    if not data.get("rest_frames"):
        raise ValueError("기준 자세 기록이 없습니다.")
    validate_rest(data["rest_frames"], data["session"]["active_fingers"], policy)
    for finger in bundle["per_finger_templates"]:
        captures = [c for c in data["captures"] if c["phase"] == "lift" and c["finger"] == finger and
                    c["stage"] == "passed" and c["baseline_ref"] == baseline]
        if not captures:
            raise ValueError(f"{finger}: 기준과 일치하는 들림 원본이 없습니다.")
        try:
            template = fit_template(data["rest_frames"], captures[-1]["frames"], finger, policy)
            # Runtime references don't change calibration mathematics.
            expected = bundle["per_finger_templates"][finger]
            same = template["groups"] == expected["groups"] and template["length_reference"] == expected["length_reference"]
            output["results"][finger] = {"outcome": "calibrated", "same_model_parameters": same,
                                           "diagnostics": template["diagnostics"]}
        except PoseError as exc:
            output["results"][finger] = {"outcome": exc.code, "diagnostics": exc.diagnostics}
    return output


def replay_input_run(run_export, geometry):
    """B-02 landmark replay: 기록된 관측(누락 포함)을 같은 순서·시각으로 다시 판정한다.

    모델 추론을 다시 돌리지 않는 landmark replay다. 결과는 source=replay이며 실시간 성능으로 세지 않는다.
    """
    from .b02 import InputRun
    if run_export.get("schema_version") != "b02-input-run.v1":
        raise ValueError("b02-input-run.v1 기록이 필요합니다.")
    start = run_export["start"]
    run = InputRun(session_id=start["session_id"], clock_id=start["clock_id"], templates=start["templates"],
                   epoch=start["command_epoch"], calibration_version=start["calibration_version"],
                   geometry_version=start["geometry_version"], config_version=start["config_version"],
                   model_ref=start["model_ref"], started_t_ms=start["started_t_ms"], geometry=start.get("geometry") or geometry)
    geometry = start.get("geometry") or geometry
    for obs in run_export["observations"]:
        raw = None if obs["world_landmarks"] is None else {"world_landmarks": obs["world_landmarks"], "landmarks": obs["landmarks"]}
        run.observe(frame_id=obs["frame_id"], t_ms=obs["t_ms"], track_id=obs["track_id"], raw=raw,
                    geometry=geometry, observation_ref=f"replay-{obs['frame_id']}")
    original = [(c["finger"], c["t_ms"], c["candidate_frame_id"]) for c in run_export["candidates"]]
    replayed = [(c["finger"], c["t_ms"], c["candidate_frame_id"]) for c in run.candidates]
    keys = lambda props: [(p["candidate_id"], p["input_status"], p["midi_note"], p["quality_flags"]) for p in props]
    complete = len(run_export["observations"]) < run_export.get("observations_limit", float("inf"))
    return {"source": "replay", "contact_verified": False, "observations": len(run_export["observations"]),
            "observations_complete": complete,
            "original_candidates": len(original), "replayed_candidates": len(replayed),
            "same_candidates": original == replayed if complete else None,
            "same_mapping": keys(run_export["proposals"]) == keys(run.proposals.values()) if complete else None,
            "replay": run.export()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    result = replay_export(data)
    runs = data.get("input_runs") or []
    if runs:
        result["input_runs"] = [{k: v for k, v in replay_input_run(r, data["geometry"]).items() if k != "replay"} | {"ended_reason": r.get("ended_reason")}
                                for r in runs]
    content = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+"\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
