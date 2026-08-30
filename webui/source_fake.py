"""内存 FakeJobSource 测试替身（021 拆分自 webui/source.py）。

不发起真实子进程；以 (keyword, city) → jobs 与 job_id → detail 映射
模拟列表/详情抓取，哨兵 ``__fail__:code`` 模拟失败。符合 JobSource
Protocol（contracts/job-source.md）。
"""

from __future__ import annotations

from collections.abc import Callable

from webui.source_breaker import SourceOutcome
from webui.source_boss_helpers import _input_hash

from webui.logging_setup import get_logger

_logger = get_logger(__name__)



class FakeJobSource:
    """In-memory JobSource for tests. Never invokes a real subprocess.

    Constructed with a mapping of (keyword, city) -> list[dict] for list
    fetches and an optional mapping of job_id -> dict for detail fetches.
    Failures are simulated by mapping to a sentinel ``__fail__:code``.

    符合 ``JobSource`` Protocol（contracts/job-source.md）：携带 ``platform``
    和显式 ``cdp_port``，支持 ``preflight``、``fetch_list``、
    ``fetch_detail`` 和 ``fetch_details_batch``。
    """

    def __init__(
        self,
        list_jobs: dict[tuple[str, str], list[dict]] | None = None,
        detail_jobs: dict[str, dict] | None = None,
        *,
        list_failures: set[tuple[str, str]] | None = None,
        detail_failures: set[str] | None = None,
        input_hash_seed: str = "fake",
        platform: str = "boss",
        cdp_port: int = 9222,
        preflight_failure: str | None = None,
    ):
        self.list_jobs = list_jobs or {}
        self.detail_jobs = detail_jobs or {}
        self.list_failures = list_failures or set()
        self.detail_failures = detail_failures or set()
        self.input_hash_seed = input_hash_seed
        self.platform = str(platform)
        if not isinstance(cdp_port, int) or isinstance(cdp_port, bool) or cdp_port <= 0:
            raise ValueError("cdp_port 必须为正整数")
        self.cdp_port = int(cdp_port)
        self._preflight_failure = preflight_failure
        self.list_calls: list[dict] = []
        self.detail_calls: list[dict] = []
        self.preflight_calls: int = 0

    def preflight(self) -> SourceOutcome:
        """检查登录态和运行环境就绪性（测试替身）。

        默认返回成功；构造时传入 ``preflight_failure`` 可模拟平台级
        阻断（如 ``source_login_required``）。
        """
        self.preflight_calls += 1
        if self._preflight_failure:
            return SourceOutcome.failure(
                failed_code=self._preflight_failure,
                safe_log=f"fake preflight platform={self.platform} port={self.cdp_port} blocked=1",
            )
        return SourceOutcome.success(
            safe_log=f"fake preflight platform={self.platform} port={self.cdp_port} ready=1",
        )

    def fetch_list(
        self, plan_item: dict, *, on_page_completed: Callable[[dict], None] | None = None,
    ) -> SourceOutcome:
        if not isinstance(plan_item, dict):
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log="plan_item_not_dict",
            )
        self.list_calls.append(dict(plan_item))
        keyword = str(plan_item.get("keyword") or "").strip()
        city = str(plan_item.get("city") or "").strip()
        expected_hash = str(plan_item.get("input_hash") or "")
        if not keyword or not expected_hash:
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log=f"fake list missing_fields keyword={bool(keyword)} hash={bool(expected_hash)}",
            )
        if (keyword, city) in self.list_failures:
            return SourceOutcome.failure(
                failed_code="source_blocked",
                safe_log=f"fake list keyword_present=1 city_present={bool(city)} blocked=1",
            )
        jobs = self.list_jobs.get((keyword, city), [])
        actual_hash = _input_hash({
            "keyword": keyword,
            "city": city,
            "source_filters": plan_item.get("source_filters") or {},
            "target_pages": int(plan_item.get("target_pages") or 1),
        })
        if expected_hash and actual_hash != expected_hash:
            return SourceOutcome.failure(
                failed_code="source_input_drift",
                safe_log="fake list input_hash_mismatch",
            )
        target_pages = max(1, int(plan_item.get("target_pages") or 1))
        if on_page_completed is not None:
            on_page_completed({
                "kind": "page_completed",
                "combo_key": str(plan_item.get("combo_key") or "") or f"{keyword}|{city}",
                "keyword": keyword,
                "city": city,
                "page": target_pages,
                "target_pages": target_pages,
                "jobs_delta": len(jobs),
                "jobs_count": len(jobs),
                "has_more": False,
                "resume_page": target_pages + 1,
                "last_completed_page": target_pages,
                "jobs_snapshot": list(jobs),
            })
        return SourceOutcome.success(
            jobs=list(jobs),
            safe_log=f"fake list keyword_present=1 city_present={bool(city)} job_count={len(jobs)}",
            input_hash=actual_hash,
        )

    def fetch_detail(self, job: dict, *, detail_output_path: str | None = None) -> SourceOutcome:
        del detail_output_path  # 测试替身不写盘，签名与真实 source 对齐
        if not isinstance(job, dict):
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log="job_not_dict",
            )
        self.detail_calls.append(dict(job))
        source_url = str(job.get("source_url") or job.get("url") or "").strip()
        job_id = str(job.get("job_id") or job.get("id") or "").strip()
        if not source_url or not job_id:
            return SourceOutcome.failure(
                failed_code="source_invalid_output",
                safe_log=f"fake detail missing_fields url={bool(source_url)} job_id={bool(job_id)}",
            )
        if job_id in self.detail_failures:
            return SourceOutcome.failure(
                failed_code="source_blocked",
                safe_log="fake detail job_id_present=1 blocked=1",
            )
        detail = self.detail_jobs.get(job_id, {})
        return SourceOutcome.success(detail=detail, safe_log=f"fake detail job_id_present=1 fields={sorted(detail.keys())[:3]}")

    def fetch_details_batch(
        self, jobs: list[dict], *, detail_output_path: str | None = None,
        on_item_done: Callable[[int], None] | None = None,
        **bounded_options,
    ) -> dict[str, SourceOutcome]:
        """批量抓取详情（测试替身）：逐个调用 fetch_detail 并按 job_id 汇总。

        单岗位失败不抛出；每个输入恰有一个终态 outcome。
        ``on_item_done``：每条处理后回调已完成条数（与 ZhilianCdpSource 对齐）。
        """
        del detail_output_path  # 测试替身不写盘，签名与真实 source 对齐
        results: dict[str, SourceOutcome] = {}
        for i, job in enumerate(jobs):
            if not isinstance(job, dict):
                results[f"idx{i}"] = SourceOutcome.failure(
                    failed_code="source_invalid_output",
                    safe_log="job_not_dict",
                )
                if on_item_done is not None:
                    try:
                        on_item_done(i + 1)
                    except Exception:
                        # 吞噬白名单（031 B4）：测试替身的进度回调，失败无需留痕
                        pass

                continue
            job_id = str(job.get("job_id") or job.get("id") or "").strip()
            key = job_id or f"idx{i}"
            results[key] = self.fetch_detail(job, **bounded_options)
            if on_item_done is not None:
                try:
                    on_item_done(i + 1)
                except Exception:
                    # 吞噬白名单（031 B4）：测试替身的进度回调，失败无需留痕
                    pass

        return results
