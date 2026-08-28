# Implementation Plan: 错误码统一、AI 可观测性与重试策略整修

**Branch**: `main`（用户决定本分支实施） | **Date**: 2026-08-14 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/011-error-ai-resilience/spec.md`

## Summary

B042-B045 一起交付：B042 统一空城市两个开始入口的“确认后按全国”行为；B043 建立唯一错误码注册表并保留旧常量兼容导出；B044 把 AI 原始响应写入独立本地轮转日志；B045 将 AI 重试改为按错误码退避 + 抖动 + 总上限，并把调优 manifest 与默认策略统一。实现上优先新建独立模块，超大文件只做最小接线。

## Technical Context

**Language/Version**: Python 3.11（后端）、Vue 3 + TypeScript + Vite（前端）

**Primary Dependencies**: Flask、SQLite、requests、logging.handlers.RotatingFileHandler、Vue 3、Vitest（现有工具链）

**Storage**: SQLite；本轮无数据库 schema 变更、无 migration。

**Testing**: 后端 `unittest`；前端 `vitest`；构建 `npm run build`；仓库卫生 `uv run python -m unittest tests.test_repo_hygiene`。

**Target Platform**: 本地 Web 工作台 / 桌面 EXE（pywebview）

**Project Type**: 单仓库 Web + 桌面壳应用

**Performance Goals**: 原始响应日志单条上限 500KB；重试总等待上限 60s；错误码查询为内存字典 O(1)。

**Constraints**:
- 不扩大超大文件：`webui/app.py` 本轮不修改；`webui/store.py`、`webui/ai.py`、`webui/src/views/DiscoveryView.vue` 只做最小接线。
- 不改变系统性暂停语义；`invalid_response` 只给精筛单条 1 次重试。
- 日志只写 `~/.career-scout/logs/`，不进入仓库；不记录 API Key/Cookie。

**Scale/Scope**: B042 前端交互，B043 跨后端/前端重构，B044/B045 集中在 AI 调用链路。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- 职责分层：错误码注册表、AI 原始日志、AI 重试策略分别进独立模块；路由层不新增业务实现。
- 单文件尺寸：新模块预计低于 800 行；现有超大文件只做最小 diff。
- 引用方向：后端 `error_registry` 为纯数据模块；`source/pipeline_exec/store/ai → error_registry`；`ai.py → ai_retry.py、ai_raw_log.py`；前端 `DiscoveryView.vue → discovery.ts`、`types.ts → errorCodes.ts`。
- 拆分纪律：本 Spec 是功能/重构交付；`store.py` 仅做常量来源替换，不搬业务逻辑。
- 验证门禁：最终按功能交付全量门禁执行。

## File Boundaries

*GATE: Must be completed before `/speckit-tasks`. User confirmation required before tasks generation.*

- **Allowed files**:
  - `webui/src/views/DiscoveryView.vue`：仅 B042 两个入口空城市确认逻辑最小接线。
  - `webui/src/discovery.ts`：新增空城市确认判定纯函数，供视图与测试复用。
  - `webui/src/views/__tests__/DiscoveryView.spec.ts`、`webui/src/__tests__/discovery.spec.ts`：B042 前端测试。
  - `webui/error_registry.py`（新增）：唯一错误码注册表、派生集合、`to_json()`、`validate_code()`、旧常量兼容导出。
  - `webui/source.py`：`SAFE_FAILURE_CODES` 改为从注册表导入并保持导出名。
  - `webui/pipeline_exec.py`：`ERROR_TAXONOMY`、`_FAILED_CODE_LABELS`、`_HARD_STOP_CODES` 改为注册表派生/兼容导出。
  - `webui/store.py`：`SYSTEMIC_BLOCK_CODES`、`INDEPENDENT_FAILURE_CODES` 改为从注册表导入并保持导出名。
  - `webui/ai.py`：AI 错误常量与用户文案改为注册表兼容导出；接入原始响应日志；按新重试策略执行。
  - `webui/src/errorCodes.ts`（新增）：前端错误码与文案镜像。
  - `webui/src/types.ts`：错误码类型引用/镜像 `errorCodes.ts`，保持现有 API 形状。
  - `webui/ai_raw_log.py`（新增）：`ai_raw.log` 轮转、脱敏、500KB 截断与 `record_raw_ai_response()`。
  - `webui/logging_setup.py`：提供 `ai_raw.log` 日志目录/轮转复用入口，不搬业务逻辑。
  - `webui/ai_retry.py`：默认重试策略改为按错误码退避 + 抖动 + 总上限；调优 override 结构兼容现有 manifest。
  - `webui/tuning.py`：manifest `retry_policy` 校验与默认策略同构，缺失/非法回退默认。
  - 测试文件：`tests/test_error_registry.py`（新增）、`tests/test_ai_raw_log.py`（新增）、`tests/test_ai_retry.py`、`tests/test_ai.py`、`tests/test_tuning.py`、`webui/src/__tests__/errorCodes.spec.ts`（新增）。
- **Forbidden files**:
  - `webui/app.py`：本轮不修改。
  - `webui/store_migrations.py`、`webui/workbench.py`、`webui/result_history.py`、`webui/result_history_api.py`、`webui/store_result_history_mixin.py`：不修改。
  - `scripts/boss_cdp_raw.py`、`scripts/zhilian_cdp_raw.py`：不修改。
  - `webui/store.py`：只允许常量来源替换，不追加业务方法。
  - `webui/ai.py`：只允许常量来源、原始日志调用、重试策略调用三处接线，不追加新业务逻辑。
- **New files**:
  - `webui/error_registry.py`（约 300 行）：错误码注册项、派生集合、兼容导出、`to_json()`/`validate_code()`。
  - `webui/ai_raw_log.py`（约 120 行）：`record_raw_ai_response()`、轮转 handler、截断与脱敏。
  - `webui/src/errorCodes.ts`（约 120 行）：前端错误码与文案镜像。
  - 新增测试：`tests/test_error_registry.py`、`tests/test_ai_raw_log.py`、`webui/src/__tests__/errorCodes.spec.ts`。
- **Reference direction**:
  - 后端：`source.py / pipeline_exec.py / store.py / ai.py → error_registry.py`；`ai.py → ai_retry.py、ai_raw_log.py`；`ai_raw_log.py → logging_setup.py`。
  - 前端：`DiscoveryView.vue → discovery.ts → api.ts`；`types.ts → errorCodes.ts`；无反向依赖。
- **Line gate**: 新模块 <800 行；`DiscoveryView.vue` 增量 ≤120 行；`store.py` 增量 ≤20 行；`ai.py` 增量 ≤80 行；`source.py`/`pipeline_exec.py`/`tuning.py` 只做来源替换与校验扩展。
- **Rationale**: B043/B044/B045 都适合独立模块承载，避免继续膨胀大文件；B042 是前端最小交互，纯函数放 `discovery.ts` 便于测试；`error_registry.py` 同时服务后端与前端同步测试。

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能/重构/拆分交付：最终门禁为相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 收口发布任务（版本提升、打包、提交、推送、Release）：不要求全量测试；按根 `AGENTS.md` 收口规则执行卫生测试、hooks、`git diff --check`、`git status` 和 `scripts/release_check.ps1`（若存在）。
- 只有 Spec 明确写入或用户明确要求时，收口任务才执行全量测试。

## Project Structure

### Documentation (this feature)

```text
specs/011-error-ai-resilience/
├── spec.md     # 需求与验收
├── plan.md     # This file
└── tasks.md    # Phase 2 output
```

### Source Code (repository root)

```text
webui/
├── error_registry.py      # 新增：唯一错误码注册表
├── ai_raw_log.py          # 新增：AI 原始响应日志
├── ai_retry.py            # 默认重试策略（按错误码退避/抖动/总上限）
├── ai.py                  # 常量来源替换 + 原始日志 + 新重试策略接线
├── source.py              # SAFE_FAILURE_CODES 来源替换
├── pipeline_exec.py       # ERROR_TAXONOMY/_HARD_STOP_CODES 来源替换
├── store.py               # SYSTEMIC/INDEPENDENT 集合来源替换
├── logging_setup.py       # ai_raw.log 轮转入口
├── tuning.py              # retry_policy 同构校验
└── src/
    ├── errorCodes.ts      # 新增：前端错误码镜像
    ├── types.ts           # 引用 errorCodes.ts
    ├── discovery.ts       # B042 空城市确认纯函数
    └── views/DiscoveryView.vue  # B042 两个入口最小接线
```

**Structure Decision**: 沿用本仓库 mixin/service 分层与前端纯函数测试模式；B043 注册表作为后端唯一数据源，前端镜像由测试锁定。

## Complexity Tracking

> 无宪法违规；不填复杂度表。
