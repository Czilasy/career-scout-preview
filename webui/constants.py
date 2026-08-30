"""Centralized constants shared across webui backend modules.

数值字面量、共享消息文案与可恢复错误元组在此单一来源化（031 B3：共享
常量家，`api → service → store` 各层只 import 不再自定义）。已命名的
类/模块常量（如 ``_REUSE_FRESHNESS_HOURS``、``MAX_DETAIL_BUDGET``）不在
此重复。
"""

from __future__ import annotations

import pathlib
import sqlite3

from webui import ai as _ai_service

# --- 路径锚点（app.py 直跑引导仍自带同值副本；api 模块一律从这里取） ---
WEBUI_ROOT = pathlib.Path(__file__).resolve().parent
PROJECT_ROOT = WEBUI_ROOT.parent
FRONTEND_DIST = WEBUI_ROOT / "dist"

# cleanup_expired_jobs / preview_cleanup_expired_jobs default retention window (days)
CLEANUP_EXPIRED_DAYS = 30

# Default detail-page scrape budget (number of jobs to fetch details for)
DETAIL_BUDGET = 60

# Feedback count interval that triggers AI settings refresh
FEEDBACK_THRESHOLD = 5

# Upper bound for list pagination endpoints
LIST_LIMIT = 100

# Number of tail log lines returned in task snapshots
LOG_TAIL_LINES = 50

# --- 共享消息文案（031 B3 归一；任务不存在合并两份漂移定义为信息更全文案） ---
_MSG_USER_FINISHED = "用户已结束任务"
_MSG_UNSUPPORTED_PLATFORM = "不支持的招聘平台"
_MSG_BOSS_LOGIN_STATUS = "BOSS 登录状态"
_MSG_TASK_NOT_FOUND = "任务不存在或已被移除"
_MSG_TASK_ALREADY_RUNNING = "该任务正在继续，请勿重复点击"
_MSG_ACCOUNT_NOT_FOUND = "账号不存在"
_MSG_EXPERIMENT_NOT_FOUND = "实验不存在"
_MSG_MANIFEST_NOT_FOUND = "任务单不存在"
_MSG_USER_STOPPED_SCRAPE = "用户已停止抓取"
_MSG_USER_STOPPED_SCREEN = "用户已停止筛选"
_MSG_PROFILE_NOT_FOUND = "画像不存在"
_MSG_PROFILE_ID_REQUIRED = "profile_id 不能为空"

# 平台域 token（pipeline_jobs_api 等共享）
_ZHILIAN_HOST_TOKEN = "zhaopin.com"

# 可恢复运维错误：业务层统一据此判定"留痕后友好返回"而非致命失败
_OPERATIONAL_ERRORS = (
    OSError,
    sqlite3.Error,
    RuntimeError,
    ValueError,
    KeyError,
    TypeError,
    _ai_service.AISecurityError,
)

# 反馈域冻结契约：error_code -> HTTP 状态（job_feedback_api 按此别名引用）
_FEEDBACK_ERROR_STATUS = {
    "invalid_action": 400,
    "invalid_action_payload": 400,
    "invalid_limit": 400,
    "invalid_request": 400,
    "profile_not_found": 404,
    "job_not_found": 404,
    "not_found": 404,
    "profile_job_not_found": 404,
    "idempotency_conflict": 409,
    "job_identity_conflict": 409,
    "state_precondition_failed": 409,
    "reminder_not_eligible": 409,
    "job_identity_incomplete": 422,
    "platform_url_mismatch": 422,
    "applied_at_required": 422,
    "applied_at_invalid": 422,
    "applied_at_in_future": 422,
    "follow_up_before_application": 422,
}
