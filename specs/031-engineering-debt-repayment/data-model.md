# Data Model: 工程还债——全仓质量整修

**Date**: 2026-08-30 | 本单为重构 spec，无业务数据实体变更；此处的"实体"为**结构性登记表**——实施时按表执行与核对，数据库 schema 零变更（宪法原则 II：不改数据库结构）。

## E1. 共享常量迁居登记表（SharedConstantsRegistry）

批次 3 执行与验收的对照表。每行：符号 | 现址 | 去处 | 兼容 re-export。

| 符号 | 现址 | 去处 | re-export |
|---|---|---|---|
| `_MSG_TASK_NOT_FOUND` | app.py:109 + task_pause_support.py:24（两份，文案漂移） | webui/constants.py（合并为"任务不存在或已被移除"） | app.py 保留 |
| `_OPERATIONAL_ERRORS` | app.py:120 | webui/constants.py | app.py 保留 |
| `_FEEDBACK_ERROR_STATUS` | app.py（行号实施时定位） | webui/constants.py | app.py 保留 |
| `_MSG_ACCOUNT_NOT_FOUND` / `_MSG_UNSUPPORTED_PLATFORM` / `_MSG_EXPERIMENT_NOT_FOUND` / `_MSG_MANIFEST_NOT_FOUND` | app.py | webui/constants.py | app.py 保留 |
| `_public_task_status` | task_status.py:16（唯一定义，app.py re-export） | 原地不动，api 模块改从 task_status.py 导入 | — |
| `LOG_TAIL_LINES` | webui/constants.py:22（已在家） | 原地不动；api 模块改从 constants.py 导入 | — |

**验收规则**：登记表全部行完成后，`grep "from webui.app import" webui scripts`（除 app.py 自身与兼容 re-export 块）= 0；`_MSG_TASK_NOT_FOUND = ` 全仓唯一定义。

## E2. boss 会话态（BossRuntimeState）

`scripts/boss/runtime.py` 持有的状态清单（以门面 `boss_cdp_raw.py:42-44` 现有可变全局为迁移基线，实施时逐项核对）：

- **会话工厂**：requests Session 的创建/复用入口（现经门面全局 `requests`/延迟全局）
- **活动标志**：`_run_active`（现门面模块级全局）
- **共享参数**：超时/重试等跨子模块共享的可变参数

**引用方向**：`scripts/boss/*（20 子模块） → runtime` 单向；runtime 不 import 任何兄弟子模块；`boss_cdp_raw.py` 仅 re-export + CLI 入口。

## E3. 智联拆分映射（ZhilianSplitMap）

| 新模块 | 迁入成员（按现 zhilian_cdp_raw.py 行号） |
|---|---|
| `scripts/zhilian/cdp.py` | _http_json、_find_page、_connect、_send、_evaluate、_navigate、_wait_expression（87-168）、_create/_close_background_tab（610-661） |
| `scripts/zhilian/search.py` | _risk_signal、_normalize_job、_canonical_job_url、_search_fetch_expression、_api_city_code、check_login_state_tri、preflight、fetch_list、_has_empty_marker（169-484、886 前） |
| `scripts/zhilian/detail.py` | _default_sleeper、_reset_detail_session、_detail_tab_worker、scrape_details_batch、fetch_detail、_scrape_detail_on_ws（485-885） |
| `scripts/zhilian/urls.py` | is_zhilian_host、input_hash（886-900） |

**兼容壳**：`scripts/zhilian_cdp_raw.py` 用 `__getattr__` 代理全部旧符号（镜像 boss_cdp_raw.py 021 B8 模式），≤150 行。

## E4. task_runners 拆分映射（RunnerSplitMap）

| 新模块 | 迁入成员（按现 task_runners.py 行号） |
|---|---|
| `webui/task_runner_support.py` | 行 39-277 全部模块级助手：_has_unlock_signal、_classify_scrape_block、_classify_risk_control_reason、_StdoutToLogBuffer、_theme_path、_split_resume_verdicts、_resume_dropped_from_verdicts、_iso_epoch_ms、_optional_positive_int、_env、_read_json、_request_hostname、_task_payload、_mask_key |
| `webui/workbench_runner.py` | WorkbenchRunner 类（570-864） |
| `webui/task_runners.py`（保留） | TaskRunner 类（278-568）+ 兼容 re-export（WorkbenchRunner、全部助手旧名） |

## E5. 吞异常基线（SwallowBaseline）

- **计数口径**：AST `ExceptHandler`，type ∈ {Exception, BaseException, 裸}，body 为单 `ast.Pass`。
- **当前基线**：79 处 / 28 文件（2026-08-30 AST 实测）。**全量工作清单**（B4 逐处处理，本表即验收对照）：

| 文件 | 处数 |
|---|---|
| scripts/zhilian_cdp_raw.py | 9 |
| webui/pipeline_exec_details.py | 8 |
| webui/source_zhilian_cdp.py | 8 |
| webui/pipeline_guard.py | 5 |
| scripts/boss/browser.py | 4 |
| webui/ai_errors.py | 4 |
| webui/ai_screening.py | 4 |
| webui/pipeline_exec_search.py | 4 |
| webui/pipeline_exec_chrome.py | 3 |
| webui/source_boss_cdp_detail.py | 3 |
| scripts/boss/cli.py | 2 |
| scripts/boss/detail_scrape.py | 2 |
| scripts/boss/detail_simulation.py | 2 |
| webui/ai_client.py | 2 |
| webui/ai_raw_log.py | 2 |
| webui/resume_identity.py | 2 |
| webui/source_fake.py（测试替身，白名单候选） | 2 |
| webui/store.py（连接关闭清理类，白名单候选+注释级改动） | 2 |
| webui/task_runners.py | 2 |
| scripts/boss/login.py | 1 |
| scripts/boss/session_import.py | 1 |
| webui/ai_screen_api.py | 1 |
| webui/app.py（:247 前端同步吞异常） | 1 |
| webui/app_support.py | 1 |
| webui/browser_recovery.py | 1 |
| webui/exec_search_api.py | 1 |
| webui/logging_setup.py | 1 |
| webui/process_executor.py | 1 |

- **白名单模式**：`tests/test_repo_hygiene.py` 内 `(file_pattern, reason)` 列表；白名单条目必须与代码内注释一一对应。候选白名单：store.py 关闭清理、source_fake 测试替身，及 B4 实施中判定为纯清理类的条目。
- **状态迁移**：79 →（批次 B4 处理后）≤ 白名单计数 → 只许下降；新增吞噬 = 测试失败。

## E6. 前端依赖契约（DiscoveryDeps）

五域接口 + 聚合（成员清单以 5 个 composable 现有 `deps.X` 解构为准，实施时逐域登记）：

| 接口 | 现经 shared 袋回填的成员（DiscoveryView.vue:269-305） |
|---|---|
| WorkflowDeps | notify、enterSearchStep、enterScreenStep、clearWorkflowState、markResultsPageSeen、persistFinishedState、clearFinishedState |
| ExecutionDeps | startScrape、openOneClickDialog、restoreRunningTask、finishPausedTask、continueAiScreen、cancelScrape、startAiScreen |
| TasksDeps | cancelActiveTasksForNewRound、mergeRecrawlUpdates、pollRecrawl、pollTask、saveScrapedOnlySnapshot、enrichPausedSnapshot、isCompletedTaskStatus |
| ResultsDeps | clearLatestResult、loadLatestResult、setPipelineResult、fetchMergedLatestResult、returnToLatest、restoreLocationsFromContext、jobId |
| SearchDeps | setDraftPlatform、loadCityCatalog、loadFilterLabels、refreshScopePreview、requireProfileConfirmed、validateProfileForScreen、showLoginGuide、isLoginErrorCode |

**验收**：口径为 TS/Vue 源码中 `:\s*any\b` 正则（**排除 CSS**——粗 grep 会把 14 处 `overflow-wrap: anywhere;` 误报成 `: any`，2026-08-30 已核实）。实测基线 **6 行 / 7 个 any**：`deps: any = {}` 5 处签名（useDiscoveryWorkflow:22 / Search:37 / Execution:44 / Tasks:43 / Results:55）+ `useDiscoveryState.ts:66` 未类型化 emit 一行 2 个。全部清零后 `deps: any` = 0、非测试 `: any` = 0、`shared: Record<string, unknown>` = 0。

## E7. 发布配置项（ReleaseGateConfig）

| 配置 | 类型 | 用途 |
|---|---|---|
| `vars.MIRROR_HOST` | GitHub vars | 镜像服务器地址（替换 14 处明文） |
| `vars.MIRROR_USER` | GitHub vars | 部署账号（非 root） |
| `vars.MIRROR_PATH` | GitHub vars | 远端目录（现 /var/www/career-scout） |
| `secrets.MIRROR_SSH_KEY` | 既有 | 部署私钥（不变） |
| `secrets.MIRROR_KNOWN_HOSTS` | GitHub secrets（用户提供） | 固化主机指纹，替换运行时 ssh-keyscan |
| 标签校验规则 | release_check.ps1 | CHANGELOG 首个 `## [x.y.z]` ↔ `refs/tags/vx.y.z` 必须共存；`-SkipTagCheck` 显式豁免并输出提示 |

## E8. 模块地图增量（ModuleMapDelta）

随批次登记（FR-022）：task_runner_support、workbench_runner、boss/runtime、zhilian 四域、maintenance/historical_recovery、discoveryDeps、瘦身抽出组件；变更条目：app.py（常量出仓后职责描述）、pipeline_context.py（B9 后删除"动态门面"描述）、task_status.py（延迟导入清零）、zhilian_cdp_raw.py / boss_cdp_raw.py / task_runners.py（兼容壳定位）。

## E9. 测试打桩迁移工作清单（PatchMigrationMap）

B9 的工作面（2026-08-30 实测：`patch("webui.app...")` 形态共 **61 处 / 12 个测试文件**）：

| 测试文件 | 打桩点 |
|---|---|
| tests/healthy_pipeline/test_pipeline_convergence_pending.py | 18 |
| tests/webui_app/test_webui_app_runtime.py | 17 |
| tests/healthy_pipeline/test_pipeline_semantics.py | 6 |
| tests/webui_app/test_webui_app_taskrun.py | 5 |
| tests/webui_app/test_webui_app_platform.py | 4 |
| tests/test_pipeline_tasks_cleanup.py | 3 |
| tests/healthy_pipeline/test_pipeline_pause_resume.py | 2 |
| tests/webui_app/test_webui_app_core.py | 2 |
| tests/test_e2e_smoke.py | 1 |
| tests/test_env_check.py | 1 |
| tests/healthy_pipeline/test_pipeline_convergence_unified.py | 1 |
| tests/webui_app/test_webui_app_semantics.py | 1 |

每符号一提交（B9 T074-T077）；迁移后 `grep patch.*webui\.app tests` = 0 为验收线。
