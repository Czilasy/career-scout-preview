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
        self.assertEqual(run["status"], "done")
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


