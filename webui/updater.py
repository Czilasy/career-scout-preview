# -*- coding: utf-8 -*-
"""应用内更新器（检查 / 下载 / 校验 / 替换脚本生成）。

流程（quitAndInstall 模式，对齐 Electron autoUpdater 四阶段）：

1. ``check_for_update``：每次实时查 GitHub Releases API（latest），与
   当前版本比较；更新检查缓存已关闭，启动和手动检查都会拿到最新发布。
   ``force``/``state_dir`` 参数保留兼容，不再影响行为。
2. ``UpdateDownloader.download_async``：后台线程流式下载对应平台资产
   （exe/dmg）到 ``~/.career-scout/downloads/``，进度可查。
3. ``verify_downloaded``：SHA256 校验（Release 必须附 ``.sha256``
   资产；缺失或校验失败一律拒绝，绝不静默替换）。
4. ``build_updater_script``：生成平台替换脚本（Windows PowerShell /
   macOS sh），由调用方在主进程退出前 detached 启动；脚本等 PID 死透
   后替换文件并重新拉起新版本（运行中的程序无法替换自己，借"身后脚本"
   完成）。Windows 版会把新版就位为带新版本号的文件名（如
   ``CareerScout-v2.5.0.exe``），不再停留在旧文件名。

源码模式（RUNTIME_MODE=source）没有可替换的安装产物，只提供检查与
浏览器下载引导，apply/restart 端点需拒绝。
"""

from __future__ import annotations

import hashlib
import logging
import json
import os
import platform
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

GITHUB_REPO = "Czilasy/career-scout-preview"
GITHUB_LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
DEFAULT_STATE_DIR = Path(
    os.environ.get("BOSS_WEBUI_STATE_DIR")
    or os.path.expanduser("~/.career-scout")
)
DOWNLOAD_TIMEOUT = 10
_CHUNK_SIZE = 256 * 1024
# 下载 URL 仅信任 GitHub 官方域，防重定向到任意地址
_ALLOWED_DOWNLOAD_HOSTS = (
    "github.com",
    "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
)


# ---------------------------------------------------------------------------
# 版本比较
# ---------------------------------------------------------------------------
def parse_version(text: str) -> tuple[int, ...]:
    """``"v2.4.0"`` → ``(2, 4, 0)``；非法段按 0，取前三段。"""
    cleaned = str(text or "").strip().lstrip("vV")
    parts = re.split(r"[.+\-]", cleaned)
    nums: list[int] = []
    for part in parts[:3]:
        m = re.match(r"^(\d+)", part)
        nums.append(int(m.group(1)) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def is_newer(remote: str, current: str) -> bool:
    return parse_version(remote) > parse_version(current)


# ---------------------------------------------------------------------------
# 资产选择
# ---------------------------------------------------------------------------
def detect_update_platform() -> str:
    """返回 ``"windows"`` / ``"macos"`` / ``"other"``。"""
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Darwin":
        return "macos"
    return "other"


@dataclass
class UpdateAsset:
    name: str
    url: str
    size: int


@dataclass
class UpdateInfo:
    """检查结果的规范化形状（可直接 jsonify）。"""

    ok: bool = True
    current: str = ""
    latest: str = ""
    has_update: bool = False
    release_url: str = ""
    release_notes: str = ""
    asset_name: str = ""
    asset_url: str = ""
    asset_size: int = 0
    sha256_url: str = ""
    reason: str = ""  # ok=False / 无对应资产时的原因码
    checked_at: float = 0.0  # 本次实时检查完成时间

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "current": self.current,
            "latest": self.latest,
            "has_update": self.has_update,
            "release_url": self.release_url,
            "release_notes": self.release_notes,
            "asset_name": self.asset_name,
            "asset_url": self.asset_url,
            "asset_size": self.asset_size,
            "sha256_url": self.sha256_url,
            "reason": self.reason,
            "checked_at": self.checked_at,
        }


def _select_assets(assets: list[dict], update_platform: str) -> tuple[UpdateAsset | None, str]:
    """从 Release 资产里挑当前平台的安装包与对应 ``.sha256`` 文件。

    返回 ``(主资产或 None, sha256 资产 URL)``。
    """
    suffix = ".exe" if update_platform == "windows" else ".dmg"
    main: UpdateAsset | None = None
    sha_url = ""
    for raw in assets:
        name = str(raw.get("name") or "")
        url = str(raw.get("browser_download_url") or "")
        size = int(raw.get("size") or 0)
        if name.endswith(suffix) and not name.endswith(suffix + ".sha256"):
            # 同平台多个取最后一个（Release 里不会重复，防御性兜底）
            main = UpdateAsset(name=name, url=url, size=size)
        elif name.endswith(suffix + ".sha256"):
            sha_url = url
    return main, sha_url


# ---------------------------------------------------------------------------
# 检查更新（不落盘缓存，每次实时请求 GitHub）
# ---------------------------------------------------------------------------
def check_for_update(
    current_version: str,
    *,
    force: bool = False,
    state_dir: Path | str | None = None,
    fetcher=None,
) -> UpdateInfo:
    """查 GitHub latest release 并与当前版本比较。

    - 更新检查缓存已关闭：每次调用都实时请求 GitHub latest release；
      ``force``/``state_dir`` 参数保留兼容，不再影响行为；
    - 网络/API 失败 → ``ok=False, reason="check_failed"``（启动检查静默降级，
      手动检查由前端提示）；
    - 当前平台无对应资产 → ``has_update=True`` 但 ``reason="no_asset"``，
      前端引导去 Release 页手动下载。
    """
    update_platform = detect_update_platform()
    getter = fetcher or (lambda: requests.get(
        GITHUB_LATEST_URL, timeout=DOWNLOAD_TIMEOUT,
        headers={"Accept": "application/vnd.github+json"},
    ))
    try:
        resp = getter()
        payload = resp.json() if hasattr(resp, "json") else json.loads(resp)
        if not isinstance(payload, dict) or "tag_name" not in payload:
            return UpdateInfo(ok=False, current=current_version, reason="check_failed")
    except Exception:
        return UpdateInfo(ok=False, current=current_version, reason="check_failed")

    return _build_info(payload, current_version, update_platform, checked_at=time.time())


def _build_info(
    api: dict, current_version: str, update_platform: str, *,
    checked_at: float = 0.0,
) -> UpdateInfo:
    tag = str(api.get("tag_name") or "")
    info = UpdateInfo(
        current=current_version,
        latest=tag.lstrip("vV"),
        release_url=str(api.get("html_url") or ""),
        release_notes=str(api.get("body") or "")[:4000],
        checked_at=checked_at,
    )
    info.has_update = bool(tag) and is_newer(tag, current_version)
    if not info.has_update:
        return info
    if update_platform == "other":
        info.reason = "no_asset"
        return info
    main, sha_url = _select_assets(list(api.get("assets") or []), update_platform)
    if main is None or not main.url:
        info.reason = "no_asset"
        return info
    info.asset_name = main.name
    info.asset_url = main.url
    info.asset_size = main.size
    info.sha256_url = sha_url
    if not sha_url:
        info.reason = "no_sha256"
    return info


# ---------------------------------------------------------------------------
# 下载（后台线程 + 进度状态）
# ---------------------------------------------------------------------------
@dataclass
class DownloadState:
    status: str = "idle"  # idle | downloading | verifying | ready | failed
    received: int = 0
    total: int = 0
    path: str = ""
    error: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "received": self.received,
            "total": self.total,
            "progress": round(self.received / self.total, 4) if self.total else 0,
            "path": self.path,
            "error": self.error,
        }


def _is_allowed_download_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in _ALLOWED_DOWNLOAD_HOSTS)


def fetch_expected_sha256(sha256_url: str, timeout: int = DOWNLOAD_TIMEOUT) -> str | None:
    """下载 ``.sha256`` 文件并解析出哈希值；失败返回 None。

    兼容两种格式：纯哈希一行 / ``<hash>  <filename>``（sha256sum 输出）。
    """
    if not sha256_url or not _is_allowed_download_url(sha256_url):
        return None
    try:
        resp = requests.get(sha256_url, timeout=timeout)
        resp.raise_for_status()
        text = resp.text.strip()
    except Exception:
        return None
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})", line.strip())
        if m:
            return m.group(1).lower()
    return None


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


class UpdateDownloader:
    """单实例下载管理器（app.config["UPDATER"]）。线程安全。"""

    def __init__(self, state_dir: Path | str | None = None):
        self._state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_info: UpdateInfo | None = None
        self._target: Path | None = None
        self._expected_sha: str | None = None
        self.state = DownloadState()

    def recover_ready(self, info: UpdateInfo | None = None) -> bool:
        """复用磁盘上已下载并通过 SHA256 校验的完整安装包。

        下载状态只存在内存里，应用重启后会回到 idle；这里根据最新
        ``UpdateInfo`` 检查 ``downloads/`` 中是否已有完整包，校验通过后
        直接恢复为 ready，避免再次下载时 Windows 报目标文件已存在。
        """
        if info is None:
            with self._lock:
                info = self._last_info
        if info is None or not info.asset_url or not info.asset_name:
            return False
        with self._lock:
            self._last_info = info
            if self.state.status in ("downloading", "verifying"):
                return False
        target = self.download_dir / info.asset_name
        if not target.is_file():
            return False
        expected = (
            fetch_expected_sha256(info.sha256_url) if info.sha256_url else None
        )
        try:
            digest = compute_sha256(target)
            size = target.stat().st_size
        except OSError:
            return False
        if not expected or digest != expected.lower():
            try:
                target.unlink()
            except OSError:
                pass
            return False
        with self._lock:
            if self.state.status in ("downloading", "verifying"):
                return False
            self._target = target
            self._expected_sha = expected.lower()
            self.state = DownloadState(
                status="ready", received=size, total=size, path=str(target),
            )
        return True

    def status(self) -> dict:
        with self._lock:
            return self.state.to_dict()

    @property
    def download_dir(self) -> Path:
        return self._state_dir / "downloads"

    def start(self, info: UpdateInfo, expected_sha256: str | None = None) -> bool:
        """启动下载；已有下载中/已完成任务时拒绝重复启动。"""
        with self._lock:
            if self.state.status in ("downloading", "verifying", "ready"):
                return False
            if not info.asset_url or not _is_allowed_download_url(info.asset_url):
                self.state = DownloadState(status="failed", error="invalid_download_url")
                return False
            self._last_info = info
            self.state = DownloadState(status="downloading", total=info.asset_size)
            self._expected_sha = expected_sha256
            self._target = self.download_dir / info.asset_name
        self._thread = threading.Thread(
            target=self._run, args=(info,), name="career-scout-updater", daemon=True,
        )
        self._thread.start()
        return True

    def _run(self, info: UpdateInfo) -> None:
        try:
            self.download_dir.mkdir(parents=True, exist_ok=True)
            tmp = self._target.with_suffix(self._target.suffix + ".part")
            with requests.get(info.asset_url, stream=True, timeout=DOWNLOAD_TIMEOUT,
                              allow_redirects=True) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or info.asset_size or 0)
                with self._lock:
                    self.state.total = total
                received = 0
                with tmp.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                        if chunk:
                            f.write(chunk)
                            received += len(chunk)
                            with self._lock:
                                self.state.received = received
            tmp.replace(self._target)
        except Exception as exc:
            logger.exception("应用内更新下载失败：%s", exc)
            with self._lock:
                self.state = DownloadState(status="failed", error="download_failed")
            return

        # SHA256 校验（强制）：无期望值时现取 .sha256；任何一步失败都拒绝
        with self._lock:
            self.state.status = "verifying"
        expected = self._expected_sha or (
            fetch_expected_sha256(info.sha256_url) if info.sha256_url else None
        )
        if not expected:
            with self._lock:
                self.state = DownloadState(
                    status="failed", error="sha256_unavailable", path=str(self._target),
                )
            return
        if compute_sha256(self._target) != expected.lower():
            try:
                self._target.unlink()
            except OSError:
                pass
            with self._lock:
                self.state = DownloadState(status="failed", error="sha256_mismatch")
            return
        with self._lock:
            self.state = DownloadState(
                status="ready", received=self.state.total, total=self.state.total,
                path=str(self._target),
            )


# ---------------------------------------------------------------------------
# 替换脚本（"身后脚本"：等主进程退出后替换并重启）
# ---------------------------------------------------------------------------
def _versioned_new_target(installer: Path, target: Path) -> Path:
    """新版就位路径：目标目录下带新版本号的文件名。

    从 installer 文件名（``CareerScout-v2.5.0.exe``）提取版本号，构造
    ``target 同目录 / CareerScout-v{版本}{后缀}``；解析失败或与 installer
    同路径（同名同目录）时回退 ``target`` 本身（保持覆盖旧文件行为）。
    """
    m = re.search(r"v?\d+(?:\.\d+)+", installer.stem)
    if not m:
        return target
    candidate = target.parent / f"CareerScout-{m.group(0)}{target.suffix}"
    if candidate.resolve() == installer.resolve():
        return target
    return candidate


def build_updater_script(
    *,
    installer_path: Path | str,
    install_target: Path | str,
    pid: int,
    script_dir: Path | str | None = None,
) -> tuple[str, Path]:
    """生成平台替换脚本，返回 ``(执行命令 argv 的首元素描述, 脚本路径)``。

    - Windows：PowerShell（无窗口、无 cmd/find 依赖）。等主进程退出后
      把新版就位为带新版本号的文件名（``CareerScout-v{新版本}.exe``），
      清理旧文件并拉起新版；
    - macOS：sh。挂载 dmg → ``cp -R`` 覆盖 .app → 卸载 → ``open`` 拉起。

    脚本先写临时文件再原子 rename，避免半截脚本。
    """
    installer = Path(installer_path)
    target = Path(install_target)
    base = Path(script_dir) if script_dir else DEFAULT_STATE_DIR
    base.mkdir(parents=True, exist_ok=True)

    if platform.system() == "Windows":
        new_target = _versioned_new_target(installer, target)
        script = base / "update_apply.ps1"
        # utf-8-sig（带 BOM）：Windows PowerShell 5.1 默认按 ANSI 解析无 BOM
        # 脚本，路径含非 ASCII 字符时会乱码导致替换失败
        content = "\n".join([
            "$ErrorActionPreference = 'Stop'",
            f"$logFile = '{base / 'update_apply.log'}'",
            "function Log($msg) {",
            "  try { Add-Content -LiteralPath $logFile -Encoding UTF8 -Value (",
            "    '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg) } catch {}",
            "}",
            "Log 'update_apply start'",
            f"$installer = '{installer}'",
            f"$target = '{target}'",
            f"$newTarget = '{new_target}'",
            f"$waitPid = {pid}",
            "",
            "# 等主进程退出，最多 30 秒；超时强杀，避免无限等待导致更新卡死",
            "# （pywebview 窗口销毁后事件循环可能不返回，主进程 PID 不消失）。",
            "# Get-Process 在进程不存在（或 PID 非法）时会抛错，必须 try/catch",
            "# 兜底，否则 Stop 模式会让脚本直接终止。",
            "$deadline = (Get-Date).AddSeconds(30)",
            "while ($true) {",
            "  $proc = $null",
            "  try { $proc = Get-Process -Id $waitPid -ErrorAction SilentlyContinue } catch { $proc = $null }",
            "  if (-not $proc) { break }",
            "  if ((Get-Date) -ge $deadline) {",
            "    try { Stop-Process -Id $waitPid -Force -ErrorAction Stop } catch {}",
            "    break",
            "  }",
            "  Start-Sleep -Milliseconds 500",
            "}",
            "",
            "# 新版就位：优先落到带新版本号的文件名；失败保留旧版退出",
            "if (-not (Test-Path -LiteralPath $installer)) {",
            "  Log \"installer missing: $installer\"",
            "  exit 1",
            "}",
            "try {",
            "  if ($newTarget -eq $target) {",
            "    Move-Item -LiteralPath $installer -Destination $target -Force -ErrorAction Stop",
            "  } else {",
            "    Move-Item -LiteralPath $installer -Destination $newTarget -Force -ErrorAction Stop",
            "    # 旧文件可能仍被 onefile 父进程短暂占用，重试删除；删不掉不阻塞新版启动",
            "    $oldRemoved = $false",
            "    for ($i = 0; $i -lt 20; $i++) {",
            "      if (-not (Test-Path -LiteralPath $target)) { $oldRemoved = $true; break }",
            "      try { Remove-Item -LiteralPath $target -Force -ErrorAction Stop; $oldRemoved = $true; break } catch { Start-Sleep -Milliseconds 500 }",
            "    }",
            "    if (-not $oldRemoved) { Log \"old target removal skipped: $target\" }",
            "  }",
            "} catch {",
            "  Log \"install failed: $($_.Exception.Message)\"",
            "  exit 1",
            "}",
            "if (-not (Test-Path -LiteralPath $newTarget)) {",
            "  Log 'install failed: new target missing'",
            "  exit 1",
            "}",
            "",
            "# 拉起新版本",
            "try { Start-Process -FilePath $newTarget } catch {",
            "  Log \"launch failed: $($_.Exception.Message)\"",
            "  exit 1",
            "}",
            "Log \"update_apply done: $newTarget\"",
            "exit 0",
        ])
        script.write_text(content, encoding="utf-8-sig")
        return ("powershell", script)

    # macOS：installer 是 .dmg；target 是 CareerScout.app 目录
    script = base / "update_apply.sh"
    mount_point = "/tmp/career-scout-update-mount"
    content = "\n".join([
        "#!/bin/bash",
        "set -u",
        f'INSTALLER="{installer}"',
        f'TARGET="{target}"',
        f'MOUNT="{mount_point}"',
        # 等主进程退出，最多 30 秒；超时强杀，避免无限等待导致更新卡死
        "i=0",
        f"while kill -0 {pid} 2>/dev/null; do",
        "  i=$((i+1))",
        f"  if [ $i -ge 30 ]; then kill -9 {pid} 2>/dev/null; break; fi",
        "  sleep 1",
        "done",
        'rm -rf "$MOUNT"',
        'hdiutil attach "$INSTALLER" -nobrowse -readonly -mountpoint "$MOUNT" >/dev/null 2>&1',
        'if [ ! -d "$MOUNT/CareerScout.app" ]; then',
        '  hdiutil detach "$MOUNT" >/dev/null 2>&1',
        "  exit 1",
        "fi",
        # 原子替换：旧版先改名备份，新版就位失败则回滚
        'if [ -d "$TARGET" ]; then mv "$TARGET" "$TARGET.old"; fi',
        'if ! cp -R "$MOUNT/CareerScout.app" "$(dirname "$TARGET")/"; then',
        '  [ -d "$TARGET.old" ] && mv "$TARGET.old" "$TARGET"',
        '  hdiutil detach "$MOUNT" >/dev/null 2>&1',
        "  exit 1",
        "fi",
        'rm -rf "$TARGET.old"',
        'hdiutil detach "$MOUNT" >/dev/null 2>&1',
        'xattr -dr com.apple.quarantine "$TARGET" >/dev/null 2>&1 || true',
        'open "$TARGET"',
        "exit 0",
    ]) + "\n"
    script.write_text(content, encoding="utf-8")
    try:
        script.chmod(0o755)
    except OSError:
        pass
    return ("bash", script)


def current_install_target() -> Path | None:
    """当前安装产物路径（exe 文件 / .app 目录）；源码模式返回 None。"""
    import sys

    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    if platform.system() == "Darwin":
        # .app 内可执行文件位于 CareerScout.app/Contents/MacOS/CareerScout
        for parent in exe.parents:
            if parent.name.endswith(".app"):
                return parent
        return None
    return exe


def clean_download_dir(state_dir: Path | str | None = None) -> None:
    """启动时清理下载残留：只删 ``.part`` 半成品与超过 30 天的完整包。

    不整目录删除：新实例可能比替换脚本先启动（例如用户手动打开新 exe），
    此时完整安装包被删会让替换脚本 ``Move-Item`` 找不到源而失败。
    """
    base = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
    downloads = base / "downloads"
    if not downloads.is_dir():
        return
    cutoff = time.time() - 30 * 86400
    for path in downloads.iterdir():
        try:
            if path.suffix == ".part" or (
                path.is_file() and path.stat().st_mtime < cutoff
            ):
                path.unlink()
        except OSError:
            pass
