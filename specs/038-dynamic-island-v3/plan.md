# 038 灵动岛 v3 · live 仪表盘 + 转盘轮播 + toast 折叠 · Plan

## 文件边界（宪法 III、IV、VI + 路径 3 红线例外）

### 允许修改
| 路径 | 改动 |
|---|---|
| `webui/src/App.vue` | 净减：删 TaskCompletedToast 相关接线（若有）；NoticeBar warning/error 分流到 island 打断队列；wire useIslandCarousel；保留 037 互斥/collapse/reset 接线。行数净减或持平。 |
| `webui/src/components/DynamicIsland.vue` | 重写：pill 内 vertical spring carousel（lane 0=live state pinned / lane 1+=interrupt 队列）+ 宽度 spring 自适应 + 四色芯片 + 红光层 + 角标。保留 037 的 panel/dismiss/navigate/aria/互斥。MUST ≤ 1200（宪法 II）。 |
| `webui/src/composables/useIslandNotices.ts` | 改 running 态不再 `clearAll()`（修 L93），改为产出 live state 数据供 carousel mainLane 消费；completed detail 扩展四元（matched/unmatched/uncertain/dropped）；终态事件仍沉 panel 历史。 |
| `webui/src/components/IslandNoticePanel.vue` | 适配：四色分类展示（若 panel 也展示完成分类）；打断来源条目样式。行数小增。 |
| `webui/src/test/setup.ts` | carousel translateY 测试桩（motion-v spring 在 jsdom 无物理，桩为瞬时）；沿用 037 的 WAAPI/matchMedia 桩。 |
| `webui/src/views/DiscoveryView.vue` | **仅路径 3 红线例外**：删 L15 import + L192 解构项 + L1118-1122 toast 元素（净 -7 行）。不动其余任何逻辑。 |
| 各测试文件 | useIslandCarousel.spec.ts 新增；useIslandNotices/DynamicIsland/App spec 更新；TaskCompletedToast.spec.ts 删除。 |
| `.specify/memory/constitution.md` | 模块地图：登记 useIslandCarousel.ts 新文件；TaskCompletedToast.vue 标记删除（035 登记 → 038 移除）。 |
| `CHANGELOG.md` | `[未发布]` 038 条目（按 AGENTS 规范）。 |
| `README.md` | 灵动岛 bullet 更新为"live 仪表盘 + 转盘轮播"。 |

### 新增
| 路径 | 职责 |
|---|---|
| `webui/src/composables/useIslandCarousel.ts` | carousel 状态机：lanes / activeLaneIndex / badgeCount / pushInterrupt(lane) / dismissActive() / reset() / mainLaneState。mainLaneState 绑定 roundStatus 实时更新（不冻结，硬不变式）。自设 ≤ 250 行。 |

### 删除
| 路径 | 原因 |
|---|---|
| `webui/src/components/TaskCompletedToast.vue` | 035 遗留；功能由 island completed pill + navigate 接管（路径 3）。 |
| `webui/src/components/__tests__/TaskCompletedToast.spec.ts` | 组件已删，测试随之删。 |

### 禁止修改
| 路径 | 原因 |
|---|---|
| `webui/src/composables/useDiscoveryState.ts` | 1267 行 TS；036 派生保持不变；`taskCompletedToast` ref 保留（删 toast 元素后无害空写）。 |
| `webui/src/views/DiscoveryView.vue`（除路径 3 的 7 行）| 1223 行超 Vue 1200 红线；路径 3 仅授权删那 7 行，其余禁改。 |
| `webui/jobFeedback.ts` 与所有 `webui/*.py`、`scripts/` | 后端 API 合同不变 |
| `webui/src/components/ReminderDrawer.vue` | 抽屉组件自身合规，本批次不动 |
| `webui/src/api.ts` 等前端 API 客户端 | 不改接口与端点 |
| 任何新增/修改后端 endpoint | 显式禁止 |

### 引用方向（新增文件遵守 view → composables → api/client）
- `useIslandCarousel.ts` → 仅依赖 `vue` + `useDiscoveryState` 的类型导入（`CapsuleStatusPayload`/`DynamicIslandState`）；不发请求；不反向依赖 view。
- `useIslandNotices.ts`（改）→ 沿用 037 依赖（vue + useDiscoveryState 类型）；新增产出 live state 数据给 carousel。
- `DynamicIsland.vue`（重写）→ 组合 useIslandCarousel + useIslandNotices + motion-v + lucide；不直接发请求。

### 行数门禁
- 宪法 II 硬线：Python ≤ 800、Vue ≤ 1200。DiscoveryView.vue 删 7 行后 1216（仍超 1200，但本批次只减不增；彻底拆分留原则 IV 专项 Spec）。
- DynamicIsland.vue 037 终态 662 行；038 重写 carousel+live+四色+红光增量——MUST ≤ 1200；自设目标 ≤ 900（预警线 75%）。
- useIslandCarousel.ts 自设 ≤ 250。
- App.vue 844 行；038 净减（删 toast 接线 + NoticeBar 分流简化）目标持平或 -10。

## 数据流

```
DiscoveryView (emit round-status)
  ↓ CapsuleStatusPayload (含 capsule.progress / TaskSnapshot 字段)
App.roundStatus (ref)
  ├─ useIslandNotices (改) → 产出 liveState + 终态 notice 历史
  │     ↓ liveState（phase/done/total/counts/glow）
  │     ↓ notices（终态历史，含 interrupt 沉入）
  └─ useIslandCarousel (新) → mainLaneState = liveState 实时（不冻结）
        ↓ + interruptQueue（投递提醒 + NoticeBar warning/error 推入）
        ↓ activeLaneIndex / translateY / badgeCount
DynamicIsland (props: carousel state + notices)
  ├─ pill 主体 = carousel viewport（lane 0 live / lane 1+ interrupt）
  ├─ 红光层（attention 时 data-glow）
  └─ IslandNoticePanel（点开看终态 + 打断历史）
        ↓ click row
  emit(navigate) → requestCapsuleNavigation → DiscoveryView 视图跳转
```

**硬不变式落地**：`useIslandCarousel` 的 `mainLaneState` 是 computed，直接读 `roundStatus.capsule`——打断展示期间 activeLaneIndex 指向 interrupt lane，但 mainLaneState computed 照常重算（Vue 响应式不停），转回 lane 0 时 pill 立即拿到最新数字。

## 接线变更点

### App.vue
1. wire carousel：
   ```ts
   const islandCarousel = useIslandCarousel(roundStatus);
   const islandNotices = createIslandNotices(roundStatus); // 已有，038 改其内部
   ```
2. 删 TaskCompletedToast 相关（App 侧若有 import/挂载——经核查 App.vue 当前无 TaskCompletedToast import，toast 渲染在 DiscoveryView；App 仅需确认无残留）。
3. NoticeBar 分流：现有 `<NoticeBar :notice="notice" @dismiss="dismissNotice" />`（L759）→ 改为：notice.tone 为 warning/error 时推入 `islandCarousel.pushInterrupt(...)` 并不渲染 NoticeBar；info/success 仍渲染 NoticeBar。
4. 投递提醒触发打断：`useReminderBadge` 的 reminderTotal 由 0→N 时（watch）推入 `islandCarousel.pushInterrupt({title:"投递提醒", detail: N+"条逾期", tone:"warning"})`。
5. 模板：
   ```html
   <DynamicIsland ref="islandRef"
     :status="roundStatus" :notices="islandNotices.notices"
     :carousel="islandCarousel"
     @navigate="handleCapsuleNavigate" @expand="handleIslandExpand"
     @dismiss="handleIslandDismiss" />
   ```
6. `watch(currentProfileId)`：已有 `islandNotices.reset()`；新增 `islandCarousel.reset()`。

### DiscoveryView.vue（路径 3 红线例外，仅 7 行）
- 删 L15：`import TaskCompletedToast from "../components/TaskCompletedToast.vue";`
- 删 L192：`  taskCompletedToast,`（多行解构项）
- 删 L1118-1122：`<TaskCompletedToast ... />` 元素

### DynamicIsland.vue（重写核心）
- pill 外壳：`overflow:hidden` viewport，高度 1 行（~36px），宽度 `width: auto; max-width: calc(100vw - 32px)` + motion-v spring 宽度过渡。
- carousel 内层：`<Motion :animate="{ y: -activeLaneIndex * lineHeight }" :transition="spring">`，lane 绝对定位堆叠。
- lane 0（main）：渲染 IslandLiveState——running 显示 `正在抓取 N/M`（蓝）/`AI精筛 N/M`（紫）+ 可选子计数；completed 显示四色芯片；attention 叠红光层；idle 呼吸。
- lane 1+（interrupt）：渲染 `{title · detail}`，tone 染色（warning 琥珀/error 红）。
- 角标：pill 右上小圆 `{{ badgeCount }}`，badgeCount++ 时 WAAPI 弹一下（沿用 037 playPop）。
- 红光层：`<div v-if="glow" class="island-glow" :data-glow="glow">`，CSS keyframes subtle pulse。
- 保留 037：panel 展开/dismiss snapshot/互斥/tab trap/Teleport backdrop/navigate/aria-live。

## 动画策略

- **carousel translateY**：motion-v `<Motion :animate="{ y: ... }" :transition="{ type:'spring', stiffness:300, damping:26 }">`。spring 有 overshoot 才"弹"。reduce-motion：`useReducedMotion()` 短路，transition 设 `{duration:0}`，y 直接设值。
- **pill 宽度**：`<Motion :animate="{ width: contentWidth }" :transition="spring">`；contentWidth 由内容测量（ResizeObserver 或字数估算）。reduce-motion 瞬时。
- **数字跳动**：`<Motion :key="done" :initial="{y:8,opacity:0}" :animate="{y:0,opacity:1}">` 旧值上滑淡出/新值下滑淡入（沿用 037 SC-002）+ WAAPI 弹一下。
- **角标弹**：badgeCount 变化时 `playPop()`（WAAPI，沿用 037）。
- **红光**：CSS `@keyframes island-glow`（subtle opacity/scale pulse，非刺眼），`@media (prefers-reduced-motion: reduce)` 静止。
- **呼吸**：沿用 037 `island-idle-breathe` keyframes。
- **退出**：避免 AnimatePresence（jsdom 退出动画不可靠）；lane 切换用 y translate 不卸载；panel 退场沿用 037 两阶段 leaving。
- **测试兼容**：setup.ts 让 motion-v spring 在 jsdom 退化为瞬时（reduced-motion matches=true 默认）；carousel translateY 测试断言用最终 y 值不依赖动画过程。

## 数据结构

### IslandLiveState（pill lane 0 内容，派生自 roundStatus.capsule）
```ts
export interface IslandLiveState {
  phase: "scraping" | "screening" | "completed" | "idle" | "attention";
  done?: number;          // running 态 progress.done
  total?: number;         // running 态 progress.total（未知省略）
  sub?: { label: string; value: string }[];  // 子计数（JD详情/失败/未启/粗筛剔除/重试等）
  counts?: { matched: number; unmatched: number; uncertain: number; dropped: number }; // completed 四元
  glow?: "error" | "paused" | "none";        // attention 红光
  platform: Platform;
}
```

### IslandLane（carousel 一格）
```ts
export interface IslandLane {
  id: string;
  type: "main" | "interrupt";
  content: IslandLiveState | { title: string; detail?: string; tone: "warning" | "error" };
  duration?: number;  // interrupt 自动转回毫秒（默认 2200）
  sticky?: boolean;   // interrupt 是否需手动 dismiss（error 可 sticky）
}
```

### useIslandCarousel API
```ts
export interface IslandCarouselApi {
  activeLaneIndex: Ref<number>;        // 0=main, 1+=interrupt
  lanes: ComputedRef<IslandLane[]>;    // mainLane + interruptQueue
  badgeCount: ComputedRef<number>;      // = interruptQueue 长度（未读打断数）
  mainLaneState: ComputedRef<IslandLiveState>;  // 绑定 roundStatus 实时（不冻结）
  pushInterrupt(lane: Omit<IslandLane, "id" | "type">): void;  // 推入队列，若当前在 lane 0 则转一次
  dismissActive(): void;               // 粘性打断手动 dismiss，转回 lane 0
  reset(): void;                        // 清队列，回 lane 0
}
export function useIslandCarousel(roundStatus: Ref<CapsuleStatusPayload | null>): IslandCarouselApi;
```

### 转一次逻辑（pushInterrupt）
1. 推入 interruptQueue（FIFO）。
2. 若 activeLaneIndex === 0：设 activeLaneIndex = queue 末尾 index，触发 carousel translateY 弹上去 + playPop()。
3. 若该 interrupt 非 sticky：setTimeout(duration) 后 activeLaneIndex 回 0，该条从 queue 移除、沉入 notice panel 未读。
4. 若 sticky（error）：不自动转回，等 dismissActive()。
5. **多条积压**：只转最新一条一次（pushInterrupt 时若 activeLaneIndex 已非 0，不重复触发 translateY，只入队+角标++）。

### useIslandNotices 改动
- L93 `if (next.state === "running" || next.state === "idle") { clearAll(); }` → running 不再 clearAll，改为更新 liveState（供 carousel mainLane 消费）；idle 仍 clearAll（无主流程）。
- `completedDetail`（L43-47）扩展返回四元 `{matched, unmatched, uncertain, dropped}`，不只 matched+pending。
- 终态事件（completed/error/paused 跃迁）仍 upsert 进 notices 列表（panel 历史）。
- 新增：interrupt 沉入——carousel 转完一条打断后，调 `upsert` 把它作为 `kind:"interrupt"` 加入 notices（未读，进 panel）。

## 里程碑（单批次合入，提交按 Conventional Commits 拆 2）

### 批次 1 — live 仪表盘 + 四色 + 转盘轮播
- 新增 useIslandCarousel；
- 改 useIslandNotices（running 不 clearAll + 四元 completed + interrupt 沉入）；
- 重写 DynamicIsland（carousel + spring 宽度 + 四色 + 红光 + 角标）；
- App.vue wire carousel + NoticeBar 分流 + 投递提醒推打断；
- 测试（useIslandCarousel、useIslandNotices 改、DynamicIsland carousel/四色/红光、App 分流）。

### 批次 2 — toast 折叠删除（路径 3）
- DiscoveryView.vue 删 7 行（L15+L192+L1118-1122）；
- 删 TaskCompletedToast.vue + TaskCompletedToast.spec.ts；
- App/DynamicIsland spec 更新（toast 不存在断言 + completed navigate 接管断言）。

两批次同 PR；提交：`feat(island): live dashboard + turntable carousel + four-color` 与 `chore(island): fold TaskCompletedToast into island (path 3 exception)`。

## 验证门禁
- 聚焦测试：useIslandCarousel.spec.ts（新增）、useIslandNotices.spec.ts（改）、DynamicIsland.spec.ts（carousel/四色/红光/宽度/badge/转回数字推进）、App.spec.ts（toast 删/navigate 接管/NoticeBar 分流）。
- 前端全量测试：vitest run。
- `npm run build`：vue-tsc 严格通过 + vite 构建成功。
- 仓库卫生：`uv run python -m unittest tests.test_repo_hygiene`。
- 后端不动；不跑后端全量。
- 用户端到端走查在交付后进行。

## 实现期偏差（2026-09-03 复审后落地）

复审在完整 Spec Kit 流程后的独立全量审查中发现若干规格/伪代码与实现约束的偏差，记录如下（spec 原文保留作为设计意图快照，裁决细节见 spec.md「复审裁决与降级」小节）：

### App.vue 行数偏差（原"持平或 -10" → 实际 +83）
- **原计划**：App.vue 844 行，038 净减（删 toast 接线 + NoticeBar 分流简化）目标持平或 -10。
- **复审前**：844 → 894（+50，复审报告 P2-6 记录）——NoticeBar 分流 + carousel reset + reminder 0→N watch + handleIslandDismiss 等接线本身就有体量。
- **复审后**：894 → 927（+33）——P2-5（history 暂停消费）新增 `historyPendingInterrupts` 数组 + `pushInterruptOrDefer` 函数 + `watch(roundStatus.scope)` flush + profile reset 清缓冲；P2-8（reminders 分流）新增 `handleIslandNavigate` 分流函数 + reminder watch target 改 "reminders"。
- **总计**：844 → 927（+83）。**未破红线**：927 远低于 Vue 1200 硬线；App.vue 不在自设预警线内。偏差可接受，但确属"plan 自设目标失守"——复审在 P2-6 已指出，本批次接受此偏差（边角需求 P2-5/P2-8 用户已要求全修复，行数增长是必要代价）。

### useIslandNotices completed read:true 偏差（原"扩展四元 detail" → 实际 completed read:true）
- **原计划**：`completedDetail`（L43-47）扩展返回四元 `{matched, unmatched, uncertain, dropped}`。
- **实际**：`completedDetail` 保留 037 行为（只返回 `匹配 N · 待确认 P`），不扩展四元——capsule 禁改，无四元数据源（见 spec.md P1-2 裁决）。新增的是 completed upsert 设 `read: true`（P2-1 裁决）。
- **影响**：completed pill 只展示两色 chip；panel completed 行只显示 detail 文字（删四色 chip 死代码）。
- **行数**：useIslandNotices.ts 214 行（037 终态 + sinkInterrupt append + IslandNoticeTarget 增 "reminders" + 头注释）。原 037 自设 ≤200，038 略超但在合理范围。

### pushInterrupt 伪代码修正（"只转第一次" → "切到最新一条"）
- **原伪代码**：
  ```
  5. 多条积压：只转最新一条一次（pushInterrupt 时若 activeLaneIndex 已非 0，不重复触发 translateY，只入队+角标++）。
  ```
- **实际语义**（spec US-3 acceptance + plan 伪代码"activeLaneIndex = queue 末尾"定案）：
  - pushInterrupt 时若 active 非 sticky → activeLaneIndex 切到新 push 的（"切到最新"）；
  - active 非 sticky → 旧的留在队列由各自 timer 沉入 panel（**不**提前退场、**不**自动轮播）；
  - active sticky + new 非 sticky → 只入队+timer（不抢位）；
  - active sticky + new sticky → 直接 sink(new) 沉入 panel（P2-3，不滞留卡角标）。
- **澄清**：US-3"多条积压只转最新一条"指**被动响应新打断**的切换，**不**是"自动轮播"——dismiss 后不补转下一条。原伪代码"只转第一次，不重复触发 translateY"的描述容易误读为"第一次后不切换"，实际是"每次 push 都切到最新"，只是不重复 trigger carousel 动画（activeLaneIndex 变化即触发，无需额外 trigger）。

### DynamicIsland.vue 行数（原"≤900 自设" → 实际 856）
- 037 终态 656 行；038 重写后实测 **856 行**（复审前 731 → 复审后 856，+125：宽度 spring trackEl/pillWidth/widthSpring/contentKey/watch + 数字弹动 labelStack + reminders target + Motion as button + max-width + 测试钩子 data-pill-width）。低于自设 900 预警线，远低于 1200 硬线。可接受。

### useIslandCarousel.ts 行数（原"≤250 自设" → 实际 215）
- 实测 **215 行**（含 P2-3 sticky 不滞留 + armSinkTimer 抽出 + IslandInterruptContent.target 增 reminders + 头注释）。低于自设 250。可接受。

### IslandNoticePanel.vue 行数（原 330 → 实际 299）
- 复审前 330 行；复审后 299 行（-31）：删 panel 四色 chip 渲染死代码（`<span v-if="notice.counts" class="notice-chips">` 块 + `v-else-if` 改 `v-if`）+ 删 `.notice-chips`/`.notice-chip`/`.c-green/.c-gray/.c-amber/.c-red` CSS + kaleido chips CSS。低于 1200 硬线。可接受。

### 测试缺口补齐（tasks T1.3 承诺的组件级 carousel 测试未兑现 → 补齐）
- **原 tasks T1.3** 承诺"DynamicIsland.spec 加 carousel 转一次/四色/红光/宽度弹/badge/转回数字推进"组件级测试，但实现期只跑了四态/点击/通知池联动 34 个用例（无组件级 carousel 测试）。
- **复审补齐**：DynamicIsland.spec 新增 "DynamicIsland 038 组件级 carousel + 宽度 spring + 数字弹动" describe 块 8 条用例（pushInterrupt 渲染 + activeLaneIndex 切换 / FR-011 转回主流程数字推进 / badgeCount playPop / 数字 playPop / labelStack 旧值淡出 / 宽度 spring data-pill-width / P2-1 completed 直达 / reduce-motion carousel 退化）。总数 34 → 42。
- **App.spec 补齐**：原 tasks T1.5 未提 P2-1/P2-5/P2-8 测试，复审新增 3 条（P2-1 completed 直达 / P2-5 history 暂停消费 flush / P2-8 reminders 行点击开抽屉）；并修复 P1-2 修复导致的 4 条旧测试失效（emitRoundTransition → emitAttentionTransition，因为 completed 现在 read:true 不再产生未读）。总数 38 → 41。

### P2-7 · DynamicIsland.spec carouselOverrides 死代码清理
- **原 mountIsland 助手**有 `carouselOverrides?: { pushInterrupt?: ... }` 形参，用于包装 pushInterrupt 加前置 hook——但 34 个旧用例无一会传此参数，属死代码。
- **复审清理**：删除 `carouselOverrides` 形参；mountIsland 改为直接挂 `carousel` 到 wrapper（`wrapper.carousel.pushInterrupt(...)` 驱动），更直观。同步删未用的 `IslandLane` / `computed` import。
