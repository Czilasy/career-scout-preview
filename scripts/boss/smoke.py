# -*- coding: utf-8 -*-

"""环境冒烟检查与健康巡检（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

import json
import time
from urllib.parse import urlencode
from scripts.boss.constants import API_JOB_LIST_PATH, BROWSER_NOT_FOUND_HINT, CDP_CMD_ATTACH_TARGET, CDP_CMD_CLOSE_TARGET, CDP_CMD_CREATE_TARGET, DEFAULT_CDP_PORT, DEFAULT_CITY_INPUT, FETCH_API_JS_TEMPLATE, LOGIN_PROBE_QUERY, MSG_BOSS_LOGIN_STATUS, MSG_DEDICATED_BROWSER_STARTED, detect_chromium_browsers
from scripts.boss.login import check_login_state_tri
from scripts.boss.search import build_search_url
import sys as _sys
def _facade():
    return _sys.modules.get("scripts.boss_cdp_raw")

def parse_jobs_eval_value(value):
    if not value:
        return []
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def has_usable_smoke_jobs(jobs):
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if (
            job.get("title")
            and job.get("salary")
            and job.get("salary_source") == "api"
            and job.get("job_link")
        ):
            return True
    return False


def run_smoke_test(cdp_port=DEFAULT_CDP_PORT):
    """Run a real browser/API smoke test without writing result files."""
    if not _facade().require_runtime_dependencies("requests", "websocket"):
        return 1

    try:
        cdp = _facade().CDPSession(cdp_port)
        city_name, city_code = _facade().resolve_city(DEFAULT_CITY_INPUT)
        search_url = build_search_url(LOGIN_PROBE_QUERY, city_code, 1, {})
        r = cdp.send(CDP_CMD_CREATE_TARGET, {"url": search_url})
        tid = r["result"]["targetId"]
        r = cdp.send(CDP_CMD_ATTACH_TARGET, {"targetId": tid, "flatten": True})
        sid = r["result"]["sessionId"]

        print(f"打开 BOSS 搜索页: {LOGIN_PROBE_QUERY} @ {city_name}")
        time.sleep(4)
        api_url = f"{API_JOB_LIST_PATH}?{urlencode({'scene': '1', 'query': LOGIN_PROBE_QUERY, 'city': city_code, 'page': 1, 'pageSize': 5})}"
        api_js = FETCH_API_JS_TEMPLATE.replace("__API_URL__", api_url)
        jobs = parse_jobs_eval_value(cdp.eval_js(api_js, sid))
        cdp.send(CDP_CMD_CLOSE_TARGET, {"targetId": tid})
        cdp.close()

        if has_usable_smoke_jobs(jobs):
            sample = next(job for job in jobs if job.get("salary") and job.get("job_link"))
            print(f"✅ Smoke test 通过: {sample.get('title')} | {sample.get('salary')}")
            return 0
        print("❌ Smoke test 未拿到可用职位；请检查登录态或 BOSS API 返回")
        return 1
    except (_facade().requests.ConnectionError, _facade().requests.Timeout, KeyError,
            json.JSONDecodeError, _facade().websocket.WebSocketException, TimeoutError) as e:
        print(f"❌ Smoke test 失败: {e}")
        return 1


# ============================================================
# --check 环境检查
# ============================================================
def collect_check_items(cdp_port=DEFAULT_CDP_PORT):
    """收集结构化环境检查结果，CLI 与 Web 展示共用同一套逻辑。

    Returns:
        (items, all_pass)
        items: [{
            "id": "browsers" | "deps" | "cdp" | "boss_login",
            "name": str,
            "status": "ok" | "fail" | "skip",
            "detail": str,
            "fix": str | None,   # 可选修复动作文案
        }]
    """
    items = []
    all_pass = True

    def append(item_id, name, status, detail, fix=None):
        nonlocal all_pass
        items.append({
            "id": item_id, "name": name,
            "status": status, "detail": detail, "fix": fix,
        })
        if status == "fail":
            all_pass = False

    # 检查 1: Chromium 浏览器（Chrome / Edge 双探测）
    browsers = detect_chromium_browsers()
    found_parts = []
    if browsers.get("chrome"):
        found_parts.append("找到 Chrome ✅")
    if browsers.get("edge"):
        found_parts.append("找到 Edge ✅")
    if found_parts:
        append("browsers", "Chromium 浏览器",
               "ok", "；".join(found_parts))
    else:
        append("browsers", "Chromium 浏览器", "fail",
               "未找到 Chrome 或 Edge", BROWSER_NOT_FOUND_HINT)

    # 检查 2: Python 依赖
    deps_ok = _facade().require_runtime_dependencies("websocket", "requests")
    if deps_ok:
        append("deps", "Python 依赖", "ok", "requests / websocket 可导入")
    else:
        missing = []
        if _facade().requests is None:
            missing.append("requests")
        if _facade().websocket is None:
            missing.append("websocket")
        append("deps", "Python 依赖", "fail",
               f"缺少依赖: {', '.join(missing)}，请运行 uv sync 或 pip install -r requirements.txt")

    # 检查 3: CDP 端口连通性（专用浏览器是否已启动）
    cdp_status = "skip"
    if _facade().requests is None:
        append("cdp", MSG_DEDICATED_BROWSER_STARTED, "skip",
               f"跳过 — 缺少 requests（无法探测 127.0.0.1:{cdp_port}）")
    else:
        try:
            resp = _facade().requests.get(f"http://127.0.0.1:{cdp_port}/json/version", timeout=5)
            data = resp.json()
            browser = data.get("Browser", "未知")
            cdp_status = "ok"
            append("cdp", MSG_DEDICATED_BROWSER_STARTED, "ok",
                   f"CDP 端口 {cdp_port} 就绪 — {browser}")
        except (_facade().requests.ConnectionError, _facade().requests.Timeout):
            cdp_status = "fail"
            append("cdp", MSG_DEDICATED_BROWSER_STARTED, "fail",
                   f"无法连接 127.0.0.1:{cdp_port}（启动任务时会自动拉起浏览器）")
        except (json.JSONDecodeError, KeyError) as e:
            cdp_status = "fail"
            append("cdp", MSG_DEDICATED_BROWSER_STARTED, "fail",
                   f"CDP 响应异常: {e}")

    # 检查 4: BOSS 登录状态（三态）
    if not deps_ok or cdp_status != "ok":
        append("boss_login", MSG_BOSS_LOGIN_STATUS, "skip",
               "跳过 — 浏览器未就绪，无法探测登录态")
    else:
        try:
            state = check_login_state_tri(cdp_port)
            if state == "logged_in":
                append("boss_login", MSG_BOSS_LOGIN_STATUS, "ok",
                       "已登录（接口返回明文薪资）")
            elif state == "restricted":
                append("boss_login", MSG_BOSS_LOGIN_STATUS, "fail",
                       "受限中 — 账号或 IP 命中风控，建议等待后重试")
            else:
                append("boss_login", MSG_BOSS_LOGIN_STATUS, "fail",
                       "未登录 — 请先在专用浏览器中登录 zhipin.com",
                       "打开专用浏览器登录: python3 scripts/boss_cdp_raw.py --setup-chrome")
        except Exception as e:
            append("boss_login", MSG_BOSS_LOGIN_STATUS, "fail",
                   f"检测失败: {e}")

    return items, all_pass


def run_check(cdp_port=DEFAULT_CDP_PORT):
    """运行环境诊断检查（终端展示层，逻辑见 collect_check_items）"""
    print("=" * 50)
    print("  BOSS直聘 CDP 环境检查")
    print("=" * 50)
    print()

    items, all_pass = collect_check_items(cdp_port)
    for index, item in enumerate(items, start=1):
        mark = {"ok": "✅", "fail": "❌", "skip": "⏭️"}.get(item["status"], "?")
        print(f"[{index}/{len(items)}] {item['name']}...")
        print(f"  {mark} {item['detail']}")
        if item["fix"]:
            print(f"     🔧 {item['fix']}")

    print()
    if all_pass:
        print("✅ 所有检查通过，可以开始抓取")
    else:
        print("❌ 部分检查未通过，请修复后重试")
    print()

    return 0 if all_pass else 1
