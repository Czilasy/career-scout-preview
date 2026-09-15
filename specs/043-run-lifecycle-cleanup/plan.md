# Implementation Plan: 未收尾流程的一次提醒与轮次数据整条进出

**Branch**: `043-run-lifecycle-cleanup` | **Date**: 2026-09-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/043-run-lifecycle-cleanup/spec.md`

## Summary

把"一轮流程"从生到死当作一个整体来管，**用最小的做法**：

- **提醒**：存在"抓完没筛选"的未收尾流程时，启动只主动提醒一次（灵动岛一行字，含来历）；提醒过即永久安静、不接力；"一次"= 岛提醒与结果接回展示同进同停。
- **数据**：流程的删除、淘汰、定稿瘦身一律"整条进出"——轮在则背后数据全留；轮被删/被淘汰则整条清除（含日志）；定稿后只保留最新一次筛选中间档；无主账本按每平台最近 30 条兜底；有主数据不误伤、未结束任务不误删。

技术路径（复用优先、不新增工具）：
1. 提醒记号 = `screening_runs` 加一列（一次小迁移，无回填）；
2. 闸门接在现有启动判定上（后端"恢复项不再返回已提醒项" + 前端"已提醒不再自动落页"），灵动岛复用既有通知形态加一行"恢复提醒"；
3. 清理 = 现有删除/裁剪动作的"整条化"改造（闭包查询 + 删除序列落在既有 store 域），触发点接在既有三处（落轮后 / 保存后 / 启动早期）；
4. **存量回收并入启动兜底**（幂等 + 自动备份 + 清单落盘），不单独出命令行工具；
5. 页面秩序只调元素顺序，文案不动。

关键决策与理由见 [research.md](./research.md)。

## Technical Context

**Language/Version**: Python 3.12+（uv 管理）；TypeScript + Vue 3（Vite）

**Primary Dependencies**: Flask（本地 HTTP 服务）、SQLite（stdlib sqlite3）、Vitest（前端测试）

**Storage**: SQLite `~/.career-scout/webui/webui.db`；迁移 v6 增加一列"提醒已发出"（ADD COLUMN，无回填）

**Testing**: 后端 `uv run python -m unittest`；前端 `npm test`；构建 `npm run build`

**Target Platform**: Windows 桌面版（打包 EXE）+ 本地浏览器访问

**Project Type**: desktop-app（本地服务 + Web UI）

**Performance Goals**: 启动期检查保持常数级查询；清理分批执行、不在任务运行中长时间持有写锁

**Constraints**: Python 单文件 ≤800 行 / Vue ≤1200 行；门面文件（`webui/store.py`、`webui/app.py`）不碰；存量回收"先备份、清单落盘、可回退"；删除不释放文件体积——验收按各表行数实测（SC-007）

**Scale/Scope**: 单用户本地库；现状约 228MB / 528 轮（522 条过程记录）/ 7.7 万抓取岗位行 / 8 万结果明细行 / 12.3 万日志行

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **原则 I 职责分层**：新增两个小服务模块（提醒 / 清理）；数据访问落在 store 域；API 只接线 —— PASS
- **原则 II 尺寸边界**：新增文件全部低于红线；超限文件（`task_continue_api.py`、`DiscoveryView.vue`）仅接线级最小改动 —— PASS
- **原则 III 引用方向**：`api → service → store`；前端 `view → composable → api client` —— PASS
- **原则 IV 拆分纪律**：非拆分 spec；删除语义变更由本 spec 定义并同步旧规格条款 —— PASS
- **原则 V 验证门禁**：聚焦测试 + 交付链收敛后一次全量 —— PASS
- **原则 VI 模块地图与落位**：两个新模块在本批次登记进 constitution 「模块地图」—— PASS

## File Boundaries

*GATE: 落位清单已定稿（用户授权由 AI 定；精简原则：不新增独立工具、测试合并）。*

- **Allowed files**:
  - **新增**：`webui/store_migrations_v6.py`、`webui/run_notice.py`、`webui/run_cleanup.py`、`tests/test_run_lifecycle.py`
  - **修改（后端）**：`webui/store_migrations.py`（仅组装 v6）、`webui/store_result_history_mixin.py`、`webui/store_whitebox.py`、`webui/store_tasks.py`、`webui/result_history.py`、`webui/running_task_api.py`、`webui/results_api.py`、`webui/runners/ai_screen_task.py`、`webui/task_continue_api.py`（接线级）、`webui/app_support.py`（接线级）
  - **修改（前端）**：`webui/src/composables/useDiscoveryExecution.ts`、`webui/src/composables/useDiscoveryTasks.ts`、`webui/src/composables/useIslandNotices.ts`、`webui/src/components/DynamicIsland.vue`、`webui/src/views/DiscoveryView.vue`（接线级）、`webui/src/views/__tests__/DiscoveryScrapeOnly.spec.ts`、`webui/src/composables/__tests__/useDiscoveryTasks.spec.ts`
  - **修改（测试）**：`tests/test_result_history.py`
  - **文档**：`specs/043-run-lifecycle-cleanup/**`；`specs/008-multi-round-history/{spec.md,data-model.md,contracts/http-api.md}` 与 `specs/010-scrape-only-view/spec.md`（标注被取代）；`.specify/memory/constitution.md`（模块地图登记）
- **Forbidden files**: `webui/store.py`、`webui/app.py`（门面，一行不碰）；`webui/source*.py`、`scripts/boss/**`、`scripts/zhilian/**`；迁移 v1–v5；`webui/whitebox*.py` 业务逻辑（仅允许经 store 层增加删除接口）
- **New files**:

| 文件 | 职责（一句话） | 预计行数 |
|---|---|---|
| `webui/store_migrations_v6.py` | 迁移：结果轮增加"提醒已发出"标记列 | ~40 |
| `webui/run_notice.py` | 未收尾流程识别、一次性提醒记号读写、提醒文案 | ~90 |
| `webui/run_cleanup.py` | 流程闭包清理：整条删除、无主兜底、定稿中间档清理、启动兜底入口 | ~170 |
| `tests/test_run_lifecycle.py` | 提醒 + 清理合并聚焦测试 | ~280 |

- **Reference direction**: `api（running_task_api / results_api / result_history_api / task_continue_api）→ service（run_notice / run_cleanup）→ store（store_result_history_mixin / store_whitebox / store_tasks 扩展）`；前端 `views → composables → api client`；不得反向
- **Line gate**: 新增文件均在红线内；`running_task_api.py` 改动后必须低于 600 行预警线；`task_continue_api.py`、`DiscoveryView.vue` 仅接线级改动
- **Rationale**: 提醒与清理是全新领域，无既有域可落，按宪法原则 VI 开新模块；不新增独立 CLI、不新增表；能接线的一行解决，不给现有文件加逻辑

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 开发、调试和返修只安排聚焦测试、原失败用例与直接受影响回归；不为每个 Task 或前置重复安排后端全量。
- 同一用户目标的所有工作收敛后，最终门禁才运行一次干净的后端全量、前端测试、`npm run build` 和仓库卫生检查。
- 全量失败后先按失败清单做聚焦返修；没有相关改动禁止重跑全量。
- 收口发布任务不适用本门禁，按根 `AGENTS.md` 执行。
- 界面类验收按模拟用户视角真实操作路径走查；数据清理类验收用构造数据核对"删干净/留完整"两侧，并以各表行数实测（不只看文件大小）。

## Project Structure

### Documentation (this feature)

```text
specs/043-run-lifecycle-cleanup/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── checklists/requirements.md
└── tasks.md             # Phase 2 output（由 /speckit-tasks 生成）
```

### Source Code (repository root)

```text
webui/
├── run_notice.py                    # 新增：未收尾流程识别与一次性提醒
├── run_cleanup.py                   # 新增：流程闭包清理 + 启动兜底
├── store_migrations_v6.py           # 新增：提醒记号迁移
├── store_migrations.py              # 修改：仅组装 v6
├── store_result_history_mixin.py    # 修改：整条删除 + 闭包查询 + 无主兜底
├── store_whitebox.py                # 修改：按 owner 删除证据
├── store_tasks.py                   # 修改：按任务删除日志与占位行
├── result_history.py                # 修改：删除接线
├── running_task_api.py              # 修改：提醒闸门
├── results_api.py                   # 修改：最新轮响应带提醒标记
├── runners/ai_screen_task.py        # 修改：定稿触发（接线）
├── task_continue_api.py             # 修改：保存触发（接线）
├── app_support.py                   # 修改：启动兜底（接线）
└── src/
    ├── composables/
    │   ├── useDiscoveryExecution.ts # 修改：接灵动岛提醒
    │   ├── useDiscoveryTasks.ts     # 修改：已提醒不再自动恢复
    │   └── useIslandNotices.ts      # 修改：新增"恢复提醒"形态
    ├── components/DynamicIsland.vue # 修改：提醒展示
    └── views/DiscoveryView.vue      # 修改：两元素对调（接线级）

tests/test_run_lifecycle.py          # 新增（提醒 + 清理）
tests/test_result_history.py         # 修改：日志保留断言改新语义
```

**Structure Decision**: 沿用现有单仓结构，不新增目录层级、不引入新依赖、不新增独立工具。

## Complexity Tracking

无违反项。
