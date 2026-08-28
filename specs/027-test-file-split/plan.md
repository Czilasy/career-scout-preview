# Implementation Plan: 测试大文件拆分重构（027）

**Branch**: `027-test-file-split` | **Date**: 2026-08-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/027-test-file-split/spec.md`

## Summary

对 7 个超 2000 行的测试文件（合计约 31,084 行）做纯搬运拆分：每个原巨型文件归置为一个子目录，域文件按被测域划分，单文件 ≤2000 行（域簇内聚不允许硬切时 ≤2200 并记录理由）。等价性用双验收证明：开工前拍基线快照（用例总数 + 全部「类名.方法名」清单），每批及终检逐条对账零差异 + 后端全量全绿。跨拆分文件共用的模块级帮手先抽到域内共享模块（`harness.py`/`builders.py`），单一使用者随类搬迁。不留兼容门面（反向依赖已实测为零），原文件随批删除。每批独立 `refactor` 提交，任何批次边界仓库都可交付、可回滚。

## Technical Context

**Language/Version**: Python 3.11+（uv 管理）

**Primary Dependencies**: unittest（后端测试）、Flask（被测应用，不改）

**Storage**: 无（不改任何 schema 与数据格式；测试用临时库行为不变）

**Testing**: `uv run python -m unittest discover -s tests`（CI 口径，保持不变）；聚焦口径 `uv run python -m unittest discover -s tests/<子目录>`（基线批实证）

**Target Platform**: 与现仓库一致（Windows 开发 + CI ubuntu），本 Spec 不触碰平台相关代码

**Project Type**: 桌面应用仓库的测试代码重构（仅 `tests/`）

**Performance Goals**: 无新增；全量测试耗时与拆分前同量级（收集机制变化不得显著拖慢收集）

**Constraints**: 纯搬运——不改测试逻辑、不改断言、不改产品代码、不改收集命令；基线快照（2026-08-28 实测 1786 用例）逐条守恒

**Scale/Scope**: 7 个文件 ≈ 31,084 行 → 7 个子目录、约 22 个域文件 + 2 个共享帮手模块 + 7 个空 `__init__.py`

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 状态 | 说明 |
|---|---|---|
| I. 职责分层 | ✅ | 约束对象为业务分层；本 Spec 按被测域归置测试，方向一致 |
| II. 单文件尺寸边界 | ✅ | 原则约束业务文件 ≤800；测试文件不在其列，本 Spec 以用户拍板的 ≤2000（≤10% 豁免）为测试域门禁 |
| III. 引用方向 | ✅ | 拆分文件 → 共享帮手模块单向；拆分文件之间不互 import 私有符号；帮手模块不反向依赖 |
| IV. 拆分与重构纪律 | ✅ | 独立 Spec、纯搬运、文件边界章节、不借机修 bug |
| V. 验证门禁 | ⚠️ 豁免 | 纯测试搬运不触碰产品代码与前端，经用户 2026-08-28 拍板豁免前端测试与 `npm run build`；保留聚焦 + 后端全量 + 卫生 + 清单对账 |
| VI. 模块地图与落位规则 | ✅ 不适用 | 模块地图覆盖业务域模块（webui/、scripts/）；测试子目录不属地图范围，不登记；门面禁改条款与本 Spec 无交集 |

Phase 1 设计后复查：无新增违规；原则 V 豁免已在 spec FR-005 与本表双重记录。

## File Boundaries

*已过文件放置门（用户 2026-08-28 确认）。*

- **Allowed files（删除）**: `tests/test_webui_app.py`、`tests/test_healthy_pipeline.py`、`tests/test_tuning.py`、`tests/test_source.py`、`tests/test_ai.py`、`tests/test_webui_store.py`、`tests/test_chrome_setup.py`（各自所属批次内删除）
- **New files**（全部位于 `tests/`，命名以批次启动时实测盘点微调，结构不变）:
  - `tests/chrome_setup/`：`__init__.py` + 约 2 个域文件
  - `tests/webui_store/`：`__init__.py` + 约 2 个域文件
  - `tests/source/`：`__init__.py` + 约 2 个域文件
  - `tests/ai/`：`__init__.py` + 约 2 个域文件（`tests.test_workbench_fixtures` import 随使用类搬迁）
  - `tests/tuning/`：`__init__.py` + `builders.py`（5 个共用构造器/替身）+ 约 3 个域文件
  - `tests/healthy_pipeline/`：`__init__.py` + `harness.py`（6 个共用模块级函数）+ 约 5 个域文件
  - `tests/webui_app/`：`__init__.py` + 约 6 个域文件（核心路由/流程续跑/账号设置/调优路由/平台感知/集成契约）
- **Forbidden files**: `webui/**`、`scripts/**`、`webui/src/**`（前端）、`.github/**`、`hooks/**`、`pyproject.toml`、`uv.lock`、2000 行以下的既有测试文件、`tests/fixtures/**`、`tests/run_isolated_webui.py`、`tests/sc002_24h_monitor.py`、`tests/sc015_viewport_check.py`、`tests/test_workbench_fixtures.py` 等非拆分范围文件
- **Reference direction**: 拆分文件 → 域内共享帮手模块（`tests/<域>/harness.py`、`builders.py`）单向；帮手模块只 import 产品代码与标准库；拆分文件之间禁止互 import；对仓内其他测试模块的既有 import（`tests.test_cross_platform_dedupe`、`tests.test_workbench_fixtures`）原样保留
- **Line gate**: 全部批次完成后 `tests/` 内无测试文件 >2000 行；带理由豁免的 ≤2200，豁免理由写入该批提交说明
- **Rationale**: 7 文件占全部测试代码六成以上，按域建子目录是唯一同时满足「规整」「可维护」「纯搬运可收口」的落法；反向依赖实测为零，故不留门面、直接删除原文件

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 拆分批次交付：本批聚焦测试 → 后端全量测试 → 用例清单对账（零差异）→ 行数核对 → 仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene`）→ `git diff --check` / `git status` → 单个 `refactor` 提交（不 push）。
- 经用户 2026-08-28 拍板：纯测试搬运豁免前端测试与 `npm run build`。
- 收口任务不在本 Spec 范围；版本不提升（纯测试重构，产品行为零变化）。

## Project Structure

### Documentation (this feature)

```text
specs/027-test-file-split/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── compatibility.md
└── tasks.md
```

### Source Code (repository root, 仅 tests/ 变化)

```text
tests/
├── chrome_setup/        ← test_chrome_setup.py(2350)
│   ├── __init__.py
│   └── test_*.py ×2
├── webui_store/         ← test_webui_store.py(2739)
│   ├── __init__.py
│   └── test_*.py ×2
├── source/              ← test_source.py(2925)
│   ├── __init__.py
│   └── test_*.py ×2
├── ai/                  ← test_ai.py(2880)
│   ├── __init__.py
│   └── test_*.py ×2
├── tuning/              ← test_tuning.py(5103)
│   ├── __init__.py
│   ├── builders.py      # 共用构造器与测试替身（非 test 前缀，不被收集）
│   └── test_*.py ×3
├── healthy_pipeline/    ← test_healthy_pipeline.py(6174)
│   ├── __init__.py
│   ├── harness.py       # 共用模块级函数（非 test 前缀，不被收集）
│   └── test_*.py ×5
├── webui_app/           ← test_webui_app.py(8913)
│   ├── __init__.py
│   └── test_*.py ×6
├── fixtures/            # 不动
└── 其余 <2000 行测试文件  # 不动
```

**Structure Decision**: 沿用 `tests/` 扁平 + 数据目录（`fixtures/`）现状，为 7 个巨型文件各建一个子目录；子目录含空 `__init__.py` 以保证 `discover -s tests` 跨 Python 版本稳定递归收集（不依赖命名空间包隐式行为）。共享帮手模块以非 `test` 前缀命名，天然不被收集。

## 批次划分（9 批）

0. **B0 基线**：工作区清零（9 个脏文件按仓库惯例处置，处置方式由用户确认）→ 全量基线跑一次记录结果 → 拍快照（总数 + 「类名.方法名」清单，存系统临时目录）→ 实证聚焦收集命令形态。基线不绿先停。
1. **B1 快赢·chrome_setup（机制验证批）**：`test_chrome_setup.py`(2350) → `tests/chrome_setup/` 2 个域文件。本批额外职责：实证子目录收集、聚焦命令、清单对账全链路，后续批次照此复制。
2. **B2 快赢·webui_store**：`test_webui_store.py`(2739) → 2 个域文件。
3. **B3 source**：`test_source.py`(2925) → 2 个域文件。
4. **B4 ai**：`test_ai.py`(2880) → 2 个域文件；`tests.test_workbench_fixtures` import 随类搬迁。
5. **B5 tuning**：抽 `builders.py`（5 个共用符号）→ `test_tuning.py`(5103) 拆 3 个域文件。
6. **B6 healthy_pipeline**：抽 `harness.py`（6 个共用函数）→ `test_healthy_pipeline.py`(6174) 拆约 5 个域文件（切片状态/收敛恢复/收敛统一/暂停续跑/语义杂项）。
7. **B7 webui_app**：`test_webui_app.py`(8913) 拆约 6 个域文件；`_tuning_quality_context`、`_make_valid_manifest_payload_web` 随 TuningManifestRouteTests 整组搬；跨文件继承的 2 个类连 import 同迁。
8. **B8 终检**：全仓测试文件行数终检（≤2000/带理由 ≤2200）→ 清单终对账 → `git diff` 核对产品代码零改动 → BACKLOG 更新（B075 归档、订正 2525→实测值）→ 卫生收尾。

## Complexity Tracking

| 豁免 | 为何需要 | 被否决的更简替代 |
|---|---|---|
| 原则 V 前端门禁豁免 | 纯测试搬运零触碰前端与产品代码，前端验证无对象 | 照跑前端全套：每批徒增构建时间，7 批累计成本无任何防护收益（用户拍板豁免） |
