import gc
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
import warnings

from webui.process_executor import ArtifactSpec, ScraperExecutor


class ScraperExecutorTests(unittest.TestCase):
    def test_execute_closes_subprocess_output_pipe(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            result = ScraperExecutor().execute(
                [sys.executable, "-c", "print('done')"], timeout_seconds=5,
            )
            gc.collect()

        self.assertTrue(result.ok)
        resource_warnings = [w for w in caught if issubclass(w.category, ResourceWarning)]
        self.assertEqual(resource_warnings, [])

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


class CancelProcessTreeTests(unittest.TestCase):
    """T078 RED: cancel signal must terminate the active process tree quickly.

    Contracts:
    - SC-010 (spec.md L279): cancel terminates new source/AI work within 30s.
    - process_executor is the boundary that actually kills subprocesses when
      ``cancel_event`` is set. Tests here verify the boundary itself, before
      the runner-level wiring is added in T079.
    """

    def test_cancel_terminates_child_processes_within_30_seconds(self):
        """cancel_event must kill the spawned process tree, not just the parent.

        Spawns a parent python that spawns a long-running child; after cancel
        the child PID must no longer be alive. Wall clock bounded ≤ 30s to
        satisfy SC-010.
        """
        import subprocess as _sp
        cancelled = threading.Event()
        # Parent python spawns a child that writes its PID and sleeps.
        # The child PID path is passed via env var to avoid shell-quoting issues.
        with tempfile.TemporaryDirectory() as tmp:
            child_pid_file = pathlib.Path(tmp) / "child.pid"
            program = (
                "import os, subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', "
                "\"import os, time; "
                "open(os.environ['BOSS_TEST_CHILD_PID'], 'w').write(str(os.getpid())); "
                "time.sleep(60)\"])\n"
                "time.sleep(60)\n"
            )
            env = {**os.environ, "BOSS_TEST_CHILD_PID": str(child_pid_file)}
            timer = threading.Timer(0.5, cancelled.set)
            timer.start()
            try:
                started = time.monotonic()
                result = ScraperExecutor().execute(
                    [sys.executable, "-c", program],
                    timeout_seconds=30, cancel_event=cancelled, env=env,
                )
                elapsed = time.monotonic() - started
            finally:
                timer.cancel()
            self.assertEqual(result.failure_code, "process_cancelled")
            self.assertLessEqual(elapsed, 30.0, "cancel must terminate within 30s (SC-010)")
            # Child PID file should exist; the child must be dead.
            self.assertTrue(child_pid_file.exists(), "child should have written its pid")
            child_pid = int(child_pid_file.read_text().strip())
            # Give the OS a moment to reap; child should no longer be alive.
            time.sleep(0.3)
            child_alive = False
            try:
                if os.name == "nt":
                    # os.kill on Windows with signal 0 doesn't work reliably; use tasklist.
                    proc = _sp.run(
                        ["tasklist", "/FI", f"PID eq {child_pid}", "/NH", "/FO", "CSV"],
                        capture_output=True, text=True, timeout=2,
                    )
                    child_alive = str(child_pid) in proc.stdout
                else:
                    os.kill(child_pid, 0)
                    child_alive = True
            except (ProcessLookupError, OSError, _sp.TimeoutExpired):
                child_alive = False
            self.assertFalse(child_alive,
                             f"child PID {child_pid} still alive after cancel — process tree not terminated")

    def test_cancel_returns_process_cancelled_failure_code(self):
        """cancel_event must surface as failure_code='process_cancelled'."""
        cancelled = threading.Event()
        timer = threading.Timer(0.1, cancelled.set)
        timer.start()
        try:
            result = ScraperExecutor().execute(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                timeout_seconds=10, cancel_event=cancelled,
            )
        finally:
            timer.cancel()
        self.assertEqual(result.failure_code, "process_cancelled")
        self.assertFalse(result.ok)

    def test_cancel_after_completion_does_not_raise(self):
        """Setting cancel_event after the process exits must not crash execute()."""
        cancelled = threading.Event()
        # Process exits almost immediately.
        result = ScraperExecutor().execute(
            [sys.executable, "-c", "print('done')"],
            timeout_seconds=5, cancel_event=cancelled,
        )
        # Now set the event post-hoc; nothing should happen.
        cancelled.set()
        self.assertTrue(result.ok)

    def test_windows_creationflags_suppress_console_window(self):
        """Windows 抓取子进程必须带 CREATE_NO_WINDOW（桌面版不闪控制台窗）。

        回归：此前只有 CREATE_NEW_PROCESS_GROUP，抓取开始/结束时各闪一次
        空白 PowerShell 窗口。
        """
        if os.name != "nt":
            self.skipTest("仅 Windows 有 creationflags 语义")
        import subprocess as _sp

        captured: dict = {}
        original_popen = _sp.Popen

        def fake_popen(command, **kwargs):
            captured.update(kwargs)
            return original_popen([sys.executable, "-c", "pass"], **kwargs)

        with unittest.mock.patch("subprocess.Popen", fake_popen):
            result = ScraperExecutor().execute(
                [sys.executable, "-c", "pass"], timeout_seconds=5,
            )
        self.assertTrue(result.ok)
        flags = int(captured.get("creationflags", 0))
        self.assertTrue(flags & _sp.CREATE_NEW_PROCESS_GROUP,
                        "必须保留独立进程组（取消时能整树终止）")
        self.assertTrue(flags & _sp.CREATE_NO_WINDOW,
                        "必须抑制控制台窗口（0x08000000）")

    def test_windows_taskkill_suppresses_console_window(self):
        """Windows 整树终止 taskkill 必须带 CREATE_NO_WINDOW（不闪空白 CMD）。"""
        if os.name != "nt":
            self.skipTest("仅 Windows 有 creationflags 语义")
        import subprocess as _sp

        process = unittest.mock.Mock()
        process.pid = 12345
        process.poll.return_value = None
        process.wait.return_value = None
        process.kill.return_value = None
        with unittest.mock.patch("webui.process_executor.subprocess.run") as run:
            ScraperExecutor._terminate_tree(process)
        self.assertEqual(run.call_args.args[0][0], "taskkill")
        flags = int(run.call_args.kwargs.get("creationflags", 0))
        self.assertTrue(flags & _sp.CREATE_NO_WINDOW,
                        "taskkill 必须抑制控制台窗口（0x08000000）")


if __name__ == "__main__":
    unittest.main()
