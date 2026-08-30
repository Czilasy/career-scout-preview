# -*- coding: utf-8 -*-

"""运行时依赖注入 require_runtime_dependencies（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

import sys

requests = None
websocket = None
_run_active = False  # 是否正在 run_search_programmatic 组合运行内（031 B5 自门面迁居）


def set_run_active(value: bool) -> None:
    """置位/清除组合运行活动标志，并镜像到门面命名空间（旧读取方兼容）。"""
    global _run_active
    _run_active = value
    facade_mod = sys.modules.get('scripts.boss_cdp_raw')
    if facade_mod is not None:
        facade_mod._run_active = value

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
    # 同步回写门面命名空间（外部以 boss.requests / boss.websocket 读写）
    _facade_mod = sys.modules.get('scripts.boss_cdp_raw')
    if _facade_mod is not None:
        _facade_mod.requests = requests
        _facade_mod.websocket = websocket
    return True
