# -*- coding: utf-8 -*-

"""BOSS 登录状态探测（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

import json
import time
from urllib.parse import urlencode
from scripts.boss.constants import API_JOB_LIST_PATH, CDP_ABOUT_BLANK, CDP_CMD_ADD_SCRIPT_ON_NEW_DOC, CDP_CMD_ATTACH_TARGET, CDP_CMD_CLOSE_TARGET, CDP_CMD_CREATE_TARGET, CDP_CMD_PAGE_NAVIGATE, DEFAULT_CDP_PORT, DEFAULT_LOGIN_TIMEOUT, HIDDEN_DEFINE_JS, LOGIN_PROBE_CITY, LOGIN_PROBE_PAGE_SIZE, LOGIN_PROBE_QUERY, _VISIBILITY_STATE_JS
from scripts.boss_cdp_signals import looks_like_risk_control
from scripts.boss.constants import log
import sys as _sys

from webui.logging_setup import get_logger

_logger = get_logger(__name__)

from scripts.boss import cdp_session
from scripts.boss import runtime

def is_logged_in_search_response(data):
    """Return True only when BOSS returns jobs with plaintext salary."""
    if not isinstance(data, dict) or data.get("code") != 0:
        return False
    zp_data = data.get("zpData", {})
    if not isinstance(zp_data, dict):
        return False
    job_list = zp_data.get("jobList")
    if not isinstance(job_list, list) or not job_list:
        return False
    return any((job.get("salaryDesc") or "").strip() for job in job_list if isinstance(job, dict))


def build_login_probe_url(query, city_code):
    params = {
        "scene": 1,
        "query": query,
        "city": city_code,
        "page": 1,
        "pageSize": LOGIN_PROBE_PAGE_SIZE,
    }
    return f"{API_JOB_LIST_PATH}?{urlencode(params)}"


def probe_login_state(cdp, sid):
    """单次搜索 API 探测 BOSS 登录态（bool 兼容包装）。

    三态实现在 probe_login_state_tri；本函数只保留「是否已登录」语义，
    供 wait_for_login 等既有调用方使用。
    """
    return probe_login_state_tri(cdp, sid) == "logged_in"


def probe_login_state_tri(cdp, sid):
    """单次搜索 API 探测，返回四态: "logged_in" | "not_logged_in" | "restricted" | "unknown"。

    判定顺序：
    - HTTP 401: 明确登录失效 → not_logged_in
    - 受限中: 其余 HTTP 4xx/429，或响应文本命中风控特征词（RISK_CONTROL_KEYWORDS）
    - 已登录: code==0 且 jobList 含明文 salaryDesc（is_logged_in_search_response）
    - 未登录: 结构完整但无明文工资
    - 未知: 空响应、JSON 解析失败或结构异常（不直接当成未登录）

    相比旧版 3 关键词 × 3 城市共 9 次请求，这里固定单关键词单城市只发 1 次。
    """
    probe_url = build_login_probe_url(LOGIN_PROBE_QUERY, LOGIN_PROBE_CITY)
    js = f"""
    (function(){{
        var xhr = new XMLHttpRequest();
        xhr.open('GET', '{probe_url}', false);
        var text = '';
        var status = 0;
        try {{ xhr.send(); text = xhr.responseText; status = xhr.status; }}
        catch (e) {{ text = ''; status = 0; }}
        return JSON.stringify({{status: status, text: text}});
    }})()
    """
    val = cdp.eval_js(js, sid)
    if not val:
        return "unknown"
    try:
        payload = json.loads(val) if isinstance(val, str) else val
    except ValueError:
        return "unknown"
    status = 0
    text = ""
    if isinstance(payload, dict):
        status = int(payload.get("status") or 0)
        text = str(payload.get("text") or "")
    elif isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, ensure_ascii=False)
    if status in (401, 403, 412, 418, 429):
        return "not_logged_in" if status == 401 else "restricted"
    # 016：先判"正常已登录返回"再谈风控——岗位正文/公司名里出现
    # "滑块/验证码/captcha"等词不再把已登录账号误判成受限。
    try:
        data = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        data = None
    if data is not None:
        code = data.get("code") if isinstance(data, dict) else None
        if code == 31:
            return "restricted"
        if code == 37:
            return "unknown"
        return "logged_in" if is_logged_in_search_response(data) else "not_logged_in"
    # 非正常结构（非 JSON）响应：高置信风控短语才判受限，其余无法确认
    if looks_like_risk_control(text):
        return "restricted"
    return "unknown"


# ============================================================
# 登录状态检测
# ============================================================
def check_login_state(cdp_port=DEFAULT_CDP_PORT):
    """通过 CDP 检测 BOSS直聘登录状态（bool 兼容包装）。

    Returns:
        True 已登录, False 未登录/受限/CDP 失败
    """
    return check_login_state_tri(cdp_port) == "logged_in"


def check_login_state_tri(cdp_port=DEFAULT_CDP_PORT):
    """通过 CDP 检测 BOSS直聘登录状态，返回三态。

    Returns:
        "logged_in" 已登录 / "not_logged_in" 未登录 /
        "restricted" 受限中 / "unknown" CDP 连接失败或超时
    """
    try:
        cdp = cdp_session.CDPSession(cdp_port)
        # 后台创建标签页，不抢占前台焦点，避免检测登录时弹窗
        r = cdp.send(CDP_CMD_CREATE_TARGET, {"url": CDP_ABOUT_BLANK, "background": True})
        tid = r["result"]["targetId"]
        r = cdp.send(CDP_CMD_ATTACH_TARGET, {"targetId": tid, "flatten": True})
        sid = r["result"]["sessionId"]

        # background 标签页 document.hidden=true、visibilityState=hidden，
        # BOSS直聘据此判定为非真人浏览。导航前注入覆盖可见性属性为 visible。
        cdp.send(CDP_CMD_ADD_SCRIPT_ON_NEW_DOC, {
            "source": (
                HIDDEN_DEFINE_JS +
                _VISIBILITY_STATE_JS +
                "Object.defineProperty(document, \'webkitHidden\', {get: () => false});"
                "Object.defineProperty(document, 'webkitVisibilityState', {get: () => 'visible'});"
            )
        }, sid)

        # 先导航到 BOSS直聘，确保 cookie 域名正确
        cdp.send(CDP_CMD_PAGE_NAVIGATE, {"url": "https://www.zhipin.com/"}, sid)
        time.sleep(4)

        state = probe_login_state_tri(cdp, sid)

        cdp.send(CDP_CMD_CLOSE_TARGET, {"targetId": tid})
        cdp.close()

        return state
    except Exception as e:
        # 覆盖 CDP 连接失败/超时/响应异常；requests 未加载时也要兜底返回 unknown
        log.error(f"登录状态检测失败: {e}")
        return "unknown"


def wait_for_login(cdp_port=DEFAULT_CDP_PORT, timeout=DEFAULT_LOGIN_TIMEOUT, interval=3,
                   account_id=None):
    """Open BOSS login page and wait until plaintext salary is available.

    account_id 非空时，登录成功会失效该账号的登录态缓存（D3 信号回写），
    下次探测重新判定，避免沿用登录前的旧状态。
    """
    cdp = cdp_session.CDPSession(cdp_port)
    r = cdp.send(CDP_CMD_CREATE_TARGET, {"url": "https://www.zhipin.com/web/user/"})
    tid = r["result"]["targetId"]
    r = cdp.send(CDP_CMD_ATTACH_TARGET, {"targetId": tid, "flatten": True})
    sid = r["result"]["sessionId"]

    deadline = time.time() + timeout
    logged_in = False
    print(f"等待 BOSS 登录完成（最长 {timeout}s）", end="", flush=True)
    try:
        while time.time() <= deadline:
            if probe_login_state(cdp, sid):
                logged_in = True
                log.info("BOSS 登录态已确认（接口返回明文薪资）")
                print("\n✅ 已检测到 BOSS 登录态，且接口返回明文薪资")
                if account_id:
                    try:
                        from scripts.login_state_cache import invalidate_login_state
                        invalidate_login_state(account_id, "boss")
                    except Exception:
                        _logger.debug("登录态缓存失效操作失败（best-effort 忽略）", exc_info=True)

                return True
            print(".", end="", flush=True)
            time.sleep(interval)
        print("\n❌ 等待登录超时")
        print("   Chrome 会继续保持打开；登录后可重新运行 --check 或抓取命令")
        return False
    finally:
        if logged_in:
            cdp.send(CDP_CMD_CLOSE_TARGET, {"targetId": tid})
        cdp.close()
