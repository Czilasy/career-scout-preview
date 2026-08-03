"""智联招聘 CDP raw scraper 骨架（tasks004 T303）。

实施门禁（fixture_manifest.json blocked_facts）：
  list/detail/empty/login wall/edgeone/rate limit/block marker 全部未核验；
  company_nature options、非全国城市码未核验；can_run_real_tasks=false。

本模块只实现 preflight 分类逻辑骨架：
  - ``preflight`` 检查 CDP 端口可达性和智联登录态/EdgeOne/验证码/限流/封禁；
  - 真实页面 marker 检测函数（``_detect_login_wall`` / ``_detect_edgeone`` /
    ``_detect_verification`` / ``_detect_rate_limit`` / ``_detect_block``）
    保持占位，返回 None 表示未核验；
  - 在 marker fixture 核验后才解锁真实 marker 检测（T305/T307/T308/T310/T311）。

signal 字符串约定（与 webui/source.py ``_ZHILIAN_PREFLIGHT_SIGNAL_MAP`` 一致）：
  "ok" / "cdp_unavailable" / "login_required" / "verification" /
  "rate_limited" / "blocked" / "unreachable" / "timeout"

安全约束（T304）：
  本模块的日志和返回值不得包含 Cookie、JD 正文、页面正文、profile 路径、
  绝对路径、token。safe_log 由 adapter 层统一构造。
"""

from __future__ import annotations

import json
import time
from typing import Any

# 智联 CDP 冻结端口（与 BOSS 9222 隔离）。
DEFAULT_CDP_PORT = 9223

# 智联平台岗位 URL host allowlist（与 webui/platforms.py 注册规则一致）。
ZHILIAN_HOST_ALLOWLIST = frozenset({
    "www.zhaopin.com",
    "zhaopin.com",
    "m.zhaopin.com",
    "fe-api.zhaopin.com",
    "i.zhaopin.com",
})

# 智联登录态探测 URL（首页，用于检测 cookie 域名和登录态）。
_ZHILIAN_LOGIN_PROBE_URL = "https://www.zhaopin.com/"


def preflight(cdp_port: int = DEFAULT_CDP_PORT) -> str | None:
    """智联 preflight：检查 CDP 端口可达性 + 登录态 + 平台可访问性。

    返回稳定 signal 字符串：
      - "ok"：CDP 可达、登录态有效、无 EdgeOne/验证码/限流/封禁；
      - "cdp_unavailable"：CDP 端口不可达；
      - "login_required"：登录态失效（登录墙 marker 命中）；
      - "verification"：触发 EdgeOne/验证码；
      - "rate_limited"：触发限流；
      - "blocked"：触发封禁；
      - "unreachable"：无法连接智联平台；
      - "timeout"：请求超时。

    实施门禁：marker 检测函数（``_detect_login_wall`` 等）保持占位（返回 None），
    表示未核验。本函数在 marker 缺失下只检查 CDP 端口可达性，登录态/EdgeOne/
    验证码/限流/封禁 marker 检测返回 None 时，本函数返回 "unreachable" 占位
    （adapter 层将 None 转为 "unreachable" signal）。

    Args:
        cdp_port: 智联冻结 CDP 端口（默认 9223）。

    Returns:
        稳定 signal 字符串，或 None（marker 未核验，由 adapter 转为占位 signal）。
    """
    # T303 实施门禁：marker 检测函数全部未核验，preflight 暂返回 None 占位。
    # 真实 CDP 端口可达性检查需要 requests/websocket-client 依赖和真实 Chrome
    # 实例，且 marker 检测需要 fixture 核验后才解锁。
    # 在 marker 缺失下返回 None，由 adapter 默认 runner 转为 "unreachable"。
    return None


def fetch_list(plan_item: dict) -> tuple[str | None, list[dict]]:
    """智联列表抓取占位（tasks004 T306 门禁）。

    真实列表抓取与字段归一化需要 list_page_markers fixture（blocked_facts），
    本函数保持占位，返回 (None, [])。adapter 默认 runner 将 None 转为
    "invalid_output" signal，触发 source_invalid_output 失败。

    marker fixture 核验后才解锁真实抓取（T305/T307/T308）。

    Args:
        plan_item: 已通过 adapter 输入校验的计划项。

    Returns:
        (signal, jobs) 元组。signal=None 表示 marker 未核验。
    """
    # T306 实施门禁：list_page_markers 未核验，fetch_list 保持占位。
    return None, []


def fetch_detail(job: dict, *, detail_output_path: str | None = None) -> tuple[str | None, dict]:
    """智联详情抓取占位（tasks004 T311 门禁）。

    真实 JD 取得需要 detail_page_markers fixture（blocked_facts），本函数保持
    占位，返回 (None, {})。adapter 默认 runner 将 None 转为 "not_found" signal，
    触发 source_not_found 失败（不伪造 JD 正文）。

    marker fixture 核验后才解锁真实 JD 取得（T310/T311）。

    Args:
        job: 已通过 adapter 输入校验的岗位 dict（含 platform_job_id、canonical_url）。
        detail_output_path: 详情输出路径（可选）。

    Returns:
        (signal, detail) 元组。signal=None 表示 marker 未核验。
    """
    # T311 实施门禁：detail_page_markers 未核验，fetch_detail 保持占位。
    return None, {}


# ---------------------------------------------------------------------------
# marker 检测占位函数（T303 真实分支）。
# 在 marker fixture 核验后才解锁真实检测逻辑。
# ---------------------------------------------------------------------------

def _detect_login_wall(page_text: str) -> bool | None:
    """检测智联登录墙 marker。

    实施门禁：login_wall_markers 为 blocked_facts，本函数返回 None（未核验）。
    marker fixture 核验后实现真实检测（如检测登录弹窗/登录跳转 URL）。
    """
    return None


def _detect_edgeone(page_text: str) -> bool | None:
    """检测 EdgeOne 边缘验证页 marker。

    实施门禁：edgeone_markers 为 blocked_facts，本函数返回 None（未核验）。
    """
    return None


def _detect_verification(page_text: str) -> bool | None:
    """检测验证码/滑块 marker。

    实施门禁：verification_markers 为 blocked_facts，本函数返回 None（未核验）。
    """
    return None


def _detect_rate_limit(response_status: int, page_text: str) -> bool | None:
    """检测限流 marker。

    实施门禁：rate_limit_markers 为 blocked_facts，本函数返回 None（未核验）。
    """
    return None


def _detect_block(page_text: str) -> bool | None:
    """检测封禁 marker。

    实施门禁：block_markers 为 blocked_facts，本函数返回 None（未核验）。
    """
    return None


def _detect_empty_state(page_text: str) -> bool | None:
    """检测智联空结果页 marker（T308 真实空结果判定）。

    实施门禁：empty_state_markers 为 blocked_facts，本函数返回 None（未核验）。
    未核验前 adapter 不得返回 empty_success（避免伪造证据）。
    """
    return None


def is_zhilian_host(url: str) -> bool:
    """URL host 是否在智联 allowlist 内（脱敏判定）。"""
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower()
    except (ValueError, TypeError):
        return False
    return host in ZHILIAN_HOST_ALLOWLIST
