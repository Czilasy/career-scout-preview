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
        self.assertEqual(boss_items[0]["mismatch_count"], 0)

        zhilian_items = self.service.list_history("zhilian")
        self.assertEqual(len(zhilian_items), 1)
        self.assertTrue(zhilian_items[0]["is_latest"])

    def test_archive_retries_transient_write_lock(self):
        """020 US7 模式：归档遇到瞬时 SQLite 写锁冲突时短退避重试，不直接失败。"""
        import contextlib
        import sqlite3
        from unittest import mock

        from webui.store import TaskStore

        _save_round(self.store, "boss", keyword="Python")
        run_id = self.store.list_history_rounds()[0]["id"]
        real_connection = TaskStore._connection
        attempts = {"n": 0}
        sleeps: list[float] = []

        @contextlib.contextmanager
        def flaky_connection(self):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise sqlite3.OperationalError("database is locked")
            with real_connection(self) as conn:
                yield conn

        with mock.patch.object(TaskStore, "_connection", flaky_connection), \
                mock.patch(
                    "webui.store_result_history_mixin.time.sleep",
                    side_effect=lambda s: sleeps.append(s),
                ):
            archived = self.service.archive_all_current_results()

        self.assertEqual(archived, [run_id])
        self.assertEqual(attempts["n"], 2, "首次锁冲突后应重试一次")
        self.assertEqual(sleeps, [0.1])

    def test_history_detail_returns_parent_source_outcomes(self):
        parent_id = "scrape-source"
        self.store.create_screening_run(
            parent_id, source_count=1, execution_params={"platform": "boss"},
        )
        self.store.append_source_attempt(
            run_id=parent_id, platform="boss", combo_key="k1",
            attempt_no=1, outcome_kind="non_empty", job_count=2,
        )
        run_id = self.store.save_pipeline_result(
            {
                "ok": True,
                "jobs": [{"platform_job_id": "j1", "title": "岗位"}],
                "dropped": [], "total_scraped": 1, "total_kept": 1,
            },
            {"platform": "boss"},
            execution_params={"platform": "boss", "scrape_task_id": parent_id},
        )
        detail = self.service.get_round(run_id)
        self.assertEqual(detail["source_summary"]["total_combos"], 1)
        self.assertEqual(detail["source_outcomes"][0]["combo_key"], "k1")

    def test_archive_is_idempotent_and_hides_latest_queries(self):
        first = _save_round(self.store, "boss")
        archived = self.service.archive_all_current_results()
        self.assertEqual(archived, [first])
        self.assertEqual(self.service.archive_all_current_results(), [])
        self.assertEqual(self.store.load_latest_pipeline_result_for_platform("boss"), None)
        self.assertEqual(len(self.service.list_history("boss")), 1)

    def test_delete_preserves_task_logs(self):
        run_id = _save_round(self.store, "boss")
        self.store.append_task_event(run_id, "stage_start", {"stage": "ai_rough"})

        self.assertTrue(self.service.delete_round(run_id))
        self.assertFalse(self.store.history_round_exists(run_id))
        self.assertEqual(len(self.store.list_task_events(run_id)), 1)

    def test_delete_latest_does_not_promote_archive(self):
        _save_round(self.store, "boss", keyword="old-1")
        _save_round(self.store, "boss", keyword="old-2")
        self.service.archive_all_current_results()
        newest = _save_round(self.store, "boss", keyword="newest")

        self.assertTrue(self.service.delete_round(newest))
        # 删除最新轮后不复活上一轮：该平台 latest 置空，旧轮保持归档
        self.assertIsNone(
            self.store.load_latest_pipeline_result_for_platform("boss"))
        items = self.service.list_history("boss")
        self.assertFalse(any(item["is_latest"] for item in items))
        self.assertTrue(all(item["archived_at"] for item in items))

    def test_history_statuses_are_three_way_only(self):
        """017-US3: 历史轮状态取值域收敛为 done/partial/scraped_only。"""
        _save_round(self.store, "boss", status="done")
        _save_round(self.store, "boss", status="partial")
        self.store.save_scraped_only_snapshot(
            {
                "ok": True,
                "jobs": [{"platform": "boss", "platform_job_id": "s1",
                          "title": "岗位", "verdict": ""}],
                "dropped": [], "total_scraped": 1, "total_kept": 1,
            },
            {"platform": "boss"},
            scrape_task_id="src-s3",
        )
        items = self.service.list_history("boss")
        self.assertEqual(len(items), 3)
        self.assertEqual(
            {item["status"] for item in items},
            {"done", "partial", "scraped_only"},
        )
        # 017-US3: 列表返回定稿时间字段（抽屉主时间消费）
        for item in items:
            self.assertIn("finished_at", item)
            self.assertIsNotNone(item["finished_at"])

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
