"""Bounded, cancellable subprocess execution for scraper commands."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


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
    """Execute one command with deadline, cancellation and bounded output."""

    def __init__(self, *, max_output_bytes: int = 1_000_000, poll_seconds: float = 0.05):
        self.max_output_bytes = max(1, int(max_output_bytes))
        self.poll_seconds = max(0.01, float(poll_seconds))

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
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": False,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(command, **popen_kwargs)
        except (FileNotFoundError, OSError):
            return ExecutionResult(None, "", "process_unreachable")

        output = bytearray()
        output_limit = threading.Event()

        def drain() -> None:
            stream = process.stdout
            if stream is None:
                return
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                remaining = self.max_output_bytes - len(output)
                accepted = chunk[:max(0, remaining)]
                if on_output and accepted:
                    on_output(accepted.decode("utf-8", errors="replace"))
                if remaining > 0:
                    output.extend(accepted)
                if len(chunk) > remaining:
                    output_limit.set()
                    return

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
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
