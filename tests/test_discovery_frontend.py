"""Discovery frontend contract tests (feature 004).

These tests exercise the HTML/JS of webui/index.html via the Flask test
client and lightweight DOM assertions. They do not spin up a real
browser; browser-render verification is in tests/test_discovery_browser.py.
"""

from __future__ import annotations

import unittest

from webui.app import create_app


class _FlaskTestCase(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile, os
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.app = create_app({"TESTING": True, "DB_PATH": self._tmp.name, "START_TASKS": False})
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        import os
        if hasattr(self, "_tmp") and os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)


class HomeFourStepTests(_FlaskTestCase):
    """T072: default home surfaces only the four-step main line."""

    def test_home_returns_200(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_home_contains_four_step_sections(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        # Step 1: resume upload
        self.assertIn("discovery-upload", html.lower())
        # Step 2: analysis / confirmation
        self.assertIn("discovery-analysis", html.lower())
        # Step 3: run progress
        self.assertIn("discovery-progress", html.lower())
        # Step 4: results
        self.assertIn("discovery-results", html.lower())

    def test_home_has_discovery_tab_as_default(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("发现", html)

    def test_advanced_settings_collapsible(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        # Advanced settings should exist but be collapsible
        self.assertIn("高级", html)

    def test_legacy_workbench_link_visible(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("历史搜索", html)

    def test_legacy_screening_link_visible(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("历史筛选", html)


class DirectionConfirmationPageTests(_FlaskTestCase):
    """T074: direction confirmation page interactions."""

    def test_render_analysis_function_exists(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("renderAnalysis", html)

    def test_confirm_directions_function_exists(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("confirmDirections", html)

    def test_ai_consent_checkbox_present(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("ai_consent", html.lower())
        self.assertIn("consent", html.lower())

    def test_upload_resume_function_exists(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("uploadResumeDiscovery", html)


class RunProgressAndResultsTests(_FlaskTestCase):
    """T076: run progress + results view."""

    def test_render_run_progress_function_exists(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("renderRunProgress", html)

    def test_render_results_function_exists(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("renderResults", html)

    def test_start_discovery_run_function_exists(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("startDiscoveryRun", html)

    def test_cancel_and_resume_functions_exist(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("cancelDiscoveryRun", html)
        self.assertIn("resumeDiscoveryRun", html)

    def test_create_discovery_card_function_exists(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("createDiscoveryCard", html)


class FeedbackAndRevokeTests(_FlaskTestCase):
    """T078: feedback + revoke interactions."""

    def _html(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        return resp.data.decode("utf-8")

    def test_job_feedback_defaults_to_exact_job_and_has_structured_reasons(self):
        html = self._html()
        self.assertIn('scope: "exact_job"', html)
        self.assertIn('reason_code:', html)
        self.assertIn('value="company_unsuitable"', html)
        self.assertIn('value="location_unsuitable"', html)
        self.assertIn('value="salary_unsuitable"', html)
        self.assertIn('value="skill_assessment_inaccurate"', html)

    def test_interested_trash_restore_and_revoke_are_real_actions(self):
        html = self._html()
        self.assertIn("markDiscoveryInterest", html)
        self.assertIn("markDiscoveryReject", html)
        self.assertIn("restoreDiscoveryJob", html)
        self.assertIn("revokeFeedback", html)
        self.assertIn('{ key: "interested", label: "感兴趣" }', html)
        self.assertIn('{ key: "trash", label: "垃圾桶" }', html)

    def test_direction_feedback_and_visible_preference_changes_are_wired(self):
        html = self._html()
        self.assertIn("recordDirectionFeedback", html)
        self.assertIn("direction_disable", html)
        self.assertIn("renderPreferenceChanges", html)
        self.assertIn('id="discoveryPreferenceChanges"', html)
        self.assertIn("loadDiscoveryFeedbackState", html)


class LegacyEntryCompatTests(_FlaskTestCase):
    """T080: legacy workbench/screening entries remain visible."""

    def test_workbench_section_still_present(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("workspace", html.lower())

    def test_screening_section_still_present(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("screening", html.lower())

    def test_switch_view_function_exists(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("switchView", html)

    def test_legacy_search_function_still_present(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("startSearch", html)

    def test_legacy_app_notice_still_present(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("setAppNotice", html)


class AnalysisSubmissionFrontendTests(_FlaskTestCase):
    """T110: 前端分析请求显式提交 resume_id、轮询 queued/analyzing/ready/failed 并展示安全失败。

    这些测试只检查 HTML/JS 静态字符串，不执行 JS；浏览器运行时验证在
    tests/test_discovery_browser.py。RED 阶段断言当前前端仍使用旧的
    FormData + profile_id 提交且未显式处理 queued/analyzing 状态。
    """

    def test_analysis_request_does_not_use_formdata_profile_id(self):
        """分析请求不得再用 FormData analysisForm.append("profile_id") 提交。

        T109 后端改为 request.get_json() 读取 {resume_id, ai_consent}；
        FormData append("profile_id") 会被后端拒绝（缺少 resume_id）。
        本断言只针对 discovery 分析路径（analysisForm 变量），不影响
        screening 上传路径（formData.append）的既有兼容行为。
        """
        html = self.client.get("/").data.decode("utf-8")
        self.assertNotIn('analysisForm.append("profile_id"', html)
        # analysisForm 变量本身也应从 discovery 分析路径移除
        self.assertNotIn('analysisForm', html)

    def test_analysis_request_uses_json_resume_id(self):
        """分析请求必须以 JSON body 提交 resume_id。

        匹配 T109 后端 request.get_json() 读取 {resume_id, ai_consent}。
        检查 DiscoveryAPI.analyses 提交附近存在 resume_id + JSON.stringify。
        """
        html = self.client.get("/").data.decode("utf-8")
        # 必须出现 resume_id 字段名
        self.assertIn("resume_id", html)
        # 必须使用 JSON.stringify 构造 body（T109 后端要求 application/json）
        self.assertIn("JSON.stringify", html)

    def test_poll_explicitly_handles_queued_status(self):
        """轮询 pollDiscoveryAnalysis 必须显式处理 queued 状态。

        T109 后端创建分析后初始状态为 queued；前端必须告知用户当前
        处于排队中，而非静默等待 2 秒重试。
        """
        html = self.client.get("/").data.decode("utf-8")
        # pollDiscoveryAnalysis 函数体必须包含 "queued" 状态分支
        self.assertIn('"queued"', html)

    def test_poll_explicitly_handles_analyzing_status(self):
        """轮询 pollDiscoveryAnalysis 必须显式处理 analyzing 状态。

        T109 后端 runtime worker 调用 analyze_resume 时会先置 analyzing；
        前端必须告知用户当前正在分析，而非静默等待。
        """
        html = self.client.get("/").data.decode("utf-8")
        self.assertIn('"analyzing"', html)

    def test_poll_displays_safe_failure_envelope(self):
        """分析失败时必须展示安全失败信封（error_code + user_message）。

        T109 后端失败信封包含 failure.error_code 和 failure.user_message；
        前端不得展示原始异常或堆栈。
        """
        html = self.client.get("/").data.decode("utf-8")
        self.assertIn("error_code", html)
        self.assertIn("user_message", html)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
