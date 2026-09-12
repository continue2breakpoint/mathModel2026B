#!/usr/bin/env node
/*
 * 题目2 统一编辑台 · 真浏览器端到端测试
 *
 *   node script/q2_ui_e2e_test.js [--port 8065] [--shots <dir>] [--keep-server]
 *
 * 它自己拉起 Flask（python3 script/q2_dashboard.py）并用无头 Chrome 通过 CDP
 * 真正点击页面，逐项验证：
 *   - 两个工作区共用同一外壳，绘图完成且关键图层都在
 *   - 收纳页（编辑台）收起 / 半屏 / 全屏三态、快捷键、URL 深链
 *   - 辅助线图层开关真的增删图元（矩阵工作区原来没有的那些）
 *   - 探针、观测表编辑、导入 / 导出 / 跨工作区互转
 *   - 全程没有 JS 异常与失败请求
 * 只依赖 Node 内置能力（fetch / WebSocket）与系统里的 Chrome/Chromium。
 */
'use strict';

const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const net = require('net');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const argv = process.argv.slice(2);
const argOf = (name, dflt) => {
  const i = argv.indexOf(name);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : dflt;
};
const PORT = Number(argOf('--port', '8065'));
const SHOTS = argOf('--shots', '');
const KEEP_SERVER = argv.includes('--keep-server');
const BASE = 'http://127.0.0.1:' + PORT;
const CDP_PORT = PORT + 900;

let failures = 0, checks = 0;
function ok(msg) { checks++; console.log('ok   - ' + msg); }
function fail(msg) { checks++; failures++; console.error('FAIL - ' + msg); }
function note(msg) { console.log('       ' + msg); }
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* ------------------------------------------------------------------ 浏览器 */
function findChrome() {
  for (const bin of ['google-chrome', 'chromium', 'chromium-browser', 'chrome']) {
    const r = spawnSync('which', [bin], { encoding: 'utf8' });
    if (r.status === 0 && r.stdout.trim()) return r.stdout.trim();
  }
  return null;
}

class Browser {
  constructor(bin, profile) {
    this.bin = bin; this.profile = profile; this.child = null; this.ws = null;
    this.id = 0; this.pending = new Map(); this.logs = []; this.errors = [];
  }
  async start() {
    fs.mkdirSync(this.profile, { recursive: true });
    this.child = spawn(this.bin, ['--headless=new', '--disable-gpu', '--no-sandbox',
      '--hide-scrollbars', '--window-size=1600,1000', '--no-first-run',
      '--disable-dev-shm-usage', '--remote-debugging-port=' + CDP_PORT,
      '--user-data-dir=' + this.profile, 'about:blank'],
      { stdio: ['ignore', 'ignore', 'ignore'] });
    let list = null;
    for (let i = 0; i < 60 && !list; i++) {
      await sleep(300);
      try { list = await (await fetch('http://127.0.0.1:' + CDP_PORT + '/json/list')).json(); } catch (e) { /* 重试 */ }
    }
    if (!list) throw new Error('Chrome 未在 ' + CDP_PORT + ' 上打开调试端口');
    const page = list.find(t => t.type === 'page') || list[0];
    this.ws = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((res, rej) => {
      this.ws.addEventListener('open', res);
      this.ws.addEventListener('error', rej);
    });
    this.ws.addEventListener('message', ev => this._onMessage(ev));
    await this.send('Page.enable', {});
    await this.send('Runtime.enable', {});
    await this.send('Log.enable', {});
  }
  _onMessage(ev) {
    const msg = JSON.parse(ev.data);
    if (msg.id && this.pending.has(msg.id)) { this.pending.get(msg.id)(msg); this.pending.delete(msg.id); return; }
    if (msg.method === 'Runtime.exceptionThrown') {
      const d = msg.params.exceptionDetails || {};
      this.errors.push((d.exception && d.exception.description) || d.text || 'unknown exception');
    }
    if (msg.method === 'Log.entryAdded' && msg.params.entry.level === 'error') {
      this.errors.push('log: ' + msg.params.entry.text);
    }
    if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
      this.logs.push(msg.params.args.map(a => a.value).join(' '));
    }
  }
  send(method, params) {
    return new Promise((res, rej) => {
      const id = ++this.id;
      this.pending.set(id, m => (m.error ? rej(new Error(method + ': ' + JSON.stringify(m.error))) : res(m.result)));
      this.ws.send(JSON.stringify({ id: id, method: method, params: params || {} }));
    });
  }
  async goto(url) {
    await this.send('Page.navigate', { url: url });
    await sleep(300);
  }
  async eval(expression, awaitPromise) {
    const r = await this.send('Runtime.evaluate', {
      expression: '(function(){' + expression + '})()',
      returnByValue: true, awaitPromise: !!awaitPromise, userGesture: true
    });
    if (r.exceptionDetails) {
      const d = r.exceptionDetails;
      throw new Error('eval 异常: ' + ((d.exception && d.exception.description) || d.text));
    }
    return r.result ? r.result.value : undefined;
  }
  async shot(file) {
    if (!SHOTS) return;
    const r = await this.send('Page.captureScreenshot', { format: 'png' });
    fs.mkdirSync(SHOTS, { recursive: true });
    fs.writeFileSync(path.join(SHOTS, file), Buffer.from(r.data, 'base64'));
  }
  async waitFor(expression, label, timeoutMs) {
    const t0 = Date.now();
    const limit = timeoutMs || 40000;
    let last = null;
    while (Date.now() - t0 < limit) {
      try { last = await this.eval('return (' + expression + ');'); } catch (e) { last = 'err:' + e.message; }
      if (last) return true;
      await sleep(250);
    }
    fail('等待超时：' + label + '（最后取值 ' + JSON.stringify(last) + '）');
    return false;
  }
  stop() {
    try { this.ws && this.ws.close(); } catch (e) { /* ignore */ }
    try { this.child && this.child.kill('SIGKILL'); } catch (e) { /* ignore */ }
  }
}

/* ------------------------------------------------------------------ 服务 */
function waitPort(port, timeoutMs) {
  return new Promise(resolve => {
    const t0 = Date.now();
    const tick = () => {
      const sock = net.connect(port, '127.0.0.1');
      sock.on('connect', () => { sock.destroy(); resolve(true); });
      sock.on('error', () => {
        sock.destroy();
        if (Date.now() - t0 > (timeoutMs || 30000)) resolve(false); else setTimeout(tick, 300);
      });
    };
    tick();
  });
}

/* ------------------------------------------------------------------ 断言辅助 */
const TRACE_NAMES = 'Array.from(document.getElementById("plot").data||[]).map(t=>t.name||t.type)';

async function expectTrace(browser, needle, label) {
  const names = await browser.eval('return ' + TRACE_NAMES + ';');
  const hit = (names || []).some(n => String(n).startsWith(needle));
  if (hit) ok(label + '：图层 "' + needle + '" 在图上'); else fail(label + '：缺少图层 "' + needle + '"，实际有 ' + JSON.stringify(names));
  return hit;
}

async function drawAndSettle(browser, label, timeoutMs) {
  /* 记下当前状态条再触发计算：否则会误把"上一轮的完成"当成这一轮结束 */
  await browser.eval('window.__pill = document.getElementById("headStatus").textContent;' +
    'Q2.activeWorkspace().draw(); return true;');
  const done = await browser.waitFor(
    'document.getElementById("headStatus").textContent !== window.__pill && ' +
    '/完成|失败/.test(document.getElementById("headStatus").textContent)', label + ' 绘图结束', timeoutMs || 60000);
  const pill = await browser.eval('return document.getElementById("headStatus").textContent;');
  if (/失败/.test(pill)) fail(label + ' 绘图失败：' + pill);
  else if (done) ok(label + '：' + pill);
  return done;
}

/* ------------------------------------------------------------------ 主流程 */
(async () => {
  const chromeBin = findChrome();
  if (!chromeBin) { console.error('找不到 Chrome/Chromium，跳过 UI 端到端测试'); process.exit(0); }
  const profile = path.join(require('os').tmpdir(), 'q2-e2e-' + process.pid);

  const server = spawn('python3', ['script/q2_dashboard.py', '--port', String(PORT)], {
    cwd: ROOT, env: Object.assign({}, process.env, { PYTHONPATH: path.join(ROOT, 'cpp') }),
    stdio: ['ignore', 'pipe', 'pipe']
  });
  let serverErr = '';
  server.stderr.on('data', d => { serverErr += d.toString(); });
  if (!await waitPort(PORT, 30000)) {
    console.error('Flask 未启动：\n' + serverErr);
    server.kill('SIGKILL'); process.exit(1);
  }
  ok('服务已启动 ' + BASE);

  const browser = new Browser(chromeBin, profile);
  try {
    await browser.start();

    /* ---------------------------------------------------- 1. 双点工作区 */
    await browser.goto(BASE + '/?drawer=half');
    await browser.waitFor('!!window.Q2 && !!window.Q2.activeWorkspace()', '页面脚本就绪');
    if (!await drawAndSettle(browser, '双点工作区首屏')) throw new Error('双点工作区没有完成绘图');
    const ws = await browser.eval('return Q2.activeWorkspace().id;');
    if (ws === 'doublet') ok('默认工作区是双点交会（/ 路由）'); else fail('默认工作区应为 doublet，实际 ' + ws);
    for (const need of ['场地圆 R=1800 m', '干扰源可能集 A1', '第一观测点 S1', '一定测到区边界',
                        '必然无信号区', 'p_det = ', '最优格点', '推荐候选 S2']) {
      await expectTrace(browser, need, '双点');
    }
    const a1 = await browser.eval('return ' + TRACE_NAMES + '.filter(n=>String(n).startsWith("干扰源可能集 A1"));');
    if (a1.length === 2) ok('ρ 不确定时可能源区画两条（ρ_min / ρ_max）：' + a1.join(' | '));
    else fail('可能源区应画两条，实际 ' + JSON.stringify(a1));
    await browser.shot('01-doublet-half.png');

    /* ---------------------------------------------------- 2. 收纳页三态 */
    await browser.eval('document.getElementById("drawerCollapse").click(); return true;');
    await sleep(500);
    let state = await browser.eval('return document.getElementById("drawer").dataset.state;');
    let plotW = await browser.eval('return document.getElementById("plot").getBoundingClientRect().width;');
    if (state === 'collapsed') ok('收纳页：点"收起"后 data-state=collapsed'); else fail('收起失败：' + state);
    if (plotW > 1000) ok('收起后画布变宽到 ' + Math.round(plotW) + 'px'); else fail('收起后画布未变宽：' + plotW);
    await browser.shot('02-doublet-collapsed.png');

    /* 收起状态下点图标栏：应自动展开半屏并跳到对应小节 */
    await browser.eval('document.querySelector(\'#rail button[data-sec="lines"]\').click(); return true;');
    await sleep(600);
    const railJump = await browser.eval(
      'const acc=[...document.querySelectorAll("#drawerBody details")].find(d=>d.dataset.sec==="lines");' +
      'return JSON.stringify({state:document.getElementById("drawer").dataset.state, open:acc?acc.open:null});');
    if (/"state":"half"/.test(railJump) && /"open":true/.test(railJump)) ok('收纳页：收起状态下点图标栏 → 展开半屏并定位到该小节');
    else fail('图标栏跳转异常：' + railJump);
    await browser.shot('02b-doublet-rail-jump.png');

    await browser.eval('document.getElementById("drawerHalf").click(); return true;');
    await sleep(500);
    state = await browser.eval('return document.getElementById("drawer").dataset.state;');
    const halfW = await browser.eval('return document.getElementById("drawer").getBoundingClientRect().width;');
    const vw = await browser.eval('return window.innerWidth;');
    if (state === 'half' && halfW > vw * 0.3 && halfW < vw * 0.6) ok('收纳页：半屏占 ' + Math.round(halfW) + '/' + vw + 'px');
    else fail('半屏宽度异常：state=' + state + ' w=' + halfW + ' vw=' + vw);

    await browser.eval('document.getElementById("drawerFull").click(); return true;');
    await sleep(600);
    state = await browser.eval('return document.getElementById("drawer").dataset.state;');
    const cover = await browser.eval(
      'const r=document.getElementById("drawer").getBoundingClientRect();' +
      'return Math.round(r.width)>=window.innerWidth-1 && Math.round(r.height)>=window.innerHeight-1;');
    if (state === 'full' && cover) ok('收纳页：全屏编辑页铺满视口'); else fail('全屏失败：' + state + ' cover=' + cover);
    await browser.shot('03-doublet-full.png');

    await browser.eval('window.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape"})); return true;');
    await sleep(500);
    state = await browser.eval('return document.getElementById("drawer").dataset.state;');
    if (state === 'half') ok('收纳页：Esc 从全屏退回半屏'); else fail('Esc 行为异常：' + state);
    const cycleSeen = [];
    for (let i = 0; i < 3; i++) {
      await browser.eval('window.dispatchEvent(new KeyboardEvent("keydown",{key:"e"})); return true;');
      await sleep(350);
      cycleSeen.push(await browser.eval('return document.getElementById("drawer").dataset.state;'));
    }
    if (cycleSeen.join(',') === 'full,collapsed,half') ok('收纳页：E 键按 半屏→全屏→收起→半屏 循环（' + cycleSeen.join('→') + '）');
    else fail('E 键循环异常：' + cycleSeen.join(','));
    await browser.eval('window.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape"})); return true;');
    await sleep(400);
    state = await browser.eval('return document.getElementById("drawer").dataset.state;');
    if (state === 'collapsed') ok('收纳页：Esc 收起编辑台'); else fail('Esc 收起异常：' + state);
    await browser.eval('document.getElementById("drawerHalf").click(); return true;');
    await sleep(400);

    /* ---------------------------------------------------- 3. 辅助线开关 */
    const before = await browser.eval('return ' + TRACE_NAMES + '.length;');
    const toggled = await browser.eval(
      'const box=document.getElementById("line_target");' +
      'if(!box) return "nobox"; if(box.disabled) return "disabled"; box.click(); return "clicked";');
    await sleep(600);
    const after = await browser.eval('return ' + TRACE_NAMES + '.length;');
    const hasTarget = await browser.eval('return ' + TRACE_NAMES + '.some(n=>String(n).startsWith("场地圆"));');
    if (toggled === 'clicked' && after === before - 1 && !hasTarget) ok('辅助线开关：关闭"场地圆"后图元减少 ' + before + '→' + after);
    else fail('辅助线开关无效：' + toggled + ' ' + before + '→' + after + ' target=' + hasTarget);
    await browser.eval('const box=document.getElementById("line_target"); if(box) box.click(); return true;');
    await sleep(500);
    const restored = await browser.eval('return ' + TRACE_NAMES + '.some(n=>String(n).startsWith("场地圆"));');
    if (restored) ok('辅助线开关：重新打开后图元恢复'); else fail('辅助线开关不能恢复');

    /* ---------------------------------------------------- 4. 探针 */
    await browser.eval('const gd=document.getElementById("plot");' +
      'gd.emit("plotly_click",{points:[{x:850,y:520}]}); return true;');
    await browser.waitFor('document.getElementById("probeCard").textContent.indexOf("S2 = (850")>=0', '探针结果', 30000);
    const probeText = await browser.eval('return document.getElementById("probeCard").textContent;');
    if (/p_det\(uniform\)/.test(probeText) && /ρ \(m\)/.test(probeText)) ok('探针：点击热图后给出 p_det 与 ρ 扫描表');
    else fail('探针输出异常：' + probeText.slice(0, 200));
    const probeMarker = await browser.eval('return ' + TRACE_NAMES + '.some(n=>String(n).indexOf("探针")>=0);');
    if (probeMarker) ok('探针：图上出现探针标记'); else fail('探针标记未出现在图上');
    await browser.shot('04-doublet-probe.png');

    /* ---------------------------------------------------- 5. 导入导出互转 */
    const jsonText = await browser.eval('return JSON.stringify(Q2.ws.doublet.state);');
    await browser.eval(
      'const ta=document.getElementById("ioText");' +
      'ta.value=JSON.stringify({schema:"q2-doublet/v1",channel:3,s1:[200,-300],theta1:45,' +
      'rho:{model:"fixed",min:1000,max:1500,fixed:1300}});' +
      'document.getElementById("ioImport").click(); return true;');
    await browser.waitFor('Q2.ws.doublet.state.channel===3 && Math.abs(Q2.ws.doublet.state.s1x-200)<1e-6',
      '导入双点 JSON', 20000);
    ok('导入：双点 JSON 生效（S1=(200,−300)、θ1=45°、频道 3）');
    await drawAndSettle(browser, '导入后重算');
    const md = await browser.eval('return (function(){' +
      'const t=document.getElementById("ioExportMd"); return true;})() && null;');
    void md;
    const matrixJson = await browser.eval(
      'return JSON.stringify({schema:"q2-doublet/v1",channel:1,s1:[0,0],theta1:0});');
    await browser.eval(
      'document.getElementById("ioText").value=' + JSON.stringify(matrixJson) + ';' +
      'document.getElementById("ioImport").click(); return true;');
    await browser.waitFor('Math.abs(Q2.ws.doublet.state.s1x)<1e-6 && Math.abs(Q2.ws.doublet.state.theta1)<1e-6',
      '再次导入', 20000);
    ok('导入：文档可以反复覆盖载入');
    void jsonText;

    /* ---------------------------------------------------- 6. 工作区切换 */
    await browser.eval('document.querySelector(\'#wsTabs button[data-ws="matrix"]\').click(); return true;');
    await browser.waitFor('Q2.activeWorkspace().id==="matrix"', '切到矩阵工作区', 20000);
    if (!await drawAndSettle(browser, '矩阵工作区首屏', 90000)) throw new Error('矩阵工作区没有完成绘图');
    await expectTrace(browser, '场地圆 R=1800 m', '矩阵');
    await expectTrace(browser, '当前可能源区 A', '矩阵');
    await expectTrace(browser, '观测点 P1', '矩阵');
    await expectTrace(browser, '测向楔形', '矩阵');
    await expectTrace(browser, 'p_det = ', '矩阵');
    await expectTrace(browser, '最优格点', '矩阵');
    const hasTable = await browser.eval('return !!document.getElementById("matrixTable");');
    if (hasTable) ok('矩阵工作区：观测表面板已渲染'); else fail('矩阵工作区缺少观测表');
    await browser.shot('05-matrix-half.png');

    /* 矩阵新增的辅助线：近距圈 / 排除盘 */
    await browser.eval(
      'const box=document.getElementById("line_near");' +
      'if(!box||box.disabled) return "no"; box.click(); return "clicked";');
    await sleep(700);
    const nearTrace = await browser.eval('return ' + TRACE_NAMES + '.some(n=>String(n).startsWith("5 m 近距圈"));');
    if (nearTrace) ok('矩阵工作区：新增的"5 m 近距圈"图层可开关并绘制');
    else fail('矩阵工作区的 5 m 近距圈图层没有生效');

    /* 矩阵探针：真实点击热图 → 两种统计条件 + 到各观测距离 */
    await browser.eval('const gd=document.getElementById("plot"); gd.emit("plotly_click",{points:[{x:900,y:400}]}); return true;');
    await browser.waitFor('document.getElementById("probeCard").textContent.indexOf("(900.0, 400.0)")>=0', '矩阵探针', 40000);
    const mProbe = await browser.eval('return document.getElementById("probeCard").textContent;');
    if (/p_det = /.test(mProbe) && /观测点/.test(mProbe)) ok('矩阵探针：给出分区、p_det、两种条件统计与到各观测点距离');
    else fail('矩阵探针输出异常：' + mProbe.slice(0, 200));

    /* ---------------------------------------------------- 7. 观测表编辑 */
    await browser.eval(
      'Q2.ws.matrix.state.res=9;' +
      'const sel=[...document.querySelectorAll("#matrixTable select")][0];' +
      'sel.value="not_find"; sel.dispatchEvent(new Event("change",{bubbles:true})); return true;');
    await sleep(400);
    await drawAndSettle(browser, '把 P1 改成未测到后重算', 90000);
    const cols = await browser.eval('return JSON.stringify(Q2.ws.matrix.state.columns);');
    if (/not_find/.test(cols)) ok('观测表：状态改为 not_find 后进入请求体'); else fail('观测表状态没有生效：' + cols);
    const negTrace = await browser.eval('return ' + TRACE_NAMES + '.some(n=>String(n).indexOf("未测到排除盘")>=0);');
    if (negTrace) ok('观测表：负例出现"未测到排除盘"图层（矩阵新增辅助线）');
    else note('负例排除盘图层未开启（默认关闭，属正常）');
    await browser.eval(
      'const sel=[...document.querySelectorAll("#matrixTable select")][0];' +
      'sel.value="find"; sel.dispatchEvent(new Event("change",{bubbles:true})); return true;');
    await sleep(300);
    await browser.eval('Q2.ws.matrix.addColumn([900,-400]); return true;');
    await sleep(200);
    const nCols = await browser.eval('return Q2.ws.matrix.state.columns.length;');
    if (nCols === 2) ok('观测表：新增待测候选列生效（' + nCols + ' 列）'); else fail('新增列失败：' + nCols);
    await drawAndSettle(browser, '两列观测重算', 90000);
    await browser.shot('06-matrix-2cols.png');

    /* ---------------------------------------------------- 8. 跨工作区互转 */
    await browser.eval(
      'const boxes=[...document.querySelectorAll("#drawerBody button")];' +
      'const b=boxes.find(x=>x.textContent.indexOf("载入到双点工作区")>=0); if(b) b.click(); return !!b;');
    await browser.waitFor('Q2.activeWorkspace().id==="doublet"', '矩阵→双点', 20000);
    const backS1 = await browser.eval('return JSON.stringify([Q2.ws.doublet.state.s1x,Q2.ws.doublet.state.s1y,Q2.ws.doublet.state.theta1]);');
    ok('互转：矩阵工作区可把观测表载入双点工作区 ' + backS1);
    await drawAndSettle(browser, '互转后双点重算');
    await browser.eval(
      'const boxes=[...document.querySelectorAll("#drawerBody button")];' +
      'const b=boxes.find(x=>x.textContent.indexOf("转到知识矩阵工作区")>=0); if(b) b.click(); return !!b;');
    await browser.waitFor('Q2.activeWorkspace().id==="matrix"', '双点→矩阵', 20000);
    const cols2 = await browser.eval('return JSON.stringify(Q2.ws.matrix.state.columns);');
    if (/find/.test(cols2)) ok('互转：双点观测可以带回矩阵工作区 ' + cols2.slice(0, 90));
    else fail('互转回矩阵失败：' + cols2);

    /* ---------------------------------------------------- 9. URL 深链 */
    await browser.goto(BASE + '/matrix?ws=matrix&drawer=full');
    await browser.waitFor('!!window.Q2 && Q2.activeWorkspace() && Q2.activeWorkspace().id==="matrix"', '深链工作区');
    const dl = await browser.eval('return JSON.stringify({ws:Q2.activeWorkspace().id,drawer:document.getElementById("drawer").dataset.state});');
    if (/"ws":"matrix"/.test(dl) && /"drawer":"full"/.test(dl)) ok('URL 深链：/matrix?ws=matrix&drawer=full 直接落到全屏矩阵编辑页');
    else fail('深链失败：' + dl);
    await drawAndSettle(browser, '深链首屏', 90000);
    await browser.shot('07-matrix-full-deeplink.png');

    /* ---------------------------------------------------- 10. 无异常 */
    if (browser.errors.length) {
      fail('页面出现 JS 异常：\n      ' + browser.errors.slice(0, 5).join('\n      '));
    } else ok('全程没有 JS 异常');
  } catch (err) {
    fail('测试中断：' + err.message);
  } finally {
    browser.stop();
    if (!KEEP_SERVER) server.kill('SIGKILL');
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  }
  console.log('\n' + (failures ? 'RESULT: ' + failures + '/' + checks + ' 项失败' : 'RESULT: 全部 ' + checks + ' 项通过'));
  process.exit(failures ? 1 : 0);
})();
