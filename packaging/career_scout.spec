# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包清单（spec003 tasks006 T042）。

产物形态：onefile + windowed（无控制台），EXE 名 ``CareerScout``。
版本从 ``pyproject.toml`` 读取注入到 EXE 文件版本元数据（右键详情可见）。

entry ``packaging/desktop.py``（Task 005 产出）：
- 运行时 ``desktop.py`` 用 ``_PROJECT_ROOT = _HERE.parent`` 定位
  ``pyproject.toml`` 读版本、定位 ``webui/dist``、``data/``；
  onefile 下 ``__file__`` 指向 ``sys._MEIPASS`` 临时目录，
  资源靠下方 ``datas`` 收集进包。

``Analysis.pathex`` 含项目根，确保 ``scripts`` / ``webui`` 顶层包可导入。

``hiddenimports`` 按 tasks006 策略"缺什么补什么"先收敛基础项：
flask、keyring（Windows 后端）、pywebview（Windows WinForms 后端）、
本项目 ``webui.app`` / ``webui.desktop_runtime`` / ``scripts.boss_cdp_raw``
/ ``scripts.job_summary``；实际构建报缺再逐项补。
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


def _version_tuple(v):
    """版本字符串 → 四元组 ``(major, minor, patch, 0)``，非法段补 0。"""
    nums = []
    for part in v.split(".")[:3]:
        try:
            nums.append(int(part))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums) + (0,)


# ---------------------------------------------------------------------------
# EXE 版本元数据（Windows 文件属性 → 右键详情可见）
# 仅 Windows 平台构造；非 Windows parse 时 import 失败则降级为 None
# ---------------------------------------------------------------------------
_version_info = None
try:
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    _ver = _version_tuple(VERSION)
    _version_info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=_ver,
            prodvers=_ver,
            mask=0x3F,
            flag_bits=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "Czilasy"),
                            StringStruct("FileDescription", "Career Scout"),
                            StringStruct("FileVersion", VERSION),
                            StringStruct("InternalName", "CareerScout"),
                            StringStruct("LegalCopyright", "Apache License 2.0"),
                            StringStruct("OriginalFilename", "CareerScout.exe"),
                            StringStruct("ProductName", "Career Scout"),
                            StringStruct("ProductVersion", VERSION),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )
except Exception:
    _version_info = None


a = Analysis(
    ["packaging/desktop.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        ("webui/dist", "webui/dist"),
        ("data/city_codes.json", "data"),
        ("data/zhilian_city_codes.json", "data"),
    ],
    hiddenimports=[
        "flask",
        "keyring",
        "keyring.backends.Windows",
        "webview",
        "webview.platforms.winforms",
        "webview.platforms.winforms.Forms",
        "webui.app",
        "webui.desktop_runtime",
        "scripts.boss_cdp_raw",
        "scripts.job_summary",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="CareerScout",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=_version_info,
)
