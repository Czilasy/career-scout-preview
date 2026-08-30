"""历史恢复手动工具测试（031 B7 自 tests/healthy_pipeline 迁出）。

恢复能力已退出生产 API 面（``/api/recovery/*`` 三条路由撤除），落位
``scripts/maintenance/historical_recovery.py``。本文件直接调工具层与 CLI，
不再经 HTTP；断言只随调用路径迁移，不放松（含新增的 ``--confirm`` 安全栏）。
"""

import contextlib
import io
import json
import pathlib
import tempfile
import unittest

from scripts.maintenance import historical_recovery as hr
from webui.store import TaskStore


class _HistoricalDataTestCase(unittest.TestCase):
    """构造与正式库结构一致的两个历史 run 测试数据。"""

    # 子类可覆盖为真实历史 run id（CLI 默认预演的就是内置常量那两个 run）。
    rough_id = "rough-test-run"
    fine_id = "fine-test-run"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.temp_root = pathlib.Path(self.temp.name)
        self.db_path = self.temp_root / "webui.db"
        self.store = TaskStore(str(self.db_path))
        self.result_dir = self.temp_root / "results"
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self._seed_historical_data()

    def _seed_historical_data(self):
        """构造与正式库结构一致的测试数据（不再用结构相反的合成数据）。

        正式库实测结构（2026-07-28）：
        15847d27（粗筛 run，1926 条）：
          - 纯字符串 verdict（正常 1876 条）：match=198, not_match=514, uncertain=646, dropped=518
          - JSON verdict（异常 50 条）：inner match=17, inner not_match=33
          - JD 非空 762 条（198 match + 514 not_match + 17 JSON match + 33 JSON not_match）
        e6250f0e（精筛 run，762 条）：
          - 全部 JSON verdict：inner match=198, inner not_match=514, inner uncertain=50
          - JD 非空 0 条（精筛不抓 JD）
        守恒律：1926=518+1408, 1408=762+646, 696=646+50
        """
        rough_id = self.rough_id
        fine_id = self.fine_id
        self.store.create_screening_run(rough_id, source_count=1926)
        self.store.update_screening_run(
            rough_id, status="succeeded",
            total_dropped=518, total_kept=1408, total_scraped=1926)
        self.store.create_screening_run(fine_id, source_count=1408)
        self.store.update_screening_run(
            fine_id, status="succeeded",
            total_dropped=0, total_kept=762, total_scraped=1408)
        with self.store._connection() as conn:
            for i in range(198):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, jd, created_at) "
                    "VALUES (?, ?, ?, 'match', ?, ?)",
                    (f"r-pm{i}", rough_id, f"job-pm{i}",
                     f"JD content for match job {i}", "2026-07-28"))
            for i in range(514):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, jd, created_at) "
                    "VALUES (?, ?, ?, 'not_match', ?, ?)",
                    (f"r-pn{i}", rough_id, f"job-pn{i}",
                     f"JD content for not_match job {i}", "2026-07-28"))
            for i in range(646):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, created_at) "
                    "VALUES (?, ?, ?, 'uncertain', ?)",
                    (f"r-u{i}", rough_id, f"job-u{i}", "2026-07-28"))
            for i in range(518):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, created_at) "
                    "VALUES (?, ?, ?, 'dropped', ?)",
                    (f"r-d{i}", rough_id, f"job-d{i}", "2026-07-28"))
            for i in range(17):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, jd, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (f"r-jm{i}", rough_id, f"job-jm{i}",
                     json.dumps({"verdict": "match", "reason": "ok"}),
                     f"JD content for JSON match job {i}", "2026-07-28"))
            for i in range(33):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, jd, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (f"r-jn{i}", rough_id, f"job-jn{i}",
                     json.dumps({"verdict": "not_match", "reason": "no"}),
                     f"JD content for JSON not_match job {i}", "2026-07-28"))
            for i in range(198):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"f-m{i}", fine_id, f"job-pm{i}",
                     json.dumps({"verdict": "match", "reason": "ok"}), "2026-07-28"))
            for i in range(514):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"f-n{i}", fine_id, f"job-pn{i}",
                     json.dumps({"verdict": "not_match", "reason": "no"}), "2026-07-28"))
            for i in range(17):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"f-cm{i}", fine_id, f"job-jm{i}",
                     json.dumps({"verdict": "uncertain",
                                 "reason": "AI 响应超时，请稍后重试，待人工确认"}),
                     "2026-07-28"))
            for i in range(33):
                conn.execute(
                    "INSERT INTO screening_results (id, run_id, platform_job_id, verdict, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"f-cn{i}", fine_id, f"job-jn{i}",
                     json.dumps({"verdict": "uncertain",
                                 "reason": "AI 响应超时，请稍后重试，待人工确认"}),
                     "2026-07-28"))


    def _preview(self):
        return hr.preview_recovery(
            self.store, rough_run_id=self.rough_id,
            fine_run_id=self.fine_id, result_dir=self.result_dir,
        )


class HistoricalRecoveryPreviewTests(_HistoricalDataTestCase):
    """预演只读性与门禁判定（原 Slice10 用例，直调工具层）。"""

    def test_preview_does_not_write(self):
        """预演不写正式数据库（FR-041）。"""
        before = self.store.get_screening_run(self.rough_id)
        self._preview()
        after = self.store.get_screening_run(self.rough_id)
        self.assertEqual(before, after, "预演不得修改数据库")

    def test_preview_15847d27_50_split_17_33(self):
        """15847d27 的 50 条 JSON verdict 识别为 inner 17 match + 33 not_match。

        注意：50 条异常是 JSON verdict 的 inner 分布，不是纯字符串。
        15847d27 的纯字符串 verdict（198 match + 514 not_match）是正常数据，严禁改写。
        """
        result = self._preview()
        r50 = result["rough_50_json"]
        self.assertEqual(r50["match"], 17)
        self.assertEqual(r50["not_match"], 33)
        self.assertEqual(r50["total"], 50)
        self.assertTrue(r50["has_valid_verdict"])
        self.assertEqual(r50["verdict_format"], "json_inner")
        plain = result["rough_run"]["plain_verdicts"]
        self.assertEqual(plain.get("match", 0), 198)
        self.assertEqual(plain.get("not_match", 0), 514)
        self.assertEqual(plain.get("uncertain", 0), 646)
        self.assertEqual(plain.get("dropped", 0), 518)

    def test_preview_e6250f0e_50_uncertain(self):
        """e6250f0e 的 50 条识别为 uncertain（AI 超时）。"""
        result = self._preview()
        unc = result["fine_50_uncertain"]
        self.assertEqual(unc["count"], 50)
        self.assertFalse(unc["has_valid_verdict"])
        self.assertIn("超时", unc["reason"])

    def test_preview_646_identified_not_split_30_8_608(self):
        """646 条识别且不猜测 30/8/608（FR-041）。"""
        result = self._preview()
        pending = result["pending_646"]
        self.assertEqual(pending["count"], 646)
        self.assertTrue(pending["cannot_split_30_8_608"])

    def test_preview_conservation_check(self):
        """守恒核对通过（1926 = 518 + 1408 = 518 + 762 + 646）。"""
        result = self._preview()
        cons = result["conservation"]
        self.assertTrue(cons["sum_dropped_kept_ok"], "dropped+kept 必须等于 source")
        self.assertTrue(cons["sum_fine_pending_ok"], "fine+pending 必须等于 kept")
        self.assertTrue(cons["anomaly_ok"], "696 = 646 + 50")
        self.assertTrue(cons["all_ok"])

    def test_preview_gate_passed(self):
        """门禁全部通过。"""
        result = self._preview()
        gate = result["gate_passed"]
        self.assertTrue(gate["all_passed"], f"门禁未通过: {gate}")

    def test_recovery_gate_blocks_if_numbers_mismatch(self):
        """数字不一致时门禁阻断恢复（FR-041）。

        删掉一条 fine uncertain 行，让 fine_50_uncertain=49（!=50），
        触发硬检查失败。
        """
        with self.store._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM screening_results WHERE id = 'f-cm0'")
            self.assertEqual(cursor.rowcount, 1, "测试必须真实破坏一条 fixture 数据")
        result = self._preview()
        self.assertFalse(result["gate_passed"]["all_passed"])
        self.assertFalse(result["written"])


class HistoricalRecoveryCliTests(_HistoricalDataTestCase):
    """CLI 三子命令：preview 输出等价、execute 需 --confirm 才写库。

    CLI 的 preview 预演工具内置的两个历史 run（契约 recovery-cli.md），
    故本类用真实 run id 播种，可直接核对门禁输出。
    """

    rough_id = hr.ROUGH_RUN_ID
    fine_id = hr.FINE_RUN_ID

    def test_cli_preview_prints_gate_evidence(self):
        """preview 子命令输出 JSON 门禁证据（与原 API 的 preview 载荷同源）。"""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = hr.main(["preview", "--db", str(self.db_path)])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["gate_passed"]["all_passed"], payload["gate_passed"])
        self.assertEqual(payload["pending_646"]["count"], 646)
        self.assertFalse(payload["written"], "preview 绝不写库")

    def test_cli_prepare_writes_backup_and_manifest(self):
        """prepare 子命令生成库备份与不可变 manifest，输出 backup_id。"""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = hr.main(["prepare", "--db", str(self.db_path)])
        self.assertEqual(code, 0, out.getvalue())
        payload = json.loads(out.getvalue())
        backup_id = payload["backup_id"]
        manifest = self.temp_root / "backups" / backup_id / "manifest.json"
        self.assertTrue(manifest.is_file(), "manifest 必须落盘")
        self.assertTrue((manifest.parent / "webui.db").is_file(), "备份库必须落盘")
        stored = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(stored["status"], "prepared")
        self.assertEqual(stored["backup_id"], backup_id)

    def test_cli_execute_requires_confirm_and_does_not_write(self):
        """缺 --confirm 时拒绝执行且写库零发生（031 B7 新增安全栏）。"""
        before = self.store.get_screening_run(self.rough_id)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = hr.main([
                "execute", "--backup-id", "deadbeef", "--db", str(self.db_path),
            ])
        self.assertEqual(code, 2, "缺 --confirm 必须按参数/校验失败退出")
        self.assertIn("--confirm", err.getvalue())
        after = self.store.get_screening_run(self.rough_id)
        self.assertEqual(before, after, "缺 --confirm 时绝不允许写库")

    def test_cli_execute_unknown_backup_returns_validation_exit_code(self):
        """未知 backup_id 属数据校验失败，退出码 3（契约 recovery-cli.md）。"""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = hr.main([
                "execute", "--backup-id", "0" * 32, "--confirm",
                "--db", str(self.db_path),
            ])
        self.assertEqual(code, 3, out.getvalue())
        self.assertEqual(json.loads(out.getvalue())["error"], "unknown_backup_id")


if __name__ == "__main__":
    unittest.main()
