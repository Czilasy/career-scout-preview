# -*- coding: utf-8 -*-
"""应用内更新器测试（webui/updater.py）。

覆盖：版本比较、资产选择、检查更新（缓存已关闭，每次实时请求）、
SHA256 解析与校验、下载状态恢复、替换脚本生成、下载器 URL 白名单拒绝。
网络一律用替身，不联网。
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from webui import updater

# RFC 5737 文档测试地址，替代真实镜像地址（FR-005：公开仓库不出现镜像地址）。
# 镜像相关用例在 setUp 统一注入三元组，保持原断言语义不变。
_TEST_MIRROR_HOST = "203.0.113.7"


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


class _FakeDownloadResponse:
    def __init__(self, content):
        self._content = content
        self.headers = {"Content-Length": str(len(content))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield self._content


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

    def test_every_check_fetches_live_without_cache(self):
        calls = []

        def fetch():
            calls.append(1)
            return _FakeResponse(self._api_payload())

        with patch.object(updater, "detect_update_platform", return_value="windows"):
            updater.check_for_update("2.4.0", state_dir=self.state_dir, fetcher=fetch)
            info = updater.check_for_update("2.4.0", state_dir=self.state_dir, fetcher=fetch)
        self.assertEqual(len(calls), 2, "缓存已关闭，每次检查都应实时请求 GitHub")
        self.assertTrue(info.has_update)
        self.assertFalse((self.state_dir / "update_check.json").exists())

    def test_force_failure_without_cache_degrades_silently(self):
        def broken():
            raise OSError("network down")

        info = updater.check_for_update(
            "2.4.0", state_dir=self.state_dir, force=True, fetcher=broken,
        )
        self.assertFalse(info.ok)
        self.assertEqual(info.reason, "check_failed")

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

    def test_fetch_expected_sha256_rejects_mismatched_asset_name(self):
        digest = "b" * 64

        class Resp:
            text = f"{digest}  CareerScout-v9.9.9.exe\n"

            def raise_for_status(self):
                pass

        with patch.object(updater.requests, "get", return_value=Resp()):
            self.assertIsNone(updater.fetch_expected_sha256(
                "https://github.com/a.sha256", expected_name="CareerScout-v2.5.0.exe",
            ))
            self.assertEqual(
                updater.fetch_expected_sha256(
                    "https://github.com/a.sha256",
                    expected_name="CareerScout-v9.9.9.exe",
                ),
                digest,
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

    def test_download_run_overwrites_existing_target(self):
        d = updater.UpdateDownloader(state_dir=tempfile.mkdtemp())
        info = updater.UpdateInfo(
            asset_name="CareerScout-v2.5.0.exe",
            asset_url="https://github.com/x/x.exe",
            sha256_url="https://github.com/x/x.sha256",
        )
        target = d.download_dir / info.asset_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"old")
        content = b"career-scout"
        d._target = target
        d._expected_sha = hashlib.sha256(content).hexdigest()
        d.state.total = len(content)
        with patch.object(updater.requests, "get",
                          return_value=_FakeDownloadResponse(content)):
            d._run(info)
        self.assertEqual(d.status()["status"], "ready")
        self.assertEqual(target.read_bytes(), content)

    def test_download_exception_stores_stable_code_and_logs_original(self):
        d = updater.UpdateDownloader(state_dir=tempfile.mkdtemp())
        info = updater.UpdateInfo(asset_url="https://github.com/x/x.exe")
        d._target = d.download_dir / "x.exe"
        with self.assertLogs("career_scout.webui.updater", level="ERROR") as logs, \
             patch.object(updater.requests, "get",
                          side_effect=OSError("connection reset by peer")):
            d._run(info)
        status = d.status()
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["error"], "download_failed")
        self.assertNotIn("connection reset", status["error"])
        self.assertTrue(any("connection reset by peer" in line for line in logs.output))


class DownloaderRecoveryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.downloader = updater.UpdateDownloader(state_dir=Path(self._tmp.name))
        self.info = updater.UpdateInfo(
            asset_name="CareerScout-v2.5.0.exe",
            asset_url="https://github.com/Czilasy/career-scout-preview/releases/download/v2.5.0/CareerScout-v2.5.0.exe",
            sha256_url="https://github.com/Czilasy/career-scout-preview/releases/download/v2.5.0/CareerScout-v2.5.0.exe.sha256",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _target(self):
        return self.downloader.download_dir / self.info.asset_name

    def test_recover_ready_accepts_verified_existing_file(self):
        target = self._target()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"career-scout")
        digest = hashlib.sha256(b"career-scout").hexdigest()
        with patch.object(updater, "fetch_expected_sha256", return_value=digest):
            self.assertTrue(self.downloader.recover_ready(self.info))
        status = self.downloader.status()
        self.assertEqual(status["status"], "ready")
        self.assertEqual(status["path"], str(target))
        self.assertEqual(status["received"], 12)

    def test_recover_ready_rejects_missing_file(self):
        with patch.object(updater, "fetch_expected_sha256", return_value="a" * 64):
            self.assertFalse(self.downloader.recover_ready(self.info))

    def test_recover_ready_deletes_mismatched_file(self):
        target = self._target()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"tampered")
        with patch.object(updater, "fetch_expected_sha256", return_value="a" * 64):
            self.assertFalse(self.downloader.recover_ready(self.info))
        self.assertFalse(target.exists())

    def test_recover_ready_skips_active_download(self):
        self.downloader.state.status = "downloading"
        with patch.object(updater, "fetch_expected_sha256", return_value="a" * 64):
            self.assertFalse(self.downloader.recover_ready(self.info))
        self.assertEqual(self.downloader.status()["status"], "downloading")


class UpdaterScriptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_windows_script_contains_wait_replace_launch(self):
        with patch.object(updater.platform, "system", return_value="Windows"):
            _, script = updater.build_updater_script(
                installer_path=self.dir / "CareerScout-v2.5.0.exe",
                install_target=self.dir / "CareerScout.exe",
                pid=12345,
                script_dir=self.dir,
            )
        self.assertEqual(script.name, "update_apply.ps1")
        text = script.read_text(encoding="utf-8-sig")
        self.assertTrue(script.read_bytes().startswith(b"\xef\xbb\xbf"))  # BOM，PS 5.1 解析 UTF-8
        self.assertIn("12345", text)  # 等主进程退出
        self.assertIn("Get-Process -Id", text)  # 替代 tasklist|find，无黑窗
        self.assertIn("Move-Item", text)  # 替换
        self.assertIn("Start-Process", text)  # 拉起新版

    def test_windows_script_renames_target_to_new_version(self):
        with patch.object(updater.platform, "system", return_value="Windows"):
            _, script = updater.build_updater_script(
                installer_path=self.dir / "CareerScout-v2.5.0.exe",
                install_target=self.dir / "CareerScout-v2.4.0.exe",
                pid=12345,
                script_dir=self.dir,
            )
        text = script.read_text(encoding="utf-8-sig")
        self.assertIn("CareerScout-v2.5.0.exe", text)  # 新版就位为带版本号文件名
        self.assertIn("Remove-Item", text)  # 清理旧文件名
        self.assertIn("for ($i = 0; $i -lt 20; $i++)", text)  # 旧文件被占用时重试删除

    def test_versioned_new_target_fallback_without_version_in_name(self):
        with patch.object(updater.platform, "system", return_value="Windows"):
            _, script = updater.build_updater_script(
                installer_path=self.dir / "career-scout-installer.exe",
                install_target=self.dir / "CareerScout.exe",
                pid=1,
                script_dir=self.dir,
            )
        text = script.read_text(encoding="utf-8-sig")
        # 无法解析版本号：保持覆盖旧文件行为，不引入重命名
        self.assertIn(f"$newTarget = '{self.dir / 'CareerScout.exe'}'", text)

    def test_macos_script_uses_temp_mount_and_cleans_up(self):
        with patch.object(updater.platform, "system", return_value="Darwin"):
            _, script = updater.build_updater_script(
                installer_path=self.dir / "new.dmg",
                install_target=Path("/Applications/CareerScout.app"),
                pid=777,
                script_dir=self.dir,
            )
        text = script.read_text(encoding="utf-8")
        self.assertIn("mktemp -d", text)
        self.assertIn("trap 'rm -rf \"$MOUNT\"' EXIT", text)
        self.assertNotIn("/tmp/career-scout-update-mount", text)

    def test_clean_download_dir_keeps_recent_complete_files(self):
        downloads = self.dir / "downloads"
        downloads.mkdir()
        part = downloads / "CareerScout-v2.5.0.exe.part"
        complete = downloads / "CareerScout-v2.5.0.exe"
        part.write_bytes(b"partial")
        complete.write_bytes(b"full")
        updater.clean_download_dir(self.dir)
        self.assertFalse(part.exists())  # 半成品残留删除
        self.assertTrue(complete.exists())  # 完整安装包保留（替换脚本可能还在用）

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


class _MirrorManifestResponse:
    """镜像 manifest 假响应（.json）；GitHub 兜底假响应带 tag_name。"""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _mirror_manifest(latest="1.8.1", files=None):
    if files is None:
        files = {
            "win": {"name": "CareerScout-v1.8.1.exe", "size": 29931838,
                    "sha256": "a" * 64},
            "mac": {"name": "CareerScout-v1.8.1.dmg", "size": 23465579,
                    "sha256": "b" * 64},
        }
    return {"latest": latest, "released": "2026-08-29", "files": files}


_GITHUB_PAYLOAD = {
    "tag_name": "v1.8.1", "html_url": "https://github.com/x/y/releases/tag/v1.8.1",
    "assets": [
        {"name": "CareerScout-v1.8.1.exe",
         "browser_download_url": "https://github.com/x/y/releases/download/v1.8.1/a.exe",
         "size": 100},
        {"name": "CareerScout-v1.8.1.exe.sha256",
         "browser_download_url": "https://github.com/x/y/releases/download/v1.8.1/a.exe.sha256",
         "size": 1},
    ],
}


class MirrorFirstTests(unittest.TestCase):
    """镜像优先：有更新即用镜像；镜像不可达/非法/无更新时回退 GitHub 复核。"""

    def setUp(self):
        for target, value in (
            ("MIRROR_HOST", _TEST_MIRROR_HOST),
            ("MIRROR_BASE_URL", f"http://{_TEST_MIRROR_HOST}"),
            ("MIRROR_MANIFEST_URL", f"http://{_TEST_MIRROR_HOST}/manifest.json"),
        ):
            p = patch.object(updater, target, value)
            p.start()
            self.addCleanup(p.stop)
        # 平台固定为 windows：镜像 manifest 只有 win/mac 条目，避免在
        # Linux/CI 上 detect_update_platform 返回 other 导致无资产断言失败。
        p = patch.object(updater, "detect_update_platform", return_value="windows")
        p.start()
        self.addCleanup(p.stop)

    def test_mirror_reachable_returns_mirror_info_without_github(self):
        with patch.object(updater.requests, "get",
                          return_value=_MirrorManifestResponse(_mirror_manifest())) as get:
            info = updater.check_for_update("1.7.10")
        self.assertTrue(info.ok)
        self.assertEqual(info.latest, "1.8.1")
        self.assertTrue(info.has_update)
        self.assertEqual(info.asset_name, "CareerScout-v1.8.1.exe")
        self.assertEqual(info.asset_url, f"http://{_TEST_MIRROR_HOST}/CareerScout-v1.8.1.exe")
        self.assertEqual(info.asset_size, 29931838)
        self.assertEqual(info.sha256_url,
                         f"http://{_TEST_MIRROR_HOST}/CareerScout-v1.8.1.exe.sha256")
        self.assertTrue(info.release_url.endswith("/releases/tag/v1.8.1"))
        get.assert_called_once_with(updater.MIRROR_MANIFEST_URL, timeout=10)

    def test_mirror_up_to_date_no_update_falls_back_to_github(self):
        # 镜像 latest == 当前版本：不据此判"已最新"，回退 GitHub 复核。
        # GitHub 也说无更新 → has_update=False；防镜像滞后的锁死隐患。
        mirror = _MirrorManifestResponse(_mirror_manifest(latest="1.8.1"))
        github = _MirrorManifestResponse(_GITHUB_PAYLOAD)
        with patch.object(updater.requests, "get", side_effect=[mirror, github]) as get:
            info = updater.check_for_update("1.8.1")
        self.assertFalse(info.has_update)
        self.assertEqual(info.asset_url, "")
        self.assertEqual(get.call_count, 2, "镜像无更新时应回退 GitHub 复核")

    def test_mirror_platform_missing_reports_no_asset(self):
        files = {"win": _mirror_manifest()["files"]["win"]}
        with patch.object(updater.requests, "get",
                          return_value=_MirrorManifestResponse(_mirror_manifest(files=files))), \
             patch.object(updater, "detect_update_platform", return_value="macos"):
            info = updater.check_for_update("1.7.10")
        self.assertTrue(info.has_update)
        self.assertEqual(info.reason, "no_asset")

    def test_mirror_invalid_version_falls_back_to_github(self):
        bad = _MirrorManifestResponse(_mirror_manifest(latest="not-a-version"))
        good = _MirrorManifestResponse(_GITHUB_PAYLOAD)
        with patch.object(updater.requests, "get", side_effect=[bad, good]):
            info = updater.check_for_update("1.7.10")
        self.assertEqual(info.latest, "1.8.1")
        self.assertTrue(info.asset_url.startswith("https://github.com/"))

    def test_mirror_unreachable_falls_back_to_github(self):
        good = _MirrorManifestResponse(_GITHUB_PAYLOAD)
        with patch.object(updater.requests, "get",
                          side_effect=[requests.ConnectionError("timeout"), good]):
            info = updater.check_for_update("1.7.10")
        self.assertTrue(info.has_update)
        self.assertTrue(info.asset_url.startswith("https://github.com/"))

    def test_mirror_stale_falls_back_to_github_for_real_update(self):
        # 镜像 manifest 滞后（停在旧版本）：回退 GitHub 拿到真正的新版提示，
        # 不被镜像旧数据锁死。这是本次修复的核心场景。
        stale_mirror = _MirrorManifestResponse(_mirror_manifest(latest="1.7.10"))
        fresh_github = _MirrorManifestResponse({
            **_GITHUB_PAYLOAD,
            "tag_name": "v1.9.0",
            "html_url": "https://github.com/x/y/releases/tag/v1.9.0",
            "assets": [
                {"name": "CareerScout-v1.9.0.exe",
                 "browser_download_url": "https://github.com/x/y/releases/download/v1.9.0/a.exe",
                 "size": 200},
                {"name": "CareerScout-v1.9.0.exe.sha256",
                 "browser_download_url": "https://github.com/x/y/releases/download/v1.9.0/a.exe.sha256",
                 "size": 1},
            ],
        })
        with patch.object(updater.requests, "get", side_effect=[stale_mirror, fresh_github]):
            info = updater.check_for_update("1.8.1")
        self.assertTrue(info.has_update)
        self.assertEqual(info.latest, "1.9.0")
        self.assertTrue(info.asset_url.startswith("https://github.com/"))

    def test_explicit_fetcher_skips_mirror(self):
        fetch = lambda: _MirrorManifestResponse(_GITHUB_PAYLOAD)
        with patch.object(updater.requests, "get") as get:
            info = updater.check_for_update("1.7.10", fetcher=fetch)
        get.assert_not_called()
        self.assertEqual(info.latest, "1.8.1")

    def test_mirror_download_urls_pass_whitelist_others_rejected(self):
        self.assertTrue(updater._is_allowed_download_url(
            f"http://{_TEST_MIRROR_HOST}/CareerScout-v1.8.1.exe"))
        self.assertTrue(updater._is_allowed_download_url(
            f"http://{_TEST_MIRROR_HOST}/CareerScout-v1.8.1.exe.sha256"))
        self.assertFalse(updater._is_allowed_download_url(
            f"http://{_TEST_MIRROR_HOST}:8080/CareerScout-v1.8.1.exe"))
        self.assertFalse(updater._is_allowed_download_url(
            f"http://user@{_TEST_MIRROR_HOST}/CareerScout-v1.8.1.exe"))
        self.assertFalse(updater._is_allowed_download_url(
            f"http://{_TEST_MIRROR_HOST}.evil.com/CareerScout-v1.8.1.exe"))
        self.assertTrue(updater._is_allowed_download_url(
            "https://github.com/x/y/releases/download/v1.8.1/a.exe"))

    def test_fetch_expected_sha256_from_mirror(self):
        class Resp:
            text = ("868b9bc8e088a5f27bec4d29d115dcfbf67e1fe77abb455d5a2e1ab55f29614b"
                    "  CareerScout-v1.8.1.dmg")

            def raise_for_status(self):
                return None
        with patch.object(updater.requests, "get", return_value=Resp()):
            digest = updater.fetch_expected_sha256(
                f"http://{_TEST_MIRROR_HOST}/CareerScout-v1.8.1.dmg.sha256",
                "CareerScout-v1.8.1.dmg",
            )
        self.assertEqual(digest,
                         "868b9bc8e088a5f27bec4d29d115dcfbf67e1fe77abb455d5a2e1ab55f29614b")


class StateDirEnvTests(unittest.TestCase):
    """BOSS_WEBUI_STATE_DIR 环境变量真实生效（031 B4 / FR-014）。"""

    def test_boss_webui_state_dir_overrides_default_state_dir(self):
        import importlib

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"BOSS_WEBUI_STATE_DIR": tmp}):
                reloaded = importlib.reload(updater)
                try:
                    self.assertEqual(reloaded.DEFAULT_STATE_DIR, Path(tmp))
                finally:
                    importlib.reload(updater)  # 还原模块级状态，避免影响其他用例


if __name__ == "__main__":
    unittest.main()
