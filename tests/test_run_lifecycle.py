"""043 聚焦测试：未收尾流程的一次性提醒与轮次数据整条进出。

覆盖（提醒部分）：
- 记号幂等与水位；
- 首次不沉默 / 已标记沉默 / 更旧不接力 / 更新者有自己的机会；
- 灵动岛载荷（时间与岗位数口径）。
"""

import json
import pathlib
import tempfile
import unittest

from webui import run_notice
from webui.store import TaskStore


def _jobs(n=2, platform="zhilian"):
    return [
        {
            "platform": platform,
            "platform_job_id": f"job-{i}",
            "job_id": f"job-{i}",
            "title": f"岗位 {i}",
            "company": "测试公司",
            "salary": "20-30K",
            "location": "上海",
            "source_url": f"https://example.com/{i}",
        }
        for i in range(n)
    ]


class RunNoticeUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")

    def _round(self, *, n=3, platform="zhilian", scrape_task_id="scrape-1"):
        return self.store.save_scraped_only_snapshot(
            {"ok": True, "jobs": _jobs(n, platform), "dropped": [], "total_scraped": n},
            {"platform": platform},
            scrape_task_id=scrape_task_id,
            platform=platform,
        )

    def _backdate(self, run_id, created_at):
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET created_at = ? WHERE id = ?",
                (created_at, run_id),
            )

    def test_mark_is_idempotent(self):
        run_id = self._round()
        self.assertTrue(self.store.mark_run_notice_sent(run_id))
        self.assertFalse(self.store.mark_run_notice_sent(run_id))
        self.assertFalse(self.store.mark_run_notice_sent(""))
        run = self.store.get_screening_run(run_id)
        self.assertTrue(str(run.get("notice_sent_at") or ""))

    def test_watermark_none_then_time(self):
        self.assertIsNone(self.store.run_notice_watermark())
        run_id = self._round()
        self.store.mark_run_notice_sent(run_id)
        self.assertTrue(self.store.run_notice_watermark())

    def test_first_unfinished_round_not_silent(self):
        run_id = self._round()
        run = self.store.get_screening_run(run_id)
        self.assertFalse(run_notice.is_notice_silent(self.store, run))

    def test_marked_round_is_silent(self):
        run_id = self._round()
        self.store.mark_run_notice_sent(run_id)
        run = self.store.get_screening_run(run_id)
        self.assertTrue(run_notice.is_notice_silent(self.store, run))

    def test_older_round_after_watermark_stays_silent(self):
        first = self._round(scrape_task_id="scrape-1")
        self.assertTrue(run_notice.mark_notice_sent(self.store, first))
        older = self._round(scrape_task_id="scrape-0")
        self._backdate(older, "2000-01-01T00:00:00+08:00")
        older_run = self.store.get_screening_run(older)
        self.assertTrue(run_notice.is_notice_silent(self.store, older_run))
        newer = self._round(scrape_task_id="scrape-2")
        self._backdate(newer, "2099-01-01T00:00:00+08:00")
        newer_run = self.store.get_screening_run(newer)
        self.assertFalse(run_notice.is_notice_silent(self.store, newer_run))

    def test_watermark_survives_source_row_delete(self):
        """043 返修：删除已提醒的流程行后水位仍在——更旧者仍沉默（不接力）。"""
        first = self._round(scrape_task_id="scrape-1")
        self.assertTrue(run_notice.mark_notice_sent(self.store, first))
        older = self._round(scrape_task_id="scrape-0")
        self._backdate(older, "2000-01-01T00:00:00+08:00")
        self.assertTrue(self.store.delete_run_closure(first))
        self.assertTrue(self.store.run_notice_watermark())
        older_run = self.store.get_screening_run(older)
        self.assertTrue(run_notice.is_notice_silent(self.store, older_run))

    def test_watermark_profile_isolation(self):
        """043 返修：水位按画像隔离，不串台。"""
        mine = self.store.save_scraped_only_snapshot(
            {"ok": True, "jobs": _jobs(1), "dropped": [], "total_scraped": 1},
            {"platform": "zhilian"},
            scrape_task_id="scrape-p1", platform="zhilian", profile_id="p1",
        )
        self.assertTrue(self.store.mark_run_notice_sent(mine))
        self.assertIsNone(self.store.run_notice_watermark("p2"))
        self.assertTrue(self.store.run_notice_watermark("p1"))

    def test_payload_message_contains_date_and_count(self):
        run_id = self._round(n=39)
        run = self.store.get_screening_run(run_id)
        payload = run_notice.build_notice_payload(run, 39)
        self.assertEqual(payload["kind"], "run_notice")
        self.assertEqual(payload["run_id"], run_id)
        self.assertEqual(payload["job_count"], 39)
        self.assertIn("39", payload["message"])
        self.assertIn("只抓取、未筛选", payload["message"])
        self.assertIn("月", payload["message"])

    def test_none_run_is_silent(self):
        self.assertTrue(run_notice.is_notice_silent(self.store, None))
        self.assertIsNone(run_notice.build_notice_payload(None, 0))


class RunCleanupClosureTests(unittest.TestCase):
    """043 T012：手动整轮删除与永久保留。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")

    def _insert_process_log(self, run_id, *, platform="boss", status="succeeded",
                            created_at="2026-09-01T10:00:00+08:00", execution_params=None):
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO screening_runs (id, status, created_at, updated_at,"
                " execution_params_json, platform, record_kind)"
                " VALUES (?, ?, ?, ?, ?, ?, 'process_log')",
                (run_id, status, created_at, created_at,
                 json.dumps(execution_params or {}), platform),
            )

    def _save_round(self, scrape_task_id, *, platform="boss", n=2):
        return self.store.save_scraped_only_snapshot(
            {"ok": True, "jobs": _jobs(n, platform), "dropped": [], "total_scraped": n},
            {"platform": platform},
            scrape_task_id=scrape_task_id,
            platform=platform,
        )

    def _count(self, table, column, value):
        with self.store._connection() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = ?", (value,),
            ).fetchone()
        return int(row["n"])

    def test_delete_run_closure_removes_everything(self):
        self._insert_process_log("scrape-root")
        round_id = self._save_round("scrape-root")
        self._insert_process_log("ai-run-1", execution_params={"scrape_task_id": "scrape-root"})
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO whitebox_runs (id, owner_kind, owner_id, started_at, updated_at)"
                " VALUES ('wb-1', 'scrape', 'scrape-root', ?, ?)",
                ("2026-09-01T10:00:00", "2026-09-01T10:00:00"),
            )
        self.store.append_task_event("scrape-root", "stage_start", {"stage": "scrape"})
        self.assertGreater(self._count("screening_results", "run_id", round_id), 0)

        self.assertTrue(self.store.delete_run_closure(round_id))
        self.assertEqual(self._count("screening_runs", "id", round_id), 0)
        self.assertEqual(self._count("screening_runs", "id", "scrape-root"), 0)
        self.assertEqual(self._count("screening_runs", "id", "ai-run-1"), 0)
        self.assertEqual(self._count("screening_results", "run_id", round_id), 0)
        self.assertEqual(self._count("whitebox_runs", "id", "wb-1"), 0)
        self.assertEqual(self._count("task_logs", "task_id", "scrape-root"), 0)
        self.assertEqual(self._count("tasks", "id", "scrape-root"), 0)

    def test_delete_refuses_while_active_task_inside(self):
        from webui.run_cleanup import HistoryDeletionBlocked
        self._insert_process_log("scrape-active", status="running")
        round_id = self._save_round("scrape-active", n=1)
        with self.assertRaises(HistoryDeletionBlocked):
            self.store.delete_run_closure(round_id)
        self.assertIsNotNone(self.store.get_screening_run(round_id))
        self.assertIsNotNone(self.store.get_screening_run("scrape-active"))

    def test_delete_other_round_keeps_unrelated_closure(self):
        self._insert_process_log("scrape-a")
        round_a = self._save_round("scrape-a")
        self._insert_process_log("scrape-b")
        round_b = self._save_round("scrape-b")
        self.assertTrue(self.store.delete_run_closure(round_b))
        self.assertIsNotNone(self.store.get_screening_run(round_a))
        self.assertIsNotNone(self.store.get_screening_run("scrape-a"))
        self.assertGreater(self._count("screening_results", "run_id", round_a), 0)

    def test_startup_keeps_all_old_unowned_processes(self):
        import threading
        from webui.app import create_app
        for index in range(32):
            self._insert_process_log(f"retained-{index}")
        create_app({
            "TESTING": False, "START_TASKS": False,
            "DB_PATH": str(self.store.db_path),
            "RESULT_DIR": str(pathlib.Path(self.temp.name) / "results"),
        })
        for thread in threading.enumerate():
            if thread.name == "run-cleanup-sweep":
                thread.join(timeout=10)
        for index in range(32):
            self.assertIsNotNone(self.store.get_screening_run(f"retained-{index}"))



if __name__ == "__main__":
    unittest.main()
