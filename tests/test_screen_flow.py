import json
import pathlib
import tempfile
import unittest
from unittest import mock

from webui.screen_flow import (
    build_round_context_payload,
    build_round_script_params,
    find_resumable_screen_run,
    load_resume_jd,
    load_resume_verdicts_with_fallback,
)
from webui.store import TaskStore

FILTERS = {"salary": ["20-30K"], "experience": ["3-5年"]}
FACTS = {"stable_key": "years", "value": "3"}


def _make_parent(store, scrape_task_id="scrape-1"):
    store.create_screening_run(
        scrape_task_id,
        source_count=1,
        execution_params={
            "script_params": {
                "keyword": "Python,后端",
                "city": ["上海"],
                "pages": 3,
            },
            "platform": "boss",
        },
    )


def _make_ai_run(store, run_id="screen-1", scrape_task_id="scrape-1",
                 status="paused", filters=None, profile="3年Python后端",
                 facts=None):
    store.create_screening_run(
        run_id,
        frozen_filters=FILTERS if filters is None else filters,
        source_count=10,
        execution_params={
            "platform": "boss",
            "scrape_task_id": scrape_task_id,
            "profile_summary": profile,
            "profile_facts": facts if facts is not None else FACTS,
        },
    )
    store.update_screening_run(run_id, status="running")
    store.update_screening_run(run_id, status=status)
    return store.get_screening_run(run_id)


class ScreenFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self.temp.name) / "state" / "webui.db"
        self.store = TaskStore(self.db_path)
        _make_parent(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def _find(self, filters=None, profile="3年Python后端", facts=None):
        return find_resumable_screen_run(
            self.store, "scrape-1",
            filters if filters is not None else FILTERS,
            profile,
            facts if facts is not None else FACTS,
        )

    def test_find_resumable_prefers_paused_over_newer_failed(self):
        _make_ai_run(self.store, "failed-run", status="failed")
        _make_ai_run(self.store, "paused-run", status="paused")
        run = self._find()
        self.assertIsNotNone(run)
        self.assertEqual(run["id"], "paused-run")

    def test_ai_keep_all_fallback_is_degraded_and_not_complete(self):
        """033 V2 T034：AI 请求失败全部保留也不得生成完整成功。"""
        from webui.ai import AISecurityError, ERROR_NETWORK, screen_jobs
        from webui.whitebox import WhiteboxService

        jobs = [{"job_id": "j1", "title": "岗位", "salary": "", "location": ""}]
        with mock.patch("webui.ai.call_ai", side_effect=AISecurityError(ERROR_NETWORK)):
            result = screen_jobs(
                jobs, {"profile_summary": "画像"}, "https://ai.invalid", "key",
                batch_size=1, raise_on_systemic=False,
            )
        self.assertTrue(result["degraded"])
        self.assertFalse(result["normal_screening_completed"])
        self.assertEqual(result["kept"], ["j1"])

        service = WhiteboxService(self.store)
        ref = service.begin("screening", "screen-ai", {
            "stages": ["ai_rough"], "units": [{"unit_key": "ai_rough"}],
        })
        for event_type, key, payload in (
            ("ai_request_failed", "request", {"reason_code": ERROR_NETWORK,
                                                 "action": "keep_all",
                                                 "normal_screening_completed": False}),
            ("unit_incomplete", "incomplete", {"stop_reason": "ai_keep_all_fallback"}),
        ):
            service.record(ref, {
                "idempotency_key": f"{key}:screen-ai", "event_type": event_type,
                "occurred_at": "2026-09-05T00:00:00+08:00", "stage": "ai_rough",
                "unit_key": "ai_rough", "required_evidence": True,
                "payload": payload,
            })
        integrity = service.finalize(ref)
        self.assertEqual(integrity["conclusion"], "unverifiable")
        self.assertTrue(integrity["degraded"])

    def test_find_resumable_failed(self):
        _make_ai_run(self.store, "failed-run", status="failed")
        self.assertEqual(self._find()["id"], "failed-run")

    def test_find_resumable_interrupted_user_finished(self):
        _make_ai_run(self.store, "finished-run", status="interrupted")
        self.store.update_screening_run(
            "finished-run", error_code="user_finished",
            error_reason="用户提前结束",
        )
        self.assertEqual(self._find()["id"], "finished-run")

    def test_find_resumable_partial(self):
        _make_ai_run(self.store, "partial-run", status="partial")
        self.assertEqual(self._find()["id"], "partial-run")

    def test_find_resumable_ignores_user_cancelled_interrupted(self):
        _make_ai_run(self.store, "cancel-run", status="interrupted")
        self.store.update_screening_run(
            "cancel-run", error_code="user_cancelled", error_reason="用户取消")
        self.assertIsNone(self._find())

    def test_find_resumable_requires_same_fields_profile_and_facts(self):
        _make_ai_run(self.store, "paused-run", status="paused")
        self.assertIsNone(self._find(filters={"salary": ["30-50K"]}))
        self.assertIsNone(self._find(profile="其它画像"))
        self.assertIsNone(self._find(facts={"stable_key": "city", "value": "上海"}))

    def test_find_resumable_recruiter_activity_full_dict_compare(self):
        """028 FR-009：第 7 类随 frozen_filters 全字典比对——选档位差异即不复用。

        未选第 7 类时 frozen_filters 与旧形态一致，既有复用行为不变（不限=等价）。
        """
        _make_ai_run(self.store, "paused-run", status="paused")
        # 旧 run 未选第 7 类；本次选中 → 不复用
        self.assertIsNone(self._find(filters=dict(
            FILTERS, recruiter_activity=["week"])))
        # 旧 run 选中档位；本次档位不同 → 不复用
        _make_ai_run(
            self.store, "paused-run-week", status="paused",
            filters=dict(FILTERS, recruiter_activity=["week"]),
        )
        self.assertIsNone(self._find(filters=dict(
            FILTERS, recruiter_activity=["quarter"])))
        # 档位一致 → 复用
        self.assertIsNotNone(self._find(filters=dict(
            FILTERS, recruiter_activity=["week"])))

    def test_find_resumable_normalizes_facts_order(self):
        _make_ai_run(self.store, "paused-run", status="paused")
        reordered = {"value": "3", "stable_key": "years"}
        run = self._find(facts=reordered)
        self.assertIsNotNone(run)

    def test_build_round_script_params_merges_parent_params(self):
        run = _make_ai_run(self.store, "paused-run", status="paused")
        params = build_round_script_params(self.store, run, FILTERS, "boss")
        self.assertEqual(params["keyword"], "Python,后端")
        self.assertEqual(params["city"], ["上海"])
        self.assertEqual(params["screening"], FILTERS)
        self.assertEqual(params["platform"], "boss")

    def test_build_round_context_payload_fields_complete(self):
        run = _make_ai_run(self.store, "paused-run", status="paused")
        ctx = build_round_context_payload(self.store, run)
        self.assertEqual(ctx["platform"], "boss")
        self.assertEqual(ctx["keywords"], ["Python", "后端"])
        self.assertEqual(ctx["cities"], ["上海"])
        self.assertEqual(ctx["screening_fields"], FILTERS)
        self.assertEqual(ctx["profile_summary"], "3年Python后端")
        self.assertEqual(ctx["profile_facts"], FACTS)
        self.assertEqual(ctx["scrape_task_id"], "scrape-1")
        self.assertEqual(ctx["screen_run_id"], "paused-run")
        self.assertEqual(ctx["status"], "paused")
        self.assertTrue(ctx["resumable"])
        self.assertTrue(ctx["has_frozen_filters"])

    def test_build_round_context_payload_user_finished_is_closed(self):
        """结束并保存后 round_context 必须持久化为不可续的阶段性完成态。"""
        _make_ai_run(self.store, "finished-run", status="interrupted")
        self.store.update_screening_run(
            "finished-run", error_code="user_finished",
            error_reason="用户提前结束，已保存部分结果",
        )
        ctx = build_round_context_payload(
            self.store, self.store.get_screening_run("finished-run"),
        )
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["status"], "partial")
        self.assertFalse(ctx["resumable"])
        self.assertEqual(ctx["screen_run_id"], "finished-run")

    def test_build_round_context_empty_filters_are_valid_unlimited_conditions(self):
        _make_ai_run(self.store, "unlimited-run", status="paused", filters={})
        ctx = build_round_context_payload(self.store, self.store.get_screening_run("unlimited-run"))
        self.assertEqual(ctx["screening_fields"], {})
        self.assertFalse(ctx["has_frozen_filters"])
    def test_build_round_context_from_snapshot_with_screen_run_id(self):
        _make_ai_run(self.store, "paused-run", status="paused")
        snapshot_id = self.store.save_pipeline_result(
            {
                "ok": True, "jobs": [], "dropped": [],
                "total_scraped": 0, "total_kept": 0,
            },
            {"platform": "boss"},
            status="partial",
            execution_params={
                "platform": "boss",
                "scrape_task_id": "scrape-1",
                "screen_run_id": "paused-run",
            },
        )
        snapshot = self.store.get_screening_run(snapshot_id)
        ctx = build_round_context_payload(self.store, snapshot)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["screen_run_id"], "paused-run")

    def test_build_round_context_from_scraped_only_snapshot(self):
        run_id = self.store.save_scraped_only_snapshot(
            {
                "ok": True,
                "jobs": [{"platform_job_id": "j1", "title": "岗位"}],
                "dropped": [], "total_scraped": 1,
            },
            {"platform": "boss", "keyword": "Python", "city": ["上海"]},
            scrape_task_id="scrape-1", platform="boss",
            profile_summary="3年Python后端", profile_facts=FACTS,
        )
        ctx = build_round_context_payload(self.store, self.store.get_screening_run(run_id))
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["keywords"], ["Python"])
        self.assertEqual(ctx["cities"], ["上海"])
        self.assertEqual(ctx["scrape_task_id"], "scrape-1")
        self.assertEqual(ctx["profile_summary"], "3年Python后端")
        self.assertEqual(ctx["profile_facts"], FACTS)
        self.assertFalse(ctx["resumable"])

    def test_build_round_context_paused_scrape_without_scrape_task_id(self):
        run_id = "paused-scrape"
        self.store.create_screening_run(
            run_id, source_count=1,
            execution_params={
                "platform": "boss",
                "script_params": {"keyword": "Go", "city": ["北京"]},
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(run_id, status="paused", current_stage="scrape")
        ctx = build_round_context_payload(self.store, self.store.get_screening_run(run_id))
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["keywords"], ["Go"])
        self.assertEqual(ctx["cities"], ["北京"])
        self.assertEqual(ctx["scrape_task_id"], "")
        self.assertTrue(ctx["resumable"])
        self.assertEqual(ctx["status"], "paused")

    def test_build_round_context_from_snapshot_falls_back_to_latest_run(self):
        _make_ai_run(self.store, "paused-run", status="paused")
        snapshot_id = self.store.save_pipeline_result(
            {
                "ok": True, "jobs": [], "dropped": [],
                "total_scraped": 0, "total_kept": 0,
            },
            {"platform": "boss"},
            status="partial",
            execution_params={"platform": "boss", "scrape_task_id": "scrape-1"},
        )
        snapshot = self.store.get_screening_run(snapshot_id)
        ctx = build_round_context_payload(self.store, snapshot)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["screen_run_id"], "paused-run")


class LoadResumeJdTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_load_resume_jd_falls_back_to_screening_results(self):
        snapshot_id = self.store.save_pipeline_result(
            {
                "ok": True,
                "jobs": [{"platform_job_id": "pid-1", "title": "岗位", "jd": "回退 JD"}],
                "dropped": [], "total_scraped": 1,
            },
            {"platform": "boss"},
        )
        missing = pathlib.Path(self.temp.name) / "missing.json"
        self.assertEqual(
            load_resume_jd(self.store, str(missing), snapshot_id), {"pid-1": "回退 JD"})

    def test_load_resume_jd_prefers_checkpoint_file(self):
        run_id = "jd-file-first"
        checkpoint = pathlib.Path(self.temp.name) / "jd.json"
        checkpoint.write_text(json.dumps({"pid-1": "文件 JD"}), encoding="utf-8")
        self.assertEqual(
            load_resume_jd(self.store, str(checkpoint), run_id), {"pid-1": "文件 JD"})


class LoadResumeVerdictsTests(unittest.TestCase):
    """018：判定回退 = 同源链合并（同抓取、同条件、同画像、新覆盖旧）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        _make_parent(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def _seed_chain_run(self, run_id, verdicts=None, checkpoint=None,
                        filters=None, profile="3年Python后端", facts=None):
        _make_ai_run(
            self.store, run_id, status="failed",
            filters=filters if filters is not None else FILTERS,
            profile=profile, facts=facts if facts is not None else FACTS,
        )
        if checkpoint is not None:
            self.store.save_checkpoint(run_id, "ai_rough", checkpoint)
        if verdicts:
            self.store.save_screening_verdicts(run_id, verdicts)
        return run_id

    def _load(self, run_id):
        return load_resume_verdicts_with_fallback(
            self.store, run_id, "boss", "scrape-1", FILTERS,
            "3年Python后端", profile_facts=FACTS)

    def test_merges_same_source_chain_verdicts(self):
        """完整判定挂在链上第一条 run 名下时，续跑合并后全部可见。"""
        self._seed_chain_run(
            "run1",
            {"a": {"verdict": "kept", "reason": ""},
             "b": {"verdict": "dropped", "reason": "经验不符"},
             "c": {"verdict": "dropped", "reason": "薪资不符"}},
            checkpoint=["a", "b", "c"],
        )
        current = self._seed_chain_run(
            "current",
            {"a": {"verdict": "not_match", "reason": "跨链路"}},
            checkpoint=["a", "b", "c"],
        )
        verdicts = self._load(current)
        self.assertEqual(set(verdicts), {"a", "b", "c"})
        # 当前 run 自身判定最终覆盖链上旧判定
        self.assertEqual(verdicts["a"]["verdict"], "not_match")
        self.assertEqual(verdicts["b"]["verdict"], "dropped")
        self.assertEqual(verdicts["c"]["verdict"], "dropped")

    def test_newer_run_overrides_older_across_chain(self):
        self._seed_chain_run("old", {"x": {"verdict": "kept", "reason": ""}})
        self._seed_chain_run("mid", {"x": {"verdict": "not_match", "reason": "跨链路"}})
        current = self._seed_chain_run("current", checkpoint=["x"])
        verdicts = self._load(current)
        self.assertEqual(verdicts["x"]["verdict"], "not_match")

    def test_skips_runs_with_different_conditions(self):
        self._seed_chain_run(
            "run1",
            {"b": {"verdict": "dropped", "reason": "经验不符"}},
            filters={"salary": ["30-50K"]},
        )
        current = self._seed_chain_run(
            "current",
            {"a": {"verdict": "kept", "reason": ""}},
            checkpoint=["a", "b"],
        )
        verdicts = self._load(current)
        self.assertEqual(set(verdicts), {"a"})

    def test_skips_runs_with_different_profile_or_facts(self):
        self._seed_chain_run(
            "run1", {"b": {"verdict": "dropped", "reason": "经验不符"}},
            profile="另一个画像",
        )
        self._seed_chain_run(
            "run2", {"c": {"verdict": "dropped", "reason": "薪资不符"}},
            facts={"stable_key": "years", "value": "5"},
        )
        current = self._seed_chain_run(
            "current",
            {"a": {"verdict": "kept", "reason": ""}},
            checkpoint=["a", "b", "c"],
        )
        verdicts = self._load(current)
        self.assertEqual(set(verdicts), {"a"})

    def test_returns_own_verdicts_when_checkpoint_covered(self):
        """断点岗位全部有判定（覆盖完整）时不触发回退，返回 run 自身判定。"""
        self._seed_chain_run(
            "run1", {"b": {"verdict": "dropped", "reason": "经验不符"}})
        current = self._seed_chain_run(
            "current",
            {"a": {"verdict": "kept", "reason": ""}},
            checkpoint=["a"],
        )
        verdicts = self._load(current)
        self.assertEqual(set(verdicts), {"a"})

    def test_count_enough_but_keys_do_not_cover_still_merges(self):
        """020 US6：判定数够但键集不覆盖断点（精筛判定计入总数）→ 仍合并，
        run1 的 dropped 不复活。"""
        self._seed_chain_run(
            "run1", {"b": {"verdict": "dropped", "reason": "经验不符"}})
        # 断点 [a, b]；当前 run 判定 {a, x}：数量 2 >= 2 但 b 无判定
        current = self._seed_chain_run(
            "current",
            {"a": {"verdict": "not_match", "reason": "跨链路"},
             "x": {"verdict": "match", "reason": "精筛"}},
            checkpoint=["a", "b"],
        )
        verdicts = self._load(current)
        self.assertEqual(set(verdicts), {"a", "b", "x"})
        self.assertEqual(verdicts["b"]["verdict"], "dropped",
                         "断点岗位缺判定必须从链上合并，dropped 不复活")

    def test_full_coverage_skips_merge(self):
        """断点 ⊆ 判定键集（全覆盖）→ 跳过合并（回归）。"""
        self._seed_chain_run(
            "run1", {"z": {"verdict": "dropped", "reason": "无关岗位"}})
        current = self._seed_chain_run(
            "current",
            {"a": {"verdict": "kept", "reason": ""},
             "b": {"verdict": "match", "reason": "精筛"}},
            checkpoint=["a", "b"],
        )
        verdicts = self._load(current)
        self.assertEqual(set(verdicts), {"a", "b"})


class DomainVerdictGuardTests(unittest.TestCase):
    """B094 T005：判定恢复守卫——自身提前返回、同源回退与版本摘要。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        _make_parent(self.store)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _selection(label):
        from webui import ai_domain_policy as domain_policy
        return domain_policy.selection_from_snapshot(
            snapshot=None, platform="boss", current_labels=[label],
        )

    def _seed(self, run_id, *, metadata=None, verdicts=None, checkpoint=None,
              filters=None):
        _make_ai_run(
            self.store, run_id, status="failed",
            filters=filters if filters is not None else FILTERS,
        )
        if metadata is not None:
            from webui import ai_domain_policy as domain_policy
            params = dict(self.store.get_screening_run(run_id)["execution_params"])
            params.update(domain_policy.run_metadata(metadata))
            with self.store._connection() as conn:
                conn.execute(
                    "UPDATE screening_runs SET execution_params_json = ? WHERE id = ?",
                    (json.dumps(params, ensure_ascii=False), run_id),
                )
        if checkpoint is not None:
            self.store.save_checkpoint(run_id, "ai_rough", checkpoint)
        if verdicts:
            self.store.save_screening_verdicts(run_id, verdicts)
        return run_id

    def test_unversioned_domain_candidate_is_skipped(self):
        self._seed("old-run", filters=dict(FILTERS, industry=["1001"]))
        self.assertIsNone(find_resumable_screen_run(
            self.store, "scrape-1", dict(FILTERS, industry=["1001"]),
            "3年Python后端", FACTS, domain_selection=self._selection("互联网"),
        ))

    def test_different_domain_semantics_is_skipped(self):
        self._seed(
            "internet-run", filters=dict(FILTERS, industry=["1001"]),
            metadata=self._selection("互联网"),
        )
        self.assertIsNone(find_resumable_screen_run(
            self.store, "scrape-1", dict(FILTERS, industry=["1001"]),
            "3年Python后端", FACTS, domain_selection=self._selection("游戏"),
        ))

    def test_same_version_and_semantics_is_resumable(self):
        self._seed(
            "internet-run", filters=dict(FILTERS, industry=["1001"]),
            metadata=self._selection("互联网"),
        )
        run = find_resumable_screen_run(
            self.store, "scrape-1", dict(FILTERS, industry=["1001"]),
            "3年Python后端", FACTS, domain_selection=self._selection("互联网"),
        )
        self.assertIsNotNone(run)
        self.assertEqual(run["id"], "internet-run")

    def test_no_domain_selection_keeps_legacy_resume(self):
        self._seed("plain-run")
        run = find_resumable_screen_run(
            self.store, "scrape-1", FILTERS, "3年Python后端", FACTS,
        )
        self.assertIsNotNone(run)
        self.assertEqual(run["id"], "plain-run")

    def _load(self, run_id, selection):
        return load_resume_verdicts_with_fallback(
            self.store, run_id, "boss", "scrape-1", FILTERS,
            "3年Python后端", profile_facts=FACTS, domain_selection=selection,
        )

    def test_own_verdicts_blocked_before_early_return(self):
        """旧语义 run 即便断点已被自身判定覆盖，也不得把旧判定交回。"""
        run_id = self._seed(
            "stale", metadata=self._selection("游戏"),
            verdicts={"a": {"verdict": "kept", "reason": ""}},
            checkpoint=["a"],
        )
        self.assertEqual(self._load(run_id, self._selection("互联网")), {})

    def test_same_semantics_returns_own_verdicts(self):
        run_id = self._seed(
            "current", metadata=self._selection("互联网"),
            verdicts={"a": {"verdict": "kept", "reason": ""}},
            checkpoint=["a"],
        )
        self.assertEqual(
            set(self._load(run_id, self._selection("互联网"))), {"a"}
        )

    def test_chain_fallback_checks_every_candidate(self):
        self._seed(
            "chain-stale",
            verdicts={"b": {"verdict": "dropped", "reason": "行业不符"}},
        )
        current = self._seed(
            "current", metadata=self._selection("互联网"),
            verdicts={"a": {"verdict": "kept", "reason": ""}},
            checkpoint=["a", "b"],
        )
        merged = self._load(current, self._selection("互联网"))
        self.assertEqual(set(merged), {"a"})

    def test_missing_context_does_not_assume_old_domain_verdicts(self):
        """调用方没传领域上下文时，未版本化的旧领域判定仍不得默认可复用。"""
        run_id = self._seed(
            "legacy-domain-run", filters=dict(FILTERS, industry=["1001"]),
            verdicts={"a": {"verdict": "kept", "reason": ""}},
            checkpoint=["a"],
        )
        self.assertEqual(load_resume_verdicts_with_fallback(
            self.store, run_id, "boss", "scrape-1",
            dict(FILTERS, industry=["1001"]), "3年Python后端",
            profile_facts=FACTS,
        ), {})


class MaterialJdModeTests(unittest.TestCase):
    """B094 T007：JD 资料模式合并文件与结果表，默认续跑行为不变。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")

    def tearDown(self):
        self.temp.cleanup()

    def _rows(self, run_id, rows):
        with self.store._connection() as conn:
            for index, (pid, jd, dropped) in enumerate(rows):
                conn.execute(
                    "INSERT INTO screening_results "
                    "(id, run_id, platform, platform_job_id, verdict, created_at, "
                    "is_dropped, jd) VALUES (?, ?, 'boss', ?, '', "
                    "'2026-10-02T00:00:00', ?, ?)",
                    (f"{run_id}-{index}", run_id, pid, int(dropped), jd),
                )

    def test_material_mode_merges_file_and_result_table(self):
        run_id = "mixed"
        self.store.create_screening_run(run_id, source_count=1)
        self._rows(run_id, [("pid-1", "结果表保留行", 0), ("pid-2", "结果表剔除行", 1)])
        path = pathlib.Path(self.temp.name) / "jd.json"
        path.write_text(json.dumps({"pid-1": "文件原文"}), encoding="utf-8")
        self.assertEqual(
            load_resume_jd(self.store, str(path), run_id),
            {"pid-1": "文件原文"},
        )
        self.assertEqual(
            load_resume_jd(self.store, str(path), run_id, include_dropped=True),
            {"pid-1": "文件原文", "pid-2": "结果表剔除行"},
        )

    def test_material_mode_without_file_reads_result_table(self):
        run_id = "no-file"
        self.store.create_screening_run(run_id, source_count=1)
        self._rows(run_id, [("pid-1", "只有结果表有", 1)])
        missing = str(pathlib.Path(self.temp.name) / "missing.json")
        self.assertEqual(
            load_resume_jd(self.store, missing, run_id, include_dropped=True),
            {"pid-1": "只有结果表有"},
        )
        self.assertEqual(load_resume_jd(self.store, missing, run_id), {})

    def test_corrupted_file_still_fails_in_material_mode(self):
        run_id = "broken"
        self.store.create_screening_run(run_id, source_count=1)
        path = pathlib.Path(self.temp.name) / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            load_resume_jd(self.store, str(path), run_id, include_dropped=True)


if __name__ == "__main__":
    unittest.main()
