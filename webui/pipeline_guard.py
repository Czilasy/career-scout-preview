"""流水线卡死防护（022-jd-stall-guard，US1-US3）。

独立于任务线程的 daemon 监控：批次登记 + 心跳（任何产出刷新 300s 计时）
→ 无心跳判定卡死 → 杀失联抓取工解出任务线程 → 任务线程侧等 3~5s 自动
重抓该批（每批最多 3 次：原始 1 + 重试 2）→ 第 3 次失败探测环境分流
（环境级 → 暂停 + 报错模块接管 + 断点可续跑；单批偶发 → 跳过进待确认
继续下一批）→ 杀进程后任务线程仍不解出（极端死锁）时兜底暂停并明示
“任务线程失去响应，请重启应用后继续”。全部事件落盘 career-scout.log。

依赖注入：write_run / store / tasks / lock / record_pause_failure /
release_worker_resume_claims 经构造参数注入（由 ctx 提供），本文件不
反向 import app / store / source；环境探测回调（env_probe）由接入方在
begin_batch 时提供（复用 source.preflight 与 ensure_chrome_ready 语义）。
属流水线防护域（宪法 VI 模块地图）。
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from webui.logging_setup import get_logger
from webui.process_executor import ScraperExecutor

#: 兜底暂停写入的暂停码（复用既有 internal 语义，不新建错误码体系）。
FALLBACK_PAUSE_CODE = "internal_error"
#: 兜底暂停的用户可读原因（FR-007）。
FALLBACK_PAUSE_REASON = "任务线程失去响应，请重启应用后继续"
#: 偶发分流标记给该批岗位的失败码（独立失败，非阻断，可进待确认补抓）。
SPORADIC_GIVEUP_CODE = "detail_timeout"


@dataclass
class _BatchState:
    """单个抓取批次的运行状态（guard 内部）。"""

    batch_key: str
    task_id: str = ""
    attempt: int = 1
    begin_ts: float = 0.0
    last_heartbeat: float = 0.0
    stalled: bool = False
    stall_ts: float = 0.0
    attempt_at_stall: int = 0
    terminal: bool = False
    process: Any = None
    env_probe: Callable[[], tuple[bool, str, str]] | None = None
    divert: str | None = None
    stall_code: str = ""
    stall_reason: str = ""
    fallback_paused: bool = False
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


class PipelineGuard:
    """JD 抓取批次卡死防护：判定、解出、重抓编排、分流收场与事件日志。

    监控线程独立于任务线程运行（daemon）：任务线程失联/卡在等待回收时
    监控仍生效。重抓与分流收场的动作在任务线程侧执行（任务线程从
    fetch_details_batch 返回后查询 guard 状态），保证单执行线语义。
    """

    def __init__(
        self,
        *,
        stall_seconds: float = 300,
        poll_seconds: float = 5,
        max_attempts: int = 3,
        retry_delay_range: tuple[float, float] = (3.0, 5.0),
        fallback_seconds: float = 20.0,
        write_run: Callable | None = None,
        store: Any = None,
        tasks: dict | None = None,
        lock: Any = None,
        record_pause_failure: Callable | None = None,
        release_worker_resume_claims: Callable | None = None,
        logger: Any = None,
    ):
        self._stall_seconds = max(0.05, float(stall_seconds))
        self._poll_seconds = max(0.02, float(poll_seconds))
        self._max_attempts = max(1, int(max_attempts))
        self._retry_min, self._retry_max = (
            max(0.0, float(retry_delay_range[0])),
            max(0.0, float(retry_delay_range[1])),
        )
        if self._retry_max < self._retry_min:
            self._retry_max = self._retry_min
        self._fallback_seconds = max(0.05, float(fallback_seconds))
        self._write_run = write_run
        self._store = store
        self._tasks = tasks
        self._lock = lock
        self._record_pause_failure = record_pause_failure
        self._release_worker_resume_claims = release_worker_resume_claims
        self._logger = logger or get_logger("pipeline_guard")
        self._batches: dict[str, _BatchState] = {}
        self._mutex = threading.RLock()
        self._closed = threading.Event()
        self._monitor = threading.Thread(
            target=self._monitor_loop, name="pipeline-guard", daemon=True,
        )
        self._monitor.start()

    # ------------------------------------------------------------------
    # 批次生命周期（任务线程调用）
    # ------------------------------------------------------------------

    def begin_batch(
        self,
        batch_key: str,
        *,
        task_id: str = "",
        attempt: int = 1,
        env_probe: Callable[[], tuple[bool, str, str]] | None = None,
    ) -> None:
        """登记一批的开始：重置心跳与卡死标记，开启新的一次尝试计时。

        ``env_probe``：第 max_attempts 次仍卡死时的环境探测回调，返回
        ``(ok, code, reason)``；None 视为探测通过（偶发分流）。
        """
        with self._mutex:
            prev = self._batches.get(batch_key)
            state = _BatchState(
                batch_key=batch_key,
                task_id=str(task_id or ""),
                attempt=max(1, int(attempt)),
                env_probe=env_probe,
            )
            state.begin_ts = time.monotonic()
            state.last_heartbeat = state.begin_ts
            self._batches[batch_key] = state
        if prev is not None and prev.stalled and state.attempt > prev.attempt:
            self._log_event("retry", state, result=f"attempt_{state.attempt}")

    def touch(self, batch_key: str) -> None:
        """刷新心跳：批次进行中任何产出（子进程 stdout、批返回等）调用。"""
        with self._mutex:
            state = self._batches.get(batch_key)
            if state is None or state.terminal:
                return
            state.last_heartbeat = time.monotonic()

    def complete_batch(self, batch_key: str) -> None:
        """标记批次正常完成（不再受监控）。"""
        with self._mutex:
            state = self._batches.get(batch_key)
            if state is None:
                return
            state.terminal = True

    def spawn_hook(self, batch_key: str) -> Callable[[Any], None]:
        """返回一个回调，供 ScraperExecutor.on_spawn 登记当前批次的子进程。

        判定卡死后 guard 用 taskkill /T /F 终止该进程，解出任务线程的
        poll 等待。
        """

        def _hook(process: Any) -> None:
            with self._mutex:
                state = self._batches.get(batch_key)
                if state is None:
                    return
                state.process = process

        return _hook

    # ------------------------------------------------------------------
    # 任务线程侧状态查询（批返回后编排用）
    # ------------------------------------------------------------------

    def batch_state(self, batch_key: str) -> dict | None:
        """返回批次状态的只读快照（dict），无批次时返回 None。"""
        with self._mutex:
            state = self._batches.get(batch_key)
            if state is None:
                return None
            return {k: v for k, v in state.__dict__.items() if not k.startswith("_")}

    def is_stalled(self, batch_key: str) -> bool:
        return bool((self.batch_state(batch_key) or {}).get("stalled"))

    def should_retry(self, batch_key: str) -> bool:
        """卡死且尝试次数未达上限：任务线程应等 3~5s 后重抓该批。"""
        st = self.batch_state(batch_key)
        return bool(
            st and st["stalled"] and not st["terminal"]
            and st["attempt"] < self._max_attempts
        )

    def should_giveup(self, batch_key: str) -> bool:
        """第 max_attempts 次卡死且已分流：任务线程应收场（暂停/跳过）。"""
        st = self.batch_state(batch_key)
        return bool(st and st["stalled"] and st["terminal"] and st["divert"])

    def divert_result(self, batch_key: str) -> str | None:
        """分流结果："environment" | "sporadic" | None。"""
        return (self.batch_state(batch_key) or {}).get("divert")

    def stall_code(self, batch_key: str) -> str:
        """环境级分流时的失败码（source_cdp_unavailable 等）。"""
        return str((self.batch_state(batch_key) or {}).get("stall_code") or "")

    def next_retry_delay(self) -> float:
        """重抓前等待时长（3~5s，可配置）。"""
        return random.uniform(self._retry_min, self._retry_max)

    def immediate_stop_task(self, task_id: str) -> None:
        """立即停止：终止该任务全部活动批次的子进程并清理批次登记（025 B076）。

        供暂停 API immediate 模式与取消路径调用；判定/监控逻辑零改动。
        终止后任务线程从 poll 等待解出；批次登记清空避免「继续」后被旧登记
        误判卡死触发额外重抓。
        """
        with self._mutex:
            keys = [k for k, s in self._batches.items()
                    if str(s.task_id) == str(task_id) and not s.terminal]
        for key in keys:
            self._kill_process(self.batch_state(key) or {})
        with self._mutex:
            for key in keys:
                state = self._batches.get(key)
                if state is not None:
                    state.terminal = True
            for key in list(self._batches):
                if str(self._batches[key].task_id) == str(task_id):
                    del self._batches[key]

    # ------------------------------------------------------------------
    # 独立监控
    # ------------------------------------------------------------------

    def _monitor_loop(self) -> None:
        while not self._closed.is_set():
            self._closed.wait(self._poll_seconds)
            if self._closed.is_set():
                break
            try:
                self.scan_once()
            except Exception:
                self._logger.exception("pipeline_guard scan 异常（已忽略，下轮继续）")

    def scan_once(self) -> None:
        """扫描一次全部活动批次（监控线程每 poll_seconds 调一次；测试可同步调）。"""
        now = time.monotonic()
        with self._mutex:
            snapshots = [dict(s.__dict__) for s in self._batches.values()]
        for st in snapshots:
            if st["terminal"]:
                continue
            if now - st["last_heartbeat"] < self._stall_seconds:
                continue
            if not st["stalled"]:
                self._mark_stalled(st, now)
            else:
                self._maybe_fallback_pause(st, now)

    def _mark_stalled(self, st: dict, now: float) -> None:
        with self._mutex:
            state = self._batches.get(st["batch_key"])
            if state is None or state.terminal:
                return
            state.stalled = True
            state.stall_ts = now
            state.attempt_at_stall = state.attempt
        if st["attempt"] >= self._max_attempts:
            self._divert(st)
        else:
            self._log_event("stall", st, result="kill_worker")
            self._kill_process(st)

    def _divert(self, st: dict) -> None:
        """第 max_attempts 次仍卡死：探测环境并按结果分流收场。"""
        probe = st["env_probe"]
        env_ok, code, reason = True, "", ""
        if probe is not None:
            try:
                env_ok, code, reason = probe()
            except Exception as exc:
                env_ok = False
                code = "internal_error"
                reason = f"环境探测异常：{type(exc).__name__}"
        with self._mutex:
            state = self._batches.get(st["batch_key"])
            if state is None:
                return
            state.divert = "sporadic" if env_ok else "environment"
            state.stall_code = "" if env_ok else str(code or "internal_error")
            state.stall_reason = str(reason or "")
            state.terminal = True
        self._log_event(
            "giveup", st, result=state.divert,
            extra={"code": state.stall_code, "reason": state.stall_reason},
        )
        # 第 3 次也杀失联抓取工：任务线程若只是卡在 poll 等待则被解出，
        # 随后读取分流结果收场；若真死锁则由兜底暂停。
        self._kill_process(st)

    def _maybe_fallback_pause(self, st: dict, now: float) -> None:
        """卡死判定后任务线程迟迟不处理（未重抓也未完成）：真死锁，兜底暂停。"""
        if st["attempt"] != st["attempt_at_stall"]:
            return
        if now - st["stall_ts"] < self._fallback_seconds:
            return
        with self._mutex:
            state = self._batches.get(st["batch_key"])
            if state is None or state.terminal:
                return
            state.fallback_paused = True
            state.terminal = True
        self._log_event("fallback", st, result="pause_thread_unresponsive")
        self._pause_task(st, FALLBACK_PAUSE_REASON, code=FALLBACK_PAUSE_CODE)

    # ------------------------------------------------------------------
    # 失联清理与暂停落库
    # ------------------------------------------------------------------

    def _kill_process(self, st: dict) -> None:
        with self._mutex:
            state = self._batches.get(st["batch_key"])
            proc = state.process if state is not None else None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                ScraperExecutor._terminate_tree(proc)
        except Exception:
            self._logger.exception("pipeline_guard 终止子进程异常（已忽略）")

    def _pause_task(self, st: dict, reason: str, *, code: str) -> None:
        """把任务标记 paused + 明确原因（兜底路径：不关浏览器、断点保留）。"""
        task_id = st["task_id"]
        if not task_id:
            return
        if self._write_run is not None:
            try:
                self._write_run(
                    task_id, status="paused", error_code=code,
                    current_stage="jd_detail", error_reason=reason,
                )
            except Exception:
                self._logger.exception("pipeline_guard 写暂停状态失败")
        if self._store is not None:
            try:
                self._store.append_task_event(
                    task_id, "pause",
                    {"stage": "jd_detail", "code": code, "reason": reason},
                )
            except Exception:
                pass
        if self._record_pause_failure is not None:
            try:
                self._record_pause_failure(task_id, "jd_detail", code, reason)
            except Exception:
                pass
        if self._tasks is not None and self._lock is not None:
            try:
                with self._lock:
                    task = self._tasks.get(task_id)
                    if task is not None:
                        task["status"] = "paused"
                        task["error"] = reason
            except Exception:
                pass
        if self._release_worker_resume_claims is not None:
            try:
                task = self._tasks.get(task_id) if self._tasks is not None else None
                self._release_worker_resume_claims(task)
            except Exception:
                pass

    def _log_event(self, event: str, st: dict | _BatchState, *, result: str = "", extra: dict | None = None) -> None:
        try:
            if not isinstance(st, dict):
                st = vars(st)
            line = (
                f"{event} batch={st['batch_key']} task={st['task_id']} "
                f"attempt={st['attempt']} result={result}"
            )
            if extra:
                line += " " + " ".join(
                    f"{k}={v}" for k, v in extra.items() if v not in (None, "")
                )
            self._logger.info(line)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------

    def close(self) -> None:
        """停止监控线程（应用退出/测试收尾）。"""
        self._closed.set()
