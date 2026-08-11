import pathlib
import tempfile
import unittest
from unittest import mock

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

    def test_missing_zhilian_identity_blocks_and_keeps_paused(self):
        self._seed_zhilian_paused(with_identity=False)
        response = self.client.post("/api/task/continue/zhilian-paused")
        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(payload["error"], "missing_frozen_identity")
        self.assertEqual(self.store.get_screening_run("zhilian-paused")["status"], "paused")

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

if __name__ == "__main__":
    unittest.main()
