#!/usr/bin/env python3
"""复算模拟器 GUI 请求流水，验证官方运动学并给出时间分解。

用法：
    # 从模拟器 GUI 日志页把表格粘成 TSV（列：接收时间/指令/位置/频道/响应摘要/虚拟时间）
    python3 tools/requestlog_kinematics.py docs/data/online-p3-matrix-20260913-requestlog.tsv
    # 顺便与本地一次 run 的记录对照
    python3 tools/requestlog_kinematics.py <log.tsv> --run logs/runs/<run_id>

模型（附件2 §4.2–4.3）：
    移动耗时 = 两次 position 的直线距离 / 5.0 m/s
    /measure 动作 = 5 s；若频道 ≠ 测向机当前频道，另加 1 s 切换耗时（并更新当前频道）
    /clear 成功 = 5 s；范围内无目标 = 3 s；/clear 不切频道、不影响当前频道
    /enter、/exit 不推进虚拟时间；初始频道 = 1
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

POS_RE = re.compile(r"\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)")


def parse(path: Path) -> list[dict]:
    rows: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\n")
        if not line.strip() or line.startswith("接收时间"):
            continue
        parts = re.split(r"\t+|\s{2,}", line)
        if len(parts) < 6:
            continue
        t, act, pos, ch, resp, vt = parts[:6]
        m = POS_RE.search(pos)
        rows.append(
            {
                "t": t.strip(),
                "act": act.strip(),
                "xy": (float(m.group(1)), float(m.group(2))) if m else None,
                "ch": int(ch) if ch.strip().isdigit() else None,
                "resp": resp.strip(),
                "vt": float(re.sub(r"[^\d.]", "", vt) or 0.0),
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tsv", type=Path)
    ap.add_argument("--run", type=Path, default=None, help="本地 logs/runs/<run_id> 目录，用于对照")
    ap.add_argument("--speed", type=float, default=5.0, help="移动速度 m/s（默认 5.0）")
    ap.add_argument("--measure-s", type=float, default=5.0)
    ap.add_argument("--clear-ok-s", type=float, default=5.0)
    ap.add_argument("--clear-fail-s", type=float, default=3.0)
    ap.add_argument("--switch-s", type=float, default=1.0)
    ap.add_argument("--tol", type=float, default=0.01, help="判定吻合的容差（秒）")
    args = ap.parse_args(argv)

    rows = parse(args.tsv)
    if not rows:
        print("没有解析到任何行", file=sys.stderr)
        return 2

    cur_ch, prev_xy, prev_vt = 1, (0.0, 0.0), 0.0
    t_move = t_meas = t_clear = t_switch = 0.0
    dist = 0.0
    n_meas = n_ok = n_fail = n_switch = n_check = n_bad = 0
    bad: list[tuple] = []

    for r in rows:
        if r["act"] in ("进入", "--"):
            prev_vt = r["vt"]
            continue
        d = math.dist(prev_xy, r["xy"]) if r["xy"] else 0.0
        move = d / args.speed
        act = sw = 0.0
        if r["act"] == "测量":
            n_meas += 1
            act = args.measure_s
            if r["ch"] != cur_ch:
                sw = args.switch_s
                n_switch += 1
            cur_ch = r["ch"]
        elif r["act"] == "清除":
            if "成功" in r["resp"]:
                act, n_ok = args.clear_ok_s, n_ok + 1
            else:
                act, n_fail = args.clear_fail_s, n_fail + 1
        else:  # 退出等
            pass
        exp, got = r["vt"] - prev_vt, move + act + sw
        n_check += 1
        if abs(exp - got) > args.tol:
            n_bad += 1
            bad.append((r["t"], r["act"], r["xy"], r["ch"], round(exp, 3), round(got, 3)))
        dist += d
        t_move += move
        if r["act"] == "测量":
            t_meas += act
        if r["act"] == "清除":
            t_clear += act
        t_switch += sw
        if r["xy"]:
            prev_xy = r["xy"]
        prev_vt = r["vt"]

    total = rows[-1]["vt"]
    print(f"行数 {len(rows)}  校验 {n_check} 行，吻合 {n_check - n_bad}，不符 {n_bad}"
          f"  （容差 {args.tol}s）")
    for b in bad[:10]:
        print("   不符:", b)
    print(f"动作计数: 测量 {n_meas} / 清除 {n_ok + n_fail}(成功 {n_ok}, 失败 {n_fail}) / 切频道 {n_switch}")
    print(f"累计移动: {dist:.3f} m")
    print(f"总虚拟时间: {total:.3f} s")
    parts = [("移动", t_move), ("检测", t_meas), ("清除", t_clear), ("切频道", t_switch)]
    s = sum(v for _, v in parts)
    print("时间分解: " + " + ".join(f"{k} {v:.2f}" for k, v in parts) + f" = {s:.3f} s")
    print("         " + "  ".join(f"{k} {v / total * 100:.1f}%" for k, v in parts))

    if args.run:
        summ = args.run / "summary.json"
        if summ.exists():
            m = json.loads(summ.read_text(encoding="utf-8")).get("metrics", {})
            pairs = [
                ("measure_count", n_meas), ("clear_count", n_ok + n_fail),
                ("clear_success_count", n_ok), ("clear_failure_count", n_fail),
                ("channel_switch_count", n_switch),
            ]
            print("\n与本地 run 对照（本地 vs 流水）:")
            for k, got in pairs:
                loc = m.get(k)
                flag = "✅" if loc == got else "❌"
                print(f"  {flag} {k}: {loc} vs {got}")
            if "move_distance_m" in m:
                print(f"  move_distance_m: {m['move_distance_m']:.3f} vs {dist:.3f}")
            if "virtual_time_s" in m:
                print(f"  virtual_time_s: {m['virtual_time_s']:.3f} vs {total:.3f}")
        else:
            print(f"（未找到 {summ}）")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
