"""Default AI retry policy and tuning overrides.

The default policy is: first request plus up to two retries (three
attempts total), with a fixed 30 second wait after each retryable
failure. Explicit ``retry_limits`` from a tuning manifest always wins
over the default plan.
"""

from __future__ import annotations

from typing import Any, Mapping

DEFAULT_AI_RETRY_MAX_ATTEMPTS = 3
DEFAULT_AI_RETRY_DELAY_SECONDS = 30


def effective_retry_plan(retry_limits: Mapping[str, int] | None) -> dict[str, Any]:
    """Return the retry plan for a call.

    ``None`` returns the default three-attempt plan. A mapping returns a
    tuning plan that preserves per-error budgets for the caller.
    """
    if retry_limits is None:
        return {
            "mode": "default",
            "max_attempts": DEFAULT_AI_RETRY_MAX_ATTEMPTS,
            "delay_seconds": DEFAULT_AI_RETRY_DELAY_SECONDS,
        }
    return {
        "mode": "tuning",
        "retry_limits": dict(retry_limits),
    }
