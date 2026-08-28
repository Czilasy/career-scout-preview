---
description: "Task list for 027 测试大文件拆分重构"
---

# Tasks: 测试大文件拆分重构（027）

**Input**: Design documents from `/specs/027-test-file-split/`

**Prerequisites**: plan.md（批次划分与文件边界）、spec.md、research.md、data-model.md、contracts/compatibility.md、quickstart.md

**Tests**: 本 Spec 不新增行为测试（纯搬运）。安全网 = 基线快照逐条对账 + 后端全量零差异全绿（quickstart 步骤 1-3）。

**Organization**: 按批次（B0-B8）组织，与 021 同构：每批独立交付、独立验证、独立 `refactor` 提交。批次与故事映射：B1-B7 全部服务 US1（尺寸与目录）与 US2（等价证明）；B5/B6 的共享帮手抽离任务专属 US3；B8 兼顾 US4（落位与归档）。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行。本 Spec **刻意不安排跨批并行**——被搬的是安全网本身，并行批会破坏清单对账的差异归因能力；批内搬运步骤也串行执行。
- **[Story]**: US1 尺寸与目录 / US2 等价证明 / US3 共享帮手 / US4 落位归档。基线与收尾阶段不打故事标签。
- 每条任务含明确文件路径。

## File Boundaries

- **Allowed files（删除）**: `tests/test_webui_app.py`、`tests/test_healthy_pipeline.py`、`tests/test_tuning.py`、`tests/test_source.py`、`tests/test_ai.py`、`tests/test_webui_store.py`、`tests/test_chrome_setup.py`（各自批内删除）
- **New files**: `tests/{chrome_setup,webui_store,source,ai,tuning,healthy_pipeline,webui_app}/` 下的 `__init__.py`、域文件（`test_*.py`）、`tests/tuning/builders.py`、`tests/healthy_pipeline/harness.py`
- **Forbidden files**: `webui/**`、`scripts/**`、`webui/src/**`、`.github/**`、`hooks/**`、`pyproject.toml`、`uv.lock`、2000 行以下的既有测试文件、`tests/fixtures/**`、`tests/run_isolated_webui.py`、`tests/sc002_24h_monitor.py`、`tests/sc015_viewport_check.py`、`tests/test_workbench_fixtures.py` 等非拆分范围文件
- **Reference direction**: 拆分文件 → 域内共享帮手模块单向；拆分文件之间不互 import；对 `tests.test_cross_platform_dedupe`、`tests.test_workbench_fixtures` 的既有 import 原样保留
- **Line gate**: 完成后 `tests/` 内无测试文件 >2000 行；带理由豁免 ≤2200 写入该批提交说明

## Verification Gate (每批)

按 quickstart 步骤 1-6：聚焦测试 → 后端全量 → 清单对账（diff 必须为空）→ 行数核对 → 卫生测试 → `git diff --check` / `git status` → 产品代码零改动抽查 → 单个 `refactor(tests)` 提交（不 push）。禁止借机修 bug：发现的既有问题原样搬运并记 BACKLOG。

---

## Phase 0: 基线 [US2]

- [X] T000 工作区清零：`git status` 必须干净——当前 9 个脏文件（`README.md`、`specs/008~014` 的 6 个 `plan.md`、`webui/app.py`）为用户在办工作，处置方式（单独提交或还原）由用户确认后执行，本 Spec 不擅自处置、不卷入拆分提交（已由用户自行提交 8f58e90 / 40fdb57，本 Spec 文档为 ecd3fe9）
- [X] T000b 全量基线：实测确认全量入口命令并跑一次全量，记录结果；**基线必须全绿才开工**，红了先停并查明原因（区分既有失败，记录在案）（`uv run python -m unittest discover -s tests`：Ran 2561，failures=2 均为既有——`test_public_assets` 断言已重写的旧版 CHANGELOG 2.8.5/2.8.4 段落、`test_repo_hygiene` 未跟踪文件；后者随暂存消除，前者记录在案不修）
- [X] T000c 拍快照：按 quickstart 步骤 0 第 3 条命令生成 `$TEMP/cs_tests_inventory_baseline.txt`，记录总数（2026-08-28 实测 1786，以本次重测为准）；快照文件全程只读（重测总数 2561，快照首行即 2561）

---

## Phase 1: B1 chrome_setup（机制验证批）[US1][US2]

**Goal**: `tests/test_chrome_setup.py`(2350) → `tests/chrome_setup/` 2 个域文件；同时首跑实证子目录收集、聚焦命令、清单对账全链路。
**Independent Test**: 清单对账零差异 + 全量全绿 + `discover -s tests/chrome_setup` 可用。

- [X] T001 [US1] 盘点 `tests/test_chrome_setup.py` 类簇结构，按被测域定 2 文件切分线，切分方案写入批次提交说明（11 类实测盘点：`load_module`×8 类、`tempfile_profile`×5 类共用 → 2 域文件 + `harness.py` 共享模块；切分线=Chrome 装配域 1 类 / 抓取契约域 10 类）
- [X] T002 [US1] 创建 `tests/chrome_setup/__init__.py`（空）与 2 个域文件，类与模块级 import/常量按使用归属逐字搬迁（`test_chrome_setup.py` 1304 行、`test_scraper_contracts.py` 895 行、`harness.py` 176 行）
- [X] T003 [US2] 删除 `tests/test_chrome_setup.py`；B1 验证收口（quickstart 步骤 1-6 全序）→ `refactor(tests)` 提交；本批提交说明记录机制实证结论（聚焦命令形态 `discover -s tests/chrome_setup` 128 项 OK；收集数 2561 与基线快照零差异；计划值 1786 系陈旧数字）

**Checkpoint**: 机制全链路验证通过，后续批次照此复制。

---

## Phase 2: B2 webui_store [US1][US2]

- [X] T004 [US1] 盘点 `tests/test_webui_store.py`(2739) 域簇，定 2 文件切分线（迁移合同域 6 类 / 存储业务域 17 类；原文件无模块级共享符号，无需共享模块）
- [X] T005 [US1] 创建 `tests/webui_store/__init__.py` + 2 个域文件，逐字搬迁（`test_store_migrations.py` 947 行、`test_store_domains.py` 1799 行；`MigrationBootstrapBackupTests` 的 `__file__` 锚点 `parent.parent`→`parent.parent.parent` 深度修正）
- [X] T006 [US2] 删除 `tests/test_webui_store.py`；B2 验证收口 → `refactor(tests)` 提交（聚焦 `discover -s tests/webui_store` Ran 139 OK(skipped=1)；全量 Ran 2561 与基线构成一致；清单对账零差异）

---

## Phase 3: B3 source [US1][US2]

- [ ] T007 [US1] 盘点 `tests/test_source.py`(2925) 域簇，定 2 文件切分线
- [ ] T008 [US1] 创建 `tests/source/__init__.py` + 2 个域文件，逐字搬迁
- [ ] T009 [US2] 删除 `tests/test_source.py`；B3 验证收口 → `refactor(tests)` 提交

---

## Phase 4: B4 ai [US1][US2]

- [ ] T010 [US1] 盘点 `tests/test_ai.py`(2880) 域簇，定 2 文件切分线；标记 `tests.test_workbench_fixtures` import 的使用类集合
- [ ] T011 [US1] 创建 `tests/ai/__init__.py` + 2 个域文件，逐字搬迁；`from tests.test_workbench_fixtures import ...` 随使用类搬入，导入路径不变
- [ ] T012 [US2] 删除 `tests/test_ai.py`；B4 验证收口 → `refactor(tests)` 提交

---

## Phase 5: B5 tuning [US1][US2][US3]

- [ ] T013 [US3] 创建 `tests/tuning/builders.py`：逐字抽离 `_sample_nine_fields`、`_expected_path_digest`、`_make_valid_manifest_payload`、`_make_valid_report_payload`、`_CleanContextFakeExecutor` 及其依赖的模块级常量（以搬迁时实测依赖为准）；抽离前先 grep 复核共用位置清单
- [ ] T014 [US1] 盘点 `tests/test_tuning.py`(5103) 域簇，定 3 文件切分线（草案：实验配置与租约 / manifest 校验 / runner 与漏斗守卫；以实测行数平衡为准，各 ≤2000）
- [ ] T015 [US1] 创建 `tests/tuning/__init__.py` + 3 个域文件，逐字搬迁；原文件内对 5 个共享符号的引用统一改为 `from tests.tuning.builders import ...`（引用处逐字等价，仅改取用路径）
- [ ] T016 [US2] 删除 `tests/test_tuning.py`；B5 验证收口，附加核对：拆分文件之间零互相 import、共享符号仅经 `builders` 取用 → `refactor(tests)` 提交

---

## Phase 6: B6 healthy_pipeline [US1][US2][US3]

- [ ] T017 [US3] 创建 `tests/healthy_pipeline/harness.py`：逐字抽离 `_load_boss_cdp_raw`、`_load_sc015_viewport_check`、`_make_app`、`_authed_test_client`、`_wait_for_pipeline_task`、`_pause_run` 及其依赖的模块级常量（如 `_SCRIPT_PATH`、`_SC015_PATH`，以实测为准）
- [ ] T018 [US1] 盘点 `tests/test_healthy_pipeline.py`(6174) 域簇，定约 5 文件切分线（草案：切片状态与异常分类 / 收敛恢复 / 收敛统一与事件 / 暂停续跑 / 语义杂项；约 1562 行的 ConvergencePendingPersistence 类簇整体单置不强切）
- [ ] T019 [US1] 创建 `tests/healthy_pipeline/__init__.py` + 域文件，逐字搬迁；harness 符号引用统一改为 `from tests.healthy_pipeline.harness import ...`
- [ ] T020 [US2] 删除 `tests/test_healthy_pipeline.py`；B6 验证收口（附加核对同 T016）→ `refactor(tests)` 提交

---

## Phase 7: B7 webui_app [US1][US2]

- [ ] T021 [US1] 盘点 `tests/test_webui_app.py`(8913、44 类) 域簇，定约 6 文件切分线（草案：核心路由 / 流程续跑 / 账号与高级设置 / 调优路由 / 平台感知 / 集成契约与续跑去重；以实测行数平衡为准）；`_tuning_quality_context`、`_make_valid_manifest_payload_web` 随 TuningManifestRouteTests 整组搬（实测仅该类使用，不进共享模块）；中段 `from tests.test_cross_platform_dedupe import ...` 与 `ResumeDedupSingleSideTests`、`ResumeVerdictCoverageChainTests` 两个继承类连 import 同迁一个文件
- [ ] T022 [US1] 创建 `tests/webui_app/__init__.py` + 域文件，逐字搬迁
- [ ] T023 [US2] 删除 `tests/test_webui_app.py`；B7 验证收口 → `refactor(tests)` 提交

---

## Phase 8: B8 终检与归档 [US1][US2][US4]

- [ ] T024 [US1] 全仓测试文件行数终检：`wc -l tests/**/*.py tests/*.py`，无 >2000（带理由豁免 ≤2200 已在各批提交说明），终检清单写入批次记录
- [ ] T025 [US2] 清单终对账 + 全量终跑：与基线快照逐行零差异、总数一致、全绿
- [ ] T026 [US2] 产品代码零改动复核：`git diff <基线提交>..HEAD -- webui/ scripts/ webui/src/ .github/ hooks/ pyproject.toml uv.lock` 为空
- [ ] T027 [US4] 落位复核与文档同步：7 个子目录命名与域文件归属核对；`roadmap/BACKLOG.md` 的 B075 移入已完成归档，订正其中旧数字 2525 为基线实测值；`specs/021-large-file-split/BACKLOG.md` 不动（属产品代码，另行立项）
- [ ] T028 卫生收口：`uv run python -m unittest tests.test_repo_hygiene` + `git diff --check` + `git status`；如需收尾提交按仓库惯例；不 push、不提升版本、不触发打包发布

---

## Dependencies & Execution Order

- **T000 → T000b → T000c** 严格串行；基线不绿不开工。
- **B1 必须先于 B2-B7**：机制验证批，子目录收集与对账链路首跑。
- **B2 → B3 → B4 → B5 → B6 → B7** 建议顺序（由小到大积累搬运经验，沿用 021）；批间理论独立，但**刻意串行**——并行批会破坏清单对账的差异归因。
- **B8** 依赖全部批次完成。
- **无并行任务**：安全网自身被搬运，任何并行都会使「哪批引入差异」不可判定；此为有意设计而非遗漏。

## Implementation Strategy

- 每批一个独立增量：搬完 → 六步门禁 → 提交。任何批次边界仓库都处于「清单零差异 + 全量绿」的可交付状态，可随时暂停/回滚。
- 单批内若发现切分线不合理（某文件超 2200 或域簇割裂），允许在该批提交前调整切分线，门禁逐次重跑。
- 发现问题不修：既有测试缺陷原样搬运，记 `roadmap/BACKLOG.md`。
- 临时文件纪律：快照与对账产物只落系统临时目录，批次收尾清理；项目根目录不得出现中转文件。

## Notes

- 纯搬运纪律高于一切便利：断言、装饰器、测试数据逐字不动；唯一允许的「改动」是共享符号取用路径（`from tests.<域>.<帮手> import`），且引用处语义等价。
- 快照文件（`$TEMP/cs_tests_inventory_baseline.txt`）是唯一基准，任何批次不得修改；若误删，只能从基线提交重新拍取并记录原因。
- 版本语义：全程不提升版本号（纯测试重构，产品行为零变化）。
