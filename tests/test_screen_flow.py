import json
import pathlib
import tempfile
import unittest

from webui.screen_flow import (
    build_round_context_payload,
    build_round_script_params,
    find_resumable_screen_run,
    load_resume_jd,
    load_resume_verdicts_with_fallback,
)
from webui.store import TaskStore

FILTERS = {"salary": ["20-30K"], "experience": ["3-5年"]}
FACTS = {"stable_key": "years", "value": "3"}


def _make_parent(store, scrape_task_id="scrape-1"):
    store.create_screening_run(
        scrape_task_id,
        source_count=1,
        execution_params={
            "script_params": {
                "keyword": "Python,后端",
                "city": ["上海"],
                "pages": 3,
            },
            "platform": "boss",
        },
    )


def _make_ai_run(store, run_id="screen-1", scrape_task_id="scrape-1",
                 status="paused", filters=None, profile="3年Python后端",
                 facts=None):
    store.create_screening_run(
        run_id,
        frozen_filters=FILTERS if filters is None else filters,
        source_count=10,
        execution_params={
            "platform": "boss",
            "scrape_task_id": scrape_task_id,
            "profile_summary": profile,
            "profile_facts": facts if facts is not None else FACTS,
        },
    )
    store.update_screening_run(run_id, status="running")
    store.update_screening_run(run_id, status=status)
    return store.get_screening_run(run_id)


class ScreenFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        _make_parent(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def _find(self, filters=None, profile="3年Python后端", facts=None):
        return find_resumable_screen_run(
            self.store, "scrape-1",
            filters if filters is not None else FILTERS,
            profile,
            facts if facts is not None else FACTS,
        )

    def test_find_resumable_prefers_paused_over_newer_failed(self):
        _make_ai_run(self.store, "failed-run", status="failed")
        _make_ai_run(self.store, "paused-run", status="paused")
        run = self._find()
        self.assertIsNotNone(run)
        self.assertEqual(run["id"], "paused-run")

    def test_find_resumable_failed(self):
        _make_ai_run(self.store, "failed-run", status="failed")
        self.assertEqual(self._find()["id"], "failed-run")

    def test_find_resumable_interrupted_user_finished(self):
        _make_ai_run(self.store, "finished-run", status="interrupted")
        self.store.update_screening_run(
            "finished-run", error_code="user_finished",
            error_reason="用户提前结束",
        )
        self.assertEqual(self._find()["id"], "finished-run")

    def test_find_resumable_partial(self):
        _make_ai_run(self.store, "partial-run", status="partial")
        self.assertEqual(self._find()["id"], "partial-run")

    def test_find_resumable_ignores_user_cancelled_interrupted(self):
        _make_ai_run(self.store, "cancel-run", status="interrupted")
        self.store.update_screening_run(
            "cancel-run", error_code="user_cancelled", error_reason="用户取消")
        self.assertIsNone(self._find())

    def test_find_resumable_requires_same_fields_profile_and_facts(self):
        _make_ai_run(self.store, "paused-run", status="paused")
        self.assertIsNone(self._find(filters={"salary": ["30-50K"]}))
        self.assertIsNone(self._find(profile="其它画像"))
        self.assertIsNone(self._find(facts={"stable_key": "city", "value": "上海"}))

    def test_find_resumable_normalizes_facts_order(self):
        _make_ai_run(self.store, "paused-run", status="paused")
        reordered = {"value": "3", "stable_key": "years"}
        run = self._find(facts=reordered)
        self.assertIsNotNone(run)

    def test_build_round_script_params_merges_parent_params(self):
        run = _make_ai_run(self.store, "paused-run", status="paused")
        params = build_round_script_params(self.store, run, FILTERS, "boss")
        self.assertEqual(params["keyword"], "Python,后端")
        self.assertEqual(params["city"], ["上海"])
        self.assertEqual(params["screening"], FILTERS)
        self.assertEqual(params["platform"], "boss")

    def test_build_round_context_payload_fields_complete(self):
        run = _make_ai_run(self.store, "paused-run", status="paused")
        ctx = build_round_context_payload(self.store, run)
        self.assertEqual(ctx["platform"], "boss")
        self.assertEqual(ctx["keywords"], ["Python", "后端"])
        self.assertEqual(ctx["cities"], ["上海"])
        self.assertEqual(ctx["screening_fields"], FILTERS)
        self.assertEqual(ctx["profile_summary"], "3年Python后端")
        self.assertEqual(ctx["profile_facts"], FACTS)
        self.assertEqual(ctx["scrape_task_id"], "scrape-1")
        self.assertEqual(ctx["screen_run_id"], "paused-run")
        self.assertEqual(ctx["status"], "paused")
        self.assertTrue(ctx["resumable"])
        self.assertTrue(ctx["has_frozen_filters"])

    def test_build_round_context_payload_user_finished_is_closed(self):
        """结束并保存后 round_context 必须持久化为不可续的阶段性完成态。"""
        _make_ai_run(self.store, "finished-run", status="interrupted")
        self.store.update_screening_run(
            "finished-run", error_code="user_finished",
            error_reason="用户提前结束，已保存部分结果",
        )
        ctx = build_round_context_payload(
            self.store, self.store.get_screening_run("finished-run"),
        )
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["status"], "partial")
        self.assertFalse(ctx["resumable"])
        self.assertEqual(ctx["screen_run_id"], "finished-run")

    def test_build_round_context_empty_filters_are_valid_unlimited_conditions(self):
        _make_ai_run(self.store, "unlimited-run", status="paused", filters={})
        ctx = build_round_context_payload(self.store, self.store.get_screening_run("unlimited-run"))
        self.assertEqual(ctx["screening_fields"], {})
        self.assertFalse(ctx["has_frozen_filters"])
    def test_build_round_context_from_snapshot_with_screen_run_id(self):
        _make_ai_run(self.store, "paused-run", status="paused")
        snapshot_id = self.store.save_pipeline_result(
            {
                "ok": True, "jobs": [], "dropped": [],
                "total_scraped": 0, "total_kept": 0,
            },
            {"platform": "boss"},
            status="partial",
            execution_params={
                "platform": "boss",
                "scrape_task_id": "scrape-1",
                "screen_run_id": "paused-run",
            },
        )
        snapshot = self.store.get_screening_run(snapshot_id)
        ctx = build_round_context_payload(self.store, snapshot)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["screen_run_id"], "paused-run")

    def test_build_round_context_from_scraped_only_snapshot(self):
        run_id = self.store.save_scraped_only_snapshot(
            {
                "ok": True,
                "jobs": [{"platform_job_id": "j1", "title": "岗位"}],
                "dropped": [], "total_scraped": 1,
            },
            {"platform": "boss", "keyword": "Python", "city": ["上海"]},
            scrape_task_id="scrape-1", platform="boss",
            profile_summary="3年Python后端", profile_facts=FACTS,
        )
        ctx = build_round_context_payload(self.store, self.store.get_screening_run(run_id))
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["keywords"], ["Python"])
        self.assertEqual(ctx["cities"], ["上海"])
        self.assertEqual(ctx["scrape_task_id"], "scrape-1")
        self.assertEqual(ctx["profile_summary"], "3年Python后端")
        self.assertEqual(ctx["profile_facts"], FACTS)
        self.assertFalse(ctx["resumable"])

    def test_build_round_context_paused_scrape_without_scrape_task_id(self):
        run_id = "paused-scrape"
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "Go", "city": ["北京"]},
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(run_id, status="paused", current_stage="scrape")
        ctx = build_round_context_payload(self.store, self.store.get_screening_run(run_id))
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["keywords"], ["Go"])
        self.assertEqual(ctx["cities"], ["北京"])
        self.assertEqual(ctx["scrape_task_id"], "")
        self.assertTrue(ctx["resumable"])
        self.assertEqual(ctx["status"], "paused")
        self.assertTrue(ctx["resumable"])

    def test_build_round_context_from_snapshot_falls_back_to_latest_run(self):
        _make_ai_run(self.store, "paused-run", status="paused")
        snapshot_id = self.store.save_pipeline_result(
            {
                "ok": True, "jobs": [], "dropped": [],
                "total_scraped": 0, "total_kept": 0,
            },
            {"platform": "boss"},
            status="partial",
            execution_params={"platform": "boss", "scrape_task_id": "scrape-1"},
        )
        snapshot = self.store.get_screening_run(snapshot_id)
        ctx = build_round_context_payload(self.store, snapshot)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["screen_run_id"], "paused-run")


class LoadResumeJdTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_load_resume_jd_falls_back_to_screening_results(self):
        snapshot_id = self.store.save_pipeline_result(
            {
                "ok": True,
                "jobs": [{"platform_job_id": "pid-1", "title": "岗位", "jd": "回退 JD"}],
                "dropped": [], "total_scraped": 1,
            },
            {"platform": "boss"},
        )
        missing = pathlib.Path(self.temp.name) / "missing.json"
        self.assertEqual(
            load_resume_jd(self.store, str(missing), snapshot_id), {"pid-1": "回退 JD"})

    def test_load_resume_jd_prefers_checkpoint_file(self):
        run_id = "jd-file-first"
        checkpoint = pathlib.Path(self.temp.name) / "jd.json"
        checkpoint.write_text(json.dumps({"pid-1": "文件 JD"}), encoding="utf-8")
        self.assertEqual(
            load_resume_jd(self.store, str(checkpoint), run_id), {"pid-1": "文件 JD"})


class LoadResumeVerdictsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        _make_parent(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def _seed_partial_run(self, run_id="partial-run", filters=None, profile="3年Python后端", facts=None):
        _make_ai_run(self.store, run_id, status="partial", filters=filters or FILTERS, profile=profile, facts=facts if facts is not None else FACTS)
        self.store.save_checkpoint(run_id, "ai_rough", ["a", "b", "c"])
        self.store.save_screening_verdicts(run_id, {"a": {"verdict": "kept", "reason": ""}})
        return run_id

    def _save_snapshot(self, filters=None, profile="3年Python后端", scrape_task_id="scrape-1"):
        return self.store.save_pipeline_result(
            {
                "ok": True,
                "jobs": [{"platform_job_id": "a", "title": "A", "verdict": "kept"}],
                "dropped": [
                    {"platform_job_id": "b", "title": "B", "reason": "经验不符"},
                    {"platform_job_id": "c", "title": "C", "reason": "薪资不符"},
                ],
                "total_scraped": 3, "total_kept": 1, "total_dropped": 2,
                "profile_summary": profile,
            },
            {
                "screening": filters if filters is not None else FILTERS,
                "platform": "boss", "scrape_task_id": scrape_task_id,
            },
            execution_params={"platform": "boss", "scrape_task_id": scrape_task_id},
        )

    def test_falls_back_to_same_round_snapshot_verdicts(self):
        run_id = self._seed_partial_run()
        self._save_snapshot()
        verdicts = load_resume_verdicts_with_fallback(
            self.store, run_id, "boss", "scrape-1", FILTERS, "3年Python后端")
        self.assertEqual(set(verdicts), {"a", "b", "c"})
        self.assertEqual(verdicts["b"]["verdict"], "dropped")
        self.assertEqual(verdicts["a"]["verdict"], "kept")

    def test_skips_snapshot_when_conditions_differ(self):
        run_id = self._seed_partial_run()
        self._save_snapshot(filters={"salary": ["30-50K"]})
        verdicts = load_resume_verdicts_with_fallback(
            self.store, run_id, "boss", "scrape-1", FILTERS, "3年Python后端")
        self.assertEqual(set(verdicts), {"a"})


if __name__ == "__main__":
    unittest.main()
