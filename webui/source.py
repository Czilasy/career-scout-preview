"""Job source adapter (feature 004).

Wraps the BOSS CDP raw scraper into a typed JobSource interface. Returns
SourceOutcome values with typed failures; never leaks raw exception text,
credentials or resume content. Validates input_hash before importing any
artifact so resume/import is rejected on plan item drift.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from scripts import boss_cdp_raw as boss
from webui.process_executor import ScraperExecutor, run_with_deadline
from webui.workbench import normalize_job_link
from webui.error_registry import SAFE_FAILURE_CODES
from webui.runtime_audit import record_runtime_event

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
SCRAPER = PROJECT_ROOT / "scripts" / "boss_cdp_raw.py"

# Valid filter fields passable to the scraper CLI (excludes city which is positional).
SCRAPER_FILTER_FIELDS = ("salary", "experience", "degree", "industry", "scale", "stage")
SCRAPER_FILTER_FIELDS = ("salary", "experience", "degree", "industry", "scale", "stage", "multiBusinessDistrict")

# BOSS 预检真实探测第一次被判 restricted 时，等待该时长后重试一次。
PREFLIGHT_RETRY_DELAY_SECONDS = 10.0

# The scraper loads optional dependencies lazily for its CLI.  The web adapter
# needs requests available before tests and preflight can patch/use it.
boss.require_runtime_dependencies("requests")


# ---------------------------------------------------------------------------
# Typed source outcomes
# ---------------------------------------------------------------------------

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
        "safe_log",
    )

    def __init__(
        self,
        *,
        ok: bool,
        jobs: list[dict] | None = None,
        detail: dict | None = None,
        empty_result: bool = False,
        empty_evidence: dict | None = None,
        failed_code: str | None = None,
        safe_log: str = "",
        input_hash: str | None = None,
        failed_reason: str = "",
    ):
        self.ok = bool(ok)
        self.jobs = jobs or []
        self.detail = detail or {}
        self.empty_result = bool(empty_result)
        self.empty_evidence = empty_evidence
        self.failed_code = failed_code
        self.safe_log = safe_log
        self.input_hash = input_hash
        self.failed_reason = failed_reason

    @classmethod
    def success(cls, *, jobs: list[dict] | None = None, detail: dict | None = None, safe_log: str = "", input_hash: str | None = None) -> SourceOutcome:
        return cls(ok=True, jobs=jobs, detail=detail, safe_log=safe_log, input_hash=input_hash)

    @classmethod
    def empty_success(cls, *, empty_evidence: dict, safe_log: str = "", input_hash: str | None = None) -> SourceOutcome:
        """真实空结果：ok=True, jobs=[], empty_result=True, empty_evidence 必填。

        empty_evidence 必须包含 ``kind``、``fixture_version`` 和 ``marker``；
        只含脱敏标记，不含页面正文、Cookie、JD 或本地路径。
        """
        if not isinstance(empty_evidence, dict) or not empty_evidence:
            raise ValueError("empty_evidence 必须为非空 dict")
        for key in ("kind", "fixture_version", "marker"):
            if not empty_evidence.get(key):
                raise ValueError(f"empty_evidence 缺少必填字段: {key}")
        return cls(
            ok=True, jobs=[], empty_result=True, empty_evidence=empty_evidence,
            safe_log=safe_log, input_hash=input_hash,
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


class BossCdpSource:
    """Adapter that invokes ``scripts/boss_cdp_raw.py`` as a subprocess.

    The adapter is responsible for:
      - Building the command line from a plan_item / job
      - Running the subprocess with UTF-8 stdout capture
      - Validating the produced artifact's ``input_hash`` against the plan
      - Returning a typed SourceOutcome with safe log lines only

    The adapter never persists anything to the store; persistence is the
    orchestrator's responsibility. The adapter never logs JD body, resume
    text, or credentials. Failures from one query never raise into the
    orchestrator; they are returned as ``SourceOutcome.failure(...)``.
    """

    #: 平台键，与平台注册表注册键一致（contracts/job-source.md）。
    platform: str = "boss"

    def __init__(
        self,
        *,
        python_executable: str | None = None,
        cwd: Path | None = None,
        scraper_path: Path | None = None,
        env: dict | None = None,
        timeout_seconds: int = 600,
        runner: Callable[..., subprocess.Popen] | None = None,
        executor: ScraperExecutor | None = None,
        cancel_event=None,
        artifact_root: Path | str | None = None,
        max_artifact_bytes: int = 20_000_000,
        cdp_port: int = boss.DEFAULT_CDP_PORT,
        breaker: SourceCircuitBreaker | None = None,
        browser_account: str | None = None,
        in_process: bool = False,
        run_id: str = "",
    ):
        self.python_executable = python_executable or sys.executable or "python"
        self.cwd = Path(cwd) if cwd else PROJECT_ROOT
        self.scraper_path = Path(scraper_path) if scraper_path else SCRAPER
        self.env = env or {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", **os.environ}
        self.timeout_seconds = int(timeout_seconds)
        self._executor = executor or ScraperExecutor()
        self.cancel_event = cancel_event
        self.artifact_root = Path(artifact_root).resolve() if artifact_root else None
        self.max_artifact_bytes = max(1, int(max_artifact_bytes))
        self.cdp_port = int(cdp_port)
        self.breaker = breaker or SourceCircuitBreaker()
        # 登录态缓存 / 风控冷却的账号维度；None 表示不记录（CLI 直连场景）
        self.browser_account = (
            str(browser_account).strip() if browser_account else None
        )
        self.run_id = str(run_id or "").strip()
        if self.run_id and "CAREER_SCOUT_CORRELATION_ID" not in self.env:
            self.env = {**self.env, "CAREER_SCOUT_CORRELATION_ID": self.run_id}
        # in_process 模式（合同 inprocess-runner §4.3）：True 时内部执行器把
        # 本类构建的 argv 翻译为 run_search_programmatic / scrape_details
        # 库式调用，不 spawn 子进程；其余行为零改动。
        self.in_process = bool(in_process)
        self._runner = runner or self._default_run
        self._use_default_runner = runner is None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def preflight(self) -> SourceOutcome:
        """Check the dedicated Chrome connection and BOSS login once per run.

        登录探测走缓存优先（D3）：账号 × 平台 15 分钟 TTL 内命中直接复用，
        不反复触发搜索 API；CDP 连通性检查保持轻量每次都做。
        """
        cache_note = ""
        if boss.requests is None:
            return SourceOutcome.failure(
                failed_code="source_unreachable",
                safe_log="runtime_dependency_missing",
            )

        try:
            response = boss.requests.get(
                f"http://127.0.0.1:{self.cdp_port}/json/version",
                timeout=5,
            )
            if response.status_code != 200:
                return SourceOutcome.failure(
                    failed_code="source_cdp_unavailable",
                    safe_log=f"cdp_http_status={response.status_code}",
                )
            payload = response.json()
            if not isinstance(payload, dict) or not payload.get("Browser"):
                return SourceOutcome.failure(
                    failed_code="source_cdp_unavailable",
                    safe_log="cdp_response_invalid",
                )
        except (boss.requests.ConnectionError, boss.requests.Timeout):
            return SourceOutcome.failure(
                failed_code="source_cdp_unavailable",
                safe_log="cdp_port_unavailable",
            )
        except (TypeError, ValueError):
            return SourceOutcome.failure(
                failed_code="source_cdp_unavailable",
                safe_log="cdp_response_invalid",
            )

        if self.browser_account:
            from scripts.login_state_cache import read_cached_state
            cached = read_cached_state(self.browser_account, "boss")
            if cached == "logged_in":
                return SourceOutcome.success(safe_log="source_ready cache=hit")
            if cached == "restricted":
                return SourceOutcome.failure(
                    failed_code="source_blocked",
                    safe_log="boss_login_restricted cache=hit",
                )
            if cached == "not_logged_in":
                cache_note = " cache=not_logged_in_ignored"
            elif cached == "unknown":
                cache_note = " cache=unknown_ignored"

        state = boss.check_login_state_tri(self.cdp_port)
        if state == "unknown":
            state = boss.check_login_state_tri(self.cdp_port)
        retry_note = ""
        if state == "restricted":
            time.sleep(PREFLIGHT_RETRY_DELAY_SECONDS)
            retry_note = " retry=1"
            state = boss.check_login_state_tri(self.cdp_port)
            if state == "unknown":
                state = boss.check_login_state_tri(self.cdp_port)
        if self.browser_account:
            from scripts.login_state_cache import write_login_state
            write_login_state(self.browser_account, "boss", state)
        if state == "logged_in":
            return SourceOutcome.success(safe_log=f"source_ready{cache_note}{retry_note}")
        if state == "restricted":
            return SourceOutcome.failure(
                failed_code="source_blocked",
                safe_log=f"boss_login_restricted{cache_note}{retry_note}",
            )
        if state == "not_logged_in":
            return SourceOutcome.failure(
                failed_code="source_login_required",
                safe_log=f"boss_login_required{cache_note}{retry_note}",
            )
        return SourceOutcome.success(
            safe_log=f"boss_login_probe_unknown{retry_note or ' retry=1'} proceed=1",
        )

    def recheck_login(self) -> SourceOutcome:
        """运行中疑似登录失效时的独立复核探测（绕过登录态缓存）。

        与任务开始时 preflight 使用同一套 CDP 登录探测，但不复用 15 分钟
        缓存，保证拿到当时当刻的真实探测结果；探测失败只降级不加冷却。
        """
        state = boss.check_login_state_tri(self.cdp_port)
        if state == "unknown":
            state = boss.check_login_state_tri(self.cdp_port)
        retry_note = ""
        if state == "restricted":
            time.sleep(PREFLIGHT_RETRY_DELAY_SECONDS)
            retry_note = " retry=1"
            state = boss.check_login_state_tri(self.cdp_port)
            if state == "unknown":
                state = boss.check_login_state_tri(self.cdp_port)
        if state == "logged_in":
            return SourceOutcome.success(safe_log=f"recheck_logged_in{retry_note}")
        if state == "restricted":
            return SourceOutcome.failure(
                failed_code="source_blocked",
                safe_log=f"recheck_restricted{retry_note}",
            )
        if state == "not_logged_in":
            return SourceOutcome.failure(
                failed_code="source_login_required",
                safe_log="recheck_not_logged_in",
            )
        return SourceOutcome.success(
            safe_log=f"recheck_unknown{retry_note} proceed=1",
        )

    def fetch_list(
        self, plan_item: dict, *, on_page_completed: Callable[[dict], None] | None = None,
    ) -> SourceOutcome:
        """Fetch a job list for one search plan item.

        ``plan_item`` must contain ``keyword``, ``city``, ``source_filters``,
        ``input_hash``, and optionally ``target_pages``.
        ``on_page_completed``：每完成一页回调结构化页级事件（含岗位快照）。"""
        if not isinstance(plan_item, dict):
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log="plan_item_not_dict",
            )
        keyword = str(plan_item.get("keyword") or "").strip()
        city = str(plan_item.get("city") or "").strip()
        expected_hash = str(plan_item.get("input_hash") or "")
        if not keyword or not expected_hash:
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log=f"plan_item_missing_fields keyword={bool(keyword)} hash={bool(expected_hash)}",
            )
        target_pages = max(1, int(plan_item.get("target_pages") or 1))
        start_page = max(1, int(plan_item.get("start_page") or 1))
        raw_filters = plan_item.get("source_filters") or {}
        if not isinstance(raw_filters, dict):
            raw_filters = {}
        source_filters = {
            key: raw_filters[key] for key in SCRAPER_FILTER_FIELDS
            if raw_filters.get(key) not in (None, "", [])
        }
        output_path = plan_item.get("list_output_path") or plan_item.get("_list_output_path")
        if not output_path:
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log="plan_item_missing_list_output_path",
            )
        if not self._artifact_path_allowed(output_path):
            return SourceOutcome.failure(
                failed_code="source_invalid_output", safe_log="list_output_path_invalid",
            )
        combo_key = str(plan_item.get("combo_key") or "") or f"{keyword}|{city}"
        page_events_path = f"{output_path!s}.page-events.jsonl"
        try:
            Path(page_events_path).parent.mkdir(parents=True, exist_ok=True)
            Path(page_events_path).write_text("", encoding="utf-8")
        except OSError:
            page_events_path = ""

        seen_page_events = 0

        def _drain_page_events():
            nonlocal seen_page_events
            if not page_events_path or on_page_completed is None:
                return
            path = Path(page_events_path)
            if not path.is_file() or path.stat().st_size > self.max_artifact_bytes:
                return
            try:
                with path.open(encoding="utf-8") as handle:
                    lines = handle.readlines()
            except OSError:
                return
            for line in lines[seen_page_events:]:
                line = line.strip()
                if not line:
                    seen_page_events += 1
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    break  # 可能读到写入中的半行，下一轮再试
                if not isinstance(event, dict):
                    seen_page_events += 1
                    continue
                event["combo_key"] = combo_key
                jobs = self._read_jobs(str(output_path)) or []
                event["jobs_snapshot"] = [_normalize_job_fields(j) for j in jobs]
                on_page_completed(event)
                seen_page_events += 1

        command = self._build_list_command(
            keyword, city, target_pages, source_filters, str(output_path),
            combo_key=combo_key, list_events_output=page_events_path or None,
            start_page=start_page,
        )
        safe_log = f"list combo_key_present=1 keyword_present=1 city_present={bool(city)} pages={target_pages}"
        if self.breaker.is_open():
            return SourceOutcome.failure(
                failed_code="source_blocked",
                safe_log=f"{safe_log} breaker_open",
            )
        try:
            returncode, captured = self._run_command(
                command, self.timeout_seconds,
                on_poll=_drain_page_events if not self.in_process else None,
                on_page_completed=on_page_completed,
            )
        except subprocess.TimeoutExpired:
            return SourceOutcome.failure(failed_code="source_timeout", safe_log=f"{safe_log} timeout={self.timeout_seconds}")
        except FileNotFoundError:
            return SourceOutcome.failure(failed_code="source_unreachable", safe_log=f"{safe_log} scraper_not_found")
        except OSError as exc:
            return SourceOutcome.failure(failed_code="source_unreachable", safe_log=f"{safe_log} os_error_type={type(exc).__name__}")
        _drain_page_events()
        # 空批次判定：退出码 0 + 0 结果 + 0 事件 = 浏览器/CDP 失联，
        # 不降级成 source_invalid_output 或普通空成功。
        page_events = (
            self._read_events_file(page_events_path)
            if page_events_path else []
        )
        lost_empty_batch = not page_events
        if returncode != 0:
            failed_code = _classify_failed_code(returncode, captured)
            self.breaker.record_signal(failed_code)
            reason = _exit_reason(returncode, captured)
            _record_risk_signals(self.browser_account, self.platform, failed_code, captured,
                                run_id=self.run_id)
            return SourceOutcome.failure(
                failed_code=failed_code,
                safe_log=f"{safe_log} returncode={returncode} reason={reason}",
            )
        jobs = self._read_jobs(str(output_path))
        if jobs is None:
            if lost_empty_batch:
                self.breaker.record_signal("source_cdp_unavailable")
                return SourceOutcome.failure(
                    failed_code="source_cdp_unavailable",
                    safe_log=f"{safe_log} empty_batch_no_events_cdp_lost",
                )
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log=f"{safe_log} output_not_json",
            )
        if not isinstance(jobs, list):
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log=f"{safe_log} output_not_list",
            )
        actual_hash = _input_hash({
            "keyword": keyword,
            "city": city,
            "source_filters": source_filters,
            "target_pages": target_pages,
        })
        if expected_hash and actual_hash != expected_hash:
            return SourceOutcome.failure(
                failed_code="source_input_drift",
                safe_log=f"{safe_log} input_hash_mismatch expected_present={bool(expected_hash)}",
            )
        # T133: normalize BOSS-specific field names to the unified JobSource
        # interface. The scraper returns encrypt_job_id/job_link/boss_name
        # (matching BOSS API field names), but fetch_detail and downstream
        # code expect job_id/source_url/company. Without this normalization,
        # every job's source_url and job_id appear empty, causing fetch_detail
        # to fail with source_invalid_output and detail_count stays at 0.
        normalized_jobs = [_normalize_job_fields(j) for j in jobs]
        if lost_empty_batch and not normalized_jobs:
            self.breaker.record_signal("source_cdp_unavailable")
            return SourceOutcome.failure(
                failed_code="source_cdp_unavailable",
                safe_log=f"{safe_log} empty_batch_no_events_cdp_lost",
            )
        self.breaker.record_success()
        if normalized_jobs:
            _record_success_signal(self.browser_account, "boss")
        return SourceOutcome.success(
            jobs=normalized_jobs,
            safe_log=f"{safe_log} job_count={len(normalized_jobs)}",
            input_hash=actual_hash,
        )

    def fetch_detail(self, job: dict, *, detail_output_path: str | None = None) -> SourceOutcome:
        """Fetch the detail page for a single job.

        ``job`` must contain ``source_url`` (or ``url``) and ``job_id``.
        Detail fetch failure never raises; it returns a typed outcome the
        orchestrator can persist as a partial snapshot.
        """
        if not isinstance(job, dict):
            return SourceOutcome.failure(failed_code="source_invalid_output", safe_log="job_not_dict")
        source_url = normalize_job_link(job.get("source_url") or job.get("url") or "")
        job_id = str(job.get("job_id") or job.get("id") or "").strip()
        if not source_url or not job_id:
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log=f"job_missing_url_or_id job_id_present={bool(job_id)}",
            )
        detail_path = detail_output_path or job.get("_detail_output_path")
        if not detail_path:
            return SourceOutcome.failure(failed_code="source_invalid_output", safe_log="detail_path_missing")
        if not self._artifact_path_allowed(detail_path):
            return SourceOutcome.failure(
                failed_code="source_invalid_output", safe_log="detail_output_path_invalid",
            )
        detail_input_path = f"{detail_path}.input.json"
        detail_job = dict(job)
        detail_job.setdefault("job_link", source_url)
        detail_job.setdefault("job_id", job_id)
        detail_job.setdefault("boss_name", detail_job.get("company", ""))
        try:
            Path(detail_input_path).write_text(
                json.dumps({"jobs": [detail_job]}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            return SourceOutcome.failure(
                failed_code="source_unreachable",
                safe_log="detail_input_write_failed",
            )
        command = self._build_detail_command(detail_input_path, str(detail_path))
        safe_log = f"detail job_id_present=1 url_host_safe={_safe_host(source_url)}"
        if self.breaker.is_open():
            try:
                Path(detail_input_path).unlink(missing_ok=True)
            except OSError:
                pass
            return SourceOutcome.failure(
                failed_code="source_blocked",
                safe_log=f"{safe_log} breaker_open",
            )
        try:
            returncode, captured = self._run_command(command, self.timeout_seconds)
        except subprocess.TimeoutExpired:
            return SourceOutcome.failure(failed_code="source_timeout", safe_log=f"{safe_log} timeout={self.timeout_seconds}")
        except FileNotFoundError:
            return SourceOutcome.failure(failed_code="source_unreachable", safe_log=f"{safe_log} scraper_not_found")
        except OSError as exc:
            return SourceOutcome.failure(failed_code="source_unreachable", safe_log=f"{safe_log} os_error_type={type(exc).__name__}")
        finally:
            try:
                Path(detail_input_path).unlink(missing_ok=True)
            except OSError:
                pass
        if returncode != 0:
            failed_code = _classify_failed_code(returncode, captured)
            self.breaker.record_signal(failed_code)
            _record_risk_signals(self.browser_account, self.platform, failed_code, captured,
                                run_id=self.run_id)
            return SourceOutcome.failure(
                failed_code=failed_code,
                safe_log=f"{safe_log} returncode={returncode} stderr_tail_safe={_safe_tail(captured)}",
            )
        detail = self._read_detail(str(detail_path))
        if detail is None:
            return SourceOutcome.failure(failed_code="source_invalid_output", safe_log=f"{safe_log} detail_not_json")
        if not isinstance(detail, dict):
            return SourceOutcome.failure(failed_code="source_invalid_output", safe_log=f"{safe_log} detail_not_dict")
        self.breaker.record_success()
        return SourceOutcome.success(detail=detail, safe_log=f"{safe_log} fields={sorted(detail.keys())[:5]}")

    # ------------------------------------------------------------------
    # T071: Batched detail fetch (feature 005 US4)
    # ------------------------------------------------------------------

    # Fields a terminal safe event MUST NOT carry (privacy contract).
    # If any of these appear in an event, the event is rejected as invalid
    # because the producer contract forbids JD body, credentials and PII
    # from crossing the producer/consumer boundary.
    _EVENT_FORBIDDEN_FIELDS = frozenset({
        "jd", "jd_body", "description",
        "encrypt_job_id", "encryptJobId", "security_id", "securityId",
        "token", "secret", "api_key", "apikey",
        "prompt", "model_response", "raw_response",
        "resume_text", "resume_body",
        "phone", "email", "id_card",
    })

    _EVENT_REQUIRED_FIELDS = ("kind", "status", "job_id", "duration_ms", "safe_code")

    _EVENT_VALID_STATUSES = frozenset({"completed", "unavailable", "failed", "cancelled"})

    def fetch_details_batch(
        self,
        jobs: list[dict],
        *,
        detail_output_path: str,
        event_callback: Callable[[dict], None] | None = None,
        max_batch_size: int = 5,
        gap_min: float = 8,
        gap_max: float = 15,
        reset_every: int = 3,
        tab_pool_size: int = 5,
        on_item_done: Callable[[int], None] | None = None,
    ) -> dict[str, SourceOutcome]:
        """Fetch details for a batch of jobs (≤5) using one scraper subprocess.

        Returns a mapping of ``job_id -> SourceOutcome``. Each job's outcome
        is built from its atomic detail record (split from the combined
        output by ``job_link``) and the corresponding terminal safe event
        parsed from the events JSONL file.

        ``on_item_done``：仅用于与 ZhilianCdpSource 对齐签名。BOSS 走子进程
        tab 池整批抓取，条级回调由事件文件统一解析（event_callback），
        本参数在批返回时一次性按批内条数回调一次，不做批内逐条推进。

        Contract (state machine §Producer / Consumer Boundaries):

        - Each batch contains at most 5 selected candidates.
        - Each job emits exactly one terminal safe event:
          ``{kind:"detail", status, job_id, duration_ms, safe_code}``.
        - Events never carry JD body, credentials or PII; events containing
          any forbidden field are rejected.
        - Malformed, unknown-kind, unknown-status or job-mismatched events
          are rejected (logged safe, not dispatched to ``event_callback``)
          and the corresponding job's outcome is ``source_invalid_output``
          with ``detail_event_invalid`` in the safe log.
        - ``event_callback`` is invoked exactly once per valid, job-matched
          event, after validation and before outcome construction.

        Failure modes that surface as per-job ``failed_code``:

        - ``source_invalid_output``: batch-size exceeded, job missing
          ``source_url``/``job_id``, event missing required fields, event
          with wrong types, event with unknown kind/status, event for a job
          not in the batch, completed event without a matching detail
          record, or detail record missing ``job_link``/``source_url``.
        - 非零退出：由退出码分类得到对应 source_* 失败码（如
          ``source_cdp_unavailable``、``source_request_limit_exceeded``）。
        - ``source_timeout``: subprocess exceeded ``timeout_seconds``.
        - ``source_unreachable``: scraper binary not found or OS error.
        - The event's ``safe_code`` is surfaced as ``failed_code`` for
          ``unavailable``/``failed``/``cancelled`` statuses (e.g.
          ``source_login_required``, ``source_invalid_output``).
        """
        # 1. Reject batches larger than max_batch_size without invoking the
        #    scraper. Each job still gets an individual outcome so the
        #    orchestrator can mark it as failed.
        if len(jobs) > max_batch_size:
            return {
                str(job.get("job_id") or job.get("id") or f"idx{i}"):
                    SourceOutcome.failure(
                        failed_code="source_invalid_output",
                        safe_log=f"batch_size_exceeded limit={max_batch_size} "
                                 f"actual={len(jobs)}",
                    )
                for i, job in enumerate(jobs)
            }
        if not isinstance(tab_pool_size, int) or not 1 <= tab_pool_size <= 10:
            raise ValueError(
                f"tab_pool_size must be an integer between 1 and 10, got {tab_pool_size!r}"
            )

        results: dict[str, SourceOutcome] = {}
        valid_jobs: list[dict] = []
        expected_urls_by_job_id: dict[str, str] = {}

        # 2. Validate each job individually. Invalid jobs get an immediate
        #    failure outcome; valid jobs proceed to the batch.
        for i, job in enumerate(jobs):
            if not isinstance(job, dict):
                results[f"idx{i}"] = SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log="job_not_dict",
                )
                continue
            job_id = str(job.get("job_id") or job.get("id") or "").strip()
            if not job_id:
                results[f"idx{i}"] = SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log="job_missing_id",
                )
                continue
            source_url = normalize_job_link(job.get("source_url") or job.get("url") or job.get("job_link") or "")
            if not source_url:
                results[job_id] = SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log="job_missing_source_url",
                )
                continue
            # Deduplicate by source_url within the batch; first occurrence wins.
            if any(existing == source_url for existing in expected_urls_by_job_id.values()):
                results[job_id] = SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log="job_duplicate_in_batch",
                )
                continue
            expected_urls_by_job_id[job_id] = source_url
            # The scraper reads job_link to build detail URLs and emits
            # job_id = job_link in events. Set both explicitly.
            valid_jobs.append({**job, "job_link": source_url, "job_id": job_id})

        if not valid_jobs:
            return results

        # 3. Validate output paths and write the batch input JSON.
        if not detail_output_path or not self._artifact_path_allowed(detail_output_path):
            for job_id in expected_urls_by_job_id:
                results[job_id] = SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log="detail_output_path_invalid",
                )
            return results

        events_output_path = f"{detail_output_path}.events.jsonl"
        batch_input_path = f"{detail_output_path}.input.json"
        try:
            Path(batch_input_path).parent.mkdir(parents=True, exist_ok=True)
            Path(batch_input_path).write_text(
                json.dumps({"jobs": valid_jobs}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            for job_id in expected_urls_by_job_id:
                results[job_id] = SourceOutcome.failure(
                    failed_code="source_unreachable",
                    safe_log="batch_input_write_failed",
                )
            return results

        # 4. Invoke the scraper subprocess.
        command = self._build_detail_batch_command(
            batch_input_path, detail_output_path, events_output_path,
            batch_size=len(valid_jobs),
            gap_min=gap_min, gap_max=gap_max, reset_every=reset_every,
            tab_pool_size=tab_pool_size,
        )
        safe_log = f"batch_detail job_count={len(valid_jobs)}"
        if self.breaker.is_open():
            try:
                Path(batch_input_path).unlink(missing_ok=True)
            except OSError:
                pass
            for job_id in expected_urls_by_job_id:
                results[job_id] = SourceOutcome.failure(
                    failed_code="source_blocked",
                    safe_log=f"{safe_log} breaker_open",
                )
            return results
        try:
            returncode, captured = self._run_command(command, self.timeout_seconds)
        except subprocess.TimeoutExpired:
            for job_id in expected_urls_by_job_id:
                results[job_id] = SourceOutcome.failure(
                    failed_code="source_timeout",
                    safe_log=f"{safe_log} timeout={self.timeout_seconds}",
                )
            return results
        except FileNotFoundError:
            for job_id in expected_urls_by_job_id:
                results[job_id] = SourceOutcome.failure(
                    failed_code="source_unreachable",
                    safe_log=f"{safe_log} scraper_not_found",
                )
            return results
        except OSError as exc:
            for job_id in expected_urls_by_job_id:
                results[job_id] = SourceOutcome.failure(
                    failed_code="source_unreachable",
                    safe_log=f"{safe_log} os_error_type={type(exc).__name__}",
                )
            return results
        finally:
            try:
                Path(batch_input_path).unlink(missing_ok=True)
            except OSError:
                pass

        # 5. Subprocess non-zero exit: whole batch failed; partial results are kept in the output file.
        if returncode != 0:
            failed_code = _classify_failed_code(returncode, captured)
            for job_id in expected_urls_by_job_id:
                results[job_id] = SourceOutcome.failure(
                    failed_code=failed_code,
                    safe_log=f"{safe_log} returncode={returncode} "
                             f"stderr_tail_safe={_safe_tail(captured)}",
                )
            # One batch-level signal (the whole batch was blocked).
            self.breaker.record_signal(failed_code)
            return results

        # 6. Parse events JSONL; validate each event; dispatch valid events.
        expected_urls = set(expected_urls_by_job_id.values())
        parsed_events = self._read_events_file(events_output_path)
        matched_event_by_url: dict[str, dict] = {}
        for event in parsed_events:
            if isinstance(event, dict) and event.get("kind") == "runtime":
                runtime_event = str(event.get("event") or "")
                if runtime_event == "detail_session_reset":
                    record_runtime_event(
                        event=runtime_event, stage="detail",
                        safe_hint=event.get("safe_hint") or "",
                    )
                else:
                    record_runtime_event(
                        event="detail_event_rejected", stage="detail",
                        failed_code="source_invalid_output",
                        safe_hint="unknown_runtime_event",
                    )
                continue
            ok, reason = self._validate_detail_event(event, expected_urls)
            if not ok:
                record_runtime_event(
                    event="detail_event_rejected", stage="detail",
                    failed_code="source_invalid_output", safe_hint=reason,
                )
                continue
            # First valid event for a given job wins; later duplicates are
            # dropped to avoid double-dispatch.
            if event["job_id"] in matched_event_by_url:
                record_runtime_event(
                    event="detail_event_rejected", stage="detail",
                    failed_code="source_invalid_output", safe_hint="duplicate_job_event",
                )
                continue
            matched_event_by_url[event["job_id"]] = event
            record_runtime_event(
                event="detail_terminal", stage="detail",
                failed_code=str(event.get("safe_code") or ""),
                safe_hint=event.get("safe_hint") or "",
                extra={"status": event.get("status") or ""},
            )
            if event_callback is not None:
                try:
                    event_callback(event)
                except Exception:
                    # The callback must never crash the batch; swallow and
                    # continue. The outcome is built from the event itself.
                    pass

        # 7. Read the combined detail JSON and index by job_link/source_url.
        details_by_url = self._read_combined_details(detail_output_path)

        # 8. Build per-job outcomes from the matched event + detail record.
        for job_id, source_url in expected_urls_by_job_id.items():
            event = matched_event_by_url.get(source_url)
            if event is None:
                results[job_id] = SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log=f"{safe_log} detail_event_invalid no_matching_event",
                )
                continue
            status = event["status"]
            safe_code = event["safe_code"]
            if status == "completed":
                detail = details_by_url.get(source_url)
                if not detail:
                    results[job_id] = SourceOutcome.failure(
                        failed_code="source_invalid_output",
                        safe_log=f"{safe_log} detail_event_invalid completed_but_no_detail",
                    )
                    continue
                results[job_id] = SourceOutcome.success(
                    detail=detail,
                    safe_log=f"{safe_log} status=completed "
                             f"fields={sorted(detail.keys())[:5]}",
                )
            elif status in ("unavailable", "failed"):
                # safe_code carries the specific source failure reason
                # (e.g. source_login_required, source_invalid_output).
                results[job_id] = SourceOutcome.failure(
                    failed_code=safe_code,
                    safe_log=f"{safe_log} status={status} safe_code={safe_code}",
                    failed_reason=str(event.get("safe_hint") or ""),
                )
            elif status == "cancelled":
                # cancelled events surface a recognizable code; if the
                # scraper emitted "ok" (which would be ambiguous), normalize
                # to source_unknown_error so the orchestrator can route the
                # unit back to retryable/cancelled state.
                code = safe_code if safe_code != "ok" else "source_unknown_error"
                results[job_id] = SourceOutcome.failure(
                    failed_code=code,
                    safe_log=f"{safe_log} status=cancelled safe_code={safe_code}",
                )
            else:
                # Unreachable: _validate_detail_event already rejected
                # unknown statuses. Defensive guard for future changes.
                results[job_id] = SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log=f"{safe_log} detail_event_invalid unknown_status={status}",
                )

        # 9. T075: feed per-job outcomes to the breaker. Signal codes advance
        #    the consecutive counter; successes reset it. Non-signal failures
        #    (invalid_output, timeout, ...) are neutral. Processing in job_id
        #    order preserves the consecutive-chain semantics: two jobs in the
        #    same batch both returning source_login_required open the breaker.
        for job_id in expected_urls_by_job_id:
            outcome = results.get(job_id)
            if outcome is None:
                continue
            if outcome.ok:
                self.breaker.record_success()
            elif outcome.failed_code in SourceCircuitBreaker.SIGNAL_CODES:
                self.breaker.record_signal(outcome.failed_code)

        # 批返回时一次性推进条数（与 ZhilianCdpSource 的条级回调同语义，
        # 保证编排层 progress 计数在两种实现下都单调）。
        if on_item_done is not None:
            try:
                on_item_done(len(jobs))
            except Exception:
                pass

        return results

    def _validate_detail_event(self, event: Any, expected_urls: set[str]) -> tuple[bool, str]:
        """Validate a terminal safe event.

        Returns ``(ok, reason)``. ``ok`` is True iff the event is a dict
        with all required fields, correct types, ``kind == "detail"``,
        ``status`` in the allowed set, ``job_id`` in ``expected_urls``,
        and no forbidden fields (JD body, credentials, PII).
        """
        if not isinstance(event, dict):
            return False, "not_dict"
        for field in self._EVENT_REQUIRED_FIELDS:
            if field not in event:
                return False, f"missing_{field}"
        # Type checks. Note: bool is a subclass of int in Python, so we
        # explicitly exclude it for duration_ms.
        kind = event["kind"]
        if not isinstance(kind, str) or kind != "detail":
            return False, "unknown_kind"
        status = event["status"]
        if not isinstance(status, str) or status not in self._EVENT_VALID_STATUSES:
            return False, "unknown_status"
        duration_ms = event["duration_ms"]
        if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms < 0:
            return False, "wrong_type_duration_ms"
        safe_code = event["safe_code"]
        if not isinstance(safe_code, str) or not safe_code:
            return False, "wrong_type_safe_code"
        job_id = event["job_id"]
        if not isinstance(job_id, str) or not job_id:
            return False, "wrong_type_job_id"
        if job_id not in expected_urls:
            return False, "job_mismatch"
        # Privacy: reject events carrying forbidden fields.
        for field in self._EVENT_FORBIDDEN_FIELDS:
            if field in event:
                return False, f"forbidden_field_{field}"
        return True, "ok"

    def _read_events_file(self, events_path: str) -> list[Any]:
        """Read events JSONL file. Returns a list of parsed objects.

        Malformed JSON lines are recorded as ``None`` so callers can
        distinguish "missing file" (empty list) from "file had malformed
        lines" (list with None entries). ``None`` entries are dropped by
        ``_validate_detail_event`` (which returns ``False, "not_dict"``).
        """
        path = Path(events_path)
        if not path.is_file() or path.stat().st_size > self.max_artifact_bytes:
            return []
        events: list[Any] = []
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        events.append(None)
        except OSError:
            return []
        return events

    def _read_combined_details(self, detail_path: str) -> dict[str, dict]:
        """Read the combined detail JSON (a list of records) and index by
        ``job_link`` (or ``source_url`` fallback).

        Returns a mapping of ``source_url -> detail_record``. Records
        without ``job_link``/``source_url`` cannot be attributed to any
        job and are dropped (the corresponding job's outcome will surface
        as ``completed_but_no_detail`` invalid output).
        """
        path = Path(detail_path)
        if not path.is_file() or path.stat().st_size > self.max_artifact_bytes:
            return {}
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(payload, list):
            return {}
        details_by_url: dict[str, dict] = {}
        for record in payload:
            if not isinstance(record, dict):
                continue
            url = record.get("job_link") or record.get("source_url") or ""
            if url:
                # First occurrence wins; later duplicates are dropped to
                # avoid attributing one detail record to multiple jobs.
                if url not in details_by_url:
                    details_by_url[url] = record
        return details_by_url

    # ------------------------------------------------------------------
    # Command building
    # ------------------------------------------------------------------

    def _build_list_command(
        self,
        keyword: str,
        city: str,
        target_pages: int,
        source_filters: dict,
        output_path: str,
        combo_key: str = "",
        list_events_output: str | None = None,
        start_page: int = 1,
    ) -> list[str]:
        command = [
            self.python_executable,
            str(self.scraper_path),
            "--cdp-port", str(self.cdp_port),
            "--keyword", keyword,
            "--city", city or "_",
            "--pages", str(int(target_pages)),
            "--output", output_path,
            "--no-detail",  # list fetch never pulls detail; orchestrator does that
            "--skip-login-check",  # 任务级 preflight 已探测，组合级不再重复
        ]
        if start_page and int(start_page) > 1:
            command.extend(["--start-page", str(int(start_page))])
        if combo_key:
            command.extend(["--combo-key", str(combo_key)])
        if list_events_output:
            command.extend(["--list-events-output", str(list_events_output)])
        for name in SCRAPER_FILTER_FIELDS:
            value = (source_filters or {}).get(name)
            if value:
                command.extend([f"--{name}", str(value)])
        return command

    def _build_detail_command(self, detail_input_path: str, detail_output_path: str) -> list[str]:
        return [
            self.python_executable,
            str(self.scraper_path),
            "--cdp-port", str(self.cdp_port),
            "--input", detail_input_path,
            "--detail-output", detail_output_path,
            "--max-details", "1",
            "--detail",
        ]

    def _build_detail_batch_command(
        self,
        batch_input_path: str,
        detail_output_path: str,
        events_output_path: str,
        batch_size: int,
        gap_min: float = 8,
        gap_max: float = 15,
        reset_every: int = 3,
        tab_pool_size: int = 5,
    ) -> list[str]:
        """Build the scraper CLI command for a batched detail fetch.

        ``--max-details`` is set to 5 (the producer contract maximum) so
        the scraper enforces the cap independently. The batch input is
        already ≤5 jobs, so this never truncates a valid batch.
        ``--events-output`` directs the scraper to write terminal safe
        events as JSONL so this adapter can parse/validate them.
        ``--enable-parallel`` (spec 007 ⑧)：批量抓取启用常驻 tab 池并行，
        常驻 tab 池复用省开关开销，错峰+补位节奏防反爬；tab 数默认 5，由调用方传入。
        ``--gap-min/--gap-max``：详情间隔秒数范围（防 code:37）。
        ``--reset-every``：每抓 N 个详情重置一次 session。
        """
        return [
            self.python_executable,
            str(self.scraper_path),
            "--cdp-port", str(self.cdp_port),
            "--input", batch_input_path,
            "--detail-output", detail_output_path,
            "--events-output", events_output_path,
            "--max-details", str(batch_size),
            "--detail",
            "--enable-parallel",
            "--tab-pool-size", str(tab_pool_size),
            "--gap-min", str(gap_min),
            "--gap-max", str(gap_max),
            "--reset-every", str(reset_every),
        ]

    # ------------------------------------------------------------------
    # Subprocess + artifact reading
    # ------------------------------------------------------------------

    def _default_run(self, command: list[str], timeout: int, *, on_poll=None) -> tuple[int, str]:
        result = self._executor.execute(
            command, timeout_seconds=timeout, cwd=self.cwd, env=self.env,
            cancel_event=self.cancel_event,
            on_poll=on_poll,
        )
        if result.failure_code == "process_timeout":
            raise subprocess.TimeoutExpired(command, timeout)
        if result.failure_code == "process_unreachable":
            raise FileNotFoundError(command[0])
        return result.returncode if result.returncode is not None else -1, result.output_tail

    # ------------------------------------------------------------------
    # in_process argv 翻译执行器（合同 inprocess-runner §4.3）
    # ------------------------------------------------------------------

    #: 无值布尔 flag（出现即为 True）。翻译器只识别本类构建的命令，
    #: 其余命令一律视为不可翻译。
    _IN_PROCESS_BOOL_FLAGS = frozenset({
        "no-detail", "detail", "enable-parallel", "analysis",
        "close-chrome", "setup-chrome", "stop-chrome", "smoke-test",
        "skip-login-check",
    })

    def _run_command(
        self, command: list[str], timeout: int, *, on_poll=None, on_page_completed=None,
    ) -> tuple[int, str]:
        if self.in_process:
            return self._run_in_process(command, timeout, on_page_completed=on_page_completed)
        if self._use_default_runner:
            return self._runner(command, timeout, on_poll=on_poll)
        return self._runner(command, timeout)

    def _run_in_process(
        self, command: list[str], timeout: int, *, on_page_completed=None,
    ) -> tuple[int, str]:
        try:
            parsed = self._translate_argv(command)
        except ValueError as exc:
            # 输入文件读取失败/格式非法：显式失败返回，与子进程模式的
            # open()/json.load() 异常等价（不静默空成功，也不向调用方裸抛）
            return (-1, str(exc))
        if parsed is None:
            return (127, "untranslatable_command")
        if parsed.get("kind") == "list" and on_page_completed is not None:
            parsed["params"]["on_page_completed"] = on_page_completed
        try:
            completed, payload = run_with_deadline(
                lambda: self._run_in_process_impl(parsed),
                timeout_seconds=timeout,
                cancel_event=self.cancel_event,
            )
        except boss.SearchCancelled:
            return (-1, "cancelled")
        except boss.CDPUnavailableError as exc:
            return (2, str(exc))
        except boss.RequestLimitExceededError as exc:
            return (11, str(exc))
        except boss.LoginRequiredError as exc:
            # captured 含「登录」关键词，_classify_failed_code 据此映射为
            # source_login_required（合同 §3 表 LoginRequiredError 行）
            return (1, f"登录态失效: {exc}")
        except boss.RiskControlError as exc:
            return (10, exc.reason)
        except PageEventPersistenceError:
            raise
        except ValueError as exc:
            return (3, str(exc))
        except Exception:
            logger.exception("in-process 抓取执行失败（已对外脱敏）")
            return (-1, "抓取执行失败")
        if not completed:
            # 与 _default_run 的 TimeoutExpired 语义一致 → source_timeout
            raise subprocess.TimeoutExpired(command, timeout)
        return payload

    def _run_in_process_impl(self, parsed: dict) -> tuple[int, str]:
        """实际库式调用；stdout 收集进线程感知 capture，返回 ``(0, tail)``。

        run_search_programmatic / scrape_details 的内部 print 走 sys.stdout，
        本方法期间被 capture 收集（其他线程的输出转发回真 stdout），
        与子进程模式的 stdout 捕获等价。
        """
        capture = _InProcessCapture(max_bytes=self._executor.max_output_bytes)
        with capture:
            if parsed["kind"] == "list":
                boss.run_search_programmatic(**parsed["params"])
            else:  # detail / detail_batch
                boss.scrape_details(**parsed["params"])
        return (0, capture.tail())

    def _translate_argv(self, command: list[str]) -> dict | None:
        """解析本类 ``_build_*_command`` 产出的 argv，返回 ``{kind, params}``。

        无法翻译的命令返回 ``None``。只识别 list-only / detail-only /
        detail-batch 三类；``--setup-chrome`` 等其他命令一律视为不可翻译。
        """
        flags: dict[str, str | bool] = {}
        # 跳过 python_executable（command[0]）和 scraper_path（command[1]）
        i = 2
        while i < len(command):
            token = command[i]
            if not isinstance(token, str) or not token.startswith("--"):
                i += 1
                continue
            flag = token[2:]
            if flag in self._IN_PROCESS_BOOL_FLAGS or i + 1 >= len(command):
                flags[flag] = True
                i += 1
            else:
                flags[flag] = command[i + 1]
                i += 2

        # setup-chrome / stop-chrome / smoke-test 等不可翻译
        if any(k in flags for k in ("setup-chrome", "stop-chrome", "smoke-test")):
            return None
        if "no-detail" in flags:
            return self._translate_list_argv(flags)
        if "events-output" in flags:
            return self._translate_detail_batch_argv(flags)
        if "detail" in flags and "input" in flags:
            return self._translate_detail_argv(flags)
        return None

    def _translate_list_argv(self, flags: dict) -> dict:
        """list-only（--no-detail）→ run_search_programmatic(detail=False)。"""
        filters = {}
        for name in SCRAPER_FILTER_FIELDS:
            val = flags.get(name)
            if val not in (None, False, ""):
                filters[name] = str(val)
        params = {
            "keyword": str(flags.get("keyword", "")),
            "city": str(flags.get("city", "_")),
            "pages": int(flags.get("pages", "1")),
            "cdp_port": int(flags.get("cdp-port", str(self.cdp_port))),
            "output_path": str(flags.get("output", "")),
            "detail": False,
            "skip_login_check": bool(flags.get("skip-login-check", False)),
            "filters": filters,
            "cancel_event": self.cancel_event,
            "combo_key": str(flags.get("combo-key", "") or "") or None,
            "list_events_output": str(flags.get("list-events-output", "") or "") or None,
            "start_page": max(1, int(flags.get("start-page", "1") or "1")),
        }
        return {"kind": "list", "params": params}

    def _translate_detail_argv(self, flags: dict) -> dict:
        """detail-only（--input + --detail + --max-details 1）→ scrape_details。"""
        input_path = str(flags.get("input", ""))
        output_path = str(flags.get("detail-output", ""))
        list_data = self._read_detail_input(input_path)
        params = {
            "list_data": list_data,
            "max_details": int(flags.get("max-details", "1")),
            "output_path": output_path,
            "cdp_port": int(flags.get("cdp-port", str(self.cdp_port))),
            "cancel_event": self.cancel_event,
        }
        return {"kind": "detail", "params": params}

    def _translate_detail_batch_argv(self, flags: dict) -> dict:
        """detail-batch（--events-output + --enable-parallel）→ scrape_details with events。"""
        input_path = str(flags.get("input", ""))
        output_path = str(flags.get("detail-output", ""))
        events_output_path = str(flags.get("events-output", ""))
        list_data = self._read_detail_input(input_path)

        # 清空 events 文件，event_callback 追加写 JSONL（与子进程产物格式一致）
        try:
            Path(events_output_path).write_text("", encoding="utf-8")
        except OSError:
            pass

        def event_callback(event):
            try:
                with open(events_output_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            except OSError:
                pass

        gap_min = float(flags.get("gap-min", "8"))
        gap_max = float(flags.get("gap-max", "15"))
        params = {
            "list_data": list_data,
            "max_details": int(flags.get("max-details", "5")),
            "output_path": output_path,
            "cdp_port": int(flags.get("cdp-port", str(self.cdp_port))),
            "event_callback": event_callback,
            "enable_parallel": True,
            "tab_pool_size": int(flags.get("tab-pool-size", "5")),
            "inter_job_gap_range": (gap_min, gap_max),
            "reset_every": int(flags.get("reset-every", "3")),
            "cancel_event": self.cancel_event,
        }
        return {"kind": "detail_batch", "params": params}

    @staticmethod
    def _read_detail_input(input_path: str) -> dict:
        """读取 detail input JSON（fetch_detail/fetch_details_batch 写入）。

        文件缺失或 JSON 非法 → 抛 ``ValueError``（in-process 模式显式失败，
        与子进程模式 ``open()``/``json.load()`` 的异常语义等价），绝不
        静默返回空列表——否则输入损坏会被误判为「成功抓到 0 条」。
        """
        try:
            with open(input_path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("无法读取详情输入文件") from exc
        if not isinstance(payload, dict):
            raise ValueError("详情输入文件格式非法（应为对象）")
        return payload

    def _artifact_path_allowed(self, raw_path: str) -> bool:
        if self.artifact_root is None:
            return True
        try:
            Path(raw_path).resolve().relative_to(self.artifact_root)
        except (OSError, ValueError):
            return False
        return True

    def _read_jobs(self, output_path: str) -> list[dict] | None:
        path = Path(output_path)
        if not path.is_file() or path.stat().st_size > self.max_artifact_bytes:
            return None
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return None
        if isinstance(payload, dict):
            jobs = payload.get("jobs")
            if isinstance(jobs, list):
                return jobs
        return None

    def _read_detail(self, detail_path: str) -> dict | None:
        path = Path(detail_path)
        if not path.is_file() or path.stat().st_size > self.max_artifact_bytes:
            return None
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return None
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
        return None


# ---------------------------------------------------------------------------
# FakeJobSource for tests
# ---------------------------------------------------------------------------


class FakeJobSource:
    """In-memory JobSource for tests. Never invokes a real subprocess.

    Constructed with a mapping of (keyword, city) -> list[dict] for list
    fetches and an optional mapping of job_id -> dict for detail fetches.
    Failures are simulated by mapping to a sentinel ``__fail__:code``.

    符合 ``JobSource`` Protocol（contracts/job-source.md）：携带 ``platform``
    和显式 ``cdp_port``，支持 ``preflight``、``fetch_list``、
    ``fetch_detail`` 和 ``fetch_details_batch``。
    """

    def __init__(
        self,
        list_jobs: dict[tuple[str, str], list[dict]] | None = None,
        detail_jobs: dict[str, dict] | None = None,
        *,
        list_failures: set[tuple[str, str]] | None = None,
        detail_failures: set[str] | None = None,
        input_hash_seed: str = "fake",
        platform: str = "boss",
        cdp_port: int = 9222,
        preflight_failure: str | None = None,
    ):
        self.list_jobs = list_jobs or {}
        self.detail_jobs = detail_jobs or {}
        self.list_failures = list_failures or set()
        self.detail_failures = detail_failures or set()
        self.input_hash_seed = input_hash_seed
        self.platform = str(platform)
        if not isinstance(cdp_port, int) or isinstance(cdp_port, bool) or cdp_port <= 0:
            raise ValueError("cdp_port 必须为正整数")
        self.cdp_port = int(cdp_port)
        self._preflight_failure = preflight_failure
        self.list_calls: list[dict] = []
        self.detail_calls: list[dict] = []
        self.preflight_calls: int = 0

    def preflight(self) -> SourceOutcome:
        """检查登录态和运行环境就绪性（测试替身）。

        默认返回成功；构造时传入 ``preflight_failure`` 可模拟平台级
        阻断（如 ``source_login_required``）。
        """
        self.preflight_calls += 1
        if self._preflight_failure:
            return SourceOutcome.failure(
                failed_code=self._preflight_failure,
                safe_log=f"fake preflight platform={self.platform} port={self.cdp_port} blocked=1",
            )
        return SourceOutcome.success(
            safe_log=f"fake preflight platform={self.platform} port={self.cdp_port} ready=1",
        )

    def fetch_list(
        self, plan_item: dict, *, on_page_completed: Callable[[dict], None] | None = None,
    ) -> SourceOutcome:
        if not isinstance(plan_item, dict):
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log="plan_item_not_dict",
            )
        self.list_calls.append(dict(plan_item))
        keyword = str(plan_item.get("keyword") or "").strip()
        city = str(plan_item.get("city") or "").strip()
        expected_hash = str(plan_item.get("input_hash") or "")
        if not keyword or not expected_hash:
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log=f"fake list missing_fields keyword={bool(keyword)} hash={bool(expected_hash)}",
            )
        if (keyword, city) in self.list_failures:
            return SourceOutcome.failure(
                failed_code="source_blocked",
                safe_log=f"fake list keyword_present=1 city_present={bool(city)} blocked=1",
            )
        jobs = self.list_jobs.get((keyword, city), [])
        actual_hash = _input_hash({
            "keyword": keyword,
            "city": city,
            "source_filters": plan_item.get("source_filters") or {},
            "target_pages": int(plan_item.get("target_pages") or 1),
        })
        if expected_hash and actual_hash != expected_hash:
            return SourceOutcome.failure(
                failed_code="source_input_drift",
                safe_log="fake list input_hash_mismatch",
            )
        target_pages = max(1, int(plan_item.get("target_pages") or 1))
        if on_page_completed is not None:
            on_page_completed({
                "kind": "page_completed",
                "combo_key": str(plan_item.get("combo_key") or "") or f"{keyword}|{city}",
                "keyword": keyword,
                "city": city,
                "page": target_pages,
                "target_pages": target_pages,
                "jobs_delta": len(jobs),
                "jobs_count": len(jobs),
                "has_more": False,
                "resume_page": target_pages + 1,
                "last_completed_page": target_pages,
                "jobs_snapshot": list(jobs),
            })
        return SourceOutcome.success(
            jobs=list(jobs),
            safe_log=f"fake list keyword_present=1 city_present={bool(city)} job_count={len(jobs)}",
            input_hash=actual_hash,
        )

    def fetch_detail(self, job: dict, *, detail_output_path: str | None = None) -> SourceOutcome:
        del detail_output_path  # 测试替身不写盘，签名与真实 source 对齐
        if not isinstance(job, dict):
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log="job_not_dict",
            )
        self.detail_calls.append(dict(job))
        source_url = str(job.get("source_url") or job.get("url") or "").strip()
        job_id = str(job.get("job_id") or job.get("id") or "").strip()
        if not source_url or not job_id:
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log=f"fake detail missing_fields url={bool(source_url)} job_id={bool(job_id)}",
            )
        if job_id in self.detail_failures:
            return SourceOutcome.failure(
                failed_code="source_blocked",
                safe_log="fake detail job_id_present=1 blocked=1",
            )
        detail = self.detail_jobs.get(job_id, {})
        return SourceOutcome.success(detail=detail, safe_log=f"fake detail job_id_present=1 fields={sorted(detail.keys())[:3]}")

    def fetch_details_batch(
        self, jobs: list[dict], *, detail_output_path: str | None = None,
        on_item_done: Callable[[int], None] | None = None,
        **bounded_options,
    ) -> dict[str, SourceOutcome]:
        """批量抓取详情（测试替身）：逐个调用 fetch_detail 并按 job_id 汇总。

        单岗位失败不抛出；每个输入恰有一个终态 outcome。
        ``on_item_done``：每条处理后回调已完成条数（与 ZhilianCdpSource 对齐）。
        """
        del detail_output_path  # 测试替身不写盘，签名与真实 source 对齐
        results: dict[str, SourceOutcome] = {}
        for i, job in enumerate(jobs):
            if not isinstance(job, dict):
                results[f"idx{i}"] = SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log="job_not_dict",
                )
                if on_item_done is not None:
                    try:
                        on_item_done(i + 1)
                    except Exception:
                        pass
                continue
            job_id = str(job.get("job_id") or job.get("id") or "").strip()
            key = job_id or f"idx{i}"
            results[key] = self.fetch_detail(job, **bounded_options)
            if on_item_done is not None:
                try:
                    on_item_done(i + 1)
                except Exception:
                    pass
        return results


# ---------------------------------------------------------------------------
# Helpers (no PII / JD body leakage)
# ---------------------------------------------------------------------------


def _input_hash(payload: Any) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _normalize_job_fields(job: dict) -> dict:
    """Normalize BOSS-specific field names to the unified JobSource interface.

    The BOSS scraper (scripts/boss_cdp_raw.py) returns jobs with field names
    matching the BOSS API: ``encrypt_job_id``, ``job_link``, ``boss_name``.
    Downstream code (fetch_detail, _persist_jobs, build_snapshot) expects the
    unified names: ``job_id``, ``source_url``, ``company``.

    This function returns a *copy* of the job dict with the unified fields
    populated when they are missing but the BOSS-specific alias is present.
    Original BOSS-specific fields are preserved for diagnostic/compatibility.
    A job that already has the unified fields (e.g. from FakeJobSource) is
    returned unchanged (still copied, to avoid mutating caller's dict).
    """
    if not isinstance(job, dict):
        return {}
    normalized = dict(job)
    # job_id: prefer existing, fall back to encrypt_job_id
    if not normalized.get("job_id"):
        alt = normalized.get("encrypt_job_id") or normalized.get("encryptJobId")
        if alt:
            normalized["job_id"] = str(alt)
    # source_url: prefer existing, fall back to job_link / url
    if not normalized.get("source_url"):
        alt = normalized.get("job_link") or normalized.get("url")
        if alt:
            normalized["source_url"] = str(alt)
    # company: prefer existing, fall back to boss_name / brand_name
    if not normalized.get("company"):
        alt = normalized.get("boss_name") or normalized.get("brand_name")
        if alt:
            normalized["company"] = str(alt)
    # welfare: BOSS 列表福利标签（"五险一金 | 双休"）→ extra.welfare_list
    # （specs/004: 归一化层补齐，extra 全链路已持久化；缺失/空时不写键，不编造）
    raw_welfare = normalized.get("welfare")
    if isinstance(raw_welfare, str):
        items = [part.strip() for part in raw_welfare.split("|") if part.strip()]
        if items:
            extra = normalized.get("extra")
            if not isinstance(extra, dict):
                extra = {}
                normalized["extra"] = extra
            extra["welfare_list"] = items
    return normalized


class _InProcessCapture(boss._ThreadAwareStdout):
    """in-process 模式 stdout 收集器：任务线程输出进缓冲，其余线程转发。

    与子进程模式的 stdout 捕获等价：只收集任务线程的 print，其他线程
    （Flask 请求等）的输出转发回真 stdout，避免日志串线；``tail()``
    返回截断到 ``max_bytes`` 的尾部文本（对齐 ScraperExecutor 语义）。
    """

    def __init__(self, max_bytes: int = 1_000_000):
        super().__init__()
        self._chunks: list[str] = []
        self._size = 0
        self._max = max(1, int(max_bytes))

    def write(self, text):
        if not text:
            return 0
        if threading.get_ident() != self._tid:
            if self._fallback is not None:
                try:
                    self._fallback.write(text)
                except Exception:
                    pass
            return len(text)
        if self._size < self._max:
            take = text[: self._max - self._size]
            self._chunks.append(take)
            self._size += len(take)
        return len(text)

    def tail(self, max_chars: int | None = None) -> str:
        limit = self._max if max_chars is None else int(max_chars)
        return "".join(self._chunks)[-limit:]


def _safe_tail(text: str, *, max_chars: int = 300) -> str:
    """Return last ``max_chars`` characters, stripped of newlines.

    Used only for safe log lines; the captured subprocess output never
    includes resume text or credentials because the scraper does not
    receive them. We still truncate to keep logs bounded.
    """
    if not text:
        return ""
    tail = text[-max_chars:].replace("\n", " ").replace("\r", " ").strip()
    return tail


# scraper 退出码 → 用户可读原因
_EXIT_REASONS = {
    1: "登录态失效或环境异常",
    2: "连不上调试浏览器（Chrome 未启动或端口不通）",
    3: "抓取参数错误（CLI 参数校验失败）",
    10: "触发风控/限流（验证码、连续空页或 HTTP 拦截）",
}

# 退出码 + 输出关键词 → 具体 failed_code（不再一律 source_blocked）
_VERIFICATION_KEYWORDS = ("验证码", "滑块", "滑动验证", "captcha", "slider")
_RATE_LIMIT_KEYWORDS = (
    "429", "http 403", "http 412", "http 418",
    "403 forbidden", "412 precondition", "418 im a teapot",
    "操作频繁", "频繁访问", "访问频繁", "稍后再试", "访问受限", "异常流量", "账号受限", "限流",
    "rate limit", "too many",
)
_LOGIN_REQUIRED_KEYWORDS = (
    "401", "登录态失效", "登录失效", "登录已失效", "请先登录", "未登录",
    "cookie 失效", "cookie已失效",
)
# 退出码 1（登录态失效或环境异常）专用：只认高置信短语，避免正文里
# 单个“登录/login/cookie”字眼把正常页面误判成登录失效。B027 回归方向。
_LOGIN_REQUIRED_HI_CONFIDENCE_KEYWORDS = (
    "401",
    "登录态失效", "登录失效", "登录已失效", "请先登录", "未登录",
    "未检测到 boss直聘登录状态",
    "cookie 失效", "cookie已失效", "cookie 已失效",
)


def _has_unlock_signal(text: str) -> bool:
    """高置信解封时间信号：完整未来时间点或明确解封/解锁时间文案。"""
    if not text:
        return False
    try:
        if boss.parse_unlock_time(text) is not None:
            return True
    except Exception:
        pass
    lowered = str(text).lower()
    return any(kw in lowered for kw in ("解封时间", "解封后", "解封于", "解锁时间"))


def _classify_failed_code(returncode: int, captured: str) -> str:
    """根据退出码和输出文本分类出具体 failed_code。

    退出码含义（boss_cdp_raw.py）：
      1  — 登录态失效或环境异常
      2  — 连不上调试浏览器（CDPUnavailableError）
      3  — 抓取参数错误（CLI 参数校验失败）
      10 — 触发风控/限流（RiskControlError：验证码、连续空页、HTTP 拦截）
      11 — 单次抓取运行请求数达到上限（RequestLimitExceededError）
    """
    if returncode == 2:
        return "source_cdp_unavailable"
    if returncode == 3:
        return "source_invalid_output"
    if returncode == 11:
        return "source_request_limit_exceeded"
    text = (captured or "").lower()
    if returncode == 1:
        if any(kw in text for kw in _LOGIN_REQUIRED_HI_CONFIDENCE_KEYWORDS):
            return "source_login_required"
        return "source_unknown_error"
    if returncode == 10:
        if any(kw in text for kw in _LOGIN_REQUIRED_KEYWORDS):
            return "source_login_required"
        if _has_unlock_signal(captured):
            return "source_rate_limited"
        if any(kw in text for kw in _RATE_LIMIT_KEYWORDS):
            return "source_rate_limited"
        if any(kw in text for kw in _VERIFICATION_KEYWORDS):
            return "source_verification_required"
        return "source_unknown_error"
    return "source_unknown_error"


def _record_risk_signals(account, platform, failed_code, captured, run_id=""):
    """抓取失败时同步回写登录态缓存与风控冷却（D3/D6 信号回写）。

    - restricted 类错误码（blocked/rate_limited/verification）→
      登录缓存写 restricted，并用风控 hint 文本标记 cooldown
      （文本含完整日期时间解封点时用精确时间，否则默认 4 小时）；
    - source_login_required → 登录缓存写 not_logged_in（不冷却）。
    account 为空时跳过（CLI 直连场景不记录账号维度）。
    """
    if not account:
        return
    from scripts.login_state_cache import write_login_state
    from webui.cooldown import mark_cooldown
    # 只有高置信风控（限流/验证码）才写 restricted 缓存与冷却；通用 source_blocked
    # 不写持久副作用，避免正常失败把账号误标成受限（B027 回归）。
    if failed_code in ("source_rate_limited", "source_verification_required"):
        write_login_state(account, platform, "restricted")
        hint = boss.extract_block_hint(captured) if captured else ""
        mark_cooldown(account, platform, hint or failed_code, from_run=run_id)
    elif failed_code == "source_login_required":
        write_login_state(account, platform, "not_logged_in")


def _record_success_signal(account, platform):
    """列表抓取持续拿到明文工资 → 登录缓存写 logged_in（D3 信号回写）。"""
    if not account:
        return
    from scripts.login_state_cache import write_login_state
    write_login_state(account, platform, "logged_in")


def _exit_reason(returncode: int, captured: str) -> str:
    """从退出码和输出尾部提取一句用户可读的失败原因。"""
    base = _EXIT_REASONS.get(returncode, f"scraper 异常退出（code={returncode}）")
    tail = _safe_tail(captured, max_chars=150)
    if tail:
        return f"{base}｜{tail}"
    return base


def _safe_host(url: str) -> str:
    """Return only the hostname of a URL for log lines, never the path."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc or ""
    except (ValueError, TypeError):
        return ""


# ===========================================================================
# tasks004 — ZhilianCdpSource adapter（2026-08-04 真实页面核验后启用）
#
#   - 构造校验（T301/T302）：显式冻结端口、profile_key 边界、不回退 BOSS；
#   - preflight（T303）：zhilian_cdp_raw.preflight 真实登录/DOM 判定；
#   - fetch_list（T306/T307/T308）：真实 API 列表、统一字段、空结果证据；
#   - fetch_detail（T311）：真实 __INITIAL_STATE__ JD，不伪造正文；
#   - fetch_details_batch（T312）：熔断器复用；
#   - 日志安全（T304）：只含平台/阶段/计数/ID/URL host，脱敏。
# ===========================================================================

# 智联 CDP 冻结端口（与 BOSS 9222 隔离，避免 profile/平台边界泄漏）。
ZHILIAN_DEFAULT_CDP_PORT = 9223

# BOSS 默认端口，智联 adapter 构造时显式拒绝，避免隐式回退 BOSS 登录空间。
_BOSS_DEFAULT_CDP_PORT = 9222

# 智联平台岗位 URL host allowlist（与 webui/platforms.py 注册规则一致）。
_ZHILIAN_HOST_ALLOWLIST = frozenset({
    "www.zhaopin.com",
    "zhaopin.com",
    "m.zhaopin.com",
    "jobs.zhaopin.com",
    "fe-api.zhaopin.com",
    "i.zhaopin.com",
})

# AI 筛选字段黑名单：不得进入 adapter 列表参数（contracts/job-source.md）。
_ZHILIAN_AI_FILTER_KEYS = frozenset({
    "source_filters", "filters", "screening_fields",
    "salary", "experience", "degree", "industry",
    "scale", "stage", "company_nature",
})

# zhilian_cdp_raw.preflight signal → SAFE_FAILURE_CODES 映射。
_ZHILIAN_PREFLIGHT_SIGNAL_MAP = {
    "ok": None,
    "cdp_unavailable": "source_cdp_unavailable",
    "login_required": "source_login_required",
    "verification": "source_verification_required",
    "rate_limited": "source_rate_limited",
    "blocked": "source_blocked",
    "unreachable": "source_unreachable",
    "timeout": "source_timeout",
}

# 智联 preflight signal ↔ 登录态缓存四态（D3：智联状态也进同一缓存）
_SIGNAL_TO_STATE = {
    "ok": "logged_in",
    "login_required": "not_logged_in",
    "verification": "restricted",
    "rate_limited": "restricted",
    "blocked": "restricted",
    "cdp_unavailable": "unknown",
    "unreachable": "unknown",
    "timeout": "unknown",
}
_STATE_TO_SIGNAL = {state: signal for signal, state in _SIGNAL_TO_STATE.items()}
_STATE_TO_SIGNAL["unknown"] = "unreachable"  # 缓存 unknown 时回退真实探测

# zhilian_cdp_raw.fetch_detail signal → SAFE_FAILURE_CODES 映射。
_ZHILIAN_DETAIL_SIGNAL_MAP = {
    "ok": None,
    "not_found": "source_not_found",
    "invalid_output": "source_invalid_output",
    "timeout": "source_timeout",
    "login_required": "source_login_required",
    "verification": "source_verification_required",
    "rate_limited": "source_rate_limited",
    "blocked": "source_blocked",
    "unreachable": "source_unreachable",
}

# zhilian_cdp_raw.fetch_list signal → SAFE_FAILURE_CODES 映射。
_ZHILIAN_LIST_SIGNAL_MAP = {
    "ok": None,
    "empty": None,  # 真实空结果走 empty_success 路径，由 marker fixture 解锁
    "login_required": "source_login_required",
    "verification": "source_verification_required",
    "rate_limited": "source_rate_limited",
    "blocked": "source_blocked",
    "unreachable": "source_unreachable",
    "timeout": "source_timeout",
    "invalid_output": "source_invalid_output",
}


def _zhilian_input_hash(payload: Any) -> str:
    """智联 input_hash：覆盖 platform/关键词/完整城市解析快照/页数。

    与 BOSS _input_hash 区别：智联 hash 必须包含 platform 字段和完整 city
    解析快照（name/platform_code/mapping_version），用于跨平台去重和
    缺城映射阻断校验。
    """
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _is_zhilian_host(url: str) -> bool:
    """URL host 是否在智联 allowlist 内（脱敏判定，不解析 path/query）。"""
    return _safe_host(url).lower() in _ZHILIAN_HOST_ALLOWLIST


def _zhilian_safe_log(*, stage: str, platform: str = "zhilian",
                     counts: dict | None = None, has_id: bool | None = None,
                     url_host: str | None = None, failed_code: str | None = None) -> str:
    """构造智联安全日志行：只含平台/阶段/计数/ID 是否存在/URL host/失败码。

    严禁包含 Cookie、JD 正文、页面正文、profile 路径、绝对路径、token 等。
    """
    parts = [f"platform={platform}", f"stage={stage}"]
    if failed_code:
        parts.append(f"failed_code={failed_code}")
    if counts:
        for key in sorted(counts):
            parts.append(f"{key}={counts[key]}")
    if has_id is not None:
        parts.append(f"has_id={'1' if has_id else '0'}")
    if url_host:
        # host 已经过 _safe_host 脱敏，不含 path/query。
        parts.append(f"url_host={url_host}")
    return " ".join(parts)


def _validate_zhilian_city_snapshot(city: Any) -> bool:
    """校验城市解析快照：必须是带 name/platform_code/mapping_version 的 dict。"""
    if not isinstance(city, dict):
        return False
    name = str(city.get("name") or "").strip()
    code = str(city.get("platform_code") or "").strip()
    if not name or not code:
        return False
    if "mapping_version" not in city:
        return False
    return True


def _validate_zhilian_plan_item(plan_item: Any) -> tuple[bool, str]:
    """校验 fetch_list 输入：dict、platform 一致、关键词、城市快照、页数、input_hash、无 AI filters。"""
    if not isinstance(plan_item, dict):
        return False, "plan_item_not_dict"
    if str(plan_item.get("platform") or "").strip() != "zhilian":
        return False, "platform_mismatch"
    keyword = str(plan_item.get("keyword") or "").strip()
    if not keyword:
        return False, "missing_keyword"
    city = plan_item.get("city")
    if not _validate_zhilian_city_snapshot(city):
        return False, "city_snapshot_invalid"
    target_pages = plan_item.get("target_pages")
    if not isinstance(target_pages, int) or isinstance(target_pages, bool) or target_pages <= 0:
        return False, "target_pages_invalid"
    input_hash = str(plan_item.get("input_hash") or "").strip()
    if not input_hash:
        return False, "missing_input_hash"
    # AI 筛选字段黑名单：任一出现即拒绝。
    for forbidden in _ZHILIAN_AI_FILTER_KEYS:
        if forbidden in plan_item:
            return False, f"ai_filter_present:{forbidden}"
    return True, "ok"


def _validate_zhilian_detail_input(job: Any) -> tuple[bool, str]:
    """校验 fetch_detail 输入：dict、platform 一致、platform_job_id、canonical_url、URL host。"""
    if not isinstance(job, dict):
        return False, "job_not_dict"
    if str(job.get("platform") or "").strip() != "zhilian":
        return False, "platform_mismatch"
    job_id = str(job.get("platform_job_id") or "").strip()
    if not job_id:
        return False, "missing_platform_job_id"
    canonical_url = str(job.get("canonical_url") or "").strip()
    if not canonical_url:
        return False, "missing_canonical_url"
    if not _is_zhilian_host(canonical_url):
        return False, "platform_url_mismatch"
    return True, "ok"


class ZhilianCdpSource:
    """智联 CDP adapter（tasks004，2026-08-04 真实页面核验后启用）。

    符合 ``JobSource`` Protocol（contracts/job-source.md）：携带 ``platform``
    和显式 ``cdp_port``，支持 ``preflight``、``fetch_list``、``fetch_detail``
    和 ``fetch_details_batch``。

    """

    platform: str = "zhilian"

    def __init__(
        self,
        *,
        browser_account: str,
        cdp_port: int,
        profile_key: str | None = None,
        breaker: SourceCircuitBreaker | None = None,
        preflight_runner: Callable[[int], str] | None = None,
        list_runner: Callable[[dict], tuple[str, list[dict]]] | None = None,
        detail_runner: Callable[[dict], tuple[str, dict]] | None = None,
        batch_detail_runner: Callable[[dict], tuple[list[tuple[str, dict]], str | None]] | None = None,
        run_id: str = "",
    ):
        if not browser_account or not str(browser_account).strip():
            raise ValueError("browser_account 必须非空")
        if not isinstance(cdp_port, int) or isinstance(cdp_port, bool) or cdp_port <= 0:
            raise ValueError("cdp_port 必须为正整数")
        if cdp_port == _BOSS_DEFAULT_CDP_PORT:
            # 显式拒绝 BOSS 默认端口，避免隐式回退 BOSS 登录空间。
            raise ValueError(
                f"智联 adapter 不得使用 BOSS 默认端口 {_BOSS_DEFAULT_CDP_PORT}，"
                f"请使用冻结端口 {ZHILIAN_DEFAULT_CDP_PORT}"
            )
        expected_profile_key = f"zhilian:{browser_account}"
        if profile_key is not None and profile_key != expected_profile_key:
            raise ValueError(
                f"profile_key 必须等于 {expected_profile_key!r}，"
                f"不得使用其它平台 profile_key"
            )
        self.browser_account = str(browser_account).strip()
        self.cdp_port = int(cdp_port)
        self.profile_key = expected_profile_key
        self.breaker = breaker or SourceCircuitBreaker()
        self.run_id = str(run_id or "").strip()
        # runner 注入：默认调用 zhilian_cdp_raw 的真实函数；测试通过替身绕过真实 CDP。
        # 测试通过注入替身绕过真实 CDP 调用。
        self._preflight_runner = preflight_runner or _default_zhilian_preflight_runner
        self._list_runner = list_runner or _default_zhilian_list_runner
        self._detail_runner = detail_runner or _default_zhilian_detail_runner
        self._batch_detail_runner = batch_detail_runner or _default_zhilian_batch_detail_runner

    def _record_risk_signal(self, failed_code: str, reason: str = "") -> None:
        """高置信智联风控信号写 restricted 缓存与冷却，来源带 run_id。"""
        if failed_code not in ("source_rate_limited", "source_verification_required"):
            return
        _record_risk_signals(
            self.browser_account, self.platform, failed_code,
            reason or _zhilian_failed_reason(failed_code), run_id=self.run_id,
        )

    # ------------------------------------------------------------------
    # T301: 平台禁用门禁（新任务创建前由编排层调用）
    # ------------------------------------------------------------------
    @staticmethod
    def preflight_disabled_platform() -> SourceOutcome:
        """智联 enabled_for_new_tasks=False 时新任务创建前阻断。

        返回 ``platform_disabled`` 稳定错误码，不静默切换 BOSS。
        编排层在调用 ZhilianCdpSource 构造前应先调用本方法判断。
        """
        return SourceOutcome.failure(
            failed_code="platform_disabled",
            safe_log=_zhilian_safe_log(
                stage="preflight", failed_code="platform_disabled",
                counts={"platform_enabled": 0},
            ),
            failed_reason="智联平台当前禁用（enabled_for_new_tasks=False）",
        )

    # ------------------------------------------------------------------
    # T303: preflight 分类
    # ------------------------------------------------------------------
    def preflight(self) -> SourceOutcome:
        """检查智联冻结 CDP 端口、profile、登录态和平台可访问性。

        调用 ``_preflight_runner``（默认 zhilian_cdp_raw.preflight），按返回的
        signal 字符串映射到错误矩阵（登录/验证/限流/封禁/CDP/超时）。

        登录判定缓存优先（D3）：账号 × 平台 15 分钟 TTL 内命中直接复用
        DOM marker 探测结果，不反复导航搜索页。
        """
        cached = None
        if self.browser_account:
            from scripts.login_state_cache import read_cached_state
            cached = read_cached_state(self.browser_account, "zhilian")
        if cached is not None and cached != "unknown":
            return self._outcome_for_signal(_STATE_TO_SIGNAL.get(cached, "ok"))
        try:
            signal = self._preflight_runner(self.cdp_port)
        except Exception:
            signal = "unreachable"
        signal = str(signal or "unreachable")
        if self.browser_account:
            from scripts.login_state_cache import write_login_state
            write_login_state(
                self.browser_account, "zhilian",
                _SIGNAL_TO_STATE.get(signal, "unknown"),
            )
        return self._outcome_for_signal(signal)

    def _outcome_for_signal(self, signal: str) -> SourceOutcome:
        """把智联 preflight signal 映射为统一 SourceOutcome。"""
        failed_code = _ZHILIAN_PREFLIGHT_SIGNAL_MAP.get(signal, "source_unknown_error")
        if failed_code is None:
            return SourceOutcome.success(
                safe_log=_zhilian_safe_log(
                    stage="preflight",
                    counts={"cdp_port": self.cdp_port, "ready": 1},
                ),
            )
        # 平台级 signal 推进熔断器（login/verification/rate_limited/blocked）。
        if failed_code in SourceCircuitBreaker.SIGNAL_CODES:
            self.breaker.record_signal(failed_code)
        self._record_risk_signal(failed_code, _zhilian_failed_reason(failed_code))
        return SourceOutcome.failure(
            failed_code=failed_code,
            safe_log=_zhilian_safe_log(
                stage="preflight", failed_code=failed_code,
                counts={"cdp_port": self.cdp_port},
            ),
            failed_reason=_zhilian_failed_reason(failed_code),
        )

    # ------------------------------------------------------------------
    # T306: fetch_list 输入校验
    # ------------------------------------------------------------------
    def fetch_list(
        self, plan_item: dict, *, on_page_completed: Callable[[dict], None] | None = None,
    ) -> SourceOutcome:
        """抓取智联岗位列表页。

        只接收关键词、规范城市解析快照和页数；真实 API 结果统一字段。
        runner 返回 empty signal 时必须携带 empty_evidence，否则按失败处理。
        ``on_page_completed``：每完成一页回调结构化页级事件。"""
        ok, reason = _validate_zhilian_plan_item(plan_item)
        if not ok:
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log=_zhilian_safe_log(
                    stage="list", failed_code="source_invalid_output",
                    counts={"reason": reason[:32]},
                ),
                failed_reason=f"输入校验失败: {reason}",
            )
        try:
            runner_item = dict(plan_item)
            runner_item["cdp_port"] = self.cdp_port
            runner_item["combo_key"] = str(plan_item.get("combo_key") or "") or ""
            runner_item["on_page_completed"] = on_page_completed
            runner_result = self._list_runner(runner_item)
            if isinstance(runner_result, tuple) and len(runner_result) == 2:
                signal, jobs = runner_result
                empty_evidence = None
            else:
                signal, jobs, empty_evidence = runner_result
        except Exception:
            return SourceOutcome.failure(
                failed_code="source_unknown_error",
                safe_log=_zhilian_safe_log(
                    stage="list", failed_code="source_unknown_error",
                ),
            )
        signal = str(signal or "invalid_output")
        if signal == "ok":
            # 字段归一化由 zhilian_cdp_raw._normalize_job 完成，这里透传。
            return SourceOutcome.success(
                jobs=list(jobs or []),
                safe_log=_zhilian_safe_log(
                    stage="list",
                    counts={"job_count": len(jobs or []),
                            "target_pages": plan_item.get("target_pages", 0)},
                ),
                input_hash=str(plan_item.get("input_hash") or ""),
            )
        if signal == "empty":
            if not isinstance(empty_evidence, dict) or not empty_evidence:
                return SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log=_zhilian_safe_log(
                        stage="list", failed_code="source_invalid_output",
                        counts={"reason": "empty_evidence_missing"},
                    ),
                    failed_reason="空结果证据缺失，不得返回 empty_success",
                )
            return SourceOutcome.empty_success(
                empty_evidence=empty_evidence,
                safe_log=_zhilian_safe_log(
                    stage="list", counts={"empty_result": 1},
                ),
                input_hash=str(plan_item.get("input_hash") or ""),
            )
        failed_code = _ZHILIAN_LIST_SIGNAL_MAP.get(signal, "source_unknown_error")
        if failed_code is None:
            failed_code = "source_unknown_error"
        if failed_code in SourceCircuitBreaker.SIGNAL_CODES:
            self.breaker.record_signal(failed_code)
        self._record_risk_signal(failed_code, _zhilian_failed_reason(failed_code))
        return SourceOutcome.failure(
            failed_code=failed_code,
            safe_log=_zhilian_safe_log(
                stage="list", failed_code=failed_code,
            ),
            failed_reason=_zhilian_failed_reason(failed_code),
        )

    # ------------------------------------------------------------------
    # T311: fetch_detail URL/平台/身份校验
    # ------------------------------------------------------------------
    def fetch_detail(
        self, job: dict, *, detail_output_path: str | None = None,
    ) -> SourceOutcome:
        """抓取智联单个岗位详情页。

        校验平台/URL/身份后调用真实 __INITIAL_STATE__ 抓取；
        无法取得真实 JD 时返回明确单项失败，不伪造正文。
        """
        ok, reason = _validate_zhilian_detail_input(job)
        if not ok:
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log=_zhilian_safe_log(
                    stage="detail", failed_code="source_invalid_output",
                    counts={"reason": reason[:32]},
                ),
                failed_reason=f"输入校验失败: {reason}",
            )
        try:
            runner_job = dict(job)
            runner_job["cdp_port"] = self.cdp_port
            signal, detail = self._detail_runner(runner_job, detail_output_path=detail_output_path)
        except Exception:
            return SourceOutcome.failure(
                failed_code="source_unknown_error",
                safe_log=_zhilian_safe_log(
                    stage="detail", failed_code="source_unknown_error",
                ),
            )
        signal = str(signal or "invalid_output")
        if signal == "ok":
            return SourceOutcome.success(
                detail=detail or {},
                safe_log=_zhilian_safe_log(
                    stage="detail",
                    counts={"has_detail": 1},
                    has_id=True,
                    url_host=_safe_host(str(job.get("canonical_url") or "")),
                ),
            )
        failed_code = _ZHILIAN_DETAIL_SIGNAL_MAP.get(signal, "source_unknown_error")
        if failed_code is None:
            failed_code = "source_unknown_error"
        if failed_code in SourceCircuitBreaker.SIGNAL_CODES:
            self.breaker.record_signal(failed_code)
        self._record_risk_signal(failed_code, _zhilian_failed_reason(failed_code))
        return SourceOutcome.failure(
            failed_code=failed_code,
            safe_log=_zhilian_safe_log(
                stage="detail", failed_code=failed_code,
                has_id=True,
                url_host=_safe_host(str(job.get("canonical_url") or "")),
            ),
            failed_reason=_zhilian_failed_reason(failed_code),
        )

    # ------------------------------------------------------------------
    # T312: fetch_details_batch 熔断器复用
    # ------------------------------------------------------------------
    def fetch_details_batch(
        self, jobs: list[dict], *, detail_output_path: str | None = None,
        on_item_done: Callable[[int], None] | None = None,
        **bounded_options,
    ) -> dict[str, SourceOutcome]:
        """批量抓取详情：单项异常继续，连续平台级 signal 触发熔断。

        熔断器打开后，后续岗位不再调用 runner，直接返回 source_blocked
        （可暂停 outcome，编排层可后续 retry）。

        ``on_item_done``：串行逐条抓取时每条完成后实时回调（1 起递增）；
        tab 池并行模式在批返回后按条回放（对齐 BOSS 子进程批返回语义），
        供编排层把进度回传给前端。

        高级设置 5 个 JD 参数经 pipeline_exec 统一映射后透传（与 BOSS 同一
        调用点）：gap_min/gap_max → 条间间隔；reset_every → 每抓 N 条导航回
        首页重置；tab_pool_size → 常驻 tab 数（1 走串行，>1 走并行池，
        钳制 1-10）。
        """
        results: dict[str, SourceOutcome] = {}
        gap_min = max(0.0, float(bounded_options.get("gap_min") or 0.0))
        gap_max = max(gap_min, float(bounded_options.get("gap_max") or gap_min))
        reset_every = max(1, int(bounded_options.get("reset_every") or 1))
        try:
            tab_pool_size = int(bounded_options.get("tab_pool_size") or 1)
        except (TypeError, ValueError):
            tab_pool_size = 1
        tab_pool_size = max(1, min(10, tab_pool_size))

        if tab_pool_size == 1:
            # 串行路径：复用现有逐条抓取（_detail_runner 替身），
            # gap_min/gap_max 作为条间间隔。
            for i, job in enumerate(jobs):
                if not isinstance(job, dict):
                    results[f"idx{i}"] = SourceOutcome.failure(
                        failed_code="source_invalid_output",
                        safe_log=_zhilian_safe_log(
                            stage="batch", failed_code="source_invalid_output",
                            counts={"idx": i, "reason": "job_not_dict"},
                        ),
                    )
                    if on_item_done is not None:
                        try:
                            on_item_done(i + 1)
                        except Exception:
                            pass
                    continue
                job_id = str(job.get("platform_job_id") or "").strip()
                key = job_id or f"idx{i}"
                # 熔断器打开：直接返回 source_blocked，不再调用 runner。
                if self.breaker.is_open():
                    results[key] = SourceOutcome.failure(
                        failed_code="source_blocked",
                        safe_log=_zhilian_safe_log(
                            stage="batch", failed_code="source_blocked",
                            counts={"idx": i, "breaker_open": 1},
                        ),
                        failed_reason="熔断器已打开，连续平台级 signal 触发",
                    )
                    if on_item_done is not None:
                        try:
                            on_item_done(i + 1)
                        except Exception:
                            pass
                    continue
                results[key] = self.fetch_detail(job, detail_output_path=detail_output_path)
                if on_item_done is not None:
                    try:
                        on_item_done(i + 1)
                    except Exception:
                        pass
                if gap_min > 0 and i + 1 < len(jobs):
                    time.sleep(random.uniform(gap_min, gap_max))
            return results

        # 并行路径（tab_pool_size > 1）：常驻 tab 池，对齐 BOSS 并行分支。
        valid: list[tuple[int, str]] = []  # (i, key)
        for i, job in enumerate(jobs):
            if not isinstance(job, dict):
                results[f"idx{i}"] = SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log=_zhilian_safe_log(
                        stage="batch", failed_code="source_invalid_output",
                        counts={"idx": i, "reason": "job_not_dict"},
                    ),
                )
                if on_item_done is not None:
                    try:
                        on_item_done(i + 1)
                    except Exception:
                        pass
                continue
            job_id = str(job.get("platform_job_id") or "").strip()
            # 并行 runner 按 canonical_url 去重；缺失/重复会导致 per_item 与
            # valid 错位（结果张冠李戴），因此在此拦截（对齐 BOSS
            # job_missing_source_url / job_duplicate_in_batch 语义）。
            canonical = str(job.get("canonical_url") or "").strip()
            if not canonical:
                results[job_id or f"idx{i}"] = SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log=_zhilian_safe_log(
                        stage="batch", failed_code="source_invalid_output",
                        counts={"idx": i, "reason": "job_missing_canonical_url"},
                    ),
                )
                if on_item_done is not None:
                    try:
                        on_item_done(i + 1)
                    except Exception:
                        pass
                continue
            if any(str(jobs[j].get("canonical_url") or "").strip() == canonical
                   for j, _ in valid):
                results[job_id or f"idx{i}"] = SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log=_zhilian_safe_log(
                        stage="batch", failed_code="source_invalid_output",
                        counts={"idx": i, "reason": "job_duplicate_in_batch"},
                    ),
                )
                if on_item_done is not None:
                    try:
                        on_item_done(i + 1)
                    except Exception:
                        pass
                continue
            valid.append((i, job_id or f"idx{i}"))
        if not valid:
            return results
        # 熔断器已打开：整批不再调用 runner，全部 source_blocked。
        if self.breaker.is_open():
            for i, key in valid:
                results[key] = SourceOutcome.failure(
                    failed_code="source_blocked",
                    safe_log=_zhilian_safe_log(
                        stage="batch", failed_code="source_blocked",
                        counts={"idx": i, "breaker_open": 1},
                    ),
                    failed_reason="熔断器已打开，连续平台级 signal 触发",
                )
                if on_item_done is not None:
                    try:
                        on_item_done(i + 1)
                    except Exception:
                        pass
            return results
        runner_jobs = []
        for i, _ in valid:
            runner_job = dict(jobs[i])
            runner_job["cdp_port"] = self.cdp_port
            runner_jobs.append(runner_job)
        try:
            per_item, degrade_signal = self._batch_detail_runner(
                {"jobs": runner_jobs},
                cdp_port=self.cdp_port,
                tab_pool_size=tab_pool_size,
                inter_job_gap_range=(gap_min, gap_max),
                reset_every=reset_every,
            )
        except Exception:
            per_item, degrade_signal = [], "unreachable"
        recorded_batch_signals: set[str] = set()
        for idx, (i, key) in enumerate(valid):
            signal, detail = per_item[idx] if idx < len(per_item) else ("skipped", {})
            signal = str(signal or "invalid_output")
            if signal == "ok":
                results[key] = SourceOutcome.success(
                    detail=dict(detail or {}),
                    safe_log=_zhilian_safe_log(
                        stage="batch", counts={"has_detail": 1},
                        has_id=True,
                        url_host=_safe_host(str(jobs[i].get("canonical_url") or "")),
                    ),
                )
            elif signal == "skipped":
                # degrade 停工后未处理：CDP 建池失败归因环境、runner 异常归
                # 未知错误、其余风险降级归 source_blocked
                if degrade_signal == "cdp_unavailable":
                    skipped_code = "source_cdp_unavailable"
                elif degrade_signal == "unreachable":
                    skipped_code = "source_unknown_error"
                else:
                    skipped_code = "source_blocked"
                results[key] = SourceOutcome.failure(
                    failed_code=skipped_code,
                    safe_log=_zhilian_safe_log(
                        stage="batch", failed_code=skipped_code,
                        counts={"idx": i, "degraded": 1},
                    ),
                    failed_reason=_zhilian_failed_reason(skipped_code),
                )
            else:
                failed_code = _ZHILIAN_DETAIL_SIGNAL_MAP.get(signal, "source_unknown_error")
                if failed_code in SourceCircuitBreaker.SIGNAL_CODES:
                    self.breaker.record_signal(failed_code)
                if failed_code not in recorded_batch_signals:
                    recorded_batch_signals.add(failed_code)
                    self._record_risk_signal(failed_code, _zhilian_failed_reason(failed_code))
                results[key] = SourceOutcome.failure(
                    failed_code=failed_code,
                    safe_log=_zhilian_safe_log(
                        stage="batch", failed_code=failed_code,
                        counts={"idx": i, "signal": signal},
                        has_id=True,
                        url_host=_safe_host(str(jobs[i].get("canonical_url") or "")),
                    ),
                    failed_reason=_zhilian_failed_reason(failed_code),
                )
            if on_item_done is not None:
                try:
                    on_item_done(i + 1)
                except Exception:
                    pass
        return results


# ---------------------------------------------------------------------------
# 默认 runner：调用 scripts/zhilian_cdp_raw.py 的真实函数。
# preflight/list/detail 分别调用真实登录判定、搜索 API 与详情页抓取。
# ---------------------------------------------------------------------------

def _default_zhilian_preflight_runner(cdp_port: int) -> str:
    """默认 preflight runner：调用 zhilian_cdp_raw.preflight。

    zhilian_cdp_raw.preflight 返回稳定 signal；
    本函数把 None 转为 "unreachable"，避免伪造成功。
    """
    try:
        from scripts import zhilian_cdp_raw as zha
    except ImportError:
        return "unreachable"
    result = zha.preflight(cdp_port=cdp_port)
    if result is None:
        return "unreachable"
    return str(result)


def _default_zhilian_list_runner(plan_item: dict) -> tuple[str, list[dict], dict | None]:
    """默认 list runner：调用 zhilian_cdp_raw.fetch_list 真实分支。"""
    try:
        from scripts import zhilian_cdp_raw as zha
    except ImportError:
        return "unreachable", [], None
    result = zha.fetch_list(
        plan_item,
        on_page_completed=plan_item.get("on_page_completed"),
    )
    if len(result) == 2:
        signal, jobs = result
        evidence = None
    else:
        signal, jobs, evidence = result
    if signal is None:
        return "invalid_output", [], None
    return str(signal), list(jobs or []), evidence


def _default_zhilian_detail_runner(job: dict, *, detail_output_path: str | None = None) -> tuple[str, dict]:
    """默认 detail runner：调用 zhilian_cdp_raw.fetch_detail。

    zhilian_cdp_raw.fetch_detail 返回真实 signal；
    本函数把 None 转为 "not_found"，不伪造 JD。
    """
    try:
        from scripts import zhilian_cdp_raw as zha
    except ImportError:
        return "unreachable", {}
    signal, detail = zha.fetch_detail(job, detail_output_path=detail_output_path)
    if signal is None:
        return "not_found", {}
    return str(signal), dict(detail or {})


def _default_zhilian_batch_detail_runner(
    list_data: dict, *,
    cdp_port: int, tab_pool_size: int,
    inter_job_gap_range: tuple[float, float], reset_every: int,
) -> tuple[list[tuple[str, dict]], str | None]:
    """默认 batch runner：调用 zhilian_cdp_raw.scrape_details_batch 并行分支。

    返回 ``(per_item, degrade_signal)``；ImportError（环境缺脚本）时全部按
    skipped + unreachable 降级，不伪造成功。
    """
    try:
        from scripts import zhilian_cdp_raw as zha
    except ImportError:
        count = len(list_data.get("jobs", []))
        return [("skipped", {})] * count, "unreachable"
    per_item, degrade_signal = zha.scrape_details_batch(
        list_data,
        cdp_port=cdp_port,
        tab_pool_size=tab_pool_size,
        inter_job_gap_range=inter_job_gap_range,
        reset_every=reset_every,
    )
    normalized = []
    for signal, detail in per_item:
        sig = str(signal or "invalid_output")
        normalized.append((sig, dict(detail or {})))
    return normalized, degrade_signal


def _zhilian_failed_reason(failed_code: str) -> str:
    """智联 failed_code → 用户可读原因（脱敏，不含页面正文/profile 路径）。"""
    reasons = {
        "source_cdp_unavailable": "CDP 端口不可用或 Chrome 未启动",
        "source_login_required": "智联登录态失效，需要重新登录",
        "source_verification_required": "触发 EdgeOne/验证码，需要人工验证",
        "source_rate_limited": "触发智联限流，需要冷却",
        "source_blocked": "智联平台封禁或阻断",
        "source_unreachable": "无法连接智联平台",
        "source_timeout": "智联请求超时",
        "source_not_found": "岗位详情无法取得（可能已下架）",
        "source_invalid_output": "输入校验失败或页面解析异常",
        "source_input_drift": "input_hash 不匹配，计划项已漂移",
        "source_unknown_error": "未知错误",
    }
    return reasons.get(failed_code, "未知错误")


__all__ = [
    "SAFE_FAILURE_CODES",
    "ZHILIAN_DEFAULT_CDP_PORT",
    "BossCdpSource",
    "FakeJobSource",
    "JobSource",
    "SourceCircuitBreaker",
    "SourceOutcome",
    "ZhilianCdpSource",
]
