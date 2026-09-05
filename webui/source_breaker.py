"""Source 域公共契约与熔断器（021 拆分自 webui/source.py）。

承载平台无关的 JobSource 契约件：SourceOutcome（类型化抓取结果）、
PageEventPersistenceError、SourceCircuitBreaker（源阻断熔断器）与
JobSource Protocol。平台 adapter 见 source_boss_cdp / source_zhilian_cdp
/ source_fake；本模块不得依赖任何平台 adapter。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Typed source outcomes
# ---------------------------------------------------------------------------

_UNSET_JOBS = object()


def _valid_empty_evidence(value: object) -> bool:
    """Return whether a source supplied the frozen empty-state marker.

    ``empty_result`` is a claim about the searched scope, not merely an empty
    Python list.  Keep the marker check in one place so both the constructor
    and the convenience factories enforce the same contract.
    """
    return isinstance(value, dict) and all(
        bool(str(value.get(key) or "").strip())
        for key in ("kind", "fixture_version", "marker")
    )

class PageEventPersistenceError(RuntimeError):
    """编排层页级快照持久化失败时抛出，source 不得吞掉。"""



class SourceOutcome:
    """Typed result from a list/detail fetch.

    Attributes:
        ok: whether the fetch produced usable data
        jobs: list of job dicts (list fetch) or single detail dict (detail fetch)
        detail: detail payload when fetching detail
        empty_result: only for list fetch; True when the search genuinely
            returned zero jobs with explicit empty-state evidence. ``ok=True``
            and ``jobs=[]`` is only allowed when ``empty_result=True``.
        empty_evidence: required when ``empty_result=True``; must contain
            ``kind``, ``fixture_version`` and ``marker`` (脱敏). Must be
            ``None`` for non-empty success and all failures.
        failed_code: safe failure code when ``ok`` is False; one of
            ``source_unreachable`` / ``source_blocked`` / ``source_not_found``
            / ``source_invalid_output`` / ``source_input_drift``.
        safe_log: safe log line (counts/ids only, no PII/JD body)
    """

    __slots__ = (
        "detail",
        "empty_evidence",
        "empty_result",
        "failed_code",
        "failed_reason",
        "input_hash",
        "jobs",
        "ok",
        "scope_complete",
        "source_exhausted",
        "stop_reason",
        "page_evidence",
        "degraded",
        "quality_counts",
        "safe_log",
    )

    def __init__(
        self,
        *,
        ok: bool,
        jobs: list[dict] | None | object = _UNSET_JOBS,
        detail: dict | None = None,
        empty_result: bool = False,
        empty_evidence: dict | None = None,
        failed_code: str | None = None,
        safe_log: str = "",
        input_hash: str | None = None,
        failed_reason: str = "",
        scope_complete: bool | None = None,
        source_exhausted: bool | None = None,
        stop_reason: str | None = None,
        page_evidence: list[dict] | None = None,
        degraded: bool = False,
        quality_counts: dict | None = None,
    ):
        self.ok = bool(ok)
        explicit_jobs = jobs is not _UNSET_JOBS
        if self.ok and (bool(empty_result)
                        or (explicit_jobs and isinstance(jobs, list) and not jobs)):
            has_empty_evidence = bool(
                explicit_jobs and isinstance(jobs, list) and not jobs
                and empty_result and _valid_empty_evidence(empty_evidence)
                and scope_complete is True
            )
            if not has_empty_evidence:
                self.ok = False
                failed_code = failed_code or "source_invalid_output"
                failed_reason = failed_reason or "空结果证据缺失，不得返回成功"
        self.jobs = [] if jobs is _UNSET_JOBS or jobs is None else list(jobs)
        self.detail = detail or {}
        self.empty_result = bool(empty_result)
        self.empty_evidence = empty_evidence
        self.failed_code = failed_code
        self.safe_log = safe_log
        self.input_hash = input_hash
        self.failed_reason = failed_reason
        self.scope_complete = scope_complete
        self.source_exhausted = source_exhausted
        self.stop_reason = stop_reason
        self.page_evidence = list(page_evidence or [])
        self.degraded = bool(degraded)
        self.quality_counts = dict(quality_counts or {})

    @classmethod
    def success(cls, *, jobs: list[dict] | None | object = _UNSET_JOBS, detail: dict | None = None,
                safe_log: str = "", input_hash: str | None = None, **evidence) -> SourceOutcome:
        # An explicitly supplied empty list must be an explicit empty result;
        # omitted jobs remains valid for preflight/detail outcomes.
        if jobs is not _UNSET_JOBS and not jobs:
            empty = (evidence.get("empty_result")
                     and _valid_empty_evidence(evidence.get("empty_evidence"))
                     and evidence.get("scope_complete") is True)
            if not empty:
                return cls.failure(failed_code="source_invalid_output",
                                   safe_log="empty_evidence_missing",
                                   failed_reason="空结果证据缺失，不得返回成功")
        return cls(ok=True, jobs=jobs, detail=detail, safe_log=safe_log,
                   input_hash=input_hash, **evidence)

    @classmethod
    def empty_success(cls, *, empty_evidence: dict, safe_log: str = "", input_hash: str | None = None,
                       **evidence) -> SourceOutcome:
        """真实空结果：须有空状态、范围完成证据与 jobs=[] 才能 ok=True。

        empty_evidence 必须包含 ``kind``、``fixture_version`` 和 ``marker``；
        只含脱敏标记，不含页面正文、Cookie、JD 或本地路径。
        """
        if not isinstance(empty_evidence, dict) or not empty_evidence:
            raise ValueError("empty_evidence 必须为非空 dict")
        for key in ("kind", "fixture_version", "marker"):
            if not empty_evidence.get(key):
                raise ValueError(f"empty_evidence 缺少必填字段: {key}")
        if evidence.get("scope_complete") is not True:
            return cls.failure(
                failed_code="source_invalid_output",
                safe_log="empty_scope_evidence_missing",
                failed_reason="空结果缺少范围完成证据，不得返回成功",
            )
        return cls(
            ok=True, jobs=[], empty_result=True, empty_evidence=empty_evidence,
            safe_log=safe_log, input_hash=input_hash, **evidence,
        )

    @classmethod
    def failure(cls, *, failed_code: str, safe_log: str = "", failed_reason: str = "") -> SourceOutcome:
        return cls(ok=False, failed_code=failed_code, safe_log=safe_log,
                    failed_reason=failed_reason)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "job_count": len(self.jobs),
            "has_detail": bool(self.detail),
            "empty_result": self.empty_result,
            "empty_evidence": self.empty_evidence,
            "failed_code": self.failed_code,
            "safe_log": self.safe_log,
            "failed_reason": self.failed_reason,
            "scope_complete": self.scope_complete,
            "source_exhausted": self.source_exhausted,
            "stop_reason": self.stop_reason,
            "page_evidence": list(self.page_evidence),
            "degraded": self.degraded,
            "quality_counts": dict(self.quality_counts),
        }




# ---------------------------------------------------------------------------
# T075: Source circuit breaker (state-machine.md L92-107)
# ---------------------------------------------------------------------------


class SourceCircuitBreaker:
    """Stateful circuit breaker for source-blocking signals.

    Contract (state machine, L92-107):

    - Opens after two consecutive source signals from: login wall,
      verification page, explicit rate-limit response, or repeated
      invalid navigation shell attributable to source blocking.
    - When open: no new source job starts; queued work stays
      retryable/blocked rather than failed as user fault.
    - Automatic restart requires preflight success AND bounded cooldown;
      no unbounded retry loop.

    The breaker is a pure state machine. It does NOT invoke preflight
    itself; the orchestrator calls ``try_reset`` with the preflight
    outcome after the cooldown has elapsed.
    """

    # The four source-blocking signal kinds. Other safe failure codes
    # (input_drift, invalid_output, timeout, ...) are user/system faults
    # and do NOT advance the breaker counter.
    SIGNAL_CODES = frozenset({
        "source_login_required",
        "source_verification_required",
        "source_rate_limited",
        "source_blocked",
    })

    DEFAULT_COOLDOWN_SECONDS = 60

    def __init__(
        self,
        *,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] | None = None,
    ):
        self._cooldown = max(1, int(cooldown_seconds))
        self._clock = clock or time.monotonic
        self._consecutive = 0
        self._last_signal: str | None = None
        self._opened_at: float | None = None
        self._cooldown_until: float | None = None

    def record_signal(self, safe_code: str) -> None:
        """Record a source-blocking signal. Opens the breaker at >=2 consecutive."""
        if safe_code not in self.SIGNAL_CODES:
            return  # non-signal codes do not advance the counter
        self._consecutive += 1
        self._last_signal = safe_code
        if self._consecutive >= 2 and self._opened_at is None:
            now = self._clock()
            self._opened_at = now
            self._cooldown_until = now + self._cooldown

    def record_success(self) -> None:
        """Record a successful source fetch. Resets the consecutive counter.

        Does NOT close an already-open breaker; only ``try_reset`` can close
        it (after cooldown + preflight success).
        """
        self._consecutive = 0
        self._last_signal = None

    def is_open(self) -> bool:
        """True if the breaker is open (no new source job should start)."""
        return self._opened_at is not None

    def open_failure_code(self) -> str:
        """开闸期间对外失败码：透传 ``last_signal``（登录报登录、风控报风控），
        信号缺失（防御路径）回落 ``source_blocked``。"""
        signal = self._last_signal
        if signal in self.SIGNAL_CODES:
            return signal
        return "source_blocked"

    def cooldown_elapsed(self, *, now: float | None = None) -> bool:
        """True if open AND the cooldown period has elapsed (probe allowed)."""
        if self._opened_at is None or self._cooldown_until is None:
            return False
        current = now if now is not None else self._clock()
        return current >= self._cooldown_until

    def try_reset(self, preflight_ok: bool, *, now: float | None = None) -> bool:
        """Attempt to close the breaker.

        Returns True iff both conditions hold: cooldown has elapsed AND
        ``preflight_ok`` is True. On success, resets all breaker state.
        Returns False otherwise (breaker stays open).
        """
        if self._opened_at is None:
            return True  # already closed
        if not preflight_ok:
            return False
        current = now if now is not None else self._clock()
        if self._cooldown_until is None or current < self._cooldown_until:
            return False
        self._consecutive = 0
        self._last_signal = None
        self._opened_at = None
        self._cooldown_until = None
        return True

    def state(self) -> dict:
        """Return a queryable snapshot of breaker state."""
        return {
            "open": self._opened_at is not None,
            "consecutive": self._consecutive,
            "last_signal": self._last_signal,
            "opened_at": self._opened_at,
            "cooldown_until": self._cooldown_until,
        }


# ---------------------------------------------------------------------------
# JobSource Protocol（contracts/job-source.md）
# ---------------------------------------------------------------------------


@runtime_checkable
class JobSource(Protocol):
    """平台无关的 JobSource 契约（contracts/job-source.md）。

    所有平台 adapter（BossCdpSource、ZhilianCdpSource、FakeJobSource）
    必须结构化符合本 Protocol。adapter 只负责平台访问和字段归一化，
    不持久化业务数据、不推进 run 状态、不执行 AI 筛选。

    列表搜索输入只包含关键词、城市解析快照和页数；薪资、经验、学历、
    行业、公司规模、融资阶段或公司性质均不得进入 adapter 列表参数。
    """

    #: 平台键，必须与平台注册表（webui/platforms.py）注册键一致。
    platform: str

    def preflight(self) -> SourceOutcome:
        """检查冻结 CDP 端口、profile、登录态和平台可访问性。

        每个新运行及每次继续运行前执行一次。任一条件失败时返回
        错误矩阵中的单一确定结果，不允许"失败或暂停"二选一语义。
        """
        ...

    def fetch_list(
        self, plan_item: dict, *, on_page_completed: Callable[[dict], None] | None = None,
    ) -> SourceOutcome:
        """抓取一个搜索组合的岗位列表页。

        plan_item 必须包含 platform、keyword、city（含平台码和映射版本）、
        target_pages、input_hash 和 list_output_path。输入不得出现
        source_filters、AI 筛选字段、融资阶段或公司性质。
        on_page_completed：每完成一页回调结构化页级事件（含岗位快照）。"""
        ...

    def fetch_detail(
        self, job: dict, *, detail_output_path: str | None = None,
    ) -> SourceOutcome:
        """抓取单个岗位详情页。

        输入必须含 platform、platform_job_id 和经过平台注册规则规范化
        的 canonical_url。详情缺失、下架、单项解析失败和平台级阻断
        按错误矩阵区分。
        """
        ...

    def fetch_details_batch(
        self, jobs: list[dict], *, detail_output_path: str | None = None,
        **bounded_options,
    ) -> dict[str, SourceOutcome]:
        """批量抓取岗位详情页。

        返回映射键为输入 platform_job_id，每个输入恰有一个终态 outcome。
        单岗位失败不抛出到批次外；平台级 signal 进入熔断器。每个
        subprocess 显式携带冻结 CDP 端口。
        """
        ...


# ---------------------------------------------------------------------------
# JobSource adapter for the BOSS CDP scraper
# ---------------------------------------------------------------------------
