"""US1-US4 API integration tests for the AI Job Workbench.

Covers: AI settings (key never echoed), profiles, resume upload/delete
(text never logged), token protection on sensitive GET routes, search
runs, feedback, history and cleanup.
"""

import io
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

from webui.app import create_app
from webui import ai as ai_service


class WorkbenchAPITestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.result_dir = self.root / "results"
        self.resume_dir = self.root / "resumes"
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(self.result_dir),
            "DB_PATH": str(self.root / "state" / "webui.db"),
            "PYTHON_EXECUTABLE": sys.executable,
            "RESUME_DIR": str(self.resume_dir),
        })
        self.client = self.app.test_client()
        session = self.client.get("/api/session")
        self.token = session.get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = self.token

    def tearDown(self):
        self.temp.cleanup()

    def _anon(self):
        """Return a client without the session token."""
        return self.app.test_client()


class AISettingsTests(WorkbenchAPITestBase):
    """T019/T020: key enters credential store, never echoed or logged."""

    def test_get_ai_settings_returns_no_key_or_credential_ref(self):
        resp = self.client.get("/api/ai-settings")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertNotIn("credential_ref", data)
        self.assertNotIn("api_key", data)
        self.assertIn("is_configured", data)

    def test_put_ai_settings_stores_key_and_does_not_echo(self):
        with mock.patch("webui.ai.store_api_key", return_value="cred-ref-123") as m_store:
            resp = self.client.put("/api/ai-settings", json={
                "endpoint_url": "https://api.example.com/v1",
                "api_key": "sk-secret-abc",
            })
        self.assertEqual(resp.status_code, 200)
        m_store.assert_called_once_with("https://api.example.com/v1", "sk-secret-abc")
        data = resp.get_json()
        self.assertNotIn("api_key", data)
        self.assertNotIn("credential_ref", data)
        self.assertTrue(data["is_configured"])

    def test_anonymous_get_ai_settings_rejected(self):
        resp = self._anon().get("/api/ai-settings")
        self.assertEqual(resp.status_code, 403)

    def test_test_connection_returns_safe_error_code(self):
        with mock.patch("webui.ai.store_api_key", return_value="cred-ref"):
            self.client.put("/api/ai-settings", json={
                "endpoint_url": "https://api.example.com/v1",
                "api_key": "sk-secret",
            })
        with mock.patch("webui.ai.retrieve_api_key", return_value="sk-secret"), \
             mock.patch("webui.ai.test_connection", return_value={"ok": False, "transport": "failed", "generation": "failed", "candidate_contract": "manual_required", "warning_codes": ["auth_failed"]}):
            resp = self.client.post("/api/ai-settings/test")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["warning_codes"], ["auth_failed"])
        self.assertEqual(data["transport"], "failed")

    def test_test_connection_returns_chinese_user_message_without_code(self):
        with mock.patch("webui.ai.store_api_key", return_value="cred-ref"):
            self.client.put("/api/ai-settings", json={
                "endpoint_url": "https://api.example.com/v1",
                "api_key": "sk-secret",
            })
        with mock.patch("webui.ai.retrieve_api_key", return_value="sk-secret"), \
             mock.patch("webui.ai.test_connection", return_value={
                 "ok": False, "transport": "failed", "generation": "failed",
                 "candidate_contract": "manual_required",
                 "warning_codes": ["auth_failed"],
             }):
            resp = self.client.post("/api/ai-settings/test")
        data = resp.get_json()
        self.assertEqual(data["user_message"], "API 密钥无效或已过期，请检查 AI 设置")
        self.assertNotIn("auth_failed", data["user_message"])

    def test_test_connection_unknown_code_falls_back_to_pure_chinese(self):
        with mock.patch("webui.ai.store_api_key", return_value="cred-ref"):
            self.client.put("/api/ai-settings", json={
                "endpoint_url": "https://api.example.com/v1",
                "api_key": "sk-secret",
            })
        with mock.patch("webui.ai.retrieve_api_key", return_value="sk-secret"), \
             mock.patch("webui.ai.test_connection", return_value={
                 "ok": False, "transport": "failed", "generation": "failed",
                 "candidate_contract": "manual_required",
                 "warning_codes": ["mystery_code"],
             }):
            resp = self.client.post("/api/ai-settings/test")
        data = resp.get_json()
        self.assertEqual(data["user_message"], "AI 服务调用失败，请检查设置后重试")
        self.assertNotIn("mystery_code", data["user_message"])

    def test_models_failure_returns_chinese_user_message_without_code(self):
        with mock.patch("webui.ai.store_api_key", return_value="cred-ref"):
            self.client.put("/api/ai-settings", json={
                "endpoint_url": "https://api.example.com/v1",
                "api_key": "sk-secret",
            })
        with mock.patch("webui.ai.retrieve_api_key", return_value="sk-secret"), \
             mock.patch("webui.ai.list_models",
                          side_effect=ai_service.AISecurityError("network_error")):
            resp = self.client.post("/api/ai-settings/models")
        self.assertEqual(resp.status_code, 502)
        data = resp.get_json()
        self.assertEqual(data["user_message"], "无法连接 AI 服务，请检查网络与地址配置")
        self.assertNotIn("network_error", data["user_message"])

    def test_models_unknown_code_falls_back_to_pure_chinese(self):
        with mock.patch("webui.ai.store_api_key", return_value="cred-ref"):
            self.client.put("/api/ai-settings", json={
                "endpoint_url": "https://api.example.com/v1",
                "api_key": "sk-secret",
            })
        with mock.patch("webui.ai.retrieve_api_key", return_value="sk-secret"), \
             mock.patch("webui.ai.list_models",
                          side_effect=ai_service.AISecurityError("mystery_code")):
            resp = self.client.post("/api/ai-settings/models")
        data = resp.get_json()
        self.assertEqual(data["user_message"], "AI 服务调用失败，请检查设置后重试")
        self.assertNotIn("mystery_code", data["user_message"])


class ProfileTests(WorkbenchAPITestBase):
    """T019: profile CRUD with manual field override."""

    def test_create_and_list_profiles(self):
        resp = self.client.post("/api/profiles", json={
            "name": "画像 A",
            "confirmed_fields": {"city": "上海", "roles": ["Python"]},
        })
        self.assertEqual(resp.status_code, 200)
        pid = resp.get_json()["id"]

        resp = self.client.get("/api/profiles")
        self.assertEqual(resp.status_code, 200)
        profiles = resp.get_json()["profiles"]
        self.assertTrue(any(p["id"] == pid for p in profiles))

    def test_patch_profile_updates_confirmed_fields(self):
        resp = self.client.post("/api/profiles", json={"name": "P"})
        pid = resp.get_json()["id"]
        resp = self.client.patch(f"/api/profiles/{pid}", json={
            "confirmed_fields": {"city": "北京", "min_salary": 25},
        })
        self.assertEqual(resp.status_code, 200)
        fields = resp.get_json()["confirmed_fields"]
        self.assertEqual(fields["city"], "北京")

    def test_copy_profile_excludes_ai_preference(self):
        source = self.client.post("/api/profiles", json={
            "name": "源",
            "confirmed_fields": {"city": "上海"},
        }).get_json()
        # Simulate AI negative preference via store
        store = self.app.config["TASK_STORE"]
        store.update_profile(source["id"], ai_preference={"negative_terms": ["外包"]})

        resp = self.client.post("/api/profiles", json={
            "name": "副本",
            "copy_from": source["id"],
        })
        self.assertEqual(resp.status_code, 200)
        copied = resp.get_json()
        self.assertEqual(copied["confirmed_fields"], {"city": "上海"})
        self.assertEqual(copied.get("ai_preference") or {}, {})


class ResumeUploadTests(WorkbenchAPITestBase):
    """T019/T020: upload, parse, delete; text never in logs or responses."""

    def _make_profile(self):
        resp = self.client.post("/api/profiles", json={"name": "测试"})
        return resp.get_json()["id"]

    def _upload_txt(self, profile_id, text="姓名：张三\n期望城市：上海\n技能：Python,FastAPI"):
        buf = io.BytesIO(text.encode("utf-8"))
        return self.client.post(
            f"/api/profiles/{profile_id}/resume",
            data={"file": (buf, "resume.txt")},
            content_type="multipart/form-data",
        )

    def test_upload_txt_returns_resume_id_without_text(self):
        pid = self._make_profile()
        with mock.patch("webui.ai.parse_resume", return_value={
            "profile_name": "张三", "city": "上海",
            "roles": ["Python"], "skills": ["Python", "FastAPI"],
            "keywords": ["Python 后端"], "suggestions": [],
        }):
            resp = self._upload_txt(pid)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("resume_id", data)
        self.assertNotIn("extracted_text", data)
        self.assertNotIn("content_hash", data)

    def test_resume_text_never_logged(self):
        pid = self._make_profile()
        secret = "SECRET_RESUME_MARKER_42"
        with mock.patch("webui.ai.parse_resume", return_value={
            "profile_name": "x", "city": "上海", "roles": [], "skills": [],
            "keywords": [], "suggestions": [],
        }):
            self._upload_txt(pid, text=f"姓名：{secret}")
        # Check task logs don't contain the resume text
        store = self.app.config["TASK_STORE"]
        with store._connection() as conn:
            rows = conn.execute("SELECT line FROM task_logs").fetchall()
        for row in rows:
            self.assertNotIn(secret, row["line"])

    def test_delete_resume_makes_it_unreadable(self):
        pid = self._make_profile()
        with mock.patch("webui.ai.parse_resume", return_value={
            "profile_name": "x", "city": "上海", "roles": [], "skills": [],
            "keywords": [], "suggestions": [],
        }):
            resp = self._upload_txt(pid)
        resume_id = resp.get_json()["resume_id"]

        resp = self.client.delete(f"/api/profiles/{pid}/resume")
        self.assertEqual(resp.status_code, 200)
        # File is gone
        store = self.app.config["TASK_STORE"]
        resume = store.get_resume(resume_id)
        self.assertIsNone(resume["extracted_text"])
        self.assertIsNone(resume["content_hash"])
        self.assertIsNone(resume["original_filename"])

    def test_delete_profile_removes_resume_file(self):
        pid = self._make_profile()
        with mock.patch("webui.ai.parse_resume", return_value={
            "profile_name": "张三", "city": "上海",
            "roles": ["Python"], "skills": ["Python"],
            "keywords": [], "suggestions": [],
        }):
            resp = self._upload_txt(pid)
        resume_id = resp.get_json()["resume_id"]
        store = self.app.config["TASK_STORE"]
        storage_path = store.get_resume(resume_id)["storage_path"]
        file_path = self.resume_dir / storage_path
        self.assertTrue(file_path.is_file())

        resp = self.client.delete(f"/api/profiles/{pid}")

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(file_path.exists())

    def test_delete_profile_rolls_back_when_file_move_fails(self):
        pid = self._make_profile()
        with mock.patch("webui.ai.parse_resume", return_value={
            "profile_name": "张三", "city": "上海",
            "roles": ["Python"], "skills": ["Python"],
            "keywords": [], "suggestions": [],
        }):
            resp = self._upload_txt(pid)
        resume_id = resp.get_json()["resume_id"]
        store = self.app.config["TASK_STORE"]
        storage_path = store.get_resume(resume_id)["storage_path"]
        file_path = self.resume_dir / storage_path

        with mock.patch("pathlib.Path.replace", side_effect=OSError("denied")):
            resp = self.client.delete(f"/api/profiles/{pid}")

        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.get_json().get("error_code"), "resume_cleanup_failed")
        self.assertEqual(store.get_profile(pid)["id"], pid)
        self.assertTrue(file_path.is_file())

    def test_delete_profile_rolls_back_on_non_os_error_during_move(self):
        pid = self._make_profile()
        store = self.app.config["TASK_STORE"]
        file_a = self.resume_dir / "a.txt"
        file_b = self.resume_dir / "b.txt"
        file_a.write_text("a", encoding="utf-8")
        file_b.write_text("b", encoding="utf-8")
        store.save_resume(pid, "a.txt", "txt", "a", "hash-a", original_filename="a.txt")
        store.save_resume(pid, "b.txt", "txt", "b", "hash-b", original_filename="b.txt")

        real_get = store.get_resume
        calls = {"n": 0}

        def fake_get(resume_id):
            calls["n"] += 1
            if calls["n"] == 1:
                return real_get(resume_id)
            raise RuntimeError("db boom")

        with mock.patch.object(store, "get_resume", side_effect=fake_get):
            resp = self.client.delete(f"/api/profiles/{pid}")

        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.get_json().get("error_code"), "resume_cleanup_failed")
        self.assertEqual(store.get_profile(pid)["id"], pid)
        self.assertTrue(file_a.exists())
        self.assertTrue(file_b.exists())
        trash_dir = self.resume_dir / ".trash"
        self.assertFalse(trash_dir.exists() or list(trash_dir.glob("*")))

    def test_delete_profile_rolls_back_when_db_delete_fails(self):
        pid = self._make_profile()
        store = self.app.config["TASK_STORE"]
        file_path = self.resume_dir / "db.txt"
        file_path.write_text("db", encoding="utf-8")
        store.save_resume(pid, "db.txt", "txt", "db", "hash-db", original_filename="db.txt")

        with mock.patch.object(store, "delete_profile", side_effect=RuntimeError("db fail")):
            with self.assertRaises(RuntimeError):
                self.client.delete(f"/api/profiles/{pid}")

        self.assertEqual(store.get_profile(pid)["id"], pid)
        self.assertTrue(file_path.exists())
        trash_dir = self.resume_dir / ".trash"
        self.assertFalse(trash_dir.exists() or list(trash_dir.glob("*")))

    def test_delete_profile_reports_cleanup_warning_when_trash_unlink_fails(self):
        pid = self._make_profile()
        store = self.app.config["TASK_STORE"]
        file_path = self.resume_dir / "warn.txt"
        file_path.write_text("warn", encoding="utf-8")
        store.save_resume(pid, "warn.txt", "txt", "warn", "hash-warn", original_filename="warn.txt")

        with mock.patch("pathlib.Path.unlink", side_effect=OSError("denied")):
            resp = self.client.delete(f"/api/profiles/{pid}")

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json().get("cleanup_warning"))
        self.assertFalse(file_path.exists())
        trash_dir = self.resume_dir / ".trash"
        trash_files = list(trash_dir.glob("*")) if trash_dir.exists() else []
        self.assertEqual(len(trash_files), 1)

    def test_anonymous_resume_read_rejected(self):
        pid = self._make_profile()
        resp = self._anon().get(f"/api/profiles/{pid}/resumes")
        self.assertEqual(resp.status_code, 403)

    def test_configured_ai_requires_explicit_upload_consent_before_parsing(self):
        pid = self._make_profile()
        with mock.patch("webui.ai.store_api_key", return_value="ref"):
            self.client.put("/api/ai-settings", json={
                "endpoint_url": "https://api.example.com/v1", "api_key": "secret",
            })
        parsed = {"profile_name": "P", "city": "上海", "roles": [], "skills": [], "keywords": [], "suggestions": []}
        with mock.patch("webui.ai.retrieve_api_key", return_value="secret"), \
             mock.patch("webui.ai.parse_resume", return_value=parsed) as parse:
            resp = self._upload_txt(pid)
        self.assertEqual(resp.status_code, 200)
        parse.assert_not_called()
        self.assertIn("AI", resp.get_json()["privacy_notice"])

    def test_second_resume_creates_an_isolated_profile(self):
        pid = self._make_profile()
        first = self._upload_txt(pid)
        self.assertEqual(first.status_code, 200)
        second = self._upload_txt(pid, text="上海 Go 后端")
        self.assertEqual(second.status_code, 200)
        new_profile_id = second.get_json()["profile_id"]
        self.assertNotEqual(new_profile_id, pid)
        store = self.app.config["TASK_STORE"]
        self.assertEqual(store.get_profile(new_profile_id)["ai_preference"], {})


class SearchRunTests(WorkbenchAPITestBase):
    """T029: search run creation and job listing."""

    def _make_profile(self, city="上海"):
        resp = self.client.post("/api/profiles", json={
            "name": "测试",
            "confirmed_fields": {"city": city, "roles": ["Python"]},
        })
        return resp.get_json()["id"]

    def test_create_search_run(self):
        pid = self._make_profile()
        resp = self.client.post("/api/search-runs", json={
            "profile_id": pid,
            "manual_keywords": ["Python 后端"],
        })
        self.assertEqual(resp.status_code, 202)
        data = resp.get_json()
        self.assertEqual(data["status"], "queued")
        self.assertEqual(data["total_detail_budget"], 60)

    def test_create_search_run_requires_city(self):
        pid = self._make_profile(city="")
        resp = self.client.post("/api/search-runs", json={
            "profile_id": pid,
            "manual_keywords": ["Python"],
        })
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertEqual(body["error_code"], "invalid_request")
        self.assertIn("城市", body["user_message"])
        self.assertNotIn("error", body)

    def test_search_run_submit_failure_is_terminal_and_reported(self):
        """033 V2 T053：工作台提交失败立即终止，不遗留 queued/running。"""
        pid = self._make_profile()
        runner = self.app.config["WORKBENCH_RUNNER"]
        fake_executor = mock.Mock()
        fake_executor.submit.side_effect = RuntimeError("executor unavailable")
        with mock.patch.object(runner, "executor", fake_executor):
            resp = self.client.post("/api/search-runs", json={
                "profile_id": pid, "manual_keywords": ["Python 后端"],
            })
        self.assertEqual(resp.status_code, 202)
        run = resp.get_json()
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "submit_failed")
        store = self.app.config["TASK_STORE"]
        report = __import__("webui.whitebox", fromlist=["WhiteboxService"]).WhiteboxService(
            store
        ).report("workbench", run["id"], include_events=True)
        self.assertEqual(report["integrity"]["conclusion"], "failed")
        self.assertTrue(any(event["event_type"] == "submission_failed" for event in report["events"]))

    def test_search_uses_validated_resume_ai_keywords_when_manual_is_empty(self):
        pid = self._make_profile()
        with mock.patch("webui.ai.store_api_key", return_value="ref"):
            self.client.put("/api/ai-settings", json={
                "endpoint_url": "https://api.example.com/v1", "api_key": "secret",
            })
        resume = io.BytesIO("上海 Python 后端".encode())
        parsed = {
            "profile_name": "P", "city": "上海", "roles": ["Python"], "skills": [],
            "keywords": ["Python 后端"], "suggestions": [],
        }
        with mock.patch("webui.ai.retrieve_api_key", return_value="secret"), \
             mock.patch("webui.ai.parse_resume", return_value=parsed):
            uploaded = self.client.post(
                f"/api/profiles/{pid}/resume",
                data={"file": (resume, "resume.txt"), "ai_consent": "true"},
                content_type="multipart/form-data",
            )
        self.assertEqual(uploaded.status_code, 200)
        resp = self.client.post("/api/search-runs", json={"profile_id": pid})
        self.assertEqual(resp.status_code, 202)
        query = self.app.config["TASK_STORE"].list_run_queries(resp.get_json()["id"])[0]
        self.assertEqual(query["frozen_query"]["keyword"], "Python 后端")

    def test_cancel_search_run(self):
        pid = self._make_profile()
        run = self.client.post("/api/search-runs", json={
            "profile_id": pid,
            "manual_keywords": ["Python"],
        }).get_json()
        resp = self.client.post(f"/api/search-runs/{run['id']}/cancel")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "interrupted")

    def test_search_run_jobs_returns_jd_excerpt_and_feedback(self):
        """Regression: cards must carry jd_excerpt and current interest_state."""
        pid = self._make_profile()
        store = self.app.config["TASK_STORE"]
        run = self.client.post("/api/search-runs", json={
            "profile_id": pid,
            "manual_keywords": ["Python"],
        }).get_json()
        # Seed a job + profile_job link + feedback
        long_jd = "使用 Python 开发后端服务，需要熟悉 FastAPI 和 SQLAlchemy，负责 API 设计与数据库优化。"
        job = store.save_job(
            "https://www.zhipin.com/job_detail/sr1.html",
            "https://www.zhipin.com/job_detail/sr1.html",
            "Python 后端", "Acme", "30-50K", "上海", long_jd,
        )
        store.link_profile_job(pid, job["id"], run["id"], run["id"])
        resp = self.client.get(f"/api/search-runs/{run['id']}/jobs")
        self.assertEqual(resp.status_code, 200)
        cards = resp.get_json()["jobs"]
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card["title"], "Python 后端")
        self.assertEqual(card["company"], "Acme")
        self.assertEqual(card["salary"], "30-50K")
        self.assertEqual(card["location"], "上海")
        self.assertEqual(card["canonical_url"], "https://www.zhipin.com/job_detail/sr1.html")
        # jd_excerpt must be populated and truncated
        self.assertIn("Python", card["jd_excerpt"])
        self.assertLessEqual(len(card["jd_excerpt"]), 320)
        self.assertEqual(card["interest_state"], "new")

    def test_search_run_jobs_reflects_feedback_and_revoke(self):
        """Feedback changes interest_state; revoke restores it."""
        pid = self._make_profile()
        store = self.app.config["TASK_STORE"]
        run = self.client.post("/api/search-runs", json={
            "profile_id": pid,
            "manual_keywords": ["Python"],
        }).get_json()
        job = store.save_job(
            "https://www.zhipin.com/job_detail/sr2.html",
            "https://www.zhipin.com/job_detail/sr2.html",
            "T", "C", "S", "L", "JD",
        )
        store.link_profile_job(pid, job["id"], run["id"], run["id"])
        # Mark not_interested
        fb = self.client.post(f"/api/jobs/{job['id']}/feedback", json={
            "profile_id": pid, "action": "not_interested",
        }).get_json()
        cards = self.client.get(f"/api/search-runs/{run['id']}/jobs").get_json()["jobs"]
        self.assertEqual(cards, [])
        # Revoke feedback
        self.client.post(f"/api/feedback/{fb['feedback_id']}/revoke")
        cards = self.client.get(f"/api/search-runs/{run['id']}/jobs").get_json()["jobs"]
        self.assertEqual(cards[0]["interest_state"], "new")


class FeedbackTests(WorkbenchAPITestBase):
    """T037/T038: feedback, revoke, profile isolation."""

    def _make_profile(self):
        return self.client.post("/api/profiles", json={"name": "P"}).get_json()["id"]

    def _make_job(self):
        store = self.app.config["TASK_STORE"]
        return store.save_job(
            "https://www.zhipin.com/job_detail/fb.html",
            "https://www.zhipin.com/job_detail/fb.html",
            "T", "C", "S", "L", "JD",
        )["id"]

    def test_post_feedback(self):
        pid = self._make_profile()
        job_id = self._make_job()
        store = self.app.config["TASK_STORE"]
        store.link_profile_job(pid, job_id, None, None)
        resp = self.client.post(f"/api/jobs/{job_id}/feedback", json={
            "profile_id": pid,
            "action": "not_interested",
            "reason": "salary",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["interest_state"], "not_interested")
        self.assertIn("feedback_id", data)

    def test_revoke_feedback(self):
        pid = self._make_profile()
        job_id = self._make_job()
        store = self.app.config["TASK_STORE"]
        store.link_profile_job(pid, job_id, None, None)
        fb = self.client.post(f"/api/jobs/{job_id}/feedback", json={
            "profile_id": pid, "action": "not_interested",
        }).get_json()
        resp = self.client.post(f"/api/feedback/{fb['feedback_id']}/revoke")
        self.assertEqual(resp.status_code, 200)

    def test_no_external_application_route(self):
        pid = self._make_profile()
        job_id = self._make_job()
        # Must not expose auto-apply
        resp = self.client.post(f"/api/jobs/{job_id}/apply", json={"profile_id": pid})
        self.assertEqual(resp.status_code, 404)

    def test_fifth_effective_feedback_updates_only_current_profile_preference(self):
        pid = self._make_profile()
        store = self.app.config["TASK_STORE"]
        with mock.patch("webui.ai.store_api_key", return_value="ref"):
            self.client.put("/api/ai-settings", json={
                "endpoint_url": "https://api.example.com/v1", "api_key": "secret",
            })
        preference = {"positive_terms": ["Python"], "negative_terms": [], "keyword_weights": {"Python": 1.0}, "uncertain": []}
        with mock.patch("webui.ai.retrieve_api_key", return_value="secret"), \
             mock.patch("webui.ai.update_preference", return_value=preference) as update:
            for index in range(5):
                job_id = store.save_job(
                    f"https://www.zhipin.com/job_detail/five-{index}.html",
                    f"https://www.zhipin.com/job_detail/five-{index}.html",
                    "T", "C", "S", "L", "JD",
                )["id"]
                store.link_profile_job(pid, job_id, None, None)
                self.client.post(f"/api/jobs/{job_id}/feedback", json={
                    "profile_id": pid, "action": "interested",
                })
        update.assert_called_once()
        self.assertEqual(store.get_latest_preference(pid)["preference_json"], preference)


class HistoryAndCleanupTests(WorkbenchAPITestBase):
    """T043/T044: history, favorites, cleanup preview."""

    def _make_profile(self):
        return self.client.post("/api/profiles", json={"name": "P"}).get_json()["id"]

    def test_list_profile_jobs(self):
        pid = self._make_profile()
        store = self.app.config["TASK_STORE"]
        job = store.save_job(
            "https://www.zhipin.com/job_detail/h.html",
            "https://www.zhipin.com/job_detail/h.html",
            "T", "C", "S", "L", "JD",
        )
        store.link_profile_job(pid, job["id"], None, None, status="interested")
        resp = self.client.get(f"/api/profile-jobs?profile_id={pid}&status=interested")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.get_json()["jobs"]), 1)

    def test_patch_profile_job_status(self):
        # Task 008 集成回归：legacy PATCH 已转入统一命令服务，写请求必须
        # 携带 request ID（contracts/http-api.md Legacy PATCH 节）。
        import uuid
        from datetime import datetime, timedelta, timezone
        pid = self._make_profile()
        store = self.app.config["TASK_STORE"]
        job = store.save_job(
            "https://www.zhipin.com/job_detail/pj.html",
            "https://www.zhipin.com/job_detail/pj.html",
            "T", "C", "S", "L", "JD",
        )
        store.link_profile_job(pid, job["id"], None, None)
        missing_key = self.client.patch(f"/api/profile-jobs/{pid}/{job['id']}", json={
            "status": "applied",
        })
        self.assertEqual(missing_key.status_code, 428)
        self.assertEqual(
            missing_key.get_json()["error_code"], "idempotency_key_required")
        # 无历史投递时间时纠正为 applied 必须同时提供真实投递时间，
        # 命令服务不得猜测当前时刻（data-model.md correct_status）。
        no_time = self.client.patch(f"/api/profile-jobs/{pid}/{job['id']}", json={
            "status": "applied",
            "request_id": str(uuid.uuid4()),
        })
        self.assertEqual(no_time.status_code, 422)
        self.assertEqual(
            no_time.get_json()["error_code"], "applied_at_required")
        applied_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        resp = self.client.patch(f"/api/profile-jobs/{pid}/{job['id']}", json={
            "status": "applied",
            "applied_at": applied_at,
            "request_id": str(uuid.uuid4()),
        })
        self.assertEqual(resp.status_code, 200)
        pj = store.get_profile_job(pid, job["id"])
        self.assertEqual(pj["status"], "applied")
        self.assertEqual(pj["applied_at"], applied_at)

    def test_cleanup_preview(self):
        resp = self.client.get("/api/cleanup-preview")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("would_remove", resp.get_json())

    def test_new_normal_job_has_a_30_day_expiry(self):
        from datetime import datetime, timedelta, timezone
        store = self.app.config["TASK_STORE"]
        job = store.save_job(
            "https://www.zhipin.com/job_detail/retention.html",
            "https://www.zhipin.com/job_detail/retention.html",
            "T", "C", "S", "L", "JD",
        )
        expiry = datetime.fromisoformat(job["expires_at"])
        self.assertGreater(expiry, datetime.now(timezone.utc) + timedelta(days=29))
        self.assertLess(expiry, datetime.now(timezone.utc) + timedelta(days=31))

    def test_cleanup_preview_does_not_modify_data(self):
        """Preview must be read-only: no profile_job status changes."""
        from datetime import datetime, timedelta, timezone
        pid = self._make_profile()
        store = self.app.config["TASK_STORE"]
        job = store.save_job(
            "https://www.zhipin.com/job_detail/exp.html",
            "https://www.zhipin.com/job_detail/exp.html",
            "T", "C", "S", "L", "JD",
        )
        # Set expiry in the past so it qualifies for cleanup
        expired = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        store.update_job_expiry(job["id"], expired)
        store.link_profile_job(pid, job["id"], None, None, status="new")
        # Call preview
        resp = self.client.get("/api/cleanup-preview")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertGreaterEqual(data["would_remove"], 1)
        self.assertIn("items", data)
        # Verify the profile_job was NOT modified
        pj = store.get_profile_job(pid, job["id"])
        self.assertEqual(pj["status"], "new")
        # Real cleanup does modify
        store.cleanup_expired_jobs(days=30)
        pj = store.get_profile_job(pid, job["id"])
        self.assertEqual(pj["status"], "deleted")


if __name__ == "__main__":
    unittest.main()
