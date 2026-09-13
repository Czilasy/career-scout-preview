"""简历解析 / 字段确认 API 路由（021 B6 T019 外迁自 webui/app.py）。

analyze-resume 上传解析与 confirm-fields 确认转换。路由体纯搬运：
HTTP 契约零改动。

Spec041 B093：`background=true` 时分析改为后台任务（202 + task_id），
任务带 profile_id/platform，刷新后可由 latest-running-task / task-state
接回；后台与同步路径共用同一字段投影，结果口径不分叉。
"""

from __future__ import annotations
import threading
import time
import uuid

from webui import ai as ai_service

from flask import jsonify, request

from webui.constants import _MSG_UNSUPPORTED_PLATFORM
from webui.core import legacy_platform_guard
from webui.logging_setup import get_logger

_logger = get_logger(__name__)

_RESUME_ANALYSIS_KIND = "resume_analysis"


def _project_analysis_payload(fields: dict, reg, platform: str) -> dict:
    """AI 原始字段 → 用户确认载荷（同步与后台任务共用同一投影）。

    城市由用户选择，AI 分析结果不代填；未选择时默认全国。
    """
    fields["city"] = []
    schema = reg.filter_schema
    schema_keys = {field.key for field in schema.fields}
    fields = {
        key: value for key, value in fields.items()
        if key in ("keyword", "city", "profile_summary", "profile_facts")
        or key in schema_keys
    }
    field_labels = {
        "keyword": ("搜索关键词", fields["keyword"], "keyword_chips"),
        "city": ("城市", fields["city"], "city"),
    }
    semantic: dict[str, list[str]] = {
        "keyword": [
            str(item.get("word", "")) if isinstance(item, dict) else str(item)
            for item in fields.get("keyword", []) if item
        ],
        "city": list(fields.get("city", [])),
    }
    for field in schema.fields:
        values = fields.get(field.key) or []
        labels = [field.label_for(v) for v in values if field.label_for(v)]
        semantic[field.key] = labels
        field_labels[field.key] = (
            field.label, values, {opt.label: opt.value for opt in field.options},
        )
    return {
        "ok": True,
        "platform": platform,
        "filter_schema_version": schema.schema_version,
        "fields": fields,
        "semantic": semantic,
        "labels": field_labels,
    }


def _register_resume_analysis_task(ctx, *, platform: str, profile_id: str) -> tuple[str, dict]:
    """登记一条简历分析任务；身份（platform/profile_id）冻结在任务上。"""
    task_id = f"resume-analysis-{uuid.uuid4().hex[:12]}"
    task = {
        "kind": _RESUME_ANALYSIS_KIND,
        "status": "queued",
        "progress": {"message": "正在分析简历…"},
        "logs": [],
        "result": None,
        "error": "",
        "started_at": int(time.time() * 1000),
        "finished_at": None,
        "stop_event": threading.Event(),
        "platform": platform,
        "profile_id": profile_id or None,
    }
    with ctx.lock:
        ctx.tasks[task_id] = task
    return task_id, task


def _run_resume_analysis_task(
    ctx, task_id: str, task: dict, *,
    file_bytes: bytes, fmt: str, settings: dict, api_key: str, reg, platform: str,
) -> None:
    """后台执行简历分析：状态只落本任务的轻量字段，不参与筛选白箱口径。"""
    from webui.ai import (
        AISecurityError,
        analyze_resume_to_fields,
        user_facing_error,
    )

    def _finish(status: str, *, error: str = "", result: dict | None = None) -> None:
        with ctx.lock:
            if ctx.tasks.get(task_id) is not task:
                return
            task["status"] = status
            task["error"] = error
            task["result"] = result
            task["finished_at"] = int(time.time() * 1000)
        ctx.schedule_pipeline_task_cleanup(task_id)

    with ctx.lock:
        if ctx.tasks.get(task_id) is not task:
            return
        task["status"] = "running"
        task["progress"] = {"message": "正在分析简历…"}
    try:
        raw_fields = analyze_resume_to_fields(
            file_bytes, fmt,
            endpoint_url=settings.get("endpoint_url", ""),
            api_key=api_key,
            model=settings.get("model", ""),
            platform=platform,
        )
        payload = _project_analysis_payload(raw_fields, reg, platform)
    except AISecurityError as exc:
        _finish("failed", error=user_facing_error(exc.error_code))
        return
    except ValueError as exc:
        _finish("failed", error=str(exc) or "简历分析失败")
        return
    except Exception as exc:
        # 不吞异常：留痕并把真实失败落到任务状态，供接回时展示。
        _logger.warning("resume analysis task failed: %s", type(exc).__name__)
        _finish("failed", error="简历分析失败，请重试")
        return
    _finish("done", result=payload)


def register_resume_fields_routes(app, ctx):
    @app.route("/api/analyze-resume", methods=["POST"])
    def analyze_resume():
        """Stage 1: Upload resume file → AI reads it → returns unified search fields.

        Accepts multipart form with 'file' field (PDF/DOCX/TXT).
        Returns JSON with the unified schema fields for user confirmation.
        `background=true` 时登记后台任务并立即返回 202 + task_id。
        """
        from webui.ai import (
            AISecurityError,
            analyze_resume_to_fields,
            user_facing_error,
        )
        from webui.platforms import (
            PlatformNotRegisteredError,
            UnknownPlatformError,
            get_platform,
            validate_platform_key,
        )
        from webui.resume import validate_format, validate_size

        file = request.files.get("file")
        if not file or not file.filename:
            return jsonify({"ok": False, "error": "未上传文件"}), 400

        try:
            fmt = validate_format(file.filename)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        file_bytes = file.read()
        try:
            validate_size(file_bytes)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        platform_raw = request.form.get("platform") or "boss"
        try:
            platform = validate_platform_key(platform_raw)
        except UnknownPlatformError:
            return jsonify({"ok": False, "error_code": "platform_validation_failed", "error": _MSG_UNSUPPORTED_PLATFORM}), 400
        try:
            reg = get_platform(platform)
        except PlatformNotRegisteredError:
            return jsonify({"ok": False, "error_code": "platform_schema_unavailable", "error": "平台 schema 不可用"}), 503

        # Get AI credentials
        settings = ctx.store.get_ai_settings()
        if not settings.get("is_configured"):
            return jsonify({"ok": False, "error": "AI 未配置，请先设置 API 地址和密钥"}), 400
        cred_ref = ctx.store.get_credential_ref()
        if not cred_ref:
            return jsonify({"ok": False, "error": "未找到 API 密钥"}), 400
        api_key = ai_service.retrieve_api_key(cred_ref)
        if not api_key:
            return jsonify({"ok": False, "error": "API 密钥读取失败"}), 400

        background = (
            str(request.form.get("background") or "").strip().lower()
            in {"1", "true", "yes"}
        )
        profile_id = str(request.form.get("profile_id") or "").strip()

        if background:
            task_id, task = _register_resume_analysis_task(
                ctx, platform=platform, profile_id=profile_id,
            )
            threading.Thread(
                target=_run_resume_analysis_task,
                args=(ctx, task_id, task),
                kwargs={
                    "file_bytes": file_bytes, "fmt": fmt, "settings": settings,
                    "api_key": api_key, "reg": reg, "platform": platform,
                },
                name=f"resume-analysis-{task_id.rsplit('-', 1)[-1]}",
                daemon=True,
            ).start()
            return jsonify({
                "ok": True,
                "task_id": task_id,
                "kind": _RESUME_ANALYSIS_KIND,
                "status": task["status"],
                "platform": platform,
                "profile_id": task["profile_id"],
            }), 202

        try:
            fields = analyze_resume_to_fields(
                file_bytes, fmt,
                endpoint_url=settings.get("endpoint_url", ""),
                api_key=api_key,
                model=settings.get("model", ""),
                platform=platform,
            )
        except AISecurityError as exc:
            return jsonify({"ok": False, "error": user_facing_error(exc.error_code)}), 502
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        # Return fields with human-readable labels for confirmation UI
        return jsonify(_project_analysis_payload(fields, reg, platform))

    @app.route("/api/confirm-fields", methods=["POST"])
    def confirm_fields():
        """Stage 2: User confirms/edits the AI-extracted fields.

        Accepts JSON body with the unified fields (user may have edited them).
        Validates all values and returns ready-to-execute script parameters.
        """
        from webui.ai import _validate_unified_fields

        body = request.get_json(silent=True)
        if isinstance(body, dict):
            legacy_platform_guard(body.get("platform"))
        if not body or not isinstance(body, dict):
            return jsonify({"ok": False, "error": "无效的请求体"}), 400

        # Validate the confirmed fields
        fields = _validate_unified_fields(body)

        if not fields.get("keyword"):
            return jsonify({"ok": False, "error": "搜索关键词不能为空"}), 400
        if not fields.get("city"):
            return jsonify({"ok": False, "error": "城市无效，请选择支持的城市"}), 400

        # spec 007 ③：keyword 现在是 [{word, recommended}]，脚本消费逗号拼接字符串。
        # 该端点已废弃（前端走 /api/execute-search），此处转换仅保证不崩。
        kw_chips = fields.get("keyword") or []
        if isinstance(kw_chips, list) and kw_chips and isinstance(kw_chips[0], dict):
            kw_str = ",".join(c.get("word", "") for c in kw_chips if c.get("word"))
        else:
            kw_str = str(kw_chips) if kw_chips else ""

        # Build the exact parameters the script consumes
        script_params = {
            "keyword": kw_str,
            "city": fields["city"],
            "filters": {},
        }
        for key in ("salary", "experience", "degree", "industry", "scale", "stage"):
            if fields.get(key):
                script_params["filters"][key] = fields[key]

        return jsonify({"ok": True, "confirmed_fields": fields, "script_params": script_params})
