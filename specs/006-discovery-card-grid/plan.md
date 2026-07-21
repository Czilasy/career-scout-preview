# Implementation Plan: 发现结果页卡片网格化与体验修复

**Branch**: `006-discovery-card-grid` | **Date**: 2026-07-22 | **Spec**: [spec.md](file:///d:/项目/boss/specs/006-discovery-card-grid/spec.md)

**Input**: Feature specification from `specs/006-discovery-card-grid/spec.md`

## Summary

将发现流程第 4 步结果页从竖向单列列表改为自适应卡片网格，完整展示 JD；清理顶部遗留死元素和「简历驱动发现」横条；修复刷新丢失结果的 bug（`restoreDiscoveryRun()` 未被 `init()` 调用）；确保标记后卡片标灰保留可撤销。所有改动限于 `webui/index.html` 单文件，不新增后端端点或数据模型。

## Technical Context

**Language/Version**: HTML + CSS + 原生 JavaScript（单文件 `webui/index.html`，约 4757 行）

**Primary Dependencies**: 无新增。现有依赖为 Flask（后端 `webui/app.py`）+ SQLite（`webui/store.py`），本次不涉及。

**Storage**: SQLite（现有，不改动）+ localStorage（现有，`boss-discovery-run` 键存储 run_id）

**Testing**: `python -m unittest tests.test_chrome_setup`（全 mock，不测前端 UI）；前端验证为手动浏览器测试。

**Target Platform**: 浏览器（Chrome），桌面 + 窄屏自适应。

**Project Type**: web-service（Flask + 单文件前端）

**Performance Goals**: 结果页渲染 149 条岗位卡片时无明显卡顿（<2 秒可交互）。

**Constraints**: 仅改 `webui/index.html` 一个文件；不破坏现有后端 API 和数据模型；不引入新依赖。

**Scale/Scope**: 单文件改动，涉及 CSS（列表容器改 grid、卡片去 margin-bottom、加标灰规则）、DOM（删除死元素块+横条、移动按钮）、JS（`init()` 加 1 行调用、`createDiscoveryCard` 精简为三区+删 6 个区块渲染代码+加标灰 class 逻辑）。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

无 `.specify/memory/constitution.md` 文件，跳过宪法检查。

## Project Structure

### Documentation (this feature)

```text
specs/006-discovery-card-grid/
├── plan.md              # 本文件
├── research.md          # Phase 0 调研输出
├── data-model.md        # Phase 1 数据模型（无新增，记录复用）
├── quickstart.md        # Phase 1 验证指南
├── contracts/           # Phase 1 合约（无新增，记录复用）
│   └── http-api.md
└── tasks.md             # Phase 2 任务清单（/speckit-tasks 生成）
```

### Source Code (repository root)

```text
webui/
├── index.html           # 唯一改动文件（CSS + DOM + JS）
├── app.py               # 不改动（后端路由已满足需求）
├── store.py             # 不改动（数据模型已满足需求）
└── ...                  # 其他 webui 模块不改动
```

**Structure Decision**: 单文件改动。所有 CSS、DOM、JS 逻辑集中在 `webui/index.html`。后端 `app.py` 和 `store.py` 已具备所需能力（反馈端点、结果 API、桥接机制），不需要修改。

## Complexity Tracking

无宪法违规，无需记录。
