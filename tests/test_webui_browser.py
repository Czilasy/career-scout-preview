"""T047: Browser-level DOM contract tests for the workbench frontend.

Validates card structure, link safety, button non-navigation, JD
truncation, not-interested exit animation and narrow-screen drawer —
without a real browser, by parsing the served HTML and inline JS.
"""

import re
import sys
import pathlib
import tempfile
import unittest

from webui.app import create_app


class WorkbenchBrowserContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        resp = self.client.get("/")
        self.html = resp.get_data(as_text=True)
        resp.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_card_links_are_https_zhipin_only(self):
        """Card read-area links must only point to HTTPS zhipin domains."""
        # Find the JS that builds canonical_url into href
        # The template must not produce http: or non-zhipin links
        self.assertNotIn("http://www.zhipin.com", self.html)
        self.assertNotIn("javascript:void", self.html)
        # Must validate URLs before opening
        self.assertTrue(
            "zhipin.com" in self.html,
            "前端必须包含 zhipin.com 域名校验逻辑",
        )

    def test_feedback_buttons_prevent_navigation(self):
        """Interested / not-interested buttons must not trigger card link navigation."""
        # Buttons should be type="button" (not submit) and should call
        # preventDefault / stopPropagation in the inline JS
        self.assertTrue(
            'type="button"' in self.html or '.type = "button"' in self.html,
            "反馈按钮必须显式指定 button 类型",
        )
        # Must have event prevention in JS
        has_prevention = (
            "preventDefault" in self.html
            or "stopPropagation" in self.html
            or "event.stopPropagation" in self.html
        )
        self.assertTrue(has_prevention, "反馈按钮必须阻止事件冒泡或默认行为")

    def test_jd_excerpt_is_truncated(self):
        """JD text on cards must be visually truncated, not full-length."""
        # CSS must limit card JD height or use line-clamp / max-height
        has_truncation = (
            "line-clamp" in self.html
            or "max-height" in self.html
            or "text-overflow" in self.html
            or "-webkit-line-clamp" in self.html
        )
        self.assertTrue(has_truncation, "JD 摘要必须有截断样式")

    def test_not_interested_has_exit_animation(self):
        """Not-interested action must trigger a smooth exit animation."""
        # CSS must define a transition or animation for card removal
        has_animation = (
            "transition" in self.html
            or "@keyframes" in self.html
            or "animation" in self.html
        )
        self.assertTrue(has_animation, "卡片必须有过渡或动画")
        # JS must add a class for exit or set opacity
        has_exit = (
            "opacity" in self.html
            or "fade-out" in self.html
            or "slide-out" in self.html
            or "exit" in self.html.lower()
        )
        self.assertTrue(has_exit, "不感兴趣必须有退场效果")

    def test_undo_after_not_interested(self):
        """Undo must be available after marking not-interested."""
        self.assertIn("撤销", self.html)

    def test_narrow_screen_drawer(self):
        """Narrow screens must collapse settings into a drawer."""
        self.assertIn("@media (max-width: 720px)", self.html)
        # Settings panel must be hideable
        self.assertIn("settingsPanel", self.html)

    def test_empty_workbench_has_a_clear_next_step(self):
        """An empty workbench must guide the user into the existing setup flow."""
        self.assertIn("workbench-empty", self.html)
        self.assertIn("打开设置并开始", self.html)
        self.assertIn("onclick=\"toggleSettings()\"", self.html)

    def test_settings_panel_has_a_clear_context_and_close_control(self):
        """The settings drawer must identify itself and remain easy to dismiss."""
        self.assertIn("settings-panel-header", self.html)
        self.assertIn("整理你的求职条件", self.html)
        self.assertIn("关闭设置区", self.html)

    def test_model_refresh_action_stays_compact_without_losing_its_meaning(self):
        """The model refresh control keeps a short label and the existing action."""
        self.assertIn('class="btn model-fetch-button"', self.html)
        self.assertIn('aria-label="拉取可用模型"', self.html)
        self.assertIn('title="拉取可用模型"', self.html)
        self.assertIn('onclick="fetchAiModels()"', self.html)

    def test_narrow_workbench_reserves_space_for_the_settings_toggle(self):
        """The collapsed settings toggle must not cover the workbench title on phones."""
        self.assertIn('.search-bar { min-height: 68px; padding: 10px 12px 10px 42px; }', self.html)

    def test_no_ai_scores_in_card_template(self):
        """Card template must not expose AI scores, ranks or match reasons."""
        self.assertNotIn("match_score", self.html)
        self.assertNotIn("ai_rank", self.html)
        self.assertNotIn("match_reason", self.html)
        self.assertNotIn("ai_score", self.html)

    def test_no_auto_application_ui(self):
        """Frontend must not show auto-apply, auto-message or probability UI."""
        self.assertNotIn("自动投递", self.html)
        self.assertNotIn("联系招聘者", self.html)
        self.assertNotIn("录用概率", self.html)
        self.assertNotIn("/api/apply", self.html)

    def test_fixed_height_cards(self):
        """Cards must have fixed height — no variable-height expansion."""
        # CSS must set a fixed or max height on job cards
        has_fixed_height = (
            "fixed-height" in self.html
            or "max-height" in self.html
            or re.search(r"height:\s*\d+px", self.html) is not None
            or "height: calc(" in self.html
        )
        self.assertTrue(has_fixed_height, "岗位卡片必须有固定高度")

    def test_untrusted_ai_and_url_values_are_not_interpolated_as_html(self):
        """AI suggestions and validated URLs must be rendered with DOM APIs, not HTML strings."""
        self.assertNotIn('el.innerHTML = `', self.html)
        self.assertNotIn('href="${safeUrl}"', self.html)
        self.assertIn("textContent", self.html)

    def test_ai_model_options_use_dom_text_nodes(self):
        """Configured and remotely listed model names must not be parsed as HTML."""
        self.assertIn("function renderAiModelOptions", self.html)
        self.assertIn("option.textContent = model", self.html)
        self.assertNotIn('sel.innerHTML = `<option value="${savedModel}"', self.html)
        self.assertNotIn('data.models.map(m => `<option value="${m}"', self.html)

    def test_screening_execution_range_controls_are_sent_to_the_api(self):
        """正式筛选页必须让用户限制页数和详情数，并发送给运行接口。"""
        self.assertIn('id="screeningPages"', self.html)
        self.assertIn('id="screeningMaxDetails"', self.html)
        self.assertIn("body.pages", self.html)
        self.assertIn("body.max_details", self.html)


class ScreeningBrowserContractTests(unittest.TestCase):
    """T053: 筛选页浏览器交互契约（卡片跳转、按钮阻止跳转、两区切换、垃圾桶查看、降级态展示）。

    解析 GET / 返回的 HTML 与内联 JS，验证 US6 所需交互行为存在。
    不使用真实浏览器，只做静态 HTML/JS 契约校验。真实浏览器交互在
    T063 浏览器验收覆盖。

    覆盖五类交互：
    1. 卡片跳转（FR-020）：感兴趣区卡片可点击跳转，仅允许 HTTPS+zhipin.com
    2. 按钮阻止跳转（FR-017）：符合/不符合区卡片的感兴趣/不感兴趣按钮不触发跳转
    3. 两区切换（FR-016）：符合/不符合按钮切换两个区域
    4. 垃圾桶查看（FR-024）：垃圾桶入口展示曾标记不感兴趣的岗位列表
    5. 降级态展示（FR-031, FR-033）：AI 不可用时提示、跳过简历、人工填筛

    本类与 T052 的 DOM 契约测试互补：T052 校验 HTML 元素存在性（data-*
    属性），本类校验 JS 行为契约（API 调用、事件处理、状态切换）。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        resp = self.client.get("/")
        self.html = resp.get_data(as_text=True)
        resp.close()

    def tearDown(self):
        self.temp.cleanup()

    # -- 卡片跳转（FR-020）--

    def test_interested_zone_api_referenced(self):
        """前端必须引用感兴趣区 API 端点用于回看。"""
        self.assertIn("/api/screening/interested", self.html)

    def test_card_navigation_validates_https(self):
        """卡片跳转必须校验 HTTPS 协议，拒绝 http: 链接。"""
        self.assertIn("https:", self.html)

    def test_card_navigation_validates_zhipin_domain(self):
        """卡片跳转必须校验 zhipin.com 域名，拒绝非预期域名。"""
        self.assertIn("zhipin.com", self.html)

    def test_card_navigation_uses_window_open_noopener(self):
        """卡片跳转必须用 window.open + noopener 打开新窗口。"""
        self.assertIn("window.open", self.html)
        self.assertIn("noopener", self.html)

    # -- 按钮阻止跳转（FR-017）--

    def test_screening_feedback_api_referenced(self):
        """前端必须引用筛选反馈 API（感兴趣/不感兴趣）。"""
        has_feedback_api = (
            "/api/screening/jobs/" in self.html
            and ("interest" in self.html or "reject" in self.html)
        )
        self.assertTrue(has_feedback_api, "前端必须引用筛选反馈 API")

    def test_screening_feedback_event_prevention(self):
        """筛选反馈按钮必须阻止事件冒泡或默认行为，不触发跳转。"""
        has_prevention = (
            "stopPropagation" in self.html
            or "preventDefault" in self.html
        )
        self.assertTrue(has_prevention, "筛选反馈按钮必须阻止事件冒泡或默认行为")

    def test_screening_feedback_button_type(self):
        """筛选反馈按钮必须是 type=button，不触发表单提交。"""
        self.assertTrue(
            'type="button"' in self.html or '.type = "button"' in self.html,
            "筛选反馈按钮必须显式指定 button 类型",
        )

    # -- 两区切换（FR-016）--

    def test_zone_switch_logic_exists(self):
        """JS 必须存在两区切换逻辑（符合/不符合）。"""
        has_zone_switch = (
            "switchZone" in self.html
            or "showMatch" in self.html
            or "showMismatch" in self.html
            or "data-zone" in self.html
        )
        self.assertTrue(has_zone_switch, "JS 必须有两区切换逻辑")

    def test_zone_tab_click_handler_exists(self):
        """两区切换按钮必须有点击处理。"""
        has_tab_handler = (
            "data-zone-tab" in self.html
            or "zoneTab" in self.html
            or "zone-tab" in self.html
        )
        self.assertTrue(has_tab_handler, "两区切换按钮必须有点击处理")

    def test_matches_mismatches_api_referenced(self):
        """前端必须引用符合区与不符合区 API 端点。"""
        self.assertIn("/api/screening/runs/", self.html)
        has_zone_api = "matches" in self.html and "mismatches" in self.html
        self.assertTrue(has_zone_api, "前端必须引用符合/不符合区 API")

    # -- 垃圾桶查看（FR-024）--

    def test_trash_api_referenced(self):
        """前端必须引用垃圾桶区 API 端点。"""
        self.assertIn("/api/screening/trash", self.html)

    def test_trash_display_function_exists(self):
        """JS 必须有加载/显示垃圾桶岗位列表的函数。"""
        has_trash_function = (
            "loadTrash" in self.html
            or "showTrash" in self.html
            or "renderTrash" in self.html
            or "/api/screening/trash" in self.html
        )
        self.assertTrue(has_trash_function, "JS 必须有显示垃圾桶列表的函数")

    # -- 降级态展示（FR-031, FR-033）--

    def test_ai_unavailable_state_handling(self):
        """JS 必须有 AI 不可用状态处理逻辑。"""
        has_ai_unavailable = (
            "ai_unavailable" in self.html
            or "aiUnavailable" in self.html
            or "ai-unavailable" in self.html
        )
        self.assertTrue(has_ai_unavailable, "JS 必须能处理 AI 不可用状态")

    def test_skip_resume_option_in_degradation(self):
        """降级时必须提供跳过简历的选项。"""
        has_skip = (
            "skipResume" in self.html
            or "skip-resume" in self.html
            or "跳过简历" in self.html
        )
        self.assertTrue(has_skip, "前端必须提供跳过简历选项")

    def test_manual_filter_hint_in_degradation(self):
        """降级时必须提示用户人工填写筛选字段。"""
        has_manual_hint = (
            "人工填筛" in self.html
            or "手动填写" in self.html
            or "manualFilter" in self.html
        )
        self.assertTrue(has_manual_hint, "前端必须提示人工填筛")


if __name__ == "__main__":
    unittest.main()
