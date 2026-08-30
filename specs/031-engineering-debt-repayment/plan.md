# Implementation Plan: 工程还债——全仓质量整修

**Branch**: `031-engineering-debt-repayment` | **Date**: 2026-08-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/031-engineering-debt-repayment/spec.md`

## Summary

行为保持的全仓质量整修：九个批次清偿深度审查发现的所有已确认问题——文档与暴露面失真、主文件反向依赖、静默吞异常、爬虫门面回溯、宪法红线超限、事故代码常驻生产 API 面、前端类型豁口、测试打桩后门、发布验证缺口。除"任务不存在"提示语统一为信息更全文案这一处许可变化外，接口与界面行为完全不变；每批独立提交、独立验证、可单独回退。

## Technical Context

**Language/Version**: Python >=3.10（CI 用 3.11；Windows 优先桌面应用）；前端 Vue 3.5 + TypeScript strict + Vite 8 + Vitest 4

**Primary Dependencies**: Flask（本地 Web 工作台）、sqlite3 标准库（无 ORM）、PyInstaller（EXE 打包）；前端运行时仅 vue 与 @lucide/vue

**Storage**: SQLite（WAL，手写版本化迁移 001-032，迁移前 bootstrap 备份校验）

**Testing**: 后端 stdlib unittest（`uv run python -m unittest discover -s tests`，94 文件/约 2733 用例）；前端 vitest（42 文件/533 用例）；构建门禁 `npm run build`（vue-tsc --noEmit）

**Target Platform**: Windows EXE（onefile）+ macOS DMG；开发期源码模式本地 127.0.0.1

**Project Type**: desktop-app（本地 Web UI + CDP 爬虫 CLI）

**Performance Goals**: 不适用——行为保持重构；约束为 CI 时长不显著回退

**Constraints**: 样式与渲染像素级不变；每批聚焦测试+后端全量+前端测试+构建+卫生检查全过；宪法红线修后全仓合规；版本号不动

**Scale/Scope**: 9 个批次；触及约 40 个源文件与 10 个测试文件；基线：后端 10.5 万行 / 前端 3.2 万行 / 3266 个既有用例

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 判定 | 说明 |
|---|---|---|
| I. 职责分层 | ✅ | 本单即执行：常量出仓独立模块（webui/constants.py 扩充）、app.py 瘦身为纯装配、蓝图收编 |
| II. 单文件尺寸边界 | ✅ | 批次 6/8 消除全部 4 个超限文件；修后红线全仓合规 |
| III. 引用方向 | ✅ | 批次 3 清零 23 处反向 import；批次 5 清零 132 处门面回溯；前端依赖类型化单向 |
| IV. 拆分与重构纪律 | ✅ | 本单即专门立项的拆分重构 Spec（含 File Boundaries、引用方向、行数门禁） |
| V. 验证门禁 | ✅ | 每批聚焦+全量+前端+构建+卫生全过；收口按根 AGENTS.md 执行 |
| VI. 模块地图与落位规则 | ✅（豁免条款适用） | 专门拆分 Spec 在批次内修改门面结构（app.py / boss_cdp_raw.py / zhilian_cdp_raw.py / task_runners.py），符合原则 VI 拆分豁免；豁免不延伸批次外；地图随批次登记（FR-022） |
| VII. 错误处理与可观测性 | ✅ | 批次 4 落地基线（pass-only 只降不升、白名单附注释、统一日志） |

无违规项，无需 Complexity Tracking。

## File Boundaries

*落位清单已于 grill-me 质询阶段逐项冻结确认（用户明确指令：实施过程中不再逐项询问）；此处为冻结落位的正式登记。*

- **Allowed files（修改）**:
  - 文档/配置：`README.md`、`AGENTS.md`、`.gitignore`、`pyproject.toml`（仅 dev 依赖段与残留 pytest 段）、`uv.lock`（联动）、删除 `sonar-project.properties`
  - CI/发布：`.github/workflows/ci.yml`、`.github/workflows/release-macos.yml`、`scripts/release_check.ps1`、`scripts/publish_mirror.ps1`
  - 主文件松绑：`webui/app.py`、`webui/constants.py`、`webui/app_support.py`、`webui/core_api.py`、`webui/ai_screen_api.py`、`webui/exec_search_api.py`、`webui/task_continue_api.py`、`webui/pipeline_jobs_api.py`、`webui/profiles_api.py`、`webui/task_state_api.py`、`webui/results_api.py`、`webui/browser_support.py`、`webui/settings_api.py`、`webui/tuning_api.py`、`webui/resume_fields_api.py`、`webui/running_task_api.py`、`webui/task_status.py`、`webui/task_pause_support.py`、`webui/location_api.py`、`webui/job_feedback_api.py`
  - 错误留证据：79 处 pass-only 所在的全部 28 个文件（**完整清单见 data-model.md E5**，含 browser_recovery.py、process_executor.py、source_fake.py 等）、`webui/logging_setup.py`（如需补子 logger 约定）
  - 爬虫：`scripts/boss/` 全部 20 个子模块（132 处替换）、`scripts/boss_cdp_raw.py`
  - 超限拆分：`scripts/zhilian_cdp_raw.py`、`webui/task_runners.py`、`webui/source_zhilian_cdp.py`（import 翻转）、`webui/source_zhilian_defaults.py`（import 翻转）、`scripts/zhilian/__init__.py`（包导出更新）
  - 事故退场：`webui/task_state_api.py`（删 3 路由）、删除 `webui/historical_recovery.py`
  - 前端：`webui/src/views/DiscoveryView.vue`、`webui/src/composables/useDiscoveryState.ts`、`useDiscoveryWorkflow.ts`、`useDiscoverySearch.ts`、`useDiscoveryExecution.ts`、`useDiscoveryTasks.ts`、`useDiscoveryResults.ts`
  - 测试：`tests/test_repo_hygiene.py`（基线+dist 扫描收紧+宪法公开后的跟踪规则）、`tests/test_updater.py`、recovery 相关测试改造、打桩改写涉及的全部测试文件、受提示语影响断言的测试
- **Forbidden files**:
  - `webui/src/styles.css`（明示不动——宪法无 CSS 红线，动了只有风险）
  - `webui/dist/**`（只由 `npm run build` 产出，禁止手改）
  - `webui/store.py`、`webui/store_*.py` 全族（本轮无涉，防止范围蔓延；**唯一例外**：B4 中 store.py 的 2 处白名单吞异常点允许注释级改动，不改任何行为）
  - `CHANGELOG.md`、各版本号文件（`pyproject.toml` 的 version 段、`webui/package.json` version 等）——版本号本轮不动；`pyproject.toml` 仅允许 dev 依赖段与残留 pytest 段
  - `specs/031-*` 之外全部 `specs/` 目录、`roadmap/`、`design/`（本地目录）
  - `packaging/build_exe.ps1`、`webui/updater.py`（实现不改；若测试暴露缺陷按"新问题当场修进本单"处理并在任务中登记）
  - 历史提交（不改写提交身份）
- **New files**:

| 新文件 | 职责 | 预计行数 |
|---|---|---|
| `webui/task_runner_support.py` | 任务运行支撑域：stdout 缓冲、卡死/风控分类、产物校验、载荷与脱敏助手 | ~260 |
| `webui/workbench_runner.py` | WorkbenchRunner 实现（搜索 run 生命周期、查询命令、结果持久化） | ~330 |
| `scripts/zhilian/cdp.py` | 智联 CDP 原语：连接/求值/等待/导航/标签管理 | ~150 |
| `scripts/zhilian/search.py` | 智联列表域：登录探测/preflight/fetch_list/风险信号/岗位归一 | ~340 |
| `scripts/zhilian/detail.py` | 智联详情域：批量详情、后台标签工作器、会话重置 | ~440 |
| `scripts/zhilian/urls.py` | 智联 host 判定与 input_hash | ~40 |
| `scripts/boss/runtime.py` | boss 会话态持有：网络会话工厂、活动标志、共享超时/重试参数 | ~120 |
| `scripts/maintenance/__init__.py` | 运维工具包标记 | ~5 |
| `scripts/maintenance/historical_recovery.py` | 2026-07-28 事故修复手动工具（预演/备份/执行三子命令 CLI） | ~1000（迁入为主） |
| `webui/src/composables/discoveryDeps.ts` | Discovery 五域依赖类型契约与聚合类型 | ~180 |
| `webui/src/components/`（1 个瘦身组件，实施时按实际抽取对象命名并登记地图） | DiscoveryView 瘦身抽出的高内聚区块 | ~200 |
| `specs/031-engineering-debt-repayment/contracts/*.md` 等 | 本单文档 | — |

- **Reference direction**: `api → service → store` 单向向下；共享常量仅被 import、不反向依赖任何层；`scripts/boss/* → boss/runtime` 单向、子模块间互不回溯门面；`scripts/zhilian/* → cdp.py` 单向；前端 `view → composables → api/client`；测试 → 生产代码；**任何模块禁止 import `webui.app`**（除 `webui/app.py` 自身与其 re-export 兼容块）。
- **Line gate**: 修后全仓 Python 业务文件 ≤800 行、Vue 单文件 ≤1200 行；兼容壳 `zhilian_cdp_raw.py` ≤150、`task_runners.py`（TaskRunner 核心 + 兼容 re-export）≤400、`boss_cdp_raw.py` ≤130（现状）；`app.py` 修后显著低于 777；`DiscoveryView.vue` ≤1200；所有新文件不超宪法红线。
- **Rationale**: 共享常量家复用既有 `webui/constants.py`（已存在且名实相符，避免新门面）；boss 会话态入包内 runtime 模块与既有 021 B8 的依赖注入方向一致；zhilian 拆分镜像 boss 包域结构（同一作者同一套爬虫家族，结构对称降低认知成本）；恢复工具落 `scripts/maintenance/` 与生产 API 面物理隔离；前端依赖契约独立成 `discoveryDeps.ts` 供五个 composable 与视图共同 import。

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能/重构/拆分交付（本单全部批次）：最终门禁 = 相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。无客观证据不得宣称完成。
- 批次 2（CI/发布流水线变更）附加验证：推送后在 GitHub Actions 观察 Windows 作业与发布门禁行为；标签校验用本地两种输入（有/无标签）验证。
- 收口发布任务（版本提升、打包、提交、推送、Release）：不要求全量测试；按根 `AGENTS.md` 收口规则执行。
- 提交/推送由用户指令触发；每批次至少一次提交，提交信息 Conventional Commits。

## Project Structure

### Documentation (this feature)

```text
specs/031-engineering-debt-repayment/
├── spec.md              # 需求（9 故事/25 FR/9 SC）
├── plan.md              # 本文件
├── research.md          # Phase 0：技术决策
├── data-model.md        # Phase 1：结构实体与登记表
├── quickstart.md        # Phase 1：逐批验证指南
├── contracts/           # Phase 1：兼容契约
│   ├── module-compatibility.md
│   ├── http-api-delta.md
│   ├── recovery-cli.md
│   ├── discovery-deps.md
│   └── release-pipeline.md
├── checklists/requirements.md
└── tasks.md             # Phase 2（/speckit-tasks 产出）
```

### Source Code (repository root)

```text
webui/
├── app.py                  # 瘦身后：入口 + 路由注册 + re-export 兼容块
├── constants.py            # 扩充：共享常量与纯函数之家
├── task_runners.py         # 兼容壳：TaskRunner 核心 + re-export
├── task_runner_support.py  # 新：任务运行支撑域
├── workbench_runner.py     # 新：WorkbenchRunner 实现
├── *_api.py                # 路由域（统一 register_*_routes）
├── historical_recovery.py  # 删除（迁 scripts/maintenance/）
└── src/
    ├── views/DiscoveryView.vue   # ≤1200 行
    ├── composables/              # +discoveryDeps.ts
    └── components/               # +1 个瘦身抽出组件

scripts/
├── boss_cdp_raw.py         # 兼容壳（不变更职责）
├── boss/                   # +runtime.py；20 子模块去门面回溯
├── zhilian_cdp_raw.py      # 兼容壳
├── zhilian/                # +cdp.py / search.py / detail.py / urls.py
└── maintenance/            # 新：historical_recovery.py 手动工具
```

**Structure Decision**: 复用既有分层与门面模式（021 系列已建立），不引入新架构概念；所有新文件均为既有域的内聚拆分或迁移，无新领域。

## Complexity Tracking

无宪法违规，无需登记。
