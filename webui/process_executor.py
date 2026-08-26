"""Bounded, cancellable subprocess execution for scraper commands."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Iterable


@dataclass(frozen=True)
class ArtifactSpec:
    path: Path | str
    root: Path | str
    required: bool = True
    max_bytes: int = 20_000_000


@dataclass(frozen=True)
class ExecutionResult:
    returncode: int | None
    output_tail: str
    failure_code: str | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.failure_code is None


class ScraperExecutor:
    """Execute one command with deadline, cancellation and bounded output.

    ``on_spawn`` / ``on_output_probe`` 是可选的实例级钩子（022 卡死防护用）：
    - ``on_spawn(process)``：Popen 成功后回调，供防护登记子进程句柄以便
      判定卡死后 taskkill 解出任务线程；
    - ``on_output_probe(text)``：drain 线程收到子进程 stdout 输出时回调，
      作为“有产出刷新心跳”的信号源；execute 显式传 ``on_output`` 时优先
      用显式回调，否则落到实例级探针。
    """

    def __init__(self, *, max_output_bytes: int = 1_000_000, poll_seconds: float = 0.05,
                 on_spawn: Callable[[subprocess.Popen], None] | None = None,
                 on_output_probe: Callable[[str], None] | None = None):
        self.max_output_bytes = max(1, int(max_output_bytes))
        self.poll_seconds = max(0.01, float(poll_seconds))
        self.on_spawn = on_spawn
        self.on_output_probe = on_output_probe

    def execute(
        self,
        command: list[str],
        *,
        timeout_seconds: float,
        artifacts: Iterable[ArtifactSpec] = (),
        cancel_event: threading.Event | None = None,
        cwd: Path | str | None = None,
        env: dict | None = None,
        on_output: Callable[[str], None] | None = None,
        on_poll: Callable[[], None] | None = None,
    ) -> ExecutionResult:
        """Run one argv-only command and return a bounded, typed result.

        The deadline is mandatory. Cancellation and output overflow terminate
        the process tree. Artifact checks run only after a zero exit status.
        """
        if not command or not all(isinstance(part, str) and part for part in command):
            return ExecutionResult(None, "", "process_command_invalid")
        if timeout_seconds <= 0:
            return ExecutionResult(None, "", "process_timeout")

        popen_kwargs = {
            "cwd": os.fspath(cwd) if cwd is not None else None,
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": False,
        }
        if os.name == "nt":
            # 桌面壳是无控制台窗口程序，不带 CREATE_NO_WINDOW 时
            # 抓取子进程会各自弹出一个空白控制台窗口（开始/结束各闪一次）。
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        else:
            popen_kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(command, **popen_kwargs)
        except OSError:
            return ExecutionResult(None, "", "process_unreachable")
        if self.on_spawn is not None:
            try:
                self.on_spawn(process)
            except Exception:
                pass

        output = bytearray()
        output_limit = threading.Event()

        def drain() -> None:
            stream = process.stdout
            if stream is None:
                return
            stream_callback = on_output or self.on_output_probe
            try:
                while True:
                    # 逐行读取而非 read(n)：Windows pipe 上 read(n) 会阻塞到
                    # 填满 n 字节或子进程退出（EOF），子进程即使实时输出也
                    # 收不到 → 卡死防护心跳（on_output_probe）失效，正常批次
                    # 会被 300s 误判卡死强杀。readline 在每行换行后立即返回，
                    # 配合子进程 PYTHONUNBUFFERED 即可实时刷新心跳。
                    # limit 65536 兜底超长行（无换行输出），防内存膨胀。
                    line = stream.readline(65536)
                    if not line:
                        return
                    remaining = self.max_output_bytes - len(output)
                    if remaining > 0:
                        accepted = line[:remaining]
                        if stream_callback and accepted:
                            stream_callback(accepted.decode("utf-8", errors="replace"))
                        output.extend(accepted)
                        if len(line) > remaining:
                            output_limit.set()
                            return
                    else:
                        output_limit.set()
                        return
            finally:
                stream.close()

        reader = threading.Thread(target=drain, name="scraper-output", daemon=True)
        reader.start()
        deadline = time.monotonic() + float(timeout_seconds)
        failure_code = None
        while process.poll() is None:
            if on_poll:
                on_poll()
            if cancel_event is not None and cancel_event.is_set():
                failure_code = "process_cancelled"
                break
            if output_limit.is_set():
                failure_code = "process_output_limit"
                break
            if time.monotonic() >= deadline:
                failure_code = "process_timeout"
                break
            time.sleep(self.poll_seconds)

        if failure_code:
            self._terminate_tree(process)
        reader.join(timeout=1)
        if failure_code is None and output_limit.is_set():
            failure_code = "process_output_limit"
        returncode = process.poll()
        tail = bytes(output[-self.max_output_bytes:]).decode("utf-8", errors="replace")
        if failure_code:
            return ExecutionResult(returncode, tail, failure_code)
        if returncode != 0:
            return ExecutionResult(returncode, tail, "process_failed")
        artifact_failure = self._validate_artifacts(artifacts)
        return ExecutionResult(returncode, tail, artifact_failure)

    @staticmethod
    def _terminate_tree(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=5, check=False,
                )
            except (subprocess.TimeoutExpired, OSError):
                process.kill()
        else:
            try:
                os.killpg(process.pid, 15)
            except ProcessLookupError:
                return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    @staticmethod
    def _validate_artifacts(artifacts: Iterable[ArtifactSpec]) -> str | None:
        for spec in artifacts:
            path = Path(spec.path).resolve()
            root = Path(spec.root).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                return "artifact_path_invalid"
            if not path.is_file():
                if spec.required:
                    return "artifact_missing"
                continue
            try:
                if path.stat().st_size > int(spec.max_bytes):
                    return "artifact_too_large"
            except OSError:
                return "artifact_unreadable"
        return None


def run_with_deadline(fn, *, timeout_seconds, cancel_event=None, grace_seconds=30.0):
    """在 worker 线程中执行阻塞调用，带硬截止时间（in-process 模式专用）。

    Python 线程无法强杀：超时后先 set cancel_event 请求协作停止（抓取代码
    在检查点抛 SearchCancelled），再留 grace_seconds 收尾；仍不退出则放弃
    等待（后台线程自生自灭，调用方已按失败处理）。

    返回 ``(completed, payload)``：

    - completed=True：payload 为 fn() 的返回值；fn 抛出的异常会原样
      重新抛出（调用方按既有异常映射处理，与同步调用语义一致）；
    - completed=False：payload 为 TimeoutError，表示超过截止时间
      （无论协作停止是否成功）。
    """
    box: dict = {}

    def _worker() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:
            box["error"] = exc

    worker = threading.Thread(target=_worker, name="in-process-deadline", daemon=True)
    worker.start()
    worker.join(timeout=max(0.1, float(timeout_seconds)))
    if not worker.is_alive():
        if "error" in box:
            raise box["error"]
        return True, box.get("value")
    if cancel_event is not None:
        cancel_event.set()
    worker.join(timeout=max(0.1, float(grace_seconds)))
    if worker.is_alive():
        return False, TimeoutError(
            f"in-process 执行超过 {timeout_seconds}s，协作停止失败，后台线程仍在运行"
        )
    return False, TimeoutError(f"in-process 执行超过 {timeout_seconds}s")
