#!/usr/bin/env node
/*
 * Headless smoke test for the Q2 dashboard page.
 *
 *   node script/q2_dashboard_headless_test.js [baseUrl]
 *
 * It extracts the <script> block of the dashboard HTML, runs it inside a vm
 * with minimal DOM / Plotly / fetch stubs, drives the real HTTP API, and
 * validates every Plotly trace that the page tries to draw plus the probe
 * panel output.  This catches runtime JavaScript errors (typos in payload
 * fields, bad trace definitions) without a browser.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..");
const BASE = process.argv[2] || "http://127.0.0.1:8055";

function fail(msg) {
  console.error("FAIL: " + msg);
  process.exitCode = 1;
}
function ok(msg) {
  console.log("ok   - " + msg);
}

const py = fs.readFileSync(path.join(ROOT, "script", "q2_dashboard.py"), "utf8");
const m = py.match(/HTML_PAGE = r"""([\s\S]*?)"""/);
if (!m) {
  fail("cannot extract HTML_PAGE");
  process.exit(1);
}
const html = m[1];
const js = html.match(/<script>([\s\S]*?)<\/script>/)[1];

// ---------------------------------------------------------------- DOM stubs
const elements = new Map();
function makeElement(id) {
  return {
    id,
    value: id === "s1x" || id === "s1y" || id === "theta1" ? "0" : "0",
    checked: true,
    textContent: "",
    innerHTML: "",
    disabled: false,
    min: "0",
    max: "360",
    handlers: {},
    addEventListener(ev, fn) {
      (this.handlers[ev] = this.handlers[ev] || []).push(fn);
    },
    on(ev, fn) {
      (this.handlers[ev] = this.handlers[ev] || []).push(fn);
    },
  };
}
// defaults matching the HTML markup
const defaults = {
  s1x: "0", s1y: "0", theta1: "0", rhoMin: "1000", rhoMax: "1500",
  rhoFixed: "1250", rhoModel: "uniform", metric: "Rmin", stat: "mean",
  colorMode: "log", res: "15", uMin: "-1800", uMax: "1800", vMin: "-1800",
  vMax: "1800", pdetLevel: "0.999", pdetMin: "0.99", probeX: "850", probeY: "520",
  rhoMinR: "1000", rhoMaxR: "1500",
};
// initial checked state copied from the markup so the harness matches the browser
const unchecked = new Set();
const booleanIds = new Set();
for (const tag of html.match(/<input[^>]*type="checkbox"[^>]*>/g) || []) {
  const id = (tag.match(/id="([^"]+)"/) || [])[1];
  if (!id) continue;
  booleanIds.add(id);
  if (!/\schecked(\s|>)/.test(tag)) unchecked.add(id);
}

const fetchLog = [];
const traceLog = [];
let clickFired = false;

const sandbox = {
  console,
  setTimeout,
  clearTimeout,
  Math,
  Number,
  JSON,
  Error,
  fetch: async (url, opts) => {
    fetchLog.push(url);
    const r = await fetch(BASE + url, opts);
    return { ok: r.ok, status: r.status, statusText: r.statusText, json: () => r.json() };
  },
  document: {
    getElementById(id) {
      if (!elements.has(id)) {
        const el = makeElement(id);
        if (defaults[id] !== undefined) el.value = defaults[id];
        el.checked = booleanIds.has(id) && !unchecked.has(id);
        if (id === "rhoModel") el.value = "uniform";
        if (id === "metric") el.value = "Rmin";
        if (id === "stat") el.value = "mean";
        if (id === "colorMode") el.value = "log";
        elements.set(id, el);
      }
      return elements.get(id);
    },
  },
  window: {
    handlers: {},
    addEventListener(ev, fn) {
      (this.handlers[ev] = this.handlers[ev] || []).push(fn);
    },
  },
  Plotly: {
    react(gd, traces, layout, config) {
      if (!Array.isArray(traces) || traces.length === 0) {
        fail("Plotly.react called with no traces");
        return;
      }
      for (const t of traces) {
        if (!t.type) fail("trace without type: " + JSON.stringify(Object.keys(t)));
        if (t.type === "heatmap" || t.type === "contour") {
          if (!Array.isArray(t.x) || !Array.isArray(t.y)) fail(t.type + " trace without x/y arrays");
          const ny = t.y.length, nx = t.x.length;
          if (!Array.isArray(t.z) || t.z.length !== ny) {
            fail(t.type + " trace z rows " + (t.z || []).length + " != y length " + ny);
          } else if (t.z.some(row => row.length !== nx)) {
            fail(t.type + " trace z row width != x length");
          }
        }
        if (t.type === "scatter" && (!Array.isArray(t.x) || !Array.isArray(t.y))) {
          fail("scatter trace without x/y arrays");
        }
      }
      traceLog.push({ n: traces.length, names: traces.map(t => t.name || t.type) });
      const gdEl = elements.get(String(gd)) || elements.get(gd);
      if (!clickFired && gdEl && gdEl.handlers["plotly_click"]) {
        clickFired = true;
        const ev = { points: [{ x: 700, y: 450 }] };
        gdEl.handlers["plotly_click"].forEach(fn => fn(ev));
      }
    },
  },
};
sandbox.globalThis = sandbox;

// ------------------------------------------------------------------- run it
(async () => {
  try {
    vm.createContext(sandbox);
    vm.runInContext(js, sandbox, { filename: "dashboard_inline.js" });
  } catch (err) {
    fail("script threw while evaluating: " + err.stack);
    process.exit(1);
  }
  const loadHandlers = sandbox.window.handlers["load"] || [];
  if (!loadHandlers.length) fail("no window load handler registered");
  for (const fn of loadHandlers) {
    try {
      await fn();
    } catch (err) {
      fail("load handler threw: " + err.stack);
    }
  }
  // draw() is debounced through setTimeout; give it time to finish
  async function settle(maxMs = 30000) {
    const t0 = Date.now();
    let st = "";
    while (Date.now() - t0 < maxMs) {
      st = sandbox.document.getElementById("status").textContent;
      if (!/计算中/.test(st)) return st;
      await new Promise(r => setTimeout(r, 250));
    }
    return st;
  }

  const status = await settle();
  const summary = sandbox.document.getElementById("summary").innerHTML;
  const probeOut = sandbox.document.getElementById("probeOut").innerHTML;

  if (/错误|Error/.test(status)) fail("status reports an error: " + status);
  else ok("heatmap draw finished: " + status);

  if (!/一定测到/.test(summary)) fail("summary missing zone counts: " + summary);
  else ok("zone summary rendered");

  if (!traceLog.length) fail("Plotly.react never called");
  else {
    const last = traceLog[traceLog.length - 1];
    ok("rendered " + last.n + " traces: " + last.names.join(", "));
    for (const need of ["一定测到区边界（ρ_min）", "一定测不到区边界（ρ_max=1500）",
                        "一定测不到（无信息）", "干扰源可能集 A1", "场地圆 R=1800 m"]) {
      if (!last.names.includes(need)) fail("missing trace: " + need);
    }
  }

  if (fetchLog.filter(u => u === "/api/heatmap").length < 1) fail("no /api/heatmap call");
  if (fetchLog.filter(u => u === "/api/probe").length < 1) fail("no /api/probe call (click handler)");
  else ok("probe endpoint exercised by the plot click handler");

  if (/错误/.test(probeOut)) fail("probe panel reports an error: " + probeOut);
  else if (!/扫描/.test(probeOut) && !/ρ\(m\)/.test(probeOut)) fail("probe panel empty");
  else ok("probe panel rendered the rho sweep table");

  // a second pass with the other rho models / metrics
  for (const [model, metric] of [["fixed", "Rmin"], ["interval", "pdet"], ["uniform", "N20"]]) {
    sandbox.document.getElementById("rhoModel").value = model;
    sandbox.document.getElementById("metric").value = metric;
    traceLog.length = 0;
    try {
      await sandbox.draw();
    } catch (err) {
      fail(`draw() threw for model=${model} metric=${metric}: ${err.message}`);
      continue;
    }
    const st = await settle();
    if (/错误/.test(st) || /计算中/.test(st)) fail(`model=${model} metric=${metric}: ${st}`);
    else ok(`model=${model} metric=${metric}: ${st}`);
  }

  console.log(process.exitCode ? "RESULT: failures above" : "RESULT: headless page test passed");
})();
