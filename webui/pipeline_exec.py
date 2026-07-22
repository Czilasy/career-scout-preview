"""Three-stage pipeline execution layer (stage 3).

Expands the confirmed multi-select search params into keyword × city
combinations, runs the BOSS CDP scraper for each combination (reusing the
scraper's built-in anti-rate-limit protections: random page delays,
human-like scrolling, request caps, circuit breaker), merges and dedups
the results, then applies the multi-select filters as a local post-filter.

The scraper subprocess enforces per-search rate limiting on its own.  This
layer adds a random delay BETWEEN combinations so consecutive searches are
never back-to-back, absorbing the same "slow is safe" philosophy.
"""

from __future__ import annotations

import random
import re
import time

from scripts import boss_cdp_raw as boss


# ---------------------------------------------------------------------------
# Auto-launch the debug Chrome (self-contained execution)
# ---------------------------------------------------------------------------

def ensure_chrome_ready(cdp_port: int | None = None) -> bool:
    """Ensure the dedicated debug Chrome is running; launch it if not.

    Returns True when CDP is reachable (already running or just launched).
    This makes execution self-contained: confirming the params auto-opens the
    browser in front of the user instead of surfacing a raw "CDP unavailable"
    infrastructure error.  Login is checked separately afterwards.
    """
    port = cdp_port or boss.DEFAULT_CDP_PORT
    if boss.is_cdp_ready(port):
        return True
    # Not running: prepare the isolated profile, stop stale processes, launch.
    profile = boss.prepare_cdp_profile()
    cdp_data_dir = profile["path"]
    try:
        boss.stop_cdp_chrome(cdp_data_dir)
    except Exception:
        pass
    cmd = [
        boss.DEFAULT_CHROME_PATH,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={cdp_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-allow-origins=*",
    ]
    boss.launch_chrome(cmd)
    # A cold Chrome start can take well over 30s to open the debug port, so
    # wait generously (90s) rather than giving up right before it comes up.
    return boss.wait_for_cdp(port, timeout=90)


def close_debug_chrome(cdp_port: int | None = None) -> bool:
    """Close the dedicated debug Chrome (best-effort).

    Uses ``boss.close_cdp_chrome``, which first verifies the port really is
    serving the scraper's isolated profile before closing — so the user's
    regular browser is never touched.  Called after a successful run so the
    automation browser doesn't linger in the taskbar.  A close failure is
    swallowed: it must never break an otherwise successful run.
    """
    port = cdp_port or boss.DEFAULT_CDP_PORT
    try:
        profile = boss.prepare_cdp_profile()
        return bool(boss.close_cdp_chrome(port, profile["path"]))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Search combination expansion
# ---------------------------------------------------------------------------

def split_keywords(keyword: str) -> list[str]:
    """Split a keyword string on Chinese/English commas into distinct terms."""
    if not keyword:
        return []
    parts = str(keyword).replace("，", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def expand_combinations(params: dict) -> list[dict]:
    """Expand confirmed params into a list of single keyword×city searches.

    ``params`` has ``keyword`` (comma string), ``city`` (list) and
    ``filters`` (dict of lists).  Returns one entry per (keyword, city)
    pair, each carrying the full multi-select ``filters`` for post-filtering.
    """
    keywords = split_keywords(params.get("keyword", ""))
    cities = params.get("city") or []
    if isinstance(cities, str):
        cities = [c.strip() for c in cities.replace("，", ",").split(",") if c.strip()]
    filters = params.get("filters") or {}
    combos = []
    for kw in keywords:
        for city in cities:
            combos.append({"keyword": kw, "city": city, "filters": filters})
    return combos


# ---------------------------------------------------------------------------
# Local post-filter: match a job against multi-select filter codes
# ---------------------------------------------------------------------------

def _job_scale_code(job: dict) -> str:
    return boss.SCALE_MAP.get((job.get("company_scale") or "").strip(), "")


def _job_stage_code(job: dict) -> str:
    return boss.STAGE_MAP.get((job.get("company_stage") or "").strip(), "")


def _job_industry_code(job: dict) -> str:
    industry = (job.get("company_industry") or "").strip()
    if industry in boss.INDUSTRY_MAP:
        return boss.INDUSTRY_MAP[industry]
    # Industry strings may be longer ("互联网 · 电商"); try prefix match.
    for name, code in boss.INDUSTRY_MAP.items():
        if name and name in industry:
            return code
    return ""


def _job_exp_degree_codes(job: dict) -> tuple[str, str]:
    """Extract experience and degree codes from the ``tags`` field.

    The scraper joins ``jobExperience`` and ``jobDegree`` into ``tags`` as
    e.g. ``"1-3年 | 本科"``.
    """
    tags = job.get("tags") or ""
    parts = [p.strip() for p in tags.split("|")]
    exp = ""
    deg = ""
    for p in parts:
        if p in boss.EXPERIENCE_MAP:
            exp = boss.EXPERIENCE_MAP[p]
        if p in boss.DEGREE_MAP:
            deg = boss.DEGREE_MAP[p]
    return exp, deg


def _job_salary_code(job: dict) -> str:
    """Best-effort mapping of a plaintext salary string to a SALARY_MAP code.

    Returns "" when the salary is unparseable (e.g. "面议"); callers treat
    an empty code as "unknown" and keep the job rather than dropping it.
    """
    salary = job.get("salary") or ""
    # 1. Direct substring match against band labels ("10-20K·13薪" -> "10-20K").
    for label, code in boss.SALARY_MAP.items():
        if label != "不限" and label in salary:
            return code
    # 2. Numeric fallback: use the lower bound of the first number found.
    nums = re.findall(r"\d+(?:\.\d+)?", salary)
    if not nums:
        return ""
    try:
        low = float(nums[0])
    except ValueError:
        return ""
    if low < 3:
        return "402"
    if low < 5:
        return "403"
    if low < 10:
        return "404"
    if low < 20:
        return "405"
    if low < 50:
        return "406"
    return "407"


def job_matches(job: dict, filters: dict) -> bool:
    """Return True iff *job* satisfies every selected multi-select filter.

    A filter dimension that the user left empty imposes no constraint.  A job
    whose value for a dimension is unknown/empty is kept (we avoid dropping
    jobs on missing data).
    """
    if not filters:
        return True

    scale_sel = filters.get("scale") or []
    if scale_sel:
        code = _job_scale_code(job)
        if code and code not in scale_sel:
            return False

    stage_sel = filters.get("stage") or []
    if stage_sel:
        code = _job_stage_code(job)
        if code and code not in stage_sel:
            return False

    industry_sel = filters.get("industry") or []
    if industry_sel:
        code = _job_industry_code(job)
        if code and code not in industry_sel:
            return False

    exp_sel = filters.get("experience") or []
    deg_sel = filters.get("degree") or []
    if exp_sel or deg_sel:
        exp_code, deg_code = _job_exp_degree_codes(job)
        if exp_sel and exp_code and exp_code not in exp_sel:
            return False
        if deg_sel and deg_code and deg_code not in deg_sel:
            return False

    salary_sel = filters.get("salary") or []
    if salary_sel:
        code = _job_salary_code(job)
        if code and code not in salary_sel:
            return False

    return True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

# Random delay range (seconds) inserted BETWEEN search combinations so
# consecutive searches are never back-to-back. Mirrors the scraper's own
# "slow is safe" rate-limit philosophy.
INTER_COMBO_DELAY = (20.0, 40.0)


def run_search(params: dict, source, *, pages: int = 3,
               progress=None, stop_event=None,
               artifact_dir=None, sleeper=None) -> dict:
    """Execute the multi-search pipeline and return merged, filtered jobs.

    ``source`` is a ``BossCdpSource`` (or compatible) providing ``preflight``
    and ``fetch_list``.  ``progress`` is an optional callable receiving a
    dict snapshot after each step.  ``stop_event`` (threading.Event-like)
    aborts the run when set.

    Returns ``{"ok": bool, "jobs": [...], "total_scraped": int,
    "total_matched": int, "combinations": int, "error": str}``.
    """
    if sleeper is None:
        sleeper = time.sleep

    combos = expand_combinations(params)

    def emit(**kw):
        if progress is not None:
            try:
                progress(kw)
            except Exception:
                pass

    if not combos:
        return {"ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": 0,
                "error": "没有可执行的搜索组合（关键词或城市为空）"}

    # Auto-launch the debug Chrome if it isn't running, so the user is shown
    # the browser instead of a raw infrastructure error.
    emit(stage="ensure_chrome", message="检查并启动调试浏览器…")
    if not ensure_chrome_ready():
        return {"ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": len(combos),
                "error": "调试浏览器未能在预期时间内就绪。如果已弹出 Chrome 窗口，请等它完全加载后再次点击确认参数；"
                         "若始终无法启动，请手动运行 scripts/boss_cdp_raw.py --setup-chrome 后重试。"}

    # Preflight: CDP connection + BOSS login.
    emit(stage="preflight", message="检查 BOSS 登录状态…")
    pre = source.preflight()
    if not pre.ok:
        if pre.failed_code == "source_login_required":
            msg = "浏览器已打开，但还未登录 BOSS。请在刚打开的浏览器窗口中登录 zhipin.com，登录后重新点击确认参数。"
        else:
            msg = f"预检失败：{pre.failed_code}"
        return {"ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": len(combos),
                "error": msg}

    merged: dict[str, dict] = {}
    total_scraped = 0

    for idx, combo in enumerate(combos):
        if stop_event is not None and stop_event.is_set():
            emit(stage="cancelled", message="运行已取消")
            break

        kw = combo["keyword"]
        city = combo["city"]
        emit(stage="searching", current=idx + 1, total=len(combos),
             keyword=kw, city=city,
             message=f"正在搜索 [{idx + 1}/{len(combos)}] {kw} · {city}")

        plan_item = {
            "keyword": kw,
            "city": city,
            "source_filters": {},  # broad search; multi-select applied as post-filter
            "target_pages": pages,
            "input_hash": _combo_hash(kw, city, pages),
            "list_output_path": _combo_output_path(artifact_dir, kw, city),
        }
        outcome = source.fetch_list(plan_item)
        if not outcome.ok:
            emit(stage="combo_failed", current=idx + 1, total=len(combos),
                 keyword=kw, city=city, failed_code=outcome.failed_code,
                 message=f"组合失败：{outcome.failed_code}")
        else:
            total_scraped += len(outcome.jobs)
            for job in outcome.jobs:
                jid = job.get("job_id") or job.get("source_url") or ""
                if jid and jid not in merged:
                    merged[jid] = job
            emit(stage="combo_done", current=idx + 1, total=len(combos),
                 keyword=kw, city=city, scraped=len(outcome.jobs),
                 merged=len(merged),
                 message=f"完成 {kw} · {city}：本页 {len(outcome.jobs)} 条，累计去重 {len(merged)} 条")

        # Delay between combinations (not after the last one).
        if idx < len(combos) - 1:
            if stop_event is not None and stop_event.is_set():
                break
            delay = random.uniform(*INTER_COMBO_DELAY)
            emit(stage="waiting", current=idx + 1, total=len(combos),
                 wait_seconds=int(delay),
                 message=f"防限流等待 {delay:.0f}s 后搜索下一个组合…")
            sleeper(delay)

    # 广搜策略：不做本地硬筛选，全量返回，筛选交给后续 AI 步骤。
    all_jobs = list(merged.values())
    emit(stage="done", total_scraped=total_scraped, total_matched=len(all_jobs),
         message=f"完成：抓取 {total_scraped} 条，去重 {len(all_jobs)} 条")

    # 运行成功后主动关闭调试浏览器，不让它留在任务栏。
    # 失败路径（尤其未登录）不关，保留窗口给用户登录/重试。
    emit(stage="closing_chrome", message="正在关闭调试浏览器…")
    close_debug_chrome()

    return {"ok": True, "jobs": all_jobs, "total_scraped": total_scraped,
            "total_matched": len(all_jobs), "combinations": len(combos),
            "error": ""}


def fetch_job_details(jobs, source, *, artifact_dir=None, progress=None):
    """对一批岗位批量抓 JD（调用方需先确保 Chrome 就绪）。返回带 jd 的岗位列表。

    Spec 007 ⑧：改用 fetch_details_batch（≤5 一批）走 --enable-parallel 常驻 tab 池，
    替代旧的逐条 fetch_detail。单条失败不中断（该岗位 jd 留空，前端可保留按需加载兜底）。
    ``progress(done, total)`` 按累计完成数回报。
    """
    import os
    if artifact_dir is None:
        artifact_dir = os.path.join(os.path.expanduser("~"), ".career-scout", "job-result")
    os.makedirs(artifact_dir, exist_ok=True)
    total = len(jobs)
    if total == 0:
        return []
    BATCH_SIZE = 5
    # 预先为每个 job 计算稳定 job_id（与 fetch_details_batch 内部 key 一致），
    # 缺 job_id 的 job 填充 idx{idx} 兜底，确保 batch 返回的 outcome 能映射回原 job。
    indexed_jobs = []
    for idx, job in enumerate(jobs):
        if not isinstance(job, dict):
            indexed_jobs.append((idx, f"idx{idx}", {}))
            continue
        jid = str(job.get("job_id") or job.get("id") or "").strip()
        if not jid:
            jid = f"idx{idx}"
        indexed_jobs.append((idx, jid, dict(job, job_id=jid)))
    jd_by_idx = {}
    done = 0
    for batch_start in range(0, len(indexed_jobs), BATCH_SIZE):
        batch = indexed_jobs[batch_start:batch_start + BATCH_SIZE]
        batch_jobs = [job for _, _, job in batch]
        batch_path = os.path.join(
            artifact_dir, f"pipeline_batch_{batch_start}_{time.time_ns()}.json"
        )
        try:
            outcomes = source.fetch_details_batch(
                batch_jobs,
                detail_output_path=batch_path,
                max_batch_size=BATCH_SIZE,
            )
        except Exception:
            outcomes = {}
        for idx, jid, _ in batch:
            outcome = outcomes.get(jid)
            jd = ""
            if outcome is not None and outcome.ok and isinstance(outcome.detail, dict):
                jd = str(outcome.detail.get("jd", "")).strip()
            jd_by_idx[idx] = jd
            done += 1
            if progress is not None:
                try:
                    progress(done, total)
                except Exception:
                    pass
    enriched = []
    for idx, job in enumerate(jobs):
        e = dict(job) if isinstance(job, dict) else {}
        e["jd"] = jd_by_idx.get(idx, "")
        enriched.append(e)
    return enriched


def _combo_hash(keyword: str, city: str, pages: int) -> str:
    import hashlib
    import json
    blob = json.dumps({"keyword": keyword, "city": city, "target_pages": pages,
                       "source_filters": {}}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _combo_output_path(artifact_dir, keyword: str, city: str) -> str:
    import os
    import re as _re
    if artifact_dir is None:
        base = os.path.join(os.path.expanduser("~"), ".career-scout", "job-result")
    else:
        base = str(artifact_dir)
    os.makedirs(base, exist_ok=True)
    safe_kw = _re.sub(r"[^\w\u4e00-\u9fff]", "", keyword)[:20] or "kw"
    safe_city = _re.sub(r"[^\w\u4e00-\u9fff]", "", city)[:10] or "city"
    return os.path.join(base, f"pipeline_{safe_kw}_{safe_city}_{time.time_ns()}.json")
