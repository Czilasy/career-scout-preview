"""Store 域共享常量与契约异常（021 B2 拆分自 webui/store.py）。

SQL 片段、状态机集合（run/task/query/feedback/profile 等）、
Latest-Result 过滤器与 DiscoveryStoreConflictError。被 webui/store.py
（门面组装）与全部 store_* mixin 共享；不得 import 任何 store 实现模块。
"""

from __future__ import annotations


_BEGIN_IMMEDIATE = "BEGIN IMMEDIATE"
_SHA256_PREFIX = "sha256:"
_STATUS_SET_CLAUSE = "status = ?"
_ERROR_CODE_SET_CLAUSE = "error_code = ?"
_UPDATED_AT_SET_CLAUSE = "updated_at = ?"
_SQL_MAX_SCHEMA_VERSION = "SELECT MAX(version) AS v FROM schema_migrations"
_SQL_DELETE_EXPIRED_RECOVERY_LOCK = "DELETE FROM recovery_lock WHERE expires_at <= ?"
_SQL_TUNING_EXPERIMENT_STATUS = "SELECT status FROM tuning_experiments WHERE id = ?"
_SQL_TUNING_MANIFEST = "SELECT * FROM tuning_task_manifests WHERE id = ?"
# 017-US5: "最新结果"判定唯一口径（FR-013）——全局与按平台共用同一过滤。
_LATEST_RESULT_VISIBLE_STATUSES = "('done', 'partial', 'scraped_only')"
_LATEST_RESULT_FILTER = (
    f"status IN {_LATEST_RESULT_VISIBLE_STATUSES} "
    "AND record_kind = 'result_snapshot' AND archived_at IS NULL"
)


class DiscoveryStoreConflictError(Exception):
    """Raised when a CAS-guarded store update detects a state conflict."""


# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------

ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"succeeded", "failed", "interrupted", "partial"}
ALLOWED_TRANSITIONS = {
    "queued": {"running", "failed", "interrupted"},
    "running": {"succeeded", "failed", "interrupted", "partial"},
    "succeeded": set(),
    "failed": set(),
    "interrupted": set(),
    "partial": set(),
}

RUN_STATUSES = {"queued", "running", "succeeded", "partial", "failed", "interrupted", "paused"}
RUN_TRANSITIONS = {
    # 未开始的任务不能伪造暂停现场；必须先进入 running 再因真实阻断暂停。
    "queued": {"running", "succeeded", "partial", "failed", "interrupted"},
    "running": {"succeeded", "partial", "failed", "interrupted", "paused"},
    "paused": {"running", "failed", "interrupted"},
    "succeeded": set(),
    "partial": set(),
    "failed": set(),
    "interrupted": set(),
}

# 统一任务状态机（FR-005）：语义清晰的状态名，与 RUN_STATUSES 映射
TASK_STATUSES = {
    "waiting",              # = queued
    "running",              # = running
    "paused",               # = paused（系统性阻断）
    "completed",            # = succeeded（无待确认）
    "completed_with_pending",  # = partial（有待确认）
    "failed",               # = failed
    "cancelled",            # = interrupted（用户取消）
}

# 统一状态名 → DB 状态名映射
TASK_TO_RUN_STATUS = {
    "waiting": "queued",
    "running": "running",
    "paused": "paused",
    "completed": "succeeded",
    "completed_with_pending": "partial",
    "failed": "failed",
    "cancelled": "interrupted",
}
RUN_TO_TASK_STATUS = {v: k for k, v in TASK_TO_RUN_STATUS.items()}


QUERY_STATUSES = {"queued", "running", "succeeded", "failed", "interrupted"}
FEEDBACK_ACTIONS = {"interested", "not_interested"}
FEEDBACK_REASONS = {"role", "salary", "location", "company", None}
PROFILE_JOB_STATUSES = {"new", "interested", "read", "applied", "stale", "deleted"}
AI_STATUS_VALUES = {"unconfigured", "testing", "ready", "failed"}
RESUME_FORMATS = {"txt", "pdf", "docx"}
MAX_DETAIL_BUDGET = 60

# 033 V2 whitebox value domains.  Keep these in the store domain so API,
# services and projections share one vocabulary without importing storage
# implementation modules.
WHITEBOX_CONCLUSIONS = frozenset({
    "succeeded", "empty", "partial", "failed", "unverifiable", "interrupted",
})
WHITEBOX_UNIT_STATUSES = frozenset({
    "planned", "running", "succeeded", "empty", "failed", "incomplete",
    "skipped", "unverifiable", "interrupted",
})
WHITEBOX_STOP_REASONS = frozenset({
    "target_reached", "source_exhausted", "explicit_empty", "cancelled", "paused",
    "hard_block", "soft_failure", "browser_lost", "persistence_failed", "unknown",
})
WHITEBOX_CONCLUSION_LABELS = {
    "succeeded": "完整成功",
    "empty": "空结果",
    "partial": "部分完成",
    "failed": "执行失败",
    "unverifiable": "无法确认",
    "interrupted": "任务已中断",
}
