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
  runIds: Record<string, string>;                 // 轮次身份 → 该轮结果 run id（归档键映射）
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
  /**
   * 开新轮：current 降级为 history[historyRunId || 旧轮次身份]，new current 回默认。
   * historyRunId（该轮结果 run id）优先——历史轮浏览按结果 run id 取现场。
   */
  archiveCurrentForNewRound(oldRunEpoch: string, historyRunId?: string, platform?: Platform): void;
  /** 切平台：按平台键隔离（同一 composable 内按 identity.platform 区分，无需显式调） */
  /** 刷新后从 sessionStorage 恢复（onMounted 钩子） */
  restoreFromSession(profileId: string): void;
  /** 持久化到 sessionStorage（onBeforeUnmount 钩子，复用既有通道） */
  persistToSession(profileId: string): void;
  /** 当前画像的轮次身份（响应式）：换轮时消费方随之落到新身份 */
  roundEpoch: Ref<string>;
  /** 取回/新建当前画像的轮次身份（跨刷新稳定，随会话存档） */
  ensureRoundEpoch(profileId: string): string;
  /** 开新一轮：换发新轮次身份（必须先归档旧轮现场再调用） */
  rotateRoundEpoch(profileId: string): string;
  /** 登记某轮的结果 run id（供开新轮归档到历史轮键） */
  noteRoundRunId(profileId: string, epoch: string, runId: string): void;
  /** 查某轮已知的结果 run id（无则空串） */
  resolveRoundRunId(profileId: string, epoch: string): string;
};
```

## 行为契约

0. **轮次身份稳定且唯一**（返工修订，spec FR-004/FR-005）：`runEpoch` 是一个业务轮次的稳定标识，由 `useDiscoverySceneState` 生成一次并随会话存档持久化（`career-scout-round-epoch:<profileId>`）。同一轮从上传、抓取、筛选到结果完成期间不得变化；**禁止**用 `task_id`（抓取/筛选/补抓任务号）或结果 `source_run_id` 直接充当轮次身份（它们会随阶段推进而变）。只有切画像、开新一轮、切平台三类事件换身份；开新一轮由 `rotateRoundEpoch` 换发，且必须先 `archiveCurrentForNewRound` 归档旧轮现场。
1. **身份不变则留**（spec FR-001/FR-004）：切页/翻历史/刷新/后台跑完冒泡/灵动岛跳转，`getCurrent` 返回上次现场，不回默认。
2. **历史轮懒存无草稿**（spec FR-002）：`getHistory` 未翻过返回默认（不预存）；历史轮现场复用 `PageScene`（返工修订，原 `HistoryViewScene` 类型零引用已删）且写入时 `listFilterDraft` 归零，只展示该轮已定筛选状态。
3. **三主体变才清**（spec FR-005）：
   - 切画像 → `clearForProfileSwitch` 清 current + history + 城市草稿。
   - 开新轮 → `archiveCurrentForNewRound` 旧轮降级为 `history[结果 run id]`（无结果时退回轮次身份）、新轮 current 回默认。
   - 切平台 → 按 `identity.platform` 隔离（草稿不串台），无显式清调用。
4. **默认态**（spec FR-009）：`current` 回默认 = 该轮真实结果 + 默认排序 + 选中第一条 + 滚到顶 + 无未确认草稿。
5. **选中按身份跟随**（spec FR-017）：`selectedJobKey` 用 `platform:platform_job_id`，重抓后按身份跟随，岗位消失回第一条。
6. **不破坏 resultEpoch 语义**：现场存档不得让「新结果重置、切分类/切平台保留」失效（JobWorkspace L81-89 watch 判据保留）。
7. **懒存上限**：history 只存实际翻过的 runId，未翻过用默认，不无限膨胀（spec Assumptions）。

## 调用方

- `useDiscoverySceneIdentity.ts`（返工新增）：派生现场身份（画像 + 稳定轮次 + 平台），负责 `ensureRoundEpoch` 与结果 run id 登记；`DiscoveryView.vue` 只接线一次。
- `useProfileInputScene.ts`（返工新增）：画像文字框高度现场（同宽同内容接回、内容/宽度/身份变化重算并回写）。
- `DiscoveryView.vue`：身份变化时调清理/降级；步骤页 `v-show`/KeepAlive 挂载时 `getCurrent` 恢复、卸载 `saveCurrent` 持久化。
- `JobWorkspace.vue`：现场 ref（sortKey/filterState/selectedJobKey/detailOpen/visibleCount/jdScrollTop）读写走 `getCurrent`/`saveCurrent`。
- `LocationPicker.vue`：城市面板 ref（open/districts/cityCode）读写走现场存档，目录缓存复用。
- `useDiscoveryTasks.ts` `resetWorkflowInternal`：开新轮先 `archiveCurrentForNewRound`（带结果 run id）再 `rotateRoundEpoch` 换身份。
- `useDiscoveryResults.ts` `enterHistoryRound`/`returnToLatest`：翻历史调 `getHistory`/`saveHistory`；当前轮身份取 `roundEpoch`。
- `useDiscoveryWorkflow.ts` `persistWorkflowState`/`restoreWorkflowState`：扩展纳入现场快照（复用 sessionStorage 通道）。

## 不变式

- 身份键三字段任一变即视为身份变；除切画像/开新轮/切平台外，任何事件（任务阶段推进、灵动岛跳转、后台补抓完成、刷新）都不得改变身份键。
- 历史轮 history 子表只含翻过的 runId；开新轮归档键优先用该轮结果 run id（与历史轮浏览同一键空间）。
- sessionStorage 版本号升至 2，旧版本（=1）按既有升级逻辑兼容（缺现场字段回默认；缺 `runIds` 时按空表处理）。
