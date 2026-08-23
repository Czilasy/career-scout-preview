# Tasks: 跨平台岗位去重（BOSS+智联）（019）

**Input**: Design documents from `/specs/019-cross-platform-job-dedup/`

**Prerequisites**: plan.md、spec.md、research.md、data-model.md、contracts/cross-platform-dedupe.md、quickstart.md

**Tests**: 按 spec Verification Scope 与宪法 V（验证门禁），本功能包含测试任务（聚焦测试为交付门禁）。

**Organization**: 按用户故事分组；US1+US2+US5 为 MVP 信任闭环（去重 + 成组可见 + 对账退路），US3/US4 递进。

## File Boundaries

（自 plan.md 解析，任务不得越界）

- **Allowed files**: `webui/app.py`（仅 `_run_ai_screen_task` 与提交入口接线，净增 ≤60 行）、`webui/src/views/DiscoveryView.vue`（仅 `fetchMergedLatestResult` 簇构建）、`webui/src/components/JobWorkspace.vue`（徽标 + 详情成组区）、`webui/src/components/OneClickScreenDialog.vue`（去重开关）、`webui/src/types.ts`（仅簇标记声明对齐）
- **Forbidden files**: `webui/store.py`、`webui/store_migrations.py`、`webui/pipeline_exec.py`、`webui/ai.py`、`webui/result_rounds.py`、`webui/screen_flow.py`、`scripts/**`、`specs/001-018`、`hooks/**`、`packaging/**`
- **New files**: `webui/job_fingerprint.py`（归一化/指纹纯函数）、`webui/cross_platform_dedupe.py`（去重编排服务：判定源收集/拆分/开关旁路/台账数据构建）、`tests/test_job_fingerprint.py`、`tests/test_cross_platform_dedupe.py`
- **Reference direction**: `app.py → cross_platform_dedupe → job_fingerprint`；service 经参数注入 store 只读方法；前端 view → api 数据 → 组件
- **Line gate**: 新 Python 文件 ≤300 行；app.py 净增 ≤60 行；Vue 组件增量合计 ≤150 行

## Verification Gate (task-type aware)

- 功能交付门禁：相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。
- 不为收口任务生成"运行全量后端/前端"清单项，除非 Spec 明确要求（本 spec 的功能交付门禁已含全量）。

## Phase 1 — Setup

无新基础设施（复用现有 Flask/Vue/SQLite 环境），无任务。

## Phase 2 — Foundational（指纹模块，全部故事的前置）

- [X] T001 [P] 新建 `webui/job_fingerprint.py`：实现 `normalize_title`（全半角统一、去空白、小写）、`normalize_city`（location 取市级：分隔符/「市」前段）、`normalize_company`（剥括号注释、组织后缀、当前城市名前缀；全半角/空白/小写）、`fingerprint(job) -> tuple | None`（三元组任一空为 None）、`build_fingerprint_index(jobs) -> dict`（首个出现优先）。口径以 research.md R6 / spec FR-002 为准，纯函数、无外部依赖。
- [X] T002 [P] 新建 `tests/test_job_fingerprint.py`：表驱动覆盖——等价写法（「北京字节跳动科技有限公司/字节跳动」「Python开发/python 开发」「北京·朝阳区/北京/北京-朝阳」）、全半角/大小写/空白、误合反例（标题后缀差异、城市不同、空标题/空公司/城市取不出）、城市前缀剥离仅剥当前城市、组织后缀剥离边界（后缀在末尾才剥）。

**Phase 验证**: `uv run python -m unittest tests.test_job_fingerprint -v` 全绿。

## Phase 3 — US1 后跑平台的重复岗位不再进 AI 筛选 (P1)

- [X] T003 新建 `webui/cross_platform_dedupe.py`：`collect_other_platform_jobs(store, current_platform, profile_summary)`（判定源收集——`list_history_rounds` + 逐轮加载，过滤：可见状态（done/partial/scraped_only）、30 天窗、轮画像摘要（T013 前不含）、轮全部岗位均剔除则跳过；取各轮非剔除岗位，按轮定稿时间从旧到新合并，同岗位多轮取最近轮为追溯目标）、`split_cross_platform_duplicates(raw_jobs, other_jobs) -> DedupeOutcome`（kept_jobs / dropped_entries；dropped 条目含 `job_id`、`verdict="dropped"`、`reason="跨平台重复：已在 <对端平台名> 保留"`、`extra={"cross_platform_dup_of": {platform, platform_job_id, source_url, finished_at}}`；无指纹岗位不参与）、`apply_to_screening_input(store, raw_jobs, current_platform, profile_summary, enabled=True)`（组合入口，开关旁路直通）。契约见 contracts/cross-platform-dedupe.md。
- [X] T004 新建 `tests/test_cross_platform_dedupe.py`：纯逻辑用例——等价岗剔除且 reason/extra 结构正确、无对端不剔除、对端仅剔除行不剔除、同岗位对端多轮取最近轮追溯、无指纹岗保留、开关关闭直通（零剔除零簇数据）。
- [X] T005 修改 `webui/app.py`：① `/api/ai-screen` 读取可选 `cross_platform_dedupe`（缺省 true），随 `create_screening_run` 的 execution_params 冻结，续跑路径沿用冻结值；② `_run_ai_screen_task` 内 `raw_jobs` 规整后调用 `apply_to_screening_input`（按冻结开关），重复岗以 `save_verdict_and_checkpoint_atomic(task_id, "ai_rough", dup_verdicts, ...)` 落判定与断点；③ 剔除数 >0 时 `emit` 进度报数（「N 条中 M 条跨平台重复，跳过 AI 筛选」）与 `append_task_event(task_id, "cross_platform_dedup", 台账载荷)`；④ `_rough_todo` 构造处排除重复岗 job_id；⑤ Stage A 后将重复岗条目并入 `dropped_by_id`（显式赋值，保留 extra 追溯）；⑥ 完成文案数字拆分（抓取/跨平台重复/实际筛选）。`total_scraped`/`raw_jobs` 计数不动（FR-008）。
- [X] T006 扩展 `tests/test_cross_platform_dedupe.py` 集成用例（store 内存库 + 构造任务）：先落多轮 BOSS 可见轮（岗位 X 只在较早轮）→ 以含 X 的智联岗位列表走 `_run_ai_screen_task`（mock AI/抓取）→ 断言：粗筛 AI 调用清单不含 X、`screening_results` 有 X 的 dropped 行且 reason 含「跨平台重复」、ai_rough checkpoint 含 X、`total_scraped` 含 X 而 AI 实际输入不含、进度事件与台账事件各一条且数字互洽、`_rough_todo` 全重复时 Stage A 正常完成（空输入守卫）、超 30 天的轮不参与比对、开关 false 提交时零剔除且行为与现状一致。

**Phase 验证（独立测试）**: `uv run python -m unittest tests.test_cross_platform_dedupe -v` 全绿；构造「两平台等价岗各一条」场景，进入筛选与结果列表的该岗位为 1 条，进度/台账/完成文案三处数字一致。

## Phase 4 — US2 重复岗位成组展示 (P1)

- [X] T007 扩展 `tests/test_cross_platform_dedupe.py`：跨平台剔除行随 `save_finished_round` 落轮后，`load_latest_pipeline_result_for_platform` 读回的 `dropped[]` 条目 `extra.cross_platform_dup_of` 完整（平台/岗位 ID/链接/最近包含轮时间）；契约不变式校验（dup_of.platform ≠ 行 platform）。
- [X] T008 修改 `webui/src/views/DiscoveryView.vue` `fetchMergedLatestResult`：合并后遍历 `dropped[]` 中含 `extra.cross_platform_dup_of` 的条目，按 `(dup_of.platform, dup_of.platform_job_id)` 反查合并 `jobs[]`，命中者挂 `_also_on_copies`（成员字段取自 dropped 行自身：platform/salary/source_url/platform_job_id），未命中静默跳过；旧轮次无该结构不受影响。
- [X] T009 [P] 修改 `webui/src/components/JobWorkspace.vue`：`_also_on_copies` 非空的岗位在列表行渲染「双平台在招」徽标；详情面板新增成组区，并排展示各平台副本（平台名、薪资、岗位链接），薪资如实展示不合并；`webui/src/types.ts` 若需对齐 JobItem 运行时簇标记声明则补充。
- [X] T010 前端验证：`npm run test --prefix webui`（若配置）与 `npm run build --prefix webui` 通过。

**Phase 验证（独立测试）**: 构造两平台 latest 载荷（BOSS 保留条目 + 智联剔除条目指向它），合并后列表一行、徽标可见、详情成组区两副本并排；对端条目缺失时退化为剔除台账可见、无报错。

## Phase 5 — US3 中断续跑后口径一致 (P2)

- [X] T011 扩展 `tests/test_cross_platform_dedupe.py`：智联筛选含跨平台剔除 → 中途暂停 → 续跑 → 断言重复岗不复活（不进 AI 调用）、最终轮 dropped 行 reason/extra 不变、计数自洽、终态校验通过（无 invalid_ai_terminal_status、无幽灵轮，对齐 018 收尾契约）；开关关闭的轮次续跑仍不剔除。
- [X] T012 扩展 `tests/test_cross_platform_dedupe.py`：模拟服务重启恢复路径（抓取快照重建筛选输入后继续）→ 断言剔除按同一规则重放、结果与一次跑完一致（对端轮不变时）、冻结开关沿用。

**Phase 验证（独立测试）**: 同一含重复岗场景一次性跑完与「暂停→续跑」「重启→恢复」三种路径最终轮数据一致。

## Phase 6 — US4 去重判定不跨画像串台 (P2)

- [X] T013 修改 `webui/cross_platform_dedupe.py` `collect_other_platform_jobs`：增加画像摘要过滤——逐轮判断，对端轮 `profile_summary` 与当前任务 `profile_summary` 双非空且不相等 → 跳过该轮；任一为空 → 不过滤（research.md R4）。
- [X] T014 扩展 `tests/test_cross_platform_dedupe.py`：多轮场景下画像过滤逐轮生效（不符轮跳过、相符轮照常）、双非空一致照常剔除、任一为空照常剔除、scraped_only 轮可作判定源、30 天窗内外组合场景。

**Phase 验证（独立测试）**: 画像 A 的 BOSS 轮不会剔画像 B 的智联岗；同画像/无画像场景去重照常。

## Phase 7 — US5 可见性收口与 Polish

- [X] T015 修改 `webui/src/components/OneClickScreenDialog.vue`：增加「跨平台去重」开关（默认开、localStorage 记忆），随一键/筛选提交携带 `cross_platform_dedupe` 字段（contracts §3）。
- [X] T016 全量验证门禁：`uv run python -m unittest discover tests` 全绿、`npm run build --prefix webui` 通过、`uv run python -m unittest tests.test_repo_hygiene` 通过。
- [X] T017 文档核对：检查 `README.md` 中筛选/结果相关说明是否需要补充跨平台去重行为（含开关与成组展示，用户可感知能力变化，按 AGENTS 文档卫生规则）；需要则更新，不需要则记录不更新原因。

## Dependencies

```text
T001/T002（并行） → T003 → T004 → T005 → T006 ─┬→ T011 → T012
                                     T007 → T008 → T009/T010 ─┼→ T013 → T014
                                               └→ T015
全部完成后 → T016 → T017
```

- MVP = US1+US2+US5 的核心链路（T003-T010、T015）：去重生效 + 结果成组可见 + 开关退路。
- US3（T011-T012）、US4（T013-T014）依赖 US1 的服务与接线，两者相互独立；T013 与 T011/T012 同文件需串行提交。
- T008 依赖 T007 的载荷断言先固化契约；T015 可与 US3/US4 并行（不同文件）。

## Parallel Execution Examples

- Phase 2：T001 与 T002 可并行（实现与表驱动测试同步写）。
- Phase 4：T009 可与 T008 并行（不同文件）；T010 收口。
- Phase 7：T015 可与 Phase 5/6 并行（不同文件面）。

## Implementation Strategy

MVP 优先：先交付「去重主链路 + 成组展示 + 可见性对账 + 开关」（T001-T010、T015），构成用户可信任的最小闭环——重复岗不进 AI、结果一行可展开核对、数字三处对账、怀疑时可一键关闭。随后按 US3（一致性证明）→ US4（画像防护）递进，每阶段以聚焦测试独立验收；最后统一跑全量门禁与文档核对。
