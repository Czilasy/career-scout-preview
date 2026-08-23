# -*- coding: utf-8 -*-

"""平台常量、JS 模板与筛选参数映射（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

import logging
import ntpath
import os
import platform
import re

_VISIBILITY_STATE_JS = "Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});"


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


MAX_API_REQUESTS = 999  # 单次抓取运行最大 API 请求数（B053：按运行隔离，不跨轮累计）


BROWSER_NOT_FOUND_HINT = "请安装 Chrome 或使用系统自带 Edge"


CHROME_EXE = "chrome.exe"


EDGE_EXE = "msedge.exe"


CDP_CMD_PAGE_NAVIGATE = "Page.navigate"


CDP_CMD_CREATE_TARGET = "Target.createTarget"


CDP_CMD_ATTACH_TARGET = "Target.attachToTarget"


CDP_CMD_ADD_SCRIPT_ON_NEW_DOC = "Page.addScriptToEvaluateOnNewDocument"


CDP_CMD_CLOSE_TARGET = "Target.closeTarget"


CDP_ABOUT_BLANK = "about:blank"


HIDDEN_DEFINE_JS = "Object.defineProperty(document, 'hidden', {get: () => false});"


MSG_BOSS_LOGIN_STATUS = "BOSS 登录状态"


MSG_DEDICATED_BROWSER_STARTED = "专用浏览器已启动"


MSG_USER_CANCELLED_SCRAPE = "用户取消抓取"


def detect_chromium_browsers():
    """探测本机 Chromium 系浏览器（Chrome / Edge），返回结构化结果。

    Returns:
        {"chrome": 可执行文件路径或 None, "edge": 可执行文件路径或 None}

    Windows 依次查 LOCALAPPDATA / PROGRAMFILES / PROGRAMFILES(X86) 下的
    chrome.exe 与 msedge.exe；macOS / Linux 顺带支持常见安装路径。
    两类浏览器各保留第一个命中的路径；两者都找不到时返回两个 None。
    """
    system = platform.system()
    found = {"chrome": None, "edge": None}
    if system == "Windows":
        candidates = (
            ("chrome", "LOCALAPPDATA", "Google", "Chrome", "Application", CHROME_EXE),
            ("chrome", "PROGRAMFILES", "Google", "Chrome", "Application", CHROME_EXE),
            ("chrome", "PROGRAMFILES(X86)", "Google", "Chrome", "Application", CHROME_EXE),
            ("edge", "PROGRAMFILES", "Microsoft", "Edge", "Application", EDGE_EXE),
            ("edge", "PROGRAMFILES(X86)", "Microsoft", "Edge", "Application", EDGE_EXE),
            ("edge", "LOCALAPPDATA", "Microsoft", "Edge", "Application", EDGE_EXE),
        )
        for kind, env_name, *parts in candidates:
            if found[kind]:
                continue
            base = os.environ.get(env_name)
            if not base:
                continue
            candidate = ntpath.join(base, *parts)
            if os.path.exists(candidate):
                found[kind] = candidate
        return found
    if system == "Darwin":
        for kind, path in (
            ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ("edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ):
            if os.path.exists(path):
                found[kind] = path
        return found
    for kind, candidates in (
        ("chrome", (
            "/usr/bin/google-chrome", "/usr/bin/chromium-browser",
            "/usr/bin/chromium", "/snap/bin/chromium",
        )),
        ("edge", ("/usr/bin/microsoft-edge", "/usr/bin/microsoft-edge-stable")),
    ):
        for candidate in candidates:
            if os.path.exists(candidate):
                found[kind] = candidate
                break
    return found


def get_default_chrome_path():
    """返回首选浏览器路径（Chrome 优先，其次 Edge）；都找不到返回 None。"""
    browsers = detect_chromium_browsers()
    return browsers.get("chrome") or browsers.get("edge")


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


LOGIN_PROBE_CITY = "101020100"


LOGIN_PROBE_PAGE_SIZE = 10


DEFAULT_LOGIN_TIMEOUT = 300


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
        var apiCode = Number(data.code || 0);
        if (apiCode !== 0) {
            return JSON.stringify([{error: 'api_code', code: apiCode, sample: String(data.message || data.msg || '').slice(0, 160)}]);
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
                location: [j.cityName || '', j.areaDistrict || '', j.businessDistrict || ''].filter(function(x){return !!x;}).join('\\u00b7'),
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
            location: (locEl ? locEl.innerText.trim() : '').split('\\u00b7').map(function(x){return x.trim();}).filter(function(x){return !!x;}).join('\\u00b7'),
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


# FR-032：不再用固定字数硬截断。
# 保留 MIN_DETAIL_TEXT_LENGTH 仅向后兼容（默认不再传入 extract_job_description）。
MIN_DETAIL_TEXT_LENGTH = 0


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


# 风控/限流关键词与实锤分档已收敛到 scripts/boss_cdp_signals.py（单一来源，
# 016-error-module-rework）；本模块经顶部 import 继续暴露同名符号供既有调用方
# 与测试使用，不再各自维护词表。
# 列表抓取：连续多少页拿不到数据就判定异常并停止（正常搜索极少连续空页）
MAX_CONSECUTIVE_EMPTY_PAGES = 3


# looks_like_risk_control / looks_like_rate_limited / looks_like_detail_rate_limited
# 的实现已迁至 scripts/boss_cdp_signals.py，此处经由顶部 import 提供同名符号。
_UNLOCK_TIME_PATTERNS = (
    # 完整年月日: 2026-08-05 18:30 / 2026/8/5 18:30 / 2026年8月5日 18:30
    re.compile(r"(?P<y>\d{4})[-/年](?P<m>\d{1,2})[-/月](?P<d>\d{1,2})日?\s+(?P<H>\d{1,2}):(?P<M>\d{2})"),
    # 月日: 8月5日 18:30（无年份 → 当年）
    re.compile(r"(?P<m>\d{1,2})月(?P<d>\d{1,2})日\s+(?P<H>\d{1,2}):(?P<M>\d{2})"),
)


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


log = logging.getLogger("boss_cdp")
