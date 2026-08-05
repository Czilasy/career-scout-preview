"""风控冷却（D6）测试：parse_unlock_time / mark_cooldown / 查询与解除。

覆盖：
- parse_unlock_time：完整日期时间、月日、中文年月日、过去时间与无匹配
- mark_cooldown：默认 4 小时、精确解封点、无效参数
- get_cooldown / remaining_seconds：未到期的存活、到期消失
- clear_cooldown：手动解除（不碰登录态缓存）
- all_cooldowns：只返回生效中的记录
"""

import pathlib
import tempfile
import time
import unittest

from scripts.boss_cdp_raw import parse_unlock_time
from webui import cooldown as cd


class ParseUnlockTimeTests(unittest.TestCase):
    """从风控文本提取完整日期时间格式的解封点。"""

    def test_full_date_time(self):
        dt = parse_unlock_time("账号将于 2099-08-05 18:30 解封")
        self.assertIsNotNone(dt)
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour, dt.minute),
                         (2099, 8, 5, 18, 30))

    def test_slash_date_time(self):
        dt = parse_unlock_time("2099/8/5 18:30 后可继续")
        self.assertIsNotNone(dt)
        self.assertEqual((dt.year, dt.month, dt.day), (2099, 8, 5))

    def test_chinese_date_time(self):
        dt = parse_unlock_time("将于 2099年8月5日 18:30 解封")
        self.assertIsNotNone(dt)
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour, dt.minute),
                         (2099, 8, 5, 18, 30))

    def test_month_day_without_year(self):
        dt = parse_unlock_time("请于 12月31日 23:59 后重试")
        self.assertIsNotNone(dt)
        self.assertEqual((dt.month, dt.day, dt.hour, dt.minute), (12, 31, 23, 59))

    def test_past_time_returns_none(self):
        self.assertIsNone(parse_unlock_time("上次封禁时间 2020-01-01 00:00"))

    def test_no_match_returns_none(self):
        self.assertIsNone(parse_unlock_time("操作频繁，请稍后再试"))
        self.assertIsNone(parse_unlock_time(""))
        self.assertIsNone(parse_unlock_time(None))


class CooldownStoreTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cooldown_path = pathlib.Path(self.temp.name) / "cooldown.json"
        cd.set_cooldown_path(self.cooldown_path)

    def tearDown(self):
        cd.reset_cooldown_path()
        self.temp.cleanup()

    def test_default_four_hours(self):
        before = time.time()
        until = cd.mark_cooldown("acc1", "boss", "操作频繁，请稍后再试")
        self.assertIsNotNone(until)
        self.assertGreater(until, before + 4 * 3600 - 5)
        self.assertLess(until, before + 4 * 3600 + 5)
        record = cd.get_cooldown("acc1", "boss")
        self.assertEqual(record["until"], until)
        self.assertIn("操作频繁", record["reason"])

    def test_exact_unlock_time_wins(self):
        until = cd.mark_cooldown(
            "acc1", "boss", "您的账号将于 2099-03-01 12:00 解除限制")
        expected = parse_unlock_time("2099-03-01 12:00")
        self.assertIsNotNone(expected)
        self.assertAlmostEqual(until, expected.timestamp(), delta=2)

    def test_seconds_override(self):
        until = cd.mark_cooldown("acc1", "boss", "测试", seconds=60)
        self.assertGreater(until, time.time() + 55)
        self.assertLess(until, time.time() + 120)

    def test_invalid_args_return_none(self):
        self.assertIsNone(cd.mark_cooldown("", "boss", "x"))
        self.assertIsNone(cd.mark_cooldown("acc1", "", "x"))

    def test_expired_record_returns_none(self):
        cd.mark_cooldown("acc1", "boss", "测试", seconds=1)
        time.sleep(1.1)
        self.assertIsNone(cd.get_cooldown("acc1", "boss"))
        self.assertEqual(cd.remaining_seconds("acc1", "boss"), 0)

    def test_remaining_seconds_positive(self):
        cd.mark_cooldown("acc1", "boss", "测试", seconds=3600)
        remaining = cd.remaining_seconds("acc1", "boss")
        self.assertGreater(remaining, 3500)

    def test_clear_single_platform(self):
        cd.mark_cooldown("acc1", "boss", "x", seconds=3600)
        cd.mark_cooldown("acc1", "zhilian", "x", seconds=3600)
        cd.clear_cooldown("acc1", "boss")
        self.assertIsNone(cd.get_cooldown("acc1", "boss"))
        self.assertIsNotNone(cd.get_cooldown("acc1", "zhilian"))

    def test_clear_all_platforms(self):
        cd.mark_cooldown("acc1", "boss", "x", seconds=3600)
        cd.mark_cooldown("acc1", "zhilian", "x", seconds=3600)
        cd.clear_cooldown("acc1")
        self.assertIsNone(cd.get_cooldown("acc1", "boss"))
        self.assertIsNone(cd.get_cooldown("acc1", "zhilian"))

    def test_all_cooldowns_prunes_expired(self):
        cd.mark_cooldown("acc1", "boss", "x", seconds=3600)
        cd.mark_cooldown("acc2", "boss", "y", seconds=1)
        time.sleep(1.1)
        snapshot = cd.all_cooldowns()
        self.assertIn("acc1", snapshot)
        self.assertNotIn("acc2", snapshot)

    def test_clear_does_not_touch_login_cache(self):
        """D6：手动解除只清 cooldown，不碰登录态缓存。"""
        from scripts import login_state_cache as cache
        cache.set_login_state_path(pathlib.Path(self.temp.name) / "login-state.json")
        try:
            cache.write_login_state("acc1", "boss", "restricted")
            cd.mark_cooldown("acc1", "boss", "x", seconds=3600)
            cd.clear_cooldown("acc1", "boss")
            self.assertIsNone(cd.get_cooldown("acc1", "boss"))
            self.assertEqual(cache.read_cached_state("acc1", "boss"), "restricted")
        finally:
            cache.reset_login_state_path()


if __name__ == "__main__":
    unittest.main()