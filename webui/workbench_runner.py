"""WorkbenchRunner：工作台搜索运行编排（031 B6 自 webui/task_runners.py 物理搬运）。

T007 建立的父运行 + 子查询模型：一个 search_run 拥有 N 个 run_query，
各自持有一份详情预算切片；父运行状态由子状态推导。复用 TaskRunner 的
进程池、结果目录与 python 解释器，只新增工作台专属编排。
"""

from __future__ import annotations

import threading
from pathlib import Path

from scripts import boss_cdp_raw as boss
from webui import ai as ai_service
from webui.constants import CLEANUP_EXPIRED_DAYS
from webui.process_executor import ArtifactSpec, run_with_deadline
from webui.task_runner_support import (
    PROJECT_ROOT,
    SCRAPER,
    _classify_risk_control_reason,
    _env,
    _read_json,
)
from webui.task_runners import TaskRunner
from webui.workbench import (
    MAX_DETAIL_BUDGET,
    allocate_detail_budget,
    normalize_job_link,
)


class WorkbenchRunner(TaskRunner):
    """T007: parent search run + child queries with budget and state machine.

    Reuses TaskRunner infrastructure (process pool, result dir, python
    executable) and adds the workbench-specific run orchestration:
    a parent search_run owns N child run_queries, each with its own
    detail budget slice.  The parent status is derived from child states.
    """

    def create_search_run(self, profile_id, *, keywords, confirmed_fields, mode="ai"):
        """Create parent run + child queries for up to 3 keywords.

        Keywords are already resolved by the caller (manual + AI merge).
        Budget is split evenly across queries with remainder to the first.
        """
        keywords = [str(k).strip() for k in (keywords or []) if str(k).strip()]
        if not keywords:
            raise ValueError("至少需要一个关键词才能创建搜索运行")
        if len(keywords) > 3:
            keywords = keywords[:3]

        profile_snapshot = dict(confirmed_fields or {})
        run = self.store.create_search_run(
            profile_id, profile_snapshot, mode, total_detail_budget=MAX_DETAIL_BUDGET,
        )
        run_id = run["id"]

        budgets = allocate_detail_budget(len(keywords), MAX_DETAIL_BUDGET)
        for ordinal, (keyword, budget) in enumerate(zip(keywords, budgets)):
            list_path = str(self.result_dir / f"list_{run_id}_{ordinal}.json")
            detail_path = str(self.result_dir / f"detail_{run_id}_{ordinal}.json")
            frozen_query = {
                "keyword": keyword, "city": profile_snapshot.get("city", ""),
                "filters": {key: profile_snapshot[key] for key in ("scale", "stage", "salary", "experience", "degree", "industry") if profile_snapshot.get(key)},
            }
            self.store.create_run_query(
                run_id, ordinal, frozen_query, list_path, detail_path, int(budget),
            )
        if self.executor:
            self.executor.submit(self._execute_search_run, run_id)
        return self.store.get_search_run(run_id)

    def _query_command(self, query):
        """Build one bounded invocation of the existing CDP scraper."""
        frozen = query["frozen_query"]
        command = [
            self.python_executable, str(SCRAPER),
            "--keyword", str(frozen["keyword"]),
            "--city", str(frozen["city"]),
            "--output", query["list_output_path"],
            "--detail-output", query["detail_output_path"],
            "--max-details", str(query["detail_budget"]),
        ]
        for name, value in frozen.get("filters", {}).items():
            command.extend([f"--{name}", str(value)])
        return command

    def _read_query_artifacts(self, run_id, query):
        """Read only this run's declared JSON artifacts after checking their paths."""
        root = self.result_dir.resolve()
        paths = [Path(query["list_output_path"]), Path(query["detail_output_path"])]
        for path in paths:
            try:
                resolved = path.resolve()
            except OSError as exc:
                raise ValueError("搜索产物路径无效") from exc
            if root not in resolved.parents or run_id not in resolved.name or not resolved.is_file():
                raise ValueError("搜索产物不存在或不属于当前运行")
        payload = _read_json(paths[0], {})
        details = _read_json(paths[1], [])
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list) or not isinstance(details, list):
            raise ValueError("搜索产物格式无效")
        return payload["jobs"], details

    def _persist_complete_jobs(self, run, query, jobs, details, seen_detail_ids=None):
        """Persist valid completed JDs; the database unique URL enforces run-wide dedupe."""
        seen_detail_ids = seen_detail_ids if seen_detail_ids is not None else set()
        detail_by_id = {
            str(item.get("job_id") or ""): item
            for item in details
            if isinstance(item, dict) and item.get("jd") and str(item.get("job_id") or "") not in seen_detail_ids
        }
        count = 0
        persisted_jobs = []
        for raw in jobs:
            if count >= int(query["detail_budget"]):
                break
            if not isinstance(raw, dict):
                continue
            detail = detail_by_id.get(str(raw.get("job_id") or ""))
            if not detail:
                continue
            excluded = [str(term).lower() for term in run["profile_snapshot"].get("excluded_terms", []) if str(term).strip()]
            searchable = " ".join([str(raw.get("title") or ""), str(raw.get("boss_name") or ""), str(detail.get("jd") or "")]).lower()
            if any(term in searchable for term in excluded):
                continue
            source_url = str(raw.get("job_link") or detail.get("job_link") or "")
            canonical_url = normalize_job_link(source_url)
            if not canonical_url:
                continue
            job = self.store.save_job(
                canonical_url, source_url,
                str(raw.get("title") or ""), str(raw.get("boss_name") or raw.get("company") or ""),
                str(raw.get("salary") or ""), str(raw.get("location") or ""), str(detail.get("jd") or ""),
            )
            self.store.link_profile_job(run["profile_id"], job["id"], run["id"], run["id"])
            self.store.append_search_event(run["id"], "job_completed", {"job_id": job["id"]})
            persisted_jobs.append(job)
            seen_detail_ids.add(str(raw.get("job_id") or ""))
            count += 1
        settings = self.store.get_ai_settings()
        if persisted_jobs and settings.get("is_configured"):
            credential_ref = self.store.get_credential_ref()
            api_key = ai_service.retrieve_api_key(credential_ref) if credential_ref else ""
            if api_key:
                try:
                    ranked_ids = ai_service.rank_jds(
                        run["profile_snapshot"],
                        [{"job_id": job["id"], "title": job["title"], "jd": job["jd"]} for job in persisted_jobs],
                        settings["endpoint_url"], api_key, settings.get("model", ""),
                    )
                    for rank, job_id in enumerate(ranked_ids):
                        self.store.link_profile_job(run["profile_id"], job_id, run["id"], run["id"], ai_rank=rank)
                except (ai_service.AISecurityError, ValueError):
                    # Ranking is optional: valid complete JDs still stream when AI fails.
                    pass
        return count

    def _stream_new_details(self, run_id, query, seen_detail_ids):
        """Read the scraper's atomic detail file while its process is still alive."""
        try:
            jobs, details = self._read_query_artifacts(run_id, query)
        except ValueError:
            return 0
        run = self.store.get_search_run(run_id)
        remaining = max(0, MAX_DETAIL_BUDGET - int(run["completed_jd_count"]))
        if not remaining:
            return 0
        original_budget = query["detail_budget"]
        query = dict(query)
        query["detail_budget"] = min(int(original_budget), remaining)
        return self._persist_complete_jobs(run, query, jobs, details, seen_detail_ids)

    def _execute_search_run(self, run_id):
        """Execute child queries sequentially and persist only validated complete JDs."""
        run = self.store.get_search_run(run_id)
        if run["status"] != "queued":
            return
        self.store.update_search_run(run_id, status="running")
        for query in self.store.list_run_queries(run_id):
            if self.store.get_search_run(run_id)["status"] == "interrupted":
                return
            self.store.update_run_query(query["id"], status="running")
            try:
                cancel_event = threading.Event()
                with self._process_lock:
                    self._cancel_events[run_id] = cancel_event
                seen_detail_ids = set()

                def stream_progress(query=query, seen_detail_ids=seen_detail_ids):
                    persisted = self._stream_new_details(run_id, query, seen_detail_ids)
                    if persisted:
                        current = self.store.get_search_run(run_id)
                        self.store.update_search_run(
                            run_id, completed_jd_count=current["completed_jd_count"] + persisted,
                        )
                if self.execution_mode == "in_process":
                    query_outcome = self._run_query_in_process(
                        run_id, query, cancel_event, stream_progress,
                    )
                    if query_outcome[0] != "succeeded":
                        raise ValueError(query_outcome[2] or "抓取器执行失败")
                else:
                    result = self.process_executor.execute(
                        self._query_command(query), timeout_seconds=600,
                        cwd=PROJECT_ROOT, env=_env(correlation_id=run_id),
                        cancel_event=cancel_event,
                        on_poll=stream_progress,
                        artifacts=[
                            ArtifactSpec(query["list_output_path"], root=self.result_dir),
                            ArtifactSpec(query["detail_output_path"], root=self.result_dir, required=False),
                        ],
                    )
                    if not result.ok:
                        raise ValueError(result.failure_code or "抓取器执行失败")
                jobs, _ = self._read_query_artifacts(run_id, query)
                persisted = self._stream_new_details(run_id, query, seen_detail_ids)
                self.store.update_run_query(query["id"], status="succeeded", counts={"completed_jd": persisted})
                current = self.store.get_search_run(run_id)
                self.store.update_search_run(
                    run_id,
                    discovered_count=current["discovered_count"] + len(jobs),
                    completed_jd_count=current["completed_jd_count"] + persisted,
                )
            except (OSError, ValueError):
                self.store.update_run_query(query["id"], status="failed", error_code="scrape_failed")
            finally:
                with self._process_lock:
                    self._processes.pop(run_id, None)
                    self._cancel_events.pop(run_id, None)
        if self.store.get_search_run(run_id)["status"] != "interrupted":
            self._finalize_run(run_id)
        self.store.cleanup_expired_jobs(days=CLEANUP_EXPIRED_DAYS)

    def _run_query_in_process(self, run_id, query, cancel_event, stream_progress):
        """in_process 模式执行单个 child query（合同 inprocess-runner §4.2）。

        把 ``_query_command`` 产出的 argv 翻译为 ``run_search_programmatic``
        直传参数；``on_poll`` 透传以保留增量入库语义；异常按 §3 映射表冻结。
        带硬超时（``in_process_timeout``），超时 → 协作取消 → 仍不退出
        则按 ``process_timeout`` 失败（与子进程模式语义对齐）。

        返回 ``(status, returncode, failure_code, output_tail)``；
        ``status`` ∈ ``{"succeeded", "failed", "interrupted"}``。
        """
        try:
            if cancel_event.is_set():
                return ("interrupted", -1, None, "")
            completed, payload = run_with_deadline(
                lambda: self._run_query_in_process_impl(
                    run_id, query, cancel_event, stream_progress,
                ),
                timeout_seconds=self.in_process_timeout,
                cancel_event=cancel_event,
            )
        except boss.SearchCancelled:
            return ("interrupted", -1, None, "")
        except boss.CDPUnavailableError as exc:
            return ("failed", 2, "source_cdp_unavailable", str(exc))
        except boss.LoginRequiredError as exc:
            return ("failed", 1, "source_login_required", str(exc))
        except boss.RequestLimitExceededError as exc:
            return ("failed", 11, "source_request_limit_exceeded", str(exc))
        except boss.RiskControlError as exc:
            return ("failed", 10, str(getattr(exc, "code", "") or "")
                    or _classify_risk_control_reason(exc.reason), exc.reason)
        except Exception as exc:
            return ("failed", -1, "process_failed", str(exc))
        if not completed:
            return ("failed", -1, "process_timeout", str(payload))
        return payload

    def _run_query_in_process_impl(self, run_id, query, cancel_event, stream_progress):
        """in-process 实际执行体（在 run_with_deadline 的 worker 线程中）。"""
        frozen = query["frozen_query"]
        boss.run_search_programmatic(
            keyword=str(frozen["keyword"]),
            city=str(frozen["city"]),
            pages=1,
            cdp_port=boss.DEFAULT_CDP_PORT,
            output_path=query["list_output_path"],
            detail_output_path=query["detail_output_path"],
            detail=True,
            max_details=int(query["detail_budget"]),
            filters=dict(frozen.get("filters") or {}),
            on_log=lambda line: self.store.append_log(run_id, line),
            on_poll=stream_progress,
            cancel_event=cancel_event,
        )
        return ("succeeded", 0, None, "")

    def _finalize_run(self, run_id):
        """Promote parent run to succeeded/partial/failed based on child states."""
        run = self.store.get_search_run(run_id)
        if run["status"] == "queued":
            self.store.update_search_run(run_id, status="running")
        queries = self.store.list_run_queries(run_id)
        if not queries:
            self.store.update_search_run(run_id, status="failed", error_code="no_queries")
            return self.store.get_search_run(run_id)

        states = [q["status"] for q in queries]
        succeeded = sum(1 for s in states if s == "succeeded")
        failed = sum(1 for s in states if s == "failed")
        if succeeded == len(queries):
            new_status = "succeeded"
        elif failed == len(queries):
            new_status = "failed"
        else:
            new_status = "partial"
        return self.store.update_search_run(run_id, status=new_status)

    def cancel_search_run(self, run_id):
        """Mark parent run interrupted; already-written jobs are preserved."""
        run = self.store.get_search_run(run_id)
        if run["status"] not in {"queued", "running"}:
            raise ValueError(f"只能取消等待中或运行中的运行，当前状态: {run['status']}")
        with self._process_lock:
            process = self._processes.get(run_id)
            cancel_event = self._cancel_events.get(run_id)
        if cancel_event is not None:
            cancel_event.set()
        if process is not None:
            process.terminate()
        return self.store.update_search_run(run_id, status="interrupted")
