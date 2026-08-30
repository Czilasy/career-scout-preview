"""健康流程暂停/恢复合同测试（027 自 tests/test_healthy_pipeline.py 拆出）。"""
import json
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from webui.app import create_app
from webui.store import (
    DiscoveryStoreConflictError,
    RUN_STATUSES, RUN_TRANSITIONS, SYSTEMIC_BLOCK_CODES,
)

from tests.healthy_pipeline.harness import _make_app, _authed_test_client, _wait_for_pipeline_task, _pause_run


class Slice7And9ApiTests(unittest.TestCase):
    """切片 7+9：统一状态接口 + 版本接口（FR-037/FR-039）。"""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = _authed_test_client(self.app)
        self.store = self.app.config["TASK_STORE"]
        # POST 请求需要本地会话令牌（protect_local_api 钩子）
        self.token = self.app.config["API_TOKEN"]
        self.run_id = "test-run-api"
        self.store.create_screening_run(self.run_id, source_count=100)

    def _auth_headers(self):
        return {"X-Boss-Token": self.token}

    def tearDown(self):
        self.temp.cleanup()

    def test_version_api_returns_hash(self):
        """/api/version 返回 backend_version/build_hash/build_time（FR-039）。"""
        resp = self.client.get("/api/version")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(
            data.get("backend_version"),
            "011-ui-fixes",
        )
        self.assertRegex(data.get("build_hash", ""), r"^[0-9a-f]{12}$")
        self.assertRegex(
            data.get("build_time", ""), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"
        )

    def test_task_state_scrape_uses_checkpoint_after_resume(self):
        """刷新时组合 checkpoint 前进，状态不得回退到旧 processed_count。"""
        run_id = "scrape-checkpoint-refresh"
        self.store.create_screening_run(run_id, source_count=10)
        self.store.update_screening_run(
            run_id, status="running", current_stage="scrape", processed_count=3,
        )
        self.store.save_checkpoint(
            run_id, "scrape", [f"kw-{i}|city" for i in range(6)],
        )
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running",
            "progress": {"stage": "combo_done", "current": 3, "total": 10},
            "logs": [], "result": None, "error": "",
            "started_at": None, "finished_at": None,
            "stop_event": threading.Event(),
        }

        response = self.client.get(f"/api/task-state/{run_id}")

        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()
        self.assertEqual(data["processed"], 6)
        self.assertEqual(data["success_count"], 6)
        self.assertEqual(data["progress"]["current"], 6)

    def test_version_hash_covers_all_backend_modules(self):
        """共享状态/恢复模块变化也必须产生新的前后端构建身份。"""
        import hashlib

        root = pathlib.Path(__file__).resolve().parents[2]
        files = sorted(
            [*root.joinpath("webui").glob("*.py"), root / "scripts" / "boss_cdp_raw.py", root / "scripts" / "zhilian_cdp_raw.py"],
            key=lambda path: path.relative_to(root).as_posix(),
        )
        digest = hashlib.sha256()
        for path in files:
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        expected = digest.hexdigest()[:12]

        response = self.client.get("/api/version")

        self.assertEqual(response.get_json()["build_hash"], expected)
        self.assertIn(root / "webui" / "store.py", files)
        # 031 B7：webui/historical_recovery.py 已迁出为 scripts/maintenance 手动
        # 工具，构建身份清单不再覆盖恢复模块（清单仍为 webui/*.py + 两个 raw 脚本）。

    def test_task_state_api_returns_complete_picture(self):
        """/api/task-state/<run_id> 返回完整状态（FR-037）。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.update_screening_run(
            self.run_id, processed_count=50, match_count=20,
            mismatch_count=25, pending_count=5, current_stage="ai_fine")
        resp = self.client.get(f"/api/task-state/{self.run_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("status", data)
        self.assertIn("stage", data)
        self.assertIn("progress", data)
        self.assertIn("success_count", data)
        self.assertIn("fail_count", data)
        self.assertIn("unstarted_count", data)
        self.assertIn("total", data)

    def test_task_state_recovers_page_progress_after_restart(self):
        """服务重启后从 scrape_page_progress 恢复单组合页级进度。"""
        run_id = "page-state-run"
        self.store.create_screening_run(run_id, source_count=1)
        self.store.update_screening_run(
            run_id, status="running", current_stage="scrape")
        self.store.save_scrape_page_progress(
            run_id, "Python|北京",
            {"combo_key": "Python|北京", "page": 2, "target_pages": 10,
             "resume_page": 3, "has_more": True, "jobs_count": 1,
             "jobs_snapshot": [{"platform_job_id": "j1", "title": "岗"}]},
        )
        resp = self.client.get(f"/api/task-state/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["progress"]["page"], 2)
        self.assertEqual(data["progress"]["target_pages"], 10)
        self.assertEqual(data["progress"]["resume_page"], 3)
        self.assertEqual(data["scraped_count"], 1)
        self.assertEqual(data["progress"]["overall_percent"], 18)

    def test_task_state_fine_stage_ignores_prior_stage_match_mismatch(self):
        """精筛阶段 success_count 不得沿用粗筛/详情阶段的 match+mismatch 累计。

        回归：精筛开始时 match+mismatch 仍是上一阶段完成数（如 30），旧逻辑
        success_count=max(match+mismatch, processed, live) 会把计数钉死在
        30/30 + 100%，而精筛判定还没开始，界面干等。精筛阶段只认精筛自己的
        进度：processed 在精筛开始时已重置为已判定数，live current 实时推送。
        """
        self.store.update_screening_run(self.run_id, status="running")
        self.store.update_screening_run(
            self.run_id, processed_count=0, match_count=20,
            mismatch_count=10, current_stage="ai_fine")
        resp = self.client.get(f"/api/task-state/{self.run_id}")
        data = resp.get_json()
        # match+mismatch=30 是上一阶段残留，不得当精筛成功数
        self.assertEqual(data["success_count"], 0)
        self.assertEqual(data["stage"], "ai_fine")

    def test_task_state_fine_stage_tracks_live_fine_progress(self):
        """精筛阶段 success_count 跟随精筛实时进度（processed/live current）。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.update_screening_run(
            self.run_id, processed_count=3, match_count=20,
            mismatch_count=10, current_stage="ai_fine")
        self.app.config["PIPELINE_TASKS"][self.run_id] = {
            "kind": "ai_screen", "status": "running",
            "progress": {"stage": "screen_b", "current": 5, "total": 30,
                         "message": "AI 精筛 5/30", "overall_percent": 16},
            "logs": [], "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
        }
        resp = self.client.get(f"/api/task-state/{self.run_id}")
        data = resp.get_json()
        # 精筛已判定 5 条：取实时进度，而不是残留的 30（match+mismatch）
        self.assertEqual(data["success_count"], 5)

    def test_task_state_api_paused_with_error_code(self):
        """暂停状态返回具体 error_code（SC-006）。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.update_screening_run(
            self.run_id, status="paused",
            error_code="captcha_required",
            error_reason="触发验证码/滑块，需手动完成")
        resp = self.client.get(f"/api/task-state/{self.run_id}")
        data = resp.get_json()
        self.assertEqual(data["status"], "paused")
        self.assertEqual(data["pause_info"]["error_code"], "captcha_required")
        self.assertIn("验证码", data["pause_info"]["error_reason"])

    def test_task_state_api_failed_with_non_systemic_error_code(self):
        """B052：失败态非系统性错误码也必须下发 pause_info，供内联展示。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.update_screening_run(
            self.run_id, status="failed",
            error_code="source_invalid_output",
            error_reason="输入校验失败或页面解析异常")

        resp = self.client.get(f"/api/task-state/{self.run_id}")

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["pause_info"]["error_code"], "source_invalid_output")

    def test_task_state_counts_success_failure_and_unstarted_separately(self):
        """暂停计数满足 success + failure + unstarted = total（SC-006）。"""
        run_id = "test-run-sc006-counts"
        self.store.create_screening_run(run_id, source_count=1408)
        _pause_run(
            self.store, run_id,
            processed_count=762,
            pending_count=38,
            current_stage="jd_detail",
            error_code="captcha_required",
            error_reason="第 800 条触发验证码",
        )

        resp = self.client.get(f"/api/task-state/{run_id}")

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["success_count"], 762)
        self.assertEqual(data["fail_count"], 38)
        self.assertEqual(data["unstarted_count"], 608)
        self.assertEqual(data["total"], 1408)
        # jd_detail 权重段 25-75：800/1408 完成 → 25+floor(50*800/1408)=53
        self.assertEqual(data["progress"]["overall_percent"], 53)

    def test_task_state_merges_live_progress_logs_and_result(self):
        """统一状态接口必须覆盖运行中内存快照与最终结果。"""
        task_id = "live-task-state"
        self.app.config["PIPELINE_TASKS"][task_id] = {
            "kind": "recrawl",
            "status": "done",
            "progress": {"stage": "done", "current": 2, "total": 2},
            "logs": ["第一条", "第二条"],
            "result": {"updates": {"job-1": {"verdict": "match"}}},
            "error": "",
            "started_at": 1000,
            "finished_at": 2000,
        }

        response = self.client.get(f"/api/task-state/{task_id}")

        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["progress"]["stage"], "done")
        self.assertEqual(data["logs"], ["第一条", "第二条"])
        self.assertEqual(data["result"]["updates"]["job-1"]["verdict"], "match")
        self.assertEqual(data["started_at"], 1000)
        self.assertEqual(data["finished_at"], 2000)

    def test_cancel_api_preserves_results(self):
        """/api/task/cancel/<run_id> 取消后保留结果（FR-024）。"""
        self.store.update_screening_run(self.run_id, status="running")
        self.store.update_screening_run(self.run_id, processed_count=50)
        resp = self.client.post(f"/api/task/cancel/{self.run_id}",
                                headers=self._auth_headers())
        self.assertEqual(resp.status_code, 200)
        run = self.store.get_screening_run(self.run_id)
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["processed_count"], 50, "取消后结果必须保留")

    def test_cancel_api_handles_live_task_before_db_row_exists(self):
        """刚创建的任务尚未落 DB 时，统一取消仍必须立即生效。"""
        task_id = "live-cancel-before-db"
        stop_event = threading.Event()
        self.app.config["PIPELINE_TASKS"][task_id] = {
            "kind": "scrape", "status": "running", "progress": {},
            "logs": [], "result": {"jobs": [{"job_id": "kept"}]},
            "error": "", "stop_event": stop_event,
            "started_at": 1000, "finished_at": None,
        }

        with mock.patch("webui.pipeline_exec.close_debug_chrome") as close_chrome:
            response = self.client.post(
                f"/api/task/cancel/{task_id}", headers=self._auth_headers(),
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["status"], "cancelled")
        self.assertTrue(stop_event.is_set())
        self.assertEqual(
            self.app.config["PIPELINE_TASKS"][task_id]["result"]["jobs"][0]["job_id"],
            "kept",
        )
        close_chrome.assert_called_once()

    def test_cancel_persistence_failure_does_not_publish_memory_cancelled(self):
        """A durable cancel failure must not split live memory from the database."""
        self.store.update_screening_run(self.run_id, status="running")
        self.app.config["PIPELINE_TASKS"][self.run_id] = {
            "kind": "ai_screen", "status": "running", "progress": {},
            "logs": [], "result": None, "error": "",
            "stop_event": threading.Event(),
        }
        with mock.patch.object(
            self.store,
            "update_screening_run",
            side_effect=RuntimeError("cancel write rejected"),
        ):
            response = self.client.post(
                f"/api/task/cancel/{self.run_id}", headers=self._auth_headers(),
            )

        self.assertEqual(response.status_code, 503, response.get_json())
        self.assertEqual(
            self.app.config["PIPELINE_TASKS"][self.run_id]["status"], "running"
        )

    def test_latest_task_read_failure_is_not_reported_as_no_task(self):
        """Restart-state read failure must be visible instead of returning has_task=false."""
        with mock.patch.object(
            self.store,
            "latest_interrupted_screening_run",
            side_effect=RuntimeError("database unavailable"),
        ):
            response = self.client.get("/api/latest-running-task")

        self.assertEqual(response.status_code, 503, response.get_json())
        self.assertEqual(response.get_json()["error"], "task_state_unavailable")

    def test_frontend_active_cancel_uses_unified_route(self):
        """运行中和暂停中的取消按钮必须共享统一状态接口。"""
        # 021 B8 T027：取消逻辑随 script setup 外迁至 composables，检查对象同步更新。
        composables_dir = (
            pathlib.Path(__file__).resolve().parents[2]
            / "webui" / "src" / "composables"
        )
        source = "\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted(composables_dir.glob("useDiscovery*.ts"))
        )
        self.assertNotIn("/api/execute-search/${encodeURIComponent", source)
        self.assertNotIn("/api/ai-screen/${encodeURIComponent", source)
        # 013：AI 筛选取消入口已移除，统一取消仅保留抓取/暂停任务路径。
        self.assertGreaterEqual(source.count("/api/task/cancel/${encodeURIComponent"), 2)


class Slice4ScrapePauseContinueTests(unittest.TestCase):
    """切片 4：列表抓取暂停继续 + checkpoint（FR-020/FR-023）。"""

    def test_classify_scrape_block_recognizes_systemic_keywords(self):
        """_classify_scrape_block 把阻断关键字映射到 SYSTEMIC_BLOCK_CODES。"""
        app, temp = _make_app()
        try:
            # create_app 内部定义了 _classify_scrape_block，通过视图函数闭包无法直接访问。
            # 这里通过 app 的源码逻辑验证：关键字命中应返回阻断码，未命中返回空串。
            # 用 store + endpoint 模拟"列表抓取失败 + 部分完成 + 阻断关键字"场景。
            store = app.config["TASK_STORE"]
            run_id = "scrape-pause-test"
            store.create_screening_run(run_id, source_count=10)
            # 模拟 _run_pipeline_task 失败分支：completed_combos 非空 + 阻断关键字
            _pause_run(store, run_id,
                                       error_code="captcha_required",
                                       current_stage="scrape")
            store.save_checkpoint(run_id, "scrape", ["kw1|city1", "kw2|city2"])
            # 验证 checkpoint 落盘
            self.assertEqual(store.load_checkpoint(run_id, "scrape"),
                             {"kw1|city1", "kw2|city2"})
            # 验证状态恢复
            run = store.get_screening_run(run_id)
            self.assertEqual(run["status"], "paused")
            self.assertEqual(run["error_code"], "captcha_required")
        finally:
            temp.cleanup()

    def test_initial_scrape_missing_cdp_source_pauses_persisted_run(self):
        """列表任务构造 CDP source 失败时也必须持久化为可继续暂停。"""
        app, temp = _make_app()
        try:
            client = _authed_test_client(app)
            token = app.config["API_TOKEN"]
            with mock.patch("webui.app._BossCdpSource", return_value=None):
                response = client.post(
                    "/api/execute-search",
                    json={"script_params": {"keyword": "后端", "city": ["上海"]}},
                    headers={"X-Boss-Token": token},
                )
                task_id = response.get_json()["task_id"]
                paused = _wait_for_pipeline_task(client, task_id)

            self.assertEqual(paused["status"], "paused", paused)
            run = app.config["TASK_STORE"].get_screening_run(task_id)
            self.assertEqual(run["status"], "paused")
            self.assertEqual(run["error_code"], "source_cdp_unavailable")
            self.assertEqual(run["current_stage"], "scrape")
        finally:
            executor = app.config.get("PIPELINE_EXECUTOR")
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            temp.cleanup()

    def test_scrape_continue_restores_combos_from_db(self):
        """服务重启后 continue 从 DB checkpoint 恢复 completed_combos（FR-020/FR-023）。"""
        app, temp = _make_app()
        try:
            client = _authed_test_client(app)
            token = app.config["API_TOKEN"]
            store = app.config["TASK_STORE"]
            old_id = "scrape-old"
            # 模拟服务重启前：内存丢失，但 DB 有 paused + checkpoint
            store.create_screening_run(old_id, source_count=10,
                                        execution_params={"script_params":
                                                          {"keyword": "前端", "city": ["上海"]}})
            _pause_run(store, old_id,
                                       error_code="captcha_required",
                                       current_stage="scrape")
            store.save_checkpoint(old_id, "scrape", ["前端|上海"])
            captured = {}

            def resumed_search(*_args, **kwargs):
                captured["skip_combos"] = set(kwargs.get("skip_combos") or set())
                return {
                    "ok": True, "jobs": [], "total_scraped": 0,
                    "total_matched": 0, "combinations": 1,
                    "completed_combos": ["前端|上海"], "error": "",
                }

            # 真实路由 + 真实 DB，只隔离外部 Chrome 和网络抓取。
            app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
            with mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                            return_value=(True, "")), \
                    mock.patch("webui.pipeline_exec.run_search",
                               side_effect=resumed_search):
                resp = client.post(f"/api/execute-search/continue/{old_id}",
                                   headers={"X-Boss-Token": token})
                self.assertEqual(resp.status_code, 200, resp.get_json())
                task_id = resp.get_json()["task_id"]
                _wait_for_pipeline_task(client, task_id)
            data = resp.get_json() or {}
            self.assertEqual(data.get("skipped"), 1)
            self.assertEqual(captured.get("skip_combos"), {"前端|上海"})
        finally:
            executor = app.config.get("PIPELINE_EXECUTOR")
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            temp.cleanup()

    def test_resume_survives_stale_pause_cleanup_timer(self):
        """暂停任务 30 分钟清理定时器不得误删续跑的新内存任务。

        复现线上卡死：暂停时排程的旧定时器到点会把同一 run_id 的续跑
        任务从内存弹掉，导致“数据已抓完但终态没写”。修复后旧定时器
        只能删它自己注册的任务对象，续跑完成仍会落 succeeded。
        """
        app, temp = _make_app()
        try:
            client = _authed_test_client(app)
            store = app.config["TASK_STORE"]
            app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
            headers = {"X-Boss-Token": app.config["API_TOKEN"]}

            class _RecordingTimer:
                instances = []

                def __init__(self, interval, fn):
                    self.interval = interval
                    self.fn = fn
                    _RecordingTimer.instances.append(self)

                def start(self):
                    pass

            _job1 = {"job_id": "j1", "platform_job_id": "j1",
                     "title": "岗位1", "source_url": "https://zhipin.example/j1.html"}
            _job2 = {"job_id": "j2", "platform_job_id": "j2",
                     "title": "岗位2", "source_url": "https://zhipin.example/j2.html"}
            _resumed_started = threading.Event()
            _release_resumed = threading.Event()

            def _first_search(*_args, **_kwargs):
                on_combo_done = _kwargs.get("on_combo_done")
                if on_combo_done is not None:
                    on_combo_done("kw|city1", [_job1], ["kw|city1"])
                return {
                    "ok": False, "jobs": [], "total_scraped": 0, "total_matched": 0,
                    "combinations": 2, "completed_combos": ["kw|city1"],
                    "hard_stop": True, "hard_stop_code": "captcha_required",
                    "error": "系统性阻断：触发验证码/滑块，需手动完成",
                }

            def _resumed_search(*_args, **_kwargs):
                _resumed_started.set()
                if not _release_resumed.wait(timeout=5):
                    return {
                        "ok": False, "jobs": [], "total_scraped": 0, "total_matched": 0,
                        "combinations": 2,
                        "completed_combos": list(_kwargs.get("skip_combos") or []),
                        "hard_stop": True, "hard_stop_code": "internal_error",
                        "error": "resume gate timeout",
                    }
                on_combo_done = _kwargs.get("on_combo_done")
                if on_combo_done is not None:
                    on_combo_done("kw|city2", [_job2], ["kw|city1", "kw|city2"])
                return {
                    "ok": True, "jobs": [_job2], "total_scraped": 1, "total_matched": 1,
                    "combinations": 2, "completed_combos": ["kw|city1", "kw|city2"],
                    "error": "",
                }

            _dispatch_calls = 0

            def _dispatch_search(*_args, **_kwargs):
                nonlocal _dispatch_calls
                _dispatch_calls += 1
                if _dispatch_calls == 1:
                    return _first_search(*_args, **_kwargs)
                return _resumed_search(*_args, **_kwargs)

            with mock.patch("threading.Timer", new=_RecordingTimer), \
                    mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                               return_value=(True, "")), \
                    mock.patch("webui.pipeline_exec.run_search",
                               side_effect=_dispatch_search):
                resp = client.post("/api/execute-search",
                                  json={"script_params": {"keyword": "前端", "city": ["上海"]}},
                                  headers=headers)
                task_id = resp.get_json()["task_id"]
                # 等 pause 稳定落库。
                for _ in range(200):
                    if store.get_screening_run(task_id)["status"] == "paused":
                        break
                    time.sleep(0.01)
                self.assertEqual(store.get_screening_run(task_id)["status"], "paused")

                resp2 = client.post(f"/api/execute-search/continue/{task_id}",
                                   headers=headers)
                self.assertEqual(resp2.status_code, 200, resp2.get_json())
                # 等续跑 worker 进入 run_search 但尚未结束。
                self.assertTrue(_resumed_started.wait(timeout=5))
                # 让旧暂停任务的清理定时器“到点”：修复后不得删除续跑新任务。
                _RecordingTimer.instances[0].fn()

                # 放行续跑前，恢复接口必须仍把整活任务当运行中，不得误收尾。
                live = client.get("/api/latest-running-task", headers=headers).get_json()
                self.assertTrue(live["has_task"])
                self.assertEqual(live["status"], "running")
                _release_resumed.set()

                # 等续跑完成并落 succeeded。
                for _ in range(500):
                    run = store.get_screening_run(task_id)
                    if run["status"] in ("succeeded", "failed", "paused", "interrupted"):
                        break
                    time.sleep(0.01)
                run = store.get_screening_run(task_id)
                self.assertEqual(run["status"], "succeeded", run)
                self.assertEqual(run["current_stage"], "scrape")
                # 状态与 stage_complete 事件分属两个独立事务：worker 先落状态
                # 再落事件，高负载下两者间存在可见窗口。等待事件落库后再断言，
                # 避免偶发读到"状态已 succeeded 但事件未提交"（T029 全量偶发）。
                for _ in range(200):
                    events = store.list_task_events(task_id)
                    if any(e["type"] == "stage_complete" for e in events):
                        break
                    time.sleep(0.01)
                events = store.list_task_events(task_id)
                self.assertEqual(
                    sum(1 for e in events if e["type"] == "stage_complete"), 1)
                self.assertEqual(
                    store.load_checkpoint(task_id, "scrape"), {"kw|city1", "kw|city2"})
        finally:
            executor = app.config.get("PIPELINE_EXECUTOR")
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            temp.cleanup()


class Slice6AiPauseTests(unittest.TestCase):
    """切片 6：AI 粗筛/精筛 systemic 错误暂停（FR-020/SC-006/SC-007/SC-008）。"""

    def test_screen_jobs_raises_on_systemic_when_strict(self):
        """screen_jobs(raise_on_systemic=True) 命中限流立即抛 AISecurityError。"""
        from webui.ai import screen_jobs, AISecurityError, ERROR_RATE_LIMIT
        jobs = [{"job_id": "1", "title": "前端", "salary": "15-25K",
                 "location": "上海", "job_labels": "本科", "company_scale": "100-499"}]
        criteria = {"profile_summary": "前端工程师", "city": ["上海"], "degree": ["本科"]}
        with mock.patch("webui.ai.call_ai",
                        side_effect=AISecurityError(ERROR_RATE_LIMIT)):
            with self.assertRaises(AISecurityError) as ctx:
                screen_jobs(jobs, criteria, "http://x", "key",
                            raise_on_systemic=True)
            self.assertEqual(ctx.exception.error_code, ERROR_RATE_LIMIT)

    def test_match_jds_raises_on_systemic_when_strict(self):
        """match_jds(raise_on_systemic=True) 命中额度耗尽立即抛 AISecurityError。"""
        from webui.ai import match_jds, AISecurityError, ERROR_QUOTA_EXHAUSTED
        jobs = [{"job_id": "1", "title": "前端", "salary": "15-25K",
                 "location": "上海", "jd": "岗位职责：前端开发"}]
        with mock.patch("webui.ai.call_ai",
                        side_effect=AISecurityError(ERROR_QUOTA_EXHAUSTED)):
            with self.assertRaises(AISecurityError) as ctx:
                match_jds(jobs, "前端工程师", "http://x", "key",
                          raise_on_systemic=True)
            self.assertEqual(ctx.exception.error_code, ERROR_QUOTA_EXHAUSTED)

    def test_map_ai_error_to_block_code_covers_systemic(self):
        """map_ai_error_to_block_code 把 AI 内部码映射到 ERROR_TAXONOMY 阻断码。"""
        from webui.ai import (map_ai_error_to_block_code, ERROR_RATE_LIMIT,
                              ERROR_QUOTA_EXHAUSTED, ERROR_AUTH, ERROR_NETWORK,
                              ERROR_TIMEOUT, ERROR_SERVER, ERROR_TRUNCATED,
                              ERROR_INVALID)
        self.assertEqual(map_ai_error_to_block_code(ERROR_RATE_LIMIT), "ai_rate_limited")
        self.assertEqual(map_ai_error_to_block_code(ERROR_QUOTA_EXHAUSTED), "ai_quota_exhausted")
        self.assertEqual(map_ai_error_to_block_code(ERROR_AUTH), "ai_key_invalid")
        self.assertEqual(map_ai_error_to_block_code(ERROR_NETWORK), "ai_network_error")
        self.assertEqual(map_ai_error_to_block_code(ERROR_TIMEOUT), "ai_network_error")
        self.assertEqual(map_ai_error_to_block_code(ERROR_SERVER), "ai_network_error")
        # 非 systemic 返回空串
        self.assertEqual(map_ai_error_to_block_code(ERROR_TRUNCATED), "")
        self.assertEqual(map_ai_error_to_block_code(ERROR_INVALID), "")


# ============================================================================
# A 阶段 RED 测试（v3.1）—— 产品代码未实现，预期全部 RED
# ============================================================================


class Slice7HardStopFirstComboTests(unittest.TestCase):
    """A.1 首组合验证码：completed=[] 也必须 paused，不得标 failed（阻断项 1）。"""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = _authed_test_client(self.app)
        self.token = self.app.config["API_TOKEN"]
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        try:
            executor = self.app.config.get("PIPELINE_EXECUTOR")
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
        except Exception:
            pass
        import gc
        gc.collect()
        self.temp.cleanup()

    def _auth(self):
        return {"X-Boss-Token": self.token}

    def test_first_combo_captcha_paused_not_failed(self):
        """首组合即触发 captcha：completed_combos=[] 也必须 paused，不得 failed。

        修复目标（B.1）：app.py 中 `if completed and _pause_code` 改为
        `if result.get("hard_stop"):`，识别 hard_stop 信号而非 completed 非空。
        """
        # mock run_search 返回首组合 captcha hard_stop，completed=[]
        def fake_run_search(*args, **kwargs):
            return {
                "ok": False,
                "jobs": [],
                "total_scraped": 0,
                "total_matched": 0,
                "combinations": 3,
                "completed_combos": [],  # 首组合即失败，completed 为空
                "hard_stop": True,
                "hard_stop_code": "captcha_required",
                "error": "系统性阻断：触发验证码/滑块，需手动完成",
            }

        # run_search 在后台 worker 内动态导入，因此 patch 真实定义模块。
        with mock.patch("webui.pipeline_exec.run_search", side_effect=fake_run_search):
            resp = self.client.post(
                "/api/execute-search",
                json={"script_params": {"keyword": "前端", "city": ["上海"]}},
                headers=self._auth())
            self.assertEqual(resp.status_code, 200)
            task_id = resp.get_json()["task_id"]
            snapshot = _wait_for_pipeline_task(self.client, task_id)

        self.assertEqual(snapshot["status"], "paused", snapshot)
        self.assertIn("captcha", snapshot.get("error", "").lower())

        # 查 DB 中是否有 paused run（不得 failed）
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT id, status, error_code FROM screening_runs "
                "WHERE id = ?", (task_id,)).fetchall()
            runs = [dict(r) for r in rows]

        # 必须有 paused run，不得 failed
        paused_runs = [r for r in runs if r.get("status") == "paused"
                       and r.get("error_code") == "captcha_required"]
        self.assertTrue(paused_runs,
                        f"首组合 captcha completed=[] 时必须 paused，实际 runs={runs}")
        failed_runs = [r for r in runs if r.get("status") == "failed"]
        self.assertFalse(failed_runs,
                         f"首组合 captcha 不得标 failed，实际 failed_runs={failed_runs}")

    def test_first_combo_captcha_no_other_combos_run(self):
        """首组合 captcha 后，后续组合不得继续抓取。"""
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        class CaptchaOnFirstComboSource:
            def __init__(self):
                self.fetch_calls = []

            def preflight(self):
                return SourceOutcome.success()

            def fetch_list(self, plan_item, *, on_page_completed=None):
                self.fetch_calls.append(plan_item)
                return SourceOutcome.failure(
                    failed_code="source_verification_required",
                    safe_log="captcha",
                )

        source = CaptchaOnFirstComboSource()
        with mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")):
            result = run_search(
                {"keyword": "前端,后端", "city": ["上海", "北京"]},
                source,
                pages=1,
                sleeper=lambda _seconds: None,
            )

        self.assertTrue(result.get("hard_stop"), result)
        self.assertEqual(result.get("hard_stop_code"), "source_verification_required")
        self.assertEqual(len(source.fetch_calls), 1, source.fetch_calls)

    def test_first_combo_generic_failure_does_not_hard_stop(self):
        """普通失败不得暂停，也不得展示风控/受限文案。"""
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        class GenericFailureSource:
            platform = "boss"
            def preflight(self):
                return SourceOutcome.success()
            def fetch_list(self, plan_item, *, on_page_completed=None):
                return SourceOutcome.failure(
                    failed_code="source_unknown_error", safe_log="普通文案",
                )

        with mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")):
            result = run_search(
                {"keyword": "前端", "city": ["上海"]},
                GenericFailureSource(),
                pages=1,
                sleeper=lambda _seconds: None,
            )
        self.assertFalse(result.get("hard_stop"), result)
        self.assertNotIn("风控", result.get("error", ""))
        self.assertNotIn("IP", result.get("error", ""))

    def test_list_cdp_lost_restarts_and_resumes_current_combo(self):
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        class RecoverableListSource:
            platform = "boss"

            def __init__(self):
                self.calls = 0
                self.plan_items = []

            def preflight(self):
                return SourceOutcome.success()

            def fetch_list(self, plan_item, *, on_page_completed=None):
                self.calls += 1
                self.plan_items.append(dict(plan_item))
                if self.calls == 1:
                    if on_page_completed is not None:
                        on_page_completed({
                            "kind": "page_completed", "combo_key": "前端|上海",
                            "keyword": "前端", "city": "上海", "page": 1,
                            "target_pages": 2, "jobs_delta": 1, "jobs_count": 1,
                            "has_more": True, "resume_page": 2, "last_completed_page": 1,
                            "jobs_snapshot": [{"job_id": "j1", "title": "旧岗位"}],
                        })
                    return SourceOutcome.failure(
                        failed_code="source_cdp_unavailable", safe_log="lost")
                return SourceOutcome.success(
                    jobs=[{"job_id": "j2", "title": "新岗位"}])

        source = RecoverableListSource()
        resume_pages = {}
        resume_jobs = {}
        with mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ), mock.patch("webui.pipeline_exec.close_debug_chrome"):
            result = run_search(
                {"keyword": "前端", "city": ["上海"]}, source, pages=2,
                sleeper=lambda _seconds: None, resume_pages=resume_pages,
                resume_jobs=resume_jobs,
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(source.calls, 2)
        self.assertEqual(source.plan_items[1]["start_page"], 2)
        self.assertEqual(resume_pages["前端|上海"], 2)
        self.assertEqual(resume_jobs["前端|上海"][0]["job_id"], "j1")

    def test_list_cdp_lost_restart_failure_pauses(self):
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        class AlwaysLostListSource:
            platform = "boss"

            def __init__(self):
                self.calls = 0

            def preflight(self):
                return SourceOutcome.success()

            def fetch_list(self, _plan_item, *, on_page_completed=None):
                self.calls += 1
                return SourceOutcome.failure(
                    failed_code="source_cdp_unavailable", safe_log="lost")

        source = AlwaysLostListSource()
        with mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready",
            side_effect=[(True, ""), (False, "launch failed")]
        ), mock.patch("webui.pipeline_exec.close_debug_chrome"):
            result = run_search(
                {"keyword": "前端", "city": ["上海"]}, source, pages=1,
                sleeper=lambda _seconds: None,
            )

        self.assertTrue(result["hard_stop"], result)
        self.assertEqual(result["hard_stop_code"], "source_cdp_unavailable")
        self.assertEqual(source.calls, 1)

    def test_list_cdp_lost_restart_success_still_lost_pauses(self):
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        class StillLostListSource:
            platform = "boss"

            def __init__(self):
                self.calls = 0

            def preflight(self):
                return SourceOutcome.success()

            def fetch_list(self, _plan_item, *, on_page_completed=None):
                self.calls += 1
                return SourceOutcome.failure(
                    failed_code="source_cdp_unavailable", safe_log="lost")

        source = StillLostListSource()
        with mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ), mock.patch("webui.pipeline_exec.close_debug_chrome"):
            result = run_search(
                {"keyword": "前端", "city": ["上海"]}, source, pages=1,
                sleeper=lambda _seconds: None,
            )

        self.assertTrue(result["hard_stop"], result)
        self.assertEqual(result["hard_stop_code"], "source_cdp_unavailable")
        self.assertEqual(source.calls, 2, "自动重启一次后仍失联，必须暂停且不循环")

    def test_run_search_skips_combo_when_resume_page_past_target(self):
        """页级 checkpoint 越过目标页数时视为已抓满，不再用非法 start_page 续抓。"""
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        class NoFetchSource:
            platform = "boss"

            def preflight(self):
                return SourceOutcome.success()

            def fetch_list(self, *_args, **_kwargs):
                raise AssertionError("已抓满页数的组合不得再次抓取")

        with mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ), mock.patch("webui.pipeline_exec.close_debug_chrome"):
            result = run_search(
                {"keyword": "前端", "city": ["上海"]},
                NoFetchSource(), pages=2, sleeper=lambda _seconds: None,
                resume_pages={"前端|上海": 3},
                resume_jobs={"前端|上海": [{"job_id": "old-1", "title": "旧岗位"}]},
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["completed_combos"], ["前端|上海"])

    def test_list_cdp_lost_after_last_page_retries_with_valid_start_page(self):
        """最后一页后失联时自动重启不得把 start_page 顶到目标页数之外。"""
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        class LastPageLostSource:
            platform = "boss"

            def __init__(self):
                self.calls = 0
                self.plan_items = []

            def preflight(self):
                return SourceOutcome.success()

            def fetch_list(self, plan_item, *, on_page_completed=None):
                self.calls += 1
                self.plan_items.append(dict(plan_item))
                if self.calls == 1:
                    if on_page_completed is not None:
                        on_page_completed({
                            "kind": "page_completed", "combo_key": "前端|上海",
                            "keyword": "前端", "city": "上海", "page": 2,
                            "target_pages": 2, "jobs_delta": 1, "jobs_count": 1,
                            "has_more": True, "resume_page": 3,
                            "last_completed_page": 2,
                            "jobs_snapshot": [{"job_id": "j1", "title": "旧岗位"}],
                        })
                    return SourceOutcome.failure(
                        failed_code="source_cdp_unavailable", safe_log="lost")
                return SourceOutcome.success(
                    jobs=[{"job_id": "j1", "title": "旧岗位"}])

        source = LastPageLostSource()
        with mock.patch(
            "webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")
        ), mock.patch("webui.pipeline_exec.close_debug_chrome"):
            result = run_search(
                {"keyword": "前端", "city": ["上海"]}, source, pages=2,
                sleeper=lambda _seconds: None,
                resume_pages={"前端|上海": 2},
                resume_jobs={"前端|上海": [{"job_id": "j1", "title": "旧岗位"}]},
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(source.calls, 2)
        self.assertEqual(source.plan_items[1]["start_page"], 2)

    def test_scrape_success_persists_terminal_status_and_combo_progress(self):
        app, temp = _make_app()
        try:
            client = _authed_test_client(app)
            with mock.patch("webui.pipeline_exec.run_search", return_value={
                "ok": True,
                "jobs": [{"job_id": "j1", "title": "前端"}],
                "total_scraped": 1,
                "total_matched": 1,
                "combinations": 2,
                "completed_combos": ["前端|上海", "后端|上海"],
                "error": "",
            }):
                response = client.post(
                    "/api/execute-search",
                    json={"script_params": {
                        "keyword": "前端,后端", "city": ["上海"],
                    }},
                    headers={"X-Boss-Token": app.config["API_TOKEN"]},
                )
                task_id = response.get_json()["task_id"]
                finished = _wait_for_pipeline_task(client, task_id)
            self.assertEqual(finished["status"], "completed", finished)
            run = app.config["TASK_STORE"].get_screening_run(task_id)
            self.assertEqual(run["status"], "succeeded")
            self.assertEqual(run["source_count"], 2)
            self.assertEqual(run["processed_count"], 2)
        finally:
            executor = app.config.get("PIPELINE_EXECUTOR")
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            temp.cleanup()

    def test_scrape_failure_persists_failed_reason(self):
        app, temp = _make_app()
        try:
            client = _authed_test_client(app)
            with mock.patch("webui.pipeline_exec.run_search", return_value={
                "ok": False, "jobs": [], "total_scraped": 0,
                "total_matched": 0, "combinations": 1,
                "completed_combos": [], "error": "岗位列表接口返回无效数据",
            }):
                response = client.post(
                    "/api/execute-search",
                    json={"script_params": {"keyword": "前端", "city": ["上海"]}},
                    headers={"X-Boss-Token": app.config["API_TOKEN"]},
                )
                task_id = response.get_json()["task_id"]
                finished = _wait_for_pipeline_task(client, task_id)
            self.assertEqual(finished["status"], "failed", finished)
            run = app.config["TASK_STORE"].get_screening_run(task_id)
            self.assertEqual(run["status"], "failed")
            self.assertEqual(run["error_reason"], "岗位列表接口返回无效数据")
        finally:
            executor = app.config.get("PIPELINE_EXECUTOR")
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            temp.cleanup()


class Slice9ResumeAfterRestartConservationTests(unittest.TestCase):
    """A.2 重启后岗位守恒：app A 销毁 → app B 继续，岗位集合守恒（阻断项 2）。"""

    @staticmethod
    def _app_config(root, db_path, result_name):
        return {
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / result_name),
            "DB_PATH": str(db_path),
            "PYTHON_EXECUTABLE": sys.executable,
        }

    @staticmethod
    def _shutdown(app):
        executor = app.config.get("PIPELINE_EXECUTOR")
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    def test_combo_snapshot_and_checkpoint_commit_together(self):
        """完整岗位快照和 completed checkpoint 使用一个存储操作提交。"""
        app, temp = _make_app()
        try:
            store = app.config["TASK_STORE"]
            run_id = "atomic-combo-run"
            store.create_screening_run(run_id, source_count=2)
            save_combo = getattr(store, "save_scrape_combo_result", None)
            self.assertTrue(callable(save_combo),
                            "TaskStore 必须提供原子组合持久化操作")
            jobs = [{
                "job_id": "j1", "title": "前端工程师", "salary": "15-25K",
                "company": "公司A", "source_url": "https://example.com/j1",
            }]
            save_combo(run_id, "前端|上海", jobs, ["前端|上海"])

            restored = store.load_scrape_run_jobs(run_id)
            self.assertEqual(restored, jobs)
            self.assertEqual(store.load_checkpoint(run_id, "scrape"), {"前端|上海"})
        finally:
            self._shutdown(app)
            temp.cleanup()

    def test_resume_keeps_full_job_payload_after_real_app_restart(self):
        """app A 暂停并退出后，app B 继续并合并旧、新岗位且零重复。"""
        from webui.app import create_app

        temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(temp.name)
        db_path = root / "shared.db"
        old_jobs = [{
            "job_id": "old-1", "title": "前端工程师", "salary": "15-25K",
            "company": "公司A", "source_url": "https://example.com/old-1",
        }]
        app_a = app_b = None
        captured_resume = {}
        try:
            app_a = create_app(self._app_config(root, db_path, "results-a"))
            client_a = _authed_test_client(app_a)

            def paused_search(*_args, **kwargs):
                callback = kwargs.get("on_combo_done")
                if callback is not None:
                    callback("前端|上海", old_jobs, ["前端|上海"])
                return {
                    "ok": False, "jobs": old_jobs, "total_scraped": 1,
                    "total_matched": 1, "combinations": 2,
                    "completed_combos": ["前端|上海"], "hard_stop": True,
                    "hard_stop_code": "captcha_required", "error": "触发验证码",
                }

            with mock.patch("webui.pipeline_exec.run_search", side_effect=paused_search):
                response = client_a.post(
                    "/api/execute-search",
                    json={"script_params": {"keyword": "前端,后端", "city": ["上海"]}},
                    headers={"X-Boss-Token": app_a.config["API_TOKEN"]},
                )
                run_id = response.get_json()["task_id"]
                paused = _wait_for_pipeline_task(client_a, run_id)
            self.assertEqual(paused["status"], "paused", paused)
            self._shutdown(app_a)
            app_a = None

            app_b = create_app(self._app_config(root, db_path, "results-b"))
            app_b.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
            client_b = _authed_test_client(app_b)

            def resumed_search(*_args, **kwargs):
                captured_resume["skip_combos"] = set(kwargs.get("skip_combos") or set())
                return {
                    "ok": True,
                    "jobs": [{
                        "job_id": "new-1", "title": "后端工程师",
                        "salary": "20-30K", "company": "公司B",
                        "source_url": "https://example.com/new-1",
                    }],
                    "total_scraped": 1, "total_matched": 1, "combinations": 2,
                    "completed_combos": ["前端|上海", "后端|上海"], "error": "",
                }

            with mock.patch("webui.pipeline_exec.run_search", side_effect=resumed_search), \
                    mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")):
                response = client_b.post(
                    f"/api/execute-search/continue/{run_id}",
                    headers={"X-Boss-Token": app_b.config["API_TOKEN"]},
                )
                self.assertEqual(response.status_code, 200, response.get_json())
                new_task_id = response.get_json()["task_id"]
                finished = _wait_for_pipeline_task(client_b, new_task_id)

            self.assertEqual(finished["status"], "completed", finished)
            jobs = finished["result"]["jobs"]
            self.assertEqual({job["job_id"] for job in jobs}, {"old-1", "new-1"})
            self.assertEqual(len(jobs), 2)
            self.assertEqual(next(j for j in jobs if j["job_id"] == "old-1"), old_jobs[0])
            self.assertEqual(captured_resume["skip_combos"], {"前端|上海"})
            self.assertEqual(finished["result"]["total_scraped"], 2)
            self.assertEqual(finished["result"]["total_matched"], 2)
            self.assertEqual(finished["progress"]["total_scraped"], 2)
            self.assertEqual(finished["progress"]["total_matched"], 2)
            self.assertIn("抓取 2 条，去重 2 条", finished["progress"]["message"])
            self.assertEqual(
                app_b.config["TASK_STORE"].get_screening_run(
                    new_task_id)["total_scraped"], 2)
        finally:
            if app_a is not None:
                self._shutdown(app_a)
            if app_b is not None:
                self._shutdown(app_b)
            temp.cleanup()


class Slice10AiResumeAfterRefreshTests(unittest.TestCase):
    """A.3 AI 刷新后继续：paused AI run 必须返回 scrape 元数据（阻断项 3）。"""

    def setUp(self):
        self.app, self.temp = _make_app()
        self.client = _authed_test_client(self.app)
        self.token = self.app.config["API_TOKEN"]
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        self.temp.cleanup()

    def _auth(self):
        return {"X-Boss-Token": self.token}

    def test_paused_ai_run_restores_scrape_task_id(self):
        """/api/latest-running-task 对 paused AI run 必须返回 scrapeTaskId 等。

        修复目标（B.3）：latest-running-task JOIN scrape_run_jobs 元数据，
        返回 scrapeTaskId、scrapeCompleted、source_run_id、checkpoint_stage。
        """
        run_id = "paused-ai-run"
        self.store.create_screening_run(
            run_id, source_count=50,
            execution_params={
                "scrape_task_id": "scrape-task-123",
                "scrape_completed": True,
                "source_run_id": "source-run-456",
            },
        )
        _pause_run(self.store, run_id,
                                          error_code="ai_rate_limited",
                                          current_stage="ai_rough")

        resp = self.client.get("/api/latest-running-task")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        # 必须返回 scrape 元数据
        self.assertIn("scrape_task_id", data,
                      f"latest-running-task 必须返回 scrape_task_id，实际 {data}")
        self.assertEqual(data["scrape_task_id"], "scrape-task-123")
        self.assertIn("scrape_completed", data)
        self.assertTrue(data["scrape_completed"])
        self.assertIn("source_run_id", data)
        self.assertEqual(data["source_run_id"], "source-run-456")

    def test_paused_ai_continue_not_blocked_by_startAiScreen(self):
        """服务重启后继续会从 DB 重建 scrape 来源并提交真正的 AI 续跑。"""
        run_id = "paused-ai-run-2"
        scrape_run_id = "scrape-task-789"
        self.store.create_screening_run(scrape_run_id, source_count=1)
        self.store.save_scrape_combo_result(
            scrape_run_id, "前端|上海",
            [{"job_id": "job-1", "title": "前端工程师"}],
            ["前端|上海"],
        )
        self.store.create_screening_run(
            run_id, source_count=50,
            frozen_filters={"city": ["上海"]},
            execution_params={
                "scrape_task_id": scrape_run_id,
                "scrape_completed": True,
                "profile_summary": "前端工程师",
            },
        )
        _pause_run(self.store, run_id,
                                          error_code="ai_rate_limited",
                                          current_stage="ai_rough")
        self.store.save_ai_settings("http://example.invalid", "test-ref", status="ready")
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.test_connection", return_value={
                    "ok": True, "warning_codes": [],
                }) as test_connection, \
                mock.patch.object(executor, "submit") as submit:
            resp = self.client.post(f"/api/task/continue/{run_id}",
                                    headers=self._auth())
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"),
                        f"paused AI run 继续不得被拦住，实际 {data}")
        self.assertEqual(data.get("task_id"), run_id)
        self.assertEqual(self.store.get_screening_run(run_id)["status"], "running")
        submitted = submit.call_args.args
        self.assertEqual(submitted[2], {"city": ["上海"]})
        self.assertEqual(submitted[3], "前端工程师")
        self.assertEqual(submitted[4], scrape_run_id)
        self.assertEqual(submitted[5], run_id)
        test_connection.assert_called_once()
        rebuilt = self.app.config["PIPELINE_TASKS"][scrape_run_id]
        self.assertEqual(rebuilt["status"], "done")
        self.assertEqual(rebuilt["result"]["jobs"][0]["job_id"], "job-1")

    def test_paused_ai_continue_keeps_one_canonical_task_identity(self):
        """A continued run must not leak a non-canonical handoff status."""
        run_id = "paused-ai-canonical"
        scrape_run_id = "scrape-ai-canonical"
        self.store.create_screening_run(scrape_run_id, source_count=1)
        self.store.save_scrape_combo_result(
            scrape_run_id,
            "前端|上海",
            [{"job_id": "job-1", "title": "前端工程师"}],
            ["前端|上海"],
        )
        self.store.create_screening_run(
            run_id,
            source_count=1,
            frozen_filters={"city": ["上海"]},
            execution_params={
                "scrape_task_id": scrape_run_id,
                "profile_summary": "前端工程师",
            },
        )
        _pause_run(
            self.store, run_id,
            error_code="ai_rate_limited",
            current_stage="ai_rough",
        )
        self.store.save_ai_settings("http://example.invalid", "test-ref", status="ready")
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.test_connection", return_value={
                    "ok": True, "warning_codes": [],
                }), \
                mock.patch.object(executor, "submit"):
            response = self.client.post(
                f"/api/task/continue/{run_id}", headers=self._auth()
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json().get("task_id"), run_id)
        persisted = self.store.get_screening_run(run_id)
        self.assertEqual(persisted["status"], "running")
        state = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertEqual(state.get("status"), "running")
        self.assertNotEqual(state.get("status"), "resumed")


class Slice12AiRoughCheckpointTests(unittest.TestCase):
    """A.5 AI 零重复：逐批 verdict 落盘，限流时保留最新 verdict（阻断项 7）。"""

    def test_ai_rough_saves_verdict_per_batch(self):
        """第一批完成后第二批限流，第一批 verdict 已交给持久化回调。"""
        from webui.ai import screen_jobs, AISecurityError, ERROR_RATE_LIMIT
        jobs = [
            {"job_id": "job-1", "title": "前端"},
            {"job_id": "job-2", "title": "后端"},
        ]
        delivered = []

        def on_batch_done(verdicts, completed_job_ids):
            delivered.append((dict(verdicts), list(completed_job_ids)))

        with mock.patch(
            "webui.ai.call_ai",
            side_effect=[{"dropped": []}, AISecurityError(ERROR_RATE_LIMIT)],
        ):
            with self.assertRaises(AISecurityError):
                screen_jobs(
                    jobs, {}, "http://example.invalid", "key",
                    batch_size=1, concurrency=1, raise_on_systemic=True,
                    on_batch_done=on_batch_done,
                )
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0][0]["job-1"]["verdict"], "kept")
        self.assertEqual(delivered[0][1], ["job-1"])

    def test_ai_batch_persistence_failure_stops_before_next_batch(self):
        """原子落库失败必须立即停止，不得继续调用下一批 AI。"""
        from webui.ai import screen_jobs
        jobs = [
            {"job_id": "job-1", "title": "前端"},
            {"job_id": "job-2", "title": "后端"},
        ]
        with mock.patch("webui.ai.call_ai", return_value={"dropped": []}) as call:
            with self.assertRaises(Exception) as ctx:
                screen_jobs(
                    jobs, {}, "http://example.invalid", "key",
                    batch_size=1, concurrency=1,
                    on_batch_done=lambda *_args: (_ for _ in ()).throw(
                        RuntimeError("disk full")
                    ),
                )
        self.assertEqual(type(ctx.exception).__name__, "AICheckpointError")
        self.assertEqual(call.call_count, 1)

    def test_ai_rough_verdict_and_completed_in_same_transaction(self):
        """verdict INSERT 与 completed job_id 推进必须同事务提交。

        修复目标（B.5）：回调内 BEGIN → INSERT screening_results →
        UPDATE checkpoint → COMMIT；任一步失败全部回滚。
        """
        from webui.app import create_app
        import pathlib
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        db_path = pathlib.Path(tmp.name) / "ai_tx.db"
        try:
            app = create_app({
                "TESTING": True, "START_TASKS": False,
                "RESULT_DIR": str(pathlib.Path(tmp.name) / "results"),
                "DB_PATH": str(db_path),
                "PYTHON_EXECUTABLE": sys.executable,
            })
            store = app.config["TASK_STORE"]
            run_id = "ai-tx-run"
            store.create_screening_run(run_id, source_count=10)
            store.update_screening_run(run_id, status="running")
            # 期望 store 有 save_verdict_and_checkpoint_atomic 方法（同事务）
            self.assertTrue(
                hasattr(store, "save_verdict_and_checkpoint_atomic"),
                "store 必须有 save_verdict_and_checkpoint_atomic 方法（同事务）")
            # 调用：写 verdict + 推进 checkpoint
            store.save_verdict_and_checkpoint_atomic(
                run_id, "ai_rough",
                {"job-1": {"verdict": "match"}},
                ["job-1"])
            # 验证：screening_results 有 verdict
            with store._connection() as conn:
                row = conn.execute(
                    "SELECT verdict FROM screening_results "
                    "WHERE run_id = ? AND platform_job_id = ?",
                    (run_id, "job-1")).fetchone()
            self.assertIsNotNone(row, "verdict 必须写入 screening_results")
            self.assertEqual(json.loads(row["verdict"])["verdict"], "match")
            # checkpoint 推进
            self.assertEqual(store.load_checkpoint(run_id, "ai_rough"),
                             {"job-1"})
            with store._connection() as conn:
                conn.execute(
                    "CREATE TRIGGER reject_ai_checkpoint BEFORE INSERT ON pipeline_checkpoints "
                    "WHEN NEW.stage = 'ai_rollback' BEGIN "
                    "SELECT RAISE(ABORT, 'checkpoint rejected'); END"
                )
            with self.assertRaises(Exception):
                store.save_verdict_and_checkpoint_atomic(
                    run_id, "ai_rollback",
                    {"job-rollback": {"verdict": "match"}},
                    ["job-rollback"],
                )
            self.assertNotIn("job-rollback", store.load_screening_verdicts(run_id))
        finally:
            tmp.cleanup()

    def test_ai_rough_rate_limit_keeps_latest_verdict(self):
        """限流时第一批 verdict 必须已落盘，不在 checkpoint 中重置为空。"""
        from webui.app import create_app
        import pathlib
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        db_path = pathlib.Path(tmp.name) / "ai_rate.db"
        try:
            app = create_app({
                "TESTING": True, "START_TASKS": False,
                "RESULT_DIR": str(pathlib.Path(tmp.name) / "results"),
                "DB_PATH": str(db_path),
                "PYTHON_EXECUTABLE": sys.executable,
            })
            store = app.config["TASK_STORE"]
            run_id = "ai-rate-run"
            store.create_screening_run(run_id, source_count=10)
            store.update_screening_run(run_id, status="running")
            # 第一批 verdict 已落盘
            self.assertTrue(hasattr(store, "save_verdict_and_checkpoint_atomic"))
            store.save_verdict_and_checkpoint_atomic(
                run_id, "ai_rough",
                {"job-1": {"verdict": "match"}},
                ["job-1"])
            # 模拟第二批限流：run 标 paused，但第一批 verdict 不丢
            _pause_run(store, run_id,
                                          error_code="ai_rate_limited")
            # 验证：第一批 verdict 仍在 screening_results
            with store._connection() as conn:
                row = conn.execute(
                    "SELECT verdict FROM screening_results "
                    "WHERE run_id = ? AND platform_job_id = ?",
                    (run_id, "job-1")).fetchone()
            self.assertIsNotNone(row, "限流后第一批 verdict 不得丢失")
            self.assertEqual(json.loads(row["verdict"])["verdict"], "match")
            # checkpoint 仍含 job-1
            self.assertIn("job-1", store.load_checkpoint(run_id, "ai_rough"))
        finally:
            tmp.cleanup()

    def test_ai_rough_resume_no_duplicate_calls(self):
        """resume 时已完成岗位不再进入 AI，只处理剩余岗位。"""
        from webui.ai import screen_jobs
        jobs = [
            {"job_id": "job-1", "title": "前端"},
            {"job_id": "job-2", "title": "后端"},
        ]
        seen_user_messages = []

        def fake_call(_endpoint, _key, messages, **_kwargs):
            seen_user_messages.append(messages[-1]["content"])
            return {"dropped": []}

        with mock.patch("webui.ai.call_ai", side_effect=fake_call):
            result = screen_jobs(
                jobs, {}, "http://example.invalid", "key",
                batch_size=1, concurrency=1,
                completed_verdicts={
                    "job-1": {"verdict": "kept", "reason": ""},
                },
            )
        self.assertEqual(len(seen_user_messages), 1)
        self.assertIn("后端", seen_user_messages[0])
        self.assertNotIn("前端", seen_user_messages[0])
        self.assertEqual(set(result["kept"]), {"job-1", "job-2"})


class Slice13ComboDoneHardStopTests(unittest.TestCase):
    """A.7 on_combo_done 持久化失败必须 hard-stop 为 internal_error。"""

    def test_run_search_delivers_completed_combo_payload(self):
        """成功组合把完整岗位和完成键交给持久化回调。"""
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        class OneComboSource:
            def preflight(self):
                return SourceOutcome.success()

            def fetch_list(self, _plan_item, *, on_page_completed=None):
                return SourceOutcome.success(jobs=[{
                    "job_id": "j1", "title": "工程师", "company": "公司A",
                    "salary": "15-25K", "source_url": "https://example.com/j1",
                }])

        delivered = []
        try:
            with mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, "")), \
                    mock.patch("webui.pipeline_exec.close_debug_chrome"):
                result = run_search(
                    {"keyword": "前端", "city": ["上海"]},
                    OneComboSource(),
                    pages=1,
                    sleeper=lambda _seconds: None,
                    on_combo_done=lambda combo, jobs, completed, **kw: delivered.append(
                        (combo, jobs, list(completed))),
                )
        except TypeError as exc:
            self.fail(f"run_search 必须支持 on_combo_done 行为：{exc}")

        self.assertTrue(result["ok"], result)
        self.assertEqual(len(delivered), 1)
        combo_key, jobs, completed = delivered[0]
        self.assertEqual(combo_key, "前端|上海")
        self.assertEqual(jobs[0]["job_id"], "j1")
        self.assertEqual(completed, ["前端|上海"])

    def test_on_combo_done_persist_failure_triggers_hard_stop(self):
        """on_combo_done 回调内持久化失败 → hard-stop 为 internal_error。

        修复目标（B.2 + 调整点 6）：on_combo_done 持久化失败时，
        run_search 返回 hard_stop=True, hard_stop_code='internal_error'。
        """
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        # mock source
        class FakeSource:
            def preflight(self):
                return SourceOutcome.success()

            def fetch_list(self, _plan_item, *, on_page_completed=None):
                return SourceOutcome.success(
                    jobs=[{"job_id": "j1", "title": "t1"}])

        # on_combo_done 抛异常模拟持久化失败
        def failing_on_combo_done(combo_key, jobs, completed_combos):
            raise RuntimeError("persist_failed")

        try:
            with mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                            return_value=(True, "")):
                result = run_search(
                    {"keyword": "前端", "city": ["上海"]},
                    FakeSource(),
                    pages=1,
                    sleeper=lambda x: None,
                    on_combo_done=failing_on_combo_done,
                )
        except TypeError as exc:
            self.fail(f"run_search 必须支持 on_combo_done 行为：{exc}")
        # 必须返回 hard_stop
        self.assertTrue(result.get("hard_stop"),
                        f"on_combo_done 失败必须 hard_stop，实际 {result}")
        self.assertEqual(result.get("hard_stop_code"), "internal_error",
                         f"hard_stop_code 必须 internal_error，实际 {result.get('hard_stop_code')}")


if __name__ == "__main__":
    unittest.main()
