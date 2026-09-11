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

## 已排除（复核后判定保留）

| # | 位置 | 结论 | 依据 |
|---|---|---|---|
| — | （暂无） | — | — |

## 附：发现日志

（按时间追加。第 1~4 批实施期间发现新候选时，在「待删候选」表尾追加 `D00N` 行，并在此登记发现时间与批次。）

- 2026-09-11（Spec 开工前复核）：D001–D005 确认；`validate_job_assessment` 连测试都没有，属模块内死段；`DynamicIsland.vue` 同名函数经核为局部实现（区分成功，不入册）。
