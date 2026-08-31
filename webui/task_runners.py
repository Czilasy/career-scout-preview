#!/usr/bin/env python3
"""TaskRunner 核心与兼容 re-export（031 B6 自本文件按域拆分）。

TaskRunner 负责顺序执行抓取命令并持久化状态与产物；共用助手已迁入
``webui/task_runner_support.py``，工作台运行编排（WorkbenchRunner）已迁入
``webui/workbench_runner.py``。本文件保留 TaskRunner 本体与旧符号的兼容
re-export，旧 import 路径全部可继续使用（契约 module-compatibility.md）。
"""

from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts import boss_cdp_raw as boss
from webui.logging_setup import get_logger
from webui.process_executor import ArtifactSpec, ScraperExecutor, run_with_deadline
from webui.runtime_audit import record_runtime_event
from webui.task_runner_support import (  # noqa: F401  兼容 re-export，见文件头注释
    DEFAULT_STATE_DIR,
    PROJECT_ROOT,
    SCRAPER,
    _classify_risk_control_reason,
    _classify_scrape_block,
    _env,
    _FINE_VERDICTS,
    _has_unlock_signal,
    _iso_epoch_ms,
    _mask_key,
    _MSG_USER_CANCELLED_TASK,
    _optional_positive_int,
    _read_json,
    _request_hostname,
    _resume_dropped_from_verdicts,
    _RISK_CONTROL_REASON_PATTERNS,
    _split_resume_verdicts,
    _StdoutToLogBuffer,
    _task_payload,
    _theme_path,
)

_logger = get_logger(__name__)


class TaskRunner:
    """Run scraper commands sequentially while persisting state and output."""

    def __init__(self, store, result_dir, python_executable, start_tasks=True,
                 execution_mode="subprocess", in_process_timeout=600):
        self.store = store
        self.result_dir = Path(result_dir)
        self.python_executable = str(python_executable)
        self.start_tasks = bool(start_tasks)
        self.execution_mode = execution_mode
        # in-process 模式硬超时（秒），与子进程模式 timeout_seconds=600 对齐
        self.in_process_timeout = max(1.0, float(in_process_timeout))
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
        self.store.append_log(task_id, _MSG_USER_CANCELLED_TASK)
        try:
            return self.store.update_task(task_id, "interrupted", error=_MSG_USER_CANCELLED_TASK)
        except ValueError:
            # in_process 模式下，_execute 的 SearchCancelled 路径可能已抢先
            # 改为 interrupted（cancel_event set → run_search_programmatic 抛
            # SearchCancelled → _run_in_process 返回 interrupted → _execute 改
            # 状态）。此时状态已是终态，返回当前快照而非抛异常。
            return self.store.get_task(task_id)

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
            self.store.append_log(task_id, "任务开始")
            cancel_event = threading.Event()
            with self._process_lock:
                self._cancel_events[task_id] = cancel_event
            if self.execution_mode == "in_process":
                outcome = self._run_in_process(task_id, task, cancel_event)
            else:
                command = self.build_command(task)
                artifacts = []
                if task["kind"] == "scrape":
                    artifacts = [
                        ArtifactSpec(task["output_path"], root=self.result_dir),
                        ArtifactSpec(task["detail_output_path"], root=self.result_dir, required=False),
                    ]
                result = self.process_executor.execute(
                    command, timeout_seconds=600, cwd=PROJECT_ROOT,
                    env=_env(correlation_id=task_id),
                    cancel_event=cancel_event, artifacts=artifacts,
                    on_output=lambda chunk: [
                        self.store.append_log(task_id, line)
                        for line in chunk.splitlines() if line.strip()
                    ],
                )
                outcome = (
                    "succeeded" if result.ok else "failed",
                    result.returncode if result.returncode is not None else -1,
                    result.failure_code or "process_failed",
                    result.output_tail or "",
                )
            # SearchCancelled → interrupted（cancel() 可能已改状态，也可能尚未）
            if outcome[0] == "interrupted":
                try:
                    if self.store.get_task(task_id)["status"] in {"queued", "running"}:
                        self.store.update_task(task_id, "interrupted", error=_MSG_USER_CANCELLED_TASK)
                except ValueError:
                    pass  # cancel() 已抢先改为 interrupted
                return
            if self.store.get_task(task_id)["status"] == "interrupted":
                return
            if outcome[0] == "succeeded":
                self.validate_artifacts(task)
                self.store.append_log(task_id, "任务完成")
                self.store.update_task(task_id, "succeeded", returncode=0)
            else:
                message = f"抓取执行失败: {outcome[2] or 'process_failed'}"
                record_runtime_event(
                    event="task_execution_failed", stage=str(task.get("kind") or "task"),
                    failed_code=str(outcome[2] or "process_failed"),
                    safe_hint=outcome[3], task_id=task_id,
                    correlation_id=task_id, store=self.store,
                    extra={"returncode": outcome[1]},
                )
                self.store.append_log(task_id, message)
                self.store.update_task(
                    task_id, "failed", returncode=outcome[1], error=message,
                )
        except Exception as exc:
            _logger.exception("任务执行兜底异常 task=%s type=%s", task_id, type(exc).__name__)
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

    # ------------------------------------------------------------------
    # in_process 模式执行（合同 inprocess-runner §4.1）
    # ------------------------------------------------------------------

    def _run_in_process(self, task_id, task, cancel_event):
        """in_process 模式执行：调用 boss 库式函数，异常按 §3 映射表冻结。

        带硬超时（``in_process_timeout``，默认 600s，与子进程模式对齐）：
        超时 → 置 cancel_event 请求协作停止，仍不退出则按
        ``process_timeout`` 失败（后台线程自生自灭，调用方已按失败处理）。

        返回 ``(status, returncode, failure_code, output_tail)``；
        ``status`` ∈ ``{"succeeded", "failed", "interrupted"}``。
        """
        try:
            completed, payload = run_with_deadline(
                lambda: self._run_in_process_impl(task_id, task, cancel_event),
                timeout_seconds=self.in_process_timeout,
                cancel_event=cancel_event,
            )
        except boss.SearchCancelled:
            return ("interrupted", -1, None, "")
        except boss.CDPUnavailableError as exc:
            return ("failed", 2, "source_cdp_unavailable", str(exc))
        except boss.LoginRequiredError as exc:
            return ("failed", 1, "source_login_required", str(exc))
        except boss.RequestLimitExceededError as exc:
            return ("failed", 11, "source_request_limit_exceeded", str(exc))
        except boss.RiskControlError as exc:
            failed_code = (
                str(getattr(exc, "code", "") or "")
                or _classify_risk_control_reason(exc.reason)
            )
            return ("failed", 10, failed_code, exc.reason)
        except Exception as exc:
            return ("failed", -1, "process_failed", str(exc))
        if not completed:
            # 与子进程模式 process_executor 的 process_timeout 语义对齐
            return ("failed", -1, "process_timeout", str(payload))
        return payload

    def _run_in_process_impl(self, task_id, task, cancel_event):
        """in-process 实际执行体（在 run_with_deadline 的 worker 线程中）。"""
        if task["kind"] == "setup_chrome":
            returncode = self._run_setup_chrome_in_process(task_id, cancel_event)
            if returncode == 0:
                return ("succeeded", 0, None, "")
            return ("failed", returncode, "process_failed", "")

        search = task["params"]["search"]
        boss.run_search_programmatic(
            keyword=search["keyword"],
            city=search["city"],
            pages=int(search["pages"]),
            cdp_port=boss.DEFAULT_CDP_PORT,
            output_path=task["output_path"],
            detail_output_path=task["detail_output_path"],
            detail=bool(search["detail"]),
            analysis=bool(search.get("analysis")),
            fmt=search.get("format", "json"),
            filters=dict(search.get("filters") or {}),
            on_log=lambda line: self.store.append_log(task_id, line),
            cancel_event=cancel_event,
        )
        return ("succeeded", 0, None, "")

    def _run_setup_chrome_in_process(self, task_id, cancel_event):
        """in_process 模式 setup_chrome：调用 boss.run_setup_chrome 库式函数。

        run_setup_chrome 无 cancel_event/on_log 参数；用线程感知 buffer
        把既有 print 按行转发到 store.append_log，cancel_event 仅在调用
        前后检查（mid-execution 不可中断，setup_chrome 通常短时）。
        """
        if cancel_event.is_set():
            return 1
        buffer = _StdoutToLogBuffer(self.store, task_id)
        with buffer:
            returncode = boss.run_setup_chrome(cdp_port=boss.DEFAULT_CDP_PORT)
        return returncode


def __getattr__(name):
    """兼容 re-export：WorkbenchRunner 延迟解析（031 B6 拆出 workbench_runner）。

    延迟而非模块级 import：workbench_runner 继承本模块的 TaskRunner，
    顶部 import 会与本模块形成循环；延迟到属性访问时两个模块都已就绪。
    """
    if name == "WorkbenchRunner":
        from webui.workbench_runner import WorkbenchRunner
        return WorkbenchRunner
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
