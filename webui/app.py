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
import sqlite3
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
FRONTEND_DIST = HERE / "dist"


_ZHILIAN_HOST_TOKEN = "zhaopin.com"


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
from webui.constants import (
    CLEANUP_EXPIRED_DAYS,
    FEEDBACK_THRESHOLD,
    LIST_LIMIT,
    LOG_TAIL_LINES,
)
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
from webui.store import SYSTEMIC_BLOCK_CODES, DiscoveryStoreConflictError, TaskStore
from webui.result_history import ResultHistoryService
from webui.result_history_api import register_result_history_routes
from webui.scrape_only import save_scrape_snapshot, save_screen_result
from webui.workbench import (
    merge_profile_fields,
    normalize_job_link,
    normalize_job_link_for_platform,
    project_card,
    select_keywords,
)

_MSG_USER_FINISHED = "用户已结束任务"
_MSG_UNSUPPORTED_PLATFORM = "不支持的招聘平台"
_MSG_BOSS_LOGIN_STATUS = "BOSS 登录状态"
_MSG_TASK_NOT_FOUND = "任务不存在"
_MSG_TASK_ALREADY_RUNNING = "该任务正在继续，请勿重复点击"
_MSG_ACCOUNT_NOT_FOUND = "账号不存在"
_MSG_EXPERIMENT_NOT_FOUND = "实验不存在"
_MSG_MANIFEST_NOT_FOUND = "任务单不存在"
_MSG_USER_STOPPED_SCRAPE = "用户已停止抓取"
_MSG_USER_STOPPED_SCREEN = "用户已停止筛选"
_MSG_PROFILE_NOT_FOUND = "画像不存在"
_MSG_PROFILE_ID_REQUIRED = "profile_id 不能为空"


_OPERATIONAL_ERRORS = (
    OSError,
    sqlite3.Error,
    RuntimeError,
    ValueError,
    KeyError,
    TypeError,
    ai_service.AISecurityError,
)


from webui.task_runners import (
    _FINE_VERDICTS,
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


def _run_to_task_status(db_status: str) -> str:
    """DB 状态 → 统一任务状态名（FR-005）。"""
    mapping = {
        "queued": "waiting",
        "running": "running",
        "paused": "paused",
        "succeeded": "completed",
        "partial": "completed_with_pending",
        "failed": "failed",
        "interrupted": "cancelled",
    }
    return mapping.get(db_status, "failed")

def _public_task_status(status: str, interruption_kind: str | None = None) -> str:
    """Canonical DB/内存状态 → 公共 API 状态（http-api.md 公共状态映射）。"""
    mapping = {
        "queued": "queued",
        "waiting": "queued",
        "running": "running",
        "paused": "paused",
        "succeeded": "completed",
        "partial": "completed_with_pending",
        "failed": "failed",
        "done": "completed",
        "interrupted": "cancelled",
        "cancelled": "cancelled",
    }
    if status == "interrupted" and interruption_kind in (
            "process_restart", "operator_stop"):
        return "interrupted"
    return mapping.get(status, status or "failed")


def _pipeline_kind_for_stage(stage: str) -> str:
    """把 screening run 阶段映射为对外 pipeline kind。"""
    if str(stage).startswith("recrawl_"):
        return "recrawl"
    if str(stage) == "scrape":
        return "scrape"
    return "ai_screen"

# ---------------------------------------------------------------------------
# Task 008 集成胶合：legacy PATCH 原子性与 pipeline 身份映射。
# 不复制任何 action/时间/提醒规则，规则全部由 JobFeedbackService 与
# pipeline_job_identity 拥有。
# ---------------------------------------------------------------------------

class _SharedConnectionStore:
    """让命令服务复用调用方持有的 SQLite 连接（仅限 legacy 混合 PATCH）。

    JobFeedbackService 只依赖 ``store._connection()`` 与
    ``store.upsert_job_with_connection``；本代理把 ``_connection()`` 重定向到
    外部连接，使 note 与生命周期写入能在同一事务中提交或回滚。
    其余属性全部透传真实 store。
    """

    def __init__(self, store, conn):
        self._store = store
        self._conn = conn

    def _connection(self):
        shared = self._conn

        @contextmanager
        def _shared_connection():
            yield shared

        return _shared_connection()

    def __getattr__(self, name):
        return getattr(self._store, name)


def _feedback_error_response(code, user_message, details=None, status=None):
    """稳定错误体（contracts/http-api.md），不泄露 SQL/路径。"""
    if status is None:
        status = _FEEDBACK_ERROR_STATUS.get(code, 500)
    return jsonify({
        "ok": False,
        "error_code": code,
        "user_message": user_message,
        "details": details or {},
    }), status


def _pipeline_identity_payload(job: dict) -> dict:
    """把 legacy pipeline 岗位载荷映射为权威身份请求。

    三元组必须原样来自载荷（job_link/source_url 只作为规范链接候选）；
    只有三元组不完整时才把 job_id 当内部 ID 候选。绝不用裸 platform_job_id
    反查内部岗位，也不从 URL/界面猜平台。
    """
    payload = {
        "platform": job.get("platform"),
        "platform_job_id": job.get("platform_job_id"),
        "canonical_url": str(
            job.get("canonical_url")
            or job.get("job_link")
            or job.get("source_url")
            or ""
        ),
        "title": job.get("title") or "",
        "company": job.get("company") or job.get("boss_name") or "",
        "salary": job.get("salary") or "",
        "location": job.get("location") or "",
        "jd": job.get("jd") or "",
        "experience": job.get("experience") or "",
        "degree": job.get("degree") or "",
    }
    triple_complete = all(
        payload[key] not in (None, "")
        for key in ("platform", "platform_job_id", "canonical_url")
    )
    if not triple_complete:
        payload["job_id"] = job.get("stored_job_id") or job.get("job_id")
    return payload

_SCREEN_STAGE_WEIGHTS: dict[str, tuple[int, int]] = {
    "resume": (0, 0),
    "screen_a": (0, 25),
    "ai_rough": (0, 25),
    "screen_a_done": (25, 25),
    "ensure_chrome": (25, 25),
    "fetch_jd": (25, 75),
    "jd_detail": (25, 75),
    "screen_b": (75, 100),
    "ai_fine": (75, 100),
    "done": (100, 100),
}

_RECRAWL_STAGE_WEIGHTS: dict[str, tuple[int, int]] = {
    "fetch_jd": (0, 60),
    "recrawl_fetch_jd": (0, 60),
    "recrawl_jd": (0, 60),
    "screen_b": (60, 100),
    "recrawl_ai": (60, 100),
    "done": (100, 100),
}


def _weighted_progress_percent(
    weights: dict[str, tuple[int, int]], stage: str, current: int, total: int,
) -> int:
    """按阶段权重与真实完成量插值，向下取整且不越过阶段终点。"""
    start, end = weights.get(stage, (0, 100))
    if total <= 0:
        return start
    ratio = min(1.0, max(0.0, current / total))
    return min(end, int(start + (end - start) * ratio))


def _screen_overall_percent(stage: str, current: int, total: int) -> int:
    """把 AI 筛选 pipeline 的当前阶段映射到整体百分比（0-100）。

    权重固定为初筛 25%、抓 JD 50%、精筛 25%；只按真实完成条数插值，
    不引入时间或阶段预估爬升。"""
    return _weighted_progress_percent(_SCREEN_STAGE_WEIGHTS, stage, current, total)


def _recrawl_overall_percent(stage: str, current: int, total: int) -> int:
    """重抓只有 JD 补抓与 AI 重判两段：0-60 与 60-100。"""
    return _weighted_progress_percent(_RECRAWL_STAGE_WEIGHTS, stage, current, total)


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
        # 构建身份拦截默认关闭：本地单机工具，防的“旧页面跑新接口”
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
    from webui.cooldown import set_cooldown_path as _set_cooldown_path
    state_dir = Path(app.config["DB_PATH"]).parent
    app.config["LOGIN_STATE_PATH"] = str(state_dir / "login-state.json")
    app.config["COOLDOWN_PATH"] = str(state_dir / "cooldown.json")
    _set_login_state_path(app.config["LOGIN_STATE_PATH"])
    _set_cooldown_path(app.config["COOLDOWN_PATH"])

    store = TaskStore(app.config["DB_PATH"])
    history_service = ResultHistoryService(store)
    if not app.config.get("TESTING"):
        configure_logging()

    def _prune_history_best_effort():
        try:
            history_service.prune_retention()
        except _OPERATIONAL_ERRORS:
            pass  # 保留清理失败不阻断任务主流程

    def _save_cancelled_history_snapshot(run, task):
        """取消结束但已有岗位时，写终态前保存本轮快照（FR-019）。"""
        try:
            if run is None:
                return None
            run_id = str(run["id"])
            if run.get("record_kind") == "result_snapshot" or store.history_round_exists(run_id):
                return None
            if any(event["type"] == "history_snapshot"
                   for event in store.list_task_events(run_id)):
                return None
            params = dict(run.get("execution_params") or {})
            platform = str(
                params.get("platform") or run.get("platform")
                or (task or {}).get("platform") or ""
            )
            if not platform:
                return None
            in_memory = (task or {}).get("result") if isinstance(task, dict) else None
            source_jobs = []
            source_dropped = []
            total_scraped = None
            scrape_task_id = str(params.get("scrape_task_id") or "")
            source_run_id = str(params.get("source_run_id") or "")
            if isinstance(in_memory, dict) and (
                in_memory.get("jobs") or in_memory.get("dropped")
            ):
                source_jobs = in_memory.get("jobs") or []
                source_dropped = in_memory.get("dropped") or []
                total_scraped = in_memory.get("total_scraped")
            elif scrape_task_id:
                source_jobs = store.load_scrape_run_jobs(scrape_task_id)
            elif source_run_id:
                payload = store.load_latest_pipeline_result(source_run_id)
                source_jobs = ((payload or {}).get("result") or {}).get("jobs") or []
                source_dropped = ((payload or {}).get("result") or {}).get("dropped") or []
                total_scraped = ((payload or {}).get("result") or {}).get("total_scraped")
            elif str(run.get("current_stage") or "") == "scrape":
                scrape_task_id = run_id
                source_jobs = store.load_scrape_run_jobs(run_id)
            if not source_jobs and not source_dropped:
                return None
            verdicts = store.load_screening_verdicts(run_id)
            pending_rows = store.load_screening_pending(run_id)
            if not verdicts and not pending_rows and scrape_task_id and scrape_task_id != run_id:
                verdicts = store.load_screening_verdicts(scrape_task_id)
                pending_rows = store.load_screening_pending(scrape_task_id)
            jd_map = {}
            try:
                jd_map = _load_jd_checkpoint(
                    _jd_checkpoint_path(app.config["RESULT_DIR"], run_id))
            except RuntimeError:
                jd_map = {}
            profile_summary = str(
                params.get("profile_summary") or run.get("profile_summary") or ""
            )
            profile_facts = params.get("profile_facts") or None
            result = _build_partial_pipeline_result(
                source_jobs, verdicts, pending_rows, jd_map,
                profile_summary,
                source_dropped=source_dropped,
                total_scraped=total_scraped,
                platform=platform,
                profile_facts=profile_facts,
            )
            if not result.get("jobs") and not result.get("dropped"):
                return None
            snapshot_id = store.save_pipeline_result(
                result,
                {"screening": run.get("frozen_filters") or {}, "platform": platform},
                started_at=run.get("started_at") or (task or {}).get("started_at"),
                finished_at=int(time.time() * 1000),
                execution_config=params.get("execution_config") or {},
                status="cancelled",
                execution_params={
                    "platform": platform,
                    "scrape_task_id": (
                        scrape_task_id
                        or (run_id if str(run.get("current_stage") or "") == "scrape" else "")
                    ),
                },
            )
            store.append_task_event(run_id, "history_snapshot", {
                "snapshot_run_id": snapshot_id,
                "status": "cancelled",
                "jobs": len(result.get("jobs") or []),
                "dropped": len(result.get("dropped") or []),
            })
            _prune_history_best_effort()
            return snapshot_id
        except Exception:
            return None  # 快照兜底失败不阻断取消

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
            pass
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
        # T010: resume reads and AI settings reads also require the session
        # token — they expose private user data even though they are GET.
        path = request.path
        sensitive_get = (
            path.startswith("/api/resumes")
            or path.startswith("/api/ai-settings")
            or path.startswith("/api/advanced-settings")
            or path.startswith("/api/tuning/")
            or path.startswith("/api/profiles/") and "/resumes" in path
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

    def _tag_boss(obj):
        """T604: legacy 成功响应标识 platform=boss（仅响应层，不持久化）。

        合同第 370 行：所有 legacy 成功响应中的任务/run/岗位/结果对象补充
        ``platform=boss``；该标识不把这些链路升级成多平台主链。返回浅拷贝，
        避免污染 store 内部对象。
        """
        if isinstance(obj, dict):
            return {**obj, "platform": "boss"}
        return obj

    @app.errorhandler(KeyError)
    def handle_key_error(error):
        return jsonify({
            "error_code": "not_found",
            "user_message": _MSG_TASK_NOT_FOUND,
        }), 404

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
            cities = [{"label": name, "value": name} for name in boss.CITY_MAP]
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
        rows = store.list_all_interested()
        items = []
        for pj in rows:
            try:
                job = store.get_job(pj["job_id"])
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
                "salary": ("薪资范围", [], boss.SALARY_MAP),
                "experience": ("经验要求", [], boss.EXPERIENCE_MAP),
                "degree": ("学历", [], boss.DEGREE_MAP),
                "industry": ("行业", [], boss.INDUSTRY_MAP),
                "scale": ("公司规模", [], boss.SCALE_MAP),
                "stage": ("融资阶段", [], boss.STAGE_MAP),
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
            {"token": app.config["API_TOKEN"], "build_hash": _build_hash,
             "version": _product_version, "runtime_mode": _runtime_mode}
            if app.config.get("TESTING") else {
                "status": "ok", "build_hash": _build_hash,
                "version": _product_version, "runtime_mode": _runtime_mode,
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
            account = _account_for_run()
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
        if _runtime_mode == "exe":
            # 合同 inprocess-runner §6：EXE 模式不 spawn 子进程，复用
            # boss.collect_check_items 库式路径；返回结构与源码模式一致。
            items, all_pass = boss.collect_check_items(cdp_port=boss.DEFAULT_CDP_PORT)
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
        result = ScraperExecutor(max_output_bytes=64_000).execute(
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

        检查逻辑与 CLI ``--check`` 共用 boss.collect_check_items；
        BOSS 登录项优先读激活账号的登录态缓存（D3），未命中才真实探测；
        AI Key 只判配置是否齐全（不验有效性，连通性由前端单独按钮触发）；
        冷却记录随响应返回（D6：面板显示「建议等待至 XX 点」）。
        """
        items, _ = boss.collect_check_items(cdp_port=boss.DEFAULT_CDP_PORT)
        by_id = {item["id"]: item for item in items}

        # BOSS 登录状态：激活账号走缓存优先（TTL 15 分钟），
        # 未命中回退 collect_check_items 的真实探测结果。
        account = _account_for_run()
        boss_login = by_id["boss_login"]
        if account:
            from scripts.login_state_cache import read_cached_state
            cached = read_cached_state(account, "boss")
            if cached == "logged_in":
                boss_login = {"id": "boss_login", "name": _MSG_BOSS_LOGIN_STATUS,
                              "status": "ok", "detail": "已登录（缓存）", "fix": None}
            elif cached == "restricted":
                boss_login = {"id": "boss_login", "name": _MSG_BOSS_LOGIN_STATUS,
                              "status": "fail", "detail": _restricted_cache_detail(account),
                              "fix": None}
            elif cached == "not_logged_in":
                boss_login = {"id": "boss_login", "name": _MSG_BOSS_LOGIN_STATUS,
                              "status": "fail", "detail": "未登录（缓存） — 请打开该账号的 BOSS 窗口登录",
                              "fix": "打开账号浏览器登录"}
            elif cached == "unknown":
                boss_login = {"id": "boss_login", "name": _MSG_BOSS_LOGIN_STATUS,
                              "status": "skip", "detail": "状态未知（缓存） — CDP 不可用，稍后重试", "fix": None}
        browser_items = [by_id["browsers"], by_id["cdp"], boss_login]

        ai_settings = store.get_ai_settings()
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
        if _runtime_mode == "exe":
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
        if _runtime_mode == "exe":
            wv2 = desktop_runtime.check_webview2()
            local_items.append({
                "id": "webview2",
                "name": "WebView2 运行时",
                "status": "ok" if wv2["installed"] else "fail",
                "detail": wv2["detail"],
                "fix": None if wv2["installed"] else "安装 WebView2 运行时",
            })

        cooldowns = []
        from webui.cooldown import all_cooldowns
        for aid, platforms in all_cooldowns().items():
            for pid, rec in platforms.items():
                cooldowns.append({
                    "account_id": aid,
                    "platform": pid,
                    "until": rec["until"],
                    "until_text": _format_unlock_time(rec["until"]),
                    "reason": rec.get("reason") or "",
                    "from_run": rec.get("from_run") or "",
                })
        return jsonify({
            "ok": True,
            "runtime_mode": _runtime_mode,
            "groups": [
                {"id": "browser", "name": "浏览器", "items": browser_items},
                {"id": "ai", "name": "AI", "items": ai_items},
                {"id": "local", "name": "本地环境", "items": local_items},
            ],
            "active_account": account,
            "cooldowns": cooldowns,
            "checked_at": int(time.time()),
        })

    @app.route("/api/profile", methods=["GET", "PUT"])
    def profile():
        if request.method == "GET":
            return jsonify({"profile": normalize_profile(store.load_profile())})
        normalized = normalize_profile(request.get_json(silent=True) or {})
        store.save_profile(normalized)
        return jsonify({"profile": normalized})

    @app.route("/api/tasks", methods=["GET", "POST"])
    def tasks():
        if request.method == "GET":
            legacy_platform_guard(request.args.get("platform"))
            limit = min(LIST_LIMIT, max(1, request.args.get("limit", 30, type=int) or 30))
            return jsonify({"tasks": [_tag_boss(t) for t in store.list_tasks(limit=limit)]})
        raw = request.get_json(silent=True) or {}
        legacy_platform_guard(raw.get("platform"))
        error_resp, error_status, warning = _submit_cooldown_guard(raw)
        if error_resp is not None:
            return error_resp, error_status
        search = validate_search_params(raw)
        profile_raw = raw.get("profile") if "profile" in raw else store.load_profile()
        normalized_profile = normalize_profile(profile_raw)
        store.save_profile(normalized_profile)
        task = runner.create_scrape(search, normalized_profile)
        payload: dict = {"task": _tag_boss(task)}
        if warning is not None:
            payload["warning"] = warning
        return jsonify(payload), 202

    @app.route("/api/scrape", methods=["POST"])
    def legacy_scrape():
        return tasks()

    @app.route("/api/setup-chrome", methods=["POST"])
    def setup_chrome():
        raw = request.get_json(silent=True) or {}
        legacy_platform_guard(raw.get("platform"))
        return jsonify({"task": _tag_boss(runner.create_setup_chrome())}), 202

    @app.route("/api/tasks/<task_id>")
    def task_detail(task_id):
        legacy_platform_guard(request.args.get("platform"))
        task = store.get_task(task_id)
        after = request.args.get("after", 0, type=int)
        task["logs"] = store.get_logs(task_id, after=after)
        return jsonify({"task": _tag_boss(task)})

    @app.route("/api/tasks/<task_id>/cancel", methods=["POST"])
    def cancel_task(task_id):
        raw = request.get_json(silent=True) or {}
        legacy_platform_guard(raw.get("platform"))
        return jsonify({"task": _tag_boss(runner.cancel(task_id))})

    @app.route("/api/tasks/<task_id>/retry", methods=["POST"])
    def retry_task(task_id):
        raw = request.get_json(silent=True) or {}
        legacy_platform_guard(raw.get("platform"))
        return jsonify({"task": _tag_boss(runner.retry(task_id))}), 202

    @app.route("/api/tasks/<task_id>/result")
    def task_result(task_id):
        legacy_platform_guard(request.args.get("platform"))
        task, list_payload, jobs, details = _task_payload(store, task_id)
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
        task, list_payload, jobs, details = _task_payload(store, task_id)
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
        task, _, jobs, details = _task_payload(store, task_id)
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
            writer.writerow(row)

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

    # == US1: AI settings, profiles, resumes =============================

    def _resolve_credentials_from_request():
        """从请求 body 读取 endpoint_url/api_key/model，缺的用已保存设置兜底。

        供 /api/ai-settings/test 和 /api/ai-settings/models 使用：让用户
        不点"保存设置"也能用当前对话框里填的值直接测试/拉取。返回
        (endpoint_url, api_key, model)；endpoint_url 或 api_key 既不在请求
        里也没有已保存值时抛 ValueError。
        """
        raw = request.get_json(silent=True) or {}
        endpoint_url = str(raw.get("endpoint_url") or "").strip()
        api_key = str(raw.get("api_key") or "").strip()
        model = str(raw.get("model") or "").strip()

        settings = store.get_ai_settings()
        if not endpoint_url:
            endpoint_url = settings.get("endpoint_url") or ""
        if not api_key:
            cred_ref = store.get_credential_ref()
            api_key = ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
        if not model:
            model = settings.get("model", "")

        if not endpoint_url:
            raise ValueError("请先填写 AI 服务 URL")
        if not api_key:
            raise ValueError("API Key 未配置")
        return endpoint_url, api_key, model

    @app.route("/api/ai-settings", methods=["GET", "PUT"])
    def ai_settings():
        if request.method == "GET":
            settings = store.get_ai_settings()
            # 返回打码后的 key 预览（保留首尾，中间星号），供前端展示
            cred_ref = store.get_credential_ref() if settings.get("is_configured") else ""
            real_key = ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
            settings["masked_key"] = _mask_key(real_key) if real_key else ""
            return jsonify(settings)
        raw = request.get_json(silent=True) or {}
        endpoint_url = str(raw.get("endpoint_url") or "").strip()
        api_key = str(raw.get("api_key") or "").strip()
        model = str(raw.get("model") or "").strip()
        if not endpoint_url:
            raise ValueError("endpoint_url 不能为空")
        # key 为空时尝试复用已存的 key（允许只改 model 不重填 key）
        if not api_key:
            existing_ref = store.get_credential_ref()
            api_key = ai_service.retrieve_api_key(existing_ref) if existing_ref else ""
            if not api_key:
                raise ValueError("api_key 不能为空（尚未保存过 key）")
        credential_ref = ai_service.store_api_key(endpoint_url, api_key)
        settings = store.save_ai_settings(
            endpoint_url, credential_ref, status="unconfigured", model=model,
        )
        return jsonify(settings)

    @app.route("/api/ai-settings/test", methods=["POST"])
    def ai_settings_test():
        # 优先用请求 body 里当前对话框填的值；缺的再用已保存设置兜底
        endpoint_url, api_key, model = _resolve_credentials_from_request()
        capability = ai_service.test_connection(endpoint_url, api_key, model=model)
        new_status = "ready" if capability["ok"] else "failed"
        error_code = capability["warning_codes"][0] if not capability["ok"] and capability["warning_codes"] else None
        store.update_ai_status(new_status, last_error_code=error_code)
        payload = dict(capability)
        if not capability.get("ok") and error_code:
            payload["user_message"] = ai_service.user_facing_error(error_code)
        else:
            payload["user_message"] = ""
        return jsonify(payload)

    @app.route("/api/ai-settings/models", methods=["POST"])
    def ai_settings_models():
        """拉取可用模型列表。前端持 key 不安全，由后端代理 GET /models。

        改成 POST：前端可把当前对话框里填的 endpoint_url/api_key/model
        放进 body，不必先点"保存设置"就能拉取。body 里缺的字段回退到
        已保存设置（与 /test 路由一致）。
        """
        endpoint_url, api_key, _model = _resolve_credentials_from_request()
        try:
            models = ai_service.list_models(endpoint_url, api_key)
        except ai_service.AISecurityError as exc:
            # 语义修正：AISecurityError 是失败，不应返回 200。
            # 前端 fetchModels 通过 response.ok 判断，502 不影响行为。
            return jsonify({
                "ok": False,
                "error_code": exc.error_code,
                "user_message": ai_service.user_facing_error(exc.error_code),
                "models": [],
            }), 502
        return jsonify({"ok": True, "models": models})

    @app.route("/api/profiles", methods=["GET", "POST"])
    def profiles():
        if request.method == "GET":
            return jsonify({"profiles": store.list_candidate_profiles()})
        raw = request.get_json(silent=True) or {}
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ValueError("画像名称不能为空")
        confirmed_fields = raw.get("confirmed_fields") or {}
        copy_from = raw.get("copy_from")
        profile = store.create_profile(
            name, confirmed_fields=confirmed_fields, copy_from=copy_from,
        )
        return jsonify(profile)

    @app.route("/api/profiles/<profile_id>", methods=["GET", "PATCH", "DELETE"])
    def profile_detail(profile_id):
        if request.method == "GET":
            return jsonify(store.get_profile(profile_id))
        if request.method == "DELETE":
            # 先删该画像下的简历物理文件，再删画像行（CASCADE 清关联表）
            resumes = store.list_resumes(profile_id)
            for r in resumes:
                if r.get("deleted_at"):
                    continue
                resume_service.delete_resume(
                    r["id"], store, resume_dir=app.config["RESUME_DIR"],
                )
            return jsonify(store.delete_profile(profile_id))
        raw = request.get_json(silent=True) or {}
        name = raw.get("name")
        confirmed_fields = raw.get("confirmed_fields")
        return jsonify(store.update_profile(
            profile_id, name=name, confirmed_fields=confirmed_fields,
        ))

    @app.route("/api/profiles/<profile_id>/resume", methods=["POST", "DELETE"])
    def profile_resume(profile_id):
        if request.method == "DELETE":
            store.get_profile(profile_id)
            resumes = store.list_resumes(profile_id)
            deleted = False
            for r in resumes:
                if r.get("deleted_at"):
                    continue
                resume_service.delete_resume(
                    r["id"], store, resume_dir=app.config["RESUME_DIR"],
                )
                deleted = True
            return jsonify({"deleted": deleted})
        # POST: upload. A second resume starts a fresh profile; only the
        # user-confirmed fields are carried forward, never learned preference.
        current_profile = store.get_profile(profile_id)
        if "file" not in request.files:
            raise ValueError("请上传简历文件")
        upload = request.files["file"]
        file_bytes = upload.read()
        filename = upload.filename or "resume.txt"
        if current_profile.get("resume_id"):
            created = store.create_profile(
                f"{Path(filename).stem[:70] or '新简历'} 求职画像",
                confirmed_fields=current_profile.get("confirmed_fields") or {},
            )
            profile_id = created["id"]
        record = resume_service.save_resume(
            profile_id, file_bytes, filename,
            resume_service.validate_format(filename),
            app.config["RESUME_DIR"], store,
        )
        # AI parse occurs only after the user has seen and accepted the
        # pre-upload notice in the UI.  The raw text never leaves this scope.
        ai_suggestion = {}
        settings = store.get_ai_settings()
        consent = request.form.get("ai_consent") == "true"
        if settings.get("is_configured") and consent:
            cred_ref = store.get_credential_ref()
            api_key = ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
            if api_key:
                try:
                    ai_suggestion = ai_service.parse_resume(
                        record["extracted_text"],
                        settings["endpoint_url"], api_key, settings.get("model", ""),
                    )
                    store.save_resume_suggestions(record["id"], ai_suggestion)
                    store.update_ai_status("ready")
                except Exception:
                    store.update_ai_status("failed", last_error_code="parse_failed")
                    ai_suggestion = {"error": "AI 解析失败，请手动填写"}
        # Merge AI suggestion into confirmed_fields (manual wins)
        profile = store.get_profile(profile_id)
        merged = merge_profile_fields(
            profile.get("confirmed_fields") or {}, ai_suggestion,
        )
        response = jsonify({
            "resume_id": record["id"],
            "profile_id": profile_id,
            "format": record.get("format"),
            "extraction_status": "ready" if (record.get("extracted_text") or "").strip() else "empty",
            "ai_suggestion": ai_suggestion,
            "merged_fields": merged,
            "privacy_notice": "如勾选 AI 解析，简历文本会发送至你配置的 AI 服务；不会写入日志或接口响应。",
        })
        return response

    @app.route("/api/profiles/<profile_id>/resumes")
    def profile_resume_list(profile_id):
        store.get_profile(profile_id)
        return jsonify({"resumes": store.list_resumes(profile_id)})

    # == US2: search runs ================================================

    @app.route("/api/search-runs", methods=["POST"])
    def create_search_run():
        raw = request.get_json(silent=True) or {}
        legacy_platform_guard(raw.get("platform"))
        profile_id = raw.get("profile_id")
        if not profile_id:
            raise ValueError(_MSG_PROFILE_ID_REQUIRED)
        profile = store.get_profile(profile_id)
        confirmed_fields = profile.get("confirmed_fields") or {}
        manual_keywords = raw.get("manual_keywords") or []
        manual_filters = raw.get("manual_filters") or {}
        # Merge manual filters into confirmed_fields for keyword selection
        if manual_filters:
            confirmed_fields = {**confirmed_fields, **manual_filters}
        # Use select_keywords to enforce city requirement and cap at 3
        ai_keywords = []
        resume_id = profile.get("resume_id")
        if resume_id:
            ai_keywords = (store.get_resume(resume_id).get("suggestions") or {}).get("keywords") or []
        keywords = select_keywords(
            manual_keywords=manual_keywords,
            ai_keywords=ai_keywords,
            confirmed_fields=confirmed_fields,
        )
        if not keywords:
            raise ValueError("至少需要一个关键词才能搜索")
        run = workbench_runner.create_search_run(
            profile_id, keywords=keywords, confirmed_fields=confirmed_fields,
        )
        return jsonify(run), 202

    @app.route("/api/search-runs/<run_id>")
    def search_run_detail(run_id):
        legacy_platform_guard(request.args.get("platform"))
        run = store.get_search_run(run_id)
        run["queries"] = store.list_run_queries(run_id)
        run["events"] = store.list_search_events(run_id, after=request.args.get("after_event_id", 0, type=int))
        return jsonify(run)

    @app.route("/api/search-runs/<run_id>/cancel", methods=["POST"])
    def cancel_search_run(run_id):
        raw = request.get_json(silent=True) or {}
        legacy_platform_guard(raw.get("platform"))
        return jsonify(workbench_runner.cancel_search_run(run_id))

    @app.route("/api/search-runs/<run_id>/jobs")
    def search_run_jobs(run_id):
        legacy_platform_guard(request.args.get("platform"))
        run = store.get_search_run(run_id)
        profile_id = run["profile_id"]
        profile_jobs = store.list_profile_jobs(profile_id, run_id=run_id)
        requested_sort = request.args.get("sort", "relevance")
        if requested_sort not in {"relevance", "latest", "salary"}:
            raise ValueError("sort 必须为 relevance、latest 或 salary")
        # 批量预取 jobs，避免后续 sort key 和 cards 循环 N+1 调用 store.get_job
        jobs_by_id = store.list_jobs_by_ids([pj["job_id"] for pj in profile_jobs])

        def _job_for(item):
            return jobs_by_id.get(str(item["job_id"])) or {}

        if requested_sort == "relevance":
            profile_jobs.sort(key=lambda item: (item.get("ai_rank") is None, item.get("ai_rank") or 0, item.get("shown_at") or ""))
        elif requested_sort == "latest":
            profile_jobs.sort(key=lambda item: _job_for(item).get("last_seen_at") or "", reverse=True)
        else:
            def salary_value(item):
                match = re.search(r"\d+(?:\.\d+)?", _job_for(item).get("salary") or "")
                return float(match.group()) if match else 0
            profile_jobs.sort(key=salary_value, reverse=True)
        after_job_id = request.args.get("after_job_id")
        if after_job_id:
            ids = [item["job_id"] for item in profile_jobs]
            if after_job_id in ids:
                profile_jobs = profile_jobs[ids.index(after_job_id) + 1:]
        cards = []
        for pj in profile_jobs:
            if pj["status"] == "deleted":
                continue
            job = jobs_by_id.get(str(pj["job_id"]))
            if not job:
                continue  # job 已被清理，跳过
            # job row already carries jd; pass it as detail so project_card
            # can emit the truncated excerpt.
            # Task 008：profile_jobs.status 是当前生命周期状态的唯一来源；
            # 历史 feedback_events 只保留偏好学习语义，不再在读投影时
            # 把 applied/read/stale 覆盖回 interested。
            cards.append(project_card(job, job, interest_state=pj["status"]))
        return jsonify({"jobs": cards})

    # == US3: feedback ===================================================

    @app.route("/api/jobs/<job_id>/feedback", methods=["POST"])
    def post_feedback(job_id):
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get("profile_id")
        action = raw.get("action")
        reason = raw.get("reason")
        if not profile_id or action not in {"interested", "not_interested"}:
            raise ValueError("需要 profile_id 和 action (interested/not_interested)")
        fb = store.create_feedback(profile_id, job_id, None, action, reason=reason)
        if action == "not_interested":
            # Task 008：显式不感兴趣是一条新的用户操作，直接把当前快照写为
            # deleted；不再依赖读取时历史反馈聚合覆盖 profile_jobs.status。
            try:
                store.update_profile_job(profile_id, job_id, status="deleted")
            except KeyError:
                pass
        feedback_count = store.count_effective_feedback(profile_id)
        settings = store.get_ai_settings()
        if feedback_count and feedback_count % FEEDBACK_THRESHOLD == 0 and settings.get("is_configured"):
            credential_ref = store.get_credential_ref()
            api_key = ai_service.retrieve_api_key(credential_ref) if credential_ref else ""
            if api_key:
                try:
                    preference = ai_service.update_preference(
                        store.get_profile(profile_id), store.list_feedback(profile_id),
                        settings["endpoint_url"], api_key, settings.get("model", ""),
                    )
                    store.save_preference_version(profile_id, feedback_count, preference)
                except (ai_service.AISecurityError, ValueError):
                    # Feedback remains valid; a failed optional preference update
                    # must not alter the user's feedback or task state.
                    store.update_ai_status("failed", last_error_code="preference_failed")
        return jsonify({
            "feedback_id": fb["id"],
            "interest_state": action,
        })

    @app.route("/api/feedback/<feedback_id>/revoke", methods=["POST"])
    def revoke_feedback(feedback_id):
        fb = store.get_feedback(feedback_id)
        already_revoked = bool(fb.get("revoked_at"))
        store.revoke_feedback(feedback_id)
        if fb["action"] == "not_interested" and not already_revoked:
            # Task 008：显式撤销也是一次用户操作。仅当当前快照仍是 deleted
            # 且没有其他生效中的不感兴趣反馈时，恢复为 new；历史事件保留。
            remaining = [
                event for event in store.list_feedback(
                    fb["profile_id"], job_id=fb["job_id"])
                if event.get("action") == "not_interested"
                and not event.get("revoked_at")
            ]
            if not remaining:
                try:
                    pj = store.get_profile_job(fb["profile_id"], fb["job_id"])
                except KeyError:
                    pj = None
                if pj is not None and pj["status"] == "deleted":
                    store.update_profile_job(
                        fb["profile_id"], fb["job_id"], status="new")
        return jsonify({"revoked": True})

    # == US4: history, favorites, cleanup ================================

    @app.route("/api/profile-jobs")
    def list_profile_jobs():
        profile_id = request.args.get("profile_id")
        if not profile_id:
            raise ValueError(_MSG_PROFILE_ID_REQUIRED)
        status = request.args.get("status")
        run_id = request.args.get("run_id")
        jobs = store.list_profile_jobs(profile_id, status=status, run_id=run_id)
        return jsonify({"jobs": jobs})

    @app.route("/api/profile-jobs/<profile_id>/<job_id>", methods=["PATCH"])
    def patch_profile_job(profile_id, job_id):
        """Legacy PATCH：迁移期兼容，全部生命周期写入转入统一命令服务。

        不允许直接 UPDATE status/applied_at 绕过事件、幂等回执和时间校验；
        request ID 来自 Idempotency-Key 请求头或 body request_id，缺失 428；
        note 与生命周期混合请求在同一事务中原子完成或整体拒绝。
        """
        raw = request.get_json(silent=True) or {}
        has_status = "status" in raw
        has_applied = "applied_at" in raw
        has_note = "note" in raw
        if not (has_status or has_applied):
            # note-only（或空载荷）保持现有独立备注语义，无需 request ID
            allowed = {k: raw[k] for k in ("note",) if k in raw}
            return jsonify(store.update_profile_job(profile_id, job_id, **allowed))

        request_id = str(
            request.headers.get("Idempotency-Key")
            or raw.get("request_id")
            or ""
        ).strip()
        if not request_id:
            return _feedback_error_response(
                "idempotency_key_required",
                "写请求必须携带 Idempotency-Key 或 request_id",
                status=428,
            )

        # 保持 legacy 语义：画像岗位关联不存在时 404，不隐式创建
        try:
            store.get_profile_job(profile_id, job_id)
        except KeyError:
            return _feedback_error_response("not_found", "画像岗位不存在", 404)

        if has_status and has_applied:
            action, target_status, applied_at = (
                "correct_status", raw["status"], raw["applied_at"])
        elif has_status:
            action, target_status, applied_at = (
                "correct_status", raw["status"], None)
        else:
            action, target_status, applied_at = (
                "correct_applied_at", None, raw["applied_at"])

        def _run_command(service):
            return service.execute_action(
                request_id=request_id,
                profile_id=profile_id,
                job={"job_id": job_id},
                action=action,
                applied_at=applied_at,
                target_status=target_status,
            )

        try:
            if has_note:
                # 混合请求：命令服务与 note 共用同一连接/事务，
                # 要么一起提交，要么整体回滚后拒绝 legacy_patch_ambiguous。
                conn = store._connect()
                try:
                    shared_service = JobFeedbackService(
                        _SharedConnectionStore(store, conn))
                    result = _run_command(shared_service)
                    conn.execute(
                        "UPDATE profile_jobs SET note=? "
                        "WHERE profile_id=? AND job_id=?",
                        (raw["note"], str(profile_id), str(job_id)),
                    )
                    conn.commit()
                except JobFeedbackError:
                    conn.rollback()
                    raise
                except sqlite3.Error:
                    conn.rollback()
                    raise JobFeedbackError(
                        "legacy_patch_ambiguous",
                        "备注与生命周期写入无法原子完成",
                    )
                finally:
                    conn.close()
            else:
                result = _run_command(job_feedback_service)
        except JobFeedbackError as exc:
            if exc.code == "legacy_patch_ambiguous":
                return _feedback_error_response(exc.code, str(exc), exc.details, 400)
            return _feedback_error_response(exc.code, str(exc), exc.details)
        except sqlite3.Error:
            return _feedback_error_response(
                "persistence_failed", "岗位数据保存失败，请重试", status=500)
        return jsonify({"ok": True, **result})

    @app.route("/api/cleanup-preview")
    def cleanup_preview():
        would_remove = store.preview_cleanup_expired_jobs(days=CLEANUP_EXPIRED_DAYS)
        return jsonify({"would_remove": len(would_remove), "items": would_remove})

    # ------------------------------------------------------------------
    # Three-stage pipeline: resume → AI fields → user confirm → script
    # ------------------------------------------------------------------

    # Stage-3 execution: in-memory progress tracker + single-worker executor.
    # Each run is keyed by a task_id; progress snapshots and final results are
    # stored here and polled by the frontend. Local single-user app, so an
    # in-memory dict is sufficient.
    _pipeline_tasks = {}
    _pipeline_lock = threading.RLock()
    _resume_claims = set()
    _pipeline_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="boss-pipeline")
    app.config["PIPELINE_TASKS"] = _pipeline_tasks
    app.config["PIPELINE_EXECUTOR"] = _pipeline_executor
    from webui.pipeline_exec import TuningRoundRunner

    def _tuning_ai_settings():
        settings = store.get_ai_settings()
        credential_ref = store.get_credential_ref()
        api_key = (
            ai_service.retrieve_api_key(credential_ref) if credential_ref else ""
        )
        return {**settings, "api_key": api_key}

    _tuning_round_runner = TuningRoundRunner(
        workspace_root=Path(store.db_path).resolve().parent.parent,
        source_factory=_make_cdp_source,
        ai_settings_provider=_tuning_ai_settings,
    )
    app.config["TUNING_ROUND_RUNNER"] = _tuning_round_runner

    def _run_tuning_manifest_child(manifest_id: str):
        from webui.tuning import TuningController

        controller = TuningController(store)
        record = store.get_task_manifest(manifest_id)
        manifest = record["manifest"]
        round_id = record["round_id"]
        sink = controller.build_measurement_sink(round_id)

        def measured(*args, **kwargs):
            controller.heartbeat_lease()
            return sink(*args, **kwargs)

        error_code = None
        try:
            # T614: 在 source/AI 执行前校验一致性，错配时阻断
            controller.validate_consistency_before_execution(
                manifest_id=manifest_id,
            )
            result = _tuning_round_runner.execute(
                manifest, measurement_callback=measured,
            )
        except (
            OSError, RuntimeError, ValueError, KeyError, TypeError,
            ai_service.AISecurityError,
        ) as exc:
            result = None
            error_code = (
                exc.error_code
                if isinstance(exc, ai_service.AISecurityError)
                else getattr(exc, "error_code", type(exc).__name__.lower())
            )
        if isinstance(result, dict):
            controller.persist_stage_artifact(
                round_id=round_id,
                stage=manifest["round_kind"],
                payload=result,
                source_artifact_id=manifest["frozen_input"].get(
                    "source_artifact_id"
                ),
            )
        summary = controller.aggregate_measurements(round_id)
        if not summary.get("input_count") and isinstance(result, dict):
            jobs = result.get("jobs")
            verdicts = result.get("verdicts")
            if isinstance(jobs, list):
                summary["input_count"] = len(jobs)
                summary["terminal_count"] = len(jobs)
                summary["success_count"] = len(jobs)
            elif isinstance(verdicts, dict):
                summary["input_count"] = len(verdicts)
                summary["terminal_count"] = len(verdicts)
                summary["success_count"] = len(verdicts)
        summary["work_duration_ms"] = (
            summary["total_duration_ms"] - summary["wait_duration_ms"]
            - summary["retry_duration_ms"]
        )
        evidence_path = manifest["monitoring"]["final_artifact_path"]
        evidence = {
            "program_report_path": evidence_path,
            "config_digest": manifest["execution_config"]["config_digest"],
            "scope_digest": manifest["frozen_input"]["scope_digest"],
            "input_artifact_digest": manifest["frozen_input"].get("artifact_digest"),
            **summary,
        }
        if error_code:
            evidence["error_counts"] = {
                **evidence.get("error_counts", {}), error_code: 1,
            }
        absolute = (Path(store.db_path).resolve().parent.parent / evidence_path).resolve()
        expected_root = (
            Path(store.db_path).resolve().parent.parent / "tuning"
            / manifest["experiment_id"]
        ).resolve()
        if expected_root not in absolute.parents:
            raise ValueError("程序证据输出路径越过实验根目录")
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_text(json.dumps(
            evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ), encoding="utf-8")
        controller._save_round_metrics(round_id, evidence)
        store.update_tuning_round_status(
            round_id, status="reported", failure_code=error_code,
        )

    app.config["RUN_TUNING_MANIFEST_CHILD"] = _run_tuning_manifest_child

    def _new_pipeline_task(kind, *, source_task_id=None):
        task = {
            "kind": kind,
            "status": "queued",
            "progress": {},
            "logs": [],
            "result": None,
            "error": "",
            # 任务创建即记开始，终态时补结束（前端计时器从快照读取，
            # 不再依赖组件存活期间的本地时钟，组件销毁重建/刷新后不再归零）。
            "started_at": int(time.time() * 1000),
            "finished_at": None,
            # 停止信号：cancel 接口 set 它，run_search 循环检查到后退出。
            # 不放进 task 的 JSON 序列化里（threading.Event 不可序列化），
            # 只在服务进程内存中存活。
            "stop_event": threading.Event(),
        }
        if source_task_id:
            task["source_task_id"] = source_task_id
        return task

    def _register_pipeline_task(task_id, kind, *, source_task_id=None):
        task = _new_pipeline_task(
            kind, source_task_id=source_task_id
        )
        with _pipeline_lock:
            _pipeline_tasks[task_id] = task
        return task

    def _claim_recrawl_start(task_id, source_run_id):
        """Atomically reserve one active recrawl per source run."""
        with _pipeline_lock:
            for existing_id, existing in _pipeline_tasks.items():
                if (
                    existing.get("kind") == "recrawl"
                    and existing.get("source_run_id") == source_run_id
                    and existing.get("status") in ("queued", "running")
                ):
                    return None, existing_id
            task = _new_pipeline_task("recrawl")
            task["source_run_id"] = source_run_id
            _pipeline_tasks[task_id] = task
            return task, None

    def _check_tuning_lease_conflict():
        """SPEC011 T015/FR-035: 检查实验租约是否被持有。

        租约被持有时返回 (False, error_response)，调用方应直接 return 该响应。
        无租约时返回 (True, None)。

        普通任务和实验任务共用此门禁，确保任意时刻只有一个压力任务运行。
        """
        try:
            lease = store.get_tuning_lease()
        except _OPERATIONAL_ERRORS:
            # 数据库异常时不阻断普通任务（租约检查是安全网，不是硬门禁）
            return True, None
        if lease.get("owner_experiment_id") is None:
            return True, None
        return False, (jsonify({
            "ok": False,
            "error": "tuning_lease_held",
            "error_code": "tuning_lease_held",
            "message": "深度实验正在独占执行环境，请等待实验结束后再启动普通任务",
            "owner_experiment_id": lease.get("owner_experiment_id"),
            "retryable": False,
            "required_action": "等待实验结束或取消实验后再启动普通任务",
        }), 409)

    def _claim_pipeline_task_id(task_id, kind, *, started_at=None):
        """Atomically reserve a concrete task id for continuation."""
        with _pipeline_lock:
            previous = _pipeline_tasks.get(task_id)
            if previous is not None and previous.get("status") in (
                "queued", "running",
            ):
                return None, previous
            task = _new_pipeline_task(kind)
            preserved_started_at = started_at
            if preserved_started_at is None and previous is not None:
                preserved_started_at = previous.get("started_at")
            if preserved_started_at is not None:
                task["started_at"] = int(preserved_started_at)
            _pipeline_tasks[task_id] = task
            return task, previous

    def _release_pipeline_claim(task_id, claimed_task, previous_task=None):
        """Remove only the reservation created by the current request."""
        with _pipeline_lock:
            if _pipeline_tasks.get(task_id) is claimed_task:
                if previous_task is None:
                    _pipeline_tasks.pop(task_id, None)
                else:
                    _pipeline_tasks[task_id] = previous_task

    def _claim_resume(run_id):
        """Atomically reserve a paused run while a continuation is scheduled."""
        with _pipeline_lock:
            if run_id in _resume_claims:
                return False
            _resume_claims.add(run_id)
            return True

    def _release_resume_claim(run_id):
        with _pipeline_lock:
            _resume_claims.discard(run_id)

    def _is_user_finished(run_id):
        """判断 run 是否已被用户结束保存（interrupted + user_finished）。"""
        try:
            run = store.get_screening_run(run_id)
        except _OPERATIONAL_ERRORS:
            return False
        return bool(
            run and run.get("status") == "interrupted"
            and run.get("error_code") == "user_finished"
        )

    def _write_run_unless_finished(run_id, **kwargs):
        """Worker 写 DB 前的统一守卫：用户已结束时跳过，绝不覆盖终态。"""
        if _is_user_finished(run_id):
            return False
        try:
            store.update_screening_run(run_id, **kwargs)
            return True
        except DiscoveryStoreConflictError:
            return False

    def _record_pause_failure(task_id, stage, code, reason, *, processed=0, total=0,
                              extra=None, exception=None, include_traceback=False):
        """Write the durable failure event for systemic pause paths."""
        diagnostics = {"stage": stage, "processed": int(processed), "total": int(total)}
        if extra:
            diagnostics.update(extra)
        record_failure(
            store, task_id, stage=stage,
            error_code=code or "internal_error",
            reason=reason or code or "任务被阻断",
            correlation_id=task_id, diagnostics=diagnostics,
            exception=exception, include_traceback=include_traceback,
        )

    def _release_worker_resume_claims(task):
        """worker 终态/暂停时释放续跑接管标记（B027 卡死点）。"""
        with _pipeline_lock:
            claim_id = (task or {}).get("resuming_from") or (task or {}).get("resumed_from")
        if claim_id:
            _release_resume_claim(str(claim_id))

    def _schedule_pipeline_task_cleanup(task_id):
        """30 分钟后自动从 _pipeline_tasks 中移除已完成的任务，避免内存泄漏。"""
        def _cleanup():
            with _pipeline_lock:
                _pipeline_tasks.pop(task_id, None)
        timer = threading.Timer(30 * 60, _cleanup)
        timer.daemon = True
        timer.start()

    app.config["SCHEDULE_PIPELINE_CLEANUP"] = _schedule_pipeline_task_cleanup

    app.config["SCHEDULE_PIPELINE_TASK_CLEANUP"] = _schedule_pipeline_task_cleanup

    # -----------------------------------------------------------------------
    # AI 筛选阶段百分比与文案
    # -----------------------------------------------------------------------
    _SCREEN_STAGE_MESSAGES: dict[str, str] = {
        "resume": "正在恢复上次进度…",
        "screen_a": "AI 粗筛中…",
        "screen_a_done": "粗筛完成，准备抓取 JD…",
        "ensure_chrome": "启动浏览器，准备抓取 JD…",
        "fetch_jd": "抓取 JD 中…",
        "screen_b": "AI 精筛中…",
        "done": "筛选完成",
        "cancelled": "运行已取消",
    }
    _EVENT_STAGE_NAMES = {
        "screen_a": "ai_rough",
        "screen_a_done": "ai_rough",
        "fetch_jd": "jd_detail",
        "screen_b": "ai_fine",
    }

    def _account_for_run(run=None) -> str:
        """Resolve the browser account for a run or the current advanced setting."""
        from webui.pipeline_exec import load_browser_accounts
        accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
        if isinstance(run, dict):
            params = run.get("execution_params") or {}
            if isinstance(params, dict):
                account = str(params.get("browser_account") or "")
                if account in accounts:
                    return account
        account = str((_load_legacy_advanced_settings() or {}).get("browser_account") or "a")
        return account if account in accounts else "a"

    def _format_unlock_time(until: float) -> str:
        """把解封时间戳格式化为用户可读的「MM-DD HH:MM」。"""
        from datetime import datetime as _dt
        return _dt.fromtimestamp(until).strftime("%m-%d %H:%M")

    def _restricted_cache_detail(account: str) -> str:
        """受限中（缓存）展示来源：冷却记录原因与来源任务，缺来源时不编造。"""
        try:
            from webui.cooldown import get_cooldown
            record = get_cooldown(account, "boss")
        except Exception:
            record = None
        base = "受限中（缓存） — 账号或 IP 命中风控，建议等待后重试"
        if not isinstance(record, dict):
            return base
        reason = str(record.get("reason") or "").strip()
        from_run = str(record.get("from_run") or "").strip()
        parts = [base]
        if reason:
            parts.append(f"原因：{reason}")
        if from_run:
            parts.append(f"来源任务：{from_run}")
        return "；".join(parts)

    def _invalidate_login_cache(account_id: str, platform: str) -> None:
        """打开浏览器登录窗口时失效该账号该平台的登录态缓存（D3 信号）。

        用户可能刚完成登录；失效后下次 preflight / env-check 重新真实探测，
        避免沿用登录前的旧状态（如缓存里的 not_logged_in 挡住任务提交）。
        """
        try:
            from scripts.login_state_cache import invalidate_login_state
            invalidate_login_state(str(account_id), str(platform))
        except Exception:
            pass

    def _submit_cooldown_guard(raw: dict):
        """任务提交前的冷却校验（D6）。

        Returns:
            (error_response, error_status, warning_payload)
            - error_response: 同账号正处于冷却 → jsonify 拒绝响应，否则 None；
            - error_status: 拒绝时的 HTTP 状态码（409），无拒绝时为 None；
            - warning_payload: 其他账号冷却中 → 连坐提醒 dict，否则 None。
        """
        from webui.cooldown import all_cooldowns
        from webui.pipeline_exec import load_browser_accounts
        accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
        account = str(raw.get("browser_account") or "") or _account_for_run()
        if account not in accounts:
            account = "a"
        platform = str(raw.get("platform") or "boss")
        cooldowns = all_cooldowns()
        record = cooldowns.get(account, {}).get(platform)
        if record is not None:
            remaining = max(1, int(record["until"] - time.time()))
            return jsonify({
                "ok": False,
                "error_code": "account_in_cooldown",
                "remaining_seconds": remaining,
                "until": record["until"],
                "user_message": (
                    f"账号正处风控冷却，建议等待至 "
                    f"{_format_unlock_time(record['until'])} 后再提交任务"
                ),
            }), 409, None
        warnings = [
            {
                "account_id": aid,
                "platform": pid,
                "until": rec["until"],
            }
            for aid, platforms in cooldowns.items()
            for pid, rec in platforms.items()
            if aid != account or pid != platform
        ]
        if not warnings:
            return None, None, None
        return None, None, {
            "code": "other_account_cooldown",
            "message": (
                "其他账号正处于风控冷却（连坐风险），新任务仍会提交，"
                "但抓取可能受影响，建议等待冷却结束后再跑"
            ),
            "cooldowns": warnings,
        }

    def _activate_run_browser(run=None) -> None:
        """Point the shared CDP helper at the selected profile."""
        from webui.pipeline_exec import resolve_browser_account, set_active_cdp_data_dir
        from webui.platforms import derive_zhilian_profile_dir, resolve_login_space
        account = str((run or {}).get("browser_account") or (run or {}).get("execution_params", {}).get("browser_account") or "") or _account_for_run(run)
        platform = str((run or {}).get("platform") or (run or {}).get("execution_params", {}).get("platform") or "boss")
        boss_dir = resolve_browser_account(account, app.config["BROWSER_ACCOUNTS_PATH"]) or ""
        resolve_login_space(platform, account, boss_profile_dir=boss_dir or "unresolved")
        profile_dir = boss_dir if platform == "boss" else derive_zhilian_profile_dir(boss_dir)
        set_active_cdp_data_dir(profile_dir)

    def _activate_task_browser(task_id: str) -> None:
        """Use the account captured when the task was submitted, if present."""
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id) or {}
            account = str(task.get("browser_account") or "")
        from webui.pipeline_exec import resolve_browser_account, set_active_cdp_data_dir
        profile_dir = resolve_browser_account(
            account, app.config["BROWSER_ACCOUNTS_PATH"])
        if profile_dir:
            platform = str(task.get("platform") or "boss")
            from webui.platforms import resolve_login_space
            _ = resolve_login_space(platform, account or "a", boss_profile_dir=profile_dir)
            from webui.platforms import derive_zhilian_profile_dir
            set_active_cdp_data_dir(profile_dir if platform == "boss" else derive_zhilian_profile_dir(profile_dir))
        else:
            _activate_run_browser()

    def _ensure_scrape_source(scrape_task_id: str) -> dict | None:
        """Return a scrape source snapshot, rebuilding it from DB after a restart."""
        with _pipeline_lock:
            source_task = _pipeline_tasks.get(scrape_task_id)
            if source_task is not None:
                source_result = source_task.get("result") or {}
                if source_task.get("status") == "done" and source_result.get("ok"):
                    return dict(source_task)
        source_jobs = store.load_scrape_run_jobs(scrape_task_id)
        if not source_jobs:
            return None
        try:
            source_run = store.get_screening_run(scrape_task_id)
        except _OPERATIONAL_ERRORS:
            source_run = None
        # 结束保存/失败/重启中断的父抓取任务只要岗位仍持久化，就允许
        # 从 scrape_run_jobs 重建只读来源快照（B027：03 页不得报缺任务）。
        if source_run is None or source_run.get("status") not in ("succeeded", "partial", "failed", "interrupted"):
            return None
        snapshot = {
            "kind": "scrape", "status": "done", "progress": {}, "logs": [],
            "result": {
                "ok": True, "jobs": source_jobs,
                "total_scraped": len(source_jobs), "total_matched": len(source_jobs),
                "completed_combos": sorted(store.load_checkpoint(scrape_task_id, "scrape")),
                "error": "",
            },
            "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(),
            "auto_screen": bool((source_run.get("execution_params") or {}).get("auto_screen")),
        }
        with _pipeline_lock:
            _pipeline_tasks[scrape_task_id] = snapshot
        return dict(snapshot)

    def _scrape_completed_for_run(execution_params: dict) -> bool:
        """Infer whether the source scrape run finished after a service restart."""
        if bool(execution_params.get("scrape_completed")):
            return True
        scrape_task_id = str(execution_params.get("scrape_task_id") or "")
        if not scrape_task_id:
            return False
        try:
            source_run = store.get_screening_run(scrape_task_id)
        except _OPERATIONAL_ERRORS:
            return False
        return bool(source_run and source_run.get("status") == "succeeded")

    def _clear_auto_screen(task_id: str) -> None:
        """清除一键链路的 auto_screen 标记（内存与 DB execution_params）。"""
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)
            if task is not None:
                task["auto_screen"] = False
        try:
            run = store.get_screening_run(task_id)
        except _OPERATIONAL_ERRORS:
            return
        if run is None:
            return
        params = dict(run.get("execution_params") or {})
        if params.get("auto_screen"):
            params["auto_screen"] = False
            try:
                store.update_screening_execution_params(task_id, params)
            except _OPERATIONAL_ERRORS:
                pass

    def _consume_auto_screen(task_id: str) -> None:
        """AI 筛选入口消费标记；调用后刷新不再自动重试。"""
        _clear_auto_screen(task_id)

    def _check_resume_block(run: dict) -> tuple[bool, str, str]:
        """Verify the paused dependency before submitting resumed work."""
        _activate_run_browser(run)
        checker = app.config.get("RESUME_BLOCK_CHECKER")
        if callable(checker):
            passed, code, reason = checker(run)
        else:
            code = str(run.get("error_code") or "")
            reason = ""
            passed = True
            try:
                if code in {
                    "captcha_required", "login_expired", "ip_risk_control",
                    "cdp_unavailable", "source_verification_required",
                    "source_login_required", "source_blocked", "source_rate_limited",
                    "source_cdp_unavailable",
                }:
                    from webui.pipeline_exec import ensure_chrome_ready, taxonomy_reason
                    # T403: 从 run 继承冻结平台/浏览器身份
                    _resume_params = run.get("execution_params") or {}
                    _resume_platform = (
                        run.get("platform")
                        or _resume_params.get("platform")
                        or "boss"
                    )
                    chrome_ok, chrome_err = ensure_chrome_ready(_resume_params.get("cdp_port"))
                    if not chrome_ok:
                        passed = False
                        code = "cdp_unavailable"
                        reason = f"调试浏览器尚未就绪：{chrome_err}"
                    else:
                        source = _make_cdp_source(
                            platform=_resume_platform,
                            browser_account=_resume_params.get("browser_account"),
                            cdp_port=_resume_params.get("cdp_port"),
                            profile_key=_resume_params.get("profile_key"),
                            run_id=str(run.get("id") or ""),
                        )
                        outcome = source.preflight() if source is not None else None
                        if outcome is None or not outcome.ok:
                            passed = False
                            source_code = getattr(outcome, "failed_code", "")
                            code = {
                                "source_login_required": "login_expired",
                                "source_cdp_unavailable": "cdp_unavailable",
                                "source_verification_required": "captcha_required",
                            }.get(source_code, code or "source_blocked")
                            reason = taxonomy_reason(
                                code, _resume_platform, fallback="阻断条件尚未解除"
                            )
                elif code in {
                    "ai_key_invalid", "ai_quota_exhausted",
                    "ai_rate_limited", "ai_network_error",
                }:
                    from webui.pipeline_exec import taxonomy_reason
                    settings = store.get_ai_settings()
                    credential_ref = store.get_credential_ref()
                    api_key = (
                        ai_service.retrieve_api_key(credential_ref)
                        if credential_ref else ""
                    )
                    if not ai_service.is_ai_available(settings, credential_ref, api_key):
                        passed = False
                        reason = "AI 配置或额度问题尚未处理，请更新后再继续"
                    else:
                        capability = ai_service.test_connection(
                            str(settings.get("endpoint_url") or ""),
                            api_key,
                            model=str(settings.get("model") or ""),
                        )
                        if not capability.get("ok"):
                            passed = False
                            reason = taxonomy_reason(
                                code, "", fallback="AI 阻断条件尚未解除"
                            )
                elif code == "internal_error":
                    passed = False
                    reason = "内部错误尚未解除，请先检查日志或重启服务"
            except (OSError, RuntimeError, ValueError) as exc:
                passed = False
                code = code or "internal_error"
                reason = f"阻断复核失败：{type(exc).__name__}"
        store.append_task_event(run["id"], "block_check", {
            "passed": bool(passed), "stage": run.get("current_stage"),
            "error_code": code, "reason": reason,
        })
        if not passed:
            store.update_screening_run(
                run["id"], error_code=code or "internal_error",
                error_reason=reason or "阻断条件尚未解除",
            )
        return bool(passed), str(code or ""), str(reason or "")

    def _persist_jd_job_failures(
            task_run_id: str, jobs: list[dict], *, stage: str,
            source_run_id: str = "", platform: str = "") -> None:
        """Persist per-job JD failures before a systemic pause returns."""
        from webui.pipeline_exec import (
            ERROR_TAXONOMY,
            failed_code_label,
            taxonomy_reason,
        )

        target_run_ids = [str(task_run_id)]
        if source_run_id and str(source_run_id) not in target_run_ids:
            target_run_ids.append(str(source_run_id))
        events = []
        source_code_aliases = {
            "source_login_required": "login_expired",
            "source_verification_required": "captcha_required",
            "source_cdp_unavailable": "cdp_unavailable",
        }
        for job in jobs or []:
            if not isinstance(job, dict) or str(job.get("jd") or "").strip():
                continue
            job_id = str(job.get("platform_job_id") or job.get("job_id") or job.get("id") or "").strip()
            failed_code = str(job.get("jd_failed_code") or "").strip()
            if not job_id or not failed_code:
                continue
            taxonomy_code = source_code_aliases.get(failed_code, failed_code)
            taxonomy = ERROR_TAXONOMY.get(taxonomy_code, {})
            reason = str(job.get("jd_failed_reason") or "").strip()
            if not reason:
                reason = taxonomy_reason(
                    taxonomy_code, platform,
                    fallback=failed_code_label(failed_code, platform) or "JD 抓取失败",
                )
            for run_id in target_run_ids:
                existing = store.get_pending_result(run_id, job_id)
                store.insert_pending_result(
                    run_id,
                    job_id,
                    failure_stage=stage,
                    retryable=bool(taxonomy.get("retryable", True)),
                    attempts=int((existing or {}).get("attempts") or 0) + 1,
                    origin_zone=str((existing or {}).get("origin_zone") or "kept"),
                    ai_payload_json={
                        "reason": reason,
                        "evidence": failed_code,
                        "evidence_detail": str(job.get("jd_failed_evidence") or ""),
                        "next_action": "retry_jd",
                    },
                    failed_code=failed_code,
                    platform=platform,
                )
            events.append(("job_fail", {
                "stage": stage,
                "job_id": job_id,
                "failed_code": failed_code,
                "reason": reason,
                "evidence_detail": str(job.get("jd_failed_evidence") or ""),
            }))
        if events:
            store.append_task_events(task_run_id, events)

    def _run_pipeline_task(
        task_id, script_params, execution_config=None, frozen_scope=None,
    ):
        from webui.pipeline_exec import expand_combinations, run_search
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)
            if task is None:
                task = {
                    "kind": "scrape", "status": "queued", "progress": {},
                    "logs": [], "result": None, "error": "",
                    "started_at": int(time.time() * 1000), "finished_at": None,
                }
                _pipeline_tasks[task_id] = task
            task["status"] = "running"
            task["script_params"] = script_params  # 断点续抓需要原始参数
            if execution_config is not None:
                task["config_digest"] = execution_config.config_digest
            if frozen_scope is not None:
                task["scope_digest"] = frozen_scope.scope_digest
            task.setdefault("page_flush_lock", threading.Lock())
            task.setdefault("page_persist_seq", 0)
            task.setdefault("last_page_snapshot_at", 0)
        if _is_user_finished(task_id):
            with _pipeline_lock:
                current = _pipeline_tasks.get(task_id)
                if current is not None:
                    current["status"] = "cancelled"
                    current["error"] = _MSG_USER_FINISHED
            _release_worker_resume_claims(_pipeline_tasks.get(task_id))
            return
        _activate_task_browser(task_id)

        def on_progress(snapshot):
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is None:
                    return
                task["progress"] = snapshot
                msg = snapshot.get("message")
                if msg:
                    task["logs"].append(msg)

        try:
            # 取出停止信号传给 run_search；cancel 接口 set 它后，
            # run_search 会在下一个组合边界退出，或因浏览器被关而抛错。
            with _pipeline_lock:
                task_ref = _pipeline_tasks.get(task_id, {})
                stop_event = task_ref.get("stop_event")
                skip_combos = task_ref.get("skip_combos") or None
                old_jobs = task_ref.get("old_jobs") or []
                # T403: 从 task dict 读取冻结 runtime，不读当前 UI/活动账号/默认端口
                frozen_platform = task_ref.get("platform") or "boss"
                frozen_cdp_port = task_ref.get("cdp_port")
                frozen_profile_key = task_ref.get("profile_key")
                frozen_browser_account = task_ref.get("browser_account")
            if store.get_screening_run(task_id) is None:
                store.create_screening_run(
                    task_id,
                    source_count=len(expand_combinations(script_params)),
                    execution_params={
                        "script_params": script_params,
                        "browser_account": _account_for_run(),
                        "execution_config": (
                            execution_config.to_dict()
                            if execution_config is not None else None
                        ),
                        "frozen_scope": (
                            frozen_scope.to_dict() if frozen_scope is not None else None
                        ),
                    },
                    backend_version=_backend_version,
                )
            _write_run_unless_finished(
                task_id, status="running", current_stage="scrape"
            )
            store.append_task_event(task_id, "stage_start", {"stage": "scrape"})
            # T403: 从冻结 runtime 创建 source，禁止读取当前 UI/活动账号/默认端口
            source = _make_cdp_source(
                platform=frozen_platform,
                browser_account=frozen_browser_account,
                cdp_port=frozen_cdp_port,
                profile_key=frozen_profile_key,
                run_id=task_id,
            )
            if source is None:
                completed = sorted(skip_combos or [])
                reason = "连不上调试浏览器，请启动 Chrome 调试端口后继续"
                _write_run_unless_finished(
                    task_id,
                    status="paused",
                    current_stage="scrape",
                    error_code="cdp_unavailable",
                    error_reason=reason,
                    processed_count=len(completed),
                )
                store.save_checkpoint(task_id, "scrape", completed)
                store.append_task_event(task_id, "pause", {
                    "stage": "scrape",
                    "code": "cdp_unavailable",
                    "completed_combos": len(completed),
                })
                _record_pause_failure(
                    task_id, "scrape", "cdp_unavailable", reason,
                    processed=len(completed), total=len(completed),
                )
                with _pipeline_lock:
                    task = _pipeline_tasks.get(task_id)
                    if task is not None:
                        task["status"] = "paused"
                        task["error"] = reason
                _release_worker_resume_claims(_pipeline_tasks.get(task_id))
                return

            def on_combo_done(combo_key, jobs, completed_combos, *, outcome=None):
                # T404: 先持久化 source attempt，再推进 combo result。
                # 持久化失败时抛异常，run_search 会捕获并硬停止。
                attempt_no = 1
                try:
                    latest = store.get_latest_source_attempt(task_id, combo_key)
                    if latest is not None:
                        attempt_no = latest["attempt_no"] + 1
                except _OPERATIONAL_ERRORS:
                    pass
                if outcome is not None:
                    outcome_kind = "empty" if outcome.empty_result else "non_empty"
                    store.append_source_attempt(
                        run_id=task_id,
                        platform=frozen_platform,
                        combo_key=combo_key,
                        attempt_no=attempt_no,
                        input_hash=outcome.input_hash,
                        outcome_kind=outcome_kind,
                        job_count=len(jobs),
                        empty_evidence=outcome.empty_evidence,
                    )
                else:
                    store.append_source_attempt(
                        run_id=task_id,
                        platform=frozen_platform,
                        combo_key=combo_key,
                        attempt_no=attempt_no,
                        outcome_kind="non_empty",
                        job_count=len(jobs),
                    )
                store.save_scrape_combo_result(
                    task_id, combo_key, jobs, completed_combos
                )
                store.append_task_events(task_id, [
                    ("job_success", {
                        "stage": "scrape", "combo_key": combo_key,
                        "job_id": str(job.get("job_id") or job.get("source_url") or ""),
                    })
                    for job in jobs if isinstance(job, dict)
                ])

            def on_page_completed(event):
                """每完成一页原子保存岗位快照与页级 checkpoint。"""
                lock = None
                with _pipeline_lock:
                    task_ref = _pipeline_tasks.get(task_id)
                    if task_ref is not None:
                        lock = task_ref.get("page_flush_lock")
                if lock is not None:
                    lock.acquire()
                try:
                    store.save_scrape_page_progress(
                        task_id, str(event.get("combo_key") or ""), event)
                finally:
                    if lock is not None:
                        lock.release()
                with _pipeline_lock:
                    task_ref = _pipeline_tasks.get(task_id)
                    if task_ref is not None:
                        task_ref["last_page_snapshot_at"] = time.time()
                        task_ref["page_persist_seq"] = int(
                            task_ref.get("page_persist_seq") or 0) + 1
                        task_ref["last_page_progress"] = dict(event)

            try:
                page_rows = store.load_scrape_page_progress(task_id)
            except _OPERATIONAL_ERRORS:
                page_rows = []
            skip_set = set(skip_combos or [])
            resume_pages = {
                row["combo_key"]: row["resume_page"]
                for row in page_rows if row["combo_key"] not in skip_set
            }
            resume_jobs = {}
            for row in page_rows:
                if row["combo_key"] in skip_set:
                    continue
                try:
                    resume_jobs[row["combo_key"]] = store.load_scrape_run_jobs(
                        task_id, combo_key=row["combo_key"])
                except _OPERATIONAL_ERRORS:
                    resume_jobs[row["combo_key"]] = []

            result = run_search(
                script_params, source,
                pages=(
                    frozen_scope.pages_per_combination
                    if frozen_scope is not None else int(script_params.get("pages") or 3)
                ), progress=on_progress,
                artifact_dir=app.config["RESULT_DIR"],
                stop_event=stop_event,
                skip_combos=skip_combos,
                on_combo_done=on_combo_done,
                execution_config=execution_config,
                on_page_completed=on_page_completed,
                resume_pages=resume_pages,
                resume_jobs=resume_jobs,
            )
            # 断点续抓：合并旧结果（按 job_id 去重）
            merged_total = None
            if old_jobs and result.get("ok"):
                existing_ids = {j.get("job_id") or j.get("source_url") or ""
                                for j in result["jobs"]}
                for job in old_jobs:
                    jid = job.get("job_id") or job.get("source_url") or ""
                    if jid and jid not in existing_ids:
                        result["jobs"].append(job)
                        existing_ids.add(jid)
                merged_total = len(result["jobs"])
                result["total_matched"] = merged_total
                result["total_scraped"] = merged_total
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    task["result"] = result
                    task["error"] = result.get("error", "")
                    if merged_total is not None:
                        progress = dict(task.get("progress") or {})
                        progress["total_scraped"] = merged_total
                        progress["message"] = (
                            f"完成：抓取 {merged_total} 条，去重 {merged_total} 条"
                        )
                        progress["total_matched"] = merged_total
                        task["progress"] = progress
                    # 用户点过停止：无论 run_search 返回 ok 与否，都标 cancelled，
                    # 不标 failed（不是出错）也不标 done（不是正常完成）。
                    if stop_event is not None and stop_event.is_set():
                        task["status"] = "cancelled"
                        task["error"] = _MSG_USER_STOPPED_SCRAPE
                        _write_run_unless_finished(
                            task_id, status="cancelled", current_stage="scrape",
                            processed_count=len(result.get("completed_combos") or []),
                            error_reason=_MSG_USER_STOPPED_SCRAPE,
                        )
                    elif result.get("ok"):
                        task["status"] = "done"
                        completed = list(result.get("completed_combos") or [])
                        _write_run_unless_finished(
                            task_id, status="succeeded", current_stage="scrape",
                            processed_count=len(completed),
                            source_count=int(result.get("combinations") or len(completed)),
                            total_scraped=int(result.get("total_scraped") or 0),
                        )
                        store.append_task_event(
                            task_id, "stage_complete", {"stage": "scrape"}
                        )
                    else:
                        # 切片4：列表抓取失败时区分"系统性阻断暂停" vs "真失败"
                        # 部分组合已完成 + 错误含阻断关键字 → paused + checkpoint
                        # 服务重启后用户点继续可从 DB checkpoint 恢复 completed_combos
                        completed = list(result.get("completed_combos") or [])
                        err_msg = str(result.get("error", "") or "")
                        _pause_code = (
                            str(result.get("hard_stop_code") or "")
                            or _classify_scrape_block(err_msg)
                        )
                        if result.get("hard_stop") and _pause_code:
                            _write_run_unless_finished(
                                task_id, status="paused", error_code=_pause_code,
                                current_stage="scrape",
                                processed_count=len(completed),
                                source_count=int(result.get("combinations") or 0),
                                error_reason=err_msg,
                                total_scraped=int(result.get("total_scraped") or 0))
                            store.save_checkpoint(task_id, "scrape", completed)
                            store.append_task_event(
                                task_id, "pause",
                                {"stage": "scrape", "code": _pause_code,
                                 "completed_combos": len(completed)})
                            _record_pause_failure(
                                task_id, "scrape", _pause_code, err_msg,
                                processed=len(completed),
                                total=int(result.get("combinations") or 0),
                            )
                            task["status"] = "paused"
                            task["error"] = (
                                f"列表抓取被阻断（{_pause_code}）："
                                f"已完成 {len(completed)} 个组合，已保存断点。"
                                "在自动化浏览器中处理后点「继续」"
                            )
                        else:
                            task["status"] = "failed"
                            store.append_task_event(task_id, "job_fail", {
                                "stage": "scrape", "error": err_msg,
                                "failed_code": _pause_code or "scrape_failed",
                            })
                            _write_run_unless_finished(
                                task_id, status="failed", current_stage="scrape",
                                processed_count=len(completed),
                                source_count=int(result.get("combinations") or 0),
                                error_reason=err_msg,
                                total_scraped=int(result.get("total_scraped") or 0),
                            )
            with _pipeline_lock:
                _terminal_status = (_pipeline_tasks.get(task_id) or {}).get("status")
            if _terminal_status in ("cancelled", "failed"):
                _clear_auto_screen(task_id)
            _schedule_pipeline_task_cleanup(task_id)
            _release_worker_resume_claims(_pipeline_tasks.get(task_id))
        except Exception as exc:
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                stop_event = task.get("stop_event") if task is not None else None
            cancelled = stop_event is not None and stop_event.is_set()
            error_message = (
                _MSG_USER_STOPPED_SCRAPE if cancelled
                else f"执行异常：{type(exc).__name__}"
            )
            if not cancelled:
                record_failure(
                    store, task_id, stage="scrape",
                    error_code="internal_error", reason=error_message,
                    correlation_id=task_id,
                    diagnostics={}, exception=exc, include_traceback=True,
                )
            persistence_error = None
            try:
                run = store.get_screening_run(task_id)
                if run and run.get("status") in ("queued", "running", "paused"):
                    _write_run_unless_finished(
                        task_id,
                        status="cancelled" if cancelled else "failed",
                        current_stage="scrape",
                        error_reason=error_message,
                    )
            except _OPERATIONAL_ERRORS as persist_exc:
                persistence_error = type(persist_exc).__name__
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    if _is_user_finished(task_id):
                        task["status"] = "cancelled"
                        task["error"] = _MSG_USER_FINISHED
                    else:
                        task["status"] = (
                            "cancelled" if cancelled and persistence_error is None else "failed"
                        )
                        task["error"] = (
                            error_message if persistence_error is None
                            else f"{error_message}；状态保存失败：{persistence_error}"
                        )
            _schedule_pipeline_task_cleanup(task_id)
            _release_worker_resume_claims(_pipeline_tasks.get(task_id))

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

    def _build_partial_pipeline_result(
            source_jobs, verdicts, pending_rows, jd_map, profile_summary,
            source_dropped=None, total_scraped=None, platform="",
            profile_facts=None):
        """Build a displayable result snapshot from persisted partial work."""
        pending_reasons = {}
        pending_codes = {}
        for item in pending_rows or []:
            jid = str(item.get("job_id") or "")
            if not jid:
                continue
            payload = item.get("ai_payload") or {}
            pending_reasons[jid] = str(
                payload.get("reason") or item.get("failed_code") or "")
            pending_codes[jid] = str(item.get("failed_code") or "")
        jobs = []
        dropped = []
        for job in source_jobs or []:
            if not isinstance(job, dict):
                continue
            jid = str(job.get("job_id") or job.get("source_url") or "")
            vobj = verdicts.get(jid) or {}
            verdict = str(vobj.get("verdict") or "")
            reason = str(vobj.get("reason") or job.get("verdict_reason") or "")
            if verdict == "dropped":
                dropped.append({
                    "platform": platform,
                    "platform_job_id": str(job.get("platform_job_id") or jid),
                    "job_id": str(job.get("job_id") or "") or None,
                    "title": job.get("title") or "", "reason": reason or "粗筛移除",
                    "canonical_url": job.get("source_url") or job.get("job_link") or "",
                })
                continue
            jd = str(jd_map.get(jid) or job.get("jd") or "").strip()
            caveats = (
                vobj.get("caveats") if isinstance(vobj.get("caveats"), list)
                else (job.get("caveats") if isinstance(job.get("caveats"), list) else [])
            )
            flags = (
                vobj.get("flags") if isinstance(vobj.get("flags"), list)
                else (job.get("flags") if isinstance(job.get("flags"), list) else [])
            )
            if verdict in ("match", "not_match", "mismatch"):
                final_verdict = "not_match" if verdict == "mismatch" else verdict
                final_reason = reason
            elif jd:
                final_verdict = "uncertain"
                final_reason = reason or "已抓取 JD，精筛未完成（提前结束）"
            else:
                final_verdict = "uncertain"
                final_reason = (
                    pending_reasons.get(jid)
                    or reason
                    or "未开始抓取 JD（提前结束）"
                )
            jobs.append({
                "platform": platform,
                "platform_job_id": str(job.get("platform_job_id") or jid),
                "job_id": str(job.get("job_id") or "") or None,
                "title": job.get("title") or "",
                "company": job.get("company") or job.get("boss_name") or "",
                "salary": job.get("salary") or "",
                "location": job.get("location") or "",
                "tags": job.get("tags") or "",
                "jd": jd,
                "source_url": job.get("source_url") or job.get("job_link") or "",
                "verdict": final_verdict,
                "verdict_reason": final_reason,
                "caveats": caveats,
                "flags": flags,
                "failed_code": pending_codes.get(jid) or "",
            })
        dropped_ids = {str(item.get("platform_job_id") or item.get("job_id") or "") for item in dropped}
        for item in source_dropped or []:
            if not isinstance(item, dict):
                continue
            jid = str(item.get("platform_job_id") or item.get("job_id") or item.get("source_url") or "")
            if jid and jid in dropped_ids:
                continue
            dropped.append({
                "platform": platform,
                "platform_job_id": str(item.get("platform_job_id") or jid),
                "job_id": str(item.get("job_id") or "") or None,
                "title": item.get("title") or "",
                "reason": item.get("reason") or item.get("verdict_reason") or "粗筛移除",
                "canonical_url": item.get("canonical_url") or item.get("source_url") or "",
            })
        return {
            "ok": True,
            "jobs": jobs,
            "dropped": dropped,
            "total_scraped": (
                total_scraped if total_scraped is not None
                else len(source_jobs or []) + len(source_dropped or [])
            ),
            "total_kept": len(jobs),
            "total_matched": sum(1 for j in jobs if j.get("verdict") == "match"),
            "total_dropped": len(dropped),
            "profile_summary": profile_summary or "",
            "profile_facts": profile_facts,
            "error": "",
        }

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


    def _run_ai_screen_task(task_id, screening_fields, profile_summary,
                            scrape_task_id, resume_from_run_id="",
                            profile_facts=None):
        """AI 筛选任务：StageA 字段粗筛 → 批量抓 JD → StageB JD 精筛。

        读取最近一次原始抓取结果，两段式 AI 筛选后把带 verdict 的最终结果
        持久化到数据库（供结果页恢复）。

        全程进度落库（screening_runs）+ 中间产物落盘（JD 断点文件 /
        screening_results 判定）：进程重启或失败后，同一抓取任务再次发起
        筛选且条件一致时自动接着上次进度（``resume_from_run_id``）。
        """
        from webui.ai import match_jds, screen_jobs
        from webui.pipeline_exec import (
            close_debug_chrome,
            ensure_chrome_ready,
            failed_code_label,
            fetch_job_details,
        )

        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)
            if task is None:
                task = {
                    "kind": "ai_screen", "status": "queued", "progress": {},
                    "logs": [], "result": None, "error": "",
                    "source_task_id": scrape_task_id,
                    "started_at": int(time.time() * 1000), "finished_at": None,
                    "stop_event": threading.Event(),
                }
                _pipeline_tasks[task_id] = task
            if task.get("status") == "cancelled":
                # 排队期间已被用户取消：直接退出，别把 cancelled 覆盖成 running
                _release_worker_resume_claims(task)
                return
            if _is_user_finished(task_id):
                _release_worker_resume_claims(task)
                return
            task["status"] = "running"
            stop_event = task.get("stop_event")
            if resume_from_run_id and not task.get("resumed_from"):
                task["resumed_from"] = resume_from_run_id

        # B033：续跑时画像事实以该轮快照为准；请求未携带（前端恢复失败、
        # 空对象等）时回退 execution_params 中的快照值，避免三通道退化。
        if resume_from_run_id and not profile_facts:
            try:
                _prev_run = store.get_screening_run(resume_from_run_id)
                _prev_params = (_prev_run or {}).get("execution_params") or {}
                if not profile_facts:
                    profile_facts = _prev_params.get("profile_facts")
            except _OPERATIONAL_ERRORS:
                pass

        _activate_task_browser(task_id)

        last_event_stage = None

        def emit(**kw):
            nonlocal last_event_stage
            stage = str(kw.get("stage", ""))
            current = int(kw.get("current") or 0)
            total = int(kw.get("total") or 0)
            kw["overall_percent"] = _screen_overall_percent(stage, current, total)
            # 调用方传了具体 message（如"筛选完成：匹配 X 条"）就优先用；没传才回退默认文案
            if not kw.get("message"):
                kw["message"] = _SCREEN_STAGE_MESSAGES.get(stage, "")
            event_stage = _EVENT_STAGE_NAMES.get(stage)
            stage_events = []
            if stage == "done" and last_event_stage:
                stage_events.append(("stage_complete", {"stage": last_event_stage}))
                last_event_stage = None
            elif event_stage and event_stage != last_event_stage:
                if last_event_stage:
                    stage_events.append(("stage_complete", {"stage": last_event_stage}))
                stage_events.append(("stage_start", {"stage": event_stage}))
                last_event_stage = event_stage
            if stage_events:
                store.append_task_events(task_id, stage_events)
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is None:
                    return
                task["progress"] = kw
                msg = kw.get("message")
                if msg:
                    task["logs"].append(msg)

        def _stop_requested():
            return stop_event is not None and stop_event.is_set()


        def _try_save_failure_snapshot(terminal_status: str) -> str | None:
            """Save a result snapshot before a failed/cancelled terminal write.

            Only runs that already produced jobs or dropped rows become history.
            """
            try:
                if store.history_round_exists(task_id):
                    return None
                if _is_user_finished(task_id):
                    return None
                if any(event["type"] == "history_snapshot" for event in store.list_task_events(task_id)):
                    return None
                in_memory = None
                try:
                    if isinstance(result, dict) and (result.get("jobs") or result.get("dropped")):
                        in_memory = result
                except (NameError, UnboundLocalError):
                    in_memory = None
                run = store.get_screening_run(task_id) or {}
                params = dict(run.get("execution_params") or {})
                platform = str(params.get("platform") or run.get("platform") or frozen_platform or "boss")
                profile = str(params.get("profile_summary") or profile_summary or "")
                source_jobs = []
                source_dropped = []
                total_scraped = None
                if in_memory is not None:
                    source_jobs = in_memory.get("jobs") or []
                    source_dropped = in_memory.get("dropped") or []
                    total_scraped = in_memory.get("total_scraped")
                else:
                    source_run_id = str(params.get("source_run_id") or "")
                    if scrape_task_id:
                        source_jobs = store.load_scrape_run_jobs(scrape_task_id)
                    elif source_run_id:
                        payload = store.load_latest_pipeline_result(source_run_id)
                        source_jobs = ((payload or {}).get("result") or {}).get("jobs") or []
                        source_dropped = ((payload or {}).get("result") or {}).get("dropped") or []
                        total_scraped = ((payload or {}).get("result") or {}).get("total_scraped")
                    if not source_jobs and not source_dropped:
                        return None
                    verdicts = store.load_screening_verdicts(task_id)
                    pending_rows = store.load_screening_pending(task_id)
                    if not verdicts and not pending_rows:
                        return None
                    jd_map = {}
                    try:
                        jd_map = _load_jd_checkpoint(
                            _jd_checkpoint_path(app.config["RESULT_DIR"], task_id))
                    except RuntimeError:
                        jd_map = {}
                    result_to_save = _build_partial_pipeline_result(
                        source_jobs, verdicts, pending_rows, jd_map, profile,
                        source_dropped=source_dropped,
                        total_scraped=total_scraped,
                        platform=platform,
                    )
                snapshot_result = result_to_save if in_memory is None else in_memory
                snapshot_id = store.save_pipeline_result(
                    snapshot_result,
                    {"screening": run.get("frozen_filters") or {}, "platform": platform},
                    started_at=run.get("started_at") or task.get("started_at"),
                    finished_at=int(time.time() * 1000),
                    execution_config=params.get("execution_config") or {},
                    status=terminal_status,
                    execution_params={
                        "platform": platform,
                        "scrape_task_id": scrape_task_id,
                    },
                )
                store.append_task_event(task_id, "history_snapshot", {
                    "snapshot_run_id": snapshot_id,
                    "status": terminal_status,
                    "jobs": len(snapshot_result.get("jobs") or []),
                    "dropped": len(snapshot_result.get("dropped") or []),
                })
                _prune_history_best_effort()
                return snapshot_id
            except Exception:
                return None  # 快照兜底失败不掩盖原始终态错误
        def _mark_cancelled():
            """用户取消：标 cancelled（不覆盖为 done/failed），落清理定时。"""
            _try_save_failure_snapshot("cancelled")
            _write_run_unless_finished(
                task_id, status="cancelled", error_reason=_MSG_USER_STOPPED_SCREEN
            )
            with _pipeline_lock:
                t = _pipeline_tasks.get(task_id)
                if t is not None:
                    t["status"] = "cancelled"
                    t["error"] = _MSG_USER_STOPPED_SCREEN
                    _release_worker_resume_claims(t)
            _schedule_pipeline_task_cleanup(task_id)

        try:
            # 1) 只读取请求明确绑定的抓取任务，避免另一标签页或另一轮抓取
            # 覆盖全局 latest 文件后导致筛选对象串线。
            with _pipeline_lock:
                source_task = _pipeline_tasks.get(scrape_task_id)
                source_result = (
                    source_task.get("result")
                    if isinstance(source_task, dict) else None
                )
            if not isinstance(source_result, dict):
                raise RuntimeError("invalid_scrape_task")
            source_run = store.get_screening_run(scrape_task_id)
            source_params = (
                source_run.get("execution_params")
                if isinstance(source_run, dict) else None
            ) or {}
            # T403: 从父 scrape run 继承冻结平台/浏览器身份
            frozen_platform = (
                task.get("platform")
                or source_params.get("platform")
                or (source_run or {}).get("platform")
                or "boss"
            )
            frozen_cdp_port = (
                task.get("cdp_port") or source_params.get("cdp_port")
            )
            frozen_profile_key = (
                task.get("profile_key") or source_params.get("profile_key")
            )
            frozen_browser_account = (
                task.get("browser_account")
                or source_params.get("browser_account")
            )
            ai_task_input_digest = (
                task.get("task_input_digest")
                or source_params.get("task_input_digest")
                or (source_run or {}).get("task_input_digest")
            )
            from webui.execution_config import (
                ExecutionConfigSnapshot,
                FrozenTaskScope,
            )
            try:
                execution_config = ExecutionConfigSnapshot.from_dict(
                    source_params["execution_config"]
                )
                frozen_scope = FrozenTaskScope.from_dict(
                    source_params["frozen_scope"]
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("frozen_snapshot_missing") from exc
            with _pipeline_lock:
                task["config_digest"] = execution_config.config_digest
                task["scope_digest"] = frozen_scope.scope_digest
            # 列表抓取遇到系统性阻断（验证码/登录失效/IP风控）：暂停，不标 failed
            if source_result.get("hard_stop"):
                _hs_code = source_result.get("hard_stop_code") or "source_blocked"
                _completed_combos = source_result.get("completed_combos") or []
                store.create_screening_run(
                    task_id,
                    frozen_filters=screening_fields,
                    source_count=len(source_result.get("jobs") or []),
                    execution_params={"scrape_task_id": scrape_task_id,
                                      "profile_summary": profile_summary or "",
                                      "profile_facts": profile_facts,
                                      "browser_account": frozen_browser_account or _account_for_run(),
                                      "execution_config": execution_config.to_dict(),
                                      "frozen_scope": frozen_scope.to_dict(),
                                      "platform": frozen_platform,
                                      "cdp_port": frozen_cdp_port,
                                      "profile_key": frozen_profile_key,
                                      "task_input_digest": ai_task_input_digest},
                    backend_version=_backend_version)
                store.save_filter_snapshot(
                    task_id,
                    platform=frozen_platform,
                    task_input_digest=ai_task_input_digest,
                )
                _write_run_unless_finished(
                    task_id, status="running", current_stage="scrape"
                )
                _write_run_unless_finished(
                    task_id, status="paused", error_code=_hs_code,
                    current_stage="scrape")
                # 保存已完成组合 checkpoint（继续时跳过）
                store.save_checkpoint(task_id, "scrape", _completed_combos)
                store.append_task_event(
                    task_id, "pause",
                    {"stage": "scrape", "code": _hs_code,
                     "completed_combos": len(_completed_combos),
                     "total_combos": source_result.get("combinations") or 0})
                _record_pause_failure(
                    task_id, "scrape", _hs_code,
                    str(source_result.get("error") or "") or
                    f"列表抓取被阻断（{_hs_code}）",
                    processed=len(_completed_combos),
                    total=int(source_result.get("combinations") or 0),
                )
                with _pipeline_lock:
                    t = _pipeline_tasks.get(task_id)
                    if t is not None:
                        t["status"] = "paused"
                        t["error"] = (
                            f"列表抓取被阻断（{_hs_code}）："
                            f"已完成 {len(_completed_combos)}/"
                            f"{source_result.get('combinations') or 0} 个组合。"
                            "处理完成后点「继续」"
                        )
                _release_worker_resume_claims(_pipeline_tasks.get(task_id))
                return
            if not source_result.get("ok"):
                raise RuntimeError("invalid_scrape_task")
            raw_jobs = [
                dict(job) for job in source_result.get("jobs", [])
                if isinstance(job, dict)
            ]
            for job in raw_jobs:
                job["job_id"] = str(job.get("platform_job_id") or job.get("job_id") or job.get("id") or "")
                job.setdefault("platform_job_id", job["job_id"])
            if not raw_jobs:
                raise RuntimeError("empty_scrape_result")

            # 任务身份和断点必须先可靠落库；否则继续调用 AI 会产生无法恢复、
            # 无法审计的孤儿工作。
            if resume_from_run_id != task_id:
                store.create_screening_run(
                    task_id,
                    frozen_filters=screening_fields,
                    source_count=len(raw_jobs),
                    execution_params={
                        "scrape_task_id": scrape_task_id,
                        "profile_summary": profile_summary,
                        "profile_facts": profile_facts,
                        "browser_account": frozen_browser_account or _account_for_run(),
                        "execution_config": execution_config.to_dict(),
                        "frozen_scope": frozen_scope.to_dict(),
                        "platform": frozen_platform,
                        "cdp_port": frozen_cdp_port,
                        "profile_key": frozen_profile_key,
                        "task_input_digest": ai_task_input_digest,
                    },
                    backend_version=_backend_version,
                )
                store.save_filter_snapshot(
                    task_id,
                    platform=frozen_platform,
                    task_input_digest=ai_task_input_digest,
                )
                _write_run_unless_finished(
                    task_id, status="running", current_stage="ai_rough"
                )
            else:
                resumed_run = store.get_screening_run(task_id)
                if resumed_run is None or resumed_run.get("status") != "running":
                    raise RuntimeError("resume_run_not_claimed")

            # 载入断点（同一抓取任务、同一筛选条件下的上次进度）
            resume_verdicts = {}
            resume_jd = {}
            if resume_from_run_id:
                resume_verdicts = store.load_screening_verdicts(resume_from_run_id)
                old_jd_path = _jd_checkpoint_path(
                    app.config["RESULT_DIR"], resume_from_run_id)
                resume_jd = _load_jd_checkpoint(old_jd_path)
                # 新 run 继承旧 run 时删除旧文件；原 run 就地继续时保留同一断点，
                # 避免在首次新批次落盘前退出导致已抓 JD 丢失。
                if resume_from_run_id != task_id:
                    _remove_jd_checkpoint(old_jd_path)
                if resume_verdicts or resume_jd:
                    emit(stage="resume",
                         message=f"接着上次进度：已有 {len(resume_verdicts)} 条判定、"
                                 f"{len(resume_jd)} 条 JD，跳过重复工作")
            resume_fine_verdicts = {}
            if resume_from_run_id:
                resume_fine_verdicts, _ = _split_resume_verdicts(resume_verdicts)

            # 2) AI 凭据
            settings = store.get_ai_settings()
            cred_ref = store.get_credential_ref()
            api_key = ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
            endpoint = settings.get("endpoint_url", "")
            model = settings.get("model", "")
            if not ai_service.is_ai_available(settings, cred_ref, api_key) or not endpoint:
                raise ai_service.AISecurityError(ai_service.ERROR_NOT_CONFIGURED)

            criteria = dict(screening_fields or {})
            criteria["profile_summary"] = profile_summary or ""

            if _stop_requested():
                _mark_cancelled()
                return

            # 3) Stage A：字段粗筛（移除明显不符，学历向下兼容）
            emit(stage="screen_a", current=0, total=len(raw_jobs),
                 message="AI 粗筛中（对照筛选字段）…")

            def _a_progress(cur, tot):
                emit(stage="screen_a", current=cur, total=tot,
                     message=f"AI 粗筛 {cur}/{tot}")

            # 切片6：粗筛继续时跳过已判定的 job_id（从 checkpoint + DB 真实 verdict 恢复）
            _rough_done_ids: set[str] = set()
            _resume_verdicts: dict[str, str] = {}  # job_id → verdict（真实判定）
            if resume_from_run_id:
                _rough_done_ids = store.load_checkpoint(
                    resume_from_run_id, "ai_rough"
                )
                # 从 DB 查真实 verdict，不默认全部 kept
                _db_verdicts = store.load_screening_verdicts(resume_from_run_id)
                for _jid, _vobj in _db_verdicts.items():
                    _resume_verdicts[_jid] = (
                        _vobj.get("verdict", "") if isinstance(_vobj, dict) else ""
                    )
            _rough_todo = [j for j in raw_jobs
                           if str(j.get("job_id", "")) not in _rough_done_ids] if _rough_done_ids else raw_jobs
            # 粗筛历史使用 kept/dropped；兼容早期写成 match 的记录。
            _rough_kept_from_resume = [
                j for j in raw_jobs
                if str(j.get("job_id", "")) in _rough_done_ids
                and _resume_verdicts.get(str(j.get("job_id", "")), "")
                in (_FINE_VERDICTS | {"kept"})
            ] if _rough_done_ids else []
            _rough_completed_ids = set(_rough_done_ids)

            try:
                def _rough_batch_done(batch_verdicts, completed_job_ids):
                    completed_snapshot = _rough_completed_ids | set(completed_job_ids)
                    store.save_verdict_and_checkpoint_atomic(
                        task_id, "ai_rough", batch_verdicts,
                        sorted(completed_snapshot),
                    )
                    _rough_completed_ids.clear()
                    _rough_completed_ids.update(completed_snapshot)

                screen_result = screen_jobs(_rough_todo, criteria, endpoint, api_key,
                                            model=model, progress=_a_progress,
                                            raise_on_systemic=True,
                                            on_batch_done=_rough_batch_done,
                                            execution_config=execution_config,
                                            correlation_id=task_id)
            except (ai_service.AISecurityError, ai_service.AICheckpointError) as _ai_exc:
                # AISecurityError（systemic）：暂停整任务，保存 checkpoint
                from webui.ai import (
                    AICheckpointError,
                    AISecurityError,
                    map_ai_error_to_block_code,
                )
                _block_code = ""
                if isinstance(_ai_exc, AICheckpointError):
                    _block_code = "internal_error"
                elif isinstance(_ai_exc, AISecurityError):
                    _block_code = map_ai_error_to_block_code(_ai_exc.error_code)
                if _block_code:
                    # 暂停状态、真实进度、checkpoint 和事件必须全部可靠落库；
                    # 任一步失败都交给外层 internal_error 路径，不能只改内存。
                    _done_keys = sorted(_rough_completed_ids)
                    _write_run_unless_finished(
                        task_id, status="paused", error_code=_block_code,
                        current_stage="ai_rough",
                        processed_count=len(_done_keys))
                    store.save_checkpoint(task_id, "ai_rough", _done_keys)
                    store.append_task_event(
                        task_id, "pause",
                        {"stage": "ai_rough", "code": _block_code,
                         "processed": len(_done_keys), "total": len(raw_jobs)})
                    _record_pause_failure(
                        task_id, "ai_rough", _block_code,
                        failed_code_label(_block_code, frozen_platform) or _block_code,
                        processed=len(_done_keys), total=len(raw_jobs),
                        exception=_ai_exc,
                    )
                    with _pipeline_lock:
                        t = _pipeline_tasks.get(task_id)
                        if t is not None:
                            t["status"] = "paused"
                            t["error"] = (
                                f"AI 粗筛被阻断（{_block_code}）："
                                f"已处理 {len(_rough_completed_ids)}/{len(raw_jobs)} 条。"
                                "处理完成后点「继续」"
                            )
                    _release_worker_resume_claims(_pipeline_tasks.get(task_id))
                    return
                raise  # 非 systemic，往上抛
            if _stop_requested():
                _mark_cancelled()
                return
            # 合并 resume 已判定的结果（resume 的岗位默认 kept，因为上次没被 drop）
            kept_ids = set(screen_result["kept"]) | {str(j.get("job_id", "")) for j in _rough_kept_from_resume}
            # 粗筛成功完成：保存全部已判定 job_id（用于未来继续时跳过）
            dropped_by_id = {
                str(d.get("job_id") or ""): d for d in (screen_result.get("dropped") or [])
            }
            if resume_from_run_id:
                for item in _resume_dropped_from_verdicts(raw_jobs, resume_verdicts):
                    dropped_by_id.setdefault(str(item.get("job_id") or ""), item)
            dropped = list(dropped_by_id.values())
            store.save_checkpoint(
                task_id,
                "ai_rough",
                list(kept_ids | {str(d.get("job_id") or "") for d in dropped}),
            )
            survivors = [j for j in raw_jobs if str(j.get("job_id", "")) in kept_ids]
            emit(stage="screen_a_done", kept=len(survivors), dropped=len(dropped),
                 message=f"粗筛完成：保留 {len(survivors)} 条，移除 {len(dropped)} 条")
            _write_run_unless_finished(
                task_id, status="running", source_cursor=0,
                total_kept=len(survivors), total_dropped=len(dropped),
            )

            # 4) 对保留的岗位分段抓 JD（重开调试浏览器，抓完关闭）。
            # 每段落盘 JD 断点文件 + 更新 source_cursor：进程崩了已抓的也不丢，
            # 重跑（含登录墙后重试）自动跳过已抓岗位。
            enriched = [dict(job) for job in survivors]
            jd_path = _jd_checkpoint_path(app.config["RESULT_DIR"], task_id)
            jd_map = dict(resume_jd)
            jd_failures: dict[str, dict[str, str]] = {}
            if survivors:
                emit(stage="ensure_chrome", message="启动调试浏览器，准备抓取 JD…")
                chrome_ok, chrome_err = ensure_chrome_ready(
                    frozen_cdp_port, minimize_after_launch=True,
                )
                if not chrome_ok:
                    reason = f"调试浏览器未就绪（{chrome_err}），请处理后继续"
                    _save_jd_checkpoint(jd_path, jd_map)
                    _write_run_unless_finished(
                        task_id, status="paused", error_code="cdp_unavailable",
                        current_stage="jd_detail", processed_count=len(jd_map),
                        error_reason=reason,
                    )
                    store.save_checkpoint(
                        task_id, "jd_detail", sorted(jd_map)
                    )
                    store.append_task_event(task_id, "pause", {
                        "stage": "jd_detail", "code": "cdp_unavailable",
                        "processed": len(jd_map), "total": len(survivors),
                    })
                    _record_pause_failure(
                        task_id, "jd_detail", "cdp_unavailable", reason,
                        processed=len(jd_map), total=len(survivors),
                    )
                    with _pipeline_lock:
                        t = _pipeline_tasks.get(task_id)
                        if t is not None:
                            t["status"] = "paused"
                            t["error"] = reason
                    _release_worker_resume_claims(_pipeline_tasks.get(task_id))
                    return
                source = _make_cdp_source(
                    platform=frozen_platform,
                    browser_account=frozen_browser_account,
                    cdp_port=frozen_cdp_port,
                    profile_key=frozen_profile_key,
                    run_id=task_id,
                )
                if source is None:
                    reason = "CDP 抓取源不可用，请确认调试浏览器后继续"
                    _save_jd_checkpoint(jd_path, jd_map)
                    _write_run_unless_finished(
                        task_id, status="paused", error_code="cdp_unavailable",
                        current_stage="jd_detail", processed_count=len(jd_map),
                        error_reason=reason,
                    )
                    store.save_checkpoint(
                        task_id, "jd_detail", sorted(jd_map)
                    )
                    store.append_task_event(task_id, "pause", {
                        "stage": "jd_detail", "code": "cdp_unavailable",
                        "processed": len(jd_map), "total": len(survivors),
                    })
                    _record_pause_failure(
                        task_id, "jd_detail", "cdp_unavailable", reason,
                        processed=len(jd_map), total=len(survivors),
                    )
                    with _pipeline_lock:
                        t = _pipeline_tasks.get(task_id)
                        if t is not None:
                            t["status"] = "paused"
                            t["error"] = reason
                    _release_worker_resume_claims(_pipeline_tasks.get(task_id))
                    return

                todo_jd = [j for j in survivors
                           if str(j.get("job_id", "")) not in jd_map]
                emit(stage="fetch_jd", current=len(jd_map), total=len(survivors),
                     message=f"抓取 JD（{len(jd_map)}/{len(survivors)}）…")
                DETAIL_CHUNK = max(1, int(execution_config.detail_batch_size))
                for chunk_start in range(0, len(todo_jd), DETAIL_CHUNK):
                    if _stop_requested():
                        close_debug_chrome(frozen_cdp_port)
                        _mark_cancelled()
                        return
                    chunk = todo_jd[chunk_start:chunk_start + DETAIL_CHUNK]
                    _jd_base = len(jd_map)

                    def _jd_progress(done, total, _base=_jd_base):
                        cur = min(_base + done, len(survivors))
                        emit(stage="fetch_jd", current=cur, total=len(survivors),
                             message=f"抓取 JD {cur}/{len(survivors)}")

                    detail_result = fetch_job_details(
                        chunk, source,
                        artifact_dir=app.config["RESULT_DIR"],
                        stop_event=stop_event,
                        progress=_jd_progress,
                        execution_config=execution_config)
                    for j in detail_result["jobs"]:
                        jid = str(j.get("job_id", ""))
                        jd = str(j.get("jd", "")).strip()
                        if jid and jd:
                            jd_map[jid] = jd
                            jd_failures.pop(jid, None)
                        elif jid and j.get("jd_failed_code"):
                            jd_failures[jid] = {
                                "code": str(j.get("jd_failed_code")),
                                "reason": str(j.get("jd_failed_reason") or ""),
                            }
                    _save_jd_checkpoint(jd_path, jd_map)
                    _write_run_unless_finished(
                        task_id, source_cursor=len(jd_map),
                        processed_count=len(jd_map), current_stage="jd_detail",
                    )
                    emit(stage="fetch_jd",
                         current=min(len(jd_map), len(survivors)), total=len(survivors),
                         message=f"抓取 JD {min(len(jd_map), len(survivors))}/{len(survivors)}")
                    if detail_result.get("hard_stop"):
                        # 源级硬信号：暂停，不关浏览器（用户需要它处理验证码/登录）
                        _hs_code = detail_result.get("hard_stop_code") or "source_blocked"
                        _hs_label = failed_code_label(_hs_code, frozen_platform)
                        _hs_hint = next((
                            str(job.get("jd_failed_reason") or "").strip()
                            for job in detail_result.get("jobs") or []
                            if job.get("jd_failed_reason")
                        ), "")
                        _hs_reason = _hs_hint if _hs_hint and _hs_hint != _hs_label else _hs_label
                        _persist_jd_job_failures(
                            task_id,
                            detail_result.get("jobs") or [],
                            stage="jd_detail",
                            platform=frozen_platform,
                        )
                        _write_run_unless_finished(
                            task_id, status="paused", error_code=_hs_code,
                            current_stage="jd_detail",
                            processed_count=len(jd_map), error_reason=_hs_reason,
                        )
                        _record_pause_failure(
                            task_id, "jd_detail", _hs_code, _hs_reason,
                            processed=len(jd_map), total=len(survivors),
                        )
                        with _pipeline_lock:
                            t = _pipeline_tasks.get(task_id)
                            if t is not None:
                                t["status"] = "paused"
                                t["error"] = (
                                    f"抓取 JD 时{_hs_reason}：已抓 "
                                    f"{len(jd_map)}/{len(survivors)} 条（已保存）。"
                                    "请在自动化浏览器中处理，完成后点「继续」"
                                )
                        _release_worker_resume_claims(_pipeline_tasks.get(task_id))
                        return
                    if detail_result.get("stopped"):
                        close_debug_chrome(frozen_cdp_port)
                        _mark_cancelled()
                        return
                close_debug_chrome(frozen_cdp_port)
            for job in enriched:
                jid = str(job.get("job_id", ""))
                job["jd"] = jd_map.get(jid, "")
                failure = jd_failures.get(jid)
                if not job["jd"] and failure:
                    job["jd_failed_code"] = failure["code"]
                    job["jd_failed_reason"] = failure["reason"]

            # 5) Stage B：JD 精筛（对比候选人画像）
            jobs_with_jd = [j for j in enriched if str(j.get("jd", "")).strip()]
            if not profile_summary.strip():
                # 无画像时跳过精筛，全部标记待人工确认
                for job in enriched:
                    job["verdict"] = "uncertain"
                    job["verdict_reason"] = "未填写求职画像，跳过 AI 精筛"
                match_count = 0
                # 跳过精筛：一条未判，进度必须从 0 起（写 len(jobs_with_jd)
                # 会造成 30/30 + 100% 假进度，且 task-state 的 max() 会钉死它）
                emit(stage="screen_b", current=0, total=len(jobs_with_jd),
                     message="未填写求职画像，已跳过 AI 精筛")
            else:
                if _stop_requested():
                    _mark_cancelled()
                    return
                # 分段精筛，每段判定落库（screening_results）+ 更新 processed_count：
                # 进程崩了已筛的判定不丢，重跑自动跳过。
                done_verdicts = dict(resume_fine_verdicts)
                # 切片6：从 checkpoint 恢复已判定 job_id（resume 场景）
                if resume_from_run_id:
                    _fine_done = store.load_checkpoint(
                        resume_from_run_id, "ai_fine"
                    )
                    if _fine_done:
                        _extra = {
                            jid: {
                                "verdict": "uncertain",
                                "reason": "从断点恢复，待重新判定",
                            }
                            for jid in _fine_done if jid not in done_verdicts
                        }
                        done_verdicts.update(_extra)
                todo_match = [j for j in jobs_with_jd
                              if str(j.get("job_id", "")) not in done_verdicts]
                no_jd_pending = len(enriched) - len(jobs_with_jd)
                _write_run_unless_finished(
                    task_id, current_stage="ai_fine",
                    processed_count=len(done_verdicts),
                    pending_count=no_jd_pending,
                )
                emit(stage="screen_b",
                     current=min(len(done_verdicts), len(jobs_with_jd)),
                     total=len(jobs_with_jd),
                     message="AI 精筛中（JD 对比简历画像）…")
                def _fine_progress(cur, tot):
                    emit(stage="screen_b",
                         current=min(len(done_verdicts) + cur, len(jobs_with_jd)),
                         total=len(jobs_with_jd),
                         message=f"AI 精筛 {min(len(done_verdicts) + cur, len(jobs_with_jd))}/{len(jobs_with_jd)}")

                def _fine_batch_done(batch_verdicts, completed_job_ids):
                    nonlocal done_verdicts
                    next_verdicts = dict(done_verdicts)
                    next_verdicts.update(batch_verdicts)
                    # verdict 与 checkpoint 同事务提交。任一步失败都必须停止，
                    # 不能继续下一批 AI 后再把内存结果伪装成可恢复进度。
                    store.save_verdict_and_checkpoint_atomic(
                        task_id, "ai_fine", batch_verdicts,
                        list(next_verdicts.keys()),
                    )
                    _write_run_unless_finished(
                        task_id, processed_count=len(next_verdicts))
                    done_verdicts = next_verdicts
                    emit(stage="screen_b",
                         current=min(len(done_verdicts), len(jobs_with_jd)),
                         total=len(jobs_with_jd),
                         message=f"AI 精筛 {min(len(done_verdicts), len(jobs_with_jd))}/{len(jobs_with_jd)}")

                try:
                    match_result = match_jds(
                        todo_match, profile_summary, endpoint, api_key,
                        model=model, raise_on_systemic=True,
                        criteria=criteria, profile_facts=profile_facts,
                        progress=_fine_progress,
                        on_batch_done=_fine_batch_done,
                        execution_config=execution_config,
                        correlation_id=task_id)
                except ai_service.AISecurityError as _ai_exc:
                    # 切片6：systemic 错误暂停整任务（不批量变 uncertain 后完成）
                    from webui.ai import AISecurityError, map_ai_error_to_block_code
                    if isinstance(_ai_exc, AISecurityError):
                        _block_code = map_ai_error_to_block_code(_ai_exc.error_code)
                        if _block_code:
                            _write_run_unless_finished(
                                task_id, status="paused", error_code=_block_code,
                                current_stage="ai_fine",
                                processed_count=len(done_verdicts))
                            store.save_checkpoint(
                                task_id, "ai_fine", list(done_verdicts.keys()))
                            store.append_task_event(
                                task_id, "pause",
                                {"stage": "ai_fine", "code": _block_code,
                                 "processed": len(done_verdicts),
                                 "total": len(jobs_with_jd)})
                            _record_pause_failure(
                                task_id, "ai_fine", _block_code,
                                failed_code_label(_block_code, frozen_platform) or _block_code,
                                processed=len(done_verdicts),
                                total=len(jobs_with_jd), exception=_ai_exc,
                            )
                            with _pipeline_lock:
                                t = _pipeline_tasks.get(task_id)
                                if t is not None:
                                    t["status"] = "paused"
                                    t["error"] = (
                                        f"AI 精筛被阻断（{_block_code}）："
                                        f"已判定 {len(done_verdicts)}/{len(jobs_with_jd)} 条。"
                                        "处理完成后点「继续」"
                                    )
                            _release_worker_resume_claims(_pipeline_tasks.get(task_id))
                            return
                    raise  # 非 systemic，往上抛
                # 兜底：末轮重试等未触发 on_batch_done 的新判定仍须落库。
                _pending_fine_verdicts = {
                    jid: verdict for jid, verdict in (match_result.get("verdicts") or {}).items()
                    if jid not in done_verdicts
                }
                if _pending_fine_verdicts:
                    _fine_batch_done(
                        _pending_fine_verdicts,
                        list(done_verdicts) + list(_pending_fine_verdicts))
                verdicts = done_verdicts
                for job in enriched:
                    jid = str(job.get("job_id", ""))
                    v = verdicts.get(jid)
                    if v:
                        job["verdict"] = v["verdict"]
                        job["verdict_reason"] = v["reason"]
                        # flags（靠谱判定）独立透传前端；中危降级项已并入 caveats
                        job["caveats"] = v.get("caveats") or []
                        job["flags"] = v.get("flags") or []
                    else:
                        # 未抓到 JD 的岗位无法精筛，标记待定（不红不绿）
                        job["verdict"] = "uncertain"
                        code = job.get("jd_failed_code", "")
                        label = failed_code_label(code, frozen_platform)
                        detail_reason = str(job.get("jd_failed_reason") or "").strip()
                        if detail_reason:
                            job["verdict_reason"] = f"未抓到 JD（{detail_reason}），无法精筛"
                        elif label:
                            job["verdict_reason"] = f"未抓到 JD（{label}），无法精筛"
                        else:
                            job["verdict_reason"] = "未抓到 JD，无法精筛"

                match_count = sum(1 for j in enriched if j.get("verdict") == "match")
            if _stop_requested():
                _mark_cancelled()
                return
            # 实时结果必须带平台身份：结果页按 job.platform 做平台筛选，
            # 内存结果缺 platform 会把全部岗位过滤成 0（重启走 DB 恢复才有值）。
            # 抓取脚本产物不带 platform，此处按任务冻结平台权威回填。
            for job in enriched:
                job.setdefault("platform", frozen_platform)
            for job in dropped:
                job.setdefault("platform", frozen_platform)
            result = {
                "ok": True,
                "jobs": enriched,
                "dropped": dropped,
                "total_scraped": len(raw_jobs),
                "total_kept": len(enriched),
                "total_matched": match_count,
                "total_dropped": len(dropped),
                "profile_summary": profile_summary,
                "profile_facts": profile_facts,
                "error": "",
            }
            job_events = []
            for job in enriched:
                verdict = job.get("verdict")
                is_failure = verdict == "uncertain"
                job_events.append((
                    "job_fail" if is_failure else "job_success",
                    {
                        "stage": "ai_fine",
                        "job_id": str(job.get("job_id") or ""),
                        "verdict": verdict,
                        "failed_code": (
                            job.get("failed_code") or job.get("jd_failed_code")
                        ) if is_failure else None,
                        "reason": job.get("verdict_reason", ""),
                    },
                ))
            store.append_task_events(task_id, job_events)
            # B038: 命中已保存的未筛选轮则原地升级（同一 run_id），否则新建。
            source_run_id = save_screen_result(
                store, result, {"screening": screening_fields, "platform": frozen_platform},
                scrape_task_id=scrape_task_id,
                status="done",
                execution_config=execution_config.to_dict(),
                platform=frozen_platform,
                started_at=task.get("started_at"),
                finished_at=int(time.time() * 1000),
            )
            result["source_run_id"] = source_run_id
            _prune_history_best_effort()
            try:
                _saved_run = store.get_screening_run(source_run_id) or {}
                store.append_task_event(task_id, "history_snapshot", {
                    "snapshot_run_id": source_run_id,
                    "status": _saved_run.get("status") or "done",
                    "jobs": len(enriched),
                    "dropped": len(dropped),
                })
            except _OPERATIONAL_ERRORS:
                pass
            mismatch_count = sum(
                1 for job in enriched if job.get("verdict") == "not_match"
            )
            pending_count = sum(
                1 for job in enriched
                if job.get("verdict") not in ("match", "not_match", "mismatch")
            )
            processed_count = match_count + mismatch_count
            terminal_message = (
                f"筛选完成，但有 {pending_count} 条待确认：匹配 {match_count} 条"
                if pending_count else f"筛选完成：匹配 {match_count} 条"
            )
            # 最终事件也是持久化契约的一部分；先写事件，再提交终态，避免
            # DB 已终态后事件写失败造成内存 failed / DB completed 分裂。
            emit(stage="done", total_matched=match_count,
                 message=terminal_message)
            _write_run_unless_finished(
                task_id,
                match_count=match_count,
                mismatch_count=mismatch_count,
                pending_count=pending_count,
                processed_count=processed_count,
                total_scraped=len(raw_jobs),
                total_kept=len(enriched),
                total_dropped=len(dropped),
                current_stage="done",
            )
            if _is_user_finished(task_id):
                final_db_status = "partial"
            else:
                final_db_status = store.finalize_run_status(task_id)
                if final_db_status not in ("succeeded", "partial"):
                    if _is_user_finished(task_id):
                        final_db_status = "partial"
                    else:
                        raise RuntimeError(
                            f"invalid_ai_terminal_status:{final_db_status}"
                        )
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    task["result"] = result
                    task["status"] = "done"
            _schedule_pipeline_task_cleanup(task_id)
            _release_worker_resume_claims(_pipeline_tasks.get(task_id))
            # 任务成功：断点文件使命完成（续跑只服务失败/取消/中断）
            _remove_jd_checkpoint(jd_path)
        except ai_service.AISecurityError as exc:
            _try_save_failure_snapshot(
                "cancelled" if _is_user_finished(task_id) else "failed")
            error_message = ai_service.user_facing_error(exc.error_code)
            if not _is_user_finished(task_id):
                record_failure(
                    store, task_id, stage="ai_screen",
                    error_code=exc.error_code, reason=error_message,
                    correlation_id=task_id,
                    diagnostics=dict(getattr(exc, "diagnostics", None) or {}),
                    exception=exc,
                )
            persistence_error = None
            try:
                _write_run_unless_finished(
                    task_id, status="failed", error_code=exc.error_code,
                    error_reason=error_message,
                )
            except _OPERATIONAL_ERRORS as persist_exc:
                persistence_error = type(persist_exc).__name__
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    if _is_user_finished(task_id):
                        task["status"] = "cancelled"
                        task["error"] = _MSG_USER_FINISHED
                    else:
                        task["status"] = "failed"
                        task["error"] = (
                            error_message if persistence_error is None
                            else f"{error_message}；状态保存失败：{persistence_error}"
                        )
            _schedule_pipeline_task_cleanup(task_id)
            with _pipeline_lock:
                _terminal_status = (_pipeline_tasks.get(task_id) or {}).get("status")
            if _terminal_status in ("cancelled", "failed"):
                _clear_auto_screen(task_id)
            _release_worker_resume_claims(_pipeline_tasks.get(task_id))
        except Exception as exc:
            _try_save_failure_snapshot(
                "cancelled" if _is_user_finished(task_id) else "failed")
            error_message = ai_service.user_facing_error("internal_error")
            if not _is_user_finished(task_id):
                record_failure(
                    store, task_id, stage="ai_screen",
                    error_code="internal_error", reason=error_message,
                    correlation_id=task_id, diagnostics={},
                    exception=exc, include_traceback=True,
                )
            persistence_error = None
            try:
                _write_run_unless_finished(
                    task_id, status="failed", error_code="internal_error",
                    error_reason=error_message,
                )
            except _OPERATIONAL_ERRORS as persist_exc:
                persistence_error = type(persist_exc).__name__
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    if _is_user_finished(task_id):
                        task["status"] = "cancelled"
                        task["error"] = _MSG_USER_FINISHED
                    else:
                        task["status"] = "failed"
                        task["error"] = (
                            error_message if persistence_error is None
                            else f"{error_message}；状态保存失败：{persistence_error}"
                        )
            _schedule_pipeline_task_cleanup(task_id)
            _release_worker_resume_claims(_pipeline_tasks.get(task_id))

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
        settings = store.get_ai_settings()
        if not settings.get("is_configured"):
            return jsonify({"ok": False, "error": "AI 未配置，请先设置 API 地址和密钥"}), 400
        cred_ref = store.get_credential_ref()
        if not cred_ref:
            return jsonify({"ok": False, "error": "未找到 API 密钥"}), 400
        api_key = ai_service.retrieve_api_key(cred_ref)
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

    @app.route("/api/advanced-settings", methods=["GET"])
    def get_advanced_settings():
        """SPEC011 T009: 返回版本化配置状态。

        保留 ``settings`` 字段用于迁移兼容，同时返回 selection、last_custom、
        mode_version 等版本化状态。
        """
        from webui.execution_config import CONFIG_SCHEMA_VERSION
        from webui.pipeline_exec import _ADVANCED_DEFAULTS
        state = store.get_advanced_config_state()
        active_version = None
        previous_version = None
        if state["active_mode_version_id"]:
            try:
                active_version = store.get_mode_version(
                    state["active_mode_version_id"]
                )
                previous_version = store.get_previous_mode_version(
                    state["active_mode_version_id"]
                )
            except KeyError:
                active_version = None
                previous_version = None
        # 兼容旧前端：settings 仍返回当前活跃设置
        legacy_settings = _load_legacy_advanced_settings()
        return jsonify({
            "ok": True,
            "selection": state["active_selection"],
            "settings": legacy_settings,
            "defaults": _ADVANCED_DEFAULTS,
            "last_custom": {
                "config_digest": state["last_custom_digest"],
                "settings": state["last_custom_config"] or {},
            } if state["last_custom_config"] else None,
            "mode_version": {
                "id": state["active_mode_version_id"],
                "version_digest": active_version["version_digest"],
                "previous_version_id": (
                    previous_version["id"] if previous_version else None
                ),
                "available_modes": ["stable", "balanced", "extreme"],
            } if active_version else None,
            "manual_ranges": (
                active_version["manual_ranges"] if active_version else {}
            ),
            "config_schema_version": CONFIG_SCHEMA_VERSION,
        })

    @app.route("/api/advanced-settings", methods=["POST"])
    def save_advanced_settings_endpoint():
        """SPEC011 T009: 兼容旧 POST 保存，同时写入 store 的自定义配置。"""
        from webui.execution_config import DEFAULT_DETAIL_TAB_POOL_SIZE, SPEED_FIELDS
        from webui.pipeline_exec import _ADVANCED_DEFAULTS
        body = request.get_json(silent=True) or {}
        settings = body.get("settings")
        if not isinstance(settings, dict):
            return jsonify({"ok": False, "error": "缺少 settings 对象"}), 400
        # 只保留合法 key，类型校验
        clean = {}
        for k, default in _ADVANCED_DEFAULTS.items():
            if k in settings:
                val = settings[k]
                if k == "browser_account":
                    from webui.pipeline_exec import load_browser_accounts
                    accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
                    clean[k] = str(val) if str(val) in accounts else "a"
                    continue
                if isinstance(default, float):
                    val = float(val)
                elif isinstance(default, int):
                    val = int(val)
                clean[k] = val
        _save_legacy_advanced_settings(clean)
        # SPEC011: 如果速度字段都存在，也写入 store 自定义配置
        speed_fields = {k: v for k, v in clean.items() if k in SPEED_FIELDS}
        speed_fields.setdefault("detail_tab_pool_size", DEFAULT_DETAIL_TAB_POOL_SIZE)
        if len(speed_fields) == len(SPEED_FIELDS):
            try:
                store.save_custom_config(speed_fields)
            except (ValueError, TypeError):
                pass  # store 保存失败不阻塞旧路径
        return jsonify({"ok": True, "settings": _load_legacy_advanced_settings()})

    def _browser_lock() -> tuple[str | None, str | None, str | None]:
        """Return the active browser lock as (kind, account id).

        Running/queued tasks lock every account; a paused run locks only the
        account frozen into its execution params (or the current fallback)."""
        with _pipeline_lock:
            for _task_id, task in reversed(list(_pipeline_tasks.items())):
                if task.get("status") in ("running", "queued"):
                    return ("running", str(task.get("browser_account") or ""),
                            str(task.get("platform") or "boss"))
        try:
            with store._connection() as conn:
                row = conn.execute(
                    "SELECT id FROM screening_runs WHERE status = 'paused' "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
        except (sqlite3.Error, RuntimeError):
            row = None
        if row is None:
            return None, None, None
        try:
            run = store.get_screening_run(row["id"]) or {}
            account = _account_for_run(run)
        except _OPERATIONAL_ERRORS:
            return "paused", None, None
        params = run.get("execution_params") or {}
        if not isinstance(params, dict):
            params = {}
        platform = str(params.get("platform") or run.get("platform") or "boss")
        return "paused", account, platform

    def _browser_busy() -> bool:
        return _browser_lock()[0] is not None

    def _has_active_pipeline_task() -> bool:
        """Only in-memory running/queued tasks block new task starts."""
        with _pipeline_lock:
            return any(
                task.get("status") in ("queued", "running")
                for task in _pipeline_tasks.values()
            )
    def _project_browser_accounts(accounts: dict) -> list[dict]:
        """Project accounts to the non-sensitive API shape (http-api.md L319)."""
        from webui.platforms import get_platform, list_platform_keys
        projected = []
        for acc in accounts.values():
            platforms = {}
            for key in list_platform_keys():
                reg = get_platform(key)
                platforms[key] = {"cdp_port": reg.default_cdp_port}
            projected.append({
                "id": str(acc.get("id") or ""),
                "name": str(acc.get("name") or ""),
                "builtin": bool(acc.get("builtin", False)),
                "platforms": platforms,
            })
        return projected

    @app.route("/api/browser-accounts", methods=["GET"])
    def list_browser_accounts():
        from webui.pipeline_exec import load_browser_accounts
        accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
        active = str((_load_legacy_advanced_settings() or {}).get("browser_account") or "a")
        if active not in accounts:
            active = "a"
        lock_kind, locked_account, lock_platform = _browser_lock()
        from scripts.login_state_cache import all_login_states
        return jsonify({
            "ok": True,
            "accounts": _project_browser_accounts(accounts),
            "active_account": active,
            "login_states": all_login_states(),
            "busy": _browser_busy(),
            "busy_kind": lock_kind,
            "locked_account": (
                locked_account if lock_kind == "paused" else None
            ),
            "locked_platform": (
                lock_platform if lock_kind is not None else None
            ),
        })

    @app.route("/api/browser-accounts", methods=["POST"])
    def add_browser_account_endpoint():
        from webui.pipeline_exec import add_browser_account
        body = request.get_json(silent=True) or {}
        name = str(body.get("name") or "").strip()
        profile_dir = str(body.get("profile_dir") or "").strip()
        try:
            account = add_browser_account(
                name, profile_dir=profile_dir,
                path=app.config["BROWSER_ACCOUNTS_PATH"],
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 422
        except (OSError, RuntimeError):
            return jsonify({"ok": False, "error": "账号保存失败，请检查磁盘后重试"}), 503
        return jsonify({"ok": True, "account": account}), 201

    @app.route("/api/browser-accounts/<account_id>/activate", methods=["POST"])
    def activate_browser_account(account_id):
        from webui.pipeline_exec import load_browser_accounts
        accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
        if str(account_id) not in accounts:
            return jsonify({"ok": False, "error": _MSG_ACCOUNT_NOT_FOUND}), 404
        settings = _load_legacy_advanced_settings()
        settings["browser_account"] = str(account_id)
        _save_legacy_advanced_settings(settings)
        return jsonify({"ok": True, "active_account": str(account_id)})

    @app.route("/api/browser-accounts/<account_id>/open", methods=["POST"])
    def open_browser_account(account_id):
        from webui.pipeline_exec import (
            ensure_chrome_ready,
            load_browser_accounts,
            set_active_cdp_data_dir,
        )
        accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
        account = accounts.get(str(account_id))
        if account is None:
            return jsonify({"ok": False, "error": _MSG_ACCOUNT_NOT_FOUND}), 404
        body = request.get_json(silent=True) or {}
        platform = str(body.get("platform") or "boss").strip()
        from webui.platforms import (
            derive_zhilian_profile_dir,
            get_platform_or_none,
            resolve_login_space,
        )
        reg = get_platform_or_none(platform)
        if reg is None:
            return jsonify({
                "ok": False, "error_code": "platform_validation_failed",
                "user_message": _MSG_UNSUPPORTED_PLATFORM, "platform": platform,
            }), 400
        boss_profile_dir = str(account.get("profile_dir") or "")
        if not boss_profile_dir:
            return jsonify({
                "ok": False, "error": "profile_missing",
                "message": "账号未配置浏览器资料目录",
            }), 409
        try:
            login_space = resolve_login_space(
                platform, str(account_id), boss_profile_dir=boss_profile_dir,
            )
        except (ValueError, RuntimeError) as exc:
            return jsonify({
                "ok": False, "error": "login_space_invalid", "message": str(exc),
            }), 409
        profile_dir = (
            boss_profile_dir if platform == "boss"
            else derive_zhilian_profile_dir(boss_profile_dir)
        )
        platform_label = reg.display_name
        lock_kind, locked_account, locked_platform = _browser_lock()
        if lock_kind is not None:
            if (lock_kind == "paused"
                    and locked_account == str(account_id)
                    and locked_platform == platform):
                set_active_cdp_data_dir(profile_dir)
                ok, msg = ensure_chrome_ready(login_space.cdp_port)
                if not ok:
                    return jsonify({
                        "ok": False, "error": "chrome_not_ready", "message": msg,
                    }), 409
                _invalidate_login_cache(str(account_id), platform)
                return jsonify({
                    "ok": True,
                    "message": (
                        f"已打开「{account['name']}」的{platform_label}自动化浏览器，"
                        "请登录后回到任务页点「继续」"
                    ),
                })
            if lock_kind == "paused":
                locked_name = (
                    accounts.get(locked_account, {}).get("name") if locked_account else ""
                )
                lock_reg = get_platform_or_none(locked_platform or "")
                lock_label = (lock_reg.display_name if lock_reg else locked_platform or "BOSS")
                message = (
                    f"当前有暂停任务，浏览器已锁定到「{locked_name}」的{lock_label}登录空间；" if locked_name else ""
                    "请先打开该登录空间或结束/取消暂停任务后再操作"
                ) if locked_name else (
                    "当前有暂停任务；请先结束或取消任务后再打开浏览器账号"
                )
                return jsonify({
                    "ok": False, "error": "browser_busy", "message": message,
                }), 409
            return jsonify({
                "ok": False, "error": "browser_busy",
                "message": "当前有任务运行，浏览器正在被占用；请先等待、取消或结束任务",
            }), 409
        set_active_cdp_data_dir(profile_dir)
        ok, msg = ensure_chrome_ready(login_space.cdp_port)
        if not ok:
            return jsonify({"ok": False, "error": "chrome_not_ready", "message": msg}), 409
        _invalidate_login_cache(str(account_id), platform)
        return jsonify({
            "ok": True,
            "message": f"已打开「{account['name']}」的{platform_label}自动化浏览器，请登录",
        })

    @app.route("/api/browser-accounts/<account_id>", methods=["DELETE"])
    def delete_browser_account_endpoint(account_id):
        from webui.pipeline_exec import delete_browser_account, load_browser_accounts
        if _browser_busy():
            return jsonify({
                "ok": False, "error": "browser_busy",
                "message": "任务运行或暂停中不能删除账号",
            }), 409
        accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
        account = accounts.get(str(account_id))
        if account is None:
            return jsonify({"ok": False, "error": _MSG_ACCOUNT_NOT_FOUND}), 404
        from webui.platforms import derive_zhilian_profile_dir, get_platform_or_none
        zhilian_reg = get_platform_or_none("zhilian")
        zhilian_port = int(zhilian_reg.default_cdp_port) if zhilian_reg else 9223
        boss_profile_dir = str(account.get("profile_dir") or "")
        zhilian_profile_dir = (
            derive_zhilian_profile_dir(boss_profile_dir) if boss_profile_dir else ""
        )

        def _port_profiles(port: int) -> list[str]:
            if not boss.is_cdp_ready(port):
                return []
            return [boss.normalize_profile_path(p) for p in boss.chrome_user_data_dirs_for_cdp_port(port) if p]

        port_profiles_boss = _port_profiles(boss.DEFAULT_CDP_PORT)
        port_profiles_zhilian = _port_profiles(zhilian_port)
        known_boss = {
            boss.normalize_profile_path(str(a.get("profile_dir") or ""))
            for a in accounts.values() if str(a.get("profile_dir") or "").strip()
        }
        known_zhilian = {
            boss.normalize_profile_path(derive_zhilian_profile_dir(
                str(a.get("profile_dir") or "")))
            for a in accounts.values() if str(a.get("profile_dir") or "").strip()
        }
        if boss_profile_dir and boss.normalize_profile_path(boss_profile_dir) in port_profiles_boss:
            return jsonify({
                "ok": False, "error": "browser_in_use",
                "message": "该账号的 BOSS 自动化浏览器正在运行，请先打开其他账号或手动关闭后再删除",
            }), 409
        if zhilian_profile_dir and boss.normalize_profile_path(zhilian_profile_dir) in port_profiles_zhilian:
            return jsonify({
                "ok": False, "error": "browser_in_use",
                "message": "该账号的智联自动化浏览器正在运行，请先打开其他账号或手动关闭后再删除",
            }), 409
        for port, profiles, known, label in (
            (boss.DEFAULT_CDP_PORT, port_profiles_boss, known_boss, "boss"),
            (zhilian_port, port_profiles_zhilian, known_zhilian, "zhilian"),
        ):
            unknown = [p for p in profiles if p and p not in known]
            if unknown:
                return jsonify({
                    "ok": False, "error": "login_space_conflict",
                    "message": f"端口 {port} 被未知 {label} profile 占用，不能删除账号",
                }), 409
        try:
            delete_browser_account(
                str(account_id), path=app.config["BROWSER_ACCOUNTS_PATH"],
            )
        except KeyError:
            return jsonify({"ok": False, "error": _MSG_ACCOUNT_NOT_FOUND}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        except (OSError, RuntimeError):
            return jsonify({"ok": False, "error": "账号删除失败，请检查磁盘后重试"}), 503
        settings = _load_legacy_advanced_settings()
        if str(settings.get("browser_account") or "") == str(account_id):
            settings["browser_account"] = "a"
            _save_legacy_advanced_settings(settings)
        return jsonify({"ok": True})

    @app.route("/api/cooldown/clear", methods=["POST"])
    def clear_cooldown_endpoint():
        """手动解除风控冷却（D6）。

        只清 cooldown.json 中该账号（可选平台）的记录，不碰登录态缓存；
        前端二次确认弹窗后再调用。
        """
        from webui.cooldown import clear_cooldown
        body = request.get_json(silent=True) or {}
        account_id = str(body.get("account_id") or "").strip()
        platform = str(body.get("platform") or "").strip() or None
        if not account_id:
            return jsonify({"ok": False, "error": "account_id 不能为空"}), 400
        clear_cooldown(account_id, platform)
        return jsonify({"ok": True})

    @app.route("/api/advanced-settings/custom", methods=["PUT"])
    def save_custom_config():
        """SPEC011 T009: 保存完整自定义配置。

        对应 HTTP API PUT /api/advanced-settings/custom。
        """
        from webui.execution_config import SPEED_FIELDS
        body = request.get_json(silent=True) or {}
        settings = body.get("settings")
        if not isinstance(settings, dict):
            return jsonify({"ok": False, "error": "缺少 settings 对象"}), 400
        # 验证速度字段完整；旧 9 字段请求由 store 补默认 JD Tab 数
        missing = [
            f for f in SPEED_FIELDS
            if f not in settings and f != "detail_tab_pool_size"
        ]
        if missing:
            return jsonify({
                "ok": False,
                "error_code": "invalid_request",
                "error": f"缺少必填字段: {missing}",
            }), 400
        try:
            digest = store.save_custom_config(settings)
        except (ValueError, TypeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 422
        # 同时保存到旧 JSON 文件以保持兼容
        _save_legacy_advanced_settings(settings)
        state = store.get_advanced_config_state()
        return jsonify({
            "ok": True,
            "selection": "custom",
            "config_digest": digest,
            "settings": state["last_custom_config"],
        })

    @app.route("/api/advanced-settings/select-mode", methods=["POST"])
    def select_mode():
        """SPEC011 T009: 选择系统参考模式。

        对应 HTTP API POST /api/advanced-settings/select-mode。
        服务端根据 scope_digest 重新计算任务规模，不信任客户端传入的 size。
        """
        body = request.get_json(silent=True) or {}
        mode = body.get("mode")
        scope_digest = body.get("scope_digest")
        if mode not in ("stable", "balanced", "extreme", "custom"):
            return jsonify({
                "ok": False,
                "error_code": "invalid_request",
                "error": "mode 必须是 stable/balanced/extreme/custom",
            }), 400
        scope = scope_previews.get(str(scope_digest or ""))
        if scope is None:
            return jsonify({
                "ok": False,
                "error_code": "scope_preview_required",
                "error": "搜索范围摘要未知，请重新校验搜索范围",
            }), 409
        task_size = scope["task_size"]
        try:
            result = store.select_mode(mode, task_size=task_size)
        except (ValueError, TypeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 422
        # 同时保存到旧 JSON 文件以保持兼容；切回 custom 也必须恢复
        # 最近自定义值，避免 SQLite 权威状态与旧执行入口分叉。
        if result.get("config"):
            _save_legacy_advanced_settings(result["config"])
        from webui.execution_config import SPEED_FIELDS
        settings = {
            field: result["config"][field] for field in SPEED_FIELDS
        }
        state = store.get_advanced_config_state()
        return jsonify({
            "ok": True,
            "selection": result["selection"],
            "settings": settings,
            "task_size": task_size,
            "mode_version_id": state["active_mode_version_id"],
            "config_digest": result["config"].get("config_digest"),
        })

    @app.route("/api/advanced-settings/mode-versions/rollback", methods=["POST"])
    def rollback_mode_version():
        """SPEC011 T009: 回退到指定模式版本。

        对应 HTTP API POST /api/advanced-settings/mode-versions/rollback。
        """
        body = request.get_json(silent=True) or {}
        target_version_id = body.get("target_version_id")
        if not target_version_id:
            return jsonify({
                "ok": False,
                "error_code": "invalid_request",
                "error": "缺少 target_version_id",
            }), 400
        try:
            store.rollback_mode_version(target_version_id)
        except (ValueError, TypeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 422
        state = store.get_advanced_config_state()
        return jsonify({
            "ok": True,
            "active_mode_version_id": state["active_mode_version_id"],
        })

    @app.route("/api/search-scope/preview", methods=["POST"])
    def search_scope_preview():
        """SPEC011 T004 / tasks005 T402: 后端权威范围预览与校验（平台感知）。

        不改变任务工作量字段；仅返回规范化后的 scope 和去重信息。
        对应 HTTP API POST /api/search-scope/preview。
        """
        from webui.execution_config import CityValidationError, preview_scope
        from webui.platforms import (
            UnknownPlatformError,
            get_platform_or_none,
            validate_platform_key,
        )

        body = request.get_json(silent=True) or {}
        platform_raw = body.get("platform") or "boss"

        # 平台键校验
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
                "error_code": "platform_validation_failed",
                "user_message": "平台未注册",
            }), 400
        if not reg.enabled_for_new_tasks:
            return jsonify({
                "ok": False,
                "error_code": "platform_disabled",
                "user_message": reg.availability_reason or "平台暂不可用",
            }), 503

        keywords = body.get("keywords")
        scope_kind = body.get("scope_kind", "cities")
        cities = body.get("cities", [])
        pages_per_combination = body.get("pages_per_combination", 1)

        if not isinstance(keywords, list):
            return jsonify({"ok": False, "error": "keywords 必须是数组"}), 400
        if scope_kind not in ("cities", "nationwide"):
            return jsonify({"ok": False, "error": "scope_kind 必须是 cities 或 nationwide"}), 400
        if not isinstance(cities, list):
            return jsonify({"ok": False, "error": "cities 必须是数组"}), 400

        if isinstance(pages_per_combination, bool) or not isinstance(
            pages_per_combination, int
        ):
            return jsonify({"ok": False, "error": "pages_per_combination 必须是整数"}), 400
        pages_int = pages_per_combination

        try:
            result = preview_scope(
                keywords=keywords,
                scope_kind=scope_kind,
                cities=cities,
                pages_per_combination=pages_int,
                platform=platform_raw,
            )
            scope_previews[result["scope"]["scope_digest"]] = dict(result["scope"])
            return jsonify({"ok": True, **result})
        except CityValidationError as e:
            return jsonify({
                "ok": False,
                "error_code": "city_validation_failed",
                "error": str(e),
                "details": e.details,
            }), 422
        except ValueError as e:
            return jsonify({
                "ok": False,
                "error_code": "scope_validation_failed",
                "error": str(e),
            }), 422

    @app.route("/api/execute-search", methods=["POST"])
    def execute_search():
        """Stage 3 / tasks005 T402: 平台感知搜索 run 创建。

        Accepts JSON ``{"script_params": {...}}`` (or the params directly).
        Launches a background task and returns a ``task_id`` for polling.

        SPEC011 T006: 后端从权威 scope 和当前配置选择创建不可变快照；
        客户端不能提供或覆盖任务规模与执行配置。
        SPEC011 T015: 实验租约持有时拒绝启动（FR-035）。
        tasks005 T402: 冻结单一平台和完整 runtime，搜索 run 筛选快照为空。
        """
        from webui.core import _AI_FILTER_KEYS, _is_non_empty_filter_value
        from webui.execution_config import (
            ExecutionConfigSnapshot,
            FrozenTaskScope,
            preview_scope,
        )
        from webui.pipeline_exec import resolve_browser_account
        from webui.platforms import (
            UnknownPlatformError,
            get_platform_or_none,
            resolve_login_space,
            validate_platform_key,
        )

        body = request.get_json(silent=True) or {}
        script_params = body.get("script_params") or body
        if not isinstance(script_params, dict):
            return jsonify({"ok": False, "error": "无效的请求体"}), 400
        if not script_params.get("keyword") or not script_params.get("city"):
            return jsonify({"ok": False, "error": "缺少关键词或城市"}), 400

        # B031: 一键链路标记；auto_screen_fields/profile 只作为刷新恢复快照，
        # 不进 script_params，不触碰搜索请求的 AI filters 校验。
        auto_screen = bool(body.get("auto_screen"))
        auto_screen_fields = body.get("auto_screen_fields") if auto_screen else {}
        if auto_screen and not isinstance(auto_screen_fields, dict):
            return jsonify({"ok": False, "error": "auto_screen_fields 必须是对象"}), 400
        auto_screen_profile = str(body.get("auto_screen_profile") or "") if auto_screen else ""
        auto_screen_facts = (
            body.get("auto_screen_facts")
            if auto_screen and isinstance(body.get("auto_screen_facts"), dict)
            else None
        )

        # T402: 平台键校验（先于任何副作用）
        platform_raw = body.get("platform") or "boss"
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
                "error_code": "platform_validation_failed",
                "user_message": "平台未注册",
            }), 400

        # T402: 非空 AI filters 拒绝（零副作用，先于租约和 scope 检查）
        offending = [
            k for k in _AI_FILTER_KEYS
            if k in script_params and _is_non_empty_filter_value(script_params[k])
        ]
        if offending:
            return jsonify({
                "ok": False,
                "error_code": "search_filters_not_supported",
                "user_message": "搜索请求不允许携带非空 AI filters: " + ", ".join(sorted(offending)),
            }), 422

        # SPEC011 T015/FR-035: 实验租约门禁
        ok, err_resp = _check_tuning_lease_conflict()
        if not ok:
            return err_resp

        # 逻辑隔离：同一时间只允许一个 pipeline 任务占用浏览器（B031 回归）。
        if _browser_busy():
            return jsonify({
                "ok": False, "error": "browser_busy",
                "message": "当前已有任务在运行或暂停，请先等待、继续或结束任务后再开始新任务",
            }), 409

        requested_digest = str(body.get("scope_digest") or "")
        scope_payload = scope_previews.get(requested_digest) if requested_digest else None
        if requested_digest and scope_payload is None:
            return jsonify({
                "ok": False,
                "error_code": "scope_preview_required",
                "error": "搜索范围摘要未知，请重新校验搜索范围",
            }), 409
        if scope_payload is None:
            raw_keyword = script_params.get("keyword")
            keywords = (
                [item.strip() for item in str(raw_keyword).replace("，", ",").split(",")]
                if not isinstance(raw_keyword, list) else raw_keyword
            )
            raw_cities = script_params.get("city") or []
            if isinstance(raw_cities, str):
                raw_cities = [
                    item.strip() for item in raw_cities.replace("，", ",").split(",")
                    if item.strip()
                ]
            nationwide = raw_cities == ["全国"]
            try:
                preview = preview_scope(
                    keywords=keywords,
                    scope_kind="nationwide" if nationwide else "cities",
                    cities=[] if nationwide else raw_cities,
                    pages_per_combination=script_params.get("pages", 3),
                    platform=platform_raw,
                )
            except (TypeError, ValueError) as exc:
                return jsonify({
                    "ok": False, "error_code": "scope_validation_failed",
                    "error": str(exc),
                }), 422
            scope_payload = preview["scope"]
            scope_previews[scope_payload["scope_digest"]] = dict(scope_payload)
        try:
            frozen_scope = FrozenTaskScope.from_dict(scope_payload)
            state = store.get_advanced_config_state()
            selected = store.select_mode(
                state["active_selection"], task_size=frozen_scope.task_size,
            )
            execution_config = ExecutionConfigSnapshot.from_dict(selected["config"])
        except (KeyError, TypeError, ValueError) as exc:
            return jsonify({
                "ok": False, "error_code": "config_resolution_failed",
                "error": str(exc),
            }), 422

        # T402: 平台一致性校验
        if frozen_scope.platform != platform_raw:
            return jsonify({
                "ok": False,
                "error_code": "scope_platform_mismatch",
                "user_message": "请求平台与搜索范围平台不一致",
            }), 409

        # T402: 平台禁用检查（在 scope 平台不匹配之后）
        if not reg.enabled_for_new_tasks:
            return jsonify({
                "ok": False,
                "error_code": "platform_disabled",
                "user_message": reg.availability_reason or "平台暂不可用",
            }), 503

        # T402: script_params 与 scope 一致性校验
        sp_keywords = script_params.get("keyword")
        if isinstance(sp_keywords, str):
            sp_keyword_list = [k.strip() for k in sp_keywords.replace("，", ",").split(",") if k.strip()]
        elif isinstance(sp_keywords, list):
            sp_keyword_list = [str(k).strip() for k in sp_keywords if k and str(k).strip()]
        else:
            sp_keyword_list = []
        sp_cities = script_params.get("city") or []
        if isinstance(sp_cities, str):
            sp_cities = [c.strip() for c in sp_cities.replace("，", ",").split(",") if c.strip()]
        scope_cities = (
            ["全国"] if frozen_scope.scope_kind == "nationwide"
            else list(frozen_scope.cities)
        )
        # pages 未显式提供时不校验（后端用 scope 冻结值覆盖）
        pages_mismatch = False
        if "pages" in script_params:
            try:
                sp_pages = int(script_params["pages"])
                pages_mismatch = sp_pages != frozen_scope.pages_per_combination
            except (TypeError, ValueError):
                pages_mismatch = True
        if (sp_keyword_list != list(frozen_scope.keywords)
                or list(sp_cities) != scope_cities
                or pages_mismatch):
            return jsonify({
                "ok": False,
                "error_code": "scope_request_mismatch",
                "user_message": "搜索参数与搜索范围不一致",
            }), 409

        script_params = dict(script_params)
        script_params["keyword"] = ",".join(frozen_scope.keywords)
        script_params["city"] = scope_cities
        script_params["pages"] = frozen_scope.pages_per_combination

        # T402: 冻结完整 runtime — 平台登录空间、task_input_digest
        browser_account = _account_for_run()
        profile_dir = resolve_browser_account(
            browser_account, app.config["BROWSER_ACCOUNTS_PATH"])
        login_space = resolve_login_space(
            platform_raw, browser_account,
            boss_profile_dir=profile_dir or "unresolved",
        )
        from webui.platforms import resolve_platform_city
        resolved_cities = []
        for city_name in scope_cities:
            entry = resolve_platform_city(platform_raw, city_name)
            resolved_cities.append({
                "name": entry.name,
                "label": entry.label,
                "platform_code": entry.platform_code,
                "mapping_version": entry.mapping_version,
            })
        task_input_digest = hashlib.sha256(json.dumps({
            "platform": platform_raw,
            "scope_digest": frozen_scope.scope_digest,
            "filter_schema_version": None,
            "frozen_filters": {},
            "browser_account": browser_account,
            "cdp_port": login_space.cdp_port,
            "profile_key": login_space.profile_key,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

        task_id = uuid.uuid4().hex
        task = _register_pipeline_task(task_id, "scrape")
        # 把冻结配置摘要存入任务记录，供进度查询返回
        with _pipeline_lock:
            task["config_digest"] = execution_config.config_digest
            task["scope_digest"] = frozen_scope.scope_digest
            task["browser_account"] = browser_account
            task["platform"] = platform_raw
            task["cdp_port"] = login_space.cdp_port
            task["profile_key"] = login_space.profile_key
            task["task_input_digest"] = task_input_digest
            task["auto_screen"] = auto_screen
        # T402: 搜索 run 的 frozen_filters 为空，筛选快照为空
        store.create_screening_run(
            task_id,
            frozen_filters={},
            source_count=frozen_scope.combination_count,
            execution_params={
                "platform": platform_raw,
                "filter_schema_version": None,
                "script_params": script_params,
                "browser_account": browser_account,
                "cdp_port": login_space.cdp_port,
                "profile_key": login_space.profile_key,
                "task_input_digest": task_input_digest,
                "execution_config": execution_config.to_dict(),
                "resolved_cities": resolved_cities,
                "frozen_scope": frozen_scope.to_dict(),
                "auto_screen": auto_screen,
                "auto_screen_fields": auto_screen_fields,
                "auto_screen_profile": auto_screen_profile,
                "auto_screen_facts": auto_screen_facts,
            },
            backend_version=_backend_version,
        )
        # T402: 持久化平台、空筛选快照和 task_input_digest
        store.save_filter_snapshot(
            task_id,
            platform=platform_raw,
            filter_schema_version=None,
            filter_snapshot={},
            task_input_digest=task_input_digest,
        )
        _activate_run_browser()
        try:
            _pipeline_executor.submit(
                _run_pipeline_task, task_id, script_params,
                execution_config, frozen_scope,
            )
        except RuntimeError:
            store.update_screening_run(
                task_id, status="failed", error_code="submit_failed",
                error_reason="任务执行器未接受任务",
            )
            raise
        return jsonify({
            "ok": True,
            "task_id": task_id,
            "platform": platform_raw,
            "config_digest": execution_config.config_digest,
            "scope_digest": frozen_scope.scope_digest,
            "task_input_digest": task_input_digest,
            "task_size": frozen_scope.task_size,
            "browser_account": browser_account,
        })

    @app.route("/api/scrape-result-save", methods=["POST"])
    def scrape_result_save():
        """B038: 把已完成的抓取任务固化为"已抓取，未筛选"历史轮。

        任务本身已自然完成，这里只固化快照（status=scraped_only），
        不终结任务、不跑 AI；0 岗位时不落库。
        """
        body = request.get_json(silent=True) or {}
        task_id = str(body.get("task_id") or "").strip()
        if not task_id:
            return jsonify({
                "ok": False, "error": "missing_task_id",
                "message": "缺少 task_id",
            }), 400
        source_snapshot = _ensure_scrape_source(task_id)
        if source_snapshot is None:
            # 区分三类：任务不存在 / 任务未完成 / 任务完成但 0 岗位。
            try:
                existing = store.get_screening_run(task_id)
            except _OPERATIONAL_ERRORS:
                existing = None
            if existing is None:
                return jsonify({
                    "ok": False, "error": "scrape_task_not_found",
                    "message": "抓取任务不存在",
                }), 404
            run_status = str(existing.get("status") or "")
            if run_status not in ("succeeded", "partial", "failed", "interrupted"):
                return jsonify({
                    "ok": False, "error": "scrape_not_completed",
                    "message": "抓取任务尚未成功完成",
                }), 409
            # 已完成的 0 岗位任务：不进历史，前端仍展示 0。
            return jsonify({"ok": True, "saved": False, "run_id": task_id})
        if source_snapshot.get("kind") != "scrape" or source_snapshot.get("status") != "done":
            return jsonify({
                "ok": False, "error": "scrape_not_completed",
                "message": "抓取任务尚未成功完成",
            }), 409
        source_result = source_snapshot.get("result") or {}
        source_jobs = source_result.get("jobs") or []
        # 平台身份优先取冻结的 run checkpoint（与 ai_screen 同一口径）。
        try:
            parent_identity = store.get_run_checkpoint_identity(task_id)
        except _OPERATIONAL_ERRORS:
            parent_identity = None
        platform = str(
            (parent_identity or {}).get("platform")
            or source_snapshot.get("platform")
            or source_result.get("platform")
            or "boss"
        )
        profile_summary = str(body.get("profile_summary") or "")
        raw_facts = body.get("profile_facts")
        profile_facts = raw_facts if isinstance(raw_facts, dict) else None
        execution_config = source_snapshot.get("execution_config") or {}
        run_row = {}
        try:
            run_row = store.get_screening_run(task_id) or {}
            params = run_row.get("execution_params") or {}
            if not execution_config:
                execution_config = params.get("execution_config") or {}
        except _OPERATIONAL_ERRORS:
            params = {}
        # 搜索参数（关键词/城市）来自 run 冻结的 script_params；
        # 缺失时退化为仅平台，历史列表关键词摘要能正常展示。
        script_params = params.get("script_params") or {}
        if isinstance(script_params, dict) and "platform" not in script_params:
            script_params = {**script_params, "platform": platform}
        outcome = save_scrape_snapshot(
            store,
            source_jobs,
            platform=platform,
            scrape_task_id=task_id,
            profile_summary=profile_summary,
            profile_facts=profile_facts,
            execution_config=execution_config,
            script_params=script_params,
            started_at=run_row.get("started_at"),
            finished_at=run_row.get("finished_at"),
        )
        return jsonify({"ok": True, **outcome})

    @app.route("/api/execute-search/continue/<old_task_id>", methods=["POST"])
    def continue_execute_search(old_task_id, _block_checked=False):
        """断点续抓：从上次失败的组合接着跑，跳过已完成的组合。

        切片4：支持 paused 状态继续（FR-020）。优先从 DB checkpoint 恢复
        completed_combos（服务重启后内存丢失也能恢复），回退到内存 task.result。
        同时检查阻断是否解除（如登录已恢复、验证码已过）。

        SPEC011 T015: 实验租约持有时拒绝继续（FR-035）。
        """
        # SPEC011 T015/FR-035: 实验租约门禁
        ok, err_resp = _check_tuning_lease_conflict()
        if not ok:
            return err_resp
        # 1) 优先从内存读 old_task
        with _pipeline_lock:
            old_task = _pipeline_tasks.get(old_task_id)
            old_snapshot = dict(old_task) if old_task else None
        # 2) 内存没有则从 DB 读（服务重启恢复场景）
        db_run = None
        try:
            db_run = store.get_screening_run(old_task_id)
        except _OPERATIONAL_ERRORS:
            db_run = None
        if old_snapshot is None and db_run is None:
            return jsonify({"ok": False, "error": "原任务不存在或已过期"}), 404
        # DB 是服务重启后仍存在的状态权威；取消/失败均为终态，不得复活。
        mem_status = old_snapshot.get("status") if old_snapshot else None
        db_status = db_run.get("status") if db_run else None
        effective_status = db_status or mem_status
        if effective_status != "paused":
            return jsonify({
                "ok": False,
                "error": "not_paused",
                "status": _run_to_task_status(effective_status),
                "message": "只有 paused 状态的任务才能继续",
            }), 409
        if db_run is not None and not _block_checked:
            passed, code, reason = _check_resume_block(db_run)
            if not passed:
                return jsonify({
                    "ok": False, "error": "block_not_resolved",
                    "error_code": code, "error_reason": reason,
                    "status": "paused",
                }), 409
        if db_run is not None:
            resume_params = dict(db_run.get("execution_params") or {})
            if not str(resume_params.get("browser_account") or ""):
                resume_params["browser_account"] = _account_for_run(db_run)
                store.update_screening_execution_params(old_task_id, resume_params)
        # 3) 收集 script_params（内存优先，DB 兜底）
        script_params = (old_snapshot or {}).get("script_params")
        if not script_params and db_run:
            try:
                ep = db_run.get("execution_params") or {}
                script_params = ep.get("script_params") or ep
            except (AttributeError, TypeError):
                script_params = None
        if not script_params:
            return jsonify({"ok": False, "error": "原任务参数丢失，无法继续"}), 400
        # 4) 收集 completed_combos：DB checkpoint 优先（持久），内存 result 兜底
        completed: set[str] = set()
        try:
            completed = store.load_checkpoint(old_task_id, "scrape")
        except _OPERATIONAL_ERRORS:
            completed = set()
        if not completed and old_snapshot:
            old_result = old_snapshot.get("result") or {}
            completed = set(old_result.get("completed_combos") or [])
        try:
            old_jobs = store.load_scrape_run_jobs(old_task_id)
        except _OPERATIONAL_ERRORS:
            old_jobs = []
        if not old_jobs and old_snapshot:
            old_result = old_snapshot.get("result") or {}
            old_jobs = old_result.get("jobs") or []
        if not _claim_resume(old_task_id):
            return jsonify({
                "ok": False, "error": "already_running",
                "message": _MSG_TASK_ALREADY_RUNNING,
            }), 409
        task_id = old_task_id
        claimed_task, previous_task = _claim_pipeline_task_id(
            task_id, "scrape",
            started_at=_iso_epoch_ms((db_run or {}).get("started_at")),
        )
        if claimed_task is None:
            _release_resume_claim(old_task_id)
            return jsonify({
                "ok": False, "error": "already_running",
                "message": _MSG_TASK_ALREADY_RUNNING,
            }), 409
        # 把续抓信息存进 task，_run_pipeline_task 会读取
        # T403: 从 DB 恢复冻结 runtime（platform/cdp_port/profile_key/
        # task_input_digest），不读当前 UI 或活动账号
        db_ep = (db_run or {}).get("execution_params") or {}
        with _pipeline_lock:
            task = _pipeline_tasks[task_id]
            task["skip_combos"] = completed
            task["old_jobs"] = old_jobs
            task["resuming_from"] = old_task_id
            task["browser_account"] = (
                db_ep.get("browser_account") or _account_for_run(db_run)
            )
            task["platform"] = db_ep.get("platform") or "boss"
            task["cdp_port"] = db_ep.get("cdp_port")
            task["profile_key"] = db_ep.get("profile_key")
            task["task_input_digest"] = db_ep.get("task_input_digest")
            task["auto_screen"] = bool(db_ep.get("auto_screen"))
        start_gate = threading.Event()
        abort_start = threading.Event()

        def run_after_claim_commits():
            start_gate.wait()
            if not abort_start.is_set():
                _run_pipeline_task(task_id, script_params)

        try:
            future = _pipeline_executor.submit(run_after_claim_commits)
            # 事件与 DB claim 都在 worker 放行前完成；继续沿用同一 task_id，
            # 避免把内部 handoff 暴露成非 canonical 的 resumed 状态。
            if db_run is not None:
                store.append_task_event(old_task_id, "resume", {"task_id": task_id})
                if not store.claim_paused_screening_run(old_task_id):
                    raise RuntimeError("resume_already_claimed")
            with _pipeline_lock:
                if _pipeline_tasks.get(task_id) is claimed_task:
                    claimed_task["status"] = "running"
        except (sqlite3.Error, RuntimeError, ValueError, KeyError) as exc:
            abort_start.set()
            start_gate.set()
            if "future" in locals():
                future.cancel()
            _release_pipeline_claim(task_id, claimed_task, previous_task)
            _release_resume_claim(old_task_id)
            return jsonify({
                "ok": False, "error": "resume_submit_failed",
                "message": f"继续任务提交失败：{type(exc).__name__}",
            }), 500
        start_gate.set()
        return jsonify({"ok": True, "task_id": task_id,
                        "skipped": len(completed), "old_jobs": len(old_jobs),
                        "resumed_from": old_task_id})

    @app.route("/api/execute-search/<task_id>/cancel", methods=["POST"])
    def cancel_execute_search(task_id):
        """停止正在运行的抓取任务。

        做法：set stop_event → 立刻关调试 Chrome（不等当前组合抓完）→
        task 标 cancelled。run_search 会因浏览器被关而退出，_run_pipeline_task
        看到 stop_event.is_set() 后标 cancelled 而非 failed/done。
        """
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)
            if task is None:
                return jsonify({"ok": False, "error": _MSG_TASK_NOT_FOUND}), 404
            if task["status"] not in ("queued", "running"):
                return jsonify({"ok": False, "error": f"任务已结束，无法取消（当前状态：{task['status']}）"}), 400
            stop_event = task.get("stop_event")
            if stop_event is not None:
                stop_event.set()
            # 立刻标记 cancelled，让前端轮询马上看到状态变化
            task["status"] = "cancelled"
            task["error"] = _MSG_USER_STOPPED_SCRAPE
            task["logs"].append("用户取消任务")
            cancel_platform = task.get("platform")
        # 关浏览器放到锁外，避免持锁时间过长。best-effort，失败不阻塞取消。
        try:
            from webui.pipeline_exec import close_debug_chrome
            close_debug_chrome()
        except Exception:
            pass
        _clear_auto_screen(task_id)
        # T412 契约 http-api.md L223-229：DB run 存在时以 DB platform 为权威；
        # 仅 DB 创建前内存窗口用注册 task 的不可变平台快照。
        if not cancel_platform:
            try:
                _db_run = store.get_screening_run(task_id)
                cancel_platform = (_db_run or {}).get("platform")
            except _OPERATIONAL_ERRORS:
                pass
        return jsonify({
            "ok": True, "run_id": task_id, "task_id": task_id,
            "platform": cancel_platform, "status": "cancelled",
        })

    @app.route("/api/ai-screen/<task_id>/cancel", methods=["POST"])
    def cancel_ai_screen(task_id):
        """停止正在运行的 AI 筛选任务。

        与抓取取消同套路但按 kind 区分：纯 AI 调用阶段（粗筛/精筛）没有
        浏览器可关，close_debug_chrome 是 no-op；抓 JD 阶段关浏览器可让
        子进程抓取立即中断。工作线程在阶段边界看到 stop_event 后标
        cancelled，不会把结果覆盖成 done。
        """
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)
            if task is None:
                return jsonify({"ok": False, "error": _MSG_TASK_NOT_FOUND}), 404
            if task.get("kind") != "ai_screen":
                return jsonify({"ok": False, "error": "不是 AI 筛选任务"}), 409
            if task["status"] not in ("queued", "running"):
                return jsonify({"ok": False, "error": f"任务已结束，无法取消（当前状态：{task['status']}）"}), 400
            stop_event = task.get("stop_event")
            if stop_event is not None:
                stop_event.set()
            # 立刻标记 cancelled，让前端轮询马上看到状态变化
            task["status"] = "cancelled"
            task["error"] = _MSG_USER_STOPPED_SCREEN
            task["logs"].append("用户取消任务")
            cancel_platform = task.get("platform")
        # 关浏览器放到锁外（仅抓 JD 阶段有意义），best-effort，失败不阻塞取消。
        try:
            from webui.pipeline_exec import close_debug_chrome
            close_debug_chrome()
        except Exception:
            pass
        # T412 契约 http-api.md L223-229：DB run 存在时以 DB platform 为权威；
        # 仅 DB 创建前内存窗口用注册 task 的不可变平台快照。
        if not cancel_platform:
            try:
                _db_run = store.get_screening_run(task_id)
                cancel_platform = (_db_run or {}).get("platform")
            except _OPERATIONAL_ERRORS:
                pass
        return jsonify({
            "ok": True, "run_id": task_id, "task_id": task_id,
            "platform": cancel_platform, "status": "cancelled",
        })

    @app.route("/api/ai-screen", methods=["POST"])
    def ai_screen():
        """Stage 3b：对已抓取的原始岗位做两段式 AI 筛选。

        T406-T407: 接收 platform 做一致性校验，从父搜索 run 继承平台/scope/
        runtime，保存字段稳定值和当时标签的完整筛选快照。

        SPEC011 T015: 实验租约持有时拒绝启动（FR-035）。
        """
        body = request.get_json(silent=True) or {}
        screening_fields = body.get("screening_fields") or {}
        profile_summary = str(body.get("profile_summary") or "")
        profile_facts = body.get("profile_facts")
        scrape_task_id = str(body.get("scrape_task_id") or "").strip()
        request_platform = str(body.get("platform") or "").strip() or None
        filter_schema_version = body.get("filter_schema_version")
        # B031: 一键自动接续在进入现有校验前消费标记，失败也不会刷新重试。
        if bool(body.get("consume_auto_screen")) and scrape_task_id:
            _consume_auto_screen(scrape_task_id)
        if not isinstance(screening_fields, dict):
            return jsonify({"ok": False, "error": "无效的筛选字段"}), 400
        if not scrape_task_id:
            return jsonify({"ok": False, "error": "缺少 scrape_task_id"}), 400
        # SPEC011 T015/FR-035: 实验租约门禁
        ok, err_resp = _check_tuning_lease_conflict()
        if not ok:
            return err_resp
        source_snapshot = _ensure_scrape_source(scrape_task_id)
        if source_snapshot is None:
            return jsonify({"ok": False, "error": "抓取任务不存在"}), 404
        if source_snapshot.get("kind") != "scrape":
            return jsonify({"ok": False, "error": "来源任务不是抓取任务"}), 409
        source_result = source_snapshot.get("result")
        if (
            source_snapshot.get("status") != "done"
            or not isinstance(source_result, dict)
            or not source_result.get("ok")
        ):
            return jsonify({"ok": False, "error": "抓取任务尚未成功完成"}), 409

        # T406: 从父搜索 run 读取平台身份
        try:
            parent_identity = store.get_run_checkpoint_identity(scrape_task_id)
        except _OPERATIONAL_ERRORS:
            parent_identity = None
        if parent_identity is None:
            parent_platform = str(source_snapshot.get("platform") or "boss")
        else:
            parent_platform = parent_identity.get("platform") or "boss"
        # 客户端显式 platform 与父平台不一致
        if request_platform and request_platform != parent_platform:
            return jsonify({
                "ok": False, "error": "parent_platform_mismatch",
                "message": "客户端平台与父搜索 run 平台不一致",
                "parent_platform": parent_platform,
            }), 409
        # T407: 校验 filter_schema_version
        parent_schema = parent_identity.get("filter_schema_version") if parent_identity else None
        if (filter_schema_version is not None and parent_schema is not None
                and int(filter_schema_version) != int(parent_schema)):
            return jsonify({
                    "ok": False, "error": "filter_schema_version_mismatch",
                    "message": "筛选 schema 版本与父 run 不一致",
            }), 409
        # 平台禁用检查
        from webui.platforms import get_platform_or_none
        platform_info = get_platform_or_none(parent_platform)
        if platform_info is not None and not platform_info.enabled_for_new_tasks:
            return jsonify({"ok": False, "error": "platform_disabled"}), 503
        # 同一抓取任务只允许一个 AI 筛选工作线程；防止多标签页重复提交。
        with _pipeline_lock:
            for existing_id, existing in _pipeline_tasks.items():
                if (existing.get("kind") == "ai_screen"
                        and existing.get("source_task_id") == scrape_task_id
                        and existing.get("status") in ("queued", "running")):
                    return jsonify({
                        "ok": False, "error": "already_running",
                        "existing_task_id": existing_id,
                        "message": "同一抓取任务已有 AI 筛选在运行",
                    }), 409
        task_id = uuid.uuid4().hex

        # 逻辑隔离：AI 筛选也不能与其它 pipeline 任务（抓取/重抓/暂停）并发。
        if _has_active_pipeline_task():
            return jsonify({
                "ok": False, "error": "browser_busy",
                "message": "当前已有任务在运行或暂停，请先等待、继续或结束任务后再开始新任务",
            }), 409

        # paused 就地继续；服务重启打断的 interrupted（error_code=restart）
        # 也可以被“重新开始 AI 筛选”继承断点，但保留旧 run 的终态记录。
        resume_from_run_id = ""
        prev = None
        try:
            prev = store.latest_screening_run_for_source(
                scrape_task_id, statuses=("paused",))
            if prev is None:
                prev = store.latest_screening_run_for_source(
                    scrape_task_id, statuses=("interrupted",))
        except _OPERATIONAL_ERRORS as exc:
            return jsonify({
                "ok": False,
                "error": "resume_state_unavailable",
                "detail": type(exc).__name__,
            }), 503
        if prev is not None:
            prev_params = prev.get("execution_params") or {}
            same_fields = prev.get("frozen_filters") == screening_fields
            same_profile = str(prev_params.get("profile_summary", "")) == profile_summary
            same_facts = str(prev_params.get("profile_facts") or "") == str(profile_facts or "")
            restart_interrupted = (
                prev["status"] == "interrupted"
                and str(prev.get("error_code") or "") == "restart"
            )
            if same_fields and same_profile and same_facts and (
                    prev["status"] == "paused" or restart_interrupted):
                resume_from_run_id = prev["id"]
        if resume_from_run_id and prev is not None and prev["status"] == "paused":
            # paused run 就地转为 running，保持唯一任务身份和 canonical 状态。
            try:
                claimed = store.claim_paused_screening_run(resume_from_run_id)
            except _OPERATIONAL_ERRORS as exc:
                return jsonify({
                    "ok": False,
                    "error": "resume_claim_failed",
                    "detail": type(exc).__name__,
                }), 503
            if not claimed:
                return jsonify({
                    "ok": False,
                    "error": "resume_already_claimed",
                }), 409
            task_id = resume_from_run_id
        claimed_old_resume = False
        if (resume_from_run_id and prev is not None
                and prev["status"] == "interrupted"):
            if not _claim_resume(resume_from_run_id):
                return jsonify({
                    "ok": False, "error": "already_running",
                    "message": _MSG_TASK_ALREADY_RUNNING,
                }), 409
            claimed_old_resume = True
        claimed_task, previous_task = _claim_pipeline_task_id(
            task_id, "ai_screen",
            started_at=(
                _iso_epoch_ms((prev or {}).get("started_at"))
                if resume_from_run_id and prev is not None else None
            ),
        )
        if claimed_task is None:
            if (resume_from_run_id and prev is not None
                    and prev["status"] == "paused"):
                store.update_screening_run(resume_from_run_id, status="paused")
            if claimed_old_resume:
                _release_resume_claim(resume_from_run_id)
            return jsonify({
                "ok": False, "error": "already_running",
            }), 409
        claimed_task["source_task_id"] = scrape_task_id
        account_source = prev if resume_from_run_id else None
        if resume_from_run_id:
            claimed_task["resumed_from"] = resume_from_run_id
        claimed_task["browser_account"] = _account_for_run(account_source)
        claimed_task["platform"] = parent_platform
        # T407: 生成 AI 阶段 task_input_digest
        ai_digest = hashlib.sha256(json.dumps({
            "platform": parent_platform,
            "scrape_task_id": scrape_task_id,
            "filter_schema_version": filter_schema_version,
            "screening_fields": {k: sorted(v) if isinstance(v, list) else v
                                 for k, v in screening_fields.items()},
            "browser_account": claimed_task.get("browser_account"),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        claimed_task["task_input_digest"] = ai_digest
        if resume_from_run_id and prev is not None:
            resume_params = dict(prev.get("execution_params") or {})
            if not str(resume_params.get("browser_account") or ""):
                resume_params["browser_account"] = _account_for_run(prev)
                store.update_screening_execution_params(resume_from_run_id, resume_params)
        _activate_run_browser(account_source)
        # T407: 创建 AI run 时保存平台身份和筛选快照
        if not resume_from_run_id:
            try:
                store.create_screening_run(
                    task_id,
                    frozen_filters=screening_fields,
                    source_count=0,
                    execution_params={
                        "platform": parent_platform,
                        "filter_schema_version": filter_schema_version,
                        "screening_fields": screening_fields,
                        "profile_summary": profile_summary,
                        "profile_facts": profile_facts,
                        "scrape_task_id": scrape_task_id,
                        "browser_account": claimed_task.get("browser_account"),
                        "task_input_digest": ai_digest,
                    },
                    backend_version=_backend_version,
                )
                store.save_filter_snapshot(
                    task_id,
                    platform=parent_platform,
                    filter_schema_version=filter_schema_version,
                    filter_snapshot=screening_fields,
                    task_input_digest=ai_digest,
                )
            except _OPERATIONAL_ERRORS as exc:
                _release_pipeline_claim(task_id, claimed_task, previous_task)
                return jsonify({
                    "ok": False,
                    "error": "ai_screen_persist_failed",
                    "detail": type(exc).__name__,
                }), 503
        try:
            _pipeline_executor.submit(
                _run_ai_screen_task, task_id, screening_fields,
                profile_summary, scrape_task_id, resume_from_run_id,
                profile_facts,
            )
        except RuntimeError:
            _release_pipeline_claim(task_id, claimed_task, previous_task)
            if (resume_from_run_id and prev is not None
                    and prev["status"] == "paused"):
                store.update_screening_run(resume_from_run_id, status="paused")
            if claimed_old_resume:
                _release_resume_claim(resume_from_run_id)
            raise
        if claimed_old_resume:
            try:
                store.update_screening_run(
                    resume_from_run_id,
                    error_code="resumed",
                    error_reason="已由新任务接管续跑",
                )
                store.append_task_event(
                    resume_from_run_id, "resume", {"task_id": task_id})
            except _OPERATIONAL_ERRORS:
                pass
        return jsonify({
            "ok": True, "task_id": task_id,
            "resuming": bool(resume_from_run_id),
            "platform": parent_platform,
            "filter_schema_version": filter_schema_version,
            "task_input_digest": ai_digest,
        })

    def _build_source_summary_and_outcomes(run_id):
        """T405: 从持久化 screening_source_attempts 汇总 source outcomes。

        按 combo 最新 attempt 汇总，不从岗位数为零反推 empty。
        返回 (source_summary, source_outcomes)。
        """
        try:
            attempts = store.list_latest_source_attempts(run_id)
        except _OPERATIONAL_ERRORS:
            attempts = []
        outcomes = []
        counts = {"non_empty": 0, "empty": 0, "failed": 0, "paused": 0}
        for a in attempts:
            outcomes.append({
                "combo_key": a["combo_key"],
                "attempt_no": a["attempt_no"],
                "outcome_kind": a["outcome_kind"],
                "job_count": a["job_count"],
                "input_hash": a["input_hash"],
                "error_code": a["error_code"],
            })
            if a["outcome_kind"] in counts:
                counts[a["outcome_kind"]] += 1
        summary = {"total_combos": len(outcomes), **counts}
        return summary, outcomes

    def _check_run_identity_conflict(run_id, task_dict):
        """T405: 校验内存 task 与 DB run 的 platform/task_input_digest 一致。

        返回 (db_run, error_response)。一致时 error_response=None；
        不一致时 error_response 为 409 响应。
        """
        mem_platform = (task_dict or {}).get("platform")
        mem_digest = (task_dict or {}).get("task_input_digest")
        try:
            db_run = store.get_screening_run(run_id)
        except _OPERATIONAL_ERRORS:
            db_run = None
        if db_run is not None and mem_platform:
            db_platform = db_run.get("platform")
            db_digest = db_run.get("task_input_digest")
            if db_platform and db_platform != mem_platform:
                return None, (jsonify({
                    "ok": False,
                    "error": "run_identity_conflict",
                    "message": "内存任务平台与数据库记录不一致",
                }), 409)
            if db_digest and mem_digest and db_digest != mem_digest:
                return None, (jsonify({
                    "ok": False,
                    "error": "run_identity_conflict",
                    "message": "内存任务输入摘要与数据库记录不一致",
                }), 409)
        return db_run, None

    @app.route("/api/search-progress/<task_id>")
    def search_progress(task_id):
        """Poll the progress of a pipeline run.

        Returns ``{status, progress, logs, result, error}``. ``status`` is one
        of ``running`` / ``done`` / ``failed``. ``result`` (present when done)
        carries the matched ``jobs`` and counts.
        """
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)
            if task is None:
                return jsonify({"ok": False, "error": _MSG_TASK_NOT_FOUND}), 404
            # T405: 内存 task 与 DB run 身份一致性校验
            db_run, conflict = _check_run_identity_conflict(task_id, task)
            if conflict is not None:
                return conflict
            # 终态补结束时间戳（首次进入终态时记一次），供前端计时器显示真实用时
            if task["status"] in ("done", "failed", "cancelled") and task.get("finished_at") is None:
                task["finished_at"] = int(time.time() * 1000)
            # T405: 按 combo 最新 attempt 汇总 source outcomes
            source_summary, source_outcomes = _build_source_summary_and_outcomes(task_id)
            snapshot = {
                "ok": True,
                "kind": task.get("kind", ""),
                "status": _public_task_status(
                    task["status"], (db_run or {}).get("interruption_kind")),
                "progress": task["progress"],
                "logs": list(task["logs"][-LOG_TAIL_LINES:]),
                "error": task["error"],
                "started_at": task.get("started_at"),
                "finished_at": task.get("finished_at"),
                "config_digest": task.get("config_digest"),
                "scope_digest": task.get("scope_digest"),
                # T405: 平台身份与 source outcomes 汇总
                "platform": task.get("platform") or (db_run or {}).get("platform"),
                "task_input_digest": task.get("task_input_digest") or (db_run or {}).get("task_input_digest"),
                "source_summary": source_summary,
                "source_outcomes": source_outcomes,
            }
            if task["status"] in ("done", "failed") and task["result"] is not None:
                # 原样返回整个 result：抓取任务含 jobs/计数；
                # AI 筛选任务还含 dropped/verdict/profile_summary 等
                snapshot["result"] = task["result"]
        return jsonify(snapshot)

    def _has_newer_saved_result_than(timestamp: str | None) -> bool:
        """True when a result snapshot was saved after the given DB timestamp."""
        if not timestamp:
            return False
        try:
            saved_at = store.latest_pipeline_result_saved_at()
        except _OPERATIONAL_ERRORS:
            return False
        # 顺序写入且时间戳相同时也视为“已有更新快照”，避免同微秒下旧任务被误恢复。
        return bool(saved_at and str(saved_at) >= str(timestamp))

    @app.route("/api/latest-running-task")
    def latest_running_task():
        """返回最近一个仍在运行（running/queued）的 pipeline 任务。

        用于页面刷新后接回任务：前端 onMounted 调这个接口，有在跑的任务
        就恢复 task_id 和进度快照，重新开始轮询。dict 保序（Py3.7+），
        最后注册的任务排在最后，倒序找第一个非终态的返回。

        查找顺序（FR-028/FR-037）：
        1. 内存中 running/queued 任务（刷新接回）
        2. DB 中最近 paused 任务（服务重启后恢复暂停态）
        3. DB 中最近 interrupted 筛选（服务重启打断的工作线程）
        4. 无任务
        """
        from webui.pipeline_exec import failed_code_label
        with _pipeline_lock:
            for task_id, task in reversed(list(_pipeline_tasks.items())):
                try:
                    _mem_db_ep = ((store.get_screening_run(task_id) or {}).get("execution_params") or {})
                except _OPERATIONAL_ERRORS:
                    _mem_db_ep = {}
                if task["status"] in ("running", "queued"):
                    return jsonify({
                        "ok": True,
                        "has_task": True,
                        "task_id": task_id,
                        "kind": task.get("kind", ""),
                        "status": task["status"],
                        "progress": task["progress"],
                        "stage": task.get("stage") or (task.get("progress") or {}).get("stage", ""),
                        "logs": list(task["logs"][-LOG_TAIL_LINES:]),
                        "error": task["error"],
                        "started_at": task.get("started_at"),
                        "finished_at": task.get("finished_at"),
                        # T409 契约 http-api.md L200-202：所有 has_task=true
                        # 响应增加 platform 和 task_input_digest。内存任务读取
                        # 注册时冻结值，不得因缺平台补成 BOSS。
                        "platform": task.get("platform"),
                        "task_input_digest": task.get("task_input_digest"),
                        "auto_screen": bool(task.get("auto_screen") or _mem_db_ep.get("auto_screen")),
                        "auto_screen_fields": _mem_db_ep.get("auto_screen_fields") or {},
                        "auto_screen_profile": str(_mem_db_ep.get("auto_screen_profile") or ""),
                    })
        # 2. DB 中最近 paused（服务重启后恢复暂停态，FR-028）
        try:
            with store._connection() as conn:
                prow = conn.execute(
                    "SELECT id, status, current_stage, error_code, error_reason, "
                    "processed_count, source_count, pending_count, match_count, "
                    "mismatch_count, total_dropped, backend_version, updated_at "
                    "FROM screening_runs WHERE status = 'paused' "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
        except (sqlite3.Error, RuntimeError):
            prow = None
        if prow is not None and _has_newer_saved_result_than(prow["updated_at"]):
            prow = None
        if prow is not None:
            paused_run = store.get_screening_run(prow["id"]) or {}
            execution_params = paused_run.get("execution_params") or {}
            paused_error_reason = (
                prow["error_reason"]
                or failed_code_label(
                    prow["error_code"], str(paused_run.get("platform") or "")
                )
                or prow["error_code"]
                or "任务已暂停"
            )
            paused_kind = _pipeline_kind_for_stage(prow["current_stage"] or "")
            paused_source_task_id = (
                str(execution_params.get("scrape_task_id") or "") or prow["id"]
            )
            paused_scraped_count = store.count_scrape_run_jobs(paused_source_task_id)
            return jsonify({
                "ok": True,
                "has_task": True,
                "task_id": prow["id"],
                "kind": paused_kind,
                "status": "paused",
                "stage": prow["current_stage"],
                "progress": {
                    "processed": prow["processed_count"],
                    "total": prow["source_count"],
                    "pending": prow["pending_count"],
                    "match": prow["match_count"],
                    "mismatch": prow["mismatch_count"],
                    "dropped": prow["total_dropped"],
                    "message": paused_error_reason,
                },
                "logs": [],
                "error": "",
                "pause_info": {
                    "error_code": prow["error_code"],
                    "error_reason": paused_error_reason,
                },
                "backend_version": prow["backend_version"],
                "current_version": _backend_version,
                "version_match": (prow["backend_version"] == _backend_version),
                "started_at": _iso_epoch_ms(paused_run.get("started_at")),
                "finished_at": _iso_epoch_ms(paused_run.get("finished_at")),
                "resumable": True,
                "source": "database",
                "scrape_task_id": execution_params.get("scrape_task_id"),
                "scrape_completed": _scrape_completed_for_run(execution_params),
                "auto_screen": bool(execution_params.get("auto_screen")),
                "auto_screen_fields": execution_params.get("auto_screen_fields") or {},
                "auto_screen_profile": str(execution_params.get("auto_screen_profile") or ""),
                "profile_summary": str(execution_params.get("profile_summary") or ""),
                "profile_facts": execution_params.get("profile_facts"),
                "source_run_id": execution_params.get("source_run_id"),
                "checkpoint_stage": prow["current_stage"],
                # T409 契约 http-api.md L200-202：DB paused 从 screening_runs
                # 读取 platform/task_input_digest；source 字段只表示状态数据
                # 来源，不能承载招聘平台。
                "platform": paused_run.get("platform"),
                "task_input_digest": paused_run.get("task_input_digest"),
                "scraped_count": paused_scraped_count,
                "source_total": int(prow["source_count"] or 0),
            })
        # 3. DB 中被进程重启打断的筛选。重启后工作线程已死，
        # 不能假装还在跑——如实告诉前端有个可续跑的中断任务。
        try:
            run = store.latest_interrupted_screening_run()
        except _OPERATIONAL_ERRORS as exc:
            return jsonify({
                "ok": False,
                "error": "task_state_unavailable",
                "detail": type(exc).__name__,
            }), 503
        if run is not None and _has_newer_saved_result_than(run.get("updated_at")):
            run = None
        if run is not None:
            interrupted_params = run.get("execution_params") or {}
            interrupted_source_task_id = (
                str(interrupted_params.get("scrape_task_id") or "") or run["id"]
            )
            interrupted_scraped_count = store.count_scrape_run_jobs(interrupted_source_task_id)
            interrupted_kind = _pipeline_kind_for_stage(run.get("current_stage") or "")
            if interrupted_kind == "scrape":
                interrupted_message = "上次抓取因服务重启被中断；已抓数据已保存"
            elif interrupted_kind == "recrawl":
                interrupted_message = "上次补抓因服务重启被中断；可结束保存已有结果"
            else:
                interrupted_message = "上次 AI 筛选因服务重启被中断"
            return jsonify({
                "ok": True,
                "has_task": True,
                "task_id": run["id"],
                "kind": _pipeline_kind_for_stage(run.get("current_stage") or ""),
                "status": "interrupted",
                "progress": {"message": interrupted_message},
                "logs": [],
                "error": "",
                "started_at": _iso_epoch_ms(run.get("started_at")),
                "finished_at": _iso_epoch_ms(run.get("finished_at")),
                "resumable": True,
                "error_code": run.get("error_code"),
                "source_run_id": (run.get("execution_params") or {}).get("source_run_id"),
                "scrape_task_id": (run.get("execution_params") or {}).get("scrape_task_id"),
                "scrape_completed": _scrape_completed_for_run(run.get("execution_params") or {}),
                "auto_screen": bool((run.get("execution_params") or {}).get("auto_screen")),
                "frozen_filters": run.get("frozen_filters") or {},
                "profile_summary": str((run.get("execution_params") or {}).get("profile_summary") or ""),
                "profile_facts": (run.get("execution_params") or {}).get("profile_facts"),
                # T409 契约 http-api.md L200-202：DB interrupted 从
                # screening_runs 读取 platform/task_input_digest。
                "platform": run.get("platform"),
                "task_input_digest": run.get("task_input_digest"),
                "scraped_count": interrupted_scraped_count,
                "source_total": int(run.get("source_count") or 0),
            })
        # 3.5 已完成抓取 + auto_screen 未消费：刷新后自动接 AI 筛选。
        try:
            with store._connection() as conn:
                auto_rows = conn.execute(
                    "SELECT id, platform, current_stage, source_count, "
                    "execution_params_json, updated_at "
                    "FROM screening_runs WHERE status = 'succeeded' "
                    "AND current_stage = 'scrape' "
                    "ORDER BY updated_at DESC LIMIT 20"
                ).fetchall()
        except (sqlite3.Error, RuntimeError):
            auto_rows = []
        for auto_row in auto_rows:
            if _has_newer_saved_result_than(auto_row["updated_at"]):
                continue
            try:
                auto_params = json.loads(auto_row["execution_params_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                auto_params = {}
            if not bool(auto_params.get("auto_screen")):
                continue
            auto_scraped_count = store.count_scrape_run_jobs(auto_row["id"])
            if auto_scraped_count <= 0:
                continue
            return jsonify({
                "ok": True,
                "has_task": True,
                "task_id": auto_row["id"],
                "kind": "scrape",
                "status": "completed",
                "stage": "scrape",
                "progress": {"message": "抓取已完成，等待 AI 筛选"},
                "logs": [],
                "error": "",
                "resumable": False,
                "source": "database",
                "auto_screen": True,
                "scrape_task_id": auto_row["id"],
                "scrape_completed": True,
                "frozen_filters": auto_params.get("auto_screen_fields") or {},
                "profile_summary": str(auto_params.get("auto_screen_profile") or ""),
                "profile_facts": auto_params.get("auto_screen_facts"),
                "platform": auto_row["platform"],
                "task_input_digest": auto_params.get("task_input_digest"),
                "scraped_count": auto_scraped_count,
                "source_total": int(auto_row["source_count"] or 0),
            })
        # 4. failed 抓取兜底：有已持久化岗位的任务刷新后可恢复显示真实数量。
        try:
            with store._connection() as conn:
                failed_rows = conn.execute(
                    "SELECT * FROM screening_runs WHERE status = 'failed' "
                    "AND current_stage = 'scrape' ORDER BY updated_at DESC LIMIT 20"
                ).fetchall()
        except (sqlite3.Error, RuntimeError):
            failed_rows = []
        for failed_row in failed_rows:
            failed_run = store.get_screening_run(failed_row["id"]) or {}
            if not failed_run or failed_run.get("error_code") == "user_finished":
                continue
            if _has_newer_saved_result_than(failed_run.get("updated_at")):
                continue
            failed_scraped_count = store.count_scrape_run_jobs(failed_run["id"])
            if failed_scraped_count <= 0:
                continue
            failed_params = failed_run.get("execution_params") or {}
            failed_error_reason = (
                failed_run.get("error_reason")
                or failed_code_label(
                    failed_run.get("error_code"), str(failed_run.get("platform") or "")
                )
                or failed_run.get("error_code")
                or "抓取失败"
            )
            return jsonify({
                "ok": True,
                "has_task": True,
                "task_id": failed_run["id"],
                "kind": "scrape",
                "status": "failed",
                "stage": "scrape",
                "progress": {
                    "message": failed_error_reason,
                },
                "logs": [],
                "error": failed_error_reason,
                "pause_info": {
                    "error_code": failed_run.get("error_code"),
                    "error_reason": failed_error_reason,
                },
                "resumable": True,
                "source": "database",
                "scrape_task_id": failed_run["id"],
                "auto_screen": bool(failed_params.get("auto_screen")),
                "platform": failed_run.get("platform"),
                "task_input_digest": failed_run.get("task_input_digest"),
                "scraped_count": failed_scraped_count,
                "source_total": int(failed_run.get("source_count") or 0),
                "execution_params": failed_params,
            })
        return jsonify({"ok": True, "has_task": False})

    @app.route("/api/latest-pipeline-result")
    def latest_pipeline_result():
        """Return the persisted latest pipeline run (survives page refresh).

        T409: 支持 platform/run_id 过滤查询；返回平台身份和 source_outcomes。

        Only a successful run is persisted, so this always reflects the most
        recent good data.  ``has_result`` is false until the first successful
        run (or if the file is missing/unreadable).

        传入 ``profile_id`` 时，给当前 profile 已标记 interested 的岗位补
        ``_marked: "interested"``，使刷新后「已感兴趣」按钮状态能正确回显
        （跨刷新持久化，见 spec）。匹配按 canonical_url——pipeline 结果的
        ``job_id`` 是 BOSS 岗位 id，profile_jobs.job_id 是内部 UUID，二者
        不能直接相等，统一用规范化链接对齐（同 _build_zone_canonical_urls）。
        """
        query_platform = request.args.get("platform", "").strip() or None
        query_run_id = request.args.get("run_id", "").strip() or None
        # T409: 精确 run_id 查询
        if query_run_id:
            try:
                run = store.get_screening_run(query_run_id)
            except _OPERATIONAL_ERRORS:
                run = None
            if run is None:
                return jsonify({"ok": True, "has_result": False})
            # 只返回已完成或部分完成的结果
            if run["status"] not in ("succeeded", "partial"):
                return jsonify({"ok": True, "has_result": False})
            # T409: run_id + platform 必须一致
            if query_platform and query_platform != run.get("platform"):
                return jsonify({
                    "ok": False, "error": "run_platform_conflict",
                    "message": "run_id 与 platform 不一致",
                }), 409
            # 从该 run 的 result snapshot 构造响应
            payload = store.load_latest_pipeline_result(query_run_id)
            if payload is None:
                return jsonify({"ok": True, "has_result": False})
        # T409: 按平台过滤
        elif query_platform:
            payload = store.load_latest_pipeline_result_for_platform(query_platform)
        else:
            payload = store.load_latest_pipeline_result()
        if payload is None:
            return jsonify({"ok": True, "has_result": False})
        result = payload["result"]
        jobs = result.get("jobs", [])
        run_id = payload.get("run_id", "")
        # T409: 汇总 source outcomes
        source_summary, source_outcomes = _build_source_summary_and_outcomes(run_id)

        profile_id = request.args.get("profile_id")
        if profile_id and isinstance(jobs, list) and jobs:
            try:
                store.get_profile(profile_id)
            except KeyError:
                profile_id = None
            if profile_id:
                interested_pjs = store.list_screening_interested(profile_id)
                # 批量预取，避免逐条 store.get_job 的 N+1
                interested_jobs = store.list_jobs_by_ids([pj["job_id"] for pj in interested_pjs])
                interested_urls = set()
                interested_slugs = set()

                def _collect_url_keys(pj_rows, job_map):
                    """把 profile_jobs 行换成 (canonical_url, slug) 匹配键集合。"""
                    urls = set()
                    slugs = set()
                    for pj in pj_rows:
                        stored = job_map.get(str(pj["job_id"]))
                        if not stored:
                            continue
                        url = normalize_job_link(stored.get("canonical_url", ""))
                        if not url:
                            continue
                        urls.add(url)
                        # 从 URL 路径提取平台岗位 slug 作为备用匹配（boss .html / 智联 .htm）
                        slug = url.rstrip("/").rsplit("/", 1)[-1]
                        for suffix in (".html", ".htm"):
                            slug = slug.removesuffix(suffix)
                        if slug:
                            slugs.add(slug)
                    return urls, slugs

                interested_urls, interested_slugs = _collect_url_keys(
                    interested_pjs, interested_jobs)
                # 投递状态：applied_at 非空即“投递过”（含已投递后跟进/荒废的状态变迁）
                applied_pjs = [
                    pj for pj in store.list_profile_jobs(profile_id)
                    if pj.get("applied_at")
                ]
                applied_jobs = store.list_jobs_by_ids([pj["job_id"] for pj in applied_pjs])
                applied_urls, applied_slugs = _collect_url_keys(applied_pjs, applied_jobs)
                if interested_urls or interested_slugs or applied_urls or applied_slugs:
                    for item in jobs:
                        if not isinstance(item, dict):
                            continue
                        url = normalize_job_link(
                            item.get("source_url") or item.get("job_link") or ""
                        )
                        if (url and url in interested_urls) or (interested_slugs and str(item.get("job_id", "")) in interested_slugs):
                            item["_marked"] = "interested"
                        if (url and url in applied_urls) or (applied_slugs and str(item.get("job_id", "")) in applied_slugs):
                            item["_applied"] = True

        return jsonify({
            "ok": True,
            "has_result": True,
            "source_run_id": payload.get("run_id"),
            "platform": payload.get("platform"),
            "status": payload.get("status", "completed"),
            "scrape_task_id": str(payload.get("scrape_task_id") or ""),
            "saved_at": payload.get("saved_at"),
            "started_at": _iso_epoch_ms(payload.get("started_at")),
            "finished_at": _iso_epoch_ms(payload.get("finished_at")),
            "script_params": payload.get("script_params", {}),
            "execution_config": payload.get("execution_config", {}),
            "source_summary": source_summary,
            "source_outcomes": source_outcomes,
            "source_evidence_available": True,
            "result": {
                "total_scraped": result.get("total_scraped", 0),
                "total_matched": result.get("total_matched", 0),
                "total_kept": result.get("total_kept", 0),
                "total_dropped": result.get("total_dropped", 0),
                "combinations": result.get("combinations", 0),
                "jobs": jobs,
                "dropped": result.get("dropped", []),
                "profile_summary": result.get("profile_summary", ""),
                "profile_facts": result.get("profile_facts"),
            },
        })

    @app.route("/api/pipeline-result/export.csv")
    def export_pipeline_result_csv():
        """导出最终结果页数据：匹配的在前、不匹配的在后，各自带分组标志行。

        数据源与 ``/api/latest-pipeline-result`` 完全同源；支持 platform /
        run_id 参数，语义与该接口一致。每个岗位行带岗位直达链接。
        """
        query_platform = request.args.get("platform", "").strip() or None
        query_run_id = request.args.get("run_id", "").strip() or None
        if query_run_id:
            try:
                run = store.get_screening_run(query_run_id)
            except _OPERATIONAL_ERRORS:
                run = None
            if run is None:
                return jsonify({
                    "error_code": "not_found", "user_message": _MSG_TASK_NOT_FOUND,
                }), 404
            if run["status"] not in ("done", "succeeded", "partial"):
                return jsonify({
                    "error_code": "result_not_ready",
                    "user_message": "结果尚未完成，暂无法导出",
                }), 409
            if query_platform and query_platform != run.get("platform"):
                return jsonify({
                    "error_code": "run_platform_conflict",
                    "user_message": "run_id 与 platform 不一致",
                }), 409
            payload = store.load_latest_pipeline_result(query_run_id)
        elif query_platform:
            payload = store.load_latest_pipeline_result_for_platform(query_platform)
        else:
            payload = store.load_latest_pipeline_result()
        if payload is None:
            return jsonify({
                "error_code": "not_found", "user_message": "暂无可导出的结果",
            }), 404

        result = payload["result"]
        columns = [
            "title", "company", "salary", "location", "experience", "degree",
            "reason", "job_link",
        ]
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()

        def _write_section(label, rows):
            section_row = {column: "" for column in columns}
            section_row["title"] = label
            writer.writerow(section_row)
            for row in rows:
                writer.writerow(row)

        matched_rows = [
            {
                **job,
                "reason": "",
                "job_link": job.get("canonical_url") or job.get("source_url") or "",
            }
            for job in (result.get("jobs") or []) if isinstance(job, dict)
        ]
        dropped_rows = [
            {
                **job,
                "job_link": job.get("canonical_url") or job.get("source_url") or "",
            }
            for job in (result.get("dropped") or []) if isinstance(job, dict)
        ]
        _write_section("匹配：", matched_rows)
        _write_section("不匹配：", dropped_rows)
        platform_label = payload.get("platform") or "all"
        return app.response_class(
            "\ufeff" + buffer.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=career_scout_jobs_{platform_label}.csv"
                ),
            },
        )

    # ------------------------------------------------------------------
    # Pipeline 结果增强：按需抓 JD 详情 + 感兴趣/不感兴趣（接入筛选工作台）
    # ------------------------------------------------------------------

    @app.route("/api/reset-latest-result", methods=["POST"])
    def reset_latest_result():
        """T418: 无 run_id 归档当前结果；有 run_id 保留日志删除历史轮次。"""
        body = request.get_json(silent=True) or {}
        target_run_id = str(body.get("run_id") or "").strip() or None
        archived_run_ids = None
        if target_run_id:
            run = store.get_screening_run(target_run_id)
            if run is None or run.get("record_kind") != "result_snapshot":
                return jsonify({"ok": False, "error": "run_not_found"}), 404
            request_platform = str(body.get("platform") or "").strip() or None
            if request_platform and request_platform != run.get("platform"):
                return jsonify({
                    "ok": False, "error": "run_platform_conflict",
                    "message": "请求平台与目标 run 不一致",
                }), 409
            cleared = store.delete_history_result_preserving_logs(target_run_id)
            platform = run.get("platform")
        else:
            archived_run_ids = store.archive_all_current_results()
            cleared = True
            platform = None
        return jsonify({"ok": True, "cleared": cleared, "run_id": target_run_id,
                        "platform": platform,
                        "archived": archived_run_ids})

    _job_detail_lock = threading.Lock()

    @app.route("/api/job-detail", methods=["POST"])
    def job_detail():
        """T417: 按需抓取单个岗位的 JD 正文。

        source_run_id + platform_job_id 为权威；从 source run 继承冻结平台。
        """
        from webui.pipeline_exec import ensure_chrome_ready

        raw = request.get_json(silent=True) or {}
        job_id = str(raw.get("job_id") or "").strip()
        platform_job_id = str(raw.get("platform_job_id") or job_id).strip()
        source_run_id = str(raw.get("source_run_id") or "").strip() or None
        raw_source_url = str(raw.get("source_url") or raw.get("job_link") or "")
        if not job_id or not raw_source_url.strip():
            return jsonify({"ok": False, "error": "缺少 job_id 或 source_url"}), 400

        # T417: 从 source run 继承冻结平台
        frozen_platform = "boss"
        frozen_browser_account = None
        frozen_cdp_port = None
        frozen_profile_key = None
        parent_run = None
        if source_run_id:
            try:
                identity = store.get_run_checkpoint_identity(source_run_id)
                parent_run = store.get_screening_run(source_run_id)
            except _OPERATIONAL_ERRORS:
                identity = parent_run = None
            if identity is not None:
                frozen_platform = str(identity.get("platform") or "boss")
            parent_params = (parent_run or {}).get("execution_params") or {}
            frozen_browser_account = str(parent_params.get("browser_account") or "") or None
            frozen_cdp_port = parent_params.get("cdp_port")
            frozen_profile_key = parent_params.get("profile_key")
            gp_task_id = str(parent_params.get("scrape_task_id") or "")
            if (not frozen_cdp_port or not frozen_profile_key) and gp_task_id:
                try:
                    gp = store.get_screening_run(gp_task_id)
                    gp_params = (gp or {}).get("execution_params") or {}
                    frozen_cdp_port = frozen_cdp_port or gp_params.get("cdp_port")
                    frozen_profile_key = frozen_profile_key or gp_params.get("profile_key")
                except _OPERATIONAL_ERRORS:
                    pass
        else:
            if _ZHILIAN_HOST_TOKEN in raw_source_url.lower():
                return jsonify({
                    "ok": False, "error": "run_identity_conflict",
                    "error_code": "run_identity_conflict",
                    "message": "智联单 JD 必须携带 source_run_id，不能按 URL 猜测来源",
                }), 409
        source_url = normalize_job_link_for_platform(
            raw_source_url, platform=frozen_platform
        )
        if not source_url:
            return jsonify({"ok": False, "error": "缺少合法 source_url"}), 400
        # 校验 URL 与平台一致性
        if frozen_platform == "zhilian" and _ZHILIAN_HOST_TOKEN not in source_url.lower():
            return jsonify({"ok": False, "error": "platform_url_mismatch",
                           "message": "智联岗位 URL 必须包含 zhaopin.com"}), 422
        if frozen_platform == "zhilian" and not (
                frozen_browser_account and frozen_cdp_port and frozen_profile_key):
            return jsonify({
                "ok": False, "error": "run_identity_conflict",
                "error_code": "run_identity_conflict",
                "message": "智联来源 run 缺少冻结浏览器身份",
            }), 409
        if source_run_id and parent_run is not None:
            _activate_run_browser(parent_run)
        chrome_ok, chrome_err = ensure_chrome_ready(
            frozen_cdp_port if frozen_platform == "zhilian" else None,
            minimize_after_launch=True,
        )
        if not chrome_ok:
            return jsonify({"ok": False,
                            "error": f"调试浏览器未能就绪：{chrome_err}"}), 503

        source = _make_cdp_source(
            platform=frozen_platform,
            browser_account=frozen_browser_account,
            cdp_port=frozen_cdp_port,
            profile_key=frozen_profile_key,
            run_id=source_run_id or "",
        )
        if source is None:
            return jsonify({"ok": False, "error": "抓取源不可用"}), 500

        job = {"job_id": platform_job_id, "source_url": source_url, "job_link": source_url}
        detail_path = str(
            Path(app.config["RESULT_DIR"]) / f"job_detail_{platform_job_id}.json"
        )
        with _job_detail_lock:
            outcome = source.fetch_detail(job, detail_output_path=detail_path)
        if not outcome.ok:
            return jsonify({"ok": False,
                            "error": f"详情抓取失败（{outcome.failed_code}）"}), 502
        jd = str((outcome.detail or {}).get("jd", "")).strip()
        if not jd:
            return jsonify({"ok": False,
                            "error": "详情页未提取到 JD 正文，岗位可能已下架"}), 502
        return jsonify({
            "ok": True, "jd": jd,
            "platform": frozen_platform, "platform_job_id": platform_job_id,
        })

    def _resolve_pipeline_job_identity(job):
        """Task 008：pipeline 入口统一走权威岗位身份解析（Task 003）。

        使用 Task 001 的 connection-aware 双索引 upsert，在调用方事务内
        完成可靠入库并返回内部 jobs.id；身份失败在关联写入前抛出，
        保证零副作用。BOSS 与智联共用同一协议，无平台分支。
        """
        identity_request = parse_identity_payload(
            _pipeline_identity_payload(job))
        with store._connection() as conn:
            return resolve_job_identity(conn, store, identity_request)

    def _pipeline_identity_error_response(exc: JobIdentityError):
        return jsonify({
            "ok": False,
            "error_code": exc.code,
            "user_message": str(exc),
            "details": exc.details,
        }), exc.http_status

    @app.route("/api/pipeline/jobs/interest", methods=["POST"])
    def pipeline_mark_interest():
        """标记 pipeline 结果岗位为感兴趣：权威身份入库 + profile_jobs(interested)。

        复用筛选工作台的持久感兴趣区——标记后可在工作台"感兴趣"区看到
        （list_screening_interested 不按 run_id 过滤）。响应 job_id 是内部 ID。
        """
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get("profile_id")
        job = raw.get("job") or {}
        if not profile_id:
            raise ValueError(_MSG_PROFILE_ID_REQUIRED)
        try:
            store.get_profile(profile_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": _MSG_PROFILE_NOT_FOUND}), 404
        try:
            resolved = _resolve_pipeline_job_identity(job)
        except JobIdentityError as exc:
            return _pipeline_identity_error_response(exc)
        store.mark_screening_interest(profile_id, resolved.job_id, run_id=None)
        return jsonify({"interest_state": "interested", "job_id": resolved.job_id})

    @app.route("/api/pipeline/jobs/reject", methods=["POST"])
    def pipeline_mark_reject():
        """标记 pipeline 结果岗位为不感兴趣：权威身份入库 + profile_jobs(deleted)。

        标记后进入筛选工作台垃圾桶区。响应 job_id 是内部 ID。
        """
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get("profile_id")
        job = raw.get("job") or {}
        if not profile_id:
            raise ValueError(_MSG_PROFILE_ID_REQUIRED)
        try:
            store.get_profile(profile_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": _MSG_PROFILE_NOT_FOUND}), 404
        try:
            resolved = _resolve_pipeline_job_identity(job)
        except JobIdentityError as exc:
            return _pipeline_identity_error_response(exc)
        store.mark_screening_reject(profile_id, resolved.job_id, run_id=None)
        return jsonify({"reject_state": "rejected", "job_id": resolved.job_id})

    @app.route("/api/pipeline/jobs/interest/cancel", methods=["POST"])
    def pipeline_cancel_interest():
        """撤销 pipeline 结果岗位的感兴趣标记：profile_jobs.status 回退。

        payload 结构与 /api/pipeline/jobs/interest 一致（profile_id + job）；
        岗位必须能通过权威三元组或内部 ID 解析。幂等——即便当前不是
        interested 也不报错，使前端"感兴趣"按钮可再次点击取消。
        """
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get("profile_id")
        job = raw.get("job") or {}
        if not profile_id or not isinstance(job, dict):
            return jsonify({"error": "missing profile_id or job"}), 400
        try:
            store.get_profile(profile_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": _MSG_PROFILE_NOT_FOUND}), 404
        try:
            resolved = _resolve_pipeline_job_identity(job)
        except JobIdentityError as exc:
            return _pipeline_identity_error_response(exc)
        try:
            store.cancel_screening_interest(profile_id, resolved.job_id)
        except sqlite3.Error as exc:
            return jsonify({"error": f"撤销感兴趣失败: {exc}"}), 500
        return jsonify({"interest_state": "cancelled", "job_id": resolved.job_id})

    @app.route("/api/pipeline/jobs/<job_id>/jd", methods=["POST"])
    def pipeline_job_refetch_jd(job_id):
        """为单个岗位补抓 JD 并回写数据库中对应 job 项。

        用于 JD 抓取失败/缺失的岗位补抓；不重跑 AI、不跨 tab。与
        /api/job-detail 共用 _job_detail_lock 串行化，避免并发争抢 CDP。
        """
        raw = request.get_json(silent=True) or {}
        source_run_id = str(raw.get("source_run_id") or "").strip()
        if source_run_id:
            if store.get_pending_result(source_run_id, job_id) is None:
                return jsonify({
                    "ok": False, "error": "not_pending",
                    "message": "只能补抓当前待确认岗位",
                }), 409
            with _pipeline_lock:
                for existing_id, task in _pipeline_tasks.items():
                    if (task.get("kind") == "recrawl"
                            and task.get("source_run_id") == source_run_id
                            and task.get("status") in ("queued", "running")):
                        return jsonify({
                            "ok": False, "error": "already_running",
                            "existing_task_id": existing_id,
                        }), 409
            task_id = f"recrawl-{uuid.uuid4().hex[:12]}"
            # T406: 从父 run 读取冻结平台身份和浏览器身份
            parent_identity = None
            parent_run = None
            try:
                parent_identity = store.get_run_checkpoint_identity(source_run_id)
                parent_run = store.get_screening_run(source_run_id)
            except _OPERATIONAL_ERRORS:
                pass
            parent_platform = (parent_identity or {}).get("platform") or "boss"
            parent_task_input_digest = (parent_identity or {}).get("task_input_digest")
            parent_params = (parent_run or {}).get("execution_params") or {}
            parent_browser_account = str(parent_params.get("browser_account") or "") or None
            parent_cdp_port = parent_params.get("cdp_port")
            parent_profile_key = parent_params.get("profile_key")
            gp_task_id = str(parent_params.get("scrape_task_id") or "")
            if (not parent_cdp_port or not parent_profile_key) and gp_task_id:
                try:
                    grandparent = store.get_screening_run(gp_task_id)
                    gp_params = (grandparent or {}).get("execution_params") or {}
                    parent_cdp_port = parent_cdp_port or gp_params.get("cdp_port")
                    parent_profile_key = parent_profile_key or gp_params.get("profile_key")
                except _OPERATIONAL_ERRORS:
                    pass
            _register_pipeline_task(task_id, "recrawl")
            with _pipeline_lock:
                _pipeline_tasks[task_id]["source_run_id"] = source_run_id
                _pipeline_tasks[task_id]["platform"] = parent_platform
                _pipeline_tasks[task_id]["cdp_port"] = parent_cdp_port
                _pipeline_tasks[task_id]["profile_key"] = parent_profile_key
                _pipeline_tasks[task_id]["browser_account"] = parent_browser_account or _account_for_run()
                _pipeline_tasks[task_id]["task_input_digest"] = parent_task_input_digest
            profile_summary = str(raw.get("profile_summary") or "")
            profile_facts = raw.get("profile_facts") or None
            store.create_screening_run(
                task_id,
                source_count=1,
                execution_params={
                    "source_run_id": source_run_id,
                    "job_ids": [str(job_id)],
                    "profile_summary": profile_summary,
                    "profile_facts": profile_facts,
                    "single_retry": True,
                    "browser_account": _pipeline_tasks[task_id]["browser_account"],
                    "platform": parent_platform,
                    "cdp_port": parent_cdp_port,
                    "profile_key": parent_profile_key,
                    "task_input_digest": parent_task_input_digest,
                },
                backend_version=_backend_version,
            )
            store.save_filter_snapshot(
                task_id,
                platform=parent_platform,
                task_input_digest=parent_task_input_digest,
            )
            store.update_screening_run(
                task_id, status="running", current_stage="recrawl_fetch_jd"
            )
            _activate_run_browser(parent_run)
            try:
                _pipeline_executor.submit(
                    _run_recrawl_task, task_id, [str(job_id)], profile_summary,
                    source_run_id, None, profile_facts,
                )
            except RuntimeError as exc:
                reason = f"后台任务提交失败：{type(exc).__name__}"
                store.update_screening_run(
                    task_id, status="failed", error_code="internal_error",
                    error_reason=reason,
                )
                store.append_task_event(task_id, "job_fail", {
                    "stage": "recrawl_submit",
                    "job_id": str(job_id),
                    "failed_code": "internal_error",
                    "reason": reason,
                })
                with _pipeline_lock:
                    task = _pipeline_tasks.get(task_id)
                    if task is not None:
                        task["status"] = "failed"
                        task["error"] = reason
                return jsonify({
                    "ok": False, "error": "single_retry_submit_failed",
                }), 500
            return jsonify({
                "ok": True, "task_id": task_id, "source_run_id": source_run_id,
                "single_retry": True,
            }), 202

        _activate_run_browser()
        from webui.pipeline_exec import ensure_chrome_ready

        raw_source_url = str(raw.get("source_url") or raw.get("job_link") or "")
        if not raw_source_url.strip():
            return jsonify({"ok": False, "error": "缺少 source_url 或 job_link",
                            "job_id": job_id}), 400

        if _ZHILIAN_HOST_TOKEN in raw_source_url.lower():
            return jsonify({
                "ok": False, "error": "run_identity_conflict",
                "error_code": "run_identity_conflict",
                "message": "智联补抓必须携带 source_run_id，不能按 URL 猜测身份",
            }), 409

        chrome_ok, chrome_err = ensure_chrome_ready(minimize_after_launch=True)
        if not chrome_ok:
            return jsonify({"error": f"CDP Chrome 未运行：{chrome_err}",
                            "error_code": "cdp_not_ready"}), 503

        # T406: fallback 路径从 source_url 推断平台，不依赖 latest done run
        # （最新完成 run 可能是另一平台，会误用平台身份）。智联无冻结
        # 账号/端口时 _make_cdp_source 返回 None，由下方 500 阻断默认 BOSS。
        fallback_platform = "boss"
        source = _make_cdp_source(platform=fallback_platform)
        source_url = normalize_job_link(raw_source_url)
        if source is None:
            return jsonify({"ok": False, "error": "抓取源不可用", "job_id": job_id}), 500

        job = {"job_id": job_id, "source_url": source_url, "job_link": source_url}
        detail_path = str(
            Path(app.config["RESULT_DIR"]) / f"job_detail_{job_id}.json"
        )
        try:
            with _job_detail_lock:
                outcome = source.fetch_detail(job, detail_output_path=detail_path)
        except (OSError, ValueError, RuntimeError) as exc:
            return jsonify({"ok": False, "error": str(exc), "job_id": job_id}), 500

        if not outcome.ok:
            return jsonify({"ok": False,
                            "error": f"详情抓取失败（{outcome.failed_code}），请确认已登录 BOSS 后重试",
                            "job_id": job_id}), 502
        jd = str((outcome.detail or {}).get("jd", "")).strip()
        if not jd:
            return jsonify({"ok": False,
                            "error": "详情页未提取到 JD 正文，岗位可能已下架",
                            "job_id": job_id}), 502

        # 抓到 JD 后回写数据库中匹配的 job 项，并尝试单条 AI 精筛
        persisted = False
        verdict_info: dict = {}
        payload = store.load_latest_pipeline_result()
        if payload is not None:
            result = payload.get("result") or {}
            jobs = result.get("jobs") or []
            matched = False
            for item in jobs:
                if isinstance(item, dict) and str(item.get("job_id")) == str(job_id):
                    matched = True
                    break
            if matched:
                run_id = store.get_latest_done_run_id()
                if run_id:
                    store.update_pipeline_job_jd(run_id, str(job_id), jd)
                    persisted = True

                # 单条 AI 精筛：有画像 + AI 已配置 → 判定后回写
                profile_summary = str(result.get("profile_summary", "")).strip()
                profile_facts = result.get("profile_facts")
                settings = store.get_ai_settings()
                cred_ref = store.get_credential_ref()
                api_key = ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
                endpoint_url = settings.get("endpoint_url", "")
                if profile_summary and api_key and endpoint_url:
                    from webui.ai import match_jds
                    job_for_ai = {"job_id": job_id, "jd": jd, "source_url": source_url}
                    # 把原始岗位信息也带上（标题、薪资等供 AI 参考）
                    for item in jobs:
                        if isinstance(item, dict) and str(item.get("job_id")) == str(job_id):
                            job_for_ai.update({k: v for k, v in item.items()
                                              if k in ("title", "salary", "company", "location")})
                            break
                    try:
                        res = match_jds(
                            [job_for_ai], profile_summary, endpoint_url, api_key,
                            model=settings.get("model", ""),
                            criteria=(payload.get("script_params") or {}).get("screening"),
                            profile_facts=profile_facts,
                        )
                    except ai_service.AISecurityError as ai_exc:
                        # JD 已可靠落库；外部 AI 阻断保留具体原因，但不能吞掉
                        # 后续的本地 verdict 持久化异常。
                        verdict_info = {
                            "verdict_reason": ai_service.user_facing_error(
                                ai_exc.error_code
                            )
                        }
                    else:
                        v = (res.get("verdicts") or {}).get(job_id)
                        if v:
                            verdict_info = {
                                "verdict": v.get("verdict"),
                                "verdict_reason": v.get("reason"),
                                "caveats": v.get("caveats", []),
                                "flags": v.get("flags", []),
                            }
                            if run_id:
                                store.save_screening_verdicts(run_id, {job_id: v})

        return jsonify({"ok": True, "jd": jd, "job_id": job_id,
                        "persisted": persisted, **verdict_info})

    @app.route("/api/pipeline/recrawl", methods=["POST"])
    def pipeline_recrawl():
        """对待确认（uncertain）岗位批量重抓：缺 JD 的补抓 JD，有 JD 的用画像重跑 AI 精筛。

        切片8（FR-022/FR-037）：防并发——同 source_run_id 已有 running 重抓任务时拒绝。
        切片8（FR-023）：job_ids 缺省时从 screening_pending_results 自动读取（全部重抓只处理待确认）。
        复用 fetch_job_details（CDP 通道，内部按 detail_batch_size 分批 + 冷却）与
        match_jds（按 match_batch_size 分批）。进度走与 AI 筛选相同的轮询机制
        （前端 pollTask + TaskProgress）。判定与 JD 原地回写 screening_results，
        返回 updates 映射供前端就地合并，保留当前结果 tab。
        """
        raw = request.get_json(silent=True) or {}
        job_ids = raw.get("job_ids")
        profile_summary = str(raw.get("profile_summary") or "")
        profile_facts = raw.get("profile_facts") or None
        source_run_id = str(
            raw.get("source_run_id") or store.get_latest_done_run_id() or ""
        ).strip()
        if not source_run_id:
            return jsonify({"ok": False, "error": "missing_source_run_id"}), 409
        pending_rows = store.list_pending_results(source_run_id)
        pending_ids = {str(item.get("job_id") or "") for item in pending_rows}
        # FR-023：job_ids 缺省或 "auto" → 从 screening_pending_results 读
        if not job_ids or job_ids == "auto":
            job_ids = sorted(pending_ids)
            if not job_ids:
                return jsonify({"ok": False, "error": "没有待确认岗位可重抓"}), 400
        if not isinstance(job_ids, list) or not job_ids:
            return jsonify({"ok": False, "error": "缺少 job_ids"}), 400
        requested_ids = {str(job_id) for job_id in job_ids}
        non_pending = sorted(requested_ids - pending_ids)
        if non_pending:
            return jsonify({
                "ok": False,
                "error": "non_pending_job_ids",
                "message": "只能重抓当前待确认岗位",
                "job_ids": non_pending,
            }), 409
        job_ids = sorted(requested_ids)
        # T406: 从父 run 读取冻结平台身份和浏览器身份
        parent_identity = None
        parent_run = None
        try:
            parent_identity = store.get_run_checkpoint_identity(source_run_id)
            parent_run = store.get_screening_run(source_run_id)
        except _OPERATIONAL_ERRORS:
            pass
        parent_platform = (parent_identity or {}).get("platform") or "boss"
        parent_task_input_digest = (parent_identity or {}).get("task_input_digest")
        parent_params = (parent_run or {}).get("execution_params") or {}
        parent_browser_account = str(parent_params.get("browser_account") or "") or None
        parent_cdp_port = parent_params.get("cdp_port")
        parent_profile_key = parent_params.get("profile_key")
        # AI screen run 不含 cdp_port/profile_key → 从祖父 scrape run 读
        gp_task_id = str(parent_params.get("scrape_task_id") or "")
        if (not parent_cdp_port or not parent_profile_key) and gp_task_id:
            try:
                grandparent = store.get_screening_run(gp_task_id)
                gp_params = (grandparent or {}).get("execution_params") or {}
                parent_cdp_port = parent_cdp_port or gp_params.get("cdp_port")
                parent_profile_key = parent_profile_key or gp_params.get("profile_key")
            except _OPERATIONAL_ERRORS:
                pass
        task_id = f"recrawl-{uuid.uuid4().hex[:12]}"
        claimed_task, existing_task_id = _claim_recrawl_start(
            task_id, source_run_id
        )
        if claimed_task is None:
            return jsonify({
                "ok": False,
                "error": "已有重抓任务在运行，请等待完成或取消后再试",
                "existing_task_id": existing_task_id,
            }), 409
        claimed_task["browser_account"] = parent_browser_account or _account_for_run()
        claimed_task["platform"] = parent_platform
        claimed_task["cdp_port"] = parent_cdp_port
        claimed_task["profile_key"] = parent_profile_key
        claimed_task["task_input_digest"] = parent_task_input_digest
        _activate_run_browser(parent_run)
        try:
            store.create_screening_run(
                task_id,
                source_count=len(job_ids),
                execution_params={
                    "source_run_id": source_run_id,
                    "job_ids": [str(x) for x in job_ids],
                    "profile_summary": profile_summary,
                    "profile_facts": profile_facts,
                    "browser_account": claimed_task["browser_account"],
                    "platform": parent_platform,
                    "cdp_port": parent_cdp_port,
                    "profile_key": parent_profile_key,
                    "task_input_digest": parent_task_input_digest,
                },
                backend_version=_backend_version,
            )
            store.save_filter_snapshot(
                task_id,
                platform=parent_platform,
                task_input_digest=parent_task_input_digest,
            )
            store.update_screening_run(
                task_id, status="running", current_stage="recrawl_fetch_jd"
            )
        except _OPERATIONAL_ERRORS as exc:
            _release_pipeline_claim(task_id, claimed_task)
            return jsonify({
                "ok": False,
                "error": f"重抓任务持久化失败：{type(exc).__name__}",
            }), 500
        try:
            _pipeline_executor.submit(
                _run_recrawl_task, task_id, [str(x) for x in job_ids],
                profile_summary, source_run_id, None, profile_facts,
            )
        except RuntimeError as exc:
            try:
                store.update_screening_run(
                    task_id, status="failed", error_code="internal_error",
                    error_reason=f"后台任务提交失败：{type(exc).__name__}",
                )
            finally:
                _release_pipeline_claim(task_id, claimed_task)
            return jsonify({
                "ok": False, "error": "recrawl_submit_failed",
            }), 500
        return jsonify({"ok": True, "task_id": task_id, "source_run_id": source_run_id}), 202

    @app.route("/api/recrawl/continue/<task_id>", methods=["POST"])
    def continue_recrawl(task_id, _block_checked=False):
        """Resume a paused recrawl in place using its persisted checkpoint.

        SPEC011 T015: 实验租约持有时拒绝继续（FR-035）。
        """
        # SPEC011 T015/FR-035: 实验租约门禁
        ok, err_resp = _check_tuning_lease_conflict()
        if not ok:
            return err_resp
        run = store.get_screening_run(task_id)
        if run is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        stage = str(run.get("current_stage") or "")
        if run.get("status") != "paused" or not stage.startswith("recrawl_"):
            return jsonify({
                "ok": False, "error": "not_paused_recrawl",
                "status": run.get("status"), "stage": stage,
            }), 409
        _activate_run_browser(run)
        if not _block_checked:
            passed, code, reason = _check_resume_block(run)
            if not passed:
                return jsonify({
                    "ok": False, "error": "block_not_resolved",
                    "error_code": code, "error_reason": reason,
                    "status": "paused",
                }), 409
        with _pipeline_lock:
            existing = _pipeline_tasks.get(task_id)
            if existing is not None and existing.get("status") in ("queued", "running"):
                return jsonify({"ok": False, "error": "already_running"}), 409

        params = run.get("execution_params") or {}
        source_run_id = str(params.get("source_run_id") or "")
        job_ids = [str(job_id) for job_id in (params.get("job_ids") or [])]
        profile_summary = str(params.get("profile_summary") or "")
        profile_facts = params.get("profile_facts") or None
        checkpoint_stage = "recrawl_ai" if stage == "recrawl_ai" else "recrawl_jd"
        completed_job_ids = store.load_checkpoint(task_id, checkpoint_stage)
        if not job_ids:
            return jsonify({"ok": False, "error": "missing_job_ids"}), 409

        claimed_task, previous_task = _claim_pipeline_task_id(
            task_id, "recrawl",
            started_at=_iso_epoch_ms(run.get("started_at")),
        )
        if claimed_task is None:
            return jsonify({"ok": False, "error": "already_running"}), 409
        claimed_task["source_run_id"] = source_run_id
        claimed_task["browser_account"] = _account_for_run(run)
        resume_params = dict(run.get("execution_params") or {})
        if not str(resume_params.get("browser_account") or ""):
            resume_params["browser_account"] = _account_for_run(run)
            store.update_screening_execution_params(task_id, resume_params)
        # T406: 继续时从 run 的 execution_params 恢复冻结平台身份
        claimed_task["platform"] = resume_params.get("platform") or "boss"
        claimed_task["cdp_port"] = resume_params.get("cdp_port")
        claimed_task["profile_key"] = resume_params.get("profile_key")
        claimed_task["task_input_digest"] = resume_params.get("task_input_digest")
        try:
            if not _write_run_unless_finished(task_id, status="running"):
                _release_pipeline_claim(task_id, claimed_task, previous_task)
                return jsonify({
                    "ok": False, "error": "user_finished",
                    "message": "任务已结束保存，不能继续",
                    "status": "completed_with_pending",
                }), 409
            store.append_task_event(task_id, "resume", {
                "stage": stage, "completed": len(completed_job_ids),
            })
            _pipeline_executor.submit(
                _run_recrawl_task, task_id, job_ids, profile_summary,
                source_run_id, completed_job_ids, profile_facts,
            )
        except _OPERATIONAL_ERRORS as exc:
            try:
                current = store.get_screening_run(task_id)
                if current is not None and current.get("status") == "running":
                    _write_run_unless_finished(
                        task_id, status="paused",
                        error_code=str(run.get("error_code") or "internal_error"),
                        error_reason=(
                            str(run.get("error_reason") or "")
                            or f"继续任务提交失败：{type(exc).__name__}"
                        ),
                    )
            finally:
                _release_pipeline_claim(
                    task_id, claimed_task, previous_task
                )
            return jsonify({
                "ok": False, "error": "resume_submit_failed",
            }), 500
        return jsonify({
            "ok": True,
            "task_id": task_id,
            "source_run_id": source_run_id,
            "completed_job_ids": sorted(completed_job_ids),
            "stage": stage,
        })

    def _run_recrawl_task(task_id, job_ids, profile_summary, source_run_id="",
                          completed_job_ids=None, profile_facts=None):
        """批量重抓后台任务：补 JD + 重判，进度与结果通过 _pipeline_tasks 暴露。

        切片8：``source_run_id`` 用于持久化（recrawl task_id 不是 screening_runs 行）。
        暂停时写入 store（用 task_id 作为 run_id 占位），保存 checkpoint，
        服务重启后用户可点继续从 checkpoint 恢复。
        """
        from webui.ai import match_jds
        from webui.pipeline_exec import (
            close_debug_chrome,
            ensure_chrome_ready,
            failed_code_label,
            fetch_job_details,
        )

        # 画像兜底：前端刷新后传空，从落盘结果里恢复（跟本轮抓取绑定，下轮覆盖）
        if not profile_summary.strip():
            payload = store.load_latest_pipeline_result(source_run_id or None)
            if payload:
                profile_summary = str(
                    (payload.get("result") or {}).get("profile_summary", "")
                )
                profile_facts = (
                    (payload.get("result") or {}).get("profile_facts") or None
                )

        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)
            if task is None:
                task = {
                    "kind": "recrawl", "status": "queued", "progress": {},
                    "logs": [], "result": None, "error": "",
                    "started_at": int(time.time() * 1000), "finished_at": None,
                    "stop_event": threading.Event(),
                }
                _pipeline_tasks[task_id] = task
            if task.get("status") == "cancelled":
                return
            if _is_user_finished(task_id):
                _release_worker_resume_claims(task)
                return
            task["status"] = "running"
            stop_event = task.get("stop_event")

        _activate_task_browser(task_id)

        # T403: 从 task dict 读取冻结平台/浏览器身份，fallback 到 run execution_params
        with _pipeline_lock:
            _t_ref = _pipeline_tasks.get(task_id, {})
            frozen_platform = _t_ref.get("platform")
            frozen_cdp_port = _t_ref.get("cdp_port")
            frozen_profile_key = _t_ref.get("profile_key")
            frozen_browser_account = _t_ref.get("browser_account")
        if not frozen_platform:
            try:
                _run_ref = store.get_screening_run(task_id)
                _params_ref = (_run_ref or {}).get("execution_params") or {}
                frozen_platform = _params_ref.get("platform") or "boss"
                frozen_cdp_port = frozen_cdp_port or _params_ref.get("cdp_port")
                frozen_profile_key = frozen_profile_key or _params_ref.get("profile_key")
                frozen_browser_account = frozen_browser_account or _params_ref.get("browser_account")
            except _OPERATIONAL_ERRORS:
                frozen_platform = frozen_platform or "boss"

        last_event_stage = None

        def emit(**kw):
            nonlocal last_event_stage
            stage = str(kw.get("stage", ""))
            current = int(kw.get("current") or 0)
            total = int(kw.get("total") or 0)
            kw["overall_percent"] = _recrawl_overall_percent(stage, current, total)
            if not kw.get("message"):
                kw["message"] = _SCREEN_STAGE_MESSAGES.get(stage, "")
            event_stage = _EVENT_STAGE_NAMES.get(stage)
            stage_events = []
            if stage == "done" and last_event_stage:
                stage_events.append(("stage_complete", {"stage": last_event_stage}))
                last_event_stage = None
            elif event_stage and event_stage != last_event_stage:
                if last_event_stage:
                    stage_events.append(("stage_complete", {"stage": last_event_stage}))
                stage_events.append(("stage_start", {"stage": event_stage}))
                last_event_stage = event_stage
            if stage_events:
                store.append_task_events(task_id, stage_events)
            with _pipeline_lock:
                t = _pipeline_tasks.get(task_id)
                if t is None:
                    return
                t["progress"] = kw

        def _stop_requested():
            return stop_event is not None and stop_event.is_set()

        def _pause_recrawl_source_unavailable(reason):
            """Persist a CDP-wide recrawl hard stop before exposing paused state."""
            code = "source_cdp_unavailable"
            completed = set(completed_job_ids or set()) | set(fetched_jd)
            failed_jobs = [
                {
                    "job_id": str(job.get("job_id") or ""),
                    "jd": "",
                    "jd_failed_code": code,
                    "jd_failed_reason": reason,
                }
                for job in no_jd
                if str(job.get("job_id") or "") not in completed
            ]
            _persist_jd_job_failures(
                task_id,
                failed_jobs,
                stage="recrawl_fetch_jd",
                source_run_id=source_run_id,
                platform=frozen_platform,
            )
            store.save_checkpoint(task_id, "recrawl_jd", sorted(completed))
            _write_run_unless_finished(
                task_id,
                status="paused",
                error_code=code,
                error_reason=reason,
                current_stage="recrawl_fetch_jd",
                processed_count=len(completed),
            )
            store.append_task_event(
                task_id,
                "pause",
                {
                    "stage": "recrawl_fetch_jd",
                    "code": code,
                    "processed": len(completed),
                    "total": len(no_jd),
                },
            )
            _record_pause_failure(
                task_id, "recrawl_fetch_jd", code, reason,
                processed=len(completed), total=len(no_jd),
            )
            publish_recrawl_updates()
            with _pipeline_lock:
                current = _pipeline_tasks.get(task_id)
                if current is not None:
                    current["status"] = "paused"
                    current["error"] = reason
            _release_worker_resume_claims(_pipeline_tasks.get(task_id))

        updates: dict = {}

        def publish_recrawl_updates():
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    task["result"] = {"updates": dict(updates)}
        try:
            payload = store.load_latest_pipeline_result(source_run_id or None)
            run_id = source_run_id or store.get_latest_done_run_id()
            jobs = (payload or {}).get("result", {}).get("jobs", []) if payload else []
            by_id: dict[str, dict] = {}
            for j in jobs:
                if not isinstance(j, dict):
                    continue
                pid = str(j.get("platform_job_id") or j.get("job_id") or "").strip()
                if pid:
                    by_id.setdefault(pid, j)
            targets = [by_id[jid] for jid in job_ids if jid in by_id]
            total = len(targets)
            emit(stage="fetch_jd", current=0, total=total,
                 message=f"准备重抓 {total} 个待确认岗位…")
            if not targets:
                emit(stage="done", current=0, total=0, message="没有可重抓的岗位")
                with _pipeline_lock:
                    t = _pipeline_tasks.get(task_id)
                    if t is not None:
                        t["status"] = "done"
                        t["result"] = {"updates": {}}
                _schedule_pipeline_task_cleanup(task_id)
                _release_worker_resume_claims(_pipeline_tasks.get(task_id))
                return

            settings = store.get_ai_settings()
            cred_ref = store.get_credential_ref()
            api_key = ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
            endpoint = settings.get("endpoint_url", "")
            model = settings.get("model", "")
            has_ai = bool(api_key and endpoint)

            # 1) 缺 JD 的先补抓（复用详情 CDP 通道，内部已按 detail_batch_size 分批 + 冷却）
            no_jd = []
            for j in targets:
                if str(j.get("jd", "")).strip():
                    continue
                url = normalize_job_link(
                    j.get("source_url") or j.get("job_link") or j.get("canonical_url") or ""
                )
                if url:
                    no_jd.append({
                        "platform_job_id": str(j.get("platform_job_id") or j.get("job_id") or ""),
                        "job_id": str(j.get("platform_job_id") or j.get("job_id") or ""),
                        "source_url": url, "job_link": url,
                    })
            fetched_jd: dict = {}
            detail_jobs: list = []
            if no_jd:
                chrome_ok, chrome_err = ensure_chrome_ready(
                    frozen_cdp_port, minimize_after_launch=True,
                )
                if chrome_ok:
                    source = _make_cdp_source(
                        platform=frozen_platform,
                        browser_account=frozen_browser_account,
                        cdp_port=frozen_cdp_port,
                        profile_key=frozen_profile_key,
                        run_id=task_id,
                    )
                    if source is not None:
                        def _jd_progress(done, tot):
                            emit(stage="fetch_jd", current=min(done, total), total=total,
                                 message=f"抓取 JD {min(done, total)}/{total}")
                        detail = fetch_job_details(
                            no_jd, source, artifact_dir=app.config["RESULT_DIR"],
                            stop_event=stop_event, progress=_jd_progress,
                            completed_job_ids=completed_job_ids,
                        )
                        detail_jobs = detail.get("jobs", [])
                        for j in detail_jobs:
                            jid = str(j.get("job_id", ""))
                            jd = str(j.get("jd", "")).strip()
                            if jid and jd:
                                fetched_jd[jid] = jd
                        completed_jd_ids = set(completed_job_ids or set()) | set(fetched_jd)
                        store.save_recrawl_jd_and_checkpoint(
                            run_id, task_id, fetched_jd, completed_jd_ids
                        )
                        for jid, jd in fetched_jd.items():
                            updates.setdefault(jid, {})["jd"] = jd
                        publish_recrawl_updates()
                        if detail.get("hard_stop"):
                            # 暂停，不关浏览器（用户需要它处理验证码/登录）
                            _hs_code = detail.get("hard_stop_code") or "source_blocked"
                            _hs_label = failed_code_label(_hs_code, frozen_platform)
                            _hs_hint = next((
                                str(job.get("jd_failed_reason") or "").strip()
                                for job in detail_jobs or []
                                if job.get("jd_failed_reason")
                            ), "")
                            _hs_reason = _hs_hint if _hs_hint and _hs_hint != _hs_label else _hs_label
                            _persist_jd_job_failures(
                                task_id,
                                detail_jobs,
                                stage="recrawl_fetch_jd",
                                source_run_id=source_run_id,
                                platform=frozen_platform,
                            )
                            # 切片8：持久化暂停状态 + checkpoint（已抓 JD 的 job_id）
                            _write_run_unless_finished(
                                task_id, status="paused", error_code=_hs_code,
                                current_stage="recrawl_fetch_jd",
                                processed_count=len(completed_jd_ids),
                                error_reason=_hs_reason)
                            store.append_task_event(
                                task_id, "pause",
                                {"stage": "recrawl_fetch_jd", "code": _hs_code,
                                 "fetched": len(fetched_jd), "total": len(no_jd)})
                            _record_pause_failure(
                                task_id, "recrawl_fetch_jd", _hs_code, _hs_reason,
                                processed=len(completed_jd_ids), total=len(no_jd),
                            )
                            with _pipeline_lock:
                                t = _pipeline_tasks.get(task_id)
                                if t is not None:
                                    t["status"] = "paused"
                                    t["error"] = (f"重抓 JD 时{_hs_reason}，已抓部分已保存；"
                                                  "请在自动化浏览器中处理，完成后点「继续」")
                            _release_worker_resume_claims(_pipeline_tasks.get(task_id))
                            return
                        if detail.get("stopped"):
                            close_debug_chrome(frozen_cdp_port)
                            with _pipeline_lock:
                                t = _pipeline_tasks.get(task_id)
                                if t is not None:
                                    t["status"] = "cancelled"
                                    t["error"] = "用户已停止重抓"
                            _schedule_pipeline_task_cleanup(task_id)
                            _release_worker_resume_claims(_pipeline_tasks.get(task_id))
                            return
                        close_debug_chrome(frozen_cdp_port)
                    else:
                        reason = "CDP 抓取源不可用，请确认调试浏览器后继续"
                        emit(stage="fetch_jd", current=0, total=total, message=reason)
                        _pause_recrawl_source_unavailable(reason)
                        return
                else:
                    reason = f"调试浏览器未就绪（{chrome_err}），请处理后继续"
                    emit(stage="fetch_jd", current=0, total=total, message=reason)
                    _pause_recrawl_source_unavailable(reason)
                    return

            # 补抓仍失败的岗位：把具体原因回写前端（验证码/限流等）
            for j in detail_jobs:
                jid = str(j.get("job_id", ""))
                code = j.get("jd_failed_code", "")
                if jid and code and jid not in fetched_jd:
                    label = failed_code_label(code, frozen_platform)
                    detail_reason = str(j.get("jd_failed_reason") or "").strip()
                    reason = (
                        f"未抓到 JD（{detail_reason}），无法精筛"
                        if detail_reason else
                        (f"未抓到 JD（{label}），无法精筛" if label else
                         "未抓到 JD，无法精筛")
                    )
                    updates.setdefault(jid, {})["verdict_reason"] = reason
            publish_recrawl_updates()

            # 2) 有 JD 且有画像的，重跑 AI 精筛
            if not has_ai:
                reason = "AI 未配置，已保留补抓结果；配置 AI 后可继续判定"
                emit(stage="screen_b", current=0, total=total, message=reason)
                _write_run_unless_finished(
                    task_id, status="paused", error_code="ai_key_invalid",
                    current_stage="recrawl_ai", processed_count=0,
                    error_reason=reason,
                )
                store.save_checkpoint(task_id, "recrawl_ai", [])
                store.append_task_event(task_id, "pause", {
                    "stage": "recrawl_ai", "code": "ai_key_invalid",
                    "processed": 0, "total": total,
                })
                _record_pause_failure(
                    task_id, "recrawl_ai", "ai_key_invalid", reason,
                    processed=0, total=total,
                )
                with _pipeline_lock:
                    t = _pipeline_tasks.get(task_id)
                    if t is not None:
                        t["status"] = "paused"
                        t["error"] = reason
                _release_worker_resume_claims(_pipeline_tasks.get(task_id))
                return
            elif not profile_summary.strip():
                emit(stage="screen_b", current=total, total=total,
                     message="未填写求职画像，跳过 AI 重判")
            else:
                to_judge = []
                for j in targets:
                    jid = str(j.get("platform_job_id") or j.get("job_id") or "")
                    jd = str(j.get("jd", "")).strip() or fetched_jd.get(jid, "")
                    if jd:
                        jj = dict(j)
                        jj["job_id"] = jid
                        to_judge.append(jj)
                if to_judge:
                    _adv = _load_legacy_advanced_settings()
                    match_batch = int(_adv.get("match_batch_size") or 4)
                    recrawl_completed_ids = set(completed_job_ids or set())
                    verdicts: dict = store.load_screening_verdicts(task_id)
                    to_judge = [
                        job for job in to_judge
                        if str(job.get("job_id", "")) not in recrawl_completed_ids
                    ]
                    _recrawl_ai_pause = False
                    # 三通道：从源 run 快照取筛选条件与画像事实（老轮无画像事实则退化）
                    recrawl_criteria = {}
                    try:
                        _src_run = store.get_screening_run(run_id)
                        _frozen = (_src_run or {}).get("frozen_filters") or {}
                        if isinstance(_frozen, dict):
                            recrawl_criteria = {
                                k: v for k, v in _frozen.items()
                                if k != "profile_summary"
                            }
                    except _OPERATIONAL_ERRORS:
                        recrawl_criteria = {}
                    for start in range(0, len(to_judge), match_batch):
                        if _stop_requested():
                            break
                        chunk = to_judge[start:start + match_batch]
                        try:
                            res = match_jds(
                                chunk, profile_summary, endpoint, api_key,
                                model=model, raise_on_systemic=True,
                                criteria=recrawl_criteria,
                                profile_facts=profile_facts,
                            )
                        except ai_service.AISecurityError as _ai_exc:
                            # 切片8：systemic 错误暂停（不批量变 uncertain 后完成）
                            from webui.ai import (
                                AISecurityError,
                                map_ai_error_to_block_code,
                            )
                            if isinstance(_ai_exc, AISecurityError):
                                _block_code = map_ai_error_to_block_code(_ai_exc.error_code)
                                if _block_code:
                                    _write_run_unless_finished(
                                        task_id, status="paused", error_code=_block_code,
                                        current_stage="recrawl_ai",
                                        processed_count=len(recrawl_completed_ids))
                                    store.save_checkpoint(
                                        task_id, "recrawl_ai",
                                        sorted(recrawl_completed_ids),
                                    )
                                    store.append_task_event(
                                        task_id, "pause",
                                        {"stage": "recrawl_ai", "code": _block_code,
                                         "processed": len(recrawl_completed_ids),
                                         "total": len(targets)})
                                    _record_pause_failure(
                                        task_id, "recrawl_ai", _block_code, _block_code,
                                        processed=len(recrawl_completed_ids),
                                        total=len(targets), exception=_ai_exc,
                                    )
                                    with _pipeline_lock:
                                        t = _pipeline_tasks.get(task_id)
                                        if t is not None:
                                            t["status"] = "paused"
                                            t["error"] = (
                                                f"重抓 AI 重判被阻断（{_block_code}）："
                                                f"已判 {len(recrawl_completed_ids)}/{len(targets)} 条。"
                                                "处理完成后点「继续」"
                                            )
                                    publish_recrawl_updates()
                                    _recrawl_ai_pause = True
                                    break
                            raise
                        batch_verdicts = res.get("verdicts", {})
                        verdicts.update(batch_verdicts)
                        recrawl_completed_ids.update(batch_verdicts)
                        store.save_verdict_and_checkpoint_atomic(
                            task_id, "recrawl_ai", batch_verdicts,
                            sorted(recrawl_completed_ids),
                        )
                        emit(stage="screen_b", current=len(recrawl_completed_ids), total=total,
                             message=f"AI 重判 {len(recrawl_completed_ids)}/{total}")
                    if _recrawl_ai_pause:
                        _release_worker_resume_claims(_pipeline_tasks.get(task_id))
                        return
                    if run_id:
                        store.save_screening_verdicts(run_id, verdicts)
                    for jid, v in verdicts.items():
                        u = updates.setdefault(jid, {})
                        u["verdict"] = v.get("verdict")
                        u["verdict_reason"] = v.get("reason")
                        u["caveats"] = v.get("caveats", [])
                    # 切片8：补救成功后从 screening_pending_results 移除（FR-023）
                    if source_run_id:
                        for jid in verdicts:
                            v = verdicts[jid].get("verdict")
                            if v in ("match", "not_match"):
                                store.delete_pending_result(source_run_id, jid)
                    publish_recrawl_updates()
                    if run_id:
                        store.recount_pipeline_result(run_id)

            emit(stage="done", current=total, total=total, message="重抓完成")
            store.append_task_events(task_id, [
                (
                    "job_success" if update.get("verdict") in ("match", "not_match")
                    or bool(update.get("jd")) else "job_fail",
                    {
                        "stage": "recrawl", "job_id": str(job_id),
                        "verdict": update.get("verdict"),
                        "reason": update.get("verdict_reason", ""),
                    },
                )
                for job_id, update in updates.items()
            ])
            _write_run_unless_finished(
                task_id,
                status="cancelled" if _stop_requested() else "succeeded",
                current_stage="done",
            )
            with _pipeline_lock:
                t = _pipeline_tasks.get(task_id)
                if t is not None:
                    t["status"] = "cancelled" if _stop_requested() else "done"
                    t["result"] = {"updates": updates}
            _schedule_pipeline_task_cleanup(task_id)
            _release_worker_resume_claims(_pipeline_tasks.get(task_id))
        except Exception as exc:
            error_message = f"重抓异常：{type(exc).__name__}"
            if not _is_user_finished(task_id):
                record_failure(
                    store, task_id, stage="recrawl",
                    error_code="internal_error", reason=error_message,
                    correlation_id=task_id, diagnostics={},
                    exception=exc, include_traceback=True,
                )
            persistence_error = None
            try:
                run = store.get_screening_run(task_id)
                if run and run.get("status") in ("queued", "running", "paused"):
                    _write_run_unless_finished(
                        task_id, status="failed", error_code="internal_error",
                        error_reason=error_message,
                    )
            except _OPERATIONAL_ERRORS as persist_exc:
                persistence_error = type(persist_exc).__name__
            with _pipeline_lock:
                t = _pipeline_tasks.get(task_id)
                if t is not None:
                    if _is_user_finished(task_id):
                        t["status"] = "cancelled"
                        t["error"] = _MSG_USER_FINISHED
                    else:
                        t["status"] = "failed"
                        t["error"] = (
                            error_message if persistence_error is None
                            else f"{error_message}；状态保存失败：{persistence_error}"
                        )
            _schedule_pipeline_task_cleanup(task_id)
            _release_worker_resume_claims(_pipeline_tasks.get(task_id))

    # ===================================================================
    # 010 healthy-pipeline-recovery: 统一接口层（FR-005/FR-020/FR-022/
    # FR-024/FR-037/FR-039/FR-041）
    # ===================================================================

    import hashlib as _hashlib_mod

    # FR-039：后端版本标识（启动时计算，继续任务时校验）
    try:
        _backend_version = "011-ui-fixes"
        _backend_files = sorted(
            [*Path(__file__).resolve().parent.glob("*.py"), SCRAPER.resolve()],
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

    @app.route("/api/version", methods=["GET"])
    def api_version():
        """FR-039：返回后端版本标识。前端用于校验是否需要刷新。"""
        return jsonify({
            "backend_version": _backend_version,
            "build_hash": _build_hash,
            "build_time": _build_time,
        })

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

    def _update_env_payload(info_dict):
        """统一附加运行时环境信息：exe 模式才可应用内安装。"""
        install_target = _updater_mod.current_install_target()
        info_dict["runtime_mode"] = _runtime_mode
        info_dict["installable"] = bool(
            _runtime_mode == "exe" and install_target is not None
        )
        return info_dict

    @app.route("/api/update-check", methods=["GET"])
    def update_check():
        info = _updater_mod.check_for_update(
            _product_version,
            state_dir=None if app.config.get("TESTING") else _updater_mod.DEFAULT_STATE_DIR,
        )
        payload = _update_env_payload(info.to_dict())
        # 跨重启后内存状态是 idle：先尝试把磁盘上已下载并通过校验的
        # 完整安装包恢复为 ready，弹窗打开时可直接重启更新。
        if payload.get("installable") and info.has_update:
            app.config["UPDATER"].recover_ready(info)
        return jsonify(payload)

    @app.route("/api/update-download", methods=["POST"])
    def update_download():
        """启动后台下载；仅接受带 sha256 资产的更新（无哈希拒绝）。"""
        info = _updater_mod.check_for_update(
            _product_version,
            state_dir=None if app.config.get("TESTING") else _updater_mod.DEFAULT_STATE_DIR,
        )
        if not info.has_update:
            return jsonify({"ok": False, "error_code": "no_update",
                            "user_message": "当前已是最新版本"}), 409
        if not info.asset_url:
            return jsonify({"ok": False, "error_code": info.reason or "no_asset",
                            "user_message": "该版本未提供当前平台的安装包，请到 Release 页手动下载"}), 422
        if not info.sha256_url:
            return jsonify({"ok": False, "error_code": "no_sha256",
                            "user_message": "该版本未提供校验文件，为安全起见请到 Release 页手动下载"}), 422
        updater = app.config["UPDATER"]
        if updater.recover_ready(info):
            return jsonify({"ok": True, "already": True, **updater.status()})
        started = updater.start(info)
        if not started:
            status = app.config["UPDATER"].status()
            if status["status"] in ("downloading", "verifying", "ready"):
                return jsonify({"ok": True, "already": True, **status})
            return jsonify({"ok": False, "error_code": "download_start_failed",
                            "user_message": "下载启动失败，请稍后重试；若仍失败请到 Release 页手动下载"}), 500
        return jsonify({"ok": True, **app.config["UPDATER"].status()})

    @app.route("/api/update-status", methods=["GET"])
    def update_status():
        app.config["UPDATER"].recover_ready()
        return jsonify({"ok": True, **app.config["UPDATER"].status()})

    @app.route("/api/update-restart", methods=["POST"])
    def update_restart():
        """生成并 detached 启动替换脚本，随后由前端退出应用。

        脚本等主进程退出后替换文件并拉起新版本；未就绪/非 exe 模式/
        无安装目标一律拒绝（源码模式没有可替换产物）。
        """
        import subprocess
        import sys as _sys

        install_target = _updater_mod.current_install_target()
        if _runtime_mode != "exe" or install_target is None:
            return jsonify({"ok": False, "error_code": "not_installable",
                            "user_message": "源码模式不支持应用内安装，请手动更新"}), 409
        status = app.config["UPDATER"].status()
        if status["status"] != "ready" or not status["path"]:
            return jsonify({"ok": False, "error_code": "download_not_ready",
                            "user_message": "更新包尚未就绪"}), 409
        installer_path = Path(status["path"])
        if not installer_path.exists():
            return jsonify({"ok": False, "error_code": "installer_missing",
                            "user_message": "更新包丢失，请重新下载"}), 409
        runner, script = _updater_mod.build_updater_script(
            installer_path=installer_path,
            install_target=install_target,
            pid=os.getpid(),
            script_dir=_updater_mod.DEFAULT_STATE_DIR,
        )
        try:
            if _sys.platform == "win32":
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                     "-ExecutionPolicy", "Bypass", "-File", str(script)],
                    cwd=str(_updater_mod.DEFAULT_STATE_DIR),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    # CREATE_NO_WINDOW：绝不弹 cmd/powershell 黑窗
                    creationflags=subprocess.CREATE_NO_WINDOW
                    | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                subprocess.Popen(
                    [runner, str(script)],
                    cwd=str(_updater_mod.DEFAULT_STATE_DIR),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except OSError as exc:
            app.logger.exception("更新重启脚本启动失败：%s", exc)
            return jsonify({"ok": False, "error_code": "updater_launch_failed",
                            "user_message": "更新脚本启动失败，请关闭软件后手动下载更新"}), 500
        return jsonify({"ok": True, "user_message": "即将重启完成更新"})

    # 主题偏好（明暗）：存 ~/.career-scout/theme.json。
    # 桌面版每次启动使用随机端口，localStorage 按 origin（含端口）隔离，
    # 前端本地持久化必然丢失；改由后端文件持久化，源码模式与 EXE 统一。
    @app.route("/api/theme", methods=["GET"])
    def api_theme_get():
        mode = "light"
        try:
            data = json.loads(_theme_path().read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("mode") in ("light", "dark"):
                mode = data["mode"]
        except (OSError, ValueError):
            pass
        return jsonify({"ok": True, "mode": mode})

    @app.route("/api/theme", methods=["PUT"])
    def api_theme_put():
        body = request.get_json(silent=True) or {}
        mode = str(body.get("mode") or "")
        if mode not in ("light", "dark"):
            return jsonify({"ok": False, "error": "mode 必须为 light 或 dark"}), 400
        try:
            path = _theme_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"mode": mode}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            return jsonify({"ok": False, "error": "theme 写入失败"}), 500
        return jsonify({"ok": True, "mode": mode})

    @app.route("/api/runs/<run_id>/diagnostics")
    def run_diagnostics(run_id: str):
        """Return a safe diagnostic summary for a pipeline run."""
        try:
            run = store.get_screening_run(run_id)
        except _OPERATIONAL_ERRORS:
            run = None
        if run is None:
            return jsonify({
                "ok": False, "error_code": "not_found",
                "user_message": _MSG_TASK_NOT_FOUND,
            }), 404
        try:
            events = store.list_task_events(run_id)
        except _OPERATIONAL_ERRORS:
            events = []
        params = run.get("execution_params") or {}
        correlation_id = str(params.get("correlation_id") or "")
        correlation_id = correlation_id or run_id
        from webui.pipeline_exec import taxonomy_reason
        code = str(run.get("error_code") or "")
        next_action = taxonomy_reason(
            code, str(run.get("platform") or ""), fallback=""
        ) if code else ""
        payload = build_diagnostic_payload(
            run_id=run_id, run=run, events=events,
            correlation_id=correlation_id, next_action=next_action,
        )
        return jsonify({"ok": True, **payload})

    @app.route("/api/task-state/<run_id>", methods=["GET"])
    def api_task_state(run_id: str):
        """FR-037：统一任务状态接口。

        返回完整状态画面：status/stage/progress/success_count/fail_count/
        unstarted_count/total/pause_info(含 error_code/error_reason)。
        前端 3 个 snapshot 统一从此接口拉取。
        """
        from webui.pipeline_exec import (
            _scrape_overall_percent,
            _scrape_page_overall_percent,
            failed_code_label,
        )

        with _pipeline_lock:
            task = _pipeline_tasks.get(run_id)
            if task is not None:
                # T405: 内存 task 与 DB run 身份一致性校验
                _, conflict = _check_run_identity_conflict(run_id, task)
                if conflict is not None:
                    return conflict
                if task["status"] in ("done", "failed", "cancelled") and task.get(
                    "finished_at"
                ) is None:
                    task["finished_at"] = int(time.time() * 1000)
                live = {
                    "kind": task.get("kind", ""),
                    "status": task.get("status", "running"),
                    "progress": dict(task.get("progress") or {}),
                    "logs": list((task.get("logs") or [])[-LOG_TAIL_LINES:]),
                    "error": task.get("error", ""),
                    "result": task.get("result"),
                    "started_at": task.get("started_at"),
                    "finished_at": task.get("finished_at"),
                    "auto_screen": task.get("auto_screen"),
                }
            else:
                live = None
        run = store.get_screening_run(run_id)
        if run is None and live is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        # T405: 按 combo 最新 attempt 汇总 source outcomes
        source_summary, source_outcomes = _build_source_summary_and_outcomes(run_id)
        live_progress = (live or {}).get("progress") or {}
        source = int((run or {}).get("source_count") or live_progress.get("total") or 0)
        # 只有 AI 筛选/补抓任务的 live current 是“条数”语义，可作计数兜底；
        # scrape 任务的 searching/waiting/combo_* 阶段 current 是组合序号，
        # 混入成功数会显示「已完成 3 / 127 岗位」这类错误计数。
        live_kind = str((live or {}).get("kind") or "")
        count_live = live_kind in ("ai_screen", "recrawl")
        live_current = int(live_progress.get("current") or 0)
        if not count_live:
            live_current = 0
        processed_db = int((run or {}).get("processed_count") or 0)
        # DB processed_count 是批次粒度（智联详情每批 15 条才落库一次），
        # 为空时用实时 live current 兜底，保证进度按条前进且跨阶段不回退。
        processed = processed_db if processed_db > 0 else live_current
        match = int((run or {}).get("match_count") or 0)
        mismatch = int((run or {}).get("mismatch_count") or 0)
        pending = int((run or {}).get("pending_count") or 0)
        dropped = int((run or {}).get("total_dropped") or 0)
        kept = int((run or {}).get("total_kept") or 0)
        if kept <= 0:
            kept = max(0, source - dropped)
        exec_params = (run or {}).get("execution_params") or {}
        scraped_count_source = str(exec_params.get("scrape_task_id") or "") or run_id
        try:
            scraped_count = store.count_scrape_run_jobs(scraped_count_source)
        except _OPERATIONAL_ERRORS:
            scraped_count = 0
        error_code = (run or {}).get("error_code")
        error_reason = (run or {}).get("error_reason")
        stage = (
            (run or {}).get("current_stage")
            or live_progress.get("stage")
            or "unknown"
        )
        progress_kind = live_kind or _pipeline_kind_for_stage(stage)
        # processed_count 只记录已成功完成的当前阶段工作单元；pending
        # 是已失败并进入待确认的独立工作单元，两者不能互相扣减。
        # JD 详情/精筛阶段只处理粗筛保留的岗位；原始列表里的 dropped
        # 已经作为独立结果展示，不能继续混进当前阶段的成功/失败/未开始。
        jd_stage = stage in ("jd_detail", "fetch_jd", "ai_fine", "screen_b", "done")
        stage_total = kept if jd_stage and kept > 0 else source
        # processed_count 只记录已成功完成的当前阶段工作单元；pending
        # 是已失败并进入待确认的独立工作单元，两者不能互相扣减。
        fail_count = pending
        # success_count 必须单调且实时：live_current（条数语义）与 DB 计数
        # 取最大值，保证智联详情逐条推进、跨阶段切换不回退。
        # 精筛阶段（ai_fine/screen_b）的 match+mismatch 仍是粗筛/详情阶段的
        # 累计值，混入会把成功数钉死在上一阶段完成数（假 30/30 + 100% 干等）；
        # 该阶段成功数只算精筛自己的进度：processed 在精筛开始时已重置为
        # 已判定数，live_current 是精筛实时推送的 current。
        if stage in ("ai_fine", "screen_b"):
            success_count = max(processed, live_current)
        else:
            success_count = max(match + mismatch, processed, live_current)
        completed_count = min(stage_total, success_count + fail_count)
        unstarted = max(0, stage_total - completed_count)
        if progress_kind == "recrawl":
            overall_percent = _recrawl_overall_percent(
                stage, completed_count, stage_total,
            )
        elif progress_kind == "ai_screen":
            overall_percent = _screen_overall_percent(
                stage, completed_count, stage_total,
            )
        else:
            overall_percent = _scrape_overall_percent(
                stage, completed_count, stage_total,
            )
        progress = dict(live_progress)
        page_rows = []
        try:
            page_rows = store.load_scrape_page_progress(scraped_count_source)
        except _OPERATIONAL_ERRORS:
            page_rows = []
        if page_rows:
            latest_page = page_rows[0]
            progress.setdefault("page", latest_page["completed_pages"])
            progress.setdefault("target_pages", latest_page["target_pages"])
            progress.setdefault("resume_page", latest_page["resume_page"])
            progress.setdefault("has_more", bool(latest_page["has_more"]))
            progress.setdefault("scraped", latest_page["jobs_count"])
            if "overall_percent" not in progress:
                page_ratio = min(
                    1.0, latest_page["completed_pages"] / max(1, latest_page["target_pages"]))
                progress["overall_percent"] = _scrape_page_overall_percent(
                    stage, completed_count, stage_total, page_ratio)
        progress.setdefault("overall_percent", overall_percent)
        progress.setdefault("current", success_count if jd_stage else completed_count)
        progress.setdefault("total", stage_total)
        pause_info = None
        effective_status = _public_task_status(
            str(live.get("status")) if live is not None else str(run["status"]),
            (run or {}).get("interruption_kind"),
        )
        if effective_status == "paused" or (
                error_code and error_code in SYSTEMIC_BLOCK_CODES):
            pause_info = {
                "error_code": error_code,
                "error_reason": error_reason or failed_code_label(
                    error_code, str((run or {}).get("platform") or (live or {}).get("platform") or "")
                ) or error_code or "",
            }
        if effective_status == "interrupted":
            progress.setdefault(
                "message", "任务因服务重启被中断，已保存进度")
        started_at = _iso_epoch_ms((live or {}).get("started_at"))
        if started_at is None:
            started_at = _iso_epoch_ms((run or {}).get("started_at"))
        finished_at = _iso_epoch_ms((live or {}).get("finished_at"))
        if finished_at is None:
            finished_at = _iso_epoch_ms((run or {}).get("finished_at"))
        return jsonify({
            "ok": True,
            "run_id": run_id,
            "kind": (live or {}).get("kind", ""),
            "status": effective_status,
            "db_status": (run or {}).get("status"),
            "stage": stage,
            "progress": progress,
            "logs": (live or {}).get("logs", []),
            "error": (live or {}).get("error") or error_reason or "",
            "success_count": success_count,
            "fail_count": fail_count,
            "unstarted_count": unstarted,
            "pending_count": pending,
            "total": stage_total,
            "source_total": source,
            "processed": processed,
            "scraped_count": scraped_count,
            "match_count": match,
            "mismatch_count": mismatch,
            "dropped_count": dropped,
            "kept_count": kept,
            "pause_info": pause_info,
            "execution_config": (
                (run or {}).get("execution_params") or {}
            ).get("execution_config") or {},
            "backend_version": (run or {}).get("backend_version"),
            "current_version": _backend_version,
            "version_match": (
                not (run or {}).get("backend_version")
                or (run or {}).get("backend_version") == _backend_version
            ),
            "updated_at": (run or {}).get("updated_at"),
            "started_at": started_at,
            "finished_at": finished_at,
            # T405: 平台身份与 source outcomes 汇总
            "platform": (run or {}).get("platform") or (live or {}).get("platform"),
            "task_input_digest": (run or {}).get("task_input_digest"),
            "auto_screen": bool((live or {}).get("auto_screen", exec_params.get("auto_screen"))),
            "source_summary": source_summary,
            "source_outcomes": source_outcomes,
            **(
                {"result": live["result"]}
                if live is not None and live.get("result") is not None else {}
            ),
        })

    @app.route("/api/task/continue/<run_id>", methods=["POST"])
    def api_task_continue(run_id: str):
        """FR-020/FR-022：统一继续接口。

        允许 paused 状态调用；running 状态拒绝（防止重复继续）。
        继续前检查阻断条件是否解除（由各阶段 handler 自行实现）。

        SPEC011 T015: 实验租约持有时拒绝继续（FR-035）。
        """
        # SPEC011 T015/FR-035: 实验租约门禁
        ok, err_resp = _check_tuning_lease_conflict()
        if not ok:
            return err_resp
        run = store.get_screening_run(run_id)
        if run is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        if run["status"] != "paused":
            return jsonify({
                "ok": False,
                "error": "not_paused",
                "status": _run_to_task_status(run["status"]),
                "message": "只有 paused 状态的任务才能继续",
            }), 409
        # FR-022：检查内存中是否已有该 run 的工作
        _activate_run_browser(run)
        with _pipeline_lock:
            existing = _pipeline_tasks.get(run_id)
            if existing is not None and existing.get("status") == "running":
                return jsonify({
                    "ok": False,
                    "error": "already_running",
                    "message": "该任务正在运行，请勿重复点击继续",
                }), 409
        # 版本校验（FR-039）
        run_version = run.get("backend_version")
        if run_version and run_version != _backend_version:
            return jsonify({
                "ok": False,
                "error": "version_mismatch",
                "message": "后端版本已变更，请刷新页面后重试",
                "run_version": run_version,
                "current_version": _backend_version,
            }), 409
        from webui.resume_identity import (
            invalidate_login_cache_for_resume,
            persist_frozen_identity,
            resolve_frozen_identity,
        )
        identity = resolve_frozen_identity(store, run)
        missing = [
            key for key in ("platform", "browser_account", "cdp_port", "profile_key")
            if identity.get(key) in (None, "")
        ]
        if not identity.get("platform") or (
            identity["platform"] == "zhilian" and missing
        ):
            return jsonify({
                "ok": False, "error": "missing_frozen_identity",
                "message": "继续任务缺少冻结的账号或浏览器身份，无法安全恢复", "status": "paused",
                "missing_fields": missing,
            }), 409
        invalidate_login_cache_for_resume(
            identity["browser_account"], identity["platform"])
        persist_frozen_identity(store, run_id, identity)
        run["platform"] = identity["platform"]
        run["execution_params"] = dict(run.get("execution_params") or {})
        run["execution_params"].update(
            {k: v for k, v in identity.items() if v not in (None, "")})
        stage = str(run.get("current_stage") or "")
        if stage.startswith("recrawl_"):
            passed, code, reason = _check_resume_block(run)
            if not passed:
                return jsonify({
                    "ok": False, "error": "block_not_resolved",
                    "error_code": code, "error_reason": reason,
                    "status": "paused",
                }), 409
            return continue_recrawl(run_id, _block_checked=True)
        if stage == "scrape":
            passed, code, reason = _check_resume_block(run)
            if not passed:
                return jsonify({
                    "ok": False, "error": "block_not_resolved",
                    "error_code": code, "error_reason": reason,
                    "status": "paused",
                }), 409
            return continue_execute_search(run_id, _block_checked=True)

        passed, code, reason = _check_resume_block(run)
        if not passed:
            return jsonify({
                "ok": False, "error": "block_not_resolved",
                "error_code": code, "error_reason": reason,
                "status": "paused",
            }), 409

        params = run.get("execution_params") or {}
        scrape_task_id = str(params.get("scrape_task_id") or "")
        profile_summary = str(params.get("profile_summary") or "")
        profile_facts = params.get("profile_facts") or None
        if not scrape_task_id:
            return jsonify({"ok": False, "error": "missing_scrape_task_id"}), 409
        source_jobs = store.load_scrape_run_jobs(scrape_task_id)
        if not source_jobs:
            return jsonify({
                "ok": False,
                "error": "missing_scrape_snapshot",
                "message": "抓取岗位快照缺失，无法安全继续 AI 筛选",
            }), 409

        if not _claim_resume(run_id):
            return jsonify({
                "ok": False,
                "error": "already_running",
                "message": _MSG_TASK_ALREADY_RUNNING,
            }), 409

        # 服务重启后内存来源丢失：从逐组合持久化结果重建只读来源快照。
        with _pipeline_lock:
            _pipeline_tasks[scrape_task_id] = {
                "kind": "scrape", "status": "done", "progress": {}, "logs": [],
                "result": {
                    "ok": True, "jobs": source_jobs,
                    "total_scraped": len(source_jobs), "total_matched": len(source_jobs),
                    "completed_combos": sorted(store.load_checkpoint(scrape_task_id, "scrape")),
                    "error": "",
                },
                "error": "", "started_at": None, "finished_at": None,
                "stop_event": threading.Event(),
            }

        task_id = run_id
        claimed_task, previous_task = _claim_pipeline_task_id(
            task_id, "ai_screen",
            started_at=_iso_epoch_ms(run.get("started_at")),
        )
        if claimed_task is None:
            _release_resume_claim(run_id)
            return jsonify({
                "ok": False,
                "error": "already_running",
                "message": _MSG_TASK_ALREADY_RUNNING,
            }), 409
        claimed_task["source_task_id"] = scrape_task_id
        claimed_task["browser_account"] = _account_for_run(run)
        claimed_task["resumed_from"] = run_id
        resume_params = dict(run.get("execution_params") or {})
        if not str(resume_params.get("browser_account") or ""):
            resume_params["browser_account"] = _account_for_run(run)
            store.update_screening_execution_params(run_id, resume_params)
        claimed_task["platform"] = identity["platform"]
        claimed_task["cdp_port"] = identity.get("cdp_port")
        claimed_task["profile_key"] = identity.get("profile_key")
        claimed_task["browser_account"] = identity["browser_account"]
        resume_params = dict(run.get("execution_params") or {})
        for key in ("platform", "browser_account", "cdp_port", "profile_key"):
            if resume_params.get(key) in (None, ""):
                resume_params[key] = identity.get(key)
        store.update_screening_execution_params(run_id, resume_params)
        start_gate = threading.Event()
        abort_start = threading.Event()

        def run_after_claim_commits(
                task_id, frozen_filters, frozen_profile, source_task_id,
                resume_from_run_id, frozen_facts):
            start_gate.wait()
            if not abort_start.is_set():
                _run_ai_screen_task(
                    task_id,
                    frozen_filters,
                    frozen_profile,
                    source_task_id,
                    resume_from_run_id,
                    frozen_facts,
                )

        try:
            future = _pipeline_executor.submit(
                run_after_claim_commits,
                task_id,
                run.get("frozen_filters") or {},
                profile_summary,
                scrape_task_id,
                run_id,
                profile_facts,
            )
            store.append_task_event(run_id, "resume", {
                "backend_version": _backend_version,
                "task_id": task_id,
            })
            if not store.claim_paused_screening_run(run_id):
                raise RuntimeError("resume_already_claimed")
            with _pipeline_lock:
                if _pipeline_tasks.get(task_id) is claimed_task:
                    claimed_task["status"] = "running"
        except (sqlite3.Error, RuntimeError, ValueError, KeyError) as exc:
            abort_start.set()
            start_gate.set()
            if "future" in locals():
                future.cancel()
            _release_pipeline_claim(task_id, claimed_task, previous_task)
            _release_resume_claim(run_id)
            return jsonify({
                "ok": False,
                "error": "resume_submit_failed",
                "message": f"继续任务提交失败：{type(exc).__name__}",
            }), 500
        start_gate.set()
        return jsonify({
            "ok": True,
            "run_id": task_id,
            "task_id": task_id,
            "resumed_from": run_id,
            "status": "running",
            "message": "AI 筛选已从断点继续",
            # T412 契约 http-api.md L216：成功响应增加 platform 和
            # task_input_digest。平台不由客户端选择，从原 run 读取。
            "platform": run.get("platform"),
            "task_input_digest": run.get("task_input_digest"),
        })

    @app.route("/api/task/cancel/<run_id>", methods=["POST"])
    def api_task_cancel(run_id: str):
        """FR-024：取消任务，保留已有结果，不自动恢复。"""
        # 任务注册先于后台线程创建 DB run，因此取消必须同时支持纯内存窗口。
        with _pipeline_lock:
            task = _pipeline_tasks.get(run_id)
            if task is not None and task.get("status") in {
                "queued", "running", "paused",
            }:
                stop_event = task.get("stop_event")
                if stop_event is not None:
                    stop_event.set()
        run = store.get_screening_run(run_id)
        if run is None and task is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404

        if run is not None and run.get("status") == "interrupted" and run.get("error_code") == "user_finished":
            return jsonify({
                "ok": False, "error": "already_finished",
                "message": "任务已结束保存，无需取消",
            }), 409

        # 有 DB 身份时先提交 durable cancel，再发布内存状态。写入失败时
        # 保持内存原状态，避免页面显示 cancelled 而数据库仍在 running。
        if run is not None:
            try:
                if run["status"] not in (
                    "succeeded", "partial", "failed", "interrupted",
                ):
                    _save_cancelled_history_snapshot(run, task)
                    store.update_screening_run(run_id, status="cancelled")
                    store.append_task_event(run_id, "cancel", {"by": "user"})
            except ValueError as exc:
                latest = store.get_screening_run(run_id)
                if latest is None or latest.get("status") not in (
                    "succeeded", "partial", "failed", "interrupted",
                ):
                    return jsonify({
                        "ok": False,
                        "error": "cancel_state_conflict",
                        "detail": type(exc).__name__,
                    }), 409
            except _OPERATIONAL_ERRORS as exc:
                latest = store.get_screening_run(run_id)
                if latest is not None and latest.get("status") in (
                    "succeeded", "partial", "failed", "interrupted",
                ):
                    with _pipeline_lock:
                        current = _pipeline_tasks.get(run_id)
                        if current is not None:
                            current["status"] = _run_to_task_status(latest["status"])
                            current["error"] = "用户已取消"
                return jsonify({
                    "ok": False,
                    "error": "cancel_persistence_failed",
                    "detail": type(exc).__name__,
                }), 503
            run = store.get_screening_run(run_id)

        with _pipeline_lock:
            current = _pipeline_tasks.get(run_id)
            if current is not None:
                current["status"] = (
                    _run_to_task_status(run["status"])
                    if run is not None else "cancelled"
                )
                current["error"] = "用户已取消"
        _parent_scrape = str(((run or {}).get("execution_params") or {}).get("scrape_task_id") or "")
        _clear_auto_screen(run_id)
        if _parent_scrape and _parent_scrape != run_id:
            _clear_auto_screen(_parent_scrape)
        if task is not None:
            try:
                from webui.pipeline_exec import close_debug_chrome
                if run is not None:
                    _activate_run_browser(run)
                close_debug_chrome()
            except (OSError, RuntimeError):
                pass  # best-effort 关闭浏览器；取消状态已经可靠提交。
        return jsonify({
            "ok": True,
            "run_id": run_id,
            "platform": (run or {}).get("platform"),
            "status": (
                _run_to_task_status(run["status"]) if run is not None else "cancelled"
            ),
            "processed_count": int((run or {}).get("processed_count") or 0),
            "message": "任务已取消，已有结果保留",
        })

    @app.route("/api/task/finish/<run_id>", methods=["POST"])
    def api_task_finish(run_id: str):
        """T416: 结束可恢复任务并生成可展示的部分结果快照。

        允许 queued/running/paused/failed 以及 interrupted(process_restart/
        operator_stop)；user_cancelled 是终态，不能通过 finish 改写。
        """
        run = store.get_screening_run(run_id)
        if run is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        # T416: 检查 interruption_kind
        interruption_kind = run.get("interruption_kind") or ""
        if run["status"] == "interrupted" and run.get("error_code") == "user_finished":
            return jsonify({
                "ok": False, "error": "already_finished",
                "message": "任务已结束保存，请勿重复操作",
            }), 409
        if run["status"] == "interrupted" and interruption_kind == "user_cancelled":
            return jsonify({
                "ok": False, "error": "user_cancelled",
                "message": "用户已取消的任务不能通过 finish 改写",
            }), 409
        if run["status"] == "interrupted" and interruption_kind not in (
                "process_restart", "operator_stop",
        ):
            return jsonify({
                "ok": False, "error": "interrupted_not_restartable",
                "message": "该中断状态不能结束保存",
            }), 409
        if run["status"] in ("succeeded", "partial"):
            return jsonify({
                "ok": False, "error": "already_terminal",
                "status": _run_to_task_status(run["status"]),
                "message": "任务已完成，无需结束保存",
            }), 409
        allowed_finish_statuses = {
            "queued", "running", "paused", "failed", "interrupted",
        }
        if run["status"] not in allowed_finish_statuses:
            return jsonify({
                "ok": False, "error": "not_paused",
                "status": _run_to_task_status(run["status"]),
                "message": "当前任务状态不能结束并保存",
            }), 409
        params = run.get("execution_params") or {}
        scrape_task_id = str(params.get("scrape_task_id") or "")
        source_run_id = str(params.get("source_run_id") or "")
        platform = params.get("platform") or run.get("platform") or "boss"
        source_jobs = []
        verdicts = {}
        pending_rows = []
        jd_map = {}
        source_payload = None
        source_dropped = []
        source_total_scraped = None
        # 先发停止信号并等待当前页原子落库稳定，再从页级快照生成部分结果。
        flush_run_id = scrape_task_id or (
            run_id if str(run.get("current_stage") or "") == "scrape" else ""
        )
        if flush_run_id:
            with _pipeline_lock:
                task = _pipeline_tasks.get(flush_run_id)
                stop_event = task.get("stop_event") if task is not None else None
                flush_lock = task.get("page_flush_lock") if task is not None else None
            if stop_event is not None:
                stop_event.set()
            if flush_lock is not None:
                stable_since = time.monotonic()
                last_seq = None
                flush_deadline = time.monotonic() + 3.0
                while time.monotonic() < flush_deadline:
                    with _pipeline_lock:
                        task = _pipeline_tasks.get(flush_run_id)
                        seq = int((task or {}).get("page_persist_seq") or 0) if task is not None else 0
                    if seq != last_seq:
                        last_seq = seq
                        stable_since = time.monotonic()
                    elif time.monotonic() - stable_since >= 0.2:
                        break
                    time.sleep(0.05)
                if flush_lock.acquire(timeout=3.0):
                    flush_lock.release()
        if scrape_task_id:
            try:
                source_jobs = store.load_scrape_run_jobs(scrape_task_id)
            except _OPERATIONAL_ERRORS:
                source_jobs = []
            verdicts = store.load_screening_verdicts(run_id)
            pending_rows = store.load_screening_pending(run_id)
            try:
                jd_map = _load_jd_checkpoint(
                    _jd_checkpoint_path(app.config["RESULT_DIR"], run_id))
            except RuntimeError as exc:
                return jsonify({
                    "ok": False, "error": str(exc),
                    "message": "JD 断点文件损坏，无法生成部分结果",
                }), 503
        elif source_run_id:
            payload = store.load_latest_pipeline_result(source_run_id)
            source_payload = payload
            source_jobs = ((payload or {}).get("result") or {}).get("jobs") or []
            source_dropped = ((payload or {}).get("result") or {}).get("dropped") or []
            source_total_scraped = ((payload or {}).get("result") or {}).get("total_scraped") or None
            verdicts = store.load_screening_verdicts(source_run_id)
            pending_rows = store.load_screening_pending(run_id)
            if not pending_rows:
                pending_rows = store.load_screening_pending(source_run_id)
        elif not scrape_task_id and not source_run_id and str(run.get("current_stage") or "") == "scrape":
            try:
                source_jobs = store.load_scrape_run_jobs(run_id)
            except _OPERATIONAL_ERRORS:
                source_jobs = []
            verdicts = store.load_screening_verdicts(run_id)
            pending_rows = store.load_screening_pending(run_id)
        if not source_jobs:
            return jsonify({
                "ok": False, "error": "missing_scrape_snapshot",
                "message": "抓取岗位快照缺失，无法生成部分结果",
            }), 409
        profile_summary = str(params.get("profile_summary") or "")
        if not profile_summary and source_run_id:
            if source_payload is None:
                source_payload = store.load_latest_pipeline_result(source_run_id)
            profile_summary = str(((source_payload or {}).get("result") or {}).get("profile_summary") or "")
        parent_scrape_task_id = scrape_task_id
        if not parent_scrape_task_id:
            parent_scrape_task_id = (
                run_id if str(run.get("current_stage") or "") == "scrape" else ""
            )
        # 快照可构建性校验完成后，才停止后台工作并原子标记 user_finished；
        # 无快照时保持原状态，避免把任务永久写成无法恢复的终态（B027）。
        with _pipeline_lock:
            task = _pipeline_tasks.get(run_id)
            if task is not None and task.get("stop_event") is not None:
                task["stop_event"].set()
            # B027：陈旧续跑接管标记不阻断结束保存；先兜底释放，再收尾。
            _resume_claims.discard(run_id)
        try:
            from webui.pipeline_exec import close_debug_chrome
            _activate_run_browser(run)
            close_debug_chrome()
        except (OSError, RuntimeError):
            pass
        # 先原子标记 user_finished：worker 后续不得再写 DB 终态。
        try:
            store.finish_screening_run(run_id)
        except DiscoveryStoreConflictError as exc:
            return jsonify({
                "ok": False, "error": str(exc),
                "message": {
                    "already_finished": "任务已结束保存，请勿重复操作",
                    "already_terminal": "任务已完成，无需结束保存",
                    "user_cancelled": "用户已取消的任务不能结束保存",
                }.get(str(exc), "任务状态已变化，无法结束保存"),
            }), 409
        except KeyError:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        _clear_auto_screen(run_id)
        if scrape_task_id and scrape_task_id != run_id:
            _clear_auto_screen(scrape_task_id)
        result = _build_partial_pipeline_result(
            source_jobs, verdicts, pending_rows, jd_map,
            profile_summary,
            source_dropped=source_dropped,
            total_scraped=source_total_scraped,
            platform=platform,
        )
        snapshot_run_id = store.save_pipeline_result(
            result, {"screening": run.get("frozen_filters") or {}, "platform": platform},
            started_at=run.get("started_at"),
            finished_at=int(time.time() * 1000),
            execution_config=params.get("execution_config") or {},
            status="partial",
            execution_params={
                "platform": platform,
                "scrape_task_id": parent_scrape_task_id,
            },
        )
        # 终态已在 finish_screening_run 原子写入（interrupted/user_finished）。
        _prune_history_best_effort()
        store.append_task_event(run_id, "finish", {
            "snapshot_run_id": snapshot_run_id,
            "stage": run.get("current_stage") or "", "jobs": len(result["jobs"]),
            "dropped": len(result["dropped"]),
        })
        with _pipeline_lock:
            current = _pipeline_tasks.get(run_id)
            if current is not None:
                current["status"] = "cancelled"
                current["error"] = "用户提前结束，已保存部分结果"
                current["result"] = result
                current["finished_at"] = int(time.time() * 1000)
        try:
            from webui.pipeline_exec import close_debug_chrome
            _activate_run_browser(run)
            close_debug_chrome()
        except (OSError, RuntimeError):
            pass
        return jsonify({
            "ok": True, "run_id": run_id, "snapshot_run_id": snapshot_run_id,
            "platform": platform,
            "status": "completed_with_pending", "result": result,
            "scrape_task_id": parent_scrape_task_id,
            "message": "任务已结束，已完成结果已保存",
        })

    @app.route("/api/recovery/preview/<run_id>", methods=["GET"])
    def api_recovery_preview(run_id: str):
        """FR-041：历史恢复只读预演接口。run_id 参数仅作占位，
        实际预演两个历史 run（15847d27 + e6250f0e）。
        """
        from webui.historical_recovery import (
            FINE_RUN_ID,
            ROUGH_RUN_ID,
            preview_recovery,
        )
        try:
            result = preview_recovery(
                store,
                rough_run_id=ROUGH_RUN_ID,
                fine_run_id=FINE_RUN_ID,
                result_dir=app.config["RESULT_DIR"],
            )
            return jsonify({"ok": True, "preview": result})
        except (OSError, sqlite3.Error, RuntimeError, ValueError, KeyError) as exc:
            return jsonify({"ok": False, "error": str(exc),
                            "error_type": type(exc).__name__}), 500

    @app.route("/api/recovery/prepare/<run_id>", methods=["POST"])
    def api_recovery_prepare(run_id: str):
        """Create the server-owned SQLite backup and manifest for recovery."""
        from webui.historical_recovery import prepare_recovery
        try:
            prepared = prepare_recovery(store)
            return jsonify({"ok": True, **prepared}), 201
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            return jsonify({
                "ok": False, "error": str(exc),
                "error_type": type(exc).__name__,
            }), 409

    @app.route("/api/recovery/execute/<run_id>", methods=["POST"])
    def api_recovery_execute(run_id: str):
        """Execute a prepared recovery by opaque server-generated backup id."""
        from webui.historical_recovery import execute_recovery
        body = request.get_json(silent=True) or {}
        backup_id = str(body.get("backup_id") or "").strip()
        if not backup_id:
            return jsonify({
                "ok": False, "error": "missing_backup_id",
                "message": "请先调用 prepare 接口创建恢复备份",
            }), 400
        try:
            result = execute_recovery(backup_id, store=store)
            status = 200 if result.get("ok") else 409
            return jsonify({"ok": result.get("ok", False), "result": result}), status
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc),
                            "error_type": type(exc).__name__}), 500

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

    @app.route("/api/tuning/experiments", methods=["POST"])
    def tuning_create_experiment():
        """POST /api/tuning/experiments

        创建 draft 状态的实验。不启动压力工作。
        """
        body = request.get_json(silent=True) or {}
        spec_version = body.get("spec_version")
        source_scope = body.get("source_scope")
        quality_context = body.get("quality_context")
        if not spec_version or not source_scope or not quality_context:
            return jsonify({
                "ok": False, "error_code": "invalid_request",
                "error": (
                    "缺少 spec_version、source_scope 或 quality_context"
                ),
            }), 400
        if not isinstance(source_scope, dict):
            return jsonify({
                "ok": False, "error_code": "invalid_request",
                "error": "source_scope 必须是对象",
            }), 400
        from webui.tuning import TuningController
        controller = TuningController(store)
        try:
            experiment = controller.create_experiment_with_input(
                spec_version=spec_version,
                source_scope=source_scope,
                workloads=body.get("workloads") or [],
                quality_context=quality_context,
            )
        except (ValueError, TypeError) as exc:
            return jsonify({
                "ok": False, "error_code": "invalid_request",
                "error": str(exc),
            }), 400
        return jsonify({
            "ok": True,
            "experiment_id": experiment["id"],
            "status": experiment["status"],
        }), 201

    @app.route("/api/tuning/experiments/<experiment_id>",
               methods=["GET"])
    def tuning_get_experiment(experiment_id: str):
        """GET /api/tuning/experiments/{id}

        返回实验持久化快照。
        """
        try:
            exp = store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": _MSG_EXPERIMENT_NOT_FOUND,
            }), 404
        from webui.tuning import TuningController
        controller = TuningController(store)
        # 计算进度和剩余时间
        progress = controller.project_remaining_time(experiment_id)
        if progress is None:
            progress = {
                "confirmed_rounds": 0,
                "remaining_required_rounds": 0,
                "estimated_remaining_seconds": 0,
            }
        # 计算可执行操作
        status = exp["status"]
        can_cancel = status not in ("cancelled", "failed", "completed")
        can_resume = status == "blocked"
        can_apply = status == "completed"
        return jsonify({
            "ok": True,
            "experiment": {
                "id": exp["id"],
                "status": status,
                "spec_version": exp["spec_version"],
                "current_stage": exp.get("current_stage"),
                "current_candidate_id": exp.get("current_candidate_id"),
                "input_version_id": exp.get("input_version_id"),
                "quality_reference_id": exp.get("quality_reference_id"),
                "blocked_code": exp.get("blocked_code"),
                "blocked_reason": exp.get("blocked_reason"),
                "source_scope": exp.get("source_scope", {}),
                "created_at": exp.get("created_at"),
                "progress": progress,
                "can_cancel": can_cancel,
                "can_resume": can_resume,
                "can_apply": can_apply,
            },
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/cancel",
               methods=["POST"])
    def tuning_cancel_experiment(experiment_id: str):
        """POST /api/tuning/experiments/{id}/cancel

        取消实验，保留已确认证据，释放租约。
        """
        try:
            store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": _MSG_EXPERIMENT_NOT_FOUND,
            }), 404
        from webui.tuning import TuningController
        controller = TuningController(store)
        try:
            controller.cancel_experiment(experiment_id)
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "invalid_state",
                "error": str(exc),
            }), 409
        return jsonify({
            "ok": True,
            "experiment_id": experiment_id,
            "status": "cancelled",
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/confirm-input",
               methods=["POST"])
    def tuning_confirm_input(experiment_id: str):
        """POST /api/tuning/experiments/{id}/confirm-input

        冻结输入版本，推进实验到 preflight。
        """
        try:
            exp = store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": _MSG_EXPERIMENT_NOT_FOUND,
            }), 404
        if exp["status"] != "draft":
            return jsonify({
                "ok": False, "error_code": "invalid_state",
                "error": f"实验状态不是 draft: {exp['status']}",
            }), 409
        from webui.tuning import TuningController
        controller = TuningController(store)
        try:
            result = controller.confirm_input(experiment_id)
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "input_incomplete",
                "error": str(exc),
            }), 409
        return jsonify({
            "ok": True,
            "experiment_id": experiment_id,
            **result,
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/resume",
               methods=["POST"])
    def tuning_resume_experiment(experiment_id: str):
        """POST /api/tuning/experiments/{id}/resume

        从 blocked 状态恢复到 awaiting_instruction。
        只允许 blocked 状态恢复，不自动选择新候选。
        """
        try:
            exp = store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": _MSG_EXPERIMENT_NOT_FOUND,
            }), 404
        if exp["status"] != "blocked":
            return jsonify({
                "ok": False, "error_code": "invalid_state",
                "error": f"只有 blocked 状态才能恢复，当前: {exp['status']}",
            }), 409
        try:
            store.update_tuning_experiment_status(
                experiment_id, status="awaiting_instruction",
            )
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "invalid_state",
                "error": str(exc),
            }), 409
        return jsonify({
            "ok": True,
            "experiment_id": experiment_id,
            "status": "awaiting_instruction",
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/result",
               methods=["GET"])
    def tuning_get_experiment_result(experiment_id: str):
        """Return safe candidate/evidence summary with an objective apply gate."""
        from webui.tuning import TuningController
        controller = TuningController(store)
        try:
            result = controller.get_experiment_result(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": _MSG_EXPERIMENT_NOT_FOUND,
            }), 404
        return jsonify({"ok": True, **result}), 200

    @app.route("/api/tuning/experiments/<experiment_id>/apply",
               methods=["POST"])
    def tuning_apply_experiment_result(experiment_id: str):
        """Apply one exact complete nine-slot candidate after explicit request."""
        body = request.get_json(silent=True) or {}
        digest = body.get("candidate_mode_version_digest")
        if not digest:
            return jsonify({
                "ok": False, "error_code": "invalid_request",
                "error": "缺少 candidate_mode_version_digest",
            }), 400
        from webui.tuning import TuningController
        controller = TuningController(store)
        try:
            version = controller.apply_candidate_mode_version(
                experiment_id=experiment_id, version_digest=str(digest),
            )
        except KeyError:
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": _MSG_EXPERIMENT_NOT_FOUND,
            }), 404
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "result_not_applicable",
                "error": str(exc),
            }), 409
        return jsonify({
            "ok": True, "mode_version_id": version["id"],
            "version_digest": version["version_digest"],
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/manifests",
               methods=["POST"])
    def tuning_issue_manifest(experiment_id: str):
        """POST /api/tuning/experiments/{id}/manifests

        控制者签发一份不可变任务单。
        """
        from webui.tuning import TuningController

        body = request.get_json(silent=True) or {}
        # 确保路径参数与 body 一致
        body["experiment_id"] = experiment_id
        controller = TuningController(store)
        # 校验实验存在且处于 awaiting_instruction
        try:
            exp = store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": _MSG_EXPERIMENT_NOT_FOUND,
            }), 404
        if exp["status"] != "awaiting_instruction":
            return jsonify({
                "ok": False, "error_code": "invalid_experiment_status",
                "error": f"实验状态不是 awaiting_instruction: {exp['status']}",
            }), 409
        try:
            result = controller.issue_manifest(body)
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "manifest_validation_failed",
                "error": str(exc),
            }), 422
        return jsonify({"ok": True, **result}), 201

    @app.route("/api/tuning/manifests/<manifest_id>", methods=["GET"])
    def tuning_get_manifest(manifest_id: str):
        """GET /api/tuning/manifests/{id}

        返回安全结构化 manifest，不含凭据。
        """
        try:
            record = store.get_task_manifest(manifest_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "manifest_not_found",
                "error": _MSG_MANIFEST_NOT_FOUND,
            }), 404
        # 不返回凭据/敏感字段
        safe_manifest = dict(record["manifest"])
        # 移除可能的敏感字段
        for sensitive in ("api_key", "credentials", "password", "token"):
            safe_manifest.pop(sensitive, None)
        return jsonify({
            "ok": True,
            "manifest_id": record["id"],
            "manifest": safe_manifest,
            "manifest_digest": record["manifest_digest"],
            "rendered_task_path": record["rendered_task_path"],
            "status": record["status"],
        }), 200

    @app.route("/api/tuning/manifests/<manifest_id>/execute",
               methods=["POST"])
    def tuning_execute_manifest(manifest_id: str):
        """POST /api/tuning/manifests/{id}/execute

        启动 manifest 对应的轮次。重新校验摘要、产物、租约。
        """
        from webui.tuning import TuningController

        try:
            record = store.get_task_manifest(manifest_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "manifest_not_found",
                "error": _MSG_MANIFEST_NOT_FOUND,
            }), 404
        controller = TuningController(store)
        try:
            started = controller.execute_manifest(manifest_id)
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "round_state_conflict",
                "error": str(exc),
            }), 409
        round_id = started["round_id"]
        child_task_id = record["manifest"].get("task_id") or round_id
        if app.config.get("START_TASKS"):
            try:
                _pipeline_executor.submit(_run_tuning_manifest_child, manifest_id)
            except RuntimeError as exc:
                return jsonify({
                    "ok": False, "error_code": "submit_failed", "error": str(exc),
                }), 503
        return jsonify({
            "ok": True,
            "child_task_id": child_task_id,
            "round_id": round_id,
            "status": "running",
            "status_url": f"/api/tuning/rounds/{round_id}",
        }), 202

    @app.route("/api/tuning/rounds/<round_id>", methods=["GET"])
    def tuning_get_round(round_id: str):
        """GET /api/tuning/rounds/{id}

        返回轮次的程序状态。
        """
        try:
            round_rec = store.get_tuning_round(round_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "round_not_found",
                "error": "轮次不存在",
            }), 404
        return jsonify({
            "ok": True,
            "round": {
                "id": round_rec["id"],
                "status": round_rec["status"],
                "experiment_id": round_rec["experiment_id"],
                "candidate_id": round_rec["candidate_id"],
                "round_kind": round_rec["round_kind"],
                "repetition_index": round_rec["repetition_index"],
                "manifest_id": round_rec.get("manifest_id"),
                "started_at": round_rec.get("started_at"),
                "finished_at": round_rec.get("finished_at"),
                "confirmed_at": round_rec.get("confirmed_at"),
                "failure_code": round_rec.get("failure_code"),
            },
        }), 200

    @app.route("/api/tuning/manifests/<manifest_id>/report",
               methods=["POST"])
    def tuning_submit_report(manifest_id: str):
        """POST /api/tuning/manifests/{id}/report

        接受一份执行者报告，校验并更新轮次状态。
        """
        from webui.tuning import TuningController

        body = request.get_json(silent=True) or {}
        try:
            _ = store.get_task_manifest(manifest_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "manifest_not_found",
                "error": _MSG_MANIFEST_NOT_FOUND,
            }), 404
        try:
            saved = TuningController(store).accept_report(
                manifest_id=manifest_id, report=body)
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "report_validation_failed",
                "error": str(exc),
                "validation_status": "rejected",
            }), 422
        return jsonify({
            "ok": True,
            "report_id": saved["report_id"],
            "validation_status": "accepted",
            "round_status": saved["round_status"],
            "experiment_status": saved["experiment_status"],
        }), 201

    @app.route("/api/tuning/rounds/<round_id>/evidence", methods=["GET"])
    def tuning_get_evidence(round_id: str):
        """GET /api/tuning/rounds/{id}/evidence

        返回安全聚合证据，不含凭据/原始简历/原始模型响应。
        """
        from webui.tuning import TuningController

        try:
            _ = store.get_tuning_round(round_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "round_not_found",
                "error": "轮次不存在",
            }), 404
        controller = TuningController(store)
        try:
            summary = controller.aggregate_measurements(round_id)
        except (KeyError, ValueError):
            summary = {}
        # 不返回敏感字段
        safe_summary = {
            "total_duration_ms": summary.get("total_duration_ms", 0),
            "stage_durations_ms": summary.get("stage_durations_ms", {}),
            "wait_duration_ms": summary.get("wait_duration_ms", 0),
            "retry_duration_ms": summary.get("retry_duration_ms", 0),
            "attempt_count": summary.get("attempt_count", 0),
            "retry_count": summary.get("retry_count", 0),
            "input_count": summary.get("input_count", 0),
            "terminal_count": summary.get("terminal_count", 0),
            "success_count": summary.get("success_count", 0),
            "failed_count": summary.get("failed_count", 0),
            "missing_count": summary.get("missing_count", 0),
            "duplicate_count": summary.get("duplicate_count", 0),
            "error_counts": summary.get("error_counts", {}),
            "error_correlation_id": summary.get("error_correlation_id"),
        }
        return jsonify({
            "ok": True,
            "evidence": safe_summary,
            "round_id": round_id,
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/decisions",
               methods=["POST"])
    def tuning_post_decision(experiment_id: str):
        """POST /api/tuning/experiments/{id}/decisions

        控制者对候选做出 promote/reject/refine 决策。
        执行者 AI 不能调用此路由。
        """
        body = request.get_json(silent=True) or {}
        candidate_id = body.get("candidate_id")
        decision = body.get("decision")
        if not candidate_id or not decision:
            return jsonify({
                "ok": False, "error_code": "invalid_request",
                "error": "缺少 candidate_id 或 decision",
            }), 400
        if decision not in ("promote", "reject", "refine"):
            return jsonify({
                "ok": False, "error_code": "invalid_decision",
                "error": f"未知决策类型: {decision}",
            }), 422
        # 校验实验存在
        try:
            _ = store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": _MSG_EXPERIMENT_NOT_FOUND,
            }), 404
        # 校验候选存在且属于该实验
        try:
            candidate = store.get_tuning_candidate(candidate_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "candidate_not_found",
                "error": "候选不存在",
            }), 404
        if candidate["experiment_id"] != experiment_id:
            return jsonify({
                "ok": False, "error_code": "candidate_mismatch",
                "error": "候选不属于该实验",
            }), 422
        # 校验 evidence ownership
        reason_evidence = body.get("reason_evidence", [])
        if not isinstance(reason_evidence, list):
            reason_evidence = []
        now = datetime.now().isoformat()
        # 应用决策
        if decision == "promote":
            with store._connection() as conn:
                conn.execute(
                    "UPDATE tuning_candidates "
                    "SET status = 'promoted', promotion_reason = ?, "
                    "    updated_at = ? WHERE id = ?",
                    (json.dumps(reason_evidence, ensure_ascii=False),
                     now, candidate_id),
                )
        elif decision == "reject":
            code = body.get("code", "rejected")
            with store._connection() as conn:
                conn.execute(
                    "UPDATE tuning_candidates "
                    "SET status = 'rejected', rejection_code = ?, "
                    "    updated_at = ? WHERE id = ?",
                    (code, now, candidate_id),
                )
        elif decision == "refine":
            next_config = body.get("next_config")
            if not next_config:
                return jsonify({
                    "ok": False, "error_code": "missing_next_config",
                    "error": "refine 决策必须提供 next_config",
                }), 422
            # 创建新的候选
            new_candidate = store.save_tuning_candidate(
                experiment_id=experiment_id,
                stage=candidate["stage"],
                strategy_step=candidate["strategy_step"],
                config=next_config,
                parent_candidate_id=candidate_id,
            )
            return jsonify({
                "ok": True,
                "decision": "refine",
                "new_candidate_id": new_candidate["id"],
            }), 200
        return jsonify({
            "ok": True,
            "decision": decision,
            "candidate_id": candidate_id,
        }), 200

    # Task 008：注册 lifecycle/state/events/reminders/advice 路由（Task 005）。
    # before_request 的 Host/会话令牌/build identity 防护自动覆盖这些路由；
    # 提醒/生命周期规则平台无关，无 platform 过滤。
    register_job_feedback_routes(app, store)

    register_result_history_routes(app, store)

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, threaded=True)
