"""B094 领域条件来源与恢复兼容（T002、T005、T007 失败用例）。

只使用临时 TaskStore 与构造快照，证明条件来源、身份隔离与恢复契约；
不调用真实模型，不读正式数据库。
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace

from webui import ai_domain_policy as policy
from webui.ai_domain_context import (
    DomainContextError,
    ScreeningPolicyIncompatibleError,
    build_screening_context,
    ensure_resume_compatible,
    load_source_jd_materials,
)
from webui.store import TaskStore

INTERNET_GROUP = "互联网/AI/软件/IT服务"
GAME_GROUP = "游戏/数字文娱"

BOSS_INTERNET_CODE = "1001"
BOSS_GAME_CODE = "1004"


def snapshot(*, unified_industry, boss_industry=None, zhilian_industry=None,
             overrides=None, zhilian_overrides=None):
    """构造一份 B096 V2 条件快照（结构与前端 buildConditionSnapshot 一致）。"""
    return {
        "snapshotVersion": 2,
        "mappingVersion": "b096-v2-2026-09-27",
        "unifiedValues": {
            "salary": [], "experience": [], "degree": [],
            "industry": list(unified_industry), "scale": [],
            "recruiter_activity": [],
        },
        "platformValues": {
            "boss": {"industry": list(boss_industry or []), "stage": []},
            "zhilian": {"industry": list(zhilian_industry or [])},
        },
        "overrides": {
            "boss": dict(overrides or {}), "zhilian": dict(zhilian_overrides or {}),
        },
        "exclusiveValues": {
            "boss": {"stage": []}, "zhilian": {"company_nature": []},
        },
    }


class PolicySourceTests(unittest.TestCase):
    """FR-007：沿用现有行业组选项，按整组含义理解。"""

    def test_original_group_labels_are_kept_whole(self):
        selection = policy.selection_from_snapshot(
            snapshot=snapshot(
                unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
            ),
            platform="boss",
            current_labels=["互联网"],
        )
        self.assertEqual(selection.labels, (INTERNET_GROUP,))
        self.assertEqual(selection.source_kind, policy.SOURCE_ORIGINAL_GROUP)
        self.assertTrue(selection.has_selection)

    def test_multiple_groups_take_union_not_intersection(self):
        selection = policy.selection_from_snapshot(
            snapshot=snapshot(
                unified_industry=[INTERNET_GROUP, GAME_GROUP],
                boss_industry=["互联网", "游戏"],
            ),
            platform="boss",
            current_labels=["互联网", "游戏"],
        )
        self.assertEqual(set(selection.labels), {INTERNET_GROUP, GAME_GROUP})
        self.assertEqual(selection.source_kind, policy.SOURCE_ORIGINAL_GROUP)

    def test_same_platform_code_from_different_groups_is_not_equivalent(self):
        internet = policy.selection_from_snapshot(
            snapshot=snapshot(
                unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
            ),
            platform="boss", current_labels=["互联网"],
        )
        override = policy.selection_from_snapshot(
            snapshot=snapshot(
                unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
                overrides={"industry": ["互联网"]},
            ),
            platform="boss", current_labels=["互联网"],
        )
        self.assertEqual(internet.labels, (INTERNET_GROUP,))
        self.assertEqual(override.labels, ("互联网",))
        self.assertNotEqual(
            policy.semantic_summary(internet), policy.semantic_summary(override)
        )

    def test_platform_override_wins_over_original_group(self):
        selection = policy.selection_from_snapshot(
            snapshot=snapshot(
                unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
                overrides={"industry": ["游戏"]},
            ),
            platform="boss", current_labels=["游戏"],
        )
        self.assertEqual(selection.labels, ("游戏",))
        self.assertEqual(selection.source_kind, policy.SOURCE_PLATFORM_OVERRIDE)

    def test_explicit_clear_does_not_revive_original_group(self):
        selection = policy.selection_from_snapshot(
            snapshot=snapshot(
                unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
                overrides={"industry": []},
            ),
            platform="boss", current_labels=[],
        )
        self.assertFalse(selection.has_selection)
        self.assertEqual(selection.source_kind, policy.SOURCE_NONE)

    def test_changed_selection_is_read_from_current_conditions(self):
        selection = policy.selection_from_snapshot(
            snapshot=snapshot(
                unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
            ),
            platform="boss", current_labels=["游戏"],
        )
        self.assertEqual(selection.labels, ("游戏",))
        self.assertEqual(selection.source_kind, policy.SOURCE_PLATFORM_OVERRIDE)

    def test_removed_selection_cannot_borrow_the_frozen_group(self):
        selection = policy.selection_from_snapshot(
            snapshot=snapshot(
                unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
            ),
            platform="boss", current_labels=[],
        )
        self.assertFalse(selection.has_selection)

    def test_legacy_snapshot_without_version_uses_platform_labels(self):
        selection = policy.selection_from_snapshot(
            snapshot=None, platform="boss", current_labels=["互联网"],
        )
        self.assertEqual(selection.labels, ("互联网",))
        self.assertEqual(selection.source_kind, policy.SOURCE_LEGACY_PLATFORM)

    def test_corrupted_v2_snapshot_raises_instead_of_unlimited(self):
        broken = snapshot(
            unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
        )
        del broken["unifiedValues"]
        with self.assertRaises(policy.DomainSnapshotError):
            policy.selection_from_snapshot(
                snapshot=broken, platform="boss", current_labels=["互联网"],
            )

    def test_corrupted_v2_platform_layer_raises(self):
        broken = snapshot(
            unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
        )
        broken["platformValues"] = {"boss": []}
        with self.assertRaises(policy.DomainSnapshotError):
            policy.selection_from_snapshot(
                snapshot=broken, platform="boss", current_labels=["互联网"],
            )

    def test_group_industry_option_labels_are_not_split_into_keywords(self):
        selection = policy.selection_from_snapshot(
            snapshot=snapshot(
                unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
            ),
            platform="boss", current_labels=["互联网"],
        )
        self.assertEqual(len(selection.labels), 1)
        self.assertIn("/", selection.labels[0])


class PolicyStateTests(unittest.TestCase):
    def test_metadata_round_trip_and_compatibility(self):
        selection = policy.selection_from_snapshot(
            snapshot=snapshot(
                unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
            ),
            platform="boss", current_labels=["互联网"],
        )
        params = policy.run_metadata(selection)
        self.assertTrue(
            policy.is_verdict_state_compatible(
                params, selection=selection, screening_fields={},
            )
        )
        other = policy.selection_from_snapshot(
            snapshot=snapshot(unified_industry=[GAME_GROUP], boss_industry=["游戏"]),
            platform="boss", current_labels=["游戏"],
        )
        self.assertFalse(
            policy.is_verdict_state_compatible(
                params, selection=other, screening_fields={},
            )
        )

    def test_unversioned_params_are_reusable_only_without_domain_selection(self):
        self.assertTrue(
            policy.is_verdict_state_compatible(
                {}, selection=policy.empty_selection(), screening_fields={},
            )
        )
        self.assertTrue(
            policy.is_verdict_state_compatible(
                {}, selection=None, screening_fields={"salary": ["406"]},
            )
        )
        self.assertFalse(
            policy.is_verdict_state_compatible(
                {}, selection=None,
                screening_fields={"industry": [BOSS_INTERNET_CODE]},
            )
        )
        self.assertFalse(
            policy.is_verdict_state_compatible(
                {}, selection=selection_with_internet(), screening_fields={},
            )
        )

    def test_other_rule_version_is_incompatible(self):
        params = {
            policy.METADATA_VERSION_KEY: "some-older-version",
            policy.METADATA_SUMMARY_KEY: "abc",
        }
        self.assertFalse(
            policy.is_verdict_state_compatible(
                params, selection=selection_with_internet(), screening_fields={},
            )
        )


def selection_with_internet():
    return policy.selection_from_snapshot(
        snapshot=snapshot(
            unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
        ),
        platform="boss", current_labels=["互联网"],
    )


class DomainContextIdentityTests(unittest.TestCase):
    """C1：上下文只准借这条 run 自己的 Track，身份冲突必须报错。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b094-context-")
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.profile_id = "b094-profile"
        with self.store._connection() as conn:
            conn.execute(
                "INSERT INTO candidate_profiles "
                "(id, name, confirmed_fields_json, ai_preference_json, created_at, updated_at) "
                "VALUES (?, 'b094', '{}', '{}', '2026-10-02', '2026-10-02')",
                (self.profile_id,),
            )
        self.ctx = SimpleNamespace(store=self.store)

    def tearDown(self):
        self.temp.cleanup()

    def _flow(self, *, boss_snapshot, zhilian_snapshot=None):
        self._seq = getattr(self, "_seq", 0) + 1
        flow = self.store.create_flow(
            profile_id=self.profile_id,
            selection="all",
            start_key=f"flow-{self._seq}",
            confirmed_filters={
                "boss": boss_snapshot,
                "zhilian": zhilian_snapshot or {},
            },
        )
        tracks = {}
        for platform in ("boss", "zhilian"):
            track = next(
                item for item in flow["tracks"] if item["platform"] == platform
            )
            source_id = f"source-{platform}"
            self.store.create_screening_run(
                source_id,
                profile_id=self.profile_id,
                execution_params={
                    "platform": platform, "flow_id": flow["id"],
                    "track_id": track["id"],
                },
            )
            self.store.bind_flow_track_runs(
                flow["id"], platform, profile_id=self.profile_id,
                scrape_run_id=source_id,
            )
            tracks[platform] = (source_id, track["id"])
        return flow, tracks

    def test_track_snapshot_of_own_platform_provides_original_group(self):
        flow, tracks = self._flow(
            boss_snapshot=snapshot(
                unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
            ),
        )
        source_id, track_id = tracks["boss"]
        context = build_screening_context(
            self.ctx,
            scrape_task_id=source_id,
            platform="boss",
            screening_fields={"industry": [BOSS_INTERNET_CODE]},
            profile_id=self.profile_id,
        )
        self.assertEqual(context.track_id, track_id)
        self.assertEqual(context.flow_id, flow["id"])
        self.assertEqual(context.selection.labels, (INTERNET_GROUP,))
        self.assertEqual(context.selection.source_kind, policy.SOURCE_ORIGINAL_GROUP)

    def test_each_platform_reads_its_own_track_snapshot(self):
        flow, tracks = self._flow(
            boss_snapshot=snapshot(
                unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
            ),
            zhilian_snapshot=snapshot(
                unified_industry=[GAME_GROUP], zhilian_industry=["网络游戏"],
            ),
        )
        boss_source, boss_track = tracks["boss"]
        zhilian_source, zhilian_track = tracks["zhilian"]
        boss_context = build_screening_context(
            self.ctx,
            scrape_task_id=boss_source,
            platform="boss",
            screening_fields={"industry": [BOSS_INTERNET_CODE]},
            profile_id=self.profile_id,
        )
        zhilian_context = build_screening_context(
            self.ctx,
            scrape_task_id=zhilian_source,
            platform="zhilian",
            screening_fields={"industry": ["网络游戏"]},
            profile_id=self.profile_id,
        )
        self.assertEqual(boss_context.track_id, boss_track)
        self.assertEqual(zhilian_context.track_id, zhilian_track)
        self.assertEqual(boss_context.selection.labels, (INTERNET_GROUP,))
        self.assertEqual(zhilian_context.selection.labels, (GAME_GROUP,))

    def test_foreign_profile_is_rejected(self):
        flow, tracks = self._flow(
            boss_snapshot=snapshot(
                unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
            ),
        )
        source_id, _ = tracks["boss"]
        with self.assertRaises((DomainContextError, KeyError)):
            build_screening_context(
                self.ctx,
                scrape_task_id=source_id,
                platform="boss",
                screening_fields={"industry": [BOSS_INTERNET_CODE]},
                profile_id="another-profile",
            )

    def test_platform_without_track_is_an_identity_conflict(self):
        flow, _tracks = self._flow(
            boss_snapshot=snapshot(
                unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
            ),
        )
        self.store.create_screening_run(
            "unbound-source",
            profile_id=self.profile_id,
            execution_params={
                "platform": "boss", "flow_id": flow["id"],
                "track_id": "not-a-track",
            },
        )
        with self.assertRaises(DomainContextError):
            build_screening_context(
                self.ctx,
                scrape_task_id="unbound-source",
                platform="boss",
                screening_fields={"industry": [BOSS_INTERNET_CODE]},
                profile_id=self.profile_id,
            )

    def test_legacy_run_without_flow_falls_back_to_platform_labels(self):
        self.store.create_screening_run(
            "legacy-source",
            execution_params={"platform": "boss"},
        )
        context = build_screening_context(
            self.ctx,
            scrape_task_id="legacy-source",
            platform="boss",
            screening_fields={"industry": ["互联网"]},
            profile_id=None,
        )
        self.assertIsNone(context.flow_id)
        self.assertEqual(context.selection.labels, ("互联网",))
        self.assertEqual(context.selection.source_kind, policy.SOURCE_LEGACY_PLATFORM)

    def test_corrupted_track_snapshot_surfaces_instead_of_unlimited(self):
        flow, tracks = self._flow(boss_snapshot={"snapshotVersion": 2, "junk": 1})
        source_id, _ = tracks["boss"]
        with self.assertRaises(DomainContextError):
            build_screening_context(
                self.ctx,
                scrape_task_id=source_id,
                platform="boss",
                screening_fields={"industry": [BOSS_INTERNET_CODE]},
                profile_id=self.profile_id,
            )


class ResumeCompatibilityTests(unittest.TestCase):
    """C4：判定恢复守卫（T005 部分用例）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b094-resume-")
        self.store = TaskStore(pathlib.Path(self.temp.name) / "state" / "webui.db")
        self.ctx = SimpleNamespace(store=self.store)

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, run_id, *, params=None, filters=None, status="paused"):
        self.store.create_screening_run(
            run_id,
            frozen_filters=filters if filters is not None
            else {"industry": [BOSS_INTERNET_CODE]},
            source_count=1,
            execution_params={
                "platform": "boss", "scrape_task_id": "source-1",
                **(params or {}),
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(run_id, status=status)

    def _context(self):
        return build_screening_context(
            self.ctx,
            scrape_task_id="source-1",
            platform="boss",
            screening_fields={"industry": [BOSS_INTERNET_CODE]},
            profile_id=None,
        )

    def test_explicit_resume_of_unversioned_domain_run_is_blocked(self):
        self._run("old-domain-run")
        with self.assertRaises(ScreeningPolicyIncompatibleError) as raised:
            ensure_resume_compatible(self.ctx, "old-domain-run", self._context())
        self.assertEqual(raised.exception.error_code, "screening_policy_incompatible")

    def test_explicit_resume_of_same_version_and_semantics_is_allowed(self):
        context = self._context()
        self._run("current-run", params=context.metadata())
        ensure_resume_compatible(self.ctx, "current-run", context)

    def test_explicit_resume_with_different_group_semantics_is_blocked(self):
        other = policy.selection_from_snapshot(
            snapshot=snapshot(
                unified_industry=[INTERNET_GROUP], boss_industry=["互联网"],
                overrides={"industry": ["互联网"]},
            ),
            platform="boss", current_labels=["互联网"],
        )
        self._run("other-semantic-run", params=policy.run_metadata(other))
        with self.assertRaises(ScreeningPolicyIncompatibleError):
            ensure_resume_compatible(self.ctx, "other-semantic-run", self._context())

    def test_no_domain_selection_keeps_existing_resume_path(self):
        self._run("plain-run", filters={"salary": ["406"]})
        context = build_screening_context(
            self.ctx,
            scrape_task_id="source-1",
            platform="boss",
            screening_fields={"salary": ["406"]},
            profile_id=None,
        )
        ensure_resume_compatible(self.ctx, "plain-run", context)

    def test_missing_resume_target_is_not_invented(self):
        ensure_resume_compatible(self.ctx, "", self._context())
        ensure_resume_compatible(self.ctx, "not-a-run", self._context())


class JdMaterialTests(unittest.TestCase):
    """C4a：JD 资料独立复用（T007 部分用例）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="b094-jd-")
        self.root = pathlib.Path(self.temp.name)
        self.store = TaskStore(self.root / "state" / "webui.db")

        def path_for(run_id):
            return str(self.root / f"jd-{run_id}.json")

        self.path_for = path_for

    def tearDown(self):
        self.temp.cleanup()

    def _screen_run(self, run_id, *, status="succeeded", platform="boss",
                    profile_id=None, params=None, created_at=None):
        self.store.create_screening_run(
            run_id,
            profile_id=profile_id,
            source_count=1,
            execution_params={
                "platform": platform, "scrape_task_id": "source-1",
                **(params or {}),
            },
        )
        self.store.update_screening_run(run_id, status="running")
        self.store.update_screening_run(run_id, status=status)
        if created_at is not None:
            with self.store._connection() as conn:
                conn.execute(
                    "UPDATE screening_runs SET created_at = ? WHERE id = ?",
                    (created_at, run_id),
                )
        return run_id

    def _write_checkpoint(self, run_id, payload):
        path = pathlib.Path(self.path_for(run_id))
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def _result_rows(self, run_id, rows):
        """按正式表结构写入 screening_results（含 is_dropped=1 行）。"""
        with self.store._connection() as conn:
            for index, row in enumerate(rows):
                conn.execute(
                    "INSERT INTO screening_results "
                    "(id, run_id, platform, platform_job_id, job_id, verdict, "
                    "created_at, is_dropped, jd, title, company) "
                    "VALUES (?, ?, 'boss', ?, ?, ?, '2026-10-02T00:00:00', ?, ?, ?, ?)",
                    (
                        f"{run_id}-{index}", run_id,
                        str(row.get("platform_job_id") or ""),
                        row.get("job_id"), str(row.get("verdict") or ""),
                        int(row.get("is_dropped") or 0), str(row.get("jd") or ""),
                        str(row.get("title") or ""), str(row.get("company") or ""),
                    ),
                )

    def _load(self, job_ids, *, profile_id=None, current_run_id="new-run"):
        return load_source_jd_materials(
            self.store,
            scrape_task_id="source-1",
            current_run_id=current_run_id,
            platform="boss",
            profile_id=profile_id,
            job_ids=job_ids,
            jd_path_for=self.path_for,
        )

    def test_material_survives_incompatible_verdicts(self):
        old = self._screen_run("old-run", status="failed")
        self._write_checkpoint(old, {"pid-1": "旧规则抓到的完整 JD"})
        loaded = self._load(["pid-1"])
        self.assertEqual(loaded, {"pid-1": "旧规则抓到的完整 JD"})

    def test_material_is_read_when_resume_id_is_empty(self):
        old = self._screen_run("old-run", status="succeeded")
        self._write_checkpoint(old, {"pid-1": "JD 原文"})
        loaded = self._load(["pid-1"])
        self.assertEqual(loaded["pid-1"], "JD 原文")

    def test_missing_checkpoint_falls_back_to_result_table_including_dropped(self):
        run_id = self._screen_run("dropped-run", status="succeeded")
        self._result_rows(run_id, [
            {
                "job_id": "internal-1", "platform_job_id": "pid-1",
                "title": "AI 产品销售", "jd": "被旧规则剔除但 JD 完整",
                "verdict": "dropped", "reason": "行业不符", "is_dropped": 1,
            },
        ])
        loaded = self._load(["pid-1"])
        self.assertEqual(loaded, {"pid-1": "被旧规则剔除但 JD 完整"})
        # 默认（旧续跑）行为不变：仍只读非剔除行
        self.assertEqual(self.store.load_screening_jd_map(run_id), {})
        self.assertEqual(
            self.store.load_screening_jd_map(run_id, include_dropped=True),
            {"pid-1": "被旧规则剔除但 JD 完整"},
        )

    def test_checkpoint_file_wins_over_result_table(self):
        run_id = self._screen_run("mixed-run", status="succeeded")
        self._write_checkpoint(run_id, {"pid-1": "文件里的 JD"})
        self._result_rows(run_id, [
            {
                "job_id": "internal-1", "platform_job_id": "pid-2",
                "title": "岗位", "jd": "结果表里的 JD",
                "verdict": "kept", "reason": "", "is_dropped": 0,
            },
        ])
        loaded = load_source_jd_materials(
            self.store,
            scrape_task_id="source-1",
            current_run_id="new-run",
            platform="boss",
            profile_id=None,
            job_ids=["pid-1", "pid-2"],
            jd_path_for=self.path_for,
        )
        self.assertEqual(
            loaded, {"pid-1": "文件里的 JD", "pid-2": "结果表里的 JD"}
        )

    def test_multiple_runs_complement_each_other_newer_first(self):
        older = self._screen_run("older-run", status="succeeded", created_at="2026-10-01T00:00:00")
        newer = self._screen_run("newer-run", status="succeeded", created_at="2026-10-02T00:00:00")
        self._write_checkpoint(older, {"pid-1": "较旧 JD", "pid-3": "只有较旧有"})
        self._write_checkpoint(newer, {"pid-1": "较新 JD", "pid-2": "较新 JD"})
        loaded = load_source_jd_materials(
            self.store,
            scrape_task_id="source-1",
            current_run_id="new-run",
            platform="boss",
            profile_id=None,
            job_ids=["pid-1", "pid-2", "pid-3"],
            jd_path_for=self.path_for,
        )
        self.assertEqual(loaded["pid-1"], "较新 JD")
        self.assertEqual(loaded["pid-3"], "只有较旧有")

    def test_current_run_and_active_runs_are_skipped(self):
        active = self._screen_run("running-run", status="running")
        self._write_checkpoint(active, {"pid-1": "进行中不取"})
        loaded = load_source_jd_materials(
            self.store,
            scrape_task_id="source-1",
            current_run_id="running-run",
            platform="boss",
            profile_id=None,
            job_ids=["pid-1"],
            jd_path_for=self.path_for,
        )
        self.assertEqual(loaded, {})

    def test_other_platform_profile_or_source_is_not_reused(self):
        other_platform = self._screen_run("zh-run", platform="zhilian")
        self._write_checkpoint(other_platform, {"pid-1": "智联 JD"})
        other_profile = self._screen_run("pf-run", profile_id="profile-b")
        self._write_checkpoint(other_profile, {"pid-1": "别的画像 JD"})
        other_source = self._screen_run(
            "os-run", params={"scrape_task_id": "source-2"},
        )
        self._write_checkpoint(other_source, {"pid-1": "别来源 JD"})
        loaded = self._load(["pid-1"])
        self.assertEqual(loaded, {})

    def test_profileless_candidate_is_reused_only_for_profileless_target(self):
        run_id = self._screen_run("no-profile-run", profile_id=None)
        self._write_checkpoint(run_id, {"pid-1": "无画像 JD"})
        with_profile = self._load(["pid-1"], profile_id="profile-a")
        self.assertEqual(with_profile, {})

    def test_job_ids_outside_current_source_set_are_ignored(self):
        run_id = self._screen_run("other-jobs")
        self._write_checkpoint(run_id, {"pid-9": "不在本轮岗位集合"})
        loaded = self._load(["pid-1"])
        self.assertEqual(loaded, {})

    def test_corrupted_checkpoint_is_an_observable_failure(self):
        run_id = self._screen_run("broken-run")
        self._write_checkpoint(run_id, "{not json")
        with self.assertRaises(RuntimeError):
            load_source_jd_materials(
                self.store,
                scrape_task_id="source-1",
                current_run_id="new-run",
                platform="boss",
                profile_id=None,
                job_ids=["pid-1"],
                jd_path_for=self.path_for,
            )

    def test_material_load_does_not_delete_old_files_or_verdicts(self):
        run_id = self._screen_run("keep-run", status="failed")
        path = self._write_checkpoint(run_id, {"pid-1": "保留"})
        self.store.save_screening_verdicts(run_id, {"pid-1": {"verdict": "dropped"}})
        self._load(["pid-1"])
        self.assertTrue(path.exists())
        stored = self.store.load_screening_verdicts(run_id)
        self.assertEqual(list(stored), ["pid-1"])
        self.assertEqual(stored["pid-1"]["verdict"], "dropped")


if __name__ == "__main__":
    unittest.main()
