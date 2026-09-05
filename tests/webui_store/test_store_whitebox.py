"""033 V2 白箱 SQLite 持久化红测。"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from webui.store import TaskStore
from webui.whitebox import WhiteboxService


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
        self.assertEqual(version, 33)
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
