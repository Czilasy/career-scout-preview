"""硬规则核验（三态）。从已删除的 screening.py 中迁出，供 discovery.py 使用。"""

import re

from scripts import boss_cdp_raw as boss

# 反向映射：filter code -> 中文标签（硬规则核验时用）
_SALARY_REVERSE = {v: k for k, v in boss.SALARY_MAP.items()}
_EXP_REVERSE = {v: k for k, v in boss.EXPERIENCE_MAP.items()}
_DEGREE_REVERSE = {v: k for k, v in boss.DEGREE_MAP.items()}
_SCALE_REVERSE = {v: k for k, v in boss.SCALE_MAP.items()}
_STAGE_REVERSE = {v: k for k, v in boss.STAGE_MAP.items()}
_INDUSTRY_REVERSE = {v: k for k, v in boss.INDUSTRY_MAP.items()}


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
        field was missing or unparseable on the job.

    Missing job fields are NOT treated as violations — a missing field cannot
    prove a mismatch. Empty constraints yield ``pass`` (nothing to verify).
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
