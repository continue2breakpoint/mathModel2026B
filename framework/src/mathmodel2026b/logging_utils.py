"""结构化日志：为"批量数学迭代测试调参"设计的记录格式。

目录布局
--------
::

    <log_root>/
      results.jsonl                 # 跨 run 的扁平索引，一行一个 run（直接喂 pandas）
      runs/
        <run_id>/
          meta.json                 # 运行配置 + 环境 + 案例真值（mock 时）
          run.jsonl                 # 逐步事件流（每次 HTTP 交互一条）
          summary.json              # 该 run 的最终指标
          trace.jsonl               # 策略内部决策轨迹（可关闭）

记录约定
--------
* 所有 JSONL 都是"一行一个完整 JSON 对象"，UTF-8，``ensure_ascii=False``。
* ``run.jsonl`` 每条都带 ``seq``（单调递增）与 ``t_wall``（epoch 秒），
  便于按时间与顺序对齐。
* ``results.jsonl`` 只放**扁平**字段（数值/字符串），参数用 ``param_*`` 前缀，
  指标用裸名，方便直接 `pandas.read_json(..., lines=True)` 后排序分组。
* ``run_id`` 同时是目录名和索引主键。
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

INDEX_FILENAME = "results.jsonl"

#: 指标字段（写进 results.jsonl 的裸名）
METRIC_FIELDS = (
    "cleared_count",
    "n_jammers",
    "cleared_ratio",
    "virtual_time_s",
    "average_clear_time_s",
    "move_distance_m",
    "measure_count",
    "measure_accepted_count",
    "clear_count",
    "clear_success_count",
    "clear_failure_count",
    "channel_switch_count",
    "scan_points_visited",
    "wall_s",
    "http_calls",
)


def default_log_root() -> Path:
    """默认日志根目录：``<mathModel2026B>/logs``。"""
    return Path(__file__).resolve().parents[3] / "logs"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def new_run_id(tag: str = "q3", seed: int | None = None) -> str:
    parts = [utc_stamp()]
    if seed is not None:
        parts.append(f"s{seed}")
    if tag:
        parts.append(tag)
    parts.append(uuid.uuid4().hex[:6])
    return "_".join(parts)


class JsonlWriter:
    """按行追加 JSON 的写入器；每条立刻 flush，进程被杀也不丢已写内容。"""

    def __init__(self, path: Path, enabled: bool = True) -> None:
        self.path = Path(path)
        self.enabled = enabled
        self._fh = None
        if enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8")

    def write(self, record: Mapping[str, Any]) -> None:
        if self._fh is None:
            return
        self._fh.write(json.dumps(record, ensure_ascii=False, default=_json_default) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "JsonlWriter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return repr(obj)


@dataclass
class RunLogger:
    """一次运行的全部落盘产物。"""

    run_id: str
    log_root: Path
    tag: str = "q3"
    write_trace: bool = True
    _seq: int = field(default=0, init=False)
    _started: float = field(default_factory=time.time, init=False)

    def __post_init__(self) -> None:
        self.log_root = Path(self.log_root)
        self.run_dir = self.log_root / "runs" / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events = JsonlWriter(self.run_dir / "run.jsonl")
        self.trace = JsonlWriter(self.run_dir / "trace.jsonl", enabled=self.write_trace)

    # -- 事件流 ----------------------------------------------------------
    def event(self, record: Mapping[str, Any]) -> None:
        self._seq += 1
        payload = dict(record)
        payload["seq"] = self._seq
        payload.setdefault("t_wall", round(time.time(), 6))
        payload.setdefault("t_rel", round(time.time() - self._started, 6))
        self.events.write(payload)

    def strategy_event(self, record: Mapping[str, Any]) -> None:
        self._seq += 1
        payload = dict(record)
        payload["seq"] = self._seq
        payload.setdefault("t_wall", round(time.time(), 6))
        payload.setdefault("t_rel", round(time.time() - self._started, 6))
        self.events.write({"action": "strategy", **payload})
        self.trace.write(payload)

    # -- 文档 ------------------------------------------------------------
    def write_meta(self, payload: Mapping[str, Any]) -> None:
        _write_json(self.run_dir / "meta.json", payload)

    def write_summary(self, payload: Mapping[str, Any]) -> None:
        _write_json(self.run_dir / "summary.json", payload)

    def append_index(self, record: Mapping[str, Any]) -> None:
        with JsonlWriter(self.log_root / INDEX_FILENAME) as writer:
            writer.write(record)

    @property
    def wall_s(self) -> float:
        return time.time() - self._started

    def close(self) -> None:
        self.events.close()
        self.trace.close()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=_json_default)
        fh.write("\n")
    os.replace(tmp, path)


def environment_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "node": _first_line(["node", "-v"]),
        "cwd": os.getcwd(),
        "env": {
            k: os.environ.get(k)
            for k in ("JAMMERS_ROOT", "JAMMERS_ROBOT_URL", "JAMMERS_CONFIG", "JAMMERS_PYTHON")
            if os.environ.get(k)
        },
    }
    root = _git_root()
    if root is not None:
        info["git_root"] = str(root)
        info["git_rev"] = _first_line(["git", "-C", str(root), "rev-parse", "HEAD"])
        info["git_dirty"] = bool(
            _first_line(["git", "-C", str(root), "status", "--porcelain"])
        )
    return info


def _first_line(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (out.stdout or "").strip()
    return text.splitlines()[0] if text else None


def _git_root() -> Path | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            cwd=str(Path(__file__).resolve().parent),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (out.stdout or "").strip()
    return Path(text) if text else None


def flatten_index_record(
    *,
    run_id: str,
    tag: str,
    mode: str,
    status: str,
    started_utc: str,
    finished_utc: str,
    params: Mapping[str, Any],
    metrics: Mapping[str, Any],
    error: str | None = None,
    notes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """把一次运行压成一行索引记录（扁平、可直接做统计）。"""
    record: dict[str, Any] = {
        "run_id": run_id,
        "tag": tag,
        "mode": mode,
        "status": status,
        "started_utc": started_utc,
        "finished_utc": finished_utc,
        "error": error,
    }
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            record[f"param_{key}"] = ",".join(str(v) for v in value)
        elif isinstance(value, dict):
            record[f"param_{key}"] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            record[f"param_{key}"] = value
    for key in METRIC_FIELDS:
        record[key] = metrics.get(key)
    record["params_json"] = json.dumps(params, ensure_ascii=False, sort_keys=True, default=_json_default)
    if notes:
        record["notes_json"] = json.dumps(notes, ensure_ascii=False, default=_json_default)
    return record


def read_index(log_root: Path | str) -> list[dict[str, Any]]:
    path = Path(log_root) / INDEX_FILENAME
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def summarize_index(rows: Iterable[Mapping[str, Any]], key: str = "run_id") -> str:
    """把索引记录渲染成对齐的文本表（命令行快速看结果）。"""
    rows = list(rows)
    if not rows:
        return "（无记录）"
    header = (
        f"{'run_id':<38} {'tag':<10} {'status':<8} {'seed':>6} "
        f"{'clear':>7} {'ratio':>7} {'virt_s':>9} {'avg_s':>8} {'wall_s':>7}"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        avg = row.get("average_clear_time_s")
        lines.append(
            f"{str(row.get(key, ''))[:38]:<38} "
            f"{str(row.get('tag', ''))[:10]:<10} "
            f"{str(row.get('status', ''))[:8]:<8} "
            f"{str(row.get('param_seed', '')):>6} "
            f"{str(row.get('cleared_count', '')):>4}/{str(row.get('n_jammers', '')):<3}"
            f"{_fmt(row.get('cleared_ratio'), 7)} "
            f"{_fmt(row.get('virtual_time_s'), 9)} "
            f"{_fmt(avg, 8)} "
            f"{_fmt(row.get('wall_s'), 7)}"
        )
    return "\n".join(lines)


def _fmt(value: Any, width: int) -> str:
    if value is None:
        return " " * (width - 1) + "-"
    if isinstance(value, float):
        return f"{value:>{width}.2f}"
    return f"{str(value):>{width}}"
