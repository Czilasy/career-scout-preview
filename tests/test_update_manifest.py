# -*- coding: utf-8 -*-
"""更新镜像 manifest 合并逻辑测试。"""

from __future__ import annotations

import base64
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from scripts.update_manifest import _write_manifest, merge_manifest


def _summary(version: str = "1.8.2", items=None) -> str:
    payload = {"version": version, "release_items": items or [
        "增加：新功能",
        "优化：使用更顺手",
        "修复：状态显示更准确",
    ]}
    return base64.b64encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")


class UpdateManifestTests(unittest.TestCase):
    def test_merge_updates_asset_and_version_bound_summary(self):
        manifest = {
            "latest": "1.8.1",
            "release_notes": "旧版本说明",
            "files": {"mac": {"name": "CareerScout-v1.8.1.dmg"}},
        }

        result = merge_manifest(
            manifest, "1.8.2", "win", "CareerScout-v1.8.2.exe",
            "a" * 64, 123, _summary(),
        )

        self.assertEqual(result["latest"], "1.8.2")
        self.assertEqual(result["files"]["win"]["name"], "CareerScout-v1.8.2.exe")
        self.assertEqual(result["files"]["mac"]["name"], "CareerScout-v1.8.1.dmg")
        self.assertEqual(result["release_notes_version"], "1.8.2")
        self.assertEqual(len(result["release_items"]), 3)
        self.assertEqual(result["release_notes"], "• 增加：新功能\n• 优化：使用更顺手\n• 修复：状态显示更准确")

    def test_merge_rejects_summary_for_another_version(self):
        with self.assertRaises(ValueError):
            merge_manifest(
                {}, "1.8.2", "win", "CareerScout-v1.8.2.exe",
                "a" * 64, 123, _summary("1.8.1"),
            )

    def test_written_manifest_is_readable_by_public_web_server(self):
        if os.name == "nt":
            self.skipTest("Windows 不保留 Unix manifest 权限位；由 Linux 镜像端验证")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            _write_manifest(path, {})

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)


if __name__ == "__main__":
    unittest.main()
