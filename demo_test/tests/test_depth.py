import base64
import io
import unittest

import numpy as np
from models.depth import DepthService, extract_signals, model_input_shape


def fixture():
    points = [[.45+(i % 5)*.02, .35+(i//5)*.03] for i in range(21)]
    anchors = [[.08,.08],[.92,.08],[.08,.92],[.92,.92]]
    y,x=np.mgrid[0:100,0:160]
    depth=(1+x*.02+y*.01).astype(np.float32)
    for u,v in points:
        cx,cy=round(u*159),round(v*99)
        depth[cy-3:cy+4,cx-3:cx+4]+=2
    return depth,points,anchors


class DepthTests(unittest.TestCase):
    def test_input_size_options_produce_distinct_patch_aligned_shapes(self):
        self.assertEqual(model_input_shape(640,360,294),(168,294))
        self.assertEqual(model_input_shape(640,360,518),(294,518))
        self.assertEqual(model_input_shape(360,640,294),(294,168))

    def test_signal_is_invariant_to_positive_scale_and_shift(self):
        d,p,a=fixture()
        first=extract_signals(d,p,a)
        second=extract_signals(d*2.7+11,p,a)
        self.assertTrue(first['valid'])
        for f in first['fingers']:
            self.assertAlmostEqual(first['fingers'][f]['contrast'],second['fingers'][f]['contrast'],places=4)
        self.assertFalse(first['contact_verified'])

    def test_occluded_desk_anchor_withholds_depth(self):
        d,p,a=fixture();a[0]=p[8]
        result=extract_signals(d,p,a)
        self.assertFalse(result['valid'])
        self.assertEqual(result['reason'],'desk_anchor_occluded')
        self.assertIsNone(result['fingers']['index']['contrast'])

    def test_collinear_desk_anchors_and_nan_are_rejected(self):
        d,p,a=fixture()
        with self.assertRaises(ValueError):extract_signals(d,p,[[.1,.1],[.3,.3],[.8,.8]])
        d[0,0]=np.nan
        with self.assertRaises(ValueError):extract_signals(d,p,a)

    def test_unloaded_model_never_emits_fake_depth(self):
        service=DepthService()
        self.assertEqual(service.status()['state'],'not_loaded')
        with self.assertRaises(RuntimeError):service.infer({})


if __name__=='__main__':unittest.main()
