"""profile_facts 契约 / 校验 / 描述聚焦测试（spec 015 US2·B062）。

覆盖 FR-005~FR-008：
- 主观字段未体现按最大接受度（flex_*）
- degree_type 默认统招，仅明确非统招才填非统招（专升本属统招）
- 客观字段有证据才填；validate 不注入默认值（默认由描述层 flex 呈现）
"""

import unittest

from webui.profile_facts import (
    DEFAULT_JOB_TYPE,
    DEFAULT_OVERTIME,
    DEFAULT_WEEK_OFF,
    build_profile_facts_description,
    calculate_experience_years,
    derive_profile_facts,
    flex_degree_type,
    flex_job_type,
    flex_overtime,
    flex_week_off,
    normalize_degree_type,
    validate_profile_facts,
)


class ValidateProfileFactsTests(unittest.TestCase):
    """宽松校验：类型 + 长度，无效项丢弃，不注入默认值。"""

    def test_keeps_valid_items(self):
        facts = validate_profile_facts({
            "core_skills": ["Python", "Django"],
            "projects": [{"name": "P", "role": "后端"}],
            "job_type": "全职",
            "degree": "本科",
            "degree_type": "非统招",
            "languages": ["英语"],
            "week_off": "双休",
            "overtime": "能够加班",
        })
        self.assertEqual(facts["core_skills"], ["Python", "Django"])
        # 「非统招」是显式合法枚举；machine 归一化只认明确非统招标志。
        self.assertEqual(facts["degree_type"], "非统招")
        # 040 批三迁移：显式 degree 保留（原 test_ai_match 集成侧断言）
        self.assertEqual(facts["degree"], "本科")
        self.assertEqual(facts["week_off"], "双休")
        self.assertEqual(facts["overtime"], "能够加班")

    def test_drops_invalid_items(self):
        facts = validate_profile_facts({
            "core_skills": ["Python", 123, ""],
            "projects": [{"stack": "无name"}, "not-dict"],
            "job_type": "不限",  # 非法枚举
            "languages": [None, 5],
            "week_off": 42,
        })
        self.assertEqual(facts["core_skills"], ["Python"])
        self.assertNotIn("projects", facts)
        self.assertNotIn("job_type", facts)
        self.assertNotIn("languages", facts)
        self.assertNotIn("week_off", facts)
        # 040 批三迁移：空/非字符串 degree 丢弃（原 test_ai_match 集成侧断言）
        self.assertNotIn("degree", validate_profile_facts({"degree": ""}))
        self.assertNotIn("degree", validate_profile_facts({"degree": 123}))

    def test_missing_fields_not_injected(self):
        """B062：validate 不把默认值写成存证——未体现保持未体现。"""
        self.assertEqual(validate_profile_facts(None), {})
        self.assertEqual(validate_profile_facts("x"), {})
        self.assertEqual(validate_profile_facts({}), {})
        self.assertNotIn("degree_type", validate_profile_facts({"degree": "本科"}))
        self.assertNotIn("week_off", validate_profile_facts({}))
        self.assertNotIn("overtime", validate_profile_facts({}))

    def test_unrepresented_preference_sentinels_are_treated_as_missing(self):
        facts = validate_profile_facts({
            "week_off": "未体现",
            "overtime": "未知",
        })
        self.assertNotIn("week_off", facts)
        self.assertNotIn("overtime", facts)
        # 040 批三迁移：job_type「未体现」显式保留（与 week_off/overtime 处理不同）
        self.assertEqual(
            validate_profile_facts({"job_type": "未体现"}), {"job_type": "未体现"})

    def test_degree_type_defaults_to_tongzhao(self):
        """FR-006：degree_type 默认「统招」，仅明确非统招标志才填「非统招」。"""
        self.assertEqual(normalize_degree_type(None), "统招")
        self.assertEqual(normalize_degree_type(""), "统招")
        self.assertEqual(normalize_degree_type("专升本"), "统招")
        self.assertEqual(normalize_degree_type("先专后本"), "统招")
        self.assertEqual(normalize_degree_type("自考"), "非统招")
        self.assertEqual(normalize_degree_type("成考"), "非统招")
        self.assertEqual(normalize_degree_type("函授"), "非统招")
        self.assertEqual(normalize_degree_type("夜校本科"), "非统招")
        # 040 批三迁移：含「本科」字样的非统招标志仍归一为非统招
        self.assertEqual(normalize_degree_type("自考本科"), "非统招")

    def test_non_tongzhao_preserved_in_facts(self):
        facts = validate_profile_facts({"degree_type": "自考"})
        self.assertEqual(facts["degree_type"], "非统招")

    def test_keeps_structured_employment_and_education_facts(self):
        facts = validate_profile_facts({
            "employment_history": [{
                "company": "甲科技",
                "role": "后端工程师",
                "start_date": "2022年07月",
                "end_date": "2024-06",
                "evidence": "任职经历：甲科技",
                "confidence": 0.93,
            }],
            "education_history": [{
                "school": "甲大学",
                "degree": "本科",
                "major": "计算机科学与技术",
                "graduation_date": "2022.06",
            }],
            "work_pattern": "996",
            "experience_years": 2,
        })

        self.assertEqual(facts["employment_history"][0]["start_date"], "2022-07")
        self.assertEqual(facts["employment_history"][0]["end_date"], "2024-06")
        self.assertEqual(facts["education_history"][0]["graduation_date"], "2022-06")
        self.assertEqual(facts["work_pattern"], "996")
        self.assertEqual(facts["experience_years"], 2.0)

    def test_drops_invalid_structured_fact_items(self):
        facts = validate_profile_facts({
            "employment_history": [
                {"company": "", "start_date": "not-a-date"},
                {"company": "有效公司", "start_date": "2022-01"},
                "invalid",
            ],
            "education_history": [{"school": 123, "graduation_date": "2022"}],
            "experience_years": "很多年",
            "work_pattern": 996,
        })

        self.assertEqual(len(facts["employment_history"]), 1)
        self.assertEqual(facts["employment_history"][0]["company"], "有效公司")
        self.assertNotIn("education_history", facts)
        self.assertNotIn("experience_years", facts)
        self.assertNotIn("work_pattern", facts)


class FlexDefaultsTests(unittest.TestCase):
    """主观字段未体现时的最大接受度（FR-008）。"""

    def test_job_type_default_unrestricted(self):
        self.assertEqual(flex_job_type({}), DEFAULT_JOB_TYPE)
        self.assertEqual(flex_job_type({"job_type": "全职"}), "全职")
        self.assertEqual(flex_job_type({"job_type": "未体现"}), DEFAULT_JOB_TYPE)

    def test_week_off_default_single(self):
        self.assertEqual(flex_week_off({}), DEFAULT_WEEK_OFF)
        self.assertEqual(flex_week_off({"week_off": "双休"}), "双休")

    def test_overtime_default_can_work(self):
        self.assertEqual(flex_overtime({}), DEFAULT_OVERTIME)
        self.assertEqual(flex_overtime({"overtime": "弹性"}), "弹性")

    def test_degree_type_flex(self):
        self.assertEqual(flex_degree_type({}), "统招")
        self.assertEqual(flex_degree_type({"degree_type": "非统招"}), "非统招")


class BuildDescriptionTests(unittest.TestCase):
    """build_profile_facts_description：兼容旧空输入语义 + 新字段展示。"""

    def test_empty_falls_back(self):
        self.assertEqual(
            build_profile_facts_description(None),
            "（无画像事实，按未体现处理）",
        )
        self.assertEqual(
            build_profile_facts_description({}),
            "（无画像事实，按未体现处理）",
        )

    def test_explicit_facts_rendered(self):
        desc = build_profile_facts_description({
            "core_skills": ["Python"],
            "degree": "本科",
            "degree_type": "非统招",
            "week_off": "双休",
        })
        self.assertIn("核心技能：Python", desc)
        self.assertIn("学历层次：本科", desc)
        self.assertIn("学历类型：非统招", desc)
        self.assertNotIn("学历类型：非统招（默认）", desc)
        self.assertIn("作息：双休", desc)

    def test_subject_default_marked_as_default(self):
        """未体现的主观字段在描述中标注「（默认）」（最大接受度）。"""
        desc = build_profile_facts_description({"degree": "本科"})
        self.assertIn("学历类型：统招（默认）", desc)
        desc2 = build_profile_facts_description({})
        # 空 facts 走兜底文案，不出现默认标注（无事实可描述时按未体现）。
        self.assertNotIn("（默认）", desc2)

    def test_legacy_preference_sentinels_render_as_defaults(self):
        desc = build_profile_facts_description({
            "degree": "本科",
            "week_off": "未体现",
            "overtime": "未体现",
        })
        self.assertIn("作息：单休（默认）", desc)
        self.assertIn("加班态度：能够加班（默认）", desc)

    def test_structured_facts_description_contains_judgments(self):
        facts = derive_profile_facts(validate_profile_facts({
            "employment_history": [
                {"company": "甲科技", "role": "后端", "start_date": "2022-01", "end_date": "2023-12"},
                {"company": "乙网络", "role": "工程师", "start_date": "2024-01", "end_date": "至今"},
            ],
            "education_history": [{
                "school": "甲大学", "degree": "本科", "graduation_date": "2021-06",
            }],
            "work_pattern": "996",
        }), as_of="2024-12")

        desc = build_profile_facts_description(facts)
        self.assertIn("任职经历：甲科技（后端，2022-01 至 2023-12）；乙网络（工程师，2024-01 至今）", desc)
        self.assertIn("累计工作经验：3.0年", desc)
        self.assertIn("毕业时间：2021-06", desc)
        self.assertIn("工作制度：996", desc)


class ExperienceCalculationTests(unittest.TestCase):
    def test_merges_overlapping_employment_intervals(self):
        years = calculate_experience_years([
            {"start_date": "2020-01", "end_date": "2021-12"},
            {"start_date": "2021-06", "end_date": "2022-06"},
        ], as_of="2024-01")
        self.assertEqual(years, 2.5)

    def test_current_flag_uses_reference_month_when_end_date_is_missing(self):
        years = calculate_experience_years([
            {"start_date": "2024-01", "current": True},
        ], as_of="2024-12")
        self.assertEqual(years, 1.0)

    def test_year_only_dates_use_full_calendar_years(self):
        years = calculate_experience_years([
            {"start_date": "2022", "end_date": "2024"},
        ])
        self.assertEqual(years, 3.0)


if __name__ == "__main__":
    unittest.main()
