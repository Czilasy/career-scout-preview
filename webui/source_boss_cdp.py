"""BOSS CDP source adapter 主体（021 拆分自 webui/source.py）。

BossCdpSource：preflight / recheck_login / fetch_list / fetch_detail 与
CLI 命令构建；批量详情、事件校验与 in-process 翻译见
source_boss_cdp_detail（以 mixin 组装）。共享助手见 source_boss_helpers。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from scripts import boss_cdp_raw as boss
from webui.process_executor import ScraperExecutor
from webui.workbench import normalize_job_link
from webui.source_breaker import SourceCircuitBreaker, SourceOutcome
from webui.source_boss_helpers import (
    _classify_failed_code,
    _exit_reason,
    _input_hash,
    _normalize_job_fields,
    _record_success_signal,
    _safe_host,
    _safe_tail,
    SCRAPER_FILTER_FIELDS,
)
from webui.source_boss_cdp_detail import _BossCdpDetailMixin

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
SCRAPER = PROJECT_ROOT / "scripts" / "boss_cdp_raw.py"

# BOSS 预检真实探测第一次被判 restricted 时，等待该时长后重试一次。
PREFLIGHT_RETRY_DELAY_SECONDS = 10.0


class BossCdpSource(_BossCdpDetailMixin):
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
        self.env = env or {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                           "PYTHONUNBUFFERED": "1", **os.environ}
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

    def _try_breaker_recovery(self) -> bool:
        """开闸且冷却期满时做一次 preflight 探测，通过才复位（020 US1）。

        冷却未满不做探测（避免空耗）；preflight 异常视为探测失败。
        """
        if not self.breaker.is_open() or not self.breaker.cooldown_elapsed():
            return False
        try:
            outcome = self.preflight()
        except Exception:
            return False
        return self.breaker.try_reset(outcome.ok)

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
            # 016：受限不再缓存（登录缓存只存 logged_in/not_logged_in 事实态），
            # 不存在"缓存命中即拦截"；未登录事实命中时也改为真实提示。
            if cached == "not_logged_in":
                return SourceOutcome.failure(
                    failed_code="source_login_required",
                    safe_log="boss_login_required cache=hit",
                )
            if cached == "unknown":
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
        if self.browser_account and state in ("logged_in", "not_logged_in", "unknown"):
            from scripts.login_state_cache import write_login_state
            write_login_state(self.browser_account, "boss", state)
        if state == "logged_in":
            return SourceOutcome.success(safe_log=f"source_ready{cache_note}{retry_note}")
        if state == "restricted":
            # 复探后仍受限 → 确认账号/平台受限（016：独立码，不再借用
            # source_blocked 的"IP 级风控"文案；受限不写缓存）。
            return SourceOutcome.failure(
                failed_code="source_account_restricted",
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
                failed_code="source_account_restricted",
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
        if self.breaker.is_open() and not self._try_breaker_recovery():
            return SourceOutcome.failure(
                failed_code=self.breaker.open_failure_code(),
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
            # 经门面运行时取用：保住 patch("webui.source._record_risk_signals") 面
            from webui import source as _facade
            _facade._record_risk_signals(self.browser_account, self.platform, failed_code, captured,
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
        if self.breaker.is_open() and not self._try_breaker_recovery():
            try:
                Path(detail_input_path).unlink(missing_ok=True)
            except OSError:
                pass
            return SourceOutcome.failure(
                failed_code=self.breaker.open_failure_code(),
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
            # 经门面运行时取用：保住 patch("webui.source._record_risk_signals") 面
            from webui import source as _facade
            _facade._record_risk_signals(self.browser_account, self.platform, failed_code, captured,
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
        simulation_mode: str | None = None,
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
        ``simulation_mode``：024 可选档位（stable/balanced/extreme），非 None 时
        追加 ``--simulation-mode``，详情加载后执行人形模拟；None 时命令与旧版
        字节一致（既有命令断言不破坏）。
        """
        command = [
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
        if simulation_mode:
            command.extend(["--simulation-mode", str(simulation_mode)])
        return command

    # ------------------------------------------------------------------
    # Subprocess + artifact reading
    # ------------------------------------------------------------------
