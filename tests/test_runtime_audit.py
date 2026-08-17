"""Tests for safe local runtime audit events."""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from webui.logging_setup import configure_logging
from webui.runtime_audit import record_runtime_event, safe_runtime_hint


class _FakeStore:
    def __init__(self):
        self.events = []

    def append_task_event(self, task_id, event_type, payload):
        self.events.append((task_id, event_type, payload))


class RuntimeAuditTests(unittest.TestCase):
    def tearDown(self):
        logger = logging.getLogger("career_scout")
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    def test_safe_hint_redacts_credentials_and_removes_url_query(self):
        hint = safe_runtime_hint(
            "cookie=secret https://www.zhipin.com/job?a=token#fragment sk-abcdef1234567890"
        )
        self.assertIn("[REDACTED]", hint)
        self.assertIn("https://www.zhipin.com/job", hint)
        self.assertNotIn("token", hint)
        self.assertNotIn("secret", hint)
        self.assertNotIn("sk-abcdef1234567890", hint)

    def test_event_writes_safe_log_and_persists_task_context(self):
        store = _FakeStore()
        with tempfile.TemporaryDirectory() as tmp:
            configure_logging(tmp, force=True)
            payload = record_runtime_event(
                event="detail_terminal", stage="detail",
                failed_code="source_invalid_output",
                safe_hint="详情页停留 about:blank https://www.zhipin.com/job?a=token",
                task_id="task-1", correlation_id="corr-1", store=store,
            )
            content = (Path(tmp) / "career-scout.log").read_text(encoding="utf-8")
            self.tearDown()

        self.assertEqual(payload["event"], "detail_terminal")
        self.assertEqual(store.events[0][0:2], ("task-1", "runtime_audit"))
        self.assertIn("task=task-1", content)
        self.assertIn("corr=corr-1", content)
        self.assertIn("about:blank", content)
        self.assertNotIn("?a=token", content)


if __name__ == "__main__":
    unittest.main()