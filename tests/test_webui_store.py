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


class ScreeningSchemaMigrationTests(unittest.TestCase):
    """T004: migration 004 adds screening_runs/screening_results, keeps 001 data."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"

    def tearDown(self):
        self.temp.cleanup()

    def _columns(self, store, table):
        with store._connection() as conn:
            return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}

    def test_schema_version_is_at_least_4(self):
        store = TaskStore(self.db_path)
        self.assertGreaterEqual(store.schema_version(), 4)

    def test_screening_runs_table_exists_with_columns(self):
        store = TaskStore(self.db_path)
        cols = self._columns(store, "screening_runs")
        expected = {
            "id", "frozen_filters_json", "status", "source_count",
            "match_count", "mismatch_count", "created_at", "updated_at", "error_code",
        }
        self.assertTrue(expected.issubset(cols), f"missing: {expected - cols}")

    def test_screening_results_table_exists_with_columns(self):
        store = TaskStore(self.db_path)
        cols = self._columns(store, "screening_results")
        expected = {"id", "run_id", "job_id", "verdict", "created_at"}
        self.assertTrue(expected.issubset(cols), f"missing: {expected - cols}")

    def test_screening_runs_status_round_trip(self):
        store = TaskStore(self.db_path)
        now = _now()
        statuses = ["queued", "running", "succeeded", "partial", "failed", "interrupted"]
        with store._connection() as conn:
            for i, st in enumerate(statuses):
                conn.execute(
                    "INSERT INTO screening_runs (id, frozen_filters_json, status, source_count, "
                    "match_count, mismatch_count, created_at, updated_at, error_code) "
                    "VALUES (?, ?, ?, 0, 0, 0, ?, ?, NULL)",
                    (f"run-{i}", "{}", st, now, now),
                )
            rows = conn.execute("SELECT status FROM screening_runs ORDER BY id").fetchall()
        self.assertEqual([r["status"] for r in rows], statuses)

    def test_screening_results_verdict_round_trip(self):
        store = TaskStore(self.db_path)
        now = _now()
        with store._connection() as conn:
            conn.execute(
                "INSERT INTO screening_runs (id, frozen_filters_json, status, source_count, "
                "match_count, mismatch_count, created_at, updated_at, error_code) "
                "VALUES (?, ?, 'succeeded', 2, 1, 1, ?, ?, NULL)",
                ("run-1", "{}", now, now),
            )
            conn.execute(
                "INSERT INTO screening_results (id, run_id, job_id, verdict, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("r-1", "run-1", "job-a", "match", now),
            )
            conn.execute(
                "INSERT INTO screening_results (id, run_id, job_id, verdict, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("r-2", "run-1", "job-b", "mismatch", now),
            )
            rows = conn.execute(
                "SELECT verdict FROM screening_results WHERE run_id=? ORDER BY job_id", ("run-1",)
            ).fetchall()
        self.assertEqual([r["verdict"] for r in rows], ["match", "mismatch"])

    def test_screening_results_unique_per_run_job(self):
        store = TaskStore(self.db_path)
        now = _now()
        with store._connection() as conn:
            conn.execute(
                "INSERT INTO screening_runs (id, frozen_filters_json, status, source_count, "
                "match_count, mismatch_count, created_at, updated_at, error_code) "
                "VALUES (?, ?, 'succeeded', 1, 1, 0, ?, ?, NULL)",
                ("run-1", "{}", now, now),
            )
            conn.execute(
                "INSERT INTO screening_results (id, run_id, job_id, verdict, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("r-1", "run-1", "job-a", "match", now),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            with store._connection() as conn:
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, job_id, verdict, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("r-2", "run-1", "job-a", "mismatch", now),
                )

    def test_old_data_preserved_after_screening_migration(self):
        store = TaskStore(self.db_path)
        store.save_profile({"target_titles": ["后端"]}, name="default")
        store.create_task("old-1", "scrape", {"k": "v"}, output_path="a.json", detail_output_path="b.json")
        reopened = TaskStore(self.db_path)
        self.assertEqual(reopened.load_profile("default"), {"target_titles": ["后端"]})
        self.assertEqual(reopened.get_task("old-1")["params"], {"k": "v"})


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


class ScreeningZoneLifecycleTests(unittest.TestCase):
    """T028: zone lifecycle — run-isolated match/mismatch zones.

    Match/mismatch zones are bound to a screening run. A new run starts
    with empty zones; old run results are preserved (as history) but not
    mixed into the new run's view.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "db")

    def tearDown(self):
        self.temp.cleanup()

    def _make_run(self, frozen=None):
        return self.store.create_screening_run(frozen or {})

    # -- new run starts empty --

    def test_new_run_has_no_results(self):
        run = self._make_run()
        self.assertEqual(self.store.get_screening_results(run["id"]), [])

    def test_new_run_counts_zero(self):
        run = self._make_run()
        counts = self.store.count_screening_results(run["id"])
        self.assertEqual(counts["match"], 0)
        self.assertEqual(counts["mismatch"], 0)

    # -- add results to a run --

    def test_add_match_result_queryable_by_verdict(self):
        run = self._make_run()
        self.store.add_screening_result(run["id"], "job-1", "match")
        matches = self.store.get_screening_results(run["id"], verdict="match")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["job_id"], "job-1")

    def test_add_mismatch_result_queryable_by_verdict(self):
        run = self._make_run()
        self.store.add_screening_result(run["id"], "job-1", "mismatch")
        mismatches = self.store.get_screening_results(run["id"], verdict="mismatch")
        self.assertEqual(len(mismatches), 1)

    def test_match_and_mismatch_separated_by_verdict(self):
        run = self._make_run()
        self.store.add_screening_result(run["id"], "job-1", "match")
        self.store.add_screening_result(run["id"], "job-2", "mismatch")
        self.store.add_screening_result(run["id"], "job-3", "match")
        matches = self.store.get_screening_results(run["id"], verdict="match")
        mismatches = self.store.get_screening_results(run["id"], verdict="mismatch")
        self.assertEqual(len(matches), 2)
        self.assertEqual(len(mismatches), 1)

    # -- run isolation: new run does not see old run results --

    def test_new_run_does_not_see_old_run_results(self):
        run1 = self._make_run()
        self.store.add_screening_result(run1["id"], "job-1", "match")
        run2 = self._make_run()
        # run2 查询为空，不混入 run1 结果
        self.assertEqual(self.store.get_screening_results(run2["id"]), [])
        self.assertEqual(self.store.get_screening_results(run2["id"], verdict="match"), [])

    def test_old_run_results_preserved_after_new_run(self):
        run1 = self._make_run()
        self.store.add_screening_result(run1["id"], "job-1", "match")
        run2 = self._make_run()
        # run1 结果仍可查（作为历史保留，不删除）
        matches = self.store.get_screening_results(run1["id"], verdict="match")
        self.assertEqual(len(matches), 1)

    def test_counts_isolated_per_run(self):
        run1 = self._make_run()
        self.store.add_screening_result(run1["id"], "job-1", "match")
        self.store.add_screening_result(run1["id"], "job-2", "mismatch")
        run2 = self._make_run()
        self.store.add_screening_result(run2["id"], "job-3", "match")
        c1 = self.store.count_screening_results(run1["id"])
        c2 = self.store.count_screening_results(run2["id"])
        self.assertEqual(c1["match"], 1)
        self.assertEqual(c1["mismatch"], 1)
        self.assertEqual(c2["match"], 1)
        self.assertEqual(c2["mismatch"], 0)

    # -- uniqueness: one result per (run, job) --

    def test_duplicate_run_job_pair_rejected(self):
        run = self._make_run()
        self.store.add_screening_result(run["id"], "job-1", "match")
        with self.assertRaises(Exception):
            self.store.add_screening_result(run["id"], "job-1", "mismatch")


class ScreeningFeedbackPersistenceTests(unittest.TestCase):
    """T036: 感兴趣/不感兴趣持久化，复用 profile_jobs 状态与 feedback_events。

    感兴趣 -> profile_jobs.status="interested" + feedback_events.action="interested"
    不感兴趣 -> profile_jobs.status="deleted" + feedback_events.action="not_interested"
    沿用 001 的撤销机制（revoked_at）。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "db")
        self.profile = self.store.create_profile("测试画像")
        self.pid = self.profile["id"]
        # 预置一条 job 到 jobs 表
        self.job = self.store.save_job(
            "https://www.zhipin.com/job_detail/job-A.html",
            "https://www.zhipin.com/job_detail/job-A.html",
            "Python", "公司A", "20K", "上海", "JD",
        )
        self.job_id = self.job["id"]

    def tearDown(self):
        self.temp.cleanup()

    # -- 感兴趣持久化 --

    def test_mark_interest_sets_profile_job_status_interested(self):
        self.store.mark_screening_interest(self.pid, self.job_id)
        pj = self.store.get_profile_job(self.pid, self.job_id)
        self.assertEqual(pj["status"], "interested")

    def test_mark_interest_writes_feedback_event_interested(self):
        self.store.mark_screening_interest(self.pid, self.job_id)
        feedbacks = self.store.list_feedback(self.pid, self.job_id)
        self.assertTrue(any(f["action"] == "interested" for f in feedbacks))

    def test_mark_interest_persists_across_new_screening_run(self):
        # 感兴趣标记后，新建筛选运行不影响感兴趣记录
        self.store.mark_screening_interest(self.pid, self.job_id)
        run = self.store.create_screening_run({})
        # 感兴趣仍在
        pj = self.store.get_profile_job(self.pid, self.job_id)
        self.assertEqual(pj["status"], "interested")

    def test_list_screening_interested_returns_interested_jobs(self):
        self.store.mark_screening_interest(self.pid, self.job_id)
        interested = self.store.list_screening_interested(self.pid)
        self.assertEqual(len(interested), 1)
        self.assertEqual(interested[0]["job_id"], self.job_id)

    def test_list_screening_interested_excludes_deleted(self):
        self.store.mark_screening_interest(self.pid, self.job_id)
        # 再标记不感兴趣（覆盖）
        self.store.mark_screening_reject(self.pid, self.job_id)
        interested = self.store.list_screening_interested(self.pid)
        self.assertEqual(interested, [])

    # -- 不感兴趣持久化 --

    def test_mark_reject_sets_profile_job_status_deleted(self):
        self.store.mark_screening_reject(self.pid, self.job_id)
        pj = self.store.get_profile_job(self.pid, self.job_id)
        self.assertEqual(pj["status"], "deleted")

    def test_mark_reject_writes_feedback_event_not_interested(self):
        self.store.mark_screening_reject(self.pid, self.job_id)
        feedbacks = self.store.list_feedback(self.pid, self.job_id)
        self.assertTrue(any(f["action"] == "not_interested" for f in feedbacks))

    def test_mark_reject_persists_across_new_screening_run(self):
        self.store.mark_screening_reject(self.pid, self.job_id)
        self.store.create_screening_run({})
        pj = self.store.get_profile_job(self.pid, self.job_id)
        self.assertEqual(pj["status"], "deleted")

    def test_list_screening_rejected_returns_deleted_jobs(self):
        self.store.mark_screening_reject(self.pid, self.job_id)
        rejected = self.store.list_screening_rejected(self.pid)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["job_id"], self.job_id)

    def test_list_screening_rejected_excludes_interested(self):
        self.store.mark_screening_reject(self.pid, self.job_id)
        self.store.mark_screening_interest(self.pid, self.job_id)
        rejected = self.store.list_screening_rejected(self.pid)
        self.assertEqual(rejected, [])

    # -- 反馈覆盖：同一 job 反复标记取最后状态 --

    def test_remark_interest_after_reject_updates_status(self):
        self.store.mark_screening_reject(self.pid, self.job_id)
        self.store.mark_screening_interest(self.pid, self.job_id)
        pj = self.store.get_profile_job(self.pid, self.job_id)
        self.assertEqual(pj["status"], "interested")
        # 感兴趣区有，垃圾桶区无
        self.assertEqual(len(self.store.list_screening_interested(self.pid)), 1)
        self.assertEqual(len(self.store.list_screening_rejected(self.pid)), 0)

    def test_remark_reject_after_interest_updates_status(self):
        self.store.mark_screening_interest(self.pid, self.job_id)
        self.store.mark_screening_reject(self.pid, self.job_id)
        pj = self.store.get_profile_job(self.pid, self.job_id)
        self.assertEqual(pj["status"], "deleted")
        self.assertEqual(len(self.store.list_screening_interested(self.pid)), 0)
        self.assertEqual(len(self.store.list_screening_rejected(self.pid)), 1)

    # -- 展示排除：垃圾桶 job_id 集合 --

    def test_list_screening_rejected_job_ids_for_exclusion(self):
        self.store.mark_screening_reject(self.pid, self.job_id)
        # 再加一条 job 标记不感兴趣
        job2 = self.store.save_job(
            "https://www.zhipin.com/job_detail/job-B.html",
            "https://www.zhipin.com/job_detail/job-B.html",
            "Java", "公司B", "15K", "北京", "JD2",
        )
        self.store.mark_screening_reject(self.pid, job2["id"])
        excluded = set(self.store.list_screening_rejected_job_ids(self.pid))
        self.assertEqual(excluded, {self.job_id, job2["id"]})

    def test_rejected_job_ids_excludes_interested(self):
        self.store.mark_screening_reject(self.pid, self.job_id)
        self.store.mark_screening_interest(self.pid, self.job_id)
        excluded = set(self.store.list_screening_rejected_job_ids(self.pid))
        self.assertEqual(excluded, set())

    def test_rejected_job_ids_empty_when_no_feedback(self):
        excluded = set(self.store.list_screening_rejected_job_ids(self.pid))
        self.assertEqual(excluded, set())

    # -- 区域清空不影响持久区 --

    def test_new_screening_run_does_not_clear_interested(self):
        self.store.mark_screening_interest(self.pid, self.job_id)
        run1 = self.store.create_screening_run({})
        self.store.add_screening_result(run1["id"], self.job_id, "match")
        run2 = self.store.create_screening_run({})
        # 临时区清空（run2 无结果），但感兴趣区保留
        self.assertEqual(self.store.get_screening_results(run2["id"]), [])
        interested = self.store.list_screening_interested(self.pid)
        self.assertEqual(len(interested), 1)

    def test_new_screening_run_does_not_clear_rejected(self):
        self.store.mark_screening_reject(self.pid, self.job_id)
        run1 = self.store.create_screening_run({})
        self.store.add_screening_result(run1["id"], self.job_id, "mismatch")
        run2 = self.store.create_screening_run({})
        rejected = self.store.list_screening_rejected(self.pid)
        self.assertEqual(len(rejected), 1)

    # -- run_id 关联（可选）--

    def test_mark_interest_with_run_id_records_run(self):
        run = self.store.create_screening_run({})
        self.store.mark_screening_interest(self.pid, self.job_id, run_id=run["id"])
        feedbacks = self.store.list_feedback(self.pid, self.job_id)
        interested_fb = [f for f in feedbacks if f["action"] == "interested"][0]
        self.assertEqual(interested_fb["run_id"], run["id"])

    def test_mark_reject_with_run_id_records_run(self):
        run = self.store.create_screening_run({})
        self.store.mark_screening_reject(self.pid, self.job_id, run_id=run["id"])
        feedbacks = self.store.list_feedback(self.pid, self.job_id)
        reject_fb = [f for f in feedbacks if f["action"] == "not_interested"][0]
        self.assertEqual(reject_fb["run_id"], run["id"])

    # -- profile 隔离 --

    def test_interest_isolated_per_profile(self):
        p2 = self.store.create_profile("画像2")
        self.store.mark_screening_interest(self.pid, self.job_id)
        # p2 的感兴趣区为空
        self.assertEqual(self.store.list_screening_interested(p2["id"]), [])
        self.assertEqual(self.store.list_screening_rejected_job_ids(p2["id"]), [])


class ScreeningCrossResumePersistenceTests(unittest.TestCase):
    """T039: 换简历时感兴趣与垃圾桶跨简历持久保留（暂定方案）。

    spec 暂定方案：换简历时感兴趣区与垃圾桶区域跨简历持久保留。
    实现方式：感兴趣/垃圾桶绑 profile_id，换简历只是更新 profile.resume_id，
    profile_id 不变，因此感兴趣/垃圾桶自然跨简历持久。
    删除简历也不影响已持久化的感兴趣/垃圾桶记录。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "db")
        self.profile = self.store.create_profile("测试画像")
        self.pid = self.profile["id"]
        # 预置简历1
        self.resume1 = self.store.save_resume(
            self.pid, "/tmp/r1.txt", "txt", "简历1内容", "hash1", "r1.txt",
        )
        # 预置 job
        self.job = self.store.save_job(
            "https://www.zhipin.com/job_detail/job-A.html",
            "https://www.zhipin.com/job_detail/job-A.html",
            "Python", "公司A", "20K", "上海", "JD",
        )
        self.job_id = self.job["id"]

    def tearDown(self):
        self.temp.cleanup()

    # -- 换简历：感兴趣保留 --

    def test_interest_persists_across_resume_change(self):
        # 用简历1期间标记感兴趣
        self.store.mark_screening_interest(self.pid, self.job_id)
        # 换简历2
        self.store.save_resume(self.pid, "/tmp/r2.txt", "txt", "简历2内容", "hash2", "r2.txt")
        # 感兴趣区仍保留
        interested = self.store.list_screening_interested(self.pid)
        self.assertEqual(len(interested), 1)
        self.assertEqual(interested[0]["job_id"], self.job_id)

    def test_interest_persists_across_multiple_resume_changes(self):
        self.store.mark_screening_interest(self.pid, self.job_id)
        for i in range(3):
            self.store.save_resume(self.pid, f"/tmp/r{i}.txt", "txt", f"简历{i}", f"h{i}", f"r{i}.txt")
        interested = self.store.list_screening_interested(self.pid)
        self.assertEqual(len(interested), 1)

    # -- 换简历：垃圾桶保留 --

    def test_reject_persists_across_resume_change(self):
        self.store.mark_screening_reject(self.pid, self.job_id)
        self.store.save_resume(self.pid, "/tmp/r2.txt", "txt", "简历2内容", "hash2", "r2.txt")
        rejected = self.store.list_screening_rejected(self.pid)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["job_id"], self.job_id)

    def test_rejected_job_ids_persists_across_resume_change(self):
        self.store.mark_screening_reject(self.pid, self.job_id)
        self.store.save_resume(self.pid, "/tmp/r2.txt", "txt", "简历2内容", "hash2", "r2.txt")
        excluded = set(self.store.list_screening_rejected_job_ids(self.pid))
        self.assertEqual(excluded, {self.job_id})

    # -- 删除简历：感兴趣/垃圾桶不受影响 --

    def test_interest_persists_after_resume_delete(self):
        self.store.mark_screening_interest(self.pid, self.job_id)
        self.store.delete_resume(self.resume1["id"])
        interested = self.store.list_screening_interested(self.pid)
        self.assertEqual(len(interested), 1)

    def test_reject_persists_after_resume_delete(self):
        self.store.mark_screening_reject(self.pid, self.job_id)
        self.store.delete_resume(self.resume1["id"])
        rejected = self.store.list_screening_rejected(self.pid)
        self.assertEqual(len(rejected), 1)

    def test_rejected_job_ids_persists_after_resume_delete(self):
        self.store.mark_screening_reject(self.pid, self.job_id)
        self.store.delete_resume(self.resume1["id"])
        excluded = set(self.store.list_screening_rejected_job_ids(self.pid))
        self.assertEqual(excluded, {self.job_id})

    # -- 换简历后仍可继续标记 --

    def test_can_mark_interest_after_resume_change(self):
        self.store.save_resume(self.pid, "/tmp/r2.txt", "txt", "简历2", "hash2", "r2.txt")
        self.store.mark_screening_interest(self.pid, self.job_id)
        interested = self.store.list_screening_interested(self.pid)
        self.assertEqual(len(interested), 1)

    def test_can_mark_reject_after_resume_change(self):
        self.store.save_resume(self.pid, "/tmp/r2.txt", "txt", "简历2", "hash2", "r2.txt")
        self.store.mark_screening_reject(self.pid, self.job_id)
        rejected = self.store.list_screening_rejected(self.pid)
        self.assertEqual(len(rejected), 1)

    # -- 换简历后展示排除仍生效 --

    def test_display_exclusion_still_works_after_resume_change(self):
        self.store.mark_screening_reject(self.pid, self.job_id)
        self.store.save_resume(self.pid, "/tmp/r2.txt", "txt", "简历2", "hash2", "r2.txt")
        # 换简历后，垃圾桶 job_id 仍在排除集合中
        excluded = set(self.store.list_screening_rejected_job_ids(self.pid))
        self.assertIn(self.job_id, excluded)

    # -- profile.resume_id 更新但不影响 profile_jobs --

    def test_profile_resume_id_updates_but_profile_jobs_intact(self):
        self.store.mark_screening_interest(self.pid, self.job_id)
        old_resume_id = self.store.get_profile(self.pid)["resume_id"]
        self.store.save_resume(self.pid, "/tmp/r2.txt", "txt", "简历2", "hash2", "r2.txt")
        new_resume_id = self.store.get_profile(self.pid)["resume_id"]
        self.assertNotEqual(old_resume_id, new_resume_id)
        # profile_jobs 记录不受影响
        pj = self.store.get_profile_job(self.pid, self.job_id)
        self.assertEqual(pj["status"], "interested")


if __name__ == "__main__":
    unittest.main()
