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
import time
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


def _merge_detail_jds(jobs, detail_output_path):
    """Merge run-scoped detail JD records into list jobs by job_id."""
    if not detail_output_path:
        return list(jobs or [])
    detail_path = Path(detail_output_path)
    if not detail_path.is_file():
        return list(jobs or [])
    try:
        with detail_path.open(encoding="utf-8") as handle:
            details = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return list(jobs or [])
    if not isinstance(details, list):
        return list(jobs or [])
    detail_by_id = {
        str(item.get("job_id") or ""): str(item.get("jd") or "")
        for item in details
        if isinstance(item, dict) and str(item.get("job_id") or "") and str(item.get("jd") or "").strip()
    }
    merged = []
    for job in jobs or []:
        if not isinstance(job, dict):
            merged.append(job)
            continue
        jd = detail_by_id.get(str(job.get("job_id") or ""))
        if jd:
            item = dict(job)
            item["jd"] = jd
            merged.append(item)
        else:
            merged.append(job)
    return merged


def _write_jobs_artifact(output_path, payload, jobs):
    """Atomically update the list artifact after JD merge."""
    output = Path(output_path)
    next_payload = dict(payload) if isinstance(payload, dict) else {}
    next_payload["jobs"] = jobs
    tmp = output.with_name(output.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(next_payload, handle, ensure_ascii=False)
    tmp.replace(output)


def execute_first_layer(filters, keyword, *, output_path, python_executable,
                        pages=None, max_details=None, start_page=None,
                        detail_output_path=None, scraper_path=None,
                        store=None, run_id=None, should_cancel=None,
                        timeout_seconds=None, on_process=None,
                        manage_status=True) -> dict:
    """Execute first-layer search: map params -> call scraper -> read artifact.

    Reuses ``scripts/boss_cdp_raw.py`` as a subprocess.  When *store* and
    *run_id* are provided, advances the run status ``queued -> running``
    and leaves it running for second-layer verification (or failed on error).

    Returns ``{"jobs": [...], "source_count": N}`` and includes
    ``"status"`` only when status management was requested.
    """
    if scraper_path is None:
        scraper_path = str(Path(__file__).resolve().parent.parent / "scripts" / "boss_cdp_raw.py")

    params = filters_to_search_params(filters)

    if store and run_id and manage_status:
        run = store.get_screening_run(run_id)
        validate_transition(run["status"], "running")
        store.update_screening_run_status(run_id, "running")

    command = [
        str(python_executable), str(scraper_path),
        "--keyword", str(keyword),
        "--city", str(params["city"]),
        "--output", str(output_path),
    ]
    if pages is not None:
        command.extend(["--pages", str(pages)])
    if start_page is not None:
        command.extend(["--start-page", str(start_page)])
    if max_details is not None:
        command.extend(["--max-details", str(max_details)])
    if detail_output_path is not None:
        command.extend(["--detail-output", str(detail_output_path)])
    for name, value in params["filters"].items():
        command.extend([f"--{name}", str(value)])

    interrupted_reason = None
    if timeout_seconds is None and should_cancel is None and on_process is None:
        result = subprocess.run(command, capture_output=True, text=True)
        returncode = result.returncode
    else:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if on_process:
            on_process(process)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if should_cancel and should_cancel():
                    interrupted_reason = "cancelled"
                    break
                if timeout_seconds is not None and time.monotonic() - started >= timeout_seconds:
                    interrupted_reason = "timeout"
                    break
                time.sleep(0.1)
            if interrupted_reason:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            returncode = process.wait()
        finally:
            if on_process:
                on_process(None)

    if returncode != 0 or interrupted_reason:
        output = Path(output_path)
        partial_jobs = []
        if output.is_file():
            try:
                with output.open(encoding="utf-8") as handle:
                    partial_payload = json.load(handle)
                if isinstance(partial_payload, dict) and isinstance(partial_payload.get("jobs"), list):
                    partial_jobs = partial_payload["jobs"]
            except (OSError, json.JSONDecodeError):
                partial_jobs = []
        if interrupted_reason == "cancelled":
            if store and run_id and manage_status:
                store.update_screening_run_status(
                    run_id, "interrupted", source_count=len(partial_jobs),
                    source_cursor=len(partial_jobs), error_code="cancelled",
                )
            return {
                "jobs": partial_jobs,
                "source_count": len(partial_jobs),
                "source_cursor": len(partial_jobs),
                "status": "interrupted",
                "error_code": "cancelled",
            }
        if partial_jobs:
            error_code = "timeout" if interrupted_reason == "timeout" else "fetch_interrupted"
            if store and run_id and manage_status:
                store.update_screening_run_status(
                    run_id, "partial", source_count=len(partial_jobs),
                    source_cursor=len(partial_jobs),
                    **({"error_code": "timeout"} if interrupted_reason == "timeout" else {}),
                )
            return {
                "jobs": partial_jobs,
                "source_count": len(partial_jobs),
                "source_cursor": len(partial_jobs),
                "status": "partial",
                "error_code": error_code,
            }
        if store and run_id and manage_status:
            store.update_screening_run_status(
                run_id, "failed",
                **({"error_code": "timeout"} if interrupted_reason == "timeout" else {}),
            )
        if interrupted_reason == "timeout":
            raise RuntimeError("抓取器执行超时")
        raise RuntimeError(f"抓取器执行失败: returncode={returncode}")

    output = Path(output_path)
    if not output.is_file():
        if store and run_id and manage_status:
            store.update_screening_run_status(run_id, "failed")
        raise RuntimeError("搜索产物不存在")

    try:
        with output.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        if store and run_id and manage_status:
            store.update_screening_run_status(run_id, "failed")
        raise RuntimeError("搜索产物无法解析") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        if store and run_id and manage_status:
            store.update_screening_run_status(run_id, "failed")
        raise RuntimeError("搜索产物格式无效")

    jobs = payload["jobs"]
    merged_jobs = _merge_detail_jds(jobs, detail_output_path)
    if merged_jobs != jobs:
        _write_jobs_artifact(output, payload, merged_jobs)
        jobs = merged_jobs

    if store and run_id and manage_status:
        store.update_screening_run_status(run_id, "running", source_count=len(jobs))

    result = {"jobs": jobs, "source_count": len(jobs)}
    if store and run_id and manage_status:
        result["status"] = "running"
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


# ---------------------------------------------------------------------------
# Policy v2: numeric min_salary (monthly K floor) tri-state check
# ---------------------------------------------------------------------------

def _parse_monthly_salary_k_v2(salary_str):
    """解析薪资字符串为月薪 (low_k, high_k) 区间（单位 K），无法解析返回 None。

    Policy v2 数值 min_salary 基准 (research.md:93)：
      - 月薪区间 "15-25K" -> (15.0, 25.0)
      - N薪 "18-22K·13薪" -> 取基础月薪区间 (18.0, 22.0)，不用 N 薪放大
      - 单值 "25K" -> (25.0, 25.0)
      - "20K以上" -> (20.0, inf)；"3K以下" -> (0.0, 3.0)
      - 明确年薪 "年薪240K" -> 折算月均 (20.0, 20.0)
      - 日薪 "200/天"、面议、缺失、不可解析格式 -> None
    """
    if not salary_str or not isinstance(salary_str, str):
        return None
    s = salary_str.strip()
    if not s or "面议" in s or "/" in s:
        return None
    # 明确年薪：折算为月均区间
    m = re.match(r"^年薪\s*(\d+(?:\.\d+)?)\s*[Kk]$", s)
    if m:
        monthly = float(m.group(1)) / 12.0
        return (monthly, monthly)
    base = s.split("·")[0].strip()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*[Kk]$", base)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = re.match(r"^(\d+(?:\.\d+)?)\s*[Kk]\s*以上$", base)
    if m:
        return (float(m.group(1)), float("inf"))
    m = re.match(r"^(\d+(?:\.\d+)?)\s*[Kk]\s*以下$", base)
    if m:
        return (0.0, float(m.group(1)))
    m = re.match(r"^(\d+(?:\.\d+)?)\s*[Kk]$", base)
    if m:
        v = float(m.group(1))
        return (v, v)
    return None


def check_min_salary_v2(job_salary, min_salary_constraint):
    """Policy v2 数值 min_salary 月薪 K 下限三态核验。

    契约 (research.md:93)：
      - 岗位月薪区间上限明确低于下限 -> violation；
      - 区间能够达到下限 -> pass；
      - 无法比较（日薪/面议/缺失/不可解析/非 user_confirmed）-> unknown。

    不伪装成旧 BOSS ``salary`` code；旧 code 由 v1 路径处理。

    返回 ``{"outcome": "pass"|"violation"|"unknown", "reason": str,
    "field": "min_salary"}``。
    """
    field = "min_salary"
    constraint = min_salary_constraint or {}
    if not isinstance(constraint, dict) or constraint.get("source") != "user_confirmed":
        return {"outcome": "unknown", "reason": "constraint_not_confirmed", "field": field}
    try:
        floor_k = float(constraint.get("amount"))
    except (TypeError, ValueError):
        return {"outcome": "unknown", "reason": "invalid_floor", "field": field}

    parsed = _parse_monthly_salary_k_v2(job_salary)
    if parsed is None:
        return {"outcome": "unknown", "reason": "unparseable_salary", "field": field}
    _low_k, high_k = parsed
    if high_k < floor_k:
        return {"outcome": "violation",
                "reason": f"上限 {high_k:g}K < 最低 {floor_k:g}K", "field": field}
    return {"outcome": "pass",
            "reason": f"区间可达最低 {floor_k:g}K", "field": field}


def _tags_contains(tags, target_label) -> bool:
    """检查 tags 中是否包含 target_label。tags 格式 '3-5年 | 本科'。

    job 无 tags 时返回 True（视为不核验，避免误杀）。
    """
    if not tags:
        return True
    segments = [s.strip() for s in str(tags).split("|")]
    return target_label in segments


_DEGREE_LEVEL = {
    "初中及以下": 0,
    "中专/中技": 1,
    "高中": 2,
    "大专": 3,
    "本科": 4,
    "硕士": 5,
    "博士": 6,
}


def _tag_value(tags, allowed_labels):
    if not tags:
        return None
    segments = [segment.strip() for segment in str(tags).split("|")]
    return next((segment for segment in segments if segment in allowed_labels), None)


def _degree_compatible(candidate_degree, required_degree) -> bool:
    if {candidate_degree, required_degree}.issubset({"大专", "本科"}):
        return True
    return _DEGREE_LEVEL[candidate_degree] >= _DEGREE_LEVEL[required_degree]


def verify_hard_rules_detailed(job, frozen_filters) -> dict:
    """Verify selected fields and retain safe parse-failure metadata.

    Missing or unparseable job fields pass leniently but are listed in
    ``parse_failures``. A parsed, incompatible field fails deterministically.
    """
    frozen = frozen_filters or {}
    if not isinstance(job, dict):
        return {"passed": False, "parse_failures": ["job"]}
    failures = []

    city = frozen.get("city", "")
    if city:
        job_city = (job.get("location") or "").split("·")[0].strip()
        if not job_city:
            failures.append("city")
        elif job_city != city:
            return {"passed": False, "parse_failures": failures}

    salary_code = frozen.get("salary", "")
    if salary_code:
        expected = _parse_salary_range(_SALARY_REVERSE.get(salary_code, ""))
        actual = _parse_salary_range(job.get("salary", ""))
        if expected is None or actual is None:
            failures.append("salary")
        elif not _ranges_overlap(expected, actual):
            return {"passed": False, "parse_failures": failures}

    exp_code = frozen.get("experience", "")
    if exp_code:
        expected = _EXP_REVERSE.get(exp_code)
        actual = _tag_value(job.get("tags"), set(_EXP_REVERSE.values()))
        if not expected or actual is None:
            failures.append("experience")
        elif actual != expected:
            return {"passed": False, "parse_failures": failures}

    degree_code = frozen.get("degree", "")
    if degree_code:
        candidate = _DEGREE_REVERSE.get(degree_code)
        required = _tag_value(job.get("tags"), set(_DEGREE_LEVEL))
        if candidate not in _DEGREE_LEVEL or required is None:
            failures.append("degree")
        elif not _degree_compatible(candidate, required):
            return {"passed": False, "parse_failures": failures}

    for key, job_key, labels in (
        ("scale", "company_scale", _SCALE_REVERSE),
        ("stage", "company_stage", _STAGE_REVERSE),
        ("industry", "company_industry", _INDUSTRY_REVERSE),
    ):
        code = frozen.get(key, "")
        if not code:
            continue
        expected = labels.get(code)
        actual = (job.get(job_key) or "").strip()
        if not expected or not actual:
            failures.append(key)
        elif actual != expected:
            return {"passed": False, "parse_failures": failures}

    return {"passed": True, "parse_failures": failures}


def verify_hard_rules(job, frozen_filters) -> bool:
    """对 job 按用户确认的非空字段逐项核验（硬规则）。

    用户选了什么核什么，没选的不核；空字段跳过；job 缺字段时该字段跳过。
    返回 True（过）或 False（不过）。不记录是被哪个字段排除。
    """
    return verify_hard_rules_detailed(job, frozen_filters)["passed"]


# ---------------------------------------------------------------------------
# T015: Tri-state hard rules (pass / violation / unknown) for feature 004
# ---------------------------------------------------------------------------

TRI_STATE_OUTCOMES = ("pass", "violation", "unknown")


def verify_hard_rules_tri_state(job, hard_constraints) -> dict:
    """Verify hard constraints returning a tri-state outcome.

    Returns ``{"outcome": "pass"|"violation"|"unknown", "checks": [...]}``
    where each check carries ``{"field", "outcome", "reason"}``.

    Semantics:
      - ``pass``: every required field the user set was present on the job
        and matched.
      - ``violation``: at least one required field was present on the job
        but explicitly mismatched (deterministic fail).
      - ``unknown``: no explicit mismatch occurred but at least one required
        field was missing or unparseable on the job. ``unknown`` never
        promotes to ``high_match``; the caller routes it to ``needs_review``.

    Missing job fields are NOT treated as violations — per spec, a missing
    field cannot prove a mismatch. Empty constraints yield ``pass`` (nothing
    to verify).
    """
    frozen = hard_constraints or {}
    if not isinstance(job, dict):
        return {"outcome": "unknown", "checks": [{"field": "job", "outcome": "unknown", "reason": "job_not_dict"}]}

    checks: list[dict] = []
    has_violation = False
    has_unknown = False

    city = frozen.get("city", "")
    if city:
        job_city = (job.get("location") or "").split("·")[0].strip()
        if not job_city:
            checks.append({"field": "city", "outcome": "unknown", "reason": "missing"})
            has_unknown = True
        elif job_city != city:
            checks.append({"field": "city", "outcome": "violation", "reason": "mismatch"})
            has_violation = True
        else:
            checks.append({"field": "city", "outcome": "pass", "reason": "match"})

    salary_code = frozen.get("salary", "")
    if salary_code:
        expected = _parse_salary_range(_SALARY_REVERSE.get(salary_code, ""))
        actual = _parse_salary_range(job.get("salary", ""))
        if expected is None or actual is None:
            checks.append({"field": "salary", "outcome": "unknown", "reason": "unparseable"})
            has_unknown = True
        elif not _ranges_overlap(expected, actual):
            checks.append({"field": "salary", "outcome": "violation", "reason": "mismatch"})
            has_violation = True
        else:
            checks.append({"field": "salary", "outcome": "pass", "reason": "overlap"})

    exp_code = frozen.get("experience", "")
    if exp_code:
        expected = _EXP_REVERSE.get(exp_code)
        actual = _tag_value(job.get("tags"), set(_EXP_REVERSE.values()))
        if not expected or actual is None:
            checks.append({"field": "experience", "outcome": "unknown", "reason": "missing"})
            has_unknown = True
        elif actual != expected:
            checks.append({"field": "experience", "outcome": "violation", "reason": "mismatch"})
            has_violation = True
        else:
            checks.append({"field": "experience", "outcome": "pass", "reason": "match"})

    degree_code = frozen.get("degree", "")
    if degree_code:
        candidate = _DEGREE_REVERSE.get(degree_code)
        required = _tag_value(job.get("tags"), set(_DEGREE_LEVEL))
        if candidate not in _DEGREE_LEVEL or required is None:
            checks.append({"field": "degree", "outcome": "unknown", "reason": "missing"})
            has_unknown = True
        elif not _degree_compatible(candidate, required):
            checks.append({"field": "degree", "outcome": "violation", "reason": "mismatch"})
            has_violation = True
        else:
            checks.append({"field": "degree", "outcome": "pass", "reason": "compatible"})

    for key, job_key, labels in (
        ("scale", "company_scale", _SCALE_REVERSE),
        ("stage", "company_stage", _STAGE_REVERSE),
        ("industry", "company_industry", _INDUSTRY_REVERSE),
    ):
        code = frozen.get(key, "")
        if not code:
            continue
        expected = labels.get(code)
        actual = (job.get(job_key) or "").strip()
        if not expected or not actual:
            checks.append({"field": key, "outcome": "unknown", "reason": "missing"})
            has_unknown = True
        elif actual != expected:
            checks.append({"field": key, "outcome": "violation", "reason": "mismatch"})
            has_violation = True
        else:
            checks.append({"field": key, "outcome": "pass", "reason": "match"})

    if has_violation:
        outcome = "violation"
    elif has_unknown:
        outcome = "unknown"
    else:
        outcome = "pass"
    return {"outcome": outcome, "checks": checks}


class PartitionVerdict(str):
    """String-compatible verdict carrying only a safe pending failure code."""

    def __new__(cls, value, failure_stage=None):
        instance = super().__new__(cls, value)
        instance.failure_stage = failure_stage
        return instance


def partition_job(job, frozen_filters, resume_text="", jd_text="", *, ai_enabled=True,
                  semantic_options=None) -> str:
    """对单条 job 做两条核验分流：硬规则 + AI 语义相似度占位。

    都过返回 "match"（符合区），任一不过返回 "mismatch"（不符合区）。
    返回值只是字符串，不含原因（符合 spec：不符合区不区分原因）。
    硬规则不过时短路返回 mismatch，不调用 AI。

    *ai_enabled* 为 False 时走降级路径（FR-034）：仅执行硬规则核验，
    跳过 AI 语义相似度，硬规则过即 match。默认 True 保持向后兼容。
    """
    if not verify_hard_rules(job, frozen_filters):
        return PartitionVerdict("mismatch")
    if not ai_enabled:
        return PartitionVerdict("match")
    options = dict(semantic_options or {})
    require_input = bool(options.pop("require_input", False))
    effective_jd = (job.get("jd") or jd_text) if isinstance(job, dict) else jd_text
    if require_input and (not str(resume_text or "").strip() or not str(effective_jd or "").strip()):
        return PartitionVerdict("pending", "verification_error")
    ai_result = assess_semantic_similarity(resume_text, effective_jd, **options)
    verdict = ai_result.get("verdict") if isinstance(ai_result, dict) else "pending"
    if verdict in {"match", "mismatch"}:
        return PartitionVerdict(verdict)
    stage = ai_result.get("failure_stage") if isinstance(ai_result, dict) else None
    return PartitionVerdict("pending", stage or "ai_invalid_output")


def partition_jobs(jobs, frozen_filters, resume_text="", jd_text="", *, ai_enabled=True,
                   semantic_options=None) -> dict:
    """对一批抓回职位做两条核验分流，返回 {"match": [...], "mismatch": [...]}。

    每个区按 jobs 在输入列表中的抓回顺序排列，不使用相似度排序（FR-029）。
    返回的 job 引用与输入一致（不复制、不修改）。

    *ai_enabled* 为 False 时走降级路径（FR-034）：仅执行硬规则核验，
    不调 AI 语义相似度。默认 True 保持向后兼容。
    """
    match_zone = []
    mismatch_zone = []
    pending_zone = []
    pending_failures = {}
    for job in jobs or []:
        try:
            verdict = partition_job(
                job, frozen_filters, resume_text, jd_text,
                ai_enabled=ai_enabled, semantic_options=semantic_options,
            )
        except Exception:
            verdict = PartitionVerdict("pending", "verification_error")
        if verdict == "match":
            match_zone.append(job)
        elif verdict == "mismatch":
            mismatch_zone.append(job)
        else:
            pending_zone.append(job)
            job_id = job.get("job_id", "") if isinstance(job, dict) else ""
            pending_failures[job_id] = getattr(verdict, "failure_stage", None) or "verification_error"
    return {
        "match": match_zone,
        "mismatch": mismatch_zone,
        "pending": pending_zone,
        "pending_failures": pending_failures,
    }


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
