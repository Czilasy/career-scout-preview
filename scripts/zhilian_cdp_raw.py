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
_ZHILIAN_SEARCH_API = "https://fe-api.zhaopin.com/c/i/search/positions"
_ZHILIAN_DETAIL_PATTERN = "https://www.zhaopin.com/jobdetail/{job_id}.htm"

# 真实页面文本 marker（2026-08-04 核验；只保存脱敏 marker，不保存页面正文）。
_LOGIN_MARKERS = (
    "请登录", "登录后查看", "扫码登录", "账号登录", "立即登录",
    "passport.zhaopin.com",
)
_VERIFY_MARKERS = (
    "EdgeOne", "人机验证", "安全验证", "请完成验证", "拖动滑块",
    "verify", "captcha",
)
_RATE_MARKERS = (
    "访问过于频繁", "请求过于频繁", "操作频繁", "稍后再试",
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
) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if bool(_evaluate(ws, expression)):
                return True
        except Exception:
            pass
        time.sleep(interval)
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
        "S_SOU_WORK_CITY": city_code,
        "order": 0,
        "actionid": f"zs-{int(time.time() * 1000)}",
        "pageSize": 20,
        "pageIndex": page_index,
        "anonymous": 1,
        "platform": 13,
        "version": "0.0.0",
    }
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
    """把注册表城市码映射为搜索 API 的 S_SOU_WORK_CITY 值。"""
    if platform_code == "jl0":
        return "0"
    return platform_code


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
        _navigate(ws, canonical)
        ready = _wait_expression(
            ws,
            "(window.__INITIAL_STATE__ && window.__INITIAL_STATE__.jobDetail "
            "&& ((window.__INITIAL_STATE__.jobDetail.detailedPosition||{}).jobDesc || "
            "document.body.innerText.includes('职位描述')))",
            timeout=30,
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
    except TimeoutError:
        return "timeout", {}
    except Exception:
        return "unreachable", {}
    finally:
        try:
            ws.close()
        except Exception:
            pass


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
