#!/usr/bin/env python3
"""Local Flask API for persistent BOSS scraping and explainable job ranking."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import secrets
import sqlite3
import sys
import threading
import uuid
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
FRONTEND_DIST = HERE / "dist"


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
from webui.constants import CLEANUP_EXPIRED_DAYS, FEEDBACK_THRESHOLD, LIST_LIMIT, LOG_TAIL_LINES
from webui.core import build_filter_options, match_jobs, normalize_profile, validate_search_params
from webui.store import SYSTEMIC_BLOCK_CODES, TaskStore
from webui.workbench import (
    allocate_detail_budget,
    MAX_DETAIL_BUDGET,
    normalize_job_link,
    select_keywords,
    project_card,
    aggregate_feedback_state,
    merge_profile_fields,
)
from webui.source import BossCdpSource as _BossCdpSource
from webui.process_executor import ArtifactSpec, ScraperExecutor
from webui import resume as resume_service
from webui import ai as ai_service


_OPERATIONAL_ERRORS = (
    OSError,
    sqlite3.Error,
    RuntimeError,
    ValueError,
    KeyError,
    TypeError,
    ai_service.AISecurityError,
)


_SCRAPE_BLOCK_PATTERNS = (
    ("login_expired", ("登录", "未登录", "wt2", "登 录", "BOSS 登录")),
    ("source_rate_limited", (
        "限流", "频繁", "rate limit", "too many", "稍后再试",
        "解锁", "冻结", "账号受限",
    )),
    ("captcha_required", ("验证码", "滑块", "gtm", "geetest")),
    ("ip_risk_control", ("IP 级风控", "风控", "ip risk", "blocked")),
    ("cdp_unavailable", ("CDP", "调试浏览器", "chrome not ready")),
)


def _classify_scrape_block(err_msg: str) -> str:
    """把 run_search 返回的 error 字符串映射到 SYSTEMIC_BLOCK_CODES。

    命中返回对应码（如 'source_rate_limited'），未命中返回空串（表示真失败，
    不应暂停）。限流优先于验证码，避免“频繁 + 滑块”文案被误显示为验证码。
    """
    if not err_msg:
        return ""
    text = err_msg.lower()
    for code, keywords in _SCRAPE_BLOCK_PATTERNS:
        for kw in keywords:
            if kw.lower() in text:
                return code
    return ""


SCRAPER = PROJECT_ROOT / "scripts" / "boss_cdp_raw.py"
DEFAULT_STATE_DIR = Path(os.environ.get("BOSS_WEBUI_STATE_DIR", os.path.expanduser("~/.career-scout/webui")))


_FINE_VERDICTS = frozenset({"match", "not_match", "mismatch", "uncertain"})


def _split_resume_verdicts(verdicts: dict) -> tuple[dict, dict]:
    """Split stored verdicts into fine-screen and rough-screen verdicts."""
    fine = {}
    rough = {}
    for job_id, verdict in (verdicts or {}).items():
        value = verdict if isinstance(verdict, dict) else {"verdict": str(verdict)}
        target = fine if str(value.get("verdict") or "") in _FINE_VERDICTS else rough
        target[str(job_id)] = value
    return fine, rough


def _resume_dropped_from_verdicts(raw_jobs, verdicts: dict) -> list[dict]:
    """Reconstruct previously dropped jobs when a resume skips rough screening."""
    dropped = []
    for job in raw_jobs or []:
        if not isinstance(job, dict):
            continue
        jid = str(job.get("job_id") or "")
        verdict = verdicts.get(jid) or {}
        if isinstance(verdict, dict) and str(verdict.get("verdict") or "") == "dropped":
            dropped.append({
                "job_id": jid,
                "title": job.get("title") or "",
                "reason": verdict.get("reason") or "粗筛移除",
                "canonical_url": job.get("source_url") or job.get("job_link") or "",
            })
    return dropped


def _iso_epoch_ms(value):
    """Convert an ISO timestamp string (or epoch ms int) to epoch milliseconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return int(parsed.timestamp() * 1000)


def _optional_positive_int(value, field, *, maximum=None):
    """Parse a user-controlled optional execution limit without coercion surprises."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是正整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} 必须是正整数") from None
    if str(value).strip() != str(parsed) or parsed < 1:
        raise ValueError(f"{field} 必须是正整数")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{field} 不能超过 {maximum}")
    return parsed


def _env():
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _read_json(path, default):
    path = Path(path) if path else None
    if not path or not path.is_file():
        return default
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return default


def _request_hostname(host):
    host = str(host or "").lower()
    if host.startswith("[") and "]" in host:
        return host[1:host.index("]")]
    return host.split(":", 1)[0]


def _task_payload(store, task_id):
    task = store.get_task(task_id)
    list_payload = _read_json(task.get("output_path"), {})
    if not isinstance(list_payload, dict):
        list_payload = {}
    jobs = list_payload.get("jobs") if isinstance(list_payload.get("jobs"), list) else []
    details = _read_json(task.get("detail_output_path"), [])
    if not isinstance(details, list):
        details = []
    return task, list_payload, jobs, details


def _mask_key(key: str) -> str:
    """打码 API key：保留前4后4字符，中间星号。短 key 全星号。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


class TaskRunner:
    """Run scraper commands sequentially while persisting state and output."""

    def __init__(self, store, result_dir, python_executable, start_tasks=True):
        self.store = store
        self.result_dir = Path(result_dir)
        self.python_executable = str(python_executable)
        self.start_tasks = bool(start_tasks)
        self._processes = {}
        self._cancel_events = {}
        self._process_lock = threading.Lock()
        self.process_executor = ScraperExecutor()
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self.executor = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="boss-task")
            if self.start_tasks else None
        )

    def create_scrape(self, search, profile):
        task_id = uuid.uuid4().hex[:12]
        output_path = self.result_dir / f"boss_jobs_{task_id}.json"
        detail_path = self.result_dir / f"boss_details_{task_id}.json"
        task = self.store.create_task(
            task_id,
            "scrape",
            {"search": search, "profile": profile},
            output_path=str(output_path),
            detail_output_path=str(detail_path),
        )
        self._submit(task_id)
        return task

    def create_setup_chrome(self):
        task_id = f"setup-{uuid.uuid4().hex[:8]}"
        task = self.store.create_task(task_id, "setup_chrome", {})
        self._submit(task_id)
        return task

    def _submit(self, task_id):
        if self.executor:
            self.executor.submit(self._execute, task_id)

    def cancel(self, task_id):
        task = self.store.get_task(task_id)
        if task["status"] not in {"queued", "running"}:
            raise ValueError(f"只能取消等待中或运行中的任务，当前状态: {task['status']}")
        with self._process_lock:
            process = self._processes.get(task_id)
            cancel_event = self._cancel_events.get(task_id)
        if cancel_event is not None:
            cancel_event.set()
        if process is not None:
            process.terminate()
        self.store.append_log(task_id, "用户取消任务")
        return self.store.update_task(task_id, "interrupted", error="用户取消任务")

    def retry(self, task_id):
        task = self.store.get_task(task_id)
        if task["status"] not in {"failed", "interrupted", "succeeded"}:
            raise ValueError(f"当前任务尚未结束，不能重试: {task['status']}")
        if task["kind"] == "setup_chrome":
            return self.create_setup_chrome()
        return self.create_scrape(task["params"]["search"], task["params"].get("profile", {}))

    def build_command(self, task):
        if task["kind"] == "setup_chrome":
            return [self.python_executable, str(SCRAPER), "--setup-chrome"]
        search = task["params"]["search"]
        command = [
            self.python_executable,
            str(SCRAPER),
            "--keyword", search["keyword"],
            "--city", search["city"],
            "--pages", str(search["pages"]),
            "--output", task["output_path"],
            "--detail-output", task["detail_output_path"],
        ]
        if not search["detail"]:
            command.append("--no-detail")
        if search["analysis"]:
            command.append("--analysis")
        if search["format"] == "csv":
            command.extend(["--format", "csv"])
        for name, value in search["filters"].items():
            command.extend([f"--{name}", value])
        return command

    def validate_artifacts(self, task):
        if task["kind"] != "scrape":
            return
        output_path = Path(task.get("output_path") or "")
        if task["id"] not in output_path.stem or not output_path.is_file():
            raise ValueError("列表产物不存在或不属于当前任务")
        try:
            with output_path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"列表产物解析失败: {exc}") from exc
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list):
            raise ValueError("列表产物缺少 jobs 列表")
        search = task["params"].get("search", {})
        if search.get("detail") and jobs:
            detail_path = Path(task.get("detail_output_path") or "")
            if task["id"] not in detail_path.stem or not detail_path.is_file():
                raise ValueError("详情产物不存在或不属于当前任务")
            try:
                with detail_path.open(encoding="utf-8") as handle:
                    details = json.load(handle)
            except (json.JSONDecodeError, OSError) as exc:
                raise ValueError(f"详情产物解析失败: {exc}") from exc
            if not isinstance(details, list):
                raise ValueError("详情产物必须是 JSON list")

    def _execute(self, task_id):
        try:
            if self.store.get_task(task_id)["status"] != "queued":
                return
            self.store.update_task(task_id, "running")
            task = self.store.get_task(task_id)
            command = self.build_command(task)
            self.store.append_log(task_id, "任务开始")
            cancel_event = threading.Event()
            with self._process_lock:
                self._cancel_events[task_id] = cancel_event
            artifacts = []
            if task["kind"] == "scrape":
                artifacts = [
                    ArtifactSpec(task["output_path"], root=self.result_dir),
                    ArtifactSpec(task["detail_output_path"], root=self.result_dir, required=False),
                ]
            result = self.process_executor.execute(
                command, timeout_seconds=600, cwd=PROJECT_ROOT, env=_env(),
                cancel_event=cancel_event, artifacts=artifacts,
                on_output=lambda chunk: [
                    self.store.append_log(task_id, line)
                    for line in chunk.splitlines() if line.strip()
                ],
            )
            if self.store.get_task(task_id)["status"] == "interrupted":
                return
            if result.ok:
                self.validate_artifacts(task)
                self.store.append_log(task_id, "任务完成")
                self.store.update_task(task_id, "succeeded", returncode=0)
            else:
                message = f"抓取执行失败: {result.failure_code or 'process_failed'}"
                self.store.append_log(task_id, message)
                self.store.update_task(
                    task_id, "failed", returncode=result.returncode if result.returncode is not None else -1,
                    error=message,
                )
        except Exception as exc:
            try:
                self.store.append_log(task_id, f"任务失败：{exc}")
                self.store.update_task(task_id, "failed", returncode=-1, error=str(exc))
            except (KeyError, ValueError) as persist_exc:
                try:
                    current = self.store.get_task(task_id)
                except KeyError as lookup_exc:
                    raise RuntimeError(
                        "task_failure_persistence_failed"
                    ) from lookup_exc
                if current.get("status") not in {
                    "succeeded", "failed", "interrupted",
                }:
                    raise RuntimeError(
                        "task_failure_persistence_failed"
                    ) from persist_exc
                # A concurrent cancellation/completion already committed a terminal
                # state. Preserve that durable winner instead of overwriting it.
        finally:
            with self._process_lock:
                self._processes.pop(task_id, None)
                self._cancel_events.pop(task_id, None)


class WorkbenchRunner(TaskRunner):
    """T007: parent search run + child queries with budget and state machine.

    Reuses TaskRunner infrastructure (process pool, result dir, python
    executable) and adds the workbench-specific run orchestration:
    a parent search_run owns N child run_queries, each with its own
    detail budget slice.  The parent status is derived from child states.
    """

    def create_search_run(self, profile_id, *, keywords, confirmed_fields, mode="ai"):
        """Create parent run + child queries for up to 3 keywords.

        Keywords are already resolved by the caller (manual + AI merge).
        Budget is split evenly across queries with remainder to the first.
        """
        keywords = [str(k).strip() for k in (keywords or []) if str(k).strip()]
        if not keywords:
            raise ValueError("至少需要一个关键词才能创建搜索运行")
        if len(keywords) > 3:
            keywords = keywords[:3]

        profile_snapshot = dict(confirmed_fields or {})
        run = self.store.create_search_run(
            profile_id, profile_snapshot, mode, total_detail_budget=MAX_DETAIL_BUDGET,
        )
        run_id = run["id"]

        budgets = allocate_detail_budget(len(keywords), MAX_DETAIL_BUDGET)
        for ordinal, (keyword, budget) in enumerate(zip(keywords, budgets)):
            list_path = str(self.result_dir / f"list_{run_id}_{ordinal}.json")
            detail_path = str(self.result_dir / f"detail_{run_id}_{ordinal}.json")
            frozen_query = {
                "keyword": keyword, "city": profile_snapshot.get("city", ""),
                "filters": {key: profile_snapshot[key] for key in ("scale", "stage", "salary", "experience", "degree", "industry") if profile_snapshot.get(key)},
            }
            self.store.create_run_query(
                run_id, ordinal, frozen_query, list_path, detail_path, int(budget),
            )
        if self.executor:
            self.executor.submit(self._execute_search_run, run_id)
        return self.store.get_search_run(run_id)

    def _query_command(self, query):
        """Build one bounded invocation of the existing CDP scraper."""
        frozen = query["frozen_query"]
        command = [
            self.python_executable, str(SCRAPER),
            "--keyword", str(frozen["keyword"]),
            "--city", str(frozen["city"]),
            "--output", query["list_output_path"],
            "--detail-output", query["detail_output_path"],
            "--max-details", str(query["detail_budget"]),
        ]
        for name, value in frozen.get("filters", {}).items():
            command.extend([f"--{name}", str(value)])
        return command

    def _read_query_artifacts(self, run_id, query):
        """Read only this run's declared JSON artifacts after checking their paths."""
        root = self.result_dir.resolve()
        paths = [Path(query["list_output_path"]), Path(query["detail_output_path"])]
        for path in paths:
            try:
                resolved = path.resolve()
            except OSError as exc:
                raise ValueError("搜索产物路径无效") from exc
            if root not in resolved.parents or run_id not in resolved.name or not resolved.is_file():
                raise ValueError("搜索产物不存在或不属于当前运行")
        payload = _read_json(paths[0], {})
        details = _read_json(paths[1], [])
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list) or not isinstance(details, list):
            raise ValueError("搜索产物格式无效")
        return payload["jobs"], details

    def _persist_complete_jobs(self, run, query, jobs, details, seen_detail_ids=None):
        """Persist valid completed JDs; the database unique URL enforces run-wide dedupe."""
        seen_detail_ids = seen_detail_ids if seen_detail_ids is not None else set()
        detail_by_id = {
            str(item.get("job_id") or ""): item
            for item in details
            if isinstance(item, dict) and item.get("jd") and str(item.get("job_id") or "") not in seen_detail_ids
        }
        count = 0
        persisted_jobs = []
        for raw in jobs:
            if count >= int(query["detail_budget"]):
                break
            if not isinstance(raw, dict):
                continue
            detail = detail_by_id.get(str(raw.get("job_id") or ""))
            if not detail:
                continue
            excluded = [str(term).lower() for term in run["profile_snapshot"].get("excluded_terms", []) if str(term).strip()]
            searchable = " ".join([str(raw.get("title") or ""), str(raw.get("boss_name") or ""), str(detail.get("jd") or "")]).lower()
            if any(term in searchable for term in excluded):
                continue
            source_url = str(raw.get("job_link") or detail.get("job_link") or "")
            canonical_url = normalize_job_link(source_url)
            if not canonical_url:
                continue
            job = self.store.save_job(
                canonical_url, source_url,
                str(raw.get("title") or ""), str(raw.get("boss_name") or raw.get("company") or ""),
                str(raw.get("salary") or ""), str(raw.get("location") or ""), str(detail.get("jd") or ""),
            )
            self.store.link_profile_job(run["profile_id"], job["id"], run["id"], run["id"])
            self.store.append_search_event(run["id"], "job_completed", {"job_id": job["id"]})
            persisted_jobs.append(job)
            seen_detail_ids.add(str(raw.get("job_id") or ""))
            count += 1
        settings = self.store.get_ai_settings()
        if persisted_jobs and settings.get("is_configured"):
            credential_ref = self.store.get_credential_ref()
            api_key = ai_service.retrieve_api_key(credential_ref) if credential_ref else ""
            if api_key:
                try:
                    ranked_ids = ai_service.rank_jds(
                        run["profile_snapshot"],
                        [{"job_id": job["id"], "title": job["title"], "jd": job["jd"]} for job in persisted_jobs],
                        settings["endpoint_url"], api_key, settings.get("model", ""),
                    )
                    for rank, job_id in enumerate(ranked_ids):
                        self.store.link_profile_job(run["profile_id"], job_id, run["id"], run["id"], ai_rank=rank)
                except (ai_service.AISecurityError, ValueError):
                    # Ranking is optional: valid complete JDs still stream when AI fails.
                    pass
        return count

    def _stream_new_details(self, run_id, query, seen_detail_ids):
        """Read the scraper's atomic detail file while its process is still alive."""
        try:
            jobs, details = self._read_query_artifacts(run_id, query)
        except ValueError:
            return 0
        run = self.store.get_search_run(run_id)
        remaining = max(0, MAX_DETAIL_BUDGET - int(run["completed_jd_count"]))
        if not remaining:
            return 0
        original_budget = query["detail_budget"]
        query = dict(query)
        query["detail_budget"] = min(int(original_budget), remaining)
        return self._persist_complete_jobs(run, query, jobs, details, seen_detail_ids)

    def _execute_search_run(self, run_id):
        """Execute child queries sequentially and persist only validated complete JDs."""
        run = self.store.get_search_run(run_id)
        if run["status"] != "queued":
            return
        self.store.update_search_run(run_id, status="running")
        for query in self.store.list_run_queries(run_id):
            if self.store.get_search_run(run_id)["status"] == "interrupted":
                return
            self.store.update_run_query(query["id"], status="running")
            try:
                cancel_event = threading.Event()
                with self._process_lock:
                    self._cancel_events[run_id] = cancel_event
                seen_detail_ids = set()

                def stream_progress():
                    persisted = self._stream_new_details(run_id, query, seen_detail_ids)
                    if persisted:
                        current = self.store.get_search_run(run_id)
                        self.store.update_search_run(
                            run_id, completed_jd_count=current["completed_jd_count"] + persisted,
                        )
                result = self.process_executor.execute(
                    self._query_command(query), timeout_seconds=600,
                    cwd=PROJECT_ROOT, env=_env(), cancel_event=cancel_event,
                    on_poll=stream_progress,
                    artifacts=[
                        ArtifactSpec(query["list_output_path"], root=self.result_dir),
                        ArtifactSpec(query["detail_output_path"], root=self.result_dir, required=False),
                    ],
                )
                if not result.ok:
                    raise ValueError(result.failure_code or "抓取器执行失败")
                jobs, details = self._read_query_artifacts(run_id, query)
                persisted = self._stream_new_details(run_id, query, seen_detail_ids)
                self.store.update_run_query(query["id"], status="succeeded", counts={"completed_jd": persisted})
                current = self.store.get_search_run(run_id)
                self.store.update_search_run(
                    run_id,
                    discovered_count=current["discovered_count"] + len(jobs),
                    completed_jd_count=current["completed_jd_count"] + persisted,
                )
            except (OSError, ValueError, json.JSONDecodeError):
                self.store.update_run_query(query["id"], status="failed", error_code="scrape_failed")
            finally:
                with self._process_lock:
                    self._processes.pop(run_id, None)
                    self._cancel_events.pop(run_id, None)
        if self.store.get_search_run(run_id)["status"] != "interrupted":
            self._finalize_run(run_id)
        self.store.cleanup_expired_jobs(days=CLEANUP_EXPIRED_DAYS)

    def _finalize_run(self, run_id):
        """Promote parent run to succeeded/partial/failed based on child states."""
        run = self.store.get_search_run(run_id)
        if run["status"] == "queued":
            self.store.update_search_run(run_id, status="running")
        queries = self.store.list_run_queries(run_id)
        if not queries:
            self.store.update_search_run(run_id, status="failed", error_code="no_queries")
            return self.store.get_search_run(run_id)

        states = [q["status"] for q in queries]
        succeeded = sum(1 for s in states if s == "succeeded")
        failed = sum(1 for s in states if s == "failed")
        if succeeded == len(queries):
            new_status = "succeeded"
        elif failed == len(queries):
            new_status = "failed"
        else:
            new_status = "partial"
        return self.store.update_search_run(run_id, status=new_status)

    def cancel_search_run(self, run_id):
        """Mark parent run interrupted; already-written jobs are preserved."""
        run = self.store.get_search_run(run_id)
        if run["status"] not in {"queued", "running"}:
            raise ValueError(f"只能取消等待中或运行中的运行，当前状态: {run['status']}")
        with self._process_lock:
            process = self._processes.get(run_id)
            cancel_event = self._cancel_events.get(run_id)
        if cancel_event is not None:
            cancel_event.set()
        if process is not None:
            process.terminate()
        return self.store.update_search_run(run_id, status="interrupted")


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
        REQUIRE_BUILD_IDENTITY=True,
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

    store = TaskStore(app.config["DB_PATH"])
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
    runner = TaskRunner(
        store,
        app.config["RESULT_DIR"],
        app.config["PYTHON_EXECUTABLE"],
        start_tasks=app.config["START_TASKS"],
    )
    workbench_runner = WorkbenchRunner(
        store,
        app.config["RESULT_DIR"],
        app.config["PYTHON_EXECUTABLE"],
        start_tasks=app.config["START_TASKS"],
    )

    def _make_cdp_source(*, artifact_root=None):
        try:
            return _BossCdpSource(
                python_executable=app.config["PYTHON_EXECUTABLE"],
                artifact_root=artifact_root or app.config["RESULT_DIR"],
            )
        except Exception:
            return None

    Path(app.config["RESUME_DIR"]).mkdir(parents=True, exist_ok=True)
    app.config["TASK_STORE"] = store
    app.config["TASK_RUNNER"] = runner
    app.config["WORKBENCH_RUNNER"] = workbench_runner

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
        # T010: resume reads and AI settings reads also require the session
        # token — they expose private user data even though they are GET.
        path = request.path
        sensitive_get = (
            path.startswith("/api/resumes")
            or path.startswith("/api/ai-settings")
            or path.startswith("/api/advanced-settings")
            or path.startswith("/api/tuning/")
            or path.startswith("/api/profiles/") and "/resumes" in path
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
        return jsonify({
            "error_code": "invalid_request",
            "user_message": str(error),
        }), 400

    @app.errorhandler(KeyError)
    def handle_key_error(error):
        return jsonify({
            "error_code": "not_found",
            "user_message": "任务不存在",
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

    @app.route("/api/options")
    def options():
        cities = [{"label": name, "value": name} for name in boss.CITY_MAP]
        return jsonify({"filters": build_filter_options(), "cities": cities})

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
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "salary": job.get("salary", ""),
                "location": job.get("location", ""),
                "job_link": job.get("source_url") or job.get("canonical_url", ""),
            })
        return jsonify({"items": items, "count": len(items)})

    @app.route("/api/filter-labels")
    def filter_labels():
        """Return field label metadata for the 6 filter chip groups (no resume needed)."""
        return jsonify({"labels": {
            "salary": ("薪资范围", [], boss.SALARY_MAP),
            "experience": ("经验要求", [], boss.EXPERIENCE_MAP),
            "degree": ("学历", [], boss.DEGREE_MAP),
            "industry": ("行业", [], boss.INDUSTRY_MAP),
            "scale": ("公司规模", [], boss.SCALE_MAP),
            "stage": ("融资阶段", [], boss.STAGE_MAP),
        }})

    @app.route("/api/session")
    def session():
        payload = (
            {"token": app.config["API_TOKEN"], "build_hash": _build_hash}
            if app.config.get("TESTING") else {
                "status": "ok", "build_hash": _build_hash,
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
        result = ScraperExecutor(max_output_bytes=64_000).execute(
            [app.config["PYTHON_EXECUTABLE"], str(SCRAPER), "--check"],
            cwd=PROJECT_ROOT, timeout_seconds=30, env=_env(),
        )
        output = "环境检查超时" if result.failure_code == "process_timeout" else result.output_tail
        return jsonify({
            "connected": result.ok,
            "returncode": result.returncode if result.returncode is not None else -1,
            "output": output,
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
            limit = min(LIST_LIMIT, max(1, request.args.get("limit", 30, type=int) or 30))
            return jsonify({"tasks": store.list_tasks(limit=limit)})
        raw = request.get_json(silent=True) or {}
        search = validate_search_params(raw)
        profile_raw = raw.get("profile") if "profile" in raw else store.load_profile()
        normalized_profile = normalize_profile(profile_raw)
        store.save_profile(normalized_profile)
        task = runner.create_scrape(search, normalized_profile)
        return jsonify({"task": task}), 202

    @app.route("/api/scrape", methods=["POST"])
    def legacy_scrape():
        return tasks()

    @app.route("/api/setup-chrome", methods=["POST"])
    def setup_chrome():
        return jsonify({"task": runner.create_setup_chrome()}), 202

    @app.route("/api/tasks/<task_id>")
    def task_detail(task_id):
        task = store.get_task(task_id)
        after = request.args.get("after", 0, type=int)
        task["logs"] = store.get_logs(task_id, after=after)
        return jsonify({"task": task})

    @app.route("/api/tasks/<task_id>/cancel", methods=["POST"])
    def cancel_task(task_id):
        return jsonify({"task": runner.cancel(task_id)})

    @app.route("/api/tasks/<task_id>/retry", methods=["POST"])
    def retry_task(task_id):
        return jsonify({"task": runner.retry(task_id)}), 202

    @app.route("/api/tasks/<task_id>/result")
    def task_result(task_id):
        task, list_payload, jobs, details = _task_payload(store, task_id)
        ranked = match_jobs(jobs, details, task["params"].get("profile"))
        return jsonify({
            "task_id": task_id,
            "keyword": list_payload.get("keyword", task["params"].get("search", {}).get("keyword", "")),
            "city": list_payload.get("city", task["params"].get("search", {}).get("city", "")),
            "total": len(ranked),
            "details": len(details),
            "jobs": ranked,
        })

    @app.route("/api/tasks/<task_id>/summary")
    def task_summary(task_id):
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
        task, _, jobs, details = _task_payload(store, task_id)
        ranked = match_jobs(jobs, details, task["params"].get("profile"))
        columns = [
            "job_id", "eligible", "match_score", "title", "boss_name", "salary",
            "location", "skills", "matched_skills", "missing_skills", "risk_flags", "job_link",
        ]
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for job in ranked:
            row = dict(job)
            for key in ("matched_skills", "missing_skills", "risk_flags"):
                row[key] = " | ".join(row.get(key) or [])
            writer.writerow(row)
        return app.response_class(
            "\ufeff" + buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=boss_jobs_{task_id}.csv"},
        )

    @app.route("/api/results")
    def results():
        result_dir = Path(app.config["RESULT_DIR"])
        files = sorted(result_dir.glob("boss_jobs_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        return jsonify({"files": [path.name for path in files]})

    # == US1: AI settings, profiles, resumes =============================

    def _resolve_credentials_from_request():
        """从请求 body 读取 endpoint_url/api_key/model，缺的用已保存设置兜底。

        供 /api/ai-settings/test 和 /api/ai-settings/models 使用：让用户
        不点"保存设置"也能用当前对话框里填的值直接测试/拉取。返回
        (endpoint_url, api_key, model)；endpoint_url 或 api_key 既不在请求
        里也没有已保存值时抛 ValueError。
        """
        raw = request.get_json(silent=True) or {}
        endpoint_url = str(raw.get("endpoint_url") or "").strip()
        api_key = str(raw.get("api_key") or "").strip()
        model = str(raw.get("model") or "").strip()

        settings = store.get_ai_settings()
        if not endpoint_url:
            endpoint_url = settings.get("endpoint_url") or ""
        if not api_key:
            cred_ref = store.get_credential_ref()
            api_key = ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
        if not model:
            model = settings.get("model", "")

        if not endpoint_url:
            raise ValueError("请先填写 AI 服务 URL")
        if not api_key:
            raise ValueError("API Key 未配置")
        return endpoint_url, api_key, model

    @app.route("/api/ai-settings", methods=["GET", "PUT"])
    def ai_settings():
        if request.method == "GET":
            settings = store.get_ai_settings()
            # 返回打码后的 key 预览（保留首尾，中间星号），供前端展示
            cred_ref = store.get_credential_ref() if settings.get("is_configured") else ""
            real_key = ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
            settings["masked_key"] = _mask_key(real_key) if real_key else ""
            return jsonify(settings)
        raw = request.get_json(silent=True) or {}
        endpoint_url = str(raw.get("endpoint_url") or "").strip()
        api_key = str(raw.get("api_key") or "").strip()
        model = str(raw.get("model") or "").strip()
        if not endpoint_url:
            raise ValueError("endpoint_url 不能为空")
        # key 为空时尝试复用已存的 key（允许只改 model 不重填 key）
        if not api_key:
            existing_ref = store.get_credential_ref()
            api_key = ai_service.retrieve_api_key(existing_ref) if existing_ref else ""
            if not api_key:
                raise ValueError("api_key 不能为空（尚未保存过 key）")
        credential_ref = ai_service.store_api_key(endpoint_url, api_key)
        settings = store.save_ai_settings(
            endpoint_url, credential_ref, status="unconfigured", model=model,
        )
        return jsonify(settings)

    @app.route("/api/ai-settings/test", methods=["POST"])
    def ai_settings_test():
        # 优先用请求 body 里当前对话框填的值；缺的再用已保存设置兜底
        endpoint_url, api_key, model = _resolve_credentials_from_request()
        capability = ai_service.test_connection(endpoint_url, api_key, model=model)
        new_status = "ready" if capability["ok"] else "failed"
        error_code = capability["warning_codes"][0] if not capability["ok"] and capability["warning_codes"] else None
        store.update_ai_status(new_status, last_error_code=error_code)
        return jsonify(capability)

    @app.route("/api/ai-settings/models", methods=["POST"])
    def ai_settings_models():
        """拉取可用模型列表。前端持 key 不安全，由后端代理 GET /models。

        改成 POST：前端可把当前对话框里填的 endpoint_url/api_key/model
        放进 body，不必先点"保存设置"就能拉取。body 里缺的字段回退到
        已保存设置（与 /test 路由一致）。
        """
        endpoint_url, api_key, _model = _resolve_credentials_from_request()
        try:
            models = ai_service.list_models(endpoint_url, api_key)
        except ai_service.AISecurityError as exc:
            # 语义修正：AISecurityError 是失败，不应返回 200。
            # 前端 fetchModels 通过 response.ok 判断，502 不影响行为。
            return jsonify({"ok": False, "error_code": exc.error_code, "models": []}), 502
        return jsonify({"ok": True, "models": models})

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
            # 先删该画像下的简历物理文件，再删画像行（CASCADE 清关联表）
            resumes = store.list_resumes(profile_id)
            for r in resumes:
                if r.get("deleted_at"):
                    continue
                resume_service.delete_resume(
                    r["id"], store, resume_dir=app.config["RESUME_DIR"],
                )
            return jsonify(store.delete_profile(profile_id))
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
                except Exception as exc:
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
        profile_id = raw.get("profile_id")
        if not profile_id:
            raise ValueError("profile_id 不能为空")
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
        run = store.get_search_run(run_id)
        run["queries"] = store.list_run_queries(run_id)
        run["events"] = store.list_search_events(run_id, after=request.args.get("after_event_id", 0, type=int))
        return jsonify(run)

    @app.route("/api/search-runs/<run_id>/cancel", methods=["POST"])
    def cancel_search_run(run_id):
        return jsonify(workbench_runner.cancel_search_run(run_id))

    @app.route("/api/search-runs/<run_id>/jobs")
    def search_run_jobs(run_id):
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
            feedback_events = store.list_feedback(profile_id, job_id=pj["job_id"])
            interest_state = aggregate_feedback_state(feedback_events) if feedback_events else pj["status"]
            if interest_state == "not_interested":
                continue
            cards.append(project_card(job, job, interest_state=interest_state))
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
        store.revoke_feedback(feedback_id)
        return jsonify({"revoked": True})

    # == US4: history, favorites, cleanup ================================

    @app.route("/api/profile-jobs")
    def list_profile_jobs():
        profile_id = request.args.get("profile_id")
        if not profile_id:
            raise ValueError("profile_id 不能为空")
        status = request.args.get("status")
        run_id = request.args.get("run_id")
        jobs = store.list_profile_jobs(profile_id, status=status, run_id=run_id)
        return jsonify({"jobs": jobs})

    @app.route("/api/profile-jobs/<profile_id>/<job_id>", methods=["PATCH"])
    def patch_profile_job(profile_id, job_id):
        raw = request.get_json(silent=True) or {}
        allowed = {k: raw[k] for k in ("status", "note", "applied_at") if k in raw}
        return jsonify(store.update_profile_job(profile_id, job_id, **allowed))

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
    _pipeline_lock = threading.Lock()
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
        from webui.tuning import TuningController

        controller = TuningController(store)
        record = store.get_task_manifest(manifest_id)
        manifest = record["manifest"]
        round_id = record["round_id"]
        sink = controller.build_measurement_sink(round_id)

        def measured(*args, **kwargs):
            controller.heartbeat_lease()
            return sink(*args, **kwargs)

        error_code = None
        try:
            result = _tuning_round_runner.execute(
                manifest, measurement_callback=measured,
            )
        except (
            OSError, RuntimeError, ValueError, KeyError, TypeError,
            ai_service.AISecurityError,
        ) as exc:
            result = None
            error_code = (
                exc.error_code
                if isinstance(exc, ai_service.AISecurityError)
                else getattr(exc, "error_code", type(exc).__name__.lower())
            )
        if isinstance(result, dict):
            controller.persist_stage_artifact(
                round_id=round_id,
                stage=manifest["round_kind"],
                payload=result,
                source_artifact_id=manifest["frozen_input"].get(
                    "source_artifact_id"
                ),
            )
        summary = controller.aggregate_measurements(round_id)
        if not summary.get("input_count") and isinstance(result, dict):
            jobs = result.get("jobs")
            verdicts = result.get("verdicts")
            if isinstance(jobs, list):
                summary["input_count"] = len(jobs)
                summary["terminal_count"] = len(jobs)
                summary["success_count"] = len(jobs)
            elif isinstance(verdicts, dict):
                summary["input_count"] = len(verdicts)
                summary["terminal_count"] = len(verdicts)
                summary["success_count"] = len(verdicts)
        summary["work_duration_ms"] = (
            summary["total_duration_ms"] - summary["wait_duration_ms"]
            - summary["retry_duration_ms"]
        )
        evidence_path = manifest["monitoring"]["final_artifact_path"]
        evidence = {
            "program_report_path": evidence_path,
            "config_digest": manifest["execution_config"]["config_digest"],
            "scope_digest": manifest["frozen_input"]["scope_digest"],
            "input_artifact_digest": manifest["frozen_input"].get("artifact_digest"),
            **summary,
        }
        if error_code:
            evidence["error_counts"] = {
                **evidence.get("error_counts", {}), error_code: 1,
            }
        absolute = (Path(store.db_path).resolve().parent.parent / evidence_path).resolve()
        expected_root = (
            Path(store.db_path).resolve().parent.parent / "tuning"
            / manifest["experiment_id"]
        ).resolve()
        if expected_root not in absolute.parents:
            raise ValueError("程序证据输出路径越过实验根目录")
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_text(json.dumps(
            evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ), encoding="utf-8")
        controller._save_round_metrics(round_id, evidence)
        store.update_tuning_round_status(
            round_id, status="reported", failure_code=error_code,
        )

    app.config["RUN_TUNING_MANIFEST_CHILD"] = _run_tuning_manifest_child

    def _new_pipeline_task(task_id, kind, *, source_task_id=None):
        task = {
            "kind": kind,
            "status": "queued",
            "progress": {},
            "logs": [],
            "result": None,
            "error": "",
            # 计时：任务创建即记开始，终态时补结束（前端计时器从快照读取，
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
            task_id, kind, source_task_id=source_task_id
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
            task = _new_pipeline_task(task_id, "recrawl")
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

    def _claim_pipeline_task_id(task_id, kind):
        """Atomically reserve a concrete task id for continuation."""
        with _pipeline_lock:
            previous = _pipeline_tasks.get(task_id)
            if previous is not None and previous.get("status") in (
                "queued", "running",
            ):
                return None, previous
            task = _new_pipeline_task(task_id, kind)
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

    def _schedule_pipeline_task_cleanup(task_id):
        """30 分钟后自动从 _pipeline_tasks 中移除已完成的任务，避免内存泄漏。"""
        def _cleanup():
            with _pipeline_lock:
                _pipeline_tasks.pop(task_id, None)
        timer = threading.Timer(30 * 60, _cleanup)
        timer.daemon = True
        timer.start()

    app.config["SCHEDULE_PIPELINE_CLEANUP"] = _schedule_pipeline_task_cleanup

    app.config["SCHEDULE_PIPELINE_TASK_CLEANUP"] = _schedule_pipeline_task_cleanup

    # -----------------------------------------------------------------------
    # AI 筛选阶段百分比与文案
    # -----------------------------------------------------------------------
    _SCREEN_STAGE_WEIGHTS: dict[str, tuple[int, int]] = {
        "resume": (0, 0),
        "screen_a": (0, 35),
        "screen_a_done": (35, 35),
        "ensure_chrome": (35, 40),
        "fetch_jd": (40, 75),
        "screen_b": (75, 100),
        "done": (100, 100),
    }

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

    def _screen_overall_percent(stage: str, current: int, total: int) -> int:
        """把 AI 筛选 pipeline 的当前阶段映射到整体百分比（0-100）。"""
        start, end = _SCREEN_STAGE_WEIGHTS.get(stage, (0, 100))
        if total <= 0:
            return start
        ratio = min(1.0, max(0.0, current / total))
        return min(100, round(start + (end - start) * ratio))


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

    def _activate_run_browser(run=None) -> None:
        """Point the shared CDP helper at the selected profile."""
        from webui.pipeline_exec import set_active_cdp_data_dir
        set_active_cdp_data_dir(_account_for_run(run))

    def _activate_task_browser(task_id: str) -> None:
        """Use the account captured when the task was submitted, if present."""
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id) or {}
            account = str(task.get("browser_account") or "")
        from webui.pipeline_exec import resolve_browser_account, set_active_cdp_data_dir
        profile_dir = resolve_browser_account(
            account, app.config["BROWSER_ACCOUNTS_PATH"])
        if profile_dir:
            set_active_cdp_data_dir(profile_dir)
        else:
            _activate_run_browser()

    def _ensure_scrape_source(scrape_task_id: str) -> dict | None:
        """Return a scrape source snapshot, rebuilding it from DB after a restart."""
        with _pipeline_lock:
            source_task = _pipeline_tasks.get(scrape_task_id)
            if source_task is not None:
                return dict(source_task)
        source_jobs = store.load_scrape_run_jobs(scrape_task_id)
        if not source_jobs:
            return None
        try:
            source_run = store.get_screening_run(scrape_task_id)
        except _OPERATIONAL_ERRORS:
            source_run = None
        if source_run is None or source_run.get("status") != "succeeded":
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
        }
        with _pipeline_lock:
            _pipeline_tasks[scrape_task_id] = snapshot
        return dict(snapshot)

    def _scrape_completed_for_run(execution_params: dict) -> bool:
        """Infer whether the source scrape run finished after a service restart."""
        if bool(execution_params.get("scrape_completed")):
            return True
        scrape_task_id = str(execution_params.get("scrape_task_id") or "")
        if not scrape_task_id:
            return False
        try:
            source_run = store.get_screening_run(scrape_task_id)
        except _OPERATIONAL_ERRORS:
            return False
        return bool(source_run and source_run.get("status") == "succeeded")

    def _check_resume_block(run: dict) -> tuple[bool, str, str]:
        """Verify the paused dependency before submitting resumed work."""
        _activate_run_browser(run)
        checker = app.config.get("RESUME_BLOCK_CHECKER")
        if callable(checker):
            passed, code, reason = checker(run)
        else:
            code = str(run.get("error_code") or "")
            reason = ""
            passed = True
            try:
                if code in {
                    "captcha_required", "login_expired", "ip_risk_control",
                    "cdp_unavailable", "source_verification_required",
                    "source_login_required", "source_blocked", "source_rate_limited",
                    "source_cdp_unavailable",
                }:
                    from webui.pipeline_exec import ensure_chrome_ready, ERROR_TAXONOMY
                    chrome_ok, chrome_err = ensure_chrome_ready()
                    if not chrome_ok:
                        passed = False
                        code = "cdp_unavailable"
                        reason = f"调试浏览器尚未就绪：{chrome_err}"
                    else:
                        source = _make_cdp_source()
                        outcome = source.preflight() if source is not None else None
                        if outcome is None or not outcome.ok:
                            passed = False
                            source_code = getattr(outcome, "failed_code", "")
                            code = {
                                "source_login_required": "login_expired",
                                "source_cdp_unavailable": "cdp_unavailable",
                                "source_verification_required": "captcha_required",
                            }.get(source_code, code or "source_blocked")
                            reason = ERROR_TAXONOMY.get(code, {}).get(
                                "reason", "BOSS 阻断条件尚未解除"
                            )
                elif code in {
                    "ai_key_invalid", "ai_quota_exhausted",
                    "ai_rate_limited", "ai_network_error",
                }:
                    from webui.pipeline_exec import ERROR_TAXONOMY
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
                            reason = ERROR_TAXONOMY.get(code, {}).get(
                                "reason", "AI 阻断条件尚未解除"
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
            source_run_id: str = "") -> None:
        """Persist per-job JD failures before a systemic pause returns."""
        from webui.pipeline_exec import ERROR_TAXONOMY, _FAILED_CODE_LABELS

        target_run_ids = [str(task_run_id)]
        if source_run_id and str(source_run_id) not in target_run_ids:
            target_run_ids.append(str(source_run_id))
        events = []
        source_code_aliases = {
            "source_login_required": "login_expired",
            "source_verification_required": "captcha_required",
            "source_cdp_unavailable": "cdp_unavailable",
        }
        for job in jobs or []:
            if not isinstance(job, dict) or str(job.get("jd") or "").strip():
                continue
            job_id = str(job.get("job_id") or job.get("id") or "").strip()
            failed_code = str(job.get("jd_failed_code") or "").strip()
            if not job_id or not failed_code:
                continue
            taxonomy_code = source_code_aliases.get(failed_code, failed_code)
            taxonomy = ERROR_TAXONOMY.get(taxonomy_code, {})
            reason = str(job.get("jd_failed_reason") or "").strip()
            if not reason:
                reason = str(
                    taxonomy.get("reason")
                    or _FAILED_CODE_LABELS.get(failed_code)
                    or "JD 抓取失败"
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
                        "next_action": "retry_jd",
                    },
                    failed_code=failed_code,
                )
            events.append(("job_fail", {
                "stage": stage,
                "job_id": job_id,
                "failed_code": failed_code,
                "reason": reason,
            }))
        if events:
            store.append_task_events(task_run_id, events)

    def _run_pipeline_task(
        task_id, script_params, execution_config=None, frozen_scope=None,
    ):
        from webui.pipeline_exec import expand_combinations, run_search
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)
            if task is None:
                task = {
                    "kind": "scrape", "status": "queued", "progress": {},
                    "logs": [], "result": None, "error": "",
                    "started_at": int(time.time() * 1000), "finished_at": None,
                }
                _pipeline_tasks[task_id] = task
            task["status"] = "running"
            task["script_params"] = script_params  # 断点续抓需要原始参数
            if execution_config is not None:
                task["config_digest"] = execution_config.config_digest
            if frozen_scope is not None:
                task["scope_digest"] = frozen_scope.scope_digest
        _activate_task_browser(task_id)

        def on_progress(snapshot):
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is None:
                    return
                task["progress"] = snapshot
                msg = snapshot.get("message")
                if msg:
                    task["logs"].append(msg)

        try:
            # 取出停止信号传给 run_search；cancel 接口 set 它后，
            # run_search 会在下一个组合边界退出，或因浏览器被关而抛错。
            with _pipeline_lock:
                task_ref = _pipeline_tasks.get(task_id, {})
                stop_event = task_ref.get("stop_event")
                skip_combos = task_ref.get("skip_combos") or None
                old_jobs = task_ref.get("old_jobs") or []
            if store.get_screening_run(task_id) is None:
                store.create_screening_run(
                    task_id,
                    source_count=len(expand_combinations(script_params)),
                    execution_params={
                        "script_params": script_params,
                        "browser_account": _account_for_run(),
                        "execution_config": (
                            execution_config.to_dict()
                            if execution_config is not None else None
                        ),
                        "frozen_scope": (
                            frozen_scope.to_dict() if frozen_scope is not None else None
                        ),
                    },
                    backend_version=_backend_version,
                )
            store.update_screening_run(
                task_id, status="running", current_stage="scrape"
            )
            store.append_task_event(task_id, "stage_start", {"stage": "scrape"})
            source = _make_cdp_source()
            if source is None:
                completed = sorted(skip_combos or [])
                reason = "连不上调试浏览器，请启动 Chrome 调试端口后继续"
                store.update_screening_run(
                    task_id,
                    status="paused",
                    current_stage="scrape",
                    error_code="cdp_unavailable",
                    error_reason=reason,
                    processed_count=len(completed),
                )
                store.save_checkpoint(task_id, "scrape", completed)
                store.append_task_event(task_id, "pause", {
                    "stage": "scrape",
                    "code": "cdp_unavailable",
                    "completed_combos": len(completed),
                })
                with _pipeline_lock:
                    task = _pipeline_tasks.get(task_id)
                    if task is not None:
                        task["status"] = "paused"
                        task["error"] = reason
                return

            def on_combo_done(combo_key, jobs, completed_combos):
                store.save_scrape_combo_result(
                    task_id, combo_key, jobs, completed_combos
                )
                store.append_task_events(task_id, [
                    ("job_success", {
                        "stage": "scrape", "combo_key": combo_key,
                        "job_id": str(job.get("job_id") or job.get("source_url") or ""),
                    })
                    for job in jobs if isinstance(job, dict)
                ])

            result = run_search(
                script_params, source,
                pages=(
                    frozen_scope.pages_per_combination
                    if frozen_scope is not None else int(script_params.get("pages") or 3)
                ), progress=on_progress,
                artifact_dir=app.config["RESULT_DIR"],
                stop_event=stop_event,
                skip_combos=skip_combos,
                on_combo_done=on_combo_done,
                execution_config=execution_config,
            )
            # 断点续抓：合并旧结果（按 job_id 去重）
            if old_jobs and result.get("ok"):
                existing_ids = {j.get("job_id") or j.get("source_url") or ""
                                for j in result["jobs"]}
                for job in old_jobs:
                    jid = job.get("job_id") or job.get("source_url") or ""
                    if jid and jid not in existing_ids:
                        result["jobs"].append(job)
                        existing_ids.add(jid)
                result["total_matched"] = len(result["jobs"])
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    task["result"] = result
                    task["error"] = result.get("error", "")
                    # 用户点过停止：无论 run_search 返回 ok 与否，都标 cancelled，
                    # 不标 failed（不是出错）也不标 done（不是正常完成）。
                    if stop_event is not None and stop_event.is_set():
                        task["status"] = "cancelled"
                        task["error"] = "用户已停止抓取"
                        store.update_screening_run(
                            task_id, status="cancelled", current_stage="scrape",
                            processed_count=len(result.get("completed_combos") or []),
                            error_reason="用户已停止抓取",
                        )
                    elif result.get("ok"):
                        task["status"] = "done"
                        completed = list(result.get("completed_combos") or [])
                        store.update_screening_run(
                            task_id, status="succeeded", current_stage="scrape",
                            processed_count=len(completed),
                            source_count=int(result.get("combinations") or len(completed)),
                            total_scraped=int(result.get("total_scraped") or 0),
                        )
                        store.append_task_event(
                            task_id, "stage_complete", {"stage": "scrape"}
                        )
                    else:
                        # 切片4：列表抓取失败时区分"系统性阻断暂停" vs "真失败"
                        # 部分组合已完成 + 错误含阻断关键字 → paused + checkpoint
                        # 服务重启后用户点继续可从 DB checkpoint 恢复 completed_combos
                        completed = list(result.get("completed_combos") or [])
                        err_msg = str(result.get("error", "") or "")
                        _pause_code = (
                            str(result.get("hard_stop_code") or "")
                            or _classify_scrape_block(err_msg)
                        )
                        if result.get("hard_stop") and _pause_code:
                            store.update_screening_run(
                                task_id, status="paused", error_code=_pause_code,
                                current_stage="scrape",
                                processed_count=len(completed),
                                source_count=int(result.get("combinations") or 0),
                                error_reason=err_msg,
                                total_scraped=int(result.get("total_scraped") or 0))
                            store.save_checkpoint(task_id, "scrape", completed)
                            store.append_task_event(
                                task_id, "pause",
                                {"stage": "scrape", "code": _pause_code,
                                 "completed_combos": len(completed)})
                            task["status"] = "paused"
                            task["error"] = (
                                f"列表抓取被阻断（{_pause_code}）："
                                f"已完成 {len(completed)} 个组合，已保存断点。"
                                "在自动化浏览器中处理后点「继续」"
                            )
                        else:
                            task["status"] = "failed"
                            store.append_task_event(task_id, "job_fail", {
                                "stage": "scrape", "error": err_msg,
                                "failed_code": _pause_code or "scrape_failed",
                            })
                            store.update_screening_run(
                                task_id, status="failed", current_stage="scrape",
                                processed_count=len(completed),
                                source_count=int(result.get("combinations") or 0),
                                error_reason=err_msg,
                                total_scraped=int(result.get("total_scraped") or 0),
                            )
            _schedule_pipeline_task_cleanup(task_id)
        except Exception as exc:
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                stop_event = task.get("stop_event") if task is not None else None
            cancelled = stop_event is not None and stop_event.is_set()
            error_message = (
                "用户已停止抓取" if cancelled
                else f"执行异常：{type(exc).__name__}"
            )
            persistence_error = None
            try:
                run = store.get_screening_run(task_id)
                if run and run.get("status") in ("queued", "running", "paused"):
                    store.update_screening_run(
                        task_id,
                        status="cancelled" if cancelled else "failed",
                        current_stage="scrape",
                        error_reason=error_message,
                    )
            except _OPERATIONAL_ERRORS as persist_exc:
                persistence_error = type(persist_exc).__name__
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    task["status"] = (
                        "cancelled" if cancelled and persistence_error is None else "failed"
                    )
                    task["error"] = (
                        error_message if persistence_error is None
                        else f"{error_message}；状态保存失败：{persistence_error}"
                    )
            _schedule_pipeline_task_cleanup(task_id)

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
            source_jobs, verdicts, pending_rows, jd_map, profile_summary):
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
            reason = str(vobj.get("reason") or "")
            if verdict == "dropped":
                dropped.append({
                    "job_id": jid,
                    "title": job.get("title") or "", "reason": reason or "粗筛移除",
                    "canonical_url": job.get("source_url") or job.get("job_link") or "",
                })
                continue
            jd = str(jd_map.get(jid) or job.get("jd") or "").strip()
            caveats = vobj.get("caveats") if isinstance(vobj.get("caveats"), list) else []
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
                "job_id": jid,
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
                "failed_code": pending_codes.get(jid) or "",
            })
        return {
            "ok": True,
            "jobs": jobs,
            "dropped": dropped,
            "total_scraped": len(source_jobs or []),
            "total_kept": len(jobs),
            "total_matched": sum(1 for j in jobs if j.get("verdict") == "match"),
            "total_dropped": len(dropped),
            "profile_summary": profile_summary or "",
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

    def _cleanup_stale_jd_checkpoints(result_dir, *, max_age_days=30):
        """兜底清理超龄 JD 断点文件（任务异常残留/文件漏删），启动时跑一次。"""
        try:
            cutoff = time.time() - max_age_days * 86400
            for name in os.listdir(result_dir):
                if not (name.startswith("ai_screen_jd_") and name.endswith(".json")):
                    continue
                full = os.path.join(result_dir, name)
                try:
                    if os.path.getmtime(full) < cutoff:
                        os.unlink(full)
                except OSError:
                    continue
        except OSError:
            pass

    def _run_ai_screen_task(task_id, screening_fields, profile_summary,
                            scrape_task_id, resume_from_run_id=""):
        """AI 筛选任务：StageA 字段粗筛 → 批量抓 JD → StageB JD 精筛。

        读取最近一次原始抓取结果，两段式 AI 筛选后把带 verdict 的最终结果
        持久化到数据库（供结果页恢复）。

        全程进度落库（screening_runs）+ 中间产物落盘（JD 断点文件 /
        screening_results 判定）：进程重启或失败后，同一抓取任务再次发起
        筛选且条件一致时自动接着上次进度（``resume_from_run_id``）。
        """
        from webui.pipeline_exec import ensure_chrome_ready, close_debug_chrome, fetch_job_details, _FAILED_CODE_LABELS
        from webui.ai import screen_jobs, match_jds

        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)
            if task is None:
                task = {
                    "kind": "ai_screen", "status": "queued", "progress": {},
                    "logs": [], "result": None, "error": "",
                    "source_task_id": scrape_task_id,
                    "started_at": int(time.time() * 1000), "finished_at": None,
                    "stop_event": threading.Event(),
                }
                _pipeline_tasks[task_id] = task
            if task.get("status") == "cancelled":
                # 排队期间已被用户取消：直接退出，别把 cancelled 覆盖成 running
                return
            task["status"] = "running"
            stop_event = task.get("stop_event")

        _activate_task_browser(task_id)

        last_event_stage = None

        def emit(**kw):
            nonlocal last_event_stage
            stage = str(kw.get("stage", ""))
            current = int(kw.get("current") or 0)
            total = int(kw.get("total") or 0)
            kw["overall_percent"] = _screen_overall_percent(stage, current, total)
            # 调用方传了具体 message（如"筛选完成：匹配 X 条"）就优先用；没传才回退默认文案
            if not kw.get("message"):
                kw["message"] = _SCREEN_STAGE_MESSAGES.get(stage, "")
            event_stage = _EVENT_STAGE_NAMES.get(stage)
            stage_events = []
            if stage == "done" and last_event_stage:
                stage_events.append(("stage_complete", {"stage": last_event_stage}))
                last_event_stage = None
            elif event_stage and event_stage != last_event_stage:
                if last_event_stage:
                    stage_events.append(("stage_complete", {"stage": last_event_stage}))
                stage_events.append(("stage_start", {"stage": event_stage}))
                last_event_stage = event_stage
            if stage_events:
                store.append_task_events(task_id, stage_events)
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is None:
                    return
                task["progress"] = kw
                msg = kw.get("message")
                if msg:
                    task["logs"].append(msg)

        def _stop_requested():
            return stop_event is not None and stop_event.is_set()

        def _mark_cancelled():
            """用户取消：标 cancelled（不覆盖为 done/failed），落清理定时。"""
            store.update_screening_run(
                task_id, status="cancelled", error_reason="用户已停止筛选"
            )
            with _pipeline_lock:
                t = _pipeline_tasks.get(task_id)
                if t is not None:
                    t["status"] = "cancelled"
                    t["error"] = "用户已停止筛选"
            _schedule_pipeline_task_cleanup(task_id)

        try:
            # 1) 只读取请求明确绑定的抓取任务，避免另一标签页或另一轮抓取
            # 覆盖全局 latest 文件后导致筛选对象串线。
            with _pipeline_lock:
                source_task = _pipeline_tasks.get(scrape_task_id)
                source_result = (
                    source_task.get("result")
                    if isinstance(source_task, dict) else None
                )
            if not isinstance(source_result, dict):
                raise RuntimeError("invalid_scrape_task")
            source_run = store.get_screening_run(scrape_task_id)
            source_params = (
                source_run.get("execution_params")
                if isinstance(source_run, dict) else None
            ) or {}
            from webui.execution_config import (
                ExecutionConfigSnapshot, FrozenTaskScope,
            )
            try:
                execution_config = ExecutionConfigSnapshot.from_dict(
                    source_params["execution_config"]
                )
                frozen_scope = FrozenTaskScope.from_dict(
                    source_params["frozen_scope"]
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("frozen_snapshot_missing") from exc
            with _pipeline_lock:
                task["config_digest"] = execution_config.config_digest
                task["scope_digest"] = frozen_scope.scope_digest
            # 列表抓取遇到系统性阻断（验证码/登录失效/IP风控）：暂停，不标 failed
            if source_result.get("hard_stop"):
                _hs_code = source_result.get("hard_stop_code") or "source_blocked"
                _completed_combos = source_result.get("completed_combos") or []
                store.create_screening_run(
                    task_id,
                    frozen_filters=screening_fields,
                    source_count=len(source_result.get("jobs") or []),
                    execution_params={"scrape_task_id": scrape_task_id,
                                      "profile_summary": profile_summary or "",
                                      "browser_account": _account_for_run(),
                                      "execution_config": execution_config.to_dict(),
                                      "frozen_scope": frozen_scope.to_dict()},
                    backend_version=_backend_version)
                store.update_screening_run(
                    task_id, status="running", current_stage="scrape"
                )
                store.update_screening_run(
                    task_id, status="paused", error_code=_hs_code,
                    current_stage="scrape")
                # 保存已完成组合 checkpoint（继续时跳过）
                store.save_checkpoint(task_id, "scrape", _completed_combos)
                store.append_task_event(
                    task_id, "pause",
                    {"stage": "scrape", "code": _hs_code,
                     "completed_combos": len(_completed_combos),
                     "total_combos": source_result.get("combinations") or 0})
                with _pipeline_lock:
                    t = _pipeline_tasks.get(task_id)
                    if t is not None:
                        t["status"] = "paused"
                        t["error"] = (
                            f"列表抓取被阻断（{_hs_code}）："
                            f"已完成 {len(_completed_combos)}/"
                            f"{source_result.get('combinations') or 0} 个组合。"
                            "处理完成后点「继续」"
                        )
                return
            if not source_result.get("ok"):
                raise RuntimeError("invalid_scrape_task")
            raw_jobs = [
                dict(job) for job in source_result.get("jobs", [])
                if isinstance(job, dict)
            ]
            if not raw_jobs:
                raise RuntimeError("empty_scrape_result")

            # 任务身份和断点必须先可靠落库；否则继续调用 AI 会产生无法恢复、
            # 无法审计的孤儿工作。
            if resume_from_run_id != task_id:
                store.create_screening_run(
                    task_id,
                    frozen_filters=screening_fields,
                    source_count=len(raw_jobs),
                    execution_params={
                        "scrape_task_id": scrape_task_id,
                        "profile_summary": profile_summary,
                        "browser_account": _account_for_run(),
                        "execution_config": execution_config.to_dict(),
                        "frozen_scope": frozen_scope.to_dict(),
                    },
                    backend_version=_backend_version,
                )
                store.update_screening_run(
                    task_id, status="running", current_stage="ai_rough"
                )
            else:
                resumed_run = store.get_screening_run(task_id)
                if resumed_run is None or resumed_run.get("status") != "running":
                    raise RuntimeError("resume_run_not_claimed")

            # 载入断点（同一抓取任务、同一筛选条件下的上次进度）
            resume_verdicts = {}
            resume_jd = {}
            if resume_from_run_id:
                resume_verdicts = store.load_screening_verdicts(resume_from_run_id)
                old_jd_path = _jd_checkpoint_path(
                    app.config["RESULT_DIR"], resume_from_run_id)
                resume_jd = _load_jd_checkpoint(old_jd_path)
                # 新 run 继承旧 run 时删除旧文件；原 run 就地继续时保留同一断点，
                # 避免在首次新批次落盘前退出导致已抓 JD 丢失。
                if resume_from_run_id != task_id:
                    _remove_jd_checkpoint(old_jd_path)
                if resume_verdicts or resume_jd:
                    emit(stage="resume",
                         message=f"接着上次进度：已有 {len(resume_verdicts)} 条判定、"
                                 f"{len(resume_jd)} 条 JD，跳过重复工作")
            resume_fine_verdicts = {}
            if resume_from_run_id:
                resume_fine_verdicts, _ = _split_resume_verdicts(resume_verdicts)

            # 2) AI 凭据
            settings = store.get_ai_settings()
            cred_ref = store.get_credential_ref()
            api_key = ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
            endpoint = settings.get("endpoint_url", "")
            model = settings.get("model", "")
            if not api_key or not endpoint:
                raise RuntimeError("ai_not_configured")

            criteria = dict(screening_fields or {})
            criteria["profile_summary"] = profile_summary or ""

            if _stop_requested():
                _mark_cancelled()
                return

            # 3) Stage A：字段粗筛（移除明显不符，学历向下兼容）
            emit(stage="screen_a", current=0, total=len(raw_jobs),
                 message="AI 粗筛中（对照筛选字段）…")

            def _a_progress(cur, tot):
                emit(stage="screen_a", current=cur, total=tot,
                     message=f"AI 粗筛 {cur}/{tot}")

            # 切片6：粗筛继续时跳过已判定的 job_id（从 checkpoint + DB 真实 verdict 恢复）
            _rough_done_ids: set[str] = set()
            _resume_verdicts: dict[str, str] = {}  # job_id → verdict（真实判定）
            if resume_from_run_id:
                _rough_done_ids = store.load_checkpoint(
                    resume_from_run_id, "ai_rough"
                )
                # 从 DB 查真实 verdict，不默认全部 kept
                _db_verdicts = store.load_screening_verdicts(resume_from_run_id)
                for _jid, _vobj in _db_verdicts.items():
                    _resume_verdicts[_jid] = (
                        _vobj.get("verdict", "") if isinstance(_vobj, dict) else ""
                    )
            _rough_todo = [j for j in raw_jobs
                           if str(j.get("job_id", "")) not in _rough_done_ids] if _rough_done_ids else raw_jobs
            # 粗筛历史使用 kept/dropped；兼容早期写成 match 的记录。
            _rough_kept_from_resume = [
                j for j in raw_jobs
                if str(j.get("job_id", "")) in _rough_done_ids
                and _resume_verdicts.get(str(j.get("job_id", "")), "") in ("kept", "match")
            ] if _rough_done_ids else []
            _rough_completed_ids = set(_rough_done_ids)

            try:
                def _rough_batch_done(batch_verdicts, completed_job_ids):
                    completed_snapshot = _rough_completed_ids | set(completed_job_ids)
                    store.save_verdict_and_checkpoint_atomic(
                        task_id, "ai_rough", batch_verdicts,
                        sorted(completed_snapshot),
                    )
                    _rough_completed_ids.clear()
                    _rough_completed_ids.update(completed_snapshot)

                screen_result = screen_jobs(_rough_todo, criteria, endpoint, api_key,
                                            model=model, progress=_a_progress,
                                            raise_on_systemic=True,
                                            on_batch_done=_rough_batch_done,
                                            execution_config=execution_config)
            except (ai_service.AISecurityError, ai_service.AICheckpointError) as _ai_exc:
                # AISecurityError（systemic）：暂停整任务，保存 checkpoint
                from webui.ai import (
                    map_ai_error_to_block_code, AISecurityError, AICheckpointError,
                )
                _block_code = ""
                if isinstance(_ai_exc, AICheckpointError):
                    _block_code = "internal_error"
                elif isinstance(_ai_exc, AISecurityError):
                    _block_code = map_ai_error_to_block_code(_ai_exc.error_code)
                if _block_code:
                    # 暂停状态、真实进度、checkpoint 和事件必须全部可靠落库；
                    # 任一步失败都交给外层 internal_error 路径，不能只改内存。
                    _done_keys = sorted(_rough_completed_ids)
                    store.update_screening_run(
                        task_id, status="paused", error_code=_block_code,
                        current_stage="ai_rough",
                        processed_count=len(_done_keys))
                    store.save_checkpoint(task_id, "ai_rough", _done_keys)
                    store.append_task_event(
                        task_id, "pause",
                        {"stage": "ai_rough", "code": _block_code,
                         "processed": len(_done_keys), "total": len(raw_jobs)})
                    with _pipeline_lock:
                        t = _pipeline_tasks.get(task_id)
                        if t is not None:
                            t["status"] = "paused"
                            t["error"] = (
                                f"AI 粗筛被阻断（{_block_code}）："
                                f"已处理 {len(_rough_completed_ids)}/{len(raw_jobs)} 条。"
                                "处理完成后点「继续」"
                            )
                    return
                raise  # 非 systemic，往上抛
            if _stop_requested():
                _mark_cancelled()
                return
            # 合并 resume 已判定的结果（resume 的岗位默认 kept，因为上次没被 drop）
            kept_ids = set(screen_result["kept"]) | {str(j.get("job_id", "")) for j in _rough_kept_from_resume}
            # 粗筛成功完成：保存全部已判定 job_id（用于未来继续时跳过）
            dropped_by_id = {
                str(d.get("job_id") or ""): d for d in (screen_result.get("dropped") or [])
            }
            if resume_from_run_id:
                for item in _resume_dropped_from_verdicts(raw_jobs, resume_verdicts):
                    dropped_by_id.setdefault(str(item.get("job_id") or ""), item)
            dropped = list(dropped_by_id.values())
            store.save_checkpoint(
                task_id,
                "ai_rough",
                list(kept_ids | {str(d.get("job_id") or "") for d in dropped}),
            )
            survivors = [j for j in raw_jobs if str(j.get("job_id", "")) in kept_ids]
            emit(stage="screen_a_done", kept=len(survivors), dropped=len(dropped),
                 message=f"粗筛完成：保留 {len(survivors)} 条，移除 {len(dropped)} 条")
            store.update_screening_run(
                task_id, status="running", source_cursor=0,
                total_kept=len(survivors), total_dropped=len(dropped),
            )

            # 4) 对保留的岗位分段抓 JD（重开调试浏览器，抓完关闭）。
            # 每段落盘 JD 断点文件 + 更新 source_cursor：进程崩了已抓的也不丢，
            # 重跑（含登录墙后重试）自动跳过已抓岗位。
            enriched = [dict(job) for job in survivors]
            jd_path = _jd_checkpoint_path(app.config["RESULT_DIR"], task_id)
            jd_map = dict(resume_jd)
            jd_failures: dict[str, dict[str, str]] = {}
            if survivors:
                emit(stage="ensure_chrome", message="启动调试浏览器，准备抓取 JD…")
                chrome_ok, chrome_err = ensure_chrome_ready()
                if not chrome_ok:
                    reason = f"调试浏览器未就绪（{chrome_err}），请处理后继续"
                    _save_jd_checkpoint(jd_path, jd_map)
                    store.update_screening_run(
                        task_id, status="paused", error_code="cdp_unavailable",
                        current_stage="jd_detail", processed_count=len(jd_map),
                        error_reason=reason,
                    )
                    store.save_checkpoint(
                        task_id, "jd_detail", sorted(jd_map)
                    )
                    store.append_task_event(task_id, "pause", {
                        "stage": "jd_detail", "code": "cdp_unavailable",
                        "processed": len(jd_map), "total": len(survivors),
                    })
                    with _pipeline_lock:
                        t = _pipeline_tasks.get(task_id)
                        if t is not None:
                            t["status"] = "paused"
                            t["error"] = reason
                    return
                source = _make_cdp_source()
                if source is None:
                    reason = "CDP 抓取源不可用，请确认调试浏览器后继续"
                    _save_jd_checkpoint(jd_path, jd_map)
                    store.update_screening_run(
                        task_id, status="paused", error_code="cdp_unavailable",
                        current_stage="jd_detail", processed_count=len(jd_map),
                        error_reason=reason,
                    )
                    store.save_checkpoint(
                        task_id, "jd_detail", sorted(jd_map)
                    )
                    store.append_task_event(task_id, "pause", {
                        "stage": "jd_detail", "code": "cdp_unavailable",
                        "processed": len(jd_map), "total": len(survivors),
                    })
                    with _pipeline_lock:
                        t = _pipeline_tasks.get(task_id)
                        if t is not None:
                            t["status"] = "paused"
                            t["error"] = reason
                    return

                todo_jd = [j for j in survivors
                           if str(j.get("job_id", "")) not in jd_map]
                emit(stage="fetch_jd", current=len(jd_map), total=len(survivors),
                     message=f"抓取 JD（{len(jd_map)}/{len(survivors)}）…")
                DETAIL_CHUNK = max(1, int(execution_config.detail_batch_size))
                for chunk_start in range(0, len(todo_jd), DETAIL_CHUNK):
                    if _stop_requested():
                        close_debug_chrome()
                        _mark_cancelled()
                        return
                    chunk = todo_jd[chunk_start:chunk_start + DETAIL_CHUNK]
                    _jd_base = len(jd_map)

                    def _jd_progress(done, total, _base=_jd_base):
                        cur = min(_base + done, len(survivors))
                        emit(stage="fetch_jd", current=cur, total=len(survivors),
                             message=f"抓取 JD {cur}/{len(survivors)}")

                    detail_result = fetch_job_details(
                        chunk, source,
                        artifact_dir=app.config["RESULT_DIR"],
                        stop_event=stop_event,
                        progress=_jd_progress,
                        execution_config=execution_config)
                    for j in detail_result["jobs"]:
                        jid = str(j.get("job_id", ""))
                        jd = str(j.get("jd", "")).strip()
                        if jid and jd:
                            jd_map[jid] = jd
                            jd_failures.pop(jid, None)
                        elif jid and j.get("jd_failed_code"):
                            jd_failures[jid] = {
                                "code": str(j.get("jd_failed_code")),
                                "reason": str(j.get("jd_failed_reason") or ""),
                            }
                    _save_jd_checkpoint(jd_path, jd_map)
                    store.update_screening_run(
                        task_id, source_cursor=len(jd_map),
                        processed_count=len(jd_map), current_stage="jd_detail",
                    )
                    emit(stage="fetch_jd",
                         current=min(len(jd_map), len(survivors)), total=len(survivors),
                         message=f"抓取 JD {min(len(jd_map), len(survivors))}/{len(survivors)}")
                    if detail_result.get("hard_stop"):
                        # 源级硬信号：暂停，不关浏览器（用户需要它处理验证码/登录）
                        _hs_code = detail_result.get("hard_stop_code") or "source_blocked"
                        _hs_label = _FAILED_CODE_LABELS.get(_hs_code, "抓取被拦截")
                        _hs_hint = next((
                            str(job.get("jd_failed_reason") or "").strip()
                            for job in detail_result.get("jobs") or []
                            if job.get("jd_failed_reason")
                        ), "")
                        _hs_reason = _hs_hint if _hs_hint and _hs_hint != _hs_label else _hs_label
                        _persist_jd_job_failures(
                            task_id,
                            detail_result.get("jobs") or [],
                            stage="jd_detail",
                        )
                        store.update_screening_run(
                            task_id, status="paused", error_code=_hs_code,
                            current_stage="jd_detail",
                            processed_count=len(jd_map), error_reason=_hs_reason,
                        )
                        with _pipeline_lock:
                            t = _pipeline_tasks.get(task_id)
                            if t is not None:
                                t["status"] = "paused"
                                t["error"] = (
                                    f"抓取 JD 时{_hs_reason}：已抓 "
                                    f"{len(jd_map)}/{len(survivors)} 条（已保存）。"
                                    "请在自动化浏览器中处理，完成后点「继续」"
                                )
                        return
                    if detail_result.get("stopped"):
                        close_debug_chrome()
                        _mark_cancelled()
                        return
                close_debug_chrome()
            for job in enriched:
                jid = str(job.get("job_id", ""))
                job["jd"] = jd_map.get(jid, "")
                failure = jd_failures.get(jid)
                if not job["jd"] and failure:
                    job["jd_failed_code"] = failure["code"]
                    job["jd_failed_reason"] = failure["reason"]

            # 5) Stage B：JD 精筛（对比候选人画像）
            jobs_with_jd = [j for j in enriched if str(j.get("jd", "")).strip()]
            if not profile_summary.strip():
                # 无画像时跳过精筛，全部标记待人工确认
                for job in enriched:
                    job["verdict"] = "uncertain"
                    job["verdict_reason"] = "未填写求职画像，跳过 AI 精筛"
                match_count = 0
                emit(stage="screen_b", current=len(jobs_with_jd), total=len(jobs_with_jd),
                     message="未填写求职画像，已跳过 AI 精筛")
            else:
                if _stop_requested():
                    _mark_cancelled()
                    return
                # 分段精筛，每段判定落库（screening_results）+ 更新 processed_count：
                # 进程崩了已筛的判定不丢，重跑自动跳过。
                done_verdicts = dict(resume_fine_verdicts)
                # 切片6：从 checkpoint 恢复已判定 job_id（resume 场景）
                if resume_from_run_id:
                    _fine_done = store.load_checkpoint(
                        resume_from_run_id, "ai_fine"
                    )
                    if _fine_done:
                        _extra = {
                            jid: {
                                "verdict": "uncertain",
                                "reason": "从断点恢复，待重新判定",
                            }
                            for jid in _fine_done if jid not in done_verdicts
                        }
                        done_verdicts.update(_extra)
                todo_match = [j for j in jobs_with_jd
                              if str(j.get("job_id", "")) not in done_verdicts]
                emit(stage="screen_b",
                     current=min(len(done_verdicts), len(jobs_with_jd)),
                     total=len(jobs_with_jd),
                     message="AI 精筛中（JD 对比简历画像）…")
                def _fine_progress(cur, tot):
                    emit(stage="screen_b",
                         current=min(len(done_verdicts) + cur, len(jobs_with_jd)),
                         total=len(jobs_with_jd),
                         message=f"AI 精筛 {min(len(done_verdicts) + cur, len(jobs_with_jd))}/{len(jobs_with_jd)}")

                def _fine_batch_done(batch_verdicts, completed_job_ids):
                    nonlocal done_verdicts
                    next_verdicts = dict(done_verdicts)
                    next_verdicts.update(batch_verdicts)
                    # verdict 与 checkpoint 同事务提交。任一步失败都必须停止，
                    # 不能继续下一批 AI 后再把内存结果伪装成可恢复进度。
                    store.save_verdict_and_checkpoint_atomic(
                        task_id, "ai_fine", batch_verdicts,
                        list(next_verdicts.keys()),
                    )
                    store.update_screening_run(
                        task_id, processed_count=len(next_verdicts))
                    done_verdicts = next_verdicts
                    emit(stage="screen_b",
                         current=min(len(done_verdicts), len(jobs_with_jd)),
                         total=len(jobs_with_jd),
                         message=f"AI 精筛 {min(len(done_verdicts), len(jobs_with_jd))}/{len(jobs_with_jd)}")

                try:
                    match_result = match_jds(
                        todo_match, profile_summary, endpoint, api_key,
                        model=model, raise_on_systemic=True,
                        progress=_fine_progress,
                        on_batch_done=_fine_batch_done,
                        execution_config=execution_config)
                except ai_service.AISecurityError as _ai_exc:
                    # 切片6：systemic 错误暂停整任务（不批量变 uncertain 后完成）
                    from webui.ai import map_ai_error_to_block_code, AISecurityError
                    if isinstance(_ai_exc, AISecurityError):
                        _block_code = map_ai_error_to_block_code(_ai_exc.error_code)
                        if _block_code:
                            store.update_screening_run(
                                task_id, status="paused", error_code=_block_code,
                                current_stage="ai_fine",
                                processed_count=len(done_verdicts))
                            store.save_checkpoint(
                                task_id, "ai_fine", list(done_verdicts.keys()))
                            store.append_task_event(
                                task_id, "pause",
                                {"stage": "ai_fine", "code": _block_code,
                                 "processed": len(done_verdicts),
                                 "total": len(jobs_with_jd)})
                            with _pipeline_lock:
                                t = _pipeline_tasks.get(task_id)
                                if t is not None:
                                    t["status"] = "paused"
                                    t["error"] = (
                                        f"AI 精筛被阻断（{_block_code}）："
                                        f"已判定 {len(done_verdicts)}/{len(jobs_with_jd)} 条。"
                                        "处理完成后点「继续」"
                                    )
                            return
                    raise  # 非 systemic，往上抛
                # 兜底：末轮重试等未触发 on_batch_done 的新判定仍须落库。
                _pending_fine_verdicts = {
                    jid: verdict for jid, verdict in (match_result.get("verdicts") or {}).items()
                    if jid not in done_verdicts
                }
                if _pending_fine_verdicts:
                    _fine_batch_done(
                        _pending_fine_verdicts,
                        list(done_verdicts) + list(_pending_fine_verdicts))
                verdicts = done_verdicts
                for job in enriched:
                    jid = str(job.get("job_id", ""))
                    v = verdicts.get(jid)
                    if v:
                        job["verdict"] = v["verdict"]
                        job["verdict_reason"] = v["reason"]
                        job["caveats"] = v.get("caveats", [])
                    else:
                        # 未抓到 JD 的岗位无法精筛，标记待定（不红不绿）
                        job["verdict"] = "uncertain"
                        code = job.get("jd_failed_code", "")
                        label = _FAILED_CODE_LABELS.get(code, "")
                        detail_reason = str(job.get("jd_failed_reason") or "").strip()
                        if detail_reason:
                            job["verdict_reason"] = f"未抓到 JD（{detail_reason}），无法精筛"
                        elif label:
                            job["verdict_reason"] = f"未抓到 JD（{label}），无法精筛"
                        else:
                            job["verdict_reason"] = "未抓到 JD，无法精筛"

                match_count = sum(1 for j in enriched if j.get("verdict") == "match")
            if _stop_requested():
                _mark_cancelled()
                return
            result = {
                "ok": True,
                "jobs": enriched,
                "dropped": dropped,
                "total_scraped": len(raw_jobs),
                "total_kept": len(enriched),
                "total_matched": match_count,
                "total_dropped": len(dropped),
                "profile_summary": profile_summary,
                "error": "",
            }
            job_events = []
            for job in enriched:
                verdict = job.get("verdict")
                is_failure = verdict == "uncertain"
                job_events.append((
                    "job_fail" if is_failure else "job_success",
                    {
                        "stage": "ai_fine",
                        "job_id": str(job.get("job_id") or ""),
                        "verdict": verdict,
                        "failed_code": (
                            job.get("failed_code") or job.get("jd_failed_code")
                        ) if is_failure else None,
                        "reason": job.get("verdict_reason", ""),
                    },
                ))
            store.append_task_events(task_id, job_events)
            source_run_id = store.save_pipeline_result(
                result, {"screening": screening_fields},
                started_at=task.get("started_at"),
                finished_at=int(time.time() * 1000),
                execution_config=execution_config.to_dict(),
            )
            result["source_run_id"] = source_run_id
            mismatch_count = sum(
                1 for job in enriched if job.get("verdict") == "not_match"
            )
            pending_count = sum(
                1 for job in enriched
                if job.get("verdict") not in ("match", "not_match", "mismatch")
            )
            processed_count = match_count + mismatch_count
            terminal_message = (
                f"筛选完成，但有 {pending_count} 条待确认：匹配 {match_count} 条"
                if pending_count else f"筛选完成：匹配 {match_count} 条"
            )
            # 最终事件也是持久化契约的一部分；先写事件，再提交终态，避免
            # DB 已终态后事件写失败造成内存 failed / DB completed 分裂。
            emit(stage="done", total_matched=match_count,
                 message=terminal_message)
            store.update_screening_run(
                task_id,
                match_count=match_count,
                mismatch_count=mismatch_count,
                pending_count=pending_count,
                processed_count=processed_count,
                total_scraped=len(raw_jobs),
                total_kept=len(enriched),
                total_dropped=len(dropped),
                current_stage="done",
            )
            final_db_status = store.finalize_run_status(task_id)
            if final_db_status not in ("succeeded", "partial"):
                raise RuntimeError(
                    f"invalid_ai_terminal_status:{final_db_status}"
                )
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    task["result"] = result
                    task["status"] = "done"
            _schedule_pipeline_task_cleanup(task_id)
            # 任务成功：断点文件使命完成（续跑只服务失败/取消/中断）
            _remove_jd_checkpoint(jd_path)
        except ai_service.AISecurityError as exc:
            error_message = ai_service.user_facing_error(exc.error_code)
            persistence_error = None
            try:
                store.update_screening_run(
                    task_id, status="failed", error_code=exc.error_code,
                    error_reason=error_message,
                )
            except _OPERATIONAL_ERRORS as persist_exc:
                persistence_error = type(persist_exc).__name__
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    task["status"] = "failed"
                    task["error"] = (
                        error_message if persistence_error is None
                        else f"{error_message}；状态保存失败：{persistence_error}"
                    )
            _schedule_pipeline_task_cleanup(task_id)
        except Exception as exc:
            error_message = f"AI 筛选异常：{type(exc).__name__}"
            persistence_error = None
            try:
                store.update_screening_run(
                    task_id, status="failed", error_code="internal_error",
                    error_reason=error_message,
                )
            except _OPERATIONAL_ERRORS as persist_exc:
                persistence_error = type(persist_exc).__name__
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    task["status"] = "failed"
                    task["error"] = (
                        error_message if persistence_error is None
                        else f"{error_message}；状态保存失败：{persistence_error}"
                    )
            _schedule_pipeline_task_cleanup(task_id)

    @app.route("/api/analyze-resume", methods=["POST"])
    def analyze_resume():
        """Stage 1: Upload resume file → AI reads it → returns unified search fields.

        Accepts multipart form with 'file' field (PDF/DOCX/TXT).
        Returns JSON with the unified schema fields for user confirmation.
        """
        from webui.resume import validate_format, validate_size
        from webui.ai import analyze_resume_to_fields, AISecurityError, user_facing_error

        file = request.files.get("file")
        if not file or not file.filename:
            return jsonify({"ok": False, "error": "未上传文件"}), 400

        try:
            fmt = validate_format(file.filename)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        file_bytes = file.read()
        try:
            validate_size(file_bytes)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        # Get AI credentials
        settings = store.get_ai_settings()
        if not settings.get("is_configured"):
            return jsonify({"ok": False, "error": "AI 未配置，请先设置 API 地址和密钥"}), 400
        cred_ref = store.get_credential_ref()
        if not cred_ref:
            return jsonify({"ok": False, "error": "未找到 API 密钥"}), 400
        api_key = ai_service.retrieve_api_key(cred_ref)
        if not api_key:
            return jsonify({"ok": False, "error": "API 密钥读取失败"}), 400

        try:
            fields = analyze_resume_to_fields(
                file_bytes, fmt,
                endpoint_url=settings.get("endpoint_url", ""),
                api_key=api_key,
                model=settings.get("model", ""),
            )
        except AISecurityError as exc:
            return jsonify({"ok": False, "error": user_facing_error(exc.error_code)}), 502
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        # Return fields with human-readable labels for confirmation UI
        field_labels = {
            "keyword": ("搜索关键词", fields["keyword"], "keyword_chips"),
            "city": ("城市", fields["city"], "city"),
            "salary": ("薪资范围", fields["salary"], boss.SALARY_MAP),
            "experience": ("经验要求", fields["experience"], boss.EXPERIENCE_MAP),
            "degree": ("学历", fields["degree"], boss.DEGREE_MAP),
            "industry": ("行业", fields["industry"], boss.INDUSTRY_MAP),
            "scale": ("公司规模", fields["scale"], boss.SCALE_MAP),
            "stage": ("融资阶段", fields["stage"], boss.STAGE_MAP),
        }

        return jsonify({"ok": True, "fields": fields, "labels": field_labels})

    @app.route("/api/confirm-fields", methods=["POST"])
    def confirm_fields():
        """Stage 2: User confirms/edits the AI-extracted fields.

        Accepts JSON body with the unified fields (user may have edited them).
        Validates all values and returns ready-to-execute script parameters.
        """
        from webui.ai import _validate_unified_fields, UNIFIED_SEARCH_FIELDS

        body = request.get_json(silent=True)
        if not body or not isinstance(body, dict):
            return jsonify({"ok": False, "error": "无效的请求体"}), 400

        # Validate the confirmed fields
        fields = _validate_unified_fields(body)

        if not fields.get("keyword"):
            return jsonify({"ok": False, "error": "搜索关键词不能为空"}), 400
        if not fields.get("city"):
            return jsonify({"ok": False, "error": "城市无效，请选择支持的城市"}), 400

        # spec 007 ③：keyword 现在是 [{word, recommended}]，脚本消费逗号拼接字符串。
        # 该端点已废弃（前端走 /api/execute-search），此处转换仅保证不崩。
        kw_chips = fields.get("keyword") or []
        if isinstance(kw_chips, list) and kw_chips and isinstance(kw_chips[0], dict):
            kw_str = ",".join(c.get("word", "") for c in kw_chips if c.get("word"))
        else:
            kw_str = str(kw_chips) if kw_chips else ""

        # Build the exact parameters the script consumes
        script_params = {
            "keyword": kw_str,
            "city": fields["city"],
            "filters": {},
        }
        for key in ("salary", "experience", "degree", "industry", "scale", "stage"):
            if fields.get(key):
                script_params["filters"][key] = fields[key]

        return jsonify({"ok": True, "confirmed_fields": fields, "script_params": script_params})

    @app.route("/api/advanced-settings", methods=["GET"])
    def get_advanced_settings():
        """SPEC011 T009: 返回版本化配置状态。

        保留 ``settings`` 字段用于迁移兼容，同时返回 selection、last_custom、
        mode_version 等版本化状态。
        """
        from webui.pipeline_exec import _ADVANCED_DEFAULTS
        from webui.execution_config import CONFIG_SCHEMA_VERSION
        state = store.get_advanced_config_state()
        active_version = None
        previous_version = None
        if state["active_mode_version_id"]:
            try:
                active_version = store.get_mode_version(
                    state["active_mode_version_id"]
                )
                previous_version = store.get_previous_mode_version(
                    state["active_mode_version_id"]
                )
            except KeyError:
                active_version = None
                previous_version = None
        # 兼容旧前端：settings 仍返回当前活跃设置
        legacy_settings = _load_legacy_advanced_settings()
        return jsonify({
            "ok": True,
            "selection": state["active_selection"],
            "settings": legacy_settings,
            "defaults": _ADVANCED_DEFAULTS,
            "last_custom": {
                "config_digest": state["last_custom_digest"],
                "settings": state["last_custom_config"] or {},
            } if state["last_custom_config"] else None,
            "mode_version": {
                "id": state["active_mode_version_id"],
                "version_digest": active_version["version_digest"],
                "previous_version_id": (
                    previous_version["id"] if previous_version else None
                ),
                "available_modes": ["stable", "balanced", "extreme"],
            } if active_version else None,
            "manual_ranges": (
                active_version["manual_ranges"] if active_version else {}
            ),
            "config_schema_version": CONFIG_SCHEMA_VERSION,
        })

    @app.route("/api/advanced-settings", methods=["POST"])
    def save_advanced_settings_endpoint():
        """SPEC011 T009: 兼容旧 POST 保存，同时写入 store 的自定义配置。"""
        from webui.pipeline_exec import _ADVANCED_DEFAULTS
        from webui.execution_config import DEFAULT_DETAIL_TAB_POOL_SIZE, SPEED_FIELDS
        body = request.get_json(silent=True) or {}
        settings = body.get("settings")
        if not isinstance(settings, dict):
            return jsonify({"ok": False, "error": "缺少 settings 对象"}), 400
        # 只保留合法 key，类型校验
        clean = {}
        for k, default in _ADVANCED_DEFAULTS.items():
            if k in settings:
                val = settings[k]
                if k == "browser_account":
                    from webui.pipeline_exec import load_browser_accounts
                    accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
                    clean[k] = str(val) if str(val) in accounts else "a"
                    continue
                if isinstance(default, float):
                    val = float(val)
                elif isinstance(default, int):
                    val = int(val)
                clean[k] = val
        _save_legacy_advanced_settings(clean)
        # SPEC011: 如果速度字段都存在，也写入 store 自定义配置
        speed_fields = {k: v for k, v in clean.items() if k in SPEED_FIELDS}
        speed_fields.setdefault("detail_tab_pool_size", DEFAULT_DETAIL_TAB_POOL_SIZE)
        if len(speed_fields) == len(SPEED_FIELDS):
            try:
                store.save_custom_config(speed_fields)
            except (ValueError, TypeError):
                pass  # store 保存失败不阻塞旧路径
        return jsonify({"ok": True, "settings": _load_legacy_advanced_settings()})

    def _browser_busy() -> bool:
        with _pipeline_lock:
            if any(
                task.get("status") in ("running", "queued")
                for task in _pipeline_tasks.values()
            ):
                return True
        try:
            with store._connection() as conn:
                row = conn.execute(
                    "SELECT 1 FROM screening_runs WHERE status = 'paused' LIMIT 1"
                ).fetchone()
            return row is not None
        except (sqlite3.Error, RuntimeError):
            return False

    @app.route("/api/browser-accounts", methods=["GET"])
    def list_browser_accounts():
        from webui.pipeline_exec import load_browser_accounts
        accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
        active = str((_load_legacy_advanced_settings() or {}).get("browser_account") or "a")
        if active not in accounts:
            active = "a"
        return jsonify({
            "ok": True,
            "accounts": list(accounts.values()),
            "active_account": active,
            "busy": _browser_busy(),
        })

    @app.route("/api/browser-accounts", methods=["POST"])
    def add_browser_account_endpoint():
        from webui.pipeline_exec import add_browser_account
        body = request.get_json(silent=True) or {}
        name = str(body.get("name") or "").strip()
        profile_dir = str(body.get("profile_dir") or "").strip()
        try:
            account = add_browser_account(
                name, profile_dir=profile_dir,
                path=app.config["BROWSER_ACCOUNTS_PATH"],
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 422
        except (OSError, RuntimeError):
            return jsonify({"ok": False, "error": "账号保存失败，请检查磁盘后重试"}), 503
        return jsonify({"ok": True, "account": account}), 201

    @app.route("/api/browser-accounts/<account_id>/activate", methods=["POST"])
    def activate_browser_account(account_id):
        from webui.pipeline_exec import load_browser_accounts
        accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
        if str(account_id) not in accounts:
            return jsonify({"ok": False, "error": "账号不存在"}), 404
        settings = _load_legacy_advanced_settings()
        settings["browser_account"] = str(account_id)
        _save_legacy_advanced_settings(settings)
        return jsonify({"ok": True, "active_account": str(account_id)})

    @app.route("/api/browser-accounts/<account_id>/open", methods=["POST"])
    def open_browser_account(account_id):
        from webui.pipeline_exec import (
            ensure_chrome_ready, load_browser_accounts, set_active_cdp_data_dir,
        )
        accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
        account = accounts.get(str(account_id))
        if account is None:
            return jsonify({"ok": False, "error": "账号不存在"}), 404
        if _browser_busy():
            return jsonify({
                "ok": False, "error": "browser_busy",
                "message": "当前有任务运行或暂停，无法打开其他账号浏览器；请先结束或取消任务",
            }), 409
        set_active_cdp_data_dir(str(account_id))
        ok, msg = ensure_chrome_ready()
        if not ok:
            return jsonify({"ok": False, "error": "chrome_not_ready", "message": msg}), 409
        return jsonify({
            "ok": True,
            "message": f"已打开「{account['name']}」的自动化浏览器，请登录 BOSS直聘",
        })

    @app.route("/api/browser-accounts/<account_id>", methods=["DELETE"])
    def delete_browser_account_endpoint(account_id):
        from webui.pipeline_exec import delete_browser_account, load_browser_accounts
        if _browser_busy():
            return jsonify({
                "ok": False, "error": "browser_busy",
                "message": "任务运行或暂停中不能删除账号",
            }), 409
        accounts = load_browser_accounts(app.config["BROWSER_ACCOUNTS_PATH"])
        account = accounts.get(str(account_id))
        if account is None:
            return jsonify({"ok": False, "error": "账号不存在"}), 404
        if (boss.is_cdp_ready(boss.DEFAULT_CDP_PORT)
                and boss.cdp_port_uses_profile(
                    boss.DEFAULT_CDP_PORT, str(account["profile_dir"]))):
            return jsonify({
                "ok": False, "error": "browser_in_use",
                "message": "该账号的自动化浏览器正在运行，请先打开其他账号或手动关闭后再删除",
            }), 409
        try:
            delete_browser_account(
                str(account_id), path=app.config["BROWSER_ACCOUNTS_PATH"],
            )
        except KeyError:
            return jsonify({"ok": False, "error": "账号不存在"}), 404
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        except (OSError, RuntimeError):
            return jsonify({"ok": False, "error": "账号删除失败，请检查磁盘后重试"}), 503
        settings = _load_legacy_advanced_settings()
        if str(settings.get("browser_account") or "") == str(account_id):
            settings["browser_account"] = "a"
            _save_legacy_advanced_settings(settings)
        return jsonify({"ok": True})

    @app.route("/api/advanced-settings/custom", methods=["PUT"])
    def save_custom_config():
        """SPEC011 T009: 保存完整自定义配置。

        对应 HTTP API PUT /api/advanced-settings/custom。
        """
        from webui.execution_config import SPEED_FIELDS
        body = request.get_json(silent=True) or {}
        settings = body.get("settings")
        if not isinstance(settings, dict):
            return jsonify({"ok": False, "error": "缺少 settings 对象"}), 400
        # 验证速度字段完整；旧 9 字段请求由 store 补默认 JD Tab 数
        missing = [
            f for f in SPEED_FIELDS
            if f not in settings and f != "detail_tab_pool_size"
        ]
        if missing:
            return jsonify({
                "ok": False,
                "error_code": "invalid_request",
                "error": f"缺少必填字段: {missing}",
            }), 400
        try:
            digest = store.save_custom_config(settings)
        except (ValueError, TypeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 422
        # 同时保存到旧 JSON 文件以保持兼容
        _save_legacy_advanced_settings(settings)
        state = store.get_advanced_config_state()
        return jsonify({
            "ok": True,
            "selection": "custom",
            "config_digest": digest,
            "settings": state["last_custom_config"],
        })

    @app.route("/api/advanced-settings/select-mode", methods=["POST"])
    def select_mode():
        """SPEC011 T009: 选择系统参考模式。

        对应 HTTP API POST /api/advanced-settings/select-mode。
        服务端根据 scope_digest 重新计算任务规模，不信任客户端传入的 size。
        """
        body = request.get_json(silent=True) or {}
        mode = body.get("mode")
        scope_digest = body.get("scope_digest")
        if mode not in ("stable", "balanced", "extreme", "custom"):
            return jsonify({
                "ok": False,
                "error_code": "invalid_request",
                "error": "mode 必须是 stable/balanced/extreme/custom",
            }), 400
        scope = scope_previews.get(str(scope_digest or ""))
        if scope is None:
            return jsonify({
                "ok": False,
                "error_code": "scope_preview_required",
                "error": "搜索范围摘要未知，请重新校验搜索范围",
            }), 409
        task_size = scope["task_size"]
        try:
            result = store.select_mode(mode, task_size=task_size)
        except (ValueError, TypeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 422
        # 同时保存到旧 JSON 文件以保持兼容；切回 custom 也必须恢复
        # 最近自定义值，避免 SQLite 权威状态与旧执行入口分叉。
        if result.get("config"):
            _save_legacy_advanced_settings(result["config"])
        from webui.execution_config import SPEED_FIELDS
        settings = {
            field: result["config"][field] for field in SPEED_FIELDS
        }
        state = store.get_advanced_config_state()
        return jsonify({
            "ok": True,
            "selection": result["selection"],
            "settings": settings,
            "task_size": task_size,
            "mode_version_id": state["active_mode_version_id"],
            "config_digest": result["config"].get("config_digest"),
        })

    @app.route("/api/advanced-settings/mode-versions/rollback", methods=["POST"])
    def rollback_mode_version():
        """SPEC011 T009: 回退到指定模式版本。

        对应 HTTP API POST /api/advanced-settings/mode-versions/rollback。
        """
        body = request.get_json(silent=True) or {}
        target_version_id = body.get("target_version_id")
        if not target_version_id:
            return jsonify({
                "ok": False,
                "error_code": "invalid_request",
                "error": "缺少 target_version_id",
            }), 400
        try:
            store.rollback_mode_version(target_version_id)
        except (ValueError, TypeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 422
        state = store.get_advanced_config_state()
        return jsonify({
            "ok": True,
            "active_mode_version_id": state["active_mode_version_id"],
        })

    @app.route("/api/search-scope/preview", methods=["POST"])
    def search_scope_preview():
        """SPEC011 T004: 后端权威范围预览与校验。

        不改变任务工作量字段；仅返回规范化后的 scope 和去重信息。
        对应 HTTP API POST /api/search-scope/preview。
        """
        from webui.execution_config import preview_scope, CityValidationError

        body = request.get_json(silent=True) or {}
        keywords = body.get("keywords")
        scope_kind = body.get("scope_kind", "cities")
        cities = body.get("cities", [])
        pages_per_combination = body.get("pages_per_combination", 1)

        if not isinstance(keywords, list):
            return jsonify({"ok": False, "error": "keywords 必须是数组"}), 400
        if scope_kind not in ("cities", "nationwide"):
            return jsonify({"ok": False, "error": "scope_kind 必须是 cities 或 nationwide"}), 400
        if not isinstance(cities, list):
            return jsonify({"ok": False, "error": "cities 必须是数组"}), 400

        if isinstance(pages_per_combination, bool) or not isinstance(
            pages_per_combination, int
        ):
            return jsonify({"ok": False, "error": "pages_per_combination 必须是整数"}), 400
        pages_int = pages_per_combination

        try:
            result = preview_scope(
                keywords=keywords,
                scope_kind=scope_kind,
                cities=cities,
                pages_per_combination=pages_int,
            )
            scope_previews[result["scope"]["scope_digest"]] = dict(result["scope"])
            return jsonify({"ok": True, **result})
        except CityValidationError as e:
            return jsonify({
                "ok": False,
                "error_code": "city_validation_failed",
                "error": str(e),
                "details": e.details,
            }), 422
        except ValueError as e:
            return jsonify({
                "ok": False,
                "error_code": "scope_validation_failed",
                "error": str(e),
            }), 422

    @app.route("/api/execute-search", methods=["POST"])
    def execute_search():
        """Stage 3: Start the scraping run with confirmed script_params.

        Accepts JSON ``{"script_params": {...}}`` (or the params directly).
        Launches a background task and returns a ``task_id`` for polling.

        SPEC011 T006: 后端从权威 scope 和当前配置选择创建不可变快照；
        客户端不能提供或覆盖任务规模与执行配置。
        SPEC011 T015: 实验租约持有时拒绝启动（FR-035）。
        """
        body = request.get_json(silent=True) or {}
        script_params = body.get("script_params") or body
        if not isinstance(script_params, dict):
            return jsonify({"ok": False, "error": "无效的请求体"}), 400
        if not script_params.get("keyword") or not script_params.get("city"):
            return jsonify({"ok": False, "error": "缺少关键词或城市"}), 400
        # SPEC011 T015/FR-035: 实验租约门禁
        ok, err_resp = _check_tuning_lease_conflict()
        if not ok:
            return err_resp

        from webui.execution_config import (
            ExecutionConfigSnapshot, FrozenTaskScope, preview_scope,
        )
        requested_digest = str(body.get("scope_digest") or "")
        scope_payload = scope_previews.get(requested_digest) if requested_digest else None
        if requested_digest and scope_payload is None:
            return jsonify({
                "ok": False,
                "error_code": "scope_preview_required",
                "error": "搜索范围摘要未知，请重新校验搜索范围",
            }), 409
        if scope_payload is None:
            raw_keyword = script_params.get("keyword")
            keywords = (
                [item.strip() for item in str(raw_keyword).replace("，", ",").split(",")]
                if not isinstance(raw_keyword, list) else raw_keyword
            )
            raw_cities = script_params.get("city") or []
            if isinstance(raw_cities, str):
                raw_cities = [
                    item.strip() for item in raw_cities.replace("，", ",").split(",")
                    if item.strip()
                ]
            nationwide = raw_cities == ["全国"]
            try:
                preview = preview_scope(
                    keywords=keywords,
                    scope_kind="nationwide" if nationwide else "cities",
                    cities=[] if nationwide else raw_cities,
                    pages_per_combination=script_params.get("pages", 3),
                )
            except (TypeError, ValueError) as exc:
                return jsonify({
                    "ok": False, "error_code": "scope_validation_failed",
                    "error": str(exc),
                }), 422
            scope_payload = preview["scope"]
            scope_previews[scope_payload["scope_digest"]] = dict(scope_payload)
        try:
            frozen_scope = FrozenTaskScope.from_dict(scope_payload)
            state = store.get_advanced_config_state()
            selected = store.select_mode(
                state["active_selection"], task_size=frozen_scope.task_size,
            )
            execution_config = ExecutionConfigSnapshot.from_dict(selected["config"])
        except (KeyError, TypeError, ValueError) as exc:
            return jsonify({
                "ok": False, "error_code": "config_resolution_failed",
                "error": str(exc),
            }), 422
        script_params = dict(script_params)
        script_params["keyword"] = ",".join(frozen_scope.keywords)
        script_params["city"] = (
            ["全国"] if frozen_scope.scope_kind == "nationwide"
            else list(frozen_scope.cities)
        )
        script_params["pages"] = frozen_scope.pages_per_combination

        task_id = uuid.uuid4().hex
        task = _register_pipeline_task(task_id, "scrape")
        # 把冻结配置摘要存入任务记录，供进度查询返回
        with _pipeline_lock:
            task["config_digest"] = execution_config.config_digest
            task["scope_digest"] = frozen_scope.scope_digest
            task["browser_account"] = _account_for_run()
        store.create_screening_run(
            task_id,
            frozen_filters=script_params.get("filters") or {},
            source_count=frozen_scope.combination_count,
            execution_params={
                "script_params": script_params,
                "browser_account": _account_for_run(),
                "execution_config": execution_config.to_dict(),
                "frozen_scope": frozen_scope.to_dict(),
            },
            backend_version=_backend_version,
        )
        _activate_run_browser()
        try:
            _pipeline_executor.submit(
                _run_pipeline_task, task_id, script_params,
                execution_config, frozen_scope,
            )
        except RuntimeError:
            store.update_screening_run(
                task_id, status="failed", error_code="submit_failed",
                error_reason="任务执行器未接受任务",
            )
            raise
        return jsonify({
            "ok": True,
            "task_id": task_id,
            "config_digest": execution_config.config_digest,
            "scope_digest": frozen_scope.scope_digest,
            "task_size": frozen_scope.task_size,
        })

    @app.route("/api/execute-search/continue/<old_task_id>", methods=["POST"])
    def continue_execute_search(old_task_id, _block_checked=False):
        """断点续抓：从上次失败的组合接着跑，跳过已完成的组合。

        切片4：支持 paused 状态继续（FR-020）。优先从 DB checkpoint 恢复
        completed_combos（服务重启后内存丢失也能恢复），回退到内存 task.result。
        同时检查阻断是否解除（如登录已恢复、验证码已过）。

        SPEC011 T015: 实验租约持有时拒绝继续（FR-035）。
        """
        # SPEC011 T015/FR-035: 实验租约门禁
        ok, err_resp = _check_tuning_lease_conflict()
        if not ok:
            return err_resp
        # 1) 优先从内存读 old_task
        with _pipeline_lock:
            old_task = _pipeline_tasks.get(old_task_id)
            old_snapshot = dict(old_task) if old_task else None
        # 2) 内存没有则从 DB 读（服务重启恢复场景）
        db_run = None
        try:
            db_run = store.get_screening_run(old_task_id)
        except _OPERATIONAL_ERRORS:
            db_run = None
        if old_snapshot is None and db_run is None:
            return jsonify({"ok": False, "error": "原任务不存在或已过期"}), 404
        # DB 是服务重启后仍存在的状态权威；取消/失败均为终态，不得复活。
        mem_status = old_snapshot.get("status") if old_snapshot else None
        db_status = db_run.get("status") if db_run else None
        effective_status = db_status or mem_status
        if effective_status != "paused":
            return jsonify({
                "ok": False,
                "error": "not_paused",
                "status": _run_to_task_status(effective_status),
                "message": "只有 paused 状态的任务才能继续",
            }), 409
        if db_run is not None and not _block_checked:
            passed, code, reason = _check_resume_block(db_run)
            if not passed:
                return jsonify({
                    "ok": False, "error": "block_not_resolved",
                    "error_code": code, "error_reason": reason,
                    "status": "paused",
                }), 409
        if db_run is not None:
            resume_params = dict(db_run.get("execution_params") or {})
            if not str(resume_params.get("browser_account") or ""):
                resume_params["browser_account"] = _account_for_run(db_run)
                store.update_screening_execution_params(old_task_id, resume_params)
        # 3) 收集 script_params（内存优先，DB 兜底）
        script_params = (old_snapshot or {}).get("script_params")
        if not script_params and db_run:
            try:
                ep = db_run.get("execution_params") or {}
                script_params = ep.get("script_params") or ep
            except (AttributeError, TypeError):
                script_params = None
        if not script_params:
            return jsonify({"ok": False, "error": "原任务参数丢失，无法继续"}), 400
        # 4) 收集 completed_combos：DB checkpoint 优先（持久），内存 result 兜底
        completed: set[str] = set()
        try:
            completed = store.load_checkpoint(old_task_id, "scrape")
        except _OPERATIONAL_ERRORS:
            completed = set()
        if not completed and old_snapshot:
            old_result = old_snapshot.get("result") or {}
            completed = set(old_result.get("completed_combos") or [])
        try:
            old_jobs = store.load_scrape_run_jobs(old_task_id)
        except _OPERATIONAL_ERRORS:
            old_jobs = []
        if not old_jobs and old_snapshot:
            old_result = old_snapshot.get("result") or {}
            old_jobs = old_result.get("jobs") or []
        if not _claim_resume(old_task_id):
            return jsonify({
                "ok": False, "error": "already_running",
                "message": "该任务正在继续，请勿重复点击",
            }), 409
        task_id = old_task_id
        claimed_task, previous_task = _claim_pipeline_task_id(task_id, "scrape")
        if claimed_task is None:
            _release_resume_claim(old_task_id)
            return jsonify({
                "ok": False, "error": "already_running",
                "message": "该任务正在继续，请勿重复点击",
            }), 409
        # 把续抓信息存进 task，_run_pipeline_task 会读取
        with _pipeline_lock:
            task = _pipeline_tasks[task_id]
            task["skip_combos"] = completed
            task["old_jobs"] = old_jobs
            task["resuming_from"] = old_task_id
            task["browser_account"] = _account_for_run(db_run)
        start_gate = threading.Event()
        abort_start = threading.Event()

        def run_after_claim_commits():
            start_gate.wait()
            if not abort_start.is_set():
                _run_pipeline_task(task_id, script_params)

        try:
            future = _pipeline_executor.submit(run_after_claim_commits)
            # 事件与 DB claim 都在 worker 放行前完成；继续沿用同一 task_id，
            # 避免把内部 handoff 暴露成非 canonical 的 resumed 状态。
            if db_run is not None:
                store.append_task_event(old_task_id, "resume", {"task_id": task_id})
                if not store.claim_paused_screening_run(old_task_id):
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
            _release_resume_claim(old_task_id)
            return jsonify({
                "ok": False, "error": "resume_submit_failed",
                "message": f"继续任务提交失败：{type(exc).__name__}",
            }), 500
        start_gate.set()
        return jsonify({"ok": True, "task_id": task_id,
                        "skipped": len(completed), "old_jobs": len(old_jobs),
                        "resumed_from": old_task_id})

    @app.route("/api/execute-search/<task_id>/cancel", methods=["POST"])
    def cancel_execute_search(task_id):
        """停止正在运行的抓取任务。

        做法：set stop_event → 立刻关调试 Chrome（不等当前组合抓完）→
        task 标 cancelled。run_search 会因浏览器被关而退出，_run_pipeline_task
        看到 stop_event.is_set() 后标 cancelled 而非 failed/done。
        """
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)
            if task is None:
                return jsonify({"ok": False, "error": "任务不存在"}), 404
            if task["status"] not in ("queued", "running"):
                return jsonify({"ok": False, "error": f"任务已结束，无法取消（当前状态：{task['status']}）"}), 400
            stop_event = task.get("stop_event")
            if stop_event is not None:
                stop_event.set()
            # 立刻标记 cancelled，让前端轮询马上看到状态变化
            task["status"] = "cancelled"
            task["error"] = "用户已停止抓取"
            task["logs"].append("用户取消任务")
        # 关浏览器放到锁外，避免持锁时间过长。best-effort，失败不阻塞取消。
        try:
            from webui.pipeline_exec import close_debug_chrome
            close_debug_chrome()
        except Exception:
            pass
        return jsonify({"ok": True, "task_id": task_id, "status": "cancelled"})

    @app.route("/api/ai-screen/<task_id>/cancel", methods=["POST"])
    def cancel_ai_screen(task_id):
        """停止正在运行的 AI 筛选任务。

        与抓取取消同套路但按 kind 区分：纯 AI 调用阶段（粗筛/精筛）没有
        浏览器可关，close_debug_chrome 是 no-op；抓 JD 阶段关浏览器可让
        子进程抓取立即中断。工作线程在阶段边界看到 stop_event 后标
        cancelled，不会把结果覆盖成 done。
        """
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)
            if task is None:
                return jsonify({"ok": False, "error": "任务不存在"}), 404
            if task.get("kind") != "ai_screen":
                return jsonify({"ok": False, "error": "不是 AI 筛选任务"}), 409
            if task["status"] not in ("queued", "running"):
                return jsonify({"ok": False, "error": f"任务已结束，无法取消（当前状态：{task['status']}）"}), 400
            stop_event = task.get("stop_event")
            if stop_event is not None:
                stop_event.set()
            # 立刻标记 cancelled，让前端轮询马上看到状态变化
            task["status"] = "cancelled"
            task["error"] = "用户已停止筛选"
            task["logs"].append("用户取消任务")
        # 关浏览器放到锁外（仅抓 JD 阶段有意义），best-effort，失败不阻塞取消。
        try:
            from webui.pipeline_exec import close_debug_chrome
            close_debug_chrome()
        except Exception:
            pass
        return jsonify({"ok": True, "task_id": task_id, "status": "cancelled"})

    @app.route("/api/ai-screen", methods=["POST"])
    def ai_screen():
        """Stage 3b：对已抓取的原始岗位做两段式 AI 筛选。

        接收 ``{"screening_fields": {...}, "profile_summary": "..."}``，
        启动后台任务（StageA 粗筛→抓JD→StageB 精筛）并返回 ``task_id`` 供轮询。

        SPEC011 T015: 实验租约持有时拒绝启动（FR-035）。
        """
        body = request.get_json(silent=True) or {}
        screening_fields = body.get("screening_fields") or {}
        profile_summary = str(body.get("profile_summary") or "")
        scrape_task_id = str(body.get("scrape_task_id") or "").strip()
        if not isinstance(screening_fields, dict):
            return jsonify({"ok": False, "error": "无效的筛选字段"}), 400
        if not scrape_task_id:
            return jsonify({"ok": False, "error": "缺少 scrape_task_id"}), 400
        # SPEC011 T015/FR-035: 实验租约门禁
        ok, err_resp = _check_tuning_lease_conflict()
        if not ok:
            return err_resp
        source_snapshot = _ensure_scrape_source(scrape_task_id)
        if source_snapshot is None:
            return jsonify({"ok": False, "error": "抓取任务不存在"}), 404
        if source_snapshot.get("kind") != "scrape":
            return jsonify({"ok": False, "error": "来源任务不是抓取任务"}), 409
        source_result = source_snapshot.get("result")
        if (
            source_snapshot.get("status") != "done"
            or not isinstance(source_result, dict)
            or not source_result.get("ok")
        ):
            return jsonify({"ok": False, "error": "抓取任务尚未成功完成"}), 409
        # 同一抓取任务只允许一个 AI 筛选工作线程；防止多标签页重复提交。
        with _pipeline_lock:
            for existing_id, existing in _pipeline_tasks.items():
                if (existing.get("kind") == "ai_screen"
                        and existing.get("source_task_id") == scrape_task_id
                        and existing.get("status") in ("queued", "running")):
                    return jsonify({
                        "ok": False, "error": "already_running",
                        "existing_task_id": existing_id,
                        "message": "同一抓取任务已有 AI 筛选在运行",
                    }), 409
        task_id = uuid.uuid4().hex
        # paused 就地继续；服务重启打断的 interrupted（error_code=restart）
        # 也可以被“重新开始 AI 筛选”继承断点，但保留旧 run 的终态记录。
        resume_from_run_id = ""
        prev = None
        try:
            prev = store.latest_screening_run_for_source(
                scrape_task_id, statuses=("paused",))
            if prev is None:
                prev = store.latest_screening_run_for_source(
                    scrape_task_id, statuses=("interrupted",))
        except _OPERATIONAL_ERRORS as exc:
            return jsonify({
                "ok": False,
                "error": "resume_state_unavailable",
                "detail": type(exc).__name__,
            }), 503
        if prev is not None:
            prev_params = prev.get("execution_params") or {}
            same_fields = prev.get("frozen_filters") == screening_fields
            same_profile = str(prev_params.get("profile_summary", "")) == profile_summary
            restart_interrupted = (
                prev["status"] == "interrupted"
                and str(prev.get("error_code") or "") == "restart"
            )
            if same_fields and same_profile and (
                    prev["status"] == "paused" or restart_interrupted):
                resume_from_run_id = prev["id"]
        if resume_from_run_id and prev is not None and prev["status"] == "paused":
            # paused run 就地转为 running，保持唯一任务身份和 canonical 状态。
            try:
                claimed = store.claim_paused_screening_run(resume_from_run_id)
            except _OPERATIONAL_ERRORS as exc:
                return jsonify({
                    "ok": False,
                    "error": "resume_claim_failed",
                    "detail": type(exc).__name__,
                }), 503
            if not claimed:
                return jsonify({
                    "ok": False,
                    "error": "resume_already_claimed",
                }), 409
            task_id = resume_from_run_id
        claimed_old_resume = False
        if (resume_from_run_id and prev is not None
                and prev["status"] == "interrupted"):
            if not _claim_resume(resume_from_run_id):
                return jsonify({
                    "ok": False, "error": "already_running",
                    "message": "该任务正在继续，请勿重复点击",
                }), 409
            claimed_old_resume = True
        claimed_task, previous_task = _claim_pipeline_task_id(task_id, "ai_screen")
        if claimed_task is None:
            if (resume_from_run_id and prev is not None
                    and prev["status"] == "paused"):
                store.update_screening_run(resume_from_run_id, status="paused")
            if claimed_old_resume:
                _release_resume_claim(resume_from_run_id)
            return jsonify({
                "ok": False, "error": "already_running",
            }), 409
        claimed_task["source_task_id"] = scrape_task_id
        account_source = prev if resume_from_run_id else None
        claimed_task["browser_account"] = _account_for_run(account_source)
        if resume_from_run_id and prev is not None:
            resume_params = dict(prev.get("execution_params") or {})
            if not str(resume_params.get("browser_account") or ""):
                resume_params["browser_account"] = _account_for_run(prev)
                store.update_screening_execution_params(resume_from_run_id, resume_params)
        _activate_run_browser(account_source)
        try:
            _pipeline_executor.submit(
                _run_ai_screen_task, task_id, screening_fields,
                profile_summary, scrape_task_id, resume_from_run_id,
            )
        except RuntimeError:
            _release_pipeline_claim(task_id, claimed_task, previous_task)
            if (resume_from_run_id and prev is not None
                    and prev["status"] == "paused"):
                store.update_screening_run(resume_from_run_id, status="paused")
            if claimed_old_resume:
                _release_resume_claim(resume_from_run_id)
            raise
        if claimed_old_resume:
            try:
                store.update_screening_run(
                    resume_from_run_id,
                    error_code="resumed",
                    error_reason="已由新任务接管续跑",
                )
                store.append_task_event(
                    resume_from_run_id, "resume", {"task_id": task_id})
            except _OPERATIONAL_ERRORS:
                pass
        return jsonify({"ok": True, "task_id": task_id,
                        "resuming": bool(resume_from_run_id)})

    @app.route("/api/search-progress/<task_id>")
    def search_progress(task_id):
        """Poll the progress of a pipeline run.

        Returns ``{status, progress, logs, result, error}``. ``status`` is one
        of ``running`` / ``done`` / ``failed``. ``result`` (present when done)
        carries the matched ``jobs`` and counts.
        """
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)
            if task is None:
                return jsonify({"ok": False, "error": "任务不存在"}), 404
            # 终态补结束时间戳（首次进入终态时记一次），供前端计时器显示真实用时
            if task["status"] in ("done", "failed", "cancelled") and task.get("finished_at") is None:
                task["finished_at"] = int(time.time() * 1000)
            snapshot = {
                "ok": True,
                "kind": task.get("kind", ""),
                "status": task["status"],
                "progress": task["progress"],
                "logs": list(task["logs"][-LOG_TAIL_LINES:]),
                "error": task["error"],
                "started_at": task.get("started_at"),
                "finished_at": task.get("finished_at"),
                "config_digest": task.get("config_digest"),
                "scope_digest": task.get("scope_digest"),
            }
            if task["status"] in ("done", "failed") and task["result"] is not None:
                # 原样返回整个 result：抓取任务含 jobs/计数；
                # AI 筛选任务还含 dropped/verdict/profile_summary 等
                snapshot["result"] = task["result"]
        return jsonify(snapshot)

    def _has_newer_saved_result_than(timestamp: str | None) -> bool:
        """True when a result snapshot was saved after the given DB timestamp."""
        if not timestamp:
            return False
        try:
            saved_at = store.latest_pipeline_result_saved_at()
        except _OPERATIONAL_ERRORS:
            return False
        return bool(saved_at and str(saved_at) > str(timestamp))

    @app.route("/api/latest-running-task")
    def latest_running_task():
        """返回最近一个仍在运行（running/queued）的 pipeline 任务。

        用于页面刷新后接回任务：前端 onMounted 调这个接口，有在跑的任务
        就恢复 task_id 和进度快照，重新开始轮询。dict 保序（Py3.7+），
        最后注册的任务排在最后，倒序找第一个非终态的返回。

        查找顺序（FR-028/FR-037）：
        1. 内存中 running/queued 任务（刷新接回）
        2. DB 中最近 paused 任务（服务重启后恢复暂停态）
        3. DB 中最近 interrupted 筛选（服务重启打断的工作线程）
        4. 无任务
        """
        with _pipeline_lock:
            for task_id, task in reversed(list(_pipeline_tasks.items())):
                if task["status"] in ("running", "queued"):
                    return jsonify({
                        "ok": True,
                        "has_task": True,
                        "task_id": task_id,
                        "kind": task.get("kind", ""),
                        "status": task["status"],
                        "progress": task["progress"],
                        "logs": list(task["logs"][-LOG_TAIL_LINES:]),
                        "error": task["error"],
                        "started_at": task.get("started_at"),
                        "finished_at": task.get("finished_at"),
                    })
        # 2. DB 中最近 paused（服务重启后恢复暂停态，FR-028）
        try:
            with store._connection() as conn:
                prow = conn.execute(
                    "SELECT id, status, current_stage, error_code, error_reason, "
                    "processed_count, source_count, pending_count, match_count, "
                    "mismatch_count, total_dropped, backend_version, updated_at "
                    "FROM screening_runs WHERE status = 'paused' "
                    "ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
        except (sqlite3.Error, RuntimeError):
            prow = None
        if prow is not None and _has_newer_saved_result_than(prow["updated_at"]):
            prow = None
        if prow is not None:
            paused_run = store.get_screening_run(prow["id"]) or {}
            execution_params = paused_run.get("execution_params") or {}
            paused_kind = (
                "recrawl" if str(prow["current_stage"] or "").startswith("recrawl_")
                else "scrape" if prow["current_stage"] == "scrape"
                else "ai_screen"
            )
            return jsonify({
                "ok": True,
                "has_task": True,
                "task_id": prow["id"],
                "kind": paused_kind,
                "status": "paused",
                "stage": prow["current_stage"],
                "progress": {
                    "processed": prow["processed_count"],
                    "total": prow["source_count"],
                    "pending": prow["pending_count"],
                    "match": prow["match_count"],
                    "mismatch": prow["mismatch_count"],
                    "dropped": prow["total_dropped"],
                    "message": prow["error_reason"] or "任务已暂停",
                },
                "logs": [],
                "error": "",
                "pause_info": {
                    "error_code": prow["error_code"],
                    "error_reason": prow["error_reason"],
                },
                "backend_version": prow["backend_version"],
                "current_version": _backend_version,
                "version_match": (prow["backend_version"] == _backend_version),
                "started_at": _iso_epoch_ms(paused_run.get("started_at")),
                "finished_at": _iso_epoch_ms(paused_run.get("finished_at")),
                "resumable": True,
                "source": "database",
                "scrape_task_id": execution_params.get("scrape_task_id"),
                "scrape_completed": _scrape_completed_for_run(execution_params),
                "source_run_id": execution_params.get("source_run_id"),
                "checkpoint_stage": prow["current_stage"],
            })
        # 3. DB 中被进程重启打断的筛选。重启后工作线程已死，
        # 不能假装还在跑——如实告诉前端有个可续跑的中断任务。
        try:
            run = store.latest_interrupted_screening_run()
        except _OPERATIONAL_ERRORS as exc:
            return jsonify({
                "ok": False,
                "error": "task_state_unavailable",
                "detail": type(exc).__name__,
            }), 503
        if run is not None and _has_newer_saved_result_than(run.get("updated_at")):
            run = None
        if run is not None:
            return jsonify({
                "ok": True,
                "has_task": True,
                "task_id": run["id"],
                "kind": (
                    "recrawl" if str(run.get("current_stage") or "").startswith("recrawl_")
                    else "scrape" if run.get("current_stage") == "scrape"
                    else "ai_screen"
                ),
                "status": "interrupted",
                "progress": {"message": "上次 AI 筛选因服务重启被中断"},
                "logs": [],
                "error": "",
                "started_at": _iso_epoch_ms(run.get("started_at")),
                "finished_at": _iso_epoch_ms(run.get("finished_at")),
                "resumable": True,
                "error_code": run.get("error_code"),
                "source_run_id": (run.get("execution_params") or {}).get("source_run_id"),
                "scrape_task_id": (run.get("execution_params") or {}).get("scrape_task_id"),
                "scrape_completed": _scrape_completed_for_run(run.get("execution_params") or {}),
                "frozen_filters": run.get("frozen_filters") or {},
                "profile_summary": str((run.get("execution_params") or {}).get("profile_summary") or ""),
            })
        return jsonify({"ok": True, "has_task": False})

    @app.route("/api/latest-pipeline-result")
    def latest_pipeline_result():
        """Return the persisted latest pipeline run (survives page refresh).

        Only a successful run is persisted, so this always reflects the most
        recent good data.  ``has_result`` is false until the first successful
        run (or if the file is missing/unreadable).

        传入 ``profile_id`` 时，给当前 profile 已标记 interested 的岗位补
        ``_marked: "interested"``，使刷新后「已感兴趣」按钮状态能正确回显
        （跨刷新持久化，见 spec）。匹配按 canonical_url——pipeline 结果的
        ``job_id`` 是 BOSS 岗位 id，profile_jobs.job_id 是内部 UUID，二者
        不能直接相等，统一用规范化链接对齐（同 _build_zone_canonical_urls）。
        """
        payload = store.load_latest_pipeline_result()
        if payload is None:
            return jsonify({"ok": True, "has_result": False})
        result = payload["result"]
        jobs = result.get("jobs", [])

        profile_id = request.args.get("profile_id")
        if profile_id and isinstance(jobs, list) and jobs:
            try:
                store.get_profile(profile_id)
            except KeyError:
                profile_id = None
            if profile_id:
                interested_pjs = store.list_screening_interested(profile_id)
                # 批量预取，避免逐条 store.get_job 的 N+1
                interested_jobs = store.list_jobs_by_ids([pj["job_id"] for pj in interested_pjs])
                interested_urls = set()
                interested_slugs = set()
                for pj in interested_pjs:
                    stored = interested_jobs.get(str(pj["job_id"]))
                    if not stored:
                        continue
                    url = normalize_job_link(stored.get("canonical_url", ""))
                    if url:
                        interested_urls.add(url)
                        # 从 URL 路径提取 BOSS 岗位 slug 作为备用匹配
                        slug = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".html")
                        if slug:
                            interested_slugs.add(slug)
                if interested_urls or interested_slugs:
                    for item in jobs:
                        if not isinstance(item, dict):
                            continue
                        url = normalize_job_link(
                            item.get("source_url") or item.get("job_link") or ""
                        )
                        if url and url in interested_urls:
                            item["_marked"] = "interested"
                        elif interested_slugs and str(item.get("job_id", "")) in interested_slugs:
                            item["_marked"] = "interested"

        return jsonify({
            "ok": True,
            "has_result": True,
            "source_run_id": payload.get("run_id"),
            "status": payload.get("status", "completed"),
            "saved_at": payload.get("saved_at"),
            "started_at": _iso_epoch_ms(payload.get("started_at")),
            "finished_at": _iso_epoch_ms(payload.get("finished_at")),
            "script_params": payload.get("script_params", {}),
            "execution_config": payload.get("execution_config", {}),
            "result": {
                "total_scraped": result.get("total_scraped", 0),
                "total_matched": result.get("total_matched", 0),
                "total_kept": result.get("total_kept", 0),
                "total_dropped": result.get("total_dropped", 0),
                "combinations": result.get("combinations", 0),
                "jobs": jobs,
                "dropped": result.get("dropped", []),
                "profile_summary": result.get("profile_summary", ""),
            },
        })

    # ------------------------------------------------------------------
    # Pipeline 结果增强：按需抓 JD 详情 + 感兴趣/不感兴趣（接入筛选工作台）
    # ------------------------------------------------------------------

    @app.route("/api/reset-latest-result", methods=["POST"])
    def reset_latest_result():
        """重新上传简历时删除上一轮持久化结果，避免刷新后旧结果复活。"""
        cleared = store.clear_latest_pipeline_result()
        return jsonify({"ok": True, "cleared": cleared})

    _job_detail_lock = threading.Lock()

    @app.route("/api/job-detail", methods=["POST"])
    def job_detail():
        """按需抓取单个岗位的 JD 正文（pipeline 列表结果不含 JD）。

        结果页卡片点"加载完整 JD"时调用：pipeline 运行完会自动关闭调试浏览器，
        这里先 ensure_chrome_ready 自动重新拉起，再经 CDP 打开详情页提取 JD。
        单次约 5~20s；用锁串行化并发请求，避免多个抓取争抢同一个 CDP。
        """
        from webui.pipeline_exec import ensure_chrome_ready

        raw = request.get_json(silent=True) or {}
        job_id = str(raw.get("job_id") or "").strip()
        source_url = normalize_job_link(
            raw.get("source_url") or raw.get("job_link") or ""
        )
        if not job_id or not source_url:
            return jsonify({"ok": False, "error": "缺少 job_id 或 source_url"}), 400

        chrome_ok, chrome_err = ensure_chrome_ready()
        if not chrome_ok:
            return jsonify({"ok": False,
                            "error": f"调试浏览器未能就绪：{chrome_err}"}), 503

        source = _make_cdp_source()
        if source is None:
            return jsonify({"ok": False, "error": "抓取源不可用"}), 500

        job = {"job_id": job_id, "source_url": source_url, "job_link": source_url}
        detail_path = str(
            Path(app.config["RESULT_DIR"]) / f"job_detail_{job_id}.json"
        )
        with _job_detail_lock:
            outcome = source.fetch_detail(job, detail_output_path=detail_path)
        if not outcome.ok:
            return jsonify({"ok": False,
                            "error": f"详情抓取失败（{outcome.failed_code}），请确认已登录 BOSS 后重试"}), 502
        jd = str((outcome.detail or {}).get("jd", "")).strip()
        if not jd:
            return jsonify({"ok": False,
                            "error": "详情页未提取到 JD 正文，岗位可能已下架"}), 502
        return jsonify({"ok": True, "jd": jd})

    def _save_pipeline_job_to_store(job):
        """把 pipeline 结果岗位落库到 jobs 表，返回 jobs 记录（链接不安全返回 None）。"""
        canonical_url = normalize_job_link(
            job.get("job_link") or job.get("source_url") or ""
        )
        if not canonical_url:
            return None
        company = job.get("boss_name") or job.get("company") or ""
        return store.save_job(
            canonical_url, canonical_url,
            job.get("title", ""), company,
            job.get("salary", ""), job.get("location", ""), "",
        )

    @app.route("/api/pipeline/jobs/interest", methods=["POST"])
    def pipeline_mark_interest():
        """标记 pipeline 结果岗位为感兴趣：save_job + profile_jobs(interested)。

        复用筛选工作台的持久感兴趣区——标记后可在工作台"感兴趣"区看到
        （list_screening_interested 不按 run_id 过滤）。
        """
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get("profile_id")
        job = raw.get("job") or {}
        if not profile_id:
            raise ValueError("profile_id 不能为空")
        try:
            store.get_profile(profile_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": "画像不存在"}), 404
        saved = _save_pipeline_job_to_store(job)
        if not saved:
            return jsonify({"error_code": "invalid_link", "user_message": "岗位链接不安全"}), 400
        store.mark_screening_interest(profile_id, saved["id"], run_id=None)
        return jsonify({"interest_state": "interested", "job_id": saved["id"]})

    @app.route("/api/pipeline/jobs/reject", methods=["POST"])
    def pipeline_mark_reject():
        """标记 pipeline 结果岗位为不感兴趣：save_job + profile_jobs(deleted)。

        标记后进入筛选工作台垃圾桶区。
        """
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get("profile_id")
        job = raw.get("job") or {}
        if not profile_id:
            raise ValueError("profile_id 不能为空")
        try:
            store.get_profile(profile_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": "画像不存在"}), 404
        saved = _save_pipeline_job_to_store(job)
        if not saved:
            return jsonify({"error_code": "invalid_link", "user_message": "岗位链接不安全"}), 400
        store.mark_screening_reject(profile_id, saved["id"], run_id=None)
        return jsonify({"reject_state": "rejected", "job_id": saved["id"]})

    @app.route("/api/pipeline/jobs/interest/cancel", methods=["POST"])
    def pipeline_cancel_interest():
        """撤销 pipeline 结果岗位的感兴趣标记：profile_jobs.status 回退。

        payload 结构与 /api/pipeline/jobs/interest 一致（profile_id + job）。
        幂等——即便当前不是 interested 也不报错，使前端"感兴趣"按钮可再次点击取消。
        """
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get("profile_id")
        job = raw.get("job") or {}
        if not profile_id or not isinstance(job, dict):
            return jsonify({"error": "missing profile_id or job"}), 400
        try:
            store.get_profile(profile_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": "画像不存在"}), 404

        # Pipeline 列表里的 job_id 是 BOSS 外部 ID，而 profile_jobs 保存的是
        # jobs 表内部 UUID。与标记接口使用同一条 canonical_url 落库/查找链，
        # 才能撤销刚才实际写入的那条记录。
        saved = _save_pipeline_job_to_store(job)
        job_id = saved["id"] if saved else str(
            job.get("stored_job_id") or job.get("job_id") or ""
        )
        if not saved:
            try:
                store.get_job(job_id)
            except KeyError:
                return jsonify({
                    "error_code": "invalid_link",
                    "user_message": "无法定位要撤销的岗位",
                }), 400
        try:
            store.cancel_screening_interest(profile_id, job_id)
        except sqlite3.Error as exc:
            return jsonify({"error": f"撤销感兴趣失败: {exc}"}), 500
        return jsonify({"interest_state": "cancelled", "job_id": job_id})

    @app.route("/api/pipeline/jobs/<job_id>/jd", methods=["POST"])
    def pipeline_job_refetch_jd(job_id):
        """为单个岗位补抓 JD 并回写数据库中对应 job 项。

        用于 JD 抓取失败/缺失的岗位补抓；不重跑 AI、不跨 tab。与
        /api/job-detail 共用 _job_detail_lock 串行化，避免并发争抢 CDP。
        """
        raw = request.get_json(silent=True) or {}
        source_run_id = str(raw.get("source_run_id") or "").strip()
        if source_run_id:
            if store.get_pending_result(source_run_id, job_id) is None:
                return jsonify({
                    "ok": False, "error": "not_pending",
                    "message": "只能补抓当前待确认岗位",
                }), 409
            with _pipeline_lock:
                for existing_id, task in _pipeline_tasks.items():
                    if (task.get("kind") == "recrawl"
                            and task.get("source_run_id") == source_run_id
                            and task.get("status") in ("queued", "running")):
                        return jsonify({
                            "ok": False, "error": "already_running",
                            "existing_task_id": existing_id,
                        }), 409
            task_id = f"recrawl-{uuid.uuid4().hex[:12]}"
            _register_pipeline_task(task_id, "recrawl")
            with _pipeline_lock:
                _pipeline_tasks[task_id]["source_run_id"] = source_run_id
            profile_summary = str(raw.get("profile_summary") or "")
            store.create_screening_run(
                task_id,
                source_count=1,
                execution_params={
                    "source_run_id": source_run_id,
                    "job_ids": [str(job_id)],
                    "profile_summary": profile_summary,
                    "single_retry": True,
                    "browser_account": _account_for_run(),
                },
                backend_version=_backend_version,
            )
            store.update_screening_run(
                task_id, status="running", current_stage="recrawl_fetch_jd"
            )
            with _pipeline_lock:
                _pipeline_tasks[task_id]["browser_account"] = _account_for_run()
            _activate_run_browser()
            try:
                _pipeline_executor.submit(
                    _run_recrawl_task, task_id, [str(job_id)], profile_summary,
                    source_run_id,
                )
            except RuntimeError as exc:
                reason = f"后台任务提交失败：{type(exc).__name__}"
                store.update_screening_run(
                    task_id, status="failed", error_code="internal_error",
                    error_reason=reason,
                )
                store.append_task_event(task_id, "job_fail", {
                    "stage": "recrawl_submit",
                    "job_id": str(job_id),
                    "failed_code": "internal_error",
                    "reason": reason,
                })
                with _pipeline_lock:
                    task = _pipeline_tasks.get(task_id)
                    if task is not None:
                        task["status"] = "failed"
                        task["error"] = reason
                return jsonify({
                    "ok": False, "error": "single_retry_submit_failed",
                }), 500
            return jsonify({
                "ok": True, "task_id": task_id, "source_run_id": source_run_id,
                "single_retry": True,
            }), 202

        _activate_run_browser()
        from webui.pipeline_exec import ensure_chrome_ready

        source_url = normalize_job_link(
            raw.get("source_url") or raw.get("job_link") or ""
        )
        if not source_url:
            return jsonify({"ok": False, "error": "缺少 source_url 或 job_link",
                            "job_id": job_id}), 400

        chrome_ok, chrome_err = ensure_chrome_ready()
        if not chrome_ok:
            return jsonify({"error": f"CDP Chrome 未运行：{chrome_err}",
                            "error_code": "cdp_not_ready"}), 503

        source = _make_cdp_source()
        if source is None:
            return jsonify({"ok": False, "error": "抓取源不可用", "job_id": job_id}), 500

        job = {"job_id": job_id, "source_url": source_url, "job_link": source_url}
        detail_path = str(
            Path(app.config["RESULT_DIR"]) / f"job_detail_{job_id}.json"
        )
        try:
            with _job_detail_lock:
                outcome = source.fetch_detail(job, detail_output_path=detail_path)
        except (OSError, ValueError, RuntimeError) as exc:
            return jsonify({"ok": False, "error": str(exc), "job_id": job_id}), 500

        if not outcome.ok:
            return jsonify({"ok": False,
                            "error": f"详情抓取失败（{outcome.failed_code}），请确认已登录 BOSS 后重试",
                            "job_id": job_id}), 502
        jd = str((outcome.detail or {}).get("jd", "")).strip()
        if not jd:
            return jsonify({"ok": False,
                            "error": "详情页未提取到 JD 正文，岗位可能已下架",
                            "job_id": job_id}), 502

        # 抓到 JD 后回写数据库中匹配的 job 项，并尝试单条 AI 精筛
        persisted = False
        verdict_info: dict = {}
        payload = store.load_latest_pipeline_result()
        if payload is not None:
            result = payload.get("result") or {}
            jobs = result.get("jobs") or []
            matched = False
            for item in jobs:
                if isinstance(item, dict) and str(item.get("job_id")) == str(job_id):
                    matched = True
                    break
            if matched:
                run_id = store.get_latest_done_run_id()
                if run_id:
                    store.update_pipeline_job_jd(run_id, str(job_id), jd)
                    persisted = True

                # 单条 AI 精筛：有画像 + AI 已配置 → 判定后回写
                profile_summary = str(result.get("profile_summary", "")).strip()
                settings = store.get_ai_settings()
                cred_ref = store.get_credential_ref()
                api_key = ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
                endpoint_url = settings.get("endpoint_url", "")
                if profile_summary and api_key and endpoint_url:
                    from webui.ai import match_jds
                    job_for_ai = {"job_id": job_id, "jd": jd, "source_url": source_url}
                    # 把原始岗位信息也带上（标题、薪资等供 AI 参考）
                    for item in jobs:
                        if isinstance(item, dict) and str(item.get("job_id")) == str(job_id):
                            job_for_ai.update({k: v for k, v in item.items()
                                              if k in ("title", "salary", "company", "location")})
                            break
                    try:
                        res = match_jds(
                            [job_for_ai], profile_summary, endpoint_url, api_key,
                            model=settings.get("model", ""))
                    except ai_service.AISecurityError as ai_exc:
                        # JD 已可靠落库；外部 AI 阻断保留具体原因，但不能吞掉
                        # 后续的本地 verdict 持久化异常。
                        verdict_info = {
                            "verdict_reason": ai_service.user_facing_error(
                                ai_exc.error_code
                            )
                        }
                    else:
                        v = (res.get("verdicts") or {}).get(job_id)
                        if v:
                            verdict_info = {
                                "verdict": v.get("verdict"),
                                "verdict_reason": v.get("reason"),
                                "caveats": v.get("caveats", []),
                            }
                            if run_id:
                                store.save_screening_verdicts(run_id, {job_id: v})

        return jsonify({"ok": True, "jd": jd, "job_id": job_id,
                        "persisted": persisted, **verdict_info})

    @app.route("/api/pipeline/recrawl", methods=["POST"])
    def pipeline_recrawl():
        """对待确认（uncertain）岗位批量重抓：缺 JD 的补抓 JD，有 JD 的用画像重跑 AI 精筛。

        切片8（FR-022/FR-037）：防并发——同 source_run_id 已有 running 重抓任务时拒绝。
        切片8（FR-023）：job_ids 缺省时从 screening_pending_results 自动读取（全部重抓只处理待确认）。
        复用 fetch_job_details（CDP 通道，内部按 detail_batch_size 分批 + 冷却）与
        match_jds（按 match_batch_size 分批）。进度走与 AI 筛选相同的轮询机制
        （前端 pollTask + TaskProgress）。判定与 JD 原地回写 screening_results，
        返回 updates 映射供前端就地合并，保留当前结果 tab。
        """
        raw = request.get_json(silent=True) or {}
        job_ids = raw.get("job_ids")
        profile_summary = str(raw.get("profile_summary") or "")
        source_run_id = str(
            raw.get("source_run_id") or store.get_latest_done_run_id() or ""
        ).strip()
        if not source_run_id:
            return jsonify({"ok": False, "error": "missing_source_run_id"}), 409
        pending_rows = store.list_pending_results(source_run_id)
        pending_ids = {str(item.get("job_id") or "") for item in pending_rows}
        # FR-023：job_ids 缺省或 "auto" → 从 screening_pending_results 读
        if not job_ids or job_ids == "auto":
            job_ids = sorted(pending_ids)
            if not job_ids:
                return jsonify({"ok": False, "error": "没有待确认岗位可重抓"}), 400
        if not isinstance(job_ids, list) or not job_ids:
            return jsonify({"ok": False, "error": "缺少 job_ids"}), 400
        requested_ids = {str(job_id) for job_id in job_ids}
        non_pending = sorted(requested_ids - pending_ids)
        if non_pending:
            return jsonify({
                "ok": False,
                "error": "non_pending_job_ids",
                "message": "只能重抓当前待确认岗位",
                "job_ids": non_pending,
            }), 409
        job_ids = sorted(requested_ids)
        task_id = f"recrawl-{uuid.uuid4().hex[:12]}"
        claimed_task, existing_task_id = _claim_recrawl_start(
            task_id, source_run_id
        )
        if claimed_task is None:
            return jsonify({
                "ok": False,
                "error": "已有重抓任务在运行，请等待完成或取消后再试",
                "existing_task_id": existing_task_id,
            }), 409
        claimed_task["browser_account"] = _account_for_run()
        _activate_run_browser()
        try:
            store.create_screening_run(
                task_id,
                source_count=len(job_ids),
                execution_params={
                    "source_run_id": source_run_id,
                    "job_ids": [str(x) for x in job_ids],
                    "profile_summary": profile_summary,
                    "browser_account": _account_for_run(),
                },
                backend_version=_backend_version,
            )
            store.update_screening_run(
                task_id, status="running", current_stage="recrawl_fetch_jd"
            )
        except _OPERATIONAL_ERRORS as exc:
            _release_pipeline_claim(task_id, claimed_task)
            return jsonify({
                "ok": False,
                "error": f"重抓任务持久化失败：{type(exc).__name__}",
            }), 500
        try:
            _pipeline_executor.submit(
                _run_recrawl_task, task_id, [str(x) for x in job_ids],
                profile_summary, source_run_id,
            )
        except RuntimeError as exc:
            try:
                store.update_screening_run(
                    task_id, status="failed", error_code="internal_error",
                    error_reason=f"后台任务提交失败：{type(exc).__name__}",
                )
            finally:
                _release_pipeline_claim(task_id, claimed_task)
            return jsonify({
                "ok": False, "error": "recrawl_submit_failed",
            }), 500
        return jsonify({"ok": True, "task_id": task_id, "source_run_id": source_run_id}), 202

    @app.route("/api/recrawl/continue/<task_id>", methods=["POST"])
    def continue_recrawl(task_id, _block_checked=False):
        """Resume a paused recrawl in place using its persisted checkpoint.

        SPEC011 T015: 实验租约持有时拒绝继续（FR-035）。
        """
        # SPEC011 T015/FR-035: 实验租约门禁
        ok, err_resp = _check_tuning_lease_conflict()
        if not ok:
            return err_resp
        run = store.get_screening_run(task_id)
        if run is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        stage = str(run.get("current_stage") or "")
        if run.get("status") != "paused" or not stage.startswith("recrawl_"):
            return jsonify({
                "ok": False, "error": "not_paused_recrawl",
                "status": run.get("status"), "stage": stage,
            }), 409
        _activate_run_browser(run)
        if not _block_checked:
            passed, code, reason = _check_resume_block(run)
            if not passed:
                return jsonify({
                    "ok": False, "error": "block_not_resolved",
                    "error_code": code, "error_reason": reason,
                    "status": "paused",
                }), 409
        with _pipeline_lock:
            existing = _pipeline_tasks.get(task_id)
            if existing is not None and existing.get("status") in ("queued", "running"):
                return jsonify({"ok": False, "error": "already_running"}), 409

        params = run.get("execution_params") or {}
        source_run_id = str(params.get("source_run_id") or "")
        job_ids = [str(job_id) for job_id in (params.get("job_ids") or [])]
        profile_summary = str(params.get("profile_summary") or "")
        checkpoint_stage = "recrawl_ai" if stage == "recrawl_ai" else "recrawl_jd"
        completed_job_ids = store.load_checkpoint(task_id, checkpoint_stage)
        if not job_ids:
            return jsonify({"ok": False, "error": "missing_job_ids"}), 409

        claimed_task, previous_task = _claim_pipeline_task_id(
            task_id, "recrawl"
        )
        if claimed_task is None:
            return jsonify({"ok": False, "error": "already_running"}), 409
        claimed_task["source_run_id"] = source_run_id
        claimed_task["browser_account"] = _account_for_run(run)
        resume_params = dict(run.get("execution_params") or {})
        if not str(resume_params.get("browser_account") or ""):
            resume_params["browser_account"] = _account_for_run(run)
            store.update_screening_execution_params(task_id, resume_params)
        try:
            store.update_screening_run(task_id, status="running")
            store.append_task_event(task_id, "resume", {
                "stage": stage, "completed": len(completed_job_ids),
            })
            _pipeline_executor.submit(
                _run_recrawl_task, task_id, job_ids, profile_summary,
                source_run_id, completed_job_ids,
            )
        except _OPERATIONAL_ERRORS as exc:
            try:
                current = store.get_screening_run(task_id)
                if current is not None and current.get("status") == "running":
                    store.update_screening_run(
                        task_id, status="paused",
                        error_code=str(run.get("error_code") or "internal_error"),
                        error_reason=(
                            str(run.get("error_reason") or "")
                            or f"继续任务提交失败：{type(exc).__name__}"
                        ),
                    )
            finally:
                _release_pipeline_claim(
                    task_id, claimed_task, previous_task
                )
            return jsonify({
                "ok": False, "error": "resume_submit_failed",
            }), 500
        return jsonify({
            "ok": True,
            "task_id": task_id,
            "source_run_id": source_run_id,
            "completed_job_ids": sorted(completed_job_ids),
            "stage": stage,
        })

    def _run_recrawl_task(task_id, job_ids, profile_summary, source_run_id="",
                          completed_job_ids=None):
        """批量重抓后台任务：补 JD + 重判，进度与结果通过 _pipeline_tasks 暴露。

        切片8：``source_run_id`` 用于持久化（recrawl task_id 不是 screening_runs 行）。
        暂停时写入 store（用 task_id 作为 run_id 占位），保存 checkpoint，
        服务重启后用户可点继续从 checkpoint 恢复。
        """
        from webui.pipeline_exec import (
            ensure_chrome_ready, close_debug_chrome, fetch_job_details, load_advanced_settings,
            _FAILED_CODE_LABELS,
        )
        from webui.ai import match_jds

        # 画像兜底：前端刷新后传空，从落盘结果里恢复（跟本轮抓取绑定，下轮覆盖）
        if not profile_summary.strip():
            payload = store.load_latest_pipeline_result(source_run_id or None)
            if payload:
                profile_summary = str(
                    (payload.get("result") or {}).get("profile_summary", "")
                )

        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)
            if task is None:
                task = {
                    "kind": "recrawl", "status": "queued", "progress": {},
                    "logs": [], "result": None, "error": "",
                    "started_at": int(time.time() * 1000), "finished_at": None,
                    "stop_event": threading.Event(),
                }
                _pipeline_tasks[task_id] = task
            if task.get("status") == "cancelled":
                return
            task["status"] = "running"
            stop_event = task.get("stop_event")

        _activate_task_browser(task_id)

        last_event_stage = None

        def emit(**kw):
            nonlocal last_event_stage
            stage = str(kw.get("stage", ""))
            current = int(kw.get("current") or 0)
            total = int(kw.get("total") or 0)
            # 重抓只有两阶段，不复用主筛选的权重（那个 0-40% 留给粗筛了）
            _RECRRAWL_WEIGHTS = {"fetch_jd": (0, 60), "screen_b": (60, 100), "done": (100, 100)}
            start, end = _RECRRAWL_WEIGHTS.get(stage, (0, 100))
            ratio = min(1.0, max(0.0, current / total)) if total > 0 else 0.0
            kw["overall_percent"] = min(100, round(start + (end - start) * ratio))
            if not kw.get("message"):
                kw["message"] = _SCREEN_STAGE_MESSAGES.get(stage, "")
            event_stage = _EVENT_STAGE_NAMES.get(stage)
            stage_events = []
            if stage == "done" and last_event_stage:
                stage_events.append(("stage_complete", {"stage": last_event_stage}))
                last_event_stage = None
            elif event_stage and event_stage != last_event_stage:
                if last_event_stage:
                    stage_events.append(("stage_complete", {"stage": last_event_stage}))
                stage_events.append(("stage_start", {"stage": event_stage}))
                last_event_stage = event_stage
            if stage_events:
                store.append_task_events(task_id, stage_events)
            with _pipeline_lock:
                t = _pipeline_tasks.get(task_id)
                if t is None:
                    return
                t["progress"] = kw

        def _stop_requested():
            return stop_event is not None and stop_event.is_set()

        def _pause_recrawl_source_unavailable(reason):
            """Persist a CDP-wide recrawl hard stop before exposing paused state."""
            code = "source_cdp_unavailable"
            completed = set(completed_job_ids or set()) | set(fetched_jd)
            failed_jobs = [
                {
                    "job_id": str(job.get("job_id") or ""),
                    "jd": "",
                    "jd_failed_code": code,
                    "jd_failed_reason": reason,
                }
                for job in no_jd
                if str(job.get("job_id") or "") not in completed
            ]
            _persist_jd_job_failures(
                task_id,
                failed_jobs,
                stage="recrawl_fetch_jd",
                source_run_id=source_run_id,
            )
            store.save_checkpoint(task_id, "recrawl_jd", sorted(completed))
            store.update_screening_run(
                task_id,
                status="paused",
                error_code=code,
                error_reason=reason,
                current_stage="recrawl_fetch_jd",
                processed_count=len(completed),
            )
            store.append_task_event(
                task_id,
                "pause",
                {
                    "stage": "recrawl_fetch_jd",
                    "code": code,
                    "processed": len(completed),
                    "total": len(no_jd),
                },
            )
            with _pipeline_lock:
                current = _pipeline_tasks.get(task_id)
                if current is not None:
                    current["status"] = "paused"
                    current["error"] = reason

        updates: dict = {}
        try:
            payload = store.load_latest_pipeline_result(source_run_id or None)
            run_id = source_run_id or store.get_latest_done_run_id()
            jobs = (payload or {}).get("result", {}).get("jobs", []) if payload else []
            by_id = {str(j.get("job_id", "")): j for j in jobs if isinstance(j, dict)}
            targets = [by_id[jid] for jid in job_ids if jid in by_id]
            total = len(targets)
            emit(stage="fetch_jd", current=0, total=total,
                 message=f"准备重抓 {total} 个待确认岗位…")
            if not targets:
                emit(stage="done", current=0, total=0, message="没有可重抓的岗位")
                with _pipeline_lock:
                    t = _pipeline_tasks.get(task_id)
                    if t is not None:
                        t["status"] = "done"
                        t["result"] = {"updates": {}}
                _schedule_pipeline_task_cleanup(task_id)
                return

            settings = store.get_ai_settings()
            cred_ref = store.get_credential_ref()
            api_key = ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
            endpoint = settings.get("endpoint_url", "")
            model = settings.get("model", "")
            has_ai = bool(api_key and endpoint)

            # 1) 缺 JD 的先补抓（复用详情 CDP 通道，内部已按 detail_batch_size 分批 + 冷却）
            no_jd = []
            for j in targets:
                if str(j.get("jd", "")).strip():
                    continue
                url = normalize_job_link(
                    j.get("source_url") or j.get("job_link") or j.get("canonical_url") or ""
                )
                if url:
                    no_jd.append({"job_id": str(j.get("job_id", "")),
                                  "source_url": url, "job_link": url})
            fetched_jd: dict = {}
            detail_jobs: list = []
            if no_jd:
                chrome_ok, chrome_err = ensure_chrome_ready()
                if chrome_ok:
                    source = _make_cdp_source()
                    if source is not None:
                        def _jd_progress(done, tot):
                            emit(stage="fetch_jd", current=min(done, total), total=total,
                                 message=f"抓取 JD {min(done, total)}/{total}")
                        detail = fetch_job_details(
                            no_jd, source, artifact_dir=app.config["RESULT_DIR"],
                            stop_event=stop_event, progress=_jd_progress,
                            completed_job_ids=completed_job_ids,
                        )
                        detail_jobs = detail.get("jobs", [])
                        for j in detail_jobs:
                            jid = str(j.get("job_id", ""))
                            jd = str(j.get("jd", "")).strip()
                            if jid and jd:
                                fetched_jd[jid] = jd
                        completed_jd_ids = set(completed_job_ids or set()) | set(fetched_jd)
                        store.save_recrawl_jd_and_checkpoint(
                            run_id, task_id, fetched_jd, completed_jd_ids
                        )
                        for jid, jd in fetched_jd.items():
                            updates.setdefault(jid, {})["jd"] = jd
                        if detail.get("hard_stop"):
                            # 暂停，不关浏览器（用户需要它处理验证码/登录）
                            _hs_code = detail.get("hard_stop_code") or "source_blocked"
                            _hs_label = _FAILED_CODE_LABELS.get(_hs_code, "抓取被拦截")
                            _hs_hint = next((
                                str(job.get("jd_failed_reason") or "").strip()
                                for job in detail_jobs or []
                                if job.get("jd_failed_reason")
                            ), "")
                            _hs_reason = _hs_hint if _hs_hint and _hs_hint != _hs_label else _hs_label
                            _persist_jd_job_failures(
                                task_id,
                                detail_jobs,
                                stage="recrawl_fetch_jd",
                                source_run_id=source_run_id,
                            )
                            # 切片8：持久化暂停状态 + checkpoint（已抓 JD 的 job_id）
                            store.update_screening_run(
                                task_id, status="paused", error_code=_hs_code,
                                current_stage="recrawl_fetch_jd",
                                processed_count=len(completed_jd_ids),
                                error_reason=_hs_reason)
                            store.append_task_event(
                                task_id, "pause",
                                {"stage": "recrawl_fetch_jd", "code": _hs_code,
                                 "fetched": len(fetched_jd), "total": len(no_jd)})
                            with _pipeline_lock:
                                t = _pipeline_tasks.get(task_id)
                                if t is not None:
                                    t["status"] = "paused"
                                    t["error"] = (f"重抓 JD 时{_hs_reason}，已抓部分已保存；"
                                                  "请在自动化浏览器中处理，完成后点「继续」")
                            return
                        if detail.get("stopped"):
                            close_debug_chrome()
                            with _pipeline_lock:
                                t = _pipeline_tasks.get(task_id)
                                if t is not None:
                                    t["status"] = "cancelled"
                                    t["error"] = "用户已停止重抓"
                            _schedule_pipeline_task_cleanup(task_id)
                            return
                        close_debug_chrome()
                    else:
                        reason = "CDP 抓取源不可用，请确认调试浏览器后继续"
                        emit(stage="fetch_jd", current=0, total=total, message=reason)
                        _pause_recrawl_source_unavailable(reason)
                        return
                else:
                    reason = f"调试浏览器未就绪（{chrome_err}），请处理后继续"
                    emit(stage="fetch_jd", current=0, total=total, message=reason)
                    _pause_recrawl_source_unavailable(reason)
                    return

            # 补抓仍失败的岗位：把具体原因回写前端（验证码/限流等）
            for j in detail_jobs:
                jid = str(j.get("job_id", ""))
                code = j.get("jd_failed_code", "")
                if jid and code and jid not in fetched_jd:
                    label = _FAILED_CODE_LABELS.get(code, "")
                    detail_reason = str(j.get("jd_failed_reason") or "").strip()
                    reason = (
                        f"未抓到 JD（{detail_reason}），无法精筛"
                        if detail_reason else
                        (f"未抓到 JD（{label}），无法精筛" if label else
                         "未抓到 JD，无法精筛")
                    )
                    updates.setdefault(jid, {})["verdict_reason"] = reason

            # 2) 有 JD 且有画像的，重跑 AI 精筛
            if not has_ai:
                reason = "AI 未配置，已保留补抓结果；配置 AI 后可继续判定"
                emit(stage="screen_b", current=0, total=total, message=reason)
                store.update_screening_run(
                    task_id, status="paused", error_code="ai_key_invalid",
                    current_stage="recrawl_ai", processed_count=0,
                    error_reason=reason,
                )
                store.save_checkpoint(task_id, "recrawl_ai", [])
                store.append_task_event(task_id, "pause", {
                    "stage": "recrawl_ai", "code": "ai_key_invalid",
                    "processed": 0, "total": total,
                })
                with _pipeline_lock:
                    t = _pipeline_tasks.get(task_id)
                    if t is not None:
                        t["status"] = "paused"
                        t["error"] = reason
                return
            elif not profile_summary.strip():
                emit(stage="screen_b", current=total, total=total,
                     message="未填写求职画像，跳过 AI 重判")
            else:
                to_judge = []
                for j in targets:
                    jid = str(j.get("job_id", ""))
                    jd = str(j.get("jd", "")).strip() or fetched_jd.get(jid, "")
                    if jd:
                        to_judge.append(j)
                if to_judge:
                    _adv = _load_legacy_advanced_settings()
                    match_batch = int(_adv.get("match_batch_size") or 4)
                    recrawl_completed_ids = set(completed_job_ids or set())
                    verdicts: dict = store.load_screening_verdicts(task_id)
                    to_judge = [
                        job for job in to_judge
                        if str(job.get("job_id", "")) not in recrawl_completed_ids
                    ]
                    _recrawl_ai_pause = False
                    for start in range(0, len(to_judge), match_batch):
                        if _stop_requested():
                            break
                        chunk = to_judge[start:start + match_batch]
                        try:
                            res = match_jds(chunk, profile_summary, endpoint, api_key,
                                            model=model, raise_on_systemic=True)
                        except ai_service.AISecurityError as _ai_exc:
                            # 切片8：systemic 错误暂停（不批量变 uncertain 后完成）
                            from webui.ai import map_ai_error_to_block_code, AISecurityError
                            if isinstance(_ai_exc, AISecurityError):
                                _block_code = map_ai_error_to_block_code(_ai_exc.error_code)
                                if _block_code:
                                    store.update_screening_run(
                                        task_id, status="paused", error_code=_block_code,
                                        current_stage="recrawl_ai",
                                        processed_count=len(recrawl_completed_ids))
                                    store.save_checkpoint(
                                        task_id, "recrawl_ai",
                                        sorted(recrawl_completed_ids),
                                    )
                                    store.append_task_event(
                                        task_id, "pause",
                                        {"stage": "recrawl_ai", "code": _block_code,
                                         "processed": len(recrawl_completed_ids),
                                         "total": len(targets)})
                                    with _pipeline_lock:
                                        t = _pipeline_tasks.get(task_id)
                                        if t is not None:
                                            t["status"] = "paused"
                                            t["error"] = (
                                                f"重抓 AI 重判被阻断（{_block_code}）："
                                                f"已判 {len(recrawl_completed_ids)}/{len(targets)} 条。"
                                                "处理完成后点「继续」"
                                            )
                                    _recrawl_ai_pause = True
                                    break
                            raise
                        batch_verdicts = res.get("verdicts", {})
                        verdicts.update(batch_verdicts)
                        recrawl_completed_ids.update(batch_verdicts)
                        store.save_verdict_and_checkpoint_atomic(
                            task_id, "recrawl_ai", batch_verdicts,
                            sorted(recrawl_completed_ids),
                        )
                        emit(stage="screen_b", current=len(recrawl_completed_ids), total=total,
                             message=f"AI 重判 {len(recrawl_completed_ids)}/{total}")
                    if _recrawl_ai_pause:
                        return
                    if run_id:
                        store.save_screening_verdicts(run_id, verdicts)
                    for jid, v in verdicts.items():
                        u = updates.setdefault(jid, {})
                        u["verdict"] = v.get("verdict")
                        u["verdict_reason"] = v.get("reason")
                        u["caveats"] = v.get("caveats", [])
                    # 切片8：补救成功后从 screening_pending_results 移除（FR-023）
                    if source_run_id:
                        for jid in verdicts:
                            v = verdicts[jid].get("verdict")
                            if v in ("match", "not_match"):
                                store.delete_pending_result(source_run_id, jid)

            emit(stage="done", current=total, total=total, message="重抓完成")
            store.append_task_events(task_id, [
                (
                    "job_success" if update.get("verdict") in ("match", "not_match")
                    or bool(update.get("jd")) else "job_fail",
                    {
                        "stage": "recrawl", "job_id": str(job_id),
                        "verdict": update.get("verdict"),
                        "reason": update.get("verdict_reason", ""),
                    },
                )
                for job_id, update in updates.items()
            ])
            store.update_screening_run(
                task_id,
                status="cancelled" if _stop_requested() else "succeeded",
                current_stage="done",
            )
            with _pipeline_lock:
                t = _pipeline_tasks.get(task_id)
                if t is not None:
                    t["status"] = "cancelled" if _stop_requested() else "done"
                    t["result"] = {"updates": updates}
            _schedule_pipeline_task_cleanup(task_id)
        except Exception as exc:
            error_message = f"重抓异常：{type(exc).__name__}"
            persistence_error = None
            try:
                run = store.get_screening_run(task_id)
                if run and run.get("status") in ("queued", "running", "paused"):
                    store.update_screening_run(
                        task_id, status="failed", error_code="internal_error",
                        error_reason=error_message,
                    )
            except _OPERATIONAL_ERRORS as persist_exc:
                persistence_error = type(persist_exc).__name__
            with _pipeline_lock:
                t = _pipeline_tasks.get(task_id)
                if t is not None:
                    t["status"] = "failed"
                    t["error"] = (
                        error_message if persistence_error is None
                        else f"{error_message}；状态保存失败：{persistence_error}"
                    )
            _schedule_pipeline_task_cleanup(task_id)

    # ===================================================================
    # 010 healthy-pipeline-recovery: 统一接口层（FR-005/FR-020/FR-022/
    # FR-024/FR-037/FR-039/FR-041）
    # ===================================================================

    import hashlib as _hashlib_mod
    import time as _time_mod

    # FR-039：后端版本标识（启动时计算，继续任务时校验）
    try:
        _backend_version = "011-ui-fixes"
        _backend_files = sorted(
            [*Path(__file__).resolve().parent.glob("*.py"), SCRAPER.resolve()],
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

    @app.route("/api/version", methods=["GET"])
    def api_version():
        """FR-039：返回后端版本标识。前端用于校验是否需要刷新。"""
        return jsonify({
            "backend_version": _backend_version,
            "build_hash": _build_hash,
            "build_time": _build_time,
        })

    def _run_to_task_status(db_status: str) -> str:
        """DB 状态 → 统一任务状态名（FR-005）。"""
        mapping = {
            "queued": "waiting",
            "running": "running",
            "paused": "paused",
            "succeeded": "completed",
            "partial": "completed_with_pending",
            "failed": "failed",
            "interrupted": "cancelled",
        }
        return mapping.get(db_status, "failed")

    @app.route("/api/task-state/<run_id>", methods=["GET"])
    def api_task_state(run_id: str):
        """FR-037：统一任务状态接口。

        返回完整状态画面：status/stage/progress/success_count/fail_count/
        unstarted_count/total/pause_info(含 error_code/error_reason)。
        前端 3 个 snapshot 统一从此接口拉取。
        """
        from webui.pipeline_exec import _FAILED_CODE_LABELS as failed_code_labels

        with _pipeline_lock:
            task = _pipeline_tasks.get(run_id)
            if task is not None:
                if task["status"] in ("done", "failed", "cancelled") and task.get(
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
                }
            else:
                live = None
        run = store.get_screening_run(run_id)
        if run is None and live is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        live_progress = (live or {}).get("progress") or {}
        source = int((run or {}).get("source_count") or live_progress.get("total") or 0)
        processed = int((run or {}).get("processed_count") or live_progress.get("current") or 0)
        match = int((run or {}).get("match_count") or 0)
        mismatch = int((run or {}).get("mismatch_count") or 0)
        pending = int((run or {}).get("pending_count") or 0)
        dropped = int((run or {}).get("total_dropped") or 0)
        kept = int((run or {}).get("total_kept") or 0)
        if kept <= 0:
            kept = max(0, source - dropped)
        error_code = (run or {}).get("error_code")
        error_reason = (run or {}).get("error_reason")
        stage = (
            (run or {}).get("current_stage")
            or live_progress.get("stage")
            or "unknown"
        )
        # processed_count 只记录已成功完成的当前阶段工作单元；pending
        # 是已失败并进入待确认的独立工作单元，两者不能互相扣减。
        # JD 详情/精筛阶段只处理粗筛保留的岗位；原始列表里的 dropped
        # 已经作为独立结果展示，不能继续混进当前阶段的成功/失败/未开始。
        jd_stage = stage in ("jd_detail", "fetch_jd", "ai_fine", "screen_b", "done")
        stage_total = kept if jd_stage and kept > 0 else source
        # processed_count 只记录已成功完成的当前阶段工作单元；pending
        # 是已失败并进入待确认的独立工作单元，两者不能互相扣减。
        fail_count = pending
        success_count = max(match + mismatch, processed)
        completed_count = min(stage_total, success_count + fail_count)
        unstarted = max(0, stage_total - completed_count)
        overall_percent = (
            round(completed_count / stage_total * 100, 1)
            if stage_total > 0 else 0
        )
        progress = dict(live_progress)
        progress.setdefault("overall_percent", overall_percent)
        progress.setdefault("current", success_count if jd_stage else completed_count)
        progress.setdefault("total", stage_total)
        pause_info = None
        effective_status = (
            str(live.get("status")) if live is not None
            else _run_to_task_status(str(run["status"]))
        )
        if effective_status == "paused" or (
                error_code and error_code in SYSTEMIC_BLOCK_CODES):
            pause_info = {
                "error_code": error_code,
                "error_reason": error_reason or failed_code_labels.get(
                    error_code, error_code or ""),
            }
        started_at = _iso_epoch_ms((live or {}).get("started_at"))
        if started_at is None:
            started_at = _iso_epoch_ms((run or {}).get("started_at"))
        finished_at = _iso_epoch_ms((live or {}).get("finished_at"))
        if finished_at is None:
            finished_at = _iso_epoch_ms((run or {}).get("finished_at"))
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
                "status": _run_to_task_status(run["status"]),
                "message": "只有 paused 状态的任务才能继续",
            }), 409
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
        stage = str(run.get("current_stage") or "")
        if stage.startswith("recrawl_"):
            passed, code, reason = _check_resume_block(run)
            if not passed:
                return jsonify({
                    "ok": False, "error": "block_not_resolved",
                    "error_code": code, "error_reason": reason,
                    "status": "paused",
                }), 409
            return continue_recrawl(run_id, _block_checked=True)
        if stage == "scrape":
            passed, code, reason = _check_resume_block(run)
            if not passed:
                return jsonify({
                    "ok": False, "error": "block_not_resolved",
                    "error_code": code, "error_reason": reason,
                    "status": "paused",
                }), 409
            return continue_execute_search(run_id, _block_checked=True)

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
        if not scrape_task_id:
            return jsonify({"ok": False, "error": "missing_scrape_task_id"}), 409
        source_jobs = store.load_scrape_run_jobs(scrape_task_id)
        if not source_jobs:
            return jsonify({
                "ok": False,
                "error": "missing_scrape_snapshot",
                "message": "抓取岗位快照缺失，无法安全继续 AI 筛选",
            }), 409

        if not _claim_resume(run_id):
            return jsonify({
                "ok": False,
                "error": "already_running",
                "message": "该任务正在继续，请勿重复点击",
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
        claimed_task, previous_task = _claim_pipeline_task_id(task_id, "ai_screen")
        if claimed_task is None:
            _release_resume_claim(run_id)
            return jsonify({
                "ok": False,
                "error": "already_running",
                "message": "该任务正在继续，请勿重复点击",
            }), 409
        claimed_task["source_task_id"] = scrape_task_id
        claimed_task["browser_account"] = _account_for_run(run)
        resume_params = dict(run.get("execution_params") or {})
        if not str(resume_params.get("browser_account") or ""):
            resume_params["browser_account"] = _account_for_run(run)
            store.update_screening_execution_params(run_id, resume_params)
        start_gate = threading.Event()
        abort_start = threading.Event()

        def run_after_claim_commits(
                task_id, frozen_filters, frozen_profile, source_task_id,
                resume_from_run_id):
            start_gate.wait()
            if not abort_start.is_set():
                _run_ai_screen_task(
                    task_id,
                    frozen_filters,
                    frozen_profile,
                    source_task_id,
                    resume_from_run_id,
                )

        try:
            future = _pipeline_executor.submit(
                run_after_claim_commits,
                task_id,
                run.get("frozen_filters") or {},
                profile_summary,
                scrape_task_id,
                run_id,
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
        })

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

        # 有 DB 身份时先提交 durable cancel，再发布内存状态。写入失败时
        # 保持内存原状态，避免页面显示 cancelled 而数据库仍在 running。
        if run is not None:
            try:
                if run["status"] not in (
                    "succeeded", "partial", "failed", "interrupted",
                ):
                    store.update_screening_run(run_id, status="cancelled")
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
                            current["status"] = _run_to_task_status(latest["status"])
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
                    _run_to_task_status(run["status"])
                    if run is not None else "cancelled"
                )
                current["error"] = "用户已取消"
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
            "status": (
                _run_to_task_status(run["status"]) if run is not None else "cancelled"
            ),
            "processed_count": int((run or {}).get("processed_count") or 0),
            "message": "任务已取消，已有结果保留",
        })

    @app.route("/api/task/finish/<run_id>", methods=["POST"])
    def api_task_finish(run_id: str):
        """结束暂停任务并生成可展示的部分结果快照。"""
        run = store.get_screening_run(run_id)
        if run is None:
            return jsonify({"ok": False, "error": "run_not_found"}), 404
        restart_interrupted = (
            run["status"] == "interrupted"
            and str(run.get("error_code") or "") == "restart"
        )
        if run["status"] != "paused" and not restart_interrupted:
            return jsonify({
                "ok": False, "error": "not_paused",
                "status": _run_to_task_status(run["status"]),
                "message": "只有 paused 或服务重启中断的任务才能结束并保存",
            }), 409
        with _pipeline_lock:
            task = _pipeline_tasks.get(run_id)
            if task is not None and task.get("status") == "running":
                return jsonify({
                    "ok": False, "error": "already_running",
                    "message": "任务正在运行，请先停止再结束",
                }), 409
            if task is not None and task.get("stop_event") is not None:
                task["stop_event"].set()
            if run_id in _resume_claims:
                return jsonify({
                    "ok": False, "error": "already_running",
                    "message": "该任务已被续跑接管，请结束续跑任务后再保存",
                }), 409
        params = run.get("execution_params") or {}
        scrape_task_id = str(params.get("scrape_task_id") or "")
        source_run_id = str(params.get("source_run_id") or "")
        source_jobs = []
        verdicts = {}
        pending_rows = []
        jd_map = {}
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
            source_jobs = ((payload or {}).get("result") or {}).get("jobs") or []
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
        if not source_jobs:
            return jsonify({
                "ok": False, "error": "missing_scrape_snapshot",
                "message": "抓取岗位快照缺失，无法生成部分结果",
            }), 409
        profile_summary = str(params.get("profile_summary") or "")
        if not profile_summary and source_run_id:
            source_payload = store.load_latest_pipeline_result(source_run_id)
            profile_summary = str(((source_payload or {}).get("result") or {}).get("profile_summary") or "")
        result = _build_partial_pipeline_result(
            source_jobs, verdicts, pending_rows, jd_map,
            profile_summary,
        )
        snapshot_run_id = store.save_pipeline_result(
            result, {"screening": run.get("frozen_filters") or {}},
            started_at=run.get("started_at"),
            finished_at=int(time.time() * 1000),
            execution_config=params.get("execution_config") or {},
            status="partial",
        )
        store.update_screening_run(
            run_id, status="cancelled", current_stage="done",
            error_code="user_finished",
            error_reason="用户提前结束，已保存部分结果",
        )
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
            "status": "completed_with_pending", "result": result,
            "message": "任务已结束，已完成结果已保存",
        })

    @app.route("/api/recovery/preview/<run_id>", methods=["GET"])
    def api_recovery_preview(run_id: str):
        """FR-041：历史恢复只读预演接口。run_id 参数仅作占位，
        实际预演两个历史 run（15847d27 + e6250f0e）。
        """
        from webui.historical_recovery import preview_recovery, ROUGH_RUN_ID, FINE_RUN_ID
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

    @app.route("/api/tuning/experiments", methods=["POST"])
    def tuning_create_experiment():
        """POST /api/tuning/experiments

        创建 draft 状态的实验。不启动压力工作。
        """
        body = request.get_json(silent=True) or {}
        spec_version = body.get("spec_version")
        source_scope = body.get("source_scope")
        quality_context = body.get("quality_context")
        if not spec_version or not source_scope or not quality_context:
            return jsonify({
                "ok": False, "error_code": "invalid_request",
                "error": (
                    "缺少 spec_version、source_scope 或 quality_context"
                ),
            }), 400
        if not isinstance(source_scope, dict):
            return jsonify({
                "ok": False, "error_code": "invalid_request",
                "error": "source_scope 必须是对象",
            }), 400
        from webui.tuning import TuningController
        controller = TuningController(store)
        try:
            experiment = controller.create_experiment_with_input(
                spec_version=spec_version,
                source_scope=source_scope,
                workloads=body.get("workloads") or [],
                quality_context=quality_context,
            )
        except (ValueError, TypeError) as exc:
            return jsonify({
                "ok": False, "error_code": "invalid_request",
                "error": str(exc),
            }), 400
        return jsonify({
            "ok": True,
            "experiment_id": experiment["id"],
            "status": experiment["status"],
        }), 201

    @app.route("/api/tuning/experiments/<experiment_id>",
               methods=["GET"])
    def tuning_get_experiment(experiment_id: str):
        """GET /api/tuning/experiments/{id}

        返回实验持久化快照。
        """
        try:
            exp = store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": "实验不存在",
            }), 404
        from webui.tuning import TuningController
        controller = TuningController(store)
        # 计算进度和剩余时间
        progress = controller.project_remaining_time(experiment_id)
        if progress is None:
            progress = {
                "confirmed_rounds": 0,
                "remaining_required_rounds": 0,
                "estimated_remaining_seconds": 0,
            }
        # 计算可执行操作
        status = exp["status"]
        can_cancel = status not in ("cancelled", "failed", "completed")
        can_resume = status == "blocked"
        can_apply = status == "completed"
        return jsonify({
            "ok": True,
            "experiment": {
                "id": exp["id"],
                "status": status,
                "spec_version": exp["spec_version"],
                "current_stage": exp.get("current_stage"),
                "current_candidate_id": exp.get("current_candidate_id"),
                "input_version_id": exp.get("input_version_id"),
                "quality_reference_id": exp.get("quality_reference_id"),
                "blocked_code": exp.get("blocked_code"),
                "blocked_reason": exp.get("blocked_reason"),
                "source_scope": exp.get("source_scope", {}),
                "created_at": exp.get("created_at"),
                "progress": progress,
                "can_cancel": can_cancel,
                "can_resume": can_resume,
                "can_apply": can_apply,
            },
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/cancel",
               methods=["POST"])
    def tuning_cancel_experiment(experiment_id: str):
        """POST /api/tuning/experiments/{id}/cancel

        取消实验，保留已确认证据，释放租约。
        """
        try:
            store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": "实验不存在",
            }), 404
        from webui.tuning import TuningController
        controller = TuningController(store)
        try:
            controller.cancel_experiment(experiment_id)
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "invalid_state",
                "error": str(exc),
            }), 409
        return jsonify({
            "ok": True,
            "experiment_id": experiment_id,
            "status": "cancelled",
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/confirm-input",
               methods=["POST"])
    def tuning_confirm_input(experiment_id: str):
        """POST /api/tuning/experiments/{id}/confirm-input

        冻结输入版本，推进实验到 preflight。
        """
        try:
            exp = store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": "实验不存在",
            }), 404
        if exp["status"] != "draft":
            return jsonify({
                "ok": False, "error_code": "invalid_state",
                "error": f"实验状态不是 draft: {exp['status']}",
            }), 409
        from webui.tuning import TuningController
        controller = TuningController(store)
        try:
            result = controller.confirm_input(experiment_id)
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "input_incomplete",
                "error": str(exc),
            }), 409
        return jsonify({
            "ok": True,
            "experiment_id": experiment_id,
            **result,
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/resume",
               methods=["POST"])
    def tuning_resume_experiment(experiment_id: str):
        """POST /api/tuning/experiments/{id}/resume

        从 blocked 状态恢复到 awaiting_instruction。
        只允许 blocked 状态恢复，不自动选择新候选。
        """
        try:
            exp = store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": "实验不存在",
            }), 404
        if exp["status"] != "blocked":
            return jsonify({
                "ok": False, "error_code": "invalid_state",
                "error": f"只有 blocked 状态才能恢复，当前: {exp['status']}",
            }), 409
        try:
            store.update_tuning_experiment_status(
                experiment_id, status="awaiting_instruction",
            )
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "invalid_state",
                "error": str(exc),
            }), 409
        return jsonify({
            "ok": True,
            "experiment_id": experiment_id,
            "status": "awaiting_instruction",
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/result",
               methods=["GET"])
    def tuning_get_experiment_result(experiment_id: str):
        """Return safe candidate/evidence summary with an objective apply gate."""
        from webui.tuning import TuningController
        controller = TuningController(store)
        try:
            result = controller.get_experiment_result(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": "实验不存在",
            }), 404
        return jsonify({"ok": True, **result}), 200

    @app.route("/api/tuning/experiments/<experiment_id>/apply",
               methods=["POST"])
    def tuning_apply_experiment_result(experiment_id: str):
        """Apply one exact complete nine-slot candidate after explicit request."""
        body = request.get_json(silent=True) or {}
        digest = body.get("candidate_mode_version_digest")
        if not digest:
            return jsonify({
                "ok": False, "error_code": "invalid_request",
                "error": "缺少 candidate_mode_version_digest",
            }), 400
        from webui.tuning import TuningController
        controller = TuningController(store)
        try:
            version = controller.apply_candidate_mode_version(
                experiment_id=experiment_id, version_digest=str(digest),
            )
        except KeyError:
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": "实验不存在",
            }), 404
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "result_not_applicable",
                "error": str(exc),
            }), 409
        return jsonify({
            "ok": True, "mode_version_id": version["id"],
            "version_digest": version["version_digest"],
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/manifests",
               methods=["POST"])
    def tuning_issue_manifest(experiment_id: str):
        """POST /api/tuning/experiments/{id}/manifests

        控制者签发一份不可变任务单。
        """
        from webui.tuning import TuningController

        body = request.get_json(silent=True) or {}
        # 确保路径参数与 body 一致
        body["experiment_id"] = experiment_id
        controller = TuningController(store)
        # 校验实验存在且处于 awaiting_instruction
        try:
            exp = store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": "实验不存在",
            }), 404
        if exp["status"] != "awaiting_instruction":
            return jsonify({
                "ok": False, "error_code": "invalid_experiment_status",
                "error": f"实验状态不是 awaiting_instruction: {exp['status']}",
            }), 409
        try:
            result = controller.issue_manifest(body)
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "manifest_validation_failed",
                "error": str(exc),
            }), 422
        return jsonify({"ok": True, **result}), 201

    @app.route("/api/tuning/manifests/<manifest_id>", methods=["GET"])
    def tuning_get_manifest(manifest_id: str):
        """GET /api/tuning/manifests/{id}

        返回安全结构化 manifest，不含凭据。
        """
        try:
            record = store.get_task_manifest(manifest_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "manifest_not_found",
                "error": "任务单不存在",
            }), 404
        # 不返回凭据/敏感字段
        safe_manifest = dict(record["manifest"])
        # 移除可能的敏感字段
        for sensitive in ("api_key", "credentials", "password", "token"):
            safe_manifest.pop(sensitive, None)
        return jsonify({
            "ok": True,
            "manifest_id": record["id"],
            "manifest": safe_manifest,
            "manifest_digest": record["manifest_digest"],
            "rendered_task_path": record["rendered_task_path"],
            "status": record["status"],
        }), 200

    @app.route("/api/tuning/manifests/<manifest_id>/execute",
               methods=["POST"])
    def tuning_execute_manifest(manifest_id: str):
        """POST /api/tuning/manifests/{id}/execute

        启动 manifest 对应的轮次。重新校验摘要、产物、租约。
        """
        from webui.tuning import TuningController

        try:
            record = store.get_task_manifest(manifest_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "manifest_not_found",
                "error": "任务单不存在",
            }), 404
        controller = TuningController(store)
        try:
            started = controller.execute_manifest(manifest_id)
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "round_state_conflict",
                "error": str(exc),
            }), 409
        round_id = started["round_id"]
        child_task_id = record["manifest"].get("task_id") or round_id
        if app.config.get("START_TASKS"):
            try:
                _pipeline_executor.submit(_run_tuning_manifest_child, manifest_id)
            except RuntimeError as exc:
                return jsonify({
                    "ok": False, "error_code": "submit_failed", "error": str(exc),
                }), 503
        return jsonify({
            "ok": True,
            "child_task_id": child_task_id,
            "round_id": round_id,
            "status": "running",
            "status_url": f"/api/tuning/rounds/{round_id}",
        }), 202

    @app.route("/api/tuning/rounds/<round_id>", methods=["GET"])
    def tuning_get_round(round_id: str):
        """GET /api/tuning/rounds/{id}

        返回轮次的程序状态。
        """
        try:
            round_rec = store.get_tuning_round(round_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "round_not_found",
                "error": "轮次不存在",
            }), 404
        return jsonify({
            "ok": True,
            "round": {
                "id": round_rec["id"],
                "status": round_rec["status"],
                "experiment_id": round_rec["experiment_id"],
                "candidate_id": round_rec["candidate_id"],
                "round_kind": round_rec["round_kind"],
                "repetition_index": round_rec["repetition_index"],
                "manifest_id": round_rec.get("manifest_id"),
                "started_at": round_rec.get("started_at"),
                "finished_at": round_rec.get("finished_at"),
                "confirmed_at": round_rec.get("confirmed_at"),
                "failure_code": round_rec.get("failure_code"),
            },
        }), 200

    @app.route("/api/tuning/manifests/<manifest_id>/report",
               methods=["POST"])
    def tuning_submit_report(manifest_id: str):
        """POST /api/tuning/manifests/{id}/report

        接受一份执行者报告，校验并更新轮次状态。
        """
        from webui.tuning import TuningController

        body = request.get_json(silent=True) or {}
        try:
            record = store.get_task_manifest(manifest_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "manifest_not_found",
                "error": "任务单不存在",
            }), 404
        try:
            saved = TuningController(store).accept_report(
                manifest_id=manifest_id, report=body)
        except ValueError as exc:
            return jsonify({
                "ok": False, "error_code": "report_validation_failed",
                "error": str(exc),
                "validation_status": "rejected",
            }), 422
        return jsonify({
            "ok": True,
            "report_id": saved["report_id"],
            "validation_status": "accepted",
            "round_status": saved["round_status"],
            "experiment_status": saved["experiment_status"],
        }), 201

    @app.route("/api/tuning/rounds/<round_id>/evidence", methods=["GET"])
    def tuning_get_evidence(round_id: str):
        """GET /api/tuning/rounds/{id}/evidence

        返回安全聚合证据，不含凭据/原始简历/原始模型响应。
        """
        from webui.tuning import TuningController

        try:
            round_rec = store.get_tuning_round(round_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "round_not_found",
                "error": "轮次不存在",
            }), 404
        controller = TuningController(store)
        try:
            summary = controller.aggregate_measurements(round_id)
        except (KeyError, ValueError):
            summary = {}
        # 不返回敏感字段
        safe_summary = {
            "total_duration_ms": summary.get("total_duration_ms", 0),
            "stage_durations_ms": summary.get("stage_durations_ms", {}),
            "wait_duration_ms": summary.get("wait_duration_ms", 0),
            "retry_duration_ms": summary.get("retry_duration_ms", 0),
            "attempt_count": summary.get("attempt_count", 0),
            "retry_count": summary.get("retry_count", 0),
            "input_count": summary.get("input_count", 0),
            "terminal_count": summary.get("terminal_count", 0),
            "success_count": summary.get("success_count", 0),
            "failed_count": summary.get("failed_count", 0),
            "missing_count": summary.get("missing_count", 0),
            "duplicate_count": summary.get("duplicate_count", 0),
            "error_counts": summary.get("error_counts", {}),
            "error_correlation_id": summary.get("error_correlation_id"),
        }
        return jsonify({
            "ok": True,
            "evidence": safe_summary,
            "round_id": round_id,
        }), 200

    @app.route("/api/tuning/experiments/<experiment_id>/decisions",
               methods=["POST"])
    def tuning_post_decision(experiment_id: str):
        """POST /api/tuning/experiments/{id}/decisions

        控制者对候选做出 promote/reject/refine 决策。
        执行者 AI 不能调用此路由。
        """
        body = request.get_json(silent=True) or {}
        candidate_id = body.get("candidate_id")
        decision = body.get("decision")
        if not candidate_id or not decision:
            return jsonify({
                "ok": False, "error_code": "invalid_request",
                "error": "缺少 candidate_id 或 decision",
            }), 400
        if decision not in ("promote", "reject", "refine"):
            return jsonify({
                "ok": False, "error_code": "invalid_decision",
                "error": f"未知决策类型: {decision}",
            }), 422
        # 校验实验存在
        try:
            exp = store.get_tuning_experiment(experiment_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "experiment_not_found",
                "error": "实验不存在",
            }), 404
        # 校验候选存在且属于该实验
        try:
            candidate = store.get_tuning_candidate(candidate_id)
        except (KeyError, ValueError):
            return jsonify({
                "ok": False, "error_code": "candidate_not_found",
                "error": "候选不存在",
            }), 404
        if candidate["experiment_id"] != experiment_id:
            return jsonify({
                "ok": False, "error_code": "candidate_mismatch",
                "error": "候选不属于该实验",
            }), 422
        # 校验 evidence ownership
        reason_evidence = body.get("reason_evidence", [])
        if not isinstance(reason_evidence, list):
            reason_evidence = []
        now = datetime.now().isoformat()
        # 应用决策
        if decision == "promote":
            with store._connection() as conn:
                conn.execute(
                    "UPDATE tuning_candidates "
                    "SET status = 'promoted', promotion_reason = ?, "
                    "    updated_at = ? WHERE id = ?",
                    (json.dumps(reason_evidence, ensure_ascii=False),
                     now, candidate_id),
                )
        elif decision == "reject":
            code = body.get("code", "rejected")
            with store._connection() as conn:
                conn.execute(
                    "UPDATE tuning_candidates "
                    "SET status = 'rejected', rejection_code = ?, "
                    "    updated_at = ? WHERE id = ?",
                    (code, now, candidate_id),
                )
        elif decision == "refine":
            next_config = body.get("next_config")
            if not next_config:
                return jsonify({
                    "ok": False, "error_code": "missing_next_config",
                    "error": "refine 决策必须提供 next_config",
                }), 422
            # 创建新的候选
            new_candidate = store.save_tuning_candidate(
                experiment_id=experiment_id,
                stage=candidate["stage"],
                strategy_step=candidate["strategy_step"],
                config=next_config,
                parent_candidate_id=candidate_id,
            )
            return jsonify({
                "ok": True,
                "decision": "refine",
                "new_candidate_id": new_candidate["id"],
            }), 200
        return jsonify({
            "ok": True,
            "decision": decision,
            "candidate_id": candidate_id,
        }), 200

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, threaded=True)
