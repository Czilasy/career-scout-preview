# Tasks: AI 精筛靠谱化（B033）

**Input**: Design documents from `/specs/009-ai-screen-trust/`

**Prerequisites**: plan.md, spec.md

**Organization**: 按依赖分层：数据层 → 特征清单 → 画像双层 → 精筛/靠谱判定 → 前端展示 → 测试。

## File Boundaries

- **Allowed files**: 见 `plan.md` File Boundaries；`webui/store.py` 只允许 INSERT/读取加列；`webui/store_migrations.py` 只允许 migration 031；`webui/app.py` 仅允许筛选任务参数透传 `profile_facts`（与 `profile_summary` 同路径的最小接线）。
- **Forbidden files**: `webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/zhilian_cdp_raw.py` 不修改；`webui/store.py` 不追加业务方法。
- **New files**: `webui/flag_features.py`。
- **Reference direction**: `pipeline_exec.py → ai.py → flag_features.py`；`store.py → store_migrations.py`；前端 `DiscoveryView.vue → JobWorkspace.vue → types.ts`。
- **Line gate**: 新增 Python 文件 ≤800 行、Vue ≤1200 行；不向超大文件追加长逻辑。

## Verification Gate

- 功能交付最终门禁：相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 收口发布任务不要求全量测试，按根 `AGENTS.md` 收口规则执行。

## Phase 1: 数据层（共享基础）

**Purpose**: 两列 schema 与读写最小接线；所有用户故事依赖此层。

- [ ] T001 [P] 在 `webui/store_migrations.py` 的 `_migrate()` 迁移序列注册 migration 031：给 `screening_runs` 加可空 `profile_facts_json TEXT`、给 `screening_results` 加可空 `flags_json TEXT`（存量行 NULL=老轮/无 flags，不做数据回填）
- [ ] T002 修改 `webui/store.py`：screening_runs 的 INSERT 增加 `profile_facts_json` 写入（来源：筛选任务参数携带的画像事实，缺省 NULL）；screening_results 的 INSERT 增加 `flags_json` 写入（来自精筛 verdict dict 的 flags，缺省 NULL）
- [ ] T003 修改 `webui/store_helpers.py`：岗位行读取增加 `flags` 解码（`_decode_json(row.get("flags_json"), [])`，与 caveats 同路径）

**Checkpoint**: 迁移可跑、新列读写可验证、存量数据读取无回归（现有测试通过）。

## Phase 2: 特征清单模块

**Purpose**: 靠谱判定数据源独立成模块，prompt 只引用，后续加特征不改 prompt 结构。

- [ ] T004 [P] 新增 `webui/flag_features.py`：20 条特征清单常量（code/level/判定依据，A 中介包装 / B 销售话术 / C 收费培训 / D 薪资雇佣关系 / E 信息矛盾 / F 异常行为；"岗位常年挂着"不纳入）；辅助函数：给定 flags 列表输出判定结果（高危≥1 或 中危≥2 → 保留输出；中危仅 1 条 → 降级为 caveats）；`FLAGS_MIN_HITS` 语义由本模块分级逻辑替代

**Checkpoint**: 模块单测通过（分级边界用例：高危 0 中危 1 → caveats；高危 1 → flags；中危 2 → flags）。

## Phase 3: 画像双层（简历分析扩展）

**Purpose**: AI 分析一次产出筛选条件 + 画像事实 + 求职画像。

- [ ] T005 [P] 修改 `webui/ai.py` 的 `analyze_resume_to_fields`：system prompt 增加画像事实提取规则（core_skills 列表 / projects[{name,role,stack,summary}] / job_type 四值 / languages 列表，缺失标"未体现"，禁止推断编造）；求职画像生成规则保持自然语言，3-5 句仅作为长度上限，不按固定字段逐句填写、不写成事实清单；结构化字段一律由隐藏的画像事实承担
- [ ] T006 修改 `webui/ai.py`：新增 `_validate_profile_facts` 宽松验证（类型 + 长度 + job_type 四值枚举；无效项丢弃不阻塞），`analyze_resume_to_fields` 返回值增加 `profile_facts` 字段

**Checkpoint**: 构造含/不含学历薪资的简历文本，验证画像事实提取与"未体现"标注、宽松验证丢弃无效项。

## Phase 4: 精筛三通道 + 靠谱判定

**Purpose**: match_jds 输入三通道、prompt 重构、flags 分级；screen_jobs 求职画像放宽；pipeline_exec 接线。

- [ ] T007 [P] 修改 `webui/ai.py` 的 `match_jds`：签名增加 `criteria`（筛选条件，转自然语言复用 `_build_criteria_description`）与 `profile_facts`（画像事实）参数；system prompt 重构为三通道输入（候选人基线=画像事实 / 筛选条件 / 求职意愿=data 最高优先级）与判断规则（未体现不得推断、意愿扩展方向不因基线未命中排除、意愿未提及维度回退基线、意愿与基线冲突以意愿为准）
- [ ] T008 [P] 修改 `webui/ai.py` 的 `match_jds`：prompt 增加靠谱判定段落（引用 `flag_features.py` 特征清单，flags 为必填字段可空数组，reason 引用 JD 原文证据）；解析端：flags 清洗与分级判定接入 `flag_features` 辅助函数；命中高危时强制 verdict=not_match 且 reason 以"疑似骗局："开头；`FLAGS_MIN_HITS` 常量删除
- [ ] T009 修改 `webui/ai.py` 的 `screen_jobs`：criteria 描述后附加求职画像全文与放宽规则（data 中明确放宽的维度以 data 为准，未涉及维度仍按筛选条件判断）；初筛判定逻辑本体不动
- [ ] T010 修改 `webui/app.py` 与 `webui/pipeline_exec.py`（画像事实传递链路，均为最小接线）：app.py 筛选任务参数组装/透传处增加 `profile_facts`（与现有 `profile_summary` 同路径，实测 420 行附近）；pipeline_exec 的 `match_jds` 调用处传 `criteria`（调用方已有筛选条件）与 `profile_facts`；`screen_jobs` 调用处传求职画像文本；不做其它改动

**Checkpoint**: 精筛三通道与 flags 分级聚焦测试通过；高危岗位 reason 前缀、中危仅标记行为正确。

## Phase 5: 前端展示

**Purpose**: 详情页高危红字 ⚠、中危黄进软性盒子、岗位条 ⚠ 标记。

- [ ] T011 修改 `webui/src/types.ts`：JobItem 增加 `flags?: { code: string; level: "high" | "medium"; reason: string }[]`
- [ ] T012 修改 `webui/src/views/DiscoveryView.vue`：后端回写合并处增加 flags 合并（与 caveats 同路径，2228 行附近）
- [ ] T013 [P] 修改 `webui/src/components/JobWorkspace.vue`：详情页"AI 判断说明"盒子——flags 含 high 时标题与正文文字标红（`--danger`）、reason 前渲染红色 ⚠ 图标（前端渲染，AI 不输出符号），盒子背景/边框不变、不新增子块；flags 含 medium 时提醒并入"软性要求提醒"盒子与 caveats 并列（`--unsure` 色系）
- [ ] T014 修改 `webui/src/components/JobWorkspace.vue`：岗位条（job-row）标题前渲染 ⚠（high 红 / medium 黄），无 flags 不渲染
- [ ] T015 修改 `webui/src/styles.css`：高危红字样式与岗位条 ⚠ 样式（复用 `--danger`/`--unsure` 变量，不新增色系）

**Checkpoint**: 组件测试通过；手工/浏览器验证：高危红字背景不变、中危黄、岗位条标记、无 flags 岗位不显示。

## Phase 6: 测试与收尾

**Purpose**: 全链路覆盖与门禁。

- [ ] T016 后端测试：`tests/test_ai.py` 增加画像事实提取/宽松验证/自然语言求职画像 prompt 契约（断言不出现"事实清单式"与固定逐句字段）、精筛三通道 prompt 契约（含老轮无画像事实退化）、flags 分级判定（含高危强制 not_match、中危降级 caveats、必填字段缺失容错）；`tests/test_workbench_api.py`（若 API 透传 flags）与迁移相关测试
- [ ] T017 前端测试：`webui/src/components/__tests__/JobWorkspace.spec.ts` 覆盖详情红/黄渲染与岗位条 ⚠；`webui/src/views/__tests__/DiscoveryView.spec.ts` 覆盖 flags 回写合并
- [ ] T018 收尾：`uv run python -m unittest tests.test_repo_hygiene`、后端聚焦测试、前端测试、`npm run build` 全绿；检查 `git status` 无意外文件；README 若有用户可感知能力变化同步更新

**Checkpoint**: 全量门禁通过，需求验收清单（spec.md 验收汇总）逐项勾选。
