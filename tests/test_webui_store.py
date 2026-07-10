import pathlib
import tempfile
import unittest

from webui.store import TaskStore


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

        restarted = TaskStore(self.db_path)

        self.assertEqual(restarted.get_task("run-1")["status"], "interrupted")

    def test_terminal_task_rejects_invalid_transition(self):
        self.store.create_task("run-1", "scrape", {})
        self.store.update_task("run-1", "running")
        self.store.update_task("run-1", "failed", returncode=1)

        with self.assertRaisesRegex(ValueError, "failed"):
            self.store.update_task("run-1", "running")

    def test_missing_task_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.store.get_task("missing")


if __name__ == "__main__":
    unittest.main()
