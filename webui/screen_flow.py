"""AI 筛选暂停/续跑/上下文编排。

分层：app.py（路由）→ 本模块（编排）→ store mixin/store.py。
本模块不依赖 app.py 内部闭包，可独立测试。
"""

from __future__ import annotations

import json

from webui.scrape_only import merge_round_script_params

RESUMABLE_STATUSES = ("paused", "failed", "interrupted", "partial")
RESUMABLE_INTERRUPTED_CODES = {"restart", "user_finished"}
_SNAPSHOT_FALLBACK_STATUSES = (
    "paused", "failed", "interrupted", "partial", "succeeded",
)


def _same_facts(left, right):
    return json.dumps(
        left or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) == json.dumps(
        right or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _normalize_keywords(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalize_cities(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def find_resumable_screen_run(
    store, scrape_task_id, screening_fields, profile_summary, profile_facts
):
    """按优先级找同一来源可续跑的 AI 筛选 run。

    顺序：paused → failed → interrupted(restart/user_finished) → partial。
    只有六类条件、画像、画像事实全部一致才返回。
    """
    candidates = store.latest_screen_runs_for_source(
        scrape_task_id, statuses=RESUMABLE_STATUSES,
    )
    for run in candidates:
        params = run.get("execution_params") or {}
        if run["status"] == "interrupted" and str(
            run.get("error_code") or ""
        ) not in RESUMABLE_INTERRUPTED_CODES:
            continue
        if run.get("frozen_filters") != screening_fields:
            continue
        if str(params.get("profile_summary") or "") != str(profile_summary or ""):
            continue
        if not _same_facts(params.get("profile_facts"), profile_facts):
            continue
        return run
    return None


def build_round_script_params(store, run, screening_fields, platform):
    """合并父抓取 script_params 与 AI 筛选快照参数。"""
    scrape_task_id = str((run.get("execution_params") or {}).get("scrape_task_id") or "")
    parent_script_params = {}
    if scrape_task_id:
        parent = store.get_screening_run(scrape_task_id) or {}
        parent_script_params = (parent.get("execution_params") or {}).get(
            "script_params"
        ) or {}
    return merge_round_script_params(
        parent_script_params,
        screening_fields if screening_fields is not None else run.get("frozen_filters") or {},
        platform,
    )


def load_resume_jd(store, jd_checkpoint_path, run_id):
    """续跑 JD 断点优先；文件缺失或为空时从 screening_results 回退。"""
    try:
        with open(jd_checkpoint_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        data = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("jd_checkpoint_unavailable") from exc
    if not isinstance(data, dict):
        raise RuntimeError("jd_checkpoint_invalid")
    resume_jd = {
        str(k): str(v) for k, v in data.items() if isinstance(v, str) and v.strip()
    }
    if resume_jd:
        return resume_jd
    return store.load_screening_jd_map(run_id)


def load_resume_verdicts_with_fallback(
    store, run_id, platform, scrape_task_id, screening_fields, profile_summary
):
    """续跑判定优先读 run 自身；粗筛 checkpoint 比判定多时从同轮快照回退。

    历史版本的硬规则剔除没有逐条写入 screening_results，但结果快照里保存了
    完整判定；回退只合并同来源、同条件、同画像的最新快照，避免续跑整批重跑。
    """
    verdicts = store.load_screening_verdicts(run_id)
    checkpoint_ids = list(store.load_checkpoint(run_id, "ai_rough") or [])
    if not checkpoint_ids or len(verdicts) >= len(checkpoint_ids):
        return verdicts
    payload = store.load_latest_pipeline_result_for_platform(platform)
    if payload is None:
        return verdicts
    if str(payload.get("scrape_task_id") or "") != str(scrape_task_id or ""):
        return verdicts
    snapshot_screening = (payload.get("script_params") or {}).get("screening")
    if snapshot_screening is not None and snapshot_screening != screening_fields:
        return verdicts
    if str((payload.get("result") or {}).get("profile_summary") or "") != str(
        profile_summary or ""
    ):
        return verdicts
    snapshot_run_id = str(payload.get("run_id") or "")
    if not snapshot_run_id or snapshot_run_id == str(run_id):
        return verdicts
    snapshot_verdicts = store.load_screening_verdicts(snapshot_run_id)
    if not snapshot_verdicts:
        return verdicts
    return {**snapshot_verdicts, **verdicts}


def resolve_snapshot_source_run(store, run):
    """结果快照追溯来源 AI run；普通 run 原样返回。"""
    if run is None:
        return None
    if run.get("record_kind") != "result_snapshot":
        return run
    params = run.get("execution_params") or {}
    screen_run_id = str(params.get("screen_run_id") or "")
    if screen_run_id:
        source = store.get_screening_run(screen_run_id)
        if source is not None and source.get("record_kind") != "result_snapshot":
            return source
    scrape_task_id = str(params.get("scrape_task_id") or "")
    if not scrape_task_id:
        return None
    candidates = store.latest_screen_runs_for_source(
        scrape_task_id, statuses=_SNAPSHOT_FALLBACK_STATUSES,
    )
    if not candidates:
        return None
    return max(candidates, key=lambda item: str(item.get("updated_at") or ""))


def build_round_context_payload(store, run):
    """构建前端恢复 02/03 所需的完整本轮上下文。

    无 scrape_task_id 时回退到 run 自身 execution_params.script_params；
    未筛选/暂停快照轮从自身 search_params_json 恢复关键词与城市。
    """
    if run is None:
        return None
    source_run = resolve_snapshot_source_run(store, run)
    if source_run is None:
        source_run = run
    if source_run is None:
        return None
    params = source_run.get("execution_params") or {}
    scrape_task_id = str(params.get("scrape_task_id") or "")
    platform = str(params.get("platform") or source_run.get("platform") or "")
    status = source_run.get("status") or ""
    if source_run.get("record_kind") == "result_snapshot":
        search = source_run.get("search_params") or {}
        screening = (
            search.get("screening")
            if isinstance(search.get("screening"), dict) else {}
        )
        return {
            "platform": platform,
            "keywords": _normalize_keywords(
                search.get("keyword") or search.get("keywords")
            ),
            "cities": _normalize_cities(
                search.get("city") or search.get("cities")
            ),
            "screening_fields": screening or {},
            "profile_summary": str(
                params.get("profile_summary") or source_run.get("profile_summary") or ""
            ),
            "profile_facts": (
                params.get("profile_facts") or source_run.get("profile_facts") or {}
            ),
            "scrape_task_id": scrape_task_id,
            "screen_run_id": str(
                params.get("screen_run_id") or source_run.get("id") or ""
            ),
            "status": status,
            "resumable": status in RESUMABLE_STATUSES,
            "has_frozen_filters": bool(screening),
        }
    parent_script_params = {}
    if scrape_task_id:
        parent = store.get_screening_run(scrape_task_id) or {}
        parent_script_params = (parent.get("execution_params") or {}).get(
            "script_params"
        ) or {}
    else:
        parent_script_params = params.get("script_params") or {}
    if not isinstance(parent_script_params, dict):
        parent_script_params = {}
    return {
        "platform": platform,
        "keywords": _normalize_keywords(parent_script_params.get("keyword")),
        "cities": _normalize_cities(parent_script_params.get("city")),
        "screening_fields": source_run.get("frozen_filters") or {},
        "profile_summary": str(params.get("profile_summary") or ""),
        "profile_facts": params.get("profile_facts") or {},
        "scrape_task_id": scrape_task_id,
        "screen_run_id": source_run.get("id") or "",
        "status": status,
        "resumable": status in RESUMABLE_STATUSES,
        "has_frozen_filters": True,
    }
