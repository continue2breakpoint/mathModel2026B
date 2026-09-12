"""Matrix UI and endpoints, mounted by q2_dashboard.py."""
from pathlib import Path
import time

import numpy as np
from flask import Blueprint, Response, jsonify, request

from q2_matrix_engine import MatrixEngine, finite, normalize_matrix

matrix_app = Blueprint('matrix', __name__)


def read_data():
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        raise ValueError('请求必须是 JSON 对象')
    return data


@matrix_app.route('/matrix')
def page():
    return Response(Path(__file__).with_name('q2_matrix_dashboard.html').read_text(), mimetype='text/html')


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
        z, prob = [], []
        for y in ys:
            row, pr = [], []
            for x in xs:
                if data.get('limit_target', True) and x*x+y*y > 1800**2:
                    row.append(None); pr.append(None)
                    continue
                result = engine.evaluate([x,y],metric,condition)
                row.append(result[stat]); pr.append(result['pdet'])
            z.append(row); prob.append(pr)
        vertices = np.vstack([np.asarray(p) for p in engine.parts])
        return jsonify(x=xs.tolist(),y=ys.tolist(),z=z,pdet=prob, baseline=engine.baseline,
                       source_polys=[[v.tolist() for v in p] for p in engine.parts],
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
        return jsonify(engine.evaluate(data['point'],data.get('metric','Rmin'),data.get('condition','all')))
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        return jsonify(error=str(exc)), 400
