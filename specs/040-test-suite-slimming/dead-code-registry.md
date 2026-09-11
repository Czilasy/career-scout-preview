# 死代码登记册（Spec 040 · 第 5 批输入）

> **规则（2026-09-11 用户冻结）**：第 1~4 批实施期间，发现产品代码中的死代码**只记录、不动手**；
> 第 5 批实施前将本册整理为待删清单交用户过目，**经逐项同意后**才删除（产品代码与其测试一并删除）。
> 表中"查证记录"用于证明"确实无人使用"；实施删除前必须再次复核引用仍为零。
>
> 记录口径：只记产品代码（`webui/`、`scripts/`、`packaging/` 等）中的死代码；
> 测试侧自身的死件与垃圾属第 1~2 批处置范围，不进本册。

## 待删候选

| # | 位置 | 是什么 | 查证记录（2026-09-11） | 建议处置 |
|---|---|---|---|---|
| D001 | `webui/semantic.py` 全文件（284 行）+ `tests/test_semantic.py`（223 行，21 例） | 简历与 JD 四维语义相似度框架：提示词构造、输出校验、相似度评估 | 全仓引用检索仅命中 `tests/test_semantic.py`；`from webui import semantic` 曾在生产代码存在，经 `8fedb96`（删除筛选工作台）与 `36fcabe`（删除 discovery 旧管线）两次重构后接线被摘除；`packaging/` 打包配置零引用 | 删除代码与测试（第 5 批） |
| D002 | `webui/semantic.py` L147-283：`validate_job_assessment` 及常量 `JOB_ASSESSMENT_CONTRACT_VERSION`、`JOB_PROPOSED_BANDS`（feature 004 遗留） | 岗位方向评估契约 v1 校验 | 全仓零引用（含测试）；仅模块内自用——连测试覆盖都没有 | 随 D001 一并删除 |
| D003 | `webui/src/discovery.ts` 导出：`normalizeIntegrity`(L43)、`integrityLabel`(L60)、`backfillJobPlatform`(L120)、`classifyTaskSize`(L173)、`recoverSelectionSettings`(L210) | 完整性归一与中文标签、平台回填、任务规模归类、选择设置恢复 | 生产代码零引用；`DynamicIsland.vue` 中的同名 `integrityLabel` 为组件内局部 computed（自读 `props.status.integrity`），并非引用本导出；仅 `discovery.spec.ts` 引用 | 删除导出与对应测试段（第 5 批） |
| D004 | `webui/src/screenFlow.ts` 导出 `primaryActionLabel`(L99) | 主操作文案派生 | 生产代码零引用；仅 `screenFlow.spec.ts` L102-103 引用 | 同上 |
| D005 | `webui/src/location.ts` 导出 `locationCombinationCount`(L85) | 地点组合计数 | 生产代码零引用；仅 `location.spec.ts` L52-53 引用 | 同上 |

## 待删清单（T081，2026-09-12 复核，逐项待用户同意）

> 复核口径：全仓检索 `webui/`、`scripts/`、`packaging/`、`tests/`、`webui/src/`；
> 排除 `specs/**`（历史文档）与本文档自身。删除按"项"为单位，逐项同意后才执行；
> 未同意项保留并注明原因（T084）。

| 项 | 待删内容（精确范围） | 2026-09-12 复核（检索式 → 结果） | 删除动作 | 预计影响 |
|---|---|---|---|---|
| D001+D002 | `webui/semantic.py` 整文件（283 行）+ `tests/test_semantic.py` 整文件（223 行 / 21 例） | `(from\|import).*semantic` → 仅 `tests/test_semantic.py:16`；`semantic` 在 `webui/*.py` 的其余 11 处命中均为英文注释里的 "semantics"（无导入）；`scripts/`、`packaging/` → 0 命中 | 删两个文件 | 后端 -21 例 / -506 行；无生产引用 |
| D003 | `webui/src/discovery.ts` 五个导出（`normalizeIntegrity` L43-58、`integrityLabel` L60-63、`backfillJobPlatform` L120-132、`classifyTaskSize` L173-180、`recoverSelectionSettings` L210-219）；连带孤立：`INTEGRITY_LABELS`（L33-40，仅被 `normalizeIntegrity` 使用）与 5 个类型导入（`IntegrityConclusion` / `AdvancedSettingsState` / `ExecutionSelection` / `ExecutionSettings` / `TaskSize`）；`webui/src/__tests__/discovery.spec.ts` 对应测试段 | `normalizeIntegrity\|integrityLabel\|backfillJobPlatform\|classifyTaskSize\|recoverSelectionSettings\|INTEGRITY_LABELS` → 仅 `discovery.ts` 定义处与 `discovery.spec.ts` 测试处；`DynamicIsland.vue` 的同名 `integrityLabel` 是组件内局部 computed（自读 props.status.integrity），非引用本导出 | 删函数 + 孤立常量/导入 + 测试段 | 前端 -12 例（classifyTaskSize 8、recoverSelectionSettings 1、backfillJobPlatform 2、integrity parsing 1） |
| D004 | `webui/src/screenFlow.ts` 的 `primaryActionLabel`（L99-101）+ `screenFlow.spec.ts` 的导入项与 `it("maps primary action labels")` 用例 | `primaryActionLabel` → 仅 `screenFlow.ts` 定义处 + `screenFlow.spec.ts`（L7 导入、L102-103 断言） | 删函数 + 测试用例 | 前端 -1 例 |
| D005 | `webui/src/location.ts` 的 `locationCombinationCount`（L85-87）+ `location.spec.ts` 的导入项与 `it("counts only district-level locations")` 用例 | `locationCombinationCount` → 仅 `location.ts` 定义处 + `location.spec.ts`（L3 导入、L52-53 断言）；私有 `hasDistrict` 另被 `locationSummary` 使用，保留 | 删函数 + 测试用例 | 前端 -1 例 |

## 已排除（复核后判定保留）

| # | 位置 | 结论 | 依据 |
|---|---|---|---|
| — | （暂无） | — | — |

## 附：发现日志

（按时间追加。第 1~4 批实施期间发现新候选时，在「待删候选」表尾追加 `D00N` 行，并在此登记发现时间与批次。）

- 2026-09-11（Spec 开工前复核）：D001–D005 确认；`validate_job_assessment` 连测试都没有，属模块内死段；`DynamicIsland.vue` 同名函数经核为局部实现（区分成功，不入册）。
- 2026-09-11（批一）：无新增候选。批一清理的均为测试侧死件/恒真引用（`_FakeWhitebox` 假件、`continue-ai-from-results` 恒真断言），按规则不入册。
- 2026-09-12（批四）：无新增候选。批四触碰的均为测试侧基建与测试文件（含 `tests/run_isolated_webui.py`、`tests/sc002_24h_monitor.py` 两个手动资产的头部标注）；`webui/ensure_frontend_sync.py` 虽被审查触及，但属现役同步校验（非死代码），不入册。
