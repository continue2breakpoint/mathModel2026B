/* 题目2 统一编辑台 · 共用运行时
 *
 * 这一层只做"外壳 + 共用部件"，不含任何算法：
 *   - 抽屉式编辑台（收起 / 半屏 / 全屏）
 *   - 小节（section）与左侧图标栏
 *   - 统一控件工厂（数字 / 滑块 / 下拉 / 复选 / 按钮）
 *   - 辅助线注册表（图层定义、开关面板、绘制器）
 *   - 统一配色管线 + 统一画布布局（两个工作区共用一张图）
 *   - 统一导入 / 导出 / 粘贴面板
 *   - 探针卡片、状态条、提示条
 * 两个工作区（双点交会、知识矩阵）只负责把自己的数据整理成 view.layers，
 * 所有绘制、样式、交互都走本文件的同一份代码，因此外观必然一致。
 */
(function (global) {
  'use strict';

  const Q2 = global.Q2 = {};

  /* ------------------------------------------------------------------ 0. 基础工具 */
  function $(id) { return document.getElementById(id); }

  function append(node, kids) {
    if (kids === null || kids === undefined || kids === false) return node;
    if (Array.isArray(kids)) { kids.forEach(k => append(node, k)); return node; }
    if (typeof kids === 'string' || typeof kids === 'number') {
      node.appendChild(document.createTextNode(String(kids)));
      return node;
    }
    node.appendChild(kids);
    return node;
  }

  function el(tag, attrs, kids) {
    const node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(k => {
        const v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === 'class') node.className = v;
        else if (k === 'text') node.textContent = v;
        else if (k === 'html') node.innerHTML = v;
        else if (k === 'dataset') { Object.keys(v).forEach(d => { node.dataset[d] = v[d]; }); }
        else if (k === 'style') node.setAttribute('style', v);
        else if (k.slice(0, 2) === 'on' && typeof v === 'function') node.addEventListener(k.slice(2), v);
        else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'open' || k === 'hidden') node[k] = v;
        else node.setAttribute(k, v);
      });
    }
    return append(node, kids);
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }
  function on(node, ev, fn) { node.addEventListener(ev, fn); return node; }
  /* 读取复选开关的当前状态（工作区用同一批 id，保证两个页面行为一致） */
  function chk(id, dflt) { const n = $(id); return n ? !!n.checked : !!dflt; }

  /* ------------------------------------------------------------------ 1. 数值格式 */
  const fmt = {
    num(x, p) {
      if (x === null || x === undefined || !Number.isFinite(Number(x))) return '—';
      const a = Math.abs(x);
      if (a !== 0 && (a < 1e-3 || a >= 1e5)) return Number(x).toExponential(2);
      return Number(x).toFixed(p === undefined ? 2 : p);
    },
    tick(x) {
      if (!Number.isFinite(x)) return '';
      const a = Math.abs(x);
      if (a !== 0 && (a < 1e-3 || a >= 1e4)) return x.toExponential(2);
      return String(Number(x.toPrecision(4)));
    },
    deg(x) { return fmt.num(x, 1) + '°'; }
  };

  const ZONE = {
    certain: { key: 'certain', name: '一定测到', cls: 'zone-certain', color: '#00b7c7' },
    probabilistic: { key: 'probabilistic', name: '概率测到', cls: 'zone-probabilistic', color: '#d9a300' },
    blind: { key: 'blind', name: '一定测不到', cls: 'zone-blind', color: '#b91c1c' }
  };
  const zoneOf = z => ZONE[z] || ZONE.probabilistic;

  /* ------------------------------------------------------------------ 2. 偏好持久化 */
  const store = {
    key: 'q2.ui.v1',
    data: {},
    load() {
      try { this.data = JSON.parse(global.localStorage.getItem(this.key) || '{}') || {}; }
      catch (e) { this.data = {}; }
      return this.data;
    },
    save() {
      try { global.localStorage.setItem(this.key, JSON.stringify(this.data)); } catch (e) { /* 隐私模式忽略 */ }
    }
  };

  /* ------------------------------------------------------------------ 3. 状态条 / 提示 */
  const ui = {};
  ui.pill = function (text, kind) {
    const node = $('headStatus');
    if (!node) return;
    node.textContent = text;
    node.className = 'pill' + (kind ? ' ' + kind : '');
  };
  ui.status = function (text) {
    const node = $('status');
    if (node) node.textContent = text;
  };
  let toastTimer = null;
  ui.toast = function (msg, kind) {
    const node = $('toast');
    if (!node) return;
    node.textContent = msg;
    node.className = 'toast' + (kind === 'err' ? ' err' : '');
    node.hidden = false;
    if (toastTimer) global.clearTimeout(toastTimer);
    toastTimer = global.setTimeout(() => { node.hidden = true; }, kind === 'err' ? 5200 : 2600);
  };

  /* ------------------------------------------------------------------ 4. 抽屉（收纳页） */
  const DRAWER_STATES = ['collapsed', 'half', 'full'];
  const drawer = {
    state: 'half',
    set(state, opts) {
      opts = opts || {};
      if (DRAWER_STATES.indexOf(state) < 0) state = 'half';
      this.state = state;
      const node = $('drawer');
      if (node) node.dataset.state = state;
      const seg = $('drawerSeg');
      if (seg && seg.children) {
        Array.prototype.forEach.call(seg.children, b => {
          b.setAttribute('aria-pressed', String(b.dataset.state === state));
        });
      }
      if (!opts.silent) {
        store.data.drawer = state; store.save();
        syncURL();
      }
      resizeSoon();
      if (state !== 'collapsed' && opts.section) showSection(opts.section);
    },
    cycle() {
      const i = DRAWER_STATES.indexOf(this.state);
      this.set(DRAWER_STATES[(i + 1) % DRAWER_STATES.length]);
    }
  };
  Q2.drawer = drawer;

  let resizeTimer = null;
  function resizeSoon() {
    if (resizeTimer) global.clearTimeout(resizeTimer);
    resizeTimer = global.setTimeout(() => Q2.plot.resize(), 240);
  }

  /* ------------------------------------------------------------------ 5. 小节与图标栏 */
  const sections = [];
  function section(def) { sections.push(def); return def; }

  function showSection(id) {
    const body = $('drawerBody');
    if (!body || !body.children) return;
    Array.prototype.forEach.call(body.children, node => {
      if (node.dataset && node.dataset.sec === id) {
        node.open = true;
        if (node.scrollIntoView) node.scrollIntoView({ block: 'nearest' });
        node.classList.add('flash');
        global.setTimeout(() => node.classList.remove('flash'), 1200);
      }
    });
    Array.prototype.forEach.call(($('rail') || { children: [] }).children, b => {
      if (b.dataset && b.dataset.sec) b.classList.toggle('active', b.dataset.sec === id);
    });
  }

  function renderSections() {
    const body = $('drawerBody');
    const rail = $('rail');
    if (!body || !rail) return;
    clear(body); clear(rail);
    sections.forEach(def => {
      const wrap = el('div', { class: 'body' });
      const acc = el('details', { class: 'acc', dataset: { sec: def.id }, open: def.open !== false }, [
        el('summary', {}, [
          el('span', { text: (def.icon ? def.icon + '  ' : '') + def.title }),
          def.tag ? el('span', { class: 'tag', text: '· ' + def.tag }) : null
        ]),
        wrap
      ]);
      body.appendChild(acc);
      if (def.build) def.build(wrap);
      rail.appendChild(el('button', {
        class: 'rail-wide', title: def.title, text: def.icon || '•',
        dataset: { sec: def.id },
        onclick: () => {
          if (drawer.state === 'collapsed') drawer.set('half', { section: def.id });
          else showSection(def.id);
        }
      }));
    });
  }

  /* ------------------------------------------------------------------ 6. 控件工厂 */
  const ctl = {};
  ctl.hint = text => el('div', { class: 'hint', text: text });
  ctl.note = (text, kind) => el('div', { class: 'note' + (kind ? ' ' + kind : ''), text: text });
  ctl.row = (...kids) => el('div', { class: 'row' }, kids);
  ctl.grid = (cols, ...kids) => el('div', { class: cols === 3 ? 'grid3' : 'grid2' }, kids);

  ctl.number = function (o) {
    const input = el('input', {
      type: 'number', value: o.value, step: o.step === undefined ? 'any' : o.step,
      min: o.min, max: o.max, id: o.id, 'aria-label': o.label || o.id, disabled: !!o.disabled
    });
    const fire = () => { if (o.oninput) o.oninput(Number(input.value)); };
    on(input, 'input', fire); on(input, 'change', fire);
    if (!o.label) return input;
    return el('label', { class: 'field' }, [
      el('span', {}, [o.label, o.suffix ? el('span', { class: 'val', text: ' ' + o.suffix }) : null]),
      input
    ]);
  };

  ctl.slider = function (o) {
    const val = el('span', { class: 'val', text: o.display ? o.display(o.value) : String(o.value) });
    const range = el('input', {
      type: 'range', min: o.min, max: o.max, step: o.step === undefined ? 1 : o.step,
      value: o.value, id: o.id + 'R', 'aria-label': (o.label || o.id) + ' 滑块'
    });
    const num = el('input', {
      type: 'number', value: o.value, step: o.step === undefined ? 1 : o.step,
      id: o.id, 'aria-label': o.label || o.id
    });
    const push = (v, from) => {
      v = Number(v);
      if (!Number.isFinite(v)) return;
      if (o.min !== undefined) v = Math.max(Number(o.min), v);
      if (o.max !== undefined) v = Math.min(Number(o.max), v);
      range.value = v; if (from !== 'num') num.value = String(v);
      val.textContent = o.display ? o.display(v) : String(v);
      if (o.oninput) o.oninput(v);
    };
    on(range, 'input', () => push(range.value, 'range'));
    on(num, 'input', () => push(num.value, 'num'));
    return el('div', { class: 'field' }, [
      el('label', {}, [o.label || o.id, val]),
      el('div', { class: 'row tight' }, [el('div', { class: 'grow' }, range), num])
    ]);
  };

  ctl.select = function (o) {
    const sel = el('select', { id: o.id, 'aria-label': o.label || o.id });
    (o.options || []).forEach(opt => {
      const value = Array.isArray(opt) ? opt[0] : opt;
      const label = Array.isArray(opt) ? opt[1] : opt;
      sel.appendChild(el('option', { value: value, text: label, selected: String(value) === String(o.value) }));
    });
    sel.value = o.value;
    on(sel, 'change', () => { if (o.onchange) o.onchange(sel.value); });
    if (!o.label) return sel;
    return el('label', { class: 'field' }, [el('span', { text: o.label }), sel]);
  };

  ctl.check = function (o) {
    const box = el('input', { type: 'checkbox', id: o.id, checked: !!o.checked });
    on(box, 'change', () => { if (o.onchange) o.onchange(box.checked); });
    return el('label', { class: 'chk' }, [
      box,
      o.swatch ? el('span', { class: 'swatch', style: 'background:' + o.swatch }) : null,
      el('span', { text: o.label, title: o.title || '' })
    ]);
  };

  ctl.button = function (o) {
    return el('button', {
      class: 'btn' + (o.kind ? ' ' + o.kind : ''), id: o.id, text: o.label,
      title: o.title || '', disabled: !!o.disabled, onclick: o.onclick
    });
  };

  ctl.badges = function (items) {
    return el('div', { class: 'badges' }, (items || []).map(b =>
      el('span', { class: 'badge' + (b.kind ? ' ' + b.kind : ''), html: b.html || b.text })));
  };

  ctl.table = function (o) {
    const thead = el('thead', {}, el('tr', {}, (o.head || []).map(h => el('th', { text: h }))));
    const tbody = el('tbody', {});
    (o.rows || []).forEach(r => {
      tbody.appendChild(el('tr', { class: r.cls || '' },
        (r.cells || []).map(c => {
          const td = el('td', { class: c.cls || '' });
          if (c.node) td.appendChild(c.node);
          else if (c.html !== undefined) td.innerHTML = c.html;
          else td.textContent = c.text === undefined ? '' : c.text;
          return td;
        })));
    });
    return el('div', { class: 'tbl-wrap' }, el('table', { class: 'tbl' }, [thead, tbody]));
  };

  Q2.ctl = ctl;
  Q2.ui = ui;
  Q2.el = el;
  Q2.clear = clear;
  Q2.fmt = fmt;
  Q2.zoneOf = zoneOf;
  Q2.section = section;

  /* ------------------------------------------------------------------ 7. 辅助线注册表 */
  const SITE_R = 1800;

  function circlePoly(cx, cy, r, sides) {
    const n = sides || 96, out = [];
    for (let i = 0; i <= n; i++) {
      const a = 2 * Math.PI * i / n;
      out.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
    }
    return out;
  }

  function polysTrace(polys, style) {
    const x = [], y = [];
    (polys || []).forEach(p => {
      if (!p || p.length < 3) return;
      p.forEach(q => { x.push(q[0]); y.push(q[1]); });
      x.push(null); y.push(null);
    });
    if (!x.length) return null;
    const trace = {
      type: 'scatter', mode: 'lines', x: x, y: y, name: style.name,
      line: { color: style.color, width: style.width || 1.6, dash: style.dash || 'solid' },
      hoverinfo: style.hover === false ? 'skip' : 'name', showlegend: style.showlegend !== false
    };
    if (style.fill) { trace.fill = 'toself'; trace.fillcolor = style.fill; }
    return trace;
  }

  function rayLine(x0, y0, deg, radius) {
    const t = deg * Math.PI / 180, ux = Math.cos(t), uy = Math.sin(t);
    const b = x0 * ux + y0 * uy, c = x0 * x0 + y0 * y0 - radius * radius;
    const disc = b * b - c;
    const len = disc <= 0 ? radius : (-b + Math.sqrt(disc));
    return { x: [x0, x0 + len * ux], y: [y0, y0 + len * uy] };
  }

  /* 每条辅助线：id / 分组 / 标签（可按工作区覆写）/ 颜色 / 线型 / 依赖的数据层 / 绘制器 */
  const LINES = [
    {
      id: 'target', group: '基准', color: '#f2a900', dash: 'dash', width: 2, on: true, need: 'target',
      label: { doublet: '场地圆 x²+y²=1800²', matrix: '场地圆 x²+y²=1800²' },
      build(view, st) {
        const r = (view.layers.target && view.layers.target.radius) || SITE_R;
        const poly = circlePoly(0, 0, r, 360);
        const t = polysTrace([poly], { name: '场地圆 R=' + fmt.num(r, 0) + ' m', color: st.color, dash: st.dash, width: st.width, hover: false });
        return t ? [t] : [];
      }
    },
    {
      id: 'region', group: '基准', color: '#ed6b25', dash: 'solid', width: 1.8, on: true, need: 'regions',
      fill: 'rgba(237,107,37,.12)',
      label: { doublet: '干扰源可能集 A1（ρ 上下界）', matrix: '当前可能源区 A（全部观测之交）' },
      build(view, st) {
        const groups = {};
        (view.layers.regions || []).forEach(r => {
          const key = r.label || '可能源区';
          (groups[key] = groups[key] || []).push(r.poly);
        });
        const traces = [];
        Object.keys(groups).forEach((key, i) => {
          const t = polysTrace(groups[key], {
            name: key, color: i === 0 ? st.color : '#c99a7a', dash: i === 0 ? st.dash : 'dashdot',
            width: st.width, fill: i === 0 ? st.fill : null
          });
          if (t) traces.push(t);
        });
        return traces;
      }
    },
    {
      id: 'obs', group: '观测', color: '#e72865', dash: 'solid', width: 1, on: true, need: 'obs',
      label: { doublet: '第一观测点 S1', matrix: '观测点 P1…Pn（含未观测列）' },
      build(view, st) {
        const obs = view.layers.obs || [];
        if (!obs.length) return [];
        const seen = obs.filter(o => o.status !== 'not_measure');
        const unmeasured = obs.filter(o => o.status === 'not_measure');
        const traces = [];
        if (seen.length) {
          traces.push({
            type: 'scatter', mode: 'markers+text',
            x: seen.map(o => o.point[0]), y: seen.map(o => o.point[1]),
            text: seen.map(o => o.label), textposition: seen.map((o, i) => i % 2 ? 'bottom center' : 'top center'),
            textfont: { size: 11, color: '#8a1035' },
            marker: { size: 9, color: st.color, line: { color: '#fff', width: 1 } },
            name: view.layers.obsName || '观测点',
            hovertemplate: '%{text} (%{x:.1f}, %{y:.1f}) m<extra></extra>'
          });
        }
        if (unmeasured.length) {
          traces.push({
            type: 'scatter', mode: 'markers+text',
            x: unmeasured.map(o => o.point[0]), y: unmeasured.map(o => o.point[1]),
            text: unmeasured.map(o => o.label), textposition: 'top center',
            textfont: { size: 10.5, color: '#66748c' },
            marker: { size: 8, symbol: 'circle-open', color: '#8b93a7', line: { width: 1.5 } },
            name: '待测候选列（未观测）', hovertemplate: '%{text} (%{x:.1f}, %{y:.1f}) m<extra></extra>'
          });
        }
        return traces;
      }
    },
    {
      id: 'wedge', group: '观测', color: '#7b1fa2', dash: 'dashdot', width: 1.5, on: true, need: 'wedges',
      label: { doublet: '±1° 测向楔形与中心射线', matrix: '各 find 观测的 ±1° 楔形与中心射线' },
      build(view, st) {
        const traces = [];
        (view.layers.wedges || []).forEach((w, i) => {
          const name = '测向楔形 ' + (w.label || ('P' + (i + 1)));
          /* 该观测自己的可行域（矩阵：每个 find 观测一份；双点：与可能源区重合，故不再重复画） */
          const t = polysTrace(w.polys || (w.poly ? [w.poly] : []), {
            name: name, color: st.color, dash: st.dash, width: st.width, showlegend: i === 0
          });
          if (t) traces.push(t);
          if (!Number.isFinite(w.bearing_deg)) return;
          const bis = rayLine(w.point[0], w.point[1], w.bearing_deg, SITE_R);
          traces.push({
            type: 'scatter', mode: 'lines', x: bis.x, y: bis.y, name: name + ' 中心射线',
            line: { color: st.color, width: 1.2, dash: 'dot' }, showlegend: i === 0, hoverinfo: 'skip'
          });
          /* ±1° 边界射线：服务端给了就用服务端的（矩阵），否则本地按场地圆解析（双点） */
          const edges = (w.rays && w.rays.length) ? w.rays : [-1, 1].map(delta => {
            const seg = rayLine(w.point[0], w.point[1], w.bearing_deg + delta, SITE_R);
            return [[w.point[0], w.point[1]], [seg.x[1], seg.y[1]]];
          });
          edges.forEach(r => {
            traces.push({
              type: 'scatter', mode: 'lines', x: [r[0][0], r[1][0]], y: [r[0][1], r[1][1]],
              name: name + ' ±1° 边界', line: { color: st.color, width: 1, dash: 'dot' },
              showlegend: false, hoverinfo: 'skip'
            });
          });
        });
        return traces;
      }
    },
    {
      id: 'near', group: '观测', color: '#0e7490', dash: 'dot', width: 1.4, on: false,
      need: 'near', ws: ['matrix'],
      label: { matrix: '5 m 近距圈（near 观测 / find 内圈）' },
      build(view, st) {
        const polys = (view.layers.near || []).map(o => circlePoly(o.point[0], o.point[1], o.radius, 48));
        const t = polysTrace(polys, { name: '5 m 近距圈', color: st.color, dash: st.dash, width: st.width, hover: false });
        return t ? [t] : [];
      }
    },
    {
      id: 'excluded', group: '观测', color: '#b91c1c', dash: 'dot', width: 1.4, on: false,
      need: 'excluded', ws: ['matrix'],
      label: { matrix: '未测到排除盘（半径 ρ_min）' },
      build(view, st) {
        const polys = (view.layers.excluded || []).map(o => circlePoly(o.point[0], o.point[1], o.radius, 96));
        const t = polysTrace(polys, { name: '未测到排除盘 ρ_min', color: st.color, dash: st.dash, width: st.width, hover: false });
        return t ? [t] : [];
      }
    },
    {
      id: 'certain', group: '分区', color: '#00b7c7', dash: 'solid', width: 2.4, on: true,
      need: 'zones', fill: 'rgba(0,183,199,.13)',
      label: { doublet: '一定测到区（按 ρ_min 判定）', matrix: '必然测到格点（p_det = 1）' },
      build(view, st) {
        const traces = [];
        traces.push({
          type: 'heatmap', x: view.xs, y: view.ys,
          z: view.zone.map(row => row.map(z => z === 'certain' ? 1 : 0)),
          colorscale: [[0, 'rgba(255,255,255,0)'], [1, st.fill || 'rgba(0,183,199,.18)']],
          zmin: 0, zmax: 1, showscale: false, hoverinfo: 'skip', name: '一定测到区', showlegend: false
        });
        const poly = view.layers.certainPoly;
        if (poly && poly.length > 2) {
          const t = polysTrace([poly], { name: '一定测到区边界', color: st.color, width: st.width, fill: null });
          if (t) traces.push(t);
        }
        return traces;
      }
    },
    {
      id: 'blind', group: '分区', color: '#b91c1c', dash: 'solid', width: 2.2, on: true,
      need: 'zones', fill: 'rgba(214,39,40,.42)',
      label: { doublet: '必然无信号区（ρ_max 判定）', matrix: '必然无信号格点（p_det = 0）' },
      build(view, st) {
        const traces = [{
          type: 'heatmap', x: view.xs, y: view.ys,
          z: view.zone.map(row => row.map(z => z === 'blind' ? 1 : 0)),
          colorscale: [[0, 'rgba(255,255,255,0)'], [1, st.fill || 'rgba(214,39,40,.42)']],
          zmin: 0, zmax: 1, showscale: false, hoverinfo: 'skip', name: '必然无信号区', showlegend: false
        }];
        const contour = view.layers.blindContour;
        if (contour) {
          traces.push({
            type: 'contour', x: view.xs, y: view.ys, z: contour,
            showscale: false, hoverinfo: 'skip',
            contours: { type: 'constraint', operation: '=', value: 0 },
            line: { color: st.color, width: st.width }, name: '无信号区边界'
          });
        }
        return traces;
      }
    },
    {
      id: 'probzone', group: '分区', color: '#d9a300', dash: 'solid', width: 1, on: false,
      need: 'zones', fill: 'rgba(255,193,7,.20)', legendOnly: true,
      label: { doublet: '概率测得区着色', matrix: '概率测得格点着色' },
      build(view, st) {
        return [{
          type: 'heatmap', x: view.xs, y: view.ys,
          z: view.zone.map(row => row.map(z => z === 'probabilistic' ? 1 : 0)),
          colorscale: [[0, 'rgba(255,255,255,0)'], [1, st.fill || 'rgba(255,193,7,.20)']],
          zmin: 0, zmax: 1, showscale: false, hoverinfo: 'skip', name: '概率测得区', showlegend: false
        }];
      }
    },
    {
      id: 'pdet', group: '分区', color: '#374151', dash: 'dot', width: 2, on: true, need: 'pdet',
      label: { doublet: 'p_det 等值线（第二点测到概率）', matrix: 'p_det 等值线（下一点测到概率）' },
      build(view, st) {
        const levels = view.layers.contourLevels || [];
        return levels.filter(v => Number.isFinite(v) && v > 0 && v < 1).map(v => ({
          type: 'contour', x: view.xs, y: view.ys, z: view.pdet,
          showscale: false, hoverinfo: 'skip',
          contours: { type: 'constraint', operation: '=', value: v, showlabels: true, labelfont: { size: 10, color: st.color } },
          line: { color: st.color, width: st.width, dash: st.dash },
          name: 'p_det = ' + v
        }));
      }
    },
    {
      id: 'best', group: '候选', color: '#d62728', dash: 'solid', width: 1, on: true, need: 'best',
      label: { doublet: '最优格点（按当前指标）', matrix: '最优格点（按当前指标）' },
      build(view, st) {
        const b = view.layers.best;
        if (!b) return [];
        const zone = zoneOf(b.zone);
        return [{
          type: 'scatter', mode: 'markers+text', x: [b.x], y: [b.y],
          text: ['最优 (' + fmt.num(b.x, 0) + ', ' + fmt.num(b.y, 0) + ')'], textposition: 'bottom center',
          textfont: { size: 11, color: '#8f1d1d' },
          marker: { symbol: 'star', size: 15, color: st.color, line: { color: '#fff', width: 1 } },
          name: '最优格点（' + zone.name + '）',
          hovertemplate: '最优格点 (%{x:.1f}, %{y:.1f}) m<br>' + (view.colorbar || '') + ' = ' + fmt.num(b.value, 4) + '<extra></extra>'
        }];
      }
    },
    {
      id: 'rec', group: '候选', color: '#d62728', dash: 'solid', width: 1, on: true,
      need: 'recommended', ws: ['doublet'],
      label: { doublet: '推荐候选 S2（局部 (850, ±520)）' },
      build(view, st) {
        const rec = view.layers.recommended || [];
        if (!rec.length) return [];
        return [{
          type: 'scatter', mode: 'markers+text',
          x: rec.map(p => p.x), y: rec.map(p => p.y),
          text: rec.map(p => p.label || '推荐 S2'),
          textposition: rec.map((p, i) => i % 2 ? 'bottom center' : 'top center'),
          marker: { symbol: 'star', size: 13, color: st.color, line: { color: '#fff', width: 1 } },
          name: '推荐候选 S2（局部 850, ±520）',
          hovertemplate: '推荐 S2 (%{x:.1f}, %{y:.1f}) m<extra></extra>'
        }];
      }
    }
  ];

  Q2.LINES = LINES;

  function labelOf(line, ws) {
    return (line.label && line.label[ws]) || line.label.default || line.id;
  }
  Q2.lineLabel = labelOf;
  Q2.linesAvailable = function (ws) { return LINES.filter(l => !l.ws || l.ws.indexOf(ws) >= 0); };
  Q2.lineEnabled = function (id) {
    const rec = store.data.lines || (store.data.lines = {});
    const def = LINES.filter(l => l.id === id)[0];
    return rec[id] === undefined ? !!(def && def.on) : !!rec[id];
  };
  Q2.lineToggle = function (id, value) {
    const rec = store.data.lines || (store.data.lines = {});
    rec[id] = !!value; store.save();
  };

  /* 依据当前 view 组合所有启用的辅助线 */
  Q2.overlayTraces = function (view, ws, probe) {
    const traces = [];
    Q2.linesAvailable(ws).forEach(line => {
      if (!Q2.lineEnabled(line.id)) return;
      if (line.need && !view.layers[line.need]) return;
      try {
        const out = line.build(view, line);
        (out || []).forEach(t => { if (t) traces.push(t); });
      } catch (err) {
        ui.toast('辅助线 ' + line.id + ' 绘制失败：' + err.message, 'err');
      }
    });
    if (probe) {
      traces.push({
        type: 'scatter', mode: 'markers', x: [probe[0]], y: [probe[1]],
        marker: { symbol: 'circle-open', size: 17, color: '#111', line: { width: 2 } },
        name: '探针点', showlegend: false, hoverinfo: 'skip'
      });
    }
    return traces;
  };

  /* 辅助线开关面板（两个工作区同一份 UI）
     面板内容在每次绘图后刷新一次：图层是否"当前有数据"取决于最近一次响应。 */
  let linesPanelHost = null, linesPanelWs = null, linesPanelCb = null;

  function buildLinesPanel(ws, view, onChange) {
    const box = el('div', { class: 'field' });
    const groups = {};
    Q2.linesAvailable(ws).forEach(line => { (groups[line.group] = groups[line.group] || []).push(line); });
    Object.keys(groups).forEach(g => {
      box.appendChild(el('div', { class: 'hint', text: '— ' + g + ' —' }));
      groups[g].forEach(line => {
        const available = !line.need || !!(view && view.layers && view.layers[line.need]);
        const chip = el('span', {
          class: 'swatch',
          style: 'background:' + line.color + ';' + (line.fill ? 'border-color:' + line.color : '')
        });
        const box2 = el('input', {
          type: 'checkbox', id: 'line_' + line.id, checked: Q2.lineEnabled(line.id), disabled: !available
        });
        on(box2, 'change', () => { Q2.lineToggle(line.id, box2.checked); if (onChange) onChange(); });
        box.appendChild(el('label', { class: 'chk' + (available ? '' : ' off'), title: available ? '' : '当前工作区或当前数据没有这个图层' }, [
          box2, chip, el('span', { text: labelOf(line, ws) + (available ? '' : '（当前无此图层）') })
        ]));
      });
    });
    return box;
  }

  Q2.linesPanel = function (ws, view, onChange) {
    linesPanelWs = ws; linesPanelCb = onChange;
    linesPanelHost = buildLinesPanel(ws, view, onChange);
    return linesPanelHost;
  };

  /* viz.draw 每次完成后调用：让"可用/不可用"跟最近一次响应一致 */
  Q2.refreshLinesPanel = function (ws, view) {
    if (!linesPanelHost || !linesPanelHost.parentNode || linesPanelWs !== ws) return;
    const fresh = buildLinesPanel(ws, view, linesPanelCb);
    linesPanelHost.parentNode.replaceChild(fresh, linesPanelHost);
    linesPanelHost = fresh;
  };

  /* ------------------------------------------------------------------ 8. 配色与画布 */
  function quantile(sorted, q) {
    if (!sorted.length) return NaN;
    const pos = (sorted.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos);
    return lo === hi ? sorted[lo] : sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  }

  const color = {};
  /* 统一配色管线：掩膜 → 变换（log/sqrt/linear/rank）→ 色标范围与刻度文本 */
  color.prepare = function (o) {
    const ny = o.z.length, nx = ny ? o.z[0].length : 0;
    const out = { z: [], n: 0, min: NaN, max: NaN };
    const finite = [];
    for (let iy = 0; iy < ny; iy++) {
      const row = [];
      for (let ix = 0; ix < nx; ix++) {
        let v = o.z[iy][ix];
        if (v === null || v === undefined || !Number.isFinite(v)) { row.push(null); continue; }
        if (o.maskAngle && o.ref) {
          const yc = o.ys ? o.ys[iy] : 0;
          const dx = o.xs[ix] - o.ref.point[0], dy = yc - o.ref.point[1];
          const t = o.ref.bearing_deg * Math.PI / 180;
          const lu = dx * Math.cos(t) + dy * Math.sin(t);
          const lv = -dx * Math.sin(t) + dy * Math.cos(t);
          if (Math.abs(lu - 855.263) > Math.sqrt(3) * Math.abs(lv)) { row.push(null); continue; }
        }
        if (o.maskPdet) {
          const p = o.pdet[iy][ix];
          if (!(Number.isFinite(p) && p >= o.pdetMin)) { row.push(null); continue; }
        }
        row.push(v); finite.push(v);
      }
      out.z.push(row);
    }
    finite.sort((a, b) => a - b);
    out.n = finite.length;
    if (!finite.length) return Object.assign(out, { zmin: 0, zmax: 1, tickvals: [], ticktext: [] });
    out.min = finite[0]; out.max = finite[finite.length - 1];
    const forward = v => o.mode === 'log' ? Math.log10(1 + Math.max(0, v))
      : o.mode === 'sqrt' ? Math.sqrt(Math.max(0, v)) : v;
    const tickQs = [0, .25, .5, .75, 1];
    const tickOriginals = tickQs.map(q => quantile(finite, q));
    if (o.mode === 'rank') {
      const uniq = [];
      finite.forEach(v => { if (!uniq.length || v !== uniq[uniq.length - 1]) uniq.push(v); });
      const rankOf = v => {
        if (uniq.length <= 1) return .5;
        let lo = 0, hi = uniq.length - 1;
        while (lo < hi) { const mid = (lo + hi) >> 1; if (uniq[mid] < v) lo = mid + 1; else hi = mid; }
        return lo / (uniq.length - 1);
      };
      out.z = out.z.map(row => row.map(v => v === null ? null : rankOf(v)));
      out.zmin = 0; out.zmax = 1;
      out.tickvals = tickQs; out.ticktext = tickOriginals.map(fmt.tick);
      return out;
    }
    let zmin = forward(finite[0]), zmax = forward(finite[finite.length - 1]);
    if (o.mode === 'linear' && o.robust && finite.length >= 5) {
      zmin = quantile(finite, .02); zmax = quantile(finite, .98);
    }
    if (zmin === zmax) { zmin -= 1; zmax += 1; }
    out.z = out.z.map(row => row.map(v => v === null ? null : forward(v)));
    out.zmin = zmin; out.zmax = zmax;
    out.tickvals = tickOriginals.map(forward); out.ticktext = tickOriginals.map(fmt.tick);
    return out;
  };

  color.modeName = function (mode) {
    return { log: 'log10(1+z)', sqrt: 'sqrt(z)', linear: '线性', rank: '分位/秩' }[mode] || mode;
  };
  Q2.color = color;

  const plot = {};
  let clickHandler = null;
  /* Plotly 只在首次 newPlot/react 之后才把 .on/.emit 挂到 graph div 上，
     所以点击监听必须在 render 之后补挂，否则会静默丢失。 */
  plot.onClick = function (fn) { clickHandler = fn; };
  function bindClick(node) {
    if (!node || node.__q2clickBound || typeof node.on !== 'function') return;
    node.on('plotly_click', ev => {
      if (!clickHandler) return;
      if (!ev || !ev.points || !ev.points.length) return;
      const p = ev.points[0];
      if (p.x === undefined || p.y === undefined) return;
      clickHandler(Number(p.x), Number(p.y));
    });
    node.__q2clickBound = true;
  }
  plot.baseLayout = function (o) {
    /* 图例统一放在画布下方的 HTML 图例里（随辅助线开关实时变化），
       因此 Plotly 自带图例关闭，热图可以占满整个画布。 */
    return {
      margin: { l: 62, r: 16, t: 50, b: 58 },
      title: { text: o.title || '', font: { size: 13.5 } },
      xaxis: {
        title: '世界坐标 x (m)', range: [o.bounds[0], o.bounds[1]],
        zeroline: true, zerolinecolor: '#d8d8d8', zerolinewidth: 1.5,
        gridcolor: '#eef0f5', scaleanchor: 'y', scaleratio: 1, constrain: 'domain'
      },
      yaxis: {
        title: '世界坐标 y (m)', range: [o.bounds[2], o.bounds[3]],
        zeroline: true, zerolinecolor: '#d8d8d8', zerolinewidth: 1.5,
        gridcolor: '#eef0f5'
      },
      paper_bgcolor: 'white', plot_bgcolor: 'white', showlegend: false,
      hovermode: 'closest', dragmode: 'pan'
    };
  };
  plot.render = function (traces, layout) {
    const node = $('plot');
    if (!node) return Promise.resolve();
    return global.Plotly.react(node, traces, layout, { responsive: true, displaylogo: false })
      .then(res => { bindClick(node); return res; });
  };
  plot.resize = function () {
    const node = $('plot');
    if (node && global.Plotly && global.Plotly.Plots) {
      try { global.Plotly.Plots.resize(node); } catch (e) { /* 忽略 */ }
    }
  };
  Q2.plot = plot;

  /* ------------------------------------------------------------------ 9. 统一渲染 */
  const viz = {};

  viz.bestFromGrid = function (view, metric) {
    let best = null;
    const prefer = (x, y, v, zone) => {
      const better = !best ||
        (best.zone !== 'certain' && zone === 'certain') ||
        ((best.zone === 'certain') === (zone === 'certain') &&
          (metric === 'pdet' ? v > best.value : v < best.value));
      if (better) best = { x: x, y: y, value: v, zone: zone };
    };
    for (let iy = 0; iy < view.z.length; iy++) {
      for (let ix = 0; ix < view.z[iy].length; ix++) {
        const v = view.z[iy][ix];
        if (v === null || !Number.isFinite(v)) continue;
        const zone = view.zone[iy][ix];
        if (zone === 'blind' || zone === null) continue;
        prefer(view.xs[ix], view.ys[iy], v, zone);
      }
    }
    return best;
  };

  viz.draw = function (o) {
    const view = o.view, ws = o.ws;
    /* 工作区切换后，旧工作区还在飞的计算结果不许再画到图上 */
    if (!Q2.isActive(ws)) return Promise.resolve();
    const info = $('plotInfo');
    const prepared = o.prepared;
    const traces = [];

    /* 1) 底色：无效 / 被掩膜 / 场地外的格点 */
    if (chk('showGrey', true)) {
      traces.push({
        type: 'heatmap', x: view.xs, y: view.ys,
        z: view.z.map(row => row.map(v => (v === null || !Number.isFinite(v)) ? 1 : 0)),
        colorscale: [[0, '#f8f9fb'], [1, '#d9dee7']], zmin: 0, zmax: 1,
        showscale: false, hoverinfo: 'skip', name: '无效 / 掩膜格点', showlegend: false
      });
    }
    /* 2) 分区着色（一定测到 / 概率测得 / 必然无信号由图层注册表统一负责） */
    /* 3) 热图本体 */
    if (view.layers.best === undefined) view.layers.best = viz.bestFromGrid(view, view.metric);
    traces.push({
      type: 'heatmap', x: view.xs, y: view.ys, z: prepared.z, customdata: view.z,
      colorscale: 'Viridis', showlegend: false,
      colorbar: {
        title: view.colorbar, tickmode: 'array',
        tickvals: prepared.tickvals, ticktext: prepared.ticktext, len: .92
      },
      zmin: prepared.zmin, zmax: prepared.zmax,
      hovertemplate: '(%{x:.0f}, %{y:.0f}) m<br>' + view.colorbar + ' = %{customdata:.4g}<extra></extra>'
    });
    /* 4) 辅助线图层（两个工作区共用同一批绘制器） */
    Q2.overlayTraces(view, ws, Q2.probe.lastPoint).forEach(t => traces.push(t));

    const layout = plot.baseLayout({ title: view.title, bounds: view.bounds });
    return plot.render(traces, layout).then(() => {
      if (!Q2.isActive(ws)) return;
      renderLegend(view, ws);
      Q2.refreshLinesPanel(ws, view);
      if (info) clear(info);
      o.badges && info && info.appendChild(ctl.badges(o.badges));
      ui.pill(o.statusText || '就绪', o.statusKind || '');
      ui.status(o.status || '');
      /* 布局稳定后再让 Plotly 量一次容器，避免 SVG 与容器高度不一致 */
      global.setTimeout(() => plot.resize(), 40);
    });
  };

  /* 依据启用中的图层动态生成图例说明，两个工作区同一份文字结构 */
  function renderLegend(view, ws) {
    const node = $('legend');
    if (!node) return;
    clear(node);
    const items = [];
    Q2.linesAvailable(ws).forEach(line => {
      if (!Q2.lineEnabled(line.id)) return;
      if (line.need && !view.layers[line.need]) return;
      items.push({
        chip: 'border-top:2.5px ' + (line.dash === 'solid' ? 'solid' : line.dash) + ' ' + line.color,
        text: labelOf(line, ws)
      });
    });
    if (chk('showGrey', true)) items.push({ chip: 'background:#d9dee7;height:9px', text: '无效 / 掩膜 / 场地外格点' });
    items.push({ chip: 'background:linear-gradient(90deg,#440154,#3b528b,#21918c,#5ec962,#fde725);height:9px', text: '热值 = ' + view.colorbar + '（' + view.colorNote + '）' });
    items.forEach(it => {
      node.appendChild(el('span', { class: 'li' }, [
        el('span', { class: 'chip', style: it.chip }), el('span', { text: it.text })
      ]));
    });
  }

  Q2.viz = viz;

  /* ------------------------------------------------------------------ 10. 探针卡片 */
  const probe = { last: null, lastPoint: null };
  probe.reset = function (hint) {
    probe.last = null; probe.lastPoint = null;
    probe.render(null, hint);
  };
  probe.render = function (o, hint) {
    const box = $('probeCard');
    if (!box) return;
    clear(box);
    if (!o) {
      box.appendChild(el('div', { class: 'plot-foot' }, [
        el('div', { class: 'hint', text: hint || '点击热图任意位置即可检验该候选点。' })
      ]));
      return;
    }
    box.appendChild(el('div', { class: 'plot-head' }, [
      el('div', { class: 'title', text: o.title }),
      el('div', { class: 'spacer' }),
      o.actions ? el('div', { class: 'row' }, o.actions) : null
    ]));
    const inner = el('div', { class: 'plot-foot' });
    if (o.badges) inner.appendChild(ctl.badges(o.badges));
    if (o.table) {
      inner.appendChild(ctl.table({
        head: o.table.head,
        rows: o.table.rows.map(r => ({ cells: r.map(c => (typeof c === 'object' ? c : { text: c })) }))
      }));
    }
    if (o.note) inner.appendChild(ctl.note(o.note.text, o.note.kind));
    box.appendChild(inner);
  };
  Q2.probe = probe;

  /* ------------------------------------------------------------------ 11. 导入 / 导出 / 粘贴 */
  const io = {};
  io.download = function (name, text, mime) {
    try {
      const url = global.URL.createObjectURL(new global.Blob([text], { type: mime || 'text/plain' }));
      const a = document.createElement('a');
      a.href = url; a.download = name;
      a.click();
      global.setTimeout(() => global.URL.revokeObjectURL(url), 1000);
    } catch (e) { ui.toast('下载失败：' + e.message, 'err'); }
  };
  io.copy = function (text, label) {
    try {
      if (global.navigator && global.navigator.clipboard && global.navigator.clipboard.writeText) {
        global.navigator.clipboard.writeText(text);
      } else if (document.execCommand) {
        const ta = el('textarea', { value: text });
        document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
      }
      ui.toast((label || '已复制') + '（' + text.length + ' 字符）');
    } catch (e) { ui.toast('复制失败：' + e.message, 'err'); }
  };
  io.readClipboard = function () {
    if (global.navigator && global.navigator.clipboard && global.navigator.clipboard.readText) {
      return global.navigator.clipboard.readText();
    }
    return Promise.reject(new Error('浏览器不允许读取剪贴板，请手动粘贴到文本框'));
  };

  /* 两个工作区共用的"数据"小节 */
  io.section = function (o) {
    const box = el('div', { class: 'field' });
    const textarea = el('textarea', {
      id: 'ioText', placeholder: o.placeholder ||
        '在此粘贴 JSON 或 Channel × Path Markdown 表，然后点"导入文本"。'
    });
    const status = el('div', { class: 'hint' });

    box.appendChild(el('div', { class: 'row' }, [
      ctl.button({ label: '导出 JSON', kind: 'tiny', id: 'ioExportJson', onclick: () => { try { const d = o.getJSON(); io.download(d.name, d.text, 'application/json'); ui.toast('已导出 ' + d.name); } catch (e) { ui.toast(e.message, 'err'); } } }),
      ctl.button({ label: '导出 Markdown', kind: 'tiny', id: 'ioExportMd', onclick: () => { try { const d = o.getMarkdown(); io.download(d.name, d.text, 'text/markdown'); ui.toast('已导出 ' + d.name); } catch (e) { ui.toast(e.message, 'err'); } } }),
      ctl.button({ label: '复制 JSON', kind: 'tiny', id: 'ioCopyJson', onclick: () => { try { io.copy(o.getJSON().text, 'JSON 已复制'); } catch (e) { ui.toast(e.message, 'err'); } } }),
      ctl.button({ label: '复制 Markdown', kind: 'tiny', id: 'ioCopyMd', onclick: () => { try { io.copy(o.getMarkdown().text, 'Markdown 已复制'); } catch (e) { ui.toast(e.message, 'err'); } } })
    ]));

    const drop = el('div', { class: 'drop', id: 'ioDrop', text: '把 .json / .md / .txt 文件拖到这里，或点击选择文件' });
    const file = el('input', { type: 'file', id: 'ioFile', accept: '.json,.md,.txt,.csv', class: 'hidden' });
    on(drop, 'click', () => file.click());
    on(file, 'change', () => { if (file.files && file.files[0]) applyFile(file.files[0]); });
    ['dragenter', 'dragover'].forEach(ev => on(drop, ev, e => { if (e.preventDefault) e.preventDefault(); drop.classList.add('hot'); }));
    ['dragleave', 'drop'].forEach(ev => on(drop, ev, e => { if (e.preventDefault) e.preventDefault(); drop.classList.remove('hot'); }));
    on(drop, 'drop', e => {
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) applyFile(f);
    });
    box.appendChild(drop); box.appendChild(file);

    async function applyFile(f) {
      try {
        if (f.text) { const text = await f.text(); textarea.value = text; await apply(text, f.name); }
        else if (global.FileReader) {
          const fr = new global.FileReader();
          fr.onload = () => { textarea.value = String(fr.result); apply(String(fr.result), f.name); };
          fr.readAsText(f);
        }
      } catch (e) { status.textContent = '读取失败：' + e.message; }
    }

    async function apply(text, source) {
      if (!text || !text.trim()) { status.textContent = '内容为空。'; return; }
      try {
        const msg = await o.applyText(text, source);
        status.textContent = '导入成功：' + (msg || '已载入');
        ui.toast('导入成功：' + (msg || ''));
      } catch (e) { status.textContent = '导入失败：' + e.message; ui.toast('导入失败：' + e.message, 'err'); }
    }

    box.appendChild(textarea);
    box.appendChild(el('div', { class: 'row' }, [
      ctl.button({ label: '导入文本', kind: 'primary tiny', id: 'ioImport', onclick: () => apply(textarea.value, '粘贴文本') }),
      ctl.button({ label: '从剪贴板读取', kind: 'tiny', id: 'ioPaste', onclick: () => io.readClipboard().then(t => { textarea.value = t; return apply(t, '剪贴板'); }).catch(e => { status.textContent = e.message; }) }),
      o.example ? ctl.button({ label: '填入示例', kind: 'tiny', id: 'ioExample', onclick: () => { textarea.value = o.example(); status.textContent = '已填入示例，点"导入文本"载入。'; } }) : null,
      o.convert ? ctl.button({ label: o.convert.label, kind: 'tiny', id: 'ioConvert', onclick: () => o.convert.run(apply, status) }) : null
    ]));
    box.appendChild(status);
    if (o.note) box.appendChild(ctl.note(o.note));
    return box;
  };
  Q2.io = io;

  /* ------------------------------------------------------------------ 12. 工作区注册与启动 */
  const workspaces = {};
  let activeWs = null;

  function syncURL() {
    try {
      if (!global.history || !global.history.replaceState) return;
      const url = new URL(global.location.href);
      url.searchParams.set('ws', activeWs || '');
      url.searchParams.set('drawer', drawer.state);
      global.history.replaceState(null, '', url.toString());
    } catch (e) { /* file:// 或受限环境忽略 */ }
  }

  Q2.registerWorkspace = function (ws) { workspaces[ws.id] = ws; };
  Q2.activeWorkspace = function () { return activeWs ? workspaces[activeWs] : null; };
  /* 计算是异步的：切换工作区后，旧工作区迟到的结果必须自己作废 */
  Q2.isActive = function (id) { return activeWs === null || activeWs === id; };

  Q2.activateWorkspace = function (id, opts) {
    opts = opts || {};
    if (!workspaces[id]) return;
    const prev = activeWs ? workspaces[activeWs] : null;
    if (prev && prev.deactivate) prev.deactivate();
    activeWs = id;
    sections.length = 0;
    const ws = workspaces[id];
    ws.buildSections();
    renderSections();
    const nameNode = $('drawerWsName');
    if (nameNode) nameNode.textContent = ws.label;
    const hintNode = $('drawerWsHint');
    if (hintNode) hintNode.textContent = ws.hint || '';
    const subNode = $('plotSub');
    if (subNode) subNode.textContent = ws.label + ' · ' + (ws.hint || '') +
      '（编辑台可收起 / 半屏 / 全屏，辅助线与导入导出两个工作区共用）';
    Array.prototype.forEach.call(($('wsTabs') || { children: [] }).children, b => {
      b.setAttribute('aria-pressed', String(b.dataset.ws === id));
    });
    store.data.ws = id; store.save(); syncURL();
    /* 探针结果属于某个工作区，切换时清空，避免把上一个工作区的读数留在卡片里 */
    probe.reset(ws.probeHint);
    if (ws.activate) ws.activate();
    if (opts.draw !== false) ws.draw();
  };

  Q2.boot = function () {
    store.load();
    let params = null;
    try { params = new global.URLSearchParams(global.location.search); } catch (e) { params = null; }
    const root = document.documentElement;
    const routeWs = (root && root.dataset && root.dataset.defaultWs) || '';
    const wantWs = (params && params.get('ws')) || routeWs || store.data.ws || 'doublet';
    const wantDrawer = (params && params.get('drawer')) || store.data.drawer || 'half';

    const seg = $('drawerSeg');
    if (seg) {
      drawerLabels().forEach(item => seg.appendChild(el('button', {
        text: item[1], dataset: { state: item[0] }, title: item[2],
        onclick: () => drawer.set(item[0])
      })));
    }
    const tabs = $('wsTabs');
    if (tabs) {
      Object.keys(workspaces).forEach(id => tabs.appendChild(el('button', {
        text: workspaces[id].label, dataset: { ws: id },
        onclick: () => Q2.activateWorkspace(id)
      })));
    }

    Q2.plot.onClick((x, y) => {
      const ws = Q2.activeWorkspace();
      if (ws && ws.onPlotClick) ws.onPlotClick(x, y);
    });
    on(global, 'keydown', e => {
      const tag = (e.target && e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      if (e.key === 'Escape') drawer.set(drawer.state === 'full' ? 'half' : 'collapsed');
      else if (e.key === 'e' || e.key === 'E') drawer.cycle();
      else if (e.key === 'f' || e.key === 'F') drawer.set(drawer.state === 'full' ? 'half' : 'full');
    });
    on(global, 'resize', () => resizeSoon());

    drawer.set(DRAWER_STATES.indexOf(wantDrawer) >= 0 ? wantDrawer : 'half', { silent: true });
    Q2.activateWorkspace(workspaces[wantWs] ? wantWs : Object.keys(workspaces)[0]);
  };

  function drawerLabels() {
    return [
      ['collapsed', '收起', '收起编辑台（E / Esc）'],
      ['half', '半屏', '半屏编辑台（E 循环）'],
      ['full', '全屏', '全屏编辑页（F）']
    ];
  }
  Q2.drawerLabels = drawerLabels;
  Q2.showSection = showSection;
})(window);
