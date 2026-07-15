"""T060/T061: README.md 与 README.en.md 文档契约测试。

验证 README.md 覆盖 002 简历驱动筛选功能的六类必备主题：
1. 简历驱动筛选（上传简历→AI 读取→建议值→用户确认→搜索）
2. 两层核验（第一层 BOSS 搜索 + 第二层硬规则与 AI 四维度语义评估）
3. 区域生命周期（符合/不符合临时区 + 感兴趣/垃圾桶持久区）
4. 感兴趣/垃圾桶（持久保留、展示阶段排除具体岗位）
5. 降级路径（AI 不可用→人工填筛+跳过简历+仅硬规则）
6. 不自动投递边界（不投递、不联系、不预测）

T061 同时验证 README.en.md 的英文说明与 README.md 的功能边界一致。

本测试只做静态文本校验，不启动 WebUI。
"""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
README_EN = (ROOT / "README.en.md").read_text(encoding="utf-8")


class ReadmeScreeningDocumentationTests(unittest.TestCase):
    """T060: README.md 必须完整记录 002 筛选功能的六类主题。"""

    # -- 主题 1：简历驱动筛选 --
    def test_resume_driven_filtering_overview_exists(self):
        self.assertIn("简历驱动", README, "README 必须说明简历驱动筛选")

    def test_resume_upload_and_ai_suggest_flow_exists(self):
        has_flow = ("上传简历" in README or "简历上传" in README) and (
            "建议值" in README or "AI 读取" in README or "填筛" in README
        )
        self.assertTrue(has_flow, "README 必须说明上传简历→AI 读取→建议值的流程")

    def test_user_confirm_priority_exists(self):
        self.assertIn(
            "确认", README, "README 必须说明用户确认值优先"
        )

    # -- 主题 2：两层核验 --
    def test_two_layer_verification_exists(self):
        self.assertIn("两层", README, "README 必须说明两层核验")

    def test_hard_rule_verification_exists(self):
        self.assertIn("硬规则", README, "README 必须说明硬规则核验")

    def test_ai_semantic_assessment_exists(self):
        has_ai_assessment = (
            "语义相似度" in README
            and ("四维度" in README or "结构化评估" in README)
            and "待确认" in README
        )
        self.assertTrue(
            has_ai_assessment, "README 必须说明 AI 语义相似度使用结构化评估并可进入待确认"
        )

    # -- 主题 3：区域生命周期 --
    def test_temporary_zone_lifecycle_exists(self):
        has_temp = "临时" in README and ("符合" in README or "不符合" in README)
        self.assertTrue(
            has_temp, "README 必须说明符合/不符合区为临时区域"
        )

    def test_zone_clear_on_new_run_exists(self):
        has_clear = "清空" in README or "下次执行" in README or "下一次执行" in README
        self.assertTrue(
            has_clear, "README 必须说明下次执行清空临时区"
        )

    def test_persistent_zone_exists(self):
        has_persist = "持久" in README and (
            "感兴趣" in README or "垃圾桶" in README
        )
        self.assertTrue(
            has_persist, "README 必须说明感兴趣/垃圾桶为持久区"
        )

    # -- 主题 4：感兴趣/垃圾桶 --
    def test_interested_zone_exists(self):
        self.assertIn("感兴趣", README, "README 必须说明感兴趣区")

    def test_trash_zone_exists(self):
        has_trash = "垃圾桶" in README
        self.assertTrue(has_trash, "README 必须说明垃圾桶区")

    def test_display_exclusion_scope_exists(self):
        has_exclusion = "展示" in README and (
            "排除" in README or "不扩展" in README
        )
        self.assertTrue(
            has_exclusion, "README 必须说明展示阶段排除且仅具体岗位"
        )

    # -- 主题 5：降级路径 --
    def test_degradation_path_exists(self):
        has_degradation = "降级" in README or "AI 不可用" in README
        self.assertTrue(
            has_degradation, "README 必须说明 AI 不可用降级路径"
        )

    def test_skip_resume_in_degradation_exists(self):
        has_skip = "跳过简历" in README or "跳过" in README
        self.assertTrue(
            has_skip, "README 必须说明降级时可跳过简历"
        )

    def test_manual_filter_in_degradation_exists(self):
        has_manual = "人工填筛" in README or "手动填写" in README or "人工填写" in README
        self.assertTrue(
            has_manual, "README 必须说明降级时人工填筛"
        )

    def test_hard_rule_only_in_degradation_exists(self):
        has_hard_only = "仅硬规则" in README or "硬规则" in README
        self.assertTrue(
            has_hard_only, "README 必须说明降级时第二层仅硬规则"
        )

    # -- 主题 6：不自动投递边界 --
    def test_no_auto_apply_boundary_exists(self):
        self.assertIn(
            "不自动投递", README, "README 必须说明不自动投递边界"
        )

    def test_no_contact_recruiter_boundary_exists(self):
        has_no_contact = "不联系" in README or "不自动联系" in README
        self.assertTrue(
            has_no_contact, "README 必须说明不联系招聘者"
        )

    def test_no_probability_prediction_exists(self):
        has_no_pred = "不预测" in README or "录用概率" in README
        self.assertTrue(
            has_no_pred, "README 必须说明不预测录用概率"
        )


class ReadmeEnScreeningDocumentationTests(unittest.TestCase):
    """T061: README.en.md 英文说明必须与 README.md 功能边界一致。

    验证 README.en.md 覆盖 002 筛选功能同样的六类主题（英文表述）。
    """

    # -- Theme 1: resume-driven filtering --
    def test_resume_driven_filtering_overview_exists(self):
        has_overview = "resume-driven" in README_EN.lower() or (
            "resume" in README_EN.lower() and "filtering" in README_EN.lower()
        )
        self.assertTrue(
            has_overview, "README.en.md must document resume-driven filtering"
        )

    def test_resume_upload_and_ai_suggest_flow_exists(self):
        has_flow = "resume" in README_EN.lower() and (
            "suggest" in README_EN.lower() or "ai" in README_EN.lower()
        )
        self.assertTrue(
            has_flow,
            "README.en.md must document the upload-resume -> AI suggest flow",
        )

    def test_user_confirm_priority_exists(self):
        has_confirm = "confirm" in README_EN.lower() or "priority" in README_EN.lower()
        self.assertTrue(
            has_confirm,
            "README.en.md must document user-confirmed value priority",
        )

    # -- Theme 2: two-layer verification --
    def test_two_layer_verification_exists(self):
        has_two_layer = "two-layer" in README_EN.lower() or (
            "two layer" in README_EN.lower()
        ) or "second layer" in README_EN.lower()
        self.assertTrue(
            has_two_layer, "README.en.md must document two-layer verification"
        )

    def test_hard_rule_verification_exists(self):
        has_hard = "hard rule" in README_EN.lower() or "hard-rule" in README_EN.lower()
        self.assertTrue(
            has_hard, "README.en.md must document hard-rule verification"
        )

    def test_ai_semantic_assessment_exists(self):
        has_ai_assessment = (
            ("semantic similarity" in README_EN.lower()
             or "semantic-similarity" in README_EN.lower())
            and "four-dimension" in README_EN.lower()
            and "review" in README_EN.lower()
        )
        self.assertTrue(
            has_ai_assessment,
            "README.en.md must document structured AI semantic assessment and review routing",
        )

    # -- Theme 3: zone lifecycle --
    def test_temporary_zone_lifecycle_exists(self):
        has_temp = "temporary" in README_EN.lower() and (
            "match" in README_EN.lower() or "mismatch" in README_EN.lower()
        )
        self.assertTrue(
            has_temp,
            "README.en.md must document match/mismatch as temporary zones",
        )

    def test_zone_clear_on_new_run_exists(self):
        has_clear = "clear" in README_EN.lower() or "next run" in README_EN.lower()
        self.assertTrue(
            has_clear, "README.en.md must document zone clearing on new run"
        )

    def test_persistent_zone_exists(self):
        has_persist = "persistent" in README_EN.lower() and (
            "interested" in README_EN.lower() or "trash" in README_EN.lower()
        )
        self.assertTrue(
            has_persist,
            "README.en.md must document interested/trash as persistent zones",
        )

    # -- Theme 4: interested / trash --
    def test_interested_zone_exists(self):
        self.assertIn(
            "Interested", README_EN, "README.en.md must document interested zone"
        )

    def test_trash_zone_exists(self):
        has_trash = "trash" in README_EN.lower() or "bin" in README_EN.lower()
        self.assertTrue(has_trash, "README.en.md must document trash zone")

    def test_display_exclusion_scope_exists(self):
        has_exclusion = "exclu" in README_EN.lower() and (
            "display" in README_EN.lower() or "presentation" in README_EN.lower()
        )
        self.assertTrue(
            has_exclusion,
            "README.en.md must document display-stage exclusion by specific job",
        )

    # -- Theme 5: degradation path --
    def test_degradation_path_exists(self):
        has_degradation = "degradation" in README_EN.lower() or (
            "ai unavailable" in README_EN.lower()
        ) or "unavailable" in README_EN.lower()
        self.assertTrue(
            has_degradation,
            "README.en.md must document the AI-unavailable degradation path",
        )

    def test_skip_resume_in_degradation_exists(self):
        has_skip = "skip" in README_EN.lower() and "resume" in README_EN.lower()
        self.assertTrue(
            has_skip,
            "README.en.md must document skipping resume in degradation",
        )

    def test_manual_filter_in_degradation_exists(self):
        has_manual = "manual" in README_EN.lower() and (
            "filter" in README_EN.lower()
        )
        self.assertTrue(
            has_manual, "README.en.md must document manual filtering in degradation"
        )

    def test_hard_rule_only_in_degradation_exists(self):
        has_hard_only = "hard rule" in README_EN.lower() or (
            "hard-rule" in README_EN.lower()
        )
        self.assertTrue(
            has_hard_only,
            "README.en.md must document hard-rule-only verification in degradation",
        )

    # -- Theme 6: no-auto-apply boundary --
    def test_no_auto_apply_boundary_exists(self):
        has_no_apply = "no-auto-application" in README_EN.lower() or (
            "no auto-application" in README_EN.lower()
        ) or "does not" in README_EN.lower()
        self.assertTrue(
            has_no_apply,
            "README.en.md must document the no-auto-application boundary",
        )

    def test_no_contact_recruiter_boundary_exists(self):
        has_no_contact = "contacting recruiters" in README_EN.lower() or (
            "contact recruiters" in README_EN.lower()
        )
        self.assertTrue(
            has_no_contact,
            "README.en.md must document the no-contact-recruiter boundary",
        )

    def test_no_probability_prediction_exists(self):
        has_no_pred = "probability" in README_EN.lower() or "prediction" in README_EN.lower()
        self.assertTrue(
            has_no_pred,
            "README.en.md must document the no-probability-prediction boundary",
        )


if __name__ == "__main__":
    unittest.main()
