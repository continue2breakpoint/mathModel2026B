#!/usr/bin/env python3
"""汇总 ``logs/results.jsonl``：命令行表格 / CSV / Markdown / 按参数分组。

用法::

    python script/report.py                      # 全部 run 的表格
    python script/report.py --tag tune1 --last 20
    python script/report.py --csv out.csv
    python script/report.py --markdown           # 贴到论文/报告里
    python script/report.py --group-by param_scan_radius
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import statistics
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(REPO_ROOT / "framework" / "src"))

from mathmodel2026b.logging_utils import default_log_root, read_index  # noqa: E402

FLOAT_FMT = {
    "cleared_ratio": "{:.3f}",
    "average_clear_time_s": "{:.1f}",
    "virtual_time_s": "{:.1f}",
    "move_distance_m": "{:.0f}",
    "wall_s": "{:.2f}",
}

NUMERIC_METRICS = (
    "cleared_ratio",
    "average_clear_time_s",
    "virtual_time_s",
    "move_distance_m",
    "measure_count",
    "clear_count",
    "wall_s",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="汇总批量调参结果")
    parser.add_argument("--log-root", default=None)
    parser.add_argument("--tag", default=None)
    parser.add_argument("--status", default=None)
    parser.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                        help="按参数过滤，如 --param scan_radius=1400")
    parser.add_argument("--last", type=int, default=None, help="只看最后 N 条")
    parser.add_argument("--group-by", default=None, help="如 param_scan_radius，或逗号分隔多列")
    parser.add_argument("--sort-by", default="started_utc")
    parser.add_argument("--csv", default=None, help="导出 CSV 到该路径")
    parser.add_argument("--markdown", action="store_true", help="以 Markdown 表格输出")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出原始记录")
    return parser


def select(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    out = rows
    if args.tag:
        out = [r for r in out if r.get("tag") == args.tag]
    if args.status:
        out = [r for r in out if r.get("status") == args.status]
    for item in args.param:
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        out = [r for r in out if str(r.get(f"param_{key.strip()}")) == value.strip()]
    out = sorted(out, key=lambda r: str(r.get(args.sort_by) or ""))
    if args.last:
        out = out[-args.last :]
    return out


def group(rows: list[dict[str, Any]], keys: list[str]) -> list[tuple[str, list[dict[str, Any]]]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        label = " | ".join(f"{k}={row.get(k)}" for k in keys)
        buckets.setdefault(label, []).append(row)
    return sorted(buckets.items())


def stat_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r.get("status") == "ok"]
    info: dict[str, Any] = {
        "runs": len(rows),
        "ok": len(ok),
        "full_clear": sum(1 for r in ok if _is_one(r.get("cleared_ratio"))),
    }
    for metric in NUMERIC_METRICS:
        values = [r[metric] for r in ok if isinstance(r.get(metric), (int, float))]
        if values:
            info[f"{metric}_mean"] = statistics.fmean(values)
            if len(values) > 1:
                info[f"{metric}_std"] = statistics.pstdev(values)
            info[f"{metric}_min"] = min(values)
            info[f"{metric}_max"] = max(values)
    return info


def _is_one(value: Any) -> bool:
    return isinstance(value, (int, float)) and abs(value - 1.0) < 1e-9


def render_table(rows: list[dict[str, Any]], markdown: bool) -> str:
    columns = [
        "run_id",
        "tag",
        "status",
        "param_seed",
        "cleared_count",
        "n_jammers",
        "cleared_ratio",
        "average_clear_time_s",
        "virtual_time_s",
        "move_distance_m",
        "wall_s",
    ]
    if markdown:
        lines = ["| " + " | ".join(columns) + " |", "|" + "|".join(["---"] * len(columns)) + "|"]
        for row in rows:
            lines.append("| " + " | ".join(_cell(row, c) for c in columns) + " |")
        return "\n".join(lines)
    buf = io.StringIO()
    widths = [max(len(c), *(len(_cell(r, c)) for r in rows)) if rows else len(c) for c in columns]
    buf.write("  ".join(c.ljust(w) for c, w in zip(columns, widths)) + "\n")
    buf.write("  ".join("-" * w for w in widths) + "\n")
    for row in rows:
        buf.write("  ".join(_cell(row, c).ljust(w) for c, w in zip(columns, widths)) + "\n")
    return buf.getvalue()


def _cell(row: dict[str, Any], column: str) -> str:
    value = row.get(column)
    if value is None:
        return "-"
    fmt = FLOAT_FMT.get(column)
    if fmt and isinstance(value, (int, float)):
        return fmt.format(value)
    if isinstance(value, float):
        return f"{value:.3f}"
    text = str(value)
    return text[:38] if column == "run_id" else text


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log_root = Path(args.log_root) if args.log_root else default_log_root()
    rows = select(read_index(log_root), args)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    elif args.group_by:
        keys = [k.strip() for k in args.group_by.split(",") if k.strip()]
        for label, bucket in group(rows, keys):
            info = stat_block(bucket)
            print(
                f"{label:<50} runs={info['ok']}/{info['runs']} full={info['full_clear']} "
                f"ratio_mean={info.get('cleared_ratio_mean', float('nan')):.3f} "
                f"avg_s_mean={info.get('average_clear_time_s_mean', float('nan')):.1f} "
                f"avg_s_std={info.get('average_clear_time_s_std', 0.0):.1f} "
                f"virt_s_mean={info.get('virtual_time_s_mean', float('nan')):.1f}"
            )
    else:
        print(render_table(rows, args.markdown))
        info = stat_block(rows)
        print(
            f"\n合计 runs={info['runs']} ok={info['ok']} 满清除={info['full_clear']} "
            f"清除率均值={info.get('cleared_ratio_mean', float('nan')):.3f} "
            f"平均定位清除时间均值={info.get('average_clear_time_s_mean', float('nan')):.1f}s "
            f"虚拟时间均值={info.get('virtual_time_s_mean', float('nan')):.1f}s"
        )

    if args.csv:
        if not rows:
            print("没有可导出的记录", file=sys.stderr)
            return 1
        fieldnames = sorted({k for row in rows for k in row})
        with open(args.csv, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        print(f"已导出 CSV：{args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
