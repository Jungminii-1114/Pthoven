"""공통 악보 전처리: MIDI 정규화 → 구조화 → 연습 버전 → 화면/비교 데이터.

원본 파일과 reference는 변경하지 않는다. 모든 손실 있는 편곡은 practice에만
적용하고 transformations/excluded_note_ids에 기록한다. 카메라 관측을 정답에
맞춰 수정하는 코드나 실제 접촉 라벨을 생성하는 코드는 포함하지 않는다.
시간의 기본 단위 quarter는 항상 4분음표 1개이며, 6/8의 음악적 박과 구분한다.
"""
from __future__ import annotations

import bisect
import copy
import hashlib
import json
import math
from importlib.metadata import version
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from pathlib import Path

import mido


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


class TempoMap:
    """템포 변경점별로 적분한다. tick·초·quarter를 구분한다."""
    def __init__(self, ppq, events):
        self.ppq = ppq
        changes = {0: 500000}
        for e in events:
            changes[e['tick']] = e['microseconds_per_quarter']
        self.ticks = sorted(changes)
        self.tempos = [changes[t] for t in self.ticks]
        self.seconds = [0.0]
        for i in range(1, len(self.ticks)):
            self.seconds.append(self.seconds[-1] + (self.ticks[i]-self.ticks[i-1])/ppq*self.tempos[i-1]/1e6)

    def to_seconds(self, tick):
        i = max(0, bisect.bisect_right(self.ticks, tick)-1)
        return self.seconds[i] + (tick-self.ticks[i])/self.ppq*self.tempos[i]/1e6

    def seconds_to_quarter(self, seconds):
        i = max(0, bisect.bisect_right(self.seconds, seconds)-1)
        return (self.ticks[i] + (seconds-self.seconds[i])*1e6/self.tempos[i]*self.ppq)/self.ppq


def read_midi(path):
    """[1] 원본 악보 MIDI → 음표·템포·박자 정규화.

    트랙마다 delta tick을 별도로 누적한다. note_on velocity=0도 note_off로
    처리하고, 같은 pitch가 겹칠 때는 FIFO로 대응시킨다. 애매한 겹침·미종료
    음은 진단을 남기며 임의의 정답 길이를 만들어 넣지 않는다.
    페달은 별도 control 데이터로 보존하고 key release 길이에 합치지 않는다.
    """
    mid = mido.MidiFile(str(path))
    if mid.type == 2 or mid.ticks_per_beat <= 0:
        raise ValueError('비동기 type 2/SMPTE MIDI는 지원하지 않습니다. PPQN score MIDI를 사용하세요.')
    notes, tempos, meters, keys, controls, diagnostics, tracks = [], [], [], [], [], [], []
    end_tick = 0
    for ti, track in enumerate(mid.tracks):
        tick, serial, active = 0, 0, defaultdict(deque)
        name = ''
        for msg in track:
            tick += msg.time
            if msg.type == 'track_name':
                name = msg.name
            if msg.type == 'set_tempo':
                if msg.tempo <= 0: raise ValueError('유효하지 않은 MIDI tempo')
                tempos.append({'tick': tick, 'microseconds_per_quarter': msg.tempo})
            elif msg.type == 'time_signature':
                meters.append({'tick': tick, 'numerator': msg.numerator, 'denominator': msg.denominator})
            elif msg.type == 'key_signature':
                keys.append({'tick': tick, 'key': msg.key})
            elif msg.type == 'control_change':
                controls.append({'tick': tick, 'track': ti, 'channel': msg.channel, 'control': msg.control, 'value': msg.value})
            elif msg.type == 'note_on' and msg.velocity > 0:
                k = (msg.channel, msg.note)
                if active[k]: diagnostics.append({'code': 'overlapping_same_pitch_fifo', 'track': ti, 'tick': tick, 'pitch': msg.note})
                active[k].append({'id': f't{ti}_n{serial}', 'pitch': msg.note, 'velocity': msg.velocity,
                                  'track': ti, 'channel': msg.channel, 'onset_tick': tick})
                serial += 1
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                k = (msg.channel, msg.note)
                if not active[k]:
                    diagnostics.append({'code': 'unmatched_note_off', 'track': ti, 'tick': tick, 'pitch': msg.note})
                    continue
                n = active[k].popleft()
                if tick <= n['onset_tick']:
                    diagnostics.append({'code': 'nonpositive_duration', 'note_id': n['id']})
                    continue
                n['offset_tick'] = tick
                notes.append(n)
        for pending in active.values():
            for n in pending: diagnostics.append({'code': 'unclosed_note_on_excluded', 'note_id': n['id']})
        tracks.append({'index': ti, 'name': name})
        end_tick = max(end_tick, tick)
    def changes(events, default):
        result = {}
        for e in sorted(events, key=lambda e: e['tick']):
            if e['tick'] in result and result[e['tick']] != e:
                diagnostics.append({'code': 'conflicting_metadata_same_tick', 'tick': e['tick']})
            result[e['tick']] = e
        result.setdefault(0, default)
        return [result[t] for t in sorted(result)]
    if not meters: diagnostics.append({'code': 'missing_time_signature_assumed_4_4'})
    tempos = changes(tempos, {'tick': 0, 'microseconds_per_quarter': 500000})
    meters = changes(meters, {'tick': 0, 'numerator': 4, 'denominator': 4})
    clock = TempoMap(mid.ticks_per_beat, tempos)
    for n in notes:
        n.update(onset_quarter=n['onset_tick']/mid.ticks_per_beat,
                 duration_quarter=(n['offset_tick']-n['onset_tick'])/mid.ticks_per_beat,
                 onset_seconds=clock.to_seconds(n['onset_tick']), offset_seconds=clock.to_seconds(n['offset_tick']))
    for e in tempos:
        e.update(onset_quarter=e['tick']/mid.ticks_per_beat, quarter_bpm=60e6/e['microseconds_per_quarter'])
    for e in meters: e['onset_quarter'] = e['tick']/mid.ticks_per_beat
    return {'ticks_per_quarter': mid.ticks_per_beat, 'notes': sorted(notes, key=lambda n: (n['onset_tick'], n['pitch'], n['track'])),
            'tempo_map': tempos, 'time_signatures': meters, 'key_signatures': keys, 'controls': controls,
            'tracks': tracks, 'end_quarter': end_tick/mid.ticks_per_beat, 'diagnostics': diagnostics}


def make_measures(score, pickup=None, annotated=None):
    """[2-a] 마디 구성. nASAP의 검수된 downbeat가 있으면 우선 사용한다.

    일반 MIDI에는 못갖춘마디 표기가 없을 수 있다. Mutopia는 --pickup-quarters
    또는 LilyPond의 단순 \\partial로 보완한다. MIDI 박자 변경이 마디 중간이면
    해당 지점에서 짧은 마디를 나누고 inferred/partial 표기로 사실을 보존한다.
    """
    end = score['end_quarter']
    meters = score['time_signatures']
    def meter_at(q): return next(e for e in reversed(meters) if e['onset_quarter'] <= q+1e-8)
    bounds, labels, source = [], {}, 'midi_meter_inferred'
    if annotated:
        source = 'nasap_annotated_downbeats'
        if any(not math.isfinite(float(q)) for q, _ in annotated): raise ValueError('유한하지 않은 downbeat')
        bounds = sorted(set(max(0.0, float(x[0])) for x in annotated if -1e-7 <= x[0] < end-1e-7))
        labels = {round(float(q), 7): label for q, label in annotated}
        if not bounds or bounds[0] > 1e-7: bounds.insert(0, 0.0)
    else:
        q = 0.0
        while q < end-1e-8:
            bounds.append(q)
            m = meter_at(q)
            length = 4*m['numerator']/m['denominator']
            if q == 0 and pickup is not None:
                if not 0 < pickup <= length: raise ValueError('pickup 길이가 박자 범위를 벗어납니다.')
                length = pickup
            future = [e['onset_quarter'] for e in meters if e['onset_quarter'] > q+1e-8]
            q = min(q+length, min(future) if future else math.inf)
    bounds.append(end)
    result = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:]), 1):
        m = meter_at(a)
        result.append({'id': f'm{i:04d}', 'ordinal': i, 'start_quarter': a, 'end_quarter': b,
                       'source_measure_label': labels.get(round(a, 7)), 'time_signature': [m['numerator'], m['denominator']],
                       'boundary_source': source, 'partial': b-a < 4*m['numerator']/m['denominator']-1e-7})
    return result


def group_notes(notes):
    """[2-b] 악보의 동일 onset끼리 동시 발음 그룹을 만든다.

    근접한 시각을 임의의 50ms 창으로 합치지 않는다. 아르페지오·꾸밈음이
    화음으로 바뀌는 것을 막기 위해 quarter 위치를 1e-8 정밀도로만 정리한다.
    이는 사용자 연주를 정답 박자에 양자화하는 기능이 아니다.
    """
    groups = defaultdict(list)
    for n in notes: groups[round(n['onset_quarter'], 8)].append(n)
    return [{'id': f'g{i:05d}', 'onset_quarter': q, 'note_ids': [n['id'] for n in ns],
             'pitches': sorted(n['pitch'] for n in ns)} for i, (q, ns) in enumerate(sorted(groups.items()), 1)]


def rests(notes, start, end):
    # MIDI에는 명시적 쉼표 이벤트가 없으므로, 선택한 음들의 시간 합집합의
    # 여집합을 계산한다. 성부별 기보 쉼표와는 다른 '전체가 쉬는 구간'이다.
    cursor, result = start, []
    for n in sorted(notes, key=lambda n: n['onset_quarter']):
        a, b = max(start, n['onset_quarter']), min(end, n['onset_quarter']+n['duration_quarter'])
        if b <= start or a >= end: continue
        if a > cursor+1e-8: result.append({'start_quarter': cursor, 'end_quarter': a})
        cursor = max(cursor, b)
    if cursor < end-1e-8: result.append({'start_quarter': cursor, 'end_quarter': end})
    return result


def difficulty(notes, groups, duration_seconds):
    events = [(n['onset_quarter'], 1) for n in notes] + [(n['onset_quarter']+n['duration_quarter'], -1) for n in notes]
    active, maximum = 0, 0
    for _, delta in sorted(events):
        active += delta
        maximum = max(maximum, active)
    top = [max(g['pitches']) for g in groups]
    # 곡 전체 평균만 사용하면 긴 쉼 뒤의 짧고 빠른 구간이 가려진다.
    # [t,t+1초) 창의 최대 onset 음표 수를 별도로 저장해 난이도를 판정한다.
    onset_times = sorted(n['onset_seconds'] for n in notes)
    left, peak = 0, 0
    for right, t in enumerate(onset_times):
        while onset_times[left] <= t-1+1e-9: left += 1
        peak = max(peak, right-left+1)
    return {'note_count': len(notes), 'onset_count': len(groups), 'max_polyphony': maximum,
            'pitch_min': min((n['pitch'] for n in notes), default=None), 'pitch_max': max((n['pitch'] for n in notes), default=None),
            'notes_per_second': len(notes)/duration_seconds if duration_seconds > 0 else 0,
            'peak_notes_per_second': peak,
            'max_top_voice_leap_semitones': max((abs(a-b) for a, b in zip(top, top[1:])), default=0)}


def keyboard(low, high, white_only=False):
    # [4-a] 지원 MIDI pitch를 가상 건반의 0~1 사각형 좌표로 변환한다.
    # 이는 실제 책상/카메라 좌표가 아니다. 화면 렌더러가 정한 영역에 비례 배치한다.
    whites = {0: 0, 2: 1, 4: 2, 5: 3, 7: 4, 9: 5, 11: 6}
    black_after = {1: 1, 3: 2, 6: 4, 8: 5, 10: 6}
    rows = []
    for pitch in range(low, high+1):
        pc, octave = pitch % 12, pitch // 12
        black = pc not in whites
        if white_only and black: continue
        width = .62 if black else 1.0
        left = octave*7 + (black_after[pc]-.31 if black else whites[pc])
        rows.append({'key_id': f'midi_{pitch}', 'pitch': pitch, 'is_black': black, '_left': left, '_width': width})
    if not rows: raise ValueError('선택 범위에 지원하는 건반이 없습니다.')
    left = min(r['_left'] for r in rows); span = max(r['_left']+r['_width'] for r in rows)-left
    for r in rows:
        r['rect'] = [(r.pop('_left')-left)/span, 0, r.pop('_width')/span, .62 if r['is_black'] else 1]
        r['z_order'] = 1 if r['is_black'] else 0
    return rows


@dataclass
class PracticeOptions:
    low: int = 60
    high: int = 72
    bpm: float | None = 60
    start_measure: int = 1
    end_measure: int | None = None
    segment_measures: int = 2
    transpose: int = 0
    melody: str = 'all'
    out_of_range: str = 'reject'
    tracks: list | None = None
    max_polyphony: int = 1
    max_density: float = 4
    white_only: bool = False


def build_document(score, source, options, measures=None):
    """파이프라인 [2]~[4]. 원본 reference와 변환된 practice를 분리한다."""
    o = options
    if not (0 <= o.low <= o.high <= 127) or o.segment_measures < 1 or o.max_polyphony < 1:
        raise ValueError('음역/구간/동시음 제한을 확인하세요.')
    if o.bpm is not None and (not math.isfinite(o.bpm) or o.bpm <= 0): raise ValueError('BPM은 양수여야 합니다.')
    if not math.isfinite(o.max_density) or o.max_density <= 0: raise ValueError('밀도 제한은 양수여야 합니다.')
    if not score['notes']: raise ValueError('처리할 음표가 없습니다.')
    measures = measures or make_measures(score)
    reference = copy.deepcopy(score)
    reference['measures'] = measures
    reference['onset_groups'] = group_notes(score['notes'])
    reference['rests'] = rests(score['notes'], 0, score['end_quarter'])
    # [2-d] 음표/그룹이 어느 마디의 어느 위치인지 직접 연결한다. 생성 순번과
    # 원본 MusicXML 마디 라벨(못갖춘마디/반복 포함)은 서로 다른 필드로 보존한다.
    starts = [m['start_quarter'] for m in measures]
    def measure_for(q): return measures[max(0, bisect.bisect_right(starts, q+1e-8)-1)]
    for n in reference['notes']:
        m = measure_for(n['onset_quarter'])
        n['measure_id'] = m['id']
        n['position_in_measure_quarters'] = n['onset_quarter']-m['start_quarter']
        n['denominator_beats_from_measure_start'] = n['position_in_measure_quarters']*m['time_signature'][1]/4
    for g in reference['onset_groups']: g['measure_id'] = measure_for(g['onset_quarter'])['id']
    last = len(measures) if o.end_measure is None else o.end_measure
    if not 1 <= o.start_measure <= last <= len(measures): raise ValueError('선택 마디 범위를 확인하세요.')
    selected = measures[o.start_measure-1:last]
    a, b = selected[0]['start_quarter'], selected[-1]['end_quarter']
    # [3-a] 지원 음역/난이도에 맞는 별도 연습 버전 구성.
    # 범위 밖 음을 가장 가까운 건반으로 몰아넣거나 옥타브 접기 하지 않는다.
    # drop/최상성부 선택은 명시적으로 요청한 경우에만 수행하고 제외 ID를 남긴다.
    kept, excluded, carry, clipped = [], [], [], []
    for n in score['notes']:
        if n['onset_quarter'] < a and n['onset_quarter']+n['duration_quarter'] > a: carry.append(n['id'])
        if not a-1e-8 <= n['onset_quarter'] < b-1e-8: continue
        if n['channel'] == 9 or (o.tracks is not None and n['track'] not in o.tracks):
            excluded.append(n['id']); continue
        p = copy.deepcopy(n)
        p['source_note_id'] = n['id']
        p['source_pitch'] = n['pitch']
        p['source_measure_id'] = measure_for(n['onset_quarter'])['id']
        p['source_onset_tick'] = p.pop('onset_tick')
        p['source_offset_tick'] = p.pop('offset_tick')
        p['pitch'] += o.transpose
        p['onset_quarter'] -= a
        p['duration_quarter'] = min(p['duration_quarter'], b-n['onset_quarter'])
        if p['duration_quarter'] < n['duration_quarter']-1e-8: clipped.append(n['id'])
        kept.append(p)
    if o.melody == 'highest':
        chosen = {max(ns, key=lambda n: (n['pitch'], -n['track']))['id'] for ns in _group_values(kept)}
        excluded.extend(n['id'] for n in kept if n['id'] not in chosen)
        kept = [n for n in kept if n['id'] in chosen]
    keymap = keyboard(o.low, o.high, o.white_only)
    supported = {k['pitch'] for k in keymap}
    outside = [n['id'] for n in kept if n['pitch'] not in supported]
    if o.out_of_range == 'drop':
        excluded.extend(outside); kept = [n for n in kept if n['id'] not in outside]
    # [3-b] quarter 기반 리듬은 유지하고 BPM만 적용한다. 고정 BPM이 없으면
    # 원본 tempo map을 선택 구간의 원점에 맞춰 적분한다. 길이도 같은 시계로 환산한다.
    clock = TempoMap(score['ticks_per_quarter'], score['tempo_map'])
    def seconds(q):
        return q*60/o.bpm if o.bpm is not None else clock.to_seconds((a+q)*score['ticks_per_quarter'])-clock.to_seconds(a*score['ticks_per_quarter'])
    for n in kept:
        n['source_onset_quarter'] = n['onset_quarter']+a
        n['onset_seconds'] = seconds(n['onset_quarter'])
        n['offset_seconds'] = seconds(n['onset_quarter']+n['duration_quarter'])
        n['key_id'] = f"midi_{n['pitch']}" if n['pitch'] in supported else None
        # MIDI velocity는 보존 정보일 뿐 RGB에서 관측한 힘/velocity 정답이 아니다.
    groups = group_notes(kept)
    for g in groups: g['onset_seconds'] = seconds(g['onset_quarter'])
    for g in groups: g['source_measure_id'] = measure_for(g['onset_quarter']+a)['id']
    metrics = difficulty(kept, groups, seconds(b-a))
    reasons = []
    if outside and o.out_of_range == 'reject': reasons.append('unsupported_pitch_range')
    if not kept: reasons.append('no_practice_notes')
    if metrics['max_polyphony'] > o.max_polyphony: reasons.append('polyphony_above_limit')
    if metrics['peak_notes_per_second'] > o.max_density: reasons.append('note_density_above_limit')
    # [2-c/4-b] 반복할 마디 구간과 화면 표시용 이벤트를 만든다.
    # 음을 마디 경계에서 새로 친 것으로 복제하지 않는다. 지속음은 원래 ID로 유지한다.
    segments = []
    for i in range(0, len(selected), o.segment_measures):
        ms = selected[i:i+o.segment_measures]; start, end = ms[0]['start_quarter']-a, ms[-1]['end_quarter']-a
        segments.append({'id': f's{len(segments)+1:04d}', 'measure_ids': [m['id'] for m in ms],
                         'start_quarter': start, 'end_quarter': end, 'start_seconds': seconds(start), 'end_seconds': seconds(end),
                         'onset_group_ids': [g['id'] for g in groups if start-1e-8 <= g['onset_quarter'] < end-1e-8]})
    transformations = {'quarter_bpm_override': o.bpm, 'transpose_semitones': o.transpose,
                       'melody_policy': o.melody, 'pitch_range_policy': o.out_of_range,
                       'source_measure_ordinals': [o.start_measure, last], 'selected_tracks': o.tracks,
                       'excluded_note_ids': excluded, 'excluded_carry_in_note_ids': carry,
                       'clipped_duration_note_ids': clipped}
    return {'schema_version': 1, 'preprocessor_version': '1.0.0', 'kind': 'pthoven_practice_score', 'source': source,
            'runtime': {'mido': version('mido')},
            'reference': reference,
            'practice': {'version_id': hashlib.sha256(json.dumps({'preprocessor_version': '1.0.0', 'source': source, 'options': asdict(o)}, sort_keys=True).encode()).hexdigest()[:12],
                         'options': asdict(o), 'transformations': transformations, 'origin_source_quarter': a,
                         'ready': not reasons, 'rejection_reasons': reasons,
                         'notes': kept, 'onset_groups': groups, 'segments': segments,
                         'rests': rests(kept, 0, b-a), 'difficulty': metrics,
                         'keyboard': keymap, 'duration_quarter': b-a, 'duration_seconds': seconds(b-a),
                         'evaluation_policy': {'assess_onsets': True, 'assess_duration': False, 'assess_velocity': False,
                                               'observations_must_not_be_snapped_to_reference': True},
                         'readiness_scope': 'configured_pitch_polyphony_density_only_not_pedagogical_validation'}}


def _group_values(notes):
    groups = defaultdict(list)
    for n in notes: groups[round(n['onset_quarter'], 8)].append(n)
    return groups.values()


def add_practice_arguments(parser):
    parser.add_argument('--low', type=int, default=60, help='지원 최저 MIDI 음')
    parser.add_argument('--high', type=int, default=72, help='지원 최고 MIDI 음')
    parser.add_argument('--bpm', type=float, default=60, help='4분음표 기준 연습 BPM')
    parser.add_argument('--original-tempo', action='store_true', help='고정 BPM 대신 원본 tempo map 사용')
    parser.add_argument('--start-measure', type=int, default=1, help='1부터 시작하는 생성 마디 순번')
    parser.add_argument('--end-measure', type=int)
    parser.add_argument('--segment-measures', type=int, default=2)
    parser.add_argument('--transpose', type=int, default=0, help='반음 단위 이조, 원본 불변')
    parser.add_argument('--melody', choices=['all', 'highest'], default='all')
    parser.add_argument('--out-of-range', choices=['reject', 'drop'], default='reject')
    parser.add_argument('--tracks', help='0부터 시작하는 MIDI track 번호, 예: 1,2')
    parser.add_argument('--max-polyphony', type=int, default=1)
    parser.add_argument('--max-density', type=float, default=4, help='1초 창 내 최대 onset 음표 수')
    parser.add_argument('--white-only', action='store_true', help='지원 음역 안에서도 흰 건반만 허용')


def options_from_args(args):
    return PracticeOptions(low=args.low, high=args.high, bpm=None if args.original_tempo else args.bpm,
                           start_measure=args.start_measure, end_measure=args.end_measure, segment_measures=args.segment_measures,
                           transpose=args.transpose, melody=args.melody, out_of_range=args.out_of_range,
                           tracks=[int(v) for v in args.tracks.split(',')] if args.tracks else None,
                           max_polyphony=args.max_polyphony, max_density=args.max_density, white_only=args.white_only)
