"""Mutopia MIDI/LilyPond → 정규화 악보와 독립 연습 버전 JSON.

실행: python preprocessing/preprocess_mutopia.py --input data/mutopia --output outputs/mutopia
LilyPond/PDF를 MIDI로 바꿔주는 프로그램은 아니다. 내려받은 .mid/.midi가 필요하다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if __package__ in (None, ''): sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from preprocessing.common import add_practice_arguments, options_from_args, read_midi, make_measures, build_document, digest, save_json


def lilypond_metadata(path):
    # Mutopia는 MusicXML 대신 LilyPond 원본을 제공한다. 코드를 실행하지 않고
    # 간단한 문자열 header만 읽는다. markup/변수/include를 임의 평가하지 않는다.
    # 복잡한 표제/라이선스는 --manifest의 검수된 값으로 보완한다.
    text = strip_comments(Path(path).read_text(errors='replace'))
    result = {}
    for field in ['title', 'composer', 'mutopiatitle', 'mutopiacomposer', 'mutopiainstrument', 'license', 'copyright', 'source']:
        match = re.search(r'\b'+field+r'\s*=\s*"((?:\\.|[^"\\])*)"', text)
        if match: result[field] = match.group(1)
    # 단순 \\partial 4. 등은 quarter 단위로 환산한다. 배수/변수 표현은 추측하지 않는다.
    partial = re.search(r'\\partial\s+(\d+)(\.*)(?![\w.*])', text)
    if partial:
        result['pickup_quarters'] = 4/int(partial.group(1))*sum(.5**i for i in range(len(partial.group(2))+1))
    elif '\\partial' in text: result['pickup_needs_manual_review'] = True
    return result


def strip_comments(text):
    """문자열 안의 %는 보존하고 LilyPond 주석 속 가짜 header/partial은 제외."""
    out, i, quoted, block = [], 0, False, 0
    while i < len(text):
        if block:
            if text.startswith('%{', i): block += 1; i += 2
            elif text.startswith('%}', i): block -= 1; i += 2
            else: i += 1
        elif quoted:
            out.append(text[i])
            if text[i] == '\\' and i+1 < len(text): i += 1; out.append(text[i])
            elif text[i] == '"': quoted = False
            i += 1
        elif text.startswith('%{', i): block = 1; out.append(' '); i += 2
        elif text[i] == '%':
            while i < len(text) and text[i] != '\n': i += 1
        else:
            if text[i] == '"': quoted = True
            out.append(text[i]); i += 1
    return ''.join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='MIDI 파일 또는 로컬 데이터 폴더')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, help='곡별 검수 메타데이터 JSON, MIDI 경로는 input 폴더 기준')
    parser.add_argument('--pickup-quarters', type=float, help='같은 못갖춘마디를 일괄 적용할 때만 사용')
    parser.add_argument('--limit', type=int)
    add_practice_arguments(parser)
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1: parser.error('--limit은 1 이상입니다.')
    root = args.input if args.input.is_dir() else args.input.parent
    if not args.input.exists(): parser.error('input 경로가 없습니다.')
    manifest = json.loads(args.manifest.read_text()) if args.manifest else []
    if not isinstance(manifest, list): parser.error('manifest는 곡 객체의 JSON 배열이어야 합니다.')
    by_path = {str(Path(item['midi_path'])): item for item in manifest}
    files = [args.input] if args.input.is_file() else sorted(p for p in root.rglob('*') if p.suffix.lower() in {'.mid', '.midi'})
    if args.limit: files = files[:args.limit]
    catalog, failures = [], []
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            # [1] Mutopia의 생성 MIDI에서 음표·tempo/meter를 읽고 원본 hash를 보존.
            score = read_midi(path)
            meta = by_path.get(rel, {})
            ly = root/meta['lilypond_path'] if meta.get('lilypond_path') else path.with_suffix('.ly')
            header = lilypond_metadata(ly) if ly.is_file() else {}
            pickup = args.pickup_quarters if args.pickup_quarters is not None else meta.get('pickup_quarters', header.get('pickup_quarters'))
            if header.get('pickup_needs_manual_review') and pickup is None:
                raise ValueError('복잡한 LilyPond partial: manifest에 pickup_quarters를 지정하세요.')
            source = {'dataset': 'Mutopia', 'midi_path': rel, 'midi_sha256': digest(path),
                      'title': meta.get('title', header.get('mutopiatitle', header.get('title', path.stem))),
                      'composer': meta.get('composer', header.get('mutopiacomposer', header.get('composer'))),
                      'license': meta.get('license', header.get('license', header.get('copyright', 'unknown'))),
                      'source_url': meta.get('source_url'), 'lilypond_header': header,
                      'lilypond_sha256': digest(ly) if ly.is_file() else None,
                      'metadata_verified': bool(meta.get('metadata_verified', False))}
            # [2] 마디·화음·쉼·반복 구간은 common에서 생성한다. 못갖춘마디가
            # 확인되지 않으면 첫 마디 0부터 박자 기반으로 추정했다는 사실을 남긴다.
            measures = make_measures(score, pickup=pickup)
            # [3] 이조/범위 선택/최상음 선택은 practice에만 적용. [4] 화면 key_id와
            # expected onset 그룹을 생성하되 실제 Vision 관측값은 여기서 만들지 않는다.
            document = build_document(score, source, options_from_args(args), measures)
            name = path.stem+'-'+document['practice']['version_id']+'.json'
            save_json(args.output/name, document)
            catalog.append({'file': name, 'source_path': rel, 'ready': document['practice']['ready'],
                            'reasons': document['practice']['rejection_reasons'], 'title': source['title']})
        except Exception as exc:
            failures.append({'path': rel, 'error': f'{type(exc).__name__}: {exc}'})
    save_json(args.output/'catalog.json', {'dataset': 'Mutopia', 'scores': catalog, 'failures': failures})
    print(f'Mutopia: {len(catalog)}곡 변환, {sum(x["ready"] for x in catalog)}곡 범위/난이도 통과, {len(failures)}곡 실패')
    return 2 if not catalog else 1 if failures else 0


if __name__ == '__main__': raise SystemExit(main())
