"""SQLite persistence for the AI job workbench.

Extends the original task/profile store with versioned migrations,
candidate profiles, resumes, AI settings, search runs, jobs, feedback
and preference versions.  Old tables (tasks, task_logs, profiles) are
preserved unchanged.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------

ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"succeeded", "failed", "interrupted", "partial"}
ALLOWED_TRANSITIONS = {
    "queued": {"running", "failed", "interrupted"},
    "running": {"succeeded", "failed", "interrupted", "partial"},
    "succeeded": set(),
    "failed": set(),
    "interrupted": set(),
    "partial": set(),
}

RUN_STATUSES = {"queued", "running", "succeeded", "partial", "failed", "interrupted"}
RUN_TRANSITIONS = {
    "queued": {"running", "failed", "interrupted"},
    "running": {"succeeded", "partial", "failed", "interrupted"},
    "succeeded": set(),
    "partial": set(),
    "failed": set(),
    "interrupted": set(),
}

QUERY_STATUSES = {"queued", "running", "succeeded", "failed", "interrupted"}
FEEDBACK_ACTIONS = {"interested", "not_interested"}
FEEDBACK_REASONS = {"role", "salary", "location", "company", None}
PROFILE_JOB_STATUSES = {"new", "interested", "applied", "deleted"}
AI_STATUS_VALUES = {"unconfigured", "testing", "ready", "failed"}
RESUME_FORMATS = {"txt", "pdf", "docx"}
MAX_DETAIL_BUDGET = 60


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return uuid.uuid4().hex[:16]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class TaskStore:
    def __init__(self, db_path):
        self.db_path = os.path.abspath(os.fspath(db_path))
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._initialize()
        self._migrate()

    # -- connection --------------------------------------------------------

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    # -- legacy initialization (tasks/task_logs/profiles) ------------------

    def _initialize(self):
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    output_path TEXT,
                    detail_output_path TEXT,
                    returncode INTEGER,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_logs (
                    task_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    line TEXT NOT NULL,
                    PRIMARY KEY (task_id, seq),
                    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS profiles (
                    name TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "UPDATE tasks SET status = 'interrupted', error = ?, updated_at = ? "
                "WHERE status IN ('queued', 'running')",
                ("服务重启，原任务已中断", _now()),
            )

    # -- migrations --------------------------------------------------------

    def _migrate(self):
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL,
                    description TEXT NOT NULL
                )
                """
            )
            row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
            current = int(row["v"] or 0)

        if current < 1:
            self._migration_001()
        if current < 2:
            self._migration_002()
        if current < 3:
            self._migration_003()
        # A process restart cannot resume an in-memory child process. Record
        # that fact instead of leaving a permanently "running" UI state.
        with self._connection() as conn:
            conn.execute(
                "UPDATE search_runs SET status = 'interrupted', error_code = 'restart', updated_at = ? "
                "WHERE status IN ('queued', 'running')",
                (_now(),),
            )
        # Always reconcile: copy old default profile if not yet in candidate_profiles
        self._copy_legacy_default_profile()

    def _migration_001(self):
        """First workbench migration: add all new tables."""
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidate_profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    confirmed_fields_json TEXT NOT NULL DEFAULT '{}',
                    ai_preference_json TEXT NOT NULL DEFAULT '{}',
                    resume_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS resumes (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    original_filename TEXT,
                    format TEXT NOT NULL,
                    extracted_text TEXT,
                    content_hash TEXT,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT,
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS ai_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    endpoint_url TEXT NOT NULL DEFAULT '',
                    credential_ref TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'unconfigured',
                    last_error_code TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS search_runs (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    profile_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    mode TEXT NOT NULL DEFAULT 'ai',
                    status TEXT NOT NULL DEFAULT 'queued',
                    total_detail_budget INTEGER NOT NULL DEFAULT 60,
                    discovered_count INTEGER NOT NULL DEFAULT 0,
                    completed_jd_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_code TEXT,
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS run_queries (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    frozen_query_json TEXT NOT NULL DEFAULT '{}',
                    list_output_path TEXT,
                    detail_output_path TEXT,
                    status TEXT NOT NULL DEFAULT 'queued',
                    detail_budget INTEGER NOT NULL DEFAULT 0,
                    counts_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES search_runs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    canonical_url TEXT NOT NULL UNIQUE,
                    source_url TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    company TEXT NOT NULL DEFAULT '',
                    salary TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    jd TEXT NOT NULL DEFAULT '',
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    expires_at TEXT,
                    FOREIGN KEY (id) REFERENCES jobs(id)
                );

                CREATE TABLE IF NOT EXISTS profile_jobs (
                    profile_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    first_run_id TEXT,
                    last_run_id TEXT,
                    ai_rank INTEGER,
                    shown_at TEXT,
                    status TEXT NOT NULL DEFAULT 'new',
                    note TEXT,
                    applied_at TEXT,
                    PRIMARY KEY (profile_id, job_id),
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS feedback_events (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    run_id TEXT,
                    action TEXT NOT NULL,
                    reason TEXT,
                    revoked_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE,
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS preference_versions (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    source_feedback_count INTEGER NOT NULL,
                    preference_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (profile_id) REFERENCES candidate_profiles(id) ON DELETE CASCADE
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) VALUES (1, ?, 'workbench base tables')",
                (_now(),),
            )

    def _migration_002(self):
        """Add removable, unconfirmed AI resume suggestions."""
        with self._connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(resumes)")}
            if "suggestions_json" not in columns:
                conn.execute("ALTER TABLE resumes ADD COLUMN suggestions_json TEXT NOT NULL DEFAULT '{}'")
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) VALUES (2, ?, 'resume suggestions')",
                (_now(),),
            )
            # Mark unfinished search runs interrupted (like tasks)
            conn.execute(
                "UPDATE search_runs SET status = 'interrupted', error_code = 'restart' "
                "WHERE status IN ('queued', 'running')"
            )

    def _migration_003(self):
        """Store resumable, cursor-addressable search events."""
        with self._connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS search_run_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, type TEXT NOT NULL, "
                "payload_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, "
                "FOREIGN KEY (run_id) REFERENCES search_runs(id) ON DELETE CASCADE)"
            )
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) VALUES (3, ?, 'search run events')",
                (_now(),),
            )

    def _copy_legacy_default_profile(self):
        """Copy old default profile to candidate_profiles if not already present."""
        with self._connection() as conn:
            old = conn.execute("SELECT value_json FROM profiles WHERE name = 'default'").fetchone()
            if old and not conn.execute("SELECT 1 FROM candidate_profiles WHERE name = 'default'").fetchone():
                conn.execute(
                    "INSERT INTO candidate_profiles (id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                    "VALUES (?, 'default', ?, '{}', ?, ?)",
                    (_uuid(), old["value_json"], _now(), _now()),
                )

    def schema_version(self) -> int:
        with self._connection() as conn:
            row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return int(row["v"] or 0)

    # -- legacy task API (unchanged) --------------------------------------

    def create_task(self, task_id, kind, params, output_path=None, detail_output_path=None):
        timestamp = _now()
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO tasks
                   (id, kind, status, params_json, output_path, detail_output_path,
                    returncode, error, created_at, updated_at)
                   VALUES (?, ?, 'queued', ?, ?, ?, NULL, NULL, ?, ?)""",
                (
                    str(task_id), str(kind), json.dumps(params or {}, ensure_ascii=False),
                    output_path, detail_output_path, timestamp, timestamp,
                ),
            )
        return self.get_task(task_id)

    def update_task(self, task_id, status, returncode=None, error=None):
        current = self.get_task(task_id)
        if status not in ALLOWED_TRANSITIONS:
            raise ValueError(f"未知任务状态: {status}")
        if status not in ALLOWED_TRANSITIONS[current["status"]]:
            raise ValueError(f"任务不能从 {current['status']} 转换到 {status}")
        with self._connection() as connection:
            connection.execute(
                "UPDATE tasks SET status = ?, returncode = ?, error = ?, updated_at = ? WHERE id = ?",
                (status, returncode, error, _now(), str(task_id)),
            )
        return self.get_task(task_id)

    def append_log(self, task_id, line):
        self.get_task(task_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM task_logs WHERE task_id = ?",
                (str(task_id),),
            ).fetchone()
            seq = int(row["next_seq"])
            connection.execute(
                "INSERT INTO task_logs (task_id, seq, created_at, line) VALUES (?, ?, ?, ?)",
                (str(task_id), seq, _now(), str(line)),
            )
        return seq

    def get_logs(self, task_id, after=0):
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM tasks WHERE id = ?", (str(task_id),)
            ).fetchone()
            if exists is None:
                raise KeyError(str(task_id))
            rows = connection.execute(
                "SELECT seq, created_at, line FROM task_logs WHERE task_id = ? AND seq > ? ORDER BY seq",
                (str(task_id), int(after or 0)),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_task(self, task_id, include_logs=False):
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (str(task_id),)).fetchone()
        if row is None:
            raise KeyError(str(task_id))
        task = dict(row)
        task["params"] = json.loads(task.pop("params_json") or "{}")
        if include_logs:
            task["logs"] = self.get_logs(task_id)
        return task

    def list_tasks(self, limit=30):
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)
            ).fetchall()
        tasks = []
        for row in rows:
            item = dict(row)
            item["params"] = json.loads(item.pop("params_json") or "{}")
            tasks.append(item)
        return tasks

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
        self.get_resume(resume_id)
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
        resume = self.get_resume(resume_id)
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

    def save_ai_settings(self, endpoint_url, credential_ref, status="unconfigured", last_error_code=None):
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO ai_settings (id, endpoint_url, credential_ref, status, last_error_code, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET endpoint_url = excluded.endpoint_url,
                   credential_ref = excluded.credential_ref, status = excluded.status,
                   last_error_code = excluded.last_error_code, updated_at = excluded.updated_at""",
                (endpoint_url, credential_ref, status, last_error_code, ts),
            )
        return self.get_ai_settings()

    def get_ai_settings(self) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM ai_settings WHERE id = 1").fetchone()
        if row is None:
            return {"endpoint_url": "", "status": "unconfigured", "last_error_code": None, "updated_at": None, "is_configured": False}
        result = dict(row)
        result["is_configured"] = bool(result["endpoint_url"] and result["credential_ref"])
        # Never expose credential_ref outside the store — callers only see is_configured
        result.pop("credential_ref", None)
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

    # -- search runs -------------------------------------------------------

    def create_search_run(self, profile_id, profile_snapshot, mode, total_detail_budget=MAX_DETAIL_BUDGET):
        rid = _uuid()
        ts = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO search_runs (id, profile_id, profile_snapshot_json, mode, status, total_detail_budget, discovered_count, completed_jd_count, created_at, updated_at, error_code) "
                "VALUES (?, ?, ?, ?, 'queued', ?, 0, 0, ?, ?, NULL)",
                (rid, str(profile_id), json.dumps(profile_snapshot, ensure_ascii=False), mode, int(total_detail_budget), ts, ts),
            )
        return self.get_search_run(rid)

    def get_search_run(self, run_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM search_runs WHERE id = ?", (str(run_id),)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._run_row(row)

    def update_search_run(self, run_id, status=None, discovered_count=None, completed_jd_count=None, error_code=None):
        current = self.get_search_run(run_id)
        if status and status not in RUN_STATUSES:
            raise ValueError(f"未知运行状态: {status}")
        if status and status not in RUN_TRANSITIONS[current["status"]]:
            raise ValueError(f"运行不能从 {current['status']} 转换到 {status}")
        sets = []
        params = []
        if status:
            sets.append("status = ?")
            params.append(status)
        if discovered_count is not None:
            sets.append("discovered_count = ?")
            params.append(int(discovered_count))
        if completed_jd_count is not None:
            sets.append("completed_jd_count = ?")
            params.append(int(completed_jd_count))
        if error_code is not None:
            sets.append("error_code = ?")
            params.append(error_code)
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(str(run_id))
        with self._connection() as conn:
            conn.execute(f"UPDATE search_runs SET {', '.join(sets)} WHERE id = ?", params)
        return self.get_search_run(run_id)

    def list_search_runs(self, profile_id=None, limit=30):
        with self._connection() as conn:
            if profile_id:
                rows = conn.execute(
                    "SELECT * FROM search_runs WHERE profile_id = ? ORDER BY created_at DESC LIMIT ?",
                    (str(profile_id), max(1, int(limit))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM search_runs ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)
                ).fetchall()
        return [self._run_row(row) for row in rows]

    def _run_row(self, row) -> dict:
        return {
            "id": row["id"],
            "profile_id": row["profile_id"],
            "profile_snapshot": json.loads(row["profile_snapshot_json"] or "{}"),
            "mode": row["mode"],
            "status": row["status"],
            "total_detail_budget": row["total_detail_budget"],
            "discovered_count": row["discovered_count"],
            "completed_jd_count": row["completed_jd_count"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "error_code": row["error_code"],
        }

    def append_search_event(self, run_id, event_type, payload=None):
        self.get_search_run(run_id)
        with self._connection() as conn:
            cursor = conn.execute(
                "INSERT INTO search_run_events (run_id, type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (str(run_id), str(event_type), json.dumps(payload or {}, ensure_ascii=False), _now()),
            )
            event_id = cursor.lastrowid
        return {"id": event_id, "run_id": str(run_id), "type": str(event_type), "payload": payload or {}}

    def list_search_events(self, run_id, after=0):
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, run_id, type, payload_json, created_at FROM search_run_events "
                "WHERE run_id = ? AND id > ? ORDER BY id ASC",
                (str(run_id), int(after or 0)),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"] or "{}"), "payload_json": None} for row in rows]

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
            sets.append("status = ?")
            params.append(status)
        if counts is not None:
            sets.append("counts_json = ?")
            params.append(json.dumps(counts, ensure_ascii=False))
        if error_code is not None:
            sets.append("error_code = ?")
            params.append(error_code)
        sets.append("updated_at = ?")
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
        from datetime import timedelta

        ts = _now()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        jid = _uuid()
        with self._connection() as conn:
            existing = conn.execute("SELECT id FROM jobs WHERE canonical_url = ?", (canonical_url,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE jobs SET source_url = ?, title = ?, company = ?, salary = ?, location = ?, jd = ?, last_seen_at = ? WHERE id = ?",
                    (source_url, title, company, salary, location, jd, ts, existing["id"]),
                )
                jid = existing["id"]
            else:
                conn.execute(
                    "INSERT INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (jid, canonical_url, source_url, title, company, salary, location, jd, ts, ts, expires_at),
                )
        return self.get_job(jid)

    def get_job(self, job_id) -> dict:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (str(job_id),)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return dict(row)

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
            existing = conn.execute(
                "SELECT * FROM profile_jobs WHERE profile_id = ? AND job_id = ?",
                (str(profile_id), str(job_id)),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE profile_jobs SET last_run_id = ?, ai_rank = ?, shown_at = COALESCE(shown_at, ?) WHERE profile_id = ? AND job_id = ?",
                    (last_run_id, ai_rank, ts, str(profile_id), str(job_id)),
                )
            else:
                conn.execute(
                    "INSERT INTO profile_jobs (profile_id, job_id, first_run_id, last_run_id, ai_rank, shown_at, status, note, applied_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                    (str(profile_id), str(job_id), first_run_id, last_run_id, ai_rank, ts, status),
                )
        return self.get_profile_job(profile_id, job_id)

    def update_profile_job(self, profile_id, job_id, status=None, note=None, applied_at=None):
        sets = []
        params = []
        if status:
            if status not in PROFILE_JOB_STATUSES:
                raise ValueError(f"未知岗位状态: {status}")
            sets.append("status = ?")
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
            clauses.append("status = ?")
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

    def cleanup_expired_jobs(self, days=30) -> int:
        """Remove normal results older than *days*. Preserves interested/applied."""
        from datetime import datetime, timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
        with self._connection() as conn:
            # Find profile_jobs that are 'new' and whose job expired before cutoff
            rows = conn.execute(
                """SELECT pj.profile_id, pj.job_id FROM profile_jobs pj
                   JOIN jobs j ON pj.job_id = j.id
                   WHERE pj.status = 'new' AND j.expires_at IS NOT NULL AND j.expires_at < ?""",
                (cutoff,),
            ).fetchall()
            count = 0
            for row in rows:
                conn.execute(
                    "UPDATE profile_jobs SET status = 'deleted' WHERE profile_id = ? AND job_id = ?",
                    (row["profile_id"], row["job_id"]),
                )
                count += 1
            return count

    def preview_cleanup_expired_jobs(self, days=30) -> list:
        """Preview which profile_jobs would be cleaned up, without modifying data.

        Returns a list of ``{profile_id, job_id}`` dicts.  The real cleanup
        is performed by :meth:`cleanup_expired_jobs`.
        """
        from datetime import datetime, timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT pj.profile_id, pj.job_id FROM profile_jobs pj
                   JOIN jobs j ON pj.job_id = j.id
                   WHERE pj.status = 'new' AND j.expires_at IS NOT NULL AND j.expires_at < ?""",
                (cutoff,),
            ).fetchall()
        return [{"profile_id": row["profile_id"], "job_id": row["job_id"]} for row in rows]
