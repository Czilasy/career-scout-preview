import json
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

import requests

from webui.app import create_app


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


if __name__ == "__main__":
    unittest.main()
