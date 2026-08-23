"""测量事件白名单与 MeasurementSink（021 B7 自 tuning.py 搬运）。

sink 供 pipeline_exec / ai / boss 抓取记录测量事件，写入前按白名单过滤
（data-model.md 2.9：凭据、原始简历、原始模型响应、JD 正文禁止持久化）。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from webui.tuning import TuningController

ALLOWED_EVENT_TYPES = frozenset({
    "stage", "batch", "request", "wait", "retry", "item_terminal",
})

# Allowlisted count keys (safe to persist; no credentials/resume/JD body).
ALLOWED_COUNT_KEYS = frozenset({
    "input_count", "output_count", "batch_size", "batch_index",
    "item_index", "status", "attempt", "kept", "dropped",
    "match", "not_match", "uncertain", "error_count", "retry_count",
    "concurrency", "total_items", "processed_items",
})

# Allowlisted metadata keys.
ALLOWED_METADATA_KEYS = frozenset({
    "stage", "step", "batch_index", "truncated_split", "backoff_ms",
    "error_code", "transport_failed", "recovered",
    "correlation_id", "attempt_id", "attempt_index", "failure_phase",
    "exception_type", "safe_message", "http_status",
    "provider_error_type", "provider_error_code", "response_empty",
    "response_length", "finish_reason", "parse_error", "parse_position",
    "retry_decision", "outcome", "artifact_path", "artifact_digest",
    "artifact_redacted",
})


class MeasurementSink:
    """Allowlisted measurement sink bound to a single round.

    Passed to pipeline_exec / ai.py / boss_cdp_raw so they can record
    measurement events without a direct dependency on TuningController.

    FR-030/SC-006: all waits, cooldowns, retries and recovery time counted.
    SC-007: terminal conservation enforced at aggregation time.
    data-model.md 2.9: credentials, raw resume, raw model response and
    JD body are forbidden — the sink strips/ rejects them before persisting.
    """

    __slots__ = ("_controller", "_monotonic_base", "_round_id")

    def __init__(self, controller: TuningController, round_id: str):
        self._controller = controller
        self._round_id = round_id
        self._monotonic_base = time.monotonic()

    @property
    def round_id(self) -> str:
        return self._round_id

    def monotonic_ms(self) -> int:
        """Return elapsed monotonic milliseconds since sink creation."""
        return int((time.monotonic() - self._monotonic_base) * 1000)

    def __call__(
        self, event_type: str, stage: str, duration_ms: int,
        *, counts: dict | None = None, error_code: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Record one measurement event (allowlisted keys only)."""
        if event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"不允许的事件类型: {event_type}")
        if duration_ms < 0:
            raise ValueError("duration_ms 必须非负")
        safe_counts = self._filter_counts(counts) if counts else None
        safe_metadata = self._filter_metadata(metadata) if metadata else None
        return self._controller.record_measurement(
            round_id=self._round_id,
            event_type=event_type,
            stage=stage,
            duration_ms=duration_ms,
            started_monotonic_ms=self.monotonic_ms(),
            counts=safe_counts,
            error_code=error_code,
            metadata=safe_metadata,
        )

    def record(
        self, event_type: str, stage: str, duration_ms: int,
        **kwargs,
    ) -> dict:
        """Alias for __call__ accepting kwargs for ergonomic call sites."""
        return self(event_type, stage, duration_ms, **kwargs)

    @staticmethod
    def _filter_counts(counts: dict) -> dict:
        """Keep only allowlisted count keys; drop anything else silently."""
        if not isinstance(counts, dict):
            return {}
        safe = {}
        for key, value in counts.items():
            key_lower = str(key).lower()
            if key_lower in ALLOWED_COUNT_KEYS:
                safe[key_lower] = value
        return safe

    @staticmethod
    def _filter_metadata(metadata: dict) -> dict:
        """Keep only allowlisted metadata keys."""
        if not isinstance(metadata, dict):
            return {}
        safe = {}
        for key, value in metadata.items():
            key_lower = str(key).lower()
            if key_lower in ALLOWED_METADATA_KEYS:
                safe[key_lower] = value
        return safe
