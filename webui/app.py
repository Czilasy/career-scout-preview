#!/usr/bin/env python3
"""Local Flask API for persistent BOSS scraping and explainable job ranking."""

from __future__ import annotations

import csv
import io
import json
import os
import secrets
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
from webui import resume as resume_service
from webui import ai as ai_service


SCRAPER = PROJECT_ROOT / "scripts" / "boss_cdp_raw.py"
DEFAULT_STATE_DIR = Path(os.path.expanduser("~/.career-scout/webui"))


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


class TaskRunner:
    """Run scraper commands sequentially while persisting state and output."""

    def __init__(self, store, result_dir, python_executable, start_tasks=True):
        self.store = store
        self.result_dir = Path(result_dir)
        self.python_executable = str(python_executable)
        self.start_tasks = bool(start_tasks)
        self._processes = {}
        self._process_lock = threading.Lock()
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
        process = None
        try:
            if self.store.get_task(task_id)["status"] != "queued":
                return
            self.store.update_task(task_id, "running")
            task = self.store.get_task(task_id)
            command = self.build_command(task)
            self.store.append_log(task_id, "任务开始")
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=_env(),
            )
            with self._process_lock:
                self._processes[task_id] = process
            if self.store.get_task(task_id)["status"] == "interrupted":
                process.terminate()
            for line in process.stdout or []:
                self.store.append_log(task_id, line.rstrip())
            returncode = process.wait()
            if self.store.get_task(task_id)["status"] == "interrupted":
                return
            if returncode == 0:
                self.validate_artifacts(task)
                self.store.append_log(task_id, "任务完成")
                self.store.update_task(task_id, "succeeded", returncode=0)
            else:
                message = f"子进程返回码 {returncode}"
                self.store.append_log(task_id, message)
                self.store.update_task(task_id, "failed", returncode=returncode, error=message)
        except Exception as exc:
            try:
                self.store.append_log(task_id, f"任务失败：{exc}")
                self.store.update_task(task_id, "failed", returncode=-1, error=str(exc))
            except (KeyError, ValueError):
                pass
        finally:
            with self._process_lock:
                self._processes.pop(task_id, None)


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
                        settings["endpoint_url"], api_key,
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
                process = subprocess.Popen(
                    self._query_command(query), cwd=PROJECT_ROOT,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding="utf-8", errors="replace", env=_env(),
                )
                with self._process_lock:
                    self._processes[run_id] = process
                seen_detail_ids = set()
                while getattr(process, "poll", lambda: 0)() is None:
                    persisted = self._stream_new_details(run_id, query, seen_detail_ids)
                    if persisted:
                        current = self.store.get_search_run(run_id)
                        self.store.update_search_run(
                            run_id, completed_jd_count=current["completed_jd_count"] + persisted,
                        )
                    time.sleep(0.5)
                if process.wait() != 0:
                    raise ValueError("抓取器执行失败")
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
        if process is not None:
            process.terminate()
        return self.store.update_search_run(run_id, status="interrupted")


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        RESULT_DIR=str(boss.DEFAULT_RESULT_DIR),
        DB_PATH=str(DEFAULT_STATE_DIR / "webui.db"),
        PYTHON_EXECUTABLE=os.environ.get("BOSS_PYTHON", sys.executable),
        START_TASKS=True,
        API_TOKEN=secrets.token_urlsafe(24),
        TRUSTED_HOSTS=["127.0.0.1", "localhost", "::1"],
        RESUME_DIR=str(DEFAULT_STATE_DIR / "resumes"),
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
            supplied = request.headers.get("X-Boss-Token", "")
            if not secrets.compare_digest(supplied, app.config["API_TOKEN"]):
                return jsonify({"error": "缺少有效的本地会话令牌"}), 403

    @app.errorhandler(ValueError)
    def handle_value_error(error):
        return jsonify({"error": str(error)}), 400

    @app.errorhandler(KeyError)
    def handle_key_error(error):
        return jsonify({"error": f"任务不存在: {error.args[0]}"}), 404

    @app.route("/")
    def index():
        return send_from_directory(HERE, "index.html")

    @app.route("/v2")
    def index_v2():
        # 备用页面：极简高对比版前端，不替换原 index.html
        return send_from_directory(HERE, "index-v2.html")

    @app.route("/api/options")
    def options():
        cities = [{"label": name, "value": name} for name in boss.CITY_MAP]
        return jsonify({"filters": build_filter_options(), "cities": cities})

    @app.route("/api/session")
    def session():
        return jsonify({"token": app.config["API_TOKEN"]})

    @app.route("/api/check")
    def check():
        try:
            completed = subprocess.run(
                [app.config["PYTHON_EXECUTABLE"], str(SCRAPER), "--check"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                env=_env(),
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            return jsonify({
                "connected": completed.returncode == 0,
                "returncode": completed.returncode,
                "output": output,
            })
        except subprocess.TimeoutExpired:
            return jsonify({"connected": False, "returncode": -1, "output": "环境检查超时"})

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
            return jsonify(store.get_ai_settings())
        raw = request.get_json(silent=True) or {}
        endpoint_url = str(raw.get("endpoint_url") or "").strip()
        api_key = str(raw.get("api_key") or "").strip()
        if not endpoint_url or not api_key:
            raise ValueError("endpoint_url 和 api_key 不能为空")
        credential_ref = ai_service.store_api_key(endpoint_url, api_key)
        settings = store.save_ai_settings(
            endpoint_url, credential_ref, status="unconfigured",
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
        ok, error_code = ai_service.test_connection(endpoint_url, api_key)
        new_status = "ready" if ok else "failed"
        store.update_ai_status(new_status, last_error_code=error_code)
        return jsonify({"ok": ok, "error_code": error_code})

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

    @app.route("/api/profiles/<profile_id>", methods=["GET", "PATCH"])
    def profile_detail(profile_id):
        if request.method == "GET":
            return jsonify(store.get_profile(profile_id))
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
                        settings["endpoint_url"], api_key,
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
        return jsonify({
            "resume_id": record["id"],
            "profile_id": profile_id,
            "ai_suggestion": ai_suggestion,
            "merged_fields": merged,
            "privacy_notice": "如勾选 AI 解析，简历文本会发送至你配置的 AI 服务；不会写入日志或接口响应。",
        })

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
                        settings["endpoint_url"], api_key,
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

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, threaded=True)
