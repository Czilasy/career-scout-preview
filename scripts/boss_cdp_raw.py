#!/usr/bin/env python3
"""
BOSS直聘职位抓取 + 分析 — 纯 CDP raw protocol

功能:
  1. 搜索特定职位 (关键词 + 城市)
  2. 筛选公司规模、融资阶段、薪资范围、经验、学历、行业
  3. 抓取详情页 JD 并分析薪资范围和技能要求
  4. 输出结构化 JSON + CSV + 终端分析报告
  5. 环境检查、Chrome CDP 自动启动、登录状态检测

用法:
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --city 101020100 --pages 5
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --scale 305 --salary 406
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --analysis
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --detail
  uv run python3 scripts/boss_cdp_raw.py --check
  uv run python3 scripts/boss_cdp_raw.py --setup-chrome
  uv run python3 scripts/boss_cdp_raw.py --version
"""

__version__ = "2.2.0"

import json
import time
import random
import sys
import argparse
import os
import re
import hashlib
import csv
import glob
import platform
import subprocess
import shutil
import signal
import logging
import ntpath
from datetime import datetime
from collections import Counter
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
from urllib.request import Request, urlopen


def configure_stdio():
    """Keep console output usable when the active code page cannot encode emoji."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(errors="replace")


configure_stdio()

websocket = None
requests = None

# ============================================================
# 全局常量
# ============================================================

# CDP 默认端口（可通过 --cdp-port 覆盖）
DEFAULT_CDP_PORT = 9222

# API 基础路径（便于统一修改）
API_JOB_LIST_PATH = "/wapi/zpgeek/search/joblist.json"
HOT_CITY_URL = "https://www.zhipin.com/wapi/zpgeek/search/job/hot/city.json"
CITY_GROUP_URL = "https://www.zhipin.com/wapi/zpCommon/data/cityGroup.json"

# 请求频率保护
MAX_PAGES = 10          # 单次最大页数
MAX_API_REQUESTS = 500  # 单次最大 API 请求数

def get_default_chrome_path():
    system = platform.system()
    if system == "Darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if system == "Windows":
        candidates = []
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(ntpath.join(local_app_data, "Google", "Chrome", "Application", "chrome.exe"))
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(env_name)
            if base:
                candidates.append(ntpath.join(base, "Google", "Chrome", "Application", "chrome.exe"))
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return candidates[0] if candidates else "chrome.exe"

    candidates = [
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/snap/bin/chromium",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[0]


def get_default_profile_dir():
    system = platform.system()
    if system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/Google/Chrome")
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = ntpath.join(os.path.expanduser("~"), "AppData", "Local")
        return ntpath.join(base, "Google", "Chrome", "User Data")
    return os.path.expanduser("~/.config/google-chrome")


DEFAULT_CHROME_PATH = get_default_chrome_path()
DEFAULT_PROFILE_DIR = get_default_profile_dir()

DEFAULT_CDP_DATA_DIR = os.path.expanduser("~/.career-scout/chrome-profile")
DEFAULT_RESULT_DIR = os.path.expanduser("~/.career-scout/job-result")
DEFAULT_CITY_INPUT = "上海"
LOGIN_PROBE_QUERY = "Java"
LOGIN_PROBE_QUERIES = ("Java", "AI Agent", "产品经理")
LOGIN_PROBE_CITY = "101020100"
LOGIN_PROBE_CITIES = ("101020100", "101010100", "101280600")
LOGIN_PROBE_PAGE_SIZE = 10
DEFAULT_LOGIN_TIMEOUT = 300

# 全局请求计数器
_request_counter = 0
_live_city_maps_cache = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("boss_cdp")


def default_output_path(kind):
    filename = f"boss_{kind}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    return os.path.join(DEFAULT_RESULT_DIR, filename)


def require_runtime_dependencies(*names):
    global requests, websocket

    missing = []
    if "requests" in names and requests is None:
        try:
            import requests as requests_module
            requests = requests_module
        except ImportError:
            missing.append("requests")
    if "websocket" in names and websocket is None:
        try:
            import websocket as websocket_module
            websocket = websocket_module
        except ImportError:
            missing.append("websocket-client")
    if missing:
        print(f"缺少依赖: {' '.join(missing)}")
        print("请安装（任选其一）:")
        print(f"  uv add {' '.join(missing)}")
        print(f"  pip install {' '.join(missing)}")
        return False
    return True


# ============================================================
# 筛选参数映射
# Source snapshots:
# - 城市: https://www.zhipin.com/wapi/zpgeek/search/job/hot/city.json + cityGroup.json
# - 筛选项: https://www.zhipin.com/wapi/zpgeek/search/job/condition.json
# ============================================================
# 城市码表已外置到 data/city_codes.json（全量城市，覆盖一二三四五线），
# resolve_city 查询链：本地静态 → 运行时拉 BOSS 接口 → 原样兜底。
# 仓库内路径（开发态）与打包后路径（pip install）都在 _city_data_path() 里处理。
CITY_DATA_FILENAME = "city_codes.json"

_local_city_map_cache = None


def _city_data_path():
    """返回 data/city_codes.json 的路径，兼容仓库开发态与 pip 打包态。"""
    # 1. 仓库开发态：脚本在 scripts/，数据在 ../data/
    repo_data = os.path.join(os.path.dirname(__file__), "..", "data", CITY_DATA_FILENAME)
    if os.path.isfile(repo_data):
        return os.path.normpath(repo_data)
    # 2. 打包态：wheel force-include 到包根 data/，用 importlib.resources 兜底
    try:
        from importlib.resources import files  # py3.9+
        pkg_data = files(__package__ or "__main__").joinpath("..", "data", CITY_DATA_FILENAME) \
            if __package__ else None
    except Exception:
        pkg_data = None
    if pkg_data is not None and os.path.isfile(str(pkg_data)):
        return str(pkg_data)
    # 3. 找不到则返回开发态路径（让调用方决定降级）
    return os.path.normpath(repo_data)


def load_local_city_map():
    """读取本地 data/city_codes.json 静态全量城市码表。

    返回 (name_to_code, code_to_name) 两个字典；读取失败返回 ({}, {})。
    结果缓存，重复调用零开销。
    """
    global _local_city_map_cache
    if _local_city_map_cache is not None:
        return _local_city_map_cache
    name_to_code = {}
    try:
        path = _city_data_path()
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for name, code in raw.items():
                if name and code is not None:
                    name_to_code[str(name)] = str(code)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        log.debug(f"读取本地城市码表失败: {e}")
    code_to_name = {code: name for name, code in name_to_code.items()}
    _local_city_map_cache = name_to_code, code_to_name
    return _local_city_map_cache


# Backward-compatible aliases for the existing Web UI integration. The
# source of truth remains the external city_codes.json introduced upstream.
CITY_MAP, CITY_R = load_local_city_map()

SCALE_MAP = {
    "0-20人": "301", "20-99人": "302", "100-499人": "303",
    "500-999人": "304", "1000-9999人": "305", "10000人以上": "306",
}

STAGE_MAP = {
    "未融资": "801", "天使轮": "802", "A轮": "803", "B轮": "804",
    "C轮": "805", "D轮及以上": "806", "已上市": "807", "不需要融资": "808",
}

SALARY_MAP = {
    "不限": "0", "3K以下": "402", "3-5K": "403", "5-10K": "404",
    "10-20K": "405", "20-50K": "406", "50K以上": "407",
}

EXPERIENCE_MAP = {
    "不限": "0", "在校生": "108", "应届生": "102", "经验不限": "101",
    "1年以内": "103", "1-3年": "104",
    "3-5年": "105", "5-10年": "106", "10年以上": "107",
}

DEGREE_MAP = {
    "不限": "0", "初中及以下": "209", "中专/中技": "208", "高中": "206",
    "大专": "202", "本科": "203", "硕士": "204", "博士": "205",
}

INDUSTRY_MAP = {
    "互联网": "1001", "电子商务": "1002", "金融": "1003", "游戏": "1004",
    "企业服务": "1005", "教育培训": "1006", "社交网络": "1007",
    "医疗健康": "1008", "生活服务": "1009", "广告营销": "1010",
}


# ============================================================
# 全局请求计数器辅助
# ============================================================
def incr_request():
    """递增全局请求计数，达到上限时抛出异常"""
    global _request_counter
    _request_counter += 1
    if _request_counter > MAX_API_REQUESTS:
        raise RuntimeError(f"已达到单次最大请求数 {MAX_API_REQUESTS}，停止抓取")
    if _request_counter >= MAX_API_REQUESTS * 0.8:
        log.warning(f"⚠️ 请求次数接近上限: {_request_counter}/{MAX_API_REQUESTS}")


# ============================================================
# CDP 连接
# ============================================================
class CDPSession:
    def __init__(self, cdp_port=DEFAULT_CDP_PORT):
        if not require_runtime_dependencies("requests", "websocket"):
            raise RuntimeError("缺少 CDP 运行依赖")
        self.cdp_port = cdp_port
        try:
            resp = requests.get(f"http://127.0.0.1:{cdp_port}/json/version", timeout=10)
            ws_url = resp.json()["webSocketDebuggerUrl"]
            self.ws = websocket.create_connection(ws_url, timeout=60)
        except (requests.ConnectionError, requests.Timeout) as e:
            raise CDPUnavailableError(
                f"连不上调试浏览器（127.0.0.1:{cdp_port}）。\n"
                "请先运行 --setup-chrome 启动带调试端口的 Chrome，并登录 BOSS直聘；\n"
                "Chrome 关了调试端口就没了，需要重新启动。"
            ) from e
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            raise CDPUnavailableError(
                f"端口 {cdp_port} 上的服务不是 Chrome 调试端口（返回内容无法识别）。\n"
                "请用 --setup-chrome 启动专用 Chrome，不要占用该端口。"
            ) from e
        except websocket.WebSocketException as e:
            raise CDPUnavailableError(
                f"调试浏览器（127.0.0.1:{cdp_port}）的 WebSocket 连接失败。\n"
                "请关闭该 Chrome 后重新运行 --setup-chrome。"
            ) from e
        self.mid = 0

    def send(self, method, params=None, sid=None, timeout=30):
        """发送 CDP 命令并等待匹配的响应。

        Args:
            method: CDP 方法名
            params: 参数字典
            sid: Target session ID
            timeout: 等待响应的超时秒数，默认 30s

        Returns:
            CDP 响应字典

        Raises:
            TimeoutError: 超过 max_retries 仍未收到匹配响应
        """
        self.mid += 1
        msg = {"id": self.mid, "method": method, "params": params or {}}
        if sid:
            msg["sessionId"] = sid
        self.ws.send(json.dumps(msg))

        start_time = time.time()
        max_retries = 1000

        for attempt in range(max_retries):
            # 检查超时
            elapsed = time.time() - start_time
            if elapsed > timeout:
                raise TimeoutError(
                    f"CDP send({method}) 超时 ({timeout}s), "
                    f"已跳过 {attempt} 条不匹配消息"
                )

            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                raise TimeoutError(f"CDP WebSocket recv 超时, method={method}")
            except websocket.WebSocketException as exc:
                raise ConnectionError(f"CDP 连接异常：{exc}")

            try:
                r = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                log.debug(f"跳过非 JSON 消息: {raw[:100]}")
                continue

            if r.get("id") == self.mid:
                return r

            # 不匹配的消息：可能是事件通知，记录并跳过
            event_name = r.get("method", "unknown")
            log.debug(f"跳过不匹配消息 (id={r.get('id')}, event={event_name})")

        raise TimeoutError(
            f"CDP send({method}) 在 {max_retries} 条消息内未找到匹配响应"
        )

    def eval_js(self, js, sid):
        r = self.send("Runtime.evaluate", {"expression": js, "returnByValue": True}, sid)
        return r.get("result", {}).get("result", {}).get("value", None)

    def close(self):
        self.ws.close()


# ============================================================
# 通过页面内 XHR 调 API 获取列表数据（明文薪资）
# ============================================================
FETCH_API_JS_TEMPLATE = """
(function(){
    try {
        var xhr = new XMLHttpRequest();
        xhr.open('GET', '__API_URL__', false);
        xhr.send();
        if (xhr.status !== 200) return JSON.stringify([{error: xhr.status}]);
        var data;
        try {
            data = JSON.parse(xhr.responseText);
        } catch (parseErr) {
            // 状态 200 但 body 不是 JSON：大概率被风控/验证码页拦截，
            // 带上响应头部片段供调用方识别（不执行、仅诊断）。
            return JSON.stringify([{error: 'parse_failed', sample: (xhr.responseText || '').slice(0, 300)}]);
        }
        var jobs = (data.zpData || {}).jobList || [];
        if (!jobs.length && !(data.zpData || {}).jobList) {
            // 结构对不上：正常空页 jobList 是空数组；字段整个缺失说明
            // 返回的不是职位列表（可能是风控 JSON/登录跳转）。
            return JSON.stringify([{error: 'unexpected_shape', sample: JSON.stringify(data).slice(0, 300)}]);
        }
        var results = jobs.map(function(j) {
            return {
                title: j.jobName || '',
                salary: j.salaryDesc || '',
                salary_source: j.salaryDesc ? 'api' : 'api_empty',
                location: (j.cityName || '') + '\\u00b7' + (j.areaDistrict || '') + '\\u00b7' + (j.businessDistrict || ''),
                tags: [j.jobExperience || '', j.jobDegree || ''].filter(function(t){return t && t !== '\\u4e0d\\u9650';}).join(' | '),
                boss_name: j.brandName || '',
                boss_title: j.bossTitle || '',
                company_scale: j.brandScaleName || '',
                company_stage: j.brandStageName || '',
                company_industry: j.brandIndustry || '',
                job_labels: (j.jobLabels || []).join(' | '),
                skills: (j.skills || []).join(' | '),
                security_id: j.securityId || '',
                lid: j.lid || '',
                encrypt_job_id: j.encryptJobId || '',
                encrypt_boss_id: j.encryptBossId || '',
                encrypt_brand_id: j.encryptBrandId || '',
                job_link: j.encryptJobId ? 'https://www.zhipin.com/job_detail/' + j.encryptJobId + '.html' : '',
                company_link: j.encryptBrandId ? 'https://www.zhipin.com/gongsi/' + j.encryptBrandId + '.html' : '',
                welfare: (j.welfareList || []).join(' | ')
            };
        });
        return JSON.stringify({jobs: results, hasMore: !!(data.zpData||{}).hasMore, totalCount: (data.zpData||{}).totalCount || 0});
    } catch (e) {
        return JSON.stringify([{error: 'js_exception', sample: String(e).slice(0, 200)}]);
    }
})()
"""

# ============================================================
# DEPRECATED: DOM 提取作为 fallback（薪资可能是加密字体）
# 此方法已弃用，仅作为 API 方式失败时的最后降级手段。
# 新代码应优先使用 FETCH_API_JS_TEMPLATE 通过 API 获取数据。
# ============================================================
EXTRACT_LIST_JS = """
(function(){
    var results = [];
    var cards = document.querySelectorAll('li.job-card-box');
    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var nameEl = card.querySelector('.job-name');
        var salaryEl = card.querySelector('.job-salary');
        var locEl = card.querySelector('.company-location');
        var tagEls = card.querySelectorAll('.tag-list li');
        var bossEl = card.querySelector('.boss-name');
        var bossLink = card.querySelector('.boss-info');
        var tags = [];
        for (var j = 0; j < tagEls.length; j++) tags.push(tagEls[j].innerText.trim());
        var jobLink = nameEl ? (nameEl.getAttribute('href') || '') : '';
        if (jobLink && jobLink.charAt(0) === '/') jobLink = 'https://www.zhipin.com' + jobLink;
        var cLink = bossLink ? (bossLink.getAttribute('href') || '') : '';
        if (cLink && cLink.charAt(0) === '/') cLink = 'https://www.zhipin.com' + cLink;
        var t = nameEl ? nameEl.innerText.trim() : '';
        if (t) results.push({
            title: t,
            salary: salaryEl ? salaryEl.innerText.trim() : '',
            salary_source: 'dom_untrusted',
            location: locEl ? locEl.innerText.trim() : '',
            tags: tags.join(' | '),
            boss_name: bossEl ? bossEl.innerText.trim() : '',
            job_link: jobLink,
            company_link: cLink
        });
    }
    return JSON.stringify(results);
})()
"""

# ============================================================
# 详情页提取与校验
# ============================================================
DETAIL_LOGIN_MARKER = "登录查看完整内容"
DETAIL_DESCRIPTION_MARKER = "职位描述"
DETAIL_COMPETITIVENESS_MARKER = "竞争力分析"
DETAIL_SAFETY_MARKER = "BOSS 安全提示"
MIN_DETAIL_TEXT_LENGTH = 120


class DetailExtractionError(ValueError):
    """The rendered page does not contain a usable job description."""


class DetailLoginRequiredError(DetailExtractionError):
    """The detail page is truncated because the BOSS session is not logged in."""


class DetailVerificationRequiredError(DetailExtractionError):
    """The detail page shows a captcha/slider verification instead of JD content."""


class RiskControlError(RuntimeError):
    """抓取中途命中风控/验证码，立即停止（不静默跳过、不伪装完成）。

    携带诊断信息，供终端醒目报错：第几页挂的、为什么、已抓多少条存哪了、
    从哪页续抓。
    """

    def __init__(self, reason, *, page=None, scraped_count=0, output_path="",
                 resume_page=None):
        self.reason = reason
        self.page = page
        self.scraped_count = scraped_count
        self.output_path = output_path
        self.resume_page = resume_page
        super().__init__(reason)


class CDPUnavailableError(RuntimeError):
    """连不上调试浏览器（Chrome 没开 / 端口不通 / 端口被占用）。"""


EXTRACT_DETAIL_JS = """
(function(){
    var pageText = document.body ? document.body.innerText : '';
    var tags = [];
    var benefitWords = ['五险','补充医疗','定期体检','带薪年假','年终奖','零食','餐补',
        '节日福利','加班补助','股票期权','员工旅游','交通补助','通讯补贴','团建',
        '生日福利','免费班车','全勤奖','包吃','弹性工作','下午茶','租房补贴',
        '体检','健身','文化','充电假','司龄假','红包','能量补贴','社团','三薪',
        '绩效','底薪','保底','活动基金','学习基金','节日礼品','无障碍'];
    var noiseWords = ['BOSS直聘','boss','BOSS','来自BOSS直聘','金','金币'];
    function isBenefit(t) {
        if (t === '...' || t.length > 15 || t.length < 2) return true;
        for (var i = 0; i < benefitWords.length; i++) {
            if (t.includes(benefitWords[i])) return true;
        }
        for (var i = 0; i < noiseWords.length; i++) {
            if (t === noiseWords[i] || t.includes(noiseWords[i])) return true;
        }
        return false;
    }
    document.querySelectorAll('.job-tags .tag-all span, .job-keyword-list span').forEach(function(s){
        var t = s.innerText.trim();
        if(t && !isBenefit(t)) tags.push(t);
    });
    var jd = '';
    var sections = document.querySelectorAll('.job-detail-section, .job-sec');
    for (var i = 0; i < sections.length; i++) {
        var text = (sections[i].innerText || '').trim();
        if (text.indexOf('职位描述') !== -1 && text.length > jd.length) {
            jd = text;
        }
    }
    return JSON.stringify({
        jd: jd,
        page_text: pageText.substring(0, 12000),
        tags: tags,
        url: location.href
    });
})()
"""


def _normalize_detail_whitespace(text):
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").splitlines()]
    normalized = "\n".join(lines).strip()
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return re.sub(r"[ \t]{2,}", " ", normalized)


def _looks_like_navigation_page(text):
    return (
        DETAIL_DESCRIPTION_MARKER not in text
        and "无障碍专区" in text
        and "首页" in text
        and "职位" in text
        and "公司" in text
    )


def _recruiter_footer_start(lines):
    stripped_lines = [line.strip() for line in lines]
    end = len(stripped_lines)
    while end and not stripped_lines[end - 1]:
        end -= 1

    def card_start(card_end):
        while card_end and not stripped_lines[card_end - 1]:
            card_end -= 1
        if card_end < 4 or stripped_lines[card_end - 2] != "·":
            return None
        activity_or_name = stripped_lines[card_end - 4]
        has_activity_line = (
            activity_or_name == "在线" or activity_or_name.endswith("活跃")
        )
        start = card_end - 5 if has_activity_line else card_end - 4
        return start if start >= 0 else None

    for marker in (DETAIL_COMPETITIVENESS_MARKER, DETAIL_SAFETY_MARKER):
        try:
            marker_index = stripped_lines.index(marker)
        except ValueError:
            continue
        start = card_start(marker_index)
        if start is not None:
            return start
    return card_start(end)


def extract_job_description(extracted, min_length=MIN_DETAIL_TEXT_LENGTH):
    """Return validated JD text without BOSS page chrome.

    `page_text` is diagnostic input only. It is never persisted unless it has
    an explicit job-description section that passes all checks.
    """
    if not isinstance(extracted, dict):
        raise DetailExtractionError("detail extractor returned non-dict")

    raw_jd = str(extracted.get("jd") or "")
    page_text = str(extracted.get("page_text") or "")
    diagnostic_text = "\n".join((raw_jd, page_text))

    if DETAIL_LOGIN_MARKER in diagnostic_text:
        raise DetailLoginRequiredError(
            "detail page is truncated at the login wall; refresh the BOSS login session"
        )
    if _looks_like_navigation_page(diagnostic_text):
        raise DetailExtractionError("detail page rendered navigation chrome without a JD")
    if looks_like_risk_control(diagnostic_text):
        raise DetailVerificationRequiredError(
            "detail page shows captcha/verification instead of JD content"
        )

    text = raw_jd
    if not text and DETAIL_DESCRIPTION_MARKER in page_text:
        text = page_text
    if DETAIL_DESCRIPTION_MARKER in text:
        text = text.split(DETAIL_DESCRIPTION_MARKER, 1)[1]

    lines = text.replace("\r\n", "\n").splitlines()
    footer_start = _recruiter_footer_start(lines)
    if footer_start is not None:
        lines = lines[:footer_start]
    else:
        for index, line in enumerate(lines):
            if line.strip() == DETAIL_SAFETY_MARKER:
                lines = lines[:index]
                break

    jd = _normalize_detail_whitespace("\n".join(lines))
    if len(jd) < min_length:
        raise DetailExtractionError(
            f"job description too short after validation: {len(jd)} < {min_length}"
        )
    return jd


# ============================================================
# 解析城市参数（支持中文和代码）
# ============================================================
def fetch_boss_json(url, timeout=10):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_live_city_maps(timeout=10):
    global _live_city_maps_cache
    if _live_city_maps_cache is not None:
        return _live_city_maps_cache

    name_to_code = {}

    try:
        hot_city_data = fetch_boss_json(HOT_CITY_URL, timeout=timeout)
        for item in hot_city_data.get("zpData", {}).get("hotCityList", []):
            name = item.get("name")
            code = item.get("code")
            if name and code is not None:
                name_to_code[name] = str(code)

        city_group_data = fetch_boss_json(CITY_GROUP_URL, timeout=timeout)
        for group in city_group_data.get("zpData", {}).get("cityGroup", []):
            for item in group.get("cityList", []):
                name = item.get("name")
                code = item.get("code")
                if name and code is not None:
                    name_to_code.setdefault(name, str(code))
    except (OSError, json.JSONDecodeError, ValueError) as e:
        log.debug(f"加载 BOSS 城市映射失败，使用内置城市映射: {e}")

    code_to_name = {code: name for name, code in name_to_code.items()}
    _live_city_maps_cache = name_to_code, code_to_name
    return _live_city_maps_cache


def resolve_city(city_input):
    """把「中文城市名 / 城市码」解析为 (name, code)。

    查询链（逐级降级）:
      1. 本地静态码表 data/city_codes.json（全量、离线可用）
      2. 运行时拉 BOSS 接口 hot/city.json + cityGroup.json（自愈）
      3. 都查不到则原样返回（兼容用户直接传裸 city code）
    """
    if not city_input:
        return city_input, city_input

    # 1. 本地静态码表
    local_map, local_reverse = load_local_city_map()
    if city_input in local_map:
        return city_input, local_map[city_input]
    if city_input in local_reverse:
        return local_reverse[city_input], city_input

    # 2. 运行时拉 BOSS 接口
    live_map, live_reverse = load_live_city_maps()
    if city_input in live_map:
        return city_input, live_map[city_input]
    if city_input in live_reverse:
        return live_reverse[city_input], city_input

    # 3. 兜底：原样返回
    return city_input, city_input


def list_cities(keyword=None, use_live=True):
    """打印支持的城市列表。keyword 非空时只打印城市名含该关键词的城市。

    优先用运行时拉取的最新码表（use_live=True），拉取失败回退本地静态码表。
    """
    name_to_code = {}
    if use_live:
        live_map, _ = load_live_city_maps()
        name_to_code.update(live_map)
    if not name_to_code:
        local_map, _ = load_local_city_map()
        name_to_code.update(local_map)
    if not name_to_code:
        print("⚠️ 无法加载城市码表（本地静态文件缺失且网络拉取失败）")
        return

    items = sorted(name_to_code.items(), key=lambda kv: kv[0])
    if keyword:
        keyword = keyword.strip()
        items = [(n, c) for n, c in items if keyword in n]
        if not items:
            print(f"没有匹配「{keyword}」的城市")
            return
    print(f"共 {len(items)} 个城市（支持中文城市名或城市码）：")
    for name, code in items:
        print(f"  {name}\t{code}")



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
    for query in LOGIN_PROBE_QUERIES:
        for city_code in LOGIN_PROBE_CITIES:
            probe_url = build_login_probe_url(query, city_code)
            js = f"""
            (function(){{
                var xhr = new XMLHttpRequest();
                xhr.open('GET', '{probe_url}', false);
                xhr.send();
                return xhr.responseText;
            }})()
            """
            val = cdp.eval_js(js, sid)
            if not val:
                continue
            try:
                data = json.loads(val) if isinstance(val, str) else val
            except (json.JSONDecodeError, ValueError):
                continue
            if is_logged_in_search_response(data):
                return True
    return False


# ============================================================
# 登录状态检测
# ============================================================
def check_login_state(cdp_port=DEFAULT_CDP_PORT):
    """通过 CDP 检测 BOSS直聘登录状态。

    Returns:
        True 已登录, False 未登录
    """
    try:
        cdp = CDPSession(cdp_port)
        # background=True：后台创建标签页，不抢占前台焦点，避免检测登录时弹窗
        r = cdp.send("Target.createTarget", {"url": "about:blank", "background": True})
        tid = r["result"]["targetId"]
        r = cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
        sid = r["result"]["sessionId"]

        # background 标签页 document.hidden=true、visibilityState=hidden，
        # BOSS直聘据此判定为非真人浏览。导航前注入覆盖可见性属性为 visible。
        cdp.send("Page.addScriptToEvaluateOnNewDocument", {
            "source": (
                "Object.defineProperty(document, 'hidden', {get: () => false});"
                "Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});"
                "Object.defineProperty(document, 'webkitHidden', {get: () => false});"
                "Object.defineProperty(document, 'webkitVisibilityState', {get: () => 'visible'});"
            )
        }, sid)

        # 先导航到 BOSS直聘，确保 cookie 域名正确
        cdp.send("Page.navigate", {"url": "https://www.zhipin.com/"}, sid)
        time.sleep(4)

        logged_in = probe_login_state(cdp, sid)

        cdp.send("Target.closeTarget", {"targetId": tid})
        cdp.close()

        return logged_in
    except (requests.ConnectionError, requests.Timeout, KeyError,
            json.JSONDecodeError, websocket.WebSocketException) as e:
        log.error(f"登录状态检测失败: {e}")
        return False


def wait_for_login(cdp_port=DEFAULT_CDP_PORT, timeout=DEFAULT_LOGIN_TIMEOUT, interval=3):
    """Open BOSS login page and wait until plaintext salary is available."""
    cdp = CDPSession(cdp_port)
    r = cdp.send("Target.createTarget", {"url": "https://www.zhipin.com/web/user/"})
    tid = r["result"]["targetId"]
    r = cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
    sid = r["result"]["sessionId"]

    deadline = time.time() + timeout
    logged_in = False
    print(f"等待 BOSS 登录完成（最长 {timeout}s）", end="", flush=True)
    try:
        while time.time() <= deadline:
            if probe_login_state(cdp, sid):
                logged_in = True
                print("\n✅ 已检测到 BOSS 登录态，且接口返回明文薪资")
                return True
            print(".", end="", flush=True)
            time.sleep(interval)
        print("\n❌ 等待登录超时")
        print("   Chrome 会继续保持打开；登录后可重新运行 --check 或抓取命令")
        return False
    finally:
        if logged_in:
            cdp.send("Target.closeTarget", {"targetId": tid})
        cdp.close()


# ============================================================
# CSV 导出
# ============================================================
CSV_COLUMNS = [
    "job_id", "title", "salary", "salary_source", "location", "tags", "boss_name",
    "company_scale", "company_stage", "company_industry", "skills",
    "job_link", "welfare",
]

DETAIL_CSV_COLUMNS = [
    "job_id", "title", "company", "salary", "salary_source", "location",
    "tags_list", "job_link", "skill_tags", "jd",
]


def write_csv(csv_path, jobs):
    """将 jobs 列表写入 CSV 文件"""
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for j in jobs:
            # 确保每列都有值
            row = {col: j.get(col, "") for col in CSV_COLUMNS}
            writer.writerow(row)
    print(f"CSV 已保存: {csv_path}")


def write_detail_csv(csv_path, details):
    """将岗位详情列表写入 CSV 文件"""
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DETAIL_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for d in details:
            row = {col: d.get(col, "") for col in DETAIL_CSV_COLUMNS}
            if isinstance(row.get("skill_tags"), list):
                row["skill_tags"] = " | ".join(row["skill_tags"])
            writer.writerow(row)
    print(f"详情 CSV 已保存: {csv_path}")


# ============================================================
# 增量写入 JSON
# ============================================================
def write_json_atomic(path, payload):
    """Write a complete sibling file and atomically replace the destination."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path = f"{path}.tmp-{os.getpid()}-{time.time_ns()}"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def flush_jobs(path, meta, jobs):
    """每次有新数据就全量刷写（jobs 去重后），保证异常退出也能保留"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # 合并已有文件
    existing_jobs = []
    seen_ids = set()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f)
            existing_jobs = old.get("jobs", [])
            seen_ids = {j.get("job_id", "") for j in existing_jobs}
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    for j in jobs:
        if j.get("job_id") not in seen_ids:
            existing_jobs.append(j)
            seen_ids.add(j.get("job_id", ""))
    meta["total"] = len(existing_jobs)
    meta["jobs"] = existing_jobs
    write_json_atomic(path, meta)


# ============================================================
# 合并外部 JSON 文件
# ============================================================
def merge_jobs(external_path, new_jobs):
    """从外部 JSON 加载 jobs，与 new_jobs 按 job_id 合并去重。

    Args:
        external_path: 已有 JSON 文件路径
        new_jobs: 新抓取的 jobs 列表

    Returns:
        合并后的 jobs 列表
    """
    try:
        with open(external_path, "r", encoding="utf-8") as f:
            old_data = json.load(f)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        log.warning(f"无法加载合并文件 {external_path}: {e}")
        return new_jobs

    old_jobs = old_data.get("jobs", [])
    merged = list(old_jobs)
    seen_ids = {j.get("job_id", "") for j in merged}

    added = 0
    for j in new_jobs:
        if j.get("job_id") not in seen_ids:
            merged.append(j)
            seen_ids.add(j.get("job_id", ""))
            added += 1

    print(f"合并: 旧文件 {len(old_jobs)} 条 + 新抓取 {len(new_jobs)} 条 = {len(merged)} 条 (新增 {added})")
    return merged


def merge_details(external_path, new_details):
    """从外部 JSON 加载详情，与 new_details 按 job_id 合并去重。

    详情文件本身可能是列表结构（scrape_details 输出）或带 jobs/details 键的字典，
    这里都做兼容。优先保留 new_details 中的同名记录（更新覆盖旧值）。

    Args:
        external_path: 已有详情 JSON 文件路径
        new_details: 新抓取的详情列表（可为空）

    Returns:
        合并后的详情列表
    """
    if not external_path:
        return new_details
    try:
        with open(external_path, "r", encoding="utf-8") as f:
            old_data = json.load(f)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        log.warning(f"无法加载合并详情文件 {external_path}: {e}")
        return new_details

    if isinstance(old_data, list):
        old_details = old_data
    elif isinstance(old_data, dict):
        old_details = old_data.get("details") or old_data.get("jobs") or []
    else:
        old_details = []

    merged = merge_details_from_lists(old_details, new_details)
    print(f"合并详情: 旧文件 {len(old_details)} 条 + 新抓取 {len(new_details)} 条 = {len(merged)} 条")
    return merged


def merge_details_from_lists(old_details, new_details):
    """把两份详情列表按 job_id 合并去重，new_details 优先（同 id 用新覆盖旧）。"""
    by_id = {}
    for d in old_details:
        jid = d.get("job_id", "") if isinstance(d, dict) else ""
        if jid:
            by_id[jid] = d
    for d in new_details:
        jid = d.get("job_id", "") if isinstance(d, dict) else ""
        if jid:
            by_id[jid] = d
    return list(by_id.values())


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
    except (json.JSONDecodeError, ValueError, TypeError):
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


# 风控/验证码特征词：命中即实锤（在 API 返回的错误样本或页面文本里找）
RISK_CONTROL_KEYWORDS = (
    "安全验证", "滑动验证", "滑块", "访问受限", "异常流量", "操作频繁",
    "captcha", "CAPTCHA", "verify-sliding", "waf",
)

# 列表抓取：连续多少页拿不到数据就判定异常并停止（正常搜索极少连续空页）
MAX_CONSECUTIVE_EMPTY_PAGES = 3


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
    except (json.JSONDecodeError, ValueError, TypeError):
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
                if isinstance(error, (int, float)):
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


def looks_like_risk_control(text):
    """文本里是否含风控/验证码特征词。"""
    if not text:
        return False
    return any(keyword in text for keyword in RISK_CONTROL_KEYWORDS)


def check_list_risk(diagnosis, *, page, consecutive_empty, scraped_count,
                    output_path, resume_page):
    """组合式风控判定：已知特征命中=实锤；结构异常/连续空页=达阈值实锤。

    返回 RiskControlError 实例（应停止）或 None（继续）。
    """
    if diagnosis:
        kind = diagnosis.get("kind", "")
        sample = diagnosis.get("sample", "")
        if looks_like_risk_control(sample):
            return RiskControlError(
                f"返回内容里出现验证码/风控特征：{sample[:80]}",
                page=page, scraped_count=scraped_count,
                output_path=output_path, resume_page=resume_page)
        if kind == "http_error":
            status = diagnosis.get("status", 0)
            if status in (401, 403, 412, 418, 429):
                hint = "登录态失效" if status == 401 else "被风控拦截"
                return RiskControlError(
                    f"列表接口返回 HTTP {status}（{hint}）",
                    page=page, scraped_count=scraped_count,
                    output_path=output_path, resume_page=resume_page)
        if kind in ("parse_failed", "unexpected_shape", "js_exception"):
            # 结构对不上：可能是页面未就绪（可疑），连续出现才算实锤，
            # 由调用方按连续空页阈值统一处置（本次先按空页计数）。
            pass
    if consecutive_empty >= MAX_CONSECUTIVE_EMPTY_PAGES:
        return RiskControlError(
            f"连续 {consecutive_empty} 页拿不到职位数据，"
            "大概率被风控限制（也可能是该搜索条件确实没有职位）",
            page=page, scraped_count=scraped_count,
            output_path=output_path, resume_page=resume_page)
    return None


def build_detail_url(job):
    """Build the URL used for detail navigation without mutating job_link."""
    link = job.get("job_link", "")
    if not link:
        return ""

    parsed = urlparse(link)
    params = parse_qsl(parsed.query, keep_blank_values=True)
    existing_keys = {key for key, _ in params}
    for query_key, job_key in (("lid", "lid"), ("securityId", "security_id")):
        value = job.get(job_key) or job.get(query_key) or ""
        if value and query_key not in existing_keys:
            params.append((query_key, value))
            existing_keys.add(query_key)

    return urlunparse(parsed._replace(query=urlencode(params)))


def find_latest_detail_file(result_dir=DEFAULT_RESULT_DIR):
    pattern = os.path.join(result_dir, "boss_details_*.json")
    files = [path for path in glob.glob(pattern) if os.path.isfile(path)]
    if not files:
        return None
    return max(files, key=lambda path: (os.path.getmtime(path), path))


def detail_candidate_paths(input_path=None, detail_output=None, result_dir=DEFAULT_RESULT_DIR):
    candidates = []
    if detail_output:
        candidates.append(detail_output)
    if input_path:
        directory = os.path.dirname(input_path) or "."
        basename = os.path.basename(input_path)
        if basename.startswith("boss_jobs_"):
            candidates.append(os.path.join(directory, basename.replace("boss_jobs_", "boss_details_", 1)))
    latest = find_latest_detail_file(result_dir)
    if latest:
        candidates.append(latest)

    deduped = []
    seen = set()
    for path in candidates:
        normalized = os.path.abspath(os.path.expanduser(path))
        if normalized not in seen:
            deduped.append(path)
            seen.add(normalized)
    return deduped


def load_existing_details(input_path=None, detail_output=None, result_dir=DEFAULT_RESULT_DIR):
    for path in detail_candidate_paths(input_path, detail_output, result_dir):
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                details = json.load(f)
            if isinstance(details, list):
                print(f"加载详情文件: {path}")
                return details
        except (json.JSONDecodeError, OSError, ValueError) as e:
            log.warning(f"无法加载详情文件 {path}: {e}")
    return None


# ============================================================
# 抓取列表
# ============================================================
def scrape_list(keyword, city_input, max_pages, filters, output_path,
                cdp_port=DEFAULT_CDP_PORT, fmt="json", allow_dom_fallback=False,
                start_page=1):
    city_name, city_code = resolve_city(city_input)
    cdp = CDPSession(cdp_port)
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
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
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

    print(f"=== BOSS直聘抓取 ===")
    print(f"关键词: {keyword} | 城市: {city_name} | 页数: {max_pages}")
    if filter_desc:
        print(f"筛选: {' | '.join(filter_desc)}")
    print()

    # background=True：后台创建标签页，不抢占前台焦点，避免抓取时反复弹窗
    # （否则最小化的 Chrome 窗口会被新标签页唤起并放大到前台）
    r = cdp.send("Target.createTarget", {"url": "about:blank", "background": True})
    tid = r["result"]["targetId"]
    r = cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
    sid = r["result"]["sessionId"]

    # background 标签页 document.hidden=true、visibilityState=hidden，
    # BOSS直聘据此判定为非真人浏览。导航前注入覆盖可见性属性为 visible。
    cdp.send("Page.addScriptToEvaluateOnNewDocument", {
        "source": (
            "Object.defineProperty(document, 'hidden', {get: () => false});"
            "Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});"
            "Object.defineProperty(document, 'webkitHidden', {get: () => false});"
            "Object.defineProperty(document, 'webkitVisibilityState', {get: () => 'visible'});"
        )
    }, sid)

    def human_scroll(cdp, sid):
        """模拟人类滚动: 随机次数、随机距离、随机停顿，偶尔回滚一点"""
        total_scrolls = random.randint(3, 6)
        for i in range(total_scrolls):
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
    prev_has_more = None  # 上一页 API 返回的 hasMore（None=未知）
    try:
        for pg in range(start_page, max_pages + 1):
            print(f"--- [{pg}/{max_pages} 页, {len(all_jobs)} 条已抓] ---")
            incr_request()

            # 每 4 页重新导航一次：BOSS 对同一页面上下文连续 API 调用约 4-5 次后
            # 返回 code:37（环境异常）。重新导航 + 滚动可重置 session 计数器，
            # 使 10 页 300 条全量抓取成为可能。
            if (pg - start_page) % 4 == 0:
                url = build_search_url(keyword, city_code, pg, filters)
                cdp.send("Page.navigate", {"url": url}, sid)
                time.sleep(random.uniform(6, 10))
                human_scroll(cdp, sid)
                human_mouse_jitter(cdp, sid)

            # 优先用 API 获取明文数据
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
            val = cdp.eval_js(api_js, sid)

            jobs, api_diagnosis, api_meta = diagnose_api_jobs_eval_value(val)

            # 风控实锤（验证码特征/特定 HTTP 错误码）：存好已抓数据后立刻停止，
            # 不做 DOM 降级（被风控时降级同样会被拦）。
            risk = check_list_risk(
                api_diagnosis, page=pg, consecutive_empty=0,
                scraped_count=len(all_jobs), output_path=output_path,
                resume_page=pg)
            if risk is not None:
                if output_path:
                    flush_jobs(output_path, {
                        "keyword": keyword,
                        "city": city_name,
                        "filters": filters,
                        "filter_desc": filter_desc,
                        "scraped_at": datetime.now().isoformat(),
                        "last_completed_page": last_completed_page,
                    }, all_jobs)
                raise risk

            # DOM 提取的薪资可能是加密字体，默认禁用；只有显式允许时才降级。
            if should_use_dom_fallback(jobs, allow_dom_fallback):
                log.warning("⚠️ API 获取失败，回退到 DOM 提取（此方式已弃用，数据可能不完整）")
                if pg > 1:
                    url = build_search_url(keyword, city_code, pg, filters)
                    cdp.send("Page.navigate", {"url": url}, sid)
                    time.sleep(random.uniform(4, 8))
                    human_scroll(cdp, sid)
                val = cdp.eval_js(EXTRACT_LIST_JS, sid)
                if val:
                    try:
                        jobs = json.loads(val) if isinstance(val, str) else val
                    except (json.JSONDecodeError, ValueError):
                        print(f"  ⚠️ JSON 解析失败")
                        jobs = []
            elif not jobs:
                log.warning("⚠️ API 未返回职位数据，已跳过 DOM fallback；如需强制降级可加 --allow-dom-fallback")

            if not jobs:
                consecutive_empty += 1
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

                # --- 哨兵第二层：用 hasMore 精确判断空页原因 ---
                # 上一页 API 说"没有更多了" → 空页是正常的"翻完了"
                if prev_has_more is False:
                    print(f"  ℹ️ 上一页 hasMore=false，搜索结果已翻完，停止（已抓 {len(all_jobs)} 条）")
                    break
                # 有数据 + 连续空页达阈值 → 大概率翻完了（兜底，防 hasMore 不准）
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY_PAGES and len(all_jobs) > 0:
                    print(f"  ℹ️ 连续 {consecutive_empty} 页无数据，搜索结果已翻完，停止翻页（已抓 {len(all_jobs)} 条）")
                    break
                # 从头就空 + 连续达阈值 → 实锤风控
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY_PAGES and len(all_jobs) == 0:
                    risk = check_list_risk(
                        api_diagnosis, page=pg, consecutive_empty=consecutive_empty,
                        scraped_count=0, output_path=output_path,
                        resume_page=pg + 1)
                    if risk is not None:
                        raise risk
                continue

            consecutive_empty = 0
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

            if pg < max_pages:
                d = random.uniform(30, 38)
                print(f"  翻页等待 {d:.0f}s...\n")
                time.sleep(d)

    except KeyboardInterrupt:
        print("\n中断")
        raise
    except RiskControlError:
        # 醒目报错统一由程序入口输出，这里不重复打印
        raise
    except RuntimeError as e:
        print(f"\n⚠️ {e}")
        raise
    finally:
        cdp.send("Target.closeTarget", {"targetId": tid})
        cdp.close()

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


# ============================================================
# 抓取详情
# ============================================================
def build_detail_record(job, extracted):
    link = job.get("job_link", "")
    return {
        "job_id": job.get("job_id", ""),
        "title": job.get("title", ""),
        "company": job.get("boss_name", ""),
        "salary": job.get("salary", ""),
        "salary_source": job.get("salary_source", ""),
        "location": job.get("location", ""),
        "tags_list": job.get("tags", ""),
        "job_link": link,
        "link": link,
        "skill_tags": extracted.get("tags", []),
        "jd": extracted.get("jd", ""),
    }


# Readiness probe marker — tests and fakes detect the readiness probe by
# looking for this literal substring in the evaluated JS expression.
_READINESS_PROBE_MARKER = "__boss_readiness_probe__"

_READINESS_PROBE_JS = (
    "/* " + _READINESS_PROBE_MARKER + " */"
    "(function(){"
    "  if (document.readyState !== 'complete') return 'not_ready';"
    "  var body = document.body || {};"
    "  var text = body.innerText || '';"
    "  if (text.length < 50) return 'not_ready';"
    "  return 'ready';"
    "})()"
)


def _default_scrape_sleeper(seconds, label=None):
    """Default sleeper delegating to ``time.sleep``.

    The ``label`` argument is accepted so that tests and contract checks
    can distinguish kinds of waits (readiness, inter-job gap, etc.).
    """
    time.sleep(seconds)


def _wait_for_detail_readiness(ws, sid, *, sleeper, timeout_seconds, max_retries):
    """Poll page readiness with a bounded wait and at most one scroll retry.

    The readiness probe is a small JS expression that returns ``"ready"``
    when ``document.readyState`` is complete and the body has meaningful
    text. When the probe returns anything else (including ``None``), we
    sleep briefly (counted against ``timeout_seconds``) and, if retries
    remain, perform a single controlled scroll before re-probing.

    Returns when the page is ready or when the retry/budget is exhausted.
    Exhaustion is not fatal — extraction proceeds and the existing
    ``DetailLoginRequiredError`` / ``DetailExtractionError`` paths handle
    invalid pages.
    """
    remaining_budget = float(timeout_seconds)
    retries = 0
    while True:
        value = ws.eval_js(_READINESS_PROBE_JS, sid)
        if value == "ready":
            return
        if retries >= max_retries:
            return
        # Single controlled scroll, then a short wait counted against budget.
        ws.eval_js("window.scrollBy(0, 300)", sid)
        retries += 1
        wait = min(2.0, remaining_budget) if remaining_budget > 0 else 0.0
        if wait > 0:
            sleeper(wait, label="readiness_wait")
            remaining_budget -= wait


def _emit_detail_safe_event(event_callback, job, status, safe_code, started_at):
    """Emit one terminal safe event for a detail job.

    The payload deliberately excludes JD body, prompts, outputs and
    credential-shaped fields (encrypt_*_id, security_id). It carries only
    producer kind, terminal status, job identity (job_link), duration and
    a safe code.
    """
    if event_callback is None:
        return
    duration_ms = int((time.time() - started_at) * 1000)
    event = {
        "kind": "detail",
        "status": status,
        "job_id": job.get("job_link", ""),
        "duration_ms": duration_ms,
        "safe_code": safe_code,
    }
    event_callback(event)


def _scrape_one_detail(ws, job, global_idx, total, results, output_path, *,
                       sleeper, event_callback, readiness_timeout_seconds,
                       max_readiness_retries, inter_job_gap_range,
                       is_last_in_run, trailing_wait):
    """Scrape a single detail page within a reused CDP session.

    Emits exactly one terminal safe event via ``event_callback`` (when
    provided) and appends the built detail record to ``results`` on
    success. Returns ``True`` on success, ``False`` on isolated failure.
    Re-raises ``RuntimeError`` for login-wall truncation so the caller
    can stop the run before persisting truncated data.

    The inter-job gap is slept via ``sleeper(label="inter_job_gap")``
    for every non-terminal-excepted job (success or isolated failure)
    unless this is the last job in the run and ``trailing_wait`` is
    False. This preserves rate-limit protection between jobs even when
    one JD fails validation.
    """
    title = job.get("title", "")
    company = job.get("boss_name", "")
    print(f"[{global_idx + 1}/{total}] {company} - {title}")

    incr_request()

    # background=True：后台创建标签页，不抢占前台焦点，避免抓取时反复弹窗
    r = ws.send("Target.createTarget", {"url": "about:blank", "background": True})
    tid = r["result"]["targetId"]
    r = ws.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
    sid = r["result"]["sessionId"]

    # background 标签页 document.hidden=true、visibilityState=hidden，
    # BOSS直聘据此判定为非真人浏览而拒绝渲染/重定向到登录页。
    # 在导航前注入，覆盖可见性属性为 visible，骗过 visibility 反爬。
    ws.send("Page.addScriptToEvaluateOnNewDocument", {
        "source": (
            "Object.defineProperty(document, 'hidden', {get: () => false});"
            "Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});"
            "Object.defineProperty(document, 'webkitHidden', {get: () => false});"
            "Object.defineProperty(document, 'webkitVisibilityState', {get: () => 'visible'});"
        )
    }, sid)

    detail_url = build_detail_url(job)
    ws.send("Page.navigate", {"url": detail_url}, sid)
    print(f"  加载页面...")

    started_at = time.time()
    _wait_for_detail_readiness(
        ws, sid,
        sleeper=sleeper,
        timeout_seconds=readiness_timeout_seconds,
        max_retries=max_readiness_retries,
    )

    print(f"  提取 JD...")
    val = ws.eval_js(EXTRACT_DETAIL_JS, sid)
    try:
        d = json.loads(val) if isinstance(val, str) else {"jd": "", "tags": []}
    except (json.JSONDecodeError, ValueError, TypeError):
        d = {"jd": "", "tags": []}

    skip_gap = False
    try:
        try:
            d["jd"] = extract_job_description(d)
        except DetailLoginRequiredError as exc:
            ws.send("Target.closeTarget", {"targetId": tid})
            _emit_detail_safe_event(
                event_callback, job, "unavailable",
                "source_login_required", started_at,
            )
            # Run is stopping — do not sleep the inter-job gap.
            skip_gap = True
            raise RuntimeError(
                "BOSS detail login expired; stopped before writing truncated JD data"
            ) from exc
        except DetailExtractionError as exc:
            print(f"  跳过无效详情页: {exc}")
            ws.send("Target.closeTarget", {"targetId": tid})
            _emit_detail_safe_event(
                event_callback, job, "failed",
                "source_invalid_output", started_at,
            )
            return False

        detail = build_detail_record(job, d)
        results.append(detail)

        if d.get("tags"):
            print(f"  技能: {', '.join(d['tags'])}")
        print(f"  JD: {len(d.get('jd', ''))} 字 ({time.time() - started_at:.0f}s)")

        # 每抓完一个详情就写入，异常退出也能保留
        if output_path:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            write_json_atomic(output_path, results)

        ws.send("Target.closeTarget", {"targetId": tid})
        _emit_detail_safe_event(
            event_callback, job, "completed", "ok", started_at,
        )
        return True
    finally:
        # Inter-job gap is rate-limit protection between jobs. It applies
        # to successful and isolated-failure jobs alike. It is skipped
        # for the last job in the run (unless trailing_wait=True) and
        # for the login-wall case where the whole run is stopping.
        if not skip_gap and (not is_last_in_run or trailing_wait):
            gap = random.uniform(inter_job_gap_range[0], inter_job_gap_range[1])
            print(f"  等待 {gap:.1f}s 后抓下一个...\n")
            sleeper(gap, label="inter_job_gap")


def _scrape_detail_on_tab(ws, sid, job, global_idx, total, *,
                          sleeper, event_callback, readiness_timeout_seconds,
                          max_readiness_retries, results_lock, results,
                          output_path, tab_label):
    """在已 attach 的常驻 tab 上抓一个详情（复用 tab，不开/关 target）。

    spec 007 ⑧：与 ``_scrape_one_detail`` 的区别——
    - 不 createTarget/attach（tab 已由 ``_tab_worker`` 建池）
    - 不 closeTarget（抓完留给下一个 job 复用）
    - ``results.append`` + ``write_json_atomic`` + ``incr_request`` 在 ``results_lock`` 内
    - 日志带 ``tab_label`` 前缀，多路汇总进进度框不混乱

    返回 True=成功，False=isolated failure，"login_required"=登录墙（触发降级）。
    """
    title = job.get("title", "")
    company = job.get("boss_name", "")
    print(f"[{tab_label}] [{global_idx + 1}/{total}] {company} - {title}")

    # incr_request 操作全局 _request_counter，非线程安全，加锁
    with results_lock:
        incr_request()

    detail_url = build_detail_url(job)
    ws.send("Page.navigate", {"url": detail_url}, sid)
    print(f"[{tab_label}]   加载页面...")

    started_at = time.time()
    _wait_for_detail_readiness(
        ws, sid,
        sleeper=sleeper,
        timeout_seconds=readiness_timeout_seconds,
        max_retries=max_readiness_retries,
    )

    print(f"[{tab_label}]   提取 JD...")
    val = ws.eval_js(EXTRACT_DETAIL_JS, sid)
    try:
        d = json.loads(val) if isinstance(val, str) else {"jd": "", "tags": []}
    except (json.JSONDecodeError, ValueError, TypeError):
        d = {"jd": "", "tags": []}

    try:
        d["jd"] = extract_job_description(d)
    except DetailLoginRequiredError as exc:
        _emit_detail_safe_event(
            event_callback, job, "unavailable", "source_login_required", started_at,
        )
        print(f"[{tab_label}]   ⚠ 登录墙，触发降级")
        return "login_required"
    except DetailVerificationRequiredError as exc:
        _emit_detail_safe_event(
            event_callback, job, "failed", "source_verification_required", started_at,
        )
        print(f"[{tab_label}]   ⚠ 详情页验证码/滑块拦截")
        return False
    except DetailExtractionError as exc:
        print(f"[{tab_label}]   跳过无效详情页: {exc}")
        _emit_detail_safe_event(
            event_callback, job, "failed", "source_invalid_output", started_at,
        )
        return False

    detail = build_detail_record(job, d)
    # results.append + write_json_atomic 必须在同一锁内，避免并发写盘竞态
    with results_lock:
        results.append(detail)
        if output_path:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            write_json_atomic(output_path, results)

    if d.get("tags"):
        print(f"[{tab_label}]   技能: {', '.join(d['tags'])}")
    print(f"[{tab_label}]   JD: {len(d.get('jd', ''))} 字 ({time.time() - started_at:.0f}s)")

    _emit_detail_safe_event(event_callback, job, "completed", "ok", started_at)
    return True


def _reset_detail_session(ws, sid, sleeper, tab_label):
    """重置详情抓取的 session 计数器（防 code:37 环境异常）。

    与列表翻页相同的策略：导航回 BOSS 首页 + 等待 + 滚动，
    让 BOSS 的 session 级请求计数归零，避免连续自动化访问触发拦截。
    """
    print(f"[{tab_label}] ⟳ session 重置：导航回首页...")
    ws.send("Page.navigate", {"url": "https://www.zhipin.com/"}, sid)
    sleeper(random.uniform(5, 8), label="session_reset_wait")
    # 模拟真人滚动
    ws.eval_js("window.scrollBy(0, 300); void(0);", sid)
    sleeper(random.uniform(2, 4), label="session_reset_scroll")
    ws.eval_js("window.scrollBy(0, -200); void(0);", sid)
    sleeper(random.uniform(1, 2), label="session_reset_scroll2")
    print(f"[{tab_label}] ⟳ session 重置完成")


def _tab_worker(cdp_port, session_factory, work_queue, total, *,
                sleeper, event_callback, readiness_timeout_seconds,
                max_readiness_retries, inter_job_gap_range, stagger_range,
                tab_id, results_lock, results, output_path, degrade_event,
                trailing_wait, reset_every=3):
    """常驻 tab 工作线程：建池 → 错峰启动 → 循环领任务抓详情 → 补位节奏 → 关池。

    spec 007 ⑧：每个 tab 配一条独立工作线程 + 独立 CDP 会话（CDPSession 是
    WebSocket 连接，不能多线程共享）。线程安全通过 ``results_lock`` 保护共享
   状态（results/output_path/incr_request），``degrade_event`` 用于 login 墙降级。
    """
    tab_label = f"tab{tab_id + 1}"
    ws = session_factory(cdp_port)
    tid = None
    try:
        # 建池：createTarget + attach + visibility 注入（后台反爬）
        r = ws.send("Target.createTarget", {"url": "about:blank", "background": True})
        tid = r["result"]["targetId"]
        r = ws.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
        sid = r["result"]["sessionId"]
        ws.send("Page.addScriptToEvaluateOnNewDocument", {
            "source": (
                "Object.defineProperty(document, 'hidden', {get: () => false});"
                "Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});"
                "Object.defineProperty(document, 'webkitHidden', {get: () => false});"
                "Object.defineProperty(document, 'webkitVisibilityState', {get: () => 'visible'});"
            )
        }, sid)

        # 错峰启动：首批第 1 个立即导航，之后每个等随机 stagger_range 再开始
        if tab_id > 0:
            stagger = random.uniform(stagger_range[0], stagger_range[1])
            print(f"[{tab_label}] 错峰等待 {stagger:.1f}s 后开始")
            sleeper(stagger, label="stagger")

        jobs_done_on_tab = 0  # 本 tab 累计抓取数，用于触发 session 重置
        while not degrade_event.is_set():
            try:
                job, global_idx = work_queue.get_nowait()
            except Exception:
                break  # queue.Empty：队列空，退出
            is_last = global_idx == total - 1
            result = _scrape_detail_on_tab(
                ws, sid, job, global_idx, total,
                sleeper=sleeper, event_callback=event_callback,
                readiness_timeout_seconds=readiness_timeout_seconds,
                max_readiness_retries=max_readiness_retries,
                results_lock=results_lock, results=results,
                output_path=output_path, tab_label=tab_label,
            )
            if result == "login_required":
                # 登录墙：设置降级事件，其他线程看到后停止领新任务
                degrade_event.set()
                print(f"[{tab_label}] 登录墙触发降级，停止领新任务")
                break
            jobs_done_on_tab += 1
            # 每抓 reset_every 个详情重置一次 session（同列表翻页防 code:37 策略）：
            # 导航回 BOSS 首页 + 滚动，重置 BOSS 的 session 级请求计数器。
            if jobs_done_on_tab % reset_every == 0 and not is_last:
                _reset_detail_session(ws, sid, sleeper, tab_label)
            # 补位节奏：宁慢求稳，抓完空出来也等随机间隔再喂下一个
            if not is_last or trailing_wait:
                gap = random.uniform(inter_job_gap_range[0], inter_job_gap_range[1])
                print(f"[{tab_label}]   等待 {gap:.1f}s 后抓下一个...")
                sleeper(gap, label="inter_job_gap")
    finally:
        # 结束一次性关 tab + 关会话
        if tid is not None:
            try:
                ws.send("Target.closeTarget", {"targetId": tid})
            except Exception:
                pass
        ws.close()
        print(f"[{tab_label}] 已关闭")


def scrape_details(list_data, max_details=None, output_path=None,
                   cdp_port=DEFAULT_CDP_PORT, fmt="json", *,
                   batch_size=5, session_factory=None, sleeper=None,
                   event_callback=None, readiness_timeout_seconds=12,
                   max_readiness_retries=1, inter_job_gap_range=(8, 15),
                   trailing_wait=False,
                   enable_parallel=False, tab_pool_size=3,
                   stagger_range=(5, 10), reset_every=3):
    """抓取岗位详情页并返回结构化结果。

    Policy v2 keyword-only parameters (feature 005) +
    spec 007 ⑧ 并行化（常驻 tab 池 + 工作线程 + 错峰/补位/降级）：

    - ``batch_size``: 串行模式每批最多 5 个候选岗位（默认 5，上限 5）。
    - ``session_factory``: 返回 CDP 会话的可调用对象，默认 ``CDPSession``。
      测试可通过它注入 fake 会话；CLI 调用不传该参数时走真实 ``CDPSession``。
    - ``sleeper``: ``sleeper(seconds, label=None)`` 用于所有受控等待，
      默认委托 ``time.sleep``。``label`` 用于测试区分 readiness_wait /
      inter_job_gap 等不同等待类型。
    - ``event_callback``: 每个岗位处理完成时回调一次，收到只含安全字段
      (kind/status/job_id/duration_ms/safe_code) 的 terminal 事件，
      不含 JD 正文、凭据或 PII。
    - ``readiness_timeout_seconds``: readiness 总等待预算，默认 12 秒。
    - ``max_readiness_retries``: 首次未就绪时最多进行 N 次受控滚动重试，
      默认 1。
    - ``inter_job_gap_range``: 同批次岗位间等待秒数范围，默认 (8, 15)。
    - ``trailing_wait``: 运行最后一项之后是否再等待一次 gap，默认 False。
    - ``enable_parallel``: spec 007 ⑧，默认 False 走原串行路径（保持向后兼容
      与 005 合约）；True 走常驻 tab 池并行（webui 调用处显式传 True）。
    - ``tab_pool_size``: 常驻 tab 数，默认 3，上限 5。
    - ``stagger_range``: 错峰启动间隔范围秒，默认 (5, 10)。

    实现要点（见 specs/005-fast-resume-discovery/contracts/state-machine.md）：
    - 串行：每批最多 5 个候选；每批复用一个 CDP 会话，逐岗位开 target。
    - 并行（⑧）：N 个常驻 tab 各配一条工作线程 + 独立 CDP 会话；进队列前
      打乱 JD 列表；错峰启动 + 补位节奏；登录墙触发降级事件。
    - readiness-driven 提取：先探针，未就绪仅一次受控滚动重试。
    - 每个岗位发出且仅发出一个 terminal safe event。
    - 运行最后一项之后不再等待 gap（除非 trailing_wait=True）。
    """
    if not isinstance(batch_size, int) or batch_size < 1 or batch_size > 5:
        raise ValueError(
            f"batch_size must be an integer between 1 and 5, got {batch_size!r}"
        )
    if session_factory is None:
        session_factory = CDPSession
    if sleeper is None:
        sleeper = _default_scrape_sleeper
    if not inter_job_gap_range or len(inter_job_gap_range) != 2:
        raise ValueError("inter_job_gap_range must be a (min, max) pair")
    gap_lo, gap_hi = inter_job_gap_range
    if gap_lo < 0 or gap_hi < gap_lo:
        raise ValueError(
            f"inter_job_gap_range invalid: {inter_job_gap_range!r}"
        )
    if not isinstance(tab_pool_size, int) or tab_pool_size < 1 or tab_pool_size > 5:
        raise ValueError(
            f"tab_pool_size must be an integer between 1 and 5, got {tab_pool_size!r}"
        )
    if not stagger_range or len(stagger_range) != 2:
        raise ValueError("stagger_range must be a (min, max) pair")
    stg_lo, stg_hi = stagger_range
    if stg_lo < 0 or stg_hi < stg_lo:
        raise ValueError(f"stagger_range invalid: {stagger_range!r}")

    raw_jobs = list_data.get("jobs", [])
    if max_details:
        raw_jobs = raw_jobs[:max_details]
    if not output_path:
        output_path = default_output_path("details")

    # 按 job_link 去重，保持原始顺序
    seen_links = set()
    unique_jobs = []
    for job in raw_jobs:
        link = job.get("job_link", "")
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        unique_jobs.append(job)

    total = len(unique_jobs)
    results = []

    if enable_parallel and total > 0:
        # spec 007 ⑧：常驻 tab 池并行抓取
        print(f"\n=== 抓取岗位详情 ({total} 个, {tab_pool_size} tab 并行) ===\n")
        import threading
        import queue as _queue_mod
        results_lock = threading.Lock()
        degrade_event = threading.Event()
        work_queue = _queue_mod.Queue()
        # 随机顺序：进队列前打乱 JD 列表，请求顺序不可预测
        shuffled = unique_jobs[:]
        random.shuffle(shuffled)
        for idx, job in enumerate(shuffled):
            work_queue.put((job, idx))
        # 启动 N 个工作线程
        threads = []
        for tab_id in range(tab_pool_size):
            t = threading.Thread(
                target=_tab_worker,
                args=(cdp_port, session_factory, work_queue, total),
                kwargs={
                    "sleeper": sleeper,
                    "event_callback": event_callback,
                    "readiness_timeout_seconds": readiness_timeout_seconds,
                    "max_readiness_retries": max_readiness_retries,
                    "inter_job_gap_range": inter_job_gap_range,
                    "stagger_range": stagger_range,
                    "tab_id": tab_id,
                    "results_lock": results_lock,
                    "results": results,
                    "output_path": output_path,
                    "degrade_event": degrade_event,
                    "trailing_wait": trailing_wait,
                    "reset_every": reset_every,
                },
                name=f"detail-tab{tab_id + 1}",
                daemon=True,
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        if degrade_event.is_set():
            print("\n⚠ 检测到登录墙，已降级停止；已抓取结果保留。")
    else:
        # 串行路径（enable_parallel=False 或 total=0 时的降级/测试用）
        print(f"\n=== 抓取岗位详情 ({total} 个, 每批 {batch_size}, 串行) ===\n")
        for batch_start in range(0, total, batch_size):
            batch = unique_jobs[batch_start:batch_start + batch_size]
            batch_idx = batch_start // batch_size
            print(f"--- 批次 {batch_idx + 1} ({len(batch)} 个岗位) ---")

            ws = session_factory(cdp_port)
            try:
                for i, job in enumerate(batch):
                    global_idx = batch_start + i
                    is_last_in_run = global_idx == total - 1
                    _scrape_one_detail(
                        ws, job, global_idx, total, results, output_path,
                        sleeper=sleeper,
                        event_callback=event_callback,
                        readiness_timeout_seconds=readiness_timeout_seconds,
                        max_readiness_retries=max_readiness_retries,
                        inter_job_gap_range=inter_job_gap_range,
                        is_last_in_run=is_last_in_run,
                        trailing_wait=trailing_wait,
                    )
            finally:
                ws.close()

    # 最终保存（dirname 为空时回退到当前目录，与循环内/其它写文件处保持一致）
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    write_json_atomic(output_path, results)
    print(f"\n详情已保存: {output_path}")

    if fmt == "csv":
        csv_path = output_path.rsplit(".", 1)[0] + ".csv"
        write_detail_csv(csv_path, results)
    return results


# ============================================================
# 动态技术术语提取
# ============================================================
def extract_tech_terms_from_jds(details, search_keyword=""):
    """从 JD 文本中动态提取高频技术术语。

    策略：
    1. 保留一个小的基础术语列表用于匹配
    2. 对 JD 正文做分词频率分析，提取高频词
    3. 将搜索关键词拆分后加入

    Args:
        details: 详情列表，每个含 "jd" 字段
        search_keyword: 搜索关键词

    Returns:
        去重后的术语列表
    """
    # 基础技术术语（小列表，用于精确匹配）
    base_tech_terms = [
        "Java", "Spring", "Redis", "MySQL", "Kafka", "Flink", "Spark",
        "Go", "Python", "微服务", "分布式", "高并发",
        "AI", "LLM", "RAG", "Agent", "SQL", "Linux",
    ]

    # 从搜索关键词中提取词
    keyword_terms = []
    for word in re.split(r'[\s,，、]+', search_keyword):
        word = word.strip()
        if len(word) >= 2:
            keyword_terms.append(word)

    # 从 JD 文本中提取高频词
    word_freq = Counter()
    for d in details:
        jd_text = d.get("jd", "")
        if not jd_text:
            continue
        # 提取英文技术词（连续 2+ 字母的词）
        en_words = re.findall(r'\b[A-Za-z][A-Za-z0-9._-]+\b', jd_text)
        for w in en_words:
            if len(w) >= 2 and len(w) <= 30:
                word_freq[w] += 1
        # 提取中文技术词（简单：连续中文字符 2-6 个）
        cn_words = re.findall(r'[\u4e00-\u9fff]{2,6}', jd_text)
        # 过滤常见非技术中文词
        stop_words = {
            "任职", "要求", "岗位", "职责", "描述", "优先", "具有",
            "负责", "相关", "经验", "能力", "以上", "及其", "工作",
            "开发", "团队", "项目", "公司", "业务", "熟悉", "熟练",
            "了解", "掌握", "参与", "完成", "进行", "能够", "学历",
            "专业", "提供", "福利", "加入", "我们", "我们只", "是通过",
            "就是", "已经", "可以", "这个", "那个", "什么", "怎么",
            "欢迎", "期待", "为你", "为你提供",
        }
        for w in cn_words:
            if w not in stop_words:
                word_freq[w] += 1

    # 取频率最高的动态词（至少出现 2 次，取 top 60）
    dynamic_terms = [
        word for word, count in word_freq.most_common(60)
        if count >= 2
    ]

    # 合并去重：基础 + 关键词 + 动态提取
    all_terms = list(dict.fromkeys(
        base_tech_terms + keyword_terms + dynamic_terms
    ))
    return all_terms


# ============================================================
# 分析报告
# ============================================================
def analyze(list_data, details=None, search_keyword=""):
    jobs = list_data.get("jobs", [])
    print(f"\n{'='*60}")
    print(f"  分析报告: {list_data.get('keyword','')} @ {list_data.get('city','')}")
    print(f"  共 {len(jobs)} 条职位")
    print(f"{'='*60}")

    # 1. 薪资分析
    print(f"\n--- 薪资分布 ---")
    salary_ranges = Counter()
    for j in jobs:
        s = j.get("salary", "")
        if "K" in s:
            salary_ranges[s] += 1
        elif "元/天" in s:
            salary_ranges[s] += 1
        else:
            salary_ranges["未标注"] += 1
    for s, c in salary_ranges.most_common(15):
        bar = "█" * c
        print(f"  {s:<20} {c:>3}  {bar}")

    # 2. 经验要求
    print(f"\n--- 经验要求 ---")
    exp_count = Counter()
    for j in jobs:
        tags = j.get("tags", "")
        for t in tags.split(" | "):
            if "年" in t or "应届" in t or "在校" in t or "经验不限" in t:
                exp_count[t] += 1
    for e, c in exp_count.most_common():
        print(f"  {e:<15} {c}")

    # 3. 学历要求
    print(f"\n--- 学历要求 ---")
    edu_count = Counter()
    for j in jobs:
        tags = j.get("tags", "")
        for t in tags.split(" | "):
            if t in ["大专", "本科", "硕士", "博士", "学历不限"]:
                edu_count[t] += 1
    for e, c in edu_count.most_common():
        print(f"  {e:<10} {c}")

    # 4. 地区分布
    print(f"\n--- 地区分布 ---")
    loc_count = Counter()
    for j in jobs:
        loc = j.get("location", "")
        # Extract district
        parts = loc.split("·")
        if len(parts) >= 2:
            loc_count[parts[1]] += 1
        elif loc:
            loc_count[loc] += 1
    for l, c in loc_count.most_common(10):
        print(f"  {l:<15} {c}")

    # 5. 公司分布
    print(f"\n--- 高频公司 ---")
    company_count = Counter()
    for j in jobs:
        c = j.get("boss_name", "")
        if c:
            company_count[c] += 1
    for c, n in company_count.most_common(10):
        print(f"  {c:<25} {n} 个岗位")

    # 6. 详情页的技能标签（如有）
    body_freq = Counter()
    if details:
        print(f"\n--- 技能要求频次（来自 JD 标签）---")
        skill_freq = Counter()
        for d in details:
            for tag in d.get("skill_tags", []):
                skill_freq[tag] += 1
        for s, c in skill_freq.most_common(25):
            bar = "█" * c
            print(f"  {s:<20} {c:>3}/{len(details)}  {bar}")

        # 7. JD 正文关键词（动态提取）
        print(f"\n--- JD 正文高频技术词 ---")
        tech_terms = extract_tech_terms_from_jds(details, search_keyword)
        for d in details:
            jd_lower = d.get("jd", "").lower()
            for term in tech_terms:
                if term.lower() in jd_lower:
                    body_freq[term] += 1
        for t, c in body_freq.most_common(25):
            pct = c / len(details) * 100
            bar = "█" * c
            print(f"  {t:<20} {c:>3}/{len(details)} ({pct:.0f}%)  {bar}")

    # 8. 简历建议
    print(f"\n--- 简历建议 ---")
    if details and body_freq:
        noise_list = {'BOSS直聘', 'boss', 'BOSS', '来自BOSS直聘', '金', '金币'}
        top_skills = [s for s, _ in Counter(
            tag for d in details for tag in d.get("skill_tags", [])
        ).most_common(10)]
        # 如果有效标签太少或都是噪音，用 JD 正文关键词代替
        valid_skills = [s for s in top_skills if len(s) >= 2 and s not in noise_list]
        if len(valid_skills) < 3:
            top_skills = [t for t, _ in body_freq.most_common(10)]
        top_body = [t for t, _ in body_freq.most_common(8)] if body_freq else []
        print(f"  技能关键词: {', '.join(top_skills)}")
        print(f"  正文高频词: {', '.join(top_body)}")
        # Experience requirement
        if exp_count:
            top_exp = exp_count.most_common(1)[0][0]
            print(f"  经验要求主流: {top_exp}")
        if edu_count:
            top_edu = edu_count.most_common(1)[0][0]
            print(f"  学历要求主流: {top_edu}")
    else:
        print("  提示: 用 --detail 抓取 JD 详情后可获得更精准的简历建议")


def parse_jobs_eval_value(value):
    if not value:
        return []
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (json.JSONDecodeError, ValueError, TypeError):
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
    if not require_runtime_dependencies("requests", "websocket"):
        return 1

    try:
        cdp = CDPSession(cdp_port)
        city_name, city_code = resolve_city(DEFAULT_CITY_INPUT)
        search_url = build_search_url(LOGIN_PROBE_QUERY, city_code, 1, {})
        r = cdp.send("Target.createTarget", {"url": search_url})
        tid = r["result"]["targetId"]
        r = cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
        sid = r["result"]["sessionId"]

        print(f"打开 BOSS 搜索页: {LOGIN_PROBE_QUERY} @ {city_name}")
        time.sleep(4)
        api_url = f"{API_JOB_LIST_PATH}?{urlencode({'scene': '1', 'query': LOGIN_PROBE_QUERY, 'city': city_code, 'page': 1, 'pageSize': 5})}"
        api_js = FETCH_API_JS_TEMPLATE.replace("__API_URL__", api_url)
        jobs = parse_jobs_eval_value(cdp.eval_js(api_js, sid))
        cdp.send("Target.closeTarget", {"targetId": tid})
        cdp.close()

        if has_usable_smoke_jobs(jobs):
            sample = next(job for job in jobs if job.get("salary") and job.get("job_link"))
            print(f"✅ Smoke test 通过: {sample.get('title')} | {sample.get('salary')}")
            return 0
        print("❌ Smoke test 未拿到可用职位；请检查登录态或 BOSS API 返回")
        return 1
    except (requests.ConnectionError, requests.Timeout, KeyError,
            json.JSONDecodeError, websocket.WebSocketException, TimeoutError) as e:
        print(f"❌ Smoke test 失败: {e}")
        return 1


# ============================================================
# --check 环境检查
# ============================================================
def run_check(cdp_port=DEFAULT_CDP_PORT):
    """运行环境诊断检查"""
    print("=" * 50)
    print("  BOSS直聘 CDP 环境检查")
    print("=" * 50)
    print()

    all_pass = True

    # 检查 1: Python 依赖
    print("[1/3] Python 依赖...")
    deps_ok = require_runtime_dependencies("websocket", "requests")
    if requests is not None:
        print(f"  ✅ requests 可导入")
    if websocket is not None:
        print(f"  ✅ websocket 可导入")
    if deps_ok:
        print(f"  ✅ 依赖完整")
    else:
        all_pass = False

    # 检查 2: CDP 端口连通性
    print("[2/3] CDP 端口连通性...")
    if requests is None:
        print(f"  ❌ 跳过 — 缺少 requests")
        all_pass = False
    else:
        try:
            resp = requests.get(f"http://127.0.0.1:{cdp_port}/json/version", timeout=5)
            data = resp.json()
            browser = data.get("Browser", "未知")
            print(f"  ✅ 通过 — Chrome {browser}")
        except (requests.ConnectionError, requests.Timeout):
            print(f"  ❌ 失败 — 无法连接 127.0.0.1:{cdp_port}")
            print(f"     请先启动 Chrome CDP: python3 {__file__} --setup-chrome")
            all_pass = False
        except (json.JSONDecodeError, KeyError) as e:
            print(f"  ❌ 失败 — CDP 响应异常: {e}")
            all_pass = False

    # 检查 3: BOSS直聘登录状态
    print("[3/3] BOSS直聘登录状态...")
    if not deps_ok:
        print(f"  ❌ 跳过 — 缺少运行依赖")
        all_pass = False
    else:
        try:
            logged_in = check_login_state(cdp_port)
            if logged_in:
                print(f"  ✅ 已登录")
            else:
                print(f"  ❌ 未登录 — 请先在 Chrome 中登录 zhipin.com")
                all_pass = False
        except Exception as e:
            print(f"  ❌ 检测失败: {e}")
            all_pass = False

    print()
    if all_pass:
        print("✅ 所有检查通过，可以开始抓取")
    else:
        print("❌ 部分检查未通过，请修复后重试")
    print()

    return 0 if all_pass else 1


# ============================================================
# --setup-chrome 自动启动
# ============================================================
def is_boss_cookie_domain(domain):
    """Return True only for zhipin.com and its real subdomains."""
    normalized = str(domain or "").strip().lower().lstrip(".")
    return normalized == "zhipin.com" or normalized.endswith(".zhipin.com")


def normalize_boss_cookie(cookie):
    """Project a CDP cookie onto the minimal safe import contract."""
    if not isinstance(cookie, dict) or not is_boss_cookie_domain(cookie.get("domain")):
        return None
    name = cookie.get("name")
    value = cookie.get("value")
    if not isinstance(name, str) or not name or not isinstance(value, str):
        return None

    normalized = {
        "name": name,
        "value": value,
        "domain": str(cookie["domain"]),
        "path": str(cookie.get("path") or "/"),
    }
    for field in ("secure", "httpOnly"):
        if isinstance(cookie.get(field), bool):
            normalized[field] = cookie[field]
    if cookie.get("sameSite") in ("Strict", "Lax", "None"):
        normalized["sameSite"] = cookie["sameSite"]
    expires = cookie.get("expires")
    if isinstance(expires, (int, float)) and expires > 0:
        normalized["expires"] = expires
    return normalized


def _cdp_cookies(session):
    response = session.send("Storage.getCookies")
    if response.get("error"):
        raise RuntimeError("cdp_cookie_read_failed")
    cookies = response.get("result", {}).get("cookies", [])
    if not isinstance(cookies, list):
        raise RuntimeError("cdp_cookie_read_failed")
    return cookies


def _rollback_boss_cookies(target, imported, original):
    """Restore only the target's BOSS-cookie subset after a failed import."""
    identities = {}
    for cookie in imported + original:
        identities[(cookie["name"], cookie["domain"], cookie["path"])] = cookie
    try:
        for name, domain, path in identities:
            response = target.send("Network.deleteCookies", {
                "name": name,
                "domain": domain,
                "path": path,
            })
            if response.get("error"):
                return False
        if original:
            response = target.send("Storage.setCookies", {"cookies": original})
            if response.get("error"):
                return False
        return True
    except Exception:
        return False


def import_boss_session(source_cdp_port, target_cdp_port, authorized=False,
                        session_factory=CDPSession, login_checker=None,
                        target_profile_checker=None):
    """Import only an explicitly authorized BOSS session between CDP browsers.

    Cookie values remain in memory and are never included in the returned result.
    """
    if not authorized:
        return {
            "status": "blocked",
            "code": "authorization_required",
            "imported_count": 0,
        }
    if source_cdp_port == target_cdp_port:
        return {
            "status": "blocked",
            "code": "source_target_port_conflict",
            "imported_count": 0,
        }
    profile_checker = target_profile_checker or (
        lambda port: cdp_port_uses_profile(port, DEFAULT_CDP_DATA_DIR)
    )
    try:
        target_is_dedicated = bool(profile_checker(target_cdp_port))
    except Exception:
        target_is_dedicated = False
    if not target_is_dedicated:
        return {
            "status": "blocked",
            "code": "target_not_dedicated_profile",
            "imported_count": 0,
        }

    source = None
    target = None
    try:
        source = session_factory(source_cdp_port)
        target = session_factory(target_cdp_port)
        source_cookies = [
            normalized
            for cookie in _cdp_cookies(source)
            if (normalized := normalize_boss_cookie(cookie)) is not None
        ]
        target_cookies = [
            normalized
            for cookie in _cdp_cookies(target)
            if (normalized := normalize_boss_cookie(cookie)) is not None
        ]
        if not source_cookies:
            return {"status": "failed", "code": "no_boss_session", "imported_count": 0}
        try:
            response = target.send("Storage.setCookies", {"cookies": source_cookies})
        except Exception:
            if not _rollback_boss_cookies(target, source_cookies, target_cookies):
                return {"status": "failed", "code": "session_import_rollback_failed", "imported_count": 0}
            return {"status": "failed", "code": "session_write_failed", "imported_count": 0}
        if not isinstance(response, dict) or response.get("error"):
            if not _rollback_boss_cookies(target, source_cookies, target_cookies):
                return {"status": "failed", "code": "session_import_rollback_failed", "imported_count": 0}
            return {"status": "failed", "code": "session_write_failed", "imported_count": 0}
        checker = login_checker or check_login_state
        try:
            verified = bool(checker(target_cdp_port))
        except Exception:
            verified = False
        if not verified:
            if not _rollback_boss_cookies(target, source_cookies, target_cookies):
                return {"status": "failed", "code": "session_import_rollback_failed", "imported_count": 0}
            return {"status": "failed", "code": "session_import_unverified", "imported_count": 0}
        return {"status": "completed", "code": "ok", "imported_count": len(source_cookies)}
    except Exception:
        return {"status": "failed", "code": "session_import_failed", "imported_count": 0}
    finally:
        for session in (source, target):
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass


def run_import_boss_session(source_cdp_port, target_cdp_port, authorized=False):
    """CLI boundary for a safe, auditable BOSS-only session import."""
    print("=" * 50)
    print("  BOSS 专用会话导入")
    print("=" * 50)
    if not authorized:
        result = {"status": "blocked", "code": "authorization_required", "imported_count": 0}
    elif source_cdp_port is None:
        result = {"status": "blocked", "code": "source_cdp_port_required", "imported_count": 0}
    elif source_cdp_port == target_cdp_port:
        result = {"status": "blocked", "code": "source_target_port_conflict", "imported_count": 0}
    elif not cdp_port_uses_profile(target_cdp_port, DEFAULT_CDP_DATA_DIR):
        result = {"status": "blocked", "code": "target_not_dedicated_profile", "imported_count": 0}
    else:
        result = import_boss_session(
            source_cdp_port=source_cdp_port,
            target_cdp_port=target_cdp_port,
            authorized=authorized,
        )
    print(f"status={result['status']}")
    print(f"code={result['code']}")
    print(f"imported_count={result['imported_count']}")
    return 0 if result["status"] == "completed" else 1


def prepare_cdp_profile(copy_login_state=False, reset=False):
    """Prepare an isolated persistent Chrome profile for CDP."""
    if copy_login_state:
        raise ValueError("copy_login_state_deprecated")
    cdp_data_dir = DEFAULT_CDP_DATA_DIR
    cdp_default = os.path.join(cdp_data_dir, "Default")

    if reset and os.path.exists(cdp_data_dir):
        shutil.rmtree(cdp_data_dir)

    os.makedirs(cdp_default, exist_ok=True)

    return {
        "path": cdp_data_dir,
        "copied": 0,
        "reset": reset,
        "copy_login_state": False,
    }


def is_cdp_ready(cdp_port):
    # 用标准库 urllib 而不是模块级 requests —— requests 默认是 None，
    # 只有 require_runtime_dependencies 被调用后才会 import。
    # ensure_chrome_ready 在 preflight 之前调用 is_cdp_ready，此时 requests
    # 可能尚未初始化，用 requests 会导致永远返回 False（90s 超时）。
    try:
        resp = urlopen(f"http://127.0.0.1:{cdp_port}/json/version", timeout=2)
        return resp.status == 200
    except Exception:
        return False


def is_chrome_command(command):
    lower = (command or "").lower()
    return any(token in lower for token in (
        "google chrome",
        "google-chrome",
        "chromium",
        "chrome.exe",
    ))


def normalize_profile_path(path):
    clean = (path or "").strip("\"'")
    if platform.system() == "Windows":
        return ntpath.normcase(ntpath.normpath(clean))
    return os.path.realpath(os.path.expanduser(clean))


def extract_user_data_dir(command):
    match = re.search(r"--user-data-dir=(\"[^\"]+\"|'[^']+'|\S+)", command or "")
    if not match:
        return None
    return match.group(1).strip("\"'")


def iter_chrome_process_commands():
    """Return (pid, command line) tuples for Chrome-like browser processes."""
    if platform.system() == "Windows":
        ps_script = (
            "Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" | "
            "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
        )
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            return []
        if not r.stdout.strip():
            return []
        try:
            data = json.loads(r.stdout)
        except (json.JSONDecodeError, ValueError):
            return []
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []
        processes = []
        for item in data:
            command = item.get("CommandLine") or ""
            if not is_chrome_command(command):
                continue
            try:
                processes.append((int(item.get("ProcessId")), command))
            except (TypeError, ValueError):
                continue
        return processes

    try:
        r = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5)
    except Exception:
        return []

    processes = []
    for line in r.stdout.splitlines():
        if not is_chrome_command(line):
            continue
        try:
            pid_text, command = line.strip().split(None, 1)
            pid = int(pid_text)
        except ValueError:
            continue
        processes.append((pid, command))
    return processes


def chrome_pids_for_user_data_dir(user_data_dir):
    """Return Chrome PIDs using the given user-data-dir."""
    pids = []
    real_dir = normalize_profile_path(user_data_dir)
    for pid, command in iter_chrome_process_commands():
        if "--user-data-dir=" not in command:
            continue
        path = extract_user_data_dir(command)
        if path and normalize_profile_path(path) == real_dir:
            pids.append(pid)
    return pids


def chrome_user_data_dirs_for_cdp_port(cdp_port):
    """Return user-data-dir paths for Chrome processes using the given CDP port."""
    dirs = []
    port_arg = f"--remote-debugging-port={cdp_port}"
    for _pid, command in iter_chrome_process_commands():
        if port_arg not in command:
            continue
        path = extract_user_data_dir(command)
        if path:
            dirs.append(path)
    return dirs


def cdp_port_uses_profile(cdp_port, cdp_data_dir):
    expected = normalize_profile_path(cdp_data_dir)
    return any(normalize_profile_path(path) == expected for path in chrome_user_data_dirs_for_cdp_port(cdp_port))


def terminate_process(pid, force=False):
    if platform.system() == "Windows":
        cmd = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            cmd.append("/F")
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        return
    os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)


def stop_cdp_chrome(cdp_data_dir):
    """Stop only Chrome processes that use the scraper's isolated profile."""
    pids = chrome_pids_for_user_data_dir(cdp_data_dir)
    if not pids:
        return 0

    for pid in pids:
        try:
            terminate_process(pid, force=False)
        except ProcessLookupError:
            pass
    for _ in range(10):
        time.sleep(0.5)
        if not chrome_pids_for_user_data_dir(cdp_data_dir):
            return len(pids)

    for pid in chrome_pids_for_user_data_dir(cdp_data_dir):
        try:
            terminate_process(pid, force=True)
        except ProcessLookupError:
            pass
    time.sleep(0.5)
    return len(pids)


def close_cdp_chrome(cdp_port=DEFAULT_CDP_PORT, cdp_data_dir=DEFAULT_CDP_DATA_DIR,
                     profile_checker=None, session_factory=CDPSession,
                     process_stopper=None, ready_checker=None, sleeper=None):
    """Close only a Chrome CDP instance using the expected dedicated profile."""
    checker = profile_checker or cdp_port_uses_profile
    if not checker(cdp_port, cdp_data_dir):
        return False

    is_ready = ready_checker or is_cdp_ready
    stop_processes = process_stopper or stop_cdp_chrome
    pause = sleeper or time.sleep
    session = None
    try:
        session = session_factory(cdp_port)
        try:
            session.send("Browser.close", timeout=5)
        except Exception:
            # Chrome may close the WebSocket before acknowledging Browser.close.
            pass
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    for _ in range(10):
        if not is_ready(cdp_port):
            return True
        pause(0.2)

    # Fallback remains restricted to the same dedicated user-data-dir.
    stop_processes(cdp_data_dir)
    return not is_ready(cdp_port)


def wait_for_cdp(cdp_port, timeout=30):
    print("等待 CDP 可用", end="")
    for _ in range(timeout):
        time.sleep(1)
        print(".", end="", flush=True)
        if is_cdp_ready(cdp_port):
            print(f"\n✅ CDP 已就绪 (端口 {cdp_port})")
            return True
    print(f"\n❌ 等待超时 ({timeout}s)，CDP 未就绪")
    print(f"   请手动检查 Chrome 是否启动，端口 {cdp_port} 是否开放")
    return False


def launch_chrome(cmd):
    """Launch Chrome detached, with stderr captured to a log file for diagnostics.

    Returns the ``subprocess.Popen`` handle so callers can check ``poll()``
    to detect early exit instead of waiting the full CDP timeout.
    """
    # 把 Chrome 的 stderr 写到日志文件，启动失败时能直接看到原因
    # （Chrome 是 GUI 程序，但启动失败信息会进 stderr）
    log_dir = os.path.dirname(DEFAULT_CDP_DATA_DIR)
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass
    log_path = os.path.join(DEFAULT_CDP_DATA_DIR, "chrome_stderr.log")
    try:
        stderr_fh = open(log_path, "ab", buffering=0)
    except Exception:
        stderr_fh = subprocess.DEVNULL
    kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": stderr_fh,
    }
    if platform.system() == "Windows":
        # 注意：不要加 DETACHED_PROCESS —— 实测在 Windows 上会导致 Chrome 启动后
        # 立即退出（exit code=21），9222 端口从未开放。只保留 CREATE_NEW_PROCESS_GROUP
        # 让 Chrome 在独立进程组里运行即可，Flask 退出也不会立即带走它。
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if creationflags:
            kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def run_setup_chrome(cdp_port=DEFAULT_CDP_PORT, copy_login_state=False,
                     reset_profile=False, wait_login=True,
                     login_timeout=DEFAULT_LOGIN_TIMEOUT):
    """自动配置并启动 Chrome CDP 模式"""
    if copy_login_state:
        print("❌ --copy-login-state 已停用：不会复制 Chrome 数据库。")
        print("   请改用 --import-boss-session + --confirm-session-import。")
        return 1
    if not require_runtime_dependencies("requests"):
        return 1

    print("=" * 50)
    print("  设置 Chrome CDP 调试模式")
    print("=" * 50)
    print()

    profile = prepare_cdp_profile(copy_login_state=copy_login_state, reset=reset_profile)
    cdp_data_dir = profile["path"]
    print(f"✅ 使用独立 Chrome profile: {cdp_data_dir}")
    if reset_profile:
        print("   已按 --reset-chrome-profile 重建 profile")
    print("   默认、首次启动、重复启动都不复制主 Chrome Cookie；首次使用请在此专用 Chrome 中登录 zhipin.com")

    if is_cdp_ready(cdp_port):
        if cdp_port_uses_profile(cdp_port, cdp_data_dir):
            print(f"\n✅ CDP 已就绪 (端口 {cdp_port})")
            if wait_login:
                return 0 if wait_for_login(cdp_port, timeout=login_timeout) else 1
            return 0
        print(f"\n❌ 端口 {cdp_port} 已被其他 Chrome CDP profile 占用")
        print(f"   请关闭旧 CDP Chrome，或改用 --cdp-port 指定其他端口")
        return 1

    stopped = stop_cdp_chrome(cdp_data_dir)
    if stopped:
        print(f"\n已关闭 {stopped} 个旧的 BOSS CDP Chrome 进程")

    print(f"\n启动 Chrome (CDP 端口: {cdp_port})...")
    cmd = [
        DEFAULT_CHROME_PATH,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={cdp_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-allow-origins=*",
    ]
    launch_chrome(cmd)

    if not wait_for_cdp(cdp_port):
        return 1

    print()
    print("Chrome 已启动。请在这个专用浏览器中登录 zhipin.com。")
    if wait_login:
        print()
        if not wait_for_login(cdp_port, timeout=login_timeout):
            return 1
    print()
    print(f"示例:")
    print(f"  uv run python3 scripts/boss_cdp_raw.py --keyword \"AI Agent\" --city 上海 --pages 3")
    print(f"  uv run python3 scripts/boss_cdp_raw.py --check")
    print(f"  uv run python3 scripts/boss_cdp_raw.py --stop-chrome   # 抓完关闭专用 Chrome")
    print()
    return 0


def run_stop_chrome():
    """关闭 BOSS 专用 CDP Chrome（按隔离 user-data-dir 精准匹配，不碰主 Chrome）。"""
    if not require_runtime_dependencies("requests"):
        return 1

    print("=" * 50)
    print("  关闭 BOSS 专用 CDP Chrome")
    print("=" * 50)
    print()

    # 只定位 scraper 专用 profile 目录，不复制、不重置
    profile = prepare_cdp_profile(copy_login_state=False, reset=False)
    cdp_data_dir = profile["path"]

    stopped = stop_cdp_chrome(cdp_data_dir)
    if stopped:
        print(f"\n✅ 已关闭 {stopped} 个 BOSS 专用 Chrome 进程 (profile: {cdp_data_dir})")
    else:
        print(f"\nℹ️  没有找到运行中的 BOSS 专用 Chrome 进程 (profile: {cdp_data_dir})")
    print()
    print("提示：仅关闭 scraper 隔离 profile 的 Chrome，不影响你的主 Chrome。")
    print()
    return 0


# ============================================================
# main
# ============================================================
def main():
    p = argparse.ArgumentParser(
        description=f"BOSS直聘抓取 + 分析 (CDP Raw) v{__version__}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
筛选参数示例:
  --scale 305          公司规模 (301=0-20人 302=20-99 303=100-499 304=500-999 305=1000-9999 306=10000+)
  --stage 807          融资阶段 (801=未融资 ... 807=已上市 808=不需要融资)
  --salary 406         薪资范围 (402=3K以下 403=3-5K 404=5-10K 405=10-20K 406=20-50K 407=50K+)
  --experience 105     经验要求 (108=在校生 102=应届生 101=经验不限 103=1年以内 104=1-3年 105=3-5年 106=5-10年 107=10年+)
  --degree 203         学历要求 (209=初中及以下 208=中专/中技 206=高中 202=大专 203=本科 204=硕士 205=博士)
  --industry 1001      行业 (1001=互联网 1002=电商 1003=金融 ...)

城市支持中文: --city 上海  或代码: --city 101020100

示例:
  # 基础搜索
  %(prog)s --keyword "Java 风控" --city 上海 --pages 5

  # 筛选大公司 + 高薪
  %(prog)s --keyword "Java 风控" --scale 305 --salary 406

  # 抓列表 + 详情 + 分析报告
  %(prog)s --keyword "Java 风控" --pages 3 --detail --analysis

  # 只分析已有数据
  %(prog)s --input ~/.career-scout/job-result/boss_jobs_20260609_1200.json --analysis --no-detail

  # 导出 CSV
  %(prog)s --keyword "Java 风控" --pages 3 --format csv

  # 合并旧数据
  %(prog)s --keyword "Java 风控" --pages 3 --merge old_data.json

  # 环境检查
  %(prog)s --check

  # 浏览器/API smoke test
  %(prog)s --smoke-test

  # 启动 Chrome CDP
  %(prog)s --setup-chrome
        """)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--keyword", default="AI Agent", help="搜索关键词")
    p.add_argument("--city", default=DEFAULT_CITY_INPUT, help=f"城市 (中文名或代码，默认 {DEFAULT_CITY_INPUT})")
    p.add_argument("--pages", type=int, default=3, help=f"抓取页数 (最大 {MAX_PAGES})")
    p.add_argument("--start-page", type=int, default=1,
                   help="从指定页继续抓取（与已有 --output 断点配合）")
    p.add_argument("--output", default=None, help="列表数据输出路径")
    p.add_argument("--detail-output", default=None, help="详情数据输出路径")
    p.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT,
                   help=f"CDP 调试端口 (默认 {DEFAULT_CDP_PORT})")
    p.add_argument("--format", default="json", choices=["json", "csv"],
                   help="输出格式 (默认 json)")
    p.add_argument("--merge", default=None,
                   help="合并已有 JSON 文件 (按 job_id 去重)")

    # 筛选参数
    p.add_argument("--scale", default=None, help="公司规模代码")
    p.add_argument("--stage", default=None, help="融资阶段代码")
    p.add_argument("--salary", default=None, help="薪资范围代码")
    p.add_argument("--experience", default=None, help="经验要求代码")
    p.add_argument("--degree", default=None, help="学历要求代码")
    p.add_argument("--industry", default=None, help="行业代码")

    # 功能开关
    p.add_argument("--detail", action="store_true", default=True, help="抓取详情页 JD（默认开启）")
    p.add_argument("--no-detail", dest="detail", action="store_false", help="不抓取详情页")
    p.add_argument("--max-details", type=int, default=None, help="最多抓几个详情")
    p.add_argument("--enable-parallel", action="store_true", default=False,
                   help="详情抓取启用常驻 tab 池并行（spec 007 ⑧；默认串行）")
    p.add_argument("--tab-pool-size", type=int, default=3,
                   help="常驻 tab 数（1-5，默认 3；仅 --enable-parallel 时生效）")
    p.add_argument("--stagger-min", type=float, default=5.0,
                   help="错峰启动最小间隔秒（默认 5；仅 --enable-parallel 时生效）")
    p.add_argument("--stagger-max", type=float, default=10.0,
                   help="错峰启动最大间隔秒（默认 10；仅 --enable-parallel 时生效）")
    p.add_argument("--gap-min", type=float, default=8.0,
                   help="详情间隔最小秒数（默认 8；防 code:37）")
    p.add_argument("--gap-max", type=float, default=15.0,
                   help="详情间隔最大秒数（默认 15；防 code:37）")
    p.add_argument("--reset-every", type=int, default=3,
                   help="每抓 N 个详情重置一次 session（默认 3；防 code:37）")
    p.add_argument("--events-output", default=None,
                   help="详情 terminal safe event 输出路径 (JSONL；每行一个事件，"
                        "仅含 kind/status/job_id/duration_ms/safe_code，"
                        "供 source 批量解析；不传则不写事件文件)")
    p.add_argument("--analysis", action="store_true", help="输出分析报告")
    p.add_argument("--input", default=None, help="从已有 JSON 文件读取（跳过抓取）")
    p.add_argument("--allow-dom-fallback", action="store_true",
                   help="API 无数据时允许降级 DOM 提取（薪资可能受字体反爬影响，默认关闭）")

    # 工具命令
    p.add_argument("--check", action="store_true", help="运行环境诊断检查")
    p.add_argument("--smoke-test", action="store_true",
                   help="用真实 Chrome/CDP 跑一次 BOSS 搜索 API smoke test（不写结果文件）")
    p.add_argument("--list-cities", nargs="?", const="", default=None,
                   metavar="关键词",
                   help="打印支持的城市列表（可选关键词过滤，如 --list-cities 江）；"
                        "支持全国城市，码表见 data/city_codes.json，运行时自动从 BOSS 同步")
    p.add_argument("--setup-chrome", action="store_true",
                   help="自动启动 Chrome CDP 调试模式")
    p.add_argument("--copy-login-state", action="store_true",
                   help="已停用；不会复制 Chrome 数据库，请使用受控会话导入")
    p.add_argument("--import-boss-session", action="store_true",
                   help="从另一个已授权 CDP 浏览器导入仅限 zhipin.com 的会话")
    p.add_argument("--source-cdp-port", type=int,
                   help="会话导入的源 Chrome CDP 端口（必须与目标端口不同）")
    p.add_argument("--confirm-session-import", action="store_true",
                   help="确认本次显式授权读取并导入源浏览器的 BOSS 会话")
    p.add_argument("--reset-chrome-profile", action="store_true",
                   help="重建 BOSS 专用 Chrome profile，会清除此专用浏览器内的登录态")
    p.add_argument("--no-wait-login", action="store_true",
                   help="--setup-chrome 启动后不等待 BOSS 登录完成")
    p.add_argument("--login-timeout", type=int, default=DEFAULT_LOGIN_TIMEOUT,
                   help=f"--setup-chrome 等待登录完成的秒数 (默认 {DEFAULT_LOGIN_TIMEOUT})")
    p.add_argument("--stop-chrome", action="store_true",
                   help="关闭 BOSS 专用 CDP Chrome（按隔离 profile 精准匹配，不影响主 Chrome）")
    p.add_argument("--close-chrome", action="store_true",
                   help="抓取正常结束后自动关闭专用 Chrome（默认不关；异常退出不触发，保留登录态）")

    args = p.parse_args()

    if args.copy_login_state:
        print("❌ --copy-login-state 已停用：不会复制 Chrome 数据库。")
        print("   请改用 --import-boss-session + --confirm-session-import。")
        sys.exit(1)

    # --check 模式
    if args.check:
        sys.exit(run_check(args.cdp_port))

    if args.smoke_test:
        sys.exit(run_smoke_test(args.cdp_port))

    # --list-cities 模式（无需 Chrome/网络依赖，本地静态码表兜底）
    if args.list_cities is not None:
        list_cities(keyword=args.list_cities or None)
        sys.exit(0)

    if args.import_boss_session:
        sys.exit(run_import_boss_session(
            source_cdp_port=args.source_cdp_port,
            target_cdp_port=args.cdp_port,
            authorized=args.confirm_session_import,
        ))

    # --setup-chrome 模式
    if args.setup_chrome:
        sys.exit(run_setup_chrome(
            args.cdp_port,
            copy_login_state=args.copy_login_state,
            reset_profile=args.reset_chrome_profile,
            wait_login=not args.no_wait_login,
            login_timeout=args.login_timeout,
        ))

    # --stop-chrome 模式（关闭 BOSS 专用 CDP Chrome，独立命令）
    if args.stop_chrome:
        sys.exit(run_stop_chrome())

    if not require_runtime_dependencies("requests", "websocket"):
        sys.exit(1)

    # 页数限制
    if args.pages > MAX_PAGES:
        print(f"⚠️ 页数 {args.pages} 超过上限 {MAX_PAGES}，已自动调整为 {MAX_PAGES}")
        args.pages = MAX_PAGES
    if args.start_page < 1 or args.start_page > args.pages:
        print(f"❌ start-page 必须在 1 到 {args.pages} 之间")
        sys.exit(2)

    # 收集筛选条件
    filters = {}
    for key in ["scale", "stage", "salary", "experience", "degree", "industry"]:
        val = getattr(args, key)
        if val:
            filters[key] = val

    # 加载或抓取列表
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            list_data = json.load(f)
        print(f"从文件加载 {len(list_data.get('jobs',[]))} 条: {args.input}")
    else:
        # 登录状态检测
        print("检测登录状态...")
        if not check_login_state(args.cdp_port):
            print("❌ 未检测到 BOSS直聘登录状态。请先在 Chrome 中登录 zhipin.com。")
            print(f"   可运行 --check 检查环境，或 --setup-chrome 启动 Chrome。")
            sys.exit(1)
        print("✅ 已登录\n")

        list_data = scrape_list(
            args.keyword, args.city, args.pages, filters, args.output,
            cdp_port=args.cdp_port, fmt=args.format,
            allow_dom_fallback=args.allow_dom_fallback,
            start_page=args.start_page,
        )

    # 合并外部文件
    merged_details = None
    if args.merge:
        merged_jobs = merge_jobs(args.merge, list_data.get("jobs", []))
        list_data["jobs"] = merged_jobs
        list_data["total"] = len(merged_jobs)
        # 重新保存合并结果
        if args.output:
            flush_jobs(args.output, {
                "keyword": list_data.get("keyword", ""),
                "city": list_data.get("city", ""),
                "filters": list_data.get("filters", {}),
                "filter_desc": list_data.get("filter_desc", []),
                "scraped_at": datetime.now().isoformat(),
                "merged_from": args.merge,
            }, merged_jobs)
            print(f"合并结果已保存: {args.output}")
            if args.format == "csv":
                csv_path = args.output.rsplit(".", 1)[0] + ".csv"
                write_csv(csv_path, merged_jobs)
        # 同时加载旧详情，供后续详情抓取/分析合并（按 job_id 去重）
        merged_details = merge_details(args.merge, [])

    # 抓详情
    details = None
    if args.detail and list_data.get("jobs"):
        # 005 US4: 当 --events-output 提供时，把每个岗位的 terminal safe
        # event 写成 JSONL（每行一个事件），供 BossCdpSource.fetch_details_batch
        # 解析/校验。事件只含 kind/status/job_id/duration_ms/safe_code，
        # 不含 JD/凭据/PII（见 _emit_detail_safe_event）。
        events_callback = None
        events_file_handle = None
        if args.events_output:
            try:
                os.makedirs(os.path.dirname(args.events_output) or ".", exist_ok=True)
                events_file_handle = open(args.events_output, "w", encoding="utf-8")
                def events_callback(event, _f=events_file_handle):
                    _f.write(json.dumps(event, ensure_ascii=False) + "\n")
                    _f.flush()
            except OSError as exc:
                print(f"⚠️ 无法写入事件文件 ({args.events_output}): {exc}")
                events_callback = None
                if events_file_handle is not None:
                    try:
                        events_file_handle.close()
                    except OSError:
                        pass
                    events_file_handle = None
        try:
            details = scrape_details(
                list_data, args.max_details, args.detail_output,
                cdp_port=args.cdp_port, fmt=args.format,
                event_callback=events_callback,
                enable_parallel=args.enable_parallel,
                tab_pool_size=args.tab_pool_size,
                stagger_range=(args.stagger_min, args.stagger_max),
                inter_job_gap_range=(args.gap_min, args.gap_max),
                reset_every=args.reset_every,
            )
        finally:
            if events_file_handle is not None:
                try:
                    events_file_handle.close()
                except OSError:
                    pass
        # 若处于合并流程，把旧详情并入本次抓取结果并重新落盘，保证 --merge 后详情不丢失
        if merged_details and args.detail_output:
            details = merge_details_from_lists(merged_details, details)
            os.makedirs(os.path.dirname(args.detail_output) or ".", exist_ok=True)
            write_json_atomic(args.detail_output, details)
            print(f"合并详情已保存: {args.detail_output}")
            if args.format == "csv":
                detail_csv = args.detail_output.rsplit(".", 1)[0] + ".csv"
                write_detail_csv(detail_csv, details)

    # 分析
    if args.analysis:
        # 如果有详情文件也加载
        if not details:
            details = load_existing_details(args.input, args.detail_output)
        analyze(list_data, details, search_keyword=args.keyword)

    # 抓取正常结束后按需收尾（仅成功路径；异常/登录失败走 sys.exit，不会触发，保留登录态）
    if args.close_chrome:
        profile = prepare_cdp_profile(copy_login_state=False, reset=False)
        stopped = stop_cdp_chrome(profile["path"])
        if stopped:
            print(f"\n🧹 已按 --close-chrome 关闭 BOSS 专用 Chrome 进程：{stopped} 个")
        else:
            print(f"\nℹ️  --close-chrome 未发现运行中的 BOSS 专用 Chrome 进程")


def print_risk_control_report(err):
    """风控停止时的终端醒目报错：第几页挂的、为什么、已抓多少条、建议干啥。"""
    print()
    print("!" * 64)
    print("  抓取已被风控拦截，提前停止（已抓数据没有丢）")
    print("!" * 64)
    print(f"  原因: {err.reason}")
    if err.page is not None:
        print(f"  停在: 第 {err.page} 页")
    print(f"  已抓: {err.scraped_count} 条" +
          (f"，已保存到 {err.output_path}" if err.output_path else ""))
    print()
    print("  建议（按顺序试）:")
    print("    1. 打开 Chrome 里的 BOSS直聘，手动过一次验证码/安全校验")
    print("    2. 歇 30 分钟以上再抓（频繁抓取容易再被拦）")
    print("    3. 仍不行就退出登录后重新扫码登录")
    if err.resume_page is not None:
        print(f"  恢复后可用 --start-page {err.resume_page} 从断点续抓，已抓的不会重抓")
    print("!" * 64)


if __name__ == "__main__":
    try:
        main()
    except CDPUnavailableError as e:
        print(f"\n❌ {e}")
        sys.exit(2)
    except RiskControlError as e:
        print_risk_control_report(e)
        sys.exit(10)
