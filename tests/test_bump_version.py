"""Focused tests for release closure helpers in scripts/bump_version.py."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUMP = ROOT / "scripts" / "bump_version.py"


class BumpVersionCliTest(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        # CI Windows 控制台默认 GBK，脚本输出含无法编码字符会炸：显式 UTF-8。
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, str(BUMP), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=60,
        )

    def test_help_exposes_release_flags(self) -> None:
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--set", result.stdout)
        self.assertIn("--allow-downgrade", result.stdout)
        self.assertIn("--expect", result.stdout)

    def test_check_current_version_passes(self) -> None:
        result = self.run_cli("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_exact_version_is_rejected(self) -> None:
        result = self.run_cli("--set", "not-a-version")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
