#!/usr/bin/env python3
"""Local Flask API for persistent BOSS scraping and explainable job ranking."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# 直跑引导（python webui/app.py）：webui 包可导入前需先把项目根放进
# sys.path，PROJECT_ROOT/FRONTEND_DIST 在此就地计算（与 webui.constants 同值，
# 模块消费方一律从 webui.constants 取）。
HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
FRONTEND_DIST = HERE / "dist"


def _resolve_python_executable() -> str:
    """Find the project venv's Python interpreter.

    uv-created venvs report sys.executable as the *global* uv Python
    (e.g. ~/.local/share/uv/python/cpython-3.11-.../python.exe) which
    does NOT carry the venv's site-packages.  Scraper subprocesses spawned
    with that interpreter fail on ``import requests``.  Prefer the venv's
    own python[.exe] sitting next to the project so child processes inherit
    the correct dependency set.
    """
    explicit = os.environ.get("BOSS_PYTHON")
    if explicit:
        return explicit
    # .venv lives at PROJECT_ROOT/.venv
    if os.name == "nt":
        candidate = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = PROJECT_ROOT / ".venv" / "bin" / "python"
    if candidate.is_file():
        return str(candidate)
    return sys.executable
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, jsonify, request, send_from_directory

from scripts import boss_cdp_raw as boss
from scripts import job_summary
from webui import ai as ai_service
from webui import desktop_runtime
from webui import resume as resume_service
# ---------------------------------------------------------------------------
# 兼容 re-export 块（031 B3）：共享常量已迁居 webui/constants.py，函数与
# 服务符号一律从其定义模块（task_runners / task_status / core / workbench）
# 导入。本块仅为存量 patch("webui.app.X") 测试与旧导入路径保活——兼容层勿新增。
# ---------------------------------------------------------------------------
from webui.constants import (
    CLEANUP_EXPIRED_DAYS,
    FEEDBACK_THRESHOLD,
    FRONTEND_DIST,
    LIST_LIMIT,
    LOG_TAIL_LINES,
    PROJECT_ROOT,
    _FEEDBACK_ERROR_STATUS,
    _MSG_ACCOUNT_NOT_FOUND,
    _MSG_BOSS_LOGIN_STATUS,
    _MSG_EXPERIMENT_NOT_FOUND,
    _MSG_MANIFEST_NOT_FOUND,
    _MSG_PROFILE_ID_REQUIRED,
    _MSG_PROFILE_NOT_FOUND,
    _MSG_TASK_ALREADY_RUNNING,
    _MSG_TASK_NOT_FOUND,
    _MSG_UNSUPPORTED_PLATFORM,
    _MSG_USER_FINISHED,
    _MSG_USER_STOPPED_SCRAPE,
    _MSG_USER_STOPPED_SCREEN,
    _OPERATIONAL_ERRORS,
    _ZHILIAN_HOST_TOKEN,
)
from webui.logging_setup import get_logger

_logger = get_logger(__name__)

from webui.core import (
    LegacyPlatformNotSupportedError,
    build_filter_options,
    legacy_platform_guard,
    match_jobs,
    normalize_profile,
    validate_search_params,
)

# Task 008：生命周期/提醒/事件/建议的命令服务与路由注册器（Task 001/005），
# 以及 pipeline 权威岗位身份解析（Task 003）。app.py 只做装配，不复制规则。
from webui.job_feedback import JobFeedbackError, JobFeedbackService
from webui.job_feedback_api import _ERROR_STATUS as _FEEDBACK_ERROR_STATUS
from webui.job_feedback_api import register_job_feedback_routes
from webui.diagnostics import build_diagnostic_payload, record_failure
from webui.pipeline_job_identity import (
    JobIdentityError,
    parse_identity_payload,
    resolve_job_identity,
)
from webui.platforms import UnknownPlatformError
from webui.process_executor import ScraperExecutor
from webui.source import BossCdpSource as _BossCdpSource
from webui.logging_setup import configure_logging
from webui.error_registry import resolve_code
from webui.store import SYSTEMIC_BLOCK_CODES, DiscoveryStoreConflictError, TaskStore
from webui.result_history import ResultHistoryService
from webui.result_history_api import register_result_history_routes
from webui.result_rounds import save_scraped_only_round
from webui.workbench import (
    merge_profile_fields,
    normalize_job_link,
    normalize_job_link_for_platform,
    project_card,
    select_keywords,
)

from webui.task_runners import (  # noqa: F401  兼容 re-export，见上块注释
    DEFAULT_STATE_DIR,
    SCRAPER,
    TaskRunner,
    WorkbenchRunner,
    _classify_scrape_block,
    _env,
    _iso_epoch_ms,
    _mask_key,
    _request_hostname,
    _resume_dropped_from_verdicts,
    _split_resume_verdicts,
    _task_payload,
    _theme_path,
)


from webui.task_status import (  # noqa: F401  兼容 re-export，见上块注释
    _SharedConnectionStore,
    _active_elapsed_ms,
    _feedback_error_response,
    _pipeline_identity_payload,
    _pipeline_kind_for_stage,
    _public_task_status,
    _recrawl_overall_percent,
    _refresh_paused_run_execution_config,
    _resolve_run_scope,
    _screen_overall_percent,
    _weighted_progress_percent,
)


def create_app(config=None):
    # Vue/Vite owns the /static namespace. Disable Flask's implicit static
    # route so hashed production assets are served from webui/dist only.
    app = Flask(__name__, static_folder=None)
    # 关闭 jsonify 默认键排序：6 个筛选码表 dict 本身已是「不限在前、其余从低到高」
    # 的逻辑序，sort_keys=True 会按 Unicode 字母序打乱（实测薪资变 10-20K、20-50K、3-5K…），
    # 前端按 jsonify 返回的顺序渲染 chip 导致乱序。见 spec 007 ⑤。
    app.json.sort_keys = False
    app.config.update(
        RESULT_DIR=str(boss.DEFAULT_RESULT_DIR),
        DB_PATH=str(DEFAULT_STATE_DIR / "webui.db"),
        ADVANCED_SETTINGS_PATH=str(DEFAULT_STATE_DIR / "advanced_settings.json"),
        PYTHON_EXECUTABLE=_resolve_python_executable(),
        START_TASKS=True,
        API_TOKEN=secrets.token_urlsafe(24),
        SESSION_COOKIE_NAME="boss_local_session",
        TRUSTED_HOSTS=["127.0.0.1", "localhost", "::1"],
        RESUME_DIR=str(DEFAULT_STATE_DIR / "resumes"),
        # 上传前由 Flask 直接拦截超大请求体，避免先把文件读入内存再校验。
        MAX_CONTENT_LENGTH=11 * 1024 * 1024,
        # 构建身份拦截默认关闭：防的"旧页面跑新接口"
        # 风险远小于误拦体验；启动时自动重建前端（见下方 sync）+ pre-push
        # 钩子已足够。测试可显式传 REQUIRE_BUILD_IDENTITY=True 验证拦截逻辑。
        REQUIRE_BUILD_IDENTITY=False,
        RUNTIME_MODE="source",
    )
    if config:
        app.config.update(config)
    if app.config.get("TESTING") and "ADVANCED_SETTINGS_PATH" not in (config or {}):
        app.config["ADVANCED_SETTINGS_PATH"] = str(
            Path(app.config["DB_PATH"]).parent / "advanced_settings.json"
        )
    if app.config.get("TESTING") and "START_TASKS" not in (config or {}):
        app.config["START_TASKS"] = False
    if app.config.get("TESTING") and "REQUIRE_BUILD_IDENTITY" not in (config or {}):
        app.config["REQUIRE_BUILD_IDENTITY"] = False

    from webui.pipeline_exec import set_browser_accounts_path
    app.config["BROWSER_ACCOUNTS_PATH"] = str(
        Path(app.config["ADVANCED_SETTINGS_PATH"]).parent / "browser_accounts.json"
    )
    set_browser_accounts_path(app.config["BROWSER_ACCOUNTS_PATH"])

    from scripts.login_state_cache import set_login_state_path as _set_login_state_path
    state_dir = Path(app.config["DB_PATH"]).parent
    app.config["LOGIN_STATE_PATH"] = str(state_dir / "login-state.json")
    _set_login_state_path(app.config["LOGIN_STATE_PATH"])

    store = TaskStore(app.config["DB_PATH"])
    history_service = ResultHistoryService(store)
    if not app.config.get("TESTING"):
        configure_logging()

    def _prune_history_best_effort():
        try:
            history_service.prune_retention()
        except _OPERATIONAL_ERRORS:
            pass  # 保留清理失败不阻断任务主流程

    from webui.tuning import TuningController
    TuningController(store).recover_after_restart()
    store.import_legacy_advanced_settings(app.config["ADVANCED_SETTINGS_PATH"])
    if store.get_advanced_config_state()["last_custom_config"] is None:
        from webui.pipeline_exec import load_advanced_settings
        store.save_custom_config(load_advanced_settings(
            app.config["ADVANCED_SETTINGS_PATH"]
        ))
    scope_previews: dict[str, dict] = {}
    store.cleanup_expired_jobs(days=CLEANUP_EXPIRED_DAYS)
    # Task 008：统一生命周期命令服务；legacy PATCH 与新 API 共用同一入口，
    # 不允许绕过事件、幂等回执和时间校验直接 UPDATE 快照。
    job_feedback_service = JobFeedbackService(store)
    # 运行时模式（合同 runtime-mode §1）：exe → in_process 分派；
    # source → subprocess（零回归）。判定集中在 desktop_runtime 模块。
    _runtime_mode = desktop_runtime.runtime_mode(app.config)
    app.config["RUNTIME_MODE"] = _runtime_mode
    # 源码模式启动时自动同步前端：dist 落后于源码就自动重建，让
    # “改代码 → 重启 → 刷新即最新版”成立，不再依赖 start.bat 入口；
    # EXE 模式前后端同包冻结，无需也不会同步；失败不阻断启动。
    if _runtime_mode == "source" and not app.config.get("TESTING"):
        try:
            from webui import ensure_frontend_sync

            ensure_frontend_sync.main()
        except Exception:
            _logger.warning("前端产物同步失败，界面可能不是最新构建", exc_info=True)

    _execution_mode = "in_process" if _runtime_mode == "exe" else "subprocess"
    runner = TaskRunner(
        store,
        app.config["RESULT_DIR"],
        app.config["PYTHON_EXECUTABLE"],
        start_tasks=app.config["START_TASKS"],
        execution_mode=_execution_mode,
    )
    workbench_runner = WorkbenchRunner(
        store,
        app.config["RESULT_DIR"],
        app.config["PYTHON_EXECUTABLE"],
        start_tasks=app.config["START_TASKS"],
        execution_mode=_execution_mode,
    )

    def _make_cdp_source(*, artifact_root=None, platform="boss",
                         browser_account=None, cdp_port=None,
                         profile_key=None, run_id=""):
        """T403: 从冻结 runtime 创建 source。

        主链调用时传入 platform/cdp_port/profile_key（来自 task dict 冻结
        身份）；调优路径只传 artifact_root（向后兼容，后续 task 统一改造）。
        禁止读取当前 UI、活动账号或默认端口——BOSS 也必须显式接收冻结
        CDP 端口（contracts/job-source.md 第 42 行）。
        """
        artifact = artifact_root or app.config["RESULT_DIR"]
        try:
            if platform == "zhilian":
                from webui.source import ZhilianCdpSource
                if not browser_account or not cdp_port:
                    return None
                return ZhilianCdpSource(
                    browser_account=browser_account,
                    cdp_port=int(cdp_port),
                    profile_key=profile_key,
                    run_id=run_id,
                )
            # BOSS — 显式传入冻结 cdp_port
            # EXE 模式传 in_process=True（合同 inprocess-runner §4.3）；
            # 源码模式保持 in_process=False（子进程路径零改动）。
            return _BossCdpSource(
                python_executable=app.config["PYTHON_EXECUTABLE"],
                artifact_root=artifact,
                cdp_port=int(cdp_port) if cdp_port else boss.DEFAULT_CDP_PORT,
                browser_account=str(browser_account or "").strip() or None,
                in_process=(_runtime_mode == "exe"),
                run_id=run_id,
            )
        except Exception:
            return None

    Path(app.config["RESUME_DIR"]).mkdir(parents=True, exist_ok=True)
    app.config["TASK_STORE"] = store
    app.config["TASK_RUNNER"] = runner
    app.config["WORKBENCH_RUNNER"] = workbench_runner
    app.config["MAKE_CDP_SOURCE"] = _make_cdp_source

    def _load_legacy_advanced_settings():
        from webui.pipeline_exec import load_advanced_settings
        return load_advanced_settings(app.config["ADVANCED_SETTINGS_PATH"])

    def _save_legacy_advanced_settings(settings):
        from webui.pipeline_exec import save_advanced_settings
        save_advanced_settings(settings, app.config["ADVANCED_SETTINGS_PATH"])

    @app.before_request
    def protect_local_api():
        trusted_hosts = set(app.config["TRUSTED_HOSTS"])
        if _request_hostname(request.host) not in trusted_hosts:
            return jsonify({"error": "拒绝不受信任的 Host"}), 403
        # 读取本地任务、画像、收藏、下载状态等 GET 也
        # 需要会话令牌；仅平台/版本/环境探测等公开端点保持匿名可读。
        path = request.path
        sensitive_get = (
            path.startswith("/api/resumes")
            or path.startswith("/api/ai-settings")
            or path.startswith("/api/advanced-settings")
            or path.startswith("/api/tuning/")
            or path.startswith("/api/profile")
            or path.startswith("/api/favorites")
            or path.startswith("/api/tasks")
            or path.startswith("/api/results")
            or path.startswith("/api/search-runs")
            or path.startswith("/api/profile-jobs")
            or path.startswith("/api/cleanup-preview")
            or path.startswith("/api/browser-accounts")
            or path.startswith("/api/search-progress")
            or path.startswith("/api/latest-running-task")
            or path.startswith("/api/latest-pipeline-result")
            or path.startswith("/api/pipeline-result/export.csv")
            or path.startswith("/api/update-status")
            or path.startswith("/api/runs/")
            or path.startswith("/api/task-state/")
            or path.startswith("/api/check")
            or path.startswith("/api/env-check")
            or path.startswith("/api/job-reminders")
            or path.startswith("/api/result-history")
            or path.startswith("/api/logs")
        )
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} or (request.method == "GET" and sensitive_get):
            origin = request.headers.get("Origin")
            if origin and (urlparse(origin).hostname or "").lower() not in trusted_hosts:
                return jsonify({"error": "拒绝跨站请求"}), 403
            if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
                return jsonify({"error": "拒绝跨站请求"}), 403
            supplied = (
                request.headers.get("X-Boss-Token", "")
                or request.cookies.get(app.config["SESSION_COOKIE_NAME"], "")
            )
            if not secrets.compare_digest(supplied, app.config["API_TOKEN"]):
                return jsonify({"error": "缺少有效的本地会话令牌"}), 403
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and app.config.get(
                "REQUIRE_BUILD_IDENTITY"
            ):
                supplied_build = request.headers.get("X-Boss-Build", "")
                if not supplied_build:
                    return jsonify({
                        "error": "build_identity_required",
                        "error_code": "build_identity_required",
                        "user_message": "页面尚未取得当前后端版本，请刷新页面后重试",
                        "current_build_hash": _build_hash,
                    }), 409
                if not secrets.compare_digest(supplied_build, _build_hash):
                    return jsonify({
                        "error": "build_identity_mismatch",
                        "error_code": "build_identity_mismatch",
                        "user_message": "页面版本与当前后端不一致，请刷新页面后重试",
                        "current_build_hash": _build_hash,
                    }), 409

    @app.errorhandler(ValueError)
    def handle_value_error(error):
        # T603: legacy 平台守卫异常优先映射，不被通用 invalid_request 吞掉。
        if isinstance(error, LegacyPlatformNotSupportedError):
            return jsonify({
                "error_code": "legacy_platform_not_supported",
                "user_message": str(error),
            }), 422
        if isinstance(error, UnknownPlatformError):
            return jsonify({
                "error_code": "platform_validation_failed",
                "user_message": _MSG_UNSUPPORTED_PLATFORM,
            }), 400
        if str(error).startswith("city_mapping_missing"):
            return jsonify({
                "error_code": "city_mapping_missing",
                "user_message": (
                    str(error).split(":", 1)[-1].strip()
                    + "，请检查城市名称或选择相近城市"
                ),
            }), 422
        return jsonify({
            "error_code": "invalid_request",
            "user_message": str(error),
        }), 400


    @app.errorhandler(KeyError)
    def handle_key_error(error):
        return jsonify({
            "error_code": "not_found",
            "user_message": _MSG_TASK_NOT_FOUND,
        }), 404





















    # == US1: AI settings, profiles, resumes =============================









    # == US2: search runs ================================================





    # == US3: feedback ===================================================



    # == US4: history, favorites, cleanup ================================




    # ------------------------------------------------------------------
    # Three-stage pipeline: resume → AI fields → user confirm → script
    # ------------------------------------------------------------------

    # Stage-3 execution: in-memory progress tracker + single-worker executor.
    # Each run is keyed by a task_id; progress snapshots and final results are
    # stored here and polled by the frontend.
    from webui.app_support import build_app_support
    ctx = build_app_support(
        app, store, runner, workbench_runner,
        job_feedback_service, history_service, resume_service,
        _prune_history_best_effort, _load_legacy_advanced_settings,
        _save_legacy_advanced_settings, _make_cdp_source,
        scope_previews, _runtime_mode)
    app.config["PIPELINE_CONTEXT"] = ctx
    from webui.settings_api import register_settings_routes
    register_settings_routes(app, ctx)
    from webui.pipeline_jobs_api import register_pipeline_jobs_routes
    register_pipeline_jobs_routes(app, ctx)
    from webui.results_api import register_results_routes
    register_results_routes(app, ctx)
    from webui.running_task_api import register_running_task_routes
    register_running_task_routes(app, ctx)
    from webui.resume_fields_api import register_resume_fields_routes
    register_resume_fields_routes(app, ctx)
    from webui.exec_search_api import register_exec_search_routes
    register_exec_search_routes(app, ctx)
    from webui.ai_screen_api import register_ai_screen_routes
    register_ai_screen_routes(app, ctx)
    from webui.core_api import register_core_routes
    register_core_routes(app, ctx)
    from webui.profiles_api import register_profiles_routes
    register_profiles_routes(app, ctx)
    from webui.task_state_api import register_task_state_routes
    register_task_state_routes(app, ctx)
    from webui.task_continue_api import register_task_continue_routes
    register_task_continue_routes(app, ctx)
    from webui.log_api import register_log_routes
    register_log_routes(app, ctx)
    from webui.browser_registry_api import register_browser_registry_routes
    register_browser_registry_routes(app, ctx)

    def _run_pipeline_task(
        task_id, script_params, execution_config=None, frozen_scope=None,
    ):
        from webui.runners.pipeline_task import run_pipeline_task
        return run_pipeline_task(
            ctx, task_id, script_params, execution_config, frozen_scope)

    ctx.run_pipeline_task = _run_pipeline_task  # 021 B6：定义晚于组装点，原地补绑定

    def _jd_checkpoint_path(result_dir, run_id):
        return os.path.join(result_dir, f"ai_screen_jd_{run_id}.json")

    def _load_jd_checkpoint(path):
        """读取 JD 断点文件；缺失表示尚无进度，损坏则阻断续跑。"""
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("jd_checkpoint_unavailable") from exc
        if not isinstance(data, dict):
            raise RuntimeError("jd_checkpoint_invalid")
        return {str(k): str(v) for k, v in data.items()
                if isinstance(v, str) and v.strip()}


    def _save_jd_checkpoint(path, jd_map):
        """原子写 JD 断点文件（每批抓完落盘，进程崩了已抓的也不丢）。"""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(jd_map, handle, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass  # best-effort 清理失败的临时文件，不改变主错误。
            raise RuntimeError("jd_checkpoint_write_failed") from exc

    def _remove_jd_checkpoint(path):
        """断点文件完成使命后删除（任务成功/被续跑接管），best-effort。"""
        try:
            os.unlink(path)
        except OSError:
            pass

    # 021 B6：定义晚于组装点，原地补绑定（recrawl_job_key 先例）
    ctx.jd_checkpoint_path = _jd_checkpoint_path
    ctx.load_jd_checkpoint = _load_jd_checkpoint
    ctx.save_jd_checkpoint = _save_jd_checkpoint
    ctx.remove_jd_checkpoint = _remove_jd_checkpoint


    def _run_ai_screen_task(task_id, screening_fields, profile_summary,
                            scrape_task_id, resume_from_run_id="",
                            profile_facts=None, execution_config=None,
                            cross_platform_dedupe=True):
        from webui.runners.ai_screen_task import run_ai_screen_task
        return run_ai_screen_task(
            ctx, task_id, screening_fields, profile_summary,
            scrape_task_id, resume_from_run_id, profile_facts,
            execution_config, cross_platform_dedupe)

    ctx.run_ai_screen_task = _run_ai_screen_task  # 021 B6：定义晚于组装点，原地补绑定































    # ------------------------------------------------------------------
    # Pipeline 结果增强：按需抓 JD 详情 + 感兴趣/不感兴趣（接入筛选工作台）
    # ------------------------------------------------------------------

    _job_detail_lock = threading.Lock()
    ctx.job_detail_lock = _job_detail_lock  # 021 B6：定义晚于组装点，原地补绑定









    def _recrawl_job_key(job):
        """Return the same stable key used by the result-page recrawl request."""
        if not isinstance(job, dict):
            return ""
        return str(
            job.get("platform_job_id")
            or job.get("job_id")
            or job.get("id")
            or job.get("canonical_url")
            or job.get("source_url")
            or job.get("job_link")
            or ""
        ).strip()

    ctx.recrawl_job_key = _recrawl_job_key  # 021 B4：定义晚于组装点，原地补绑定



    def _run_recrawl_task(task_id, job_ids, profile_summary, source_run_id="",
                          completed_job_ids=None, profile_facts=None,
                          execution_config=None):
        from webui.runners.recrawl_task import run_recrawl_task

        return run_recrawl_task(
            ctx, task_id, job_ids, profile_summary, source_run_id,
            completed_job_ids, profile_facts, execution_config,
        )

    ctx.run_recrawl_task = _run_recrawl_task  # 021 B6：定义晚于组装点，原地补绑定


    # ===================================================================
    # 010 healthy-pipeline-recovery: 统一接口层（FR-005/FR-020/FR-022/
    # FR-024/FR-037/FR-039/FR-041）
    # ===================================================================

    import hashlib as _hashlib_mod

    # FR-039：后端版本标识（启动时计算，继续任务时校验）
    try:
        _backend_version = "011-ui-fixes"
        _backend_files = sorted(
            [*Path(__file__).resolve().parent.glob("*.py"), SCRAPER.resolve(),
             (PROJECT_ROOT / "scripts" / "zhilian_cdp_raw.py").resolve()],
            key=lambda path: path.relative_to(PROJECT_ROOT).as_posix(),
        )
        _build_digest = _hashlib_mod.sha256()
        for _backend_file in _backend_files:
            _relative_name = _backend_file.relative_to(PROJECT_ROOT).as_posix()
            _build_digest.update(_relative_name.encode("utf-8"))
            _build_digest.update(b"\0")
            _build_digest.update(_backend_file.read_bytes())
            _build_digest.update(b"\0")
        _build_hash = _build_digest.hexdigest()[:12]
        _build_time = datetime.fromtimestamp(
            max(path.stat().st_mtime for path in _backend_files)
        ).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError):
        _backend_version = "unknown"
        _build_hash = "unknown"
        _build_time = "unknown"

    ctx.backend_version = _backend_version  # 021 B5：定义晚于组装点，原地补绑定
    ctx.build_hash = _build_hash
    ctx.build_time = _build_time

    from webui.version_update_api import register_version_update_routes
    register_version_update_routes(app, ctx)

    # ------------------------------------------------------------------
    # 应用内更新（webui/updater.py；quitAndInstall 模式）
    # ------------------------------------------------------------------
    from webui import updater as _updater_mod

    def _read_product_version():
        """从 pyproject.toml 读产品版本（frozen 时资源根含该文件）。"""
        try:
            text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
            m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
            if m:
                return m.group(1)
        except OSError:
            pass
        return "0.0.0"

    _product_version = _read_product_version()
    if not app.config.get("TESTING"):
        _updater_mod.clean_download_dir(DEFAULT_STATE_DIR)
    app.config["UPDATER"] = _updater_mod.UpdateDownloader(DEFAULT_STATE_DIR)

    ctx.product_version = _product_version  # 021 B6：定义晚于组装点，原地补绑定

    def _update_env_payload(info_dict):
        """统一附加运行时环境信息：exe 模式才可应用内安装。"""
        install_target = _updater_mod.current_install_target()
        info_dict["runtime_mode"] = _runtime_mode
        info_dict["installable"] = bool(
            _runtime_mode == "exe" and install_target is not None
        )
        return info_dict

    ctx.update_env_payload = _update_env_payload  # 021 B6：定义晚于组装点，原地补绑定
















    # 注：/api/latest-running-task 的 paused 恢复逻辑已合并到上面的
    # latest_running_task() 中（查找顺序：内存 running → DB paused → DB interrupted）。
    # 此处原 api_latest_running_task_extended 已删除，避免重复路由注册。

    # ==================================================================
    # SPEC011 T023: 控制者 manifest/decision 路由与执行者路由
    # 对应 contracts/http-api.md 第 4-6 节
    # ==================================================================

    # ------------------------------------------------------------------
    # SPEC011 T030: 实验生命周期路由
    # 对应 contracts/http-api.md 第 3 节
    # ------------------------------------------------------------------

    from webui.tuning_api import register_tuning_routes
    register_tuning_routes(app, ctx)














    # Task 008：注册 lifecycle/state/events/reminders/advice 路由（Task 005）。
    # before_request 的 Host/会话令牌/build identity 防护自动覆盖这些路由；
    # 提醒/生命周期规则平台无关，无 platform 过滤。
    register_job_feedback_routes(app, ctx)

    register_result_history_routes(app, store)

    from webui.location_api import register_location_routes
    register_location_routes(app, ctx)

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, threaded=True)
