"""webui.recruiter_activity 判定域测试（028 B081 第 7 类筛选条件）。

覆盖：Boss 文本→天数区间映射（2026-08-29 实测值域）、智联时间戳→精确天数、
四档×三事实形态判定矩阵（下界超档拦/上界跨档不拦/精确超档拦/未知不拦）、
人话距离格式化、判定说明模板、未知收集与 caveat 合并助手。
"""

from __future__ import annotations

import time
import unittest

from webui import recruiter_activity as ra


def _fact(source, text, lower, upper, *, known=True, last_online_ms=None):
    return {
        "source": source,
        "text": text,
        "last_online_ms": last_online_ms,
        "age_lower_days": lower,
        "age_upper_days": upper,
        "known": known,
    }


# ---------------------------------------------------------------------------
# 常量与档位表
# ---------------------------------------------------------------------------
class FieldConstantsTests(unittest.TestCase):
    def test_field_key_and_thresholds(self):
        self.assertEqual(ra.FIELD_KEY, "recruiter_activity")
        self.assertEqual(
            ra.THRESHOLD_DAYS,
            {"week": 7, "month": 30, "quarter": 90, "half_year": 180},
        )

    def test_threshold_labels(self):
        self.assertEqual(
            ra.FIELD_LABELS,
            {
                "week": "近一周",
                "month": "近一个月",
                "quarter": "近三个月",
                "half_year": "近半年",
            },
        )
        self.assertEqual(
            ra.DAYS_TO_LABEL,
            {7: "近一周", 30: "近一个月", 90: "近三个月", 180: "近半年"},
        )


# ---------------------------------------------------------------------------
# Boss 文本归一化（实测值域 2026-08-29：18 详情页 7 种文本 + 无名片形态）
# ---------------------------------------------------------------------------
class BossNormalizeTests(unittest.TestCase):
    def _norm(self, text):
        return ra.normalize_detail_activity(
            "boss", {"recruiter_activity_text": text}
        )

    def test_upper_bound_texts(self):
        cases = {
            "在线": (0, 0),
            "刚刚活跃": (0, 0),
            "今日活跃": (0, 1),
            "昨日活跃": (1, 2),
            "3日内活跃": (0, 3),
            "2周内活跃": (0, 14),
            "3月内活跃": (0, 90),
        }
        for text, (lower, upper) in cases.items():
            fact = self._norm(text)
            self.assertTrue(fact["known"], text)
            self.assertEqual(fact["source"], "boss", text)
            self.assertEqual(fact["text"], text, text)
            self.assertEqual(fact["age_lower_days"], lower, text)
            self.assertEqual(fact["age_upper_days"], upper, text)
            self.assertIsNone(fact["last_online_ms"], text)

    def test_lower_bound_texts(self):
        cases = {
            "半年前活跃": 180,
            "2月前活跃": 60,
            "5年前活跃": 1825,
        }
        for text, lower in cases.items():
            fact = self._norm(text)
            self.assertTrue(fact["known"], text)
            self.assertEqual(fact["age_lower_days"], lower, text)
            self.assertIsNone(fact["age_upper_days"], text)

    def test_unknown_text_falls_back(self):
        fact = self._norm("长期未上线")
        self.assertFalse(fact["known"])
        self.assertEqual(fact["text"], "长期未上线")

    def test_empty_text_unknown(self):
        fact = self._norm("")
        self.assertFalse(fact["known"])

    def test_non_string_text_unknown_without_raise(self):
        fact = self._norm(None)
        self.assertFalse(fact["known"])

    def test_missing_keys_returns_none(self):
        self.assertIsNone(ra.normalize_detail_activity("boss", {}))
        self.assertIsNone(ra.normalize_detail_activity("boss", {"other": 1}))

    def test_unknown_platform_returns_none(self):
        self.assertIsNone(
            ra.normalize_detail_activity(
                "liepin", {"recruiter_activity_text": "在线"}
            )
        )


# ---------------------------------------------------------------------------
# 智联时间戳归一化（2026-08-28 实测：判定以 lastOnlineTime 为准，文本仅展示）
# ---------------------------------------------------------------------------
class ZhilianNormalizeTests(unittest.TestCase):
    def test_exact_age_from_ms(self):
        now_ms = time.time() * 1000
        ts = now_ms - 10 * 86400 * 1000
        fact = ra.normalize_detail_activity(
            "zhilian",
            {
                "recruiter_last_online_ms": ts,
                "recruiter_activity_text": "今日活跃",
            },
        )
        self.assertTrue(fact["known"])
        self.assertEqual(fact["source"], "zhilian")
        self.assertEqual(fact["text"], "今日活跃")
        self.assertAlmostEqual(fact["last_online_ms"], ts, delta=1)
        self.assertAlmostEqual(fact["age_lower_days"], 10.0, delta=0.01)
        self.assertAlmostEqual(fact["age_upper_days"], 10.0, delta=0.01)

    def test_bad_ms_unknown_without_raise(self):
        for bad in (None, "abc", [], ""):
            fact = ra.normalize_detail_activity(
                "zhilian", {"recruiter_last_online_ms": bad}
            )
            self.assertFalse(fact["known"], repr(bad))

    def test_negative_age_clamped_to_zero(self):
        now_ms = time.time() * 1000
        fact = ra.normalize_detail_activity(
            "zhilian", {"recruiter_last_online_ms": now_ms + 3600 * 1000}
        )
        self.assertTrue(fact["known"])
        self.assertEqual(fact["age_lower_days"], 0)

    def test_missing_keys_returns_none(self):
        self.assertIsNone(ra.normalize_detail_activity("zhilian", {}))


# ---------------------------------------------------------------------------
# 档位判定矩阵
# ---------------------------------------------------------------------------
class EvaluateTests(unittest.TestCase):
    def test_lower_bound_exceeds_blocks(self):
        verdict = ra.evaluate(
            _fact("boss", "半年前活跃", 180, None), 7
        )
        self.assertEqual(verdict["verdict"], "not_match")
        self.assertIn("半年前", verdict["reason"])
        self.assertIn("近一周", verdict["reason"])

    def test_exact_over_blocks_with_humanized_distance(self):
        verdict = ra.evaluate(
            _fact("zhilian", "", 200.0, 200.0), 90
        )
        self.assertEqual(verdict["verdict"], "not_match")
        self.assertIn("6 个月前", verdict["reason"])
        self.assertIn("近三个月", verdict["reason"])

    def test_exact_boundary_not_over(self):
        # 「距今超过所选档位」为严格大于：下界等于档位不确定，不拦
        self.assertIsNone(ra.evaluate(_fact("boss", "半年前活跃", 180, None), 180))

    def test_interval_crossing_not_blocks(self):
        # 2周内活跃 [0,14] vs 近一周：可能 3 天也可能 10 天，保守不拦
        self.assertIsNone(ra.evaluate(_fact("boss", "2周内活跃", 0, 14), 7))

    def test_within_upper_not_blocks(self):
        self.assertIsNone(ra.evaluate(_fact("boss", "3日内活跃", 0, 3), 7))
        self.assertIsNone(ra.evaluate(_fact("boss", "3月内活跃", 0, 90), 180))

    def test_unknown_never_blocks(self):
        self.assertIsNone(ra.evaluate(_fact("boss", "长期未上线", None, None, known=False), 7))
        self.assertIsNone(ra.evaluate(None, 7))
        self.assertIsNone(ra.evaluate({}, 7))
        self.assertIsNone(ra.evaluate("junk", 7))

    def test_unknown_threshold_label_fallback(self):
        verdict = ra.evaluate(_fact("zhilian", "", 200.0, 200.0), 45)
        self.assertIn("所选档位", verdict["reason"])


# ---------------------------------------------------------------------------
# 人话距离
# ---------------------------------------------------------------------------
class HumanizeDaysTests(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(ra.humanize_days(3), "3 天前")
        self.assertEqual(ra.humanize_days(13), "13 天前")
        self.assertEqual(ra.humanize_days(14), "2 周前")
        self.assertEqual(ra.humanize_days(59), "8 周前")
        self.assertEqual(ra.humanize_days(60), "2 个月前")
        self.assertEqual(ra.humanize_days(200), "6 个月前")
        self.assertEqual(ra.humanize_days(364), "12 个月前")
        self.assertEqual(ra.humanize_days(365), "1 年前")
        self.assertEqual(ra.humanize_days(800), "2 年前")


# ---------------------------------------------------------------------------
# 未知收集与 caveat 合并（US2：拿不到数据不误拦）
# ---------------------------------------------------------------------------
class UnknownHelpersTests(unittest.TestCase):
    def test_collects_only_unknown_when_selected(self):
        jobs = [
            {"job_id": "a", "extra": {"recruiter_activity": _fact("boss", "x", None, None, known=False)}},
            {"job_id": "b", "extra": {}},  # 无事实（存量）
            {"job_id": "c", "extra": {"recruiter_activity": _fact("boss", "刚刚活跃", 0, 0)}},
            {"job_id": "d"},  # 连 extra 都没有
        ]
        ids = ra.unknown_job_ids(jobs, {"recruiter_activity": "week"})
        self.assertEqual(ids, {"a", "b", "d"})

    def test_no_collection_without_selected_threshold(self):
        jobs = [{"job_id": "a", "extra": {}}]
        self.assertEqual(ra.unknown_job_ids(jobs, {}), set())
        self.assertEqual(ra.unknown_job_ids(jobs, {"salary": ["406"]}), set())

    def test_merge_unknown_caveat(self):
        verdict = ra.merge_unknown_caveat({"verdict": "match", "reason": "r"})
        self.assertEqual(verdict["caveats"], ["招聘者活跃时间未知，未按第 7 类拦截"])

    def test_merge_unknown_caveat_appends_without_dup(self):
        verdict = {"verdict": "match", "reason": "r", "caveats": ["已有提示"]}
        verdict = ra.merge_unknown_caveat(verdict)
        self.assertEqual(verdict["caveats"], ["已有提示", "招聘者活跃时间未知，未按第 7 类拦截"])
        verdict = ra.merge_unknown_caveat(verdict)
        self.assertEqual(verdict["caveats"].count("招聘者活跃时间未知，未按第 7 类拦截"), 1)

    def test_merge_unknown_caveat_non_dict_noop(self):
        self.assertEqual(ra.merge_unknown_caveat(None), None)


if __name__ == "__main__":
    unittest.main()
