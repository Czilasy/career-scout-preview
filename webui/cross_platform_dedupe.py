"""跨平台岗位去重编排服务（019）。

生效点：后跑平台 AI 筛选输入组装处（app.py 接线，一行调用
``apply_to_screening_input``）。判定源 = 对端平台近 30 天内全部可见轮
（done/partial/scraped_only）的非剔除岗位；指纹比对见 ``job_fingerprint``
（严格归一化精确匹配，仅跨平台生效，宁可漏判不误合）。

分层（plan.md）：app.py（接线）→ 本模块（编排）→ job_fingerprint（纯函数）；
本模块经参数注入 store 只读查询（``list_history_rounds`` /
``load_latest_pipeline_result``），不 import app/store 实现，不写库。

数据契约见 ``specs/019-cross-platform-job-dedup/contracts/cross-platform-dedupe.md``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from webui.job_fingerprint import fingerprint
from webui.platforms import KNOWN_PLATFORM_KEYS, get_platform_or_none
from webui.result_rounds import VISIBLE_STATUSES

__all__ = [
    "DEDUPE_WINDOW_DAYS",
    "EXTRA_KEY",
    "OtherPlatformJob",
    "DedupeOutcome",
    "collect_other_platform_jobs",
    "split_cross_platform_duplicates",
    "apply_to_screening_input",
]

#: 对端轮时间窗（天）：超窗整轮不参与，防拿重新招聘的旧同名岗位误剔。
DEDUPE_WINDOW_DAYS = 30

#: 剔除行 extra 里的对端指向键（前后端契约）。
EXTRA_KEY = "cross_platform_dup_of"

_CST = timezone(timedelta(hours=8))  # 与 store 层落库时区一致（东八区）


def _display_name(platform: str) -> str:
    """平台显示名走注册表（唯一权威，不自建第二套）。"""
    reg = get_platform_or_none(platform)
    return reg.display_name if reg is not None else platform


def _parse_ts(value) -> datetime | None:
    """ISO 文本或 epoch 毫秒 → aware datetime（naive 视为东八区）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, _CST)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_CST)
    return parsed


@dataclass(frozen=True)
class OtherPlatformJob:
    """对端判定源岗位：岗位数据 + 所属平台 + 追溯用最近包含轮信息。"""

    job: dict
    platform: str
    round_id: str
    finished_at: str  # 最近包含该岗位的对端轮定稿时间（原样透传，ISO 文本）


@dataclass
class DedupeOutcome:
    """一次去重判定的全部产物（app.py 只接线消费，不重复组装）。"""

    kept_jobs: list = field(default_factory=list)
    dropped_entries: list = field(default_factory=list)
    dup_verdicts: dict = field(default_factory=dict)
    total_scraped: int = 0

    @property
    def deduped_count(self) -> int:
        return len(self.dropped_entries)

    @property
    def progress_message(self) -> str | None:
        """进度报数文案；0 条剔除时 None（不打扰）。"""
        if not self.dropped_entries:
            return None
        return (f"本批 {self.total_scraped} 条中 {self.deduped_count} 条"
                "跨平台重复，跳过 AI 筛选")

    def ledger_payload(self) -> dict | None:
        """任务事件台账载荷；0 条剔除时 None（不写事件）。"""
        if not self.dropped_entries:
            return None
        dropped = []
        for entry in self.dropped_entries:
            dropped.append({
                "job_id": str(entry.get("job_id") or ""),
                "title": str(entry.get("title") or ""),
                "dup_of": dict((entry.get("extra") or {}).get(EXTRA_KEY) or {}),
            })
        return {
            "dropped": dropped,
            "counts": {"scraped": self.total_scraped,
                       "deduped": self.deduped_count},
        }


def collect_other_platform_jobs(
    store, current_platform: str, profile_summary: str = "", *, now=None,
) -> list[OtherPlatformJob]:
    """收集对端判定源：近 ``DEDUPE_WINDOW_DAYS`` 天全部可见轮的非剔除岗位。

    逐轮过滤：可见状态（done/partial/scraped_only）、定稿时间超窗整轮跳过、
    轮画像摘要与当前任务双非空且不一致整轮跳过（R4：不跨画像串台；任一
    为空不过滤）、轮内无非剔除岗位（全剔除行）整轮跳过。轮按定稿顺序从
    旧到新汇入，同岗位多轮取最近轮为追溯目标（由索引层保证）。
    """
    current_platform = str(current_platform or "")
    current_summary = str(profile_summary or "").strip()
    now_dt = now or datetime.now(_CST)
    window = timedelta(days=DEDUPE_WINDOW_DAYS)
    sources: list[OtherPlatformJob] = []
    for platform_key in KNOWN_PLATFORM_KEYS:
        if platform_key == current_platform:
            continue
        # list_history_rounds 新→旧；反转为旧→新，保证“首个命中定源、
        # 追溯取最近轮”的顺序语义。
        rounds = list(store.list_history_rounds(platform_key) or [])
        for run in reversed(rounds):
            if str(run.get("status") or "") not in VISIBLE_STATUSES:
                continue
            round_summary = str(run.get("profile_summary") or "").strip()
            if current_summary and round_summary and round_summary != current_summary:
                continue
            finished_raw = run.get("finished_at") or run.get("created_at")
            finished = _parse_ts(finished_raw)
            if finished is None or now_dt - finished > window:
                continue
            payload = store.load_latest_pipeline_result(str(run.get("id") or ""))
            result = (payload or {}).get("result") or {}
            round_jobs = [
                job for job in result.get("jobs") or []
                if isinstance(job, dict)
            ]
            if not round_jobs:
                continue
            for job in round_jobs:
                sources.append(OtherPlatformJob(
                    job=job,
                    platform=str(job.get("platform") or platform_key),
                    round_id=str(run.get("id") or ""),
                    finished_at=str(finished_raw or ""),
                ))
    return sources


def _build_source_index(
    other_jobs: list[OtherPlatformJob],
) -> dict[tuple[str, str, str], dict]:
    """指纹→对端保留条目。首个命中定身份，追溯时间随更近的轮刷新。"""
    index: dict[tuple[str, str, str], dict] = {}
    for item in other_jobs:
        key = fingerprint(item.job)
        if key is None:
            continue
        hit = index.get(key)
        if hit is None:
            index[key] = {
                "platform": item.platform,
                "platform_job_id": str(
                    item.job.get("platform_job_id")
                    or item.job.get("job_id") or ""),
                "source_url": str(
                    item.job.get("source_url")
                    or item.job.get("canonical_url") or ""),
                "finished_at": item.finished_at,
            }
        else:
            hit["finished_at"] = item.finished_at  # 追溯以最近包含轮为准
    return index


def split_cross_platform_duplicates(
    raw_jobs: list, other_jobs: list[OtherPlatformJob],
    current_platform: str,
) -> DedupeOutcome:
    """按指纹拆分筛选输入：命中对端的岗位 → 剔除条目（含 verdict/extra）。"""
    current_platform = str(current_platform or "")
    index = _build_source_index(other_jobs)
    outcome = DedupeOutcome(total_scraped=len(raw_jobs or []))
    for job in raw_jobs or []:
        if not isinstance(job, dict):
            continue
        jid = str(job.get("job_id") or "")
        key = fingerprint(job)
        hit = index.get(key) if key is not None else None
        if hit is None or hit["platform"] == current_platform:
            outcome.kept_jobs.append(job)
            continue
        reason = f"跨平台重复：已在 {_display_name(hit['platform'])} 保留"
        outcome.dup_verdicts[jid] = {"verdict": "dropped", "reason": reason}
        outcome.dropped_entries.append({
            "job_id": jid,
            "platform_job_id": str(job.get("platform_job_id") or jid),
            "title": str(job.get("title") or ""),
            "company": str(job.get("company") or job.get("boss_name") or ""),
            "salary": str(job.get("salary") or ""),
            "location": str(job.get("location") or ""),
            "experience": str(job.get("experience") or ""),
            "degree": str(job.get("degree") or ""),
            "source_url": str(job.get("source_url") or job.get("job_link") or ""),
            "canonical_url": str(
                job.get("canonical_url") or job.get("source_url")
                or job.get("job_link") or ""),
            "reason": reason,
            "extra": {EXTRA_KEY: dict(hit)},
        })
    return outcome


def apply_to_screening_input(
    store, raw_jobs: list, current_platform: str, profile_summary: str = "",
    *, enabled: bool = True, now=None,
) -> DedupeOutcome:
    """组合入口（app.py 一行调用）：开关旁路直通，开启则收集 + 拆分。"""
    jobs = list(raw_jobs or [])
    if not enabled:
        return DedupeOutcome(
            kept_jobs=jobs, total_scraped=len(jobs))
    other_jobs = collect_other_platform_jobs(
        store, current_platform, profile_summary, now=now)
    return split_cross_platform_duplicates(jobs, other_jobs, current_platform)
