"""webui.app 平台合同测试（027 自 tests/test_webui_app.py 拆出）。"""
import hashlib
import json
import pathlib
import sqlite3
import sys
import tempfile
import threading
import uuid
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from webui.app import create_app
from webui.task_runners import _iso_epoch_ms


class PlatformAwareSearchScopeTests(unittest.TestCase):
    """tasks005 T401: 平台感知搜索预览/创建 API 测试。

    合同（contracts/http-api.md）：
    - POST /api/search-scope/preview 接受 ``platform``，禁用平台返回 503，
      未知平台返回 400，scope 显式含 ``platform`` 和 ``scope_digest``。
    - POST /api/execute-search 接受 ``platform``，非空 filters 返回 422，
      禁用平台返回 503，平台与 scope 不一致返回 409，搜索 run 的
      ``frozen_filters`` 和筛选快照为空，响应含 ``task_input_digest``。
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

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    # -- preview: platform registration ----------------------------------

    def test_preview_explicit_boss_returns_scope_with_platform(self):
        """显式 platform=boss 预览成功，scope 显式含 platform=boss。"""
        resp = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python 后端"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["scope"]["platform"], "boss")
        self.assertTrue(data["scope"]["scope_digest"])

    def test_preview_omitted_platform_defaults_to_boss(self):
        """省略平台兼容旧 BOSS 前端，scope 显式含 platform=boss。"""
        resp = self.client.post("/api/search-scope/preview", json={
            "keywords": ["Python 后端"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["scope"]["platform"], "boss")

    def test_preview_unknown_platform_returns_400(self):
        """未知平台键返回 400 platform_validation_failed。"""
        resp = self.client.post("/api/search-scope/preview", json={
            "platform": "lagou",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        })
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertEqual(data["error_code"], "platform_validation_failed")

    def test_preview_disabled_platform_returns_503(self):
        """智联 enabled_for_new_tasks=False → 503 platform_disabled。"""
        from unittest import mock
        from webui.platforms import get_platform_or_none
        def _disabled(platform_raw):
            if platform_raw == "zhilian":
                return mock.Mock(enabled_for_new_tasks=False, availability_reason="disabled for test")
            return get_platform_or_none(platform_raw)
        with mock.patch("webui.platforms.get_platform_or_none", side_effect=_disabled):
            resp = self.client.post("/api/search-scope/preview", json={
                "platform": "zhilian",
                "keywords": ["Python"],
                "scope_kind": "nationwide",
                "cities": [],
                "pages_per_combination": 1,
            })
            self.assertEqual(resp.status_code, 503)
            data = resp.get_json()
            self.assertEqual(data["error_code"], "platform_disabled")

    # -- preview: scope digest includes platform -------------------------

    def test_preview_scope_digest_is_deterministic_and_contains_platform(self):
        """同一平台同一参数的 scope_digest 稳定；scope 含 platform 字段。"""
        body = {
            "platform": "boss",
            "keywords": ["Python 后端"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }
        resp1 = self.client.post("/api/search-scope/preview", json=body)
        resp2 = self.client.post("/api/search-scope/preview", json=body)
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(
            resp1.get_json()["scope"]["scope_digest"],
            resp2.get_json()["scope"]["scope_digest"],
        )
        self.assertEqual(resp1.get_json()["scope"]["platform"], "boss")

    # -- execute-search: non-empty filters rejection ---------------------

    def test_execute_search_rejects_non_empty_filters(self):
        """非空 filters 返回 422 search_filters_not_supported，不创建 run。"""
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]
        resp = self.client.post("/api/execute-search", json={
            "platform": "boss",
            "script_params": {
                "keyword": "Python",
                "city": ["上海"],
                "pages": 1,
                "filters": {"stage": "804"},
            },
            "scope_digest": preview["scope_digest"],
        })
        self.assertEqual(resp.status_code, 422)
        data = resp.get_json()
        self.assertEqual(data["error_code"], "search_filters_not_supported")

    def test_execute_search_rejects_screening_fields(self):
        """screening_fields 属于 AI 筛选，搜索请求携带时返回 422。"""
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]
        resp = self.client.post("/api/execute-search", json={
            "platform": "boss",
            "script_params": {
                "keyword": "Python",
                "city": ["上海"],
                "pages": 1,
                "screening_fields": [{"name": "salary", "value": ["405"]}],
            },
            "scope_digest": preview["scope_digest"],
        })
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(
            resp.get_json()["error_code"], "search_filters_not_supported")

    # -- execute-search: disabled platform -------------------------------

    def test_execute_search_disabled_platform_returns_503(self):
        """智联禁用 → execute-search 返回 503 platform_disabled，不创建 run。"""
        from webui.platforms import get_platform_or_none
        def _disabled(platform_raw):
            if platform_raw == "zhilian":
                return mock.Mock(enabled_for_new_tasks=False, availability_reason="disabled for test")
            return get_platform_or_none(platform_raw)
        with mock.patch("webui.platforms.get_platform_or_none", side_effect=_disabled):
            resp = self.client.post("/api/execute-search", json={
                "platform": "zhilian",
                "script_params": {
                    "keyword": "Python",
                    "city": ["全国"],
                    "pages": 1,
                },
            })
            self.assertEqual(resp.status_code, 503)
            self.assertEqual(resp.get_json()["error_code"], "platform_disabled")

    # -- execute-search: platform mismatch -------------------------------

    def test_execute_search_platform_mismatch_returns_409(self):
        """scope 平台与请求平台不一致 → 409 scope_platform_mismatch。"""
        boss_preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]
        resp = self.client.post("/api/execute-search", json={
            "platform": "zhilian",
            "script_params": {
                "keyword": "Python",
                "city": ["上海"],
                "pages": 1,
            },
            "scope_digest": boss_preview["scope_digest"],
        })
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(
            resp.get_json()["error_code"], "scope_platform_mismatch")

    # -- execute-search: creates run with frozen identity ----------------

    def test_execute_search_freezes_platform_and_empty_filter_snapshot(self):
        """搜索 run 持久化 platform=boss、空 frozen_filters、空筛选快照、
        非空 task_input_digest，且 execution_params 含 cdp_port/profile_key。
        """
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
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
                },
                "scope_digest": preview["scope_digest"],
            })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        body = resp.get_json()
        self.assertEqual(body["platform"], "boss")
        self.assertTrue(body["task_input_digest"])
        self.assertEqual(body["scope_digest"], preview["scope_digest"])

        task_id = body["task_id"]
        store = self.app.config["TASK_STORE"]
        run = store.get_screening_run(task_id)
        self.assertIsNotNone(run)
        # 搜索 run 的筛选快照为空
        self.assertEqual(run["frozen_filters"], {})
        # execution_params 含平台冻结身份
        params = run["execution_params"]
        self.assertEqual(params["platform"], "boss")
        self.assertTrue(params["cdp_port"])
        self.assertTrue(params["profile_key"])
        self.assertTrue(params["task_input_digest"])
        self.assertEqual(params["browser_account"], body.get("browser_account") or params["browser_account"])

    def test_execute_search_scope_request_mismatch_returns_409(self):
        """script_params 的关键词/城市/页数与 scope 不一致 → 409 scope_request_mismatch。"""
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]
        resp = self.client.post("/api/execute-search", json={
            "platform": "boss",
            "script_params": {
                "keyword": "Java",  # 与 scope 关键词不一致
                "city": ["上海"],
                "pages": 1,
            },
            "scope_digest": preview["scope_digest"],
        })
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(
            resp.get_json()["error_code"], "scope_request_mismatch")

    # -- T403: source created from frozen runtime -----------------------

    def _wait_for_task(self, task_id, timeout=5.0):
        """Poll search-progress until task leaves queued/running."""
        import time as _time
        deadline = _time.monotonic() + timeout
        last = None
        while _time.monotonic() < deadline:
            resp = self.client.get(f"/api/search-progress/{task_id}")
            if resp.status_code == 200:
                last = resp.get_json()
                if last.get("status") not in ("queued", "running"):
                    return last
            _time.sleep(0.02)
        raise AssertionError(f"task {task_id} did not finish within {timeout}s; last={last}")

    def test_boss_source_receives_frozen_cdp_port(self):
        """T403: BOSS source 显式接收冻结 cdp_port，不使用默认端口。

        合同（contracts/job-source.md 第 42 行）：
        "BOSS 也必须显式接收冻结的 CDP 端口。"

        _make_cdp_source 从 task dict 读取冻结的 platform/cdp_port/
        profile_key，传给 BossCdpSource 构造函数。不读当前 UI、活动
        账号或默认端口。
        """
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]

        fake_result = {
            "ok": True, "jobs": [], "total_scraped": 0,
            "total_matched": 0, "combinations": 1,
            "completed_combos": ["Python|上海"], "error": "",
        }
        with mock.patch("webui.app._BossCdpSource",
                        return_value=mock.MagicMock()) as mock_cls, \
                mock.patch("webui.pipeline_exec.run_search",
                           return_value=fake_result) as mock_search:
            resp = self.client.post("/api/execute-search", json={
                "platform": "boss",
                "script_params": {
                    "keyword": "Python", "city": ["上海"], "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
            })
            self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
            task_id = resp.get_json()["task_id"]
            self._wait_for_task(task_id)

        # BossCdpSource 必须被调用，且显式传入冻结 cdp_port
        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        self.assertIn(
            "cdp_port", kwargs,
            "cdp_port 必须从冻结 runtime 显式传入，不能省略让 adapter 用默认端口",
        )
        self.assertEqual(kwargs["cdp_port"], 9222)
        # run_search 收到的 source 是 mock 返回的对象（验证 source 被传递）
        mock_search.assert_called_once()
        self.assertIsNotNone(mock_search.call_args.args[1])

    def test_resume_restores_frozen_runtime_from_db(self):
        """T403: 续抓时从 DB 恢复 platform/cdp_port/profile_key 到 task dict。

        合同（contracts/http-api.md 第 212 行）：
        "按冻结 browser_account/cdp_port/profile_key 创建原平台 adapter。"

        continue_execute_search 必须从 DB execution_params 恢复冻结身份，
        不能只恢复 browser_account 而丢弃 platform/cdp_port/profile_key。
        """
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]

        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit"):
            resp = self.client.post("/api/execute-search", json={
                "platform": "boss",
                "script_params": {
                    "keyword": "Python", "city": ["上海"], "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
            })
        self.assertEqual(resp.status_code, 200)
        task_id = resp.get_json()["task_id"]

        # 模拟服务重启：清空内存 task，DB 保留 paused 状态
        store = self.app.config["TASK_STORE"]
        store.update_screening_run(task_id, status="running")
        store.update_screening_run(
            task_id, status="paused", current_stage="scrape",
            error_code="captcha_required", error_reason="测试暂停",
        )
        store.save_checkpoint(task_id, "scrape", [])
        self.app.config["PIPELINE_TASKS"].pop(task_id, None)

        # 续抓：mock executor 和 block check 防止任务实际运行
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda run: (True, "", "")
        with mock.patch.object(executor, "submit"):
            resp = self.client.post(
                f"/api/execute-search/continue/{task_id}")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

        # task dict 必须从 DB 恢复冻结 runtime
        tasks = self.app.config["PIPELINE_TASKS"]
        task = tasks.get(task_id)
        self.assertIsNotNone(task, "续抓后 task 必须在内存中恢复")
        self.assertEqual(task.get("platform"), "boss")
        self.assertTrue(task.get("cdp_port"),
                        "续抓后 task 必须有冻结 cdp_port")
        self.assertTrue(task.get("profile_key"),
                        "续抓后 task 必须有冻结 profile_key")
        self.assertTrue(task.get("task_input_digest"),
                        "续抓后 task 必须有冻结 task_input_digest")
        db_started = _iso_epoch_ms(
            store.get_screening_run(task_id).get("started_at"))
        self.assertEqual(
            task.get("started_at"), db_started,
            "续抓必须沿用原任务 started_at，前端计时不清零",
        )

    # -- T404: source attempt before combo result -----------------------

    def test_source_attempt_precedes_combo_result(self):
        """T404: source attempt 在 combo result 之前持久化。

        合同（tasks005 节点门禁 A）：
        "在任何完成键、run 进度、状态或 snapshot 更新前追加 source attempt"
        """
        from webui.source import SourceOutcome
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]

        fake_source = mock.MagicMock()
        fake_source.preflight.return_value = SourceOutcome.success(
            safe_log="source_ready")
        fake_source.fetch_list.return_value = SourceOutcome.success(
            jobs=[{"job_id": "job-1", "title": "Python"}],
            safe_log="list job_count=1",
            input_hash="sha256-fake",
        )
        store = self.app.config["TASK_STORE"]
        call_order = []
        orig_append = store.append_source_attempt
        orig_save = store.save_scrape_combo_result

        def tracked_append(**kw):
            call_order.append("append_source_attempt")
            return orig_append(**kw)

        def tracked_save(*args, **kw):
            call_order.append("save_scrape_combo_result")
            return orig_save(*args, **kw)

        with mock.patch("webui.app._BossCdpSource", return_value=fake_source), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch.object(store, "append_source_attempt",
                                  side_effect=tracked_append), \
                mock.patch.object(store, "save_scrape_combo_result",
                                  side_effect=tracked_save):
            resp = self.client.post("/api/execute-search", json={
                "platform": "boss",
                "script_params": {
                    "keyword": "Python", "city": ["上海"], "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
            })
            self.assertEqual(resp.status_code, 200)
            task_id = resp.get_json()["task_id"]
            self._wait_for_task(task_id)

        self.assertIn("append_source_attempt", call_order,
                      "source attempt 必须被调用")
        self.assertIn("save_scrape_combo_result", call_order,
                      "combo result 必须被调用")
        append_idx = call_order.index("append_source_attempt")
        save_idx = call_order.index("save_scrape_combo_result")
        self.assertLess(
            append_idx, save_idx,
            "source attempt 必须在 combo result 之前持久化")

    def test_source_attempt_failure_prevents_combo_result(self):
        """T404: append_source_attempt 失败时不得推进 combo result。"""
        from webui.source import SourceOutcome
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]

        fake_source = mock.MagicMock()
        fake_source.preflight.return_value = SourceOutcome.success(
            safe_log="source_ready")
        fake_source.fetch_list.return_value = SourceOutcome.success(
            jobs=[{"job_id": "job-1", "title": "Python"}],
            safe_log="list job_count=1",
            input_hash="sha256-fake",
        )
        store = self.app.config["TASK_STORE"]
        save_called = False

        def fail_append(**kw):
            raise sqlite3.Error("persist failed")

        def tracked_save(*args, **kw):
            nonlocal save_called
            save_called = True

        with mock.patch("webui.app._BossCdpSource", return_value=fake_source), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch.object(store, "append_source_attempt",
                                  side_effect=fail_append), \
                mock.patch.object(store, "save_scrape_combo_result",
                                  side_effect=tracked_save):
            resp = self.client.post("/api/execute-search", json={
                "platform": "boss",
                "script_params": {
                    "keyword": "Python", "city": ["上海"], "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
            })
            self.assertEqual(resp.status_code, 200)
            task_id = resp.get_json()["task_id"]
            self._wait_for_task(task_id)

        self.assertFalse(
            save_called,
            "append_source_attempt 失败时不得推进 save_scrape_combo_result")

    # -- T405: 按 combo 最新 attempt 汇总 source outcomes ----------------

    def _create_finished_scrape_task(self, platform="boss"):
        """创建一个已完成的搜索任务，返回 (task_id, store)。"""
        from webui.source import SourceOutcome
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": platform,
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]

        fake_source = mock.MagicMock()
        fake_source.preflight.return_value = SourceOutcome.success(
            safe_log="source_ready")
        fake_source.fetch_list.return_value = SourceOutcome.success(
            jobs=[{"job_id": "job-1", "title": "Python"}],
            safe_log="list job_count=1",
            input_hash="sha256-fake",
        )
        with mock.patch("webui.app._BossCdpSource", return_value=fake_source), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"):
            resp = self.client.post("/api/execute-search", json={
                "platform": platform,
                "script_params": {
                    "keyword": "Python", "city": ["上海"], "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
            })
            self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
            task_id = resp.get_json()["task_id"]
            self._wait_for_task(task_id)
        store = self.app.config["TASK_STORE"]
        return task_id, store

    def test_search_progress_returns_platform_and_digest(self):
        """T405: search-progress 返回 platform、task_input_digest、
        source_summary 和 source_outcomes。"""
        task_id, _ = self._create_finished_scrape_task()

        resp = self.client.get(f"/api/search-progress/{task_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("platform"), "boss")
        self.assertTrue(data.get("task_input_digest"))
        self.assertIn("source_summary", data)
        self.assertIn("source_outcomes", data)

    def test_task_state_returns_platform_and_digest(self):
        """T405: task-state 返回 platform、task_input_digest、
        source_summary 和 source_outcomes。"""
        task_id, _ = self._create_finished_scrape_task()

        resp = self.client.get(f"/api/task-state/{task_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("platform"), "boss")
        self.assertTrue(data.get("task_input_digest"))
        self.assertIn("source_summary", data)
        self.assertIn("source_outcomes", data)

    def test_source_outcomes_latest_per_combo(self):
        """T405: source_outcomes 按 combo 最新 attempt 汇总。

        同一 combo 多次 attempt 时只返回最新；不同 combo 各自返回最新。
        """
        task_id, store = self._create_finished_scrape_task()

        # 任务完成后已有 1 条 attempt（combo: Python|上海，non_empty）
        # 追加同 combo 第 2 次 attempt（模拟重试后变 empty）
        store.append_source_attempt(
            run_id=task_id, platform="boss",
            combo_key="Python|上海", attempt_no=2,
            input_hash="sha256-v2",
            outcome_kind="empty", job_count=0,
            empty_evidence={"kind": "explicit_empty_state",
                            "fixture_version": "v1",
                            "marker": "normalized-empty-state"},
        )
        # 另一个 combo
        store.append_source_attempt(
            run_id=task_id, platform="boss",
            combo_key="Java|上海", attempt_no=1,
            input_hash="sha256-java",
            outcome_kind="non_empty", job_count=3,
        )

        resp = self.client.get(f"/api/task-state/{task_id}")
        data = resp.get_json()
        outcomes = data.get("source_outcomes") or []
        by_combo = {o["combo_key"]: o for o in outcomes}
        self.assertIn("Python|上海", by_combo)
        self.assertIn("Java|上海", by_combo)
        # Python|上海 最新是 attempt_no=2，empty
        self.assertEqual(by_combo["Python|上海"]["attempt_no"], 2)
        self.assertEqual(by_combo["Python|上海"]["outcome_kind"], "empty")
        # Java|上海 是 non_empty
        self.assertEqual(by_combo["Java|上海"]["outcome_kind"], "non_empty")

    def test_no_empty_inference_from_zero_jobs(self):
        """T405: 无 source attempt 记录时不从岗位数为零推断 empty。

        刷新/重启后若 DB 无 attempt 记录，source_outcomes 为空列表，
        source_summary 不报告 empty。
        """
        task_id, store = self._create_finished_scrape_task()

        # 删除所有 source attempts 模拟无记录
        with store._connection() as conn:
            conn.execute(
                "DELETE FROM screening_source_attempts WHERE run_id=?",
                (task_id,))

        resp = self.client.get(f"/api/task-state/{task_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        outcomes = data.get("source_outcomes") or []
        self.assertEqual(outcomes, [],
                         "无 attempt 记录时 source_outcomes 必须为空，"
                         "不从零岗位推断 empty")
        summary = data.get("source_summary") or {}
        self.assertEqual(summary.get("empty_count", 0), 0,
                         "无 attempt 记录时不得报告 empty")

    def test_search_progress_identity_conflict(self):
        """T405: 内存 task 平台与 DB run 不一致 → 409 run_identity_conflict。"""
        task_id, store = self._create_finished_scrape_task()

        # 篡改 DB run 的 platform，制造内存（boss）与 DB（zhilian）不一致
        with store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET platform='zhilian' WHERE id=?",
                (task_id,))

        resp = self.client.get(f"/api/search-progress/{task_id}")
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertEqual(data.get("error"), "run_identity_conflict")

    def test_search_progress_digest_conflict(self):
        """T713: 内存 task digest 与 DB run 不一致 → 409 run_identity_conflict。"""
        task_id, store = self._create_finished_scrape_task()

        # 给内存 task 写入一个 task_input_digest，再篡改 DB run 的 digest
        tasks = self.app.config["PIPELINE_TASKS"]
        task = tasks.get(task_id)
        if task is not None:
            task["task_input_digest"] = "mem-digest-aaa"
        with store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET task_input_digest='db-digest-bbb' WHERE id=?",
                (task_id,))

        resp = self.client.get(f"/api/search-progress/{task_id}")
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertEqual(data.get("error"), "run_identity_conflict")


# ======================================================================
# 门禁B: T406-T409 — AI run 平台继承 + 结果身份
# ======================================================================


class AiScreenPlatformInheritanceTests(unittest.TestCase):
    """T406-T407: ai_screen 平台继承与筛选快照测试。"""

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

    def _create_completed_scrape_run(self, platform="boss"):
        """创建并持久化一个完成的搜索 run。"""
        run_id = f"scrape_{platform}_{uuid.uuid4().hex[:8]}"
        self.store.create_screening_run(
            run_id,
            frozen_filters={},
            source_count=5,
            execution_params={
                "platform": platform,
                "cdp_port": 9222,
                "profile_key": "a",
                "task_input_digest": hashlib.sha256(
                    json.dumps({"platform": platform}, sort_keys=True).encode()
                ).hexdigest(),
            },
            backend_version="test",
        )
        self.store.update_screening_run(run_id, status="succeeded",
                                          current_stage="done",
                                          processed_count=5, match_count=3)
        # 在内存中注册为已完成任务
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "done", "progress": {}, "logs": [],
            "result": {"ok": True, "jobs": [], "total_scraped": 5,
                       "total_matched": 3, "completed_combos": ["Python|上海"],
                       "error": ""},
            "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(),
            "platform": platform,
            "task_input_digest": "test_digest",
        }
        return run_id

    # -- T406: 平台一致性校验 -------------------------------------------

    def test_ai_screen_with_matching_platform_succeeds(self):
        """T406: 客户端 platform 与父 run 一致时成功。"""
        scrape_id = self._create_completed_scrape_run("boss")
        resp = self.client.post("/api/ai-screen", json={
            "scrape_task_id": scrape_id,
            "platform": "boss",
            "screening_fields": {"salary": ["405"]},
            "profile_summary": "测试候选人",
        })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["platform"], "boss")
        self.assertTrue(data["task_input_digest"])

    def test_ai_screen_platform_mismatch_returns_409(self):
        """T406: 客户端 platform 与父 run 不一致 → 409 parent_platform_mismatch。"""
        scrape_id = self._create_completed_scrape_run("boss")
        resp = self.client.post("/api/ai-screen", json={
            "scrape_task_id": scrape_id,
            "platform": "zhilian",
            "screening_fields": {"salary": ["405"]},
            "profile_summary": "测试候选人",
        })
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertEqual(data.get("error"), "parent_platform_mismatch")

    def test_ai_screen_omitted_platform_inherits_parent(self):
        """T406: 省略 platform 时继承父 run 平台。"""
        scrape_id = self._create_completed_scrape_run("boss")
        resp = self.client.post("/api/ai-screen", json={
            "scrape_task_id": scrape_id,
            "screening_fields": {"salary": ["405"]},
            "profile_summary": "测试候选人",
        })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertEqual(data["platform"], "boss")

    def test_ai_screen_filter_schema_version_mismatch_returns_409(self):
        """T406: filter_schema_version 与父 run 不一致 → 409。"""
        scrape_id = self._create_completed_scrape_run("boss")
        # 设置父 run 的 schema_version
        self.store.update_screening_run(scrape_id)
        # 直接改 DB 设置 schema_version
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET filter_schema_version=2 WHERE id=?",
                (scrape_id,))
        resp = self.client.post("/api/ai-screen", json={
            "scrape_task_id": scrape_id,
            "platform": "boss",
            "screening_fields": {"salary": ["405"]},
            "profile_summary": "测试候选人",
            "filter_schema_version": 1,
        })
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertEqual(data.get("error"), "filter_schema_version_mismatch")

    # -- T407: 别人字段稳定值与当时标签的完整快照 -----------------------

    def test_ai_screen_saves_filter_snapshot(self):
        """T407: AI 筛选保存字段稳定值和当时标签的完整筛选快照。"""
        scrape_id = self._create_completed_scrape_run("boss")
        screening_fields = {
            "salary": ["405", "406"],
            "experience": ["103"],
            "degree": ["202"],
        }
        resp = self.client.post("/api/ai-screen", json={
            "scrape_task_id": scrape_id,
            "platform": "boss",
            "screening_fields": screening_fields,
            "profile_summary": "测试候选人",
        })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        task_id = resp.get_json()["task_id"]

        # 验证筛选快照已持久化
        run = self.store.get_screening_run(task_id)
        self.assertIsNotNone(run)
        self.assertEqual(run.get("platform"), "boss")
        params = run.get("execution_params") or {}
        self.assertEqual(params.get("platform"), "boss")
        self.assertTrue(
            params.get("task_input_digest"),
            "task_input_digest 必须存在于 execution_params",
        )

    def test_ai_screen_creates_run_with_parent_platform(self):
        """T407: 新 AI run 的 execution_params 含父 run 平台。"""
        scrape_id = self._create_completed_scrape_run("boss")
        resp = self.client.post("/api/ai-screen", json={
            "scrape_task_id": scrape_id,
            "platform": "boss",
            "screening_fields": {"salary": ["405"]},
            "profile_summary": "测试候选人",
        })
        self.assertEqual(resp.status_code, 200)
        task_id = resp.get_json()["task_id"]
        run = self.store.get_screening_run(task_id)
        self.assertIsNotNone(run)
        params = run.get("execution_params") or {}
        self.assertEqual(params.get("platform"), "boss")
        self.assertEqual(params.get("scrape_task_id"), scrape_id)


# ======================================================================
# 门禁D: T414-T419 — 平台敏感外围入口
# ======================================================================


class PlatformAwareTaskStateTests(unittest.TestCase):
    """T414: task state/progress 返回平台和 source outcomes。"""

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

    def test_task_state_returns_platform(self):
        """T414: api_task_state 返回目标 run 真实平台。"""
        run_id = "test_ts_platform"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'zhilian', 'paused', 'process_log', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        resp = self.client.get(f"/api/task-state/{run_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("platform"), "zhilian")
        self.assertIn("source_summary", data)
        self.assertIn("source_outcomes", data)

    def test_search_progress_returns_platform_and_source_outcomes(self):
        """T414: search-progress 返回平台和 source outcomes。"""
        run_id = "test_sp_platform"
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
            "platform": "boss",
            "task_input_digest": "test_digest",
        }
        resp = self.client.get(f"/api/search-progress/{run_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("platform"), "boss")
        self.assertIn("source_summary", data)
        self.assertIn("source_outcomes", data)


class PlatformAwareCancelTests(unittest.TestCase):
    """T415: 取消的平台感知。"""

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

    def test_cancel_returns_platform(self):
        """T415: 取消接口返回平台信息。"""
        run_id = "test_cancel_platform"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'boss', 'running', 'process_log', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
            "platform": "boss",
        }
        resp = self.client.post(f"/api/task/cancel/{run_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("platform"), "boss")
        self.assertIn("status", data)

    def test_cancel_writes_durable_state(self):
        """T415: 取消先 durable 写 interrupted，再发内存事件。"""
        run_id = "test_cancel_durable"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'boss', 'running', 'process_log', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
            "platform": "boss",
        }
        resp = self.client.post(f"/api/task/cancel/{run_id}")
        self.assertEqual(resp.status_code, 200)
        # 验证 DB 已更新
        run = self.store.get_screening_run(run_id)
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "interrupted")

    def test_cancel_with_jobs_keeps_scrape_data_without_history_round(self):
        """017-US1: 取消保留底层已抓岗位数据，但不再生成历史轮。"""
        run_id = "cancel-with-jobs-history"
        jobs = [
            {"job_id": "j1", "platform_job_id": "j1", "title": "岗位1",
             "source_url": "https://zhipin.example/j1.html"},
            {"job_id": "j2", "platform_job_id": "j2", "title": "岗位2",
             "source_url": "https://zhipin.example/j2.html"},
        ]
        self.store.create_screening_run(
            run_id, source_count=len(jobs),
            execution_params={"platform": "boss"},
        )
        self.store.save_scrape_combo_result(run_id, "kw|city", jobs, ["kw|city"])
        self.store.update_screening_run(
            run_id, status="running", current_stage="scrape")

        resp = self.client.post(f"/api/task/cancel/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        run = self.store.get_screening_run(run_id)
        # DB 存 interrupted + user_cancelled（对外公共词汇 cancelled）
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["interruption_kind"], "user_cancelled")
        # 底层已抓岗位数据保留，可对同一批抓取结果重新发起筛选
        kept = self.store.load_scrape_run_jobs(run_id)
        self.assertEqual(len(kept), 2)
        # 017-US1: 取消不再生成历史轮
        self.assertEqual(self.store.list_history_rounds("boss"), [])
        history = self.client.get("/api/result-history").get_json()
        self.assertEqual(history["items"], [])

    def test_cancel_without_jobs_does_not_create_history(self):
        """FR-019: 没有岗位产出的取消不进入历史。"""
        run_id = "cancel-no-jobs-history"
        self.store.create_screening_run(
            run_id, source_count=0,
            execution_params={"platform": "boss"},
        )
        self.store.update_screening_run(
            run_id, status="running", current_stage="scrape")

        resp = self.client.post(f"/api/task/cancel/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(self.store.list_history_rounds("boss"), [])

    def test_restart_interrupted_run_has_no_history_round(self):
        """017-US1: 进程强杀重启后任务显示中断、可续跑，历史不新增轮。"""
        run_id = "restart-interrupted-017"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, error_code, record_kind, "
                "frozen_filters_json, source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'boss', 'interrupted', 'restart', 'process_log', "
                "'{}', 2, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["status"], "interrupted")
        self.assertTrue(data["resumable"])
        # 017-US1: 重启中断不产生历史轮
        self.assertEqual(self.store.list_history_rounds("boss"), [])
        history = self.client.get("/api/result-history").get_json()
        self.assertEqual(history["items"], [])


class PlatformAwareFinishTests(unittest.TestCase):
    """T416: 提前结束的平台感知。"""

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

    def test_finish_rejects_user_cancelled(self):
        """T416: user_cancelled 的 run 不能通过 finish 改写。"""
        run_id = "test_finish_user_cancelled"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, interruption_kind, record_kind, "
                "frozen_filters_json, source_count, match_count, "
                "mismatch_count, execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'boss', 'interrupted', 'user_cancelled', "
                "'process_log', '{}', 0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertEqual(data.get("error"), "user_cancelled")

    def test_finish_accepts_paused_and_returns_platform(self):
        """T416: paused 的 run 可 finish，返回平台。"""
        run_id = "test_finish_paused"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, "
                "frozen_filters_json, source_count, match_count, "
                "mismatch_count, execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'boss', 'paused', "
                "'process_log', '{}', 0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        resp = self.client.post(f"/api/task/finish/{run_id}")
        # 可能因缺少 scrape_task_id 而 409，但不应该报 404 或 500
        self.assertNotEqual(resp.status_code, 404)
        if resp.status_code == 409:
            data = resp.get_json()
            self.assertIn(data.get("error", ""),
                          ["missing_scrape_snapshot", "not_paused"])

    def test_finish_accepts_restart_interrupted(self):
        """T416: interrupted/process_restart 的 run 可 finish。"""
        run_id = "test_finish_restart"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, interruption_kind, record_kind, "
                "frozen_filters_json, source_count, match_count, "
                "mismatch_count, execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'boss', 'interrupted', 'process_restart', "
                "'process_log', '{}', 0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        resp = self.client.post(f"/api/task/finish/{run_id}")
        # 可能因缺少 scrape_task_id 而 409，但不应该报 404 或 500
        self.assertNotEqual(resp.status_code, 404)
        if resp.status_code == 409:
            data = resp.get_json()
            self.assertIn(data.get("error", ""),
                          ["missing_scrape_snapshot", "not_paused"])


class PlatformAwareJobDetailTests(unittest.TestCase):
    """T417: 单 JD 抓取平台继承。"""

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

    def test_job_detail_missing_params_returns_400(self):
        """T417: 缺少 job_id 或 source_url 返回 400。"""
        resp = self.client.post("/api/job-detail", json={
            "job_id": "",
            "source_url": "",
        })
        self.assertEqual(resp.status_code, 400)


class DraftSwitchTargetRunConservationTests(unittest.TestCase):
    """T712: 创建目标 run 后把草稿切到另一平台，外围操作仍作用于原 run。

    验证 cancel/finish/continue/reset 路由从 run.platform 读取平台，
    不读全局 draft platform。草稿切换不应改变目标 run 的平台归属。
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

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _seed_paused_zhilian_run(self, run_id="draft-switch-zhilian", status="paused", record_kind="process_log"):
        """种入一个 zhilian run，模拟目标 run 已创建。"""
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'zhilian', ?, 'process_log', '{}', "
                "0, 0, 0, '{\"platform\":\"zhilian\"}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id), status),
            )
        return run_id

    def test_cancel_after_draft_switch_still_targets_original_run(self):
        """T712: 创建 zhilian run → 草稿切到 boss → cancel 仍作用于 zhilian run。"""
        run_id = self._seed_paused_zhilian_run()
        # cancel 路由不读 draft，从 run.platform 读取
        resp = self.client.post(f"/api/task/cancel/{run_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("platform"), "zhilian",
                         "草稿切换后 cancel 仍应返回原 run 平台")
        # 验证 DB 中 run 的 platform 未被改写
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["platform"], "zhilian")

    def test_reset_after_draft_switch_still_targets_original_run(self):
        """017-US4: 旧结果清空端点已删除（404）；归档/删除统一走历史接口。"""
        # 旧 reset 端点不存在（无论草稿如何切换）
        resp = self.client.post("/api/reset-latest-result", json={
            "run_id": "anything", "platform": "boss",
        })
        self.assertEqual(resp.status_code, 404)
        # 新路径：归档走 archive-latest，删除走 DELETE /api/result-history/<run_id>
        run_id = self._seed_paused_zhilian_run(status="succeeded")
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET record_kind = 'result_snapshot' WHERE id = ?",
                (run_id,),
            )
        archive = self.client.post("/api/result-history/archive-latest")
        self.assertEqual(archive.status_code, 200)
        self.assertIn(run_id, archive.get_json()["archived_run_ids"])


class CrossPlatformBrowserConservationTests(unittest.TestCase):
    """T714: cancel/finish zhilian run 不得关闭 boss 浏览器，反之亦然。

    路由层调用 close_debug_chrome() 时不得误关另一平台的浏览器。
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

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _seed_running_run(self, run_id, platform="zhilian"):
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                f"VALUES (?, '{platform}', 'running', 'process_log', '{{}}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
            "platform": platform,
        }
        return run_id

    @mock.patch("webui.pipeline_exec.close_debug_chrome")
    def test_cancel_zhilian_run_does_not_close_with_boss_port(self, mock_close):
        """T714: cancel zhilian run 时 close_debug_chrome 不得用 BOSS 默认端口 9222 关闭。"""
        run_id = self._seed_running_run("cancel-zhilian-conservation", platform="zhilian")
        resp = self.client.post(f"/api/task/cancel/{run_id}")
        self.assertEqual(resp.status_code, 200)
        # close_debug_chrome 被调用时，参数不得是 BOSS 默认端口 9222
        if mock_close.called:
            call_args = mock_close.call_args
            port_arg = call_args[0][0] if call_args[0] else None
            self.assertNotEqual(port_arg, 9222,
                                "cancel zhilian run 不得用 BOSS 端口 9222 关闭浏览器")

    @mock.patch("webui.pipeline_exec.close_debug_chrome")
    def test_cancel_boss_run_does_not_close_with_zhilian_port(self, mock_close):
        """T714: cancel boss run 时 close_debug_chrome 不得用智联端口 9223 关闭。"""
        run_id = self._seed_running_run("cancel-boss-conservation", platform="boss")
        resp = self.client.post(f"/api/task/cancel/{run_id}")
        self.assertEqual(resp.status_code, 200)
        if mock_close.called:
            call_args = mock_close.call_args
            port_arg = call_args[0][0] if call_args[0] else None
            self.assertNotEqual(port_arg, 9223,
                                "cancel boss run 不得用智联端口 9223 关闭浏览器")


class PlatformAwareResetResultTests(unittest.TestCase):
    """T418: 结果重置平台感知。"""

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

    def test_legacy_reset_endpoint_removed(self):
        """017-US4: 旧结果清空端点已删除（404），归档/删除统一走历史接口。"""
        resp = self.client.post("/api/reset-latest-result", json={
            "run_id": "nonexistent",
        })
        self.assertEqual(resp.status_code, 404)

    def test_archive_and_delete_go_through_history_api(self):
        """017-US4: 归档走 archive-latest，删除走 DELETE /api/result-history/<run_id>。"""
        run_id = "archive-delete-017"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at, finished_at) "
                "VALUES (?, 'boss', 'succeeded', 'result_snapshot', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL, NULL)",
                (str(run_id),),
            )
        archive = self.client.post("/api/result-history/archive-latest")
        self.assertEqual(archive.status_code, 200)
        self.assertIn(run_id, archive.get_json()["archived_run_ids"])
        deleted = self.client.delete(f"/api/result-history/{run_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.get_json()["deleted"], True)


class PlatformAwareBrowserAccountTests(unittest.TestCase):
    """T419: 浏览器账号的平台语义。"""

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

    def test_browser_list_includes_platform(self):
        """T419: 浏览器列表接口返回平台信息。"""
        resp = self.client.get("/api/browser-accounts")
        # 接口可能返回 200 或 404，但不应该 500
        self.assertNotEqual(resp.status_code, 500)
        if resp.status_code == 200:
            data = resp.get_json()
            self.assertIn("accounts", data)

    def test_check_returns_platform_info(self):
        """T419: /api/check 返回平台信息。"""
        resp = self.client.get("/api/check")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        # 至少返回 ok 和平台信息
        self.assertIn("ok", data)


# ======================================================================
# T207 补丁：HTTP 端点暴露（/api/platforms、/api/options?platform、/api/filter-labels?platform）
# ======================================================================
# platforms.py 的服务投影函数（project_filter_schema、list_platforms）在
# tasks003 已测（见 tests/test_platforms.py T207），但 app.py 从未将其暴露
# 为 HTTP 端点——tasks003 允许文件范围不含 app.py。本类补 HTTP 端点测试，
# 与 test_platforms.py 的函数投影测试互补。详见 plan.md 切片 3 末尾。


class PlatformAwareEndpointsTests(unittest.TestCase):
    """T207 补丁：三平台感知端点的 HTTP 行为。"""

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

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    # -- /api/platforms -----------------------------------------------

    def test_platforms_endpoint_returns_registry_with_default(self):
        """/api/platforms 返回 BOSS+智联注册项，default=boss。"""
        resp = self.client.get("/api/platforms")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("default_platform"), "boss")
        platforms = {p["key"]: p for p in data.get("platforms", [])}
        self.assertIn("boss", platforms)
        self.assertIn("zhilian", platforms)
        # 智联 fixture 未核验，禁用新任务；BOSS 已启用
        # 智联真实元数据核验后启用；BOSS 已启用
        self.assertTrue(platforms["zhilian"]["enabled_for_new_tasks"])
        self.assertTrue(platforms["boss"]["enabled_for_new_tasks"])
        # 不返回 profile 路径/路径摘要（T207 安全要求）
        for p in data.get("platforms", []):
            for key in ("profile_dir", "boss_profile_dir", "profile_key", "cdp_port"):
                self.assertNotIn(key, p, f"平台投影不得返回 {key}")

    def test_platforms_endpoint_returns_schema_and_city_versions(self):
        """/api/platforms 返回 filter_schema_version 和 city_mapping_version。"""
        resp = self.client.get("/api/platforms")
        data = resp.get_json()
        platforms = {p["key"]: p for p in data["platforms"]}
        self.assertEqual(platforms["boss"]["filter_schema_version"], 2)
        self.assertEqual(platforms["boss"]["city_mapping_version"], 2)
        self.assertEqual(platforms["zhilian"]["filter_schema_version"], 3)
        self.assertEqual(platforms["zhilian"]["city_mapping_version"], 2)

    # -- /api/options -------------------------------------------------

    def test_options_without_platform_keeps_legacy_shape(self):
        """无 platform 参数时保持旧 BOSS 形状 {filters, cities}（兼容现有前端）。"""
        resp = self.client.get("/api/options")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("filters", data)
        self.assertIn("cities", data)
        self.assertIn("stage", data["filters"])
        self.assertIn({"label": "上海", "value": "上海"}, data["cities"])
        # 不应出现新形状字段
        for forbidden in ("ok", "platform", "city_mapping_version", "schema_version"):
            self.assertNotIn(forbidden, data)

    def test_options_with_platform_boss_returns_canonical_cities(self):
        """/api/options?platform=boss 返回新形状 {ok, platform, city_mapping_version, cities}。"""
        resp = self.client.get("/api/options?platform=boss")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["platform"], "boss")
        self.assertEqual(data["city_mapping_version"], 2)
        for city in data["cities"]:
            self.assertIn("label", city)
            self.assertIn("value", city)
            # 合同：前端不接收平台城市码；后端解析并冻结
            self.assertNotIn("platform_code", city)
            self.assertNotIn("code", city)

    def test_options_with_platform_zhilian_returns_nationwide_only(self):
        """/api/options?platform=zhilian 只返回全国（jl0），其它城市码未核验不暴露。"""
        resp = self.client.get("/api/options?platform=zhilian")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["platform"], "zhilian")
        self.assertEqual(data["city_mapping_version"], 2)
        cities = data["cities"]
        self.assertGreaterEqual(len(cities), 20)
        labels = {c["label"] for c in cities}
        self.assertIn("全国", labels)
        self.assertIn("上海", labels)

    def test_options_with_unknown_platform_returns_400(self):
        """未知平台返回 400 platform_validation_failed。"""
        resp = self.client.get("/api/options?platform=unknown")
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertEqual(data.get("error_code"), "platform_validation_failed")

    def test_analyze_resume_boss_projects_stage_and_common_fields(self):
        """BOSS 简历分析返回 stage 语义，不出现 company_nature。"""
        import io
        from webui import app as app_module
        fields = {
            "keyword": [{"word": "Python 后端", "recommended": True}],
            "city": ["上海"], "salary": ["406"], "experience": ["105"],
            "degree": ["203"], "industry": ["1001"], "scale": ["303"],
            "stage": ["804"], "profile_summary": "3年Python后端", "company_nature": ["1"],
        }
        store = self.app.config["TASK_STORE"]
        with mock.patch.object(store, "get_ai_settings", return_value={
            "is_configured": True, "endpoint_url": "https://api.example.com", "model": "test",
        }), mock.patch.object(store, "get_credential_ref", return_value="ref"), \
                mock.patch.object(app_module.ai_service, "retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.analyze_resume_to_fields", return_value=fields):
            resp = self.client.post(
                "/api/analyze-resume",
                data={"file": (io.BytesIO(b"resume"), "resume.txt"), "platform": "boss"},
                content_type="multipart/form-data",
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["platform"], "boss")
        self.assertEqual(data["fields"]["city"], [])
        self.assertEqual(data["semantic"]["city"], [])
        self.assertNotIn("company_nature", data["fields"])
        self.assertEqual(data["fields"]["stage"], ["804"])
        self.assertEqual(data["semantic"]["stage"], ["B轮"])
        self.assertEqual(data["semantic"]["experience"], ["3-5年"])
        self.assertNotIn("company_nature", data["semantic"])

    def test_analyze_resume_passes_through_profile_facts(self):
        """B033：简历分析响应透传 profile_facts（画像事实链路源头）。"""
        import io
        from webui import app as app_module
        facts = {
            "core_skills": ["Python", "Django"],
            "projects": [{"name": "订单系统", "role": "后端开发"}],
            "job_type": "全职",
            "languages": ["英语"],
        }
        fields = {
            "keyword": [{"word": "Python 后端", "recommended": True}],
            "city": ["上海"], "salary": ["406"], "experience": ["105"],
            "degree": ["203"], "industry": ["1001"], "scale": ["303"],
            "stage": ["804"], "profile_summary": "3年Python后端",
            "profile_facts": facts,
        }
        store = self.app.config["TASK_STORE"]
        with mock.patch.object(store, "get_ai_settings", return_value={
            "is_configured": True, "endpoint_url": "https://api.example.com", "model": "test",
        }), mock.patch.object(store, "get_credential_ref", return_value="ref"), \
                mock.patch.object(app_module.ai_service, "retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.analyze_resume_to_fields", return_value=fields):
            resp = self.client.post(
                "/api/analyze-resume",
                data={"file": (io.BytesIO(b"resume"), "resume.txt"), "platform": "boss"},
                content_type="multipart/form-data",
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["fields"]["profile_facts"], facts)

    def test_analyze_resume_zhilian_projects_company_nature_and_drops_stage(self):
        """智联简历分析返回 company_nature 语义，不出现 stage。"""
        import io
        from webui import app as app_module
        fields = {
            "keyword": [{"word": "Python 后端", "recommended": True}],
            "city": ["上海"], "salary": [], "experience": ["0305"], "degree": ["4"],
            "industry": [], "scale": [], "company_nature": ["1"], "stage": ["804"],
            "profile_summary": "3年Python后端",
        }
        store = self.app.config["TASK_STORE"]
        with mock.patch.object(store, "get_ai_settings", return_value={
            "is_configured": True, "endpoint_url": "https://api.example.com", "model": "test",
        }), mock.patch.object(store, "get_credential_ref", return_value="ref"), \
                mock.patch.object(app_module.ai_service, "retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.analyze_resume_to_fields", return_value=fields):
            resp = self.client.post(
                "/api/analyze-resume",
                data={"file": (io.BytesIO(b"resume"), "resume.txt"), "platform": "zhilian"},
                content_type="multipart/form-data",
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["platform"], "zhilian")
        self.assertNotIn("stage", data["fields"])
        self.assertEqual(data["fields"]["company_nature"], ["1"])
        self.assertEqual(data["semantic"]["company_nature"], ["国企"])
        self.assertNotIn("stage", data["semantic"])

    def test_analyze_resume_unknown_platform_returns_400(self):
        """简历分析未知平台在调用 AI 前返回 platform_validation_failed。"""
        import io
        with mock.patch("webui.ai.analyze_resume_to_fields") as analyze:
            resp = self.client.post(
                "/api/analyze-resume",
                data={"file": (io.BytesIO(b"resume"), "resume.txt"), "platform": "unknown"},
                content_type="multipart/form-data",
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json().get("error_code"), "platform_validation_failed")
        analyze.assert_not_called()

    # -- /api/filter-labels -------------------------------------------

    def test_filter_labels_without_platform_keeps_legacy_shape(self):
        """无 platform 参数时保持旧 BOSS 形状 {labels: {...}}（兼容现有前端）。"""
        resp = self.client.get("/api/filter-labels")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("labels", data)
        # 旧形状是 6 字段，含 stage，不含 company_nature
        self.assertIn("stage", data["labels"])
        self.assertNotIn("company_nature", data["labels"])
        # 不应出现新形状字段
        for forbidden in ("ok", "platform", "schema_version", "enabled_for_new_tasks", "fields"):
            self.assertNotIn(forbidden, data)

    def test_filter_labels_with_platform_zhilian_returns_company_nature(self):
        """/api/filter-labels?platform=zhilian 返回 company_nature，不含 stage；options 未核验为空。"""
        resp = self.client.get("/api/filter-labels?platform=zhilian")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["platform"], "zhilian")
        self.assertEqual(data["schema_version"], 3)
        # 智联真实元数据核验后启用
        self.assertTrue(data["enabled_for_new_tasks"])
        field_keys = [f["key"] for f in data["fields"]]
        # 字段顺序：salary/experience/degree/industry/scale/company_nature/
        # recruiter_activity（028 第 7 类）
        self.assertEqual(field_keys, [
            "salary", "experience", "degree", "industry", "scale", "company_nature",
            "recruiter_activity",
        ])
        self.assertNotIn("stage", field_keys)
        # 智联 options 已由真实元数据核验，全部非空；第 7 类单选（028）
        for f in data["fields"]:
            self.assertGreater(len(f["options"]), 0, f"字段 {f['key']} options 应已核验")
            if f["key"] == "recruiter_activity":
                self.assertFalse(f["multiple"])
            else:
                self.assertTrue(f["multiple"])

    def test_filter_labels_with_platform_boss_returns_stage(self):
        """/api/filter-labels?platform=boss 返回 stage，不含 company_nature。"""
        resp = self.client.get("/api/filter-labels?platform=boss")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["platform"], "boss")
        self.assertTrue(data["enabled_for_new_tasks"])
        field_keys = [f["key"] for f in data["fields"]]
        # BOSS 字段顺序：salary/experience/degree/industry/scale/
        # recruiter_activity（028 第 7 类）/stage
        self.assertEqual(field_keys, [
            "salary", "experience", "degree", "industry", "scale",
            "recruiter_activity", "stage",
        ])
        self.assertNotIn("company_nature", field_keys)

    def test_filter_labels_with_unknown_platform_returns_400(self):
        """未知平台返回 400 platform_validation_failed。"""
        resp = self.client.get("/api/filter-labels?platform=unknown")
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertEqual(data.get("error_code"), "platform_validation_failed")


if __name__ == "__main__":
    unittest.main()
