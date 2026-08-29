# Implementation Plan: 第 7 类筛选条件：招聘者活跃时间（028）

**Branch**: `028-recruiter-activity-filter` | **Date**: 2026-08-29 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/028-recruiter-activity-filter/spec.md`（对应 BACKLOG B081；需求 2026-08-29 grill-me 冻结）

## Summary

在现有六类 AI 筛选条件外新增第 7 类「招聘者上次活跃」（单选四档 7/30/90/180 天）：详情抓取时采集招聘者活跃数据（Boss 详情页名片活跃文本 / 智联详情 `staff.lastOnlineTime` 毫秒时间戳），归一化为统一事实字典进 `extra`；JD 详情抓取后、AI 精筛判定前由确定性规则判定「活跃距今超过档位 → 不匹配」，说明由模板生成；拿不到数据标「活跃时间未知」不拦截；仅新抓取生效无回填。技术要点：超标文件分流（`scripts/zhilian/detail_fields.py` 新包、`ai_screening.py` 最小接线）、判定域独立新模块 `webui/recruiter_activity.py`、schema 公共字段 + 版本递增、前端 single-select 交互、无新列无 migration。

## Technical Context

**Language/Version**: Python 3.11+（uv 管理）；前端 Vue 3 + TypeScript + Vite（webui/src）

**Primary Dependencies**: 后端标准库 unittest；前端 vue-tsc/vitest

**Storage**: SQLite（`jobs.extra_json` 承载活跃事实，无新列无 migration）

**Testing**: `uv run python -m unittest discover -s tests`；前端 `npm test`；构建 `npm run build`

**Target Platform**: Windows/macOS 桌面应用内嵌 WebUI

**Performance Goals**: 判定为纯字典/数值比较，无感知开销；详情采集不增加请求数（复用详情页既有内容）

**Constraints**: 超标文件禁追加逻辑（zhilian_cdp_raw.py 894 行、ai_screening.py 653 行过 600 预警线、detail_scrape.py 720 行过预警线）；未知数据绝不误拦；第 7 类不进 AI prompt

**Scale/Scope**: 两平台各一个新筛选字段 + 一个判定域模块 + 采集/合并/落库链路 + 前端单选交互；测试新增约 2 个文件、扩展 3 个既有文件

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 结论 |
| --- | --- |
| I. 职责分层 | ✅ 判定域独立 `webui/recruiter_activity.py`；采集归 scripts 抓取域；落库经 store 域方法；无路由层逻辑 |
| II. 单文件尺寸边界 | ✅ 新模块预计 ≤150 行；`ai_screening.py`（653）仅 ≤6 行接线、`detail_scrape.py`（720）仅 +1 无逻辑字典键、`zhilian_cdp_raw.py`（894）仅引用级拼接——全部在 Complexity Tracking 说明 |
| III. 引用方向 | ✅ `ai_filters → recruiter_activity`；`pipeline_exec_details → store_jobs`；scripts 抓取域不 import webui；前端 view → composables |
| IV. 拆分与重构纪律 | ✅ 本 Spec 无拆分内容；超标文件改动按「分流」模式仅为最小引用，不构成拆分批次 |
| V. 验证门禁 | ✅ 功能交付全门禁：聚焦测试 + 后端全量 + 前端测试 + `npm run build` + 卫生检查（spec Verification Scope 已写） |
| VI. 模块地图与落位 | ✅ 新文件 3 个（2 产品 + 1 包）将随本批登记进宪法模块地图；判定域属筛选域新模块；`listFilter.ts`、门面文件全部禁改 |

## File Boundaries

*GATE: 文件落位清单（用户 2026-08-29 指示「做到 tasks 结束」，视为预授权，清单在此记录）。*

- **Allowed files（修改）**:
  - `scripts/boss/detail_parse.py`（+~35 行：`extract_recruiter_activity_text`）
  - `scripts/boss/detail_scrape.py`（+1 行：`build_detail_record` 增 `recruiter_activity_text` 键）
  - `scripts/zhilian_cdp_raw.py`（≤8 行引用级：import 新模块 + 提取表达式拼接 + 合并调用，无新逻辑）
  - `webui/platforms_schema.py`（公共字段 + 两平台版本号）
  - `webui/platforms_boss.py`、`webui/platforms_zhilian.py`（schema 构建加字段）
  - `webui/ai_filters.py`（硬规则组合入口 + `_build_criteria_description` 排除新键 + 标签）
  - `webui/ai_screening.py`（≤6 行：match_jds 硬规则块接线，粗筛不动）
  - `webui/pipeline_exec_details.py`（+~25 行：归一化合并 + store 更新调用）
  - `webui/store_jobs.py`（+~30 行：`update_job_extra`）
  - `webui/src/types.ts`、`webui/src/composables/useDiscoveryState.ts`、`webui/src/composables/useDiscoverySearch.ts`、`webui/src/components/OneClickScreenDialog.vue`（multiple 透传 + 单选 toggle）
  - `webui/screen_flow.py`（仅 docstring 全量口径，零逻辑）
  - `webui/screen_flow.py`（仅 docstring「六类」→全量口径，随 T010）
  - `README.md`（收口阶段文档同步）、`.specify/memory/constitution.md`（模块地图登记，收口阶段）
  - 测试：`tests/ai/test_ai_match.py`、`tests/test_platforms.py`、`tests/test_screen_flow.py`、`tests/webui_store/` 既有 store 测试（扩展）；新增 `tests/ai/test_recruiter_activity.py`、`tests/source/test_recruiter_activity_capture.py`、`webui/src/__tests__/` 单选交互 spec
- **Forbidden files**: `webui/app.py`、`webui/store.py`、`webui/source.py`、`webui/ai.py`、`scripts/boss_cdp_raw.py`（门面）；`webui/src/listFilter.ts`（结果页本地过滤，无关）；`webui/store_migrations_*.py`（无新列）；`webui/results_api.py`、`webui/runners/*`（内存链路自然携带，无需改）；`webui/ai_prompts.py`（六类文案保持）；Boss/智联列表搜索抓取逻辑
- **New files**:
  - `webui/recruiter_activity.py`（~150 行）——招聘者活跃判定域：Boss 文本→区间映射表、智联时间戳归一化、档位判定与说明模板、人话距离格式化、未知 caveat 助手
  - `scripts/zhilian/__init__.py`、`scripts/zhilian/detail_fields.py`（~70 行）——智联详情 staff 字段提取 JS 常量与合并纯函数（超标文件分流）
  - `tests/ai/test_recruiter_activity.py`、`tests/source/test_recruiter_activity_capture.py`、`webui/src/__tests__/` 单选交互 spec——判定域/采集链路/前端交互测试
  - `specs/028-recruiter-activity-filter/*`（本批 Spec 产物）
- **Reference direction**: `scripts（采集，只出原始文本/时间戳）→ webui/recruiter_activity（归一化+判定）← ai_filters（硬规则入口）/ pipeline_exec_details（合并落库）→ store_jobs（持久化）`；前端 `view → composables → api/client`；禁止反向
- **Line gate**: 改动后 `ai_screening.py` ≤660、`detail_scrape.py` ≤725、`zhilian_cdp_raw.py` ≤905（引用级）、其余 <600；新文件 ≤200
- **Rationale**: 判定域独立成模块是宪法原则 VI 落位要求（ai_screening.py 过预警线禁增长）；zhilian 新包是超标文件分流的唯一合规路径；无新列/migration 因判定只需 JSON 事实字典且存量无回填

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能交付（本 Spec）：聚焦测试 + 后端全量测试 + 前端测试 + `npm run build` + 仓库卫生检查，全绿方可交付（宪法原则 V，不豁免）。
- 收口发布任务（版本提升/打包/提交/推送）：按根 AGENTS.md 收口规则执行卫生测试、hooks、`git diff --check`、`git status`、`scripts/release_check.ps1`（若存在）。
- 收口阶段任务：`scripts/bump_version.py` minor 提升 + CHANGELOG 条目 + README 同步 + 模块地图登记。

## Project Structure

### Documentation (this feature)

```text
specs/028-recruiter-activity-filter/
├── plan.md              # 本文件
├── research.md          # Phase 0：值域实测/落位/数据流决策
├── data-model.md        # Phase 1：活跃事实字典/档位/判定规则
├── quickstart.md        # Phase 1：验证指南
├── contracts/           # Phase 1：filter-schema.md、recruiter-activity-payload.md
└── tasks.md             # Phase 2（/speckit-tasks 产出）
```

### Source Code (repository root)

```text
scripts/
├── boss/
│   ├── detail_parse.py        # +extract_recruiter_activity_text（名片活跃行截获）
│   └── detail_scrape.py       # build_detail_record 增键
└── zhilian/                   # 新包（超标文件分流）
    ├── __init__.py
    └── detail_fields.py       # staff 提取 JS 常量 + 合并纯函数
scripts/zhilian_cdp_raw.py     # 引用级拼接（≤8 行）

webui/
├── recruiter_activity.py      # 新：判定域（映射/归一化/判定/说明模板）
├── platforms_schema.py        # 公共字段 + 版本 1→2 / 2→3
├── platforms_boss.py          # schema 构建加字段
├── platforms_zhilian.py       # schema 构建加字段
├── ai_filters.py              # 硬规则组合入口 + criteria 描述排除 + 标签
├── ai_screening.py            # match_jds 接线（≤6 行）
├── pipeline_exec_details.py   # 详情合并 extra + update_job_extra 调用
├── store_jobs.py              # update_job_extra
└── src/
    ├── types.ts                        # multiple 透传
    ├── composables/useDiscoveryState.ts # filterGroups 保留 multiple
    ├── composables/useDiscoverySearch.ts # toggleFilter 单选分支
    └── components/OneClickScreenDialog.vue # 自带 toggle 单选分支

tests/
├── ai/test_recruiter_activity.py          # 新：判定域全值域矩阵
├── ai/test_ai_match.py                    # 扩：精筛接线三态
├── source/test_recruiter_activity_capture.py # 新：采集/合并/落库
├── test_platforms.py                      # 扩：schema/校验/版本
└── test_screen_flow.py                    # 扩：续跑一致性
```

**Structure Decision**: 沿用 021/027 确立的域模块 + 门面禁改结构；判定域新模块、抓取采集归 scripts 域包、持久化归 store 域 mixin、前端改动收敛在 Discovery 域 composables。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `zhilian_cdp_raw.py`（894 行，超红线）引用级 +≤8 行 | staff 数据只能在抓取子进程浏览器 JS 内提取，表达式拼接点唯一（:521-531） | 不动该文件则功能无法实现；逻辑全部放新包 `scripts/zhilian/detail_fields.py`，符合原则 VI 分流模式；单独拆分 Spec 过重（引用级改动非逻辑追加） |
| `ai_screening.py`（653 行，过预警线）+≤6 行 | match_jds 硬规则块是唯一接线点 | 判定逻辑全部在 `webui/recruiter_activity.py`，接线仅为一次函数调用；复制流程到新模块反而引入行为漂移风险 |
| `detail_scrape.py`（720 行，过预警线）+1 行 | 详情产物键集在 `build_detail_record` 定义，截获逻辑在 detail_parse | 键名常量化到新模块会让 2 行变更膨胀为跨文件耦合 |
