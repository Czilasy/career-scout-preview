# -*- coding: utf-8 -*-
"""发布镜像使用的版本摘要测试。"""

from __future__ import annotations

import unittest

from scripts.release_summary import build_release_summary


class ReleaseSummarySourceTests(unittest.TestCase):
    def test_current_changelog_produces_user_facing_items_only(self):
        summary = build_release_summary("1.8.2")

        self.assertEqual(summary["version"], "1.8.2")
        self.assertGreaterEqual(len(summary["release_items"]), 3)
        self.assertLessEqual(len(summary["release_items"]), 8)
        self.assertTrue(all("安装包" not in item for item in summary["release_items"]))
        self.assertTrue(all("SHA256" not in item for item in summary["release_items"]))
        self.assertIn("更新提示只展示本次更新最重要的内容", "\n".join(summary["release_items"]))

    def test_missing_version_fails_before_publish(self):
        with self.assertRaises(ValueError):
            build_release_summary("9.9.9")


if __name__ == "__main__":
    unittest.main()
