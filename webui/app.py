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

        return jsonify({
            "ok": True,
            "runtime_mode": _runtime_mode,
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
        search = validate_search_params(raw)
        profile_raw = raw.get("profile") if "profile" in raw else store.load_profile()
        normalized_profile = normalize_profile(profile_raw)
        store.save_profile(normalized_profile)
        task = runner.create_scrape(search, normalized_profile)
        payload: dict = {"task": _tag_boss(task)}
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
            writer.writerow({key: boss.csv_safe_cell(row.get(key, "")) for key in columns})

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
            # 先把简历文件移到 .trash，DB 删除失败可回滚；DB 删除成功后再
            # 清理回收文件，避免 DB 失败时原始简历文件已不可恢复。
            resume_dir = Path(app.config["RESUME_DIR"]).resolve()
            trash_dir = resume_dir / ".trash"
            moved: list[tuple[Path, Path]] = []
            try:
                for r in store.list_resumes(profile_id):
                    if r.get("deleted_at"):
                        continue
                    try:
                        storage_path = store.get_resume(r["id"]).get("storage_path") or ""
                    except KeyError:
                        continue
                    if not storage_path:
                        continue
                    file_path = (resume_dir / storage_path).resolve()
                    try:
                        file_path.relative_to(resume_dir)
                    except ValueError:
                        continue
                    if not file_path.is_file():
                        continue
                    trash_dir.mkdir(parents=True, exist_ok=True)
                    trash_path = trash_dir / f"{file_path.name}.{uuid.uuid4().hex}.trash"
                    file_path.replace(trash_path)
                    moved.append((trash_path, file_path))
            except Exception as exc:
                for trash_path, original_path in reversed(moved):
                    try:
                        if trash_path.exists():
                            trash_path.replace(original_path)
                    except OSError:
                        app.logger.exception("简历文件回滚失败：%s -> %s", trash_path, original_path)
                app.logger.exception("简历文件清理失败，画像未删除：%s", exc)
                try:
                    trash_dir.rmdir()
                except OSError:
                    pass
                return jsonify({
                    "ok": False,
                    "error": "简历文件清理失败，画像未删除",
                    "error_code": "resume_cleanup_failed",
                }), 500
            try:
                result = store.delete_profile(profile_id)
            except Exception:
                for trash_path, original_path in reversed(moved):
                    try:
                        if trash_path.exists():
                            trash_path.replace(original_path)
                    except OSError:
                        app.logger.exception("DB 删除失败后简历文件回滚失败：%s -> %s", trash_path, original_path)
                try:
                    trash_dir.rmdir()
                except OSError:
                    pass
                raise
            cleanup_warning = False
            for trash_path, _original in moved:
                try:
                    if trash_path.exists():
                        trash_path.unlink()
                except OSError:
                    cleanup_warning = True
                    app.logger.warning("画像已删除，但回收文件清理失败：%s", trash_path)
            if cleanup_warning:
                result["cleanup_warning"] = True
            else:
                try:
                    trash_dir.rmdir()
                except OSError:
                    pass
            return jsonify(result)
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
                _, conflict = ctx.check_run_identity_conflict(run_id, task)
                if conflict is not None:
                    return conflict
                if task["status"] in ("done", "partial", "failed", "cancelled") and task.get(
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
        source_summary, source_outcomes = ctx.build_source_summary_and_outcomes(run_id)
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
        stage = (
            (run or {}).get("current_stage")
            or live_progress.get("stage")
            or "unknown"
        )
        processed_db = int((run or {}).get("processed_count") or 0)
        durable_completed = 0
        if live_kind == "scrape" or str(stage) == "scrape":
            # A resumed scrape can finish combinations after the last run
            # projection write (or while the process is being refreshed). The
            # checkpoint is committed with each combo and is therefore the
            # durable floor for the user-facing progress counter.
            try:
                durable_completed = len(store.load_checkpoint(run_id, "scrape"))
            except _OPERATIONAL_ERRORS:
                durable_completed = 0
        processed_db = max(processed_db, durable_completed)
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
        progress_kind = live_kind or _pipeline_kind_for_stage(stage)
        # Recrawl runs persist their final stage as ``done``.  Once the live
        # task has been cleaned up, recover the kind from the durable run id
        # so refreshes retain recrawl-specific messaging/count semantics.
        if progress_kind == "ai_screen" and str(run_id).startswith("recrawl-"):
            progress_kind = "recrawl"
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
        if not count_live:
            # A browser refresh may leave an in-memory snapshot behind the
            # durable checkpoint. Do not let that stale current/percent mask
            # the reconciled combo count.
            progress["current"] = max(
                int(progress.get("current") or 0),
                success_count if jd_stage else completed_count,
            )
            if durable_completed > live_current:
                progress["overall_percent"] = overall_percent
        else:
            progress.setdefault("current", success_count if jd_stage else completed_count)
        progress.setdefault("total", stage_total)
        # A persisted terminal task has no in-memory live progress after a
        # refresh. Reconstruct the user-facing result message from durable
        # status/counts so a partial recrawl never falls back to generic
        # success text.
        if (
            progress_kind == "recrawl"
            and str((run or {}).get("status") or "") == "partial"
            and pending > 0
        ):
            progress.setdefault("message", f"重抓完成，但仍有 {pending} 个岗位待确认")
        pause_info = None
        effective_status = _public_task_status(
            str(live.get("status")) if live is not None else str(run["status"]),
            (run or {}).get("interruption_kind"),
        )
        _resolved_error_code = (
            resolve_code(error_code) if error_code else "")
        if effective_status == "paused" or (
                effective_status == "failed" and error_code) or (
                _resolved_error_code and _resolved_error_code in SYSTEMIC_BLOCK_CODES):
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
        # 暂停不计时：从 task_logs 的 pause/resume 事件推导累计实际运行时长。
        # 刷新页面后仍有效（事件已持久化）；无事件或无法计算时回退 None。
        try:
            task_events = store.list_task_events(run_id)
        except _OPERATIONAL_ERRORS:
            task_events = []
        active_elapsed_ms = _active_elapsed_ms(started_at, finished_at, task_events)
        # 016：软失败组合留痕（combo_issue/kind=combo_failed），倒序取最近 20 条；
        # 文案来自统一注册表，前端只展示不猜码。
        combo_issues = []
        _platform_label = str((run or {}).get("platform") or (live or {}).get("platform") or "")
        for _event in reversed(task_events):
            _payload = _event.get("payload") or {}
            if (
                _event.get("type") == "combo_issue"
                and _payload.get("kind") == "combo_failed"
            ):
                _code = resolve_code(
                    str(_payload.get("failed_code") or "source_unknown_error"))
                combo_issues.append({
                    "combo_key": str(_payload.get("combo_key") or ""),
                    "code": _code,
                    "code_text": failed_code_label(_code, _platform_label) or _code,
                    "reason": str(_payload.get("reason") or ""),
                    "ts": _payload.get("ts") or _event.get("at") or "",
                })
            if len(combo_issues) >= 20:
                break
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
            "active_elapsed_ms": active_elapsed_ms,
            # T405: 平台身份与 source outcomes 汇总
            "platform": (run or {}).get("platform") or (live or {}).get("platform"),
            "task_input_digest": (run or {}).get("task_input_digest"),
            "auto_screen": bool((live or {}).get("auto_screen", exec_params.get("auto_screen"))),
            "source_summary": source_summary,
            "source_outcomes": source_outcomes,
            "combo_issues": combo_issues,
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
                "status": _public_task_status(run["status"], run.get("interruption_kind")),
                "message": "只有 paused 状态的任务才能继续",
            }), 409
        # B057：限流后可指定另一个已登录账号继续同一断点。
        # 未显式指定时使用当前 active 账号；只替换执行账号与浏览器身份，
        # 任务身份/断点/结果保持不变。
        _continue_body = request.get_json(silent=True) or {}
        target_account = str(_continue_body.get("target_account") or "").strip()
        if not target_account:
            active_account = str(
                (_load_legacy_advanced_settings() or {}).get("browser_account") or "a"
            )
            frozen_account = str(
                ((run.get("execution_params") or {}).get("browser_account") or "")
                or _account_for_run(run)
            )
            if active_account and active_account != frozen_account:
                target_account = active_account
        if target_account:
            from webui.pipeline_exec import load_browser_accounts, resolve_browser_account
            from webui.platforms import resolve_login_space
            from webui.resume_identity import persist_frozen_identity
            accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
            if target_account not in accounts:
                return jsonify({
                    "ok": False, "error": "target_account_not_found",
                    "message": "目标账号不存在，请刷新账号列表后重试",
                    "status": "paused",
                }), 404
            platform = str(run.get("platform") or (run.get("execution_params") or {}).get("platform") or "boss")
            target_dir = resolve_browser_account(
                target_account, app.config["BROWSER_ACCOUNTS_PATH"]) or "unresolved"
            try:
                _login_space = resolve_login_space(
                    platform, target_account, boss_profile_dir=target_dir)
            except ValueError:
                return jsonify({
                    "ok": False, "error": "target_account_invalid",
                    "message": "目标账号浏览器身份不可用，请确认该账号已配置",
                    "status": "paused",
                }), 409
            candidate = {
                "platform": platform,
                "browser_account": target_account,
                "cdp_port": _login_space.cdp_port,
                "profile_key": _login_space.profile_key,
            }
            candidate_params = dict(run.get("execution_params") or {})
            candidate_params.update(
                {k: v for k, v in candidate.items() if v not in (None, "")})
            candidate_run = dict(run)
            candidate_run["execution_params"] = candidate_params
            candidate_run["platform"] = platform
            passed, code, reason = _check_resume_block(candidate_run)
            if not passed:
                return jsonify({
                    "ok": False, "error": "block_not_resolved",
                    "error_code": code, "error_reason": reason,
                    "target_account": target_account,
                    "status": "paused",
                    "message": (
                        f"目标账号「{accounts[target_account].get('name') or target_account}」"
                        f"暂不可用：{reason}"
                    ),
                }), 409
            persist_frozen_identity(store, run_id, candidate)
            run = store.get_screening_run(run_id) or run
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
        # 高级设置续跑生效：block 检查通过后才按当前 active 配置刷新该 run 的
        # execution_config 并写回 DB（三条续跑路径统一从刷新后的配置读取）。
        # pages/frozen_scope 保持冻结不变；block 未解除时不提前改写 DB 快照。
        stage = str(run.get("current_stage") or "")
        refreshed_config = None

        def _refresh_run_config():
            nonlocal refreshed_config
            refreshed_config = _refresh_paused_run_execution_config(run, store)
            if refreshed_config is not None:
                run["execution_params"]["execution_config"] = refreshed_config.to_dict()

        if stage.startswith("recrawl_"):
            passed, code, reason = _check_resume_block(run)
            if not passed:
                return jsonify({
                    "ok": False, "error": "block_not_resolved",
                    "error_code": code, "error_reason": reason,
                    "status": "paused",
                }), 409
            _refresh_run_config()
            return ctx.continue_recrawl(run_id, _block_checked=True)
        if stage == "scrape":
            passed, code, reason = _check_resume_block(run)
            if not passed:
                return jsonify({
                    "ok": False, "error": "block_not_resolved",
                    "error_code": code, "error_reason": reason,
                    "status": "paused",
                }), 409
            _refresh_run_config()
            return ctx.continue_execute_search(run_id, _block_checked=True)

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
        _refresh_run_config()

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
                resume_from_run_id, frozen_facts, execution_config):
            start_gate.wait()
            if not abort_start.is_set():
                _run_ai_screen_task(
                    task_id,
                    frozen_filters,
                    frozen_profile,
                    source_task_id,
                    resume_from_run_id,
                    frozen_facts,
                    execution_config=execution_config,
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
                # 高级设置续跑生效：优先使用本轮刷新后的配置，而非父抓取 run 的旧冻结值
                refreshed_config,
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

    @app.route("/api/task/pause/<run_id>", methods=["POST"])
    def api_task_pause(run_id: str):
        """013：安全暂停 AI 筛选任务。

        设置内存任务 stop_mode="pause" 并触发停止信号；worker 在安全边界
        落库后把 DB run 写为 paused，并生成 04 可查看的部分结果快照。
        """
        with _pipeline_lock:
            task = _pipeline_tasks.get(run_id)
            if task is None:
                return jsonify({
                    "ok": False, "error": "run_not_found",
                    "message": _MSG_TASK_NOT_FOUND,
                }), 404
            if task.get("kind") not in ("ai_screen", "recrawl"):
                return jsonify({
                    "ok": False, "error": "not_pausable_task",
                    "message": "只有 AI 筛选或重抓任务可以暂停",
                }), 409
            if task["status"] not in ("queued", "running"):
                return jsonify({
                    "ok": False, "error": "task_not_active",
                    "message": f"任务当前状态（{task['status']}）不能暂停",
                }), 409
            run = store.get_screening_run(run_id)
            if run is not None and run.get("status") not in ("queued", "running"):
                return jsonify({
                    "ok": False, "error": "task_not_active",
                    "message": f"任务当前状态（{run.get('status')}）不能暂停",
                }), 409
            stop_event = task.get("stop_event")
            if stop_event is None:
                return jsonify({
                    "ok": False, "error": "stop_signal_unavailable",
                    "message": "任务缺少停止信号，无法暂停",
                }), 409
            task["stop_mode"] = "pause"
            stop_event.set()
        return jsonify({"ok": True, "run_id": run_id, "status": "pausing"})

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
                    store.update_screening_run(
                        run_id, status="cancelled",
                        error_code="user_cancelled",
                        error_reason="用户已取消",
                    )
                    store.save_interruption_kind(run_id, "user_cancelled")
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
                            current["status"] = _public_task_status(
                                latest["status"], latest.get("interruption_kind"))
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
                    _public_task_status(run["status"], run.get("interruption_kind"))
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
                _public_task_status(run["status"], run.get("interruption_kind")) if run is not None else "cancelled"
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
                "status": _public_task_status(run["status"], run.get("interruption_kind")),
                "message": "任务已完成，无需结束保存",
            }), 409
        allowed_finish_statuses = {
            "queued", "running", "paused", "failed", "interrupted",
        }
        if run["status"] not in allowed_finish_statuses:
            return jsonify({
                "ok": False, "error": "not_paused",
                "status": _public_task_status(run["status"], run.get("interruption_kind")),
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
        # 先落“用户正在收尾”标记：worker 即使抢先写 cancelled，也保留 operator_stop，
        # 不会变成 finish 无法收尾的空 kind 中断。
        try:
            store.save_interruption_kind(run_id, "operator_stop")
        except _OPERATIONAL_ERRORS:
            pass
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
        profile_facts = params.get("profile_facts")
        if not isinstance(profile_facts, dict) or not profile_facts:
            profile_facts = None
            if scrape_task_id:
                try:
                    parent_run = store.get_screening_run(scrape_task_id)
                except _OPERATIONAL_ERRORS:
                    parent_run = None
                parent_facts = ((parent_run or {}).get("execution_params") or {}).get("profile_facts")
                if isinstance(parent_facts, dict) and parent_facts:
                    profile_facts = parent_facts
            if profile_facts is None and source_run_id:
                if source_payload is None:
                    source_payload = store.load_latest_pipeline_result(source_run_id)
                source_facts = ((source_payload or {}).get("result") or {}).get("profile_facts")
                if isinstance(source_facts, dict) and source_facts:
                    profile_facts = source_facts
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
        result = _build_partial_pipeline_result(
            source_jobs, verdicts, pending_rows, jd_map,
            profile_summary,
            source_dropped=source_dropped,
            total_scraped=source_total_scraped,
            platform=platform,
            profile_facts=profile_facts,
        )
        from webui.screen_flow import build_round_script_params
        from webui.result_rounds import save_finished_round
        snapshot_run_id = save_finished_round(
            store,
            result,
            build_round_script_params(store, run, run.get("frozen_filters") or {}, platform),
            scrape_task_id=parent_scrape_task_id,
            status="partial",
            execution_config=params.get("execution_config") or {},
            platform=platform,
            profile_summary=profile_summary,
            profile_facts=profile_facts,
            started_at=run.get("started_at"),
            finished_at=int(time.time() * 1000),
        )
        # 快照先落库，再原子标记 user_finished：保存失败时任务仍可重试，
        # 不会留下“已结束但无结果”的死状态；worker 已收到停止信号。
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
