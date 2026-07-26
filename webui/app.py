#!/usr/bin/env python3
"""Local Flask API for persistent BOSS scraping and explainable job ranking."""

from __future__ import annotations

import csv
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
from webui.source import BossCdpSource as _BossCdpSource
from webui.process_executor import ArtifactSpec, ScraperExecutor
from webui import resume as resume_service
from webui import ai as ai_service


SCRAPER = PROJECT_ROOT / "scripts" / "boss_cdp_raw.py"
DEFAULT_STATE_DIR = Path(os.environ.get("BOSS_WEBUI_STATE_DIR", os.path.expanduser("~/.career-scout/webui")))


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
        PYTHON_EXECUTABLE=_resolve_python_executable(),
        START_TASKS=True,
        API_TOKEN=secrets.token_urlsafe(24),
        SESSION_COOKIE_NAME="boss_local_session",
        TRUSTED_HOSTS=["127.0.0.1", "localhost", "::1"],
        RESUME_DIR=str(DEFAULT_STATE_DIR / "resumes"),
    )
    if config:
        app.config.update(config)
    if app.config.get("TESTING") and "START_TASKS" not in (config or {}):
        app.config["START_TASKS"] = False

    store = TaskStore(app.config["DB_PATH"])
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

    def _make_cdp_source():
        try:
            return _BossCdpSource(
                python_executable=app.config["PYTHON_EXECUTABLE"],
                artifact_root=app.config["RESULT_DIR"],
            )
        except Exception:
            return None

    Path(app.config["RESUME_DIR"]).mkdir(parents=True, exist_ok=True)
    app.config["TASK_STORE"] = store
    app.config["TASK_RUNNER"] = runner
    app.config["WORKBENCH_RUNNER"] = workbench_runner

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
    _pipeline_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="boss-pipeline")
    app.config["PIPELINE_TASKS"] = _pipeline_tasks
    app.config["PIPELINE_EXECUTOR"] = _pipeline_executor

    def _register_pipeline_task(task_id, kind, *, source_task_id=None):
        task = {
            "kind": kind,
            "status": "queued",
            "progress": {},
            "logs": [],
            "result": None,
            "error": "",
            # 停止信号：cancel 接口 set 它，run_search 循环检查到后退出。
            # 不放进 task 的 JSON 序列化里（threading.Event 不可序列化），
            # 只在服务进程内存中存活。
            "stop_event": threading.Event(),
        }
        if source_task_id:
            task["source_task_id"] = source_task_id
        with _pipeline_lock:
            _pipeline_tasks[task_id] = task
        return task

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

    def _screen_overall_percent(stage: str, current: int, total: int) -> int:
        """把 AI 筛选 pipeline 的当前阶段映射到整体百分比（0-100）。"""
        start, end = _SCREEN_STAGE_WEIGHTS.get(stage, (0, 100))
        if total <= 0:
            return start
        ratio = min(1.0, max(0.0, current / total))
        return min(100, round(start + (end - start) * ratio))

    def _run_pipeline_task(task_id, script_params):
        from webui.pipeline_exec import run_search
        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)
            if task is None:
                task = {
                    "kind": "scrape", "status": "queued", "progress": {},
                    "logs": [], "result": None, "error": "",
                }
                _pipeline_tasks[task_id] = task
            task["status"] = "running"

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
            source = _make_cdp_source()
            if source is None:
                raise RuntimeError("source_unavailable")
            # 取出停止信号传给 run_search；cancel 接口 set 它后，
            # run_search 会在下一个组合边界退出，或因浏览器被关而抛错。
            with _pipeline_lock:
                stop_event = _pipeline_tasks.get(task_id, {}).get("stop_event")
            result = run_search(
                script_params, source,
                pages=3, progress=on_progress,
                artifact_dir=app.config["RESULT_DIR"],
                stop_event=stop_event,
            )
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
                    else:
                        task["status"] = "done" if result.get("ok") else "failed"
            _schedule_pipeline_task_cleanup(task_id)
        except Exception as exc:
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    # 异常时也要看 stop_event：用户主动取消导致的异常算 cancelled
                    stop_event = task.get("stop_event")
                    if stop_event is not None and stop_event.is_set():
                        task["status"] = "cancelled"
                        task["error"] = "用户已停止抓取"
                    else:
                        task["status"] = "failed"
                        task["error"] = f"执行异常：{type(exc).__name__}"
            _schedule_pipeline_task_cleanup(task_id)

    def _jd_checkpoint_path(result_dir, run_id):
        return os.path.join(result_dir, f"ai_screen_jd_{run_id}.json")

    def _load_jd_checkpoint(path):
        """读取 JD 断点文件 {job_id: jd}；不存在/不可读返回空 dict。"""
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()
                        if isinstance(v, str) and v.strip()}
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _save_jd_checkpoint(path, jd_map):
        """原子写 JD 断点文件（每批抓完落盘，进程崩了已抓的也不丢）。"""
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(jd_map, handle, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            pass  # 落盘失败不阻断抓取（内存数据仍在）

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
        from webui.pipeline_exec import ensure_chrome_ready, close_debug_chrome, fetch_job_details
        from webui.ai import screen_jobs, match_jds

        with _pipeline_lock:
            task = _pipeline_tasks.get(task_id)
            if task is None:
                task = {
                    "kind": "ai_screen", "status": "queued", "progress": {},
                    "logs": [], "result": None, "error": "",
                    "source_task_id": scrape_task_id,
                    "stop_event": threading.Event(),
                }
                _pipeline_tasks[task_id] = task
            if task.get("status") == "cancelled":
                # 排队期间已被用户取消：直接退出，别把 cancelled 覆盖成 running
                return
            task["status"] = "running"
            stop_event = task.get("stop_event")

        def emit(**kw):
            stage = str(kw.get("stage", ""))
            current = int(kw.get("current") or 0)
            total = int(kw.get("total") or 0)
            kw["overall_percent"] = _screen_overall_percent(stage, current, total)
            # 调用方传了具体 message（如"筛选完成：匹配 X 条"）就优先用；没传才回退默认文案
            if not kw.get("message"):
                kw["message"] = _SCREEN_STAGE_MESSAGES.get(stage, "")
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
            with _pipeline_lock:
                t = _pipeline_tasks.get(task_id)
                if t is not None:
                    t["status"] = "cancelled"
                    t["error"] = "用户已停止筛选"
            try:
                store.update_screening_run(task_id, status="cancelled")
            except Exception:
                pass
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
            if not isinstance(source_result, dict) or not source_result.get("ok"):
                raise RuntimeError("invalid_scrape_task")
            raw_jobs = [
                dict(job) for job in source_result.get("jobs", [])
                if isinstance(job, dict)
            ]
            if not raw_jobs:
                raise RuntimeError("empty_scrape_result")

            # 登记任务（进度落库，进程重启可查）；落库失败不阻断筛选
            try:
                store.create_screening_run(
                    task_id,
                    frozen_filters=screening_fields,
                    source_count=len(raw_jobs),
                    execution_params={
                        "scrape_task_id": scrape_task_id,
                        "profile_summary": profile_summary,
                    },
                )
            except Exception:
                pass

            # 载入断点（同一抓取任务、同一筛选条件下的上次进度）
            resume_verdicts = {}
            resume_jd = {}
            if resume_from_run_id:
                try:
                    resume_verdicts = store.load_screening_verdicts(resume_from_run_id)
                except Exception:
                    resume_verdicts = {}
                old_jd_path = _jd_checkpoint_path(
                    app.config["RESULT_DIR"], resume_from_run_id)
                resume_jd = _load_jd_checkpoint(old_jd_path)
                # 旧断点已被本任务继承：删除旧文件（本任务会写自己的断点）
                _remove_jd_checkpoint(old_jd_path)
                if resume_verdicts or resume_jd:
                    emit(stage="resume",
                         message=f"接着上次进度：已有 {len(resume_verdicts)} 条判定、"
                                 f"{len(resume_jd)} 条 JD，跳过重复工作")

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

            screen_result = screen_jobs(raw_jobs, criteria, endpoint, api_key,
                                        model=model, progress=_a_progress)
            if _stop_requested():
                _mark_cancelled()
                return
            kept_ids = set(screen_result["kept"])
            survivors = [j for j in raw_jobs if str(j.get("job_id", "")) in kept_ids]
            dropped = screen_result["dropped"]
            emit(stage="screen_a_done", kept=len(survivors), dropped=len(dropped),
                 message=f"粗筛完成：保留 {len(survivors)} 条，移除 {len(dropped)} 条")
            try:
                store.update_screening_run(task_id, status="running",
                                           source_cursor=0)
            except Exception:
                pass

            # 4) 对保留的岗位分段抓 JD（重开调试浏览器，抓完关闭）。
            # 每段落盘 JD 断点文件 + 更新 source_cursor：进程崩了已抓的也不丢，
            # 重跑（含登录墙后重试）自动跳过已抓岗位。
            enriched = [dict(job) for job in survivors]
            jd_path = _jd_checkpoint_path(app.config["RESULT_DIR"], task_id)
            jd_map = dict(resume_jd)
            if survivors:
                emit(stage="ensure_chrome", message="启动调试浏览器，准备抓取 JD…")
                chrome_ok, chrome_err = ensure_chrome_ready()
                if not chrome_ok:
                    raise RuntimeError(f"chrome_not_ready: {chrome_err}")
                source = _make_cdp_source()
                if source is None:
                    raise RuntimeError("source_unavailable")

                todo_jd = [j for j in survivors
                           if str(j.get("job_id", "")) not in jd_map]
                emit(stage="fetch_jd", current=len(jd_map), total=len(survivors),
                     message=f"抓取 JD（{len(jd_map)}/{len(survivors)}）…")
                DETAIL_CHUNK = 10
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
                        progress=_jd_progress)
                    for j in detail_result["jobs"]:
                        jd = str(j.get("jd", "")).strip()
                        if jd:
                            jd_map[str(j.get("job_id", ""))] = jd
                    _save_jd_checkpoint(jd_path, jd_map)
                    try:
                        store.update_screening_run(task_id, source_cursor=len(jd_map))
                    except Exception:
                        pass
                    emit(stage="fetch_jd",
                         current=min(len(jd_map), len(survivors)), total=len(survivors),
                         message=f"抓取 JD {min(len(jd_map), len(survivors))}/{len(survivors)}")
                    if detail_result.get("login_wall"):
                        close_debug_chrome()
                        # BOSS 登录失效：停+说人话（已抓的已落盘，重试自动续抓）
                        try:
                            store.update_screening_run(
                                task_id, status="failed", error_code="login_wall")
                        except Exception:
                            pass
                        with _pipeline_lock:
                            t = _pipeline_tasks.get(task_id)
                            if t is not None:
                                t["status"] = "failed"
                                t["error"] = (
                                    f"抓取 JD 时 BOSS 登录已失效：已抓 "
                                    f"{len(jd_map)}/{len(survivors)} 条（已保存）。"
                                    "请在 Chrome 重新登录 BOSS直聘后重试，会接着已抓的继续"
                                )
                        _schedule_pipeline_task_cleanup(task_id)
                        return
                    if detail_result.get("stopped"):
                        close_debug_chrome()
                        _mark_cancelled()
                        return
                close_debug_chrome()
            for job in enriched:
                job["jd"] = jd_map.get(str(job.get("job_id", "")), "")

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
                done_verdicts = dict(resume_verdicts)
                todo_match = [j for j in jobs_with_jd
                              if str(j.get("job_id", "")) not in done_verdicts]
                emit(stage="screen_b",
                     current=min(len(done_verdicts), len(jobs_with_jd)),
                     total=len(jobs_with_jd),
                     message="AI 精筛中（JD 对比简历画像）…")
                MATCH_CHUNK = 20
                for chunk_start in range(0, len(todo_match), MATCH_CHUNK):
                    if _stop_requested():
                        _mark_cancelled()
                        return
                    chunk = todo_match[chunk_start:chunk_start + MATCH_CHUNK]
                    match_result = match_jds(chunk, profile_summary, endpoint, api_key,
                                             model=model)
                    done_verdicts.update(match_result["verdicts"])
                    try:
                        store.save_screening_verdicts(task_id, match_result["verdicts"])
                        store.update_screening_run(
                            task_id, processed_count=len(done_verdicts))
                    except Exception:
                        pass  # 落库失败不阻断筛选（内存结果仍在）
                    emit(stage="screen_b",
                         current=min(len(done_verdicts), len(jobs_with_jd)),
                         total=len(jobs_with_jd),
                         message=f"AI 精筛 {min(len(done_verdicts), len(jobs_with_jd))}/{len(jobs_with_jd)}")
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
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    task["result"] = result
                    task["status"] = "done"
            try:
                store.update_screening_run(
                    task_id, status="done",
                    match_count=match_count,
                    mismatch_count=sum(1 for j in enriched if j.get("verdict") == "not_match"),
                    processed_count=len(jobs_with_jd))
            except Exception:
                pass
            _schedule_pipeline_task_cleanup(task_id)
            emit(stage="done", total_matched=match_count,
                 message=f"筛选完成：匹配 {match_count} 条")
            store.save_pipeline_result(result, {"screening": screening_fields})
            # 任务成功：断点文件使命完成（续跑只服务失败/取消/中断）
            _remove_jd_checkpoint(jd_path)
        except ai_service.AISecurityError as exc:
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    task["status"] = "failed"
                    task["error"] = ai_service.user_facing_error(exc.error_code)
            try:
                store.update_screening_run(task_id, status="failed",
                                           error_code=exc.error_code)
            except Exception:
                pass
            _schedule_pipeline_task_cleanup(task_id)
        except Exception as exc:
            with _pipeline_lock:
                task = _pipeline_tasks.get(task_id)
                if task is not None:
                    task["status"] = "failed"
                    task["error"] = f"AI 筛选异常：{type(exc).__name__}"
            try:
                store.update_screening_run(task_id, status="failed",
                                           error_code=type(exc).__name__)
            except Exception:
                pass
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
        from webui.pipeline_exec import load_advanced_settings, _ADVANCED_DEFAULTS
        return jsonify({"ok": True, "settings": load_advanced_settings(),
                        "defaults": _ADVANCED_DEFAULTS})

    @app.route("/api/advanced-settings", methods=["POST"])
    def save_advanced_settings_endpoint():
        from webui.pipeline_exec import save_advanced_settings, _ADVANCED_DEFAULTS
        body = request.get_json(silent=True) or {}
        settings = body.get("settings")
        if not isinstance(settings, dict):
            return jsonify({"ok": False, "error": "缺少 settings 对象"}), 400
        # 只保留合法 key，类型校验
        clean = {}
        for k, default in _ADVANCED_DEFAULTS.items():
            if k in settings:
                val = settings[k]
                if isinstance(default, float):
                    val = float(val)
                elif isinstance(default, int):
                    val = int(val)
                clean[k] = val
        save_advanced_settings(clean)
        from webui.pipeline_exec import load_advanced_settings
        return jsonify({"ok": True, "settings": load_advanced_settings()})

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
        _register_pipeline_task(task_id, "scrape")
        _pipeline_executor.submit(_run_pipeline_task, task_id, script_params)
        return jsonify({"ok": True, "task_id": task_id})

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
        """
        body = request.get_json(silent=True) or {}
        screening_fields = body.get("screening_fields") or {}
        profile_summary = str(body.get("profile_summary") or "")
        scrape_task_id = str(body.get("scrape_task_id") or "").strip()
        if not isinstance(screening_fields, dict):
            return jsonify({"ok": False, "error": "无效的筛选字段"}), 400
        if not scrape_task_id:
            return jsonify({"ok": False, "error": "缺少 scrape_task_id"}), 400
        with _pipeline_lock:
            source_task = _pipeline_tasks.get(scrape_task_id)
            source_snapshot = dict(source_task) if source_task else None
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
        task_id = uuid.uuid4().hex
        # 断点续筛：同一抓取任务 + 同一筛选条件 + 同一画像，且上次
        # failed/cancelled/interrupted → 接着上次进度（已抓 JD / 已筛判定不重复做）。
        resume_from_run_id = ""
        try:
            prev = store.latest_screening_run_for_source(
                scrape_task_id, statuses=("failed", "cancelled", "interrupted"))
            if prev is not None:
                prev_params = prev.get("execution_params") or {}
                same_fields = prev.get("frozen_filters") == screening_fields
                same_profile = str(prev_params.get("profile_summary", "")) == profile_summary
                if same_fields and same_profile:
                    resume_from_run_id = prev["id"]
        except Exception:
            resume_from_run_id = ""
        if resume_from_run_id:
            # 旧 run 标记为已续跑：进度由新 run 接管落库，首页中断提示不再捞到它
            try:
                store.update_screening_run(resume_from_run_id, status="resumed")
            except Exception:
                pass
        _register_pipeline_task(
            task_id, "ai_screen", source_task_id=scrape_task_id,
        )
        _pipeline_executor.submit(
            _run_ai_screen_task, task_id, screening_fields,
            profile_summary, scrape_task_id, resume_from_run_id,
        )
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
            snapshot = {
                "ok": True,
                "kind": task.get("kind", ""),
                "status": task["status"],
                "progress": task["progress"],
                "logs": list(task["logs"][-LOG_TAIL_LINES:]),
                "error": task["error"],
            }
            if task["status"] in ("done", "failed") and task["result"] is not None:
                # 原样返回整个 result：抓取任务含 jobs/计数；
                # AI 筛选任务还含 dropped/verdict/profile_summary 等
                snapshot["result"] = task["result"]
        return jsonify(snapshot)

    @app.route("/api/latest-running-task")
    def latest_running_task():
        """返回最近一个仍在运行（running/queued）的 pipeline 任务。

        用于页面刷新后接回任务：前端 onMounted 调这个接口，有在跑的任务
        就恢复 task_id 和进度快照，重新开始轮询。dict 保序（Py3.7+），
        最后注册的任务排在最后，倒序找第一个非终态的返回。
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
                    })
        # 内存没有：查 DB 里被进程重启打断的筛选。重启后工作线程已死，
        # 不能假装还在跑——如实告诉前端有个可续跑的中断任务。
        try:
            run = store.latest_interrupted_screening_run()
        except Exception:
            run = None
        if run is not None:
            return jsonify({
                "ok": True,
                "has_task": True,
                "task_id": run["id"],
                "kind": "ai_screen",
                "status": "interrupted",
                "progress": {"message": "上次 AI 筛选因服务重启被中断"},
                "logs": [],
                "error": "",
                "resumable": True,
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
            "saved_at": payload.get("saved_at"),
            "script_params": payload.get("script_params", {}),
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
        from webui.pipeline_exec import ensure_chrome_ready

        raw = request.get_json(silent=True) or {}
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

        # 抓到 JD 后回写数据库中匹配的 job 项；
        # spec 要求补抓不重跑 AI、不跨 tab，故只更新 jd，verdict 等其它字段保持不变。
        persisted = False
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
            else:
                app.logger.warning(
                    "pipeline_job_refetch_jd: job_id=%s 在最近一次运行结果中未找到匹配项，未回写",
                    job_id,
                )

        return jsonify({"ok": True, "jd": jd, "job_id": job_id, "persisted": persisted})

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, threaded=True)
