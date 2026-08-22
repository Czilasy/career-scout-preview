"""spec003 tasks004 — 后端 in-process 接线测试（T023/T024/T025/T027）。

冻结合同：specs/003-desktop-exe/contracts/inprocess-runner.md。

覆盖：
- T023: TaskRunner in_process 模式状态机（queued→running→succeeded/failed），
  与子进程模式语义等价（fake run_search_programmatic 注入，不依赖真实 Chrome）；
- T024: in_process 模式 cancel() 不触碰 process、状态 interrupted、已写产物保留；
- T025: WorkbenchRunner in_process 模式流式持久化（on_poll 增量入库，完成前 job 已入库）；
- T027: 异常映射（CDPUnavailable / RiskControl / LoginRequired / SearchCancelled
  → 对应 failure_code / interrupted），映射表冻结为测试断言。
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from scripts import boss_cdp_raw as boss
from webui.app import TaskRunner, WorkbenchRunner
from webui.store import TaskStore


# ===========================================================================
# 辅助
# ===========================================================================
def _make_search(detail=True, **overrides):
    base = {
        "keyword": "AI Agent",
        "city": "上海",
        "pages": 1,
        "detail": detail,
        "analysis": False,
        "format": "json",
        "filters": {},
    }
    base.update(overrides)
    return base


def _write_list_artifact(path, jobs=None):
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"jobs": jobs or []}, ensure_ascii=False), encoding="utf-8")


def _write_detail_artifact(path, details=None):
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(details or [], ensure_ascii=False), encoding="utf-8")


# ===========================================================================
# T023/T024/T027：TaskRunner in_process 模式
# ===========================================================================
class TaskRunnerInProcessTests(unittest.TestCase):
    """in_process 模式状态机、取消、异常映射（合同 §4.1）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.result_dir = self.root / "results"
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self.store = TaskStore(str(self.root / "state" / "webui.db"))
        self.runner = TaskRunner(
            self.store,
            str(self.result_dir),
            sys.executable,
            start_tasks=False,  # 手动驱动 _execute
            execution_mode="in_process",
        )

    def tearDown(self):
        self.temp.cleanup()

    def _create_scrape_task(self, task_id="t1", detail=True):
        search = _make_search(detail=detail)
        return self.store.create_task(
            task_id, "scrape", {"search": search, "profile": {}},
            output_path=str(self.result_dir / f"boss_jobs_{task_id}.json"),
            detail_output_path=str(self.result_dir / f"boss_details_{task_id}.json"),
        )

    # ---- T023: 状态机 ------------------------------------------------

    def test_scrape_succeeds_in_process(self):
        """in_process: queued → running → succeeded，产物校验通过。"""
        self._create_scrape_task("t1", detail=True)

        captured_kwargs = {}

        def fake_run(**kwargs):
            captured_kwargs.update(kwargs)
            _write_list_artifact(kwargs["output_path"], jobs=[{"job_id": "j1"}])
            _write_detail_artifact(
                kwargs["detail_output_path"],
                details=[{"job_id": "j1", "jd": "desc"}],
            )
            return {"list_data": {"jobs": [{"job_id": "j1"}]}, "details": [{"job_id": "j1"}]}

        with mock.patch.object(boss, "run_search_programmatic", side_effect=fake_run) as m:
            self.runner._execute("t1")

        # 参数 dict 直传，不经过 argv 文本往返
        self.assertEqual(m.call_count, 1)
        self.assertEqual(captured_kwargs["keyword"], "AI Agent")
        self.assertEqual(captured_kwargs["city"], "上海")
        self.assertEqual(captured_kwargs["pages"], 1)
        self.assertTrue(captured_kwargs["detail"])
        self.assertEqual(captured_kwargs["output_path"], str(self.result_dir / "boss_jobs_t1.json"))
        self.assertEqual(captured_kwargs["detail_output_path"], str(self.result_dir / "boss_details_t1.json"))
        # on_log / cancel_event 透传
        self.assertTrue(callable(captured_kwargs["on_log"]))
        self.assertIsInstance(captured_kwargs["cancel_event"], threading.Event)

        final = self.store.get_task("t1")
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final.get("returncode"), 0)
        # 日志按行转发（"任务开始" + programmatic 内部 print 转发 + "任务完成"）
        logs = self.store.get_logs("t1")
        log_lines = [entry["line"] for entry in logs]
        self.assertIn("任务开始", log_lines)
        self.assertIn("任务完成", log_lines)

    def test_scrape_no_detail_in_process(self):
        """in_process: detail=False 时不传 detail=True，列表产物校验通过。"""
        self._create_scrape_task("t2", detail=False)

        captured_kwargs = {}

        def fake_run(**kwargs):
            captured_kwargs.update(kwargs)
            _write_list_artifact(kwargs["output_path"], jobs=[{"job_id": "j1"}])
            return {"list_data": {"jobs": [{"job_id": "j1"}]}, "details": None}

        with mock.patch.object(boss, "run_search_programmatic", side_effect=fake_run):
            self.runner._execute("t2")

        self.assertFalse(captured_kwargs["detail"])
        self.assertEqual(self.store.get_task("t2")["status"], "succeeded")

    def test_setup_chrome_succeeds_in_process(self):
        """in_process: setup_chrome 任务调用 boss.run_setup_chrome 库式函数（合同 §6）。"""
        self.store.create_task("setup-abc", "setup_chrome", {})

        with mock.patch.object(boss, "run_setup_chrome", return_value=0) as m:
            self.runner._execute("setup-abc")

        self.assertEqual(m.call_count, 1)
        self.assertEqual(self.store.get_task("setup-abc")["status"], "succeeded")
        self.assertEqual(self.store.get_task("setup-abc").get("returncode"), 0)

    def test_setup_chrome_fails_in_process(self):
        """in_process: setup_chrome 返回非零 → failed。"""
        self.store.create_task("setup-fail", "setup_chrome", {})

        with mock.patch.object(boss, "run_setup_chrome", return_value=1):
            self.runner._execute("setup-fail")

        task = self.store.get_task("setup-fail")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task.get("returncode"), 1)

    def test_subprocess_mode_unchanged(self):
        """execution_mode 默认 subprocess：build_command / ScraperExecutor 路径不变。"""
        runner = TaskRunner(
            self.store, str(self.result_dir), sys.executable,
            start_tasks=False,  # 不显式传 execution_mode
        )
        self.assertEqual(runner.execution_mode, "subprocess")

    # ---- T024: cancel 语义 -------------------------------------------

    def test_cancel_in_process_does_not_touch_process(self):
        """in_process cancel(): 不触碰 process（无 process 可终止）、状态 interrupted、产物保留。"""
        self._create_scrape_task("t3", detail=False)

        # 用 started_event 同步：等 _execute 真正进入 fake_run 后再 cancel，
        # 避免与 _execute 自己创建/注册 cancel_event 的竞态。
        started_event = threading.Event()

        # fake run 在 cancel_event 被 set 后立即返回（模拟 SearchCancelled）
        def fake_run(**kwargs):
            started_event.set()
            # 等待 cancel_event 被 set
            kwargs["cancel_event"].wait(timeout=2)
            _write_list_artifact(kwargs["output_path"], jobs=[{"job_id": "j1"}])
            raise boss.SearchCancelled()

        run_thread = threading.Thread(
            target=lambda: self.runner._execute("t3"), daemon=True,
        )
        with mock.patch.object(boss, "run_search_programmatic", side_effect=fake_run):
            run_thread.start()
            # 确保 _execute 已进入 run_search_programmatic
            self.assertTrue(started_event.wait(timeout=2), "fake_run 应启动")
            # 直接调用 cancel
            self.runner.cancel("t3")
            run_thread.join(timeout=5)

        self.assertFalse(run_thread.is_alive(), "_execute 应在取消后退出")
        final = self.store.get_task("t3")
        self.assertEqual(final["status"], "interrupted")
        # 产物保留（fake_run 在抛 SearchCancelled 前已写）
        list_path = self.result_dir / "boss_jobs_t3.json"
        self.assertTrue(list_path.is_file())
        # cancel 不应注册任何 process（_processes 字典无 t3）
        with self.runner._process_lock:
            self.assertNotIn("t3", self.runner._processes)

    def test_cancel_queued_in_process(self):
        """in_process cancel() 对 queued 任务也生效（无 process 可终止）。"""
        self._create_scrape_task("t4", detail=False)
        # 任务仍在 queued（_execute 未启动）
        self.runner.cancel("t4")
        self.assertEqual(self.store.get_task("t4")["status"], "interrupted")

    # ---- T027: 异常映射（冻结表） ------------------------------------

    def test_cdp_unavailable_maps_to_source_cdp_unavailable(self):
        """T027: CDPUnavailableError → returncode=2, failure_code=source_cdp_unavailable。"""
        self._create_scrape_task("t5", detail=False)
        with mock.patch.object(boss, "run_search_programmatic",
                               side_effect=boss.CDPUnavailableError("cdp down")):
            self.runner._execute("t5")
        task = self.store.get_task("t5")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task.get("returncode"), 2)
        self.assertIn("source_cdp_unavailable", task.get("error") or "")

    def test_login_required_maps_to_source_login_required(self):
        """T027: LoginRequiredError → returncode=1, failure_code=source_login_required。"""
        self._create_scrape_task("t6", detail=False)
        with mock.patch.object(boss, "run_search_programmatic",
                               side_effect=boss.LoginRequiredError("not logged in")):
            self.runner._execute("t6")
        task = self.store.get_task("t6")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task.get("returncode"), 1)
        self.assertIn("source_login_required", task.get("error") or "")

    def test_request_limit_exceeded_maps_to_source_request_limit_exceeded(self):
        """B053: RequestLimitExceededError → returncode=11, failure_code=source_request_limit_exceeded。"""
        self._create_scrape_task("t11", detail=False)
        with mock.patch.object(
            boss, "run_search_programmatic",
            side_effect=boss.RequestLimitExceededError("limit hit"),
        ):
            self.runner._execute("t11")
        task = self.store.get_task("t11")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task.get("returncode"), 11)
        self.assertIn("source_request_limit_exceeded", task.get("error") or "")

    def test_risk_control_rate_limited_maps_correctly(self):
        """T027: RiskControlError(reason 含限流关键词) → source_rate_limited。"""
        self._create_scrape_task("t7", detail=False)
        err = boss.RiskControlError("访问频繁，请稍后再试", page=2, scraped_count=10)
        with mock.patch.object(boss, "run_search_programmatic", side_effect=err):
            self.runner._execute("t7")
        task = self.store.get_task("t7")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task.get("returncode"), 10)
        self.assertIn("source_rate_limited", task.get("error") or "")

    def test_risk_control_verification_maps_correctly(self):
        """T027: RiskControlError(reason 含验证码关键词) → source_verification_required。"""
        self._create_scrape_task("t8", detail=False)
        err = boss.RiskControlError("出现验证码滑块", page=1, scraped_count=0)
        with mock.patch.object(boss, "run_search_programmatic", side_effect=err):
            self.runner._execute("t8")
        task = self.store.get_task("t8")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task.get("returncode"), 10)
        self.assertIn("source_verification_required", task.get("error") or "")

    def test_risk_control_http_403_maps_to_rate_limited(self):
        """T027: RiskControlError(HTTP 403) → source_rate_limited。"""
        self._create_scrape_task("t9", detail=False)
        err = boss.RiskControlError("HTTP 403 blocked", page=1, scraped_count=0)
        with mock.patch.object(boss, "run_search_programmatic", side_effect=err):
            self.runner._execute("t9")
        task = self.store.get_task("t9")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task.get("returncode"), 10)
        self.assertIn("source_rate_limited", task.get("error") or "")

    def test_risk_control_generic_maps_to_unknown_error(self):
        """T027: 无高置信特征的 RiskControlError → source_unknown_error。"""
        self._create_scrape_task("t9b", detail=False)
        err = boss.RiskControlError("unknown reason", page=1, scraped_count=0)
        with mock.patch.object(boss, "run_search_programmatic", side_effect=err):
            self.runner._execute("t9b")
        task = self.store.get_task("t9b")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task.get("returncode"), 10)
        self.assertIn("source_unknown_error", task.get("error") or "")

    def test_search_cancelled_maps_to_interrupted(self):
        """T027: SearchCancelled → interrupted，不落失败码。"""
        self._create_scrape_task("t10", detail=False)
        with mock.patch.object(boss, "run_search_programmatic",
                               side_effect=boss.SearchCancelled()):
            self.runner._execute("t10")
        task = self.store.get_task("t10")
        self.assertEqual(task["status"], "interrupted")
        # interrupted 状态下 error 字段为取消文案，不含 failure_code
        self.assertNotIn("source_", task.get("error") or "")

    def test_generic_exception_maps_to_process_failed(self):
        """未识别异常 → returncode=-1, failure_code=process_failed。"""
        self._create_scrape_task("t11", detail=False)
        with mock.patch.object(boss, "run_search_programmatic",
                               side_effect=RuntimeError("unexpected")):
            self.runner._execute("t11")
        task = self.store.get_task("t11")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task.get("returncode"), -1)
        self.assertIn("process_failed", task.get("error") or "")

    # ---- in-process 超时保护（与子进程 process_timeout 语义对齐） ----

    def test_in_process_timeout_maps_to_process_timeout(self):
        """超过硬超时且不响应协作取消 → failed + process_timeout，不无限挂起。"""
        runner = TaskRunner(
            self.store, str(self.result_dir), sys.executable,
            start_tasks=False, execution_mode="in_process",
            in_process_timeout=0.3,
        )
        self._create_scrape_task("t12", detail=False)

        def fake_run(**kwargs):
            # 不响应 cancel_event，模拟卡死在 CDP 调用
            time.sleep(2)

        with mock.patch.object(boss, "run_search_programmatic", side_effect=fake_run):
            runner._execute("t12")
        task = self.store.get_task("t12")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task.get("returncode"), -1)
        self.assertIn("process_timeout", task.get("error") or "")

    def test_in_process_timeout_collaborative_stop_still_timeout(self):
        """超时后协作取消成功（SearchCancelled）仍按 process_timeout 失败。"""
        runner = TaskRunner(
            self.store, str(self.result_dir), sys.executable,
            start_tasks=False, execution_mode="in_process",
            in_process_timeout=0.3,
        )
        self._create_scrape_task("t13", detail=False)

        def fake_run(**kwargs):
            kwargs["cancel_event"].wait(timeout=2)
            raise boss.SearchCancelled()

        with mock.patch.object(boss, "run_search_programmatic", side_effect=fake_run):
            runner._execute("t13")
        task = self.store.get_task("t13")
        self.assertEqual(task["status"], "failed")
        self.assertIn("process_timeout", task.get("error") or "")


# ===========================================================================
# T025: WorkbenchRunner in_process 流式持久化
# ===========================================================================
class WorkbenchRunnerInProcessTests(unittest.TestCase):
    """in_process 模式下，on_poll 增量入库语义保留（合同 §4.2）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.result_dir = self.root / "results"
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self.store = TaskStore(str(self.root / "state" / "webui.db"))
        self.runner = WorkbenchRunner(
            self.store,
            str(self.result_dir),
            sys.executable,
            start_tasks=False,
            execution_mode="in_process",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_on_poll_passed_to_programmatic(self):
        """in_process: on_poll 闭包透传给 run_search_programmatic，增量入库语义保留。"""
        # 创建一个 search_run + 1 个 child query
        profile = self.store.create_profile("in_process 测试画像")
        profile_snapshot = {"city": "上海"}
        run = self.store.create_search_run(
            profile["id"], profile_snapshot, "ai", total_detail_budget=5,
        )
        run_id = run["id"]
        list_path = str(self.result_dir / f"list_{run_id}_0.json")
        detail_path = str(self.result_dir / f"detail_{run_id}_0.json")
        frozen_query = {
            "keyword": "AI", "city": "上海",
            "filters": {"salary": "403"},
        }
        self.store.create_run_query(run_id, 0, frozen_query, list_path, detail_path, 5)

        captured_kwargs = {}
        poll_calls = []

        def fake_run(**kwargs):
            captured_kwargs.update(kwargs)
            # 模拟抓取过程中调用 on_poll（增量入库）
            for _ in range(2):
                if kwargs.get("on_poll"):
                    kwargs["on_poll"]()
                    poll_calls.append(1)
            # 写最终产物
            _write_list_artifact(kwargs["output_path"], jobs=[{"job_id": "j1", "job_link": "https://www.zhipin.com/job/1"}])
            _write_detail_artifact(kwargs["detail_output_path"], details=[{"job_id": "j1", "jd": "desc", "job_link": "https://www.zhipin.com/job/1"}])
            return {"list_data": {"jobs": [{"job_id": "j1"}]}, "details": [{"job_id": "j1"}]}

        with mock.patch.object(boss, "run_search_programmatic", side_effect=fake_run):
            self.runner._execute_search_run(run_id)

        # on_poll 透传给 programmatic
        self.assertTrue(callable(captured_kwargs.get("on_poll")))
        self.assertEqual(len(poll_calls), 2)
        # query 最终状态为 succeeded
        queries = self.store.list_run_queries(run_id)
        self.assertEqual(queries[0]["status"], "succeeded")

    def test_query_timeout_maps_to_process_timeout(self):
        """child query 超时 → query failed（parent 不卡死，按失败收敛）。"""
        runner = WorkbenchRunner(
            self.store, str(self.result_dir), sys.executable,
            start_tasks=False, execution_mode="in_process",
            in_process_timeout=0.3,
        )
        profile = self.store.create_profile("超时画像")
        run = self.store.create_search_run(
            profile["id"], {"city": "上海"}, "ai", total_detail_budget=5,
        )
        run_id = run["id"]
        list_path = str(self.result_dir / f"list_{run_id}_0.json")
        detail_path = str(self.result_dir / f"detail_{run_id}_0.json")
        self.store.create_run_query(
            run_id, 0, {"keyword": "AI", "city": "上海", "filters": {}},
            list_path, detail_path, 5,
        )

        def fake_run(**kwargs):
            time.sleep(2)

        with mock.patch.object(boss, "run_search_programmatic", side_effect=fake_run):
            runner._execute_search_run(run_id)

        # query 失败收敛为 scrape_failed（与子进程模式失败的持久化语义一致）
        queries = self.store.list_run_queries(run_id)
        self.assertEqual(queries[0]["status"], "failed")
        self.assertEqual(queries[0].get("error_code"), "scrape_failed")


# ===========================================================================
# BossCdpSource in-process 路径（超时 / 输入缺失显式失败 / captured 收集）
# ===========================================================================
class BossCdpSourceInProcessTests(unittest.TestCase):
    """in_process=True 时 source 层行为与子进程模式对齐。"""

    def setUp(self):
        from webui.source import BossCdpSource

        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.source = BossCdpSource(
            python_executable=sys.executable,
            timeout_seconds=600,
            in_process=True,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _detail_command(self):
        return [
            sys.executable, str(self.source.scraper_path),
            "--input", str(self.root / "nope.json"),
            "--detail-output", str(self.root / "out.json"),
            "--max-details", "1",
            "--detail",
        ]

    def test_detail_input_missing_fails_explicitly(self):
        """input 文件缺失 → ValueError（不再静默返回空成功）。"""
        with self.assertRaises(ValueError):
            self.source._read_detail_input(str(self.root / "nope.json"))

    def test_detail_input_invalid_json_fails_explicitly(self):
        """input JSON 非法 → ValueError。"""
        bad = self.root / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.source._read_detail_input(str(bad))

    def test_translate_detail_input_missing_returns_failure(self):
        """完整链路：input 缺失 → 非零失败码 + 错误文本，而非 (0, "")。"""
        code, captured = self.source._run_in_process(self._detail_command(), 5)
        self.assertNotEqual(code, 0)
        self.assertIn("无法读取详情输入文件", captured)

    def test_success_captures_output_tail(self):
        """成功路径 captured 非空（收集 print 输出），与子进程模式对齐。"""
        output_path = self.root / "out.json"
        command = [
            sys.executable, str(self.source.scraper_path),
            "--cdp-port", "9222",
            "--keyword", "AI", "--city", "_", "--pages", "1",
            "--output", str(output_path),
            "--no-detail",
        ]

        def fake_run(**kwargs):
            print("fake progress line")
            out = pathlib.Path(kwargs["output_path"])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"jobs": []}), encoding="utf-8")
            return {"list_data": {"jobs": []}, "details": None}

        with mock.patch.object(boss, "run_search_programmatic", side_effect=fake_run):
            code, captured = self.source._run_in_process(command, 5)
        self.assertEqual(code, 0)
        self.assertIn("fake progress line", captured)

    def test_generic_exception_message_does_not_leak_absolute_path(self):
        """未识别异常返回通用文案，不把绝对路径带进错误输出。"""
        output_path = self.root / "out.json"
        command = [
            sys.executable, str(self.source.scraper_path),
            "--cdp-port", "9222",
            "--keyword", "AI", "--city", "_", "--pages", "1",
            "--output", str(output_path),
            "--no-detail",
        ]

        with mock.patch.object(
            boss, "run_search_programmatic",
            side_effect=RuntimeError(r"C:\secret\browser\path"),
        ):
            code, captured = self.source._run_in_process(command, 5)
        self.assertEqual(code, -1)
        self.assertEqual(captured, "抓取执行失败")
        self.assertNotIn("secret", captured)

    def test_timeout_raises_timeout_expired(self):
        """超时 → subprocess.TimeoutExpired（fetch_* 据此分类为 source_timeout）。"""
        output_path = self.root / "out.json"
        command = [
            sys.executable, str(self.source.scraper_path),
            "--cdp-port", "9222",
            "--keyword", "AI", "--city", "_", "--pages", "1",
            "--output", str(output_path),
            "--no-detail",
        ]

        def fake_run(**kwargs):
            time.sleep(2)

        with mock.patch.object(boss, "run_search_programmatic", side_effect=fake_run):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.source._run_in_process(command, timeout=0.3)


# ===========================================================================
# run_with_deadline（in-process 超时执行器）
# ===========================================================================
class RunWithDeadlineTests(unittest.TestCase):
    """超时执行器语义：正常完成 / 原样抛异常 / 超时置取消并返回 TimeoutError。"""

    def test_completes_before_deadline(self):
        from webui.process_executor import run_with_deadline

        completed, payload = run_with_deadline(lambda: 42, timeout_seconds=5)
        self.assertTrue(completed)
        self.assertEqual(payload, 42)

    def test_original_exception_is_re_raised(self):
        from webui.process_executor import run_with_deadline

        def boom():
            raise ValueError("boom")

        with self.assertRaises(ValueError):
            run_with_deadline(boom, timeout_seconds=5)

    def test_timeout_sets_cancel_event_and_returns_timeout_error(self):
        from webui.process_executor import run_with_deadline

        cancel = threading.Event()
        started = threading.Event()

        def slow():
            started.set()
            cancel.wait(timeout=3)
            raise boss.SearchCancelled()

        completed, payload = run_with_deadline(
            slow, timeout_seconds=0.3, cancel_event=cancel,
        )
        self.assertFalse(completed)
        self.assertIsInstance(payload, TimeoutError)
        self.assertTrue(cancel.is_set())


# ===========================================================================
# 风控 reason 兜底分类器词表（RiskControlError 缺 code 时的防御路径，016）
# ===========================================================================
class RiskControlClassifierTests(unittest.TestCase):
    """_classify_risk_control_reason 对对齐后的词表命中正确。"""

    def test_rate_limit_keywords_aligned(self):
        from webui.task_runners import _classify_risk_control_reason

        for text in ("访问受限", "检测到异常流量", "操作频繁", "账号受限", "429 too many"):
            self.assertEqual(
                _classify_risk_control_reason(text), "source_rate_limited", text,
            )

    def test_http_status_and_unlock_time_aligned(self):
        from webui.task_runners import _classify_risk_control_reason
        for text in (
            "列表接口返回 HTTP 403（被风控拦截）", "HTTP 412", "HTTP 418",
            "账号将于 2099-08-05 18:30 解封",
        ):
            self.assertEqual(
                _classify_risk_control_reason(text), "source_rate_limited", text,
            )

    def test_common_words_are_not_rate_limited(self):
        from webui.task_runners import _classify_risk_control_reason
        for text in ("登录解锁更多职位", "频繁更新职位", "冻结岗位"):
            self.assertEqual(
                _classify_risk_control_reason(text), "source_unknown_error", text,
            )

    def test_verification_keywords_aligned(self):
        from webui.task_runners import _classify_risk_control_reason

        for text in ("需要滑动滑块验证", "出现 captcha", "slider verify", "geetest 校验"):
            self.assertEqual(
                _classify_risk_control_reason(text), "source_verification_required", text,
            )

    def test_login_keywords_aligned(self):
        from webui.task_runners import _classify_risk_control_reason

        for text in ("请先登录", "未登录", "登 录 失效", "wt2 参数错误", "401 unauthorized"):
            self.assertEqual(
                _classify_risk_control_reason(text), "source_login_required", text,
            )

    def test_unmatched_reason_maps_to_unknown_error(self):
        from webui.task_runners import _classify_risk_control_reason

        self.assertEqual(_classify_risk_control_reason(""), "source_unknown_error")
        self.assertEqual(_classify_risk_control_reason("unknown reason"), "source_unknown_error")


# ===========================================================================
# 线程感知 stdout buffer（并发日志不串线）
# ===========================================================================
class ThreadAwareStdoutTests(unittest.TestCase):
    """_LineLogBuffer 只捕获任务线程输出，其他线程转发到 fallback。"""

    def test_other_thread_output_forwards_to_fallback(self):
        lines = []
        buf = boss._LineLogBuffer(lines.append)
        with buf:
            print("main thread line")
            other = threading.Thread(
                target=lambda: print("other thread line"), daemon=True,
            )
            other.start()
            other.join()
        self.assertIn("main thread line", lines)
        self.assertNotIn("other thread line", lines)

    def test_guarded_restore_restores_previous_stdout(self):
        original = sys.stdout
        buf = boss._LineLogBuffer(lambda line: None)
        with buf:
            self.assertIs(sys.stdout, buf)
        self.assertIs(sys.stdout, original)

    def test_capture_collects_only_task_thread(self):
        from webui.source import _InProcessCapture

        capture = _InProcessCapture(max_bytes=1024)
        with capture:
            print("task line")
            other = threading.Thread(
                target=lambda: print("foreign line"), daemon=True,
            )
            other.start()
            other.join()
        tail = capture.tail()
        self.assertIn("task line", tail)
        self.assertNotIn("foreign line", tail)


# ===========================================================================
# execution_mode 默认值
# ===========================================================================
class ExecutionModeDefaultTests(unittest.TestCase):
    """execution_mode 默认 subprocess（合同 §4.1）。"""

    def test_default_is_subprocess(self):
        store = TaskStore.__new__(TaskStore)  # 不触发 DB 初始化
        runner = TaskRunner(store, "/tmp", sys.executable, start_tasks=False)
        self.assertEqual(runner.execution_mode, "subprocess")

    def test_in_process_explicit(self):
        store = TaskStore.__new__(TaskStore)
        runner = TaskRunner(store, "/tmp", sys.executable, start_tasks=False,
                            execution_mode="in_process")
        self.assertEqual(runner.execution_mode, "in_process")


if __name__ == "__main__":
    unittest.main()
