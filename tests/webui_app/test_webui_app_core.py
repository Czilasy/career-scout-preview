"""webui.app 核心路由与前端合同测试（027 自 tests/test_webui_app.py 拆出）。"""
import json
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock
from webui.app import create_app


class ThemeApiTests(unittest.TestCase):
    """主题偏好持久化（/api/theme）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.theme_path = root / "theme.json"
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
        self._patcher = mock.patch.object(
            self.app.config["PIPELINE_CONTEXT"], "theme_path",
            return_value=self.theme_path)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.temp.cleanup()

    def test_theme_defaults_to_light(self):
        data = self.client.get("/api/theme").get_json()
        self.assertEqual(data, {"ok": True, "mode": "light"})

    def test_theme_persists_put_then_get(self):
        resp = self.client.put("/api/theme", json={"mode": "dark"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["mode"], "dark")
        data = self.client.get("/api/theme").get_json()
        self.assertEqual(data["mode"], "dark")
        self.assertTrue(self.theme_path.exists())

    def test_theme_rejects_invalid_mode(self):
        resp = self.client.put("/api/theme", json={"mode": "neon"})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(self.theme_path.exists())

    def test_theme_accepts_kaleido_easter_egg_mode(self):
        resp = self.client.put("/api/theme", json={"mode": "kaleido"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["mode"], "kaleido")
        data = self.client.get("/api/theme").get_json()
        self.assertEqual(data["mode"], "kaleido")
        self.assertTrue(self.theme_path.exists())


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
            "RUNTIME_MODE": "exe",
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

    def test_sensitive_get_routes_require_session_token(self):
        anonymous = self.app.test_client()
        anonymous.environ_base.pop("HTTP_X_BOSS_TOKEN", None)
        for path in (
            "/api/favorites",
            "/api/profile",
            "/api/profiles",
            "/api/latest-pipeline-result",
            "/api/search-progress/missing",
            "/api/update-status",
            "/api/check",
            "/api/env-check",
            "/api/job-reminders/count",
            "/api/job-reminders",
            "/api/result-history",
        ):
            with self.subTest(path=path):
                self.assertEqual(anonymous.get(path).status_code, 403)

    def test_update_status_does_not_expose_download_path(self):
        response = self.client.get("/api/update-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json().get("path"), "")

    def test_check_uses_returncode_not_log_keywords(self):
        completed = type("Completed", (), {
            "returncode": 1,
            "output_tail": "CDP 9222 检查失败，未登录",
            "failure_code": "process_failed",
            "ok": False,
        })()
        with mock.patch("webui.process_executor.ScraperExecutor.execute", return_value=completed):
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

    def test_export_csv_groups_matched_before_unmatched_with_labels(self):
        task = self._create_scrape_task()
        output_path = pathlib.Path(task["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({
            "keyword": "Python 后端",
            "city": "上海",
            "jobs": [
                {
                    "job_id": "bad",
                    "title": "Java 工程师",
                    "boss_name": "某公司",
                    "salary": "10-15K",
                    "location": "上海",
                    "skills": "Java",
                },
                {
                    "job_id": "good",
                    "title": "Python 后端工程师",
                    "boss_name": "产品公司",
                    "salary": "25-35K",
                    "location": "上海·浦东新区",
                    "skills": "Python,FastAPI",
                },
            ],
        }, ensure_ascii=False), encoding="utf-8")

        response = self.client.get(f"/api/tasks/{task['id']}/export.csv")

        self.assertEqual(response.status_code, 200)
        lines = [
            line for line in response.get_data(as_text=True).splitlines()
            if line.strip()
        ]
        first_cells = [line.lstrip("\ufeff").split(",")[0] for line in lines]
        self.assertEqual(first_cells, ["job_id", "匹配：", "good", "不匹配：", "bad"])

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
            {"error_code": "not_found", "user_message": "任务不存在或已被移除"},
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
        obsolete_page = pathlib.Path(__file__).parents[2] / "webui" / "index-v2.html"
        self.assertFalse(obsolete_page.exists())


class UpdateRestartFailureTests(unittest.TestCase):
    def test_launch_failure_returns_chinese_message_and_hides_exception(self):
        from webui import updater as updater_mod
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            app = create_app({
                "TESTING": True,
                "START_TASKS": False,
                "RUNTIME_MODE": "exe",
                "RESULT_DIR": str(root / "results"),
                "DB_PATH": str(root / "state" / "webui.db"),
                "PYTHON_EXECUTABLE": sys.executable,
            })
            client = app.test_client()
            session = client.get("/api/session")
            client.environ_base["HTTP_X_BOSS_TOKEN"] = session.get_json()["token"]
            installer = root / "CareerScout-v2.8.5.exe"
            installer.write_bytes(b"payload")
            app.config["UPDATER"].state = updater_mod.DownloadState(
                status="ready", path=str(installer),
            )
            with mock.patch.object(
                updater_mod, "current_install_target",
                return_value=root / "CareerScout.exe",
            ), mock.patch.object(
                updater_mod, "build_updater_script",
                return_value=("python", root / "update_apply.ps1"),
            ), mock.patch(
                "subprocess.Popen", side_effect=OSError("launch boom"),
            ):
                resp = client.post("/api/update-restart")
            self.assertEqual(resp.status_code, 500)
            data = resp.get_json()
            self.assertEqual(data["error_code"], "updater_launch_failed")
            self.assertEqual(
                data["user_message"],
                "更新脚本启动失败，请关闭软件后手动下载更新",
            )
            self.assertNotIn("OSError", data["user_message"])
            self.assertNotIn("launch boom", data["user_message"])


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
        """Scenario A: 024 新口径 <15=small, 15~30=medium, >30=large。"""
        cases = [
            (1, 1, 1, "small"),
            (3, 3, 1, "small"),    # 9
            (14, 1, 1, "small"),   # 14
            (15, 1, 1, "medium"),  # 15
            (30, 1, 1, "medium"),  # 30
            (31, 1, 1, "large"),   # 31
            (200, 1, 1, "large"),  # 200
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

    def test_preview_rejects_over_two_hundred_pages(self):
        resp = self.client.post("/api/search-scope/preview", json={
            "keywords": [f"kw{i}" for i in range(201)],
            "scope_kind": "cities",
            "cities": ["北京"],
            "pages_per_combination": 1,
        })
        self.assertEqual(resp.status_code, 422)
        data = resp.get_json()
        self.assertEqual(data["error_code"], "scope_validation_failed")
        self.assertIn("201", data["error"])
        self.assertIn("200", data["error"])

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

    def test_interest_can_be_cancelled_with_authoritative_identity(self):
        # Task 008: pipeline 收藏/撤销必须走权威三元组，不再接受裸平台 ID。
        job = {
            "platform": "boss",
            "platform_job_id": "boss-external-001",
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
        # _run_ai_screen_task(task_id, screening_fields, profile_summary,
        # scrape_task_id, resume_from_run_id, profile_facts)
        # submit 的 args[0] 是函数本身
        self.assertEqual(submit.call_args.args[4], "scrape-finished")
        self.assertEqual(submit.call_args.args[5], "")  # 无上次进度则不续跑

    def test_ai_screen_restores_user_finished_parent_from_db(self):
        store = self.app.config["TASK_STORE"]
        scrape_id = "user-finished-parent"
        jobs = [
            {"job_id": "j1", "platform_job_id": "j1", "title": "岗位",
             "source_url": "https://zhipin.example/j1.html"},
        ]
        store.create_screening_run(scrape_id, source_count=1, execution_params={
            "platform": "boss",
            "execution_config": {
                "schema_version": 1, "inter_combo_delay": 10,
                "detail_batch_size": 15, "detail_interval": 2,
                "detail_reset_every": 4, "detail_batch_cooldown": 5,
                "detail_tab_pool_size": 5, "screen_batch_size": 50,
                "screen_concurrency": 5, "match_batch_size": 4,
                "match_concurrency": 10, "config_digest": "cfg",
            },
            "frozen_scope": {
                "schema_version": 1, "platform": "boss",
                "keywords": ["前端"], "scope_kind": "cities",
                "cities": ["上海"], "pages_per_combination": 3,
                "combination_count": 1, "planned_pages": 3,
                "task_size": "small", "scope_digest": "scope",
            },
        })
        store.save_scrape_combo_result(scrape_id, "kw|city", jobs, ["kw|city"])
        store.update_screening_run(scrape_id, status="running", current_stage="scrape")
        store.update_screening_run(
            scrape_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="操作频繁",
        )
        finish = self.client.post(f"/api/task/finish/{scrape_id}")
        self.assertEqual(finish.status_code, 200, finish.get_json())
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
        ) as submit:
            accepted = self.client.post("/api/ai-screen", json={
                "screening_fields": {"salary": ["406"]},
                "profile_summary": "Python 后端",
                "scrape_task_id": scrape_id,
            })
        self.assertEqual(accepted.status_code, 200, accepted.get_json())
        self.assertEqual(submit.call_args.args[4], scrape_id)


class SourceErrorClassificationTests(unittest.TestCase):
    """016：分类只认结构化失败行；退出码兜底不再扫全文关键词。"""

    def test_exit_10_without_failure_line_is_status_unclear(self):
        from webui.source import _classify_failed_code
        # 全文关键词扫描已删除：敏感词输出不再定类
        for sample in (
            "触发风控：验证码拦截", "slider detected",
            "HTTP 429 Too Many Requests", "被限流了", "连续空页",
            "账号将于 2099-08-05 18:30 解封",
            "账号操作频繁，触发滑块验证，请稍后再试",
        ):
            self.assertEqual(_classify_failed_code(10, sample), "source_status_unclear")

    def test_failure_line_decides_the_code(self):
        from webui.source import _classify_failed_code
        cases = (
            ("source_verification_required", "__CAREERSCOUT_FAILED__ code=source_verification_required hint=验证码"),
            ("source_rate_limited", "__CAREERSCOUT_FAILED__ code=source_rate_limited hint=操作频繁"),
            ("source_login_required", "__CAREERSCOUT_FAILED__ code=source_login_required hint=401"),
            ("source_status_unclear", "__CAREERSCOUT_FAILED__ code=source_status_unclear hint=无法确认"),
        )
        for expected, sample in cases:
            self.assertEqual(_classify_failed_code(10, sample), expected)

    def test_exit_1_with_login_keyword_returns_login_required(self):
        from webui.source import _classify_failed_code
        self.assertEqual(
            _classify_failed_code(1, "请先登录 BOSS 直聘"),
            "source_login_required",
        )

    def test_exit_1_generic_returns_unknown_error(self):
        from webui.source import _classify_failed_code
        self.assertEqual(
            _classify_failed_code(1, "环境异常"),
            "source_unknown_error",
        )

    def test_exit_2_returns_cdp_unavailable(self):
        from webui.source import _classify_failed_code
        self.assertEqual(
            _classify_failed_code(2, "connect ECONNREFUSED 127.0.0.1:9222"),
            "source_cdp_unavailable",
        )

    def test_exit_3_returns_invalid_output(self):
        from webui.source import _classify_failed_code
        self.assertEqual(
            _classify_failed_code(3, "start-page 必须在 1 到 3 之间"),
            "source_invalid_output",
        )

    def test_unknown_exit_code_returns_unknown_error(self):
        from webui.source import _classify_failed_code
        self.assertEqual(
            _classify_failed_code(99, "unknown"),
            "source_unknown_error",
        )

    def test_rate_limit_label_not_verification(self):
        """限流文案不得显示成验证码/滑块（用户反馈回归）。"""
        from webui.pipeline_exec import _FAILED_CODE_LABELS
        self.assertEqual(_FAILED_CODE_LABELS["source_rate_limited"], "账号/操作频繁被限流")
        self.assertNotIn("验证码", _FAILED_CODE_LABELS["source_rate_limited"])

    def test_scrape_block_only_parses_failure_line(self):
        from webui.app import _classify_scrape_block
        # 关键词路径已删：只有失败行能给出码
        self.assertEqual(_classify_scrape_block(""), "")
        for sample in ("登录解锁更多职位", "频繁更新职位", "冻结岗位",
                       "账号操作频繁，触发滑块验证，请稍后再试",
                       "列表接口返回 HTTP 403（被风控拦截）"):
            self.assertEqual(_classify_scrape_block(sample), "", sample)
        self.assertEqual(
            _classify_scrape_block("__CAREERSCOUT_FAILED__ code=ip_risk_control hint=x"),
            "source_blocked")

    def test_failed_code_label_zhilian_login_has_no_boss(self):
        from webui.pipeline_exec import failed_code_label, taxonomy_reason
        self.assertEqual(
            failed_code_label("source_login_required", "zhilian"), "智联登录已失效")
        self.assertNotIn("BOSS", failed_code_label("login_expired", "zhilian"))
        self.assertIn("智联", taxonomy_reason("login_expired", "zhilian"))


if __name__ == "__main__":
    unittest.main()
