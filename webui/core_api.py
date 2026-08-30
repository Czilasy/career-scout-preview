"""核心杂项 API 路由（021 B6 T019 外迁自 webui/app.py）。

入口页、静态资源、平台/选项/收藏/过滤标签/会话、环境自检、旧抓取
入口、任务查询与 CSV 导出。路由体纯搬运：HTTP 契约零改动。
"""

from __future__ import annotations

import csv
import io
import time
from pathlib import Path

from flask import jsonify, request, send_from_directory

from scripts import job_summary
from webui import desktop_runtime
from webui.constants import (
    FRONTEND_DIST,
    LIST_LIMIT,
    PROJECT_ROOT,
    _MSG_BOSS_LOGIN_STATUS,
    _MSG_UNSUPPORTED_PLATFORM,
)
from webui.task_runners import SCRAPER, _env, _task_payload
from webui.core import (
    build_filter_options,
    legacy_platform_guard,
    match_jobs,
    normalize_profile,
    validate_search_params,
)

def register_core_routes(app, ctx):
    def _tag_boss(obj):
        """T604: legacy 成功响应标识 platform=boss（仅响应层，不持久化）。

        合同第 370 行：所有 legacy 成功响应中的任务/run/岗位/结果对象补充
        ``platform=boss``；该标识不把这些链路升级成多平台主链。返回浅拷贝，
        避免污染 store 内部对象。
        """
        if isinstance(obj, dict):
            return {**obj, "platform": "boss"}
        return obj

    @app.route("/")
    def index():
        index_path = FRONTEND_DIST / "index.html"
        if not index_path.is_file():
            return jsonify({
                "error_code": "frontend_not_built",
                "user_message": "前端构建产物不存在，请先在 webui 目录执行 npm run build",
            }), 503
        html = index_path.read_text(encoding="utf-8")
        resp = app.response_class(html, mimetype="text/html")
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

    @app.route("/static/<path:filename>")
    def frontend_static(filename):
        response = send_from_directory(FRONTEND_DIST, filename)
        # Vite filenames contain content hashes, so long-lived immutable cache
        # is safe while index.html itself remains no-cache.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.route("/api/platforms")
    def platforms_list():
        """T207 补丁：返回平台注册表投影（contracts/http-api.md GET /api/platforms）。

        platforms.py 的 list_platforms() 已在 tasks003 测过投影函数；
        本路由只负责 HTTP 暴露，不返回 profile 路径或路径摘要。
        """
        from webui.platforms import DEFAULT_PLATFORM, list_platforms
        platforms = [
            {
                "key": reg.key,
                "display_name": reg.display_name,
                "filter_schema_version": reg.filter_schema.schema_version,
                "city_mapping_version": reg.city_catalog.mapping_version,
                "enabled_for_new_tasks": reg.enabled_for_new_tasks,
                "availability_reason": reg.availability_reason,
            }
            for reg in list_platforms()
        ]
        return jsonify({
            "ok": True,
            "platforms": platforms,
            "default_platform": DEFAULT_PLATFORM,
        })

    @app.route("/api/options")
    def options():
        """T207 补丁：平台感知城市目录（contracts/http-api.md GET /api/options?platform）。

        兼容策略：
        - 无 platform 参数 → 旧 BOSS 形状 {filters, cities}（保护现有前端和测试）
        - 显式 platform → 新形状 {ok, platform, city_mapping_version, cities:[{label, value}]}
          cities 的 value 是规范名（不是平台码）；后端解析并冻结（合同 L57）。
        """
        platform_raw = request.args.get("platform")
        if not platform_raw:
            # 旧 BOSS 兼容形状（不动一行，保护 test_options_come_from_scraper_maps）
            cities = [{"label": name, "value": name} for name in ctx.boss.CITY_MAP]
            return jsonify({"filters": build_filter_options(), "cities": cities})
        # 新平台感知形状
        from webui.platforms import (
            UnknownPlatformError,
            get_platform_or_none,
            validate_platform_key,
        )
        try:
            validate_platform_key(platform_raw)
        except UnknownPlatformError:
            return jsonify({
                "ok": False,
                "error_code": "platform_validation_failed",
                "user_message": _MSG_UNSUPPORTED_PLATFORM,
            }), 400
        reg = get_platform_or_none(platform_raw)
        if reg is None:
            return jsonify({
                "ok": False,
                "error_code": "platform_schema_unavailable",
                "user_message": "平台尚未注册",
            }), 503
        cities = [
            {"label": e.label, "value": e.name}
            for e in reg.city_catalog.entries
        ]
        return jsonify({
            "ok": True,
            "platform": reg.key,
            "city_mapping_version": reg.city_catalog.mapping_version,
            "cities": cities,
        })

    @app.route("/api/favorites")
    def favorites_list():
        """Return all favorited (interested) jobs across profiles."""
        rows = ctx.store.list_all_interested()
        items = []
        for pj in rows:
            try:
                job = ctx.store.get_job(pj["job_id"])
            except KeyError:
                continue
            items.append({
                "job_id": job["id"],
                "profile_id": pj.get("profile_id", ""),
                # 身份字段一并返回：取消收藏走内部 ID 即可，但保留三元组
                # 供调用方按权威身份协议使用（platform-schema 三身份独立）。
                "platform": job.get("platform", ""),
                "platform_job_id": job.get("platform_job_id", ""),
                "canonical_url": job.get("canonical_url", ""),
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "salary": job.get("salary", ""),
                "location": job.get("location", ""),
                "job_link": job.get("source_url") or job.get("canonical_url", ""),
            })
        return jsonify({"items": items, "count": len(items)})

    @app.route("/api/filter-labels")
    def filter_labels():
        """T207 补丁：平台 AI 筛选 schema（contracts/http-api.md GET /api/filter-labels?platform）。

        兼容策略：
        - 无 platform 参数 → 旧 BOSS 形状 {labels: {salary, stage, ...}}（保护现有前端）
        - 显式 platform → 新形状 {ok, platform, schema_version, enabled_for_new_tasks, fields}
          复用 platforms.project_filter_schema 直接返回所需结构。
        """
        platform_raw = request.args.get("platform")
        if not platform_raw:
            # 旧 BOSS 兼容形状（不动一行，保护 DiscoveryView.vue 现有调用）
            return jsonify({"labels": {
                "salary": ("薪资范围", [], ctx.boss.SALARY_MAP),
                "experience": ("经验要求", [], ctx.boss.EXPERIENCE_MAP),
                "degree": ("学历", [], ctx.boss.DEGREE_MAP),
                "industry": ("行业", [], ctx.boss.INDUSTRY_MAP),
                "scale": ("公司规模", [], ctx.boss.SCALE_MAP),
                "stage": ("融资阶段", [], ctx.boss.STAGE_MAP),
            }})
        # 新平台感知形状：复用 platforms.project_filter_schema
        from webui.platforms import (
            UnknownPlatformError,
            get_platform_or_none,
            project_filter_schema,
            validate_platform_key,
        )
        try:
            validate_platform_key(platform_raw)
        except UnknownPlatformError:
            return jsonify({
                "ok": False,
                "error_code": "platform_validation_failed",
                "user_message": _MSG_UNSUPPORTED_PLATFORM,
            }), 400
        if get_platform_or_none(platform_raw) is None:
            return jsonify({
                "ok": False,
                "error_code": "platform_schema_unavailable",
                "user_message": "平台 schema 不可用",
            }), 503
        return jsonify(project_filter_schema(platform_raw))

    @app.route("/api/session")
    def session():
        payload = (
            {"token": app.config["API_TOKEN"], "build_hash": ctx.build_hash,
             "version": ctx.product_version, "runtime_mode": ctx.runtime_mode}
            if app.config.get("TESTING") else {
                "status": "ok", "build_hash": ctx.build_hash,
                "version": ctx.product_version, "runtime_mode": ctx.runtime_mode,
            }
        )
        response = jsonify(payload)
        response.set_cookie(
            app.config["SESSION_COOKIE_NAME"], app.config["API_TOKEN"],
            httponly=True, samesite="Strict", secure=False, path="/",
        )
        return response

    @app.route("/api/check")
    def check():
        # 契约 http-api.md L334-336：显式平台解析对应登录空间；省略平台只
        # 兼容 BOSS。智联检查不得调用旧 BOSS scraper，新前端走 browser
        # account open 打开登录空间。
        check_platform = (request.args.get("platform") or "boss").strip()
        from webui.platforms import get_platform_or_none, resolve_login_space
        reg = get_platform_or_none(check_platform)
        if reg is None:
            return jsonify({
                "ok": False,
                "error_code": "platform_validation_failed",
                "user_message": _MSG_UNSUPPORTED_PLATFORM,
                "platform": check_platform,
            }), 400
        if check_platform != "boss":
            from webui.pipeline_exec import resolve_browser_account
            account = ctx.account_for_run()
            boss_dir = resolve_browser_account(
                account, app.config["BROWSER_ACCOUNTS_PATH"])
            if not boss_dir:
                return jsonify({
                    "ok": False, "platform": check_platform, "connected": False,
                    "error_code": "platform_schema_unavailable",
                    "user_message": "账号浏览器资料目录不可用",
                }), 503
            login_space = resolve_login_space(
                check_platform, account, boss_profile_dir=boss_dir)
            from webui.source import ZhilianCdpSource
            source = ZhilianCdpSource(
                browser_account=account, cdp_port=login_space.cdp_port,
                profile_key=login_space.profile_key)
            outcome = source.preflight()
            return jsonify({
                "ok": bool(outcome.ok),
                "platform": check_platform,
                "connected": bool(outcome.ok),
                "error_code": outcome.failed_code or "",
                "error_reason": outcome.failed_reason or "",
            })
        if ctx.runtime_mode == "exe":
            # 合同 inprocess-runner §6：EXE 模式不 spawn 子进程，复用
            # ctx.boss.collect_check_items 库式路径；返回结构与源码模式一致。
            items, all_pass = ctx.boss.collect_check_items(cdp_port=ctx.boss.DEFAULT_CDP_PORT)
            lines = []
            for index, item in enumerate(items, start=1):
                mark = {"ok": "✅", "fail": "❌", "skip": "⏭️"}.get(item["status"], "?")
                lines.append(f"[{index}/{len(items)}] {item['name']}...")
                lines.append(f"  {mark} {item['detail']}")
                if item.get("fix"):
                    lines.append(f"     🔧 {item['fix']}")
            lines.append("")
            lines.append("✅ 所有检查通过，可以开始抓取" if all_pass
                         else "❌ 部分检查未通过，请修复后重试")
            output = "\n".join(lines)
            return jsonify({
                "ok": bool(all_pass),
                "platform": "boss",
                "connected": bool(all_pass),
                "returncode": 0 if all_pass else 1,
                "output": output,
            })
        result = ctx.ScraperExecutor(max_output_bytes=64_000).execute(
            [app.config["PYTHON_EXECUTABLE"], str(SCRAPER), "--check"],
            cwd=PROJECT_ROOT, timeout_seconds=30, env=_env(),
        )
        output = "环境检查超时" if result.failure_code == "process_timeout" else result.output_tail
        return jsonify({
            "ok": bool(result.ok),
            "platform": "boss",
            "connected": result.ok,
            "returncode": result.returncode if result.returncode is not None else -1,
            "output": output,
        })

    @app.route("/api/env-check")
    def env_check():
        """结构化环境检查：浏览器 / AI / 本地 三组，逐项返回状态。

        检查逻辑与 CLI ``--check`` 共用 ctx.boss.collect_check_items；
        BOSS 登录项优先读激活账号的登录态缓存（D3），未命中才真实探测；
        AI Key 只判配置是否齐全（不验有效性，连通性由前端单独按钮触发）；
        冷却记录随响应返回（D6：面板显示「建议等待至 XX 点」）。
        """
        items, _ = ctx.boss.collect_check_items(cdp_port=ctx.boss.DEFAULT_CDP_PORT)
        by_id = {item["id"]: item for item in items}

        # BOSS 登录状态：激活账号走缓存优先（TTL 15 分钟），
        # 未命中回退 collect_check_items 的真实探测结果。
        account = ctx.account_for_run()
        boss_login = by_id["boss_login"]
        if account:
            from scripts.login_state_cache import read_cached_state
            cached = read_cached_state(account, "boss")
            if cached == "logged_in":
                boss_login = {"id": "boss_login", "name": _MSG_BOSS_LOGIN_STATUS,
                              "status": "ok", "detail": "已登录（缓存）", "fix": None}
            elif cached == "not_logged_in":
                boss_login = {"id": "boss_login", "name": _MSG_BOSS_LOGIN_STATUS,
                              "status": "fail", "detail": "未登录（缓存） — 请打开该账号的 BOSS 窗口登录",
                              "fix": "打开账号浏览器登录"}
            elif cached == "unknown":
                boss_login = {"id": "boss_login", "name": _MSG_BOSS_LOGIN_STATUS,
                              "status": "skip", "detail": "状态未知（缓存） — CDP 不可用，稍后重试", "fix": None}
        browser_items = [by_id["browsers"], by_id["cdp"], boss_login]

        ai_settings = ctx.store.get_ai_settings()
        ai_configured = bool(ai_settings.get("is_configured"))
        ai_items = [{
            "id": "ai_key",
            "name": "AI Key 配置",
            "status": "ok" if ai_configured else "fail",
            "detail": (
                "已配置（模型与端点就绪）"
                if ai_configured else "未配置 — 到「AI 设置」填入 API Key"
            ),
            "fix": None if ai_configured else "打开 AI 设置",
        }]

        data_dir = Path.home() / ".career-scout"
        data_writable = False
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            probe = data_dir / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            data_writable = True
        except OSError:
            data_writable = False
        dist_ok = (FRONTEND_DIST / "index.html").is_file()
        # 运行时模式差异（合同 runtime-mode §2.2）：
        # - EXE 模式 deps 项改「内置运行时」恒 ok / fix=null；
        # - EXE 模式新增 webview2 项（源码模式不存在该项）。
        if ctx.runtime_mode == "exe":
            deps_item = {
                "id": "deps",
                "name": "内置运行时",
                "status": "ok",
                "detail": "Python 运行时与依赖已内置，无需安装",
                "fix": None,
            }
        else:
            deps_item = {
                "id": "deps",
                "name": "Python 依赖",
                "status": by_id["deps"]["status"],
                "detail": by_id["deps"]["detail"],
                "fix": by_id["deps"]["fix"],
            }
        local_items = [
            {
                "id": "data_dir",
                "name": "数据目录可写",
                "status": "ok" if data_writable else "fail",
                "detail": (
                    "~/.career-scout 可写"
                    if data_writable else "~/.career-scout 不可写，请检查用户目录权限"
                ),
                "fix": None if data_writable else "检查用户目录权限",
            },
            {
                "id": "webui_dist",
                "name": "前端构建产物",
                "status": "ok" if dist_ok else "fail",
                "detail": (
                    "webui/dist 存在"
                    if dist_ok else "webui/dist 缺失，请运行 npm run build"
                ),
                "fix": None if dist_ok else "npm run build",
            },
            deps_item,
        ]
        if ctx.runtime_mode == "exe":
            wv2 = desktop_runtime.check_webview2()
            local_items.append({
                "id": "webview2",
                "name": "WebView2 运行时",
                "status": "ok" if wv2["installed"] else "fail",
                "detail": wv2["detail"],
                "fix": None if wv2["installed"] else "安装 WebView2 运行时",
            })

        return jsonify({
            "ok": True,
            "runtime_mode": ctx.runtime_mode,
            "groups": [
                {"id": "browser", "name": "浏览器", "items": browser_items},
                {"id": "ai", "name": "AI", "items": ai_items},
                {"id": "local", "name": "本地环境", "items": local_items},
            ],
            "active_account": account,
            "checked_at": int(time.time()),
        })

    @app.route("/api/profile", methods=["GET", "PUT"])
    def profile():
        if request.method == "GET":
            return jsonify({"profile": normalize_profile(ctx.store.load_profile())})
        normalized = normalize_profile(request.get_json(silent=True) or {})
        ctx.store.save_profile(normalized)
        return jsonify({"profile": normalized})

    @app.route("/api/tasks", methods=["GET", "POST"])
    def tasks():
        if request.method == "GET":
            legacy_platform_guard(request.args.get("platform"))
            limit = min(LIST_LIMIT, max(1, request.args.get("limit", 30, type=int) or 30))
            return jsonify({"tasks": [_tag_boss(t) for t in ctx.store.list_tasks(limit=limit)]})
        raw = request.get_json(silent=True) or {}
        legacy_platform_guard(raw.get("platform"))
        search = validate_search_params(raw)
        profile_raw = raw.get("profile") if "profile" in raw else ctx.store.load_profile()
        normalized_profile = normalize_profile(profile_raw)
        ctx.store.save_profile(normalized_profile)
        task = ctx.runner.create_scrape(search, normalized_profile)
        payload: dict = {"task": _tag_boss(task)}
        return jsonify(payload), 202

    @app.route("/api/scrape", methods=["POST"])
    def legacy_scrape():
        return tasks()

    @app.route("/api/setup-chrome", methods=["POST"])
    def setup_chrome():
        raw = request.get_json(silent=True) or {}
        legacy_platform_guard(raw.get("platform"))
        return jsonify({"task": _tag_boss(ctx.runner.create_setup_chrome())}), 202

    @app.route("/api/tasks/<task_id>")
    def task_detail(task_id):
        legacy_platform_guard(request.args.get("platform"))
        task = ctx.store.get_task(task_id)
        after = request.args.get("after", 0, type=int)
        task["logs"] = ctx.store.get_logs(task_id, after=after)
        return jsonify({"task": _tag_boss(task)})

    @app.route("/api/tasks/<task_id>/cancel", methods=["POST"])
    def cancel_task(task_id):
        raw = request.get_json(silent=True) or {}
        legacy_platform_guard(raw.get("platform"))
        return jsonify({"task": _tag_boss(ctx.runner.cancel(task_id))})

    @app.route("/api/tasks/<task_id>/retry", methods=["POST"])
    def retry_task(task_id):
        raw = request.get_json(silent=True) or {}
        legacy_platform_guard(raw.get("platform"))
        return jsonify({"task": _tag_boss(ctx.runner.retry(task_id))}), 202

    @app.route("/api/tasks/<task_id>/result")
    def task_result(task_id):
        legacy_platform_guard(request.args.get("platform"))
        task, list_payload, jobs, details = _task_payload(ctx.store, task_id)
        ranked = match_jobs(jobs, details, task["params"].get("profile"))
        return jsonify({
            "task_id": task_id,
            "platform": "boss",
            "keyword": list_payload.get("keyword", task["params"].get("search", {}).get("keyword", "")),
            "city": list_payload.get("city", task["params"].get("search", {}).get("city", "")),
            "total": len(ranked),
            "details": len(details),
            "jobs": ranked,
        })

    @app.route("/api/tasks/<task_id>/summary")
    def task_summary(task_id):
        legacy_platform_guard(request.args.get("platform"))
        task, list_payload, jobs, details = _task_payload(ctx.store, task_id)
        search = task["params"].get("search", {})
        summary = job_summary.build_summary(
            jobs,
            details,
            search_keyword=list_payload.get("keyword", search.get("keyword", "")),
            city=list_payload.get("city", search.get("city", "")),
        )
        return jsonify({
            "summary": summary,
            "summary_text": job_summary.format_summary(summary),
            "prompt": job_summary.build_prompt(summary),
        })

    @app.route("/api/tasks/<task_id>/export.csv")
    def export_csv(task_id):
        legacy_platform_guard(request.args.get("platform"))
        task, _, jobs, details = _task_payload(ctx.store, task_id)
        ranked = match_jobs(jobs, details, task["params"].get("profile"))
        columns = [
            "job_id", "eligible", "match_score", "title", "boss_name", "salary",
            "location", "skills", "matched_skills", "missing_skills", "risk_flags", "job_link",
        ]
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()

        def _write_job_row(job):
            row = dict(job)
            for key in ("matched_skills", "missing_skills", "risk_flags"):
                row[key] = " | ".join(row.get(key) or [])
            writer.writerow({key: ctx.boss.csv_safe_cell(row.get(key, "")) for key in columns})

        def _write_section(label, jobs):
            section_row = {column: "" for column in columns}
            section_row["job_id"] = label
            writer.writerow(section_row)
            for job in jobs:
                _write_job_row(job)

        # 结果页同源数据按匹配结果分组：匹配的在前，不匹配的在后
        matched_jobs = [job for job in ranked if job.get("eligible")]
        unmatched_jobs = [job for job in ranked if not job.get("eligible")]
        _write_section("匹配：", matched_jobs)
        _write_section("不匹配：", unmatched_jobs)
        return app.response_class(
            "\ufeff" + buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=boss_jobs_{task_id}.csv"},
        )

    @app.route("/api/results")
    def results():
        legacy_platform_guard(request.args.get("platform"))
        result_dir = Path(app.config["RESULT_DIR"])
        files = sorted(result_dir.glob("boss_jobs_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        return jsonify({"platform": "boss", "files": [path.name for path in files]})
