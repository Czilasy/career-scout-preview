"""PipelineGuard 卡死防护测试（022-jd-stall-guard，US1/US2/US3）。

覆盖：
- US1：批次登记/心跳/完成生命周期；无心跳判定卡死；有心跳不误杀；
      卡死杀失联抓取工、任务线程侧自动重抓编排（attempt 1→2→3）。
- US2：第 3 次失败分流——环境级 → 暂停+错误码；偶发 → 跳过；线程
      不解出兜底 → paused + "请重启应用"提示。
- US3：卡死/重试/放弃/分流事件行写入 career-scout.log。
- fetch_job_details 接入后的端到端重抓行为（注入假卡死，验证自动
  重抓并最终完成该批，不依赖真实浏览器）。
"""

import logging
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from webui.pipeline_guard import PipelineGuard
from webui.source_breaker import SourceOutcome

STALL = 0.15        # 测试用短卡死阈值（秒）
POLL = 0.03         # 监控扫描间隔（秒）
FALLBACK = 0.2      # 兜底暂停延迟（秒）
RETRY_RANGE = (0.01, 0.02)


class _FakeStore:
    def __init__(self):
        self.events = []
        self.pending = []

    def append_task_event(self, task_id, name, payload):
        self.events.append((task_id, name, payload))

    def get_pending_result(self, run_id, job_id):
        return None

    def insert_pending_result(self, run_id, job_id, **kwargs):
        self.pending.append((run_id, job_id, kwargs))


class _FakeCtx:
    def __init__(self):
        self.tasks = {}
        self.lock = threading.RLock()
        self.store = _FakeStore()
        self.write_run_calls = []
        self.pause_failures = []
        self.release_calls = []

    def write_run(self, task_id, **kwargs):
        self.write_run_calls.append((task_id, kwargs))

    def record_pause_failure(self, task_id, stage, code, reason, **kwargs):
        self.pause_failures.append((task_id, stage, code, reason))

    def release_worker_resume_claims(self, task):
        self.release_calls.append(task)


class _FakeProcess:
    def __init__(self, poll_value=None):
        self._poll = poll_value

    def poll(self):
        return self._poll


class _FakeWhitebox:
    def __init__(self):
        self.facts = []

    def record_for_owner(self, owner_kind, owner_id, fact):
        self.facts.append((owner_kind, owner_id, fact))
        return True


def _make_guard(ctx, **overrides):
    kwargs = dict(
        write_run=ctx.write_run,
        store=ctx.store,
        tasks=ctx.tasks,
        lock=ctx.lock,
        record_pause_failure=ctx.record_pause_failure,
        release_worker_resume_claims=ctx.release_worker_resume_claims,
        stall_seconds=STALL,
        poll_seconds=POLL,
        fallback_seconds=FALLBACK,
        retry_delay_range=RETRY_RANGE,
    )
    kwargs.update(overrides)
    return PipelineGuard(**kwargs)


class PipelineGuardCoreTests(unittest.TestCase):
    """guard 单例核心：生命周期 / 卡死判定 / 心跳 / kill / 重抓 / 分流 / 兜底。"""

    def setUp(self):
        self.ctx = _FakeCtx()
        self.guard = _make_guard(self.ctx)

    def tearDown(self):
        self.guard.close()

    # ---- US1 生命周期 ----

    def test_batch_lifecycle_begin_touch_complete(self):
        self.guard.begin_batch("b1", task_id="t1", attempt=1)
        self.guard.touch("b1")
        st = self.guard.batch_state("b1")
        self.assertFalse(st["terminal"])
        self.guard.complete_batch("b1")
        st = self.guard.batch_state("b1")
        self.assertTrue(st["terminal"])

    def test_stall_detected_without_heartbeat(self):
        self.guard.begin_batch("b1", task_id="t1", attempt=1)
        time.sleep(STALL + 0.05)
        self.guard.scan_once()
        self.assertTrue(self.guard.is_stalled("b1"))

    def test_heartbeat_prevents_stall(self):
        self.guard.begin_batch("b1", task_id="t1", attempt=1)
        deadline = time.monotonic() + STALL * 2.0
        while time.monotonic() < deadline:
            self.guard.touch("b1")
            time.sleep(STALL * 0.15)
            self.guard.scan_once()
        self.assertFalse(self.guard.is_stalled("b1"))

    def test_stall_kills_registered_worker_process(self):
        proc = _FakeProcess(poll_value=None)
        self.guard.begin_batch("b1", task_id="t1", attempt=1)
        self.guard.spawn_hook("b1")(proc)
        with mock.patch(
            "webui.pipeline_guard.ScraperExecutor._terminate_tree"
        ) as kill:
            time.sleep(STALL + 0.05)
            self.guard.scan_once()
        kill.assert_called_once()
        self.assertTrue(self.guard.is_stalled("b1"))

    def test_retry_orchestration_attempt_increments_and_resets_stall(self):
        self.guard.begin_batch("b1", task_id="t1", attempt=1)
        time.sleep(STALL + 0.05)
        self.guard.scan_once()
        self.assertTrue(self.guard.is_stalled("b1"))
        self.assertTrue(self.guard.should_retry("b1"))
        self.assertFalse(self.guard.should_giveup("b1"))
        delay = self.guard.next_retry_delay()
        self.assertGreaterEqual(delay, RETRY_RANGE[0])
        self.assertLessEqual(delay, RETRY_RANGE[1])
        # 任务线程重抓：新 attempt 重置卡死标记与计时
        self.guard.begin_batch("b1", task_id="t1", attempt=2)
        self.assertFalse(self.guard.is_stalled("b1"))
        self.assertFalse(self.guard.should_retry("b1"))

    def test_many_attempts_never_exceed_max(self):
        self.guard.begin_batch("b1", task_id="t1", attempt=3)
        time.sleep(STALL + 0.05)
        self.guard.scan_once()
        self.assertTrue(self.guard.is_stalled("b1"))
        self.assertFalse(self.guard.should_retry("b1"))
        self.assertTrue(self.guard.should_giveup("b1"))

    # ---- US2 分流 ----

    def test_third_failure_env_probe_failed_diverts_environment(self):
        def env_probe():
            return (False, "source_cdp_unavailable", "调试浏览器未就绪")

        self.guard.begin_batch("b1", task_id="t1", attempt=3, env_probe=env_probe)
        time.sleep(STALL + 0.05)
        self.guard.scan_once()
        self.assertTrue(self.guard.should_giveup("b1"))
        self.assertEqual(self.guard.divert_result("b1"), "environment")
        self.assertEqual(self.guard.stall_code("b1"), "source_cdp_unavailable")

    def test_third_failure_env_probe_passed_diverts_sporadic(self):
        def env_probe():
            return (True, "", "")

        self.guard.begin_batch("b1", task_id="t1", attempt=3, env_probe=env_probe)
        time.sleep(STALL + 0.05)
        self.guard.scan_once()
        self.assertTrue(self.guard.should_giveup("b1"))
        self.assertEqual(self.guard.divert_result("b1"), "sporadic")

    def test_third_failure_env_probe_exception_diverts_environment(self):
        def env_probe():
            raise RuntimeError("probe boom")

        self.guard.begin_batch("b1", task_id="t1", attempt=3, env_probe=env_probe)
        time.sleep(STALL + 0.05)
        self.guard.scan_once()
        self.assertEqual(self.guard.divert_result("b1"), "environment")

    def test_unresponsive_thread_fallback_pauses_task(self):
        self.ctx.tasks["t1"] = {"status": "running"}
        self.guard.begin_batch("b1", task_id="t1", attempt=1)
        time.sleep(STALL + 0.05)
        self.guard.scan_once()
        self.assertTrue(self.guard.should_retry("b1"))
        # 任务线程不来处理：不 complete、不 begin 新 attempt（真死锁）
        time.sleep(FALLBACK + 0.1)
        self.guard.scan_once()
        paused = [c for c in self.ctx.write_run_calls
                  if c[1].get("status") == "paused"]
        self.assertTrue(paused, "兜底必须暂停任务")
        self.assertIn("任务线程失去响应", paused[-1][1]["error_reason"])
        self.assertEqual(self.ctx.tasks["t1"]["status"], "paused")

    # ---- US3 事件日志 ----

    def test_stall_retry_giveup_events_written_to_log(self):
        from webui.logging_setup import configure_logging, get_logger
        with tempfile.TemporaryDirectory() as tmp:
            configure_logging(tmp, force=True)
            try:
                guard = _make_guard(self.ctx)
                guard.begin_batch("b1", task_id="t1", attempt=1)
                time.sleep(STALL + 0.05)
                guard.scan_once()
                guard.begin_batch("b1", task_id="t1", attempt=2)
                time.sleep(STALL + 0.05)
                guard.scan_once()
                guard.close()
            finally:
                for handler in list(logging.getLogger("career_scout").handlers):
                    logging.getLogger("career_scout").removeHandler(handler)
                    try:
                        handler.close()
                    except Exception:
                        pass
            content = Path(tmp, "career-scout.log").read_text(encoding="utf-8")
            self.assertIn("stall", content)
            self.assertIn("retry", content)
            self.assertIn("batch=b1", content)
            self.assertIn("attempt", content)

    def test_whitebox_guard_events_keep_task_unit_and_attempt_context(self):
        """033 V2 T035：卡住/重试事件必须可定位到任务、阶段、单元和尝试。"""
        whitebox = _FakeWhitebox()
        guard = _make_guard(self.ctx, whitebox=whitebox)
        try:
            guard.begin_batch("detail-batch", task_id="task-1", attempt=1)
            time.sleep(STALL + 0.05)
            guard.scan_once()
            guard.begin_batch("detail-batch", task_id="task-1", attempt=2)
            for _owner, owner_id, fact in whitebox.facts:
                self.assertEqual(owner_id, "task-1")
                self.assertEqual(fact["stage"], "jd_detail")
                self.assertEqual(fact["unit_key"], "detail-batch")
                self.assertGreaterEqual(fact["attempt_no"], 1)
                self.assertEqual(fact["payload"]["task_id"], "task-1")
        finally:
            guard.close()


class FetchJobDetailsGuardTests(unittest.TestCase):
    """fetch_job_details 接入 guard：注入假卡死 → 自动重抓 → 批次完成（US1 验收）。"""

    def setUp(self):
        self.ctx = _FakeCtx()
        self.guard = _make_guard(self.ctx)

    def tearDown(self):
        self.guard.close()

    def test_stalled_batch_is_retried_and_completes(self):
        from webui.pipeline_exec_details import fetch_job_details

        calls = {"n": 0}

        class _FakeSource:
            platform = "boss"
            cdp_port = 9222

            def __init__(self):
                self._executor = mock.Mock()

            def fetch_details_batch(self, jobs, **kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    # 第一次：失联（超过卡死阈值才返回，模拟被 kill 后返回空结果）
                    time.sleep(STALL + 0.1)
                    return {
                        str(job.get("job_id")): SourceOutcome.failure(
                            failed_code="source_timeout", safe_log="stalled",
                        )
                        for job in jobs
                    }
                # 第二次：正常返回
                return {
                    str(job.get("job_id")): SourceOutcome.success(
                        detail={"jd": f"JD-{job.get('job_id')}"},
                        safe_log="ok",
                    )
                    for job in jobs
                }

        jobs = [
            {"platform_job_id": "1", "job_id": "1", "title": "A"},
            {"platform_job_id": "2", "job_id": "2", "title": "B"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            result = fetch_job_details(
                jobs, _FakeSource(),
                artifact_dir=tmp,
                execution_config=SimpleNamespace(
                    detail_batch_size=5, detail_interval=0,
                    detail_reset_every=0, detail_batch_cooldown=0,
                    detail_tab_pool_size=1,
                ),
                guard=self.guard,
                batch_key_prefix="jd-t1-0",
            )
        self.assertEqual(calls["n"], 2, "卡死批必须自动重抓一次")
        self.assertEqual(result["fetched"], 2)
        jds = {j["job_id"]: j["jd"] for j in result["jobs"]}
        self.assertEqual(jds["1"], "JD-1")
        self.assertEqual(jds["2"], "JD-2")
        st = self.guard.batch_state("jd-t1-0:0")
        self.assertEqual(st["attempt"], 2)

    def test_batch_registers_real_run_id_not_batch_key_prefix(self):
        """回归：guard 批次登记的 task_id 必须是真 run_id，而非 batch_key_prefix。

        历史 bug：pipeline_exec_details 把 task_id 传成 ``jd-<runid>-<chunk>``，
        导致卡死兜底 _pause_task 用错误 id 写 screening_runs 抛 KeyError、任务
        永久悬死；immediate_stop_task(run_id) 也匹配不到批次。修复后 begin_batch
        必须收到真正的 run_id。
        """
        from webui.pipeline_exec_details import fetch_job_details
        run_id = "REAL-RUN-ID-123"

        class _FakeSource:
            platform = "boss"
            cdp_port = 9222

            def __init__(self):
                self._executor = mock.Mock()

            def fetch_details_batch(self, jobs, **kwargs):
                return {
                    str(job.get("job_id")): SourceOutcome.success(
                        detail={"jd": "x"}, safe_log="ok",
                    )
                    for job in jobs
                }

        jobs = [
            {"platform_job_id": "1", "job_id": "1", "title": "A"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            fetch_job_details(
                jobs, _FakeSource(),
                artifact_dir=tmp,
                execution_config=SimpleNamespace(
                    detail_batch_size=5, detail_interval=0,
                    detail_reset_every=0, detail_batch_cooldown=0,
                    detail_tab_pool_size=1,
                ),
                guard=self.guard,
                batch_key_prefix=f"jd-{run_id}-30",
                task_id=run_id,
            )
        st = self.guard.batch_state(f"jd-{run_id}-30:0")
        self.assertIsNotNone(st, "批次应已被 guard 登记")
        self.assertEqual(st["task_id"], run_id,
                         "guard 批次 task_id 必须是真 run_id，不得是 jd- 前缀的批次键")


if __name__ == "__main__":
    unittest.main()
