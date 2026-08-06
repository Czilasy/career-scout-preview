"""桌面运行时检测模块（spec003 tasks002）。

集中承载运行时模式判定、WebView2 注册表检测、随机端口选择三件纯逻辑，
供 ``webui/app.py`` 的 ``env_check``、桌面壳 ``packaging/desktop.py`` 与
单测共享调用。本模块刻意保持无副作用、可注入，且**不 import pywebview**
（窗口壳属于另一波次）。

冻结合同：``specs/003-desktop-exe/contracts/runtime-mode.md``。
"""

import socket
import sys

# ---------------------------------------------------------------------------
# winreg 可用性（非 Windows 平台 import 失败时安全退化）
# ---------------------------------------------------------------------------
try:  # pragma: no cover - 平台分支由测试 mock _has_winreg 覆盖
    import winreg

    _has_winreg = True
except ImportError:  # pragma: no cover
    winreg = None
    _has_winreg = False


# 注册表常量镜像（避免在模块顶层依赖 winreg 常量对象，便于非 Windows 加载）
_HKLM = 0x80000002  # HKEY_LOCAL_MACHINE
_HKCU = 0x80000001  # HKEY_CURRENT_USER

# 合同 §3：HKLM 带 WOW6432Node（64 位 Windows 32 位视图），HKCU 不带
_HKLM_SUBKEY = (
    r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
    r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
)
_HKCU_SUBKEY = (
    r"Software\Microsoft\EdgeUpdate\Clients"
    r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
)

# 合法运行时模式白名单（合同 §1 只定义这两种）
_VALID_MODES = ("source", "exe")


# ===========================================================================
# 1. 运行时模式判定
# ===========================================================================
def runtime_mode(config):
    """返回当前运行时模式 ``"source"`` 或 ``"exe"``。

    优先级（合同 §1.1）：
    1. ``config`` 显式包含 ``RUNTIME_MODE`` 键时，以该值为准：仅接受
       ``"source"`` / ``"exe"``；**非法值/非 str 一律回退 ``"source"``
       （最安全默认，避免误激活 EXE 路径），绝不抛异常**；
    2. 未提供 config 或未设置该键时，``getattr(sys, "frozen", False)`` 兜底：
       冻结环境返回 ``"exe"``，否则返回 ``"source"``。

    设计要点：区分「显式传键但值非法」与「完全没传键」——前者回退安全默认
    ``"source"``，后者才走 ``sys.frozen`` 兜底。
    """
    if config and "RUNTIME_MODE" in config:
        mode = config["RUNTIME_MODE"]
        if isinstance(mode, str) and mode in _VALID_MODES:
            return mode
        # 显式传了键但值非法：回退安全默认 source，不走 frozen 兜底
        return "source"
    if getattr(sys, "frozen", False):
        return "exe"
    return "source"


# ===========================================================================
# 2. WebView2 注册表检测
# ===========================================================================
def _default_reg_reader(root, subkey):
    """默认注册表读取器：``winreg.OpenKey`` + ``QueryValueEx("pv")``。

    只读，绝不写入。任何 ``OSError``（键缺失 / 权限不足）返回 ``None``。
    """
    try:
        with winreg.OpenKey(root, subkey) as key:
            value, _ = winreg.QueryValueEx(key, "pv")
            return value
    except OSError:
        return None


def _parse_version(value):
    """把版本字符串解析为 int tuple；无法解析返回 ``None``。"""
    if not isinstance(value, str):
        return None
    parts = value.split(".")
    try:
        return tuple(int(part) for part in parts)
    except (ValueError, TypeError):
        return None


def _is_installed_version(value):
    """版本字符串解析后严格大于 ``0.0.0.0`` 才算已装（合同 §3）。"""
    parsed = _parse_version(value)
    return parsed is not None and parsed > (0, 0, 0, 0)


def check_webview2(reg_reader=None):
    """检测 WebView2 运行时是否安装。

    返回结构::

        {
            "installed": bool,   # 是否已装（版本 > 0.0.0.0）
            "available": bool,   # 当前平台是否可检测（winreg 是否可用 / 是否注入 reader）
            "version": str|None, # 检测到的有效版本字符串，未装时 None
            "detail": str,       # 面向用户/调试的文案
        }

    - ``reg_reader`` 为可调用对象时优先使用（协议：``reader(root:int, subkey:str) -> str|None``），
      便于单测注入替身；reader 抛异常一律视为该位置缺失，不向外传播。
    - 未注入 reader 且当前平台无 ``winreg``（非 Windows）时返回
      ``available=False``，**不抛异常**（合同 §3 实现约束）。
    - 只读注册表，不写入、不修改。
    - HKLM / HKCU 任一位置存在且版本有效即视为已装；两处缺失或版本无效视为未装。
    """
    if reg_reader is None:
        if not _has_winreg:
            return {
                "installed": False,
                "available": False,
                "version": None,
                "detail": "当前平台不支持 WebView2 检测",
            }
        reg_reader = _default_reg_reader

    # 依次探测 HKLM（64 位 32 位视图）与 HKCU，任一有效即已装
    for root, subkey in ((_HKLM, _HKLM_SUBKEY), (_HKCU, _HKCU_SUBKEY)):
        try:
            value = reg_reader(root, subkey)
        except OSError:
            # reader 系统错误（OpenKey 失败等）视为该位置缺失
            value = None
        if _is_installed_version(value):
            return {
                "installed": True,
                "available": True,
                "version": value,
                "detail": f"已安装 WebView2 运行时（版本 {value}）",
            }

    return {
        "installed": False,
        "available": True,
        "version": None,
        "detail": (
            "未检测到 WebView2 运行时，请安装 Microsoft Edge WebView2"
            "（下载地址见环境检查提示）"
        ),
    }


# ===========================================================================
# 3. 随机端口选择
# ===========================================================================
def pick_free_port():
    """返回一个当前可绑定的空闲端口（int）。

    实现：``socket.bind(("127.0.0.1", 0))`` 让操作系统分配临时端口，取出后
    立即关闭 socket 返回该端口号。绑定回环地址，确保 EXE 后端只监听本机。

    不保证两次调用结果必不同（端口可被其他进程抢占），但保证返回的端口
    在调用瞬间可绑定到回环地址。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
