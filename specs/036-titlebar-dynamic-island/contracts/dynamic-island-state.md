# Contract: 顶栏胶囊状态（B088 灵动岛）

**Spec**: [spec.md](../spec.md) | **Date**: 2026-09-01

## 1. 状态对象（composables → App → DynamicIsland）

由 Discovery composables 汇总为一个胶囊状态对象，经 App 传给 `DynamicIsland.vue`。组件只消费、不抓取。

```ts
type DynamicIslandState =
  | { state: "idle"; platform: "boss" | "zhilian" }
  | {
      state: "running";
      platform: "boss" | "zhilian";
      progress: { phase: "scraping" | "screening"; done: number; total?: number };
    }
  | {
      state: "completed";
      platform: "boss" | "zhilian";
      results: { matched: number; pending: number };
    }
  | {
      state: "attention";
      platform: "boss" | "zhilian";
      attention: { kind: "paused" | "error" | "pending"; message: string };
    };
```

## 2. 派生规则

- 多态并存取优先级：`attention > running > completed > idle`（spec FR-013），同一时刻只输出一件。
- 空闲平台名：最近一次使用平台；从未使用默认 `boss`（spec A2）。
- 进度 `total` 未知（如未知总量）：省略分母，只显示 `done`，不显示假分母（spec 边界）。
- `pending` 为 0：结果态不显示待确认数字，只显示匹配数（spec 边界）。

## 3. 点击行为

| 状态 | 点击目标 | 行为 |
|---|---|---|
| idle | 主页 | 回到首页 |
| running | 当前任务 | 回到正在跑的任务视图 |
| completed | 结果页 | 进入本轮结果页 |
| attention | 处理现场 | 去暂停/出错/待确认处理位置 |

## 4. 数据来源

- `progress.done/total`：`useDiscoveryTasks.ts` 既有任务轮询快照中的进度字段。
- `results.matched/pending`：`useDiscoveryResults.ts` 结果态数据。
- `attention.kind/message`：任务状态（暂停/出错）与待确认岗位数（`useDiscoveryState.ts` / 结果数据）。
- 上抛链路沿用既有 `round-status` 机制扩展，不新增轮询。
