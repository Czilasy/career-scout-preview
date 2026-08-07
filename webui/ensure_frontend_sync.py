"""Ensure webui/dist matches the backend and frontend source that will be served.

start.bat calls this before launching the WebUI. The frontend build embeds
the backend build hash, and Flask rejects writes when the served dist was
built against a different backend snapshot. If either the backend files or
the frontend source changes, this helper rebuilds the frontend automatically.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
SCRAPER = PROJECT_ROOT / "scripts" / "boss_cdp_raw.py"
STATE_FILE = HERE / "dist" / "build-state.json"


def _backend_files() -> list[Path]:
    files = [*HERE.glob("*.py"), SCRAPER]
    return sorted(files, key=lambda path: path.relative_to(PROJECT_ROOT).as_posix())


def _frontend_files() -> list[Path]:
    files: list[Path] = []
    src_dir = HERE / "src"
    if src_dir.is_dir():
        files.extend(path for path in src_dir.rglob("*") if path.is_file())
    for extra in ("index.html", "vite.config.ts"):
        path = HERE / extra
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda path: path.relative_to(HERE).as_posix())


def _digest(files: list[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def current_backend_hash() -> str:
    return _digest(_backend_files(), PROJECT_ROOT)


def current_frontend_hash() -> str:
    return _digest(_frontend_files(), HERE)


def _built_state() -> dict[str, str] | None:
    if not STATE_FILE.is_file():
        return None
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    return {str(key): str(item) for key, item in value.items()}


def _dist_has_hash(value: str) -> bool:
    assets_dir = HERE / "dist" / "assets"
    if not assets_dir.is_dir():
        return False
    for path in assets_dir.glob("*.js"):
        try:
            if value in path.read_text(encoding="utf-8", errors="ignore"):
                return True
        except OSError:
            continue
    return False


def _run_build() -> None:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise RuntimeError("未找到 npm，请先安装 Node.js")
    if os.name == "nt":
        command = ["cmd", "/c", npm, "run", "build"]
    else:
        command = [npm, "run", "build"]
    result = subprocess.run(command, cwd=HERE, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"npm run build 失败，退出码 {result.returncode}")


def main() -> int:
    current_backend = current_backend_hash()
    current_frontend = current_frontend_hash()
    built = _built_state()
    if (
        built
        and built.get("backend") == current_backend
        and built.get("frontend") == current_frontend
        and _dist_has_hash(current_backend)
    ):
        print("前端已同步，无需重新构建")
        return 0

    check_only = "--check" in sys.argv[1:]
    if check_only:
        print("webui/dist 与源码不同步，请先运行 npm run build 并提交", file=sys.stderr)
        return 1

    print("检测到代码变化，正在自动构建前端...")
    try:
        _run_build()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    after = _built_state()
    if (
        not after
        or after.get("backend") != current_backend
        or after.get("frontend") != current_frontend
        or not _dist_has_hash(current_backend)
    ):
        print(
            f"构建完成但版本仍不一致：期望后端 {current_backend} / "
            f"前端 {current_frontend}，实际 {after}",
            file=sys.stderr,
        )
        return 1

    print("前端构建完成，版本已同步")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
