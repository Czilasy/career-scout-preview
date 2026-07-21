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
        self.assertIn("搜索工作台", html)

    def test_legacy_screening_link_visible(self):
        resp = self.client.get("/")
        html = resp.data.decode("utf-8")
        self.assertIn("筛选工作台", html)


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

    def test_frontend_renders_scope_visible_in_preference_changes(self):
        """T090/FR-051: 前端必须能渲染反馈的作用范围（scope）。"""
        html = self._html()
        # preference_changes 渲染必须含 scope 字段引用（exact_job/direction）
        self.assertIn("exact_job", html)
        self.assertIn("scope", html)

    def test_frontend_resume_entry_visible_for_interrupted_run(self):
        """T090/SC-013: 前端必须含恢复入口（resumeDiscoveryRun）以支持中断后恢复。"""
        html = self._html()
        self.assertIn("resumeDiscoveryRun", html,
                      "T090/SC-013: 前端必须含 resumeDiscoveryRun 入口")


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

    def test_normalized_quality_and_manual_direction_controls_exist(self):
        html = self.client.get("/").data.decode("utf-8")
        for marker in ("quality_status", "discovery-quality-state", "需要确认", "简历未提供", "discoveryManualDirection", "discoveryManualTerms"):
            self.assertIn(marker, html)

    def test_default_selection_requires_explicit_true(self):
        html = self.client.get("/").data.decode("utf-8")
        self.assertIn("cb.checked = d.default_enabled === true", html)
        self.assertNotIn("cb.checked = d.default_enabled !== false", html)

    def test_dynamic_candidate_values_use_text_content(self):
        html = self.client.get("/").data.decode("utf-8")
        render = html.split("function renderAnalysis(analysis)", 1)[1].split("async function confirmDirections", 1)[0]
        self.assertIn("textContent = summary.headline", render)
        self.assertIn("item.textContent = \"需要确认：\"", render)


class CandidateProfileEditorV2FrontendTests(_FlaskTestCase):
    """T032/T033: editable fact provenance, intent and typed salary UI."""

    def _html(self):
        return self.client.get("/").data.decode("utf-8")

    def test_fact_inference_unknown_and_current_intent_sections_exist(self):
        html = self._html()
        for marker in (
            "discovery-facts-explicit", "discovery-facts-inferred",
            "discovery-profile-unknowns", "discovery-current-intent",
            "简历事实", "推断（需确认）", "未知项", "当前求职意愿",
        ):
            self.assertIn(marker, html)

    def test_fact_correct_add_reject_controls_use_candidate_version_hash(self):
        html = self._html()
        for marker in (
            "editCandidateFact", "addCandidateFact", "rejectCandidateFact",
            "saveCandidateProfileEdits", "candidate-versions",
            "expected_content_hash", "candidate_version_conflict",
            "candidate_fact_invalid",
        ):
            self.assertIn(marker, html)

    def test_direction_manual_controls_and_numeric_min_salary_are_typed(self):
        html = self._html()
        self.assertIn("discoveryManualDirection", html)
        self.assertIn("discoveryManualTerms", html)
        self.assertIn('type="number" id="discoveryHardSalary"', html)
        self.assertIn('amount: Number(salaryEl.value)', html)
        self.assertIn('source: "user_confirmed"', html)
        self.assertNotIn("hardConstraints.min_salary = salaryEl.value.trim()", html)

    def test_dynamic_fact_values_are_rendered_with_text_content(self):
        html = self._html()
        self.assertIn("renderCandidateProfileVersion", html)
        self.assertIn("factValue.textContent", html)
        self.assertNotIn("factValue.innerHTML", html)


class ProgressiveResultsV2FrontendTests(_FlaskTestCase):
    """T049 RED: 3-second polling, non-terminal results visible, revision-based
    no-redraw, stable card identity and explainable disappearance.

    Contract source: specs/005-fast-resume-discovery/contracts/http-api.md
      - Frontend polls GET /api/discovery/runs/{id}/results every 3 seconds.
      - Results are visible while run is still active (non-terminal).
      - If after_revision returns changed=false, the DOM is not redrawn.
      - Each result card has a stable identity keyed by job_id.
      - When a job disappears from results, the reason is explainable.
    """

    def _html(self):
        return self.client.get("/").data.decode("utf-8")

    def test_progressive_polling_function_with_3_second_interval_exists(self):
        """前端包含 3 秒轮询 progressive results 的函数。"""
        html = self._html()
        self.assertIn("pollProgressiveResults", html)
        # 3-second interval marker.
        self.assertIn("3000", html)

    def test_non_terminal_results_visible_rendering_exists(self):
        """非终态运行结果可见的渲染逻辑存在。"""
        html = self._html()
        # A function that renders results for active (non-terminal) runs.
        self.assertIn("renderProgressiveResults", html)
        # Must handle run_status that is not terminal.
        self.assertIn("run_status", html)

    def test_revision_unchanged_skips_redraw(self):
        """revision 不变时跳过重绘（changed=false 短路）。"""
        html = self._html()
        # Client tracks last revision and skips redraw when changed=false.
        self.assertIn("after_revision", html)
        self.assertIn("changed", html)
        # A guard that prevents unnecessary DOM updates.
        self.assertIn("lastResultRevision", html)

    def test_stable_card_identity_keyed_by_job_id(self):
        """结果卡片使用 job_id 作为稳定身份标识。"""
        html = self._html()
        # Card identity must be keyed by job_id, not array index.
        self.assertIn("data-job-id", html)
        # A map or registry tracking existing cards by job_id.
        self.assertIn("cardRegistry", html)

    def test_explainable_disappearance_reason_rendering(self):
        """岗位从结果中消失时展示可解释原因。"""
        html = self._html()
        # Disappearance reasons must be rendered.
        self.assertIn("disappearanceReason", html)
        # At least one reason code must be handled.
        self.assertIn("budget_deferred", html)


class RecommendationProjectorFrontendTests(_FlaskTestCase):
    """T063: frontend rendering for canonical recommendation projector.

    Contract source: specs/005-fast-resume-discovery/contracts/http-api.md
      - Direction + category filter UI
      - Multi-direction visibility
      - Complete JD card (company/title/salary/location/JD/source/fetched_at)
      - Sort reason display
      - Bilateral evidence and gap display
      - needs_review partition
    """

    def _html(self):
        return self.client.get("/").data.decode("utf-8")

    def test_direction_filter_ui_exists(self):
        """前端包含方向筛选 UI。"""
        html = self._html()
        self.assertIn("directionFilter", html)

    def test_category_filter_ui_exists(self):
        """前端包含类别筛选 UI。"""
        html = self._html()
        self.assertIn("categoryFilter", html)

    def test_jd_card_renders_full_job_fields(self):
        """JD 卡片渲染公司/岗位/薪资/地点/JD/来源/抓取时间。"""
        html = self._html()
        self.assertIn("source_url", html)
        self.assertIn("fetched_at", html)
        self.assertIn("jd_excerpt", html)

    def test_bilateral_evidence_rendering_exists(self):
        """正向依据和双方证据 refs 渲染逻辑存在。"""
        html = self._html()
        self.assertIn("candidate_evidence_refs", html)
        self.assertIn("job_evidence_refs", html)

    def test_gap_display_rendering_exists(self):
        """差距展示渲染逻辑存在。"""
        html = self._html()
        self.assertIn("explanation", html)
        self.assertIn("gaps", html)

    def test_sort_reason_display_exists(self):
        """排序原因展示逻辑存在。"""
        html = self._html()
        self.assertIn("sort_components", html)

    def test_needs_review_partition_rendering_exists(self):
        """needs_review 分区渲染逻辑存在。"""
        html = self._html()
        self.assertIn("needs_review", html)


class V2ProgressAndStatusVisibilityFrontendTests(_FlaskTestCase):
    """T082 RED: 前端渲染四类进度权威名、partial/failed/interrupted/cancelled
    状态徽章、cancel_requested_at 展示、resume 409 错误处理。

    Contract source: specs/005-fast-resume-discovery/contracts/http-api.md
      - L203-208: progress authoritative names include search_queries_completed
        and recommendations
      - L318: cancel response includes cancel_requested_at and four-part progress
      - L319: resume rejects hash drift with 409; frontend must surface the
        conflict reason
      - partial/failed/interrupted/cancelled states must be visibly distinct
        in the UI
    """

    def _html(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        return resp.data.decode("utf-8")

    # --- T082: four-class progress authoritative names ----------------

    def test_frontend_renders_search_queries_completed_label(self):
        """前端必须渲染四类进度权威名 search_queries_completed。

        RED: 当前进度条只展示 source_count/detail_count/evaluated_count 等
        v1 别名标签，缺少 v2 权威名 search_queries_completed。
        """
        html = self._html()
        self.assertIn("search_queries_completed", html)

    def test_frontend_renders_recommendations_label(self):
        """前端必须渲染四类进度权威名 recommendations。

        RED: 当前进度条缺少 recommendations 字段标签。
        """
        html = self._html()
        self.assertIn("recommendations", html)

    def test_frontend_renders_four_part_progress_labels(self):
        """前端必须渲染四类进度的中文标签。

        四类进度（list_candidates、details_selected、details_completed、
        assessments_completed）应有可见中文标签。
        """
        html = self._html()
        # 至少应出现四类进度的字段名。
        self.assertIn("list_candidates", html)
        self.assertIn("details_selected", html)
        self.assertIn("details_completed", html)
        self.assertIn("assessments_completed", html)

    # --- T082: partial/failed/interrupted/cancelled status badges -----

    def test_frontend_renders_partial_status_badge(self):
        """partial 状态必须有可见的状态徽章/文案。"""
        html = self._html()
        # partial 状态文案必须出现（中文「部分成功」或英文 partial 标签）
        self.assertTrue("partial" in html.lower() or "部分成功" in html)

    def test_frontend_renders_failed_status_badge(self):
        """failed 状态必须有可见的状态徽章/文案。"""
        html = self._html()
        self.assertTrue("failed" in html.lower() or "失败" in html)

    def test_frontend_renders_interrupted_status_badge(self):
        """interrupted 状态必须有可见的状态徽章/文案。"""
        html = self._html()
        self.assertTrue("interrupted" in html.lower() or "中断" in html)

    def test_frontend_renders_cancelled_status_badge(self):
        """cancelled 状态必须有可见的状态徽章/文案。"""
        html = self._html()
        self.assertTrue("cancelled" in html.lower() or "已取消" in html)

    def test_frontend_renders_complete_terminal_indicator(self):
        """前端必须能区分终态（complete=true）与可恢复态（complete=false）。

        RED: 当前 _run_summary 不返回 complete 字段，前端没有渲染终态指示。
        """
        html = self._html()
        # complete 标记用于区分终态 vs 可恢复态
        self.assertIn("complete", html)

    # --- T082: cancel_requested_at display ----------------------------

    def test_frontend_renders_cancel_requested_at_field(self):
        """前端必须能展示 cancel_requested_at 字段。

        RED: 当前后端 _run_summary 不返回 cancel_requested_at，前端也未渲染。
        """
        html = self._html()
        self.assertIn("cancel_requested_at", html)

    # --- T082: resume 409 error handling ------------------------------

    def test_frontend_handles_resume_409_state_conflict(self):
        """前端必须处理 resume 端点返回的 409 state_conflict 错误。

        RED: 当前 resume 端点直接返回 202，前端没有 409 错误处理分支。
        GREEN 后 resume 同步返回 409，前端必须能识别并展示冲突原因。
        """
        html = self._html()
        # 必须有 409 状态码处理逻辑
        self.assertIn("409", html)
        # 必须有 state_conflict 错误码处理
        self.assertIn("state_conflict", html)

    def test_frontend_displays_resume_conflict_user_message(self):
        """前端在 resume 409 时必须展示用户可读的冲突说明。"""
        html = self._html()
        # 必须有展示冲突原因的元素或函数
        self.assertTrue(
            "resumeConflict" in html or "恢复冲突" in html or "无法恢复" in html,
            "前端缺少 resume 冲突说明渲染逻辑",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
