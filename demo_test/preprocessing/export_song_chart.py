"""전처리 practice JSON → 화면 낙하 가이드용 곡 차트(pthoven-song-chart.v1).

입력은 preprocess_mutopia.py / preprocess_nasap.py가 만든 곡 JSON이다. 이 파일은
practice.notes를 그대로 옮기고, 화면에 필요한 건반 이름과 출처 표기만 붙인다.
새 음을 만들거나 음높이·시각을 고치지 않는다. 구간을 더 자를 때(--end-quarter)는
잘린 음표 ID를 차트에 기록한다.

데모 가상 건반(vision geometry.py)은 흰 건반 C4~C5 8개이므로 그 밖의 음,
동시음, ready=false 연습 버전은 거부한다.

실행 예(저장소 demo_test 폴더 기준):
  python preprocessing/export_song_chart.py --practice outputs/x/score.json \
    --id pleyel-rondo-m1-4 --level 1 --title-ko "플레옐 론도" \
    --output-dir ../vision/web/songs
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SCHEMA = "pthoven-song-chart.v1"
WHITE_KEYS = {60: "C4", 62: "D4", 64: "E4", 65: "F4", 67: "G4", 69: "A4", 71: "B4", 72: "C5"}
EPS = 1e-6


class ChartError(ValueError):
    pass


def build_chart(document, *, song_id, level, title_ko, end_quarter=None):
    if document.get("kind") != "pthoven_practice_score":
        raise ChartError("전처리 곡 JSON(kind=pthoven_practice_score)이 아닙니다.")
    practice, source = document["practice"], document["source"]
    if not practice.get("ready"):
        raise ChartError(f"ready=false 연습 버전은 차트로 만들지 않습니다: {practice.get('rejection_reasons')}")
    notes = sorted(practice["notes"], key=lambda n: (n["onset_quarter"], -n["pitch"]))
    excluded = []
    if end_quarter is not None:
        excluded = [n["id"] for n in notes if n["onset_quarter"] >= end_quarter - EPS]
        notes = [n for n in notes if n["onset_quarter"] < end_quarter - EPS]
    if not notes:
        raise ChartError("남은 음표가 없습니다.")
    chart_notes = []
    for i, n in enumerate(notes):
        if n["pitch"] not in WHITE_KEYS:
            raise ChartError(f"{n['id']}: 데모 건반(흰 건반 C4~C5) 밖의 음 {n['pitch']}")
        if i and n["onset_quarter"] - notes[i - 1]["onset_quarter"] < EPS:
            raise ChartError(f"{n['id']}: 같은 시각의 동시음은 지원하지 않습니다.")
        duration = n["duration_quarter"]
        if end_quarter is not None:
            duration = min(duration, end_quarter - n["onset_quarter"])
        chart_notes.append({
            "id": f"n{i + 1:03d}", "source_note_id": n["source_note_id"], "midi": n["pitch"],
            "key": WHITE_KEYS[n["pitch"]], "onset_quarter": round(n["onset_quarter"], 6),
            "duration_quarter": round(duration, 6), "source_measure_id": n.get("source_measure_id"),
        })
    options, transformations = practice["options"], practice["transformations"]
    meters = document["reference"].get("time_signatures") or [{"numerator": 4, "denominator": 4}]
    last = chart_notes[-1]
    return {
        "schema": SCHEMA, "id": song_id, "level": level, "title_ko": title_ko,
        "title": source.get("title"), "composer": source.get("composer"),
        "license": source.get("license"), "source_url": source.get("source_url"),
        "attribution": (f"{source.get('title')} — {source.get('composer')} · {source.get('dataset')} "
                        f"({source.get('source_url')}) · {source.get('license')} · "
                        "프토벤 연습용으로 발췌·이조·단선율화"),
        "excerpt": {
            "source_measures": transformations.get("source_measure_ordinals"),
            "transpose_semitones": options.get("transpose", 0),
            "tracks": transformations.get("selected_tracks"),
            "melody_policy": options.get("melody"),
            "end_quarter": end_quarter, "excluded_after_end_note_ids": excluded,
        },
        "derived_from": {
            "dataset": source.get("dataset"), "midi_sha256": source.get("midi_sha256"),
            "preprocessor_version": document.get("preprocessor_version"),
            "practice_version_id": practice.get("version_id"),
        },
        "time_signature": [meters[0]["numerator"], meters[0]["denominator"]],
        "default_quarter_bpm": options.get("bpm"),
        "duration_quarter": round(last["onset_quarter"] + last["duration_quarter"], 6),
        "keys": [WHITE_KEYS[p] for p in sorted({n["midi"] for n in chart_notes})],
        "min_onset_gap_quarter": round(min((b["onset_quarter"] - a["onset_quarter"] for a, b in zip(chart_notes, chart_notes[1:])), default=0), 6),
        "notes": chart_notes,
        "evaluation_policy": practice.get("evaluation_policy"),
    }


def write_index(directory):
    directory = Path(directory)
    songs = []
    for path in sorted(directory.glob("*.json")):
        if path.name == "index.json":
            continue
        chart = json.loads(path.read_text())
        if chart.get("schema") != SCHEMA:
            continue
        songs.append({"id": chart["id"], "file": path.name, "level": chart["level"], "title_ko": chart["title_ko"],
                      "composer": chart["composer"], "note_count": len(chart["notes"]), "keys": chart["keys"],
                      "license": chart["license"]})
    songs.sort(key=lambda s: (s["level"], s["id"]))
    (directory / "index.json").write_text(json.dumps({"schema": "pthoven-song-index.v1", "songs": songs}, ensure_ascii=False, indent=2) + "\n")
    return songs


def main(argv=None):
    p = argparse.ArgumentParser(description="전처리 practice JSON → 화면 낙하 가이드 곡 차트")
    p.add_argument("--practice", required=True, help="전처리기가 만든 곡 JSON")
    p.add_argument("--id", required=True)
    p.add_argument("--level", type=int, required=True)
    p.add_argument("--title-ko", required=True)
    p.add_argument("--end-quarter", type=float, help="이 quarter 이후 시작하는 음표를 잘라 낸다(ID 기록)")
    p.add_argument("--output-dir", required=True)
    a = p.parse_args(argv)
    chart = build_chart(json.loads(Path(a.practice).read_text()), song_id=a.id, level=a.level,
                        title_ko=a.title_ko, end_quarter=a.end_quarter)
    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{a.id}.json").write_text(json.dumps(chart, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    songs = write_index(out)
    print(f"{a.id}: 음표 {len(chart['notes'])}개, 건반 {','.join(chart['keys'])} · 목록 {len(songs)}곡")


if __name__ == "__main__":
    main()
