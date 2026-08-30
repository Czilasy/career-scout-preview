# -*- coding: utf-8 -*-
"""智联 CDP 原语与平台常量（031 B6 自 scripts/zhilian_cdp_raw.py 物理搬运）。

本模块是 ``scripts/zhilian/`` 四域的公共底层，承载两类内容：

- 平台常量：CDP 默认端口、host allowlist、探测 URL、页内提取 JS、风险
  marker——search（风险判定）与 detail（详情提取）两域共用，故落在本层；
- CDP 原语：HTTP 端点调用、WS 连接、求值、导航、就绪探测、后台标签建销。

引用方向：``scripts/zhilian/{search,detail,urls}.py → cdp.py`` 单向；
本模块不 import 任何兄弟子模块。
"""

from __future__ import annotations

import json
import random
import time
import urllib.parse
import urllib.request
from typing import Any

from webui.logging_setup import get_logger

_logger = get_logger(__name__)


DEFAULT_CDP_PORT = 9223

# 智联平台岗位 URL host allowlist（与 webui/platforms.py 注册规则一致）。
ZHILIAN_HOST_ALLOWLIST = frozenset({
    "www.zhaopin.com",
    "zhaopin.com",
    "m.zhaopin.com",
    "jobs.zhaopin.com",
    "fe-api.zhaopin.com",
    "i.zhaopin.com",
})

_ZHILIAN_LOGIN_PROBE_URL = "https://www.zhaopin.com/"
_ZHILIAN_SEARCH_PROBE_URL = "https://www.zhaopin.com/sou/"
_ZHILIAN_SEARCH_API = "https://fe-api.zhaopin.com/c/i/search/positions"
_ZHILIAN_DETAIL_PATTERN = "https://www.zhaopin.com/jobdetail/{job_id}.htm"

_ZHILIAN_PASSPORT_HOST = "passport.zhaopin.com"
_LOCATION_HREF_JS = "location.href"
_BODY_TEXT_JS = "document.body ? document.body.innerText.slice(0,3000) : ''"
_DETAIL_READY_JS = (
    "(window.__INITIAL_STATE__ && window.__INITIAL_STATE__.jobDetail "
    "&& ((window.__INITIAL_STATE__.jobDetail.detailedPosition||{}).jobDesc || "
    "document.body.innerText.includes('职位描述')))"
)

# 真实页面文本 marker（2026-08-04 核验；只保存脱敏 marker，不保存页面正文）。
# 2026-08-07 收紧：移除 "verify"/"captcha"/"稍后再试" 等泛词——正常页面
# 文案（按钮、提示、广告位）可能包含这些词，导致误判风控（账号被错误标记
# restricted 并写入 4h 冷却）。现在只保留高置信度的完整短语。
# 2026-08-13 再收紧：裸数字/英文泛词（429/403/forbidden/无法访问）会命中
# 正常详情页正文（公司排名、门牌号、英文条款），不再单独判风控。
_LOGIN_MARKERS = (
    "请登录", "登录后查看", "扫码登录", "账号登录", "立即登录",
    _ZHILIAN_PASSPORT_HOST,
)
_VERIFY_MARKERS = (
    "EdgeOne", "人机验证", "安全验证", "请完成验证", "拖动滑块",
)
_RATE_MARKERS = (
    "访问过于频繁", "请求过于频繁", "操作频繁",
    "too many requests",
)
_BLOCK_MARKERS = (
    "访问被拒绝", "禁止访问", "已被封禁",
    "403 forbidden", "http 403", "error 403",
)
_EMPTY_MARKERS = (
    "很抱歉，您搜索的职位找不到！",
    "换个条件试试吧",
)

# background tab 的 document.hidden 为 true，若平台据此判定非真人浏览会
# 拒绝渲染；导航前注入覆盖属性（对齐 BOSS _scrape_one_detail 的做法）。
_VISIBILITY_OVERRIDE_JS = (
    "Object.defineProperty(document, 'hidden', {get: () => false});"
    "Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});"
    "Object.defineProperty(document, 'webkitHidden', {get: () => false});"
    "Object.defineProperty(document, 'webkitVisibilityState', {get: () => 'visible'});"
)


def _http_json(port: int, path: str, *, method: str = "GET") -> Any:
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _find_page(port: int, create: bool = True) -> dict:
    pages = _http_json(port, "/json/list")
    for page in pages:
        if page.get("type") == "page" and "zhaopin.com" in str(page.get("url") or ""):
            return page
    for page in pages:
        if page.get("type") == "page":
            return page
    if not create:
        raise RuntimeError("no_page")
    url = urllib.parse.quote(_ZHILIAN_LOGIN_PROBE_URL, safe="")
    return _http_json(port, f"/json/new?{url}", method="PUT")


def _connect(port: int) -> Any:
    import websocket  # type: ignore

    page = _find_page(port)
    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("no_ws_url")
    ws = websocket.create_connection(ws_url, timeout=30)
    ws.settimeout(1)
    return ws


def _send(ws: Any, method: str, params: dict | None = None) -> dict:
    msg_id = int(time.time() * 1000) % 100000 + random.randint(1, 999)
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            raw = ws.recv()
        except Exception:
            continue
        message = json.loads(raw)
        if message.get("id") == msg_id:
            if "error" in message:
                raise RuntimeError(str(message["error"]))
            return message.get("result", {})
    raise TimeoutError("cdp_command_timeout")


def _evaluate(ws: Any, expression: str) -> Any:
    result = _send(ws, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": True,
    })
    value = result.get("result", {}).get("value")
    exception = result.get("exceptionDetails")
    if exception:
        raise RuntimeError(str(exception.get("text") or "evaluate_error"))
    return value


def _navigate(ws: Any, url: str) -> None:
    _evaluate(ws, f"location.href = {json.dumps(url, ensure_ascii=False)}; 'nav'")


def _wait_expression(
    ws: Any, expression: str, *, timeout: float = 25.0, interval: float = 0.8,
    sleeper: Any = None,
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if bool(_evaluate(ws, expression)):
                return True
        except Exception:
            _logger.debug("就绪探测求值失败，按未就绪处理", exc_info=True)

        (sleeper or time.sleep)(interval)
    return False


# ---------------------------------------------------------------------------
# T313: 后台 tab 建销（对齐 BOSS scrape_details 并行分支）
# ---------------------------------------------------------------------------

def _create_background_tab(port: int) -> tuple[Any, str]:
    """创建后台 tab 并连其 page 级 WS；返回 (ws, target_id)。

    ``Target.createTarget`` 是 browser 域命令：临时连 browser 级 WS 发出后
    即关闭，新 tab 后续命令全部走 page 级 WS——智联现有 ``_send``/``_evaluate``
    无 sessionId 概念，直接复用，无需引入 attachToTarget 机制。
    """
    import websocket  # type: ignore

    version = _http_json(port, "/json/version")
    browser_ws_url = version.get("webSocketDebuggerUrl")
    if not browser_ws_url:
        raise RuntimeError("no_browser_ws_url")
    browser_ws = websocket.create_connection(browser_ws_url, timeout=15)
    browser_ws.settimeout(1)
    try:
        target_id = _send(browser_ws, "Target.createTarget", {
            "url": "about:blank",
            "background": True,
        }).get("targetId")
    finally:
        try:
            browser_ws.close()
        except Exception:
            _logger.debug("浏览器会话关闭失败（best-effort 忽略）", exc_info=True)

    if not target_id:
        raise RuntimeError("create_target_failed")
    # /json/list 的 entry id 即 targetId，从中取新 tab 的 page 级 WS URL
    page = None
    for entry in _http_json(port, "/json/list"):
        if entry.get("id") == target_id:
            page = entry
            break
    if page is None or not page.get("webSocketDebuggerUrl"):
        raise RuntimeError("no_tab_ws_url")
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30)
    ws.settimeout(1)
    return ws, str(target_id)


def _close_background_tab(port: int, target_id: str) -> None:
    """关闭池 tab（HTTP 端点；page 级 WS 会话无 Target 域命令）。"""
    try:
        _http_json(
            port,
            f"/json/close/{urllib.parse.quote(target_id, safe='')}",
            method="PUT",
        )
    except Exception:
        _logger.debug("后台标签关闭请求失败（best-effort 忽略）", exc_info=True)
