"""B-02 계약·합성 테스트 (B02-01~12, INV-01~10). 합성 손은 수학 검증용이며 사람 정확도가 아니다."""
import copy
import unittest

import numpy as np
from pthoven_tap.examples import analytic_hand

from pthoven_vision.b02 import CoordinateBuffer, InputRun, map_virtual_note
from pthoven_vision.contracts import (AckEvent, AckRequest, B01Error, CaptureRequest, DeviceRequest,
                                      FrameRequest, GeometryRequest)
from pthoven_vision.geometry import build_geometry
from pthoven_vision.policy import Policy
from pthoven_vision.replay import replay_input_run
from pthoven_vision.session import B01

# analytic_hand 검지 끝 영상 좌표 ≈ (0.43, 0.50) → 640×360에서 (275, 180) → 8건반(폭 80) 중 F4.
KEYBOARD = {'left': 0, 'top': 100, 'width': 640, 'height': 150}


class B02SessionTests(unittest.TestCase):
    def setUp(self, keyboard=KEYBOARD, fingers=None):
        self.time = [100.0]
        self.engine = B01(Policy(preparation_ms=1, min_collect_ms=200, max_collect_ms=1000,
                                 min_valid_frames=8, ready_stable_ms=80), now=lambda: self.time[0])
        req = {'clock_id': 'clock-a', 'selected_device_id': 'iphone'}
        if fingers:
            req['active_fingers'] = fingers
        self.sid = self.engine.create(req)['data']['session_id']
        self.s = self.engine.get(self.sid)
        self.engine.device(self.sid, DeviceRequest(command_epoch=self.s.epoch, permission='granted', stream='receiving',
                           width=640, height=360), {'state': 'ready', 'model_ref': 'synthetic'})
        self.engine.set_geometry(self.sid, GeometryRequest(command_epoch=self.s.epoch, expected_geometry_version=0,
            geometry={'source_size': {'width': 640, 'height': 360}, 'display_size': {'width': 640, 'height': 360},
                      'keyboard_rect': keyboard}))
        self.t = 0; self.i = 0
        self.rng = np.random.default_rng(5)

    def hand(self, lifts, noise=0., shift=(0., 0.)):
        world = np.asarray(analytic_hand(lifts)) + (self.rng.normal(0, noise, (21, 3)) if noise else 0)
        image = [[.5 + p[0] * .1 + shift[0], .6 - p[1] * .1 + shift[1]] for p in world]
        return [{'handedness': 'right', 'image_xy': image, 'world_landmarks': world.tolist()}]

    def send(self, lift=0, hands=None, capture=None, finger='index', dt=40, **kw):
        self.t += dt; self.i += 1; self.time[0] += dt / 1000
        request = FrameRequest(command_epoch=self.s.epoch, geometry_version=self.s.geometry_version,
            calibration_version=self.s.calibration_version, clock_id='clock-a', frame_id=self.i, t_ms=self.t,
            image='fixture', capture_id=capture)
        return self.engine.process_observation(self.sid, request, hands if hands is not None else self.hand({finger: lift}, **kw))

    def calibrate(self, fingers=('index',)):
        self.send()
        cap = self.engine.start_capture(self.sid, CaptureRequest(command_epoch=self.s.epoch, phase='rest', finger=fingers[0]))['data']['capture_id']
        for _ in range(12): self.send(capture=cap)
        self.assertEqual(self.engine.finish_capture(self.sid, cap, self.s.epoch)['outcome'], 'rest_saved')
        for f in fingers:
            self.send()
            cap = self.engine.start_capture(self.sid, CaptureRequest(command_epoch=self.s.epoch, phase='lift', finger=f))['data']['capture_id']
            for _ in range(12): self.send(1, capture=cap, finger=f)
            self.assertEqual(self.engine.finish_capture(self.sid, cap, self.s.epoch)['outcome'], 'calibrated')
        for _ in range(4): self.send()
        self.assertEqual(self.engine.ready(self.sid, self.s.epoch)['outcome'], 'ready_for_observation')

    def start(self):
        response = self.engine.start_input(self.sid, self.s.epoch)
        self.assertEqual(response['outcome'], 'input_running')
        return response

    def tap_cycles(self, n, finger='index', rest_frames=8, lift_profile=(.4, 1, 1, 1, .4), **kw):
        proposals = []
        for _ in range(n):
            for _ in range(rest_frames):
                proposals += self.send(finger=finger, **kw)['data']['input']['proposals']
            for lift in lift_profile:
                proposals += self.send(lift, finger=finger, **kw)['data']['input']['proposals']
        for _ in range(rest_frames):
            proposals += self.send(finger=finger, **kw)['data']['input']['proposals']
        return proposals

    # B01-10 ---------------------------------------------------------------
    def test_start_requires_ready_and_calibration(self):
        self.send()
        self.assertEqual(self.engine.start_input(self.sid, self.s.epoch)['outcome'], 'hold_required')
        self.assertIsNone(self.s.run)

    # B02-05·06·07 -----------------------------------------------------------
    def test_tap_cycles_emit_one_provisional_note_each_with_candidate_frame_coordinates(self):
        self.calibrate(); self.start()
        proposals = self.tap_cycles(5)
        self.assertEqual(len(proposals), 5)
        for p in proposals:
            self.assertEqual(p['input_status'], 'accepted_provisional')
            self.assertEqual((p['virtual_key'], p['midi_note']), ('F4', 65))
            self.assertIsNone(p['emitted_ms'])
            self.assertFalse(p['contact_verified']); self.assertEqual(p['kind'], 'pose_tap_candidate')
            row = self.s.run.buffer.at(p['t_ms'])
            self.assertEqual(row['frame_id'], p['candidate_frame_id'])
        self.assertEqual(len({p['event_id'] for p in proposals}), 5)

    def test_static_and_jittered_rest_never_emit(self):
        self.calibrate(); self.start()
        out = []
        for _ in range(300):
            out += self.send(noise=.003)['data']['input']['proposals']
        self.assertEqual(out, [])

    def test_hand_loss_mid_lift_discards_cycle(self):
        self.calibrate(); self.start()
        out = []
        for _ in range(8): out += self.send()['data']['input']['proposals']
        for lift in (.4, 1, 1): out += self.send(lift)['data']['input']['proposals']
        out += self.send(hands=[])['data']['input']['proposals']          # HAND_NOT_OBSERVED
        for _ in range(10): out += self.send()['data']['input']['proposals']
        self.assertEqual(out, [])

    def test_tracking_gap_does_not_join_two_half_cycles(self):
        self.calibrate(); self.start()
        out = []
        for _ in range(8): out += self.send()['data']['input']['proposals']
        for lift in (.4, 1, 1): out += self.send(lift)['data']['input']['proposals']
        out += self.send(dt=400)['data']['input']['proposals']           # 400ms 공백 뒤 복귀 자세
        for _ in range(10): out += self.send()['data']['input']['proposals']
        self.assertEqual(out, [])

    # B02-07 매핑 보류 -------------------------------------------------------
    def test_outside_keyboard_is_withheld_without_note(self):
        self.setUp(keyboard={'left': 0, 'top': 250, 'width': 640, 'height': 100})
        self.calibrate(); self.start()
        proposals = self.tap_cycles(3)
        self.assertEqual(len(proposals), 3)
        for p in proposals:
            self.assertEqual(p['input_status'], 'withheld')
            self.assertEqual(p['quality_flags'], ['OUTSIDE_KEYBOARD'])
            self.assertIsNone(p['midi_note']); self.assertIsNone(p['virtual_key'])

    def test_key_boundary_is_withheld(self):
        # 검지 끝 x≈275px. 키 경계를 그 위치에 두면(폭 550 → 키폭 68.75, 4번째 경계 275) 보류.
        self.setUp(keyboard={'left': 0, 'top': 100, 'width': 550, 'height': 150})
        self.calibrate(); self.start()
        proposals = self.tap_cycles(2)
        self.assertTrue(proposals)
        self.assertTrue(all(p['quality_flags'] == ['KEY_BOUNDARY'] and p['midi_note'] is None for p in proposals))

    def test_map_requires_exact_candidate_frame(self):
        geometry = build_geometry({'source_size': {'width': 640, 'height': 360}, 'display_size': {'width': 640, 'height': 360},
                                   'keyboard_rect': KEYBOARD})
        buf = CoordinateBuffer()
        buf.add({'t_ms': 100., 'frame_id': 3, 'tips': {'index': [.43, .5]}, 'geometry_version': 1})
        cand = {'candidate_id': 'tap-x', 'session_id': 's', 'clock_id': 'c', 't_ms': 140., 'candidate_frame_id': 4,
                'track_id': 'h', 'finger': 'index', 'calibration_version': 1, 'geometry_version': 1, 'config_version': 1,
                'command_epoch': 1, 'model_ref': 'm'}
        self.assertEqual(map_virtual_note(cand, buf, geometry)['quality_flags'], ['COORDINATE_UNAVAILABLE'])
        cand.update(t_ms=100., candidate_frame_id=3)
        ok = map_virtual_note(cand, buf, geometry)
        self.assertEqual((ok['input_status'], ok['midi_note']), ('accepted_provisional', 65))
        geometry2 = dict(geometry, geometry_version=2)
        self.assertEqual(map_virtual_note(cand, buf, geometry2)['quality_flags'], ['STALE_GEOMETRY'])

    def test_flip_x_maps_through_same_geometry(self):
        geometry = build_geometry({'source_size': {'width': 640, 'height': 360}, 'display_size': {'width': 640, 'height': 360},
                                   'keyboard_rect': KEYBOARD, 'flip_x': True})
        buf = CoordinateBuffer(); buf.add({'t_ms': 1., 'frame_id': 1, 'tips': {'index': [.43, .5]}, 'geometry_version': 1})
        cand = {'candidate_id': 'tap-y', 'session_id': 's', 'clock_id': 'c', 't_ms': 1., 'candidate_frame_id': 1, 'track_id': 'h',
                'finger': 'index', 'calibration_version': 1, 'geometry_version': 1, 'config_version': 1, 'command_epoch': 1, 'model_ref': 'm'}
        # 좌우 반전: x=0.43 → 표시 x=640·0.57=364.8 → G4.
        self.assertEqual(map_virtual_note(cand, buf, geometry)['virtual_key'], 'G4')

    # B02-08 게시·중복 ------------------------------------------------------
    def test_ack_finalizes_once_and_keeps_first_emitted_ms(self):
        self.calibrate(); self.start()
        p = self.tap_cycles(1)[0]
        ack = lambda emitted, ref=p['decision_ref'], eid=p['event_id']: self.engine.acknowledge(self.sid, AckRequest(
            command_epoch=self.s.epoch, decision_ref=ref, clock_id='clock-a', events=[AckEvent(event_id=eid, emitted_ms=emitted)]))
        with self.assertRaises(B01Error) as early:
            ack(p['t_ms'] - 1)
        self.assertEqual(early.exception.code, 'EMITTED_BEFORE_CANDIDATE')
        first = ack(p['t_ms'] + 30)['data']['events'][0]
        self.assertEqual(first['schema_version'], 'virtual-input.v1')
        self.assertEqual(first['emitted_ms'], p['t_ms'] + 30)
        self.assertEqual(ack(p['t_ms'] + 99)['data']['events'][0]['emitted_ms'], p['t_ms'] + 30)
        self.assertEqual(len(self.s.run.final_events), 1)
        with self.assertRaises(B01Error): ack(p['t_ms'] + 30, ref='decision-unknown')
        with self.assertRaises(B01Error): ack(p['t_ms'] + 30, eid='evt-unknown')
        with self.assertRaises(B01Error):
            self.engine.acknowledge(self.sid, AckRequest(command_epoch=self.s.epoch, decision_ref=p['decision_ref'],
                clock_id='other-clock', events=[AckEvent(event_id=p['event_id'], emitted_ms=p['t_ms'] + 30)]))

    # B02-09·10 watchdog / 중단 ----------------------------------------------
    def test_frame_stall_ends_run_and_blocks_old_epoch(self):
        self.calibrate(); self.start()
        self.tap_cycles(1)
        epoch = self.s.epoch
        self.time[0] += 1
        snap = self.engine.snapshot(self.sid)['data']
        self.assertEqual(snap['lifecycle_phase'], 'hold')
        self.assertIsNone(self.s.run)
        self.assertEqual(self.s.run_history[-1]['ended_reason'], 'FRAME_STALLED')
        self.assertGreater(self.s.epoch, epoch)
        with self.assertRaises(B01Error):
            self.engine.process_observation(self.sid, FrameRequest(command_epoch=epoch, geometry_version=self.s.geometry_version,
                calibration_version=self.s.calibration_version, clock_id='clock-a', frame_id=self.i + 1, t_ms=self.t + 40, image='x'),
                self.hand({}))

    def test_pause_discards_run_and_restart_needs_ready(self):
        self.calibrate(); self.start()
        self.tap_cycles(1)
        stopped = self.engine.stop_input(self.sid, self.s.epoch)
        self.assertEqual(stopped['outcome'], 'input_stopped')
        self.assertEqual(stopped['data']['last_run']['counts']['candidates'], 1)
        self.assertEqual(self.engine.start_input(self.sid, self.s.epoch)['outcome'], 'hold_required')

    def test_recalibration_request_stops_input(self):
        self.calibrate(); self.start()
        self.engine.request_recalibration(self.sid, self.s.epoch)
        self.assertIsNone(self.s.run)
        self.assertEqual(self.s.phase, 'calibrating')

    # B02-11 동시 후보 --------------------------------------------------------
    def test_two_active_fingers_keep_independent_candidates(self):
        self.setUp(fingers=['index', 'middle'])
        self.calibrate(('index', 'middle')); self.start()
        out = []
        for _ in range(8): out += self.send(hands=self.hand({}))['data']['input']['proposals']
        for lift in (.4, 1, 1, 1, .4):
            out += self.send(hands=self.hand({'index': lift, 'middle': lift}))['data']['input']['proposals']
        for _ in range(8): out += self.send(hands=self.hand({}))['data']['input']['proposals']
        self.assertEqual(sorted(p['finger'] for p in out), ['index', 'middle'])
        self.assertEqual(len({p['candidate_id'] for p in out}), 2)

    # B02-12 / INV-05 재현성 --------------------------------------------------
    def test_export_and_landmark_replay_reproduce_candidates(self):
        self.calibrate(); self.start()
        self.tap_cycles(3, noise=.001)
        self.send(hands=[])
        self.tap_cycles(1, noise=.001)
        export = self.engine.export(self.sid, self.s.epoch)['data']
        run = export['input_runs'][-1]
        self.assertEqual(run['schema_version'], 'b02-input-run.v1')
        replayed = replay_input_run(run, export['geometry'])
        self.assertEqual(replayed['source'], 'replay')
        self.assertTrue(replayed['same_candidates']); self.assertTrue(replayed['same_mapping'])
        self.assertEqual(replayed['replayed_candidates'], 4)
        self.assertIn(None, [o['world_landmarks'] for o in run['observations']])  # 누락 프레임도 보존


class B02ApiTests(unittest.TestCase):
    def test_routes_exist(self):
        from pthoven_vision.api import create_app
        paths = {r.path for r in create_app().routes}
        for path in ('/start', '/pause', '/events/ack'):
            self.assertIn('/api/vision/v1/sessions/{sid}' + path, paths)


if __name__ == '__main__':
    unittest.main()
