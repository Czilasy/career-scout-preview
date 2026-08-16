import pathlib
import tempfile
import unittest

from webui.store import TaskStore


def _scrape_jobs(n=2, platform="boss"):
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


def _screened_jobs(n=2):
    jobs = _scrape_jobs(n)
    jobs[0]["verdict"] = "match"
    jobs[0]["verdict_reason"] = "经验匹配"
    if n > 1:
        jobs[1]["verdict"] = "uncertain"
        jobs[1]["verdict_reason"] = "JD 抓取失败"
        jobs[1]["failed_code"] = "detail_invalid"
    return jobs


class ScrapeOnlyStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    # -- 保存 ------------------------------------------------------------

    def test_save_scraped_only_snapshot_writes_undecided_round(self):
        run_id = self.store.save_scraped_only_snapshot(
            {"ok": True, "jobs": _scrape_jobs(), "dropped": [], "total_scraped": 2},
            {"platform": "boss", "keyword": "Python", "city": ["上海"]},
            scrape_task_id="scrape-1",
            platform="boss",
            profile_summary="3年Python后端",
        )
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["status"], "scraped_only")
        self.assertEqual(run["record_kind"], "result_snapshot")
        self.assertEqual(run["total_scraped"], 2)
        self.assertEqual(run["total_kept"], 2)
        self.assertEqual(run["match_count"], 0)
        self.assertEqual(run["execution_params"].get("scrape_task_id"), "scrape-1")

        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT verdict, is_dropped FROM screening_results WHERE run_id = ?",
                (run_id,),
            ).fetchall()
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_pending_results WHERE run_id = ?",
                (run_id,),
            ).fetchone()["n"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["verdict"] == "" for row in rows))
        self.assertTrue(all(row["is_dropped"] == 0 for row in rows))
        self.assertEqual(pending, 0)

    def test_scraped_only_round_enters_history(self):
        run_id = self.store.save_scraped_only_snapshot(
            {"ok": True, "jobs": _scrape_jobs(1), "dropped": [], "total_scraped": 1},
            {"platform": "boss"},
            scrape_task_id="scrape-1",
            platform="boss",
        )
        rounds = self.store.list_history_rounds("boss")
        self.assertEqual([r["id"] for r in rounds], [run_id])

    # -- 来源查询 --------------------------------------------------------

    def test_latest_scraped_only_for_source_finds_round(self):
        self.store.save_scraped_only_snapshot(
            {"ok": True, "jobs": _scrape_jobs(1), "dropped": [], "total_scraped": 1},
            {"platform": "boss"}, scrape_task_id="scrape-1", platform="boss",
        )
        run = self.store.latest_scraped_only_for_source("scrape-1")
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "scraped_only")
        self.assertIsNone(self.store.latest_scraped_only_for_source("scrape-other"))
        self.assertIsNone(self.store.latest_scraped_only_for_source(""))

    def test_latest_scraped_only_for_source_ignores_screened_rounds(self):
        self.store.save_pipeline_result(
            {"ok": True, "jobs": _screened_jobs(1), "dropped": [], "total_scraped": 1},
            {"platform": "boss"},
            execution_params={"scrape_task_id": "scrape-1"},
        )
        self.assertIsNone(self.store.latest_scraped_only_for_source("scrape-1"))

    # -- 升级 ------------------------------------------------------------

    def test_upgrade_scraped_run_keeps_position_and_rewrites_content(self):
        run_id = self.store.save_scraped_only_snapshot(
            {"ok": True, "jobs": _scrape_jobs(2), "dropped": [], "total_scraped": 2},
            {"platform": "boss"},
            scrape_task_id="scrape-1", platform="boss",
        )
        created_before = self.store.get_screening_run(run_id)["created_at"]

        self.store.upgrade_scraped_run(
            run_id,
            {"ok": True, "jobs": _screened_jobs(2), "dropped": [], "total_scraped": 2},
            {"platform": "boss", "screening": {"salary": ["20-30K"]}},
            status="done",
            platform="boss",
        )
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["status"], "partial")  # 有 pending 岗位自动降级
        self.assertEqual(run["created_at"], created_before)
        self.assertEqual(run["match_count"], 1)
        self.assertEqual(run["pending_count"], 1)
        self.assertEqual(run["search_params"].get("screening", {}).get("salary"), ["20-30K"])

        with self.store._connection() as conn:
            verdicts = [
                row["verdict"]
                for row in conn.execute(
                    "SELECT verdict FROM screening_results WHERE run_id = ?",
                    (run_id,),
                ).fetchall()
            ]
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_pending_results WHERE run_id = ?",
                (run_id,),
            ).fetchone()["n"]
        self.assertEqual(sorted(verdicts), ["match", "uncertain"])
        self.assertEqual(pending, 1)

    def test_upgrade_scraped_run_all_match_stays_done(self):
        run_id = self.store.save_scraped_only_snapshot(
            {"ok": True, "jobs": _scrape_jobs(1), "dropped": [], "total_scraped": 1},
            {"platform": "boss"}, scrape_task_id="scrape-1", platform="boss",
        )
        jobs = _screened_jobs(1)
        jobs[0]["verdict"] = "match"
        self.store.upgrade_scraped_run(
            run_id,
            {"ok": True, "jobs": jobs, "dropped": [], "total_scraped": 1},
            {"platform": "boss"}, status="done", platform="boss",
        )
        self.assertEqual(self.store.get_screening_run(run_id)["status"], "done")

    def test_upgrade_unknown_run_is_noop(self):
        # 未知 run_id 不报错也不产生内容（UPDATE 0 行 + DELETE 0 行）
        self.store.upgrade_scraped_run(
            "missing-run",
            {"ok": True, "jobs": _screened_jobs(1), "dropped": [], "total_scraped": 1},
            {"platform": "boss"}, status="done", platform="boss",
        )
        self.assertIsNone(self.store.get_screening_run("missing-run"))

    def test_upgrade_scraped_run_with_dropped_jobs_rewrites_history(self):
        run_id = self.store.save_scraped_only_snapshot(
            {"ok": True, "jobs": _scrape_jobs(2), "dropped": [], "total_scraped": 2},
            {"platform": "boss"}, scrape_task_id="scrape-1", platform="boss",
        )
        jobs = _screened_jobs(1)
        jobs[0]["verdict"] = "match"
        dropped = _scrape_jobs(2)[1:]
        dropped[0]["reason"] = "粗筛淘汰"
        self.store.upgrade_scraped_run(
            run_id,
            {
                "ok": True, "jobs": jobs, "dropped": dropped,
                "total_scraped": 2, "total_kept": 1, "total_dropped": 1,
            },
            {"platform": "boss"}, status="done", platform="boss",
        )
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["match_count"], 1)
        self.assertEqual(run["total_dropped"], 1)
        self.assertEqual([r["id"] for r in self.store.list_history_rounds("boss")], [run_id])

        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT verdict, is_dropped FROM screening_results WHERE run_id = ?", (run_id,),
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertIn({"verdict": "match", "is_dropped": 0}, [dict(r) for r in rows])
        self.assertIn({"verdict": "dropped", "is_dropped": 1}, [dict(r) for r in rows])

    # -- 最新轮白名单 ----------------------------------------------------
    def test_upgrade_archived_scraped_run_clears_archived_at(self):
        run_id = self.store.save_scraped_only_snapshot(
            {"ok": True, "jobs": _scrape_jobs(1), "dropped": [], "total_scraped": 1},
            {"platform": "boss"}, scrape_task_id="scrape-1", platform="boss",
        )
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET archived_at = ? WHERE id = ?",
                ("2026-08-01T00:00:00+08:00", run_id),
            )
        jobs = _screened_jobs(1)
        jobs[0]["verdict"] = "match"
        self.store.upgrade_scraped_run(
            run_id,
            {"ok": True, "jobs": jobs, "dropped": [], "total_scraped": 1},
            {"platform": "boss"}, status="done", platform="boss",
            scrape_task_id="scrape-1",
        )
        run = self.store.get_screening_run(run_id)
        self.assertIsNone(run["archived_at"])
        payload = self.store.load_latest_pipeline_result_for_platform("boss")
        self.assertEqual(payload["run_id"], run_id)

    def test_upgrade_scraped_run_preserves_parent_keyword(self):
        parent_id = "scrape-parent"
        self.store.create_screening_run(
            parent_id, source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "Python", "city": ["上海"]},
            },
        )
        run_id = self.store.save_scraped_only_snapshot(
            {"ok": True, "jobs": _scrape_jobs(1), "dropped": [], "total_scraped": 1},
            {"platform": "boss"}, scrape_task_id=parent_id, platform="boss",
        )
        self.store.upgrade_scraped_run(
            run_id,
            {"ok": True, "jobs": _screened_jobs(1), "dropped": [], "total_scraped": 1},
            {"platform": "boss", "screening": {"salary": ["20-30K"]}},
            status="done", platform="boss", scrape_task_id=parent_id,
        )
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["search_params"].get("keyword"), "Python")
        self.assertEqual(run["search_params"].get("city"), ["上海"])
        self.assertEqual(
            run["search_params"]["screening"]["salary"], ["20-30K"])

    def test_scraped_only_is_latest_result_with_raw_status(self):
        run_id = self.store.save_scraped_only_snapshot(
            {"ok": True, "jobs": _scrape_jobs(1), "dropped": [], "total_scraped": 1},
            {"platform": "boss"}, scrape_task_id="scrape-1", platform="boss",
        )
        payload = self.store.load_latest_pipeline_result_for_platform("boss")
        self.assertIsNotNone(payload)
        self.assertEqual(payload["run_id"], run_id)
        self.assertEqual(payload["status"], "scraped_only")
        self.assertEqual(payload["result"]["jobs"][0]["verdict"], "")

        latest = self.store.load_latest_pipeline_result()
        self.assertIsNotNone(latest)
        self.assertEqual(latest["status"], "scraped_only")

    def test_upgraded_round_exposes_screened_status(self):
        run_id = self.store.save_scraped_only_snapshot(
            {"ok": True, "jobs": _scrape_jobs(1), "dropped": [], "total_scraped": 1},
            {"platform": "boss"}, scrape_task_id="scrape-1", platform="boss",
        )
        jobs = _screened_jobs(1)
        jobs[0]["verdict"] = "match"
        self.store.upgrade_scraped_run(
            run_id,
            {"ok": True, "jobs": jobs, "dropped": [], "total_scraped": 1},
            {"platform": "boss"}, status="done", platform="boss",
        )
        payload = self.store.load_latest_pipeline_result_for_platform("boss")
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["result"]["jobs"][0]["verdict"], "match")


if __name__ == "__main__":
    unittest.main()
