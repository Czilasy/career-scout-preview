"""Pure JD admission rules shared by AI screening entry points."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def has_usable_jd(job: Mapping[str, Any]) -> bool:
    """Return whether a job has non-whitespace detail text for fine screening."""
    return bool(str(job.get("jd") or "").strip())


def missing_jd_verdict(job: Mapping[str, Any]) -> dict[str, str]:
    """Build the only valid fine-screening outcome when detail is unavailable."""
    failure_reason = str(job.get("jd_failed_reason") or "").strip()
    if failure_reason:
        reason = f"未抓到 JD（{failure_reason}），无法精筛"
    else:
        reason = "未抓到 JD，无法精筛"
    return {"verdict": "uncertain", "reason": reason}