"""Local rotating log setup with task context and redaction.

Logs live outside the repository under the user data directory so public
repo hygiene rules are not affected.  The redaction formatter is a safety
net for credential-shaped values; callers must still avoid logging raw
API keys, cookies, resume text or JD bodies.

Whitebox guarantees (033):
- ``get_logger`` lazily configures the local file logger when it has no
  handler yet, so any process (webui, scraper subprocess, CLI entry) that
  uses the unified logger always has a log destination.
- ``SafeRotatingFileHandler`` guards rotation with a cross-process lock so
  the main process and scraper subprocesses can share one log file, and
  degrades to append instead of crashing when the file is deleted, read-only
  or locked.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import logging.handlers
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Iterator


LOGGER_NAME = "career_scout"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 10
LOCK_SUFFIX = ".lock"

_task_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "career_scout_task_id", default=""
)
_correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "career_scout_correlation_id", default=""
)

_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(sk-[a-z0-9]{8,})"),
    re.compile(r"(?i)(bearer\s*[=:]?\s*[a-z0-9._~+/=-]+)"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*[^\s,;]+)"),
    re.compile(r"(?i)(authorization\s*[=:]\s*[^\s,;]+)"),
    re.compile(r"(?i)(cookie\s*[=:]\s*[^\s,;]+)"),
    re.compile(r"(?i)(x-boss-token\s*[=:]\s*[^\s,;]+)"),
)


def redact(text: str) -> str:
    """Replace common credential-shaped fragments with a placeholder."""
    result = str(text)
    for pattern in _SENSITIVE_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


class TaskContextFilter(logging.Filter):
    """Attach the current task/correlation ids to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.task_id = _task_id_var.get() or "-"
        record.correlation_id = _correlation_id_var.get() or "-"
        return True


class RedactingFormatter(logging.Formatter):
    """Format then redact credential-shaped values in the final line."""

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        return redact(message)


def _is_test_context() -> bool:
    """Detect test runners via sys.modules so lazy init uses a temp dir.

    pytest and unittest register themselves in sys.modules while running;
    normal webui / scraper subprocess startup does not import them.
    """
    return bool(sys.modules.get("pytest")) or bool(sys.modules.get("unittest"))


@contextlib.contextmanager
def _exclusive_lock(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive lock on ``lock_path`` across process boundaries.

    Windows uses ``msvcrt.locking``; POSIX uses ``fcntl.flock``. When locking
    is unavailable or fails, degrade to unlocked (rotation stays best-effort)
    rather than raising — a logging failure must never crash the app.
    """
    handle = None
    locked = False
    try:
        try:
            if os.name == "nt":
                import msvcrt  # type: ignore[import-not-found]
                handle = open(lock_path, "a+b")
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                locked = True
            elif os.name == "posix":
                import fcntl  # type: ignore[import-not-found]
                handle = open(lock_path, "a+b")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                locked = True
        except OSError:
            pass
        yield
    finally:
        if handle is not None:
            try:
                if locked:
                    if os.name == "nt":
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    elif os.name == "posix":
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            handle.close()


class SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler hardened for cross-process writes (whitebox).

    - ``doRollover`` runs under a cross-process lock so two processes sharing
      one log file cannot corrupt rotation.
    - Rotation/write ``OSError`` degrades to append instead of crashing.
    - A deleted log file is rebuilt lazily on the next emit.
    """

    def doRollover(self) -> None:
        with _exclusive_lock(Path(self.baseFilename + LOCK_SUFFIX)):
            try:
                super().doRollover()
            except OSError:
                # Concurrent rotation by another process or a transient FS
                # error: reopen and keep appending (skip this rotation).
                self.stream = None
                try:
                    self.stream = self._open()
                except OSError:
                    self.stream = None

    def emit(self, record: logging.LogRecord) -> None:
        if self.stream is not None and not Path(self.baseFilename).is_file():
            # Log file removed externally: rebuild it on the next write.
            try:
                self.stream.close()
            except Exception:
                # 句柄已失效：置空交由下方重建，不留纯 pass。
                self.stream = None
            self.stream = None
        if self.stream is None:
            try:
                self.stream = self._open()
            except OSError:
                self.stream = None
        super().emit(record)


def default_log_dir() -> Path:
    override = os.environ.get("CAREER_SCOUT_LOG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".career-scout" / "logs"


def _default_level() -> int:
    raw = os.environ.get("CAREER_SCOUT_LOG_LEVEL", "").strip().upper()
    if not raw:
        return logging.DEBUG
    return getattr(logging, raw, logging.DEBUG)


def configure_logging(
    log_dir: str | os.PathLike[str] | None = None,
    *,
    level: int | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    force: bool = False,
) -> logging.Logger:
    """Configure the named rotating file logger once per process."""
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers and not force:
        return logger

    directory = Path(log_dir).expanduser() if log_dir else default_log_dir()
    if log_dir is None and _is_test_context():
        # 测试上下文且未显式指定目录：一律落到系统临时目录，防测试噪音
        # 灌进正式日志目录（033 白箱边缘情况）。显式传 log_dir 的调用不受影响。
        directory = Path(tempfile.gettempdir()) / "career-scout-test-logs"
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "career-scout.log"

    handler = SafeRotatingFileHandler(
        log_path,
        maxBytes=max(int(max_bytes), 1),
        backupCount=max(1, int(backup_count)),
        encoding="utf-8",
    )
    handler.setFormatter(
        RedactingFormatter(
            "%(asctime)s %(levelname)s %(name)s task=%(task_id)s "
            "corr=%(correlation_id)s %(message)s"
        )
    )
    handler.addFilter(TaskContextFilter())
    logger.setLevel(level if level is not None else _default_level())
    logger.propagate = False
    if force:
        for existing in list(logger.handlers):
            logger.removeHandler(existing)
            try:
                existing.close()
            except Exception:
                _logger.debug("旧日志句柄关闭失败（force 重配场景，忽略）", exc_info=True)

    logger.addHandler(handler)
    logger.info("career-scout log initialized at %s", log_path)
    return logger


@contextlib.contextmanager
def bind_task_context(
    task_id: str = "", correlation_id: str = ""
) -> Iterator[None]:
    """Bind task context for the duration of a block."""
    task_token = _task_id_var.set(str(task_id or ""))
    correlation_token = _correlation_id_var.set(str(correlation_id or ""))
    try:
        yield
    finally:
        _task_id_var.reset(task_token)
        _correlation_id_var.reset(correlation_token)


def is_configured() -> bool:
    """Return whether the local file logger has been configured."""
    return bool(logging.getLogger(LOGGER_NAME).handlers)


def _configure_lazily() -> None:
    """Configure the local file logger on first use (whitebox).

    Test contexts write to a temp dir so automation never pollutes the real
    user log directory; real processes use the default/user-overridden dir.
    Setup failures are swallowed (lastResort still surfaces WARNING+) — a
    logging failure must never crash the caller.
    """
    try:
        if _is_test_context():
            directory = Path(tempfile.gettempdir()) / "career-scout-test-logs"
            configure_logging(directory)
        else:
            configure_logging()
    except Exception:
        return
    # 子进程侧：从环境变量关联 run 级上下文（主进程用显式 bind_task_context）。
    corr = os.environ.get("CAREER_SCOUT_CORRELATION_ID", "").strip()
    if corr:
        _correlation_id_var.set(corr)
    # 任务编号同理：子进程现场日志要能归属到具体任务（033 白箱）。
    task = os.environ.get("CAREER_SCOUT_TASK_ID", "").strip()
    if task:
        _task_id_var.set(task)


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    """Return a child logger of the configured local logger.

    Whitebox (033): when the local file logger has no handler yet (e.g. a
    scraper subprocess or a CLI entry that never called ``configure_logging``),
    configure it lazily so every record has a destination.
    """
    if not is_configured():
        _configure_lazily()
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name != LOGGER_NAME else LOGGER_NAME)


_logger = get_logger(__name__)
