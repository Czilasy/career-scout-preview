# -*- coding: utf-8 -*-

"""CDPSession 连接会话（021 B8 T026 自 scripts/boss_cdp_raw.py 物理搬运）。"""

import json
import time
from scripts.boss.constants import DEFAULT_CDP_PORT
from scripts.boss.exceptions import CDPUnavailableError
from scripts.boss.constants import log
import sys as _sys
def _facade():
    return _sys.modules.get("scripts.boss_cdp_raw")

# ============================================================
# CDP 连接
# ============================================================
class CDPSession:
    def __init__(self, cdp_port=DEFAULT_CDP_PORT):
        if not _facade().require_runtime_dependencies("requests", "websocket"):
            raise RuntimeError("缺少 CDP 运行依赖")
        self.cdp_port = cdp_port
        try:
            resp = _facade().requests.get(f"http://127.0.0.1:{cdp_port}/json/version", timeout=10)
            ws_url = resp.json()["webSocketDebuggerUrl"]
            self.ws = _facade().websocket.create_connection(ws_url, timeout=60)
        except (_facade().requests.ConnectionError, _facade().requests.Timeout) as e:
            raise CDPUnavailableError(
                f"连不上调试浏览器（127.0.0.1:{cdp_port}）。\n"
                "请先运行 --setup-chrome 启动带调试端口的 Chrome，并登录 BOSS直聘；\n"
                "Chrome 关了调试端口就没了，需要重新启动。"
            ) from e
        except (KeyError, ValueError) as e:
            raise CDPUnavailableError(
                f"端口 {cdp_port} 上的服务不是 Chrome 调试端口（返回内容无法识别）。\n"
                "请用 --setup-chrome 启动专用 Chrome，不要占用该端口。"
            ) from e
        except _facade().websocket.WebSocketException as e:
            raise CDPUnavailableError(
                f"调试浏览器（127.0.0.1:{cdp_port}）的 WebSocket 连接失败。\n"
                "请关闭该 Chrome 后重新运行 --setup-chrome。"
            ) from e
        self.mid = 0

    def send(self, method, params=None, sid=None, timeout=30):
        """发送 CDP 命令并等待匹配的响应。

        Args:
            method: CDP 方法名
            params: 参数字典
            sid: Target session ID
            timeout: 等待响应的超时秒数，默认 30s

        Returns:
            CDP 响应字典

        Raises:
            TimeoutError: 超过 max_retries 仍未收到匹配响应
        """
        self.mid += 1
        msg = {"id": self.mid, "method": method, "params": params or {}}
        if sid:
            msg["sessionId"] = sid
        self.ws.send(json.dumps(msg))

        start_time = time.time()
        max_retries = 1000

        for attempt in range(max_retries):
            # 检查超时
            elapsed = time.time() - start_time
            if elapsed > timeout:
                raise TimeoutError(
                    f"CDP send({method}) 超时 ({timeout}s), "
                    f"已跳过 {attempt} 条不匹配消息"
                )

            try:
                raw = self.ws.recv()
            except _facade().websocket.WebSocketTimeoutException:
                raise TimeoutError(f"CDP WebSocket recv 超时, method={method}")
            except _facade().websocket.WebSocketException as exc:
                raise ConnectionError(f"CDP 连接异常：{exc}")

            try:
                r = json.loads(raw)
            except ValueError:
                log.debug(f"跳过非 JSON 消息: {raw[:100]}")
                continue

            if r.get("id") == self.mid:
                return r

            # 不匹配的消息：可能是事件通知，记录并跳过
            event_name = r.get("method", "unknown")
            log.debug(f"跳过不匹配消息 (id={r.get('id')}, event={event_name})")

        raise TimeoutError(
            f"CDP send({method}) 在 {max_retries} 条消息内未找到匹配响应"
        )

    def eval_js(self, js, sid):
        r = self.send("Runtime.evaluate", {"expression": js, "returnByValue": True}, sid)
        return r.get("result", {}).get("result", {}).get("value", None)

    def close(self):
        self.ws.close()
