from __future__ import annotations
import json
from unittest.mock import patch, MagicMock
import requests


def _mock_chat_response(payload: dict, status_code: int = 200) -> MagicMock:
    """Build a mock requests.Response simulating a streaming chat completions reply.

    call_ai now uses stream=True and reads SSE lines via iter_lines().
    The mock produces ``data: {...}`` chunks followed by ``data: [DONE]``.
    """
    response = MagicMock()
    response.status_code = status_code
    content_str = json.dumps(payload, ensure_ascii=False)
    # 模拟流式：把完整 content 拆成若干 chunk（每 chunk 最多 40 字符）
    chunk_size = 40
    lines = []
    for i in range(0, len(content_str), chunk_size):
        chunk_text = content_str[i:i + chunk_size]
        sse_data = json.dumps(
            {"choices": [{"delta": {"content": chunk_text}, "finish_reason": None}]},
            ensure_ascii=False,
        )
        lines.append(f"data: {sse_data}")
    # 最后一个 chunk 带 finish_reason
    lines.append(f'data: {json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]})}')
    lines.append("data: [DONE]")
    lines.append("")  # 尾部空行
    response.iter_lines.return_value = iter(lines)
    return response


def _mock_stream_raw(content_str: str, status_code: int = 200,
                     finish_reason: str | None = "stop") -> MagicMock:
    """Build a streaming mock from a raw content string (may be invalid JSON)."""
    response = MagicMock()
    response.status_code = status_code
    sse_data = json.dumps(
        {"choices": [{"delta": {"content": content_str},
                      "finish_reason": finish_reason}]},
        ensure_ascii=False,
    )
    lines = [f"data: {sse_data}", "data: [DONE]", ""]
    response.iter_lines.return_value = iter(lines)
    return response
