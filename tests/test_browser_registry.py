# -*- coding: utf-8 -*-

"""浏览器注册表域测试（029 B082③）。

真机只有 Chrome/Edge 时，其余 6 家的兜底验收 = 注册表完整性断言 +
探测/解析/校验的注入式单测（spec SC-004）。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.boss import browser_registry as br
from webui.pipeline_exec_accounts import effective_data_dir


def _fake_detect(**overrides):
    """构造 detect_browsers 替身：默认 chrome/edge 已装，其余未装。"""
    installed = {"chrome", "edge"}
    installed |= overrides.pop("extra", set())
    installed -= overrides.pop("missing", set())

    def _detect(env=None, exists=None):
        return [
            {
                "key": entry["key"],
                "name": entry["name"],
                "installed": entry["key"] in installed,
                "path": (f"C:\\browsers\\{entry['key']}.exe"
                         if entry["key"] in installed else None),
            }
            for entry in br.BROWSER_REGISTRY
        ]

    return _detect


# ===========================================================================
# 注册表完整性（真机未安装浏览器的兜底验收）
# ===========================================================================
class RegistryIntegrityTests(unittest.TestCase):
    """冻结清单：8 家、字段完整、标识唯一。"""

    def test_frozen_v1_list(self):
        self.assertEqual(
            br.REGISTRY_KEYS,
            ("chrome", "edge", "brave", "vivaldi", "opera",
             "se360", "qqbrowser", "quark"),
        )

    def test_keys_and_data_dir_keys_unique(self):
        keys = [entry["key"] for entry in br.BROWSER_REGISTRY]
        dir_keys = [entry["data_dir_key"] for entry in br.BROWSER_REGISTRY]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(dir_keys), len(set(dir_keys)))

    def test_entries_have_required_fields(self):
        import re

        for entry in br.BROWSER_REGISTRY:
            self.assertTrue(entry["name"], entry["key"])
            self.assertTrue(entry["exe_names"], entry["key"])
            self.assertGreaterEqual(len(entry["windows_candidates"]), 1, entry["key"])
            self.assertGreaterEqual(len(entry["windows_candidates"][0]), 2, entry["key"])
            self.assertRegex(entry["data_dir_key"], r"^[a-z0-9-]+$", entry["key"])
            for candidate in entry["windows_candidates"]:
                self.assertTrue(candidate[0].isupper() or "(" in candidate[0], candidate)
                self.assertIn(
                    candidate[-1].lower(),
                    [exe.lower() for exe in entry["exe_names"]],
                    candidate,
                )

    def test_all_registry_exe_names(self):
        names = br.all_registry_exe_names()
        for exe in ("chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe",
                    "opera.exe", "360chrome.exe", "360se.exe",
                    "qqbrowser.exe", "quarkpc.exe"):
            self.assertIn(exe, names)
        self.assertEqual(len(names), 9)

    def test_registry_entry_lookup(self):
        self.assertEqual(br.registry_entry("brave")["data_dir_key"], "brave")
        self.assertIsNone(br.registry_entry("nope"))
        self.assertIsNone(br.registry_entry(None))


# ===========================================================================
# 探测与解析
# ===========================================================================
class DetectTests(unittest.TestCase):
    """路径探测：候选命中与全量清单形状。"""

    def test_detect_returns_full_list_shape(self):
        results = br.detect_browsers(env={}, exists=lambda p: False)
        self.assertEqual([item["key"] for item in results], list(br.REGISTRY_KEYS))
        for item in results:
            self.assertFalse(item["installed"])
            self.assertIsNone(item["path"])

    def test_detect_windows_candidate_hit(self):
        env = {"PROGRAMFILES": r"C:\Program Files"}
        exists = lambda p: "Chrome" in p  # noqa: E731
        results = br.detect_browsers(env=env, exists=exists)
        chrome = next(item for item in results if item["key"] == "chrome")
        self.assertTrue(chrome["installed"])
        self.assertTrue(chrome["path"].endswith("chrome.exe"))
        self.assertIn("Google", chrome["path"])

    def test_detect_candidate_order_env_fallback(self):
        """候选按顺序：PROGRAMFILES 缺环境变量时回退 LOCALAPPDATA 命中。"""
        env = {"LOCALAPPDATA": r"C:\cs-test\AppData\Local"}
        exists = lambda p: "Edge" in p  # noqa: E731
        results = br.detect_browsers(env=env, exists=exists)
        edge = next(item for item in results if item["key"] == "edge")
        self.assertTrue(edge["installed"])
        self.assertIn("AppData", edge["path"])

    def test_detect_missing_env_skipped(self):
        env = {"LOCALAPPDATA": r"C:\cs-test\AppData\Local"}
        exists = lambda p: "360Chrome" in p  # noqa: E731
        results = br.detect_browsers(env=env, exists=exists)
        se360 = next(item for item in results if item["key"] == "se360")
        # PROGRAMFILES 未提供 → LOCALAPPDATA 候选命中
        self.assertTrue(se360["installed"])
        self.assertIn("360Chrome", se360["path"])


class ResolveExecutableTests(unittest.TestCase):
    """选择 → 可执行文件解析（auto/registry/manual 三模式）。"""

    def test_auto_prefers_chrome_then_edge(self):
        path, reason = br.resolve_executable(
            selection_loader=lambda: {"mode": "auto"},
            detect_fn=_fake_detect(),
        )
        self.assertEqual(path, r"C:\browsers\chrome.exe")
        self.assertIsNone(reason)

    def test_auto_falls_to_edge_then_registry_order(self):
        path, _ = br.resolve_executable(
            selection_loader=lambda: {"mode": "auto"},
            detect_fn=_fake_detect(missing={"chrome"}),
        )
        self.assertEqual(path, r"C:\browsers\edge.exe")
        path, _ = br.resolve_executable(
            selection_loader=lambda: {"mode": "auto"},
            detect_fn=_fake_detect(missing={"chrome", "edge"}, extra={"brave"}),
        )
        self.assertEqual(path, r"C:\browsers\brave.exe")

    def test_auto_nothing_found_reason_readable(self):
        path, reason = br.resolve_executable(
            selection_loader=lambda: {"mode": "auto"},
            detect_fn=_fake_detect(missing={"chrome", "edge"}),
        )
        self.assertIsNone(path)
        self.assertIn("Chromium", reason)

    def test_registry_mode_resolves_installed(self):
        path, reason = br.resolve_executable(
            selection_loader=lambda: {"mode": "registry", "key": "edge"},
            detect_fn=_fake_detect(),
        )
        self.assertEqual(path, r"C:\browsers\edge.exe")
        self.assertIsNone(reason)

    def test_registry_mode_uninstalled_reason(self):
        path, reason = br.resolve_executable(
            selection_loader=lambda: {"mode": "registry", "key": "vivaldi"},
            detect_fn=_fake_detect(),
        )
        self.assertIsNone(path)
        self.assertIn("Vivaldi", reason)
        self.assertIn("重新选择", reason)

    def test_manual_mode_existing_path(self):
        exists = lambda p: p.endswith("mybrowser.exe")  # noqa: E731
        path, reason = br.resolve_executable(
            selection_loader=lambda: {
                "mode": "manual", "manual_path": r"D:\Browsers\mybrowser.exe"},
            detect_fn=_fake_detect(),
            exists=exists,
        )
        self.assertEqual(path, r"D:\Browsers\mybrowser.exe")
        self.assertIsNone(reason)

    def test_manual_mode_missing_path_reason(self):
        path, reason = br.resolve_executable(
            selection_loader=lambda: {
                "mode": "manual", "manual_path": r"D:\gone.exe"},
            exists=lambda p: False,
        )
        self.assertIsNone(path)
        self.assertIn("不存在或已失效", reason)


# ===========================================================================
# 选择持久化
# ===========================================================================
class SelectionPersistenceTests(unittest.TestCase):
    """browser_selection.json 读写与容错。"""

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "browser_selection.json"

    def test_save_load_roundtrip_registry(self):
        br.save_browser_selection("registry", key="brave", path=self.path)
        self.assertEqual(
            br.load_browser_selection(self.path),
            {"mode": "registry", "key": "brave"},
        )

    def test_save_load_roundtrip_manual(self):
        br.save_browser_selection("manual", manual_path=r"D:\b.exe", path=self.path)
        self.assertEqual(
            br.load_browser_selection(self.path),
            {"mode": "manual", "manual_path": r"D:\b.exe"},
        )

    def test_save_auto(self):
        br.save_browser_selection("auto", path=self.path)
        self.assertEqual(br.load_browser_selection(self.path), {"mode": "auto"})

    def test_invalid_mode_raises(self):
        with self.assertRaises(ValueError):
            br.save_browser_selection("nope", path=self.path)
        with self.assertRaises(ValueError):
            br.save_browser_selection("registry", key="nope", path=self.path)
        with self.assertRaises(ValueError):
            br.save_browser_selection("manual", path=self.path)

    def test_corrupt_file_falls_back_auto(self):
        self.path.write_text("not json{", encoding="utf-8")
        self.assertEqual(br.load_browser_selection(self.path), {"mode": "auto"})

    def test_unknown_key_persists_as_auto(self):
        self.path.write_text(
            json.dumps({"mode": "registry", "key": "gone"}), encoding="utf-8"
        )
        self.assertEqual(br.load_browser_selection(self.path), {"mode": "auto"})

    def test_missing_file_is_auto(self):
        self.assertEqual(br.load_browser_selection(self.path), {"mode": "auto"})


# ===========================================================================
# 手动路径校验与内核判定
# ===========================================================================
class ValidateManualPathTests(unittest.TestCase):
    """``--version`` 探活校验（fake runner 注入）。"""

    def _ok_runner(self):
        def runner(cmd, timeout=10):
            return 0, "Google Chrome 126.0.6478.126\n"
        return runner

    def test_valid_chromium_path(self):
        ok, info = br.validate_manual_path(
            r"C:\b\chrome.exe", runner=self._ok_runner(), exists=lambda p: True
        )
        self.assertTrue(ok)
        self.assertIn("126", info["version"])

    def test_missing_file_fails(self):
        ok, info = br.validate_manual_path(
            r"C:\gone.exe", runner=self._ok_runner(), exists=lambda p: False
        )
        self.assertFalse(ok)
        self.assertEqual(info["error"], "path_validation_failed")

    def test_firefox_output_kernel_incompatible(self):
        def runner(cmd, timeout=10):
            return 0, "Mozilla Firefox 129.0\n"

        ok, info = br.validate_manual_path(
            r"C:\b\firefox.exe", runner=runner, exists=lambda p: True
        )
        self.assertFalse(ok)
        self.assertEqual(info["error"], "kernel_incompatible")
        self.assertIn("内核不兼容", info["message"])

    def test_empty_output_fails(self):
        def runner(cmd, timeout=10):
            return 0, ""

        ok, info = br.validate_manual_path(
            r"C:\b\x.exe", runner=runner, exists=lambda p: True
        )
        self.assertFalse(ok)
        self.assertEqual(info["error"], "path_validation_failed")

    def test_nonzero_exit_fails(self):
        def runner(cmd, timeout=10):
            return 1, "boom"

        ok, info = br.validate_manual_path(
            r"C:\b\x.exe", runner=runner, exists=lambda p: True
        )
        self.assertFalse(ok)
        self.assertEqual(info["error"], "path_validation_failed")

    def test_runner_oserror_fails(self):
        def runner(cmd, timeout=10):
            raise OSError("not a win32 executable")

        ok, info = br.validate_manual_path(
            r"C:\b\x.exe", runner=runner, exists=lambda p: True
        )
        self.assertFalse(ok)
        self.assertIn("无法作为浏览器执行", info["message"])

    def test_runner_timeout_fails(self):
        import subprocess

        def runner(cmd, timeout=10):
            raise subprocess.TimeoutExpired(cmd, timeout)

        ok, info = br.validate_manual_path(
            r"C:\b\x.exe", runner=runner, exists=lambda p: True
        )
        self.assertFalse(ok)
        self.assertIn("超时", info["message"])

    def test_empty_path_fails(self):
        ok, info = br.validate_manual_path("", runner=self._ok_runner())
        self.assertFalse(ok)
        self.assertEqual(info["error"], "path_validation_failed")


class KernelJudgeTests(unittest.TestCase):
    """CDP Browser 字段与版本输出判定。"""

    def test_is_chromium_version_output(self):
        self.assertTrue(br.is_chromium_version_output("Google Chrome 126.0"))
        self.assertTrue(br.is_chromium_version_output("Vivaldi 6.8.0"))
        self.assertFalse(br.is_chromium_version_output("Mozilla Firefox 129.0"))
        self.assertFalse(br.is_chromium_version_output(""))
        self.assertFalse(br.is_chromium_version_output(None))

    def test_is_chromium_cdp_browser(self):
        self.assertTrue(br.is_chromium_cdp_browser("Chrome/120.0.6099.199"))
        self.assertTrue(br.is_chromium_cdp_browser("Edg/120.0.2210.61"))
        self.assertTrue(br.is_chromium_cdp_browser("Chromium/118.0"))
        self.assertFalse(br.is_chromium_cdp_browser("Firefox/129.0"))
        self.assertFalse(br.is_chromium_cdp_browser(""))
        self.assertFalse(br.is_chromium_cdp_browser(None))

    def test_fetch_cdp_browser_field_parses(self):
        import io

        payload = json.dumps({"Browser": "Chrome/120.0.6099.199"}).encode()

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        field = br.fetch_cdp_browser_field(
            9222, urlopen=lambda url, timeout: _Resp(payload)
        )
        self.assertEqual(field, "Chrome/120.0.6099.199")

    def test_fetch_cdp_browser_field_error_returns_none(self):
        def boom(url, timeout):
            raise OSError("refused")

        self.assertIsNone(br.fetch_cdp_browser_field(9222, urlopen=boom))


# ===========================================================================
# 账号数据目录按浏览器命名空间派生（research D6）
# ===========================================================================
class EffectiveDataDirTests(unittest.TestCase):
    """chrome/edge 恒等（存量兼容）；其余按 data_dir_key 开命名空间。"""

    def test_chrome_identity(self):
        profile = r"C:\cs-test\.career-scout\chrome-profile"
        self.assertEqual(effective_data_dir(profile, "chrome"), profile)

    def test_edge_identity(self):
        profile = r"C:\cs-test\.career-scout\chrome-profile"
        self.assertEqual(effective_data_dir(profile, "edge"), profile)

    def test_third_party_namespaced(self):
        profile = r"C:\cs-test\.career-scout\chrome-profile"
        result = effective_data_dir(profile, "brave")
        self.assertEqual(
            result,
            str(Path(profile).parent / "chrome-profile-brave" / "chrome-profile"),
        )

    def test_per_account_dirs_isolated_under_namespace(self):
        base = Path(tempfile.mkdtemp())
        acc_a = str(base / "chrome-profile" / "account_a")
        acc_b = str(base / "chrome-profile" / "account_b")
        # 派生以各自父目录为命名空间根：账号彼此隔离、键一致则同命名空间
        self.assertEqual(
            effective_data_dir(acc_a, "vivaldi"),
            str(base / "chrome-profile" / "chrome-profile-vivaldi" / "account_a"),
        )
        self.assertEqual(
            effective_data_dir(acc_b, "vivaldi"),
            str(base / "chrome-profile" / "chrome-profile-vivaldi" / "account_b"),
        )
        # 默认内置账号 A（目录本身就是 chrome-profile）落在 ~/.career-scout 一级
        default_dir = str(base / "chrome-profile")
        self.assertEqual(
            effective_data_dir(default_dir, "vivaldi"),
            str(base / "chrome-profile-vivaldi" / "chrome-profile"),
        )

    def test_none_or_unknown_key_identity(self):
        profile = r"C:\p\account_a"
        self.assertEqual(effective_data_dir(profile, None), profile)
        self.assertEqual(effective_data_dir(profile, "auto"), profile)

    def test_same_input_same_output(self):
        profile = r"C:\p\account_a"
        self.assertEqual(
            effective_data_dir(profile, "se360"), effective_data_dir(profile, "se360")
        )


# ===========================================================================
# 路由域端点（create_app 模式，状态目录重定向到临时目录）
# ===========================================================================
class BrowserRegistryApiTests(unittest.TestCase):
    """GET/PUT/POST /api/browser-registry 契约（contracts/browser-registry-api.md）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp.name)
        self._old_env = os.environ.get("CAREER_SCOUT_STATE_DIR")
        os.environ["CAREER_SCOUT_STATE_DIR"] = str(self.state_dir)
        from webui.app import create_app

        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(self.state_dir / "results"),
            "DB_PATH": str(self.state_dir / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        self.token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        # 真机探测结果不确定 → mock 注册表探测（chrome/edge 已装）
        patcher = mock.patch.object(br, "detect_browsers", _fake_detect())
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("CAREER_SCOUT_STATE_DIR", None)
        else:
            os.environ["CAREER_SCOUT_STATE_DIR"] = self._old_env
        self.temp.cleanup()

    def test_get_returns_registry_selection_effective_path(self):
        resp = self.client.get("/api/browser-registry")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(
            [item["key"] for item in data["registry"]], list(br.REGISTRY_KEYS)
        )
        self.assertEqual(data["selection"], {"mode": "auto"})
        self.assertEqual(data["effective_path"], r"C:\browsers\chrome.exe")
        brave = next(item for item in data["registry"] if item["key"] == "brave")
        self.assertFalse(brave["installed"])
        self.assertIsNone(brave["path"])

    def test_put_registry_mode_persists(self):
        resp = self.client.put(
            "/api/browser-registry", json={"mode": "registry", "key": "edge"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])
        self.assertEqual(resp.get_json()["selection"], {"mode": "registry", "key": "edge"})
        # 持久化落地（重启保持）
        persisted = br.load_browser_selection(
            self.state_dir / "browser_selection.json"
        )
        self.assertEqual(persisted, {"mode": "registry", "key": "edge"})

    def test_put_registry_unknown_key_400(self):
        resp = self.client.put(
            "/api/browser-registry", json={"mode": "registry", "key": "nope"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "invalid_selection")

    def test_put_invalid_mode_400(self):
        resp = self.client.put("/api/browser-registry", json={"mode": "wat"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "invalid_selection")

    def test_put_manual_invalid_path_400_not_saved(self):
        resp = self.client.put(
            "/api/browser-registry", json={"mode": "manual", "path": r"C:\gone.exe"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "path_validation_failed")
        # 未通过校验不得落盘
        self.assertFalse(
            (self.state_dir / "browser_selection.json").exists()
        )

    def test_put_manual_valid_path_saved(self):
        with mock.patch.object(
            br, "validate_manual_path",
            return_value=(True, {"ok": True, "version": "126.0.1"}),
        ):
            resp = self.client.put(
                "/api/browser-registry",
                json={"mode": "manual", "path": r"C:\browsers\my.exe"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])
        persisted = br.load_browser_selection(
            self.state_dir / "browser_selection.json"
        )
        self.assertEqual(persisted, {"mode": "manual", "manual_path": r"C:\browsers\my.exe"})

    def test_validate_path_endpoint_reports_failure(self):
        resp = self.client.post(
            "/api/browser-registry/validate-path", json={"path": r"C:\gone.exe"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "path_validation_failed")

    def test_validate_path_endpoint_reports_success(self):
        with mock.patch.object(
            br, "validate_manual_path",
            return_value=(True, {"ok": True, "version": "Google Chrome 126.0"}),
        ):
            resp = self.client.post(
                "/api/browser-registry/validate-path",
                json={"path": r"C:\browsers\my.exe"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
