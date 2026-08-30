# -*- coding: utf-8 -*-
"""智联列表域（031 B6 自 scripts/zhilian_cdp_raw.py 物理搬运）。

登录态探测（``check_login_state_tri``）、抓取前检查（``preflight``）、
列表抓取（``fetch_list``）、空结果 marker 确认，以及三者共用的风险信号
判定（``_risk_signal``）与岗位字段归一（``_normalize_job``）。

signal 字符串约定（与 webui/source.py ``_ZHILIAN_PREFLIGHT_SIGNAL_MAP`` 一致）：
  "ok" / "cdp_unavailable" / "login_required" / "verification" /
  "rate_limited" / "blocked" / "unreachable" / "timeout"
fetch_list: ("ok", jobs) / ("empty", [], evidence) / (signal, [])
"""

from __future__ import annotations

import json
import random
import time
import urllib.parse
from typing import Any

from scripts.zhilian.cdp import (
    DEFAULT_CDP_PORT,
    _BODY_TEXT_JS,
    _BLOCK_MARKERS,
    _LOCATION_HREF_JS,
    _LOGIN_MARKERS,
    _RATE_MARKERS,
    _VERIFY_MARKERS,
    _ZHILIAN_DETAIL_PATTERN,
    _ZHILIAN_LOGIN_PROBE_URL,
    _ZHILIAN_PASSPORT_HOST,
    _ZHILIAN_SEARCH_API,
    _ZHILIAN_SEARCH_PROBE_URL,
    _connect,
    _evaluate,
    _http_json,
    _navigate,
    _wait_expression,
)
from webui.logging_setup import get_logger

_logger = get_logger(__name__)


def _risk_signal(text: str, url: str = "") -> str | None:
    low = text.lower()
    if url.startswith("chrome-error://chromewebdata/") or url.startswith("data:text/html,chromewebdata"):
        return "unreachable"
    if any(m.lower() in low for m in _VERIFY_MARKERS):
        return "verification"
    if any(m.lower() in low for m in _RATE_MARKERS):
        return "rate_limited"
    if any(m.lower() in low for m in _BLOCK_MARKERS):
        return "blocked"
    if any(m.lower() in low for m in _LOGIN_MARKERS) or _ZHILIAN_PASSPORT_HOST in url:
        return "login_required"
    return None


def _normalize_job(item: dict) -> dict:
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
        url = str(_evaluate(ws, _LOCATION_HREF_JS) or "")
    except Exception:
        return "unknown"
    finally:
        try:
            ws.close()
        except Exception:
            _logger.debug("CDP 会话关闭失败（best-effort 忽略）", exc_info=True)

    low = body.lower()
    if any(m.lower() in low for m in _LOGIN_MARKERS) or _ZHILIAN_PASSPORT_HOST in url:
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
            body = str(_evaluate(ws, _BODY_TEXT_JS) or "")
            signal = _risk_signal(body, str(_evaluate(ws, _LOCATION_HREF_JS) or ""))
            return signal or "login_required"
        body = str(_evaluate(ws, _BODY_TEXT_JS) or "")
        signal = _risk_signal(body, str(_evaluate(ws, _LOCATION_HREF_JS) or ""))
        return {
            "verification": "verification",
            "rate_limited": "rate_limited",
            "blocked": "blocked",
        }.get(signal, "ok")
    except TimeoutError:
        return "timeout"
    except Exception:
        return "unreachable"
    finally:
        try:
            ws.close()
        except Exception:
            _logger.debug("CDP 会话关闭失败（best-effort 忽略）", exc_info=True)


def fetch_list(plan_item: dict, *, on_page_completed=None) -> tuple[str | None, list[dict], dict | None]:
    """抓取智联岗位列表；返回 (signal, jobs, empty_evidence)。

    ``on_page_completed`` 每完成一页回调一次结构化页级事件。
    """
    city = plan_item.get("city") or {}
    keyword = str(plan_item.get("keyword") or "").strip()
    city_code = str(city.get("platform_code") or "").strip()
    route_city_code = str(plan_item.get("route_city_code") or city.get("route_city_code") or "").strip()
    target_pages = int(plan_item.get("target_pages") or 1)
    start_page = max(1, int(plan_item.get("start_page") or 1))
    if not keyword or not city_code:
        return "invalid_output", [], None

    try:
        ws = _connect(DEFAULT_CDP_PORT if not plan_item.get("cdp_port") else int(plan_item["cdp_port"]))
    except Exception:
        return "cdp_unavailable", [], None

    try:
        merged: dict[str, dict] = {}
        for job in plan_item.get("existing_jobs") or []:
            if isinstance(job, dict) and job.get("platform_job_id"):
                merged[job["platform_job_id"]] = job
        api_city = _api_city_code(city_code)
        for page_index in range(start_page, max(1, target_pages) + 1):
            value = _evaluate(ws, _search_fetch_expression(keyword, api_city, page_index))
            if not isinstance(value, dict) or value.get("error"):
                return "invalid_output", [], None
            jobs = [_normalize_job(item) for item in value.get("jobs") or []]
            for job in jobs:
                jid = job.get("platform_job_id")
                if jid:
                    merged[jid] = job
            if on_page_completed is not None:
                on_page_completed({
                    "kind": "page_completed",
                    "combo_key": str(plan_item.get("combo_key") or "") or f"{keyword}|{city.get('name') or city_code}",
                    "keyword": keyword,
                    "city": city.get("name") or city_code,
                    "page": page_index,
                    "target_pages": target_pages,
                    "jobs_delta": len(jobs),
                    "jobs_count": len(merged),
                    "has_more": bool(value.get("jobs")) and not bool(value.get("isEndPage")),
                    "resume_page": page_index + 1,
                    "last_completed_page": page_index,
                    "jobs_snapshot": list(merged.values()),
                })
            if value.get("isEndPage") or not value.get("jobs"):
                break
            time.sleep(random.uniform(0.8, 1.6))
        if merged:
            return "ok", list(merged.values()), None
        # 真实空结果：必须能由当前页面明确空状态 marker 解释。
        if _has_empty_marker(ws, route_city_code or city_code, keyword):
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
            _logger.debug("CDP 会话关闭失败（best-effort 忽略）", exc_info=True)


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
