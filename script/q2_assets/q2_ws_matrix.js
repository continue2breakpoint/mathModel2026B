/* 工作区 B：知识矩阵（Channel × Path 观测表 → 下一观测点）
 *
 * 与双点工作区共用同一套外壳、控件、图层绘制器、导入导出面板；
 * 本文件只负责观测表编辑、请求体与 view.layers 的整理。
 */
(function (global) {
  'use strict';
  const Q2 = global.Q2;
  const { ctl, fmt, section } = Q2;

  const API = { heatmap: '/api/matrix/heatmap', probe: '/api/matrix/probe', normalize: '/api/matrix/import' };
  const STATUSES = [
    ['find', '测到并测向'],
    ['not_find', '未测到'],
    ['not_measure', '未观测'],
    ['near', '5 m 内信号过强']
  ];
  const ST_CLS = { find: 'row-find', not_find: 'row-not_find', near: 'row-near', not_measure: '' };

  const state = {
    channel: 1,
    columns: [{ point: [0, 0], status: 'find', bearing_deg: 0 }],
    rhoModel: 'uniform', rhoMin: 1000, rhoMax: 1500, rhoFixed: 1250,
    metric: 'Rmin', stat: 'mean', condition: 'all',
    res: 21, order: 3, angleBins: 48, sides: 96,
    uMin: -1800, uMax: 1800, vMin: -1800, vMax: 1800,
    colorMode: 'log', robust: false, maskPdet: false, pdetMin: 0.99,
    maskAngle: false, limitTarget: true, showGrey: true,
    pdetLevel: 0.999, pdetHalf: true, auto: false
  };
  let last = null, lastView = null, busy = false, timer = null, revision = 0;

  /* ---------------------------------------------------------------- 请求体 */
  function matrixDoc() {
    return {
      schema: 'q2-channel/v1', channel: Math.round(state.channel),
      columns: state.columns.map(c => {
        const out = { point: [Number(c.point[0]), Number(c.point[1])], status: c.status };
        if (c.status === 'find') out.bearing_deg = Number(c.bearing_deg || 0);
        return out;
      })
    };
  }
  function payload() {
    return {
      matrix: matrixDoc(),
      bounds: [state.uMin, state.uMax, state.vMin, state.vMax],
      resolution: state.res, order: state.order, angle_bins: state.angleBins, sides: state.sides,
      rho_model: state.rhoModel, rho_min: state.rhoMin, rho_max: state.rhoMax, rho_fixed: state.rhoFixed,
      metric: state.metric, stat: state.stat, condition: state.condition,
      limit_target: state.limitTarget
    };
  }
  function metricLabel() {
    if (state.metric === 'pdet') return state.stat === 'variance' ? 'Var[p_det] = p(1−p)' : 'p_det';
    const base = state.metric === 'Rmin' ? 'R_min (m)' : 'N20_lb';
    return (state.stat === 'mean' ? 'E[' : 'Var[') + base + ']';
  }
  function colorNote() {
    return (state.metric === 'pdet' ? '测到概率，' : (state.stat === 'mean' ? '期望，' : '方差，')) +
      '颜色=' + Q2.color.modeName(state.colorMode) +
      (state.condition === 'detected' ? '，条件于测到' : '，含未测到结果');
  }
  function firstFind() { return state.columns.filter(c => c.status === 'find')[0] || null; }

  /* ---------------------------------------------------------------- 计算 */
  async function draw() {
    if (busy) return;
    busy = true;
    const btn = document.getElementById('compute');
    if (btn) btn.disabled = true;
    const rev = revision;
    Q2.ui.pill('计算中…', 'busy');
    Q2.ui.status('计算中…（' + state.res + '×' + state.res + ' 格 × 每格非凸几何，加密网格会更慢）');
    try {
      const resp = await fetch(API.heatmap, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload())
      });
      const data = await resp.json();
      if (!Q2.isActive('matrix')) return;            /* 期间切走了工作区，结果作废 */
      if (!resp.ok || data.error) throw new Error(data.error || resp.statusText);
      last = data;
      lastView = toView(data);
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

  function toView(d) {
    const obs = [];
    const wedges = [], near = [], excluded = [];
    d.matrix.columns.forEach((c, i) => {
      obs.push({ point: c.point, status: c.status, label: 'P' + (i + 1) });
    });
    (d.observations || []).forEach((o, i) => {
      const idx = d.matrix.columns.findIndex(c => c.point[0] === o.point[0] && c.point[1] === o.point[1]);
      const label = 'P' + (idx >= 0 ? idx + 1 : i + 1);
      if (o.status === 'find') {
        wedges.push({ point: o.point, bearing_deg: o.bearing_deg, polys: o.poly || [], rays: o.rays || [], label: label });
      }
      if (o.near_radius) near.push({ point: o.point, radius: o.near_radius });
      if (o.exclude_radius) excluded.push({ point: o.point, radius: o.exclude_radius });
    });
    const b = d.baseline || {};
    return {
      xs: d.x, ys: d.y, z: d.z, pdet: d.pdet, zone: d.zone,
      bounds: [state.uMin, state.uMax, state.vMin, state.vMax],
      metric: d.metric,
      title: '知识矩阵下一观测点热图 · ' + metricLabel() + '（频道 ' + d.matrix.channel + '，' +
        d.matrix.columns.length + ' 列观测，' + (d.metric === 'pdet' ? '检测概率' :
        (d.stat === 'mean' ? '期望' : '方差')) + '）',
      colorbar: metricLabel(),
      colorNote: colorNote(),
      layers: {
        target: { radius: d.target_radius || 1800 },
        regions: (d.source_polys || []).map(p => ({ poly: p, label: '当前可能源区 A（全部观测之交）' })),
        obs: obs,
        obsName: '观测点 P1…Pn',
        wedges: wedges,
        near: near,
        excluded: excluded,
        zones: true,
        pdet: d.pdet,
        certainPoly: null,
        blindContour: null,
        contourLevels: [Number(state.pdetLevel)].concat(state.pdetHalf ? [0.5] : []).filter(v => v > 0 && v < 1),
        recommended: [],
        baseline: b
      }
    };
  }

  async function render(rev) {
    const prepared = Q2.color.prepare({
      xs: lastView.xs, ys: lastView.ys, z: lastView.z, pdet: lastView.pdet,
      mode: state.colorMode, robust: state.robust,
      maskAngle: state.maskAngle, maskPdet: state.maskPdet, pdetMin: state.pdetMin,
      ref: refObs()
    });
    /* 分区统计（格点计数与面积，和双点工作区同一口径） */
    const dx = Math.abs(lastView.xs[1] - lastView.xs[0]) || 0;
    const dy = Math.abs(lastView.ys[1] - lastView.ys[0]) || 0;
    const cell = dx * dy / 1e6;
    let nC = 0, nP = 0, nB = 0, nUsed = 0;
    lastView.zone.forEach(row => row.forEach(z => {
      if (z === 'certain') nC++; else if (z === 'probabilistic') nP++; else if (z === 'blind') nB++;
    }));
    lastView.z.forEach(row => row.forEach(v => { if (v !== null && Number.isFinite(v)) nUsed++; }));
    const b = lastView.layers.baseline || {};
    const badges = [
      { text: '频道 ' + state.channel + ' · ' + state.columns.length + ' 列（' +
        state.columns.filter(c => c.status !== 'not_measure').length + ' 个已观测）' },
      { kind: 'ok', text: '必然测到 ' + nC + ' 格 · ' + fmt.num(nC * cell, 2) + ' km²' },
      { kind: 'warn', text: '概率测得 ' + nP + ' 格 · ' + fmt.num(nP * cell, 2) + ' km²' },
      { kind: 'bad', text: '必然无信号 ' + nB + ' 格 · ' + fmt.num(nB * cell, 2) + ' km²' },
      { kind: 'plain', text: '可能区域 R_min=' + fmt.num(b.Rmin, 1) + ' m · N20下界=' + b.N20 +
        ' · 面积 ' + fmt.num((b.area || 0) / 1e6, 2) + ' km²' },
      { kind: 'plain', text: '积分节点 ' + last.nodes + ' · 有效格点 ' + nUsed + '/' +
        (lastView.xs.length * lastView.ys.length) + ' · 用时 ' + fmt.num(last.elapsed_s, 2) + ' s' }
    ];
    await Q2.viz.draw({
      view: lastView, ws: 'matrix', prepared: prepared, badges: badges,
      statusText: '完成 · ' + fmt.num(last.elapsed_s, 2) + ' s', statusKind: 'ok',
      status: '完成：' + fmt.num(last.elapsed_s, 2) + ' s，' + last.nodes + ' 个源积分节点，' +
        '可能区域 R_min=' + fmt.num(b.Rmin, 2) + ' m、N20 下界=' + b.N20 +
        (rev !== revision ? '（输入已改变，此图对应计算开始时的数据）' : '')
    });
  }

  function refObs() {
    const f = firstFind();
    return f ? { point: f.point, bearing_deg: Number(f.bearing_deg || 0) } : null;
  }

  function schedule() {
    revision++;
    if (state.auto) { if (timer) global.clearTimeout(timer); timer = global.setTimeout(draw, 400); }
    else Q2.ui.pill('观测表或参数已改动，点"计算热图"', '');
  }
  function rebuild() { revision++; Q2.activateWorkspace('matrix', { draw: false }); draw(); }

  /* ---------------------------------------------------------------- 文档 */
  function toJSON() {
    return {
      schema: 'q2-channel/v1', channel: Math.round(state.channel),
      columns: state.columns.map(c => {
        const out = { point: [Number(c.point[0]), Number(c.point[1])], status: c.status };
        if (c.status === 'find') out.bearing_deg = Number(c.bearing_deg || 0);
        return out;
      }),
      rho: { model: state.rhoModel, min: state.rhoMin, max: state.rhoMax, fixed: state.rhoFixed },
      view: { bounds: [state.uMin, state.uMax, state.vMin, state.vMax], resolution: state.res,
              metric: state.metric, stat: state.stat, condition: state.condition,
              order: state.order, angle_bins: state.angleBins, sides: state.sides },
      display: { color_mode: state.colorMode, robust: state.robust, mask_pdet: state.maskPdet,
                 pdet_min: state.pdetMin, mask_angle: state.maskAngle,
                 pdet_level: state.pdetLevel, pdet_half: state.pdetHalf }
    };
  }
  function toMarkdown() {
    const head = '| Channel\\Path | ' + state.columns.map(c =>
      '(' + fmt.num(c.point[0], 2) + ', ' + fmt.num(c.point[1], 2) + ')').join(' | ') + ' |\n';
    const sep = '| - | ' + state.columns.map(() => '-').join(' | ') + ' |\n';
    const row = '| ' + state.channel + ' | ' + state.columns.map(c => JSON.stringify(
      c.status === 'find' ? { status: 'find', angle: { svd: Number(c.bearing_deg || 0) } }
        : { status: c.status.replace(/_/g, ' ') })).join(' | ') + ' |\n';
    return head + sep + row;
  }
  function jsonText() { return JSON.stringify(toJSON(), null, 2); }
  function applyJSON(doc) {
    if (doc.channel !== undefined) state.channel = Number(doc.channel);
    if (Array.isArray(doc.columns)) {
      state.columns = doc.columns.map(c => {
        const out = { point: [Number(c.point[0]), Number(c.point[1])], status: c.status };
        if (c.status === 'find') out.bearing_deg = Number(c.bearing_deg !== undefined ? c.bearing_deg : 0);
        return out;
      });
    }
    if (doc.rho) {
      if (doc.rho.model) state.rhoModel = doc.rho.model;
      if (Number.isFinite(Number(doc.rho.min))) state.rhoMin = Number(doc.rho.min);
      if (Number.isFinite(Number(doc.rho.max))) state.rhoMax = Number(doc.rho.max);
      if (Number.isFinite(Number(doc.rho.fixed))) state.rhoFixed = Number(doc.rho.fixed);
    }
    if (doc.view) {
      const v = doc.view, bb = v.bounds;
      if (bb && bb.length === 4) { state.uMin = +bb[0]; state.uMax = +bb[1]; state.vMin = +bb[2]; state.vMax = +bb[3]; }
      if (Number.isFinite(Number(v.resolution))) state.res = Number(v.resolution);
      if (v.metric) state.metric = v.metric;
      if (v.stat) state.stat = v.stat;
      if (v.condition) state.condition = v.condition;
      if (Number.isFinite(Number(v.order))) state.order = Number(v.order);
      if (Number.isFinite(Number(v.angle_bins))) state.angleBins = Number(v.angle_bins);
      if (Number.isFinite(Number(v.sides))) state.sides = Number(v.sides);
    }
    if (doc.display) {
      const d = doc.display;
      if (d.color_mode) state.colorMode = d.color_mode;
      if (d.robust !== undefined) state.robust = !!d.robust;
      if (d.mask_pdet !== undefined) state.maskPdet = !!d.mask_pdet;
      if (d.mask_angle !== undefined) state.maskAngle = !!d.mask_angle;
      if (Number.isFinite(Number(d.pdet_min))) state.pdetMin = Number(d.pdet_min);
      if (Number.isFinite(Number(d.pdet_level))) state.pdetLevel = Number(d.pdet_level);
      if (d.pdet_half !== undefined) state.pdetHalf = !!d.pdet_half;
    }
  }

  async function applyText(text) {
    const trimmed = text.trim();
    if (trimmed.startsWith('{')) {
      let doc = null;
      try { doc = JSON.parse(trimmed); } catch (e) { throw new Error('JSON 解析失败：' + e.message); }
      if (doc && doc.schema === 'q2-doublet/v1') {
        const columns = [{ point: doc.s1, status: 'find', bearing_deg: doc.theta1 }].concat(doc.extra_columns || []);
        state.channel = Number(doc.channel || state.channel);
        state.columns = columns.map(c => ({ point: [Number(c.point[0]), Number(c.point[1])], status: c.status,
          bearing_deg: c.bearing_deg !== undefined ? Number(c.bearing_deg) : (c.status === 'find' ? 0 : undefined) }));
        state.columns.forEach(c => { if (c.bearing_deg === undefined) delete c.bearing_deg; });
        rebuild();
        return '已把双点交会文档转成 ' + state.columns.length + ' 列观测';
      }
      if (doc && doc.schema === 'q2-channel/v1' && doc.rho) {   /* 本页完整导出文档 */
        applyJSON(doc); rebuild();
        return '已载入知识矩阵文档（含 ρ / 视图 / 显示参数）';
      }
    }
    const resp = await fetch(API.normalize, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: trimmed, channel: state.channel })
    });
    const data = await resp.json();
    if (!resp.ok || data.error) throw new Error(data.error || resp.statusText);
    state.channel = data.channel;
    state.columns = data.columns.map(c => {
      const out = { point: [Number(c.point[0]), Number(c.point[1])], status: c.status };
      if (c.status === 'find') out.bearing_deg = Number(c.bearing_deg || 0);
      return out;
    });
    rebuild();
    return '已载入频道 ' + data.channel + ' 的 ' + data.columns.length + ' 列观测';
  }

  /* ---------------------------------------------------------------- 观测表 */
  function coordInput(c, i, k) {
    const input = Q2.el('input', {
      type: 'number', step: 'any', value: Number(c.point[k]),
      'aria-label': 'P' + (i + 1) + (k ? ' y' : ' x')
    });
    input.addEventListener('input', () => {
      const v = Number(input.value);
      if (Number.isFinite(v)) { c.point[k] = v; schedule(); }
    });
    return input;
  }
  function statusSelect(c, i) {
    const sel = Q2.el('select', { 'aria-label': 'P' + (i + 1) + ' 状态' },
      STATUSES.map(s => Q2.el('option', { value: s[0], text: s[1], selected: s[0] === c.status })));
    sel.value = c.status;
    sel.addEventListener('change', () => {
      c.status = sel.value;
      if (c.status === 'find' && c.bearing_deg === undefined) c.bearing_deg = 0;
      refreshTable(); schedule();
    });
    return sel;
  }
  function bearingInput(c, i) {
    const input = Q2.el('input', {
      type: 'number', step: 'any', disabled: c.status !== 'find',
      value: Number(c.bearing_deg === undefined ? 0 : c.bearing_deg),
      'aria-label': 'P' + (i + 1) + ' 示向角'
    });
    input.addEventListener('input', () => {
      const v = Number(input.value);
      if (Number.isFinite(v)) { c.bearing_deg = v; schedule(); }
    });
    return input;
  }
  function tableRows() {
    return state.columns.map((c, i) => ({
      cls: ST_CLS[c.status] || '',
      cells: [
        { text: 'P' + (i + 1), cls: 'num' },
        { node: coordInput(c, i, 0) },
        { node: coordInput(c, i, 1) },
        { node: statusSelect(c, i) },
        { node: bearingInput(c, i) },
        { node: Q2.el('button', {
          class: 'btn tiny', text: '删除',
          onclick: () => {
            state.columns.splice(i, 1);
            if (!state.columns.length) state.columns.push({ point: [0, 0], status: 'not_measure' });
            refreshTable(); schedule();
          }
        }) }
      ]
    }));
  }

  let tableWrap = null;
  function refreshTable() {
    if (!tableWrap) return;
    Q2.clear(tableWrap);
    tableWrap.appendChild(ctl.table({
      head: ['列', 'x (m)', 'y (m)', '频道 ' + state.channel + ' 状态', '示向角 (°)', '操作'],
      rows: tableRows()
    }));
  }

  /* ---------------------------------------------------------------- 面板 */
  function buildSections() {
    /* 1) 数据 · 导入导出粘贴 */
    section({
      id: 'data', icon: '⇅', title: '数据 · 导入 / 导出 / 粘贴', open: false,
      tag: 'JSON · Markdown · 剪贴板',
      build(body) {
        body.appendChild(Q2.io.section({
          placeholder: '粘贴 q2-channel/v1 JSON、Channel × Path Markdown 表，或双点交会文档。',
          getJSON: () => ({ name: 'channel-' + state.channel + '.json', text: jsonText() }),
          getMarkdown: () => ({ name: 'channel-' + state.channel + '.md', text: toMarkdown() }),
          applyText: applyText,
          example: () => jsonText(),
          convert: {
            label: '载入到双点工作区 →',
            run: () => {
              if (!Q2.ws.doublet) { Q2.ui.toast('双点工作区没有加载', 'err'); return; }
              const f = firstFind();
              if (!f) { Q2.ui.toast('当前观测表没有 find 观测，双点工作区需要 1 个', 'err'); return; }
              const others = state.columns.filter(c => c !== f);
              if (others.some(c => c.status !== 'not_measure')) {
                Q2.ui.toast('当前表还有 ' + others.filter(c => c.status !== 'not_measure').length +
                  ' 个非未观测列，双点模型只含 1 次观测；已按 P1 载入', 'err');
              }
              Q2.ws.doublet.loadColumns(state.columns, state.channel);
              Q2.activateWorkspace('doublet');
            }
          },
          note: '本页导出 JSON 无损保存真实观测坐标（schema q2-channel/v1）；Markdown 是项目通用的 Channel × Path 表，' +
            '旧版矩阵 Markdown 的坐标是格心，导入时按表头坐标使用。'
        }));
      }
    });

    /* 2) 观测表 */
    section({
      id: 'matrix', icon: '▦', title: '观测表（Channel × Path）', open: true,
      tag: state.columns.length + ' 列',
      build(body) {
        body.appendChild(ctl.grid(2,
          ctl.number({ id: 'channel', label: '频道号', value: state.channel, min: 1, max: 20, step: 1,
            oninput: v => { state.channel = Math.round(v); refreshTable(); schedule(); } }),
          ctl.select({
            id: 'preset', label: '载入示例', value: '', options: [['', '（选择示例）'],
              ['cross', '两次交会示例'], ['single', '单次观测（退化为双点）'], ['negative', '含未测到负例']],
            onchange: v => { if (v) loadPreset(v); }
          })
        ));
        tableWrap = Q2.el('div', { id: 'matrixTable', class: 'field' });
        tableWrap.appendChild(ctl.table({
          head: ['列', 'x (m)', 'y (m)', '频道 ' + state.channel + ' 状态', '示向角 (°)', '操作'],
          rows: tableRows()
        }));
        body.appendChild(tableWrap);
        body.appendChild(ctl.row(
          ctl.button({ label: '新增观测列', kind: 'tiny', onclick: () => { state.columns.push({ point: [1000, 0], status: 'not_measure' }); refreshTable(); schedule(); } }),
          ctl.button({ label: '新增待测候选', kind: 'tiny', onclick: () => { state.columns.push({ point: [850, 520], status: 'not_measure' }); refreshTable(); schedule(); } }),
          ctl.button({ label: '清空为一行', kind: 'tiny', onclick: () => { state.columns = [{ point: [0, 0], status: 'find', bearing_deg: 0 }]; state.channel = 1; rebuild(); } })
        ));
        body.appendChild(ctl.note('状态语义：find = 测到并测向（±1°）、not_find = 未测到、not_measure = 未观测（不参与推断）、' +
          'near = 5 m 内信号过强。同坐标重复记录去重，矛盾记录会被后端拒绝。'));
      }
    });

    /* 3) 模型与精度 */
    section({
      id: 'model', icon: 'ρ', title: '模型 · ρ · 指标 · 精度', open: true,
      build(body) {
        body.appendChild(ctl.select({
          id: 'rhoModel', label: 'ρ 的认知模型（所有观测共用同一个 ρ）', value: state.rhoModel,
          options: [['uniform', '只知道区间，取 ρ~U[ρ_min,ρ_max]（可算期望/方差）'],
                    ['fixed', '假设 ρ = ρ_fixed 已知（指示函数）']],
          onchange: v => { state.rhoModel = v; rebuild(); }
        }));
        body.appendChild(ctl.grid(3,
          ctl.number({ id: 'rhoMin', label: 'ρ_min (m)', value: state.rhoMin, step: 25, min: 100, max: 3000, oninput: v => { state.rhoMin = v; schedule(); } }),
          ctl.number({ id: 'rhoMax', label: 'ρ_max (m)', value: state.rhoMax, step: 25, min: 100, max: 3000, oninput: v => { state.rhoMax = v; schedule(); } }),
          ctl.number({ id: 'rhoFixed', label: 'ρ_fixed (m)' + (state.rhoModel === 'fixed' ? '' : '（仅 fixed 模型）'),
            value: state.rhoFixed, step: 25, min: 100, max: 3000, disabled: state.rhoModel !== 'fixed',
            oninput: v => { state.rhoFixed = v; schedule(); } })
        ));
        body.appendChild(ctl.grid(3,
          ctl.select({
            id: 'metric', label: '颜色指标', value: state.metric,
            options: [['Rmin', 'R_min：最小覆盖圆半径 (m)'], ['N20', 'N20_lb：20 m 圆覆盖数下界'], ['pdet', 'p_det：测到信号概率']],
            onchange: v => { state.metric = v; schedule(); }
          }),
          ctl.select({
            id: 'stat', label: '统计量', value: state.stat,
            options: [['mean', '期望'], ['variance', '方差']],
            onchange: v => { state.stat = v; schedule(); }
          }),
          ctl.select({
            id: 'condition', label: '统计条件', value: state.condition,
            options: [['all', '全部结果（含未测到）'], ['detected', '仅测到信号（含 near）']],
            onchange: v => { state.condition = v; schedule(); }
          })
        ));
        body.appendChild(ctl.note('定位区域采用保守端点：正例半径取上限、负例仅排除下限；概率权重使用全部观测对共同 ρ 的约束。' +
          'N20 是多边形近似区域的覆盖数下界，不是连续圆覆盖的精确最优值。'));
        body.appendChild(ctl.grid(2,
          ctl.slider({ id: 'res', label: '热图网格分辨率', min: 3, max: 51, step: 2, value: state.res,
            display: v => v + ' × ' + v, oninput: v => { state.res = v; schedule(); } }),
          ctl.select({ id: 'order', label: '求积阶数', value: String(state.order),
            options: [['3', '3'], ['5', '5'], ['8', '8'], ['10', '10']],
            onchange: v => { state.order = Number(v); schedule(); } })
        ));
        body.appendChild(ctl.grid(2,
          ctl.select({ id: 'angleBins', label: '角度积分段', value: String(state.angleBins),
            options: [['48', '48'], ['96', '96'], ['192', '192'], ['360', '360']],
            onchange: v => { state.angleBins = Number(v); schedule(); } }),
          ctl.select({ id: 'sides', label: '圆周边数', value: String(state.sides),
            options: [['96', '96'], ['192', '192'], ['360', '360']],
            onchange: v => { state.sides = Number(v); schedule(); } })
        ));
        body.appendChild(ctl.check({ id: 'auto', label: '观测表/参数改动后自动重算（大网格建议关闭）', checked: state.auto,
          onchange: v => { state.auto = v; if (v) schedule(); } }));
        body.appendChild(ctl.grid(2,
          ctl.number({ id: 'uMin', label: 'x 最小', value: state.uMin, step: 50, oninput: v => { state.uMin = v; schedule(); } }),
          ctl.number({ id: 'uMax', label: 'x 最大', value: state.uMax, step: 50, oninput: v => { state.uMax = v; schedule(); } }),
          ctl.number({ id: 'vMin', label: 'y 最小', value: state.vMin, step: 50, oninput: v => { state.vMin = v; schedule(); } }),
          ctl.number({ id: 'vMax', label: 'y 最大', value: state.vMax, step: 50, oninput: v => { state.vMax = v; schedule(); } })
        ));
        body.appendChild(ctl.row(
          ctl.button({ label: '全场范围', kind: 'tiny', onclick: () => { state.uMin = -1800; state.uMax = 1800; state.vMin = -1800; state.vMax = 1800; rebuild(); } }),
          ctl.button({ label: '放大到可能区域', kind: 'tiny', onclick: zoomToSources })
        ));
        body.appendChild(ctl.check({ id: 'limitTarget', label: '只计算落在场地圆内的格点', checked: state.limitTarget,
          onchange: v => { state.limitTarget = v; schedule(); } }));
      }
    });

    /* 4) 辅助线（与双点工作区同一份注册表） */
    section({
      id: 'lines', icon: '✎', title: '辅助线图层', open: true, tag: '双工作区共用',
      build(body) {
        body.appendChild(ctl.hint('矩阵工作区原来只有可能区域一块多边形；现在场地圆、测向楔形、±1° 射线、近距圈、' +
          '排除盘、p_det 等值线、最优格点都来自与双点工作区相同的绘制器。'));
        body.appendChild(Q2.linesPanel('matrix', lastView, () => render(revision)));
      }
    });

    /* 5) 显示与掩膜 */
    section({
      id: 'display', icon: '◐', title: '配色 · 掩膜 · 等值线', open: false,
      build(body) {
        body.appendChild(ctl.grid(2,
          ctl.select({
            id: 'colorMode', label: '颜色映射函数', value: state.colorMode,
            options: [['log', 'log10(1+z)，推荐'], ['sqrt', 'sqrt(z)'], ['linear', '线性'], ['rank', '分位/秩着色']],
            onchange: v => { state.colorMode = v; render(revision); }
          }),
          ctl.number({ id: 'pdetLevel', label: 'p_det 等值线水平', value: state.pdetLevel, step: 0.001, min: 0.001, max: 0.9999,
            oninput: v => { state.pdetLevel = v; render(revision); } })
        ));
        body.appendChild(ctl.grid(2,
          ctl.check({ id: 'pdetHalf', label: '同时画 p_det = 0.5 线', checked: state.pdetHalf,
            onchange: v => { state.pdetHalf = v; render(revision); } }),
          ctl.check({ id: 'robust', label: '线性配色按 2%–98% 分位裁剪', checked: state.robust,
            onchange: v => { state.robust = v; render(revision); } })
        ));
        body.appendChild(ctl.check({ id: 'showGrey', label: '无效 / 掩膜 / 场地外格点用灰色底表示', checked: state.showGrey,
          onchange: v => { state.showGrey = v; render(revision); } }));
        body.appendChild(ctl.check({ id: 'maskAngle', label: '屏蔽近平行交会带（以第一个 find 观测为参考方向）', checked: state.maskAngle,
          onchange: v => { state.maskAngle = v; render(revision); } }));
        body.appendChild(ctl.grid(2,
          ctl.check({ id: 'maskPdet', label: '屏蔽 p_det 低于阈值的格点', checked: state.maskPdet,
            onchange: v => { state.maskPdet = v; render(revision); } }),
          ctl.number({ id: 'pdetMin', label: 'p_det 阈值', value: state.pdetMin, step: 0.005, min: 0, max: 1,
            oninput: v => { state.pdetMin = v; if (state.maskPdet) render(revision); } })
        ));
      }
    });

    /* 6) 探针 */
    section({
      id: 'probe', icon: '◎', title: '探针 · 单点检验', open: false,
      build(body) {
        body.appendChild(ctl.hint('点击热图任意位置也会触发探针；探针给出该点在两种统计条件下的期望/方差、分区归属，' +
          '以及到各观测点的距离。'));
        body.appendChild(ctl.grid(2,
          ctl.number({ id: 'probeX', label: '候选点 x (m)', value: 850, step: 10 }),
          ctl.number({ id: 'probeY', label: '候选点 y (m)', value: 520, step: 10 })
        ));
        body.appendChild(ctl.row(
          ctl.button({ label: '探测该点', kind: 'primary tiny',
            onclick: () => probeAt(Number(document.getElementById('probeX').value), Number(document.getElementById('probeY').value)) }),
          ctl.button({ label: '把该点加入观测表', kind: 'tiny', id: 'addProbe',
            onclick: () => { addColumn([Number(document.getElementById('probeX').value), Number(document.getElementById('probeY').value)]); } })
        ));
      }
    });
  }

  function loadPreset(kind) {
    if (kind === 'cross') {
      state.columns = [
        { point: [0, 0], status: 'find', bearing_deg: 0 },
        { point: [850, 520], status: 'find', bearing_deg: (Math.atan2(-520, 150) * 180 / Math.PI + 360) % 360 }
      ];
    } else if (kind === 'single') {
      state.columns = [{ point: [0, 0], status: 'find', bearing_deg: 0 }];
    } else if (kind === 'negative') {
      state.columns = [
        { point: [0, 0], status: 'find', bearing_deg: 0 },
        { point: [-1200, 0], status: 'not_find' }
      ];
    }
    rebuild();
  }

  function zoomToSources() {
    if (!last || !last.source_bounds) { Q2.ui.toast('请先计算一次', 'err'); return; }
    const b = last.source_bounds;
    const pad = Math.max(30, Math.max(b[1] - b[0], b[3] - b[2]) * 0.6);
    state.uMin = Math.round(b[0] - pad); state.uMax = Math.round(b[1] + pad);
    state.vMin = Math.round(b[2] - pad); state.vMax = Math.round(b[3] + pad);
    if (state.res < 25) state.res = 25;
    rebuild();
  }

  /* ---------------------------------------------------------------- 探针 */
  async function probeAt(x, y) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return;
    const px = document.getElementById('probeX'), py = document.getElementById('probeY');
    if (px) px.value = String(Number(x.toFixed(1)));
    if (py) py.value = String(Number(y.toFixed(1)));
    Q2.probe.render({ title: '探针 · 计算中…' });
    try {
      const body = payload();
      body.point = [x, y];
      const resp = await fetch(API.probe, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
      });
      const d = await resp.json();
      if (!resp.ok || d.error) throw new Error(d.error || resp.statusText);
      Q2.probe.lastPoint = [x, y];
      Q2.probe.last = d;
      const rows = (d.distances || []).map(o => ([
        { text: '(' + fmt.num(o.point[0], 0) + ', ' + fmt.num(o.point[1], 0) + ')' },
        { html: '<span class="hint">' + (STATUSES.filter(s => s[0] === o.status)[0] || ['', o.status])[1] + '</span>' },
        { text: fmt.num(o.distance, 1), cls: 'num' }
      ]));
      Q2.probe.render({
        title: '探针 · 候选点 (' + fmt.num(x, 1) + ', ' + fmt.num(y, 1) + ') m',
        badges: [
          { kind: d.zone === 'certain' ? 'ok' : d.zone === 'blind' ? 'bad' : 'warn',
            text: (Q2.zoneOf(d.zone) || {}).name || d.zone },
          { text: 'p_det = ' + fmt.num(d.pdet, 5) },
          { text: d.condition + ' 条件下 ' + (d.metric === 'pdet' ? 'p_det' : d.metric) + ' = ' +
            (d.mean === null ? '无定义' : fmt.num(d.mean, 3)) + '，方差 ' + (d.variance === null ? '—' : fmt.num(d.variance, 3)) },
          { kind: 'plain', text: '另一种条件（' + d.condition_other + '）= ' +
            (d.mean_other === null ? '无定义' : fmt.num(d.mean_other, 3)) },
          { kind: 'plain', text: '当前可能区域 R_min=' + fmt.num(d.baseline.Rmin, 1) + ' m · N20下界=' + d.baseline.N20 }
        ],
        table: { head: ['观测点', '状态', '到候选点距离 (m)'], rows: rows },
        note: d.repeated
          ? { text: '该坐标已有观测记录：直接采用原结果，不再重新抽取 ρ 与测向误差，新增信息为零。', kind: 'warn' }
          : { text: 'p_det 是共同 ρ 后验下该点测到信号的权重；必然测到 = 所有正权重源点都满足 d < U(G)。' },
        actions: [
          ctl.button({ label: '把该点加入观测表', kind: 'tiny', onclick: () => addColumn([x, y]) }),
          ctl.button({ label: '载入到双点工作区', kind: 'tiny', onclick: () => {
            if (!Q2.ws.doublet) { Q2.ui.toast('双点工作区没有加载', 'err'); return; }
            if (!Q2.ws.doublet.loadColumns(state.columns, state.channel)) {
              Q2.ui.toast('当前表需要恰好 1 个 find 观测才能载入双点工作区', 'err');
              return;
            }
            Q2.activateWorkspace('doublet');
          } })
        ]
      });
      render(revision);
    } catch (err) {
      Q2.probe.render({ title: '探针失败', note: { text: err.message, kind: 'bad' } });
    }
  }

  function addColumn(point) {
    state.columns.push({ point: [Number(point[0]), Number(point[1])], status: 'not_measure' });
    if (Q2.activeWorkspace() && Q2.activeWorkspace().id === 'matrix') { refreshTable(); schedule(); }
    Q2.ui.toast('已加入待测候选列 (' + fmt.num(point[0], 1) + ', ' + fmt.num(point[1], 1) + ')');
  }

  /* ---------------------------------------------------------------- 注册 */
  Q2.registerWorkspace({
    id: 'matrix',
    label: '知识矩阵 Channel×Path',
    hint: '多观测知识结构 → 下一个观测点',
    probeHint: '点击热图任意位置即可检验候选点；会给出两种统计条件下的期望/方差与到各观测的距离。',
    buildSections: buildSections,
    draw: draw,
    activate() { tableWrap = document.getElementById('matrixTable'); },
    onPlotClick: probeAt
  });

  Q2.ws = Q2.ws || {};
  Q2.ws.matrix = {
    state: state,
    addColumn: addColumn,
    loadDocument(doc, silent) {
      state.channel = Number(doc.channel || 1);
      state.columns = (doc.columns || []).map(c => {
        const out = { point: [Number(c.point[0]), Number(c.point[1])], status: c.status };
        if (c.status === 'find') out.bearing_deg = Number(c.bearing_deg || 0);
        return out;
      });
      if (!silent) rebuild();
    }
  };
})(window);
