# Feature Specification: 测试大文件拆分重构（027）

**Feature Branch**: `027-test-file-split`

**Created**: 2026-08-28

**Status**: Draft

**Input**: User description: "拆解这些巨大的测试文件，并且使用文件夹等目录好好放好，看起来规整一点，但是逻辑不准有任何的改变。做完之后必须能证明和以前完全相同。" —— 对应 BACKLOG B075：7 个超 2000 行的测试文件纯搬运拆分到单文件 ≤2000 行（允许 ≤10% 超出），按原巨型文件建子目录归置；逻辑零变化；等价性靠「类名.方法名清单逐条对账 + 全量全绿」双验收。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 全部超标测试文件回到尺寸线内并规整入目录 (Priority: P1)

7 个超标测试文件（2026-08-28 实测行数：`tests/test_webui_app.py` 8913、`tests/test_healthy_pipeline.py` 6174、`tests/test_tuning.py` 5103、`tests/test_source.py` 2925、`tests/test_ai.py` 2880、`tests/test_webui_store.py` 2739、`tests/test_chrome_setup.py` 2350）全部拆到单文件 ≤2000 行以内，并按原巨型文件归入子目录（如 `tests/webui_app/`、`tests/healthy_pipeline/` 等），测试目录结构一眼规整。允许个别文件因域簇内聚不硬切而超出 ≤10%（≤2200 行），但必须记录超标理由。

**Why this priority**: 7 个文件合计约 31,084 行，占全部测试代码（约 51,207 行）的六成以上；这是立项的唯一尺寸目标，不拆到线内等于没做。

**Independent Test**: 每批拆分完成后用行数统计确认该批新文件全部 ≤2000（或带理由 ≤2200），原巨型文件删除后测试收集数量与拆分前完全一致。

**Acceptance Scenarios**:

1. **Given** 拆分全部批次完成，**When** 统计 `tests/` 下全部 `.py` 测试文件行数，**Then** 无任何测试文件超过 2000 行（带理由的 ≤2200 除外），原 7 个巨型文件不再存在。
2. **Given** 拆分全部完成，**When** 查看 `tests/` 目录，**Then** 每个原巨型文件对应一个子目录，域内测试文件按被测域命名，2000 行以下的既有测试文件保持原位不动。
3. **Given** 任意一批拆分完成，**When** 用既有的测试收集命令收集测试，**Then** 收集到的用例总数与拆分前一致，收集命令本身不需要修改。

---

### User Story 2 - 每批拆分零行为变化且可证明等价 (Priority: P1)

每个拆分批次都是纯搬运：不改任何测试逻辑、不改断言、不改 fixture 数据、不改 patch 目标、不改任何产品代码。每批交付前用双重验收证明等价：

- **清单对账**：开工前拍基线快照——全部用例逐条倒出为「类名.方法名」清单（不含模块路径，因为搬运后路径必然变化）加总数；每批之后重新收集、逐条 diff，必须一条不多、一条不少、一条不改名。
- **全量全绿**：每批之后跑完整后端测试，与基线一样全绿。

**Why this priority**: 用户明确"逻辑不准有任何的改变"，且测试文件本身就是安全网——搬的就是裁判，必须用清单对账防"搬丢"（丢掉的测试不会红，只会静默消失）、用全量全绿防"搬坏"（import 断链、共享帮手没跟过去、执行顺序依赖都会当场红）。两条合起来才是完整证明。

**Independent Test**: 每批一个 `refactor` 提交，提交前清单对账零差异 + 后端全量全绿 + 卫生测试通过。

**Acceptance Scenarios**:

1. **Given** 某批次拆分完成，**When** 重新收集用例并与基线清单逐条对账，**Then** 「类名.方法名」集合完全一致、总数不变。
2. **Given** 某批次拆分完成，**When** 运行后端全量测试，**Then** 全部通过，与基线结果一致，无新增失败、无新增跳过。
3. **Given** 全部批次完成，**When** 检查全程 `git diff`，**Then** 产品代码（`webui/` 业务模块、`scripts/` 业务脚本、前端）零改动，改动仅发生在 `tests/` 内。

---

### User Story 3 - 共享测试帮手先抽离、归置有明确落位 (Priority: P2)

多个测试类共用的模块级帮手在拆文件之前先抽到共享模块（纯搬运）：`tests/test_healthy_pipeline.py` 的 6 个模块级函数（`_load_boss_cdp_raw`、`_load_sc015_viewport_check`、`_make_app`、`_authed_test_client`、`_wait_for_pipeline_task`、`_pause_run`，实测各有 2~50 处调用）、`tests/test_tuning.py` 的 5 个构造器与测试替身（`_sample_nine_fields`、`_expected_path_digest`、`_make_valid_manifest_payload`、`_make_valid_report_payload`、`_CleanContextFakeExecutor`，实测各有 3~54 处调用）。抽离判据为**实际共用横跨多个拆分文件**；仅供单一类使用的模块级函数（如 `tests/test_webui_app.py` 的 `_tuning_quality_context`、`_make_valid_manifest_payload_web`，实测仅 TuningManifestRouteTests 一类使用）不抽离，随使用它的类整组搬迁。抽离后各拆分文件不得为共用符号互相 import 兄弟文件的私有定义。

**Why this priority**: 这是唯一需要设计判断的部分（相当于 021 的 PipelineContext 批）；不先抽离，拆出的文件会互相牵扯，纯搬运无法收口。

**Independent Test**: 抽离批完成后清单对账零差异 + 后端全量全绿；共享符号原位语义不变，全部既有用法不改。

**Acceptance Scenarios**:

1. **Given** 共享帮手抽离批完成，**When** 运行后端全量测试并对账清单，**Then** 全绿且清单零差异。
2. **Given** 后续拆分批完成，**When** 检查拆分文件之间的 import，**Then** 不存在"从同原文件的另一个拆分文件 import 私有符号"的依赖，共用符号一律来自共享模块。

---

### User Story 4 - 拆分后新增测试落位清晰 (Priority: P3)

拆分完成后，任一被测域新增测试时直接落入对应子目录的域文件，不再向巨型文件追加；子目录与域文件的命名让"某个 API/流程的测试在哪"一眼可查。

**Why this priority**: 复用性与可维护性是拆分的长期收益，依赖前三 story 完成。

**Independent Test**: 拆分完成后查看目录结构：每个子目录内文件按被测域命名，单文件行数距 2000 行红线有充足余量。

**Acceptance Scenarios**:

1. **Given** 拆分完成，**When** 需要为某个既有域补一条测试，**Then** 能直接找到对应子目录下的域文件写入，不需要新建巨型文件或触碰无关文件。

### Edge Cases

- **跨文件继承的测试类**：`test_webui_app.py` 末尾的 `ResumeDedupSingleSideTests`、`ResumeVerdictCoverageChainTests` 继承自 `tests.test_cross_platform_dedupe` 的 `CrossPlatformDedupeIntegrationTests`（原文件中段 import）。拆分时连类带 import 整组搬进同一个新文件，继承链不断。
- **跨文件 fixture import**：`test_ai.py` 从 `tests.test_workbench_fixtures` import 样例数据（该文件不在拆分范围、保持原位）；拆分 `test_ai.py` 时这些 import 随使用类原样搬入新文件，导入路径不变。
- **执行顺序变化**：测试按收集顺序执行，拆分后文件与用例的执行顺序必然变化；若存在隐性顺序依赖会在全量中暴露——基线全绿 + 每批全绿即为护栏；基线阶段如发现既有偶发失败先记录，区分"拆坏"与"本来就红"。
- **工作区不干净**：当前存在未提交改动（9 个脏文件）。开工前必须先按仓库惯例处置干净，禁止卷入拆分提交。
- **拆分中发现测试代码本身有 bug**：不借机修复，原样搬运并记入 BACKLOG（沿用 021 FR-007 纪律）。
- **中间批次状态**：每批独立提交，仓库在任何批次边界都处于清单对账零差异 + 全量绿的可交付状态，可随时暂停/回滚；出问题可按批次提交定位。
- **清单对账的范围**：对账项为「类名.方法名」，不含模块路径；模块路径变化是拆分的预期结果而非差异。
- **不触碰的对象**：2000 行以下的既有测试文件、`tests/fixtures/` 数据目录、`tests/run_isolated_webui.py` 等非测试脚本保持原位不动。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统（本次为测试代码库）MUST 将 7 个超标测试文件拆分至单文件 ≤2000 行以内，验证方式为行数统计；个别文件因域簇内聚不允许硬切时允许 ≤10% 超出（≤2200 行），但 MUST 在该批提交说明中记录超标理由。
- **FR-002**: 拆分 MUST 保持行为零变化：不改测试逻辑、不改断言、不改测试数据与 fixture、不改 patch 目标字符串、不改产品代码；每个用例的代码体逐字搬运。
- **FR-003**: 拆分 MUST 按原巨型文件建立子目录归置拆分产物，子目录命名与域文件命名 MUST 体现代理域划分；2000 行以下的既有测试文件与数据目录 MUST 保持原位。
- **FR-004**: 开工前 MUST 拍取基线快照：测试收集总数与全部用例的「类名.方法名」清单；清单 MUST 为含重复条目的完整列表（非去重集合），以同时捕获漏搬与重复收集；每个批次交付前与全部完成后 MUST 重新收集并逐条对账，清单完全一致且总数不变才允许提交。收集总数以开工时基线实测为准（2026-08-28 实测收集 1786 个用例；BACKLOG 记载的 2525 为过时数字，不采用）。
- **FR-005**: 每个批次交付前 MUST 通过：本批聚焦测试、后端全量测试、仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene`）；全绿且清单对账零差异后以单个 `refactor` Conventional Commit 收尾，不自动 push。本次为纯测试代码搬运、不触碰产品代码与前端，经用户 2026-08-28 拍板豁免前端测试与 `npm run build` 门禁。
- **FR-006**: 拆分过程中发现的既有测试缺陷 MUST 不借机修复，原样搬运并记入 BACKLOG。
- **FR-007**: 共享测试帮手 MUST 先于域文件拆分抽离到共享模块（纯搬运）；抽离判据为实际共用横跨多个拆分文件，仅供单一类使用者随使用类整组搬迁，不做无必要的集中；抽离清单以搬运时实际共用情况复核为准；抽离后 MUST NOT 出现拆分文件之间互相 import 同原文件私有符号的依赖。
- **FR-008**: 原巨型文件拆分完成后 MUST 直接删除，不保留兼容门面；前提为反向依赖实测为零（2026-08-28 已核验：无任何文件从这 7 个文件 import 符号；`patch("tests.*")` 零命中）。
- **FR-009**: 测试收集命令（CI 的 `uv run python -m unittest discover -s tests`）MUST 保持不变且收集完整；子目录 MUST 兼容该收集机制（`tests/` 为无 `__init__.py` 的命名空间包，基线批以清单对账实证子目录可被递归收集）。
- **FR-010**: 每个拆分批次 MUST 是可独立验证、可独立回滚的交付单元；任何批次边界的仓库状态都满足清单零差异 + 全量绿。

### Key Entities

- **基线快照（baseline snapshot）**: 开工前一次性拍取的等价性参照物——用例总数与全部用例的「类名.方法名」清单；是每批对账的唯一基准。
- **拆分批次（batch）**: 一次独立验证与提交的交付单元，对应一个原巨型文件（或共享帮手抽离），一个 `refactor` 提交。
- **共享测试帮手模块**: 多文件共用符号的唯一落位，纯搬运产物，语义与原位一致。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 全部批次完成后，`tests/` 下无任何测试文件超过 2000 行（带理由的 ≤2200 除外），原 7 个巨型文件全部消失，行数统计可验证。
- **SC-002**: 每个批次边界与最终完成时，用例清单与基线完全一致（逐条零差异），用例总数与基线实测值（2026-08-28 实测 1786）一个不多、一个不少、一个不改名；后端全量测试全绿。
- **SC-003**: 全程 `git diff` 证明产品代码（`webui/`、`scripts/`、前端）零改动；代码改动仅发生在 `tests/` 目录内（`specs/` 文档与 BACKLOG 的状态更新除外）。
- **SC-004**: 测试收集命令不变、收集数量不变；拆分后向任一域新增测试可直接落入对应子目录域文件。

## Verification Scope *(mandatory)*

- 拆分批次交付（本 Spec 主体）：验证范围为本批聚焦测试、后端全量测试、仓库卫生检查与用例清单对账；经用户拍板，纯测试搬运豁免前端测试与 `npm run build`。
- 收口发布任务：默认只做卫生测试、hooks、`git diff --check`、`git status` 和 `scripts/release_check.ps1`（若存在）；不自动要求全量测试。
- 只有用户明确要求“全量/全部测试/完整验证”时，收口任务才执行全量测试。

## Assumptions

- 基线全绿才开工：基线快照拍取时后端全量必须全绿；若基线即红，先停止并查明原因，区分既有失败与后续拆分引入的失败。
- 开工前工作区清零：当前 9 个未提交脏文件先按仓库惯例单独处置，禁止卷入拆分提交（沿用 021 T000 纪律）。
- 版本语义：本 Spec 为纯测试重构、产品行为零变化，默认全程不提升版本号；如收口时有异议再统一评估。
- 021 遗留的 3 个超标产品文件（`webui/historical_recovery.py`、`scripts/zhilian_cdp_raw.py`、`webui/task_runners.py`）属产品代码，不在本 Spec 范围，另行立项。
- `tests/` 为无 `__init__.py` 的命名空间包，`unittest discover -s tests` 递归子目录的收集行为以基线批的清单对账实证为准。
- 用例清单以「类名.方法名」为对账粒度；方法体内代码的逐字性由每批 `git diff` 纯搬运审查保证。
