"""平台岗位链接规范化与登录空间解析（021 B7 自 platforms.py 搬运）。"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from webui.url_safety import clean_https_url, is_safe_https_authority, parse_https_url

from webui.platforms_schema import (
    _BOSS_ALLOWED_HOSTS,
    _MSG_BOSS_PROFILE_DIR_REQUIRED,
    _MSG_BROWSER_ACCOUNT_REQUIRED,
    _ZHILIAN_ALLOWED_HOSTS,
    BOSS_DEFAULT_CDP_PORT,
    LoginSpace,
    ZHILIAN_DEFAULT_CDP_PORT,
)




# ---------------------------------------------------------------------------
# URL 规范化（平台权威）
# ---------------------------------------------------------------------------

def _clean_url(raw: str) -> str:
    """剥离 query/fragment，返回 scheme+host+path 或空串。"""
    if not raw or not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    cleaned = parsed._replace(query="", fragment="")
    return urlunparse(cleaned)




def normalize_boss_job_url(raw: str) -> str:
    """BOSS 岗位链接规范化：仅 HTTPS + *.zhipin.com，剥离 query/fragment。"""
    parsed = parse_https_url(raw)
    if parsed is None:
        return ""
    if not is_safe_https_authority(
        parsed, allowed_hosts=_BOSS_ALLOWED_HOSTS, allow_subdomains=True
    ):
        return ""
    return clean_https_url(parsed)




def normalize_zhilian_job_url(raw: str) -> str:
    """智联岗位链接规范化。

    允许 zhaopin.com/www.zhaopin.com/m.zhaopin.com 的 jobdetail/<id>.htm，
    以及 jobs.zhaopin.com/<id>.htm（真实详情源站）；http 升级为 https，
    jobs/m 域名统一归一为 www.zhaopin.com/jobdetail/<id>.htm，移除 query/fragment。
    """
    if not raw or not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
    except ValueError:
        return ""
    scheme = parsed.scheme.lower()
    if scheme == "http":
        parsed = parsed._replace(scheme="https")
    elif scheme != "https":
        return ""
    host = (parsed.hostname or "").lower()
    if not is_safe_https_authority(
        parsed, allowed_hosts=_ZHILIAN_ALLOWED_HOSTS
    ):
        return ""
    if host not in _ZHILIAN_ALLOWED_HOSTS:
        return ""
    path = parsed.path or ""
    if host in ("jobs.zhaopin.com", "m.zhaopin.com"):
        job_match = _ZHILIAN_JOB_SOURCE_PATH_RE.match(path)
        if job_match is None:
            return ""
        return _ZHILIAN_DETAIL_PATTERN.format(job_id=job_match.group(1))
    if not _ZHILIAN_JOB_PATH_RE.match(path):
        return ""
    cleaned = parsed._replace(query="", fragment="", params="")
    return urlunparse(cleaned)


import re as _re

_ZHILIAN_JOB_PATH_RE = _re.compile(r"^/jobdetail/[A-Za-z0-9_-]+\.htm$")


_ZHILIAN_JOB_SOURCE_PATH_RE = _re.compile(r"^/([A-Za-z0-9_-]+)\.htm$")


_ZHILIAN_DETAIL_PATTERN = "https://www.zhaopin.com/jobdetail/{job_id}.htm"




# ---------------------------------------------------------------------------
# 登录空间解析
# ---------------------------------------------------------------------------

def resolve_boss_login_space(
    browser_account: str, *, boss_profile_dir: str,
) -> LoginSpace:
    """BOSS 登录空间：复用现有账号 profile_dir，端口 9222。"""
    if not browser_account:
        raise ValueError(_MSG_BROWSER_ACCOUNT_REQUIRED)
    if not boss_profile_dir:
        raise ValueError(_MSG_BOSS_PROFILE_DIR_REQUIRED)
    return LoginSpace(
        platform="boss",
        browser_account=browser_account,
        profile_key=f"boss:{browser_account}",
        cdp_port=BOSS_DEFAULT_CDP_PORT,
    )




def resolve_zhilian_login_space(
    browser_account: str, *, boss_profile_dir: str,
) -> LoginSpace:
    """智联登录空间：profile_dir = boss_profile_dir + '.zhilian'，端口 9223。"""
    if not browser_account:
        raise ValueError(_MSG_BROWSER_ACCOUNT_REQUIRED)
    if not boss_profile_dir:
        raise ValueError(_MSG_BOSS_PROFILE_DIR_REQUIRED)
    return LoginSpace(
        platform="zhilian",
        browser_account=browser_account,
        profile_key=f"zhilian:{browser_account}",
        cdp_port=ZHILIAN_DEFAULT_CDP_PORT,
    )




def _boss_profile_dir_differs_from_zhilian(boss_profile_dir: str) -> bool:
    """校验 BOSS profile_dir 与其 .zhilian 派生路径不同。"""
    if not boss_profile_dir:
        return False
    return str(boss_profile_dir).rstrip("/") + ".zhilian" != str(boss_profile_dir)
