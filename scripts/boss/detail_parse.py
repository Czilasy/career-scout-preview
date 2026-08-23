# -*- coding: utf-8 -*-

"""详情文本助手与详情产物路径（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

import glob
import json
import os
from urllib.parse import parse_qsl
import re
from urllib.parse import urlencode
from urllib.parse import urlparse
from urllib.parse import urlunparse
from scripts.boss.constants import DEFAULT_RESULT_DIR, DETAIL_COMPETITIVENESS_MARKER, DETAIL_DESCRIPTION_MARKER, DETAIL_LOGIN_MARKER, DETAIL_SAFETY_MARKER
from scripts.boss.exceptions import DetailExtractionError, DetailLoginRequiredError, DetailRateLimitedError, DetailVerificationRequiredError
from scripts.boss.search import extract_block_hint
from scripts.boss_cdp_signals import looks_like_detail_rate_limited, looks_like_risk_control
from scripts.boss.constants import log

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


def _looks_like_detail_shell(jd: str) -> bool:
    """Reject known navigation/placeholder content without a length gate."""
    normalized = _normalize_detail_whitespace(jd)
    if not normalized:
        return True
    if normalized in {
        "暂无职位描述", "暂无岗位描述", "暂无", "只有一句话", "--", "-",
    }:
        return True
    tokens = [token for token in re.split(r"[\s,，。|/·]+", normalized) if token]
    shell_tokens = {
        "首页", "消息", "求职", "招聘", "职位", "公司", "我的",
        "推荐", "沟通", "发现", "登录", "注册",
    }
    return bool(tokens) and all(token in shell_tokens for token in tokens)


def extract_job_description(extracted):
    """Return validated JD text without BOSS page chrome.

    `page_text` is diagnostic input only. It is never persisted unless it has
    an explicit job-description section that passes all checks.

    FR-032：不再用固定 120 字硬截断。改为内容真实性判断：
    - 登录墙/导航壳/风控页 → 拒绝（保留原检查）
    - 空内容 → 拒绝
    - 明确从 JD 字段或职位描述区提取、且不是空壳/导航内容 → 通过
    - 语义标记只作为内容信号，不再作为短文本的必选词表
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
    # 真实 JD 正文可能包含“限流/频繁/解锁”等岗位职责词汇，只有拿不到真实 JD
    # 时才允许把页面文本判成风控/限流页。
    raw_jd_text = raw_jd
    if DETAIL_DESCRIPTION_MARKER in raw_jd_text:
        raw_jd_text = raw_jd_text.split(DETAIL_DESCRIPTION_MARKER, 1)[1]
    has_real_jd = (
        bool(_normalize_detail_whitespace(raw_jd_text))
        and not _looks_like_detail_shell(raw_jd_text)
    )
    if not has_real_jd:
        if looks_like_detail_rate_limited(diagnostic_text):
            raise DetailRateLimitedError(
                extract_block_hint(diagnostic_text)
                or "BOSS 账号/操作频繁被限流"
            )
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
    # FR-032：内容真实性判断替代固定字数硬截断
    if not jd.strip():
        raise DetailExtractionError("job description is empty after validation")
    if _looks_like_detail_shell(jd):
        raise DetailExtractionError(
            "detail page contains navigation or placeholder text that is too short "
            "to contain a real JD"
        )
    return jd


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
        except (OSError, ValueError) as e:
            log.warning(f"无法加载详情文件 {path}: {e}")
    return None
