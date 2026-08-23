"""候选人档案域（021 B2 拆分自 webui/store.py）：profiles CRUD、简历与
AI 设置、筛选意向标记。

以 mixin 形式由 webui/store.py 的 TaskStore 组装；实例状态（db_path、
_connection 等）来自 TaskStore 核心。模块不得 import webui.store。
"""

from __future__ import annotations

import json

from webui.store_helpers import (
    _now,
    _uuid,
)
from webui.store_constants import (
    AI_STATUS_VALUES,
    RESUME_FORMATS,
)


class StoreProfilesMixin:
    def save_profile(self, profile, name="default"):
        timestamp = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO profiles (name, value_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET value_json = excluded.value_json,
                   updated_at = excluded.updated_at""",
                (name, json.dumps(profile or {}, ensure_ascii=False), timestamp),
            )

    def load_profile(self, name="default"):
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value_json FROM profiles WHERE name = ?", (name,)
            ).fetchone()
        return json.loads(row["value_json"]) if row else {}

    # -- candidate profiles ------------------------------------------------

    def create_profile(self, name, confirmed_fields=None, resume_id=None, copy_from=None):
        name = str(name or "").strip()
        if not name or len(name) > 80:
            raise ValueError("画像名称长度必须为 1 至 80 个字符")
        confirmed = confirmed_fields or {}
        if copy_from:
            source = self.get_profile(copy_from)
            # Only copy manual (confirmed) fields, never AI preference
            confirmed = {**source["confirmed_fields"], **confirmed}
        pid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles (id, name, confirmed_fields_json, ai_preference_json, resume_id, created_at, updated_at) "
                "VALUES (?, ?, ?, '{}', ?, ?, ?)",
                (pid, name, json.dumps(confirmed, ensure_ascii=False), resume_id, ts, ts),
            )
        return self.get_profile(pid)

    def get_profile(self, profile_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM candidate_profiles WHERE id = ?", (str(profile_id),)).fetchone()
        if row is None:
            raise KeyError(profile_id)
        return self._profile_row(row)

    def list_candidate_profiles(self) -> list:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM candidate_profiles ORDER BY created_at ASC").fetchall()
        return [self._profile_row(row) for row in rows]

    def update_profile(self, profile_id, name=None, confirmed_fields=None, ai_preference=None, resume_id=None):
        current = self.get_profile(profile_id)
        ts = _now()
        new_name = name.strip() if name else current["name"]
        if not new_name or len(new_name) > 80:
            raise ValueError("画像名称长度必须为 1 至 80 个字符")
        fields = confirmed_fields if confirmed_fields is not None else current["confirmed_fields"]
        pref = ai_preference if ai_preference is not None else current["ai_preference"]
        rid = resume_id if resume_id is not None else current["resume_id"]
        with self._connection() as conn:
            conn.execute(
                "UPDATE candidate_profiles SET name = ?, confirmed_fields_json = ?, ai_preference_json = ?, resume_id = ?, updated_at = ? WHERE id = ?",
                (new_name, json.dumps(fields, ensure_ascii=False), json.dumps(pref, ensure_ascii=False), rid, ts, str(profile_id)),
            )
        return self.get_profile(profile_id)

    def delete_profile(self, profile_id):
        """删除画像及其关联数据。

        - 岗位状态事件（profile_job_events）与命令回执
          （profile_job_command_receipts）两张子表对 profile_jobs 是
          ON DELETE RESTRICT，删主表前必须显式清理（回执另有 event_id
          外键，先删回执再删事件）。
        - 其余关联（profile_jobs / search_runs / resumes / screening_*）
          由外键 ON DELETE CASCADE 随主表行清理。
        """
        pid = str(profile_id)
        # 校验存在，不存在抛 KeyError 与 get_profile 行为一致
        self.get_profile(pid)
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id FROM resumes WHERE profile_id = ? AND deleted_at IS NULL",
                (pid,),
            ).fetchall()
        resume_ids = [r["id"] for r in rows]
        # 删除简历文件需要 resume_service，但 store 不依赖 resume_service；
        # 这里只清数据库层，文件删除由 app 层调用前清理（见 app.py delete_profile 路由）。
        with self._connection() as conn:
            conn.execute(
                "DELETE FROM profile_job_command_receipts WHERE profile_id = ?",
                (pid,),
            )
            conn.execute(
                "DELETE FROM profile_job_events WHERE profile_id = ?",
                (pid,),
            )
            conn.execute("DELETE FROM candidate_profiles WHERE id = ?", (pid,))
        return {"deleted": True, "resume_ids": resume_ids}

    def _profile_row(self, row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "confirmed_fields": json.loads(row["confirmed_fields_json"] or "{}"),
            "ai_preference": json.loads(row["ai_preference_json"] or "{}"),
            "resume_id": row["resume_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # -- resumes -----------------------------------------------------------

    def save_resume(self, profile_id, storage_path, fmt, extracted_text, content_hash, original_filename=None):
        if fmt not in RESUME_FORMATS:
            raise ValueError(f"不支持的简历格式: {fmt}")
        rid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO resumes (id, profile_id, storage_path, original_filename, format, extracted_text, content_hash, created_at, deleted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (rid, str(profile_id), storage_path, original_filename, fmt, extracted_text, content_hash, ts),
            )
            conn.execute(
                "UPDATE candidate_profiles SET resume_id = ?, updated_at = ? WHERE id = ?",
                (rid, ts, str(profile_id)),
            )
        return self.get_resume(rid)

    def get_resume(self, resume_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM resumes WHERE id = ?", (str(resume_id),)).fetchone()
        if row is None:
            raise KeyError(resume_id)
        result = dict(row)
        result["suggestions"] = json.loads(result.pop("suggestions_json", "{}") or "{}")
        return result

    def save_resume_suggestions(self, resume_id, suggestions):
        _ = self.get_resume(resume_id)
        with self._connection() as conn:
            conn.execute(
                "UPDATE resumes SET suggestions_json = ? WHERE id = ? AND deleted_at IS NULL",
                (json.dumps(suggestions or {}, ensure_ascii=False), str(resume_id)),
            )
        return self.get_resume(resume_id)

    def delete_resume(self, resume_id):
        """Wipe resume text, hash, filename, storage_path and break the profile link.

        File removal is the responsibility of ``resume_service.delete_resume``,
        which knows the resume directory.  Here we only wipe database fields so
        the store layer never depends on the filesystem layout.
        """
        self.get_resume(resume_id)
        with self._connection() as conn:
            # Wipe all sensitive fields then mark deleted_at
            conn.execute(
                "UPDATE resumes SET extracted_text = NULL, content_hash = NULL, original_filename = NULL, suggestions_json = '{}', storage_path = '', deleted_at = ? WHERE id = ?",
                (_now(), str(resume_id)),
            )
            # Break the profile->resume link so unconfirmed AI suggestions
            # derived from this resume no longer appear active.
            conn.execute(
                "UPDATE candidate_profiles SET resume_id = NULL, updated_at = ? "
                "WHERE resume_id = ?",
                (_now(), str(resume_id)),
            )
        return True

    def list_resumes(self, profile_id) -> list:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, profile_id, format, created_at, deleted_at FROM resumes WHERE profile_id = ? ORDER BY created_at DESC",
                (str(profile_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- AI settings -------------------------------------------------------

    def save_ai_settings(self, endpoint_url, credential_ref, status="unconfigured", last_error_code=None, model=""):
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO ai_settings (id, endpoint_url, credential_ref, status, last_error_code, model, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET endpoint_url = excluded.endpoint_url,
                   credential_ref = excluded.credential_ref, status = excluded.status,
                   last_error_code = excluded.last_error_code, model = excluded.model,
                   updated_at = excluded.updated_at""",
                (endpoint_url, credential_ref, status, last_error_code, str(model or ""), ts),
            )
        return self.get_ai_settings()

    def get_ai_settings(self) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM ai_settings WHERE id = 1").fetchone()
        if row is None:
            return {"endpoint_url": "", "model": "", "status": "unconfigured", "last_error_code": None, "updated_at": None, "is_configured": False}
        result = dict(row)
        result["is_configured"] = bool(result["endpoint_url"] and result["credential_ref"])
        # Never expose credential_ref outside the store — callers only see is_configured
        result.pop("credential_ref", None)
        if "model" not in result:
            result["model"] = ""
        return result

    def get_credential_ref(self) -> str:
        with self._connection() as conn:
            row = conn.execute("SELECT credential_ref FROM ai_settings WHERE id = 1").fetchone()
        return row["credential_ref"] if row else ""

    def update_ai_status(self, status, last_error_code=None):
        if status not in AI_STATUS_VALUES:
            raise ValueError(f"未知 AI 状态: {status}")
        with self._connection() as conn:
            conn.execute(
                "UPDATE ai_settings SET status = ?, last_error_code = ?, updated_at = ? WHERE id = 1",
                (status, last_error_code, _now()),
            )
        return self.get_ai_settings()

    # -- screening feedback persistence (interested / rejected zones) -----

    def mark_screening_interest(self, profile_id, job_id, run_id=None) -> dict:
        """标记岗位为感兴趣：profile_jobs.status='interested' + feedback_events。

        复用 001 的 create_feedback（内部已更新 status='interested'）。
        若 profile_job 记录不存在则先建立。感兴趣进持久感兴趣区，跨简历保留。
        """
        # 确保 profile_job 记录存在（status 默认 new）
        try:
            self.get_profile_job(profile_id, job_id)
        except KeyError:
            self.link_profile_job(profile_id, job_id, run_id, run_id, status="new")
        # create_feedback 内部对 action='interested' 会更新 status='interested'
        return self.create_feedback(profile_id, job_id, run_id, "interested")

    def mark_screening_reject(self, profile_id, job_id, run_id=None) -> dict:
        """标记岗位为不感兴趣：profile_jobs.status='deleted' + feedback_events。

        复用 001 的 create_feedback（写 not_interested 反馈），并显式设
        status='deleted' 使其进入持久垃圾桶区。跨简历保留。
        """
        # 确保 profile_job 记录存在
        try:
            self.get_profile_job(profile_id, job_id)
        except KeyError:
            self.link_profile_job(profile_id, job_id, run_id, run_id, status="new")
        # create_feedback 对 not_interested 不自动更新 status，需显式设
        feedback = self.create_feedback(profile_id, job_id, run_id, "not_interested")
        self.update_profile_job(profile_id, job_id, status="deleted")
        return feedback

    def cancel_screening_interest(self, profile_id, job_id):
        """撤销感兴趣标记：把 profile_jobs.status 从 interested 回退到默认 'new'。

        幂等——若当前不是 interested（或记录不存在）也不报错。schema 中
        status 列为 NOT NULL DEFAULT 'new'，故回退到 'new' 而非 NULL。
        仅清状态，不撤销历史 feedback_events。
        """
        with self._connection() as conn:
            conn.execute(
                "UPDATE profile_jobs SET status = 'new' "
                "WHERE profile_id = ? AND job_id = ? AND status = 'interested'",
                (str(profile_id), str(job_id)),
            )
        try:
            return self.get_profile_job(profile_id, job_id)
        except KeyError:
            return None

    def cancel_screening_reject(self, profile_id, job_id):
        """撤销不感兴趣标记：把 profile_jobs.status 从 deleted 回退到默认 'new'。

        幂等——若当前不是 deleted（或记录不存在）也不报错。
        """
        # 从反馈历史恢复删除前的用户意图：已投递优先，其次最近一次未撤销的
        # “感兴趣”，都没有则回退默认 'new'。
        previous_status = "new"
        try:
            job = self.get_profile_job(profile_id, job_id)
            if job.get("applied_at"):
                previous_status = "applied"
            else:
                events = self.list_feedback(profile_id, job_id)
                last_reject_idx = None
                for idx, event in enumerate(events):
                    if event["action"] == "not_interested" and not event.get("revoked_at"):
                        last_reject_idx = idx
                if last_reject_idx is not None:
                    for event in reversed(events[:last_reject_idx]):
                        if event["action"] == "interested" and not event.get("revoked_at"):
                            previous_status = "interested"
                            break
        except KeyError:
            previous_status = "new"
        with self._connection() as conn:
            conn.execute(
                "UPDATE profile_jobs SET status = ? "
                "WHERE profile_id = ? AND job_id = ? AND status = 'deleted'",
                (previous_status, str(profile_id), str(job_id)),
            )
        try:
            return self.get_profile_job(profile_id, job_id)
        except KeyError:
            return None

    def list_screening_interested(self, profile_id) -> list:
        """返回持久感兴趣区的 profile_jobs 列表（status='interested'）。

        按最近反馈时间降序（shown_at DESC），便于长期回看。
        """
        return self.list_profile_jobs(profile_id, status="interested")

    def list_all_interested(self) -> list:
        """返回所有 profile 的 interested 岗位列表，带 profile_id 用于取消收藏。"""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM profile_jobs WHERE status = 'interested' ORDER BY shown_at DESC",
            ).fetchall()
        return [dict(row) for row in rows]

    # -- search runs -------------------------------------------------------
