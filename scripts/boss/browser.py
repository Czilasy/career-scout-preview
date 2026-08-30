# -*- coding: utf-8 -*-

"""Chrome 检测、进程管理与 CDP 生命周期（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

import json
import ntpath
import os
import platform
import re
import shutil
import signal
import subprocess
import time
from urllib.request import urlopen
from scripts.boss.cdp_session import CDPSession
from scripts.boss.constants import BROWSER_NOT_FOUND_HINT, CHROME_EXE, DEFAULT_CDP_DATA_DIR, DEFAULT_CDP_PORT, DEFAULT_CHROME_PATH, DEFAULT_LOGIN_TIMEOUT, EDGE_EXE
from scripts.boss.browser_registry import all_registry_exe_names
import sys as _sys

from webui.logging_setup import get_logger

_logger = get_logger(__name__)



_REGISTRY_COMMAND_TOKENS: frozenset | None = None


def _registry_command_tokens():
    """注册表全部浏览器命令行标识（exe 全名 + 去掉 .exe 的词干，小写）。

    不可变集合，模块级缓存（029 审查修复：进程枚举高频调用不再重建）。
    """
    global _REGISTRY_COMMAND_TOKENS
    if _REGISTRY_COMMAND_TOKENS is None:
        tokens = set()
        for exe in all_registry_exe_names():
            tokens.add(exe)
            if exe.endswith(".exe"):
                tokens.add(exe[:-4])
        _REGISTRY_COMMAND_TOKENS = frozenset(tokens)
    return _REGISTRY_COMMAND_TOKENS

# CDP Chrome 防膨胀启动参数：
# - 限制磁盘/媒体缓存上限，避免抓取缓存无限增长；
# - 禁用 Optimization Guide 本地 AI 模型下载（实测 OptGuideOnDeviceModel 单个账号可膨胀到 4GB+）。
CDP_LAUNCH_ARGS = [
    "--disk-cache-size=104857600",
    "--media-cache-size=52428800",
    "--disable-features=OptimizationGuideModelDownloading,OptimizationHints",
]

def _facade():
    return _sys.modules.get("scripts.boss_cdp_raw")

def prepare_cdp_profile(copy_login_state=False, reset=False, data_dir=None):
    """Prepare an isolated persistent Chrome profile for CDP."""
    if copy_login_state:
        raise ValueError("copy_login_state_deprecated")
    cdp_data_dir = os.path.abspath(os.path.expanduser(
        str(data_dir or _facade().DEFAULT_CDP_DATA_DIR)
    ))
    cdp_default = os.path.join(cdp_data_dir, "Default")

    if reset and os.path.exists(cdp_data_dir):
        shutil.rmtree(cdp_data_dir)

    os.makedirs(cdp_default, exist_ok=True)

    return {
        "path": cdp_data_dir,
        "copied": 0,
        "reset": reset,
        "copy_login_state": False,
    }


def is_cdp_ready(cdp_port):
    # 用标准库 urllib 而不是模块级 requests —— requests 默认是 None，
    # 只有 _facade().require_runtime_dependencies 被调用后才会 import。
    # ensure_chrome_ready 在 preflight 之前调用 _facade().is_cdp_ready，此时 requests
    # 可能尚未初始化，用 requests 会导致永远返回 False（90s 超时）。
    try:
        resp = urlopen(f"http://127.0.0.1:{cdp_port}/json/version", timeout=2)
        return resp.status == 200
    except Exception:
        return False


def is_chrome_command(command):
    lower = (command or "").lower()
    tokens = _registry_command_tokens() | {
        "google chrome",
        "google-chrome",
        "chromium",
        CHROME_EXE,
        "microsoft edge",
        "msedge",
        EDGE_EXE,
    }
    return any(token in lower for token in tokens)


def normalize_profile_path(path):
    clean = (path or "").strip("\"'")
    if platform.system() == "Windows":
        return ntpath.normcase(ntpath.normpath(clean))
    return os.path.realpath(os.path.expanduser(clean))


def extract_user_data_dir(command):
    match = re.search(r"--user-data-dir=(\"[^\"]+\"|'[^']+'|\S+)", command or "")
    if not match:
        return None
    return match.group(1).strip("\"'")


def iter_chrome_process_commands():
    """Return (pid, command line) tuples for Chrome-like browser processes.

    029：进程名过滤器按浏览器注册表全部 exe 名单生成（不再硬编码
    chrome.exe/msedge.exe），Brave/Vivaldi/360/QQ 等同样纳入进程管理面。
    """
    if platform.system() == "Windows":
        name_filter = " or ".join(
            f"name = '{exe}'" for exe in sorted(all_registry_exe_names())
        )
        ps_script = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            f"Get-CimInstance Win32_Process -Filter \"{name_filter}\" | "
            "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
        )
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                stdin=subprocess.DEVNULL,
                capture_output=True, encoding="utf-8", errors="replace", timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            return []
        if not r.stdout.strip():
            return []
        try:
            data = json.loads(r.stdout)
        except ValueError:
            return []
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []
        processes = []
        for item in data:
            command = item.get("CommandLine") or ""
            if not _facade().is_chrome_command(command):
                continue
            try:
                processes.append((int(item.get("ProcessId")), command))
            except (TypeError, ValueError):
                continue
        return processes

    try:
        r = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
    except Exception:
        return []

    processes = []
    for line in r.stdout.splitlines():
        if not _facade().is_chrome_command(line):
            continue
        try:
            pid_text, command = line.strip().split(None, 1)
            pid = int(pid_text)
        except ValueError:
            continue
        processes.append((pid, command))
    return processes


def chrome_pids_for_user_data_dir(user_data_dir):
    """Return Chrome PIDs using the given user-data-dir."""
    pids = []
    real_dir = _facade().normalize_profile_path(user_data_dir)
    for pid, command in _facade().iter_chrome_process_commands():
        if "--user-data-dir=" not in command:
            continue
        path = _facade().extract_user_data_dir(command)
        if path and _facade().normalize_profile_path(path) == real_dir:
            pids.append(pid)
    return pids


def chrome_user_data_dirs_for_cdp_port(cdp_port):
    """Return user-data-dir paths for Chrome processes using the given CDP port."""
    dirs = []
    port_arg = f"--remote-debugging-port={cdp_port}"
    for _pid, command in _facade().iter_chrome_process_commands():
        if port_arg not in command:
            continue
        path = _facade().extract_user_data_dir(command)
        if path:
            dirs.append(path)
    return dirs


def cdp_port_uses_profile(cdp_port, cdp_data_dir):
    expected = _facade().normalize_profile_path(cdp_data_dir)
    return any(_facade().normalize_profile_path(path) == expected for path in _facade().chrome_user_data_dirs_for_cdp_port(cdp_port))


def terminate_process(pid, force=False):
    if platform.system() == "Windows":
        cmd = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            cmd.append("/F")
        subprocess.run(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)


def stop_cdp_chrome(cdp_data_dir):
    """Stop only Chrome processes that use the scraper's isolated profile."""
    pids = _facade().chrome_pids_for_user_data_dir(cdp_data_dir)
    if not pids:
        return 0

    for pid in pids:
        try:
            _facade().terminate_process(pid, force=False)
        except ProcessLookupError:
            pass
    for _ in range(10):
        time.sleep(0.5)
        if not _facade().chrome_pids_for_user_data_dir(cdp_data_dir):
            return len(pids)

    for pid in _facade().chrome_pids_for_user_data_dir(cdp_data_dir):
        try:
            _facade().terminate_process(pid, force=True)
        except ProcessLookupError:
            pass
    time.sleep(0.5)
    return len(pids)


def close_cdp_chrome(cdp_port=DEFAULT_CDP_PORT, cdp_data_dir=DEFAULT_CDP_DATA_DIR,
                     profile_checker=None, session_factory=CDPSession,
                     process_stopper=None, ready_checker=None, sleeper=None):
    """Close only a Chrome CDP instance using the expected dedicated profile."""
    checker = profile_checker or _facade().cdp_port_uses_profile
    if not checker(cdp_port, cdp_data_dir):
        return False

    is_ready = ready_checker or _facade().is_cdp_ready
    stop_processes = process_stopper or _facade().stop_cdp_chrome
    pause = sleeper or time.sleep
    session = None
    try:
        session = session_factory(cdp_port)
        try:
            session.send("Browser.close", timeout=5)
        except Exception:
            # Chrome may close the WebSocket before acknowledging Browser.close.
            _logger.debug("Browser.close 请求失败（可能已退出）", exc_info=True)

    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                _logger.debug("会话关闭失败（best-effort 忽略）", exc_info=True)


    for _ in range(10):
        if not is_ready(cdp_port):
            return True
        pause(0.2)

    # Fallback remains restricted to the same dedicated user-data-dir.
    stop_processes(cdp_data_dir)
    return not is_ready(cdp_port)


def _cdp_page_target_id(cdp_port):
    """Return the first page target id of the CDP browser, or None."""
    try:
        resp = urlopen(f"http://127.0.0.1:{cdp_port}/json", timeout=2)
        targets = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None
    for target in targets or []:
        if target.get("type") == "page" and target.get("id"):
            return target["id"]
    return None


def minimize_chrome_window(cdp_port=DEFAULT_CDP_PORT, *,
                           session_factory=CDPSession,
                           target_id_provider=None):
    """Best-effort: minimize the scraper Chrome window via CDP Browser domain.

    最小化不改变浏览器指纹（不切 headless，避免触发平台风控），只把窗口
    收到任务栏，不打扰用户。任何失败都静默返回 False，不影响抓取主流程。
    """
    provider = target_id_provider or _cdp_page_target_id
    try:
        target_id = provider(cdp_port)
        if not target_id:
            return False
        session = session_factory(cdp_port)
        try:
            resp = session.send(
                "Browser.getWindowForTarget", {"targetId": target_id}, timeout=10,
            )
            window_id = (resp.get("result") or {}).get("windowId")
            if not window_id:
                return False
            session.send("Browser.setWindowBounds", {
                "windowId": window_id,
                "bounds": {"windowState": "minimized"},
            }, timeout=10)
            return True
        finally:
            try:
                session.close()
            except Exception:
                _logger.debug("会话关闭失败（best-effort 忽略）", exc_info=True)

    except Exception:
        return False


def wait_for_cdp(cdp_port, timeout=30):
    print("等待 CDP 可用", end="")
    for _ in range(timeout):
        time.sleep(1)
        print(".", end="", flush=True)
        if _facade().is_cdp_ready(cdp_port):
            print(f"\n✅ CDP 已就绪 (端口 {cdp_port})")
            return True
    print(f"\n❌ 等待超时 ({timeout}s)，CDP 未就绪")
    print(f"   请手动检查 Chrome 是否启动，端口 {cdp_port} 是否开放")
    return False


def launch_chrome(cmd):
    """Launch Chrome detached, with stderr captured to a log file for diagnostics.

    Returns the ``subprocess.Popen`` handle so callers can check ``poll()``
    to detect early exit instead of waiting the full CDP timeout.
    """
    # 把 Chrome 的 stderr 写到日志文件，启动失败时能直接看到原因
    # （Chrome 是 GUI 程序，但启动失败信息会进 stderr）
    log_dir = os.path.dirname(_facade().DEFAULT_CDP_DATA_DIR)
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        _logger.debug("调试日志目录创建失败（沿用现有目录）", exc_info=True)

    log_path = os.path.join(_facade().DEFAULT_CDP_DATA_DIR, "chrome_stderr.log")
    try:
        stderr_fh = open(log_path, "ab", buffering=0)
    except Exception:
        stderr_fh = subprocess.DEVNULL
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": stderr_fh,
    }
    if platform.system() == "Windows":
        # 注意：不要加 DETACHED_PROCESS —— 实测在 Windows 上会导致 Chrome 启动后
        # 立即退出（exit code=21），9222 端口从未开放。只保留 CREATE_NEW_PROCESS_GROUP
        # 让 Chrome 在独立进程组里运行即可，Flask 退出也不会立即带走它。
        # CREATE_NO_WINDOW：桌面壳是 windowed 程序（无控制台），子进程不抑制
        # 控制台时系统会弹一个 CMD 窗口闪一下；Chrome 是 GUI 程序且
        # stderr 已重定向到日志文件，不需要控制台（与 DETACHED_PROCESS
        # 不同，它不影响 Chrome 进程存活）。
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
        if creationflags:
            kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(cmd, **kwargs)
    finally:
        if stderr_fh != subprocess.DEVNULL:
            stderr_fh.close()


def run_setup_chrome(cdp_port=DEFAULT_CDP_PORT, copy_login_state=False,
                     reset_profile=False, wait_login=True,
                     login_timeout=DEFAULT_LOGIN_TIMEOUT):
    """自动配置并启动 Chrome CDP 模式"""
    if copy_login_state:
        print("❌ --copy-login-state 已停用：不会复制 Chrome 数据库。")
        print("   请改用 --import-boss-session + --confirm-session-import。")
        return 1
    if not _facade().require_runtime_dependencies("requests"):
        return 1

    if not DEFAULT_CHROME_PATH:
        print(f"❌ 未找到 Chrome/Edge，{BROWSER_NOT_FOUND_HINT}")
        return 1

    print("=" * 50)
    print("  设置 Chrome CDP 调试模式")
    print("=" * 50)
    print()

    profile = _facade().prepare_cdp_profile(copy_login_state=copy_login_state, reset=reset_profile)
    cdp_data_dir = profile["path"]
    print(f"✅ 使用独立 Chrome profile: {cdp_data_dir}")
    if reset_profile:
        print("   已按 --reset-chrome-profile 重建 profile")
    print("   默认、首次启动、重复启动都不复制主 Chrome Cookie；首次使用请在此专用 Chrome 中登录 zhipin.com")

    if _facade().is_cdp_ready(cdp_port):
        if _facade().cdp_port_uses_profile(cdp_port, cdp_data_dir):
            print(f"\n✅ CDP 已就绪 (端口 {cdp_port})")
            if wait_login:
                return 0 if _facade().wait_for_login(cdp_port, timeout=login_timeout) else 1
            return 0
        print(f"\n❌ 端口 {cdp_port} 已被其他 Chrome CDP profile 占用")
        print("   请关闭旧 CDP Chrome，或改用 --cdp-port 指定其他端口")
        return 1

    stopped = _facade().stop_cdp_chrome(cdp_data_dir)
    if stopped:
        print(f"\n已关闭 {stopped} 个旧的 BOSS CDP Chrome 进程")

    print(f"\n启动 Chrome (CDP 端口: {cdp_port})...")
    cmd = [
        DEFAULT_CHROME_PATH,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={cdp_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-allow-origins=*",
    ] + CDP_LAUNCH_ARGS
    _facade().launch_chrome(cmd)

    if not _facade().wait_for_cdp(cdp_port):
        return 1

    print()
    print("Chrome 已启动。请在这个专用浏览器中登录 zhipin.com。")
    if wait_login:
        print()
        if not _facade().wait_for_login(cdp_port, timeout=login_timeout):
            return 1
    print()
    print("示例:")
    print("  uv run python3 scripts/boss_cdp_raw.py --keyword \"AI Agent\" --city 上海 --pages 3")
    print("  uv run python3 scripts/boss_cdp_raw.py --check")
    print("  uv run python3 scripts/boss_cdp_raw.py --stop-chrome   # 抓完关闭专用 Chrome")
    print()
    return 0


def run_stop_chrome():
    """关闭 BOSS 专用 CDP Chrome（按隔离 user-data-dir 精准匹配，不碰主 Chrome）。"""
    if not _facade().require_runtime_dependencies("requests"):
        return 1

    print("=" * 50)
    print("  关闭 BOSS 专用 CDP Chrome")
    print("=" * 50)
    print()

    # 只定位 scraper 专用 profile 目录，不复制、不重置
    profile = _facade().prepare_cdp_profile(copy_login_state=False, reset=False)
    cdp_data_dir = profile["path"]

    stopped = _facade().stop_cdp_chrome(cdp_data_dir)
    if stopped:
        print(f"\n✅ 已关闭 {stopped} 个 BOSS 专用 Chrome 进程 (profile: {cdp_data_dir})")
    else:
        print(f"\nℹ️  没有找到运行中的 BOSS 专用 Chrome 进程 (profile: {cdp_data_dir})")
    print()
    print("提示：仅关闭 scraper 隔离 profile 的 Chrome，不影响你的主 Chrome。")
    print()
    return 0
