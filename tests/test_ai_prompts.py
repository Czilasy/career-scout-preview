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

    def test_resume_prompt_copy_migrated_from_integration_cases(self):
        """040 审查补丁（2026-09-12）：批三收敛时未迁移的集成侧简历提示词断言。"""
        prompt = build_resume_analysis_prompt("keyword: 示例\ncity: 示例")
        self.assertIn("必须固定输出以下五个段落", prompt)
        for section in (
            "1. 求职方向：",
            "2. 核心能力：",
            "3. 工作与项目经历：",
            "4. 学历与基本条件：",
            "5. 岗位偏好与排除项：",
        ):
            self.assertIn(section, prompt)
        self.assertIn("冒号后只能写无", prompt)
        self.assertIn("自然语言", prompt)
        self.assertIn("简历里明确写了就填，没写的字段留空", prompt)
        self.assertIn("简历写了什么就写什么，没写的不补", prompt)
        self.assertIn("projects：只列简历明确写出的项目/工作/实习经历", prompt)
        self.assertIn("不得因为技能、经历或职业方向自行推断偏好和排除项", prompt)
        self.assertIn("工作/项目方向、个人角色和所用技术栈", prompt)
        self.assertIn("summary 只写简历明确给出的职责或成果一句话", prompt)
        self.assertNotIn("最终总共5-10句", prompt)
        self.assertNotIn("随机挑1-3个自然补充", prompt)
        self.assertNotIn("不一次全塞", prompt)


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
        self.assertIn("工作制度", prompt)
        self.assertIn("双休/单休/大小周/996", prompt)

    def test_profile_summary_overrides_hidden_facts_for_intent(self):
        prompt = self._prompt()
        self.assertIn("求职画像优先于隐藏画像字段", prompt)
        self.assertIn("隐藏画像字段不得覆盖求职画像", prompt)
        self.assertIn("冲突时按求职画像执行", prompt)
        self.assertIn("结构化筛选条件 > 求职画像 > 隐藏画像字段", prompt)
        self.assertIn("简历事实", prompt)
        self.assertIn("系统计算", prompt)

    def test_prompt_lists_structured_resume_judgment_fields(self):
        prompt = build_resume_analysis_prompt("x")
        for field in (
            "employment_history", "education_history", "start_date",
            "end_date", "graduation_date", "experience_years", "work_pattern",
        ):
            self.assertIn(field, prompt)

    def test_hard_rules_keep(self):
        """无领域选择时六类硬条件与高危 flag 保持旧口径。"""
        prompt = self._prompt()
        self.assertIn("六类字段", prompt)
        self.assertIn("疑似骗局", prompt)
        self.assertIn("统招公办本科", prompt)

    def test_match_prompt_keeps_integration_copy(self):
        """040 批三迁移：原 test_ai_match 集成侧独有文案断言归正本统一保护。"""
        prompt = self._prompt()
        self.assertIn("最高优先级，绝对硬约束", prompt)
        self.assertIn("已确认的筛选条件", prompt)
        self.assertIn("不得只写 caveats 后仍判 match", prompt)
        self.assertIn("薪资筛选区间已由系统硬性核对", prompt)
        self.assertIn("JD 正文硬要求优先于标题和标签", prompt)
        self.assertIn("判断是参考不是法律", prompt)
        self.assertIn("以候选人自己的主业方向为锚", prompt)
        self.assertIn("明显跨链路的岗位默认 match=false", prompt)
        self.assertIn("匹配从宽只适用于候选人没有约束的维度", prompt)
        self.assertIn("以 JD 主责为准", prompt)
        self.assertIn("不得把 AI 已识别出的方向冲突", prompt)
        self.assertNotIn("本身不得作为 match=false 的理由", prompt)
        self.assertNotIn("行业、类别、技能不完全一致不排除", prompt)
        self.assertIn("以意愿为准", prompt)
        self.assertIn("岗位靠谱判定", prompt)
        self.assertIn("flags 为必填字段", prompt)

    def test_match_prompt_copy_migrated_from_integration_cases(self):
        """040 审查补丁（2026-09-12）：批三收敛时未迁移的集成侧精筛提示词断言。"""
        prompt = self._prompt()
        self.assertIn("用户明确写'不限/都可以/接受xx'", prompt)
        self.assertIn("默认匹配，不得写'候选人未知'", prompt)
        self.assertIn("技术栈硬冲突", prompt)
        self.assertIn("hard_ok", prompt)
        self.assertNotIn("fulltime_ok", prompt)
        self.assertIn("必须具备 Python 3年以上生产环境开发经验", prompt)
        self.assertIn("2-3年及以上", prompt)
        self.assertIn("硬性要求与已选条件冲突时", prompt)
        self.assertIn(
            "已确认的筛选条件（薪资/经验/学历/规模/融资/行业）是硬约束", prompt,
        )
        self.assertNotIn("行业分类不是硬约束", prompt)
        self.assertIn("flags 为必填字段，无命中输出空数组", prompt)
        self.assertIn(
            "不得把 AI 已识别出的方向冲突、硬性不满足只写进 caveats 后仍判 match",
            prompt,
        )


class DomainConstraintAssemblyTests(unittest.TestCase):
    """B094 T018：粗筛与精筛共用一套领域约束，无选择时保持旧契约。"""

    @staticmethod
    def _selection(*labels):
        from webui import ai_domain_policy as domain_policy
        return domain_policy.DomainSelection(
            labels=labels, source_kind=domain_policy.SOURCE_ORIGINAL_GROUP,
        )

    def _match_prompt(self, selection=None):
        from webui.ai_prompts import build_domain_constraint
        return build_match_system_prompt(
            criteria_desc="期望行业（领域线索，不按分类剔除）：互联网",
            profile_summary="3年Python后端",
            facts_desc="（无）",
            features_prompt_text="岗位靠谱特征清单",
            hard_fields_text="薪资/经验/学历/规模/融资",
            domain_text=build_domain_constraint(selection),
        )

    def test_domain_block_covers_work_product_business_and_generic_roles(self):
        prompt = self._match_prompt(self._selection("互联网/AI/软件/IT服务"))
        for fragment in (
            "互联网/AI/软件/IT服务", "工作内容", "产品", "主营业务",
            "行政", "通用职能", "偶然提到关键词", "办公工具", "不得编造",
        ):
            self.assertIn(fragment, prompt)

    def test_domain_block_states_union_and_no_exemption(self):
        prompt = self._match_prompt(
            self._selection("互联网/AI/软件/IT服务", "游戏/数字文娱"))
        self.assertIn("互联网/AI/软件/IT服务", prompt)
        self.assertIn("游戏/数字文娱", prompt)
        self.assertIn("任一领域相关即满足领域条件", prompt)
        self.assertIn("领域相关不豁免任何其它条件", prompt)

    def test_empty_domain_argument_matches_default_assembly(self):
        plain = self._match_prompt(None)
        baseline = build_match_system_prompt(
            criteria_desc="期望行业（领域线索，不按分类剔除）：互联网",
            profile_summary="3年Python后端",
            facts_desc="（无）",
            features_prompt_text="岗位靠谱特征清单",
            hard_fields_text="薪资/经验/学历/规模/融资",
        )
        self.assertEqual(plain, baseline)
        self.assertNotIn("【领域条件】", plain)

    def test_screen_prompt_reuses_the_same_domain_block(self):
        from webui.ai_prompts import build_domain_constraint, build_screen_system_prompt
        block = build_domain_constraint(self._selection("互联网/AI/软件/IT服务"))
        prompt = build_screen_system_prompt(
            criteria_desc="（无明确标准，宽松判断）",
            hard_fields_text="薪资/经验/学历/规模/融资",
            input_note="",
            domain_text=block,
        )
        self.assertIn(block, prompt)
        self.assertIn("岗位名称或类别", prompt)
        self.assertIn("拿不准一律保留", prompt)

    def test_screen_prompt_without_selection_keeps_legacy_rules(self):
        from webui.ai_prompts import build_screen_system_prompt
        prompt = build_screen_system_prompt(
            criteria_desc="（无明确标准，宽松判断）",
            hard_fields_text="薪资/经验/学历/规模/融资",
            input_note="",
        )
        self.assertNotIn("【领域条件】", prompt)
        self.assertIn(
            "已选择的筛选字段是硬约束：岗位标签明确列出的薪资/经验/学历/规模/融资",
            prompt)


class DomainRoughNoteTests(unittest.TestCase):
    """真实验收返修：粗筛输入里没有公司与业务事实，领域条件只能防误排。

    粗筛若把「是否属于所选领域」当成一项要证明的条件，就会在只看到标题和薪资时
    写下「领域不相关」，并把画像期望当新增硬条件——两者都违反冻结需求。
    """

    @staticmethod
    def _selection(*labels):
        from webui import ai_domain_policy as domain_policy
        return domain_policy.DomainSelection(
            labels=labels, source_kind=domain_policy.SOURCE_ORIGINAL_GROUP,
        )

    def _rough_prompt(self, selection=None):
        from webui.ai_prompts import build_domain_rough_note, build_screen_system_prompt
        return build_screen_system_prompt(
            criteria_desc="期望行业（领域线索，不按分类剔除）：互联网",
            hard_fields_text="薪资/经验/学历/规模/融资",
            input_note="",
            domain_text=build_domain_rough_note(selection),
        )

    def test_rough_note_keeps_labels_and_forbids_domain_based_drops(self):
        prompt = self._rough_prompt(
            self._selection("互联网/AI/软件/IT服务", "游戏/数字文娱"))
        self.assertIn("互联网/AI/软件/IT服务", prompt)
        self.assertIn("游戏/数字文娱", prompt)
        self.assertIn("不得以「领域不相关」为理由剔除", prompt)
        self.assertIn("留到取得职位描述后进行", prompt)

    def test_rough_note_does_not_license_new_hard_conditions(self):
        prompt = self._rough_prompt(self._selection("互联网/AI/软件/IT服务"))
        for fragment in (
            "领域相关不豁免任何其它条件",
            "求职画像与靠谱判定仍按原规则生效",
            "主营业务",
        ):
            self.assertNotIn(fragment, prompt)
        self.assertIn("候选人画像只用于放宽，不能用来新增硬条件", prompt)

    def test_fine_stage_still_carries_full_domain_criteria(self):
        from webui.ai_prompts import build_domain_constraint
        block = build_domain_constraint(self._selection("互联网/AI/软件/IT服务"))
        self.assertIn("主营业务", block)
        self.assertIn("领域相关不豁免任何其它条件", block)

    def test_existing_internet_option_includes_ai_and_software_work(self):
        from webui.ai_prompts import build_domain_constraint
        block = build_domain_constraint(self._selection("互联网", "游戏"))
        self.assertIn("现有互联网选项包含 AI、软件、IT 服务", block)
        self.assertIn("不能抵消岗位工作或产品的直接关联证据", block)
        self.assertIn("公司名称和行业分类不是实际主营业务证据", block)
        self.assertIn("不能把无法证明相关写成不相关", block)
        self.assertIn("不要求同时相关", block)

    def test_rough_note_empty_without_selection(self):
        self.assertEqual(self._rough_prompt(None), self._rough_prompt())


if __name__ == "__main__":
    unittest.main()
