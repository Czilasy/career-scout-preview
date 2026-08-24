"""日志读取路由域（022-jd-stall-guard US4，T018）。

GET /api/logs：读 career-scout.log 尾部 / 更早分页 / 轮询增量；受本地
会话令牌保护（before_request 全局敏感 GET 清单覆盖）。每次请求重开文件
并携带文件身份（size:mtime）检测轮转，轮转后从新文件读取，保证实时
更新不失效（FR-009）。只读文件系统与 logging_setup，不触碰 store。
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import jsonify, request

from webui.logging_setup import default_log_dir

LOG_FILE_NAME = "career-scout.log"
DEFAULT_TAIL = 200
MAX_TAIL = 500


def register_log_routes(app, ctx):
    def _log_path() -> Path:
        directory = app.config.get("CAREER_SCOUT_LOG_DIR") or default_log_dir()
        return Path(directory) / LOG_FILE_NAME

    def _read_file() -> tuple[list[str], str | None]:
        """读取日志全文并返回 (行列表, 文件身份)。文件不存在时返回空。"""
        path = _log_path()
        try:
            stat = os.stat(path)
            identity = f"{stat.st_size}:{int(stat.st_mtime)}"
        except OSError:
            return [], None
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            return [], identity
        return lines, identity

    def _parse_int(value, default: int, *, minimum: int = 0) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, parsed)

    @app.route("/api/logs")
    def api_logs():
        tail = min(
            _parse_int(request.args.get("tail"), DEFAULT_TAIL, minimum=1),
            MAX_TAIL,
        )
        offset = _parse_int(request.args.get("offset"), 0)
        since = _parse_int(request.args.get("since"), 0)
        client_identity = str(request.args.get("identity") or "").strip()

        lines, identity = _read_file()
        total = len(lines)
        rotated = bool(client_identity and identity and identity != client_identity)
        if not lines:
            return jsonify({
                "ok": True, "lines": [], "start": 0, "end": 0,
                "total": 0, "identity": identity or "",
                "rotated": rotated, "empty": True,
            })
        if rotated:
            # 轮转：直接返回新文件尾部，前端据此重置展示（实时更新不失效）
            selected = lines[-tail:]
            start = total - len(selected) + 1
            end = total
        elif offset and offset > 1:
            # 更早分页：返回行号 < offset 的最多 tail 行（上滑加载历史）
            limit = min(tail, offset - 1)
            selected = lines[max(0, offset - 1 - limit): offset - 1]
            start = offset - len(selected)
            end = offset - 1
        elif since and since > 0 and total > since:
            # 轮询增量：返回行号 > since 的新增行
            selected = lines[since:]
            start = since + 1
            end = total
        else:
            selected = lines[-tail:]
            start = total - len(selected) + 1
            end = total
        return jsonify({
            "ok": True, "lines": selected, "start": start, "end": end,
            "total": total, "identity": identity or "",
            "rotated": rotated, "empty": False,
        })
