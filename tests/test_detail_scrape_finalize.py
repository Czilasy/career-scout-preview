"""回归测试：detail_scrape 并行路径的收尾超时逻辑。

背景（026 之后发现，根因已核实）：
- 上一会话把收尾 t.join() 从无限等待改成 90 秒共享 deadline，且 join 在
  worker 启动后立即开始（覆盖整个抓取过程而非收尾），导致稳定/平衡档详情
  抓取必然 >90s 被强制中断 → 抛 RuntimeError → 退出码 1 → source_unknown_error
  → 大量岗位「未抓到 JD」。
- 本测试验证修复后行为：
  1. 正常抓取完整完成、所有岗位 JD 完整、不抛异常。
  2. 超时（finalize_timeout 很小）只记录、不抛致命错，已抓结果保留。
  3. 超时后已抓结果已原子落盘。
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

# worker 线程的 print 含 ⟳ 等非 GBK 字符，测试 stdout 重定向为 utf-8
sys.stdout.reconfigure(encoding="utf-8")

import scripts.boss.detail_scrape as ds


class _FakeSession:
    """scrape_details 并行路径的 CDP 会话替身（参考 test_boss_programmatic）。"""

    def __init__(self):
        self._mid = 0
        self.closed = False
        self.call_log = []

    def send(self, method, params=None, sid=None, timeout=30):
        params = params or {}
        self._mid += 1
        self.call_log.append({"method": method, "params": params})
        if method == "Target.createTarget":
            return {"result": {"targetId": f"target-{self._mid}"}}
        if method == "Target.attachToTarget":
            return {"result": {"sessionId": f"session-{self._mid}"}}
        return {"result": {}}

    def eval_js(self, js, sid):
        self.call_log.append({"method": "Runtime.evaluate", "params": {"expression": js}})
        if "__boss_readiness_probe__" in js or "document.readyState" in js:
            return "ready"
        return json.dumps({"jd": "岗位描述 " + ("后端服务开发参与系统架构设计。 " * 12), "tags": ["Python"]})

    def close(self):
        self.closed = True


def _make_jobs(n):
    return [
        {
            "title": f"Job-{i}",
            "job_link": f"https://www.zhipin.com/job_detail/enc{i}.html",
            "salary": "20-30K",
            "location": "上海",
        }
        for i in range(n)
    ]


def _no_sleep(seconds, label=None):
    return None


def _slow_sleep(seconds, label=None):
    time.sleep(seconds)


def _run(list_data, *, tab_pool_size=2, finalize_timeout=600, sleeper=_no_sleep):
    facade = mock.Mock()
    facade._run_active = False
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "details.json")
        with mock.patch.object(ds, "_facade", return_value=facade):
            results = ds.scrape_details(
                list_data, output_path=out,
                enable_parallel=True, tab_pool_size=tab_pool_size,
                session_factory=lambda cdp_port=None: _FakeSession(),
                sleeper=sleeper,
                inter_job_gap_range=(0.02, 0.05),
                finalize_timeout=finalize_timeout,
            )
        exists = os.path.exists(out)
        persisted = 0
        if exists:
            with open(out, encoding="utf-8") as f:
                persisted = len(json.load(f))
        return results, exists, persisted


class DetailScrapeFinalizeRegressionTests(unittest.TestCase):

    def test_normal_scrape_completes_all_jobs_with_jd(self):
        """正常抓取（在 finalize_timeout 内完成）不截断，所有岗位 JD 完整、不抛异常。"""
        jobs = _make_jobs(8)
        results, exists, persisted = _run({"jobs": jobs}, tab_pool_size=2)
        self.assertEqual(len(results), 8)
        for r in results:
            self.assertTrue(str(r.get("jd", "")).strip(), f"job jd 缺失: {r}")
        self.assertTrue(exists)
        self.assertEqual(persisted, 8)

    def test_finalize_timeout_records_without_raising(self):
        """finalize_timeout 很小 + 慢抓取 → 超时只记录、不抛 RuntimeError，已抓结果保留。"""
        jobs = _make_jobs(12)
        results, exists, persisted = _run(
            {"jobs": jobs},
            tab_pool_size=2,
            finalize_timeout=1.5,
            sleeper=_slow_sleep,
        )
        # 修复前：超时抛 RuntimeError → 本测试直接失败。
        # 修复后：不抛，且已抓结果非空、已落盘。
        self.assertGreater(len(results), 0)
        self.assertTrue(exists)
        self.assertGreater(persisted, 0)

    def test_finalize_timeout_results_persisted(self):
        """超时后已抓结果已原子落盘（output 文件存在且非空）。"""
        jobs = _make_jobs(12)
        _, exists, persisted = _run(
            {"jobs": jobs},
            tab_pool_size=2,
            finalize_timeout=1.5,
            sleeper=_slow_sleep,
        )
        self.assertTrue(exists)
        self.assertGreater(persisted, 0)


if __name__ == "__main__":
    unittest.main()
