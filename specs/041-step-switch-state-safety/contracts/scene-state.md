# Contract: 现场存档（Scene State）

**Spec**: `specs/041-step-switch-state-safety/spec.md` | **Module**: `useDiscoverySceneState.ts`

## 用途

步骤页内部现场（滚动/选中/草稿/详情开合/批次/面板展开/画像高度/已加载目录）的存取，绑在「画像+轮次+平台」身份键上。切页/翻历史/刷新保留，切画像/开新轮/切平台按场景清或隔离。

## 公开接口

```typescript
interface SceneIdentity {
  profileId: string;
  runEpoch: string;
  platform: Platform;
}

interface PageScene {
  /* 见 data-model.md */
}

interface SceneStore {
  current: PageScene;                              // 当前轮现场
  history: Record<string, PageScene>;             // 历史轮查看现场（懒存）
}

export function useDiscoverySceneState(): {
  /** 读取当前身份键下的现场（无则返回默认） */
  getCurrent(identity: SceneIdentity): PageScene;
  /** 写当前现场（防抖，身份变时丢弃旧键写入） */
  saveCurrent(identity: SceneIdentity, patch: Partial<PageScene>): void;
  /** 读取历史轮查看现场（懒存：未翻过返回默认，不预存） */
  getHistory(runId: string): PageScene;
  /** 写历史轮查看现场（翻过才写） */
  saveHistory(runId: string, patch: Partial<PageScene>): void;
  /** 切画像：清 current + 清 history + 清城市草稿（新画像干净态） */
  clearForProfileSwitch(): void;
  /** 开新轮：current 降级为 history[旧runId]，new current 回默认 */
  archiveCurrentForNewRound(oldRunEpoch: string): void;
  /** 切平台：按平台键隔离（同一 composable 内按 identity.platform 区分，无需显式调） */
  /** 刷新后从 sessionStorage 恢复（onMounted 钩子） */
  restoreFromSession(profileId: string): void;
  /** 持久化到 sessionStorage（onBeforeUnmount 钩子，复用既有通道） */
  persistToSession(profileId: string): void;
};
```

## 行为契约

1. **身份不变则留**（spec FR-001/FR-004）：切页/翻历史/刷新/后台跑完冒泡，`getCurrent` 返回上次现场，不回默认。
2. **历史轮懒存无草稿**（spec FR-002）：`getHistory` 未翻过返回默认（不预存）；`HistoryViewScene` 无 `listFilterDraft`，只展示该轮已定筛选状态。
3. **三主体变才清**（spec FR-005）：
   - 切画像 → `clearForProfileSwitch` 清 current + history + 城市草稿。
   - 开新轮 → `archiveCurrentForNewRound` 旧轮降级为 history、新轮 current 回默认。
   - 切平台 → 按 `identity.platform` 隔离（草稿不串台），无显式清调用。
4. **默认态**（spec FR-009）：`current` 回默认 = 该轮真实结果 + 默认排序 + 选中第一条 + 滚到顶 + 无未确认草稿。
5. **选中按身份跟随**（spec FR-017）：`selectedJobKey` 用 `platform:platform_job_id`，重抓后按身份跟随，岗位消失回第一条。
6. **不破坏 resultEpoch 语义**：现场存档不得让「新结果重置、切分类/切平台保留」失效（JobWorkspace L81-89 watch 判据保留）。
7. **懒存上限**：history 只存实际翻过的 runId，未翻过用默认，不无限膨胀（spec Assumptions）。

## 调用方

- `DiscoveryView.vue`：身份变化时调清理/降级；步骤页 `v-show`/KeepAlive 挂载时 `getCurrent` 恢复、卸载 `saveCurrent` 持久化。
- `JobWorkspace.vue`：现场 ref（sortKey/filterState/selectedJobKey/detailOpen/visibleCount/jdScrollTop）读写走 `getCurrent`/`saveCurrent`。
- `LocationPicker.vue`：城市面板 ref（open/districts/cityCode）读写走现场存档，目录缓存复用。
- `useDiscoveryTasks.ts` `resetWorkflowInternal`：开新轮调 `archiveCurrentForNewRound`。
- `useDiscoveryResults.ts` `enterHistoryRound`/`returnToLatest`：翻历史调 `getHistory`/`saveHistory`。
- `useDiscoveryWorkflow.ts` `persistWorkflowState`/`restoreWorkflowState`：扩展纳入现场快照（复用 sessionStorage 通道）。

## 不变式

- 身份键三字段任一变即视为身份变。
- 历史轮 history 子表只含翻过的 runId。
- sessionStorage 版本号升至 2，旧版本（=1）按既有升级逻辑兼容（缺现场字段回默认）。
