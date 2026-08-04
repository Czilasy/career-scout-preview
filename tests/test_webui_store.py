import hashlib
import json
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from webui.store import TaskStore, _now


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_task_lifecycle_and_logs_are_persisted(self):
        self.store.create_task(
            "run-1", "scrape", {"keyword": "Python"},
            output_path="jobs.json", detail_output_path="details.json",
        )
        self.store.update_task("run-1", "running")
        first = self.store.append_log("run-1", "开始")
        second = self.store.append_log("run-1", "完成")
        self.store.update_task("run-1", "succeeded", returncode=0)

        task = self.store.get_task("run-1", include_logs=True)

        self.assertEqual(task["status"], "succeeded")
        self.assertEqual(task["params"], {"keyword": "Python"})
        self.assertEqual([item["seq"] for item in task["logs"]], [first, second])
        self.assertEqual([item["line"] for item in task["logs"]], ["开始", "完成"])
        self.assertEqual(self.store.list_tasks()[0]["id"], "run-1")

    def test_profile_round_trip(self):
        profile = {"target_titles": ["后端工程师"], "min_salary": 25}

        self.store.save_profile(profile)

        self.assertEqual(self.store.load_profile(), profile)

    def test_new_store_marks_unfinished_tasks_interrupted(self):
        self.store.create_task("run-1", "scrape", {})
        self.store.update_task("run-1", "running")

        reopened = TaskStore(self.db_path)

        self.assertEqual(reopened.get_task("run-1")["status"], "interrupted")

    def test_terminal_task_rejects_invalid_transition(self):
        self.store.create_task("run-1", "scrape", {})
        self.store.update_task("run-1", "running")
        self.store.update_task("run-1", "failed", returncode=1)

        with self.assertRaisesRegex(ValueError, "failed"):
            self.store.update_task("run-1", "running")

    def test_missing_task_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.store.get_task("missing")


class Migration28SchemaTests(unittest.TestCase):
    """Task 001 migration 28 contract tests."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self._cleanup_shared_backup_dir()

    def tearDown(self):
        self._cleanup_shared_backup_dir()
        self.temp.cleanup()

    @staticmethod
    def _cleanup_shared_backup_dir():
        dummy = TaskStore.__new__(TaskStore)
        backup_dir = TaskStore._migration_backup_dir(dummy)
        if backup_dir.exists():
            for path in backup_dir.iterdir():
                try:
                    path.unlink()
                except OSError:
                    pass

    def _build_v27_database(self):
        with patch.object(TaskStore, "_migration_028", return_value=None):
            store = TaskStore(self.db_path)
        self.assertEqual(store.schema_version(), 27)
        return store

    def test_migration_28_adds_lifecycle_tables_column_and_indexes(self):
        store = TaskStore(self.db_path)

        with store._connection() as conn:
            profile_job_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(profile_jobs)")
            }
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            index_sql = [
                row["sql"] or ""
                for row in conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index'"
                )
            ]

        self.assertGreaterEqual(store.schema_version(), 28)
        self.assertIn("last_follow_up_at", profile_job_columns)
        self.assertIn("profile_job_events", tables)
        self.assertIn("profile_job_command_receipts", tables)
        joined = " ".join(index_sql).lower()
        self.assertIn("idx_profile_jobs_reminder_candidates", joined)
        self.assertIn("idx_profile_job_events_history", joined)
        self.assertIn("idx_profile_job_command_receipts_job", joined)

    def test_migration_28_is_idempotent_and_new_history_tables_enforce_foreign_keys(self):
        store = TaskStore(self.db_path)
        reopened = TaskStore(self.db_path)

        self.assertEqual(reopened.schema_version(), 28)
        with reopened._connection() as conn:
            migration_count = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=28"
            ).fetchone()[0]
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO profile_job_events (id, profile_id, job_id, action, occurred_at) "
                    "VALUES ('orphan-event', 'missing-profile', 'missing-job', 'mark_read', ?)",
                    (_now(),),
                )

        self.assertEqual(store.schema_version(), 28)
        self.assertEqual(migration_count, 1)

    def test_migration_27_to_28_keeps_existing_rows_and_creates_no_history(self):
        store = self._build_v27_database()
        profile = store.create_profile("迁移画像")
        job = store.save_job(
            "https://www.zhipin.com/job_detail/migration-28.html",
            "https://www.zhipin.com/job_detail/migration-28.html",
            "岗位", "公司", "20K", "上海", "JD",
        )
        store.link_profile_job(profile["id"], job["id"], None, None, status="interested")

        reopened = TaskStore(self.db_path)
        with reopened._connection() as conn:
            profile_job = conn.execute(
                "SELECT status, applied_at, last_follow_up_at FROM profile_jobs "
                "WHERE profile_id=? AND job_id=?",
                (profile["id"], job["id"]),
            ).fetchone()
            event_count = conn.execute(
                "SELECT COUNT(*) FROM profile_job_events"
            ).fetchone()[0]
            receipt_count = conn.execute(
                "SELECT COUNT(*) FROM profile_job_command_receipts"
            ).fetchone()[0]

        self.assertGreaterEqual(reopened.schema_version(), 28)
        self.assertEqual(profile_job["status"], "interested")
        self.assertIsNone(profile_job["applied_at"])
        self.assertIsNone(profile_job["last_follow_up_at"])
        self.assertEqual(event_count, 0)
        self.assertEqual(receipt_count, 0)
        manifest_path = next(reopened.backup_dir_for_tests().glob("*.manifest.json"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_schema_version"], 27)
        self.assertEqual(manifest["backup_schema_version"], 27)
        self.assertIn("-to-v28-", manifest["backup_file"])

    def test_failed_migration_28_rolls_back_schema_and_version(self):
        self._build_v27_database()

        original_add_column = TaskStore._add_column_if_missing

        def fail_after_first_alter(conn, table, column, definition):
            original_add_column(conn, table, column, definition)
            raise RuntimeError("injected migration failure")

        with patch.object(
            TaskStore, "_add_column_if_missing", side_effect=fail_after_first_alter
        ):
            with self.assertRaisesRegex(RuntimeError, "injected migration failure"):
                TaskStore(self.db_path)

        with sqlite3.connect(self.db_path) as conn:
            version = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(profile_jobs)")
            }
            event_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='profile_job_events'"
            ).fetchone()
        self.assertEqual(version, 27)
        self.assertNotIn("last_follow_up_at", columns)
        self.assertIsNone(event_table)


class SchemaMigrationTests(unittest.TestCase):
    """T004: versioned SQLite migrations preserve old data and add new tables."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_schema_migrations_table_exists_with_version(self):
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            row = conn.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        self.assertGreaterEqual(row["version"], 1)

    def test_old_tables_preserved_after_migration(self):
        store = TaskStore(self.db_path)
        store.create_task("old-1", "scrape", {"k": "v"}, output_path="a.json", detail_output_path="b.json")
        store.save_profile({"target_titles": ["后端"]}, name="default")

        reopened = TaskStore(self.db_path)
        task = reopened.get_task("old-1")
        self.assertEqual(task["params"], {"k": "v"})
        self.assertEqual(reopened.load_profile("default"), {"target_titles": ["后端"]})

    def test_old_default_profile_copied_to_candidate_profiles(self):
        store = TaskStore(self.db_path)
        store.save_profile({"target_titles": ["后端"], "min_salary": 20}, name="default")

        reopened = TaskStore(self.db_path)
        profiles = reopened.list_candidate_profiles()
        self.assertTrue(any(p["name"] == "default" for p in profiles))

    def test_new_workbench_tables_exist(self):
        store = TaskStore(self.db_path)
        expected = {
            "candidate_profiles", "resumes", "ai_settings", "search_runs",
            "run_queries", "jobs", "profile_jobs", "feedback_events",
            "preference_versions", "schema_migrations",
        }
        with store._connection() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            tables = {row["name"] for row in rows}
        self.assertTrue(expected.issubset(tables), f"missing: {expected - tables}")

    def test_migration_is_idempotent(self):
        TaskStore(self.db_path)
        store = TaskStore(self.db_path)
        store2 = TaskStore(self.db_path)
        # Reopening should not error or duplicate migrations
        self.assertGreaterEqual(store.schema_version(), store2.schema_version())

    def test_migration_026_adds_start_finish_columns(self):
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            columns = {
                row["name"] for row in conn.execute(
                    "PRAGMA table_info(screening_runs)"
                ).fetchall()
            }
        self.assertIn("started_at", columns)
        self.assertIn("finished_at", columns)
        self.assertGreaterEqual(store.schema_version(), 26)


class MigrationBootstrapBackupTests(unittest.TestCase):
    """T102: migration 27 前的 SQLite backup / manifest / SHA-256 / quick_check / 版本一致 / 失败阻断。

    本组测试只验证 bootstrap 合同本身：当且仅当源库已存在且 schema version < 27 时，
    TaskStore 构造前必须生成一致性备份、manifest 与 SHA-256；任一验证失败时
    TaskStore 拒绝构造，且源库未被 v27 部分写入。备份产物必须落在本地忽略目录，
    manifest 不得泄露绝对路径。重复构造必须幂等（已迁移库不再触发备份）。

    这些测试在 T103 实现前应全部失败（RED）。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            ignore_cleanup_errors=True
        )
        self.root = pathlib.Path(self.temp.name)
        self.db_path = self.root / "state" / "webui.db"
        # 清理全局备份目录，避免跨测试残留干扰
        self._cleanup_shared_backup_dir()

    def tearDown(self):
        self.temp.cleanup()

    def _cleanup_shared_backup_dir(self) -> None:
        """清理全局备份目录中的残留文件，确保每个测试从空目录开始。"""
        from webui.store import TaskStore
        dummy = TaskStore.__new__(TaskStore)
        backup_dir = TaskStore._migration_backup_dir(dummy)
        if backup_dir.exists():
            for f in backup_dir.iterdir():
                try:
                    f.unlink()
                except OSError:
                    pass

    def _build_v26_database(self) -> None:
        """构造一个真实的 v26 数据库，用于触发迁移前 bootstrap。"""
        TaskStore(self.db_path)

    def _patch_schema_to_below_27(self) -> None:
        """把已迁移库的 schema_migrations 版本强制压到 26，模拟迁移前状态。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM schema_migrations WHERE version >= 27")

    def test_bootstrap_creates_backup_manifest_and_sha256_before_v27(self):
        """源库 v26 且待迁移时，构造 TaskStore 必须先产出备份文件、manifest 与 SHA-256。"""
        self._build_v26_database()
        self._patch_schema_to_below_27()

        store = TaskStore(self.db_path)

        backup_dir = store.backup_dir_for_tests()
        backups = list(backup_dir.glob("*.sqlite"))
        manifests = list(backup_dir.glob("*.manifest.json"))
        self.assertEqual(len(backups), 1, "应恰好生成一个 v26 备份文件")
        self.assertEqual(len(manifests), 1, "应恰好生成一个 manifest")

        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        # 必填字段
        for key in (
            "backup_file",
            "source_schema_version",
            "backup_schema_version",
            "source_size_bytes",
            "backup_size_bytes",
            "source_sha256",
            "backup_sha256",
            "created_at",
            "tool_version",
        ):
            self.assertIn(key, manifest, f"manifest 缺少字段: {key}")
        # 源库与备份 schema 版本一致
        self.assertEqual(manifest["source_schema_version"], 26)
        self.assertEqual(manifest["backup_schema_version"], 26)
        # SHA-256 必须是 64 位十六进制
        self._full_sha256(manifest["source_sha256"])
        self._full_sha256(manifest["backup_sha256"])
        # 备份 SHA-256 必须与备份文件实际内容一致
        actual = hashlib.sha256(backups[0].read_bytes()).hexdigest()
        self.assertEqual(actual, manifest["backup_sha256"])

    def test_bootstrap_manifest_omits_absolute_paths(self):
        """manifest 不得记录本地绝对路径（源库路径、profile 路径等）。"""
        self._build_v26_database()
        self._patch_schema_to_below_27()

        store = TaskStore(self.db_path)

        manifest_path = next(store.backup_dir_for_tests().glob("*.manifest.json"))
        raw = manifest_path.read_text(encoding="utf-8")
        # 备份目录与源库路径都包含临时目录绝对路径；manifest 里不得出现
        self.assertNotIn(str(self.root), raw, "manifest 泄露了本地绝对路径")
        self.assertNotIn(str(self.db_path), raw, "manifest 泄露了源库绝对路径")

    def test_bootstrap_backup_passes_readonly_quick_check(self):
        """备份库以只读连接打开时 PRAGMA quick_check 必须返回 ok，且可读 schema_migrations。"""
        self._build_v26_database()
        self._patch_schema_to_below_27()

        store = TaskStore(self.db_path)

        backup = next(store.backup_dir_for_tests().glob("*.sqlite"))
        # 只读连接验证
        ro = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
        try:
            row = ro.execute("PRAGMA quick_check").fetchone()
            self.assertEqual(row[0], "ok")
            v = ro.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
            self.assertEqual(v[0], 26)
        finally:
            ro.close()

    def test_bootstrap_skipped_when_already_at_target(self):
        """已迁移到 27 的库重复构造 TaskStore 不得再生成新备份。"""
        self._build_v26_database()
        # 手动把 schema version 提升到 27，模拟 migration 27 已执行
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO schema_migrations (version, applied_at, description) "
                "VALUES (27, ?, 'simulated migration 27 for bootstrap test')",
                (_now(),),
            )

        store = TaskStore(self.db_path)
        self.assertGreaterEqual(store.schema_version(), 27)

        backup_dir = store.backup_dir_for_tests()
        first_count = len(list(backup_dir.glob("*.sqlite")))
        self.assertEqual(first_count, 0, "已迁移到 27 的库不得生成备份")

        # 第二次构造：仍 27，不得再备份
        TaskStore(self.db_path)
        second_count = len(list(backup_dir.glob("*.sqlite")))
        self.assertEqual(second_count, 0, "已迁移库重复构造不得新增备份")

    def test_bootstrap_failure_blocks_taskstore_construction(self):
        """备份验证失败时 TaskStore 必须拒绝构造，且源库未被写入 v27。"""
        self._build_v26_database()
        self._patch_schema_to_below_27()

        # 把源库损坏：截断字节，使 SQLite backup/quick_check 失败
        self.db_path.write_bytes(self.db_path.read_bytes()[:512])

        with self.assertRaises(Exception):
            TaskStore(self.db_path)

        # 源库不得出现 v27 migration 记录（即使是损坏的库也不得有部分写入）
        # 用只读连接尝试读取；损坏库可能完全无法打开，此时也算"未写入 v27"
        try:
            ro = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            try:
                row = ro.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version >= 27"
                ).fetchone()
                self.assertEqual(row[0], 0, "源库被部分写入 v27")
            finally:
                ro.close()
        except sqlite3.DatabaseError:
            pass  # 损坏库无法打开，符合"未被 v27 部分写入"

    def test_bootstrap_artifacts_live_in_ignored_directory(self):
        """备份与 manifest 必须落在 .gitignore 已忽略的目录，不进入仓库。"""
        self._build_v26_database()
        self._patch_schema_to_below_27()

        store = TaskStore(self.db_path)
        backup_dir = store.backup_dir_for_tests()

        repo_root = pathlib.Path(__file__).resolve().parent.parent
        try:
            rel = backup_dir.resolve().relative_to(repo_root.resolve())
        except ValueError:
            self.fail("备份目录必须位于仓库内（相对路径可被 .gitignore 匹配）")

        gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8")
        # 备份目录相对路径必须被 .gitignore 覆盖（按目录前缀匹配）
        matched = any(
            str(rel).startswith(line.strip().rstrip("/"))
            for line in gitignore.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
        self.assertTrue(matched, f"备份目录 {rel} 未被 .gitignore 覆盖")

    @staticmethod
    def _full_sha256(value: str) -> None:
        assert isinstance(value, str) and len(value) == 64 and all(
            c in "0123456789abcdef" for c in value
        ), f"不是合法 SHA-256 十六进制: {value!r}"


class Migration27SchemaTests(unittest.TestCase):
    """T105: migration 27 失败优先测试——平台字段、双身份、筛选快照、source attempt。

    覆盖 data-model.md Migration 27 事务的全部新增字段、列重命名、
    新表、存量回填和索引要求。T106-T110 实现前应全部失败（RED）。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self._cleanup_shared_backup_dir()

    def tearDown(self):
        self.temp.cleanup()

    def _cleanup_shared_backup_dir(self) -> None:
        from webui.store import TaskStore
        dummy = TaskStore.__new__(TaskStore)
        backup_dir = TaskStore._migration_backup_dir(dummy)
        if backup_dir.exists():
            for f in backup_dir.iterdir():
                try:
                    f.unlink()
                except OSError:
                    pass

    def _column_names(self, conn, table: str) -> set:
        return {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }

    def test_jobs_has_platform_and_dual_identity_fields(self):
        """jobs 必须新增 platform、platform_job_id、experience、degree、extra_json。"""
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            cols = self._column_names(conn, "jobs")
        for expected in ("platform", "platform_job_id", "experience", "degree", "extra_json"):
            self.assertIn(expected, cols, f"jobs 缺少字段: {expected}")

    def test_screening_runs_has_platform_and_filter_snapshot_fields(self):
        """screening_runs 新增 platform、filter_schema_version、filter_snapshot_json、task_input_digest、interruption_kind。"""
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            cols = self._column_names(conn, "screening_runs")
        for expected in (
            "platform", "filter_schema_version", "filter_snapshot_json",
            "task_input_digest", "interruption_kind",
        ):
            self.assertIn(expected, cols, f"screening_runs 缺少字段: {expected}")

    def test_screening_results_has_platform_and_dual_identity(self):
        """screening_results 新增 platform、platform_job_id、可空内部 job_id、experience、degree、extra_json。"""
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            cols = self._column_names(conn, "screening_results")
        for expected in (
            "platform", "platform_job_id", "job_id",
            "experience", "degree", "extra_json",
        ):
            self.assertIn(expected, cols, f"screening_results 缺少字段: {expected}")

    def test_screening_pending_results_renames_job_id_to_platform_job_id(self):
        """screening_pending_results 的 job_id 必须重命名为 platform_job_id，并新增 platform。"""
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            cols = self._column_names(conn, "screening_pending_results")
        self.assertIn("platform", cols)
        self.assertIn("platform_job_id", cols)
        # 旧的同名 job_id 列不得保留（data-model 禁止兼容别名）
        self.assertNotIn("job_id", cols, "screening_pending_results 不得保留旧 job_id 列")

    def test_scrape_run_jobs_renames_job_id_to_platform_job_id(self):
        """scrape_run_jobs 的 job_id 必须重命名为 platform_job_id。"""
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            cols = self._column_names(conn, "scrape_run_jobs")
        self.assertIn("platform_job_id", cols)
        self.assertNotIn("job_id", cols, "scrape_run_jobs 不得保留旧 job_id 列")

    def test_tuning_experiments_has_platform(self):
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            cols = self._column_names(conn, "tuning_experiments")
        self.assertIn("platform", cols)

    def test_tuning_task_manifests_has_platform(self):
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            cols = self._column_names(conn, "tuning_task_manifests")
        self.assertIn("platform", cols)

    def test_tuning_stage_artifacts_has_outer_platform_columns(self):
        """tuning_stage_artifacts 新增 platform、source_artifact_kind、scope_digest、task_input_digest。"""
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            cols = self._column_names(conn, "tuning_stage_artifacts")
        for expected in (
            "platform", "source_artifact_kind", "scope_digest", "task_input_digest",
        ):
            self.assertIn(expected, cols, f"tuning_stage_artifacts 缺少字段: {expected}")

    def test_screening_source_attempts_table_exists(self):
        """migration 27 必须创建追加式 screening_source_attempts 表。"""
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            tables = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertIn("screening_source_attempts", tables)

    def test_screening_source_attempts_has_required_columns_and_constraints(self):
        """screening_source_attempts 必须有枚举、计数、空证据、外键和唯一约束。"""
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            cols = self._column_names(conn, "screening_source_attempts")
            # 表定义 + 显式索引（自动索引 sql 为 NULL，过滤掉）
            table_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='screening_source_attempts'"
            ).fetchone()
            indexes = [
                row["sql"] for row in conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='screening_source_attempts'"
                )
                if row["sql"]
            ]
        for expected in (
            "id", "run_id", "platform", "combo_key", "attempt_no",
            "input_hash", "outcome_kind", "job_count",
            "empty_evidence_json", "error_code", "error_reason", "created_at",
        ):
            self.assertIn(expected, cols, f"screening_source_attempts 缺少字段: {expected}")
        # UNIQUE(run_id, combo_key, attempt_no) 可能在表定义或显式索引中
        joined = (str(table_sql["sql"]) + " " + " ".join(indexes)).upper()
        self.assertIn("RUN_ID", joined)
        self.assertIn("COMBO_KEY", joined)
        self.assertIn("ATTEMPT_NO", joined)
        self.assertIn("UNIQUE", joined)

    def test_existing_jobs_backfilled_to_boss_platform(self):
        """存量 jobs 记录的 platform 必须回填为 boss。"""
        store = TaskStore(self.db_path)
        # 先插入一条 job（用 v26 schema，无 platform 列——T106 后才有列）
        with store._connection() as conn:
            conn.execute(
                "INSERT INTO jobs (id, canonical_url, title, company, first_seen_at, last_seen_at) "
                "VALUES ('job-1', 'https://www.zhipin.com/job/1.htm', 't', 'c', ?, ?)",
                (_now(), _now()),
            )
        # 重新打开触发 migration 27（已迁移则直接读取）
        reopened = TaskStore(self.db_path)
        with reopened._connection() as conn:
            row = conn.execute(
                "SELECT platform FROM jobs WHERE id = 'job-1'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["platform"], "boss")

    def test_existing_screening_runs_backfilled_to_boss(self):
        """存量 screening_runs 记录的 platform 必须回填为 boss。"""
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            conn.execute(
                "INSERT INTO screening_runs (id, status, created_at, updated_at) "
                "VALUES ('run-1', 'succeeded', ?, ?)",
                (_now(), _now()),
            )
        reopened = TaskStore(self.db_path)
        with reopened._connection() as conn:
            row = conn.execute(
                "SELECT platform FROM screening_runs WHERE id = 'run-1'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["platform"], "boss")

    def test_existing_tuning_experiments_backfilled_to_boss(self):
        """存量 tuning_experiments 记录的 platform 必须回填为 boss。"""
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            conn.execute(
                "INSERT INTO tuning_experiments (id, spec_version, source_scope_json, created_at, updated_at) "
                "VALUES ('exp-1', 'v1', '{}', ?, ?)",
                (_now(), _now()),
            )
        reopened = TaskStore(self.db_path)
        with reopened._connection() as conn:
            row = conn.execute(
                "SELECT platform FROM tuning_experiments WHERE id = 'exp-1'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["platform"], "boss")

    def test_jobs_platform_job_id_partial_unique_index(self):
        """jobs 上必须创建 (platform, platform_job_id) 部分唯一索引，且 platform_job_id IS NOT NULL。"""
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            indexes = [
                row["sql"] for row in conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='jobs'"
                )
                if row["sql"]
            ]
        joined = " ".join(indexes).lower()
        self.assertIn("platform", joined)
        self.assertIn("platform_job_id", joined)
        self.assertIn("unique", joined)

    def test_schema_version_is_at_least_27(self):
        """migration 27 完成后 schema_version 必须 >= 27。"""
        store = TaskStore(self.db_path)
        self.assertGreaterEqual(store.schema_version(), 27)

    def test_migration_27_is_idempotent(self):
        """重复构造 TaskStore 不得重复执行 migration 27 或报错。"""
        TaskStore(self.db_path)
        store = TaskStore(self.db_path)
        self.assertGreaterEqual(store.schema_version(), 27)

    def test_failed_migration_27_rolls_back_schema_and_version(self):
        """T705: migration 27 中途失败必须整笔回滚，源库版本仍是 26，无部分写入。"""
        # 先构造一个 v26 库（含 jobs/screening_runs 等基础表，无 platform 列）
        TaskStore(self.db_path)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM schema_migrations WHERE version >= 27")
            # 记录 migration 27 前的 jobs 列集（无 platform/platform_job_id）
            pre_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
            }
        self.assertNotIn("platform", pre_cols)
        self.assertNotIn("platform_job_id", pre_cols)

        # 注入失败：第一次 ALTER 成功后立即抛错
        original_add_column = TaskStore._add_column_if_missing

        def fail_after_first_alter(conn, table, column, definition):
            original_add_column(conn, table, column, definition)
            raise RuntimeError("injected migration 27 failure")

        with patch.object(
            TaskStore, "_add_column_if_missing", side_effect=fail_after_first_alter
        ):
            with self.assertRaisesRegex(RuntimeError, "injected migration 27 failure"):
                TaskStore(self.db_path)

        # 验证源库未被部分写入：版本仍是 26，jobs 仍无 platform 列
        with sqlite3.connect(self.db_path) as conn:
            version = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            post_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
            }
        self.assertEqual(version, 26, "migration 27 失败后版本必须仍是 26")
        self.assertNotIn("platform", post_cols, "回滚后 jobs 不得残留 platform 列")
        self.assertNotIn("platform_job_id", post_cols, "回滚后 jobs 不得残留 platform_job_id 列")

    def test_old_jobs_canonical_url_unique_constraint_preserved(self):
        """migration 27 必须保留 jobs.canonical_url 的全局唯一约束。"""
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            schema = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'"
            ).fetchone()
            indexes = [
                row["sql"] for row in conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='jobs'"
                )
                if row["sql"]
            ]
        joined = (str(schema["sql"]) + " " + " ".join(indexes)).lower()
        self.assertIn("canonical_url", joined)
        self.assertIn("unique", joined)


class JobUpsertDualIndexTests(unittest.TestCase):
    """T111: Job 双索引冲突算法八个分支的事务测试。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self._cleanup_shared_backup_dir()
        self.store = TaskStore(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def _cleanup_shared_backup_dir(self) -> None:
        from webui.store import TaskStore
        dummy = TaskStore.__new__(TaskStore)
        backup_dir = TaskStore._migration_backup_dir(dummy)
        if backup_dir.exists():
            for f in backup_dir.iterdir():
                try:
                    f.unlink()
                except OSError:
                    pass

    def _boss_url(self, n):
        return f"https://www.zhipin.com/job/{n}.html"

    def test_branch_1_url_platform_mismatch_rejected(self):
        """分支1：URL host 不属于声明平台时返回 platform_url_mismatch。"""
        result = self.store.upsert_job(
            platform="boss", platform_job_id="b1",
            canonical_url="https://www.zhaopin.com/jobs/1.html",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "platform_url_mismatch")

    def test_branch_2_url_hit_different_platform_conflict(self):
        """分支2：URL 命中但该行平台与输入平台不一致，返回 job_identity_conflict。

        构造脏数据（BOSS URL 被标记为 zhilian 平台，模拟历史/迁移残留），
        以 boss 平台 upsert 同一 URL：分支1 通过（URL 属于 boss），
        分支2 命中 by_url.platform='zhilian' != 'boss'，返回冲突，不得跨平台认领。
        """
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO jobs (id, canonical_url, platform, platform_job_id, first_seen_at, last_seen_at) "
                "VALUES ('job-dirty', ?, 'zhilian', NULL, ?, ?)",
                (self._boss_url(1), _now(), _now()),
            )
        result = self.store.upsert_job(
            platform="boss", platform_job_id="b1",
            canonical_url=self._boss_url(1),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "job_identity_conflict")
        # 冲突时原行数据保持不变
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT id, platform, canonical_url FROM jobs WHERE canonical_url=?",
                (self._boss_url(1),),
            ).fetchone()
        self.assertEqual(row["id"], "job-dirty")
        self.assertEqual(row["platform"], "zhilian")

    def test_branch_3_both_miss_create_new_job(self):
        """分支3：平台ID和URL都未命中，创建新内部UUID。"""
        result = self.store.upsert_job(
            platform="boss", platform_job_id="b1",
            canonical_url=self._boss_url(1),
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["job_id"])
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT id, platform, platform_job_id, canonical_url FROM jobs "
                "WHERE platform = 'boss' AND platform_job_id = 'b1'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["canonical_url"], self._boss_url(1))
        self.assertEqual(row["id"], result["job_id"])

    def test_branch_4_only_platform_id_hit_update_url(self):
        """分支4：只命中平台ID，新URL未被占用，更新URL。"""
        self.store.upsert_job(platform="boss", platform_job_id="b1", canonical_url=self._boss_url(1))
        result = self.store.upsert_job(platform="boss", platform_job_id="b1", canonical_url=self._boss_url(2))
        self.assertTrue(result["ok"])
        with self.store._connection() as conn:
            row = conn.execute("SELECT canonical_url FROM jobs WHERE platform='boss' AND platform_job_id='b1'").fetchone()
        self.assertEqual(row["canonical_url"], self._boss_url(2))

    def test_branch_4_only_platform_id_hit_url_taken_by_other(self):
        """分支4：只命中平台ID，但新URL已被其它行占用，返回 job_identity_conflict。"""
        self.store.upsert_job(platform="boss", platform_job_id="b1", canonical_url=self._boss_url(1))
        self.store.upsert_job(platform="boss", platform_job_id="b2", canonical_url=self._boss_url(2))
        result = self.store.upsert_job(platform="boss", platform_job_id="b1", canonical_url=self._boss_url(2))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "job_identity_conflict")

    def test_branch_5_only_url_hit_same_platform_write_platform_id(self):
        """分支5：只命中URL，平台一致，platform_job_id 为 NULL 时补写。"""
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO jobs (id, canonical_url, platform, platform_job_id, first_seen_at, last_seen_at) "
                "VALUES ('job-x', ?, 'boss', NULL, ?, ?)",
                (self._boss_url(1), _now(), _now()),
            )
        result = self.store.upsert_job(platform="boss", platform_job_id="b1", canonical_url=self._boss_url(1))
        self.assertTrue(result["ok"])
        with self.store._connection() as conn:
            row = conn.execute("SELECT id, platform_job_id FROM jobs WHERE canonical_url=?", (self._boss_url(1),)).fetchone()
        self.assertEqual(row["id"], "job-x")
        self.assertEqual(row["platform_job_id"], "b1")

    def test_branch_5_only_url_hit_different_platform_id(self):
        """分支5：只命中URL，平台一致但已有不同 platform_job_id，返回冲突。"""
        self.store.upsert_job(platform="boss", platform_job_id="b1", canonical_url=self._boss_url(1))
        result = self.store.upsert_job(platform="boss", platform_job_id="b2", canonical_url=self._boss_url(1))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "job_identity_conflict")

    def test_branch_6_both_hit_same_row_update(self):
        """分支6：平台ID和URL命中同一行，更新可变字段。"""
        self.store.upsert_job(platform="boss", platform_job_id="b1", canonical_url=self._boss_url(1), title="old")
        result = self.store.upsert_job(platform="boss", platform_job_id="b1", canonical_url=self._boss_url(1), title="new")
        self.assertTrue(result["ok"])
        with self.store._connection() as conn:
            row = conn.execute("SELECT title FROM jobs WHERE platform='boss' AND platform_job_id='b1'").fetchone()
        self.assertEqual(row["title"], "new")

    def test_url_only_upsert_preserves_existing_platform_job_id(self):
        url = self._boss_url(10)
        first = self.store.upsert_job(
            platform="boss", platform_job_id="stable-id", canonical_url=url
        )

        second = self.store.upsert_job(
            platform="boss", platform_job_id=None, canonical_url=url, title="updated"
        )

        self.assertTrue(second["ok"])
        self.assertEqual(second["job_id"], first["job_id"])
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT platform_job_id, title FROM jobs WHERE id=?",
                (first["job_id"],),
            ).fetchone()
        self.assertEqual(row["platform_job_id"], "stable-id")
        self.assertEqual(row["title"], "updated")

    def test_branch_7_both_hit_different_rows_conflict(self):
        """分支7：平台ID和URL分别命中不同内部UUID，返回冲突。"""
        self.store.upsert_job(platform="boss", platform_job_id="b1", canonical_url=self._boss_url(1))
        self.store.upsert_job(platform="boss", platform_job_id="b2", canonical_url=self._boss_url(2))
        result = self.store.upsert_job(platform="boss", platform_job_id="b1", canonical_url=self._boss_url(2))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "job_identity_conflict")

    def test_branch_8_conflict_preserves_original_data(self):
        """分支8：任一冲突保持原URL、内部UUID、收藏和反馈关联不变。"""
        r = self.store.upsert_job(platform="boss", platform_job_id="b1", canonical_url=self._boss_url(1))
        job_id = r["job_id"]
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles (id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES ('p1', 'test', '{}', '{}', ?, ?)",
                (_now(), _now()),
            )
            conn.execute(
                "INSERT INTO profile_jobs (profile_id, job_id, shown_at, status) VALUES ('p1', ?, ?, 'new')",
                (job_id, _now()),
            )
        self.store.upsert_job(platform="boss", platform_job_id="b2", canonical_url=self._boss_url(2))
        result = self.store.upsert_job(platform="boss", platform_job_id="b1", canonical_url=self._boss_url(2))
        self.assertFalse(result["ok"])
        with self.store._connection() as conn:
            row = conn.execute("SELECT id, canonical_url FROM jobs WHERE platform='boss' AND platform_job_id='b1'").fetchone()
            pj = conn.execute("SELECT COUNT(*) FROM profile_jobs WHERE job_id=?", (job_id,)).fetchone()
        self.assertEqual(row["canonical_url"], self._boss_url(1))
        self.assertEqual(row["id"], job_id)
        self.assertEqual(pj[0], 1)


class CandidateProfileStoreTests(unittest.TestCase):
    """T012: multi-profile create, copy manual fields, isolate feedback."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "db")

    def tearDown(self):
        self.temp.cleanup()

    def test_create_and_list_profiles(self):
        p1 = self.store.create_profile("画像 A")
        p2 = self.store.create_profile("画像 B")
        ids = [p["id"] for p in self.store.list_candidate_profiles()]
        self.assertEqual(set(ids), {p1["id"], p2["id"]})

    def test_profile_name_length_validated(self):
        with self.assertRaisesRegex(ValueError, "名称"):
            self.store.create_profile("")
        with self.assertRaisesRegex(ValueError, "名称"):
            self.store.create_profile("x" * 81)

    def test_copy_manual_fields_without_ai_preference(self):
        source = self.store.create_profile("源", confirmed_fields={"city": "上海", "roles": ["Python"]})
        # Simulate AI negative preference on source
        self.store.update_profile(source["id"], ai_preference={"negative_terms": ["外包"]})
        copied = self.store.create_profile("副本", copy_from=source["id"])

        self.assertEqual(copied["confirmed_fields"], {"city": "上海", "roles": ["Python"]})
        # Copied profile must NOT inherit AI negative preference
        self.assertEqual(copied.get("ai_preference") or {}, {})

    def test_profile_isolation_for_feedback(self):
        p1 = self.store.create_profile("P1")
        p2 = self.store.create_profile("P2")
        job = self.store.save_job(
            "https://www.zhipin.com/job_detail/j1.html",
            "https://www.zhipin.com/job_detail/j1.html",
            "后端", "公司", "20K", "上海", "JD",
        )
        self.store.create_feedback(p1["id"], job["id"], None, "not_interested", reason="role")
        # P2 should have zero effective feedback
        self.assertEqual(self.store.count_effective_feedback(p2["id"]), 0)
        self.assertEqual(self.store.count_effective_feedback(p1["id"]), 1)


class SearchRunStoreTests(unittest.TestCase):
    """T006/T023/T040: parent run, child query states, budget, history."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "db")
        self.profile = self.store.create_profile("测试")

    def tearDown(self):
        self.temp.cleanup()

    def test_search_run_lifecycle_states(self):
        run = self.store.create_search_run(self.profile["id"], {"city": "上海"}, "ai")
        self.assertEqual(run["status"], "queued")
        self.store.update_search_run(run["id"], status="running")
        self.store.update_search_run(run["id"], status="succeeded")
        self.assertEqual(self.store.get_search_run(run["id"])["status"], "succeeded")

    def test_search_run_partial_state(self):
        run = self.store.create_search_run(self.profile["id"], {}, "ai")
        self.store.update_search_run(run["id"], status="running")
        self.store.update_search_run(run["id"], status="partial")
        self.assertEqual(self.store.get_search_run(run["id"])["status"], "partial")

    def test_run_query_with_controlled_paths(self):
        run = self.store.create_search_run(self.profile["id"], {}, "ai")
        q = self.store.create_run_query(
            run["id"], 0, {"keyword": "Python"},
            list_output_path="results/list_run.json_0.json",
            detail_output_path="results/detail_run.json_0.json",
            detail_budget=20,
        )
        self.assertEqual(q["status"], "queued")
        self.assertEqual(q["detail_budget"], 20)

    def test_run_query_detail_budget_sum_capped(self):
        run = self.store.create_search_run(self.profile["id"], {}, "ai", total_detail_budget=60)
        budgets = [20, 20, 20]
        for i, b in enumerate(budgets):
            self.store.create_run_query(run["id"], i, {}, "l.json", "d.json", b)
        queries = self.store.list_run_queries(run["id"])
        self.assertEqual(sum(q["detail_budget"] for q in queries), 60)


class FeedbackAndPreferenceStoreTests(unittest.TestCase):
    """T032/T033: feedback revoke, count, preference versions."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "db")
        self.profile = self.store.create_profile("P")
        self.job = self.store.save_job(
            "https://www.zhipin.com/job_detail/fb.html",
            "https://www.zhipin.com/job_detail/fb.html",
            "T", "C", "S", "L", "JD",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_feedback_and_revoke(self):
        fb = self.store.create_feedback(self.profile["id"], self.job["id"], None, "not_interested", reason="salary")
        self.assertEqual(self.store.count_effective_feedback(self.profile["id"]), 1)
        self.store.revoke_feedback(fb["id"])
        self.assertEqual(self.store.count_effective_feedback(self.profile["id"]), 0)

    def test_preference_version_created_after_five_feedback(self):
        for i in range(5):
            job = self.store.save_job(
                f"https://www.zhipin.com/job_detail/p{i}.html",
                f"https://www.zhipin.com/job_detail/p{i}.html",
                "T", "C", "S", "L", "JD",
            )
            self.store.create_feedback(self.profile["id"], job["id"], None, "interested")
        pv = self.store.save_preference_version(self.profile["id"], 5, {"positive_terms": ["Python"]})
        self.assertEqual(pv["source_feedback_count"], 5)
        latest = self.store.get_latest_preference(self.profile["id"])
        self.assertIsNotNone(latest)


class CleanupStoreTests(unittest.TestCase):
    """T041: 30-day cleanup preserves interested/applied, respects path boundary."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "db")
        self.profile = self.store.create_profile("P")

    def tearDown(self):
        self.temp.cleanup()

    def test_cleanup_removes_expired_normal_but_keeps_interested(self):
        from datetime import datetime, timezone, timedelta

        old = datetime.now(timezone.utc) - timedelta(days=35)
        expired = self.store.save_job("https://www.zhipin.com/job_detail/e.html", "https://www.zhipin.com/job_detail/e.html", "T", "C", "S", "L", "JD")
        self.store.update_job_expiry(expired["id"], old)
        self.store.link_profile_job(self.profile["id"], expired["id"], None, None, status="new")

        kept = self.store.save_job("https://www.zhipin.com/job_detail/k.html", "https://www.zhipin.com/job_detail/k.html", "T", "C", "S", "L", "JD")
        self.store.link_profile_job(self.profile["id"], kept["id"], None, None, status="interested")

        removed = self.store.cleanup_expired_jobs(days=30)
        self.assertGreaterEqual(removed, 1)
        # Interested job still accessible
        remaining = self.store.list_profile_jobs(self.profile["id"])
        statuses = [pj["status"] for pj in remaining]
        self.assertIn("interested", statuses)

    def test_cleanup_preserves_read_stale_and_lifecycle_events(self):
        from datetime import datetime, timezone, timedelta

        old = datetime.now(timezone.utc) - timedelta(days=35)
        preserved = []
        with self.store._connection() as conn:
            for status in ("read", "stale"):
                job = self.store.save_job(
                    f"https://www.zhipin.com/job_detail/cleanup-{status}.html",
                    f"https://www.zhipin.com/job_detail/cleanup-{status}.html",
                    "T", "C", "S", "L", "JD",
                )
                self.store.update_job_expiry(job["id"], old)
                self.store.link_profile_job(
                    self.profile["id"], job["id"], None, None, status=status
                )
                preserved.append((job["id"], status))

        # A real lifecycle event must remain attached to the explicit state.
        job_id, _ = preserved[0]
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO profile_job_events (id, profile_id, job_id, action, from_status, to_status, occurred_at) "
                "VALUES ('cleanup-event', ?, ?, 'mark_read', 'new', 'read', ?)",
                (self.profile["id"], job_id, _now()),
            )

        self.store.cleanup_expired_jobs(days=30)

        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT job_id, status FROM profile_jobs WHERE profile_id=? AND job_id IN (?, ?)",
                (self.profile["id"], preserved[0][0], preserved[1][0]),
            ).fetchall()
            event_count = conn.execute(
                "SELECT COUNT(*) FROM profile_job_events WHERE id='cleanup-event'"
            ).fetchone()[0]
        self.assertEqual({(row["job_id"], row["status"]) for row in rows}, set(preserved))
        self.assertEqual(event_count, 1)


class ScreeningRunStoreTests(unittest.TestCase):
    """AI 筛选任务持久化：进度落库 + 判定断点（screening_runs / screening_results）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_screening_run_lifecycle(self):
        self.store.create_screening_run(
            "sr-1", frozen_filters={"city": ["上海"]}, source_count=100,
            execution_params={"scrape_task_id": "task-9", "profile_summary": "画像"})

        run = self.store.get_screening_run("sr-1")
        self.assertEqual(run["status"], "queued")
        self.assertEqual(run["source_count"], 100)

        self.store.update_screening_run("sr-1", status="running", source_cursor=30)
        self.store.update_screening_run("sr-1", processed_count=60)
        self.store.update_screening_run("sr-1", status="done", match_count=20,
                                        mismatch_count=40)

        run = self.store.get_screening_run("sr-1")
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["source_cursor"], 30)
        self.assertEqual(run["processed_count"], 60)
        self.assertEqual(run["match_count"], 20)
        self.assertEqual(run["frozen_filters"], {"city": ["上海"]})
        self.assertEqual(run["execution_params"]["scrape_task_id"], "task-9")

    def test_missing_screening_run_returns_none(self):
        self.assertIsNone(self.store.get_screening_run("nope"))

    def test_verdicts_round_trip_and_upsert(self):
        self.store.create_screening_run("sr-2")
        self.store.save_screening_verdicts("sr-2", {
            "job-1": {"verdict": "match", "reason": "合适"},
            "job-2": {"verdict": "not_match", "reason": "不合适"},
        })
        # upsert：同一 (run_id, job_id) 覆盖
        self.store.save_screening_verdicts("sr-2", {
            "job-2": {"verdict": "uncertain", "reason": "待确认"},
        })

        verdicts = self.store.load_screening_verdicts("sr-2")
        self.assertEqual(verdicts["job-1"]["verdict"], "match")
        self.assertEqual(verdicts["job-2"]["verdict"], "uncertain")

    def test_result_snapshot_verdict_writeback_keeps_reason_and_caveats(self):
        run_id = self.store.save_pipeline_result({
            "jobs": [
                {"job_id": "j1", "title": "岗位", "verdict": "uncertain",
                 "verdict_reason": "旧原因", "caveats": ["旧提示"]},
            ],
            "dropped": [{"job_id": "d1", "title": "淘汰", "reason": "经验不符"}],
            "total_scraped": 2, "total_kept": 1, "total_matched": 0,
            "total_dropped": 1, "profile_summary": "",
        }, {})
        self.store.save_screening_verdicts(run_id, {
            "j1": {"verdict": "not_match", "reason": "新原因", "caveats": ["新提示"]},
        })
        verdicts = self.store.load_screening_verdicts(run_id)
        self.assertEqual(verdicts["j1"]["verdict"], "not_match")
        self.assertEqual(verdicts["j1"]["reason"], "新原因")
        self.assertEqual(verdicts["j1"]["caveats"], ["新提示"])
        loaded = self.store.load_latest_pipeline_result(run_id)
        job = loaded["result"]["jobs"][0]
        self.assertEqual(job["verdict"], "not_match")
        self.assertEqual(job["verdict_reason"], "新原因")
        self.assertEqual(job["caveats"], ["新提示"])

    def test_load_latest_pipeline_result_parses_legacy_json_verdict_cell(self):
        run_id = self.store.save_pipeline_result({
            "jobs": [{"job_id": "j1", "title": "岗位", "verdict": "uncertain"}],
            "dropped": [], "total_scraped": 1, "total_kept": 1,
            "total_matched": 0, "total_dropped": 0, "profile_summary": "",
        }, {})
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_results SET verdict = ?, verdict_reason = '' "
                "WHERE run_id = ? AND platform_job_id = ?",
                (json.dumps({"verdict": "match", "reason": "JSON原因",
                            "caveats": ["JSON提示"]}, ensure_ascii=False),
                 run_id, "j1"),
            )
        loaded = self.store.load_latest_pipeline_result(run_id)
        job = loaded["result"]["jobs"][0]
        self.assertEqual(job["verdict"], "match")
        self.assertEqual(job["verdict_reason"], "JSON原因")
        self.assertEqual(job["caveats"], ["JSON提示"])

    def test_recount_pipeline_result_updates_counts_and_status(self):
        run_id = self.store.save_pipeline_result({
            "jobs": [
                {"job_id": "m", "title": "A", "verdict": "match", "verdict_reason": "合适"},
                {"job_id": "n", "title": "B", "verdict": "not_match", "verdict_reason": "不符"},
                {"job_id": "u", "title": "C", "verdict": "uncertain", "verdict_reason": "待确认"},
            ],
            "dropped": [{"job_id": "d", "title": "D", "reason": "粗筛移除"}],
            "total_scraped": 4, "total_kept": 3, "total_matched": 1,
            "total_dropped": 1, "profile_summary": "",
        }, {})
        self.store.insert_pending_result(
            run_id, "u", failure_stage="ai_fine", failed_code="ai_missing_job",
        )
        counts = self.store.recount_pipeline_result(run_id)
        self.assertEqual(counts["status"], "partial")
        self.assertEqual(counts["pending_count"], 1)
        self.assertEqual(counts["total_dropped"], 1)
        self.store.save_screening_verdicts(run_id, {
            "u": {"verdict": "match", "reason": "重判匹配", "caveats": []},
        })
        self.store.delete_pending_result(run_id, "u")
        counts = self.store.recount_pipeline_result(run_id)
        self.assertEqual(counts["status"], "done")
        self.assertEqual(counts["pending_count"], 0)
        self.assertEqual(counts["match_count"], 2)
        self.assertEqual(counts["mismatch_count"], 1)
        self.assertEqual(counts["total_kept"], 3)
        self.assertEqual(counts["total_dropped"], 1)

    def test_latest_screening_run_for_source_matches_execution_params(self):
        self.store.create_screening_run(
            "sr-a", execution_params={"scrape_task_id": "t-1", "profile_summary": "画像A"})
        self.store.update_screening_run("sr-a", status="failed",
                                        error_code="quota_exhausted")
        self.store.create_screening_run(
            "sr-b", execution_params={"scrape_task_id": "t-2", "profile_summary": "画像B"})

        found = self.store.latest_screening_run_for_source(
            "t-1", statuses=("failed", "cancelled", "interrupted"))
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], "sr-a")
        # 状态不在筛选集合里则找不到
        self.assertIsNone(self.store.latest_screening_run_for_source(
            "t-2", statuses=("failed",)))

    def test_restart_marks_running_screening_run_interrupted(self):
        self.store.create_screening_run("sr-3")
        self.store.update_screening_run("sr-3", status="running")

        reopened = TaskStore(self.db_path)

        run = reopened.get_screening_run("sr-3")
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["error_code"], "restart")
        # data-model.md:114 / quickstart.md:173 —— 服务重启打断必须写
        # interruption_kind='process_restart'，否则公共状态会被映射成
        # 终态 cancelled、finish 接口 409 拒绝，任务卡死无法恢复。
        self.assertEqual(run["interruption_kind"], "process_restart")
        self.assertEqual(reopened.latest_interrupted_screening_run()["id"], "sr-3")

    def test_latest_interrupted_excludes_user_finished(self):
        self.store.create_screening_run("sr-user-finished")
        self.store.update_screening_run("sr-user-finished", status="running")
        self.store.update_screening_run(
            "sr-user-finished", status="cancelled", error_code="user_finished",
        )
        self.assertIsNone(self.store.latest_interrupted_screening_run())

    def test_latest_interrupted_excludes_user_cancelled_without_code(self):
        """用户主动停止的任务 error_code 为空，也不得当成服务重启中断。"""
        self.store.create_screening_run("sr-user-cancelled")
        self.store.update_screening_run("sr-user-cancelled", status="running")
        self.store.update_screening_run(
            "sr-user-cancelled", status="cancelled", error_reason="用户已停止筛选")
        self.assertIsNone(self.store.latest_interrupted_screening_run())

    def test_latest_interrupted_excludes_resumed_old_run(self):
        """旧 run 被新任务接管后标记 resumed，不再出现在重启恢复队列。"""
        self.store.create_screening_run("sr-resumed")
        self.store.update_screening_run("sr-resumed", status="running")
        self.store.update_screening_run(
            "sr-resumed", status="interrupted", error_code="restart")
        self.store.update_screening_run(
            "sr-resumed", error_code="resumed", error_reason="已由新任务接管续跑")
        self.assertIsNone(self.store.latest_interrupted_screening_run())

    def test_claim_paused_screening_run_clears_block_reason(self):
        self.store.create_screening_run("claim-clear-block", source_count=1)
        self.store.update_screening_run("claim-clear-block", status="running")
        self.store.update_screening_run(
            "claim-clear-block", status="paused",
            error_code="ai_rate_limited", error_reason="限流",
        )
        self.assertTrue(self.store.claim_paused_screening_run("claim-clear-block"))
        run = self.store.get_screening_run("claim-clear-block")
        self.assertEqual(run["status"], "running")
        self.assertIsNone(run["error_code"])
        self.assertIsNone(run["error_reason"])

    def test_create_screening_run_marks_process_log(self):
        """工作日记（process_log）：create_screening_run 写入的 run 必须标 process_log。"""
        self.store.create_screening_run("sr-pl", source_count=10)
        run = self.store.get_screening_run("sr-pl")
        self.assertEqual(run["record_kind"], "process_log")

    def test_save_pipeline_result_marks_result_snapshot(self):
        """结果存档（result_snapshot）：save_pipeline_result 写入的 run 必须标 result_snapshot。"""
        result = {
            "ok": True,
            "jobs": [{"job_id": "j1", "verdict": "match", "title": "AI工程师"}],
            "dropped": [],
            "total_scraped": 1,
            "total_kept": 1,
            "total_matched": 1,
            "total_dropped": 0,
            "profile_summary": "画像",
        }
        run_id = self.store.save_pipeline_result(
            result, {"screening": {}},
            started_at=1_700_000_000_000,
            finished_at=1_700_000_100_000,
            execution_config={
                "screen_batch_size": 50, "match_batch_size": 10,
            },
        )
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["record_kind"], "result_snapshot")
        self.assertEqual(run["started_at"], "2023-11-15T06:13:20+08:00")
        self.assertEqual(run["finished_at"], "2023-11-15T06:15:00+08:00")
        loaded = self.store.load_latest_pipeline_result(run_id)
        self.assertEqual(loaded["execution_config"]["screen_batch_size"], 50)
        self.assertEqual(loaded["execution_config"]["match_batch_size"], 10)

    def test_save_pipeline_result_with_pending_jobs_marks_partial(self):
        result = {
            "jobs": [
                {"job_id": "p1", "verdict": "uncertain", "verdict_reason": "待确认"},
            ],
            "dropped": [], "total_scraped": 1, "total_kept": 1,
            "total_matched": 0, "total_dropped": 0,
        }
        run_id = self.store.save_pipeline_result(result, {})
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["status"], "partial")
        loaded = self.store.load_latest_pipeline_result(run_id)
        self.assertEqual(loaded["status"], "completed_with_pending")
        self.assertEqual(self.store.get_latest_done_run_id(), run_id)

    def test_clear_latest_pipeline_result_clears_partial_snapshot(self):
        result = {
            "jobs": [
                {"job_id": "p1", "verdict": "uncertain", "verdict_reason": "待确认"},
            ],
            "dropped": [], "total_scraped": 1, "total_kept": 1,
            "total_matched": 0, "total_dropped": 0,
        }
        run_id = self.store.save_pipeline_result(result, {})
        self.assertEqual(self.store.get_screening_run(run_id)["status"], "partial")
        self.assertTrue(self.store.clear_latest_pipeline_result())
        self.assertIsNone(self.store.load_latest_pipeline_result())
        self.assertIsNone(self.store.get_latest_done_run_id())

    def test_load_latest_pipeline_result_skips_process_log(self):
        """load_latest_pipeline_result 只能返回 result_snapshot，跳过 process_log。"""
        # 先写一条 process_log（create_screening_run）
        self.store.create_screening_run("sr-pl2", source_count=5)
        self.store.update_screening_run("sr-pl2", status="done", match_count=0,
                                        mismatch_count=5)
        # 再写一条 result_snapshot（save_pipeline_result，时间戳更晚）
        result = {
            "ok": True,
            "jobs": [{"job_id": "j2", "verdict": "match", "title": "AI工程师"}],
            "dropped": [],
            "total_scraped": 1,
            "total_kept": 1,
            "total_matched": 1,
            "total_dropped": 0,
            "profile_summary": "画像",
        }
        self.store.save_pipeline_result(result, {"screening": {}})

        loaded = self.store.load_latest_pipeline_result()
        self.assertIsNotNone(loaded)
        # 加载到的必须是 result_snapshot（有 jobs 字段且非空），不是 process_log
        self.assertEqual(len(loaded["result"]["jobs"]), 1)
        self.assertEqual(loaded["result"]["jobs"][0]["platform_job_id"], "j2")
        self.assertIsNone(loaded["result"]["jobs"][0]["job_id"])


    def test_clear_latest_pipeline_result_removes_only_latest_snapshot(self):
        """重新上传简历时只清理最新结果存档，保留更早的 result_snapshot。"""
        older = {
            "ok": True,
            "jobs": [{"job_id": "old", "verdict": "match", "title": "旧结果"}],
            "dropped": [],
            "total_scraped": 1,
            "total_kept": 1,
            "total_matched": 1,
            "total_dropped": 0,
            "profile_summary": "画像1",
        }
        newer = {
            "ok": True,
            "jobs": [{"job_id": "new", "verdict": "match", "title": "新结果"}],
            "dropped": [],
            "total_scraped": 1,
            "total_kept": 1,
            "total_matched": 1,
            "total_dropped": 0,
            "profile_summary": "画像2",
        }
        older_id = self.store.save_pipeline_result(older, {"screening": {}})
        newer_id = self.store.save_pipeline_result(newer, {"screening": {}})
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET created_at = ? WHERE id = ?",
                ("2026-01-01T00:00:00+08:00", older_id),
            )
            conn.execute(
                "UPDATE screening_runs SET created_at = ? WHERE id = ?",
                ("2026-01-02T00:00:00+08:00", newer_id),
            )

        self.assertTrue(self.store.clear_latest_pipeline_result())
        loaded = self.store.load_latest_pipeline_result()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["run_id"], older_id)
        self.assertEqual(loaded["result"]["jobs"][0]["platform_job_id"], "old")
        self.assertIsNone(loaded["result"]["jobs"][0]["job_id"])
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_results WHERE run_id = ?", (newer_id,),
            ).fetchone()
            self.assertEqual(rows["n"], 0)

        self.assertTrue(self.store.clear_latest_pipeline_result())
        self.assertIsNone(self.store.load_latest_pipeline_result())
        self.assertFalse(self.store.clear_latest_pipeline_result())


class AdvancedConfigStateStoreTests(unittest.TestCase):
    """SPEC011 T007: advanced_config_state + mode_config_versions 持久化。

    RED 测试：在 T008 完成前应失败，因为 store.py 尚未实现这些表和方法。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _valid_matrix():
        slot = {
            "inter_combo_delay": 10.0, "detail_batch_size": 15,
            "detail_interval": 2.0, "detail_reset_every": 4,
            "detail_batch_cooldown": 5.0, "screen_batch_size": 50,
            "screen_concurrency": 5, "match_batch_size": 4,
            "match_concurrency": 10,
        }
        return {
            mode: {size: dict(slot) for size in ("small", "medium", "large")}
            for mode in ("stable", "balanced", "extreme")
        }

    def test_migration_creates_advanced_config_state_table(self):
        """迁移后 advanced_config_state 表存在且为单例。"""
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='advanced_config_state'"
            ).fetchone()
        self.assertIsNotNone(row, "advanced_config_state 表必须存在")

    def test_migration_creates_mode_config_versions_table(self):
        """迁移后 mode_config_versions 表存在。"""
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='mode_config_versions'"
            ).fetchone()
        self.assertIsNotNone(row, "mode_config_versions 表必须存在")

    def test_get_advanced_config_state_returns_defaults(self):
        """无任何保存时返回默认状态：selection=custom, 无活跃版本。"""
        state = self.store.get_advanced_config_state()
        self.assertIn(state["active_selection"], ("custom", "stable", "balanced", "extreme"))
        self.assertIn("last_custom_config", state)
        self.assertIn("active_mode_version_id", state)

    def test_save_custom_config_stores_complete_config(self):
        """保存自定义配置：完整速度字段 + digest，原子替换。"""
        config = {
            "inter_combo_delay": 10.0,
            "detail_batch_size": 15,
            "detail_interval": 2.0,
            "detail_reset_every": 4,
            "detail_batch_cooldown": 5.0,
            "detail_tab_pool_size": 5,
            "screen_batch_size": 50,
            "screen_concurrency": 5,
            "match_batch_size": 4,
            "match_concurrency": 10,
        }
        self.store.save_custom_config(config)
        state = self.store.get_advanced_config_state()
        self.assertEqual(state["active_selection"], "custom")
        self.assertIsNotNone(state["last_custom_config"])
        self.assertEqual(state["last_custom_config"]["detail_batch_size"], 15)
        self.assertEqual(state["last_custom_config"]["detail_tab_pool_size"], 5)
        self.assertIsNotNone(state["last_custom_digest"])

    def test_save_custom_config_rejects_partial_patch(self):
        """部分字段保存被拒绝。"""
        with self.assertRaises((ValueError, TypeError)):
            self.store.save_custom_config({"inter_combo_delay": 10.0})

    def test_select_mode_stable(self):
        """选择 stable 模式：载入对应配置，selection=stable。"""
        result = self.store.select_mode("stable", task_size="small")
        self.assertEqual(result["selection"], "stable")
        self.assertIsNotNone(result["config"])
        self.assertIn("inter_combo_delay", result["config"])

    def test_select_mode_balanced(self):
        """选择 balanced 模式。"""
        result = self.store.select_mode("balanced", task_size="medium")
        self.assertEqual(result["selection"], "balanced")

    def test_select_mode_extreme(self):
        """选择 extreme 模式。"""
        result = self.store.select_mode("extreme", task_size="large")
        self.assertEqual(result["selection"], "extreme")

    def test_select_mode_rejects_unknown(self):
        """未知模式被拒绝。"""
        with self.assertRaises(ValueError):
            self.store.select_mode("turbo", task_size="small")

    def test_select_mode_does_not_change_pages(self):
        """FR-009: 模式选择不改变 pages。"""
        result = self.store.select_mode("stable", task_size="small")
        self.assertNotIn("pages", result["config"])

    def test_select_mode_uses_active_mode_version_matrix(self):
        slot = {
            "inter_combo_delay": 77.0,
            "detail_batch_size": 7,
            "detail_interval": 3.0,
            "detail_reset_every": 2,
            "detail_batch_cooldown": 8.0,
            "screen_batch_size": 25,
            "screen_concurrency": 3,
            "match_batch_size": 2,
            "match_concurrency": 4,
        }
        matrix = {
            mode: {size: dict(slot) for size in ("small", "medium", "large")}
            for mode in ("stable", "balanced", "extreme")
        }
        version_id = self.store.create_mode_version(matrix=matrix, manual_ranges={})
        self.store.apply_mode_version(version_id)

        result = self.store.select_mode("stable", task_size="small")

        self.assertEqual(result["config"]["inter_combo_delay"], 77.0)
        self.assertEqual(result["mode_version_id"], version_id)

    def test_select_custom_updates_active_selection(self):
        config = {
            "inter_combo_delay": 42.0,
            "detail_batch_size": 7,
            "detail_interval": 3.0,
            "detail_reset_every": 2,
            "detail_batch_cooldown": 8.0,
            "screen_batch_size": 25,
            "screen_concurrency": 3,
            "match_batch_size": 2,
            "match_concurrency": 4,
        }
        self.store.save_custom_config(config)
        self.store.select_mode("stable", task_size="small")
        self.store.select_mode("custom", task_size="small")
        self.assertEqual(
            self.store.get_advanced_config_state()["active_selection"], "custom"
        )

    def test_apply_mode_version_atomic(self):
        """应用模式版本：整体替换，旧的被 superseded。"""
        # 先创建一个候选版本
        version_id = self.store.create_mode_version(
            matrix=self._valid_matrix(),
            manual_ranges={},
        )
        self.store.apply_mode_version(version_id)
        state = self.store.get_advanced_config_state()
        self.assertEqual(state["active_mode_version_id"], version_id)

    def test_rollback_mode_version(self):
        """回退到上一版本：整体恢复。"""
        v1 = self.store.create_mode_version(matrix=self._valid_matrix(), manual_ranges={})
        self.store.apply_mode_version(v1)
        v2 = self.store.create_mode_version(matrix=self._valid_matrix(), manual_ranges={})
        self.store.apply_mode_version(v2)
        # 回退到 v1
        self.store.rollback_mode_version(v1)
        state = self.store.get_advanced_config_state()
        self.assertEqual(state["active_mode_version_id"], v1)

    def test_apply_mode_version_does_not_overwrite_custom(self):
        """FR-066: 应用模式版本不覆盖自定义配置。"""
        custom_config = {
            "inter_combo_delay": 42.0,
            "detail_batch_size": 7,
            "detail_interval": 3.0,
            "detail_reset_every": 2,
            "detail_batch_cooldown": 8.0,
            "screen_batch_size": 25,
            "screen_concurrency": 3,
            "match_batch_size": 2,
            "match_concurrency": 4,
        }
        self.store.save_custom_config(custom_config)
        version_id = self.store.create_mode_version(
            matrix=self._valid_matrix(), manual_ranges={})
        self.store.apply_mode_version(version_id)
        state = self.store.get_advanced_config_state()
        # 自定义配置仍在
        self.assertEqual(state["last_custom_config"]["inter_combo_delay"], 42.0)

    def test_legacy_json_import_one_time(self):
        """旧 advanced_settings.json 一次性导入。"""
        # 写一个旧 JSON 文件
        import json
        import os
        legacy_path = pathlib.Path(self.temp.name) / "advanced_settings.json"
        legacy_config = {
            "pages": 2,
            "inter_combo_delay": 12.0,
            "detail_batch_size": 8,
            "detail_interval": 3.0,
            "detail_reset_every": 3,
            "detail_batch_cooldown": 6.0,
            "screen_batch_size": 30,
            "screen_concurrency": 3,
            "match_batch_size": 3,
            "match_concurrency": 5,
        }
        legacy_path.write_text(json.dumps(legacy_config), encoding="utf-8")
        # 执行导入
        self.store.import_legacy_advanced_settings(legacy_path)
        state = self.store.get_advanced_config_state()
        self.assertIsNotNone(state["last_custom_config"])
        self.assertEqual(state["last_custom_config"]["inter_combo_delay"], 12.0)
        # pages 不应被导入到配置快照
        self.assertNotIn("pages", state["last_custom_config"])
        # 再次导入不应覆盖（一次性）
        legacy_config["inter_combo_delay"] = 99.0
        legacy_path.write_text(json.dumps(legacy_config), encoding="utf-8")
        self.store.import_legacy_advanced_settings(legacy_path)
        state2 = self.store.get_advanced_config_state()
        self.assertEqual(state2["last_custom_config"]["inter_combo_delay"], 12.0,
                         "一次性导入：第二次不应覆盖")


class ExperimentConfigIsolationStoreTests(unittest.TestCase):
    """T010 RED: store 层证明实验临时候选配置永不覆盖 advanced_config_state。

    覆盖 FR-042、SC-014、FR-066。
    这些测试在 T012 实现实验表族方法前应失败。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        # 预置用户正式配置
        self.user_custom = {
            "inter_combo_delay": 42.0,
            "detail_batch_size": 7,
            "detail_interval": 3.0,
            "detail_reset_every": 2,
            "detail_batch_cooldown": 8.0,
            "screen_batch_size": 25,
            "screen_concurrency": 3,
            "match_batch_size": 2,
            "match_concurrency": 4,
        }
        self.store.save_custom_config(self.user_custom)
        self.store.select_mode("stable", task_size="small")
        self.baseline = self.store.get_advanced_config_state()

    def tearDown(self):
        self.temp.cleanup()

    def _assert_user_state_unchanged(self, msg: str = ""):
        current = self.store.get_advanced_config_state()
        self.assertEqual(current["active_selection"], self.baseline["active_selection"],
                         f"active_selection 被修改 {msg}")
        self.assertEqual(current["last_custom_config"], self.baseline["last_custom_config"],
                         f"last_custom_config 被修改 {msg}")
        self.assertEqual(current["last_custom_digest"], self.baseline["last_custom_digest"],
                         f"last_custom_digest 被修改 {msg}")
        self.assertEqual(current["active_mode_version_id"], self.baseline["active_mode_version_id"],
                         f"active_mode_version_id 被修改 {msg}")

    def test_create_tuning_experiment_does_not_touch_user_config(self):
        """FR-042: 创建实验记录不修改 advanced_config_state。"""
        experiment = self.store.create_tuning_experiment(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
        )
        self.assertIsNotNone(experiment["id"])
        self._assert_user_state_unchanged("after create_tuning_experiment")

    def test_save_tuning_candidate_does_not_touch_user_config(self):
        """FR-042: 保存候选配置到实验表不修改 advanced_config_state。"""
        experiment = self.store.create_tuning_experiment(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
        )
        candidate_config = {
            "inter_combo_delay": 1.0,
            "detail_batch_size": 100,
            "detail_interval": 0.5,
            "detail_reset_every": 1,
            "detail_batch_cooldown": 1.0,
            "screen_batch_size": 200,
            "screen_concurrency": 20,
            "match_batch_size": 50,
            "match_concurrency": 30,
        }
        candidate = self.store.save_tuning_candidate(
            experiment_id=experiment["id"],
            stage="list",
            strategy_step="single_field",
            config=candidate_config,
        )
        self.assertIsNotNone(candidate["id"])
        self._assert_user_state_unchanged("after save_tuning_candidate")

    def test_cancel_tuning_experiment_does_not_touch_user_config(self):
        """SC-014: 取消实验不修改 advanced_config_state。"""
        experiment = self.store.create_tuning_experiment(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
        )
        self.store.update_tuning_experiment_status(
            experiment["id"], status="cancelled",
        )
        self._assert_user_state_unchanged("after cancel")

    def test_fail_tuning_experiment_does_not_touch_user_config(self):
        """SC-014: 实验失败不修改 advanced_config_state。"""
        experiment = self.store.create_tuning_experiment(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
        )
        # 按合法路径到达 running，再转 failed
        self.store.update_tuning_experiment_status(experiment["id"], status="preflight")
        self.store.update_tuning_experiment_status(experiment["id"], status="awaiting_instruction")
        self.store.update_tuning_experiment_status(experiment["id"], status="queued")
        self.store.update_tuning_experiment_status(experiment["id"], status="running")
        self.store.update_tuning_experiment_status(
            experiment["id"], status="failed", blocked_code="hard_error",
        )
        self._assert_user_state_unchanged("after fail")

    def test_recover_tuning_experiment_does_not_touch_user_config(self):
        """SC-014: 重启恢复不修改 advanced_config_state。"""
        experiment = self.store.create_tuning_experiment(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
        )
        # 模拟重启后的恢复操作
        self.store.reconcile_tuning_after_restart()
        self._assert_user_state_unchanged("after reconcile")

    def test_apply_mode_version_preserves_custom_config(self):
        """FR-066: 应用模式版本不覆盖最近自定义配置。"""
        _slot = {
            "inter_combo_delay": 20.0, "detail_batch_size": 10,
            "detail_interval": 2.0, "detail_reset_every": 3,
            "detail_batch_cooldown": 5.0, "screen_batch_size": 30,
            "screen_concurrency": 3, "match_batch_size": 3, "match_concurrency": 5,
        }
        matrix = {
            mode: {size: dict(_slot) for size in ("small", "medium", "large")}
            for mode in ("stable", "balanced", "extreme")
        }
        version_id = self.store.create_mode_version(
            matrix=matrix, manual_ranges={},
        )
        self.store.apply_mode_version(version_id)
        # 最近自定义配置必须保持不变
        current = self.store.get_advanced_config_state()
        self.assertEqual(current["last_custom_config"], self.baseline["last_custom_config"],
                         "apply_mode_version 覆盖了最近自定义配置")
        self.assertEqual(current["last_custom_digest"], self.baseline["last_custom_digest"],
                         "apply_mode_version 覆盖了最近自定义摘要")

    def test_rollback_mode_version_preserves_custom_config(self):
        """FR-066: 回退模式版本不覆盖最近自定义配置。"""
        _slot1 = {
            "inter_combo_delay": 20.0, "detail_batch_size": 10,
            "detail_interval": 2.0, "detail_reset_every": 3,
            "detail_batch_cooldown": 5.0, "screen_batch_size": 30,
            "screen_concurrency": 3, "match_batch_size": 3, "match_concurrency": 5,
        }
        _slot2 = {
            "inter_combo_delay": 25.0, "detail_batch_size": 12,
            "detail_interval": 2.5, "detail_reset_every": 4,
            "detail_batch_cooldown": 6.0, "screen_batch_size": 35,
            "screen_concurrency": 4, "match_batch_size": 4, "match_concurrency": 6,
        }
        matrix1 = {
            mode: {size: dict(_slot1) for size in ("small", "medium", "large")}
            for mode in ("stable", "balanced", "extreme")
        }
        matrix2 = {
            mode: {size: dict(_slot2) for size in ("small", "medium", "large")}
            for mode in ("stable", "balanced", "extreme")
        }
        v1 = self.store.create_mode_version(matrix=matrix1, manual_ranges={})
        self.store.apply_mode_version(v1)
        v2 = self.store.create_mode_version(matrix=matrix2, manual_ranges={})
        self.store.apply_mode_version(v2)
        # 回退到 v1
        self.store.rollback_mode_version(v1)
        # 最近自定义配置必须保持不变
        current = self.store.get_advanced_config_state()
        self.assertEqual(current["last_custom_config"], self.baseline["last_custom_config"],
                         "rollback_mode_version 覆盖了最近自定义配置")
        self.assertEqual(current["last_custom_digest"], self.baseline["last_custom_digest"],
                         "rollback_mode_version 覆盖了最近自定义摘要")


class TuningEntitiesMigrationTests(unittest.TestCase):
    """T011 RED: 实验表族迁移与实体持久化测试。

    覆盖 data-model.md 第 2 节全部持久化实体和第 6 节不变量。
    这些测试在 T012 实现表族迁移前应失败。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def _expected_tuning_tables(self) -> set:
        return {
            "tuning_experiments",
            "tuning_input_versions",
            "tuning_workloads",
            "tuning_quality_references",
            "tuning_candidates",
            "tuning_rounds",
            "tuning_task_manifests",
            "tuning_executor_reports",
            "tuning_measurement_events",
            "tuning_execution_lease",
        }

    def test_migration_creates_all_tuning_tables(self):
        """迁移后 data-model.md 定义的全部表存在。"""
        expected = self._expected_tuning_tables()
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            tables = {row["name"] for row in rows}
        missing = expected - tables
        self.assertEqual(missing, set(), f"缺少实验表: {missing}")

    def test_execution_lease_is_singleton(self):
        """tuning_execution_lease 是单例行 (id=1)。"""
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM tuning_execution_lease WHERE id = 1"
            ).fetchone()
        self.assertEqual(row["c"], 1, "execution_lease 必须有且仅有 id=1 的单例行")


class TuningExperimentStateTests(unittest.TestCase):
    """T011 RED: 实验状态机合法转换测试。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def _create_experiment(self) -> dict:
        return self.store.create_tuning_experiment(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
        )

    def test_experiment_starts_as_draft(self):
        """新实验状态为 draft。"""
        exp = self._create_experiment()
        fetched = self.store.get_tuning_experiment(exp["id"])
        self.assertEqual(fetched["status"], "draft")

    def test_draft_to_preflight_transition(self):
        """draft → preflight 合法。"""
        exp = self._create_experiment()
        self.store.update_tuning_experiment_status(exp["id"], status="preflight")
        self.assertEqual(
            self.store.get_tuning_experiment(exp["id"])["status"], "preflight"
        )

    def test_terminal_states_reject_resume(self):
        """cancelled/failed/completed 为终态，不能再转 running。"""
        exp = self._create_experiment()
        self.store.update_tuning_experiment_status(exp["id"], status="cancelled")
        with self.assertRaises(ValueError):
            self.store.update_tuning_experiment_status(exp["id"], status="running")

    def test_running_cannot_skip_evaluation_and_complete(self):
        """state-machine.md: completed 只能从 evaluating 且通过最终门禁进入。"""
        exp = self._create_experiment()
        for status in ("preflight", "awaiting_instruction", "queued", "running"):
            self.store.update_tuning_experiment_status(exp["id"], status=status)
        with self.assertRaises(ValueError):
            self.store.update_tuning_experiment_status(exp["id"], status="completed")


class TuningLeaseTests(unittest.TestCase):
    """T011 RED: 独占租约 claim/heartbeat/release 测试。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_claim_lease_succeeds_when_free(self):
        """空闲租约可被 claim。"""
        result = self.store.claim_tuning_lease(
            experiment_id="exp-1", round_id="round-1", owner_token="token-abc",
        )
        self.assertTrue(result["ok"])
        lease = self.store.get_tuning_lease()
        self.assertEqual(lease["owner_experiment_id"], "exp-1")

    def test_claim_lease_fails_when_held(self):
        """租约被持有时第二个 claim 失败（SC-004）。"""
        self.store.claim_tuning_lease(
            experiment_id="exp-1", round_id="round-1", owner_token="token-abc",
        )
        result = self.store.claim_tuning_lease(
            experiment_id="exp-2", round_id="round-2", owner_token="token-def",
        )
        self.assertFalse(result["ok"])

    def test_release_lease_allows_reclaim(self):
        """释放后可重新 claim。"""
        self.store.claim_tuning_lease(
            experiment_id="exp-1", round_id="round-1", owner_token="token-abc",
        )
        self.store.release_tuning_lease(owner_token="token-abc")
        result = self.store.claim_tuning_lease(
            experiment_id="exp-2", round_id="round-2", owner_token="token-def",
        )
        self.assertTrue(result["ok"])

    def test_heartbeat_extends_lease(self):
        """heartbeat 延长租约。"""
        self.store.claim_tuning_lease(
            experiment_id="exp-1", round_id="round-1", owner_token="token-abc",
        )
        self.store.heartbeat_tuning_lease(owner_token="token-abc")
        lease = self.store.get_tuning_lease()
        self.assertIsNotNone(lease["heartbeat_at"])

    def test_stale_lease_can_be_taken_over(self):
        """过期租约可被接管（重启恢复）。"""
        self.store.claim_tuning_lease(
            experiment_id="exp-1", round_id="round-1", owner_token="token-abc",
        )
        # 模拟过期：直接更新 lease_until 为过去时间
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE tuning_execution_lease SET lease_until = ? WHERE id = 1",
                ("2020-01-01T00:00:00Z",),
            )
        # 接管
        result = self.store.claim_tuning_lease(
            experiment_id="exp-2", round_id="round-2", owner_token="token-def",
            allow_stale_takeover=True,
        )
        self.assertTrue(result["ok"])


class TuningRoundStateTests(unittest.TestCase):
    """T011 RED: 轮次状态机与 uncertain 恢复测试。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        self.experiment = self.store.create_tuning_experiment(
            spec_version="011-deep-configuration-probing",
            source_scope={
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_confirmed_round_is_not_reexecuted(self):
        """SC-005: 已确认轮次不重复执行。"""
        candidate = self.store.save_tuning_candidate(
            experiment_id=self.experiment["id"],
            stage="list",
            strategy_step="single_field",
            config={
                "inter_combo_delay": 10.0, "detail_batch_size": 15,
                "detail_interval": 2.0, "detail_reset_every": 4,
                "detail_batch_cooldown": 5.0, "screen_batch_size": 50,
                "screen_concurrency": 5, "match_batch_size": 4,
                "match_concurrency": 10,
            },
        )
        round_rec = self.store.create_tuning_round(
            experiment_id=self.experiment["id"],
            candidate_id=candidate["id"],
            workload_id="wl-1",
            round_kind="list",
            repetition_index=1,
        )
        self.store.update_tuning_round_status(round_rec["id"], status="issued")
        self.store.claim_tuning_lease(
            experiment_id=self.experiment["id"], round_id=round_rec["id"],
            owner_token="round-test-owner",
        )
        self.store.update_tuning_round_status(round_rec["id"], status="running")
        self.store.update_tuning_round_status(round_rec["id"], status="reported")
        self.store.update_tuning_round_status(round_rec["id"], status="confirmed")
        # 重启恢复后，confirmed 轮次保持 confirmed
        self.store.reconcile_tuning_after_restart()
        fetched = self.store.get_tuning_round(round_rec["id"])
        self.assertEqual(fetched["status"], "confirmed")

    def test_round_cannot_skip_issued_running_and_reported_states(self):
        candidate = self.store.save_tuning_candidate(
            experiment_id=self.experiment["id"],
            stage="list", strategy_step="single_field",
            config={
                "inter_combo_delay": 10.0, "detail_batch_size": 15,
                "detail_interval": 2.0, "detail_reset_every": 4,
                "detail_batch_cooldown": 5.0, "screen_batch_size": 50,
                "screen_concurrency": 5, "match_batch_size": 4,
                "match_concurrency": 10,
            },
        )
        round_rec = self.store.create_tuning_round(
            experiment_id=self.experiment["id"], candidate_id=candidate["id"],
            workload_id="wl-guard", round_kind="list", repetition_index=1,
        )
        for status in ("running", "reported", "confirmed"):
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.store.update_tuning_round_status(round_rec["id"], status=status)

    def test_running_round_becomes_uncertain_on_restart(self):
        """SC-005: 重启时 running 轮次变为 uncertain。"""
        candidate = self.store.save_tuning_candidate(
            experiment_id=self.experiment["id"],
            stage="list",
            strategy_step="single_field",
            config={
                "inter_combo_delay": 10.0, "detail_batch_size": 15,
                "detail_interval": 2.0, "detail_reset_every": 4,
                "detail_batch_cooldown": 5.0, "screen_batch_size": 50,
                "screen_concurrency": 5, "match_batch_size": 4,
                "match_concurrency": 10,
            },
        )
        round_rec = self.store.create_tuning_round(
            experiment_id=self.experiment["id"],
            candidate_id=candidate["id"],
            workload_id="wl-1",
            round_kind="list",
            repetition_index=1,
        )
        self.store.update_tuning_round_status(round_rec["id"], status="issued")
        self.store.claim_tuning_lease(
            experiment_id=self.experiment["id"], round_id=round_rec["id"],
            owner_token="restart-test-owner",
        )
        self.store.update_tuning_round_status(round_rec["id"], status="running")
        for status in ("preflight", "awaiting_instruction", "queued", "running"):
            self.store.update_tuning_experiment_status(
                self.experiment["id"], status=status,
            )
        # 重启恢复
        self.store.reconcile_tuning_after_restart()
        fetched = self.store.get_tuning_round(round_rec["id"])
        self.assertEqual(fetched["status"], "uncertain")
        experiment = self.store.get_tuning_experiment(self.experiment["id"])
        self.assertEqual(experiment["status"], "blocked")
        self.assertEqual(experiment["blocked_code"], "restart_interrupted_round")
        self.assertIn(round_rec["id"], experiment["blocked_reason"])
        self.assertIsNone(self.store.get_tuning_lease()["owner_experiment_id"])


class TuningInvariantTests(unittest.TestCase):
    """T011 RED: data-model.md 第 6 节不变量测试。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_one_active_mode_version_at_most(self):
        """不变量：同一时刻最多一个 active 模式版本。"""
        _slot = {
            "inter_combo_delay": 20.0, "detail_batch_size": 10,
            "detail_interval": 2.0, "detail_reset_every": 3,
            "detail_batch_cooldown": 5.0, "screen_batch_size": 30,
            "screen_concurrency": 3, "match_batch_size": 3, "match_concurrency": 5,
        }
        matrix = {
            mode: {size: dict(_slot) for size in ("small", "medium", "large")}
            for mode in ("stable", "balanced", "extreme")
        }
        v1 = self.store.create_mode_version(matrix=matrix, manual_ranges={})
        self.store.apply_mode_version(v1)
        v2 = self.store.create_mode_version(matrix=matrix, manual_ranges={})
        self.store.apply_mode_version(v2)
        # 只能有一个 active
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM mode_config_versions WHERE status = 'active'"
            ).fetchone()
        self.assertEqual(row["c"], 1, "同一时刻只能有一个 active 模式版本")

    def test_no_partial_mode_matrix_becomes_active(self):
        """不变量：不完整的九槽位矩阵不能成为 active。"""
        # 只有一个模式、一个规模的残缺矩阵
        partial_matrix = {
            "stable": {"small": {
                "inter_combo_delay": 20.0, "detail_batch_size": 10,
                "detail_interval": 2.0, "detail_reset_every": 3,
                "detail_batch_cooldown": 5.0, "screen_batch_size": 30,
                "screen_concurrency": 3, "match_batch_size": 3, "match_concurrency": 5,
            }},
            # 缺 balanced 和 extreme
        }
        with self.assertRaises(ValueError):
            self.store.create_mode_version(matrix=partial_matrix, manual_ranges={})
