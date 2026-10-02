# Tasks: 多平台主流程分轨合流与统一条件映射（B096 V2）

**Input**: `specs/046-parallel-platform-flow/v2/` 下的冻结 Spec、Plan、Research、Data Model、Quickstart 与 Contracts

**Tests**: V2 明确要求测试先行、受控顺序矩阵、最终全量、真实源码 E2E 与真实 EXE 最小验证。

最新执行边界：2026-10-02 用户人工确认主链路页面体验，并授权修复分支合入 main、删除本地修复分支、更新 Bug 单及只读检查剩余事项。仅本地收口，不推送、打标签、发布或清理运行工作目录；EXE 继续排除。以下各轮测试、阻断和旧授权边界保留为历史记录，以本节为当前状态。

## 人工测试后本地收口（2026-10-02）

- 用户确认新轮旧进度回灌问题已解决；主链路页面展示符合预期。修复在新轮成功重置后记录已退出 Flow，并使旧请求失效，后续简历分析和条件弹窗查询不再挂回该 Flow；记录按画像保存在当前标签页会话中。
- 用户要求历史组合卡片与单平台卡片成套：复用历史统计、关键词和既有卡片样式，保留日志、删除及详情入口；移除「筛选条件已按当时冻结」提示；顶部固定「聚合 / BOSS / 智联」，聚合只展示组合流程，平台页分别展示各平台历史。
- 用户明确由人工操作和验收页面，本轮未修改测试文件、未运行功能测试、未操作或验收网页；三次后续界面变更均完成前端构建，最新构建成功。该证据不能称后续界面功能自动化回归通过。
- 本地收口卫生检查 `uv run python -m unittest tests.test_repo_hygiene` 14项通过；新增实例动作源码及测试已按本次收口授权纳入暂存区，原新增文件未提交的卫生阻断解除。合并前继续执行提交 hook 和发布检查，最终 Git 结果由实际命令确认。
- 历史后端全量3506项记录中的保护哈希失败仍保留，未改哈希或削弱断言，本次不重复全量。旧 D-11/D-12/D-10b、夹具和搜索 queued 投影线索仅为待核实记录，不据此新增修复或宣称仍然复现。
- B096 本地 Bug/待办单移入已完成归档。B106 跨平台去重等其他待办不属于本次合并范围，保留原状态。

**Organization**: 按四个 P1 用户故事组织。单个故事可以独立验证，但本功能只有四个故事全部通过才达到“主流程完成”的最高目标。

## 前一轮文档核查任务与证据口径（2026-10-02，历史边界）

- 本轮授权：在 V2 内更新文档，然后只读检查 `feature/parallel-platform-flow`；不修改产品代码、测试、服务或数据，不提交、推送、合并或删除分支。
- 复用标准：同一套业务逻辑以独立实例上下文运行一次或两次；组合层负责协调，不复制平台业务、动作判定或状态含义。
- T001–T054 及其勾选保留为历史执行记录；勾选表示当时执行或记录过阻断，不表示当前用户验收通过，也不要求全部重跑。历史测试与 E2E 结果不冒充本轮复验。
- 后续修复只针对当前代码证实的差距；已经共享且正确的路径保留。旧缺陷诊断被后续记录更正时采用更正，不按旧描述重复修复。
- [x] R001 对齐 Spec、Plan、Tasks、页面契约与数据模型的逻辑复用原则和当前阶段；2026-10-02 文档差异检查，不改产品行为。
- [x] R002 只读追踪当前单平台/组合的启动、执行、状态、动作、结果与恢复路径，区分复用、必要协调和重复业务；核查提交 `cb2dd8f59293602982b3a5a1ccc856271a399552` 的当前源码，结论见下节。未执行测试或真实运行。
- [x] R003 将已核实问题、对应文件、用户影响、建议顺序及未验证边界记录在本文件；不把历史报告或测试勾选当作当前代码结论。

以上三项为前一轮文档与核查任务；本次接手实施授权与修改后证据见下方「接手实施」，不再以旧只读边界阻止返修。

## 2026-10-02 当前分支核查与后续建议

### 核查范围与事实

- 分支为 `feature/parallel-platform-flow`，工作树开始时干净；main 留在独立工作目录，未切换、修改或合并。核查依据是当前源码、调用位置与相关测试原文，属于静态审查，不是运行验收。
- 启动已符合组合方向：`useDiscoveryParallelFlow.ts:608–654` 创建共同 Flow，按平台并发调用既有 `/api/execute-search`。`flow_submission_service.py:641–659` 的有 Flow / 无 Flow 路径均提交 `ctx.run_pipeline_task`。
- AI 执行已复用：`ai_screen_api.py:630–651`、`flow_ai_coordinator.py:229–242,509–522` 均落到 `ctx.run_ai_screen_task`；平台 lane 与 Flow 绑定属于执行协调，不是另一套筛选算法。
- 继续操作接回既有实现：`flow_task_coordinator.py:482–503,620–642` 分别调用抓取 continuation 或原 `api_task_continue`；应保留这些入口及身份、检查点、资源门禁。
- 旧 D-03 的展示重建已纠正：`ParallelPlatformProgress.vue:2–8,49–70` 使用 `TaskProgress` 与 `ScreenRoundActions`；`screenFlow.ts:185–224` 通过既有抓取/筛选主动作派生构造轨道动作。不能再把该历史问题原样当作尚未修复。
- 结果合流存在共用路径：`useDiscoveryFlowCoordinator.ts:218–227` 注入既有结果加载，`useDiscoveryFlowPresentation.ts:637–661` 按新结果签名刷新并通知；`useDiscoveryResults.ts:129–179` 的 preservePresentation 路径保留结果展示现场。这证明代码接线存在，实际滚动/排序/刷新体验仍需真实验收。

### 修复前静态核查证实的偏差（保留定位证据）

| ID | 等级 | 用户影响与代码证据 | 对应需求与建议 |
|---|---|---|---|
| V2-A01 | 阻断 | 单轨“结束并保存”直接绑定整份单平台现场的收尾函数：`DiscoveryView.vue:847,971` → `useDiscoveryExecution.ts:1054–1151`。函数清空全局抓取/筛选/重抓忙态，置整轮 finishedPartial，清理工作流存档，并把单轨响应直接传给 `setPipelineResult`；该结果写入默认会增加 resultEpoch、重选结果分类（`useDiscoveryResults.ts:131,162–178`）。另一轨仍在运行时，这些前端副作用超出目标实例，且可暂时用单轨结果替换合流结果。 | FR-013/014/015/017。保留共用 run 级结束保存接口，把业务收尾和现场更新按实例组织；共同 Flow 结果走现有合流读取，兄弟实例及阅读现场不被清理。不要另写并行专用保存算法。 |
| V2-A02 | 阻断 | 阶段展示与动作目标不一致：`useDiscoveryFlowPresentation.ts:470–488` 已按阶段得出 displaySnapshot，却仍把整条 Track 的 trackStatus 传给抓取动作。抓取已成功、AI 正在运行时，返回 02 可显示“已完成”并同时给“暂停”按钮；按钮经 `operateTrack` 调用轨道接口，后端 `_target_run_ids` 优先 screen_run_id（`flow_task_coordinator.py:404–419`），实际暂停 AI。 | FR-009/010/015/017。动作派生须同时接收当前阶段事实与实际可操作 run，旧阶段卡不能借后续阶段的状态造动作；共享派生和调用方都按同一实例/阶段口径修复。 |
| V2-A03 | 阻断 | 失败轨道动作与后端终态门禁冲突：`screenFlow.ts:107–108,207–221` 对 failed AI 派生“继续”及终止；轨道按钮经 `useDiscoveryParallelFlow.ts:694,728–731` 请求 resume/stop，但 `flow_service.py:423–424` 对 failed 一律拒绝为已结束。即使外观复用了，用户仍会得到无法成功的操作入口。 | FR-009/015/017。沿用既有可恢复性与终态分类，让前后端使用一致能力判断；不得以新增失败重试策略或仅隐藏按钮替代原因核实。 |
| V2-A04 | 非阻断缺陷，验收前需修复 | 组合动作跳过成熟的批次选择：单平台暂停/结束保存经过 `useScreenRoundFlow.ts:450–488` 的“立即停止 / 等本批完成”选择；组合暂停直接到 `operate_bound_task` 发 stop_event（`flow_task_coordinator.py:586–605`），组合结束保存直接调用 finishPausedTask，缺省 waitForBatch=false。用户对同一平台的操作语义随单独/组合模式变化。 | FR-009/017 与本次逻辑一致原则。把原批次选择、请求模式和忙态接到对应实例，复用已有 `pause_with_mode`、结束保存入口，不复制第二套弹窗和生命周期规则。 |

以上是静态代码能够证实的接线或契约偏差；尚未通过运行复现量化影响，不据此宣称全部路径失败或数据库损坏。`useDiscoveryFlowPresentation.spec.ts:621–639` 覆盖已完成抓取的卡片状态，但该例没有断言动作目标，不能证明 V2-A02 已被测试挡住。

### 建议修复顺序与范围（待产品代码授权）

1. 先让共用结束保存与批次动作能消费实例上下文，解决 V2-A01/A04；允许共享逻辑接收上下文，不另造“全部”业务分支。重点路径为 `useDiscoveryExecution.ts`、`useScreenRoundFlow.ts`、`discoveryDeps.ts`、`DiscoveryView.vue`、`useDiscoveryParallelFlow.ts` 与 `flow_task_coordinator.py`，最终写入清单须结合红测确认。
2. 再统一阶段/run 的动作能力与后端可恢复性，解决 V2-A02/A03；重点路径为 `screenFlow.ts`、`useDiscoveryFlowPresentation.ts`、`flow_service.py` 及原 pause/continue 入口。单平台和组合回归同一行为，不顺手改筛选策略。
3. 聚焦回归必须真实覆盖：一轨结束保存而另一轨继续；02 已完成抓取卡不误控 AI；失败/暂停入口与后端一致；批次选择只作用于指定实例；同轮结果、筛选排序及现场保持。现有历史任务不全量重跑、不削弱旧断言来制造通过。
4. 修复收敛后再按原门禁统一自动化与真实源码/EXE 验证，交用户验收；EXE、先后完成顺序、刷新/冷启动、失败组合与窄屏真实渲染均仍保留验收要求。本轮未执行任何运行门禁。

范围提醒：历史 Plan 禁止写入若干当前需要核查的共享模块，且现状已有后来授权的改动；本轮不倒推授权、不回退代码。开始返修前应明确精确文件边界。实测 `DiscoveryView.vue` 为 1173 行；`store_flow_state.py` 为 891 行（超过 Python 800 行红线，若需改该文件必须先按项目规则单独安排拆分）；状态、执行等 TypeScript 大模块分别为 2106/1343 行，是聚焦修改时的维护风险，不以此启动全仓重构。

### 未验证及延期

- 当前自动化是否通过、历史源码 E2E 能否在现提交完整复现、EXE 环境和实际行为均未重验；只保留历史记录，不宣称本轮通过。
- 本轮不读取正式数据库、不启动应用、不操作真实平台、不安装打包依赖、不改变基线哈希门禁。历史基线失败需要独立核实其定义与授权，不能顺手删除测试或重写哈希。
- 额外优化（如全仓去平台字面量、全部大模块拆分）不混入核心返修；只处理直接影响共同逻辑与独立实例的差距。

## 2026-10-02 接手实施（本轮授权）

用户已明确授权在现有功能分支修复 V2-A01–A04，前一轮只读边界是历史记录。本轮精确允许/禁止文件、引用方向及验收用例见 Plan「2026-10-02 接手修复边界」，覆盖下方历史清单中直接相关条目；不提交、推送、合并、发布，不改正式数据。

- [x] R004 核实工作树与四项接线偏差，保护六份既有文档；HEAD 与交接一致。读取既有 run 暂停/继续门禁，可恢复 failed 仅限 `is_resume_eligible_run`，不是所有失败都可重试。
- [x] R005 在修改产品前登记精确范围、引用方向、禁止文件及验收用例；不需改 `store_flow_state.py`，不启动无关拆分。
- [x] R006 实例结束保存/批次选择红测后修复共享入口，保护兄弟轨与合流现场；组件和页面回归通过。正式短程验证见下文，AI 批次两种选择仍只有自动化证据。
- [x] R007 阶段/run 与失败门禁红测后修复；后端旧阶段 409、终态 failed 拒绝与真实暂停恢复按同一能力口径验证。
- [x] R008 已执行统一最终自动化并保留失败清单；后端 3501 条/2 项门禁失败，最新前端 1538 条通过，构建通过。此勾选只表示执行并记录验证，不代表所有门禁通过。
- [x] R009 已核实正式入口、副本与 EXE 环境；分别记录实际证据、阻断和未测，完整真实门禁未通过，用户未验收，V2 不标完成。
- [x] R010 正式 Flow `e006ea895cc2462d` 暴露旧判定遮盖新判定，2 条红测后修复共享结果合并；233 条直接回归通过。正式页面返回/刷新后显示 2 match / 12 not_match / 440 pending / 358 dropped，排序、选中和详情滚动保持。
- [x] R011 正式新轮清理对已完成抓取发取消并被 503 阻断，4 条红测后按权威 Flow 提供当前未结束操作目标；317 条直接回归通过，正式网页新轮回到 01 且提交入口可用。旧 search_runs 为 queued 的诊断只读，不迁移或修补正式数据，不绕过 legacy 混合投影严格门禁。

### 修改后证据与交验边界（2026-10-02）

R014 真实复测未通过：Flow `23c150c579fa4aa1`，AI `61406ac7b0bc4d79a968515dc98db6d3`，批次立即暂停→paused、继续→running 均 HTTP 200；终结 partial/flow_result_incomplete，停在 03。先对共享 resume 后旧暂停事实污染新尝试写失败用例，再按 Plan 补充边界修复。另 84 条 Flow/API/暂停直接回归通过，不能替代此真实失败。

R014 第二次返修自动化证据：旧暂停事实影响完成的新红测失败（partial != succeeded）；共享 resume 使用既有 store API 建立新尝试，既有 owner 记录接到当前尝试，未改 store/算法/规则归约器。94 条 worker/白箱/继续/实例直接回归、2 条路径与尺寸检查通过，服务重启后 session HTTP 200；白箱 524 行、AI worker 640 行。日志 `ai-resume-attempt-red.log`、`ai-resume-attempt-green.log`、`ai-attempt-boundary.log`。再次等待正式新任务复测，不回写旧失败任务。

- [x] R012 正式终结 Flow 被旧暂停投影挡住新轮；1 条红测后修复归属与活动状态混用，360 条直接前端回归通过；正式及 test 副本网页新轮均回到 01、提交可用。
- [x] R013 正式单轨重复启动 AI；2 条红测后复用 Flow 刷新和既有任务恢复，401 条直接回归通过；最新前端全量 1542 条、构建通过。新正式单轨自动接回既有 AI，没有重复提交报错；暂停继续后的结果页仍被下项阻断，不能称单平台全流程通过。
- [x] R014 正式旧 AI `72974ac6433a49ee95df485267bf713b` 立即暂停→继续后状态冲突、停在 03；两轮失败用例及共享恢复返修后，新正式任务完成下方复测。精确边界与引用方向见 Plan。测试子智能体只记录页面/按钮/HTTP 事实，诊断修复由主代理负责；未修补正式旧 run。

R014 最新正式复测：Flow `1e0168a4e9be420c`，抓取 `74e263d9bb23478087f41774afb93fd4`，AI `f615fd0c77c246899fc9921a5d3cc38f`，结果 `9d8b3de8-8ac0-48c0-a5ad-f87d7b20824e`。经单平台入口自动到 03；JD 批次弹窗立即停止→paused、继续→running 均 HTTP 200，同一 AI run 最终公开 completed/db succeeded/integrity succeeded/evidence_complete=true，自动进入 04，四桶为 4/5/0/11。主代理实际查看稳定截图 `single-rerun-04-complete.png` 核对页面与数量（TEMP 证据目录）；这是正式单平台用户流程，不能替代完整双平台 SC-008。

共享后端返修收敛后最终全量：`uv run python -m unittest discover -s tests`，3503 条、1728.519 秒、退出 1，仅两项 FAIL：`test_baseline_files_keep_pre_v2_hashes`（保护哈希仍 36 项不符）与 `test_no_untracked_non_ignored_files`（4 个必要新增文件未提交）；无 ERROR。主代理读取完整日志尾部与失败名称核对；不改历史哈希、不提交或忽略新文件伪造门禁。证据 `backend-after-resume-final.log`。本次允许重跑依据是此前全量后实际修改共享 AI worker/白箱，并有两项红测、94 条聚焦通过及正式新任务复测；本次后不再无改动重跑全量。最新前端仍为 1542 条与构建通过（之后只改后端及文档）。

R014 当前自动化证据：新增用例在修复前失败（续跑时生命周期仍 terminal），仅同 owner/run 续跑将白箱 begin 改接既有 resume 后，80 条 worker/白箱/store/继续/实例动作直接回归通过，3 条路径与尺寸检查通过。历史事件保留、严格终态不可升级门禁保留；正式服务在无 running/queued 任务时重启，等待新任务网页复测。日志 `%TEMP%/career-scout-046-current-20261002/ai-resume-red.log`、`ai-resume-green.log`、`ai-resume-boundary.log`。保护哈希不符仍为 36 项，清单与断言未修改；不能沿用此前“后端源码未变化”作为此返修后的验证结论。

- 调用与边界：保存仍只有既有 `/api/task/finish/{run}`；实例上下文只隔离忙态和前端副作用。暂停复用 `pause_with_mode` 和既有批次弹窗；继续接既有 continuation。新轮组合层只提供已归属编号与当前未结束阶段，共用取消/归档入口执行；不复制生命周期逻辑。
- 首轮红测：172 条中 5 项新增回归失败、167 项旧回归通过；修复后 172 条通过。结果合流返修 2 条红测、新轮返修 4 条红测均先失败后修复，旧严格门禁断言保留。
- 后端直接集成/接口回归：`tests.test_b096_instance_actions tests.test_b096_flow_api tests.test_b096_flow_service tests.test_task_pause_support`，89 条通过。使用隔离 SQLite、Flask 测试客户端与外部边界桩；其中暂停调用真实共享暂停逻辑，不是正式账号 E2E。
- 后端统一全量：`uv run python -m unittest discover -s tests`，3501 条，2 项失败：`test_baseline_files_keep_pre_v2_hashes`（36 项历史保护哈希不符）和 `test_no_untracked_non_ignored_files`（本轮 4 个必要新增文件未提交）。输出保存在 `%TEMP%/career-scout-046-current-20261002/backend-final.log`；未重复运行后端全量。后续返修只涉及前端及文档，后端源码未变化。
- 基线审计：HEAD 与保护基线不符 43 项，工作树不符 36 项，本轮没有新增不符路径；基线、数量和哈希断言均未改。当前范围/尺寸两项结构检查通过，`DiscoveryView.vue` 1176 行；`store_flow_state.py` 891 行且未改。
- 最新前端全量：70 文件、1538 条通过，`npm run build`（含 `vue-tsc --noEmit`）退出 0，2300 modules；保留现有大包提示，不扩范围拆包。单平台、两种完成顺序、过期操作、共享批次选择、结果原地合入和现场/新轮回归在自动化中覆盖；使用假件，不能称为正式端到端。
- 卫生：14 条中 13 条通过，唯一失败为 4 个新增源文件/测试尚未提交；不通过提交、忽略或削弱门禁伪造通过。文档本地路径问题已修复并重验；`git diff --check` 无错误。
- 正式源码运行：服务已重启并加载最新构建，正式库确认 `env=live`。Flow `e006ea895cc2462d` 从网页“全部”入口用已有真实条件启动双轨，智联先完成 AI，BOSS 抓取中报 `source_cdp_unavailable` 后失败。已完成抓取卡无后续 AI 操作、失败卡无不可执行的继续/终止；04 最新判定已复核。设置薪资最高、选择第 2 项、详情滚动 118px 后刷新，三者及分类数量均保持；390px 窄屏两卡均 366px，页面无横向溢出。此轮不是完整 SC-008。
- 正式短程独立操作：Flow `60b8904d47cf4bc2`，BOSS run `81acb39ef1bf4944bffaf61b28625539` 处于暂停，结束保存后 BOSS stopped，智联 run `bb87df23b82b4a6792c4053a3415eb0a` 仍 running。随后智联暂停至 paused、继续回 running，BOSS 状态不变；最后经网页结束智联，Flow stopped，结果快照 `bfe653e3-b8f2-4c0a-bf9b-410f4c056cda`，238 岗位。BOSS 此次零岗位、没有结果快照；此证据验证兄弟运行与真实暂停/继续，不冒充两轨非空结果合流或 AI 批次验收。
- 副本冷启动：只读备份正式 SQLite 到独立临时目录，仅副本 env 改 test，5001 正常应用入口、全新浏览器。首份活动副本恢复两轨中断，逐屏查看/保存/导出/新轮可达；单轨保存保留兄弟状态。旧构建新轮产生两次已结束任务 409，因此该次不标零错误通过。最终构建的新副本可恢复短程 Flow 结果，但没有真实 AI run，03 条件页不解锁；完整硬走查未通过，另列收敛任务，不用副本冒充正式 E2E。
- EXE：当前环境 `PyInstaller` 不可导入，未安装依赖、构建或执行 EXE，SC-009 未验证。正式两轨完整合流、AI 批次两种选择、两个实际完成顺序及正式单平台完整主流程仍需用户验收环境补测；历史 D-11/D-12/D-10b 和夹具线索未因此宣称消失。
- 服务与数据：正式源码服务保留供验收；短程测试已结束，两轨没有遗留活体任务。正式数据只经项目网页操作，没有手动迁移或修补。副本服务和三个本轮浏览器已关闭；临时文件删除（包括按具体文件删除）被自动审批以 `blocked by policy` 拒绝，因此复制库、截图与工具临时产物仍留在 `%TEMP%/career-scout-046-current-20261002/` 和本轮 `%TEMP%/.playwright-cli/` 产物中，不入仓库；失败测试输出保留供复查。

## File Boundaries

- [x] R017 正式双轨完成后，自动恢复已选常用配置覆盖本轮编辑（短范围1×1变5×2）；完成轮返回02/03时共享Workflow清除会话。3条失败用例先红，共享配置自动恢复只接身份、保留已恢复的可用草稿；用户显式选配置仍应用全部内容。完成轮在返回页仍保留会话。382条直接回归及新构建正式刷新02/03/04保持，见下方最新证据。
- [x] R018 正式第三轮03順序刷新前BOSS/智联，刷新后智联/BOSS；公开AI开始时间不代表会话中首次加入顺序，现有内存顺序丢失。针对刷新重挂载红测后，现有FlowPresentation在按Flow隔离的页面现场保存已观察顺序，不存业务状态；重挂载、不同Flow隔离及既有后入追加回归通过，存储异常沿原时间回退。正式新构建03顺序刷新保持。

最新返修验证：`index-Lm1mxb1a.js`，正式原浏览器02经UI修改为AI开发助理/东莞1×1，reload仍停02且短范围保持；03 reload仍停03、智联/BOSS同序，七组不限/未选与真实提交相同；04选中智联后端开发工程师、薪资最高、列表/详情scrollTop=100/100，reload同页同岗位同排序同滚动，四桶14/22/41/62。新轮按钮回01三入口enabled，未启动新任务。控制台0错误0警告。证据 `guard-02/03/04-before/after-reload.png` 与 `guard-01-new-round.png`（均TEMP）；最新前端70文件1548条通过，构建成功，3条范围/尺寸检查通过。后端无变更，不重复全量；旧后端3506条两项结构/卫生失败仍保留。

最终补证：Flow `1d19f93928be4edb` 明确BOSS先非空结果、智联后合入；只暂停/继续智联均200，04选中BOSS同一岗位（稳定键a90c16e8cb0080861HN_2dq5FFRY）、薪资最高、列表/详情100/100合流前后保持，智联通知一次，最终25/83/0/31。前一A轮100后又修改排序的观测不能单独归因合流，不据此改CSS；新轮明确先排序稳定再设置滚动验证已通过。正式BOSS单平台Flow `4c0de59e266a45b2` 点击单独抓取一次自动接AI，在20/30详情时立即保存HTTP200、waited_for_batch=false，30待确认部分结果可见并明确提前结束，未伪装完整成功。副本cold-source-final（只读备份正式库、仅副本env=test、正常5001入口、全新浏览器）恢复上述完整双轨：02两卡90/49，03两卡、七组，04两方25/83/0/31；非首岗位/薪资最高/100/100刷新保持，CSV导出到TEMP，新轮01三入口enabled且02/03/04锁定，0错误0警告。副本不计作正式平台E2E；只停止本轮副本Python进程，正式服务保留。

条件映射正式渲染补证：原有画像确认aria-pressed=false时主入口按既有画像门禁提示并拦截，确认后一次打开条件弹窗，无新Flow/搜索提交。全部5K-10K/1年以下/本科映射BOSS5-10K及在校生/应届生/经验不限/1年以内、智联4K-6K/6K-8K/8K-10K及经验不限/1年以下；BOSS追加大专不反向修改全部或智联本科，统一追加硕士后双方本科/硕士重新覆盖、BOSS大专消失，其它已选字段保持；融资/公司性质未自动选。多选是实际行为，不把追加描述成单选替换。0错误0警告，测试浏览器已关闭，未保存或改既有配置。主动返回旧页后阶段推进不抢页仍单独补真实证据，T055待最后核实。

正式双轨真实证据（不替代用户最终验收）：Flow `9f66959a829d4936` BOSS先结果、智联后合入，两轨非空完整成功；智联详情批次等本批完成暂停及继续均200，最终57判定岗位、82淘汰，04四桶27/30/0/82。此轮选中项出现过切换，前/后截图采样不完整，不将其计作阅读现场通过，继续补测。Flow `c52333a288134429` 通过仅暂停BOSS使智联先完成，BOSS立即暂停/继续均200；后入BOSS后仍选同一智联岗位、薪资最高和100/100滚动，通知1次，最终115判定岗位、24淘汰，04四桶30/85/0/24。Flow `1e4efa867f734279` BOSS详情等本批抓完保存200（wait_for_batch=true），BOSS41非空结果/49淘汰，智联继续且暂停/继续均200后36结果/13淘汰；Flow stopped，04四桶14/22/41/62、当前Flow两方来源77岗位。旧平台卡不产生后续AI动作。正式BOSS单平台/立即保存及第一顺序阅读现场继续取证，T055不提前勾选。

最终收证（R016/T056）：正式原浏览器刷新加载 `index-C4fha1qn.js`，04可达且2/8/0/10正确。随后网页单独抓取一次，范围智联/AI开发助理/东莞/一页，Flow `de25ca3f720c4307`，抓取 `88108d9c52ab4e92bca01f9750723af6`，AI `11bdc311353b4b8b90f9c0968f2bf68f`，结果 `f1ac53e4-05ea-4c7c-95f7-20452d360937`；未点AI启动，自动接回既有worker、自动进入04，公开 completed/succeeded/done、完整性 succeeded/evidence_complete=true，四桶5/15/0/0。主代理查看 `live-final-04.png` 核对真实结果。cold-guard加载同一最新构建，03七组、04四桶、选中岗位和排序保持。最新前端70文件1544条通过、退出0（`frontend-after-restore-final.log`），构建成功；3条范围/尺寸检查及git diff --check通过。保护哈希实时仍36项不符，4个必要新增文件未跟踪，未放宽门禁或提交。测试浏览器已关闭、只停止自己启动的cold-guard服务，正式源码服务保留供用户验收；正式双平台SC-008与EXE仍未验证，不标V2完成。

- [x] R016 Flow `7d97e27586a94fa0`，智联/AI开发助理/东莞/一页20岗位；AI `48e8114e923b44d385541b5f24f4b96b` completed/succeeded、完整性 succeeded，六分钟保持；Flow done，结果 `693e4379-14b7-46c0-bd8f-e48533dffcb2`（匹配2/不匹配8/待确认0/丢弃10）。原网页停03、查看结果disabled，截图 `live-5000-03-completed.png`。实际点击单独抓取、auto_screen=false；共享交接与草稿恢复两项返修后，旧正式现场04解锁，新正式任务自动到04，见最新收证；未回写后台结果。

R016 自动化：auto_screen=false 的针对性红测缺少 restoreRunningTask 调用；共享 Flow 交接去掉旧自动启动标记的附加条件，既有恢复接回后台 AI，不发新 AI 请求。286 条 Tasks/Execution/View 直接回归通过；已有完成单轨 Flow 的真实按钮启用、点击后结果内容断言通过。前端构建成功 `index-BczvPt1M.js`（既有大包警告），日志 `single-flow-no-auto-red.log`、`single-flow-no-auto-green.log`、`single-flow-result-navigation.log`、`single-flow-no-auto-build.log`。正式旧结果刷新、新短任务与只读副本 cold-guard 走查及最终前端测试等待观察事实；不提前标整项通过。

R016 恢复覆盖返修：正式已加载上述新构建、Flow结果接口HTTP200且10/10岗位，旧03存档恢复仍清掉结果开放状态。共享草稿恢复新增保留权威结果上下文，初始/profile Flow恢复沿共同入口传入；红测当前结果被旧null覆盖，245条直接回归通过；带旧03存档的视图断言确认04启用、点击后当前结果可见。构建 `index-C4fha1qn.js`，证据 `flow-draft-restore-red.log`、`flow-draft-restore-green.log`、`flow-draft-navigation.log`、`flow-draft-restore-build.log`。正式原浏览器及新短任务重测等待返回。

副本 cold-guard（只读正式备份，env=test）观察：01/02/03七组/04均可达，2/8/0/10四桶，完整成功、未降级、证据完整；CSV HTTP200，刷新选中“前端工程师助理”和综合排序保持，新轮01两入口enabled且未提交。控制台0错误0警告；没有新增抓取/AI请求。截图 `cold-guard-01.png`、`cold-guard-02.png`、`cold-guard-03.png`、`cold-guard-04-before-export.png`、`cold-guard-04-after-refresh.png`、`cold-guard-01-new-round.png`。该单轨副本不替代正式双平台完成顺序或EXE；最新恢复返修后仍需返回检查。此前前端70文件1543条通过、退出0（`frontend-after-handoff-final.log`）；最新返修后正在做一次最终前端确认，旧结果不代替最新结果。

R015 最新收证：新任务上述AI完成后六分钟所有公开采样仍 completed/succeeded，无失联降级，guard真实完成稳定性已取证；前端到04失败独立记录R016，冷启动仍未验收。最终后端3506条用时2252.979s，退出码1，仅保护哈希与未跟踪文件卫生两项失败；日志 `backend-after-guard-final.log`，禁止无后端改动再跑全量。前端返修后只做相关前端测试与构建。

- [x] R015 完成后的遗留批次仍写失败：正式 AI `a68b0010031f4ddcb496953560c747cd` 在 05:29:47 成功，05:33:19/39 被 guard 追加 stall/fallback/unit_failed；冷启动仅暴露已有污染。共享 guard 实例状态门禁的失败用例、62条直接回归及新正式完成任务六分钟观察已取证。T056 仍未通过；03 只显示进入 AI 的平台符合页面契约，单卡本身不能推断 legacy 恢复错误。

R015 自动化：三个针对性用例修复前失败，覆盖终结/暂停实例的遗留批次、stall 后完成和持久终态与内存冲突；共享 guard 在产生监控副作用前退役非活跃批次后，62 条 guard/白箱/暂停/实例直接回归及 3 条范围/尺寸检查通过。`pipeline_guard.py` 455 行；`store_flow_state.py` 未修改。证据 `guard-terminal-red.log`、`guard-terminal-green.log`、`guard-boundary.log`。正式服务在确认无 queued/running run 后重启，新正式任务及唯一一次最终后端验证仍在进行。

R015 第一次真实复测未完成：网页实际启动画像填充的五关键词、两城市、每组合一页，146 岗位（未采用预期短配置）。Flow `90c5b8365f794d8f`，抓取 `05a60ae1d8a3407cbf32a8adcf7c585d`，AI `13587574fc074504aeed1fed6e45bd49`；等当前批次暂停和继续均 HTTP 200，最终 failed/flow_result_incomplete、白箱 partial，停在 03/查看结果 disabled。事件表显示 JD 87 条中取得 86、待确认 1，并未出现 guard stall/fallback；不能标完成后六分钟验证通过。截图 `live-5000-03-final.png`。已明确下一次只用既有短范围：智联、AI开发助理、东莞、每组合一页，不调整完成门禁或回写旧任务。

- **Allowed files**: 只允许修改 `plan.md` 中列出的产品、测试和文档路径。
- **Forbidden files**: `design/**`、`v1/**`、`useDiscoveryState.ts`、`app.py`、`store.py`、`exec_search_api.py`、Flow 后台协调/存储/runner、平台抓取树枝、B106、发布与版本文件。
- **New files**: `parallelFilterMapping.ts`、`useDiscoveryFlowPresentation.ts`、`store_migrations_v8.py` 及对应两个前端测试文件。
- **Reference direction**: view → composable/pure mapping → API；API → service → store；并行进度壳 → 原 `TaskProgress.vue` / `ScreenRoundActions.vue`，共享业务派生按实例计算。
- **Line gate**: `DiscoveryView.vue` 最终低于 1200 行且净减少；新 Python 低于600行预警线，新 Vue 低于900行预警线。
- **Baseline gate**: `pre-v2-protected.sha256` 已冻结 76 个 V2 范围外的既有工作区文件；清单内文件哈希不得变化，清单外新增差异只能出现在允许路径。`design/**` 不读取、不哈希、不修改。

## Verification Gate

- 每个故事先让聚焦测试失败，再实施，再只跑本故事和直接相邻回归。
- 整条链收敛后只运行一次干净后端全量、前端全量、构建和卫生检查。
- 自动化不能替代真实源码/EXE 主流程；真实门禁缺失时不得宣布完成。
- 不提交、不推送、不发布；这些动作不在本 Tasks 范围。

## Implementation Record

- 2026-09-28：根据真实 E2E 发现并经用户明确授权，补修智联回调契约、CDP 关闭端点容错、详情无效输出诊断、流水线失败证据和结果通知竞态；本地回归与结构门禁纳入验证，真实源码复验仍待执行。
- 2026-09-28：根据后续真实 E2E 取证并经用户明确授权，新增 `webui/src/composables/discoveryDeps.ts` 与 `webui/src/composables/useDiscoveryExecution.ts` 精确 follow-up 路径，注入当前全部流程的抓取任务归属判断，阻止 Flow-owned 任务被 legacy 自动筛选重复启动；本地回归待随本轮验证，真实源码复验仍待执行。

- 2026-10-01（V2 重新执行轮）：规格新增 `## 状态词表`、`## 状态所有权`、`## 冷启动走查（硬验收）` 三节作为验收尺子（提交 `4fe49ae`）。基线证据：前端全量 69 文件 / 1394 例全绿（`%TEMP%\cs046_v2\fe-baseline.log`）；后端聚焦组合 189 例中唯一失败为 `test_baseline_files_keep_pre_v2_hashes`（36/76 基线哈希不符，属结构门禁：基线取自未提交工作树且与 `e4ca9e0` 自相矛盾，在 HEAD 不可能绿）。
- 2026-10-01 D-01（提交 `910589d`）：真实库冻结快照实测统一经验值为「1年以下」（来自简历建议，符合 FR-004），BOSS 四档同选符合冻结契约，其中「经验不限」是岗位属性档而非字段级哨兵。实测真因两处已修：智联把岗位档 `-1` 误登记为字段级不限制（`ai_platform_adapter.py`）、智联薪资「5K-10K」少映射一档 `4K-6K`（`parallelFilterMapping.ts`）；反向投影改为唯一命中。`tests.ai.test_ai_platform_filters` 17/17、`parallelFilterMapping.spec.ts` 17/17、`vue-tsc` 0 错。
- 2026-10-01 D-02（提交 `39091ed`）：`store_runs.py` 批次与阶段末两条写入路径收敛为共用 upsert 与唯一取值域，整包 JSON 不再进 `verdict` 列；全仓 `json.dumps(verdict` 归零，文件 789 行仍在红线内。`tests.healthy_pipeline.test_pipeline_pause_resume` 69/69 通过（其中 2 条旧断言按新入库形状由整包 JSON 改为枚举比对，属规格驱动的返修）。历史 10851 行整包 JSON 未迁移，仍由读方容错分支与 `historical_recovery` 兜住。

## Phase 1: Setup（结构护栏）

**Purpose**: 在改实现前先把本轮最容易复发的结构错误变成失败测试。

- [x] T001 在 `tests/test_b096_final_review.py` 增加 V2 边界与结构红测：读取 `specs/046-parallel-platform-flow/v2/pre-v2-protected.sha256` 校验 76 个既有范围外文件哈希未变，拒绝允许路径和冻结清单之外的新差异；同时禁止 `ParallelPlatformProgress.vue` 固定 35/65/100、禁止双列 grid、要求复用 `TaskProgress.vue`、禁止 `platformConfirmed`/确认复选框，并校验 `webui/src/views/DiscoveryView.vue` 不超过 1200 行

---

## Phase 2: Foundational（共享契约与持久化基础）

**Purpose**: 建立四个故事共用的类型、映射与配置快照存储；完成前不得接 UI 主流程。

- [x] T002 在 `webui/src/types.ts` 增加 `UnifiedFilterValues`、`PlatformFilterValues`、`ConditionSnapshotV2` 和常用配置 `conditions` 类型，保留 version 1 响应兼容
- [x] T003 [P] 在 `tests/webui_store/test_store_migrations.py` 先写迁移 040 红测：新库/旧库都有 `condition_snapshot_json`、默认 `{}`、重复迁移幂等
- [x] T004 在 `webui/store_migrations_v8.py` 实现迁移 040，并只在 `webui/store_migrations.py` 增加 import/MRO 组装使 T003 通过
- [x] T005 [P] 在 `webui/src/__tests__/parallelFilterMapping.spec.ts` 先写纯函数红测：冻结表全选项覆盖、标签精确解析、去重/不限互斥、字段级覆盖、平台微调不回流、专属字段保留、简历语义初值与 schema 缺项阻断
- [x] T006 在 `webui/src/parallelFilterMapping.ts` 实现映射版本、六类统一 schema、人工映射、简历语义投影、字段级平台覆盖、override 计算和 ConditionSnapshotV2 构造，使 T005 通过
- [x] T007 在 `.specify/memory/constitution.md` 模块地图登记 `parallelFilterMapping.ts`、`useDiscoveryFlowPresentation.ts` 与 `store_migrations_v8.py` 的职责和单向引用边界

**Checkpoint**: 统一条件能确定性生成两平台最终值，常用配置具备冻结它们的持久化位置。

---

## Phase 3: User Story 1 - 从“全部”直接确定两平台条件（Priority: P1）

**Goal**: “全部”默认显示六类统一条件，立即映射到两平台；无平台确认复选框；一次主按钮启动；中性主题；常用配置按原值恢复。

**Independent Test**: 修改统一字段 → 检查两平台映射 → 平台微调不回流 → 再改统一字段只覆盖同字段 → 保存/恢复配置 → 一次点击启动，全程没有额外确认。

### Tests for User Story 1

- [x] T008 [P] [US1] 在 `webui/src/components/__tests__/OneClickScreenDialog.spec.ts` 写红测：标签顺序为全部/BOSS/智联、全部仅六类、专属字段只在平台页、切页立即看到映射、无“我已确认”控件、主按钮始终按当前值提交
- [x] T009 [P] [US1] 在 `webui/src/composables/__tests__/useDiscoveryParallelFlow.spec.ts` 写红测：没有确认状态/缺失确认异常，请求不发送 `confirmed`，Flow 与 execute-search 分别保存完整快照和当前平台最终字段，恢复兼容 V1 纯字段快照
- [x] T010 [P] [US1] 在 `webui/src/composables/__tests__/useTheme.spec.ts` 写红测：`all` 在 light/dark 使用中性令牌、旧平台写入不能越过 all 作用域、清除作用域后恢复最近品牌
- [x] T011 [P] [US1] 在 `tests/test_b096_flow_api.py` 写红测：`POST /api/flows` 不要求 `confirmed` 且不再返回 `confirmations_required`，仍校验 profile/selection/platform availability
- [x] T012 [P] [US1] 在 `tests/test_search_packages.py` 写红测：version 2 完整校验/返回 conditions、version 1 无条件快照仍可读、损坏快照整包拒绝、不污染画像事实
- [x] T013 [P] [US1] 在 `webui/src/composables/__tests__/useSearchPackages.spec.ts` 与 `webui/src/views/__tests__/DiscoverySearchPackages.spec.ts` 写红测：保存/恢复统一值、两平台最终值、overrides、专属字段和 mappingVersion，恢复不重算映射且失败不部分回填

### Implementation for User Story 1

- [x] T014 [US1] 在 `webui/src/components/OneClickScreenDialog.vue` 用统一/平台三页草稿替换双平台确认状态，接入 `parallelFilterMapping.ts` 的字段级即时映射并保持单平台模式原行为
- [x] T015 [US1] 在 `webui/src/composables/useDiscoveryParallelFlow.ts` 删除 `platformConfirmed` 及确认门禁，持有统一值和平台最终值，提交 ConditionSnapshotV2，并在 execute-search 仅发送当前平台最终字段
- [x] T016 [US1] 在 `webui/flow_api.py` 删除 `confirmed` 参数解析和逐平台确认错误，保持既有 FlowService 调用、幂等和错误映射不变
- [x] T017 [US1] 在 `webui/src/composables/useTheme.ts` 增加中性 `all` 作用域优先级，在 `webui/src/styles/theme.css` 增加 light/dark 中性令牌且不改 BOSS/智联令牌
- [x] T018 [US1] 在 `webui/store_search_packages.py` 读写 `condition_snapshot_json`，在 `webui/search_packages.py` 增加 version 2 白名单校验/投影并保持 version 1 可读
- [x] T019 [US1] 在 `webui/src/composables/useSearchPackages.ts` 构造、保存、完整校验并一次性恢复条件快照，失败时回滚关键词、城市、画像和条件全部 refs
- [x] T020 [US1] 在 `webui/src/views/DiscoveryView.vue` 以薄接线把统一草稿、平台草稿、简历 semantic、常用配置和 all 主题连到现有入口，删除确认参数传递且确保文件净减少
- [x] T021 [US1] 运行 `uv run python -m unittest tests.test_b096_flow_api tests.test_search_packages tests.webui_store.test_store_migrations`，并在 `webui/` 运行 `npm test -- parallelFilterMapping.spec.ts OneClickScreenDialog.spec.ts useDiscoveryParallelFlow.spec.ts useTheme.spec.ts useSearchPackages.spec.ts DiscoverySearchPackages.spec.ts`，只修复 US1 失败

**Checkpoint**: US1 可独立演示“全部”六类条件、平台微调、配置恢复和一次启动。

---

## Phase 4: User Story 2 - 领先平台推动页面、落后平台继续追赶（Priority: P1）

**Goal**: 两条线不互等；02/03 每平台一行原进度；领先平台推动 03/04；用户返回后不抢页；独立操作不串线。

**Independent Test**: 分别驱动智联先、BOSS 先、近同时和一轨失败，检查阶段列表、稳定顺序、自动前进、手动停留和单轨操作。

### Tests for User Story 2

- [x] T022 [P] [US2] 在 `webui/src/composables/__tests__/useDiscoveryFlowPresentation.spec.ts` 写红测：智联先/BOSS 先/近同时、02 保留完成抓取、03 仅到达平台、稳定追加、首次水合不跳页、运行跃迁自动前进、手动返回后不抢页
- [x] T023 [P] [US2] 在 `webui/src/components/__tests__/ParallelPlatformProgress.spec.ts` 写红测：每项渲染原 `TaskProgress`、纵向单列、传入真实 snapshot、无英文原始阶段/假百分比、暂停继续停止只发对应平台
- [x] T024 [P] [US2] 在 `webui/src/views/__tests__/DiscoveryView.spec.ts` 写视图红测：02 两条抓取进度、03 按进入顺序追加、第一条 AI/结果分别推动步骤、第二条到达不重复跳页

### Implementation for User Story 2

- [x] T025 [US2] 在 `webui/src/composables/useDiscoveryFlowPresentation.ts` 实现按 run id 拉真实 task-state、02/03 阶段投影、进入时间排序、单调解锁水位、初始水合抑制和手动停留状态机
- [x] T026 [US2] 历史任务：在 `webui/src/components/ParallelPlatformProgress.vue` 删除自制进度算法和卡片网格，改为纵向渲染 `TaskProgress.vue` 并保留对应平台操作按钮。2026-10-02 澄清：操作必须复用 `ScreenRoundActions.vue` 和共享业务派生，不能以另写按钮规则完成本项；当前符合情况由 R002 核查，不由旧勾选推断。
- [x] T027 [US2] 在 `webui/src/composables/useDiscoveryIslandBridge.ts` 接受 Flow 投影的可达步骤集合，避免灵动岛仍用旧单平台 `enabledSteps` 把已解锁 03/04 拦住
- [x] T028 [US2] 在 `webui/src/views/DiscoveryView.vue` 用 Flow presentation 的 `scrapeItems`/`screenItems`、投影步骤与选择处理替换 V1 直接 track 展示，保持单平台路径不变且文件低于 1200 行
- [x] T029 [US2] 运行 `webui` 下 `npm test -- useDiscoveryFlowPresentation.spec.ts ParallelPlatformProgress.spec.ts useDiscoveryIslandBridge.spec.ts DiscoveryView.spec.ts`，并运行 `uv run python -m unittest tests.test_b096_final_review`，只修复 US2 及直接相邻失败

**Checkpoint**: US2 可在受控状态下完整证明“分轨合流”，但尚不宣称结果阅读现场和刷新历史全部完成。

---

## Phase 5: User Story 3 - 结果原地合流且不打断阅读（Priority: P1）

**Goal**: 第一平台结果立即可见；第二平台结果在同一 04 原地加入，保留阅读现场并只提示一次。

**Independent Test**: 04 设置平台筛选、分类、列表筛选、排序、滚动和展开岗位，再加入第二平台结果；所有现场保持且只出现一次对应灵动岛通知。

### Tests for User Story 3

- [x] T030 [P] [US3] 在 `webui/src/composables/__tests__/useDiscoveryResults.spec.ts` 写红测：同 Flow 新结果使用 preservePresentation，不切分类/平台筛选、不换 scene identity、不混入其他 Flow，失败平台部分岗位仍保留未筛选标记
- [x] T031 [P] [US3] 在 `webui/src/composables/__tests__/useDiscoveryFlowPresentation.spec.ts` 补红测：新 result signature 触发一次刷新和一次 `{flow}:{platform}:{run}` 通知，初始双结果水合不通知，同签名重复轮询不通知
- [x] T032 [P] [US3] 在 `webui/src/views/__tests__/DiscoveryView.spec.ts` 写集成红测：第二平台加入后筛选、排序、滚动、展开和选中岗位保持，灵动岛载荷指向 results

### Implementation for User Story 3

- [x] T033 [US3] 在 `webui/src/composables/useDiscoveryResults.ts` 为 Flow 同轮合流增加 `preservePresentation` 选项，复用现有 Flow results 请求并保持当前分类、平台筛选和 scene identity
- [x] T034 [US3] 在 `webui/src/composables/useDiscoveryFlowPresentation.ts` 实现结果签名基线、首次水合抑制、去重通知及注入式结果刷新，不直接操作结果 DOM
- [x] T035 [US3] 在 `webui/src/views/DiscoveryView.vue` 将结果刷新与 `island-notice` 现有 emit 接到 presentation，并移除当前 deep watch 的无差别结果重载
- [x] T036 [US3] 在 `webui/` 运行 `npm test -- useDiscoveryResults.spec.ts useDiscoveryFlowPresentation.spec.ts DiscoveryView.spec.ts`，只修复 US3 和结果工作台直接回归

**Checkpoint**: US3 可独立证明第二平台结果不打断用户阅读。

---

## Phase 6: User Story 4 - 刷新、返回和历史始终恢复同一流程（Priority: P1）

**Goal**: 02/03/04 刷新恢复当前有效页面和已解锁入口；两条状态、条件快照、历史与流程归属不串轮。

**Independent Test**: 在四种现场刷新：双抓取、抓取+AI、结果+运行、双完成；再浏览历史和开始下一轮，检查页面、入口、进度、条件和结果归属。

### Tests for User Story 4

- [x] T037 [P] [US4] 在 `webui/src/views/__tests__/DiscoveryRecovery.spec.ts` 写红测：02/03/04 各自刷新原页、已解锁页不回锁、刷新不自动前进、刷新后用户停留旧页仍不被慢平台抢走
- [x] T038 [P] [US4] 在 `tests/test_b096_flow_history.py` 写红测：历史 Flow 返回两条 Track 的 V2 ConditionSnapshot、旧 V1 纯字段快照兼容、后续映射版本变化不重算、不同 Flow 不混入
- [x] T039 [P] [US4] 在 `webui/src/views/__tests__/DiscoverySearchPackages.spec.ts` 补红测：version 1 配置恢复为空白条件、version 2 恢复原始平台微调和专属字段且不触发映射覆盖
- [x] T040 [P] [US4] 在 `webui/src/views/__tests__/DiscoveryView.spec.ts` 写红测：失败/暂停/完成组合保持兄弟运行线和已解锁入口，结束后新单平台/新全部流程不继承旧结果或通知签名

### Implementation for User Story 4

- [x] T041 [US4] 在 `webui/src/composables/useDiscoveryFlowPresentation.ts` 完成按 flow id 重置运行时记忆、刷新时解锁推导/有效页钳制、旧页停留恢复和失败部分结果可见判断
- [x] T042 [US4] 在 `webui/src/composables/useDiscoveryParallelFlow.ts` 完成 V1/V2 快照恢复分支与新 Flow 清理，确保恢复只采用冻结 `platformValues` 且不执行当前映射
- [x] T043 [US4] 在 `webui/src/views/DiscoveryView.vue` 将 workflow 恢复顺序接到 Flow 初始水合之后的无跳转校正，并保持历史模式仍由既有入口控制
- [x] T044 [US4] 运行 `uv run python -m unittest tests.test_b096_flow_history`，并在 `webui/` 运行 `npm test -- DiscoveryRecovery.spec.ts DiscoverySearchPackages.spec.ts DiscoveryView.spec.ts useDiscoveryFlowPresentation.spec.ts useDiscoveryParallelFlow.spec.ts`，只修复 US4 及直接相邻失败

**Checkpoint**: 四个用户故事均具备受控自动化证据，进入统一收口。

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 文档同步、尺寸/零引用检查、整链自动化与真实应用门禁。

- [x] T045 [P] 在 `README.md` 更新“全部”六类映射、一次启动、分轨合流和原地结果加入说明；在 `CHANGELOG.md` 按用户可感知口径新增 3–6 条未发布记录 —— README 半边已落（提交 `4fe49ae`，四条：六类映射 / 一次启动 / 分轨合流 / 原地加入）。CHANGELOG 半边按用户 2026-09-30 定案「同一未发布版本内的返修不算用户可感知新增，发布时统一攒」不追加，且本轮不授权发布；V2 未收口（见 D-13），此时写"分轨合流已可用"会名实不符。
- [x] T046 在 `tests/test_b096_final_review.py` 完成最终结构审计：新模块存在、旧并行假进度零引用、确认状态零引用、`pre-v2-protected.sha256` 全部哈希一致、允许路径和冻结清单之外零新增差异、`DiscoveryView.vue` 行数门禁通过 —— 五项绿，一项**结构/授权阻断**：基线哈希那条（`test_baseline_files_keep_pre_v2_hashes`）在 HEAD 不可能绿，基线 76 项取自当时未提交的工作树却与实现同提交入库，对 `e4ca9e0~1` 实测 76 项全不符；按交接口径一字未动。边界闸基准本轮改严为「`54164e6..HEAD` 提交区间 ∪ 工作树」，检出 0。
- [x] T047 运行后端聚焦组合 `uv run python -m unittest tests.test_b096_flow_api tests.test_b096_flow_history tests.test_b096_final_review tests.test_search_packages tests.webui_store.test_store_migrations`，保存失败清单并只处理直接相关失败 —— 收口后实测 `Ran 196 tests … FAILED (failures=1)`，唯一失败是结构门禁 `test_baseline_files_keep_pre_v2_hashes`（36 项基线哈希，见 T046 的授权阻断），无其它失败。
- [x] T048 在 `webui/` 运行前端聚焦组合 `npm test -- parallelFilterMapping.spec.ts OneClickScreenDialog.spec.ts ParallelPlatformProgress.spec.ts useDiscoveryParallelFlow.spec.ts useDiscoveryFlowPresentation.spec.ts useDiscoveryIslandBridge.spec.ts useDiscoveryResults.spec.ts useSearchPackages.spec.ts useTheme.spec.ts DiscoveryRecovery.spec.ts DiscoverySearchPackages.spec.ts DiscoveryView.spec.ts` —— 以全量前端超集执行：69 文件 / 1511 例全绿（`%TEMP%\cs046_v2\acc_npmtest.log` 与收口后的复跑）。
- [x] T049 运行唯一一次干净后端全量 `uv run python -m unittest discover -s tests`；若失败，把失败清单写入 `specs/046-parallel-platform-flow/v2/tasks.md` 的实施记录，禁止无相关修改重跑全量 —— 收敛后跑一次（进程 19:29 起、19:55 结束）：**`Ran 3496 tests in 1555.660s` → `FAILED (failures=1)`**，失败清单只有一条：`FAIL: test_baseline_files_keep_pre_v2_hashes (test_b096_final_review.B096V2StructuralGuardTests)` = 结构/授权阻断（基线哈希，见 T046），无产品失败。日志 `%TEMP%\cs046_v2\be-full-final.log`。
- [x] T050 在 `webui/` 依次运行 `npm test` 与 `npm run build`，记录文件数、测试数、构建模块数和任何失败边界到 `specs/046-parallel-platform-flow/v2/tasks.md` —— 收口后前端全量 **69 文件 / 1518 例全绿**（D-10 与 D-13 各自的红测已计入），`npx vue-tsc --noEmit` 退出码 **0**，`npm run build` 退出码 **0**（产物 `dist/assets/index-D05zNV-p.js` 651.09 kB / gzip 208.04 kB），构建是最后一条前端命令。日志：`%TEMP%\cs046_v2\be-full-final.log`（后端全量）、`acc_npmtest.log`（前端）。
- [x] T051 运行 `uv run python -m unittest tests.test_repo_hygiene`、`git diff --check`、`git status --short`，确认无临时输出、凭据、意外文件和越界修改 —— `tests.test_repo_hygiene` **14 例全绿**；`git diff --check` 退出码 0（只有 CRLF 归一化提示，无空白错）；`git status --short` 只剩本文件一条待提交改动，**无未跟踪文件、无临时产物、无凭据**。
- [x] T052 按 `specs/046-parallel-platform-flow/v2/quickstart.md` 经正式入口、正式 live 数据库和真实 BOSS/智联登录态执行完整源码 E2E，记录 Flow/run id、实际完成顺序、02→03→04、旧页停留、结果合流和刷新证据，不记录敏感值 —— 已跑通一次真实轮：Flow `43e7c6adfc054004`，`selection=all`，15:20:59 一键启动 → 两条 `POST /api/execute-search`；**智联 15:25:42 先收尾**（抓 378 / 精筛 11：1 保留 10 淘汰，`result_run_id=c50be9bb-4474-442e-9f9f-c38c8ce90483`），**BOSS 15:40:44 后收尾**（抓 671 / 精筛 65：14 保留 51 淘汰，`result_run_id=4b4d086b-0992-4c10-8070-041182865559`）；收尾后 04 合并 匹配 15 / 不匹配 61 / 待确认 0 / 已筛除 973，全程控制台 0 error 0 warning；进行中三次全新浏览器上下文均自动接回现场。**未完成部分**：进行中那 15 分钟 04 看不到已交付平台的结果、D-08 说明未渲染 → 登记为 D-13 返修（本文件 1.3 与最终报告）。
- [x] T053 按项目现有本地打包流程构建 EXE（不改 `packaging/**`、不发布、不上传），并依 `quickstart.md` 执行“全部”主流程最小充分真实验证；未能执行时明确记录环境阻断 —— **环境阻断，未执行且未用源码运行冒充**：`pyinstaller` 不在 `pyproject.toml`/`uv.lock`/`requirements.txt`，当前 venv `import PyInstaller` 直接 `ModuleNotFoundError`，`packaging/build_exe.ps1:60-62` 会以"打包依赖缺失"终止。SC-009 因此标未验证。
- [x] T054 在 `specs/046-parallel-platform-flow/INDEX.md` 与 `specs/046-parallel-platform-flow/v2/tasks.md` 汇总已完成、测试证据、真实 E2E/EXE 状态、未验证边界和延期项；只有 T049–T053 的必需证据齐备才把 V2 标为完成 —— 已汇总（本条 + INDEX.md 2026-10-01 条目 + 最终报告 `%TEMP%\career-scout-spec046-v2-final-report.md`）。**V2 不标"完成"**：T053（EXE 真实一致性）为环境阻断未执行、D-11/D-12/D-10b 与失真夹具仍开着，按"已完成实现但有明确未验证边界与延期项"如实记录。

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 无依赖，先建立结构红线。
- Phase 2 依赖 Phase 1，阻塞所有用户故事。
- US1 依赖 Phase 2，提供条件快照与一次启动。
- US2 依赖 Phase 2，并在接入 `DiscoveryView.vue` 时依赖 US1 的统一入口接线已稳定。
- US3 依赖 US2 的 Flow presentation 和页面 04 解锁。
- US4 依赖 US1 的快照与 US2/US3 的页面投影、结果签名。
- Phase 7 依赖四个故事全部完成。

### User Story Dependencies

```plain
Foundation
   - US1 条件与启动
   - US2 分轨页面 - US3 结果合流
          ________________
US1 条件快照 ____________________  - US4 刷新与历史
```

### Parallel Opportunities

- T003 与 T005 可并行：分别修改 Python 迁移测试与前端纯函数测试。
- 同一故事内标 `[P]` 的红测文件互不重叠，可并行编写。
- T045 文档可在最终结构审计前并行完成。
- 逻辑上的 `[P]` 不自动授权多 AI；若用户后续明确要求多 AI，必须按项目规则使用独立分支、独立工作目录和唯一负责人。

---

## Parallel Examples

### US1

```plain
并行红测：OneClickScreenDialog.spec.ts / useDiscoveryParallelFlow.spec.ts / useTheme.spec.ts / test_b096_flow_api.py / test_search_packages.py
串行实现：parallelFilterMapping → Dialog/ParallelFlow → flow_api/theme → search package → DiscoveryView
```

### US2

```plain
并行红测：useDiscoveryFlowPresentation.spec.ts / ParallelPlatformProgress.spec.ts / DiscoveryView.spec.ts
串行实现：FlowPresentation → ParallelPlatformProgress → IslandBridge → DiscoveryView
```

### US3

```plain
并行红测：useDiscoveryResults.spec.ts / FlowPresentation 结果签名测试 / DiscoveryView 现场测试
串行实现：useDiscoveryResults → FlowPresentation 通知 → DiscoveryView 接线
```

### US4

```plain
并行红测：DiscoveryRecovery.spec.ts / test_b096_flow_history.py / DiscoverySearchPackages.spec.ts
串行实现：FlowPresentation 水合 → ParallelFlow 快照兼容 → DiscoveryView 恢复顺序
```

---

## Implementation Strategy

### 最小可演示切片

US1 是最小可独立演示切片：能证明统一条件、无确认、主题和快照正确。但它不是可发布 MVP；用户最高目标要求 US1–US4 全部完成。

### 增量顺序

1. 结构护栏 + 共享契约。
2. US1：先修条件与启动入口。
3. US2：再修 02→03→04 分轨推进和原进度。
4. US3：接通第二平台结果原地合流。
5. US4：完成刷新、返回、历史与跨轮隔离。
6. 统一自动化门禁 → 真实源码 E2E → EXE 最小验证。

### 完成标准

- 54 个任务均有客观证据或明确环境阻断。
- 所有实现任务只触碰允许文件。
- 聚焦测试、一次最终全量、前端构建和卫生检查有修改后证据。
- 正式源码主流程真实跑通；EXE 真实验证完成后才可声称双形态可用。

最新正式组合观察：Flow `8369b7905d6b4a6f`；应用默认 BOSS 浏览器入口 HTTP 409，提示 CDP 端口被非采集账号 Chrome 占用。BOSS `9a7a95ace1294d66a1c1e72bc3446ff5` 暂停 source_cdp_unavailable；智联继续独立运行。智联 AI `a68b0010031f4ddcb496953560c747cd` 真实批次暂停 HTTP 200、mode=graceful、expected_run_id 对应本实例，实际 paused；继续 HTTP 200 回 running。BOSS 经既有结束保存入口 HTTP 200 后 stopped、无结果；智联 done、结果 `99ecc31b-149e-4387-a47e-586ea0325713`，04 为 15/20/0/14，Flow stopped。未关闭占用端口的外部 Chrome，未修改正式数据修环境。BOSS 非空完成、双非空结果及两个先后顺序仍被环境阻断，T055 保持未勾选；此轮不冒充完整 SC-008。

额度中断后的核实：cold-final 副本走查误启动新 Flow `41e361bb0444408e`（197 JD），不能计为既有 Flow 冷启动恢复通过。只读查库确认正式库 latest 仍 `8369b7905d6b4a6f`、env=live，误启动仅在 env=test 副本；恢复会话时已无该副本服务或浏览器存活。该旧副本不再启动。重新只读备份为 cold-resume，仅副本 env=test，测试范围收紧为读取既有 Flow/步骤导航/导出/最后新轮，不允许任何抓取、AI 或新 Flow 启动；测试观察者只报告事实。此前全量后产品代码未再修改，不重复全量。

## Phase 8: Convergence（2026-10-02，交验未完成项）

本次按当前 V2 工件与修改后证据收敛，不重跑历史 T001–T054。缺少证据的真实门禁保持未勾选；下面不授予发布、提交、安装依赖或正式数据修补权限。

- [x] T055 [US2/US3/US4，本轮源码证据] 正式网页完成SC-008双轨主流程，两轨非空、两个完成顺序、单轨保存兄弟继续、批次立即/等本批完成、合流一次通知/阅读现场、返回与刷新取证；详见R017/R018及最终记录。BOSS旧环境阻断已经实际解除，不用旧历史或假件计通过。此勾选只表示本轮AI测试证据齐备，用户验收尚未执行，EXE排除，后端全量两项门禁仍未绿。
- [x] T056 [US4] 使用包含已进入 AI/已完成真实 Flow 的正式库只读副本，在独立端口和全新浏览器补齐冷启动硬走查：01→02→03 七类条件→04 四桶/导出→新轮 01 可提交，逐屏截图且控制台零错误零警告。最新cold-guard只读备份只改env=test，完成单轨Flow `7d97e27586a94fa0`及AI run已在副本，全部上述页面走查及最新构建返回/刷新取证。单轨副本门禁通过，不替代T055的正式双平台合流、完成顺序或T057的EXE。
- [ ] T057 [SC-009，已排除本轮] 用户最新明确 EXE 不需要验证，当前只管源码；保留历史未验证记录，不构建、不计作本轮阻断，不用源码服务宣称 EXE 可用。

本轮源码最终记录：正式Flow `79a587418bde44dc`，智联先结果，BOSS暂停/继续均HTTP200；智联首次结果使03自动到04属于正常领先推进。随后真实手动点击顶栏03，恢复BOSS前后均03，BOSS完成后仍03，最后由测试者手动点04；未点击结果通知，不抢页证据明确，原先“03期间自动04”含混描述已更正，未据此改坏导航逻辑。最终BOSS12/53/0/25、智联17/18/0/14，合并29/71/0/39且两方来源，刷新保持；新轮01三入口enabled，has_task=false，控制台0错误0警告。截图 `active-03-boss-resumed.png`、`active-03-both-complete.png`、`active-04-after-refresh.png`、`active-01-new-round-after-final.png`（TEMP）。此前C轮BOSS先、智联后阅读现场与第二轮反向完成均有证据，未因本轮暂停较晚而伪称“智联仍运行时停02”已单独实测；02刷新/返回与后台导航公共守卫由直接回归和其它正式取证覆盖。

最终验证边界：源码主流程与最新双轨副本冷启动通过本轮上述走查，EXE用户排除；前端70文件1548条/构建/3项范围尺寸/git diff --check通过。后端唯一一次最终全量3506条仍失败2项（保护哈希36项历史不符、4个必要新增文件未提交），完整日志 `backend-after-guard-final.log` 保留；最新卫生14条仅未跟踪文件1项失败，`refresh-hygiene.log`。禁止用新基线、忽略、削弱断言或提交伪造全绿。未合并main、未提交、未推送、未发布，未迁移修补正式库；正式源码5000服务留供用户验收，自己的测试浏览器与cold-source-final副本进程已结束。此前清理临时产物被自动审批拒绝，必要证据/副本仅留TEMP，不入仓库。用户验收待执行，不把本轮AI走查称用户已验收。

非阻断观察：恢复完成的个别公开Track仍带历史user_paused原因，但界面和当前能力按完整成功终态显示；未据此扩大后台/存储修改。第一次合流选中项变化无法完整复原当时操作，后续两个顺序均明确固定选中对象与滚动后合流保持；未凭含混记录增加样式或结果规则。

最新正式双轨启动：应用 BOSS 浏览器入口 HTTP 200；01/02 全部，AI开发助理/东莞/每组合一页，经原有条件弹窗最终按钮一次提交，`POST /api/flows` 201、两个既有搜索接口均200。Flow `9f66959a829d4936`，抓取 run `4ceb813c48d544af81ec74a57d89b947`、`dd9b089b5d4f452988125472f8e3397f`；两卡均运行中，控制台0错误0警告。此前只点入口、未点弹窗最终按钮的“无搜索请求”是观察步骤不足，不是已确认产品缺陷。正式两轨执行/合流仍等待结果，T055 不提前勾选。

保留且未扩大本轮修复：历史保护哈希门禁与新增文件未提交的卫生阻断；只读发现旧搜索投影 queued 的一致性线索；历史 D-11/D-12/D-10b 与夹具线索。后续先核实再按范围处理，不自动写正式数据、改哈希或新增重构。
