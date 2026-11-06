"""다운로드 없이 두 전처리기를 확인하는 합성 입력 생성. 실제 Mutopia/nASAP 자료 아님."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import mido


def midi(path, pitches=(60, 64, 67), performance=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    m = mido.MidiFile(ticks_per_beat=480)
    t = mido.MidiTrack(); m.tracks.append(t)
    t.append(mido.MetaMessage('time_signature', numerator=4, denominator=4))
    t.append(mido.MetaMessage('set_tempo', tempo=500000))
    for i, pitch in enumerate(pitches):
        t.append(mido.Message('note_on', note=pitch, velocity=64, time=20 if performance and i else 0))
        t.append(mido.Message('note_off', note=pitch, time=480))
    t.append(mido.MetaMessage('end_of_track', time=480))
    m.save(path)


def create_examples(output):
    output = Path(output)
    mut = output/'mutopia'; mut.mkdir(parents=True, exist_ok=True)
    midi(mut/'synthetic.mid')
    (mut/'synthetic.ly').write_text('\\header { title = "Synthetic C E G" composer = "Synthetic fixture" copyright = "Synthetic fixture" }\n')
    (mut/'manifest.json').write_text(json.dumps([{'midi_path': 'synthetic.mid', 'title': 'Synthetic C E G',
                                                'license': 'synthetic fixture', 'metadata_verified': False}]))
    nasap = output/'nasap'; piece = nasap/'Synthetic/CEG'; piece.mkdir(parents=True, exist_ok=True)
    midi(piece/'midi_score.mid'); midi(piece/'performance.mid', performance=True)
    xml = '''<?xml version="1.0" encoding="utf-8"?>
<score-partwise version="3.1"><part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>
<note id="n1"><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration><type>quarter</type></note>
<note id="n2"><pitch><step>E</step><octave>4</octave></pitch><duration>1</duration><type>quarter</type></note>
<note id="n3"><pitch><step>G</step><octave>4</octave></pitch><duration>1</duration><type>quarter</type></note>
<note><rest/><duration>1</duration><type>quarter</type></note></measure></part></score-partwise>'''
    (piece/'xml_score.musicxml').write_text(xml)
    (piece/'note_alignment.tsv').write_text('xml_id\tmidi_id\ttrack\tchannel\tpitch\tonset\nn1-1\tn0\t0\t0\t60\t0\nn2-1\tn1\t0\t0\t64\t0.5\nn3-1\tn2\t0\t0\t67\t1\n')
    row = {'composer': 'Synthetic', 'title': 'CEG', 'folder': 'Synthetic/CEG',
           'xml_score': 'Synthetic/CEG/xml_score.musicxml', 'midi_score': 'Synthetic/CEG/midi_score.mid',
           'midi_performance': 'Synthetic/CEG/performance.mid', 'robust_note_alignment': '1.0',
           'note_alignments': 'Synthetic/CEG/note_alignment.tsv'}
    with (nasap/'metadata.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
    (nasap/'asap_annotations.json').write_text(json.dumps({row['midi_performance']: {'midi_score_downbeats': [0], 'downbeats_score_map': [1]}}))
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('outputs/preprocessing_examples'))
    args = parser.parse_args(); create_examples(args.output)
    print(f'합성 예제 입력: {args.output}')
