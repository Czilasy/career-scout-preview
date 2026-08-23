"""Three-stage pipeline execution layer (stage 3).

Expands the confirmed multi-select search params into keyword × city
combinations, runs the BOSS CDP scraper for each combination (reusing the
scraper's built-in anti-rate-limit protections: random page delays,
human-like scrolling, request caps, circuit breaker), merges and dedups
the results, then applies the multi-select filters as a local post-filter.

The scraper subprocess enforces per-search rate limiting on its own.  This
layer adds a random delay BETWEEN combinations so consecutive searches are
never back-to-back, absorbing the same "slow is safe" philosophy.

021 B7 后本文件为兼容门面：实现拆至 pipeline_exec_* 域模块，
re-export 全部既有符号，旧 import 与 patch 面不变（宪法 VI）。
"""

from __future__ import annotations

from __future__ import annotations

import json
import hashlib
import os
import random
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from scripts import boss_cdp_raw as boss
from webui.source import PageEventPersistenceError
from webui.browser_recovery import BrowserRecovery
from webui.error_registry import (
    ERROR_TAXONOMY,
    FAILED_CODE_LABELS as _FAILED_CODE_LABELS,
    SYSTEMIC_BLOCK_CODES as _HARD_STOP_CODES,
)
from webui.error_registry import resolve_code


from webui.pipeline_exec_settings import (
    ADVANCED_SETTINGS_PATH,
    _ADVANCED_DEFAULTS,
    _ADVANCED_SETTINGS_DIR,
    _MSG_CDP_UNAVAILABLE,
    _MSG_IP_RISK_CONTROL,
    _MSG_ZHILIAN_LOGIN_REQUIRED,
    _PIPELINE_OPERATION_ERRORS,
    load_advanced_settings,
    save_advanced_settings,
)
from webui.pipeline_exec_accounts import (
    BROWSER_ACCOUNTS,
    _BROWSER_ACCOUNTS_LOCK,
    _BROWSER_ACCOUNTS_PATH,
    _cdp_data_dir,
    _default_browser_accounts,
    _normalize_account_name,
    add_browser_account,
    browser_accounts_path,
    delete_browser_account,
    load_browser_accounts,
    reset_browser_accounts_path,
    resolve_browser_account,
    save_browser_accounts,
    set_active_cdp_data_dir,
    set_browser_accounts_path,
)
from webui.pipeline_exec_status import (
    _PLATFORM_LABEL_OVERRIDES,
    _PLATFORM_TAXONOMY_OVERRIDES,
    _SCRAPE_STAGE_MESSAGES,
    _SCRAPE_STAGE_WEIGHTS,
    _classify_detail_batch_exception,
    _scrape_overall_percent,
    _scrape_page_overall_percent,
    failed_code_label,
    taxonomy_reason,
)
from webui.pipeline_exec_chrome import (
    _read_chrome_stderr_tail,
    close_debug_chrome,
    ensure_chrome_ready,
)
from webui.pipeline_exec_filters import (
    _job_exp_degree_codes,
    _job_industry_code,
    _job_salary_code,
    _job_scale_code,
    _job_stage_code,
    expand_combinations,
    job_matches,
    split_keywords,
)
from webui.pipeline_exec_search import (
    run_search,
)
from webui.pipeline_exec_details import (
    fetch_job_details,
)
from webui.pipeline_exec_artifacts import (
    _combo_hash,
    _combo_output_path,
    get_frozen_artifact_manifest,
)
from webui.pipeline_exec_tuning import (
    TuningRoundRunner,
    TuningStageError,
)

# `_ACTIVE_CDP_DATA_DIR` 曾为本模块自有全局（外部测试直接读写该符号）。
# 拆分后权威值在 pipeline_exec_accounts，此处保留镜像定义，
# 由 set_active_cdp_data_dir 每次写入时同步回写，保持可 patch 语义。
_ACTIVE_CDP_DATA_DIR: str | None = None
