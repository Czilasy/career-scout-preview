"""Round 2 controlled experiment: find match_concurrency ceiling.

Round 1 result: all 4 groups had 429=0 with match_concurrency=6. This means
we never pushed to the boundary. Round 2 increases match_concurrency across
4 gradient groups (10/15/20/30) to find where 429 starts firing.

Variable (only one): match_concurrency
Fixed base (validated safe in round 1):
  - detail_batch_size=15, detail_interval=2, detail_batch_cooldown=5
    (round-1 group 2/4 had code:37=0, safe to use as base)
  - match_batch_size=4 (avoid round-1 group 3/4 uncertain warning interference)
  - screen_concurrency=3 (not testing this round)
  - pages=3 (~90 jobs sample)

Groups:
  C1: match_concurrency=10  (frontend upper bound)
  C2: match_concurrency=15  (break frontend)
  C3: match_concurrency=20  (high concurrency)
  C4: match_concurrency=30  (find ceiling — even if sample can't saturate,
                              429 firing is still a valid signal)

Usage:
    python _orchestrator_round2.py          # run all 4 groups
    python _orchestrator_round2.py 2        # run only group C2
"""
from __future__ import annotations

import json
import os
import sys
import time
import threading
import traceback
from pathlib import Path

# ============================================================
# Project path setup — script is in experiment_results/round2/, so go up 2 levels
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

STATE_DIR = Path(os.environ.get(
    "BOSS_WEBUI_STATE_DIR",
    str(Path.home() / ".career-scout" / "webui"),
))
ADVANCED_SETTINGS_PATH = STATE_DIR / "advanced_settings.json"
DB_PATH = STATE_DIR / "webui.db"
RESULT_DIR = Path.home() / ".career-scout" / "job-result"
RESULT_DIR.mkdir(parents=True, exist_ok=True)

EXPERIMENT_DIR = PROJECT_ROOT / "experiment_results" / "round2"
EXPERIMENT_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# Constants — fixed base (validated safe in round 1)
# ============================================================
# Detail params: round-1 group 2/4 validated code:37=0, safe as fixed base
FIXED_DETAIL = {
    "detail_batch_size": 15,
    "detail_interval": 2,
    "detail_batch_cooldown": 5,
}

# Other fixed params (not variables this round)
COMMON_SETTINGS = {
    "pages": 3,
    "inter_combo_delay": 10.0,
    "detail_reset_every": 4,
    "screen_batch_size": 50,
    "screen_concurrency": 3,        # not testing this round
    "match_batch_size": 4,           # avoid round-1 uncertain warning interference
}

# The ONLY variable this round: match_concurrency
GROUPS = [
    {"n": 1, "name": "c1_concurrency_10", "match_concurrency": 10},
    {"n": 2, "name": "c2_concurrency_15", "match_concurrency": 15},
    {"n": 3, "name": "c3_concurrency_20", "match_concurrency": 20},
    {"n": 4, "name": "c4_concurrency_30", "match_concurrency": 30},
]

# Fixed sample
KEYWORD = "AI应用开发"
CITY = "东莞"
PAGES = 3

# Reuse round-1 profile/fields (real user conditions)
PROFILE_SUMMARY = (
    "候选人具有约1年AI应用开发工作经验，毕业于计算机科学与技术本科（专升本），"
    "求职意向为全职AI应用开发/智能体方向岗位。核心技能包括Python后端（FastAPI）、"
    "多智能体编排（LangGraph）、RAG系统构建（RAGFlow/MaxKB）以及Vue3前端，"
    "能独立完成全栈AI系统设计与交付。"
)
SCREENING_FIELDS = {
    "salary": ["403", "404", "405"],
    "experience": ["103", "101", "104"],
    "degree": ["203", "202"],
    "industry": [],
    "scale": ["301", "302", "303", "304"],
    "stage": [],
}


# ============================================================
# Metrics collector (thread-safe) — same as round 1
# ============================================================
class MetricsCollector:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.count_429 = 0
            self.count_node_switch = 0
            self.count_code37 = 0
            self.total_input_tokens = 0
            self.total_output_tokens = 0
            self.ai_call_count = 0
            self.ai_call_total_sec = 0.0
            self.ai_call_durations = []
            self.jd_failed_count = 0
            self.jd_fetched_count = 0
            self.subprocess_logs = []

    def record_ai_call(self, duration_sec, hit_429=False,
                       input_tokens=0, output_tokens=0):
        with self.lock:
            self.ai_call_count += 1
            self.ai_call_total_sec += duration_sec
            self.ai_call_durations.append(duration_sec)
            if hit_429:
                self.count_429 += 1
            self.total_input_tokens += int(input_tokens)
            self.total_output_tokens += int(output_tokens)

    def record_subprocess_log(self, safe_log):
        with self.lock:
            if safe_log:
                self.subprocess_logs.append(str(safe_log))
                if "code:37" in str(safe_log) or "code=37" in str(safe_log) \
                   or "code: 37" in str(safe_log):
                    self.count_code37 += 1

    def record_jd(self, fetched=0, failed=0):
        with self.lock:
            self.jd_fetched_count += fetched
            self.jd_failed_count += failed

    def snapshot(self):
        with self.lock:
            return {
                "count_429": self.count_429,
                "count_node_switch": self.count_node_switch,
                "count_code37": self.count_code37,
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "ai_call_count": self.ai_call_count,
                "ai_per_batch_sec": round(
                    self.ai_call_total_sec / self.ai_call_count, 2
                ) if self.ai_call_count else 0.0,
                "jd_failed_count": self.jd_failed_count,
                "jd_fetched_count": self.jd_fetched_count,
            }


# ============================================================
# Instrumentation: same as round 1
# ============================================================
def install_instrumentation(metrics: MetricsCollector):
    import webui.ai as ai_service

    original_post = ai_service.requests.post

    def wrapped_post(url, *args, **kwargs):
        resp = original_post(url, *args, **kwargs)
        try:
            url_str = str(url)
            if "chat/completions" in url_str:
                if resp.status_code == 429:
                    with metrics.lock:
                        metrics.count_429 += 1
                elif resp.status_code == 200:
                    try:
                        body = resp.json()
                        usage = body.get("usage") or {}
                        with metrics.lock:
                            metrics.total_input_tokens += int(usage.get("prompt_tokens") or 0)
                            metrics.total_output_tokens += int(usage.get("completion_tokens") or 0)
                    except Exception:
                        pass
        except Exception:
            pass
        return resp

    ai_service.requests.post = wrapped_post

    original_call_ai = ai_service.call_ai

    def wrapped_call_ai(endpoint_url, api_key, messages,
                        timeout=ai_service.DEFAULT_TIMEOUT,
                        temperature=0.3, model=""):
        t0 = time.time()
        try:
            return original_call_ai(endpoint_url, api_key, messages,
                                     timeout=timeout,
                                     temperature=temperature, model=model)
        finally:
            duration = time.time() - t0
            metrics.record_ai_call(duration, hit_429=False)

    ai_service.call_ai = wrapped_call_ai


# ============================================================
# Helpers
# ============================================================
def write_group_settings(group: dict):
    """Write group's advanced_settings.json (fixed base + this group's concurrency)."""
    settings = dict(COMMON_SETTINGS)
    settings.update(FIXED_DETAIL)
    settings["match_concurrency"] = group["match_concurrency"]
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(ADVANCED_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    return settings


def get_ai_credentials():
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT endpoint_url, model, credential_ref FROM ai_settings WHERE id=1"
    ).fetchone()
    conn.close()
    if not row:
        raise RuntimeError("ai_settings table empty")
    cred_ref = row["credential_ref"]
    import keyring
    api_key = keyring.get_password("boss-workbench", cred_ref) or ""
    if not api_key:
        raise RuntimeError(f"api_key not found in keyring for cred_ref={cred_ref}")
    return {
        "endpoint_url": row["endpoint_url"],
        "model": row["model"],
        "api_key": api_key,
    }


def make_cdp_source():
    py = sys.executable
    from webui.source import BossCdpSource
    return BossCdpSource(
        python_executable=py,
        artifact_root=str(RESULT_DIR),
    )


# ============================================================
# Per-group pipeline runner (mirrors round-1 orchestrator)
# ============================================================
def run_group(group: dict, metrics: MetricsCollector) -> dict:
    import webui.ai as ai_service
    import webui.pipeline_exec as pe

    group_n = group["n"]
    print(f"\n{'=' * 70}")
    print(f"  GROUP C{group_n}: {group['name'].upper()}")
    print(f"  match_concurrency={group['match_concurrency']}  (the ONLY variable)")
    print(f"  detail=15/2/5  match_batch_size=4  screen_concurrency=3  (fixed base)")
    print(f"{'=' * 70}")

    settings_used = write_group_settings(group)
    print(f"[C{group_n}] settings written: {settings_used}")

    metrics.reset()
    ai_cred = get_ai_credentials()
    print(f"[C{group_n}] AI endpoint={ai_cred['endpoint_url']} model={ai_cred['model']}")

    criteria = dict(SCREENING_FIELDS)
    criteria["city"] = [CITY]
    criteria["profile_summary"] = PROFILE_SUMMARY

    result = {
        "group": group_n,
        "name": group["name"],
        "round": 2,
        "variable": "match_concurrency",
        "match_concurrency": group["match_concurrency"],
        "config": settings_used,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "aborted": False,
        "abort_reason": "",
    }

    t_total_start = time.time()

    # ============================================================
    # Stage 0: List page scraping
    # ============================================================
    print(f"\n[C{group_n}] === Stage 0: list page scraping ===")
    t0 = time.time()
    source = make_cdp_source()
    params = {
        "keyword": KEYWORD,
        "city": [CITY],
        "filters": {},
    }

    def progress_cb(kw):
        stage = kw.get("stage", "")
        msg = kw.get("message", "")
        if msg:
            print(f"  [list] {stage}: {msg}")

    search_result = pe.run_search(
        params, source,
        pages=PAGES,
        progress=progress_cb,
    )
    t_listpage = time.time() - t0
    listpage_duration_sec = round(t_listpage, 2)

    if not search_result.get("ok"):
        result["error"] = f"list_scrape_failed: {search_result.get('error', '')}"
        result["total_duration_sec"] = round(time.time() - t_total_start, 2)
        result["listpage_duration_sec"] = listpage_duration_sec
        result["aborted"] = True
        result["abort_reason"] = "list_scrape_failed"
        return result

    raw_jobs = search_result.get("jobs", [])
    print(f"[C{group_n}] list scrape ok: {len(raw_jobs)} jobs in {listpage_duration_sec}s")

    # ============================================================
    # Stage A: screen_jobs (恒定量 — 验证环境干扰用)
    # ============================================================
    print(f"\n[C{group_n}] === Stage A: AI coarse screening (恒定量) ===")
    t0 = time.time()

    def _a_progress(cur, tot):
        if cur % 10 == 0 or cur == tot:
            print(f"  [stage_a] {cur}/{tot}")

    screen_result = ai_service.screen_jobs(
        raw_jobs, criteria,
        ai_cred["endpoint_url"], ai_cred["api_key"],
        model=ai_cred["model"],
        progress=_a_progress,
    )
    t_stage_a = time.time() - t0
    stage_a_duration_sec = round(t_stage_a, 2)

    kept_ids = set(screen_result["kept"])
    survivors = [j for j in raw_jobs
                 if str(j.get("job_id", "")) in kept_ids]
    dropped = screen_result["dropped"]
    print(f"\n[C{group_n}] stage_a ok: kept={len(survivors)} dropped={len(dropped)} in {stage_a_duration_sec}s")

    # ============================================================
    # Stage 1: fetch JD
    # ============================================================
    print(f"\n[C{group_n}] === Stage 1: fetch JD ===")
    chrome_ok, chrome_err = pe.ensure_chrome_ready()
    if not chrome_ok:
        result["error"] = f"chrome_not_ready_for_jd: {chrome_err}"
        result["aborted"] = True
        result["abort_reason"] = "chrome_not_ready_for_jd"
        result["total_duration_sec"] = round(time.time() - t_total_start, 2)
        result["listpage_duration_sec"] = listpage_duration_sec
        result["stage_a_duration_sec"] = stage_a_duration_sec
        result["fetch_jd_duration_sec"] = 0.0
        return result

    source = make_cdp_source()
    t0 = time.time()

    def _jd_progress(done, total):
        if done % 10 == 0 or done == total:
            print(f"  [jd] {done}/{total}")

    detail_result = pe.fetch_job_details(
        survivors, source,
        artifact_dir=str(RESULT_DIR),
        progress=_jd_progress,
    )
    t_fetch_jd = time.time() - t0
    fetch_jd_duration_sec = round(t_fetch_jd, 2)

    pe.close_debug_chrome()

    enriched = detail_result.get("jobs", [])
    jd_fetched = sum(1 for j in enriched if str(j.get("jd", "")).strip())
    jd_failed = len(survivors) - jd_fetched
    metrics.record_jd(fetched=jd_fetched, failed=jd_failed)

    print(f"\n[C{group_n}] fetch_jd ok: fetched={jd_fetched} failed={jd_failed} in {fetch_jd_duration_sec}s")

    # Abort check: code:37 ≥3 (shouldn't trigger since detail params are validated safe,
    # but kept as safety)
    if metrics.count_code37 >= 3:
        print(f"[C{group_n}] ABORT: code:37 count={metrics.count_code37} ≥3")
        result["aborted"] = True
        result["abort_reason"] = f"code37_threshold_reached (count={metrics.count_code37})"

    # ============================================================
    # Stage B: match_jds — this is where match_concurrency variable matters
    # ============================================================
    if not result["aborted"]:
        print(f"\n[C{group_n}] === Stage B: AI JD precise screening (variable: match_concurrency={group['match_concurrency']}) ===")
        jobs_with_jd = [j for j in enriched if str(j.get("jd", "")).strip()]
        t0 = time.time()

        match_result = ai_service.match_jds(
            jobs_with_jd, PROFILE_SUMMARY,
            ai_cred["endpoint_url"], ai_cred["api_key"],
            model=ai_cred["model"],
        )
        t_stage_b = time.time() - t0
        stage_b_duration_sec = round(t_stage_b, 2)

        verdicts = match_result.get("verdicts", {})
        match_count = sum(1 for v in verdicts.values()
                          if v.get("verdict") == "match")
        mismatch_count = sum(1 for v in verdicts.values()
                             if v.get("verdict") == "not_match")
        uncertain_count = sum(1 for v in verdicts.values()
                              if v.get("verdict") == "uncertain")
        print(f"[C{group_n}] stage_b ok: match={match_count} mismatch={mismatch_count} uncertain={uncertain_count} in {stage_b_duration_sec}s")
    else:
        stage_b_duration_sec = 0.0
        match_count = mismatch_count = uncertain_count = 0

    # ============================================================
    # Compute final metrics
    # ============================================================
    total_duration_sec = round(time.time() - t_total_start, 2)
    snap = metrics.snapshot()

    jd_per_job_sec = round(t_fetch_jd / max(jd_fetched, 1), 2) if jd_fetched else 0.0

    cost_input = (snap["total_input_tokens"] / 1_000_000) * 0.14
    cost_output = (snap["total_output_tokens"] / 1_000_000) * 0.28
    total_cost_usd = round(cost_input + cost_output, 6)
    cost_per_job_usd = round(total_cost_usd / max(len(raw_jobs), 1), 6)

    result.update({
        "total_duration_sec": total_duration_sec,
        "listpage_duration_sec": listpage_duration_sec,
        "fetch_jd_duration_sec": fetch_jd_duration_sec,
        "stage_a_duration_sec": stage_a_duration_sec,
        "stage_b_duration_sec": stage_b_duration_sec,
        "jd_per_job_sec": jd_per_job_sec,
        "ai_per_batch_sec": snap["ai_per_batch_sec"],
        # stability
        "count_429": snap["count_429"],
        "count_code37": snap["count_code37"],
        "count_node_switch": snap["count_node_switch"],
        "jd_failed_count": snap["jd_failed_count"],
        # quality
        "match_count": match_count,
        "mismatch_count": mismatch_count,
        "uncertain_count": uncertain_count,
        "dropped_count": len(dropped),
        "total_raw_jobs": len(raw_jobs),
        "total_survivors": len(survivors),
        "total_jd_fetched": jd_fetched,
        # cost
        "total_input_tokens": snap["total_input_tokens"],
        "total_output_tokens": snap["total_output_tokens"],
        "total_cost_usd": total_cost_usd,
        "cost_per_job_usd": cost_per_job_usd,
        # misc
        "ai_call_count": snap["ai_call_count"],
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    return result


# ============================================================
# Main entry
# ============================================================
def main():
    only_group = int(sys.argv[1]) if len(sys.argv) > 1 else None

    metrics = MetricsCollector()
    install_instrumentation(metrics)

    print("=" * 70)
    print("BOSS pipeline performance experiment — ROUND 2")
    print("  Goal: find match_concurrency ceiling (where 429 starts firing)")
    print(f"  Variable: match_concurrency (gradient 10/15/20/30)")
    print(f"  Fixed base: detail=15/2/5, match_batch_size=4, screen_concurrency=3")
    print(f"  keyword: {KEYWORD}")
    print(f"  city: {CITY}")
    print(f"  pages: {PAGES}")
    print(f"  state_dir: {STATE_DIR}")
    print(f"  result_dir: {RESULT_DIR}")
    print(f"  groups: {[g['n'] for g in GROUPS if only_group is None or g['n'] == only_group]}")
    print("=" * 70)

    # Backup advanced_settings.json (don't overwrite existing backup if present)
    bak = ADVANCED_SETTINGS_PATH.parent / "advanced_settings.json.bak.round2"
    if not bak.is_file() and ADVANCED_SETTINGS_PATH.is_file():
        import shutil
        shutil.copyfile(str(ADVANCED_SETTINGS_PATH), str(bak))
        print(f"[backup] saved {bak}")
    elif bak.is_file():
        print(f"[backup] using existing {bak}")

    for group in GROUPS:
        if only_group is not None and group["n"] != only_group:
            continue
        try:
            result = run_group(group, metrics)
        except Exception as exc:
            print(f"\n[FATAL C{group['n']}] {type(exc).__name__}: {exc}")
            traceback.print_exc()
            result = {
                "group": group["n"],
                "name": group["name"],
                "round": 2,
                "aborted": True,
                "abort_reason": f"unhandled_exception: {type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }

        out_file = EXPERIMENT_DIR / f"group_C{group['n']}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n[C{group['n']}] result written to {out_file}")
        print(f"  total={result.get('total_duration_sec', 'N/A')}s "
              f"429={result.get('count_429', 'N/A')} "
              f"aborted={result.get('aborted', False)}")

        # Inter-group cooldown
        if group["n"] < 4:
            print(f"\n[cooldown] sleeping 60s before next group...")
            time.sleep(60)

    print("\n" + "=" * 70)
    print("Round 2 complete. Restoring advanced_settings.json...")
    print("=" * 70)
    bak = ADVANCED_SETTINGS_PATH.parent / "advanced_settings.json.bak.round2"
    if bak.is_file():
        import shutil
        shutil.copyfile(str(bak), str(ADVANCED_SETTINGS_PATH))
        print(f"restored from {bak}")
    else:
        print(f"WARN: backup not found at {bak}")


if __name__ == "__main__":
    main()
