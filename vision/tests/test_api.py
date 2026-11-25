import base64
import unittest
import cv2
import numpy as np
from fastapi.testclient import TestClient
from pthoven_tap.examples import analytic_hand
from pthoven_vision.api import create_app, PREFIX
from pthoven_vision.session import B01
from pthoven_vision.policy import Policy


class FakeTracker:
    def __init__(self):self.calls=0
    def status(self):return {'state':'ready','model_ref':'synthetic-fixture','contact_verified':False}
    def prepare(self):return self.status()
    def infer(self,sid,image,t_ms):self.calls+=1;return []
    def close(self,sid):pass
    def close_all(self):pass


class APITests(unittest.TestCase):
    def setUp(self):
        self.tracker=FakeTracker();self.engine=B01()
        self.client=TestClient(create_app(self.engine,self.tracker))
        self.client.__enter__()
        self.start={'clock_id':'test-clock','selected_device_id':'exact-device'}
        self.context=self.client.post(PREFIX+'/sessions',json=self.start).json()['data']
        self.sid=self.context['session_id'];self.path=PREFIX+'/sessions/'+self.sid

    def tearDown(self):self.client.__exit__(None,None,None)

    def test_static_and_health(self):
        r=self.client.get('/');self.assertEqual(r.status_code,200);self.assertIn('B01',r.text)
        self.assertEqual(self.client.get('/app.js').status_code,200)
        self.assertEqual(self.client.get(PREFIX+'/health').json()['scope'],'B-01+B-02')
        self.assertEqual(self.client.get('/src/pthoven_vision/session.py').status_code,404)

    def test_no_client_landmark_injection_or_false_tracker_ready(self):
        r=self.client.post(self.path+'/device',json={'command_epoch':1,'permission':'granted',
            'stream':'receiving','tracker':'ready'})
        self.assertEqual(r.status_code,422)
        r=self.client.post(self.path+'/frames',json={'landmarks':[[0,0]]*21})
        self.assertEqual(r.status_code,422)

    def test_strict_validation(self):
        for data in [{**self.start,'active_fingers':['index','index']},{**self.start,'input_source':'keyboard'}]:
            self.assertEqual(self.client.post(PREFIX+'/sessions',json=data).status_code,422)
        self.assertEqual(self.client.get('/.env').status_code,404)

    def test_idempotency_and_conflicting_payload(self):
        headers={'X-Request-ID':'same-create'}
        a=self.client.post(PREFIX+'/sessions',json=self.start,headers=headers)
        b=self.client.post(PREFIX+'/sessions',json=self.start,headers=headers)
        self.assertEqual(a.json(),b.json())
        c=self.client.post(PREFIX+'/sessions',json={**self.start,'selected_hand':'left'},headers=headers)
        self.assertEqual(c.status_code,409)

    def test_cross_origin_and_body_size(self):
        self.assertEqual(self.client.post(PREFIX+'/sessions',json=self.start,headers={'Origin':'https://other.example'}).status_code,403)
        self.assertEqual(self.client.post(PREFIX+'/sessions',content=b'x'*2_000_001).status_code,413)

    def test_stale_epoch_cannot_change_session(self):
        r=self.client.post(self.path+'/device',json={'command_epoch':0,'permission':'granted','stream':'receiving'})
        self.assertEqual(r.status_code,409)
        self.assertEqual(self.engine.get(self.sid).device['permission'],'unknown')

    def test_real_jpeg_no_hand_and_duplicate_rejected_before_inference(self):
        self.client.post(self.path+'/device',json={'command_epoch':1,'permission':'granted','stream':'receiving','width':640,'height':360})
        ok,encoded=cv2.imencode('.jpg',np.zeros((360,640,3),np.uint8));self.assertTrue(ok)
        payload={'command_epoch':1,'geometry_version':0,'calibration_version':0,'clock_id':'test-clock',
            'frame_id':1,'t_ms':1000.0,'image':'data:image/jpeg;base64,'+base64.b64encode(encoded).decode()}
        r=self.client.post(self.path+'/frames',json=payload)
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['outcome'],'no_hand')
        self.assertEqual(self.client.post(self.path+'/frames',json=payload).status_code,409)
        self.assertEqual(self.tracker.calls,1)

    def test_invalid_image_and_nonfinite_clock_rejected(self):
        self.client.post(self.path+'/device',json={'command_epoch':1,'permission':'granted','stream':'receiving'})
        payload={'command_epoch':1,'geometry_version':0,'calibration_version':0,'clock_id':'test-clock',
            'frame_id':1,'t_ms':1.0,'image':'not-jpeg'}
        self.assertEqual(self.client.post(self.path+'/frames',json=payload).status_code,400)
        self.assertEqual(self.tracker.calls,0)
        raw='{"command_epoch":1,"geometry_version":0,"calibration_version":0,"clock_id":"test-clock","frame_id":1,"t_ms":NaN,"image":"fixture"}'
        self.assertEqual(self.client.post(self.path+'/frames',content=raw,headers={'Content-Type':'application/json'}).status_code,422)

    def test_chunked_request_size_is_bounded(self):
        def chunks():
            yield b' ' * 2_000_001
            yield b'{"clock_id":"x","selected_device_id":"y"}'
        self.assertEqual(self.client.post(PREFIX+'/sessions',content=chunks(),headers={'Content-Type':'application/json'}).status_code,413)

    def test_corrupt_jpeg_is_structured_error(self):
        self.client.post(self.path+'/device',json={'command_epoch':1,'permission':'granted','stream':'receiving'})
        packet={'command_epoch':1,'geometry_version':0,'calibration_version':0,'clock_id':'test-clock',
                'frame_id':1,'t_ms':40.0,'image':'data:image/jpeg;base64,'+base64.b64encode(b'garbage').decode()}
        r=self.client.post(self.path+'/frames',json=packet)
        self.assertEqual(r.status_code,400)
        self.assertEqual(r.json()['status'],'error')

    def test_unprepared_device_never_advances_tracker(self):
        packet={'command_epoch':1,'geometry_version':0,'calibration_version':0,'clock_id':'test-clock',
                'frame_id':1,'t_ms':40.0,'image':'data:image/jpeg;base64,Zm9v'}
        self.assertEqual(self.client.post(self.path+'/frames',json=packet).status_code,409)
        self.assertEqual(self.tracker.calls,0)

    def test_failed_inference_immediately_replaces_previous_quality(self):
        from pthoven_vision.contracts import B01Error
        self.client.post(self.path+'/device',json={'command_epoch':1,'permission':'granted','stream':'receiving'})
        s=self.engine.get(self.sid);s.latest_quality={'quality_state':'usable_provisional'}
        self.tracker.infer=lambda *args: (_ for _ in ()).throw(B01Error('INFERENCE_FAILED','fake native failure',503))
        ok,raw=cv2.imencode('.jpg',np.zeros((36,64,3),np.uint8));self.assertTrue(ok)
        packet={'command_epoch':1,'geometry_version':0,'calibration_version':0,'clock_id':'test-clock','frame_id':1,
                't_ms':40.0,'image':'data:image/jpeg;base64,'+base64.b64encode(raw).decode()}
        r=self.client.post(self.path+'/frames',json=packet)
        self.assertEqual(r.status_code,503)
        self.assertEqual(s.latest_quality['reason_codes'],['INFERENCE_FAILED'])
        self.assertEqual(s.device['input'],'blocked')

    def test_watchdog_is_not_blocked_by_slow_inference(self):
        import threading,time
        self.client.post(self.path+'/device',json={'command_epoch':1,'permission':'granted','stream':'receiving'})
        session=self.engine.get(self.sid)
        session.last_received=self.engine.now();session.last_t_ms=1;session.last_frame_id=0;session.phase='ready'
        began=threading.Event();release=threading.Event();responses=[]
        def slow(*args):
            began.set();release.wait(3);return []
        self.tracker.infer=slow
        _,raw=cv2.imencode('.jpg',np.zeros((36,64,3),np.uint8))
        packet={'command_epoch':1,'geometry_version':0,'calibration_version':0,'clock_id':'test-clock','frame_id':1,
                't_ms':40.0,'image':'data:image/jpeg;base64,'+base64.b64encode(raw).decode()}
        worker=threading.Thread(target=lambda:responses.append(self.client.post(self.path+'/frames',json=packet)))
        worker.start();self.assertTrue(began.wait(1))
        try:
            session.last_received=self.engine.now()-1
            started=time.monotonic();snapshot=self.client.get(self.path).json()['data']
            self.assertLess(time.monotonic()-started,.5)
            self.assertEqual(snapshot['lifecycle_phase'],'hold')
        finally:release.set();worker.join(3)
        self.assertFalse(worker.is_alive());self.assertEqual(responses[0].status_code,409)

    def test_incompatible_frame_aspect_invalidates_geometry(self):
        self.client.post(self.path+'/device',json={'command_epoch':1,'permission':'granted','stream':'receiving'})
        geometry={'source_size':{'width':1600,'height':900},'display_size':{'width':640,'height':360},
                  'keyboard_rect':{'left':0,'top':250,'width':640,'height':100}}
        ctx=self.client.put(self.path+'/geometry',json={'command_epoch':1,'expected_geometry_version':0,'geometry':geometry}).json()['data']
        _,raw=cv2.imencode('.jpg',np.zeros((48,64,3),np.uint8))
        packet={'command_epoch':ctx['command_epoch'],'geometry_version':ctx['geometry_version'],'calibration_version':0,
                'clock_id':'test-clock','frame_id':1,'t_ms':40.0,'image':'data:image/jpeg;base64,'+base64.b64encode(raw).decode()}
        self.assertEqual(self.client.post(self.path+'/frames',json=packet).status_code,409)
        self.assertIsNone(self.engine.get(self.sid).geometry)
        self.assertEqual(self.tracker.calls,0)

    def test_ready_does_not_claim_calibrated(self):
        r=self.client.post(self.path+'/ready',json={'command_epoch':1}).json()
        self.assertEqual(r['outcome'],'hold_required')

    def test_close_is_explicit_and_idempotent(self):
        a=self.client.post(self.path+'/close',json={'command_epoch':1})
        self.assertEqual(a.json()['outcome'],'closed')
        b=self.client.post(self.path+'/close',json={'command_epoch':a.json()['data']['command_epoch']})
        self.assertEqual(b.json()['outcome'],'closed')


if __name__=='__main__':unittest.main()
