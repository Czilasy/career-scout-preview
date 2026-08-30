"""webui.app 任务运行合同测试（027 自 tests/test_webui_app.py 拆出）。"""
import json
import pathlib
import sqlite3
import sys
import tempfile
import threading
import uuid
import unittest
from unittest import mock
from webui.app import create_app


class TaskFinishAndCountRegressionTests(unittest.TestCase):
    """结束并保存 + JD 阶段计数回归（用户反馈 606/24/37/930）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.result_dir = root / "results"
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(self.result_dir),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        self.temp.cleanup()

    def _seed_paused_ai_screen(self):
        scrape_id = "finish-scrape-src"
        jobs = [
            {"job_id": f"j{i:03d}", "title": f"岗位{i}",
             "source_url": f"https://zhipin.example/j{i:03d}.html"}
            for i in range(930)
        ]
        self.store.create_screening_run(scrape_id, source_count=930)
        self.store.save_scrape_combo_result(scrape_id, "kw|city", jobs, ["kw|city"])
        run_id = "finish-ai-screen-run"
        self.store.create_screening_run(
            run_id, source_count=930,
            execution_params={
                "scrape_task_id": scrape_id,
                "profile_summary": "测试画像",
                "profile_facts": {"core_skills": ["Python"], "job_type": "全职"},
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.save_screening_verdicts(run_id, {
            f"j{i:03d}": {"verdict": "dropped", "reason": "粗筛移除"}
            for i in range(667, 930)
        })
        self.store.update_screening_run(
            run_id, status="paused", current_stage="jd_detail",
            processed_count=606, pending_count=24,
            total_kept=667, total_dropped=263,
            error_code="source_rate_limited",
            error_reason="账号/操作频繁被限流",
        )
        self.result_dir.mkdir(parents=True, exist_ok=True)
        jd_map = {f"j{i:03d}": f"JD {i}" for i in range(606)}
        (self.result_dir / f"ai_screen_jd_{run_id}.json").write_text(
            json.dumps(jd_map), encoding="utf-8"
        )
        for i in range(606, 630):
            self.store.insert_pending_result(
                run_id, f"j{i:03d}", failure_stage="jd_detail",
                failed_code="source_rate_limited",
                ai_payload_json={"reason": "账号/操作频繁被限流"},
            )
        return run_id

    def test_task_state_jd_stage_uses_kept_total(self):
        run_id = "count-jd-kept"
        self.store.create_screening_run(run_id, source_count=930)
        self.store.update_screening_run(
            run_id, status="running", current_stage="jd_detail",
            processed_count=606, pending_count=24,
            total_kept=667, total_dropped=263,
        )
        self.store.update_screening_run(
            run_id, status="paused", error_code="source_rate_limited",
            error_reason="账号/操作频繁被限流",
        )
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertEqual(data["success_count"], 606)
        self.assertEqual(data["fail_count"], 24)
        self.assertEqual(data["unstarted_count"], 37)
        self.assertEqual(data["total"], 667)
        self.assertEqual(data["source_total"], 930)

    def test_task_state_done_stage_uses_kept_total(self):
        """完成阶段也按粗筛保留数统计，不再把剔除岗位显示成未开始。"""
        run_id = "count-done-kept"
        self.store.create_screening_run(run_id, source_count=311)
        self.store.update_screening_run(
            run_id, status="succeeded", current_stage="done",
            processed_count=246, match_count=133, mismatch_count=113,
            pending_count=0, total_kept=246, total_dropped=65,
        )
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertEqual(data["success_count"], 246)
        self.assertEqual(data["fail_count"], 0)
        self.assertEqual(data["unstarted_count"], 0)
        self.assertEqual(data["total"], 246)
        self.assertEqual(data["source_total"], 311)

    def test_task_state_fallback_matches_screen_stage_weights(self):
        """DB-only task-state 兜底百分比必须与 emit 权重一致且不提前到 100。"""
        cases = [
            ("screen_a", 50, 100, 12),
            ("fetch_jd", 10, 100, 30),
            ("screen_b", 98, 100, 99),
            ("screen_b", 100, 100, 100),
        ]
        for index, (stage, processed, total, expected) in enumerate(cases):
            run_id = f"state-weight-{stage}-{index}"
            self.store.create_screening_run(run_id, source_count=total)
            self.store.update_screening_run(
                run_id, status="running", current_stage=stage,
                processed_count=processed, total_kept=total,
            )
            data = self.client.get(f"/api/task-state/{run_id}").get_json()
            self.assertEqual(data["progress"]["overall_percent"], expected)

    def test_task_state_uses_recrawl_weights_for_live_and_db_fallback(self):
        """重抓 task-state 使用 60/40 权重，实时与 DB 兜底一致。"""
        run_id = "state-recrawl-db"
        self.store.create_screening_run(run_id, source_count=100)
        self.store.update_screening_run(
            run_id, status="running", current_stage="recrawl_fetch_jd",
            processed_count=50,
        )
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertEqual(data["progress"]["overall_percent"], 30)

        live_id = "state-recrawl-live"
        self.store.create_screening_run(live_id, source_count=100)
        self.store.update_screening_run(
            live_id, status="running", current_stage="screen_b",
            processed_count=50, total_kept=100,
        )
        self.app.config["PIPELINE_TASKS"][live_id] = {
            "kind": "recrawl", "status": "running", "stage": "screen_b",
            "progress": {"stage": "screen_b", "current": 50, "total": 100},
            "logs": [], "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
        }
        data = self.client.get(f"/api/task-state/{live_id}").get_json()
        self.assertEqual(data["progress"]["overall_percent"], 80)

    def test_latest_running_task_memory_branch_returns_stage(self):
        """刷新接回内存运行任务时，stage 必须随快照返回。"""
        tasks = self.app.config["PIPELINE_TASKS"]
        tasks["mem-stage-task"] = {
            "kind": "scrape",
            "status": "running",
            "stage": "risk_warning",
            "progress": {
                "stage": "searching", "current": 2, "total": 10,
                "overall_percent": 20,
            },
            "logs": [], "error": "", "started_at": 1000, "finished_at": None,
            "platform": "boss", "task_input_digest": "digest-1",
        }
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["stage"], "risk_warning")
        self.assertEqual(data["progress"]["overall_percent"], 20)

    def test_latest_running_task_memory_branch_returns_extended_fields(self):
        self.store.create_screening_run(
            "scrape-parent", source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "Python", "city": ["上海"]},
            },
        )
        self.store.create_screening_run(
            "mem-ext", source_count=1,
            frozen_filters={"salary": ["20-30K"]},
            execution_params={
                "platform": "boss",
                "scrape_task_id": "scrape-parent",
                "profile_summary": "3年Python后端",
                "profile_facts": {"years": 3},
            },
        )
        self.store.update_screening_run("mem-ext", status="running", current_stage="ai_rough")
        tasks = self.app.config["PIPELINE_TASKS"]
        tasks["mem-ext"] = {
            "kind": "ai_screen", "status": "running", "stage": "ai_rough",
            "progress": {}, "logs": [], "error": "", "started_at": 1000,
            "finished_at": None, "platform": "boss",
        }
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["scrape_task_id"], "scrape-parent")
        self.assertFalse(data["scrape_completed"])
        self.assertEqual(data["frozen_filters"], {"salary": ["20-30K"]})
        self.assertEqual(data["profile_summary"], "3年Python后端")
        self.assertEqual(data["profile_facts"], {"years": 3})
        self.assertEqual(data["round_context"]["keywords"], ["Python"])
        self.assertEqual(data["round_context"]["screen_run_id"], "mem-ext")

    def test_latest_running_task_memory_branch_falls_back_to_progress_stage(self):
        tasks = self.app.config["PIPELINE_TASKS"]
        tasks["mem-stage-fallback"] = {
            "kind": "ai_screen",
            "status": "queued",
            "progress": {"stage": "combo_done", "overall_percent": 10},
            "logs": [], "error": "", "started_at": 1000, "finished_at": None,
        }
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertEqual(data["stage"], "combo_done")

    def test_latest_running_task_skips_stale_paused_when_newer_result_saved(self):
        """已有更新的完成结果时，旧 paused 任务不再抢占刷新后的恢复提示。"""
        run_id = "stale-paused"
        self.store.create_screening_run(run_id, source_count=930)
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="ai_fine",
            error_code="ai_rate_limited", error_reason="AI 服务限流",
        )
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET updated_at = ? WHERE id = ?",
                ("2026-08-01T08:00:00+08:00", run_id),
            )
        self.store.save_pipeline_result(
            {
                "jobs": [], "dropped": [], "total_scraped": 930,
                "total_kept": 0, "total_dropped": 930, "profile_summary": "",
            },
            {},
        )
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertFalse(data["has_task"])

    def test_finish_paused_task_saves_partial_snapshot_and_latest(self):
        run_id = self._seed_paused_ai_screen()
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["status"], "completed_with_pending")
        self.assertEqual(len(data["result"]["jobs"]), 667)
        self.assertEqual(len(data["result"]["dropped"]), 263)
        self.assertEqual(data["result"]["total_scraped"], 930)
        self.assertEqual(data["result"]["total_kept"], 667)
        self.assertEqual(
            data["result"]["profile_facts"],
            {"core_skills": ["Python"], "job_type": "全职"},
        )
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertEqual(latest["status"], "completed_with_pending")
        self.assertEqual(latest["source_run_id"], data["snapshot_run_id"])
        self.assertEqual(
            latest["result"]["profile_facts"],
            {"core_skills": ["Python"], "job_type": "全职"},
        )
        ctx = latest.get("round_context") or {}
        self.assertEqual(ctx.get("status"), "partial")
        self.assertFalse(ctx.get("resumable"))
        finished = self.store.get_screening_run(run_id)
        self.assertEqual(finished["status"], "interrupted")
        self.assertEqual(finished["error_code"], "user_finished")
        latest_running = self.client.get("/api/latest-running-task").get_json()
        self.assertFalse(latest_running["has_task"])

    def test_finish_snapshot_preserves_parent_search_params(self):
        run_id = self._seed_paused_ai_screen()
        parent = self.store.get_screening_run("finish-scrape-src")
        ep = dict(parent.get("execution_params") or {})
        ep["script_params"] = {
            "keyword": "Python",
            "city": ["上海"],
            "locations": [{
                "platform": "boss", "city_name": "上海",
                "district_name": "浦东新区", "district_code": "310115",
            }],
        }
        self.store.update_screening_execution_params("finish-scrape-src", ep)
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        sp = latest.get("script_params") or {}
        self.assertEqual(sp.get("keyword"), "Python")
        self.assertEqual(sp.get("city"), ["上海"])
        self.assertEqual(sp.get("locations"), ep["script_params"]["locations"])

    def test_finish_restart_interrupted_ai_screen_saves_partial_snapshot(self):
        """服务重启中断的任务也能直接结束并保存部分结果，无需先重新开始。"""
        run_id = self._seed_paused_ai_screen()
        self.store.update_screening_run(
            run_id, status="interrupted", error_code="restart",
            error_reason="服务重启中断",
        )
        self.store.save_interruption_kind(run_id, "process_restart")
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["status"], "completed_with_pending")
        self.assertEqual(len(data["result"]["jobs"]), 667)
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertEqual(latest["source_run_id"], data["snapshot_run_id"])
        finished = self.store.get_screening_run(run_id)
        self.assertEqual(finished["error_code"], "user_finished")
        ctx = latest.get("round_context") or {}
        self.assertEqual(ctx.get("status"), "partial")
        self.assertFalse(ctx.get("resumable"))
        # finish 表示用户主动结束，kind 必须从 process_restart 改为
        # user_finished，否则公共状态仍显示 interrupted（可恢复），
        # 误导用户以为任务还能继续。
        self.assertEqual(finished["interruption_kind"], "user_finished")

    def test_finish_rejects_user_cancelled_interrupted_run(self):
        run_id = "finish-cancelled-run"
        self.store.create_screening_run(run_id, source_count=1)
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(
            run_id, status="cancelled", error_reason="用户已停止筛选")
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "interrupted_not_restartable")

    def test_finish_paused_scrape_run_saves_partial_snapshot(self):
        """列表抓取阶段暂停的任务也能结束并保存已抓岗位。"""
        run_id = "finish-scrape-run"
        jobs = [
            {"job_id": "s1", "title": "岗位1", "source_url": "https://zhipin.example/s1.html"},
            {"job_id": "s2", "title": "岗位2", "source_url": "https://zhipin.example/s2.html"},
        ]
        self.store.create_screening_run(run_id, source_count=len(jobs))
        self.store.save_scrape_combo_result(run_id, "kw|city", jobs, ["kw|city"])
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="账号/操作频繁被限流",
        )
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["status"], "completed_with_pending")
        self.assertEqual(data["result"]["total_scraped"], 2)
        self.assertEqual(data["result"]["total_kept"], 2)
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertEqual(latest["status"], "completed_with_pending")

    def test_finish_paused_scrape_run_preserves_profile_facts(self):
        """列表抓取提前结束时，快照必须继承父任务的隐藏画像事实。"""
        run_id = "finish-scrape-facts"
        jobs = [
            {"job_id": "s1", "title": "岗位1", "source_url": "https://zhipin.example/s1.html"},
        ]
        facts = {"core_skills": ["Python"], "job_type": "全职"}
        self.store.create_screening_run(
            run_id, source_count=len(jobs),
            execution_params={
                "platform": "boss",
                "profile_summary": "3年Python后端候选人",
                "profile_facts": facts,
            },
        )
        self.store.save_scrape_combo_result(run_id, "kw|city", jobs, ["kw|city"])
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="测试限流",
        )
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["result"]["profile_facts"], facts)
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertEqual(latest["result"]["profile_facts"], facts)

    def _seed_scrape_run(self, run_id, count, status, platform="boss",
                        error_code=None, error_reason=None):
        jobs = [
            {"job_id": f"j{i}", "platform_job_id": f"j{i}", "title": f"岗位{i}",
             "source_url": f"https://{platform}.example/j{i}.html"}
            for i in range(count)
        ]
        self.store.create_screening_run(
            run_id, source_count=count,
            execution_params={"platform": platform},
        )
        self.store.save_scrape_combo_result(run_id, "kw|city", jobs, ["kw|city"])
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        if status == "interrupted":
            self.store.update_screening_run(
                run_id, status="interrupted", current_stage="scrape",
                error_code=error_code or "restart",
                error_reason=error_reason or "服务重启中断",
            )
            self.store.save_interruption_kind(run_id, "process_restart")
        else:
            self.store.update_screening_run(
                run_id, status=status, current_stage="scrape",
                error_code=error_code, error_reason=error_reason,
            )
        return jobs

    def test_latest_running_task_restores_failed_scrape_with_real_count(self):
        self._seed_scrape_run(
            "recover-failed", 1280, "failed", platform="boss",
            error_code="scrape_failed", error_reason="列表抓取失败",
        )
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["kind"], "scrape")
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["scraped_count"], 1280)
        self.assertEqual(data["source_total"], 1280)
        self.assertEqual(data["platform"], "boss")
        self.assertEqual(data["scrape_task_id"], "recover-failed")

    def test_latest_running_task_restores_completed_plain_scrape(self):
        run_id = "recover-plain-completed"
        jobs = [{
            "job_id": "j1", "platform_job_id": "j1", "title": "岗位1",
            "source_url": "https://zhipin.example/j1.html",
        }]
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "Python", "city": ["上海"]},
                "profile_summary": "3年Python后端",
                "profile_facts": {"years": 3},
            },
        )
        self.store.save_scrape_combo_result(run_id, "kw|city", jobs, ["kw|city"])
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(run_id, status="succeeded", current_stage="scrape")
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["kind"], "scrape")
        self.assertEqual(data["scrape_task_id"], run_id)
        self.assertTrue(data["scrape_completed"])
        self.assertEqual(data["profile_summary"], "3年Python后端")
        self.assertEqual(data["profile_facts"], {"years": 3})
        self.assertEqual(data["round_context"]["keywords"], ["Python"])
        saved = self.client.post("/api/scrape-result-save", json={"task_id": run_id}).get_json()
        self.assertTrue(saved["saved"])
        self.assertFalse(self.client.get("/api/latest-running-task").get_json()["has_task"])

    def test_latest_running_task_restores_paused_and_interrupted_counts(self):
        self._seed_scrape_run(
            "recover-paused", 12, "paused", platform="boss",
            error_code="captcha_required", error_reason="验证码",
        )
        paused = self.client.get("/api/latest-running-task").get_json()
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["scraped_count"], 12)

    def test_latest_running_task_zhilian_pause_reason_has_no_boss_fallback(self):
        self._seed_scrape_run(
            "recover-zhilian-pause", 3, "paused", platform="zhilian",
            error_code="source_login_required", error_reason="",
        )
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertEqual(data["pause_info"]["error_reason"], "智联登录已失效")
        self.assertNotIn("BOSS", data["progress"]["message"])

    def test_latest_running_task_restores_interrupted_count(self):
        self._seed_scrape_run(
            "recover-interrupted", 8, "interrupted", platform="zhilian",
        )
        interrupted = self.client.get("/api/latest-running-task").get_json()
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertEqual(interrupted["scraped_count"], 8)
        self.assertEqual(interrupted["platform"], "zhilian")
        self.assertEqual(interrupted["kind"], "scrape")
        self.assertIn("上次抓取", interrupted["progress"]["message"])

    def test_latest_running_task_skips_failed_when_newer_result_saved(self):
        self._seed_scrape_run("recover-stale-failed", 5, "failed")
        self.store.save_pipeline_result({
            "jobs": [], "dropped": [], "total_scraped": 5, "total_kept": 0,
            "total_dropped": 5, "profile_summary": "",
        }, {})
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertFalse(data["has_task"])

    def test_latest_running_task_true_zero_jobs_still_zero(self):
        run_id = "recover-zero"
        self.store.create_screening_run(run_id, source_count=0)
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(run_id, status="failed", current_stage="scrape")
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertFalse(data["has_task"])

    def test_latest_running_task_reconciles_orphaned_running_scrape(self):
        """数据已抓完但终态漏写的 running 抓取，刷新时自动补写完成。"""
        run_id = "orphan-running-complete"
        jobs = [
            {"job_id": "j1", "platform_job_id": "j1", "title": "岗位1",
             "source_url": "https://zhipin.example/j1.html"},
            {"job_id": "j2", "platform_job_id": "j2", "title": "岗位2",
             "source_url": "https://zhipin.example/j2.html"},
        ]
        self.store.create_screening_run(
            run_id, source_count=2,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "AI", "city": ["深圳"]},
            },
        )
        self.store.save_scrape_combo_result(run_id, "kw|city1", [jobs[0]], ["kw|city1"])
        self.store.save_scrape_combo_result(
            run_id, "kw|city2", [jobs[1]], ["kw|city1", "kw|city2"])
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")

        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["kind"], "scrape")
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["current_stage"], "scrape")
        events = self.store.list_task_events(run_id)
        self.assertEqual(
            sum(1 for e in events if e["type"] == "stage_complete"), 1)
        # 幂等：再次恢复不再重复补写完成事件。
        again = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(again["has_task"])
        events = self.store.list_task_events(run_id)
        self.assertEqual(
            sum(1 for e in events if e["type"] == "stage_complete"), 1)

    def test_finish_failed_scrape_run_saves_partial_snapshot(self):
        self._seed_scrape_run(
            "finish-failed-scrape", 3, "failed", platform="zhilian",
            error_code="scrape_failed", error_reason="列表抓取失败",
        )
        resp = self.client.post("/api/task/finish/finish-failed-scrape")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["status"], "completed_with_pending")
        self.assertEqual(data["platform"], "zhilian")
        self.assertEqual(data["result"]["total_scraped"], 3)
        finished = self.store.get_screening_run("finish-failed-scrape")
        self.assertEqual(finished["status"], "interrupted")
        self.assertEqual(finished["error_code"], "user_finished")

    def test_finish_running_scrape_run_saves_partial_snapshot(self):
        self._seed_scrape_run("finish-running-scrape", 4, "running")
        resp = self.client.post("/api/task/finish/finish-running-scrape")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["result"]["total_scraped"], 4)
        self.assertEqual(len(data["result"]["jobs"]), 4)
        finished = self.store.get_screening_run("finish-running-scrape")
        self.assertEqual(finished["error_code"], "user_finished")

    def test_finish_without_snapshot_saves_empty_result(self):
        """0 岗位结束保存：允许 finish 并保存空快照，空快照不进历史列表。"""
        run_id = "finish-no-snapshot"
        self.store.create_screening_run(run_id, source_count=0)
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["status"], "completed_with_pending")
        self.assertEqual(data["result"]["total_scraped"], 0)
        self.assertEqual(len(data["result"]["jobs"]), 0)
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["error_code"], "user_finished")
        self.assertEqual(run["interruption_kind"], "user_finished")
        self.assertEqual(self.store.list_history_rounds(), [])

    def test_finish_twice_returns_explicit_conflict(self):
        self._seed_scrape_run("finish-twice", 2, "paused")
        first = self.client.post("/api/task/finish/finish-twice")
        self.assertEqual(first.status_code, 200)
        second = self.client.post("/api/task/finish/finish-twice")
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()["error"], "already_finished")

    def test_cancel_after_finish_returns_friendly_conflict(self):
        """用户已结束保存的任务再点取消，返回明确冲突而不改写终态。"""
        self._seed_scrape_run("finish-cancel-guard", 2, "paused")
        first = self.client.post("/api/task/finish/finish-cancel-guard")
        self.assertEqual(first.status_code, 200)
        resp = self.client.post("/api/task/cancel/finish-cancel-guard")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "already_finished")
        run = self.store.get_screening_run("finish-cancel-guard")
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["error_code"], "user_finished")

    def test_latest_pipeline_result_returns_parent_scrape_task_id(self):
        self._seed_scrape_run("finish-parent-link", 2, "paused", platform="boss")
        resp = self.client.post("/api/task/finish/finish-parent-link")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertEqual(latest["scrape_task_id"], "finish-parent-link")
        self.assertEqual(latest["platform"], "boss")

    def test_finish_partial_jobs_and_dropped_carry_platform(self):
        self._seed_scrape_run(
            "finish-zhilian-partial", 3, "paused", platform="zhilian",
            error_code="source_rate_limited", error_reason="操作频繁",
        )
        self.store.save_screening_verdicts("finish-zhilian-partial", {
            "j0": {"verdict": "dropped", "reason": "粗筛移除"},
        })
        resp = self.client.post("/api/task/finish/finish-zhilian-partial")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        result = resp.get_json()["result"]
        self.assertTrue(result["jobs"])
        self.assertTrue(result["dropped"])
        for job in result["jobs"]:
            self.assertEqual(job["platform"], "zhilian")
        for job in result["dropped"]:
            self.assertEqual(job["platform"], "zhilian")

    def test_task_state_zhilian_pause_info_has_no_boss_text(self):
        run_id = "zhilian-pause-info"
        self.store.create_screening_run(
            run_id, source_count=1, execution_params={"platform": "zhilian"},
        )
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_login_required", error_reason="",
        )
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertIn("智联", data["pause_info"]["error_reason"])
        self.assertNotIn("BOSS", data["pause_info"]["error_reason"])

    def test_finish_normalizes_mismatch_verdict(self):
        """partial 快照把历史 mismatch 归一为 not_match，避免待确认计数膨胀。"""
        run_id = "finish-mismatch"
        jobs = [
            {"job_id": "m1", "title": "岗位", "source_url": "https://zhipin.example/m1.html"},
        ]
        scrape_id = "finish-mismatch-src"
        self.store.create_screening_run(scrape_id, source_count=1)
        self.store.save_scrape_combo_result(scrape_id, "kw|city", jobs, ["kw|city"])
        self.store.create_screening_run(
            run_id, source_count=1, execution_params={"scrape_task_id": scrape_id},
        )
        self.store.update_screening_run(run_id, status="running", current_stage="jd_detail")
        self.store.save_screening_verdicts(run_id, {
            "m1": {"verdict": "mismatch", "reason": "不符合"},
        })
        self.store.update_screening_run(
            run_id, status="paused", current_stage="jd_detail",
            processed_count=0, total_kept=1, total_dropped=0,
        )
        self.result_dir.mkdir(parents=True, exist_ok=True)
        (self.result_dir / f"ai_screen_jd_{run_id}.json").write_text("{}", encoding="utf-8")
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["result"]["jobs"][0]["verdict"], "not_match")

    def test_finish_recrawl_partial_preserves_reason_caveats_and_dropped(self):
        """重抓中途结束：partial 快照必须保留来源 reason/caveats，淘汰不能归零。"""
        source_result = {
            "jobs": [
                {"job_id": "j1", "title": "岗位A", "company": "公司A",
                 "salary": "10K", "location": "东莞", "tags": "1-3年", "jd": "JD A",
                 "source_url": "https://zhipin.example/j1.html",
                 "verdict": "match", "verdict_reason": "技能匹配", "caveats": ["注意学历"]},
                {"job_id": "j2", "title": "岗位B", "company": "公司B",
                 "salary": "8K", "location": "东莞", "tags": "1-3年", "jd": "JD B",
                 "source_url": "https://zhipin.example/j2.html",
                 "verdict": "uncertain", "verdict_reason": "AI 漏判，待确认", "caveats": []},
            ],
            "dropped": [
                {"job_id": "d1", "title": "淘汰岗", "reason": "经验不符",
                 "canonical_url": "https://zhipin.example/d1.html"},
            ],
            "total_scraped": 3, "total_kept": 2, "total_matched": 1,
            "total_dropped": 1, "profile_summary": "画像", "error": "",
        }
        source_id = self.store.save_pipeline_result(source_result, {})
        recrawl_id = "finish-recrawl-partial"
        self.store.create_screening_run(
            recrawl_id, source_count=2,
            execution_params={"source_run_id": source_id, "profile_summary": "画像"},
        )
        self.store.update_screening_run(
            recrawl_id, status="running", current_stage="recrawl_fetch_jd",
        )
        self.store.update_screening_run(
            recrawl_id, status="paused", current_stage="recrawl_ai",
            error_code="ai_network_error", error_reason="AI 网络故障",
        )
        resp = self.client.post(f"/api/task/finish/{recrawl_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        result = resp.get_json()["result"]
        self.assertEqual(result["total_scraped"], 3)
        self.assertEqual(result["total_kept"], 2)
        self.assertEqual(result["total_dropped"], 1)
        self.assertEqual(len(result["dropped"]), 1)
        self.assertEqual(result["dropped"][0]["platform_job_id"], "d1")
        self.assertEqual(result["dropped"][0]["reason"], "经验不符")
        jobs = {job["platform_job_id"]: job for job in result["jobs"]}
        self.assertEqual(jobs["j1"]["verdict_reason"], "技能匹配")
        self.assertEqual(jobs["j1"]["caveats"], ["注意学历"])
        self.assertEqual(jobs["j2"]["verdict_reason"], "AI 漏判，待确认")

    def test_recrawl_writeback_reflects_in_latest_result_without_new_snapshot(self):
        """重抓写回来源 run 后，latest-pipeline-result 应实时反映新判定和淘汰。"""
        source_id = self.store.save_pipeline_result({
            "jobs": [
                {"job_id": "j1", "title": "岗位", "verdict": "uncertain",
                 "verdict_reason": "待确认", "caveats": []},
            ],
            "dropped": [{"job_id": "d1", "title": "淘汰", "reason": "粗筛移除"}],
            "total_scraped": 2, "total_kept": 1, "total_matched": 0,
            "total_dropped": 1, "profile_summary": "画像", "error": "",
        }, {})
        self.store.insert_pending_result(
            source_id, "j1", failure_stage="ai_fine", failed_code="ai_missing_job",
        )
        self.store.save_screening_verdicts(source_id, {
            "j1": {"verdict": "not_match", "reason": "重判不匹配", "caveats": ["新提示"]},
        })
        self.store.delete_pending_result(source_id, "j1")
        self.store.recount_pipeline_result(source_id)
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertEqual(latest["status"], "completed")
        job = latest["result"]["jobs"][0]
        self.assertEqual(job["verdict"], "not_match")
        self.assertEqual(job["verdict_reason"], "重判不匹配")
        self.assertEqual(job["caveats"], ["新提示"])
        self.assertEqual(latest["result"]["total_dropped"], 1)

    def test_finish_rejects_non_paused_run(self):
        run_id = "finish-not-paused"
        self.store.create_screening_run(run_id, source_count=1)
        self.store.update_screening_run(
            run_id, status="succeeded", current_stage="done")
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "already_terminal")

    def test_recrawl_without_source_run_id_rejected(self):
        """017-US4: 重抓必须显式携带目标轮；缺省即使存在最新轮也 409。"""
        source_id = self.store.save_pipeline_result({
            "jobs": [{"job_id": "j1", "platform_job_id": "j1", "title": "岗位",
                      "verdict": "uncertain", "verdict_reason": "待确认",
                      "caveats": []}],
            "dropped": [], "total_scraped": 1, "total_kept": 1,
            "total_matched": 0, "total_dropped": 0, "profile_summary": "画像",
            "error": "",
        }, {"platform": "boss"})
        self.store.insert_pending_result(
            source_id, "j1", failure_stage="ai_fine", failed_code="ai_missing_job",
        )
        resp = self.client.post("/api/pipeline/recrawl", json={
            "job_ids": ["j1"], "profile_summary": "画像",
        })
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "missing_source_run_id")

    def test_jd_refetch_without_source_run_id_rejected(self):
        """017-US4: 单岗位 JD 补抓必须携带目标轮；缺省被拒绝。"""
        resp = self.client.post("/api/pipeline/jobs/j1/jd", json={
            "source_url": "https://zhipin.example/j1.html",
            "profile_summary": "画像",
        })
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "missing_source_run_id")

    def test_legacy_reset_latest_result_endpoint_removed(self):
        """017-US4: 旧结果清空端点已删除；归档/删除统一走历史接口。"""
        resp = self.client.post("/api/reset-latest-result", json={})
        self.assertEqual(resp.status_code, 404)

    def _save_zhilian_pipeline_result(self):
        return self.store.save_pipeline_result({
            "jobs": [
                {"job_id": "j1", "platform_job_id": "pj1", "title": "匹配岗",
                 "company": "公司A", "salary": "2-3万", "location": "上海",
                 "experience": "1-3年", "degree": "本科",
                 "canonical_url": "https://zhaopin.example/j1.htm",
                 "verdict": "match", "verdict_reason": "", "caveats": []},
            ],
            "dropped": [
                {"job_id": "d1", "platform_job_id": "pd1", "title": "淘汰岗",
                 "reason": "薪资低于期望",
                 "canonical_url": "https://zhaopin.example/d1.htm"},
            ],
            "total_scraped": 2, "total_kept": 1, "total_matched": 1,
            "total_dropped": 1, "profile_summary": "画像", "error": "",
        }, {"platform": "zhilian"})

    def test_platform_latest_result_keeps_job_links(self):
        """按平台读取结果快照时，岗位链接必须从 source_url 列回填。"""
        self._save_zhilian_pipeline_result()
        latest = self.client.get(
            "/api/latest-pipeline-result?platform=zhilian").get_json()
        self.assertTrue(latest["has_result"])
        self.assertEqual(
            latest["result"]["jobs"][0]["canonical_url"],
            "https://zhaopin.example/j1.htm",
        )
        self.assertEqual(
            latest["result"]["dropped"][0]["canonical_url"],
            "https://zhaopin.example/d1.htm",
        )

    def test_platform_latest_result_keeps_verdict_fields(self):
        """R1: 按平台读取结果快照时 verdict 系字段必须完整（曾漏字段导致全部进“待确认”）。

        jobs 必须带 verdict/verdict_reason/caveats/tags/extra；
        total_matched 取 match_count 而非 total_scraped；
        partial 快照映射为 completed_with_pending 与全量路径一致。
        """
        self.store.save_pipeline_result({
            "jobs": [
                {"job_id": "j1", "platform_job_id": "pj1", "title": "匹配岗",
                 "company": "公司A", "salary": "2-3万", "location": "上海",
                 "tags": "Python", "jd": "岗位描述",
                 "canonical_url": "https://zhaopin.example/j1.htm",
                 "verdict": "match", "verdict_reason": "技能匹配",
                 "caveats": ["注意学历"], "extra": {"company_nature_label": "国企"}},
                {"job_id": "j2", "platform_job_id": "pj2", "title": "待确认岗",
                 "canonical_url": "https://zhaopin.example/j2.htm",
                 "verdict": "uncertain", "verdict_reason": "AI 漏判",
                 "caveats": ["薪资待确认"], "extra": {}},
            ],
            "dropped": [],
            "total_scraped": 2, "total_kept": 2, "total_matched": 1,
            "total_dropped": 0, "profile_summary": "画像", "error": "",
        }, {"platform": "zhilian"})
        latest = self.client.get(
            "/api/latest-pipeline-result?platform=zhilian").get_json()
        self.assertTrue(latest["has_result"])
        self.assertEqual(latest["status"], "completed_with_pending")
        jobs = {job["platform_job_id"]: job for job in latest["result"]["jobs"]}
        self.assertEqual(jobs["pj1"]["verdict"], "match")
        self.assertEqual(jobs["pj1"]["verdict_reason"], "技能匹配")
        self.assertEqual(jobs["pj1"]["caveats"], ["注意学历"])
        self.assertEqual(jobs["pj1"]["tags"], "Python")
        self.assertEqual(jobs["pj1"]["jd"], "岗位描述")
        self.assertEqual(jobs["pj1"]["extra"], {"company_nature_label": "国企"})
        self.assertEqual(jobs["pj2"]["verdict"], "uncertain")
        self.assertEqual(jobs["pj2"]["caveats"], ["薪资待确认"])
        # total_matched 必须取 match_count（1），而不是 total_scraped（2）
        self.assertEqual(latest["result"]["total_matched"], 1)
        self.assertEqual(latest["result"]["total_kept"], 2)
        self.assertEqual(latest["result"]["profile_summary"], "画像")

    def test_pipeline_export_csv_groups_matched_before_dropped_with_links(self):
        self._save_zhilian_pipeline_result()

        response = self.client.get(
            "/api/pipeline-result/export.csv?platform=zhilian")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.content_type)
        self.assertIn("career_scout_jobs_zhilian.csv", response.headers["Content-Disposition"])
        lines = [
            line for line in response.get_data(as_text=True).splitlines()
            if line.strip()
        ]
        titles = [line.lstrip("\ufeff").split(",")[0] for line in lines]
        self.assertEqual(titles, ["title", "匹配：", "匹配岗", "不匹配：", "淘汰岗"])
        self.assertIn("https://zhaopin.example/j1.htm", lines[2])
        self.assertIn("薪资低于期望", lines[4])
        self.assertIn("https://zhaopin.example/d1.htm", lines[4])

    def test_pipeline_export_csv_without_result_returns_not_found(self):
        response = self.client.get(
            "/api/pipeline-result/export.csv?platform=zhilian")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error_code"], "not_found")

    def test_pipeline_export_csv_by_run_id_accepts_done_snapshot(self):
        """前端按当前结果的 run_id 导出：done 状态快照必须可导出。"""
        run_id = self._save_zhilian_pipeline_result()

        response = self.client.get(
            f"/api/pipeline-result/export.csv?run_id={run_id}")

        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn("匹配：", text)
        self.assertIn("不匹配：", text)


class ScreenContinueFlowTests(unittest.TestCase):
    """013：暂停路由、round_context 透传与续跑候选契约。"""

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

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _create_completed_scrape_run(self, script_params=None):
        run_id = f"scrape_{uuid.uuid4().hex[:8]}"
        self.store.create_screening_run(
            run_id,
            frozen_filters={},
            source_count=2,
            execution_params={
                "platform": "boss",
                "script_params": script_params or {
                    "keyword": "Python,后端", "city": ["上海"], "pages": 3,
                },
                "execution_config": {
                    "schema_version": 1, "inter_combo_delay": 10,
                    "detail_batch_size": 15, "detail_interval": 2,
                    "detail_reset_every": 4, "detail_batch_cooldown": 5,
                    "detail_tab_pool_size": 5, "screen_batch_size": 50,
                    "screen_concurrency": 5, "match_batch_size": 4,
                    "match_concurrency": 10, "config_digest": None,
                },
                "frozen_scope": {
                    "schema_version": 1, "platform": "boss",
                    "keywords": ["Python"], "scope_kind": "cities",
                    "cities": ["上海"], "pages_per_combination": 3,
                    "combination_count": 1, "planned_pages": 3,
                    "task_size": "small", "scope_digest": None,
                },
            },
            backend_version="test",
        )
        self.store.update_screening_run(
            run_id, status="succeeded", current_stage="done",
            processed_count=2, match_count=2,
        )
        jobs = [
            {"job_id": "j1", "platform_job_id": "j1", "title": "岗位",
             "source_url": "https://zhipin.example/j1.html"},
        ]
        self.store.save_scrape_combo_result(run_id, "kw|city", jobs, ["kw|city"])
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "done", "progress": {}, "logs": [],
            "result": {"ok": True, "jobs": jobs, "total_scraped": 1,
                       "total_matched": 1, "completed_combos": ["kw|city"]},
            "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(), "platform": "boss",
        }
        return run_id

    def _seed_ai_run(self, scrape_id, run_id, status="paused",
                    error_code=None, filters=None):
        self.store.create_screening_run(
            run_id,
            frozen_filters=filters or {"salary": ["20-30K"]},
            source_count=2,
            execution_params={
                "platform": "boss",
                "scrape_task_id": scrape_id,
                "profile_summary": "测试画像",
                "profile_facts": {"years": 3},
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(run_id, status=status)
        if error_code:
            self.store.update_screening_run(
                run_id, error_code=error_code, error_reason="用户提前结束")

    def test_pause_route_returns_pausing_and_sets_stop_mode(self):
        scrape_id = self._create_completed_scrape_run()
        run_id = "pause-screen-run"
        self._seed_ai_run(scrape_id, run_id, status="running")
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "ai_screen", "status": "running", "progress": {}, "logs": [],
            "error": "", "stop_event": threading.Event(), "platform": "boss",
        }
        resp = self.client.post(f"/api/task/pause/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["status"], "pausing")
        task = self.app.config["PIPELINE_TASKS"][run_id]
        self.assertEqual(task["stop_mode"], "pause")
        self.assertTrue(task["stop_event"].is_set())

    def test_pause_route_rejects_non_ai_and_terminal(self):
        run_id = "pause-reject"
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "recrawl", "status": "running", "progress": {}, "logs": [],
            "error": "", "stop_event": threading.Event(), "platform": "boss",
        }
        resp = self.client.post(f"/api/task/pause/{run_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "pausing")
        self.assertEqual(self.app.config["PIPELINE_TASKS"][run_id]["stop_mode"], "pause")

        done_id = "pause-done"
        self.app.config["PIPELINE_TASKS"][done_id] = {
            "kind": "ai_screen", "status": "done", "progress": {}, "logs": [],
            "error": "", "stop_event": threading.Event(), "platform": "boss",
        }
        resp = self.client.post(f"/api/task/pause/{done_id}")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "task_not_active")

    def test_latest_running_task_paused_returns_round_context(self):
        scrape_id = self._create_completed_scrape_run()
        run_id = "paused-ctx"
        self._seed_ai_run(scrape_id, run_id, status="paused")
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        ctx = data["round_context"]
        self.assertEqual(ctx["keywords"], ["Python", "后端"])
        self.assertEqual(ctx["cities"], ["上海"])
        self.assertEqual(ctx["screening_fields"], {"salary": ["20-30K"]})
        self.assertEqual(ctx["profile_summary"], "测试画像")
        self.assertEqual(ctx["profile_facts"], {"years": 3})
        self.assertEqual(ctx["screen_run_id"], run_id)
        self.assertEqual(ctx["status"], "paused")
        self.assertTrue(ctx["resumable"])

    def test_latest_pipeline_result_returns_round_context_from_snapshot(self):
        scrape_id = self._create_completed_scrape_run()
        run_id = "snapshot-ctx"
        self._seed_ai_run(scrape_id, run_id, status="paused")
        self.store.save_pipeline_result(
            {
                "ok": True, "jobs": [{"platform_job_id": "j1", "title": "岗位", "verdict": "uncertain"}],
                "dropped": [], "total_scraped": 1, "total_kept": 1,
            },
            {"platform": "boss"},
            status="partial",
            execution_params={
                "platform": "boss", "scrape_task_id": scrape_id,
                "screen_run_id": run_id,
            },
        )
        data = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertTrue(data["has_result"])
        ctx = data["round_context"]
        self.assertEqual(ctx["screen_run_id"], run_id)
        self.assertEqual(ctx["status"], "paused")
        self.assertTrue(ctx["resumable"])

    def test_ai_screen_resumes_failed_partial_and_user_finished(self):
        cases = [
            ("failed", "failed", None),
            ("partial", "partial", None),
            ("finished", "interrupted", "user_finished"),
        ]
        for label, status, error_code in cases:
            scrape_id = self._create_completed_scrape_run()
            run_id = f"resume-{label}"
            self._seed_ai_run(scrape_id, run_id, status=status, error_code=error_code)
            with mock.patch.object(
                self.app.config["PIPELINE_EXECUTOR"], "submit",
                return_value=mock.Mock(),
            ):
                resp = self.client.post("/api/ai-screen", json={
                    "scrape_task_id": scrape_id,
                    "screening_fields": {"salary": ["20-30K"]},
                    "profile_summary": "测试画像",
                    "profile_facts": {"years": 3},
                    "filter_schema_version": 1,
                })
            self.assertEqual(resp.status_code, 200, resp.get_json())
            data = resp.get_json()
            self.assertTrue(data["resuming"])
            self.assertNotEqual(data["task_id"], run_id)
            self.app.config["PIPELINE_TASKS"].pop(data["task_id"], None)

    def test_ai_screen_falls_back_to_parent_profile_facts_when_missing(self):
        """旧快照漏存画像事实时，AI 筛选从父抓取任务恢复三通道输入。"""
        scrape_id = self._create_completed_scrape_run()
        facts = {"core_skills": ["Python"], "job_type": "全职"}
        run = self.store.get_screening_run(scrape_id)
        ep = dict(run.get("execution_params") or {})
        ep["profile_summary"] = "3年Python后端候选人"
        ep["profile_facts"] = facts
        self.store.update_screening_execution_params(scrape_id, ep)
        captured = []
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *args, **kwargs: captured.append((fn, args, kwargs)) or None,
        ):
            resp = self.client.post("/api/ai-screen", json={
                "scrape_task_id": scrape_id,
                "screening_fields": {"salary": ["20-30K"]},
                "profile_summary": "3年Python后端候选人",
                "filter_schema_version": 1,
            })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        fn, args, kwargs = captured[0]
        self.assertEqual(args[5], facts)

    def test_b057_continue_switch_account_persists_target_identity(self):
        """B057：限流后切换到另一账号继续，冻结身份被替换且断点保留。"""
        task_id = "b057-switch"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "前端", "city": ["上海"], "pages": 3},
                "browser_account": "a", "cdp_port": 9222, "profile_key": "boss:a",
            },
        )
        if self.store.get_screening_run(task_id)["status"] == "queued":
            self.store.update_screening_run(task_id, status="running")
        self.store.update_screening_run(
            task_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="源账号限流",
        )
        checked = []
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda run: (
            checked.append((run.get("execution_params") or {}).get("browser_account"))
            or (True, "", "")
        )
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            resp = self.client.post(
                f"/api/task/continue/{task_id}",
                json={"target_account": "b"},
            )
        self.assertEqual(resp.status_code, 200, resp.get_json())
        params = (self.store.get_screening_run(task_id) or {}).get("execution_params") or {}
        self.assertEqual(params.get("browser_account"), "b")
        self.assertEqual(params.get("profile_key"), "boss:b")
        self.assertEqual(params.get("cdp_port"), 9222)
        self.assertEqual(checked, ["b", "b"])
        submit.assert_called_once()

    def test_b057_continue_switch_missing_account_rejected(self):
        task_id = "b057-missing"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={"platform": "boss",
                           "script_params": {"keyword": "前端", "city": ["上海"]}},
        )
        if self.store.get_screening_run(task_id)["status"] == "queued":
            self.store.update_screening_run(task_id, status="running")
        self.store.update_screening_run(
            task_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="源账号限流",
        )
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
        resp = self.client.post(
            f"/api/task/continue/{task_id}",
            json={"target_account": "nope"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error"], "target_account_not_found")

    def test_b057_continue_switch_no_longer_blocked_by_cooldown(self):
        # 016：冷却功能删除；切换账号续跑不再有 account_in_cooldown 拦截路径
        task_id = "b057-cooldown"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={"platform": "boss",
                           "script_params": {"keyword": "前端", "city": ["上海"]}},
        )
        if self.store.get_screening_run(task_id)["status"] == "queued":
            self.store.update_screening_run(task_id, status="running")
        self.store.update_screening_run(
            task_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="源账号限流",
        )
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
        resp = self.client.post(
            f"/api/task/continue/{task_id}",
            json={"target_account": "b"},
        )
        # 目标账号存在：不再返回 account_in_cooldown，按正常续跑路径走
        self.assertNotEqual(resp.get_json().get("error"), "account_in_cooldown")

    def test_b057_continue_switch_target_preflight_failure_keeps_identity(self):
        task_id = "b057-preflight"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "前端", "city": ["上海"], "pages": 3},
                "browser_account": "a", "cdp_port": 9222, "profile_key": "boss:a",
            },
        )
        if self.store.get_screening_run(task_id)["status"] == "queued":
            self.store.update_screening_run(task_id, status="running")
        self.store.update_screening_run(
            task_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="源账号限流",
        )
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (
            False, "cdp_unavailable", "目标账号浏览器未就绪",
        )
        resp = self.client.post(
            f"/api/task/continue/{task_id}",
            json={"target_account": "b"},
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "block_not_resolved")
        params = (self.store.get_screening_run(task_id) or {}).get("execution_params") or {}
        self.assertEqual(params.get("browser_account"), "a")

    def test_b057_continue_uses_active_account_without_target(self):
        """暂停中切换 active 账号后，不带 target_account 继续沿用新账号。

        030 双门槛口径：创建时全局账号为 a（快照），暂停期间用户把全局切到
        b → 当前 ≠ 快照 → 继续沿用新账号 b；语义与 B057 原始行为一致。
        """
        task_id = "b057-active-switch"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "前端", "city": ["上海"], "pages": 3},
                "browser_account": "a", "cdp_port": 9222, "profile_key": "boss:a",
                "active_account_at_freeze": "a",
            },
        )
        if self.store.get_screening_run(task_id)["status"] == "queued":
            self.store.update_screening_run(task_id, status="running")
        self.store.update_screening_run(
            task_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="源账号限流",
        )
        with mock.patch("webui.pipeline_exec.close_debug_chrome"):
            activated = self.client.post("/api/browser-accounts/b/activate")
        self.assertEqual(activated.status_code, 200, activated.get_json())
        checked = []
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda run: (
            checked.append((run.get("execution_params") or {}).get("browser_account"))
            or (True, "", "")
        )
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            resp = self.client.post(f"/api/task/continue/{task_id}", json={})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        params = (self.store.get_screening_run(task_id) or {}).get("execution_params") or {}
        self.assertEqual(params.get("browser_account"), "b")
        self.assertEqual(params.get("profile_key"), "boss:b")
        self.assertEqual(params.get("cdp_port"), 9222)
        self.assertEqual(checked, ["b", "b"])
        submit.assert_called_once()

    def test_worker_pause_writes_paused_without_history_round(self):
        """US1：用户暂停后任务可继续，但历史列表零增长（不再写 partial 快照）。"""
        from webui import pipeline_exec
        scrape_id = self._create_completed_scrape_run()
        captured = []
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *args, **kwargs: captured.append((fn, args, kwargs)) or None,
        ):
            resp = self.client.post("/api/ai-screen", json={
                "scrape_task_id": scrape_id,
                "screening_fields": {"salary": ["20-30K"]},
                "profile_summary": "测试画像",
                "profile_facts": {"years": 3},
                "filter_schema_version": 1,
            })
        self.assertEqual(resp.status_code, 200)
        task_id = resp.get_json()["task_id"]
        fn, args, kwargs = captured[0]
        # 先设暂停模式但不发信号；粗筛 mock 落判定后注入暂停信号，
        # 使暂停检测点（粗筛后 3446）已有部分判定——US1 核心场景
        # （AI 已判部分岗位后点暂停）。
        self.app.config["PIPELINE_TASKS"][task_id]["stop_mode"] = "pause"
        def _rough_ok(*a, **kw):
            kw["on_batch_done"]({"j1": "match"}, ["j1"])
            self.app.config["PIPELINE_TASKS"][task_id]["stop_event"].set()
            return {"verdicts": {"j1": "match"}, "kept": ["j1"], "dropped": []}
        with mock.patch.object(
            pipeline_exec, "resolve_browser_account", return_value="",
        ), mock.patch.object(pipeline_exec, "set_active_cdp_data_dir"):
            with mock.patch.object(
                self.store, "get_ai_settings",
                return_value={"endpoint_url": "http://ai.test", "model": "m", "is_configured": True},
            ), mock.patch("webui.ai.is_ai_available", return_value=True), \
               mock.patch("webui.ai.screen_jobs", side_effect=_rough_ok):
                fn(*args, **kwargs)
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "paused")
        self.assertEqual(run["error_code"], "user_paused")
        self.assertEqual(self.app.config["PIPELINE_TASKS"][task_id]["status"], "paused")
        events = self.store.list_task_events(task_id)
        self.assertTrue(any(event["type"] == "pause" for event in events))
        # 017-US1：即使已判部分岗位，暂停也只是暂停，历史不新增轮
        self.assertEqual(self.store.list_history_rounds(), [])
        history = self.client.get("/api/result-history").get_json()
        self.assertEqual(history["items"], [])

    def test_worker_hard_block_pauses_without_history_round(self):
        """US1：AI 粗筛系统性阻断（如验证码/限流）强停任务，历史不新增轮。"""
        from webui import pipeline_exec
        from webui.ai import AISecurityError
        scrape_id = self._create_completed_scrape_run()
        captured = []
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *args, **kwargs: captured.append((fn, args, kwargs)) or None,
        ):
            resp = self.client.post("/api/ai-screen", json={
                "scrape_task_id": scrape_id,
                "screening_fields": {"salary": ["20-30K"]},
                "profile_summary": "测试画像",
                "profile_facts": {"years": 3},
                "filter_schema_version": 1,
            })
        self.assertEqual(resp.status_code, 200)
        task_id = resp.get_json()["task_id"]
        # 粗筛 mock 先落判定再抛阻断：强停时已有部分判定（US1 核心场景）
        def _blocking_screen_jobs(*args, **kwargs):
            kwargs["on_batch_done"]({"j1": "match"}, ["j1"])
            raise AISecurityError("ai_rate_limited")
        fn, args, kwargs = captured[0]
        with mock.patch.object(
            pipeline_exec, "resolve_browser_account", return_value="",
        ), mock.patch.object(pipeline_exec, "set_active_cdp_data_dir"):
            with mock.patch.object(
                self.store, "get_ai_settings",
                return_value={"endpoint_url": "http://ai.test", "model": "m", "is_configured": True},
            ), mock.patch("webui.ai.is_ai_available", return_value=True), \
               mock.patch("webui.ai.screen_jobs", side_effect=_blocking_screen_jobs), \
               mock.patch(
                   "webui.ai.map_ai_error_to_block_code",
                   return_value="ai_blocked",
               ):
                fn(*args, **kwargs)
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "paused")
        self.assertTrue(run["error_code"])
        # 017-US1：硬阻断强停只是暂停，历史不新增轮
        self.assertEqual(self.store.list_history_rounds(), [])
        history = self.client.get("/api/result-history").get_json()
        self.assertEqual(history["items"], [])

    def test_scrape_result_save_falls_back_to_parent_profile(self):
        scrape_id = self._create_completed_scrape_run()
        run = self.store.get_screening_run(scrape_id)
        ep = dict(run.get("execution_params") or {})
        ep["profile_summary"] = "测试画像"
        ep["profile_facts"] = {"years": 3}
        self.store.update_screening_execution_params(scrape_id, ep)
        resp = self.client.post("/api/scrape-result-save", json={"task_id": scrape_id})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertTrue(resp.get_json().get("saved"))
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertEqual((latest.get("result") or {}).get("profile_summary"), "测试画像")
        self.assertEqual((latest.get("result") or {}).get("profile_facts"), {"years": 3})

    def test_resume_chain_restores_full_survivors_after_collapse(self):
        """018 事故链回归：完整判定挂在链首 run，续跑幸存者不塌缩。

        run1 名下 277 条粗筛判定（165 kept + 112 dropped）+ 277 断点；
        run2（续跑目标）名下仅 40 条精筛判定 + 继承的 277 断点；
        run3 续跑 → 幸存者 165、40 条精筛判定被继承、无岗位静默消失。
        """
        from webui import pipeline_exec
        kept_n, dropped_n, fine_n = 165, 112, 40
        jobs = [
            {"job_id": f"j{i:03d}", "platform_job_id": f"j{i:03d}",
             "title": "岗位", "salary": "20K", "location": "上海",
             "source_url": f"https://zhipin.example/j{i:03d}.html"}
            for i in range(kept_n + dropped_n)
        ]
        kept_ids = [f"j{i:03d}" for i in range(kept_n)]
        dropped_ids = [f"j{kept_n + i:03d}" for i in range(dropped_n)]
        scrape_id = self._create_completed_scrape_run()
        self.app.config["PIPELINE_TASKS"][scrape_id]["result"] = {
            "ok": True, "jobs": jobs, "total_scraped": len(jobs),
            "total_matched": 0, "completed_combos": ["kw|city"],
        }
        run1 = "chain-run1"
        self._seed_ai_run(scrape_id, run1, status="failed")
        self.store.save_screening_verdicts(run1, {
            **{jid: "kept" for jid in kept_ids},
            **{jid: "dropped" for jid in dropped_ids},
        })
        self.store.save_checkpoint(run1, "ai_rough", kept_ids + dropped_ids)
        run2 = "chain-run2"
        self._seed_ai_run(scrape_id, run2, status="failed")
        fine_ids = kept_ids[:fine_n]
        self.store.save_screening_verdicts(run2, {
            jid: {"verdict": "not_match", "reason": "跨链路",
                  "caveats": [], "flags": []}
            for jid in fine_ids
        })
        self.store.save_checkpoint(run2, "ai_rough", kept_ids + dropped_ids)
        self.store.save_checkpoint(run2, "ai_fine", fine_ids)

        captured = []
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *a, **kw: captured.append((fn, a, kw)) or None,
        ):
            resp = self.client.post("/api/ai-screen", json={
                "scrape_task_id": scrape_id,
                "screening_fields": {"salary": ["20-30K"]},
                "profile_summary": "测试画像",
                "profile_facts": {"years": 3},
                "filter_schema_version": 1,
            })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertTrue(resp.get_json()["resuming"])
        task_id = resp.get_json()["task_id"]
        fn, args, kwargs = captured[0]

        screen_calls, match_calls = [], []

        def _fake_screen_jobs(jobs_arg, *a, **kw):
            screen_calls.append(list(jobs_arg))
            return {"kept": [], "dropped": [], "verdicts": {}}

        def _fake_match_jds(jobs_arg, *a, **kw):
            match_calls.append(list(jobs_arg))
            verdicts = {
                str(j["job_id"]): {"verdict": "match", "reason": "匹配",
                                   "caveats": [], "flags": []}
                for j in jobs_arg
            }
            if verdicts and kw.get("on_batch_done"):
                kw["on_batch_done"](
                    verdicts, [str(j["job_id"]) for j in jobs_arg])
            return {"verdicts": verdicts}

        def _fake_fetch_details(chunk, source, **kw):
            return {
                "jobs": [dict(j, jd="岗位描述") for j in chunk],
                "hard_stop": False, "stopped": False,
            }

        with mock.patch.object(
            pipeline_exec, "resolve_browser_account", return_value="",
        ), mock.patch.object(pipeline_exec, "set_active_cdp_data_dir"), \
           mock.patch.object(
               pipeline_exec, "ensure_chrome_ready",
               return_value=(True, "")), \
           mock.patch.object(pipeline_exec, "close_debug_chrome"), \
           mock.patch.object(
               pipeline_exec, "fetch_job_details",
               side_effect=_fake_fetch_details), \
           mock.patch.object(
               self.store, "get_ai_settings",
               return_value={"endpoint_url": "http://ai.test", "model": "m",
                             "is_configured": True},
           ), mock.patch(
               "webui.ai.is_ai_available", return_value=True,
           ), mock.patch(
               "webui.ai.screen_jobs", side_effect=_fake_screen_jobs,
           ), mock.patch(
               "webui.ai.match_jds", side_effect=_fake_match_jds,
           ):
            fn(*args, **kwargs)

        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["total_kept"], kept_n)
        self.assertEqual(run["total_dropped"], dropped_n)
        self.assertEqual(run["status"], "succeeded")
        # 链首 277 断点齐全：粗筛零重筛（旧代码会重筛或塌缩幸存者）
        self.assertEqual(screen_calls, [[]])
        # 40 条精筛判定被继承，只精筛剩余 125 条
        self.assertEqual(len(match_calls), 1)
        self.assertEqual(len(match_calls[0]), kept_n - fine_n)
        # 无岗位静默消失：幸存者 + 移除 = 全部岗位
        self.assertEqual(run["total_kept"] + run["total_dropped"], len(jobs))
        # 正常完成：历史恰好一条轮（018 换序后正常路径仍写轮）
        self.assertEqual(len(self.store.list_history_rounds()), 1)

    def test_finalize_mismatch_fails_without_history_round(self):
        """018：终态校验失败抛错时库里没有任何 done 轮（幽灵轮事故回归）。"""
        from webui import pipeline_exec
        jobs = [
            {"job_id": f"j{i}", "platform_job_id": f"j{i}", "title": "岗位",
             "source_url": f"https://zhipin.example/j{i}.html"}
            for i in range(3)
        ]
        scrape_id = self._create_completed_scrape_run()
        self.app.config["PIPELINE_TASKS"][scrape_id]["result"] = {
            "ok": True, "jobs": jobs, "total_scraped": 3,
            "total_matched": 0, "completed_combos": ["kw|city"],
        }
        captured = []
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *a, **kw: captured.append((fn, a, kw)) or None,
        ):
            resp = self.client.post("/api/ai-screen", json={
                "scrape_task_id": scrape_id,
                "screening_fields": {"salary": ["20-30K"]},
                "profile_summary": "测试画像",
                "profile_facts": {"years": 3},
                "filter_schema_version": 1,
            })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        task_id = resp.get_json()["task_id"]
        fn, args, kwargs = captured[0]

        def _fake_screen_jobs(jobs_arg, *a, **kw):
            # j3 既不保留也不移除：计数对不上，finalize 必判 paused
            return {
                "kept": ["j0"],
                "dropped": [{"job_id": "j1", "title": "岗位", "reason": "经验不符"}],
                "verdicts": {"j0": "kept", "j1": "dropped"},
            }

        def _fake_match_jds(jobs_arg, *a, **kw):
            verdicts = {
                str(j["job_id"]): {"verdict": "match", "reason": "匹配",
                                   "caveats": [], "flags": []}
                for j in jobs_arg
            }
            if verdicts and kw.get("on_batch_done"):
                kw["on_batch_done"](
                    verdicts, [str(j["job_id"]) for j in jobs_arg])
            return {"verdicts": verdicts}

        def _fake_fetch_details(chunk, source, **kw):
            return {
                "jobs": [dict(j, jd="岗位描述") for j in chunk],
                "hard_stop": False, "stopped": False,
            }

        with mock.patch.object(
            pipeline_exec, "resolve_browser_account", return_value="",
        ), mock.patch.object(pipeline_exec, "set_active_cdp_data_dir"), \
           mock.patch.object(
               pipeline_exec, "ensure_chrome_ready",
               return_value=(True, "")), \
           mock.patch.object(pipeline_exec, "close_debug_chrome"), \
           mock.patch.object(
               pipeline_exec, "fetch_job_details",
               side_effect=_fake_fetch_details), \
           mock.patch.object(
               self.store, "get_ai_settings",
               return_value={"endpoint_url": "http://ai.test", "model": "m",
                             "is_configured": True},
           ), mock.patch(
               "webui.ai.is_ai_available", return_value=True,
           ), mock.patch(
               "webui.ai.screen_jobs", side_effect=_fake_screen_jobs,
           ), mock.patch(
               "webui.ai.match_jds", side_effect=_fake_match_jds,
           ):
            fn(*args, **kwargs)

        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "internal_error")
        self.assertEqual(
            self.app.config["PIPELINE_TASKS"][task_id]["status"], "failed")
        # 幽灵轮回归：校验失败时不得留下任何 done 历史
        self.assertEqual(self.store.list_history_rounds(), [])
        events = self.store.list_task_events(task_id)
        self.assertFalse(
            any(event["type"] == "history_snapshot" for event in events))


class ResultRoundRescueTests(unittest.TestCase):
    """020 US7：终态 succeeded 落库后写结果轮失败 → 重试 → 条件降级 →
    续跑直达收尾重建结果轮。"""

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

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _create_scrape(self):
        jobs = [
            {"job_id": f"j{i}", "platform_job_id": f"j{i}", "title": "岗位",
             "source_url": f"https://zhipin.example/j{i}.html"}
            for i in range(3)
        ]
        run_id = f"scrape_{uuid.uuid4().hex[:8]}"
        self.store.create_screening_run(
            run_id,
            frozen_filters={},
            source_count=len(jobs),
            execution_params={
                "platform": "boss",
                "script_params": {
                    "keyword": "Python,后端", "city": ["上海"], "pages": 3,
                },
                "execution_config": {
                    "schema_version": 1, "inter_combo_delay": 10,
                    "detail_batch_size": 15, "detail_interval": 2,
                    "detail_reset_every": 4, "detail_batch_cooldown": 5,
                    "detail_tab_pool_size": 5, "screen_batch_size": 50,
                    "screen_concurrency": 5, "match_batch_size": 4,
                    "match_concurrency": 10, "config_digest": None,
                },
                "frozen_scope": {
                    "schema_version": 1, "platform": "boss",
                    "keywords": ["Python"], "scope_kind": "cities",
                    "cities": ["上海"], "pages_per_combination": 3,
                    "combination_count": 1, "planned_pages": 3,
                    "task_size": "small", "scope_digest": None,
                },
            },
            backend_version="test",
        )
        self.store.update_screening_run(
            run_id, status="succeeded", current_stage="done",
            processed_count=len(jobs), match_count=len(jobs),
        )
        self.store.save_scrape_combo_result(
            run_id, "kw|city", jobs, ["kw|city"])
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "done", "progress": {}, "logs": [],
            "result": {"ok": True, "jobs": jobs, "total_scraped": len(jobs),
                       "total_matched": 0, "completed_combos": ["kw|city"]},
            "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(), "platform": "boss",
        }
        return run_id

    def _start_screen(self, scrape_id):
        captured = []
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *a, **kw: captured.append((fn, a, kw)) or None,
        ):
            resp = self.client.post("/api/ai-screen", json={
                "scrape_task_id": scrape_id,
                "screening_fields": {"salary": ["20-30K"]},
                "profile_summary": "测试画像",
                "profile_facts": {"years": 3},
                "filter_schema_version": 1,
            })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        return captured[0]

    def _run_with_mocks(self, captured_fn, screen_calls, match_calls,
                        save_side_effect=None):
        """跑一轮筛选任务；可注入 save_finished_round 替身。"""
        from webui import pipeline_exec
        import webui.result_rounds as result_rounds

        def _fake_screen_jobs(jobs_arg, *a, **kw):
            screen_calls.append([str(j.get("job_id")) for j in jobs_arg])
            return {
                "kept": [str(j.get("job_id")) for j in jobs_arg],
                "dropped": [], "verdicts": {},
            }

        def _fake_match_jds(jobs_arg, *a, **kw):
            match_calls.append([str(j["job_id"]) for j in jobs_arg])
            verdicts = {
                str(j["job_id"]): {"verdict": "match", "reason": "匹配",
                                   "caveats": [], "flags": []}
                for j in jobs_arg
            }
            if verdicts and kw.get("on_batch_done"):
                kw["on_batch_done"](
                    verdicts, [str(j["job_id"]) for j in jobs_arg])
            return {"verdicts": verdicts}

        def _fake_fetch_details(chunk, source, **kw):
            return {
                "jobs": [dict(j, jd="岗位描述") for j in chunk],
                "hard_stop": False, "stopped": False,
            }

        fn, args, kwargs = captured_fn
        patches = [
            mock.patch.object(pipeline_exec, "resolve_browser_account",
                              return_value=""),
            mock.patch.object(pipeline_exec, "set_active_cdp_data_dir"),
            mock.patch.object(pipeline_exec, "ensure_chrome_ready",
                              return_value=(True, "")),
            mock.patch.object(pipeline_exec, "close_debug_chrome"),
            mock.patch.object(pipeline_exec, "fetch_job_details",
                              side_effect=_fake_fetch_details),
            mock.patch.object(
                self.store, "get_ai_settings",
                return_value={"endpoint_url": "http://ai.test", "model": "m",
                              "is_configured": True}),
            mock.patch("webui.ai.is_ai_available",
                       return_value=True),
            mock.patch("webui.ai.screen_jobs", side_effect=_fake_screen_jobs),
            mock.patch("webui.ai.match_jds", side_effect=_fake_match_jds),
        ]
        if save_side_effect is not None:
            patches.append(mock.patch.object(
                self.store, "save_pipeline_result",
                side_effect=save_side_effect))
        for p in patches:
            p.start()
        try:
            fn(*args, **kwargs)
        finally:
            for p in patches:
                p.stop()

    def _new_task_ids(self, before, *known):
        return set(self.app.config["PIPELINE_TASKS"]) - set(before) - set(known)

    def test_lock_failure_retries_downgrades_and_resume_rebuilds_round(self):
        import sqlite3
        scrape_id = self._create_scrape()
        before = set(self.app.config["PIPELINE_TASKS"])

        # 第一轮：save_finished_round 恒抛瞬时锁错
        attempts = []

        def always_locked(*a, **kw):
            attempts.append(1)
            raise sqlite3.OperationalError("database is locked")

        captured = self._start_screen(scrape_id)
        screen_calls, match_calls = [], []
        self._run_with_mocks(captured, screen_calls, match_calls,
                             save_side_effect=always_locked)
        new_ids = self._new_task_ids(before, scrape_id)
        self.assertEqual(len(new_ids), 1, new_ids)
        task_id = new_ids.pop()

        # 重试 3 次后条件降级
        self.assertEqual(len(attempts), 3)
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "result_round_save_failed")
        task_mem = self.app.config["PIPELINE_TASKS"][task_id]
        self.assertEqual(task_mem["status"], "failed")
        self.assertIn("点继续可重试保存", task_mem["error"])
        events = [e["type"] for e in self.store.list_task_events(task_id)]
        self.assertIn("result_round_save_failed", events)
        # 零结果轮
        self.assertEqual(self.store.list_history_rounds(), [])

        # 续跑：AI 零重筛、直达收尾、结果轮重建、终态 succeeded
        screen_calls2, match_calls2 = [], []
        captured2 = self._start_screen(scrape_id)
        self._run_with_mocks(captured2, screen_calls2, match_calls2)
        resumed_ids = self._new_task_ids(before, scrape_id, task_id)
        resumed_id = resumed_ids.pop() if resumed_ids else task_id

        run2 = self.store.get_screening_run(resumed_id)
        self.assertEqual(run2["status"], "succeeded", run2)
        # AI 零调用：粗筛/精筛收到的都是空清单
        self.assertTrue(screen_calls2 and all(c == [] for c in screen_calls2),
                        screen_calls2)
        self.assertTrue(match_calls2 and all(c == [] for c in match_calls2),
                        match_calls2)
        # 结果轮成功写入且一条流程一条轮
        self.assertEqual(len(self.store.list_history_rounds()), 1)


class ResumeAccountGateIntegrationTests(unittest.TestCase):
    """030：统一继续接口双门槛自动换号集成回归。

    核心场景（用户原始缺陷）：全局账号 a、R2 冻结账号 b、用户未动任何
    设置，任务报错暂停后点继续——必须继续用 b，不得被静默改写为 a。
    """

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
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda run: (True, "", "")

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _seed_paused_run(self, task_id, *, browser_account, snapshot,
                          error_code, current_stage="jd_detail"):
        """种子一个 BOSS paused run：冻结账号/快照/暂停码可控。"""
        scrape_id = f"{task_id}-src"
        jobs = [
            {"job_id": "j001", "title": "岗位1",
             "source_url": "https://zhipin.example/j001.html"},
        ]
        self.store.create_screening_run(scrape_id, source_count=1)
        self.store.save_scrape_combo_result(scrape_id, "kw|city", jobs, ["kw|city"])
        params = {
            "platform": "boss",
            "scrape_task_id": scrape_id,
            "profile_summary": "测试画像",
            "browser_account": browser_account,
            "cdp_port": 9222,
            "profile_key": f"boss:{browser_account}",
        }
        if snapshot is not None:
            params["active_account_at_freeze"] = snapshot
        self.store.create_screening_run(task_id, source_count=1,
                                        execution_params=params)
        self.store.update_screening_run(task_id, status="running")
        self.store.update_screening_run(
            task_id, status="paused", current_stage=current_stage,
            error_code=error_code, error_reason="测试暂停",
        )
        return task_id

    def _continue(self, task_id):
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit"):
            return self.client.post(f"/api/task/continue/{task_id}", json={})

    def _account_switch_events(self, task_id):
        return [e for e in self.store.list_task_events(task_id)
                if e.get("type") == "account_switch"]

    def test_untouched_global_keeps_r2_frozen_account(self):
        """核心回归：用户未动全局账号（当前=快照），继续沿用冻结 b 不改写。"""
        task_id = self._seed_paused_run(
            "gate-untouched", browser_account="b", snapshot="a",
            error_code="source_rate_limited")
        resp = self._continue(task_id)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        params = (self.store.get_screening_run(task_id) or {}).get(
            "execution_params") or {}
        self.assertEqual(params.get("browser_account"), "b")
        self.assertEqual(params.get("active_account_at_freeze"), "a")
        self.assertEqual(self._account_switch_events(task_id), [])

    def test_missing_snapshot_keeps_frozen_account(self):
        """存量任务无快照 → 不自动换号，沿用冻结身份。"""
        task_id = self._seed_paused_run(
            "gate-legacy", browser_account="b", snapshot=None,
            error_code="source_rate_limited")
        resp = self._continue(task_id)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        params = (self.store.get_screening_run(task_id) or {}).get(
            "execution_params") or {}
        self.assertEqual(params.get("browser_account"), "b")
        self.assertEqual(self._account_switch_events(task_id), [])

    def test_switch_writes_event_and_log_line(self):
        """B057 场景（暂停期间激活 b）→ 换号发生且事件+日志行留痕。"""
        task_id = self._seed_paused_run(
            "gate-switch", browser_account="a", snapshot="a",
            error_code="source_rate_limited", current_stage="scrape")
        with mock.patch("webui.pipeline_exec.close_debug_chrome"):
            activated = self.client.post("/api/browser-accounts/b/activate")
        self.assertEqual(activated.status_code, 200, activated.get_json())
        resp = self._continue(task_id)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        params = (self.store.get_screening_run(task_id) or {}).get(
            "execution_params") or {}
        self.assertEqual(params.get("browser_account"), "b")
        events = self._account_switch_events(task_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"].get("from_account"), "a")
        self.assertEqual(events[0]["payload"].get("to_account"), "b")
        logs = " ".join(self.app.config["PIPELINE_TASKS"][task_id]["logs"])
        self.assertIn("切换到账号", logs)

    def test_no_switch_leaves_no_audit_trace(self):
        """未换号的续跑零留痕（无事件、日志无换号行）。"""
        task_id = self._seed_paused_run(
            "gate-clean", browser_account="b", snapshot="a",
            error_code="source_rate_limited")
        resp = self._continue(task_id)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        logs = " ".join(self.app.config["PIPELINE_TASKS"][task_id]["logs"])
        self.assertNotIn("切换到账号", logs)


class JobDetailConcurrencyGuardTests(unittest.TestCase):
    """030 US4：任务运行中单岗位 JD 抓取被拒 + JD 阶段浏览器身份重绑。"""

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

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def test_job_detail_rejected_while_task_running(self):
        """任务运行中 POST /api/job-detail → 409 中文提示，无浏览器副作用。"""
        self.app.config["PIPELINE_TASKS"]["busy-task"] = {
            "kind": "ai_screen", "status": "running", "progress": {}, "logs": [],
        }
        with mock.patch("webui.pipeline_exec.set_active_cdp_data_dir") as set_dir:
            resp = self.client.post("/api/job-detail", json={
                "job_id": "j001",
                "source_url": "https://zhipin.example/j001.html",
                "source_run_id": "some-run",
            })
        self.assertEqual(resp.status_code, 409, resp.get_json())
        body = resp.get_json()
        self.assertEqual(body.get("error"), "browser_busy")
        self.assertIn("任务", body.get("message") or "")
        set_dir.assert_not_called()

    def test_jd_stage_rebinds_task_browser_before_chrome(self):
        """JD 阶段启动浏览器前以任务冻结身份重绑（消除并发污染窗口）。"""
        from types import SimpleNamespace
        from webui.runners.ai_screen_jd import run_jd_stage
        import threading

        calls = []

        class _FakeStore:
            def append_task_events(self, run_id, events):
                pass

        class _FakeCtx:
            lock = threading.Lock()
            tasks = {}
            pipeline_guard = None
            screen_stage_messages = {}
            event_stage_names = {}

            def screen_overall_percent(self, stage, current, total):
                return 0

            def activate_task_browser(self, task_id, **kwargs):
                calls.append(("rebind", task_id))

            def make_cdp_source(self, **kwargs):
                return object()

            def write_run(self, run_id, **kwargs):
                pass

        ctx = _FakeCtx()
        ctx.app = SimpleNamespace(config={"RESULT_DIR": self.temp.name})
        ctx.store = _FakeStore()
        job = {"job_id": "j001", "title": "岗位1",
               "source_url": "https://zhipin.example/j001.html"}
        with mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                        return_value=(True, "")) as chrome, \
             mock.patch("webui.pipeline_exec.close_debug_chrome"), \
             mock.patch("webui.pipeline_exec.fetch_job_details",
                        return_value={"jobs": []}):
            outcome = run_jd_stage(
                ctx, "gate-jd-run", [dict(job)], [dict(job)], {}, "jd-path",
                "boss", 9222, "boss:b", "b",
                SimpleNamespace(detail_batch_size=1), None,
                lambda **kw: None, lambda: False, lambda: None,
                lambda *a, **kw: None)
        self.assertIsNotNone(outcome)
        self.assertEqual(calls, [("rebind", "gate-jd-run")])
        chrome.assert_called_once()


if __name__ == "__main__":
    unittest.main()
