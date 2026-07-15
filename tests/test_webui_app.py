import json
import pathlib
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

        self.assertEqual(session.get_json(), {"status": "ok"})
        cookie = session.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        authenticated = client.post(
            "/api/profiles", json={"name": "cookie-session", "confirmed_fields": {}},
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

    def test_frontend_uses_persistent_responsive_workspace_contract(self):
        response = self.client.get("/")
        html = response.get_data(as_text=True)
        response.close()

        # Dark theme workbench
        self.assertIn("color-scheme: dark", html)
        # Settings panel on the left (default expanded, no collapse toggle)
        self.assertIn('id="settingsPanel"', html)
        self.assertIn('data-pane="config"', html)
        # Single-column job card flow
        self.assertIn('id="jobCardList"', html)
        # Card fields: name, company, salary, location, JD excerpt
        self.assertIn("job-card", html)
        # Feedback buttons (interested / not interested) — no navigation
        self.assertIn("感兴趣", html)
        self.assertIn("不感兴趣", html)
        # Search and profile endpoints
        self.assertIn("/api/profiles", html)
        self.assertIn("/api/search-runs", html)
        self.assertIn("/api/ai-settings", html)
        # Responsive: narrow-screen drawer
        self.assertIn("@media (max-width: 720px)", html)
        # No external CDN, no raw AI scores/reasons, no auto-apply.
        # Note: match_score is approved by spec 004 for program-validated
        # discovery result cards (FR-064); raw AI fields stay excluded.
        self.assertNotIn("cdn.jsdelivr.net", html)
        self.assertNotIn("ai_rank", html)
        self.assertNotIn("match_reason", html)
        self.assertNotIn("ai_score", html)
        self.assertNotIn("/api/apply", html)
        self.assertNotIn("/api/export-csv", html)

    def test_screening_design_prototype_has_a_browser_route(self):
        response = self.client.get("/screening-prototype")
        html = response.get_data(as_text=True)
        response.close()

        self.assertEqual(response.status_code, 200)
        self.assertIn("筛选工作台原型（模拟）", html)


class ScreeningTokenProtectionTests(unittest.TestCase):
    """T009: screening GET endpoints require X-Boss-Token (except filter-options)."""

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
        # 用未注入 token 的 client 模拟无凭据请求
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_sensitive_screening_get_rejected_without_token(self):
        for path in (
            "/api/screening/runs/abc",
            "/api/screening/runs/abc/matches",
            "/api/screening/runs/abc/mismatches",
            "/api/screening/interested",
            "/api/screening/trash",
        ):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 403, f"{path} 应要求 token")

    def test_filter_options_open_without_token(self):
        # filter-options 是公开枚举，不应被 token 拒绝（未实现时 404，但不是 403）
        resp = self.client.get("/api/screening/filter-options")
        self.assertNotEqual(resp.status_code, 403)


class ScreeningResumeAPITests(unittest.TestCase):
    """T017: screening resume upload/suggest — token, privacy, structure."""

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
        session = self.client.get("/api/session")
        self.token = session.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        self.bare_client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def _upload(self, content=None, filename="resume.txt"):
        from io import BytesIO
        if content is None:
            content = "Python 后端 5 年经验 FastAPI".encode("utf-8")
        return self.client.post(
            "/api/screening/resume",
            data={"file": (BytesIO(content), filename)},
            content_type="multipart/form-data",
        )

    def _configure_ai(self):
        store = self.app.config["TASK_STORE"]
        store.save_ai_settings(
            "https://api.example.com/v1/chat/completions", "api.example.com",
        )

    # -- token protection --

    def test_upload_resume_rejected_without_token(self):
        from io import BytesIO
        resp = self.bare_client.post(
            "/api/screening/resume",
            data={"file": (BytesIO(b"test"), "resume.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 403)

    def test_suggest_rejected_without_token(self):
        resp = self.bare_client.post(
            "/api/screening/resume/suggest",
            json={"resume_id": "any"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_filter_options_returns_seven_classes_without_token(self):
        resp = self.bare_client.get("/api/screening/filter-options")
        self.assertEqual(resp.status_code, 200)
        options = resp.get_json()["options"]
        for key in ("salary", "experience", "degree", "scale", "stage", "industry", "city"):
            self.assertIn(key, options)
            self.assertEqual(options[key][0], {"label": "不限", "value": ""})

    # -- privacy: resume text never in response --

    def test_upload_response_excludes_resume_text(self):
        resp = self._upload(content=b"SECRET_RESUME_CONTENT_42")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertNotIn("extracted_text", body)
        self.assertNotIn("SECRET_RESUME_CONTENT_42", json.dumps(body, ensure_ascii=False))

    def test_suggest_response_excludes_resume_text(self):
        upload = self._upload(content=b"SECRET_RESUME_CONTENT_99")
        resume_id = upload.get_json()["resume_id"]
        resp = self.client.post(
            "/api/screening/resume/suggest",
            json={"resume_id": resume_id},
        )
        self.assertNotIn("SECRET_RESUME_CONTENT_99", json.dumps(resp.get_json(), ensure_ascii=False))

    # -- suggest structure --

    def test_suggest_ai_unavailable_when_not_configured(self):
        upload = self._upload()
        resume_id = upload.get_json()["resume_id"]
        resp = self.client.post(
            "/api/screening/resume/suggest",
            json={"resume_id": resume_id},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "ai_unavailable")

    def test_suggest_ok_returns_seven_fields(self):
        self._configure_ai()
        ai_payload = {
            "city": "上海", "salary": "405", "experience": "105",
            "degree": "203", "scale": "303", "stage": "804", "industry": "1001",
        }
        with mock.patch("webui.ai.keyring.get_password", return_value="secret-key"):
            with mock.patch("webui.ai.requests.post") as mp:
                mr = mock.MagicMock()
                mr.status_code = 200
                mr.json.return_value = {
                    "choices": [{"message": {"content": json.dumps(ai_payload, ensure_ascii=False)}}]
                }
                mp.return_value = mr
                upload = self._upload()
                resume_id = upload.get_json()["resume_id"]
                resp = self.client.post(
                    "/api/screening/resume/suggest",
                    json={"resume_id": resume_id},
                )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["status"], "ok")
        for field in ("city", "salary", "experience", "degree", "scale", "stage", "industry"):
            self.assertIn(field, body["suggestions"])

    def test_suggest_ai_failure_returns_ai_unavailable(self):
        self._configure_ai()
        with mock.patch("webui.ai.keyring.get_password", return_value="secret-key"):
            with mock.patch("webui.ai.requests.post", side_effect=requests.Timeout("timed out")):
                upload = self._upload()
                resume_id = upload.get_json()["resume_id"]
                resp = self.client.post(
                    "/api/screening/resume/suggest",
                    json={"resume_id": resume_id},
                )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "ai_unavailable")

    # -- api key never in response --

    def test_suggest_success_excludes_api_key(self):
        self._configure_ai()
        with mock.patch("webui.ai.keyring.get_password", return_value="SECRET-KEY-777"):
            with mock.patch("webui.ai.requests.post") as mp:
                mr = mock.MagicMock()
                mr.status_code = 200
                mr.json.return_value = {
                    "choices": [{"message": {"content": json.dumps({"city": "上海"}, ensure_ascii=False)}}]
                }
                mp.return_value = mr
                upload = self._upload()
                resume_id = upload.get_json()["resume_id"]
                resp = self.client.post(
                    "/api/screening/resume/suggest",
                    json={"resume_id": resume_id},
                )
        self.assertNotIn("SECRET-KEY-777", json.dumps(resp.get_json(), ensure_ascii=False))

    def test_suggest_failure_excludes_api_key(self):
        self._configure_ai()
        with mock.patch("webui.ai.keyring.get_password", return_value="SECRET-KEY-666"):
            with mock.patch("webui.ai.requests.post", side_effect=requests.Timeout("timed out")):
                upload = self._upload()
                resume_id = upload.get_json()["resume_id"]
                resp = self.client.post(
                    "/api/screening/resume/suggest",
                    json={"resume_id": resume_id},
                )
        self.assertNotIn("SECRET-KEY-666", json.dumps(resp.get_json(), ensure_ascii=False))


class ScreeningExecutionAPITests(unittest.TestCase):
    """T023: POST /api/screening/runs — execution, freeze, city fallback, artifact check.

    Mocks ``webui.app.execute_first_layer`` with a side_effect that really
    advances the run status through the store, so the HTTP endpoint's full
    flow (freeze -> create run -> execute -> return final status) is exercised.
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
        session = self.client.get("/api/session")
        self.token = session.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        self.bare_client = self.app.test_client()
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        self.temp.cleanup()

    def _fake_execute_ok(self, jobs=None, source_count=None):
        """Return a side_effect that advances run status to succeeded."""
        if jobs is None:
            jobs = [{"job_id": "job-1", "title": "Python"}]
        source_count = source_count if source_count is not None else len(jobs)

        def _impl(filters, keyword, *, output_path, python_executable, store=None, run_id=None,
                  **_execution_limits):
            if store and run_id:
                store.update_screening_run_status(run_id, "running")
                store.update_screening_run_status(run_id, "succeeded", source_count=source_count)
            return {"jobs": jobs, "source_count": source_count, "status": "succeeded"}

        return _impl

    def _fake_execute_fail(self):
        """Return a side_effect that advances run to failed then raises."""

        def _impl(filters, keyword, *, output_path, python_executable, store=None, run_id=None,
                  **_execution_outputs):
            if store and run_id:
                store.update_screening_run_status(run_id, "running")
                store.update_screening_run_status(run_id, "failed")
            raise RuntimeError("抓取器执行失败: returncode=1")

        return _impl

    # -- execution success --

    def test_create_run_returns_201_with_run_id_and_succeeded_status(self):
        with mock.patch("webui.app.execute_first_layer",
                        side_effect=self._fake_execute_ok(
                            jobs=[{"job_id": "job-1", "title": "Python"},
                                  {"job_id": "job-2", "title": "Java"}], source_count=2)):
            resp = self.client.post("/api/screening/runs", json={
                "filters": {"city": "上海", "salary": "405"},
                "keyword": "Python 后端",
            })
        self.assertEqual(resp.status_code, 201)
        body = resp.get_json()
        self.assertIn("run_id", body)
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["source_count"], 2)

    def test_create_run_persists_run_with_frozen_filters(self):
        with mock.patch("webui.app.execute_first_layer",
                        side_effect=self._fake_execute_ok()):
            resp = self.client.post("/api/screening/runs", json={
                "filters": {"city": "上海", "salary": "405"},
                "keyword": "Python",
            })
        run_id = resp.get_json()["run_id"]
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["source_count"], 1)
        self.assertEqual(run["frozen_filters"]["city"], "上海")
        self.assertEqual(run["frozen_filters"]["salary"], "405")

    # -- filter freezing: faithful record of allowed fields --

    def test_create_run_freezes_empty_string_fields(self):
        # 空字符串忠实记录用户未填的字段，核验时跳过
        with mock.patch("webui.app.execute_first_layer",
                        side_effect=self._fake_execute_ok()):
            resp = self.client.post("/api/screening/runs", json={
                "filters": {"city": "上海", "salary": "", "experience": ""},
                "keyword": "Python",
            })
        self.assertEqual(resp.status_code, 201)
        run_id = resp.get_json()["run_id"]
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["frozen_filters"]["salary"], "")
        self.assertEqual(run["frozen_filters"]["experience"], "")

    def test_create_run_freezes_empty_filters(self):
        with mock.patch("webui.app.execute_first_layer",
                        side_effect=self._fake_execute_ok()):
            resp = self.client.post("/api/screening/runs", json={
                "filters": {},
                "keyword": "Python",
            })
        self.assertEqual(resp.status_code, 201)
        run_id = resp.get_json()["run_id"]
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["frozen_filters"], {})

    # -- city empty fallback: frozen snapshot records empty, execute maps to nationwide --

    def test_create_run_empty_city_passes_empty_in_frozen_snapshot(self):
        captured = {}

        def capture(filters, keyword, *, output_path, python_executable, store=None, run_id=None,
                    **_execution_outputs):
            captured["filters"] = filters
            if store and run_id:
                store.update_screening_run_status(run_id, "running")
                store.update_screening_run_status(run_id, "succeeded", source_count=0)
            return {"jobs": [], "source_count": 0, "status": "succeeded"}

        with mock.patch("webui.app.execute_first_layer", side_effect=capture):
            resp = self.client.post("/api/screening/runs", json={
                "filters": {"city": "", "salary": "405"},
                "keyword": "Python",
            })
        self.assertEqual(resp.status_code, 201)
        # 冻结快照忠实记录空 city，下游 filters_to_search_params 负责兜底全国
        self.assertEqual(captured["filters"]["city"], "")
        self.assertEqual(captured["filters"]["salary"], "405")

    # -- execution failure / artifact check --

    def test_create_run_execution_failure_returns_500_with_failed_status(self):
        with mock.patch("webui.app.execute_first_layer",
                        side_effect=self._fake_execute_fail()):
            resp = self.client.post("/api/screening/runs", json={
                "filters": {"city": "上海"},
                "keyword": "Python",
            })
        self.assertEqual(resp.status_code, 500)
        body = resp.get_json()
        self.assertIn("run_id", body)
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error_code"], "execution_failed")
        # run 持久化为 failed，客户端可凭 run_id 查询
        run = self.store.get_screening_run(body["run_id"])
        self.assertEqual(run["status"], "failed")

    # -- parameter validation --

    def test_create_run_missing_keyword_returns_400(self):
        with mock.patch("webui.app.execute_first_layer",
                        side_effect=self._fake_execute_ok()):
            resp = self.client.post("/api/screening/runs", json={
                "filters": {"city": "上海"},
            })
        self.assertEqual(resp.status_code, 400)

    def test_create_run_filters_not_dict_returns_400(self):
        with mock.patch("webui.app.execute_first_layer",
                        side_effect=self._fake_execute_ok()):
            resp = self.client.post("/api/screening/runs", json={
                "filters": ["city", "上海"],
                "keyword": "Python",
            })
        self.assertEqual(resp.status_code, 400)

    def test_create_run_filters_with_disallowed_keys_returns_400(self):
        # is_valid_filters 在冻结前拒绝含非法字段的请求
        with mock.patch("webui.app.execute_first_layer",
                        side_effect=self._fake_execute_ok()):
            resp = self.client.post("/api/screening/runs", json={
                "filters": {"city": "上海", "bad": "x"},
                "keyword": "Python",
            })
        self.assertEqual(resp.status_code, 400)

    # -- token protection --

    def test_create_run_rejected_without_token(self):
        resp = self.bare_client.post("/api/screening/runs", json={
            "filters": {"city": "上海"},
            "keyword": "Python",
        })
        self.assertEqual(resp.status_code, 403)


class ScreeningZoneQueryAPITests(unittest.TestCase):
    """T034: US3 集成测试 — 分流、区域清空、不标原因、排序。

    覆盖场景：
    1. 混合 jobs 经 POST /api/screening/runs 后正确分流到 matches/mismatches
    2. 新 run 的 matches/mismatches 不混入旧 run 结果（区域清空）
    3. mismatches 接口不返回排除原因/字段
    4. matches 按 jobs 在搜索产物中的抓回顺序排列
    5. GET /api/screening/runs/{run_id} 返回正确计数
    6. run 不存在返回 404
    7. 无 token 拒绝访问
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
        session = self.client.get("/api/session")
        self.token = session.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        self.bare_client = self.app.test_client()
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        self.temp.cleanup()

    def _fake_execute_with_jobs(self, jobs):
        """Return side_effect that returns the given jobs and advances status."""
        def _impl(filters, keyword, *, output_path, python_executable, store=None, run_id=None,
                  **_execution_outputs):
            # 把 jobs 写入产物文件，让 GET /matches 能读到 job 详情
            output_path = pathlib.Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False), encoding="utf-8")
            if store and run_id:
                store.update_screening_run_status(run_id, "running")
                store.update_screening_run_status(run_id, "succeeded", source_count=len(jobs))
            return {"jobs": jobs, "source_count": len(jobs), "status": "succeeded"}
        return _impl

    def _create_run(self, filters, keyword, jobs):
        """Helper: mock execute + POST /api/screening/runs, return response."""
        with mock.patch("webui.app.execute_first_layer",
                        side_effect=self._fake_execute_with_jobs(jobs)):
            return self.client.post("/api/screening/runs", json={
                "filters": filters,
                "keyword": keyword,
            })

    def _job(self, job_id, **overrides):
        from tests.test_screening_fixtures import sample_screening_job
        job = sample_screening_job(job_id=job_id)
        job.update(overrides)
        return job

    def _job_ids(self, items):
        return [item["job_id"] for item in items]

    # -- 混合分流 --

    def test_mixed_jobs_partitioned_to_matches_and_mismatches(self):
        # A/C 进符合区，B 进不符合区（scale 不匹配冻结条件）
        jobs = [
            self._job("job-A"),
            self._job("job-B", company_scale="20-99人"),
            self._job("job-C"),
        ]
        resp = self._create_run({"scale": "303"}, "Python", jobs)
        self.assertEqual(resp.status_code, 201)
        run_id = resp.get_json()["run_id"]

        matches = self.client.get(f"/api/screening/runs/{run_id}/matches").get_json()
        mismatches = self.client.get(f"/api/screening/runs/{run_id}/mismatches").get_json()
        self.assertEqual(self._job_ids(matches["items"]), ["job-A", "job-C"])
        self.assertEqual(self._job_ids(mismatches["items"]), ["job-B"])

    def test_all_match_jobs_all_in_matches(self):
        jobs = [self._job("job-A"), self._job("job-B")]
        resp = self._create_run({}, "Python", jobs)
        run_id = resp.get_json()["run_id"]
        matches = self.client.get(f"/api/screening/runs/{run_id}/matches").get_json()
        mismatches = self.client.get(f"/api/screening/runs/{run_id}/mismatches").get_json()
        self.assertEqual(self._job_ids(matches["items"]), ["job-A", "job-B"])
        self.assertEqual(mismatches["items"], [])

    def test_all_mismatch_jobs_all_in_mismatches(self):
        jobs = [self._job("job-A", company_scale="20-99人"),
                self._job("job-B", company_scale="20-99人")]
        resp = self._create_run({"scale": "303"}, "Python", jobs)
        run_id = resp.get_json()["run_id"]
        matches = self.client.get(f"/api/screening/runs/{run_id}/matches").get_json()
        mismatches = self.client.get(f"/api/screening/runs/{run_id}/mismatches").get_json()
        self.assertEqual(matches["items"], [])
        self.assertEqual(self._job_ids(mismatches["items"]), ["job-A", "job-B"])

    # -- 区域清空：新 run 不混入旧 run 结果 --

    def test_new_run_matches_does_not_include_old_run_results(self):
        # run1 的符合区有 job-A
        resp1 = self._create_run({}, "Python", [self._job("job-A")])
        run1_id = resp1.get_json()["run_id"]
        # run2 的符合区只有 job-B
        resp2 = self._create_run({}, "Python", [self._job("job-B")])
        run2_id = resp2.get_json()["run_id"]
        matches2 = self.client.get(f"/api/screening/runs/{run2_id}/matches").get_json()
        self.assertEqual(self._job_ids(matches2["items"]), ["job-B"])
        self.assertNotIn("job-A", self._job_ids(matches2["items"]))

    def test_new_run_mismatches_does_not_include_old_run_results(self):
        resp1 = self._create_run({"scale": "303"}, "Python",
                                  [self._job("job-A", company_scale="20-99人")])
        run1_id = resp1.get_json()["run_id"]
        resp2 = self._create_run({"scale": "303"}, "Python",
                                  [self._job("job-B", company_scale="20-99人")])
        run2_id = resp2.get_json()["run_id"]
        mismatches2 = self.client.get(f"/api/screening/runs/{run2_id}/mismatches").get_json()
        self.assertEqual(self._job_ids(mismatches2["items"]), ["job-B"])

    def test_old_run_results_preserved_after_new_run(self):
        # 旧 run 结果作为历史保留，不被新 run 清空
        resp1 = self._create_run({}, "Python", [self._job("job-A")])
        run1_id = resp1.get_json()["run_id"]
        self._create_run({}, "Python", [self._job("job-B")])
        matches1 = self.client.get(f"/api/screening/runs/{run1_id}/matches").get_json()
        self.assertEqual(self._job_ids(matches1["items"]), ["job-A"])

    # -- 不标原因：mismatches 不返回排除原因/字段 --

    def test_mismatches_response_has_no_reason_field(self):
        jobs = [self._job("job-A", company_scale="20-99人"),
                self._job("job-B", company_industry="金融")]
        resp = self._create_run({"scale": "303", "industry": "1001"}, "Python", jobs)
        run_id = resp.get_json()["run_id"]
        mismatches = self.client.get(f"/api/screening/runs/{run_id}/mismatches").get_json()
        for item in mismatches["items"]:
            self.assertNotIn("reason", item)
            self.assertNotIn("excluded_by", item)
            self.assertNotIn("excluded_field", item)
            self.assertNotIn("rule", item)

    def test_mismatches_does_not_distinguish_hard_vs_ai(self):
        # 接口契约：不区分硬规则或 AI 排除，混在一起
        jobs = [self._job("job-A", company_scale="20-99人")]
        resp = self._create_run({"scale": "303"}, "Python", jobs)
        run_id = resp.get_json()["run_id"]
        mismatches = self.client.get(f"/api/screening/runs/{run_id}/mismatches").get_json()
        for item in mismatches["items"]:
            self.assertNotIn("source", item)
            self.assertNotIn("verdict_source", item)

    def test_mismatches_item_fields_match_contract(self):
        jobs = [self._job("job-A", company_scale="20-99人")]
        resp = self._create_run({"scale": "303"}, "Python", jobs)
        run_id = resp.get_json()["run_id"]
        mismatches = self.client.get(f"/api/screening/runs/{run_id}/mismatches").get_json()
        item = mismatches["items"][0]
        expected = {"job_id", "title", "company", "salary", "location",
                    "jd_excerpt", "canonical_url", "interest_state"}
        self.assertEqual(set(item.keys()), expected)

    def test_zone_items_include_jd_excerpt_from_artifact(self):
        jobs = [self._job("job-A", jd="负责 Python FastAPI 后端开发和 AI 应用落地")]
        resp = self._create_run({}, "Python", jobs)
        run_id = resp.get_json()["run_id"]
        matches = self.client.get(f"/api/screening/runs/{run_id}/matches").get_json()
        self.assertIn("Python FastAPI", matches["items"][0]["jd_excerpt"])

    # -- 排序：matches 按抓回顺序 --

    def test_matches_in_scrape_order(self):
        # 5 条 job 混合分流，符合区按抓回顺序 [A, C, D]
        jobs = [
            self._job("job-A"),
            self._job("job-B", company_scale="20-99人"),
            self._job("job-C"),
            self._job("job-D"),
            self._job("job-E", company_scale="20-99人"),
        ]
        resp = self._create_run({"scale": "303"}, "Python", jobs)
        run_id = resp.get_json()["run_id"]
        matches = self.client.get(f"/api/screening/runs/{run_id}/matches").get_json()
        self.assertEqual(self._job_ids(matches["items"]), ["job-A", "job-C", "job-D"])

    def test_matches_not_sorted_by_title(self):
        # 故意让 job_id 与 title 顺序不一致，验证不按 title 排序
        jobs = [
            self._job("job-1", title="Zebra"),
            self._job("job-2", title="Alpha"),
            self._job("job-3", title="Mike"),
        ]
        resp = self._create_run({}, "Python", jobs)
        run_id = resp.get_json()["run_id"]
        matches = self.client.get(f"/api/screening/runs/{run_id}/matches").get_json()
        # 按 job_id（抓回顺序），不是按 title 字母序
        self.assertEqual(self._job_ids(matches["items"]), ["job-1", "job-2", "job-3"])

    # -- GET /api/screening/runs/{run_id} 计数 --

    def test_get_run_returns_correct_counts(self):
        jobs = [
            self._job("job-A"),
            self._job("job-B", company_scale="20-99人"),
            self._job("job-C"),
        ]
        resp = self._create_run({"scale": "303"}, "Python", jobs)
        run_id = resp.get_json()["run_id"]
        run_resp = self.client.get(f"/api/screening/runs/{run_id}")
        self.assertEqual(run_resp.status_code, 200)
        body = run_resp.get_json()
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["source_count"], 3)
        self.assertEqual(body["match_count"], 2)
        self.assertEqual(body["mismatch_count"], 1)

    # -- run 不存在 404 --

    def test_matches_unknown_run_returns_404(self):
        resp = self.client.get("/api/screening/runs/nonexistent-run/matches")
        self.assertEqual(resp.status_code, 404)
        body = resp.get_json()
        self.assertEqual(body["error_code"], "not_found")

    def test_mismatches_unknown_run_returns_404(self):
        resp = self.client.get("/api/screening/runs/nonexistent-run/mismatches")
        self.assertEqual(resp.status_code, 404)

    def test_get_run_unknown_returns_404(self):
        resp = self.client.get("/api/screening/runs/nonexistent-run")
        self.assertEqual(resp.status_code, 404)

    # -- token 保护 --

    def test_matches_rejected_without_token(self):
        resp = self.bare_client.get("/api/screening/runs/some-run/matches")
        self.assertEqual(resp.status_code, 403)

    def test_mismatches_rejected_without_token(self):
        resp = self.bare_client.get("/api/screening/runs/some-run/mismatches")
        self.assertEqual(resp.status_code, 403)

    def test_get_run_rejected_without_token(self):
        resp = self.bare_client.get("/api/screening/runs/some-run")
        self.assertEqual(resp.status_code, 403)

    # -- 空产物：run 成功但无 jobs --

    def test_empty_jobs_returns_empty_zones(self):
        resp = self._create_run({}, "Python", [])
        run_id = resp.get_json()["run_id"]
        matches = self.client.get(f"/api/screening/runs/{run_id}/matches").get_json()
        mismatches = self.client.get(f"/api/screening/runs/{run_id}/mismatches").get_json()
        self.assertEqual(matches["items"], [])
        self.assertEqual(mismatches["items"], [])
        self.assertEqual(matches["count"], 0)


class ScreeningFeedbackAPITests(unittest.TestCase):
    """T043: US4 集成测试 — 反馈、展示排除、持久保留与链接安全。

    覆盖场景：
    1. POST /interest 与 /reject 返回正确状态
    2. GET /interested 与 /trash 持久返回岗位列表
    3. matches/mismatches 传入 profile_id 时排除垃圾桶岗位
    4. matches 的 interest_state 正确反映 interested/rejected
    5. 不安全链接被拒绝
    6. 感兴趣/垃圾桶跨 run 持久保留
    7. 跨画像隔离
    8. 令牌保护
    9. 错误处理（缺参数、未知画像/岗位）
    10. interested ↔ rejected 状态切换
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
        session = self.client.get("/api/session")
        self.token = session.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        self.bare_client = self.app.test_client()
        self.store = self.app.config["TASK_STORE"]
        self.profile = self.store.create_profile("US4 测试画像")

    def tearDown(self):
        self.temp.cleanup()

    def _fake_execute_with_jobs(self, jobs):
        def _impl(filters, keyword, *, output_path, python_executable, store=None, run_id=None,
                  **_execution_outputs):
            output_path = pathlib.Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False), encoding="utf-8")
            if store and run_id:
                store.update_screening_run_status(run_id, "running")
                store.update_screening_run_status(run_id, "succeeded", source_count=len(jobs))
            return {"jobs": jobs, "source_count": len(jobs), "status": "succeeded"}
        return _impl

    def _create_run(self, filters, keyword, jobs):
        with mock.patch("webui.app.execute_first_layer",
                        side_effect=self._fake_execute_with_jobs(jobs)):
            return self.client.post("/api/screening/runs", json={
                "filters": filters,
                "keyword": keyword,
            })

    def _job(self, job_id, **overrides):
        from tests.test_screening_fixtures import sample_screening_job
        job = sample_screening_job(job_id=job_id)
        job.update(overrides)
        return job

    def _job_ids(self, items):
        return [item["job_id"] for item in items]

    def _pid(self):
        return self.profile["id"]

    # -- POST /interest 与 /reject 返回正确状态 --

    def test_mark_interest_returns_200_with_interested_state(self):
        resp = self._create_run({}, "Python", [self._job("job-A")])
        run_id = resp.get_json()["run_id"]
        resp = self.client.post(f"/api/screening/jobs/job-A/interest", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["interest_state"], "interested")
        self.assertIn("job_id", body)

    def test_mark_reject_returns_200_with_rejected_state(self):
        resp = self._create_run({}, "Python", [self._job("job-A")])
        run_id = resp.get_json()["run_id"]
        resp = self.client.post(f"/api/screening/jobs/job-A/reject", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["reject_state"], "rejected")

    # -- GET /interested 与 /trash 持久返回岗位列表 --

    def test_interested_list_returns_marked_job(self):
        resp = self._create_run({}, "Python", [self._job("job-A")])
        run_id = resp.get_json()["run_id"]
        self.client.post(f"/api/screening/jobs/job-A/interest", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        interested = self.client.get(
            f"/api/screening/interested?profile_id={self._pid()}"
        ).get_json()
        self.assertEqual(interested["count"], 1)
        item = interested["items"][0]
        self.assertEqual(item["title"], "Python 后端工程师")
        self.assertEqual(item["interest_state"], "interested")
        self.assertTrue(item["canonical_url"].startswith("https://"))

    def test_trash_list_returns_marked_job(self):
        resp = self._create_run({}, "Python", [self._job("job-A")])
        run_id = resp.get_json()["run_id"]
        self.client.post(f"/api/screening/jobs/job-A/reject", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        trash = self.client.get(
            f"/api/screening/trash?profile_id={self._pid()}"
        ).get_json()
        self.assertEqual(trash["count"], 1)
        item = trash["items"][0]
        self.assertEqual(item["reject_state"], "rejected")

    def test_interested_list_empty_without_any_mark(self):
        interested = self.client.get(
            f"/api/screening/interested?profile_id={self._pid()}"
        ).get_json()
        self.assertEqual(interested["count"], 0)

    def test_trash_list_empty_without_any_mark(self):
        trash = self.client.get(
            f"/api/screening/trash?profile_id={self._pid()}"
        ).get_json()
        self.assertEqual(trash["count"], 0)

    # -- matches/mismatches 传入 profile_id 时排除垃圾桶岗位 --

    def test_rejected_job_excluded_from_matches_with_profile_id(self):
        resp = self._create_run({}, "Python",
                                [self._job("job-A"), self._job("job-B")])
        run_id = resp.get_json()["run_id"]
        self.client.post(f"/api/screening/jobs/job-A/reject", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        matches = self.client.get(
            f"/api/screening/runs/{run_id}/matches?profile_id={self._pid()}"
        ).get_json()
        self.assertEqual(self._job_ids(matches["items"]), ["job-B"])

    def test_rejected_job_excluded_from_mismatches_with_profile_id(self):
        resp = self._create_run({"scale": "303"}, "Python", [
            self._job("job-A", company_scale="20-99人"),
            self._job("job-B", company_scale="20-99人"),
        ])
        run_id = resp.get_json()["run_id"]
        self.client.post(f"/api/screening/jobs/job-A/reject", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        mismatches = self.client.get(
            f"/api/screening/runs/{run_id}/mismatches?profile_id={self._pid()}"
        ).get_json()
        self.assertEqual(self._job_ids(mismatches["items"]), ["job-B"])

    def test_matches_without_profile_id_does_not_exclude(self):
        """不传 profile_id 时不排除（向后兼容 US3 行为）。"""
        resp = self._create_run({}, "Python",
                                [self._job("job-A"), self._job("job-B")])
        run_id = resp.get_json()["run_id"]
        self.client.post(f"/api/screening/jobs/job-A/reject", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        matches = self.client.get(f"/api/screening/runs/{run_id}/matches").get_json()
        self.assertEqual(self._job_ids(matches["items"]), ["job-A", "job-B"])

    # -- interest_state 在 matches 中正确反映 --

    def test_interest_state_reflects_interested_in_matches(self):
        resp = self._create_run({}, "Python",
                                [self._job("job-A"), self._job("job-B")])
        run_id = resp.get_json()["run_id"]
        self.client.post(f"/api/screening/jobs/job-A/interest", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        matches = self.client.get(
            f"/api/screening/runs/{run_id}/matches?profile_id={self._pid()}"
        ).get_json()
        states = {item["job_id"]: item["interest_state"] for item in matches["items"]}
        self.assertEqual(states["job-A"], "interested")
        self.assertEqual(states["job-B"], "none")

    def test_interest_state_reflects_rejected_in_mismatches(self):
        # job-A 被标记 rejected，但它被排除，所以 mismatches 里看不到它
        # job-B 未标记，interest_state 为 none
        resp = self._create_run({"scale": "303"}, "Python", [
            self._job("job-A", company_scale="20-99人"),
            self._job("job-B", company_scale="20-99人"),
        ])
        run_id = resp.get_json()["run_id"]
        self.client.post(f"/api/screening/jobs/job-A/reject", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        mismatches = self.client.get(
            f"/api/screening/runs/{run_id}/mismatches?profile_id={self._pid()}"
        ).get_json()
        states = {item["job_id"]: item["interest_state"] for item in mismatches["items"]}
        self.assertNotIn("job-A", states)
        self.assertEqual(states["job-B"], "none")

    # -- 不安全链接被拒绝 --

    def test_mark_interest_with_unsafe_link_returns_400(self):
        resp = self._create_run({}, "Python",
                                [self._job("job-A", job_link="http://evil.com/job")])
        run_id = resp.get_json()["run_id"]
        resp = self.client.post(f"/api/screening/jobs/job-A/interest", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error_code"], "invalid_link")

    def test_mark_reject_with_unsafe_link_returns_400(self):
        resp = self._create_run({}, "Python",
                                [self._job("job-A", job_link="ftp://bad.com/x")])
        run_id = resp.get_json()["run_id"]
        resp = self.client.post(f"/api/screening/jobs/job-A/reject", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error_code"], "invalid_link")

    def test_interested_list_canonical_url_normalized(self):
        # job_link 带查询参数，interested 列表返回规范化后的 URL（无 query）
        resp = self._create_run({}, "Python", [
            self._job("job-A", job_link="https://www.zhipin.com/job_detail/job-A.html?source=x"),
        ])
        run_id = resp.get_json()["run_id"]
        self.client.post(f"/api/screening/jobs/job-A/interest", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        interested = self.client.get(
            f"/api/screening/interested?profile_id={self._pid()}"
        ).get_json()
        self.assertEqual(
            interested["items"][0]["canonical_url"],
            "https://www.zhipin.com/job_detail/job-A.html",
        )

    # -- 感兴趣/垃圾桶跨 run 持久保留 --

    def test_interested_survives_new_run(self):
        resp1 = self._create_run({}, "Python", [self._job("job-A")])
        run1_id = resp1.get_json()["run_id"]
        self.client.post(f"/api/screening/jobs/job-A/interest", json={
            "profile_id": self._pid(), "run_id": run1_id,
        })
        # 新 run 不影响持久感兴趣区
        self._create_run({}, "Python", [self._job("job-B")])
        interested = self.client.get(
            f"/api/screening/interested?profile_id={self._pid()}"
        ).get_json()
        self.assertEqual(interested["count"], 1)
        self.assertEqual(interested["items"][0]["title"], "Python 后端工程师")

    def test_trash_survives_new_run(self):
        resp1 = self._create_run({}, "Python", [self._job("job-A")])
        run1_id = resp1.get_json()["run_id"]
        self.client.post(f"/api/screening/jobs/job-A/reject", json={
            "profile_id": self._pid(), "run_id": run1_id,
        })
        self._create_run({}, "Python", [self._job("job-B")])
        trash = self.client.get(
            f"/api/screening/trash?profile_id={self._pid()}"
        ).get_json()
        self.assertEqual(trash["count"], 1)

    def test_rejected_job_excluded_in_new_run_matches(self):
        # run1 标记 job-A 为不感兴趣
        resp1 = self._create_run({}, "Python", [self._job("job-A")])
        run1_id = resp1.get_json()["run_id"]
        self.client.post(f"/api/screening/jobs/job-A/reject", json={
            "profile_id": self._pid(), "run_id": run1_id,
        })
        # run2 也包含 job-A，传入 profile_id 应排除
        resp2 = self._create_run({}, "Python", [self._job("job-A"), self._job("job-B")])
        run2_id = resp2.get_json()["run_id"]
        matches2 = self.client.get(
            f"/api/screening/runs/{run2_id}/matches?profile_id={self._pid()}"
        ).get_json()
        self.assertNotIn("job-A", self._job_ids(matches2["items"]))
        self.assertIn("job-B", self._job_ids(matches2["items"]))

    # -- 跨画像隔离 --

    def test_cross_profile_isolation_interested(self):
        p2 = self.store.create_profile("画像 B")
        resp = self._create_run({}, "Python", [self._job("job-A")])
        run_id = resp.get_json()["run_id"]
        self.client.post(f"/api/screening/jobs/job-A/interest", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        interested_p1 = self.client.get(
            f"/api/screening/interested?profile_id={self._pid()}"
        ).get_json()
        interested_p2 = self.client.get(
            f"/api/screening/interested?profile_id={p2['id']}"
        ).get_json()
        self.assertEqual(interested_p1["count"], 1)
        self.assertEqual(interested_p2["count"], 0)

    def test_cross_profile_isolation_trash(self):
        p2 = self.store.create_profile("画像 B")
        resp = self._create_run({}, "Python", [self._job("job-A")])
        run_id = resp.get_json()["run_id"]
        self.client.post(f"/api/screening/jobs/job-A/reject", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        trash_p1 = self.client.get(
            f"/api/screening/trash?profile_id={self._pid()}"
        ).get_json()
        trash_p2 = self.client.get(
            f"/api/screening/trash?profile_id={p2['id']}"
        ).get_json()
        self.assertEqual(trash_p1["count"], 1)
        self.assertEqual(trash_p2["count"], 0)

    # -- 令牌保护 --

    def test_mark_interest_rejected_without_token(self):
        resp = self.bare_client.post("/api/screening/jobs/job-A/interest", json={
            "profile_id": "x", "run_id": "y",
        })
        self.assertEqual(resp.status_code, 403)

    def test_mark_reject_rejected_without_token(self):
        resp = self.bare_client.post("/api/screening/jobs/job-A/reject", json={
            "profile_id": "x", "run_id": "y",
        })
        self.assertEqual(resp.status_code, 403)

    def test_interested_list_rejected_without_token(self):
        resp = self.bare_client.get("/api/screening/interested?profile_id=x")
        self.assertEqual(resp.status_code, 403)

    def test_trash_list_rejected_without_token(self):
        resp = self.bare_client.get("/api/screening/trash?profile_id=x")
        self.assertEqual(resp.status_code, 403)

    # -- 错误处理 --

    def test_mark_interest_missing_profile_id_returns_400(self):
        resp = self._create_run({}, "Python", [self._job("job-A")])
        run_id = resp.get_json()["run_id"]
        resp = self.client.post(f"/api/screening/jobs/job-A/interest", json={
            "run_id": run_id,
        })
        self.assertEqual(resp.status_code, 400)

    def test_mark_interest_missing_run_id_returns_400(self):
        resp = self._create_run({}, "Python", [self._job("job-A")])
        run_id = resp.get_json()["run_id"]
        resp = self.client.post(f"/api/screening/jobs/job-A/interest", json={
            "profile_id": self._pid(),
        })
        self.assertEqual(resp.status_code, 400)

    def test_mark_interest_unknown_profile_returns_404(self):
        resp = self._create_run({}, "Python", [self._job("job-A")])
        run_id = resp.get_json()["run_id"]
        resp = self.client.post(f"/api/screening/jobs/job-A/interest", json={
            "profile_id": "nonexistent-profile", "run_id": run_id,
        })
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error_code"], "not_found")

    def test_mark_interest_unknown_job_returns_404(self):
        resp = self._create_run({}, "Python", [self._job("job-A")])
        run_id = resp.get_json()["run_id"]
        resp = self.client.post(f"/api/screening/jobs/job-X/interest", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error_code"], "not_found")

    def test_interested_list_missing_profile_id_returns_400(self):
        resp = self.client.get("/api/screening/interested")
        self.assertEqual(resp.status_code, 400)

    def test_trash_list_missing_profile_id_returns_400(self):
        resp = self.client.get("/api/screening/trash")
        self.assertEqual(resp.status_code, 400)

    def test_interested_list_unknown_profile_returns_404(self):
        resp = self.client.get("/api/screening/interested?profile_id=nonexistent")
        self.assertEqual(resp.status_code, 404)

    # -- interested ↔ rejected 状态切换 --

    def test_interest_then_reject_moves_to_trash(self):
        resp = self._create_run({}, "Python", [self._job("job-A")])
        run_id = resp.get_json()["run_id"]
        self.client.post(f"/api/screening/jobs/job-A/interest", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        self.client.post(f"/api/screening/jobs/job-A/reject", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        interested = self.client.get(
            f"/api/screening/interested?profile_id={self._pid()}"
        ).get_json()
        trash = self.client.get(
            f"/api/screening/trash?profile_id={self._pid()}"
        ).get_json()
        self.assertEqual(interested["count"], 0)
        self.assertEqual(trash["count"], 1)

    def test_reject_then_interest_moves_to_interested(self):
        resp = self._create_run({}, "Python", [self._job("job-A")])
        run_id = resp.get_json()["run_id"]
        self.client.post(f"/api/screening/jobs/job-A/reject", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        self.client.post(f"/api/screening/jobs/job-A/interest", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        interested = self.client.get(
            f"/api/screening/interested?profile_id={self._pid()}"
        ).get_json()
        trash = self.client.get(
            f"/api/screening/trash?profile_id={self._pid()}"
        ).get_json()
        self.assertEqual(interested["count"], 1)
        self.assertEqual(trash["count"], 0)

    # -- 符合区与不符合区都可标记 --

    def test_mismatch_job_can_be_marked_interest(self):
        resp = self._create_run({"scale": "303"}, "Python",
                                [self._job("job-A", company_scale="20-99人")])
        run_id = resp.get_json()["run_id"]
        resp = self.client.post(f"/api/screening/jobs/job-A/interest", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        self.assertEqual(resp.status_code, 200)
        interested = self.client.get(
            f"/api/screening/interested?profile_id={self._pid()}"
        ).get_json()
        self.assertEqual(interested["count"], 1)

    def test_match_job_can_be_marked_reject(self):
        resp = self._create_run({}, "Python", [self._job("job-A")])
        run_id = resp.get_json()["run_id"]
        resp = self.client.post(f"/api/screening/jobs/job-A/reject", json={
            "profile_id": self._pid(), "run_id": run_id,
        })
        self.assertEqual(resp.status_code, 200)
        trash = self.client.get(
            f"/api/screening/trash?profile_id={self._pid()}"
        ).get_json()
        self.assertEqual(trash["count"], 1)


class ScreeningDegradationIntegrationTests(unittest.TestCase):
    """T050: AI 不可用降级端到端集成测试（FR-031 ~ FR-034）。

    覆盖场景：
    1. AI 不可用时 POST /api/screening/runs 接受纯人工填筛并成功执行
    2. AI 不可用时第二层不调 assess_semantic_similarity（FR-034）
    3. 上传简历可跳过：不传 resume_id 也能创建运行（FR-033）
    4. AI 可用时第二层调用 assess_semantic_similarity（对照）
    5. suggest 在 credential_ref 为空时返回 ai_unavailable
    6. suggest 在 api_key 缺失（keyring 返回 None）时返回 ai_unavailable
    7. AI 不可用时运行按硬规则正确分流
    8. 降级运行仍需令牌保护
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
        session = self.client.get("/api/session")
        self.token = session.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        self.bare_client = self.app.test_client()
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        self.temp.cleanup()

    def _fake_execute_with_jobs(self, jobs):
        """Return side_effect that writes jobs to artifact and advances status."""
        def _impl(filters, keyword, *, output_path, python_executable, store=None, run_id=None,
                  **_execution_limits):
            output_path = pathlib.Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False), encoding="utf-8")
            if store and run_id:
                store.update_screening_run_status(run_id, "running")
                store.update_screening_run_status(run_id, "succeeded", source_count=len(jobs))
            return {"jobs": jobs, "source_count": len(jobs), "status": "succeeded"}
        return _impl

    def _job(self, job_id, **overrides):
        from tests.test_screening_fixtures import sample_screening_job
        job = sample_screening_job(job_id=job_id)
        job.update(overrides)
        return job

    def _create_run(self, filters, keyword, jobs):
        """Helper: mock execute + POST /api/screening/runs, return response."""
        with mock.patch("webui.app.execute_first_layer",
                        side_effect=self._fake_execute_with_jobs(jobs)):
            return self.client.post("/api/screening/runs", json={
                "filters": filters,
                "keyword": keyword,
            })

    def _configure_ai(self, credential_ref="api.example.com", model=""):
        self.store.save_ai_settings(
            "https://api.example.com/v1/chat/completions", credential_ref, model=model,
        )

    def _upload_resume(self, content=None):
        from io import BytesIO
        if content is None:
            content = "Python 后端 5 年经验 FastAPI".encode("utf-8")
        resp = self.client.post(
            "/api/screening/resume",
            data={"file": (BytesIO(content), "resume.txt")},
            content_type="multipart/form-data",
        )
        return resp.get_json()["resume_id"]

    # -- AI 不可用：人工填筛 + 仅硬规则 --

    def test_run_succeeds_without_ai_manual_filters(self):
        # AI 未配置，纯人工填写筛选条件 → 运行成功（FR-032）
        jobs = [self._job("job-1"), self._job("job-2")]
        resp = self._create_run({"city": "上海"}, "Python", jobs)
        self.assertEqual(resp.status_code, 201)
        body = resp.get_json()
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["source_count"], 2)

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_degraded_run_does_not_call_semantic_similarity(self, mock_ai):
        # FR-034: AI 不可用时第二层不调语义相似度
        jobs = [self._job("job-1"), self._job("job-2")]
        self._create_run({"city": "上海"}, "Python", jobs)
        mock_ai.assert_not_called()

    def test_run_without_resume_succeeds(self):
        # FR-033: 上传简历可跳过，不传 resume_id 也能创建运行
        jobs = [self._job("job-1")]
        resp = self._create_run({}, "Python", jobs)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.get_json()["status"], "succeeded")

    def test_run_forwards_explicit_execution_limits_to_scraper(self):
        """用户选择的小范围抓取参数必须原样传给第一层编排。"""
        jobs = [self._job("job-1")]
        with mock.patch(
            "webui.app.execute_first_layer",
            side_effect=self._fake_execute_with_jobs(jobs),
        ) as execute:
            response = self.client.post("/api/screening/runs", json={
                "filters": {}, "keyword": "Python",
                "pages": 1, "max_details": 3,
            })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(execute.call_args.kwargs.get("pages"), 1)
        self.assertEqual(execute.call_args.kwargs.get("max_details"), 3)

    def test_run_forwards_run_scoped_detail_output_to_scraper(self):
        """正式筛选必须声明本次 run 的详情产物，不能读取默认时间戳文件。"""
        captured = {}

        def capture(_filters, _keyword, **kwargs):
            captured.update(kwargs)
            kwargs["store"].update_screening_run_status(kwargs["run_id"], "running")
            return {"jobs": [], "source_count": 0, "status": "running"}

        with mock.patch("webui.app.execute_first_layer", side_effect=capture):
            response = self.client.post("/api/screening/runs", json={
                "filters": {}, "keyword": "Python",
                "pages": 1, "max_details": 3,
            })

        body = response.get_json()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            pathlib.Path(captured["detail_output_path"]).name,
            f"screening_{body['run_id']}_details.json",
        )

    def test_run_rejects_invalid_execution_limits_before_scraping(self):
        """范围控制只能是正整数，非法值不得触发抓取。"""
        with mock.patch("webui.app.execute_first_layer") as execute:
            response = self.client.post("/api/screening/runs", json={
                "filters": {}, "keyword": "Python", "pages": 0,
            })
        self.assertEqual(response.status_code, 400)
        execute.assert_not_called()

    @mock.patch("webui.screening.assess_semantic_similarity")
    def test_run_with_ai_calls_semantic_similarity(self, mock_ai):
        # 对照：AI 可用且提供简历/JD 时第二层调用语义相似度
        self._configure_ai(model="deepseek-v4-flash-free")
        with mock.patch("webui.ai.keyring.get_password", return_value="secret-key"):
            resume_id = self._upload_resume()
            jobs = [self._job("job-1", jd="Python FastAPI"),
                    self._job("job-2", jd="Python backend")]
            with mock.patch("webui.app.execute_first_layer",
                            side_effect=self._fake_execute_with_jobs(jobs)):
                self.client.post("/api/screening/runs", json={
                    "filters": {"city": "上海"},
                    "keyword": "Python",
                    "resume_id": resume_id,
                })
        self.assertEqual(mock_ai.call_count, 2)
        self.assertEqual(
            mock_ai.call_args.kwargs["model"], "deepseek-v4-flash-free",
        )

    # -- suggest 降级边界 --

    def test_suggest_unavailable_when_credential_ref_empty(self):
        # endpoint 已配但 credential_ref 为空 → is_configured=False → ai_unavailable
        self._configure_ai(credential_ref="")
        resume_id = self._upload_resume()
        resp = self.client.post(
            "/api/screening/resume/suggest",
            json={"resume_id": resume_id},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "ai_unavailable")

    def test_suggest_unavailable_when_api_key_missing_from_keyring(self):
        # endpoint + credential_ref 都配了，但 keyring 里没存 key → ai_unavailable
        self._configure_ai(credential_ref="api.example.com")
        with mock.patch("webui.ai.keyring.get_password", return_value=None):
            resume_id = self._upload_resume()
            resp = self.client.post(
                "/api/screening/resume/suggest",
                json={"resume_id": resume_id},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "ai_unavailable")

    # -- 降级运行按硬规则正确分流 --

    def test_degraded_run_hard_rules_partition_correct(self):
        # AI 不可用，混合 jobs 按硬规则分流
        jobs = [
            self._job("match-1"),                              # 硬规则过
            self._job("mismatch-1", company_scale="20-99人"),  # 硬规则不过
            self._job("match-2"),                              # 硬规则过
        ]
        resp = self._create_run({"scale": "303"}, "Python", jobs)
        self.assertEqual(resp.status_code, 201)
        body = resp.get_json()
        self.assertEqual(body["match_count"], 2)
        self.assertEqual(body["mismatch_count"], 1)

        run_id = body["run_id"]
        matches = self.client.get(f"/api/screening/runs/{run_id}/matches").get_json()
        mismatches = self.client.get(f"/api/screening/runs/{run_id}/mismatches").get_json()
        self.assertEqual(matches["count"], 2)
        self.assertEqual(mismatches["count"], 1)

    # -- 降级运行仍需令牌保护 --

    def test_degraded_run_requires_token(self):
        resp = self.bare_client.post("/api/screening/runs", json={
            "filters": {"city": "上海"},
            "keyword": "Python",
        })
        self.assertEqual(resp.status_code, 403)


class ScreeningDOMContractTests(unittest.TestCase):
    """T052: 筛选页 DOM 契约（筛选栏、执行按钮、两区切换、卡片、反馈按钮、感兴趣区与垃圾桶区入口）。

    GET / 返回的 HTML 必须包含 US6 所需 DOM 元素（以 data-screening / data-zone /
    data-filter / data-feedback 属性标识），供 JS 挂载交互。本类只测 DOM 契约存在性，
    不测交互行为（交互在 T053 浏览器测试覆盖）。
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
        session = self.client.get("/api/session")
        self.token = session.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.html = resp.get_data(as_text=True)

    def tearDown(self):
        self.temp.cleanup()

    # -- 筛选栏 --

    def test_filter_bar_container_present(self):
        self.assertIn('data-screening="filters"', self.html)

    def test_seven_filter_selects_present(self):
        for field in ("city", "salary", "experience", "degree", "scale", "stage", "industry"):
            with self.subTest(field=field):
                self.assertIn(f'data-filter="{field}"', self.html)

    # -- 简历上传 --

    def test_resume_upload_input_present(self):
        self.assertIn('data-screening="resume-upload"', self.html)

    def test_resume_upload_button_has_loading_feedback(self):
        self.assertIn('id="resumeUploadButton"', self.html)
        self.assertIn('aria-busy="false"', self.html)
        self.assertIn('class="btn-label">上传解析</span>', self.html)
        self.assertIn('class="btn-spinner" aria-hidden="true"', self.html)
        self.assertIn("@keyframes btnSpin", self.html)
        self.assertIn("function setResumeUploadLoading(isLoading)", self.html)
        self.assertIn("button.disabled = isLoading", self.html)
        self.assertIn('button.setAttribute("aria-busy", String(isLoading))', self.html)

        upload_source = self.html.split("async function uploadResume()", 1)[1].split(
            "function showAiSuggestion", 1
        )[0]
        self.assertIn("if (uploadButton && uploadButton.disabled) return;", upload_source)
        self.assertIn("setResumeUploadLoading(true);", upload_source)
        self.assertIn("finally", upload_source)
        self.assertIn("setResumeUploadLoading(false);", upload_source)

    def test_suggest_button_present(self):
        self.assertIn('data-screening="suggest-btn"', self.html)

    def test_resume_consent_tooltip_releases_mouse_focus(self):
        self.assertIn(
            ".consent-wrap:hover .consent-tooltip,\n"
            "    .consent-wrap:focus-within .consent-tooltip { opacity: 1; visibility: visible; }",
            self.html,
        )
        self.assertNotIn(
            ".consent-wrap:has(input:checked) .consent-tooltip",
            self.html,
        )
        self.assertIn("function initResumeConsentTooltip()", self.html)
        self.assertIn("consentWrap.addEventListener(\"pointerdown\"", self.html)
        self.assertIn("consentInput.blur()", self.html)
        self.assertIn("setTimeout(() => consentInput.blur(), 0)", self.html)
        self.assertIn("initResumeConsentTooltip();", self.html)

    # -- 执行按钮与关键词 --

    def test_keyword_input_present(self):
        self.assertIn('data-screening="keyword"', self.html)

    def test_execute_button_present(self):
        self.assertIn('data-screening="execute-btn"', self.html)

    # -- 运行状态 --

    def test_run_status_container_present(self):
        self.assertIn('data-screening="status"', self.html)

    # -- 两区切换 --

    def test_match_zone_tab_present(self):
        self.assertIn('data-zone-tab="match"', self.html)

    def test_mismatch_zone_tab_present(self):
        self.assertIn('data-zone-tab="mismatch"', self.html)

    def test_match_zone_container_present(self):
        self.assertIn('data-zone="match"', self.html)

    def test_mismatch_zone_container_present(self):
        self.assertIn('data-zone="mismatch"', self.html)

    # -- 岗位卡片与反馈按钮 --

    def test_job_card_template_present(self):
        self.assertIn('data-screening="job-card"', self.html)

    def test_interest_button_present(self):
        self.assertIn('data-feedback="interest"', self.html)

    def test_reject_button_present(self):
        self.assertIn('data-feedback="reject"', self.html)

    # -- 感兴趣区与垃圾桶区入口 --

    def test_interested_zone_entry_present(self):
        self.assertIn('data-screening="interested-entry"', self.html)

    def test_trash_zone_entry_present(self):
        self.assertIn('data-screening="trash-entry"', self.html)

    # -- 降级态 --

    def test_ai_unavailable_prompt_present(self):
        self.assertIn('data-screening="ai-unavailable"', self.html)

    def test_skip_resume_option_present(self):
        self.assertIn('data-screening="skip-resume"', self.html)


if __name__ == "__main__":
    unittest.main()
