import pathlib
import sys
import tempfile
import threading
import time
import unittest

from webui.process_executor import ArtifactSpec, ScraperExecutor


class ScraperExecutorTests(unittest.TestCase):
    def test_success_returns_bounded_output_and_valid_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            artifact = root / "result.json"
            command = [
                sys.executable, "-c",
                f"from pathlib import Path; Path({str(artifact)!r}).write_text('{{}}'); print('ok')",
            ]
            result = ScraperExecutor(max_output_bytes=1024).execute(
                command, timeout_seconds=5,
                artifacts=[ArtifactSpec(artifact, root=root, max_bytes=1024)],
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.returncode, 0)
        self.assertIn("ok", result.output_tail)

    def test_timeout_terminates_process(self):
        result = ScraperExecutor().execute(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_seconds=0.2,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.failure_code, "process_timeout")

    def test_cancel_event_terminates_process(self):
        cancelled = threading.Event()
        timer = threading.Timer(0.1, cancelled.set)
        timer.start()
        try:
            result = ScraperExecutor().execute(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                timeout_seconds=5, cancel_event=cancelled,
            )
        finally:
            timer.cancel()
        self.assertEqual(result.failure_code, "process_cancelled")

    def test_output_limit_terminates_noisy_process(self):
        forwarded = []
        result = ScraperExecutor(max_output_bytes=256).execute(
            [sys.executable, "-c", "print('x' * 10000)"], timeout_seconds=5,
            on_output=forwarded.append,
        )
        self.assertEqual(result.failure_code, "process_output_limit")
        self.assertLessEqual(len(result.output_tail.encode("utf-8")), 256)
        self.assertLessEqual(len("".join(forwarded).encode("utf-8")), 256)

    def test_artifact_must_stay_inside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "allowed"
            root.mkdir()
            outside = pathlib.Path(tmp) / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            result = ScraperExecutor().execute(
                [sys.executable, "-c", "pass"], timeout_seconds=5,
                artifacts=[ArtifactSpec(outside, root=root)],
            )
        self.assertEqual(result.failure_code, "artifact_path_invalid")


if __name__ == "__main__":
    unittest.main()
