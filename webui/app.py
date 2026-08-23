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
    if status == "interrupted" and interruption_kind == "user_finished":
        return "cancelled"
    return mapping.get(status, status or "failed")


def _pipeline_kind_for_stage(stage: str) -> str:
    """把 screening run 阶段映射为对外 pipeline kind。"""
    if str(stage).startswith("recrawl_"):
        return "recrawl"
    if str(stage) == "scrape":
        return "scrape"
    return "ai_screen"


def _active_elapsed_ms(started_at_ms, finished_at_ms, events):
    """从 pause/resume 事件推导累计实际运行时长（排除暂停），单位毫秒。

    暂停时间不计入"已用"：暂停区间以 task_logs 的 pause/resume 事件为准，
    未闭合的 pause（任务仍处于暂停态）按暂停持续到截止时刻处理。
    无 started_at 时返回 None（调用方沿用 started_at 差值回退）；
    无 pause/resume 事件但有 started_at 时返回跨度（全程实际运行，无暂停）。
    """
    if not started_at_ms:
        return None
    end_ms = finished_at_ms if finished_at_ms is not None else int(time.time() * 1000)
    paused_ms = 0
    pause_start = None
    for event in events or []:
        if not isinstance(event, dict):
            continue
        at = _iso_epoch_ms(event.get("at"))
        if at is None:
            continue
        if event.get("type") == "pause" and pause_start is None:
            pause_start = at
        elif event.get("type") == "resume" and pause_start is not None:
            paused_ms += max(0, at - pause_start)
            pause_start = None
    if pause_start is not None:
        # 最后一次暂停还没有对应 resume（当前暂停中或事件流未闭合）。
        paused_ms += max(0, end_ms - pause_start)
    return max(0, (end_ms - started_at_ms) - paused_ms)


def _resolve_run_scope(run, store):
    """从 run 或其父抓取 run 解析 frozen_scope；都不可用返回 None。

    AI 筛选/抓取 run 自带 frozen_scope；补抓（recrawl）run 没有，从
    source_run_id 指向的父抓取 run 继承（同一批岗位，规模一致）。
    """
    from webui.execution_config import FrozenTaskScope
    params = run.get("execution_params") or {}
    candidates = [params.get("frozen_scope")]
    source_run_id = str(params.get("source_run_id") or "")
    if source_run_id:
        try:
            parent = store.get_screening_run(source_run_id)
            parent_params = (parent or {}).get("execution_params") or {}
        except _OPERATIONAL_ERRORS:
            parent_params = {}
        candidates.append(parent_params.get("frozen_scope"))
    for raw in candidates:
        if not raw:
            continue
        try:
            return FrozenTaskScope.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _refresh_paused_run_execution_config(run, store):
    """按当前 active 配置刷新 paused run 的 execution_config 并写回 DB。

    当前配置来源与新建任务一致：``store.get_advanced_config_state()`` +
    ``store.select_mode(active_selection, task_size=frozen_scope.task_size)``。
    pages/frozen_scope 不在 execution_config 中，保持不变。
    返回新 ExecutionConfigSnapshot；scope 缺失或配置解析失败时返回 None。
    """
    from webui.execution_config import ExecutionConfigSnapshot
    frozen_scope = _resolve_run_scope(run, store)
    if frozen_scope is None:
        return None
    try:
        state = store.get_advanced_config_state()
        selected = store.select_mode(
            state["active_selection"], task_size=frozen_scope.task_size,
        )
        config = ExecutionConfigSnapshot.from_dict(selected["config"])
    except (KeyError, TypeError, ValueError):
        return None
    params = run.get("execution_params") or {}
    new_params = dict(params)
    new_params["execution_config"] = config.to_dict()
    try:
        store.update_screening_execution_params(run.get("id") or run.get("run_id"), new_params)
    except _OPERATIONAL_ERRORS:
        return None
    return config

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
        # 上传前由 Flask 直接拦截超大请求体，避免先把文件读入内存再校验。
        MAX_CONTENT_LENGTH=11 * 1024 * 1024,
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
        # 本地单用户工具：读取本地任务、画像、收藏、下载状态等 GET 也
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
            or path.startswith("/api/recovery/")
            or path.startswith("/api/check")
            or path.startswith("/api/env-check")
            or path.startswith("/api/job-reminders")
            or path.startswith("/api/result-history")
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
        from webui.runners.tuning_manifest import run_tuning_manifest_child

        return run_tuning_manifest_child(ctx, manifest_id)


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
        """30 分钟后自动移除已完成任务，避免内存泄漏。

        定时器只删除排程时刻捕获的那个任务对象：续跑会用同一 run_id
        重新注册新任务，旧暂停任务的定时器到点不能误删仍在运行的新任务。
        """
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)

        def _cleanup():
            with _pipeline_lock:
                if _pipeline_tasks.get(task_id) is task:
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

    def _activate_task_browser(task_id: str, *, platform: str | None = None,
                               browser_account: str | None = None) -> None:
        """Bind CDP helpers to a task's frozen browser identity.

        ``ensure_chrome_ready`` reads a process-wide active profile.  A
        background recrawl must therefore rebind it from its frozen task
        identity immediately before checking CDP, rather than inheriting the
        profile last selected by a request or another task.
        """
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id) or {}
            account = str(browser_account or task.get("browser_account") or "")
        from webui.pipeline_exec import resolve_browser_account, set_active_cdp_data_dir
        profile_dir = resolve_browser_account(
            account, app.config["BROWSER_ACCOUNTS_PATH"])
        if profile_dir:
            resolved_platform = str(platform or task.get("platform") or "boss")
            from webui.platforms import resolve_login_space
            _ = resolve_login_space(
                resolved_platform, account or "a", boss_profile_dir=profile_dir
            )
            from webui.platforms import derive_zhilian_profile_dir
            set_active_cdp_data_dir(
                profile_dir if resolved_platform == "boss"
                else derive_zhilian_profile_dir(profile_dir)
            )
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
            _raw_code = str(run.get("error_code") or "")
            code = resolve_code(_raw_code) if _raw_code else ""
            reason = ""
            passed = True
            _ai_resume_codes = {
                "ai_rate_limited", "ai_quota_exhausted",
                "ai_key_invalid", "ai_network_error",
            }
            try:
                if code in SYSTEMIC_BLOCK_CODES and code not in _ai_resume_codes:
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
                        code = "source_cdp_unavailable"
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
                            code = resolve_code(source_code) if source_code else (
                                code or "source_blocked")
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
        for job in jobs or []:
            if not isinstance(job, dict) or str(job.get("jd") or "").strip():
                continue
            job_id = str(job.get("platform_job_id") or job.get("job_id") or job.get("id") or "").strip()
            failed_code = str(job.get("jd_failed_code") or "").strip()
            if not job_id or not failed_code:
                continue
            taxonomy_code = resolve_code(failed_code)
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

    # ------------------------------------------------------------------
    # 021 B3/B4：共享运行态显式化为 PipelineContext；四个后台 runner 及其
    # 助手经 ctx 访问（替代闭包捕获）。可 patch 的 webui.app 模块级符号
    # 不在此绑定——ctx.__getattr__ 动态读取 webui.app，保证 patch 面不变。
    # 字段集随外迁批次按实际捕获清单补全（data-model 规则）。
    # ------------------------------------------------------------------
    from webui.pipeline_context import PipelineContext

    ctx = PipelineContext(
        app=app,
        store=store,
        tasks=_pipeline_tasks,
        lock=_pipeline_lock,
        resume_claims=_resume_claims,
        executor=_pipeline_executor,
        write_run=_write_run_unless_finished,
        make_cdp_source=_make_cdp_source,
        tuning_round_runner=_tuning_round_runner,
        is_user_finished=_is_user_finished,
        release_worker_resume_claims=_release_worker_resume_claims,
        record_pause_failure=_record_pause_failure,
        account_for_run=_account_for_run,
        activate_task_browser=_activate_task_browser,
        clear_auto_screen=_clear_auto_screen,
        schedule_pipeline_task_cleanup=_schedule_pipeline_task_cleanup,
        persist_jd_job_failures=_persist_jd_job_failures,
        load_legacy_advanced_settings=_load_legacy_advanced_settings,
        event_stage_names=_EVENT_STAGE_NAMES,
        screen_stage_messages=_SCREEN_STAGE_MESSAGES,
        operational_errors=_OPERATIONAL_ERRORS,
        msg_user_finished=_MSG_USER_FINISHED,
        msg_user_stopped_scrape=_MSG_USER_STOPPED_SCRAPE,
        msg_user_stopped_screen=_MSG_USER_STOPPED_SCREEN,
        recrawl_overall_percent=_recrawl_overall_percent,
        screen_overall_percent=_screen_overall_percent,
        prune_history_best_effort=_prune_history_best_effort,
        runtime_mode=_runtime_mode,
        run_tuning_manifest_child=_run_tuning_manifest_child,
        save_legacy_advanced_settings=_save_legacy_advanced_settings,
        invalidate_login_cache=_invalidate_login_cache,
        activate_run_browser=_activate_run_browser,
        scope_previews=scope_previews,
        check_resume_block=_check_resume_block,
        check_tuning_lease_conflict=_check_tuning_lease_conflict,
        claim_pipeline_task_id=_claim_pipeline_task_id,
        release_pipeline_claim=_release_pipeline_claim,
        register_pipeline_task=_register_pipeline_task,
        claim_recrawl_start=_claim_recrawl_start,
        claim_resume=_claim_resume,
        release_resume_claim=_release_resume_claim,
        ensure_scrape_source=_ensure_scrape_source,
        consume_auto_screen=_consume_auto_screen,
        runner=runner,
        workbench_runner=workbench_runner,
        job_feedback_service=job_feedback_service,
        history_service=history_service,
        resume_service=resume_service,
    )
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

    def _latest_paused_run_for_browser_close() -> tuple[dict | None, int | None]:
        """Return the latest paused run and its frozen CDP port, if any."""
        try:
            with store._connection() as conn:
                row = conn.execute(
                    "SELECT id FROM screening_runs WHERE status = 'paused' "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
        except (sqlite3.Error, RuntimeError):
            return None, None
        if row is None:
            return None, None
        try:
            run = store.get_screening_run(row["id"]) or {}
        except _OPERATIONAL_ERRORS:
            return None, None
        params = run.get("execution_params") or {}
        if not isinstance(params, dict):
            params = {}
        raw_port = params.get("cdp_port")
        try:
            frozen_port = int(raw_port) if raw_port not in (None, "") else None
        except (TypeError, ValueError):
            frozen_port = None
        return run, frozen_port

    def _close_paused_run_browser() -> None:
        """Best-effort close the automation browser frozen by the paused run."""
        from webui.pipeline_exec import close_debug_chrome
        run, frozen_port = _latest_paused_run_for_browser_close()
        if run is None:
            return
        try:
            _activate_run_browser(run)
        except (OSError, RuntimeError, ValueError):
            pass
        try:
            close_debug_chrome(
                frozen_port if frozen_port is not None else boss.DEFAULT_CDP_PORT)
        except (OSError, RuntimeError):
            pass

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

    # 021 B6：定义晚于组装点，原地补绑定（浏览器锁共享助手）
    ctx.browser_lock = _browser_lock
    ctx.browser_busy = _browser_busy
    ctx.close_paused_run_browser = _close_paused_run_browser
    ctx.has_active_pipeline_task = _has_active_pipeline_task
    ctx.project_browser_accounts = _project_browser_accounts

























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
    register_job_feedback_routes(app, store)

    register_result_history_routes(app, store)

    from webui.location_api import bp as location_api_bp
    app.register_blueprint(location_api_bp)

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, threaded=True)
