import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

from webui.app import TaskRunner
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
        with mock.patch("webui.app.subprocess.Popen", return_value=FakeProcess(0)):
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


if __name__ == "__main__":
    unittest.main()
