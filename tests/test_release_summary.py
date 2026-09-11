# -*- coding: utf-8 -*-
"""发布镜像使用的版本摘要测试。"""

from __future__ import annotations

import unittest

from scripts.release_summary import _reject_overlong_items, build_release_summary


class ReleaseSummaryLengthGateTests(unittest.TestCase):
    """更新说明每条必须一行说清，超长即视为写得太细，发布前拦下。"""

    def test_overlong_item_blocks_publish_with_item_named(self):
        overlong = "优化：" + "这件事的解释" * 6
        with self.assertRaises(ValueError) as ctx:
            _reject_overlong_items(["修复：抓取中断后不再丢岗位", overlong])
        self.assertIn("写得太细", str(ctx.exception))
        self.assertIn(overlong, str(ctx.exception))

    def test_reasonable_items_pass_the_gate(self):
        _reject_overlong_items([
            "增加：全新的万花筒彩蛋主题，可长按顶部入口切换",
            "优化：浏览器与账号入口合并，切换账号更顺手",
            "修复：抓取中断后不再丢岗位",
        ])

    def test_missing_version_fails_before_publish(self):
        with self.assertRaises(ValueError):
            build_release_summary("9.9.9")


if __name__ == "__main__":
    unittest.main()
