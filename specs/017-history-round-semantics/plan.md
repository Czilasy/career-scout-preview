# Implementation Plan: 历史轮次与流程终结语义修复

**Branch**: `017-history-round-semantics` | **Date**: 2026-08-22 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/017-history-round-semantics/spec.md`

## Summary

历史轮的准入收敛为"到达 04 页"的三种出口（自然跑完、结束保存、跳过筛选）；暂停、错误强停、取消、重启中断不再产生历史轮；一条流程最多一条轮（统一写入服务防重）；历史主时间改为定稿时间（重抓/补筛刷新）；标签收敛三种；存量历史一次性全清；任务状态报告与"最新结果"判定各收敛为唯一口径；删除死端点与"猜最新"回退。

## Technical Context

**Language/Version**: Python 3.12（后端）、TypeScript + Vue 3（前端）

**Primary Dependencies**: Flask、SQLite（内置 store）、Vitest（前端）

**Storage**: SQLite（screening_runs 及子表；本 spec 无 DDL，仅一次数据清空迁移）

**Testing**: unittest（后端）、Vitest（前端）、npm run build

**Target Platform**: Windows/macOS 桌面应用（Flask 本地服务 + 前端 SPA）

**Project Type**: desktop-app（web-service 架构）

**Performance Goals**: 无新增性能目标；快照写入路径同步执行，单轮落库毫秒级（现状保持）

**Constraints**: `webui/app.py`（9783 行）与 `webui/store.py`（4882 行）只删不增，交付后行数必须下降；新逻辑全部落新模块

**Scale/Scope**: 后端约 6 文件改动 + 2 新文件；前端 3 文件改动；测试 3-5 文件

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 检查 | 结果 |
|---|---|---|
| I. 职责分层 | 写入收口落新服务模块 `webui/result_rounds.py`（service 层）；路由只做参数校验与响应 | ✅ 通过 |
| II. 单文件尺寸 | app.py/store.py 净减；新文件 <300 行 | ✅ 通过 |
| III. 引用方向 | `app.py → result_rounds.py → store/mixins`；`scrape_only.py → result_rounds.py`；无反向 | ✅ 通过 |
| IV. 拆分纪律 | 无纯搬迁；finish 端点构建逻辑留原位（research R7），只替换写入调用；行为变化全部由失败测试先行定义 | ✅ 通过 |
| V. 验证门禁 | 功能交付：聚焦测试 + 后端全量 + 前端测试 + build + 卫生 | ✅ 通过 |

Phase 1 复查：设计未引入新违规。

## File Boundaries

*GATE: 用户已于 2026-08-22 确认以下布局。*

- **Allowed files**:
  - 新增：`webui/result_rounds.py`（历史轮写入统一服务，~250 行）、`tests/test_result_rounds.py`（聚焦测试，~200 行）
  - 修改：`webui/app.py`（只删不增：拆两套快照兜底及其调用、删死端点、删重抓回退、状态映射二合一、三出口改调 result_rounds、重抓回写段改调 apply_recrawl_writeback；净减 250+ 行）
  - 修改：`webui/store.py`（recount 刷新定稿时间；删 `get_latest_done_run_id`；latest 过滤抽单一口径；净减）
  - 修改：`webui/store_migrations.py`（新增一次性迁移：清空存量历史轮，无 DDL）
  - 修改：`webui/scrape_only.py`（`save_screen_result` 分流并入 result_rounds，保留纯构建函数）
  - 修改：`webui/src/discovery.ts`（标签三态）、`webui/src/components/ResultHistoryDrawer.vue`（主时间=定稿时间）、`webui/src/views/DiscoveryView.vue`（状态词核对与替换）
  - 修改测试：`tests/test_result_history.py`、`tests/test_webui_app.py`、`tests/test_webui_store.py`、前端 `ResultHistoryDrawer.spec.ts` 等
- **Forbidden files**: `webui/ai.py`、`webui/error_registry.py`、`webui/tuning.py`（016 错误模块重构范围）；`scripts/boss_cdp_raw.py`；数据库表结构（无 DDL）；前端其余视图与组件
- **Reference direction**: `app.py(路由) → result_rounds.py(服务) → store/mixins(数据)`；`scrape_only.py → result_rounds.py`；前端 `view → composable → api`
- **Line gate**: `result_rounds.py` < 800；改动 Vue 文件 < 1200；app.py 与 store.py 交付后行数低于当前值
- **Rationale**: 宪法禁止向超大文件追加新逻辑；统一写入服务是本 spec 核心，独立模块聚焦测试与收口

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能交付门禁：`tests.test_result_rounds` + `tests.test_result_history` 聚焦 → 后端全量 `uv run python -m unittest` → 前端 `npm test` → `npm run build` → `uv run python -m unittest tests.test_repo_hygiene`。
- 本 spec 交付为功能批次，按上述全门禁执行（宪法 V）。

## Project Structure

### Documentation (this feature)

```text
specs/017-history-round-semantics/
├── plan.md              # 本文件
├── research.md          # Phase 0：R1-R8 决策与证据
├── data-model.md        # Phase 1：轮状态机与词汇表
├── quickstart.md        # Phase 1：端到端验证场景
├── contracts/
│   └── http-api.md      # Phase 1：HTTP 行为变更清单
├── checklists/
│   └── requirements.md  # specify 质量校验（已通过）
└── tasks.md             # /speckit-tasks 产出
```

### Source Code (repository root)

```text
webui/
├── result_rounds.py          # [新增] 历史轮写入统一服务
├── app.py                    # [修改·净减] 路由层：删兜底/死端点/回退，改调服务
├── store.py                  # [修改·净减] recount 定稿时间、删 get_latest_done_run_id、口径抽取
├── store_migrations.py       # [修改] 存量清空迁移
└── scrape_only.py            # [修改] 写入分流委托 result_rounds

webui/src/
├── discovery.ts              # [修改] 标签三态
├── components/ResultHistoryDrawer.vue  # [修改] 主时间=定稿时间
└── views/DiscoveryView.vue   # [修改] 状态词核对与替换

tests/
├── test_result_rounds.py     # [新增] 写入服务聚焦测试
├── test_result_history.py    # [修改] 历史语义用例
├── test_webui_app.py         # [修改] 中断路径不再成轮用例
└── test_webui_store.py       # [修改] 删除 get_latest_done_run_id 断言
```

**Structure Decision**: 单仓库桌面应用；后端按"路由 → 服务 → store"三层落位，新服务独立模块；前端只动历史展示与状态词消费三处。

## Complexity Tracking

无宪法违规需豁免。
