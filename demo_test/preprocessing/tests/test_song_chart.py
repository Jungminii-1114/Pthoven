"""export_song_chart: 전처리 practice JSON → 화면 가이드 곡 차트 변환 검사.

mido 없이 실행된다. 실행(demo_test 폴더): python -m unittest preprocessing.tests.test_song_chart -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from export_song_chart import ChartError, build_chart, main  # noqa: E402


def document(notes, ready=True):
    return {
        "kind": "pthoven_practice_score", "preprocessor_version": "1.0.0",
        "source": {"dataset": "Mutopia", "title": "Test", "composer": "Anon", "license": "Public Domain",
                   "source_url": "https://www.mutopiaproject.org/x", "midi_sha256": "abc"},
        "reference": {"time_signatures": [{"numerator": 3, "denominator": 4}]},
        "practice": {"ready": ready, "rejection_reasons": [] if ready else ["polyphony_above_limit"], "version_id": "v1",
                     "options": {"transpose": -7, "melody": "highest", "bpm": 60},
                     "transformations": {"source_measure_ordinals": [1, 4], "selected_tracks": [1]},
                     "evaluation_policy": {"assess_onsets": True}, "notes": notes},
    }


def note(i, pitch, onset, duration=1.0):
    return {"id": f"t1_n{i}", "source_note_id": f"t1_n{i}", "pitch": pitch, "onset_quarter": onset,
            "duration_quarter": duration, "source_measure_id": "m0001"}


class SongChartTests(unittest.TestCase):
    def test_copies_pitches_and_times_without_changing_them(self):
        c = build_chart(document([note(0, 65, 0), note(1, 64, 1, 2), note(2, 60, 3)]), song_id="s", level=1, title_ko="곡")
        self.assertEqual([(n["midi"], n["key"], n["onset_quarter"], n["duration_quarter"]) for n in c["notes"]],
                         [(65, "F4", 0, 1), (64, "E4", 1, 2), (60, "C4", 3, 1)])
        self.assertEqual(c["keys"], ["C4", "E4", "F4"])
        self.assertEqual(c["time_signature"], [3, 4]); self.assertEqual(c["duration_quarter"], 4)
        self.assertEqual(c["excerpt"]["transpose_semitones"], -7)
        self.assertIn("mutopiaproject.org", c["attribution"]); self.assertEqual(c["min_onset_gap_quarter"], 1)

    def test_rejects_not_ready_black_keys_and_chords(self):
        with self.assertRaisesRegex(ChartError, "ready=false"):
            build_chart(document([note(0, 60, 0)], ready=False), song_id="s", level=1, title_ko="곡")
        with self.assertRaisesRegex(ChartError, "건반"):
            build_chart(document([note(0, 61, 0)]), song_id="s", level=1, title_ko="곡")
        with self.assertRaisesRegex(ChartError, "동시음"):
            build_chart(document([note(0, 60, 0), note(1, 64, 0)]), song_id="s", level=1, title_ko="곡")
        with self.assertRaises(ChartError):
            build_chart({"kind": "other"}, song_id="s", level=1, title_ko="곡")

    def test_end_quarter_records_dropped_notes_and_clips_duration(self):
        c = build_chart(document([note(0, 60, 0), note(1, 62, 1, 3), note(2, 64, 4)]), song_id="s", level=1,
                        title_ko="곡", end_quarter=3)
        self.assertEqual([n["source_note_id"] for n in c["notes"]], ["t1_n0", "t1_n1"])
        self.assertEqual(c["notes"][-1]["duration_quarter"], 2)
        self.assertEqual(c["excerpt"]["excluded_after_end_note_ids"], ["t1_n2"])

    def test_cli_writes_chart_and_sorted_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "score.json"; src.write_text(json.dumps(document([note(0, 60, 0)])))
            out = Path(tmp) / "songs"
            main(["--practice", str(src), "--id", "b", "--level", "2", "--title-ko", "둘", "--output-dir", str(out)])
            main(["--practice", str(src), "--id", "a", "--level", "1", "--title-ko", "하나", "--output-dir", str(out)])
            index = json.loads((out / "index.json").read_text())
            self.assertEqual([s["id"] for s in index["songs"]], ["a", "b"])
            self.assertEqual(json.loads((out / "a.json").read_text())["schema"], "pthoven-song-chart.v1")


if __name__ == "__main__":
    unittest.main()
