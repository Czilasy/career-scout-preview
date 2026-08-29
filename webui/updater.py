"""应用内更新器（检查 / 下载 / 校验 / 替换脚本生成）。

流程（quitAndInstall 模式，对齐 Electron autoUpdater 四阶段）：

1. ``check_for_update``：先查国内自建更新镜像（固定 IP 的静态
   manifest.json，直连可达、无 API 配额），镜像不可达或内容非法时
   回退 GitHub Releases API（latest）。两边都与当前版本比较；更新
   检查缓存已关闭，启动和手动检查都会拿到最新发布。``fetcher`` 参数
   用于测试注入 GitHub 响应，显式提供时跳过镜像路径（纯 GitHub 行为）。
   ``force``/``state_dir`` 参数保留兼容，不再影响行为。
2. ``UpdateDownloader.download_async``：后台线程流式下载对应平台资产
   （exe/dmg）到 ``~/.career-scout/downloads/``，进度可查。
3. ``verify_downloaded``：SHA256 校验（Release/镜像必须附 ``.sha256``
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
import json
import logging
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
# 国内自建更新镜像（固定 IP 静态分发）。HTTP 明文是该源的已知取舍：
# 安装包传输完整性由强制 SHA256 校验兜底（manifest 与安装包同源），
# 写权限锁在服务器 SSH 密钥上，公开可读是设计预期。
MIRROR_HOST = "49.232.60.135"
MIRROR_BASE_URL = f"http://{MIRROR_HOST}"
MIRROR_MANIFEST_URL = f"{MIRROR_BASE_URL}/manifest.json"
_MIRROR_PLATFORM_KEYS = {"windows": "win", "macos": "mac"}
DEFAULT_STATE_DIR = Path(
    os.environ.get("BOSS_WEBUI_STATE_DIR")
    or os.path.expanduser("~/.career-scout")
)
DOWNLOAD_TIMEOUT = 10
_CHUNK_SIZE = 256 * 1024
# 下载 URL 仅信任 GitHub 官方域与自建镜像源，防重定向到任意地址
_ALLOWED_DOWNLOAD_HOSTS = (
    "github.com",
    "objects.githubusercontent.com",
    "github-releases.githubusercontent.com",
)


# ---------------------------------------------------------------------------
# 版本比较
# ---------------------------------------------------------------------------
_BAT_EXIT_ONE = "  exit 1"


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
    reason: str = ""  # 失败原因或缺少对应资产时的原因码
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
# 检查更新（不落盘缓存，每次实时请求：先镜像，不可达再 GitHub）
# ---------------------------------------------------------------------------
def _check_mirror(
    current_version: str, update_platform: str, *, checked_at: float = 0.0,
) -> UpdateInfo | None:
    """查自建镜像 manifest 并构建 UpdateInfo；不可达/内容非法返回 None。

    manifest 形状（服务器 /root/update_manifest.py 生成）::

        {"latest": "1.8.1", "released": "...",
         "files": {"win": {"name", "sha256", "size"}, "mac": {...}}}
    """
    try:
        resp = requests.get(MIRROR_MANIFEST_URL, timeout=DOWNLOAD_TIMEOUT)
        payload = resp.json()
        latest = str(payload["latest"]).strip()
        files = payload["files"]
    except Exception:
        return None
    # 版本号格式非法的 manifest 视为不可信，回退 GitHub，绝不据此判"已是最新"
    if not re.fullmatch(r"v?\d+(?:\.\d+){1,2}", latest):
        return None
    info = UpdateInfo(
        current=current_version,
        latest=latest.lstrip("vV"),
        release_url=f"https://github.com/{GITHUB_REPO}/releases/tag/v{latest.lstrip('vV')}",
        checked_at=checked_at,
    )
    info.has_update = is_newer(latest, current_version)
    if not info.has_update:
        return info
    entry = files.get(_MIRROR_PLATFORM_KEYS.get(update_platform, "")) \
        if isinstance(files, dict) else None
    if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
        info.reason = "no_asset"
        return info
    name = str(entry["name"])
    info.asset_name = name
    info.asset_url = f"{MIRROR_BASE_URL}/{name}"
    info.asset_size = int(entry.get("size") or 0)
    info.sha256_url = f"{MIRROR_BASE_URL}/{name}.sha256"
    return info


def check_for_update(
    current_version: str,
    *,
    force: bool = False,
    state_dir: Path | str | None = None,
    fetcher=None,
) -> UpdateInfo:
    """查最新发布（先自建镜像，不可达再 GitHub）并与当前版本比较。

    - 更新检查缓存已关闭：每次调用都实时请求；``force``/``state_dir``
      参数保留兼容，不再影响行为；
    - ``fetcher`` 用于测试注入 GitHub 响应；显式提供时跳过镜像路径
      （保持纯 GitHub 行为，既有测试确定性不受影响）；
    - 两路都失败 → ``ok=False, reason="check_failed"``（启动检查静默
      降级，手动检查由前端提示）；
    - 当前平台无对应资产 → ``has_update=True`` 但 ``reason="no_asset"``，
      前端引导去 Release 页手动下载。
    """
    del force, state_dir  # 缓存已关闭，保留兼容签名
    update_platform = detect_update_platform()
    if fetcher is None:
        mirror_info = _check_mirror(
            current_version, update_platform, checked_at=time.time(),
        )
        if mirror_info is not None:
            return mirror_info
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
    if _is_mirror_download_url(parsed):
        return True
    from webui.url_safety import is_safe_https_authority

    return is_safe_https_authority(
        parsed, allowed_hosts=_ALLOWED_DOWNLOAD_HOSTS, allow_subdomains=True
    )


def _is_mirror_download_url(parsed) -> bool:
    """镜像源精确放行：仅自建固定 IP、仅 80 端口、无 userinfo 的 http URL。"""
    return (
        parsed.scheme == "http"
        and (parsed.hostname or "").lower() == MIRROR_HOST
        and parsed.port in (None, 80)
        and parsed.username is None
        and parsed.password is None
    )


def fetch_expected_sha256(
    sha256_url: str, expected_name: str = "", timeout: int = DOWNLOAD_TIMEOUT,
) -> str | None:
    """下载 ``.sha256`` 文件并解析出哈希值；失败返回 None。

    兼容两种格式：纯哈希一行 / ``<hash>  <filename>``（sha256sum 输出）。
    带文件名时要求与 ``expected_name`` 一致，防止校验到其它资产。
    """
    if not sha256_url or not _is_allowed_download_url(sha256_url):
        return None
    try:
        resp = requests.get(sha256_url, timeout=timeout)
        resp.raise_for_status()
        text = resp.text.strip()
    except Exception:
        return None
    from pathlib import Path as _Path

    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})(?:\s+\*?(.+))?$", line.strip())
        if not m:
            continue
        listed_name = (m.group(2) or "").strip()
        if expected_name and listed_name:
            if _Path(listed_name).name != expected_name:
                continue
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
            fetch_expected_sha256(info.sha256_url, info.asset_name)
            if info.sha256_url else None
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
            fetch_expected_sha256(info.sha256_url, info.asset_name)
            if info.sha256_url else None
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

def _ps_single_quote(value: Path | str) -> str:
    """PowerShell 单引号字面量：路径内的单引号翻倍，避免注入脚本。"""
    return "'" + str(value).replace("'", "''") + "'"

def _sh_single_quote(value: Path | str) -> str:
    """POSIX shell 单引号字面量：单引号以 '\'' 闭合，避免注入脚本。"""
    return "'" + str(value).replace("'", "'\\''") + "'"


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
            f"$logFile = {_ps_single_quote(base / 'update_apply.log')}",
            "function Log($msg) {",
            "  try { Add-Content -LiteralPath $logFile -Encoding UTF8 -Value (",
            "    '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg) } catch {}",
            "}",
            "Log 'update_apply start'",
            f"$installer = {_ps_single_quote(installer)}",
            f"$target = {_ps_single_quote(target)}",
            f"$newTarget = {_ps_single_quote(new_target)}",
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
            _BAT_EXIT_ONE,
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
            _BAT_EXIT_ONE,
            "}",
            "if (-not (Test-Path -LiteralPath $newTarget)) {",
            "  Log 'install failed: new target missing'",
            _BAT_EXIT_ONE,
            "}",
            "",
            "# 拉起新版本",
            "try { Start-Process -FilePath $newTarget } catch {",
            "  Log \"launch failed: $($_.Exception.Message)\"",
            _BAT_EXIT_ONE,
            "}",
            "Log \"update_apply done: $newTarget\"",
            "exit 0",
        ])
        script.write_text(content, encoding="utf-8-sig")
        return ("powershell", script)

    # macOS：installer 是 .dmg；target 是 CareerScout.app 目录
    script = base / "update_apply.sh"
    content = "\n".join([
        "#!/bin/bash",
        "set -u",
        f"INSTALLER={_sh_single_quote(installer)}",
        f"TARGET={_sh_single_quote(target)}",
        "MOUNT=\"$(mktemp -d \"${TMPDIR:-/tmp}/career-scout-update.XXXXXX\")\"",
        "trap 'rm -rf \"$MOUNT\"' EXIT",
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
        _BAT_EXIT_ONE,
        "fi",
        # 原子替换：旧版先改名备份，新版就位失败则回滚
        'if [ -d "$TARGET" ]; then mv "$TARGET" "$TARGET.old"; fi',
        'if ! cp -R "$MOUNT/CareerScout.app" "$(dirname "$TARGET")/"; then',
        '  [ -d "$TARGET.old" ] && mv "$TARGET.old" "$TARGET"',
        '  hdiutil detach "$MOUNT" >/dev/null 2>&1',
        _BAT_EXIT_ONE,
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
