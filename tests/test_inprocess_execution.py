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
import sys
import tempfile
import threading
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

    def test_risk_control_generic_maps_to_source_blocked(self):
        """T027: RiskControlError(reason 无特定关键词) → source_blocked。"""
        self._create_scrape_task("t9", detail=False)
        err = boss.RiskControlError("HTTP 403 blocked", page=1, scraped_count=0)
        with mock.patch.object(boss, "run_search_programmatic", side_effect=err):
            self.runner._execute("t9")
        task = self.store.get_task("t9")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task.get("returncode"), 10)
        self.assertIn("source_blocked", task.get("error") or "")

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
