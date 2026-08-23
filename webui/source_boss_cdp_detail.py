"""BOSS CDP 详情批量/进程内执行 mixin（021 拆分自 webui/source.py）。

BossCdpSource 的批量详情抓取（fetch_details_batch）、终端安全事件校验、
组合产物读取、子进程/in-process 执行入口与产物读取助手。以 mixin 形式
由 webui/source_boss_cdp.BossCdpSource 组装；本模块不定义类 __init__，
实例状态全部来自主体类。
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts import boss_cdp_raw as boss
from webui.process_executor import run_with_deadline
from webui.runtime_audit import record_runtime_event
from webui.task_runners import _classify_risk_control_reason
from webui.workbench import normalize_job_link
from webui.source_breaker import PageEventPersistenceError, SourceCircuitBreaker, SourceOutcome
from webui.source_boss_helpers import (
    SCRAPER_FILTER_FIELDS,
    _classify_failed_code,
    _format_inprocess_failure,
    _safe_tail,
)

logger = logging.getLogger(__name__)


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




class _BossCdpDetailMixin:
    """批量详情、事件校验、in-process 翻译与产物读取（021 拆分）。"""

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
        if self.breaker.is_open() and not self._try_breaker_recovery():
            try:
                Path(batch_input_path).unlink(missing_ok=True)
            except OSError:
                pass
            for job_id in expected_urls_by_job_id:
                results[job_id] = SourceOutcome.failure(
                    failed_code=self.breaker.open_failure_code(),
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
            # 016：登录失效走结构化失败行，不再依赖 captured 关键词
            return (1, _format_inprocess_failure("source_login_required", str(exc)))
        except boss.RiskControlError as exc:
            # 016：优先异常自带 code；缺码时用 reason 兜底分类并把结果写进失败行，
            # 与子进程模式共享同一分类契约
            code = str(getattr(exc, "code", "") or "") or _classify_risk_control_reason(exc.reason)
            return (10, _format_inprocess_failure(code, exc.reason))
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
