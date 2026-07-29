import json
import pathlib
import sqlite3
import tempfile
import unittest

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
        self.assertEqual(reopened.latest_interrupted_screening_run()["id"], "sr-3")

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
        run_id = self.store.save_pipeline_result(result, {"screening": {}})
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["record_kind"], "result_snapshot")

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
        self.assertEqual(loaded["result"]["jobs"][0]["job_id"], "j2")


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
        """保存自定义配置：完整九字段 + digest，原子替换。"""
        config = {
            "inter_combo_delay": 10.0,
            "detail_batch_size": 15,
            "detail_interval": 2.0,
            "detail_reset_every": 4,
            "detail_batch_cooldown": 5.0,
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

