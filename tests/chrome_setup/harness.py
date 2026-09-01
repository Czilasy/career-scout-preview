import importlib.util
import json
import os
import pathlib
import sys
import tempfile
from unittest import mock

# 基线全量运行时，其他测试模块（webui、sc015_viewport_check 等）已先把真实
# requests / websocket 导入 sys.modules，load_module 的 setdefault 因此不生效。
# 拆分后单独运行本目录时必须先导入真实依赖，复刻同一前置状态；否则裸 Mock
# 会被 require_runtime_dependencies 的真实 import 捡走并回写覆盖测试注入。
import requests  # noqa: F401
import websocket  # noqa: F401


SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "boss_cdp_raw.py"

# 测试真实 spawn 的 CLI 子进程（--help/--check/session-import 等）会经
# get_logger 懒初始化写日志；统一把日志目录指到系统临时目录，防测试噪音
# 灌进正式日志目录（033 白箱边缘情况）。
os.environ.setdefault(
    "CAREER_SCOUT_LOG_DIR",
    str(pathlib.Path(tempfile.gettempdir()) / "career-scout-test-logs"),
)


def load_module():
    """加载 boss_cdp_raw 模块，mock 掉 websocket/requests 两个可选依赖。

    034 修复：exec 加载期间 boss_cdp_raw 的 ``__name__ != "scripts.boss_cdp_raw"``
    分支会把 ``sys.modules["scripts.boss_cdp_raw"]`` 替换成 exec 实例；结束后必须
    恢复原始引用，避免全量顺序下污染后续测试。
    """
    sys.modules.setdefault("websocket", mock.Mock())
    sys.modules.setdefault("requests", mock.Mock())
    original = sys.modules.get("scripts.boss_cdp_raw")
    spec = importlib.util.spec_from_file_location("boss_cdp_raw", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if original is not None:
            sys.modules["scripts.boss_cdp_raw"] = original
        else:
            sys.modules.pop("scripts.boss_cdp_raw", None)
    return module


class tempfile_profile:
    def __enter__(self):
        import tempfile

        self.tmp_path = pathlib.Path(tempfile.mkdtemp())
        root = self.tmp_path
        source_profile = root / "Google" / "Chrome"
        default = source_profile / "Default"
        default.mkdir(parents=True)
        for name in ["Cookies", "Cookies-journal", "Login Data", "Web Data"]:
            (default / name).write_text(name, encoding="utf-8")
        network = default / "Network"
        network.mkdir()
        (network / "Cookies").write_text("network cookies", encoding="utf-8")
        (source_profile / "Local State").write_text("state", encoding="utf-8")
        self.paths = {
            "source_profile": source_profile,
            "cdp_profile": root / "persistent-profile",
        }
        return self.paths

    def __exit__(self, exc_type, exc, tb):
        # Windows 上 Chrome 子进程可能还占着 chrome_stderr.log，cleanup 会抛
        # PermissionError；用 ignore_errors 静默清理（CI 上本来也不该因临时文件失败）
        import shutil
        shutil.rmtree(self.tmp_path, ignore_errors=True)


def fake_run(calls, *args, **kwargs):
    calls["run"].append(args[0])
    return type("Completed", (), {"stdout": "", "returncode": 0})()


ROOT_PATH = SCRIPT_PATH.parents[1]


def _normalize_version(raw):
    """统一版本号格式，去掉 'v' 前缀和 patch 段，只比较 major.minor。

    README 里常写成 'v2.3'，pyproject/脚本里是 '2.3.0'，
    只要 major.minor 一致即视为同步，避免 patch 号差异造成误报。
    """
    text = str(raw).strip().lstrip("vV")
    parts = text.split(".")
    major = parts[0] if len(parts) > 0 else "0"
    minor = parts[1] if len(parts) > 1 else "0"
    return f"{major}.{minor}"


def _make_scrape_details_list_data(n=10):
    """Build a deterministic list_data payload with *n* jobs.

    The fields mirror what scrape_jobs emits and what scrape_details
    consumes today (job_link, encrypt_*_id, security_id, skills, etc.).
    The fake JD/credential values are intentionally distinctive so that
    any leak into a safe event is easy to detect.
    """
    jobs = []
    for i in range(n):
        jobs.append({
            "title": f"Job-{i}",
            "boss_name": f"Company-{i}",
            "job_link": f"https://www.zhipin.com/job_detail/encrypt{i}.html",
            "encrypt_job_id": f"SECRET-ENC-JOB-{i}",
            "encrypt_boss_id": f"SECRET-ENC-BOSS-{i}",
            "encrypt_brand_id": f"SECRET-ENC-BRAND-{i}",
            "security_id": f"SECRET-SEC-{i}",
            "skills": "Python | SQL",
            "salary": "20-30K",
            "city": "上海",
        })
    return {"jobs": jobs}


def _fake_detail_payload_default():
    """Return a default fake JD long enough to pass extract_job_description.

    The payload deliberately avoids ``DETAIL_DESCRIPTION_MARKER`` and
    ``DETAIL_LOGIN_MARKER`` so the extractor accepts it. The distinctive
    ``SECRET-JD-BODY`` marker lets leak-detection tests assert that the
    JD never reaches a safe event payload.
    """
    return {
        "jd": "SECRET-JD-BODY " + ("后端服务开发参与系统架构设计。 " * 12),
        "tags": ["SECRET-TAG"],
    }


class _FakeScrapeDetailsCDPSession:
    """In-memory CDPSession double for scrape_details contract tests.

    Records every CDP call into ``call_log`` and scripts readiness +
    detail-extraction responses. The fake never opens a WebSocket and
    never touches the network, so tests are deterministic.

    ``readiness_responses`` is a FIFO list consumed by readiness probes
    (recognised by the ``__boss_readiness_probe__`` marker). When the
    list is empty, the probe is treated as "ready".
    """

    def __init__(self, *, readiness_responses=None, detail_payload=None):
        self.call_log = []
        self._mid = 0
        self._readiness_responses = list(readiness_responses or [])
        self._detail_payload = detail_payload if detail_payload is not None else _fake_detail_payload_default()
        self.closed = False
        self.cdp_port = None

    def send(self, method, params=None, sid=None, timeout=30):
        params = params or {}
        self._mid += 1
        self.call_log.append({"method": method, "params": params, "sid": sid})
        if method == "Target.createTarget":
            return {"result": {"targetId": f"target-{self._mid}"}}
        if method == "Target.attachToTarget":
            return {"result": {"sessionId": f"session-{self._mid}"}}
        if method in (
            "Page.addScriptToEvaluateOnNewDocument",
            "Page.navigate",
            "Target.closeTarget",
            "Input.dispatchMouseEvent",
        ):
            return {"result": {}}
        raise AssertionError(f"unexpected CDP method: {method}")

    def eval_js(self, js, sid):
        self.call_log.append({
            "method": "Runtime.evaluate",
            "params": {"expression": js},
            "sid": sid,
        })
        if "__boss_readiness_probe__" in js or "document.readyState" in js:
            if self._readiness_responses:
                return self._readiness_responses.pop(0)
            return "ready"
        return json.dumps(self._detail_payload)

    def close(self):
        self.closed = True


def _make_recording_sleeper():
    """Return ``(sleeper, calls)`` where ``calls`` records ``(seconds, label)``."""
    calls = []

    def sleeper(seconds, label=None):
        calls.append((float(seconds), label))

    return sleeper, calls
