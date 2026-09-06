#!/usr/bin/env python3
"""合并安装包与同版本更新摘要到镜像 manifest.json。

用法：
update_manifest.py <version> <platform> <filename> <sha256> <size> <summary_base64>
"""

from __future__ import annotations

import base64
import copy
import datetime
import json
import os
import re
import sys
import tempfile
from pathlib import Path

_MANIFEST_PATH = Path(
    os.environ.get("CAREER_SCOUT_MANIFEST_PATH", "/var/www/career-scout/manifest.json")
)
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _decode_summary(summary_base64: str, version: str) -> list[str]:
    if not summary_base64:
        raise ValueError("发布新版本必须提供结构化更新摘要")
    try:
        payload = json.loads(base64.b64decode(summary_base64).decode("utf-8"))
    except Exception as exc:
        raise ValueError("更新摘要不是有效 JSON") from exc
    summary_version = str(payload.get("version") or "").strip().lstrip("vV")
    if summary_version != version:
        raise ValueError("更新摘要版本与安装包版本不一致")
    items = payload.get("release_items")
    if not isinstance(items, list):
        raise ValueError("更新摘要缺少条目列表")
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not 3 <= len(cleaned) <= 8:
        raise ValueError("更新摘要条数必须在 3～8 条之间")
    return cleaned


def _asset_version_matches(name: str, version: str) -> bool:
    match = re.fullmatch(
        r"CareerScout-v?(\d+\.\d+\.\d+)\.(?:exe|dmg)",
        str(name or "").strip(),
        re.IGNORECASE,
    )
    return bool(match and match.group(1) == version)


def merge_manifest(
    manifest: dict,
    version: str,
    platform: str,
    name: str,
    sha256: str,
    size: int | str,
    summary_base64: str,
) -> dict:
    """返回合并后的 manifest，不直接写文件，便于本地测试。"""
    version = str(version or "").strip().lstrip("vV")
    if not _VERSION_RE.fullmatch(version):
        raise ValueError("版本号格式无效")
    if platform not in {"win", "mac"}:
        raise ValueError("平台无效")
    if not _asset_version_matches(name, version):
        raise ValueError("安装包文件名版本与发布版本不一致")
    items = _decode_summary(summary_base64, version)

    result = copy.deepcopy(manifest)
    result["latest"] = version
    result["released"] = datetime.date.today().isoformat()
    result.setdefault("files", {})[platform] = {
        "name": name,
        "sha256": str(sha256),
        "size": int(size),
    }
    result["release_notes_version"] = version
    result["release_items"] = items
    result["release_notes"] = "\n".join(f"• {item}" for item in items)
    return result


def _write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        # manifest.json 由 Nginx 直接对外提供，临时文件替换前固定为公开可读。
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 6:
        raise SystemExit(
            "用法：update_manifest.py <version> <platform> <filename> "
            "<sha256> <size> <summary_base64>"
        )
    version, platform, name, sha256, size, summary_base64 = args
    manifest = {}
    if _MANIFEST_PATH.exists():
        manifest = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    result = merge_manifest(
        manifest, version, platform, name, sha256, size, summary_base64,
    )
    _write_manifest(_MANIFEST_PATH, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
