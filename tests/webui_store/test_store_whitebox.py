"""033 V2 白箱 SQLite 持久化红测。"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from webui.store import TaskStore
from webui.whitebox import WhiteboxService
from webui.whitebox_evidence import ScrapeEvidence
from unittest import mock


class StoreWhiteboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_schema_33_has_three_whitebox_tables_and_constraints(self):
        with self.store._connection() as conn:
            version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            names = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertGreaterEqual(version, 33)
        self.assertTrue({"whitebox_runs", "whitebox_units", "whitebox_events"} <= names)

    def test_event_idempotency_and_null_has_more_are_preserved(self):
        run = self.store.create_whitebox_run(
            "scrape", "run-1", {"stages": ["scrape_list"], "units": [{"unit_key": "a"}]}
        )
        fact = {
            "idempotency_key": "page-a-1",
            "event_type": "page_completed",
            "occurred_at": "2026-09-05T00:00:00+08:00",
            "stage": "scrape_list",
            "unit_kind": "keyword_city",
            "unit_key": "a",
            "attempt_no": 1,
            "required_evidence": True,
            "payload": {"page": 1, "planned_pages": 1, "returned_count": 0,
                        "new_unique_count": 0, "has_more": None, "resume_page": 2},
        }
        first = self.store.append_whitebox_event(run["id"], fact)
        second = self.store.append_whitebox_event(run["id"], fact)
        self.assertEqual(first["sequence"], second["sequence"])
        events = self.store.list_whitebox_events(run["id"])
        self.assertEqual(len(events), 1)
        self.assertIsNone(json.loads(events[0]["payload_json"])["has_more"])

    def test_page_events_remain_after_unit_projection_update(self):
        run = self.store.create_whitebox_run(
            "scrape", "run-2", {"stages": ["scrape_list"], "units": [{"unit_key": "a"}]}
        )
        self.store.append_whitebox_event(run["id"], {
            "idempotency_key": "page-a-1", "event_type": "page_completed",
            "occurred_at": "2026-09-05T00:00:00+08:00", "stage": "scrape_list",
            "unit_key": "a", "attempt_no": 1, "required_evidence": True,
            "payload": {"page": 1, "planned_pages": 1, "returned_count": 1,
                        "new_unique_count": 1, "has_more": False, "resume_page": 2},
        })
        self.store.upsert_whitebox_unit(run["id"], {
            "stage": "scrape_list", "unit_kind": "keyword_city", "unit_key": "a",
            "attempt_no": 1, "status": "succeeded", "evidence_complete": True,
            "unit_unique_count": 1,
        })
        self.assertEqual(len(self.store.list_whitebox_events(run["id"])), 1)

    def test_emergency_records_are_append_only_and_import_idempotent(self):
        path = pathlib.Path(self.temp.name) / "emergency.jsonl"
        record = {"owner_kind": "scrape", "owner_id": "run-3", "event_type": "whitebox_incomplete",
                  "idempotency_key": "failure-1", "payload": {"token": "secret"}}
        self.store.append_whitebox_emergency(path, record)
        self.store.append_whitebox_emergency(path, record)
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)
        run = self.store.create_whitebox_run(
            "scrape", "run-3", {"stages": ["scrape_list"], "units": [{"unit_key": "a"}]}
        )
        imported = self.store.import_whitebox_emergency(path, run["id"])
        self.assertEqual(imported, 1)
        self.assertEqual(self.store.import_whitebox_emergency(path, run["id"]), 0)
        payload = self.store.list_whitebox_events(run["id"])[0]["payload_json"]
        self.assertNotIn("secret", payload)


class WhiteboxResumeAfterFailureTests(unittest.TestCase):
    """收尾族：任务失败后点「继续」，凭证重置并如实收口为成功。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_resume_whitebox_run_resets_terminal_failure(self):
        plan = {"stages": ["scrape_list"], "units": [{"unit_key": "a"}, {"unit_key": "b"}]}
        run = self.store.create_whitebox_run("scrape", "run-resume", plan)
        self.store.set_whitebox_lifecycle(run["id"], "terminal")
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE whitebox_runs SET conclusion='failed', evidence_complete=1, "
                "failed_unit_count=2, primary_code='source_cdp_unavailable', "
                "primary_reason='连不上调试浏览器', finalized_at='2026-01-01T00:00:00+08:00' "
                "WHERE id=?", (run["id"],),
            )

        resumed = self.store.resume_whitebox_run("scrape", "run-resume", plan)

        self.assertEqual(resumed["lifecycle_status"], "running")
        self.assertIsNone(resumed["conclusion"])
        self.assertEqual(resumed["failed_unit_count"], 0)
        self.assertIsNone(resumed["primary_code"])
        self.assertIsNone(resumed["finalized_at"])
        units = self.store.list_whitebox_units(run["id"])
        self.assertEqual({u["unit_key"] for u in units}, {"a", "b"})
        with self.assertRaises(ValueError):
            self.store.resume_whitebox_run(
                "scrape", "run-resume",
                {"stages": ["scrape_list"], "units": [{"unit_key": "c"}]},
            )

    def test_resume_after_failed_run_records_success_over_stale_failure(self):
        combos = [
            {"combo_key": "kw|上海", "keyword": "kw", "city": "上海"},
            {"combo_key": "kw|北京", "keyword": "kw", "city": "北京"},
        ]

        # 第一次：浏览器起不来，两个单元失败，白箱定稿为失败终态
        first = ScrapeEvidence(self.store, "run-resume", combos, 3)
        for combo in combos:
            outcome = mock.Mock(
                failed_code="source_cdp_unavailable",
                failed_reason="连不上调试浏览器",
            )
            first.failed(combo["combo_key"], outcome, reason="连不上调试浏览器")
        first.finish({
            "ok": False, "jobs": [], "total_scraped": 0, "total_matched": 0,
            "combinations": 2, "completed_combos": [], "error": "连不上调试浏览器",
        })
        failed_run = self.store.get_whitebox_run("scrape", "run-resume")
        self.assertEqual(failed_run["conclusion"], "failed")
        self.assertEqual(failed_run["lifecycle_status"], "terminal")

        # 第二次（继续）：resume 重置凭证，重新收集证据并成功收口
        second = ScrapeEvidence(self.store, "run-resume", combos, 3)
        for combo in combos:
            second.unit_started(combo["combo_key"], 3, 1)
            second.page(combo["combo_key"], {
                "page": 1, "planned_pages": 3, "returned_count": 30,
                "new_unique_count": 30, "has_more": False, "resume_page": 2,
            }, 3)
            outcome = mock.Mock(
                ok=True, empty_result=False, jobs=[{"job_id": "j"}],
                input_hash="hash-" + combo["combo_key"], page_evidence=[],
                source_exhausted=False, stop_reason=None, scope_complete=True,
                quality_counts={}, empty_evidence={},
            )
            second.completed(combo["combo_key"], outcome)
        second.finish({
            "ok": True, "jobs": [{"job_id": "j1"}], "total_scraped": 60,
            "total_matched": 60, "combinations": 2,
            "completed_combos": ["kw|上海", "kw|北京"], "error": "",
        })

        final_run = self.store.get_whitebox_run("scrape", "run-resume")
        self.assertEqual(final_run["conclusion"], "succeeded")
        self.assertEqual(final_run["lifecycle_status"], "terminal")
        self.assertEqual(final_run["evidence_complete"], 1)
        self.assertIsNone(final_run["primary_code"])

    def test_resume_keeps_completed_units_and_resets_unfinished(self):
        """继续时已完成单元的凭证行必须保留：删掉它会让「跳过已完成组合」核对不到，
        把已完成的事实降级成「恢复时缺少完成证据」。"""
        plan = {"stages": ["scrape_list"], "units": [{"unit_key": "a"}, {"unit_key": "b"}]}
        run = self.store.create_whitebox_run("scrape", "run-keep", plan)
        self.store.upsert_whitebox_unit(run["id"], {
            "stage": "scrape_list", "unit_kind": "keyword_city", "unit_key": "a",
            "attempt_no": 1, "status": "succeeded", "evidence_complete": True,
            "scope_complete": True, "planned_pages": 1, "completed_pages": 1,
            "returned_total_count": 30, "unit_unique_count": 30,
            "stop_reason": "target_reached",
        })

        self.store.resume_whitebox_run("scrape", "run-keep", plan)

        units = {unit["unit_key"]: unit for unit in self.store.list_whitebox_units(run["id"])}
        self.assertEqual(units["a"]["status"], "succeeded")
        self.assertTrue(units["a"]["evidence_complete"])
        self.assertEqual(units["a"]["unit_unique_count"], 30)
        self.assertIn("b", units)
        self.assertNotEqual(units["b"]["status"], "succeeded")

    def test_resume_skip_keeps_prior_completion_and_finalizes_success(self):
        """暂停→继续：暂停前抓完的组合不再被记成「跳过」，结论如实为成功。"""
        combos = [
            {"combo_key": "kw|上海", "keyword": "kw", "city": "上海"},
            {"combo_key": "kw|北京", "keyword": "kw", "city": "北京"},
        ]
        first = ScrapeEvidence(self.store, "run-skip", combos, 1)
        first.unit_started("kw|上海", 1, 1)
        first.page("kw|上海", {
            "page": 1, "planned_pages": 1, "returned_count": 30,
            "new_unique_count": 30, "has_more": False, "resume_page": 2,
        }, 1)
        first.completed("kw|上海", mock.Mock(
            ok=True, empty_result=False, jobs=[{"job_id": "j1"}],
            input_hash="hash-1",
            page_evidence=[{
                "event_type": "page_completed", "page": 1,
                "returned_count": 30, "new_unique_count": 30,
            }],
            source_exhausted=False,
            stop_reason="target_reached", scope_complete=True,
            quality_counts={}, empty_evidence={},
        ))
        first.pause({"completed_units": 1})

        second = ScrapeEvidence(self.store, "run-skip", combos, 1)
        second.skip("kw|上海")
        # 内存视图同步为已完成：不重记「跳过」，暂停计数等内存口径也不落后。
        self.assertEqual(second.units["kw|上海"]["status"], "succeeded")
        self.assertEqual(second.units["kw|上海"]["unit_unique_count"], 30)
        second.unit_started("kw|北京", 1, 1)
        second.page("kw|北京", {
            "page": 1, "planned_pages": 1, "returned_count": 30,
            "new_unique_count": 30, "has_more": False, "resume_page": 2,
        }, 1)
        second.completed("kw|北京", mock.Mock(
            ok=True, empty_result=False, jobs=[{"job_id": "j2"}],
            input_hash="hash-2", page_evidence=[], source_exhausted=False,
            stop_reason="target_reached", scope_complete=True,
            quality_counts={}, empty_evidence={},
        ))
        second.finish({
            "ok": True, "jobs": [{"job_id": "j1"}, {"job_id": "j2"}],
            "total_scraped": 60, "total_matched": 60, "combinations": 2,
            "completed_combos": ["kw|上海", "kw|北京"], "error": "",
        })

        run = self.store.get_whitebox_run("scrape", "run-skip")
        self.assertEqual(run["conclusion"], "succeeded")
        self.assertEqual(run["failed_unit_count"], 0)
        self.assertEqual(run["completed_unit_count"], 2)
        units = {unit["unit_key"]: unit for unit in self.store.list_whitebox_units(run["id"])}
        self.assertEqual(units["kw|上海"]["status"], "succeeded")
        skipped = [
            event for event in self.store.list_whitebox_events(run["id"])
            if event["event_type"] == "unit_skipped"
        ]
        self.assertEqual(skipped, [])

    def test_finish_counts_persisted_jobs_across_resume(self):
        """去重计数以已持久化抓取记录为准：继续前抓到的岗位也计入。"""
        combos = [
            {"combo_key": "kw|上海", "keyword": "kw", "city": "上海"},
            {"combo_key": "kw|北京", "keyword": "kw", "city": "北京"},
        ]
        self.store.create_screening_run("run-count", source_count=2)
        first = ScrapeEvidence(self.store, "run-count", combos, 1)
        first.unit_started("kw|上海", 1, 1)
        first.completed("kw|上海", mock.Mock(
            ok=True, empty_result=False, jobs=[{"job_id": "j1"}],
            input_hash="hash-1", page_evidence=[], source_exhausted=False,
            stop_reason="target_reached", scope_complete=True,
            quality_counts={}, empty_evidence={},
        ))
        first.pause({"completed_units": 1})
        self.store.save_scrape_combo_result(
            "run-count", "kw|上海", [{"platform_job_id": "j1"}], ["kw|上海"],
        )

        second = ScrapeEvidence(self.store, "run-count", combos, 1)
        second.skip("kw|上海")
        second.unit_started("kw|北京", 1, 1)
        second.completed("kw|北京", mock.Mock(
            ok=True, empty_result=False, jobs=[{"job_id": "j2"}],
            input_hash="hash-2", page_evidence=[], source_exhausted=False,
            stop_reason="target_reached", scope_complete=True,
            quality_counts={}, empty_evidence={},
        ))
        self.store.save_scrape_combo_result(
            "run-count", "kw|北京", [{"platform_job_id": "j2"}], ["kw|上海", "kw|北京"],
        )
        second.finish({
            "ok": True, "jobs": [{"job_id": "j1"}, {"job_id": "j2"}],
            "total_scraped": 2, "total_matched": 2, "combinations": 2,
            "completed_combos": ["kw|上海", "kw|北京"], "error": "",
        })

        run = self.store.get_whitebox_run("scrape", "run-count")
        self.assertEqual(run["run_unique_count"], 2)

    def test_report_events_are_stable_and_paginated(self):
        service = WhiteboxService(self.store)
        ref = service.begin(
            "scrape", "run-pagination",
            {"stages": ["scrape_list"], "units": [{"unit_key": "a"}]},
        )
        for index in range(3):
            service.record(ref, {
                "idempotency_key": f"event-{index}",
                "event_type": "task_started",
                "occurred_at": f"2026-09-05T00:00:0{index}+08:00",
                "stage": "scrape_list",
                "required_evidence": False,
                "payload": {"index": index},
            })
        first = service.report("scrape", "run-pagination", include_events=True, event_limit=2)
        self.assertEqual([event["sequence"] for event in first["events"]], [1, 2])
        self.assertTrue(first["events_truncated"])
        self.assertEqual(first["next_sequence"], 2)
        second = service.report(
            "scrape", "run-pagination", include_events=True,
            after_sequence=first["next_sequence"], event_limit=2,
        )
        self.assertEqual([event["sequence"] for event in second["events"]], [3])
        self.assertFalse(second["events_truncated"])


if __name__ == "__main__":
    unittest.main()
