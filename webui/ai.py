"""AI adapter: credential management, connection testing, JSON-validated AI calls.

All errors are sanitized to safe classification codes.  API keys, request
bodies and raw responses never appear in exceptions, logs or return values.
The application validates every AI output on its own side — the AI never
decides task status.

021 B7 后本文件为兼容门面：实现拆至 ai_errors / ai_schannel / ai_client /
ai_filters / ai_screening / ai_resume 域模块，re-export 全部既有符号，
旧 import 与 patch 面不变（宪法 VI）。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from collections.abc import Mapping
from urllib.parse import urlparse

import keyring
import requests

from scripts import boss_cdp_raw as boss
from webui.core import salary_monthly_bounds
from webui.ai_retry import (
    FINE_SINGLE_INVALID_RESPONSE_DELAY_SECONDS,
    effective_retry_plan,
    retry_delay_seconds,
)
from webui.error_registry import (
    AI_TAXONOMY_TARGETS,
    ERROR_AUTH,
    ERROR_INVALID,
    ERROR_NETWORK,
    ERROR_NOT_CONFIGURED,
    ERROR_QUOTA_EXHAUSTED,
    ERROR_RATE_LIMIT,
    ERROR_SERVER,
    ERROR_TIMEOUT,
    ERROR_TRUNCATED,
    ERROR_USER_MESSAGES,
    SYSTEMIC_AI_ERROR_CODES,
)
from webui.error_registry import resolve_code
from webui.ai_raw_log import record_raw_ai_response
from webui.flag_features import (
    build_features_prompt_text,
    clean_flags,
    decide_flags,
)
from webui.screening_jd_gate import has_usable_jd, missing_jd_verdict
from webui.profile_facts import (
    build_profile_facts_description,
    validate_profile_facts,
)
from webui.ai_prompts import (
    build_match_system_prompt,
    build_resume_analysis_prompt,
)


from webui.ai_errors import (
    AICheckpointError,
    AISecurityError,
    _emit_batch_event,
    _emit_item_terminal_event,
    _emit_request_event,
    _emit_retry_event,
    _extract_provider_error,
    _is_quota_exhausted_response,
    _looks_truncated,
    _measurement_item_index,
    map_ai_error_to_block_code,
    user_facing_error,
)
from webui.ai_schannel import (
    _SCHANNEL_POST_SCRIPT,
    _windows_schannel_post,
)
from webui.ai_client import (
    CONNECTION_TIMEOUT,
    DEFAULT_TIMEOUT,
    FINE_BATCH_TIMEOUT,
    KEYRING_SERVICE,
    NETWORK_BACKOFF_SECONDS,
    RANK_BATCH_SIZE,
    RATE_LIMIT_ATTEMPTS,
    RATE_LIMIT_BACKOFF_SECONDS,
    RETRYABLE_STATUS,
    SERVER_ERROR_BACKOFF_SECONDS,
    STREAM_IDLE_TIMEOUT,
    STREAM_TOTAL_TIMEOUT,
    _AI_CHECKPOINT_FAILED,
    _CHAT_COMPLETIONS_PATH,
    _MODELS_PATH,
    _chat_completions_url,
    _host_from_url,
    _post_ai_json,
    _read_stream,
    _read_stream_with_timeout,
    call_ai,
    delete_api_key,
    list_models,
    retrieve_api_key,
    store_api_key,
    test_connection,
)
from webui.ai_filters import (
    AI_CONSECUTIVE_FAILURE_LIMIT,
    MATCH_BATCH_SIZE,
    MATCH_CONCURRENCY,
    SCREEN_BATCH_SIZE,
    SCREEN_CONCURRENCY,
    _COMBINED_EXPERIENCE_CODES,
    _FILTER_CODE_MAPS,
    _FILTER_CODE_READERS,
    _FILTER_FIELD_LABELS,
    _adv_setting,
    _build_criteria_description,
    _criteria_codes,
    _degree_code_label,
    _job_criteria_hard_mismatch,
    _job_degree_codes,
    _job_experience_codes,
    _job_filter_tag_text,
    _job_industry_codes,
    _job_scale_codes,
    _job_stage_codes,
    _job_value_label,
    _salary_hard_mismatch,
    _salary_selected_bounds,
)
from webui.ai_screening import (
    match_jds,
    screen_jobs,
)
from webui.ai_resume import (
    RESUME_SENTINEL_LABELS,
    UNIFIED_SEARCH_FIELDS,
    _FALLBACK_KEYWORDS_MAX,
    _build_field_options_prompt,
    _fallback_keywords_from_facts,
    _normalize_ai_payload_keys,
    _require,
    _require_str_list,
    _resume_bytes_to_text,
    _resume_platform_registry,
    _validate_unified_fields,
    analyze_resume_to_fields,
    is_ai_available,
    parse_resume,
    rank_jds,
    update_preference,
    validate_preference_response,
    validate_rank_response,
    validate_resume_response,
)
