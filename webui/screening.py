"""002 resume-driven screening: run state machine and filter snapshot.

This module hosts the screening logic layered on top of the 001 workbench.
Phase 2 (T008) implements only the state machine and filter snapshot; later
phases add filter-option enums, hard-rule verification, and orchestration.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
from pathlib import Path

from scripts import boss_cdp_raw as boss

# T031 在 webui.ai 中实现 AI 语义相似度占位；T030 实现分流前需要该函数可调用。
# T031 完成前用本地兜底占位（恒返回 match），T031 完成后 try 分支生效。
try:
    from webui.ai import assess_semantic_similarity
except ImportError:  # pragma: no cover - T031 完成后此分支不再执行
    def assess_semantic_similarity(resume_text, jd_text):
        return {"verdict": "match"}


# 反向映射：filter code -> 中文标签（硬规则核验时用）
_SALARY_REVERSE = {v: k for k, v in boss.SALARY_MAP.items()}
_EXP_REVERSE = {v: k for k, v in boss.EXPERIENCE_MAP.items()}
_DEGREE_REVERSE = {v: k for k, v in boss.DEGREE_MAP.items()}
_SCALE_REVERSE = {v: k for k, v in boss.SCALE_MAP.items()}
_STAGE_REVERSE = {v: k for k, v in boss.STAGE_MAP.items()}
_INDUSTRY_REVERSE = {v: k for k, v in boss.INDUSTRY_MAP.items()}


# ---------------------------------------------------------------------------
# Execution run state machine
# ---------------------------------------------------------------------------

STATUSES = ("queued", "running", "succeeded", "partial", "failed", "interrupted")
TERMINAL_STATUSES = ("succeeded", "partial", "failed", "interrupted")
INTERRUPTIBLE_STATUSES = ("queued", "running")

# Legal transitions. Terminal statuses have no outgoing transitions.
# queued -> running (start), queued -> interrupted (restart before start)
# running -> succeeded (all verified), partial (some verify errors),
#             failed (execution error), interrupted (cancel/restart)
_TRANSITIONS = {
    "queued": {"running", "interrupted"},
    "running": {"succeeded", "partial", "failed", "interrupted"},
}


def is_valid_transition(from_status, to_status) -> bool:
    """Return True iff from_status -> to_status is a legal state transition."""
    if from_status not in STATUSES or to_status not in STATUSES:
        return False
    if from_status in TERMINAL_STATUSES:
        return False
    return to_status in _TRANSITIONS.get(from_status, set())


def validate_transition(from_status, to_status) -> None:
    """Raise ValueError if from_status -> to_status is not legal."""
    if from_status not in STATUSES:
        raise ValueError(f"unknown source status: {from_status!r}")
    if to_status not in STATUSES:
        raise ValueError(f"unknown target status: {to_status!r}")
    if not is_valid_transition(from_status, to_status):
        raise ValueError(f"invalid transition: {from_status} -> {to_status}")


# ---------------------------------------------------------------------------
# Filter snapshot
# ---------------------------------------------------------------------------

ALLOWED_FILTER_KEYS = ("city", "salary", "experience", "degree", "scale", "stage", "industry")


def is_valid_filters(filters) -> bool:
    """Return True iff filters only contains allowed keys (all optional)."""
    if not isinstance(filters, dict):
        return False
    return set(filters.keys()).issubset(set(ALLOWED_FILTER_KEYS))


def freeze_filters(filters) -> dict:
    """Return a frozen snapshot of filter conditions.

    Deep-copies the input, keeps only allowed keys (empty strings preserved
    to faithfully record which fields the user left blank), and strips any
    disallowed keys so downstream verification trusts the snapshot.
    """
    if not isinstance(filters, dict):
        raise ValueError("filters must be a dict")
    snapshot = {}
    for key in ALLOWED_FILTER_KEYS:
        if key in filters:
            snapshot[key] = copy.deepcopy(filters[key])
    return snapshot


# ---------------------------------------------------------------------------
# Filter option enums (sourced from scripts.boss_cdp_raw maps)
# ---------------------------------------------------------------------------

def build_screening_filter_options() -> dict:
    """Return 7-class filter option enums for the screening UI.

    Classes: salary/experience/degree/scale/stage/industry/city. Each class
    starts with {"label": "不限", "value": ""} to reflect the no-required-
    fields rule. Values come from boss_cdp_raw maps; "0" entries excluded.
    City uses city name as value (matches 001 convention); "全国" is omitted
    because "不限" already means nationwide.
    """
    options = {}
    for name, mapping in (
        ("salary", boss.SALARY_MAP),
        ("experience", boss.EXPERIENCE_MAP),
        ("degree", boss.DEGREE_MAP),
        ("scale", boss.SCALE_MAP),
        ("stage", boss.STAGE_MAP),
        ("industry", boss.INDUSTRY_MAP),
    ):
        options[name] = [{"label": "不限", "value": ""}] + [
            {"label": label, "value": value}
            for label, value in mapping.items()
            if value != "0"
        ]
    options["city"] = [{"label": "不限", "value": ""}] + [
        {"label": name, "value": name}
        for name in boss.CITY_MAP
        if name != "全国"
    ]
    return options


# ---------------------------------------------------------------------------
# Filter merging (user value precedence over AI suggestion)
# ---------------------------------------------------------------------------

def merge_filters(user_filters, ai_suggest) -> dict:
    """Merge user-confirmed filters with AI suggestions.

    Rule per field: user value (non-empty) takes precedence; otherwise AI
    value (non-empty); otherwise empty string. Output always has all seven
    allowed keys. Disallowed keys in either input are ignored.
    """
    user = user_filters or {}
    ai = ai_suggest or {}
    merged = {}
    for key in ALLOWED_FILTER_KEYS:
        u = user.get(key, "")
        a = ai.get(key, "")
        if isinstance(u, str) and u.strip():
            merged[key] = u
        elif isinstance(a, str) and a.strip():
            merged[key] = a
        else:
            merged[key] = ""
    return merged


# ---------------------------------------------------------------------------
# First-layer search: filter mapping + scraper orchestration
# ---------------------------------------------------------------------------

def filters_to_search_params(filters) -> dict:
    """Map confirmed screening filters to BOSS search parameters.

    city (name) -> city code; empty city -> nationwide code ("100010000").
    Non-empty filter codes collected into ``filters`` dict; empty ones
    excluded so the scraper only receives active filters.
    """
    frozen = freeze_filters(filters)
    city_name = frozen.get("city", "")
    if city_name in boss.CITY_MAP:
        city_code = boss.CITY_MAP[city_name]
    elif city_name in boss.CITY_R:
        city_code = city_name  # already a code
    else:
        city_code = boss.CITY_MAP["全国"]  # empty or unknown -> nationwide
    search_filters = {
        key: frozen[key]
        for key in ("scale", "stage", "salary", "experience", "degree", "industry")
        if frozen.get(key)
    }
    return {"city": city_code, "filters": search_filters}


def execute_first_layer(filters, keyword, *, output_path, python_executable,
                        scraper_path=None, store=None, run_id=None) -> dict:
    """Execute first-layer search: map params -> call scraper -> read artifact.

    Reuses ``scripts/boss_cdp_raw.py`` as a subprocess.  When *store* and
    *run_id* are provided, advances the run status ``queued -> running``
    then ``running -> succeeded`` (or ``failed`` on error).

    Returns ``{"jobs": [...], "source_count": N}`` and includes
    ``"status"`` only when status management was requested.
    """
    if scraper_path is None:
        scraper_path = str(Path(__file__).resolve().parent.parent / "scripts" / "boss_cdp_raw.py")

    params = filters_to_search_params(filters)

    if store and run_id:
        run = store.get_screening_run(run_id)
        validate_transition(run["status"], "running")
        store.update_screening_run_status(run_id, "running")

    command = [
        str(python_executable), str(scraper_path),
        "--keyword", str(keyword),
        "--city", str(params["city"]),
        "--output", str(output_path),
    ]
    for name, value in params["filters"].items():
        command.extend([f"--{name}", str(value)])

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        if store and run_id:
            store.update_screening_run_status(run_id, "failed")
        raise RuntimeError(f"抓取器执行失败: returncode={result.returncode}")

    output = Path(output_path)
    if not output.is_file():
        if store and run_id:
            store.update_screening_run_status(run_id, "failed")
        raise RuntimeError("搜索产物不存在")

    with output.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []

    if store and run_id:
        store.update_screening_run_status(run_id, "succeeded")

    result = {"jobs": jobs, "source_count": len(jobs)}
    if store and run_id:
        result["status"] = "succeeded"
    return result


# ---------------------------------------------------------------------------
# Second-layer: hard-rule verification, partition, zone lifecycle
# ---------------------------------------------------------------------------

def _parse_salary_range(salary_str):
    """解析薪资字符串为 (low, high) 范围。

    "12-18K" -> (12, 18)；"3K以下" -> (0, 3)；"50K以上" -> (50, inf)。
    "20-30K·13薪" 取 "·" 前部分 -> (20, 30)。无法解析返回 None。
    """
    if not salary_str:
        return None
    s = str(salary_str).strip().split("·")[0]
    nums = re.findall(r"\d+", s)
    if not nums:
        return None
    if "以下" in s:
        return (0, int(nums[0]))
    if "以上" in s:
        return (int(nums[0]), float("inf"))
    if len(nums) >= 2:
        return (int(nums[0]), int(nums[1]))
    return None


def _ranges_overlap(r1, r2) -> bool:
    """两个范围是否有重叠（含边界相等）。任一为 None 返回 False。"""
    if r1 is None or r2 is None:
        return False
    return max(r1[0], r2[0]) <= min(r1[1], r2[1])


def _tags_contains(tags, target_label) -> bool:
    """检查 tags 中是否包含 target_label。tags 格式 '3-5年 | 本科'。

    job 无 tags 时返回 True（视为不核验，避免误杀）。
    """
    if not tags:
        return True
    segments = [s.strip() for s in str(tags).split("|")]
    return target_label in segments


def verify_hard_rules(job, frozen_filters) -> bool:
    """对 job 按用户确认的非空字段逐项核验（硬规则）。

    用户选了什么核什么，没选的不核；空字段跳过；job 缺字段时该字段跳过。
    返回 True（过）或 False（不过）。不记录是被哪个字段排除。
    """
    frozen = frozen_filters or {}
    if not isinstance(job, dict):
        return False

    # city: job location 首段（"上海·浦东·张江" -> "上海"）
    city = frozen.get("city", "")
    if city:
        job_city = (job.get("location") or "").split("·")[0].strip()
        if job_city != city:
            return False

    # salary: frozen 段范围与 job 薪资范围重叠
    salary_code = frozen.get("salary", "")
    if salary_code:
        frozen_label = _SALARY_REVERSE.get(salary_code)
        if frozen_label:
            frozen_range = _parse_salary_range(frozen_label)
            job_range = _parse_salary_range(job.get("salary", ""))
            if not _ranges_overlap(frozen_range, job_range):
                return False

    # experience: job tags 包含 frozen 经验标签
    exp_code = frozen.get("experience", "")
    if exp_code:
        exp_label = _EXP_REVERSE.get(exp_code)
        if exp_label and not _tags_contains(job.get("tags"), exp_label):
            return False

    # degree: job tags 包含 frozen 学历标签
    deg_code = frozen.get("degree", "")
    if deg_code:
        deg_label = _DEGREE_REVERSE.get(deg_code)
        if deg_label and not _tags_contains(job.get("tags"), deg_label):
            return False

    # scale: job company_scale 等于 frozen 规模标签
    scale_code = frozen.get("scale", "")
    if scale_code:
        scale_label = _SCALE_REVERSE.get(scale_code)
        if scale_label and (job.get("company_scale") or "").strip() != scale_label:
            return False

    # stage: job company_stage 等于 frozen 阶段标签
    stage_code = frozen.get("stage", "")
    if stage_code:
        stage_label = _STAGE_REVERSE.get(stage_code)
        if stage_label and (job.get("company_stage") or "").strip() != stage_label:
            return False

    # industry: job company_industry 等于 frozen 行业标签
    ind_code = frozen.get("industry", "")
    if ind_code:
        ind_label = _INDUSTRY_REVERSE.get(ind_code)
        if ind_label and (job.get("company_industry") or "").strip() != ind_label:
            return False

    return True


def partition_job(job, frozen_filters, resume_text="", jd_text="", *, ai_enabled=True) -> str:
    """对单条 job 做两条核验分流：硬规则 + AI 语义相似度占位。

    都过返回 "match"（符合区），任一不过返回 "mismatch"（不符合区）。
    返回值只是字符串，不含原因（符合 spec：不符合区不区分原因）。
    硬规则不过时短路返回 mismatch，不调用 AI。

    *ai_enabled* 为 False 时走降级路径（FR-034）：仅执行硬规则核验，
    跳过 AI 语义相似度，硬规则过即 match。默认 True 保持向后兼容。
    """
    if not verify_hard_rules(job, frozen_filters):
        return "mismatch"
    if not ai_enabled:
        return "match"
    ai_result = assess_semantic_similarity(resume_text, jd_text)
    if isinstance(ai_result, dict) and ai_result.get("verdict") == "match":
        return "match"
    return "mismatch"


def partition_jobs(jobs, frozen_filters, resume_text="", jd_text="", *, ai_enabled=True) -> dict:
    """对一批抓回职位做两条核验分流，返回 {"match": [...], "mismatch": [...]}。

    每个区按 jobs 在输入列表中的抓回顺序排列，不使用相似度排序（FR-029）。
    返回的 job 引用与输入一致（不复制、不修改）。

    *ai_enabled* 为 False 时走降级路径（FR-034）：仅执行硬规则核验，
    不调 AI 语义相似度。默认 True 保持向后兼容。
    """
    match_zone = []
    mismatch_zone = []
    for job in jobs or []:
        verdict = partition_job(job, frozen_filters, resume_text, jd_text, ai_enabled=ai_enabled)
        if verdict == "match":
            match_zone.append(job)
        else:
            mismatch_zone.append(job)
    return {"match": match_zone, "mismatch": mismatch_zone}


# ---------------------------------------------------------------------------
# Display-stage exclusion (FR-022, FR-023)
# ---------------------------------------------------------------------------

def exclude_trash_jobs(jobs, rejected_job_ids) -> list:
    """展示阶段排除垃圾桶里的具体岗位（FR-022, FR-023）。

    只按具体 job_id 排除，不扩展到同公司或相似特征岗位。
    保留 jobs 在输入列表中的抓回顺序。返回的 job 引用与输入一致。
    rejected_job_ids 接受 list 或 set；job 无 job_id 字段时按 "" 处理。
    """
    rejected = set(rejected_job_ids or [])
    return [
        job for job in (jobs or [])
        if (job.get("job_id", "") if isinstance(job, dict) else "") not in rejected
    ]


# ---------------------------------------------------------------------------
# Interest-zone link validation (FR-020)
# ---------------------------------------------------------------------------

def is_safe_interest_link(url) -> bool:
    """校验感兴趣区跳转链接：仅 HTTPS 且预期 BOSS 域名（FR-020）。

    复用 001 的 normalize_job_link：规范化后非空即安全。
    不安全链接不展示跳转。
    """
    from webui.workbench import normalize_job_link
    return bool(normalize_job_link(url))
