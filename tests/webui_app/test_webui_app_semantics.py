"""webui.app 语义守恒合同测试（027 自 tests/test_webui_app.py 拆出）。"""
import json
import pathlib
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from webui.app import create_app

from tests.test_cross_platform_dedupe import (  # noqa: E402
    CrossPlatformDedupeIntegrationTests,
    _boss_kept_job,
    _wait_for_pipeline_task,
    _zl_job,
)
from tests.test_cross_platform_dedupe import EXTRA_KEY  # noqa: E402


# ======================================================================
# T409: Latest result 三种查询模式
# ======================================================================


class LatestPipelineResultQueryTests(unittest.TestCase):
    """T409: latest_pipeline_result 的三种查询模式。"""

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

    def _save_result_snapshot(self, run_id, platform="boss",
                               status="done"):
        """保存一个 result_snapshot 记录。"""
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "total_scraped, total_kept, total_dropped, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at, finished_at) "
                "VALUES (?, ?, ?, 'result_snapshot', '{}', "
                "0, 0, 0, 0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL, NULL)",
                (str(run_id), str(platform), str(status)),
            )

    def test_global_latest_returns_most_recent(self):
        """T409: 无参数时返回全局最近成功结果。"""
        self._save_result_snapshot("run_001", "boss")
        import time
        time.sleep(0.01)
        self._save_result_snapshot("run_002", "zhilian")
        resp = self.client.get("/api/latest-pipeline-result")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["has_result"])
        # 应返回最近的 run_002
        self.assertEqual(data.get("source_run_id"), "run_002")

    def test_query_by_platform_returns_filtered(self):
        """T409: platform=boss 时只返回 boss 的最近结果。"""
        self._save_result_snapshot("run_001", "boss")
        import time
        time.sleep(0.01)
        self._save_result_snapshot("run_002", "zhilian")
        resp = self.client.get("/api/latest-pipeline-result?platform=boss")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["has_result"])
        self.assertEqual(data.get("source_run_id"), "run_001")
        self.assertEqual(data.get("platform"), "boss")

    def test_query_by_run_id_returns_exact(self):
        """T409: run_id 查询返回精确结果。"""
        # app.py 对 run_id 查询检查 status in ('succeeded', 'partial')
        self._save_result_snapshot("run_001", "boss", status="succeeded")
        resp = self.client.get(
            "/api/latest-pipeline-result?run_id=run_001")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["has_result"])
        self.assertEqual(data.get("source_run_id"), "run_001")

    def test_query_run_id_platform_mismatch_returns_409(self):
        """T409: run_id + platform 不一致 → 409 run_platform_conflict。"""
        # app.py 对 run_id 查询检查 status in ('succeeded', 'partial')
        self._save_result_snapshot("run_001", "boss", status="succeeded")
        resp = self.client.get(
            "/api/latest-pipeline-result?run_id=run_001&platform=zhilian")
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertEqual(data.get("error"), "run_platform_conflict")

    def test_unknown_run_id_returns_no_result(self):
        """T409: 不存在的 run_id 返回 has_result=False。"""
        resp = self.client.get(
            "/api/latest-pipeline-result?run_id=nonexistent")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["has_result"])

    def test_result_contains_source_outcomes(self):
        """T409: 结果包含 source_summary 和 source_outcomes。"""
        self._save_result_snapshot("run_001", "boss")
        resp = self.client.get("/api/latest-pipeline-result")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("source_summary", data)
        self.assertIn("source_outcomes", data)
        self.assertIn("source_evidence_available", data)


# ======================================================================
# 门禁C: T410-T413 — 状态映射 + 恢复 + 原子 claim
# ======================================================================


class StatusMappingTests(unittest.TestCase):
    """T410: 唯一公共状态映射和四类非终态恢复测试。"""

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

    def _create_run(self, run_id, status="queued"):
        """创建指定状态的 screening_run。"""
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, ?, 'process_log', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id), str(status)),
            )

    def test_public_status_vocabulary_has_no_waiting(self):
        """017-US5: 一套话术一个口径——公共词汇唯一且不含 waiting（queued 统一）。"""
        from webui.app import _public_task_status
        cases = {
            "queued": "queued",
            "waiting": "queued",  # 旧词并入 queued（前端无人消费）
            "running": "running",
            "paused": "paused",
            "succeeded": "completed",
            "done": "completed",
            "partial": "completed_with_pending",
            "failed": "failed",
            "interrupted": "cancelled",  # 无 interruption_kind 时按终态取消
        }
        for db_status, expected in cases.items():
            self.assertEqual(
                _public_task_status(db_status), expected,
                f"DB 状态 {db_status} 应映射到 {expected}",
            )

    def test_same_task_status_across_detail_poll_and_resume(self):
        """017-US5: 同一任务在详情/轮询/接回三接口状态词一致（无 waiting 分叉）。"""
        cases = {
            "paused": "paused",
            "partial": "completed_with_pending",
            "failed": "failed",
        }
        for db_status, expected in cases.items():
            run_id = f"vocab-{db_status}"
            self._create_run(run_id, db_status)
            self.app.config["PIPELINE_TASKS"][run_id] = {
                "kind": "scrape", "status": db_status, "progress": {}, "logs": [],
                "result": None, "error": "", "started_at": None,
                "finished_at": None, "stop_event": threading.Event(),
                "platform": "boss",
            }
            detail = self.client.get(f"/api/task-state/{run_id}").get_json()
            self.assertEqual(detail.get("status"), expected, f"{db_status} 详情")
            poll = self.client.get(f"/api/search-progress/{run_id}").get_json()
            self.assertEqual(poll.get("status"), expected, f"{db_status} 轮询")
            self.assertNotEqual(poll.get("status"), "waiting")
        # 接回接口（列表顶部）对 paused 任务与详情/轮询一致
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["status"], "paused")

    def test_public_task_status_mapping_unique(self):
        """T410: 内存/DB canonical 状态统一映射到公共 API 状态。"""
        from webui.app import _public_task_status
        cases = {
            ("queued", None): "queued",
            ("waiting", None): "queued",
            ("running", None): "running",
            ("paused", None): "paused",
            ("succeeded", None): "completed",
            ("done", None): "completed",
            ("partial", None): "completed_with_pending",
            ("failed", None): "failed",
            ("interrupted", "user_cancelled"): "cancelled",
            ("interrupted", "process_restart"): "interrupted",
            ("interrupted", "operator_stop"): "interrupted",
        }
        for (status, kind), expected in cases.items():
            self.assertEqual(
                _public_task_status(status, kind), expected,
                f"状态 {status}/{kind} 应映射到 {expected}",
            )
    def test_task_state_returns_mapped_status(self):
        """T410: api_task_state 返回映射后的任务状态。"""
        run_id = "test_status_mapping"
        self._create_run(run_id, "paused")
        # 注册内存 task
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "paused", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
        }
        resp = self.client.get(f"/api/task-state/{run_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "paused")
        self.assertEqual(data.get("db_status"), "paused")

    def test_task_state_success_count_tracks_live_progress_current(self):
        """内存任务 progress.current 实时推进时，success_count 必须跟随。

        回归：智联详情批内条级进度（on_item_done → emit current）必须实时
        反映到 task-state 计数画面；此前只读 DB processed_count（批次粒度），
        用户看到「已完成」长时间卡 0。
        """
        run_id = "test_live_current"
        self._create_run(run_id, "running")
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "ai_screen", "status": "running",
            "progress": {"stage": "fetch_jd", "current": 7, "total": 28,
                         "message": "抓取 JD 7/28", "overall_percent": 37},
            "logs": [], "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
        }
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        # DB processed_count 为 0，但内存进度已推进到 7：取两者最大值。
        self.assertEqual(data["success_count"], 7)

    def test_task_state_scrape_does_not_use_combo_index_as_success_count(self):
        """scrape 任务的 searching 阶段 current 是组合序号，不得当成功数显示。

        回归：live_current 只对条数语义的任务（ai_screen/recrawl）启用；
        scrape 列表抓取把组合序号混进成功数会显示「已完成 3 / 127 岗位」。
        """
        run_id = "test_scrape_combo_current"
        self._create_run(run_id, "running")
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running",
            "progress": {"stage": "searching", "current": 3, "total": 5,
                         "message": "正在抓第 3 个关键词组合", "overall_percent": 30},
            "logs": [], "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
        }
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        # DB processed_count=0、match/mismatch=0：组合序号 3 不得透出。
        self.assertEqual(data["success_count"], 0)

    def test_task_state_interrupted_maps_to_cancelled(self):
        """T410: interrupted DB 状态 → cancelled 任务状态。"""
        run_id = "test_interrupted_mapping"
        self._create_run(run_id, "interrupted")
        resp = self.client.get(f"/api/task-state/{run_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "cancelled")
        self.assertEqual(data.get("db_status"), "interrupted")

    def test_task_state_restart_interrupted_carries_message(self):
        """服务重启中断的任务不应显示默认“正在准备任务”。"""
        run_id = "interrupt-msg"
        self._create_run(run_id, "interrupted")
        self.store.save_interruption_kind(run_id, "process_restart")
        data = self.client.get("/api/task-state/interrupt-msg").get_json()
        self.assertEqual(data.get("status"), "interrupted")
        self.assertIn("message", data.get("progress") or {})
        self.assertNotEqual(data["progress"]["message"], "正在准备任务")

    # -- T412: continue 一致性校验 + 原子 claim --------------------------

    def test_continue_checks_platform_consistency(self):
        """T412: continue 验证平台一致性。"""
        run_id = "test_continue_platform"
        self._create_run(run_id, "paused")
        # 设置 platform
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET platform='boss' WHERE id=?",
                (run_id,))

        # 尝试继续但不匹配平台（无平台校验时也会因无 execution_params 失败）
        resp = self.client.post(f"/api/task/continue/{run_id}")
        # 可能因缺少 scrape_task_id 而失败，但不应该报 404
        self.assertNotEqual(resp.status_code, 404)

    def test_claim_paused_run_atomic(self):
        """T412: claim_paused_screening_run 原子性——两次调用只成功一次。"""
        run_id = "test_atomic_claim"
        self._create_run(run_id, "paused")
        self.assertTrue(
            self.store.claim_paused_screening_run(run_id),
            "第一次 claim 应成功",
        )
        # 第二次 claim 应失败（已被标记为 running）
        self.assertFalse(
            self.store.claim_paused_screening_run(run_id),
            "第二次 claim 应失败——paused→running 只允许一次",
        )

    def test_claim_non_paused_run_fails(self):
        """T412: 非 paused 状态的 run 不能被 claim。"""
        run_id = "test_claim_non_paused"
        self._create_run(run_id, "running")
        self.assertFalse(
            self.store.claim_paused_screening_run(run_id),
            "running 状态的 run 不能被 claim",
        )

    # -- T413: 重启打断标记 ---------------------------------------------

    def test_stale_runs_marked_interrupted_on_startup(self):
        """T413: 服务重启时 running/queued 的 run 被标记为 interrupted。"""
        from webui.store import TaskStore
        run_id = "test_stale_interrupted"
        self._create_run(run_id, "running")

        # 模拟重启：创建新 store 实例
        new_store = TaskStore(self.store.db_path)
        run = new_store.get_screening_run(run_id)
        self.assertIsNotNone(run)
        self.assertEqual(
            run["status"], "interrupted",
            "重启后 running 的 run 应被标记为 interrupted",
        )
        self.assertEqual(
            run.get("error_code"), "restart",
            "interrupted 的 error_code 应为 restart",
        )


class PauseElapsedAndResumeConfigTests(unittest.TestCase):
    """暂停不计时（active_elapsed_ms）+ 高级设置续跑生效（三路径刷新配置）。"""

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
        self.tz = timezone(timedelta(hours=8))

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _create_run(self, run_id, status="queued"):
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, ?, 'process_log', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id), str(status)),
            )

    def _iso(self, sec):
        return (datetime(2026, 8, 1, 10, 0, 0, tzinfo=self.tz)
                + timedelta(seconds=sec)).isoformat()

    def _insert_event(self, run_id, event_type, at_iso):
        with self.store._connection() as conn:
            # task_logs 外键指向 tasks 表：先插入占位行（同 append_task_events）
            conn.execute(
                "INSERT OR IGNORE INTO tasks (id, kind, status, params_json, created_at, updated_at) "
                "VALUES (?, 'screening_event_log', 'logging', '{}', ?, ?)",
                (str(run_id), at_iso, at_iso),
            )
            seq = int(conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq "
                "FROM task_logs WHERE task_id = ?",
                (str(run_id),),
            ).fetchone()["next_seq"])
            line = json.dumps(
                {"type": event_type, "payload": {}, "at": at_iso},
                ensure_ascii=False,
            )
            conn.execute(
                "INSERT INTO task_logs (task_id, seq, created_at, line) "
                "VALUES (?, ?, ?, ?)",
                (str(run_id), seq, at_iso, line),
            )

    def _scope(self, **overrides):
        raw = {
            "schema_version": 1, "platform": "boss",
            "keywords": ["Python"], "scope_kind": "cities",
            "cities": ["上海"], "pages_per_combination": 3,
            "combination_count": 1, "planned_pages": 3,
            "task_size": "small", "scope_digest": None,
        }
        raw.update(overrides)
        return raw

    def _config(self, **overrides):
        base = {
            "inter_combo_delay": 30.0,
            "detail_batch_size": 10,
            "detail_interval": 2.0,
            "detail_reset_every": 3,
            "detail_batch_cooldown": 4.0,
            "detail_tab_pool_size": 10,
            "screen_batch_size": 30,
            "screen_concurrency": 3,
            "match_batch_size": 4,
            "match_concurrency": 8,
        }
        base.update(overrides)
        return base

    def _resume_mocks(self):
        """POST /api/task/continue 期间的浏览器隔离 mock。"""
        return (
            mock.patch("webui.pipeline_exec.resolve_browser_account", return_value=""),
            mock.patch("webui.pipeline_exec.set_active_cdp_data_dir"),
            mock.patch("webui.platforms.resolve_login_space"),
        )

    # ---- 暂停不计时：/api/task-state 返回 active_elapsed_ms ----

    def test_task_state_active_elapsed_excludes_pause(self):
        run_id = "elapsed-exclude"
        self._create_run(run_id, "succeeded")
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET started_at = ?, finished_at = ? WHERE id = ?",
                (self._iso(0), self._iso(240), run_id),
            )
        # 60s 暂停 → 180s 恢复：暂停 120s；总跨度 240s，实际运行 120s
        self._insert_event(run_id, "pause", self._iso(60))
        self._insert_event(run_id, "resume", self._iso(180))
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        # 只有 screening_runs.status 没有白箱完成证据，不能显示完整成功。
        self.assertEqual(data["status"], "completed_with_pending")
        self.assertEqual(data["integrity"]["conclusion"], "unverifiable")
        self.assertEqual(data["active_elapsed_ms"], 120_000)

    def test_task_state_active_elapsed_frozen_while_paused(self):
        """暂停中无 finished_at：未闭合的 pause 截止到当前，累计仍定格在暂停前。"""
        run_id = "elapsed-paused"
        self._create_run(run_id, "paused")
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET started_at = ? WHERE id = ?",
                (self._iso(0), run_id),
            )
        self._insert_event(run_id, "pause", self._iso(120))
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertEqual(data["status"], "paused")
        # active = (now - 0) - (now - 120) = 120s，不受 now 影响
        self.assertEqual(data["active_elapsed_ms"], 120_000)

    def test_task_state_active_elapsed_none_without_events(self):
        """无 pause/resume 事件（老 run / 无暂停）时回退 None，前端沿用 started_at 差值。"""
        run_id = "elapsed-none"
        self._create_run(run_id, "succeeded")
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertIsNone(data.get("active_elapsed_ms"))

    # ---- 高级设置续跑生效 ----

    def test_continue_ai_screen_refreshes_execution_config(self):
        """暂停 AI 续跑：用新 Tab 数/间隔刷新 run 配置，DB 与 worker 都拿到新值。"""
        run_id = "resume-ai-config"
        old = self._config(detail_tab_pool_size=10, detail_interval=2.0)
        self.store.create_screening_run(
            run_id,
            frozen_filters={"salary": ["20-30K"]},
            source_count=2,
            execution_params={
                "platform": "boss",
                "scrape_task_id": "scrape-src-config",
                "browser_account": "a", "cdp_port": 9222, "profile_key": "boss:a",
                "profile_summary": "测试画像", "profile_facts": {"years": 3},
                "execution_config": old,
                "frozen_scope": self._scope(),
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="screen_b",
            error_code="ai_rate_limited", error_reason="AI 限流",
        )
        new = self._config(detail_tab_pool_size=6, detail_interval=5.0)
        self.store.save_custom_config(new)
        # 续 AI 需要父抓取 run 的岗位快照
        self.store.create_screening_run(
            "scrape-src-config", source_count=1,
            execution_params={"platform": "boss"},
        )
        self.store.update_screening_run("scrape-src-config", status="succeeded")
        jobs = [{"job_id": "j1", "platform_job_id": "j1",
                 "source_url": "https://zhipin.example/j1.html"}]
        self.store.save_scrape_combo_result(
            "scrape-src-config", "kw|city", jobs, ["kw|city"])

        captured = []
        r1, r2, r3 = self._resume_mocks()
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *a, **kw: captured.append((fn, a, kw)) or None,
        ), r1, r2, r3:
            self.app.config["RESUME_BLOCK_CHECKER"] = lambda run: (True, "", "")
            resp = self.client.post(f"/api/task/continue/{run_id}", json={})
        self.assertEqual(resp.status_code, 200, resp.get_json())

        from webui.execution_config import ExecutionConfigSnapshot
        fn, args, kwargs = captured[0]
        submitted = args[-1]
        self.assertIsInstance(submitted, ExecutionConfigSnapshot)
        self.assertEqual(submitted.detail_tab_pool_size, 6)
        self.assertEqual(submitted.detail_interval, 5.0)
        run = self.store.get_screening_run(run_id)
        db_config = (run.get("execution_params") or {}).get("execution_config") or {}
        self.assertEqual(db_config.get("detail_tab_pool_size"), 6)
        self.assertEqual(db_config.get("detail_interval"), 5.0)
        # pages/frozen_scope 保持冻结
        frozen = (run.get("execution_params") or {}).get("frozen_scope") or {}
        self.assertEqual(frozen.get("pages_per_combination"), 3)

    def test_continue_scrape_refreshes_inter_combo_delay(self):
        """暂停续抓：run_search 收到刷新后的间隔，pages 仍用冻结的 frozen_scope。"""
        run_id = "resume-scrape-config"
        old = self._config(inter_combo_delay=30.0, detail_tab_pool_size=10)
        self.store.create_screening_run(
            run_id,
            source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "Python", "city": ["上海"], "pages": 3},
                "browser_account": "a", "cdp_port": 9222, "profile_key": "boss:a",
                "execution_config": old,
                "frozen_scope": self._scope(),
            },
        )
        if self.store.get_screening_run(run_id)["status"] == "queued":
            self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="源账号限流",
        )
        new = self._config(inter_combo_delay=10.0)
        self.store.save_custom_config(new)

        captured = []
        r1, r2, r3 = self._resume_mocks()
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *a, **kw: captured.append((fn, a, kw)) or None,
        ), r1, r2, r3:
            self.app.config["RESUME_BLOCK_CHECKER"] = lambda run: (True, "", "")
            resp = self.client.post(f"/api/task/continue/{run_id}", json={})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        run = self.store.get_screening_run(run_id)
        db_config = (run.get("execution_params") or {}).get("execution_config") or {}
        self.assertEqual(db_config.get("inter_combo_delay"), 10.0)

        # submit 被拦截后 start_gate 已放行；手动跑续抓 worker 并拦截 run_search
        fn, args, kwargs = captured[0]
        run_search_calls = []
        with mock.patch(
            "webui.pipeline_exec.run_search",
            side_effect=lambda *a, **kw: run_search_calls.append(kw) or {
                "ok": True, "jobs": [], "total_scraped": 0, "total_matched": 0,
                "combinations": 0, "error": "", "completed_combos": [],
            },
        ), mock.patch("webui.pipeline_exec.resolve_browser_account", return_value=""), \
           mock.patch("webui.pipeline_exec.set_active_cdp_data_dir"), \
           mock.patch.object(self.app.config["PIPELINE_CONTEXT"], "source_class"):
            fn()
        self.assertEqual(len(run_search_calls), 1)
        self.assertEqual(run_search_calls[0]["execution_config"].inter_combo_delay, 10.0)
        self.assertEqual(run_search_calls[0]["pages"], 3)

    def test_continue_recrawl_refreshes_match_concurrency(self):
        """暂停续补抓：用刷新后的并发配置，scope 从父抓取 run 继承且 pages 不变。"""
        run_id = "resume-recrawl-config"
        parent_id = "parent-scrape-recrawl"
        self.store.create_screening_run(
            parent_id,
            source_count=2,
            execution_params={
                "platform": "boss",
                "execution_config": self._config(match_concurrency=8),
                "frozen_scope": self._scope(),
            },
        )
        self.store.update_screening_run(parent_id, status="succeeded")
        self.store.create_screening_run(
            run_id,
            source_count=1,
            execution_params={
                "platform": "boss",
                "source_run_id": parent_id,
                "job_ids": ["j1"],
                "profile_summary": "测试画像",
                "browser_account": "a", "cdp_port": 9222, "profile_key": "boss:a",
            },
        )
        if self.store.get_screening_run(run_id)["status"] == "queued":
            self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="recrawl_jd",
        )
        new = self._config(match_concurrency=15)
        self.store.save_custom_config(new)

        captured = []
        r1, r2, r3 = self._resume_mocks()
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *a, **kw: captured.append((fn, a, kw)) or None,
        ), r1, r2, r3:
            self.app.config["RESUME_BLOCK_CHECKER"] = lambda run: (True, "", "")
            resp = self.client.post(f"/api/task/continue/{run_id}", json={})
        self.assertEqual(resp.status_code, 200, resp.get_json())

        from webui.execution_config import ExecutionConfigSnapshot
        fn, args, kwargs = captured[0]
        submitted = args[-1]
        self.assertIsInstance(submitted, ExecutionConfigSnapshot)
        self.assertEqual(submitted.match_concurrency, 15)
        run = self.store.get_screening_run(run_id)
        db_config = (run.get("execution_params") or {}).get("execution_config") or {}
        self.assertEqual(db_config.get("match_concurrency"), 15)
        # 父抓取 run 的 pages/scope 不被刷新改动
        parent = self.store.get_screening_run(parent_id)
        self.assertEqual(
            (parent.get("execution_params") or {}).get("frozen_scope", {}).get("pages_per_combination"),
            3,
        )

    def test_continue_blocked_does_not_refresh_db_config(self):
        """阻断未解除时继续被拒，且不提前改写 paused run 的配置快照。"""
        run_id = "resume-blocked-config"
        old = self._config(detail_tab_pool_size=10)
        self.store.create_screening_run(
            run_id,
            source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "Python", "city": ["上海"], "pages": 3},
                "browser_account": "a", "cdp_port": 9222, "profile_key": "boss:a",
                "execution_config": old,
                "frozen_scope": self._scope(),
            },
        )
        if self.store.get_screening_run(run_id)["status"] == "queued":
            self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="源账号限流",
        )
        new = self._config(detail_tab_pool_size=6)
        self.store.save_custom_config(new)

        r1, r2, r3 = self._resume_mocks()
        with r1, r2, r3:
            self.app.config["RESUME_BLOCK_CHECKER"] = (
                lambda run: (False, "captcha_required", "验证码未处理")
            )
            resp = self.client.post(f"/api/task/continue/{run_id}", json={})
        self.assertEqual(resp.status_code, 409)
        run = self.store.get_screening_run(run_id)
        db_config = (run.get("execution_params") or {}).get("execution_config") or {}
        # block 检查通过前不得刷新：DB 仍保留旧配置
        self.assertEqual(db_config.get("detail_tab_pool_size"), 10)


class AutoScreenChainTests(unittest.TestCase):
    """B031 一键链路 auto_screen 标记：创建/返回/消费/清除。"""

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

    def _preview(self):
        return self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]

    def _start_auto_search(self, task_id_hint=None):
        preview = self._preview()
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit"):
            resp = self.client.post("/api/execute-search", json={
                "platform": "boss",
                "script_params": {
                    "keyword": "Python",
                    "city": ["上海"],
                    "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
                "auto_screen": True,
                "auto_screen_fields": {"salary": ["406"]},
                "auto_screen_profile": "Python 后端候选人",
                "auto_screen_facts": {"core_skills": ["Python"], "job_type": "全职"},
            })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return resp.get_json()["task_id"]

    def _seed_succeeded_scrape(self, run_id, auto_screen=True):
        jobs = [
            {"job_id": "j1", "platform_job_id": "j1", "title": "岗位1",
             "source_url": "https://zhipin.example/j1.html"},
        ]
        self.store.create_screening_run(
            run_id,
            source_count=1,
            execution_params={
                "platform": "boss",
                "auto_screen": bool(auto_screen),
                "auto_screen_fields": {"salary": ["406"]},
                "auto_screen_profile": "Python 后端候选人",
                "auto_screen_facts": {"core_skills": ["Python"], "job_type": "全职"},
            },
        )
        self.store.save_scrape_combo_result(run_id, "kw|city", jobs, ["kw|city"])
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(run_id, status="succeeded", current_stage="scrape")
        # 033 V2：这个夹具代表真实的已完成抓取，因此补齐页级和范围完成证据。
        from webui.store_helpers import _now
        from webui.whitebox import WhiteboxService
        whitebox = WhiteboxService(self.store)
        ref = whitebox.begin("scrape", run_id, {
            "stages": ["scrape_list"],
            "units": [{
                "unit_key": "kw|city",
                "unit_kind": "keyword_city",
                "stage": "scrape_list",
                "planned_pages": 1,
                "required": True,
            }],
        })
        whitebox.record(ref, {
            "idempotency_key": f"page:{run_id}",
            "event_type": "page_completed",
            "occurred_at": _now(),
            "stage": "scrape_list",
            "unit_kind": "keyword_city",
            "unit_key": "kw|city",
            "attempt_no": 1,
            "required_evidence": True,
            "payload": {
                "page": 1,
                "planned_pages": 1,
                "returned_count": len(jobs),
                "new_unique_count": len(jobs),
                "has_more": False,
                "resume_page": 2,
                "scope_complete": True,
                "source_exhausted": True,
                "stop_reason": "target_reached",
            },
        })
        whitebox.record(ref, {
            "idempotency_key": f"scope-completed:{run_id}",
            "event_type": "scope_completed",
            "occurred_at": _now(),
            "stage": "scrape_list",
            "unit_kind": "keyword_city",
            "unit_key": "kw|city",
            "attempt_no": 1,
            "required_evidence": True,
            "payload": {
                "scope_complete": True,
                "source_exhausted": True,
                "stop_reason": "target_reached",
                "returned_total_count": len(jobs),
                "unit_unique_count": len(jobs),
            },
        })
        whitebox.finalize(ref, lifecycle_end="succeeded")
        return jobs

    def test_execute_search_persists_auto_screen_flag(self):
        task_id = self._start_auto_search()
        run = self.store.get_screening_run(task_id)
        params = run["execution_params"]
        self.assertTrue(params["auto_screen"])
        self.assertEqual(params["auto_screen_fields"], {"salary": ["406"]})
        self.assertEqual(params["auto_screen_profile"], "Python 后端候选人")
        self.assertEqual(
            params["auto_screen_facts"],
            {"core_skills": ["Python"], "job_type": "全职"},
            "B033：一键任务必须冻结画像事实快照，供刷新后自动接续",
        )
        task = self.app.config["PIPELINE_TASKS"][task_id]
        self.assertTrue(task["auto_screen"])
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertTrue(data["auto_screen"])
        self.assertEqual(data["auto_screen_fields"], {"salary": ["406"]})
        self.assertEqual(data["auto_screen_profile"], "Python 后端候选人")

    def test_execute_search_rejects_invalid_auto_screen_fields(self):
        preview = self._preview()
        resp = self.client.post("/api/execute-search", json={
            "platform": "boss",
            "script_params": {"keyword": "Python", "city": ["上海"], "pages": 1},
            "scope_digest": preview["scope_digest"],
            "auto_screen": True,
            "auto_screen_fields": ["salary"],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "auto_screen_fields 必须是对象")

    def test_execute_search_rejects_when_ai_screen_running(self):
        """AI 筛选占用浏览器时，不能再启动新的抓取任务。"""
        preview = self._preview()
        self.app.config["PIPELINE_TASKS"]["running-ai-screen"] = {
            "kind": "ai_screen", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(), "platform": "boss",
        }
        resp = self.client.post("/api/execute-search", json={
            "platform": "boss",
            "script_params": {"keyword": "Python", "city": ["上海"], "pages": 1},
            "scope_digest": preview["scope_digest"],
        })
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "browser_busy")

    def test_ai_screen_rejects_when_scrape_running(self):
        """抓取任务运行时，不能对另一来源启动 AI 筛选。"""
        source_id = "busy-scrape-source"
        self._seed_succeeded_scrape(source_id)
        self.app.config["PIPELINE_TASKS"]["running-scrape"] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(), "platform": "boss",
        }
        resp = self.client.post("/api/ai-screen", json={
            "screening_fields": {"salary": ["406"]},
            "profile_summary": "Python 后端候选人",
            "scrape_task_id": source_id,
        })
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "browser_busy")

    def test_ai_screen_consumes_flag_before_validation(self):
        run_id = "auto-consume-fail"
        self._seed_succeeded_scrape(run_id)
        resp = self.client.post("/api/ai-screen", json={
            "screening_fields": "bad",
            "consume_auto_screen": True,
            "scrape_task_id": run_id,
        })
        self.assertEqual(resp.status_code, 400)
        run = self.store.get_screening_run(run_id)
        self.assertFalse(run["execution_params"]["auto_screen"])
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertFalse(data["has_task"])

    def test_latest_running_task_restores_completed_auto_screen(self):
        run_id = "auto-refresh"
        self._seed_succeeded_scrape(run_id)
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["status"], "completed")
        self.assertTrue(data["auto_screen"])
        self.assertEqual(data["scrape_task_id"], run_id)
        self.assertEqual(data["frozen_filters"], {"salary": ["406"]})
        self.assertEqual(data["profile_summary"], "Python 后端候选人")
        self.assertEqual(
            data["profile_facts"],
            {"core_skills": ["Python"], "job_type": "全职"},
            "B033：auto_screen 恢复分支必须透传画像事实快照",
        )
        self.assertEqual(data["scraped_count"], 1)
        # 消费后刷新不再恢复自动接续。
        self.client.post("/api/ai-screen", json={
            "screening_fields": "bad",
            "consume_auto_screen": True,
            "scrape_task_id": run_id,
        })
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertFalse(data["has_task"])

    def test_latest_running_task_paused_returns_profile_fields(self):
        """B033：paused 分支恢复时必须返回画像文本与画像事实快照。"""
        run_id = "paused-screen"
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={
                "platform": "boss",
                "scrape_task_id": "scrape-parent",
                "profile_summary": "3年Python后端",
                "profile_facts": {"core_skills": ["Python"], "job_type": "全职"},
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="ai_rough",
            error_code="source_blocked", error_reason="验证码",
        )
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["status"], "paused")
        self.assertEqual(data["profile_summary"], "3年Python后端")
        self.assertEqual(
            data["profile_facts"],
            {"core_skills": ["Python"], "job_type": "全职"},
            "B033：paused 恢复分支必须透传画像事实，否则续跑退化为两通道",
        )

    def test_execute_search_cancel_clears_flag(self):
        task_id = self._start_auto_search()
        resp = self.client.post(f"/api/execute-search/{task_id}/cancel")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertFalse(self.store.get_screening_run(task_id)["execution_params"]["auto_screen"])
        self.assertFalse(self.app.config["PIPELINE_TASKS"][task_id]["auto_screen"])

    def test_task_cancel_clears_flag(self):
        run_id = "auto-cancel"
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={"platform": "boss", "auto_screen": True},
        )
        self.store.update_screening_run(run_id, status="running")
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(), "platform": "boss", "auto_screen": True,
        }
        resp = self.client.post(f"/api/task/cancel/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertFalse(self.store.get_screening_run(run_id)["execution_params"]["auto_screen"])

    def test_task_finish_clears_flag(self):
        run_id = "auto-finish"
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={"platform": "boss", "auto_screen": True},
        )
        jobs = [
            {"job_id": "j1", "platform_job_id": "j1", "title": "岗位1",
             "source_url": "https://zhipin.example/j1.html"},
        ]
        self.store.save_scrape_combo_result(run_id, "kw|city", jobs, ["kw|city"])
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="操作频繁",
        )
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertFalse(self.store.get_screening_run(run_id)["execution_params"]["auto_screen"])

    def test_paused_run_preserves_flag(self):
        run_id = "auto-paused"
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={"platform": "boss", "auto_screen": True},
        )
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="captcha_required", error_reason="验证码",
        )
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["status"], "paused")
        self.assertTrue(data["auto_screen"])

    def test_task_state_returns_auto_screen_with_memory_priority(self):
        run_id = "auto-state"
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={"platform": "boss", "auto_screen": True},
        )
        self.store.update_screening_run(run_id, status="running")
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertTrue(data["auto_screen"])
        # 内存任务优先：内存 False 时覆盖 DB True。
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(), "platform": "boss", "auto_screen": False,
        }
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertFalse(data["auto_screen"])

    def test_latest_running_task_skips_zero_job_auto_screen(self):
        run_id = "auto-zero"
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={"platform": "boss", "auto_screen": True},
        )
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(run_id, status="succeeded", current_stage="scrape")
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertFalse(data["has_task"])


class B054LocationApiTests(unittest.TestCase):
    """B054 地点目录、预览、执行与校验接口。"""

    def setUp(self):
        import gc
        gc.collect()
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

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    @staticmethod
    def _locations():
        return [
            {
                "platform": "boss",
                "city_name": "上海",
                "city_code": "101020100",
                "district_name": "浦东新区",
                "district_code": "310115",
            },
            {
                "platform": "boss",
                "city_name": "上海",
                "city_code": "101020100",
                "district_name": "徐汇区",
                "district_code": "310104",
            },
            {
                "platform": "boss",
                "city_name": "上海",
                "city_code": "101020100",
                "district_name": "黄浦区",
                "district_code": "310101",
            },
        ]

    def test_preview_accepts_locations_and_counts_combos(self):
        resp = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "locations": self._locations(),
            "pages_per_combination": 1,
        })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertEqual(data["scope"]["combination_count"], 3)
        self.assertEqual(data["scope"]["planned_pages"], 3)
        self.assertEqual(len(data["scope"]["locations"]), 3)

    def test_old_preview_without_locations_unchanged(self):
        resp = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("locations", resp.get_json()["scope"])

    def test_execute_freezes_locations_into_script_params(self):
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "locations": self._locations(),
            "pages_per_combination": 1,
        }).get_json()["scope"]
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit"):
            resp = self.client.post("/api/execute-search", json={
                "platform": "boss",
                "script_params": {
                    "keyword": "Python",
                    "city": ["上海"],
                    "pages": 1,
                    "locations": self._locations(),
                },
                "scope_digest": preview["scope_digest"],
            })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        task_id = resp.get_json()["task_id"]
        run = self.app.config["TASK_STORE"].get_screening_run(task_id)
        stored = run["execution_params"]["script_params"]
        self.assertEqual(len(stored["locations"]), 3)
        self.assertEqual(stored["locations"][0]["district_name"], "浦东新区")

    def test_location_validate_scope_kind_nationwide_rejects_locations(self):
        resp = self.client.post("/api/location/validate", json={
            "platform": "boss",
            "scope_kind": "nationwide",
            "locations": self._locations(),
        })
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.get_json()["error_code"], "scope_validation_failed")

    def test_location_catalog_empty_districts_returns_200(self):
        with mock.patch("webui.location_api.get_districts", return_value=[]):
            resp = self.client.get("/api/location-catalog?platform=boss&city=上海")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["districts"], [])

    def test_location_catalog_unavailable_returns_503(self):
        from webui.location_catalog import LocationCatalogUnavailable
        with mock.patch(
            "webui.location_api.get_districts",
            side_effect=LocationCatalogUnavailable("down"),
        ):
            resp = self.client.get("/api/location-catalog?platform=boss&city=上海")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.get_json()["error_code"], "location_catalog_unavailable")


# ===========================================================================
# 020 US3：续跑时跨平台重复岗位只进剔除列表（复用 019 集成测试基建）
# ===========================================================================


class ResumeDedupSingleSideTests(CrossPlatformDedupeIntegrationTests):
    """断点内已保留岗位 + 本轮新命中跨平台重复 → 岗位只在剔除侧。"""

    def _run_first_round_paused_at_fine(self, scrape_task_id, rough_seen):
        """第一轮：无 BOSS 轮；粗筛全过（判定入断点），精筛限流暂停。"""
        from webui.ai import AISecurityError, ERROR_RATE_LIMIT

        def rough_ok(jobs, *args, **kwargs):
            rough_seen.extend(str(j.get("job_id")) for j in jobs)
            return {"kept": [str(j.get("job_id")) for j in jobs],
                    "dropped": [],
                    "verdicts": {str(j.get("job_id")): {
                        "verdict": "kept", "reason": "符合", "caveats": []}
                        for j in jobs}}

        def fine_blocked(chunk, *args, **kwargs):
            raise AISecurityError(ERROR_RATE_LIMIT)

        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", side_effect=rough_ok), \
                mock.patch("webui.ai.match_jds", side_effect=fine_blocked), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")), \
                mock.patch("webui.source.ZhilianCdpSource",
                           return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details",
                           side_effect=lambda chunk, *a, **k: {
                               "jobs": [{**job, "jd": "职责描述"} for job in chunk],
                               "hard_stop": False, "hard_stop_code": None,
                               "stopped": False, "fetched": len(chunk),
                           }), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"):
            response = self.client.post("/api/ai-screen", json={
                "screening_fields": {"keyword": "后端"},
                "profile_summary": "后端工程师",
                "scrape_task_id": scrape_task_id,
            }, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.get_json())
            task_id = response.get_json()["task_id"]
            finished = _wait_for_pipeline_task(self.client, task_id)
        return task_id, finished

    def _continue_recording_fine(self, run_id, rough_seen, fine_seen):
        """续跑并记录粗筛/精筛实际输入；返回最终快照。"""
        def rough_ok(jobs, *args, **kwargs):
            rough_seen.extend(str(j.get("job_id")) for j in jobs)
            return {"kept": [str(j.get("job_id")) for j in jobs],
                    "dropped": [], "verdicts": {}}

        def fine_ok(chunk, *args, **kwargs):
            fine_seen.extend(str(job["job_id"]) for job in chunk)
            return {"verdicts": {
                str(job["job_id"]): {"verdict": "match", "reason": "匹配",
                                     "caveats": []}
                for job in chunk}}

        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", side_effect=rough_ok), \
                mock.patch("webui.ai.match_jds", side_effect=fine_ok), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")), \
                mock.patch("webui.source.ZhilianCdpSource",
                           return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details",
                           side_effect=lambda chunk, *a, **k: {
                               "jobs": [{**job, "jd": "职责描述"} for job in chunk],
                               "hard_stop": False, "hard_stop_code": None,
                               "stopped": False, "fetched": len(chunk),
                           }), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"):
            response = self.client.post(
                f"/api/task/continue/{run_id}", headers=self.headers)
            self.assertEqual(response.status_code, 200, response.get_json())
            return _wait_for_pipeline_task(self.client, run_id)

    def test_checkpoint_kept_job_hit_by_dedupe_lands_in_dropped_only(self):
        """断点内已保留 + 暂停期间对端出现同指纹岗位 → 续跑只进剔除侧。"""
        self._install_zhilian_source(
            "zl-src", [_zl_job("zl-dup"), _zl_job("zl-keep", company="别的公司")])

        rough_seen: list[str] = []
        task_id, first = self._run_first_round_paused_at_fine("zl-src", rough_seen)
        self.assertEqual(first["status"], "paused", first)
        self.assertEqual(sorted(rough_seen), ["zl-dup", "zl-keep"])
        # 断点内 zl-dup 已有保留判定
        verdicts = self.store.load_screening_verdicts(task_id)
        self.assertIn("zl-dup", verdicts)
        # 暂停后对端平台出现同指纹岗位
        self._save_boss_round([_boss_kept_job("boss-x")], days_ago=1)

        rough2: list[str] = []
        fine2: list[str] = []
        finished = self._continue_recording_fine(task_id, rough2, fine2)

        self.assertEqual(finished["status"], "completed", finished)
        # 粗筛不重筛（断点全覆盖）
        self.assertEqual(rough2, [])
        # 岗位只在剔除侧：不进保留/幸存者、不进精筛输入
        self.assertNotIn("zl-dup", fine2)
        self.assertEqual(fine2, ["zl-keep"])
        payload = self.store.load_latest_pipeline_result_for_platform("zhilian")
        kept_ids = [str(j.get("platform_job_id") or j.get("job_id"))
                    for j in payload["result"]["jobs"]]
        dropped_ids = [str(e.get("platform_job_id"))
                       for e in payload["result"]["dropped"]]
        self.assertNotIn("zl-dup", kept_ids)
        self.assertEqual(dropped_ids.count("zl-dup"), 1)
        self.assertIn("跨平台重复",
                      payload["result"]["dropped"][0]["reason"])
        # 计数不翻倍
        self.assertEqual(payload["result"]["total_scraped"], 2)
        self.assertEqual(payload["result"]["total_dropped"], 1)
        # 018 收尾契约：一条流程一条轮
        zhilian_rounds = [
            r for r in self.store.list_history_rounds("zhilian")
            if r["status"] in ("done", "partial", "scraped_only")
        ]
        self.assertEqual(len(zhilian_rounds), 1)


# ===========================================================================
# 020 US6：判定合并覆盖口径（多 run 链）
# ===========================================================================


class ResumeVerdictCoverageChainTests(CrossPlatformDedupeIntegrationTests):
    """多 run 链：run1 粗筛 dropped → run2 接管只写精筛判定（数量够但
    键集不覆盖断点）→ run3 续跑。覆盖口径下必须合并，dropped 不复活。"""

    _EXEC_PARAMS = {
        "platform": "zhilian",
        "scrape_task_id": "zl-src",
        "profile_summary": "后端工程师",
        "profile_facts": None,
    }

    def _seed_chain_run(self, run_id, *, status, verdicts, checkpoint,
                        source_count=3):
        self.store.create_screening_run(
            run_id,
            frozen_filters={"keyword": "后端"},
            source_count=source_count,
            execution_params=dict(self._EXEC_PARAMS),
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(run_id, status=status)
        self.store.save_checkpoint(run_id, "ai_rough", checkpoint)
        if verdicts:
            self.store.save_screening_verdicts(run_id, verdicts)

    def _continue_chain(self, run_id, rough_seen, fine_seen):
        def rough_ok(jobs, *args, **kwargs):
            rough_seen.extend(str(j.get("job_id")) for j in jobs)
            return {"kept": [str(j.get("job_id")) for j in jobs],
                    "dropped": [], "verdicts": {}}

        def fine_ok(chunk, *args, **kwargs):
            fine_seen.extend(str(job["job_id"]) for job in chunk)
            return {"verdicts": {
                str(job["job_id"]): {"verdict": "match", "reason": "匹配",
                                     "caveats": []}
                for job in chunk}}

        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
        with mock.patch("webui.ai.retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.screen_jobs", side_effect=rough_ok), \
                mock.patch("webui.ai.match_jds", side_effect=fine_ok), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")), \
                mock.patch("webui.source.ZhilianCdpSource",
                           return_value=object()), \
                mock.patch("webui.pipeline_exec.fetch_job_details",
                           side_effect=lambda chunk, *a, **k: {
                               "jobs": [{**job, "jd": "职责描述"} for job in chunk],
                               "hard_stop": False, "hard_stop_code": None,
                               "stopped": False, "fetched": len(chunk),
                           }), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"):
            response = self.client.post(
                f"/api/task/continue/{run_id}", headers=self.headers)
            self.assertEqual(response.status_code, 200, response.get_json())
            return _wait_for_pipeline_task(self.client, run_id)

    def _events_of(self, run_id, event_type):
        return [e for e in self.store.list_task_events(run_id)
                if str(e.get("type") or "") == event_type]

    def test_count_enough_but_uncovered_merges_and_dropped_stays_dropped(self):
        self._install_zhilian_source("zl-src", [
            _zl_job("zl-a"),
            _zl_job("zl-b", title="资深后端"),
            _zl_job("zl-keep", company="别的公司"),
        ])
        # run1 粗筛：a 判 dropped
        self._seed_chain_run(
            "chain-run1", status="failed",
            verdicts={"zl-a": {"verdict": "dropped", "reason": "经验不符"}},
            checkpoint=["zl-a", "zl-b", "zl-keep"])
        # run2 接管只写了精筛判定：数量 3 >= 断点 3 但 zl-a 无判定
        self._seed_chain_run(
            "chain-run2", status="paused",
            verdicts={
                "zl-b": {"verdict": "match", "reason": "匹配", "caveats": []},
                "zl-keep": {"verdict": "match", "reason": "匹配", "caveats": []},
                "zl-extra": {"verdict": "match", "reason": "历史精筛", "caveats": []},
            },
            checkpoint=["zl-a", "zl-b", "zl-keep"])

        rough: list[str] = []
        fine: list[str] = []
        finished = self._continue_chain("chain-run2", rough, fine)

        self.assertEqual(finished["status"], "completed", finished)
        # 覆盖口径触发合并：断点全覆盖、粗筛不重筛
        self.assertEqual(rough, [])
        # run1 的 dropped 不复活：不进精筛、只在剔除侧
        self.assertNotIn("zl-a", fine)
        payload = self.store.load_latest_pipeline_result_for_platform("zhilian")
        kept_ids = [str(j.get("platform_job_id") or j.get("job_id"))
                    for j in payload["result"]["jobs"]]
        dropped_ids = [str(e.get("platform_job_id"))
                       for e in payload["result"]["dropped"]]
        self.assertNotIn("zl-a", kept_ids)
        self.assertIn("zl-a", dropped_ids)
        # 合并后断点全覆盖：不再报 resume_inconsistent
        inconsistent = [
            e for e in self.store.list_task_events("chain-run2")
            if str(e.get("type") or "") == "resume_inconsistent"]
        self.assertEqual(inconsistent, [])

    def test_uncoverable_gap_records_missing_count_event(self):
        self._install_zhilian_source("zl-src", [
            _zl_job("zl-a"),
            _zl_job("zl-keep", company="别的公司"),
        ])
        # 链上任何 run 都没有 zl-a 的判定；断点却包含它
        self._seed_chain_run(
            "gap-run1", status="failed",
            verdicts={"zl-b": {"verdict": "match", "reason": "匹配", "caveats": []}},
            checkpoint=["zl-a", "zl-b"], source_count=2)
        self._seed_chain_run(
            "gap-run2", status="paused",
            verdicts={
                "zl-keep": {"verdict": "match", "reason": "匹配", "caveats": []},
                "zl-extra": {"verdict": "match", "reason": "历史精筛", "caveats": []},
            },
            checkpoint=["zl-a", "zl-b"], source_count=2)

        rough: list[str] = []
        fine: list[str] = []
        finished = self._continue_chain("gap-run2", rough, fine)

        self.assertEqual(finished["status"], "completed", finished)
        # 数量口径（2 >= 2）不会报；覆盖口径必须报缺失数且不阻断
        events = self._events_of("gap-run2", "resume_inconsistent")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["missing"], 1)


# 删除基类名字，避免 unittest 把 import 进命名空间的基类再收集一遍
# （子类仍经 __bases__ 持有它，继承重跑不受影响）
del CrossPlatformDedupeIntegrationTests


if __name__ == "__main__":
    unittest.main()
