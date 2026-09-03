# 037 灵动岛 v3 · live 仪表盘 + 转盘轮播 + toast 折叠 · 统一 Spec

**Feature Branch**: `037-dynamic-island-v3`
**Created**: 2026-09-03
**Input**: 用户确认（基于 037 交付后的体验反馈："我没看到你说的灵动岛有多好啊。作为用户，一个最显眼的体验就是那种小浮窗的提示不应该也要集成到里面去吗？" + 中间态/转盘方向口头反馈 + 四轮质询答复）

**现行口径**：本文件是灵动岛唯一现行 Spec，整合了旧版通知中心、提醒按钮回退、live 仪表盘、转盘轮播、toast 折叠以及后续修订结论。旧版文档不再作为独立前置规范；代码、测试和已确认的用户体验保持不变。

## 统一范围与继承口径

- 通知中心能力包含终态通知、打断沉入、面板展开/收起、未读高亮、会话级已读、行点击导航和三抽屉互斥。
- 提醒按钮只负责投递提醒：角标与列表均使用服务端投递提醒总数，不与灵动岛通知合并。
- 当前主流程由 live lane 驱动，打断由 carousel 展示一次后沉入面板；不再保留一套与 carousel 并行的旧折叠态状态机。
- 已确认的修订边界全部纳入本 Spec：减少动态、数字旧值退场、kaleido 面板与胶囊统一 6px blur、profile 切换清理旧状态，以及短文案胶囊不得裁剪。

## 最高目标（用户原话级，验收/任务的唯一衡量尺）

- **岛要展示中间态，不只展示结果**：running 态实时显示"正在抓取 12/30""AI精筛 3/30""粗筛剔除 5"这类进行中信息；completed 态按当前已提供的数据展示匹配/待确认两类。完整的不匹配/已筛除分类待后续数据契约具备后再扩展，不在本次统一 Spec 中虚构数据。旧版 `useIslandNotices` 曾主动丢弃 running 数据，当前改为 live 驱动。
- **岛是"弹弹的"实时仪表盘**：pill 宽度随内容 spring 弹性伸缩（"弹弹的感觉"），数字跳动有弹性，颜色清晰区分阶段和状态（蓝=抓取/紫=精筛/绿=匹配/琥珀=待确认/红光=报错或暂停）。字数长短无所谓，因为岛会弹大弹小。
- **转盘轮播优雅处理打断**：主流程（live state）永远 pinned 在 lane 0；打断消息到达，pill 垂直转一次展示这条、转完回主流程，那条不再在 pill 重复展示。多条积压只转最新一条，余数沉入 panel 未读、角标提示。主流程才是 pill 的主角。
- **报错/暂停是 live state 红光**：任务暂停/停止后整个岛体隐隐闪光泛红（有设计感）；任务恢复/修好自然褪红，回到正常态。不进打断队列、不需手动 dismiss。现有报错 UI（进度条、红字）一个不动，灵动岛是叠加层。
- **toast 折叠**：TaskCompletedToast（035）删除，completed pill + 点击 navigate 接管"完成→查看最新"功能；所有 NoticeBar 提示都进入灵动岛，统一展示一次后沉入通知面板，角标提示未读。
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

### US-2 — completed 态结果分类
任务跑完，pill completed 态展示当前数据源支持的两类芯片：
- `匹配 5`（绿）、`待确认 3`（琥珀）；不匹配/已筛除暂不在胶囊中显示，因为当前状态源没有向胶囊提供这两类计数。
- 芯片与当前结果页可用的 matched/pending 口径一致；未来数据源扩展后再单独增加其余分类。
- 点击 pill（completed + 无未读）navigate 到结果页（接管 TaskCompletedToast 的"完成→查看最新"）。

**Acceptance**:
- 模拟 running → completed(judged)，pill 渲染匹配和待确认两个芯片，颜色绿/琥珀，计数与当前 capsule 结果字段一致。
- 完成态通知继续使用 `匹配 N · 待确认 P` 摘要；不在没有数据源的情况下扩展为四元组。

### US-3 — 转盘轮播优雅处理打断
主流程在 pill 上展示时（running/completed/idle），一条重要打断消息（投递提醒/NoticeBar warning/error）到达：
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
- 任务恢复/修好（capsule 离开 attention）：红光自然褪去，回到正常态（running 蓝/紫或 completed 两色）。
- 不需手动 dismiss——红光绑定任务状态，任务恢复即褪。

**Acceptance**:
- 模拟 running → attention(kind:error)，pill 背景出现红光层（测试断点：含 `data-glow="error"` 或红光 class）。
- 模拟 attention → running：红光层移除，回到蓝/紫进度态。
- 红光期间推一条打断：carousel 仍转一次展示打断、转回红光态（红光不被打断覆盖）。
- 现有报错 UI（DiscoveryView 进度条/红字/暂停条）**不动**——断言这些组件渲染不受灵动岛影响。

### US-5 — TaskCompletedToast 折叠删除（路径 3，已授权）
TaskCompletedToast.vue（035 遗留）的"完成→点击查看最新"功能由灵动岛 completed pill + navigate 语义性接管；toast 组件及其渲染一并删除（用户授权红线例外）：
- 任务跑完不再弹独立浮窗 toast；pill completed 两色芯片就是"完成"信号，点击 navigate 到结果页（等价原 toast 的"点此查看最新结果"）。
- 删除：DiscoveryView.vue 删 L15 import + L192 解构项 + L1118-1122 元素（净 -7 行）；`webui/src/components/TaskCompletedToast.vue` + `webui/src/components/__tests__/TaskCompletedToast.spec.ts` 删除。
- `taskCompletedToast` ref 留在 `useDiscoveryState.ts`（禁改），删元素后偶尔被设 true 但无消费者，无害空写。

**Acceptance**:
- 岛 completed 态是"完成→查看最新"的主路径：点击 completed pill（无未读打断）触发 navigate("results")，跳到结果页；当前完成态显示匹配/待确认两色芯片。
- DiscoveryView.vue 无 TaskCompletedToast import（L15）/解构项（L192）/模板元素（L1118-1122）；`data-testid="task-completed-toast"` 元素不再出现。
- TaskCompletedToast.vue + TaskCompletedToast.spec.ts 文件不存在。
- `useDiscoveryState.ts` 的 `taskCompletedToast` ref 仍在（禁改），但 App/DiscoveryView 不再渲染其对应 toast。

### US-6 — NoticeBar 统一折叠
NoticeBar（App.vue 的提示来源）不再作为独立浮窗展示，所有 notice 都由灵动岛承接：warning/error 保留原有告警色；info/success 归一为提示色。它们都进入同一条打断队列，展示一次后沉入通知面板并保留未读。
- NoticeBar 组件保留其提示数据来源，但不再渲染为用户可见的独立条幅。
- 多条提示按 carousel 的"只转最新一条"语义处理；不自动轮播旧提示。

**Acceptance**:
- 推一条 `{tone:"warning", message:"..."}` notice：进入 island 打断队列（carousel 转一次），NoticeBar 不再行内展示 warning。
- 推一条 `{tone:"success", message:"已保存"}`：进入 island 打断队列并展示一次，NoticeBar 不渲染；展示结束后沉入未读历史条目。
- 推一条 `{tone:"error", message:"..."}`：进入 island 打断队列 + pill 触发红光（若任务同时进入 attention）。

### 边角
- 跨 profile 切换：carousel 重置（打断队列清空、panel 清空、回到 idle lane 0），并立即清除旧 profile 的胶囊状态；新 profile 状态到达前不显示旧进度、结果或错误。
- 浏览历史轮次（scope=history）：pill 展示历史轮的 completed 摘要（当前两色口径），打断队列暂停消费（历史浏览期间不轮转展示新打断，顺延到回到最新）。
- 窄屏（≤760px）：pill 宽度有上限（不超屏宽 -32px），内容超出截断或省略；carousel 仍垂直转。
- kaleido 主题：红光/两色芯片在半透明毛玻璃下保证可见，面板与胶囊统一使用 6px blur。

## Requirements

### Functional

- **FR-001** pill 主体 MUST 绑定 `roundStatus.capsule` 的 live state：running 态实时显示 `progress.done/total` + phase 颜色（蓝=scraping/紫=screening），不再由通知清理逻辑丢弃 running 数据。数据零新增接口，全部读现有 `roundStatusPayload`。
- **FR-002** completed 态 MUST 展示当前数据源支持的两色芯片：匹配(绿 `c-green`)/待确认(琥珀 `c-amber`)；不匹配/已筛除只有在状态源提供对应计数后才能扩展，不得伪造计数。
- **FR-003** pill 内部 MUST 实现垂直 spring carousel：lane 0 = 当前 live state（pinned，打断转完回这里）；lane 1+ = 打断队列 FIFO。`translateY` 用 motion-v spring 过渡（`type:"spring", stiffness:300, damping:26`，有 overshoot 才"弹"）。reduce-motion 退化为瞬时切换（translateY 直接设值无过渡）。
- **FR-004** 打断到达 MUST 垂直转一次：carousel translateY 弹性上移一格 → 展示打断 → ~2.2s 后弹回 lane 0。所有 NoticeBar tone 与投递提醒转完都沉入 notice panel 作未读，不在 pill 重复展示；warning/error 保留原 tone，info/success 归一为提示色。
- **FR-005** 多条积压 MUST 只转最新一条一次：余数只进 panel 未读、不轮播；pill 角标 = panel 未读数。点角标/pill 展开 panel 看全部。**不自动轮播**（用户明确："只转动第一次，转完回到主线程，不再展示，左上角/岛内小图标提示未查看"）。
- **FR-006** attention(error/paused) MUST 是 live state 红光层（非打断队列项）：岛体背景/边框泛隐隐闪光红（subtle glow，有设计感）。任务离开 attention 态（capsule 切走）红光自动褪去，不需手动 dismiss。红光期间打断仍可轮转（转完回红光态）。
- **FR-007** pill 宽度 MUST 随内容 spring 弹性伸缩（"弹弹的"）：宽度跟随当前 lane 的自然内容宽度、左右内边距、左右边框和未读角标空间自适应，motion-v spring 过渡（非 linear）；首帧布局或字体稳定后宽度变化必须重新测量，支持的视口内不得裁剪文案。内容入场的弹性只允许沿垂直轴变化，不得把文字或芯片向左右撑出裁剪边界。reduce-motion 退化为瞬时宽度切换。
- **FR-008** TaskCompletedToast 的"完成→查看最新"功能 MUST 由灵动岛 completed pill + navigate 语义性接管（点击 completed pill 无未读打断时 navigate("results")）。**toast 删除走路径 3（用户已授权红线例外）**：DiscoveryView.vue 删 L15 import + L192 解构项 + L1118-1122 元素（净 -7 行）；TaskCompletedToast.vue + 其 spec 文件删除。`useDiscoveryState.ts` 的 `taskCompletedToast` ref 保留不动（禁改，删元素后无害空写）。
- **FR-009** 所有 NoticeBar notice MUST 进入灵动岛的同一打断队列：warning/error 保留原 tone，info/success 归一为提示色；展示结束后均沉入未读历史。NoticeBar 不再作为用户可见的独立浮窗。
- **FR-010** 现有报错 UI（DiscoveryView 进度条/红字/暂停条、loginGuide 等）MUST 不动——灵动岛是叠加层，不替换/移除现有报错展示。
- **FR-011**（硬不变式）carousel MUST 只切显示位，永不冻结底层数据流：`roundStatus` 照常驱动 lane 0（main flow）数据更新；打断展示期间 done/total 照常推进；转回主流程时用户直接看到新数字。任何实现不得在打断期间暂停/缓存 mainLane 的数据订阅。
- **FR-012** 全部动画在 reduce-motion 下退化为瞬时（`useReducedMotion()` 短路）：carousel translateY 直接设值无过渡、pill 宽度瞬时切换、红光瞬时出现/消失、数字瞬时换。
- **FR-013** 保留通知中心的 panel 展开/dismiss snapshot（带 id 快照）/互斥（collapseIsland + 三抽屉）/tab trap/Teleport backdrop/navigate 语义/all-read 直接 navigate/aria-live announce；dialog 打开后首个可交互通知获得焦点，Tab 与 Shift+Tab 只能在 dialog 内循环，关闭后归还胶囊；completed 终态记录为已读，error/paused/interrupt 仍按未读提醒处理。
- **FR-014** 切换当前 profile 时 MUST 立即清除旧 profile 的胶囊状态、通知和未读数；新 profile 状态到达前不得继续显示旧 profile 的运行进度、结果或错误。
- **FR-015** completed 摘要数字变化时，旧摘要必须短暂退场、新摘要继续入场；系统减少动态时直接切换，不保留退场动画。
- **FR-016** 补抓阶段 `recrawl_fetch_jd` MUST 显示为“抓取 JD”，窗口尺寸变化后 MUST 重新测量胶囊宽度；长文案在窄屏单行省略，点击可查看当前普通反馈的完整临时详情。

### Non-Functional / 架构

- **架构边界（AGENTS + 宪法）**：
  - 允许修改：`webui/src/App.vue`（删 TaskCompletedToast 接线 + 将所有 NoticeBar 反馈接入灵动岛）、`webui/src/components/DynamicIsland.vue`（carousel + live state + 两色 + 红光 + 宽度测量）、`webui/src/composables/useIslandNotices.ts`（running 不清池 + interrupt 沉入 + profile reset）、`webui/src/components/IslandNoticePanel.vue`（打断来源与主题样式）、各测试文件、`webui/src/test/setup.ts`（如需测试桩增强）。
  - **已确认范围例外**：`webui/src/views/DiscoveryView.vue` 删除 toast 相关 7 行，并将恢复提示转成灵动岛通知；`webui/src/composables/useDiscoveryState.ts` 允许补充 JD 阶段派生和 `jd` 类型；`webui/src/discovery.ts` 允许同步阶段类型；`webui/src/styles.css` 允许清理已移除恢复浮窗的样式。除此之外不扩展到其他业务逻辑。
  - 新增：`webui/src/composables/useIslandCarousel.ts`（carousel 状态机：lanes/activeLaneIndex/badge/pushInterrupt/dismissActive）。
  - 新增：`webui/src/composables/useIslandValueTransition.ts`（展示值切换期间的旧值退场，不发请求、不拥有业务状态）。
  - 删除：`webui/src/components/TaskCompletedToast.vue`、`webui/src/components/__tests__/TaskCompletedToast.spec.ts`。
  - 禁止修改：`webui/src/composables/useDiscoveryState.ts` 中与本 Spec 无关的既有派生、任何后端文件（`webui/*.py`、`scripts/`）、`webui/src/jobFeedback.ts` 等 API 客户端、`webui/src/components/ReminderDrawer.vue`、`webui/src/api.ts`。
  - 禁改新增接口：不改动任何后端 endpoint。
  - 新文件 MUST 在同一批次登记 `.specify/memory/constitution.md`「模块地图」（原则 VI）。
- **行数门禁（宪法 II 硬线）**：Python ≤ 800、Vue ≤ 1200、TS 无硬线但自设轻量。DynamicIsland.vue 的 carousel+live+两色+红光实现 MUST 不破 1200；useIslandCarousel.ts 自设 ≤ 250。
- **零新接口**：本 Spec 全部数据来自 036 已上抛的 `roundStatus`（含 `capsule.progress`、`TaskSnapshot` 字段）+ 现有 `useReminderBadge` + 现有 NoticeBar notice 流。不新增/不改任何后端 API。

### Key Entities
- **IslandLiveState**（派生自 `roundStatus.capsule`，pill lane 0 内容）：`{ phase: "scraping"|"jd"|"screening"|"completed"|"idle"|"attention", done, total?, counts?: {matched, pending}, glow?: "error"|"paused"|"none" }`
- **IslandLane**（carousel 一格）：`{ id, type: "main"|"interrupt", content: IslandLiveState | {title, detail, tone}, priority, sticky?: boolean, duration?: number }`
- **useIslandCarousel API**：`{ activeLaneIndex, lanes, badgeCount, pushInterrupt(lane), dismissActive(), reset(), mainLaneState }` —— mainLaneState 绑定 roundStatus 实时更新（不冻结）
- **IslandNotice**：`kind` 包含 `"completed"|"error"|"paused"|"interrupt"`；`target` 支持 `"reminders"`；完成摘要当前使用 matched/pending 两元口径。

## Success Criteria

- **SC-001** running 态 pill 展示 live 进度（`正在抓取 N/M`），done 由 N→N+1 时旧值上滑淡出、新值下滑淡入 + 弹动；pill 宽度 spring 弹大。
- **SC-002** completed 态 pill 展示匹配/待确认两色芯片，颜色绿/琥珀，计数与当前 capsule 结果字段一致；不匹配/已筛除不在无数据源时伪造展示。
- **SC-003** 打断到达：carousel 垂直转一次（translateY spring 弹性），展示打断 2.2s，转回主流程；转回后主流程数字已推进（硬不变式验证）。
- **SC-004** 多条积压只转最新一条一次，余数进 panel 未读，角标=未读数；不自动轮播。
- **SC-005** attention 态 pill 红光层出现（`data-glow="error"|"paused"`）；离开 attention 红光褪去；红光期间打断仍可轮转。
- **SC-006** DiscoveryView.vue 无 TaskCompletedToast import/解构/渲染（净 -7 行）；TaskCompletedToast.vue + spec 文件不存在；任务跑完无 `data-testid="task-completed-toast"` 元素；岛 completed pill 点击 navigate("results") 等价原 toast"查看最新"。
- **SC-007** 所有 NoticeBar 提示进入灵动岛；展示一次后均进入未读历史，warning/error 保留原 tone，info/success 使用提示色；不渲染独立 NoticeBar。
- **SC-008** 现有报错 UI（DiscoveryView 进度条/红字）渲染不受灵动岛影响（断言未改）。
- **SC-009** reduce-motion 下 carousel/宽度/红光/数字全退化为瞬时。
- **SC-010** 验证门禁：聚焦测试 + 前端全量测试 + `npm run build` + 仓库卫生全部通过（宪法 V）。
- **SC-011** 切换 profile 后旧 profile 的胶囊状态、通知和未读数立即消失；新 profile 的状态到达后只显示新 profile 内容。
- **SC-012** 真实浏览器中无未读角标的 BOSS/智联空闲胶囊，平台名完整显示；首帧布局或字体稳定、内容入场弹动和 hover/press 期间，左右边界仍不裁剪内容。
- **SC-013** completed 摘要数字变化时旧值短暂退场、新值入场；减少动态模式下不保留退场摘要。
- **SC-014** `recrawl_fetch_jd` 显示为“抓取 JD”；窗口 resize 后胶囊左右边界重新适配，窄屏长文案不发生硬裁剪。

## Verification Scope
- 聚焦测试：`useIslandCarousel.spec.ts`（转盘/不冻结/重置）、`useIslandNotices.spec.ts`（running live + 两色完成态 + interrupt 沉入）、`DynamicIsland.spec.ts`（carousel/两色/红光/宽度/数字退场/badge/转回数字推进）、`App.spec.ts`（toast 删除/navigate 接管/NoticeBar 分流/profile reset）。
- 前端全量测试：vitest run。
- `npm run build`：vue-tsc 严格通过 + vite 构建成功。
- 仓库卫生：`uv run python -m unittest tests.test_repo_hygiene`。
- 后端不动；不跑后端全量。
- 用户端到端走查在交付后进行。

## Assumptions
- A1 不新增/不改任何后端接口；live 数据全部来自 036 已上抛的 `roundStatus.capsule`（含 progress）+ `TaskSnapshot` 字段。
- A2 `TaskSnapshot` 的 kept_count/dropped_count/pending_count/success_count/fail_count/unstarted_count 字段已由后端填充（见 `useDiscoveryState.ts` L1144-1169 接口定义 + `ai_screening.py` 的 `_emit_*` 事件）——本版前端直接读，不改后端。
- A3 打断来源当前只有投递提醒 + 全部 NoticeBar tone；未来扩展（其他外部消息）按同一接口接入。
- A4 `useDiscoveryState.ts` 的 `taskCompletedToast` ref 保留不动（除已确认的 JD 阶段派生外不扩展改动）；DiscoveryView.vue 的 toast 渲染（L15+L192+L1118-1122）已按路径 3 删除，恢复提示转交灵动岛；删后 ref 偶尔被设 true 但无消费者，无害空写。
- A5 NoticeBar 组件保留；只改 App 侧 notice 分流逻辑（所有 tone → island 同一打断队列，info/success 归一为提示色）。
- A6 红光"隐隐闪光"用 CSS keyframes（subtle pulse，非刺眼纯红），reduce-motion 下静止。
- A7 转盘"弹"的 overshoot 由 motion-v spring 物理提供，不手写 keyframes overshoot。
- A8 通知中心的 panel/dismiss/互斥/navigate/aria 行为全保留；统一后不再维护第二套旧版状态机。
- A9 胶囊宽度计算按自然内容宽度 + 内边距 + 边框 + 未读角标空间测量；字体或首帧布局稳定后重新测量，避免短文案裁剪。

## TaskCompletedToast 删除路径决策（已定：路径 3，用户授权 2026-09-03）

TaskCompletedToast 渲染在 `DiscoveryView.vue`（当前 1224 行，超 Vue 1200 红线）；`taskCompletedToast` ref 在 `useDiscoveryState.ts`（当前 1289 行 TS）。宪法原则 IV"重构/拆分不混入功能开发"使本功能 Spec 默认不能改 DiscoveryView，但本版已记录用户确认的 toast/恢复提示例外。

**用户授权路径 3**：本批次内对 DiscoveryView.vue 做红线例外，删除 toast 相关 7 行并把恢复提示转交灵动岛；除这两个已确认边界外不扩展业务逻辑。此处仍需持续关注超线文件的后续拆分，但不回退 V3 的用户体验。

**删除清单**（DiscoveryView.vue，共 7 行）：
- L15：`import TaskCompletedToast from "../components/TaskCompletedToast.vue";`
- L192：`  taskCompletedToast,`（多行解构中的一项）
- L1118-1122：`<TaskCompletedToast :visible="taskCompletedToast.visible" @click="taskCompletedToast.visible = false; void returnToLatest()" @close="taskCompletedToast.visible = false" />`

**保留不动**：
- `useDiscoveryState.ts` 的 `taskCompletedToast` ref——删元素后它偶尔被设 true 但无消费者，属无害空写。
- `returnToLatest()` 方法（别处仍调用）。
- DiscoveryView 其他逻辑（仅删上述 7 行，不动其余）。

**文件**：`webui/src/components/TaskCompletedToast.vue` 删除；`webui/src/components/__tests__/TaskCompletedToast.spec.ts` 删除（组件已删，测试随之删）。

## Out of Scope
- 删除 `useDiscoveryState.ts` 的 `taskCompletedToast` ref（禁改红线，保留不动）。
- 新增打断来源类型（除投递提醒 + 现有 NoticeBar 流外的新消息源）。
- 跨会话持久化打断队列/未读状态（保持会话级内存）。
- carousel 3D 真·转盘物理旋转（用 vertical spring carousel 近似实现，视觉等效）。
- 自定义 pill 颜色/字号设置项。
- 后端任何改动。

## 复审裁决与降级（实现期 · 2026-09-03）

复审在完整 Spec Kit 流程后的独立全量审查中发现若干规格与实现约束冲突，裁决如下（已在本批次落地，spec 原文保留作为设计意图快照）：

### P1-2 · FR-002/US-2/SC-002 四色 → 两色降级
- **裁决**：completed 态 pill 只展示 `匹配(绿) + 待确认(琥珀)` 两色芯片，**不**实现"不匹配(灰)/已筛除(红)"两色。
- **根因**：`useDiscoveryState.ts` 的 capsule 仅上抛 `results: { matched, pending }` 两元，链路无四元数据源；本版不为四色扩展改造无关数据层。原 FR-002"扩展 completedDetail 返回四元"在当前数据合同下不可能实现——`completedDetail` 的输入只有 `capsule.results`，扩不出 unmatched/dropped。
- **落地**：`useIslandNotices.completedDetail` 仍只返回 `匹配 N · 待确认 P`；`DynamicIsland.vue` completed lane 只渲染 `c-green + c-amber` 两色 chip；`IslandNoticePanel.vue` 删除不可用的"四色 chip"渲染死代码（panel completed 行只显示 detail 文字）。
- **未来**：待后续 Spec 单独扩展 capsule 上抛 unmatched/dropped（需改 `useDiscoveryState.ts`，必须先拆分该文件），届时四色 chip 随 capsule 扩展一并加回。
- **影响文档**：CHANGELOG/README 与本统一 Spec 均按当前两色口径记录；未来四色扩展需先补齐 capsule 数据源，再单独更新本 Spec。

### P2-1 · FR-008 vs FR-013 裁决：completed 终态通知 `read:true`
- **裁决**：completed 跃迁派生的终态通知初始 `read: true`（**不**计未读）。
- **根因**：FR-008"completed pill 点击直达 results"与 FR-013"unread-gating（有未读时点击展开 panel 而非 navigate）"冲突——若 completed 通知未读，pill 点击会被 unread-gating 拦截去展开 panel，无法直达 results。FR-013 的 unread-gating 是为了"用户没看过的告警先看"，但 completed 的"完成"信号已由 pill completed live state 彩色芯片实时展示（不依赖 panel 行），panel 的 completed 行只是历史记录、不算未读。
- **落地**：`useIslandNotices.processTransition` 中 completed upsert 设 `read: true`；error/paused/interrupt 保持 `read: false`（用户没看过的告警仍要角标提示）。
- **效果**：完成后 pill 不显示未读角标（unreadCount=0），点击直达 results（等价被删 TaskCompletedToast 的一键直达）；error/paused/interrupt 仍走 unread-gating → panel 流。
- **测试**：`useIslandNotices.spec.ts` completed 用例加 `read:true` + `unreadCount 0` 断言；`DynamicIsland.spec.ts` 新增 "P2-1 回归：completed + read:true notice → 点 pill 直达 results" 用例；`App.spec.ts` 新增 "037 P2-1: completed 通知 read:true，点 pill 直达结果页" 用例。

### P2-3 · FR-005 防御性 sticky 打断不滞留队列卡死角标
- **裁决**：当前用户可见来源（投递提醒、NoticeBar 反馈）均不使用 sticky；所有当前打断按一次展示后自动沉入或结束。`useIslandCarousel` 保留 sticky 分支仅作内部防御，避免未来误传 sticky 时卡死队列或角标。
- **根因**：原 pushInterrupt 伪代码"多条积压只转最新一条"在 sticky 场景下有死角——若未来某个内部调用展示 sticky，新 sticky 入队后可能永远等不到展示机会，badgeCount 会一直包含它，无法消除。
- **落地**：`useIslandCarousel.pushInterrupt` 继续检查 active 是否 sticky：active sticky + new sticky → sink(new) 直接沉入 panel；active sticky + new 非 sticky → 只入队+timer；当前产品路径不传入 sticky。
- **"只转最新一条"语义澄清**：US-3"多条积压只转最新一条"指**被动响应新打断**——展示位永远切到最新 push 的，旧条靠各自 timer 沉入 panel；**不**是"自动轮播"——dismiss 后不补转下一条。原 plan 伪代码"activeLaneIndex = queue 末尾"本意即"切到最新"，实现按此语义落地。
- **测试**：`useIslandCarousel.spec.ts` 保留 3 条 sticky 防御性测试（展示中 push 非 sticky 不抢位 / 展示中 push sticky 直接沉入 / 非 sticky 展示中 push sticky 抢位）；这些测试不代表当前用户可见来源会 sticky。

### P2-5 · 边角"history 暂停消费"落地
- **裁决**：`scope === "history"`（浏览历史轮）期间，新提示（NoticeBar 反馈 + 投递提醒 0→N）暂停消费——经 `pushInterruptOrDefer` 攒入 `historyPendingInterrupts` 缓冲；scope 离开 history（回到 live）时 watch 逐条 flush（最后一条占据展示位"只转最新"，余数照常计时沉入 panel）。
- **根因**：原 spec 边角"history 期间打断队列暂停消费，顺延到回到最新"未明确"如何顺延"——若直接 drop，回 live 后用户错过；若直接 pushInterrupt，会打断 history 浏览。
- **落地**：`App.vue` 新增 `historyPendingInterrupts` 数组 + `pushInterruptOrDefer(lane)` 函数 + `watch(roundStatus.scope)` flush；`showNotice` 与 reminder watch 改调 `pushInterruptOrDefer`；profile reset 清空缓冲。
- **测试**：`App.spec.ts` 新增 "037 P2-5: history 浏览期间 notify 暂停消费，回最新时 flush" 用例。

### P2-8 · 打断行点击目标 "reminders" 分流
- **裁决**：投递提醒打断沉入 panel 后，行点击 target="reminders"——App 层 `handleIslandNavigate` 拦截：reminders → `toggleReminderDrawer()` 开提醒抽屉；其余 target（home/task/results/attention）→ `requestCapsuleNavigation`。
- **根因**：原 spec 未明确"投递提醒打断沉入后行点击去哪"——`requestCapsuleNavigation` 只认 home/task/results/attention（`CapsuleNavigationTarget`，`useDiscoveryState` 禁改），不认识 reminders。投递提醒的语义本就是"开提醒抽屉看逾期列表"，分流必须在 App 层做。
- **落地**：`useIslandNotices.IslandNoticeTarget` 增 "reminders"；`useIslandCarousel.IslandInterruptContent.target` 增 "reminders"；`DynamicIsland.vue` 的 `CapsuleTarget` 增 "reminders"；App reminder 0→N watch 的 pushInterrupt content.target 设 "reminders"；`App.vue` 删 `handleCapsuleNavigate`，新增 `handleIslandNavigate(target)` 分流。
- **测试**：`App.spec.ts` 新增 "037 P2-8: 投递提醒打断 panel 后行点击直达 reminders（开提醒抽屉）" 用例。

### P1-3 / P2-4 · FR-007 pill 宽度 spring 实现方案
- **裁决**：放弃 ResizeObserver 镜像渲染（复杂 + setup 需加 RO 桩），改用 `watch([contentKey, unread], ..., { flush: "post" })` + `nextTick` + 读当前 lane 的 CSS 自然宽度（无法读取时再回退 `offsetWidth`）测量展示位，零镜像、零新桩；字体接口可用时在字体就绪后再测量。
- **落地**：`DynamicIsland.vue` 使用 `trackEl`/`setTrackRef` 测量当前 lane 自然宽，`remeasureWidth()` 为左右内边距、左右边框和角标预留空间；内容/未读变化、组件挂载和字体就绪后重新测量；CSS `.island-pill` 保留 `max-width: calc(100vw - 32px)` 兜底；测试钩子保留 `:data-pill-width="pillWidth ?? null"`。
- **常量**：`PILL_PAD_X=36`（左右 padding 18×2）/ `PILL_BORDER_W=2`（左右 border 1×2）/ `BADGE_W=30`（未读角标占位）/ `PILL_MIN_W=60`；reduce-motion `widthSpring` 设 `{duration:0}` 瞬时。
- **测试**：`DynamicIsland.spec.ts` 覆盖角标出现后的重算、边框空间和字体就绪后的重新测量，确保短文案不裁剪。

### SC-001 · 数字跳动旧值退场实现
- **裁决**：旧值经 `useIslandValueTransition` 短暂保留渲染 `.is-value-out`（absolute + CSS keyframes 上滑淡出），400ms 后自动清理；新值由 keyed Motion 重建承担下滑淡入。
- **落地**：`DynamicIsland.vue` 接入纯 UI 值切换 composable；CSS 保留 `.island-value.is-value-out` + `@keyframes island-value-out`；减少动态时不保留退场摘要。
- **测试**：`DynamicIsland.spec.ts` 新增旧值上滑淡出、400ms 后清理和数字变化触发 playPop（reduced=false）用例。
