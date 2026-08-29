"""岗位 upsert 与快照域（021 B2 拆分自 webui/store.py）：T112 双索引冲突
算法 upsert、结果快照、收藏岗位 upsert。

以 mixin 形式由 webui/store.py 的 TaskStore 组装；实例状态（db_path、
_connection 等）来自 TaskStore 核心。模块不得 import webui.store。
"""

from __future__ import annotations

import json

from webui.store_helpers import (
    _now,
    _uuid,
)


class StoreJobsMixin:

    # -- T112: Job 双索引冲突算法 upsert ---------------------------------

    def upsert_job(
        self,
        *,
        platform: str,
        platform_job_id: str | None,
        canonical_url: str,
        title: str = "",
        company: str = "",
        salary: str = "",
        location: str = "",
        jd: str = "",
        experience: str = "",
        degree: str = "",
        extra: dict | None = None,
    ) -> dict:
        """Job 双索引冲突算法（data-model.md "Job upsert 冲突算法"）。

        返回 {"ok": bool, "job_id": str | None, "error_code": str | None}。
        所有 8 个分支在同一事务中完成。
        """
        from webui.platforms import normalize_job_url
        normalized_url = normalize_job_url(platform, canonical_url)
        if not normalized_url:
            return {"ok": False, "job_id": None, "error_code": "platform_url_mismatch"}
        with self._connection() as conn:
            return self.upsert_job_with_connection(
                conn, platform=platform, platform_job_id=platform_job_id,
                canonical_url=normalized_url, title=title, company=company,
                salary=salary, location=location, jd=jd, experience=experience,
                degree=degree, extra=extra, _validated_url=True,
            )

    def upsert_job_with_connection(
        self, conn, *, platform: str, platform_job_id: str | None,
        canonical_url: str, title: str = "", company: str = "",
        salary: str = "", location: str = "", jd: str = "",
        experience: str = "", degree: str = "", extra: dict | None = None,
        _validated_url: bool = False,
    ) -> dict:
        """Run the dual-index upsert on a caller-owned transaction.

        Lifecycle and pipeline actions use this method so job identity, the
        profile link and lifecycle records can commit or roll back together.
        """
        from webui.platforms import normalize_job_url
        normalized_url = canonical_url if _validated_url else normalize_job_url(platform, canonical_url)
        if not normalized_url:
            return {"ok": False, "job_id": None, "error_code": "platform_url_mismatch"}

        platform_job_id = None if platform_job_id in (None, "") else str(platform_job_id)
        extra_json = json.dumps(extra or {}, ensure_ascii=False, sort_keys=True)
        now = _now()
        by_pid = None
        if platform_job_id is not None:
            row = conn.execute(
                "SELECT id, canonical_url FROM jobs WHERE platform=? AND platform_job_id=?",
                (str(platform), platform_job_id),
            ).fetchone()
            if row is not None:
                by_pid = {"id": row["id"], "canonical_url": row["canonical_url"]}

        by_url = conn.execute(
            "SELECT id, platform, platform_job_id FROM jobs WHERE canonical_url=?",
            (normalized_url,),
        ).fetchone()
        by_url_dict = None if by_url is None else {
            "id": by_url["id"], "platform": by_url["platform"],
            "platform_job_id": by_url["platform_job_id"],
        }
        if by_url_dict and by_url_dict["platform"] != platform:
            return {"ok": False, "job_id": None, "error_code": "job_identity_conflict"}

        def insert_job(job_id):
            conn.execute(
                "INSERT INTO jobs (id, canonical_url, source_url, title, company, salary, "
                "location, jd, first_seen_at, last_seen_at, platform, platform_job_id, "
                "experience, degree, extra_json) VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, normalized_url, title, company, salary, location, jd, now, now,
                 platform, platform_job_id, experience, degree, extra_json),
            )

        def update_job(job_id, *, update_url=False, update_platform_id=False):
            if update_url:
                conn.execute(
                    "UPDATE jobs SET canonical_url=?, title=?, company=?, salary=?, location=?, "
                    "jd=?, experience=?, degree=?, extra_json=?, last_seen_at=? WHERE id=?",
                    (normalized_url, title, company, salary, location, jd, experience,
                     degree, extra_json, now, job_id),
                )
            elif update_platform_id:
                conn.execute(
                    "UPDATE jobs SET platform_job_id=COALESCE(?, platform_job_id), title=?, company=?, salary=?, location=?, "
                    "jd=?, experience=?, degree=?, extra_json=?, last_seen_at=? WHERE id=?",
                    (platform_job_id, title, company, salary, location, jd, experience,
                     degree, extra_json, now, job_id),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET title=?, company=?, salary=?, location=?, jd=?, "
                    "experience=?, degree=?, extra_json=?, last_seen_at=? WHERE id=?",
                    (title, company, salary, location, jd, experience, degree, extra_json,
                     now, job_id),
                )

        if by_pid is None and by_url_dict is None:
            new_id = _uuid()
            insert_job(new_id)
            return {"ok": True, "job_id": new_id, "error_code": None}
        if by_pid is not None and by_url_dict is None:
            other = conn.execute(
                "SELECT id FROM jobs WHERE canonical_url=? AND id != ?",
                (normalized_url, by_pid["id"]),
            ).fetchone()
            if other is not None:
                return {"ok": False, "job_id": None, "error_code": "job_identity_conflict"}
            update_job(by_pid["id"], update_url=True)
            return {"ok": True, "job_id": by_pid["id"], "error_code": None}
        if by_pid is None and by_url_dict is not None:
            existing_pid = by_url_dict["platform_job_id"]
            if existing_pid is not None and platform_job_id is not None and existing_pid != platform_job_id:
                return {"ok": False, "job_id": None, "error_code": "job_identity_conflict"}
            update_job(by_url_dict["id"], update_platform_id=True)
            return {"ok": True, "job_id": by_url_dict["id"], "error_code": None}
        if by_pid["id"] != by_url_dict["id"]:
            return {"ok": False, "job_id": None, "error_code": "job_identity_conflict"}
        update_job(by_pid["id"])
        return {"ok": True, "job_id": by_pid["id"], "error_code": None}

    # -- T113: 结果快照读写 API -------------------------------------------

    def update_job_extra(self, platform: str, platform_job_id: str,
                         patch: dict) -> bool:
        """合并 patch 进既有岗位的 extra_json（028 B081 原语）。

        按 (platform, platform_job_id) 定位；读现有 extra_json → dict 合并 →
        原子写回。行不存在或 patch 为空返回 False，不抛错（调用方仅记日志）。
        保留 extra 内既有键，仅覆盖 patch 提供的键。
        """
        if not isinstance(patch, dict) or not patch:
            return False
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id, extra_json FROM jobs WHERE platform = ? AND platform_job_id = ?",
                (platform, platform_job_id),
            ).fetchone()
            if row is None:
                return False
            try:
                current = json.loads(row["extra_json"]) if row["extra_json"] else {}
            except (TypeError, ValueError):
                current = {}
            if not isinstance(current, dict):
                current = {}
            current.update(patch)
            conn.execute(
                "UPDATE jobs SET extra_json = ? WHERE id = ?",
                (json.dumps(current, ensure_ascii=False), row["id"]),
            )
            return True

    def save_result_snapshot(
        self,
        *,
        run_id: str,
        platform: str,
        platform_job_id: str,
        job_id: str | None = None,
        verdict: str,
        experience: str = "",
        degree: str = "",
        extra: dict | None = None,
    ) -> str:
        """T113: 保存结果快照，同时记录 platform、platform_job_id、可空内部 job_id 和完整岗位字段。

        返回结果行 id。如果 (run_id, platform_job_id) 已存在则更新。
        """
        extra_json = json.dumps(extra or {}, ensure_ascii=False, sort_keys=True)
        ts = _now()
        result_id = _uuid()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "INSERT INTO screening_results "
                "(id, run_id, job_id, verdict, created_at, platform, platform_job_id, "
                " experience, degree, extra_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, platform_job_id) DO UPDATE SET "
                " job_id = excluded.job_id, verdict = excluded.verdict, "
                " experience = excluded.experience, degree = excluded.degree, "
                " extra_json = excluded.extra_json",
                (result_id, str(run_id), job_id, str(verdict), ts,
                 str(platform), str(platform_job_id),
                 experience, degree, extra_json),
            )
        return result_id

    # -- T114: source attempt 追加及汇总 API ------------------------------

    def append_source_attempt(
        self,
        *,
        run_id: str,
        platform: str,
        combo_key: str,
        attempt_no: int,
        input_hash: str | None = None,
        outcome_kind: str,
        job_count: int = 0,
        empty_evidence: dict | None = None,
        error_code: str | None = None,
        error_reason: str | None = None,
    ) -> str:
        """T114: 追加一条 source attempt 记录。返回记录 id。

        禁止从零岗位反推 empty：outcome_kind='empty' 时 empty_evidence 必填。
        """
        if outcome_kind == "empty" and not empty_evidence:
            raise ValueError("outcome_kind='empty' 时 empty_evidence 必填")
        evidence_json = json.dumps(empty_evidence or {}, ensure_ascii=False, sort_keys=True) if empty_evidence else None
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            cur = conn.execute(
                "INSERT INTO screening_source_attempts "
                "(run_id, platform, combo_key, attempt_no, input_hash, "
                " outcome_kind, job_count, empty_evidence_json, error_code, error_reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(run_id), str(platform), str(combo_key), int(attempt_no),
                 input_hash, str(outcome_kind), int(job_count), evidence_json,
                 error_code, error_reason, ts),
            )
            attempt_id = cur.lastrowid
        return attempt_id

    def get_latest_source_attempt(self, run_id: str, combo_key: str) -> dict | None:
        """T114: 按 run/combo 获取最新 attempt。返回字典或 None。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM screening_source_attempts "
                "WHERE run_id=? AND combo_key=? ORDER BY attempt_no DESC LIMIT 1",
                (str(run_id), str(combo_key)),
            ).fetchone()
        if row is None:
            return None
        return self._source_attempt_row(row)

    def list_latest_source_attempts(self, run_id: str) -> list[dict]:
        """T405: 按 run 列出所有 combo 的最新 attempt。

        返回安全投影列表（不含敏感字段），每个 combo 一条。
        刷新/重启后从此方法汇总 source outcomes，不从岗位数为零反推 empty。
        """
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT s.* FROM screening_source_attempts s "
                "INNER JOIN ("
                "  SELECT combo_key, MAX(attempt_no) AS max_no "
                "  FROM screening_source_attempts WHERE run_id=? "
                "  GROUP BY combo_key"
                ") m ON s.combo_key=m.combo_key AND s.attempt_no=m.max_no "
                "WHERE s.run_id=? "
                "ORDER BY s.combo_key",
                (str(run_id), str(run_id)),
            ).fetchall()
        return [self._source_attempt_row(row) for row in rows]

    @staticmethod
    def _source_attempt_row(row) -> dict:
        """安全投影：返回 source attempt 的安全字段。"""
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "platform": row["platform"],
            "combo_key": row["combo_key"],
            "attempt_no": int(row["attempt_no"]),
            "input_hash": row["input_hash"],
            "outcome_kind": row["outcome_kind"],
            "job_count": int(row["job_count"]),
            "empty_evidence": json.loads(row["empty_evidence_json"] or "{}") if row["empty_evidence_json"] else None,
            "error_code": row["error_code"],
            "error_reason": row["error_reason"],
            "created_at": row["created_at"],
        }

    # -- T115: 筛选快照、task digest、interruption kind 持久化 ------------

    def save_filter_snapshot(
        self,
        run_id: str,
        *,
        platform: str,
        filter_schema_version: int | None = None,
        filter_snapshot: dict | None = None,
        task_input_digest: str | None = None,
    ) -> None:
        """T115: 持久化筛选快照、schema version 和 task input digest。"""
        snapshot_json = json.dumps(filter_snapshot or {}, ensure_ascii=False, sort_keys=True)
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "UPDATE screening_runs SET platform=?, filter_schema_version=?, "
                "filter_snapshot_json=?, task_input_digest=?, updated_at=? WHERE id=?",
                (str(platform), filter_schema_version, snapshot_json,
                 task_input_digest, ts, str(run_id)),
            )

    def save_interruption_kind(self, run_id: str, interruption_kind: str) -> None:
        """T115: 持久化 interruption kind（仅 status=interrupted 时使用）。"""
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "UPDATE screening_runs SET interruption_kind=?, updated_at=? WHERE id=?",
                (str(interruption_kind), ts, str(run_id)),
            )

    def get_run_checkpoint_identity(self, run_id: str) -> dict | None:
        """T115: 读取 run 的 checkpoint 身份一致性信息。"""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT platform, filter_schema_version, filter_snapshot_json, "
                "task_input_digest, interruption_kind FROM screening_runs WHERE id=?",
                (str(run_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "platform": row["platform"],
            "filter_schema_version": row["filter_schema_version"],
            "filter_snapshot": json.loads(row["filter_snapshot_json"] or "{}") if row["filter_snapshot_json"] else {},
            "task_input_digest": row["task_input_digest"],
            "interruption_kind": row["interruption_kind"],
        }

    def save_checkpoint_identity(
        self,
        run_id: str,
        *,
        platform: str,
        filter_schema_version: int | None = None,
        filter_snapshot: dict | None = None,
        task_input_digest: str | None = None,
        interruption_kind: str | None = None,
    ) -> None:
        """T115: 持久化 checkpoint 身份一致性信息（写入端）。

        一次性写入 platform、filter_schema_version、filter_snapshot_json、
        task_input_digest 和 interruption_kind。用于 run 创建时冻结身份。
        """
        snapshot_json = json.dumps(filter_snapshot or {}, ensure_ascii=False, sort_keys=True)
        ts = _now()
        with self._connection() as conn:
            self._assert_recovery_writes_allowed(conn)
            conn.execute(
                "UPDATE screening_runs SET platform=?, filter_schema_version=?, "
                "filter_snapshot_json=?, task_input_digest=?, interruption_kind=?, "
                "updated_at=? WHERE id=?",
                (str(platform), filter_schema_version, snapshot_json,
                 task_input_digest, interruption_kind, ts, str(run_id)),
            )

    def verify_checkpoint_identity(
        self,
        run_id: str,
        *,
        expected_platform: str,
        expected_task_input_digest: str | None = None,
    ) -> tuple[bool, str]:
        """T115: 校验 checkpoint 身份一致性。

        继续运行前调用：若 run 的 platform 与 expected_platform 不一致，
        或 task_input_digest 与期望值不一致，返回 (False, reason)。
        """
        identity = self.get_run_checkpoint_identity(run_id)
        if identity is None:
            return (False, "run_not_found")
        if identity["platform"] != expected_platform:
            return (False, f"platform_mismatch: {identity['platform']} != {expected_platform}")
        if expected_task_input_digest is not None and identity["task_input_digest"] != expected_task_input_digest:
            return (False, "task_input_digest_mismatch")
        return (True, "")

    # -- T116: 收藏/反馈原子 upsert + 内部 UUID 关联 ----------------------

    def upsert_job_for_favorite(
        self,
        *,
        platform: str,
        platform_job_id: str,
        canonical_url: str,
        title: str = "",
        company: str = "",
        salary: str = "",
        location: str = "",
        jd: str = "",
        experience: str = "",
        degree: str = "",
        extra: dict | None = None,
    ) -> dict:
        """T116: 收藏/反馈所需的原子"岗位 upsert + 内部 UUID 关联"存储操作。

        不把 platform_job_id 当内部 UUID。内部 job_id 由 upsert_job 分配。
        """
        return self.upsert_job(
            platform=platform,
            platform_job_id=platform_job_id,
            canonical_url=canonical_url,
            title=title,
            company=company,
            salary=salary,
            location=location,
            jd=jd,
            experience=experience,
            degree=degree,
            extra=extra,
        )
