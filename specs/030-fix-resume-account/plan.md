# Implementation Plan: 续跑账号身份修复

**Branch**: `030-fix-resume-account` | **Date**: 2026-08-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/030-fix-resume-account/spec.md`

## Summary

修复统一继续接口自动换号覆盖 R2 角色冻结账号的缺陷：任务创建时快照当时全局账号，自动换号改为双门槛（用户暂停期间主动换过全局账号 且 暂停码非 AI 类），存量任务无快照不自动换，换号写任务事件与续跑日志行；顺带统一存量任务缺冻结账号时的角色解析口径（BOSS 走 R2 角色解析，智联不变）、为单岗位 JD 抓取加任务运行期 409 门禁、JD 阶段启动浏览器前重绑任务冻结身份。核心逻辑落入既有续跑身份域文件 `webui/resume_identity.py`，超预警线路由文件只保留一行式调用并净不增长。

## Technical Context

**Language/Version**: Python 3.12（uv 管理，后端 Flask）；前端 Vue 3 + TS（本批次零前端改动）

**Primary Dependencies**: Flask（路由层）、既有 `webui.pipeline_exec_accounts`（账号簿/角色）、`webui.resume_identity`（续跑身份域）、`scripts.login_state_cache`（登录态缓存）

**Storage**: SQLite（`screening_runs.execution_params_json`；新增快照键为 execution_params 内字段，无表结构变更）

**Testing**: unittest（后端 `tests/`，webui 集成测试在 `tests/webui_app/`）；前端 vitest（本批次不触及）

**Target Platform**: Windows/macOS 桌面应用内嵌 WebUI

**Performance Goals**: 不适用（行为修复，无性能目标）

**Constraints**: 超预警线文件（task_continue_api 765 / exec_search_api 644 / pipeline_jobs_api 659）净不增长；不改动既有 HTTP 契约的请求格式；显式 target_account 行为完全不变

**Scale/Scope**: 后端 6 个文件 + 测试 2 个文件；无前端源码改动

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I 职责分层**：✅ 通过——账号身份决策逻辑归续跑身份域（resume_identity），路由层只保留调用与响应组装；不落门面。
- **II 单文件尺寸边界**：✅ 通过——resume_identity 63→约 200 行（<800）；三个超预警线文件按"搬运换新增"净不增长（task_continue_api 净减约 40 行）。
- **III 引用方向**：✅ 通过——`*_api.py → resume_identity → pipeline_exec_accounts`，无反向依赖；resume_identity 不 import api/app。
- **IV 拆分与重构纪律**：✅ 不涉及独立重构 Spec；搬运以行为不变为前提，由既有测试先行护航。
- **V 验证门禁**：✅ 适用本批次——聚焦测试 + 后端全量 + 前端测试 + `npm run build` + 卫生检查（见 Verification Gate）。
- **VI 模块地图与落位**：✅ 通过——落入既有续跑身份域模块；`webui/resume_identity.py` 本批次登记进宪法模块地图（地图小节一行，非原则修订，无需版本升级）。

## File Boundaries

*GATE: 2026-08-30 经用户确认。*

- **Allowed files**: `webui/resume_identity.py`、`webui/task_continue_api.py`、`webui/pipeline_jobs_api.py`、`webui/exec_search_api.py`、`webui/ai_screen_api.py`、`webui/runners/ai_screen_jd.py`、`webui/runners/ai_screen_task.py`（030 实施补登：快照须随 runner 落库写入，见 Complexity Tracking）、`tests/webui_app/test_webui_app_taskrun.py`、`tests/webui_app/test_resume_account_gate.py`（新）、`tests/test_pipeline_pause_guard.py`（030 实施补登：T015 桩适配）、`.specify/memory/constitution.md`（仅模块地图小节）
- **Forbidden files**: `webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`（门面禁改）；`webui/src/**`（零前端改动）；`webui/dist/**`（构建产物）；`scripts/boss/**`（无关域）；`webui/store_*.py`（无表结构变更）
- **New files**: `tests/webui_app/test_resume_account_gate.py`——双门槛判定/快照/兜底口径聚焦测试（预计 ~250 行）；源码零新文件
- **Reference direction**: `*_api.py → resume_identity → pipeline_exec_accounts`；`runners/ai_screen_jd → ctx.activate_task_browser`（既有注入模式）；禁止反向 import
- **Line gate**: task_continue_api ≤760（实际 752，净减）；pipeline_jobs_api ≤666；exec_search_api ≤659；resume_identity ≤400；ai_screen_task ≤646；ai_screen_api/ai_screen_jd 低于 600 预警线
- **Rationale**: 自动换号、快照、兜底口径同属"续跑身份"既有域，resume_identity.py 已是该域载体；落入既有域符合宪法 VI，避免新文件；超线路由文件只留一行式调用并以搬运补偿增长

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能交付批次：最终门禁为相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查（宪法 V）。
- 收口发布任务（本批次后续的 patch 版本提升/提交/推送）：按根 `AGENTS.md` 收口规则执行，不自动要求全量测试。

## Project Structure

### Documentation (this feature)

```text
specs/030-fix-resume-account/
├── plan.md              # 本文件
├── research.md          # Phase 0 产出
├── data-model.md        # Phase 1 产出
├── quickstart.md        # Phase 1 产出
├── contracts/
│   └── http-api-delta.md # Phase 1 产出（契约增量）
└── tasks.md             # Phase 2 产出（/speckit-tasks）
```

### Source Code (repository root)

```text
webui/
├── resume_identity.py        # 续跑身份域：冻结身份解析/持久化 + 本批次新增
│                             #   快照键与创建点助手、双门槛自动换号判定、
│                             #   BOSS 缺冻结账号角色解析兜底、父身份继承
├── task_continue_api.py      # 统一继续接口：改为一行式调用，净减
├── pipeline_jobs_api.py      # job-detail 409 门禁；continue_recrawl 兜底统一
├── exec_search_api.py        # 创建点快照；续跑兜底助手化
├── ai_screen_api.py          # 创建点快照
└── runners/
    └── ai_screen_jd.py       # JD 阶段前重绑任务浏览器身份

tests/webui_app/
├── test_webui_app_taskrun.py # B057 场景回归 + 集成用例扩展
└── test_resume_account_gate.py # 新增聚焦测试
```

**Structure Decision**: 沿用 021 拆分后的既有分层（`*_api.py` 路由域 + runner 包 + 域模块），不引入新目录；唯一新增为测试文件。

## Complexity Tracking

> 超预警线文件（≥600 行）的新增逻辑均已分流至续跑身份域 `resume_identity.py`；
> 残留增长为不可再减的路由接线（门禁/快照键/审计调用/形参透传），逐文件豁免如下。

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| task_continue_api 765→752（净减，合规） | 换号应用与校验整段收口到域模块 | 保留路由内实现会继续逼近 800 红线 |
| exec_search_api 644→658（+14） | 快照键+导入+续跑填充改域调用+换号日志行透传 | 日志行属用户可见契约（US2），不可裁；逻辑已在域内 |
| pipeline_jobs_api 659→665（+6） | job-detail 并发门禁+两个创建点快照键+导入 | 门禁与快照是本批次行为本体，路由层最小形态 |
| ai_screen_task 638→644（+6） | runner 落库行是续跑读取快照的最终行（API 预建行被 INSERT OR REPLACE 覆盖），快照必须随行写入 | 为 6 行开新模块属碎片化，且"源码零新文件"是冻结边界 |
