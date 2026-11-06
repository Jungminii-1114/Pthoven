"""nASAP metadata + score MIDI/MusicXML + note alignment → 학습/연습 기준 JSON.

실행: python preprocessing/preprocess_nasap.py --root data/nasap --output outputs/nasap --limit 1
오디오는 읽지 않는다. 악보 MIDI와 performance MIDI를 구분하고, 연주 타이밍을
악보 정답으로 덮어쓰지 않는다. note alignment는 원본 reference에만 적용한다.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import sys
import warnings
from importlib.metadata import version
from collections import defaultdict
from pathlib import Path

if __package__ in (None, ''): sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from preprocessing.common import add_practice_arguments, options_from_args, read_midi, TempoMap, make_measures, build_document, digest, save_json


def robust_flag(value):
    # 실제 nASAP metadata.csv는 True가 아닌 문자열 '1.0'을 사용한다.
    try: return float(value) == 1
    except (TypeError, ValueError): return False


def local_file(root, rel):
    path = (root/rel).resolve()
    if root.resolve() not in path.parents: raise ValueError('데이터 루트 밖의 경로입니다.')
    if not path.is_file(): raise FileNotFoundError(rel)
    return path


def read_xml_links(path, score):
    """MusicXML ID·성부·반복 순서를 보존하고 runtime MIDI 음표와 교차 확인.

    nASAP xml_id는 n2-1처럼 반복 차수를 포함한다. 문자열 접미사를 잘라서
    합치지 않고 Partitura로 반복을 펼친다. pitch+악보 quarter onset이 유일하게
    일치할 때만 MIDI note_id에 연결한다. 모호한 unison/꾸밈음/반복 차이는
    unresolved로 남기며 억지로 가장 가까운 음에 붙이지 않는다.
    """
    import partitura as pt
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always')
        xml = pt.load_score(str(path))
        part = pt.score.merge_parts(xml)
        unfolded = pt.score.unfold_part_maximal(part)
        arr = unfolded.note_array(include_staff=True, include_grace_notes=True)
    by_pitch = defaultdict(list)
    for n in score['notes']: by_pitch[n['pitch']].append(n)
    rows, mapping = [], {}
    for x in arr:
        note_id, pitch, q = str(x['id']), int(x['pitch']), float(x['onset_quarter'])
        candidates = [n for n in by_pitch[pitch] if abs(n['onset_quarter']-q) <= max(1/score['ticks_per_quarter'], 1e-4)]
        ids = [n['id'] for n in candidates]
        entry = {'xml_id': note_id, 'pitch': pitch, 'onset_quarter': q, 'duration_quarter': float(x['duration_quarter']),
                 'voice': int(x['voice']), 'staff': int(x['staff']), 'runtime_note_ids': ids,
                 'mapping_status': 'unique' if len(ids) == 1 else 'ambiguous' if ids else 'unresolved'}
        rows.append(entry); mapping[note_id] = entry
        if len(candidates) == 1:
            candidates[0].setdefault('musicxml_note_ids', []).append(note_id)
    return {'unfold_policy': 'partitura_unfold_part_maximal', 'notes': rows,
            'mapping_counts': {s: sum(r['mapping_status'] == s for r in rows) for s in ['unique', 'ambiguous', 'unresolved']},
            'warnings': sorted(set(str(w.message) for w in captured))}, mapping


def read_performance(path, alignment_path, xml_map):
    """기존 실제 연주의 note_id와 원본 악보의 대응을 보존한다.

    Partitura의 performance id를 그대로 써서 TSV의 midi_id와 연결한다.
    raw MIDI track 번호와 Partitura의 track 번호는 다를 수 있으므로 track으로
    임의 재조인하지 않는다. deletion/insertion도 분리하여 원본 정답을 유지한다.
    """
    import partitura as pt
    performance = pt.load_performance_midi(str(path)).note_array()
    pnotes = {str(n['id']): {'id': str(n['id']), 'pitch': int(n['pitch']), 'velocity': int(n['velocity']),
                           'onset_seconds': float(n['onset_sec']), 'duration_seconds': float(n['duration_sec']),
                           'track': int(n['track']), 'channel': int(n['channel'])} for n in performance}
    alignment = pt.io.importparangonada.load_alignment_from_ASAP(str(alignment_path))
    result = []
    for row in alignment:
        sid, pid = row.get('score_id'), row.get('performance_id')
        xml = xml_map.get(sid)
        result.append({**row, 'runtime_score_note_ids': xml['runtime_note_ids'] if xml and xml['mapping_status'] == 'unique' else [],
                       'score_mapping_status': xml['mapping_status'] if xml else 'not_applicable' if sid is None else 'xml_id_unresolved',
                       'performance_id_resolved': pid in pnotes if pid is not None else None})
    return {'notes': list(pnotes.values()), 'alignment': result,
            'alignment_counts': {label: sum(r['label'] == label for r in result) for label in ['match', 'insertion', 'deletion']},
            'applies_to': 'unmodified_reference_only', 'source_clock': 'performance_seconds',
            'is_vision_training_ground_truth': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--include-nonrobust', action='store_true')
    parser.add_argument('--composer', help='작곡가 부분 문자열')
    parser.add_argument('--title', help='곡명 부분 문자열')
    parser.add_argument('--limit', type=int, help='처리할 performance 행 수')
    add_practice_arguments(parser)
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1: parser.error('--limit은 1 이상입니다.')
    root = args.root.resolve()
    with local_file(root, 'metadata.csv').open(newline='') as f: rows = list(csv.DictReader(f))
    required = {'xml_score', 'midi_score', 'midi_performance', 'note_alignments', 'robust_note_alignment'}
    if not rows or not required.issubset(rows[0]): parser.error('nASAP 확장 metadata.csv가 필요합니다. ASAP 원본에는 note_alignments 열이 없습니다.')
    rows = [r for r in rows if args.include_nonrobust or robust_flag(r.get('robust_note_alignment'))]
    if args.composer: rows = [r for r in rows if args.composer.lower() in r.get('composer', '').lower()]
    if args.title: rows = [r for r in rows if args.title.lower() in r.get('title', '').lower()]
    if args.limit: rows = rows[:args.limit]
    annotations_file = root/'asap_annotations.json'
    annotations = json.loads(annotations_file.read_text()) if annotations_file.exists() else {}
    documents, xml_maps, failures = {}, {}, []
    for row in rows:
        try:
            midi_path, xml_path = local_file(root, row['midi_score']), local_file(root, row['xml_score'])
            key = row['midi_score']+'|'+row['xml_score']
            if key not in documents:
                # [1] runtime 기준은 score MIDI다. performance MIDI의 루바토·오류를
                # 정답 박자에 복사하지 않는다. XML은 음표 ID·성부·반복 연결에 사용한다.
                score = read_midi(midi_path)
                xml_info, mapping = read_xml_links(xml_path, score)
                ann = annotations.get(row['midi_performance'], {})
                downbeats = ann.get('midi_score_downbeats', [])
                labels = ann.get('downbeats_score_map', [])
                annotated = None
                if downbeats:
                    # [2] nASAP 주석의 시각은 score MIDI의 '초'다. tempo map을 역으로
                    # 통과시켜 quarter로 바꿔야 속도 변경 후에도 마디 위치가 보존된다.
                    clock = TempoMap(score['ticks_per_quarter'], score['tempo_map'])
                    annotated = [(clock.seconds_to_quarter(float(t)), labels[i] if i < len(labels) else None) for i, t in enumerate(downbeats)]
                source = {'dataset': 'nASAP', 'midi_path': row['midi_score'], 'midi_sha256': digest(midi_path),
                          'musicxml_path': row['xml_score'], 'musicxml_sha256': digest(xml_path),
                          'title': row.get('title'), 'composer': row.get('composer'),
                          'license': 'CC BY-NC-SA 4.0 (verify upstream LICENSE for selected files)',
                          'partitura_version': version('partitura'), 'metadata_sha256': digest(root/'metadata.csv'),
                          'measure_annotation_source_performance': row['midi_performance'] if downbeats else None}
                source['score_annotations_sha256'] = digest(annotations_file) if annotations_file.exists() else None
                # [2~4] 원본 구조 → 연습 편곡 → 화면/비교용 schedule은 공통 코드.
                doc = build_document(score, source, options_from_args(args), make_measures(score, annotated=annotated))
                doc['reference']['musicxml'] = xml_info
                doc['performances'] = []
                documents[key] = doc; xml_maps[key] = mapping
            performance_path = local_file(root, row['midi_performance'])
            alignment_path = local_file(root, row['note_alignments'])
            performance = read_performance(performance_path, alignment_path, xml_maps[key])
            performance.update(path=row['midi_performance'], sha256=digest(performance_path),
                               alignment_path=row['note_alignments'], alignment_sha256=digest(alignment_path),
                               robust_note_alignment=robust_flag(row['robust_note_alignment']))
            # 편곡 후 음표를 삭제/이조한 practice에 원본 alignment를 그대로 적용하지
            # 않는다. 원본 reference의 ID 링크만 보존하고, 실제 Vision 라벨과 구분한다.
            documents[key]['performances'].append(performance)
        except Exception as exc:
            failures.append({'performance': row.get('midi_performance'), 'error': f'{type(exc).__name__}: {exc}'})
    catalog = []
    for doc in documents.values():
        if not doc['performances']: continue
        name = 'score-'+doc['practice']['version_id']+'.json'
        save_json(args.output/name, doc)
        catalog.append({'file': name, 'title': doc['source']['title'], 'composer': doc['source']['composer'],
                        'ready': doc['practice']['ready'], 'reasons': doc['practice']['rejection_reasons'],
                        'performance_count': len(doc['performances']), 'xml_mapping': doc['reference']['musicxml']['mapping_counts']})
    save_json(args.output/'catalog.json', {'dataset': 'nASAP', 'scores': catalog, 'failures': failures,
                                          'quality_filter': 'all' if args.include_nonrobust else 'robust_only'})
    print(f'nASAP: {len(catalog)}개 악보, {sum(x["performance_count"] for x in catalog)}개 연주, {len(failures)}개 실패')
    return 2 if not catalog else 1 if failures else 0


if __name__ == '__main__': raise SystemExit(main())
