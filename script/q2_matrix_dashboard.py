"""Matrix UI and endpoints, mounted by q2_dashboard.py.

页面与双点工作区共用同一份外壳（`q2_page.page`），只有默认工作区不同；
本文件只提供知识矩阵的 HTTP 端点。
"""
import time

import numpy as np
from flask import Blueprint, jsonify, request

import q2_page
from q2_matrix_engine import MatrixEngine, finite, normalize_matrix

matrix_app = Blueprint('matrix', __name__)


def read_data():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        raise ValueError('请求必须是 JSON 对象')
    return data


@matrix_app.route('/matrix')
def page():
    """统一编辑台，默认落在知识矩阵工作区。"""
    return q2_page.page(default_ws='matrix')


def read_engine(data):
    return MatrixEngine(data['matrix'], **{k: data[k] for k in
        ('rho_min','rho_max','rho_model','rho_fixed','sides','order','angle_bins') if k in data})


@matrix_app.route('/api/matrix/import', methods=['POST'])
def import_matrix():
    try:
        data = read_data()
        return jsonify(normalize_matrix(data['text'], int(data.get('channel',1))))
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        return jsonify(error=str(exc)), 400


@matrix_app.route('/api/matrix/heatmap', methods=['POST'])
def heatmap():
    try:
        data = read_data()
        t0 = time.monotonic()
        metric, stat = data.get('metric','Rmin'), data.get('stat','mean')
        condition = data.get('condition','all')
        if stat not in ('mean','variance'):
            raise ValueError('未知统计量')
        n = int(data.get('resolution',15))
        if not 3 <= n <= 51:
            raise ValueError('网格分辨率应为 3–51')
        bounds = [finite(x) for x in data.get('bounds',[-1800,1800,-1800,1800])]
        if len(bounds) != 4 or bounds[0] >= bounds[1] or bounds[2] >= bounds[3]:
            raise ValueError('绘图范围需要 xmin < xmax，ymin < ymax')
        engine = read_engine(data)
        xs, ys = np.linspace(*bounds[:2], n), np.linspace(*bounds[2:],n)
        z, prob, zones = [], [], []
        for y in ys:
            row, pr, zo = [], [], []
            for x in xs:
                if data.get('limit_target', True) and x*x+y*y > 1800**2:
                    row.append(None); pr.append(None); zo.append(None)
                    continue
                result = engine.evaluate([x,y],metric,condition)
                row.append(result[stat]); pr.append(result['pdet']); zo.append(result['zone'])
            z.append(row); prob.append(pr); zones.append(zo)
        vertices = np.vstack([np.asarray(p) for p in engine.parts])
        return jsonify(x=xs.tolist(),y=ys.tolist(),z=z,pdet=prob,zone=zones,
                       baseline=engine.baseline,
                       source_polys=[[v.tolist() for v in p] for p in engine.parts],
                       observations=engine.observation_regions(),
                       target_radius=1800.0,
                       source_bounds=[float(vertices[:,0].min()),float(vertices[:,0].max()),
                                      float(vertices[:,1].min()),float(vertices[:,1].max())],
                       nodes=len(engine.points), elapsed_s=time.monotonic()-t0,
                       metric=metric,stat=stat,condition=condition,
                       geometry='conservative-endpoints',matrix=engine.matrix)
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        return jsonify(error=str(exc)), 400


@matrix_app.route('/api/matrix/probe', methods=['POST'])
def probe():
    try:
        data = read_data()
        engine = read_engine(data)
        metric = data.get('metric','Rmin')
        condition = data.get('condition','all')
        result = dict(engine.evaluate(data['point'],metric,condition))
        point = [finite(v) for v in data['point']]
        result['distances'] = [
            {'point': [float(v) for v in c['point']], 'status': c['status'],
             'distance': float(np.hypot(point[0]-c['point'][0], point[1]-c['point'][1]))}
            for c in engine.observations]
        other = 'all' if condition == 'detected' else 'detected'
        alternative = engine.evaluate(point, metric, other)
        result.update(metric=metric, condition=condition,
                      mean_other=alternative['mean'], variance_other=alternative['variance'],
                      condition_other=other, baseline=engine.baseline,
                      nodes=len(engine.points))
        return jsonify(result)
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        return jsonify(error=str(exc)), 400
