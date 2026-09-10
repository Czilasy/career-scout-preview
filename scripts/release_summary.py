"""从 CHANGELOG 提取可直接展示给用户的版本摘要。"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from webui.updater import _summarize_release_notes

_ROOT = Path(__file__).resolve().parents[1]
_CHANGELOG = _ROOT / "CHANGELOG.md"
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
# 更新说明每条必须一行说清（口径约 25 字）；这里留少量余量做硬把关，
# 超长说明写细了，发布前必须改短（见 AGENTS.md 更新说明写作规范）。
_MAX_ITEM_CHARS = 30


def _changelog_body(version: str) -> str:
    lines = _CHANGELOG.read_text(encoding="utf-8").splitlines()
    heading = re.compile(rf"^## \[v?{re.escape(version)}\](?:\s|$)")
    start = next((i for i, line in enumerate(lines) if heading.match(line)), None)
    if start is None:
        raise ValueError(f"CHANGELOG 中找不到版本 {version}")
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"^## \[", lines[i])),
        len(lines),
    )
    return "\n".join(lines[start + 1:end])


def _reject_overlong_items(items: list[str]) -> None:
    """拦下写得太细的更新条目：一句话说不清就说明该改短，不是补细节。"""
    overlong = [item for item in items if len(item) > _MAX_ITEM_CHARS]
    if overlong:
        detail = "；".join(f"「{item}」共{len(item)}字" for item in overlong)
        raise ValueError(
            f"更新条目超过 {_MAX_ITEM_CHARS} 字，写得太细，请改短后再发布：{detail}"
        )


def build_release_summary(version: str) -> dict[str, object]:
    """返回镜像清单需要的版本绑定摘要。"""
    version = str(version or "").strip().lstrip("vV")
    if not _VERSION_RE.fullmatch(version):
        raise ValueError(f"版本号格式无效: {version!r}")
    items = _summarize_release_notes(_changelog_body(version), version) or []
    if not 3 <= len(items) <= 8:
        raise ValueError(f"版本 {version} 的有效更新摘要应为 3～8 条，当前 {len(items)} 条")
    _reject_overlong_items(items)
    return {"version": version, "release_items": items}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成镜像更新摘要 JSON")
    parser.add_argument("--version", required=True, help="版本号，例如 1.8.2")
    args = parser.parse_args()
    try:
        summary = build_release_summary(args.version)
    except ValueError as exc:
        parser.error(str(exc))
    # 发布脚本会在 Windows PowerShell 中读取 stdout；使用 ASCII 转义避免
    # 本机控制台编码把中文摘要改写成乱码，JSON 解析后仍会还原原文。
    print(json.dumps(summary, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
