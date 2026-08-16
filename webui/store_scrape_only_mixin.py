"""Scraped-only result snapshot data access for the task store.

A ``scraped_only`` round is a result_snapshot whose jobs carry no AI
verdict yet ("已抓取，未筛选"). Jobs are persisted without touching the
pending table so the "待筛选" wording never collides with AI-screening
"待人工确认" semantics. When the user later runs AI screening on the
same source task, the round is upgraded in place (same run_id, created_at
kept) instead of creating a new history round.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from webui.store_helpers import _now, _to_iso_timestamp, latest_screening_run_for_source

_RECORD_SNAPSHOT = "result_snapshot"
_STATUS_SCRAPED_ONLY = "scraped_only"


class ScrapeOnlyStoreMixin:
    """Store-level scraped-only snapshot writes and source lookups."""

    # ------------------------------------------------------------------
    # 保存：把已完成的抓取任务固化为“已抓取，未筛选”历史轮
    # ------------------------------------------------------------------

    def save_scraped_only_snapshot(
        self,
        result: dict,
        script_params: dict,
        *,
        scrape_task_id: str = "",
        started_at=None,
        finished_at=None,
        execution_config=None,
        platform: str = "",
        profile_summary: str = "",
        profile_facts: dict | None = None,
    ) -> str:
        """Persist an undecided scrape result as a ``scraped_only`` snapshot.

        Mirrors ``save_pipeline_result`` column layout but stores every job
        with an empty verdict and writes no pending rows. Returns run_id.
        """
        run_id = str(uuid.uuid4())
        now = _now()
        started_at = _to_iso_timestamp(started_at)
        finished_at = _to_iso_timestamp(finished_at) or now
        jobs = result.get("jobs") or []
        platform = str(platform or script_params.get("platform") or result.get("platform") or "boss")
        execution_json = {"execution_config": execution_config or {}}
        if platform:
            execution_json["platform"] = platform
        if scrape_task_id:
            execution_json["scrape_task_id"] = str(scrape_task_id)
        profile_facts_json = (
            json.dumps(profile_facts, ensure_ascii=False, sort_keys=True)
            if profile_facts else None
        )
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "INSERT INTO screening_runs "
                "(id, platform, frozen_filters_json, status, source_count, match_count, mismatch_count, "
                " pending_count, processed_count, created_at, updated_at, started_at, "
                " finished_at, search_params_json, execution_params_json, "
                " profile_summary, total_scraped, total_kept, total_dropped, record_kind, "
                " profile_facts_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "'result_snapshot', ?)",
                (
                    run_id,
                    platform,
                    json.dumps(script_params, ensure_ascii=False),
                    _STATUS_SCRAPED_ONLY,
                    len(jobs),
                    0, 0, 0, 0,
                    now, now, started_at, finished_at,
                    json.dumps(script_params, ensure_ascii=False),
                    json.dumps(execution_json, ensure_ascii=False),
                    str(profile_summary or ""),
                    len(jobs),
                    len(jobs),
                    0,
                    profile_facts_json,
                ),
            )
            self._insert_result_rows(conn, run_id, jobs, [], platform, default_verdict="")
        return run_id

    # ------------------------------------------------------------------
    # 来源查询：找同一抓取任务已保存的未筛选轮（补筛升级目标）
    # ------------------------------------------------------------------

    def latest_scraped_only_for_source(self, source_task_id: str) -> dict[str, Any] | None:
        """Return the newest ``scraped_only`` snapshot for a scrape task."""
        if not source_task_id:
            return None
        with self._connection() as conn:
            row = latest_screening_run_for_source(
                conn, source_task_id, statuses=(_STATUS_SCRAPED_ONLY,),
            )
        return self._screening_run_row(row) if row is not None else None

    # ------------------------------------------------------------------
    # 升级：AI 筛选完成后把未筛选轮原地升级为完成/部分结果
    # ------------------------------------------------------------------

    def upgrade_scraped_run(
        self,
        run_id: str,
        result: dict,
        script_params: dict,
        *,
        status: str = "done",
        execution_config=None,
        platform: str = "",
        profile_summary: str = "",
        profile_facts: dict | None = None,
        scrape_task_id: str = "",
        finished_at=None,
    ) -> str:
        """Rewrite one scraped-only round with AI screening output.

        Updates the existing ``screening_runs`` row (``created_at`` /
        ``started_at`` untouched) and replaces results/pending content, so
        the history round keeps its position and no new round is created.
        """
        jobs = result.get("jobs") or []
        dropped = result.get("dropped") or []
        match_count = sum(1 for job in jobs if job.get("verdict") == "match")
        mismatch_count = sum(
            1 for job in jobs if job.get("verdict") in ("not_match", "mismatch")
        )
        pending_jobs = [
            job for job in jobs
            if job.get("verdict") not in ("match", "not_match", "mismatch")
        ]
        if status == "done" and pending_jobs:
            status = "partial"
        platform = str(platform or script_params.get("platform") or "boss")
        execution_json = {"execution_config": execution_config or {}}
        if platform:
            execution_json["platform"] = platform
        if scrape_task_id:
            execution_json["scrape_task_id"] = str(scrape_task_id)
        profile_facts_json = (
            json.dumps(profile_facts, ensure_ascii=False, sort_keys=True)
            if profile_facts else None
        )
        script_params = dict(script_params or {})
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            existing_row = conn.execute(
                "SELECT search_params_json, execution_params_json FROM screening_runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()
            existing_search = {}
            parent_script = {}
            if existing_row is not None:
                try:
                    existing_search = json.loads(existing_row["search_params_json"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    existing_search = {}
                if not isinstance(existing_search, dict):
                    existing_search = {}
                try:
                    existing_exec = json.loads(existing_row["execution_params_json"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    existing_exec = {}
                if not isinstance(existing_exec, dict):
                    existing_exec = {}
                parent_task_id = str(existing_exec.get("scrape_task_id") or scrape_task_id or "")
                if parent_task_id:
                    parent_row = conn.execute(
                        "SELECT execution_params_json FROM screening_runs WHERE id = ?",
                        (parent_task_id,),
                    ).fetchone()
                    if parent_row is not None:
                        try:
                            parent_exec = json.loads(parent_row["execution_params_json"] or "{}")
                        except (json.JSONDecodeError, TypeError):
                            parent_exec = {}
                        if not isinstance(parent_exec, dict):
                            parent_exec = {}
                        parent_script = parent_exec.get("script_params") or {}
                        if not isinstance(parent_script, dict):
                            parent_script = {}
            script_params = {**parent_script, **existing_search, **script_params}
            cursor = conn.execute(
                "UPDATE screening_runs SET status = ?, platform = ?, "
                " frozen_filters_json = ?, search_params_json = ?, "
                " execution_params_json = ?, match_count = ?, mismatch_count = ?, "
                " pending_count = ?, processed_count = ?, profile_summary = ?, "
                " profile_facts_json = ?, total_scraped = ?, total_kept = ?, "
                " total_dropped = ?, source_count = ?, finished_at = ?, updated_at = ?, "
                " archived_at = NULL "
                "WHERE id = ? AND record_kind = 'result_snapshot'",
                (
                    str(status),
                    platform,
                    json.dumps(script_params, ensure_ascii=False),
                    json.dumps(script_params, ensure_ascii=False),
                    json.dumps(execution_json, ensure_ascii=False),
                    match_count,
                    mismatch_count,
                    len(pending_jobs),
                    match_count + mismatch_count,
                    str(profile_summary or ""),
                    profile_facts_json,
                    result.get("total_scraped", len(jobs)),
                    result.get("total_kept", len(jobs)),
                    result.get("total_dropped", len(dropped)),
                    result.get("total_scraped", len(jobs)),
                    _to_iso_timestamp(finished_at) or _now(),
                    _now(),
                    str(run_id),
                ),
            )
            # 目标轮不存在时保持 noop，避免向不存在的 run 插入外键孤儿行。
            if cursor.rowcount == 0:
                return str(run_id)
            conn.execute(
                "DELETE FROM screening_results WHERE run_id = ?", (str(run_id),)
            )
            conn.execute(
                "DELETE FROM screening_pending_results WHERE run_id = ?",
                (str(run_id),),
            )
            self._insert_result_rows(
                conn, run_id, jobs, dropped, platform, default_verdict="uncertain"
            )
            self._insert_pending_rows(conn, run_id, pending_jobs, platform)
        return str(run_id)

    # ------------------------------------------------------------------
    # 私有：结果行与 pending 行插入（保存/升级共用，字段与
    # save_pipeline_result 保持同构，避免两套列布局漂移）
    # ------------------------------------------------------------------

    def _insert_result_rows(
        self, conn, run_id: str, jobs: list, dropped: list, platform: str,
        *, default_verdict: str,
    ) -> None:
        now = _now()
        for job in jobs:
            conn.execute(
                "INSERT INTO screening_results "
                "(id, run_id, platform, platform_job_id, job_id, verdict, created_at, title, company, salary, "
                " location, tags, jd, source_url, verdict_reason, caveats_json, flags_json, is_dropped, "
                " experience, degree, extra_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (
                    str(uuid.uuid4()), str(run_id), platform,
                    str(job.get("platform_job_id") or job.get("job_id") or ""),
                    None,  # 内部 UUID 由收藏/反馈落库时回填
                    str(job.get("verdict") or default_verdict),
                    now,
                    str(job.get("title") or ""),
                    str(job.get("company") or job.get("boss_name") or ""),
                    str(job.get("salary") or ""),
                    str(job.get("location") or ""),
                    str(job.get("tags") or ""),
                    str(job.get("jd") or ""),
                    str(job.get("canonical_url") or job.get("source_url") or ""),
                    str(job.get("verdict_reason") or ""),
                    json.dumps(job.get("caveats") or [], ensure_ascii=False),
                    json.dumps(job.get("flags") or [], ensure_ascii=False),
                    str(job.get("experience") or ""),
                    str(job.get("degree") or ""),
                    json.dumps(job.get("extra") or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
        for job in dropped:
            conn.execute(
                "INSERT INTO screening_results "
                "(id, run_id, platform, platform_job_id, job_id, verdict, created_at, title, company, salary, "
                " location, tags, jd, source_url, verdict_reason, caveats_json, flags_json, is_dropped, "
                " experience, degree, extra_json) "
                "VALUES (?, ?, ?, ?, ?, 'dropped', ?, ?, ?, ?, ?, ?, '', ?, ?, '[]', '[]', 1, ?, ?, ?)",
                (
                    str(uuid.uuid4()), str(run_id), platform,
                    str(job.get("platform_job_id") or job.get("job_id") or ""),
                    None,
                    now,
                    str(job.get("title") or ""),
                    str(job.get("company") or job.get("boss_name") or ""),
                    str(job.get("salary") or ""),
                    str(job.get("location") or ""),
                    str(job.get("tags") or ""),
                    str(job.get("canonical_url") or job.get("source_url") or ""),
                    str(job.get("reason") or job.get("verdict_reason") or ""),
                    str(job.get("experience") or ""),
                    str(job.get("degree") or ""),
                    json.dumps(job.get("extra") or {}, ensure_ascii=False, sort_keys=True),
                ),
            )

    def _insert_pending_rows(self, conn, run_id: str, pending_jobs: list, platform: str) -> None:
        now = _now()
        for job in pending_jobs:
            failed_code = str(
                job.get("failed_code") or job.get("jd_failed_code")
                or ("ai_missing_job" if job.get("jd") else "detail_invalid")
            )
            failure_stage = str(
                job.get("failed_stage")
                or ("ai_fine" if job.get("jd") else "jd_detail")
            )
            conn.execute(
                "INSERT INTO screening_pending_results "
                "(id, run_id, platform, platform_job_id, failure_stage, retryable, attempts, "
                " last_failed_at, origin_zone, ai_payload_json, created_at, failed_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()), str(run_id), platform,
                    str(job.get("platform_job_id") or job.get("job_id") or ""),
                    failure_stage,
                    0 if failed_code == "job_offline" else 1,
                    int(job.get("attempts") or 1), now,
                    str(job.get("origin_zone") or "kept"),
                    json.dumps(job.get("ai_payload") or {}, ensure_ascii=False),
                    now, failed_code,
                ),
            )
