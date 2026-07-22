"""Real-browser acceptance tests for the Vue WebUI.

The former version of this module called global functions from the legacy
inline application (``renderAnalysis``, ``renderResults`` and friends).  The
Vue build deliberately has no such globals, so these tests exercise the
compiled application through its visible controls and HTTP boundary instead.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from urllib.parse import urlparse

from webui.app import create_app

PLAYWRIGHT_AVAILABLE = True
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


ADVANCED_SETTINGS = {
    "pages": 3,
    "inter_combo_delay": 30,
    "detail_batch_size": 5,
    "screen_batch_size": 50,
    "screen_concurrency": 1,
    "match_batch_size": 4,
}

ANALYSIS_RESPONSE = {
    "ok": True,
    "fields": {
        "keyword": [
            {"word": "Python 后端", "recommended": True},
            {"word": "AI Agent", "recommended": False},
        ],
        "city": ["上海"],
        "salary": ["406"],
        "experience": [],
        "degree": [],
        "industry": [],
        "scale": [],
        "stage": [],
        "profile_summary": "Python 后端候选人",
    },
    "labels": {
        "keyword": ["搜索关键词", [], "keyword_chips"],
        "city": ["城市", ["上海"], "city"],
        "salary": ["薪资范围", ["406"], {"不限": "0", "20-50K": "406"}],
        "experience": ["经验要求", [], {"不限": "0", "3-5年": "105"}],
        "degree": ["学历", [], {"不限": "0", "本科": "203"}],
        "industry": ["行业", [], {"不限": "0", "互联网": "100020"}],
        "scale": ["公司规模", [], {"不限": "0", "100-499人": "304"}],
        "stage": ["融资阶段", [], {"不限": "0", "B轮": "804"}],
    },
}


def _job(index: int, verdict: str = "match") -> dict:
    return {
        "job_id": f"job-{index}",
        "title": f"Python 工程师 {index}",
        "company": f"测试公司 {index}",
        "salary": "20-30K",
        "location": "上海",
        "jd": f"岗位描述 {index}",
        "verdict": verdict,
        "verdict_reason": "技能与岗位要求相符",
        "canonical_url": f"https://www.zhipin.com/job_detail/job-{index}.html",
    }


class _BrowserServer:
    """Serve the production Vite bundle from Flask on a free local port."""

    def __init__(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.app = create_app({
            "TESTING": True,
            "DB_PATH": self._tmp.name,
            "START_TASKS": False,
        })
        from werkzeug.serving import make_server

        self._server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.port = self._server.port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path="/"):
        return f"http://127.0.0.1:{self.port}{path}"

    def stop(self):
        self._server.shutdown()
        self._thread.join(timeout=5)
        if os.path.exists(self._tmp.name):
            os.unlink(self._tmp.name)


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE, "playwright not installed")
class VueWebUIBrowserTests(unittest.TestCase):
    """Exercise the compiled Vue application, not component test doubles."""

    @classmethod
    def setUpClass(cls):
        cls.server = _BrowserServer()
        cls._pw = sync_playwright().start()
        cls._browser = cls._pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls._browser.close()
        cls._pw.stop()
        cls.server.stop()

    def setUp(self):
        self._context = self._browser.new_context(
            viewport={"width": 1366, "height": 768},
        )
        self.page = self._context.new_page()
        self.api_calls: list[dict] = []
        self.latest_result: dict | None = None
        self.scrape_progress: list[dict] = []
        self.screen_progress: list[dict] = []
        self.screening_run: dict | None = None
        self.page.route("**/api/**", self._handle_api)
        self.page.goto(self.server.url("/"), wait_until="networkidle")

    def tearDown(self):
        self._context.close()

    def _json_body(self, request):
        try:
            return request.post_data_json
        except Exception:  # Playwright raises when a body is not JSON.
            return None

    def _fulfill(self, route, body: dict, status: int = 200):
        route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps(body, ensure_ascii=False),
        )

    def _handle_api(self, route):
        request = route.request
        path = urlparse(request.url).path
        method = request.method
        self.api_calls.append({
            "path": path,
            "method": method,
            "json": self._json_body(request),
        })

        if path == "/api/session":
            return self._fulfill(route, {"token": "browser-test-token"})
        if path == "/api/check":
            return self._fulfill(route, {"connected": True})
        if path == "/api/profiles":
            return self._fulfill(route, {
                "profiles": [{"id": "profile-1", "name": "默认画像"}],
            })
        if path == "/api/advanced-settings":
            return self._fulfill(route, {"settings": ADVANCED_SETTINGS})
        if path == "/api/latest-pipeline-result":
            return self._fulfill(route, {
                "has_result": self.latest_result is not None,
                "result": self.latest_result,
            })
        if path == "/api/screening/filter-options":
            return self._fulfill(route, {"options": {}})
        if path in ("/api/screening/interested", "/api/screening/trash"):
            return self._fulfill(route, {"items": []})
        if path == "/api/ai-settings":
            return self._fulfill(route, {
                "endpoint_url": "https://example.invalid/v1/chat/completions",
                "model": "test-model",
                "masked_key": "sk-****",
            })
        if path == "/api/analyze-resume":
            return self._fulfill(route, ANALYSIS_RESPONSE)
        if path == "/api/execute-search":
            return self._fulfill(route, {"task_id": "scrape-1"})
        if path == "/api/search-progress/scrape-1":
            snapshot = self.scrape_progress.pop(0) if self.scrape_progress else {
                "status": "done",
                "progress": {"current": 1, "total": 1, "message": "抓取完成"},
                "logs": [],
                "result": {"ok": True, "jobs": []},
            }
            return self._fulfill(route, snapshot)
        if path == "/api/ai-screen":
            return self._fulfill(route, {"task_id": "screen-1"})
        if path == "/api/search-progress/screen-1":
            snapshot = self.screen_progress.pop(0) if self.screen_progress else {
                "status": "done",
                "progress": {"current": 1, "total": 1, "message": "筛选完成"},
                "logs": [],
                "result": {"ok": True, "jobs": [], "dropped": []},
            }
            return self._fulfill(route, snapshot)
        if path in (
            "/api/pipeline/jobs/interest",
            "/api/pipeline/jobs/interest/cancel",
        ):
            return self._fulfill(route, {"ok": True})
        if path == "/api/screening/runs" and method == "POST":
            self.screening_run = {
                "run_id": "screening-run-1",
                "status": "running",
                "processed_count": 0,
                "source_count": 10,
                "parse_failure_count": 0,
            }
            return self._fulfill(route, self.screening_run)
        if path == "/api/screening/runs/screening-run-1" and method == "GET":
            return self._fulfill(route, self.screening_run or {
                "run_id": "screening-run-1",
                "status": "running",
            })
        if path == "/api/screening/runs/screening-run-1/cancel":
            self.screening_run = {
                "run_id": "screening-run-1",
                "status": "cancelled",
                "processed_count": 3,
                "source_count": 10,
                "parse_failure_count": 0,
            }
            return self._fulfill(route, self.screening_run)
        if path.startswith("/api/screening/runs/screening-run-1/"):
            return self._fulfill(route, {"items": []})

        return self._fulfill(route, {"ok": True, "items": []})

    def _reload(self):
        self.page.reload(wait_until="networkidle")

    def _analyze_resume(self):
        self.page.set_input_files(
            '[data-testid="resume-input"]',
            {
                "name": "resume.txt",
                "mimeType": "text/plain",
                "buffer": b"Python backend developer",
            },
        )
        self.page.locator('[data-testid="resume-consent"]').check()
        self.page.locator('[data-testid="analyze-resume"]').click()
        self.page.get_by_role("heading", name="确认关键词与城市").wait_for()

    def _show_latest_results(self, jobs: list[dict], dropped=None):
        dropped = dropped or []
        self.latest_result = {
            "ok": True,
            "jobs": jobs,
            "dropped": dropped,
            "total_scraped": len(jobs) + len(dropped),
            "total_kept": len(jobs),
            "total_matched": sum(job.get("verdict") == "match" for job in jobs),
            "total_dropped": len(dropped),
        }
        self._reload()
        self.page.locator(".step-nav button", has_text="查看结果").click()

    def test_desktop_navigation_and_four_gated_steps_render_without_overflow(self):
        labels = self.page.locator('.view-tabs [role="tab"]').all_inner_texts()
        self.assertEqual(labels, ["智能选岗", "高级筛选"])
        steps = self.page.locator(".step-nav button")
        self.assertEqual(steps.all_inner_texts(), [
            "1\n上传简历",
            "2\n广泛抓取",
            "3\nAI 筛选",
            "4\n查看结果",
        ])
        self.assertFalse(steps.nth(0).is_disabled())
        self.assertTrue(all(steps.nth(i).is_disabled() for i in range(1, 4)))
        overflow = self.page.evaluate(
            "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        self.assertFalse(overflow)

    def test_resume_upload_requires_file_and_explicit_ai_consent(self):
        self.page.locator('[data-testid="analyze-resume"]').click()
        self.assertIn("请先选择简历文件", self.page.locator(".notice-bar").inner_text())

        self.page.set_input_files(
            '[data-testid="resume-input"]',
            {"name": "resume.txt", "mimeType": "text/plain", "buffer": b"resume"},
        )
        self.page.locator('[data-testid="analyze-resume"]').click()
        self.assertIn("请勾选 AI 解析同意", self.page.locator(".notice-bar").inner_text())

        self.page.locator('[data-testid="resume-consent"]').check()
        self.page.locator('[data-testid="analyze-resume"]').click()
        self.page.get_by_role("heading", name="确认关键词与城市").wait_for()
        self.assertTrue(
            self.page.locator('[data-testid="keyword-chip"]').first.is_visible()
        )

    def test_four_step_requests_remain_separate_and_bound_to_one_scrape(self):
        self._analyze_resume()
        self.scrape_progress = [{
            "status": "done",
            "progress": {"current": 1, "total": 1, "message": "抓取完成"},
            "logs": ["已保存 2 个岗位"],
            "result": {"ok": True, "jobs": [_job(1), _job(2)]},
        }]
        self.page.locator('[data-testid="start-scrape"]').click()
        self.page.locator('[data-testid="continue-to-screen"]').wait_for()

        scrape_call = next(
            call for call in self.api_calls
            if call["path"] == "/api/execute-search" and call["method"] == "POST"
        )
        self.assertEqual(scrape_call["json"], {
            "script_params": {
                "keyword": "Python 后端",
                "city": ["上海"],
                "filters": {},
            },
        })

        self.page.locator('[data-testid="continue-to-screen"]').click()
        self.assertEqual(self.page.locator(".filter-group").count(), 6)
        self.screen_progress = [{
            "status": "done",
            "progress": {"current": 2, "total": 2, "message": "筛选完成"},
            "logs": [],
            "result": {
                "ok": True,
                "jobs": [_job(1, "priority"), _job(2, "uncertain")],
                "dropped": [],
                "total_scraped": 2,
                "total_kept": 2,
                "total_matched": 1,
                "total_dropped": 0,
            },
        }]
        self.page.locator('[data-testid="start-ai-screen"]').click()
        self.page.locator('.result-tabs [role="tab"]').first.wait_for()

        screen_call = next(
            call for call in self.api_calls
            if call["path"] == "/api/ai-screen" and call["method"] == "POST"
        )
        self.assertEqual(screen_call["json"]["scrape_task_id"], "scrape-1")
        result_labels = [
            "".join(text.split())
            for text in self.page.locator('.result-tabs [role="tab"]').all_inner_texts()
        ]
        self.assertEqual(result_labels, ["优先投递1", "可以考虑0", "待确认1", "不推荐0", "已筛除0"])

    def test_running_and_failed_scrape_states_are_actionable(self):
        self._analyze_resume()
        self.scrape_progress = [{
            "status": "failed",
            "progress": {"message": "连接检查失败"},
            "logs": [],
            "error": "BOSS 专用浏览器未连接",
        }]
        self.page.locator('[data-testid="start-scrape"]').click()
        progress = self.page.locator(".task-progress")
        progress.get_by_text("执行失败").wait_for()
        self.assertIn("BOSS 专用浏览器未连接", progress.inner_text())
        self.assertIn("BOSS 专用浏览器未连接", self.page.locator(".notice-bar").inner_text())
        self.assertNotIn("未知错误", self.page.locator("body").inner_text())

    def test_empty_result_has_a_clear_state(self):
        self._show_latest_results([])
        panel = self.page.locator('[data-testid="discovery-view"] .empty-panel')
        self.assertTrue(panel.is_visible())
        self.assertIn("没有", panel.inner_text())
        self.assertIn("完成当前步骤后", panel.inner_text())

    def test_large_result_set_is_batched_with_one_detail_panel(self):
        self._show_latest_results([_job(index) for index in range(65)])
        self.assertEqual(self.page.locator(".result-overview").count(), 0)
        self.assertEqual(self.page.locator('[data-testid="job-row"]').count(), 30)
        self.assertEqual(self.page.locator('[data-testid="job-detail"]').count(), 1)
        self.assertIn("已加载 30", self.page.locator(".job-list-heading").inner_text())

        layout = self.page.evaluate("""() => {
            const list = document.querySelector('.job-list');
            const workspace = document.querySelector('.job-workspace');
            return {
                bodyOverflow: getComputedStyle(document.body).overflow,
                documentClientHeight: document.documentElement.clientHeight,
                documentScrollHeight: document.documentElement.scrollHeight,
                listClientHeight: list.clientHeight,
                listScrollHeight: list.scrollHeight,
                workspaceBottom: workspace.getBoundingClientRect().bottom,
                viewportHeight: window.innerHeight,
            };
        }""")
        self.assertEqual(layout["bodyOverflow"], "hidden")
        self.assertLessEqual(
            layout["documentScrollHeight"], layout["documentClientHeight"] + 1,
        )
        self.assertGreater(layout["listScrollHeight"], layout["listClientHeight"])
        self.assertLessEqual(layout["workspaceBottom"], layout["viewportHeight"] + 1)

        self.page.locator('[data-testid="load-more"]').click()
        self.assertEqual(self.page.locator('[data-testid="job-row"]').count(), 60)
        self.assertEqual(self.page.locator('[data-testid="job-detail"]').count(), 1)

    def test_sparse_result_rows_stay_compact_in_the_scrollable_list(self):
        self._show_latest_results([
            _job(1, "uncertain"),
            _job(2, "uncertain"),
        ])
        self.page.get_by_role("tab", name="待确认 2").click()

        rows = self.page.locator('[data-testid="job-row"]')
        self.assertEqual(rows.count(), 2)
        heights = rows.evaluate_all(
            "elements => elements.map(element => element.getBoundingClientRect().height)"
        )
        self.assertTrue(
            all(80 <= height <= 96 for height in heights),
            f"稀疏结果的岗位卡片应保持紧凑高度，实际为 {heights}",
        )
        second_box = rows.nth(1).bounding_box()
        list_box = self.page.locator(".job-list").bounding_box()
        self.assertLessEqual(second_box["y"] + second_box["height"], list_box["y"] + 192)

    def test_untrusted_job_text_is_rendered_as_text_and_link_is_hardened(self):
        job = _job(1)
        job["title"] = "<img src=x onerror=window.__xss=1>"
        self._show_latest_results([job])
        detail = self.page.locator('[data-testid="job-detail"]')
        self.assertIn("<img src=x onerror=window.__xss=1>", detail.inner_text())
        self.assertIsNone(self.page.evaluate("() => window.__xss"))
        link = detail.get_by_role("link", name="查看原岗位")
        self.assertEqual(link.get_attribute("target"), "_blank")
        self.assertEqual(link.get_attribute("rel"), "noopener noreferrer")

    def test_interest_persists_cancel_uses_http_and_reject_stays_local(self):
        self._show_latest_results([_job(1)])
        detail = self.page.locator('[data-testid="job-detail"]')
        detail.get_by_role("button", name="感兴趣", exact=True).click()
        detail.get_by_role("button", name="撤销感兴趣", exact=True).wait_for()
        detail.get_by_role("button", name="撤销感兴趣", exact=True).click()
        detail.get_by_role("button", name="感兴趣", exact=True).wait_for()

        interest_paths = [
            call["path"] for call in self.api_calls
            if call["path"].startswith("/api/pipeline/jobs/interest")
        ]
        self.assertEqual(interest_paths, [
            "/api/pipeline/jobs/interest",
            "/api/pipeline/jobs/interest/cancel",
        ])

        before = len(self.api_calls)
        detail.get_by_role("button", name="不感兴趣", exact=True).click()
        detail.get_by_role("button", name="撤销不感兴趣", exact=True).wait_for()
        self.assertEqual(len(self.api_calls), before)
        self.assertIn("仅本轮有效", self.page.locator(".notice-bar").inner_text())

    def test_ai_settings_dialog_has_focus_management_and_escape_cancel(self):
        trigger = self.page.locator('[data-testid="ai-settings-trigger"]')
        trigger.click()
        dialog = self.page.get_by_role("dialog", name="AI 设置")
        dialog.wait_for()
        self.assertEqual(dialog.get_attribute("aria-modal"), "true")
        self.assertTrue(dialog.evaluate("el => el.contains(document.activeElement)"))
        self.page.keyboard.press("Escape")
        self.assertTrue(dialog.is_hidden())
        self.assertTrue(trigger.evaluate("el => document.activeElement === el"))

    def test_375px_layout_keeps_header_actions_and_tabs_reachable(self):
        self.page.set_viewport_size({"width": 375, "height": 812})
        self.page.wait_for_timeout(100)
        overflow = self.page.evaluate(
            "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"
        )
        self.assertFalse(overflow)

        trigger = self.page.locator('[data-testid="ai-settings-trigger"]')
        trigger_box = trigger.bounding_box()
        self.assertGreaterEqual(trigger_box["width"], 44)
        self.assertGreaterEqual(trigger_box["height"], 44)
        self.assertTrue(self.page.get_by_label("当前画像").is_visible())

        self.page.locator('[data-testid="screening-tab"]').click()
        zone_tabs = self.page.locator(".screening-zone-tabs")
        self.assertTrue(zone_tabs.is_visible())
        self.assertFalse(zone_tabs.evaluate("el => el.scrollWidth > el.clientWidth"))
        self.assertEqual(zone_tabs.locator('[role="tab"]').count(), 5)

    def test_mobile_job_detail_is_full_screen_and_can_reopen(self):
        self.page.set_viewport_size({"width": 375, "height": 812})
        self._show_latest_results([_job(1), _job(2)])
        detail = self.page.locator('[data-testid="job-detail"]')
        box = detail.bounding_box()
        self.assertEqual(detail.evaluate("el => getComputedStyle(el).position"), "fixed")
        self.assertLessEqual(abs(box["x"]), 1)
        self.assertLessEqual(abs(box["y"]), 1)
        self.assertGreaterEqual(box["width"], 374)
        self.assertGreaterEqual(box["height"], 811)

        detail.get_by_role("button", name="关闭岗位详情").click()
        self.assertTrue(detail.is_hidden())
        self.page.locator('[data-testid="job-row"]').first.click()
        self.assertTrue(detail.is_visible())

    def test_screening_run_can_be_cancelled_without_losing_the_page(self):
        self.page.locator('[data-testid="screening-tab"]').click()
        self.page.locator('[data-testid="screening-keyword"]').fill("Python 后端")
        self.page.locator('[data-testid="start-screening"]').click()
        cancel = self.page.get_by_role("button", name="取消", exact=True)
        cancel.wait_for()
        cancel.click()
        self.page.get_by_text("筛选已取消，已完成结果已保留").wait_for()
        self.assertIn("cancelled", self.page.locator(".run-status-strip").inner_text())
        self.assertTrue(self.page.locator('[data-testid="screening-view"]').is_visible())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
