"""T060: README.md 文档契约测试。

验证 README.md 覆盖 002 简历驱动筛选功能的六类必备主题：
1. 简历驱动筛选（上传简历→AI 读取→建议值→用户确认→搜索）
2. 两层核验（第一层 BOSS 搜索 + 第二层硬规则与 AI 语义相似度占位）
3. 区域生命周期（符合/不符合临时区 + 感兴趣/垃圾桶持久区）
4. 感兴趣/垃圾桶（持久保留、展示阶段排除具体岗位）
5. 降级路径（AI 不可用→人工填筛+跳过简历+仅硬规则）
6. 不自动投递边界（不投递、不联系、不预测）

本测试只做静态文本校验，不启动 WebUI。
"""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


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

    def test_ai_semantic_placeholder_exists(self):
        has_ai_placeholder = "语义相似度" in README and "占位" in README
        self.assertTrue(
            has_ai_placeholder, "README 必须说明 AI 语义相似度为占位"
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


if __name__ == "__main__":
    unittest.main()
