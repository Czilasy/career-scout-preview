"""智联招聘 CDP raw scraper（tasks004 真实分支）。

实现基于 2026-08-04 真实登录页核验：
- 列表使用 ``fe-api.zhaopin.com/c/i/search/positions`` POST（页内 fetch，携带 Cookie）；
- 详情使用 ``https://www.zhaopin.com/jobdetail/<number>.htm`` 页内 ``__INITIAL_STATE__``；
- 城市码来自 ``fe-api.zhaopin.com/c/i/search/base/data``（上海 538、北京 530、广州 763 等）；
- 空结果、登录墙、EdgeOne、限流、封禁 marker 来自当前真实页面文本。

本模块只做平台访问和字段归一化，不写数据库、不推进 run 状态、不执行 AI。
日志和返回值不包含 Cookie、JD 正文、页面正文、profile 路径、绝对路径或 token。

signal 字符串约定（与 webui/source.py ``_ZHILIAN_PREFLIGHT_SIGNAL_MAP`` 一致）：
  "ok" / "cdp_unavailable" / "login_required" / "verification" /
  "rate_limited" / "blocked" / "unreachable" / "timeout"
fetch_list: ("ok", jobs) / ("empty", [], evidence) / (signal, [])
fetch_detail: ("ok", detail) / (signal, {})
"""

from __future__ import annotations

import hashlib
import json
import random
import time
import urllib.parse
import urllib.request
from typing import Any

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

# 真实页面文本 marker（2026-08-04 核验；只保存脱敏 marker，不保存页面正文）。
# 2026-08-07 收紧：移除 "verify"/"captcha"/"稍后再试" 等泛词——正常页面
# 文案（按钮、提示、广告位）可能包含这些词，导致误判风控（账号被错误标记
# restricted 并写入 4h 冷却）。现在只保留高置信度的完整短语。
_LOGIN_MARKERS = (
    "请登录", "登录后查看", "扫码登录", "账号登录", "立即登录",
    "passport.zhaopin.com",
)
_VERIFY_MARKERS = (
    "EdgeOne", "人机验证", "安全验证", "请完成验证", "拖动滑块",
)
_RATE_MARKERS = (
    "访问过于频繁", "请求过于频繁", "操作频繁",
    "429", "too many requests",
)
_BLOCK_MARKERS = (
    "访问被拒绝", "禁止访问", "无法访问", "已被封禁",
    "403", "forbidden",
)
_EMPTY_MARKERS = (
    "很抱歉，您搜索的职位找不到！",
    "换个条件试试吧",
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
            pass
        (sleeper or time.sleep)(interval)
    return False


def _risk_signal(text: str, url: str = "") -> str | None:
    low = text.lower()
    if any(m.lower() in low for m in _VERIFY_MARKERS):
        return "verification"
    if any(m.lower() in low for m in _RATE_MARKERS):
        return "rate_limited"
    if any(m.lower() in low for m in _BLOCK_MARKERS):
        return "blocked"
    if any(m.lower() in low for m in _LOGIN_MARKERS) or "passport.zhaopin.com" in url:
        return "login_required"
    return None


def _normalize_job(item: dict, *, city_code: str) -> dict:
    job_id = str(item.get("number") or item.get("positionId") or "").strip()
    title = str(item.get("name") or "").strip()
    company = str(item.get("companyName") or "").strip()
    salary = str(item.get("salary") or item.get("salaryReal") or "").strip()
    location = " ".join(
        part for part in (
            str(item.get("workCity") or "").strip(),
            str(item.get("cityDistrict") or "").strip(),
        ) if part
    )
    experience = str(item.get("workingExp") or "").strip()
    degree = str(item.get("education") or "").strip()
    raw_url = str(item.get("positionURL") or "").strip()
    canonical = _canonical_job_url(job_id) if job_id else ""
    extra: dict[str, str] = {}
    for key, label in (
        ("companySize", "company_size"),
        ("industryName", "industry"),
        ("propertyName", "company_nature_label"),
    ):
        value = str(item.get(key) or "").strip()
        if value:
            extra[label] = value
    return {
        "platform": "zhilian",
        "platform_job_id": job_id,
        "title": title,
        "company": company,
        "salary": salary,
        "location": location,
        "experience": experience,
        "degree": degree,
        "source_url": raw_url or canonical,
        "canonical_url": canonical,
        "extra": extra,
    }


def _canonical_job_url(job_id: str) -> str:
    return _ZHILIAN_DETAIL_PATTERN.format(job_id=urllib.parse.quote(job_id, safe=""))


def _search_fetch_expression(keyword: str, city_code: str, page_index: int) -> str:
    body = {
        "S_SOU_FULL_INDEX": keyword,
        "order": 0,
        "actionid": f"zs-{int(time.time() * 1000)}",
        "pageSize": 20,
        "pageIndex": page_index,
        "anonymous": 1,
        "platform": 13,
        "version": "0.0.0",
    }
    # 全国（城市码为空）不传 S_SOU_WORK_CITY：智联 fe-api 对 "0"/"jl0"
    # 一律忽略城市条件但返回空列表（2026-08-07 实测 code=200 空 list），
    # 只有省略字段才是真正的全国搜索。
    if city_code:
        body["S_SOU_WORK_CITY"] = city_code
    return (
        "(async()=>{"
        "const body=" + json.dumps(body, ensure_ascii=False) + ";"
        "const r=await fetch(" + json.dumps(_ZHILIAN_SEARCH_API) + ","
        "{method:'POST',credentials:'include',"
        "headers:{'Content-Type':'application/json'},"
        "body:JSON.stringify(body)});"
        "const t=await r.text();let j={};"
        "try{j=JSON.parse(t)}catch(e){return {error:'parse'}}"
        "if(!j||j.code!==200){return {error:String(j&&j.code||r.status),msg:j&&j.msg||''}}"
        "const d=j.data||{};const list=Array.isArray(d.list)?d.list:[];"
        "return {count:d.count||0,isEndPage:!!d.isEndPage,"
        "jobs:list.map(x=>({name:x.name,salary:x.salary60||x.salaryReal||x.salary||'',"
        "workingExp:x.workingExp||'',education:x.education||'',"
        "workCity:x.workCity||'',cityDistrict:x.cityDistrict||'',"
        "positionURL:x.positionURL||'',number:x.number||x.positionId||'',"
        "companyName:x.companyName||'',companySize:x.companySize||'',"
        "industryName:x.industryName||'',propertyName:x.propertyName||''}))};"
        "})()"
    )


def _api_city_code(platform_code: str) -> str:
    """把注册表城市码映射为搜索 API 的 S_SOU_WORK_CITY 值。

    全国（jl0）返回空串：fe-api 不传城市字段才是真正的全国搜索
    （"0"/"jl0" 会被服务端接受但恒返回空列表，2026-08-07 实测）。
    """
    if platform_code == "jl0":
        return ""
    return platform_code


def check_login_state_tri(cdp_port: int = DEFAULT_CDP_PORT) -> str:
    """智联登录态 DOM marker 探测，零 API 请求。

    Returns:
        "logged_in" / "not_logged_in" / "restricted" / "unknown"

    方法：导航智联搜索页，读取页面文本做 marker 判定：
    - _LOGIN_MARKERS 命中（"请登录"等）→ not_logged_in
    - _VERIFY_MARKERS（EdgeOne/人机验证）或 _RATE_MARKERS（访问过于频繁）→ restricted
    - 均未命中 → logged_in（宽松判定）
    - CDP 连接失败 / 导航超时 → unknown

    marker 可靠性说明（2026-08-04 + 2026-08-05 真实页面冒烟核验）：
    - 无 Cookie 的新 profile 访问搜索页会先落到 Tencent EdgeOne 人机验证页
      （文本约 180 字符，含 "EdgeOne"/"请完成验证"），判定为 restricted；
    - 未登录搜索页会出现"请登录"引导且 URL 可能跳转 passport.zhaopin.com，
      两者都可稳定命中；
    - 已登录搜索页 header 显示用户名，不出现"请登录/立即登录"字样，
      因此宽松判定不会误伤已登录用户；
    - 限流页固定含"访问过于频繁"。
    """
    try:
        ws = _connect(cdp_port)
    except Exception:
        return "unknown"
    try:
        _navigate(ws, _ZHILIAN_SEARCH_PROBE_URL)
        loaded = _wait_expression(
            ws,
            "document.body && document.body.innerText.trim().length > 40",
            timeout=30,
        )
        if not loaded:
            return "unknown"
        body = str(_evaluate(ws, "document.body.innerText.slice(0, 6000)") or "")
        url = str(_evaluate(ws, "location.href") or "")
    except Exception:
        return "unknown"
    finally:
        try:
            ws.close()
        except Exception:
            pass
    low = body.lower()
    if any(m.lower() in low for m in _LOGIN_MARKERS) or "passport.zhaopin.com" in url:
        return "not_logged_in"
    if any(m.lower() in low for m in _VERIFY_MARKERS) or any(m.lower() in low for m in _RATE_MARKERS):
        return "restricted"
    return "logged_in"


def preflight(cdp_port: int = DEFAULT_CDP_PORT) -> str | None:
    """检查 CDP 端口、登录态和平台可访问性，返回稳定 signal。"""
    try:
        version = _http_json(cdp_port, "/json/version")
        if not isinstance(version, dict) or not version.get("Browser"):
            return "cdp_unavailable"
    except Exception:
        return "cdp_unavailable"

    try:
        ws = _connect(cdp_port)
    except Exception:
        return "cdp_unavailable"

    try:
        _navigate(ws, _ZHILIAN_LOGIN_PROBE_URL)
        # 首页真实 user 对象只暴露 Name/Resume（2026-08-04 核验），
        # 登录判定同时接受 __INITIAL_STATE__.user.Name 与 DOM 登录名。
        ok = _wait_expression(
            ws,
            "(window.__INITIAL_STATE__ && window.__INITIAL_STATE__.user "
            "&& (Number(window.__INITIAL_STATE__.user.userId||0)>0 "
            "|| String(window.__INITIAL_STATE__.user.Name||'').trim()!=='')) "
            "|| (document.querySelector('.c-login__top__name') "
            "&& String(document.querySelector('.c-login__top__name').innerText||'').trim()!=='')",
            timeout=30,
        )
        if not ok:
            body = str(_evaluate(ws, "document.body ? document.body.innerText.slice(0,3000) : ''") or "")
            signal = _risk_signal(body, str(_evaluate(ws, "location.href") or ""))
            return signal or "login_required"
        body = str(_evaluate(ws, "document.body ? document.body.innerText.slice(0,3000) : ''") or "")
        signal = _risk_signal(body, str(_evaluate(ws, "location.href") or ""))
        return "verification" if signal == "verification" else ("rate_limited" if signal == "rate_limited"
               else ("blocked" if signal == "blocked" else "ok"))
    except TimeoutError:
        return "timeout"
    except Exception:
        return "unreachable"
    finally:
        try:
            ws.close()
        except Exception:
            pass


def fetch_list(plan_item: dict) -> tuple[str | None, list[dict], dict | None]:
    """抓取智联岗位列表；返回 (signal, jobs, empty_evidence)。"""
    city = plan_item.get("city") or {}
    keyword = str(plan_item.get("keyword") or "").strip()
    city_code = str(city.get("platform_code") or "").strip()
    target_pages = int(plan_item.get("target_pages") or 1)
    if not keyword or not city_code:
        return "invalid_output", [], None

    try:
        ws = _connect(DEFAULT_CDP_PORT if not plan_item.get("cdp_port") else int(plan_item["cdp_port"]))
    except Exception:
        return "cdp_unavailable", [], None

    try:
        merged: dict[str, dict] = {}
        api_city = _api_city_code(city_code)
        for page_index in range(1, max(1, target_pages) + 1):
            value = _evaluate(ws, _search_fetch_expression(keyword, api_city, page_index))
            if not isinstance(value, dict) or value.get("error"):
                return "invalid_output", [], None
            jobs = [_normalize_job(item, city_code=city_code) for item in value.get("jobs") or []]
            for job in jobs:
                jid = job.get("platform_job_id")
                if jid:
                    merged[jid] = job
            if value.get("isEndPage") or not value.get("jobs"):
                break
            time.sleep(random.uniform(0.8, 1.6))
        if merged:
            return "ok", list(merged.values()), None
        # 真实空结果：必须能由当前页面明确空状态 marker 解释。
        if _has_empty_marker(ws, city_code, keyword):
            evidence = {
                "kind": "explicit_empty_state",
                "fixture_version": "zhilian-list-v1",
                "marker": "normalized-empty-state",
            }
            return "empty", [], evidence
        return "invalid_output", [], None
    except TimeoutError:
        return "timeout", [], None
    except Exception:
        return "unreachable", [], None
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _has_empty_marker(ws: Any, city_code: str, keyword: str) -> bool:
    """导航到搜索页并用页面搜索框确认空状态 marker。"""
    route_city = "jl0" if city_code == "jl0" else f"jl{city_code}"
    url = f"https://www.zhaopin.com/sou/{route_city}/kw/p1"
    try:
        _navigate(ws, url)
        _wait_expression(ws, "document.querySelector('.query-search__content-input')!==null", timeout=20)
        expression = (
            "(async()=>{"
            "const i=document.querySelector('.query-search__content-input');"
            "const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
            "setter.call(i," + json.dumps(keyword, ensure_ascii=False) + ");"
            "i.dispatchEvent(new Event('input',{bubbles:true}));"
            "i.dispatchEvent(new Event('change',{bubbles:true}));"
            "await new Promise(r=>setTimeout(r,600));"
            "const b=document.querySelector('.query-search__content-button');"
            "if(b){b.click();}return true;})()"
        )
        _evaluate(ws, expression)
        ok = _wait_expression(
            ws,
            "document.body.innerText.includes('很抱歉，您搜索的职位找不到！') || "
            "document.querySelectorAll('div.joblist-box__item').length>0",
            timeout=20,
        )
        if not ok:
            return False
        return bool(_evaluate(
            ws,
            "document.body.innerText.includes('很抱歉，您搜索的职位找不到！') && "
            "document.querySelectorAll('div.joblist-box__item').length===0",
        ))
    except Exception:
        return False


def _scrape_detail_on_ws(
    ws: Any, job: dict, *, sleeper: Any = None,
) -> tuple[str, dict]:
    """在已连接的 page WS 上抓取单岗位详情（导航+提取+校验+构建）。

    单条 ``fetch_detail`` 与并行 tab worker 共用（T313）；``sleeper``
    透传给就绪探针，测试可注入替身记录等待序列。返回 ``(signal, detail)``：
    - "ok"：detail 为结构化记录
    - 平台级：login_required / verification / rate_limited / blocked
    - 单条失败：not_found / invalid_output / timeout / unreachable
    """
    job_id = str(job.get("platform_job_id") or "").strip()
    canonical = str(job.get("canonical_url") or _canonical_job_url(job_id)).strip()
    if not job_id or not canonical:
        return "invalid_output", {}
    _navigate(ws, canonical)
    ready = _wait_expression(
        ws,
        "(window.__INITIAL_STATE__ && window.__INITIAL_STATE__.jobDetail "
        "&& ((window.__INITIAL_STATE__.jobDetail.detailedPosition||{}).jobDesc || "
        "document.body.innerText.includes('职位描述')))",
        timeout=30,
        sleeper=sleeper,
    )
    if not ready:
        body = str(_evaluate(ws, "document.body ? document.body.innerText.slice(0,3000) : ''") or "")
        signal = _risk_signal(body, str(_evaluate(ws, "location.href") or ""))
        return signal or "not_found", {}
    value = _evaluate(ws, (
        "(()=>{const s=window.__INITIAL_STATE__||{};"
        "const p=((s.jobDetail||{}).detailedPosition)||{};"
        "const c=((s.jobDetail||{}).detailedCompany)||{};"
        "const clean=(p.jobDesc||'').replace(/<br\\s*\\/?>/gi,'\\n').replace(/<[^>]+>/g,'').trim();"
        "return {number:p.number||p.positionNumber||'',name:p.name||'',salary:p.salary||'',"
        "workingExp:p.workingExp||'',education:p.education||'',"
        "workCity:p.workCity||'',cityDistrict:p.cityDistrict||'',"
        "companyName:p.companyName||'',companySize:c.companySize||'',"
        "industry:c.industryName||'',jd:clean,positionStatus:p.positionStatus||0,"
        "jobStatus:p.jobStatus||0};})()"
    ))
    if not isinstance(value, dict):
        return "invalid_output", {}
    detail_id = str(value.get("number") or "").strip()
    jd = str(value.get("jd") or "").strip()
    if detail_id and detail_id != job_id:
        return "invalid_output", {}
    if not jd:
        if str(value.get("positionStatus") or "") in ("4", "5", "6") or str(value.get("jobStatus") or "") in ("4", "5", "6"):
            return "not_found", {}
        return "invalid_output", {}
    detail = {
        "platform": "zhilian",
        "platform_job_id": detail_id or job_id,
        "title": str(value.get("name") or job.get("title") or "").strip(),
        "company": str(value.get("companyName") or job.get("company") or "").strip(),
        "salary": str(value.get("salary") or job.get("salary") or "").strip(),
        "location": " ".join(part for part in (
            str(value.get("workCity") or "").strip(),
            str(value.get("cityDistrict") or "").strip(),
        ) if part),
        "experience": str(value.get("workingExp") or job.get("experience") or "").strip(),
        "degree": str(value.get("education") or job.get("degree") or "").strip(),
        "jd": jd,
        "canonical_url": _canonical_job_url(detail_id or job_id),
        "source_url": _canonical_job_url(detail_id or job_id),
        "extra": dict(job.get("extra") or {}),
    }
    return "ok", detail


def fetch_detail(job: dict, *, detail_output_path: str | None = None) -> tuple[str | None, dict]:
    """抓取智联单岗位详情；返回 (signal, detail)。"""
    job_id = str(job.get("platform_job_id") or "").strip()
    canonical = str(job.get("canonical_url") or _canonical_job_url(job_id)).strip()
    if not job_id or not canonical:
        return "invalid_output", {}
    port = DEFAULT_CDP_PORT if not job.get("cdp_port") else int(job["cdp_port"])
    try:
        ws = _connect(port)
    except Exception:
        return "cdp_unavailable", {}

    try:
        return _scrape_detail_on_ws(ws, job)
    except TimeoutError:
        return "timeout", {}
    except Exception:
        return "unreachable", {}
    finally:
        try:
            ws.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# T313: 并行 tab 池抓取（对齐 BOSS scrape_details 并行分支）
# ---------------------------------------------------------------------------
# background tab 的 document.hidden 为 true，若平台据此判定非真人浏览会
# 拒绝渲染；导航前注入覆盖属性（对齐 BOSS _scrape_one_detail 的做法）。
_VISIBILITY_OVERRIDE_JS = (
    "Object.defineProperty(document, 'hidden', {get: () => false});"
    "Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});"
    "Object.defineProperty(document, 'webkitHidden', {get: () => false});"
    "Object.defineProperty(document, 'webkitVisibilityState', {get: () => 'visible'});"
)

# 平台级信号：任一命中即全体停工（对齐 BOSS 登录墙/限流降级语义）。
_DEGRADE_SIGNALS = frozenset({
    "login_required", "verification", "rate_limited", "blocked",
})


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
            pass
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
        pass


def _reset_detail_session(ws: Any, sleeper: Any, tab_label: str) -> None:
    """导航回智联首页重置详情抓取上下文（对齐 BOSS ``_reset_detail_session``）。

    防御性措施：每抓 ``reset_every`` 条导航回首页 + 等待 + 滚动，打散请求
    序列，降低连续详情页访问触发 EdgeOne/限流的风险。智联无 BOSS code:37
    式 session 计数依据，真实效果由 tab=2 实跑核验。
    """
    print(f"[{tab_label}] ⟳ session 重置：导航回首页...")
    _navigate(ws, _ZHILIAN_LOGIN_PROBE_URL)
    sleeper(random.uniform(3, 5), label="session_reset_wait")
    _evaluate(ws, "window.scrollBy(0, 300); void(0);")
    sleeper(random.uniform(1.5, 2.5), label="session_reset_scroll")
    _evaluate(ws, "window.scrollBy(0, -200); void(0);")
    sleeper(random.uniform(1, 1.5), label="session_reset_scroll2")
    print(f"[{tab_label}] ⟳ session 重置完成")


def _detail_tab_worker(cdp_port: int, connector: Any, work_queue: Any,
                       total: int, *, sleeper: Any,
                       inter_job_gap_range: tuple[float, float],
                       stagger_range: tuple[float, float], tab_id: int,
                       reset_every: int, degrade_event: Any,
                       degrade_reason: dict[str, str], results_lock: Any,
                       results: dict[int, tuple[str, dict]]) -> None:
    """常驻 tab 工作线程：建池 → 错峰启动 → 循环领任务抓详情 → 重置 → 关池。

    与 BOSS ``_tab_worker`` 同构，连接走智联 page 级 WS（无 sessionId）：
    - ``connector(cdp_port)`` 建 background tab，返回 ``(ws, target_id)``
    - 每条 ``(signal, detail)`` 在 ``results_lock`` 保护下写入
      ``results[orig_idx]``，主线程 join 后按原顺序聚合
    - 平台级 signal 置 ``degrade_event`` 全体停工；单条失败
      （not_found/invalid_output/timeout/unreachable）不中断
    """
    tab_label = f"tab{tab_id + 1}"
    ws = None
    target_id = None
    try:
        ws, target_id = connector(cdp_port)
    except Exception:
        print(f"[{tab_label}] ⚠ 建池失败（CDP 不可达）")
        degrade_reason["reason"] = "cdp_unavailable"
        degrade_event.set()
        return
    try:
        _send(ws, "Page.addScriptToEvaluateOnNewDocument", {
            "source": _VISIBILITY_OVERRIDE_JS,
        })
        # 错峰启动：首批第 1 个立即开始，之后每个等随机 stagger 再领任务
        if tab_id > 0:
            stagger = random.uniform(stagger_range[0], stagger_range[1])
            print(f"[{tab_label}] 错峰等待 {stagger:.1f}s 后开始")
            sleeper(stagger, label="stagger")
        jobs_done_on_tab = 0
        while not degrade_event.is_set():
            try:
                job, seq, orig_idx = work_queue.get_nowait()
            except Exception:
                break  # 队列空，退出
            is_last = seq == total - 1
            signal, detail = _scrape_detail_on_ws(ws, job, sleeper=sleeper)
            signal = str(signal or "invalid_output")
            with results_lock:
                results[orig_idx] = (signal, dict(detail or {}))
            if signal in _DEGRADE_SIGNALS:
                print(f"[{tab_label}] ⚠ 命中平台级信号 {signal}，触发降级停工")
                degrade_reason["reason"] = signal
                degrade_event.set()
                break
            jobs_done_on_tab += 1
            # 每抓 reset_every 个详情导航回首页重置一次（对齐 BOSS 语义）
            if jobs_done_on_tab % reset_every == 0 and not is_last:
                _reset_detail_session(ws, sleeper, tab_label)
            # 补位节奏：抓完等随机间隔再领下一个
            if not is_last:
                gap = random.uniform(inter_job_gap_range[0], inter_job_gap_range[1])
                print(f"[{tab_label}]   等待 {gap:.1f}s 后抓下一个...")
                sleeper(gap, label="inter_job_gap")
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        if target_id is not None:
            _close_background_tab(cdp_port, target_id)
        print(f"[{tab_label}] 已关闭")


def scrape_details_batch(list_data, max_details=None, output_path=None,
                         cdp_port=DEFAULT_CDP_PORT, *,
                         tab_pool_size=5, inter_job_gap_range=(2, 9),
                         stagger_range=(3, 8), reset_every=4,
                         event_callback=None, sleeper=None, connector=None):
    """tab 池并行抓取岗位详情；返回 ``(per_item, degrade_signal)``。

    - ``per_item``：按输入顺序的 ``[(signal, detail), ...]``，每条独立成败；
      degrade 停工后未处理的任务以 ``("skipped", {})`` 占位，由调用方映射
      ``source_blocked``。
    - ``degrade_signal``：平台级信号（login_required/verification/
      rate_limited/blocked/cdp_unavailable）或 None，用于调用方推进熔断器。
    - ``tab_pool_size``：常驻 tab 数，1-10；``reset_every``：每抓 N 条导航回
      首页重置；``inter_job_gap_range``/``stagger_range``：条间间隔与错峰
      启动范围；``sleeper``/``connector``：测试注入点（等待替身 / 建池替身）。

    ``output_path``/``event_callback`` 为兼容参数：智联 in-process 直接返回
    结果，不写盘、不产出事件文件（与 ``fetch_detail`` 现状一致）。单条失败
    不中断整体；平台级 signal 触发全体停工，已抓结果保留。
    """
    import queue as _queue_mod
    import threading

    if not isinstance(tab_pool_size, int) or tab_pool_size < 1 or tab_pool_size > 10:
        raise ValueError(
            f"tab_pool_size must be an integer between 1 and 10, got {tab_pool_size!r}"
        )
    if not isinstance(reset_every, int) or reset_every < 1:
        raise ValueError(f"reset_every must be an integer >= 1, got {reset_every!r}")
    if not inter_job_gap_range or len(inter_job_gap_range) != 2:
        raise ValueError("inter_job_gap_range must be a (min, max) pair")
    if inter_job_gap_range[0] < 0 or inter_job_gap_range[1] < inter_job_gap_range[0]:
        raise ValueError(f"inter_job_gap_range invalid: {inter_job_gap_range!r}")
    if not stagger_range or len(stagger_range) != 2:
        raise ValueError("stagger_range must be a (min, max) pair")
    if stagger_range[0] < 0 or stagger_range[1] < stagger_range[0]:
        raise ValueError(f"stagger_range invalid: {stagger_range!r}")
    if sleeper is None:
        sleeper = time.sleep
    if connector is None:
        connector = _create_background_tab

    raw_jobs = list_data.get("jobs", []) if isinstance(list_data, dict) else list_data
    if max_details:
        raw_jobs = raw_jobs[:max_details]
    # 按 canonical_url 去重，保持原始顺序
    seen_links = set()
    unique_jobs = []
    for job in raw_jobs:
        url = str(job.get("canonical_url") or "").strip()
        if not url or url in seen_links:
            continue
        seen_links.add(url)
        unique_jobs.append(job)

    total = len(unique_jobs)
    if total == 0:
        return [], None

    results_lock = threading.Lock()
    degrade_event = threading.Event()
    degrade_reason: dict[str, str] = {}
    work_queue = _queue_mod.Queue()
    # 随机顺序进队列（请求顺序不可预测），但保留原始下标用于结果聚合
    indexed = list(enumerate(unique_jobs))
    random.shuffle(indexed)
    for seq, (orig_idx, job) in enumerate(indexed):
        work_queue.put((job, seq, orig_idx))
    results: dict[int, tuple[str, dict]] = {}

    threads = []
    for tab_id in range(tab_pool_size):
        t = threading.Thread(
            target=_detail_tab_worker,
            args=(cdp_port, connector, work_queue, total),
            kwargs={
                "sleeper": sleeper,
                "inter_job_gap_range": inter_job_gap_range,
                "stagger_range": stagger_range,
                "tab_id": tab_id,
                "reset_every": reset_every,
                "degrade_event": degrade_event,
                "degrade_reason": degrade_reason,
                "results_lock": results_lock,
                "results": results,
            },
            name=f"zhilian-detail-tab{tab_id + 1}",
            daemon=True,
        )
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    per_item = [
        results[idx] if idx in results else ("skipped", {})
        for idx in range(total)
    ]
    return per_item, degrade_reason.get("reason")


def is_zhilian_host(url: str) -> bool:
    """URL host 是否在智联 allowlist 内（脱敏判定）。"""
    if not url:
        return False
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except (ValueError, TypeError):
        return False
    return host in ZHILIAN_HOST_ALLOWLIST


def input_hash(payload: dict) -> str:
    """智联输入 hash：覆盖 platform/关键词/完整城市解析快照/页数。"""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
