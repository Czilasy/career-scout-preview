from __future__ import annotations

def build_partial_pipeline_result(
        source_jobs, verdicts, pending_rows, jd_map, profile_summary,
        source_dropped=None, total_scraped=None, platform="",
        profile_facts=None, unfiltered=False):
    """Build a displayable result snapshot from persisted partial work."""
    pending_reasons = {}
    pending_codes = {}
    for item in pending_rows or []:
        jid = str(item.get("job_id") or "")
        if not jid:
            continue
        payload = item.get("ai_payload") or {}
        pending_reasons[jid] = str(
            payload.get("reason") or item.get("failed_code") or "")
        pending_codes[jid] = str(item.get("failed_code") or "")
    jobs = []
    dropped = []
    for job in source_jobs or []:
        if not isinstance(job, dict):
            continue
        jid = str(job.get("job_id") or job.get("source_url") or "")
        vobj = verdicts.get(jid) or {}
        verdict = str(vobj.get("verdict") or "")
        reason = str(vobj.get("reason") or job.get("verdict_reason") or "")
        if verdict == "dropped":
            dropped.append({
                "platform": platform,
                "platform_job_id": str(job.get("platform_job_id") or jid),
                "job_id": str(job.get("job_id") or "") or None,
                "title": job.get("title") or "", "reason": reason or "粗筛移除",
                "canonical_url": job.get("source_url") or job.get("job_link") or "",
            })
            continue
        jd = str(jd_map.get(jid) or job.get("jd") or "").strip()
        caveats = (
            vobj.get("caveats") if isinstance(vobj.get("caveats"), list)
            else (job.get("caveats") if isinstance(job.get("caveats"), list) else [])
        )
        flags = (
            vobj.get("flags") if isinstance(vobj.get("flags"), list)
            else (job.get("flags") if isinstance(job.get("flags"), list) else [])
        )
        if verdict in ("match", "not_match", "mismatch"):
            final_verdict = "not_match" if verdict == "mismatch" else verdict
            final_reason = reason
        elif jd:
            final_verdict = "uncertain"
            final_reason = reason or "已抓取 JD，精筛未完成（提前结束）"
        else:
            final_verdict = "uncertain"
            final_reason = (
                pending_reasons.get(jid)
                or reason
                or "未开始抓取 JD（提前结束）"
            )
        jobs.append({
            "platform": platform,
            "platform_job_id": str(job.get("platform_job_id") or jid),
            "job_id": str(job.get("job_id") or "") or None,
            "title": job.get("title") or "",
            "company": job.get("company") or job.get("boss_name") or "",
            "salary": job.get("salary") or "",
            "location": job.get("location") or "",
            "tags": job.get("tags") or "",
            "jd": jd,
            "source_url": job.get("source_url") or job.get("job_link") or "",
            "verdict": final_verdict,
            "verdict_reason": final_reason,
            "caveats": caveats,
            "flags": flags,
            "failed_code": pending_codes.get(jid) or "",
        })
    dropped_ids = {str(item.get("platform_job_id") or item.get("job_id") or "") for item in dropped}
    for item in source_dropped or []:
        if not isinstance(item, dict):
            continue
        jid = str(item.get("platform_job_id") or item.get("job_id") or item.get("source_url") or "")
        if jid and jid in dropped_ids:
            continue
        dropped.append({
            "platform": platform,
            "platform_job_id": str(item.get("platform_job_id") or jid),
            "job_id": str(item.get("job_id") or "") or None,
            "title": item.get("title") or "",
            "reason": item.get("reason") or item.get("verdict_reason") or "粗筛移除",
            "canonical_url": item.get("canonical_url") or item.get("source_url") or "",
        })
    return {
        "ok": True,
        "jobs": jobs,
        "dropped": dropped,
        "total_scraped": (
            total_scraped if total_scraped is not None
            else len(source_jobs or []) + len(source_dropped or [])
        ),
        "total_kept": 0 if unfiltered else len(jobs),
        "total_matched": sum(1 for j in jobs if j.get("verdict") == "match"),
        "total_dropped": len(dropped),
        "profile_summary": profile_summary or "",
        "profile_facts": profile_facts,
        "error": "",
    }
