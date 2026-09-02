# 037 灵动岛 v2 + 提醒按钮回退 · Tasks

执行顺序：批次 1（接住全局提醒）→ 批次 2（提醒按钮回退）。两批次同 PR 内分两个提交。

## 批次 1 — 灵动岛接住全局提醒

### T1.1 新增 `webui/src/composables/useIslandNotices.ts`
- 导出 `IslandNotice`、`IslandNoticeKind`、`createIslandNotices`；
- 内部 `watch(roundStatus.value?.capsule, ...)` 派生通知；
- 同 kind 替换；进 running/idle 清空；
- 初始 prev=null 时不产通知；
- 测试：`webui/src/composables/__tests__/useIslandNotices.spec.ts`：跑完/出错/暂停三类各自产生；多类并存；进 running 清空；初始观察不产；markRead/markAllRead/reset。

### T1.2 重写 `webui/src/components/DynamicIsland.vue`
- props：`status: CapsuleStatusPayload | null`、`notices: IslandNotice[]`；
- emits：`navigate(target)`、`expand()`；
- 暴露 `collapse()` via defineExpose；
- 内部 open ref；首次进入 open=true 视为"点开"，emit expand 一次；
- 渲染：胶囊（Motion）+ IslandNoticePanel（v-if=open 时挂载；position absolute 在胶囊下方）；
- 胶囊内容：根据 capsule.state 渲染（沿用 036 文案口径）；
- 动画：
  - 胶囊外壳 Motion whileHover scale 1.03 / whilePress scale 0.96（reduced→duration 0）；
  - 内容 :key 切换触发入场动画（scale 1.1→1 + opacity 0→1，弹簧 280ms）；
  - 新通知到达：岛整体弹动 keyframes（CSS，~350ms）；
  - 呼吸：CSS keyframes，reduced 关闭。
- 主题：明暗令牌；kaleido 半透明 + blur 6px；
- 测试：四态文案、新通知 :key、expand 事件、collapse() 方法、kaleido data-theme。

### T1.3 新增 `webui/src/components/IslandNoticePanel.vue`
- props：`notices: IslandNotice[]`、`status: CapsuleStatusPayload | null`（用于显示当前平台角标等可选项，本批次可仅传 notices）；
- emits：`row-click(notice)`；
- 渲染：每条通知一个 `.island-notice-row`：kind 对应小图标（绿/红/橙）+ title + detail + 前往；未读高亮（左侧 2px 边框）；
- 容器：固定 max-width 380，圆角 13，var(--panel) 背景，var(--shadow)；
- 交互：row click → emit row-click；
- 主题：kaleido 半透明 + blur；
- 测试：渲染 kind、mark-read 行为（父传入 notices 列表已 read 状态时显示淡化）、row-click 事件。

### T1.4 接线 `webui/src/App.vue`
- 引入 `createIslandNotices`、`useReminderBadge`（本批次仅引入前一个；useReminderBadge 在批次 2 引入）；
- `const islandNotices = createIslandNotices(roundStatus)`；
- `const islandRef = ref<...>()`；
- `handleIslandExpand` 关闭三个 drawer；
- `toggleReminderDrawer`/`toggleFavorites`/`closeHistoryDrawer` 各自调 `collapseIsland()`；
- `<DynamicIsland ref="islandRef" :status="roundStatus" :notices="islandNotices.notices" @navigate="handleCapsuleNavigate" @expand="handleIslandExpand" />`；
- `watch(currentProfileId)` 增加 `islandNotices.reset()`。

### T1.5 增强 `webui/src/test/setup.ts`
- matchMedia 替身按 query 分流：
  - `(prefers-reduced-motion: reduce)` → 默认 matches=true；
  - `(max-width: 760px|1050px|1300px)` → 由 `__setNarrowMatchMedia` 控制；
  - 其它 → matches=false。

### T1.6 App 测试更新
- `webui/src/__tests__/App.spec.ts`：调整 036 is-land 测试（断言新 API、新 click 行为）；新增：
  - "岛点击展开面板（有通知时）"；
  - "面板/抽屉互斥：开面板关三 drawer；开抽屉关面板"；
  - "profile 切换重置通知池"；
  - "岛点击无通知时直达目标（running 跳 task）"。

### T1.7 验证门禁（批次 1 后）
- `cd webui && npm run test`：全部通过；
- `cd webui && npm run build`：vue-tsc + vite 构建通过；
- `uv run python -m unittest tests.test_repo_hygiene`：通过；
- 不动后端；不跑后端全量。

## 批次 2 — 提醒按钮回退 + Bug 根治

### T2.1 新增 `webui/src/composables/useReminderBadge.ts`
- 函数 `useReminderBadge(currentProfileId)`：
  - 内部 `reminderTotal` ref；
  - 内部 `refreshReminderCount()`（沿用 App.vue 472-492 逻辑，含 seq 守卫）；
  - 内部 watch currentProfileId：reset + refresh；
  - 返回：`reminderTotal`、`badgeText`（≥100 = "99+"）、`ariaLabel`（"查看提醒，共 N 个逾期岗位"；total=0 时简化为"查看提醒"）、`refreshReminderCount`；
- 测试：`webui/src/composables/__tests__/useReminderBadge.spec.ts`：total=0 / 3 / 137；99+ 截断；profile 切换 seq 守卫。

### T2.2 App.vue 删除 reminderBreakdown 通用化块
- 删除 494-530（含 reminderBreakdown / reminderTotalAll / reminderBadgeText / reminderAriaLabel）；
- 替换为：
  ```ts
  const reminderBadge = useReminderBadge(currentProfileId);
  function handleJobFeedbackChanged(payload?: { profileId?: string }) {
    if (payload?.profileId && payload.profileId !== currentProfileId.value) return;
    void reminderBadge.refreshReminderCount();
    void loadFavorites();
  }
  ```
- 模板 627-632 角标回单源：
  ```html
  <em v-if="reminderBadge.reminderTotal.value > 0"
      class="fav-badge reminder-badge"
      data-testid="reminder-badge"
      aria-hidden="true">{{ reminderBadge.badgeText.value }}</em>
  ```

### T2.3 App 测试断言更新
- 角标测试断言改回：badge text=数字（无合成）、aria 含 "查看提醒，共 N 个逾期岗位" 或 "查看提醒"；
- 新增："投递=0、任务跑完（completed+pending=2）→ 角标=0、抽屉空态"覆盖回归原 Bug。

### T2.4 验证门禁（批次 2 后）
- `npm run test`、`npm run build`、仓库卫生检查均通过；
- `wc -l webui/src/App.vue` ≤ 800（净减目标）。

## 收口
- CHANGELOG.md 追加本版本未发布条目（按 AGENTS 写作规范）；
- `.specify/memory/constitution.md` 模块地图追加 4 行；
- README 校对（如有需要）；
- 不提交、不推送、不打包（用户未授权）。