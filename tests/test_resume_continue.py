import pathlib
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

from webui.app import create_app
from webui.resume_identity import (
    invalidate_login_cache_for_resume,
    persist_frozen_identity,
    resolve_frozen_identity,
)


class FakeStore:
    def __init__(self, run, parent=None):
        self.runs = {run["id"]: run}
        if parent:
            self.runs[parent["id"]] = parent
        self.updated = {}

    def get_screening_run(self, run_id):
        return self.runs.get(str(run_id))

    def update_screening_execution_params(self, run_id, params):
        self.updated[str(run_id)] = params


class ResumeIdentityTests(unittest.TestCase):
    def test_resolves_complete_identity_from_run(self):
        run = {
            "id": "r1", "platform": "zhilian",
            "execution_params": {
                "platform": "zhilian", "browser_account": "a",
                "cdp_port": 9223, "profile_key": "zhilian:a",
            },
        }
        identity = resolve_frozen_identity(FakeStore(run), run)
        self.assertEqual(identity, {
            "platform": "zhilian", "browser_account": "a",
            "cdp_port": 9223, "profile_key": "zhilian:a",
        })

    def test_missing_identity_stays_missing(self):
        run = {
            "id": "r1", "platform": "zhilian",
            "execution_params": {"platform": "zhilian", "browser_account": "a"},
        }
        identity = resolve_frozen_identity(FakeStore(run), run)
        self.assertIsNone(identity["cdp_port"])
        self.assertEqual(identity["profile_key"], "")

    def test_falls_back_to_parent_scrape_identity(self):
        parent = {
            "id": "scrape-1", "platform": "zhilian",
            "execution_params": {
                "platform": "zhilian", "browser_account": "b",
                "cdp_port": 9223, "profile_key": "zhilian:b",
            },
        }
        run = {
            "id": "r1", "platform": "",
            "execution_params": {"scrape_task_id": "scrape-1"},
        }
        identity = resolve_frozen_identity(FakeStore(run, parent), run)
        self.assertEqual(identity["platform"], "zhilian")
        self.assertEqual(identity["browser_account"], "b")
        self.assertEqual(identity["cdp_port"], 9223)

    def test_persist_writes_non_empty_fields(self):
        store = FakeStore({"id": "r1", "execution_params": {"platform": "boss"}})
        persist_frozen_identity(store, "r1", {
            "platform": "boss", "browser_account": "a",
            "cdp_port": 9222, "profile_key": "boss:a",
        })
        self.assertEqual(store.updated["r1"]["cdp_port"], 9222)
        self.assertEqual(store.updated["r1"]["profile_key"], "boss:a")

    def test_invalidate_login_cache_for_resume(self):
        from scripts import login_state_cache as cache
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = pathlib.Path(tmp) / "login-state.json"
            cache.set_login_state_path(cache_path)
            try:
                cache.write_login_state("acc1", "zhilian", "logged_in")
                self.assertEqual(cache.read_cached_state("acc1", "zhilian"), "logged_in")
                invalidate_login_cache_for_resume("acc1", "zhilian")
                self.assertIsNone(cache.read_cached_state("acc1", "zhilian"))
            finally:
                cache.reset_login_state_path()

    def test_activation_failure_uses_registry_source_message(self):
        from webui.error_registry import ERROR_USER_MESSAGES
        from webui.resume_identity import activate_frozen_identity_candidate

        def fail_activation(_run):
            raise RuntimeError("raw browser diagnostic")

        result = activate_frozen_identity_candidate(
            fail_activation,
            {"platform": "zhilian", "execution_params": {}},
            {
                "platform": "zhilian", "browser_account": "a",
                "cdp_port": 9223, "profile_key": "zhilian:a",
            },
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "source_cdp_unavailable")
        self.assertEqual(
            result["message"],
            ERROR_USER_MESSAGES["source_cdp_unavailable"],
        )
        self.assertNotIn("raw browser diagnostic", repr(result))

    def test_cleanup_audit_write_failure_is_logged_and_not_silent(self):
        from webui.frozen_browser_identity import cleanup_frozen_task_browser
        from webui import task_continue_api

        class AuditStore:
            def get_screening_run(self, run_id):
                return {"id": run_id, "platform": "boss", "execution_params": {}}

            def append_task_event(self, run_id, event_type, payload):
                raise RuntimeError("audit sink unavailable")

        source = pathlib.Path(task_continue_api.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(source, r"except Exception:\s*pass")
        self.assertIn("browser cleanup audit event write failed", source)
        with self.assertLogs(
                "career_scout.webui.frozen_browser_identity", level="WARNING") as logs:
            result = cleanup_frozen_task_browser(
                AuditStore(), "audit-run", None,
                accounts_path=None,
                activate=lambda _profile: None,
                close=lambda: False,
            )
        self.assertFalse(result.ok)
        self.assertTrue(any("RuntimeError" in line for line in logs.output))

    def test_account_switch_audit_write_failure_logs_type_without_payload(self):
        from webui.resume_identity import record_account_switch_event

        class AuditStore:
            def append_task_event(self, run_id, event_type, payload):
                raise RuntimeError("secret-token-must-not-be-logged")

        with self.assertLogs(
                "career_scout.webui.resume_identity", level="WARNING") as logs:
            written = record_account_switch_event(
                AuditStore(), "audit-switch-run", from_account="a",
                to_account="b", accounts={}, phase="resume", reason="auto",
            )

        self.assertFalse(written)
        self.assertTrue(any("RuntimeError" in line for line in logs.output))
        self.assertTrue(any("audit-switch-run" in line for line in logs.output))
        self.assertFalse(any("secret-token" in line for line in logs.output))

    def test_child_identity_resolution_warning_preserves_original_identity(self):
        from webui.resume_identity import resolve_child_frozen_identity

        class ParentStore:
            def get_run_checkpoint_identity(self, run_id):
                return None

            def get_screening_run(self, run_id):
                return {
                    "id": run_id,
                    "platform": "boss",
                    "execution_params": {
                        "platform": "boss",
                        "browser_account": "account-secret",
                        "cdp_port": None,
                        "profile_key": "account-secret",
                    },
                }

        with mock.patch(
                "webui.pipeline_exec_accounts.resolve_browser_account",
                return_value="profile-path-secret"), mock.patch(
                "webui.platforms.resolve_login_space",
                side_effect=RuntimeError("credential-secret")), self.assertLogs(
                    "career_scout.webui.resume_identity", level="WARNING") as logs:
            identity = resolve_child_frozen_identity(
                ParentStore(), "source-child-run",
                fallback_account="fallback-secret",
                accounts_path="accounts-path-secret",
            )

        self.assertEqual(identity, {
            "platform": "boss",
            "browser_account": "account-secret",
            "cdp_port": None,
            "profile_key": "account-secret",
            "filter_schema_version": None,
        })
        self.assertTrue(any("source-child-run" in line for line in logs.output))
        self.assertTrue(any("operation=child_login_space_resolve" in line
                            for line in logs.output))
        self.assertTrue(any("RuntimeError" in line for line in logs.output))
        for secret in (
                "account-secret", "profile-path-secret", "credential-secret",
                "accounts-path-secret", "fallback-secret"):
            self.assertFalse(any(secret in line for line in logs.output))


class ResumeContinueApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temp.name)
        self.app = create_app({
            "TESTING": True,
            "START_TASKS": False,
            "RESULT_DIR": str(root / "results"),
            "DB_PATH": str(root / "state" / "webui.db"),
        })
        self.client = self.app.test_client()
        token = self.client.get("/api/session").get_json()["token"]
        self.client.environ_base["HTTP_X_BOSS_TOKEN"] = token
        self.store = self.app.config["TASK_STORE"]
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda run: (True, "", "")

    def tearDown(self):
        self.temp.cleanup()

    def _seed_zhilian_paused(self, run_id="zhilian-paused", with_identity=True):
        scrape_id = f"{run_id}-scrape"
        self.store.create_screening_run(scrape_id, source_count=1)
        self.store.save_scrape_combo_result(
            scrape_id, "前端|上海",
            [{"job_id": "job-1", "title": "前端工程师", "platform_job_id": "z-1"}],
            ["前端|上海"],
        )
        params = {
            "scrape_task_id": scrape_id,
            "profile_summary": "前端工程师",
            "platform": "zhilian",
            "browser_account": "a",
            "cdp_port": 9223,
            "profile_key": "zhilian:a",
            "execution_config": {},
        }
        if not with_identity:
            params.pop("browser_account", None)
            params.pop("cdp_port", None)
            params.pop("profile_key", None)
        self.store.create_screening_run(
            run_id, source_count=1, frozen_filters={"city": ["上海"]},
            execution_params=params,
        )
        self.store.update_screening_run(run_id, status="running", current_stage="ai_rough")
        self.store.update_screening_run(
            run_id, status="paused", error_code="cdp_unavailable",
            current_stage="ai_rough",
        )
        self.store.save_ai_settings("http://example.invalid", "test-ref", status="ready")

    def _seed_boss_scrape_paused(self, run_id, error_code="user_paused"):
        self.store.create_screening_run(
            run_id,
            source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {
                    "keyword": "前端", "city": ["上海"], "pages": 1,
                },
                "browser_account": "a",
                "cdp_port": 9222,
                "profile_key": "boss:a",
            },
        )
        self.store.update_screening_run(
            run_id, status="running", current_stage="scrape",
        )
        self.store.save_checkpoint(run_id, "scrape", [])
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code=error_code,
        )

    def test_scrape_continue_corrupt_checkpoint_finishes_failed_without_worker(self):
        """统一继续入口在严格断点读取失败时返回 409 并结束为 failed。"""
        for platform in ("boss", "zhilian"):
            with self.subTest(platform=platform):
                run_id = f"corrupt-unified-scrape-{platform}"
                self.store.create_screening_run(
                    run_id,
                    source_count=1,
                    execution_params={
                        "platform": platform,
                        "script_params": {"keyword": "前端", "city": ["上海"], "pages": 1},
                        "browser_account": "a",
                        "cdp_port": 9222 if platform == "boss" else 9223,
                        "profile_key": f"{platform}:a",
                    },
                )
                self.store.update_screening_run(
                    run_id, status="running", current_stage="scrape",
                )
                self.store.update_screening_run(
                    run_id, status="paused", current_stage="scrape",
                    error_code="captcha_required", error_reason="触发验证码",
                )
                raw_checkpoint = '{"credential":"must-not-be-replaced"'
                with self.store._connection() as conn:
                    conn.execute(
                        "INSERT INTO pipeline_checkpoints "
                        "(run_id, stage, completed_keys_json, saved_at) "
                        "VALUES (?, 'scrape', ?, 1)",
                        (run_id, raw_checkpoint),
                    )
                context = self.app.config["PIPELINE_CONTEXT"]
                context.tasks[run_id] = {
                    "kind": "scrape", "status": "paused", "result": None,
                }
                executor = self.app.config["PIPELINE_EXECUTOR"]
                with mock.patch.object(context, "activate_run_browser") as activate, \
                        mock.patch.object(executor, "submit") as submit, \
                        mock.patch("webui.task_continue_api.commit_continue_identity") as commit, \
                        mock.patch("webui.task_continue_api.invalidate_login_cache_for_resume") as invalidate:
                    response = self.client.post(f"/api/task/continue/{run_id}")

                self.assertEqual(response.status_code, 409, response.get_json())
                payload = response.get_json()
                self.assertEqual(payload["error"], "checkpoint_read_failed")
                self.assertEqual(payload["status"], "failed")
                failed_run = self.store.get_screening_run(run_id)
                self.assertEqual(failed_run["status"], "failed")
                self.assertEqual(failed_run["source_count"], 1)
                # The shared continuation order checks the frozen browser
                # binding and fresh source state before reading the checkpoint.
                activate.assert_called_once()
                commit.assert_not_called()
                invalidate.assert_called_once()
                with self.store._connection() as conn:
                    row = conn.execute(
                        "SELECT completed_keys_json FROM pipeline_checkpoints "
                        "WHERE run_id = ? AND stage = 'scrape'", (run_id,),
                    ).fetchone()
                self.assertEqual(row["completed_keys_json"], raw_checkpoint)
                submit.assert_not_called()

    def test_unified_scrape_continue_checks_identity_and_source_before_checkpoint(self):
        """统一继续入口按身份、平台复检、断点、分发的顺序执行。"""
        run_id = "unified-scrape-continue-order"
        self._seed_boss_scrape_paused(run_id, error_code="source_login_required")
        context = self.app.config["PIPELINE_CONTEXT"]
        events = []

        with mock.patch.object(
                context, "activate_run_browser",
                side_effect=lambda _run: events.append("identity")), \
                mock.patch.object(
                    context, "check_resume_block",
                    side_effect=lambda _run: (
                        events.append("fresh_source") or (True, "", "")
                    )), \
                mock.patch(
                    "webui.task_continue_api.scrape_checkpoint",
                    side_effect=lambda *args, **kwargs: (
                        events.append("checkpoint") or []
                    )), \
                mock.patch.object(
                    context, "continue_execute_search",
                    side_effect=lambda *args, **kwargs: (
                        events.append("dispatch") or ("continued", 200)
                    )), \
                mock.patch(
                    "webui.task_continue_api.invalidate_login_cache_for_resume",
                ):
            response = self.client.post(f"/api/task/continue/{run_id}")

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(
            events,
            ["identity", "fresh_source", "checkpoint", "dispatch"],
        )

    def test_probe_only_resume_check_stops_after_cdp_probe(self):
        """仅 CDP 探测模式不能访问尚未生成的登录态 outcome。"""
        run_id = "probe-only-resume-check"
        self._seed_boss_scrape_paused(run_id, error_code="source_login_required")
        context = self.app.config["PIPELINE_CONTEXT"]

        with mock.patch(
                "webui.pipeline_exec.probe_chrome_ready",
                return_value=(True, ""),
        ), mock.patch.object(context, "source_class") as source_factory:
            self.app.config.pop("RESUME_BLOCK_CHECKER", None)
            passed, code, _reason = context.check_resume_block(
                self.store.get_screening_run(run_id), probe_only=True,
            )

        self.assertTrue(passed)
        self.assertEqual(code, "source_login_required")
        source_factory.assert_not_called()

    def test_ai_kind_with_scrape_stage_keeps_ai_continue_order(self):
        """AI hard-stop rows using the scrape stage must not take scrape resume."""
        run_id = "ai-kind-scrape-stage"
        self._seed_zhilian_paused(run_id)
        self.store.update_screening_run(
            run_id, status="running", current_stage="scrape",
        )
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="源账号限流",
        )
        raw_checkpoint = '{"credential":"ai-run-must-not-be-read"'
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO pipeline_checkpoints "
                "(run_id, stage, completed_keys_json, saved_at) "
                "VALUES (?, 'scrape', ?, 1)",
                (run_id, raw_checkpoint),
            )
        context = self.app.config["PIPELINE_CONTEXT"]
        executor = self.app.config["PIPELINE_EXECUTOR"]
        self.app.config["PIPELINE_TASKS"][run_id] = {
            "kind": "ai_screen", "status": "paused", "result": None,
        }
        with mock.patch.object(context, "activate_run_browser"), \
                mock.patch.object(executor, "submit", return_value=mock.Mock()), \
                mock.patch.object(
                    context, "continue_execute_search",
                    side_effect=AssertionError(
                        "AI continue must not dispatch scrape worker",
                    ),
                ) as continue_scrape:
            response = self.client.post(f"/api/task/continue/{run_id}")

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertNotEqual(
            response.get_json().get("error"), "checkpoint_read_failed",
        )
        continue_scrape.assert_not_called()
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT completed_keys_json FROM pipeline_checkpoints "
                "WHERE run_id = ? AND stage = 'scrape'", (run_id,),
            ).fetchone()
        self.assertEqual(row["completed_keys_json"], raw_checkpoint)

    def test_ai_scrape_stage_after_restart_keeps_ai_continue_order(self):
        """Persisted AI hard-stop rows remain AI after the live task is gone."""
        run_id = "ai-restart-scrape-stage"
        self._seed_zhilian_paused(run_id)
        self.store.update_screening_run(
            run_id, status="running", current_stage="scrape",
        )
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_rate_limited", error_reason="源账号限流",
        )
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO pipeline_checkpoints "
                "(run_id, stage, completed_keys_json, saved_at) "
                "VALUES (?, 'scrape', ?, 1)",
                (run_id, '{"credential":"restart-ai-must-not-be-read"'),
            )
        context = self.app.config["PIPELINE_CONTEXT"]
        executor = self.app.config["PIPELINE_EXECUTOR"]
        context.tasks.pop(run_id, None)
        with mock.patch.object(context, "activate_run_browser"), \
                mock.patch.object(executor, "submit", return_value=mock.Mock()), \
                mock.patch.object(
                    context, "continue_execute_search",
                    side_effect=AssertionError(
                        "AI continue must not dispatch scrape worker",
                    ),
                ) as continue_scrape:
            response = self.client.post(f"/api/task/continue/{run_id}")

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertNotEqual(
            response.get_json().get("error"), "checkpoint_read_failed",
        )
        continue_scrape.assert_not_called()

    def test_source_factory_none_uses_registry_reason_in_whitebox_primary_reason(self):
        from webui.error_registry import ERROR_USER_MESSAGES
        from webui.runners.pipeline_task import run_pipeline_task
        from webui.whitebox import WhiteboxService

        run_id = "source-factory-none"
        script_params = {"keyword": "前端", "city": ["上海"], "pages": 1}
        self.store.create_screening_run(
            run_id,
            source_count=1,
            execution_params={
                "platform": "zhilian",
                "script_params": script_params,
                "browser_account": "a",
                "cdp_port": 9223,
                "profile_key": "zhilian:a",
            },
        )
        context = self.app.config["PIPELINE_CONTEXT"]
        task = context.register_pipeline_task(run_id, "scrape")
        task.update({
            "platform": "zhilian", "browser_account": "a",
            "cdp_port": 9223, "profile_key": "zhilian:a",
        })

        with mock.patch.object(context, "activate_task_browser"), \
                mock.patch.object(context, "make_cdp_source", return_value=None), \
                mock.patch.object(context, "schedule_pipeline_task_cleanup"), \
                mock.patch.object(context, "clear_auto_screen"):
            run_pipeline_task(context, run_id, script_params)

        expected = ERROR_USER_MESSAGES["source_cdp_unavailable"]
        run = self.store.get_screening_run(run_id)
        self.assertEqual(run["error_code"], "source_cdp_unavailable")
        self.assertEqual(run["error_reason"], expected)
        self.assertEqual(task["error"], expected)
        integrity = WhiteboxService(self.store).report("scrape", run_id)["integrity"]
        self.assertEqual(integrity["primary_code"], "source_cdp_unavailable")
        self.assertEqual(integrity["primary_reason"], expected)

    def test_direct_search_continue_activation_failure_uses_registry_reason(self):
        from webui.error_registry import ERROR_USER_MESSAGES
        from webui.frozen_browser_identity import FrozenBrowserBindingError

        run_id = "direct-search-continue-activation-failure"
        self.store.create_screening_run(
            run_id,
            source_count=1,
            execution_params={
                "platform": "zhilian",
                "script_params": {"keyword": "前端", "city": ["上海"], "pages": 1},
                "browser_account": "a",
                "cdp_port": 9223,
                "profile_key": "zhilian:a",
            },
        )
        self.store.update_screening_run(
            run_id, status="running", current_stage="scrape",
        )
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="cdp_unavailable",
        )
        context = self.app.config["PIPELINE_CONTEXT"]
        expected = ERROR_USER_MESSAGES["source_cdp_unavailable"]
        with mock.patch.object(
                context, "activate_run_browser",
                side_effect=FrozenBrowserBindingError("raw bind diagnostic"),
        ), mock.patch.object(context, "schedule_pipeline_task_cleanup"), \
                mock.patch.object(context, "clear_auto_screen"):
            response = self.client.post(
                f"/api/execute-search/continue/{run_id}")

        self.assertEqual(response.status_code, 409, response.get_json())
        payload = response.get_json()
        self.assertEqual(payload["error"], "source_cdp_unavailable")
        self.assertEqual(payload["error_code"], "source_cdp_unavailable")
        self.assertEqual(payload["error_reason"], expected)
        self.assertEqual(payload["message"], expected)
        self.assertNotIn("raw bind diagnostic", str(payload))

    def test_direct_search_resume_unknown_probe_stays_paused(self):
        """兼容继续入口的未知复检结果也必须保持可恢复暂停。"""
        from webui.source import SourceOutcome

        run_id = "direct-search-continue-unknown-probe"
        self.store.create_screening_run(
            run_id,
            source_count=1,
            execution_params={
                "platform": "zhilian",
                "script_params": {
                    "keyword": "前端", "city": ["上海"], "pages": 1,
                },
                "browser_account": "a",
                "cdp_port": 9223,
                "profile_key": "zhilian:a",
            },
        )
        self.store.update_screening_run(
            run_id, status="running", current_stage="scrape",
        )
        self.store.save_checkpoint(run_id, "scrape", [])
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_login_required",
        )
        source = SimpleNamespace(
            preflight=mock.Mock(return_value=SourceOutcome.success()),
            recheck_login=mock.Mock(
                return_value=SourceOutcome.failure(
                    failed_code="source_unknown_error",
                ),
            ),
        )
        context = self.app.config["PIPELINE_CONTEXT"]
        with mock.patch.object(context, "activate_run_browser"), \
                mock.patch(
                    "webui.pipeline_exec.probe_chrome_ready",
                    return_value=(True, ""),
                ), \
                mock.patch("webui.source.ZhilianCdpSource", return_value=source), \
                mock.patch.object(context, "schedule_pipeline_task_cleanup"), \
                mock.patch.object(context, "clear_auto_screen"):
            self.app.config.pop("RESUME_BLOCK_CHECKER", None)
            response = self.client.post(
                f"/api/execute-search/continue/{run_id}",
            )

        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(response.get_json()["status"], "paused")
        self.assertEqual(response.get_json()["error_code"], "source_unknown_error")
        self.assertEqual(
            self.store.get_screening_run(run_id)["status"], "paused",
        )
        source.recheck_login.assert_called_once()

    def test_direct_search_continue_resolves_missing_identity_before_probe(self):
        """兼容继续入口也必须先补全冻结身份再复检平台状态。"""
        from webui.source import SourceOutcome

        run_id = "direct-search-continue-identity-order"
        self.store.create_screening_run(
            run_id,
            source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {
                    "keyword": "前端", "city": ["上海"], "pages": 1,
                },
                "cdp_port": 9222,
                "profile_key": "boss:a",
            },
        )
        self.store.update_screening_run(
            run_id, status="running", current_stage="scrape",
        )
        self.store.save_checkpoint(run_id, "scrape", [])
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_login_required",
        )
        events = []
        source = SimpleNamespace(
            preflight=mock.Mock(return_value=SourceOutcome.success()),
            recheck_login=mock.Mock(
                side_effect=lambda: (
                    events.append("login") or SourceOutcome.success()
                ),
            ),
        )
        context = self.app.config["PIPELINE_CONTEXT"]
        with mock.patch.object(
                context, "account_for_run", return_value="a"), \
                mock.patch.object(
                    context, "activate_run_browser",
                    side_effect=lambda run: events.append(
                        ("identity", (run.get("execution_params") or {}).get(
                            "browser_account"))),
                ), mock.patch(
                    "webui.pipeline_exec.probe_chrome_ready",
                    side_effect=lambda _port: (
                        events.append("cdp") or (True, "")
                    ),
                ), mock.patch.object(
                    context, "source_class", return_value=source,
                ), mock.patch.object(
                    context.executor, "submit",
                ) as submit:
            self.app.config.pop("RESUME_BLOCK_CHECKER", None)
            response = self.client.post(
                f"/api/execute-search/continue/{run_id}",
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(events, [("identity", "a"), "cdp", "login"])
        self.assertEqual(
            self.store.get_screening_run(run_id)["execution_params"]
            .get("browser_account"),
            "a",
        )
        source.recheck_login.assert_called_once()
        submit.assert_called_once()

    def test_direct_search_continue_passes_completed_legacy_identity_to_source(self):
        """旧 BOSS 任务缺端口/配置时，复检与 worker 必须使用补全后的身份。"""
        from webui.source import SourceOutcome

        run_id = "direct-search-continue-legacy-identity"
        self.store.create_screening_run(
            run_id,
            source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {
                    "keyword": "前端", "city": ["上海"], "pages": 1,
                },
                "browser_account": "b",
            },
        )
        self.store.update_screening_run(
            run_id, status="running", current_stage="scrape",
        )
        self.store.save_checkpoint(run_id, "scrape", [])
        self.store.update_screening_run(
            run_id, status="paused", current_stage="scrape",
            error_code="source_login_required",
        )
        source = SimpleNamespace(
            preflight=mock.Mock(return_value=SourceOutcome.success()),
            recheck_login=mock.Mock(return_value=SourceOutcome.success()),
        )
        captured = {}
        context = self.app.config["PIPELINE_CONTEXT"]

        def capture_source(**kwargs):
            captured.update(kwargs)
            return source

        with mock.patch.object(context, "activate_run_browser"), \
                mock.patch(
                    "webui.pipeline_exec.probe_chrome_ready",
                    return_value=(True, ""),
                ), mock.patch.object(
                    context, "source_class", side_effect=capture_source,
                ), mock.patch.object(
                    context.executor, "submit",
                ) as submit:
            self.app.config.pop("RESUME_BLOCK_CHECKER", None)
            response = self.client.post(
                f"/api/execute-search/continue/{run_id}",
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(captured["cdp_port"], 9222)
        self.assertEqual(captured["profile_key"], "boss:b")
        params = self.store.get_screening_run(run_id)["execution_params"]
        self.assertEqual(params["cdp_port"], 9222)
        self.assertEqual(params["profile_key"], "boss:b")
        source.recheck_login.assert_called_once()
        submit.assert_called_once()

    def test_execute_search_activation_failure_uses_registry_reason(self):
        from webui.error_registry import ERROR_USER_MESSAGES
        from webui.frozen_browser_identity import FrozenBrowserBindingError

        context = self.app.config["PIPELINE_CONTEXT"]
        expected = ERROR_USER_MESSAGES["source_cdp_unavailable"]
        with mock.patch.object(
                context, "activate_run_browser",
                side_effect=FrozenBrowserBindingError("raw start diagnostic"),
        ), mock.patch.object(context, "schedule_pipeline_task_cleanup"), \
                mock.patch.object(context, "clear_auto_screen"):
            response = self.client.post(
                "/api/execute-search",
                json={
                    "platform": "zhilian",
                    "script_params": {
                        "keyword": "前端", "city": ["上海"], "pages": 1,
                    },
                },
            )

        self.assertEqual(response.status_code, 503, response.get_json())
        payload = response.get_json()
        self.assertEqual(payload["error"], "source_cdp_unavailable")
        self.assertEqual(payload["error_code"], "source_cdp_unavailable")
        self.assertEqual(payload["error_reason"], expected)
        self.assertNotIn("raw start diagnostic", str(payload))
        with self.store._connection() as conn:
            run = conn.execute(
                "SELECT status, error_code FROM screening_runs "
                "ORDER BY created_at DESC LIMIT 1",
            ).fetchone()
        self.assertEqual(run["status"], "paused")
        self.assertEqual(run["error_code"], "source_cdp_unavailable")

    def test_missing_zhilian_identity_blocks_and_keeps_paused(self):
        self._seed_zhilian_paused(with_identity=False)
        context = self.app.config["PIPELINE_CONTEXT"]
        with mock.patch.object(
            context, "account_for_run",
            side_effect=AssertionError("智联缺身份不得解析全局账号"),
        ), mock.patch.object(context, "activate_run_browser") as activate:
            response = self.client.post("/api/task/continue/zhilian-paused")
        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(payload["error"], "missing_frozen_identity")
        self.assertEqual(self.store.get_screening_run("zhilian-paused")["status"], "paused")
        activate.assert_not_called()

    def test_complete_zhilian_identity_is_written_back_and_used(self):
        self._seed_zhilian_paused()
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(executor, "submit"):
            response = self.client.post("/api/task/continue/zhilian-paused")
        self.assertEqual(response.status_code, 200, response.get_json())
        run = self.store.get_screening_run("zhilian-paused")
        params = run["execution_params"]
        self.assertEqual(params["platform"], "zhilian")
        self.assertEqual(params["cdp_port"], 9223)
        self.assertEqual(params["profile_key"], "zhilian:a")
        claimed = self.app.config["PIPELINE_TASKS"].get("zhilian-paused")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["platform"], "zhilian")
        self.assertEqual(claimed["cdp_port"], 9223)
        self.assertEqual(claimed["profile_key"], "zhilian:a")

    def test_continue_binding_failure_does_not_persist_target_identity(self):
        """继续激活失败时目标账号不应污染 paused run，且可重试。"""
        from webui.frozen_browser_identity import FrozenBrowserBindingError
        from webui.error_registry import ERROR_USER_MESSAGES

        self._seed_zhilian_paused()
        context = self.app.config["PIPELINE_CONTEXT"]
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(
            context, "activate_run_browser",
            side_effect=FrozenBrowserBindingError("profile bind failed"),
        ), mock.patch.object(executor, "submit"):
            failed = self.client.post(
                "/api/task/continue/zhilian-paused",
                json={"target_account": "b"},
            )
        self.assertEqual(failed.status_code, 409, failed.get_json())
        self.assertEqual(failed.get_json()["error"], "source_cdp_unavailable")
        self.assertEqual(
            failed.get_json()["error_reason"],
            ERROR_USER_MESSAGES["source_cdp_unavailable"],
        )
        self.assertEqual(
            failed.get_json()["message"],
            ERROR_USER_MESSAGES["source_cdp_unavailable"],
        )
        self.assertNotIn("profile bind failed", str(failed.get_json()))
        run = self.store.get_screening_run("zhilian-paused")
        self.assertEqual(run["status"], "paused")
        self.assertEqual(run["execution_params"]["browser_account"], "a")

        with mock.patch.object(context, "activate_run_browser"), \
                mock.patch.object(executor, "submit") as submit:
            retried = self.client.post(
                "/api/task/continue/zhilian-paused",
                json={"target_account": "b"},
            )
        self.assertEqual(retried.status_code, 200, retried.get_json())
        self.assertEqual(
            self.store.get_screening_run("zhilian-paused")["execution_params"]["browser_account"],
            "b",
        )
        submit.assert_called_once()

    def test_default_resume_block_activation_failure_is_not_a_500(self):
        """默认阻断检查不得再次隐式激活并在提交后污染身份。"""
        from webui.frozen_browser_identity import FrozenBrowserBindingError

        self._seed_zhilian_paused()
        self.app.config.pop("RESUME_BLOCK_CHECKER", None)
        context = self.app.config["PIPELINE_CONTEXT"]
        executor = self.app.config["PIPELINE_EXECUTOR"]
        with mock.patch.object(
            context, "activate_run_browser",
            side_effect=FrozenBrowserBindingError("profile bind failed"),
        ), mock.patch.object(executor, "submit") as submit:
            failed = self.client.post(
                "/api/task/continue/zhilian-paused",
                json={"target_account": "b"},
            )
        self.assertEqual(failed.status_code, 409, failed.get_json())
        self.assertNotEqual(failed.status_code, 500)
        self.assertEqual(failed.get_json()["error"], "source_cdp_unavailable")
        self.assertEqual(
            self.store.get_screening_run("zhilian-paused")["status"], "paused")
        self.assertEqual(
            self.store.get_screening_run("zhilian-paused")["execution_params"]["browser_account"],
            "a",
        )
        submit.assert_not_called()

        self.app.config["RESUME_BLOCK_CHECKER"] = lambda _run: (True, "", "")
        with mock.patch.object(context, "activate_run_browser"), \
                mock.patch.object(executor, "submit") as retry_submit:
            retried = self.client.post(
                "/api/task/continue/zhilian-paused",
                json={"target_account": "b"},
            )
        self.assertEqual(retried.status_code, 200, retried.get_json())
        self.assertEqual(
            self.store.get_screening_run("zhilian-paused")["execution_params"]["browser_account"],
            "b",
        )
        retry_submit.assert_called_once()

    def test_default_resume_checker_probes_after_one_explicit_activation(self):
        """默认续跑顺序是一次显式激活，再做不启动浏览器的阻断检查。"""
        from webui import pipeline_exec

        self._seed_zhilian_paused()
        self.app.config.pop("RESUME_BLOCK_CHECKER", None)
        context = self.app.config["PIPELINE_CONTEXT"]
        executor = self.app.config["PIPELINE_EXECUTOR"]
        source = SimpleNamespace(
            preflight=mock.Mock(return_value=SimpleNamespace(ok=True)),
        )
        with mock.patch.object(context, "activate_run_browser") as activate, \
                mock.patch("webui.source.ZhilianCdpSource", return_value=source), \
                mock.patch.object(pipeline_exec, "probe_chrome_ready", return_value=(True, "")), \
                mock.patch.object(
                    pipeline_exec, "ensure_chrome_ready",
                    side_effect=AssertionError("resume checker must not launch Chrome"),
                ), mock.patch.object(executor, "submit") as submit:
            response = self.client.post("/api/task/continue/zhilian-paused")

        self.assertEqual(response.status_code, 200, response.get_json())
        activate.assert_called_once()
        source.preflight.assert_called_once()
        submit.assert_called_once()

    def test_default_checker_target_and_auto_switch_activation_failures_are_retryable(self):
        """目标账号和自动换号激活失败都保持暂停且不写入新身份。"""
        from webui.frozen_browser_identity import FrozenBrowserBindingError

        for target_account, auto_switch in (("b", False), ("", True)):
            with self.subTest(auto_switch=auto_switch):
                run_id = f"zhilian-activation-failure-{'auto' if auto_switch else 'target'}"
                self._seed_zhilian_paused(run_id)
                params = self.store.get_screening_run(run_id)["execution_params"]
                params["active_account_at_freeze"] = "a"
                self.store.update_screening_execution_params(run_id, params)
                self.app.config.pop("RESUME_BLOCK_CHECKER", None)
                context = self.app.config["PIPELINE_CONTEXT"]
                executor = self.app.config["PIPELINE_EXECUTOR"]
                with mock.patch.object(
                    context, "activate_run_browser",
                    side_effect=FrozenBrowserBindingError("profile bind failed"),
                ) as activate, mock.patch.object(
                    context, "load_legacy_advanced_settings",
                    return_value={"browser_account": "b"},
                ), mock.patch.object(executor, "submit") as submit:
                    body = {"target_account": target_account} if target_account else {}
                    response = self.client.post(
                        f"/api/task/continue/{run_id}", json=body)

                self.assertEqual(response.status_code, 409, response.get_json())
                self.assertNotEqual(response.status_code, 500)
                self.assertEqual(response.get_json()["error"], "source_cdp_unavailable")
                current = self.store.get_screening_run(run_id)
                self.assertEqual(current["status"], "paused")
                self.assertEqual(current["execution_params"]["browser_account"], "a")
                activate.assert_called_once()
                submit.assert_not_called()

    def test_cached_login_does_not_bypass_real_probe_and_cache_is_invalidated(self):
        from scripts import login_state_cache as cache
        self._seed_zhilian_paused()
        cache.write_login_state("a", "zhilian", "logged_in")
        self.app.config["RESUME_BLOCK_CHECKER"] = lambda run: (
            False, "source_login_required", "真实探测失败"
        )
        response = self.client.post("/api/task/continue/zhilian-paused")
        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(payload["error"], "block_not_resolved")
        self.assertEqual(payload["error_code"], "source_login_required")
        self.assertEqual(self.store.get_screening_run("zhilian-paused")["status"], "paused")
        self.assertIsNone(cache.read_cached_state("a", "zhilian"))

    def test_legacy_recoverable_failed_scrape_can_resume_from_checkpoint(self):
        """兼容修复前已落成 failed 的可恢复抓取任务，并继续原断点。"""
        for platform in ("boss", "zhilian"):
            with self.subTest(platform=platform):
                run_id = f"legacy-failed-{platform}"
                port = 9222 if platform == "boss" else 9223
                self.store.create_screening_run(
                    run_id,
                    source_count=2,
                    execution_params={
                        "platform": platform,
                        "script_params": {
                            "keyword": "前端", "city": ["上海"], "pages": 1,
                        },
                        "browser_account": "a",
                        "cdp_port": port,
                        "profile_key": f"{platform}:a",
                    },
                )
                self.store.update_screening_run(
                    run_id, status="running", current_stage="scrape",
                )
                self.store.save_checkpoint(run_id, "scrape", ["前端|上海"])
                self.store.update_screening_run(
                    run_id, status="failed", current_stage="scrape",
                    error_code="source_cdp_unavailable",
                    error_reason="旧版本错误落库",
                )
                context = self.app.config["PIPELINE_CONTEXT"]
                with mock.patch.object(context, "activate_run_browser"), \
                        mock.patch.object(
                            context,
                            "continue_execute_search",
                            return_value={"ok": True},
                        ) as continue_search:
                    response = self.client.post(f"/api/task/continue/{run_id}")

                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertEqual(
                    self.store.get_screening_run(run_id)["status"], "paused",
                )
                continue_search.assert_called_once_with(
                    run_id, _block_checked=True, account_switch_note=None,
                )

    def test_continue_uses_fresh_cdp_and_login_recheck_in_identity_order(self):
        """BOSS/智联继续都按身份绑定→CDP→新鲜登录态复检。"""
        from webui.source import SourceOutcome

        for platform in ("boss", "zhilian"):
            with self.subTest(platform=platform):
                run_id = f"fresh-recheck-{platform}"
                port = 9222 if platform == "boss" else 9223
                self.store.create_screening_run(
                    run_id,
                    source_count=1,
                    execution_params={
                        "platform": platform,
                        "script_params": {
                            "keyword": "前端", "city": ["上海"], "pages": 1,
                        },
                        "browser_account": "a",
                        "cdp_port": port,
                        "profile_key": f"{platform}:a",
                    },
                )
                self.store.update_screening_run(
                    run_id, status="running", current_stage="scrape",
                )
                self.store.save_checkpoint(run_id, "scrape", [])
                self.store.update_screening_run(
                    run_id, status="paused", current_stage="scrape",
                    error_code="source_login_required",
                )
                events = []
                source = SimpleNamespace(
                    preflight=mock.Mock(return_value=SourceOutcome.success()),
                    recheck_login=mock.Mock(
                        side_effect=lambda: (
                            events.append("login")
                            or SourceOutcome.failure(
                                failed_code="source_login_required",
                            )
                        ),
                    ),
                )
                context = self.app.config["PIPELINE_CONTEXT"]
                activation = mock.patch.object(
                    context,
                    "activate_run_browser",
                    side_effect=lambda _run: events.append("identity"),
                )
                probe = mock.patch(
                    "webui.pipeline_exec.probe_chrome_ready",
                    side_effect=lambda _port: (events.append("cdp") or (True, "")),
                )
                factory = (
                    mock.patch.object(context, "source_class", return_value=source)
                    if platform == "boss" else
                    mock.patch("webui.source.ZhilianCdpSource", return_value=source)
                )
                with activation, probe, factory, mock.patch.object(
                        self.app.config["PIPELINE_EXECUTOR"], "submit") as submit:
                    self.app.config.pop("RESUME_BLOCK_CHECKER", None)
                    response = self.client.post(f"/api/task/continue/{run_id}")

                self.assertEqual(response.status_code, 409, response.get_json())
                self.assertEqual(events, ["identity", "cdp", "login"])
                source.recheck_login.assert_called_once()
                source.preflight.assert_not_called()
                self.assertEqual(
                    self.store.get_screening_run(run_id)["status"], "paused",
                )
                submit.assert_not_called()
                self.app.config["RESUME_BLOCK_CHECKER"] = lambda run: (True, "", "")

    def test_user_paused_scrape_continue_rechecks_fresh_source_before_dispatch(self):
        """用户暂停的抓取继续前也必须按身份→CDP→登录态顺序复检。"""
        from webui.source import SourceOutcome

        run_id = "user-paused-fresh-recheck"
        self._seed_boss_scrape_paused(run_id)
        events = []
        source = SimpleNamespace(
            preflight=mock.Mock(return_value=SourceOutcome.success()),
            recheck_login=mock.Mock(
                side_effect=lambda: (
                    events.append("login") or SourceOutcome.success()
                ),
            ),
        )
        context = self.app.config["PIPELINE_CONTEXT"]
        with mock.patch.object(
                context, "activate_run_browser",
                side_effect=lambda _run: events.append("identity")), \
                mock.patch(
                    "webui.pipeline_exec.probe_chrome_ready",
                    side_effect=lambda _port: (
                        events.append("cdp") or (True, "")
                    ),
                ), \
                mock.patch.object(context, "source_class", return_value=source), \
                mock.patch.object(
                    context, "continue_execute_search", return_value={"ok": True},
                ) as continue_search:
            self.app.config.pop("RESUME_BLOCK_CHECKER", None)
            response = self.client.post(f"/api/task/continue/{run_id}")

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(events, ["identity", "cdp", "login"])
        source.recheck_login.assert_called_once()
        source.preflight.assert_not_called()
        continue_search.assert_called_once_with(
            run_id, _block_checked=True, account_switch_note=None,
        )

    def test_user_paused_fresh_source_failure_uses_cdp_reconnect_error(self):
        """新鲜 source 构造失败时不能继续沿用 user_paused 旧码。"""
        from webui.error_registry import ERROR_USER_MESSAGES

        run_id = "user-paused-source-factory-failure"
        self._seed_boss_scrape_paused(run_id)
        context = self.app.config["PIPELINE_CONTEXT"]
        with mock.patch.object(context, "activate_run_browser"), \
                mock.patch(
                    "webui.pipeline_exec.probe_chrome_ready",
                    return_value=(True, ""),
                ), mock.patch.object(context, "source_class", None), \
                mock.patch.object(context, "continue_execute_search") as continue_search:
            self.app.config.pop("RESUME_BLOCK_CHECKER", None)
            response = self.client.post(f"/api/task/continue/{run_id}")

        self.assertEqual(response.status_code, 409, response.get_json())
        payload = response.get_json()
        self.assertEqual(payload["error_code"], "source_cdp_unavailable")
        self.assertEqual(
            payload["error_reason"],
            ERROR_USER_MESSAGES["source_cdp_unavailable"],
        )
        self.assertEqual(
            self.store.get_screening_run(run_id)["status"], "paused",
        )
        continue_search.assert_not_called()

    def test_source_probe_unknown_is_retried_on_next_continue(self):
        """新鲜探测仍不确定时保持暂停，下一次继续不能绕过复检。"""
        from webui.source import SourceOutcome

        run_id = "unknown-source-retry"
        self._seed_boss_scrape_paused(run_id, error_code="source_unknown_error")
        source = SimpleNamespace(
            preflight=mock.Mock(return_value=SourceOutcome.success()),
            recheck_login=mock.Mock(side_effect=[
                SourceOutcome.failure(failed_code="source_unknown_error"),
                SourceOutcome.success(),
            ]),
        )
        context = self.app.config["PIPELINE_CONTEXT"]
        with mock.patch.object(context, "activate_run_browser"), \
                mock.patch(
                    "webui.pipeline_exec.probe_chrome_ready",
                    return_value=(True, ""),
                ), \
                mock.patch.object(context, "source_class", return_value=source), \
                mock.patch.object(
                    context, "continue_execute_search", return_value={"ok": True},
                ) as continue_search:
            self.app.config.pop("RESUME_BLOCK_CHECKER", None)
            first = self.client.post(f"/api/task/continue/{run_id}")
            second = self.client.post(f"/api/task/continue/{run_id}")

        self.assertEqual(first.status_code, 409, first.get_json())
        self.assertEqual(first.get_json()["error_code"], "source_unknown_error")
        self.assertEqual(first.get_json()["status"], "paused")
        self.assertEqual(second.status_code, 200, second.get_json())
        self.assertEqual(source.recheck_login.call_count, 2)
        continue_search.assert_called_once_with(
            run_id, _block_checked=True, account_switch_note=None,
        )

if __name__ == "__main__":
    unittest.main()
