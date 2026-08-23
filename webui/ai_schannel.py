"""Windows schannel curl POST 适配（021 B7 自 ai.py 搬运）。

用系统 curl 的 schannel 后端绕开 Python TLS 栈执行 AI POST 请求；
由 webui.ai._post_ai_json 经门面在调用时动态取用（patch 面保持）。
"""

from __future__ import annotations

import json
import os
import subprocess

import requests




_SCHANNEL_POST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
try {
    $inputData = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $headers = @{ Authorization = ('Bearer ' + $inputData.api_key) }
    $body = $inputData.payload | ConvertTo-Json -Depth 30 -Compress
    $response = Invoke-WebRequest -UseBasicParsing `
        -Uri $inputData.url -Method Post -Headers $headers `
        -ContentType 'application/json' `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) `
        -TimeoutSec ([int]$inputData.timeout_seconds)
    @{ ok = $true; status = [int]$response.StatusCode; body = $response.Content } |
        ConvertTo-Json -Compress -Depth 5
} catch {
    $webResponse = $_.Exception.Response
    if ($null -ne $webResponse) {
        $reader = [IO.StreamReader]::new($webResponse.GetResponseStream())
        $responseBody = $reader.ReadToEnd()
        $reader.Dispose()
        @{ ok = $true; status = [int]$webResponse.StatusCode; body = $responseBody } |
            ConvertTo-Json -Compress -Depth 5
    } else {
        @{ ok = $false } | ConvertTo-Json -Compress
    }
}
"""




def _windows_schannel_post(
    url: str, api_key: str, payload: dict, *, timeout_seconds: int,
) -> requests.Response:
    """POST through Windows Schannel without exposing credentials in argv."""
    request_input = json.dumps({
        "url": url,
        "api_key": api_key,
        "payload": payload,
        "timeout_seconds": max(1, int(timeout_seconds)),
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        completed = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive",
                "-Command", _SCHANNEL_POST_SCRIPT,
            ],
            input=request_input,
            capture_output=True,
            timeout=max(1, int(timeout_seconds)) + 10,
            check=False,
            **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}),
        )
    except subprocess.TimeoutExpired:
        raise requests.Timeout("Schannel fallback timed out") from None
    except OSError:
        raise requests.ConnectionError("Schannel fallback unavailable") from None
    if completed.returncode != 0:
        raise requests.ConnectionError("Schannel fallback failed")
    try:
        envelope = json.loads(completed.stdout.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise requests.ConnectionError("Schannel fallback returned invalid data") from None
    if not isinstance(envelope, dict) or not envelope.get("ok"):
        raise requests.ConnectionError("Schannel fallback failed")
    body = envelope.get("body")
    if not isinstance(body, str):
        body = ""
    response = requests.Response()
    response.status_code = int(envelope.get("status") or 0)
    response._content = body.encode("utf-8")
    response.encoding = "utf-8"
    response.url = url
    return response
