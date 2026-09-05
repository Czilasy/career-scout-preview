"""任务状态映射与共享纯助手（021 B6 T019 外迁自 webui/app.py 模块级）。

公共任务状态口径、阶段→pipeline 类型映射、活跃时长口径、run scope
解析、暂停 run 配置刷新、legacy PATCH 连接代理、pipeline 身份载荷、
阶段进度权重。纯函数/纯类，无 app 依赖；webui.app 以 re-export 保持
既有 import 路径兼容。
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from flask import jsonify

from webui.constants import _FEEDBACK_ERROR_STATUS, _OPERATIONAL_ERRORS

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


def _public_status_for_integrity(
    integrity: dict | None, fallback: str = "", interruption_kind: str | None = None,
) -> str:
    """Map the canonical whitebox conclusion without re-evaluating evidence."""
    lifecycle = str(fallback or "")
    if lifecycle in {"failed", "cancelled", "interrupted"}:
        # A completeness projection must not replace a more authoritative
        # lifecycle terminal state with a pending/unverifiable label.
        return _public_task_status(lifecycle, interruption_kind)
    conclusion = str((integrity or {}).get("conclusion") or "")
    if conclusion == "interrupted":
        if interruption_kind in {"user_finished", "user_cancelled"}:
            return "cancelled"
        return "interrupted"
    return {
        "succeeded": "completed", "empty": "completed", "partial": "completed_with_pending",
        "failed": "failed", "unverifiable": "completed_with_pending",
    }.get(conclusion, _public_task_status(fallback))


def _pipeline_kind_for_stage(stage: str) -> str:
    """把 screening run 阶段映射为对外 pipeline kind。"""
    if str(stage).startswith("recrawl_"):
        return "recrawl"
    if str(stage) == "scrape":
        return "scrape"
    return "ai_screen"


def _active_elapsed_ms(started_at_ms, finished_at_ms, events):
    from webui.task_runners import _iso_epoch_ms  # 延迟：避免与 webui.task_runners 循环
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
