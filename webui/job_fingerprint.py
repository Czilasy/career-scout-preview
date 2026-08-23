"""岗位指纹：跨平台去重用的确定性归一化纯函数（019）。

口径冻结于 specs/019（FR-002 / research R6）：
- 全半角统一（NFKC）、去空白、小写；
- 标题仅字符级归一，不做语义归并；
- 公司剥括号注释、末尾组织后缀与当前城市前缀，不做互含/相似度；
- 城市取市级（分隔符首段，去「市」后缀）；
- 三元组任一为空 → 无指纹，岗位不参与跨平台判定。

宁可漏判（重复仍显示两条）不可误合（不同岗位被并成一条）。
无外部依赖、无 I/O；同输入恒同输出。
"""

from __future__ import annotations

import re
import unicodedata

__all__ = [
    "normalize_title",
    "normalize_city",
    "normalize_company",
    "fingerprint",
    "build_fingerprint_index",
]

# 组织形态后缀（末尾才剥，长词优先，循环剥到没有为止）。
_ORG_SUFFIXES: tuple[str, ...] = (
    "股份有限公司", "有限责任公司", "有限合伙企业", "有限公司",
    "股份公司", "责任公司", "集团公司", "合伙企业", "集团",
    "corporation", "company", "limited", "inc", "ltd", "llc", "corp",
)

# 行业词（末尾循环剥，至多两个，且剥后核心名仍非空；「字节跳动科技」≡「字节跳动」）。
_INDUSTRY_WORDS: tuple[str, ...] = (
    "信息技术", "互联网", "文化传媒", "文化传播", "科技", "网络",
    "信息", "软件", "数据", "智能", "传媒", "文化", "实业", "投资",
    "控股", "咨询", "服务", "教育", "电子", "通信", "技术", "医药",
    "生物", "医疗", "环保", "能源", "置业", "资产", "管理",
)

# 括号注释（成对剥除内容，中英文常见括号形态）。
_BRACKET_RE = re.compile(r"[（(\[【〔「][^（）()\[\]【〕「]*[）)\]】〕」]")

# 末尾孤立标点（如 "Inc." 的句点），后缀剥离前清掉。
_TRAILING_JUNK_RE = re.compile(r"[.。·•]+$")

# 城市分隔符：location 取首段（北京·朝阳区 / 北京-朝阳 / 北京/朝阳…）。
_CITY_SEPARATORS_RE = re.compile(r"[·．.\-—–/／\s、，,]+")


def _fold(text: str) -> str:
    """NFKC 全半角统一 → 去全部空白 → 小写。"""
    if not text or not isinstance(text, str):
        return ""
    folded = unicodedata.normalize("NFKC", text)
    folded = "".join(folded.split())
    return folded.lower()


def normalize_title(title: str) -> str:
    """标题归一：仅字符级（全半角/空白/大小写），不做语义归并。"""
    return _fold(title)


def normalize_city(location: str) -> str:
    """城市归一：取分隔符首段并去「市」后缀（市级）；取不出为空。"""
    folded = _fold(location or "")
    if not folded:
        return ""
    first = _CITY_SEPARATORS_RE.split(folded, 1)[0]
    if first.endswith("市"):
        first = first[:-1]
    return first


def normalize_company(company: str, city: str = "") -> str:
    """公司归一：剥括号注释 → 循环剥末尾组织后缀 → 循环剥末尾行业词（≤2）
    → 剥当前城市前缀（仅剥本岗位城市；城市不同不剥，宁可漏判）。"""
    folded = _fold(company or "")
    if not folded:
        return ""
    folded = _BRACKET_RE.sub("", folded)
    for _ in range(3):
        folded = _TRAILING_JUNK_RE.sub("", folded)
        for suffix in _ORG_SUFFIXES:
            if folded.endswith(suffix) and len(folded) > len(suffix):
                folded = folded[: -len(suffix)]
                break
        else:
            break
    for _ in range(2):
        for word in _INDUSTRY_WORDS:
            if folded.endswith(word) and len(folded) > len(word):
                folded = folded[: -len(word)]
                break
        else:
            break
    city_name = _fold(city or "")
    if city_name and folded.startswith(city_name) and len(folded) > len(city_name):
        folded = folded[len(city_name):]
    return folded


def fingerprint(job: dict) -> tuple[str, str, str] | None:
    """三元组指纹（归一化公司, 归一化标题, 市级城市）；任一为空返回 None。"""
    if not isinstance(job, dict):
        return None
    city = normalize_city(str(job.get("location") or ""))
    company = normalize_company(
        str(job.get("company") or job.get("boss_name") or ""), city)
    title = normalize_title(str(job.get("title") or ""))
    if not company or not title or not city:
        return None
    return (company, title, city)


def build_fingerprint_index(jobs) -> dict[tuple[str, str, str], dict]:
    """构建 指纹→岗位 索引；首个出现优先，无指纹岗位跳过。"""
    index: dict[tuple[str, str, str], dict] = {}
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        key = fingerprint(job)
        if key is not None and key not in index:
            index[key] = job
    return index
