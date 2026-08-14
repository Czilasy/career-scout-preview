# Implementation Plan: 抓取批次失败透出、重抓分流与暂停提示整修

**Branch**: `main`（用户决定本分支实施） | **Date**: 2026-08-14 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/012-scrape-batch-fixes/spec.md`

## Summary

B050/B051/B052/B053 一起交付：BOSS 详情并行 worker 异常透出到主流程并保留部分结果；请求计数改为按单次抓取运行隔离、上限 999、命中返回 `source_request_limit_exceeded`；JD 详情批空批次不再直接误判 `source_cdp_unavailable`；重抓后端校验放宽为“source run 快照内无最终判定”并按 JD 有无分流；暂停/失败提示统一为中文原因 + 红色错误字段内联，移除诊断盒与复制按钮。

## Technical Context

**Language/Version**: Python 3.11（后端）、Vue 3 + TypeScript + Vite（前端）

**Primary Dependencies**: Flask、SQLite、threading/queue、Vue 3、Vitest（现有工具链）

**Storage**: SQLite；本轮无数据库 schema 变更、无 migration。

**Testing**: 后端 `unittest`；前端 `vitest`；构建 `npm run build`；仓库卫生 `uv run python -m unittest tests.test_repo_hygiene`。

**Target Platform**: 本地 Web 工作台 / 桌面 EXE（pywebview）

**Project Type**: 单仓库 Web + 桌面壳应用

**Performance Goals**: 请求计数为运行级内存状态；worker 异常收集为线程安全列表；无新增 I/O 路径。

**Constraints**:
- B050/B053 只修 BOSS 路径；智联并行 worker 不动。
- `scripts/boss_cdp_raw.py` 本轮作为显式修复目标开放修改；其余超大文件只做最小接线。
- 不改变列表阶段空批次判定；不改变 CDP 自动重启语义。
- 新错误码进入统一注册表与前端镜像，未知码校验规则不变。

**Scale/Scope**: 单用户本地工具；B050/B053 集中在 BOSS 详情并行链路，B051 为后端重抓校验 + 已有分流逻辑接线，B052 为前端展示。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- 职责分层：错误码仍由 `webui/error_registry.py` 统一承载；`source.py`/`pipeline_exec.py` 只做失败码映射与硬停集合接线；`boss_cdp_raw.py` 只做计数隔离与线程透出。
- 单文件尺寸：`boss_cdp_raw.py` 为超大文件，本轮是 B050/B053 的显式修复目标，只追加运行级计数封装与 worker 异常收集，不搬业务逻辑；`TaskProgress.vue` 增量 ≤60 行。
- 引用方向：后端 `source.py / pipeline_exec.py → error_registry.py`；前端 `TaskProgress.vue → errorCodes.ts`；无反向依赖。
- 拆分纪律：本轮是缺陷修复，不是重构；不改数据库结构、不改接口契约，只放宽重抓校验语义并保持错误码兼容。
- 验证门禁：最终按功能交付全量门禁执行。

## File Boundaries

*GATE: Must be completed before `/speckit-tasks`. User confirmation received as part of requirement freeze.*

- **Allowed files**:
  - `scripts/boss_cdp_raw.py`：运行级请求计数封装（`reset_request_counter()`/运行上下文）、`RequestLimitExceededError`、worker 异常收集与透出、退出码 11 映射。
  - `webui/source.py`：`_classify_failed_code` 增加退出码 11 → `source_request_limit_exceeded`；JD 详情批空批次不再判 `source_cdp_unavailable`；in-process 异常映射补 `RequestLimitExceededError`。
  - `webui/pipeline_exec.py`：JD 硬停集合加入 `source_request_limit_exceeded`。
  - `webui/error_registry.py`：新增 `source_request_limit_exceeded` 注册项并进入系统性阻断集合。
  - `webui/app.py`：`/api/pipeline/recrawl` 校验逻辑改为“source run 快照内无最终判定”并保留 `non_pending_job_ids` 拒绝；不改任务执行函数。
  - `webui/src/errorCodes.ts`：新增错误码与中文文案镜像。
  - `webui/src/components/TaskProgress.vue`：暂停/失败两态统一内联展示；移除诊断盒与复制按钮。
  - `webui/src/styles.css`：红色错误字段内联样式；删除诊断盒样式（若不再被引用）。
  - 测试文件：`tests/test_source.py`、`tests/test_healthy_pipeline.py`、`tests/test_inprocess_execution.py`、`tests/test_error_registry.py`、`webui/src/__tests__/errorCodes.spec.ts`、`webui/src/components/__tests__/TaskProgress.spec.ts`。
- **Forbidden files**:
  - `webui/store.py`、`webui/store_migrations.py`、`webui/workbench.py`、`webui/result_history*.py`、`webui/ai*.py`、`scripts/zhilian_cdp_raw.py`：本轮不修改。
  - `webui/app.py`：只允许 `pipeline_recrawl` 校验段最小改动，不追加新路由或业务方法。
- **New files**: 无（沿用现有模块与测试文件）。
- **Reference direction**:
  - 后端：`source.py / pipeline_exec.py → error_registry.py`；`boss_cdp_raw.py` 提供 `RequestLimitExceededError` 与运行级计数供 `source.py` 映射。
  - 前端：`TaskProgress.vue → errorCodes.ts`；无反向依赖。
- **Line gate**: `boss_cdp_raw.py` 增量 ≤120 行；`app.py` 增量 ≤40 行；`TaskProgress.vue` 增量 ≤60 行；其余文件仅来源替换/集合扩展。
- **Rationale**: B050/B053 的根因就在 `boss_cdp_raw.py` 并行分支，必须显式修改；B051 只动校验段，任务内分流逻辑已存在；B052 是组件内展示收敛，不需要新文件。

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能/重构/拆分交付：最终门禁为相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 收口发布任务（版本提升、打包、提交、推送、Release）：不要求全量测试；按根 `AGENTS.md` 收口规则执行卫生测试、hooks、`git diff --check`、`git status` 和 `scripts/release_check.ps1`（若存在）。
- 只有 Spec 明确写入或用户明确要求时，收口任务才执行全量测试。

## Project Structure

### Documentation (this feature)

```text
specs/012-scrape-batch-fixes/
├── spec.md          # 需求与验收
├── plan.md          # This file
├── tasks.md         # Phase 2 output
└── checklists/requirements.md
```

### Source Code (repository root)

```text
scripts/
└── boss_cdp_raw.py        # 运行级计数、RequestLimitExceededError、worker 异常透出

webui/
├── error_registry.py      # source_request_limit_exceeded 注册
├── source.py              # 退出码 11 映射、详情空批次判定收窄
├── pipeline_exec.py       # JD 硬停集合扩展
├── app.py                 # recrawl 校验放宽（最小改动）
└── src/
    ├── errorCodes.ts      # 错误码镜像
    ├── styles.css         # 内联红色字段样式
    └── components/TaskProgress.vue  # B052 展示收敛
```

**Structure Decision**: 沿用现有模块，不新建文件；超大文件只做最小 diff，避免引入新耦合。

## Complexity Tracking

> 无宪法违规；不填复杂度表。
