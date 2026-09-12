/* 工作区 A：双点交会（S1、θ1、ρ 给定 → 选择第二检测点 S2）
 *
 * 只负责：把自己的输入整理成统一 view.layers，调用 /api/heatmap；
 * 面板、辅助线、配色、图例、导入导出全部来自 q2_app.js。
 */
(function (global) {
  'use strict';
  const Q2 = global.Q2;
  const { ctl, fmt, section } = Q2;

  const API = {
    heatmap: '/api/heatmap',
    probe: '/api/probe',
    importMatrix: '/api/matrix/import'
  };
  const SITE_R = 1800, S1_MAX = 1700, RECOMMENDED_LOCAL = [[850, 520], [850, -520]];

  const state = {
    s1x: 0, s1y: 0, theta1: 0, channel: 1,
    rhoModel: 'uniform', rhoMin: 1000, rhoMax: 1500, rhoFixed: 1250,
    metric: 'Rmin', stat: 'mean',
    uMin: -1800, uMax: 1800, vMin: -1800, vMax: 1800, res: 27,
    colorMode: 'log', robust: false, maskAngle: false, maskPdet: false, pdetMin: 0.99,
    pdetLevel: 0.999, pdetHalf: true, limitTarget: true, showGrey: true,
    integrateDelta: true, nPhi: 5, nR: 4, sides: 128, auto: true
  };
  let last = null;          /* 最近一次 /api/heatmap 响应 */
  let lastView = null;      /* 最近一次统一 view（辅助线面板要看它决定可用性） */
  let busy = false, timer = null, revision = 0, importedColumns = null;

  /* ---------------------------------------------------------------- 请求体 */
  function payload() {
    return {
      s1x: state.s1x, s1y: state.s1y, theta1: state.theta1,
      u_min: state.uMin, u_max: state.uMax, v_min: state.vMin, v_max: state.vMax,
      nx: state.res, ny: state.res,
      metric: state.metric, stat: state.stat,
      integrate_delta: state.integrateDelta,
      n_phi: state.nPhi, n_r: state.nR, disk_sides: state.sides, bound_sides: state.sides,
      limit_target: state.limitTarget,
      rho_model: state.rhoModel, rho_min: state.rhoMin,
      rho_max: state.rhoMax, rho_fixed: state.rhoFixed
    };
  }

  function metricLabel() {
    const m = { Rmin: 'R_min：最小包围圆半径 (m)', N20: 'N20_lb：20 m 圆覆盖数下界', pdet: 'p_det：第二次检测成功概率' }[state.metric];
    if (state.metric === 'pdet') return 'p_det';
    return (state.stat === 'mean' ? 'E[' : 'Var[') + (state.metric === 'Rmin' ? 'R_min' : 'N20_lb') + ']' + (state.metric === 'Rmin' ? ' (m)' : '');
  }
  function colorNote() {
    return (state.metric === 'pdet' ? 'p_det，' : (state.stat === 'mean' ? '期望，' : '方差，')) + '颜色=' + Q2.color.modeName(state.colorMode);
  }
  function zoneName(z) { return (Q2.zoneOf(z) || {}).name || z; }

  /* ---------------------------------------------------------------- 计算 */
  async function draw() {
    if (busy) return;
    busy = true;
    const btn = Q2.el && document.getElementById('compute');
    if (btn) btn.disabled = true;
    const rev = revision;
    Q2.ui.pill('计算中…', 'busy');
    Q2.ui.status('计算中…');
    try {
      const body = payload();
      const resp = await fetch(API.heatmap, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
      });
      const data = await resp.json();
      if (!Q2.isActive('doublet')) return;            /* 期间切走了工作区，结果作废 */
      if (!resp.ok || data.error) throw new Error(data.error || resp.statusText);
      last = data;
      lastView = toView(data, body);
      await render(rev);
    } catch (err) {
      Q2.ui.pill('计算失败', 'err');
      Q2.ui.status('计算失败：' + err.message);
      Q2.ui.toast('计算失败：' + err.message, 'err');
    } finally {
      busy = false;
      if (btn) btn.disabled = false;
    }
  }

  function toView(d, body) {
    const zoneNames = d.zone.map(row => row.map(z => z === 0 ? 'certain' : z === 2 ? 'blind' : 'probabilistic'));
    const regions = (d.source_polys || []).map(p => ({
      poly: p.poly, label: '干扰源可能集 A1（' + p.label + '）'
    }));
    /* 楔形图层只画测向方向（中心射线与 ±1° 边界射线）：双点的可能源区 A1 就是
       楔形∩ρ圆盘，多边形已由"可能源区"图层负责，这里不再重复描一遍。 */
    return {
      xs: d.u, ys: d.v, z: d.z, pdet: d.pdet, zone: zoneNames,
      bounds: [body.u_min, body.u_max, body.v_min, body.v_max],
      metric: d.metric,
      title: '第二检测点位置热图 · ' + metricLabel() + '（ρ_min=' + fmt.num(d.rho.min, 0) +
        ' m, ρ_max=' + fmt.num(d.rho.max, 0) + ' m, 模型=' + d.rho.model + '）',
      colorbar: metricLabel(),
      colorNote: colorNote(),
      layers: {
        target: { radius: SITE_R },
        regions: regions,
        obs: [{ point: d.s1, status: 'find', label: 'S1' }],
        obsName: '第一观测点 S1',
        wedges: [{ point: d.s1, bearing_deg: d.theta1, polys: [], label: 'S1' }],
        near: [],
        zones: true,
        pdet: d.pdet,
        certainPoly: d.certain_poly && d.certain_poly.length > 2 ? d.certain_poly : null,
        blindContour: d.gap,
        contourLevels: [Number(state.pdetLevel)].concat(state.pdetHalf ? [0.5] : []).filter(v => v > 0 && v < 1),
        recommended: (d.recommended || []).map(p => ({ x: p[0], y: p[1], label: '推荐 S2' }))
      }
    };
  }

  async function render(rev) {
    const prepared = Q2.color.prepare({
      xs: lastView.xs, ys: lastView.ys, z: lastView.z, pdet: lastView.pdet,
      mode: state.colorMode, robust: state.robust,
      maskAngle: state.maskAngle, maskPdet: state.maskPdet, pdetMin: state.pdetMin,
      ref: { point: [state.s1x, state.s1y], bearing_deg: state.theta1 }
    });
    const counts = last.counts, areas = last.areas_km2;
    const badges = [
      { text: 'S1 = (' + fmt.num(state.s1x, 0) + ', ' + fmt.num(state.s1y, 0) + ') m' },
      { text: 'θ1 = ' + fmt.deg(state.theta1) },
      { kind: 'ok', text: '一定测到 ' + counts.certain + ' 格 · ' + fmt.num(areas.certain, 2) + ' km²' },
      { kind: 'warn', text: '概率测得 ' + counts.probabilistic + ' 格 · ' + fmt.num(areas.probabilistic, 2) + ' km²' },
      { kind: 'bad', text: '一定测不到 ' + counts.blind + ' 格 · ' + fmt.num(areas.blind, 2) + ' km²' },
      { kind: 'plain', text: '有效格点 ' + prepared.n + '/' + (lastView.xs.length * lastView.ys.length) +
        ' · 用时 ' + fmt.num(last.elapsed_s, 2) + ' s' }
    ];
    await Q2.viz.draw({
      view: lastView, ws: 'doublet', prepared: prepared, badges: badges,
      statusText: '完成 · ' + fmt.num(last.elapsed_s, 2) + ' s',
      statusKind: 'ok',
      status: '完成：' + fmt.num(last.elapsed_s, 2) + ' s，有效格点 ' + prepared.n + '/' +
        (lastView.xs.length * lastView.ys.length) + '，热值 ' + lastView.colorbar +
        (rev !== revision ? '（输入已改变，图为计算开始时的数据）' : '')
    });
  }

  function schedule() {
    clampS1(); revision++;
    if (state.auto) { if (timer) global.clearTimeout(timer); timer = global.setTimeout(draw, 260); }
    else { Q2.ui.pill('参数已改动，点"计算热图"', ''); }
  }

  function clampS1() {
    const r = Math.hypot(state.s1x, state.s1y);
    if (r > S1_MAX) { state.s1x = state.s1x * S1_MAX / r; state.s1y = state.s1y * S1_MAX / r; }
    state.s1x = Math.round(state.s1x * 100) / 100;
    state.s1y = Math.round(state.s1y * 100) / 100;
  }

  /* ---------------------------------------------------------------- 文档 */
  function toJSON() {
    return {
      schema: 'q2-doublet/v1',
      channel: state.channel,
      s1: [state.s1x, state.s1y], theta1: state.theta1,
      rho: { model: state.rhoModel, min: state.rhoMin, max: state.rhoMax, fixed: state.rhoFixed },
      view: { bounds: [state.uMin, state.uMax, state.vMin, state.vMax], resolution: state.res,
              metric: state.metric, stat: state.stat },
      display: { color_mode: state.colorMode, robust: state.robust, mask_angle: state.maskAngle,
                 mask_pdet: state.maskPdet, pdet_min: state.pdetMin,
                 pdet_level: state.pdetLevel, pdet_half: state.pdetHalf },
      extra_columns: importedColumns || []
    };
  }
  function toMarkdown() {
    const c = '{"status":"find","angle":{"svd":' + fmt.num(state.theta1, 4) + '}}';
    return '| Channel\\Path | (' + fmt.num(state.s1x, 2) + ', ' + fmt.num(state.s1y, 2) + ') |\n' +
      '| - | - |\n| ' + state.channel + ' | ' + c + ' |\n';
  }
  function jsonText() { return JSON.stringify(toJSON(), null, 2); }
  function fileName(ext) { return 'doublet-channel' + state.channel + '.' + ext; }

  function applyJSON(doc) {
    if (doc.s1) { state.s1x = Number(doc.s1[0]); state.s1y = Number(doc.s1[1]); }
    if (Number.isFinite(Number(doc.theta1))) state.theta1 = Number(doc.theta1);
    if (Number.isFinite(Number(doc.channel))) state.channel = Number(doc.channel);
    if (doc.rho) {
      if (doc.rho.model) state.rhoModel = doc.rho.model;
      ['min', 'max', 'fixed'].forEach(k => {
        if (Number.isFinite(Number(doc.rho[k]))) state['rho' + k[0].toUpperCase() + k.slice(1)] = Number(doc.rho[k]);
      });
    }
    if (doc.view) {
      const b = doc.view.bounds;
      if (b && b.length === 4) { state.uMin = +b[0]; state.uMax = +b[1]; state.vMin = +b[2]; state.vMax = +b[3]; }
      if (Number.isFinite(Number(doc.view.resolution))) state.res = Number(doc.view.resolution);
      if (doc.view.metric) state.metric = doc.view.metric;
      if (doc.view.stat) state.stat = doc.view.stat;
    }
    if (doc.display) {
      const d = doc.display;
      if (d.color_mode) state.colorMode = d.color_mode;
      if (d.robust !== undefined) state.robust = !!d.robust;
      if (d.mask_angle !== undefined) state.maskAngle = !!d.mask_angle;
      if (d.mask_pdet !== undefined) state.maskPdet = !!d.mask_pdet;
      if (Number.isFinite(Number(d.pdet_min))) state.pdetMin = Number(d.pdet_min);
      if (Number.isFinite(Number(d.pdet_level))) state.pdetLevel = Number(d.pdet_level);
      if (d.pdet_half !== undefined) state.pdetHalf = !!d.pdet_half;
    }
    importedColumns = Array.isArray(doc.extra_columns) ? doc.extra_columns : null;
  }

  /* 统一导入入口：本页 JSON / 知识矩阵 JSON / Channel × Path Markdown */
  async function applyText(text) {
    const trimmed = text.trim();
    if (trimmed.startsWith('{')) {
      let doc = null;
      try { doc = JSON.parse(trimmed); } catch (e) { throw new Error('JSON 解析失败：' + e.message); }
      if (doc && doc.schema === 'q2-doublet/v1') {
        applyJSON(doc);
        rebuild();
        return '已载入双点交会文档';
      }
    }
    /* 其余都当作知识矩阵（JSON 或 Markdown）交给后端规范化 */
    const resp = await fetch(API.importMatrix, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: trimmed, channel: state.channel })
    });
    const data = await resp.json();
    if (!resp.ok || data.error) throw new Error(data.error || resp.statusText);
    const finds = data.columns.filter(c => c.status === 'find');
    const others = data.columns.filter(c => c.status !== 'find');
    if (finds.length !== 1) {
      throw new Error('该知识矩阵有 ' + finds.length + ' 个 find 观测，双点工作区只接受恰好 1 个；' +
        '请点"载入到知识矩阵工作区"继续编辑。');
    }
    state.channel = data.channel;
    state.s1x = finds[0].point[0]; state.s1y = finds[0].point[1];
    state.theta1 = finds[0].bearing_deg;
    importedColumns = others;
    rebuild();
    return '已载入频道 ' + data.channel + ' 的一个 find 观测' +
      (others.length ? '（另有 ' + others.length + ' 列未观测/负担例，已保留在文档中）' : '');
  }

  /* ---------------------------------------------------------------- 面板 */
  function rebuild() {
    clampS1(); revision++;
    Q2.activateWorkspace('doublet', { draw: false });
    draw();
  }

  function buildSections() {
    /* 1) 数据 · 导入导出粘贴（与知识矩阵工作区同一份组件） */
    section({
      id: 'data', icon: '⇅', title: '数据 · 导入 / 导出 / 粘贴', open: true,
      tag: 'JSON · Markdown · 剪贴板',
      build(body) {
        body.appendChild(Q2.io.section({
          placeholder: '粘贴 q2-doublet/v1 JSON、q2-channel/v1 JSON 或 Channel × Path Markdown 表。',
          getJSON: () => ({ name: fileName('json'), text: jsonText() }),
          getMarkdown: () => ({ name: fileName('md'), text: toMarkdown() }),
          applyText: applyText,
          example: () => jsonText(),
          convert: {
            label: '转到知识矩阵工作区 →',
            run: () => {
              if (!Q2.ws.matrix) { Q2.ui.toast('知识矩阵工作区没有加载', 'err'); return; }
              const columns = [{ point: [state.s1x, state.s1y], status: 'find', bearing_deg: state.theta1 }]
                .concat(importedColumns || []);
              Q2.ws.matrix.loadDocument({ channel: state.channel, columns: columns }, true);
              Q2.activateWorkspace('matrix');
              Q2.ui.toast('已把 S1 作为 find 观测列带入知识矩阵工作区');
            }
          },
          note: '导出的 Markdown 就是项目通用的 Channel × Path 表，可直接粘进知识矩阵工作区；' +
            'JSON 还额外保存 ρ 模型、绘图范围与显示参数。'
        }));
      }
    });

    /* 2) 第一观测点（环境给定，不是决策量） */
    section({
      id: 'input', icon: '①', title: '第一观测点 S1 与示向角', open: true, tag: '环境给定',
      build(body) {
        body.appendChild(ctl.hint('决策量只有 S2。S1、θ1、ρ 都是环境给定的量，做成旋钮是为了检验 S2 的选取在整个可行范围内都成立。'));
        body.appendChild(ctl.slider({
          id: 's1x', label: 'S1.x (m)', min: -1700, max: 1700, step: 25, value: state.s1x,
          display: v => fmt.num(v, 0),
          oninput: v => { state.s1x = v; schedule(); }
        }));
        body.appendChild(ctl.slider({
          id: 's1y', label: 'S1.y (m)', min: -1700, max: 1700, step: 25, value: state.s1y,
          display: v => fmt.num(v, 0),
          oninput: v => { state.s1y = v; schedule(); }
        }));
        body.appendChild(ctl.slider({
          id: 'theta1', label: '示向角 θ1 (°)', min: 0, max: 359, step: 1, value: state.theta1,
          display: v => fmt.deg(v),
          oninput: v => { state.theta1 = v; schedule(); }
        }));
        body.appendChild(ctl.hint('滑块右侧的数字框可以直接输入精确值（自动限制在 S1 半径 1700 m 内），不需要另开一组输入框。'));
        body.appendChild(ctl.row(
          ctl.button({ label: 'S1 归零', kind: 'tiny', onclick: () => { state.s1x = 0; state.s1y = 0; rebuild(); } }),
          ctl.button({ label: '用推荐几何 S1=(0,0), θ1=0°', kind: 'tiny', onclick: () => { state.s1x = 0; state.s1y = 0; state.theta1 = 0; rebuild(); } })
        ));
        body.appendChild(ctl.number({ id: 'channel', label: '频道号（仅用于导出/互转）', value: state.channel, min: 1, max: 20, step: 1, oninput: v => { state.channel = Math.round(v); } }));
        body.appendChild(ctl.hint('S1 被限制在半径 1700 m 内，保证第一点位于场地圆内部。'));
      }
    });

    /* 3) ρ 模型与统计口径 */
    section({
      id: 'model', icon: 'ρ', title: '有效距离 ρ 与颜色指标', open: true, tag: '不可决策，需全覆盖',
      build(body) {
        body.appendChild(ctl.grid(3,
          ctl.number({ id: 'rhoMin', label: 'ρ_min (m)', value: state.rhoMin, step: 25, min: 100, max: 3000, oninput: v => { state.rhoMin = v; schedule(); } }),
          ctl.number({ id: 'rhoMax', label: 'ρ_max (m)', value: state.rhoMax, step: 25, min: 100, max: 3000, oninput: v => { state.rhoMax = v; schedule(); } }),
          ctl.number({ id: 'rhoFixed', label: 'ρ_fixed (m)' + (state.rhoModel === 'fixed' ? '' : '（仅 fixed 模型）'),
            value: state.rhoFixed, step: 25, min: 100, max: 3000, disabled: state.rhoModel !== 'fixed',
            oninput: v => { state.rhoFixed = v; schedule(); } })
        ));
        body.appendChild(ctl.select({
          id: 'rhoModel', label: 'ρ 的认知模型', value: state.rhoModel,
          options: [['uniform', '只知道区间，取 ρ~U[ρ_min,ρ_max]（概率）'],
                    ['fixed', '假设 ρ = ρ_fixed 已知（敏感性扫描）'],
                    ['interval', '只知道区间，不设分布（0/1 可能性）']],
          onchange: v => { state.rhoModel = v; rebuild(); }
        }));
        body.appendChild(ctl.grid(2,
          ctl.select({
            id: 'metric', label: '颜色指标（随机变量）', value: state.metric,
            options: [['Rmin', 'R_min：最小覆盖圆半径 (m)'], ['N20', 'N20_lb：20 m 圆覆盖数下界'], ['pdet', 'p_det：第二次检测成功概率']],
            onchange: v => { state.metric = v; rebuild(); }
          }),
          ctl.select({
            id: 'stat', label: '统计量（p_det 指标下忽略）', value: state.stat,
            options: [['mean', '期望'], ['var', '方差']],
            onchange: v => { state.stat = v; rebuild(); }
          })
        ));
        body.appendChild(ctl.note('题目给定 ρ∈[1000,1500] m；与 ρ 分布无关的结论只有三区标注：一定测到（按 ρ_min 判定）、' +
          '一定测不到（按 ρ_max 判定）、其余为概率测得。ρ 模型只影响期望指标与 p_det 的数值。'));
      }
    });

    /* 4) 精度与画布 */
    section({
      id: 'precision', icon: '▦', title: '精度 · 网格 · 绘图范围', open: true,
      build(body) {
        body.appendChild(ctl.slider({
          id: 'res', label: '热图网格分辨率', min: 11, max: 91, step: 2, value: state.res,
          display: v => v + ' × ' + v,
          oninput: v => { state.res = v; schedule(); }
        }));
        body.appendChild(ctl.grid(2,
          ctl.check({ id: 'integrateDelta', label: '对第二次示向误差 δ2 做三点求积', checked: state.integrateDelta, onchange: v => { state.integrateDelta = v; schedule(); } }),
          ctl.number({ id: 'sides', label: '圆周近似边数 / 积分阶', value: state.sides, step: 8, min: 24, max: 360, oninput: v => { state.sides = v; schedule(); } })
        ));
        body.appendChild(ctl.hint('采用确定性、折点感知的 Gauss–Legendre 求积，不是蒙特卡洛；提高分辨率或圆周边数后比较结果即可检查数值收敛。'));
        body.appendChild(ctl.grid(2,
          ctl.number({ id: 'uMin', label: 'x 最小', value: state.uMin, step: 50, oninput: v => { state.uMin = v; schedule(); } }),
          ctl.number({ id: 'uMax', label: 'x 最大', value: state.uMax, step: 50, oninput: v => { state.uMax = v; schedule(); } }),
          ctl.number({ id: 'vMin', label: 'y 最小', value: state.vMin, step: 50, oninput: v => { state.vMin = v; schedule(); } }),
          ctl.number({ id: 'vMax', label: 'y 最大', value: state.vMax, step: 50, oninput: v => { state.vMax = v; schedule(); } })
        ));
        body.appendChild(ctl.row(
          ctl.button({ label: '全场范围（场地圆外接正方形）', kind: 'tiny', onclick: () => { state.uMin = -1800; state.uMax = 1800; state.vMin = -1800; state.vMax = 1800; if (state.res < 31) state.res = 31; rebuild(); } }),
          ctl.button({ label: '放大到可能区域', kind: 'tiny', onclick: zoomToSources })
        ));
        body.appendChild(ctl.check({ id: 'limitTarget', label: '只计算 S2 落在场地圆内的格点', checked: state.limitTarget, onchange: v => { state.limitTarget = v; schedule(); } }));
        body.appendChild(ctl.check({ id: 'auto', label: '参数改动后自动重算', checked: state.auto, onchange: v => { state.auto = v; if (v) schedule(); } }));
      }
    });

    /* 5) 辅助线（注册表驱动，与知识矩阵工作区同一份面板） */
    section({
      id: 'lines', icon: '✎', title: '辅助线图层', open: true, tag: '双工作区共用',
      build(body) {
        body.appendChild(ctl.hint('这两个工作区用的是同一套图层绘制器；面板里被灰掉的图层次工作区确实没有对应数据。'));
        body.appendChild(Q2.linesPanel('doublet', lastView, () => render(revision)));
      }
    });

    /* 6) 显示与掩膜 */
    section({
      id: 'display', icon: '◐', title: '配色 · 掩膜 · 等值线', open: false,
      build(body) {
        body.appendChild(ctl.grid(2,
          ctl.select({
            id: 'colorMode', label: '颜色映射函数', value: state.colorMode,
            options: [['log', 'log10(1+z)，推荐'], ['sqrt', 'sqrt(z)'], ['linear', '线性'], ['rank', '分位/秩着色']],
            onchange: v => { state.colorMode = v; render(revision); }
          }),
          ctl.number({ id: 'pdetLevel', label: 'p_det 等值线水平', value: state.pdetLevel, step: 0.001, min: 0.001, max: 0.9999, oninput: v => { state.pdetLevel = v; render(revision); } })
        ));
        body.appendChild(ctl.grid(2,
          ctl.check({ id: 'pdetHalf', label: '同时画 p_det = 0.5 线', checked: state.pdetHalf, onchange: v => { state.pdetHalf = v; render(revision); } }),
          ctl.check({ id: 'robust', label: '线性配色按 2%–98% 分位裁剪', checked: state.robust, onchange: v => { state.robust = v; render(revision); } })
        ));
        body.appendChild(ctl.check({ id: 'showGrey', label: '无效 / 掩膜 / 场地外格点用灰色底表示', checked: state.showGrey, onchange: v => { state.showGrey = v; render(revision); } }));
        body.appendChild(ctl.check({ id: 'maskAngle', label: '屏蔽近平行交会带（局部 |x沿−855.263| > √3·|y横| 的格点）', checked: state.maskAngle, onchange: v => { state.maskAngle = v; render(revision); } }));
        body.appendChild(ctl.grid(2,
          ctl.check({ id: 'maskPdet', label: '屏蔽 p_det 低于阈值的格点', checked: state.maskPdet, onchange: v => { state.maskPdet = v; render(revision); } }),
          ctl.number({ id: 'pdetMin', label: 'p_det 阈值', value: state.pdetMin, step: 0.005, min: 0, max: 1, oninput: v => { state.pdetMin = v; if (state.maskPdet) render(revision); } })
        ));
      }
    });

    /* 7) 探针 */
    section({
      id: 'probe', icon: '◎', title: '探针 · 单点检验与 ρ 扫描', open: false,
      build(body) {
        body.appendChild(ctl.hint('点击热图任意位置也会触发探针；ρ 扫描固定 S2、让不可决策的 ρ 走遍 [ρ_min, ρ_max]。'));
        body.appendChild(ctl.grid(2,
          ctl.number({ id: 'probeX', label: 'S2.x (m)', value: 850, step: 10 }),
          ctl.number({ id: 'probeY', label: 'S2.y (m)', value: 520, step: 10 })
        ));
        body.appendChild(ctl.row(
          ctl.button({ label: '探测该点', kind: 'primary tiny', onclick: () => probeAt(Number(document.getElementById('probeX').value), Number(document.getElementById('probeY').value)) }),
          ctl.button({ label: '探测推荐点', kind: 'tiny', onclick: () => probeAt(850, 520) })
        ));
      }
    });
  }

  function zoomToSources() {
    if (!last || !last.source_bounds) { Q2.ui.toast('请先计算一次', 'err'); return; }
    const b = last.source_bounds;
    const pad = Math.max(30, Math.max(b[1] - b[0], b[3] - b[2]) * 0.6);
    state.uMin = Math.round(b[0] - pad); state.uMax = Math.round(b[1] + pad);
    state.vMin = Math.round(b[2] - pad); state.vMax = Math.round(b[3] + pad);
    if (state.res < 41) state.res = 41;
    rebuild();
  }

  /* ---------------------------------------------------------------- 探针 */
  async function probeAt(x, y) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    const px = document.getElementById('probeX'), py = document.getElementById('probeY');
    if (px) px.value = String(x.toFixed ? Number(x.toFixed(1)) : x);
    if (py) py.value = String(y.toFixed ? Number(y.toFixed(1)) : y);
    Q2.probe.render({ title: '探针 · 计算中…' });
    try {
      const body = payload();
      body.s2x = x; body.s2y = y; body.n_sweep = 7;
      const resp = await fetch(API.probe, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
      });
      const d = await resp.json();
      if (!resp.ok || d.error) throw new Error(d.error || resp.statusText);
      Q2.probe.lastPoint = [d.s2[0], d.s2[1]];
      Q2.probe.last = d;
      const rows = (d.sweep || []).map(s => ([
        { text: fmt.num(s.rho, 0), cls: 'num' },
        { html: '<span class="' + Q2.zoneOf(s.zone).cls + '">' + zoneName(s.zone) + '</span>' },
        { text: fmt.num(s.p_det, 4), cls: 'num' },
        { text: s.value === null ? '—' : fmt.num(s.value, 3), cls: 'num' },
        { text: fmt.num(s.margin, 1), cls: 'num' },
        { text: fmt.num(s.gap, 1), cls: 'num' }
      ]));
      Q2.probe.render({
        title: '探针 · S2 = (' + fmt.num(d.s2[0], 1) + ', ' + fmt.num(d.s2[1], 1) + ') m',
        badges: [
          { kind: d.zone === 'certain' ? 'ok' : d.zone === 'blind' ? 'bad' : 'warn', text: zoneName(d.zone) },
          { text: '到 S1 距离 ' + fmt.num(d.dist_s1, 1) + ' m' },
          { text: '局部 (沿, 横) = (' + fmt.num(d.local_along, 1) + ', ' + fmt.num(d.local_lateral, 1) + ')' },
          { text: 'p_det(' + d.rho.model + ') = ' + fmt.num(d.p_det, 5) },
          { text: 'p_det(普通求积) = ' + fmt.num(d.p_det_quad, 5) },
          { kind: 'plain', text: 'margin ' + fmt.num(d.margin, 1) + ' m · gap ' + fmt.num(d.gap, 1) + ' m' }
        ],
        table: {
          head: ['ρ (m)', 'ρ 假设下的分区', 'p_det', d.metric, 'margin', 'gap'],
          rows: rows
        },
        note: { text: 'margin ≤ 0 表示按 ρ_min 判定必然成功；gap > 0 表示按 ρ_max 判定必然无信号；两者之间为概率测得。' +
          'ρ 扫描正是"S2 的选取是否覆盖了不可决策的 ρ"的直接检验。' },
        actions: [
          ctl.button({ label: '送到知识矩阵工作区', kind: 'tiny', onclick: () => {
            if (!Q2.ws.matrix) { Q2.ui.toast('知识矩阵工作区没有加载', 'err'); return; }
            Q2.ws.matrix.addColumn([d.s2[0], d.s2[1]]);
            Q2.activateWorkspace('matrix');
          } })
        ]
      });
      render(revision);
    } catch (err) {
      Q2.probe.render({ title: '探针失败', note: { text: err.message, kind: 'bad' } });
    }
  }

  /* ---------------------------------------------------------------- 注册 */
  Q2.registerWorkspace({
    id: 'doublet',
    label: '双点交会 S1→S2',
    hint: '单次交会的第二检测点选择，ρ 三区全覆盖',
    probeHint: '点击热图任意位置即可检验该 S2；会同时给出该点的分区判定、p_det 与 ρ 扫描表。',
    buildSections: buildSections,
    draw: draw,
    onPlotClick: probeAt
  });

  Q2.ws = Q2.ws || {};
  Q2.ws.doublet = {
    state: state,
    loadDocument(doc) { applyJSON(doc); rebuild(); },
    loadColumns(columns, channel) {
      const finds = columns.filter(c => c.status === 'find');
      if (finds.length === 1) {
        state.channel = channel || state.channel;
        state.s1x = finds[0].point[0]; state.s1y = finds[0].point[1];
        state.theta1 = finds[0].bearing_deg;
        importedColumns = columns.filter(c => c !== finds[0]);
        rebuild();
        return true;
      }
      return false;
    }
  };
})(window);
