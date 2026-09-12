"""Run: python3 -m unittest discover -s script -p 'test_q2_matrix*.py'."""
import math
import unittest

import numpy as np

from q2_matrix_engine import MatrixEngine, normalize_matrix, metrics, clip
from q2_dashboard import app


def matrix(*cells):
    return {'schema':'q2-channel/v1','channel':1,'columns':list(cells)}


def find(point, angle):
    return dict(point=point,status='find',bearing_deg=angle)


class MatrixTests(unittest.TestCase):
    def test_roundtrip_and_wrap(self):
        text = '| Channel\\Path | (0, 0) | (850, 520) |\n| - | - | - |\n| 1 | {"status":"find","angle":{"lower":359,"upper":1}} | {"status":"not find"} |'
        m = normalize_matrix(text)
        self.assertEqual(m['columns'][0]['bearing_deg'],0)
        self.assertEqual(normalize_matrix(m),m)
        self.assertEqual(m['columns'][1]['status'],'not_find')

    def test_shared_radius_analytical_bounds(self):
        e = MatrixEngine(matrix(find([0,0],0),dict(point=[0,1100],status='not_find')))
        pts = np.array([[1100.,0],[1400.,0],[700.,0]])
        lo, hi = e.bounds(pts)
        np.testing.assert_allclose(lo,[1100,1400,1000])
        np.testing.assert_allclose(hi,[1500,1500,math.hypot(700,1100)])
        # A candidate at (0, 0) is guaranteed to repeat detection; rho is not redrawn.
        self.assertEqual(e.evaluate([0,0])['pdet'],1)

    def test_single_observation_reference_and_convergence(self):
        m = matrix(find([0,0],0))
        low = MatrixEngine(m).evaluate([850,520])
        high = MatrixEngine(m,sides=360,order=8,angle_bins=192).evaluate([850,520])
        self.assertEqual(high['pdet'],1)
        self.assertAlmostEqual(high['mean'],25.36,delta=.08)
        self.assertAlmostEqual(high['variance'],8.575**2,delta=1)
        self.assertAlmostEqual(low['mean'],high['mean'],delta=.1)

    def test_order_duplicates_and_unmeasured_invariance(self):
        a,b = find([0,0],0),find([850,520],math.degrees(math.atan2(-520,150)))
        e1 = MatrixEngine(matrix(a,b))
        e2 = MatrixEngine(matrix(b,a,a,dict(point=[100,100],status='not_measure')))
        self.assertEqual(e1.baseline,e2.baseline)
        self.assertEqual(e1.evaluate([1000,30]),e2.evaluate([1000,30]))
        r = e1.evaluate([850,520])
        self.assertTrue(r['repeated'])
        self.assertEqual(r['variance'],0)
        self.assertEqual(r['mean'],e1.baseline['Rmin'])

    def test_no_information_blind_candidate(self):
        e = MatrixEngine(matrix(find([0,0],0)))
        r = e.evaluate([-4000,0])
        self.assertEqual(r['pdet'],0)
        self.assertAlmostEqual(r['mean'],e.baseline['Rmin'])
        self.assertEqual(r['variance'],0)
        self.assertIsNone(e.evaluate([-4000,0],condition='detected')['mean'])

    def test_negative_geometry_nonconvex_retains_truth(self):
        e = MatrixEngine(matrix(dict(point=[0,0],status='not_find')),sides=48)
        self.assertGreater(len(e.parts),1)
        def inside(p,poly):
            return all(float((b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0])) >= -1e-6
                       for a,b in zip(poly,poly[1:]+poly[:1]))
        self.assertFalse(any(inside([0,0],p) for p in e.parts))
        for t in np.linspace(0,2*math.pi,40):
            p = [1001*math.cos(t),1001*math.sin(t)]
            self.assertTrue(any(inside(p,q) for q in e.parts))

    def test_disconnected_cover_count_not_diameter_bound(self):
        parts = [[np.array([x+dx,dy],float) for dx,dy in [(0,0),(1,0),(1,1),(0,1)]] for x in [0,1000]]
        self.assertEqual(metrics(parts)['N20'],2)

    def test_projection_bound_invariant_under_splitting(self):
        p = [np.array(v,float) for v in [(0,0),(1000,0),(1000,10),(0,10)]]
        split = [clip(p,np.array([1,0]),500),clip(p,np.array([-1,0]),-500)]
        self.assertEqual(metrics([p])['N20'],metrics(split)['N20'])

    def test_near_and_internal_observation(self):
        e = MatrixEngine(matrix(dict(point=[1000,0],status='near')))
        self.assertLess(e.baseline['Rmin'],5.01)
        r = e.evaluate([1001,0])
        self.assertEqual(r['pdet'],1)
        self.assertLessEqual(r['mean'],e.baseline['Rmin']+1e-8)
        self.assertGreaterEqual(r['variance'],0)

    def test_fixed_prior_no_observations_area_probability(self):
        e = MatrixEngine(matrix(dict(point=[20,0],status='not_measure')),rho_model='fixed',rho_fixed=1250,order=10,sides=192)
        p = e.evaluate([0,0],'pdet')['pdet']
        self.assertAlmostEqual(p,(1250/1800)**2,delta=.025)

    def test_contradictions_and_invalid_inputs(self):
        cases = [matrix(find([0,0],0),dict(point=[0,0],status='not_find')),
                 matrix(find([0,0],0),find([100,0],180),dict(point=[50,0],status='not_find')),
                 matrix(find([float('nan'),0],0)),matrix(dict(point=[0,0],status='cleared'))]
        for m in cases:
            with self.assertRaises((ValueError,TypeError)):
                MatrixEngine(m)

    def test_api_routes_and_validation(self):
        client = app.test_client()
        self.assertEqual(client.get('/matrix').status_code,200)
        payload = dict(matrix=matrix(find([0,0],0)),resolution=3,bounds=[800,900,450,550])
        r=client.post('/api/matrix/heatmap',json=payload)
        self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(np.shape(r.json['z']),(3,3))
        self.assertTrue(np.isfinite(r.json['z']).all())
        self.assertEqual(client.post('/api/matrix/heatmap',json={**payload,'metric':'oops'}).status_code,400)
        self.assertEqual(client.post('/api/matrix/heatmap',json={**payload,'rho_model':'interval'}).status_code,400)
        self.assertEqual(client.post('/api/matrix/heatmap',json={**payload,'bounds':[0,0,0,1]}).status_code,400)


if __name__ == '__main__':
    unittest.main()
