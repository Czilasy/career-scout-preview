# Implementation Plan: 日志白箱

**Branch**: `033-log-whitebox` | **Date**: 2026-09-01 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/033-log-whitebox/spec.md`

## Summary

用户要求"永恒白箱"：任何一次失败，事后必能在日志查到真实原因，不允许"出事了但什么都没留下"。排查确认根因是"接线断线"：日志体系（career-scout.log + RotatingFileHandler + 脱敏 + 任务上下文）已存在，但主进程 4 个文件用裸 `logging.getLogger`（不落盘）、子进程完全未初始化日志、脚本侧裸 `boss_cdp` logger、运行时审计未配置时静默跳过、子进程现场未记录。

本方案两层落地：**第一层接线**——把全部断点接入统一日志树；**第二层机制**——"自动开账本"（get_logger 懒初始化）保证任何进程任何入口日志必有落点，并扩展卫生测试拦截"裸日志"与"出错不留痕"两类回归。

子进程方案已确认：**做法甲**（子进程直接初始化日志写同一个 career-scout.log），带多进程写安全防护。

## Technical Context

**Language/Version**: Python 3（uv 管理）；前端 Vue 3（本功能零前端改动）

**Primary Dependencies**: 标准库 `logging`/`logging.handlers`、`subprocess`、`msvcrt`（Windows 文件锁）；Flask（日志读取路由既有）；无新增第三方依赖

**Storage**: 日志文件 `~/.career-scout/logs/career-scout.log`（RotatingFileHandler 5MB×10）+ `ai_raw.log`（既有，不动）；SQLite `task_logs`（既有，不动）

**Testing**: 后端 `uv run python -m unittest`（含卫生测试 `tests.test_repo_hygiene`）；前端 Vitest（本功能无前端改动，跑构建即可）

**Target Platform**: Windows（源码 + EXE 桌面），macOS（DMG）；双平台日志行为一致

**Project Type**: 桌面应用（内嵌 web UI）

**Performance Goals**: 日志轮转上限不变（5MB×10）；子进程默认 INFO 级别控制写量，不因接通 debug 刷爆日志

**Constraints**: 多进程写同一日志文件安全（轮转竞态防护）；测试不污染正式日志目录；`source_boss_cdp_detail.py` 行数紧贴红线 800，只做等价替换不新增逻辑

**Scale/Scope**: 单用户桌面工具；主进程 + 抓取子进程两个写日志进程

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **原则 I 职责分层**：改动集中在日志基础设施（logging_setup.py）与既有域模块的日志调用，路由/业务逻辑不混入；不违反。
- **原则 II 单文件尺寸**：`source_boss_cdp_detail.py` 797 行——本次仅等价替换日志对象（净增 ≤2 行，不超 800）；`logging_setup.py` 140→约 240 行，远低于红线；其余文件净增 ≤3 行；不违反。
- **原则 III 引用方向**：`scripts/boss/*` 已 import `webui.logging_setup`（既有方向），不新增反向依赖；tests 引用被测代码，正常；不违反。
- **原则 IV 拆分与重构纪律**：本功能是"接线"而非拆分；不新建模块。若实现中发现 `source_boss_cdp_detail.py` 行数将超线，在该批次内把 `_InProcessCapture` 整体迁出到新模块（等价搬运，不改行为）。
- **原则 V 验证门禁**：按门禁执行聚焦测试、后端全量、前端构建、卫生检查；不违反。
- **原则 VI 模块地图与落位**：改动全部落在既有域模块（日志基础设施、source 域、task 域、AI 域、脚本域），本功能不产生新代码模块，无需登记新地图条目。对超预警线文件（`source_boss_cdp_detail.py` 797、`updater.py` 724、`ai_client.py` 718、`source_boss_cdp.py` 611、`scripts/boss/detail_scrape.py` 734）：改动均为等价替换或 ≤3 行最小增量，不向红线增长；`ai_client.py` 的失败留痕为新增 1-2 行日志调用，在 800 红线内；不违反。

## File Boundaries

*GATE: 已获用户确认（2026-09-01）。*

- **Allowed files**（修改，14 个）：
  1. `webui/logging_setup.py` — 懒初始化 + 多进程写安全 + 级别 env 支持（核心改造）
  2. `webui/source_boss_cdp_detail.py` — 614 行裸 logger→统一封装；补 `_logger` 定义；in-process 通用 except 留痕带异常类型；净增 ≤2 行
  3. `webui/source_boss_cdp.py` — 删死代码 `logger` 定义（-1 行）；启动子进程 env 注入 `CAREER_SCOUT_LOG_LEVEL=INFO`
  4. `webui/source.py` — 删死代码 `logger` 定义（-1 行）
  5. `webui/updater.py` — 裸 logger→统一封装（等价替换）
  6. `webui/runtime_audit.py` — 未配置时不静默跳过：自动就绪或降级标记
  7. `webui/ai_client.py` — 失败原因留痕（新增 1-2 行日志调用）
  8. `webui/error_registry.py` — `_LOGGER` 统一走封装（等价替换）
  9. `scripts/boss_cdp_raw.py` — 子进程日志就绪确认（懒初始化后基本零改动；仅核实）
  10. `scripts/boss/constants.py` — 裸 `log = logging.getLogger("boss_cdp")` 归入统一日志树（等价替换，import 面不变）
  11. `tests/test_repo_hygiene.py` — 新增"禁止裸 logging.getLogger"检查 + 扩展"except 不留痕"检查
  12. `scripts/boss/detail_scrape.py` — 每岗位详情 terminal 结果补一行 `_logger.info`（R6，净增 1-2 行，当前 734 行）
  13. `scripts/boss/search.py` — 风控/限流判定结果补一行 `log.warning`（R6，净增 1-2 行，当前 556 行）
  14. `scripts/boss/login.py` — 登录成功补一行 `log.info`（R6，净增 1 行，当前 205 行）
  15. `webui/task_runners.py` — 任务级兜底异常补 `_logger.exception`（审查发现 FR-009，+1 行，当前 350 行）
  16. `scripts/zhilian/search.py` — 智联风险信号判定补 `log.warning`（审查发现 FR-003 扩展，+3 行，当前 364 行）

- **Forbidden files**：`webui/app.py`、`webui/store.py`（门面只 re-export，不加逻辑）；`webui/src/` 全部前端；抓取阻断（Spec A）相关逻辑（`source_boss_cdp_detail.py` 失败事件读取、`boss_cdp_raw.py` 异常映射）；`webui/ai_raw_log.py` 的设计（独立账本保留）；roadmap/、`.codebuddy/`
- **New files**：无新源码模块；仅本 spec 目录下文档（research.md、data-model.md、quickstart.md、contracts/）
- **Reference direction**: 各域模块 → `webui/logging_setup`（基础设施，只被引用不反向引用）；`scripts/boss/*` → `webui.logging_setup`（既有方向）；tests → 被测代码
- **Line gate**: `source_boss_cdp_detail.py` ≤800（等价替换）；`logging_setup.py` ≤250；`scripts/boss/detail_scrape.py`（734）≤800 且净增 ≤3 行；`webui/task_runners.py`（350）、`scripts/zhilian/search.py`（364）≤800；其余文件净增 ≤3 行
- **Rationale**: 全部改动都是既有断线的接线与机制加固，不产生新业务模块；日志基础设施改造集中在一个文件，避免能力散落；对紧贴红线的文件只做等价替换，拆分责任归 031 工程还债

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 最终门禁：相关模块聚焦测试、后端全量测试、前端 `npm run build`、仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene`）。
- 本功能为功能/重构交付，不适用收口规则（不提交不推送）。
- 验收（对应用户原话"所有错误都要能够在日志中查得到"）：
  1. 源码模式真实小抓取后，子进程抓取现场（详情页结果、登录检查、风控判定）出现在 `career-scout.log`，且带任务编号；
  2. 制造一次失败（含 in-process 未知异常路径），日志可查异常类型、阶段、任务编号；
  3. 卫生测试两条新检查生效且全绿；
  4. 前端 `/api/logs` 可查到上述日志。

## Project Structure

### Documentation (this feature)

```text
specs/033-log-whitebox/
├── plan.md              # This file
├── spec.md              # 冻结规格
├── research.md          # 技术决策（Phase 0）
├── data-model.md        # 日志记录契约（Phase 1）
├── quickstart.md        # 验证指南（Phase 1）
├── contracts/           # 日志行格式 / 自动初始化契约（Phase 1）
└── tasks.md             # Phase 2 输出（/speckit-tasks 生成）
```

### Source Code (repository root)

```text
webui/
├── logging_setup.py     # [改] 懒初始化 + 多进程写安全 + 级别 env
├── source_boss_cdp.py   # [改] 删死 logger + env 注入级别
├── source_boss_cdp_detail.py # [改] 等价替换日志对象 + 现场留痕增强
├── source.py            # [改] 删死 logger
├── updater.py           # [改] 等价替换
├── runtime_audit.py     # [改] 未配置降级
├── ai_client.py         # [改] 失败留痕
└── error_registry.py    # [改] 统一封装

scripts/boss_cdp_raw.py  # [改] 子进程日志就绪核实
scripts/boss/constants.py# [改] log 归统一树

tests/test_repo_hygiene.py # [改] 两条新卫生检查
```

**Structure Decision**: 复用既有分层结构，不引入新模块；基础设施改造集中于 `logging_setup.py`。

## Complexity Tracking

无宪法违规需要辩解；新检查规则的设计复杂度在 research.md 中说明。
