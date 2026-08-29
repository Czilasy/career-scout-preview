# -*- coding: utf-8 -*-

"""浏览器注册表域（029 B082③ 新增）。

Chromium 系浏览器注册表 + 探测 + 选择持久化 + 手动路径校验 + CDP 内核判定。
消费方：``webui/pipeline_exec_chrome.py``（启动 exe 解析与内核校验）、
``webui/browser_registry_api.py``（设置端点）、``scripts/boss/browser.py``
（进程枚举 exe 名单）、``scripts/boss/constants.py``（探测委托）。

Firefox / Safari 不做（非 Chromium 内核，CDP 协议不兼容；备忘 BACKLOG B083）。
"""

import json
import os
import platform
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# 注册表（v1 冻结 8 家，research D4；key 顺序即 auto 探测优先级）
# ---------------------------------------------------------------------------
#: windows_candidates: (环境变量名, 相对段...)；macos_paths / linux_paths: 绝对路径
BROWSER_REGISTRY = (
    {
        "key": "chrome",
        "name": "Chrome",
        "exe_names": ("chrome.exe",),
        "data_dir_key": "chrome",
        "windows_candidates": (
            ("LOCALAPPDATA", "Google", "Chrome", "Application", "chrome.exe"),
            ("PROGRAMFILES", "Google", "Chrome", "Application", "chrome.exe"),
            ("PROGRAMFILES(X86)", "Google", "Chrome", "Application", "chrome.exe"),
        ),
        "macos_paths": ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",),
        "linux_paths": (
            "/usr/bin/google-chrome", "/usr/bin/chromium-browser",
            "/usr/bin/chromium", "/snap/bin/chromium",
        ),
    },
    {
        "key": "edge",
        "name": "Edge",
        "exe_names": ("msedge.exe",),
        "data_dir_key": "edge",
        "windows_candidates": (
            ("PROGRAMFILES", "Microsoft", "Edge", "Application", "msedge.exe"),
            ("PROGRAMFILES(X86)", "Microsoft", "Edge", "Application", "msedge.exe"),
            ("LOCALAPPDATA", "Microsoft", "Edge", "Application", "msedge.exe"),
        ),
        "macos_paths": ("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",),
        "linux_paths": ("/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable"),
    },
    {
        "key": "brave",
        "name": "Brave",
        "exe_names": ("brave.exe",),
        "data_dir_key": "brave",
        "windows_candidates": (
            ("LOCALAPPDATA", "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            ("PROGRAMFILES", "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
            ("PROGRAMFILES(X86)", "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        ),
        "macos_paths": ("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",),
        "linux_paths": ("/usr/bin/brave-browser", "/usr/bin/brave"),
    },
    {
        "key": "vivaldi",
        "name": "Vivaldi",
        "exe_names": ("vivaldi.exe",),
        "data_dir_key": "vivaldi",
        "windows_candidates": (
            ("LOCALAPPDATA", "Vivaldi", "Application", "vivaldi.exe"),
            ("PROGRAMFILES", "Vivaldi", "Application", "vivaldi.exe"),
            ("PROGRAMFILES(X86)", "Vivaldi", "Application", "vivaldi.exe"),
        ),
        "macos_paths": ("/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",),
        "linux_paths": ("/usr/bin/vivaldi", "/usr/bin/vivaldi-stable"),
    },
    {
        "key": "opera",
        "name": "Opera",
        "exe_names": ("opera.exe",),
        "data_dir_key": "opera",
        "windows_candidates": (
            ("LOCALAPPDATA", "Programs", "Opera", "opera.exe"),
            ("PROGRAMFILES", "Opera", "opera.exe"),
            ("PROGRAMFILES(X86)", "Opera", "opera.exe"),
        ),
        "macos_paths": ("/Applications/Opera.app/Contents/MacOS/Opera",),
        "linux_paths": ("/usr/bin/opera",),
    },
    {
        "key": "se360",
        "name": "360 极速浏览器",
        "exe_names": ("360chrome.exe", "360se.exe"),
        "data_dir_key": "se360",
        "windows_candidates": (
            ("PROGRAMFILES", "360Chrome", "Chrome", "Application", "360chrome.exe"),
            ("PROGRAMFILES(X86)", "360Chrome", "Chrome", "Application", "360chrome.exe"),
            ("LOCALAPPDATA", "360Chrome", "Chrome", "Application", "360chrome.exe"),
            ("PROGRAMFILES", "360se6", "Application", "360se.exe"),
            ("PROGRAMFILES(X86)", "360se6", "Application", "360se.exe"),
            ("LOCALAPPDATA", "360se6", "Application", "360se.exe"),
        ),
        "macos_paths": (),
        "linux_paths": (),
    },
    {
        "key": "qqbrowser",
        "name": "QQ 浏览器",
        "exe_names": ("qqbrowser.exe",),
        "data_dir_key": "qqbrowser",
        "windows_candidates": (
            ("PROGRAMFILES(X86)", "Tencent", "QQBrowser", "QQBrowser.exe"),
            ("PROGRAMFILES", "Tencent", "QQBrowser", "QQBrowser.exe"),
            ("LOCALAPPDATA", "Tencent", "QQBrowser", "QQBrowser.exe"),
        ),
        "macos_paths": (),
        "linux_paths": (),
    },
    {
        "key": "quark",
        "name": "夸克浏览器",
        "exe_names": ("quarkpc.exe",),
        "data_dir_key": "quark",
        "windows_candidates": (
            ("LOCALAPPDATA", "Programs", "Quark", "QuarkPC.exe"),
            ("LOCALAPPDATA", "Quark", "QuarkPC.exe"),
            ("PROGRAMFILES", "Quark", "QuarkPC.exe"),
            ("PROGRAMFILES(X86)", "Quark", "QuarkPC.exe"),
        ),
        "macos_paths": (),
        "linux_paths": (),
    },
)

REGISTRY_KEYS = tuple(entry["key"] for entry in BROWSER_REGISTRY)

# ---------------------------------------------------------------------------
# 选择持久化（注册表域自持 browser_selection.json；
# 不走 advanced_settings.json——其键白名单在 settings 域内，保持边界）
# ---------------------------------------------------------------------------
SELECTION_FILENAME = "browser_selection.json"


def _selection_path(path=None):
    if path is not None:
        return Path(path)
    base = Path(
        os.environ.get("CAREER_SCOUT_STATE_DIR")
        or os.path.expanduser("~/.career-scout")
    )
    return base / SELECTION_FILENAME


def load_browser_selection(path=None):
    """读取浏览器选择；缺省/损坏 → ``{"mode": "auto"}``（向后兼容现状）。"""
    selection_path = _selection_path(path)
    try:
        data = json.loads(selection_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"mode": "auto"}
    if not isinstance(data, dict):
        return {"mode": "auto"}
    mode = data.get("mode")
    if mode == "registry" and data.get("key") in REGISTRY_KEYS:
        return {"mode": "registry", "key": str(data["key"])}
    if mode == "manual" and str(data.get("manual_path") or "").strip():
        return {"mode": "manual", "manual_path": str(data["manual_path"])}
    return {"mode": "auto"}


def save_browser_selection(mode, key=None, manual_path=None, path=None) -> None:
    """持久化浏览器选择。mode 必须是 auto/registry/manual。"""
    if mode not in ("auto", "registry", "manual"):
        raise ValueError("mode 必须是 auto/registry/manual")
    if mode == "registry" and key not in REGISTRY_KEYS:
        raise ValueError(f"未知浏览器 key: {key}")
    if mode == "manual" and not str(manual_path or "").strip():
        raise ValueError("手动模式必须提供 manual_path")
    payload = {"mode": mode}
    if mode == "registry":
        payload["key"] = str(key)
    if mode == "manual":
        payload["manual_path"] = str(manual_path)
    selection_path = _selection_path(path)
    selection_path.parent.mkdir(parents=True, exist_ok=True)
    selection_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 探测与解析
# ---------------------------------------------------------------------------
def _detect_entry_path(entry, env=None, exists=None):
    """单条注册表记录的路径探测：命中返回路径，否则 None。

    ``env`` / ``exists`` 为 None 时在调用时取 ``os.environ`` / ``os.path.exists``
    （晚绑定：测试对 os 模块属性的 mock 补丁必须能生效，不能在定义期捕获）。"""
    if env is None:
        env = os.environ
    if exists is None:
        exists = os.path.exists
    system = platform.system()
    if system == "Windows":
        for env_name, *parts in entry["windows_candidates"]:
            base = env.get(env_name)
            if not base:
                continue
            candidate = os.path.join(base, *parts)
            if exists(candidate):
                return candidate
        return None
    paths = entry["macos_paths"] if system == "Darwin" else entry["linux_paths"]
    for candidate in paths:
        if exists(candidate):
            return candidate
    return None


def detect_browsers(env=None, exists=None):
    """按注册表顺序探测，返回全量清单::

        [{"key", "name", "installed", "path"}, ...]

    未安装的条目也返回（``installed=False, path=None``），供设置界面展示。
    """
    results = []
    for entry in BROWSER_REGISTRY:
        found = _detect_entry_path(entry, env=env, exists=exists)
        results.append(
            {
                "key": entry["key"],
                "name": entry["name"],
                "installed": found is not None,
                "path": found,
            }
        )
    return results


def resolve_executable(selection_loader=None, detect_fn=None, exists=None):
    """按当前选择解析抓取用浏览器可执行文件路径。

    返回 ``(path | None, reason | None)``：

    - auto → 按注册表顺序取第一个已安装的浏览器（chrome/edge 优先，
      与现状一致；其余注册表条目次之）；
    - registry → 指定 key 的探测路径；未安装 → ``(None, 原因)``；
    - manual → 手动路径（存在性校验）；缺失 → ``(None, 原因)``。
    """
    loader = selection_loader or load_browser_selection
    if exists is None:
        exists = os.path.exists
    selection = loader()
    mode = selection.get("mode") or "auto"

    if mode == "manual":
        manual_path = os.path.abspath(os.path.expanduser(
            str(selection.get("manual_path") or "")))
        if manual_path and exists(manual_path):
            return manual_path, None
        return None, "手动指定的浏览器路径不存在或已失效，请重新指定"

    if mode == "registry":
        key = str(selection.get("key") or "")
        results = (detect_fn or detect_browsers)()
        for item in results:
            if item["key"] == key:
                if item["installed"]:
                    return item["path"], None
                return None, f"所选浏览器 {item['name']} 未安装或路径失效，请在设置中重新选择"
        return None, f"未知浏览器 key: {key}"

    # auto：注册表顺序第一个已安装的浏览器（chrome/edge 在前，兼容现状）
    for item in (detect_fn or detect_browsers)():
        if item["installed"]:
            return item["path"], None
    return None, "未找到可用的 Chromium 浏览器：请安装 Chrome，或在设置 → 浏览器与账号中选择/手动指定路径"


# ---------------------------------------------------------------------------
# 手动路径校验（保存前 `--version` 探活）与 CDP 内核判定
# ---------------------------------------------------------------------------
def is_chromium_version_output(text):
    """``--version`` 输出判定：非空且非 Firefox 家族 → Chromium 系。"""
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return "firefox" not in lowered


def validate_manual_path(path, runner=None, exists=None, timeout=10):
    """手动指定路径校验：可执行 + ``--version`` 探活 + 内核家族判定。

    返回 ``(ok, info)``；``info`` 含 ``error``（失败类别）、``message``
    （用户可读）、``version``（成功时版本串）。``runner(cmd, timeout)``
    可注入替身，返回 ``(returncode, stdout)`` 或抛异常。
    """
    message = {
        "path_validation_failed": "路径校验失败：该文件无法作为浏览器执行（未返回版本信息）",
        "kernel_incompatible": "内核不兼容：该浏览器不是 Chromium 内核，无法用于抓取",
    }

    def _fail(error, detail=""):
        info = {"ok": False, "error": error, "message": message[error]}
        if detail:
            info["message"] = f"{info['message']}（{detail}）"
        return False, info

    if exists is None:
        exists = os.path.exists
    raw = str(path or "").strip()
    if not raw:
        return _fail("path_validation_failed", "路径为空")
    expanded = os.path.abspath(os.path.expanduser(raw))
    if not exists(expanded):
        return _fail("path_validation_failed", "文件不存在")

    run = runner or _run_version_command
    try:
        returncode, stdout = run([expanded, "--version"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return _fail("path_validation_failed", "执行超时")
    except OSError as exc:
        return _fail("path_validation_failed", str(exc))

    version_text = str(stdout or "").strip().splitlines()
    version_text = version_text[0].strip() if version_text else ""
    if returncode != 0 or not version_text:
        return _fail("path_validation_failed", f"退出码 {returncode}")
    if not is_chromium_version_output(version_text):
        return _fail("kernel_incompatible", version_text)
    return True, {"ok": True, "version": version_text}


def _run_version_command(cmd, timeout=10):
    """``--version`` 探活默认实现（Windows 抑制控制台窗口）。"""
    kwargs = {}
    if platform.system() == "Windows":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, **kwargs
    )
    return completed.returncode, completed.stdout or ""


def is_chromium_cdp_browser(browser_field):
    """CDP ``/json/version`` 的 Browser 字段判定是否 Chromium 内核。

    Chromium 系（含 Edge 的 ``Edg/``、各国产壳的 ``Chrome/``）均放行；
    Firefox 等非 Chromium 字段拒绝。
    """
    lowered = str(browser_field or "").strip().lower()
    if not lowered:
        return False
    return "chrome" in lowered or "chromium" in lowered or "edg" in lowered


def fetch_cdp_browser_field(port, timeout=3, urlopen=None):
    """读取 CDP ``/json/version`` 的 Browser 字段；失败返回 None（不阻断）。"""
    import urllib.request

    getter = urlopen or urllib.request.urlopen
    try:
        with getter(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return str((data or {}).get("Browser") or "") or None
    except Exception:
        return None


def selection_data_dir_key():
    """当前选择对应的浏览器命名空间键；解析失败返回 None（恒等映射）。

    manual 模式返回 ``"manual"``；auto 按注册表顺序取第一个已安装条目；
    registry 模式取该条目 ``data_dir_key``。供数据目录派生（research D6）。
    """
    try:
        selection = load_browser_selection()
        mode = selection.get("mode")
        if mode == "manual":
            return "manual"
        if mode == "registry":
            entry = registry_entry(selection.get("key"))
            return entry["data_dir_key"] if entry else None
        for item in detect_browsers():
            if item["installed"]:
                entry = registry_entry(item["key"])
                return entry["data_dir_key"] if entry else None
    except Exception:
        return None
    return None


def all_registry_exe_names():
    """注册表全部可执行文件名（小写集合），供进程枚举过滤器使用。"""
    names = set()
    for entry in BROWSER_REGISTRY:
        for exe in entry["exe_names"]:
            names.add(str(exe).lower())
    return names


def registry_entry(key):
    """按 key 取注册表条目；未知 key 返回 None。"""
    for entry in BROWSER_REGISTRY:
        if entry["key"] == str(key or ""):
            return entry
    return None
