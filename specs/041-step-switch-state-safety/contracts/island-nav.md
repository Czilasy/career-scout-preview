# Contract: 灵动岛落点派生（Island Navigation）

**Spec**: `specs/041-step-switch-state-safety/spec.md` | **Module**: `useIslandNavigation.ts`

## 用途

灵动岛胶囊与通知面板行点击的落点派生：按任务真实进度派生目标页，点灵动岛退出历史回当前轮且历史现场原地保留，启动占位期间点击防吞锁，硬红线「任何路径不清成空白上传页」。

## 公开接口

```typescript
import type { TaskSnapshot, Platform } from "../types";

interface NavInput {
  capsuleState: "idle" | "analyzing" | "running" | "completed" | "attention";
  /** 任务真实进度（capsule.progress.phase 或快照状态派生） */
  taskPhase: "scraping" | "jd" | "screening" | "scraped" | "completed" | "none";
  /** 暂停/出错时卡在哪个阶段 */
  stuckAt: "scrape" | "screen" | "none";
  /** 步骤页启用态 */
  enabledSteps: Set<string>;
  /** 是否在历史模式 */
  historyMode: boolean;
  /** 启动加载占位是否进行中 */
  bootstrapping: boolean;
}

type IslandNavTarget = "home" | "task-scrape" | "task-screen" | "results" | "reminders";

export function useIslandNavigation(): {
  /** 派生落点（spec FR-010） */
  deriveTarget(input: NavInput): IslandNavTarget;
  /** 执行导航：退出历史（若在）→ 落目标页 → 硬红线检查（不清成空白上传页） */
  navigate(target: IslandNavTarget, opts: {
    onExitHistory: () => void;       // 调 returnToLatest（历史现场原地保留）
    onSetActiveStep: (step: string) => void;
  }): void;
  /** 占位期间点击处理：等加载完执行 / 明确不响应但放行下一次（spec FR-013） */
  handleBootstrapClick(pendingTarget: IslandNavTarget | null): void;
};
```

## 行为契约

1. **落点按真实进度派生**（spec FR-010）：
   - idle → home
   - analyzing → home（显示分析中）
   - running·scraping → task-scrape
   - running·jd/screening → task-screen
   - completed → results
   - attention(paused/error)·stuckAt=scrape → task-scrape（不无脑送 screen）
   - attention·stuckAt=screen → task-screen
2. **历史退出保留现场**（spec FR-011）：navigate 时若 historyMode，先 `onExitHistory`（returnToLatest，历史现场原地保留），再落目标页。
3. **硬红线**（spec FR-012）：navigate 永不调用 resetWorkflow 或把 pipelineResult=null + activeStep="upload" 的组合（不清成空白上传页）。
4. **占位防吞锁**（spec FR-013）：bootstrapping 时点击要么排队等加载完执行一次，要么明确不响应但放行下一次同目标点击——不吞掉且锁死后续。
5. **页面启用检查**（边缘场景）：落点前检查 enabledSteps，未启用页按真实进度落到对应启用页（如暂停时页面二未启用不送未启用页）。
6. **通知面板行点击**（spec FR-014）：规矩同胶囊点击，去该通知对应现场。

## 调用方

- `DynamicIsland.vue`：`stateTarget`（L124-131）改调 `deriveTarget`；`onPillClick`（L358-368）emit navigate 经 `navigate()` 处理。
- `IslandNoticePanel.vue`：行点击 emit navigate 同上。
- `DiscoveryView.vue`：`capsuleNavigationTarget` watch（L908-934）接 `navigate` 的 `onSetActiveStep`。
- `useDiscoveryResults.ts` `returnToLatest`：作为 `onExitHistory` 回调注入。

## 不变式

- `deriveTarget` 输入相同则输出相同（纯函数派生）。
- navigate 永不触发 resetWorkflow / clearLatestResult。
- 历史退出后历史轮现场仍在 history 子表（不丢）。
