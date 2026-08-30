"""调试浏览器生命周期：就绪检查与关闭（021 B7 自 pipeline_exec.py 搬运）。

ensure_chrome_ready / close_debug_chrome 经 webui.pipeline_exec 门面在调用时
动态取用，保持 patch 面不变。
"""

from __future__ import annotations

import os
import time

from webui.pipeline_exec_accounts import (
    _cdp_data_dir,
    effective_data_dir,
    load_browser_accounts,
)
from webui.logging_setup import get_logger

_logger = get_logger(__name__)

from scripts import boss_cdp_raw as boss
from scripts.boss.browser_registry import (
    fetch_cdp_browser_field,
    is_chromium_cdp_browser,
    resolve_executable,
    selection_data_dir_key,
)




# ---------------------------------------------------------------------------
# 内核校验（029 FR-013：所选浏览器必须是 Chromium 内核）
# ---------------------------------------------------------------------------
def _kernel_check_error(cdp_port: int) -> str | None:
    """CDP 内核判定；不兼容返回用户可读错误，取不到字段放行（不阻断）。"""
    field = fetch_cdp_browser_field(cdp_port)
    if field is not None and not is_chromium_cdp_browser(field):
        return (
            f"所选浏览器内核不兼容（调试端点报告：{field}）。"
            "抓取仅支持 Chromium 内核浏览器，请在 设置 → 浏览器与账号 中重新选择。"
        )
    return None




# ---------------------------------------------------------------------------
# Auto-launch the debug Chrome (self-contained execution)
# ---------------------------------------------------------------------------

def ensure_chrome_ready(cdp_port: int | None = None, *,
                        minimize_after_launch: bool = False) -> tuple[bool, str]:
    """Ensure the dedicated debug Chrome is running; launch it if not.

    Returns ``(True, "")`` when CDP is reachable (already running or just
    launched).  Returns ``(False, msg)`` when the browser fails to come up,
    where ``msg`` carries the cause (early exit / stderr tail / timeout) so
    the caller can surface it to the user instead of a generic "not ready".

    This makes execution self-contained: confirming the params auto-opens the
    browser in front of the user instead of surfacing a raw "CDP unavailable"
    infrastructure error.  Login is checked separately afterwards.

    ``minimize_after_launch``：仅当本函数实际启动了 Chrome 时，通过 CDP
    把窗口最小化到任务栏（不切 headless，避免平台风控）。已运行的 Chrome
    不动，避免打断用户正在进行的登录/人工操作；登录空间打开浏览器的调用
    方不要开启此参数。
    """
    port = cdp_port or boss.DEFAULT_CDP_PORT
    if boss.is_cdp_ready(port):
        cdp_data_dir = _cdp_data_dir()
        kernel_error = _kernel_check_error(port)
        if kernel_error:
            return False, kernel_error
        if boss.cdp_port_uses_profile(port, cdp_data_dir):
            return True, ""
        data_dir_key = selection_data_dir_key()
        known_profiles = {
            boss.normalize_profile_path(effective_data_dir(
                str(info["profile_dir"]), data_dir_key))
            for info in load_browser_accounts().values()
        }
        try:
            from webui.platforms import derive_zhilian_profile_dir, get_platform
            zhilian_port = int(get_platform("zhilian").default_cdp_port)
        except Exception:
            zhilian_port = 9223
        if port == zhilian_port:
            known_profiles.update(
                boss.normalize_profile_path(effective_data_dir(
                    derive_zhilian_profile_dir(str(info["profile_dir"])),
                    data_dir_key))
                for info in load_browser_accounts().values()
                if str(info.get("profile_dir") or "").strip()
            )
        port_profiles = [
            boss.normalize_profile_path(path)
            for path in boss.chrome_user_data_dirs_for_cdp_port(port)
            if path
        ]
        if not any(profile in known_profiles for profile in port_profiles):
            return False, "CDP 端口被非 scraper 账号的 Chrome 占用，为避免误关未自动切换"
        try:
            boss.close_cdp_chrome(port, cdp_data_dir, profile_checker=lambda *_: True)
        except Exception as exc:
            return False, f"切换账号时关闭旧 Chrome 失败：{type(exc).__name__}"
    # Not running: prepare the isolated profile, stop stale processes, launch.
    profile = boss.prepare_cdp_profile(data_dir=_cdp_data_dir())
    cdp_data_dir = profile["path"]
    try:
        boss.stop_cdp_chrome(cdp_data_dir)
    except Exception:
        _logger.debug("CDP Chrome 停止失败（可能已自行退出）", exc_info=True)

    # 029：启动 exe 按设置中的浏览器选择解析（auto/registry/manual）
    exe_path, exe_reason = resolve_executable()
    if exe_path is None:
        return False, exe_reason or "未找到可用的 Chromium 浏览器"
    cmd = [
        exe_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={cdp_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-allow-origins=*",
    ] + list(boss.CDP_LAUNCH_ARGS)
    proc = boss.launch_chrome(cmd)
    launched = True
    # 轮询 CDP，同时检查 Chrome 进程是否还活着
    # 死等 90 秒会让用户莫名其妙，Chrome 早退时立即返回失败原因
    deadline = time.time() + 90
    attempt = 0
    # Windows handoff 机制：当已有相同 user-data-dir 的 Chrome 实例在跑时，
    # 新启动的 chrome.exe 主进程会把命令行转发给已运行实例并立即退出
    # （exit code 通常是 0 或 21），但子进程仍在运行并会监听调试端口。
    # 此时 Popen.poll() 立即返回非 None，但 is_cdp_ready 不久后会变 True。
    # 所以主进程退出后不能立即认为失败，要继续等 CDP 就绪一段时间。
    parent_exited_at = None
    PARENT_EXIT_GRACE = 10  # 主进程退出后给 CDP 10s 宽限期
    while time.time() < deadline:
        if boss.is_cdp_ready(port):
            # 内核校验：非 Chromium 内核（如 Firefox/魔改壳）立即报错，
            # 不做重试等待（换内核不会自愈，避免无反馈等待）
            kernel_error = _kernel_check_error(port)
            if kernel_error:
                return False, kernel_error
            if launched and minimize_after_launch:
                # 最小化是锦上添花，失败不阻断任务流程
                try:
                    boss.minimize_chrome_window(port)
                except Exception:
                    _logger.debug("窗口最小化失败（锦上添花步骤，忽略）", exc_info=True)

            return True, ""
        try:
            rc = proc.poll()
        except Exception:
            rc = None
        if rc is not None:
            # Chrome 主进程已退出
            if parent_exited_at is None:
                parent_exited_at = time.time()
            # 主进程退出超过宽限期，CDP 还没就绪，才认为真的失败
            if time.time() - parent_exited_at > PARENT_EXIT_GRACE:
                attempt += 1
                if attempt <= 3:
                    # 重试前清理可能残留的 Chrome 子进程
                    # （否则新 Chrome 又会 handoff 给旧子进程，无限循环）
                    try:
                        boss.stop_cdp_chrome(cdp_data_dir)
                    except Exception:
                        _logger.debug("CDP Chrome 停止失败（可能已自行退出）", exc_info=True)

                    time.sleep(2)
                    proc = boss.launch_chrome(cmd)
                    parent_exited_at = None
                    continue
                # 重试 3 次都失败，返回错误
                tail = _read_chrome_stderr_tail(cdp_data_dir)
                if tail:
                    return False, f"调试浏览器启动后立即退出（exit code={rc}，已重试 {attempt-1} 次）。stderr 末尾：\n{tail}"
                return False, f"调试浏览器启动后立即退出（exit code={rc}，已重试 {attempt-1} 次），无 stderr 输出。"
        time.sleep(1)
    return False, "等待 CDP 就绪超时（90s）。Chrome 进程仍在运行但未开放调试端口。"




def _read_chrome_stderr_tail(cdp_data_dir: str, max_chars: int = 800) -> str:
    """读取 chrome_stderr.log 的末尾内容，用于诊断启动失败。"""
    log_path = os.path.join(cdp_data_dir, "chrome_stderr.log")
    try:
        with open(log_path, "rb") as f:
            data = f.read()
        if not data:
            return ""
        text = data.decode("utf-8", errors="replace")
        if len(text) > max_chars:
            text = "..." + text[-max_chars:]
        return text.strip()
    except Exception:
        return ""




def close_debug_chrome(cdp_port: int | None = None) -> bool:
    """Close the dedicated debug Chrome (best-effort).

    Uses ``boss.close_cdp_chrome``, which first verifies the port really is
    serving the scraper's isolated profile before closing — so the user's
    regular browser is never touched.  Called after a successful run so the
    automation browser doesn't linger in the taskbar.  A close failure is
    swallowed: it must never break an otherwise successful run.
    """
    port = cdp_port or boss.DEFAULT_CDP_PORT
    try:
        profile = boss.prepare_cdp_profile(data_dir=_cdp_data_dir())
        return bool(boss.close_cdp_chrome(port, profile["path"]))
    except Exception:
        return False
