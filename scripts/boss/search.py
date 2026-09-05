# -*- coding: utf-8 -*-

"""列表抓取：URL 构建、诊断与 scrape_list（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

from datetime import datetime
import hashlib
import json
import os
import random
import time
from urllib.parse import urlencode
from scripts.boss.constants import API_JOB_LIST_PATH, CDP_ABOUT_BLANK, CDP_CMD_ADD_SCRIPT_ON_NEW_DOC, CDP_CMD_ATTACH_TARGET, CDP_CMD_CLOSE_TARGET, CDP_CMD_CREATE_TARGET, CDP_CMD_PAGE_NAVIGATE, DEFAULT_CDP_PORT, DEGREE_MAP, EXPERIENCE_MAP, EXTRACT_LIST_JS, FETCH_API_JS_TEMPLATE, HIDDEN_DEFINE_JS, INDUSTRY_MAP, MAX_CONSECUTIVE_EMPTY_PAGES, MSG_USER_CANCELLED_SCRAPE, SALARY_MAP, SCALE_MAP, STAGE_MAP, _UNLOCK_TIME_PATTERNS, _VISIBILITY_STATE_JS
from scripts.boss.exceptions import RiskControlError, SearchCancelled
from scripts.boss.output import default_output_path, flush_jobs, write_csv
from scripts.boss.rate_limit import begin_request_run
from scripts.boss_cdp_signals import RATE_LIMIT_KEYWORDS, RISK_CONTROL_KEYWORDS, VERDICT_CONFIRMED, VERDICT_RETRY, api_code_diagnosis, classify_list_diagnosis
from scripts.boss.constants import log
import sys as _sys
from scripts.boss import city_map
from scripts.boss import cdp_session
from scripts.boss import login
from scripts.boss import rate_limit
from scripts.boss import runtime

# ============================================================
# 构建搜索 URL
# ============================================================
def build_search_url(keyword, city_code, page, filters):
    params = {"query": keyword, "city": city_code, "page": page}
    for key, code in filters.items():
        if code:
            params[key] = code
    return f"https://www.zhipin.com/web/geek/job?{urlencode(params)}"


def should_use_dom_fallback(jobs, allow_dom_fallback=False):
    return allow_dom_fallback and not jobs


def parse_api_jobs_eval_value(value):
    if not value:
        return []
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []

    jobs = []
    for item in parsed:
        if not isinstance(item, dict) or item.get("error"):
            continue
        if item.get("title") or item.get("job_link"):
            jobs.append(item)
    return jobs


def diagnose_api_jobs_eval_value(value):
    """解析列表 API 返回，同时给出诊断信息和翻页元数据。

    返回 (jobs, diagnosis, meta)：
    - jobs：错误条目剔除后的职位列表。
    - diagnosis：None 表示正常；否则 dict(kind=..., ...)。
    - meta：{"hasMore": bool, "totalCount": int} 或 None（旧格式/错误时无）。
    """
    if not value:
        return [], {"kind": "empty_response"}, None
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return [], {"kind": "empty_response"}, None

    # 新格式：{"jobs": [...], "hasMore": bool, "totalCount": int}
    if isinstance(parsed, dict) and "jobs" in parsed:
        meta = {"hasMore": bool(parsed.get("hasMore")), "totalCount": int(parsed.get("totalCount") or 0)}
        raw_jobs = parsed["jobs"]
        if not isinstance(raw_jobs, list):
            return [], {"kind": "empty_response"}, meta
        jobs = [j for j in raw_jobs if isinstance(j, dict) and (j.get("title") or j.get("job_link"))]
        return jobs, None, meta

    # 旧格式 / 错误格式：[...]
    if not isinstance(parsed, list):
        return [], {"kind": "empty_response"}, None

    jobs = []
    diagnosis = None
    for item in parsed:
        if not isinstance(item, dict):
            continue
        error = item.get("error")
        if error:
            if diagnosis is None:
                if error == "api_code":
                    diagnosis = api_code_diagnosis(
                        item.get("code"), item.get("sample", "")
                    )
                elif isinstance(error, (int, float)):
                    diagnosis = {"kind": "http_error", "status": int(error)}
                else:
                    diagnosis = {
                        "kind": str(error),
                        "sample": str(item.get("sample", ""))[:300],
                    }
            continue
        if item.get("title") or item.get("job_link"):
            jobs.append(item)
    return jobs, diagnosis, None


def parse_unlock_time(text):
    """从风控/限流文本里提取完整日期时间形式的解封点。

    Returns:
        命中且时间在未来时返回 datetime，否则 None。

    支持格式（D6）：``YYYY-MM-DD HH:MM``、``YYYY年M月D日 HH:MM``、
    ``M月D日 HH:MM``（无年份按当年）。时间已过去的解析结果视为无效
    （风控文案里的历史时间不构成解封点），返回 None 由调用方走默认冷却。
    """
    if not text:
        return None
    now = datetime.now()
    for pattern in _UNLOCK_TIME_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            year = int(match.group("y")) if "y" in match.groupdict() else now.year
            candidate = datetime(
                year, int(match.group("m")), int(match.group("d")),
                int(match.group("H")), int(match.group("M")),
            )
        except (ValueError, TypeError):
            continue
        if candidate <= now:
            continue
        return candidate
    return None


def extract_block_hint(text, max_chars=160):
    """从风控/限流页文本里取一句最相关提示，避免整页落入错误信息。"""
    if not text:
        return ""
    lines = [line.strip() for line in text.replace("\r\n", "\n").splitlines() if line.strip()]
    keywords = RATE_LIMIT_KEYWORDS + RISK_CONTROL_KEYWORDS + (
        "分钟后", "小时", "重试", "解锁", "时间",
    )
    for line in lines:
        if any(keyword in line for keyword in keywords):
            return line[:max_chars]
    return text.replace("\r\n", " ").replace("\n", " ")[:max_chars]


def check_list_risk(diagnosis, *, page, consecutive_empty, scraped_count,
                    output_path, resume_page):
    """单页诊断的实锤判定：确认受限/登录失效/验证码即抛 RiskControlError。

    016-error-module-rework：
    - 单次拦截（403/429）与结构异常不再定罪，返回 None 由调用方原地重试；
    - "连续空页"不再作为风控定性理由（聚合刹车语义在 scrape_list 内处理）；
    - 抛错携带注册表错误码（RiskControlError.code）。
    """
    verdict, code, hint = classify_list_diagnosis(diagnosis, repeated=False)
    if verdict == VERDICT_CONFIRMED:
        return RiskControlError(
            hint, code=code,
            page=page, scraped_count=scraped_count,
            output_path=output_path, resume_page=resume_page)
    return None


# ============================================================
# 抓取列表
# ============================================================
def scrape_list(keyword, city_input, max_pages, filters, output_path,
                cdp_port=DEFAULT_CDP_PORT, fmt="json", allow_dom_fallback=False,
                start_page=1, *, cancel_event=None, on_poll=None,
                combo_key=None, on_page_completed=None, list_events_output=None):
    if not runtime._run_active:
        begin_request_run()
    city_name, city_code = city_map.resolve_city(city_input)
    cdp = cdp_session.CDPSession(cdp_port)
    all_jobs = []
    seen = set()
    if not output_path:
        output_path = default_output_path("jobs")
    start_page = max(1, int(start_page))
    last_completed_page = start_page - 1
    if start_page > 1 and os.path.exists(output_path):
        try:
            with open(output_path, encoding="utf-8") as handle:
                checkpoint = json.load(handle)
            if checkpoint.get("keyword") == keyword and isinstance(checkpoint.get("jobs"), list):
                all_jobs = list(checkpoint["jobs"])
                seen = {
                    job.get("job_link") or job.get("title", "")
                    for job in all_jobs if isinstance(job, dict)
                }
                last_completed_page = int(
                    checkpoint.get("last_completed_page", last_completed_page)
                )
        except (OSError, ValueError, TypeError):
            all_jobs = []
            seen = set()

    # 显示筛选条件
    filter_desc = []
    if filters.get("scale"):
        for k, v in SCALE_MAP.items():
            if v == filters["scale"]:
                filter_desc.append(f"规模={k}")
    if filters.get("stage"):
        for k, v in STAGE_MAP.items():
            if v == filters["stage"]:
                filter_desc.append(f"融资={k}")
    if filters.get("salary"):
        for k, v in SALARY_MAP.items():
            if v == filters["salary"]:
                filter_desc.append(f"薪资={k}")
    if filters.get("experience"):
        for k, v in EXPERIENCE_MAP.items():
            if v == filters["experience"]:
                filter_desc.append(f"经验={k}")
    if filters.get("degree"):
        for k, v in DEGREE_MAP.items():
            if v == filters["degree"]:
                filter_desc.append(f"学历={k}")
    if filters.get("industry"):
        for k, v in INDUSTRY_MAP.items():
            if v == filters["industry"]:
                filter_desc.append(f"行业={k}")

    print("=== BOSS直聘抓取 ===")
    print(f"关键词: {keyword} | 城市: {city_name} | 页数: {max_pages}")
    if filter_desc:
        print(f"筛选: {' | '.join(filter_desc)}")
    print()

    # 后台创建标签页，不抢占前台焦点，避免抓取时反复弹窗
    # （否则最小化的 Chrome 窗口会被新标签页唤起并放大到前台）
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

    def human_scroll(cdp, sid):
        """模拟人类滚动: 随机次数、随机距离、随机停顿，偶尔回滚一点"""
        total_scrolls = random.randint(3, 6)
        for _ in range(total_scrolls):
            # 大部分往下滚，偶尔往上回滚一点（模拟阅读回看）
            if random.random() < 0.15:
                delta = -random.randint(50, 150)
            else:
                delta = random.randint(150, 500)
            cdp.eval_js(f"window.scrollBy(0,{delta})", sid)
            # 滚动间隔随机：有时快速连续滚，有时停下来"看"
            if random.random() < 0.3:
                time.sleep(random.uniform(2.0, 4.0))
            else:
                time.sleep(random.uniform(0.5, 1.5))

    def human_mouse_jitter(cdp, sid):
        """偶尔移动鼠标位置，模拟人在页面上活动"""
        if random.random() < 0.4:
            x = random.randint(100, 800)
            y = random.randint(100, 600)
            cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": x, "y": y
            }, sid)

    consecutive_empty = 0
    events_handle = None
    if list_events_output:
        try:
            os.makedirs(os.path.dirname(list_events_output) or ".", exist_ok=True)
            events_handle = open(list_events_output, "w", encoding="utf-8")
        except OSError as exc:
            print(f"⚠️ 无法写入列表事件文件 ({list_events_output}): {exc}")
            events_handle = None

    scope_stop_reason = None
    scope_source_exhausted = None
    scope_explicit_empty = False
    returned_total_count = 0

    def _emit_page(page, delta, has_more, resume_page, *, snapshot=None,
                   returned_count=None, scope_complete=None, stop_reason=None):
        """每完成一页发出结构化事件，供 WebUI 页级持久化/进度使用。"""
        event = {
            "kind": "page_completed",
            "combo_key": combo_key or f"{keyword}|{city_name}",
            "keyword": keyword,
            "city": city_name,
            "page": int(page),
            "target_pages": int(max_pages),
            "jobs_delta": int(delta),
            "returned_count": int(delta if returned_count is None else returned_count),
            "new_unique_count": int(delta),
            "unit_unique_count": len(all_jobs),
            "jobs_count": len(all_jobs),
            "has_more": has_more if has_more in (True, False, None) else None,
            "resume_page": int(resume_page),
            "last_completed_page": int(last_completed_page),
            "scope_complete": scope_complete,
            "stop_reason": stop_reason,
        }
        if on_page_completed is not None:
            cb_event = dict(event)
            if snapshot is not None:
                cb_event["jobs_snapshot"] = list(snapshot)
            on_page_completed(cb_event)
        if events_handle is not None:
            events_handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            events_handle.flush()
    legit_empty_streak = 0  # API 正常应答但无职位的连续空页数（非风控信号）
    prev_has_more = None  # 上一页 API 返回的 hasMore（None=未知）
    try:
        for pg in range(start_page, max_pages + 1):
            # programmatic 取消/轮询检查点（与 scrape_details 逐岗位检查点同语义）；
            # CLI 不传 cancel_event/on_poll，行为与现状完全一致。
            if cancel_event is not None and cancel_event.is_set():
                raise SearchCancelled(MSG_USER_CANCELLED_SCRAPE)
            if on_poll is not None:
                on_poll()
            print(f"--- [{pg}/{max_pages} 页, {len(all_jobs)} 条已抓] ---")
            rate_limit.incr_request()

            # 每 4 页重新导航一次：BOSS 对同一页面上下文连续 API 调用约 4-5 次后
            # 返回 code:37（环境异常）。重新导航 + 滚动可重置 session 计数器，
            # 使 10 页 300 条全量抓取成为可能。
            if (pg - start_page) % 4 == 0:
                url = build_search_url(keyword, city_code, pg, filters)
                cdp.send(CDP_CMD_PAGE_NAVIGATE, {"url": url}, sid)
                time.sleep(random.uniform(6, 10))
                human_scroll(cdp, sid)
                human_mouse_jitter(cdp, sid)

            # 优先用 API 获取明文数据；单次可疑（拦截/结构异常/code:37）先原地
            # 重试本页一次（016：单次不定罪），重试后仍异常按分档处置。
            api_params = {
                "scene": "1",
                "query": keyword,
                "city": city_code,
                "page": pg,
                "pageSize": 30,
            }
            for k, v in filters.items():
                if v:
                    api_params[k] = v
            api_url = f"{API_JOB_LIST_PATH}?{urlencode(api_params)}"
            api_js = FETCH_API_JS_TEMPLATE.replace("__API_URL__", api_url)

            def _fetch_api_page():
                return diagnose_api_jobs_eval_value(cdp.eval_js(api_js, sid))

            def _renavigate_and_wait():
                url = build_search_url(keyword, city_code, pg, filters)
                cdp.send(CDP_CMD_PAGE_NAVIGATE, {"url": url}, sid)
                time.sleep(random.uniform(4, 8))
                human_scroll(cdp, sid)

            jobs, api_diagnosis, api_meta = _fetch_api_page()
            verdict, verdict_code, verdict_hint = classify_list_diagnosis(
                api_diagnosis, repeated=False)
            if verdict is not None:
                log.warning(
                    "风控/限流判定 verdict=%s code=%s hint=%s",
                    verdict, verdict_code, verdict_hint,
                )
            if verdict == VERDICT_RETRY:
                print(f"  ⚠️ {verdict_hint}；重新导航后重试本页一次…")
                _renavigate_and_wait()
                jobs, api_diagnosis, api_meta = _fetch_api_page()
                verdict, verdict_code, verdict_hint = classify_list_diagnosis(
                    api_diagnosis, repeated=True)
                if verdict is not None:
                    log.warning(
                        "风控/限流判定（重试后）verdict=%s code=%s hint=%s",
                        verdict, verdict_code, verdict_hint,
                    )
                if verdict == VERDICT_CONFIRMED:
                    verdict_hint = verdict_hint + "（重试后复现）"

            # 实锤（验证码特征/明确请求受限/重试后仍被拦截/登录失效）：
            # 存好已抓数据后立刻停止，不做 DOM 降级（被风控时降级同样会被拦）。
            if verdict == VERDICT_CONFIRMED:
                if output_path:
                    flush_jobs(output_path, {
                        "keyword": keyword,
                        "city": city_name,
                        "filters": filters,
                        "filter_desc": filter_desc,
                        "scraped_at": datetime.now().isoformat(),
                        "last_completed_page": last_completed_page,
                    }, all_jobs)
                _emit_page(
                    last_completed_page, 0, True, pg, snapshot=all_jobs,
                    returned_count=0)
                raise RiskControlError(
                    verdict_hint, code=verdict_code,
                    page=pg, scraped_count=len(all_jobs),
                    output_path=output_path, resume_page=pg)

            # DOM 提取的薪资可能是加密字体，默认禁用；只有显式允许时才降级。
            if should_use_dom_fallback(jobs, allow_dom_fallback):
                log.warning("⚠️ API 获取失败，回退到 DOM 提取（此方式已弃用，数据可能不完整）")
                if pg > 1:
                    url = build_search_url(keyword, city_code, pg, filters)
                    cdp.send(CDP_CMD_PAGE_NAVIGATE, {"url": url}, sid)
                    time.sleep(random.uniform(4, 8))
                    human_scroll(cdp, sid)
                val = cdp.eval_js(EXTRACT_LIST_JS, sid)
                if val:
                    try:
                        jobs = json.loads(val) if isinstance(val, str) else val
                    except ValueError:
                        print("  ⚠️ JSON 解析失败")
                        jobs = []
            elif not jobs:
                log.warning("⚠️ API 未返回职位数据，已跳过 DOM fallback；如需强制降级可加 --allow-dom-fallback")

            returned_total_count += len(jobs or [])

            if not jobs:
                consecutive_empty += 1
                # API 以合法结构应答但无职位（diagnosis=None）→ 正常空页。
                # 正常空页连续出现说明是搜索条件本身没结果，不是 IP 风控。
                if api_diagnosis is None:
                    legit_empty_streak += 1
                else:
                    legit_empty_streak = 0
                print(f"  ⚠️ 无数据（连续 {consecutive_empty} 页）")
                last_completed_page = pg
                if output_path:
                    flush_jobs(output_path, {
                        "keyword": keyword,
                        "city": city_name,
                        "filters": filters,
                        "filter_desc": filter_desc,
                        "scraped_at": datetime.now().isoformat(),
                        "last_completed_page": last_completed_page,
                    }, all_jobs)
                page_has_more = api_meta.get("hasMore") if isinstance(api_meta, dict) else prev_has_more
                _emit_page(pg, 0, page_has_more, pg + 1, snapshot=all_jobs,
                           returned_count=0)

                # --- 哨兵第二层：用 hasMore 精确判断空页原因 ---
                # 上一页 API 说"没有更多了" → 空页是正常的"翻完了"
                if prev_has_more is False:
                    print(f"  ℹ️ 上一页 hasMore=false，搜索结果已翻完，停止（已抓 {len(all_jobs)} 条）")
                    scope_stop_reason, scope_source_exhausted = "source_exhausted", True
                    break
                # 当页 API 正常应答且明确无更多数据 → 该组合确实没有职位，与风控无关。
                # 不提前 break 会在连续空页后被误判成风控，把没被封的账号报成 IP 限流。
                if (api_diagnosis is None and api_meta
                        and (api_meta.get("totalCount") == 0
                             or api_meta.get("hasMore") is False)):
                    print(f"  ℹ️ API 正常返回但该搜索组合无职位（totalCount/hasMore 明确），停止（已抓 {len(all_jobs)} 条）")
                    scope_stop_reason = "explicit_empty" if not all_jobs else "source_exhausted"
                    scope_source_exhausted = True
                    scope_explicit_empty = not all_jobs
                    break
                # 有数据 + 连续空页达阈值 → 大概率翻完了（兜底，防 hasMore 不准）
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY_PAGES and len(all_jobs) > 0:
                    print(f"  ℹ️ 连续 {consecutive_empty} 页无数据，搜索结果已翻完，停止翻页（已抓 {len(all_jobs)} 条）")
                    scope_stop_reason, scope_source_exhausted = "source_exhausted", True
                    break
                # 从头就空 + 连续达阈值：全部空页都是 API 正常应答 → 真实空结果；
                # 伴随结构异常/拦截的空页已按分档处置，这里只做"停止翻页"刹车，
                # 不再把连续空页定性成风控（016：聚合症状不定罪）。
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY_PAGES and len(all_jobs) == 0:
                    if legit_empty_streak >= consecutive_empty:
                        print(f"  ℹ️ 连续 {consecutive_empty} 页 API 正常应答但无职位，判定该搜索条件没有职位（非风控）")
                        scope_stop_reason, scope_source_exhausted = "explicit_empty", True
                        scope_explicit_empty = True
                        break
                    print(f"  ⚠️ 连续 {consecutive_empty} 页无法获取职位数据，原因无法确认，停止本组合")
                    raise RiskControlError(
                        f"连续 {consecutive_empty} 页无法获取职位数据，原因无法确认",
                        code="source_status_unclear",
                        page=pg, scraped_count=0,
                        output_path=output_path, resume_page=pg + 1)
                continue

            consecutive_empty = 0
            legit_empty_streak = 0
            prev_has_more = api_meta.get("hasMore") if api_meta else None
            new = 0
            for j in jobs:
                key = j.get('job_link') or j['title']
                j['job_id'] = hashlib.md5(key.encode()).hexdigest()[:16]
                if key in seen:
                    continue
                seen.add(key)
                all_jobs.append(j)
                new += 1
                salary = j.get('salary','?')
                scale = j.get('company_scale', '')
                extra = f" | {scale}" if scale else ""
                print(f"  ✓ {j['title']} | {salary} | {j.get('location','')} | {j.get('boss_name','')}{extra}")

            print(f"  本页 {len(jobs)} 条, 新增 {new}, 累计 {len(all_jobs)}")
            last_completed_page = pg

            # 每页抓完就写入文件，异常退出也能保留
            if output_path:
                flush_jobs(output_path, {
                    "keyword": keyword,
                    "city": city_name,
                    "filters": filters,
                    "filter_desc": filter_desc,
                    "scraped_at": datetime.now().isoformat(),
                    "last_completed_page": last_completed_page,
                }, all_jobs)
            page_has_more = api_meta.get("hasMore") if isinstance(api_meta, dict) else None
            _emit_page(pg, new, page_has_more, pg + 1, snapshot=all_jobs,
                       returned_count=len(jobs))

            if pg < max_pages:
                d = random.uniform(12, 22)
                print(f"  翻页等待 {d:.0f}s...\n")
                time.sleep(d)

        # Emit the terminal scope fact before the handle is closed below.
        if scope_stop_reason is None and last_completed_page >= max_pages:
            scope_stop_reason = "target_reached"
        if scope_stop_reason and events_handle is not None:
            events_handle.write(json.dumps({
                "kind": "scope_completed", "event_type": "scope_completed",
                "combo_key": combo_key or f"{keyword}|{city_name}", "keyword": keyword,
                "city": city_name, "scope_complete": True,
                "source_exhausted": scope_source_exhausted, "stop_reason": scope_stop_reason,
                "returned_total_count": returned_total_count, "unit_unique_count": len(all_jobs),
                "explicit_empty": bool(scope_explicit_empty),
            }, ensure_ascii=False) + "\n")
            if scope_explicit_empty:
                events_handle.write(json.dumps({
                    "kind": "explicit_empty", "event_type": "explicit_empty",
                    "combo_key": combo_key or f"{keyword}|{city_name}", "keyword": keyword,
                    "city": city_name, "scope_complete": True,
                    "source_exhausted": scope_source_exhausted, "stop_reason": "explicit_empty",
                    "explicit_empty": True, "fixture_version": "boss-list-v2", "marker": "explicit-empty",
                }, ensure_ascii=False) + "\n")
            events_handle.flush()

    except KeyboardInterrupt:
        print("\n中断")
        raise
    except RiskControlError:
        # 醒目报错统一由程序入口输出，这里不重复打印
        raise
    except SearchCancelled:
        # 用户取消：已写产物已在循环中 flush，直接传播，不打警告
        raise
    except RuntimeError as e:
        print(f"\n⚠️ {e}")
        raise
    finally:
        cdp.send(CDP_CMD_CLOSE_TARGET, {"targetId": tid})
        cdp.close()
        if events_handle is not None:
            try:
                events_handle.close()
            except OSError:
                pass

    print(f"\n{'='*60}")
    print(f"完成: {len(all_jobs)} 条")

    if all_jobs:
        # 最终写入（含时间戳更新）
        flush_jobs(output_path, {
            "keyword": keyword,
            "city": city_name,
            "filters": filters,
            "filter_desc": filter_desc,
            "scraped_at": datetime.now().isoformat(),
            "last_completed_page": last_completed_page,
        }, all_jobs)
        print(f"已保存: {output_path}")

        # CSV 导出
        if fmt == "csv":
            csv_path = output_path.rsplit(".", 1)[0] + ".csv"
            write_csv(csv_path, all_jobs)
    else:
        print("无数据")
        flush_jobs(output_path, {
            "keyword": keyword,
            "city": city_name,
            "filters": filters,
            "filter_desc": filter_desc,
            "scraped_at": datetime.now().isoformat(),
            "last_completed_page": last_completed_page,
        }, [])

    return {"keyword": keyword, "city": city_name, "total": len(all_jobs), "jobs": all_jobs}
