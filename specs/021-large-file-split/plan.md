# Implementation Plan: 大文件拆分重构（021）

**Branch**: `021-large-file-split` | **Date**: 2026-08-23 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/021-large-file-split/spec.md`

## Summary

对 11 个超标文件做纯拆分重构：行为不变、接口不变、测试零改动，通过门面 re-export 保持旧路径兼容。`app.py` 采用「运行时上下文对象 → runner 逐个外迁 → 薄路由归位」；其余文件按域拆模块/mixin/composable，均为搬运批。每批独立 Plan/Tasks/Implement/Converge，每批全量测试绿后一个 `refactor` 提交。落位规则已写入宪法 1.2.0 原则 VI（模块地图 + 75% 预警线），各批次落地时登记模块地图。

## Technical Context

**Language/Version**: Python 3.11+（uv 管理）、Vue 3 + TypeScript + Vite

**Primary Dependencies**: Flask（webui 后端）、Playwright/CDP（抓取）、unittest（后端测试）、Vitest（前端测试）

**Storage**: SQLite（`webui.db`，本 Spec 不改任何 schema 与数据格式）

**Testing**: `uv run python -m unittest discover tests`；前端 `npm run test` / `npm run build`（webui/ 下）

**Target Platform**: Windows 桌面（PyWebview 壳）+ macOS 打包

**Project Type**: 桌面应用（Flask 后端 + Vue 前端）

**Performance Goals**: 无新增性能要求；行为与拆分前一致即可

**Constraints**: 纯搬运——不改逻辑、不改接口签名、不改 DB、不改前端模板；现有测试文件零改动

**Scale/Scope**: 11 个超标文件 ≈ 3.9 万行；测试安全网 ≈ 后端 2.4 万行 + 前端 452 用例

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 状态 | 说明 |
|---|---|---|
| I. 职责分层 | ✅ 本 Spec 即其执行载体 | app.py 收敛为入口+注册 |
| II. 单文件尺寸边界 | ✅ 目标本身 | Python ≤800 / Vue ≤1200 |
| III. 引用方向 | ✅ | runners 不互相 import；store 不反依 app |
| IV. 拆分与重构纪律 | ✅ | 独立 Spec、文件边界章节、行为不变 |
| V. 验证门禁 | ✅ | 每批：聚焦+后端全量+前端+build+卫生 |
| VI. 模块地图与落位规则（1.2.0 新增） | ✅ | 各批次登记模块地图；门面禁追加逻辑 |

Phase 1 设计后复查：无违规；唯一需说明处是 `runners/ai_screen_task` 因原闭包 4072 行，需按段拆 2-3 个子模块而非硬塞一个文件（子模块间允许同包内单向 import：task → 段模块）。

## File Boundaries

*已过文件放置门（用户 2026-08-23 确认）。*

- **Allowed files（修改）**: `webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/bump_version.py`（仅 B8：版本同步目标从 `boss_cdp_raw.py` 指向 `scripts/boss/` 新位置，同步行为不变）、`webui/tuning.py`、`webui/ai.py`、`webui/store_migrations.py`、`webui/pipeline_exec.py`、`webui/platforms.py`、`webui/src/views/DiscoveryView.vue`、`.specify/memory/constitution.md`（仅登记模块地图）
- **New files**: 见下表；每文件 Python ≤800 行
  - `webui/pipeline_context.py`（运行时上下文对象，~200）
  - `webui/runners/`：`__init__.py`、`tuning_manifest.py`、`pipeline_task.py`、`recrawl_task.py`、`ai_screen_task.py` + 分段子模块（各 ≤800）
  - `webui/source_boss_cdp.py`(~1230→拆2)、`source_zhilian_cdp.py`、`source_fake.py`、`source_breaker.py`
  - `webui/store_config.py`、`store_recovery.py`、`store_pipeline_results.py`、`store_jobs.py` 等 5-8 个域 mixin（各 400-700）
  - `scripts/boss/`：`cdp_session.py`、`exceptions.py`、`detail_extract.py`、`city_map.py`、`cli.py` 等（各 ≤800）
  - `webui/tuning_*.py`、`ai_*.py`、`store_migrations_v*.py`、`pipeline_exec_*.py`、`platforms_*.py` 域子模块
  - `webui/src/composables/useDiscovery*.ts` 4-6 个（模板不动）
- **Forbidden files**: `tests/**`（零改动验收）、`pyproject.toml` 版本字段、任何 DB 迁移的**语义与 schema**（store_migrations.py 仅允许 B7 物理归组、不改逻辑）、`webui/src/views/` 其他视图、前端模板结构
- **Reference direction**: `app.py → webui/runners/* → webui/pipeline_context → webui/store*`；runners 间禁止互相 import；**runners 禁止 `from webui.app import` 可被 monkeypatch 的符号，必须经 ctx 或 `webui.app` 模块属性调用时取用**；`source.py`/`app.py`/`boss_cdp_raw.py` 仅作门面 re-export 与组装；composables 不 import view
- **Line gate**: 全部批次完成后 11 个原文件 + 所有新文件 Python ≤800 / Vue ≤1200
- **Rationale**: 宪法 I/IV/VI 禁止向门面追加逻辑；域模块是唯一可维护落位；平台/脚本分组为宪法布局预先画定

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能/重构/拆分交付：最终门禁为相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene`）。
- 每批一个 `refactor` Conventional Commit，全绿后提交，不自动 push。
- 收口发布任务不在本 Spec 范围。

## Project Structure

### Documentation (this feature)

```text
specs/021-large-file-split/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
└── tasks.md
```

### Source Code (repository root)

```text
webui/
├── app.py                     # 门面：入口 + 上下文组装 + 路由注册 + re-export（≤800）
├── pipeline_context.py        # 运行时上下文对象
├── runners/                   # 四个后台任务 runner 及 ai_screen 分段子模块
├── source.py                  # 门面 re-export
├── source_{boss_cdp,zhilian_cdp,fake,breaker}.py
├── store.py                   # TaskStore = 域 mixin 组装
├── store_{config,recovery,pipeline_results,jobs,...}.py
├── tuning*.py / ai_*.py / pipeline_exec_*.py / platforms_*.py  # 域子模块+门面
└── src/
    ├── views/DiscoveryView.vue        # 模板不动，整文件 <1200（主脚本预期 ~600，为预期值非硬门禁）
    └── composables/useDiscovery*.ts   # 4-6 个

scripts/
├── boss_cdp_raw.py            # 薄门面/CLI 入口（CLI 行为不变）
└── boss/                      # cdp_session / exceptions / detail_extract / city_map / cli ...
```

**Structure Decision**: 沿用仓库既有扁平模块 + 包混合风格（`*_api.py`、mixin 先例），不引入与现状冲突的新层级；`runners/`、`scripts/boss/` 为宪法布局预先指定的位置。

## 批次划分（8 批）

1. **B1 快赢·source**：source.py 拆平台子模块 + 门面。
2. **B2 快赢·store**：store.py 抽 5-8 个域 mixin（含 migrations 堆叠物理归组，或挪 B7）。
3. **B3 app 设计批**：pipeline_context 对象落地，闭包改持引用（行为不变）。
4. **B4 app·runner 外迁 I**：tuning_manifest + recrawl（较小两个先行）。
5. **B5 app·runner 外迁 II**：pipeline_task。
6. **B6 app·runner 外迁 III**：ai_screen_task + 分段子模块 + app.py 收敛为薄路由。
7. **B7 搬运批**：tuning.py / ai.py / pipeline_exec.py / platforms.py / store_migrations.py 拆域子模块。
8. **B8 boss + 前端**：boss_cdp_raw.py 归位 scripts/boss/；DiscoveryView 抽 composables；收尾核对模块地图与红线。

## Complexity Tracking

无宪法违规需豁免；ai_screen 超大闭包按段拆子模块属原则 II 的执行而非违反。
