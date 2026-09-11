# Tasks: 测试体系低噪音瘦身

**Input**: Design documents from `/specs/040-test-suite-slimming/`

**Prerequisites**: [plan.md](./plan.md)、[spec.md](./spec.md)、[research.md](./research.md)、[data-model.md](./data-model.md)、[quickstart.md](./quickstart.md)、[contracts/batch-verification.md](./contracts/batch-verification.md)、[dead-code-registry.md](./dead-code-registry.md)

**Status**: 已生成（2026-09-11）。按冻结节奏：**本清单完成后为停滞点**——等待用户批准后才开始实施批一；每批完成后再次停止。

## 全局规则（对每条任务都适用）

- **R1 引用复查**：任何删除前，全仓检索确认 0 命中（记录检索式）；有命中不得删。
- **R2 指纹验证**：合并/降级/挪动类改动按 [contracts/batch-verification.md](./contracts/batch-verification.md) 第 3 节执行（改前建指纹 A、改后复验 B，A = B 才通过）。
- **R3 判定不变**：不改变断言语义与失败判定；不做与任务无关的文案微调。
- **R4 粒度**：一处一个小改动，可单独理解、单独回退。
- **R5 发现纪律**：发现产品缺陷只上报（不顺手改）；发现产品死代码只登记进 [dead-code-registry.md](./dead-code-registry.md)（不顺手删）。
- **R6 纪律**：不提交、不推送（等用户指令）；测试日志与临时产物进系统临时目录并当轮清理。
- **R7 行号说明**：下列行号为 2026-09-11 审计时点值；实施时以内容定位，漂移只在批末报告注明。
- **R8 停滞点**：每批末执行"批末收尾"任务（全量验证 + 统计 + 报告）后**停止**，等用户当轮指令再进下一批。

## File Boundaries（引自 plan.md，用户已过目）

- **Allowed files**: `tests/**`；`webui/src/**/__tests__/**`、`webui/src/**/*.spec.ts`、`webui/src/test/**`；`webui/vite.config.ts`（仅批四）；`hooks/pre-commit`（仅批四）；`specs/040-test-suite-slimming/**`；批五（逐项经用户同意后）：`webui/semantic.py`、`webui/src/{discovery,screenFlow,location}.ts` 的孤儿导出及其测试。
- **Forbidden files**: `webui/**`、`scripts/**`、`packaging/**` 全部产品代码（批五清单外）；门面文件（`webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py`、`scripts/zhilian_cdp_raw.py`）；`roadmap/BACKLOG.md`；禁碰红线断言（所在文件可改，断言不得删除/弱化）。
- **New files**: 仅本 Spec 目录内文档；测试代码不新增文件（如确需抽公共助手，当批前单独列出并经用户同意）。
- **Reference direction**: 测试 → 产品单向；产品侧引用零变更。
- **Line gate**: 产品无新增；第 5 批只减。

## Verification Gate

- 每批：受影响子集 + 仓库卫生（`uv run python -m unittest tests.test_repo_hygiene`）。
- 批末：后端全量（`uv run python -m unittest discover -s tests`）+ 前端测试（`cd webui; npm test`）+ 构建（`npm run build`）+ 卫生；统计对比基线（FR-015）。
- 不执行提交/推送，直到用户明确指令。

---

## Phase 1: 批一 · 假保护必修（US1）

**Goal**: 修复"看着在保护、实际保护不到"的用例与守卫；清理过期门禁与永久跳过用例。
**Independent Test**: 每条修复后注入对应故障，用例必须变红（修复前不红/不执行）。

- [x] T001 [US1] 修复 `tests/test_pipeline_guard.py` L245-261 零执行用例：改用真 `WhiteboxService` + 真 store（临时 sqlite），或给 `_FakeStore`（L31-44）补齐白箱链路所需方法使事实真实写入；修复后注入"阻断事实写入"验证该用例变红
- [x] T002 [US1] `tests/webui_app/test_webui_app_runtime.py` L17-107 补隔离：对 `_kernel_check_error`（真实 HTTP 探测）与 `load_browser_accounts`（真读用户目录）打桩；断言不变；验证跑该文件期间无真实端口请求、无用户目录读取
- [x] T003 [US1] `tests/sc015_viewport_check.py` L20-23、L134 更新过期选择器为当前真实元素名（`resume-ai-screen` → `continue-ai-screen`；`.verdict-reason` → 实施时查前端现值）
- [x] T004 [US1] `tests/healthy_pipeline/test_pipeline_state.py` L744-745 同步选择器断言（与 T003 同批完成，保证门禁与测试一致）
- [x] T005 [US1] `tests/test_desktop_shell.py` L530-541 用例传临时 `state_dir`，杜绝写真实 `~/.career-scout/desktop_window.json`；验证运行前后真实目录无变化
- [x] T006 [US1] `tests/healthy_pipeline/test_pipeline_state.py` L781-800 删除两条因 `specs/010-healthy-pipeline-recovery` 不存在而永久跳过的用例（研究决策 5）
- [x] T007 [US1] `webui/src/views/__tests__/DiscoveryScrapeOnly.spec.ts` L180 名实不符用例补"开始 AI 筛选入口"正向断言
- [x] T008 [US1] `tests/test_pipeline_exec_accounts.py` L300-312 三条相同断言的登录态用例改为可判别（夹具使 fallback 与池首不同）
- [x] T009 [US1] `tests/test_desktop_runtime.py` L140-155 补真断言（"只读调用"断言只读语义）；同文件 L196-200 恒真断言（`>=1` 必真）定性处置
- [x] T010 [US1] `tests/test_update_manifest.py` L55-57 Windows 恒跳过：补平台无关等价变体（研究决策 6；确无等价手段则保留跳过并注明，上报）
- [x] T011 [US1] `tests/test_logging_setup.py` L102-116 同上（日志文件删除重建的 Windows 变体）
- [x] T012 [US1] `tests/chrome_setup/test_scraper_contracts.py` L530 恒满足断言（`sum<=12`）改真边界断言；L270 版本硬编码改引生产常量
- [x] T013 [US1] `tests/webui_store/test_store_migrations.py` L210 恒真幂等断言改真断言或删（定性记录）
- [x] T014 [US1] 共享日志目录隔离：`tests/test_logging_setup.py`、`tests/test_logging_whitebox.py`、`tests/chrome_setup/harness.py` 的 `career-scout-test-logs` 改为各自独立 mkdtemp
- [x] T015 [US1] `tests/test_account_round_robin.py` 补全猴子补丁还原（`mark/clear_account_rate_limited` 等；研究决策 7）
- [x] T016 [US1] `webui/src/components/__tests__/BrowserAccountsDialog.spec.ts` L543 消除顺序依赖（`mockImplementationOnce` 撞模块级 session 缓存：自带预热或改稳定桩）
- [x] T017 [US1] `webui/src/__tests__/ApiBuildIdentity.spec.ts` L48 按调用下标取 fetch 改为按请求特征匹配
- [x] T018 [US1] `tests/webui_app/test_webui_app_semantics.py` L432-445 恒真断言（`assertNotEqual(status, 404)`）改真断言
- [x] T019 [US1] `tests/webui_store/test_scrape_only_store.py` L160-167 断言从未被创建的 run：修夹具真创建或改断言
- [x] T020 [US1] `tests/test_desktop_shell_wiring.py` L424 依赖日志中文关键词"事件"改结构化断言（事件名/字段）
- [x] T021 [US1] `webui/src/components/__tests__/App.spec.ts` L679/974/987/1001（`.notice-bar`）与 L1076（`task-completed-toast`）定性处置：前者保留并加说明注释（防误接回的墓碑）；后者（无对应元素）改真断言或删
- [x] T022 [US1] `tests/test_candidate.py` L658-659、`tests/test_execution_config.py` L722-723 将 `unittest.main()` 挪至文件末尾（修复直跑丢例）
- [x] T023 [US1] 批一收尾：受影响子集 + 全量 + 前端 + 构建 + 卫生；统计对比基线；按契约第 4 节写批末报告（含每条注入验证记录、`types.spec.ts` 定性说明）；**停止等用户指令**

> 说明：审计原 §4.6（`webui/semantic.py`）与 §4.7（前端 6 个零引用符号）经用户拍板转批五；本批不触碰。`types.spec.ts` 的处置（价值在 `vue-tsc`/构建）在批二 T036 与批末报告一并说明。

---

## Phase 2: 批二 · 零风险删除（US2）

**Goal**: 删除全仓零引用死件、逐字重复用例、未用导入/死参数/死分支；回收临时资源。
**Independent Test**: 每条删除前引用复查 0 命中；批末全量不变红。

- [ ] T024 [P] [US2] `tests/test_desktop_window_state.py` 删除 L31-131 零引用假替身类（约 100 行）；同步 L9 docstring
- [ ] T025 [P] [US2] `tests/test_workbench_fixtures.py` 删除 L130-157 + L17 零调用符号（`sample_jobs`/`sample_details`/`write_json_file`/`load_fixture`/`FIXTURES_DIR`）；`sample_pdf_bytes(text)` 死参修正
- [ ] T026 [US2] `tests/fixtures/` 删除零引用样本（9 个 JSON + `e2e_resume.txt`）：先复核 `zhilian/` 子目录与 README 的实际引用（R1），全零引用则连同目录与 README 清理；有引用的保留
- [ ] T027 [P] [US2] `tests/test_indexes.py` 删除 L28-34 `_explain()`（零调用）；更新 L1-7 过时 docstring
- [ ] T028 [US2] `tests/chrome_setup/test_scraper_contracts.py` 删除 L913-924（自测 argparse）；删除 L6 死导入；合并 L338/500 两份 setUp；L419-439/811-871 同事实三处并一
- [ ] T029 [P] [US2] `tests/source/test_source_boss.py` 删除 L437 死语句
- [ ] T030 [P] [US2] `tests/test_pipeline_exec_accounts.py` 删除 L33 死导入
- [ ] T031 [P] [US2] `webui/src/views/__tests__/DiscoveryView.spec.ts` 删除 L3779-3785 `confirmProfileFromScreen` 未使用桩（0 调用，R1 复核）
- [ ] T032 [US2] 逐字重复用例合并（14 组，逐组小步，保留覆盖更全侧）：
  - `tests/tuning/test_tuning_funnel.py` L203-213（保 L181-191）
  - `tests/webui_store/test_store_migrations.py` L729-732（并入 L734-738）
  - `tests/webui_store/test_store_domains.py` L1142-1161 ↔ L1335-1356（保更全侧）
  - `tests/webui_store/test_scrape_only_store.py` L76-84 ↔ `tests/test_scrape_only.py` L70-81（保 API 层）
  - `tests/test_account_round_robin.py` L208-209（保 L205）
  - `tests/test_resume.py` L360-367（保 L102-110）
  - `tests/test_process_executor.py` L207-220（保 L101-112）
  - `tests/test_detail_scrape_finalize.py` L123-133（保 L108-121）
  - `tests/test_desktop_shell_wiring.py` L440-444 ↔ `tests/test_desktop_shell.py` L476-483（保组件级）
  - `tests/test_inprocess_execution.py` L168-174（保 L715-718）
  - `tests/ai/test_ai_retry.py` L243-247（保 L267-271）
  - `tests/webui_app/test_webui_app_tuning.py` L1174-1175（连写两遍，删一处）
  - `tests/test_screen_flow.py` L269（保 L267）
  - `tests/source/test_recruiter_activity_capture.py` 两个近乎逐字 `_run` 合并
- [ ] T033 [US2] 后端未用导入/死参数/死分支批量清理：`tests/ai/harness.py` L3/L4、`tests/ai/test_ai_calls.py` L18、`tests/ai/test_ai_match.py` L6/L7/L10、`tests/source/harness.py` L1、`tests/chrome_setup/test_scraper_contracts.py` L6、`tests/source/test_source_zhilian.py` L2/L9-13/L16、`tests/chrome_setup/harness.py` L35-36/L53、`tests/healthy_pipeline/*` 各文件死导入（`json/os/time`）、`tests/test_pipeline_pause_guard.py` L19 及 L47-51/L62-69/L141-143、`tests/test_pipeline_exec_accounts.py` L14、`tests/test_scrape_only.py` L6、`tests/test_logging_mode.py` L15、`tests/webui_app/test_webui_app_platform.py` L11/L94、`tests/test_result_history.py` L10（死参）/L69、`tests/test_updater.py` L297（内层重复导入）
- [ ] T034 [P] [US2] 前端 spec 冗余清理：`webui/src/views/__tests__/DiscoveryView.spec.ts` 的 `noTask` 桩抽公共常量、去冗余 `flushPromises`（逐处复核）
- [ ] T035 [US2] §2.5 资源回收（mkdtemp 不清理→补清理）：`tests/test_browser_registry.py` L217/L409、`tests/test_updater.py` 4 处、`tests/test_desktop_shell_wiring.py` 15 处、`tests/test_desktop_window_state.py` setUp、`tests/webui_app/test_webui_app_runtime.py` L539/L572、`tests/test_pipeline_pause_guard.py` L371、`tests/test_location_scope.py` L193（`artifact_dir="tmp"` 改系统临时目录）
- [ ] T036 [US2] 恒真/伪断言批量处置（前端）：`webui/src/__tests__/errorCodes.spec.ts` L39-42、`listFilter.spec.ts` L107-108、`components/__tests__/BrowserAccountsDialog.spec.ts` L682、`ReminderDrawer.spec.ts` L650/L693-694/L697、`ScreenRoundActions.spec.ts` L46/L163、`TaskContinue.spec.ts` L92-98（`objectContaining({})`）、`composables/__tests__/useScreenRoundFlow.spec.ts` L278-281/L429-431/L485-488 及 L39/L41（死 refs）、`__tests__/discovery.spec.ts` L362/L418-422（恒假分支）、`__tests__/types.spec.ts` L82/88/94（自比较恒过，另在文件头注明其价值在 `npm run build`/`vue-tsc`）——逐处定性：有保护价值的修复为真断言，确无价值的删除
- [ ] T037 [US2] 恒真/伪断言批量处置（后端）：`tests/test_risk_signal_tiers.py` L29-36、`tests/test_whitebox_rules.py` L118-121、`tests/test_ai_prompts.py` L19、`tests/test_pipeline_tasks_cleanup.py` L43、`tests/test_login_state_cache.py` L86、`tests/test_desktop_shell.py` L455-465（与生产同表达式自证）、`tests/test_desktop_runtime.py` L196-200——同上逐处定性
- [ ] T038 [US2] 批二收尾：引用复查记录汇总 + 全量 + 前端 + 构建 + 卫生；统计对比；批末报告；**停止等用户指令**

> 说明：`tests/test_semantic.py` L33-38 不单独处理（随批五整文件删除）。

---

## Phase 3: 批三 · 重复选边（US3）

**Goal**: 约 30 组多文件重复事实收敛到单一正本；每组以故障注入指纹验证保护不变。
**Independent Test**: 每组指纹 A = B；正本侧断言只增不减。

- [ ] T039 [US3] AI 重试/错误码：保 `tests/ai/test_ai_calls.py`（门面 + 原始日志）；`tests/ai/test_ai_retry.py` 收敛至独有项（60s 上限/策略表）；合并 `tests/ai/harness.py` 与 `_mock_chat_response`
- [ ] T040 [US3] profile_facts 纯函数：保 `tests/test_profile_facts.py`；`tests/ai/test_ai_match.py` L1387-1448 收敛
- [ ] T041 [US3] prompt 文案：保 `tests/test_ai_prompts.py`；集成侧（`test_ai_match.py` L1122-1305/L1632-1691）只留"三层注入"指纹
- [ ] T042 [US3] 招聘者活跃 10 天归一：保 `tests/source/test_recruiter_activity_capture.py`（多一层合并链）；`tests/test_recruiter_activity.py` L123-138 收敛
- [ ] T043 [US3] `_classify_failed_code`：保 `tests/source/test_source_boss.py` L1190-1262（最全）；`tests/webui_app/test_webui_app_core.py` L774-829、`tests/test_scrape_block_classification.py`、`tests/test_risk_signal_tiers.py` 收敛为冒烟
- [ ] T044 [US3] 登录空间隔离：保 `tests/test_platforms.py` L833-1005；`tests/chrome_setup/test_scraper_contracts.py` L30-127 收敛
- [ ] T045 [US3] 版本一致性：保 `tests/test_repo_hygiene.py` L126-150；`tests/test_bump_version.py` L35-37 保留一处调用
- [ ] T046 [US3] `fetch_job_details` 契约：保 `tests/healthy_pipeline/test_pipeline_state.py` L351-595（回归最全）；`test_pipeline_guard`/`test_pipeline_pause_guard`/`test_pipeline_tasks_cleanup`/`tests/test_detail_attempts_v4.py` 只留独有断言
- [ ] T047 [US3] 损坏断点→failed：保 `tests/test_task_pause_support.py` L110-226（含 store 层唯一断言）；`healthy_pipeline/test_pipeline_pause_resume.py` L633-696 与 `tests/test_resume_continue.py` L282-342 三处减到两处
- [ ] T048 [US3] 续跑文案：保 `tests/test_resume_continue.py` L522-777；`healthy_pipeline/test_pipeline_pause_resume.py` L2336-2593 收敛
- [ ] T049 [US3] 换号审计/双门槛：保 `tests/test_resume_account_gate.py`；`tests/test_resume_continue.py` L141-158 与 taskrun 集成层各留 1 条
- [ ] T050 [US3] scrape_only 建轮/原地升级：保 `tests/test_scrape_only.py`（API 层）；`test_scrape_only_store.py`、`tests/test_result_rounds.py` L153-242 收敛
- [ ] T051 [US3] 删最新不复活：保 store 层（`tests/webui_store/test_store_domains.py` L69-97）；`tests/test_result_history.py` L134-146 收敛
- [ ] T052 [US3] 发布摘要过滤：保 `tests/test_updater.py` L194-218；`tests/test_release_summary.py` L15-30 收敛
- [ ] T053 [US3] 详情预算 60：`tests/test_workbench.py` L123-135、`tests/test_webui_runner.py` L158、`tests/test_workbench_api.py` L413、`tests/webui_store/test_store_domains.py` L468 各层收 1 条
- [ ] T054 [US3] 窗口控制原语：保 `tests/test_window_controls.py` L53-127；`tests/test_desktop_shell_wiring.py` L446-487 收敛
- [ ] T055 [US3] size_guard：保 `tests/test_desktop_window_state.py` L507-516；`tests/test_desktop_shell_wiring.py` L362-381 收敛
- [ ] T056 [US3] Chrome 路径：`tests/test_env_check.py` L112-129 ↔ `tests/chrome_setup/test_chrome_setup.py` L863-877 二选一
- [ ] T057 [US3] 环境检查四行夹具（逐字相同）：`tests/test_env_check.py` L252-261 ↔ 审计记 `runtime.py` L1826-1836（实施时按内容定位）——合并
- [ ] T058 [US3] 轮询/建 app 三件套：`tests/test_cross_platform_dedupe.py` L239-281/L270-281 ↔ `tests/healthy_pipeline/harness.py` L47-78——合并回 harness
- [ ] T059 [US3] `_make_ai_run`：`tests/test_screen_flow.py` L35-51 ↔ `tests/webui_store/test_store_screen_resume.py` L8-24——合并
- [ ] T060 [US3] TaskProgress 用时/暂停：保 `webui/src/components/__tests__/TaskProgress.spec.ts` L215-231/L233-256（多一条"暂停后推进 30 秒不回流"）；删 `TaskContinue.spec.ts` L527-544/L546-562
- [ ] T061 [US3] 完成/进行中计数派生：保 `TaskProgressB039.spec.ts`（page_done）+ `TaskProgress.spec.ts` L567-585；`TaskContinue.spec.ts` L788-807 / `TaskProgress.spec.ts` L93-108 收敛
- [ ] T062 [US3] 平台徽章：保 `TaskProgress.spec.ts` L872-912；`TaskContinue.spec.ts` L110-179 收敛
- [ ] T063 [US3] 按钮矩阵：组件级（`ScreenRoundActions.spec.ts` L34-79）为主；`DiscoveryView.spec.ts` L4946-4995 集成留 1 条
- [ ] T064 [US3] recrawl 三态：保 `RecrawlContinue.spec.ts` L133-189（带 `job_ids`）；`DiscoveryView.spec.ts` L3650-3657 与 `DiscoveryRecovery.spec.ts` L114-139 收敛
- [ ] T065 [US3] pending 胶囊：单元保 `PendingRecrawlCapsule.spec.ts` L16-47；`DiscoveryView.spec.ts` L5088-5156 只留"切平台/页签不重弹"
- [ ] T066 [US3] 提醒角标文案/99+：保纯函数层（`useReminderBadge.spec.ts` L92-101）；`App.spec.ts` L457-515 收敛
- [ ] T067 [US3] 主题切换：保 `useTheme.spec.ts`；`App.spec.ts` L760-825 留 1 条接线
- [ ] T068 [US3] T505 schema 竞态：保逻辑层（`discovery.spec.ts` L383）；`DiscoveryView.spec.ts` L1467 收敛（视图层注释自认重复）
- [ ] T069 [US3] 批三收尾：逐组指纹记录汇总 + 全量 + 前端 + 构建 + 卫生；统计对比（重点：全量时长不增）；批末报告；**停止等用户指令**

> 每组施工前先建指纹 A（R2）；对不上的组回退并在报告登记"不可合并"。`webui_app_semantics` 双跑消除在批四（T070）。

---

## Phase 4: 批四 · 缠结小修（US4）

**Goal**: 状态还原、真实资源隔离、临时目录、定时器/unmount 清理、构建指纹、钩子路径；只做局部小修。
**Independent Test**: 单独/换序/重复跑一致；真实资源零触碰；改 spec 不触发前端重建。

- [ ] T070 [US4] `tests/webui_app/test_webui_app_semantics.py` 两子类改组合，消除同一批全链路整跑 3 遍（研究决策 9；先建指纹，保留各自独有断言）
- [ ] T071 [US4] `webui/src/test/setup.ts` 测试基建修复：`IntersectionObserver` 假件（0 引用）处置（接线或删除）、`matchMedia` 默认与状态跨用例复位、animate 桩补 `finished`
- [ ] T072 [US4] `webui/vite.config.ts` 构建指纹候选集排除测试文件（研究决策 2）；验证：改一个 spec 文件后 frontend 指纹不变、改源码仍变
- [ ] T073 [US4] `hooks/pre-commit` 移除本机硬编码解释器路径（研究决策 3）；验证：路径 0 命中、卫生检查照常
- [ ] T074 [US4] §5.2 影子实现复核定性（逐条给出"保留/加注释/升级"结论并落注释，不做深度重构）：`tests/tuning/builders.py::_expected_path_digest`、`tests/test_pipeline_job_identity.py::FakeJobStore`、`tests/healthy_pipeline/test_pipeline_pause_resume.py::_record_mock_scrape_completion`、`tests/source/test_source_boss.py` 产物 schema、`tests/source/test_recruiter_activity_capture.py::_exec_config`
- [ ] T075 [US4] 真实外部资源分级处置（§5.8）：`tests/chrome_setup/*` 真子进程 4 处、`tests/test_process_executor.py` 真 taskkill、`tests/test_repo_hygiene.py` 读本地 git——逐条复核边界（不用真实用户目录、不残留进程），必要时 docstring 注明；runtime/桌面壳已在批一修
- [ ] T076 [US4] `tests/test_task_pause_support.py` L336-360 线程竞争 75 轮 → 3-5 轮（竞态窗口来自 Barrier 不来自轮数）；先验证稳定性
- [ ] T077 [US4] `tests/test_detail_scrape_finalize.py` 真睡 5-10s 的 stagger 改显式小值（保持目录生命周期断言语义）
- [ ] T078 [US4] 前端测试 unmount/全局清理：`LocationPicker.spec.ts`（0 unmount + innerHeight 泄漏）、`App.spec.ts`（42 例仅 2 次 unmount）、`DiscoveryView.spec.ts`（117 例仅 21 次 unmount）——补统一 `afterEach` 清理
- [ ] T079 [US4] `tests/test_start_bat.py` 定性：保留既有文案断言（唯一护栏）并在文件头注明"文本断言、行为覆盖有限"；游离脚本 `tests/run_isolated_webui.py`、`tests/sc002_24h_monitor.py` 头部注明"手动资产，不进 CI"
- [ ] T080 [US4] 批四收尾：单独/换序/重复跑验证 + 全量 + 前端 + 构建 + 卫生；统计对比；批末报告（含不做清单确认：巨型文件拆分/CI 重做/包加载改造未动）；**停止等用户指令**

---

## Phase 5: 批五 · 死代码回收（US5）

**Goal**: 按登记册回收产品死代码；清单先过目、逐项同意后删除。
**Independent Test**: 删除后全量 + 前端 + 构建全绿；符号 0 命中。

- [ ] T081 [US5] 整理 `dead-code-registry.md` 为待删清单（含第 1~4 批新登记项），交用户过目并逐项取得同意记录
- [ ] T082 [US5] 按同意结果删除：`webui/semantic.py` + `tests/test_semantic.py`（D001/D002）；`webui/src/discovery.ts` 五个孤儿导出 + `discovery.spec.ts` 对应测试段（D003）；`webui/src/screenFlow.ts` `primaryActionLabel` + spec 段（D004）；`webui/src/location.ts` `locationCombinationCount` + spec 段（D005）——逐项小步
- [ ] T083 [US5] 删除后验证：全量 + 前端 + 构建 + 卫生；被删符号全仓 0 命中复核；构建通过即证明无残留引用（构建失败→立即恢复并上报）
- [ ] T084 [US5] 更新登记册状态（已删除/不同意/剔除及原因）；未同意项保留并注明
- [ ] T085 [US5] 批五收尾：统计对比 + 批末报告；**停止等用户指令**

---

## Final Phase: 收尾（全部批次完成后）

- [ ] T086 全量对账：后端/前端 文件数·行数·用例数·时长 与基线对比；逐条核对 SC-001~SC-008 并给证据摘要
- [ ] T087 更新 [quickstart.md](./quickstart.md) 结果记录区与 [dead-code-registry.md](./dead-code-registry.md) 终态；输出总报告；提交与推送等待用户指令

---

## Dependencies & Execution Order

- 五批严格按序：批一 → 批二 → 批三 → 批四 → 批五（每批末停止，等用户当轮指令）。
- 批内：R1/R2 前置（删除先复查、合并先指纹）先行；批末收尾任务最后。
- 跨批依赖：批五依赖登记册（批一~批四持续登记）；批二的 `types.spec.ts` 定性与批一报告关联；批三的 `webui_app_semantics` 双跑消除在批四实施（先指纹）。
- [P] 标注：同批内不同文件、无前置依赖的条目可并行；同一文件内条目一律串行。

## Implementation Strategy

1. **按批交付、每批停靠**：批一最小闭环（假保护止损）；此后每批独立可回退。
2. 每批 = 小步改动若干 + 批末全量验证 + 报告 + 停止。
3. 任何时刻发现产品缺陷/死代码 → R5 登记，不顺手改。
4. 全程不提交、不推送，直到用户明确指令。

## Notes

- 本清单共 87 项任务（批一 23 / 批二 15 / 批三 31 / 批四 11 / 批五 5 / 收尾 2）。
- 所有位置（文件:行号）为审计时点证据，实施时以内容定位。
- 禁碰红线断言（见 spec.md）在所有任务中不得删除或弱化。
- 三件不做事项（巨型测试文件拆分、CI 流水线重做、测试包加载方式改造）只在本清单与 plan 中登记，不生成任务。
