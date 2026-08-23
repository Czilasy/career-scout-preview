"""流水线结果持久化域（021 B2 拆分自 webui/store.py）：保存/加载/重数
latest pipeline result、平台最新结果、JD 更新。

以 mixin 形式由 webui/store.py 的 TaskStore 组装；实例状态（db_path、
_connection 等）来自 TaskStore 核心。模块不得 import webui.store。
"""

from __future__ import annotations

import json
import uuid

from webui.store_helpers import (
    _build_pipeline_result_rows,
    _decode_json,
    _now,
    _to_iso_timestamp,
)
from webui.store_constants import (
    _LATEST_RESULT_FILTER,
)


class StorePipelineResultsMixin:
    # ===================================================================
    # Pipeline result persistence (replaces latest_pipeline_result.json)
    # ===================================================================

    def save_pipeline_result(self, result: dict, script_params: dict, *,
                             started_at=None, finished_at=None, execution_config=None,
                             status: str = "done", execution_params: dict | None = None) -> str:
        """Persist a complete or partial pipeline run result to the database.

        Creates a screening_runs row and one screening_results row per job
        (both kept and dropped). ``status`` is the raw snapshot status:
        ``done`` for completed runs, ``partial`` for user-finished partial runs.
        Returns the run_id.
        """
        run_id = str(uuid.uuid4())
        now = _now()
        started_at = _to_iso_timestamp(started_at)
        finished_at = _to_iso_timestamp(finished_at) or now
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
        script_params = dict(script_params or {})
        extra_params = dict(execution_params or {})
        if extra_params:
            for key in ("platform", "scrape_task_id"):
                value = extra_params.get(key)
                if value is not None and not script_params.get(key):
                    script_params[key] = value
        execution_json = {"execution_config": execution_config or {}}
        if extra_params:
            execution_json.update(extra_params)
        profile_facts = result.get("profile_facts")
        profile_facts_json = (
            json.dumps(profile_facts, ensure_ascii=False, sort_keys=True)
            if profile_facts is not None else None
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
                    str(script_params.get("platform") or result.get("platform") or "boss"),
                    json.dumps(
                        script_params.get("screening")
                        if isinstance(script_params.get("screening"), dict) else {},
                        ensure_ascii=False,
                    ),
                    str(status),
                    result.get("total_scraped", 0),
                    match_count,
                    mismatch_count,
                    len(pending_jobs),
                    match_count + mismatch_count,
                    now, now, started_at, finished_at,
                    json.dumps(script_params, ensure_ascii=False),
                    json.dumps(execution_json, ensure_ascii=False),
                    result.get("profile_summary", ""),
                    result.get("total_scraped", 0),
                    result.get("total_kept", 0),
                    result.get("total_dropped", len(dropped)),
                    profile_facts_json,
                ),
            )
            # Insert kept jobs
            for job in jobs:
                platform = str(script_params.get("platform") or result.get("platform") or "boss")
                conn.execute(
                    "INSERT OR REPLACE INTO screening_results "
                    "(id, run_id, platform, platform_job_id, job_id, verdict, created_at, title, company, salary, "
                    " location, tags, jd, source_url, verdict_reason, caveats_json, flags_json, is_dropped, "
                    " experience, degree, extra_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                    (
                        str(uuid.uuid4()), run_id, platform,
                        str(job.get("platform_job_id") or job.get("job_id") or ""),
                        None,  # 内部 UUID 由收藏/反馈落库时回填
                        job.get("verdict", "uncertain"),
                        now,
                        job.get("title", ""),
                        job.get("company", ""),
                        job.get("salary", ""),
                        job.get("location", ""),
                        job.get("tags", ""),
                        job.get("jd", ""),
                        job.get("canonical_url") or job.get("source_url") or "",
                        job.get("verdict_reason", ""),
                        json.dumps(job.get("caveats") or [], ensure_ascii=False),
                        json.dumps(job.get("flags") or [], ensure_ascii=False),
                        job.get("experience", ""),
                        job.get("degree", ""),
                        json.dumps(job.get("extra") or {}, ensure_ascii=False, sort_keys=True),
                    ),
                )
            # Insert dropped jobs
            for job in dropped:
                platform = str(script_params.get("platform") or result.get("platform") or "boss")
                conn.execute(
                    "INSERT OR REPLACE INTO screening_results "
                    "(id, run_id, platform, platform_job_id, job_id, verdict, created_at, title, company, salary, "
                    " location, tags, jd, source_url, verdict_reason, caveats_json, is_dropped, "
                    " experience, degree, extra_json) "
                    "VALUES (?, ?, ?, ?, ?, 'dropped', ?, ?, ?, ?, ?, ?, '', ?, ?, '[]', 1, ?, ?, ?)",
                    (
                        str(uuid.uuid4()), run_id, platform,
                        str(job.get("platform_job_id") or job.get("job_id") or ""),
                        None,
                        now,
                        job.get("title", ""),
                        job.get("company", ""),
                        job.get("salary", ""),
                        job.get("location", ""),
                        job.get("tags", ""),
                        job.get("canonical_url", ""),
                        job.get("reason", ""),
                        job.get("experience", ""),
                        job.get("degree", ""),
                        json.dumps(job.get("extra") or {}, ensure_ascii=False, sort_keys=True),
                    ),
                )
            for job in pending_jobs:
                failed_code = str(
                    job.get("failed_code") or job.get("jd_failed_code")
                    or ("ai_missing_job" if job.get("jd") else "detail_invalid")
                )
                failure_stage = str(
                    job.get("failed_stage")
                    or ("ai_fine" if job.get("jd") else "jd_detail")
                )
                ai_payload = dict(job.get("ai_payload") or {})
                ai_payload.setdefault(
                    "reason", str(job.get("verdict_reason") or failed_code))
                ai_payload.setdefault("evidence", failed_code)
                ai_payload.setdefault(
                    "evidence_detail",
                    str(job.get("jd_failed_evidence") or ""),
                )
                ai_payload.setdefault("next_action", "retry_jd")
                conn.execute(
                    "INSERT INTO screening_pending_results "
                    "(id, run_id, platform, platform_job_id, failure_stage, retryable, attempts, "
                    " last_failed_at, origin_zone, ai_payload_json, created_at, failed_code) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()), run_id,
                        str(script_params.get("platform") or result.get("platform") or "boss"),
                        str(job.get("platform_job_id") or job.get("job_id") or ""),
                        failure_stage,
                        0 if failed_code == "job_offline" else 1,
                        int(job.get("attempts") or 1), now,
                        str(job.get("origin_zone") or "kept"),
                        json.dumps(ai_payload, ensure_ascii=False),
                        now, failed_code,
                    ),
                )
        return run_id

    def load_latest_pipeline_result(self, run_id: str | None = None) -> dict | None:
        """Load the most recent successful pipeline run from the database.

        Returns a payload matching the old JSON file format:
        {"saved_at": ..., "script_params": {...}, "result": {...}}
        or None if no successful run exists.
        """
        with self._connection() as conn:
            if run_id:
                run = conn.execute(
                    "SELECT * FROM screening_runs WHERE id = ? "
                    "AND record_kind = 'result_snapshot' LIMIT 1",
                    (str(run_id),),
                ).fetchone()
            else:
                run = conn.execute(
                    "SELECT * FROM screening_runs WHERE " + _LATEST_RESULT_FILTER + " "
                    "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                ).fetchone()
            if run is None:
                return None
            run = dict(run)
            rows = conn.execute(
                "SELECT * FROM screening_results WHERE run_id = ? ORDER BY rowid",
                (run["id"],),
            ).fetchall()

        jobs, dropped = _build_pipeline_result_rows(rows)

        script_params = {}
        try:
            script_params = json.loads(run.get("search_params_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass

        execution_params = {}
        try:
            execution_params = json.loads(run.get("execution_params_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass

        result = {
            "ok": True,
            "jobs": jobs,
            "dropped": dropped,
            "total_scraped": run.get("total_scraped", 0),
            "total_kept": run.get("total_kept", len(jobs)),
            "total_matched": run.get("match_count", 0),
            "total_dropped": run.get("total_dropped", len(dropped)),
            "profile_summary": run.get("profile_summary", ""),
            # B033：画像事实快照随结果透传（刷新恢复、补筛复用快照的读取源）
            "profile_facts": _decode_json(run.get("profile_facts_json"), None),
            "error": "",
        }
        return {
            "run_id": run["id"],
            "platform": run.get("platform"),
            "saved_at": run["created_at"],
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "script_params": script_params,
            "status": (
                "scraped_only" if run.get("status") == "scraped_only"
                else "completed_with_pending" if run.get("status") == "partial"
                else "completed"
            ),
            "execution_config": execution_params.get("execution_config") or {},
            "scrape_task_id": str(execution_params.get("scrape_task_id") or ""),
            "result": result,
        }

    def recount_pipeline_result(self, run_id):
        """Recompute result_snapshot counts after recrawl write-back."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT record_kind FROM screening_runs WHERE id = ?",
                (str(run_id),),
            ).fetchone()
            if row is None or row["record_kind"] != "result_snapshot":
                return None
            rows = conn.execute(
                "SELECT verdict, is_dropped FROM screening_results WHERE run_id = ?",
                (str(run_id),),
            ).fetchall()
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_pending_results WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()["n"]
        match = mismatch = kept = dropped = 0
        for row in rows:
            if row["is_dropped"]:
                dropped += 1
                continue
            kept += 1
            verdict = row["verdict"] or ""
            try:
                parsed = json.loads(verdict)
                if isinstance(parsed, dict):
                    verdict = str(parsed.get("verdict") or "")
            except (json.JSONDecodeError, TypeError):
                pass
            if verdict == "match":
                match += 1
            elif verdict in ("not_match", "mismatch"):
                mismatch += 1
        status = "done" if pending == 0 else "partial"
        now = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "UPDATE screening_runs SET status = ?, match_count = ?, mismatch_count = ?, "
                " pending_count = ?, processed_count = ?, total_kept = ?, "
                " total_dropped = ?, source_count = ?, finished_at = ?, updated_at = ? WHERE id = ?",
                (
                    status, match, mismatch, pending, match + mismatch,
                    kept, dropped, kept + dropped, now, now, str(run_id),
                ),
            )
        return {
            "status": status, "match_count": match, "mismatch_count": mismatch,
            "pending_count": pending, "processed_count": match + mismatch,
            "total_kept": kept, "total_dropped": dropped,
        }

    def load_latest_pipeline_result_for_platform(self, platform: str) -> dict | None:
        """T409: 按平台加载最近一次成功结果。

        与 load_latest_pipeline_result 共用 _build_pipeline_result_rows，
        保证 verdict/caveats/tags/extra 等字段一致（刷新后结果不落“待确认”）。
        """
        with self._connection() as conn:
            run = conn.execute(
                "SELECT * FROM screening_runs WHERE platform=? AND " + _LATEST_RESULT_FILTER + " "
                "ORDER BY created_at DESC LIMIT 1",
                (str(platform),),
            ).fetchone()
            if run is None:
                return None
            run = dict(run)
            rows = conn.execute(
                "SELECT * FROM screening_results WHERE run_id = ? ORDER BY rowid",
                (run["id"],),
            ).fetchall()

        jobs, dropped = _build_pipeline_result_rows(rows)
        script_params = _decode_json(run.get("search_params_json"), {})
        execution_params = _decode_json(run.get("execution_params_json"), {})
        result = {
            "ok": True,
            "jobs": jobs,
            "dropped": dropped,
            "total_scraped": run.get("total_scraped", 0),
            "total_kept": run.get("total_kept", len(jobs)),
            "total_matched": run.get("match_count", 0),
            "total_dropped": run.get("total_dropped", len(dropped)),
            "profile_summary": run.get("profile_summary", ""),
            # B033：画像事实快照随结果透传（刷新恢复、补筛复用快照的读取源）
            "profile_facts": _decode_json(run.get("profile_facts_json"), None),
            "error": "",
        }
        return {
            "run_id": run["id"],
            "platform": run.get("platform"),
            "saved_at": run.get("created_at"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "script_params": script_params,
            "status": (
                "scraped_only" if run.get("status") == "scraped_only"
                else "completed_with_pending" if run.get("status") == "partial"
                else "completed"
            ),
            "execution_config": execution_params.get("execution_config") or {},
            "scrape_task_id": str(execution_params.get("scrape_task_id") or ""),
            "result": result,
        }

    def latest_pipeline_result_saved_at(self) -> str | None:
        """Return the latest durable update time of a result snapshot.

        Recrawl updates the existing source snapshot in place, so ``created_at``
        is the original screening time and cannot tell whether a newer result
        superseded an older paused task.
        """
        with self._connection() as conn:
            row = conn.execute(
                "SELECT updated_at FROM screening_runs "
                "WHERE status IN ('done', 'partial', 'scraped_only') AND record_kind = 'result_snapshot' "
                "AND archived_at IS NULL "
                "ORDER BY updated_at DESC LIMIT 1",
            ).fetchone()
        return row["updated_at"] if row is not None else None

    def update_pipeline_job_jd(self, run_id: str, job_id: str, jd: str):
        """Update the JD text for a specific job in a pipeline run (补抓 JD)."""
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "UPDATE screening_results SET jd = ? WHERE run_id = ? AND platform_job_id = ?",
                (jd, str(run_id), str(job_id)),
            )
