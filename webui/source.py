"""Job source adapter (feature 004) — 门面（021 拆分）。

本文件现为兼容门面：re-export 拆分后各子模块的全部既有符号，保持
``from webui.source import ...`` 与 ``patch("webui.source.X")`` 旧路径
不变。实现位于：
  - source_breaker.py        SourceOutcome / 熔断器 / JobSource Protocol
  - source_boss_helpers.py   失败分类、归一化、登录事实回写等共享助手
  - source_boss_cdp.py       BossCdpSource 主体（preflight/list/detail/命令构建）
  - source_boss_cdp_detail.py  批量详情、事件校验、in-process 执行 mixin
  - source_zhilian_cdp.py    ZhilianCdpSource 与智联常量/校验助手
  - source_zhilian_defaults.py  智联默认 CLI runner 与失败原因映射
  - source_fake.py           FakeJobSource 测试替身
除本拆分批次外，不得在此追加逻辑（宪法 VI）。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from scripts import boss_cdp_raw as boss
from scripts.boss_cdp_signals import FAILURE_LINE_PREFIX, parse_failure_line
from webui.process_executor import ScraperExecutor, run_with_deadline
from webui.workbench import normalize_job_link
from webui.error_registry import (
    ERROR_USER_MESSAGES,
    SAFE_FAILURE_CODES,
    resolve_code,
)
from webui.task_runners import _classify_risk_control_reason
from webui.runtime_audit import record_runtime_event

from webui.source_breaker import (
    JobSource,
    PageEventPersistenceError,
    SourceCircuitBreaker,
    SourceOutcome,
)
from webui.source_boss_helpers import (
    _EXIT_REASONS,
    _LOGIN_REQUIRED_HI_CONFIDENCE_KEYWORDS,
    _classify_failed_code,
    _exit_reason,
    _format_inprocess_failure,
    _input_hash,
    _normalize_job_fields,
    _record_risk_signals,
    _record_success_signal,
    _safe_host,
    _safe_tail,
)
from webui.source_boss_cdp import (
    HERE,
    PREFLIGHT_RETRY_DELAY_SECONDS,
    PROJECT_ROOT,
    SCRAPER,
    SCRAPER_FILTER_FIELDS,
    BossCdpSource,
)
from webui.source_boss_cdp_inprocess import _InProcessCapture
from webui.source_zhilian_cdp import (
    _BOSS_DEFAULT_CDP_PORT,
    _SIGNAL_TO_STATE,
    _STATE_TO_SIGNAL,
    _ZHILIAN_AI_FILTER_KEYS,
    _ZHILIAN_DETAIL_SIGNAL_MAP,
    _ZHILIAN_HOST_ALLOWLIST,
    _ZHILIAN_LIST_SIGNAL_MAP,
    _ZHILIAN_PREFLIGHT_SIGNAL_MAP,
    ZHILIAN_DEFAULT_CDP_PORT,
    ZhilianCdpSource,
    _is_zhilian_host,
    _validate_zhilian_city_snapshot,
    _validate_zhilian_detail_input,
    _validate_zhilian_plan_item,
    _zhilian_input_hash,
    _zhilian_safe_log,
)
from webui.source_zhilian_defaults import (
    _default_zhilian_batch_detail_runner,
    _default_zhilian_detail_runner,
    _default_zhilian_list_runner,
    _default_zhilian_preflight_runner,
    _zhilian_failed_reason,
)
from webui.source_fake import FakeJobSource

# The scraper loads optional dependencies lazily for its CLI.  The web adapter
# needs requests available before tests and preflight can patch/use it.
boss.require_runtime_dependencies("requests")

__all__ = [
    "SAFE_FAILURE_CODES",
    "ZHILIAN_DEFAULT_CDP_PORT",
    "BossCdpSource",
    "FakeJobSource",
    "JobSource",
    "SourceCircuitBreaker",
    "SourceOutcome",
    "ZhilianCdpSource",
]
