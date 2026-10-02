"""AI 筛选暂停/续跑/上下文编排。

分层：app.py（路由）→ 本模块（编排）→ store mixin/store.py。
本模块不依赖 app.py 内部闭包，可独立测试。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from webui import ai_domain_policy as domain_policy
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


def read_domain_selection(store, scrape_task_id, screening_fields, *,
                          platform=None, profile_id=None):
    """只读派生当前领域语义；候选查找与 worker 共用，不依赖 API/context。"""
    from webui.ai_platform_adapter import resolve_platform_ai_adapter
    from webui.flow_task_coordinator import resolve_flow_binding

    source = store.get_screening_run(scrape_task_id) or {}
    params = source.get("execution_params") or {}
    binding = resolve_flow_binding(SimpleNamespace(store=store), scrape_task_id)
    bound = binding or {}
    platform = str(platform or bound.get("platform") or params.get("platform")
                   or source.get("platform") or "").strip().lower()
    if bound.get("platform") and platform != bound["platform"]:
        raise domain_policy.DomainSnapshotError("source platform conflicts with task")
    bound_profile = bound.get("profile_id")
    if profile_id and bound_profile and str(profile_id) != str(bound_profile):
        raise domain_policy.DomainSnapshotError("source profile conflicts with task")
    adapter = resolve_platform_ai_adapter(platform)
    labels = adapter.domain_selection({"industry": (screening_fields or {}).get("industry")}).labels
    snapshot = None
    if bound.get("flow_id") and bound.get("track_id"):
        flow = store.get_flow(bound["flow_id"], profile_id=bound_profile or profile_id)
        track = next((row for row in (flow or {}).get("tracks") or []
                      if str(row.get("platform") or "") == platform), None)
        if track is None or str(track.get("id")) != str(bound["track_id"]):
            raise domain_policy.DomainSnapshotError("source Track identity conflicts")
        snapshot = track.get("confirmed_filters_snapshot")
        if snapshot is not None and not isinstance(snapshot, dict):
            raise domain_policy.DomainSnapshotError("condition snapshot must be an object")
    selection = domain_policy.selection_from_snapshot(
        snapshot=snapshot, platform=platform, current_labels=labels,
    )
    return selection, binding


def find_resumable_screen_run(
    store, scrape_task_id, screening_fields, profile_summary, profile_facts,
    *, domain_selection=None,
):
    """按优先级找同一来源可续跑的 AI 筛选 run。

    顺序：paused → failed → interrupted(restart/user_finished) → partial。
    只有已冻结筛选条件全量一致（含 028 第 7 类，全字典相等比对）、画像、
    画像事实全部一致才返回。
    ``domain_selection``：B094 内部可选参数。领域规则版本或领域语义摘要不一致
    的候选直接跳过，让既有入口按新规则新建筛选；不先返回旧候选再让 runner
    反复失败，也不把不兼容缓存默认为可用。
    """
    if domain_selection is None and domain_policy.industry_selected_in_fields(screening_fields):
        from webui.flow_task_coordinator import FlowTaskOperationError, MissingPlatformIdentityError
        try:
            domain_selection, _binding = read_domain_selection(store, scrape_task_id, screening_fields)
        except (domain_policy.DomainSnapshotError, FlowTaskOperationError,
                MissingPlatformIdentityError, KeyError, ValueError):
            # 不选择候选、不降级为不限；worker 会以登记分类持久化快照失败。
            return None
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
        if not domain_policy.is_verdict_state_compatible(
            params, selection=domain_selection, screening_fields=screening_fields,
        ):
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
    else:
        parent_script_params = (run.get("execution_params") or {}).get(
            "script_params"
        ) or {}
    if not isinstance(parent_script_params, dict):
        parent_script_params = {}
    return merge_round_script_params(
        parent_script_params,
        screening_fields if screening_fields is not None else run.get("frozen_filters") or {},
        platform,
    )


def load_resume_jd(store, jd_checkpoint_path, run_id, include_dropped=False):
    """续跑 JD 断点优先；文件缺失或为空时从 screening_results 回退。

    ``include_dropped=True`` 是 B094 的只读资料模式：断点文件与结果表合并，
    同一候选以文件原文为准，旧剔除标记不限制客观资料。默认参数保持既有
    续跑行为（只读非剔除行）。
    """
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
    if not include_dropped:
        if resume_jd:
            return resume_jd
        return store.load_screening_jd_map(run_id)
    table = store.load_screening_jd_map(run_id, include_dropped=True) or {}
    if not isinstance(table, dict):
        return dict(resume_jd)
    merged = {
        str(k): str(v) for k, v in table.items()
        if isinstance(v, str) and v.strip()
    }
    merged.update(resume_jd)
    return merged


def load_resume_verdicts_with_fallback(
    store, run_id, platform, scrape_task_id, screening_fields, profile_summary,
    profile_facts=None, *, domain_selection=None,
):
    """续跑判定优先读 run 自身；粗筛 checkpoint 比判定多时从同源链合并。

    历史版本的硬规则剔除没有逐条写入 screening_results，但同源链上的
    其他 run 保存了完整判定（018 事故：完整判定挂在链上第一条 run 名下）。
    回退只合并同来源、同条件、同画像、同画像事实的 run（排除自身），按
    created_at 从旧到新合并、新的覆盖旧的，避免续跑整批重跑或幸存者塌缩。
    ``platform`` 仅保持既有签名兼容，合并不再依赖结果快照。
    ``domain_selection``：B094 内部可选参数。自身判定在提前返回之前先过
    规则兼容守卫，同源回退逐条检查版本与领域语义摘要；调用方没传该信息时，
    带领域选择的旧判定同样不得默认为可复用。
    """
    own_run = store.get_screening_run(run_id) or {}
    own_params = own_run.get("execution_params") or {}
    if not domain_policy.is_verdict_state_compatible(
        own_params, selection=domain_selection,
        screening_fields=own_run.get("frozen_filters") or screening_fields,
    ):
        return {}
    verdicts = store.load_screening_verdicts(run_id)
    checkpoint_ids = list(store.load_checkpoint(run_id, "ai_rough") or [])
    # 020 US6：覆盖比较取代数量比较——精筛判定计入总数后"数量够"不代表
    # "断点全覆盖"；断点内任何无判定记录的岗位都触发同源链合并。
    if not checkpoint_ids or not (set(checkpoint_ids) - set(verdicts)):
        return verdicts
    merged = {}
    for run in store.latest_screen_runs_for_source(scrape_task_id) or []:
        if str(run.get("id") or "") == str(run_id):
            continue
        params = run.get("execution_params") or {}
        if run.get("frozen_filters") != screening_fields:
            continue
        if str(params.get("profile_summary") or "") != str(profile_summary or ""):
            continue
        if not _same_facts(params.get("profile_facts"), profile_facts):
            continue
        if not domain_policy.is_verdict_state_compatible(
            params, selection=domain_selection, screening_fields=screening_fields,
        ):
            continue
        merged.update(store.load_screening_verdicts(str(run.get("id") or "")))
    if not merged:
        return verdicts
    return {**merged, **verdicts}


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
    # 用户主动「结束并保存结果」是持久化终态：快照保留但断点不复活，
    # 刷新后 03 不得再出现继续/结束按钮（同一抓取源重开筛选时后端仍可接续进度）。
    closed_saved = bool(
        status == "interrupted"
        and source_run.get("error_code") == "user_finished"
    )
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
            "locations": (
                search.get("locations")
                if isinstance(search.get("locations"), list) else []
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
            "status": "partial" if closed_saved else status,
            "resumable": False if closed_saved else status in RESUMABLE_STATUSES,
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
        "locations": (
            parent_script_params.get("locations")
            if isinstance(parent_script_params.get("locations"), list) else []
        ),
        "screening_fields": source_run.get("frozen_filters") or {},
        "profile_summary": str(params.get("profile_summary") or ""),
        "profile_facts": params.get("profile_facts") or {},
        "scrape_task_id": scrape_task_id,
        "screen_run_id": source_run.get("id") or "",
        "status": "partial" if closed_saved else status,
        "resumable": False if closed_saved else status in RESUMABLE_STATUSES,
        # 空对象是合法的“六类条件均不限”，不能误报为条件快照丢失。
        "has_frozen_filters": bool(source_run.get("frozen_filters") or {}),
    }
