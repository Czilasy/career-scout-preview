---
description: "Tasks for feature implementation"
---

# Tasks: 工程还债——全仓质量整修

**Input**: Design documents from `/specs/031-engineering-debt-repayment/`

**Prerequisites**: plan.md（必需）、spec.md（必需）、research.md、data-model.md、contracts/、quickstart.md

**Organization**: 按执行顺序排列（冻结顺序），每阶段标注所属用户故事（US 编号对应 spec.md 故事）。执行顺序：US1 → US9 → US2 → US3 → US4 → US5 → US6 → US7 → US8 → 收口。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Story]**: 对应 spec.md 用户故事
- 每条含确切文件路径

## File Boundaries

自 plan.md「File Boundaries」解析而来，逐任务不得越界。

- **Allowed files**: plan.md 允许清单全部路径（文档/配置、CI/发布、主文件松绑 20 文件、错误留证据 ~28 文件、boss 20 子模块、超限拆分目标、事故退场、前端 7 文件、测试文件）
- **Forbidden files**: `webui/src/styles.css`、`webui/dist/**`、`webui/store.py` 与 `store_*.py` 全族、`CHANGELOG.md` 与版本号文件、`packaging/build_exe.ps1`、`webui/updater.py`、`specs/031-*` 之外全部 specs、`roadmap/`、`design/`
- **New files**: 见 plan.md 新文件表（11 个源文件 + server-ops.md 本地操作单）
- **Reference direction**: `api → service → store`；共享常量单向被引用；`boss/* → runtime`；`zhilian/* → cdp.py`；`view → composables → api/client`；测试 → 生产代码；**禁止 import webui.app**（app.py 自身与兼容 re-export 除外）
- **Line gate**: Python ≤800 / Vue ≤1200；兼容壳 zhilian_cdp_raw ≤150、task_runners ≤400、boss_cdp_raw ≤130；DiscoveryView ≤1200

## Verification Gate (task-type aware)

- 每批次收尾（各批最后一个任务）：相关模块聚焦测试 + 后端全量（`uv run python -m unittest discover -s tests`）+ 前端测试与构建（`npm test`、`npm run build`）+ 仓库卫生测试 + `git diff --check`。全过才可提交该批 commit。
- 提交信息 Conventional Commits；推送 GitHub 等用户指令；每批独立提交保证可单独 revert。
- 收口任务（最终关单）不跑额外全量之外的套件，按 quickstart「关单总检」执行。

---

## Phase 1: Setup（开工前置）

**Purpose**: 确认起点可回退、基线可对照

- [ ] T001 确认工作树干净：030-fix-resume-account 工作已收口提交；本单 specs/031 文档同步作为 docs 提交入库（卫生测试不允许未跟踪文件，不先入库会拦住后续每批提交）
- [ ] T002 [P] 基线快照：运行 quickstart「通用门禁」全套并记录结果到系统临时目录对照文件（测试输出纪律：不落仓库根）；复核审查基线数字（79 pass-only / 23 反向 import / 132 回溯 / 4 超限文件）

**Checkpoint**: 起点全绿、基线在案，可开始批次实施

---

## Phase 2: Foundational

无阻断性前置任务——本单为重构，起点即当前主干（021 系列已建立的分层与门面模式直接复用）。

---

## Phase 3: US1 - 文档与暴露面失真清零 (P1)

**Goal**: 文档与事实一致、公开文本无服务器地址、死配置清除、宪法公开、dist 豁免收紧

**Independent Test**: quickstart B1 清单逐条 grep/ls 可复核，不依赖其他故事

- [ ] T003 [P] [US1] README.md 版本陈述修正：标题与"最新正式版"描述一致指向 v1.8.3 事实（FR-001）
- [ ] T004 [P] [US1] AGENTS.md 超大文件小节更新为实测清单（historical_recovery 990 / zhilian_cdp_raw 900 / task_runners 864 / DiscoveryView 1249；移除三个已拆完文件的过时表述）（FR-002）
- [ ] T005 [P] [US1] .gitignore 放行 `.specify/memory/constitution.md` 精确路径，其余 .specify 维持忽略；用 `git check-ignore` 验证规则生效（FR-007）
- [ ] T006 [P] [US1] 删除 sonar-project.properties；pyproject.toml 删除 `[tool.pytest.ini_options]` 残留段与 dev 组 coverage 依赖；`uv lock` 联动 uv.lock（FR-008）
- [ ] T007 [US1] tests/test_repo_hygiene.py：凭据扫描取消 `webui/dist/` 整体豁免（test_no_local_paths_or_credentials_in_tracked_files，行 232 豁免改为对 dist 文本产物启用 sk-/AKIA/PEM 模式）（FR-009）
- [ ] T008 [US1] .github/workflows/release-macos.yml：镜像地址/账号/目录变量化（vars.MIRROR_HOST / vars.MIRROR_USER / vars.MIRROR_PATH），known_hosts 改用 secrets.MIRROR_KNOWN_HOSTS 固化指纹，14 处明文清零；缺配置时显式跳过并醒目提示（FR-005；契约 release-pipeline.md）
- [ ] T009 [US1] scripts/publish_mirror.ps1：同步变量化并支持非 root 部署账号（FR-005/006）
- [ ] T010 [US1] 产出本地操作单 roadmap/server-ops-031.md（roadmap 为本地专用目录、已被 .gitignore，不入 git）：服务器建非 root 部署账号、目录授权、固化 host 指纹、可选换 IP；标注"需要用户提供"项（FR-021）
- [ ] T011 [US1] 验证门禁：quickstart B1 全清单 + 通用门禁全套；通过后提交 commit #1（`docs: 文档失真清零与暴露面收敛（031 B1）`）

**Checkpoint**: 文档可信、暴露面归零、卫生测试含 dist 扫描

---

## Phase 4: US9 - 发布验证闭环补全 (P2)

**Goal**: Windows CI 作业、发布构建前测试门禁、tag 纪律

**Independent Test**: CI 配置推送后 Actions 可观察；标签校验本地两态可验证

- [ ] T012 [US9] .github/workflows/ci.yml 新增 windows-latest 作业：setup-python 3.11 + uv sync + `uv run python -m unittest discover -s tests` + npm ci + `npm test`（运行器自带 Chrome）（FR-023）
- [ ] T013 [US9] .github/workflows/release-macos.yml 新增 build 前测试门禁 job（后端套件），DMG 构建作业 `needs` 该 job（FR-024）
- [ ] T014 [US9] scripts/release_check.ps1 增标签校验：读 CHANGELOG 首个 `## [x.y.z]`，校验 `refs/tags/vx.y.z` 存在；`-SkipTagCheck` 显式豁免并输出提示（FR-025）
- [ ] T015 [US9] 本地验证标签校验两态：构造"最新版本无标签"（临时 checkout 或临时 CHANGELOG 于临时目录演练）→ 失败；有标签 → 通过
- [ ] T016 [US9] 验证门禁：quickstart B2；提交 commit #2（`ci: windows 测试作业与发布验证门禁（031 B2）`）；推送后于 GitHub Actions 观察 Windows 作业（推送需用户指令）

**Checkpoint**: 改坏能被 Windows 平台抓住；版本-标签脱节不再重演

---

## Phase 5: US2 - 主文件松绑，反向依赖清零 (P1)

**Goal**: 23 处反向 import 清零、常量归一、注册方式统一

**Independent Test**: quickstart B3 四项 grep 全达标 + 路由域聚焦测试

- [ ] T017 [US2] webui/constants.py 扩充为共享常量家：迁入 `_MSG_TASK_NOT_FOUND`（合并两份定义为"任务不存在或已被移除"）、`_OPERATIONAL_ERRORS`、`_FEEDBACK_ERROR_STATUS`、`_MSG_ACCOUNT_NOT_FOUND`、`_MSG_UNSUPPORTED_PLATFORM`、`_MSG_EXPERIMENT_NOT_FOUND`、`_MSG_MANIFEST_NOT_FOUND`（data-model E1）（FR-003/010）
- [ ] T018 [US2] webui/app.py 建集中"兼容 re-export 块"引用 constants.py，注释标注"兼容层勿新增"（契约 module-compatibility.md）
- [ ] T019 [P] [US2] import 翻转第一组：webui/app_support.py、core_api.py、ai_screen_api.py（改从 constants.py / task_status.py 导入）
- [ ] T020 [P] [US2] import 翻转第二组：exec_search_api.py、task_continue_api.py、pipeline_jobs_api.py、profiles_api.py
- [ ] T021 [P] [US2] import 翻转第三组：task_state_api.py、results_api.py、browser_support.py、running_task_api.py
- [ ] T022 [P] [US2] import 翻转第四组：settings_api.py、tuning_api.py、resume_fields_api.py
- [ ] T023 [US2] webui/task_status.py：三处延迟 import（行 84/114/170）删除改常量家导入；`_feedback_error_response` 的说明文字恢复为有效 docstring（import 移至其后）（FR-004）
- [ ] T024 [US2] webui/task_pause_support.py：删除副本 `_MSG_TASK_NOT_FOUND` 定义，改 import webui.constants（FR-003）
- [ ] T025 [US2] webui/location_api.py、job_feedback_api.py 蓝图收编为 `register_*_routes(app, ctx)`；app.py 注册方式统一（FR-011）
- [ ] T026 [US2] tests 中断言旧短文案"任务不存在"的用例同步更新为统一文案（测试跟随，不放松断言）
- [ ] T027 [US2] 验证门禁：quickstart B3 四项 grep + 路由域聚焦测试（tests/webui_app/test_webui_app_core 等）+ 后端全量 + 前端 + 构建 + 卫生；通过后提交 commit #3（`refactor: 主文件松绑，反向依赖清零（031 B3）`）
- [ ] T028 [US2] 宪法模块地图登记本批变更（constants.py 职责描述更新、task_status.py 描述更新）（FR-022）

**Checkpoint**: 主文件只剩装配与兼容层；提示语全仓唯一定义

---

## Phase 6: US3 - 出错必有留痕，且不会回潮 (P1)

**Goal**: 79 处吞异常三档处理、统一日志、print 清零、基线防回潮、updater 测试补覆盖

**Independent Test**: quickstart B4（AST 计数 ≤ 基线、白名单附注释、抽查留痕可达）

- [ ] T029 [US3] webui/logging_setup.py 确认子 logger 约定（`get_logger(__name__)`）并在需要处补充说明（D3：复用不新建）
- [ ] T030 [P] [US3] 三档处理第一组（8 文件）：scripts/zhilian_cdp_raw.py（9 处）、webui/pipeline_exec_details.py（8）、webui/source_zhilian_cdp.py（8）、webui/pipeline_guard.py（5）、webui/ai_errors.py（4）、webui/ai_screening.py（4）、webui/pipeline_exec_search.py（4）、scripts/boss/browser.py（4）——每处按"显式返回 / 留痕 / 白名单+注释"三档判定（FR-012）
- [ ] T031 [P] [US3] 三档处理第二组：其余 20 文件——**完整清单以 data-model.md E5 表为准**（含 ai_client、ai_raw_log、app.py:247、browser_recovery、process_executor、source_fake、store.py 白名单候选等）：同上原则逐处处理
- [ ] T032 [US3] 业务模块散装 print 清零：以 237 处调用清单核对，仅保留 scripts/ CLI 入口与 stdout 合理项（ensure_frontend_sync 等），webui 业务模块内清零（FR-013）
- [ ] T033 [US3] tests/test_repo_hygiene.py 新增 AST 基线测试：pass-only 计数 ≤ 基线（基线=处理后的白名单计数）、白名单条目必须与代码注释一一对应、新增吞噬即失败（FR-012）
- [ ] T034 [US3] tests/test_updater.py 新增状态目录环境变量（BOSS_WEBUI_STATE_DIR）真实用例（FR-014）
- [ ] T035 [US3] 验证门禁：quickstart B4（AST 复核、抽查 3 处留痕、print 清零）+ 通用门禁全套；通过后提交 commit #4（`fix: 静默吞异常三档治理与日志统一（031 B4）`）
- [ ] T036 [US3] 宪法模块地图更新 logging_setup 条目描述（基线执法职责登记）

**Checkpoint**: 错误可查、回潮有闸——为后续拆分批次提供保险丝

---

## Phase 7: US4 - 爬虫模块自己管自己 (P2)

**Goal**: 132 处门面回溯清零，boss 包独立

**Independent Test**: quickstart B5（回溯 grep=0、独立 import 成功、boss 聚焦测试全绿）

- [ ] T037 [US4] 新建 scripts/boss/runtime.py：会话工厂、`_run_active` 活动标志、共享超时/重试参数（data-model E2；现 boss_cdp_raw.py:42-44 全局为迁移基线）（FR-015）
- [ ] T038 [P] [US4] 回溯替换第一组：search.py、detail_parse.py、detail_scrape.py、detail_analyze.py、programmatic.py
- [ ] T039 [P] [US4] 回溯替换第二组：browser.py、login.py、cdp_session.py、rate_limit.py、session_import.py
- [ ] T040 [P] [US4] 回溯替换第三组：output.py、smoke.py、cli.py、city_map.py、constants.py、exceptions.py 及其余子模块
- [ ] T041 [US4] scripts/boss_cdp_raw.py 收敛为纯兼容壳：re-export + CLI 入口（≤130 行不变更职责）；tests 中 patch boss_cdp_raw 模块全局（websocket/requests/_run_active）的用例同步迁移为 patch `scripts.boss.runtime` 对应成员（会话态搬家后旧 patch 点不再生效，必须同步，否则聚焦测试假绿）（FR-015）
- [ ] T042 [US4] 验证门禁：quickstart B5 + tests/source/test_source_boss.py 聚焦 + 通用门禁全套；通过后提交 commit #5（`refactor: boss 包会话态自持，门面回溯清零（031 B5）`）
- [ ] T043 [US4] 宪法模块地图登记 scripts/boss/runtime.py

**Checkpoint**: boss 包可独立理解与测试

---

## Phase 8: US5 - 宪法红线归位（后端超限拆分）(P2)

**Goal**: zhilian 900→≤150 壳+四域模块；task_runners 864→≤400+两新模块；只搬代码

**Independent Test**: quickstart B6（行数达标、旧路径可用、聚焦测试全绿）

- [ ] T044 [US5] 新建 scripts/zhilian/cdp.py：CDP 原语（_http_json、_find_page、_connect、_send、_evaluate、_navigate、_wait_expression、_create/_close_background_tab）（data-model E3）
- [ ] T045 [US5] 新建 scripts/zhilian/search.py：登录探测/preflight/fetch_list/风险信号/岗位归一（check_login_state_tri、preflight、fetch_list、_has_empty_marker、_risk_signal、_normalize_job、_canonical_job_url、_search_fetch_expression、_api_city_code）
- [ ] T046 [US5] 新建 scripts/zhilian/detail.py：批量详情（_default_sleeper、_reset_detail_session、_detail_tab_worker、scrape_details_batch、fetch_detail、_scrape_detail_on_ws）
- [ ] T047 [P] [US5] 新建 scripts/zhilian/urls.py：is_zhilian_host、input_hash
- [ ] T048 [US5] scripts/zhilian_cdp_raw.py 改 `__getattr__` 兼容壳（≤150 行，镜像 boss_cdp_raw 模式）（FR-016）
- [ ] T049 [P] [US5] 消费方 import 翻转：webui/source_zhilian_cdp.py、webui/source_zhilian_defaults.py、webui/app.py 中 zhilian 引用改新模块；scripts/zhilian/__init__.py 包导出更新（ensure_frontend_sync.py 中出现 zhilian 字样仅为构建哈希文件清单，无 import，不涉及）
- [ ] T050 [US5] 新建 webui/task_runner_support.py：task_runners.py 行 39-277 全部助手迁入（_StdoutToLogBuffer、_classify_*、_theme_path、_split_resume_verdicts 等 14 项）（data-model E4）
- [ ] T051 [US5] 新建 webui/workbench_runner.py：WorkbenchRunner 类（570-864）迁入
- [ ] T052 [US5] webui/task_runners.py 收敛为 TaskRunner 核心 + 兼容 re-export（≤400 行）（FR-016）
- [ ] T053 [US5] 验证门禁：quickstart B6 + tests/source/test_source_zhilian.py、tests/webui_app/test_webui_app_taskrun.py、tests/healthy_pipeline/ 聚焦 + 通用门禁全套；通过后提交 commit #6（`refactor: zhilian 与 task_runners 按域拆分至宪法红线内（031 B6）`）
- [ ] T054 [US5] 宪法模块地图登记 6 个新模块与两个兼容壳新定位（FR-022）

**Checkpoint**: 后端红线归位；旧 import 全部兼容

---

## Phase 9: US6 - 事故代码退出生产前门 (P2)

**Goal**: 990 行迁手动工具、3 接口撤除

**Independent Test**: quickstart B7（路由 404、CLI 预演等价、测试全绿）

- [ ] T055 [US6] 新建 scripts/maintenance/__init__.py；webui/historical_recovery.py 整体迁入 scripts/maintenance/historical_recovery.py，加 argparse 外壳：preview/prepare/execute 三子命令 + `--confirm` 破坏性操作安全栏（契约 recovery-cli.md）（FR-017）
- [ ] T056 [US6] webui/task_state_api.py 删除 3 条 /api/recovery/* 路由及相关 import（FR-017；契约 http-api-delta.md）
- [ ] T057 [US6] 删除 webui/historical_recovery.py；recovery 测试改造：tests/webui_app 中 recovery 用例迁为 tests/maintenance/test_historical_recovery.py（直接调工具层；含 --confirm 缺失拒绝写库用例）
- [ ] T058 [US6] 验证门禁：quickstart B7（GET /api/recovery/preview/x → 404、CLI 预演输出等价）+ 通用门禁全套；通过后提交 commit #7（`refactor: 事故恢复能力迁出生产 API 面为手动工具（031 B7）`）
- [ ] T059 [US6] 宪法模块地图：登记 maintenance 工具、移除 webui/historical_recovery 条目（FR-022）

**Checkpoint**: 生产 API 面无恢复入口；能力手动可复用

---

## Phase 10: US7 - 前端理线：类型闭环、样式冻结 (P2)

**Goal**: deps 类型化清零 any、DiscoveryView ≤1200、逻辑问题当场修、样式像素级不变

**Independent Test**: quickstart B8（any=0、行数达标、样式 diff 为空、533 用例全绿）

- [ ] T060 [US7] 新建 webui/src/composables/discoveryDeps.ts：五域接口 + `DiscoveryDeps` 聚合 + `wireDiscoveryDeps`（成员以 5 个 composable 现有 deps 解构为准登记，契约 discovery-deps.md；data-model E6）（FR-018）
- [ ] T061 [US7] useDiscoverySearch.ts 类型化打样：`deps: any = {}` 签名改 SearchDeps（契约成员落实）
- [ ] T062 [P] [US7] useDiscoveryWorkflow.ts 类型化（WorkflowDeps）
- [ ] T063 [P] [US7] useDiscoveryExecution.ts 类型化（ExecutionDeps）
- [ ] T064 [P] [US7] useDiscoveryTasks.ts 类型化（TasksDeps）
- [ ] T065 [P] [US7] useDiscoveryResults.ts 类型化（ResultsDeps）
- [ ] T066 [US7] webui/src/views/DiscoveryView.vue：`shared: Record<string, unknown>` 袋替换为 `wireDiscoveryDeps` 显式接线（37 处回填清零）；非测试 `: any` 清零（口径与基线见 data-model E6：6 行 7 个，含 useDiscoveryState.ts:66 未类型化 emit；CSS 的 `overflow-wrap: anywhere` 非目标）（FR-018）
- [ ] T067 [US7] DiscoveryView.vue 瘦身：抽出 1 个高内聚区块为 webui/src/components/ 新组件（实施时按耦合度选定，候选：历史轮次区/结果标签区；≤1200 行达标）（FR-016）
- [ ] T068 [US7] 测试 fake deps 类型化同步（webui/src/views/__tests__/DiscoveryView.spec.ts 等）；断言只随事实迁移不放松
- [ ] T069 [US7] 样式冻结核对：`git diff` 构建产物无样式规则变化；渲染像素级一致（FR-019）
- [ ] T070 [US7] 理线中发现的逻辑问题：逐条单独提交修复（冻结规矩：当场修，不与搬移混合；本任务为登记点，实际每处一 commit）
- [ ] T071 [US7] 验证门禁：quickstart B8 + npm test 全绿 + npm run build + 卫生测试；通过后提交 commit（`refactor: 前端依赖类型闭环与页面瘦身（031 B8）`）
- [ ] T072 [US7] 宪法模块地图登记 discoveryDeps.ts 与新组件（FR-022）

**Checkpoint**: 类型检查覆盖全部 composable 边界；样式零变化

---

## Phase 11: US8 - 拆掉"隔空改主文件"的测试后门 (P3)

**Goal**: patch("webui.app.X") 清零、动态门面移除、8 符号显式注入

**Independent Test**: quickstart B9（patch 形态=0、__getattr__=0、全量全绿）

- [x] T073 [US8] 规划核对：_PATCHABLE_APP_SYMBOLS 8 符号（boss、_BossCdpSource、ai_service、ScraperExecutor、threading、uuid、os、_theme_path）的测试使用点全量清单（**61 处 / 12 文件，见 data-model E9**）；`_theme_path` 出仓 webui/task_runner_support.py 既有定义对齐（data-model E4）
- [x] T074 [US8] 符号迁移：boss 与 _BossCdpSource → PipelineContext 构造期注入（make_cdp_source 既有工厂路径优先）；tests 中相应 patch 改注入 fake / patch.object(ctx)；独立提交
- [x] T075 [US8] 符号迁移：ai_service 与 ScraperExecutor → ctx 注入；tests 同步；独立提交
- [x] T076 [US8] 符号迁移：threading、uuid、os → ctx 注入（时钟/随机源注入点）；tests 同步；独立提交
- [x] T077 [US8] 符号迁移：_theme_path → ctx 注入；tests 同步；独立提交
- [x] T078 [US8] webui/pipeline_context.py 删除 `__getattr__` 动态门面与 `_PATCHABLE_APP_SYMBOLS`；webui/app_support.py 组装处同步；webui/app.py 相关模块级符号清理（FR-020）
- [x] T079 [US8] 验证门禁：quickstart B9 + 后端全量 + 前端 + 构建 + 卫生；通过后提交 commit（`test: 猴补丁后门拆除，可替换符号显式注入（031 B9）`）
- [x] T080 [US8] 宪法模块地图更新 pipeline_context.py 条目（移除"动态门面"表述）（FR-022）

**Checkpoint**: 生产代码不再为测试变形；松绑收尾完成

---

## Phase 12: Polish & 关单收口

**Purpose**: 全单总检与文档收尾

- [x] T081 关单总检：quickstart「关单总检」四项全过（B1-B9 清单、三道保险复核含 styles.css diff 为空、宪法红线全仓 wc -l 复核、每批 revert 可行性抽查）
- [x] T082 [P] spec.md 状态 Draft → 可交付；checklists/requirements.md 复核勾选；本单 quickstart/research 与实施事实不一致处回写更正
- [x] T083 [P] 向用户交付 roadmap/server-ops-031.md 操作单并核对"需用户提供"项（服务器命令由用户执行）（FR-021）
- [x] T084 关单汇报：9 批次对照冻结清单逐项"完成证据"（SC-001~009 对照）；推送 GitHub 与后续发布由用户指令触发

---

## Dependencies & Execution Order

### 批次依赖（冻结顺序）

```text
T001 工作树干净（030 收口）
  → US1（B1 独立）
  → US9（B2 独立，观察 CI 需推送）
  → US2（B3 主文件松绑）
      → US3（B4 错误留证据——基线护栏必须在拆分批前就位）
          → US4（B5 爬虫）
          → US5（B6 后端超限拆分）
              → US6（B7 事故退场）
      → US7（B8 前端理线——仅依赖 US2 完成后端不阻塞前端；可与 US4-US6 并行推进）
  → US8（B9 打桩收尾——依赖 US2 注入前提 + 全部批次的 patch 面稳定，故最后）
  → Polish 关单
```

### 关键依赖说明

- US3（基线护栏）必须先于 US4/US5/US6 交付：拆分类批次期间任何新吞噬立即被测试抓住。
- US8 依赖 US2（常量出仓后 patch 面才可迁移）与全部批次完成（patch 点不再漂移）。
- US7 前端与后端批次无耦合，可并行；样式冻结核对依赖其自身构建。
- 每批验证门禁失败 → 修复后重跑，禁止带病提交。

### 并行机会

- T019-T022（四组 import 翻转）、T038-T040（三组回溯替换）、T044/T047、T062-T065（四 composable 类型化）均 [P] 可并行。
- 单人执行时按序即可；[P] 标记保留给多代理/多会话场景。

## Implementation Strategy

- **逐批交付**：每批 = 独立可验证增量 + 独立 commit；任一批后停下全仓仍全绿（三道保险不破）。
- **先护栏后拆房**：B4 基线先立，B5-B7 大搬移受其保护。
- **测试跟随不放松**：断言只随事实迁移；聚焦测试失败优先怀疑搬移引入回归，而非改断言。
- **新问题当场修**：实施中挖出的清单外问题当场修进本单并在对应批次登记，不开新 spec（冻结规矩）。

## Notes

- 全程不碰 `webui/src/styles.css` 与版本号文件；不碰 store 族。
- 提交 push 由用户指令；批次 2 的 CI 观察在用户推送后于 GitHub 侧完成。
- 测试输出一律落系统临时目录，不进仓库根（测试输出纪律）。
- MVP 即 US1 单批：仅文档与暴露面修复即可独立交付价值。
