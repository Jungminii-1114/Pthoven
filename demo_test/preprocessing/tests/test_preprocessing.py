"""악보/템포/마디/편곡 보존과 데이터셋별 독립 CLI를 확인한다. 촬영 성능 테스트 아님."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import mido
from preprocessing.common import read_midi, TempoMap, make_measures, build_document, PracticeOptions, keyboard, difficulty, group_notes
from preprocessing.preprocess_mutopia import lilypond_metadata, main as mutopia_main
from preprocessing.preprocess_nasap import robust_flag, read_xml_links, read_performance, main as nasap_main
from preprocessing.make_examples import create_examples


class PreprocessingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        create_examples(self.root)
        self.path = self.root/'mutopia/synthetic.mid'

    def tearDown(self): self.tmp.cleanup()

    def score(self): return read_midi(self.path)

    def test_piecewise_tempo_integral_and_inverse(self):
        t = TempoMap(480, [{'tick': 0, 'microseconds_per_quarter': 500000}, {'tick': 480, 'microseconds_per_quarter': 1000000}])
        self.assertAlmostEqual(t.to_seconds(960), 1.5)
        self.assertAlmostEqual(t.seconds_to_quarter(1.5), 2)

    def test_track_local_delta_times_and_note_on_zero(self):
        m = mido.MidiFile(ticks_per_beat=480)
        for pitch, start in [(60, 480), (64, 960)]:
            t = mido.MidiTrack(); m.tracks.append(t)
            t.extend([mido.Message('note_on', note=pitch, time=start), mido.Message('note_on', note=pitch, velocity=0, time=480)])
        m.save(self.path)
        self.assertEqual([n['onset_quarter'] for n in self.score()['notes']], [1, 2])
        self.assertEqual([n['duration_quarter'] for n in self.score()['notes']], [1, 1])

    def test_unclosed_notes_are_not_fabricated(self):
        m = mido.MidiFile(); t = mido.MidiTrack(); m.tracks.append(t)
        t.append(mido.Message('note_on', note=60)); m.save(self.path)
        s = self.score(); self.assertEqual(s['notes'], [])
        self.assertEqual(s['diagnostics'][0]['code'], 'unclosed_note_on_excluded')

    def test_type_two_midi_is_rejected(self):
        m = mido.MidiFile(type=2); m.tracks.append(mido.MidiTrack()); m.save(self.path)
        with self.assertRaises(ValueError): self.score()

    def test_compound_meter_quarters_not_six_quarters(self):
        s = self.score(); s['time_signatures'][0].update(numerator=6, denominator=8); s['end_quarter'] = 6
        self.assertEqual([m['end_quarter'] for m in make_measures(s)], [3, 6])

    def test_pickup_and_annotated_measure_labels(self):
        s = self.score(); s['end_quarter'] = 5
        self.assertEqual([m['end_quarter'] for m in make_measures(s, pickup=1)], [1, 5])
        bars = make_measures(s, annotated=[(1, 0), (3, '1-2')])
        self.assertEqual(bars[0]['start_quarter'], 0)
        self.assertEqual(bars[2]['source_measure_label'], '1-2')

    def test_reference_immutable_while_practice_is_transposed_and_retimed(self):
        s = self.score(); before = copy.deepcopy(s)
        d = build_document(s, {'dataset': 'synthetic'}, PracticeOptions(low=60, high=84, transpose=12, bpm=60))
        self.assertEqual(s, before)
        self.assertEqual(d['reference']['notes'][0]['pitch'], 60)
        self.assertEqual(d['practice']['notes'][0]['pitch'], 72)
        self.assertEqual(d['practice']['notes'][1]['onset_seconds'], 1)
        self.assertEqual(d['practice']['notes'][1]['source_pitch'], 64)
        self.assertEqual(d['reference']['notes'][1]['measure_id'], 'm0001')

    def test_range_reject_and_explicit_drop_preserve_source(self):
        reject = build_document(self.score(), {}, PracticeOptions(low=60, high=64))
        drop = build_document(self.score(), {}, PracticeOptions(low=60, high=64, out_of_range='drop'))
        self.assertFalse(reject['practice']['ready']); self.assertTrue(drop['practice']['ready'])
        self.assertEqual(len(drop['reference']['notes']), 3)
        self.assertEqual(len(drop['practice']['transformations']['excluded_note_ids']), 1)

    def test_white_only_has_five_keys_and_rejects_sharps(self):
        self.assertEqual([k['pitch'] for k in keyboard(60, 67, True)], [60, 62, 64, 65, 67])
        s = self.score(); s['notes'][0]['pitch'] = 61
        d = build_document(s, {}, PracticeOptions(low=60, high=67, white_only=True))
        self.assertFalse(d['practice']['ready'])
        self.assertIsNone(d['practice']['notes'][0]['key_id'])

    def test_long_note_crossing_cut_is_not_recreated_as_a_new_press(self):
        s = self.score(); s['end_quarter'] = 8
        s['notes'][0]['duration_quarter'] = 6
        d = build_document(s, {}, PracticeOptions(start_measure=2))
        self.assertEqual(d['practice']['notes'], [])
        self.assertEqual(d['practice']['transformations']['excluded_carry_in_note_ids'], [s['notes'][0]['id']])

    def test_near_onsets_are_not_silently_merged(self):
        s = self.score(); s['notes'][1]['onset_quarter'] = .01
        self.assertEqual(len(group_notes(s['notes'])), 3)

    def test_burst_density_not_hidden_by_long_rest(self):
        s = self.score()
        ns = [dict(s['notes'][0], id=str(i), onset_quarter=i/10, onset_seconds=i/10) for i in range(8)]
        d = difficulty(ns, group_notes(ns), 100)
        self.assertEqual(d['peak_notes_per_second'], 8)
        self.assertLess(d['notes_per_second'], 1)

    def test_highest_onset_policy_does_not_promise_monophony(self):
        s = self.score(); s['notes'][0]['duration_quarter'] = 2.5
        d = build_document(s, {}, PracticeOptions(melody='highest', max_polyphony=1))
        self.assertIn('polyphony_above_limit', d['practice']['rejection_reasons'])

    def test_lilypond_comments_and_dotted_pickup(self):
        p = self.path.with_suffix('.ly')
        p.write_text('% \\partial 2\n%{ \\partial 1 %}\n\\header {title="100% music"}\n\\partial 4.')
        meta = lilypond_metadata(p)
        self.assertEqual(meta['title'], '100% music')
        self.assertEqual(meta['pickup_quarters'], 1.5)

    def test_two_datasets_run_independently_on_synthetic_formats(self):
        self.assertEqual(mutopia_main(['--input', str(self.root/'mutopia'), '--output', str(self.root/'mut-out'), '--white-only', '--high', '67']), 0)
        self.assertEqual(nasap_main(['--root', str(self.root/'nasap'), '--output', str(self.root/'nasap-out'), '--white-only', '--high', '67']), 0)
        for folder in ['mut-out', 'nasap-out']:
            catalog = json.loads((self.root/folder/'catalog.json').read_text())
            doc = json.loads((self.root/folder/catalog['scores'][0]['file']).read_text())
            self.assertTrue(doc['practice']['ready'])
            self.assertEqual([n['pitch'] for n in doc['practice']['notes']], [60, 64, 67])
        self.assertEqual(doc['reference']['musicxml']['mapping_counts']['unique'], 3)
        self.assertEqual(doc['performances'][0]['alignment_counts']['match'], 3)
        self.assertEqual(doc['performances'][0]['applies_to'], 'unmodified_reference_only')

    def test_robust_quality_accepts_real_csv_decimal_format(self):
        self.assertTrue(robust_flag('1.0')); self.assertFalse(robust_flag('nan')); self.assertFalse(robust_flag('0.0'))

    def test_nasap_insertions_deletions_are_not_changed_into_matches(self):
        folder = self.root/'nasap/Synthetic/CEG'
        _, mapping = read_xml_links(folder/'xml_score.musicxml', read_midi(folder/'midi_score.mid'))
        tsv = folder/'note_alignment.tsv'
        tsv.write_text('xml_id\tmidi_id\ttrack\tchannel\tpitch\tonset\nn1-1\tn0\t0\t0\t60\t0\nn2-1\tdeletion\t0\t0\t64\t0.5\ninsertion\tn1\t0\t0\t64\t0.5\n')
        perf = read_performance(folder/'performance.mid', tsv, mapping)
        self.assertEqual(perf['alignment_counts'], {'match': 1, 'insertion': 1, 'deletion': 1})
        self.assertEqual(perf['alignment'][2]['runtime_score_note_ids'], [])

    def test_excerpt_duration_clipping_is_explicit(self):
        s = self.score(); s['end_quarter'] = 8; s['notes'][0]['duration_quarter'] = 6
        d = build_document(s, {}, PracticeOptions(end_measure=1, max_polyphony=3))
        self.assertEqual(d['practice']['notes'][0]['duration_quarter'], 4)
        self.assertEqual(d['reference']['notes'][0]['duration_quarter'], 6)
        self.assertEqual(d['practice']['transformations']['clipped_duration_note_ids'], [s['notes'][0]['id']])


if __name__ == '__main__': unittest.main()
