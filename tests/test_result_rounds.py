"""Focus tests for the unified history-round write service (017).

宪法 IV：行为变化由失败测试先行定义。本文件定义 ``webui/result_rounds``
三个写入入口的契约：

- ``save_finished_round``：自然完成与结束保存共用；同流程（scrape_task_id +
  platform）已有轮原地升级不新增；0 岗位不成轮。
- ``save_scraped_only_round``：跳过筛选建轮；幂等；0 岗位不成轮。
- ``apply_recrawl_writeback``：重抓判定/JD 回写 + 计数重算 + 定稿时间刷新。
"""

import pathlib
import sys
import tempfile
import unittest
from datetime import datetime

from webui.app import create_app
from webui.result_history import ResultHistoryService
from webui.store import TaskStore

# 目标服务（T003 实现前 import 失败，本文件全部用例红）
from webui.result_rounds import (
    apply_recrawl_writeback,
    save_finished_round,
    save_scraped_only_round,
)

PLATFORM = "boss"
SCRAPE_TASK_ID = "scrape-task-017"


def _history_count(service, platform=None) -> int:
    return len(service.list_history(platform))


def _match_job(platform_job_id, verdict="match", platform=PLATFORM):
    return {
        "platform": platform,
        "platform_job_id": platform_job_id,
        "job_id": platform_job_id,
        "title": "后端工程师",
        "company": "示例科技",
        "salary": "25-40K",
        "location": "上海",
        "jd": "3 年 Python 后端经验",
        "verdict": verdict,
    }


def _result(verdicts=("match",), platform=PLATFORM):
    jobs = [
        _match_job(f"job-{index}", verdict, platform)
        for index, verdict in enumerate(verdicts)
    ]
    return {
        "ok": True,
        "jobs": jobs,
        "dropped": [],
        "total_scraped": len(jobs),
        "total_kept": len(jobs),
        "total_matched": sum(1 for v in verdicts if v == "match"),
        "total_dropped": 0,
        "profile_summary": "3年Python后端经验",
    }


def _script_params(platform=PLATFORM):
    return {"platform": platform, "keyword": "Python", "city": ["上海"]}


def _run_payload(store, run_id):
    return store.get_screening_run(run_id)


class SaveFinishedRoundTests(unittest.TestCase):
    """US2 核心：一条流程一条轮，结束保存/自然跑完共用同一写入入口。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        self.service = ResultHistoryService(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_natural_completion_saves_one_round(self):
        run_id = save_finished_round(
            self.store, _result(), _script_params(),
            scrape_task_id=SCRAPE_TASK_ID, status="done", platform=PLATFORM,
        )
        self.assertIsNotNone(run_id)
        items = self.service.list_history(PLATFORM)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["run_id"], run_id)
        self.assertEqual(items[0]["status"], "done")

    def test_finish_save_saves_one_partial_round(self):
        run_id = save_finished_round(
            self.store, _result(("uncertain",)), _script_params(),
            scrape_task_id=SCRAPE_TASK_ID, status="partial", platform=PLATFORM,
        )
        self.assertIsNotNone(run_id)
        items = self.service.list_history(PLATFORM)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "partial")
        self.assertEqual(items[0]["pending_count"], 1)

    def test_three_exits_each_produce_exactly_one_round(self):
        # 自然跑完
        natural_id = save_finished_round(
            self.store, _result(), _script_params(),
            scrape_task_id="exit-natural", status="done", platform=PLATFORM,
        )
        # 结束保存
        finish_id = save_finished_round(
            self.store, _result(("uncertain",)), _script_params(),
            scrape_task_id="exit-finish", status="partial", platform=PLATFORM,
        )
        # 跳过筛选
        outcome = save_scraped_only_round(
            self.store, [_match_job("s1", verdict="")],
            platform=PLATFORM, scrape_task_id="exit-scrape-only",
        )
        self.assertTrue(outcome["saved"])
        items = self.service.list_history(PLATFORM)
        self.assertEqual(len(items), 3)
        run_ids = {item["run_id"] for item in items}
        self.assertIn(natural_id, run_ids)
        self.assertIn(finish_id, run_ids)
        self.assertIn(outcome["run_id"], run_ids)

    def test_same_flow_second_finish_upgrades_not_duplicates(self):
        first_id = save_finished_round(
            self.store, _result(("match",)), _script_params(),
            scrape_task_id=SCRAPE_TASK_ID, status="done", platform=PLATFORM,
        )
        first_created = _run_payload(self.store, first_id)["created_at"]

        second_id = save_finished_round(
            self.store, _result(("match", "not_match")), _script_params(),
            scrape_task_id=SCRAPE_TASK_ID, status="done", platform=PLATFORM,
        )
        self.assertEqual(second_id, first_id)
        items = self.service.list_history(PLATFORM)
        self.assertEqual(len(items), 1)
        run = _run_payload(self.store, second_id)
        self.assertEqual(run["created_at"], first_created)
        self.assertEqual(run["match_count"], 1)
        self.assertEqual(run["mismatch_count"], 1)

    def test_scraped_only_then_finish_upgrades_in_place(self):
        outcome = save_scraped_only_round(
            self.store, [_match_job("s1", verdict="")],
            platform=PLATFORM, scrape_task_id=SCRAPE_TASK_ID,
        )
        self.assertTrue(outcome["saved"])
        scraped_id = outcome["run_id"]
        scraped_created = _run_payload(self.store, scraped_id)["created_at"]

        finished_id = save_finished_round(
            self.store, _result(("match", "not_match")), _script_params(),
            scrape_task_id=SCRAPE_TASK_ID, status="done", platform=PLATFORM,
        )
        self.assertEqual(finished_id, scraped_id)
        items = self.service.list_history(PLATFORM)
        self.assertEqual(len(items), 1)
        run = _run_payload(self.store, finished_id)
        self.assertEqual(run["created_at"], scraped_created)
        self.assertEqual(run["status"], "done")

    def test_same_scrape_task_different_platform_is_separate_flow(self):
        save_finished_round(
            self.store, _result(), _script_params(PLATFORM),
            scrape_task_id=SCRAPE_TASK_ID, status="done", platform=PLATFORM,
        )
        save_finished_round(
            self.store, _result(platform="zhilian"), _script_params("zhilian"),
            scrape_task_id=SCRAPE_TASK_ID, status="done", platform="zhilian",
        )
        items = self.service.list_history()
        self.assertEqual(len(items), 2)
        self.assertEqual({item["platform"] for item in items}, {PLATFORM, "zhilian"})

    def test_empty_result_creates_no_round(self):
        empty = {
            "ok": True, "jobs": [], "dropped": [],
            "total_scraped": 0, "total_kept": 0, "total_matched": 0,
            "total_dropped": 0, "profile_summary": "",
        }
        run_id = save_finished_round(
            self.store, empty, _script_params(),
            scrape_task_id=SCRAPE_TASK_ID, status="done", platform=PLATFORM,
        )
        self.assertIsNone(run_id)
        self.assertEqual(_history_count(self.service, PLATFORM), 0)


class SaveScrapedOnlyRoundTests(unittest.TestCase):
    """US2：跳过筛选出口，幂等建未筛选轮，0 岗位不成轮。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        self.service = ResultHistoryService(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_saves_scraped_only_round(self):
        outcome = save_scraped_only_round(
            self.store, [_match_job("s1", verdict="")],
            platform=PLATFORM, scrape_task_id=SCRAPE_TASK_ID,
        )
        self.assertTrue(outcome["saved"])
        self.assertIsNotNone(outcome["run_id"])
        items = self.service.list_history(PLATFORM)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "scraped_only")

    def test_idempotent_same_source_does_not_duplicate(self):
        first = save_scraped_only_round(
            self.store, [_match_job("s1", verdict="")],
            platform=PLATFORM, scrape_task_id=SCRAPE_TASK_ID,
        )
        second = save_scraped_only_round(
            self.store, [_match_job("s1", verdict=""), _match_job("s2", verdict="")],
            platform=PLATFORM, scrape_task_id=SCRAPE_TASK_ID,
        )
        # 幂等命中：不新建，但结果可用（saved=True，前端展示 result）
        self.assertTrue(second["saved"])
        self.assertEqual(second["run_id"], first["run_id"])
        self.assertEqual(_history_count(self.service, PLATFORM), 1)

    def test_empty_source_creates_no_round(self):
        outcome = save_scraped_only_round(
            self.store, [], platform=PLATFORM, scrape_task_id=SCRAPE_TASK_ID,
        )
        self.assertFalse(outcome["saved"])
        self.assertEqual(_history_count(self.service, PLATFORM), 0)


class ApplyRecrawlWritebackTests(unittest.TestCase):
    """US3：重抓回写原地更新计数并刷新定稿时间（轮身份与排序不变）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        self.service = ResultHistoryService(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def _round_with_pending(self, finished_at=None):
        run_id = save_finished_round(
            self.store, _result(("uncertain",)), _script_params(),
            scrape_task_id=SCRAPE_TASK_ID, status="partial",
            platform=PLATFORM, finished_at=finished_at,
        )
        run = _run_payload(self.store, run_id)
        self.assertEqual(run["pending_count"], 1)
        self.assertEqual(run["match_count"], 0)
        return run_id

    def test_writeback_refreshes_counts_and_finished_at(self):
        run_id = self._round_with_pending(finished_at="2026-01-01T00:00:00+08:00")
        created_at = _run_payload(self.store, run_id)["created_at"]
        old_finished = _run_payload(self.store, run_id)["finished_at"]

        recount = apply_recrawl_writeback(
            self.store, run_id,
            {"job-0": {"verdict": "match", "reason": "经验吻合"}},
            source_run_id=run_id,
        )
        self.assertIsNotNone(recount)
        run = _run_payload(self.store, run_id)
        self.assertEqual(run["match_count"], 1)
        self.assertEqual(run["mismatch_count"], 0)
        self.assertEqual(run["pending_count"], 0)
        self.assertEqual(run["status"], "done")
        # 定稿时间刷新到新时刻；创建时间与轮身份不变
        new_finished = run["finished_at"]
        self.assertGreater(
            datetime.fromisoformat(new_finished),
            datetime.fromisoformat(old_finished),
        )
        self.assertEqual(run["created_at"], created_at)
        items = self.service.list_history(PLATFORM)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["run_id"], run_id)
        self.assertEqual(items[0]["total_matched"], 1)

    def test_writeback_partial_verdict_keeps_pending(self):
        run_id = self._round_with_pending()
        apply_recrawl_writeback(
            self.store, run_id,
            {"job-0": {"verdict": "uncertain", "reason": "JD 仍缺失"}},
            source_run_id=run_id,
        )
        run = _run_payload(self.store, run_id)
        # 未判定成功：pending 保留，轮保持 partial
        self.assertEqual(run["pending_count"], 1)
        self.assertEqual(run["status"], "partial")


class ResultRoundFlowApiTests(unittest.TestCase):
    """US2 端到端操作序列：一条流程一条轮（017）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
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
        self.service = ResultHistoryService(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def _seed_scrape(self, scrape_id, count=3):
        jobs = [
            {"job_id": f"j{i}", "platform_job_id": f"j{i}", "title": f"岗位{i}",
             "source_url": f"https://zhipin.example/j{i}.html"}
            for i in range(count)
        ]
        self.store.create_screening_run(scrape_id, source_count=count)
        self.store.save_scrape_combo_result(scrape_id, "kw|city", jobs, ["kw|city"])
        return jobs

    def _seed_paused_ai_run(self, scrape_id, run_id):
        self.store.create_screening_run(
            run_id, source_count=3,
            execution_params={
                "scrape_task_id": scrape_id, "platform": "boss",
                "profile_summary": "测试画像", "profile_facts": {"years": 3},
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.save_screening_verdicts(
            run_id, {"j0": {"verdict": "match", "reason": "匹配"}})
        self.store.update_screening_run(
            run_id, status="paused", current_stage="jd_detail",
            processed_count=1, pending_count=2,
            total_kept=3, total_dropped=0,
        )
        return run_id

    def test_pause_then_finish_saves_exactly_one_round(self):
        """US2: 暂停→结束保存 = 历史恰 1 条（无暂停残影轮）。"""
        scrape_id = "flow-pause-finish-src"
        run_id = "flow-pause-finish"
        self._seed_scrape(scrape_id)
        self._seed_paused_ai_run(scrape_id, run_id)
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        items = self.service.list_history("boss")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "partial")

    def test_pause_resume_complete_saves_exactly_one_round(self):
        """US2: 暂停（无轮）→继续→自然跑完 = 历史恰 1 条。"""
        scrape_id = "flow-pause-resume-src"
        run_id = "flow-pause-resume"
        self._seed_scrape(scrape_id)
        self._seed_paused_ai_run(scrape_id, run_id)
        # 暂停路径不产生轮（US1 契约）
        self.assertEqual(self.store.list_history_rounds(), [])
        # 续跑完成后落库：同一流程（scrape_task_id）只产生一条轮
        completed_id = save_finished_round(
            self.store, _result(("match", "not_match", "match")), _script_params(),
            scrape_task_id=scrape_id, status="done", platform=PLATFORM,
        )
        items = self.service.list_history("boss")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["run_id"], completed_id)
        self.assertEqual(items[0]["status"], "done")

    def test_finish_twice_after_completion_returns_409(self):
        """US2: 完成后重复结束保存被拒绝（409 already_finished），历史不新增。"""
        scrape_id = "flow-finish-twice-src"
        run_id = "flow-finish-twice"
        self._seed_scrape(scrape_id)
        self._seed_paused_ai_run(scrape_id, run_id)
        first = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(first.status_code, 200, first.get_json())
        second = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()["error"], "already_finished")
        self.assertEqual(len(self.store.list_history_rounds()), 1)

    def test_finish_upgrades_existing_scraped_only_round_in_place(self):
        """US2: 跳过筛选建轮后结束保存 → 原地升级同轮，不新增、位置不变。

        当前实现（T010 前）finish 端点直写新建 → 双轮，本用例红。
        """
        scrape_id = "flow-scrape-only-finish-src"
        jobs = self._seed_scrape(scrape_id)
        outcome = save_scraped_only_round(
            self.store, jobs, platform=PLATFORM, scrape_task_id=scrape_id,
        )
        self.assertTrue(outcome["saved"])
        scraped_id = outcome["run_id"]
        created_at = _run_payload(self.store, scraped_id)["created_at"]
        run_id = "flow-scrape-only-finish"
        self._seed_paused_ai_run(scrape_id, run_id)
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        items = self.store.list_history_rounds("boss")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], scraped_id)
        self.assertEqual(items[0]["created_at"], created_at)
        self.assertEqual(items[0]["status"], "partial")


# ===========================================================================
# 020 US7：save_finished_round 瞬时锁短退避重试
# ===========================================================================
class _FlakyWriteStore:
    """最小桩：get_screening_run / 防重查询直通，写入口按脚本抛错。"""

    def __init__(self, failures):
        self._failures = list(failures)
        self.write_attempts = 0

    def get_screening_run(self, run_id):
        return None

    def latest_scraped_only_for_source(self, scrape_task_id):
        return None

    def list_history_rounds(self, platform=None):
        return []

    def upgrade_scraped_run(self, *args, **kwargs):
        raise AssertionError("不应走升级路径")

    def save_pipeline_result(self, *args, **kwargs):
        self.write_attempts += 1
        if self._failures:
            failure = self._failures.pop(0)
            if failure is not None:
                raise failure
        return f"round-{self.write_attempts}"


class SaveFinishedRoundRetryTests(unittest.TestCase):
    def setUp(self):
        import webui.result_rounds as result_rounds
        self.result_rounds = result_rounds
        self.sleeps: list[float] = []
        result_rounds._retry_sleep = self.sleeps.append

    def tearDown(self):
        self.result_rounds._retry_sleep = self.result_rounds._default_retry_sleep

    def test_transient_lock_retries_then_succeeds(self):
        import sqlite3
        store = _FlakyWriteStore([
            sqlite3.OperationalError("database is locked"),
            sqlite3.OperationalError("database is locked"),
        ])
        run_id = save_finished_round(
            store, _result(), _script_params(),
            scrape_task_id=SCRAPE_TASK_ID, status="done", platform=PLATFORM,
        )
        self.assertEqual(store.write_attempts, 3)
        self.assertEqual(len(self.sleeps), 2, "两次重试之间必须退避")
        self.assertEqual(run_id, "round-3")

    def test_non_operational_error_not_retried(self):
        store = _FlakyWriteStore([ValueError("boom")])
        with self.assertRaises(ValueError):
            save_finished_round(
                store, _result(), _script_params(),
                scrape_task_id=SCRAPE_TASK_ID, status="done", platform=PLATFORM,
            )
        self.assertEqual(store.write_attempts, 1)
        self.assertEqual(self.sleeps, [])


if __name__ == "__main__":
    unittest.main()
