import hashlib
import json
import pathlib
import sqlite3
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from webui.store import TaskStore, _now


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
        with patch.object(TaskStore, "_migration_028", return_value=None), \
                patch.object(TaskStore, "_migration_029", return_value=None), \
                patch.object(TaskStore, "_migration_030", return_value=None), \
                patch.object(TaskStore, "_migration_031", return_value=None), \
                patch.object(TaskStore, "_migration_032", return_value=None):
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
        self.assertGreaterEqual(reopened.schema_version(), 28)
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

        self.assertGreaterEqual(store.schema_version(), 28)
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

    def test_migration_031_adds_profile_facts_and_flags_columns(self):
        """B033：screening_runs.profile_facts_json + screening_results.flags_json。"""
        store = TaskStore(self.db_path)
        with store._connection() as conn:
            run_columns = {
                row["name"] for row in conn.execute(
                    "PRAGMA table_info(screening_runs)"
                ).fetchall()
            }
            result_columns = {
                row["name"] for row in conn.execute(
                    "PRAGMA table_info(screening_results)"
                ).fetchall()
            }
        self.assertIn("profile_facts_json", run_columns)
        self.assertIn("flags_json", result_columns)
        self.assertGreaterEqual(store.schema_version(), 31)

    def test_save_pipeline_result_persists_facts_and_flags(self):
        """新轮次：画像事实写入 screening_runs、flags 写入 screening_results，读回一致。"""
        store = TaskStore(self.db_path)
        run_id = store.save_pipeline_result(
            {
                "jobs": [{
                    "platform_job_id": "p1",
                    "title": "后端开发",
                    "verdict": "match",
                    "verdict_reason": "合适",
                    "caveats": ["优先英语"],
                    "flags": [{"code": "B1", "level": "medium", "reason": "标题含无责底薪"}],
                }],
                "dropped": [],
                "total_scraped": 1,
                "total_kept": 1,
                "total_dropped": 0,
                "profile_summary": "3年Python后端",
                "profile_facts": {
                    "core_skills": ["Python"],
                    "job_type": "全职",
                },
            },
            {"screening": {}, "platform": "boss"},
        )
        payload = store.load_latest_pipeline_result(run_id)
        self.assertIsNotNone(payload)
        self.assertEqual(
            (payload or {}).get("result", {}).get("profile_facts"),
            {"core_skills": ["Python"], "job_type": "全职"},
            "B033：结果快照读取必须透传画像事实（刷新恢复、补筛复用快照的读取源）",
        )
        run = store.get_screening_run(run_id)
        self.assertEqual(
            run["profile_facts"],
            {"core_skills": ["Python"], "job_type": "全职"},
        )
        jobs = (payload or {}).get("result", {}).get("jobs", [])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["flags"], [{
            "code": "B1", "level": "medium", "reason": "标题含无责底薪"}])

    def test_legacy_rows_without_flags_read_back_empty(self):
        """老轮次 flags_json 为 NULL：读回 flags=[]，无回归。"""
        store = TaskStore(self.db_path)
        run_id = store.save_pipeline_result(
            {
                "jobs": [{
                    "platform_job_id": "p1",
                    "title": "后端开发",
                    "verdict": "match",
                    "verdict_reason": "合适",
                    "caveats": [],
                }],
                "dropped": [],
                "total_scraped": 1,
                "total_kept": 1,
                "total_dropped": 0,
                "profile_summary": "画像",
            },
            {"screening": {}, "platform": "boss"},
        )
        # 模拟老轮：清空两列（老数据为 NULL）
        with store._connection() as conn:
            conn.execute("UPDATE screening_runs SET profile_facts_json = NULL WHERE id = ?", (run_id,))
            conn.execute("UPDATE screening_results SET flags_json = NULL")
        payload = store.load_latest_pipeline_result(run_id)
        jobs = (payload or {}).get("result", {}).get("jobs", [])
        self.assertEqual(jobs[0]["flags"], [])
        self.assertIsNone(store.get_screening_run(run_id)["profile_facts"])

    def test_save_screening_verdicts_persists_flags(self):
        """每批精筛落盘：flags_json 与 caveats 同路径写入。"""
        store = TaskStore(self.db_path)
        run_id = store.create_screening_run("run-x")["id"]
        store.save_screening_verdicts(run_id, {
            "job-1": {
                "verdict": "not_match",
                "reason": "疑似骗局：要求先交培训费",
                "caveats": [],
                "flags": [{"code": "C1", "level": "high", "reason": "要求先交培训费"}],
            }
        })
        verdicts = store.load_screening_verdicts(run_id)
        self.assertEqual(verdicts["job-1"]["verdict"], "not_match")
        self.assertEqual(verdicts["job-1"]["flags"], [{
            "code": "C1", "level": "high", "reason": "要求先交培训费"}])


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

        repo_root = pathlib.Path(__file__).resolve().parent.parent.parent
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
        """T705: migration 27 中途失败必须阻断版本写入。

        SQLite ALTER TABLE ADD COLUMN 不可回滚（DDL 非事务性），但
        schema_migrations 版本记录必须在失败时不写入 27，确保下次构造
        会重试 migration。守恒检查（外键/重复身份/URL 唯一）也必须
        阻断版本推进。
        """
        import os
        v26_path = os.environ.get("CAREER_SCOUT_V26_BACKUP", "")
        if not v26_path:
            self.skipTest("未设置 CAREER_SCOUT_V26_BACKUP 环境变量，跳过 migration 27 回滚测试")
        v26_src = pathlib.Path(v26_path)
        if not v26_src.exists():
            self.skipTest("v26 备份库不存在，跳过 migration 27 回滚测试")
        import shutil
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(v26_src, self.db_path)

        # 确认 v26 库版本和列结构
        with sqlite3.connect(self.db_path) as conn:
            pre_version = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
        self.assertEqual(pre_version, 26, "备份库必须是 v26")

        # 注入失败：第一次 _add_column_if_missing 成功后立即抛错
        original_add_column = TaskStore._add_column_if_missing

        def fail_after_first_alter(conn, table, column, definition):
            original_add_column(conn, table, column, definition)
            raise RuntimeError("injected migration 27 failure")

        self._cleanup_shared_backup_dir()

        with patch.object(
            TaskStore, "_add_column_if_missing", side_effect=fail_after_first_alter
        ):
            with self.assertRaisesRegex(RuntimeError, "injected migration 27 failure"):
                TaskStore(self.db_path)

        # 版本记录不得推进到 27（确保下次构造重试）
        with sqlite3.connect(self.db_path) as conn:
            version = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
        self.assertEqual(version, 26, "migration 27 失败后版本必须仍是 26")

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


class Migration032ClearHistoryTests(unittest.TestCase):
    """017-US3: 升级迁移一次性清空存量历史轮（FR-009/SC-005）。

    - 全部 result_snapshot 轮（含子表行）删除；任务行（process_log）与
      任务日志/事件保留；活动任务进度与断点不受影响。
    - recount_pipeline_result 重算时同步刷新 finished_at（定稿时间）。
    """

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

    def _build_v31_with_history(self):
        """构造带存量历史轮 + 活动任务的 v31 库（patch 掉 _migration_032）。"""
        with patch.object(TaskStore, "_migration_032", return_value=None):
            store = TaskStore(self.db_path)
        self.assertEqual(store.schema_version(), 31)
        # 存量历史轮（result_snapshot + 岗位行）
        round_id = store.save_pipeline_result(
            {
                "ok": True,
                "jobs": [{"platform": "boss", "platform_job_id": "j1",
                          "title": "岗位", "verdict": "match"}],
                "dropped": [], "total_scraped": 1, "total_kept": 1,
                "total_matched": 1, "total_dropped": 0,
            },
            {"platform": "boss"},
        )
        store.append_task_event(round_id, "stage_start", {"stage": "ai"})
        # 活动任务（process_log + 岗位 + 事件）
        task_id = "active-task-017"
        store.create_screening_run(
            task_id, source_count=1, execution_params={"platform": "boss"},
        )
        store.save_scrape_combo_result(
            task_id, "k", [{"job_id": "j9", "platform_job_id": "j9",
                            "title": "岗位"}], ["k"],
        )
        store.append_task_event(task_id, "stage_start", {"stage": "scrape"})
        return store, round_id, task_id

    def test_upgrade_clears_history_rounds_keeps_active_tasks(self):
        store, round_id, task_id = self._build_v31_with_history()
        self.assertEqual(len(store.list_history_rounds("boss")), 1)

        reopened = TaskStore(self.db_path)  # 触发 migration 32
        self.assertGreaterEqual(reopened.schema_version(), 32)
        # 存量历史轮全清
        self.assertEqual(reopened.list_history_rounds("boss"), [])
        self.assertFalse(reopened.history_round_exists(round_id))
        # 活动任务与日志保留
        task = reopened.get_screening_run(task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task["record_kind"], "process_log")
        self.assertEqual(len(reopened.list_task_events(task_id)), 1)

    def test_recount_refreshes_finished_at(self):
        store = TaskStore(self.db_path)
        run_id = store.save_pipeline_result(
            {
                "ok": True,
                "jobs": [{"platform": "boss", "platform_job_id": "p1",
                          "title": "岗位", "verdict": "uncertain"}],
                "dropped": [], "total_scraped": 1, "total_kept": 1,
                "total_matched": 0, "total_dropped": 0,
            },
            {"platform": "boss"},
            finished_at="2026-01-01T00:00:00+08:00",
        )
        before = store.get_screening_run(run_id)["finished_at"]
        store.recount_pipeline_result(run_id)
        after = store.get_screening_run(run_id)["finished_at"]
        # 017-US3: 定稿时间随重算刷新（重抓/补筛完成后时间诚实）
        self.assertGreater(
            datetime.fromisoformat(after),
            datetime.fromisoformat(before),
        )
