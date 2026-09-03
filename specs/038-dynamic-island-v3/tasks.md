# 038 灵动岛 v3 · live 仪表盘 + 转盘轮播 + toast 折叠 · Tasks

执行顺序：批次 1（live 仪表盘 + 四色 + 转盘轮播）→ 批次 2（toast 折叠删除·路径 3）。两批次同 PR 内分两个提交。

## 批次 1 — live 仪表盘 + 四色 + 转盘轮播

### T1.1 新增 `webui/src/composables/useIslandCarousel.ts`
- 导出 `IslandLiveState`、`IslandLane`、`IslandCarouselApi`、`useIslandCarousel`；
- `mainLaneState` computed 直接读 `roundStatus.capsule`，产出 live state（phase/done/total/sub/counts/glow/platform）——**不冻结**（硬不变式 FR-011）；
- `lanes` = mainLane + interruptQueue（FIFO）；
- `activeLaneIndex` ref（0=main，1+=interrupt）；
- `pushInterrupt(lane)`：入队；若 activeLaneIndex===0 则设为队尾 index 触发 carousel 转一次 + playPop()；若已在 interrupt lane 则只入队+角标++（不重复转）；
- 定时 interrupt：`setTimeout(duration)` 后 activeLaneIndex 回 0、该条从队移除、沉入 notice panel 未读；sticky interrupt 不自动回，等 `dismissActive()`；
- `badgeCount` = interruptQueue 长度（未读打断数）；
- `reset()`：清队列、回 lane 0（跨 profile 切换调）；
- 测试 `webui/src/composables/__tests__/useIslandCarousel.spec.ts`：
  - mainLaneState 实时跟随 roundStatus（running done 5→6 时 mainLane 数字变 6，证明不冻结）；
  - pushInterrupt 转 activeLaneIndex 0→1，2.2s 后回 0；
  - 多条积压：连推 3 条只转一次（activeLaneIndex 不在 0 时 push 不再触发 translateY）；
  - sticky interrupt 不自动回，dismissActive 才回；
  - 打断期间 mainLane done 照常推进（硬不变式验证）；
  - reset 清队列回 lane 0。

### T1.2 改 `webui/src/composables/useIslandNotices.ts`
- 删 L93 running 态 `clearAll()`——running 不再清池，改为产出 live state 数据（供 carousel mainLane 消费，实际由 useIslandCarousel 直接读 roundStatus 派生，useIslandNotices 只负责终态历史 + interrupt 沉入）；
- `completedDetail`（L43-47）扩展返回四元 `{matched, unmatched, uncertain, dropped}`（不只 matched+pending）；计数与 `resultCountsFromPipeline` + `groups` 同源；
- idle 态仍 clearAll（无主流程）；
- 终态事件（completed/error/paused 跃迁）仍 upsert 进 notices（panel 历史）；
- 新增：interrupt 沉入——暴露 `sinkInterrupt(notice)` 供 carousel 转完一条后把该打断作为 `kind:"interrupt"` upsert 进 notices（未读，进 panel）；
- `IslandNoticeKind` 增加 `"interrupt"`；
- 测试更新 `webui/src/composables/__tests__/useIslandNotices.spec.ts`：
  - running 不再 clearAll（断言 notices 不被 running 清空，live state 由别处派生）；
  - completed detail 返回四元（断言含 unmatched+dropped）；
  - idle 仍 clearAll；
  - sinkInterrupt：调一次新增一条 kind:"interrupt" 未读；
  - 037 既有用例（终态跃迁/markReadBatch/reset/history scope 排除）不回退。

### T1.3 重写 `webui/src/components/DynamicIsland.vue`
- props 增 `carousel: IslandCarouselApi`（沿用 037 的 status/notices）；
- pill 外壳：`overflow:hidden` viewport（高度 1 行 ~36px），宽度 `width:auto; max-width:calc(100vw - 32px)` + motion-v `<Motion :animate="{ width: contentWidth }" :transition="spring">`；
- carousel 内层：`<Motion :animate="{ y: -activeLaneIndex * lineHeight }" :transition="{type:'spring',stiffness:300,damping:26}">`，lane 绝对定位堆叠；reduce-motion → transition `{duration:0}`；
- lane 0（main）：渲染 IslandLiveState——
  - running scraping：`正在抓取 ${done}/${total?}` + 可选 sub（JD详情/失败/未启/翻页冷却/详情批次）；
  - running screening：`AI精筛 ${done}/${total?}` + sub（粗筛剔除/已判定/重试/待确认）；
  - completed：四色芯片（匹配绿/不匹配灰/待确认琥珀/已筛除红）；
  - attention：叠红光层（`data-glow`）；
  - idle：呼吸（沿用 037）；
- lane 1+（interrupt）：`{title · detail}`，tone 染色（warning 琥珀/error 红）；
- 角标：pill 右上 `<button class="island-badge" v-if="badgeCount>0">{{badgeCount}}</button>`，badgeCount++ 时 playPop()；点击展开 panel（沿用 037 open 行为）；
- 红光层：`<div v-if="glow!=='none'" class="island-glow" :data-glow="glow">`，CSS `@keyframes island-glow` subtle pulse；reduce-motion 静止；
- 数字跳动：`<Motion :key="done" :initial="{y:8,opacity:0}" :animate="{y:0,opacity:1}">` + playPop()；
- 保留 037：panel 展开/dismiss snapshot（带 id 快照）/互斥（collapseIsland + 三抽屉）/tab trap/Teleport backdrop/navigate/all-read 直接 navigate/aria-live announce；
- 行数 MUST ≤ 1200（宪法 II）；自设目标 ≤ 900；
- 测试更新 `webui/src/components/__tests__/DynamicIsland.spec.ts`：
  - carousel 转一次（pushInterrupt 后 translateY/activeLaneIndex 变化）；
  - 四色芯片渲染（4 个 chip，颜色 class）；
  - 红光层（attention 时 data-glow 出现，离开移除）；
  - 宽度弹（content 变化时 Motion animate width 变化）；
  - 角标 badgeCount + playPop；
  - 转回主流程数字已推进（pushInterrupt → 2.2s → 回 lane 0，mainLane done 已 +1）；
  - reduce-motion 全退化瞬时；
  - 037 既有用例（四态/navigate/dismiss/escape/focus/tab trap/panel）不回退。

### T1.4 改 `webui/src/components/IslandNoticePanel.vue`
- 适配四色：completed 行展示四色芯片（不只 matched+pending 文字）；
- interrupt 来源条目样式：kind:"interrupt" 行用 tone 染色（warning 琥珀/error 红）；
- 沿用 037：排序/未读高亮/已读淡化/行点击直达/role=dialog；
- 测试更新 `webui/src/components/__tests__/IslandNoticePanel.spec.ts`：四色 chip 渲染、interrupt 行 tone 染色；037 既有用例不回退。

### T1.5 接线 `webui/src/App.vue`
- `const islandCarousel = useIslandCarousel(roundStatus)`；
- `<DynamicIsland ref="islandRef" :status="roundStatus" :notices="islandNotices.notices" :carousel="islandCarousel" @navigate @expand @dismiss="handleIslandDismiss" />`；
- NoticeBar 分流：`<NoticeBar v-if="notice && (notice.tone==='info'||notice.tone==='success')" :notice="notice" @dismiss="dismissNotice" />`——warning/error 不渲染 NoticeBar，改为 `islandCarousel.pushInterrupt({title:notice.message, detail:'', tone:notice.tone})` 后清 notice；
- 投递提醒触发打断：watch `reminderBadge.reminderTotal` 由 0→N 时 `islandCarousel.pushInterrupt({title:'投递提醒', detail: N+'条逾期', tone:'warning', duration:2200})`；
- `watch(currentProfileId)`：已有 `islandNotices.reset()`；新增 `islandCarousel.reset()`；
- 保留 037：handleIslandExpand（关三抽屉+settingsMenu+themePicker）/handleIslandDismiss（markReadBatch 快照）/collapseIsland 互斥；
- 行数目标持平或 -10；
- 测试更新 `webui/src/__tests__/App.spec.ts`：
  - NoticeBar warning/error 不渲染（推 warning notice → NoticeBar 不见，carousel pushInterrupt 调用）；
  - NoticeBar info/success 仍渲染；
  - 投递提醒 0→3 → carousel pushInterrupt 调一次（badgeCount=1）；
  - profile 切换 → carousel.reset() + islandNotices.reset()；
  - 037 既有用例（互斥/navigate/dismiss/角标单源）不回退。

### T1.6 增强 `webui/src/test/setup.ts`
- 沿用 037：matchMedia 按 query 分流（reduced 默认 true）、`Element.prototype.animate` WAAPI 桩、`__setReducedMotionMatchMedia`；
- motion-v spring 在 jsdom 无物理——靠 reduced=true 让 transition 退化为 `{duration:0}`，carousel translateY 测试断言用最终 y 值；
- 无需新增桩（037 已够）。

### T1.7 验证门禁（批次 1 后）
- `cd webui && npm run test`：全部通过；
- `cd webui && npm run build`：vue-tsc + vite 构建通过；
- `uv run python -m unittest tests.test_repo_hygiene`：通过；
- 不动后端；不跑后端全量。

## 批次 2 — toast 折叠删除（路径 3，用户授权红线例外）

### T2.1 `webui/src/views/DiscoveryView.vue` 删 7 行
- 删 L15：`import TaskCompletedToast from "../components/TaskCompletedToast.vue";`
- 删 L192：`  taskCompletedToast,`（多行解构项）
- 删 L1118-1122：`<TaskCompletedToast :visible="taskCompletedToast.visible" @click="taskCompletedToast.visible = false; void returnToLatest()" @close="taskCompletedToast.visible = false" />`
- **不动其余任何行**；删后 `taskCompletedToast` ref 偶尔被设 true 但无消费者（无害空写，ref 在 useDiscoveryState 禁改不动）。

### T2.2 删 `webui/src/components/TaskCompletedToast.vue` + `webui/src/components/__tests__/TaskCompletedToast.spec.ts`
- 两文件删除（组件已删，测试随之删）。

### T2.3 App/DynamicIsland spec 更新（toast 删除 + navigate 接管）
- `App.spec.ts`：断言无 `data-testid="task-completed-toast"` 元素；completed pill 点击 → navigate("results")；
- `DynamicIsland.spec.ts`：completed 态点击（无未读）→ navigate("results")（等价原 toast"查看最新"）。

### T2.4 验证门禁（批次 2 后）
- `npm run test`、`npm run build`、仓库卫生检查均通过；
- `wc -l webui/src/views/DiscoveryView.vue` = 1216（1223 - 7）；
- TaskCompletedToast.vue + spec 文件不存在。

## 收口
- `CHANGELOG.md` 追加 038 未发布条目（按 AGENTS 写作规范）：灵动岛 v3 live 仪表盘 + 转盘轮播 + 双色分类 + 红光 live state + TaskCompletedToast 折叠删除；
- `.specify/memory/constitution.md` 模块地图：登记 `useIslandCarousel.ts` 新文件；`TaskCompletedToast.vue` 行标注"038 删除"；
- `README.md` 灵动岛 bullet 更新为"live 仪表盘 + 转盘轮播"；
- 不提交、不推送、不打包（用户未授权 git）。

## 复审后补齐（2026-09-03 独立全量审查后落地）

复审在完整 Spec Kit 流程后跑独立全量审查（见 REVIEW_PROMPT.md §4.1-4.8 + §5），发现 3 项 P1 + 8 项 P2 + 7 项测试缺口。用户裁决"不要进 bug 单，全修复吧"——本批次内全部修复，未进 BACKLOG。落地清单：

### T1.3 补齐组件级 carousel 测试（原承诺未兑现）
- DynamicIsland.spec 新增 "DynamicIsland 038 组件级 carousel + 宽度 spring + 数字弹动" describe 块 8 条用例：
  1. pushInterrupt → interrupt lane 渲染（title/detail/data-tone）+ activeLaneIndex 切换
  2. FR-011 硬不变式：打断展示期间主流程数字继续推进，转回主流程显示新值
  3. badgeCount 增长触发 playPop（reduced=false）
  4. SC-001：数字变化触发 playPop（reduced=false）
  5. SC-001：labelStack 旧值上滑淡出，400ms 后出栈
  6. FR-007 宽度 spring：data-pill-width 随角标出现重算（增宽）
  7. P2-1 回归：completed + read:true notice → 点 pill 直达 results（不展开 panel）
  8. reduce-motion 下 carousel 状态机仍工作（push → sink → 回 lane 0）
- mountIsland 助手重构：删 `carouselOverrides` 死代码形参（P2-7），改为挂 `carousel` 到 wrapper 便于测试驱动 pushInterrupt；删未用的 `IslandLane` / `computed` import。

### T1.5 补齐 P2-1 / P2-5 / P2-8 App.spec 测试
- **P2-1 completed 直达**：新增 "038 P2-1: completed 通知 read:true，点 pill 直达结果页（不展开 panel）" 用例。
- **P2-5 history 暂停消费**：新增 "038 P2-5: history 浏览期间 notify 暂停消费，回最新时 flush" 用例（fake timers 推进 2200ms 验证沉入）。
- **P2-8 reminders 行点击分流**：新增 "038 P2-8: 投递提醒打断沉入 panel 后行点击直达 reminders（开提醒抽屉）" 用例（reminder 0→3 → sink → 行点击 → reminder-drawer 出现）。
- **P1-2 修复连带回归**：4 条旧测试（"island catches notices first" / "mutually exclusive" / "switching profile resets" / "completed capsule never feeds reminder badge"）原用 emitRoundTransition（completed 现在 read:true 不再产生未读）失效 → 改用 `emitAttentionTransition`（paused 仍 read:false 是仅剩的 panel 未读来源），更新 testid 选择器（`dynamic-island-completed` → `dynamic-island-attention`，`island-notice-row-completed` → `island-notice-row-paused`，文案"匹配 5" → "任务已暂停"）。新增 `emitAttentionTransition` 助手。

### P1/P2 修复清单（源码已完成，详见 spec.md / plan.md「复审裁决」小节）
- **P1-1** 打断沉入改 append（不再 upsert 互相吞掉）——`useIslandNotices.sinkInterrupt` + `useIslandCarousel.pushInterrupt` 切到最新语义。
- **P1-2** 文档对齐两色（CHANGELOG/README）+ 删 panel 四色 chip 死代码（`IslandNoticePanel.vue`）。
- **P1-3** pill 宽度 spring 实现（`DynamicIsland.vue` trackEl + pillWidth + widthSpring + contentKey + watch + max-width）。
- **P2-1** completed read:true（`useIslandNotices.processTransition`）。
- **P2-2/P2-3** carousel 状态机：只转最新 + sticky 不卡角标（`useIslandCarousel.pushInterrupt`）。
- **P2-4** pill max-width: calc(100vw - 32px) 兜底（`DynamicIsland.vue` CSS）。
- **P2-5** history 暂停消费（`App.vue` pushInterruptOrDefer + watch scope flush）。
- **P2-6**（如有，详见复审报告）已落地。
- **P2-7** DynamicIsland.spec carouselOverrides 死代码清理。
- **P2-8** reminders 行点击分流（`App.vue` handleIslandNavigate + `useIslandNotices.IslandNoticeTarget` + `useIslandCarousel.IslandInterruptContent.target` + `DynamicIsland.vue` CapsuleTarget）。
