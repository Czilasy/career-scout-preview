"""数据库索引存在性测试。

断言 cleanup_expired_jobs 依赖的 jobs.expires_at partial 索引存在。
空表上 EXPLAIN QUERY PLAN 不可靠（优化器可能选全表扫），
故直接查 sqlite_master 断言索引存在，而非断言查询计划命中。
"""

from __future__ import annotations

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



if __name__ == "__main__":
    unittest.main()
