"""版本号递增工具：一次改齐三处并生成 CHANGELOG 条目。

用法::

    python scripts/bump_version.py patch [-m "修复收藏抽屉取消收藏报错"]
    python scripts/bump_version.py minor [-m "新增平台筛选"]
    python scripts/bump_version.py major [-m "2.0 大版本"]

规则（见 CONTRIBUTING.md「版本管理」）：

- patch（2.7.0 -> 2.7.1）：小修小补（bug 修复、文案、样式）
- minor（2.7.x -> 2.8.0）：新功能（向后兼容）
- major（2.x.y -> 3.0.0）：重构 / 超大功能 / 纪念性版本

同步更新的版本位置：

1. pyproject.toml  ``version = "x.y.z"``（产品版本权威源，app.py 从这里读）
2. webui/package.json  ``"version": "x.y.z"``
3. scripts/boss_cdp_raw.py  ``__version__ = "x.y.z"``
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

VERSION_FILES: list[tuple[Path, re.Pattern[str], str]] = [
    (ROOT / "pyproject.toml", re.compile(r'^(version\s*=\s*["\'])[^"\']+(["\'])', re.MULTILINE), r"\g<1>{new}\g<2>"),
    (ROOT / "webui" / "package.json", re.compile(r'^(\s*"version"\s*:\s*")[^"]+(")', re.MULTILINE), r"\g<1>{new}\g<2>"),
    (ROOT / "scripts" / "boss_cdp_raw.py", re.compile(r'^(__version__\s*=\s*["\'])[^"\']+(["\'])', re.MULTILINE), r"\g<1>{new}\g<2>"),
]

CHANGELOG = ROOT / "CHANGELOG.md"


def read_current_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not match:
        raise SystemExit("pyproject.toml 中未找到 version 字段")
    return match.group(1)


def bump(current: str, part: str) -> str:
    parts = current.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise SystemExit(f"无法解析版本号: {current!r}")
    major, minor, patch = (int(p) for p in parts)
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "major":
        return f"{major + 1}.0.0"
    raise SystemExit(f"未知递增类型: {part}")


def write_versions(next_version: str) -> None:
    for path, pattern, template in VERSION_FILES:
        if not path.exists():
            raise SystemExit(f"版本文件不存在: {path}")
        text = path.read_text(encoding="utf-8")
        updated, count = pattern.subn(template.format(new=next_version), text, count=1)
        if count != 1:
            raise SystemExit(f"{path} 中未找到版本字段，未做任何修改")
        path.write_text(updated, encoding="utf-8")
        print(f"已更新 {path.relative_to(ROOT)} -> {next_version}")


def prepend_changelog(next_version: str, message: str) -> None:
    if not CHANGELOG.exists():
        raise SystemExit(f"CHANGELOG 不存在: {CHANGELOG}")
    text = CHANGELOG.read_text(encoding="utf-8")
    entry = (
        f"## [{next_version}] - {date.today().isoformat()}\n\n"
        f"### 变更\n\n"
        f"- {message.strip()}\n\n"
    )
    # 插到第一个 `## [` 之前（保留文件头说明）。
    match = re.search(r"^## \[", text, re.MULTILINE)
    if match is None:
        raise SystemExit("CHANGELOG 中未找到版本条目位置")
    updated = text[: match.start()] + entry + text[match.start():]
    CHANGELOG.write_text(updated, encoding="utf-8")
    print(f"已写入 CHANGELOG 条目 [{next_version}]")


def main() -> int:
    parser = argparse.ArgumentParser(description="递增 Career Scout 版本号并生成 CHANGELOG 条目")
    parser.add_argument("part", choices=("patch", "minor", "major"), help="递增类型")
    parser.add_argument("-m", "--message", default="常规发布", help="CHANGELOG 条目描述（一行）")
    args = parser.parse_args()

    current = read_current_version()
    next_version = bump(current, args.part)
    print(f"{current} -> {next_version}")
    write_versions(next_version)
    prepend_changelog(next_version, args.message)
    print("完成。请确认 git diff 后按 Conventional Commits 提交。")
    return 0


if __name__ == "__main__":
    sys.exit(main())