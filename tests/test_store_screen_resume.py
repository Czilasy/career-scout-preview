import pathlib
import tempfile
import unittest

from webui.store import TaskStore


def _make_ai_run(store, run_id, scrape_task_id="scrape-1", status="paused",
                 platform="boss", filters=None, profile="3年Python后端",
                 facts=None, updated_later=False):
    store.create_screening_run(
        run_id,
        frozen_filters=filters or {"salary": ["20-30K"]},
        source_count=10,
        execution_params={
            "platform": platform,
            "scrape_task_id": scrape_task_id,
            "profile_summary": profile,
            "profile_facts": facts or {"stable_key": "years", "value": "3"},
        },
    )
    store.update_screening_run(run_id, status="running")
    store.update_screening_run(run_id, status=status)
    return store.get_screening_run(run_id)


class StoreScreenResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_load_screening_jd_map_prefers_platform_job_id(self):
        snapshot_id = self.store.save_pipeline_result(
            {
                "ok": True,
                "jobs": [{
                    "platform_job_id": "pid-1", "job_id": "internal-1",
                    "title": "岗位", "jd": "JD 内容",
                }],
                "dropped": [], "total_scraped": 1,
            },
            {"platform": "boss"},
        )
        jd_map = self.store.load_screening_jd_map(snapshot_id)
        self.assertEqual(jd_map, {"pid-1": "JD 内容"})

    def test_load_screening_jd_map_falls_back_to_job_id_and_skips_empty(self):
        run_id = "jd-fallback-run"
        self.store.create_screening_run(run_id, source_count=1)
        with self.store._connection() as conn:
            for row in (
                ("a", None, "A JD"),
                ("", "b", "B JD"),
                ("c", "c-internal", ""),
            ):
                conn.execute(
                    "INSERT INTO screening_results "
                    "(id, run_id, platform, platform_job_id, job_id, verdict, "
                    "created_at, is_dropped, jd) "
                    "VALUES (?, ?, 'boss', ?, ?, '', '2026-01-01T00:00:00', 0, ?)",
                    (f"id-{row[0] or row[1]}", run_id, row[0], row[1], row[2]),
                )
            conn.execute(
                "INSERT INTO screening_results "
                "(id, run_id, platform, platform_job_id, job_id, verdict, "
                "created_at, is_dropped, jd) "
                "VALUES ('dropped', ?, 'boss', 'drop-1', NULL, 'dropped', "
                "'2026-01-01T00:00:00', 1, '不应读取')",
                (run_id,),
            )
        jd_map = self.store.load_screening_jd_map(run_id)
        self.assertEqual(jd_map, {"a": "A JD", "b": "B JD"})

    def test_latest_screen_runs_for_source_keeps_status_priority(self):
        _make_ai_run(self.store, "paused-run", status="paused")
        _make_ai_run(self.store, "failed-run", status="failed")
        candidates = self.store.latest_screen_runs_for_source(
            "scrape-1", statuses=("paused", "failed", "interrupted", "partial"),
        )
        self.assertEqual([run["id"] for run in candidates], ["paused-run", "failed-run"])
        self.assertEqual(candidates[0]["status"], "paused")
        self.assertEqual(candidates[1]["status"], "failed")

    def test_latest_screen_runs_for_source_returns_empty_for_unknown(self):
        self.assertEqual(
            self.store.latest_screen_runs_for_source("missing", ("paused",)), [])


if __name__ == "__main__":
    unittest.main()
