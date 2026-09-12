"""统一界面外壳的静态资源服务（两个 Flask 蓝图共用）。

双点工作区（`/`）与知识矩阵工作区（`/matrix`）返回**同一个页面**，
只有默认工作区不同；页面里的 `__VERSION__` 与 `__DEFAULT_WS__` 在服务时替换。
"""
from __future__ import annotations

from pathlib import Path

from flask import Response

ASSETS = Path(__file__).resolve().parent / "q2_assets"
INDEX = ASSETS / "q2_index.html"

#: 统一界面外壳的版本号（同时用于 /version 与静态资源的缓存击穿）
VERSION = "2026-09-13-unified-ui-v1"

NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

MIME = {".css": "text/css", ".js": "application/javascript",
        ".html": "text/html", ".svg": "image/svg+xml", ".json": "application/json"}


def page(default_ws: str = "", version: str = "") -> Response:
    """统一页面；``default_ws`` 让 /matrix 直接落在知识矩阵工作区。"""
    html = INDEX.read_text(encoding="utf-8")
    html = html.replace("__VERSION__", version or VERSION).replace("__DEFAULT_WS__", default_ws)
    return Response(html, mimetype="text/html", headers=NO_CACHE)


def asset(name: str) -> Response:
    """`/assets/<name>`：只允许取 q2_assets/ 下的文件，避免路径穿越。"""
    target = (ASSETS / name).resolve()
    if ASSETS.resolve() not in target.parents or not target.is_file():
        return Response("not found", status=404, mimetype="text/plain")
    return Response(target.read_text(encoding="utf-8"),
                    mimetype=MIME.get(target.suffix, "text/plain"), headers=NO_CACHE)
