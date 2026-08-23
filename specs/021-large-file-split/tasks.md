---
description: "Task list for 021 大文件拆分重构"
---

# Tasks: 大文件拆分重构（021）

**Input**: Design documents from `/specs/021-large-file-split/`

**Prerequisites**: plan.md（批次划分与文件边界）、spec.md、research.md、data-model.md、contracts/compatibility.md

**Tests**: 本 Spec 不新增行为测试（纯搬运），安全网为既有全量测试零改动通过。

**Organization**: 按批次（B1-B8）组织，每批独立交付、独立验证、独立提交。批次与 Spec 用户故事的映射：B1-B8 全部服务 US1（尺寸红线）与 US2（零行为变化）；B3-B6 专属 US3（app.py 规范化）；B1/B2/B7/B8 兼顾 US4（复用性）。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1/US2/US3/US4
- Include exact file paths in descriptions

## File Boundaries

- **Allowed files**: `webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`webui/tuning.py`、`webui/ai.py`、`webui/store_migrations.py`、`webui/pipeline_exec.py`、`webui/platforms.py`、`webui/src/views/DiscoveryView.vue`、`.specify/memory/constitution.md`（仅模块地图小节）
- **Forbidden files**: `tests/**`、`pyproject.toml` 版本字段、任何 DB schema/迁移逻辑、`webui/src/views/` 其他视图、DiscoveryView 模板段
- **New files**: `webui/pipeline_context.py`、`webui/runners/**`、`webui/source_*.py`、`webui/store_*.py`、`webui/tuning_*.py`、`webui/ai_*.py`、`webui/pipeline_exec_*.py`、`webui/platforms_*.py`、`webui/store_migrations_*.py`、`scripts/boss/**`、`webui/src/composables/useDiscovery*.ts`
- **Reference direction**: `app.py → runners/* → pipeline_context → store*`；runners 互不 import；门面仅 re-export/组装；composables 不 import view
- **Line gate**: 全部完成后所有 Python ≤800 / Vue ≤1200

## Verification Gate (task-type aware)

- 每批次（拆分交付）门禁：本批聚焦测试 → 后端全量 → 前端测试与 `npm run build`（涉前端时）→ `uv run python -m unittest tests.test_repo_hygiene` → `git diff --check` / `git status` → 单个 `refactor` 提交（不 push）。
- 禁止借机修 bug：发现的既有问题原样搬运并记 BACKLOG。

---

## Phase 0: 基线 [US2]

- [ ] T000 [US2] 工作区清零：`git status` 必须干净后才开工——当前存在 `webui/dist` 构建残留与 `specs/021-large-file-split/` 未跟踪：先按仓库惯例单独提交 021 spec 文档（`chore(speckit): 021 大文件拆分重构立项文档`），dist 残留按 AGENTS「意外文件优先处理」处置（确认来源后单独提交或还原，禁止卷入后续 refactor 提交）
- [ ] T000b [US2] 拆分前基线：实测确认全量后端测试入口命令并跑一次全量（记录基线结果与命令）；记录 11 个文件实测行数清单（2026-08-23 首测：9402/4935/4165/2765/2619/2247/2198/1766/1221/3789）作为对照。基线不绿先停，查明原因（基线必须全绿才开工）

---

## Phase 1: B1 快赢·source 拆分 [US1][US2][US4]

**Goal**: `webui/source.py`(2720) 拆为平台子模块 + 门面，调用方零改动。
**Independent Test**: `tests/test_source.py`(2518 行) 零改动全绿；`from webui.source import ...` 全符号可用。

- [ ] T001 [US1] 拆 `BossCdpSource` 及其私有助手到 `webui/source_boss_cdp.py`（若超 800 行拆 `source_boss_cdp_detail.py`）
- [ ] T002 [P] [US1] 拆 `ZhilianCdpSource` 到 `webui/source_zhilian_cdp.py`；`FakeJobSource` 到 `webui/source_fake.py`；`SourceCircuitBreaker` 到 `webui/source_breaker.py`
- [ ] T003 [US2] `webui/source.py` 改为门面：re-export 全部原公开符号（`_BossCdpSource` 等私有符号一并保留）
- [ ] T004 [US1] B1 验证收口：聚焦 test_source → 后端全量 → 行数核对 → 卫生测试 → `refactor(source)` 提交；宪法模块地图登记新文件

**Checkpoint**: source 域完成，`source.py` ≤200 行门面。

---

## Phase 2: B2 快赢·store 域 mixin [US1][US2][US4]

**Goal**: `webui/store.py`(4874) 按域抽 5-8 个 mixin，`store.py` 收敛为组装。
**Independent Test**: `tests/test_webui_store.py`(2596 行) 零改动全绿；TaskStore 全方法可用。

- [ ] T005 [US1] 盘点 store.py 174 个方法的域簇清单（高级配置/模式版本、恢复锁、pipeline 结果、jobs upsert、source attempts、断点身份、legacy tasks、profiles），写入批次提交说明
- [ ] T006 [US1] 抽 store 域 mixin：`webui/store_config.py`、`store_recovery.py`、`store_pipeline_results.py`、`store_jobs.py`、`store_misc.py`（按盘点结果可增减，各 ≤800）
- [ ] T007 [US2] `webui/store.py` 收敛为 TaskStore 组装 + re-export；确认 mixin 引用方向单向（不 import app）
- [ ] T008 [US1] B2 验证收口：聚焦 test_webui_store → 后端全量 → 行数核对 → 卫生测试 → `refactor(store)` 提交；模块地图登记

**Checkpoint**: store 主文件 ≤800。

---

## Phase 3: B3 app 设计批·PipelineContext [US3][US2]

**Goal**: 共享运行态收进显式上下文对象，闭包改持引用，行为不变。本批**不外迁任何 runner**。
**Independent Test**: 后端全量绿；monkeypatch 面（`boss`/`_BossCdpSource`/`ai_service`/`ScraperExecutor`/6 个模块级助手）原位可用。

- [ ] T009 [US3] 新建 `webui/pipeline_context.py`：定义 PipelineContext（tasks/lock/store/emit/write_run 及实际捕获清单补全——以四个 runner 的完整闭包捕获清单为验收物，不以上述字段为限），`webui/app.py` 的 create_app 内组装一次
- [ ] T010 [US3] app.py 内四个 runner 与嵌套助手的共享状态访问改为经 ctx（纯改写引用，逐段对照搬运，不改逻辑）；**硬约束：可被 monkeypatch 的符号以全仓 grep `patch("webui.app.` 实测清单为准（含 `boss`、`_BossCdpSource`、`ai_service`、`ScraperExecutor`、`threading`、`uuid`、`os`、`_theme_path`，B3 时复核），ctx 持有或提供经 `webui.app` 模块属性调用时取用的访问方式，保证 `patch("webui.app.xxx")` 仍打在真实执行路径上**
- [ ] T011 [US2] B3 验证收口：后端全量（重点 test_webui_app.py 8361 行）→ 卫生测试 → `refactor(app)` 提交；模块地图登记 pipeline_context

**Checkpoint**: 闭包耦合解为显式对象，为 B4-B6 纯搬运铺路。

---

## Phase 4: B4 app·runner 外迁 I（tuning_manifest + recrawl）[US3][US2]

- [ ] T012 [US3] 外迁 `_run_tuning_manifest_child` 到 `webui/runners/tuning_manifest.py`（超 800 行则按段拆子模块）；外迁代码禁止 `from webui.app import` 可 patch 符号，统一经 ctx 取用
- [ ] T013 [P] [US3] 外迁 `_run_recrawl_task` 到 `webui/runners/recrawl_task.py`（同上约束）
- [ ] T014 [US3] 新建 `webui/runners/__init__.py`；app.py 保留触发路由与 re-export
- [ ] T015 [US2] B4 验证收口：**专项验证 monkeypatch 面——跑 test_webui_app.py / test_healthy_pipeline.py 中所有 `patch("webui.app.*)` 用例（`_BossCdpSource`、`boss`、`threading`、`ai_service`、`ScraperExecutor`、`uuid`、`os`、`_theme_path`），确认补丁打在外迁后的真实执行路径上（必要时用断点/打标确认被 patch 的对象确实被执行）** → 后端全量 → 卫生测试 → `refactor(app)` 提交；模块地图登记

---

## Phase 5: B5 app·runner 外迁 II（pipeline_task）[US3][US2]

- [ ] T016 [US3] 外迁 `_run_pipeline_task` 到 `webui/runners/pipeline_task.py`（2274 行 → 按段拆 2-3 个子模块，各 ≤800）
- [ ] T017 [US2] B5 验证收口：后端全量（重点 test_healthy_pipeline.py 6089 行）→ 卫生测试 → `refactor(app)` 提交；模块地图登记

---

## Phase 6: B6 app·runner 外迁 III（ai_screen + 薄路由收敛）[US3][US2]

- [ ] T018 [US3] 外迁 `_run_ai_screen_task`（4072 行）到 `webui/runners/ai_screen_task.py` + 2-3 个分段子模块（判定/终态/续跑；task 单向 import 段模块）
- [ ] T019 [US3] 剩余嵌套路由助手归入 `register_*` 模块（沿 job_feedback_api 先例），`webui/app.py` 收敛为入口 + 上下文组装 + 路由注册 + re-export，≤800
- [ ] T020 [US2] B6 验证收口：后端全量 → 冒烟（启动 + 小规模筛选流程）→ 卫生测试 → `refactor(app)` 提交；模块地图登记

**Checkpoint**: app.py 红线达标（US3 完成）。

---

## Phase 7: B7 搬运批·剩余 Python 文件 [US1][US2]

- [ ] T021 [P] [US1] `webui/tuning.py`(2619) 拆 `tuning_*.py` 域子模块 + 门面
- [ ] T022 [P] [US1] `webui/ai.py`(2247) 拆 `ai_*.py` 域子模块 + 门面
- [ ] T023 [P] [US1] `webui/pipeline_exec.py`(1766) 拆 `pipeline_exec_*.py` + 门面；`webui/platforms.py`(1221) 拆 `platforms_*.py` + 门面
- [ ] T024 [P] [US1] `webui/store_migrations.py`(2198) 历史迁移按版本段物理归组到 `store_migrations_v*.py`（**仅物理搬运，禁止合并/重写/修改任何迁移语义**）+ 门面
- [ ] T025 [US2] B7 验证收口：后端全量 → 行数核对 → 卫生测试 → `refactor` 提交；模块地图登记

---

## Phase 8: B8 boss 脚本 + 前端 composables [US1][US2][US4]

- [ ] T026 [US1] `scripts/boss_cdp_raw.py`(4165) 按宪法分组归位到 `scripts/boss/`（cdp_session/exceptions/detail_extract/city_map/cli 等）；原文件留薄门面，`python scripts/boss_cdp_raw.py` CLI 行为不变
- [ ] T026a [US2] 同步 `scripts/bump_version.py` 的版本同步目标：从 `scripts/boss_cdp_raw.py` 指向 `scripts/boss/` 新位置（同步行为本身不变，版本号与既有 bump 语义不变）
- [ ] T027 [US1] `webui/src/views/DiscoveryView.vue` 抽 `<script setup>` 逻辑为 4-6 个 `webui/src/composables/useDiscovery*.ts`，模板段零改动，整文件 <1200（主脚本预期 ~600 行为预期值非硬门禁，按职责拆、不为凑行数拆）
- [ ] T028 [US2] B8 验证收口：后端全量 + 前端 452 用例 + `npm run build` + 冒烟 → 卫生测试 → `refactor` 提交；模块地图登记

---

## Phase 9: 收尾 Polish [US1][US4]

- [ ] T029 [US1] 全仓行数终检：无 Python >800 / Vue >1200；输出终检清单
- [ ] T030 [US4] 核对宪法「模块地图」小节完整覆盖全部新模块（路径 + 一句话职责）
- [ ] T031 [US2] 核对 `git diff` 全程 tests/ 零改动；BACKLOG 登记搬运中发现的既有 bug（如有）
- [ ] T032 运行 quickstart.md 全流程验证；卫生测试后收尾提交（如需）

---

## Dependencies & Execution Order

- **B1 → B2**：相互独立，可先后或并行（不同文件）。
- **B3 必须先于 B4/B5/B6**（上下文对象是外迁前提）；B4 → B5 → B6 建议顺序（由小到大积累搬运经验），B4/B5 理论可并行但不建议。
- **B7、B8 与 B3-B6 独立**，可穿插执行；B8 前端部分与任何后端批并行安全。
- **Phase 9 收尾**依赖全部批次完成。

### Parallel Opportunities

- T001/T002（B1 内）、T012/T013（B4 内）、T021-T024（B7 全部）可并行。
- B8 前端（T027）可与 B7 并行。

## Implementation Strategy

- 每批一个独立增量的 MVP 心态：B1 即第一个可交付验证批次。
- 每批结束立即提交，仓库任何批次边界都是全测试绿状态，可随时暂停/回滚。
- 单批内部若发现批次过大（如 B6），允许在 Plan 边界内拆成多个连续 refactor 提交，但验证门禁逐提交执行。

## Notes

- 纯搬运纪律：发现 bug 不修，记 BACKLOG；发现"顺手可以优化"的一律不动。
- 门面 re-export 永久保留（本 Spec 不做清理，清理另行立项）。
