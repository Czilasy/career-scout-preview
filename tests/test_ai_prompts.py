"""ai_prompts / prompt_texts 组装聚焦测试（spec 015 US2·B062）。

覆盖 FR-009（match 提示词删除第四层默认偏好）与 FR-006（简历分析
提示词含字段填写说明书），以及宽松匹配语义。
"""

import unittest

from webui.ai_prompts import build_match_system_prompt, build_resume_analysis_prompt


class BuildResumeAnalysisPromptTests(unittest.TestCase):
    def test_includes_field_fill_book(self):
        prompt = build_resume_analysis_prompt("keyword: 示例\ncity: 示例")
        self.assertIn("逐字段填写说明书", prompt)
        self.assertIn("degree_type", prompt)
        self.assertIn("week_off", prompt)
        self.assertIn("overtime", prompt)
        self.assertIn("default", prompt.replace("默认\"统招\"", "default"))

    def test_degree_type_rule_mentions_default_and_exclusions(self):
        prompt = build_resume_analysis_prompt("x")
        self.assertIn("默认\"统招\"", prompt)
        self.assertIn("自考/成考/函授/夜校", prompt)
        self.assertIn("专升本", prompt)

    def test_no_hardcoded_fulltime_only_preference(self):
        """旧「只找全职，兼职不考虑」的硬默认已随第四层移除。"""
        prompt = build_resume_analysis_prompt("x")
        self.assertNotIn("只找全职", prompt)
        self.assertNotIn("不接受996", prompt)
        self.assertNotIn("期望双休", prompt)


class BuildMatchSystemPromptTests(unittest.TestCase):
    def _prompt(self):
        return build_match_system_prompt(
            criteria_desc="（无明确标准，宽松判断）",
            profile_summary="3年Python后端",
            facts_desc="学历类型：统招（默认）；作息：单休（默认）",
            features_prompt_text="岗位靠谱特征清单",
        )

    def test_layers_present(self):
        prompt = self._prompt()
        self.assertIn("【第一层·筛选条件】", prompt)
        self.assertIn("【第二层·求职画像】", prompt)
        self.assertIn("【第三层·隐藏画像字段】", prompt)

    def test_fourth_layer_removed(self):
        """FR-009：第四层「默认偏好」整段删除。"""
        prompt = self._prompt()
        self.assertNotIn("第四层", prompt)
        self.assertNotIn("默认偏好", prompt)
        # 旧硬默认短语（“只找全职，兼职/外包/按单结算不考虑”）整句移除；
        # 新文案仅在“画像明确只接受全职时”语境保留“全职”二字，不做硬默认。
        self.assertNotIn("只找全职，兼职/外包/按单结算不考虑", prompt)
        self.assertNotIn("不接受996", prompt)
        self.assertNotIn("期望双休", prompt)

    def test_lenient_subjective_preferences(self):
        """宽松匹配：JD 更苛刻时记 caveats，不判不匹配。"""
        prompt = self._prompt()
        self.assertIn("主观偏好", prompt)
        self.assertIn("最大接受度", prompt)
        self.assertIn("（默认）", prompt)
        self.assertIn("不得判不匹配", prompt)
        self.assertIn("实习/兼职与全职冲突", prompt)

    def test_hard_rules_keep(self):
        """六类硬条件与高危 flag 照常硬约束。"""
        prompt = self._prompt()
        self.assertIn("六类字段", prompt)
        self.assertIn("疑似骗局", prompt)
        self.assertIn("统招公办本科", prompt)


if __name__ == "__main__":
    unittest.main()