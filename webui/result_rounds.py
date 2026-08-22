"""History round write service: 历史轮唯一写入入口（017-history-round-semantics）。

分层：app.py（路由）→ 本模块（服务）→ store/mixins（数据访问）。
本模块不依赖 app.py 内部状态，可独立测试。

历史轮的写入面收敛为三个合法出口，全部经本模块落库：
- 自然跑完 / 结束保存 → ``save_finished_round``
- 跳过 AI 筛选直接查看结果 → ``save_scraped_only_round``
- 重抓 / 补筛回写 → ``apply_recrawl_writeback``

防重守卫只存在于本模块：同流程（scrape_task_id + platform）已有可见轮
（done/partial/scraped_only）时原地升级，不新增轮（一条流程一条轮）。
"""

from __future__ import annotations

import json
from typing import Any

# 历史查询与最新结果判定的可见状态集（US3/US5 唯一口径）
VISIBLE_STATUSES = ("done", "partial", "scraped_only")


def build_undecided_result(
    source_jobs: list[dict],
    *,
    platform: str,
    profile_summary: str = "",
    profile_facts: dict | None = None,
) -> dict:
    """把抓取任务原始岗位规整为无判定 result。

    字段与 ``_build_partial_pipeline_result`` 输出同构（前端 JobWorkspace
    直接消费），但 ``verdict`` 一律留空——未筛选轮的判定状态由轮次级
    ``scraped_only`` 表达，不写 pending、不混入"待人工确认"。
    """
    jobs = []
    for job in source_jobs or []:
        if not isinstance(job, dict):
            continue
        jid = str(job.get("platform_job_id") or job.get("job_id") or "")
        jobs.append({
            "platform": platform,
            "platform_job_id": jid,
            "job_id": str(job.get("job_id") or "") or None,
            "title": str(job.get("title") or ""),
            "company": str(job.get("company") or job.get("boss_name") or ""),
            "salary": str(job.get("salary") or ""),
            "location": str(job.get("location") or ""),
            "tags": str(job.get("tags") or ""),
            "jd": str(job.get("jd") or ""),
            "source_url": str(job.get("source_url") or job.get("job_link") or ""),
            "canonical_url": str(job.get("canonical_url") or job.get("source_url") or job.get("job_link") or ""),
            "verdict": "",
            "verdict_reason": "",
            "caveats": [],
            "flags": [],
            "experience": str(job.get("experience") or ""),
            "degree": str(job.get("degree") or ""),
            "extra": job.get("extra") or {},
        })
    return {
        "ok": True,
        "jobs": jobs,
        "dropped": [],
        "total_scraped": len(jobs),
        "total_kept": len(jobs),
        "total_matched": 0,
        "total_dropped": 0,
        "profile_summary": str(profile_summary or ""),
        "profile_facts": profile_facts or {},
        "error": "",
    }


def save_finished_round(
    store,
    result: dict,
    script_params: dict,
    *,
    scrape_task_id: str = "",
    status: str = "done",
    execution_config: dict | None = None,
    platform: str = "",
    profile_summary: str = "",
    profile_facts: dict | None = None,
    started_at=None,
    finished_at=None,
) -> str | None:
    """自然跑完与结束保存共用的落库入口（一条流程一条轮）。

    - 0 岗位（jobs 与 dropped 均为空）不成轮，返回 None（沿用现有规则）。
    - 同流程（scrape_task_id + platform）已有可见轮：原地升级（判定数据
      重写、计数重算、定稿时间刷新、解除归档），不新增，返回原 run_id。
    - 无已有轮：新建 result_snapshot 轮，返回新 run_id。
    """
    jobs = result.get("jobs") or []
    dropped = result.get("dropped") or []
    if not jobs and not dropped:
        return None
    if profile_summary is None or not str(profile_summary).strip():
        profile_summary = str(result.get("profile_summary") or "")
    if profile_facts is None:
        profile_facts = result.get("profile_facts")
    merged_script_params = _merge_parent_script_params(
        store, scrape_task_id, script_params)
    existing = _existing_round_for_flow(store, scrape_task_id, platform)
    if existing is not None:
        return store.upgrade_scraped_run(
            str(existing["id"]),
            result,
            merged_script_params,
            status=status,
            execution_config=execution_config,
            platform=platform or str(existing.get("platform") or "boss"),
            profile_summary=profile_summary,
            profile_facts=profile_facts,
            scrape_task_id=str(scrape_task_id),
            finished_at=finished_at,
        )
    return store.save_pipeline_result(
        result,
        merged_script_params,
        started_at=started_at,
        finished_at=finished_at,
        execution_config=execution_config,
        status=status,
        execution_params={
            "platform": platform,
            "scrape_task_id": str(scrape_task_id),
        },
    )


def save_scraped_only_round(
    store,
    source_jobs: list[dict],
    *,
    platform: str,
    scrape_task_id: str,
    profile_summary: str = "",
    profile_facts: dict | None = None,
    script_params: dict | None = None,
    execution_config: dict | None = None,
    started_at=None,
    finished_at=None,
) -> dict:
    """跳过筛选出口：把已完成的抓取任务固化为未筛选轮（幂等）。

    - 0 岗位不成轮，返回 ``{"saved": False, "run_id": None, "result": None}``。
    - 同流程已有未筛选轮：不落库，返回既有 run_id（幂等命中）。
    - 否则新建 scraped_only 轮。
    返回结构兼容旧 ``save_scrape_snapshot``：``{saved, run_id, result}``。
    """
    if not source_jobs:
        return {"saved": False, "run_id": None, "result": None}
    result = build_undecided_result(
        source_jobs,
        platform=platform,
        profile_summary=profile_summary,
        profile_facts=profile_facts,
    )
    existing = _existing_scraped_only_for_flow(store, scrape_task_id, platform)
    if existing is not None:
        # 幂等命中：轮已存在，结果可用（saved=True，前端展示 result）
        result["source_run_id"] = str(existing["id"])
        return {"saved": True, "run_id": str(existing["id"]), "result": result}
    run_id = store.save_scraped_only_snapshot(
        result,
        dict(script_params or {"platform": platform}),
        scrape_task_id=str(scrape_task_id),
        started_at=started_at,
        finished_at=finished_at,
        execution_config=execution_config or {},
        platform=platform,
        profile_summary=profile_summary,
        profile_facts=profile_facts,
    )
    result["source_run_id"] = run_id
    return {"saved": True, "run_id": run_id, "result": result}


def apply_recrawl_writeback(
    store,
    run_id: str,
    verdicts: dict,
    *,
    source_run_id: str = "",
) -> dict | None:
    """重抓 / 补筛完成后把判定回写目标轮并刷新定稿时间。

    - 判定写回目标轮（``save_screening_verdicts``，upsert）。
    - 已补救成功的岗位（match/not_match）从待确认表移除
      （``delete_pending_result``，未判定成功的 pending 保留）。
    - 计数重算并刷新 ``finished_at``（``recount_pipeline_result``）。
    返回 recount 结果；目标轮不存在时返回 None。
    """
    if not run_id:
        return None
    if verdicts:
        store.save_screening_verdicts(run_id, verdicts)
        if source_run_id:
            for job_id, verdict in verdicts.items():
                v = verdict.get("verdict") if isinstance(verdict, dict) else verdict
                if v in ("match", "not_match"):
                    store.delete_pending_result(source_run_id, job_id)
    return store.recount_pipeline_result(run_id)


# ---------------------------------------------------------------------------
# 私有：防重查询与参数合并
# ---------------------------------------------------------------------------

def _existing_round_for_flow(store, scrape_task_id: str, platform: str) -> dict | None:
    """同流程（scrape_task_id + platform）已有可见轮则返回，否则 None。

    判定完成类轮（done/partial）按来源扫描历史；未筛选轮（scraped_only）
    走专用来源查询。防重唯一执行点（FR-008）。
    """
    if not scrape_task_id:
        return None
    scraped = _existing_scraped_only_for_flow(store, scrape_task_id, platform)
    if scraped is not None:
        return scraped
    for run in store.list_history_rounds(str(platform) or None):
        if str(run.get("status") or "") not in VISIBLE_STATUSES:
            continue
        if str(run.get("platform") or "") != str(platform or ""):
            continue
        params = _decode_params(run.get("execution_params_json"))
        if str(params.get("scrape_task_id") or "") == str(scrape_task_id):
            return run
    return None


def _existing_scraped_only_for_flow(
    store, scrape_task_id: str, platform: str,
) -> dict | None:
    """同流程（scrape_task_id + platform）已有未筛选轮则返回，否则 None。"""
    if not scrape_task_id:
        return None
    row = store.latest_scraped_only_for_source(scrape_task_id)
    if row is not None and str(row.get("platform") or "") == str(platform or ""):
        return row
    return None


def _merge_parent_script_params(store, scrape_task_id: str, script_params: dict) -> dict:
    """合并父抓取的 script_params，保留父轮关键词/城市（与旧 save_screen_result 一致）。"""
    merged = dict(script_params or {})
    if scrape_task_id:
        parent = store.get_screening_run(scrape_task_id) or {}
        parent_script = (parent.get("execution_params") or {}).get("script_params") or {}
        if isinstance(parent_script, dict):
            merged = {**parent_script, **merged}
    return merged


def _decode_params(value: Any) -> dict:
    try:
        params = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError):
        params = {}
    return params if isinstance(params, dict) else {}
