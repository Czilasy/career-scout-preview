"""批中暂停/断点保全测试（025-screen-pause-round-reset，US3 B077 + US1 B076）。

覆盖：
- US3 T001：批返回后结果处理提前——卡死重抓全失败（giveup）时，抢救出的已抓
      不再被重抓分支丢弃（并入 jd_by_idx 保全）。
- US3 T002：卡死重抓剔除——已抓成功岗位从重抓列表剔除、只重抓缺失、重抓用
      新产物文件、结果合并不重复；剔除后为空（卡死前已全部抓完）不重抓。
- US3 T003：批返回窗口普通停止（stop_event 置位、非 immediate）→ 已处理并入
      的已抓保全 → 返回 stopped=True 且结果含已抓。
- US3 T004：run_jd_stage stopped 路径——jd_map 非空时落盘断点、为空时不写
      （绝不写空断点）；断点保留已抓 JD。
- US3 T005：卡死防护判定参数未变（300s/3 次/分流，022 冻结语义保持）。
- US1 T010：暂停 API mode=immediate——任务转 paused（非 cancelled）、guard
      批次登记清理、活动批子进程被终止；mode 缺省 = graceful（现状行为）。
- US1 T011：immediate 幂等——已 paused 任务再调 immediate → ok 不 409；
      已 immediate 再调不报错。
"""

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from webui.app import create_app
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


def _exec_config():
    return SimpleNamespace(
        detail_batch_size=5, detail_interval=0,
        detail_reset_every=0, detail_batch_cooldown=0,
        detail_tab_pool_size=1,
    )


class _BaseFakeSource:
    """测试替身 source：环境探测通过（第 3 次卡死分流为 sporadic）。"""

    platform = "boss"
    cdp_port = 9222

    def __init__(self):
        self._executor = mock.Mock()

    def preflight(self):
        return SimpleNamespace(ok=True)


class FetchJobDetailsRescueTests(unittest.TestCase):
    """US3：批返回后立即处理结果，抢救的已抓不再被重抓分支丢弃。"""

    def setUp(self):
        self.ctx = _FakeCtx()
        self.guard = _make_guard(self.ctx)
        self.stop_event = threading.Event()

    def tearDown(self):
        self.guard.close()

    def _run_fetch(self, source, jobs):
        from webui.pipeline_exec_details import fetch_job_details
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                        return_value=(True, "")):
            return fetch_job_details(
                jobs, source,
                artifact_dir=tmp,
                stop_event=self.stop_event,
                execution_config=_exec_config(),
                guard=self.guard,
                batch_key_prefix="jd-t1-0",
            )

    @staticmethod
    def _make_source():
        return _BaseFakeSource()

    def test_rescued_jobs_kept_even_when_all_retries_fail(self):
        """T001：重抓全失败（第 3 次 giveup）时，抢救出的已抓仍保全。"""
        calls = {"n": 0}

        class _FakeSource(_BaseFakeSource):

            def fetch_details_batch(self, jobs, **kwargs):
                calls["n"] += 1
                # 每次都"失联"（超过卡死阈值才返回）→ 每轮都被判卡死
                time.sleep(STALL + 0.1)
                return {
                    "1": SourceOutcome.success(
                        detail={"jd": "JD-1-rescued"}, safe_log="rescued_partial",
                    ),
                    "2": SourceOutcome.failure(
                        failed_code="source_timeout", safe_log="stalled",
                    ),
                }

        jobs = [
            {"platform_job_id": "1", "job_id": "1", "title": "A"},
            {"platform_job_id": "2", "job_id": "2", "title": "B"},
        ]
        result = self._run_fetch(_FakeSource(), jobs)
        # 第 1 次原始 + 2 次重试 = 3 次，之后 giveup 分流（env_probe=None → sporadic）
        self.assertEqual(calls["n"], 3)
        self.assertEqual(result["stall_divert"], "sporadic")
        jds = {j["job_id"]: j.get("jd", "") for j in result["jobs"]}
        self.assertEqual(jds["1"], "JD-1-rescued", "抢救出的已抓必须保全，不得被重抓分支丢弃")
        self.assertEqual(jds["2"], "", "缺失岗位保持失败")

    def test_retry_skips_already_fetched_and_uses_new_artifact(self):
        """T002：卡死重抓剔除已抓成功岗位、只重抓缺失、重抓用新产物文件。"""
        calls = {"n": 0}
        paths = []
        second_job_ids = []

        class _FakeSource(_BaseFakeSource):

            def fetch_details_batch(self, jobs, **kwargs):
                calls["n"] += 1
                paths.append(kwargs.get("detail_output_path"))
                if calls["n"] == 1:
                    time.sleep(STALL + 0.1)  # 失联 → 判卡死
                    return {
                        "1": SourceOutcome.success(
                            detail={"jd": "JD-1-rescued"}, safe_log="rescued_partial",
                        ),
                        "2": SourceOutcome.failure(
                            failed_code="source_timeout", safe_log="stalled",
                        ),
                    }
                second_job_ids.append([j.get("job_id") for j in jobs])
                return {
                    "2": SourceOutcome.success(
                        detail={"jd": "JD-2"}, safe_log="ok",
                    ),
                }

        jobs = [
            {"platform_job_id": "1", "job_id": "1", "title": "A"},
            {"platform_job_id": "2", "job_id": "2", "title": "B"},
        ]
        result = self._run_fetch(_FakeSource(), jobs)
        self.assertEqual(calls["n"], 2, "重抓只应发生一次")
        self.assertEqual(second_job_ids, [["2"]], "已抓成功的岗位不得重复抓")
        self.assertNotEqual(paths[0], paths[1], "重抓必须使用新产物文件，不得覆盖已抓产物")
        self.assertEqual(result["fetched"], 2)
        jds = {j["job_id"]: j.get("jd", "") for j in result["jobs"]}
        self.assertEqual(jds["1"], "JD-1-rescued")
        self.assertEqual(jds["2"], "JD-2")

    def test_no_retry_when_all_rescued_before_stall_detected(self):
        """T002 子场景：卡死前该批已全部抓完（抢救全成功）→ 剔除后为空，不重抓。"""
        calls = {"n": 0}

        class _FakeSource(_BaseFakeSource):

            def fetch_details_batch(self, jobs, **kwargs):
                calls["n"] += 1
                time.sleep(STALL + 0.1)  # 失联 → 判卡死，但产物已全抓
                return {
                    str(j.get("job_id")): SourceOutcome.success(
                        detail={"jd": f"JD-{j.get('job_id')}"},
                        safe_log="rescued_partial",
                    )
                    for j in jobs
                }

        jobs = [
            {"platform_job_id": "1", "job_id": "1", "title": "A"},
            {"platform_job_id": "2", "job_id": "2", "title": "B"},
        ]
        result = self._run_fetch(_FakeSource(), jobs)
        self.assertEqual(calls["n"], 1, "全部已抓则无需重抓")
        self.assertEqual(result["fetched"], 2)
        jds = {j["job_id"]: j.get("jd", "") for j in result["jobs"]}
        self.assertEqual(jds["1"], "JD-1")
        self.assertEqual(jds["2"], "JD-2")

    def test_stop_at_batch_return_preserves_rescued_jobs(self):
        """T003：批返回窗口普通停止（非 immediate）→ 已并入的已抓保全、返回 stopped。"""
        calls = {"n": 0}
        stop_event = self.stop_event

        class _FakeSource(_BaseFakeSource):

            def fetch_details_batch(self, jobs, **kwargs):
                calls["n"] += 1
                # 批返回瞬间用户暂停（普通暂停，非立即停止）
                stop_event.set()
                return {
                    "1": SourceOutcome.success(
                        detail={"jd": "JD-1-rescued"}, safe_log="rescued_partial",
                    ),
                    "2": SourceOutcome.failure(
                        failed_code="source_timeout", safe_log="stalled",
                    ),
                }

        jobs = [
            {"platform_job_id": "1", "job_id": "1", "title": "A"},
            {"platform_job_id": "2", "job_id": "2", "title": "B"},
        ]
        result = self._run_fetch(_FakeSource(), jobs)
        self.assertTrue(result["stopped"])
        jds = {j["job_id"]: j.get("jd", "") for j in result["jobs"]}
        self.assertEqual(jds["1"], "JD-1-rescued", "普通停止必须保全已抓结果")
        self.assertEqual(jds["2"], "")


class RunJdStageCheckpointTests(unittest.TestCase):
    """US3 T004：run_jd_stage stopped 路径——有已抓才落盘断点、绝不写空。"""

    def _make_ctx(self):
        ctx = _FakeCtx()
        ctx.app = SimpleNamespace(config={"RESULT_DIR": "RESULT_DIR"})
        ctx.make_cdp_source = mock.Mock(return_value=SimpleNamespace())
        ctx.persist_jd_job_failures = mock.Mock()
        return ctx

    def _run_stage(self, ctx, resume_jd, survivor_job_ids):
        from webui.runners.ai_screen_jd import run_jd_stage
        jd_path = str(Path(tempfile.mkdtemp()) / "checkpoint.json")
        save_jd_checkpoint = mock.Mock()
        handle_user_stop = mock.Mock()
        survivors = [{"job_id": jid, "title": f"J{jid}"} for jid in survivor_job_ids]
        with mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                        return_value=(True, "")) as _chrome, \
             mock.patch("webui.pipeline_exec.fetch_job_details",
                        return_value={"jobs": [], "stopped": True}) as _fetch, \
             mock.patch("webui.pipeline_exec.close_debug_chrome") as _close:
            run_jd_stage(
                ctx, "t1",
                enriched=[], survivors=survivors,
                resume_jd=resume_jd, jd_path=jd_path,
                frozen_platform="boss", frozen_cdp_port=9222,
                frozen_profile_key="pk", frozen_browser_account="ba",
                execution_config=SimpleNamespace(detail_batch_size=5),
                stop_event=threading.Event(),
                emit=lambda **kw: None,
                stop_requested=lambda: False,
                handle_user_stop=handle_user_stop,
                save_jd_checkpoint=save_jd_checkpoint,
            )
        return save_jd_checkpoint, handle_user_stop

    def test_stopped_with_existing_jd_persists_checkpoint(self):
        """jd_map 非空（resume_jd 有已抓）→ 暂停返回前落盘断点保全。"""
        ctx = self._make_ctx()
        save_jd_checkpoint, handle_user_stop = self._run_stage(
            ctx, {"1": "already-fetched-jd"}, survivor_job_ids=["2"])
        handle_user_stop.assert_called_once()
        save_jd_checkpoint.assert_called_once()
        args = save_jd_checkpoint.call_args[0]
        self.assertEqual(args[1].get("1"), "already-fetched-jd",
                         "断点必须保留已抓 JD")

    def test_stopped_with_empty_jd_does_not_write_empty_checkpoint(self):
        """jd_map 为空 → 暂停返回前绝不写空断点。"""
        ctx = self._make_ctx()
        save_jd_checkpoint, handle_user_stop = self._run_stage(
            ctx, {}, survivor_job_ids=["1"])
        handle_user_stop.assert_called_once()
        save_jd_checkpoint.assert_not_called()


class StallGuardSemanticsTests(unittest.TestCase):
    """US3 T005：卡死防护判定参数未变（022 冻结语义保持）。"""

    def test_default_stall_parameters_unchanged(self):
        ctx = _FakeCtx()
        guard = PipelineGuard(
            write_run=ctx.write_run, store=ctx.store, tasks=ctx.tasks,
            lock=ctx.lock,
            record_pause_failure=ctx.record_pause_failure,
            release_worker_resume_claims=ctx.release_worker_resume_claims,
        )
        try:
            self.assertEqual(guard._stall_seconds, 300, "300 秒无动静判定卡死（022 冻结）")
            self.assertEqual(guard._max_attempts, 3, "原始 1 + 重试 2 = 3（022 冻结）")
            self.assertAlmostEqual(guard._retry_min, 3.0)
            self.assertAlmostEqual(guard._retry_max, 5.0)
        finally:
            guard.close()

    def test_judgment_methods_intact(self):
        # 判定/监控方法必须原样存在（本次零改动）
        for name in ("scan_once", "_mark_stalled", "_divert",
                     "_maybe_fallback_pause", "_monitor_loop"):
            self.assertTrue(hasattr(PipelineGuard, name), name)


class ApiPauseImmediateTests(unittest.TestCase):
    """US1 T010/T011：暂停 API mode=immediate（批中立即停止）与幂等。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        session = self.client.get("/api/session")
        self.token = session.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        self.tasks = self.app.config["PIPELINE_TASKS"]
        self.ctx = self.app.config["PIPELINE_CONTEXT"]

    def tearDown(self):
        self.temp.cleanup()

    def _seed_screen_task(self, task_id, status="running"):
        self.tasks[task_id] = {
            "kind": "ai_screen", "status": status, "progress": {},
            "logs": [], "result": None, "error": "",
            "source_task_id": "scrape-1",
            "started_at": 1000, "finished_at": None,
            "stop_event": threading.Event(),
        }
        return self.tasks[task_id]

    def test_pause_immediate_marks_and_cleans_guard_batches(self):
        """T010：mode=immediate——任务停止信号置位、立即标记、guard 批次清理。"""
        task = self._seed_screen_task("ai-immediate-1")
        guard = self.ctx.pipeline_guard
        with mock.patch.object(guard, "immediate_stop_task", create=True) as stop:
            resp = self.client.post(
                "/api/task/pause/ai-immediate-1", json={"mode": "immediate"})
        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["ok"])
        stop.assert_called_once_with("ai-immediate-1")
        self.assertTrue(task["stop_event"].is_set())
        self.assertTrue(getattr(task["stop_event"], "immediate", False),
                        "立即停止必须携带 immediate 信号（fetch_job_details 据此作废当前批）")
        self.assertEqual(task["stop_mode"], "pause", "立即停止仍落「暂停」语义，非取消")

    def test_pause_default_mode_is_graceful(self):
        """T010：mode 缺省 = graceful（现状行为，不产生 immediate 信号）。"""
        task = self._seed_screen_task("ai-graceful-1")
        guard = self.ctx.pipeline_guard
        with mock.patch.object(guard, "immediate_stop_task", create=True) as stop:
            resp = self.client.post("/api/task/pause/ai-graceful-1", json={})
        self.assertEqual(resp.status_code, 200)
        stop.assert_not_called()
        self.assertTrue(task["stop_event"].is_set())
        self.assertFalse(getattr(task["stop_event"], "immediate", False))
        self.assertEqual(task["stop_mode"], "pause")

    def test_pause_immediate_idempotent_when_already_paused(self):
        """T011：已 paused 任务再调 immediate → ok 不 409。"""
        task = self._seed_screen_task("ai-immediate-2", status="paused")
        task["stop_event"].set()
        resp = self.client.post(
            "/api/task/pause/ai-immediate-2", json={"mode": "immediate"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])

    def test_pause_immediate_idempotent_on_second_call(self):
        """T011：已 immediate 标记再调 → 不报错、guard 不重复清理。"""
        task = self._seed_screen_task("ai-immediate-3")
        guard = self.ctx.pipeline_guard
        with mock.patch.object(guard, "immediate_stop_task", create=True) as stop:
            r1 = self.client.post(
                "/api/task/pause/ai-immediate-3", json={"mode": "immediate"})
            r2 = self.client.post(
                "/api/task/pause/ai-immediate-3", json={"mode": "immediate"})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.get_json()["ok"])
        self.assertEqual(stop.call_count, 1, "第二次 immediate 不再重复清理")
        self.assertTrue(task["stop_event"].is_set())


class CooldownSegmentTests(unittest.TestCase):
    """US2 T022：批间冷却分段响应停止信号（批间暂停不用干等冷却结束）。"""

    def test_cooldown_aborts_early_when_stop_event_set(self):
        from webui.pipeline_exec_details import fetch_job_details
        stop_event = threading.Event()
        sleeps = []

        def _fake_sleep(seconds):
            sleeps.append(seconds)
            # 第一批完成后的冷却 sleep 期间置暂停信号
            stop_event.set()

        class _FakeSource(_BaseFakeSource):
            def fetch_details_batch(self, jobs, **kwargs):
                return {
                    str(j.get("job_id")): SourceOutcome.success(
                        detail={"jd": "x"}, safe_log="ok")
                    for j in jobs
                }

        jobs = [{"job_id": str(i), "platform_job_id": str(i)}
                for i in range(3)]
        config = SimpleNamespace(
            detail_batch_size=1, detail_interval=0,
            detail_reset_every=0, detail_batch_cooldown=30,
            detail_tab_pool_size=1,
        )
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("webui.pipeline_exec_details.time.sleep",
                           side_effect=_fake_sleep):
            result = fetch_job_details(
                jobs, _FakeSource(), artifact_dir=tmp,
                stop_event=stop_event,
                execution_config=config,
                batch_key_prefix="jd-t1-0",
            )
        self.assertTrue(result["stopped"], "冷却期间暂停必须提前停止")
        self.assertLess(sum(sleeps), 30,
                        "冷却 sleep 必须分段响应停止信号，不等冷却结束")


class GuardImmediateStopTests(unittest.TestCase):
    """US2 T023：立即停止终止活动批次子进程并清理批次登记。"""

    class _FakeProcess:
        def __init__(self):
            self._poll = None

        def poll(self):
            return self._poll

    def test_immediate_stop_terminates_and_clears_own_task_batches(self):
        ctx = _FakeCtx()
        guard = _make_guard(ctx)
        try:
            proc = self._FakeProcess()
            guard.begin_batch("jd-t1-0:0", task_id="t1", attempt=1)
            guard.spawn_hook("jd-t1-0:0")(proc)
            guard.begin_batch("jd-t1-0:1", task_id="t1", attempt=1)
            guard.begin_batch("jd-other:0", task_id="t2", attempt=1)
            with mock.patch(
                    "webui.pipeline_guard.ScraperExecutor._terminate_tree"
            ) as kill:
                guard.immediate_stop_task("t1")
            kill.assert_called_once()
            self.assertIsNone(guard.batch_state("jd-t1-0:0"),
                              "t1 批次登记必须清理")
            self.assertIsNone(guard.batch_state("jd-t1-0:1"))
            self.assertIsNotNone(guard.batch_state("jd-other:0"),
                                 "其他任务的批次不受影响")
        finally:
            guard.close()

    def test_immediate_stop_noop_when_no_active_batches(self):
        ctx = _FakeCtx()
        guard = _make_guard(ctx)
        try:
            guard.immediate_stop_task("t1")  # 无批次：不抛异常
            self.assertEqual(guard.batch_state("jd-t1-0:0"), None)
        finally:
            guard.close()


class CancelCleanupTests(unittest.TestCase):
    """US2 T024：取消路径也清理 guard 批次登记。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        session = self.client.get("/api/session")
        self.token = session.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        self.tasks = self.app.config["PIPELINE_TASKS"]
        self.ctx = self.app.config["PIPELINE_CONTEXT"]

    def tearDown(self):
        self.temp.cleanup()

    def test_cancel_cleans_guard_batches(self):
        task_id = "cancel-cleanup-1"
        self.tasks[task_id] = {
            "kind": "ai_screen", "status": "running", "progress": {},
            "logs": [], "result": None, "error": "",
            "source_task_id": "scrape-1",
            "started_at": 1000, "finished_at": None,
            "stop_event": threading.Event(),
        }
        guard = self.ctx.pipeline_guard
        with mock.patch.object(guard, "immediate_stop_task", create=True) as stop:
            resp = self.client.post(f"/api/task/cancel/{task_id}")
        self.assertEqual(resp.status_code, 200)
        stop.assert_called_once_with(task_id)


if __name__ == "__main__":
    unittest.main()
