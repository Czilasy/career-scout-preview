"""抓取进度域（021 B2 拆分自 webui/store.py）：待确认岗位、组合结果、
页级进度、recrawl 检查点与 run 岗位装载。

以 mixin 形式由 webui/store.py 的 TaskStore 组装；实例状态（db_path、
_connection 等）来自 TaskStore 核心。模块不得 import webui.store。
"""

from __future__ import annotations

import json

from webui.store_helpers import (
    _now,
    _uuid,
)


class StoreScrapeRunsMixin:

    # -- pending results（待确认岗位，FR-011~016/FR-040） -------------------

    def insert_pending_result(self, run_id, job_id, *, failure_stage, retryable=True,
                              attempts=1, origin_zone="match", ai_payload_json=None,
                              failed_code=None, platform="boss"):
        """登记一条待确认岗位（独立失败）。同一 (run_id, job_id) 重复写则更新。

        FR-040：必须带具体 failed_code，禁止仅用"未抓到 JD"等模糊描述。
        """
        if not failed_code and failure_stage:
            # 兜底：failure_stage 推默认 code（仍要求调用方尽量传 failed_code）
            failed_code = failed_code or failure_stage
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "INSERT INTO screening_pending_results "
                "(id, run_id, platform, platform_job_id, failure_stage, retryable, attempts, last_failed_at, "
                " origin_zone, ai_payload_json, created_at, failed_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, platform_job_id) DO UPDATE SET "
                " platform = excluded.platform, "
                " failure_stage = excluded.failure_stage, "
                " retryable = excluded.retryable, "
                " attempts = excluded.attempts, "
                " last_failed_at = excluded.last_failed_at, "
                " origin_zone = excluded.origin_zone, "
                " ai_payload_json = excluded.ai_payload_json, "
                " failed_code = excluded.failed_code",
                (
                    _uuid(), str(run_id), str(platform), str(job_id), str(failure_stage),
                    1 if retryable else 0, int(attempts), ts,
                    str(origin_zone),
                    json.dumps(ai_payload_json or {}, ensure_ascii=False),
                    ts, str(failed_code) if failed_code else None,
                ),
            )
        self.update_pending_count(run_id)
        return self.get_pending_result(run_id, job_id)

    def update_pending_count(self, run_id):
        """从 screening_pending_results 实时计数并写回 screening_runs.pending_count。

        FR-016/SC-018：pending_count 必须反映真实待确认数，不得恒为 0。
        """
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_pending_results WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()
        count = int(row["n"] or 0)
        # 直接写库，绕过状态机（pending_count 是数据字段，不是状态）
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "UPDATE screening_runs SET pending_count = ?, updated_at = ? WHERE id = ?",
                (count, _now(), str(run_id)),
            )
        return count

    def get_pending_result(self, run_id, job_id):
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM screening_pending_results WHERE run_id = ? AND platform_job_id = ?",
                (str(run_id), str(job_id)),
            ).fetchone()
        return self._pending_result_row(row) if row is not None else None

    def list_pending_results(self, run_id):
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM screening_pending_results WHERE run_id = ? "
                "ORDER BY last_failed_at ASC",
                (str(run_id),),
            ).fetchall()
        return [self._pending_result_row(r) for r in rows]

    def delete_pending_result(self, run_id, job_id):
        """补救成功后从待确认表移除。返回是否实际删除。"""
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            cur = conn.execute(
                "DELETE FROM screening_pending_results WHERE run_id = ? AND platform_job_id = ?",
                (str(run_id), str(job_id)),
            )
            deleted = cur.rowcount > 0
        if deleted:
            self.update_pending_count(run_id)
        return deleted

    def _pending_result_row(self, row) -> dict:
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "job_id": row["platform_job_id"],
            "platform": row["platform"],
            "failure_stage": row["failure_stage"],
            "retryable": bool(row["retryable"]),
            "attempts": int(row["attempts"]),
            "last_failed_at": row["last_failed_at"],
            "origin_zone": row["origin_zone"],
            "ai_payload": json.loads(row["ai_payload_json"] or "{}"),
            "created_at": row["created_at"],
            "failed_code": row["failed_code"] if "failed_code" in row.keys() else None,
        }

    # -- checkpoints（断点续抓，FR-023） -----------------------------------

    def save_scrape_combo_result(self, run_id, combo_key, jobs, completed_combos):
        """Atomically persist one completed combination and its checkpoint."""
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            for job in jobs or []:
                if not isinstance(job, dict):
                    continue
                job_id = str(job.get("platform_job_id") or job.get("job_id") or job.get("source_url") or "").strip()
                if not job_id:
                    continue
                conn.execute(
                    "INSERT INTO scrape_run_jobs "
                    "(run_id, platform_job_id, combo_key, job_payload_json, scraped_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(run_id, platform_job_id) DO UPDATE SET "
                    " combo_key = excluded.combo_key, "
                    " job_payload_json = excluded.job_payload_json, "
                    " scraped_at = excluded.scraped_at",
                    (
                        str(run_id), job_id, str(combo_key),
                        json.dumps(job, ensure_ascii=False), ts,
                    ),
                )
            conn.execute(
                "INSERT INTO pipeline_checkpoints "
                "(run_id, stage, completed_keys_json, saved_at) VALUES (?, 'scrape', ?, ?) "
                "ON CONFLICT(run_id, stage) DO UPDATE SET "
                " completed_keys_json = excluded.completed_keys_json, "
                " saved_at = excluded.saved_at",
                (
                    str(run_id),
                    json.dumps(list(completed_combos or []), ensure_ascii=False),
                    ts,
                ),
            )
            # The checkpoint is the durable source of truth for scrape combo
            # progress. Keep the run projection in the same transaction so a
            # refresh (or a resumed worker) cannot observe the old count.
            conn.execute(
                "UPDATE screening_runs SET processed_count = CASE "
                "WHEN processed_count < ? THEN ? ELSE processed_count END, "
                "updated_at = ? WHERE id = ? AND record_kind = 'process_log'",
                (
                    len(completed_combos or []),
                    len(completed_combos or []),
                    ts,
                    str(run_id),
                ),
            )

    def save_scrape_page_progress(self, run_id, combo_key, progress_event):
        """Atomically persist one completed page and its jobs snapshot.

        ``progress_event`` 必须携带 page/target_pages/resume_page/jobs_snapshot
        等结构化页级事实；岗位快照与页级 checkpoint 同事务提交，失败回滚。
        """
        event = dict(progress_event or {})
        combo_key = str(event.get("combo_key") or combo_key or "").strip()
        if not combo_key:
            raise ValueError("combo_key required")
        snapshot = event.get("jobs_snapshot") or []
        page = max(0, int(event.get("page") or 0))
        target = max(1, int(event.get("target_pages") or 1))
        resume = max(1, int(event.get("resume_page") or page + 1))
        last_page = max(0, int(event.get("last_completed_page") or page))
        jobs_count = max(0, int(event.get("jobs_count") or len(snapshot) or 0))
        has_more = 1 if bool(event.get("has_more", True)) else 0
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            for job in snapshot:
                if not isinstance(job, dict):
                    continue
                job_id = str(
                    job.get("platform_job_id") or job.get("job_id") or job.get("source_url") or ""
                ).strip()
                if not job_id:
                    continue
                conn.execute(
                    "INSERT INTO scrape_run_jobs "
                    "(run_id, platform_job_id, combo_key, job_payload_json, scraped_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(run_id, platform_job_id) DO UPDATE SET "
                    " combo_key = excluded.combo_key, "
                    " job_payload_json = excluded.job_payload_json, "
                    " scraped_at = excluded.scraped_at",
                    (
                        str(run_id), job_id, combo_key,
                        json.dumps(job, ensure_ascii=False), ts,
                    ),
                )
            conn.execute(
                "INSERT INTO scrape_page_progress "
                "(run_id, combo_key, completed_pages, target_pages, resume_page, has_more, jobs_count, last_completed_page, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, combo_key) DO UPDATE SET "
                " completed_pages = excluded.completed_pages, "
                " target_pages = excluded.target_pages, "
                " resume_page = excluded.resume_page, "
                " has_more = excluded.has_more, "
                " jobs_count = excluded.jobs_count, "
                " last_completed_page = excluded.last_completed_page, "
                " updated_at = excluded.updated_at",
                (
                    str(run_id), combo_key, page, target, resume, has_more,
                    jobs_count, last_page, ts,
                ),
            )

    def load_scrape_page_progress(self, run_id):
        """Load persisted per-page scrape checkpoints, newest first."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT run_id, combo_key, completed_pages, target_pages, "
                "resume_page, has_more, jobs_count, last_completed_page, updated_at "
                "FROM scrape_page_progress WHERE run_id = ? "
                "ORDER BY updated_at DESC, combo_key ASC", (str(run_id),),
            ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "combo_key": row["combo_key"],
                "completed_pages": int(row["completed_pages"]),
                "target_pages": int(row["target_pages"]),
                "resume_page": int(row["resume_page"]),
                "has_more": bool(row["has_more"]),
                "jobs_count": int(row["jobs_count"]),
                "last_completed_page": int(row["last_completed_page"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def save_recrawl_jd_and_checkpoint(
            self, source_run_id, recrawl_run_id, jd_by_job, completed_job_ids,
            *, extra_by_job=None):
        """Atomically persist partial recrawl JDs and their resume checkpoint.

        ``extra_by_job``（028 B084）：job_id → 招聘者活跃事实字典；提供的岗位
        在写回 JD 的同一事务里把事实合并进 screening_results.extra_json
        （只覆盖 recruiter_activity 键，保留既有键），供后续轮次读取判定。
        """
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            for job_id, jd in (jd_by_job or {}).items():
                fact = (extra_by_job or {}).get(str(job_id))
                if fact is None:
                    conn.execute(
                        "UPDATE screening_results SET jd = ? "
                        "WHERE run_id = ? AND platform_job_id = ?",
                        (str(jd), str(source_run_id), str(job_id)),
                    )
                    continue
                row = conn.execute(
                    "SELECT extra_json FROM screening_results "
                    "WHERE run_id = ? AND platform_job_id = ?",
                    (str(source_run_id), str(job_id)),
                ).fetchone()
                try:
                    current = json.loads(row["extra_json"]) if row and row["extra_json"] else {}
                except (TypeError, ValueError):
                    current = {}
                if not isinstance(current, dict):
                    current = {}
                current.update({"recruiter_activity": fact})
                conn.execute(
                    "UPDATE screening_results SET jd = ?, extra_json = ? "
                    "WHERE run_id = ? AND platform_job_id = ?",
                    (str(jd), json.dumps(current, ensure_ascii=False),
                     str(source_run_id), str(job_id)),
                )
            conn.execute(
                "INSERT INTO pipeline_checkpoints "
                "(run_id, stage, completed_keys_json, saved_at) "
                "VALUES (?, 'recrawl_jd', ?, ?) "
                "ON CONFLICT(run_id, stage) DO UPDATE SET "
                " completed_keys_json = excluded.completed_keys_json, "
                " saved_at = excluded.saved_at",
                (
                    str(recrawl_run_id),
                    json.dumps(sorted(set(completed_job_ids or [])), ensure_ascii=False),
                    ts,
                ),
            )

    def load_scrape_run_jobs(self, run_id, combo_key=None):
        """Load persisted job payloads for a scrape run, optionally one combo."""
        params: list = [str(run_id)]
        combo_filter = ""
        if combo_key:
            combo_filter = " AND combo_key = ?"
            params.append(str(combo_key))
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT job_payload_json FROM scrape_run_jobs "
                f"WHERE run_id = ?{combo_filter} "
                "ORDER BY scraped_at ASC, platform_job_id ASC",
                tuple(params),
            ).fetchall()
        jobs = []
        for row in rows:
            try:
                payload = json.loads(row["job_payload_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict):
                jobs.append(payload)
        return jobs

    def count_scrape_run_jobs(self, run_id) -> int:
        """已持久化抓取岗位数（scrape_run_jobs 行数），是恢复计数的权威来源。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM scrape_run_jobs WHERE run_id = ?", (str(run_id),)
            ).fetchone()
        return int(row["n"] or 0) if row is not None else 0

    def save_checkpoint(self, run_id, stage, keys):
        """保存某阶段的已完成 key 列表。同 (run_id, stage) 覆盖。"""
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "INSERT INTO pipeline_checkpoints (run_id, stage, completed_keys_json, saved_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(run_id, stage) DO UPDATE SET "
                " completed_keys_json = excluded.completed_keys_json, "
                " saved_at = excluded.saved_at",
                (
                    str(run_id), str(stage),
                    json.dumps(list(keys or []), ensure_ascii=False),
                    ts,
                ),
            )

    def load_checkpoint(self, run_id, stage):
        """加载某阶段的已完成 key 列表；无记录返回空集合。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT completed_keys_json FROM pipeline_checkpoints "
                "WHERE run_id = ? AND stage = ?",
                (str(run_id), str(stage)),
            ).fetchone()
        if row is None:
            return set()
        try:
            return set(json.loads(row["completed_keys_json"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            return set()

    def list_checkpoints(self, run_id):
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT stage, completed_keys_json, saved_at FROM pipeline_checkpoints "
                "WHERE run_id = ? ORDER BY saved_at ASC",
                (str(run_id),),
            ).fetchall()
        return [
            {
                "stage": r["stage"],
                "completed_keys": json.loads(r["completed_keys_json"] or "[]"),
                "saved_at": r["saved_at"],
            }
            for r in rows
        ]

    def delete_checkpoint(self, run_id, stage=None):
        """删除断点。stage=None 删除该 run 全部断点（任务成功收尾时用）。"""
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            if stage is None:
                conn.execute(
                    "DELETE FROM pipeline_checkpoints WHERE run_id = ?",
                    (str(run_id),),
                )
            else:
                conn.execute(
                    "DELETE FROM pipeline_checkpoints WHERE run_id = ? AND stage = ?",
                    (str(run_id), str(stage)),
                )
