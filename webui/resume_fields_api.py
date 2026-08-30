"""简历解析 / 字段确认 API 路由（021 B6 T019 外迁自 webui/app.py）。

analyze-resume 上传解析与 confirm-fields 确认转换。路由体纯搬运：
HTTP 契约零改动；可 patch 符号经 ctx 动态门面。
"""

from __future__ import annotations

from flask import jsonify, request

from webui.constants import _MSG_UNSUPPORTED_PLATFORM
from webui.core import legacy_platform_guard

def register_resume_fields_routes(app, ctx):
    @app.route("/api/analyze-resume", methods=["POST"])
    def analyze_resume():
        """Stage 1: Upload resume file → AI reads it → returns unified search fields.

        Accepts multipart form with 'file' field (PDF/DOCX/TXT).
        Returns JSON with the unified schema fields for user confirmation.
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
        api_key = ctx.ai_service.retrieve_api_key(cred_ref)
        if not api_key:
            return jsonify({"ok": False, "error": "API 密钥读取失败"}), 400

        try:
            fields = analyze_resume_to_fields(
                file_bytes, fmt,
                endpoint_url=settings.get("endpoint_url", ""),
                api_key=api_key,
                model=settings.get("model", ""),
                platform=platform,
            )
            # 城市由用户选择，AI 分析结果不代填；未选择时默认全国。
            fields["city"] = []
        except AISecurityError as exc:
            return jsonify({"ok": False, "error": user_facing_error(exc.error_code)}), 502
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        # Return fields with human-readable labels for confirmation UI
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

        return jsonify({
            "ok": True,
            "platform": platform,
            "filter_schema_version": schema.schema_version,
            "fields": fields,
            "semantic": semantic,
            "labels": field_labels,
        })

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
