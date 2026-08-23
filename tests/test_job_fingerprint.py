"""019 T002：岗位指纹归一化表驱动单测（spec FR-002 / US1 验收）。"""

from __future__ import annotations

import unittest

from webui.job_fingerprint import (
    build_fingerprint_index,
    fingerprint,
    normalize_city,
    normalize_company,
    normalize_title,
)


class NormalizeTitleTests(unittest.TestCase):
    CASES = [
        ("Python开发", "python开发"),
        ("python 开发", "python开发"),
        ("Ｐｙｔｈｏｎ开发", "python开发"),  # 全角字母
        ("PYTHON 开发", "python开发"),
        (" 高级  后端 工程师 ", "高级后端工程师"),
        ("", ""),
        (None, ""),
    ]

    def test_table(self):
        for raw, expected in self.CASES:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_title(raw), expected)


class NormalizeCityTests(unittest.TestCase):
    CASES = [
        ("北京·朝阳区", "北京"),
        ("北京", "北京"),
        ("北京市", "北京"),
        ("北京-朝阳", "北京"),
        ("北京／朝阳区", "北京"),
        ("上海·浦东新区", "上海"),
        ("广州市", "广州"),
        ("", ""),
        (None, ""),
        ("···", ""),  # 只剩分隔符 → 空（取不出市级）
    ]

    def test_table(self):
        for raw, expected in self.CASES:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_city(raw), expected)


class NormalizeCompanyTests(unittest.TestCase):
    CASES = [
        # (原始, 当前城市, 期望)
        ("北京字节跳动科技有限公司", "北京", "字节跳动"),  # spec US1 验收对
        ("字节跳动", "北京", "字节跳动"),
        ("北京字节跳动科技有限公司", "", "北京字节跳动"),  # 无城市：不剥城市前缀
        ("字节跳动（北京）", "北京", "字节跳动"),
        ("字节跳动【官网】", "北京", "字节跳动"),
        ("某某集团股份有限公司", "上海", "某某"),
        ("某某集团", "上海", "某某"),
        ("华为技术有限公司", "深圳", "华为"),
        ("ＡＢＣ Ｉｎｃ", "北京", "abc"),
        ("ABC inc.", "北京", "abc"),
        ("上海某某网络科技有限公司", "上海", "某某"),  # 行业词循环剥（网络+科技）
        ("上海某某教育", "上海", "某某"),
    ]

    def test_table(self):
        for raw, city, expected in self.CASES:
            with self.subTest(raw=raw, city=city):
                self.assertEqual(normalize_company(raw, city), expected)

    def test_city_prefix_stripped_only_for_current_city(self):
        """城市前缀仅剥当前城市（T002 边界）：公司带别的城市前缀不剥。"""
        self.assertEqual(normalize_company("北京字节跳动", "上海"), "北京字节跳动")
        self.assertEqual(normalize_company("北京字节跳动", "北京"), "字节跳动")

    def test_suffix_stripped_only_at_end(self):
        """组织后缀仅在末尾才剥（T002 边界）：中间出现不剥。"""
        self.assertEqual(normalize_company("有限公司某某", "北京"), "有限公司某某")
        # 后缀剥到只剩后缀本身时不剥（避免归一成空公司名）。
        self.assertEqual(normalize_company("有限公司", "北京"), "有限公司")

    def test_no_cross_core_merge(self):
        """不做互含/相似度：核心名不同不合并。"""
        self.assertNotEqual(
            normalize_company("北京字节跳动科技有限公司", "北京"),
            normalize_company("北京飞书科技有限公司", "北京"),
        )
        self.assertNotEqual(
            normalize_company("字节跳动科技", "北京"),
            normalize_company("飞书科技", "北京"),
        )
        # 同核心不同行业词属同一雇主家族写法差异 → 按设计合并（漏判优先于误合）。
        self.assertEqual(
            normalize_company("字节跳动科技有限公司", "北京"),
            normalize_company("字节跳动信息有限公司", "北京"),
        )


class FingerprintTests(unittest.TestCase):
    def test_equivalent_jobs_share_fingerprint(self):
        boss_job = {
            "company": "北京字节跳动科技有限公司", "title": "Python开发",
            "location": "北京·朝阳区",
        }
        zhilian_job = {
            "company": "字节跳动", "title": "python 开发", "location": "北京",
        }
        self.assertEqual(fingerprint(boss_job), fingerprint(zhilian_job))
        self.assertEqual(
            fingerprint(boss_job), ("字节跳动", "python开发", "北京"))

    def test_any_empty_component_yields_none(self):
        """三元组任一空 → 无指纹不参与判定（含城市取不出）。"""
        self.assertIsNone(fingerprint({"company": "", "title": "工程师",
                                       "location": "北京"}))
        self.assertIsNone(fingerprint({"company": "公司", "title": "",
                                       "location": "北京"}))
        self.assertIsNone(fingerprint({"company": "公司", "title": "工程师",
                                       "location": "··"}))
        self.assertIsNone(fingerprint({}))
        self.assertIsNone(fingerprint(None))
        self.assertIsNone(fingerprint("not-a-dict"))

    def test_no_false_merge_on_title_suffix_or_city(self):
        base = {"company": "北京字节跳动科技有限公司", "title": "Python开发",
                "location": "北京"}
        titled = {**base, "title": "Python开发工程师"}  # 标题后缀差异 → 漏判不合并
        self.assertNotEqual(fingerprint(base), fingerprint(titled))
        other_city = {**base, "location": "上海"}  # 城市不同不合并
        self.assertNotEqual(fingerprint(base), fingerprint(other_city))

    def test_boss_name_fallback(self):
        """BOSS 原始岗位可能只有 boss_name：与 company 等价。"""
        via_boss_name = {
            "boss_name": "北京字节跳动科技有限公司", "title": "Python开发",
            "location": "北京",
        }
        via_company = {
            "company": "字节跳动", "title": "python开发", "location": "北京市",
        }
        self.assertEqual(fingerprint(via_boss_name), fingerprint(via_company))


class BuildIndexTests(unittest.TestCase):
    def test_first_occurrence_wins_and_fingerprintless_skipped(self):
        first = {"company": "字节跳动", "title": "python开发", "location": "北京",
                 "platform_job_id": "boss-1"}
        second = {"company": "字节跳动", "title": "python开发", "location": "北京",
                  "platform_job_id": "boss-2"}
        no_fp = {"company": "", "title": "x", "location": ""}
        index = build_fingerprint_index([first, second, no_fp, None])
        self.assertEqual(len(index), 1)
        self.assertIs(index[fingerprint(first)], first)

    def test_empty_input(self):
        self.assertEqual(build_fingerprint_index([]), {})
        self.assertEqual(build_fingerprint_index(None), {})


if __name__ == "__main__":
    unittest.main()
