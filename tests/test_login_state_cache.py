"""登录态缓存（D3）测试：TTL / 四态 / 回写 / 失效 / 快照。

覆盖：
- write_login_state / read_cached_state：四态读写与 TTL 15 分钟
- invalidate_login_state：单平台与全平台失效
- read_login_state：非法记录容错
- all_login_states：清理后的快照
"""

import json
import pathlib
import tempfile
import time
import unittest

from scripts import login_state_cache as cache


class LoginStateCacheTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.temp.name) / "login-state.json"
        cache.set_login_state_path(self.path)

    def tearDown(self):
        cache.reset_login_state_path()
        self.temp.cleanup()

    def test_write_and_read_roundtrip(self):
        cache.write_login_state("acc1", "boss", "logged_in")
        record = cache.read_login_state("acc1", "boss")
        self.assertEqual(record["state"], "logged_in")
        self.assertIsInstance(record["at"], float)
        self.assertEqual(cache.read_cached_state("acc1", "boss"), "logged_in")

    def test_four_states_roundtrip(self):
        for state in ("logged_in", "not_logged_in", "restricted", "unknown"):
            cache.write_login_state("acc1", "boss", state)
            self.assertEqual(cache.read_cached_state("acc1", "boss"), state)

    def test_invalid_state_is_rejected(self):
        cache.write_login_state("acc1", "boss", "half_logged")
        self.assertIsNone(cache.read_cached_state("acc1", "boss"))

    def test_ttl_expiry(self):
        cache.write_login_state("acc1", "boss", "logged_in")
        # 写入后立即注入一条过期的记录
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({
                "acc1": {"boss": {"state": "logged_in", "at": time.time() - 1000}},
            }, f)
        self.assertIsNone(cache.read_cached_state("acc1", "boss"))
        # 原始记录仍可读（TTL 是读取层的语义）
        self.assertEqual(cache.read_login_state("acc1", "boss")["state"], "logged_in")

    def test_short_ttl_override(self):
        cache.write_login_state("acc1", "boss", "logged_in")
        time.sleep(0.05)
        self.assertIsNone(cache.read_cached_state("acc1", "boss", ttl=0.01))

    def test_invalidate_single_platform(self):
        cache.write_login_state("acc1", "boss", "logged_in")
        cache.write_login_state("acc1", "zhilian", "not_logged_in")
        cache.invalidate_login_state("acc1", "boss")
        self.assertIsNone(cache.read_cached_state("acc1", "boss"))
        self.assertEqual(cache.read_cached_state("acc1", "zhilian"), "not_logged_in")

    def test_invalidate_all_platforms(self):
        cache.write_login_state("acc1", "boss", "logged_in")
        cache.write_login_state("acc1", "zhilian", "restricted")
        cache.invalidate_login_state("acc1")
        self.assertIsNone(cache.read_cached_state("acc1", "boss"))
        self.assertIsNone(cache.read_cached_state("acc1", "zhilian"))

    def test_missing_account_returns_none(self):
        self.assertIsNone(cache.read_cached_state("ghost", "boss"))
        self.assertIsNone(cache.read_login_state("", "boss"))

    def test_corrupt_file_is_tolerated(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(cache.read_cached_state("acc1", "boss"))
        cache.write_login_state("acc1", "boss", "logged_in")
        self.assertEqual(cache.read_cached_state("acc1", "boss"), "logged_in")

    def test_all_login_states_snapshot(self):
        cache.write_login_state("acc1", "boss", "logged_in")
        cache.write_login_state("acc1", "zhilian", "restricted")
        cache.write_login_state("acc2", "boss", "unknown")
        snapshot = cache.all_login_states()
        self.assertEqual(snapshot["acc1"]["boss"]["state"], "logged_in")
        self.assertEqual(snapshot["acc1"]["zhilian"]["state"], "restricted")
        self.assertEqual(snapshot["acc2"]["boss"]["state"], "unknown")


if __name__ == "__main__":
    unittest.main()