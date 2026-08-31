# Implementation Plan: 抓取阻断正确化

**Branch**: `034-scrape-block-handling` | **Date**: 2026-09-01 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/034-scrape-block-handling/spec.md`

## Summary

用户原始需求：抓取遇账号层面被拦（验证码、限流、风控、登录失效）必须"停下来、说清楚、能续跑"，且内部保留真实原因。排查确认根因是**两处接线断线**：

1. `scripts/boss_cdp_raw.py` 的 `__main__` 只捕获 `CDPUnavailableError` / `ConnectionError` / `ResultFileWriteError`，**未捕获** `RiskControlError` / `LoginRequiredError` / `RequestLimitExceededError` → 子进程模式下这三类异常裸 traceback 退出码 1 → webui 兜底分类成 `source_unknown_error` → 熔断不触发、暂停不触发、岗位全进"待确认"。
2. `webui/source_boss_cdp_detail.py::fetch_details_batch` 在子进程**非零退出**时不读 `.events.jsonl` 事件文件（脚本已把每岗位真实 `safe_code` 写进去），只用退出码粗分类 → 真实原因二次丢弃。

本方案：脚本主入口补捕获三类异常转结构化失败行 + 对应退出码（复用既有 `emit_failure_line` 与错误码体系，同 026 先例）；`fetch_details_batch` 非零退出时读事件文件按真实 `safe_code` 逐岗位归类。验证码/限流/登录失效走既有"熔断器连续信号 → 暂停 + 友好提示 + 断点续跑"路径，**不新建机制**。

## Technical Context

**Language/Version**: Python 3（uv 管理）；无前端改动

**Primary Dependencies**: 标准库 `sys`、`json`、`subprocess`；既有 `scripts/boss_cdp_signals.emit_failure_line`、`webui/source_boss_helpers._classify_failed_code`、`webui/source_breaker.SourceCircuitBreaker`；无新增第三方依赖

**Storage**: 不涉及数据库；事件文件 `.events.jsonl`（既有产物，本次读取复用）

**Testing**: 后端 `uv run python -m unittest`（含卫生测试 `tests.test_repo_hygiene`）；无前端改动

**Target Platform**: Windows（源码 + EXE 桌面），macOS（DMG）；脚本 CLI 与 webui 编排双路径

**Project Type**: 桌面应用（内嵌 web UI）+ CLI 抓取脚本

**Performance Goals**: 无性能敏感点；事件文件读取已有 `max_artifact_bytes` 上限防护，复用即可

**Constraints**: `webui/source_boss_cdp_detail.py` 797 行紧贴宪法红线 800——本次改动**只改既有非零退出分支内部**，净增行数最小化（预计 ≤15 行），若实现中发现超线，把 `_InProcessCapture` 或事件归类逻辑整体迁出到新模块（等价搬运，不改行为，同 033 先例）。`scripts/boss_cdp_raw.py` 为门面，薄映射豁免同 026。

**Scale/Scope**: 单用户桌面工具；本次只做两处接线 + 验收，不碰报错文案、暂停机制、前端交互、历史数据。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **原则 I 职责分层**：脚本主入口补捕获是"薄映射"（异常→失败行→退出码），与 026 的 `except ResultFileWriteError` 同性质、同落点（`__main__` 块），不向门面追加业务逻辑；webui 侧改动复用既有 `_read_events_file` 分类方法，不新增路由/业务层。不违反。
- **原则 II 单文件尺寸**：`source_boss_cdp_detail.py` 797 行——只改既有分支内部、净增 ≤15 行，若超 800 则按 033 先例将事件归类逻辑迁出到新模块（等价搬运）；`scripts/boss_cdp_raw.py` 129 行（薄映射豁免）；不违反。
- **原则 III 引用方向**：webui 侧 `source_boss_cdp_detail.py` → `_read_events_file`（自身方法）；脚本侧 `boss_cdp_raw.py` → `emit_failure_line`（既有 import）；无新增跨层反向依赖。不违反。
- **原则 IV 拆分与重构纪律**：本功能是"接线"而非拆分，不新建业务模块；唯一可能的新文件是行数超线时的事件归类 helper（等价搬运）。不违反。
- **原则 V 验证门禁**：按门禁执行聚焦测试、后端全量、卫生检查；无前端改动则前端构建照常执行；不违反。
- **原则 VI 模块地图与落位**：改动全部落在既有域模块（`scripts/boss_cdp_raw.py` 门面薄映射、`webui/source_boss_cdp_detail.py` detail mixin），不产生新业务域模块。对超预警线文件 `source_boss_cdp_detail.py`（797）：改动限于既有分支内部、净增最小化，超线则等价迁出（同 033 先例）；不违反。

## File Boundaries

*GATE: 已按用户确认范围推演（质询 1-5 已确认：两处接线 + 验收，不碰文案/暂停/前端/历史）。*

- **Allowed files**（修改，2 个源码文件 + 测试）：
  1. `scripts/boss_cdp_raw.py` — `__main__` 补捕获 `RiskControlError` / `LoginRequiredError` / `RequestLimitExceededError` → `emit_failure_line` + 退出码（10/1/11），与既有 `CDPUnavailableError`/`ResultFileWriteError` 捕获同构；import 补三个异常类（薄映射豁免同 026）
  2. `webui/source_boss_cdp_detail.py` — `fetch_details_batch` 非零退出分支（现 281-308 行）：读取事件文件，按真实 `safe_code` 逐岗位归类；缺失岗位沿用 `_classify_failed_code` 兜底；账号级码走熔断器信号
  3. `tests/test_scrape_block_classification.py` — 新增假样本回归测试（脚本异常→失败行→webui 分类→熔断信号）
- **Forbidden files**：白箱 033 已改文件（`webui/logging_setup.py`、`webui/runtime_audit.py`、`webui/source.py`、`webui/source_boss_cdp.py`、`webui/updater.py`、`webui/error_registry.py`、`webui/ai_client.py`、`scripts/boss/constants.py`、`scripts/boss/search.py`、`scripts/boss/login.py`、`scripts/boss/detail_scrape.py`、`webui/task_runners.py`）；`webui/error_registry.py`（错误码/文案已完备，本次不新增）；`webui/src/` 全部前端；`webui/app.py`、`webui/store.py`；数据库；roadmap/、`.codebuddy/`
- **New files**：`tests/test_scrape_block_classification.py`（聚焦测试，~150-250 行）；若 `source_boss_cdp_detail.py` 超线则新 `webui/source_boss_detail_events.py`（事件归类 helper，等价搬运）；本 spec 目录下文档（research.md、data-model.md、quickstart.md、contracts/）
- **Reference direction**: 脚本侧 `scripts/boss_cdp_raw.py`（`__main__` 薄映射）→ `scripts/boss_cdp_signals.emit_failure_line`（既有）；webui 侧 `source_boss_cdp_detail.py` → `_read_events_file` / `_classify_failed_code`（既有，本文件内/同域 helpers）；tests → 被测代码。无新增跨模块引用。
- **Line gate**: `scripts/boss_cdp_raw.py` ≤140（净增 ≤10，薄映射）；`source_boss_cdp_detail.py` ≤800（仅改既有分支内部、净增 ≤15；超线则迁出事件归类逻辑）；`tests/test_scrape_block_classification.py` ≤300
- **Rationale**: 改动全部是既有断线的接线，复用已有错误码体系、失败行契约、熔断器、事件文件，不产生新业务机制；对紧贴红线的文件只做分支内最小改动，拆分责任归 031 工程还债（033 同口径）

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 最终门禁：相关模块聚焦测试、后端全量测试、前端 `npm run build`、仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene`）。
- 本功能为功能交付，不适用收口规则（不提交不推送）。
- 验收（对应用户已确认质询 4：假样本回归 + 真实账号冒烟，不去真实触发拦截）：
  1. 假样本：构造验证码/限流/登录失效/请求上限的脚本输出样本 → 脚本主入口补捕获后输出结构化失败行 + 正确退出码 → webui 分类成对应账号级码 → 熔断信号推进；构造单条软失败样本 → 带原因进"待确认"；
  2. 假样本：`fetch_details_batch` 非零退出且事件文件含账号级码 → 逐岗位真实 `safe_code` 归类，账号级码不进"待确认"；
  3. 事件文件缺失场景 → 回退 `_classify_failed_code`，不崩溃；
  4. 真实账号跑一次正常小抓取冒烟，基本盘无回归。

## Project Structure

### Documentation (this feature)

```text
specs/034-scrape-block-handling/
├── plan.md              # This file
├── spec.md              # 冻结规格
├── research.md          # 技术决策（Phase 0）
├── data-model.md        # 失败分类/信号契约（Phase 1）
├── quickstart.md        # 验证指南（Phase 1）
├── contracts/           # 退出码/失败行/事件文件契约（Phase 1）
└── tasks.md             # Phase 2 输出（/speckit-tasks 生成）
```

### Source Code (repository root)

```text
scripts/boss_cdp_raw.py  # [改] __main__ 补捕获三类异常 → 失败行 + 退出码
webui/source_boss_cdp_detail.py # [改] fetch_details_batch 非零退出读事件文件归类
tests/test_scrape_block_classification.py # [新] 假样本回归聚焦测试
```

**Structure Decision**: 复用既有分层结构，不引入新业务模块；接线改动最小化。

## Complexity Tracking

无宪法违规需要辩解；事件归类逻辑若因行数超线迁出到 `webui/source_boss_detail_events.py`，为等价搬运（同 033 先例），不改变本 plan 的行为设计。
