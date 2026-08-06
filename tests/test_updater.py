# -*- coding: utf-8 -*-
"""应用内更新器测试（webui/updater.py）。

覆盖：版本比较、资产选择、检查更新（含 24h 缓存）、SHA256 解析与
校验、替换脚本生成、下载器 URL 白名单拒绝。网络一律用替身，不联网。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from webui import updater


class VersionTests(unittest.TestCase):
    def test_parse_version_strips_v_and_takes_three(self):
        self.assertEqual(updater.parse_version("v2.4.0"), (2, 4, 0))
        self.assertEqual(updater.parse_version("2.10"), (2, 10, 0))
        self.assertEqual(updater.parse_version("v1.2.3-beta"), (1, 2, 3))

    def test_parse_version_invalid_falls_back_to_zero(self):
        self.assertEqual(updater.parse_version("abc"), (0, 0, 0))
        self.assertEqual(updater.parse_version(""), (0, 0, 0))

    def test_is_newer(self):
        self.assertTrue(updater.is_newer("v2.5.0", "2.4.0"))
        self.assertTrue(updater.is_newer("v2.10.0", "v2.9.9"))
        self.assertFalse(updater.is_newer("v2.4.0", "2.4.0"))
        self.assertFalse(updater.is_newer("v2.3.9", "2.4.0"))


class SelectAssetsTests(unittest.TestCase):
    def _assets(self):
        return [
            {"name": "CareerScout-v2.5.0.exe", "browser_download_url":
                "https://github.com/x/y/releases/download/v2.5.0/a.exe", "size": 100},
            {"name": "CareerScout-v2.5.0.exe.sha256", "browser_download_url":
                "https://github.com/x/y/releases/download/v2.5.0/a.exe.sha256", "size": 1},
            {"name": "CareerScout-v2.5.0.dmg", "browser_download_url":
                "https://github.com/x/y/releases/download/v2.5.0/b.dmg", "size": 200},
            {"name": "CareerScout-v2.5.0.dmg.sha256", "browser_download_url":
                "https://github.com/x/y/releases/download/v2.5.0/b.dmg.sha256", "size": 1},
        ]

    def test_windows_selection(self):
        main, sha = updater._select_assets(self._assets(), "windows")
        self.assertIsNotNone(main)
        self.assertTrue(main.name.endswith(".exe"))
        self.assertFalse(main.name.endswith(".sha256"))
        self.assertTrue(sha.endswith(".exe.sha256"))

    def test_macos_selection(self):
        main, sha = updater._select_assets(self._assets(), "macos")
        self.assertTrue(main.name.endswith(".dmg"))
        self.assertTrue(sha.endswith(".dmg.sha256"))

    def test_missing_asset_returns_none(self):
        main, sha = updater._select_assets([], "windows")
        self.assertIsNone(main)
        self.assertEqual(sha, "")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class CheckUpdateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _api_payload(self, tag="v9.9.9", assets=None):
        return {
            "tag_name": tag,
            "html_url": "https://github.com/x/y/releases/tag/" + tag,
            "body": "notes",
            "assets": assets if assets is not None else [
                {"name": "CareerScout-v9.9.9.exe",
                 "browser_download_url": "https://github.com/d.exe", "size": 5},
                {"name": "CareerScout-v9.9.9.exe.sha256",
                 "browser_download_url": "https://github.com/d.exe.sha256", "size": 1},
            ],
        }

    def test_has_update_with_asset(self):
        with patch.object(updater, "detect_update_platform", return_value="windows"):
            info = updater.check_for_update(
                "2.4.0", state_dir=self.state_dir,
                fetcher=lambda: _FakeResponse(self._api_payload()),
            )
        self.assertTrue(info.ok)
        self.assertTrue(info.has_update)
        self.assertEqual(info.latest, "9.9.9")
        self.assertTrue(info.asset_url.endswith(".exe"))
        self.assertTrue(info.sha256_url.endswith(".sha256"))

    def test_no_update_when_same_version(self):
        with patch.object(updater, "detect_update_platform", return_value="windows"):
            info = updater.check_for_update(
                "9.9.9", state_dir=self.state_dir,
                fetcher=lambda: _FakeResponse(self._api_payload()),
            )
        self.assertFalse(info.has_update)

    def test_fetch_failure_degrades_silently(self):
        def broken():
            raise OSError("network down")

        info = updater.check_for_update(
            "2.4.0", state_dir=self.state_dir, fetcher=broken,
        )
        self.assertFalse(info.ok)
        self.assertEqual(info.reason, "check_failed")

    def test_cache_prevents_second_fetch_within_ttl(self):
        calls = []

        def fetch():
            calls.append(1)
            return _FakeResponse(self._api_payload())

        with patch.object(updater, "detect_update_platform", return_value="windows"):
            updater.check_for_update("2.4.0", state_dir=self.state_dir, fetcher=fetch)
            info = updater.check_for_update("2.4.0", state_dir=self.state_dir, fetcher=fetch)
        self.assertEqual(len(calls), 1, "24h 内第二次检查应命中缓存")
        self.assertTrue(info.has_update)

    def test_force_bypasses_cache(self):
        calls = []

        def fetch():
            calls.append(1)
            return _FakeResponse(self._api_payload())

        with patch.object(updater, "detect_update_platform", return_value="windows"):
            updater.check_for_update("2.4.0", state_dir=self.state_dir, fetcher=fetch)
            updater.check_for_update("2.4.0", state_dir=self.state_dir, force=True, fetcher=fetch)
        self.assertEqual(len(calls), 2)

    def test_no_sha256_asset_flags_reason(self):
        assets = [{"name": "CareerScout-v9.9.9.exe",
                   "browser_download_url": "https://github.com/d.exe", "size": 5}]
        with patch.object(updater, "detect_update_platform", return_value="windows"):
            info = updater.check_for_update(
                "2.4.0", state_dir=self.state_dir,
                fetcher=lambda: _FakeResponse(self._api_payload(assets=assets)),
            )
        self.assertTrue(info.has_update)
        self.assertEqual(info.reason, "no_sha256")


class Sha256Tests(unittest.TestCase):
    def test_fetch_expected_sha256_parses_sha256sum_format(self):
        digest = "a" * 64

        class Resp:
            text = f"{digest}  CareerScout.exe\n"

            def raise_for_status(self):
                pass

        with patch.object(updater.requests, "get", return_value=Resp()):
            self.assertEqual(
                updater.fetch_expected_sha256("https://github.com/a.sha256"), digest
            )

    def test_fetch_rejects_non_github_url(self):
        self.assertIsNone(updater.fetch_expected_sha256("https://evil.com/a.sha256"))

    def test_compute_sha256_matches_hashlib(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"career-scout")
            path = Path(f.name)
        try:
            import hashlib

            self.assertEqual(
                updater.compute_sha256(path),
                hashlib.sha256(b"career-scout").hexdigest(),
            )
        finally:
            path.unlink()


class DownloaderTests(unittest.TestCase):
    def test_start_rejects_disallowed_url(self):
        d = updater.UpdateDownloader(state_dir=tempfile.mkdtemp())
        info = updater.UpdateInfo(asset_url="https://evil.com/x.exe")
        self.assertFalse(d.start(info))
        self.assertEqual(d.status()["status"], "failed")
        self.assertEqual(d.status()["error"], "invalid_download_url")

    def test_start_rejects_double_start(self):
        d = updater.UpdateDownloader(state_dir=tempfile.mkdtemp())
        d.state.status = "downloading"
        info = updater.UpdateInfo(asset_url="https://github.com/x.exe")
        self.assertFalse(d.start(info))


class UpdaterScriptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_windows_script_contains_wait_replace_launch(self):
        with patch.object(updater.platform, "system", return_value="Windows"):
            _, script = updater.build_updater_script(
                installer_path=self.dir / "new.exe",
                install_target=self.dir / "CareerScout.exe",
                pid=12345,
                script_dir=self.dir,
            )
        text = script.read_text(encoding="utf-8")
        self.assertIn("12345", text)  # 等主进程退出
        self.assertIn("move /Y", text)  # 替换
        self.assertIn('start ""', text)  # 拉起新版

    def test_macos_script_mounts_copies_and_relaunches(self):
        with patch.object(updater.platform, "system", return_value="Darwin"):
            _, script = updater.build_updater_script(
                installer_path=self.dir / "new.dmg",
                install_target=Path("/Applications/CareerScout.app"),
                pid=777,
                script_dir=self.dir,
            )
        text = script.read_text(encoding="utf-8")
        self.assertIn("kill -0 777", text)
        self.assertIn("hdiutil attach", text)
        self.assertIn("cp -R", text)
        self.assertIn("hdiutil detach", text)
        self.assertIn("TARGET.old", text)  # 原子替换备份回滚
        self.assertIn('open "$TARGET"', text)


class ExternalLinkWhitelistTests(unittest.TestCase):
    def test_allows_github_and_subdomain(self):
        from packaging import desktop

        self.assertTrue(desktop.is_allowed_external_url(
            "https://github.com/Czilasy/career-scout-preview"))
        self.assertTrue(desktop.is_allowed_external_url(
            "https://gist.github.com/someone"))

    def test_rejects_others(self):
        from packaging import desktop

        self.assertFalse(desktop.is_allowed_external_url("http://github.com/x"))
        self.assertFalse(desktop.is_allowed_external_url("https://evil.com"))
        self.assertFalse(desktop.is_allowed_external_url("https://github.com.evil.com"))
        self.assertFalse(desktop.is_allowed_external_url(""))


if __name__ == "__main__":
    unittest.main()
