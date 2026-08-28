import json
import pathlib
import tempfile
import unittest


class _LoginCacheIsolated(unittest.TestCase):
    """D3：preflight 缓存优先会读 login-state.json，使用 browser_account 的测试
    类必须指向临时文件，避免命中 ~/.career-scout/login-state.json 的真实残留。"""

    def setUp(self):
        self._state_tmp = tempfile.TemporaryDirectory()
        from scripts import login_state_cache as _cache
        _cache.set_login_state_path(
            pathlib.Path(self._state_tmp.name) / "login-state.json")

    def tearDown(self):
        from scripts import login_state_cache as _cache
        _cache.reset_login_state_path()
        self._state_tmp.cleanup()
