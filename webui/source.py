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
from pathlib import Path
from typing import Any, Callable

from webui.discovery import DiscoveryError, build_snapshot


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
SCRAPER = PROJECT_ROOT / "scripts" / "boss_cdp_raw.py"


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

    __slots__ = ("ok", "jobs", "detail", "failed_code", "safe_log", "input_hash")

    def __init__(
        self,
        *,
        ok: bool,
        jobs: list[dict] | None = None,
        detail: dict | None = None,
        failed_code: str | None = None,
        safe_log: str = "",
        input_hash: str | None = None,
    ):
        self.ok = bool(ok)
        self.jobs = jobs or []
        self.detail = detail or {}
        self.failed_code = failed_code
        self.safe_log = safe_log
        self.input_hash = input_hash

    @classmethod
    def success(cls, *, jobs: list[dict] | None = None, detail: dict | None = None, safe_log: str = "", input_hash: str | None = None) -> "SourceOutcome":
        return cls(ok=True, jobs=jobs, detail=detail, safe_log=safe_log, input_hash=input_hash)

    @classmethod
    def failure(cls, *, failed_code: str, safe_log: str = "") -> "SourceOutcome":
        return cls(ok=False, failed_code=failed_code, safe_log=safe_log)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "job_count": len(self.jobs),
            "has_detail": bool(self.detail),
            "failed_code": self.failed_code,
            "safe_log": self.safe_log,
        }


SAFE_FAILURE_CODES = frozenset({
    "source_unreachable",
    "source_blocked",
    "source_not_found",
    "source_invalid_output",
    "source_input_drift",
    "source_timeout",
    "source_unknown_error",
})


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
    ):
        self.python_executable = python_executable or sys.executable or "python"
        self.cwd = Path(cwd) if cwd else PROJECT_ROOT
        self.scraper_path = Path(scraper_path) if scraper_path else SCRAPER
        self.env = env or {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", **os.environ}
        self.timeout_seconds = int(timeout_seconds)
        self._runner = runner or self._default_run

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
        source_filters = plan_item.get("source_filters") or {}
        output_path = plan_item.get("list_output_path") or plan_item.get("_list_output_path")
        if not output_path:
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log="plan_item_missing_list_output_path",
            )
        command = self._build_list_command(keyword, city, target_pages, source_filters, str(output_path))
        safe_log = f"list keyword_present=1 city_present={bool(city)} pages={target_pages}"
        try:
            returncode, captured = self._runner(command, self.timeout_seconds)
        except subprocess.TimeoutExpired:
            return SourceOutcome.failure(failed_code="source_timeout", safe_log=f"{safe_log} timeout={self.timeout_seconds}")
        except FileNotFoundError:
            return SourceOutcome.failure(failed_code="source_unreachable", safe_log=f"{safe_log} scraper_not_found")
        except OSError as exc:
            return SourceOutcome.failure(failed_code="source_unreachable", safe_log=f"{safe_log} os_error_type={type(exc).__name__}")
        if returncode != 0:
            return SourceOutcome.failure(
                failed_code="source_blocked",
                safe_log=f"{safe_log} returncode={returncode} stderr_tail_safe={_safe_tail(captured)}",
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
        source_url = str(job.get("source_url") or job.get("url") or "").strip()
        job_id = str(job.get("job_id") or job.get("id") or "").strip()
        if not source_url or not job_id:
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log=f"job_missing_url_or_id job_id_present={bool(job_id)}",
            )
        detail_path = detail_output_path or job.get("_detail_output_path")
        if not detail_path:
            return SourceOutcome.failure(failed_code="source_invalid_output", safe_log="detail_path_missing")
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
            return SourceOutcome.failure(
                failed_code="source_blocked",
                safe_log=f"{safe_log} returncode={returncode} stderr_tail_safe={_safe_tail(captured)}",
            )
        detail = self._read_detail(str(detail_path))
        if detail is None:
            return SourceOutcome.failure(failed_code="source_invalid_output", safe_log=f"{safe_log} detail_not_json")
        if not isinstance(detail, dict):
            return SourceOutcome.failure(failed_code="source_invalid_output", safe_log=f"{safe_log} detail_not_dict")
        return SourceOutcome.success(detail=detail, safe_log=f"{safe_log} fields={sorted(detail.keys())[:5]}")

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
        for name in ("scale", "stage", "salary", "experience", "degree", "industry"):
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

    # ------------------------------------------------------------------
    # Subprocess + artifact reading
    # ------------------------------------------------------------------

    def _default_run(self, command: list[str], timeout: int) -> tuple[int, str]:
        """Run the scraper subprocess and capture stdout+stderr (combined).

        Returns ``(returncode, captured_output)``. Captured output is for
        diagnostic only; never persisted to the database in raw form.
        """
        proc = subprocess.Popen(
            command,
            cwd=str(self.cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=self.env,
        )
        try:
            stdout, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=30)
            raise
        return proc.returncode, stdout or ""

    def _read_jobs(self, output_path: str) -> list[dict] | None:
        path = Path(output_path)
        if not path.is_file():
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
        if not path.is_file():
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


def _safe_tail(text: str, *, max_chars: int = 120) -> str:
    """Return last ``max_chars`` characters, stripped of newlines.

    Used only for safe log lines; the captured subprocess output never
    includes resume text or credentials because the scraper does not
    receive them. We still truncate to keep logs bounded.
    """
    if not text:
        return ""
    tail = text[-max_chars:].replace("\n", " ").replace("\r", " ").strip()
    return tail


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
