import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

from webui.app import create_app
from webui.scrape_only import (
    build_undecided_result,
    save_scrape_snapshot,
    save_screen_result,
)
from webui.store import TaskStore


def _scrape_jobs(n=2):
    return [
        {
            "platform_job_id": f"job-{i}",
            "job_id": f"job-{i}",
            "title": f"岗位 {i}",
            "boss_name": f"公司 {i}",
            "salary": "20-30K",
            "location": "上海",
            "job_link": f"https://example.com/{i}",
        }
        for i in range(n)
    ]


def _screened_result(n=2):
    jobs = []
    for i in range(n):
        job = {
            "platform": "boss",
            "platform_job_id": f"job-{i}",
            "job_id": f"job-{i}",
            "title": f"岗位 {i}",
            "verdict": "match" if i == 0 else "uncertain",
            "verdict_reason": "经验匹配" if i == 0 else "JD 抓取失败",
        }
        jobs.append(job)
    return {"ok": True, "jobs": jobs, "dropped": [], "total_scraped": n,
            "profile_summary": "3年Python后端", "profile_facts": {}}


class ScrapeOnlyServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_build_undecided_result_normalizes_fields(self):
        result = build_undecided_result(_scrape_jobs(2), platform="boss")
        self.assertEqual(result["total_scraped"], 2)
        self.assertEqual(result["total_kept"], 2)
        self.assertEqual(result["total_matched"], 0)
        job = result["jobs"][0]
        self.assertEqual(job["platform"], "boss")
        self.assertEqual(job["verdict"], "")
        self.assertEqual(job["company"], "公司 0")  # boss_name 兜底
        self.assertEqual(job["source_url"], "https://example.com/0")  # job_link 兜底
        self.assertEqual(job["caveats"], [])
        self.assertEqual(job["flags"], [])

    def test_save_scrape_snapshot_persists_round(self):
        outcome = save_scrape_snapshot(
            self.store, _scrape_jobs(), platform="boss",
            scrape_task_id="scrape-1", profile_summary="3年Python后端",
        )
        self.assertTrue(outcome["saved"])
        self.assertEqual(outcome["result"]["source_run_id"], outcome["run_id"])
        run = self.store.get_screening_run(outcome["run_id"])
        self.assertEqual(run["status"], "scraped_only")
        self.assertEqual(run["execution_params"].get("scrape_task_id"), "scrape-1")

    def test_save_scrape_snapshot_zero_jobs_skips_persist(self):
        before = self.store.list_history_rounds()
        outcome = save_scrape_snapshot(
            self.store, [], platform="boss", scrape_task_id="scrape-0",
        )
        self.assertFalse(outcome["saved"])
        self.assertEqual(self.store.list_history_rounds(), before)

    def test_save_screen_result_upgrades_same_round(self):
        first = save_scrape_snapshot(
            self.store, _scrape_jobs(), platform="boss", scrape_task_id="scrape-1",
        )
        created_before = self.store.get_screening_run(first["run_id"])["created_at"]

        run_id = save_screen_result(
            self.store, _screened_result(), {"screening": {}, "platform": "boss"},
            scrape_task_id="scrape-1", status="done", platform="boss",
        )
        self.assertEqual(run_id, first["run_id"])
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["created_at"], created_before)
        self.assertEqual(run["status"], "partial")  # 含 pending 岗位
        self.assertEqual(run["execution_params"].get("scrape_task_id"), "scrape-1")
        # 升级后不再是 scraped_only，同一来源不再命中
        self.assertIsNone(self.store.latest_scraped_only_for_source("scrape-1"))
        self.assertEqual(len(self.store.list_history_rounds("boss")), 1)

    def test_save_screen_result_creates_new_round_when_no_scraped_only(self):
        run_id = save_screen_result(
            self.store, _screened_result(1), {"screening": {}, "platform": "boss"},
            scrape_task_id="scrape-x", status="done", platform="boss",
        )
        self.assertIsNotNone(run_id)
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["status"], "done")


class ScrapeResultSaveApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.db_path = root / "state" / "webui.db"
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(self.db_path),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        session = self.client.get("/api/session")
        self.token = session.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        # 与 app 共享同一 DB 的 store，用于构造真实抓取任务数据
        # （_ensure_scrape_source 走 DB 重建分支，不依赖内存任务表）。
        self.store = TaskStore(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def _seed_scrape_task(self, task_id, jobs):
        # scrape_run_jobs 外键指向 screening_runs：先建 run 行再写岗位。
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO screening_runs (id, status, created_at, updated_at, execution_params_json) "
                "VALUES (?, 'succeeded', ?, ?, ?)",
                (
                    task_id,
                    "2026-08-12T00:00:00",
                    "2026-08-12T00:00:00",
                    json.dumps({"script_params": {
                        "keyword": ["Python"], "city": ["上海"],
                    }}),
                ),
            )
        self.store.save_scrape_combo_result(task_id, "k1", jobs, ["k1"])

    def test_save_ok(self):
        task_id = "scrape-abc"
        self._seed_scrape_task(task_id, _scrape_jobs(2))
        resp = self.client.post("/api/scrape-result-save",
                                json={"task_id": task_id, "profile_summary": "画像"})
        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["saved"])
        self.assertEqual(body["result"]["jobs"][0]["verdict"], "")
        run = self.store.get_screening_run(body["run_id"])
        self.assertEqual(run["status"], "scraped_only")
        self.assertEqual(run["execution_params"].get("scrape_task_id"), task_id)
        # B038 审查修复：搜索参数（关键词/城市）随轮次保存，历史摘要可读。
        self.assertEqual(run["search_params"].get("keyword"), ["Python"])
        self.assertEqual(run["search_params"].get("city"), ["上海"])

    def test_save_is_idempotent_for_same_source(self):
        task_id = "scrape-idem"
        self._seed_scrape_task(task_id, _scrape_jobs(1))
        first = self.client.post("/api/scrape-result-save", json={"task_id": task_id}).get_json()
        second = self.client.post("/api/scrape-result-save", json={"task_id": task_id}).get_json()
        self.assertTrue(first["saved"])
        self.assertTrue(second["saved"])
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(len(self.store.list_history_rounds("boss")), 1)

    def test_latest_pipeline_result_by_run_id_accepts_scraped_only(self):
        task_id = "scrape-by-id"
        self._seed_scrape_task(task_id, _scrape_jobs(1))
        saved = self.client.post("/api/scrape-result-save", json={"task_id": task_id}).get_json()
        resp = self.client.get(f"/api/latest-pipeline-result?run_id={saved['run_id']}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["has_result"])
        self.assertEqual(data["status"], "scraped_only")

    def test_save_zero_jobs_returns_saved_false(self):
        task_id = "scrape-empty"
        self._seed_scrape_task(task_id, [])
        resp = self.client.post("/api/scrape-result-save", json={"task_id": task_id})
        body = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(body["ok"])
        self.assertFalse(body["saved"])
        self.assertEqual(len(self.store.list_history_rounds()), 0)

    def test_save_missing_task_id_rejected(self):
        resp = self.client.post("/api/scrape-result-save", json={})
        self.assertEqual(resp.status_code, 400)

    def test_save_unknown_task_not_found(self):
        resp = self.client.post("/api/scrape-result-save", json={"task_id": "nope"})
        self.assertEqual(resp.status_code, 404)

    def test_save_non_scrape_source_rejected(self):
        # run 存在但状态未完成（running）：重建路径无法认定来源 → 409
        task_id = "screen-abc"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO screening_runs (id, status, created_at, updated_at) "
                "VALUES (?, 'running', ?, ?)",
                (task_id, "2026-08-12T00:00:00", "2026-08-12T00:00:00"),
            )
        resp = self.client.post("/api/scrape-result-save", json={"task_id": task_id})
        self.assertEqual(resp.status_code, 409)


if __name__ == "__main__":
    unittest.main()
