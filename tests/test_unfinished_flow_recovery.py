"""045 聚焦测试：未完成流程统一找回、启动残留恢复与一次性提醒。

B101: 有列表岗位即可落轮进历史；0 岗位不成轮。
B103: 无 worker 的 queued/running/paused 有岗位残留启动可恢复；已落轮不提醒。
"""

import pathlib
import sys
import tempfile
import unittest

from webui.app import create_app
from webui import run_notice
from webui.result_rounds import save_scraped_only_round
from webui.store import TaskStore


def _jobs(count=2, platform="boss"):
    return [
        {
            "platform": platform,
            "platform_job_id": f"job-{index}",
            "job_id": f"job-{index}",
            "title": f"岗位 {index}",
            "company": "测试公司",
            "salary": "20-30K",
            "location": "上海",
            "source_url": f"https://example.com/{index}",
        }
        for index in range(count)
    ]


class UnfinishedRoundTests(unittest.TestCase):
    """B101：历史分界线是落轮，不要求 JD 或 AI。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")

    def test_list_only_jobs_enter_history_without_jd_or_ai(self):
        outcome = save_scraped_only_round(
            self.store,
            _jobs(1),
            platform="boss",
            scrape_task_id="b101-source",
            script_params={"keyword": "Python", "city": ["上海"]},
        )
        self.assertTrue(outcome["saved"])
        round_id = outcome["run_id"]
        run = self.store.get_screening_run(round_id)
        self.assertEqual(run["record_kind"], "result_snapshot")
        self.assertEqual(run["status"], "scraped_only")
        rows = self.store.list_history_rounds("boss")
        self.assertEqual([item["id"] for item in rows], [round_id])

    def test_zero_jobs_do_not_create_round(self):
        outcome = save_scraped_only_round(
            self.store,
            [],
            platform="boss",
            scrape_task_id="b101-empty",
        )
        self.assertFalse(outcome["saved"])
        self.assertIsNone(outcome["run_id"])
        self.assertEqual(self.store.list_history_rounds(), [])


class StartupResidueTests(unittest.TestCase):
    """B103：强杀/崩溃后留下的活状态必须能被启动恢复识别。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)

    def _create_residue(self, run_id, *, status, count=2, stage="scrape"):
        self.store.create_screening_run(
            run_id,
            source_count=5,
            profile_id="profile-a",
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "Python", "city": ["上海"]},
            },
        )
        # 状态机要求 queued → running 后才能 paused。
        if status in ("paused", "running"):
            self.store.update_screening_run(run_id, status="running")
        updates = {
            "current_stage": stage,
            "processed_count": count,
            "source_count": 5,
        }
        if status != "running":
            updates["status"] = status
        self.store.update_screening_run(run_id, **updates)
        # 残留运行态默认带 process_restart 语义；本 helper 只在调用方显式
        # 传 stage="" 时使用，用于测试重启映射。
        if count:
            self.store.save_scrape_combo_result(
                run_id,
                "python|shanghai",
                _jobs(count),
                ["python|shanghai"],
            )
        return run_id

    def test_paused_residue_returns_recovery(self):
        run_id = self._create_residue("b103-running", status="running", count=2)
        self.store.update_screening_run(
            "b103-running", status="paused", current_stage="scrape",
        )
        app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": pathlib.Path(self.temp.name) / "results",
            "DB_PATH": str(self.db_path),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        client = app.test_client()
        token = client.get("/api/session").get_json()["token"]
        client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        data = client.get(
            "/api/latest-running-task?profile_id=profile-a"
        ).get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["task_id"], run_id)
        self.assertEqual(data["status"], "paused")
        self.assertTrue(data["resumable"])

    def test_paused_residue_marks_notice_once(self):
        run_id = self._create_residue("b103-stale", status="running", count=2)
        self.store.update_screening_run(
            "b103-stale", status="paused", current_stage="scrape",
        )
        app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": pathlib.Path(self.temp.name) / "results",
            "DB_PATH": str(self.db_path),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        client = app.test_client()
        token = client.get("/api/session").get_json()["token"]
        client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        first = client.get("/api/latest-running-task?profile_id=profile-a").get_json()
        self.assertTrue(first["has_task"])
        self.assertEqual(first["task_id"], run_id)
        self.assertEqual(first["job_count"], 2)
        self.assertTrue(first["notice"]["message"])
        second = client.get("/api/latest-running-task?profile_id=profile-a").get_json()
        self.assertTrue(second["has_task"])
        self.assertIsNone(second["notice"])

    def test_zero_job_paused_residue_has_no_notice(self):
        """0 岗位的 paused 任务仍可续跑（UI 需要），但绝不能弹一次性提醒。"""
        self._create_residue("b103-zero", status="paused", count=0)
        app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": pathlib.Path(self.temp.name) / "results",
            "DB_PATH": str(self.db_path),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        client = app.test_client()
        token = client.get("/api/session").get_json()["token"]
        client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        data = client.get("/api/latest-running-task?profile_id=profile-a").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["job_count"], 0)
        self.assertIsNone(data["notice"])

    def test_finished_saved_round_is_not_notice_candidate(self):
        self._create_residue("b103-running", status="paused", count=1)
        jobs = self.store.load_scrape_run_jobs("b103-running")
        outcome = save_scraped_only_round(
            self.store,
            jobs,
            platform="boss",
            scrape_task_id="b103-running",
        )
        # 保存后来源已有 result_snapshot；has_newer_saved_result_than 保护下
        # paused 分支不再返回该流程，已保存轮不进入提醒候选。
        app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": pathlib.Path(self.temp.name) / "results",
            "DB_PATH": str(self.db_path),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        client = app.test_client()
        token = client.get("/api/session").get_json()["token"]
        client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        data = client.get("/api/latest-running-task?profile_id=profile-a").get_json()
        self.assertFalse(data["has_task"])
        self.assertEqual(
            [row["id"] for row in self.store.list_history_rounds()],
            [outcome["run_id"]],
        )

if __name__ == "__main__":
    unittest.main()


class UnfinishedFinishApiTests(unittest.TestCase):
    """B102 后端前置：桌面保存可复用现有 finish 入口。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]

    def _seed_running_scrape(self, run_id="b102-scrape", count=2):
        jobs = [
            {
                "job_id": f"j{i}", "platform_job_id": f"j{i}",
                "title": f"岗位{i}", "source_url": f"https://zhipin.example/j{i}.html",
            }
            for i in range(count)
        ]
        self.store.create_screening_run(
            run_id,
            source_count=count,
            profile_id="profile-a",
            execution_params={"platform": "boss", "script_params": {"keyword": "Python"}},
        )
        self.store.update_screening_run(
            run_id, status="running", current_stage="scrape", source_count=count,
        )
        self.store.save_scrape_combo_result(
            run_id, "python|shanghai", jobs, ["python|shanghai"],
        )
        return run_id

    def test_running_scrape_finishes_into_history(self):
        run_id = self._seed_running_scrape()
        response = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()
        self.assertTrue(data["ok"])
        rounds = self.store.list_history_rounds("boss")
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]["id"], data["snapshot_run_id"])
        self.assertEqual(rounds[0]["status"], "partial")
        self.assertEqual(rounds[0]["total_scraped"], 2)

    def test_zero_job_running_finish_creates_no_round(self):
        run_id = self._seed_running_scrape("b102-zero", count=0)
        response = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIsNone(response.get_json().get("snapshot_run_id"))
        self.assertEqual(self.store.list_history_rounds(), [])
