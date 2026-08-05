"""定向验证：限流时停整个运行 + 限流页不关闭 + 不再开新页面。

用 fake CDP 会话模拟：第 1 个岗位详情页是限流页。
断言（串行路径）：
1. scrape_details 抛 RiskControlError（运行级停止）
2. 限流页未被 closeTarget（留在屏幕上）
3. 第 2 个岗位从未被 createTarget（不再开新页面）
4. 错误信息含"限流"字样（webui 退出码 10 分类依赖）
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "boss_cdp_raw.py"
    spec = importlib.util.spec_from_file_location("boss_cdp_raw_probe", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RATE_LIMIT_PAGE_TEXT = "操作频繁，请稍后再试\n您的账号访问受限，请解锁后继续"


class FakeSession:
    """串行详情路径的 fake CDP 会话：详情页一律返回限流页文本。"""

    def __init__(self, cdp_port=None):
        self.calls = []
        self.targets = 0

    def send(self, method, params=None, sid=None, timeout=30):
        self.calls.append((method, params or {}))
        if method == "Target.createTarget":
            self.targets += 1
            return {"result": {"targetId": f"tid-{self.targets}"}}
        if method == "Target.attachToTarget":
            return {"result": {"sessionId": "sid-1"}}
        return {"result": {}}

    def eval_js(self, js, sid):
        if "readyState" in js:  # readiness 探针
            return "ready"
        # 详情提取：无 JD + 限流文案 → 触发 DetailRateLimitedError
        return json.dumps({"jd": "", "page_text": RATE_LIMIT_PAGE_TEXT,
                           "tags": [], "url": "https://www.zhipin.com/x"})

    def close(self):
        pass


class RateLimitStopRunTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.session = FakeSession()
        self.jobs = [
            {"job_link": f"https://www.zhipin.com/job_detail/{i}.html",
             "title": f"岗位{i}", "boss_name": "某公司"}
            for i in range(3)
        ]

    def test_rate_limit_stops_run_and_keeps_page_open(self):
        module = self.module
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "details.json"
            with self.assertRaises(module.RiskControlError) as ctx:
                module.scrape_details(
                    {"jobs": self.jobs}, output_path=str(output),
                    session_factory=lambda cdp_port=None: self.session,
                    sleeper=lambda s, label=None: None,
                    event_callback=events.append,
                )
        # 1) 错误信息含限流字样（退出码 10 分类链路依赖）
        self.assertIn("限流", str(ctx.exception))
        # 2) 只开了 1 个页面：第 1 个撞限流就停，第 2/3 个从未开页
        self.assertEqual(self.session.targets, 1,
                         "限流后不得继续开新页面")
        # 3) 限流页未被关闭：没有任何 closeTarget
        closed = [p for m, p in self.session.calls if m == "Target.closeTarget"]
        self.assertEqual(closed, [], "限流页必须留在屏幕上")
        # 4) 该岗位恰好一个 terminal safe event
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["safe_code"], "source_rate_limited")
        self.assertEqual(events[0]["status"], "failed")

    def test_parallel_rate_limit_keeps_tab_and_exits_via_risk_error(self):
        """并行 tab 池路径：限流 → degrade + 该 tab 不关 + RiskControlError。"""
        module = self.module
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "details.json"
            with self.assertRaises(module.RiskControlError) as ctx:
                module.scrape_details(
                    {"jobs": self.jobs}, output_path=str(output),
                    session_factory=lambda cdp_port=None: FakeSession(),
                    sleeper=lambda s, label=None: None,
                    enable_parallel=True, tab_pool_size=2,
                    stagger_range=(0, 0), inter_job_gap_range=(0, 0),
                )
        self.assertIn("限流", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
