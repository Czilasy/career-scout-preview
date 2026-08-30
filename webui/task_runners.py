#!/usr/bin/env python3
"""Task runner and app helper code extracted from webui.app.

Keeps the Flask factory focused on route assembly; scraper execution,
workbench orchestration and small parsing helpers live here.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import boss_cdp_raw as boss
from scripts.boss_cdp_signals import parse_failure_line
from webui import ai as ai_service
from webui.error_registry import resolve_code
from webui.constants import CLEANUP_EXPIRED_DAYS
from webui.process_executor import ArtifactSpec, ScraperExecutor, run_with_deadline
from webui.runtime_audit import record_runtime_event
from webui.workbench import (
    MAX_DETAIL_BUDGET,
    allocate_detail_budget,
    normalize_job_link,
)
from webui.logging_setup import get_logger

_logger = get_logger(__name__)


_MSG_USER_CANCELLED_TASK = "用户取消任务"


def _has_unlock_signal(text: str) -> bool:
    """高置信解封时间信号：完整未来时间点或明确解封/解锁时间文案。"""
    if not text:
        return False
    try:
        if boss.parse_unlock_time(text) is not None:
            return True
    except Exception:
        _logger.debug("解封时间解析失败，回退关键词判定", exc_info=True)

    lowered = str(text).lower()
    return any(kw in lowered for kw in ("解封时间", "解封后", "解封于", "解锁时间"))


def _classify_scrape_block(err_msg: str) -> str:
    """hard_stop_code 缺失时的兜底：只解析结构化失败行，不再全文猜码。

    016-error-module-rework：run_search 硬停时总携带 hard_stop_code；
    本函数仅防御性兜底，输出全文关键词扫描路径已删除（岗位文案里的
    "429/滑块"等词曾把软失败误判成限流硬停）。
    """
    if not err_msg:
        return ""
    parsed = parse_failure_line(err_msg)
    if parsed is not None:
        return resolve_code(parsed[0])
    return ""


# 风控异常 reason → 安全失败码（合同 inprocess-runner §3 的防御性兜底）。
# 016：RiskControlError 自带 code 后本表仅在异常对象缺码时使用；
# 顺序敏感：限流优先于验证码，避免"频繁 + 滑块"文案被误判为验证码。
_RISK_CONTROL_REASON_PATTERNS = (
    ("source_login_required", (
        "登录已失效", "登录过期", "未登录", "登 录 失效", "登 录 已失效", "请先登录", "wt2", "401", "login expired",
    )),
    ("source_rate_limited", (
        "操作频繁", "频繁访问", "访问频繁", "稍后再试", "访问受限", "异常流量", "账号受限", "限流",
        "rate limit", "too many", "429", "http 403", "http 412", "http 418",
        "403 forbidden", "412 precondition", "418 im a teapot",
    )),
    ("source_verification_required", (
        "验证码", "滑块", "滑动验证", "captcha", "slider", "geetest",
    )),
)


def _classify_risk_control_reason(reason: str) -> str:
    """把 RiskControlError.reason 文本映射到安全失败码；未命中返回 source_unknown_error。

    用于 in_process 模式异常映射（合同 §3 表 RiskControlError 行）。
    子进程模式按退出码 10 单独分类，不走本函数。
    """
    if not reason:
        return "source_unknown_error"
    if _has_unlock_signal(reason):
        return "source_rate_limited"
    text = str(reason).lower()
    for code, keywords in _RISK_CONTROL_REASON_PATTERNS:
        for kw in keywords:
            if kw.lower() in text:
                return code
    return "source_unknown_error"


class _StdoutToLogBuffer(boss._ThreadAwareStdout):
    """捕获任务线程 print 输出并按行转发到 store.append_log（合同 §2.2）。

    供 setup_chrome 等「无 on_log 参数的库式函数」使用：以 buffer 自身
    作上下文管理器（带守卫恢复）把既有 print 按行转发，不修改既有
    print 语句；其他线程的输出转发回真 stdout，避免日志串线。
    行格式与子进程模式 stdout 完全一致。
    """

    def __init__(self, store, task_id):
        super().__init__()
        self._store = store
        self._task_id = task_id
        self._buf = []

    def write(self, text):
        if not text:
            return 0
        if threading.get_ident() != self._tid:
            if self._fallback is not None:
                try:
                    self._fallback.write(text)
                except Exception:
                    _logger.debug("降级输出通道写入失败（忽略）", exc_info=True)

            return len(text)
        parts = text.splitlines(keepends=True)
        for part in parts:
            self._buf.append(part)
            if part.endswith("\n") or part.endswith("\r"):
                line = "".join(self._buf).rstrip("\r\n")
                if line.strip():
                    self._store.append_log(self._task_id, line)
                self._buf = []
        return len(text)

    def flush(self):
        if threading.get_ident() != self._tid:
            super().flush()
            return
        if self._buf:
            line = "".join(self._buf).rstrip("\r\n")
            if line.strip():
                self._store.append_log(self._task_id, line)
            self._buf = []


SCRAPER = PROJECT_ROOT / "scripts" / "boss_cdp_raw.py"
DEFAULT_STATE_DIR = Path(
    os.environ.get("CAREER_SCOUT_STATE_DIR")
    or os.environ.get("BOSS_WEBUI_STATE_DIR")
    or os.path.expanduser("~/.career-scout/webui")
)


def _theme_path() -> Path:
    """主题偏好文件：与登录态/冷却等同级放 ~/.career-scout/theme.json。

    不用 DEFAULT_STATE_DIR（webui 子目录）：主题属于用户偏好，与桌面窗口
    状态（desktop_window.json）同级，便于用户直接查看与备份。
    """
    return Path(os.path.expanduser("~/.career-scout")) / "theme.json"


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


def _env(correlation_id: str = ""):
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    if correlation_id:
        environment["CAREER_SCOUT_CORRELATION_ID"] = str(correlation_id)
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

                def stream_progress(query=query, seen_detail_ids=seen_detail_ids):
                    persisted = self._stream_new_details(run_id, query, seen_detail_ids)
                    if persisted:
                        current = self.store.get_search_run(run_id)
                        self.store.update_search_run(
                            run_id, completed_jd_count=current["completed_jd_count"] + persisted,
                        )
                if self.execution_mode == "in_process":
                    query_outcome = self._run_query_in_process(
                        run_id, query, cancel_event, stream_progress,
                    )
                    if query_outcome[0] != "succeeded":
                        raise ValueError(query_outcome[2] or "抓取器执行失败")
                else:
                    result = self.process_executor.execute(
                        self._query_command(query), timeout_seconds=600,
                        cwd=PROJECT_ROOT, env=_env(correlation_id=run_id),
                        cancel_event=cancel_event,
                        on_poll=stream_progress,
                        artifacts=[
                            ArtifactSpec(query["list_output_path"], root=self.result_dir),
                            ArtifactSpec(query["detail_output_path"], root=self.result_dir, required=False),
                        ],
                    )
                    if not result.ok:
                        raise ValueError(result.failure_code or "抓取器执行失败")
                jobs, _ = self._read_query_artifacts(run_id, query)
                persisted = self._stream_new_details(run_id, query, seen_detail_ids)
                self.store.update_run_query(query["id"], status="succeeded", counts={"completed_jd": persisted})
                current = self.store.get_search_run(run_id)
                self.store.update_search_run(
                    run_id,
                    discovered_count=current["discovered_count"] + len(jobs),
                    completed_jd_count=current["completed_jd_count"] + persisted,
                )
            except (OSError, ValueError):
                self.store.update_run_query(query["id"], status="failed", error_code="scrape_failed")
            finally:
                with self._process_lock:
                    self._processes.pop(run_id, None)
                    self._cancel_events.pop(run_id, None)
        if self.store.get_search_run(run_id)["status"] != "interrupted":
            self._finalize_run(run_id)
        self.store.cleanup_expired_jobs(days=CLEANUP_EXPIRED_DAYS)

    def _run_query_in_process(self, run_id, query, cancel_event, stream_progress):
        """in_process 模式执行单个 child query（合同 inprocess-runner §4.2）。

        把 ``_query_command`` 产出的 argv 翻译为 ``run_search_programmatic``
        直传参数；``on_poll`` 透传以保留增量入库语义；异常按 §3 映射表冻结。
        带硬超时（``in_process_timeout``），超时 → 协作取消 → 仍不退出
        则按 ``process_timeout`` 失败（与子进程模式语义对齐）。

        返回 ``(status, returncode, failure_code, output_tail)``；
        ``status`` ∈ ``{"succeeded", "failed", "interrupted"}``。
        """
        try:
            if cancel_event.is_set():
                return ("interrupted", -1, None, "")
            completed, payload = run_with_deadline(
                lambda: self._run_query_in_process_impl(
                    run_id, query, cancel_event, stream_progress,
                ),
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
            return ("failed", 10, str(getattr(exc, "code", "") or "")
                    or _classify_risk_control_reason(exc.reason), exc.reason)
        except Exception as exc:
            return ("failed", -1, "process_failed", str(exc))
        if not completed:
            return ("failed", -1, "process_timeout", str(payload))
        return payload

    def _run_query_in_process_impl(self, run_id, query, cancel_event, stream_progress):
        """in-process 实际执行体（在 run_with_deadline 的 worker 线程中）。"""
        frozen = query["frozen_query"]
        boss.run_search_programmatic(
            keyword=str(frozen["keyword"]),
            city=str(frozen["city"]),
            pages=1,
            cdp_port=boss.DEFAULT_CDP_PORT,
            output_path=query["list_output_path"],
            detail_output_path=query["detail_output_path"],
            detail=True,
            max_details=int(query["detail_budget"]),
            filters=dict(frozen.get("filters") or {}),
            on_log=lambda line: self.store.append_log(run_id, line),
            on_poll=stream_progress,
            cancel_event=cancel_event,
        )
        return ("succeeded", 0, None, "")

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
