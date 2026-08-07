"""Repo hygiene guard for the public release."""

from __future__ import annotations

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
        """项目根目录不得残留测试中转文件（fulltest.log 等）。

        这些文件被 ``*.log`` 忽略规则覆盖，git 层面不可见（check-ignore
        与 git status 都查不到），必须直接文件系统检查。
        """
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
            "tests/test_historical_recovery_realdb.py",
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
        # 本机真实环境特征：克隆者请替换成自己的本地路径片段。
        # 拆开拼接是为了避免本文件自身的字面量被扫描命中。
        local_env = [
            "C:\\Users\\" + "22879",
            "D:\\" + "项目",
        ]
        patterns = [
            # 真实凭据形态：OpenAI key、PEM 私钥头、AWS Access Key
            re.compile(r"sk-[A-Za-z0-9]{20,}"),
            re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
            re.compile(r"AKIA[0-9A-Z]{16}"),
            # Windows 用户目录，排除 demo/example 等测试占位用户名
            re.compile(
                r"[A-Za-z]:[\\/]Users[\\/]"
                r"(?!(?:demo|example|fake|mock|test|sample|public|guest)[\\/-])"
                r"[A-Za-z0-9_.-]+"
            ),
        ] + [re.compile(re.escape(p), re.IGNORECASE) for p in local_env]
        issues = []
        for rel in paths:
            if rel.startswith("webui/dist/"):
                continue
            if pathlib.PurePosixPath(rel).suffix.lower() not in text_suffixes:
                continue
            text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
            for rx in patterns:
                m = rx.search(text)
                if m:
                    issues.append(f"{rel}: 命中 {m.group(0)!r}")
        self.assertEqual(issues, [], "已跟踪文本文件不得包含本地路径或凭据")
