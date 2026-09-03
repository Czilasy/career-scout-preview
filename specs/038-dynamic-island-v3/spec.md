# 038 灵动岛 v3 · live 仪表盘 + 转盘轮播 + toast 折叠 · Spec

**Feature Branch**: `038-dynamic-island-v3`
**Created**: 2026-09-03
**Input**: 用户确认（基于 037 交付后的体验反馈："我没看到你说的灵动岛有多好啊。作为用户，一个最显眼的体验就是那种小浮窗的提示不应该也要集成到里面去吗？" + 中间态/转盘方向口头反馈 + 四轮质询答复）

**前置**：037（灵动岛 v2 + 提醒角标回退）已交付（707 测试通过、build 通过、卫生 14/14）。037 把灵动岛建成"终态通知中心骨架"——但只接住胶囊状态跃迁的终态事件（completed/error/paused），running 态被 `clearAll()` 主动丢弃，现有浮窗 toast（TaskCompletedToast/NoticeBar）未整合。038 在 037 骨架上把灵动岛从"终态通知中心"升级为"live 仪表盘 + 转盘轮播"。

## 最高目标（用户原话级，验收/任务的唯一衡量尺）

- **岛要展示中间态，不只展示结果**：running 态实时显示"正在抓取 12/30""AI精筛 3/30""粗筛剔除 5"这类进行中信息；completed 态四色分类（匹配/不匹配/待确认/已筛除），补齐 037 只展示匹配+待确认漏掉的两类。数据已存在于状态机（`roundStatus.capsule.progress`、`TaskSnapshot` 的 kept/dropped/pending/success/fail 计数），037 的 `useIslandNotices` L93 主动丢弃 running 数据——038 改为 live 驱动。
- **岛是"弹弹的"实时仪表盘**：pill 宽度随内容 spring 弹性伸缩（"弹弹的感觉"），数字跳动有弹性，颜色多姿多彩（蓝=抓取/紫=精筛/绿=匹配/灰=不匹配/琥珀=待确认/红=已筛除+报错红光）。字数长短无所谓，因为岛会弹大弹小。
- **转盘轮播优雅处理打断**：主流程（live state）永远 pinned 在 lane 0；打断消息到达，pill 垂直转一次展示这条、转完回主流程，那条不再在 pill 重复展示。多条积压只转最新一条，余数沉入 panel 未读、角标提示。主流程才是 pill 的主角。
- **报错/暂停是 live state 红光**：任务暂停/停止后整个岛体隐隐闪光泛红（有设计感）；任务恢复/修好自然褪红，回到正常态。不进打断队列、不需手动 dismiss。现有报错 UI（进度条、红字）一个不动，灵动岛是叠加层。
- **toast 折叠**：TaskCompletedToast（035）删除，completed pill + 点击 navigate 接管"完成→查看最新"功能；NoticeBar 的 warning/error notice 折进打断队列，info/success 保留 NoticeBar 行内条。
- **硬不变式**：轮播只切显示位，永不冻结底层数据流——打断展示期间主流程数据照常由 `roundStatus` 驱动更新，转回来用户直接看到新数字。

## User Scenarios & Testing

### US-1 — running 态 live 仪表盘（中间态展示）
任务跑起来后，灵动岛 pill 不再"清空什么都不展示"，而是实时显示当前阶段进度：
- 抓取阶段（phase=scraping，蓝色系）：`正在抓取 12/30`；可叠加 `JD详情 8/15 · 失败 2 · 未启 0`；`翻页冷却 10s`/`详情批次 15/批` 等进行中信息。
- AI精筛阶段（phase=screening，紫色系）：`AI精筛 3/30`；`粗筛剔除 5`；`已判定 8`；`重试 1次`；`待确认 3`。
- 数字跳动有弹性（旧值上滑淡出、新值下滑淡入 + "啵"一下放大回弹）。
- pill 宽度随内容增长 spring 弹性变宽（"弹弹的"）。

**Acceptance**:
- 模拟 `roundStatus.capsule` 由 idle → running(progress:{phase:scraping, done:5, total:30})，pill 文字从默认态切到 `正在抓取 5/30`，颜色切蓝系，宽度弹大。
- done 由 5 → 6：旧 `5` 上滑淡出、新 `6` 下滑淡入 + 弹动；pill 宽度微弹。
- phase 由 scraping → screening：颜色蓝 → 紫，文字切 `AI精筛 N/30`。
- reduce-motion 下全部退化为瞬时切换。
- 数据来源零新增接口：全部读 `roundStatus.capsule.progress`（036 已构建，见 `useDiscoveryState.ts` L792-801）+ `TaskSnapshot` 字段（L1144-1169）。

### US-2 — completed 态四色分类
任务跑完，pill completed 态展示四色芯片（不是 037 的两色文字）：
- `匹配 5`（绿）、`不匹配 18`（灰）、`待确认 3`（琥珀）、`已筛除 2`（红）——四类并排芯片，补齐 037 漏的"不匹配"+"已筛除"。
- 芯片与结果页 `resultTabs`（`useDiscoveryState.ts` L687-699）四类口径一致。
- 点击 pill（completed + 无未读）navigate 到结果页（接管 TaskCompletedToast 的"完成→查看最新"）。

**Acceptance**:
- 模拟 running → completed(judged)，pill 渲染 4 个芯片，颜色绿/灰/琥珀/红，计数与 `resultCountsFromPipeline` + `groups` 一致。
- 037 的 `completedDetail` 只返回 `匹配 N · 待确认 P`——038 扩展为四元组。

### US-3 — 转盘轮播优雅处理打断
主流程在 pill 上展示时（running/completed/idle），一条打断消息（投递提醒/NoticeBar warning/error）到达：
1. pill 垂直转一次——主流程文字往上转开、打断消息从下方转入展示位（spring 弹性，有"弹"的 overshoot）。
2. 展示 ~2.2s 后，carousel 反向转回——打断消息往上转出、主流程从上方落回显示位。
3. 该打断消息不再在 pill 重复展示；它沉入 notice panel 作为未读条目。
4. pill 角标（未读数）+1。
5. 多条积压：只转最新一条一次；余数只进 panel、不轮播；角标 = panel 未读数。

**Acceptance**:
- 主流程 `正在抓取 12/30` 展示中；推一条打断 `{title:"投递提醒", detail:"3条逾期"}`：carousel translateY 弹性变化，打断消息进入视窗、主流程转出；2.2s 后转回。
- 转回后主流程显示 `正在抓取 13/30`（**数字已推进**，证明打断期间数据流没冻结——硬不变式）。
- 连推 3 条打断：只最新一条转一次展示，转回后角标=3（panel 有 3 条未读）；不自动轮播第 2、3 条。
- 点角标/pill 展开 panel：3 条打断都列出，未读高亮。
- reduce-motion 下 carousel 退化为瞬时切换（无 translateY 动画）。

### US-4 — 报错/暂停红光 live state
任务出错/暂停，pill 整体进入"隐隐闪光红"态（不是进打断队列，是胶囊 live state 的视觉层）：
- 岛体背景/边框泛红光（subtle glow，有设计感，不是纯红填充）。
- 期间打断消息仍可轮转展示（红光态也 pinned，打断转完回红光）。
- 任务恢复/修好（capsule 离开 attention）：红光自然褪去，回到正常态（running 蓝/紫或 completed 四色）。
- 不需手动 dismiss——红光绑定任务状态，任务恢复即褪。

**Acceptance**:
- 模拟 running → attention(kind:error)，pill 背景出现红光层（测试断点：含 `data-glow="error"` 或红光 class）。
- 模拟 attention → running：红光层移除，回到蓝/紫进度态。
- 红光期间推一条打断：carousel 仍转一次展示打断、转回红光态（红光不被打断覆盖）。
- 现有报错 UI（DiscoveryView 进度条/红字/暂停条）**不动**——断言这些组件渲染不受 038 影响。

### US-5 — TaskCompletedToast 折叠删除（路径 3，已授权）
TaskCompletedToast.vue（035 遗留）的"完成→点击查看最新"功能由灵动岛 completed pill + navigate 语义性接管；toast 组件及其渲染一并删除（用户授权红线例外）：
- 任务跑完不再弹独立浮窗 toast；pill completed 四色芯片就是"完成"信号，点击 navigate 到结果页（等价原 toast 的"点此查看最新结果"）。
- 删除：DiscoveryView.vue 删 L15 import + L192 解构项 + L1118-1122 元素（净 -7 行）；`webui/src/components/TaskCompletedToast.vue` + `webui/src/components/__tests__/TaskCompletedToast.spec.ts` 删除。
- `taskCompletedToast` ref 留在 `useDiscoveryState.ts`（禁改），删元素后偶尔被设 true 但无消费者，无害空写。

**Acceptance**:
- 岛 completed 态是"完成→查看最新"的主路径：点击 completed pill（无未读打断）触发 navigate("results")，跳到结果页。
- DiscoveryView.vue 无 TaskCompletedToast import（L15）/解构项（L192）/模板元素（L1118-1122）；`data-testid="task-completed-toast"` 元素不再出现。
- TaskCompletedToast.vue + TaskCompletedToast.spec.ts 文件不存在。
- `useDiscoveryState.ts` 的 `taskCompletedToast` ref 仍在（禁改），但 App/DiscoveryView 不再渲染其对应 toast。

### US-6 — NoticeBar 部分折叠
NoticeBar（App.vue 行内提示条）的 warning/error notice 折进灵动岛打断队列（轮转展示一次）；info/success notice 保留 NoticeBar 行内条（如"已保存""导出完成"这类操作反馈不抢灵动岛）：
- NoticeBar 组件保留，但 notice 的 warning/error 分流到 island 打断队列。
- info/success 仍在原位行内展示。

**Acceptance**:
- 推一条 `{tone:"warning", message:"..."}` notice：进入 island 打断队列（carousel 转一次），NoticeBar 不再行内展示 warning。
- 推一条 `{tone:"success", message:"已保存"}`：NoticeBar 行内展示，island 不轮转。
- 推一条 `{tone:"error", message:"..."}`：进入 island 打断队列 + pill 触发红光（若任务同时进入 attention）。

### 边角
- 跨 profile 切换：carousel 重置（打断队列清空、panel 清空、回到 idle lane 0）；沿用 037 的 `reset()` 调用点。
- 浏览历史轮次（scope=history）：pill 展示历史轮的 completed 摘要（四色），打断队列暂停消费（历史浏览期间不轮转展示新打断，顺延到回到最新）；沿用 037 复审 N4 边界。
- 窄屏（≤760px）：pill 宽度有上限（不超屏宽 -32px），内容超出截断或省略；carousel 仍垂直转。
- kaleido 主题：红光/四色芯片在半透明毛玻璃下保证可见（沿用 036/037 特判）。

## Requirements

### Functional

- **FR-001** pill 主体 MUST 绑定 `roundStatus.capsule` 的 live state：running 态实时显示 `progress.done/total` + phase 颜色（蓝=scraping/紫=screening），不再 `clearAll()` 丢弃 running 数据（修 `useIslandNotices.ts` L93）。数据零新增接口，全部读 036 已构建的 `roundStatusPayload`（`useDiscoveryState.ts` L792-801）。
- **FR-002** completed 态 MUST 展示四色芯片：匹配(绿 `c-green`)/不匹配(灰 `c-gray`)/待确认(琥珀 `c-amber`)/已筛除(红 `c-red`)。补齐 037 `completedDetail`（L43-47）只返回匹配+待确认两元的漏。计数与 `resultCountsFromPipeline` + `groups`（L1254-1268, L674）同源。
- **FR-003** pill 内部 MUST 实现垂直 spring carousel：lane 0 = 当前 live state（pinned，打断转完回这里）；lane 1+ = 打断队列 FIFO。`translateY` 用 motion-v spring 过渡（`type:"spring", stiffness:300, damping:26`，有 overshoot 才"弹"）。reduce-motion 退化为瞬时切换（translateY 直接设值无过渡）。
- **FR-004** 打断到达 MUST 垂直转一次：carousel translateY 弹性上移一格 → 展示打断 → ~2.2s 后弹回 lane 0。该打断条目转完沉入 notice panel 作未读，不在 pill 重复展示。打断来源：投递提醒（useReminderBadge 触发）+ NoticeBar warning/error notice。
- **FR-005** 多条积压 MUST 只转最新一条一次：余数只进 panel 未读、不轮播；pill 角标 = panel 未读数（沿用 037 unreadCount）。点角标/pill 展开 panel 看全部。**不自动轮播**（用户明确："只转动第一次，转完回到主线程，不再展示，左上角/岛内小图标提示未查看"）。
- **FR-006** attention(error/paused) MUST 是 live state 红光层（非打断队列项）：岛体背景/边框泛隐隐闪光红（subtle glow，有设计感）。任务离开 attention 态（capsule 切走）红光自动褪去，不需手动 dismiss。红光期间打断仍可轮转（转完回红光态）。
- **FR-007** pill 宽度 MUST 随内容 spring 弹性伸缩（"弹弹的"）：宽度跟随文字长度+芯片数自适应，motion-v spring 过渡（非 linear）。reduce-motion 退化为瞬时宽度切换。
- **FR-008** TaskCompletedToast 的"完成→查看最新"功能 MUST 由灵动岛 completed pill + navigate 语义性接管（点击 completed pill 无未读打断时 navigate("results")）。**toast 删除走路径 3（用户已授权红线例外）**：DiscoveryView.vue 删 L15 import + L192 解构项 + L1118-1122 元素（净 -7 行）；TaskCompletedToast.vue + 其 spec 文件删除。`useDiscoveryState.ts` 的 `taskCompletedToast` ref 保留不动（禁改，删元素后无害空写）。
- **FR-009** NoticeBar warning/error notice MUST 分流到 island 打断队列；info/success 保留 NoticeBar 行内条。NoticeBar 组件保留。
- **FR-010** 现有报错 UI（DiscoveryView 进度条/红字/暂停条、loginGuide 等）MUST 不动——灵动岛是叠加层，不替换/移除现有报错展示。038 只新增 island 侧的红光+打断，不改 DiscoveryView 任何渲染。
- **FR-011**（硬不变式）carousel MUST 只切显示位，永不冻结底层数据流：`roundStatus` 照常驱动 lane 0（main flow）数据更新；打断展示期间 done/total 照常推进；转回主流程时用户直接看到新数字。任何实现不得在打断期间暂停/缓存 mainLane 的数据订阅。
- **FR-012** 全部动画在 reduce-motion 下退化为瞬时（`useReducedMotion()` 短路）：carousel translateY 直接设值无过渡、pill 宽度瞬时切换、红光瞬时出现/消失、数字瞬时换。
- **FR-013** 保留 037 已交付的：panel 展开/dismiss snapshot（带 id 快照）/互斥（collapseIsland + 三抽屉）/tab trap/Teleport backdrop/navigate 语义/all-read 直接 navigate/aria-live announce。038 是叠加增强，不回退 037 行为。

### Non-Functional / 架构

- **架构边界（AGENTS + 宪法）**：
  - 允许修改：`webui/src/App.vue`（净减：删 TaskCompletedToast 接线 + NoticeBar warning/error 分流）、`webui/src/components/DynamicIsland.vue`（重写为 carousel + live state + 四色 + 红光）、`webui/src/composables/useIslandNotices.ts`（改 running 不 clearAll、扩展 completed 四元）、`webui/src/components/IslandNoticePanel.vue`（如需适配四色/打断来源）、各测试文件、`webui/src/test/setup.ts`（如需 carousel/WAAPI 测试桩增强）。
  - **红线例外（路径 3，用户授权）**：`webui/src/views/DiscoveryView.vue` 仅删 toast 相关 7 行（L15 import + L192 解构项 + L1118-1122 元素，净 -7 行），不动其余任何逻辑。此为原则 IV 红线例外，仅限本批次、仅限这 7 行删除；不延伸到 038 之外。
  - 新增：`webui/src/composables/useIslandCarousel.ts`（carousel 状态机：lanes/activeLaneIndex/badge/pushInterrupt/dismissActive）。
  - 删除：`webui/src/components/TaskCompletedToast.vue`、`webui/src/components/__tests__/TaskCompletedToast.spec.ts`。
  - 禁止修改：`webui/src/composables/useDiscoveryState.ts`（1267 行 TS，036 派生保持不变；`taskCompletedToast` ref 保留但不消费渲染 toast）、任何后端文件（`webui/*.py`、`scripts/`）、`webui/src/jobFeedback.ts` 等 API 客户端、`webui/src/components/ReminderDrawer.vue`、`webui/src/api.ts`。
  - 禁改新增接口：不改动任何后端 endpoint。
  - 新文件 MUST 在同一批次登记 `.specify/memory/constitution.md`「模块地图」（原则 VI）。
- **行数门禁（宪法 II 硬线）**：Python ≤ 800、Vue ≤ 1200、TS 无硬线但自设轻量。DynamicIsland.vue 037 终态 656 行，038 重写后 carousel+live+四色+红光增量——MUST 不破 1200；useIslandCarousel.ts 自设 ≤ 250。
- **零新接口**：038 全部数据来自 036 已上抛的 `roundStatus`（含 `capsule.progress`、`TaskSnapshot` 字段）+ 现有 `useReminderBadge` + 现有 NoticeBar notice 流。不新增/不改任何后端 API。

### Key Entities
- **IslandLiveState**（派生自 `roundStatus.capsule`，pill lane 0 内容）：`{ phase: "scraping"|"screening", done, total?, counts?: {matched, unmatched, uncertain, dropped}, glow?: "error"|"paused"|"none" }`
- **IslandLane**（carousel 一格）：`{ id, type: "main"|"interrupt", content: IslandLiveState | {title, detail, tone}, priority, sticky?: boolean, duration?: number }`
- **useIslandCarousel API**：`{ activeLaneIndex, lanes, badgeCount, pushInterrupt(lane), dismissActive(), reset(), mainLaneState }` —— mainLaneState 绑定 roundStatus 实时更新（不冻结）
- **IslandNotice**（沿用 037，扩展）：`kind` 增加 `"interrupt"`；`detail` 支持四元 `{matched, unmatched, uncertain, dropped}`

## Success Criteria

- **SC-001** running 态 pill 展示 live 进度（`正在抓取 N/M`），done 由 N→N+1 时旧值上滑淡出、新值下滑淡入 + 弹动；pill 宽度 spring 弹大。
- **SC-002** completed 态 pill 四色芯片齐全（匹配/不匹配/待确认/已筛除），颜色绿/灰/琥珀/红，计数与结果页 `resultTabs` 一致。
- **SC-003** 打断到达：carousel 垂直转一次（translateY spring 弹性），展示打断 2.2s，转回主流程；转回后主流程数字已推进（硬不变式验证）。
- **SC-004** 多条积压只转最新一条一次，余数进 panel 未读，角标=未读数；不自动轮播。
- **SC-005** attention 态 pill 红光层出现（`data-glow="error"|"paused"`）；离开 attention 红光褪去；红光期间打断仍可轮转。
- **SC-006** DiscoveryView.vue 无 TaskCompletedToast import/解构/渲染（净 -7 行）；TaskCompletedToast.vue + spec 文件不存在；任务跑完无 `data-testid="task-completed-toast"` 元素；岛 completed pill 点击 navigate("results") 等价原 toast"查看最新"。
- **SC-007** NoticeBar warning/error 进 island 打断队列；info/success 留 NoticeBar 行内。
- **SC-008** 现有报错 UI（DiscoveryView 进度条/红字）渲染不受 038 影响（断言未改）。
- **SC-009** reduce-motion 下 carousel/宽度/红光/数字全退化为瞬时。
- **SC-010** 验证门禁：聚焦测试 + 前端全量测试 + `npm run build` + 仓库卫生全部通过（宪法 V）。

## Verification Scope
- 聚焦测试：`useIslandCarousel.spec.ts`（新增）、`useIslandNotices.spec.ts`（改 running 不 clearAll + 四元 completed）、`DynamicIsland.spec.ts`（carousel 转一次/四色/红光/宽度弹/badge/转回数字推进）、`App.spec.ts`（toast 删除/navigate 接管/NoticeBar 分流）。
- 前端全量测试：vitest run。
- `npm run build`：vue-tsc 严格通过 + vite 构建成功。
- 仓库卫生：`uv run python -m unittest tests.test_repo_hygiene`。
- 后端不动；不跑后端全量。
- 用户端到端走查在交付后进行。

## Assumptions
- A1 不新增/不改任何后端接口；live 数据全部来自 036 已上抛的 `roundStatus.capsule`（含 progress）+ `TaskSnapshot` 字段。
- A2 `TaskSnapshot` 的 kept_count/dropped_count/pending_count/success_count/fail_count/unstarted_count 字段已由后端填充（见 `useDiscoveryState.ts` L1144-1169 接口定义 + `ai_screening.py` 的 `_emit_*` 事件）——038 前端直接读，不改后端。
- A3 打断来源当前只有投递提醒 + NoticeBar warning/error；未来扩展（其他外部消息）按同接口 pushInterrupt 进队列。
- A4 `useDiscoveryState.ts` 的 `taskCompletedToast` ref 保留不动（禁改红线 1267 行 TS）；DiscoveryView.vue 的 toast 渲染（L15+L192+L1118-1122）按路径 3 删除（用户已授权红线例外，净 -7 行）；删后 ref 偶尔被设 true 但无消费者，无害空写。
- A5 NoticeBar 组件保留；只改 App 侧 notice 分流逻辑（warning/error → island 队列）。
- A6 红光"隐隐闪光"用 CSS keyframes（subtle pulse，非刺眼纯红），reduce-motion 下静止。
- A7 转盘"弹"的 overshoot 由 motion-v spring 物理提供，不手写 keyframes overshoot。
- A8 037 已交付的 panel/dismiss/互斥/navigate/aria 行为全保留，038 不回退。

## TaskCompletedToast 删除路径决策（已定：路径 3，用户授权 2026-09-03）

TaskCompletedToast 渲染在 `DiscoveryView.vue`（1223 行超 Vue 1200 红线）；`taskCompletedToast` ref 在 `useDiscoveryState.ts`（1267 行 TS，亦禁改）。宪法原则 IV"重构/拆分不混入功能开发"使 038 功能 Spec 默认不能改 DiscoveryView。

**用户授权路径 3**：038 批次内对 DiscoveryView.vue 做红线例外，纯删除 toast 相关 7 行（净 -7，文件远离红线而非逼近），符合原则 II"超线文件应缩小"精神；原则 IV"不混"本意防功能 Spec 顺手把超线文件改花，此处纯删不改行为，风险可控。

**删除清单**（DiscoveryView.vue，共 7 行）：
- L15：`import TaskCompletedToast from "../components/TaskCompletedToast.vue";`
- L192：`  taskCompletedToast,`（多行解构中的一项）
- L1118-1122：`<TaskCompletedToast :visible="taskCompletedToast.visible" @click="taskCompletedToast.visible = false; void returnToLatest()" @close="taskCompletedToast.visible = false" />`

**保留不动**：
- `useDiscoveryState.ts` 的 `taskCompletedToast` ref（禁改 1267 行 TS）——删元素后它偶尔被设 true 但无消费者，属无害空写。
- `returnToLatest()` 方法（别处仍调用）。
- DiscoveryView 其他逻辑（仅删上述 7 行，不动其余）。

**文件**：`webui/src/components/TaskCompletedToast.vue` 删除；`webui/src/components/__tests__/TaskCompletedToast.spec.ts` 删除（组件已删，测试随之删）。

## Out of Scope
- 删除 `useDiscoveryState.ts` 的 `taskCompletedToast` ref（禁改红线，保留不动）。
- 新增打断来源类型（除投递提醒 + NoticeBar warning/error 外的新消息源）。
- 跨会话持久化打断队列/未读状态（会话级，同 037）。
- carousel 3D 真·转盘物理旋转（用 vertical spring carousel 近似实现，视觉等效）。
- 自定义 pill 颜色/字号设置项。
- 后端任何改动。

## 复审裁决与降级（实现期 · 2026-09-03）

复审在完整 Spec Kit 流程后的独立全量审查中发现若干规格与实现约束冲突，裁决如下（已在本批次落地，spec 原文保留作为设计意图快照）：

### P1-2 · FR-002/US-2/SC-002 四色 → 两色降级
- **裁决**：completed 态 pill 只展示 `匹配(绿) + 待确认(琥珀)` 两色芯片，**不**实现"不匹配(灰)/已筛除(红)"两色。
- **根因**：`useDiscoveryState.ts` 禁改（1267 行 TS，宪法红线）；capsule 仅上抛 `results: { matched, pending }` 两元，链路无四元数据源。原 FR-002"扩展 completedDetail 返回四元"在合规路径上不可能实现——`completedDetail` 的输入只有 `capsule.results`，扩不出 unmatched/dropped。
- **落地**：`useIslandNotices.completedDetail` 仍只返回 `匹配 N · 待确认 P`（保留 037 行为）；`DynamicIsland.vue` completed lane 只渲染 `c-green + c-amber` 两色 chip；`IslandNoticePanel.vue` 删除"四色 chip"渲染死代码（panel completed 行只显示 detail 文字）。
- **未来**：待后续 Spec 单独扩展 capsule 上抛 unmatched/dropped（需改 `useDiscoveryState.ts`，必须先拆分该文件），届时四色 chip 随 capsule 扩展一并加回。
- **影响文档**：CHANGELOG/README 已对齐两色表述；本 spec 原文 FR-002/US-2/SC-002 保留四色设计意图，作为未来扩展的目标。

### P2-1 · FR-008 vs FR-013 裁决：completed 终态通知 `read:true`
- **裁决**：completed 跃迁派生的终态通知初始 `read: true`（**不**计未读）。
- **根因**：FR-008"completed pill 点击直达 results"与 FR-013"unread-gating（有未读时点击展开 panel 而非 navigate）"冲突——若 completed 通知未读，pill 点击会被 unread-gating 拦截去展开 panel，无法直达 results。FR-013 的 unread-gating 是为了"用户没看过的告警先看"，但 completed 的"完成"信号已由 pill completed live state 彩色芯片实时展示（不依赖 panel 行），panel 的 completed 行只是历史记录、不算未读。
- **落地**：`useIslandNotices.processTransition` 中 completed upsert 设 `read: true`；error/paused/interrupt 保持 `read: false`（用户没看过的告警仍要角标提示）。
- **效果**：完成后 pill 不显示未读角标（unreadCount=0），点击直达 results（等价被删 TaskCompletedToast 的一键直达）；error/paused/interrupt 仍走 unread-gating → panel 流。
- **测试**：`useIslandNotices.spec.ts` completed 用例加 `read:true` + `unreadCount 0` 断言；`DynamicIsland.spec.ts` 新增 "P2-1 回归：completed + read:true notice → 点 pill 直达 results" 用例；`App.spec.ts` 新增 "038 P2-1: completed 通知 read:true，点 pill 直达结果页" 用例。

### P2-3 · FR-005 sticky 打断不滞留队列卡死角标
- **裁决**：展示位被 sticky 打断占据（等用户手动处理）时，新打断不抢位——非 sticky 只入队+timer；新 sticky 无 timer 也无展示机会，直接沉入 panel（未读），永不滞留队列卡死角标。
- **根因**：原 pushInterrupt 伪代码"多条积压只转最新一条"在 sticky 场景下有死角——若当前展示 sticky，新 sticky 入队后永远等不到展示机会，badgeCount 会一直包含它，无法消除。
- **落地**：`useIslandCarousel.pushInterrupt` 检查 active 是否 sticky：active sticky + new sticky → sink(new) 直接沉入 panel；active sticky + new 非 sticky → 只入队+timer；active 非 sticky → 切到 new（"切到最新"）。
- **"只转最新一条"语义澄清**：US-3"多条积压只转最新一条"指**被动响应新打断**——展示位永远切到最新 push 的，旧条靠各自 timer 沉入 panel；**不**是"自动轮播"——dismiss 后不补转下一条。原 plan 伪代码"activeLaneIndex = queue 末尾"本意即"切到最新"，实现按此语义落地。
- **测试**：`useIslandCarousel.spec.ts` 新增 3 条 sticky 测试（展示中 push 非 sticky 不抢位 / 展示中 push sticky 直接沉入 / 非 sticky 展示中 push sticky 抢位）；reset 测试断言更新（第二条 sticky 直接沉入 → badgeCount=1 而非 2）。

### P2-5 · 边角"history 暂停消费"落地
- **裁决**：`scope === "history"`（浏览历史轮）期间，新打断（warning/error notice + 投递提醒 0→N）暂停消费——经 `pushInterruptOrDefer` 攒入 `historyPendingInterrupts` 缓冲；scope 离开 history（回到 live）时 watch 逐条 flush（最后一条占据展示位"只转最新"，余数照常计时沉入 panel）。
- **根因**：原 spec 边角"history 期间打断队列暂停消费，顺延到回到最新"未明确"如何顺延"——若直接 drop，回 live 后用户错过；若直接 pushInterrupt，会打断 history 浏览。
- **落地**：`App.vue` 新增 `historyPendingInterrupts` 数组 + `pushInterruptOrDefer(lane)` 函数 + `watch(roundStatus.scope)` flush；`showNotice` 与 reminder watch 改调 `pushInterruptOrDefer`；profile reset 清空缓冲。
- **测试**：`App.spec.ts` 新增 "038 P2-5: history 浏览期间 notify 暂停消费，回最新时 flush" 用例。

### P2-8 · 打断行点击目标 "reminders" 分流
- **裁决**：投递提醒打断沉入 panel 后，行点击 target="reminders"——App 层 `handleIslandNavigate` 拦截：reminders → `toggleReminderDrawer()` 开提醒抽屉；其余 target（home/task/results/attention）→ `requestCapsuleNavigation`。
- **根因**：原 spec 未明确"投递提醒打断沉入后行点击去哪"——`requestCapsuleNavigation` 只认 home/task/results/attention（`CapsuleNavigationTarget`，`useDiscoveryState` 禁改），不认识 reminders。投递提醒的语义本就是"开提醒抽屉看逾期列表"，分流必须在 App 层做。
- **落地**：`useIslandNotices.IslandNoticeTarget` 增 "reminders"；`useIslandCarousel.IslandInterruptContent.target` 增 "reminders"；`DynamicIsland.vue` 的 `CapsuleTarget` 增 "reminders"；App reminder 0→N watch 的 pushInterrupt content.target 设 "reminders"；`App.vue` 删 `handleCapsuleNavigate`，新增 `handleIslandNavigate(target)` 分流。
- **测试**：`App.spec.ts` 新增 "038 P2-8: 投递提醒打断沉入 panel 后行点击直达 reminders（开提醒抽屉）" 用例。

### P1-3 / P2-4 · FR-007 pill 宽度 spring 实现方案
- **裁决**：放弃 ResizeObserver 镜像渲染（复杂 + setup 需加 RO 桩），改用 `watch([contentKey, unread], ..., { flush: "post" })` + `nextTick` + 读 `track.children[activeLaneIndex].offsetWidth` 测量展示位 lane 自然宽，零镜像、零新桩。
- **落地**：`DynamicIsland.vue` 新增 `trackEl` ref + `setTrackRef`（Motion `$el` 适配）+ `pillWidth` ref + `widthSpring` computed + `widthAnimate` computed + `remeasureWidth()` + `contentKey` computed + watch + `onMounted(remeasureWidth)`；模板 pill 改 `<Motion as="button" :ref="setPillRef" :animate="widthAnimate" :transition="widthSpring">`，track 改 `<Motion :ref="setTrackRef">`；CSS `.island-pill` 加 `max-width: calc(100vw - 32px)` 兜底；测试钩子 `:data-pill-width="pillWidth ?? null"`。
- **常量**：`PILL_PAD_X=32`（左右 padding 16×2）/ `BADGE_W=30`（未读角标占位）/ `PILL_MIN_W=56`；reduce-motion `widthSpring` 设 `{duration:0}` 瞬时。
- **测试**：`DynamicIsland.spec.ts` 新增 "FR-007 宽度 spring：data-pill-width 随角标出现重算（增宽）" 用例。

### SC-001 · 数字跳动 labelStack 栈实现
- **裁决**：旧值经 `labelStack` ref 短暂保留渲染 `.is-value-out`（absolute + CSS keyframes 上滑淡出），400ms 后 `window.setTimeout` 清栈（jsdom 不跑 CSS animation，用 setTimeout 保证测试可推进）；新值由 `runningLabel` Motion 的 `:key` 重建承担下滑淡入。
- **落地**：`DynamicIsland.vue` 新增 `labelStack` ref + `labelOutTimer` + `watch(runningLabel)` 推栈 + `watch(liveDone)` playPop + `onBeforeUnmount` clearTimeout；模板 running 分支加 `<span v-for="old in labelStack" class="island-value is-value-out">`；CSS 新增 `.island-value.is-value-out` + `@keyframes island-value-out`；reduce-motion 媒体查询加 `.island-value.is-value-out { animation: none }`。
- **测试**：`DynamicIsland.spec.ts` 新增 "SC-001：labelStack 旧值上滑淡出，400ms 后出栈" + "SC-001：数字变化触发 playPop（reduced=false）" 用例。
