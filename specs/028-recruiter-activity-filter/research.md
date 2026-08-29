# Research: 028 招聘者活跃时间筛选（B081）

日期：2026-08-29。事实来源：本仓代码核验 + 2026-08-29 Boss 实测（18 详情页 18 成功）+ 2026-08-28 智联浏览器实测（见 BACKLOG B081）。

## D1. Boss 活跃文本值域与映射表

- **Decision**: 值域实测 7 种：`在线`、`刚刚活跃`、`今日活跃`、`3日内活跃`、`2周内活跃`、`3月内活跃`、`半年前活跃`；形态规律为上界型（N日内/N周内/N月内活跃 → 活跃距今 ∈ [0, 上界]）与下界型（半年前活跃 → [180, ∞)）。映射表：在线→[0,0]、刚刚活跃→[0,0]、今日活跃→[0,1]、昨日活跃→[1,2]（未实测但为已知形态，tests/chrome_setup/test_chrome_setup.py:609 有夹具）、N日内活跃→[0,N]、N周内活跃→[0,7N]、N月内活跃→[0,30N]、半年前活跃→[180,∞)、防御性 N月前活跃→[30N,∞)、N年前活跃→[365N,∞)；表外文本→未知。
- **Rationale**: 拦截条件是「活跃距今 > 档位」，只有下界型文本能证明「确定超过」；上界型跨档位时（如 2周内活跃 vs 近一周）保守不拦，与冻结口径「确定超过才拦、未知不误拦」一致。
- **Alternatives considered**: 文本直接交给 AI 判定——违反冻结决策「规则模板生成、确定性判定」；按上界激进拦截——会误拦（2周内活跃者可能昨天刚活跃）。

## D2. Boss 活跃文本截获位置

- **Decision**: `scripts/boss/detail_parse.py` 新增 `extract_recruiter_activity_text(page_text) -> str`（复用名片区块定位：`_recruiter_footer_start` 的 card 检测——名片行结构为「姓名 | 活跃文本 | 公司 | · | 头衔」紧邻竞争力分析标记；无「·」分隔的裸名片（实测 18 中 1 例）返回空 → 未知）；`scripts/boss/detail_scrape.py` 的 `build_detail_record` 把该文本作为 `recruiter_activity_text` 键带出。
- **Rationale**: 现状 page_text 在 `extract_job_description`（detail_parse.py:134-136）裁剪后即弃，是唯一截获点；detail_scrape 720 行过预警线，只加一个无逻辑字典键，逻辑落在 216 行的 detail_parse。
- **Alternatives considered**: 在 webui 侧二次解析详情产物——page_text 根本不落盘，不可行；改造 EXTRACT_DETAIL_JS 单独提取名片 DOM——详情页文本形态已稳定且实测核验，无需新 JS。

## D3. 智联 lastOnlineTime 提取与超标文件分流

- **Decision**: 新建 `scripts/zhilian/` 包（`detail_fields.py`），承载详情页 staff 字段提取 JS 片段（`window.__INITIAL_STATE__.jobDetail.staff`：`lastOnlineTime` 毫秒时间戳 + 状态文本）与结果合并纯函数；`scripts/zhilian_cdp_raw.py`（894 行，超标）仅做 import + 现有提取表达式拼接（约 +8 行引用级改动，无新逻辑）。
- **Rationale**: staff 数据只能在抓取子进程的浏览器 JS 里取；宪法原则 VI 预警线/红线要求超标文件后续改动开新模块分流，禁止继续积累逻辑。`_scrape_detail_on_ws`（:481-558）是详情提取唯一入口，表达式拼接点唯一（:521-531）。
- **Alternatives considered**: 直接改 zhilian_cdp_raw.py 加逻辑——违反宪法原则 VI/AGENTS.md 超大文件纪律；webui 侧补抓 staff——详情接口数据只在子进程可见，不可行。

## D4. 数据流与持久化载体

- **Decision**: 详情抓取产物经 `webui/pipeline_exec_details.py` 的 `_apply_batch_outcomes`（:139-190，现状只保留 jd，其余字段丢弃）时，把新字段归一化为 `recruiter_activity` 事实字典并入内存 job 的 `extra` 字典；详情成功后调用 store 新方法 `update_job_extra` 把该字典合并进 `jobs.extra_json`（仅新抓取生效，无回填）。精筛内存路径（ai_screen_task.py:398 survivors → ai_screen_fine.py:19）自然携带 extra；补抓重判路径（recrawl_task.py:212-238 经 `_build_pipeline_result_rows`）从 store 读行也带 extra。
- **Rationale**: 内存 `extra` 字典是既有载体（`store_pipeline_results.py:132` 写 `job.get("extra")` 进 screening_results.extra_json，`source_boss_helpers.py:64-74` 列表阶段先例），零新增通道；jobs 表持久化满足 US3-3（手动重抓自然带上）；无新列、无 migration。
- **Alternatives considered**: jobs 表新列 + migration 033——判定只在精筛读一次，JSON 字典足够，避免迁移成本；只存内存不落库——补抓/重判路径拿不到，违反 FR-003。

## D5. 判定落位与超标文件最小接线

- **Decision**: 新建 `webui/recruiter_activity.py`（判定域：映射表归一化 `normalize_detail_activity`、档位判定 `evaluate(activity, threshold_days) -> not_match verdict dict | None`、未知收集与 caveat 合并助手 `unknown_job_ids`/`merge_unknown_caveat`、判定说明模板、人话距离格式化）；`webui/ai_filters.py` 的硬规则入口扩展为组合函数（六类 + 第 7 类，第 7 类仅在精筛入口启用）；`webui/ai_screening.py`（653 行，过 600 预警线）仅在 `match_jds` 硬规则块（:446-460，实测核实为 `_job_criteria_hard_mismatch(_job, criteria)` 循环）做最小接线（≤6 行），`screen_jobs`（粗筛，:110-136）不动。判定说明由规则模板生成：「负责人上次活跃{人话距离}，超过要求的{档位 label}」；未知岗位（known=false 且选中档位）的 AI verdict 合并时附 caveat「招聘者活跃时间未知，未按第 7 类拦截」（精筛 verdict dict 序列化经 `save_verdict_and_checkpoint_atomic`，caveats 随 dict 落库，store_runs.py:546）。
- **Rationale**: 确定性规则与六类硬规则同层（冻结决策）；判定域独立成模块避免 ai_screening.py 继续增长（宪法原则 VI）；第 7 类不进粗筛（FR-008，列表期无数据）。
- **Alternatives considered**: 判定逻辑写进 ai_screening.py——文件过预警线，禁止增长；写进 ai_prompts.py 让 AI 判——违反冻结决策。

## D6. 筛选 schema 与 AI prompt 边界

- **Decision**: `webui/platforms_schema.py` 公共字段集（`_BOSS_COMMON_FIELDS`，两平台共用）追加 `recruiter_activity`；`FilterField(multiple=False)`，四档稳定码 `week/month/quarter/half_year`（label 近一周/近一个月/近三个月/近半年）；`BOSS_FILTER_SCHEMA_VERSION 1→2`、`ZHILIAN_FILTER_SCHEMA_VERSION 2→3`。`webui/platforms_boss.py` / `platforms_zhilian.py` 各自 schema 构建加该字段。`webui/ai_filters.py` `_build_criteria_description`（:64-87，粗筛 :140 与精筛 :465 都调用）排除 `recruiter_activity` 键——AI 粗筛/精筛 prompt 均不出现第 7 类（FR-010）；`webui/ai_prompts.py` 六类文案不改。续跑复用 `screen_flow.find_resumable_screen_run`（:48-72）是 frozen_filters 全字典相等比对，新维度自动纳入，无需改码；用户不选第 7 类时 frozen_filters 与旧形态一致，旧 run 仍可复用（行为等价于不限）。
- **Rationale**: 单选由既有 `validate_filter_values` 的 `field.multiple` 校验强制（platforms_filters.py「不允许多选」分支已存在）；schema 驱动使 /api/filter-labels 与前端 chips 自动获得新字段。
- **Alternatives considered**: 修改 ai_prompts.py「六类」文案为「七类」——第 7 类不进 AI，文案保持六类才是事实；screen_flow 增加字段枚举——全字典比对已覆盖，加枚举是多余抽象。

## D7. 前端单选交互

- **Decision**: 前端现状把 `multiple` 丢弃（useDiscoveryState.ts:598-616 filterGroups 只保留 key/label/sentinel/options），`toggleFilter`（useDiscoverySearch.ts:208-214）与 OneClickScreenDialog.vue 自带 toggle（:73/:119-133）恒为数组增删。改动：filterGroups 透传 `multiple`；两处 toggle 增加单选分支——`multiple === false` 时：点已选档位 = 取消（清空）；点新档位 = 替换；「不限」哨兵 = 清空。`webui/src/types.ts` 的 FilterGroup 类型加 `multiple?: boolean`。
- **Rationale**: 后端 `validate_filter_values` 已强制单选（多选返回 422），前端不做单选交互会导致用户选两个档位被 422 拒绝，体验断裂；改动面仅两个 toggle 函数 + 类型透传。
- **Alternatives considered**: 后端放宽为「多选取最宽」——违反冻结决策（单选）；新增独立单选组件——过度设计，chips 样式复用。

## D8. 测试落位

- **Decision**: 判定域单测新建 `tests/ai/test_recruiter_activity.py`（映射表全值域、四档×上界/下界/精确三种事实的判定矩阵、未知兜底、说明模板、_build_criteria_description 排除断言）；采集层测试新建 `tests/source/test_recruiter_activity_capture.py`（boss detail_parse 提取含无名片例、build_detail_record 带键、zhilian 合并、_apply_batch_outcomes 透传、update_job_extra 落库）；schema/校验扩展既有 `tests/test_platforms.py`（FilterFieldTests/ValidateFilterValuesTests/Boss·ZhilianRegistrationTests 风格）；精筛接线扩展 `tests/ai/test_ai_match.py`（MatchJdsFailurePolicyTests 风格：命中/未知不拦/不选不限）；续跑扩展 `tests/test_screen_flow.py`。
- **Rationale**: 与 027 拆分后的测试目录结构对齐（tests/ai/、tests/source/、tests/webui_store/ 等）；既有测试类风格延续，便于聚焦门禁按文件运行。
- **Alternatives considered**: 全塞进单个新大文件——违反测试拆分方向。

## D9. 已核验代码事实（写 plan/tasks 的依据）

- 精筛入参为内存 dict（键 job_id/title/salary/location/jd/tags…+ extra），不经 store 查询（ai_screen_fine.py:19、ai_screen_task.py:398、:275-278）。
- 六类硬规则 `_job_criteria_hard_mismatch` 读 job 的 salary/tags/jobExperience/jobDegree/tags_list/company_scale/company_stage/company_industry（ai_filters.py:150-210、:265-279），reason 模板「{字段label}{值}不在筛选范围」。
- 硬规则在 `screen_jobs`（ai_screening.py:111-131）与 `match_jds`（:446-460）两处应用；第 7 类只加后者。
- `validate_filter_values`（platforms_filters.py:39）按 schema 校验 + multiple 强制；schema 版本不匹配抛 `filter_schema_version_mismatch`。
- 精筛 verdict dict（verdict/reason/caveats/flags）整体序列化落库（store_runs.py:546）；结果分桶在前端 `partitionPipelineResult`（src/discovery.ts:117-127）按 verdict 取值，not_match 已有不匹配桶，前端零改动。
- 详情失败岗位沿用「未抓到 JD 无法精筛」既有语义（ai_screen_jd.py:207-218），第 7 类不新增失败分类。
