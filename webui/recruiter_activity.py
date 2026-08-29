# -*- coding: utf-8 -*-
"""招聘者活跃判定域（028 B081 第 7 类筛选条件）。

Boss 取详情页招聘者名片活跃文本，智联取详情接口 staff.lastOnlineTime 毫秒
时间戳；两平台归一化为统一的「活跃距今天数区间」事实字典
（specs/028-recruiter-activity-filter/data-model.md），精筛硬规则按下界判定：
区间下界 > 档位天数才拦（确定超过才拦），未知/拿不准不拦。
"""

from __future__ import annotations

import re
import time

FIELD_KEY = "recruiter_activity"

#: 四档档位稳定码 → 天数（2026-08-29 冻结口径：距今严格大于才拦）
THRESHOLD_DAYS = {"week": 7, "month": 30, "quarter": 90, "half_year": 180}

#: 档位稳定码 → 用户可见 label
FIELD_LABELS = {
    "week": "近一周",
    "month": "近一个月",
    "quarter": "近三个月",
    "half_year": "近半年",
}

#: 天数 → label（判定说明用）
DAYS_TO_LABEL = {days: FIELD_LABELS[code] for code, days in THRESHOLD_DAYS.items()}


def filter_option_pairs():
    """第 7 类筛选 options（value=档位稳定码，label=中文档位），供平台 schema 构建。"""
    return tuple((code, FIELD_LABELS[code]) for code in THRESHOLD_DAYS)

UNKNOWN_CAVEAT = "招聘者活跃时间未知，未按第 7 类拦截"

_DAY_MS = 86400.0 * 1000.0

# Boss 名片活跃文本 → (age_lower_days, age_upper_days)；上界 None 表示无上界。
# 上界型「N日内/周内/月内活跃」与下界型「半年前/N月前/N年前活跃」按
# 2026-08-29 实测值域收敛（18 详情页）；表外文本一律 known=False 未知兜底。
_BOSS_TEXT_PATTERNS = (
    (re.compile(r"^在线$"), lambda m: (0.0, 0.0)),
    (re.compile(r"^刚刚活跃$"), lambda m: (0.0, 0.0)),
    (re.compile(r"^今日活跃$"), lambda m: (0.0, 1.0)),
    (re.compile(r"^昨日活跃$"), lambda m: (1.0, 2.0)),
    (re.compile(r"^(\d+)日内活跃$"), lambda m: (0.0, float(m.group(1)))),
    (re.compile(r"^(\d+)周内活跃$"), lambda m: (0.0, 7.0 * float(m.group(1)))),
    (re.compile(r"^(\d+)月内活跃$"), lambda m: (0.0, 30.0 * float(m.group(1)))),
    (re.compile(r"^半年前活跃$"), lambda m: (180.0, None)),
    (re.compile(r"^(\d+)月前活跃$"), lambda m: (30.0 * float(m.group(1)), None)),
    (re.compile(r"^(\d+)年前活跃$"), lambda m: (365.0 * float(m.group(1)), None)),
)


def _unknown_fact(source, text):
    return {
        "source": source,
        "text": text if isinstance(text, str) else "",
        "last_online_ms": None,
        "age_lower_days": None,
        "age_upper_days": None,
        "known": False,
    }


def _normalize_boss(detail):
    if "recruiter_activity_text" not in detail:
        return None
    text = detail.get("recruiter_activity_text")
    fact = _unknown_fact("boss", text)
    if not isinstance(text, str) or not text:
        return fact
    stripped = text.strip()
    for pattern, to_interval in _BOSS_TEXT_PATTERNS:
        matched = pattern.match(stripped)
        if matched:
            lower, upper = to_interval(matched)
            fact["text"] = stripped
            fact["age_lower_days"] = lower
            fact["age_upper_days"] = upper
            fact["known"] = True
            break
    return fact


def _normalize_zhilian(detail):
    has_text = "recruiter_activity_text" in detail
    has_ms = "recruiter_last_online_ms" in detail
    if not has_text and not has_ms:
        return None
    fact = _unknown_fact("zhilian", detail.get("recruiter_activity_text"))
    if not has_ms:
        return fact
    try:
        ts = float(detail.get("recruiter_last_online_ms"))
    except (TypeError, ValueError):
        return fact
    if ts <= 0:
        return fact
    age_days = max(0.0, (time.time() * 1000.0 - ts) / _DAY_MS)
    fact["last_online_ms"] = int(ts) if float(ts).is_integer() else ts
    fact["age_lower_days"] = age_days
    fact["age_upper_days"] = age_days
    fact["known"] = True
    return fact


def normalize_detail_activity(platform, detail):
    """平台详情产物 → 统一活跃事实字典；无任何活跃键返回 None。

    解析异常一律产出 known=False 的事实（未知兜底），绝不抛错中断筛选。
    """
    if not isinstance(detail, dict):
        return None
    if platform == "boss":
        return _normalize_boss(detail)
    if platform == "zhilian":
        return _normalize_zhilian(detail)
    return None


def humanize_days(days):
    """距今天数 → 人话距离（判定说明用，向下取整符合「至少」语义）。"""
    days = float(days)
    if days < 14:
        return f"{int(days)} 天前"
    if days < 60:
        return f"{int(days // 7)} 周前"
    if days < 365:
        return f"{int(days // 30)} 个月前"
    return f"{int(days // 365)} 年前"


def evaluate(activity, threshold_days):
    """档位判定：区间下界超过档位天数 → not_match verdict；否则 None。

    Boss 下界型文本（半年前活跃等）的说明优先用原始文本形态（「半年前」），
    其余用人话距离。未知事实/区间拿不准一律 None（不拦截）。
    """
    if not isinstance(activity, dict) or not activity.get("known"):
        return None
    try:
        lower = float(activity.get("age_lower_days"))
    except (TypeError, ValueError):
        return None
    threshold = float(threshold_days)
    if lower <= threshold:
        return None
    label = DAYS_TO_LABEL.get(threshold_days, "所选档位")
    text = activity.get("text")
    if isinstance(text, str) and text.endswith("前活跃"):
        distance = text[: -len("活跃")]
    else:
        distance = humanize_days(lower)
    return {
        "verdict": "not_match",
        "reason": f"负责人上次活跃{distance}，超过要求的{label}",
    }


def unknown_job_ids(jobs, screening_fields):
    """收集「选中档位但拿不到可判定活跃事实」的岗位 id；未选档位返回空集。"""
    if not isinstance(screening_fields, dict) or FIELD_KEY not in screening_fields:
        return set()
    unknown = set()
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        extra = job.get("extra")
        fact = extra.get(FIELD_KEY) if isinstance(extra, dict) else None
        if not isinstance(fact, dict) or not fact.get("known"):
            unknown.add(str(job.get("job_id", "")))
    return unknown


def merge_unknown_caveat(verdict):
    """给最终 verdict 并入未知 caveat（幂等）；非 dict 原样返回。"""
    if not isinstance(verdict, dict):
        return verdict
    caveats = list(verdict.get("caveats") or [])
    if UNKNOWN_CAVEAT not in caveats:
        caveats.append(UNKNOWN_CAVEAT)
    verdict["caveats"] = caveats
    return verdict
