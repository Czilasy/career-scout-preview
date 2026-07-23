"""数据库索引红测试（第 2 波 T026）。

预期当前实现 fail：
- cleanup_expired_jobs 的 SQL 会全表扫 jobs（无 expires_at 索引）
- discovery_job_snapshots WHERE run_id=? AND fetch_status=? 无复合索引，全表扫

第 2 波 T035 创建索引后应变 GREEN。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest

from webui.store import TaskStore


class IndexExistenceTests(unittest.TestCase):
    """断言关键索引存在，避免 EXPLAIN QUERY PLAN 出现全表扫。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TaskStore(f"{self.tmp.name}/indexes.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _explain(self, sql: str, params=()):
        """跑 EXPLAIN QUERY PLAN，返回所有 detail 行的拼接文本。"""
        with self.store._connection() as conn:
            rows = conn.execute(
                f"EXPLAIN QUERY PLAN {sql}", params,
            ).fetchall()
        return " | ".join(str(row["detail"]) for row in rows)

    def test_cleanup_expired_jobs_uses_index_on_expires_at(self):
        # cleanup_expired_jobs 的 SQL 查 jobs.expires_at < cutoff
        # 期望 T035 新增 partial 索引 idx_jobs_expires_at（WHERE expires_at IS NOT NULL）
        # 注：EXPLAIN QUERY PLAN 在空表上不可靠（优化器可能选全表扫），
        # 因此改为断言索引存在而非 EXPLAIN 命中。
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='jobs' AND name='idx_jobs_expires_at'",
            ).fetchall()
        self.assertEqual(
            len(rows), 1,
            "应存在 partial 索引 idx_jobs_expires_at（WHERE expires_at IS NOT NULL），"
            "当前缺失，cleanup_expired_jobs JOIN jobs ON expires_at < cutoff 会全表扫",
        )

    def test_discovery_job_snapshots_run_status_uses_composite_index(self):
        # discovery_runner.py 常按 (run_id, fetch_status) 查 snapshots
        # 当前只有 UNIQUE(run_id, job_id) 自动索引，run_id 命中后还要 filter fetch_status
        # 期望 T035 新增复合索引 idx_discovery_job_snapshots_run_status (run_id, fetch_status)
        # 让查询直接两列定位
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='discovery_job_snapshots' AND name='idx_discovery_job_snapshots_run_status'",
            ).fetchall()
        self.assertEqual(
            len(rows), 1,
            "应存在复合索引 idx_discovery_job_snapshots_run_status(run_id, fetch_status)，"
            "当前缺失，discovery_runner 按 (run_id, fetch_status) 查询时需在 run_id 命中后逐行 filter",
        )


if __name__ == "__main__":
    unittest.main()
