import pathlib
import threading
import time
import unittest
from contextlib import nullcontext
from unittest import mock

from webui.app_support import PlatformExecutionCapacity
from webui.browser_support import build_browser_support
from webui.frozen_browser_identity import activate_frozen_browser_run


class B096ParallelCapacityTests(unittest.TestCase):
    def tearDown(self):
        capacity = getattr(self, "capacity", None)
        if capacity is not None:
            capacity.shutdown(wait=True)

    def test_different_platforms_can_overlap_while_each_platform_has_one_lane(self):
        self.capacity = PlatformExecutionCapacity()
        barrier = threading.Barrier(2)
        seen = []
        seen_lock = threading.Lock()

        def work(platform):
            with seen_lock:
                seen.append((platform, "entered"))
            barrier.wait(timeout=2)
            return platform

        boss = self.capacity.submit("boss", work, "boss")
        zhilian = self.capacity.submit("zhilian", work, "zhilian")
        self.assertEqual({boss.result(timeout=3), zhilian.result(timeout=3)}, {"boss", "zhilian"})
        self.assertEqual(
            {item[0] for item in seen if item[1] == "entered"},
            {"boss", "zhilian"},
        )

    def test_same_platform_is_serial_and_stop_does_not_cancel_other_lane(self):
        self.capacity = PlatformExecutionCapacity()
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        events = []

        def first():
            first_started.set()
            release_first.wait(timeout=2)
            events.append("boss-stopped")
            return "stopped"

        def second():
            second_started.set()
            events.append("boss-second")
            return "second"

        first_future = self.capacity.submit("boss", first)
        second_future = self.capacity.submit("boss", second)
        self.assertTrue(first_started.wait(timeout=1))
        self.assertFalse(second_started.wait(timeout=0.15))
        release_first.set()
        self.assertEqual(first_future.result(timeout=2), "stopped")
        self.assertEqual(second_future.result(timeout=2), "second")
        self.assertEqual(events, ["boss-stopped", "boss-second"])

        other_lane = self.capacity.submit("zhilian", lambda: "still-running")
        self.assertEqual(other_lane.result(timeout=2), "still-running")

    def test_unknown_platform_cannot_open_a_capacity_lane(self):
        self.capacity = PlatformExecutionCapacity()
        with self.assertRaises(ValueError):
            self.capacity.submit("third", lambda: None)

    def test_ai_capacity_has_independent_platform_lanes(self):
        self.capacity = PlatformExecutionCapacity(thread_name_prefix="ai-screen")
        barrier = threading.Barrier(2)

        def work():
            barrier.wait(timeout=2)
            return threading.current_thread().name

        boss = self.capacity.submit("boss", work)
        zhilian = self.capacity.submit("zhilian", work)
        names = {boss.result(timeout=3), zhilian.result(timeout=3)}
        self.assertEqual(len(names), 2)
        self.assertTrue(all(name.startswith(("ai-screen-boss", "ai-screen-zhilian")) for name in names))


class B096ResourceIsolationTests(unittest.TestCase):
    def test_browser_busy_is_scoped_by_platform_but_global_settings_remain_busy(self):
        tasks = {
            "boss-run": {"status": "running", "platform": "boss", "browser_account": "a"},
        }
        lock = threading.RLock()
        store = mock.Mock()
        connection = mock.Mock()
        connection.execute.return_value.fetchall.return_value = []
        store._connection.return_value = nullcontext(connection)
        browser = build_browser_support(
            store,
            tasks,
            lock,
            lambda _run=None: "a",
            mock.Mock(),
        )
        _browser_lock, browser_busy, _close_info, _close, _has_active, _project = browser
        self.assertTrue(browser_busy())
        self.assertTrue(browser_busy("boss"))
        self.assertFalse(browser_busy("zhilian"))
        self.assertEqual(_browser_lock("boss")[2], "boss")

        tasks["boss-second"] = {
            "status": "queued", "platform": "boss", "browser_account": "a",
        }
        self.assertTrue(browser_busy("boss"), "同平台/同资源仍须互斥")

    def test_frozen_account_profile_and_port_are_platform_specific(self):
        activate = []
        boss = activate_frozen_browser_run(
            {
                "platform": "boss",
                "execution_params": {
                    "platform": "boss", "browser_account": "a",
                    "profile_key": "boss:a", "cdp_port": 9222,
                },
            },
            accounts_path="unused",
            resolve_account=lambda account, _path: f"C:/profiles/{account}",
            activate=activate.append,
        )
        zhilian = activate_frozen_browser_run(
            {
                "platform": "zhilian",
                "execution_params": {
                    "platform": "zhilian", "browser_account": "a",
                    "profile_key": "zhilian:a", "cdp_port": 9223,
                    "execution_config": {},
                },
            },
            accounts_path="unused",
            resolve_account=lambda account, _path: f"C:/profiles/{account}",
            activate=activate.append,
        )
        self.assertEqual((boss.profile_key, boss.cdp_port), ("boss:a", 9222))
        self.assertEqual((zhilian.profile_key, zhilian.cdp_port), ("zhilian:a", 9223))
        self.assertNotEqual(boss.profile_dir, zhilian.profile_dir)
        self.assertEqual(len(activate), 2)


if __name__ == "__main__":
    unittest.main()
