import pathlib
import tempfile
import unittest

from webui.app import create_app
from webui.result_history import ResultHistoryService
from webui.store import TaskStore


def _save_round(store, platform="boss", status="done", keyword="Python", job_id=None):
    job_key = job_id or f"{platform}-{status}-{len(store.list_history_rounds(platform))}"
    result = {
        "ok": True,
        "jobs": [{
            "platform": platform,
            "platform_job_id": job_key,
            "job_id": job_key,
            "title": "后端工程师",
            "verdict": "match",
        }],
        "dropped": [],
        "total_scraped": 1,
        "total_kept": 1,
        "total_matched": 1,
        "total_dropped": 0,
        "profile_summary": "3年Python后端经验",
    }
    return store.save_pipeline_result(
        result,
        {"platform": platform, "keyword": keyword, "city": ["上海"]},
        status=status,
    )


class ResultHistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        self.service = ResultHistoryService(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_list_filters_and_marks_latest_per_platform(self):
        _save_round(self.store, "boss", keyword="Python")
        _save_round(self.store, "boss", keyword="Java")
        _save_round(self.store, "zhilian", keyword="Go")

        items = self.service.list_history()
        self.assertEqual(len(items), 3)
        self.assertEqual({item["platform"] for item in items}, {"boss", "zhilian"})
        boss_items = [item for item in items if item["platform"] == "boss"]
        self.assertEqual([item["is_latest"] for item in boss_items], [True, False])
        self.assertEqual(boss_items[0]["keyword_summary"], "Java / 上海")
        self.assertEqual(boss_items[0]["profile_summary_preview"], "3年Python后端经验")

        zhilian_items = self.service.list_history("zhilian")
        self.assertEqual(len(zhilian_items), 1)
        self.assertTrue(zhilian_items[0]["is_latest"])

    def test_archive_is_idempotent_and_hides_latest_queries(self):
        first = _save_round(self.store, "boss")
        archived = self.service.archive_latest()
        self.assertEqual(archived, [first])
        self.assertEqual(self.service.archive_latest(), [])
        self.assertEqual(self.store.load_latest_pipeline_result_for_platform("boss"), None)
        self.assertEqual(len(self.service.list_history("boss")), 1)

    def test_delete_preserves_task_logs(self):
        run_id = _save_round(self.store, "boss")
        self.store.append_task_event(run_id, "stage_start", {"stage": "ai_rough"})

        self.assertTrue(self.service.delete_round(run_id))
        self.assertFalse(self.store.history_round_exists(run_id))
        self.assertEqual(len(self.store.list_task_events(run_id)), 1)

    def test_delete_latest_promotes_most_recent_archive(self):
        _save_round(self.store, "boss", keyword="old-1")
        _save_round(self.store, "boss", keyword="old-2")
        self.service.archive_latest()
        newest = _save_round(self.store, "boss", keyword="newest")

        self.assertTrue(self.service.delete_round(newest))
        items = self.service.list_history("boss")
        self.assertTrue(items[0]["is_latest"])
        self.assertIsNone(items[0]["archived_at"])
        self.assertEqual(items[0]["keyword_summary"], "old-2 / 上海")

    def test_failed_round_detail_keeps_raw_status(self):
        run_id = _save_round(self.store, "zhilian", status="failed")
        detail = self.service.get_round(run_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["status"], "failed")
        self.assertEqual(detail["result"]["jobs"][0]["platform"], "zhilian")

    def test_prune_keeps_30_rounds_per_platform(self):
        for index in range(32):
            _save_round(self.store, "boss", keyword=f"k-{index}")
        deleted = self.service.prune_retention(limit=30)
        self.assertEqual(len(deleted), 2)
        items = self.service.list_history("boss")
        self.assertEqual(len(items), 30)


class ResultHistoryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
        })
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        self.temp.cleanup()

    def test_history_list_and_detail(self):
        run_id = _save_round(self.store, "boss")

        response = self.client.get("/api/result-history")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["items"][0]["run_id"], run_id)

        detail = self.client.get(f"/api/result-history/{run_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()["status"], "done")

        missing = self.client.get("/api/result-history/missing")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.get_json()["error"], "round_not_found")

    def test_archive_latest_and_delete(self):
        run_id = _save_round(self.store, "boss")
        archive = self.client.post("/api/result-history/archive-latest")
        self.assertEqual(archive.status_code, 200)
        self.assertEqual(archive.get_json()["archived_run_ids"], [run_id])

        deleted = self.client.delete(f"/api/result-history/{run_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.get_json()["deleted"], True)

        missing = self.client.delete("/api/result-history/missing")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.get_json()["error"], "round_not_found")

    def test_invalid_platform_rejected(self):
        response = self.client.get("/api/result-history?platform=all")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_platform")


if __name__ == "__main__":
    unittest.main()
