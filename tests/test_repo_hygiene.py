"""Repo hygiene guard for the public release."""

from __future__ import annotations

import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


class RepoHygieneTests(unittest.TestCase):
    def test_no_untracked_non_ignored_files(self):
        raw = _git("ls-files", "--others", "--exclude-standard", "-z")
        paths = [p for p in raw.split("\0") if p]
        self.assertEqual(
            paths,
            [],
            "Untracked non-ignored files should be committed or ignored",
        )

    def test_required_ignore_rules_exist(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        required = [
            ".venv/",
            ".webui-state/",
            ".chrome-profiles/",
            ".career-scout/",
            "*.db",
            "*.sqlite",
            "*.sqlite3",
            "*.log",
            ".env",
            "__pycache__/",
            "node_modules/",
            "tests/run_isolated_webui.py",
            "tests/sc002_24h_monitor.py",
            "tests/test_historical_recovery_realdb.py",
        ]
        missing = [rule for rule in required if rule not in text]
        self.assertEqual(missing, [])

    def test_no_sensitive_or_local_files_tracked(self):
        raw = _git("ls-files", "-z")
        paths = [p for p in raw.split("\0") if p]
        forbidden_dir_parts = {
            ".venv",
            ".webui-state",
            ".chrome-profiles",
            ".career-scout",
            "node_modules",
            "__pycache__",
            "logs",
            "tuning",
            ".trash",
            ".agents",
            ".uploads",
            ".workbuddy",
            ".worktrees",
            ".trae",
        }
        forbidden_names = {
            ".env",
            "run_isolated_webui.py",
            "sc002_24h_monitor.py",
            "test_historical_recovery_realdb.py",
        }
        forbidden_suffixes = (
            ".db",
            ".sqlite",
            ".sqlite3",
            ".log",
            ".pem",
            ".key",
            ".p12",
            ".whl",
            ".tar.gz",
            ".pyc",
            ".pyo",
        )
        bad = []
        for rel in paths:
            path = pathlib.PurePosixPath(rel)
            if any(part in forbidden_dir_parts for part in path.parts):
                bad.append(rel)
            elif path.name in forbidden_names or path.name.endswith(forbidden_suffixes):
                bad.append(rel)
        self.assertEqual(bad, [])

    def test_commit_author_email_is_noreply(self):
        email = _git("log", "-1", "--format=%ae").strip()
        self.assertEqual(
            email,
            "czyooutzilas-sketch@users.noreply.github.com",
        )
