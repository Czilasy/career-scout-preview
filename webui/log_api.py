"""日志读取路由域（022-jd-stall-guard US4，T018）。

GET /api/logs：读 career-scout.log 尾部 / 更早分页 / 轮询增量；受本地
会话令牌保护（before_request 全局敏感 GET 清单覆盖）。每次请求重开文件
并携带文件身份（size:mtime）检测轮转，轮转后从新文件读取，保证实时
更新不失效（FR-009）。运行日志优先读取任务持久化日志，兼容旧版文件日志。
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
        # 035：按任务过滤（运行日志）——仅保留包含该 task_id 的日志行。
        task_id = str(request.args.get("task_id") or "").strip()

        # 统一成 (行号, 行文本) 列表：文件日志行号 = 1..N 的位置；持久化任务
        # 日志行号 = task_logs.seq（稳定递增）。游标（since/offset/start/end）
        # 全部按这套稳定行号进出，前端据此做增量合并。
        if task_id:
            # 任务输出由 task_logs 持久化，历史轮次对应的 pipeline run id
            # 不会出现在 career-scout.log 的每一行中。优先返回持久化日志；
            # 不存在该任务时保留旧版文件日志按文本过滤的兼容行为。
            try:
                task_rows = ctx.store.get_logs(task_id)
            except KeyError:
                task_rows = None
            except ctx.operational_errors:
                task_rows = None
            if task_rows is not None:
                numbered = [
                    (int(row.get("seq") or 0), str(row["line"]))
                    for row in task_rows
                ]
                identity = f"task:{task_id}"
            else:
                # 旧版兼容：文件里按文本过滤。过滤后重新编号（1..M），与旧行为
                # 一致，游标在过滤集内自洽。
                file_lines, identity = _read_file()
                filtered = [line for line in file_lines if task_id in line]
                numbered = list(enumerate(filtered, start=1))
        else:
            file_lines, identity = _read_file()
            numbered = list(enumerate(file_lines, start=1))

        total = numbered[-1][0] if numbered else 0
        rotated = bool(client_identity and identity and identity != client_identity)
        if not numbered:
            return jsonify({
                "ok": True, "lines": [], "start": 0, "end": 0,
                "total": 0, "identity": identity or "",
                "rotated": rotated, "empty": True,
            })
        if rotated:
            # 轮转：直接返回新文件尾部，前端据此重置展示（实时更新不失效）
            selected = numbered[-tail:]
        elif offset and offset > 1:
            # 更早分页：返回行号 < offset 的最多 tail 行（上滑加载历史）
            selected = [item for item in numbered if item[0] < offset][-tail:]
        elif since and since > 0:
            # 轮询增量：只返回行号 > since 的新增行。没有新增行时必须返回
            # 空增量——旧实现会退回重发整个尾部，轮询每拍把同一批日志再追加
            # 一遍（运行日志重复渲染 15 倍的根因）。
            selected = [item for item in numbered if item[0] > since]
        else:
            selected = numbered[-tail:]
        lines = [line for _, line in selected]
        if selected:
            start = selected[0][0]
            end = selected[-1][0]
        elif since and since > 0:
            # 无新增行：保持游标不动（end == since），前端不会前移也不会重复。
            start = since + 1
            end = since
        else:
            start = 0
            end = 0
        return jsonify({
            "ok": True, "lines": lines, "start": start, "end": end,
            "total": total, "identity": identity or "",
            "rotated": rotated, "empty": False,
        })
