# Implementation Plan: AI 精筛靠谱化（B033）

**Branch**: `009-ai-screen-trust` | **Date**: 2026-08-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/009-ai-screen-trust/spec.md`（grill-me 冻结结论）

## Summary

以 B033 为主：精筛靠谱化。核心实现策略：画像双层（画像事实隐藏落库 + profile_summary 复用承载求职画像）、精筛三通道输入（筛选条件 + 画像事实 + 求职画像）、靠谱判定结构化（特征清单独立模块 + 分级 flags，高危强制 not_match）、结果页与岗位条预警标记、初筛输入增加求职画像放宽（初筛逻辑不动）。存储扩展两个列（`screening_runs.profile_facts_json`、`screening_results.flags_json`），老轮退化零迁移。

## Technical Context

**Language/Version**: Python 3.11（后端）、Vue 3 + TypeScript + Vite（前端）

**Primary Dependencies**: Flask、SQLite、Vue 3、Vitest、Playwright（现有工具链）

**Storage**: SQLite；新增 schema migration 031：`screening_runs.profile_facts_json TEXT`（NULL=老轮）、`screening_results.flags_json TEXT`（NULL=无 flags）。

**Testing**: 后端 `unittest`；前端 `vitest`；构建 `npm run build`；仓库卫生 `uv run python -m unittest tests.test_repo_hygiene`。

**Target Platform**: 本地 Web 工作台 / 桌面 EXE（pywebview）

**Project Type**: 单仓库 Web + 桌面壳应用

**Performance Goals**: 精筛 prompt 输入含画像事实（约 200 token 内），不显著增加单批成本；flags 解析与现有 caveats 同复杂度。

**Constraints**: 不扩大超大文件；`webui/store.py` 只允许 INSERT/读取加列；`webui/store_migrations.py` 只允许 migration 031；`webui/app.py` 除 API 透传需要外不修改；初筛判定逻辑不修改。

**Scale/Scope**: 特征清单 20 条为常量表，后续可扩展。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- 职责分层：特征清单与分级判定进新模块 `webui/flag_features.py`；prompt 与 AI 契约在 `webui/ai.py`；调用接线在 `webui/pipeline_exec.py`（仅传参）；数据访问在 store（仅加列）。
- 单文件尺寸：新增 `webui/flag_features.py` 约 120 行；`ai.py` 本功能新增约 150 行（prompt 重构 + 验证 + 解析），不触发拆分门槛。
- 引用方向：`pipeline_exec.py → ai.py → flag_features.py`；`store.py → store_migrations.py`（仅 schema）；前端 `DiscoveryView.vue → JobWorkspace.vue → types.ts`。
- 拆分纪律：本 Spec 是功能交付，不是重构 Spec；`store.py` 仅做最小加列读写，不搬逻辑；`app.py` 默认不动。
- 验证门禁：最终按功能交付全量门禁执行。

## File Boundaries

*GATE: Must be completed before `/speckit-tasks`. User confirmed 2026-08-11 after review.*

- **Allowed files**:
  - `webui/ai.py`：简历分析 prompt 增加画像事实提取与求职画像规则升级（自然语言，3-5 句仅长度上限，不固定字段、不事实清单化、缺失显式标注）；`match_jds` 增加 criteria/profile_facts 参数并重构精筛 prompt（三通道 + 靠谱判定段落）；flags 解析改为必填 + 分级判定（高危≥1 / 中危≥2，废弃 `FLAGS_MIN_HITS`）；`screen_jobs` prompt 增加求职画像放宽规则（输入加 data 文本）。
  - `webui/pipeline_exec.py`：仅 `match_jds` 调用处传 criteria（复用 `_build_criteria_description`）与 profile_facts；`screen_jobs` 调用处传求职画像文本；不做其它改动。
  - `webui/store_migrations.py`：仅新增 migration 031（两个列）。
  - `webui/store.py`：仅 screening_runs/screening_results 的 INSERT 增加新列写入、读取路径增加 flags 字段；不新增业务方法。
  - `webui/store_helpers.py`：读取时增加 flags 解码（与 caveats 同路径）。
  - `webui/src/types.ts`：JobItem 增加 `flags` 字段。
  - `webui/src/components/JobWorkspace.vue`：详情高危红字 ⚠（AI 判断说明盒子内，背景/边框不变）、中危黄进软性盒子、岗位条标题前 ⚠ 标记。
  - `webui/src/views/DiscoveryView.vue`：后端回写合并处增加 flags 合并。
  - `webui/src/styles.css`：高危红字与岗位条 ⚠ 样式（复用 `--danger`/`--unsure` 变量）。
  - 测试：`tests/test_ai.py`、`tests/test_workbench_api.py`（若 API 透传 flags）、`webui/src/components/__tests__/JobWorkspace.spec.ts`、`webui/src/views/__tests__/DiscoveryView.spec.ts`。
- **Forbidden files**:
  - `webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/zhilian_cdp_raw.py`：不修改。
  - `webui/store.py` 不允许追加业务方法；`webui/store_migrations.py` 不允许做非 031 的其它结构改动。
- **Allowed with minimum change**:
  - `webui/app.py`：仅筛选任务参数组装/透传处增加 `profile_facts`（与现有 `profile_summary` 同路径，实测 420 行附近已有 params 透传）；不做其它改动。此例外是画像事实保存链路的必要接线（AI 分析 → 任务参数 → pipeline_exec → 落库），无法绕开。
- **New files**:
  - `webui/flag_features.py`（约 120 行）：20 条特征清单常量（code/level/判定依据），分级判定辅助函数（高危≥1 或中危≥2 → 输出；中危 1 条 → caveats）。
