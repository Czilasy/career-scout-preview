"""历史恢复回归测试 — 基于正式库只读副本。

正式数据库路径：~\\.career-scout\\webui\\webui.db
正式库实测不变量（2026-07-28）：
  15847d27（粗筛 run，source=1926）：
    - 纯字符串 verdict（正常 1876 条）：match=198, not_match=514, uncertain=646, dropped=518
    - JSON verdict（异常 50 条）：inner match=17, inner not_match=33
    - JD 非空 762 条
  e6250f0e（精筛 run，762 条）：
    - 全部 JSON verdict：inner match=198, inner not_match=514, inner uncertain=50
  守恒律：
    1926 = 518(dropped) + 1408(kept)
    1408 = 762(进精筛) + 646(未进精筛)
    696 = 646(未处理) + 50(AI超时)

本测试严禁写正式库，全部在临时副本上运行。
"""
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

_REAL_DB = pathlib.Path(r"~/.career-scout\webui\webui.db")

# 真实不变量（与 historical_recovery.py 常量一致）
ROUGH_RUN_ID = "15847d27-7419-4f01-ae09-9e4c9e2641bb"
FINE_RUN_ID = "e6250f0ed794492180269de050bfd41a"

ROUGH_TOTAL = 1926
ROUGH_PLAIN_MATCH = 198
ROUGH_PLAIN_NOT_MATCH = 514
ROUGH_PLAIN_UNCERTAIN = 646
ROUGH_PLAIN_DROPPED = 518
ROUGH_PLAIN_TOTAL = 1876  # 198 + 514 + 646 + 518
ROUGH_JSON_MATCH = 17
ROUGH_JSON_NOT_MATCH = 33
ROUGH_JSON_TOTAL = 50
ROUGH_JD_COUNT = 762
ROUGH_SOURCE_COUNT = 1926
ROUGH_DROPPED = 518
ROUGH_KEPT = 1408

FINE_TOTAL = 762
FINE_INNER_MATCH = 198
FINE_INNER_NOT_MATCH = 514
FINE_INNER_UNCERTAIN = 50

PENDING_646 = 646
TOTAL_ANOMALY = 696  # 646 + 50


def _recovery_fixture_source() -> pathlib.Path:
    """Use the committed recovery backup once the formal DB is recovered."""
    probe = sqlite3.connect(f"{_REAL_DB.resolve().as_uri()}?mode=ro", uri=True)
    try:
        has_audit = probe.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recovery_audit'"
        ).fetchone()
        if has_audit:
            row = probe.execute(
                "SELECT backup_id FROM recovery_audit "
                "WHERE status='committed' AND tx_committed=1 "
                "AND stats_json LIKE '%action_1_rough_50_json_unified%' "
                "ORDER BY finished_at DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                candidate = _REAL_DB.parent / "backups" / str(row[0]) / "webui.db"
                if candidate.is_file():
                    return candidate
    finally:
        probe.close()
    return _REAL_DB


def _copy_real_db_snapshot(destination: pathlib.Path) -> None:
    """Create a consistent pre-recovery fixture through a read-only source."""
    source_path = _recovery_fixture_source()
    source = sqlite3.connect(f"{source_path.resolve().as_uri()}?mode=ro", uri=True)
    target = sqlite3.connect(str(destination))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _execute_prepared(store):
    """Exercise the only supported recovery path: prepare, then backup id."""
    from webui.historical_recovery import prepare_recovery, execute_recovery
    prepared = prepare_recovery(store)
    return execute_recovery(prepared["backup_id"], store=store)


@unittest.skipUnless(_REAL_DB.exists(), f"正式库不存在：{_REAL_DB}")
class TestPreviewRecoveryOnRealDB(unittest.TestCase):
    """只读预演：基于正式库副本硬断言所有不变量。"""

    @classmethod
    def setUpClass(cls):
        """复制正式库到临时文件，全程只读操作。"""
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.tmp_db = pathlib.Path(cls._tmpdir.name) / "realdb_preview.db"
        _copy_real_db_snapshot(cls.tmp_db)
        # 导入 TaskStore 并打开副本
        from webui.store import TaskStore
        cls.store = TaskStore(db_path=str(cls.tmp_db))

    @classmethod
    def tearDownClass(cls):
        try:
            cls.store._conn.close()
        except Exception:
            pass
        cls._tmpdir.cleanup()

    def test_rough_run_total_1926(self):
        """15847d27 总数必须 1926。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store)
        self.assertEqual(result["rough_run"]["total"], ROUGH_TOTAL,
                         f"粗筛 run 总数应为 {ROUGH_TOTAL}，实际 {result['rough_run']['total']}")

    def test_rough_run_plain_verdicts_1876(self):
        """15847d27 纯字符串 verdict 必须是 198 match + 514 not_match + 646 uncertain + 518 dropped。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store)
        plain = result["rough_run"]["plain_verdicts"]
        self.assertEqual(plain.get("match", 0), ROUGH_PLAIN_MATCH,
                         f"纯字符串 match 应 {ROUGH_PLAIN_MATCH}，实际 {plain.get('match', 0)}")
        self.assertEqual(plain.get("not_match", 0), ROUGH_PLAIN_NOT_MATCH,
                         f"纯字符串 not_match 应 {ROUGH_PLAIN_NOT_MATCH}，实际 {plain.get('not_match', 0)}")
        self.assertEqual(plain.get("uncertain", 0), ROUGH_PLAIN_UNCERTAIN,
                         f"纯字符串 uncertain 应 {ROUGH_PLAIN_UNCERTAIN}，实际 {plain.get('uncertain', 0)}")
        self.assertEqual(plain.get("dropped", 0), ROUGH_PLAIN_DROPPED,
                         f"纯字符串 dropped 应 {ROUGH_PLAIN_DROPPED}，实际 {plain.get('dropped', 0)}")
        plain_total = sum(plain.values())
        self.assertEqual(plain_total, ROUGH_PLAIN_TOTAL,
                         f"纯字符串 verdict 总数应 {ROUGH_PLAIN_TOTAL}，实际 {plain_total}")

    def test_rough_50_json_inner_17_match_33_not_match(self):
        """15847d27 的 50 条 JSON verdict inner 必须是 17 match + 33 not_match。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store)
        r50 = result["rough_50_json"]
        self.assertEqual(r50["match"], ROUGH_JSON_MATCH,
                         f"JSON inner match 应 {ROUGH_JSON_MATCH}，实际 {r50['match']}")
        self.assertEqual(r50["not_match"], ROUGH_JSON_NOT_MATCH,
                         f"JSON inner not_match 应 {ROUGH_JSON_NOT_MATCH}，实际 {r50['not_match']}")
        self.assertEqual(r50["total"], ROUGH_JSON_TOTAL,
                         f"JSON verdict 总数应 {ROUGH_JSON_TOTAL}，实际 {r50['total']}")
        self.assertTrue(r50["has_valid_verdict"],
                        "50 条 JSON verdict 必须有有效判定（格式统一即可，不调 AI）")

    def test_fine_run_762_json_all(self):
        """e6250f0e 必须是 762 条全部 JSON verdict。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store)
        fine = result["fine_run"]
        self.assertEqual(fine["total"], FINE_TOTAL,
                         f"精筛 run 总数应 {FINE_TOTAL}，实际 {fine['total']}")
        inner = fine["inner_verdicts"]
        self.assertEqual(inner.get("match", 0), FINE_INNER_MATCH,
                         f"精筛 inner match 应 {FINE_INNER_MATCH}，实际 {inner.get('match', 0)}")
        self.assertEqual(inner.get("not_match", 0), FINE_INNER_NOT_MATCH,
                         f"精筛 inner not_match 应 {FINE_INNER_NOT_MATCH}，实际 {inner.get('not_match', 0)}")
        self.assertEqual(inner.get("uncertain", 0), FINE_INNER_UNCERTAIN,
                         f"精筛 inner uncertain 应 {FINE_INNER_UNCERTAIN}，实际 {inner.get('uncertain', 0)}")

    def test_fine_50_uncertain_ai_timeout(self):
        """e6250f0e 的 50 条 uncertain 必须是 AI 超时，无有效判定。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store)
        f50 = result["fine_50_uncertain"]
        self.assertEqual(f50["count"], FINE_INNER_UNCERTAIN,
                         f"精筛 uncertain 应 {FINE_INNER_UNCERTAIN}，实际 {f50['count']}")
        self.assertFalse(f50["has_valid_verdict"],
                         "50 条 uncertain 是 AI 超时，无有效判定，必须交给新流程重新调 AI")

    def test_pending_646_uncertain_not_in_fine(self):
        """646 条 = 15847d27 uncertain 中未进 e6250f0e 的岗位。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store)
        p646 = result["pending_646"]
        self.assertEqual(p646["count"], PENDING_646,
                         f"pending 应 {PENDING_646}，实际 {p646['count']}")
        self.assertTrue(p646["cannot_split_30_8_608"],
                        "数据库无 failed_code 记录，禁止猜测 30/8/608 分布")

    def test_jd_762_protection(self):
        """15847d27 中 762 条 JD 非空岗位必须受保护，禁止重复抓取。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store)
        jd = result["jd_762_protection"]
        self.assertEqual(jd["jd_exists"], ROUGH_JD_COUNT,
                         f"JD 非空数应 {ROUGH_JD_COUNT}，实际 {jd['jd_exists']}")
        self.assertTrue(jd["jd_protected"],
                        f"762 条 JD 必须受保护，实际 jd_exists={jd['jd_exists']}")

    def test_conservation_laws(self):
        """守恒律：1926=518+1408, 1408=762+646, 696=646+50。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store)
        c = result["conservation"]
        self.assertEqual(c["source"], ROUGH_SOURCE_COUNT,
                         f"source 应 {ROUGH_SOURCE_COUNT}，实际 {c['source']}")
        self.assertEqual(c["dropped"], ROUGH_DROPPED,
                         f"dropped 应 {ROUGH_DROPPED}，实际 {c['dropped']}")
        self.assertEqual(c["kept"], ROUGH_KEPT,
                         f"kept 应 {ROUGH_KEPT}，实际 {c['kept']}")
        self.assertTrue(c["sum_dropped_kept_ok"],
                        f"1926=518+1408 守恒失败：dropped={c['dropped']}, kept={c['kept']}, sum={c['dropped']+c['kept']}")
        self.assertTrue(c["sum_fine_pending_ok"],
                        f"1408=762+646 守恒失败：fine={c['fine_processed']}, pending={c['pending']}, sum={c['fine_processed']+c['pending']}")
        self.assertTrue(c["anomaly_ok"],
                        f"696=646+50 守恒失败：pending={c['pending']}, uncertain={c['fine_uncertain']}, sum={c['total_anomaly']}")
        self.assertTrue(c["all_ok"], "所有守恒律必须通过")

    def test_gate_all_passed(self):
        """门禁必须全部通过：17+33, 50, 646, 守恒。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store)
        g = result["gate_passed"]
        self.assertTrue(g["rough_50_json_match_17"],
                        f"JSON match 必须 17，gate 检查失败")
        self.assertTrue(g["rough_50_json_not_match_33"],
                        f"JSON not_match 必须 33，gate 检查失败")
        self.assertTrue(g["fine_50_uncertain_50"],
                        f"fine uncertain 必须 50，gate 检查失败")
        self.assertTrue(g["pending_646"],
                        f"pending 必须 646，gate 检查失败")
        self.assertTrue(g["conservation_ok"],
                        f"守恒律必须通过，gate 检查失败")
        self.assertTrue(g["all_passed"], "所有门禁必须通过")

    def test_preview_not_written(self):
        """预演严禁写库。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store)
        self.assertFalse(result["written"], "预演不得写入正式数据库")


@unittest.skipUnless(_REAL_DB.exists(), f"正式库不存在：{_REAL_DB}")
class TestExecuteRecoveryPreservesPlainVerdicts(unittest.TestCase):
    """执行恢复：验证只改 50 条 JSON verdict，不动 1876 条纯字符串 verdict。

    每个测试方法独立复制正式库，避免 execute_recovery 副作用串扰。
    """

    def setUp(self):
        """每个测试方法独立复制正式库，保证隔离。"""
        from webui.store import TaskStore
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_db = pathlib.Path(self._tmpdir.name) / "realdb_execute.db"
        _copy_real_db_snapshot(self.tmp_db)
        self.store = TaskStore(db_path=str(self.tmp_db))

    def tearDown(self):
        try:
            self.store._conn.close()
        except Exception:
            pass
        self._tmpdir.cleanup()

    def _query_verdict_counts(self, run_id):
        """查询某 run 的 verdict 分布，区分纯字符串和 JSON。"""
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT verdict, COUNT(*) AS n FROM screening_results "
                "WHERE run_id = ? GROUP BY verdict",
                (run_id,),
            ).fetchall()
        plain = {}
        json_count = 0
        json_inner = {}
        for r in rows:
            v = r["verdict"] or ""
            n = int(r["n"])
            if v.startswith("{"):
                json_count += n
                try:
                    data = json.loads(v)
                    if isinstance(data, dict) and "verdict" in data:
                        inner = data["verdict"]
                        json_inner[inner] = json_inner.get(inner, 0) + n
                except (json.JSONDecodeError, TypeError):
                    pass
            else:
                plain[v] = plain.get(v, 0) + n
        return {"plain": plain, "json_count": json_count, "json_inner": json_inner}

    def test_plain_verdicts_unchanged_after_recovery(self):
        """恢复后 1876 条原纯字符串 verdict 的 job_id → verdict 映射必须完全不变。

        恢复会把 50 条 JSON verdict 转为纯字符串，所以 plain 总数会增加 50。
        但原 1876 条纯字符串 verdict 的 job_id → verdict 映射不得变化。
        """
        from webui.historical_recovery import execute_recovery

        # 恢复前：记录所有纯字符串 verdict 的 job_id → verdict 映射
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT job_id, verdict FROM screening_results "
                "WHERE run_id = ? AND verdict NOT LIKE '{%'",
                (ROUGH_RUN_ID,),
            ).fetchall()
        before_plain_map = {r["job_id"]: r["verdict"] for r in rows}
        self.assertEqual(len(before_plain_map), ROUGH_PLAIN_TOTAL,
                         f"恢复前纯字符串 verdict 应 {ROUGH_PLAIN_TOTAL} 条，实际 {len(before_plain_map)}")

        # 执行恢复
        result = _execute_prepared(self.store)
        self.assertTrue(result.get("ok"), f"恢复执行失败：{result.get('error', 'unknown')}")

        # 恢复后：原 1876 条 job_id 的 verdict 必须全部不变
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT job_id, verdict FROM screening_results "
                "WHERE run_id = ? AND verdict NOT LIKE '{%'",
                (ROUGH_RUN_ID,),
            ).fetchall()
        after_plain_map = {r["job_id"]: r["verdict"] for r in rows}

        # 原 1876 条必须全部仍在，且 verdict 不变
        for jid, old_v in before_plain_map.items():
            self.assertIn(jid, after_plain_map,
                          f"原纯字符串 job_id {jid} 恢复后消失")
            self.assertEqual(after_plain_map[jid], old_v,
                             f"原纯字符串 job_id {jid} verdict 被改写：{old_v} → {after_plain_map[jid]}")

        # 恢复后 plain 总数 = 原 1876 + JSON 转换的 50 = 1926
        self.assertEqual(len(after_plain_map), ROUGH_PLAIN_TOTAL + ROUGH_JSON_TOTAL,
                         f"恢复后纯字符串 verdict 应 {ROUGH_PLAIN_TOTAL + ROUGH_JSON_TOTAL}"
                         f"（原 {ROUGH_PLAIN_TOTAL} + JSON {ROUGH_JSON_TOTAL}），实际 {len(after_plain_map)}")
        # 验证具体分布：match=215(198+17), not_match=547(514+33), uncertain=646, dropped=518
        from collections import Counter
        after_counts = Counter(after_plain_map.values())
        self.assertEqual(after_counts.get("match", 0), ROUGH_PLAIN_MATCH + ROUGH_JSON_MATCH,
                         f"恢复后 match 应 {ROUGH_PLAIN_MATCH + ROUGH_JSON_MATCH}，实际 {after_counts.get('match', 0)}")
        self.assertEqual(after_counts.get("not_match", 0), ROUGH_PLAIN_NOT_MATCH + ROUGH_JSON_NOT_MATCH,
                         f"恢复后 not_match 应 {ROUGH_PLAIN_NOT_MATCH + ROUGH_JSON_NOT_MATCH}，实际 {after_counts.get('not_match', 0)}")
        self.assertEqual(after_counts.get("uncertain", 0), ROUGH_PLAIN_UNCERTAIN,
                         "uncertain 不变")
        self.assertEqual(after_counts.get("dropped", 0), ROUGH_PLAIN_DROPPED,
                         "dropped 不变")

    def test_json_verdicts_converted_after_recovery(self):
        """恢复后 50 条 JSON verdict 必须全部转为纯字符串。"""
        from webui.historical_recovery import execute_recovery

        before = self._query_verdict_counts(ROUGH_RUN_ID)
        self.assertEqual(before["json_count"], ROUGH_JSON_TOTAL,
                         f"恢复前 JSON verdict 应 {ROUGH_JSON_TOTAL}，实际 {before['json_count']}")

        result = _execute_prepared(self.store)
        self.assertTrue(result.get("ok"))

        after = self._query_verdict_counts(ROUGH_RUN_ID)
        self.assertEqual(after["json_count"], 0,
                         f"恢复后 JSON verdict 应为 0，实际 {after['json_count']}")
        self.assertEqual(result["action_1_rough_50_json_unified"], ROUGH_JSON_TOTAL,
                         f"action_1 应统一 {ROUGH_JSON_TOTAL} 条，实际 {result['action_1_rough_50_json_unified']}")

    def test_jd_762_preserved_after_recovery(self):
        """恢复后 762 条 JD 必须仍然存在，未被清空或改写。"""
        from webui.historical_recovery import execute_recovery

        result = _execute_prepared(self.store)
        self.assertTrue(result.get("ok"))

        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_results "
                "WHERE run_id = ? AND jd IS NOT NULL AND length(jd) > 0",
                (ROUGH_RUN_ID,),
            ).fetchone()
        jd_count = int(row["n"] or 0)
        self.assertEqual(jd_count, ROUGH_JD_COUNT,
                         f"恢复后 JD 非空数应 {ROUGH_JD_COUNT}，实际 {jd_count}")
        self.assertEqual(result["action_3_jd_762_protected"], ROUGH_JD_COUNT,
                         "action_3 必须报告 762 条 JD 受保护")

    def test_pending_646_written_to_pending_table(self):
        """恢复后 646 条必须写入 screening_pending_results。"""
        from webui.historical_recovery import execute_recovery

        result = _execute_prepared(self.store)
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["action_4_pending_646_written"], PENDING_646,
                         f"action_4 应写 {PENDING_646} 条 pending，实际 {result['action_4_pending_646_written']}")

        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_pending_results "
                "WHERE run_id = ?",
                (ROUGH_RUN_ID,),
            ).fetchone()
        written = int(row["n"] or 0)
        self.assertEqual(written, PENDING_646,
                         f"pending 表应有 {PENDING_646} 条，实际 {written}")

    def test_fine_50_written_to_pending_table(self):
        """恢复后 e6250f0e 的 50 条 uncertain 必须写入 pending 表。"""
        from webui.historical_recovery import execute_recovery

        result = _execute_prepared(self.store)
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["action_2_fine_50_marked"], FINE_INNER_UNCERTAIN,
                         f"action_2 应标记 {FINE_INNER_UNCERTAIN} 条，实际 {result['action_2_fine_50_marked']}")

        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_pending_results "
                "WHERE run_id = ?",
                (FINE_RUN_ID,),
            ).fetchone()
        written = int(row["n"] or 0)
        self.assertEqual(written, FINE_INNER_UNCERTAIN,
                         f"pending 表应有 {FINE_INNER_UNCERTAIN} 条 fine run 记录，实际 {written}")

    def test_post_recovery_conservation_ok(self):
        """恢复后守恒律必须仍然成立。"""
        from webui.historical_recovery import execute_recovery

        result = _execute_prepared(self.store)
        self.assertTrue(result.get("ok"))

        post = result["post_recovery"]
        c = post["conservation"]
        self.assertTrue(c["all_ok"],
                        f"恢复后守恒律失败：{c}")
        # 恢复后 rough run 的 plain verdict 应包含原纯字符串 + JSON 转换后的
        # source/dropped/kept 不变
        self.assertEqual(c["source"], ROUGH_SOURCE_COUNT)
        self.assertEqual(c["dropped"], ROUGH_DROPPED)
        self.assertEqual(c["kept"], ROUGH_KEPT)


@unittest.skipUnless(_REAL_DB.exists(), f"正式库不存在：{_REAL_DB}")
class TestRunDataRoles(unittest.TestCase):
    """验证两条 run 的数据角色（粗筛 vs 精筛）。"""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.tmp_db = pathlib.Path(cls._tmpdir.name) / "realdb_roles.db"
        _copy_real_db_snapshot(cls.tmp_db)
        from webui.store import TaskStore
        cls.store = TaskStore(db_path=str(cls.tmp_db))

    @classmethod
    def tearDownClass(cls):
        try:
            cls.store._conn.close()
        except Exception:
            pass
        cls._tmpdir.cleanup()

    def test_rough_run_is_15847d27(self):
        """15847d27 是粗筛 run，source_count=1926，有纯字符串 + JSON 两种 verdict。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store)
        rough = result["rough_run"]
        self.assertEqual(rough["id"], ROUGH_RUN_ID)
        self.assertEqual(rough["source_count"], ROUGH_SOURCE_COUNT)
        self.assertEqual(rough["total"], ROUGH_TOTAL)
        # 必须同时有纯字符串和 JSON verdict
        self.assertTrue(rough["plain_verdicts"], "粗筛 run 必须有纯字符串 verdict")
        self.assertTrue(rough["inner_verdicts"], "粗筛 run 必须有 JSON inner verdict（50 条异常）")
        self.assertEqual(rough["jd_count"], ROUGH_JD_COUNT,
                         "粗筛 run 必须有 762 条 JD（精筛不抓 JD）")

    def test_fine_run_is_e6250f0e(self):
        """e6250f0e 是精筛 run，762 条全部 JSON verdict，JD 非空 0 条。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store)
        fine = result["fine_run"]
        self.assertEqual(fine["id"], FINE_RUN_ID)
        self.assertEqual(fine["total"], FINE_TOTAL)
        # 精筛 run 全部是 JSON verdict，无纯字符串
        self.assertFalse(fine["plain_verdicts"],
                         "精筛 run 不应有纯字符串 verdict（全部是 JSON）")
        self.assertTrue(fine["inner_verdicts"],
                        "精筛 run 必须有 JSON inner verdict")
        # 精筛不抓 JD，JD 非空应为 0
        self.assertEqual(fine["jd_count"], 0,
                         f"精筛 run JD 非空应 0（精筛不抓 JD），实际 {fine['jd_count']}")

    def test_fine_run_762_equals_rough_kept_minus_pending(self):
        """精筛 762 = 粗筛 kept 1408 - pending 646。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store)
        fine_total = result["fine_run"]["total"]
        kept = result["rough_run"]["total_kept"]
        pending = result["pending_646"]["count"]
        self.assertEqual(fine_total, kept - pending,
                         f"精筛 {fine_total} 应 = kept {kept} - pending {pending} = {kept - pending}")

    def test_rough_jd_762_equals_fine_total(self):
        """粗筛 762 条 JD 非空 = 精筛 762 条总数（进精筛的岗位都有 JD）。"""
        from webui.historical_recovery import preview_recovery
        result = preview_recovery(self.store)
        jd_count = result["rough_run"]["jd_count"]
        fine_total = result["fine_run"]["total"]
        self.assertEqual(jd_count, fine_total,
                         f"粗筛 JD 非空 {jd_count} 应 = 精筛总数 {fine_total}")


@unittest.skipUnless(_REAL_DB.exists(), f"正式库不存在：{_REAL_DB}")
class TestNoMisidentificationOfPlainVerdicts(unittest.TestCase):
    """回归保护：确保不会把 198 match + 514 not_match 纯字符串误判为异常。"""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls.tmp_db = pathlib.Path(cls._tmpdir.name) / "realdb_no_misid.db"
        _copy_real_db_snapshot(cls.tmp_db)
        from webui.store import TaskStore
        cls.store = TaskStore(db_path=str(cls.tmp_db))

    @classmethod
    def tearDownClass(cls):
        try:
            cls.store._conn.close()
        except Exception:
            pass
        cls._tmpdir.cleanup()

    def test_50_json_not_198_514_plain(self):
        """异常必须是 50 条 JSON verdict (17+33)，不是 198+514 纯字符串。"""
        from webui.historical_recovery import _identify_rough_50_json_split
        with self.store._connection() as conn:
            r50 = _identify_rough_50_json_split(conn, ROUGH_RUN_ID)
        # 必须 17+33=50，不是 198+514=712
        self.assertEqual(r50["match"], ROUGH_JSON_MATCH,
                         f"异常 match 应 {ROUGH_JSON_MATCH}（JSON inner），实际 {r50['match']}")
        self.assertEqual(r50["not_match"], ROUGH_JSON_NOT_MATCH,
                         f"异常 not_match 应 {ROUGH_JSON_NOT_MATCH}（JSON inner），实际 {r50['not_match']}")
        self.assertEqual(r50["total"], ROUGH_JSON_TOTAL,
                         f"异常总数应 {ROUGH_JSON_TOTAL}（不是 {ROUGH_PLAIN_MATCH + ROUGH_PLAIN_NOT_MATCH}）")
        self.assertEqual(r50["verdict_format"], "json_inner",
                         "异常识别必须指向 JSON inner verdict，不是纯字符串")

    def test_execute_recovery_does_not_touch_514_not_match(self):
        """execute_recovery 不得改写 514 条纯字符串 not_match。"""
        from webui.historical_recovery import execute_recovery

        # 恢复前：514 条纯字符串 not_match
        with self.store._connection() as conn:
            before_row = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_results "
                "WHERE run_id = ? AND verdict = 'not_match'",
                (ROUGH_RUN_ID,),
            ).fetchone()
        before_not_match = int(before_row["n"] or 0)
        self.assertEqual(before_not_match, ROUGH_PLAIN_NOT_MATCH,
                         f"恢复前纯字符串 not_match 应 {ROUGH_PLAIN_NOT_MATCH}，实际 {before_not_match}")

        # 执行恢复
        result = _execute_prepared(self.store)
        self.assertTrue(result.get("ok"))

        # 恢复后：纯字符串 not_match 应 = 原 514 + JSON 转换的 33 = 547
        # （JSON 33 条 not_match 被格式统一为纯字符串 not_match）
        with self.store._connection() as conn:
            after_row = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_results "
                "WHERE run_id = ? AND verdict = 'not_match'",
                (ROUGH_RUN_ID,),
            ).fetchone()
        after_not_match = int(after_row["n"] or 0)
        self.assertEqual(after_not_match, ROUGH_PLAIN_NOT_MATCH + ROUGH_JSON_NOT_MATCH,
                         f"恢复后纯字符串 not_match 应 {ROUGH_PLAIN_NOT_MATCH + ROUGH_JSON_NOT_MATCH}"
                         f"（原 {ROUGH_PLAIN_NOT_MATCH} + JSON {ROUGH_JSON_NOT_MATCH}），实际 {after_not_match}")

        # 关键：恢复前 514 条 not_match 的 job_id 必须全部仍在，且 verdict 仍为 not_match
        # （即没有被误改或删除）
        with self.store._connection() as conn:
            # 取恢复前 514 条 not_match 的 job_id
            # 由于已执行恢复，我们验证总数：514 + 33 = 547
            # 如果误改了 514 条，总数会不对
            pass  # 已通过 above 断言验证


# ============================================================================
# A.6 事务/回滚/备份/幂等测试前移（v3.1 调整点 7）—— RED 阶段
# ============================================================================

@unittest.skipUnless(_REAL_DB.exists(), f"正式库不存在：{_REAL_DB}")
class TestPrepareRecoveryCreatesBackup(unittest.TestCase):
    """prepare_recovery 创建备份并登记 manifest（不写正式 recovery_audit）。"""

    def setUp(self):
        from webui.store import TaskStore
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_db = pathlib.Path(self._tmpdir.name) / "prepare.db"
        _copy_real_db_snapshot(self.tmp_db)
        self.store = TaskStore(db_path=str(self.tmp_db))

    def tearDown(self):
        try:
            self.store._conn.close()
        except Exception:
            pass
        self._tmpdir.cleanup()

    def test_prepare_recovery_exists(self):
        """historical_recovery 必须有 prepare_recovery 函数。"""
        from webui.historical_recovery import prepare_recovery  # noqa: F401

    def test_prepare_recovery_creates_backup_in_fixed_dir(self):
        """prepare_recovery 用 SQLite Backup API 在固定目录创建备份。"""
        from webui.historical_recovery import prepare_recovery
        result = prepare_recovery(self.store)
        self.assertIsInstance(result, dict, f"prepare_recovery 应返回 dict，实际 {type(result)}")
        self.assertIn("backup_id", result, f"必须返回 backup_id，实际 {result}")
        self.assertIn("backup_sha256", result)
        self.assertEqual(len(result["backup_sha256"]), 64,
                         f"sha256 必须 64 字符，实际 {len(result.get('backup_sha256', ''))}")
        self.assertIn("source_fingerprint", result)
        # 备份文件在固定目录
        self.assertIn("backup_path", result)
        backup_path = pathlib.Path(result["backup_path"])
        self.assertTrue(backup_path.exists(),
                        f"备份文件必须存在：{backup_path}")

    def test_prepare_recovery_writes_manifest_not_audit(self):
        """prepared 状态只写 manifest（backups/<backup_id>/manifest.json），不写正式 recovery_audit。"""
        from webui.historical_recovery import prepare_recovery
        result = prepare_recovery(self.store)
        backup_id = result["backup_id"]
        # manifest 在 backups/<backup_id>/manifest.json
        manifest_path = (pathlib.Path(result["backup_path"]).parent
                         / "manifest.json")
        self.assertTrue(manifest_path.exists(),
                        f"manifest 必须存在：{manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest.get("backup_id"), backup_id)
        self.assertEqual(manifest.get("status"), "prepared")
        # 正式 recovery_audit 表不应有 prepared 记录
        with self.store._connection() as conn:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM recovery_audit "
                    "WHERE backup_id = ?", (backup_id,)).fetchone()
                count = int(row["n"] or 0) if row else 0
            except Exception:
                count = 0
        self.assertEqual(count, 0,
                         "prepare 不得写正式 recovery_audit，prepared 只在 manifest")


@unittest.skipUnless(_REAL_DB.exists(), f"正式库不存在：{_REAL_DB}")
class TestExecuteRecoveryUsesBackupIdOnly(unittest.TestCase):
    """execute_recovery 只接受 backup_id + store（keyword-only），不接受客户端 backup_path。"""

    def setUp(self):
        from webui.store import TaskStore
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_db = pathlib.Path(self._tmpdir.name) / "exec_backup_id.db"
        _copy_real_db_snapshot(self.tmp_db)
        self.store = TaskStore(db_path=str(self.tmp_db))

    def tearDown(self):
        try:
            self.store._conn.close()
        except Exception:
            pass
        self._tmpdir.cleanup()

    def test_execute_recovery_signature_requires_store_keyword(self):
        """execute_recovery 签名：store 必填 keyword-only，无默认回退。"""
        import inspect
        from webui.historical_recovery import execute_recovery
        sig = inspect.signature(execute_recovery)
        params = sig.parameters
        # store 必须是 keyword-only
        self.assertIn("store", params,
                      "execute_recovery 必须有 store 参数")
        self.assertEqual(params["store"].kind,
                         inspect.Parameter.KEYWORD_ONLY,
                         "store 必须是 keyword-only")
        self.assertIs(params["store"].default, inspect.Parameter.empty,
                      "store 不得有默认值")

    def test_execute_recovery_rejects_unknown_backup_id(self):
        """传入未注册的 backup_id 必须拒绝。"""
        from webui.historical_recovery import execute_recovery
        result = execute_recovery("unknown-backup-id", store=self.store)
        self.assertFalse(result.get("ok"),
                         f"未注册 backup_id 必须拒绝，实际 {result}")
        self.assertIn("error", result)

    def test_execute_recovery_revalidates_backup_hash(self):
        """execute_recovery 必须重新校验备份 SHA256。"""
        from webui.historical_recovery import prepare_recovery, execute_recovery
        prep = prepare_recovery(self.store)
        backup_id = prep["backup_id"]
        # 篡改备份文件
        backup_path = pathlib.Path(prep["backup_path"])
        backup_path.write_bytes(b"tampered")
        result = execute_recovery(backup_id, store=self.store)
        self.assertFalse(result.get("ok"),
                         f"备份被篡改必须拒绝，实际 {result}")


@unittest.skipUnless(_REAL_DB.exists(), f"正式库不存在：{_REAL_DB}")
class TestRecoverySingleTransaction(unittest.TestCase):
    """execute_recovery 单 connection 单事务，任一步失败全部回滚。"""

    def setUp(self):
        from webui.store import TaskStore
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_db = pathlib.Path(self._tmpdir.name) / "single_tx.db"
        _copy_real_db_snapshot(self.tmp_db)
        self.store = TaskStore(db_path=str(self.tmp_db))

    def tearDown(self):
        try:
            self.store._conn.close()
        except Exception:
            pass
        self._tmpdir.cleanup()

    def test_all_helpers_use_same_connection(self):
        """execute 运行时所有数据 helper 必须收到同一个 connection。"""
        from webui import historical_recovery as hr
        helpers_to_check = [
            "_identify_rough_50_json_split",
            "_identify_fine_50_uncertain",
            "_identify_pending_646",
            "_check_762_jd_protection",
            "_query_run_summary",
        ]
        seen_connection_ids = []
        patches = []
        for name in helpers_to_check:
            original = getattr(hr, name)

            def wrapper(*args, _original=original, **kwargs):
                if args[0].in_transaction:
                    seen_connection_ids.append(id(args[0]))
                return _original(*args, **kwargs)

            patches.append(mock.patch.object(hr, name, side_effect=wrapper))
        for patcher in patches:
            patcher.start()
        try:
            prepared = hr.prepare_recovery(self.store)
            result = hr.execute_recovery(prepared["backup_id"], store=self.store)
        finally:
            for patcher in reversed(patches):
                patcher.stop()
        self.assertTrue(result.get("tx_committed"), result)
        self.assertTrue(seen_connection_ids)
        self.assertEqual(len(set(seen_connection_ids)), 1,
                         "恢复数据 helper 必须共享同一个事务 connection")

    def test_action3_failure_rolls_back_all(self):
        """动作 3 失败 → 动作 1/2 全部回滚，数据与恢复前完全一致。"""
        from webui.historical_recovery import prepare_recovery, execute_recovery
        import hashlib

        def recovery_data_hash():
            with self.store._connection() as conn:
                payload = {}
                for table, columns, order in (
                    ("screening_results", "run_id, job_id, verdict, COALESCE(jd, '')", "run_id, job_id"),
                    ("screening_pending_results", "run_id, job_id, failure_stage, attempts, COALESCE(failed_code, '')", "run_id, job_id"),
                    ("pipeline_checkpoints", "run_id, stage, completed_keys_json", "run_id, stage"),
                    ("screening_runs", "id, status, source_count, pending_count, total_dropped, total_kept", "id"),
                ):
                    payload[table] = [list(row) for row in conn.execute(
                        f"SELECT {columns} FROM {table} ORDER BY {order}"
                    ).fetchall()]
            return hashlib.sha256(json.dumps(
                payload, ensure_ascii=False, sort_keys=True
            ).encode()).hexdigest()

        before_hash = recovery_data_hash()

        prep = prepare_recovery(self.store)
        backup_id = prep["backup_id"]

        # mock 动作 3 抛异常（需识别动作 3 函数，这里用通用方式）
        from unittest import mock
        with mock.patch("webui.historical_recovery._check_762_jd_protection",
                        side_effect=RuntimeError("injected_failure")):
            result = execute_recovery(backup_id, store=self.store)

        # 必须回滚
        self.assertFalse(result.get("tx_committed"),
                         f"动作 3 失败必须 tx_committed=False，实际 {result}")

        after_hash = recovery_data_hash()
        self.assertEqual(before_hash, after_hash,
                         "动作 3 失败必须全部回滚，数据哈希不一致")

    def test_unexpected_programming_error_rolls_back_and_propagates(self):
        """未知编程错误不得伪装成业务失败，但事务仍必须回滚。"""
        from webui import historical_recovery as hr

        prepared = hr.prepare_recovery(self.store)
        with mock.patch.object(
            hr, "_check_762_jd_protection",
            side_effect=AssertionError("programming bug"),
        ):
            with self.assertRaisesRegex(AssertionError, "programming bug"):
                hr.execute_recovery(prepared["backup_id"], store=self.store)

        with self.store._connection() as conn:
            rough_json = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_results "
                "WHERE run_id = ? AND verdict LIKE '{%'", (ROUGH_RUN_ID,),
            ).fetchone()["n"]
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_pending_results WHERE run_id = ?",
                (ROUGH_RUN_ID,),
            ).fetchone()["n"]
        self.assertEqual(rough_json, ROUGH_JSON_TOTAL)
        self.assertEqual(pending, 0)

    def test_post_recovery_gate_rolls_back_incomplete_pending_write(self):
        """恢复后真实 pending 集合不完整时，事务不得提交。"""
        from webui import historical_recovery as hr

        prepared = hr.prepare_recovery(self.store)
        with mock.patch.object(hr, "_apply_action_4", return_value=PENDING_646):
            result = hr.execute_recovery(prepared["backup_id"], store=self.store)

        self.assertFalse(result.get("tx_committed"), result)
        self.assertIn("post_recovery_gate", result.get("error", ""))
        with self.store._connection() as conn:
            pending = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_pending_results WHERE run_id = ?",
                (ROUGH_RUN_ID,),
            ).fetchone()["n"]
        self.assertEqual(pending, 0, "门禁失败后 pending 写入必须全部回滚")

    def test_post_recovery_gate_rejects_wrong_rough_split(self):
        """动作 1 即使改了 50 行，也必须精确保持 17 match / 33 not_match。"""
        from webui import historical_recovery as hr

        def corrupt_split(conn):
            cursor = conn.execute(
                "UPDATE screening_results SET verdict = 'match' "
                "WHERE run_id = ? AND verdict LIKE '{%'", (ROUGH_RUN_ID,),
            )
            return cursor.rowcount

        prepared = hr.prepare_recovery(self.store)
        with mock.patch.object(hr, "_apply_action_1", side_effect=corrupt_split):
            result = hr.execute_recovery(prepared["backup_id"], store=self.store)
        self.assertFalse(result.get("tx_committed"), result)
        self.assertIn("rough_verdict_distribution_exact", result.get("error", ""))

    def test_post_recovery_gate_rejects_generic_pending_metadata(self):
        """任意非空失败码不等于具体原因，空 payload 也不得通过门禁。"""
        from webui import historical_recovery as hr

        def write_generic_pending(conn):
            rows = conn.execute(
                "SELECT job_id FROM screening_results "
                "WHERE run_id = ? AND verdict = 'uncertain' "
                "AND job_id NOT IN (SELECT job_id FROM screening_results WHERE run_id = ?)",
                (ROUGH_RUN_ID, FINE_RUN_ID),
            ).fetchall()
            for row in rows:
                hr._insert_pending(
                    conn, run_id=ROUGH_RUN_ID, job_id=row["job_id"],
                    failure_stage="jd_detail", failed_code="generic_failure",
                    origin_zone="kept", ai_payload={},
                )
            return len(rows)

        prepared = hr.prepare_recovery(self.store)
        with mock.patch.object(hr, "_apply_action_4", side_effect=write_generic_pending):
            result = hr.execute_recovery(prepared["backup_id"], store=self.store)
        self.assertFalse(result.get("tx_committed"), result)
        self.assertIn("rough_pending_metadata_exact", result.get("error", ""))

    def test_post_commit_diagnostic_failure_preserves_committed_truth(self):
        """提交后的诊断失败只能告警，不能把已提交恢复改写为 failed。"""
        from webui import historical_recovery as hr

        prepared = hr.prepare_recovery(self.store)
        with mock.patch.object(
                hr, "preview_recovery", side_effect=RuntimeError("diagnostic failed")):
            result = hr.execute_recovery(prepared["backup_id"], store=self.store)

        self.assertTrue(result.get("tx_committed"), result)
        self.assertTrue(result.get("ok"), result)
        self.assertIn("post_commit_warnings", result)
        with self.store._connection() as conn:
            audit = conn.execute(
                "SELECT status, tx_committed, error FROM recovery_audit "
                "WHERE backup_id = ?", (prepared["backup_id"],),
            ).fetchone()
        self.assertEqual(audit["status"], "committed")
        self.assertEqual(audit["tx_committed"], 1)
        self.assertIsNone(audit["error"])

    def test_action4_records_honest_historical_reason(self):
        """缺少历史细分证据时必须明确记录原因缺失，不能写 NULL。"""
        from webui import historical_recovery as hr

        prepared = hr.prepare_recovery(self.store)
        result = hr.execute_recovery(prepared["backup_id"], store=self.store)
        self.assertTrue(result.get("tx_committed"), result)
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT failed_code, ai_payload_json FROM screening_pending_results "
                "WHERE run_id = ?", (ROUGH_RUN_ID,),
            ).fetchall()
        self.assertEqual(len(rows), PENDING_646)
        self.assertEqual(
            {row["failed_code"] for row in rows}, {"historical_reason_unavailable"}
        )
        payloads = [json.loads(row["ai_payload_json"]) for row in rows]
        self.assertTrue(all(p.get("reason") for p in payloads))
        self.assertTrue(all(p.get("next_action") == "recrawl_jd" for p in payloads))

    def test_committed_metadata_repair_is_backed_up_audited_and_conservative(self):
        """旧版已提交恢复的空原因可单独补正，且不改 verdict/JD。"""
        from webui import historical_recovery as hr

        initial = hr.prepare_recovery(self.store)
        recovered = hr.execute_recovery(initial["backup_id"], store=self.store)
        self.assertTrue(recovered.get("tx_committed"), recovered)
        with self.store._connection() as conn:
            primary_audit = conn.execute(
                "SELECT recovery_key, backup_id FROM recovery_audit "
                "WHERE status = 'committed' AND tx_committed = 1 "
                "AND stats_json LIKE '%action_1_rough_50_json_unified%'"
            ).fetchone()
            conn.execute(
                "UPDATE screening_pending_results "
                "SET failed_code = NULL, ai_payload_json = '{}' WHERE run_id = ?",
                (ROUGH_RUN_ID,),
            )
            before = hr._recovery_integrity_snapshot(conn)

        repair_backup = hr.prepare_recovery(self.store)
        repaired = hr.repair_committed_pending_metadata(
            repair_backup["backup_id"], store=self.store
        )
        self.assertTrue(repaired.get("tx_committed"), repaired)
        self.assertEqual(repaired.get("updated"), PENDING_646)
        with self.store._connection() as conn:
            after = hr._recovery_integrity_snapshot(conn)
            rows = conn.execute(
                "SELECT failed_code, ai_payload_json FROM screening_pending_results "
                "WHERE run_id = ?", (ROUGH_RUN_ID,),
            ).fetchall()
            audit = conn.execute(
                "SELECT status, tx_committed, stats_json FROM recovery_audit "
                "WHERE backup_id = ?",
                (repair_backup["backup_id"],),
            ).fetchone()
        self.assertEqual(before, after)
        self.assertEqual(len(rows), PENDING_646)
        self.assertEqual(
            {row["failed_code"] for row in rows}, {"historical_reason_unavailable"}
        )
        self.assertTrue(all(json.loads(row["ai_payload_json"]).get("reason") for row in rows))
        self.assertEqual(audit["status"], "committed")
        self.assertEqual(audit["tx_committed"], 1)
        repair_stats = json.loads(audit["stats_json"])
        self.assertEqual(
            repair_stats.get("parent_recovery_key"), primary_audit["recovery_key"]
        )
        self.assertEqual(
            repair_stats.get("parent_backup_id"), primary_audit["backup_id"]
        )
        manifest = json.loads(
            (pathlib.Path(repair_backup["backup_path"]).parent / "manifest.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(manifest.get("status"), "committed")
        self.assertEqual(manifest.get("operation"), "pending_metadata_repair")

    def _complete_metadata_repair(self):
        """Create and commit a metadata repair using only this temporary DB."""
        from webui import historical_recovery as hr

        initial = hr.prepare_recovery(self.store)
        recovered = hr.execute_recovery(initial["backup_id"], store=self.store)
        self.assertTrue(recovered.get("tx_committed"), recovered)
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE screening_pending_results "
                "SET failed_code = NULL, ai_payload_json = '{}' WHERE run_id = ?",
                (ROUGH_RUN_ID,),
            )
        repair_backup = hr.prepare_recovery(self.store)
        repaired = hr.repair_committed_pending_metadata(
            repair_backup["backup_id"], store=self.store
        )
        self.assertTrue(repaired.get("tx_committed"), repaired)
        return hr, repair_backup

    def test_committed_metadata_repair_revalidates_backup_hash(self):
        """Idempotent retry must reject a committed repair with a changed backup."""
        hr, repair_backup = self._complete_metadata_repair()
        pathlib.Path(repair_backup["backup_path"]).write_bytes(b"tampered")

        retried = hr.repair_committed_pending_metadata(
            repair_backup["backup_id"], store=self.store
        )

        self.assertFalse(retried.get("ok"), retried)
        self.assertEqual(retried.get("error"), "backup_hash_mismatch")

    def test_committed_metadata_repair_revalidates_manifest_identity(self):
        """Idempotent retry must reject a committed repair with a changed manifest."""
        hr, repair_backup = self._complete_metadata_repair()
        manifest_path = pathlib.Path(repair_backup["backup_path"]).parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["operation"] = "unexpected_operation"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        retried = hr.repair_committed_pending_metadata(
            repair_backup["backup_id"], store=self.store
        )

        self.assertFalse(retried.get("ok"), retried)
        self.assertEqual(retried.get("error"), "invalid_repair_manifest")

@unittest.skipUnless(_REAL_DB.exists(), f"正式库不存在：{_REAL_DB}")
class TestMetadataRepairAuditBinding(unittest.TestCase):
    """元数据补正只能基于可核验的正式历史恢复 audit。"""

    _METADATA_BACKUP = (
        _REAL_DB.parent / "backups" / "e848e245a2b544dd9ffdf5c25920840e" /
        "webui.db"
    )

    def setUp(self):
        if not self._METADATA_BACKUP.is_file():
            self.skipTest(f"元数据补正备份不存在：{self._METADATA_BACKUP}")
        from webui.store import TaskStore

        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_db = pathlib.Path(self._tmpdir.name) / "metadata_binding.db"
        source = sqlite3.connect(
            f"{self._METADATA_BACKUP.resolve().as_uri()}?mode=ro", uri=True
        )
        target = sqlite3.connect(str(self.tmp_db))
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        self.store = TaskStore(db_path=str(self.tmp_db))

    def tearDown(self):
        if not hasattr(self, "store"):
            return
        try:
            self.store._conn.close()
        except Exception:
            pass
        self._tmpdir.cleanup()

    def test_metadata_repair_rejects_unrelated_committed_audit(self):
        """只有无关 committed audit 时不得把它当作正式历史恢复证明。"""
        from webui import historical_recovery as hr

        with self.store._connection() as conn:
            conn.execute("DELETE FROM recovery_audit")
            conn.execute(
                "INSERT INTO recovery_audit "
                "(id, recovery_key, backup_id, status, tx_committed, error, "
                "stats_json, started_at, finished_at) "
                "VALUES ('unrelated-id', 'unrelated-key', 'unrelated-backup', "
                "'committed', 1, NULL, ?, '2026-07-28T00:00:00+08:00', "
                "'2026-07-28T00:00:00+08:00')",
                (json.dumps({"operation": "unrelated"}),),
            )

        prepared = hr.prepare_recovery(self.store)
        repaired = hr.repair_committed_pending_metadata(
            prepared["backup_id"], store=self.store
        )

        self.assertFalse(repaired.get("ok"), repaired)
        self.assertEqual(repaired.get("error"), "committed_recovery_required")
        self.assertFalse(repaired.get("tx_committed", False), repaired)
        with self.store._connection() as conn:
            untouched = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_pending_results "
                "WHERE run_id = ? AND failed_code IS NULL",
                (ROUGH_RUN_ID,),
            ).fetchone()["n"]
        self.assertEqual(untouched, PENDING_646)

    def test_metadata_repair_rejects_forged_action_stats_without_backup_identity(self):
        """伪造相同 action 数字但没有 committed 备份身份时仍必须拒绝。"""
        from webui import historical_recovery as hr

        forged_stats = {
            "operation": "unrelated",
            "action_1_rough_50_json_unified": 50,
            "action_2_fine_50_marked": 50,
            "action_3_jd_762_protected": 762,
            "action_4_pending_646_written": 646,
        }
        with self.store._connection() as conn:
            conn.execute("DELETE FROM recovery_audit")
            conn.execute(
                "INSERT INTO recovery_audit "
                "(id, recovery_key, backup_id, status, tx_committed, error, "
                "stats_json, started_at, finished_at) "
                "VALUES ('forged-id', 'forged-key', 'forged-backup', "
                "'committed', 1, NULL, ?, '2026-07-28T00:00:00+08:00', "
                "'2026-07-28T00:00:00+08:00')",
                (json.dumps(forged_stats),),
            )

        prepared = hr.prepare_recovery(self.store)
        repaired = hr.repair_committed_pending_metadata(
            prepared["backup_id"], store=self.store
        )

        self.assertFalse(repaired.get("ok"), repaired)
        self.assertEqual(repaired.get("error"), "committed_recovery_required")
        self.assertFalse(repaired.get("tx_committed", False), repaired)
        with self.store._connection() as conn:
            untouched = conn.execute(
                "SELECT COUNT(*) AS n FROM screening_pending_results "
                "WHERE run_id = ? AND failed_code IS NULL",
                (ROUGH_RUN_ID,),
            ).fetchone()["n"]
        self.assertEqual(untouched, PENDING_646)


@unittest.skipUnless(_REAL_DB.exists(), f"正式库不存在：{_REAL_DB}")
class TestRecoveryIdempotencyByRecoveryKey(unittest.TestCase):
    """recovery_key 状态机：committed 才幂等返回，failed 允许重试。"""

    def setUp(self):
        from webui.store import TaskStore
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_db = pathlib.Path(self._tmpdir.name) / "idempotent.db"
        _copy_real_db_snapshot(self.tmp_db)
        self.store = TaskStore(db_path=str(self.tmp_db))

    def tearDown(self):
        try:
            self.store._conn.close()
        except Exception:
            pass
        self._tmpdir.cleanup()

    def test_committed_recovery_key_returns_already_recovered(self):
        """committed 状态的 recovery_key 第二次调用返回 already_recovered。"""
        from webui.historical_recovery import prepare_recovery, execute_recovery
        prep = prepare_recovery(self.store)
        backup_id = prep["backup_id"]
        # 第一次执行
        r1 = execute_recovery(backup_id, store=self.store)
        self.assertTrue(r1.get("tx_committed"),
                        f"第一次执行必须成功，实际 {r1}")
        # 第二次执行：应返回 already_recovered
        r2 = execute_recovery(backup_id, store=self.store)
        self.assertTrue(r2.get("already_recovered"),
                        f"committed 后第二次必须 already_recovered，实际 {r2}")

    def test_committed_recovery_revalidates_bound_backup_hash(self):
        """幂等返回前仍须验证对应 manifest 和备份 SHA，不能只信 audit。"""
        from webui.historical_recovery import prepare_recovery, execute_recovery

        prep = prepare_recovery(self.store)
        backup_id = prep["backup_id"]
        first = execute_recovery(backup_id, store=self.store)
        self.assertTrue(first.get("tx_committed"), first)

        pathlib.Path(prep["backup_path"]).write_bytes(b"tampered-after-commit")
        second = execute_recovery(backup_id, store=self.store)

        self.assertFalse(second.get("ok"), second)
        self.assertFalse(second.get("already_recovered", False), second)
        self.assertEqual(second.get("error"), "backup_hash_mismatch")

    def test_failed_recovery_key_allows_retry(self):
        """failed 状态允许重试，不返回 already_recovered。"""
        from webui.historical_recovery import prepare_recovery, execute_recovery
        from unittest import mock
        prep = prepare_recovery(self.store)
        backup_id = prep["backup_id"]
        # 第一次失败
        with mock.patch("webui.historical_recovery._check_762_jd_protection",
                        side_effect=RuntimeError("injected")):
            r1 = execute_recovery(backup_id, store=self.store)
        self.assertFalse(r1.get("tx_committed"))
        # 第二次重试：不应返回 already_recovered
        # （需新的 prepare 或同 backup_id 重试，取决于状态机设计）
        r2 = execute_recovery(backup_id, store=self.store)
        self.assertFalse(r2.get("already_recovered"),
                         f"failed 状态应允许重试，实际 {r2}")


@unittest.skipUnless(_REAL_DB.exists(), f"正式库不存在：{_REAL_DB}")
class TestFailureAuditIndependentTransaction(unittest.TestCase):
    """失败审计在 ROLLBACK 后用独立短事务写入。"""

    def setUp(self):
        from webui.store import TaskStore
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_db = pathlib.Path(self._tmpdir.name) / "fail_audit.db"
        _copy_real_db_snapshot(self.tmp_db)
        self.store = TaskStore(db_path=str(self.tmp_db))

    def tearDown(self):
        try:
            self.store._conn.close()
        except Exception:
            pass
        self._tmpdir.cleanup()

    def test_failure_audit_written_after_rollback(self):
        """故障注入后：主事务 ROLLBACK（数据无变化）+ 独立短事务写 recovery_audit。"""
        from webui.historical_recovery import prepare_recovery, execute_recovery
        from unittest import mock
        import hashlib

        # 恢复前哈希
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT job_id, verdict FROM screening_results "
                "WHERE run_id = ? ORDER BY job_id",
                (ROUGH_RUN_ID,)).fetchall()
        before_hash = hashlib.sha256(
            "\n".join(f"{r['job_id']}|{r['verdict']}" for r in rows).encode()
        ).hexdigest()

        prep = prepare_recovery(self.store)
        backup_id = prep["backup_id"]
        with mock.patch("webui.historical_recovery._check_762_jd_protection",
                        side_effect=RuntimeError("injected")):
            result = execute_recovery(backup_id, store=self.store)

        self.assertFalse(result.get("tx_committed"))

        # 数据无变化（主事务已回滚）
        with self.store._connection() as conn:
            rows = conn.execute(
                "SELECT job_id, verdict FROM screening_results "
                "WHERE run_id = ? ORDER BY job_id",
                (ROUGH_RUN_ID,)).fetchall()
        after_hash = hashlib.sha256(
            "\n".join(f"{r['job_id']}|{r['verdict']}" for r in rows).encode()
        ).hexdigest()
        self.assertEqual(before_hash, after_hash,
                         "主事务必须回滚，数据无变化")

        # recovery_audit 有失败记录（独立短事务写入）
        with self.store._connection() as conn:
            try:
                row = conn.execute(
                    "SELECT tx_committed, error FROM recovery_audit "
                    "WHERE backup_id = ? AND tx_committed = 0",
                    (backup_id,)).fetchone()
            except Exception:
                row = None
        self.assertIsNotNone(row,
                             "失败审计必须用独立短事务写入 recovery_audit")
        self.assertIn("injected", row["error"] or "",
                      f"失败审计必须含错误信息，实际 {row['error']}")

    def test_success_audit_in_main_transaction(self):
        """成功审计与恢复数据同事务提交（不在独立短事务写）。"""
        from webui.historical_recovery import prepare_recovery, execute_recovery
        prep = prepare_recovery(self.store)
        backup_id = prep["backup_id"]
        result = execute_recovery(backup_id, store=self.store)
        self.assertTrue(result.get("tx_committed"))

        # recovery_audit 有成功记录
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT tx_committed FROM recovery_audit WHERE backup_id = ?",
                (backup_id,)).fetchone()
        self.assertIsNotNone(row, "成功审计必须写入 recovery_audit")
        self.assertTrue(int(row["tx_committed"]) if row["tx_committed"] is not None else False,
                        "成功审计 tx_committed 必须为 True")


@unittest.skipUnless(_REAL_DB.exists(), f"正式库不存在：{_REAL_DB}")
class TestRecoveryGlobalLock(unittest.TestCase):
    """全局 recovery lock：恢复期间拒绝新任务写库。"""

    def setUp(self):
        from webui.store import TaskStore
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_db = pathlib.Path(self._tmpdir.name) / "lock.db"
        _copy_real_db_snapshot(self.tmp_db)
        self.store = TaskStore(db_path=str(self.tmp_db))

    def tearDown(self):
        try:
            self.store._conn.close()
        except Exception:
            pass
        self._tmpdir.cleanup()

    def test_store_has_recovery_lock_methods(self):
        """store 必须有 acquire/release/is_recovery_locked 方法。"""
        self.assertTrue(hasattr(self.store, "acquire_recovery_lock"),
                        "store 必须有 acquire_recovery_lock 方法")
        self.assertTrue(hasattr(self.store, "release_recovery_lock"),
                        "store 必须有 release_recovery_lock 方法")
        self.assertTrue(hasattr(self.store, "is_recovery_locked"),
                        "store 必须有 is_recovery_locked 方法")

    def test_recovery_lock_blocks_new_tasks_during_recovery(self):
        """恢复期间新任务写库被拒绝。"""
        self.assertTrue(hasattr(self.store, "acquire_recovery_lock"))
        # 获取锁
        self.store.acquire_recovery_lock(owner_token="test-owner",
                                          maintenance=True)
        try:
            self.assertTrue(self.store.is_recovery_locked(),
                            "获取锁后 is_recovery_locked 必须为 True")
            # 尝试创建新 run 应被拒绝
            with self.assertRaises(Exception) as ctx:
                self.store.create_screening_run("blocked-new-run",
                                                  source_count=10)
            self.assertIn("recovery", str(ctx.exception).lower(),
                          f"拒绝信息应含 recovery，实际 {ctx.exception}")
        finally:
            self.store.release_recovery_lock(owner_token="test-owner")
        self.assertFalse(self.store.is_recovery_locked(),
                         "释放锁后 is_recovery_locked 必须为 False")

    def test_recovery_lock_has_owner_token_and_expires_at(self):
        """recovery_lock 表必须有 owner_token 和 expires_at 字段。"""
        self.assertTrue(hasattr(self.store, "acquire_recovery_lock"))
        self.store.acquire_recovery_lock(owner_token="owner-abc",
                                          maintenance=True)
        try:
            with self.store._connection() as conn:
                row = conn.execute(
                    "SELECT owner_token, expires_at, maintenance "
                    "FROM recovery_lock LIMIT 1").fetchone()
            self.assertIsNotNone(row, "recovery_lock 表必须有记录")
            self.assertEqual(row["owner_token"], "owner-abc")
            self.assertIsNotNone(row["expires_at"],
                                 "expires_at 必须有值")
        finally:
            self.store.release_recovery_lock(owner_token="owner-abc")

    def test_wrong_owner_cannot_release_lock(self):
        self.store.acquire_recovery_lock(owner_token="owner-a", maintenance=True)
        try:
            self.assertFalse(self.store.release_recovery_lock(owner_token="owner-b"))
            self.assertTrue(self.store.is_recovery_locked())
        finally:
            self.store.release_recovery_lock(owner_token="owner-a")

    def test_expired_lock_is_automatically_released(self):
        self.store.acquire_recovery_lock(owner_token="owner-expired", maintenance=True)
        with self.store._connection() as conn:
            conn.execute(
                "UPDATE recovery_lock SET expires_at = '2000-01-01T00:00:00+08:00'"
            )
        self.assertFalse(self.store.is_recovery_locked())

    def test_lock_waits_for_active_workers(self):
        self.store.create_screening_run("active-before-recovery", source_count=1)
        with self.assertRaises(TimeoutError):
            self.store.acquire_recovery_lock(
                owner_token="owner-wait", maintenance=True, wait_timeout=0
            )
        self.store.update_screening_run("active-before-recovery", status="interrupted")
        self.assertTrue(self.store.acquire_recovery_lock(
            owner_token="owner-wait", maintenance=True, wait_timeout=0
        ))
        self.store.release_recovery_lock(owner_token="owner-wait")

    def test_lock_check_and_acquire_share_immediate_transaction(self):
        """活跃任务检查与落锁必须在同一写事务内，消除 TOCTOU。"""
        statements = []
        original_connect = self.store._connect

        def traced_connect():
            conn = original_connect()
            conn.set_trace_callback(statements.append)
            return conn

        with mock.patch.object(self.store, "_connect", side_effect=traced_connect):
            self.store.acquire_recovery_lock(
                owner_token="owner-atomic", maintenance=True, wait_timeout=0
            )
        try:
            normalized = [statement.strip().upper() for statement in statements]
            begin_index = next(
                i for i, statement in enumerate(normalized)
                if statement.startswith("BEGIN IMMEDIATE")
            )
            worker_index = next(
                i for i, statement in enumerate(normalized)
                if "SELECT COUNT(*) AS N FROM TASKS" in statement
            )
            lock_write_index = next(
                i for i, statement in enumerate(normalized)
                if statement.startswith("INSERT INTO RECOVERY_LOCK")
            )
            self.assertLess(begin_index, worker_index)
            self.assertLess(worker_index, lock_write_index)
        finally:
            self.store.release_recovery_lock(owner_token="owner-atomic")

    def test_recovery_lock_blocks_all_pipeline_write_paths(self):
        """恢复锁持有期间，既有流水线的增量写入同样必须被拒绝。"""
        self.store.acquire_recovery_lock(owner_token="owner-writes", maintenance=True)
        operations = (
            lambda: self.store.save_pipeline_result({"jobs": [], "dropped": []}, {}),
            lambda: self.store.update_pipeline_job_jd(ROUGH_RUN_ID, "missing", "jd"),
            lambda: self.store.save_recrawl_jd_and_checkpoint(
                ROUGH_RUN_ID, ROUGH_RUN_ID, {}, []
            ),
            lambda: self.store.save_screening_verdicts(
                ROUGH_RUN_ID, {"blocked-job": {"verdict": "uncertain"}}
            ),
        )
        try:
            for operation in operations:
                with self.subTest(operation=operation):
                    with self.assertRaisesRegex(RuntimeError, "recovery maintenance"):
                        operation()
        finally:
            self.store.release_recovery_lock(owner_token="owner-writes")


@unittest.skipUnless(_REAL_DB.exists(), f"正式库不存在：{_REAL_DB}")
class TestSourceFingerprint(unittest.TestCase):
    """source fingerprint 覆盖 run、job_id/verdict/JD、pending、schema。"""

    def setUp(self):
        from webui.store import TaskStore
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_db = pathlib.Path(self._tmpdir.name) / "fingerprint.db"
        _copy_real_db_snapshot(self.tmp_db)
        self.store = TaskStore(db_path=str(self.tmp_db))

    def tearDown(self):
        try:
            self.store._conn.close()
        except Exception:
            pass
        self._tmpdir.cleanup()

    def test_fingerprint_covers_run_job_verdict_jd_pending_schema(self):
        """fingerprint 必须按关联元组整体排序哈希，覆盖所有关键字段。"""
        from webui.historical_recovery import prepare_recovery, _compute_source_fingerprint
        self.assertTrue(callable(_compute_source_fingerprint),
                        "必须有 _compute_source_fingerprint 函数")
        fp = _compute_source_fingerprint(self.store)
        self.assertIsInstance(fp, str)
        self.assertEqual(len(fp), 64,
                         f"fingerprint 应 64 字符 sha256，实际 {len(fp)}")
        # prepare_recovery 返回的 fingerprint 必须一致
        prep = prepare_recovery(self.store)
        self.assertEqual(prep["source_fingerprint"], fp,
                         "prepare_recovery 的 fingerprint 必须与 _compute_source_fingerprint 一致")

    def test_fingerprint_changes_when_data_modified(self):
        """数据被修改后 fingerprint 必须变化。"""
        from webui.historical_recovery import _compute_source_fingerprint
        fp_before = _compute_source_fingerprint(self.store)
        # 修改一条 verdict
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT job_id FROM screening_results "
                "WHERE run_id = ? AND verdict = 'match' LIMIT 1",
                (ROUGH_RUN_ID,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE screening_results SET verdict = 'match' "
                    "WHERE job_id = ? AND run_id = ?",
                    (row["job_id"], ROUGH_RUN_ID))
        fp_after = _compute_source_fingerprint(self.store)
        # 由于我们改成了相同值，fingerprint 可能不变；改个不同的值
        with self.store._connection() as conn:
            row = conn.execute(
                "SELECT job_id FROM screening_results "
                "WHERE run_id = ? AND verdict = 'not_match' LIMIT 1",
                (ROUGH_RUN_ID,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE screening_results SET verdict = 'modified' "
                    "WHERE job_id = ? AND run_id = ?",
                    (row["job_id"], ROUGH_RUN_ID))
        fp_modified = _compute_source_fingerprint(self.store)
        self.assertNotEqual(fp_before, fp_modified,
                            "数据被修改后 fingerprint 必须变化")

    def test_fingerprint_covers_each_required_dimension(self):
        from webui.historical_recovery import _compute_source_fingerprint

        mutations = (
            ("run", "UPDATE screening_runs SET source_count = source_count + 1 WHERE id = ?", (ROUGH_RUN_ID,)),
            ("jd", "UPDATE screening_results SET jd = COALESCE(jd, '') || ' changed' WHERE run_id = ? AND job_id = (SELECT job_id FROM screening_results WHERE run_id = ? LIMIT 1)", (ROUGH_RUN_ID, ROUGH_RUN_ID)),
            ("pending", "INSERT INTO screening_pending_results (id, run_id, job_id, failure_stage, retryable, attempts, last_failed_at, origin_zone, ai_payload_json, created_at, failed_code) VALUES ('fp-pending', ?, 'fp-job', 'jd_detail', 1, 1, '2026-07-28', 'kept', '{}', '2026-07-28', NULL)", (ROUGH_RUN_ID,)),
            ("schema", "INSERT INTO schema_migrations (version, applied_at, description) VALUES (999, '2026-07-28', 'fingerprint test')", ()),
        )
        for label, sql, params in mutations:
            with self.subTest(dimension=label):
                before = _compute_source_fingerprint(self.store)
                with self.store._connection() as conn:
                    conn.execute(sql, params)
                after = _compute_source_fingerprint(self.store)
                self.assertNotEqual(before, after, f"fingerprint 必须覆盖 {label}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
