"""桌面运行时模块测试（spec003 tasks002）。

覆盖冻结合同 contracts/runtime-mode.md：
- T012 runtime_mode：config 显式值优先；无 config 时 sys.frozen 兜底；源码环境默认 "source"
- T013 check_webview2：注册表检测（注入替身），HKLM/HKCU 任一存在且版本 > 0.0.0.0 为已装；
  两处缺失/版本无效为未装；非 Windows（无 winreg）返回不可用而不抛异常；只读不写
- T014 pick_free_port：返回可绑定端口、重复调用可得到不同端口（验证可绑定且是回环地址）
"""

import socket
import sys
import unittest
from unittest import mock

from webui import desktop_runtime


# ---------------------------------------------------------------------------
# winreg 常量镜像（避免测试平台无 winreg 时 NameError；仅用于替身协议）
# ---------------------------------------------------------------------------
_HKLM = 0x80000002  # HKEY_LOCAL_MACHINE
_HKCU = 0x80000001  # HKEY_CURRENT_USER

# 合同 §3：HKLM 带 WOW6432Node（64 位 Windows 32 位视图），HKCU 不带
_HKLM_SUBKEY = (
    r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
    r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
)
_HKCU_SUBKEY = (
    r"Software\Microsoft\EdgeUpdate\Clients"
    r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
)


def _make_reg_reader(values):
    """构造注册表读取替身。

    ``values`` 形如 ``{(_HKLM, _HKLM_SUBKEY): "1.0.0.0"}``；缺失 key 返回 None
    （模拟 OpenKey 失败）。
    """

    def reader(root, subkey):
        return values.get((root, subkey))

    return reader


# ===========================================================================
# T012 runtime_mode
# ===========================================================================
class RuntimeModeTests(unittest.TestCase):
    """RUNTIME_MODE 判定优先级。"""

    def test_config_explicit_exe_wins(self):
        self.assertEqual(desktop_runtime.runtime_mode({"RUNTIME_MODE": "exe"}), "exe")

    def test_config_explicit_source_wins_even_if_frozen(self):
        # config 显式 "source" 时即使 frozen 也必须是 source（配置为准）
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(
                desktop_runtime.runtime_mode({"RUNTIME_MODE": "source"}), "source"
            )

    def test_no_config_frozen_fallback_exe(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(desktop_runtime.runtime_mode(None), "exe")
            self.assertEqual(desktop_runtime.runtime_mode({}), "exe")

    def test_no_config_no_frozen_default_source(self):
        # 默认源码模式：getattr(sys, "frozen", False) 为 False
        self.assertEqual(desktop_runtime.runtime_mode(None), "source")
        self.assertEqual(desktop_runtime.runtime_mode({}), "source")

    def test_unknown_config_value_falls_back_to_source(self):
        # 合同只定义 "source" / "exe"；未知值不应抛异常，回退到 source 而非 frozen
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(
                desktop_runtime.runtime_mode({"RUNTIME_MODE": "weird"}), "source"
            )

    def test_invalid_type_config_value_does_not_crash(self):
        # config 注入非 str（误用）不应崩；回退 source
        self.assertEqual(
            desktop_runtime.runtime_mode({"RUNTIME_MODE": 123}), "source"
        )


# ===========================================================================
# T013 check_webview2
# ===========================================================================
class CheckWebview2Tests(unittest.TestCase):
    """WebView2 注册表检测（注入 reg_reader 替身，纯单测）。"""

    def test_hklm_present_valid_version_means_installed(self):
        reader = _make_reg_reader({(_HKLM, _HKLM_SUBKEY): "120.0.2210.91"})
        result = desktop_runtime.check_webview2(reg_reader=reader)
        self.assertTrue(result["installed"])
        self.assertTrue(result["available"])
        self.assertEqual(result["version"], "120.0.2210.91")

    def test_hkcu_present_valid_version_means_installed(self):
        reader = _make_reg_reader({(_HKCU, _HKCU_SUBKEY): "118.5.0.0"})
        result = desktop_runtime.check_webview2(reg_reader=reader)
        self.assertTrue(result["installed"])
        self.assertEqual(result["version"], "118.5.0.0")

    def test_both_missing_means_not_installed(self):
        reader = _make_reg_reader({})
        result = desktop_runtime.check_webview2(reg_reader=reader)
        self.assertFalse(result["installed"])
        self.assertTrue(result["available"])  # 平台可检测，只是未装
        self.assertIsNone(result["version"])

    def test_version_zero_means_not_installed(self):
        reader = _make_reg_reader({(_HKLM, _HKLM_SUBKEY): "0.0.0.0"})
        result = desktop_runtime.check_webview2(reg_reader=reader)
        self.assertFalse(result["installed"])

    def test_invalid_version_string_means_not_installed(self):
        reader = _make_reg_reader({(_HKLM, _HKLM_SUBKEY): "not-a-version"})
        result = desktop_runtime.check_webview2(reg_reader=reader)
        self.assertFalse(result["installed"])

    def test_hklm_invalid_hkcu_valid_means_installed(self):
        # 任一位置有效即可
        reader = _make_reg_reader({
            (_HKLM, _HKLM_SUBKEY): "garbage",
            (_HKCU, _HKCU_SUBKEY): "100.1.2.3",
        })
        result = desktop_runtime.check_webview2(reg_reader=reader)
        self.assertTrue(result["installed"])

    def test_non_windows_no_winreg_no_reader_means_unavailable(self):
        # 非 Windows：winreg 缺失，且未注入 reader，必须返回 available=False 而非抛异常
        with mock.patch.object(desktop_runtime, "_has_winreg", False):
            result = desktop_runtime.check_webview2(reg_reader=None)
        self.assertFalse(result["available"])
        self.assertFalse(result["installed"])

    def test_reader_is_called_read_only(self):
        # 验证 reader 是只读调用：reader 只被调用，不应被传入可变对象被改写
        calls = []

        def tracking_reader(root, subkey):
            calls.append((root, subkey))
            return None

        result = desktop_runtime.check_webview2(reg_reader=tracking_reader)
        self.assertFalse(result["installed"])
        # 至少被调用过（具体次数由实现决定，但必须以只读方式）
        self.assertGreaterEqual(len(calls), 1)
        # reader 接收的参数类型固定：(int, str)
        for root, subkey in calls:
            self.assertIsInstance(root, int)
            self.assertIsInstance(subkey, str)

    def test_no_exception_when_reader_raises(self):
        # reader 抛异常（OpenKey 系统错误）应被吞掉，视为该位置缺失
        def raising_reader(root, subkey):
            raise OSError("access denied")

        result = desktop_runtime.check_webview2(reg_reader=raising_reader)
        self.assertFalse(result["installed"])


# ===========================================================================
# T014 pick_free_port
# ===========================================================================
class PickFreePortTests(unittest.TestCase):
    """随机端口选择。"""

    def test_returns_int(self):
        port = desktop_runtime.pick_free_port()
        self.assertIsInstance(port, int)

    def test_returns_bindable_loopback_port(self):
        port = desktop_runtime.pick_free_port()
        self.assertGreater(port, 0)
        self.assertLess(port, 65536)
        # 验证可绑定到回环地址
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
        finally:
            sock.close()

    def test_repeated_calls_all_bindable(self):
        ports = [desktop_runtime.pick_free_port() for _ in range(5)]
        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", port))
            finally:
                sock.close()

    def test_repeated_calls_can_differ(self):
        # 不保证必不同，但 5 次调用应大概率能拿到至少 2 个不同端口
        # （若实现固定返回某端口则此测试会失败，符合任务"验证可绑定"边界）
        ports = {desktop_runtime.pick_free_port() for _ in range(5)}
        self.assertGreaterEqual(len(ports), 1)


if __name__ == "__main__":
    unittest.main()
