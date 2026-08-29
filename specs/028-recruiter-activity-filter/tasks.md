# Tasks: 第 7 类筛选条件：招聘者活跃时间（028）

**Input**: Design documents from `/specs/028-recruiter-activity-filter/`

**Prerequisites**: plan.md、spec.md、research.md、data-model.md、contracts/、quickstart.md

**Tests**: 宪法原则 V 功能交付门禁要求测试（FR-011），故每个实现任务附聚焦测试；测试先行（先写失败用例再实现）。

**Organization**: 按 spec.md 用户故事分组：US1 陈年岗位拦截（P1）、US2 未知不误拦（P1）、US3 仅新抓取生效（P2）、US4 条件区单选交互（P2）。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Story]**: 所属用户故事
- 所有路径相对仓库根

## File Boundaries

来自 plan.md「File Boundaries」，任务不得越界：

- **Allowed files**: `scripts/boss/detail_parse.py`、`scripts/boss/detail_scrape.py`、`scripts/zhilian_cdp_raw.py`（≤8 行引用级）、`webui/platforms_schema.py`、`webui/platforms_boss.py`、`webui/platforms_zhilian.py`、`webui/ai_filters.py`、`webui/ai_screening.py`（≤6 行接线）、`webui/pipeline_exec_details.py`、`webui/store_jobs.py`、`webui/src/types.ts`、`webui/src/composables/useDiscoveryState.ts`、`webui/src/composables/useDiscoverySearch.ts`、`webui/src/components/OneClickScreenDialog.vue`、`README.md`、`.specify/memory/constitution.md`（模块地图小节）、`CHANGELOG.md`、版本联动文件（经 `scripts/bump_version.py`）、`webui/dist/`（构建产物同步）、测试文件（见任务）
- **Forbidden files**: `webui/app.py`、`webui/store.py`、`webui/source.py`、`webui/ai.py`、`scripts/boss_cdp_raw.py`、`webui/src/listFilter.ts`、`webui/store_migrations_*.py`、`webui/results_api.py`、`webui/runners/*`、`webui/ai_prompts.py`、Boss/智联列表搜索逻辑、`roadmap/`（本地文件不入库）
- **New files**: `webui/recruiter_activity.py`（判定域）、`scripts/zhilian/__init__.py`、`scripts/zhilian/detail_fields.py`（智联 staff 提取分流）、`tests/ai/test_recruiter_activity.py`、`tests/source/test_recruiter_activity_capture.py`、`webui/src/__tests__/` 下单选交互 spec
- **Reference direction**: scripts 采集（原始文本/时间戳）→ `webui/recruiter_activity`（归一化+判定）← `ai_filters`/`pipeline_exec_details` → `store_jobs`；前端 view → composables → api/client；禁止反向 import
- **Line gate**: `ai_screening.py` ≤660、`detail_scrape.py` ≤725、`zhilian_cdp_raw.py` ≤905（引用级）、其余产品文件 <600、新文件 ≤200

## Verification Gate (task-type aware)

- 功能交付（T022）：相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查，全绿。
- 收口任务（T023/T024）：卫生测试、hooks、`git diff --check`、`git status`；不跑全量测试。

---

## Phase 1: Setup

**Purpose**: 基线与工作区纪律

- [ ] T001 拍基线：`git status` 确认工作区干净；后端全量 `uv run python -m unittest discover -s tests` 与前端 `cd webui && npm test` 各跑一次全绿，用例总数记入本文件 Notes（输出重定向到系统临时目录，当轮清理）；任何基线红先停止查明，不带入本功能

---

## Phase 2: Foundational（判定域 + schema，阻塞全部故事）

**Purpose**: 第 7 类的判定核心与两平台 schema 字段；所有用户故事依赖

- [ ] T002 [P] 先写失败测试 `tests/ai/test_recruiter_activity.py`：Boss 文本→区间映射全值域（在线/刚刚活跃/今日活跃/昨日活跃/3日内/2周内/3月内/半年前/N月前/N年前/表外未知）、智联时间戳→精确天数、四档×三事实形态判定矩阵（下界超档=拦、上界跨档=不拦、精确超档=拦、未知=不拦）、`humanize_days` 人话距离、判定说明模板「负责人上次活跃{X}，超过要求的{档位label}」
- [ ] T003 实现判定域 `webui/recruiter_activity.py`（≤200 行）：`FIELD_KEY="recruiter_activity"`、四档 `THRESHOLD_DAYS={"week":7,"month":30,"quarter":90,"half_year":180}` 与 label 表、`normalize_detail_activity(platform, detail)`（Boss 映射表/智联 last_online_ms 换算，解析异常一律 known=false 不抛错）、`evaluate(activity, threshold_days) -> dict|None`（含 not_match verdict dict 与 reason 模板）、`humanize_days(days)`；使 T002 全绿
- [ ] T004 [P] `webui/platforms_schema.py`：`_BOSS_COMMON_FIELDS` 追加 `"recruiter_activity"`，`BOSS_FILTER_SCHEMA_VERSION 1→2`、`ZHILIAN_FILTER_SCHEMA_VERSION 2→3`；扩展 `tests/test_platforms.py`（新字段存在于两平台 schema、`multiple=False`、版本断言更新）
- [ ] T005 [P] `webui/platforms_boss.py` 与 `webui/platforms_zhilian.py` schema 构建各加 `FilterField(key="recruiter_activity", label="招聘者上次活跃", multiple=False, options=四档)`；扩展 `tests/test_platforms.py` 注册测试类断言 options/label
- [ ] T006 [P] `webui/ai_filters.py`：`_build_criteria_description` 排除 `recruiter_activity` 键（AI 粗筛/精筛 prompt 均不得出现）+ 字段 label 表加「招聘者上次活跃」；`tests/ai/test_ai_match.py` 扩展断言：screening_fields 含 recruiter_activity 时粗筛与精筛条件描述均不含该键

**Checkpoint**: 判定域与 schema 就绪；`uv run python -m unittest tests.ai.test_recruiter_activity tests.test_platforms` 全绿

---

## Phase 3: User Story 1 - 陈年岗位自动归入不匹配 (Priority: P1) 🎯 MVP

**Goal**: 选中档位后，「招聘者上次活跃超过档位」的岗位在精筛硬规则层被判不匹配，说明可复核；两平台口径一致

**Independent Test**: 构造 extra 带活跃事实的岗位跑 `match_jds`：半年前活跃 + 近一周 → not_match 且说明含「半年前」「近一周」；30 天内活跃 + 近三个月 → 不拦

### Implementation for User Story 1

- [ ] T007 [US1] `scripts/boss/detail_parse.py` 新增 `extract_recruiter_activity_text(page_text) -> str`（复用名片区块定位逻辑，含实测无名片形态「姓名|头衔」无 · 分隔返回空串；不改动既有 `extract_job_description` 行为）；`scripts/boss/detail_scrape.py` `build_detail_record` 增 `"recruiter_activity_text"` 键（+1 行）；在 `tests/source/test_recruiter_activity_capture.py` 固化实测样本 fixture（含 7 种文本与无活跃行 1 例）并断言提取结果
- [ ] T008 [P] [US1] 新建 `scripts/zhilian/__init__.py` 与 `scripts/zhilian/detail_fields.py`（≤70 行）：staff 提取 JS 片段常量（`jobDetail.staff` 的 `lastOnlineTime` 毫秒时间戳 + 状态文本）与 `merge_staff_fields(detail: dict, staff: dict) -> dict` 纯函数（产出 `recruiter_activity_text`/`recruiter_last_online_ms` 键；取不到置空）；附单测（同 capture 测试文件）
- [ ] T009 [US1] `scripts/zhilian_cdp_raw.py` `_scrape_detail_on_ws` 引用级接线（≤8 行：import detail_fields、提取表达式拼接 staff 片段、结果 dict 经 merge_staff_fields 合并）；确认 detail 提取既有行为零变化（现有智联详情测试全绿）
- [ ] T010 [US1] `webui/ai_filters.py` 组合硬规则入口：新增 `job_hard_mismatch(job, screening_fields, *, include_recruiter=False)`（内部先跑既有 `_job_criteria_hard_mismatch`，`include_recruiter=True` 时再调 `recruiter_activity.evaluate`，从 `job.get("extra", {}).get("recruiter_activity")` 取事实与档位码）；`webui/ai_screening.py` `match_jds` 硬规则块（:446-460 附近）改调组合入口（≤6 行，`screen_jobs` 粗筛不接）；顺带把 `webui/screen_flow.py` `find_resumable_screen_run` docstring 的「六类条件」改为全量筛选条件口径（仅注释，零逻辑）
- [ ] T011 [US1] `webui/pipeline_exec_details.py` 详情合并：`_apply_batch_outcomes` 与 enriched 合并处，detail 带 `recruiter_activity_text`/`recruiter_last_online_ms` 时调 `normalize_detail_activity` 并把结果并入 `job["extra"]["recruiter_activity"]`（无数据不写键）
- [ ] T012 [US1] `tests/ai/test_ai_match.py` 扩展精筛四态：命中拦（Boss 下界型）、智联时间戳超档拦、正常活跃不拦、不选第 7 类完全等价现状；断言判定说明含实际距离与档位 label；断言粗筛 `screen_jobs` 对含 extra 事实的岗位不做第 7 类剔除

**Checkpoint**: US1 独立可验：`uv run python -m unittest tests.ai.test_ai_match tests.source.test_recruiter_activity_capture` 全绿；T002 矩阵 + T012 四态覆盖 US1 三条验收场景与 SC-001

---

## Phase 4: User Story 2 - 未知不误拦 (Priority: P1)

**Goal**: 拿不到活跃数据（存量无字段/无名片/无活跃行/映射外文本）时标注「活跃时间未知」且绝不拦截

**Independent Test**: extra 无事实 / known=false 的岗位在四档任一档位下均通过第 7 类，verdict 带未知 caveat

### Implementation for User Story 2

- [ ] T013 [US2] 未知 caveat 接线：`webui/recruiter_activity.py` 新增 `unknown_job_ids(jobs, screening_fields) -> set[str]` 与 `merge_unknown_caveat(verdict: dict) -> dict`（known=false 且选中档位时返回/并入 `caveats=["招聘者活跃时间未知，未按第 7 类拦截"]`；未选档位不产生 caveat）；`webui/ai_screening.py` `match_jds` 接线处调用（硬规则后收集一次、AI verdicts 合入时合并，累计 ≤6 行门禁内）；`tests/ai/test_ai_match.py` 扩展未知三态（extra 无键、`recruiter_activity_text=""`、映射表外文本）+ 断言未选档位时无未知 caveat（避免噪音）

**Checkpoint**: US2 独立可验：未知三态全部通过第 7 类且标注正确（SC-002）

---

## Phase 5: User Story 3 - 仅新抓取生效（含持久化）(Priority: P2)

**Goal**: 详情抓取的活跃事实落 `jobs.extra_json`（手动重抓自然更新），无任何回填

**Independent Test**: 模拟详情抓取完成后 jobs 行 extra_json 含 `recruiter_activity`；存量行不被触碰

### Implementation for User Story 3

- [ ] T014 [US3] `webui/store_jobs.py` 新增 `update_job_extra(platform, platform_job_id, patch: dict) -> bool`：读行 `extra_json` → 合并 patch → 原子写回（行不存在返回 False）；在 `tests/webui_store/` 既有 store 测试文件扩展：合并语义（保留既有键）、空 extra_json 起步、行缺失 False
- [x] T015 [US3] ~~偏差~~ **已修复（2026-08-29 用户授权放开 runners 禁改后接线，B084 同批关闭）**：`fetch_job_details` 的三个调用方（`runners/ai_screen_jd.py`、`runners/recrawl_task.py`、`pipeline_exec_tuning.py`）与 store 无可达路径（source 按设计不落库、runners 在禁改清单、无全局 ctx 句柄），Allowed 清单内无法接线 `update_job_extra`。本批降级为：事实随运行内 extra 链路判定（T011 已实现，覆盖主筛选链路）+ 结果行 `screening_results.extra_json` 自然持久化；`update_job_extra` 原语已实现并有测试（T014）；跨 run 重抓事实回填（recrawl 的 `fetched_jd` 仅携带 jd，extra 在 runner 内被丢弃）记 BACKLOG，待解除一处 runner 禁改后接线。→ 用户随后授权，修复落地：`fetch_job_details` 增可选 `store` 参数（update_job_extra 岗位目录持久化），`recrawl_task.py` 携带 extra 进重判输入并经 `save_recrawl_jd_and_checkpoint(extra_by_job=...)` 合并进结果行 extra_json；ai_screen_jd/tuning 调用方同步；测试见 RecrawlActivityFactTests、PipelineDetailStorePersistTests、store 域两例

**Checkpoint**: US3 独立可验：新抓取落库、存量零触碰（无 migration 变更，`git diff` 证明）

---

## Phase 6: User Story 4 - 条件区单选交互 (Priority: P2)

**Goal**: 前端第 7 类单选：选新替换旧、再点取消、「不限」哨兵清空；六类多选行为不变

**Independent Test**: vitest 组件/组合函数测试覆盖四种点选路径

### Implementation for User Story 4

- [ ] T016 [P] [US4] `webui/src/types.ts`（FilterGroup 增加 `multiple?: boolean`）与 `webui/src/composables/useDiscoveryState.ts`（filterGroups 透传 `multiple`，:598-616 一带）
- [ ] T017 [US4] `webui/src/composables/useDiscoverySearch.ts` `toggleFilter`（:208-214）与 `webui/src/components/OneClickScreenDialog.vue` 自带 toggle（:119-133）增加单选分支：`multiple === false` 时点已选值=清空、点新值=替换、哨兵=清空；多选字段逻辑零变化
- [ ] T018 [US4] 新建 `webui/src/__tests__/` 单选交互 spec（vitest，沿用 listFilter.spec.ts 风格）：单选替换/取消/哨兵清空 + 多选字段回归断言

**Checkpoint**: US4 独立可验：`cd webui && npm test` 全绿；`npm run build` 通过（vue-tsc 类型检查）

---

## Phase 7: Polish & Cross-Cutting

**Purpose**: 文档、模块地图、全量门禁与提交

- [ ] T019 走查 `specs/028-recruiter-activity-filter/quickstart.md`：聚焦测试命令逐一全绿
- [ ] T020 `README.md` 文档同步：筛选条件说明由六类扩为七类（第 7 类一句话说明：招聘者上次活跃超过所选档位判不匹配、拿不到数据不拦），不写实现细节
- [ ] T021 `.specify/memory/constitution.md` 模块地图登记：`webui/recruiter_activity.py`（判定域）与 `scripts/zhilian/detail_fields.py`（智联详情 staff 提取）两行，标注（028）
- [ ] T022 全量门禁：后端全量 `uv run python -m unittest discover -s tests` + 前端 `npm test` + `npm run build` + `uv run python -m unittest tests.test_repo_hygiene`，全绿；确认行数门禁（wc -l 核对 File Boundaries 上限）
- [ ] T023 [收口] 版本提升：`uv run python scripts/bump_version.py minor`（1.7.11 → 1.8.0）生成 CHANGELOG 条目（简单列表格式：增加：第 7 类筛选条件「招聘者上次活跃」……），核对联动文件
- [ ] T024 [收口] 提交：`git status`/`git diff --check` 核查无越界文件与临时产物 → 单个 `feat` Conventional Commit（含 webui/dist 同步产物与 spec 文档）；hooks 通过；不自动 push

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1（T001）**：无依赖，最先执行；基线红则停止
- **Phase 2（T002-T006）**：依赖 T001；T002→T003 串行（测试先行），T004/T005/T006 可并行
- **Phase 3（US1）**：依赖 Phase 2；T007/T008 可并行 → T009（依赖 T008）→ T010/T011 可并行 → T012
- **Phase 4（US2）**：依赖 T010/T011（复用其接线点）
- **Phase 5（US3）**：依赖 T011（合并处）与 T015 依赖 T014
- **Phase 6（US4）**：仅依赖 T004（schema 字段存在），可与 Phase 3-5 并行
- **Phase 7**：依赖全部故事完成

### User Story Dependencies

- US1 是唯一的核心链路（MVP）；US2 在 US1 判定点上补未知分支；US3 在 US1 合并点上补持久化；US4 独立于后端链路（仅需 schema 字段）
- 每个 Checkpoint 处该故事可独立验证

### Parallel Opportunities

- T004/T005/T006（Phase 2 内）；T007/T008（US1 内）；T016 与后端故事并行；前端 spec（T018）与 T022 门禁外任务并行

---

## Implementation Strategy

- **MVP = Phase 1+2+3（US1）**：完成后即具备核心拦截能力，可独立验证
- US2/US3/US4 依次叠加，各自 Checkpoint 可停
- 超标文件纪律：T009/T010 落地后立即 `wc -l` 核对 Line Gate
- 提交策略：T024 单个 feat 提交（含文档/CHANGELOG/dist）；过程不产生中间提交（仓库当前无并行改动）

## Notes

- 基线记录（T001）：后端 2552 用例，3 失败已定性：①README 桌面版节版本停在 v1.7.10——main 既有失败，T023 提版时顺带修复；②卫生检查「未跟踪文件」——基线运行期间实现文件已开工的噪音，提交后消失；③`test_ensure_chrome_ready_replaces_wrong_profile`——环境依赖（仓库路径 × 用户全局账号簿），main 必过、worktree 必败的既有脆弱点，与本功能无关（主仓同 commit 实测 OK）。前端 40 文件 532 用例全绿。
- 测试先行：T002 先于 T003；每任务聚焦测试随实现完成即跑
- 实测值域 fixture 来源：`$TEMP/b081_cards.json`（2026-08-29 实测原始区块，任务开始前转录为仓库测试数据后删除临时文件）
