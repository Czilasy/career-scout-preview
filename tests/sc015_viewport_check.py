"""SC-015 桌面/窄屏真实渲染门禁（默认只检查隔离 WebUI 5050）。

验收对象是结果页控件，必须配合 `tests/run_isolated_webui.py --fixture recrawl`
启动种子数据；`--fixture ai` 的暂停 AI 任务停在筛选步骤，不含结果页控件，
不适用本脚本。"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

import websocket


DEFAULT_BASE_URL = "http://127.0.0.1:5050"
VIEWPORTS = ((375, 812), (390, 844), (768, 1024), (1440, 900))
CONTINUE_SELECTOR = (
    '[data-testid="continue-scrape"],'
    '[data-testid="resume-ai-screen"],'
    '[data-testid="resume-recrawl"]'
)
PENDING_ROW_SELECTOR = '[data-testid="job-row"]'
RECRAWL_SELECTOR = '[data-testid="pending-recrawl"]'
PLATFORM_FILTER_SELECTOR = '[data-testid="result-platform-filter"]'


def parse_args(argv=None):
    """Parse isolated WebUI and Chrome CDP endpoints."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222/json")
    parser.add_argument("--fixture", choices=("ai", "recrawl"), default="recrawl",
                        help="隔离 WebUI 种子类型；SC-015 需要 recrawl（结果页）")
    return parser.parse_args(argv)


def find_page_tab(cdp_url="http://127.0.0.1:9222/json"):
    """Return the first inspectable Chrome page target, if one exists."""
    with urllib.request.urlopen(cdp_url, timeout=3) as response:
        tabs = json.loads(response.read())
    for tab in tabs:
        if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl"):
            return tab
    return None


def _cdp_request(ws, request_id, method, params):
    """Send one CDP command and ignore events until its matching response arrives."""
    ws.send(json.dumps({
        "id": request_id,
        "method": method,
        "params": params,
    }))
    while True:
        response = json.loads(ws.recv())
        if response.get("id") == request_id:
            if response.get("error"):
                raise RuntimeError(f"CDP {method} failed: {response['error']}")
            return response


def test_viewport(ws_url, width, height, base_url):
    """Render one real viewport through CDP and return its DOM visibility evidence."""
    ws = websocket.create_connection(
        ws_url, timeout=10, suppress_origin=True, enable_multithread=False
    )
    try:
        _cdp_request(
            ws, 1, "Page.navigate", {"url": f"{base_url.rstrip('/')}/"}
        )
        time.sleep(3)
        _cdp_request(ws, 2, "Emulation.setDeviceMetricsOverride", {
            "width": width,
            "height": height,
            "deviceScaleFactor": 2,
            "mobile": width <= 760,
        })
        time.sleep(2)

        js = r"""
(async () => {
  const inspect = (selector) => {
    const element = document.querySelector(selector);
    if (!element) return {exists: false, visible: false, text: ""};
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    const inViewport = rect.left >= 0 && rect.top >= 0
      && rect.right <= window.innerWidth && rect.bottom <= window.innerHeight;
    const centerX = Math.min(window.innerWidth - 1, Math.max(0, rect.left + rect.width / 2));
    const centerY = Math.min(window.innerHeight - 1, Math.max(0, rect.top + rect.height / 2));
    const topmost = rect.width > 0 && rect.height > 0
      ? document.elementFromPoint(centerX, centerY) : null;
    const unobscured = Boolean(topmost && (topmost === element || element.contains(topmost)));
    const visible = style.display !== "none"
      && style.visibility !== "hidden" && style.visibility !== "collapse"
      && Number.parseFloat(style.opacity || "1") > 0
      && rect.width > 0 && rect.height > 0 && inViewport && unobscured;
    return {exists: true, visible, text: element.textContent?.trim() || ""};
  };
  const layoutEvidence = () => {
    const recrawlEl = document.querySelector(__RECRAWL_SELECTOR__);
    const sliderEl = document.querySelector(__PLATFORM_FILTER_SELECTOR__);
    const rectOf = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {left: r.left, right: r.right, top: r.top, bottom: r.bottom,
              width: r.width, height: r.height};
    };
    const recrawlRect = rectOf(recrawlEl);
    const sliderRect = rectOf(sliderEl);
    const inViewport = (r) => r && r.left >= 0 && r.top >= 0
      && r.right <= window.innerWidth && r.bottom <= window.innerHeight;
    const visible = (el, r) => Boolean(el && r && inViewport(r)
      && getComputedStyle(el).display !== "none"
      && getComputedStyle(el).visibility !== "hidden"
      && Number.parseFloat(getComputedStyle(el).opacity || "1") > 0);
    const overlap = Boolean(recrawlRect && sliderRect
      && !(recrawlRect.right <= sliderRect.left
        || sliderRect.right <= recrawlRect.left
        || recrawlRect.bottom <= sliderRect.top
        || sliderRect.bottom <= recrawlRect.top));
    const clipped = Boolean(recrawlEl && recrawlEl.scrollWidth > recrawlEl.clientWidth + 1);
    return {
      recrawlButton: recrawlEl ? (visible(recrawlEl, recrawlRect) ? "visible" : "hidden") : "missing",
      platformSlider: sliderEl ? (visible(sliderEl, sliderRect) ? "visible" : "hidden") : "missing",
      overlap, recrawlTextClipped: clipped,
    };
  };
  const task = inspect('.task-progress');
  const pause = inspect('[data-testid="pause-reason"]');
  const continuation = inspect(__CONTINUE_SELECTOR__);
  let pending = inspect('.verdict-reason p');
  if (!pending.visible) {
    if (window.innerWidth <= 760) {
      const row = document.querySelector(__PENDING_ROW_SELECTOR__);
      if (row) {
        row.click();
        await new Promise(resolve => setTimeout(resolve, 250));
      }
    }
    const reason = document.querySelector('.verdict-reason p');
    if (reason) {
      reason.scrollIntoView({block: 'center', inline: 'nearest'});
      await new Promise(resolve => setTimeout(resolve, 100));
      pending = inspect('.verdict-reason p');
    }
  }
  const scrollTo = async (selector) => {
    const element = document.querySelector(selector);
    if (element) {
      element.scrollIntoView({block: "center", inline: "nearest"});
      await new Promise(resolve => setTimeout(resolve, 150));
    }
  };
  await scrollTo(__RECRAWL_SELECTOR__);
  await scrollTo(__PLATFORM_FILTER_SELECTOR__);
  const layout = layoutEvidence();
  const shell = document.querySelector('.view-shell');
  return JSON.stringify({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    isMobile: window.matchMedia('(max-width: 760px)').matches,
    url: window.location.href,
    viewShellCols: shell ? getComputedStyle(shell).gridTemplateColumns : 'none',
    taskProgress: task.exists ? 'exists' : 'none',
    taskProgressVisible: task.visible,
    pauseReason: pause.text,
    pauseReasonVisible: pause.visible,
    continueButton: continuation.exists ? 'visible' : 'missing',
    continueButtonVisible: continuation.visible,
    pendingReason: pending.text,
    pendingReasonVisible: pending.visible,
    recrawlButton: layout.recrawlButton,
    platformSlider: layout.platformSlider,
    overlap: layout.overlap,
    recrawlTextClipped: layout.recrawlTextClipped,
  });
})()
""".strip().replace(
            "__CONTINUE_SELECTOR__", json.dumps(CONTINUE_SELECTOR)
        ).replace(
            "__PENDING_ROW_SELECTOR__", json.dumps(PENDING_ROW_SELECTOR)
        ).replace(
            "__RECRAWL_SELECTOR__", json.dumps(RECRAWL_SELECTOR)
        ).replace(
            "__PLATFORM_FILTER_SELECTOR__", json.dumps(PLATFORM_FILTER_SELECTOR)
        )
        response = _cdp_request(ws, 3, "Runtime.evaluate", {
            "expression": js, "awaitPromise": True,
        })
        return json.loads(response["result"]["result"]["value"])
    finally:
        ws.close()


def _reset_viewport(ws_url):
    """Clear the CDP device metrics override after the acceptance run."""
    ws = websocket.create_connection(
        ws_url, timeout=10, suppress_origin=True, enable_multithread=False
    )
    try:
        _cdp_request(ws, 1, "Emulation.clearDeviceMetricsOverride", {})
    finally:
        ws.close()


def validate_viewport_result(result, width, base_url):
    """Raise when SC-015 evidence is absent; printing alone is never a pass."""
    failures = []
    expected_mobile = width <= 760
    if not str(result.get("url") or "").startswith(base_url.rstrip("/")):
        failures.append("页面没有加载目标隔离 WebUI")
    if int(result.get("clientWidth") or 0) != width:
        failures.append(f"视口宽度不是 {width}px")
    if result.get("overflow") or int(result.get("scrollWidth") or 0) > width:
        failures.append("页面存在横向溢出")
    if bool(result.get("isMobile")) != expected_mobile:
        failures.append("响应式断点状态不正确")
    if result.get("viewShellCols") == "none":
        failures.append("主视图未渲染")
    if result.get("taskProgress") != "exists" or result.get("taskProgressVisible") is not True:
        failures.append("任务进度未渲染")
    if (
        not str(result.get("pauseReason") or "").strip()
        or result.get("pauseReasonVisible") is not True
    ):
        failures.append("暂停原因不可见")
    if (
        result.get("continueButton") != "visible"
        or result.get("continueButtonVisible") is not True
    ):
        failures.append("继续操作不可见")
    if (
        not str(result.get("pendingReason") or "").strip()
        or result.get("pendingReasonVisible") is not True
    ):
        failures.append("待确认原因不可见")
    if result.get("recrawlButton") != "visible":
        failures.append("全部重抓入口不可见")
    if result.get("platformSlider") != "visible":
        failures.append("平台筛选滑块不可见")
    if result.get("overlap"):
        failures.append("平台筛选滑块与全部重抓按钮重叠")
    if result.get("recrawlTextClipped"):
        failures.append("全部重抓按钮文字截断")
    if failures:
        raise AssertionError("；".join(failures))


def run(argv=None):
    """Execute all required viewports and return a process-style exit code."""
    args = parse_args(argv)
    try:
        tab = find_page_tab(args.cdp_url)
        if not tab:
            print("ERROR: No page tab found in CDP", file=sys.stderr)
            return 2
        ws_url = tab["webSocketDebuggerUrl"]
        for width, height in VIEWPORTS:
            result = test_viewport(
                ws_url, width, height, args.base_url.rstrip("/")
            )
            validate_viewport_result(result, width, args.base_url.rstrip("/"))
            print(f"PASS {width}px: {json.dumps(result, ensure_ascii=False)}")
        _reset_viewport(ws_url)
        return 0
    except (
        AssertionError, KeyError, OSError, ValueError,
        urllib.error.URLError, websocket.WebSocketException,
    ) as exc:
        print(f"FAIL SC-015: {type(exc).__name__}: {exc}", file=sys.stderr)
        if args.fixture != "recrawl":
            print(
                "提示：SC-015 验证结果页控件，请用 run_isolated_webui.py --fixture recrawl 启动种子",
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
