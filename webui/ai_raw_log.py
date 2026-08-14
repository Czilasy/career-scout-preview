"""Rotating local log for raw AI response bodies (B044).

Writes the final streamed response text before JSON parsing into
``~/.career-scout/logs/ai_raw.log`` with the same rotation policy as the
main local log.  Credential-shaped values are redacted; the body itself is
otherwise kept for troubleshooting.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from pathlib import Path
import threading

from webui.logging_setup import default_log_dir, redact

LOGGER_NAME = "career_scout.ai_raw"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 10
MAX_RAW_AI_RESPONSE_BYTES = 500 * 1024

_handler: logging.Handler | None = None
_handler_dir: Path | None = None
_handler_lock = threading.Lock()


def _resolve_handler(log_dir: Path | None) -> logging.Handler:
    global _handler, _handler_dir
    target = Path(log_dir).expanduser() if log_dir is not None else default_log_dir()
    target = target.resolve()
    if _handler is not None and _handler_dir == target:
        return _handler
    if _handler is not None:
        try:
            _handler.close()
        except Exception:
            pass
    target.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        target / "ai_raw.log",
        maxBytes=DEFAULT_MAX_BYTES,
        backupCount=DEFAULT_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    _handler = handler
    _handler_dir = target
    return handler

def _close_handler() -> None:
    global _handler, _handler_dir
    if _handler is not None:
        try:
            _handler.close()
        except Exception:
            pass
    _handler = None
    _handler_dir = None


def record_raw_ai_response(
    correlation_id: str,
    attempt_index: int,
    text: str,
    *,
    log_dir: str | Path | None = None,
) -> None:
    """Write one raw AI response attempt to the local rotating log."""
    body = redact(str(text or ""))
    original_length = len(str(text or ""))
    truncated = False
    if len(body) > MAX_RAW_AI_RESPONSE_BYTES:
        truncated = True
        body = body[:MAX_RAW_AI_RESPONSE_BYTES]
    payload = {
        "correlation_id": str(correlation_id or ""),
        "attempt_index": int(attempt_index),
        "original_length": original_length,
        "truncated": truncated,
        "body": body,
    }
    # 并发 AI 调用共用全局 handler，串行写入避免轮转/关闭竞态丢日志。
    with _handler_lock:
        handler = _resolve_handler(log_dir)
        logger = logging.getLogger(LOGGER_NAME)
        logger.handlers = [handler]
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.info(json.dumps(payload, ensure_ascii=False))
        _close_handler()
