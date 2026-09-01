# Data Model: 桌面壳自绘标题栏 + 顶栏胶囊灵动岛

**Spec**: [spec.md](./spec.md) | **Date**: 2026-09-01

## 实体总览

| 实体 | 归属 | 持久化 | 说明 |
|---|---|---|---|
| 窗口状态 | B084 | `~/.career-scout/desktop_window.json`（schema 3，既有） | 无边框不改变其语义 |
| 胶囊状态 | B088 | 前端内存态 | 由 composables 汇总上抛 |
| 提醒计数 | B088 | 服务端已有 + 前端聚合 | 提醒按钮通用化 |

## 窗口状态（B084，既有实体，不新增）

复用 029 契约（`packaging/window_state.py`）：`desktop_window.json` schema 3。

- 字段：`width/height/x/y`（普通矩形）、`maximized`（关闭时是否最大化）、`default_width/default_height`（用户默认尺寸）
- 约束：普通矩形语义、读时工作区钳制、无记忆/损坏按首开处理
- 本 Spec 不修改该文件与契约；`desktop.py` 仅调整 `create_window` 参数（`frameless=True`）

## 胶囊状态（B088，新实体，前端内存态）

由 Discovery composables 汇总为一个胶囊状态对象，经 `round-status` 上抛链路（扩展 payload）或等价状态源传给 `DynamicIsland.vue`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `state` | `idle \| running \| completed \| attention` | 四态；多态并存取优先级最高一件 |
| `platform` | `boss \| zhilian` | 当前平台（空闲态显示用） |
| `progress` | `{ phase: "scraping" \| "screening"; done: number; total?: number }` | 运行中进度（total 未知时省略分母） |
| `results` | `{ matched: number; pending: number }` | 跑完结果 |
| `attention` | `{ kind: "paused" \| "error" \| "pending"; message: string }` | 需处理态 |
| `target` | `home \| task \| results \| attention` | 点击跳转目标（组件据此派发） |

状态判定优先级：`attention > running > completed > idle`（spec FR-013）。

## 提醒计数（B088 提醒按钮通用化）

- 既有：投递提醒计数（`getJobReminderCount`，服务端权威）。
- 扩展：聚合各类提醒数量（投递、待确认、出错、跑完等），点击仍打开提醒抽屉。
- 约束：数量以既有状态层为唯一来源，不在前端伪造；`ReminderDrawer` 展示各类提醒内容。

## 状态流转（胶囊）

```text
idle ──任务启动──▶ running ──任务完成──▶ completed
                    │                      │
                    │ 暂停/出错/需决策       │
                    └──────▶ attention ◀────┘
```

- 任务进行中同时有待确认岗位：显示 `attention`（优先级更高），处理完回到 `running`。
- 后台任务跑完、用户不在任务视图：进入 `completed`，点击去结果页。
- 无任务/无结果：`idle`，显示平台名，点击回主页。
