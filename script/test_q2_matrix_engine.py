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

    def test_zone_classification_matches_pdet(self):
        """分区是 p_det 本身的读数，界面用它上色，必须和几何判定一致。"""
        e = MatrixEngine(matrix(find([0,0],0)))
        self.assertEqual(e.evaluate([850,520])['zone'],'certain')     # 后验必然测到
        blind = e.evaluate([-4000,0])
        self.assertEqual(blind['zone'],'blind')
        self.assertEqual(blind['pdet'],0.0)
        mid = e.evaluate([0,2400],condition='all')
        self.assertEqual(mid['zone'],MatrixEngine.zone(mid['pdet']))

    def test_observation_regions_for_overlays(self):
        """辅助线数据：find 给可行域 + ±1° 射线，not_find 给被排除的 ρ_min 圆盘。"""
        e = MatrixEngine(matrix(find([0,0],0),dict(point=[-1200,0],status='not_find'),
                                dict(point=[1000,0],status='near')))
        regions = {tuple(o['point']): o for o in e.observation_regions()}
        f = regions[(0.0,0.0)]
        self.assertEqual(f['status'],'find')
        self.assertTrue(f['poly'] and f['poly'][0])
        self.assertEqual(len(f['rays']),2)
        for ray in f['rays']:
            self.assertEqual(ray[0],[0.0,0.0])
            self.assertAlmostEqual(math.hypot(*ray[1]),1800,delta=1)   # 射线画到场地圆
        n = regions[(-1200.0,0.0)]
        self.assertEqual(n['status'],'not_find')
        self.assertEqual(n['exclude_radius'],1000)
        self.assertTrue(n['poly'])
        near = regions[(1000.0,0.0)]
        self.assertEqual(near['status'],'near')
        self.assertAlmostEqual(near['near_radius'],5.0)

    def test_heatmap_returns_zones_and_overlays(self):
        client = app.test_client()
        payload = dict(matrix=matrix(find([0,0],0)),resolution=5,bounds=[-1800,1800,-1800,1800])
        d = client.post('/api/matrix/heatmap',json=payload).json
        self.assertEqual(np.shape(d['zone']),(5,5))
        self.assertEqual(len(d['observations']),1)
        self.assertEqual(d['observations'][0]['status'],'find')
        self.assertEqual(d['target_radius'],1800.0)
        flat = {z for row in d['zone'] for z in row}
        self.assertTrue(flat <= {None,'certain','probabilistic','blind'})
        self.assertIn('certain',flat)          # 观测点自身所在格必然是"一定测到"

    def test_probe_reports_zone_and_distances(self):
        client = app.test_client()
        payload = dict(matrix=matrix(find([0,0],0)),resolution=3,bounds=[800,900,450,550])
        d = client.post('/api/matrix/probe',json={**payload,'point':[850,520]}).json
        self.assertEqual(d['zone'],'certain')
        self.assertEqual(d['metric'],'Rmin')
        self.assertEqual(len(d['distances']),1)
        self.assertAlmostEqual(d['distances'][0]['distance'],math.hypot(850,520),delta=1e-6)
        self.assertIn('mean_other',d)

    def test_unified_page_is_shared_by_both_routes(self):
        """两个路由返回同一份统一界面，只有默认工作区不同。"""
        client = app.test_client()
        import re
        root = client.get('/').get_data(as_text=True)
        mt = client.get('/matrix').get_data(as_text=True)
        self.assertIn('data-default-ws="doublet"',root)
        self.assertIn('data-default-ws="matrix"',mt)
        norm = lambda h: re.sub(r'data-default-ws="[^"]*"','data-default-ws="X"',h)
        self.assertEqual(norm(root),norm(mt))
        self.assertEqual(client.get('/favicon.ico').status_code,204)

    def test_assets_are_served_and_traversal_blocked(self):
        client = app.test_client()
        for name in ('q2_app.css','q2_app.js','q2_ws_doublet.js','q2_ws_matrix.js'):
            r = client.get('/assets/'+name)
            self.assertEqual(r.status_code,200,name)
            self.assertGreater(len(r.data),2000,name)
        self.assertEqual(client.get('/assets/../q2_dashboard.py').status_code,404)
        self.assertEqual(client.get('/assets/nope.js').status_code,404)

    def test_unified_page_has_no_placeholder_left(self):
        html = app.test_client().get('/').get_data(as_text=True)
        self.assertNotIn('__VERSION__',html)
        self.assertNotIn('__DEFAULT_WS__',html)
        for asset in ('/assets/q2_app.css','/assets/q2_app.js','/assets/q2_ws_doublet.js','/assets/q2_ws_matrix.js'):
            self.assertIn(asset,html)


if __name__ == '__main__':
    unittest.main()


class UiContractTests(unittest.TestCase):
    """前端契约：q2_assets/*.js 读取的字段必须真的出现在响应里。

    统一界面把绘制逻辑集中到了共用层，后端字段一旦改名，页面会静默画空图，
    所以这里把两个工作区用到的键逐条钉住。
    """

    def test_doublet_endpoints_contract(self):
        client = app.test_client()
        base = dict(s1x=0, s1y=0, theta1=0, u_min=-1800, u_max=1800, v_min=-1800, v_max=1800,
                    nx=9, ny=9, metric='Rmin', stat='mean',
                    rho_model='uniform', rho_min=1000, rho_max=1500)
        d = client.post('/api/heatmap', json=base).json
        for key in ('u', 'v', 'z', 'pdet', 'zone', 'certain_poly', 'gap', 'counts', 'areas_km2',
                    'source_polys', 'source_bounds', 'recommended', 'best', 'elapsed_s',
                    'metric', 'stat', 's1', 'theta1', 'rho'):
            self.assertIn(key, d, key)
        self.assertEqual([p['label'] for p in d['source_polys']],
                         ['ρ_min=1000 m', 'ρ_max=1500 m'])
        self.assertTrue(all(set(p) == {'rho', 'label', 'poly'} for p in d['source_polys']))
        for stat in ('counts', 'areas_km2'):
            self.assertEqual(set(d[stat]), {'certain', 'probabilistic', 'blind'} | (
                {'computed', 'skipped'} if stat == 'counts' else set()))
        pr = client.post('/api/probe', json={**base, 's2x': 850, 's2y': 520}).json
        for key in ('s2', 'zone', 'margin', 'gap', 'p_det', 'p_det_quad', 'value', 'var',
                    'dist_s1', 'local_along', 'local_lateral', 'sweep', 'rho', 'metric'):
            self.assertIn(key, pr, key)
        self.assertEqual(set(pr['sweep'][0]), {'rho', 'p_det', 'margin', 'gap', 'zone', 'value'})

    def test_matrix_endpoints_contract(self):
        client = app.test_client()
        payload = dict(matrix=matrix(find([0,0],0)), resolution=5, bounds=[-1800,1800,-1800,1800])
        d = client.post('/api/matrix/heatmap', json=payload).json
        for key in ('x', 'y', 'z', 'pdet', 'zone', 'baseline', 'source_polys', 'source_bounds',
                    'observations', 'target_radius', 'nodes', 'elapsed_s', 'metric', 'stat',
                    'condition', 'matrix'):
            self.assertIn(key, d, key)
        self.assertEqual(set(d['baseline']), {'Rmin', 'N20', 'area'})
        self.assertEqual(d['matrix']['schema'], 'q2-channel/v1')
        obs = d['observations'][0]
        self.assertEqual(set(obs), {'point', 'status', 'bearing_deg', 'poly', 'near_radius', 'rays'})
        pr = client.post('/api/matrix/probe', json={**payload, 'point': [850, 520]}).json
        for key in ('pdet', 'mean', 'variance', 'repeated', 'zone', 'distances', 'metric',
                    'condition', 'mean_other', 'variance_other', 'condition_other', 'baseline', 'nodes'):
            self.assertIn(key, pr, key)
        self.assertEqual(set(pr['distances'][0]), {'point', 'status', 'distance'})
