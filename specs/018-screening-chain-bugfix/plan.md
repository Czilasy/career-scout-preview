# Implementation Plan: 筛选链路三处 Bug 修复（018）

**Branch**: `main`（缺陷修复，随仓库既有分支提交） | **Date**: 2026-08-22 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/018-screening-chain-bugfix/spec.md`（需求冻内核：上一会话自包含提示词）

## Summary

三处原地修复 + 一次数据清理：① AI 响应 `results`/`dropped` 字段类型守卫（精筛 `webui/ai.py _match_one_batch` 与粗筛 `_process_batch` 同款写法），坏格式按无结果降级走既有重试/uncertain/熔断链路；② 续跑幸存者语义反转为"断点内默认保留、仅明确 dropped 才移除"，续跑判定回退段替换为同源链合并（同 scrape_task_id、同条件、同画像、同画像事实，created_at 从旧到新，新覆盖旧），外加 `resume_inconsistent` 护栏事件；③ `_run_ai_screen_task` 成功收尾段换序，finalize 校验先于写历史轮，杜绝幽灵 done 轮；④ live 库一次性删除幽灵轮 828f8807（先备份）。纯后端，前端零改动、不重建 dist、不提版本。

## Technical Context

**Language/Version**: Python 3.12（后端）；前端零改动

**Primary Dependencies**: Flask、SQLite（内置 store + mixins）；无新增依赖

**Storage**: SQLite（screening_runs / screening_results / pipeline_checkpoints / task_logs）；**无 DDL、无迁移**

**Testing**: unittest；聚焦模块 `tests.test_ai tests.test_webui_app tests.test_result_rounds tests.test_repo_hygiene` + 被改写的 `tests.test_screen_flow`

**Target Platform**: Windows/macOS 桌面应用（Flask 本地服务 + 前端 SPA，本次不触前端）

**Project Type**: desktop-app（web-service 架构）

**Performance Goals**: 无新增性能目标；同源链合并最多遍历同一抓取下的有限 run 数（个位数量级），每 run 一次既有 verdict 查询

**Constraints**: `webui/app.py`（约 9800 行）与 `webui/store.py` 只允许原地修改既有逻辑，不允许追加全新功能逻辑；不给 store 追加全新方法（只允许向后兼容扩展现有方法）；测试输出一律进系统临时目录

**Scale/Scope**: 后端 4 个源文件小改（ai.py、app.py、screen_flow.py、store_screen_resume_mixin.py）；测试 3 个文件增改 + 1 个文件改写；文档 specs/018-*；CHANGELOG 一条；一次性数据清理不入库

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 检查 | 结果 |
|---|---|---|
| I. 职责分层 | 全部为既有模块原地修复：ai.py 解析守卫、app.py 编排语义修正、screen_flow.py 续跑编排、store mixin 数据访问扩展；无新路由、无新业务入口 | ✅ 通过 |
| II. 单文件尺寸 | 不新增任何代码文件；app.py/store.py 净变化为小幅度增删；screen_flow.py/store_screen_resume_mixin.py 远低于 800 行 | ✅ 通过 |
| III. 引用方向 | `app.py → screen_flow.py → store_screen_resume_mixin → store.py`，与现状一致；无反向依赖 | ✅ 通过 |
| IV. 拆分纪律 | 无拆分、无搬迁；行为变化全部由失败测试先行定义（新增用例先行或同步修正既有断言） | ✅ 通过 |
| V. 验证门禁 | 本次为缺陷修复交付：聚焦测试 = 冻结提示词限定的四组 + 被改写的 test_screen_flow；全量测试/build 不在本次范围（提示词明确"只跑涉及的模块"、前端零改动） | ✅ 通过（按提示词限定聚焦范围） |

## File Boundaries

*GATE: 已在需求冻内核（自包含提示词）中逐文件批准，此处记录成文。*

- **Allowed files**（原地修改既有逻辑）:
  - `webui/ai.py` — `_match_one_batch` 的 results 类型守卫；`_process_batch`（粗筛）的 dropped 类型守卫
  - `webui/app.py` — `_run_ai_screen_task` 内：粗筛幸存者条件反转与判定来源替换、`resume_inconsistent` 护栏事件、`_FINE_VERDICTS` 引用清理、成功收尾段换序
  - `webui/screen_flow.py` — `load_resume_verdicts_with_fallback` 回退段替换为同源链合并（签名向后兼容，新增可选 `profile_facts`）
  - `webui/store_screen_resume_mixin.py` — `latest_screen_runs_for_source` 向后兼容扩展（`statuses=None` → 全部状态、created_at 升序）
  - `tests/test_ai.py`、`tests/test_webui_app.py`、`tests/test_screen_flow.py`、`tests/test_result_rounds.py`（如断言旧顺序则同步修正）
  - `CHANGELOG.md` — 按卫生格式补条目
  - `specs/018-screening-chain-bugfix/**` — 本流程文档
- **Forbidden files**: `webui/store.py`（本次不动）、`webui/src/**`（前端）、`webui/dist/**`（不重建）、`scripts/boss_cdp_raw.py`、任何表结构/迁移文件、任何新 store 方法、任何新代码文件
- **New files**: 仅 `specs/018-screening-chain-bugfix/` 下文档（spec/plan/tasks/research/data-model/quickstart/contracts/checklists）；无新代码文件
- **Reference direction**: `app.py → screen_flow.py → store_screen_resume_mixin.py →（store.py 不动）`；`ai.py` 被 app.py 函数内延迟 import（现状保持）
- **Line gate**: app.py 净增 ≤ 约 20 行（护栏事件与判定来源替换），store.py 0 改动；其余文件远低于 800 行上限
- **Rationale**: 三处均为既有链路的缺陷修复，按架构红线落回各自既有职责位置；新增代码文件反而违反"普通功能不得向超大文件外再造平行入口"的收敛方向

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能交付验证：`uv run python -m unittest tests.test_ai tests.test_webui_app tests.test_result_rounds tests.test_repo_hygiene tests.test_screen_flow`（聚焦；最后一个是因本次改写其用例）。
- 前端测试与 `npm run build` 不适用（前端零改动、不重建 dist，冻结核明确）。
- 提交前：`uv run python -m unittest tests.test_repo_hygiene` + `git diff --check` + `git status` 检查。
- 数据清理（live 库）在代码修复合入后执行：备份 → 两条 DELETE → `scripts/db_info.py` 复核。

## Project Structure

### Documentation (this feature)

```text
specs/018-screening-chain-bugfix/
├── plan.md              # 本文件
├── research.md          # Phase 0：事故机理实证与设计决策
├── data-model.md        # Phase 1：无 DDL；实体与事件载荷说明
├── quickstart.md        # Phase 1：验证步骤
├── contracts/           # Phase 1：resume_inconsistent 事件契约 + 判定合并行为契约
├── checklists/          # requirements.md（已生成）
└── tasks.md             # /speckit-tasks 输出
```

### Source Code (repository root)

```text
webui/
├── ai.py                      # 修复一：两处解析守卫（原地点）
├── app.py                     # 修复二主修+护栏、修复三换序（原地点）
├── screen_flow.py             # 修复二辅修：同源链合并（原地点）
└── store_screen_resume_mixin.py  # latest_screen_runs_for_source 扩展（原地点）

tests/
├── test_ai.py                 # 坏格式回归
├── test_webui_app.py          # 事故链回归 + 收尾换位回归
├── test_screen_flow.py        # 判定合并用例改写
└── test_result_rounds.py      # 如断言旧顺序则同步修正
```

**Structure Decision**: 复用现有 web-service 分层，无结构变更。

## Complexity Tracking

> 无宪法违规需要豁免；空。
