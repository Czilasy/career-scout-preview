# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller macOS 打包清单（.app 应用包）。

产物形态：onedir + windowed 的 ``CareerScout.app``（macOS 不支持
onefile GUI 应用，.app 本身就是目录结构），后续由 ``build_dmg.sh``
打成 ``.dmg``。

与 Windows 版（career_scout.spec）的差异：
- 无 win32 版本元数据（VSVersionInfo 仅 Windows 有效）；
- 多一个 ``BUNDLE`` 段，生成带 ``CFBundleIdentifier`` 的 .app；
- ``hiddenimports`` 换成 macOS 后端：pywebview cocoa 后端（依赖
  pyobjc 的 Cocoa/WebKit）、keyring macOS/chainer 后端。

entry 脚本 ``packaging/desktop.py`` 已跨平台：
- 单实例：macOS 走 ``~/.career-scout/CareerScout-SingleInstance.lock``
  flock 文件锁；
- 错误提示：macOS 走 osascript 原生对话框；
- 资源定位与 Windows 一致（frozen 时资源根 = ``sys._MEIPASS``）。

架构策略：PyPI 上部分依赖（如 markupsafe）只提供单架构 wheel，
无法构建真正的 universal2 包（PyInstaller 会报
IncompatibleBinaryArchError）。因此默认跟随构建机架构
（GitHub macos-latest 为 arm64，覆盖所有 Apple Silicon Mac）；
如需 Intel 版，在 Intel Mac 或 x86_64 runner 上重跑同一脚本即可。
"""

import re
from pathlib import Path

# SPECPATH 由 PyInstaller 执行 spec 时注入，指向 spec 文件所在目录（packaging/）
PROJECT_ROOT = Path(SPECPATH).resolve().parent  # type: ignore[name-defined]

block_cipher = None


# ---------------------------------------------------------------------------
# 从 pyproject.toml 读版本（与 desktop.py.read_version 同策略）
# ---------------------------------------------------------------------------
def _read_version():
    pyproject = PROJECT_ROOT / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return "0.0.0"
    m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return m.group(1) if m else "0.0.0"


VERSION = _read_version()

# 跟随构建机架构（macos-latest = arm64）；显式写死可改 "arm64"/"x86_64"
TARGET_ARCH = None

a = Analysis(
    # entry 脚本路径用绝对路径避免 CWD/SPECPATH 歧义
    [str(PROJECT_ROOT / "packaging" / "desktop.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        # datas 源路径按 CWD 解析；用绝对路径消除 CWD/SPECPATH 歧义
        (str(PROJECT_ROOT / "webui" / "dist"), "webui/dist"),
        (str(PROJECT_ROOT / "data" / "city_codes.json"), "data"),
        (str(PROJECT_ROOT / "data" / "zhilian_city_codes.json"), "data"),
        # pyproject.toml 收集到 _MEIPASS 根，desktop.py.read_version 靠
        # _PROJECT_ROOT/pyproject.toml 定位 → 窗口标题版本正确
        (str(PROJECT_ROOT / "pyproject.toml"), "."),
    ],
    hiddenimports=[
        "flask",
        "keyring",
        "keyring.backends.macOS",
        "keyring.backends.chainer",
        "keyring.backends.fail",
        "webview",
        "webview.platforms.cocoa",
        "objc",
        "AppKit",
        "WebKit",
        "webui.app",
        "webui.desktop_runtime",
        "scripts.boss_cdp_raw",
        "scripts.job_summary",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CareerScout",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=TARGET_ARCH,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="CareerScout",
)
app = BUNDLE(
    coll,
    name="CareerScout.app",
    bundle_identifier="com.czilasy.careerscout",
    info_plist={
        "CFBundleName": "Career Scout",
        "CFBundleDisplayName": "Career Scout",
        "CFBundleVersion": VERSION,
        "CFBundleShortVersionString": VERSION,
        "NSHighResolutionCapable": True,
        # 仅访问本机 127.0.0.1 后端与用户 Chrome 配置文件，不声明网络客户端权限
        "LSMinimumSystemVersion": "11.0",
    },
)
