"""026 B079：结果文件 os.replace 写失败重试与专门异常。

``write_json_atomic`` 在 Windows 下偶发因目标文件被占用抛 OSError；
修复后应短暂重试（偶发占用重试即过），重试耗尽抛专门异常
``ResultFileWriteError``（与登录失效严格区分，spec FR-006/FR-007/FR-008）。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.boss.exceptions import ResultFileWriteError
from scripts.boss.output import flush_jobs, write_json_atomic


class WriteJsonAtomicRetryTests(unittest.TestCase):
    def test_oserror_once_then_retry_succeeds(self):
        """T010：os.replace 首次抛 OSError → 重试成功，文件完整、tmp 清理（FR-006）。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.json")
            real_replace = os.replace
            calls = {"n": 0}

            def flaky_replace(src, dst):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("目标文件被占用")
                return real_replace(src, dst)

            with mock.patch(
                "scripts.boss.output.os.replace", side_effect=flaky_replace,
            ):
                write_json_atomic(path, {"jobs": [{"job_id": "a"}]})

            self.assertGreaterEqual(calls["n"], 2)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["jobs"][0]["job_id"], "a")
            self.assertEqual(list(Path(tmp).glob("*.tmp-*")), [])

    def test_exhausted_retries_raise_result_file_write_error(self):
        """T011：重试耗尽 → 抛 ResultFileWriteError（非裸 OSError），tmp 清理（FR-008）。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.json")
            with mock.patch(
                "scripts.boss.output.os.replace",
                side_effect=OSError("持续占用"),
            ):
                with self.assertRaises(ResultFileWriteError):
                    write_json_atomic(path, {"jobs": []})
            self.assertEqual(list(Path(tmp).glob("*.tmp-*")), [])

    def test_flush_jobs_merge_unchanged_after_retry(self):
        """T012：flush_jobs 合并逻辑不受重试影响——重试成功后 jobs 去重、total 正确（FR-006）。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.json")
            meta = {"total": 0}
            real_replace = os.replace
            calls = {"n": 0}

            def flaky_replace(src, dst):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("目标文件被占用")
                return real_replace(src, dst)

            with mock.patch(
                "scripts.boss.output.os.replace", side_effect=flaky_replace,
            ):
                flush_jobs(path, meta, [{"job_id": "a"}, {"job_id": "b"}])
            with mock.patch(
                "scripts.boss.output.os.replace", side_effect=real_replace,
            ):
                flush_jobs(path, meta, [{"job_id": "b"}, {"job_id": "c"}])

            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(
                [j["job_id"] for j in data["jobs"]], ["a", "b", "c"],
            )
            self.assertEqual(data["total"], 3)
            self.assertEqual(list(Path(tmp).glob("*.tmp-*")), [])


if __name__ == "__main__":
    unittest.main()
