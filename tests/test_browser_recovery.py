"""BrowserRecovery 失联事件边界测试。"""

import unittest

from webui.browser_recovery import BrowserRecovery


class BrowserRecoveryTests(unittest.TestCase):

    def test_same_loss_event_restarts_once(self):
        calls = []

        def ensure(_port, **_kwargs):
            calls.append(1)
            return True, ""

        recovery = BrowserRecovery(cdp_port=9222, ensure_chrome_ready=ensure)
        ok, msg = recovery.try_restart()
        self.assertTrue(ok, msg)
        ok2, _ = recovery.try_restart()
        self.assertFalse(ok2, "同一失联事件不得重复自动重启")
        self.assertEqual(len(calls), 1)

    def test_restart_failure_consumes_event(self):
        calls = []

        def ensure(_port, **_kwargs):
            calls.append(1)
            return False, "launch failed"

        recovery = BrowserRecovery(ensure_chrome_ready=ensure)
        ok, msg = recovery.try_restart()
        self.assertFalse(ok)
        self.assertIn("launch failed", msg)
        ok2, _ = recovery.try_restart()
        self.assertFalse(ok2)
        self.assertEqual(len(calls), 1)

    def test_progress_resets_event_boundary(self):
        calls = []

        def ensure(_port, **_kwargs):
            calls.append(1)
            return True, ""

        recovery = BrowserRecovery(ensure_chrome_ready=ensure)
        self.assertTrue(recovery.try_restart()[0])
        self.assertFalse(recovery.try_restart()[0])
        recovery.mark_progress()
        self.assertTrue(recovery.try_restart()[0])
        self.assertEqual(len(calls), 2)

    def test_only_cdp_loss_codes_are_recoverable(self):
        for code in ("cdp_unavailable", "source_cdp_unavailable"):
            self.assertTrue(BrowserRecovery.is_browser_lost(code))
        for code in ("source_invalid_output", "captcha_required",
                     "source_rate_limited", None, ""):
            self.assertFalse(BrowserRecovery.is_browser_lost(code))


if __name__ == "__main__":
    unittest.main()
