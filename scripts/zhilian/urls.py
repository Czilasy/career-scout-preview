# -*- coding: utf-8 -*-
"""智联 URL 判定与输入指纹（031 B6 自 scripts/zhilian_cdp_raw.py 物理搬运）。

纯函数域：host allowlist 判定与计划项输入 hash，无 CDP/网络依赖。
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse

from scripts.zhilian.cdp import ZHILIAN_HOST_ALLOWLIST


def is_zhilian_host(url: str) -> bool:
    """URL host 是否在智联 allowlist 内（脱敏判定）。"""
    if not url:
        return False
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except (ValueError, TypeError):
        return False
    return host in ZHILIAN_HOST_ALLOWLIST


def input_hash(payload: dict) -> str:
    """智联输入 hash：覆盖 platform/关键词/完整城市解析快照/页数。"""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
