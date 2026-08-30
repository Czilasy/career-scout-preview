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

031 B6 后本文件为兼容门面：实现按域归组至 ``scripts/zhilian/``（cdp /
search / detail / urls），本文件只 re-export——经 ``__getattr__`` 动态代理
全部旧符号，保持旧 import 与旧测试 patch 面可用（宪法 VI 拆分豁免）。
新代码一律 import 域模块，不走本门面。
"""

from __future__ import annotations

import scripts.zhilian.cdp as _zhilian_cdp
import scripts.zhilian.detail as _zhilian_detail
import scripts.zhilian.search as _zhilian_search
import scripts.zhilian.urls as _zhilian_urls

# 域模块查找顺序：urls 为纯函数域，放最后兜底。
_ZHILIAN_MODULES = (
    _zhilian_cdp,
    _zhilian_search,
    _zhilian_detail,
    _zhilian_urls,
)


def __getattr__(name):
    """代理旧符号到域模块（镜像 scripts/boss_cdp_raw.py 021 B8 模式）。

    注意：本门面只做「读取代理」。域模块内部互调走的是域模块自己的全局，
    因此 ``patch("scripts.zhilian_cdp_raw.X")`` 不会影响域内调用——测试需
    patch 域模块（如 ``scripts.zhilian.detail._scrape_detail_on_ws``）。
    """
    for _m in _ZHILIAN_MODULES:
        if hasattr(_m, name):
            return getattr(_m, name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
