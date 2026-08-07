"""版本号递增/校验工具。

用法::

    python scripts/bump_version.py patch [-m "修复收藏抽屉取消收藏报错"]
    python scripts/bump_version.py minor [-m "新增平台筛选"]
    python scripts/bump_version.py major [-m "2.0 大版本"]
    python scripts/bump_version.py --check

规则（见根目录 AGENTS.md「版本与发布」）：

- patch（2.7.0 -> 2.7.1）：小修小补（bug 修复、文案、样式）
- minor（2.7.x -> 2.8.0）：新功能（向后兼容）
- major（2.x.y -> 3.0.0）：重构 / 超大功能 / 纪念性版本

同步更新的版本位置：

1. pyproject.toml  ``version = "x.y.z"``（产品版本权威源）
2. webui/package.json
3. webui/package-lock.json（前两处：根包与 packages[""]）
4. uv.lock（career-scout 包块）
5. scripts/boss_cdp_raw.py  ``__version__``
6. tests/test_desktop_shell.py  版本断言
7. README.md  标题版本号
8. CHANGELOG.md  新增条目（默认「修复」分组，如需「增加/优化」发布前改分组标签）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
PACKAGE_LOCK = ROOT / "webui" / "package-lock.json"
UV_LOCK = ROOT / "uv.lock"

VERSION_PATTERNS: list[tuple[Path, re.Pattern[str]]] = [
    (ROOT / "pyproject.toml", re.compile(r'^version\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)),
    (ROOT / "webui" / "package.json", re.compile(r'^\s*"version"\s*:\s*"([^"]+)"', re.MULTILINE)),
    (ROOT / "scripts" / "boss_cdp_raw.py", re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)),
    (ROOT / "tests" / "test_desktop_shell.py", re.compile(r'^\s*self\.assertEqual\(version,\s*"([^"]+)"\)', re.MULTILINE)),
    (ROOT / "README.md", re.compile(r'^# Career Scout v([\d.]+)(?= ·)', re.MULTILINE)),
]

UV_LOCK_PATTERN = re.compile(r'^name = "career-scout"\nversion = "([^"]+)"', re.MULTILINE)
PACKAGE_LOCK_PATTERN = re.compile(r'^(\s*"version"\s*:\s*")[^"]+(")', re.MULTILINE)


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


def _package_lock_versions() -> list[str]:
    data = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))
    root = data.get("version")
    package = (data.get("packages") or {}).get("", {}).get("version")
    return [v for v in (root, package) if v]


def _uv_lock_version() -> str | None:
    match = UV_LOCK_PATTERN.search(UV_LOCK.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def check_versions(expected: str) -> int:
    problems: list[str] = []
    for path, pattern in VERSION_PATTERNS:
        if not path.exists():
            problems.append(f"{path.relative_to(ROOT)} 缺失")
            continue
        match = pattern.search(path.read_text(encoding="utf-8"))
        if not match or match.group(1) != expected:
            problems.append(f"{path.relative_to(ROOT)} 应为 {expected}")
    if PACKAGE_LOCK.exists():
        for value in _package_lock_versions():
            if value != expected:
                problems.append(f"webui/package-lock.json 应为 {expected}（当前 {value}）")
    if UV_LOCK.exists():
        value = _uv_lock_version()
        if value != expected:
            problems.append(f"uv.lock career-scout 应为 {expected}（当前 {value}）")
    if problems:
        print("版本不一致：", file=sys.stderr)
        for item in problems:
            print(f"- {item}", file=sys.stderr)
        return 1
    print(f"版本一致：{expected}")
    return 0


def _replace_single(path: Path, pattern: re.Pattern[str], next_version: str) -> None:
    if not path.exists():
        raise SystemExit(f"版本文件不存在: {path}")
    text = path.read_text(encoding="utf-8")
    updated, count = pattern.subn(
        lambda m: m.group(0).replace(m.group(1), next_version, 1), text, count=1
    )
    if count != 1:
        raise SystemExit(f"{path.relative_to(ROOT)} 中未找到版本字段，未做任何修改")
    path.write_text(updated, encoding="utf-8")
    print(f"已更新 {path.relative_to(ROOT)} -> {next_version}")


def _update_package_lock(next_version: str) -> None:
    text = PACKAGE_LOCK.read_text(encoding="utf-8")
    updated, count = PACKAGE_LOCK_PATTERN.subn(
        rf"\g<1>{next_version}\g<2>", text, count=2
    )
    if count != 2:
        raise SystemExit("webui/package-lock.json 未找到两处根版本字段")
    PACKAGE_LOCK.write_text(updated, encoding="utf-8")
    print(f"已更新 webui/package-lock.json -> {next_version}")


def _update_uv_lock(next_version: str) -> None:
    text = UV_LOCK.read_text(encoding="utf-8")
    updated, count = UV_LOCK_PATTERN.subn(
        lambda m: f'name = "career-scout"\nversion = "{next_version}"', text, count=1
    )
    if count != 1:
        raise SystemExit("uv.lock 未找到 career-scout 包块")
    UV_LOCK.write_text(updated, encoding="utf-8")
    print(f"已更新 uv.lock career-scout -> {next_version}")


def prepend_changelog(next_version: str, message: str) -> None:
    if not CHANGELOG.exists():
        raise SystemExit(f"CHANGELOG 不存在: {CHANGELOG}")
    text = CHANGELOG.read_text(encoding="utf-8")
    entry = (
        f"## [{next_version}] - {date.today().isoformat()}\n\n"
        f"修复：\n"
        f"- {message.strip()}\n\n"
    )
    match = re.search(r"^## \[", text, re.MULTILINE)
    if match is None:
        raise SystemExit("CHANGELOG 中未找到版本条目位置")
    updated = text[: match.start()] + entry + text[match.start():]
    CHANGELOG.write_text(updated, encoding="utf-8")
    print(f"已写入 CHANGELOG 条目 [{next_version}]")


def main() -> int:
    parser = argparse.ArgumentParser(description="递增或校验 Career Scout 版本号")
    parser.add_argument("part", nargs="?", choices=("patch", "minor", "major"), help="递增类型")
    parser.add_argument("-m", "--message", default="常规发布", help="CHANGELOG 条目描述（一行）")
    parser.add_argument("--check", action="store_true", help="只校验版本一致性，不修改文件")
    args = parser.parse_args()

    current = read_current_version()
    if args.check:
        return check_versions(current)
    if args.part is None:
        parser.error("需要 part（patch/minor/major）或 --check")

    next_version = bump(current, args.part)
    print(f"{current} -> {next_version}")
    for path, pattern in VERSION_PATTERNS:
        _replace_single(path, pattern, next_version)
    _update_package_lock(next_version)
    _update_uv_lock(next_version)
    prepend_changelog(next_version, args.message)
    print("完成。请确认 git diff 后按 Conventional Commits 提交。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
