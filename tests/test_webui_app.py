import hashlib
import json
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

import requests

from webui.app import create_app


def _tuning_quality_context():
    return {
        "profile_summary": (
            "AI应用开发候选人，掌握 Python、FastAPI、LangGraph 和 RAG。"
        ),
        "screening_fields": {
            "salary": ["403", "404", "405"],
            "experience": ["101", "103", "104"],
            "degree": ["202", "203"],
        },
        "profile_ref": "user-confirmed:test",
    }


class WebUIAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.result_dir = root / "results"
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(self.result_dir),
            "DB_PATH": str(root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        session = self.client.get("/api/session")
        self.assertEqual(session.status_code, 200)
        self.token = session.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token

    def tearDown(self):
        self.temp.cleanup()

    def _create_scrape_task(self, analysis=False):
        response = self.client.post("/api/tasks", json={
            "keyword": "Python 后端",
            "city": "上海",
            "pages": 2,
            "detail": True,
            "analysis": analysis,
            "format": "json",
            "stage": "804",
            "profile": {
                "target_titles": "Python 后端",
                "must_skills": "Python,FastAPI",
                "min_salary": 20,
            },
        })
        self.assertEqual(response.status_code, 202)
        return response.get_json()["task"]

    def test_options_come_from_scraper_maps(self):
        payload = self.client.get("/api/options").get_json()

        stages = {item["label"]: item["value"] for item in payload["filters"]["stage"]}
        self.assertEqual(stages["B轮"], "804")
        self.assertEqual(stages["不需要融资"], "808")
        self.assertIn({"label": "上海", "value": "上海"}, payload["cities"])

    def test_testing_app_isolates_legacy_advanced_settings_file(self):
        """SPEC011: isolated Flask tests must never write the user's JSON."""
        settings_path = pathlib.Path(self.app.config["ADVANCED_SETTINGS_PATH"])
        self.assertTrue(
            settings_path.is_relative_to(pathlib.Path(self.temp.name)),
            f"测试高级设置路径必须位于临时目录，实际为 {settings_path}",
        )
        response = self.client.post("/api/advanced-settings", json={
            "settings": {
                "pages": 3,
                "inter_combo_delay": 10,
                "detail_batch_size": 15,
                "detail_interval": 2,
                "detail_reset_every": 4,
                "detail_batch_cooldown": 5,
                "screen_batch_size": 50,
                "screen_concurrency": 5,
                "match_batch_size": 4,
                "match_concurrency": 10,
            },
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(settings_path.is_file())

    def test_production_session_uses_httponly_cookie_without_json_token(self):
        root = pathlib.Path(self.temp.name) / "production-session"
        app = create_app({
            "TESTING": False,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "RESUME_DIR": str(root / "resumes"),
        })
        client = app.test_client()

        session = client.get("/api/session")

        payload = session.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertNotIn("token", payload)
        self.assertRegex(payload["build_hash"], r"^[0-9a-f]{12}$")
        cookie = session.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        authenticated = client.post(
            "/api/profiles", json={"name": "cookie-session", "confirmed_fields": {}},
            headers={"X-Boss-Build": payload["build_hash"]},
        )
        self.assertNotEqual(authenticated.status_code, 403)

        without_session = app.test_client().post(
            "/api/profiles", json={"name": "blocked", "confirmed_fields": {}},
        )
        self.assertEqual(without_session.status_code, 403)

    def test_sensitive_configuration_gets_require_session_token(self):
        anonymous = self.app.test_client()
        anonymous.environ_base.pop("HTTP_X_BOSS_TOKEN", None)
        for path in (
            "/api/advanced-settings",
            "/api/tuning/experiments/missing/result",
            "/api/tuning/manifests/missing",
            "/api/tuning/rounds/missing/evidence",
        ):
            with self.subTest(path=path):
                self.assertEqual(anonymous.get(path).status_code, 403)

    def test_check_uses_returncode_not_log_keywords(self):
        completed = type("Completed", (), {
            "returncode": 1,
            "output_tail": "CDP 9222 检查失败，未登录",
            "failure_code": "process_failed",
            "ok": False,
        })()
        with mock.patch("webui.app.ScraperExecutor.execute", return_value=completed):
            response = self.client.get("/api/check")

        payload = response.get_json()
        self.assertFalse(payload["connected"])
        self.assertEqual(payload["returncode"], 1)

    def test_profile_is_normalized_and_persisted(self):
        response = self.client.put("/api/profile", json={
            "target_titles": "后端工程师, Python 后端",
            "must_skills": "Python,FastAPI",
            "min_salary": "25",
        })
        self.assertEqual(response.status_code, 200)

        profile = self.client.get("/api/profile").get_json()["profile"]
        self.assertEqual(profile["target_titles"], ["后端工程师", "Python 后端"])
        self.assertEqual(profile["min_salary"], 25.0)

    def test_task_contract_and_analysis_switch(self):
        task = self._create_scrape_task(analysis=False)

        self.assertEqual(task["kind"], "scrape")
        self.assertEqual(task["status"], "queued")
        self.assertIn(task["id"], pathlib.Path(task["output_path"]).stem)
        self.assertIn(task["id"], pathlib.Path(task["detail_output_path"]).stem)

        detail = self.client.get(f"/api/tasks/{task['id']}").get_json()["task"]
        self.assertFalse(detail["params"]["search"]["analysis"])
        history = self.client.get("/api/tasks").get_json()["tasks"]
        self.assertEqual(history[0]["id"], task["id"])

    def test_setup_chrome_is_a_persisted_task(self):
        response = self.client.post("/api/setup-chrome")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()["task"]["kind"], "setup_chrome")

    def test_task_can_be_cancelled_and_retried_with_same_params(self):
        task = self._create_scrape_task()

        cancelled = self.client.post(f"/api/tasks/{task['id']}/cancel")
        retried = self.client.post(f"/api/tasks/{task['id']}/retry")

        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.get_json()["task"]["status"], "interrupted")
        self.assertEqual(retried.status_code, 202)
        retry_task = retried.get_json()["task"]
        self.assertNotEqual(retry_task["id"], task["id"])
        self.assertEqual(retry_task["params"], task["params"])

    def test_result_summary_and_csv_are_scoped_to_task_paths(self):
        task = self._create_scrape_task()
        output_path = pathlib.Path(task["output_path"])
        detail_path = pathlib.Path(task["detail_output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({
            "keyword": "Python 后端",
            "city": "上海",
            "jobs": [{
                "job_id": "owned",
                "title": "Python 后端工程师",
                "boss_name": "产品公司",
                "salary": "25-35K",
                "location": "上海·浦东新区",
                "skills": "Python",
            }],
        }, ensure_ascii=False), encoding="utf-8")
        detail_path.write_text(json.dumps([{
            "job_id": "owned",
            "jd": "使用 FastAPI 开发服务",
            "skill_tags": ["FastAPI"],
        }], ensure_ascii=False), encoding="utf-8")
        (self.result_dir / "boss_jobs_newer-unrelated.json").write_text(
            json.dumps({"jobs": [{"job_id": "other", "title": "错误任务"}]}, ensure_ascii=False),
            encoding="utf-8",
        )

        result = self.client.get(f"/api/tasks/{task['id']}/result").get_json()
        summary = self.client.get(f"/api/tasks/{task['id']}/summary").get_json()
        csv_response = self.client.get(f"/api/tasks/{task['id']}/export.csv")

        self.assertEqual([job["job_id"] for job in result["jobs"]], ["owned"])
        self.assertGreaterEqual(result["jobs"][0]["match_score"], 70)
        self.assertEqual(result["jobs"][0]["missing_skills"], [])
        self.assertEqual(summary["summary"]["total_jobs"], 1)
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("text/csv", csv_response.content_type)
        self.assertIn("owned", csv_response.get_data(as_text=True))

    def test_unknown_filter_is_rejected_with_400(self):
        response = self.client.post("/api/tasks", json={"keyword": "Python", "salary": "999"})

        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertEqual(body["error_code"], "invalid_request")
        self.assertIn("salary", body["user_message"])
        self.assertNotIn("error", body)

    def test_missing_task_uses_safe_error_contract(self):
        response = self.client.get("/api/tasks/missing-task")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.get_json(),
            {"error_code": "not_found", "user_message": "任务不存在"},
        )

    def test_mutations_require_token_and_reject_untrusted_hosts_and_origins(self):
        anonymous = self.app.test_client()

        missing_token = anonymous.post("/api/setup-chrome")
        hostile_host = anonymous.get("/api/profile", headers={"Host": "evil.example"})
        hostile_origin = self.client.post(
            "/api/setup-chrome",
            headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
        )

        self.assertEqual(missing_token.status_code, 403)
        self.assertIn(hostile_host.status_code, {400, 403})
        self.assertEqual(hostile_origin.status_code, 403)

    def test_running_result_tolerates_transient_malformed_json(self):
        task = self._create_scrape_task()
        output_path = pathlib.Path(task["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("{", encoding="utf-8")

        response = self.client.get(f"/api/tasks/{task['id']}/result")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["jobs"], [])

    def test_frontend_serves_built_vue_entry_and_hashed_assets(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        assets = re.findall(r'(?:src|href)="(/static/assets/[^"]+)"', html)

        self.assertIn('<div id="app"></div>', html)
        self.assertNotIn('/src/main.ts', html)
        self.assertGreaterEqual(len(assets), 2)
        self.assertIn("no-cache", response.headers.get("Cache-Control", ""))
        for asset in assets:
            with self.subTest(asset=asset):
                asset_response = self.client.get(asset)
                self.assertEqual(asset_response.status_code, 200)
                self.assertGreater(len(asset_response.data), 100)
                asset_response.close()
        # discovery result cards (FR-064); raw AI fields stay excluded.
        self.assertNotIn("cdn.jsdelivr.net", html)
        self.assertNotIn("ai_rank", html)
        self.assertNotIn("match_reason", html)
        self.assertNotIn("ai_score", html)
        self.assertNotIn("/api/apply", html)
        self.assertNotIn("/api/export-csv", html)

    def test_obsolete_v2_frontend_is_retired(self):
        response = self.client.get("/v2")
        response.close()

        self.assertEqual(response.status_code, 404)
        obsolete_page = pathlib.Path(__file__).parents[1] / "webui" / "index-v2.html"
        self.assertFalse(obsolete_page.exists())


class SearchScopePreviewTests(unittest.TestCase):
    """SPEC011 T004: 后端权威范围预览端点测试。"""

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
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_normalizes_keywords_and_cities(self):
        """Scenario A: 重复关键词/城市别名统一为正式名称。"""
        resp = self.client.post("/api/search-scope/preview", json={
            "keywords": ["AI应用开发", " ai应用开发 "],
            "scope_kind": "cities",
            "cities": ["东莞", "东莞市"],
            "pages_per_combination": 3,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["scope"]["keywords"], ["AI应用开发"])
        self.assertEqual(data["scope"]["cities"], ["东莞"])
        self.assertEqual(data["scope"]["planned_pages"], 3)
        self.assertEqual(data["scope"]["task_size"], "small")

    def test_preview_rejects_unknown_city_with_suggestions(self):
        """Scenario A: 未知城市返回 422 + 建议，不自动替换。"""
        resp = self.client.post("/api/search-scope/preview", json={
            "keywords": ["AI"],
            "scope_kind": "cities",
            "cities": ["不存在的城市XYZ"],
            "pages_per_combination": 1,
        })
        self.assertEqual(resp.status_code, 422)
        data = resp.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error_code"], "city_validation_failed")

    def test_preview_rejects_nationwide_with_cities(self):
        """Scenario A: 全国与具体城市互斥。"""
        resp = self.client.post("/api/search-scope/preview", json={
            "keywords": ["AI"],
            "scope_kind": "nationwide",
            "cities": ["北京"],
            "pages_per_combination": 1,
        })
        self.assertEqual(resp.status_code, 422)

    def test_preview_page_boundaries(self):
        """Scenario A: 1/9=small, 10/19=medium, 20/30=large。"""
        cases = [
            (1, 1, 1, "small"),
            (3, 3, 1, "small"),    # 9
            (2, 5, 1, "medium"),   # 10
            (19, 1, 1, "medium"),  # 19
            (4, 5, 1, "large"),    # 20
            (10, 3, 1, "large"),   # 30
        ]
        cities = ["北京", "上海", "广州", "深圳", "杭州",
                  "天津", "西安", "苏州", "武汉", "厦门",
                  "长沙", "成都", "郑州", "重庆", "南京",
                  "青岛", "大连", "沈阳", "哈尔滨"]
        for kw_count, city_count, pages, expected_size in cases:
            resp = self.client.post("/api/search-scope/preview", json={
                "keywords": [f"kw{i}" for i in range(kw_count)],
                "scope_kind": "cities",
                "cities": cities[:city_count],
                "pages_per_combination": pages,
            })
            self.assertEqual(resp.status_code, 200, f"kw={kw_count} city={city_count} pages={pages}")
            data = resp.get_json()
            self.assertEqual(data["scope"]["task_size"], expected_size,
                             f"kw={kw_count} city={city_count} pages={pages}")

    def test_preview_rejects_over_thirty_pages(self):
        resp = self.client.post("/api/search-scope/preview", json={
            "keywords": [f"kw{i}" for i in range(31)],
            "scope_kind": "cities",
            "cities": ["北京"],
            "pages_per_combination": 1,
        })
        self.assertEqual(resp.status_code, 422)

    def test_preview_rejects_empty_keywords(self):
        resp = self.client.post("/api/search-scope/preview", json={
            "keywords": [],
            "scope_kind": "cities",
            "cities": ["北京"],
            "pages_per_combination": 1,
        })
        self.assertEqual(resp.status_code, 422)

    def test_preview_rejects_fractional_and_boolean_pages(self):
        for pages in (1.9, True):
            with self.subTest(pages=pages):
                resp = self.client.post("/api/search-scope/preview", json={
                    "keywords": ["AI应用开发"],
                    "scope_kind": "cities",
                    "cities": ["东莞"],
                    "pages_per_combination": pages,
                })
                self.assertEqual(resp.status_code, 400)

    def test_preview_does_not_change_workload_fields(self):
        """预览不修改任何任务工作量字段。"""
        # 先保存一组设置
        self.client.post("/api/advanced-settings", json={
            "settings": {
                "pages": 5,
                "inter_combo_delay": 10.0,
                "detail_batch_size": 15,
                "detail_interval": 2,
                "detail_reset_every": 4,
                "detail_batch_cooldown": 5,
                "screen_batch_size": 50,
                "screen_concurrency": 5,
                "match_batch_size": 4,
                "match_concurrency": 10,
            }
        })
        # 预览不应影响已保存的设置
        self.client.post("/api/search-scope/preview", json={
            "keywords": ["AI"],
            "scope_kind": "cities",
            "cities": ["北京"],
            "pages_per_combination": 3,
        })
        saved = self.client.get("/api/advanced-settings").get_json()
        self.assertEqual(saved["settings"]["pages"], 5)


class PipelineFeedbackRegressionTests(unittest.TestCase):
    """Regression coverage for pipeline feedback ID mapping."""

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
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        profile = self.client.post(
            "/api/profiles",
            json={"name": "pipeline-feedback", "confirmed_fields": {}},
        )
        self.profile_id = profile.get_json()["id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_interest_can_be_cancelled_with_original_pipeline_job_payload(self):
        job = {
            "job_id": "boss-external-001",
            "title": "Python 后端工程师",
            "salary": "20-30K",
            "location": "上海",
            "company": "示例公司",
            "job_link": "https://www.zhipin.com/job_detail/boss-external-001.html",
        }
        payload = {"profile_id": self.profile_id, "job": job}

        marked = self.client.post("/api/pipeline/jobs/interest", json=payload)
        self.assertEqual(marked.status_code, 200)
        self.assertEqual(
            len(self.app.config["TASK_STORE"].list_screening_interested(self.profile_id)),
            1,
        )

        cancelled = self.client.post(
            "/api/pipeline/jobs/interest/cancel",
            json=payload,
        )

        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(
            self.app.config["TASK_STORE"].list_screening_interested(self.profile_id),
            [],
        )

    def test_ai_screen_requires_the_exact_completed_scrape_task(self):
        missing = self.client.post("/api/ai-screen", json={
            "screening_fields": {},
            "profile_summary": "Python 后端",
        })
        unknown = self.client.post("/api/ai-screen", json={
            "screening_fields": {},
            "profile_summary": "Python 后端",
            "scrape_task_id": "unknown-task",
        })

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(unknown.status_code, 404)

        self.app.config["PIPELINE_TASKS"]["scrape-finished"] = {
            "kind": "scrape",
            "status": "done",
            "progress": {},
            "logs": [],
            "result": {"ok": True, "jobs": [{"job_id": "job-1"}]},
            "error": "",
        }
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
        ) as submit:
            accepted = self.client.post("/api/ai-screen", json={
                "screening_fields": {"salary": ["406"]},
                "profile_summary": "Python 后端",
                "scrape_task_id": "scrape-finished",
            })

        self.assertEqual(accepted.status_code, 200)
        submit.assert_called_once()
        # 任务函数签名末两位是 (scrape_task_id, resume_from_run_id)
        self.assertEqual(submit.call_args.args[-2], "scrape-finished")
        self.assertEqual(submit.call_args.args[-1], "")  # 无上次进度则不续跑


class SourceErrorClassificationTests(unittest.TestCase):
    """退出码 + 关键词 → 具体 failed_code 分类。"""

    def test_exit_10_with_captcha_keyword_returns_verification(self):
        from webui.source import _classify_failed_code
        self.assertEqual(
            _classify_failed_code(10, "触发风控：验证码拦截"),
            "source_verification_required",
        )

    def test_exit_10_with_slider_keyword_returns_verification(self):
        from webui.source import _classify_failed_code
        self.assertEqual(
            _classify_failed_code(10, "slider detected"),
            "source_verification_required",
        )

    def test_exit_10_with_429_keyword_returns_rate_limited(self):
        from webui.source import _classify_failed_code
        self.assertEqual(
            _classify_failed_code(10, "HTTP 429 Too Many Requests"),
            "source_rate_limited",
        )

    def test_exit_10_with_rate_limit_chinese_returns_rate_limited(self):
        from webui.source import _classify_failed_code
        self.assertEqual(
            _classify_failed_code(10, "被限流了"),
            "source_rate_limited",
        )

    def test_exit_10_generic_returns_blocked(self):
        from webui.source import _classify_failed_code
        self.assertEqual(
            _classify_failed_code(10, "连续空页"),
            "source_blocked",
        )

    def test_exit_1_with_login_keyword_returns_login_required(self):
        from webui.source import _classify_failed_code
        self.assertEqual(
            _classify_failed_code(1, "请先登录 BOSS 直聘"),
            "source_login_required",
        )

    def test_exit_1_generic_returns_blocked(self):
        from webui.source import _classify_failed_code
        self.assertEqual(
            _classify_failed_code(1, "环境异常"),
            "source_blocked",
        )

    def test_exit_2_returns_cdp_unavailable(self):
        from webui.source import _classify_failed_code
        self.assertEqual(
            _classify_failed_code(2, "connect ECONNREFUSED 127.0.0.1:9222"),
            "source_cdp_unavailable",
        )

    def test_unknown_exit_code_returns_blocked(self):
        from webui.source import _classify_failed_code
        self.assertEqual(
            _classify_failed_code(99, "unknown"),
            "source_blocked",
        )


class RunSearchAllFailTests(unittest.TestCase):
    """所有组合全失败时 run_search 返回 ok:False。"""

    @mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, ""))
    def test_all_combos_fail_returns_ok_false(self, _mock_chrome):
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        # 构造一个 source，fetch_list 永远失败
        class _FailSource:
            def preflight(self):
                return SourceOutcome.success(jobs=[], safe_log="ok", input_hash="")
            def fetch_list(self, plan_item):
                return SourceOutcome.failure(
                    failed_code="source_verification_required",
                    safe_log="list returncode=10 reason=验证码",
                )

        result = run_search(
            {"keyword": "Python", "city": ["上海"]},
            _FailSource(),
            pages=1,
            artifact_dir=self._tmp_dir(),
        )
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("hard_stop"), result)
        self.assertEqual(result.get("hard_stop_code"), "source_verification_required")
        self.assertIn("验证码", result["error"])

    @mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, ""))
    def test_partial_fail_still_returns_ok_true(self, _mock_chrome):
        from webui.pipeline_exec import run_search
        from webui.source import SourceOutcome

        call_count = [0]

        class _MixedSource:
            def preflight(self):
                return SourceOutcome.success(jobs=[], safe_log="ok", input_hash="")
            def fetch_list(self, plan_item):
                call_count[0] += 1
                if call_count[0] == 1:
                    return SourceOutcome.failure(
                        failed_code="source_timeout", safe_log="reason=单组合超时")
                return SourceOutcome.success(
                    jobs=[{"job_id": "j1", "source_url": "u1"}],
                    safe_log="ok", input_hash=plan_item.get("input_hash", ""))

        result = run_search(
            {"keyword": "A,B", "city": ["X"]},
            _MixedSource(),
            pages=1,
            artifact_dir=self._tmp_dir(),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["total_scraped"], 1)

    @staticmethod
    def _tmp_dir():
        import tempfile
        return tempfile.mkdtemp(prefix="boss_test_")


class AdvancedSettingsContractTests(unittest.TestCase):
    """SPEC011 T009: 高级设置 GET/PUT/POST 端点契约测试。"""

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
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token

    def tearDown(self):
        self.temp.cleanup()

    def _preview_scope(self, *, pages=3):
        resp = self.client.post("/api/search-scope/preview", json={
            "keywords": ["AI应用开发"],
            "scope_kind": "cities",
            "cities": ["东莞"],
            "pages_per_combination": pages,
        })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        return resp.get_json()["scope"]

    def test_get_returns_versioned_state(self):
        """GET 返回 selection、settings、config_schema_version。"""
        resp = self.client.get("/api/advanced-settings")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("selection", data)
        self.assertIn("settings", data)
        self.assertIn("config_schema_version", data)

    def test_create_app_imports_legacy_settings_once(self):
        root = pathlib.Path(self.temp.name) / "legacy-app"
        legacy_path = root / "advanced_settings.json"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(json.dumps({
            "pages": 9,
            "inter_combo_delay": 18.0,
            "detail_batch_size": 8,
            "detail_interval": 3.0,
            "detail_reset_every": 3,
            "detail_batch_cooldown": 6.0,
            "screen_batch_size": 30,
            "screen_concurrency": 3,
            "match_batch_size": 3,
            "match_concurrency": 5,
        }), encoding="utf-8")
        app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
            "ADVANCED_SETTINGS_PATH": str(legacy_path),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        state = app.config["TASK_STORE"].get_advanced_config_state()
        self.assertEqual(state["last_custom_config"]["inter_combo_delay"], 18.0)
        self.assertIsNotNone(state["legacy_imported_at"])
        self.assertNotIn("pages", state["last_custom_config"])

    def test_put_custom_saves_complete_config(self):
        """PUT /custom 保存完整九字段，返回 digest。"""
        resp = self.client.put("/api/advanced-settings/custom", json={
            "settings": {
                "inter_combo_delay": 10.0,
                "detail_batch_size": 15,
                "detail_interval": 2.0,
                "detail_reset_every": 4,
                "detail_batch_cooldown": 5.0,
                "screen_batch_size": 50,
                "screen_concurrency": 5,
                "match_batch_size": 4,
                "match_concurrency": 10,
            }
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["selection"], "custom")
        self.assertIn("config_digest", data)

    def test_put_custom_rejects_partial(self):
        """PUT /custom 缺字段返回 400。"""
        resp = self.client.put("/api/advanced-settings/custom", json={
            "settings": {"inter_combo_delay": 10.0}
        })
        self.assertEqual(resp.status_code, 400)

    def test_select_mode_stable(self):
        """POST /select-mode stable 返回对应配置。"""
        scope = self._preview_scope(pages=3)
        resp = self.client.post("/api/advanced-settings/select-mode", json={
            "mode": "stable",
            "scope_digest": scope["scope_digest"],
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["selection"], "stable")
        self.assertEqual(data["task_size"], "small")
        self.assertIn("settings", data)
        self.assertNotIn("pages", data["settings"])

    def test_select_mode_uses_backend_preview_size(self):
        """客户端 task_size 不能覆盖 scope digest 对应的后端规模。"""
        scope = self._preview_scope(pages=10)
        resp = self.client.post("/api/advanced-settings/select-mode", json={
            "mode": "balanced",
            "scope_digest": scope["scope_digest"],
            "task_size": "small",
        })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["task_size"], "medium")

    def test_select_mode_rejects_unknown_scope_digest(self):
        resp = self.client.post("/api/advanced-settings/select-mode", json={
            "mode": "stable", "scope_digest": "sha256:unknown",
        })
        self.assertEqual(resp.status_code, 409)

    def test_select_mode_custom_recovers_last(self):
        """先存自定义，再选 custom 能恢复。"""
        self.client.put("/api/advanced-settings/custom", json={
            "settings": {
                "inter_combo_delay": 42.0,
                "detail_batch_size": 7,
                "detail_interval": 3.0,
                "detail_reset_every": 2,
                "detail_batch_cooldown": 8.0,
                "screen_batch_size": 25,
                "screen_concurrency": 3,
                "match_batch_size": 2,
                "match_concurrency": 4,
            }
        })
        scope = self._preview_scope(pages=3)
        # 切到 stable
        self.client.post("/api/advanced-settings/select-mode", json={
            "mode": "stable", "scope_digest": scope["scope_digest"]
        })
        # 切回 custom
        resp = self.client.post("/api/advanced-settings/select-mode", json={
            "mode": "custom", "scope_digest": scope["scope_digest"]
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["selection"], "custom")
        self.assertEqual(data["settings"]["inter_combo_delay"], 42.0)
        persisted = json.loads(pathlib.Path(
            self.app.config["ADVANCED_SETTINGS_PATH"]
        ).read_text(encoding="utf-8"))
        self.assertEqual(persisted["inter_combo_delay"], 42.0)
        self.assertEqual(
            self.app.config["TASK_STORE"].get_advanced_config_state()["active_selection"],
            "custom",
        )

    def test_rollback_requires_target(self):
        """POST /rollback 缺 target 返回 400。"""
        resp = self.client.post("/api/advanced-settings/mode-versions/rollback", json={})
        self.assertEqual(resp.status_code, 400)


def _make_valid_manifest_payload_web(
    *, experiment_id: str, candidate_id: str, round_id: str,
) -> dict:
    """构造一份完整的合法 manifest payload（web 测试用）。"""
    import hashlib
    config = {
        "schema_version": 1,
        "inter_combo_delay": 5.0,
        "detail_batch_size": 10,
        "detail_interval": 2.0,
        "detail_reset_every": 3,
        "detail_batch_cooldown": 4.0,
        "screen_batch_size": 30,
        "screen_concurrency": 3,
        "match_batch_size": 2,
        "match_concurrency": 4,
    }
    config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)
    config["config_digest"] = "sha256:" + hashlib.sha256(
        config_json.encode("utf-8")
    ).hexdigest()
    scope = {
        "keywords": ["AI应用开发"],
        "scope_kind": "cities",
        "cities": ["东莞"],
        "pages_per_combination": 3,
    }
    scope_json = json.dumps(scope, ensure_ascii=False, sort_keys=True)
    scope_digest = "sha256:" + hashlib.sha256(
        scope_json.encode("utf-8")
    ).hexdigest()
    artifact_digest = "sha256:" + hashlib.sha256(b"artifact").hexdigest()
    return {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "candidate_id": candidate_id,
        "round_id": round_id,
        "task_id": f"task-{round_id}",
        "spec_version": "011-deep-configuration-probing",
        "objective": "测试候选在 list 阶段的耗时与完整性",
        "round_kind": "list",
        "strategy_step": "single_field",
        "repetition_index": 1,
        "preconditions": [
            {
                "id": "check_lease",
                "instruction": "确认实验租约由当前进程持有",
                "expected": "lease owner is current process",
                "on_failure": "block_and_report",
                "evidence_field": "preflight[0].evidence",
            },
            {
                "id": "check_input_artifact",
                "instruction": "确认冻结输入产物存在且摘要匹配",
                "expected": "artifact_digest matches frozen_input.artifact_digest",
                "on_failure": "block_and_report",
                "evidence_field": "preflight[1].evidence",
            },
        ],
        "frozen_input": {
            "input_version_id": "iv-1",
            "workload_id": "wl-1",
            "task_size": "small",
            "structure_index": 1,
            "scope": scope,
            "scope_digest": scope_digest,
            "artifact_path": f"tuning/{experiment_id}/inputs/iv-1.json",
            "artifact_digest": artifact_digest,
            "expected_input_count": 30,
            "planned_pages": 3,
        },
        "execution_config": config,
        "fixed_fields": {
            "keywords": ["AI应用开发"],
            "scope_kind": "cities",
            "cities": ["东莞"],
            "pages_per_combination": 3,
            "planned_pages": 3,
            "task_size": "small",
            "model_reference": "gpt-default",
            "build_identity": "v1",
        },
        "execution_steps": [
            {
                "seq": 1,
                "action": "start_round",
                "instruction": "按 manifest 启动轮次并写入 evidence",
                "expected_status": "running",
                "timeout_seconds": 600,
                "on_timeout": "stop_new_work_and_block_report",
                "named_retry": None,
                "evidence_field": "steps[0].evidence",
            },
            {
                "seq": 2,
                "action": "confirm_round",
                "instruction": "在 evidence 写入后确认轮次完成",
                "expected_status": "confirmed",
                "timeout_seconds": 60,
                "on_timeout": "stop",
                "named_retry": None,
                "evidence_field": "steps[1].evidence",
            },
        ],
        "monitoring": {
            "status_endpoint": f"/api/tuning/rounds/{round_id}",
            "polling_interval_seconds": 5,
            "max_observation_interval_seconds": 3600,
            "expected_stage_sequence": ["running", "confirmed"],
            "monotonic_counters": ["input_count", "terminal_count"],
            "hard_error_codes": ["hard_error"],
            "recoverable_error_rule": {
                "max_retries": 2,
                "backoff_ms": 1000,
            },
            "evidence_snapshot_interval_seconds": 30,
            "final_artifact_path": f"tuning/{experiment_id}/evidence/{round_id}.json",
        },
        "retry_policy": {
            "max_retries": 2,
            "backoff_ms": 1000,
            "recoverable_codes": ["captcha_required"],
        },
        "stop_conditions": [
            {
                "code": "captcha_required",
                "match": "program error_code equals captcha_required",
                "severity": "recoverable",
                "action": "execute_named_retry",
                "required_evidence": ["status_snapshot"],
            },
            {
                "code": "hard_error",
                "match": "program error_code equals hard_error",
                "severity": "fatal",
                "action": "block_and_report",
                "required_evidence": ["status_snapshot"],
            },
        ],
        "allowed_writes": [
            f"tuning/{experiment_id}/evidence/",
            f"tuning/{experiment_id}/tasks/",
        ],
        "required_artifacts": [
            {
                "artifact_type": "program_report",
                "path": f"tuning/{experiment_id}/evidence/{round_id}.json",
                "producer": "application",
                "existence_requirement": "required",
                "digest_requirement": "sha256",
                "minimum_fields": ["total_duration_ms", "terminal_count"],
                "absence_makes": "invalid",
            },
        ],
        "forbidden_actions": [
            "edit_source_code",
            "select_another_candidate",
            "overwrite_prior_results",
            "write_outside_allowed_paths",
            "adjust_acceptance_criteria",
        ],
        "report_contract": {
            "required_fields": [
                "schema_version", "report_id", "task_id", "experiment_id",
                "candidate_id", "round_id", "manifest_digest", "status",
                "preflight", "steps", "program_evidence", "artifacts",
                "stop_reason", "unexecuted_steps", "started_at",
                "finished_at",
            ],
            "forbidden_fields": [
                "parameter_suggestions", "candidate_ranking",
                "next_candidate", "mode_recommendation",
            ],
            "notes_policy": "observable_facts_only",
        },
    }


class TuningManifestRouteTests(unittest.TestCase):
    """SPEC011 T023 RED: 控制者 manifest/decision 路由与执行者路由测试。

    覆盖 contracts/http-api.md 第 4-6 节。
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
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        # 通过 store 直接创建实验+候选+轮次（T030 才会添加实验 HTTP 路由）
        from webui.store import TaskStore
        from webui.tuning import TuningController
        db_path = root / "state" / "webui.db"
        self.store = TaskStore(db_path)
        self.controller = TuningController(self.store)
        def scope(keyword_count, pages):
            return {
                "keywords": [f"接口结构{i}" for i in range(keyword_count)],
                "scope_kind": "cities", "cities": ["东莞"],
                "pages_per_combination": pages,
            }
        scopes = [
            ("small", scope(1, 3)), ("small", scope(2, 3)),
            ("medium", scope(2, 5)), ("medium", scope(3, 5)),
            ("large", scope(4, 5)), ("large", scope(5, 5)),
        ]
        self.experiment = self.controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope=scopes[0][1],
            quality_context=_tuning_quality_context(),
            workloads=[
                {"task_size": size, "structure_index": index % 2 + 1,
                 "scope": value}
                for index, (size, value) in enumerate(scopes)
            ],
        )
        self.controller.confirm_input(self.experiment["id"])
        self.bundle = self.store.get_tuning_input_bundle(self.experiment["id"])
        self.workload = self.bundle["workloads"][0]
        self.candidate = self.controller.add_candidate(
            experiment_id=self.experiment["id"],
            stage="list",
            strategy_step="single_field",
            config={
                "inter_combo_delay": 5.0,
                "detail_batch_size": 10,
                "detail_interval": 2.0,
                "detail_reset_every": 3,
                "detail_batch_cooldown": 4.0,
                "screen_batch_size": 30,
                "screen_concurrency": 3,
                "match_batch_size": 2,
                "match_concurrency": 4,
            },
        )
        self.round = self.controller.create_round(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            workload_id=self.workload["id"],
            round_kind="list",
            repetition_index=1,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _make_manifest(self) -> dict:
        manifest = _make_valid_manifest_payload_web(
            experiment_id=self.experiment["id"],
            candidate_id=self.candidate["id"],
            round_id=self.round["id"],
        )
        root = f"tuning/{self.experiment['id']}"
        scope = self.workload["scope"]
        manifest["execution_config"] = self.store.get_tuning_candidate(
            self.candidate["id"])["config"]
        manifest["frozen_input"].update({
            "input_version_id": self.bundle["input_version"]["id"],
            "workload_id": self.workload["id"],
            "task_size": self.workload["task_size"],
            "structure_index": self.workload["structure_index"],
            "scope_digest": scope["scope_digest"],
            "artifact_digest": self.workload["artifact_digest"],
            "quality_context_digest": self.bundle["input_version"][
                "quality_context_digest"
            ],
            "planned_pages": self.workload["planned_pages"],
            "artifact_manifest_path": f"{root}/input/{self.workload['id']}.json",
        })
        manifest["fixed_fields"] = {
            key: scope[key] for key in (
                "keywords", "scope_kind", "cities", "pages_per_combination",
                "planned_pages", "task_size",
            )
        }
        evidence_path = f"{root}/evidence/{self.round['id']}.json"
        manifest["monitoring"]["final_artifact_path"] = evidence_path
        manifest["allowed_writes"] = [evidence_path, f"{root}/artifacts/{self.round['id']}/"]
        manifest["required_artifacts"][0]["path"] = evidence_path
        return manifest

    # -- POST /api/tuning/experiments/{id}/manifests --------------------

    def test_issue_manifest_route_success(self):
        """POST /manifests 成功签发返回 201。"""
        manifest = self._make_manifest()
        resp = self.client.post(
            f"/api/tuning/experiments/{self.experiment['id']}/manifests",
            json=manifest,
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "issued")
        self.assertIsNotNone(data["manifest_id"])
        self.assertIsNotNone(data["manifest_digest"])
        self.assertIn("rendered_task_path", data)

    def test_issue_manifest_route_missing_field_returns_422(self):
        """POST /manifests 缺字段返回 422。"""
        manifest = self._make_manifest()
        del manifest["objective"]
        resp = self.client.post(
            f"/api/tuning/experiments/{self.experiment['id']}/manifests",
            json=manifest,
        )
        self.assertEqual(resp.status_code, 422)
        data = resp.get_json()
        self.assertFalse(data["ok"])

    def test_issue_manifest_route_placeholder_returns_422(self):
        """POST /manifests 包含占位符返回 422。"""
        manifest = self._make_manifest()
        manifest["objective"] = "<placeholder> 目标"
        resp = self.client.post(
            f"/api/tuning/experiments/{self.experiment['id']}/manifests",
            json=manifest,
        )
        self.assertEqual(resp.status_code, 422)

    # -- GET /api/tuning/manifests/{id} ---------------------------------

    def test_get_manifest_route_success(self):
        """GET /manifests/{id} 返回安全结构化 manifest。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        resp = self.client.get(
            f"/api/tuning/manifests/{issued['manifest_id']}"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["manifest_id"], issued["manifest_id"])
        self.assertEqual(
            data["manifest_digest"], issued["manifest_digest"]
        )
        self.assertIn("rendered_task_path", data)
        # 不返回凭据/敏感内容
        manifest_text = json.dumps(data.get("manifest", {}))
        self.assertNotIn("api_key", manifest_text.lower())

    def test_get_manifest_route_not_found(self):
        """GET /manifests/{id} 不存在返回 404。"""
        resp = self.client.get("/api/tuning/manifests/missing-id")
        self.assertEqual(resp.status_code, 404)

    # -- POST /api/tuning/manifests/{id}/execute ------------------------

    def test_execute_manifest_route_success(self):
        """POST /manifests/{id}/execute 启动轮次返回 202。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        resp = self.client.post(
            f"/api/tuning/manifests/{issued['manifest_id']}/execute"
        )
        self.assertEqual(resp.status_code, 202)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["round_id"], self.round["id"])
        self.assertEqual(data["child_task_id"], manifest["task_id"])
        self.assertIn("status_url", data)

    def test_execute_manifest_route_wrong_digest_returns_409(self):
        """POST /execute 在 manifest 被篡改后返回 409。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        # 篡改已签发的 manifest
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE tuning_task_manifests SET manifest_json = ? WHERE id = ?",
                ('{"tampered": true}', issued["manifest_id"]),
            )
        resp = self.client.post(
            f"/api/tuning/manifests/{issued['manifest_id']}/execute"
        )
        self.assertEqual(resp.status_code, 409)

    def test_manifest_child_persists_stage_artifact_before_reported(self):
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        result = {"jobs": [{"job_id": "job-1"}]}
        self.app.config["TUNING_ROUND_RUNNER"].execute = mock.Mock(
            return_value=result,
        )

        self.app.config["RUN_TUNING_MANIFEST_CHILD"](issued["manifest_id"])

        round_record = self.store.get_tuning_round(self.round["id"])
        self.assertEqual(round_record["status"], "reported")
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT id FROM tuning_stage_artifacts "
                "WHERE producer_round_id = ?",
                (self.round["id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        artifact = self.store.get_tuning_stage_artifact(row["id"])
        self.assertEqual(artifact["stage"], "list")
        artifact_path = pathlib.Path(self.temp.name) / artifact["artifact_path"]
        self.assertEqual(
            json.loads(artifact_path.read_text(encoding="utf-8")), result,
        )

    def test_manifest_child_does_not_report_when_stage_artifact_write_fails(self):
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        self.app.config["TUNING_ROUND_RUNNER"].execute = mock.Mock(
            return_value={"jobs": [{"job_id": "job-1"}]},
        )

        with mock.patch(
            "webui.tuning.TuningController.persist_stage_artifact",
            side_effect=OSError("artifact write failed"),
        ):
            with self.assertRaisesRegex(OSError, "artifact write failed"):
                self.app.config["RUN_TUNING_MANIFEST_CHILD"](
                    issued["manifest_id"]
                )

        round_record = self.store.get_tuning_round(self.round["id"])
        self.assertEqual(round_record["status"], "running")

    def test_manifest_child_persists_safe_ai_failure_instead_of_stalling(self):
        from webui.ai import AISecurityError

        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        self.app.config["TUNING_ROUND_RUNNER"].execute = mock.Mock(
            side_effect=AISecurityError("network_error"),
        )

        self.app.config["RUN_TUNING_MANIFEST_CHILD"](issued["manifest_id"])

        round_record = self.store.get_tuning_round(self.round["id"])
        self.assertEqual(round_record["status"], "reported")
        self.assertEqual(round_record["failure_code"], "network_error")
        evidence_path = (
            pathlib.Path(self.temp.name)
            / manifest["monitoring"]["final_artifact_path"]
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(evidence["error_counts"], {"network_error": 1})

    def test_create_app_reconciles_issued_manifest_after_restart(self):
        manifest = self._make_manifest()
        self.controller.issue_manifest(manifest)
        self.assertEqual(
            self.store.get_tuning_round(self.round["id"])["status"], "issued"
        )
        self.assertEqual(
            self.store.get_tuning_lease()["owner_round_id"], self.round["id"]
        )

        restarted = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(pathlib.Path(self.temp.name) / "results"),
            "DB_PATH": str(pathlib.Path(self.temp.name) / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        restarted_store = restarted.config["TASK_STORE"]

        self.assertEqual(
            restarted_store.get_tuning_round(self.round["id"])["status"],
            "uncertain",
        )
        experiment = restarted_store.get_tuning_experiment(
            self.experiment["id"]
        )
        self.assertEqual(experiment["status"], "blocked")
        self.assertEqual(
            experiment["blocked_code"], "restart_interrupted_round"
        )
        self.assertIsNone(
            restarted_store.get_tuning_lease()["owner_round_id"]
        )

    def test_create_app_reconciles_unconfirmed_report_after_restart(self):
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        self.store.update_tuning_round_status(
            self.round["id"], status="reported",
        )

        restarted = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(pathlib.Path(self.temp.name) / "results"),
            "DB_PATH": str(pathlib.Path(self.temp.name) / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        restarted_store = restarted.config["TASK_STORE"]

        self.assertEqual(
            restarted_store.get_tuning_round(self.round["id"])["status"],
            "uncertain",
        )
        experiment = restarted_store.get_tuning_experiment(
            self.experiment["id"]
        )
        self.assertEqual(experiment["status"], "blocked")
        self.assertEqual(
            experiment["blocked_code"], "restart_interrupted_round"
        )
        self.assertIsNone(
            restarted_store.get_tuning_lease()["owner_round_id"]
        )

    # -- GET /api/tuning/rounds/{round_id} ------------------------------

    def test_get_round_route_success(self):
        """GET /rounds/{id} 返回程序状态。"""
        manifest = self._make_manifest()
        self.controller.issue_manifest(manifest)
        resp = self.client.get(
            f"/api/tuning/rounds/{self.round['id']}"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["round"]["id"], self.round["id"])
        self.assertIn("status", data["round"])

    def test_get_round_route_not_found(self):
        """GET /rounds/{id} 不存在返回 404。"""
        resp = self.client.get("/api/tuning/rounds/missing-id")
        self.assertEqual(resp.status_code, 404)

    # -- POST /api/tuning/manifests/{id}/report -------------------------

    def test_submit_report_route_success(self):
        """POST /report 成功接受返回 201。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        report = self._make_valid_report(issued["manifest_digest"])
        resp = self.client.post(
            f"/api/tuning/manifests/{issued['manifest_id']}/report",
            json=report,
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertEqual(data["validation_status"], "accepted")

    def test_submit_report_route_invalid_returns_422(self):
        """POST /report 校验失败返回 422。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        report = self._make_valid_report(issued["manifest_digest"])
        report["manifest_digest"] = "sha256-wrong"
        resp = self.client.post(
            f"/api/tuning/manifests/{issued['manifest_id']}/report",
            json=report,
        )
        self.assertEqual(resp.status_code, 422)

    # -- GET /api/tuning/rounds/{round_id}/evidence ---------------------

    def test_get_evidence_route_success(self):
        """GET /rounds/{id}/evidence 返回安全聚合证据。"""
        manifest = self._make_manifest()
        self.controller.issue_manifest(manifest)
        # 记录一些测量事件
        self.controller.record_measurement(
            round_id=self.round["id"],
            event_type="stage",
            stage="list",
            duration_ms=1000,
            counts={"input_count": 30, "output_count": 30},
        )
        resp = self.client.get(
            f"/api/tuning/rounds/{self.round['id']}/evidence"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("evidence", data)

    # -- POST /api/tuning/experiments/{id}/decisions --------------------

    def test_post_decision_route_promote_success(self):
        """POST /decisions promote 成功返回 200。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        report = self._make_valid_report(issued["manifest_digest"])
        self.controller.accept_report(
            manifest_id=issued["manifest_id"], report=report,
        )
        resp = self.client.post(
            f"/api/tuning/experiments/{self.experiment['id']}/decisions",
            json={
                "candidate_id": self.candidate["id"],
                "decision": "promote",
                "reason_evidence": [self.round["id"]],
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])

    def test_post_decision_route_reject_success(self):
        """POST /decisions reject 成功返回 200。"""
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        report = self._make_valid_report(issued["manifest_digest"])
        self.controller.accept_report(
            manifest_id=issued["manifest_id"], report=report,
        )
        resp = self.client.post(
            f"/api/tuning/experiments/{self.experiment['id']}/decisions",
            json={
                "candidate_id": self.candidate["id"],
                "decision": "reject",
                "code": "hard_error",
                "reason_evidence": [self.round["id"]],
            },
        )
        self.assertEqual(resp.status_code, 200)

    def _make_valid_report(self, manifest_digest: str) -> dict:
        """构造一份完整的合法 executor report。"""
        manifest = self._make_manifest()
        evidence = {
            "program_report_path": manifest["required_artifacts"][0]["path"],
            "config_digest": manifest["execution_config"]["config_digest"],
            "scope_digest": manifest["frozen_input"]["scope_digest"],
            "input_artifact_digest": manifest["frozen_input"]["artifact_digest"],
            "total_duration_ms": 45000,
            "stage_durations_ms": {"list": 40000},
            "work_duration_ms": 40000,
            "wait_duration_ms": 5000, "retry_duration_ms": 0,
            "attempt_count": 1, "retry_count": 0,
            "input_count": 30, "terminal_count": 30,
            "success_count": 30, "failed_count": 0,
            "missing_count": 0, "duplicate_count": 0,
            "quality_diff_count": 0, "error_counts": {},
        }
        evidence_path = pathlib.Path(self.temp.name) / evidence["program_report_path"]
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        raw_evidence = json.dumps(
            evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        evidence_path.write_bytes(raw_evidence)
        evidence_digest = "sha256:" + hashlib.sha256(raw_evidence).hexdigest()
        return {
            "schema_version": 1,
            "report_id": "report-001",
            "task_id": manifest["task_id"],
            "experiment_id": manifest["experiment_id"],
            "candidate_id": manifest["candidate_id"],
            "round_id": manifest["round_id"],
            "manifest_digest": manifest_digest,
            "status": "completed",
            "preflight": [
                {"id": "check_lease", "result": "passed",
                 "evidence": "lease ok"},
                {"id": "check_input_artifact", "result": "passed",
                 "evidence": "frozen input checked"},
            ],
            "steps": [
                {"seq": 1, "status": "completed",
                 "evidence": "round started"},
                {"seq": 2, "status": "completed",
                 "evidence": "round confirmed"},
            ],
            "observations": {
                "total_duration_observed": 45000,
                "stages_observed": ["list", "done"],
            },
            "program_evidence": {**evidence, "program_report_digest": evidence_digest},
            "artifacts": [
                {
                    "artifact_type": "program_report",
                    "path": evidence["program_report_path"],
                    "digest": evidence_digest,
                    "exists": True,
                },
            ],
            "stop_reason": None,
            "unexecuted_steps": [],
            "executor_notes": ["所有步骤按任务单完成"],
            "started_at": "2026-07-29T10:00:00+08:00",
            "finished_at": "2026-07-29T10:01:30+08:00",
        }


class TuningExperimentRouteTests(unittest.TestCase):
    """SPEC011 T030 RED: 实验生命周期 HTTP 路由测试。

    覆盖 contracts/http-api.md 第 3 节：create / confirm-input / status /
    cancel / resume。
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
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _complete_workloads():
        return [
            {"task_size": "small", "structure_index": 1, "scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3}},
            {"task_size": "small", "structure_index": 2, "scope": {
                "keywords": ["AI应用开发", "智能体开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 4}},
            {"task_size": "medium", "structure_index": 1, "scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞", "深圳"], "pages_per_combination": 5}},
            {"task_size": "medium", "structure_index": 2, "scope": {
                "keywords": ["AI应用开发", "智能体开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 5}},
            {"task_size": "large", "structure_index": 1, "scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞", "深圳"], "pages_per_combination": 10}},
            {"task_size": "large", "structure_index": 2, "scope": {
                "keywords": ["AI应用开发", "智能体开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 10}},
        ]

    @staticmethod
    def _quality_context():
        return {
            "profile_summary": "AI应用开发候选人，掌握 Python、FastAPI、LangGraph 和 RAG。",
            "screening_fields": {
                "salary": ["403", "404", "405"],
                "experience": ["101", "103", "104"],
                "degree": ["202", "203"],
            },
            "profile_ref": "user-confirmed:test",
        }

    # -- POST /api/tuning/experiments ------------------------------------

    def test_create_experiment_route_returns_201(self):
        """POST /api/tuning/experiments 创建实验返回 201。"""
        resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
            "quality_context": self._quality_context(),
            "workloads": [
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
        })
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIsNotNone(data["experiment_id"])
        self.assertEqual(data["status"], "draft")

    def test_create_experiment_route_rejects_missing_quality_context(self):
        resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
            },
            "workloads": self._complete_workloads(),
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("quality_context", resp.get_json()["error"])

    def test_create_experiment_route_missing_fields_returns_400(self):
        """缺少必填字段返回 400。"""
        resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
        })
        self.assertEqual(resp.status_code, 400)

    # -- GET /api/tuning/experiments/{id} --------------------------------

    def test_get_experiment_route_returns_picture(self):
        """GET /api/tuning/experiments/{id} 返回实验快照。"""
        # 先创建实验
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
            "quality_context": self._quality_context(),
            "workloads": [
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
        })
        exp_id = create_resp.get_json()["experiment_id"]
        resp = self.client.get(f"/api/tuning/experiments/{exp_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["experiment"]["id"], exp_id)
        self.assertIn("status", data["experiment"])
        self.assertIn("can_cancel", data["experiment"])
        self.assertIn("can_resume", data["experiment"])

    def test_get_experiment_route_not_found_returns_404(self):
        """不存在的实验返回 404。"""
        resp = self.client.get("/api/tuning/experiments/nonexistent")
        self.assertEqual(resp.status_code, 404)

    # -- POST /api/tuning/experiments/{id}/cancel ------------------------

    def test_cancel_experiment_route_returns_200(self):
        """POST /cancel 取消实验返回 200。"""
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
            "quality_context": self._quality_context(),
            "workloads": [
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
        })
        exp_id = create_resp.get_json()["experiment_id"]
        resp = self.client.post(f"/api/tuning/experiments/{exp_id}/cancel")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        # 验证实验状态为 cancelled
        get_resp = self.client.get(f"/api/tuning/experiments/{exp_id}")
        self.assertEqual(
            get_resp.get_json()["experiment"]["status"], "cancelled")

    def test_cancel_experiment_route_not_found_returns_404(self):
        """取消不存在的实验返回 404。"""
        resp = self.client.post(
            "/api/tuning/experiments/nonexistent/cancel")
        self.assertEqual(resp.status_code, 404)

    # -- POST /api/tuning/experiments/{id}/confirm-input -----------------

    def test_confirm_input_route_advances_through_preflight(self):
        """确认后完成本地 preflight 并进入可签发状态。"""
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
            "quality_context": self._quality_context(),
            "workloads": self._complete_workloads(),
        })
        exp_id = create_resp.get_json()["experiment_id"]
        resp = self.client.post(
            f"/api/tuning/experiments/{exp_id}/confirm-input")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "awaiting_instruction")
        self.assertTrue(data["input_version_id"])
        self.assertEqual(len(data["scope_digest"]), 64)
        int(data["scope_digest"], 16)
        self.assertEqual(len(data["workload_digests"]), 6)

    def test_create_persists_draft_input_and_all_workloads(self):
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
            },
            "quality_context": self._quality_context(),
            "workloads": self._complete_workloads(),
        })
        self.assertEqual(create_resp.status_code, 201, create_resp.get_json())
        exp_id = create_resp.get_json()["experiment_id"]
        from webui.store import TaskStore
        store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        bundle = store.get_tuning_input_bundle(exp_id)
        self.assertEqual(bundle["input_version"]["status"], "draft")
        self.assertEqual(len(bundle["workloads"]), 6)
        self.assertEqual(
            {item["task_size"] for item in bundle["workloads"]},
            {"small", "medium", "large"})

    def test_confirm_input_rejects_incomplete_workload_matrix(self):
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
            },
            "quality_context": self._quality_context(),
            "workloads": [
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
        })
        exp_id = create_resp.get_json()["experiment_id"]
        resp = self.client.post(
            f"/api/tuning/experiments/{exp_id}/confirm-input")
        self.assertEqual(resp.status_code, 409)

    def test_confirm_input_rejects_duplicate_workload_structures(self):
        workloads = self._complete_workloads()
        for start in (0, 2, 4):
            workloads[start + 1]["scope"] = dict(workloads[start]["scope"])
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
            },
            "quality_context": self._quality_context(),
            "workloads": workloads,
        })
        exp_id = create_resp.get_json()["experiment_id"]
        resp = self.client.post(
            f"/api/tuning/experiments/{exp_id}/confirm-input"
        )
        self.assertEqual(resp.status_code, 409)
        self.assertIn("不同", resp.get_json()["error"])
        self.assertEqual(resp.get_json()["error_code"], "input_incomplete")

    # -- POST /api/tuning/experiments/{id}/resume ------------------------

    def test_resume_experiment_route_blocked_to_awaiting(self):
        """POST /resume 从 blocked 恢复到 awaiting_instruction。"""
        # 创建并推进到 blocked
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
            "quality_context": self._quality_context(),
            "workloads": [
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
        })
        exp_id = create_resp.get_json()["experiment_id"]
        # 推进到 preflight 再到 blocked
        from webui.store import TaskStore
        root = pathlib.Path(self.temp.name)
        store = TaskStore(root / "state" / "webui.db")
        store.update_tuning_experiment_status(exp_id, status="preflight")
        store.update_tuning_experiment_status(
            exp_id, status="blocked",
            blocked_code="test_block",
            blocked_reason="测试阻断")
        resp = self.client.post(
            f"/api/tuning/experiments/{exp_id}/resume")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])

    def test_resume_experiment_route_not_blocked_returns_409(self):
        """非 blocked 状态恢复返回 409。"""
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"],
                "scope_kind": "cities",
                "cities": ["东莞"],
                "pages_per_combination": 3,
            },
            "quality_context": self._quality_context(),
            "workloads": [
                {"task_size": "small", "structure_index": 1, "scope": {}},
            ],
        })
        exp_id = create_resp.get_json()["experiment_id"]
        # draft 状态不能 resume
        resp = self.client.post(
            f"/api/tuning/experiments/{exp_id}/resume")
        self.assertEqual(resp.status_code, 409)

    def test_incomplete_result_is_visible_but_not_applicable(self):
        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
            },
            "quality_context": self._quality_context(),
            "workloads": [],
        })
        exp_id = create_resp.get_json()["experiment_id"]
        result = self.client.get(f"/api/tuning/experiments/{exp_id}/result")
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.get_json()["can_apply"])
        apply_resp = self.client.post(
            f"/api/tuning/experiments/{exp_id}/apply",
            json={"candidate_mode_version_digest": "sha256:not-ready"},
        )
        self.assertEqual(apply_resp.status_code, 409)

    def test_zero_round_candidate_cannot_complete_or_apply(self):
        from webui.store import TaskStore
        from webui.tuning import TuningController

        create_resp = self.client.post("/api/tuning/experiments", json={
            "spec_version": "011-deep-configuration-probing",
            "source_scope": {
                "keywords": ["AI应用开发"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 3,
            },
            "quality_context": self._quality_context(),
            "workloads": [],
        })
        exp_id = create_resp.get_json()["experiment_id"]
        root = pathlib.Path(self.temp.name)
        store = TaskStore(root / "state" / "webui.db")
        controller = TuningController(store)
        config = {
            "inter_combo_delay": 10.0, "detail_batch_size": 15,
            "detail_interval": 2.0, "detail_reset_every": 4,
            "detail_batch_cooldown": 5.0, "screen_batch_size": 50,
            "screen_concurrency": 5, "match_batch_size": 4,
            "match_concurrency": 10,
        }
        matrix = {
            mode: {size: dict(config) for size in ("small", "medium", "large")}
            for mode in ("stable", "balanced", "extreme")
        }
        custom_digest = store.save_custom_config({**config, "detail_batch_size": 8})
        previous_id = store.create_mode_version(matrix=matrix, manual_ranges={})
        store.apply_mode_version(previous_id)
        for status in ("preflight", "awaiting_instruction", "queued", "running"):
            store.update_tuning_experiment_status(exp_id, status=status)
        candidate = controller.create_candidate_mode_version(
            experiment_id=exp_id, matrix=matrix)
        store.update_tuning_experiment_status(exp_id, status="evaluating")
        with self.assertRaises(ValueError):
            store.update_tuning_experiment_status(exp_id, status="completed")

        result = self.client.get(f"/api/tuning/experiments/{exp_id}/result")
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertFalse(result.get_json()["can_apply"])
        self.assertEqual(
            result.get_json()["candidate_mode_version_digest"],
            candidate["version_digest"])

        apply_resp = self.client.post(
            f"/api/tuning/experiments/{exp_id}/apply",
            json={"candidate_mode_version_digest": candidate["version_digest"]},
        )
        self.assertEqual(apply_resp.status_code, 409, apply_resp.get_json())
        self.assertEqual(
            store.get_advanced_config_state()["active_mode_version_id"],
            previous_id)
        self.assertEqual(
            store.get_advanced_config_state()["last_custom_digest"], custom_digest)

        self.assertEqual(
            store.get_advanced_config_state()["last_custom_digest"], custom_digest)


if __name__ == "__main__":
    unittest.main()
