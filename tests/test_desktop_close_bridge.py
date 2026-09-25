"""045 v2 聚焦测试：桌面关闭确认的两段式编排。

覆盖：场景判定（无流程 / 零岗位 / 未运行有岗位 / 运行中有岗位）、
两段式时序（立即取消本次关闭 → 前端回调后收尾关窗）、取消保持现场、
关闭守卫阻断二次弹框、前端不可用时放行。

宿主动作全部注入，不依赖真实 pywebview 与 HTTP。
"""

import unittest

from packaging import desktop_close


class _FakeHost:
    """关闭确认宿主的测试替身：记录动作，同步执行异步任务。"""

    def __init__(self, latest=None, *, action="confirm", frontend_error=False,
                 auto_callback=True, finish_result=None):
        self.latest = latest
        self.action = action
        self.frontend_error = frontend_error
        self.finish_result = finish_result
        # 真实时序里用户点击是异步的；关掉自动回调可断言「还没决定」的中间态
        self.auto_callback = auto_callback
        self.calls = []
        self.confirm_requests = []
        self.closed = False
        self.saved_state = False
        self.notified_failures = []

    def latest_running_task(self):
        return self.latest

    def request_frontend_confirm(self, scenario, callback):
        self.confirm_requests.append(scenario)
        if self.frontend_error:
            raise RuntimeError("frontend unavailable")
        if self.auto_callback:
            callback(self.action)

    def pause_run(self, run_id):
        self.calls.append(("pause", run_id))
        return {"ok": True}

    def finish_run(self, run_id):
        self.calls.append(("finish", run_id))
        if self.finish_result is not None:
            return self.finish_result
        return {"ok": True}

    def save_window_state(self):
        self.saved_state = True

    def run_async(self, fn):
        # 测试替身同步执行，避免测试等待后台线程
        fn()

    def close_window(self):
        self.closed = True

    def log(self, message):
        self.calls.append(("log", message))

    def notify_failure(self, payload):
        self.notified_failures.append(payload)


def _latest(status="running", job_count=3, run_id="run-1", has_task=True):
    return {
        "has_task": has_task,
        "task_id": run_id,
        "status": status,
        "job_count": job_count,
        "scraped_count": job_count,
    }


class ScenarioClassificationTests(unittest.TestCase):
    """场景判定是纯函数，必须可脱离宿主单测。"""

    def test_no_task_is_none(self):
        self.assertEqual(
            desktop_close.classify_close_scenario(None),
            desktop_close.SCENARIO_NONE,
        )
        self.assertEqual(
            desktop_close.classify_close_scenario({"has_task": False}),
            desktop_close.SCENARIO_NONE,
        )

    def test_zero_jobs_is_none(self):
        self.assertEqual(
            desktop_close.classify_close_scenario(_latest(job_count=0)),
            desktop_close.SCENARIO_NONE,
        )

    def test_running_with_jobs_is_running_stop(self):
        for status in ("running", "queued"):
            self.assertEqual(
                desktop_close.classify_close_scenario(_latest(status=status)),
                desktop_close.SCENARIO_RUNNING_STOP,
            )

    def test_idle_with_jobs_is_idle_save(self):
        for status in ("paused", "interrupted", "failed"):
            self.assertEqual(
                desktop_close.classify_close_scenario(_latest(status=status)),
                desktop_close.SCENARIO_IDLE_SAVE,
            )


class TwoPhaseCloseTests(unittest.TestCase):
    """两段式：先取消本次关闭，回调后再决定关还是留。"""

    def test_no_task_closes_without_confirm(self):
        host = _FakeHost(latest=None)
        bridge = desktop_close.CloseConfirmBridge(host)
        self.assertTrue(bridge.on_closing())
        self.assertEqual(host.confirm_requests, [])
        self.assertTrue(host.saved_state)

    def test_zero_jobs_closes_silently(self):
        host = _FakeHost(latest=_latest(job_count=0))
        bridge = desktop_close.CloseConfirmBridge(host)
        self.assertTrue(bridge.on_closing())
        self.assertEqual(host.confirm_requests, [])

    def test_with_jobs_cancels_this_close_and_asks_frontend(self):
        host = _FakeHost(latest=_latest(status="running"), auto_callback=False)
        bridge = desktop_close.CloseConfirmBridge(host)
        self.assertFalse(bridge.on_closing())
        self.assertEqual(host.confirm_requests, [desktop_close.SCENARIO_RUNNING_STOP])
        self.assertFalse(host.closed)

    def test_cancel_keeps_everything(self):
        host = _FakeHost(latest=_latest(status="running"), action="cancel")
        bridge = desktop_close.CloseConfirmBridge(host)
        self.assertFalse(bridge.on_closing())
        self.assertFalse(host.closed)
        self.assertEqual(host.calls, [])

    def test_confirm_idle_finishes_without_pause(self):
        host = _FakeHost(latest=_latest(status="paused"))
        bridge = desktop_close.CloseConfirmBridge(host)
        self.assertFalse(bridge.on_closing())
        self.assertTrue(host.closed)
        self.assertNotIn(("pause", "run-1"), host.calls)
        self.assertIn(("finish", "run-1"), host.calls)

    def test_confirm_running_pauses_before_finish(self):
        host = _FakeHost(latest=_latest(status="running"))
        bridge = desktop_close.CloseConfirmBridge(host)
        self.assertFalse(bridge.on_closing())
        self.assertTrue(host.closed)
        self.assertEqual(
            [name for name, _ in host.calls], ["pause", "finish"],
        )

    def test_guard_allows_second_closing(self):
        host = _FakeHost(latest=_latest(status="running"))
        bridge = desktop_close.CloseConfirmBridge(host)
        self.assertFalse(bridge.on_closing())
        # 关窗动作会再次触发 closing；守卫必须放行，否则弹框死循环
        self.assertTrue(bridge.on_closing())
        self.assertEqual(len(host.confirm_requests), 1)

    def test_finish_failure_keeps_window(self):
        host = _FakeHost(
            latest=_latest(status="paused"),
            finish_result={"ok": False, "error": "save_failed"},
        )
        bridge = desktop_close.CloseConfirmBridge(host)
        self.assertFalse(bridge.on_closing())
        self.assertFalse(host.closed)
        self.assertFalse(host.saved_state)
        self.assertEqual(len(host.notified_failures), 1)

    def test_already_saved_round_closes(self):
        host = _FakeHost(
            latest=_latest(status="paused"),
            finish_result={"ok": False, "error": "already_finished"},
        )
        bridge = desktop_close.CloseConfirmBridge(host)
        self.assertFalse(bridge.on_closing())
        self.assertTrue(host.closed)

    def test_frontend_unavailable_closes_anyway(self):
        host = _FakeHost(
            latest=_latest(status="running"), frontend_error=True,
        )
        bridge = desktop_close.CloseConfirmBridge(host)
        self.assertTrue(bridge.on_closing())
        self.assertTrue(any(name == "log" for name, _ in host.calls))

    def test_query_failure_closes_anyway(self):
        class _BrokenHost(_FakeHost):
            def latest_running_task(self):
                raise RuntimeError("backend down")

        host = _BrokenHost(latest=None)
        bridge = desktop_close.CloseConfirmBridge(host)
        self.assertTrue(bridge.on_closing())
        self.assertTrue(any(name == "log" for name, _ in host.calls))


if __name__ == "__main__":
    unittest.main()
