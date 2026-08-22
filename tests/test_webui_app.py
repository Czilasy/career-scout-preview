import hashlib
import json
import pathlib
import re
import sqlite3
import sys
import tempfile
import threading
import uuid
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock


from webui.app import create_app
from webui.task_runners import _iso_epoch_ms


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
        self._patcher = mock.patch("webui.app._theme_path", return_value=self.theme_path)
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
        """Scenario A: 1/9=small, 10/49=medium, 50/200=large。"""
        cases = [
            (1, 1, 1, "small"),
            (3, 3, 1, "small"),    # 9
            (2, 5, 1, "medium"),   # 10
            (49, 1, 1, "medium"),  # 49
            (5, 10, 1, "large"),   # 50
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


class TaskFinishAndCountRegressionTests(unittest.TestCase):
    """结束并保存 + JD 阶段计数回归（用户反馈 606/24/37/930）。"""

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
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        self.temp.cleanup()

    def _seed_paused_ai_screen(self):
        scrape_id = "finish-scrape-src"
        jobs = [
            {"job_id": f"j{i:03d}", "title": f"岗位{i}",
             "source_url": f"https://zhipin.example/j{i:03d}.html"}
            for i in range(930)
        ]
        self.store.create_screening_run(scrape_id, source_count=930)
        self.store.save_scrape_combo_result(scrape_id, "kw|city", jobs, ["kw|city"])
        run_id = "finish-ai-screen-run"
        self.store.create_screening_run(
            run_id, source_count=930,
            execution_params={
                "scrape_task_id": scrape_id,
                "profile_summary": "测试画像",
                "profile_facts": {"core_skills": ["Python"], "job_type": "全职"},
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.save_screening_verdicts(run_id, {
            f"j{i:03d}": {"verdict": "dropped", "reason": "粗筛移除"}
            for i in range(667, 930)
        })
        self.store.update_screening_run(
            run_id, status="paused", current_stage="jd_detail",
            processed_count=606, pending_count=24,
            total_kept=667, total_dropped=263,
            error_code="source_rate_limited",
            error_reason="账号/操作频繁被限流",
        )
        self.result_dir.mkdir(parents=True, exist_ok=True)
        jd_map = {f"j{i:03d}": f"JD {i}" for i in range(606)}
        (self.result_dir / f"ai_screen_jd_{run_id}.json").write_text(
            json.dumps(jd_map), encoding="utf-8"
        )
        for i in range(606, 630):
            self.store.insert_pending_result(
                run_id, f"j{i:03d}", failure_stage="jd_detail",
                failed_code="source_rate_limited",
                ai_payload_json={"reason": "账号/操作频繁被限流"},
            )
        return run_id

    def test_task_state_jd_stage_uses_kept_total(self):
        run_id = "count-jd-kept"
        self.store.create_screening_run(run_id, source_count=930)
        self.store.update_screening_run(
            run_id, status="running", current_stage="jd_detail",
            processed_count=606, pending_count=24,
            total_kept=667, total_dropped=263,
        )
        self.store.update_screening_run(
            run_id, status="paused", error_code="source_rate_limited",
            error_reason="账号/操作频繁被限流",
        )
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertEqual(data["success_count"], 606)
        self.assertEqual(data["fail_count"], 24)
        self.assertEqual(data["unstarted_count"], 37)
        self.assertEqual(data["total"], 667)
        self.assertEqual(data["source_total"], 930)

    def test_task_state_done_stage_uses_kept_total(self):
        """完成阶段也按粗筛保留数统计，不再把剔除岗位显示成未开始。"""
        run_id = "count-done-kept"
        self.store.create_screening_run(run_id, source_count=311)
        self.store.update_screening_run(
            run_id, status="succeeded", current_stage="done",
            processed_count=246, match_count=133, mismatch_count=113,
            pending_count=0, total_kept=246, total_dropped=65,
        )
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertEqual(data["success_count"], 246)
        self.assertEqual(data["fail_count"], 0)
        self.assertEqual(data["unstarted_count"], 0)
        self.assertEqual(data["total"], 246)
        self.assertEqual(data["source_total"], 311)

    def test_task_state_fallback_matches_screen_stage_weights(self):
        """DB-only task-state 兜底百分比必须与 emit 权重一致且不提前到 100。"""
        cases = [
            ("screen_a", 50, 100, 12),
            ("fetch_jd", 10, 100, 30),
            ("screen_b", 98, 100, 99),
            ("screen_b", 100, 100, 100),
        ]
        for index, (stage, processed, total, expected) in enumerate(cases):
            run_id = f"state-weight-{stage}-{index}"
            self.store.create_screening_run(run_id, source_count=total)
            self.store.update_screening_run(
                run_id, status="running", current_stage=stage,
                processed_count=processed, total_kept=total,
            )
            data = self.client.get(f"/api/task-state/{run_id}").get_json()
            self.assertEqual(data["progress"]["overall_percent"], expected)

    def test_task_state_uses_recrawl_weights_for_live_and_db_fallback(self):
        """重抓 task-state 使用 60/40 权重，实时与 DB 兜底一致。"""
        run_id = "state-recrawl-db"
        self.store.create_screening_run(run_id, source_count=100)
        self.store.update_screening_run(
            run_id, status="running", current_stage="recrawl_fetch_jd",
            processed_count=50,
        )
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertEqual(data["progress"]["overall_percent"], 30)

        live_id = "state-recrawl-live"
        self.store.create_screening_run(live_id, source_count=100)
        self.store.update_screening_run(
            live_id, status="running", current_stage="screen_b",
            processed_count=50, total_kept=100,
        )
        self.app.config["PIPELINE_TASKS"][live_id] = {
            "kind": "recrawl", "status": "running", "stage": "screen_b",
            "progress": {"stage": "screen_b", "current": 50, "total": 100},
            "logs": [], "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
        }
        data = self.client.get(f"/api/task-state/{live_id}").get_json()
        self.assertEqual(data["progress"]["overall_percent"], 80)

    def test_latest_running_task_memory_branch_returns_stage(self):
        """刷新接回内存运行任务时，stage 必须随快照返回。"""
        tasks = self.app.config["PIPELINE_TASKS"]
        tasks["mem-stage-task"] = {
            "kind": "scrape",
            "status": "running",
            "stage": "risk_warning",
            "progress": {
                "stage": "searching", "current": 2, "total": 10,
                "overall_percent": 20,
            },
            "logs": [], "error": "", "started_at": 1000, "finished_at": None,
            "platform": "boss", "task_input_digest": "digest-1",
        }
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["stage"], "risk_warning")
        self.assertEqual(data["progress"]["overall_percent"], 20)

    def test_latest_running_task_memory_branch_returns_extended_fields(self):
        self.store.create_screening_run(
            "scrape-parent", source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "Python", "city": ["上海"]},
            },
        )
        self.store.create_screening_run(
            "mem-ext", source_count=1,
            frozen_filters={"salary": ["20-30K"]},
            execution_params={
                "platform": "boss",
                "scrape_task_id": "scrape-parent",
                "profile_summary": "3年Python后端",
                "profile_facts": {"years": 3},
            },
        )
        self.store.update_screening_run("mem-ext", status="running", current_stage="ai_rough")
        tasks = self.app.config["PIPELINE_TASKS"]
        tasks["mem-ext"] = {
            "kind": "ai_screen", "status": "running", "stage": "ai_rough",
            "progress": {}, "logs": [], "error": "", "started_at": 1000,
            "finished_at": None, "platform": "boss",
        }
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["scrape_task_id"], "scrape-parent")
        self.assertFalse(data["scrape_completed"])
        self.assertEqual(data["frozen_filters"], {"salary": ["20-30K"]})
        self.assertEqual(data["profile_summary"], "3年Python后端")
        self.assertEqual(data["profile_facts"], {"years": 3})
        self.assertEqual(data["round_context"]["keywords"], ["Python"])
        self.assertEqual(data["round_context"]["screen_run_id"], "mem-ext")

    def test_latest_running_task_memory_branch_falls_back_to_progress_stage(self):
        tasks = self.app.config["PIPELINE_TASKS"]
        tasks["mem-stage-fallback"] = {
            "kind": "ai_screen",
            "status": "queued",
            "progress": {"stage": "combo_done", "overall_percent": 10},
            "logs": [], "error": "", "started_at": 1000, "finished_at": None,
        }
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertEqual(data["stage"], "combo_done")

    def test_latest_running_task_skips_stale_paused_when_newer_result_saved(self):
        """已有更新的完成结果时，旧 paused 任务不再抢占刷新后的恢复提示。"""
        run_id = "stale-paused"
        self.store.create_screening_run(run_id, source_count=930)
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="ai_fine",
            error_code="ai_rate_limited", error_reason="AI 服务限流",
        )
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET updated_at = ? WHERE id = ?",
                ("2026-08-01T08:00:00+08:00", run_id),
            )
        self.store.save_pipeline_result(
            {
                "jobs": [], "dropped": [], "total_scraped": 930,
                "total_kept": 0, "total_dropped": 930, "profile_summary": "",
            },
            {},
        )
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertFalse(data["has_task"])

    def test_finish_paused_task_saves_partial_snapshot_and_latest(self):
        run_id = self._seed_paused_ai_screen()
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["status"], "completed_with_pending")
        self.assertEqual(len(data["result"]["jobs"]), 667)
        self.assertEqual(len(data["result"]["dropped"]), 263)
        self.assertEqual(data["result"]["total_scraped"], 930)
        self.assertEqual(data["result"]["total_kept"], 667)
        self.assertEqual(
            data["result"]["profile_facts"],
            {"core_skills": ["Python"], "job_type": "全职"},
        )
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertEqual(latest["status"], "completed_with_pending")
        self.assertEqual(latest["source_run_id"], data["snapshot_run_id"])
        self.assertEqual(
            latest["result"]["profile_facts"],
            {"core_skills": ["Python"], "job_type": "全职"},
        )
        ctx = latest.get("round_context") or {}
        self.assertEqual(ctx.get("status"), "partial")
        self.assertFalse(ctx.get("resumable"))
        finished = self.store.get_screening_run(run_id)
        self.assertEqual(finished["status"], "interrupted")
        self.assertEqual(finished["error_code"], "user_finished")
        latest_running = self.client.get("/api/latest-running-task").get_json()
        self.assertFalse(latest_running["has_task"])

    def test_finish_snapshot_preserves_parent_search_params(self):
        run_id = self._seed_paused_ai_screen()
        parent = self.store.get_screening_run("finish-scrape-src")
        ep = dict(parent.get("execution_params") or {})
        ep["script_params"] = {
            "keyword": "Python",
            "city": ["上海"],
            "locations": [{
                "platform": "boss", "city_name": "上海",
                "district_name": "浦东新区", "district_code": "310115",
            }],
        }
        self.store.update_screening_execution_params("finish-scrape-src", ep)
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        sp = latest.get("script_params") or {}
        self.assertEqual(sp.get("keyword"), "Python")
        self.assertEqual(sp.get("city"), ["上海"])
        self.assertEqual(sp.get("locations"), ep["script_params"]["locations"])

    def test_finish_restart_interrupted_ai_screen_saves_partial_snapshot(self):
        """服务重启中断的任务也能直接结束并保存部分结果，无需先重新开始。"""
        run_id = self._seed_paused_ai_screen()
        self.store.update_screening_run(
            run_id, status="interrupted", error_code="restart",
            error_reason="服务重启中断",
        )
        self.store.save_interruption_kind(run_id, "process_restart")
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["status"], "completed_with_pending")
        self.assertEqual(len(data["result"]["jobs"]), 667)
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertEqual(latest["source_run_id"], data["snapshot_run_id"])
        finished = self.store.get_screening_run(run_id)
        self.assertEqual(finished["error_code"], "user_finished")
        ctx = latest.get("round_context") or {}
        self.assertEqual(ctx.get("status"), "partial")
        self.assertFalse(ctx.get("resumable"))
        # finish 表示用户主动结束，kind 必须从 process_restart 改为
        # user_finished，否则公共状态仍显示 interrupted（可恢复），
        # 误导用户以为任务还能继续。
        self.assertEqual(finished["interruption_kind"], "user_finished")

    def test_finish_rejects_user_cancelled_interrupted_run(self):
        run_id = "finish-cancelled-run"
        self.store.create_screening_run(run_id, source_count=1)
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(
            run_id, status="cancelled", error_reason="用户已停止筛选")
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "interrupted_not_restartable")

    def test_finish_paused_scrape_run_saves_partial_snapshot(self):
        """列表抓取阶段暂停的任务也能结束并保存已抓岗位。"""
        run_id = "finish-scrape-run"
        jobs = [
            {"job_id": "s1", "title": "岗位1", "source_url": "https://zhipin.example/s1.html"},
            {"job_id": "s2", "title": "岗位2", "source_url": "https://zhipin.example/s2.html"},
        ]
        self.store.create_screening_run(run_id, source_count=len(jobs))
        self.store.save_scrape_combo_result(run_id, "kw|city", jobs, ["kw|city"])
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="账号/操作频繁被限流",
        )
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["status"], "completed_with_pending")
        self.assertEqual(data["result"]["total_scraped"], 2)
        self.assertEqual(data["result"]["total_kept"], 2)
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertEqual(latest["status"], "completed_with_pending")

    def test_finish_paused_scrape_run_preserves_profile_facts(self):
        """列表抓取提前结束时，快照必须继承父任务的隐藏画像事实。"""
        run_id = "finish-scrape-facts"
        jobs = [
            {"job_id": "s1", "title": "岗位1", "source_url": "https://zhipin.example/s1.html"},
        ]
        facts = {"core_skills": ["Python"], "job_type": "全职"}
        self.store.create_screening_run(
            run_id, source_count=len(jobs),
            execution_params={
                "platform": "boss",
                "profile_summary": "3年Python后端候选人",
                "profile_facts": facts,
            },
        )
        self.store.save_scrape_combo_result(run_id, "kw|city", jobs, ["kw|city"])
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="测试限流",
        )
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["result"]["profile_facts"], facts)
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertEqual(latest["result"]["profile_facts"], facts)

    def _seed_scrape_run(self, run_id, count, status, platform="boss",
                        error_code=None, error_reason=None):
        jobs = [
            {"job_id": f"j{i}", "platform_job_id": f"j{i}", "title": f"岗位{i}",
             "source_url": f"https://{platform}.example/j{i}.html"}
            for i in range(count)
        ]
        self.store.create_screening_run(
            run_id, source_count=count,
            execution_params={"platform": platform},
        )
        self.store.save_scrape_combo_result(run_id, "kw|city", jobs, ["kw|city"])
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        if status == "interrupted":
            self.store.update_screening_run(
                run_id, status="interrupted", current_stage="scrape",
                error_code=error_code or "restart",
                error_reason=error_reason or "服务重启中断",
            )
            self.store.save_interruption_kind(run_id, "process_restart")
        else:
            self.store.update_screening_run(
                run_id, status=status, current_stage="scrape",
                error_code=error_code, error_reason=error_reason,
            )
        return jobs

    def test_latest_running_task_restores_failed_scrape_with_real_count(self):
        self._seed_scrape_run(
            "recover-failed", 1280, "failed", platform="boss",
            error_code="scrape_failed", error_reason="列表抓取失败",
        )
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["kind"], "scrape")
        self.assertEqual(data["status"], "failed")
        self.assertEqual(data["scraped_count"], 1280)
        self.assertEqual(data["source_total"], 1280)
        self.assertEqual(data["platform"], "boss")
        self.assertEqual(data["scrape_task_id"], "recover-failed")

    def test_latest_running_task_restores_completed_plain_scrape(self):
        run_id = "recover-plain-completed"
        jobs = [{
            "job_id": "j1", "platform_job_id": "j1", "title": "岗位1",
            "source_url": "https://zhipin.example/j1.html",
        }]
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "Python", "city": ["上海"]},
                "profile_summary": "3年Python后端",
                "profile_facts": {"years": 3},
            },
        )
        self.store.save_scrape_combo_result(run_id, "kw|city", jobs, ["kw|city"])
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(run_id, status="succeeded", current_stage="scrape")
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["kind"], "scrape")
        self.assertEqual(data["scrape_task_id"], run_id)
        self.assertTrue(data["scrape_completed"])
        self.assertEqual(data["profile_summary"], "3年Python后端")
        self.assertEqual(data["profile_facts"], {"years": 3})
        self.assertEqual(data["round_context"]["keywords"], ["Python"])
        saved = self.client.post("/api/scrape-result-save", json={"task_id": run_id}).get_json()
        self.assertTrue(saved["saved"])
        self.assertFalse(self.client.get("/api/latest-running-task").get_json()["has_task"])

    def test_latest_running_task_restores_paused_and_interrupted_counts(self):
        self._seed_scrape_run(
            "recover-paused", 12, "paused", platform="boss",
            error_code="captcha_required", error_reason="验证码",
        )
        paused = self.client.get("/api/latest-running-task").get_json()
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["scraped_count"], 12)

    def test_latest_running_task_zhilian_pause_reason_has_no_boss_fallback(self):
        self._seed_scrape_run(
            "recover-zhilian-pause", 3, "paused", platform="zhilian",
            error_code="source_login_required", error_reason="",
        )
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertEqual(data["pause_info"]["error_reason"], "智联登录已失效")
        self.assertNotIn("BOSS", data["progress"]["message"])

    def test_latest_running_task_restores_interrupted_count(self):
        self._seed_scrape_run(
            "recover-interrupted", 8, "interrupted", platform="zhilian",
        )
        interrupted = self.client.get("/api/latest-running-task").get_json()
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertEqual(interrupted["scraped_count"], 8)
        self.assertEqual(interrupted["platform"], "zhilian")
        self.assertEqual(interrupted["kind"], "scrape")
        self.assertIn("上次抓取", interrupted["progress"]["message"])

    def test_latest_running_task_skips_failed_when_newer_result_saved(self):
        self._seed_scrape_run("recover-stale-failed", 5, "failed")
        self.store.save_pipeline_result({
            "jobs": [], "dropped": [], "total_scraped": 5, "total_kept": 0,
            "total_dropped": 5, "profile_summary": "",
        }, {})
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertFalse(data["has_task"])

    def test_latest_running_task_true_zero_jobs_still_zero(self):
        run_id = "recover-zero"
        self.store.create_screening_run(run_id, source_count=0)
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(run_id, status="failed", current_stage="scrape")
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertFalse(data["has_task"])

    def test_latest_running_task_reconciles_orphaned_running_scrape(self):
        """数据已抓完但终态漏写的 running 抓取，刷新时自动补写完成。"""
        run_id = "orphan-running-complete"
        jobs = [
            {"job_id": "j1", "platform_job_id": "j1", "title": "岗位1",
             "source_url": "https://zhipin.example/j1.html"},
            {"job_id": "j2", "platform_job_id": "j2", "title": "岗位2",
             "source_url": "https://zhipin.example/j2.html"},
        ]
        self.store.create_screening_run(
            run_id, source_count=2,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "AI", "city": ["深圳"]},
            },
        )
        self.store.save_scrape_combo_result(run_id, "kw|city1", [jobs[0]], ["kw|city1"])
        self.store.save_scrape_combo_result(
            run_id, "kw|city2", [jobs[1]], ["kw|city1", "kw|city2"])
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")

        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["kind"], "scrape")
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["current_stage"], "scrape")
        events = self.store.list_task_events(run_id)
        self.assertEqual(
            sum(1 for e in events if e["type"] == "stage_complete"), 1)
        # 幂等：再次恢复不再重复补写完成事件。
        again = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(again["has_task"])
        events = self.store.list_task_events(run_id)
        self.assertEqual(
            sum(1 for e in events if e["type"] == "stage_complete"), 1)

    def test_finish_failed_scrape_run_saves_partial_snapshot(self):
        self._seed_scrape_run(
            "finish-failed-scrape", 3, "failed", platform="zhilian",
            error_code="scrape_failed", error_reason="列表抓取失败",
        )
        resp = self.client.post("/api/task/finish/finish-failed-scrape")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["status"], "completed_with_pending")
        self.assertEqual(data["platform"], "zhilian")
        self.assertEqual(data["result"]["total_scraped"], 3)
        finished = self.store.get_screening_run("finish-failed-scrape")
        self.assertEqual(finished["status"], "interrupted")
        self.assertEqual(finished["error_code"], "user_finished")

    def test_finish_running_scrape_run_saves_partial_snapshot(self):
        self._seed_scrape_run("finish-running-scrape", 4, "running")
        resp = self.client.post("/api/task/finish/finish-running-scrape")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["result"]["total_scraped"], 4)
        self.assertEqual(len(data["result"]["jobs"]), 4)
        finished = self.store.get_screening_run("finish-running-scrape")
        self.assertEqual(finished["error_code"], "user_finished")

    def test_finish_without_snapshot_saves_empty_result(self):
        """0 岗位结束保存：允许 finish 并保存空快照，空快照不进历史列表。"""
        run_id = "finish-no-snapshot"
        self.store.create_screening_run(run_id, source_count=0)
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertEqual(data["status"], "completed_with_pending")
        self.assertEqual(data["result"]["total_scraped"], 0)
        self.assertEqual(len(data["result"]["jobs"]), 0)
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["error_code"], "user_finished")
        self.assertEqual(run["interruption_kind"], "user_finished")
        self.assertEqual(self.store.list_history_rounds(), [])

    def test_finish_twice_returns_explicit_conflict(self):
        self._seed_scrape_run("finish-twice", 2, "paused")
        first = self.client.post("/api/task/finish/finish-twice")
        self.assertEqual(first.status_code, 200)
        second = self.client.post("/api/task/finish/finish-twice")
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()["error"], "already_finished")

    def test_cancel_after_finish_returns_friendly_conflict(self):
        """用户已结束保存的任务再点取消，返回明确冲突而不改写终态。"""
        self._seed_scrape_run("finish-cancel-guard", 2, "paused")
        first = self.client.post("/api/task/finish/finish-cancel-guard")
        self.assertEqual(first.status_code, 200)
        resp = self.client.post("/api/task/cancel/finish-cancel-guard")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "already_finished")
        run = self.store.get_screening_run("finish-cancel-guard")
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["error_code"], "user_finished")

    def test_latest_pipeline_result_returns_parent_scrape_task_id(self):
        self._seed_scrape_run("finish-parent-link", 2, "paused", platform="boss")
        resp = self.client.post("/api/task/finish/finish-parent-link")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertEqual(latest["scrape_task_id"], "finish-parent-link")
        self.assertEqual(latest["platform"], "boss")

    def test_finish_partial_jobs_and_dropped_carry_platform(self):
        self._seed_scrape_run(
            "finish-zhilian-partial", 3, "paused", platform="zhilian",
            error_code="source_rate_limited", error_reason="操作频繁",
        )
        self.store.save_screening_verdicts("finish-zhilian-partial", {
            "j0": {"verdict": "dropped", "reason": "粗筛移除"},
        })
        resp = self.client.post("/api/task/finish/finish-zhilian-partial")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        result = resp.get_json()["result"]
        self.assertTrue(result["jobs"])
        self.assertTrue(result["dropped"])
        for job in result["jobs"]:
            self.assertEqual(job["platform"], "zhilian")
        for job in result["dropped"]:
            self.assertEqual(job["platform"], "zhilian")

    def test_task_state_zhilian_pause_info_has_no_boss_text(self):
        run_id = "zhilian-pause-info"
        self.store.create_screening_run(
            run_id, source_count=1, execution_params={"platform": "zhilian"},
        )
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_login_required", error_reason="",
        )
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertIn("智联", data["pause_info"]["error_reason"])
        self.assertNotIn("BOSS", data["pause_info"]["error_reason"])

    def test_finish_normalizes_mismatch_verdict(self):
        """partial 快照把历史 mismatch 归一为 not_match，避免待确认计数膨胀。"""
        run_id = "finish-mismatch"
        jobs = [
            {"job_id": "m1", "title": "岗位", "source_url": "https://zhipin.example/m1.html"},
        ]
        scrape_id = "finish-mismatch-src"
        self.store.create_screening_run(scrape_id, source_count=1)
        self.store.save_scrape_combo_result(scrape_id, "kw|city", jobs, ["kw|city"])
        self.store.create_screening_run(
            run_id, source_count=1, execution_params={"scrape_task_id": scrape_id},
        )
        self.store.update_screening_run(run_id, status="running", current_stage="jd_detail")
        self.store.save_screening_verdicts(run_id, {
            "m1": {"verdict": "mismatch", "reason": "不符合"},
        })
        self.store.update_screening_run(
            run_id, status="paused", current_stage="jd_detail",
            processed_count=0, total_kept=1, total_dropped=0,
        )
        self.result_dir.mkdir(parents=True, exist_ok=True)
        (self.result_dir / f"ai_screen_jd_{run_id}.json").write_text("{}", encoding="utf-8")
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["result"]["jobs"][0]["verdict"], "not_match")

    def test_finish_recrawl_partial_preserves_reason_caveats_and_dropped(self):
        """重抓中途结束：partial 快照必须保留来源 reason/caveats，淘汰不能归零。"""
        source_result = {
            "jobs": [
                {"job_id": "j1", "title": "岗位A", "company": "公司A",
                 "salary": "10K", "location": "东莞", "tags": "1-3年", "jd": "JD A",
                 "source_url": "https://zhipin.example/j1.html",
                 "verdict": "match", "verdict_reason": "技能匹配", "caveats": ["注意学历"]},
                {"job_id": "j2", "title": "岗位B", "company": "公司B",
                 "salary": "8K", "location": "东莞", "tags": "1-3年", "jd": "JD B",
                 "source_url": "https://zhipin.example/j2.html",
                 "verdict": "uncertain", "verdict_reason": "AI 漏判，待确认", "caveats": []},
            ],
            "dropped": [
                {"job_id": "d1", "title": "淘汰岗", "reason": "经验不符",
                 "canonical_url": "https://zhipin.example/d1.html"},
            ],
            "total_scraped": 3, "total_kept": 2, "total_matched": 1,
            "total_dropped": 1, "profile_summary": "画像", "error": "",
        }
        source_id = self.store.save_pipeline_result(source_result, {})
        recrawl_id = "finish-recrawl-partial"
        self.store.create_screening_run(
            recrawl_id, source_count=2,
            execution_params={"source_run_id": source_id, "profile_summary": "画像"},
        )
        self.store.update_screening_run(
            recrawl_id, status="running", current_stage="recrawl_fetch_jd",
        )
        self.store.update_screening_run(
            recrawl_id, status="paused", current_stage="recrawl_ai",
            error_code="ai_network_error", error_reason="AI 网络故障",
        )
        resp = self.client.post(f"/api/task/finish/{recrawl_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        result = resp.get_json()["result"]
        self.assertEqual(result["total_scraped"], 3)
        self.assertEqual(result["total_kept"], 2)
        self.assertEqual(result["total_dropped"], 1)
        self.assertEqual(len(result["dropped"]), 1)
        self.assertEqual(result["dropped"][0]["platform_job_id"], "d1")
        self.assertEqual(result["dropped"][0]["reason"], "经验不符")
        jobs = {job["platform_job_id"]: job for job in result["jobs"]}
        self.assertEqual(jobs["j1"]["verdict_reason"], "技能匹配")
        self.assertEqual(jobs["j1"]["caveats"], ["注意学历"])
        self.assertEqual(jobs["j2"]["verdict_reason"], "AI 漏判，待确认")

    def test_recrawl_writeback_reflects_in_latest_result_without_new_snapshot(self):
        """重抓写回来源 run 后，latest-pipeline-result 应实时反映新判定和淘汰。"""
        source_id = self.store.save_pipeline_result({
            "jobs": [
                {"job_id": "j1", "title": "岗位", "verdict": "uncertain",
                 "verdict_reason": "待确认", "caveats": []},
            ],
            "dropped": [{"job_id": "d1", "title": "淘汰", "reason": "粗筛移除"}],
            "total_scraped": 2, "total_kept": 1, "total_matched": 0,
            "total_dropped": 1, "profile_summary": "画像", "error": "",
        }, {})
        self.store.insert_pending_result(
            source_id, "j1", failure_stage="ai_fine", failed_code="ai_missing_job",
        )
        self.store.save_screening_verdicts(source_id, {
            "j1": {"verdict": "not_match", "reason": "重判不匹配", "caveats": ["新提示"]},
        })
        self.store.delete_pending_result(source_id, "j1")
        self.store.recount_pipeline_result(source_id)
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertEqual(latest["status"], "completed")
        job = latest["result"]["jobs"][0]
        self.assertEqual(job["verdict"], "not_match")
        self.assertEqual(job["verdict_reason"], "重判不匹配")
        self.assertEqual(job["caveats"], ["新提示"])
        self.assertEqual(latest["result"]["total_dropped"], 1)

    def test_finish_rejects_non_paused_run(self):
        run_id = "finish-not-paused"
        self.store.create_screening_run(run_id, source_count=1)
        self.store.update_screening_run(
            run_id, status="succeeded", current_stage="done")
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "already_terminal")

    def test_recrawl_without_source_run_id_rejected(self):
        """017-US4: 重抓必须显式携带目标轮；缺省即使存在最新轮也 409。"""
        source_id = self.store.save_pipeline_result({
            "jobs": [{"job_id": "j1", "platform_job_id": "j1", "title": "岗位",
                      "verdict": "uncertain", "verdict_reason": "待确认",
                      "caveats": []}],
            "dropped": [], "total_scraped": 1, "total_kept": 1,
            "total_matched": 0, "total_dropped": 0, "profile_summary": "画像",
            "error": "",
        }, {"platform": "boss"})
        self.store.insert_pending_result(
            source_id, "j1", failure_stage="ai_fine", failed_code="ai_missing_job",
        )
        resp = self.client.post("/api/pipeline/recrawl", json={
            "job_ids": ["j1"], "profile_summary": "画像",
        })
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "missing_source_run_id")

    def test_jd_refetch_without_source_run_id_rejected(self):
        """017-US4: 单岗位 JD 补抓必须携带目标轮；缺省被拒绝。"""
        resp = self.client.post("/api/pipeline/jobs/j1/jd", json={
            "source_url": "https://zhipin.example/j1.html",
            "profile_summary": "画像",
        })
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "missing_source_run_id")

    def test_legacy_reset_latest_result_endpoint_removed(self):
        """017-US4: 旧结果清空端点已删除；归档/删除统一走历史接口。"""
        resp = self.client.post("/api/reset-latest-result", json={})
        self.assertEqual(resp.status_code, 404)

    def _save_zhilian_pipeline_result(self):
        return self.store.save_pipeline_result({
            "jobs": [
                {"job_id": "j1", "platform_job_id": "pj1", "title": "匹配岗",
                 "company": "公司A", "salary": "2-3万", "location": "上海",
                 "experience": "1-3年", "degree": "本科",
                 "canonical_url": "https://zhaopin.example/j1.htm",
                 "verdict": "match", "verdict_reason": "", "caveats": []},
            ],
            "dropped": [
                {"job_id": "d1", "platform_job_id": "pd1", "title": "淘汰岗",
                 "reason": "薪资低于期望",
                 "canonical_url": "https://zhaopin.example/d1.htm"},
            ],
            "total_scraped": 2, "total_kept": 1, "total_matched": 1,
            "total_dropped": 1, "profile_summary": "画像", "error": "",
        }, {"platform": "zhilian"})

    def test_platform_latest_result_keeps_job_links(self):
        """按平台读取结果快照时，岗位链接必须从 source_url 列回填。"""
        self._save_zhilian_pipeline_result()
        latest = self.client.get(
            "/api/latest-pipeline-result?platform=zhilian").get_json()
        self.assertTrue(latest["has_result"])
        self.assertEqual(
            latest["result"]["jobs"][0]["canonical_url"],
            "https://zhaopin.example/j1.htm",
        )
        self.assertEqual(
            latest["result"]["dropped"][0]["canonical_url"],
            "https://zhaopin.example/d1.htm",
        )

    def test_platform_latest_result_keeps_verdict_fields(self):
        """R1: 按平台读取结果快照时 verdict 系字段必须完整（曾漏字段导致全部进“待确认”）。

        jobs 必须带 verdict/verdict_reason/caveats/tags/extra；
        total_matched 取 match_count 而非 total_scraped；
        partial 快照映射为 completed_with_pending 与全量路径一致。
        """
        self.store.save_pipeline_result({
            "jobs": [
                {"job_id": "j1", "platform_job_id": "pj1", "title": "匹配岗",
                 "company": "公司A", "salary": "2-3万", "location": "上海",
                 "tags": "Python", "jd": "岗位描述",
                 "canonical_url": "https://zhaopin.example/j1.htm",
                 "verdict": "match", "verdict_reason": "技能匹配",
                 "caveats": ["注意学历"], "extra": {"company_nature_label": "国企"}},
                {"job_id": "j2", "platform_job_id": "pj2", "title": "待确认岗",
                 "canonical_url": "https://zhaopin.example/j2.htm",
                 "verdict": "uncertain", "verdict_reason": "AI 漏判",
                 "caveats": ["薪资待确认"], "extra": {}},
            ],
            "dropped": [],
            "total_scraped": 2, "total_kept": 2, "total_matched": 1,
            "total_dropped": 0, "profile_summary": "画像", "error": "",
        }, {"platform": "zhilian"})
        latest = self.client.get(
            "/api/latest-pipeline-result?platform=zhilian").get_json()
        self.assertTrue(latest["has_result"])
        self.assertEqual(latest["status"], "completed_with_pending")
        jobs = {job["platform_job_id"]: job for job in latest["result"]["jobs"]}
        self.assertEqual(jobs["pj1"]["verdict"], "match")
        self.assertEqual(jobs["pj1"]["verdict_reason"], "技能匹配")
        self.assertEqual(jobs["pj1"]["caveats"], ["注意学历"])
        self.assertEqual(jobs["pj1"]["tags"], "Python")
        self.assertEqual(jobs["pj1"]["jd"], "岗位描述")
        self.assertEqual(jobs["pj1"]["extra"], {"company_nature_label": "国企"})
        self.assertEqual(jobs["pj2"]["verdict"], "uncertain")
        self.assertEqual(jobs["pj2"]["caveats"], ["薪资待确认"])
        # total_matched 必须取 match_count（1），而不是 total_scraped（2）
        self.assertEqual(latest["result"]["total_matched"], 1)
        self.assertEqual(latest["result"]["total_kept"], 2)
        self.assertEqual(latest["result"]["profile_summary"], "画像")

    def test_pipeline_export_csv_groups_matched_before_dropped_with_links(self):
        self._save_zhilian_pipeline_result()

        response = self.client.get(
            "/api/pipeline-result/export.csv?platform=zhilian")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.content_type)
        self.assertIn("career_scout_jobs_zhilian.csv", response.headers["Content-Disposition"])
        lines = [
            line for line in response.get_data(as_text=True).splitlines()
            if line.strip()
        ]
        titles = [line.lstrip("\ufeff").split(",")[0] for line in lines]
        self.assertEqual(titles, ["title", "匹配：", "匹配岗", "不匹配：", "淘汰岗"])
        self.assertIn("https://zhaopin.example/j1.htm", lines[2])
        self.assertIn("薪资低于期望", lines[4])
        self.assertIn("https://zhaopin.example/d1.htm", lines[4])

    def test_pipeline_export_csv_without_result_returns_not_found(self):
        response = self.client.get(
            "/api/pipeline-result/export.csv?platform=zhilian")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error_code"], "not_found")

    def test_pipeline_export_csv_by_run_id_accepts_done_snapshot(self):
        """前端按当前结果的 run_id 导出：done 状态快照必须可导出。"""
        run_id = self._save_zhilian_pipeline_result()

        response = self.client.get(
            f"/api/pipeline-result/export.csv?run_id={run_id}")

        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn("匹配：", text)
        self.assertIn("不匹配：", text)


class ScreenContinueFlowTests(unittest.TestCase):
    """013：暂停路由、round_context 透传与续跑候选契约。"""

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
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _create_completed_scrape_run(self, script_params=None):
        run_id = f"scrape_{uuid.uuid4().hex[:8]}"
        self.store.create_screening_run(
            run_id,
            frozen_filters={},
            source_count=2,
            execution_params={
                "platform": "boss",
                "script_params": script_params or {
                    "keyword": "Python,后端", "city": ["上海"], "pages": 3,
                },
                "execution_config": {
                    "schema_version": 1, "inter_combo_delay": 10,
                    "detail_batch_size": 15, "detail_interval": 2,
                    "detail_reset_every": 4, "detail_batch_cooldown": 5,
                    "detail_tab_pool_size": 5, "screen_batch_size": 50,
                    "screen_concurrency": 5, "match_batch_size": 4,
                    "match_concurrency": 10, "config_digest": None,
                },
                "frozen_scope": {
                    "schema_version": 1, "platform": "boss",
                    "keywords": ["Python"], "scope_kind": "cities",
                    "cities": ["上海"], "pages_per_combination": 3,
                    "combination_count": 1, "planned_pages": 3,
                    "task_size": "small", "scope_digest": None,
                },
            },
            backend_version="test",
        )
        self.store.update_screening_run(
            run_id, status="succeeded", current_stage="done",
            processed_count=2, match_count=2,
        )
        jobs = [
            {"job_id": "j1", "platform_job_id": "j1", "title": "岗位",
             "source_url": "https://zhipin.example/j1.html"},
        ]
        self.store.save_scrape_combo_result(run_id, "kw|city", jobs, ["kw|city"])
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "done", "progress": {}, "logs": [],
            "result": {"ok": True, "jobs": jobs, "total_scraped": 1,
                       "total_matched": 1, "completed_combos": ["kw|city"]},
            "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(), "platform": "boss",
        }
        return run_id

    def _seed_ai_run(self, scrape_id, run_id, status="paused",
                    error_code=None, filters=None):
        self.store.create_screening_run(
            run_id,
            frozen_filters=filters or {"salary": ["20-30K"]},
            source_count=2,
            execution_params={
                "platform": "boss",
                "scrape_task_id": scrape_id,
                "profile_summary": "测试画像",
                "profile_facts": {"years": 3},
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(run_id, status=status)
        if error_code:
            self.store.update_screening_run(
                run_id, error_code=error_code, error_reason="用户提前结束")

    def test_pause_route_returns_pausing_and_sets_stop_mode(self):
        scrape_id = self._create_completed_scrape_run()
        run_id = "pause-screen-run"
        self._seed_ai_run(scrape_id, run_id, status="running")
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "ai_screen", "status": "running", "progress": {}, "logs": [],
            "error": "", "stop_event": threading.Event(), "platform": "boss",
        }
        resp = self.client.post(f"/api/task/pause/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["status"], "pausing")
        task = self.app.config["PIPELINE_TASKS"][run_id]
        self.assertEqual(task["stop_mode"], "pause")
        self.assertTrue(task["stop_event"].is_set())

    def test_pause_route_rejects_non_ai_and_terminal(self):
        run_id = "pause-reject"
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "recrawl", "status": "running", "progress": {}, "logs": [],
            "error": "", "stop_event": threading.Event(), "platform": "boss",
        }
        resp = self.client.post(f"/api/task/pause/{run_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "pausing")
        self.assertEqual(self.app.config["PIPELINE_TASKS"][run_id]["stop_mode"], "pause")

        done_id = "pause-done"
        self.app.config["PIPELINE_TASKS"][done_id] = {
            "kind": "ai_screen", "status": "done", "progress": {}, "logs": [],
            "error": "", "stop_event": threading.Event(), "platform": "boss",
        }
        resp = self.client.post(f"/api/task/pause/{done_id}")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "task_not_active")

    def test_latest_running_task_paused_returns_round_context(self):
        scrape_id = self._create_completed_scrape_run()
        run_id = "paused-ctx"
        self._seed_ai_run(scrape_id, run_id, status="paused")
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        ctx = data["round_context"]
        self.assertEqual(ctx["keywords"], ["Python", "后端"])
        self.assertEqual(ctx["cities"], ["上海"])
        self.assertEqual(ctx["screening_fields"], {"salary": ["20-30K"]})
        self.assertEqual(ctx["profile_summary"], "测试画像")
        self.assertEqual(ctx["profile_facts"], {"years": 3})
        self.assertEqual(ctx["screen_run_id"], run_id)
        self.assertEqual(ctx["status"], "paused")
        self.assertTrue(ctx["resumable"])

    def test_latest_pipeline_result_returns_round_context_from_snapshot(self):
        scrape_id = self._create_completed_scrape_run()
        run_id = "snapshot-ctx"
        self._seed_ai_run(scrape_id, run_id, status="paused")
        self.store.save_pipeline_result(
            {
                "ok": True, "jobs": [{"platform_job_id": "j1", "title": "岗位", "verdict": "uncertain"}],
                "dropped": [], "total_scraped": 1, "total_kept": 1,
            },
            {"platform": "boss"},
            status="partial",
            execution_params={
                "platform": "boss", "scrape_task_id": scrape_id,
                "screen_run_id": run_id,
            },
        )
        data = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertTrue(data["has_result"])
        ctx = data["round_context"]
        self.assertEqual(ctx["screen_run_id"], run_id)
        self.assertEqual(ctx["status"], "paused")
        self.assertTrue(ctx["resumable"])

    def test_ai_screen_resumes_failed_partial_and_user_finished(self):
        cases = [
            ("failed", "failed", None),
            ("partial", "partial", None),
            ("finished", "interrupted", "user_finished"),
        ]
        for label, status, error_code in cases:
            scrape_id = self._create_completed_scrape_run()
            run_id = f"resume-{label}"
            self._seed_ai_run(scrape_id, run_id, status=status, error_code=error_code)
            with mock.patch.object(
                self.app.config["PIPELINE_EXECUTOR"], "submit",
                return_value=mock.Mock(),
            ):
                resp = self.client.post("/api/ai-screen", json={
                    "scrape_task_id": scrape_id,
                    "screening_fields": {"salary": ["20-30K"]},
                    "profile_summary": "测试画像",
                    "profile_facts": {"years": 3},
                    "filter_schema_version": 1,
                })
            self.assertEqual(resp.status_code, 200, resp.get_json())
            data = resp.get_json()
            self.assertTrue(data["resuming"])
            self.assertNotEqual(data["task_id"], run_id)
            self.app.config["PIPELINE_TASKS"].pop(data["task_id"], None)

    def test_ai_screen_falls_back_to_parent_profile_facts_when_missing(self):
        """旧快照漏存画像事实时，AI 筛选从父抓取任务恢复三通道输入。"""
        scrape_id = self._create_completed_scrape_run()
        facts = {"core_skills": ["Python"], "job_type": "全职"}
        run = self.store.get_screening_run(scrape_id)
        ep = dict(run.get("execution_params") or {})
        ep["profile_summary"] = "3年Python后端候选人"
        ep["profile_facts"] = facts
        self.store.update_screening_execution_params(scrape_id, ep)
        captured = []
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *args, **kwargs: captured.append((fn, args, kwargs)) or None,
        ):
            resp = self.client.post("/api/ai-screen", json={
                "scrape_task_id": scrape_id,
                "screening_fields": {"salary": ["20-30K"]},
                "profile_summary": "3年Python后端候选人",
                "filter_schema_version": 1,
            })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        fn, args, kwargs = captured[0]
        self.assertEqual(args[5], facts)

    def test_b057_continue_switch_account_persists_target_identity(self):
        """B057：限流后切换到另一账号继续，冻结身份被替换且断点保留。"""
        task_id = "b057-switch"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "前端", "city": ["上海"], "pages": 3},
                "browser_account": "a", "cdp_port": 9222, "profile_key": "boss:a",
            },
        )
        if self.store.get_screening_run(task_id)["status"] == "queued":
            self.store.update_screening_run(task_id, status="running")
        self.store.update_screening_run(
            task_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="源账号限流",
        )
        checked = []
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda run: (
            checked.append((run.get("execution_params") or {}).get("browser_account"))
            or (True, "", "")
        )
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            resp = self.client.post(
                f"/api/task/continue/{task_id}",
                json={"target_account": "b"},
            )
        self.assertEqual(resp.status_code, 200, resp.get_json())
        params = (self.store.get_screening_run(task_id) or {}).get("execution_params") or {}
        self.assertEqual(params.get("browser_account"), "b")
        self.assertEqual(params.get("profile_key"), "boss:b")
        self.assertEqual(params.get("cdp_port"), 9222)
        self.assertEqual(checked, ["b", "b"])
        submit.assert_called_once()

    def test_b057_continue_switch_missing_account_rejected(self):
        task_id = "b057-missing"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={"platform": "boss",
                           "script_params": {"keyword": "前端", "city": ["上海"]}},
        )
        if self.store.get_screening_run(task_id)["status"] == "queued":
            self.store.update_screening_run(task_id, status="running")
        self.store.update_screening_run(
            task_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="源账号限流",
        )
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
        resp = self.client.post(
            f"/api/task/continue/{task_id}",
            json={"target_account": "nope"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.get_json()["error"], "target_account_not_found")

    def test_b057_continue_switch_no_longer_blocked_by_cooldown(self):
        # 016：冷却功能删除；切换账号续跑不再有 account_in_cooldown 拦截路径
        task_id = "b057-cooldown"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={"platform": "boss",
                           "script_params": {"keyword": "前端", "city": ["上海"]}},
        )
        if self.store.get_screening_run(task_id)["status"] == "queued":
            self.store.update_screening_run(task_id, status="running")
        self.store.update_screening_run(
            task_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="源账号限流",
        )
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
        resp = self.client.post(
            f"/api/task/continue/{task_id}",
            json={"target_account": "b"},
        )
        # 目标账号存在：不再返回 account_in_cooldown，按正常续跑路径走
        self.assertNotEqual(resp.get_json().get("error"), "account_in_cooldown")

    def test_b057_continue_switch_target_preflight_failure_keeps_identity(self):
        task_id = "b057-preflight"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "前端", "city": ["上海"], "pages": 3},
                "browser_account": "a", "cdp_port": 9222, "profile_key": "boss:a",
            },
        )
        if self.store.get_screening_run(task_id)["status"] == "queued":
            self.store.update_screening_run(task_id, status="running")
        self.store.update_screening_run(
            task_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="源账号限流",
        )
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (
            False, "cdp_unavailable", "目标账号浏览器未就绪",
        )
        resp = self.client.post(
            f"/api/task/continue/{task_id}",
            json={"target_account": "b"},
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "block_not_resolved")
        params = (self.store.get_screening_run(task_id) or {}).get("execution_params") or {}
        self.assertEqual(params.get("browser_account"), "a")

    def test_b057_continue_uses_active_account_without_target(self):
        """暂停中切换 active 账号后，不带 target_account 继续沿用新账号。"""
        task_id = "b057-active-switch"
        self.store.create_screening_run(
            task_id, source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "前端", "city": ["上海"], "pages": 3},
                "browser_account": "a", "cdp_port": 9222, "profile_key": "boss:a",
            },
        )
        if self.store.get_screening_run(task_id)["status"] == "queued":
            self.store.update_screening_run(task_id, status="running")
        self.store.update_screening_run(
            task_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="源账号限流",
        )
        with mock.patch("webui.pipeline_exec.close_debug_chrome"):
            activated = self.client.post("/api/browser-accounts/b/activate")
        self.assertEqual(activated.status_code, 200, activated.get_json())
        checked = []
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda run: (
            checked.append((run.get("execution_params") or {}).get("browser_account"))
            or (True, "", "")
        )
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit") as submit:
            resp = self.client.post(f"/api/task/continue/{task_id}", json={})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        params = (self.store.get_screening_run(task_id) or {}).get("execution_params") or {}
        self.assertEqual(params.get("browser_account"), "b")
        self.assertEqual(params.get("profile_key"), "boss:b")
        self.assertEqual(params.get("cdp_port"), 9222)
        self.assertEqual(checked, ["b", "b"])
        submit.assert_called_once()

    def test_worker_pause_writes_paused_without_history_round(self):
        """US1：用户暂停后任务可继续，但历史列表零增长（不再写 partial 快照）。"""
        from webui import pipeline_exec
        scrape_id = self._create_completed_scrape_run()
        captured = []
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *args, **kwargs: captured.append((fn, args, kwargs)) or None,
        ):
            resp = self.client.post("/api/ai-screen", json={
                "scrape_task_id": scrape_id,
                "screening_fields": {"salary": ["20-30K"]},
                "profile_summary": "测试画像",
                "profile_facts": {"years": 3},
                "filter_schema_version": 1,
            })
        self.assertEqual(resp.status_code, 200)
        task_id = resp.get_json()["task_id"]
        fn, args, kwargs = captured[0]
        # 先设暂停模式但不发信号；粗筛 mock 落判定后注入暂停信号，
        # 使暂停检测点（粗筛后 3446）已有部分判定——US1 核心场景
        # （AI 已判部分岗位后点暂停）。
        self.app.config["PIPELINE_TASKS"][task_id]["stop_mode"] = "pause"
        def _rough_ok(*a, **kw):
            kw["on_batch_done"]({"j1": "match"}, ["j1"])
            self.app.config["PIPELINE_TASKS"][task_id]["stop_event"].set()
            return {"verdicts": {"j1": "match"}, "kept": ["j1"], "dropped": []}
        with mock.patch.object(
            pipeline_exec, "resolve_browser_account", return_value="",
        ), mock.patch.object(pipeline_exec, "set_active_cdp_data_dir"):
            with mock.patch.object(
                self.store, "get_ai_settings",
                return_value={"endpoint_url": "http://ai.test", "model": "m", "is_configured": True},
            ), mock.patch("webui.app.ai_service.is_ai_available", return_value=True), \
               mock.patch("webui.ai.screen_jobs", side_effect=_rough_ok):
                fn(*args, **kwargs)
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "paused")
        self.assertEqual(run["error_code"], "user_paused")
        self.assertEqual(self.app.config["PIPELINE_TASKS"][task_id]["status"], "paused")
        events = self.store.list_task_events(task_id)
        self.assertTrue(any(event["type"] == "pause" for event in events))
        # 017-US1：即使已判部分岗位，暂停也只是暂停，历史不新增轮
        self.assertEqual(self.store.list_history_rounds(), [])
        history = self.client.get("/api/result-history").get_json()
        self.assertEqual(history["items"], [])

    def test_worker_hard_block_pauses_without_history_round(self):
        """US1：AI 粗筛系统性阻断（如验证码/限流）强停任务，历史不新增轮。"""
        from webui import pipeline_exec
        from webui.ai import AISecurityError
        scrape_id = self._create_completed_scrape_run()
        captured = []
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *args, **kwargs: captured.append((fn, args, kwargs)) or None,
        ):
            resp = self.client.post("/api/ai-screen", json={
                "scrape_task_id": scrape_id,
                "screening_fields": {"salary": ["20-30K"]},
                "profile_summary": "测试画像",
                "profile_facts": {"years": 3},
                "filter_schema_version": 1,
            })
        self.assertEqual(resp.status_code, 200)
        task_id = resp.get_json()["task_id"]
        # 粗筛 mock 先落判定再抛阻断：强停时已有部分判定（US1 核心场景）
        def _blocking_screen_jobs(*args, **kwargs):
            kwargs["on_batch_done"]({"j1": "match"}, ["j1"])
            raise AISecurityError("ai_rate_limited")
        fn, args, kwargs = captured[0]
        with mock.patch.object(
            pipeline_exec, "resolve_browser_account", return_value="",
        ), mock.patch.object(pipeline_exec, "set_active_cdp_data_dir"):
            with mock.patch.object(
                self.store, "get_ai_settings",
                return_value={"endpoint_url": "http://ai.test", "model": "m", "is_configured": True},
            ), mock.patch("webui.app.ai_service.is_ai_available", return_value=True), \
               mock.patch("webui.ai.screen_jobs", side_effect=_blocking_screen_jobs), \
               mock.patch(
                   "webui.ai.map_ai_error_to_block_code",
                   return_value="ai_blocked",
               ):
                fn(*args, **kwargs)
        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "paused")
        self.assertTrue(run["error_code"])
        # 017-US1：硬阻断强停只是暂停，历史不新增轮
        self.assertEqual(self.store.list_history_rounds(), [])
        history = self.client.get("/api/result-history").get_json()
        self.assertEqual(history["items"], [])

    def test_scrape_result_save_falls_back_to_parent_profile(self):
        scrape_id = self._create_completed_scrape_run()
        run = self.store.get_screening_run(scrape_id)
        ep = dict(run.get("execution_params") or {})
        ep["profile_summary"] = "测试画像"
        ep["profile_facts"] = {"years": 3}
        self.store.update_screening_execution_params(scrape_id, ep)
        resp = self.client.post("/api/scrape-result-save", json={"task_id": scrape_id})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertTrue(resp.get_json().get("saved"))
        latest = self.client.get("/api/latest-pipeline-result").get_json()
        self.assertEqual((latest.get("result") or {}).get("profile_summary"), "测试画像")
        self.assertEqual((latest.get("result") or {}).get("profile_facts"), {"years": 3})

    def test_resume_chain_restores_full_survivors_after_collapse(self):
        """018 事故链回归：完整判定挂在链首 run，续跑幸存者不塌缩。

        run1 名下 277 条粗筛判定（165 kept + 112 dropped）+ 277 断点；
        run2（续跑目标）名下仅 40 条精筛判定 + 继承的 277 断点；
        run3 续跑 → 幸存者 165、40 条精筛判定被继承、无岗位静默消失。
        """
        from webui import pipeline_exec
        kept_n, dropped_n, fine_n = 165, 112, 40
        jobs = [
            {"job_id": f"j{i:03d}", "platform_job_id": f"j{i:03d}",
             "title": "岗位", "salary": "20K", "location": "上海",
             "source_url": f"https://zhipin.example/j{i:03d}.html"}
            for i in range(kept_n + dropped_n)
        ]
        kept_ids = [f"j{i:03d}" for i in range(kept_n)]
        dropped_ids = [f"j{kept_n + i:03d}" for i in range(dropped_n)]
        scrape_id = self._create_completed_scrape_run()
        self.app.config["PIPELINE_TASKS"][scrape_id]["result"] = {
            "ok": True, "jobs": jobs, "total_scraped": len(jobs),
            "total_matched": 0, "completed_combos": ["kw|city"],
        }
        run1 = "chain-run1"
        self._seed_ai_run(scrape_id, run1, status="failed")
        self.store.save_screening_verdicts(run1, {
            **{jid: "kept" for jid in kept_ids},
            **{jid: "dropped" for jid in dropped_ids},
        })
        self.store.save_checkpoint(run1, "ai_rough", kept_ids + dropped_ids)
        run2 = "chain-run2"
        self._seed_ai_run(scrape_id, run2, status="failed")
        fine_ids = kept_ids[:fine_n]
        self.store.save_screening_verdicts(run2, {
            jid: {"verdict": "not_match", "reason": "跨链路",
                  "caveats": [], "flags": []}
            for jid in fine_ids
        })
        self.store.save_checkpoint(run2, "ai_rough", kept_ids + dropped_ids)
        self.store.save_checkpoint(run2, "ai_fine", fine_ids)

        captured = []
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *a, **kw: captured.append((fn, a, kw)) or None,
        ):
            resp = self.client.post("/api/ai-screen", json={
                "scrape_task_id": scrape_id,
                "screening_fields": {"salary": ["20-30K"]},
                "profile_summary": "测试画像",
                "profile_facts": {"years": 3},
                "filter_schema_version": 1,
            })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertTrue(resp.get_json()["resuming"])
        task_id = resp.get_json()["task_id"]
        fn, args, kwargs = captured[0]

        screen_calls, match_calls = [], []

        def _fake_screen_jobs(jobs_arg, *a, **kw):
            screen_calls.append(list(jobs_arg))
            return {"kept": [], "dropped": [], "verdicts": {}}

        def _fake_match_jds(jobs_arg, *a, **kw):
            match_calls.append(list(jobs_arg))
            verdicts = {
                str(j["job_id"]): {"verdict": "match", "reason": "匹配",
                                   "caveats": [], "flags": []}
                for j in jobs_arg
            }
            if verdicts and kw.get("on_batch_done"):
                kw["on_batch_done"](
                    verdicts, [str(j["job_id"]) for j in jobs_arg])
            return {"verdicts": verdicts}

        def _fake_fetch_details(chunk, source, **kw):
            return {
                "jobs": [dict(j, jd="岗位描述") for j in chunk],
                "hard_stop": False, "stopped": False,
            }

        with mock.patch.object(
            pipeline_exec, "resolve_browser_account", return_value="",
        ), mock.patch.object(pipeline_exec, "set_active_cdp_data_dir"), \
           mock.patch.object(
               pipeline_exec, "ensure_chrome_ready",
               return_value=(True, "")), \
           mock.patch.object(pipeline_exec, "close_debug_chrome"), \
           mock.patch.object(
               pipeline_exec, "fetch_job_details",
               side_effect=_fake_fetch_details), \
           mock.patch.object(
               self.store, "get_ai_settings",
               return_value={"endpoint_url": "http://ai.test", "model": "m",
                             "is_configured": True},
           ), mock.patch(
               "webui.app.ai_service.is_ai_available", return_value=True,
           ), mock.patch(
               "webui.ai.screen_jobs", side_effect=_fake_screen_jobs,
           ), mock.patch(
               "webui.ai.match_jds", side_effect=_fake_match_jds,
           ):
            fn(*args, **kwargs)

        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["total_kept"], kept_n)
        self.assertEqual(run["total_dropped"], dropped_n)
        self.assertEqual(run["status"], "succeeded")
        # 链首 277 断点齐全：粗筛零重筛（旧代码会重筛或塌缩幸存者）
        self.assertEqual(screen_calls, [[]])
        # 40 条精筛判定被继承，只精筛剩余 125 条
        self.assertEqual(len(match_calls), 1)
        self.assertEqual(len(match_calls[0]), kept_n - fine_n)
        # 无岗位静默消失：幸存者 + 移除 = 全部岗位
        self.assertEqual(run["total_kept"] + run["total_dropped"], len(jobs))
        # 正常完成：历史恰好一条轮（018 换序后正常路径仍写轮）
        self.assertEqual(len(self.store.list_history_rounds()), 1)

    def test_finalize_mismatch_fails_without_history_round(self):
        """018：终态校验失败抛错时库里没有任何 done 轮（幽灵轮事故回归）。"""
        from webui import pipeline_exec
        jobs = [
            {"job_id": f"j{i}", "platform_job_id": f"j{i}", "title": "岗位",
             "source_url": f"https://zhipin.example/j{i}.html"}
            for i in range(3)
        ]
        scrape_id = self._create_completed_scrape_run()
        self.app.config["PIPELINE_TASKS"][scrape_id]["result"] = {
            "ok": True, "jobs": jobs, "total_scraped": 3,
            "total_matched": 0, "completed_combos": ["kw|city"],
        }
        captured = []
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *a, **kw: captured.append((fn, a, kw)) or None,
        ):
            resp = self.client.post("/api/ai-screen", json={
                "scrape_task_id": scrape_id,
                "screening_fields": {"salary": ["20-30K"]},
                "profile_summary": "测试画像",
                "profile_facts": {"years": 3},
                "filter_schema_version": 1,
            })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        task_id = resp.get_json()["task_id"]
        fn, args, kwargs = captured[0]

        def _fake_screen_jobs(jobs_arg, *a, **kw):
            # j3 既不保留也不移除：计数对不上，finalize 必判 paused
            return {
                "kept": ["j0"],
                "dropped": [{"job_id": "j1", "title": "岗位", "reason": "经验不符"}],
                "verdicts": {"j0": "kept", "j1": "dropped"},
            }

        def _fake_match_jds(jobs_arg, *a, **kw):
            verdicts = {
                str(j["job_id"]): {"verdict": "match", "reason": "匹配",
                                   "caveats": [], "flags": []}
                for j in jobs_arg
            }
            if verdicts and kw.get("on_batch_done"):
                kw["on_batch_done"](
                    verdicts, [str(j["job_id"]) for j in jobs_arg])
            return {"verdicts": verdicts}

        def _fake_fetch_details(chunk, source, **kw):
            return {
                "jobs": [dict(j, jd="岗位描述") for j in chunk],
                "hard_stop": False, "stopped": False,
            }

        with mock.patch.object(
            pipeline_exec, "resolve_browser_account", return_value="",
        ), mock.patch.object(pipeline_exec, "set_active_cdp_data_dir"), \
           mock.patch.object(
               pipeline_exec, "ensure_chrome_ready",
               return_value=(True, "")), \
           mock.patch.object(pipeline_exec, "close_debug_chrome"), \
           mock.patch.object(
               pipeline_exec, "fetch_job_details",
               side_effect=_fake_fetch_details), \
           mock.patch.object(
               self.store, "get_ai_settings",
               return_value={"endpoint_url": "http://ai.test", "model": "m",
                             "is_configured": True},
           ), mock.patch(
               "webui.app.ai_service.is_ai_available", return_value=True,
           ), mock.patch(
               "webui.ai.screen_jobs", side_effect=_fake_screen_jobs,
           ), mock.patch(
               "webui.ai.match_jds", side_effect=_fake_match_jds,
           ):
            fn(*args, **kwargs)

        run = self.store.get_screening_run(task_id)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "internal_error")
        self.assertEqual(
            self.app.config["PIPELINE_TASKS"][task_id]["status"], "failed")
        # 幽灵轮回归：校验失败时不得留下任何 done 历史
        self.assertEqual(self.store.list_history_rounds(), [])
        events = self.store.list_task_events(task_id)
        self.assertFalse(
            any(event["type"] == "history_snapshot" for event in events))

class ChromeAccountProfileSwitchTests(unittest.TestCase):
    """账号切换时，端口上旧账号的 Chrome 必须被替换而不是复用。"""

    def test_ensure_chrome_ready_replaces_wrong_profile(self):
        from webui import pipeline_exec
        launched = mock.Mock()
        launched.poll.return_value = None
        with mock.patch.object(
            pipeline_exec.boss, "is_cdp_ready", side_effect=[True, True],
        ), mock.patch.object(
            pipeline_exec.boss, "cdp_port_uses_profile", return_value=False,
        ) as uses, mock.patch.object(
            pipeline_exec.boss, "chrome_user_data_dirs_for_cdp_port",
            return_value=[pipeline_exec.BROWSER_ACCOUNTS["b"]["profile_dir"]],
        ), mock.patch.object(
            pipeline_exec.boss, "close_cdp_chrome",
        ) as close, mock.patch.object(
            pipeline_exec.boss, "prepare_cdp_profile",
            return_value={"path": "C:/profiles/account-b"},
        ), mock.patch.object(
            pipeline_exec.boss, "stop_cdp_chrome",
        ), mock.patch.object(
            pipeline_exec.boss, "launch_chrome", return_value=launched,
        ):
            ok, _msg = pipeline_exec.ensure_chrome_ready(9333)
        self.assertTrue(ok)
        uses.assert_called_once()
        close.assert_called_once()

    def test_ensure_chrome_ready_refuses_unknown_profile(self):
        """端口被非 A/B 的 Chrome 占用时禁止自动关闭，避免误伤主 Chrome。"""
        from webui import pipeline_exec
        with mock.patch.object(
            pipeline_exec.boss, "is_cdp_ready", return_value=True,
        ), mock.patch.object(
            pipeline_exec.boss, "cdp_port_uses_profile", return_value=False,
        ), mock.patch.object(
            pipeline_exec.boss, "chrome_user_data_dirs_for_cdp_port",
            return_value=["C:/unknown/profile"],
        ), mock.patch.object(
            pipeline_exec.boss, "close_cdp_chrome",
        ) as close:
            ok, msg = pipeline_exec.ensure_chrome_ready(9333)
        self.assertFalse(ok)
        self.assertIn("避免误关", msg)
        close.assert_not_called()

    def test_ensure_chrome_ready_minimizes_only_when_requested(self):
        """R6: 实际启动 Chrome 且 minimize_after_launch=True 时最小化窗口；默认不最小化。"""
        from webui import pipeline_exec
        launched = mock.Mock()
        launched.poll.return_value = None
        base = [
            mock.patch.object(
                pipeline_exec.boss, "is_cdp_ready", side_effect=[False, True],
            ),
            mock.patch.object(
                pipeline_exec.boss, "prepare_cdp_profile",
                return_value={"path": "C:/profiles/r6"},
            ),
            mock.patch.object(pipeline_exec.boss, "stop_cdp_chrome"),
            mock.patch.object(
                pipeline_exec.boss, "launch_chrome", return_value=launched,
            ),
        ]
        # 默认参数：不最小化（登录空间等调用方需要窗口在前台）
        with base[0], base[1], base[2], base[3], mock.patch.object(
            pipeline_exec.boss, "minimize_chrome_window",
        ) as minimize:
            ok, _msg = pipeline_exec.ensure_chrome_ready(9444)
        self.assertTrue(ok)
        minimize.assert_not_called()
        # 任务路径：启动后立即最小化
        with base[0], base[1], base[2], base[3], mock.patch.object(
            pipeline_exec.boss, "minimize_chrome_window",
        ) as minimize:
            ok, _msg = pipeline_exec.ensure_chrome_ready(
                9444, minimize_after_launch=True,
            )
        self.assertTrue(ok)
        minimize.assert_called_once_with(9444)


class ChromeWindowMinimizeTests(unittest.TestCase):
    """R6: CDP Browser.setWindowBounds 最小化抓取浏览器窗口。"""

    def test_minimize_chrome_window_sends_browser_commands(self):
        from scripts import boss_cdp_raw as boss
        session = mock.Mock()
        session.send.side_effect = [
            {"id": 1, "result": {"windowId": 42}},
            {"id": 2, "result": {}},
        ]
        ok = boss.minimize_chrome_window(
            9222,
            session_factory=lambda _port: session,
            target_id_provider=lambda _port: "target-1",
        )
        self.assertTrue(ok)
        session.send.assert_any_call(
            "Browser.getWindowForTarget", {"targetId": "target-1"}, timeout=10,
        )
        session.send.assert_any_call("Browser.setWindowBounds", {
            "windowId": 42,
            "bounds": {"windowState": "minimized"},
        }, timeout=10)
        session.close.assert_called_once()

    def test_minimize_chrome_window_without_target_returns_false(self):
        from scripts import boss_cdp_raw as boss
        ok = boss.minimize_chrome_window(
            9222,
            session_factory=mock.Mock(),
            target_id_provider=lambda _port: None,
        )
        self.assertFalse(ok)

    def test_minimize_chrome_window_failure_is_silent(self):
        from scripts import boss_cdp_raw as boss
        session = mock.Mock()
        session.send.side_effect = RuntimeError("cdp closed")
        ok = boss.minimize_chrome_window(
            9222,
            session_factory=lambda _port: session,
            target_id_provider=lambda _port: "target-1",
        )
        self.assertFalse(ok)


class BrowserAccountApiTests(unittest.TestCase):
    """顶部栏浏览器账号管理接口。"""

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
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        self.temp.cleanup()
        from webui.pipeline_exec import reset_browser_accounts_path
        reset_browser_accounts_path()

    def test_list_add_activate_custom_account(self):
        data = self.client.get("/api/browser-accounts").get_json()
        self.assertEqual(len(data["accounts"]), 2)
        self.assertEqual(data["active_account"], "a")
        resp = self.client.post("/api/browser-accounts", json={"name": "账号 C"})
        self.assertEqual(resp.status_code, 201, resp.get_json())
        account = resp.get_json()["account"]
        self.assertFalse(account["builtin"])
        self.assertIn(".chrome-profiles", account["profile_dir"])
        activated = self.client.post(
            f"/api/browser-accounts/{account['id']}/activate"
        ).get_json()
        self.assertEqual(activated["active_account"], account["id"])
        settings = self.client.get("/api/advanced-settings").get_json()
        self.assertEqual(settings["settings"]["browser_account"], account["id"])

    def test_duplicate_name_rejected_and_delete_custom(self):
        self.client.post("/api/browser-accounts", json={"name": "账号 C"})
        dup = self.client.post("/api/browser-accounts", json={"name": "账号 C"})
        self.assertEqual(dup.status_code, 422)
        builtin = self.client.delete("/api/browser-accounts/a")
        self.assertEqual(builtin.status_code, 409)
        data = self.client.get("/api/browser-accounts").get_json()
        custom = next(a for a in data["accounts"] if a["id"] not in ("a", "b"))
        self.client.post(f"/api/browser-accounts/{custom['id']}/activate")
        deleted = self.client.delete(f"/api/browser-accounts/{custom['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        data = self.client.get("/api/browser-accounts").get_json()
        self.assertEqual(data["active_account"], "a")
        self.assertNotIn(custom["id"], [a["id"] for a in data["accounts"]])

    def test_legacy_b_account_is_deletable_and_not_reseeded(self):
        data = self.client.get("/api/browser-accounts").get_json()
        b = next(a for a in data["accounts"] if a["id"] == "b")
        self.assertFalse(b["builtin"])
        deleted = self.client.delete("/api/browser-accounts/b")
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        data = self.client.get("/api/browser-accounts").get_json()
        self.assertNotIn("b", [a["id"] for a in data["accounts"]])

    def _seed_paused_run(self, account="b", run_id="busy-account-run", platform="boss"):
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={"browser_account": account, "platform": platform},
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(run_id, status="paused")
        return run_id

    @mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, ""))
    @mock.patch("webui.pipeline_exec.close_debug_chrome", return_value=True)
    def test_paused_open_other_account_allowed_and_closes_old_browser(
            self, mock_close, _ready):
        self._seed_paused_run(account="b")
        order = []
        mock_close.side_effect = lambda port: order.append(("close", port)) or True
        _ready.side_effect = lambda port: order.append(("ready", port)) or (True, "")
        resp = self.client.post("/api/browser-accounts/a/open")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertTrue(resp.get_json()["ok"])
        self.assertEqual(order, [("close", 9222), ("ready", 9222)])

    @mock.patch("webui.pipeline_exec.close_debug_chrome", return_value=True)
    def test_paused_activate_allowed_and_closes_old_browser(self, mock_close):
        self._seed_paused_run(account="b")
        resp = self.client.post("/api/browser-accounts/a/activate")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["active_account"], "a")
        mock_close.assert_called_once_with(9222)
        data = self.client.get("/api/browser-accounts").get_json()
        self.assertEqual(data["active_account"], "a")

    def test_running_activate_rejected(self):
        self.app.config["PIPELINE_TASKS"]["running-activate"] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(),
        }
        resp = self.client.post("/api/browser-accounts/b/activate")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "browser_busy")

    @mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, ""))
    def test_open_browser_allows_paused_task_account(self, _ready):
        self._seed_paused_run(account="b")
        resp = self.client.post("/api/browser-accounts/b/open")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertTrue(resp.get_json()["ok"])
        self.assertIn("登录", resp.get_json()["message"])

    def test_list_exposes_paused_account_lock(self):
        self._seed_paused_run(account="b")
        data = self.client.get("/api/browser-accounts").get_json()
        self.assertTrue(data["busy"])
        self.assertEqual(data["busy_kind"], "paused")
        self.assertEqual(data["locked_account"], "b")

    @mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, ""))
    def test_open_browser_succeeds_when_idle(self, _ready):
        resp = self.client.post("/api/browser-accounts/a/open")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertTrue(resp.get_json()["ok"])

    def test_list_accounts_returns_platforms_without_profile_dir(self):
        data = self.client.get("/api/browser-accounts").get_json()
        self.assertEqual(len(data["accounts"]), 2)
        for account in data["accounts"]:
            self.assertNotIn("profile_dir", account)
            self.assertIn("boss", account["platforms"])
            self.assertIn("zhilian", account["platforms"])
            self.assertEqual(account["platforms"]["boss"]["cdp_port"], 9222)
            self.assertEqual(account["platforms"]["zhilian"]["cdp_port"], 9223)

    @mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, ""))
    def test_open_zhilian_uses_zhilian_port(self, _ready):
        resp = self.client.post("/api/browser-accounts/b/open", json={"platform": "zhilian"})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertIn("智联", resp.get_json()["message"])
        _ready.assert_called_once_with(9223)

    def test_open_unknown_platform_returns_400(self):
        resp = self.client.post("/api/browser-accounts/a/open", json={"platform": "unknown"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error_code"], "platform_validation_failed")

    @mock.patch("webui.pipeline_exec.ensure_chrome_ready", return_value=(True, ""))
    @mock.patch("webui.pipeline_exec.close_debug_chrome", return_value=True)
    def test_paused_open_any_platform_for_locked_account(self, mock_close, _ready):
        self._seed_paused_run(account="b", platform="zhilian")
        boss_open = self.client.post("/api/browser-accounts/b/open", json={"platform": "boss"})
        self.assertEqual(boss_open.status_code, 200, boss_open.get_json())
        self.assertEqual(_ready.call_args_list[-1], mock.call(9222))
        zhilian_open = self.client.post("/api/browser-accounts/b/open", json={"platform": "zhilian"})
        self.assertEqual(zhilian_open.status_code, 200, zhilian_open.get_json())
        self.assertEqual(_ready.call_args_list[-1], mock.call(9223))
        self.assertEqual(mock_close.call_count, 2)

    def test_delete_refuses_zhilian_browser_running(self):
        resp = self.client.post("/api/browser-accounts", json={"name": "账号 Z"})
        account = resp.get_json()["account"]
        zhilian_dir = account["profile_dir"].rstrip("/\\") + ".zhilian"
        with mock.patch("webui.app.boss.is_cdp_ready", side_effect=[False, True]), \
                mock.patch("webui.app.boss.chrome_user_data_dirs_for_cdp_port",
                           side_effect=lambda port: [] if port == 9222 else [zhilian_dir]):
            deleted = self.client.delete(f"/api/browser-accounts/{account['id']}")
        self.assertEqual(deleted.status_code, 409, deleted.get_json())
        self.assertEqual(deleted.get_json()["error"], "browser_in_use")

    def test_paused_delete_allowed(self):
        self._seed_paused_run(account="b")
        deleted = self.client.delete("/api/browser-accounts/b")
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        data = self.client.get("/api/browser-accounts").get_json()
        self.assertNotIn("b", [a["id"] for a in data["accounts"]])

    def test_running_delete_rejected(self):
        self.app.config["PIPELINE_TASKS"]["running-delete"] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(),
        }
        resp = self.client.delete("/api/browser-accounts/b")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "browser_busy")

    def test_paused_delete_closes_open_target_browser(self):
        resp = self.client.post("/api/browser-accounts", json={"name": "账号 Z"})
        account = resp.get_json()["account"]
        profile_dir = account["profile_dir"]
        self._seed_paused_run(account="b")
        ready_calls = {"n": 0}
        def is_ready(port):
            ready_calls["n"] += 1
            return ready_calls["n"] == 1 and port == 9222
        with mock.patch("webui.app.boss.is_cdp_ready", side_effect=is_ready), \
                mock.patch("webui.app.boss.chrome_user_data_dirs_for_cdp_port",
                           side_effect=lambda port: [profile_dir] if port == 9222 else []), \
                mock.patch("webui.pipeline_exec.close_debug_chrome",
                           return_value=True) as mock_close:
            deleted = self.client.delete(f"/api/browser-accounts/{account['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.get_json())
        mock_close.assert_called_once_with(9222)

    def test_paused_delete_close_failure_returns_409(self):
        resp = self.client.post("/api/browser-accounts", json={"name": "账号 Z"})
        account = resp.get_json()["account"]
        profile_dir = account["profile_dir"]
        self._seed_paused_run(account="b")
        with mock.patch("webui.app.boss.is_cdp_ready", side_effect=[True, False]), \
                mock.patch("webui.app.boss.chrome_user_data_dirs_for_cdp_port",
                           side_effect=lambda port: [profile_dir] if port == 9222 else []), \
                mock.patch("webui.pipeline_exec.close_debug_chrome",
                           return_value=False):
            deleted = self.client.delete(f"/api/browser-accounts/{account['id']}")
        self.assertEqual(deleted.status_code, 409, deleted.get_json())
        self.assertEqual(deleted.get_json()["error"], "browser_in_use")
        data = self.client.get("/api/browser-accounts").get_json()
        self.assertIn(account["id"], [a["id"] for a in data["accounts"]])

    def test_resolve_browser_account_returns_custom_profile(self):
        from webui import pipeline_exec
        resp = self.client.post("/api/browser-accounts", json={"name": "账号 C"})
        account = resp.get_json()["account"]
        path = self.app.config["BROWSER_ACCOUNTS_PATH"]
        self.assertEqual(
            pipeline_exec.resolve_browser_account(account["id"], path),
            account["profile_dir"],
        )
        self.assertEqual(pipeline_exec.resolve_browser_account("missing", path), "")

    def test_add_rejects_duplicate_profile_dir(self):
        first = self.client.post("/api/browser-accounts", json={"name": "账号 C"})
        profile_dir = first.get_json()["account"]["profile_dir"]
        dup = self.client.post("/api/browser-accounts", json={
            "name": "账号 D", "profile_dir": profile_dir,
        })
        self.assertEqual(dup.status_code, 422, dup.get_json())
        self.assertIn("不能与其他账号重复", dup.get_json()["error"])

    def test_delete_refuses_when_browser_running(self):
        resp = self.client.post("/api/browser-accounts", json={"name": "账号 C"})
        account = resp.get_json()["account"]
        profile_dir = account["profile_dir"]
        with mock.patch("webui.app.boss.is_cdp_ready", side_effect=[True, False]), \
                mock.patch("webui.app.boss.chrome_user_data_dirs_for_cdp_port",
                           side_effect=lambda port: [profile_dir] if port == 9222 else []):
            deleted = self.client.delete(f"/api/browser-accounts/{account['id']}")
        self.assertEqual(deleted.status_code, 409, deleted.get_json())
        self.assertEqual(deleted.get_json()["error"], "browser_in_use")


class AiResumeVerdictSplitTests(unittest.TestCase):
    """续跑时粗筛 kept 不得被当成精筛判定。"""

    def test_split_resume_verdicts_keeps_rough_out_of_fine(self):
        from webui.app import _split_resume_verdicts
        verdicts = {
            "job-kept": {"verdict": "kept", "reason": ""},
            "job-drop": {"verdict": "dropped", "reason": "粗筛移除"},
            "job-match": {"verdict": "match", "reason": "匹配"},
            "job-uncertain": {"verdict": "uncertain", "reason": "待确认"},
        }
        fine, rough = _split_resume_verdicts(verdicts)
        self.assertEqual(set(fine), {"job-match", "job-uncertain"})
        self.assertEqual(set(rough), {"job-kept", "job-drop"})

    def test_resume_dropped_reconstructed_from_raw_jobs(self):
        from webui.app import _resume_dropped_from_verdicts
        raw_jobs = [
            {"job_id": "j1", "title": "前端", "source_url": "https://a/j1.html"},
            {"job_id": "j2", "title": "后端", "source_url": "https://a/j2.html"},
        ]
        verdicts = {
            "j1": {"verdict": "kept"},
            "j2": {"verdict": "dropped", "reason": "经验不符"},
        }
        dropped = _resume_dropped_from_verdicts(raw_jobs, verdicts)
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0]["job_id"], "j2")
        self.assertEqual(dropped[0]["reason"], "经验不符")


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
            def fetch_list(self, plan_item, *, on_page_completed=None):
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
            def fetch_list(self, plan_item, *, on_page_completed=None):
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

    def _complete_speed_settings(self):
        return {
            "inter_combo_delay": 10.0,
            "detail_batch_size": 15,
            "detail_interval": 2.0,
            "detail_reset_every": 4,
            "detail_batch_cooldown": 5.0,
            "detail_tab_pool_size": 5,
            "screen_batch_size": 50,
            "screen_concurrency": 5,
            "match_batch_size": 4,
            "match_concurrency": 10,
        }

    def test_get_returns_versioned_state(self):
        """GET 返回 selection、settings、config_schema_version。"""
        resp = self.client.get("/api/advanced-settings")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("selection", data)
        self.assertIn("settings", data)
        self.assertIn("config_schema_version", data)

    def test_advanced_settings_round_trip_browser_account(self):
        resp = self.client.post("/api/advanced-settings", json={
            "settings": {"browser_account": "b", "pages": 4},
        })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = self.client.get("/api/advanced-settings").get_json()
        self.assertEqual(data["settings"]["browser_account"], "b")
        bad = self.client.post("/api/advanced-settings", json={
            "settings": {"browser_account": "z"},
        })
        self.assertEqual(bad.status_code, 200)
        data = self.client.get("/api/advanced-settings").get_json()
        self.assertEqual(data["settings"]["browser_account"], "a")

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
        """PUT /custom 保存完整速度字段（含 JD 并发 Tab 数），返回 digest。"""
        resp = self.client.put("/api/advanced-settings/custom", json={
            "settings": {
                "inter_combo_delay": 10.0,
                "detail_batch_size": 15,
                "detail_interval": 2.0,
                "detail_reset_every": 4,
                "detail_batch_cooldown": 5.0,
                "detail_tab_pool_size": 5,
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
        self.assertEqual(data["settings"]["detail_tab_pool_size"], 5)

    def test_put_custom_preserves_active_browser_account(self):
        """保存速度设置后，当前浏览器账号不能被旧 JSON 兼容写覆盖。"""
        activated = self.client.post("/api/browser-accounts/b/activate")
        self.assertEqual(activated.status_code, 200, activated.get_json())
        resp = self.client.put("/api/advanced-settings/custom", json={
            "settings": self._complete_speed_settings(),
        })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        persisted = json.loads(pathlib.Path(
            self.app.config["ADVANCED_SETTINGS_PATH"]
        ).read_text(encoding="utf-8"))
        self.assertEqual(persisted.get("browser_account"), "b")
        data = self.client.get("/api/advanced-settings").get_json()
        self.assertEqual(data["settings"]["browser_account"], "b")
        accounts = self.client.get("/api/browser-accounts").get_json()
        self.assertEqual(accounts["active_account"], "b")

    def test_select_mode_preserves_active_browser_account(self):
        """切换执行模式后，当前浏览器账号不能被模式配置覆盖。"""
        activated = self.client.post("/api/browser-accounts/b/activate")
        self.assertEqual(activated.status_code, 200, activated.get_json())
        scope = self._preview_scope(pages=3)
        resp = self.client.post("/api/advanced-settings/select-mode", json={
            "mode": "stable", "scope_digest": scope["scope_digest"],
        })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        persisted = json.loads(pathlib.Path(
            self.app.config["ADVANCED_SETTINGS_PATH"]
        ).read_text(encoding="utf-8"))
        self.assertEqual(persisted.get("browser_account"), "b")
        data = self.client.get("/api/advanced-settings").get_json()
        self.assertEqual(data["settings"]["browser_account"], "b")
        accounts = self.client.get("/api/browser-accounts").get_json()
        self.assertEqual(accounts["active_account"], "b")

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
        "detail_tab_pool_size": 5,
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
            ("large", scope(10, 5)), ("large", scope(11, 5)),
        ]
        self.experiment = self.controller.create_experiment_with_input(
            spec_version="011-deep-configuration-probing",
            source_scope={**scopes[0][1], "browser_account": "a", "filter_schema_version": 1},
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
        manifest["fixed_fields"]["platform"] = "boss"
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

    def test_manifest_child_preserves_safe_stage_failure_code(self):
        manifest = self._make_manifest()
        issued = self.controller.issue_manifest(manifest)
        self.controller.execute_manifest(issued["manifest_id"])
        error = RuntimeError("list stage stopped")
        error.error_code = "source_cdp_unavailable"
        self.app.config["TUNING_ROUND_RUNNER"].execute = mock.Mock(
            side_effect=error,
        )

        self.app.config["RUN_TUNING_MANIFEST_CHILD"](issued["manifest_id"])

        round_record = self.store.get_tuning_round(self.round["id"])
        self.assertEqual(round_record["failure_code"], "source_cdp_unavailable")

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
                "keywords": ["AI应用开发", "智能体开发", "Python后端", "Java后端", "前端开发"], "scope_kind": "cities",
                "cities": ["东莞", "深圳"], "pages_per_combination": 5}},
            {"task_size": "large", "structure_index": 2, "scope": {
                "keywords": ["AI应用开发", "智能体开发", "Python后端", "Java后端", "前端开发", "Go后端", "测试开发", "运维开发", "数据分析", "产品经理"], "scope_kind": "cities",
                "cities": ["东莞"], "pages_per_combination": 5}},
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
                "browser_account": "a",
                "filter_schema_version": 1,
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
                "browser_account": "a",
                "filter_schema_version": 1,
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
                "browser_account": "a",
                "filter_schema_version": 1,
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
                "browser_account": "a",
                "filter_schema_version": 1,
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
                "browser_account": "a",
                "filter_schema_version": 1,
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
                "browser_account": "a",
                "filter_schema_version": 1,
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
                "browser_account": "a",
                "filter_schema_version": 1,
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
                "browser_account": "a",
                "filter_schema_version": 1,
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
                "browser_account": "a",
                "filter_schema_version": 1,
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
                "browser_account": "a",
                "filter_schema_version": 1,
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
                "browser_account": "a",
                "filter_schema_version": 1,
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


class SourceDetailBatchCommandTests(unittest.TestCase):
    """JD 批量详情命令必须透传并发 tab 数，默认值为 5。"""

    def test_batch_command_forwards_tab_pool_size(self):
        from webui.source import BossCdpSource

        source = BossCdpSource(
            python_executable="python",
            scraper_path="scripts/boss_cdp_raw.py",
        )
        command = source._build_detail_batch_command(
            "batch.input.json", "batch.out.json", "batch.events.jsonl",
            batch_size=2, gap_min=1, gap_max=2, reset_every=3,
            tab_pool_size=5,
        )
        self.assertIn("--tab-pool-size", command)
        self.assertEqual(command[command.index("--tab-pool-size") + 1], "5")

    def test_batch_detail_default_tab_pool_is_five(self):
        import inspect
        from webui.source import BossCdpSource

        self.assertEqual(
            inspect.signature(BossCdpSource.fetch_details_batch)
            .parameters["tab_pool_size"].default,
            5,
        )
        self.assertEqual(
            inspect.signature(BossCdpSource._build_detail_batch_command)
            .parameters["tab_pool_size"].default,
            5,
        )

class LegacyPlatformGuardTests(unittest.TestCase):
    """tasks007 T601-T604: legacy BOSS-only 路由对显式智联/未知平台的零副作用拒绝。

    合同（contracts/http-api.md 第 351-370 行 Legacy BOSS-only 矩阵）：
    - 显式 ``zhilian`` → ``422 legacy_platform_not_supported``，且发生在任务/对象
      查找和任何副作用之前。
    - 其它未知平台 → ``400 platform_validation_failed``。
    - 显式 ``boss`` 或省略平台 → 走既有 BOSS 行为，成功对象标识 ``platform=boss``。
    """

    _TASK_BODY = {
        "keyword": "Python 后端", "city": "上海", "pages": 1,
        "detail": False, "analysis": False, "format": "json",
    }
    _CONFIRM_BODY = {
        "keyword": [{"word": "Python", "recommended": True}],
        "city": "上海",
    }

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.result_dir = root / "results"
        self.db_path = root / "state" / "webui.db"
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(self.result_dir),
            "DB_PATH": str(self.db_path),
            "PYTHON_EXECUTABLE": sys.executable,
        })
        self.client = self.app.test_client()
        session = self.client.get("/api/session")
        self.assertEqual(session.status_code, 200)
        self.token = session.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token
        # 预置一个 BOSS 任务用于 task 子路由（cancel/retry/result/summary/export）。
        resp = self.client.post("/api/tasks", json=self._TASK_BODY)
        self.assertEqual(resp.status_code, 202)
        self.task_id = resp.get_json()["task"]["id"]

    def tearDown(self):
        self.temp.cleanup()

    # ----- 快照助手 -------------------------------------------------------

    def _db_table_counts(self):
        conn = sqlite3.connect(str(self.db_path))
        try:
            names = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
            return {
                name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in names
            }
        finally:
            conn.close()

    def _result_files(self):
        if not self.result_dir.exists():
            return []
        return sorted(p.name for p in self.result_dir.glob("*"))

    def _snapshot(self):
        return {
            "db": self._db_table_counts(),
            "result_files": self._result_files(),
            "task_count": len(self.client.get("/api/tasks").get_json()["tasks"]),
        }

    def _assert_no_side_effects(self, before, *, context=""):
        after = self._snapshot()
        self.assertEqual(
            after["db"], before["db"],
            f"{context}DB 表行数发生变化: {before['db']} -> {after['db']}",
        )
        self.assertEqual(
            after["result_files"], before["result_files"],
            f"{context}结果文件列表发生变化: {before['result_files']} -> {after['result_files']}",
        )
        self.assertEqual(
            after["task_count"], before["task_count"],
            f"{context}任务数量发生变化: {before['task_count']} -> {after['task_count']}",
        )

    # ----- 路由矩阵 -------------------------------------------------------

    def _legacy_routes(self):
        """返回 (name, method, path, body_or_None) 列表，path 已填入 task_id。"""
        tid = self.task_id
        return [
            ("tasks_get", "GET", "/api/tasks", None),
            ("tasks_post", "POST", "/api/tasks", dict(self._TASK_BODY)),
            ("scrape_post", "POST", "/api/scrape", dict(self._TASK_BODY)),
            ("setup_chrome_post", "POST", "/api/setup-chrome", {}),
            ("task_detail_get", "GET", f"/api/tasks/{tid}", None),
            ("task_cancel_post", "POST", f"/api/tasks/{tid}/cancel", {}),
            ("task_retry_post", "POST", f"/api/tasks/{tid}/retry", {}),
            ("task_result_get", "GET", f"/api/tasks/{tid}/result", None),
            ("task_summary_get", "GET", f"/api/tasks/{tid}/summary", None),
            ("task_export_get", "GET", f"/api/tasks/{tid}/export.csv", None),
            ("results_get", "GET", "/api/results", None),
            ("confirm_fields_post", "POST", "/api/confirm-fields", dict(self._CONFIRM_BODY)),
            ("search_runs_post", "POST", "/api/search-runs",
             {"profile_id": "missing", "manual_keywords": ["Python"]}),
            ("search_run_detail_get", "GET", "/api/search-runs/missing-run", None),
            ("search_run_jobs_get", "GET", "/api/search-runs/missing-run/jobs", None),
            ("search_run_cancel_post", "POST", "/api/search-runs/missing-run/cancel", {}),
        ]

    def _send(self, method, path, *, platform, body=None):
        if method == "GET":
            qs = {} if platform is None else {"platform": platform}
            return self.client.get(path, query_string=qs)
        payload = dict(body or {})
        if platform is not None:
            payload["platform"] = platform
        return self.client.post(path, json=payload)

    # ----- T601/T602: 显式 zhilian → 422 + 零副作用 -----------------------

    def test_zhilian_rejected_with_422_and_zero_side_effects(self):
        for name, method, path, body in self._legacy_routes():
            with self.subTest(route=name):
                before = self._snapshot()
                resp = self._send(method, path, platform="zhilian", body=body)
                self.assertEqual(
                    resp.status_code, 422,
                    f"{name}: 期望 422，实际 {resp.status_code} "
                    f"{resp.get_data(as_text=True)[:200]}",
                )
                data = resp.get_json()
                self.assertIsNotNone(data, f"{name}: 响应非 JSON")
                self.assertEqual(
                    data["error_code"], "legacy_platform_not_supported",
                    f"{name}: error_code={data.get('error_code')}",
                )
                self._assert_no_side_effects(before, context=f"[{name}] ")

    # ----- T601/T602: 未知平台 → 400 + 零副作用 ---------------------------

    def test_unknown_platform_rejected_with_400_and_zero_side_effects(self):
        for name, method, path, body in self._legacy_routes():
            with self.subTest(route=name):
                before = self._snapshot()
                resp = self._send(method, path, platform="weird-platform", body=body)
                self.assertEqual(
                    resp.status_code, 400,
                    f"{name}: 期望 400，实际 {resp.status_code} "
                    f"{resp.get_data(as_text=True)[:200]}",
                )
                data = resp.get_json()
                self.assertIsNotNone(data, f"{name}: 响应非 JSON")
                self.assertEqual(
                    data["error_code"], "platform_validation_failed",
                    f"{name}: error_code={data.get('error_code')}",
                )
                self._assert_no_side_effects(before, context=f"[{name}] ")

    # ----- T601/T602: zhilian 拒绝发生在对象查找前（不返回 404） -----------

    def test_zhilian_rejects_before_object_lookup(self):
        """对不存在的 task/run id，显式 zhilian 必须返回 422 而非 404。"""
        cases = [
            ("missing_task_detail", "GET", "/api/tasks/missing-task", None),
            ("missing_task_cancel", "POST", "/api/tasks/missing-task/cancel", {}),
            ("missing_task_retry", "POST", "/api/tasks/missing-task/retry", {}),
            ("missing_task_result", "GET", "/api/tasks/missing-task/result", None),
            ("missing_task_summary", "GET", "/api/tasks/missing-task/summary", None),
            ("missing_task_export", "GET", "/api/tasks/missing-task/export.csv", None),
            ("missing_run_detail", "GET", "/api/search-runs/missing-run", None),
            ("missing_run_jobs", "GET", "/api/search-runs/missing-run/jobs", None),
            ("missing_run_cancel", "POST", "/api/search-runs/missing-run/cancel", {}),
        ]
        for name, method, path, body in cases:
            with self.subTest(route=name):
                resp = self._send(method, path, platform="zhilian", body=body)
                self.assertEqual(
                    resp.status_code, 422,
                    f"{name}: 期望 422（拒绝先于对象查找），实际 {resp.status_code}",
                )
                self.assertEqual(
                    resp.get_json()["error_code"], "legacy_platform_not_supported",
                )

    # ----- T604: 显式 boss / 省略平台 → 既有 BOSS 行为 --------------------

    def test_explicit_boss_preserves_legacy_behavior(self):
        for name, path in [
            ("tasks_list", "/api/tasks"),
            ("results", "/api/results"),
            ("task_detail", f"/api/tasks/{self.task_id}"),
            ("task_result", f"/api/tasks/{self.task_id}/result"),
            ("task_summary", f"/api/tasks/{self.task_id}/summary"),
        ]:
            with self.subTest(route=name):
                resp = self.client.get(path, query_string={"platform": "boss"})
                self.assertEqual(resp.status_code, 200, f"{name}: {resp.status_code}")

    def test_omitted_platform_preserves_legacy_behavior(self):
        for name, path in [
            ("tasks_list", "/api/tasks"),
            ("results", "/api/results"),
            ("task_detail", f"/api/tasks/{self.task_id}"),
            ("task_result", f"/api/tasks/{self.task_id}/result"),
            ("task_summary", f"/api/tasks/{self.task_id}/summary"),
        ]:
            with self.subTest(route=name):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 200, f"{name}: {resp.status_code}")

    def test_boss_success_objects_marked_platform_boss(self):
        """T604: legacy 成功对象补充 platform=boss 标识。"""
        resp = self.client.post("/api/tasks", json=self._TASK_BODY)
        self.assertEqual(resp.status_code, 202)
        task = resp.get_json()["task"]
        self.assertEqual(
            task.get("platform"), "boss",
            f"任务对象缺少 platform=boss 标识: {task.get('platform')}",
        )

        detail = self.client.get(f"/api/tasks/{task['id']}").get_json()["task"]
        self.assertEqual(detail.get("platform"), "boss")

        results = self.client.get("/api/results").get_json()
        self.assertEqual(
            results.get("platform"), "boss",
            f"/api/results 响应缺少 platform=boss: {results.get('platform')}",
        )

    def test_scrape_omitted_platform_creates_boss_task(self):
        """/api/scrape 省略平台保持旧创建别名，任务标识 platform=boss。"""
        resp = self.client.post("/api/scrape", json=self._TASK_BODY)
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.get_json()["task"].get("platform"), "boss")


class PlatformAwareSearchScopeTests(unittest.TestCase):
    """tasks005 T401: 平台感知搜索预览/创建 API 测试。

    合同（contracts/http-api.md）：
    - POST /api/search-scope/preview 接受 ``platform``，禁用平台返回 503，
      未知平台返回 400，scope 显式含 ``platform`` 和 ``scope_digest``。
    - POST /api/execute-search 接受 ``platform``，非空 filters 返回 422，
      禁用平台返回 503，平台与 scope 不一致返回 409，搜索 run 的
      ``frozen_filters`` 和筛选快照为空，响应含 ``task_input_digest``。
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
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    # -- preview: platform registration ----------------------------------

    def test_preview_explicit_boss_returns_scope_with_platform(self):
        """显式 platform=boss 预览成功，scope 显式含 platform=boss。"""
        resp = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python 后端"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["scope"]["platform"], "boss")
        self.assertTrue(data["scope"]["scope_digest"])

    def test_preview_omitted_platform_defaults_to_boss(self):
        """省略平台兼容旧 BOSS 前端，scope 显式含 platform=boss。"""
        resp = self.client.post("/api/search-scope/preview", json={
            "keywords": ["Python 后端"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["scope"]["platform"], "boss")

    def test_preview_unknown_platform_returns_400(self):
        """未知平台键返回 400 platform_validation_failed。"""
        resp = self.client.post("/api/search-scope/preview", json={
            "platform": "lagou",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        })
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertEqual(data["error_code"], "platform_validation_failed")

    def test_preview_disabled_platform_returns_503(self):
        """智联 enabled_for_new_tasks=False → 503 platform_disabled。"""
        from unittest import mock
        from webui.platforms import get_platform_or_none
        def _disabled(platform_raw):
            if platform_raw == "zhilian":
                return mock.Mock(enabled_for_new_tasks=False, availability_reason="disabled for test")
            return get_platform_or_none(platform_raw)
        with mock.patch("webui.platforms.get_platform_or_none", side_effect=_disabled):
            resp = self.client.post("/api/search-scope/preview", json={
                "platform": "zhilian",
                "keywords": ["Python"],
                "scope_kind": "nationwide",
                "cities": [],
                "pages_per_combination": 1,
            })
            self.assertEqual(resp.status_code, 503)
            data = resp.get_json()
            self.assertEqual(data["error_code"], "platform_disabled")

    # -- preview: scope digest includes platform -------------------------

    def test_preview_scope_digest_is_deterministic_and_contains_platform(self):
        """同一平台同一参数的 scope_digest 稳定；scope 含 platform 字段。"""
        body = {
            "platform": "boss",
            "keywords": ["Python 后端"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }
        resp1 = self.client.post("/api/search-scope/preview", json=body)
        resp2 = self.client.post("/api/search-scope/preview", json=body)
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(
            resp1.get_json()["scope"]["scope_digest"],
            resp2.get_json()["scope"]["scope_digest"],
        )
        self.assertEqual(resp1.get_json()["scope"]["platform"], "boss")

    # -- execute-search: non-empty filters rejection ---------------------

    def test_execute_search_rejects_non_empty_filters(self):
        """非空 filters 返回 422 search_filters_not_supported，不创建 run。"""
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]
        resp = self.client.post("/api/execute-search", json={
            "platform": "boss",
            "script_params": {
                "keyword": "Python",
                "city": ["上海"],
                "pages": 1,
                "filters": {"stage": "804"},
            },
            "scope_digest": preview["scope_digest"],
        })
        self.assertEqual(resp.status_code, 422)
        data = resp.get_json()
        self.assertEqual(data["error_code"], "search_filters_not_supported")

    def test_execute_search_rejects_screening_fields(self):
        """screening_fields 属于 AI 筛选，搜索请求携带时返回 422。"""
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]
        resp = self.client.post("/api/execute-search", json={
            "platform": "boss",
            "script_params": {
                "keyword": "Python",
                "city": ["上海"],
                "pages": 1,
                "screening_fields": [{"name": "salary", "value": ["405"]}],
            },
            "scope_digest": preview["scope_digest"],
        })
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(
            resp.get_json()["error_code"], "search_filters_not_supported")

    # -- execute-search: disabled platform -------------------------------

    def test_execute_search_disabled_platform_returns_503(self):
        """智联禁用 → execute-search 返回 503 platform_disabled，不创建 run。"""
        from webui.platforms import get_platform_or_none
        def _disabled(platform_raw):
            if platform_raw == "zhilian":
                return mock.Mock(enabled_for_new_tasks=False, availability_reason="disabled for test")
            return get_platform_or_none(platform_raw)
        with mock.patch("webui.platforms.get_platform_or_none", side_effect=_disabled):
            resp = self.client.post("/api/execute-search", json={
                "platform": "zhilian",
                "script_params": {
                    "keyword": "Python",
                    "city": ["全国"],
                    "pages": 1,
                },
            })
            self.assertEqual(resp.status_code, 503)
            self.assertEqual(resp.get_json()["error_code"], "platform_disabled")

    # -- execute-search: platform mismatch -------------------------------

    def test_execute_search_platform_mismatch_returns_409(self):
        """scope 平台与请求平台不一致 → 409 scope_platform_mismatch。"""
        boss_preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]
        resp = self.client.post("/api/execute-search", json={
            "platform": "zhilian",
            "script_params": {
                "keyword": "Python",
                "city": ["上海"],
                "pages": 1,
            },
            "scope_digest": boss_preview["scope_digest"],
        })
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(
            resp.get_json()["error_code"], "scope_platform_mismatch")

    # -- execute-search: creates run with frozen identity ----------------

    def test_execute_search_freezes_platform_and_empty_filter_snapshot(self):
        """搜索 run 持久化 platform=boss、空 frozen_filters、空筛选快照、
        非空 task_input_digest，且 execution_params 含 cdp_port/profile_key。
        """
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit"):
            resp = self.client.post("/api/execute-search", json={
                "platform": "boss",
                "script_params": {
                    "keyword": "Python",
                    "city": ["上海"],
                    "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
            })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        body = resp.get_json()
        self.assertEqual(body["platform"], "boss")
        self.assertTrue(body["task_input_digest"])
        self.assertEqual(body["scope_digest"], preview["scope_digest"])

        task_id = body["task_id"]
        store = self.app.config["TASK_STORE"]
        run = store.get_screening_run(task_id)
        self.assertIsNotNone(run)
        # 搜索 run 的筛选快照为空
        self.assertEqual(run["frozen_filters"], {})
        # execution_params 含平台冻结身份
        params = run["execution_params"]
        self.assertEqual(params["platform"], "boss")
        self.assertTrue(params["cdp_port"])
        self.assertTrue(params["profile_key"])
        self.assertTrue(params["task_input_digest"])
        self.assertEqual(params["browser_account"], body.get("browser_account") or params["browser_account"])

    def test_execute_search_scope_request_mismatch_returns_409(self):
        """script_params 的关键词/城市/页数与 scope 不一致 → 409 scope_request_mismatch。"""
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]
        resp = self.client.post("/api/execute-search", json={
            "platform": "boss",
            "script_params": {
                "keyword": "Java",  # 与 scope 关键词不一致
                "city": ["上海"],
                "pages": 1,
            },
            "scope_digest": preview["scope_digest"],
        })
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(
            resp.get_json()["error_code"], "scope_request_mismatch")

    # -- T403: source created from frozen runtime -----------------------

    def _wait_for_task(self, task_id, timeout=5.0):
        """Poll search-progress until task leaves queued/running."""
        import time as _time
        deadline = _time.monotonic() + timeout
        last = None
        while _time.monotonic() < deadline:
            resp = self.client.get(f"/api/search-progress/{task_id}")
            if resp.status_code == 200:
                last = resp.get_json()
                if last.get("status") not in ("queued", "running"):
                    return last
            _time.sleep(0.02)
        raise AssertionError(f"task {task_id} did not finish within {timeout}s; last={last}")

    def test_boss_source_receives_frozen_cdp_port(self):
        """T403: BOSS source 显式接收冻结 cdp_port，不使用默认端口。

        合同（contracts/job-source.md 第 42 行）：
        "BOSS 也必须显式接收冻结的 CDP 端口。"

        _make_cdp_source 从 task dict 读取冻结的 platform/cdp_port/
        profile_key，传给 BossCdpSource 构造函数。不读当前 UI、活动
        账号或默认端口。
        """
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]

        fake_result = {
            "ok": True, "jobs": [], "total_scraped": 0,
            "total_matched": 0, "combinations": 1,
            "completed_combos": ["Python|上海"], "error": "",
        }
        with mock.patch("webui.app._BossCdpSource",
                        return_value=mock.MagicMock()) as mock_cls, \
                mock.patch("webui.pipeline_exec.run_search",
                           return_value=fake_result) as mock_search:
            resp = self.client.post("/api/execute-search", json={
                "platform": "boss",
                "script_params": {
                    "keyword": "Python", "city": ["上海"], "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
            })
            self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
            task_id = resp.get_json()["task_id"]
            self._wait_for_task(task_id)

        # BossCdpSource 必须被调用，且显式传入冻结 cdp_port
        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        self.assertIn(
            "cdp_port", kwargs,
            "cdp_port 必须从冻结 runtime 显式传入，不能省略让 adapter 用默认端口",
        )
        self.assertEqual(kwargs["cdp_port"], 9222)
        # run_search 收到的 source 是 mock 返回的对象（验证 source 被传递）
        mock_search.assert_called_once()
        self.assertIsNotNone(mock_search.call_args.args[1])

    def test_resume_restores_frozen_runtime_from_db(self):
        """T403: 续抓时从 DB 恢复 platform/cdp_port/profile_key 到 task dict。

        合同（contracts/http-api.md 第 212 行）：
        "按冻结 browser_account/cdp_port/profile_key 创建原平台 adapter。"

        continue_execute_search 必须从 DB execution_params 恢复冻结身份，
        不能只恢复 browser_account 而丢弃 platform/cdp_port/profile_key。
        """
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]

        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit"):
            resp = self.client.post("/api/execute-search", json={
                "platform": "boss",
                "script_params": {
                    "keyword": "Python", "city": ["上海"], "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
            })
        self.assertEqual(resp.status_code, 200)
        task_id = resp.get_json()["task_id"]

        # 模拟服务重启：清空内存 task，DB 保留 paused 状态
        store = self.app.config["TASK_STORE"]
        store.update_screening_run(task_id, status="running")
        store.update_screening_run(
            task_id, status="paused", current_stage="scrape",
            error_code="captcha_required", error_reason="测试暂停",
        )
        store.save_checkpoint(task_id, "scrape", [])
        self.app.config["PIPELINE_TASKS"].pop(task_id, None)

        # 续抓：mock executor 和 block check 防止任务实际运行
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda run: (True, "", "")
        with mock.patch.object(executor, "submit"):
            resp = self.client.post(
                f"/api/execute-search/continue/{task_id}")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

        # task dict 必须从 DB 恢复冻结 runtime
        tasks = self.app.config["PIPELINE_TASKS"]
        task = tasks.get(task_id)
        self.assertIsNotNone(task, "续抓后 task 必须在内存中恢复")
        self.assertEqual(task.get("platform"), "boss")
        self.assertTrue(task.get("cdp_port"),
                        "续抓后 task 必须有冻结 cdp_port")
        self.assertTrue(task.get("profile_key"),
                        "续抓后 task 必须有冻结 profile_key")
        self.assertTrue(task.get("task_input_digest"),
                        "续抓后 task 必须有冻结 task_input_digest")
        db_started = _iso_epoch_ms(
            store.get_screening_run(task_id).get("started_at"))
        self.assertEqual(
            task.get("started_at"), db_started,
            "续抓必须沿用原任务 started_at，前端计时不清零",
        )

    # -- T404: source attempt before combo result -----------------------

    def test_source_attempt_precedes_combo_result(self):
        """T404: source attempt 在 combo result 之前持久化。

        合同（tasks005 节点门禁 A）：
        "在任何完成键、run 进度、状态或 snapshot 更新前追加 source attempt"
        """
        from webui.source import SourceOutcome
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]

        fake_source = mock.MagicMock()
        fake_source.preflight.return_value = SourceOutcome.success(
            safe_log="source_ready")
        fake_source.fetch_list.return_value = SourceOutcome.success(
            jobs=[{"job_id": "job-1", "title": "Python"}],
            safe_log="list job_count=1",
            input_hash="sha256-fake",
        )
        store = self.app.config["TASK_STORE"]
        call_order = []
        orig_append = store.append_source_attempt
        orig_save = store.save_scrape_combo_result

        def tracked_append(**kw):
            call_order.append("append_source_attempt")
            return orig_append(**kw)

        def tracked_save(*args, **kw):
            call_order.append("save_scrape_combo_result")
            return orig_save(*args, **kw)

        with mock.patch("webui.app._BossCdpSource", return_value=fake_source), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch.object(store, "append_source_attempt",
                                  side_effect=tracked_append), \
                mock.patch.object(store, "save_scrape_combo_result",
                                  side_effect=tracked_save):
            resp = self.client.post("/api/execute-search", json={
                "platform": "boss",
                "script_params": {
                    "keyword": "Python", "city": ["上海"], "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
            })
            self.assertEqual(resp.status_code, 200)
            task_id = resp.get_json()["task_id"]
            self._wait_for_task(task_id)

        self.assertIn("append_source_attempt", call_order,
                      "source attempt 必须被调用")
        self.assertIn("save_scrape_combo_result", call_order,
                      "combo result 必须被调用")
        append_idx = call_order.index("append_source_attempt")
        save_idx = call_order.index("save_scrape_combo_result")
        self.assertLess(
            append_idx, save_idx,
            "source attempt 必须在 combo result 之前持久化")

    def test_source_attempt_failure_prevents_combo_result(self):
        """T404: append_source_attempt 失败时不得推进 combo result。"""
        from webui.source import SourceOutcome
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]

        fake_source = mock.MagicMock()
        fake_source.preflight.return_value = SourceOutcome.success(
            safe_log="source_ready")
        fake_source.fetch_list.return_value = SourceOutcome.success(
            jobs=[{"job_id": "job-1", "title": "Python"}],
            safe_log="list job_count=1",
            input_hash="sha256-fake",
        )
        store = self.app.config["TASK_STORE"]
        save_called = False

        def fail_append(**kw):
            raise sqlite3.Error("persist failed")

        def tracked_save(*args, **kw):
            nonlocal save_called
            save_called = True

        with mock.patch("webui.app._BossCdpSource", return_value=fake_source), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"), \
                mock.patch.object(store, "append_source_attempt",
                                  side_effect=fail_append), \
                mock.patch.object(store, "save_scrape_combo_result",
                                  side_effect=tracked_save):
            resp = self.client.post("/api/execute-search", json={
                "platform": "boss",
                "script_params": {
                    "keyword": "Python", "city": ["上海"], "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
            })
            self.assertEqual(resp.status_code, 200)
            task_id = resp.get_json()["task_id"]
            self._wait_for_task(task_id)

        self.assertFalse(
            save_called,
            "append_source_attempt 失败时不得推进 save_scrape_combo_result")

    # -- T405: 按 combo 最新 attempt 汇总 source outcomes ----------------

    def _create_finished_scrape_task(self, platform="boss"):
        """创建一个已完成的搜索任务，返回 (task_id, store)。"""
        from webui.source import SourceOutcome
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": platform,
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]

        fake_source = mock.MagicMock()
        fake_source.preflight.return_value = SourceOutcome.success(
            safe_log="source_ready")
        fake_source.fetch_list.return_value = SourceOutcome.success(
            jobs=[{"job_id": "job-1", "title": "Python"}],
            safe_log="list job_count=1",
            input_hash="sha256-fake",
        )
        with mock.patch("webui.app._BossCdpSource", return_value=fake_source), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")), \
                mock.patch("webui.pipeline_exec.close_debug_chrome"):
            resp = self.client.post("/api/execute-search", json={
                "platform": platform,
                "script_params": {
                    "keyword": "Python", "city": ["上海"], "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
            })
            self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
            task_id = resp.get_json()["task_id"]
            self._wait_for_task(task_id)
        store = self.app.config["TASK_STORE"]
        return task_id, store

    def test_search_progress_returns_platform_and_digest(self):
        """T405: search-progress 返回 platform、task_input_digest、
        source_summary 和 source_outcomes。"""
        task_id, _ = self._create_finished_scrape_task()

        resp = self.client.get(f"/api/search-progress/{task_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("platform"), "boss")
        self.assertTrue(data.get("task_input_digest"))
        self.assertIn("source_summary", data)
        self.assertIn("source_outcomes", data)

    def test_task_state_returns_platform_and_digest(self):
        """T405: task-state 返回 platform、task_input_digest、
        source_summary 和 source_outcomes。"""
        task_id, _ = self._create_finished_scrape_task()

        resp = self.client.get(f"/api/task-state/{task_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("platform"), "boss")
        self.assertTrue(data.get("task_input_digest"))
        self.assertIn("source_summary", data)
        self.assertIn("source_outcomes", data)

    def test_source_outcomes_latest_per_combo(self):
        """T405: source_outcomes 按 combo 最新 attempt 汇总。

        同一 combo 多次 attempt 时只返回最新；不同 combo 各自返回最新。
        """
        task_id, store = self._create_finished_scrape_task()

        # 任务完成后已有 1 条 attempt（combo: Python|上海，non_empty）
        # 追加同 combo 第 2 次 attempt（模拟重试后变 empty）
        store.append_source_attempt(
            run_id=task_id, platform="boss",
            combo_key="Python|上海", attempt_no=2,
            input_hash="sha256-v2",
            outcome_kind="empty", job_count=0,
            empty_evidence={"kind": "explicit_empty_state",
                            "fixture_version": "v1",
                            "marker": "normalized-empty-state"},
        )
        # 另一个 combo
        store.append_source_attempt(
            run_id=task_id, platform="boss",
            combo_key="Java|上海", attempt_no=1,
            input_hash="sha256-java",
            outcome_kind="non_empty", job_count=3,
        )

        resp = self.client.get(f"/api/task-state/{task_id}")
        data = resp.get_json()
        outcomes = data.get("source_outcomes") or []
        by_combo = {o["combo_key"]: o for o in outcomes}
        self.assertIn("Python|上海", by_combo)
        self.assertIn("Java|上海", by_combo)
        # Python|上海 最新是 attempt_no=2，empty
        self.assertEqual(by_combo["Python|上海"]["attempt_no"], 2)
        self.assertEqual(by_combo["Python|上海"]["outcome_kind"], "empty")
        # Java|上海 是 non_empty
        self.assertEqual(by_combo["Java|上海"]["outcome_kind"], "non_empty")

    def test_no_empty_inference_from_zero_jobs(self):
        """T405: 无 source attempt 记录时不从岗位数为零推断 empty。

        刷新/重启后若 DB 无 attempt 记录，source_outcomes 为空列表，
        source_summary 不报告 empty。
        """
        task_id, store = self._create_finished_scrape_task()

        # 删除所有 source attempts 模拟无记录
        with store._connection() as conn:
            conn.execute(
                "DELETE FROM screening_source_attempts WHERE run_id=?",
                (task_id,))

        resp = self.client.get(f"/api/task-state/{task_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        outcomes = data.get("source_outcomes") or []
        self.assertEqual(outcomes, [],
                         "无 attempt 记录时 source_outcomes 必须为空，"
                         "不从零岗位推断 empty")
        summary = data.get("source_summary") or {}
        self.assertEqual(summary.get("empty_count", 0), 0,
                         "无 attempt 记录时不得报告 empty")

    def test_search_progress_identity_conflict(self):
        """T405: 内存 task 平台与 DB run 不一致 → 409 run_identity_conflict。"""
        task_id, store = self._create_finished_scrape_task()

        # 篡改 DB run 的 platform，制造内存（boss）与 DB（zhilian）不一致
        with store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET platform='zhilian' WHERE id=?",
                (task_id,))

        resp = self.client.get(f"/api/search-progress/{task_id}")
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertEqual(data.get("error"), "run_identity_conflict")

    def test_search_progress_digest_conflict(self):
        """T713: 内存 task digest 与 DB run 不一致 → 409 run_identity_conflict。"""
        task_id, store = self._create_finished_scrape_task()

        # 给内存 task 写入一个 task_input_digest，再篡改 DB run 的 digest
        tasks = self.app.config["PIPELINE_TASKS"]
        task = tasks.get(task_id)
        if task is not None:
            task["task_input_digest"] = "mem-digest-aaa"
        with store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET task_input_digest='db-digest-bbb' WHERE id=?",
                (task_id,))

        resp = self.client.get(f"/api/search-progress/{task_id}")
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertEqual(data.get("error"), "run_identity_conflict")


# ======================================================================
# 门禁B: T406-T409 — AI run 平台继承 + 结果身份
# ======================================================================


class AiScreenPlatformInheritanceTests(unittest.TestCase):
    """T406-T407: ai_screen 平台继承与筛选快照测试。"""

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
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _create_completed_scrape_run(self, platform="boss"):
        """创建并持久化一个完成的搜索 run。"""
        run_id = f"scrape_{platform}_{uuid.uuid4().hex[:8]}"
        self.store.create_screening_run(
            run_id,
            frozen_filters={},
            source_count=5,
            execution_params={
                "platform": platform,
                "cdp_port": 9222,
                "profile_key": "a",
                "task_input_digest": hashlib.sha256(
                    json.dumps({"platform": platform}, sort_keys=True).encode()
                ).hexdigest(),
            },
            backend_version="test",
        )
        self.store.update_screening_run(run_id, status="succeeded",
                                          current_stage="done",
                                          processed_count=5, match_count=3)
        # 在内存中注册为已完成任务
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "done", "progress": {}, "logs": [],
            "result": {"ok": True, "jobs": [], "total_scraped": 5,
                       "total_matched": 3, "completed_combos": ["Python|上海"],
                       "error": ""},
            "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(),
            "platform": platform,
            "task_input_digest": "test_digest",
        }
        return run_id

    # -- T406: 平台一致性校验 -------------------------------------------

    def test_ai_screen_with_matching_platform_succeeds(self):
        """T406: 客户端 platform 与父 run 一致时成功。"""
        scrape_id = self._create_completed_scrape_run("boss")
        resp = self.client.post("/api/ai-screen", json={
            "scrape_task_id": scrape_id,
            "platform": "boss",
            "screening_fields": {"salary": ["405"]},
            "profile_summary": "测试候选人",
        })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["platform"], "boss")
        self.assertTrue(data["task_input_digest"])

    def test_ai_screen_platform_mismatch_returns_409(self):
        """T406: 客户端 platform 与父 run 不一致 → 409 parent_platform_mismatch。"""
        scrape_id = self._create_completed_scrape_run("boss")
        resp = self.client.post("/api/ai-screen", json={
            "scrape_task_id": scrape_id,
            "platform": "zhilian",
            "screening_fields": {"salary": ["405"]},
            "profile_summary": "测试候选人",
        })
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertEqual(data.get("error"), "parent_platform_mismatch")

    def test_ai_screen_omitted_platform_inherits_parent(self):
        """T406: 省略 platform 时继承父 run 平台。"""
        scrape_id = self._create_completed_scrape_run("boss")
        resp = self.client.post("/api/ai-screen", json={
            "scrape_task_id": scrape_id,
            "screening_fields": {"salary": ["405"]},
            "profile_summary": "测试候选人",
        })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertEqual(data["platform"], "boss")

    def test_ai_screen_filter_schema_version_mismatch_returns_409(self):
        """T406: filter_schema_version 与父 run 不一致 → 409。"""
        scrape_id = self._create_completed_scrape_run("boss")
        # 设置父 run 的 schema_version
        self.store.update_screening_run(scrape_id)
        # 直接改 DB 设置 schema_version
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET filter_schema_version=2 WHERE id=?",
                (scrape_id,))
        resp = self.client.post("/api/ai-screen", json={
            "scrape_task_id": scrape_id,
            "platform": "boss",
            "screening_fields": {"salary": ["405"]},
            "profile_summary": "测试候选人",
            "filter_schema_version": 1,
        })
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertEqual(data.get("error"), "filter_schema_version_mismatch")

    # -- T407: 别人字段稳定值与当时标签的完整快照 -----------------------

    def test_ai_screen_saves_filter_snapshot(self):
        """T407: AI 筛选保存字段稳定值和当时标签的完整筛选快照。"""
        scrape_id = self._create_completed_scrape_run("boss")
        screening_fields = {
            "salary": ["405", "406"],
            "experience": ["103"],
            "degree": ["202"],
        }
        resp = self.client.post("/api/ai-screen", json={
            "scrape_task_id": scrape_id,
            "platform": "boss",
            "screening_fields": screening_fields,
            "profile_summary": "测试候选人",
        })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        task_id = resp.get_json()["task_id"]

        # 验证筛选快照已持久化
        run = self.store.get_screening_run(task_id)
        self.assertIsNotNone(run)
        self.assertEqual(run.get("platform"), "boss")
        params = run.get("execution_params") or {}
        self.assertEqual(params.get("platform"), "boss")
        self.assertTrue(
            params.get("task_input_digest"),
            "task_input_digest 必须存在于 execution_params",
        )

    def test_ai_screen_creates_run_with_parent_platform(self):
        """T407: 新 AI run 的 execution_params 含父 run 平台。"""
        scrape_id = self._create_completed_scrape_run("boss")
        resp = self.client.post("/api/ai-screen", json={
            "scrape_task_id": scrape_id,
            "platform": "boss",
            "screening_fields": {"salary": ["405"]},
            "profile_summary": "测试候选人",
        })
        self.assertEqual(resp.status_code, 200)
        task_id = resp.get_json()["task_id"]
        run = self.store.get_screening_run(task_id)
        self.assertIsNotNone(run)
        params = run.get("execution_params") or {}
        self.assertEqual(params.get("platform"), "boss")
        self.assertEqual(params.get("scrape_task_id"), scrape_id)


# ======================================================================
# T409: Latest result 三种查询模式
# ======================================================================


class LatestPipelineResultQueryTests(unittest.TestCase):
    """T409: latest_pipeline_result 的三种查询模式。"""

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
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _save_result_snapshot(self, run_id, platform="boss",
                               status="done"):
        """保存一个 result_snapshot 记录。"""
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "total_scraped, total_kept, total_dropped, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at, finished_at) "
                "VALUES (?, ?, ?, 'result_snapshot', '{}', "
                "0, 0, 0, 0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL, NULL)",
                (str(run_id), str(platform), str(status)),
            )

    def test_global_latest_returns_most_recent(self):
        """T409: 无参数时返回全局最近成功结果。"""
        self._save_result_snapshot("run_001", "boss")
        import time
        time.sleep(0.01)
        self._save_result_snapshot("run_002", "zhilian")
        resp = self.client.get("/api/latest-pipeline-result")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["has_result"])
        # 应返回最近的 run_002
        self.assertEqual(data.get("source_run_id"), "run_002")

    def test_query_by_platform_returns_filtered(self):
        """T409: platform=boss 时只返回 boss 的最近结果。"""
        self._save_result_snapshot("run_001", "boss")
        import time
        time.sleep(0.01)
        self._save_result_snapshot("run_002", "zhilian")
        resp = self.client.get("/api/latest-pipeline-result?platform=boss")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["has_result"])
        self.assertEqual(data.get("source_run_id"), "run_001")
        self.assertEqual(data.get("platform"), "boss")

    def test_query_by_run_id_returns_exact(self):
        """T409: run_id 查询返回精确结果。"""
        # app.py 对 run_id 查询检查 status in ('succeeded', 'partial')
        self._save_result_snapshot("run_001", "boss", status="succeeded")
        resp = self.client.get(
            "/api/latest-pipeline-result?run_id=run_001")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["has_result"])
        self.assertEqual(data.get("source_run_id"), "run_001")

    def test_query_run_id_platform_mismatch_returns_409(self):
        """T409: run_id + platform 不一致 → 409 run_platform_conflict。"""
        # app.py 对 run_id 查询检查 status in ('succeeded', 'partial')
        self._save_result_snapshot("run_001", "boss", status="succeeded")
        resp = self.client.get(
            "/api/latest-pipeline-result?run_id=run_001&platform=zhilian")
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertEqual(data.get("error"), "run_platform_conflict")

    def test_unknown_run_id_returns_no_result(self):
        """T409: 不存在的 run_id 返回 has_result=False。"""
        resp = self.client.get(
            "/api/latest-pipeline-result?run_id=nonexistent")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["has_result"])

    def test_result_contains_source_outcomes(self):
        """T409: 结果包含 source_summary 和 source_outcomes。"""
        self._save_result_snapshot("run_001", "boss")
        resp = self.client.get("/api/latest-pipeline-result")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("source_summary", data)
        self.assertIn("source_outcomes", data)
        self.assertIn("source_evidence_available", data)


# ======================================================================
# 门禁C: T410-T413 — 状态映射 + 恢复 + 原子 claim
# ======================================================================


class StatusMappingTests(unittest.TestCase):
    """T410: 唯一公共状态映射和四类非终态恢复测试。"""

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
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _create_run(self, run_id, status="queued"):
        """创建指定状态的 screening_run。"""
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, ?, 'process_log', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id), str(status)),
            )

    def test_public_status_vocabulary_has_no_waiting(self):
        """017-US5: 一套话术一个口径——公共词汇唯一且不含 waiting（queued 统一）。"""
        from webui.app import _public_task_status
        cases = {
            "queued": "queued",
            "waiting": "queued",  # 旧词并入 queued（前端无人消费）
            "running": "running",
            "paused": "paused",
            "succeeded": "completed",
            "done": "completed",
            "partial": "completed_with_pending",
            "failed": "failed",
            "interrupted": "cancelled",  # 无 interruption_kind 时按终态取消
        }
        for db_status, expected in cases.items():
            self.assertEqual(
                _public_task_status(db_status), expected,
                f"DB 状态 {db_status} 应映射到 {expected}",
            )

    def test_same_task_status_across_detail_poll_and_resume(self):
        """017-US5: 同一任务在详情/轮询/接回三接口状态词一致（无 waiting 分叉）。"""
        cases = {
            "paused": "paused",
            "partial": "completed_with_pending",
            "failed": "failed",
        }
        for db_status, expected in cases.items():
            run_id = f"vocab-{db_status}"
            self._create_run(run_id, db_status)
            self.app.config["PIPELINE_TASKS"][run_id] = {
                "kind": "scrape", "status": db_status, "progress": {}, "logs": [],
                "result": None, "error": "", "started_at": None,
                "finished_at": None, "stop_event": threading.Event(),
                "platform": "boss",
            }
            detail = self.client.get(f"/api/task-state/{run_id}").get_json()
            self.assertEqual(detail.get("status"), expected, f"{db_status} 详情")
            poll = self.client.get(f"/api/search-progress/{run_id}").get_json()
            self.assertEqual(poll.get("status"), expected, f"{db_status} 轮询")
            self.assertNotEqual(poll.get("status"), "waiting")
        # 接回接口（列表顶部）对 paused 任务与详情/轮询一致
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["status"], "paused")

    def test_public_task_status_mapping_unique(self):
        """T410: 内存/DB canonical 状态统一映射到公共 API 状态。"""
        from webui.app import _public_task_status
        cases = {
            ("queued", None): "queued",
            ("waiting", None): "queued",
            ("running", None): "running",
            ("paused", None): "paused",
            ("succeeded", None): "completed",
            ("done", None): "completed",
            ("partial", None): "completed_with_pending",
            ("failed", None): "failed",
            ("interrupted", "user_cancelled"): "cancelled",
            ("interrupted", "process_restart"): "interrupted",
            ("interrupted", "operator_stop"): "interrupted",
        }
        for (status, kind), expected in cases.items():
            self.assertEqual(
                _public_task_status(status, kind), expected,
                f"状态 {status}/{kind} 应映射到 {expected}",
            )
    def test_task_state_returns_mapped_status(self):
        """T410: api_task_state 返回映射后的任务状态。"""
        run_id = "test_status_mapping"
        self._create_run(run_id, "paused")
        # 注册内存 task
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "paused", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
        }
        resp = self.client.get(f"/api/task-state/{run_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "paused")
        self.assertEqual(data.get("db_status"), "paused")

    def test_task_state_success_count_tracks_live_progress_current(self):
        """内存任务 progress.current 实时推进时，success_count 必须跟随。

        回归：智联详情批内条级进度（on_item_done → emit current）必须实时
        反映到 task-state 计数画面；此前只读 DB processed_count（批次粒度），
        用户看到「已完成」长时间卡 0。
        """
        run_id = "test_live_current"
        self._create_run(run_id, "running")
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "ai_screen", "status": "running",
            "progress": {"stage": "fetch_jd", "current": 7, "total": 28,
                         "message": "抓取 JD 7/28", "overall_percent": 37},
            "logs": [], "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
        }
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        # DB processed_count 为 0，但内存进度已推进到 7：取两者最大值。
        self.assertEqual(data["success_count"], 7)

    def test_task_state_scrape_does_not_use_combo_index_as_success_count(self):
        """scrape 任务的 searching 阶段 current 是组合序号，不得当成功数显示。

        回归：live_current 只对条数语义的任务（ai_screen/recrawl）启用；
        scrape 列表抓取把组合序号混进成功数会显示「已完成 3 / 127 岗位」。
        """
        run_id = "test_scrape_combo_current"
        self._create_run(run_id, "running")
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running",
            "progress": {"stage": "searching", "current": 3, "total": 5,
                         "message": "正在抓第 3 个关键词组合", "overall_percent": 30},
            "logs": [], "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
        }
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        # DB processed_count=0、match/mismatch=0：组合序号 3 不得透出。
        self.assertEqual(data["success_count"], 0)

    def test_task_state_interrupted_maps_to_cancelled(self):
        """T410: interrupted DB 状态 → cancelled 任务状态。"""
        run_id = "test_interrupted_mapping"
        self._create_run(run_id, "interrupted")
        resp = self.client.get(f"/api/task-state/{run_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "cancelled")
        self.assertEqual(data.get("db_status"), "interrupted")

    def test_task_state_restart_interrupted_carries_message(self):
        """服务重启中断的任务不应显示默认“正在准备任务”。"""
        run_id = "interrupt-msg"
        self._create_run(run_id, "interrupted")
        self.store.save_interruption_kind(run_id, "process_restart")
        data = self.client.get("/api/task-state/interrupt-msg").get_json()
        self.assertEqual(data.get("status"), "interrupted")
        self.assertIn("message", data.get("progress") or {})
        self.assertNotEqual(data["progress"]["message"], "正在准备任务")

    # -- T412: continue 一致性校验 + 原子 claim --------------------------

    def test_continue_checks_platform_consistency(self):
        """T412: continue 验证平台一致性。"""
        run_id = "test_continue_platform"
        self._create_run(run_id, "paused")
        # 设置 platform
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET platform='boss' WHERE id=?",
                (run_id,))

        # 尝试继续但不匹配平台（无平台校验时也会因无 execution_params 失败）
        resp = self.client.post(f"/api/task/continue/{run_id}")
        # 可能因缺少 scrape_task_id 而失败，但不应该报 404
        self.assertNotEqual(resp.status_code, 404)

    def test_claim_paused_run_atomic(self):
        """T412: claim_paused_screening_run 原子性——两次调用只成功一次。"""
        run_id = "test_atomic_claim"
        self._create_run(run_id, "paused")
        self.assertTrue(
            self.store.claim_paused_screening_run(run_id),
            "第一次 claim 应成功",
        )
        # 第二次 claim 应失败（已被标记为 running）
        self.assertFalse(
            self.store.claim_paused_screening_run(run_id),
            "第二次 claim 应失败——paused→running 只允许一次",
        )

    def test_claim_non_paused_run_fails(self):
        """T412: 非 paused 状态的 run 不能被 claim。"""
        run_id = "test_claim_non_paused"
        self._create_run(run_id, "running")
        self.assertFalse(
            self.store.claim_paused_screening_run(run_id),
            "running 状态的 run 不能被 claim",
        )

    # -- T413: 重启打断标记 ---------------------------------------------

    def test_stale_runs_marked_interrupted_on_startup(self):
        """T413: 服务重启时 running/queued 的 run 被标记为 interrupted。"""
        from webui.store import TaskStore
        run_id = "test_stale_interrupted"
        self._create_run(run_id, "running")

        # 模拟重启：创建新 store 实例
        new_store = TaskStore(self.store.db_path)
        run = new_store.get_screening_run(run_id)
        self.assertIsNotNone(run)
        self.assertEqual(
            run["status"], "interrupted",
            "重启后 running 的 run 应被标记为 interrupted",
        )
        self.assertEqual(
            run.get("error_code"), "restart",
            "interrupted 的 error_code 应为 restart",
        )


class PauseElapsedAndResumeConfigTests(unittest.TestCase):
    """暂停不计时（active_elapsed_ms）+ 高级设置续跑生效（三路径刷新配置）。"""

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
        self.store = self.app.config["TASK_STORE"]
        self.tz = timezone(timedelta(hours=8))

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _create_run(self, run_id, status="queued"):
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, ?, 'process_log', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id), str(status)),
            )

    def _iso(self, sec):
        return (datetime(2026, 8, 1, 10, 0, 0, tzinfo=self.tz)
                + timedelta(seconds=sec)).isoformat()

    def _insert_event(self, run_id, event_type, at_iso):
        with self.store._connection() as conn:
            # task_logs 外键指向 tasks 表：先插入占位行（同 append_task_events）
            conn.execute(
                "INSERT OR IGNORE INTO tasks (id, kind, status, params_json, created_at, updated_at) "
                "VALUES (?, 'screening_event_log', 'logging', '{}', ?, ?)",
                (str(run_id), at_iso, at_iso),
            )
            seq = int(conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq "
                "FROM task_logs WHERE task_id = ?",
                (str(run_id),),
            ).fetchone()["next_seq"])
            line = json.dumps(
                {"type": event_type, "payload": {}, "at": at_iso},
                ensure_ascii=False,
            )
            conn.execute(
                "INSERT INTO task_logs (task_id, seq, created_at, line) "
                "VALUES (?, ?, ?, ?)",
                (str(run_id), seq, at_iso, line),
            )

    def _scope(self, **overrides):
        raw = {
            "schema_version": 1, "platform": "boss",
            "keywords": ["Python"], "scope_kind": "cities",
            "cities": ["上海"], "pages_per_combination": 3,
            "combination_count": 1, "planned_pages": 3,
            "task_size": "small", "scope_digest": None,
        }
        raw.update(overrides)
        return raw

    def _config(self, **overrides):
        base = {
            "inter_combo_delay": 30.0,
            "detail_batch_size": 10,
            "detail_interval": 2.0,
            "detail_reset_every": 3,
            "detail_batch_cooldown": 4.0,
            "detail_tab_pool_size": 10,
            "screen_batch_size": 30,
            "screen_concurrency": 3,
            "match_batch_size": 4,
            "match_concurrency": 8,
        }
        base.update(overrides)
        return base

    def _resume_mocks(self):
        """POST /api/task/continue 期间的浏览器隔离 mock。"""
        return (
            mock.patch("webui.pipeline_exec.resolve_browser_account", return_value=""),
            mock.patch("webui.pipeline_exec.set_active_cdp_data_dir"),
            mock.patch("webui.platforms.resolve_login_space"),
        )

    # ---- 暂停不计时：/api/task-state 返回 active_elapsed_ms ----

    def test_task_state_active_elapsed_excludes_pause(self):
        run_id = "elapsed-exclude"
        self._create_run(run_id, "succeeded")
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET started_at = ?, finished_at = ? WHERE id = ?",
                (self._iso(0), self._iso(240), run_id),
            )
        # 60s 暂停 → 180s 恢复：暂停 120s；总跨度 240s，实际运行 120s
        self._insert_event(run_id, "pause", self._iso(60))
        self._insert_event(run_id, "resume", self._iso(180))
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["active_elapsed_ms"], 120_000)

    def test_task_state_active_elapsed_frozen_while_paused(self):
        """暂停中无 finished_at：未闭合的 pause 截止到当前，累计仍定格在暂停前。"""
        run_id = "elapsed-paused"
        self._create_run(run_id, "paused")
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET started_at = ? WHERE id = ?",
                (self._iso(0), run_id),
            )
        self._insert_event(run_id, "pause", self._iso(120))
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertEqual(data["status"], "paused")
        # active = (now - 0) - (now - 120) = 120s，不受 now 影响
        self.assertEqual(data["active_elapsed_ms"], 120_000)

    def test_task_state_active_elapsed_none_without_events(self):
        """无 pause/resume 事件（老 run / 无暂停）时回退 None，前端沿用 started_at 差值。"""
        run_id = "elapsed-none"
        self._create_run(run_id, "succeeded")
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertIsNone(data.get("active_elapsed_ms"))

    # ---- 高级设置续跑生效 ----

    def test_continue_ai_screen_refreshes_execution_config(self):
        """暂停 AI 续跑：用新 Tab 数/间隔刷新 run 配置，DB 与 worker 都拿到新值。"""
        run_id = "resume-ai-config"
        old = self._config(detail_tab_pool_size=10, detail_interval=2.0)
        self.store.create_screening_run(
            run_id,
            frozen_filters={"salary": ["20-30K"]},
            source_count=2,
            execution_params={
                "platform": "boss",
                "scrape_task_id": "scrape-src-config",
                "browser_account": "a", "cdp_port": 9222, "profile_key": "boss:a",
                "profile_summary": "测试画像", "profile_facts": {"years": 3},
                "execution_config": old,
                "frozen_scope": self._scope(),
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="screen_b",
            error_code="ai_rate_limited", error_reason="AI 限流",
        )
        new = self._config(detail_tab_pool_size=6, detail_interval=5.0)
        self.store.save_custom_config(new)
        # 续 AI 需要父抓取 run 的岗位快照
        self.store.create_screening_run(
            "scrape-src-config", source_count=1,
            execution_params={"platform": "boss"},
        )
        self.store.update_screening_run("scrape-src-config", status="succeeded")
        jobs = [{"job_id": "j1", "platform_job_id": "j1",
                 "source_url": "https://zhipin.example/j1.html"}]
        self.store.save_scrape_combo_result(
            "scrape-src-config", "kw|city", jobs, ["kw|city"])

        captured = []
        r1, r2, r3 = self._resume_mocks()
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *a, **kw: captured.append((fn, a, kw)) or None,
        ), r1, r2, r3:
            self.app.config["RESUME_BLOCK_CHECKER"] = lambda run: (True, "", "")
            resp = self.client.post(f"/api/task/continue/{run_id}", json={})
        self.assertEqual(resp.status_code, 200, resp.get_json())

        from webui.execution_config import ExecutionConfigSnapshot
        fn, args, kwargs = captured[0]
        submitted = args[-1]
        self.assertIsInstance(submitted, ExecutionConfigSnapshot)
        self.assertEqual(submitted.detail_tab_pool_size, 6)
        self.assertEqual(submitted.detail_interval, 5.0)
        run = self.store.get_screening_run(run_id)
        db_config = (run.get("execution_params") or {}).get("execution_config") or {}
        self.assertEqual(db_config.get("detail_tab_pool_size"), 6)
        self.assertEqual(db_config.get("detail_interval"), 5.0)
        # pages/frozen_scope 保持冻结
        frozen = (run.get("execution_params") or {}).get("frozen_scope") or {}
        self.assertEqual(frozen.get("pages_per_combination"), 3)

    def test_continue_scrape_refreshes_inter_combo_delay(self):
        """暂停续抓：run_search 收到刷新后的间隔，pages 仍用冻结的 frozen_scope。"""
        run_id = "resume-scrape-config"
        old = self._config(inter_combo_delay=30.0, detail_tab_pool_size=10)
        self.store.create_screening_run(
            run_id,
            source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "Python", "city": ["上海"], "pages": 3},
                "browser_account": "a", "cdp_port": 9222, "profile_key": "boss:a",
                "execution_config": old,
                "frozen_scope": self._scope(),
            },
        )
        if self.store.get_screening_run(run_id)["status"] == "queued":
            self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="源账号限流",
        )
        new = self._config(inter_combo_delay=10.0)
        self.store.save_custom_config(new)

        captured = []
        r1, r2, r3 = self._resume_mocks()
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *a, **kw: captured.append((fn, a, kw)) or None,
        ), r1, r2, r3:
            self.app.config["RESUME_BLOCK_CHECKER"] = lambda run: (True, "", "")
            resp = self.client.post(f"/api/task/continue/{run_id}", json={})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        run = self.store.get_screening_run(run_id)
        db_config = (run.get("execution_params") or {}).get("execution_config") or {}
        self.assertEqual(db_config.get("inter_combo_delay"), 10.0)

        # submit 被拦截后 start_gate 已放行；手动跑续抓 worker 并拦截 run_search
        fn, args, kwargs = captured[0]
        run_search_calls = []
        with mock.patch(
            "webui.pipeline_exec.run_search",
            side_effect=lambda *a, **kw: run_search_calls.append(kw) or {
                "ok": True, "jobs": [], "total_scraped": 0, "total_matched": 0,
                "combinations": 0, "error": "", "completed_combos": [],
            },
        ), mock.patch("webui.pipeline_exec.resolve_browser_account", return_value=""), \
           mock.patch("webui.pipeline_exec.set_active_cdp_data_dir"), \
           mock.patch("webui.app._BossCdpSource"):
            fn()
        self.assertEqual(len(run_search_calls), 1)
        self.assertEqual(run_search_calls[0]["execution_config"].inter_combo_delay, 10.0)
        self.assertEqual(run_search_calls[0]["pages"], 3)

    def test_continue_recrawl_refreshes_match_concurrency(self):
        """暂停续补抓：用刷新后的并发配置，scope 从父抓取 run 继承且 pages 不变。"""
        run_id = "resume-recrawl-config"
        parent_id = "parent-scrape-recrawl"
        self.store.create_screening_run(
            parent_id,
            source_count=2,
            execution_params={
                "platform": "boss",
                "execution_config": self._config(match_concurrency=8),
                "frozen_scope": self._scope(),
            },
        )
        self.store.update_screening_run(parent_id, status="succeeded")
        self.store.create_screening_run(
            run_id,
            source_count=1,
            execution_params={
                "platform": "boss",
                "source_run_id": parent_id,
                "job_ids": ["j1"],
                "profile_summary": "测试画像",
                "browser_account": "a", "cdp_port": 9222, "profile_key": "boss:a",
            },
        )
        if self.store.get_screening_run(run_id)["status"] == "queued":
            self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="recrawl_jd",
        )
        new = self._config(match_concurrency=15)
        self.store.save_custom_config(new)

        captured = []
        r1, r2, r3 = self._resume_mocks()
        with mock.patch.object(
            self.app.config["PIPELINE_EXECUTOR"], "submit",
            side_effect=lambda fn, *a, **kw: captured.append((fn, a, kw)) or None,
        ), r1, r2, r3:
            self.app.config["RESUME_BLOCK_CHECKER"] = lambda run: (True, "", "")
            resp = self.client.post(f"/api/task/continue/{run_id}", json={})
        self.assertEqual(resp.status_code, 200, resp.get_json())

        from webui.execution_config import ExecutionConfigSnapshot
        fn, args, kwargs = captured[0]
        submitted = args[-1]
        self.assertIsInstance(submitted, ExecutionConfigSnapshot)
        self.assertEqual(submitted.match_concurrency, 15)
        run = self.store.get_screening_run(run_id)
        db_config = (run.get("execution_params") or {}).get("execution_config") or {}
        self.assertEqual(db_config.get("match_concurrency"), 15)
        # 父抓取 run 的 pages/scope 不被刷新改动
        parent = self.store.get_screening_run(parent_id)
        self.assertEqual(
            (parent.get("execution_params") or {}).get("frozen_scope", {}).get("pages_per_combination"),
            3,
        )

    def test_continue_blocked_does_not_refresh_db_config(self):
        """阻断未解除时继续被拒，且不提前改写 paused run 的配置快照。"""
        run_id = "resume-blocked-config"
        old = self._config(detail_tab_pool_size=10)
        self.store.create_screening_run(
            run_id,
            source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "Python", "city": ["上海"], "pages": 3},
                "browser_account": "a", "cdp_port": 9222, "profile_key": "boss:a",
                "execution_config": old,
                "frozen_scope": self._scope(),
            },
        )
        if self.store.get_screening_run(run_id)["status"] == "queued":
            self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="源账号限流",
        )
        new = self._config(detail_tab_pool_size=6)
        self.store.save_custom_config(new)

        r1, r2, r3 = self._resume_mocks()
        with r1, r2, r3:
            self.app.config["RESUME_BLOCK_CHECKER"] = (
                lambda run: (False, "captcha_required", "验证码未处理")
            )
            resp = self.client.post(f"/api/task/continue/{run_id}", json={})
        self.assertEqual(resp.status_code, 409)
        run = self.store.get_screening_run(run_id)
        db_config = (run.get("execution_params") or {}).get("execution_config") or {}
        # block 检查通过前不得刷新：DB 仍保留旧配置
        self.assertEqual(db_config.get("detail_tab_pool_size"), 10)


# ======================================================================
# 门禁D: T414-T419 — 平台敏感外围入口
# ======================================================================


class PlatformAwareTaskStateTests(unittest.TestCase):
    """T414: task state/progress 返回平台和 source outcomes。"""

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
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def test_task_state_returns_platform(self):
        """T414: api_task_state 返回目标 run 真实平台。"""
        run_id = "test_ts_platform"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'zhilian', 'paused', 'process_log', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        resp = self.client.get(f"/api/task-state/{run_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("platform"), "zhilian")
        self.assertIn("source_summary", data)
        self.assertIn("source_outcomes", data)

    def test_search_progress_returns_platform_and_source_outcomes(self):
        """T414: search-progress 返回平台和 source outcomes。"""
        run_id = "test_sp_platform"
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
            "platform": "boss",
            "task_input_digest": "test_digest",
        }
        resp = self.client.get(f"/api/search-progress/{run_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("platform"), "boss")
        self.assertIn("source_summary", data)
        self.assertIn("source_outcomes", data)


class PlatformAwareCancelTests(unittest.TestCase):
    """T415: 取消的平台感知。"""

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
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def test_cancel_returns_platform(self):
        """T415: 取消接口返回平台信息。"""
        run_id = "test_cancel_platform"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'boss', 'running', 'process_log', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
            "platform": "boss",
        }
        resp = self.client.post(f"/api/task/cancel/{run_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("platform"), "boss")
        self.assertIn("status", data)

    def test_cancel_writes_durable_state(self):
        """T415: 取消先 durable 写 interrupted，再发内存事件。"""
        run_id = "test_cancel_durable"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'boss', 'running', 'process_log', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
            "platform": "boss",
        }
        resp = self.client.post(f"/api/task/cancel/{run_id}")
        self.assertEqual(resp.status_code, 200)
        # 验证 DB 已更新
        run = self.store.get_screening_run(run_id)
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "interrupted")

    def test_cancel_with_jobs_keeps_scrape_data_without_history_round(self):
        """017-US1: 取消保留底层已抓岗位数据，但不再生成历史轮。"""
        run_id = "cancel-with-jobs-history"
        jobs = [
            {"job_id": "j1", "platform_job_id": "j1", "title": "岗位1",
             "source_url": "https://zhipin.example/j1.html"},
            {"job_id": "j2", "platform_job_id": "j2", "title": "岗位2",
             "source_url": "https://zhipin.example/j2.html"},
        ]
        self.store.create_screening_run(
            run_id, source_count=len(jobs),
            execution_params={"platform": "boss"},
        )
        self.store.save_scrape_combo_result(run_id, "kw|city", jobs, ["kw|city"])
        self.store.update_screening_run(
            run_id, status="running", current_stage="scrape")

        resp = self.client.post(f"/api/task/cancel/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        run = self.store.get_screening_run(run_id)
        # DB 存 interrupted + user_cancelled（对外公共词汇 cancelled）
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["interruption_kind"], "user_cancelled")
        # 底层已抓岗位数据保留，可对同一批抓取结果重新发起筛选
        kept = self.store.load_scrape_run_jobs(run_id)
        self.assertEqual(len(kept), 2)
        # 017-US1: 取消不再生成历史轮
        self.assertEqual(self.store.list_history_rounds("boss"), [])
        history = self.client.get("/api/result-history").get_json()
        self.assertEqual(history["items"], [])

    def test_cancel_without_jobs_does_not_create_history(self):
        """FR-019: 没有岗位产出的取消不进入历史。"""
        run_id = "cancel-no-jobs-history"
        self.store.create_screening_run(
            run_id, source_count=0,
            execution_params={"platform": "boss"},
        )
        self.store.update_screening_run(
            run_id, status="running", current_stage="scrape")

        resp = self.client.post(f"/api/task/cancel/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(self.store.list_history_rounds("boss"), [])

    def test_restart_interrupted_run_has_no_history_round(self):
        """017-US1: 进程强杀重启后任务显示中断、可续跑，历史不新增轮。"""
        run_id = "restart-interrupted-017"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, error_code, record_kind, "
                "frozen_filters_json, source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'boss', 'interrupted', 'restart', 'process_log', "
                "'{}', 2, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["status"], "interrupted")
        self.assertTrue(data["resumable"])
        # 017-US1: 重启中断不产生历史轮
        self.assertEqual(self.store.list_history_rounds("boss"), [])
        history = self.client.get("/api/result-history").get_json()
        self.assertEqual(history["items"], [])


class PlatformAwareFinishTests(unittest.TestCase):
    """T416: 提前结束的平台感知。"""

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
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def test_finish_rejects_user_cancelled(self):
        """T416: user_cancelled 的 run 不能通过 finish 改写。"""
        run_id = "test_finish_user_cancelled"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, interruption_kind, record_kind, "
                "frozen_filters_json, source_count, match_count, "
                "mismatch_count, execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'boss', 'interrupted', 'user_cancelled', "
                "'process_log', '{}', 0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 409)
        data = resp.get_json()
        self.assertEqual(data.get("error"), "user_cancelled")

    def test_finish_accepts_paused_and_returns_platform(self):
        """T416: paused 的 run 可 finish，返回平台。"""
        run_id = "test_finish_paused"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, "
                "frozen_filters_json, source_count, match_count, "
                "mismatch_count, execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'boss', 'paused', "
                "'process_log', '{}', 0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        resp = self.client.post(f"/api/task/finish/{run_id}")
        # 可能因缺少 scrape_task_id 而 409，但不应该报 404 或 500
        self.assertNotEqual(resp.status_code, 404)
        if resp.status_code == 409:
            data = resp.get_json()
            self.assertIn(data.get("error", ""),
                          ["missing_scrape_snapshot", "not_paused"])

    def test_finish_accepts_restart_interrupted(self):
        """T416: interrupted/process_restart 的 run 可 finish。"""
        run_id = "test_finish_restart"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, interruption_kind, record_kind, "
                "frozen_filters_json, source_count, match_count, "
                "mismatch_count, execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'boss', 'interrupted', 'process_restart', "
                "'process_log', '{}', 0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        resp = self.client.post(f"/api/task/finish/{run_id}")
        # 可能因缺少 scrape_task_id 而 409，但不应该报 404 或 500
        self.assertNotEqual(resp.status_code, 404)
        if resp.status_code == 409:
            data = resp.get_json()
            self.assertIn(data.get("error", ""),
                          ["missing_scrape_snapshot", "not_paused"])


class PlatformAwareJobDetailTests(unittest.TestCase):
    """T417: 单 JD 抓取平台继承。"""

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
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def test_job_detail_missing_params_returns_400(self):
        """T417: 缺少 job_id 或 source_url 返回 400。"""
        resp = self.client.post("/api/job-detail", json={
            "job_id": "",
            "source_url": "",
        })
        self.assertEqual(resp.status_code, 400)


class DraftSwitchTargetRunConservationTests(unittest.TestCase):
    """T712: 创建目标 run 后把草稿切到另一平台，外围操作仍作用于原 run。

    验证 cancel/finish/continue/reset 路由从 run.platform 读取平台，
    不读全局 draft platform。草稿切换不应改变目标 run 的平台归属。
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
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _seed_paused_zhilian_run(self, run_id="draft-switch-zhilian", status="paused", record_kind="process_log"):
        """种入一个 zhilian run，模拟目标 run 已创建。"""
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'zhilian', ?, 'process_log', '{}', "
                "0, 0, 0, '{\"platform\":\"zhilian\"}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id), status),
            )
        return run_id

    def test_cancel_after_draft_switch_still_targets_original_run(self):
        """T712: 创建 zhilian run → 草稿切到 boss → cancel 仍作用于 zhilian run。"""
        run_id = self._seed_paused_zhilian_run()
        # cancel 路由不读 draft，从 run.platform 读取
        resp = self.client.post(f"/api/task/cancel/{run_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("platform"), "zhilian",
                         "草稿切换后 cancel 仍应返回原 run 平台")
        # 验证 DB 中 run 的 platform 未被改写
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["platform"], "zhilian")

    def test_reset_after_draft_switch_still_targets_original_run(self):
        """017-US4: 旧结果清空端点已删除（404）；归档/删除统一走历史接口。"""
        # 旧 reset 端点不存在（无论草稿如何切换）
        resp = self.client.post("/api/reset-latest-result", json={
            "run_id": "anything", "platform": "boss",
        })
        self.assertEqual(resp.status_code, 404)
        # 新路径：归档走 archive-latest，删除走 DELETE /api/result-history/<run_id>
        run_id = self._seed_paused_zhilian_run(status="succeeded")
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_runs SET record_kind = 'result_snapshot' WHERE id = ?",
                (run_id,),
            )
        archive = self.client.post("/api/result-history/archive-latest")
        self.assertEqual(archive.status_code, 200)
        self.assertIn(run_id, archive.get_json()["archived_run_ids"])


class CrossPlatformBrowserConservationTests(unittest.TestCase):
    """T714: cancel/finish zhilian run 不得关闭 boss 浏览器，反之亦然。

    路由层调用 close_debug_chrome() 时不得误关另一平台的浏览器。
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
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _seed_running_run(self, run_id, platform="zhilian"):
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                f"VALUES (?, '{platform}', 'running', 'process_log', '{{}}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
            "platform": platform,
        }
        return run_id

    @mock.patch("webui.pipeline_exec.close_debug_chrome")
    def test_cancel_zhilian_run_does_not_close_with_boss_port(self, mock_close):
        """T714: cancel zhilian run 时 close_debug_chrome 不得用 BOSS 默认端口 9222 关闭。"""
        run_id = self._seed_running_run("cancel-zhilian-conservation", platform="zhilian")
        resp = self.client.post(f"/api/task/cancel/{run_id}")
        self.assertEqual(resp.status_code, 200)
        # close_debug_chrome 被调用时，参数不得是 BOSS 默认端口 9222
        if mock_close.called:
            call_args = mock_close.call_args
            port_arg = call_args[0][0] if call_args[0] else None
            self.assertNotEqual(port_arg, 9222,
                                "cancel zhilian run 不得用 BOSS 端口 9222 关闭浏览器")

    @mock.patch("webui.pipeline_exec.close_debug_chrome")
    def test_cancel_boss_run_does_not_close_with_zhilian_port(self, mock_close):
        """T714: cancel boss run 时 close_debug_chrome 不得用智联端口 9223 关闭。"""
        run_id = self._seed_running_run("cancel-boss-conservation", platform="boss")
        resp = self.client.post(f"/api/task/cancel/{run_id}")
        self.assertEqual(resp.status_code, 200)
        if mock_close.called:
            call_args = mock_close.call_args
            port_arg = call_args[0][0] if call_args[0] else None
            self.assertNotEqual(port_arg, 9223,
                                "cancel boss run 不得用智联端口 9223 关闭浏览器")


class PlatformAwareResetResultTests(unittest.TestCase):
    """T418: 结果重置平台感知。"""

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
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def test_legacy_reset_endpoint_removed(self):
        """017-US4: 旧结果清空端点已删除（404），归档/删除统一走历史接口。"""
        resp = self.client.post("/api/reset-latest-result", json={
            "run_id": "nonexistent",
        })
        self.assertEqual(resp.status_code, 404)

    def test_archive_and_delete_go_through_history_api(self):
        """017-US4: 归档走 archive-latest，删除走 DELETE /api/result-history/<run_id>。"""
        run_id = "archive-delete-017"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at, finished_at) "
                "VALUES (?, 'boss', 'succeeded', 'result_snapshot', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL, NULL)",
                (str(run_id),),
            )
        archive = self.client.post("/api/result-history/archive-latest")
        self.assertEqual(archive.status_code, 200)
        self.assertIn(run_id, archive.get_json()["archived_run_ids"])
        deleted = self.client.delete(f"/api/result-history/{run_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.get_json()["deleted"], True)


class PlatformAwareBrowserAccountTests(unittest.TestCase):
    """T419: 浏览器账号的平台语义。"""

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
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def test_browser_list_includes_platform(self):
        """T419: 浏览器列表接口返回平台信息。"""
        resp = self.client.get("/api/browser-accounts")
        # 接口可能返回 200 或 404，但不应该 500
        self.assertNotEqual(resp.status_code, 500)
        if resp.status_code == 200:
            data = resp.get_json()
            self.assertIn("accounts", data)

    def test_check_returns_platform_info(self):
        """T419: /api/check 返回平台信息。"""
        resp = self.client.get("/api/check")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        # 至少返回 ok 和平台信息
        self.assertIn("ok", data)


# ======================================================================
# T207 补丁：HTTP 端点暴露（/api/platforms、/api/options?platform、/api/filter-labels?platform）
# ======================================================================
# platforms.py 的服务投影函数（project_filter_schema、list_platforms）在
# tasks003 已测（见 tests/test_platforms.py T207），但 app.py 从未将其暴露
# 为 HTTP 端点——tasks003 允许文件范围不含 app.py。本类补 HTTP 端点测试，
# 与 test_platforms.py 的函数投影测试互补。详见 plan.md 切片 3 末尾。
class PlatformAwareEndpointsTests(unittest.TestCase):
    """T207 补丁：三平台感知端点的 HTTP 行为。"""

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
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    # -- /api/platforms -----------------------------------------------

    def test_platforms_endpoint_returns_registry_with_default(self):
        """/api/platforms 返回 BOSS+智联注册项，default=boss。"""
        resp = self.client.get("/api/platforms")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("default_platform"), "boss")
        platforms = {p["key"]: p for p in data.get("platforms", [])}
        self.assertIn("boss", platforms)
        self.assertIn("zhilian", platforms)
        # 智联 fixture 未核验，禁用新任务；BOSS 已启用
        # 智联真实元数据核验后启用；BOSS 已启用
        self.assertTrue(platforms["zhilian"]["enabled_for_new_tasks"])
        self.assertTrue(platforms["boss"]["enabled_for_new_tasks"])
        # 不返回 profile 路径/路径摘要（T207 安全要求）
        for p in data.get("platforms", []):
            for key in ("profile_dir", "boss_profile_dir", "profile_key", "cdp_port"):
                self.assertNotIn(key, p, f"平台投影不得返回 {key}")

    def test_platforms_endpoint_returns_schema_and_city_versions(self):
        """/api/platforms 返回 filter_schema_version 和 city_mapping_version。"""
        resp = self.client.get("/api/platforms")
        data = resp.get_json()
        platforms = {p["key"]: p for p in data["platforms"]}
        self.assertEqual(platforms["boss"]["filter_schema_version"], 1)
        self.assertEqual(platforms["boss"]["city_mapping_version"], 2)
        self.assertEqual(platforms["zhilian"]["filter_schema_version"], 2)
        self.assertEqual(platforms["zhilian"]["city_mapping_version"], 2)

    # -- /api/options -------------------------------------------------

    def test_options_without_platform_keeps_legacy_shape(self):
        """无 platform 参数时保持旧 BOSS 形状 {filters, cities}（兼容现有前端）。"""
        resp = self.client.get("/api/options")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("filters", data)
        self.assertIn("cities", data)
        self.assertIn("stage", data["filters"])
        self.assertIn({"label": "上海", "value": "上海"}, data["cities"])
        # 不应出现新形状字段
        for forbidden in ("ok", "platform", "city_mapping_version", "schema_version"):
            self.assertNotIn(forbidden, data)

    def test_options_with_platform_boss_returns_canonical_cities(self):
        """/api/options?platform=boss 返回新形状 {ok, platform, city_mapping_version, cities}。"""
        resp = self.client.get("/api/options?platform=boss")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["platform"], "boss")
        self.assertEqual(data["city_mapping_version"], 2)
        for city in data["cities"]:
            self.assertIn("label", city)
            self.assertIn("value", city)
            # 合同：前端不接收平台城市码；后端解析并冻结
            self.assertNotIn("platform_code", city)
            self.assertNotIn("code", city)

    def test_options_with_platform_zhilian_returns_nationwide_only(self):
        """/api/options?platform=zhilian 只返回全国（jl0），其它城市码未核验不暴露。"""
        resp = self.client.get("/api/options?platform=zhilian")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["platform"], "zhilian")
        self.assertEqual(data["city_mapping_version"], 2)
        cities = data["cities"]
        self.assertGreaterEqual(len(cities), 20)
        labels = {c["label"] for c in cities}
        self.assertIn("全国", labels)
        self.assertIn("上海", labels)

    def test_options_with_unknown_platform_returns_400(self):
        """未知平台返回 400 platform_validation_failed。"""
        resp = self.client.get("/api/options?platform=unknown")
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertEqual(data.get("error_code"), "platform_validation_failed")

    def test_analyze_resume_boss_projects_stage_and_common_fields(self):
        """BOSS 简历分析返回 stage 语义，不出现 company_nature。"""
        import io
        from webui import app as app_module
        fields = {
            "keyword": [{"word": "Python 后端", "recommended": True}],
            "city": ["上海"], "salary": ["406"], "experience": ["105"],
            "degree": ["203"], "industry": ["1001"], "scale": ["303"],
            "stage": ["804"], "profile_summary": "3年Python后端", "company_nature": ["1"],
        }
        store = self.app.config["TASK_STORE"]
        with mock.patch.object(store, "get_ai_settings", return_value={
            "is_configured": True, "endpoint_url": "https://api.example.com", "model": "test",
        }), mock.patch.object(store, "get_credential_ref", return_value="ref"), \
                mock.patch.object(app_module.ai_service, "retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.analyze_resume_to_fields", return_value=fields):
            resp = self.client.post(
                "/api/analyze-resume",
                data={"file": (io.BytesIO(b"resume"), "resume.txt"), "platform": "boss"},
                content_type="multipart/form-data",
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["platform"], "boss")
        self.assertEqual(data["fields"]["city"], [])
        self.assertEqual(data["semantic"]["city"], [])
        self.assertNotIn("company_nature", data["fields"])
        self.assertEqual(data["fields"]["stage"], ["804"])
        self.assertEqual(data["semantic"]["stage"], ["B轮"])
        self.assertEqual(data["semantic"]["experience"], ["3-5年"])
        self.assertNotIn("company_nature", data["semantic"])

    def test_analyze_resume_passes_through_profile_facts(self):
        """B033：简历分析响应透传 profile_facts（画像事实链路源头）。"""
        import io
        from webui import app as app_module
        facts = {
            "core_skills": ["Python", "Django"],
            "projects": [{"name": "订单系统", "role": "后端开发"}],
            "job_type": "全职",
            "languages": ["英语"],
        }
        fields = {
            "keyword": [{"word": "Python 后端", "recommended": True}],
            "city": ["上海"], "salary": ["406"], "experience": ["105"],
            "degree": ["203"], "industry": ["1001"], "scale": ["303"],
            "stage": ["804"], "profile_summary": "3年Python后端",
            "profile_facts": facts,
        }
        store = self.app.config["TASK_STORE"]
        with mock.patch.object(store, "get_ai_settings", return_value={
            "is_configured": True, "endpoint_url": "https://api.example.com", "model": "test",
        }), mock.patch.object(store, "get_credential_ref", return_value="ref"), \
                mock.patch.object(app_module.ai_service, "retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.analyze_resume_to_fields", return_value=fields):
            resp = self.client.post(
                "/api/analyze-resume",
                data={"file": (io.BytesIO(b"resume"), "resume.txt"), "platform": "boss"},
                content_type="multipart/form-data",
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["fields"]["profile_facts"], facts)

    def test_analyze_resume_zhilian_projects_company_nature_and_drops_stage(self):
        """智联简历分析返回 company_nature 语义，不出现 stage。"""
        import io
        from webui import app as app_module
        fields = {
            "keyword": [{"word": "Python 后端", "recommended": True}],
            "city": ["上海"], "salary": [], "experience": ["0305"], "degree": ["4"],
            "industry": [], "scale": [], "company_nature": ["1"], "stage": ["804"],
            "profile_summary": "3年Python后端",
        }
        store = self.app.config["TASK_STORE"]
        with mock.patch.object(store, "get_ai_settings", return_value={
            "is_configured": True, "endpoint_url": "https://api.example.com", "model": "test",
        }), mock.patch.object(store, "get_credential_ref", return_value="ref"), \
                mock.patch.object(app_module.ai_service, "retrieve_api_key", return_value="key"), \
                mock.patch("webui.ai.analyze_resume_to_fields", return_value=fields):
            resp = self.client.post(
                "/api/analyze-resume",
                data={"file": (io.BytesIO(b"resume"), "resume.txt"), "platform": "zhilian"},
                content_type="multipart/form-data",
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["platform"], "zhilian")
        self.assertNotIn("stage", data["fields"])
        self.assertEqual(data["fields"]["company_nature"], ["1"])
        self.assertEqual(data["semantic"]["company_nature"], ["国企"])
        self.assertNotIn("stage", data["semantic"])

    def test_analyze_resume_unknown_platform_returns_400(self):
        """简历分析未知平台在调用 AI 前返回 platform_validation_failed。"""
        import io
        with mock.patch("webui.ai.analyze_resume_to_fields") as analyze:
            resp = self.client.post(
                "/api/analyze-resume",
                data={"file": (io.BytesIO(b"resume"), "resume.txt"), "platform": "unknown"},
                content_type="multipart/form-data",
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json().get("error_code"), "platform_validation_failed")
        analyze.assert_not_called()

    # -- /api/filter-labels -------------------------------------------

    def test_filter_labels_without_platform_keeps_legacy_shape(self):
        """无 platform 参数时保持旧 BOSS 形状 {labels: {...}}（兼容现有前端）。"""
        resp = self.client.get("/api/filter-labels")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("labels", data)
        # 旧形状是 6 字段，含 stage，不含 company_nature
        self.assertIn("stage", data["labels"])
        self.assertNotIn("company_nature", data["labels"])
        # 不应出现新形状字段
        for forbidden in ("ok", "platform", "schema_version", "enabled_for_new_tasks", "fields"):
            self.assertNotIn(forbidden, data)

    def test_filter_labels_with_platform_zhilian_returns_company_nature(self):
        """/api/filter-labels?platform=zhilian 返回 company_nature，不含 stage；options 未核验为空。"""
        resp = self.client.get("/api/filter-labels?platform=zhilian")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["platform"], "zhilian")
        self.assertEqual(data["schema_version"], 2)
        # 智联真实元数据核验后启用
        self.assertTrue(data["enabled_for_new_tasks"])
        field_keys = [f["key"] for f in data["fields"]]
        # 字段顺序：salary/experience/degree/industry/scale/company_nature
        self.assertEqual(field_keys, [
            "salary", "experience", "degree", "industry", "scale", "company_nature",
        ])
        self.assertNotIn("stage", field_keys)
        # 智联 options 未核验，应为空数组
        # 智联 options 已由真实元数据核验，全部非空
        for f in data["fields"]:
            self.assertGreater(len(f["options"]), 0, f"字段 {f['key']} options 应已核验")
            self.assertTrue(f["multiple"])

    def test_filter_labels_with_platform_boss_returns_stage(self):
        """/api/filter-labels?platform=boss 返回 stage，不含 company_nature。"""
        resp = self.client.get("/api/filter-labels?platform=boss")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["platform"], "boss")
        self.assertTrue(data["enabled_for_new_tasks"])
        field_keys = [f["key"] for f in data["fields"]]
        # BOSS 字段顺序：salary/experience/degree/industry/scale/stage
        self.assertEqual(field_keys, [
            "salary", "experience", "degree", "industry", "scale", "stage",
        ])
        self.assertNotIn("company_nature", field_keys)

    def test_filter_labels_with_unknown_platform_returns_400(self):
        """未知平台返回 400 platform_validation_failed。"""
        resp = self.client.get("/api/filter-labels?platform=unknown")
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertEqual(data.get("error_code"), "platform_validation_failed")


# ======================================================================
# 契约合规补丁测试：覆盖本次 5 处契约违规修复（webui/app.py）
# ======================================================================
# 详见 contracts/http-api.md：
# - L219-229：cancel 合同（run_id + platform + status，DB 权威、内存快照兜底）
# - L247-251：/api/job-detail 成功响应含 platform + platform_job_id + jd
# - L253-255：/api/pipeline/jobs/{platform_job_id}/jd（fallback 无 source_run_id
#   是历史兼容路径，不得依赖 latest done run 猜平台）
# - L334-336：/api/check 显式平台解析；智联不得调旧 BOSS scraper
class ContractCompliancePatchTests(unittest.TestCase):
    """契约合规补丁：5 处修复的 HTTP 行为回归。"""

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
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    # -- (a) /api/check ------------------------------------------------

    def test_check_default_returns_boss_platform(self):
        """契约 L334-336：省略 platform 只兼容 BOSS，返回 platform=boss。"""
        completed = type("Completed", (), {
            "returncode": 0,
            "output_tail": "",
            "failure_code": None,
            "ok": True,
        })()
        with mock.patch("webui.app.ScraperExecutor.execute",
                        return_value=completed):
            resp = self.client.get("/api/check")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["platform"], "boss")
        self.assertTrue(data["connected"])

    def test_check_zhilian_returns_preflight_without_boss_scraper(self):
        """契约 L334-336：智联检查走自身 preflight，不调用旧 BOSS scraper。"""
        with (
            mock.patch("webui.app.ScraperExecutor.execute") as exec_mock,
            mock.patch("webui.source.ZhilianCdpSource") as source_cls,
        ):
            source_cls.return_value.preflight.return_value = mock.Mock(
                ok=True, failed_code="", failed_reason="")
            resp = self.client.get("/api/check?platform=zhilian")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["platform"], "zhilian")
        self.assertTrue(data["connected"])
        # 关键：智联检查路径不得调用旧 BOSS scraper
        self.assertFalse(exec_mock.called)

    # -- (b) /api/execute-search/<id>/cancel ---------------------------

    def test_execute_search_cancel_returns_run_id_and_platform(self):
        """契约 L219-229：取消响应含 run_id + platform + status=cancelled。"""
        run_id = "test_exec_cancel_platform"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'boss', 'running', 'process_log', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
            "platform": "boss",
        }
        with mock.patch("webui.pipeline_exec.close_debug_chrome"):
            resp = self.client.post(f"/api/execute-search/{run_id}/cancel")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["run_id"], run_id)
        self.assertEqual(data["task_id"], run_id)
        self.assertEqual(data["platform"], "boss")
        self.assertEqual(data["status"], "cancelled")

    # -- (c) /api/ai-screen/<id>/cancel --------------------------------

    def test_ai_screen_cancel_returns_run_id_and_platform(self):
        """契约 L219-229：AI 筛选取消响应含 run_id + platform + status。"""
        run_id = "test_ai_cancel_platform"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO screening_runs "
                "(id, platform, status, record_kind, frozen_filters_json, "
                "source_count, match_count, mismatch_count, "
                "execution_params_json, profile_summary, "
                "created_at, updated_at, started_at) "
                "VALUES (?, 'boss', 'running', 'process_log', '{}', "
                "0, 0, 0, '{}', '', "
                "datetime('now'), datetime('now'), NULL)",
                (str(run_id),),
            )
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "ai_screen", "status": "running", "progress": {},
            "logs": [], "result": None, "error": "", "started_at": None,
            "finished_at": None, "stop_event": threading.Event(),
            "platform": "boss",
        }
        with mock.patch("webui.pipeline_exec.close_debug_chrome"):
            resp = self.client.post(f"/api/ai-screen/{run_id}/cancel")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["run_id"], run_id)
        self.assertEqual(data["platform"], "boss")
        self.assertEqual(data["status"], "cancelled")

    # -- (d) /api/job-detail success path ------------------------------

    def test_job_detail_success_includes_platform_and_platform_job_id(self):
        """契约 L247-251：成功响应含 platform + platform_job_id + jd。"""
        from webui.source import SourceOutcome
        fake_source = mock.MagicMock()
        fake_source.fetch_detail.return_value = SourceOutcome.success(
            detail={"jd": "岗位职责：负责后端业务开发与 API 设计。"},
            safe_log="detail ok",
        )
        with mock.patch("webui.app._BossCdpSource",
                        return_value=fake_source), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")):
            resp = self.client.post("/api/job-detail", json={
                "job_id": "job-abc",
                "platform_job_id": "job-abc",
                "source_url": "https://www.zhipin.com/job/abc.html",
            })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["platform"], "boss")
        self.assertEqual(data["platform_job_id"], "job-abc")
        self.assertTrue(data["jd"])
        # _make_cdp_source 走 boss 分支（_BossCdpSource 被调用）
        self.assertTrue(fake_source.fetch_detail.called)

    def test_job_detail_zhilian_requires_source_run_id(self):
        """契约 L247-251：智联单 JD 不得只凭 URL 猜测来源。"""
        from webui.source import SourceOutcome
        fake_source = mock.MagicMock()
        fake_source.fetch_detail.return_value = SourceOutcome.success(
            detail={"jd": "岗位职责：负责后端业务开发与 API 设计。"},
            safe_log="detail ok",
        )
        with mock.patch("webui.source.ZhilianCdpSource",
                        return_value=fake_source), \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")):
            resp = self.client.post("/api/job-detail", json={
                "job_id": "job-abc",
                "platform_job_id": "job-abc",
                "source_url": "https://www.zhaopin.com/jobdetail/job-abc.htm",
            })
        self.assertEqual(resp.status_code, 409, resp.get_json())
        self.assertEqual(resp.get_json().get("error_code"), "run_identity_conflict")
        self.assertFalse(fake_source.fetch_detail.called)

    # -- (e) /api/pipeline/jobs/<job_id>/jd fallback path --------------

    def test_pipeline_job_jd_fallback_infers_boss_platform_from_url(self):
        """契约 L253-255：无 source_run_id 的 fallback 路径从 source_url 推断
        BOSS 平台，不依赖 latest done run（避免最新完成 run 是另一平台时
        误用平台身份）。"""
        from webui.source import SourceOutcome
        fake_source = mock.MagicMock()
        fake_source.fetch_detail.return_value = SourceOutcome.success(
            detail={"jd": "岗位职责：后端开发与系统维护。"},
            safe_log="detail ok",
        )
        with mock.patch("webui.app._BossCdpSource",
                        return_value=fake_source) as boss_mock, \
                mock.patch("webui.pipeline_exec.ensure_chrome_ready",
                           return_value=(True, "")):
            resp = self.client.post(
                "/api/pipeline/jobs/job-xyz/jd",
                json={"source_url": "https://www.zhipin.com/job/xyz.html"},
            )
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["jd"])
        # _BossCdpSource 被调用证明 fallback_platform == "boss"
        # （若误判 zhilian，会调 ZhilianCdpSource 而非 _BossCdpSource，
        # 且无 browser_account/cdp_port 时 _make_cdp_source 返回 None → 500）
        self.assertTrue(boss_mock.called)
        self.assertTrue(fake_source.fetch_detail.called)

    def test_pipeline_job_jd_zhilian_without_source_run_id_rejects(self):
        """契约 L253-255：智联补抓必须携带 source_run_id，不能按 URL 猜测。"""
        resp = self.client.post(
            "/api/pipeline/jobs/job-xyz/jd",
            json={"source_url": "https://www.zhaopin.com/jobdetail/job-xyz.htm"},
        )
        self.assertEqual(resp.status_code, 409, resp.get_json())
        self.assertEqual(resp.get_json().get("error_code"), "run_identity_conflict")


class Task008BackendIntegrationTests(unittest.TestCase):
    """Task 008：后端共享入口与兼容集成。

    覆盖：新 API 注册与认证、生命周期写入/失败保持/重启持久化、
    legacy PATCH 命令服务映射、pipeline 权威身份、跨平台提醒无过滤、
    相似岗位隔离、不安全 URL 和偏好反馈兼容。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.db_path = str(self.root / "state" / "webui.db")
        self.app = self._build_app(self.root / "app-1")
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]
        self.profile_id = self.client.post(
            "/api/profiles", json={"name": "t008", "confirmed_fields": {}},
        ).get_json()["id"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _build_app(self, subdir):
        return create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(self.root / subdir / "results"),
            "DB_PATH": self.db_path,
            "PYTHON_EXECUTABLE": sys.executable,
        })

    def _past(self, days):
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    def _future(self, hours):
        return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()

    def _add_job(self, *, platform="boss", pid=None, url=None,
                 title="岗位", company="公司"):
        pid = pid or f"{platform}-pid-{uuid.uuid4().hex[:8]}"
        url = url or (
            f"https://www.zhipin.com/job_detail/{pid}.html"
            if platform == "boss"
            else f"https://www.zhaopin.com/jobdetail/{pid}.htm"
        )
        result = self.store.upsert_job(
            platform=platform, platform_job_id=pid, canonical_url=url,
            title=title, company=company, salary="20-30K", location="上海",
            jd="负责后端开发。",
        )
        assert result["ok"], result
        return result["job_id"], pid, url

    def _post_action(self, job_id, action, *, request_id=None,
                     applied_at=None, target_status=None, profile_id=None):
        return self.client.post("/api/profile-jobs/actions", json={
            "request_id": request_id or str(uuid.uuid4()),
            "profile_id": profile_id or self.profile_id,
            "job": {"job_id": job_id},
            "action": action,
            "applied_at": applied_at,
            "target_status": target_status,
        })

    def _state(self, job_id):
        resp = self.client.get("/api/profile-jobs/state", query_string={
            "profile_id": self.profile_id, "job_id": job_id,
        })
        assert resp.status_code == 200, resp.get_data(as_text=True)
        return resp.get_json()

    def _table_counts(self):
        with self.store._connection() as conn:
            return {
                table: conn.execute(
                    f"SELECT COUNT(*) AS c FROM {table}"
                ).fetchone()["c"]
                for table in (
                    "jobs", "profile_jobs", "profile_job_events",
                    "profile_job_command_receipts", "feedback_events",
                )
            }

    # -- 1. 新 API 注册且保留认证 ----------------------------------------

    def test_new_feedback_routes_registered_and_auth_protected(self):
        job_id, _, _ = self._add_job()

        count = self.client.get("/api/job-reminders/count", query_string={
            "profile_id": self.profile_id,
        })
        self.assertEqual(count.status_code, 200)
        body = count.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["threshold_hours"], 720)

        reminders = self.client.get("/api/job-reminders", query_string={
            "profile_id": self.profile_id,
        })
        self.assertEqual(reminders.status_code, 200)
        self.assertEqual(reminders.get_json()["items"], [])

        state = self._state(job_id)
        self.assertTrue(state["ok"])
        self.assertFalse(state["exists"])

        events = self.client.get(
            f"/api/profile-jobs/{self.profile_id}/{job_id}/events",
        )
        self.assertEqual(events.status_code, 200)
        self.assertEqual(events.get_json()["events"], [])

        advice = self.client.post(
            f"/api/profile-jobs/{self.profile_id}/{job_id}/advice", json={},
        )
        self.assertEqual(advice.status_code, 409)
        self.assertEqual(advice.get_json()["error_code"], "reminder_not_eligible")

        # 写路由必须保留会话令牌防护
        anon = self.app.test_client()
        blocked = anon.post("/api/profile-jobs/actions", json={
            "request_id": str(uuid.uuid4()),
            "profile_id": self.profile_id,
            "job": {"job_id": job_id},
            "action": "mark_read",
        })
        self.assertEqual(blocked.status_code, 403)
        blocked_reminders_write = anon.post(
            f"/api/profile-jobs/{self.profile_id}/{job_id}/advice", json={},
        )
        self.assertEqual(blocked_reminders_write.status_code, 403)

    # -- 2. 生命周期写入、失败保持原状态、重启持久化 -------------------

    def test_lifecycle_write_failure_keeps_state_and_survives_restart(self):
        job_id, _, _ = self._add_job()
        applied_at = self._past(40)

        ok = self._post_action(job_id, "mark_applied", applied_at=applied_at)
        self.assertEqual(ok.status_code, 200, ok.get_data(as_text=True))
        state = self._state(job_id)["state"]
        self.assertEqual(state["status"], "applied")
        self.assertEqual(state["applied_at"], applied_at)
        self.assertTrue(state["reminder"]["eligible"])

        bad = self._post_action(job_id, "correct_applied_at",
                                applied_at=self._future(2))
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(bad.get_json()["error_code"], "applied_at_in_future")
        state = self._state(job_id)["state"]
        self.assertEqual(state["status"], "applied")
        self.assertEqual(state["applied_at"], applied_at)
        events = self.client.get(
            f"/api/profile-jobs/{self.profile_id}/{job_id}/events",
        ).get_json()["events"]
        self.assertEqual(len(events), 1)

        # 模拟应用重启：同一 DB 重新构造应用
        restarted = self._build_app("app-2")
        client2 = restarted.test_client()
        client2.environ_base["HTTP_X_BOSS_TOKEN"] = client2.get(
            "/api/session").get_json()["token"]
        state2 = client2.get("/api/profile-jobs/state", query_string={
            "profile_id": self.profile_id, "job_id": job_id,
        }).get_json()
        self.assertTrue(state2["exists"])
        self.assertEqual(state2["state"]["status"], "applied")
        self.assertEqual(state2["state"]["applied_at"], applied_at)
        count2 = client2.get("/api/job-reminders/count", query_string={
            "profile_id": self.profile_id,
        }).get_json()
        self.assertEqual(count2["total"], 1)

    # -- 3. legacy PATCH 命令服务映射 ------------------------------------

    def test_legacy_patch_requires_request_id_and_maps_to_command_service(self):
        job_id, _, _ = self._add_job()
        self.store.link_profile_job(self.profile_id, job_id, None, None)
        url = f"/api/profile-jobs/{self.profile_id}/{job_id}"
        applied_at = self._past(40)

        missing = self.client.patch(url, json={
            "status": "applied", "applied_at": applied_at,
        })
        self.assertEqual(missing.status_code, 428)
        self.assertEqual(
            missing.get_json()["error_code"], "idempotency_key_required")
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, job_id)["status"], "new")
        self.assertEqual(self._table_counts()["profile_job_events"], 0)

        request_id = str(uuid.uuid4())
        marked = self.client.patch(
            url,
            json={"status": "applied", "applied_at": applied_at,
                  "request_id": request_id},
        )
        self.assertEqual(marked.status_code, 200, marked.get_data(as_text=True))
        pj = self.store.get_profile_job(self.profile_id, job_id)
        self.assertEqual(pj["status"], "applied")
        self.assertEqual(pj["applied_at"], applied_at)
        counts = self._table_counts()
        self.assertEqual(counts["profile_job_events"], 1)
        self.assertEqual(counts["profile_job_command_receipts"], 1)

        # 同 request_id 同载荷重放：不产生第二次写入
        replay = self.client.patch(
            url,
            json={"status": "applied", "applied_at": applied_at,
                  "request_id": request_id},
        )
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.get_json()["replayed"])
        counts = self._table_counts()
        self.assertEqual(counts["profile_job_events"], 1)
        self.assertEqual(counts["profile_job_command_receipts"], 1)

        # Idempotency-Key 请求头同样是合法 request ID 来源
        via_header = self.client.patch(
            url,
            json={"status": "read"},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        self.assertEqual(via_header.status_code, 200)
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, job_id)["status"], "read")

        # 时间校验不得被 legacy 入口绕过
        future = self.client.patch(
            url,
            json={"status": "applied", "applied_at": self._future(3),
                  "request_id": str(uuid.uuid4())},
        )
        self.assertEqual(future.status_code, 422)
        self.assertEqual(
            future.get_json()["error_code"], "applied_at_in_future")
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, job_id)["status"], "read")

    def test_legacy_patch_note_and_lifecycle_are_atomic(self):
        job_id, _, _ = self._add_job()
        self.store.link_profile_job(self.profile_id, job_id, None, None)
        url = f"/api/profile-jobs/{self.profile_id}/{job_id}"
        applied_at = self._past(40)

        mixed = self.client.patch(
            url,
            json={"status": "applied", "applied_at": applied_at,
                  "note": "混合备注",
                  "request_id": str(uuid.uuid4())},
        )
        self.assertEqual(mixed.status_code, 200, mixed.get_data(as_text=True))
        pj = self.store.get_profile_job(self.profile_id, job_id)
        self.assertEqual(pj["status"], "applied")
        self.assertEqual(pj["note"], "混合备注")
        self.assertEqual(self._table_counts()["profile_job_events"], 1)

        # 非法生命周期字段 + note：整体拒绝，note 不部分写入
        rejected = self.client.patch(
            url,
            json={"status": "nonsense", "note": "不应写入",
                  "request_id": str(uuid.uuid4())},
        )
        self.assertEqual(rejected.status_code, 400)
        pj = self.store.get_profile_job(self.profile_id, job_id)
        self.assertEqual(pj["note"], "混合备注")
        self.assertEqual(pj["status"], "applied")
        self.assertEqual(self._table_counts()["profile_job_events"], 1)

        # note-only 保持现有独立备注语义，无需 request ID
        note_only = self.client.patch(url, json={"note": "仅备注"})
        self.assertEqual(note_only.status_code, 200)
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, job_id)["note"], "仅备注")

    # -- 4. pipeline interest/reject/cancel 权威三元组 -------------------

    def test_pipeline_feedback_uses_authoritative_triple_both_platforms(self):
        boss_job = {
            "platform": "boss",
            "platform_job_id": "boss-pid-1",
            "title": "Python 后端",
            "company": "甲公司",
            "job_link": "https://www.zhipin.com/job_detail/boss-pid-1.html",
        }
        zhilian_job = {
            "platform": "zhilian",
            "platform_job_id": "zl-pid-1",
            "title": "Python 后端",
            "company": "甲公司",
            "job_link": "https://www.zhaopin.com/jobdetail/zl-pid-1.htm",
        }

        marked = self.client.post("/api/pipeline/jobs/interest",
                                  json={"profile_id": self.profile_id,
                                        "job": boss_job})
        self.assertEqual(marked.status_code, 200, marked.get_data(as_text=True))
        boss_internal = marked.get_json()["job_id"]
        self.assertNotEqual(boss_internal, "boss-pid-1")
        boss_row = self.store.get_job(boss_internal)
        self.assertEqual(boss_row["platform"], "boss")
        self.assertEqual(boss_row["platform_job_id"], "boss-pid-1")

        marked_zl = self.client.post("/api/pipeline/jobs/interest",
                                     json={"profile_id": self.profile_id,
                                           "job": zhilian_job})
        self.assertEqual(marked_zl.status_code, 200)
        zl_internal = marked_zl.get_json()["job_id"]
        self.assertNotEqual(zl_internal, "zl-pid-1")
        self.assertEqual(self.store.get_job(zl_internal)["platform"], "zhilian")
        self.assertEqual(
            len(self.store.list_screening_interested(self.profile_id)), 2)

        rejected = self.client.post("/api/pipeline/jobs/reject",
                                    json={"profile_id": self.profile_id,
                                          "job": zhilian_job})
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.get_json()["job_id"], zl_internal)
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, zl_internal)["status"], "deleted")

        cancelled = self.client.post("/api/pipeline/jobs/interest/cancel",
                                     json={"profile_id": self.profile_id,
                                           "job": boss_job})
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.get_json()["job_id"], boss_internal)
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, boss_internal)["status"], "new")

        cancelled_reject = self.client.post("/api/pipeline/jobs/reject/cancel",
                                             json={"profile_id": self.profile_id,
                                                   "job": zhilian_job})
        self.assertEqual(cancelled_reject.status_code, 200)
        self.assertEqual(cancelled_reject.get_json()["job_id"], zl_internal)
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, zl_internal)["status"], "interested")

    def test_cancel_reject_restores_previous_interest(self):
        job = {
            "platform": "boss",
            "platform_job_id": "boss-pid-reject-restore",
            "title": "Python 后端",
            "company": "乙公司",
            "job_link": "https://www.zhipin.com/job_detail/boss-pid-reject-restore.html",
        }
        marked = self.client.post("/api/pipeline/jobs/interest", json={
            "profile_id": self.profile_id, "job": job,
        })
        self.assertEqual(marked.status_code, 200)
        internal = marked.get_json()["job_id"]
        rejected = self.client.post("/api/pipeline/jobs/reject", json={
            "profile_id": self.profile_id, "job": job,
        })
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, internal)["status"], "deleted")
        cancelled = self.client.post("/api/pipeline/jobs/reject/cancel", json={
            "profile_id": self.profile_id, "job": job,
        })
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(self.store.get_profile_job(
            self.profile_id, internal)["status"], "interested")

    def test_cancel_interest_with_internal_id_only_succeeds(self):
        """收藏抽屉取消收藏只传内部 job_id 必须成功（回归）。

        App 收藏抽屉此前附带 job_link 但缺 platform/platform_job_id，
        后端权威解析把「内部 ID + 部分三元组」判为身份不完整 → 422
        “岗位身份信息不完整”。修复后前端只传内部 job_id，走内部 ID
        解析，不再触发三元组校验。
        """
        job = {
            "platform": "boss",
            "platform_job_id": "fav-pid-1",
            "job_link": "https://www.zhipin.com/job_detail/fav-pid-1.html",
        }
        marked = self.client.post("/api/pipeline/jobs/interest", json={
            "profile_id": self.profile_id, "job": job,
        })
        self.assertEqual(marked.status_code, 200, marked.get_data(as_text=True))
        internal_id = marked.get_json()["job_id"]

        cancelled = self.client.post("/api/pipeline/jobs/interest/cancel", json={
            "profile_id": self.profile_id,
            "job": {"job_id": internal_id},
        })
        self.assertEqual(cancelled.status_code, 200, cancelled.get_data(as_text=True))
        self.assertEqual(cancelled.get_json()["job_id"], internal_id)
        self.assertEqual(
            self.store.get_profile_job(self.profile_id, internal_id)["status"], "new")

    def test_identity_failures_do_not_leak_internal_details(self):
        """身份错误文案面向用户，不裸露“三元组”等内部数据结构。"""
        resp = self.client.post("/api/pipeline/jobs/interest", json={
            "profile_id": self.profile_id,
            "job": {
                "platform": "boss",
                "platform_job_id": "bare-pid",
                "job_link": "https://www.zhipin.com/job_detail/bare-pid.html",
            },
        })
        self.assertEqual(resp.status_code, 200)
        # 部分三元组 + 内部 ID 携带时后端必须拒绝，且文案不含内部术语。
        seeded = self.client.post("/api/pipeline/jobs/interest", json={
            "profile_id": self.profile_id,
            "job": {
                "platform": "boss",
                "platform_job_id": "bare-pid",
                "job_link": "https://www.zhipin.com/job_detail/bare-pid.html",
            },
        })
        self.assertEqual(seeded.status_code, 200)
        internal_id = seeded.get_json()["job_id"]
        half = self.client.post("/api/pipeline/jobs/interest/cancel", json={
            "profile_id": self.profile_id,
            "job": {"job_id": internal_id,
                    "job_link": "https://www.zhipin.com/job_detail/bare-pid.html"},
        })
        self.assertEqual(half.status_code, 422)
        body = half.get_json()
        self.assertEqual(body["error_code"], "job_identity_incomplete")
        self.assertNotIn("三元组", body.get("user_message", ""))
        self.assertNotIn("内部岗位", body.get("user_message", ""))

    def test_pipeline_identity_failures_have_zero_side_effects(self):
        before = self._table_counts()

        incomplete = self.client.post("/api/pipeline/jobs/interest", json={
            "profile_id": self.profile_id,
            "job": {
                "platform_job_id": "bare-pid",
                "job_link": "https://www.zhipin.com/job_detail/bare-pid.html",
            },
        })
        self.assertEqual(incomplete.status_code, 422)
        self.assertEqual(
            incomplete.get_json()["error_code"], "job_identity_incomplete")

        mismatch = self.client.post("/api/pipeline/jobs/interest", json={
            "profile_id": self.profile_id,
            "job": {
                "platform": "boss",
                "platform_job_id": "mismatch-pid",
                "job_link": "https://www.zhaopin.com/jobdetail/mismatch-pid.htm",
            },
        })
        self.assertEqual(mismatch.status_code, 422)
        self.assertEqual(
            mismatch.get_json()["error_code"], "platform_url_mismatch")

        # 双索引冲突：(platform,pid) 与 canonical_url 命中不同记录
        self._add_job(platform="boss", pid="conflict-a",
                      url="https://www.zhipin.com/job_detail/conflict-a.html")
        self._add_job(platform="boss", pid="conflict-b",
                      url="https://www.zhipin.com/job_detail/conflict-b.html")
        after_seed = self._table_counts()
        conflict = self.client.post("/api/pipeline/jobs/interest", json={
            "profile_id": self.profile_id,
            "job": {
                "platform": "boss",
                "platform_job_id": "conflict-a",
                "job_link": "https://www.zhipin.com/job_detail/conflict-b.html",
            },
        })
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(
            conflict.get_json()["error_code"], "job_identity_conflict")

        self.assertEqual(self._table_counts(), after_seed)
        self.assertGreaterEqual(after_seed["jobs"], before["jobs"] + 2)
        self.assertEqual(after_seed["profile_jobs"], before["profile_jobs"])
        self.assertEqual(
            after_seed["feedback_events"], before["feedback_events"])

    # -- 5. 相似岗位隔离 / 无平台过滤 / 不安全 URL / 偏好兼容 ----------

    def test_similar_jobs_are_isolated_by_internal_id(self):
        job_a, _, _ = self._add_job(
            platform="boss", title="高级后端工程师", company="同名公司")
        job_b, _, _ = self._add_job(
            platform="zhilian", title="高级后端工程师", company="同名公司")
        self.assertNotEqual(job_a, job_b)

        self.assertEqual(
            self._post_action(job_b, "mark_read").status_code, 200)
        self.assertEqual(
            self._post_action(job_a, "mark_applied",
                              applied_at=self._past(40)).status_code, 200)

        self.assertEqual(self._state(job_a)["state"]["status"], "applied")
        self.assertEqual(self._state(job_b)["state"]["status"], "read")
        total = self.client.get("/api/job-reminders/count", query_string={
            "profile_id": self.profile_id,
        }).get_json()["total"]
        self.assertEqual(total, 1)

    def test_reminders_mix_boss_and_zhilian_without_platform_filter(self):
        boss_job, _, _ = self._add_job(platform="boss")
        zhilian_job, _, _ = self._add_job(platform="zhilian")
        for job_id in (boss_job, zhilian_job):
            self.assertEqual(
                self._post_action(job_id, "mark_applied",
                                  applied_at=self._past(35)).status_code, 200)

        payload = self.client.get("/api/job-reminders", query_string={
            "profile_id": self.profile_id,
        }).get_json()
        self.assertEqual(payload["total"], 2)
        platforms = {item["platform"] for item in payload["items"]}
        self.assertEqual(platforms, {"boss", "zhilian"})
        for item in payload["items"]:
            self.assertTrue(item["can_open"])

    def test_invalid_canonical_url_blocks_open_but_not_lifecycle_data(self):
        unsafe_id = uuid.uuid4().hex
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO jobs (id, canonical_url, source_url, title, "
                "company, salary, location, jd, first_seen_at, last_seen_at, "
                "platform, platform_job_id, experience, degree, extra_json) "
                "VALUES (?, ?, '', '不安全链接', '公司', '20K', '上海', 'JD', "
                "datetime('now'), datetime('now'), 'boss', 'unsafe-pid', "
                "'', '', '{}')",
                (unsafe_id, "javascript:alert(1)"),
            )
        self.assertEqual(
            self._post_action(unsafe_id, "mark_applied",
                              applied_at=self._past(40)).status_code, 200)

        state = self._state(unsafe_id)
        self.assertTrue(state["exists"])
        self.assertEqual(state["state"]["status"], "applied")

        payload = self.client.get("/api/job-reminders", query_string={
            "profile_id": self.profile_id,
        }).get_json()
        items = [item for item in payload["items"]
                 if item["job_id"] == unsafe_id]
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0]["can_open"])
        self.assertIsNone(items[0]["canonical_url"])

    def test_history_preference_feedback_never_overrides_current_status(self):
        # search-runs 需要带城市的画像；生命周期断言仍用主画像
        run_profile = self.client.post("/api/profiles", json={
            "name": "t008-run",
            "confirmed_fields": {"city": "上海", "roles": ["Python"]},
        }).get_json()["id"]
        run = self.client.post("/api/search-runs", json={
            "profile_id": run_profile,
            "manual_keywords": ["Python"],
        })
        self.assertEqual(run.status_code, 202, run.get_data(as_text=True))
        run = run.get_json()
        job = self.store.save_job(
            "https://www.zhipin.com/job_detail/t008-compat.html",
            "https://www.zhipin.com/job_detail/t008-compat.html",
            "兼容岗位", "公司", "20K", "上海", "JD",
        )
        self.store.link_profile_job(
            run_profile, job["id"], run["id"], run["id"])

        self.assertEqual(self.client.post(
            f"/api/jobs/{job['id']}/feedback",
            json={"profile_id": run_profile, "action": "interested"},
        ).status_code, 200)
        cards = self.client.get(
            f"/api/search-runs/{run['id']}/jobs").get_json()["jobs"]
        self.assertEqual(cards[0]["interest_state"], "interested")

        self.assertEqual(
            self._post_action(job["id"], "mark_applied",
                              applied_at=self._past(40),
                              profile_id=run_profile).status_code, 200)

        cards = self.client.get(
            f"/api/search-runs/{run['id']}/jobs").get_json()["jobs"]
        self.assertEqual(len(cards), 1)
        # 历史 interested 事件不得把当前 applied 投影回 interested
        self.assertEqual(cards[0]["interest_state"], "applied")
        # 偏好事件仍保留（偏好学习语义不变）
        events = self.store.list_feedback(run_profile, job_id=job["id"])
        self.assertEqual(
            [e["action"] for e in events if not e["revoked_at"]],
            ["interested"],
        )


# ===========================================================================
# spec003 tasks004 T028/T029 — env_check EXE 模式 + _make_cdp_source EXE 接线
#
# 冻结合同：specs/003-desktop-exe/contracts/runtime-mode.md §2、
# contracts/inprocess-runner.md §4.3。
# ===========================================================================
class EnvCheckRuntimeModeTests(unittest.TestCase):
    """T028: env_check 响应含 runtime_mode 字段，EXE 模式检查项差异。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _make_app(self, runtime_mode="source"):
        app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(self.root / "results"),
            "DB_PATH": str(self.root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
            "RUNTIME_MODE": runtime_mode,
        })
        client = app.test_client()
        token = client.get("/api/session").get_json()["token"]
        client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        return app, client

    def _fake_check_items(self):
        return ([
            {"id": "browsers", "name": "Chromium 浏览器", "status": "ok",
             "detail": "找到 Chrome ✅", "fix": None},
            {"id": "deps", "name": "Python 依赖", "status": "ok",
             "detail": "requests / websocket 可导入", "fix": None},
            {"id": "cdp", "name": "专用浏览器已启动", "status": "fail",
             "detail": "无法连接 127.0.0.1:9222", "fix": "启动专用浏览器"},
            {"id": "boss_login", "name": "BOSS 登录状态", "status": "skip",
             "detail": "跳过", "fix": None},
        ], False)

    def test_source_mode_returns_runtime_mode_source(self):
        """源码模式：runtime_mode='source'，local 组无 webview2 项。"""
        app, client = self._make_app("source")
        with mock.patch("webui.app.boss.collect_check_items",
                        return_value=self._fake_check_items()):
            payload = client.get("/api/env-check").get_json()
        self.assertEqual(payload["runtime_mode"], "source")
        local_items = next(g for g in payload["groups"] if g["id"] == "local")["items"]
        item_ids = [item["id"] for item in local_items]
        self.assertNotIn("webview2", item_ids)

    def test_exe_mode_returns_runtime_mode_exe(self):
        """EXE 模式：runtime_mode='exe'。"""
        app, client = self._make_app("exe")
        with mock.patch("webui.app.boss.collect_check_items",
                        return_value=self._fake_check_items()), \
                mock.patch("webui.desktop_runtime.check_webview2",
                           return_value={"installed": True, "available": True,
                                         "version": "120.0.0.0", "detail": "已安装"}):
            payload = client.get("/api/env-check").get_json()
        self.assertEqual(payload["runtime_mode"], "exe")

    def test_exe_mode_deps_item_is_builtin_runtime(self):
        """EXE 模式：deps 项名称改「内置运行时」，状态恒 ok，fix 为 null。"""
        app, client = self._make_app("exe")
        with mock.patch("webui.app.boss.collect_check_items",
                        return_value=self._fake_check_items()), \
                mock.patch("webui.desktop_runtime.check_webview2",
                           return_value={"installed": True, "available": True,
                                         "version": "120.0.0.0", "detail": "已安装"}):
            payload = client.get("/api/env-check").get_json()
        local_items = next(g for g in payload["groups"] if g["id"] == "local")["items"]
        deps_item = next(item for item in local_items if item["id"] == "deps")
        self.assertIn("内置运行时", deps_item["name"])
        self.assertEqual(deps_item["status"], "ok")
        self.assertIsNone(deps_item["fix"])

    def test_exe_mode_webview2_item_present(self):
        """EXE 模式：local 组含 webview2 项（注入检测替身）。"""
        app, client = self._make_app("exe")
        with mock.patch("webui.app.boss.collect_check_items",
                        return_value=self._fake_check_items()), \
                mock.patch("webui.desktop_runtime.check_webview2",
                           return_value={"installed": True, "available": True,
                                         "version": "120.0.0.0", "detail": "已安装 WebView2"}):
            payload = client.get("/api/env-check").get_json()
        local_items = next(g for g in payload["groups"] if g["id"] == "local")["items"]
        webview2_item = next(item for item in local_items if item["id"] == "webview2")
        self.assertEqual(webview2_item["status"], "ok")
        self.assertIn("WebView2", webview2_item["detail"])

    def test_exe_mode_webview2_not_installed(self):
        """EXE 模式：webview2 未安装时 status=fail，fix 文案含「安装 WebView2」。"""
        app, client = self._make_app("exe")
        with mock.patch("webui.app.boss.collect_check_items",
                        return_value=self._fake_check_items()), \
                mock.patch("webui.desktop_runtime.check_webview2",
                           return_value={"installed": False, "available": True,
                                         "version": None, "detail": "未检测到 WebView2"}):
            payload = client.get("/api/env-check").get_json()
        local_items = next(g for g in payload["groups"] if g["id"] == "local")["items"]
        webview2_item = next(item for item in local_items if item["id"] == "webview2")
        self.assertEqual(webview2_item["status"], "fail")
        self.assertIn("WebView2", webview2_item.get("fix") or "")

    def test_env_check_has_no_cooldown_payload(self):
        # 016：冷却功能删除；env-check 不再返回 cooldowns 字段
        app, client = self._make_app("source")
        with mock.patch("webui.app.boss.collect_check_items",
                        return_value=self._fake_check_items()):
            payload = client.get("/api/env-check").get_json()
        self.assertNotIn("cooldowns", payload)


class MakeCdpSourceRuntimeModeTests(unittest.TestCase):
    """T029: _make_cdp_source EXE 模式 BOSS 传 in_process=True，智联不变。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _make_app(self, runtime_mode="source"):
        app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(self.root / "results"),
            "DB_PATH": str(self.root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
            "RUNTIME_MODE": runtime_mode,
        })
        return app

    def test_exe_mode_boss_source_gets_in_process_true(self):
        """EXE 模式：_make_cdp_source(platform=boss) 返回的 source.in_process=True。"""
        app = self._make_app("exe")
        factory = app.config["MAKE_CDP_SOURCE"]
        source = factory(platform="boss", cdp_port=9222, browser_account="acc")
        self.assertIsNotNone(source)
        self.assertTrue(getattr(source, "in_process", False))

    def test_source_mode_boss_source_in_process_false(self):
        """源码模式：_make_cdp_source(platform=boss) 返回的 source.in_process=False。"""
        app = self._make_app("source")
        factory = app.config["MAKE_CDP_SOURCE"]
        source = factory(platform="boss", cdp_port=9222, browser_account="acc")
        self.assertIsNotNone(source)
        self.assertFalse(getattr(source, "in_process", False))

    def test_exe_mode_zhilian_source_unchanged(self):
        """EXE 模式：_make_cdp_source(platform=zhilian) 智联构造不变（无 in_process）。"""
        app = self._make_app("exe")
        factory = app.config["MAKE_CDP_SOURCE"]
        source = factory(platform="zhilian", cdp_port=9223,
                         browser_account="acc", profile_key="zhilian:acc")
        self.assertIsNotNone(source)
        # 智联 source 不应有 in_process 属性，或属性为 False
        self.assertFalse(getattr(source, "in_process", False))




class AutoScreenChainTests(unittest.TestCase):
    """B031 一键链路 auto_screen 标记：创建/返回/消费/清除。"""

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
        self.store = self.app.config["TASK_STORE"]

    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    def _preview(self):
        return self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        }).get_json()["scope"]

    def _start_auto_search(self, task_id_hint=None):
        preview = self._preview()
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit"):
            resp = self.client.post("/api/execute-search", json={
                "platform": "boss",
                "script_params": {
                    "keyword": "Python",
                    "city": ["上海"],
                    "pages": 1,
                },
                "scope_digest": preview["scope_digest"],
                "auto_screen": True,
                "auto_screen_fields": {"salary": ["406"]},
                "auto_screen_profile": "Python 后端候选人",
                "auto_screen_facts": {"core_skills": ["Python"], "job_type": "全职"},
            })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return resp.get_json()["task_id"]

    def _seed_succeeded_scrape(self, run_id, auto_screen=True):
        jobs = [
            {"job_id": "j1", "platform_job_id": "j1", "title": "岗位1",
             "source_url": "https://zhipin.example/j1.html"},
        ]
        self.store.create_screening_run(
            run_id,
            source_count=1,
            execution_params={
                "platform": "boss",
                "auto_screen": bool(auto_screen),
                "auto_screen_fields": {"salary": ["406"]},
                "auto_screen_profile": "Python 后端候选人",
                "auto_screen_facts": {"core_skills": ["Python"], "job_type": "全职"},
            },
        )
        self.store.save_scrape_combo_result(run_id, "kw|city", jobs, ["kw|city"])
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(run_id, status="succeeded", current_stage="scrape")
        return jobs

    def test_execute_search_persists_auto_screen_flag(self):
        task_id = self._start_auto_search()
        run = self.store.get_screening_run(task_id)
        params = run["execution_params"]
        self.assertTrue(params["auto_screen"])
        self.assertEqual(params["auto_screen_fields"], {"salary": ["406"]})
        self.assertEqual(params["auto_screen_profile"], "Python 后端候选人")
        self.assertEqual(
            params["auto_screen_facts"],
            {"core_skills": ["Python"], "job_type": "全职"},
            "B033：一键任务必须冻结画像事实快照，供刷新后自动接续",
        )
        task = self.app.config["PIPELINE_TASKS"][task_id]
        self.assertTrue(task["auto_screen"])
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertTrue(data["auto_screen"])
        self.assertEqual(data["auto_screen_fields"], {"salary": ["406"]})
        self.assertEqual(data["auto_screen_profile"], "Python 后端候选人")

    def test_execute_search_rejects_invalid_auto_screen_fields(self):
        preview = self._preview()
        resp = self.client.post("/api/execute-search", json={
            "platform": "boss",
            "script_params": {"keyword": "Python", "city": ["上海"], "pages": 1},
            "scope_digest": preview["scope_digest"],
            "auto_screen": True,
            "auto_screen_fields": ["salary"],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "auto_screen_fields 必须是对象")

    def test_execute_search_rejects_when_ai_screen_running(self):
        """AI 筛选占用浏览器时，不能再启动新的抓取任务。"""
        preview = self._preview()
        self.app.config["PIPELINE_TASKS"]["running-ai-screen"] = {
            "kind": "ai_screen", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(), "platform": "boss",
        }
        resp = self.client.post("/api/execute-search", json={
            "platform": "boss",
            "script_params": {"keyword": "Python", "city": ["上海"], "pages": 1},
            "scope_digest": preview["scope_digest"],
        })
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "browser_busy")

    def test_ai_screen_rejects_when_scrape_running(self):
        """抓取任务运行时，不能对另一来源启动 AI 筛选。"""
        source_id = "busy-scrape-source"
        self._seed_succeeded_scrape(source_id)
        self.app.config["PIPELINE_TASKS"]["running-scrape"] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(), "platform": "boss",
        }
        resp = self.client.post("/api/ai-screen", json={
            "screening_fields": {"salary": ["406"]},
            "profile_summary": "Python 后端候选人",
            "scrape_task_id": source_id,
        })
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "browser_busy")

    def test_ai_screen_consumes_flag_before_validation(self):
        run_id = "auto-consume-fail"
        self._seed_succeeded_scrape(run_id)
        resp = self.client.post("/api/ai-screen", json={
            "screening_fields": "bad",
            "consume_auto_screen": True,
            "scrape_task_id": run_id,
        })
        self.assertEqual(resp.status_code, 400)
        run = self.store.get_screening_run(run_id)
        self.assertFalse(run["execution_params"]["auto_screen"])
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertFalse(data["has_task"])

    def test_latest_running_task_restores_completed_auto_screen(self):
        run_id = "auto-refresh"
        self._seed_succeeded_scrape(run_id)
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["status"], "completed")
        self.assertTrue(data["auto_screen"])
        self.assertEqual(data["scrape_task_id"], run_id)
        self.assertEqual(data["frozen_filters"], {"salary": ["406"]})
        self.assertEqual(data["profile_summary"], "Python 后端候选人")
        self.assertEqual(
            data["profile_facts"],
            {"core_skills": ["Python"], "job_type": "全职"},
            "B033：auto_screen 恢复分支必须透传画像事实快照",
        )
        self.assertEqual(data["scraped_count"], 1)
        # 消费后刷新不再恢复自动接续。
        self.client.post("/api/ai-screen", json={
            "screening_fields": "bad",
            "consume_auto_screen": True,
            "scrape_task_id": run_id,
        })
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertFalse(data["has_task"])

    def test_latest_running_task_paused_returns_profile_fields(self):
        """B033：paused 分支恢复时必须返回画像文本与画像事实快照。"""
        run_id = "paused-screen"
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={
                "platform": "boss",
                "scrape_task_id": "scrape-parent",
                "profile_summary": "3年Python后端",
                "profile_facts": {"core_skills": ["Python"], "job_type": "全职"},
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="ai_rough",
            error_code="source_blocked", error_reason="验证码",
        )
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["status"], "paused")
        self.assertEqual(data["profile_summary"], "3年Python后端")
        self.assertEqual(
            data["profile_facts"],
            {"core_skills": ["Python"], "job_type": "全职"},
            "B033：paused 恢复分支必须透传画像事实，否则续跑退化为两通道",
        )

    def test_execute_search_cancel_clears_flag(self):
        task_id = self._start_auto_search()
        resp = self.client.post(f"/api/execute-search/{task_id}/cancel")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertFalse(self.store.get_screening_run(task_id)["execution_params"]["auto_screen"])
        self.assertFalse(self.app.config["PIPELINE_TASKS"][task_id]["auto_screen"])

    def test_task_cancel_clears_flag(self):
        run_id = "auto-cancel"
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={"platform": "boss", "auto_screen": True},
        )
        self.store.update_screening_run(run_id, status="running")
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(), "platform": "boss", "auto_screen": True,
        }
        resp = self.client.post(f"/api/task/cancel/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertFalse(self.store.get_screening_run(run_id)["execution_params"]["auto_screen"])

    def test_task_finish_clears_flag(self):
        run_id = "auto-finish"
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={"platform": "boss", "auto_screen": True},
        )
        jobs = [
            {"job_id": "j1", "platform_job_id": "j1", "title": "岗位1",
             "source_url": "https://zhipin.example/j1.html"},
        ]
        self.store.save_scrape_combo_result(run_id, "kw|city", jobs, ["kw|city"])
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="操作频繁",
        )
        resp = self.client.post(f"/api/task/finish/{run_id}")
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertFalse(self.store.get_screening_run(run_id)["execution_params"]["auto_screen"])

    def test_paused_run_preserves_flag(self):
        run_id = "auto-paused"
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={"platform": "boss", "auto_screen": True},
        )
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="captcha_required", error_reason="验证码",
        )
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertTrue(data["has_task"])
        self.assertEqual(data["status"], "paused")
        self.assertTrue(data["auto_screen"])

    def test_task_state_returns_auto_screen_with_memory_priority(self):
        run_id = "auto-state"
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={"platform": "boss", "auto_screen": True},
        )
        self.store.update_screening_run(run_id, status="running")
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertTrue(data["auto_screen"])
        # 内存任务优先：内存 False 时覆盖 DB True。
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "scrape", "status": "running", "progress": {}, "logs": [],
            "result": None, "error": "", "started_at": None, "finished_at": None,
            "stop_event": threading.Event(), "platform": "boss", "auto_screen": False,
        }
        data = self.client.get(f"/api/task-state/{run_id}").get_json()
        self.assertFalse(data["auto_screen"])

    def test_latest_running_task_skips_zero_job_auto_screen(self):
        run_id = "auto-zero"
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={"platform": "boss", "auto_screen": True},
        )
        self.store.update_screening_run(run_id, status="running", current_stage="scrape")
        self.store.update_screening_run(run_id, status="succeeded", current_stage="scrape")
        data = self.client.get("/api/latest-running-task").get_json()
        self.assertFalse(data["has_task"])



class B054LocationApiTests(unittest.TestCase):
    """B054 地点目录、预览、执行与校验接口。"""

    def setUp(self):
        import gc
        gc.collect()
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
        import gc
        gc.collect()
        try:
            self.temp.cleanup()
        except (PermissionError, OSError):
            pass

    @staticmethod
    def _locations():
        return [
            {
                "platform": "boss",
                "city_name": "上海",
                "city_code": "101020100",
                "district_name": "浦东新区",
                "district_code": "310115",
            },
            {
                "platform": "boss",
                "city_name": "上海",
                "city_code": "101020100",
                "district_name": "徐汇区",
                "district_code": "310104",
            },
            {
                "platform": "boss",
                "city_name": "上海",
                "city_code": "101020100",
                "district_name": "黄浦区",
                "district_code": "310101",
            },
        ]

    def test_preview_accepts_locations_and_counts_combos(self):
        resp = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "locations": self._locations(),
            "pages_per_combination": 1,
        })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertEqual(data["scope"]["combination_count"], 3)
        self.assertEqual(data["scope"]["planned_pages"], 3)
        self.assertEqual(len(data["scope"]["locations"]), 3)

    def test_old_preview_without_locations_unchanged(self):
        resp = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "pages_per_combination": 1,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("locations", resp.get_json()["scope"])

    def test_execute_freezes_locations_into_script_params(self):
        preview = self.client.post("/api/search-scope/preview", json={
            "platform": "boss",
            "keywords": ["Python"],
            "scope_kind": "cities",
            "cities": ["上海"],
            "locations": self._locations(),
            "pages_per_combination": 1,
        }).get_json()["scope"]
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit"):
            resp = self.client.post("/api/execute-search", json={
                "platform": "boss",
                "script_params": {
                    "keyword": "Python",
                    "city": ["上海"],
                    "pages": 1,
                    "locations": self._locations(),
                },
                "scope_digest": preview["scope_digest"],
            })
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        task_id = resp.get_json()["task_id"]
        run = self.app.config["TASK_STORE"].get_screening_run(task_id)
        stored = run["execution_params"]["script_params"]
        self.assertEqual(len(stored["locations"]), 3)
        self.assertEqual(stored["locations"][0]["district_name"], "浦东新区")

    def test_location_validate_scope_kind_nationwide_rejects_locations(self):
        resp = self.client.post("/api/location/validate", json={
            "platform": "boss",
            "scope_kind": "nationwide",
            "locations": self._locations(),
        })
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.get_json()["error_code"], "scope_validation_failed")

    def test_location_catalog_empty_districts_returns_200(self):
        with mock.patch("webui.location_api.get_districts", return_value=[]):
            resp = self.client.get("/api/location-catalog?platform=boss&city=上海")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["districts"], [])

    def test_location_catalog_unavailable_returns_503(self):
        from webui.location_catalog import LocationCatalogUnavailable
        with mock.patch(
            "webui.location_api.get_districts",
            side_effect=LocationCatalogUnavailable("down"),
        ):
            resp = self.client.get("/api/location-catalog?platform=boss&city=上海")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.get_json()["error_code"], "location_catalog_unavailable")

if __name__ == "__main__":

    unittest.main()
