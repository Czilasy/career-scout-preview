# Implementation Plan: 跨平台岗位去重（BOSS+智联）（019）

**Branch**: `019-cross-platform-job-dedup` | **Date**: 2026-08-23 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/019-cross-platform-job-dedup/spec.md`

## Summary

同一岗位在 BOSS 与智联各跑一条筛选流程的架构下，后跑平台的 AI 筛选输入组装点（`_run_ai_screen_task` 的 `raw_jobs` 组装处）对「对端平台近 30 天内全部可见轮的非剔除岗位」（逐轮画像过滤）做确定性指纹比对（归一化公司+标题+城市，仅跨平台生效），命中者以「跨平台重复」剔除：不进粗筛/精筛/JD 抓取，verdict 落库 dropped、extra 携带对端条目追溯信息（最近包含轮）、并入最终剔除列表；断点续跑/重启在同点确定性重放。可见性设计（2026-08-23 用户确认）：前端合并视图把重复簇**成组展示**（列表一行 + 徽标，详情并排两平台副本含薪资）；筛选进度报数、完成口径可对账（抓取/跨平台重复/实际筛选）、任务事件台账、「跨平台去重」开关（默认开，随执行参数冻结，续跑沿用）。不回改已完成轮次。无数据库结构变更、无新端点（开关为既有提交端点的请求字段）。

## Technical Context

**Language/Version**: Python 3.11（后端）、Vue 3 + TypeScript（前端）

**Primary Dependencies**: Flask（现有 app.py 路由）、现有 `store` 只读查询、现有前端 `apiRequest` 管线

**Storage**: SQLite（`screening_runs` / `screening_results`，零结构变更；剔除记录复用 dropped 行 + `extra_json`）

**Testing**: `uv run python -m unittest`（新增聚焦测试文件 + 现有全量回归）；前端 `npm run test`（如覆盖）、`npm run build`

**Target Platform**: Windows/macOS 本地桌面（pywebview 壳）与浏览器访问

**Project Type**: 本地 Web 应用（Flask + Vue）

**Performance Goals**: 数千岗位下指纹构建+分桶比对为纯内存 O(n)，单次耗时 <100ms 量级，无可感知影响

**Constraints**: 不改 DB schema；不回改已完成轮次；`webui/app.py` 不得积累业务实现（宪法）

**Scale/Scope**: 单用户本地库，单轮岗位量级 ~3000

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 检查 | 结论 |
|---|---|---|
| I. 职责分层 | 判定与编排逻辑全部落新 service 模块（`job_fingerprint` 纯函数、`cross_platform_dedupe` 编排）；`app.py` 仅在既有筛选任务内注入调用点与列表合并（接线，非业务实现） | 通过 |
| II. 单文件尺寸 | 新文件 ≤300 行；`app.py` 净增 ≤40 行接线（该文件超限问题由既有拆分 Spec 处理，本功能不向其追加业务逻辑） | 通过（附判断依据） |
| III. 引用方向 | `app.py → cross_platform_dedupe → job_fingerprint`；`cross_platform_dedupe` 经参数注入 store 只读查询，不 import app/store 实现 | 通过 |
| IV. 拆分与重构纪律 | 本功能不拆文件、不改接口、不改数据库；行为变化由失败测试先行定义 | 通过 |
| V. 验证门禁 | 聚焦测试 + 后端全量 + 前端测试 + `npm run build` + 仓库卫生 | 通过（见 Verification Gate） |

Phase 1 复查：设计未引入反向依赖、未触碰 store/schema，结论不变。

## File Boundaries

*GATE: 用户未应答落点确认（本会话质询无应答），按宪法默认规则代确认：逻辑进新模块、超大文件只留接线。*

- **Allowed files**（允许修改）:
  - `webui/app.py` — 仅 `_run_ai_screen_task` 与其提交入口内接线：raw_jobs 组装后调用去重服务并落 dup 判定 + 进度报数 + 台账事件；`_rough_todo` 构造处排除重复岗；Stage A 后 `dropped_by_id` 合并重复岗条目；完成文案数字拆分；`/api/ai-screen` 读取开关字段并随执行参数冻结。净增 ≤60 行，不新增函数定义。
  - `webui/src/views/DiscoveryView.vue` — 仅 `fetchMergedLatestResult` 内：由两平台 dropped 记录的 extra 追溯信息构建重复簇数据（运行时私有字段，复用 `_result_run_id` 惯例），供成组展示。
  - `webui/src/components/JobWorkspace.vue` — 列表行「双平台在招」徽标 + 详情面板成组区（两平台副本并排：平台/薪资/链接）。
  - `webui/src/components/OneClickScreenDialog.vue` — 「跨平台去重」开关（默认开，本地记忆）。
  - `webui/src/types.ts` — 仅对齐 JobItem 运行时私有簇标记声明（若现有惯例要求）。
- **Forbidden files**（禁止修改）: `webui/store.py`、`webui/store_migrations.py`（零 schema/查询变更，去重源复用现有只读方法与任务事件方法）、`webui/pipeline_exec.py`（run 内合并不动）、`webui/ai.py`、`webui/result_rounds.py`、`webui/screen_flow.py`、`scripts/**`、`specs/001-018`、`hooks/**`、`packaging/**`
- **New files**（新增）:
  - `webui/job_fingerprint.py`（~150 行）— 纯归一化与指纹函数：`normalize_title` / `normalize_city` / `normalize_company` / `fingerprint` / `build_fingerprint_index`，无外部依赖。
  - `webui/cross_platform_dedupe.py`（~220 行）— 去重编排服务：`collect_other_platform_jobs(store, current_platform, profile_summary)`（对端判定源收集：`list_history_rounds` + 逐轮读取，按可见状态/30 天窗/画像摘要过滤，取非剔除岗位）、`split_cross_platform_duplicates(raw_jobs, other_jobs)`（返回保留集与剔除条目，剔除条目含 reason/extra 追溯，追溯目标取最近包含轮）、`apply_to_screening_input(...)`（组合入口，供 app.py 一行调用）。
  - `tests/test_job_fingerprint.py`（~200 行）— 归一化规则表驱动单测（全半角/空白/大小写、公司后缀与城市前缀剥离、市级提取、空值无指纹、误合反例）。
  - `tests/test_cross_platform_dedupe.py`（~380 行）— 拆分逻辑单测 + store 集成（对端多轮收集、30 天窗、画像过滤、历史轮岗位剔除与追溯、剔除条目结构与 extra、app 接线冒烟：粗筛输入不含重复、判定落库、续跑重放、计数自洽）。
- **Reference direction**: `app.py（接线）→ cross_platform_dedupe（service）→ job_fingerprint（纯函数）；cross_platform_dedupe → store 公开只读方法（参数注入）；前端 view → 既有 api 数据 → 组件渲染`
- **Line gate**: 新 Python 文件 ≤300 行（宪法上限 800）；`app.py` 净增 ≤60 行；Vue 组件增量合计 ≤150 行（成组展示为主要增量）。
- **Rationale**: 判定逻辑是与抓取/筛选/存储无关的独立职责，新模块保证可测与可复用；`app.py`/`store.py` 均为宪法点名的超大文件，本功能以「新逻辑新模块 + 既有流程最小接线」满足不扩大要求。可见性（报数/台账/开关）与成组展示为用户明确要求的信任基础，随本功能交付。

## Verification Gate

- 功能/重构/拆分交付：最终门禁为相关模块聚焦测试（新增两测试文件 + `tests/test_healthy_pipeline.py` 相关用例）、后端全量测试（`uv run python -m unittest discover tests`）、前端测试（`npm run test --prefix webui` 若配置）、`npm run build`、仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene`）。
- 收口发布任务（版本提升、打包、提交、推送、Release）：不要求全量测试；按根 `AGENTS.md` 收口规则执行卫生测试、hooks、`git diff --check`、`git status` 和 `scripts/release_check.ps1`（若存在）。
- 只有 Spec 明确写入或用户明确要求时，收口任务才执行全量测试。

## Project Structure

### Documentation (this feature)

```text
specs/019-cross-platform-job-dedup/
├── plan.md              # 本文件
├── research.md          # 架构事实调查与决策依据
├── data-model.md        # 数据实体与剔除记录结构
├── quickstart.md        # 端到端验证指南
├── contracts/
│   └── cross-platform-dedupe.md  # 剔除记录 extra 数据契约（前后端共识）
└── tasks.md             # /speckit-tasks 生成
```

### Source Code (repository root)

```text
webui/
├── app.py                        # 接线修改（≤60 行净增：去重调用/报数/台账/开关/剔除合并）
├── job_fingerprint.py            # 新增：归一化与指纹纯函数
├── cross_platform_dedupe.py      # 新增：跨平台去重编排服务
└── src/
    ├── types.ts                  # JobItem 簇标记对齐（如需）
    ├── views/DiscoveryView.vue   # 合并视图重复簇数据构建
    └── components/
        ├── JobWorkspace.vue      # 徽标 + 详情成组区（两平台副本并排）
        └── OneClickScreenDialog.vue  # 「跨平台去重」开关

tests/
├── test_job_fingerprint.py       # 新增
└── test_cross_platform_dedupe.py # 新增
```

**Structure Decision**: 单仓库 Web 应用（Flask 后端 + Vue 前端）；后端按「app 路由 → service 模块 → store」分层，本功能新增两个 service 层模块；前端沿用 view → api → component 既有结构，改动限定于结果合并与展示两处。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `app.py`（超大文件）净增 ≤60 行接线 | 去重必须织入既有筛选任务的输入组装/粗筛输入/剔除合并/完成文案四个既有局部，外加报数与台账事件调用；无法从外部模块替换该流程 | 把接线逻辑放进新模块需要把 `_run_ai_screen_task` 整体搬出 app.py——属于宪法要求单独 Spec 的拆分工作，不得与功能混做；本方案已把全部判定/编排逻辑放新模块，app.py 仅保留不可回避的调用点 |
