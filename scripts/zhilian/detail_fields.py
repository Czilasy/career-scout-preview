# -*- coding: utf-8 -*-
"""智联详情页 staff 字段提取（028 B081 第 7 类：招聘者上次活跃）。

scripts/zhilian_cdp_raw.py 已超宪法红线（894 行），本模块承接新增逻辑
（宪法原则 VI 分流）：JS 片段常量与结果合并纯函数。staff 数据只在抓取
子进程的浏览器里可见，webui 侧拿不到。判定以 lastOnlineTime 毫秒时间戳
为准（2026-08-28 浏览器实测）；状态文本仅作展示，取不到为空串（未知兜底）。
"""

from __future__ import annotations

#: 详情提取 JS——const 声明段（拼入 _scrape_detail_on_ws 现有表达式，
#: 与 s/p/c/clean 声明同级，位于 return { 之前）。
STAFF_CONST_JS = (
    "const st=((s.jobDetail||{}).staff)||{};"
    "const stMs=Number(st.lastOnlineTime);"
)

#: 详情提取 JS——返回对象字段段（拼入对象字面量结尾）。
#: lastOnlineTime 无效时 staffLastOnlineMs=0（merge 侧不落键）；
#: 状态文本候选键逐一回退，全部缺失为空串。
STAFF_FIELD_JS = (
    "staffLastOnlineMs:(isFinite(stMs)&&stMs>0)?stMs:0,"
    "recruiter_activity_text:(st.statusText||st.statusDesc||st.activeDesc"
    "||st.activeText||st.onlineText||'')"
)


def merge_staff_fields(detail: dict, value: dict) -> dict:
    """把 JS 提取结果中的 staff 字段并入 detail dict（028 载荷契约键名）。

    lastOnlineTime 无效（0/负/非数值）时不写 ``recruiter_last_online_ms``，
    只保留展示文本——webui.recruiter_activity 归一化为未知，不误拦。
    """
    value = value if isinstance(value, dict) else {}
    text = str(value.get("recruiter_activity_text") or "")
    if text:
        detail["recruiter_activity_text"] = text
    try:
        ms = float(value.get("staffLastOnlineMs"))
    except (TypeError, ValueError):
        return detail
    if ms > 0:
        detail["recruiter_last_online_ms"] = (
            int(ms) if float(ms).is_integer() else ms
        )
    return detail
