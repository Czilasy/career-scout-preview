"""B044: raw AI response logging tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from webui.ai_raw_log import MAX_RAW_AI_RESPONSE_BYTES, record_raw_ai_response


class RawAiResponseLogTests(unittest.TestCase):
    def _payload(self, log_dir: Path) -> dict:
        line = (log_dir / "ai_raw.log").read_text(encoding="utf-8").strip()
        return json.loads(line.split("career_scout.ai_raw ", 1)[1])

    def test_writes_body_correlation_and_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            record_raw_ai_response("corr-1", 2, '{"ok": true}', log_dir=tmp)
            payload = self._payload(Path(tmp))
            self.assertEqual(payload["correlation_id"], "corr-1")
            self.assertEqual(payload["attempt_index"], 2)
            self.assertEqual(payload["body"], '{"ok": true}')

    def test_truncates_oversized_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = "x" * (MAX_RAW_AI_RESPONSE_BYTES + 100)
            record_raw_ai_response("corr-2", 1, body, log_dir=tmp)
            payload = self._payload(Path(tmp))
            self.assertTrue(payload["truncated"])
            self.assertEqual(payload["original_length"], len(body))
            self.assertLessEqual(len(payload["body"]), MAX_RAW_AI_RESPONSE_BYTES)

    def test_redacts_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = "key=sk-secret1234567890 cookie=abc123 bearer=token123"
            record_raw_ai_response("corr-3", 1, body, log_dir=tmp)
            payload = self._payload(Path(tmp))
            self.assertIn("[REDACTED]", payload["body"])
            self.assertNotIn("sk-secret1234567890", payload["body"])
            self.assertNotIn("abc123", payload["body"])
            self.assertNotIn("token123", payload["body"])

    def test_concurrent_writes_keep_all_records(self):
        from concurrent.futures import ThreadPoolExecutor

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            expected = 8 * 25

            def worker(worker_index: int):
                for index in range(25):
                    record_raw_ai_response(
                        f"c-{worker_index}-{index}", 1, f"body-{worker_index}-{index}",
                        log_dir=tmp_path,
                    )

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(worker, range(8)))

            lines = (tmp_path / "ai_raw.log").read_text(
                encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), expected)


if __name__ == "__main__":
    unittest.main()
