# Implementation Plan: 测试体系低噪音瘦身

**Branch**: `040-test-suite-slimming` | **Date**: 2026-09-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/040-test-suite-slimming/spec.md`（已冻结）

**Note**: 本计划严格对齐冻结规格与 2026-09-11 六轮边界质询结论；文件放置清单已于同日交用户过目。五批推进、批次之间为停滞点。

## Summary

对全项目测试代码与测试基建做"减重不减防护"治理，分五批：①假保护必修（断言永不执行/恒真/名实不符/过期门禁）；②零风险删除（零引用死件、逐字重复用例、未用导入与死参数）；③重复选边（约 30 组多文件重复事实收敛到单一正本，以故障注入指纹验证保护不变）；④缠结小修（全局状态还原、真实资源隔离、临时目录隔离、定时器清理、构建指纹排除测试文件、提交前脚本去本机硬编码路径）；⑤死代码回收（按 Spec 内登记册，经用户逐项同意后删除 `semantic` 模块与前端孤儿导出及其测试）。

全程不改变产品行为与失败判定口径；第 5 批是唯一一次产品代码改动，且需逐项授权。任何批次发现产品缺陷只记录上报、不顺手修改。

## Technical Context

**Language/Version**: Python 3.11（uv 管理）；TypeScript + Vue 3（Vite / Vitest）

**Primary Dependencies**: 不新增任何依赖；后端 `unittest`、前端 `vitest`（均为既有）

**Storage**: 不涉及数据库结构与数据；测试临时文件使用系统临时目录

**Testing**: 后端 `uv run python -m unittest discover -s tests`；前端 `cd webui && npm test`；构建 `cd webui && npm run build`；卫生 `uv run python -m unittest tests.test_repo_hygiene`

**Target Platform**: 开发机（Windows + PowerShell）与 CI（ubuntu）；本 Spec 主要面向开发机验证

**Project Type**: 单仓桌面应用（Python 后端 + Vue 前端）；本 Spec 为测试体系治理（非功能批次）

**Performance Goals**: 后端全量测试时长不超过基线（约 19 分钟）；前端不超过基线（约 26 秒）

**Constraints**: 不新增测试框架/依赖；测试文件不受产品行数红线约束；巨型测试文件拆分明确不做（另行立项）

**Scale/Scope**: 后端 127 文件 / 65,697 行 / 3163 例；前端 52 文件 / 21,402 行 / 872 例；五批合计约 130 条治理条目

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 检查 | 结论 |
|---|---|---|
| I 职责分层 | 不新增产品业务逻辑；测试侧改动不涉及产品分层 | PASS |
| II 尺寸边界 | 不新增产品文件；第 5 批为纯删除（只减不增）；测试文件拆分另行立项 | PASS |
| III 引用方向 | 测试 → 产品单向引用；不改产品侧任何引用 | PASS |
| IV 拆分纪律 | 本 Spec 属"测试体系治理"批次；产品代码唯一改动为第 5 批死代码删除（用户逐项授权，只删码不改行为），非重构 | PASS（附说明，见下） |
| V 验证门禁 | 每批：受影响子集 + 卫生；批末：全量 + 前端 + `npm run build` | PASS |
| VI 模块地图 | 不新增模块，无需登记；第 5 批删除后同步清理相关引用 | PASS |

**Phase 1 后复查**：无变化（设计未引入新的架构面）。

**IV 附说明**：宪法"引用方向/拆分纪律"约束产品代码重构；本 Spec 第 5 批的产品代码动作为"删除经用户逐项确认、经全仓引用复查为零的死代码"，不属于重构、不改变行为、不移动职责，且为用户明确授权；其余四批不触碰产品代码。

## File Boundaries

*GATE: Must be completed before `/speckit-tasks`. During `/speckit-plan`, produce the file placement list (new/modified/forbidden files, reference direction, line estimate, rationale) and get user confirmation before Phase 0. Every feature must answer all lines.*

- **Allowed files**（允许修改）:
  - `tests/**` — 五批的后端测试条目（逐条清单见 tasks.md，每批实施前逐条复核行号，以内容为准）
  - `webui/src/**/__tests__/**`、`webui/src/**/*.spec.ts`、`webui/src/test/**` — 五批的前端测试条目与测试基建
  - `webui/vite.config.ts` — 仅批四：构建指纹候选集排除测试文件（不改其它配置语义）
  - `hooks/pre-commit` — 仅批四：移除本机硬编码解释器路径，保留既有回退与门禁语义
  - `specs/040-test-suite-slimming/**` — 本 Spec 文档（plan/research/data-model/quickstart/contracts/tasks）与死代码登记册
  - 批五（逐项经用户同意后才可动）：`webui/semantic.py`（整文件）、`webui/src/discovery.ts` 与 `webui/src/screenFlow.ts`、`webui/src/location.ts` 的指定孤儿导出、`tests/test_semantic.py`（整文件）、`webui/src/__tests__/{discovery,screenFlow,location}.spec.ts` 的对应测试段
- **Forbidden files**（禁止修改）:
  - `webui/**`、`scripts/**`、`packaging/**` 的全部产品代码（批五清单之外的任何文件）
  - 门面文件：`webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/zhilian_cdp_raw.py`（即使批五也不动）
  - `roadmap/BACKLOG.md`（用户私人待办本，不写入）
  - 禁碰红线断言所在位置（其所在文件可改，但那些断言不得删除或弱化）
  - 巨型测试文件拆分（`test_pipeline_pause_resume.py`、`test_webui_app_taskrun.py`、`test_webui_app_semantics.py`、`DiscoveryView.spec.ts` 的拆分不做）、CI 流水线重做、`tests/` 包加载方式改造
- **New files**（新增文件）:
  - 仅 `specs/040-test-suite-slimming/` 内文档与登记册
  - 测试代码不新增文件（问题全部为删除/合并/修复/隔离）；若实施中确需抽取公共助手，当批开工前单独列出并经用户同意
- **Reference direction**: 测试 → 产品单向（`tests/* → webui/* | scripts/*`；`*.spec.ts → src/*`）；产品侧引用零变更
- **Line gate**: 产品无新增代码；第 5 批只减；测试文件行数不设红线（拆分另行立项）
- **Rationale**: 测试瘦身只需动测试与其配置；产品代码唯一例外（死代码回收）被隔离为第 5 批、逐项授权；构建指纹与提交前脚本的修改属"测试/构建相关配置"范围（用户已在文件清单中过目）

## Verification Gate

*GATE: Must be completed before `/speckit-tasks`.*

- 功能/重构/拆分交付：最终门禁为相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查（`uv run python -m unittest tests.test_repo_hygiene`）。
- 本 Spec 为工程治理交付，适用上述门禁；节奏为每批"受影响子集 + 卫生"，批末"全量 + 前端 + 构建"，并做批次统计对比（FR-015）。
- 收口发布不自动执行全量测试；提交与推送等待用户明确指令。

## Project Structure

### Documentation (this feature)

```text
specs/040-test-suite-slimming/
├── spec.md              # 已冻结
├── plan.md              # 本文件
├── research.md          # Phase 0 输出
├── data-model.md        # Phase 1 输出
├── quickstart.md        # Phase 1 输出
├── contracts/
│   └── batch-verification.md
├── dead-code-registry.md  # 死代码登记册（用户要求）
└── tasks.md             # Phase 2 输出（/speckit-tasks）
```

### Source Code (repository root)

```text
tests/                                    # 批一~批四：修复/删除/合并/隔离（逐条见 tasks.md）
webui/src/**/__tests__/                   # 批一~批四：前端测试条目
webui/src/test/setup.ts                   # 批四：开关复位、假件清理
webui/vite.config.ts                      # 批四：构建指纹排除测试文件
hooks/pre-commit                          # 批四：移除本机硬编码路径
webui/semantic.py                         # 批五（经同意）：整文件删除
tests/test_semantic.py                    # 批五（经同意）：整文件删除
webui/src/{discovery,screenFlow,location}.ts  # 批五（经同意）：孤儿导出删除
```

**Structure Decision**: 沿用既有单仓结构（`tests/` + `webui/src/`）；不新增目录层级、不新增测试文件。

## Complexity Tracking

无宪法违规项需要豁免。
