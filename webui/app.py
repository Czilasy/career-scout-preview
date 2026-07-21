#!/usr/bin/env python3
"""Local Flask API for persistent BOSS scraping and explainable job ranking."""

from __future__ import annotations

import csv
import io
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import uuid
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, jsonify, request, send_from_directory

from scripts import boss_cdp_raw as boss
from scripts import job_summary
from webui.core import build_filter_options, match_jobs, normalize_profile, validate_search_params
from webui.store import TaskStore
from webui.workbench import (
    allocate_detail_budget,
    MAX_DETAIL_BUDGET,
    normalize_job_link,
    select_keywords,
    project_card,
    aggregate_feedback_state,
    merge_profile_fields,
)
from webui.discovery import (
    DiscoveryError as _DiscoveryError,
    ERROR_CODE_MAP as _ERROR_CODE_MAP,
    analyze_resume as _discovery_analyze_resume,
    confirm_directions as _discovery_confirm_directions,
    compile_search_plan as _discovery_compile_plan,
    compute_discovery_input_hash as _compute_discovery_input_hash,
    normalize_portfolio_assessment as _normalize_discovery_assessment,
    project_recommendations as _project_recommendations,
)
from webui.discovery_runner import DiscoveryTaskRuntime as _DiscoveryTaskRuntime
from webui.source import BossCdpSource as _BossCdpSource
from webui.process_executor import ArtifactSpec, ScraperExecutor
from webui import resume as resume_service
from webui import ai as ai_service
from webui.screening import (
    build_screening_filter_options,
    execute_first_layer,
    freeze_filters,
    is_valid_filters,
    partition_job,
    partition_jobs,
    verify_hard_rules_detailed,
    exclude_trash_jobs,
)


SCRAPER = PROJECT_ROOT / "scripts" / "boss_cdp_raw.py"
DEFAULT_STATE_DIR = Path(os.path.expanduser("~/.career-scout/webui"))
# 最新一轮流水线结果的持久化文件（刷新页面后据此恢复展示，新一轮运行覆盖它）
LATEST_PIPELINE_RESULT_PATH = DEFAULT_STATE_DIR / "latest_pipeline_result.json"
# 最新一轮"只抓不筛"的原始抓取结果，供 AI 筛选步骤读取（与最终筛选结果分开）
LATEST_SCRAPE_RESULT_PATH = DEFAULT_STATE_DIR / "latest_scrape_result.json"


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
            except (KeyError, ValueError):
                pass
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
        self.store.cleanup_expired_jobs(days=30)

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


class ScreeningRunner:
    """Own background screening workers, cancellation events and scraper handles."""

    def __init__(self, store, *, start_tasks=True, timeout_seconds=300):
        self.store = store
        self.start_tasks = bool(start_tasks)
        self.timeout_seconds = max(1, int(timeout_seconds))
        self._lock = threading.Lock()
        self._cancel_events = {}
        self._processes = {}
        self.executor = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="boss-screening")
            if self.start_tasks else None
        )

    def submit(self, run_id, execute):
        event = threading.Event()
        with self._lock:
            self._cancel_events[run_id] = event
        if self.executor:
            self.executor.submit(self._run, run_id, execute)
        else:
            self._run(run_id, execute)

    def _run(self, run_id, execute):
        try:
            execute(
                run_id,
                should_cancel=lambda: self.is_cancelled(run_id),
                on_process=lambda process: self.track_process(run_id, process),
                timeout_seconds=self.timeout_seconds,
            )
        except Exception:
            self.store.update_screening_run_status(
                run_id, "failed", error_code="execution_failed",
                expected_statuses={"queued", "running"},
            )
        finally:
            with self._lock:
                self._processes.pop(run_id, None)
                self._cancel_events.pop(run_id, None)

    def is_cancelled(self, run_id):
        with self._lock:
            event = self._cancel_events.get(run_id)
        if event and event.is_set():
            return True
        try:
            return self.store.get_screening_run(run_id)["status"] == "interrupted"
        except KeyError:
            return True

    def track_process(self, run_id, process):
        with self._lock:
            if process is None:
                self._processes.pop(run_id, None)
            else:
                self._processes[run_id] = process

    def cancel(self, run_id):
        run = self.store.get_screening_run(run_id)
        if run["status"] not in {"queued", "running"}:
            raise ValueError("只能取消等待中或运行中的筛选")
        with self._lock:
            event = self._cancel_events.get(run_id)
            process = self._processes.get(run_id)
            if event:
                event.set()
        if process is not None and process.poll() is None:
            process.terminate()
        return self.store.update_screening_run_status(
            run_id, "interrupted", error_code="cancelled",
            expected_statuses={"queued", "running"},
        )


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        RESULT_DIR=str(boss.DEFAULT_RESULT_DIR),
        DB_PATH=str(DEFAULT_STATE_DIR / "webui.db"),
        PYTHON_EXECUTABLE=os.environ.get("BOSS_PYTHON", sys.executable),
        START_TASKS=True,
        API_TOKEN=secrets.token_urlsafe(24),
        SESSION_COOKIE_NAME="boss_local_session",
        TRUSTED_HOSTS=["127.0.0.1", "localhost", "::1"],
        RESUME_DIR=str(DEFAULT_STATE_DIR / "resumes"),
        SCREENING_TIMEOUT_SECONDS=300,
    )
    if config:
        app.config.update(config)
    if app.config.get("TESTING") and "START_TASKS" not in (config or {}):
        app.config["START_TASKS"] = False

    store = TaskStore(app.config["DB_PATH"])
    store.cleanup_expired_jobs(days=30)
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
    screening_runner = ScreeningRunner(
        store,
        start_tasks=app.config["START_TASKS"],
        timeout_seconds=app.config["SCREENING_TIMEOUT_SECONDS"],
    )

    # T101: 应用持有的唯一 DiscoveryTaskRuntime。
    # provider/source factory 让 runtime 在每次 submit_run 时获取最新 AI 设置
    # 和 CDP source 状态，而不是被钉死在 create_app 构造时刻的实例上。
    def _make_discovery_source():
        try:
            return _BossCdpSource(
                python_executable=app.config["PYTHON_EXECUTABLE"],
                artifact_root=app.config["RESULT_DIR"],
            )
        except Exception:
            return None

    discovery_runtime = _DiscoveryTaskRuntime(
        store,
        source_factory=_make_discovery_source,
        ai_provider_factory=lambda: _build_ai_provider(store),
        result_dir=app.config["RESULT_DIR"],
        max_workers=1,
    )
    Path(app.config["RESUME_DIR"]).mkdir(parents=True, exist_ok=True)
    app.config["TASK_STORE"] = store
    app.config["TASK_RUNNER"] = runner
    app.config["WORKBENCH_RUNNER"] = workbench_runner
    app.config["SCREENING_RUNNER"] = screening_runner
    app.config["DISCOVERY_RUNTIME"] = discovery_runtime

    @app.before_request
    def protect_local_api():
        trusted_hosts = set(app.config["TRUSTED_HOSTS"])
        if _request_hostname(request.host) not in trusted_hosts:
            return jsonify({"error": "拒绝不受信任的 Host"}), 403
        # T010: resume reads and AI settings reads also require the session
        # token — they expose private user data even though they are GET.
        # T009: screening GET endpoints (runs/interested/trash) expose job
        # data and require the token; filter-options is a public enum and
        # stays open.
        path = request.path
        screening_sensitive_get = (
            path.startswith("/api/screening/runs")
            or path.startswith("/api/screening/interested")
            or path.startswith("/api/screening/trash")
            or path.startswith("/api/screening/cleanup")
        )
        sensitive_get = (
            path.startswith("/api/resumes")
            or path.startswith("/api/ai-settings")
            or path.startswith("/api/profiles/") and "/resumes" in path
            or screening_sensitive_get
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
        # Read as text so the generated HTML uses stable LF line endings on
        # every platform; DOM contract checks and embedded script snippets
        # should not depend on the host filesystem newline convention.
        html = (HERE / "index.html").read_text(encoding="utf-8")
        resp = app.response_class(html, mimetype="text/html")
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp

    @app.route("/screening-prototype")
    def screening_prototype():
        """Serve the isolated visual review prototype without production APIs."""
        return send_from_directory(HERE, "screening-prototype.html")

    @app.route("/api/options")
    def options():
        cities = [{"label": name, "value": name} for name in boss.CITY_MAP]
        return jsonify({"filters": build_filter_options(), "cities": cities})

    @app.route("/api/session")
    def session():
        payload = (
            {"token": app.config["API_TOKEN"]}
            if app.config.get("TESTING") else {"status": "ok"}
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
            limit = min(100, max(1, request.args.get("limit", 30, type=int) or 30))
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
        settings = store.get_ai_settings()
        endpoint_url = settings.get("endpoint_url") or ""
        if not endpoint_url:
            raise ValueError("请先保存 AI 服务 URL")
        cred_ref = store.get_credential_ref()
        api_key = ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
        capability = ai_service.test_connection(endpoint_url, api_key, model=settings.get("model", ""))
        new_status = "ready" if capability["ok"] else "failed"
        error_code = capability["warning_codes"][0] if not capability["ok"] and capability["warning_codes"] else None
        store.update_ai_status(new_status, last_error_code=error_code)
        return jsonify(capability)

    @app.route("/api/ai-settings/models", methods=["GET"])
    def ai_settings_models():
        """拉取可用模型列表。前端持 key 不安全，由后端代理 GET /models。"""
        settings = store.get_ai_settings()
        endpoint_url = settings.get("endpoint_url") or ""
        if not endpoint_url:
            raise ValueError("请先保存 AI 服务 URL")
        cred_ref = store.get_credential_ref()
        api_key = ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
        if not api_key:
            raise ValueError("API Key 未配置")
        try:
            models = ai_service.list_models(endpoint_url, api_key)
        except ai_service.AISecurityError as exc:
            return jsonify({"ok": False, "error_code": exc.error_code, "models": []}), 200
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
        discovery_flow = (
            request.form.get("flow") == "discovery"
            or request.args.get("flow") == "discovery"
        )
        # AI parse occurs only after the user has seen and accepted the
        # pre-upload notice in the UI.  The raw text never leaves this scope.
        ai_suggestion = {}
        settings = store.get_ai_settings()
        consent = request.form.get("ai_consent") == "true"
        if not discovery_flow and settings.get("is_configured") and consent:
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
        return (response, 201) if discovery_flow else response

    @app.route("/api/profiles/<profile_id>/resumes")
    def profile_resume_list(profile_id):
        store.get_profile(profile_id)
        return jsonify({"resumes": store.list_resumes(profile_id)})

    # == US1: screening resume upload & AI suggest ========================

    @app.route("/api/screening/filter-options")
    def screening_filter_options():
        """Return 7-class filter option enums. Public endpoint, no token."""
        return jsonify({"options": build_screening_filter_options()})

    @app.route("/api/screening/resume", methods=["POST"])
    def screening_upload_resume():
        """Upload a resume for screening. Reuses 001 resume storage.

        Returns resume_id and a privacy notice. Never returns the resume
        text. AI parsing is deferred to the /suggest endpoint.
        """
        profile_id = request.form.get("profile_id")
        if not profile_id:
            created = store.create_profile("筛选简历")
            profile_id = created["id"]
        if "file" not in request.files:
            raise ValueError("请上传简历文件")
        upload = request.files["file"]
        file_bytes = upload.read()
        filename = upload.filename or "resume.txt"
        record = resume_service.save_resume(
            profile_id, file_bytes, filename,
            resume_service.validate_format(filename),
            app.config["RESUME_DIR"], store,
        )
        return jsonify({
            "resume_id": record["id"],
            "profile_id": profile_id,
            "privacy_notice": "简历文本仅用于 AI 读取筛选项建议，不会写入日志或接口响应。",
        })

    @app.route("/api/screening/resume/suggest", methods=["POST"])
    def screening_resume_suggest():
        """Read a resume and call AI for filter suggestions.

        Returns {status: "ok", suggestions: {...}} on success, or
        {status: "ai_unavailable"} when AI is not configured or fails.
        Never returns the resume text.
        """
        raw = request.get_json(silent=True) or {}
        resume_id = raw.get("resume_id")
        if not resume_id:
            raise ValueError("resume_id 不能为空")
        resume = store.get_resume(resume_id)
        extracted_text = resume.get("extracted_text") or ""
        if not extracted_text:
            raise ValueError("简历文本为空")
        settings = store.get_ai_settings()
        cred_ref = store.get_credential_ref()
        api_key = ai_service.retrieve_api_key(cred_ref) if cred_ref else ""
        if not ai_service.is_ai_available(settings, cred_ref, api_key):
            return jsonify({"status": "ai_unavailable"})
        try:
            profile = store.get_profile(resume["profile_id"])
            suggestion_result = ai_service.suggest_screening_filters_cautious(
                extracted_text, settings["endpoint_url"], api_key,
                confirmed_fields=profile.get("confirmed_fields") or {},
                model=settings.get("model", ""),
            )
        except ai_service.AISecurityError:
            return jsonify({"status": "ai_unavailable"})
        store.save_resume_suggestions(resume_id, suggestion_result)
        return jsonify({
            "status": "ok",
            "suggestions": suggestion_result["values"],
            "suggestion_meta": suggestion_result["meta"],
        })

    # == US2: screening execution run ====================================

    def _screening_run_payload(run):
        return {
            "run_id": run["id"],
            "status": run["status"],
            "source_count": run["source_count"],
            "match_count": run["match_count"],
            "mismatch_count": run["mismatch_count"],
            "pending_count": run.get("pending_count", 0),
            "parse_failure_count": run.get("parse_failure_count", 0),
            "parse_failures": run.get("parse_failures", {}),
        }

    def _execute_screening_pipeline(run_id, *, should_cancel, on_process,
                                    timeout_seconds, resume_saved=False):
        """Run fetch + per-job verification with cancellation-safe writes."""
        run = store.get_screening_run(run_id)
        if should_cancel():
            return run
        frozen = run["frozen_filters"]
        execution = run.get("execution", {})
        keyword = execution.get("keyword", "")
        execution_limits = {
            key: execution[key] for key in ("pages", "max_details")
            if execution.get(key) is not None
        }
        resume_text = ""
        if run.get("resume_id"):
            resume_text = store.get_resume(run["resume_id"]).get("extracted_text") or ""
        output_path = Path(app.config["RESULT_DIR"]) / f"screening_{run_id}.json"
        detail_output_path = Path(app.config["RESULT_DIR"]) / f"screening_{run_id}_details.json"
        guarded = bool(app.config["START_TASKS"])
        if guarded or resume_saved:
            store.update_screening_run_status(
                run_id, "running", expected_statuses={"queued"},
            )
            if should_cancel():
                return store.get_screening_run(run_id)

        search_result = None
        if resume_saved:
            payload = _read_json(output_path, {})
            saved_jobs = payload.get("jobs") if isinstance(payload, dict) else None
            try:
                last_completed_page = int(payload.get("last_completed_page", 0))
            except (TypeError, ValueError):
                last_completed_page = 0
            target_pages = int(execution.get("pages") or 0)
            can_continue_source = (
                last_completed_page >= 1
                and target_pages > last_completed_page
                and payload.get("keyword") == keyword
            )
            if can_continue_source:
                execution_limits["start_page"] = last_completed_page + 1
            elif isinstance(saved_jobs, list) and saved_jobs:
                search_result = {
                    "jobs": saved_jobs,
                    "source_count": len(saved_jobs),
                    "status": (
                        "running" if target_pages and last_completed_page >= target_pages
                        else "partial"
                    ),
                    "error_code": "resumed_from_saved_artifact",
                }
        if search_result is None:
            try:
                search_result = execute_first_layer(
                    frozen, keyword,
                    output_path=str(output_path),
                    detail_output_path=str(detail_output_path),
                    python_executable=app.config["PYTHON_EXECUTABLE"],
                    **execution_limits,
                    store=store,
                    run_id=run_id,
                    should_cancel=should_cancel if guarded else None,
                    timeout_seconds=timeout_seconds if guarded else None,
                    on_process=on_process if guarded else None,
                    manage_status=not (guarded or resume_saved),
                )
            except Exception:
                if not should_cancel():
                    store.update_screening_run_status(
                        run_id, "failed", error_code="execution_failed",
                        **({"expected_statuses": {"queued", "running"}} if guarded else {}),
                    )
                return store.get_screening_run(run_id)

        if should_cancel() or search_result.get("status") == "interrupted":
            return store.get_screening_run(run_id)

        jobs = search_result.get("jobs", []) if isinstance(search_result, dict) else []
        ai_settings = store.get_ai_settings()
        ai_cred_ref = store.get_credential_ref()
        ai_api_key = ai_service.retrieve_api_key(ai_cred_ref) if ai_cred_ref else ""
        ai_available = ai_service.is_ai_available(
            ai_settings, ai_cred_ref, ai_api_key,
        ) and bool(resume_text)
        semantic_options = None
        if ai_available:
            semantic_options = {
                "ai_available": True,
                "endpoint_url": ai_settings["endpoint_url"],
                "api_key": ai_api_key,
                "model": ai_settings.get("model", ""),
                "require_input": True,
            }

        existing_results = store.get_screening_results(run_id)
        existing_pending = store.list_pending(run_id)
        completed_job_ids = {
            item["job_id"] for item in [*existing_results, *existing_pending]
        }
        current = store.get_screening_run(run_id)
        parse_distribution = dict(current.get("parse_failures", {}))
        parse_failure_count = int(current.get("parse_failure_count", 0))
        persisted_counts = store.count_screening_results(run_id)
        counts = {
            "match": persisted_counts["match"],
            "mismatch": persisted_counts["mismatch"],
            "pending": len(existing_pending),
        }
        active_statuses = {"running"}
        for index, job in enumerate(jobs, start=1):
            if job.get("job_id", "") in completed_job_ids:
                continue
            if should_cancel():
                break
            partition = partition_jobs(
                [job], frozen, resume_text,
                ai_enabled=ai_available,
                semantic_options=semantic_options,
            )
            if should_cancel():
                break
            job_id = job.get("job_id", "")
            write_guard = active_statuses if guarded else None
            written = None
            if partition["match"]:
                written = store.add_screening_result(
                    run_id, job_id, "match", expected_run_statuses=write_guard,
                )
                if written:
                    counts["match"] += 1
                    completed_job_ids.add(job_id)
            elif partition["mismatch"]:
                written = store.add_screening_result(
                    run_id, job_id, "mismatch", expected_run_statuses=write_guard,
                )
                if written:
                    counts["mismatch"] += 1
                    completed_job_ids.add(job_id)
            else:
                written = store.add_pending_result(
                    run_id, job_id,
                    partition.get("pending_failures", {}).get(job_id, "verification_error"),
                    expected_run_statuses=write_guard,
                )
                if written:
                    counts["pending"] += 1
                    completed_job_ids.add(job_id)
            if guarded and not written:
                break
            detail = verify_hard_rules_detailed(job, frozen)
            for field in detail["parse_failures"]:
                parse_distribution[field] = parse_distribution.get(field, 0) + 1
                parse_failure_count += 1
            store.update_screening_run_status(
                run_id, "running",
                source_count=len(jobs), match_count=counts["match"],
                mismatch_count=counts["mismatch"], pending_count=counts["pending"],
                processed_count=index, source_cursor=index,
                parse_failure_count=parse_failure_count,
                parse_failures=parse_distribution,
                **({"expected_statuses": active_statuses} if guarded else {}),
            )

        if should_cancel():
            return store.get_screening_run(run_id)
        pending_count = counts["pending"]
        final_status = (
            "partial" if pending_count or search_result.get("status") == "partial"
            else "succeeded"
        )
        return store.update_screening_run_status(
            run_id, final_status,
            source_count=len(jobs), match_count=counts["match"],
            mismatch_count=counts["mismatch"], pending_count=pending_count,
            processed_count=len(completed_job_ids), source_cursor=len(jobs),
            parse_failure_count=parse_failure_count,
            parse_failures=parse_distribution,
            **({"expected_statuses": active_statuses} if guarded else {}),
        )

    @app.route("/api/screening/runs", methods=["POST"])
    def screening_create_run():
        """Freeze inputs, persist the run, then queue it in normal runtime."""
        raw = request.get_json(silent=True) or {}
        filters = raw.get("filters") or {}
        keyword = raw.get("keyword") or ""
        if not isinstance(filters, dict):
            raise ValueError("filters 必须是对象")
        if not is_valid_filters(filters):
            raise ValueError("filters 含有不允许的字段")
        if not keyword:
            raise ValueError("keyword 不能为空")
        pages = _optional_positive_int(raw.get("pages"), "pages", maximum=boss.MAX_PAGES)
        max_details = _optional_positive_int(raw.get("max_details"), "max_details")
        if pages is not None and max_details is not None and max_details > pages * 30:
            raise ValueError(f"max_details 不能超过 {pages * 30}")
        execution = {"keyword": keyword}
        if pages is not None:
            execution["pages"] = pages
        if max_details is not None:
            execution["max_details"] = max_details

        frozen = freeze_filters(filters)
        profile_id = raw.get("profile_id")
        if profile_id:
            store.get_profile(profile_id)
            # Executing is the explicit confirmation point. Missing keys are
            # deliberate clears, so persist exactly the frozen selection.
            store.update_profile(profile_id, confirmed_fields=frozen)
        resume_id = raw.get("resume_id")
        if resume_id:
            store.get_resume(resume_id)
        run = store.create_screening_run(
            frozen, resume_id=resume_id, profile_id=profile_id,
            execution=execution,
        )
        run_id = run["id"]
        screening_runner.submit(run_id, _execute_screening_pipeline)
        if app.config["START_TASKS"]:
            return jsonify(_screening_run_payload(store.get_screening_run(run_id))), 202

        final = store.get_screening_run(run_id)
        payload = _screening_run_payload(final)
        if final["status"] == "failed":
            payload["error_code"] = "execution_failed"
            return jsonify(payload), 500
        return jsonify(payload), 201

    @app.route("/api/screening/runs/<run_id>", methods=["GET"])
    def screening_get_run(run_id):
        """返回运行状态、source_count、match_count、mismatch_count、error_code。"""
        try:
            run = store.get_screening_run(run_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": "运行不存在"}), 404
        return jsonify({
            "run_id": run["id"],
            "status": run["status"],
            "source_count": run["source_count"],
            "match_count": run["match_count"],
            "mismatch_count": run["mismatch_count"],
            "pending_count": run.get("pending_count", 0),
            "processed_count": run.get("processed_count", 0),
            "source_cursor": run.get("source_cursor", 0),
            "parse_failure_count": run.get("parse_failure_count", 0),
            "parse_failures": run.get("parse_failures", {}),
            "error_code": run.get("error_code"),
            "frozen_filters": run.get("frozen_filters", {}),
        })

    @app.route("/api/screening/runs/<run_id>/resume", methods=["POST"])
    def screening_resume_run(run_id):
        """Continue an interrupted run from its saved artifact when available."""
        store.requeue_screening_run(run_id)

        def execute_saved(resume_run_id, **kwargs):
            return _execute_screening_pipeline(
                resume_run_id, resume_saved=True, **kwargs,
            )

        screening_runner.submit(run_id, execute_saved)
        if app.config["START_TASKS"]:
            return jsonify(_screening_run_payload(store.get_screening_run(run_id))), 202
        final = store.get_screening_run(run_id)
        payload = _screening_run_payload(final)
        if final["status"] == "failed":
            payload["error_code"] = final.get("error_code") or "execution_failed"
            return jsonify(payload), 500
        return jsonify(payload), 200

    # == US3: match/mismatch zone query ==================================

    def _load_run_jobs(run_id):
        """从第一层搜索产物读取 jobs 列表（按抓回顺序）。产物不存在返回空。"""
        output_path = Path(app.config["RESULT_DIR"]) / f"screening_{run_id}.json"
        if not output_path.is_file():
            return []
        try:
            with output_path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return []
        return payload.get("jobs", []) if isinstance(payload, dict) else []

    def _format_screening_job_item(job, interest_state="none"):
        """格式化岗位为接口契约字段：job_id/title/company/salary/location/
        jd_excerpt/canonical_url/interest_state。不返回排除原因。
        interest_state 由 US4 的展示排除逻辑填充（interested/rejected/none）。
        """
        if not isinstance(job, dict):
            job = {}
        return {
            "job_id": job.get("job_id", ""),
            "title": job.get("title", ""),
            "company": job.get("boss_name", ""),
            "salary": job.get("salary", ""),
            "location": job.get("location", ""),
            "jd_excerpt": str(job.get("jd", ""))[:320],
            "canonical_url": job.get("job_link", ""),
            "interest_state": interest_state,
        }

    def _build_zone_canonical_urls(profile_id):
        """构建感兴趣区与垃圾桶区的 canonical_url 集合（FR-020, FR-022）。

        遍历持久区的 profile_jobs，经 normalize_job_link 规范化 jobs.canonical_url，
        返回 (interested_urls, rejected_urls)。链接不安全的岗位不纳入集合。
        """
        interested_urls = set()
        for pj in store.list_screening_interested(profile_id):
            try:
                job = store.get_job(pj["job_id"])
            except KeyError:
                continue
            url = normalize_job_link(job.get("canonical_url", ""))
            if url:
                interested_urls.add(url)
        rejected_urls = set()
        for pj in store.list_screening_rejected(profile_id):
            try:
                job = store.get_job(pj["job_id"])
            except KeyError:
                continue
            url = normalize_job_link(job.get("canonical_url", ""))
            if url:
                rejected_urls.add(url)
        return interested_urls, rejected_urls

    def _screening_job_interest_state(job, interested_urls, rejected_urls):
        """根据 screening job 的 job_link 规范化后判断 interest_state。"""
        if not isinstance(job, dict):
            return "none"
        url = normalize_job_link(job.get("job_link", ""))
        if url in rejected_urls:
            return "rejected"
        if url in interested_urls:
            return "interested"
        return "none"

    @app.route("/api/screening/runs/<run_id>/matches", methods=["GET"])
    def screening_matches(run_id):
        """返回本次执行符合区岗位列表，按抓回顺序排列。

        从 screening_results 查 verdict=match 的 job_id（按 created_at 升序，
        即抓回顺序），再从第一层搜索产物读 job 详情。不返回排除原因。
        传入 profile_id 时执行展示排除（FR-022）并填充 interest_state。
        """
        try:
            store.get_screening_run(run_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": "运行不存在"}), 404
        results = store.get_screening_results(run_id, verdict="match")
        jobs = _load_run_jobs(run_id)
        jobs_by_id = {j.get("job_id", ""): j for j in jobs if isinstance(j, dict)}
        match_jobs = [jobs_by_id.get(r["job_id"], {}) for r in results]

        profile_id = request.args.get("profile_id")
        states = {}
        if profile_id:
            try:
                store.get_profile(profile_id)
            except KeyError:
                profile_id = None
        if profile_id:
            interested_urls, rejected_urls = _build_zone_canonical_urls(profile_id)
            # 经 canonical_url 桥接 screening job_id 与持久垃圾桶记录
            rejected_screening_ids = {
                j.get("job_id", "") for j in match_jobs
                if _screening_job_interest_state(j, interested_urls, rejected_urls) == "rejected"
            }
            match_jobs = exclude_trash_jobs(match_jobs, rejected_screening_ids)
            states = {
                j.get("job_id", ""): _screening_job_interest_state(j, interested_urls, rejected_urls)
                for j in match_jobs
            }

        items = [_format_screening_job_item(j, states.get(j.get("job_id", ""), "none")) for j in match_jobs]
        return jsonify({"items": items, "count": len(items)})

    @app.route("/api/screening/runs/<run_id>/mismatches", methods=["GET"])
    def screening_mismatches(run_id):
        """返回本次执行不符合区岗位列表，混在一起，不返回排除原因、
        不区分硬规则或 AI 排除。字段同符合区。
        传入 profile_id 时执行展示排除（FR-022）并填充 interest_state。
        """
        try:
            store.get_screening_run(run_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": "运行不存在"}), 404
        results = store.get_screening_results(run_id, verdict="mismatch")
        jobs = _load_run_jobs(run_id)
        jobs_by_id = {j.get("job_id", ""): j for j in jobs if isinstance(j, dict)}
        mismatch_jobs = [jobs_by_id.get(r["job_id"], {}) for r in results]

        profile_id = request.args.get("profile_id")
        states = {}
        if profile_id:
            try:
                store.get_profile(profile_id)
            except KeyError:
                profile_id = None
        if profile_id:
            interested_urls, rejected_urls = _build_zone_canonical_urls(profile_id)
            rejected_screening_ids = {
                j.get("job_id", "") for j in mismatch_jobs
                if _screening_job_interest_state(j, interested_urls, rejected_urls) == "rejected"
            }
            mismatch_jobs = exclude_trash_jobs(mismatch_jobs, rejected_screening_ids)
            states = {
                j.get("job_id", ""): _screening_job_interest_state(j, interested_urls, rejected_urls)
                for j in mismatch_jobs
            }

        items = [_format_screening_job_item(j, states.get(j.get("job_id", ""), "none")) for j in mismatch_jobs]
        return jsonify({"items": items, "count": len(items)})

    # == 003: pending verification, retry and manual routing =============

    def _sync_screening_run_counts(run_id):
        counts = store.count_screening_results(run_id)
        pending_count = len(store.list_pending(run_id))
        run = store.get_screening_run(run_id)
        if run["status"] in {"queued", "running", "partial", "succeeded"}:
            status = "partial" if pending_count else "succeeded"
        else:
            status = run["status"]
        return store.update_screening_run_status(
            run_id, status,
            match_count=counts["match"], mismatch_count=counts["mismatch"],
            pending_count=pending_count,
            processed_count=counts["match"] + counts["mismatch"] + pending_count,
        )

    @app.route("/api/screening/runs/<run_id>/pending", methods=["GET"])
    def screening_pending(run_id):
        """Return safe pending-verification items for one screening run."""
        try:
            store.get_screening_run(run_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": "运行不存在"}), 404
        jobs = {j.get("job_id", ""): j for j in _load_run_jobs(run_id) if isinstance(j, dict)}
        items = []
        for pending in store.list_pending(run_id):
            job = _format_screening_job_item(jobs.get(pending["job_id"], {}))
            job.update({
                "failure_stage": pending["failure_stage"],
                "retryable": pending["retryable"],
                "attempts": pending["attempts"],
                "last_failed_at": pending["last_failed_at"],
            })
            items.append(job)
        return jsonify({"items": items, "count": len(items)})

    def _retry_one_pending(run_id, pending):
        job_id = pending["job_id"]
        job = _find_screening_job(job_id, run_id)
        if not job:
            store.add_pending_result(run_id, job_id, "verification_error", retryable=False)
            return "pending"
        run = store.get_screening_run(run_id)
        resume_text = ""
        if run.get("resume_id"):
            try:
                resume_text = store.get_resume(run["resume_id"]).get("extracted_text") or ""
            except KeyError:
                resume_text = ""
        settings = store.get_ai_settings()
        credential_ref = store.get_credential_ref()
        api_key = ai_service.retrieve_api_key(credential_ref) if credential_ref else ""
        ai_enabled = ai_service.is_ai_available(settings, credential_ref, api_key) and bool(resume_text)
        options = None
        if ai_enabled:
            options = {"ai_available": True, "endpoint_url": settings["endpoint_url"],
                       "api_key": api_key, "model": settings.get("model", ""),
                       "require_input": True}
        verdict = partition_job(
            job, run["frozen_filters"], resume_text,
            ai_enabled=ai_enabled, semantic_options=options,
        )
        if verdict in {"match", "mismatch"}:
            store.manual_route_pending(run_id, job_id, str(verdict))
        else:
            store.add_pending_result(
                run_id, job_id,
                getattr(verdict, "failure_stage", None) or "verification_error",
                retryable=True,
            )
        return str(verdict)

    @app.route("/api/screening/runs/<run_id>/pending/<job_id>/retry", methods=["POST"])
    def screening_retry_pending(run_id, job_id):
        """Retry one retryable pending item and persist its new state."""
        pending = next((p for p in store.list_pending(run_id) if p["job_id"] == job_id), None)
        if not pending:
            return jsonify({"error_code": "not_found", "user_message": "待核验岗位不存在"}), 404
        if not pending["retryable"]:
            return jsonify({"error_code": "not_retryable", "user_message": "该岗位不可自动重试"}), 409
        verdict = _retry_one_pending(run_id, pending)
        run = _sync_screening_run_counts(run_id)
        return jsonify({"job_id": job_id, "verdict": verdict, "status": run["status"]})

    @app.route("/api/screening/runs/<run_id>/pending/retry-all", methods=["POST"])
    def screening_retry_all_pending(run_id):
        """Retry every retryable pending item without blocking on skipped rows."""
        pending = store.list_pending(run_id)
        retried = 0
        skipped = 0
        for item in pending:
            if not item["retryable"]:
                skipped += 1
                continue
            _retry_one_pending(run_id, item)
            retried += 1
        run = _sync_screening_run_counts(run_id)
        return jsonify({"retried": retried, "skipped": skipped, "status": run["status"]})

    @app.route("/api/screening/runs/<run_id>/pending/<job_id>/route", methods=["POST"])
    def screening_route_pending(run_id, job_id):
        """Apply an explicit human match/mismatch decision to a pending item."""
        target = (request.get_json(silent=True) or {}).get("target")
        if target not in {"match", "mismatch"}:
            raise ValueError("target 必须为 match 或 mismatch")
        result = store.manual_route_pending(run_id, job_id, target)
        run = _sync_screening_run_counts(run_id)
        return jsonify({"result": result, "status": run["status"]})

    @app.route("/api/screening/runs/<run_id>/cancel", methods=["POST"])
    def screening_cancel_run(run_id):
        """Interrupt a running screening run without deleting saved results."""
        return jsonify(screening_runner.cancel(run_id))

    # == US4: interest / reject / persistent zones =======================

    def _find_screening_job(job_id, run_id):
        """按 job_id 在指定 run 的第一层搜索产物中查找岗位。未找到返回 None。"""
        if not run_id:
            return None
        for job in _load_run_jobs(run_id):
            if isinstance(job, dict) and job.get("job_id", "") == job_id:
                return job
        return None

    def _save_screening_job_to_store(job):
        """将 screening 岗位保存到 jobs 表，返回 jobs 记录或 None（链接不安全）。"""
        canonical_url = normalize_job_link(job.get("job_link", ""))
        if not canonical_url:
            return None
        return store.save_job(
            canonical_url, canonical_url,
            job.get("title", ""), job.get("boss_name", ""),
            job.get("salary", ""), job.get("location", ""), "",
        )

    @app.route("/api/screening/jobs/<job_id>/interest", methods=["POST"])
    def screening_mark_interest(job_id):
        """标记感兴趣：保存岗位到 jobs 表，写入持久感兴趣区（FR-018, FR-019）。

        请求体需含 profile_id 与 run_id；run_id 用于从第一层搜索产物定位岗位详情。
        链接经 normalize_job_link 校验，不安全返回 400。返回 interest_state。
        """
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get("profile_id")
        run_id = raw.get("run_id")
        if not profile_id:
            raise ValueError("profile_id 不能为空")
        if not run_id:
            raise ValueError("run_id 不能为空")
        try:
            store.get_profile(profile_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": "画像不存在"}), 404
        job = _find_screening_job(job_id, run_id)
        if not job:
            return jsonify({"error_code": "not_found", "user_message": "岗位不存在"}), 404
        saved = _save_screening_job_to_store(job)
        if not saved:
            return jsonify({"error_code": "invalid_link", "user_message": "岗位链接不安全"}), 400
        store.mark_screening_interest(profile_id, saved["id"], run_id=run_id)
        return jsonify({"interest_state": "interested", "job_id": saved["id"]})

    @app.route("/api/screening/jobs/<job_id>/reject", methods=["POST"])
    def screening_mark_reject(job_id):
        """标记不感兴趣：保存岗位到 jobs 表，写入持久垃圾桶区（FR-021）。

        请求体需含 profile_id 与 run_id。链接经 normalize_job_link 校验。
        返回 reject_state。
        """
        raw = request.get_json(silent=True) or {}
        profile_id = raw.get("profile_id")
        run_id = raw.get("run_id")
        if not profile_id:
            raise ValueError("profile_id 不能为空")
        if not run_id:
            raise ValueError("run_id 不能为空")
        origin_zone = raw.get("origin_zone") or "match"
        if origin_zone not in {"match", "mismatch", "pending", "interested"}:
            raise ValueError("origin_zone 无效")
        try:
            store.get_profile(profile_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": "画像不存在"}), 404
        job = _find_screening_job(job_id, run_id)
        if not job:
            return jsonify({"error_code": "not_found", "user_message": "岗位不存在"}), 404
        saved = _save_screening_job_to_store(job)
        if not saved:
            return jsonify({"error_code": "invalid_link", "user_message": "岗位链接不安全"}), 400
        store.reject_screening_with_origin(
            profile_id, saved["id"], source_job_id=job_id,
            run_id=run_id, origin_zone=origin_zone,
        )
        return jsonify({"reject_state": "rejected", "job_id": saved["id"]})

    @app.route("/api/screening/interested", methods=["GET"])
    def screening_interested_list():
        """返回持久感兴趣区岗位列表，可长期回看（FR-018, FR-024）。

        每条含 canonical_url，经 normalize_job_link 校验（仅 HTTPS 且预期 BOSS
        域名），不安全链接返回空字符串（FR-020）。需传入 profile_id 查询参数。
        """
        profile_id = request.args.get("profile_id")
        if not profile_id:
            raise ValueError("profile_id 不能为空")
        try:
            store.get_profile(profile_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": "画像不存在"}), 404
        interested = store.list_screening_interested(profile_id)
        items = []
        for pj in interested:
            try:
                job = store.get_job(pj["job_id"])
            except KeyError:
                continue
            canonical_url = normalize_job_link(job.get("canonical_url", ""))
            items.append({
                "job_id": job["id"],
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "salary": job.get("salary", ""),
                "location": job.get("location", ""),
                "canonical_url": canonical_url,
                "interest_state": "interested",
            })
        return jsonify({"items": items, "count": len(items)})

    @app.route("/api/screening/trash", methods=["GET"])
    def screening_trash_list():
        """返回持久垃圾桶区岗位列表，可查看（FR-024）。

        需传入 profile_id 查询参数。canonical_url 经 normalize_job_link 校验。
        """
        profile_id = request.args.get("profile_id")
        if not profile_id:
            raise ValueError("profile_id 不能为空")
        try:
            store.get_profile(profile_id)
        except KeyError:
            return jsonify({"error_code": "not_found", "user_message": "画像不存在"}), 404
        rejected = store.list_screening_rejected(profile_id)
        origins = {
            row["job_id"]: row for row in store.list_trash_with_origin(profile_id)
        }
        items = []
        for pj in rejected:
            try:
                job = store.get_job(pj["job_id"])
            except KeyError:
                continue
            canonical_url = normalize_job_link(job.get("canonical_url", ""))
            items.append({
                "job_id": job["id"],
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "salary": job.get("salary", ""),
                "location": job.get("location", ""),
                "canonical_url": canonical_url,
                "reject_state": "rejected",
                "origin_zone": origins.get(job["id"], {}).get("origin_zone", "match"),
            })
        return jsonify({"items": items, "count": len(items)})

    @app.route("/api/screening/trash/<job_id>/restore", methods=["POST"])
    def screening_restore_trash(job_id):
        """Restore a long-lived trash record, recreating a cleaned snapshot if needed."""
        profile_id = (request.get_json(silent=True) or {}).get("profile_id")
        if not profile_id:
            raise ValueError("profile_id 不能为空")
        restored = store.get_active_trash_record(profile_id, job_id)
        feedback = store.list_feedback(profile_id, job_id=job_id)
        was_interested = any(
            item.get("action") == "interested" and not item.get("revoked_at")
            for item in feedback
        )
        status = "interested" if restored["origin_zone"] == "interested" or was_interested else "new"
        recovery_run_id = restored.get("run_id")
        run_exists = False
        try:
            if recovery_run_id:
                store.get_screening_run(recovery_run_id)
                run_exists = True
        except KeyError:
            run_exists = False

        origin = restored["origin_zone"]
        create_recovery = origin in {"match", "mismatch", "pending"} and not run_exists
        output_path = None
        if create_recovery:
            recovery_run_id = uuid.uuid4().hex[:16]
            job = store.get_job(job_id)
            source_job_id = restored.get("source_job_id") or job_id
            artifact_job = {
                "job_id": source_job_id, "title": job.get("title", ""),
                "boss_name": job.get("company", ""), "salary": job.get("salary", ""),
                "location": job.get("location", ""), "jd": job.get("jd", ""),
                "job_link": job.get("canonical_url", ""),
            }
            output_path = Path(app.config["RESULT_DIR"]) / f"screening_{recovery_run_id}.json"
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with output_path.open("w", encoding="utf-8") as handle:
                    json.dump({"jobs": [artifact_job]}, handle, ensure_ascii=False)
            except OSError:
                return jsonify({
                    "error_code": "restore_artifact_failed",
                    "user_message": "恢复产物写入失败，岗位仍保留在垃圾桶",
                }), 500
        try:
            restored = store.complete_trash_restore(
                profile_id, job_id, status,
                recovery_run_id=recovery_run_id,
                create_recovery=create_recovery,
            )
        except (sqlite3.Error, ValueError, KeyError):
            if output_path:
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        return jsonify({
            "job_id": job_id, "restored_to": restored["origin_zone"],
            "recovery_run_id": recovery_run_id,
        })

    # == 003: 30-day temporary screening cleanup ========================

    @app.route("/api/screening/cleanup/preview", methods=["GET"])
    def screening_cleanup_preview():
        """Preview temporary screening cleanup, including pending warnings."""
        days = request.args.get("days", 30, type=int) or 30
        if days != 30:
            raise ValueError("screening cleanup days 必须为 30")
        return jsonify(store.preview_cleanup_with_pending_prompt(days=days))

    @app.route("/api/screening/cleanup", methods=["POST"])
    def screening_cleanup_execute():
        """Clean expired temporary screening data and record the outcome."""
        days = int((request.get_json(silent=True) or {}).get("days", 30))
        if days != 30:
            raise ValueError("screening cleanup days 必须为 30")
        result = store.cleanup_temp_run_data(days=days)
        run_ids = result.pop("run_ids", [])
        artifact_failures = 0
        result_root = Path(app.config["RESULT_DIR"]).resolve()
        for run_id in run_ids:
            for name in (f"screening_{run_id}.json", f"screening_{run_id}_details.json"):
                artifact = (result_root / name).resolve()
                if artifact.parent != result_root:
                    artifact_failures += 1
                    continue
                try:
                    artifact.unlink(missing_ok=True)
                except OSError:
                    artifact_failures += 1
        result["artifact_fail_count"] = artifact_failures
        result["fail_count"] += artifact_failures
        record = store.record_cleanup(
            f"screening_temp_{days}d",
            result["success_count"], result["fail_count"], result["pending_at_cleanup"],
        )
        return jsonify({"result": result, "record": record})

    @app.route("/api/screening/cleanup/history", methods=["GET"])
    def screening_cleanup_history():
        """Return queryable screening cleanup audit records."""
        items = store.list_cleanup_records()
        return jsonify({"items": items, "count": len(items)})

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
        if requested_sort == "relevance":
            profile_jobs.sort(key=lambda item: (item.get("ai_rank") is None, item.get("ai_rank") or 0, item.get("shown_at") or ""))
        elif requested_sort == "latest":
            profile_jobs.sort(key=lambda item: store.get_job(item["job_id"]).get("last_seen_at") or "", reverse=True)
        else:
            def salary_value(item):
                match = re.search(r"\d+(?:\.\d+)?", store.get_job(item["job_id"]).get("salary") or "")
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
            job = store.get_job(pj["job_id"])
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
        if feedback_count and feedback_count % 5 == 0 and settings.get("is_configured"):
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
        would_remove = store.preview_cleanup_expired_jobs(days=30)
        return jsonify({"would_remove": len(would_remove), "items": would_remove})

    # ===================================================================
    # 004 discovery: analyses, confirmations, runs, results, feedback
    # ===================================================================

    @app.errorhandler(_DiscoveryError)
    def handle_discovery_error(error):
        return jsonify(error.to_envelope()), _discovery_status_code(error)

    def _discovery_status_code(error):
        code = error.error_code
        if code == "not_found":
            return 404
        if code in {"state_conflict", "candidate_version_conflict"}:
            return 409
        return 400

    @app.route("/api/discovery/analyses", methods=["POST"])
    def discovery_create_analysis():
        """Create a queued candidate analysis attempt and submit it (US1).

        T109: 接受 application/json {resume_id, ai_consent}，创建 queued
        analysis 后提交 DiscoveryTaskRuntime 异步执行。不再同步调用 AI
        直接返回 ready/failed。
        """
        raw = request.get_json(silent=True) or {}
        resume_id = raw.get("resume_id")
        if not resume_id:
            raise ValueError("resume_id 不能为空")
        if "ai_consent" not in raw:
            raise ValueError("ai_consent 不能为空")
        if raw.get("ai_consent") is not True:
            raise ValueError("ai_consent 必须明确为 true")
        try:
            resume = store.get_resume(resume_id)
        except KeyError:
            raise _DiscoveryError("not_found", user_message="简历不存在。")
        profile_id = resume.get("profile_id")
        if not profile_id:
            raise _DiscoveryError(
                "input_incomplete", user_message="简历未关联候选人档案。",
            )
        # T109: 创建 queued attempt，提交 runtime 异步执行
        # Persist the canonical candidate v3 contract marker.
        ai_settings = store.get_ai_settings()
        analysis = store.create_analysis(
            resume_id, profile_id,
            model_name=ai_settings.get("model", ""),
            contract_version=(raw.get("contract_version") or "v3"),
        )
        discovery_runtime = app.config["DISCOVERY_RUNTIME"]
        discovery_runtime.submit_analysis(analysis["id"], ai_consent=True)
        return jsonify(_analysis_summary(analysis, store)), 202

    @app.route("/api/discovery/analyses/<analysis_id>")
    def discovery_get_analysis(analysis_id):
        analysis = store.get_analysis(analysis_id)
        evidence = store.list_evidence(analysis_id)
        directions = store.list_directions(analysis_id)
        return jsonify({
            "analysis_id": analysis["id"],
            "resume_id": analysis["resume_id"],
            "profile_id": analysis["profile_id"],
            "status": analysis["status"],
            "version": analysis.get("version"),
            "contract_version": analysis.get("contract_version", "v3"),
            "stage": analysis.get("stage", analysis.get("analysis_stage", "queued")),
            "quality_status": analysis.get("quality_status", "manual_required"),
            "quality_warnings": analysis.get("quality_warnings", []),
            "quality": {
                "status": analysis.get("quality_status", "complete"),
                "warnings": analysis.get("quality_warnings", []),
            },
            "summary": analysis.get("summary", {}),
            "evidence": [
                {
                    "id": e["id"],
                    "type": e["evidence_type"],
                    "normalized_value": e["normalized_value"],
                    "safe_excerpt": e.get("safe_excerpt", ""),
                    "assertion_type": e.get("assertion_type", "explicit"),
                    "confidence": e.get("confidence", 0),
                }
                for e in evidence
            ],
            "unknowns": analysis.get("unknowns", []),
            "directions": [
                {
                    "id": d["id"],
                    "name": d["name"],
                    "type": d["direction_type"],
                    "rationale": d.get("rationale", ""),
                    "evidence_ids": [r["evidence_id"] for r in store.list_direction_evidence(d["id"])],
                    "gaps": d.get("gaps", []),
                    "confidence": d.get("confidence", 0),
                    "default_enabled": d.get("default_enabled", False),
                    "search_terms": d.get("search_terms", []),
                }
                for d in directions
            ],
            "failure": _analysis_failure(analysis),
            "candidate_profile_version_id": analysis.get("candidate_profile_version_id"),
        }), 200

    @app.route("/api/discovery/candidate-versions/<version_id>", methods=["GET", "PATCH"])
    def discovery_candidate_version(version_id):
        try:
            if request.method == "GET":
                return jsonify(store.get_candidate_profile_version(version_id)), 200
            raw = request.get_json(silent=True) or {}
            expected_hash = raw.get("expected_content_hash")
            if not expected_hash:
                raise _DiscoveryError("candidate_version_conflict")
            try:
                updated = store.update_candidate_profile_draft(
                    version_id, expected_content_hash=expected_hash,
                    operations=raw.get("operations", []),
                    unknown_resolutions=raw.get("unknown_resolutions"),
                )
            except ValueError as exc:
                code = str(exc)
                if code == "candidate_version_conflict":
                    raise _DiscoveryError("candidate_version_conflict") from None
                if code == "candidate_version_not_draft":
                    raise _DiscoveryError("state_conflict") from None
                raise _DiscoveryError("candidate_fact_invalid") from None
            return jsonify(updated), 200
        except KeyError:
            raise _DiscoveryError("not_found", user_message="候选人画像版本不存在。") from None

    @app.route("/api/discovery/analyses/<analysis_id>/retry", methods=["POST"])
    def discovery_retry_analysis(analysis_id):
        """Create a new analysis attempt for the same resume (T109).

        仅在请求体显式给出 ai_consent=true 时创建新版本并提交。
        """
        try:
            analysis = store.get_analysis(analysis_id)
        except KeyError:
            raise _DiscoveryError("not_found", user_message="分析不存在。")
        raw = request.get_json(silent=True) or {}
        if raw.get("ai_consent") is not True:
            raise ValueError("ai_consent 必须明确为 true")
        resume_id = analysis["resume_id"]
        profile_id = analysis["profile_id"]
        # T109: 创建新版本 queued attempt，提交 runtime 异步执行
        new_analysis = store.create_analysis(
            resume_id, profile_id, contract_version="v3",
        )
        discovery_runtime = app.config["DISCOVERY_RUNTIME"]
        discovery_runtime.submit_analysis(new_analysis["id"], ai_consent=True)
        return jsonify(_analysis_summary(new_analysis, store)), 202

    @app.route("/api/discovery/confirmations", methods=["POST"])
    def discovery_create_confirmation():
        raw = request.get_json(silent=True) or {}
        analysis_id = raw.get("analysis_id")
        enabled_direction_ids = raw.get("enabled_direction_ids", [])
        hard_constraints = raw.get("hard_constraints", {})
        soft_preferences = raw.get("soft_preferences", {})
        safe_limits = raw.get("safe_limits", {})
        user_directions = raw.get("user_directions", [])
        candidate_profile_version_id = raw.get("candidate_profile_version_id")
        if candidate_profile_version_id:
            analysis = store.get_analysis(analysis_id)
            valid_direction_ids = {item["id"] for item in store.list_directions(analysis_id)}
            if not enabled_direction_ids or any(
                direction_id not in valid_direction_ids for direction_id in enabled_direction_ids
            ):
                raise _DiscoveryError("intent_invalid")
            expected_hash = raw.get("expected_content_hash")
            intent_hash = _compute_discovery_input_hash({
                "candidate_profile_version_id": candidate_profile_version_id,
                "profile_content_hash": expected_hash,
                "enabled_direction_ids": sorted(enabled_direction_ids),
                "hard_constraints": hard_constraints,
                "soft_preferences": soft_preferences,
                "safe_limits": safe_limits,
            }, policy_version="discovery_v2")
            try:
                confirmation = store.create_confirmation_v2(
                    candidate_profile_version_id=candidate_profile_version_id,
                    expected_content_hash=expected_hash,
                    hard_constraints=hard_constraints,
                    soft_preferences=soft_preferences,
                    safe_limits=safe_limits,
                    directions=[{
                        "direction_id": direction_id, "enabled": True,
                        "user_added": False, "user_label": None,
                    } for direction_id in enabled_direction_ids],
                    intent_hash=intent_hash,
                )
            except ValueError as exc:
                if str(exc) == "candidate_version_conflict":
                    raise _DiscoveryError("candidate_version_conflict") from None
                raise _DiscoveryError("state_conflict") from None
            return jsonify({
                "confirmation_id": confirmation["id"],
                "analysis_id": confirmation["analysis_id"],
                "candidate_profile_version_id": candidate_profile_version_id,
                "intent_contract_version": "intent_v2",
                "intent_hash": intent_hash,
                "version": confirmation["version"],
                "enabled_direction_ids": enabled_direction_ids,
                "confirmed_at": confirmation["confirmed_at"],
            }), 201
        confirmation = _discovery_confirm_directions(
            store, analysis_id, enabled_direction_ids,
            hard_constraints=hard_constraints,
            soft_preferences=soft_preferences,
            safe_limits=safe_limits,
            user_directions=user_directions,
        )
        return jsonify({
            "confirmation_id": confirmation["id"],
            "analysis_id": confirmation["analysis_id"],
            "version": confirmation["version"],
            "enabled_direction_ids": [d["direction_id"] for d in store.get_confirmation(confirmation["id"])["directions"]],
            "confirmed_at": confirmation["confirmed_at"],
        }), 201

    @app.route("/api/discovery/runs", methods=["POST"])
    def discovery_create_run():
        raw = request.get_json(silent=True) or {}
        confirmation_id = raw.get("confirmation_id")
        if not confirmation_id:
            raise ValueError("confirmation_id 不能为空")
        policy_version = raw.get("policy_version", "discovery_v1")
        if policy_version not in ("discovery_v1", "discovery_v2"):
            raise _DiscoveryError("state_conflict", user_message="不支持的 policy_version。")
        try:
            confirmation = store.get_confirmation(confirmation_id)
        except KeyError:
            raise _DiscoveryError("not_found", user_message="确认信息不存在。")
        # Build confirmation view for plan compilation
        directions = store.list_directions(confirmation["analysis_id"])
        enabled_dirs = []
        for d in directions:
            for cd in confirmation["directions"]:
                if cd["direction_id"] == d["id"] and cd["enabled"]:
                    enabled_dirs.append({
                        "id": d["id"],
                        "name": d["name"],
                        "search_terms": d.get("search_terms", []),
                    })
        confirmation_view = {
            "enabled_directions": enabled_dirs,
            "hard_constraints": confirmation.get("hard_constraints", {}),
            "safe_limits": confirmation.get("safe_limits", {}),
        }
        plan = _discovery_compile_plan(confirmation_view)
        run = store.create_discovery_run(
            profile_id=confirmation["profile_id"],
            resume_id=confirmation["resume_id"],
            analysis_id=confirmation["analysis_id"],
            confirmation_id=confirmation_id,
            input_hash=plan["input_hash"],
            policy_version=policy_version,
        )
        # T101/T133: 不在路由预创建 search_plan_items。
        # 1. 旧实现给所有 items 设置同一个 plan 级 input_hash，违反
        #    search_plan_items 的 UNIQUE (plan_id, input_hash) 约束；
        # 2. 旧 input_hash 与 _source_input_hash 计算结果不一致，导致
        #    runtime resume 校验时全部触发 input_hash_mismatch。
        # 改由 DiscoveryRuntime._stage_planning 用 _materialize_plan_items
        # 生成 per-item 唯一且与 source adapter 一致的 input_hash。
        discovery_runtime = app.config["DISCOVERY_RUNTIME"]
        discovery_runtime.submit_run(run["id"])
        return jsonify(_run_summary(run, store)), 202

    @app.route("/api/discovery/runs/<run_id>")
    def discovery_get_run(run_id):
        try:
            run = store.get_discovery_run(run_id)
        except KeyError:
            raise _DiscoveryError("not_found", user_message="运行不存在。")
        return jsonify(_run_summary(run, store)), 200

    @app.route("/api/discovery/runs/<run_id>/candidates")
    def discovery_run_candidates(run_id):
        """T048: Candidate diagnostic endpoint (http-api.md).

        Returns safe list fields, precheck, selection rank/reason and work
        state.  Never returns resume text or raw prompt.
        """
        try:
            store.get_discovery_run(run_id)
        except KeyError:
            raise _DiscoveryError("not_found", user_message="运行不存在。")
        decision = request.args.get("decision")
        state = request.args.get("state")
        direction_id = request.args.get("direction_id")
        limit = min(100, max(1, request.args.get("limit", 100, type=int) or 100))
        candidates = store.list_run_candidates(
            run_id,
            state=state,
            selection_decision=decision,
        )
        if direction_id:
            candidates = [
                c for c in candidates
                if direction_id in (c.get("direction_ids") or [])
            ]
        items = []
        for c in candidates[:limit]:
            items.append({
                "candidate_id": c["id"],
                "job_id": c["job_id"],
                "source_url": c.get("source_url", ""),
                "state": c.get("state", "discovered"),
                "selection_decision": c.get("selection_decision", "pending"),
                "selection_rank": c.get("selection_rank"),
                "selection_reason": c.get("selection_reason"),
                "precheck_outcome": c.get("precheck_outcome"),
                "direction_ids": c.get("direction_ids") or [],
                "list_fields": c.get("list_fields") or {},
            })
        return jsonify({"items": items, "next": None}), 200

    @app.route("/api/discovery/runs/<run_id>/cancel", methods=["POST"])
    def discovery_cancel_run(run_id):
        try:
            run = store.get_discovery_run(run_id)
        except KeyError:
            raise _DiscoveryError("not_found", user_message="运行不存在。")
        terminal = {"succeeded", "failed", "cancelled"}
        if run["status"] in terminal:
            raise _DiscoveryError("state_conflict", user_message=f"运行已终态 ({run['status']})。")
        # T101: 委托给应用持有的 runtime，先持久化取消请求再阻止后续工作。
        discovery_runtime = app.config["DISCOVERY_RUNTIME"]
        try:
            updated = discovery_runtime.cancel_run(run_id)
        except _DiscoveryError:
            raise
        return jsonify(_run_summary(updated, store)), 202

    @app.route("/api/discovery/runs/<run_id>/resume", methods=["POST"])
    def discovery_resume_run(run_id):
        try:
            run = store.get_discovery_run(run_id)
        except KeyError:
            raise _DiscoveryError("not_found", user_message="运行不存在。")
        resumable = {"interrupted", "partial"}
        if run["status"] not in resumable:
            raise _DiscoveryError("state_conflict", user_message=f"运行状态 {run['status']} 不可恢复。")
        # T083: Synchronous hash drift check (http-api.md L319).
        # Reject profile/confirmation/policy/input hash drift with 409 before
        # re-submitting work, so clients can immediately detect drift instead
        # of waiting for the background thread to fail asynchronously.
        from webui.discovery_runner import check_v2_resume_hash_drift
        try:
            check_v2_resume_hash_drift(store, run)
        except _DiscoveryError:
            raise
        # T101: 真实重新提交未完成工作，不得仅修改显示状态。
        discovery_runtime = app.config["DISCOVERY_RUNTIME"]
        discovery_runtime.resume_run(run_id)
        return jsonify(_run_summary(store.get_discovery_run(run_id), store)), 202

    @app.route("/api/discovery/runs/<run_id>/results")
    def discovery_run_results(run_id):
        try:
            run = store.get_discovery_run(run_id)
        except KeyError:
            raise _DiscoveryError("not_found", user_message="运行不存在。")
        category = request.args.get("category")
        direction_id = request.args.get("direction_id")
        limit = min(100, max(1, request.args.get("limit", 20, type=int) or 20))
        after_revision = request.args.get("after_revision", type=int)
        policy_version = run.get("policy_version", "discovery_v1")
        server_revision = int(run.get("result_revision") or 0)
        # T048: v2 after_revision short-circuit — if client is up-to-date,
        # return changed=false with empty items (http-api.md).
        if policy_version == "discovery_v2" and after_revision is not None:
            if after_revision >= server_revision:
                return jsonify({
                    "run_id": run_id,
                    "run_status": run["status"],
                    "revision": server_revision,
                    "changed": False,
                    "complete": run["status"] in ("succeeded", "partial", "failed", "cancelled"),
                    "items": [],
                    "counts": {},
                    "next": None,
                }), 200

        snapshots = store.list_snapshots(run_id) if hasattr(store, "list_snapshots") else []
        assessments = store.list_assessments(run_id) if hasattr(store, "list_assessments") else []
        directions_by_id = {}
        try:
            dirs = store.list_directions(run.get("analysis_id", ""))
            directions_by_id = {d["id"]: d for d in dirs}
        except Exception:
            pass

        # T064: v2 runs use the canonical recommendation projector.
        if policy_version == "discovery_v2":
            # Normalize assessments: attach snapshot_completeness from snapshot.
            snap_by_id = {s["id"]: s for s in snapshots}
            normalized_assessments = []
            for a in assessments:
                snap = snap_by_id.get(a.get("snapshot_id", ""), {})
                normalized_assessments.append({
                    **a,
                    "job_id": snap.get("job_id", a.get("job_id", "")),
                    "snapshot_completeness": snap.get("completeness", "unavailable"),
                })
            # Build snapshot dicts for the projector.
            snap_dicts = [
                {
                    "id": s["id"],
                    "job_id": s.get("job_id", ""),
                    "fields": {
                        "title": s.get("title", ""),
                        "company": s.get("company", ""),
                        "salary": s.get("salary", ""),
                        "location": s.get("location", ""),
                        "jd": s.get("jd", ""),
                        "tags": s.get("tags") or [],
                    },
                    "source_url": normalize_job_link(s.get("source_url", "")),
                    "source_status": s.get("source_status", "unknown"),
                    "fetched_at": s.get("fetched_at", ""),
                    "completeness": s.get("completeness", "unavailable"),
                    "reused": bool(s.get("reused")),
                }
                for s in snapshots
            ]
            # Only include snapshots with valid BOSS HTTPS links (FR-042).
            snap_dicts = [s for s in snap_dicts if s["source_url"]]
            direction_list = list(directions_by_id.values())
            items = _project_recommendations(
                run_id, snap_dicts, normalized_assessments, direction_list,
                direction_filter=direction_id,
                category_filter=category,
            )
            # Enrich assessments with direction_name.
            for item in items:
                for a in item.get("assessments", []):
                    did = a.get("direction_id", "")
                    a["direction_name"] = (directions_by_id.get(did) or {}).get("name", "")
                pa = item.get("primary_assessment", {})
                pa["direction_name"] = (
                    directions_by_id.get(pa.get("direction_id", ""), {}).get("name", "")
                )
            counts: dict[str, int] = {}
            for item in items:
                cat = item.get("category", "needs_review")
                counts[cat] = counts.get(cat, 0) + 1
            items = items[:limit]
            return jsonify({
                "run_id": run_id,
                "run_status": run["status"],
                "revision": server_revision,
                "changed": True,
                "complete": run["status"] in ("succeeded", "partial", "failed", "cancelled"),
                "items": items,
                "counts": counts,
                "next": None,
            }), 200

        # v1 fallback: legacy per-snapshot assembly.
        assessments_by_snapshot: dict[str, list[dict]] = {}
        for a in assessments:
            sid = a.get("snapshot_id", "")
            assessments_by_snapshot.setdefault(sid, []).append(a)
        category_priority = {"high_match": 0, "adjacent_match": 1, "growth_match": 2,
                             "needs_review": 3, "not_suitable": 4}
        items = []
        for snap in snapshots:
            source_url = normalize_job_link(snap.get("source_url", ""))
            if not source_url:
                continue
            snap_id = snap["id"]
            snap_assessments = [
                _normalize_discovery_assessment({
                    **assessment,
                    "snapshot_completeness": snap.get("completeness", "unavailable"),
                })
                for assessment in assessments_by_snapshot.get(snap_id, [])
            ]
            if not snap_assessments:
                continue
            if direction_id:
                snap_assessments = [a for a in snap_assessments if a.get("direction_id") == direction_id]
                if not snap_assessments:
                    continue
            primary_idx = min(range(len(snap_assessments)),
                              key=lambda i: category_priority.get(
                                  snap_assessments[i].get("category", "needs_review"), 99))
            primary = snap_assessments[primary_idx]
            primary_category = primary.get("category", "needs_review")
            if category and primary_category != category:
                continue
            def _to_summary(a):
                did = a.get("direction_id", "")
                return {
                    "direction_id": did,
                    "direction_name": (directions_by_id.get(did) or {}).get("name", ""),
                    "category": a.get("category", "needs_review"),
                    "hard_outcome": a.get("hard_outcome", "unknown"),
                    "match_score": a.get("match_score"),
                    "confidence": a.get("confidence"),
                    "evidence": [{"client_ref": eid} for eid in (a.get("candidate_evidence_ids") or [])],
                    "gaps": a.get("gaps") or [],
                    "failure_code": a.get("failure_code"),
                }
            assessment_summaries = [_to_summary(a) for a in snap_assessments]
            items.append({
                "job_id": snap.get("job_id", ""),
                "title": snap.get("title", ""),
                "company": snap.get("company", ""),
                "salary": snap.get("salary", ""),
                "location": snap.get("location", ""),
                "source_url": source_url,
                "source_status": snap.get("source_status", "unknown"),
                "completeness": snap.get("completeness", "unavailable"),
                "missing_fields": snap.get("missing_fields") or [],
                "primary_assessment": assessment_summaries[primary_idx],
                "assessments": assessment_summaries,
            })
        counts = {}
        for item in items:
            cat = item["primary_assessment"]["category"]
            counts[cat] = counts.get(cat, 0) + 1
        items = items[:limit]
        return jsonify({"items": items, "counts": counts, "next": None}), 200

    @app.route("/api/discovery/runs/<run_id>/jobs/<job_id>/retry", methods=["POST"])
    def discovery_retry_job(run_id, job_id):
        try:
            run = store.get_discovery_run(run_id)
        except KeyError:
            raise _DiscoveryError("not_found", user_message="运行不存在。")
        if run["status"] in {"succeeded", "failed", "cancelled"}:
            raise _DiscoveryError("state_conflict", user_message="运行已终态。")
        # T119: 真实提交到 runtime，重置 snapshot 并重新提交 run
        discovery_runtime = app.config["DISCOVERY_RUNTIME"]
        try:
            updated = discovery_runtime.retry_job(run_id, job_id)
        except _DiscoveryError:
            raise
        return jsonify({"accepted": True, "run_id": run_id, "job_id": job_id}), 202

    def _discovery_preference_changes(feedback_items):
        changes = []
        for item in feedback_items:
            base = {
                "feedback_id": item["id"],
                "reason_code": item.get("reason_code"),
                "created_at": item.get("created_at"),
            }
            if item.get("target_type") == "direction" and item.get("action") == "direction_disable":
                changes.append({**base, "kind": "direction_disabled", "direction_id": item.get("direction_id")})
            elif item.get("target_type") == "job" and item.get("action") == "not_interested":
                changes.append({**base, "kind": "job_excluded", "job_id": item.get("job_id"), "scope": item.get("scope")})
            elif item.get("target_type") == "job" and item.get("action") == "interested":
                changes.append({**base, "kind": "job_interested", "job_id": item.get("job_id"), "scope": item.get("scope")})
        return changes

    def _discovery_feedback_payload(items):
        safe_items = [{
            "id": item["id"],
            "run_id": item.get("run_id"),
            "job_id": item.get("job_id"),
            "direction_id": item.get("direction_id"),
            "target_type": item.get("target_type"),
            "action": item.get("action"),
            "reason_code": item.get("reason_code"),
            "scope": item.get("scope"),
            "created_at": item.get("created_at"),
        } for item in items]
        return {
            "items": safe_items,
            "preference_changes": _discovery_preference_changes(safe_items),
        }

    @app.route("/api/discovery/feedback", methods=["GET", "POST"])
    def discovery_create_feedback():
        if request.method == "GET":
            profile_id = request.args.get("profile_id")
            if not profile_id:
                raise ValueError("profile_id 不能为空")
            try:
                store.get_profile(profile_id)
            except KeyError:
                raise _DiscoveryError("not_found", user_message="画像不存在。")
            feedback_items = store.list_discovery_feedback(profile_id, effective_only=True)
            run_id = request.args.get("run_id")
            if run_id:
                feedback_items = [item for item in feedback_items if item.get("run_id") == run_id]
            return jsonify(_discovery_feedback_payload(feedback_items)), 200

        raw = request.get_json(silent=True) or {}
        required = ("profile_id", "target_type", "action")
        for field in required:
            if not raw.get(field):
                raise ValueError(f"{field} 不能为空")
        if raw["target_type"] == "job" and not (raw.get("target_id") or raw.get("job_id")):
            raise ValueError("job_id 不能为空")
        if raw["target_type"] == "direction" and not raw.get("direction_id"):
            raise ValueError("direction_id 不能为空")
        feedback = store.create_discovery_feedback(
            profile_id=raw["profile_id"],
            target_type=raw["target_type"],
            action=raw["action"],
            scope=raw.get("scope", "exact_job"),
            safe_note=raw.get("note", ""),
            run_id=raw.get("run_id"),
            job_id=raw.get("target_id") or raw.get("job_id"),
            direction_id=raw.get("direction_id"),
            assessment_id=raw.get("assessment_id"),
            reason_code=raw.get("reason_code"),
        )
        visible = _discovery_feedback_payload(store.list_discovery_feedback(raw["profile_id"], effective_only=True))
        return jsonify({
            "feedback_id": feedback["id"],
            "effective_scope": feedback.get("scope", "exact_job"),
            "preference_changes": visible["preference_changes"],
        }), 201

    @app.route("/api/discovery/feedback/<feedback_id>/revoke", methods=["POST"])
    def discovery_revoke_feedback(feedback_id):
        try:
            feedback = store.revoke_discovery_feedback(feedback_id)
        except KeyError:
            raise _DiscoveryError("not_found", user_message="反馈不存在。")
        remaining = store.list_discovery_feedback(feedback["profile_id"], effective_only=True)
        return jsonify({
            "revoked": True,
            "preference_changes": _discovery_preference_changes(remaining),
        }), 200

    # Helper functions for discovery routes
    def _analysis_summary(analysis, store):
        return {
            "analysis_id": analysis["id"],
            "resume_id": analysis["resume_id"],
            "profile_id": analysis["profile_id"],
            "status": analysis["status"],
            "version": analysis.get("version"),
            "stage": analysis.get("stage", "queued"),
            "quality_status": analysis.get("quality_status", "complete"),
            "quality_warnings": analysis.get("quality_warnings", []),
            "contract_version": analysis.get("contract_version", "v3"),
            "candidate_profile_version_id": analysis.get("candidate_profile_version_id"),
        }

    def _analysis_failure(analysis):
        if analysis.get("status") != "failed":
            return None
        code = analysis.get("failure_code") or "verification_error"
        return {
            "error_code": code,
            "user_message": _DiscoveryError(code).user_message if code in _ERROR_CODE_MAP else "分析失败。",
            "stage": "analyzing",
            "retryable": _ERROR_CODE_MAP.get(code, {}).get("retryable", False),
        }

    def _run_summary(run, store):
        # T133: openapi.yaml Run schema 要求 progress + counts 两个对象，
        # 都是 additionalProperties: integer。旧实现返回空数据，导致
        # E2E 和 HTTP 客户端无法读取真实进度。
        # get_discovery_run 返回的 run 字典包含直接列名（source_count 等）
        # 和 progress/counts 包装字段；这里统一从直接列名构建，避免双重包装。
        failure = None
        if run.get("failure_code"):
            failure = {
                "code": run["failure_code"],
                "stage": run.get("failure_stage", ""),
            }
        policy_version = run.get("policy_version", "discovery_v1")
        # T083: v2 authoritative progress names (http-api.md L203-208).
        # v1 aliases (source_count/detail_count/evaluated_count) kept as
        # compatibility aliases; v2 names are authoritative.
        source_count = int(run.get("source_count", 0))
        detail_count = int(run.get("detail_count", 0))
        evaluated_count = int(run.get("evaluated_count", 0))
        # recommendations = high_match + adjacent_match + growth_match
        # (categorized recommendation count, not the raw recommendation_count
        # column which is never written by the runner).
        recommendations = (
            int(run.get("high_count", 0))
            + int(run.get("adjacent_count", 0))
            + int(run.get("growth_count", 0))
        )
        progress = {
            "source_count": source_count,
            "detail_count": detail_count,
            "evaluated_count": evaluated_count,
            # v2 authoritative names (http-api.md L203-208)
            "search_queries_completed": source_count,
            "list_candidates": int(run.get("list_candidate_count") or 0),
            "details_selected": int(run.get("detail_selected_count") or 0),
            "details_completed": int(run.get("detail_completed_count") or 0),
            "assessments_completed": int(run.get("assessment_completed_count") or 0),
            "recommendations": recommendations,
        }
        # T083: complete flag distinguishes terminal (true) from recoverable
        # (false). Terminal statuses: succeeded/failed/cancelled/partial.
        # interrupted and active statuses are not complete.
        status = run["status"]
        complete = status in {"succeeded", "failed", "cancelled", "partial"}
        summary = {
            "run_id": run["id"],
            "confirmation_id": run.get("confirmation_id", ""),
            "policy_version": policy_version,
            "status": status,
            "stage": run.get("stage", status),
            "progress": progress,
            "counts": {
                "high": int(run.get("high_count", 0)),
                "adjacent": int(run.get("adjacent_count", 0)),
                "growth": int(run.get("growth_count", 0)),
                "review": int(run.get("review_count", 0)),
                "unsuitable": int(run.get("unsuitable_count", 0)),
            },
            "failure": failure,
            "updated_at": run.get("updated_at", run.get("created_at", "")),
            "complete": complete,
        }
        # T083: cancel_requested_at present only when cancel has been requested
        # (http-api.md L318). Avoids null fields in non-cancelled runs.
        cancel_requested_at = run.get("cancel_requested_at")
        if cancel_requested_at:
            summary["cancel_requested_at"] = cancel_requested_at
        if policy_version == "discovery_v2":
            summary["result_revision"] = int(run.get("result_revision") or 0)
        return summary

    def _build_ai_provider(store):
        """Construct a DiscoveryAIProvider from current AI settings.

        T101/T099: 返回真实 DiscoveryAIProvider（仅持 endpoint/model/api_key），
        不再返回未定义的 _AIProviderAdapter。不读写 TaskStore，不泄漏凭据。
        """
        settings = store.get_ai_settings()
        if not settings.get("is_configured"):
            return None
        cred_ref = store.get_credential_ref()
        if not cred_ref:
            return None
        api_key = ai_service.retrieve_api_key(cred_ref)
        if not api_key:
            return None
        return ai_service.DiscoveryAIProvider(
            endpoint=settings.get("endpoint_url", ""),
            model=settings.get("model", ""),
            api_key=api_key,
        )

    # ------------------------------------------------------------------
    # Three-stage pipeline: resume → AI fields → user confirm → script
    # ------------------------------------------------------------------

    # Stage-3 execution: in-memory progress tracker + single-worker executor.
    # Each run is keyed by a task_id; progress snapshots and final results are
    # stored here and polled by the frontend. Local single-user app, so an
    # in-memory dict is sufficient.
    import threading as _threading
    _pipeline_tasks = {}
    _pipeline_lock = _threading.Lock()
    _pipeline_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="boss-pipeline")

    def _save_latest_pipeline_result(result, script_params):
        """Persist the latest successful run so a page refresh can restore it.

        Writes atomically (temp file + os.replace) so an interrupted write
        never leaves a corrupt latest-result file.  Each new successful run
        overwrites the previous one.
        """
        payload = {
            "saved_at": time.time(),
            "script_params": script_params,
            "result": result,
        }
        try:
            LATEST_PIPELINE_RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = LATEST_PIPELINE_RESULT_PATH.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(tmp, LATEST_PIPELINE_RESULT_PATH)
        except OSError:
            pass  # persistence is best-effort; the in-memory result still works

    def _load_latest_pipeline_result():
        """Return the persisted latest run payload, or None if absent/unreadable."""
        try:
            with open(LATEST_PIPELINE_RESULT_PATH, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
            return None
        return payload

    def _save_latest_scrape_result(result, script_params):
        """持久化"只抓不筛"的原始抓取结果，供 AI 筛选步骤读取（原子写入）。"""
        payload = {
            "saved_at": time.time(),
            "script_params": script_params,
            "result": result,
        }
        try:
            LATEST_SCRAPE_RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = LATEST_SCRAPE_RESULT_PATH.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(tmp, LATEST_SCRAPE_RESULT_PATH)
        except OSError:
            pass

    def _load_latest_scrape_result():
        """读取最近一次原始抓取结果；不存在/不可读返回 None。"""
        try:
            with open(LATEST_SCRAPE_RESULT_PATH, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
            return None
        return payload

    def _run_pipeline_task(task_id, script_params):
        from webui.pipeline_exec import run_search
        with _pipeline_lock:
            _pipeline_tasks[task_id] = {
                "status": "running", "progress": {}, "logs": [],
                "result": None, "error": "",
            }

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
            source = _make_discovery_source()
            if source is None:
                raise RuntimeError("source_unavailable")
            result = run_search(
                script_params, source,
                pages=3, progress=on_progress,
                artifact_dir=app.config["RESULT_DIR"],
            )
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    task["result"] = result
                    task["error"] = result.get("error", "")
                    task["status"] = "done" if result.get("ok") else "failed"
            # 抓取成功：持久化原始结果供 AI 筛选步骤读取（最终筛选结果另存）
            if result.get("ok"):
                _save_latest_scrape_result(result, script_params)
        except Exception as exc:
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    task["status"] = "failed"
                    task["error"] = f"执行异常：{type(exc).__name__}"

    def _run_ai_screen_task(task_id, screening_fields, profile_summary):
        """AI 筛选任务：StageA 字段粗筛 → 批量抓 JD → StageB JD 精筛。

        读取最近一次原始抓取结果，两段式 AI 筛选后把带 verdict 的最终结果
        持久化到 latest_pipeline_result.json（供结果页恢复）。
        """
        from webui.pipeline_exec import ensure_chrome_ready, close_debug_chrome, fetch_job_details
        from webui.ai import screen_jobs, match_jds

        with _pipeline_lock:
            _pipeline_tasks[task_id] = {
                "status": "running", "progress": {}, "logs": [],
                "result": None, "error": "",
            }

        def emit(**kw):
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is None:
                    return
                task["progress"] = kw
                msg = kw.get("message")
                if msg:
                    task["logs"].append(msg)

        try:
            # 1) 读取原始抓取结果（只抓不筛的全量岗位）
            scrape_payload = _load_latest_scrape_result()
            if scrape_payload is None:
                raise RuntimeError("no_scrape_result")
            raw_jobs = scrape_payload["result"].get("jobs", [])
            if not raw_jobs:
                raise RuntimeError("empty_scrape_result")

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

            # 3) Stage A：字段粗筛（移除明显不符，学历向下兼容）
            emit(stage="screen_a", current=0, total=len(raw_jobs),
                 message="AI 粗筛中（对照筛选字段）…")

            def _a_progress(cur, tot):
                emit(stage="screen_a", current=cur, total=tot,
                     message=f"AI 粗筛 {cur}/{tot}")

            screen_result = screen_jobs(raw_jobs, criteria, endpoint, api_key,
                                        model=model, progress=_a_progress)
            kept_ids = set(screen_result["kept"])
            survivors = [j for j in raw_jobs if str(j.get("job_id", "")) in kept_ids]
            dropped = screen_result["dropped"]
            emit(stage="screen_a_done", kept=len(survivors), dropped=len(dropped),
                 message=f"粗筛完成：保留 {len(survivors)} 条，移除 {len(dropped)} 条")

            # 4) 对保留的岗位批量抓 JD（重开调试浏览器，抓完关闭）
            enriched = survivors
            if survivors:
                emit(stage="ensure_chrome", message="启动调试浏览器，准备抓取 JD…")
                if not ensure_chrome_ready():
                    raise RuntimeError("chrome_not_ready")
                source = _make_discovery_source()
                if source is None:
                    raise RuntimeError("source_unavailable")

                def _jd_progress(cur, tot):
                    emit(stage="fetch_jd", current=cur, total=tot,
                         message=f"抓取 JD {cur}/{tot}")

                emit(stage="fetch_jd", current=0, total=len(survivors),
                     message=f"抓取 JD（0/{len(survivors)}）…")
                enriched = fetch_job_details(survivors, source,
                                             artifact_dir=app.config["RESULT_DIR"],
                                             progress=_jd_progress)
                close_debug_chrome()

            # 5) Stage B：JD 精筛（对比候选人画像）
            jobs_with_jd = [j for j in enriched if str(j.get("jd", "")).strip()]
            emit(stage="screen_b", current=0, total=len(jobs_with_jd),
                 message="AI 精筛中（JD 对比简历画像）…")

            def _b_progress(cur, tot):
                emit(stage="screen_b", current=cur, total=tot,
                     message=f"AI 精筛 {cur}/{tot}")

            match_result = match_jds(jobs_with_jd, profile_summary, endpoint, api_key,
                                     model=model, progress=_b_progress)
            verdicts = match_result["verdicts"]
            for job in enriched:
                jid = str(job.get("job_id", ""))
                v = verdicts.get(jid)
                if v:
                    job["verdict"] = v["verdict"]
                    job["verdict_reason"] = v["reason"]
                else:
                    # 未抓到 JD 的岗位无法精筛，标记待定（不红不绿）
                    job["verdict"] = "uncertain"
                    job["verdict_reason"] = "未抓到 JD，无法精筛"

            match_count = sum(1 for j in enriched if j.get("verdict") == "match")
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
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    task["result"] = result
                    task["status"] = "done"
            emit(stage="done", total_matched=match_count,
                 message=f"筛选完成：匹配 {match_count} 条")
            _save_latest_pipeline_result(result, {"screening": screening_fields})
        except Exception as exc:
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    task["status"] = "failed"
                    task["error"] = f"AI 筛选异常：{type(exc).__name__}"

    @app.route("/api/analyze-resume", methods=["POST"])
    def analyze_resume():
        """Stage 1: Upload resume file → AI reads it → returns unified search fields.

        Accepts multipart form with 'file' field (PDF/DOCX/TXT).
        Returns JSON with the unified schema fields for user confirmation.
        """
        from webui.resume import validate_format, validate_size
        from webui.ai import analyze_resume_to_fields, AISecurityError

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
            return jsonify({"ok": False, "error": f"AI 分析失败: {exc.error_code}"}), 502
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        # Return fields with human-readable labels for confirmation UI
        field_labels = {
            "keyword": ("搜索关键词", fields["keyword"], "text"),
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

        # Build the exact parameters the script consumes
        script_params = {
            "keyword": fields["keyword"],
            "city": fields["city"],
            "filters": {},
        }
        for key in ("salary", "experience", "degree", "industry", "scale", "stage"):
            if fields.get(key):
                script_params["filters"][key] = fields[key]

        return jsonify({"ok": True, "confirmed_fields": fields, "script_params": script_params})

    @app.route("/api/execute-search", methods=["POST"])
    def execute_search():
        """Stage 3: Start the scraping run with confirmed script_params.

        Accepts JSON ``{"script_params": {...}}`` (or the params directly).
        Launches a background task and returns a ``task_id`` for polling.
        """
        body = request.get_json(silent=True) or {}
        script_params = body.get("script_params") or body
        if not isinstance(script_params, dict):
            return jsonify({"ok": False, "error": "无效的请求体"}), 400
        if not script_params.get("keyword") or not script_params.get("city"):
            return jsonify({"ok": False, "error": "缺少关键词或城市"}), 400

        task_id = uuid.uuid4().hex
        _pipeline_executor.submit(_run_pipeline_task, task_id, script_params)
        return jsonify({"ok": True, "task_id": task_id})

    @app.route("/api/ai-screen", methods=["POST"])
    def ai_screen():
        """Stage 3b：对已抓取的原始岗位做两段式 AI 筛选。

        接收 ``{"screening_fields": {...}, "profile_summary": "..."}``，
        启动后台任务（StageA 粗筛→抓JD→StageB 精筛）并返回 ``task_id`` 供轮询。
        """
        body = request.get_json(silent=True) or {}
        screening_fields = body.get("screening_fields") or {}
        profile_summary = str(body.get("profile_summary") or "")
        if not isinstance(screening_fields, dict):
            return jsonify({"ok": False, "error": "无效的筛选字段"}), 400
        task_id = uuid.uuid4().hex
        _pipeline_executor.submit(_run_ai_screen_task, task_id, screening_fields, profile_summary)
        return jsonify({"ok": True, "task_id": task_id})

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
            snapshot = {
                "ok": True,
                "status": task["status"],
                "progress": task["progress"],
                "logs": list(task["logs"][-50:]),
                "error": task["error"],
            }
            if task["status"] in ("done", "failed") and task["result"] is not None:
                # 原样返回整个 result：抓取任务含 jobs/计数；
                # AI 筛选任务还含 dropped/verdict/profile_summary 等
                snapshot["result"] = task["result"]
        return jsonify(snapshot)

    @app.route("/api/latest-pipeline-result")
    def latest_pipeline_result():
        """Return the persisted latest pipeline run (survives page refresh).

        Only a successful run is persisted, so this always reflects the most
        recent good data.  ``has_result`` is false until the first successful
        run (or if the file is missing/unreadable).
        """
        payload = _load_latest_pipeline_result()
        if payload is None:
            return jsonify({"ok": True, "has_result": False})
        result = payload["result"]
        return jsonify({
            "ok": True,
            "has_result": True,
            "saved_at": payload.get("saved_at"),
            "script_params": payload.get("script_params", {}),
            "result": {
                "total_scraped": result.get("total_scraped", 0),
                "total_matched": result.get("total_matched", 0),
                "total_kept": result.get("total_kept", 0),
                "total_dropped": result.get("total_dropped", 0),
                "combinations": result.get("combinations", 0),
                "jobs": result.get("jobs", []),
                "dropped": result.get("dropped", []),
                "profile_summary": result.get("profile_summary", ""),
            },
        })

    # ------------------------------------------------------------------
    # Pipeline 结果增强：按需抓 JD 详情 + 感兴趣/不感兴趣（接入筛选工作台）
    # ------------------------------------------------------------------
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

        if not ensure_chrome_ready():
            return jsonify({"ok": False,
                            "error": "调试浏览器未能就绪，无法抓取详情"}), 503

        source = _make_discovery_source()
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

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, threaded=True)
