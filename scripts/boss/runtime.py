# -*- coding: utf-8 -*-

"""运行时依赖注入 require_runtime_dependencies（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

import sys
import sys as _sys
def _facade():
    return _sys.modules.get("scripts.boss_cdp_raw")

requests = None
websocket = None

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
