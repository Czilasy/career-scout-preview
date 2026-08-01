"""Job source adapter (feature 004).

Wraps the BOSS CDP raw scraper into a typed JobSource interface. Returns
SourceOutcome values with typed failures; never leaks raw exception text,
credentials or resume content. Validates input_hash before importing any
artifact so resume/import is rejected on plan item drift.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from scripts import boss_cdp_raw as boss
from webui.process_executor import ScraperExecutor
from webui.workbench import normalize_job_link


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
SCRAPER = PROJECT_ROOT / "scripts" / "boss_cdp_raw.py"

# Valid filter fields passable to the scraper CLI (excludes city which is positional).
SCRAPER_FILTER_FIELDS = ("salary", "experience", "degree", "industry", "scale", "stage")

# The scraper loads optional dependencies lazily for its CLI.  The web adapter
# needs requests available before tests and preflight can patch/use it.
boss.require_runtime_dependencies("requests")


# ---------------------------------------------------------------------------
# Typed source outcomes
# ---------------------------------------------------------------------------


class SourceOutcome:
    """Typed result from a list/detail fetch.

    Attributes:
        ok: whether the fetch produced usable data
        jobs: list of job dicts (list fetch) or single detail dict (detail fetch)
        detail: detail payload when fetching detail
        failed_code: safe failure code when ``ok`` is False; one of
            ``source_unreachable`` / ``source_blocked`` / ``source_not_found``
            / ``source_invalid_output`` / ``source_input_drift``.
        safe_log: safe log line (counts/ids only, no PII/JD body)
    """

    __slots__ = (
        "ok", "jobs", "detail", "failed_code", "safe_log", "input_hash",
        "failed_reason",
    )

    def __init__(
        self,
        *,
        ok: bool,
        jobs: list[dict] | None = None,
        detail: dict | None = None,
        failed_code: str | None = None,
        safe_log: str = "",
        input_hash: str | None = None,
        failed_reason: str = "",
    ):
        self.ok = bool(ok)
        self.jobs = jobs or []
        self.detail = detail or {}
        self.failed_code = failed_code
        self.safe_log = safe_log
        self.input_hash = input_hash
        self.failed_reason = failed_reason

    @classmethod
    def success(cls, *, jobs: list[dict] | None = None, detail: dict | None = None, safe_log: str = "", input_hash: str | None = None) -> "SourceOutcome":
        return cls(ok=True, jobs=jobs, detail=detail, safe_log=safe_log, input_hash=input_hash)

    @classmethod
    def failure(cls, *, failed_code: str, safe_log: str = "", failed_reason: str = "") -> "SourceOutcome":
        return cls(ok=False, failed_code=failed_code, safe_log=safe_log,
                    failed_reason=failed_reason)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "job_count": len(self.jobs),
            "has_detail": bool(self.detail),
            "failed_code": self.failed_code,
            "safe_log": self.safe_log,
            "failed_reason": self.failed_reason,
        }


SAFE_FAILURE_CODES = frozenset({
    "source_cdp_unavailable",
    "source_login_required",
    "source_unreachable",
    "source_blocked",
    "source_not_found",
    "source_invalid_output",
    "source_input_drift",
    "source_timeout",
    "source_unknown_error",
    "source_verification_required",
    "source_rate_limited",
})


# ---------------------------------------------------------------------------
# T075: Source circuit breaker (state-machine.md L92-107)
# ---------------------------------------------------------------------------


class SourceCircuitBreaker:
    """Stateful circuit breaker for source-blocking signals.

    Contract (specs/005-fast-resume-discovery/contracts/state-machine.md
    L92-107):

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

    def is_open(self, *, now: float | None = None) -> bool:
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
        self._runner = runner or self._default_run
        self.breaker = breaker or SourceCircuitBreaker()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def preflight(self) -> SourceOutcome:
        """Check the dedicated Chrome connection and BOSS login once per run."""
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

        if not boss.check_login_state(self.cdp_port):
            return SourceOutcome.failure(
                failed_code="source_login_required",
                safe_log="boss_login_required",
            )
        return SourceOutcome.success(safe_log="source_ready")

    def fetch_list(self, plan_item: dict) -> SourceOutcome:
        """Fetch a job list for one search plan item.

        ``plan_item`` must contain ``keyword``, ``city``, ``source_filters``,
        ``input_hash``, and optionally ``target_pages``.
        """
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
        command = self._build_list_command(keyword, city, target_pages, source_filters, str(output_path))
        safe_log = f"list keyword_present=1 city_present={bool(city)} pages={target_pages}"
        if self.breaker.is_open():
            return SourceOutcome.failure(
                failed_code="source_blocked",
                safe_log=f"{safe_log} breaker_open",
            )
        try:
            returncode, captured = self._runner(command, self.timeout_seconds)
        except subprocess.TimeoutExpired:
            return SourceOutcome.failure(failed_code="source_timeout", safe_log=f"{safe_log} timeout={self.timeout_seconds}")
        except FileNotFoundError:
            return SourceOutcome.failure(failed_code="source_unreachable", safe_log=f"{safe_log} scraper_not_found")
        except OSError as exc:
            return SourceOutcome.failure(failed_code="source_unreachable", safe_log=f"{safe_log} os_error_type={type(exc).__name__}")
        if returncode != 0:
            failed_code = _classify_failed_code(returncode, captured)
            self.breaker.record_signal(failed_code)
            reason = _exit_reason(returncode, captured)
            return SourceOutcome.failure(
                failed_code=failed_code,
                safe_log=f"{safe_log} returncode={returncode} reason={reason}",
            )
        jobs = self._read_jobs(str(output_path))
        if jobs is None:
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
        self.breaker.record_success()
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
            returncode, captured = self._runner(command, self.timeout_seconds)
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
    ) -> dict[str, SourceOutcome]:
        """Fetch details for a batch of jobs (≤5) using one scraper subprocess.

        Returns a mapping of ``job_id -> SourceOutcome``. Each job's outcome
        is built from its atomic detail record (split from the combined
        output by ``job_link``) and the corresponding terminal safe event
        parsed from the events JSONL file.

        Contract (see specs/005-fast-resume-discovery/contracts/state-machine.md
        §Producer / Consumer Boundaries):

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
        - ``source_blocked``: scraper subprocess exited non-zero.
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
            returncode, captured = self._runner(command, self.timeout_seconds)
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

        # 5. Subprocess non-zero exit: no partial results from a failed batch.
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
            ok, reason = self._validate_detail_event(event, expected_urls)
            if not ok:
                continue  # rejected events are silently dropped
            # First valid event for a given job wins; later duplicates are
            # dropped to avoid double-dispatch.
            if event["job_id"] in matched_event_by_url:
                continue
            matched_event_by_url[event["job_id"]] = event
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
                    except (json.JSONDecodeError, ValueError):
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
    ) -> list[str]:
        command = [
            self.python_executable,
            str(self.scraper_path),
            "--keyword", keyword,
            "--city", city or "_",
            "--pages", str(int(target_pages)),
            "--output", output_path,
            "--no-detail",  # list fetch never pulls detail; orchestrator does that
        ]
        for name in SCRAPER_FILTER_FIELDS:
            value = (source_filters or {}).get(name)
            if value:
                command.extend([f"--{name}", str(value)])
        return command

    def _build_detail_command(self, detail_input_path: str, detail_output_path: str) -> list[str]:
        return [
            self.python_executable,
            str(self.scraper_path),
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

    def _default_run(self, command: list[str], timeout: int) -> tuple[int, str]:
        """Run the scraper subprocess and capture stdout+stderr (combined).

        Returns ``(returncode, captured_output)``. Captured output is for
        diagnostic only; never persisted to the database in raw form.
        """
        result = self._executor.execute(
            command, timeout_seconds=timeout, cwd=self.cwd, env=self.env,
            cancel_event=self.cancel_event,
        )
        if result.failure_code == "process_timeout":
            raise subprocess.TimeoutExpired(command, timeout)
        if result.failure_code == "process_unreachable":
            raise FileNotFoundError(command[0])
        return result.returncode if result.returncode is not None else -1, result.output_tail

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
    """

    def __init__(
        self,
        list_jobs: dict[tuple[str, str], list[dict]] | None = None,
        detail_jobs: dict[str, dict] | None = None,
        *,
        list_failures: set[tuple[str, str]] | None = None,
        detail_failures: set[str] | None = None,
        input_hash_seed: str = "fake",
    ):
        self.list_jobs = list_jobs or {}
        self.detail_jobs = detail_jobs or {}
        self.list_failures = list_failures or set()
        self.detail_failures = detail_failures or set()
        self.input_hash_seed = input_hash_seed
        self.list_calls: list[dict] = []
        self.detail_calls: list[dict] = []

    def fetch_list(self, plan_item: dict) -> SourceOutcome:
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
        return SourceOutcome.success(
            jobs=list(jobs),
            safe_log=f"fake list keyword_present=1 city_present={bool(city)} job_count={len(jobs)}",
            input_hash=actual_hash,
        )

    def fetch_detail(self, job: dict, *, detail_output_path: str | None = None) -> SourceOutcome:
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
                safe_log=f"fake detail job_id_present=1 blocked=1",
            )
        detail = self.detail_jobs.get(job_id, {})
        return SourceOutcome.success(detail=detail, safe_log=f"fake detail job_id_present=1 fields={sorted(detail.keys())[:3]}")


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
    return normalized


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
    10: "触发风控/限流（验证码、连续空页或 HTTP 拦截）",
}

# 退出码 + 输出关键词 → 具体 failed_code（不再一律 source_blocked）
_VERIFICATION_KEYWORDS = ("验证码", "滑块", "captcha", "slider", "verify")
_RATE_LIMIT_KEYWORDS = (
    "429", "限流", "rate limit", "too many", "频繁", "稍后再试",
    "解锁", "冻结", "访问受限", "异常流量", "账号受限",
)


def _classify_failed_code(returncode: int, captured: str) -> str:
    """根据退出码和输出文本分类出具体 failed_code。

    退出码含义（boss_cdp_raw.py）：
      1  — 登录态失效或环境异常
      2  — 连不上调试浏览器（CDPUnavailableError）
      10 — 触发风控/限流（RiskControlError：验证码、连续空页、HTTP 拦截）
    """
    if returncode == 2:
        return "source_cdp_unavailable"
    text = (captured or "").lower()
    if returncode == 1:
        if any(kw in text for kw in ("登录", "login", "cookie")):
            return "source_login_required"
        return "source_blocked"
    if returncode == 10:
        if any(kw in text for kw in _RATE_LIMIT_KEYWORDS):
            return "source_rate_limited"
        if any(kw in text for kw in _VERIFICATION_KEYWORDS):
            return "source_verification_required"
        return "source_blocked"
    return "source_blocked"


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


__all__ = [
    "BossCdpSource",
    "FakeJobSource",
    "SourceOutcome",
    "SAFE_FAILURE_CODES",
]
