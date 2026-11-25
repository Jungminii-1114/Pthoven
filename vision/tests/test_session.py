import copy
import unittest
from unittest.mock import patch
import numpy as np
from pthoven_tap.examples import analytic_hand
from pthoven_vision.contracts import (B01Error, CaptureRequest, DeviceRequest,
    FrameRequest, GeometryRequest, StartRequest)
from pthoven_vision.policy import Policy
from pthoven_vision.session import B01
from pthoven_vision.quality import QualityTimeline


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.time = [100.0]
        self.engine = B01(Policy(preparation_ms=1, min_collect_ms=200, max_collect_ms=1000,
                                 min_valid_frames=8, ready_stable_ms=80), now=lambda: self.time[0])
        self.sid = self.engine.create({'clock_id':'clock-a','selected_device_id':'iphone'})['data']['session_id']
        self.s = self.engine.get(self.sid)
        self.engine.device(self.sid,DeviceRequest(command_epoch=self.s.epoch,permission='granted',stream='receiving',
                           width=640,height=360),{'state':'ready','model_ref':'synthetic'})
        self.engine.set_geometry(self.sid,GeometryRequest(command_epoch=self.s.epoch,expected_geometry_version=0,
            geometry={'source_size':{'width':640,'height':360},'display_size':{'width':640,'height':360},
                      'keyboard_rect':{'left':0,'top':250,'width':640,'height':100}}))
        self.t = 0
        self.i = 0

    def send(self,lift=0,hands=None,capture=None):
        self.t += 40;self.i += 1;self.time[0] += .04
        world=analytic_hand({'index':lift})
        image=[[.5+p[0]*.1,.6-p[1]*.1] for p in world]
        request=FrameRequest(command_epoch=self.s.epoch,geometry_version=self.s.geometry_version,
            calibration_version=self.s.calibration_version,clock_id='clock-a',frame_id=self.i,t_ms=self.t,image='fixture',capture_id=capture)
        return self.engine.process_observation(self.sid,request,hands if hands is not None else
            [{'handedness':'right','image_xy':image,'world_landmarks':world}])

    def capture(self,phase,lift):
        self.send()
        cap=self.engine.start_capture(self.sid,CaptureRequest(command_epoch=self.s.epoch,phase=phase))['data']['capture_id']
        for _ in range(12):self.send(lift,capture=cap)
        return cap,self.engine.finish_capture(self.sid,cap,self.s.epoch)

    def calibrated(self):
        self.assertEqual(self.capture('rest',0)[1]['outcome'],'rest_saved')
        self.assertEqual(self.capture('lift',1)[1]['outcome'],'calibrated')

    def test_complete_b01_waits_for_neutral_and_freshness(self):
        self.calibrated()
        self.assertEqual(self.engine.ready(self.sid,self.s.epoch)['outcome'],'hold_required')
        for _ in range(4):self.send()
        ready=self.engine.ready(self.sid,self.s.epoch)
        self.assertEqual(ready['outcome'],'ready_for_observation')
        self.assertIn('index',ready['data']['calibration']['per_finger_templates'])
        self.time[0] += 1
        self.assertEqual(self.engine.snapshot(self.sid)['data']['lifecycle_phase'],'hold')
        epoch=self.s.epoch
        self.engine.snapshot(self.sid)
        self.assertEqual(epoch,self.s.epoch)

    def test_new_baseline_removes_previous_lifts(self):
        self.calibrated();old=self.s.baseline_ref
        self.capture('rest',0)
        self.assertNotEqual(old,self.s.baseline_ref)
        self.assertEqual(self.s.templates,{})

    def test_lift_failure_preserves_other_valid_template(self):
        self.calibrated();old=copy.deepcopy(self.s.templates)
        response=self.capture('lift',0)[1]
        self.assertNotEqual(response['outcome'],'calibrated')
        self.assertEqual(old,self.s.templates)

    def test_unselected_finger_cannot_capture(self):
        self.send()
        with self.assertRaises(B01Error):self.engine.start_capture(self.sid,
            CaptureRequest(command_epoch=self.s.epoch,phase='rest',finger='ring'))

    def test_queued_frame_without_capture_id_not_collected(self):
        self.send()
        cap=self.engine.start_capture(self.sid,CaptureRequest(command_epoch=self.s.epoch,phase='rest'))['data']['capture_id']
        for _ in range(20):self.send()
        self.assertEqual(self.s.captures[cap].attempted,0)

    def test_cancel_during_fit_cannot_publish(self):
        self.send()
        cap=self.engine.start_capture(self.sid,CaptureRequest(command_epoch=self.s.epoch,phase='rest'))['data']['capture_id']
        for _ in range(12):self.send(capture=cap)
        from pthoven_vision.pose import validate_rest
        def cancelling(*args):
            self.engine.cancel_capture(self.sid,cap,self.s.epoch)
            return validate_rest(*args)
        with patch('pthoven_vision.session.validate_rest',side_effect=cancelling):
            with self.assertRaises(B01Error):self.engine.finish_capture(self.sid,cap,self.s.epoch)
        self.assertIsNone(self.s.baseline_ref)

    def test_display_only_change_preserves_templates_but_increments_epoch(self):
        self.calibrated();old=copy.deepcopy(self.s.templates);epoch=self.s.epoch
        self.engine.set_geometry(self.sid,GeometryRequest(command_epoch=epoch,expected_geometry_version=self.s.geometry_version,
            geometry={'source_size':{'width':640,'height':360},'display_size':{'width':800,'height':450},
                      'keyboard_rect':{'left':0,'top':300,'width':800,'height':100},'flip_x':True}))
        self.assertEqual(old,self.s.templates);self.assertGreater(self.s.epoch,epoch)

    def test_invalid_geometry_is_transactional(self):
        old=self.s.context()
        with self.assertRaises(ValueError):self.engine.set_geometry(self.sid,GeometryRequest(command_epoch=self.s.epoch,
            expected_geometry_version=self.s.geometry_version,geometry={}))
        self.assertEqual(old,self.s.context())

    def test_no_hand_is_success_and_ambiguous_not_inference_failure(self):
        self.assertEqual(self.send(hands=[])['status'],'success')
        self.assertEqual(self.s.latest_quality['reason_codes'],['HAND_NOT_OBSERVED'])
        h={'handedness':'right'}
        self.send(hands=[h,h])
        self.assertEqual(self.s.latest_quality['reason_codes'],['HAND_IDENTITY_AMBIGUOUS'])

    def test_closed_sessions_release_capacity(self):
        for _ in range(40):
            sid=self.engine.create({'clock_id':'c','selected_device_id':'device'})['data']['session_id']
            self.engine.close(sid,self.engine.get(sid).epoch)
        self.assertLessEqual(len(self.engine.sessions),32)

    def test_client_stall_cancels_capture_before_fit_can_publish(self):
        self.send();cap=self.engine.start_capture(self.sid,CaptureRequest(command_epoch=self.s.epoch,phase='rest'))['data']['capture_id']
        for _ in range(12):self.send(capture=cap)
        from pthoven_vision.pose import validate_rest
        def stall(*args):
            self.engine.device(self.sid,DeviceRequest(command_epoch=self.s.epoch,permission='granted',stream='stalled'),{'state':'ready'})
            return validate_rest(*args)
        with patch('pthoven_vision.session.validate_rest',side_effect=stall):
            with self.assertRaises(B01Error):self.engine.finish_capture(self.sid,cap,self.s.epoch)
        self.assertIsNone(self.s.baseline_ref)

    def test_recalibration_request_does_not_complete_calibration(self):
        self.calibrated()
        response=self.engine.request_recalibration(self.sid,self.s.epoch)
        self.assertEqual(response['outcome'],'recalibration_requested')
        for _ in range(5):self.send()
        self.assertEqual(self.engine.ready(self.sid,self.s.epoch)['outcome'],'hold_required')

    def test_close_blocks_old_frames_and_exports(self):
        self.engine.close(self.sid,self.s.epoch)
        with self.assertRaises(B01Error):self.send()
        with self.assertRaises(B01Error):self.engine.export(self.sid,self.s.epoch)

    def test_frames_use_browser_monotonic_clock(self):
        self.send();request=FrameRequest(command_epoch=self.s.epoch,geometry_version=self.s.geometry_version,
            calibration_version=0,clock_id='wrong',frame_id=2,t_ms=80,image='fixture')
        with self.assertRaises(B01Error):self.engine.process_observation(self.sid,request,[])
        self.assertEqual(self.s.last_frame_id,1)

    def test_export_replay_uses_original_calibration_samples(self):
        self.calibrated()
        from pthoven_vision.replay import replay_export
        data=self.engine.export(self.sid,self.s.epoch)['data']
        replay=replay_export(data)
        self.assertTrue(replay['results']['index']['same_model_parameters'])
        self.assertIn('t_ms',data['rest_frames'][0])
        self.assertEqual(replay['source'],'replay')

    def test_real_scope_export_has_no_contact_claim_or_video(self):
        self.calibrated();data=self.engine.export(self.sid,self.s.epoch)['data']
        self.assertFalse(data['contact_verified']);self.assertFalse(data['contains_video'])
        self.assertTrue(data['rest_frames']);self.assertTrue(data['captures'])


class ContractTests(unittest.TestCase):
    def test_bad_start_contracts(self):
        for extra in [{'active_fingers':[]},{'active_fingers':['index','index']},{'input_source':'keyboard'},
                      {'exercise_ref':'missing'},{'unknown':1}]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):StartRequest.model_validate(
                {'clock_id':'a','selected_device_id':'b',**extra})

    def test_policy_rejects_bad_ranges(self):
        for kw in [{'min_valid_ratio':1.1},{'max_collect_ms':100},{'shape_limit':float('nan')},
                   {'max_capture_frames':1}]:
            with self.subTest(kw=kw),self.assertRaises(ValueError):Policy(**kw)

    def test_quality_duration_does_not_bridge_a_gap(self):
        q=QualityTimeline(200);q.observe(100,True,[]);q.observe(600,False,['HAND_NOT_OBSERVED'])
        snapshot=q.snapshot()
        self.assertEqual(snapshot['denominators']['usable_duration_ms'],200)
        self.assertEqual(snapshot['denominators']['unknown_duration_ms'],300)
        self.assertEqual(q.snapshot(stalled=True)['unknown_intervals'][-1]['end_ms'],None)


if __name__=='__main__':unittest.main()
