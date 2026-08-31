---
description: "Task list for 日志白箱 implementation"
---

# Tasks: 日志白箱

**Input**: Design documents from `/specs/033-log-whitebox/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/logging-contract.md

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## File Boundaries

Resolved from plan.md（用户已确认 2026-09-01）：

- **Allowed files**: `webui/logging_setup.py`、`webui/source_boss_cdp_detail.py`、`webui/source_boss_cdp.py`、`webui/source.py`、`webui/updater.py`、`webui/runtime_audit.py`、`webui/ai_client.py`、`webui/error_registry.py`、`scripts/boss_cdp_raw.py`、`scripts/boss/constants.py`、`scripts/boss/detail_scrape.py`、`scripts/boss/search.py`、`scripts/boss/login.py`、`tests/test_repo_hygiene.py`、`tests/test_logging_setup.py`、`tests/` 下新增聚焦测试文件
- **Forbidden files**: `webui/app.py`、`webui/store.py`、`webui/src/` 全部前端、`webui/ai_raw_log.py` 的设计、抓取阻断（Spec A）相关逻辑、roadmap/、`.codebuddy/`
- **New files**: 无新源码模块；仅测试文件可新增（如 `tests/test_logging_whitebox.py`）
- **Reference direction**: 各域模块 → `webui/logging_setup`（基础设施）；`scripts/boss/*` → `webui.logging_setup`（既有方向）；tests → 被测代码
- **Line gate**: `source_boss_cdp_detail.py` ≤800（等价替换，净增 ≤2 行）；`logging_setup.py` ≤250；`scripts/boss/detail_scrape.py` ≤800（净增 ≤3 行）；其余文件净增 ≤3 行

## Verification Gate

- 功能/重构交付：最终门禁为相关模块聚焦测试、后端全量测试、前端 `npm run build`、仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene`）。
- 收口发布任务不适用本清单（本功能不提交不推送）。

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 本项目为既有仓库，无需项目初始化。跳过代码级 Setup；基线验证并入 Phase 2 Checkpoint。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 日志基础设施改造——"自动开账本"、多进程写安全、级别 env。本阶段是 US1/US2 的地基。

**⚠️ CRITICAL**: 本阶段完成前，任何用户故事不得开工。

- [X] T001 在 `webui/logging_setup.py` 实现 `get_logger()` 懒初始化：首次调用且 `is_configured()` 为假时自动 `configure_logging()`；测试上下文（`sys.modules` 含 `pytest`/`unittest`）时日志目录落到 `tempfile.gettempdir()/career-scout-test-logs`；懒初始化时打一行 INFO 记录实际日志路径。不覆盖已有配置（`force=False` 语义）
- [X] T002 在 `webui/logging_setup.py` 新增 `SafeRotatingFileHandler`：`doRollover` 持跨进程文件锁（Windows `msvcrt.locking` 锁 `career-scout.log.lock`；POSIX `fcntl.flock`；均不可用则退化为无锁）；写/轮转 `OSError` 时降级（跳过本轮轮转继续追加、文件被删则重建），不崩溃不丢已写内容；`configure_logging()` 改用该 handler
- [X] T003 在 `webui/logging_setup.py` 的 `configure_logging()` 支持 `level` 默认值从 env `CAREER_SCOUT_LOG_LEVEL` 读取（未设置保持 DEBUG）
- [X] T004 扩展 `tests/test_logging_setup.py` 覆盖：懒初始化自动建文件、测试上下文写临时目录、已有配置不被覆盖、`SafeRotatingFileHandler` 锁与 `OSError` 降级、env 级别解析

**Checkpoint**: 基础就绪——`uv run python -m unittest tests.test_logging_setup` 通过；`get_logger` 在任何进程调用即有落点。

---

## Phase 3: User Story 1 - 任何失败都能查到真实原因 (Priority: P1) 🎯 MVP

**Goal**: 主进程全部断线接通——所有失败路径的日志真正落盘 career-scout.log。

**Independent Test**: 制造一次失败（含 in-process 未知异常路径），日志能查到异常类型、阶段、任务编号，而非仅"抓取执行失败"。

### Implementation for User Story 1

- [X] T005 [US1] 在 `webui/source_boss_cdp_detail.py`：删除 32 行裸 `logger = logging.getLogger(__name__)`，文件顶部补 `_logger = get_logger(__name__)` 定义（顺带修复 57/357/433 行 `_logger` 未定义的隐患）；614 行 in-process 通用 `except Exception` 改为 `_logger.exception("in-process 抓取执行失败 type=%s", type(exc).__name__)`（留痕带异常类型）。净增 ≤2 行
- [X] T006 [P] [US1] 在 `webui/source_boss_cdp.py`：删除 35 行死代码 `logger = logging.getLogger(__name__)` 定义（已核实无外部引用）
- [X] T007 [P] [US1] 在 `webui/source.py`：删除 98 行死代码 `logger = logging.getLogger(__name__)` 定义（已核实无外部引用）
- [X] T008 [P] [US1] 在 `webui/updater.py`：41 行裸 logger 改为 `get_logger(__name__)`；492 行 `logger.exception` 调用保持，日志落盘
- [X] T009 [P] [US1] 在 `webui/runtime_audit.py`：`record_runtime_event` 在 `is_configured()` 为假时不再静默跳过——触发懒初始化兜底（`get_logger` 已自动配置）或至少打一条 stderr 降级标记，保证审计事件必留痕
- [X] T010 [P] [US1] 在 `webui/ai_client.py`：失败路径（`raise AISecurityError` 前）补一行 `_logger.warning/error`，记录异常类型 + error_code（级别 ≥WARNING，避免刷屏）
- [X] T011 [P] [US1] 在 `webui/error_registry.py`：`_LOGGER` 从裸 `logging.getLogger("career_scout.error_registry")` 改为 `get_logger("error_registry")`（统一封装，行为不变）
- [X] T012 [US1] 新增聚焦测试（如 `tests/test_logging_whitebox.py`）：in-process 通用异常路径下 `career-scout.log` 出现带异常类型与 `task=` 的记录；断言不依赖真实抓取（测试桩构造异常）
- [X] T026 [US1] 在 `webui/task_runners.py` 外层兜底 `except Exception` 补 `_logger.exception`（异常类型+堆栈进主日志；审查发现，FR-009）

**Checkpoint**: 主进程 4 处裸 logger + in-process 留痕 + 审计降级全部落地；`tests/test_logging_whitebox.py` 通过。

---

## Phase 4: User Story 2 - 抓取脚本现场可查 (Priority: P2)

**Goal**: 子进程日志有落点且关键现场（详情页结果、登录检查、风控判定）进 career-scout.log。

**Independent Test**: 跑一次真实小抓取，日志页/日志文件能查到子进程现场行（含任务编号），而非只有主进程汇总结论。

### Implementation for User Story 2

- [X] T013 [US2] 在 `webui/source_boss_cdp.py`：构造子进程 env 时注入 `CAREER_SCOUT_LOG_LEVEL=INFO`（T006 之后同文件串行）
- [X] T014 [P] [US2] 在 `scripts/boss/constants.py`：393 行裸 `log = logging.getLogger("boss_cdp")` 改为 `log = get_logger("boss_cdp")`（归统一日志树；import 面 `from scripts.boss.constants import log` 不变）
- [X] T015 [US2] 在 `scripts/boss_cdp_raw.py`：核实子进程日志随懒初始化自动就绪（T001 生效）；若 `__main__` 异常路径干扰则补最小配置，不改 Spec A 的异常映射逻辑
- [X] T016 [P] [US2] 在 `scripts/boss/detail_scrape.py`：每岗位详情 terminal 结果补一行 `_logger.info("detail job_id=%s status=%s safe_code=%s", ...)`（复用 `_emit_detail_safe_event` 已有字段，不复制事件文件内容）。净增 1-2 行
- [X] T017 [P] [US2] 在 `scripts/boss/search.py`：`classify_list_diagnosis` 返回 verdict 非 None（风控/限流判定）时补一行 `log.warning(...)`（含 verdict/failed_code/hint）。净增 1-2 行
- [X] T018 [P] [US2] 在 `scripts/boss/login.py`：登录成功补一行 `log.info(...)`（现状已有失败 `log.error`）。净增 1 行
- [X] T027 [US2] 在 `scripts/boss/search.py` 风控重试后（repeated=True）判定处补 `log.warning` 记录最终结论（审查发现，FR-003 扩展）
- [X] T028 [US2] 在 `scripts/zhilian/search.py` `_risk_signal` 判定出风险信号时补 `_logger.warning`（审查发现，FR-003 扩展）

**Checkpoint**: 子进程现场行出现在 career-scout.log；子进程 DEBUG 噪音不刷爆日志（INFO 级别生效）。

---

## Phase 5: User Story 3 - 卫生机制防回归 (Priority: P3)

**Goal**: 仓库自检强制拦截"裸日志"与"出错不留痕"，从源头杜绝再次断线。

**Independent Test**: 故意新增一处裸 `logging.getLogger` 或"except 后仅局部赋值"代码，卫生检查必须失败并指出位置；还原后必须全绿。

### Implementation for User Story 3

- [X] T019 [US3] 在 `tests/test_repo_hygiene.py` 新增检查：AST 扫描 `webui/`、`scripts/`（排除 `__pycache__`）禁止 `logging.getLogger(...)` 调用（含 `from logging import getLogger` 后调用）；豁免 `webui/logging_setup.py`、`webui/ai_raw_log.py`、`tests/`、`dist/`、`node_modules/`；复用现有注释 marker 白名单机制
- [X] T020 [US3] 在 `tests/test_repo_hygiene.py` 扩展"except 不留痕"检查：ExceptHandler body 语句数 ≤2 且不含 `Call`/`Raise`/`Return`/`Continue`/`Break`/属性赋值（`Assign`/`AnnAssign` 到属性或实例）→ 失败；沿用现有 pass-only 基线框架与白名单机制
- [X] T021 [US3] 校准：跑 `uv run python -m unittest tests.test_repo_hygiene` 确认两条新检查全绿（存量已由 T005-T018 清零）；如有误伤，按注释 marker 白名单收敛并记录

**Checkpoint**: 卫生检查全绿；故意违规用例临时验证失败后还原。

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 端到端验证与全量回归。

- [X] T022 按 `specs/033-log-whitebox/quickstart.md` 执行验证 1-5：真实小抓取（DAD 账号、平衡档）确认子进程现场进日志；失败路径留痕（测试桩或真实失败）；卫生检查正反向；前端 `/api/logs` 可查；日志文件被删/只读不崩溃。记录每项结果与所用数据
- [X] T023 后端全量：`uv run python -m unittest discover -s tests`
- [X] T024 前端构建：`cd webui && npm run build`（本功能无前端改动，验证不破坏构建）
- [X] T025 清理：确认无测试日志/中转文件落项目根目录；确认正式日志目录未被测试污染

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 2)**: 无前置——阻塞 US1/US2
- **User Story 1 (P1)**: 依赖 Phase 2
- **User Story 2 (P2)**: 依赖 Phase 2（懒初始化是子进程日志就绪的前提）
- **User Story 3 (P3)**: 依赖 US1 + US2 完成存量整改（否则新检查一启用即红）
- **Polish (Final)**: 依赖全部用户故事

### Within Each User Story

- 同文件任务严格串行：T006 → T013（`source_boss_cdp.py`）；T005 → T012（测试依赖实现）
- 其余任务多为不同文件，可并行

### Parallel Opportunities

- US1：T006/T007/T008/T009/T010/T011 六文件互不依赖，可并行；T005 独立；T012 依赖 T005 完成后
- US2：T014/T016/T017/T018 四文件互不依赖，可并行；T013 依赖 T006；T015 依赖 T001
- US3：T019/T020 同文件串行；T021 依赖两者

---

## Parallel Example: User Story 1

```bash
# 并行执行（不同文件）：
#   T005  source_boss_cdp_detail.py
#   T006  source_boss_cdp.py
#   T007  source.py
#   T008  updater.py
#   T009  runtime_audit.py
#   T010  ai_client.py
#   T011  error_registry.py
# 全部完成后跑 T012 聚焦测试
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 2（T001-T004）→ 日志基础设施就绪
2. Phase 3（T005-T012）→ 主进程全部失败路径可查（MVP 达成）
3. **STOP and VALIDATE**: 制造失败看日志
4. 继续 US2 → US3 → Polish

### Incremental Delivery

- 每个用户故事独立可测：US1 验失败留痕、US2 验子进程现场、US3 验卫生检查
- 每完成一个 checkpoint 即报告；不提交不推送，等待用户安排

## Notes

- 所有测试日志/重定向使用系统临时目录，禁止写项目根目录
- 改动保持小而准，禁止重写大文件；`source_boss_cdp_detail.py` 只做等价替换
- 不触碰 Spec A（抓取阻断）逻辑；不提交、不推送、不发布
- 若 `source_boss_cdp_detail.py` 行数将超 800，在该批次把 `_InProcessCapture` 整体迁出（等价搬运，另行说明）
