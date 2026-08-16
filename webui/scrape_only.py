"""Scrape-only orchestration: 跳过 AI 直接查看结果的保存与补筛升级。

分层：app.py（路由）→ 本模块（编排）→ store mixin（数据访问）。
本模块不依赖 app.py 内部状态，可独立测试。
"""

from __future__ import annotations

from typing import Any


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


def save_scrape_snapshot(
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
    """把已完成的抓取任务固化为未筛选轮。

    0 岗位时不落库，返回 ``{saved: False}``；有岗位时保存并返回
    ``{saved: True, run_id, result}``。
    """
    if not source_jobs:
        return {"saved": False}
    result = build_undecided_result(
        source_jobs,
        platform=platform,
        profile_summary=profile_summary,
        profile_facts=profile_facts,
    )
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


def save_screen_result(
    store,
    result: dict,
    script_params: dict,
    *,
    scrape_task_id: str,
    status: str = "done",
    execution_config: dict | None = None,
    platform: str = "",
    profile_summary: str = "",
    profile_facts: dict | None = None,
    started_at=None,
    finished_at=None,
) -> str:
    """AI 筛选完成后的落库分流：命中未筛选轮则升级，否则新建。

    补筛复用同一来源岗位：升级只重写同一 run_id（created_at 不变），
    不生成新的历史轮记录。profile 未显式传入时按 save_pipeline_result
    的既有习惯从 result 读取。
    """
    if profile_summary is None or not str(profile_summary).strip():
        profile_summary = str(result.get("profile_summary") or "")
    if profile_facts is None:
        profile_facts = result.get("profile_facts")
    merged_script_params = dict(script_params or {})
    if scrape_task_id:
        parent = store.get_screening_run(scrape_task_id) or {}
        parent_script = (parent.get("execution_params") or {}).get("script_params") or {}
        if isinstance(parent_script, dict):
            merged_script_params = {**parent_script, **merged_script_params}
    upgraded = store.latest_scraped_only_for_source(scrape_task_id)
    if upgraded is not None:
        return store.upgrade_scraped_run(
            str(upgraded["id"]),
            result,
            merged_script_params,
            status=status,
            execution_config=execution_config,
            platform=platform or str(upgraded.get("platform") or "boss"),
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


def build_screen_script_params(screening_fields: dict | None, platform: str) -> dict:
    """与现有 AI 保存一致的 script_params 形态（screening + platform）。"""
    return {"screening": dict(screening_fields or {}), "platform": platform}


def merge_round_script_params(
    parent_script_params: dict | None,
    screening_fields: dict | None,
    platform: str,
) -> dict:
    """合并父抓取 script_params 与 AI 筛选快照参数，保留父轮关键词/城市。"""
    return {
        **dict(parent_script_params or {}),
        "screening": dict(screening_fields or {}),
        "platform": platform,
    }
