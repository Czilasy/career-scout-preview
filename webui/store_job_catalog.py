"""岗位目录与反馈域（021 B2 拆分自 webui/store.py）：run query、jobs 表
crud、档案-岗位关联、反馈与偏好版本、过期清理。

以 mixin 形式由 webui/store.py 的 TaskStore 组装；实例状态（db_path、
_connection 等）来自 TaskStore 核心。模块不得 import webui.store。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from webui.constants import CLEANUP_EXPIRED_DAYS
from webui.store_helpers import (
    _now,
    _uuid,
)
from webui.store_constants import (
    FEEDBACK_ACTIONS,
    FEEDBACK_REASONS,
    PROFILE_JOB_STATUSES,
    _ERROR_CODE_SET_CLAUSE,
    _STATUS_SET_CLAUSE,
    _UPDATED_AT_SET_CLAUSE,
)


class StoreJobCatalogMixin:

    # -- run queries -------------------------------------------------------

    def create_run_query(self, run_id, ordinal, frozen_query, list_output_path, detail_output_path, detail_budget):
        qid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO run_queries (id, run_id, ordinal, frozen_query_json, list_output_path, detail_output_path, status, detail_budget, counts_json, error_code, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, '{}', NULL, ?, ?)",
                (qid, str(run_id), int(ordinal), json.dumps(frozen_query, ensure_ascii=False), list_output_path, detail_output_path, int(detail_budget), ts, ts),
            )
        return self.get_run_query(qid)

    def get_run_query(self, query_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM run_queries WHERE id = ?", (str(query_id),)).fetchone()
        if row is None:
            raise KeyError(query_id)
        return self._query_row(row)

    def update_run_query(self, query_id, status=None, counts=None, error_code=None):
        sets = []
        params = []
        if status:
            sets.append(_STATUS_SET_CLAUSE)
            params.append(status)
        if counts is not None:
            sets.append("counts_json = ?")
            params.append(json.dumps(counts, ensure_ascii=False))
        if error_code is not None:
            sets.append(_ERROR_CODE_SET_CLAUSE)
            params.append(error_code)
        sets.append(_UPDATED_AT_SET_CLAUSE)
        params.append(_now())
        params.append(str(query_id))
        with self._connection() as conn:
            conn.execute(f"UPDATE run_queries SET {', '.join(sets)} WHERE id = ?", params)
        return self.get_run_query(query_id)

    def list_run_queries(self, run_id) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM run_queries WHERE run_id = ? ORDER BY ordinal ASC", (str(run_id),)
            ).fetchall()
        return [self._query_row(row) for row in rows]

    def _query_row(self, row) -> dict:
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "ordinal": row["ordinal"],
            "frozen_query": json.loads(row["frozen_query_json"] or "{}"),
            "list_output_path": row["list_output_path"],
            "detail_output_path": row["detail_output_path"],
            "status": row["status"],
            "detail_budget": row["detail_budget"],
            "counts": json.loads(row["counts_json"] or "{}"),
            "error_code": row["error_code"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # -- jobs --------------------------------------------------------------

    def save_job(self, canonical_url, source_url, title, company, salary, location, jd):
        ts = _now()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        jid = _uuid()
        with self._connection() as conn:
            # ON CONFLICT(canonical_url) DO UPDATE: 单语句 UPSERT，避免并发下
            # SELECT-then-INSERT 撞 UNIQUE(canonical_url)。
            # RETURNING id 取回实际写入行的 id（新插入=jid，已存在=原 id）。
            row = conn.execute(
                "INSERT INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(canonical_url) DO UPDATE SET "
                "source_url = excluded.source_url, title = excluded.title, company = excluded.company, "
                "salary = excluded.salary, location = excluded.location, jd = excluded.jd, "
                "last_seen_at = excluded.last_seen_at "
                "RETURNING id",
                (jid, canonical_url, source_url, title, company, salary, location, jd, ts, ts, expires_at),
            ).fetchone()
            jid = row["id"]
        return self.get_job(jid)

    def get_job(self, job_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return dict(row)

    def update_job_platform_identity(self, job_id, platform, platform_job_id=None):
        """回写 jobs 表的 platform / platform_job_id（T711 平台身份完整）。

        ``save_job`` 走 canonical_url UPSERT，不写 platform（默认 'boss'）。
        pipeline 收藏/反馈落库的智联岗位需要在这里补回真实平台身份，
        避免刷新后 jobs.platform 错配成 'boss'。``platform_job_id`` 非空时
        一并写入；为空时只更新 platform，保留原 platform_job_id。
        """
        if not job_id or not platform:
            return
        with self._connection() as conn:
            if platform_job_id:
                conn.execute(
                    "UPDATE jobs SET platform=?, platform_job_id=? WHERE id=?",
                    (str(platform), str(platform_job_id), str(job_id)),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET platform=? WHERE id=?",
                    (str(platform), str(job_id)),
                )

    def list_jobs_by_ids(self, job_ids) -> dict:
        """批量查询 jobs，一次 SELECT WHERE id IN (...)。

        返回 {job_id: row_dict}。不存在的 job_id 不在结果中。
        空列表返回 {}。单次连接，消除 N+1 模式。
        """
        ids = [str(jid) for jid in job_ids if jid]
        if not ids:
            return {}
        # 分批避免 SQL IN 列表过长（SQLite 限制 SQLITE_MAX_VARIABLE_NUMBER，默认 999）
        out: dict = {}
        with self._connection() as conn:
            for i in range(0, len(ids), 500):
                batch = ids[i:i + 500]
                placeholders = ",".join("?" * len(batch))
                rows = conn.execute(
                    f"SELECT * FROM jobs WHERE id IN ({placeholders})",
                    batch,
                ).fetchall()
                for row in rows:
                    out[str(row["id"])] = dict(row)
        return out

    def update_job_expiry(self, job_id, expires_at):
        with self._connection() as conn:
            conn.execute("UPDATE jobs SET expires_at = ? WHERE id = ?", (expires_at.isoformat() if hasattr(expires_at, "isoformat") else str(expires_at), str(job_id)))
        return self.get_job(job_id)

    # -- profile jobs ------------------------------------------------------

    def link_profile_job(self, profile_id, job_id, first_run_id, last_run_id, ai_rank=None, status="new"):
        if status not in PROFILE_JOB_STATUSES:
            raise ValueError(f"未知岗位状态: {status}")
        ts = _now()
        with self._connection() as conn:
            # ON CONFLICT(profile_id, job_id) DO UPDATE: 单语句 UPSERT，避免并发下
            # SELECT-then-INSERT 撞 PRIMARY KEY(profile_id, job_id)。
            conn.execute(
                "INSERT INTO profile_jobs (profile_id, job_id, first_run_id, last_run_id, ai_rank, shown_at, status, note, applied_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL) "
                "ON CONFLICT(profile_id, job_id) DO UPDATE SET "
                "last_run_id = excluded.last_run_id, ai_rank = excluded.ai_rank, "
                "shown_at = COALESCE(shown_at, excluded.shown_at)",
                (str(profile_id), str(job_id), first_run_id, last_run_id, ai_rank, ts, status),
            )
        return self.get_profile_job(profile_id, job_id)

    def update_profile_job(self, profile_id, job_id, status=None, note=None, applied_at=None):
        # 字段名来自内部调用方（hardcoded），非用户输入，无需白名单
        sets = []
        params = []
        if status:
            if status not in PROFILE_JOB_STATUSES:
                raise ValueError(f"未知岗位状态: {status}")
            sets.append(_STATUS_SET_CLAUSE)
            params.append(status)
        if note is not None:
            sets.append("note = ?")
            params.append(note)
        if applied_at is not None:
            sets.append("applied_at = ?")
            params.append(applied_at)
        if sets:
            params.extend([str(profile_id), str(job_id)])
            with self._connection() as conn:
                conn.execute(f"UPDATE profile_jobs SET {', '.join(sets)} WHERE profile_id = ? AND job_id = ?", params)
        return self.get_profile_job(profile_id, job_id)

    def get_profile_job(self, profile_id, job_id) -> dict:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM profile_jobs WHERE profile_id = ? AND job_id = ?",
                (str(profile_id), str(job_id)),
            ).fetchone()
        if row is None:
            raise KeyError((profile_id, job_id))
        return dict(row)

    def list_profile_jobs(self, profile_id, status=None, run_id=None) -> list:
        clauses = ["profile_id = ?"]
        params = [str(profile_id)]
        if status:
            clauses.append(_STATUS_SET_CLAUSE)
            params.append(status)
        if run_id:
            clauses.append("(first_run_id = ? OR last_run_id = ?)")
            params.extend([str(run_id), str(run_id)])
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM profile_jobs WHERE {' AND '.join(clauses)} ORDER BY shown_at DESC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    # -- feedback ----------------------------------------------------------

    def create_feedback(self, profile_id, job_id, run_id, action, reason=None):
        if action not in FEEDBACK_ACTIONS:
            raise ValueError(f"未知反馈动作: {action}")
        if reason not in FEEDBACK_REASONS:
            raise ValueError(f"未知反馈原因: {reason}")
        fid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO feedback_events (id, profile_id, job_id, run_id, action, reason, revoked_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                (fid, str(profile_id), str(job_id), run_id, action, reason, ts),
            )
            # Update profile_job status to match feedback
            if action == "interested":
                conn.execute(
                    "UPDATE profile_jobs SET status = 'interested' WHERE profile_id = ? AND job_id = ?",
                    (str(profile_id), str(job_id)),
                )
        return self.get_feedback(fid)

    def get_feedback(self, feedback_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM feedback_events WHERE id = ?", (str(feedback_id),)).fetchone()
        if row is None:
            raise KeyError(feedback_id)
        return dict(row)

    def revoke_feedback(self, feedback_id):
        with self._connection() as conn:
            conn.execute(
                "UPDATE feedback_events SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (_now(), str(feedback_id)),
            )

    def list_feedback(self, profile_id, job_id=None) -> list:
        clauses = ["profile_id = ?"]
        params = [str(profile_id)]
        if job_id:
            clauses.append("job_id = ?")
            params.append(str(job_id))
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM feedback_events WHERE {' AND '.join(clauses)} ORDER BY created_at ASC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def count_effective_feedback(self, profile_id) -> int:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM feedback_events WHERE profile_id = ? AND revoked_at IS NULL",
                (str(profile_id),),
            ).fetchone()
        return int(row["c"])

    # -- preference versions ----------------------------------------------

    def save_preference_version(self, profile_id, source_feedback_count, preference_json):
        pid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO preference_versions (id, profile_id, source_feedback_count, preference_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (pid, str(profile_id), int(source_feedback_count), json.dumps(preference_json, ensure_ascii=False), ts),
            )
            # Persist the preference on the profile too
            conn.execute(
                "UPDATE candidate_profiles SET ai_preference_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(preference_json, ensure_ascii=False), ts, str(profile_id)),
            )
        return {"id": pid, "profile_id": str(profile_id), "source_feedback_count": int(source_feedback_count), "preference_json": preference_json, "created_at": ts}

    def get_latest_preference(self, profile_id):
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM preference_versions WHERE profile_id = ? ORDER BY created_at DESC LIMIT 1",
                (str(profile_id),),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["preference_json"] = json.loads(result["preference_json"] or "{}")
        return result

    # -- cleanup -----------------------------------------------------------

    def cleanup_expired_jobs(self, days=CLEANUP_EXPIRED_DAYS) -> int:
        """Remove normal results older than *days*. Preserves interested/applied."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
        with self._connection() as conn:
            # 单条 UPDATE + 子查询，消除原来逐行 UPDATE 的 N 次 DB 往返。
            # 命中 idx_jobs_expires_at 索引（partial: WHERE expires_at IS NOT NULL）。
            cursor = conn.execute(
                """UPDATE profile_jobs SET status = 'deleted'
                   WHERE status = 'new'
                     AND (profile_id, job_id) IN (
                       SELECT pj.profile_id, pj.job_id FROM profile_jobs pj
                       JOIN jobs j ON pj.job_id = j.id
                       WHERE pj.status = 'new'
                         AND j.expires_at IS NOT NULL
                         AND j.expires_at < ?
                     )""",
                (cutoff,),
            )
            return cursor.rowcount

    def preview_cleanup_expired_jobs(self, days=CLEANUP_EXPIRED_DAYS) -> list:
        """Preview which profile_jobs would be cleaned up, without modifying data.

        Returns a list of ``{profile_id, job_id}`` dicts.  The real cleanup
        is performed by :meth:`cleanup_expired_jobs`.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT pj.profile_id, pj.job_id FROM profile_jobs pj
                   JOIN jobs j ON pj.job_id = j.id
                   WHERE pj.status = 'new' AND j.expires_at IS NOT NULL AND j.expires_at < ?""",
                (cutoff,),
            ).fetchall()
        return [{"profile_id": row["profile_id"], "job_id": row["job_id"]} for row in rows]
