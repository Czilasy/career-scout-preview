"""Local rotating log setup with task context and redaction.

Logs live outside the repository under the user data directory so public
repo hygiene rules are not affected.  The redaction formatter is a safety
net for credential-shaped values; callers must still avoid logging raw
API keys, cookies, resume text or JD bodies.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import logging.handlers
import os
import re
from pathlib import Path
from typing import Iterator


LOGGER_NAME = "career_scout"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 10

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


def default_log_dir() -> Path:
    override = os.environ.get("CAREER_SCOUT_LOG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".career-scout" / "logs"


def configure_logging(
    log_dir: str | os.PathLike[str] | None = None,
    *,
    level: int = logging.DEBUG,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    force: bool = False,
) -> logging.Logger:
    """Configure the named rotating file logger once per process."""
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers and not force:
        return logger

    directory = Path(log_dir).expanduser() if log_dir else default_log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "career-scout.log"

    handler = logging.handlers.RotatingFileHandler(
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
    logger.setLevel(level)
    logger.propagate = False
    if force:
        for existing in list(logger.handlers):
            logger.removeHandler(existing)
            try:
                existing.close()
            except Exception:
                _logger.debug("旧日志句柄关闭失败（force 重配场景，忽略）", exc_info=True)

    logger.addHandler(handler)
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

def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    """Return a child logger of the configured local logger."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name != LOGGER_NAME else LOGGER_NAME)

_logger = get_logger(__name__)
