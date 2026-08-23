# Feature Specification: 大文件拆分重构（021）

**Feature Branch**: `021-large-file-split`

**Created**: 2026-08-23

**Status**: Draft

**Input**: User description: "文件拆分但是逻辑不变，规范化一点，复用性高一点" —— 对全仓超标文件做纯拆分重构，行为不变，只改善结构、规范性与复用性。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 全部超标文件回到宪法尺寸线内 (Priority: P1)

11 个超标文件（2026-08-23 实测行数：`webui/app.py` 9402、`webui/store.py` 4935、`scripts/boss_cdp_raw.py` 4165、`webui/source.py` 2765、`webui/tuning.py` 2619、`webui/ai.py` 2247、`webui/store_migrations.py` 2198、`webui/pipeline_exec.py` 1766、`webui/platforms.py` 1221、`webui/src/views/DiscoveryView.vue` 3789）全部拆到 Python ≤800 行 / Vue ≤1200 行以内，且对外行为与接口完全不变。

**Why this priority**: 宪法红线是本项目的硬约束，也是本次立项的唯一目标；不拆到线内等于没做。

**Independent Test**: 每批拆分完成后用行数统计确认所有涉及文件低于红线，用门面 re-export 确认旧 import 路径全部可用。

**Acceptance Scenarios**:

1. **Given** 拆分全部批次完成，**When** 统计全仓 Python/Vue 文件行数，**Then** 无任何业务文件超过 800/1200 行红线。
2. **Given** 任意现有调用方（含全部测试文件），**When** 以拆分前的旧路径 import 任意公开符号，**Then** import 成功且行为与拆分前一致。

---

### User Story 2 - 每批拆分零行为变化 (Priority: P1)

每个拆分批次都是纯搬运/结构调整：不改任何业务逻辑、不改对外接口签名、不改数据库结构与数据格式。每批完成后全量后端测试与前端 452 用例全绿。

**Why this priority**: 用户明确要求"逻辑不变"；这是拆分重构的成败判据，与尺寸目标并列。

**Independent Test**: 每批一个 refactor 提交，提交前跑相关模块聚焦测试 + 后端全量测试 + 前端测试与构建，全绿才提交。

**Acceptance Scenarios**:

1. **Given** 某批次拆分完成，**When** 运行后端全量测试与前端全部用例，**Then** 全部通过，无跳过的既有用例。
2. **Given** 拆分完成后的应用，**When** 用户按原有方式启动和使用（启动流程、抓取、AI 筛选、调优、历史查看），**Then** 一切表现与拆分前无差异。

---

### User Story 3 - app.py 结构规范化 (Priority: P2)

`webui/app.py` 的巨型工厂函数按「运行时上下文对象 + runner 逐批外迁 + 薄路由归位」拆解：共享运行态（任务表、锁、store、事件推送等）收进显式上下文对象，四个 runner 闭包（tuning manifest / pipeline / recrawl / ai_screen）外迁为独立模块，`app.py` 收敛为入口与路由注册。

**Why this priority**: app.py 是唯一需要设计判断的硬骨头（单闭包 4072 行、共享状态引用密度极高），拆开后其余文件的维护与复用成本显著下降。

**Independent Test**: 每迁移一个 runner，现有涉及该 runner 的测试路径（调优、流水线、续跑、AI 筛选）全部通过；monkeypatch 面（`boss`、`_BossCdpSource`、`ai_service`、`ScraperExecutor` 及 6 个模块级助手）保持可用。

**Acceptance Scenarios**:

1. **Given** 上下文对象批完成，**When** 四个 runner 与全部嵌套助手引用共享状态，**Then** 均通过上下文对象访问，行为与闭包捕获时一致。
2. **Given** 四个 runner 全部外迁，**When** 查看 `app.py`，**Then** 只剩应用入口、上下文组装与路由注册，行数低于 800。

---

### User Story 4 - 复用性提升 (Priority: P3)

拆分产出的模块边界清晰、职责单一：source 按平台子模块组织、store 按业务域 mixin 组织、boss 抓取脚本按宪法 `scripts/boss/` 分组（CDP/解析/存储/执行/CLI）、DiscoveryView 逻辑抽为 composables（模板不动），后续新功能直接落入对应域模块。

**Why this priority**: 用户要求"复用性高一点"；这是拆分的质量要求，依赖前三 story 的完成。

**Independent Test**: 拆分后向任一域新增小功能时，不需要触碰任何门面文件即可完成；模块间依赖保持单向（api → service → store）。

**Acceptance Scenarios**:

1. **Given** source 拆分完成，**When** 新增一个平台 source，**Then** 只需新增子模块文件并在门面注册，不修改既有平台实现。
2. **Given** DiscoveryView 拆分完成，**When** 查看该文件，**Then** 模板部分未被改动，逻辑位于 composables 且单文件低于红线。

### Edge Cases

- 某文件拆分后门面 re-export 与新模块同名冲突时：以旧路径符号为准，新模块内部命名让位。
- 拆分中发现某段代码本身有 bug 时：不借机修复，保持原样外迁，bug 记入 BACKLOG。
- 中间批次状态下（部分文件已拆、部分未拆）：每批独立提交，仓库在任何批次边界都处于全测试绿的可交付状态。
- `store_migrations.py` 历史迁移堆叠：只做物理归组降行数，不合并、不重写任何迁移逻辑。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统 MUST 将全部 11 个超标文件拆分至宪法红线（Python ≤800 行、Vue ≤1200 行）以内，验证方式为行数统计。
- **FR-002**: 拆分 MUST 保持行为不变：不改业务逻辑、不改对外接口与签名、不改数据库结构与数据格式、不改前端模板结构。
- **FR-003**: 旧 import 路径 MUST 通过门面 re-export 继续可用；测试文件对 `webui.app`、`webui.source` 等模块级符号的 monkeypatch 面在拆分后 MUST 全部保持有效，现有测试零改动。可 patch 符号以全仓 grep `patch("webui.app.` 的实测清单为准（2026-08-23 核验：`_BossCdpSource`×28、`boss`×15、`threading`×4、`ai_service`×4、`ScraperExecutor`×3、`uuid`×2、`os`×1、`_theme_path`×1，清单随批次复核更新）；外迁代码 MUST 经 `pipeline_context` 或 `webui.app` 模块属性在调用时动态访问上述全部符号（含 stdlib 模块），禁止在 runner 模块内 `from webui.app import` 或以自有 import 固化引用——否则门面补丁打不到真实执行路径。
- **FR-004**: `app.py` 拆分 MUST 采用「运行时上下文对象 → runner 逐个外迁（tuning → pipeline → recrawl → ai_screen）→ 薄路由归入 register_* 模块」的顺序，每步独立成批。
- **FR-005**: 每个批次交付前 MUST 通过：相关模块聚焦测试、后端全量测试、前端全部用例、`npm run build`、仓库卫生检查；全绿后以单个 `refactor` 提交收尾，不自动 push。
- **FR-006**: 拆分产出的新模块 MUST 遵守宪法文件布局（`webui/api|services|store`、`scripts/boss/`、`webui/src/composables/`）与单向引用（api → service → store）。
- **FR-007**: 拆分过程中发现的既有 bug MUST 不借机修复，原样搬运并记入 BACKLOG。
- **FR-008**: `DiscoveryView.vue` 拆分 MUST 只抽取 `<script setup>` 逻辑为 composables，模板段不改动；整文件 MUST 低于 Vue 红线 1200 行（主脚本预期约 600 行，为预期值而非硬门禁，禁止为凑行数做无职责依据的拆分）。

### Key Entities

- **运行时上下文（PipelineContext）**: app 内共享运行态的显式载体——任务表、锁、store、事件推送、运行写入助手；四个 runner 通过它访问共享状态。
- **门面（facade）**: 保留旧路径（`webui/app.py`、`webui/source.py` 等）作为兼容入口，re-export 全部既有公开符号。
- **拆分批次（batch）**: 一次独立 Plan/Tasks/Implement/Converge 的交付单元，每批一个 refactor 提交。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 全仓无任何 Python 业务文件超过 800 行、Vue 文件超过 1200 行（行数统计可验证）。
- **SC-002**: 每个批次交付时后端全量测试与前端 452 用例全部通过，既有测试文件零改动（git diff 验证）。
- **SC-003**: 全部批次完成后，应用从启动到抓取、筛选、历史查看的端到端表现与拆分前一致（冒烟验证）。
- **SC-004**: 拆分后每个新模块职责单一，模块间依赖单向；向任一域新增功能不再需要修改任何门面文件。

## Verification Scope *(mandatory)*

- 功能/重构/拆分交付：验证范围为相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 收口发布任务：默认只做卫生测试、hooks、`git diff --check`、`git status` 和 `scripts/release_check.ps1`（若存在）；不自动要求全量测试。
- 只有用户明确要求“全量/全部测试/完整验证”时，收口任务才执行全量测试。

## Assumptions

- 020（错误真相一致性）已落地，深路径回归护栏可用，无前置阻塞。
- 拆分只搬代码不改行为，因此不需要新增行为测试；安全网依赖既有全量测试。
- 行数红线按物理行计，与既有卫生/宪法口径一致。
- 本轮不做 re-export 门面的最终清理（旧符号永久保留），如需清理另行立项。
- 版本语义：纯拆分重构整体按 minor 交付（最后一批收尾时统一评估），单批不 bump 版本。
