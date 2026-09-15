# Tasks: 未收尾流程的一次提醒与轮次数据整条进出

**Input**: `spec.md`、`plan.md`、`research.md`

**Delivery rule**: 最小做法——能接线的一行解决；不新增表、不新增独立工具、不给现有文件加逻辑块；每个功能块只跑聚焦测试，整条交付链收敛后只跑一次全量。

## File Boundaries

与 `plan.md`「File Boundaries」一致，本清单不得越界：

- 允许新增：`webui/store_migrations_v6.py`、`webui/run_notice.py`、`webui/run_cleanup.py`、`tests/test_run_lifecycle.py`
- 允许修改：`webui/` 下 store 域三文件、`result_history.py`、`running_task_api.py`、`results_api.py`、`runners/ai_screen_task.py`、`task_continue_api.py`（接线级）、`app_support.py`（接线级）、`store_migrations.py`（仅组装）；前端 7 文件：`useDiscoveryExecution.ts`、`useDiscoveryTasks.ts`、`useIslandNotices.ts`、`DynamicIsland.vue`、`DiscoveryView.vue`（接线级）、`views/__tests__/DiscoveryScrapeOnly.spec.ts`、`composables/__tests__/useDiscoveryTasks.spec.ts`；`tests/test_result_history.py`；规格与模块地图文档
- 禁止：`webui/store.py`、`webui/app.py`、`webui/source*.py`、`scripts/boss/**`、`scripts/zhilian/**`、迁移 v1–v5、`webui/whitebox*.py` 业务逻辑

## Phase 1: 提醒一次性（US1）

- [x] T001 迁移 v6：`screening_runs` 增加 `notice_sent_at`（`webui/store_migrations_v6.py`；`webui/store_migrations.py` 仅组装），聚焦验证迁移幂等
- [x] T002 `webui/run_notice.py`：识别最新未收尾流程、判定"已提醒"、写入记号、组装灵动岛文案；提醒时 MUST 把同时在场的更旧未收尾一并标记为沉默（落实 FR-004 不接力）
- [x] T003 `webui/running_task_api.py`：已完成普通抓取分支接线——已提醒的不再返回；首次置记号并带提醒载荷
- [x] T004 `webui/results_api.py`：最新轮响应带"是否已提醒"标记
- [x] T005 `webui/src/composables/useDiscoveryTasks.ts`：已提醒轮不再自动恢复落页（闸门）
- [x] T006 灵动岛接线：`useDiscoveryExecution.ts`（推送提醒）+ `useIslandNotices.ts` / `DynamicIsland.vue`（展示一行字）
- [x] T007 聚焦测试：`tests/test_run_lifecycle.py`（提醒部分）+ 前端 `useDiscoveryTasks.spec.ts` 补断言

## Phase 2: 数据整条进出（US2）

- [x] T008 store 扩展：`store_result_history_mixin.py`（闭包查询 + 整条删除 + 无主兜底）、`store_whitebox.py`（按 owner 删除证据）、`store_tasks.py`（按任务删日志与占位行）
- [x] T009 `webui/run_cleanup.py`：编排（有主/无主判定、未结束任务保护、删除序列、定稿中间档清理）
- [x] T010 删除接线：`webui/result_history.py` 走整条清除；`tests/test_result_history.py` 断言改为新语义
- [x] T011 触发点接线：`runners/ai_screen_task.py`（落轮后）、`task_continue_api.py`（保存后）、`app_support.py`（启动兜底：清理 + 存量回收 + 自动备份 + 清单落盘；启动兜底即覆盖 FR-011 的"只抓取、从不筛选"路径）
- [x] T012 聚焦测试：`tests/test_run_lifecycle.py`（清理部分：删干净 / 留完整 / 保护 / 中间档）
- [x] T013 文档同步：008/010 标注被取代；constitution 模块地图登记两个新模块

## Phase 3: 页面秩序（US3）

- [x] T014 `webui/src/views/DiscoveryView.vue` 两元素对调（接线级）+ `DiscoveryScrapeOnly.spec.ts` 断言
- [x] T015 界面走查：未筛选轮与常规场景对照（模拟用户操作路径）

## Phase 4: 验证与收口

- [x] T016 聚焦全跑：`tests/test_run_lifecycle.py` + `tests/test_result_history.py` + 受影响前端测试
- [x] T017 构造数据核对："删干净 / 留完整"两侧 + 未结束任务保护 + 中间档只留最新
- [x] T018 交付链收敛后一次全量：后端全量 + `npm test` + `npm run build` + 卫生测试
- [x] T019 返修（收口前）：提醒水位持久化（迁移 036 `run_notice_state`，按画像分行）——删除已提醒行后更旧者仍沉默（FR-004 不接力）；补聚焦测试 2 例；副本端到端 + 全量复验

## Dependencies & Execution Order

```text
T001 → T002 → T003 → T004 → T005 → T006 → T007   （Phase 1 链）
T008 → T009 → T010 → T011 → T012                  （Phase 2 链）
T014 → T015                                        （Phase 3，独立）
T016 → T017 → T018                                 （收尾）
```

## Notes

- Phase 1 与 Phase 2 相互独立，可先后推进；Phase 3 随时可做。
- 每个 Phase 内部只跑聚焦测试；T018 是整条交付链唯一一次全量。
- **停滞点**：本清单产出后停下，等待用户明确下令才进入实现。
