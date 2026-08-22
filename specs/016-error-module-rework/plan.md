# Implementation Plan: 报错模块整体修复与优化

**Branch**: `main`（016-error-module-rework） | **Date**: 2026-08-22 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/016-error-module-rework/spec.md`

## Summary

以"硬阻断就停、软失败记录继续"为轴心重构报错模块：实锤分档消灭账号受限误报（B069），
结构化失败行取代全文关键词分类，错误码收敛为单一注册表（四对重复码改别名、派生集合自动化），
冷却功能整体删除，登录缓存收敛两态，软失败复用 combo_issue 事件落库，
三条进度线在中断恢复时从持久断点起步。

## Technical Context

**Language/Version**: Python 3.11（后端）/ Vue 3 + TypeScript + Vite（前端）
**Primary Dependencies**: Flask（WebUI）、sqlite3（store）、CDP 抓取脚本（requests/websocket）
**Storage**: SQLite（runs / task_events / checkpoints）+ JSON 文件（login-state.json；cooldown.json 弃用）
**Testing**: unittest（后端）、Vitest（前端）、`npm run build`（dist 同步）
**Target Platform**: Windows 桌面（EXE 壳）+ 本地浏览器 CDP
**Project Type**: 桌面应用（Flask + Vue 前端）
**Constraints**: 宪法 v1.1.0 —— 超大文件只改既有行为与删码；新逻辑落小模块/新文件；验证门禁全量。

## Constitution Check

| 原则 | 结论 |
|---|---|
| I 职责分层 | 通过：分类/分档逻辑落 `boss_cdp_signals.py` 与 `error_registry.py`；app.py 只删不加 |
| II 单文件尺寸边界 | 通过：`boss_cdp_signals.py` 49→约 220；`error_registry.py` 393→约 470；`task_runners.py` 删表微缩；其余修改文件不净增行 |
| III 引用方向 | 通过：`boss_cdp_raw → boss_cdp_signals`；`source/pipeline_exec/app/task_runners → error_registry`；前端 view→api client |
| IV 拆分与重构纪律 | 通过：本 Spec 为行为修复 Spec，非拆分 Spec；超大文件无逻辑追加，只有行为修改与删除 |
| V 验证门禁 | 适用：聚焦测试 + 后端全量 + 前端测试 + `npm run build` + 卫生检查 |

## File Boundaries（已获用户确认，2026-08-22）

- **Allowed files（修改既有行为/删码）**:
  - `scripts/boss_cdp_raw.py`：探测换序；空页原地重试；连续空页只刹车；403/429 连续两次实锤；打印结构化失败行；删本地关键词表（改 import）；删"大概率被风控限制"文案。
  - `scripts/boss_cdp_signals.py`：BOSS 关键词单一来源 + 实锤分档 + 失败行 emit/parse。
  - `scripts/zhilian_cdp_raw.py`：失败路径打印结构化失败行；信号语义对齐（词表保留）。
  - `scripts/login_state_cache.py`：状态值域收敛两态；遗留 restricted 读作无缓存。
  - `webui/error_registry.py`：删 4 个重复码改别名；新增 `source_account_restricted`/`source_status_unclear`；SYSTEMIC_BLOCK_CODES 派生；导出 aliases。
  - `webui/source.py`：分类只认失败行；删全文扫描；删冷却/受限缓存写入；preflight 缓存两态；智联信号映射对齐。
  - `webui/pipeline_exec.py`：硬停集合引用派生；combo_failed 走 `_notify_combo_issue`；JD 硬停集合统一。
  - `webui/task_runners.py`：删 `_SCRAPE_BLOCK_PATTERNS`；`_classify_scrape_block` 仅作 hard_stop_code 缺失兜底。
  - `webui/app.py`：删冷却守卫/clear 端点/env-check cooldowns/受限缓存细节冷却部分；task-state 附 `combo_issues`；resume 首拍断点进度；续跑校验用统一码。
  - `webui/cooldown.py`：**整文件删除**。
  - 前端：`webui/src/errorCodes.ts`（镜像同步）、`types.ts`、`EnvCheckDialog.vue`（删冷却）、`DiscoveryView.vue`（删冷却 UI + 恢复进度断点初始化）、`TaskProgress.vue`（软失败摘要 + 断点首拍）、`api.ts`（combo_issues 类型）。
  - 测试：删除 `tests/test_cooldown.py`、`tests/test_cooldown_api.py`；修改 research R6 清单所列。
- **Forbidden files**: `webui/store.py`（事件/checkpoint 机制已有，不动）、`webui/ai.py` 与 `webui/ai_retry.py` 的重试策略、`webui/execution_config.py`、`webui/prompt_texts.py`、抓取速度配置、与报错无关模块。
- **New files**: `tests/test_risk_signal_tiers.py`（约 300 行）；本 Spec 文档产物。
- **Reference direction**: `boss_cdp_raw/zhilian_cdp_raw → boss_cdp_signals`；`webui/* → error_registry`；`view → api client`。
- **Line gate**: `boss_cdp_signals.py` ≤ 300；`error_registry.py` ≤ 550；修改的超大文件净行数不增。
- **Rationale**: 分档/分类属信号域职责，落纯分类小模块；注册表扩展属其既有职责；app.py/store.py 超大文件禁增。

## Verification Gate

- 功能交付门禁：`uv run python -m unittest discover -s tests`（全量后端）+ `cd webui && npm test -- --run` + `npm run build`（dist 同步并纳入提交）+ `uv run python -m unittest tests.test_repo_hygiene`。
- 用户已明示"全量开始"，交付执行全量门禁。

## Project Structure

### Documentation (this feature)

```text
specs/016-error-module-rework/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/error-module-contracts.md
└── tasks.md
```

### Source Code (改动热点)

```text
scripts/boss_cdp_signals.py      # 分档 + 失败行（BOSS 单一来源）
scripts/boss_cdp_raw.py          # 判定行为修改 + 删码
scripts/zhilian_cdp_raw.py       # 失败行 + 信号对齐
scripts/login_state_cache.py     # 两态收敛
webui/error_registry.py          # 统一注册表 + 派生集合
webui/source.py                  # 分类/缓存/冷却副作用删除
webui/pipeline_exec.py           # 硬停统一 + 软失败事件
webui/task_runners.py            # 删关键词表
webui/app.py                     # 删冷却 + combo_issues + 断点首拍
webui/cooldown.py                # 删除
webui/src/{errorCodes.ts,types.ts,api.ts,EnvCheckDialog.vue,DiscoveryView.vue,TaskProgress.vue}
tests/test_risk_signal_tiers.py  # 新增
```

## Implementation Phases（供 /speckit-tasks 展开）

1. **注册表统一**：别名/派生/新码/一致性测试（地基，先行）。
2. **BOSS 信号域**：signals 模块分档+失败行；脚本判定行为改造（探测换序/空页重试/连续实锤/删文案）。
3. **智联对齐**：失败行 + 信号映射。
4. **webui 分类与副作用**：source.py 分类重写、缓存两态、冷却调用删除；task_runners 删表。
5. **流水线与任务层**：pipeline_exec 硬停统一 + 软失败事件；app.py 冷却删除 + combo_issues + 断点首拍。
6. **前端**：镜像同步、冷却 UI 删除、进度断点起步、软失败展示；dist 构建。
7. **测试与门禁**：新测试 + 受影响测试改写 + 全量验证。

## Risks & Mitigations

- 退出码 10 语义被外部脚本/文档引用 → 全库检索退出码引用，仅本仓库内消费，契约文档同步。
- 前端镜像测试锁旧码 → 镜像与注册表同步为同一提交内变更。
- `webui/app.py` 删码引发隐式依赖 → 聚焦测试 + 全量后端测试兜底。
- 智联 marker 语义漂移 → 保留现有两轮收紧结果，仅加失败行，不重写词表。
