# Implementation Plan: 高级设置深度自动调优

**Branch**: `master` | **Date**: 2026-07-29 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/011-deep-configuration-probing/spec.md`

## Summary

将当前一份全局高级设置改造成三层清晰模型：用户最近自定义配置、可整体应用/回退的稳定/平衡/极限模式版本、每次普通任务或实验轮次持有的不可变执行快照。新增深度实验控制模块和持久化状态，按列表、详情、AI 粗筛、AI 精筛分阶段串行探测，复用固定前置数据，记录完整耗时、重试、等待、错误、完整性和逐项质量差异，再通过漏斗晋级与边界细测生成小/中/大任务的三档配置。

真实实验采用控制者与执行者隔离：控制者签发包含全部精确参数的版本化任务单；低自主性执行 AI 只能触发、监控和提交结构化报告；客观指标由程序生成并带产物摘要，执行 AI 不能自报成功、调参或决定下一任务。实验任务严格串行、配置与正式设置隔离、跨重启可恢复；只有全量验证完成且用户确认后，模式版本才能整体应用。

## Technical Context

**Language/Version**: Python >=3.10（本地 `.venv` 为 3.11.15）；TypeScript 5.9 + Vue 3.5

**Primary Dependencies**: Flask 3、Vue 3、Vite 8、Vitest 4、requests、websocket-client、Python 标准库 `sqlite3`；不新增运行时依赖

**Storage**: SQLite `webui.db` 作为实验、配置版本、任务单、轮次和证据的权威存储；状态目录保存冻结输入与测量产物；现有 `advanced_settings.json` 仅作一次性自定义配置迁移来源

**Testing**: Python `unittest`、Vitest、`py_compile`、TypeScript/Vite 构建、隔离数据库集成测试、真实 Chrome/CDP 小中大任务验收、桌面与窄屏真实渲染检查

**Target Platform**: Windows 本地单用户 Web 应用，用户本人已登录的 Chrome CDP，单个 WebUI 服务实例

**Project Type**: 本地 Web 应用（Flask 后端 + Vue SPA + Chrome/CDP 抓取器）

**Performance Goals**: 在 1 至 30 个计划页内找到硬约束全部通过时总耗时中位数最低的配置；同一实验任意时刻只运行一个压力任务；低价值探索预计使总时长接近 24 小时时必须被淘汰

**Constraints**: 不通过减少关键词、城市、页数或质量标准提速；最终候选每规模至少 2 种结构、每结构至少 3 次；硬错误不得重复撞击；实验不得覆盖正式设置；运行配置必须冻结；程序生成客观证据；执行 AI 无调参和结论权

**Scale/Scope**: 9 个速度字段、4 个执行阶段、3 个任务规模、3 个系统模式、1 个最近自定义配置；最终最多 9 个内部模式槽位，但允许多个槽位引用同一配置

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

项目没有 `.specify/memory/constitution.md`。本计划以用户提供的项目 `AGENTS.md`、[spec.md](./spec.md) 和既有健康流程契约作为门禁。

### 前置门禁

- **真实证据**：旧实验、旧阈值和当前配置只能提出假设，不能冒充新基线或已验证结论。
- **程序掌握推进权**：任务状态、配置快照、完整性和客观测量必须由程序持久化；执行 AI 的文字报告不能替代客观证据。
- **硬错误阻断**：数据缺失、质量越界、状态损坏、登录/验证/来源阻断必须阻止候选继续晋级。
- **配置隔离**：实验临时值不得写入用户正式模式或最近自定义配置。
- **单文件原则**：不把 WebUI 调优逻辑塞入 `scripts/boss_cdp_raw.py`，该文件只增加必要测量点并保留现有详情抓取安全约束；复杂控制逻辑位于 `webui/` 深模块。
- **严格验证**：跨状态、持久化和真实外部链路按严格档分片实施；每片先有可执行验收，再推进下一片。
- **服务生效**：修改后端或前端后必须按项目规则替换旧 WebUI 服务并验证新进程与新构建实际生效。

**Gate 结论**：PASS。没有必须违反的项目原则。

## Project Structure

### Documentation (this feature)

```text
specs/011-deep-configuration-probing/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md
│   ├── executor-protocol.md
│   └── state-machine.md
├── checklists/
│   └── requirements.md
└── tasks.md                 # 由 speckit-tasks 后续生成
```

### Source Code (repository root)

```text
data/
└── city_codes.json                 # 可执行城市、全国和明确别名

webui/
├── app.py                          # HTTP 路由、后台执行 adapter、独占门禁
├── execution_config.py             # 新增：配置 schema、规范化、校验、快照、任务规模
├── tuning.py                       # 新增：实验控制、候选评估、任务单/报告校验
├── pipeline_exec.py                # 显式接收冻结配置、列表/详情测量事件
├── ai.py                           # 显式批量/并发、请求尝试和等待测量事件
├── source.py                       # 详情 terminal event 与批次证据透传
├── store.py                        # 实验表族、模式版本、租约、原子轮次确认
├── src/
│   ├── discovery.ts                # 输入规范化预览、规模边界纯函数
│   ├── types.ts                    # 模式、实验、证据接口类型
│   ├── views/DiscoveryView.vue     # 任务范围锁定、调优入口、结果应用
│   └── components/
│       ├── ExecutionModeSelector.vue  # 新增：三模式 + 最近自定义
│       └── TuningWorkspace.vue        # 新增：实验状态、阻断、报告与应用
└── dist/                           # 构建产物

scripts/
└── boss_cdp_raw.py                 # 仅补充安全测量事件，不改变隐藏保护参数

tests/
├── test_execution_config.py        # 新增：配置、城市、任务规模、快照
├── test_tuning.py                  # 新增：实验控制、任务单、评估、恢复
├── test_webui_store.py             # migration、租约、原子确认、版本应用
├── test_healthy_pipeline.py        # 普通任务冻结配置与健康流程回归
├── test_pipeline_tasks_cleanup.py  # 批次边界和停止行为
└── test_chrome_setup.py            # CDP 测量与城市码表回归
```

**Structure Decision**: 保持现有单仓 Web 应用结构。新增 `execution_config.py` 作为所有设置调用方共享的深模块，新增 `tuning.py` 隔离实验控制复杂度；`app.py` 只保留路由和 adapter，`store.py` 只保留持久化实现。前端把模式选择和长实验工作区从已经较大的 `DiscoveryView.vue` 拆成两个独立界面模块。核心抓取逻辑继续留在 `scripts/boss_cdp_raw.py`。

## Architecture

### 1. 配置权威与冻结执行

`execution_config.py` 提供一个小接口：规范化原始设置、验证物理约束、生成规范 JSON/摘要、计算任务规模、选择模式槽位。普通任务和实验任务在创建时取得完整 `ExecutionConfigSnapshot`，把快照与摘要写入运行记录并显式传给列表、详情和 AI 阶段。任何阶段不得再次读取可变全局设置。

现有 `advanced_settings.json` 在 migration 后首次读取为“最近自定义配置”，随后 SQLite 成为模式版本、活动选择和最近自定义的权威存储。旧文件不再参与运行时阶段决策。

### 2. 实验控制面与执行面

- **控制面**：`tuning.py` 验证输入版本、候选策略、晋级/淘汰、质量参考、任务单和报告；只有控制面能签发下一任务。
- **执行面**：复用现有单线程 pipeline executor，按任务单中的冻结快照运行一个明确阶段或端到端轮次，输出程序测量证据。
- **执行 AI**：读取渲染后的自包含任务单，完成前置检查、触发任务、轮询、保存报告；不得直接写数据库或修改设置。
- **数据库租约**：实验占有唯一执行租约；普通任务和其他实验在租约存在时不得启动。重启后租约按持久状态恢复或安全释放。

### 3. 测量与证据

各阶段接收统一 measurement sink，使用单调时钟记录耗时、使用墙钟记录事件时间。测量至少覆盖阶段、组合/批次、请求尝试、等待/冷却、重试/退避、输入输出数量、失败码、完整性和逐项质量差异。程序生成的事件和摘要在轮次确认事务中持久化；执行报告只能引用这些证据及其摘要，不能以 AI 自述替代。

### 4. 搜索与验证

每阶段按“单字段粗探 → 优胜组合 → 边界细测”推进。低价值候选只跑一次小任务；接近最佳或异常候选至少 3 次；最终候选在小、中、大每档至少两种结构、每结构至少 3 次，并执行不复用中间结果的端到端验收。控制面以完整总耗时中位数排序，以慢速尾部、重试、质量和压力作为并列决胜条件。

### 5. 用户模式

后端保存一个完整 `ModeConfigVersion`，内部含 `stable/balanced/extreme × small/medium/large` 九个槽位。前端只显示稳定、平衡、极限和最近自定义。点击系统模式时，后端按冻结前任务规模返回对应快照；手动修改后保存为最近自定义。模式版本只允许整体应用和整体回退。

## Implementation Slices

### Slice 1 - 配置 schema、输入规范化与冻结快照

**Goal**: 建立所有调用方唯一的配置语义，消除全局 JSON 晚绑定读取。

**Changes**:
- 新增 `execution_config.py`，定义 9 个速度字段、类型、物理边界、规范序列化和摘要。
- 将关键词、城市、全国和计划页数规范化放到后端权威接口；前端保留纯预览并由后端复核。
- 把 `run_search`、`fetch_job_details`、`screen_jobs`、`match_jds` 改为显式接收快照。
- 任务创建时把快照写入 `screening_runs.execution_params`；阶段不再重读正式设置。

**Gate**: 创建任务后修改正式设置，运行中每个阶段仍使用同一摘要；`pages` 不进入模式配置；9/10/19/20/30 边界和城市别名测试通过。

### Slice 2 - 模式版本、最近自定义与迁移

**Goal**: 建立三档九槽位、最近自定义和整体版本切换。

**Changes**:
- 新增配置状态与模式版本表；一次性导入现有 `advanced_settings.json` 为最近自定义。
- 后端返回稳定/平衡/极限的当前规模配置和最近自定义。
- 整体应用/回退使用单事务切换活动版本引用。
- 深度测试发现的新手动范围作为版本化元数据保存，不改变任务范围字段。

**Gate**: 三档按规模选择正确；模式切换不改变页数；自定义往返零丢失；版本整体回退无混用。

### Slice 3 - 实验表族、状态机和独占租约

**Goal**: 持久表达实验、输入、候选、轮次、阻断和跨重启恢复。

**Changes**:
- 新增 data model 中的实验表族与合法状态转换。
- 新增数据库级唯一执行租约和原子 claim。
- 活动轮次中断后标记 `uncertain`；已确认轮次永不重复。
- 普通任务与实验共用租约门禁，仍复用单线程执行 adapter。

**Gate**: 并发启动只有一个成功；服务重启后已确认轮次零重复；不确定轮次只重跑一次；实验与普通任务不重叠。

### Slice 4 - 统一测量事件与质量参考

**Goal**: 让速度、重试、等待、完整性和质量全部由程序提供客观证据。

**Changes**:
- 在列表、详情、AI 调用链加入统一 measurement sink。
- 接通详情 terminal event 的 `duration_ms` 与 safe code，同时补充其未覆盖的批次、等待和端到端时长。
- AI 记录每次 attempt、退避、截断拆分、批次输入输出数量和失败码，不保存密钥、原始简历或敏感响应。
- 新增质量参考版本和逐项差异比较。

**Gate**: 报告总耗时等于阶段工作、等待、冷却、重试与恢复的可追踪组合；缺失、重复和错配为 0；敏感值不进入事件。

### Slice 5 - 执行任务单、报告与控制者门禁

**Goal**: 让无上下文执行 AI 能机械执行，且无法获得实验决策权。

**Changes**:
- 实现 executor task manifest 和 report 的严格校验、规范摘要和状态转换。
- 从 manifest 渲染单次自包含 Markdown 任务单；不得包含凭据或任意写入路径。
- 报告只接受程序证据引用和规定字段；缺字段时整轮无效。
- 控制者显式提交下一任务；执行者不能自行生成候选。

**Gate**: 使用干净上下文的执行者只凭任务单完成验收；未知情况返回阻断；篡改参数、输入、次数或证据摘要被拒绝。

### Slice 6 - 分阶段 runner、漏斗晋级与边界选择

**Goal**: 把探路、复用、细测和最终模式选择固化为可恢复流程。

**Changes**:
- 支持列表、详情、粗筛、精筛和端到端五种轮次类型。
- 冻结并版本化每阶段复用输入；被测阶段必须真实执行。
- 实现动态步长、晋级门禁、危险边界、收益收敛和剩余时间预测。
- 生成三档九槽位候选版本，允许不同槽位引用同一配置。

**Gate**: 明显较差候选不进入高成本验证；预计时间接近 24 小时时只裁剪探路；最终重复与端到端门禁不可绕过。

### Slice 7 - 前端模式与深度实验工作区

**Goal**: 提供安静、可恢复且不暴露九槽位复杂度的用户流程。

**Changes**:
- 增加稳定/平衡/极限/自定义分段选择，修改速度字段自动进入自定义。
- 显示规范化关键词、城市、计划页数和任务规模；运行后锁定范围字段。
- 增加深度实验创建确认、串行进度、当前候选、剩余时间、阻断、取消/继续、摘要、详细证据和整体应用/回退。
- 运行中模式切换只对安全节点后的未开始工作生效，并明确记录混合段落。

**Gate**: 桌面与窄屏均无溢出、遮挡、双滚动；刷新/重启恢复同一实验；未完成版本没有应用操作。

### Slice 8 - 集成回归与真实执行包

**Goal**: 证明普通任务未退化，并为低自主性执行 AI 提供可直接运行的交付物。

**Changes**:
- 完成 quickstart 中的自动、隔离、重启和真实链路验收。
- 生成独立执行指南、任务单模板和报告模板；每次真实实验由控制者填入精确参数后签发。
- 按小、中、大多结构运行最终验收，用户确认后才应用完整模式版本。
- 更新 README 双语、CHANGELOG 和运行版本；重启 WebUI 并验证新进程。

**Gate**: 所有 SC 通过；执行者无自主决策步骤；旧健康流程回归通过；程序生成证据与执行报告一致。

## Post-Design Constitution Check

- 配置通过不可变快照进入运行链，满足真实和可追踪要求。
- 客观测量由程序生成，执行 AI 不能凭文字推进，满足程序掌握最终推进权。
- SQLite 事务、租约和不确定轮次语义覆盖长任务状态、失败阻断与恢复。
- 新模块仅承载 WebUI 深度控制复杂度；核心抓取仍留在既有单文件，符合项目单文件原则。
- 实验临时配置、模式版本和最近自定义分离，避免错误覆盖用户成果。
- 后续翻页深度/累计页数实验明确排除，没有借本功能扩大范围。

**Post-design gate**: PASS。

## Complexity Tracking

无宪法门禁违规。新增两个后端模块和两个前端组件是为了形成真实接口：配置 schema 被普通任务与实验共同复用，调优控制状态被 HTTP、持久化、执行任务单和测试共同复用；删除这些模块会使复杂度重新散落到 `app.py`、`pipeline_exec.py`、`ai.py` 和 `DiscoveryView.vue`，因此不是浅层包装。
