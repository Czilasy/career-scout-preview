"""并发竞态红测试（第 2 波 T025）。

预期当前实现 fail：
- append_log / create_analysis / create_confirmation 的 MAX(seq|version)+1 → INSERT
  在 SQLite 默认 deferred 隔离下，两并发事务都能读到同样 MAX，第二个 INSERT
  撞 UNIQUE 约束抛 IntegrityError。
- save_job / link_profile_job 的 SELECT-then-INSERT/UPDATE 在并发下可能
  重复 INSERT（save_job 有 UNIQUE(canonical_url)，link_profile_job 有
  PRIMARY KEY(profile_id, job_id)）。

第 2 波 T027-T030b 修复后这些测试应变 GREEN。
"""

from __future__ import annotations

import tempfile
import threading
import unittest

from webui.store import TaskStore


class _ConcurrentStoreFixture(unittest.TestCase):
    """共享建表 + 创建基础 profile/resume 的 fixture。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TaskStore(f"{self.tmp.name}/concurrency.db")
        self.profile = self.store.create_profile("并发测试画像")
        self.resume = self.store.save_resume(
            self.profile["id"], "storage/path.pdf", "pdf",
            "示例简历文本", "hash-conc", "path.pdf",
        )

    def tearDown(self):
        self.store._close() if hasattr(self.store, "_close") else None
        self.tmp.cleanup()


class AppendLogConcurrentTests(_ConcurrentStoreFixture):
    """T027: append_log 并发追加同一 task_id。"""

    def test_two_threads_append_100_lines_each_no_integrity_error(self):
        # 先建一个 task，append_log 需要先 get_task 通过
        import uuid as _uuid_mod
        task_id = str(_uuid_mod.uuid4())
        self.store.create_task(
            task_id=task_id,
            kind="scrape",
            params={"keyword": "Python", "city": "北京"},
            output_path="/tmp/x.json",
        )

        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def worker():
            try:
                barrier.wait()
                for i in range(100):
                    self.store.append_log(task_id, f"line-{i}")
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # 当前实现会抛 sqlite3.IntegrityError（UNIQUE(task_id, seq) 撞）
        self.assertEqual(errors, [], f"append_log 并发抛错: {errors}")

        # seq 应为 1-200 全部唯一
        logs = self.store.get_logs(task_id)
        seqs = [log["seq"] for log in logs]
        self.assertEqual(len(seqs), 200, f"应得 200 条日志，实际 {len(seqs)}")
        self.assertEqual(len(set(seqs)), 200, "seq 出现重复")
        self.assertEqual(sorted(seqs), list(range(1, 201)))


class SaveJobConcurrentTests(_ConcurrentStoreFixture):
    """T030: save_job 并发 upsert 同一 canonical_url。"""

    def test_two_threads_save_same_canonical_url_only_one_row(self):
        # 多轮并发，每轮一对线程同时 save 同一 canonical_url
        # 累计 10 轮，应无 IntegrityError，且 jobs 表中该 url 只有 1 行
        canonical_url = "https://www.zhipin.com/job/1001.html"
        errors: list[Exception] = []
        lock = threading.Lock()
        NUM_ROUNDS = 10

        def worker(round_barrier: threading.Barrier, round_idx: int):
            try:
                round_barrier.wait()
                self.store.save_job(
                    canonical_url=canonical_url,
                    source_url=canonical_url,
                    title=f"Python 后端-{round_idx}",
                    company=f"公司-{threading.get_ident()}-{round_idx}",
                    salary="25-35K",
                    location="北京",
                    jd="岗位描述",
                )
            except Exception as exc:
                with lock:
                    errors.append(exc)

        for r in range(NUM_ROUNDS):
            barrier = threading.Barrier(2)
            t1 = threading.Thread(target=worker, args=(barrier, r))
            t2 = threading.Thread(target=worker, args=(barrier, r))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        # 当前实现：两线程都 SELECT 到 existing=None，都走 INSERT，第二个撞
        # UNIQUE(canonical_url) 抛 IntegrityError
        self.assertEqual(errors, [], f"save_job 并发抛错: {errors}")

        # 应只有 1 行 jobs 记录（canonical_url 唯一）
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT id, title, company FROM jobs WHERE canonical_url = ?",
                (canonical_url,),
            ).fetchall()
        self.assertEqual(len(rows), 1, f"应只有 1 行 jobs，实际 {len(rows)}")
        # title/company 应非空（不被空值覆盖）
        self.assertTrue(rows[0]["title"], "title 不应为空")
        self.assertTrue(rows[0]["company"], "company 不应为空")


class LinkProfileJobConcurrentTests(_ConcurrentStoreFixture):
    """T030b: link_profile_job 并发 link 同一 (profile_id, job_id)。"""

    def test_two_threads_link_same_profile_job_only_one_row(self):
        # 先建一个 job 供 link
        job = self.store.save_job(
            canonical_url="https://www.zhipin.com/job/2002.html",
            source_url="https://www.zhipin.com/job/2002.html",
            title="后端", company="公司A", salary="20K",
            location="上海", jd="jd",
        )
        run = self.store.create_search_run(
            profile_id=self.profile["id"],
            profile_snapshot={"keyword": "Python"},
            mode="list",
        )

        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def worker():
            try:
                barrier.wait()
                self.store.link_profile_job(
                    profile_id=self.profile["id"],
                    job_id=job["id"],
                    first_run_id=run["id"],
                    last_run_id=run["id"],
                )
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # 当前实现可能撞 PRIMARY KEY(profile_id, job_id) 抛 IntegrityError
        self.assertEqual(errors, [], f"link_profile_job 并发抛错: {errors}")

        # 应只有 1 行 profile_jobs 记录
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM profile_jobs WHERE profile_id = ? AND job_id = ?",
                (self.profile["id"], job["id"]),
            ).fetchall()
        self.assertEqual(len(rows), 1, f"应只有 1 行 profile_jobs，实际 {len(rows)}")


if __name__ == "__main__":
    unittest.main()
