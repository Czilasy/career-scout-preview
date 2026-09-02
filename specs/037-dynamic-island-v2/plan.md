# 037 灵动岛 v2 + 提醒按钮回退 · Plan

## 文件边界（宪法 III、IV、VI）

### 允许修改
| 路径 | 改动 |
|---|---|
| `webui/src/App.vue` | 接线通知池与面板；面板/抽屉互斥；删除 reminderBreakdown 通用化块；导入新组件。行数净减。 |
| `webui/src/components/DynamicIsland.vue` | 全文重写为 motion-v 驱动：常驻胶囊 + 弹出面板 + 暴露 collapse() + expand 事件。 |
| `webui/src/components/__tests__/DynamicIsland.spec.ts` | 改为新 API 断言（传入 notices 列表、四态/优先级/点击展开/暴露 collapse）。 |
| `webui/src/__tests__/App.spec.ts` | 角标断言改回投递单源；新增面板展开/互斥/通知池用例。 |
| `webui/src/test/setup.ts` | matchMedia 替身按 query 分流；reduced 默认 true。 |
| `webui/package.json`、`webui/package-lock.json` | 增加 motion-v ^2.4.0（已 npm install 完成）。 |
| `.specify/memory/constitution.md` | 模块地图追加 4 行新文件登记。 |
| `CHANGELOG.md` | 新增本版本未发布条目（按 AGENTS 写作规范）。 |
| `README.md` | 顶部图片与功能清单校对（如有需要）。 |

### 新增
| 路径 | 职责 |
|---|---|
| `webui/src/composables/useIslandNotices.ts` | 通知池：消费 `roundStatus` 派生四类通知 + 已读/重置 API |
| `webui/src/composables/useReminderBadge.ts` | 抽取并回退角标状态：reminderTotal / badgeText / ariaLabel / refresh / reset |
| `webui/src/components/IslandNoticePanel.vue` | 通知面板：常驻底渲染，列表通知行 + 顶部提示；绝对定位在胶囊下方 |

### 禁止修改
| 路径 | 原因 |
|---|---|
| `webui/src/views/DiscoveryView.vue` | 1249 行超 Vue 1200 红线，禁改 |
| `webui/src/composables/useDiscoveryState.ts` | 1267 行 TS；036 派生体保持不变 |
| `webui/src/jobFeedback.ts` 与所有 `webui/*.py`、`scripts/`、`roadmap/` 之外的 Python | 后端 API 合同不变 |
| `webui/src/components/ReminderDrawer.vue` | 抽屉组件自身合规，本批次不动 |
| `webui/src/api.ts` 等前端 API 客户端 | 不改接口与端点 |
| 任何新增/修改后端 endpoint | 显式禁止 |

### 引用方向（新增文件遵守 view → composables → api/client）
- `useIslandNotices.ts` → 仅依赖 `vue` 与 `useDiscoveryState` 的类型导入；不发请求。
- `useReminderBadge.ts` → 仅依赖 `vue` 与 `../jobFeedback` 的 `getJobReminderCount`；不动 API 形状。
- `DynamicIsland.vue` / `IslandNoticePanel.vue` → 组合 new composables + motion-v + lucide。

### 行数门禁
- 任何文件不突破宪法 II：Python ≤ 800、Vue ≤ 1200。
- App.vue 当前 852 行（>900 预警线 75%）；本批次净减目标 ≥ 50 行（回退块删除 + 抽取）。
- 新文件保持轻量：useIslandNotices ≤ 200、useReminderBadge ≤ 150、IslandNoticePanel ≤ 250、DynamicIsland ≤ 400（结构 + motion-v 动画 prop 较多）。

## 数据流

```
DiscoveryView (emit round-status)
  ↓ CapsuleStatusPayload
App.roundStatus (ref)
  ↓ watch (roundStatus.value?.capsule)
useIslandNotices.notices / unreadCount
  ↓ :notices
DynamicIsland (props) + IslandNoticePanel (props)
  ↓ click on row
emit(row-click)
App → requestCapsuleNavigation (useDiscoveryState.watch 866)
DiscoveryView → 视图跳转
```

## 接线变更点（App.vue 区域）

1. 替换角标合成块（删除 494-530）：
   ```vue
   const reminderBadge = useReminderBadge(currentProfileId);
   ```
2. 删除 627-632 通用化徽标模板：
   ```html
   <em v-if="reminderBadge.reminderTotal.value > 0"
       class="fav-badge reminder-badge"
       data-testid="reminder-badge"
       aria-hidden="true">{{ reminderBadge.badgeText.value }}</em>
   ```
3. 新增岛接线：
   ```ts
   const islandNotices = createIslandNotices(roundStatus);
   const islandRef = ref<InstanceType<typeof DynamicIsland> | null>(null);
   const collapseIsland = () => islandRef.value?.collapse?.();
   function handleIslandExpand() { favoritesOpen = false; closeHistoryDrawer(); reminderDrawerOpen = false; }
   // 三个 drawer toggle 中各加一行 collapseIsland()。
   ```
4. 模板：
   ```html
   <DynamicIsland ref="islandRef"
     :status="roundStatus" :notices="islandNotices.notices"
     @navigate="handleCapsuleNavigate" @expand="handleIslandExpand" />
   ```
5. 面板挂载：
   ```html
   <component :is="islandNotices.notices" /> <!-- no -->
   ```
   实际：面板作为 DynamicIsland 内部子组件由其 own 渲染；App 仅需提供 collapse 入口。
   或：DynamicIsland 渲染 IslandNoticePanel（内部子组件），无需 App 额外挂载。
6. `handleJobFeedbackChanged` 调用 `reminderBadge.refreshReminderCount()`。
7. `watch(currentProfileId)` 增加 `islandNotices.reset()`。

## 动画策略

- **入场**：用 `<Motion>` 组件 + `:initial`/`:animate`。
- **退出**：避免 AnimatePresence（jsdom 退出动画结束不可靠）。用 v-if 直接卸载 + 胶囊自身弹簧回弹补偿。
- **数字**：`<Motion :key="label">` 触发重渲染入场；旧值由 Vue 自然移除（无 exit anim）。视觉上是"数字切换"。
- **弹动**：`@keyframes island-bounce` (scale 1→1.12→1, 350ms) 在 `unreadCount > 0` 且新通知到达时触发；通过 `:key` 变化或单独 class 切换。
- **呼吸**：CSS keyframes（岛 idle），respect reduce-motion（已有 DynamicIsland.vue:213-222 处理）。
- **reduce-motion**：`useReducedMotion()` 短路，`:initial="reduced ? undefined : {...}"`
- **测试兼容**：setup.ts 让 prefers-reduced-motion 在测试里 matches=true → Motion 初始动画等价无 → 元素直接渲染最终态。

## 数据结构

### useIslandNotices 形状

```ts
export type IslandNoticeKind = "completed" | "error" | "paused";
export interface IslandNotice {
  id: string;            // 内部唯一
  kind: IslandNoticeKind;
  title: string;
  detail?: string;
  target: "task" | "results" | "attention";
  at: number;
  read: boolean;
}
export interface IslandNoticesApi {
  notices: Ref<IslandNotice[]>;
  unreadCount: ComputedRef<number>;
  markAllRead(): void;
  markRead(id: string): void;
  reset(): void;
}
export function createIslandNotices(roundStatus: Ref<CapsuleStatusPayload | null>): IslandNoticesApi;
```

### 跃迁规则

| prev.state | next.state | 行为 |
|---|---|---|
| 任意非 undefined | `running` | 清空池 |
| 任意非 undefined | `idle` | 清空池 |
| `running` | `completed` | spawn "completed" |
| 任意 | `attention` kind=error | spawn "error"（替换已有 error） |
| 任意 | `attention` kind=paused | spawn "paused"（替换已有 paused） |
| 同 state 内部数字/详情变化 | 同 | 不产生新通知，已存在通知的 detail 更新 |
| 初始观察（prev==null） | 任意 | 仅记录 prev 不产生通知 |

池最多 1 条/同 kind；条目无自动过期；reset() 清空。

## 里程碑（先接住，再回退；同批次合入，但合入顺序写入 CHANGELOG 与 git 提交拆 2 个提交）

### 批次 1 — 岛接住全局提醒
- 新增 useIslandNotices、IslandNoticePanel；
- DynamicIsland 重写（motion-v 动画）；
- App.vue 接线通知池与面板/抽屉互斥；
- 测试（useIslandNotices、IslandNoticePanel、DynamicIsland 新 API、App 接线）。

### 批次 2 — 提醒按钮回退 + Bug 根治
- 新增 useReminderBadge（删除原 reminderBreakdown/ariaLabel/badgeText）；
- App.vue 模板角标回单源；
- 测试（角标 aria、内容；投递=0+任务跑完场景不出现数字）。

两批次同 PR；提交信息按 Conventional Commits 分别：`feat(island): wire island notice pool + motion-v animations` 与 `fix(reminder): revert badge to delivery-only`.

## 验证门禁
- 聚焦测试 + 前端全量测试 + `npm run build` + 仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene`）。
- 后端不动；本批次不跑后端全量（仅前端 + setup + spec 文件改动）。