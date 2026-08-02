#!/usr/bin/env python3
"""Generate the public release tree for career-scout.

The public tree is copied from the tracked git index, so local-only files
(state, logs, generated fixtures) never leak. Internal directories and
sensitive tests are excluded explicitly, and the private AGENTS.md is replaced
with tools/public_AGENTS.md.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = ROOT / ".release" / "career-scout"

EXCLUDED_DIRS = frozenset({
    ".agents", ".chrome-profiles", ".devlog", ".release", ".specify", ".trae",
    ".trash", ".uploads", ".venv", ".webui-state", ".workbuddy", ".worktrees",
    "docs", "logs", "specs", "tuning",
})

EXCLUDED_FILES = frozenset({
    "tests/run_isolated_webui.py",
    "tests/sc002_24h_monitor.py",
    "tests/test_historical_recovery_realdb.py",
})


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=True
    )
    return [path for path in result.stdout.split("\0") if path]


def is_excluded(relative: str) -> bool:
    path = PurePosixPath(relative)
    parts = [part.lower() for part in path.parts]
    if any(part in EXCLUDED_DIRS for part in parts):
        return True
    if relative.lower() in EXCLUDED_FILES:
        return True
    if path.name.lower() == "agents.md":
        return True
    return False


def copy_file(root: Path, dest: Path, relative: str) -> None:
    source = root / relative
    target = dest / relative
    if not source.exists():
        raise FileNotFoundError(f"tracked file missing from working tree: {relative}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest", type=Path, default=DEFAULT_DEST,
        help="destination directory (default: .release/career-scout)",
    )
    parser.add_argument(
        "--force", action="store_true", help="replace an existing destination"
    )
    args = parser.parse_args()

    dest = args.dest.resolve()
    if dest == ROOT or not dest.is_relative_to(ROOT):
        parser.error("--dest must be inside the project workspace")
    if dest.exists():
        if not args.force:
            parser.error(f"{dest} already exists; use --force to replace it")
        shutil.rmtree(dest)

    copied: list[str] = []
    skipped: list[str] = []
    for relative in tracked_files(ROOT):
        if is_excluded(relative):
            skipped.append(relative)
            continue
        copy_file(ROOT, dest, relative)
        copied.append(relative)

    public_agent = ROOT / "tools" / "public_AGENTS.md"
    if not public_agent.exists():
        print("missing tools/public_AGENTS.md", file=sys.stderr)
        return 1
    target_agent = dest / "AGENTS.md"
    target_agent.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(public_agent, target_agent)
    copied.append("AGENTS.md (public template)")

    print(f"Copied {len(copied)} files, skipped {len(skipped)} files -> {dest}")
    for relative in sorted(skipped):
        print(f"  skipped: {relative}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
