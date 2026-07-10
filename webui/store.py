"""SQLite persistence for background tasks, logs, and the local job profile."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"succeeded", "failed", "interrupted"}
ALLOWED_TRANSITIONS = {
    "queued": {"running", "failed", "interrupted"},
    "running": {"succeeded", "failed", "interrupted"},
    "succeeded": set(),
    "failed": set(),
    "interrupted": set(),
}


def _now():
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    def __init__(self, db_path):
        self.db_path = os.path.abspath(os.fspath(db_path))
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._initialize()

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
