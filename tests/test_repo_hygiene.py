"""Repo hygiene guard for the public release."""

from __future__ import annotations

import ast
import json
import os
import pathlib
import re
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
        encoding="utf-8",
        errors="replace",
    ).stdout


def _is_ignored(path: str) -> bool:
    return subprocess.run(
        ["git", "check-ignore", "-q", "--", path],
        cwd=ROOT, check=False, capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).returncode == 0


def _local_env_paths() -> list[str]:
    """本机环境特征从环境变量或本地配置读取，避免硬编码进公开测试。"""
    paths = [p for p in os.environ.get("CAREER_SCOUT_LOCAL_ENV_PATHS", "").split("|") if p]
    config = pathlib.Path.home() / ".career-scout" / "local_env.txt"
    if config.is_file():
        paths.extend(
            line.strip() for line in config.read_text(encoding="utf-8").splitlines() if line.strip()
        )
    return paths


class RepoHygieneTests(unittest.TestCase):
    def test_no_untracked_non_ignored_files(self):
        raw = _git("ls-files", "--others", "--exclude-standard", "-z")
        paths = [p for p in raw.split("\0") if p]
        self.assertEqual(
            paths,
            [],
            "Untracked non-ignored files should be committed or ignored",
        )

    def test_no_temp_logs_in_project_root(self):
        """项目根目录不得残留测试中转文件（fulltest.log 等）。"""
        known = ("fulltest.log", "full_test_run.log", "testrun.log")
        present = [name for name in known if (ROOT / name).exists()]
        self.assertEqual(present, [], "已知测试中转文件必须删除，不得遗留")
        root_logs = sorted(
            p.name for p in ROOT.glob("*.log") if p.is_file()
        )
        self.assertEqual(
            root_logs, [],
            "项目根目录不得存在 .log 文件：测试日志/重定向必须写入系统临时目录",
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
            ".env.*",
            "!.env.example",
            "__pycache__/",
            "node_modules/",
            "tests/run_isolated_webui.py",
            "tests/sc002_24h_monitor.py",
        ]
        missing = [rule for rule in required if rule not in text]
        self.assertEqual(missing, [])

    def test_gitignore_rules_are_effective(self):
        must_ignore = [
            ".env.local",
            ".env.production",
            "scratch.db",
            ".chrome-profiles/account_x",
            "logs/run.log",
            "roadmap/REFERENCE_GET_JOBS.md",
            ".career-scout/webui.db",
            "webui/node_modules/pkg/index.js",
            "docs/private.md",
        ]
        must_not_ignore = [
            "webui/dist/assets/app.js",
            ".env.example",
            "hooks/pre-commit",
            "hooks/pre-push",
        ]
        bad_ignored = [p for p in must_ignore if not _is_ignored(p)]
        bad_tracked = [p for p in must_not_ignore if _is_ignored(p)]
        self.assertEqual(bad_ignored, [], "关键路径应被 .gitignore 忽略")
        self.assertEqual(bad_tracked, [], "公开必需路径不应被 .gitignore 忽略")

    def test_hooks_are_tracked(self):
        raw = _git("ls-files", "--", "hooks/pre-commit", "hooks/pre-push")
        paths = [p for p in raw.splitlines() if p]
        self.assertEqual(paths, ["hooks/pre-commit", "hooks/pre-push"])

    def test_hooks_path_is_enabled(self):
        result = subprocess.run(
            ["git", "config", "--get", "core.hooksPath"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(result.stdout.strip(), "hooks", "请先运行 git config core.hooksPath hooks")

    def test_version_consistency(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        self.assertIsNotNone(match, "pyproject.toml 缺少 version")
        expected = match.group(1)
        checks = [
            ("webui/package.json", r'^\s*"version"\s*:\s*"([^"]+)"'),
            ("scripts/boss_cdp_raw.py", r'^__version__\s*=\s*["\']([^"\']+)["\']'),
            ("tests/test_desktop_shell.py", r'^\s*self\.assertEqual\(version,\s*"([^"]+)"\)'),
            ("README.md", r'^# Career Scout v([\d.]+)(?= ·)'),
        ]
        mismatches = []
        for rel, pattern in checks:
            file_match = re.search(pattern, (ROOT / rel).read_text(encoding="utf-8"), re.MULTILINE)
            if not file_match or file_match.group(1) != expected:
                mismatches.append(rel)
        lock = json.loads((ROOT / "webui/package-lock.json").read_text(encoding="utf-8"))
        lock_values = [lock.get("version"), (lock.get("packages") or {}).get("", {}).get("version")]
        if any(value != expected for value in lock_values if value):
            mismatches.append("webui/package-lock.json")
        uv = (ROOT / "uv.lock").read_text(encoding="utf-8")
        uv_match = re.search(r'^name = "career-scout"\nversion = "([^"]+)"', uv, re.MULTILINE)
        if not uv_match or uv_match.group(1) != expected:
            mismatches.append("uv.lock")
        self.assertEqual(mismatches, [], f"版本号应全仓库一致：{expected}")

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
            ".trash",
            ".agents",
            ".uploads",
            ".workbuddy",
            ".worktrees",
        }
        forbidden_names = {
            ".env",
            "run_isolated_webui.py",
            "sc002_24h_monitor.py",
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
            # 调优实验运行数据只禁止仓库根目录下的 tuning/（tests/tuning 是测试包）。
            if path.parts and path.parts[0] == "tuning":
                bad.append(rel)
                continue
            if any(part in forbidden_dir_parts for part in path.parts) or path.name in forbidden_names or path.name.endswith(forbidden_suffixes):
                bad.append(rel)
        self.assertEqual(bad, [])

    def test_commit_identity_email_is_gmail(self):
        ident = _git("log", "-1", "--format=%ae|%ce").strip()
        author, committer = ident.split("|", 1)
        self.assertEqual(author, "czyooutzilas@gmail.com")
        self.assertEqual(committer, "czyooutzilas@gmail.com")

    def test_commit_messages_follow_conventional_commits(self):
        raw = _git("log", "--no-merges", "-3", "--format=%s")
        pattern = re.compile(
            r"^(feat|fix|docs|style|refactor|test|perf|build|ci|chore|revert)(\([^)]+\))?: .+"
        )
        bad = [line for line in raw.splitlines() if line and not pattern.match(line)]
        self.assertEqual(bad, [], "最近提交信息应使用 Conventional Commits 格式")

    def test_no_local_paths_or_credentials_in_tracked_files(self):
        raw = _git("ls-files", "-z")
        paths = [p for p in raw.split("\0") if p]
        text_suffixes = {
            ".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml",
            ".ini", ".cfg", ".vue", ".ts", ".js", ".mjs", ".css",
            ".html", ".bat", ".sh", ".ps1",
        }
        local_env = _local_env_paths()
        patterns = [
            re.compile(r"sk-[A-Za-z0-9]{20,}"),
            re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
            re.compile(r"AKIA[0-9A-Z]{16}"),
            re.compile(
                r"[A-Za-z]:[\\/]Users[\\/]"
                r"(?!(?:demo|example|fake|mock|test|sample|public|guest)[\\/-])"
                r"[A-Za-z0-9_.-]+"
            ),
        ] + [re.compile(re.escape(p), re.IGNORECASE) for p in local_env]
        issues = []
        for rel in paths:
            suffix = pathlib.PurePosixPath(rel).suffix.lower()
            if suffix not in text_suffixes:
                continue
            text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
            # dist 文本产物豁免本地路径规则（构建机路径会回显进产物），
            # 但凭据模式（sk-/PEM/AKIA）对 dist 同样全量生效（FR-009）
            active = patterns
            if rel.startswith("webui/dist/"):
                active = patterns[:3]
            for rx in active:
                m = rx.search(text)
                if m:
                    issues.append(f"{rel}: 命中 {m.group(0)!r}")
        self.assertEqual(issues, [], "已跟踪文本文件不得包含本地路径或凭据")

    def test_silent_except_pass_baseline(self):
        """pass-only 吞噬基线（031 B4 / FR-012）：只许下降，白名单与代码注释一一对应。

        口径：AST ExceptHandler，type ∈ {Exception, BaseException, 裸}，body 为单
        ``pass``。非白名单文件出现即失败（新增吞噬 = 测试失败）。
        """
        allowed = {"store.py", "source_fake.py"}
        marker = "吞噬白名单"
        total = 0
        for base in ("webui", "scripts"):
            for p in (ROOT / base).rglob("*.py"):
                if "__pycache__" in str(p):
                    continue
                src = p.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(src)
                lines = src.splitlines()
                for x in ast.walk(tree):
                    if not (
                        isinstance(x, ast.ExceptHandler)
                        and len(x.body) == 1
                        and isinstance(x.body[0], ast.Pass)
                    ):
                        continue
                    ty = ast.unparse(x.type) if x.type else "bare"
                    if ty not in ("Exception", "BaseException", "bare"):
                        continue
                    rel = p.relative_to(ROOT).as_posix()
                    total += 1
                    if p.name not in allowed:
                        self.fail(
                            f"{rel}:{x.lineno} 出现未治理的 pass-only 吞噬"
                            "（显式返回 / 留痕 / 白名单+注释 三档必居其一）"
                        )
                    ctx = "\n".join(lines[max(0, x.lineno - 3): x.lineno + 1])
                    self.assertIn(
                        marker, ctx, f"{rel}:{x.lineno} 白名单条目缺少「{marker}」注释"
                    )
        self.assertLessEqual(total, 4, "pass-only 吞噬总数不得超过白名单基线 4")
