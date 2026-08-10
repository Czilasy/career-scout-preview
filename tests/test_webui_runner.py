import json
import pathlib
import sys
import tempfile
import unittest

from webui.app import TaskRunner, WorkbenchRunner
from webui.core import normalize_profile, validate_search_params
from webui.store import TaskStore


class FakeProcess:
    stdout = []

    def __init__(self, returncode=0):
        self.returncode = returncode
        self.terminated = False

    def wait(self):
        return self.returncode

    def terminate(self):
        self.terminated = True


class FakeExecutionExecutor:
    def __init__(self, *, callback=None, returncode=0, failure_code=None):
        self.callback = callback
        self.returncode = returncode
        self.failure_code = failure_code
        self.calls = []

    def execute(self, command, **kwargs):
        from types import SimpleNamespace
        self.calls.append((command, kwargs))
        if self.callback:
            self.callback(command)
        return SimpleNamespace(
            ok=self.returncode == 0 and self.failure_code is None,
            returncode=self.returncode,
            failure_code=self.failure_code,
            output_tail="",
        )


class TaskRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.store = TaskStore(root / "webui.db")
        self.runner = TaskRunner(self.store, root / "results", sys.executable, start_tasks=False)
        self.search = validate_search_params({"keyword": "Python", "detail": True})
        self.profile = normalize_profile({})

    def tearDown(self):
        self.temp.cleanup()

    def test_validate_artifacts_rejects_missing_and_malformed_list_output(self):
        task = self.runner.create_scrape(self.search, self.profile)

        with self.assertRaisesRegex(ValueError, "列表产物"):
            self.runner.validate_artifacts(task)

        path = pathlib.Path(task["output_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "解析"):
            self.runner.validate_artifacts(task)

    def test_validate_artifacts_accepts_legitimate_empty_result(self):
        task = self.runner.create_scrape(self.search, self.profile)
        path = pathlib.Path(task["output_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"jobs": []}), encoding="utf-8")

        self.runner.validate_artifacts(task)

    def test_validate_artifacts_requires_details_for_nonempty_detailed_run(self):
        task = self.runner.create_scrape(self.search, self.profile)
        path = pathlib.Path(task["output_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"jobs": [{"job_id": "one"}]}), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "详情产物"):
            self.runner.validate_artifacts(task)

    def test_execute_marks_returncode_zero_without_artifact_failed(self):
        task = self.runner.create_scrape(self.search, self.profile)
        self.runner.process_executor = FakeExecutionExecutor()
        self.runner._execute(task["id"])

        stored = self.store.get_task(task["id"])
        self.assertEqual(stored["status"], "failed")
        self.assertIn("列表产物", stored["error"])

    def test_cancel_queued_task_marks_it_interrupted(self):
        task = self.runner.create_scrape(self.search, self.profile)

        cancelled = self.runner.cancel(task["id"])

        self.assertEqual(cancelled["status"], "interrupted")
        self.assertIn("取消", cancelled["error"])

    def test_cancel_running_task_terminates_process(self):
        task = self.runner.create_scrape(self.search, self.profile)
        self.store.update_task(task["id"], "running")
        process = FakeProcess()
        self.runner._processes[task["id"]] = process

        cancelled = self.runner.cancel(task["id"])

        self.assertTrue(process.terminated)
        self.assertEqual(cancelled["status"], "interrupted")


class WorkbenchRunnerTests(unittest.TestCase):
    """T006/T023: parent search run, child query states, budget, controlled paths."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.store = TaskStore(self.root / "webui.db")
        self.runner = WorkbenchRunner(
            self.store, self.root / "results", sys.executable, start_tasks=False
        )
        self.profile = self.store.create_profile("测试", confirmed_fields={"city": "上海"})

    def tearDown(self):
        self.temp.cleanup()

    def test_create_search_run_with_keywords_creates_parent_and_queries(self):
        run = self.runner.create_search_run(
            self.profile["id"],
            keywords=["Python 后端", "FastAPI"],
            confirmed_fields={"city": "上海"},
        )
        self.assertEqual(run["status"], "queued")
        self.assertEqual(run["total_detail_budget"], 60)

        queries = self.store.list_run_queries(run["id"])
        self.assertEqual(len(queries), 2)
        self.assertEqual(queries[0]["ordinal"], 0)
        self.assertEqual(queries[1]["ordinal"], 1)
        # Budget split across 2 queries
        self.assertEqual(sum(q["detail_budget"] for q in queries), 60)

    def test_query_output_paths_contain_run_and_ordinal(self):
        run = self.runner.create_search_run(
            self.profile["id"],
            keywords=["Python"],
            confirmed_fields={"city": "上海"},
        )
        queries = self.store.list_run_queries(run["id"])
        for q in queries:
            self.assertIn(run["id"], q["list_output_path"])
            self.assertIn(run["id"], q["detail_output_path"])

    def test_query_command_preserves_manual_industry_filter(self):
        run = self.runner.create_search_run(
            self.profile["id"], keywords=["Python"],
            confirmed_fields={"city": "上海", "industry": "100020"},
        )
        command = self.runner._query_command(self.store.list_run_queries(run["id"])[0])
        self.assertEqual(command[command.index("--industry") + 1], "100020")

    def test_run_succeeds_when_all_queries_succeed(self):
        run = self.runner.create_search_run(
            self.profile["id"],
            keywords=["Python"],
            confirmed_fields={"city": "上海"},
        )
        # Simulate query completion
        queries = self.store.list_run_queries(run["id"])
        for q in queries:
            self.store.update_run_query(q["id"], status="succeeded")
        self.runner._finalize_run(run["id"])
        self.assertEqual(self.store.get_search_run(run["id"])["status"], "succeeded")

    def test_run_partial_when_some_queries_fail(self):
        run = self.runner.create_search_run(
            self.profile["id"],
            keywords=["Python", "Go"],
            confirmed_fields={"city": "上海"},
        )
        queries = self.store.list_run_queries(run["id"])
        self.store.update_run_query(queries[0]["id"], status="succeeded")
        self.store.update_run_query(queries[1]["id"], status="failed", error_code="scrape_error")
        self.runner._finalize_run(run["id"])
        self.assertEqual(self.store.get_search_run(run["id"])["status"], "partial")

    def test_run_failed_when_all_queries_fail(self):
        run = self.runner.create_search_run(
            self.profile["id"],
            keywords=["Python"],
            confirmed_fields={"city": "上海"},
        )
        queries = self.store.list_run_queries(run["id"])
        for q in queries:
            self.store.update_run_query(q["id"], status="failed", error_code="scrape_error")
        self.runner._finalize_run(run["id"])
        self.assertEqual(self.store.get_search_run(run["id"])["status"], "failed")

    def test_cancel_preserves_written_jobs(self):
        run = self.runner.create_search_run(
            self.profile["id"],
            keywords=["Python"],
            confirmed_fields={"city": "上海"},
        )
        self.store.update_search_run(run["id"], status="running")
        # Write a job before cancel
        job = self.store.save_job(
            "https://www.zhipin.com/job_detail/cancel.html",
            "https://www.zhipin.com/job_detail/cancel.html",
            "T", "C", "S", "L", "JD",
        )
        self.store.link_profile_job(self.profile["id"], job["id"], run["id"], run["id"])

        cancelled = self.runner.cancel_search_run(run["id"])
        self.assertEqual(cancelled["status"], "interrupted")
        # Job still accessible
        remaining = self.store.list_profile_jobs(self.profile["id"])
        self.assertEqual(len(remaining), 1)

    def test_cancel_running_search_terminates_child_process(self):
        run = self.runner.create_search_run(
            self.profile["id"], keywords=["Python"], confirmed_fields={"city": "上海"},
        )
        self.store.update_search_run(run["id"], status="running")
        process = FakeProcess()
        self.runner._processes[run["id"]] = process
        self.runner.cancel_search_run(run["id"])
        self.assertTrue(process.terminated)

    def test_detail_budget_never_exceeds_60(self):
        run = self.runner.create_search_run(
            self.profile["id"],
            keywords=["k1", "k2", "k3"],
            confirmed_fields={"city": "上海"},
        )
        queries = self.store.list_run_queries(run["id"])
        total = sum(q["detail_budget"] for q in queries)
        self.assertLessEqual(total, 60)

    def test_execute_search_run_runs_existing_scraper_and_persists_complete_jd(self):
        """A queued parent run must actually execute its child query and stream a safe JD."""
        run = self.runner.create_search_run(
            self.profile["id"], keywords=["Python"], confirmed_fields={"city": "上海"},
        )

        def write_artifacts(command, **_kwargs):
            output = pathlib.Path(command[command.index("--output") + 1])
            detail = pathlib.Path(command[command.index("--detail-output") + 1])
            output.write_text(json.dumps({"jobs": [{
                "job_id": "safe", "job_link": "https://www.zhipin.com/job_detail/safe.html",
                "title": "Python 后端", "boss_name": "Acme", "salary": "30K", "location": "上海",
            }]}), encoding="utf-8")
            detail.write_text(json.dumps([{"job_id": "safe", "jd": "完整 JD"}]), encoding="utf-8")
            return FakeProcess(0)

        executor = FakeExecutionExecutor(callback=write_artifacts)
        self.runner.process_executor = executor
        self.runner._execute_search_run(run["id"])

        command = executor.calls[0][0]
        self.assertEqual(command[command.index("--max-details") + 1], "60")
        self.assertEqual(self.store.get_search_run(run["id"])["status"], "succeeded")
        jobs = self.store.list_profile_jobs(self.profile["id"], run_id=run["id"])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(self.store.get_job(jobs[0]["job_id"])["jd"], "完整 JD")
        events = self.store.list_search_events(run["id"])
        self.assertEqual(events[0]["type"], "job_completed")


if __name__ == "__main__":
    unittest.main()
