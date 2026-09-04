<!--
  Sync Impact Report
  Version: 1.2.0 → 1.3.0
  Added: 原则 VII「错误处理与可观测性」——禁止新增纯 pass 吞异常、宽异常必须留痕或显式返回、日志统一配置、pass-only 基线只降不升（卫生测试强制）
  Modified: 原则 I 措辞「蓝图注册」→「路由注册」（031 统一路由注册方式，移除蓝图差异）
  Templates: 无需变更（plan/tasks 模板 File Boundaries 章节已兼容）
  Follow-up: 031 各批次落地时更新「模块地图」小节（常量出仓、boss runtime、zhilian/task_runners 拆分、historical_recovery 迁出、pipeline_context patch 面迁移）
-->

<!--
  Sync Impact Report（历史）
  Version: 1.1.0 → 1.2.0
  Added: 原则 VI「模块地图与落位规则」——新功能按域落位、75% 预警线分流、门面禁改（拆分 Spec 批次豁免）、地图随批次登记
  Templates: 无需变更（File Boundaries 章节已兼容落位规则）
  Follow-up: 021 拆分各批次落地时更新本文件「模块地图」小节
-->

<!--
  Sync Impact Report（历史）
  Version: 0 → 1.1.0
  Added: 职责分层、单文件尺寸边界、拆分迁移纪律、引用方向、验证门禁；验证门禁限定功能/重构交付，收口任务按根 AGENTS 执行
  Templates: plan-template.md / tasks-template.md 已同步增加 File Boundaries
-->

# Career Scout Preview Constitution

## Core Principles

### I. 职责分层

每个代码文件只承担一类职责。路由/API 层只负责路由、参数校验和响应组装；业务逻辑放在 service 层；数据访问按业务域拆到 store 模块。`webui/app.py` 只保留应用入口与路由注册，不得继续积累业务实现；共享常量与纯函数必须落在独立模块，禁止以 app.py 为全局命名空间。现有超大文件必须通过专门的重构 Spec 拆分，普通功能不得向其中追加新逻辑。

### II. 单文件尺寸边界

Python 业务文件不超过 800 行，Vue 单文件组件不超过 1200 行。超过上限的文件必须拆分为职责内聚的模块，并保留原入口兼容导出。拆文件只搬代码、不改行为、不改接口、不改数据库结构；每批拆分必须完成聚焦测试、后端全量测试、前端测试与构建验证。

### III. 引用方向

依赖必须单向向下：`api → service → store`；`store` 不得反向 import `api/app`。前端按 `view → composables → api/client` 组织，view 不得反向依赖 composables 内部实现。跨模块引用只允许通过公开接口，禁止通过私有符号直接耦合。

### IV. 拆分与重构纪律

重构/拆分必须单独建立 Spec 与 Plan，不得混入功能开发。任务必须写明允许修改、禁止修改、新增文件和引用方向。旧文件保留为兼容入口，行为变化必须由失败测试先行定义。

### V. 验证门禁

- 适用范围：功能开发、重构、拆分等 Spec Kit 交付批次。
- 每个功能或拆分批次交付前必须通过：相关模块聚焦测试、后端全量测试、前端测试、`npm run build`、仓库卫生检查。无客观证据不得宣称完成。
- 版本提升、打包、提交、推送、Release 等收口任务不适用本门禁，按根目录 `AGENTS.md`「收口任务验证与命令边界」执行，默认不跑全量测试。

### VI. 模块地图与落位规则

- 新增或修改功能前 MUST 先查阅本文件「模块地图」小节，按业务域落位到对应模块；属于既有域的功能 MUST 落入该域模块，禁止落入门面文件。
- 找不到对应域（全新领域）时才允许开新文件；新文件 MUST 在同一批次内登记进「模块地图」（路径 + 一句话职责）。
- 预警线：Python 文件达到 600 行、Vue 文件达到 900 行（红线 75%）时，后续改动 MUST 开新模块分流，不得继续增长至红线。
- 门面文件（`webui/app.py`、`webui/store.py`、`webui/source.py`、`scripts/boss_cdp_raw.py` 等兼容入口）只允许 re-export 与组装，MUST NOT 追加任何新逻辑。唯一例外：专门立项的拆分重构 Spec（符合原则 IV）可在其批次内修改门面文件的结构；豁免不延伸到拆分 Spec 之外。

#### 模块地图

- `webui/source.py` — source 域门面：re-export 全部既有符号，保持旧 import 与 patch 面（021 B1）
- `webui/source_breaker.py` — source 域公共契约：SourceOutcome / SourceCircuitBreaker / JobSource Protocol / PageEventPersistenceError（021 B1）
- `webui/source_boss_helpers.py` — BOSS source 共享助手：失败分类、字段归一化、登录事实回写、脱敏日志、SCRAPER_FILTER_FIELDS（021 B1）
- `webui/source_boss_cdp.py` — BossCdpSource 主体：preflight / recheck_login / fetch_list / fetch_detail / CLI 命令构建（021 B1）
- `webui/source_boss_cdp_detail.py` — BossCdpSource 的 detail mixin：批量详情、终端事件校验、in-process 翻译执行、产物读取（021 B1）
- `webui/source_boss_detail_events.py` — BOSS 详情事件归类纯助手：非零退出时按事件文件真实 safe_code 逐岗位归类，区分账号级阻断与单条软失败（034 拆分，纯函数）
- `webui/source_zhilian_cdp.py` — ZhilianCdpSource 主体与智联 signal 映射/输入校验助手（021 B1）
- `webui/source_zhilian_defaults.py` — 智联默认 CLI runner 与 failed_code → 用户可读原因映射（021 B1）
- `webui/source_fake.py` — FakeJobSource 内存测试替身（021 B1）
- `webui/store.py` — store 域门面：TaskStore = 核心（连接/迁移引导）+ 域 mixin 组装 + re-export（021 B2）
- `webui/store_constants.py` — store 域共享常量与 DiscoveryStoreConflictError 契约异常（021 B2）
- `webui/store_config.py` — 高级配置与模式版本域 mixin（021 B2）
- `webui/store_recovery.py` — 恢复锁域 mixin（021 B2）
- `webui/store_pipeline_results.py` — 流水线结果持久化域 mixin（021 B2）
- `webui/store_jobs.py` — 岗位 upsert 与快照域 mixin（021 B2）
- `webui/store_tasks.py` — legacy 任务与日志域 mixin（021 B2）
- `webui/store_profiles.py` — 候选人档案/简历/AI 设置域 mixin（021 B2）
- `webui/store_runs.py` — 搜索/筛选 run 生命周期与 verdict 域 mixin（021 B2）
- `webui/store_scrape_runs.py` — 抓取进度/待确认岗位/检查点域 mixin（021 B2）
- `webui/store_job_catalog.py` — 岗位目录/反馈/偏好/过期清理域 mixin（021 B2）
- `webui/store_tuning_experiments.py` — 调优实验状态机域 mixin（021 B2）
- `webui/store_tuning_rounds.py` — 调优轮次/租约域 mixin（021 B2）
- `webui/store_tuning_reports.py` — 调优测量/质量参照/任务单与执行者报告域 mixin（021 B2）
- `webui/pipeline_context.py` — create_app 共享运行态载体 PipelineContext；原 __getattr__ 动态门面已拆除，source_class / theme_path 构造期显式注入，其余可替换符号由各消费方模块级直连（021 B3，031 B9 更新）
- `webui/runners/` — 后台任务 runner 包（021 B4-B6）：tuning_manifest.py（调优任务单子进程）、recrawl_task.py（批量重抓）、pipeline_task.py（列表抓取）、ai_screen_task.py（AI 筛选编排）+ ai_screen_rough/jd/fine.py 三段模块（task 单向 import 段模块，段间不互相 import）
- `webui/task_status.py` — 任务状态口径与共享纯助手（公共状态映射、run scope 解析、暂停配置刷新、legacy PATCH 连接代理、进度权重）；常量与错误元组自 webui.constants 导入，反向依赖清零（021 B6，031 B3 更新）
- `webui/constants.py` — 共享常量家：数值常量、消息文案、可恢复错误元组、反馈状态表、路径锚点（031 B3 起为跨层唯一来源）
- `webui/app_support.py` — create_app 管线支撑工厂 build_app_support：任务表/锁/执行器、调优 runner、声明与租约、终态写入、清理定时、账号激活、续跑断言、PipelineContext 组装；threading / ai_service 模块级直连（021 B6，031 B9 更新）
- `webui/browser_support.py` — 浏览器锁共享助手工厂（活动任务锁口径、暂停 run 浏览器关闭、账号投影）（021 B6）
- `scripts/boss/runtime.py` — boss 包会话态持有：requests/websocket 依赖注入、require_runtime_dependencies、_run_active 活动标志（set_run_active 带门面镜像同步）；子模块单向引用 runtime，禁止回溯门面（031 B5 扩充收口）
- `webui/logging_setup.py` — 日志统一配置：career_scout 旋转文件 + 凭据脱敏 + 任务上下文；子 logger 约定 `get_logger(__name__)`；pass-only 吞噬基线执法对象（031 B4）
- `webui/*_api.py` 路由域模块（021 B6 T019，register_*(app, ctx) 模式）：version_update_api（版本/更新/主题）、tuning_api（调优实验/manifest/decision）、settings_api（AI/高级设置/浏览器账号）、pipeline_jobs_api（岗位操作/批量重抓）、results_api（结果/进度/导出）、running_task_api（最新运行任务快照）、resume_fields_api（简历解析/字段确认）、exec_search_api（搜索执行/续跑/取消）、ai_screen_api（筛选提交/取消）、core_api（入口/静态/平台/选项/任务查询）、profiles_api（画像/搜索运行/岗位反馈）、task_state_api（状态/诊断/恢复预览）、task_continue_api（续跑/暂停/取消/结束）
- `webui/app.py` — 薄装配门面（021 B6 后 ≤800 行）：入口 + 配置 + ctx（经 build_app_support）+ runner 包装 + 路由注册 + re-export
- `webui/tuning.py` — 调优域门面：TuningController = 五域 mixin MRO 组装 + re-export（021 B7 T021）
- `webui/tuning_digest.py` — 调优摘要工具：稳定 SHA-256 文件/目录摘要（021 B7 T021）
- `webui/tuning_events.py` — 调优测量事件白名单与 MeasurementSink（021 B7 T021）
- `webui/tuning_experiments.py` — 调优实验生命周期与租约协调 mixin（021 B7 T021）
- `webui/tuning_rounds.py` — 调优轮次管理、阶段产物证明与分阶段轮次适配 mixin（021 B7 T021）
- `webui/tuning_quality.py` — 调优测量事件聚合与质量参照比较 mixin（021 B7 T021）
- `webui/tuning_manifests.py` — 调优任务单签发、执行与报告校验渲染 mixin（021 B7 T021）
- `webui/tuning_candidates.py` — 调优候选提案、收敛判定与硬停止/受控重试 mixin（021 B7 T021）
- `webui/ai.py` — AI 域门面：re-export 全部既有符号，patch 敏感符号经 _facade 动态取用（021 B7 T022）
- `webui/ai_errors.py` — AI 错误分类与测量遥测事件（021 B7 T022）
- `webui/ai_schannel.py` — Windows schannel curl POST 适配（021 B7 T022）
- `webui/ai_client.py` — AI 传输层：URL 构建、JSON POST、密钥环、连通性（021 B7 T022）
- `webui/ai_filters.py` — AI 筛选条件构建与确认不匹配判定助手（021 B7 T022）
- `webui/ai_screening.py` — AI 粗筛 screen_jobs 与 JD 精筛 match_jds（021 B7 T022）
- `webui/ai_resume.py` — AI 简历解析、统一字段校验与偏好更新（021 B7 T022）
- `webui/pipeline_exec.py` — pipeline 执行域门面：re-export 全部既有符号，CDP 活动目录经门面镜像同步（021 B7 T023）
- `webui/resume_identity.py` — 续跑身份域：冻结身份解析/持久化、账号快照、双门槛自动换号判定、换号留痕、角色感知兜底、父身份继承（030）；038 B091 in-flight 撞墙换号留痕由 account_round_robin 限流标记承担
- `webui/pipeline_exec_settings.py` — 高级设置读写（021 B7 T023）
- `webui/pipeline_exec_accounts.py` — 浏览器账号簿与 CDP 数据目录；Spec 038 B091 账号池配置 schema（pool 多选 + 配额 + 限流标记）+ 默认零配置 + 限流持久化 helper（021 B7 T023 / 038 B091 T003/T014）
- `webui/account_round_robin.py` — Spec 038 B091 多账号轮询分摊调度域：纯调度（RotationQueue/plan_round_robin）、IO 编排（ListRobin/DetailRobin/clone_source/_switch_browser_account）、撞墙换号接力、engagement 规则保护既有替身、限流持久化 best-effort（038 B091 T001）+ 白箱 seam 接线（038 B091 V2）
- `webui/account_round_robin_observability.py` — Spec 038 B091 V2 轮询白箱安全摘要适配器：账号池快照、分配段、正常/撞墙切换、失败不完整标记，复用 `task_logs`，不记录凭据或岗位正文
- `webui/pipeline_exec_status.py` — 失败码口径、taxonomy 理由与抓取进度权重（021 B7 T023）
- `webui/pipeline_exec_chrome.py` — 调试浏览器生命周期：就绪检查与关闭（021 B7 T023）
- `webui/pipeline_exec_filters.py` — 搜索参数展开与本地岗位过滤匹配（021 B7 T023）
- `webui/pipeline_exec_search.py` — run_search：关键词×城市组合抓取主流程（021 B7 T023）
- `webui/pipeline_exec_details.py` — fetch_job_details：批量详情抓取（021 B7 T023）
- `webui/pipeline_exec_artifacts.py` — 组合产物检查点与冻结清单（021 B7 T023）
- `webui/pipeline_exec_tuning.py` — 调优轮次执行器 TuningRoundRunner（021 B7 T023）
- `webui/platforms.py` — 平台注册域门面：re-export 全部既有符号，_REGISTRY 经 _facade 动态取用（021 B7 T023）
- `webui/platforms_schema.py` — 平台常量、异常族与注册项/筛选 schema 数据类（021 B7 T023）
- `webui/platforms_urls.py` — 平台站内链接规范化与登录空间解析（021 B7 T023）
- `webui/platforms_registry.py` — 平台注册表存取（021 B7 T023）
- `webui/platforms_checks.py` — 平台能力检查：URL/登录空间派发、fixture 完整性（021 B7 T023）
- `webui/platforms_filters.py` — 平台筛选 schema 投影与取值校验（021 B7 T023）
- `webui/platforms_boss.py` — BOSS 平台注册：schema/城市目录构建与运行时初始化（021 B7 T023）
- `webui/platforms_zhilian.py` — 智联平台注册：冻结 schema/城市目录（021 B7 T023）
- `webui/store_migrations.py` — 迁移域门面：StoreMigrationsMixin = v1..v4 MRO 组装 + re-export（021 B7 T024）
- `webui/store_migrations_v1.py` — 迁移 001-008 与调度/公共助手（021 B7 T024）
- `webui/store_migrations_v2.py` — 迁移 009-016（021 B7 T024）
- `webui/store_migrations_v3.py` — 迁移 017-024（021 B7 T024）
- `webui/store_migrations_v4.py` — 迁移 025-032（021 B7 T024）
- `scripts/boss_cdp_raw.py` — boss 抓取门面：re-export 全部符号（__getattr__ 代理），requests/websocket 延迟全局与 CLI 入口（021 B8 T026）
- `scripts/boss/` — boss 抓取域包（021 B8 T026）：constants（常量/JS 模板/筛选映射）、exceptions（异常族）、runtime（依赖注入）、city_map（城市码表）、rate_limit（请求计数限流）、cdp_session（CDPSession）、login（登录探测）、output（CSV/JSON 输出）、search（列表抓取）、detail_parse/detail_scrape/detail_analyze（详情三域）、smoke（巡检）、session_import（cookie 导入）、browser（Chrome 管理）、programmatic（组合运行）、cli（CLI 入口）
- `webui/src/composables/useDiscoveryState.ts` — Discovery 数据层：全部响应式状态与派生（021 B8 T027）
- `webui/src/composables/useDiscoveryWorkflow.ts` — Discovery 工作流步骤与持久化动作（021 B8 T027）
- `webui/src/composables/useDiscoverySearch.ts` — Discovery 搜索参数/高级设置/画像校验动作（021 B8 T027）
- `webui/src/composables/useDiscoveryExecution.ts` — Discovery 抓取/AI 筛选执行动作（021 B8 T027）
- `webui/src/composables/useDiscoveryTasks.ts` — Discovery 任务轮询/快照/重抓动作（021 B8 T027）
- `webui/src/composables/useDiscoveryResults.ts` — Discovery 结果/历史/反馈/导出动作（021 B8 T027）
- `webui/pipeline_guard.py` — 流水线防护域：JD 抓取批次卡死判定（心跳/独立监控/失联清理）/重抓编排/环境分流/兜底暂停/事件日志（022）
- `webui/log_api.py` — 日志读取路由域：GET /api/logs 读 career-scout.log 尾部/分页/轮询偏移与轮转切换（022）
- `webui/mode_configs.py` — 档位配置数据域：三档×三规模冻结数值、任务规模阈值、get_mode_config（024）
- `scripts/boss/detail_simulation.py` — 详情抓取人形模拟行为：加载随机等待/人形滚动/概率鼠标移动的参数与执行（024）
- `webui/src/components/ModeWarningBanner.vue` — 档位/规模风险黄色警示区组件：极限档与任务规模过大警告合并显示、不可关闭（024）
- `webui/task_pause_support.py` — 暂停编排助手：暂停 API mode 分支（immediate 批中立即停止/graceful）、幂等、guard 批次清理联动、取消清理（025）
- `webui/src/components/PauseBatchChoiceDialog.vue` — 批中暂停二选一弹窗：立即停止（默认聚焦回车触发）/ 等这批抓完、平实提示、当前批进度（025）
- `webui/recruiter_activity.py` — 招聘者活跃判定域：两平台活跃事实归一化（Boss 文本值域映射/智联时间戳）、第 7 类档位判定与判定说明模板、未知 caveat 助手（028）
- `scripts/zhilian/detail_fields.py` — 智联详情 staff 字段提取：lastOnlineTime 毫秒时间戳与状态文本的 JS 常量与合并纯函数（028）
- `packaging/window_state.py` — 窗口状态域：desktop_window.json schema 3 读写/旧版升级/工作区钳制、WindowStateTracker 普通矩形追踪、工作区枚举与窗口事件接线适配（029，b082 分支）
- `scripts/boss/browser_registry.py` — 浏览器注册表域：8 家 Chromium 浏览器配置/探测/选择持久化（browser_selection.json）/手动路径校验/CDP 内核判定（029，b082 分支）
- `webui/browser_registry_api.py` — 浏览器注册表路由域：探测清单/保存选择/路径校验端点（029，b082 分支）
- `webui/src/components/BrowserSettingsDialog.vue` — 浏览器选择对话框：注册表清单/手动路径即时校验/当前生效路径展示（029，b082 分支）
- `webui/src/themes/registry.ts` — 主题注册口：light/dark/kaleido 三态登记与值校验（032）
- `webui/src/themes/ThemePickerOptions.vue` — 长按弹层选项列表：三主题标本与当前态标识（032）
- `webui/src/themes/__tests__/registry.spec.ts` — 注册口聚焦测试（032）
- `webui/src/themes/kaleido/kaleido.css` — 万花筒主题样式：kaleido 令牌降级基座＋四页视觉＋流动层（032）
- `webui/src/themes/kaleido/KaleidoField.vue` — 万花筒光场组件：光轮/碎玻璃/注视之眼（032）
- `webui/src/themes/kaleido/useKaleidoMotion.ts` — 万花筒交互动效：转筒/瞳孔/逃生舱/首启转场（032）
- `scripts/zhilian/cdp.py` — 智联 CDP 原语与平台常量：HTTP/WS 连接、求值/导航/就绪探测、后台标签建销、端口与 host allowlist、探测 URL/提取 JS/风险 marker（031 B6）
- `scripts/zhilian/search.py` — 智联列表域：登录态探测/preflight/fetch_list/空结果 marker 确认、风险信号判定与岗位字段归一（031 B6）
- `scripts/zhilian/detail.py` — 智联详情域：单条详情提取、tab 池并行批量抓取、会话重置与默认等待器（031 B6）
- `scripts/zhilian/urls.py` — 智联纯函数域：host allowlist 判定与计划项 input_hash（031 B6）
- `webui/task_runner_support.py` — 任务运行支撑域：stdout 转日志缓冲、硬停/风控原因分类、产物读取与时间解析、载荷组装与 key 脱敏、路径常量（031 B6）
- `webui/workbench_runner.py` — WorkbenchRunner：父搜索运行 + 子查询编排、详情预算切片、增量入库与父状态推导（031 B6）
- `scripts/zhilian_cdp_raw.py` — 智联抓取兼容门面：re-export 全部既有符号（__getattr__ 代理到 `scripts/zhilian/` 四域），无实现逻辑（031 B6）
- `webui/task_runners.py` — TaskRunner 核心 + 兼容 re-export：助手落 `task_runner_support.py`、工作台编排落 `workbench_runner.py`（031 B6）
- `scripts/maintenance/historical_recovery.py` — 2026-07-28 事故恢复手动运维工具：只读预演 / 服务端 SQLite 备份与不可变 manifest / 门禁恢复三子命令 CLI 与 `--confirm` 安全栏（031 B7，自 `webui/historical_recovery.py` 整体迁入；原 `/api/recovery/*` 三条生产路由已撤除）
- `webui/src/composables/discoveryDeps.ts` — Discovery 跨域依赖契约：五域提供接口（Workflow/Search/Execution/Tasks/Results）与消费接口（*Needs）、容器建/接线函数（createDiscoveryDeps / wireDiscoveryDeps / attachRoundFlow）（031 B8）
- `webui/src/composables/useModeWarnings.ts` — 档位/规模风险警示文案：极限档与总页数 >30 两条警示的合并计算（031 B8 自 DiscoveryView.vue 抽出）
- `webui/src/composables/useNarrowSearchLayout.ts` — 窄屏布局断点判定：`max-width: 1050px` 媒体查询订阅，供两个配置抽屉的联动/独立切换（031 B8 自 DiscoveryView.vue 抽出）
- `webui/src/components/TaskCompletedToast.vue` — ~~顶部冒泡提示组件~~（035 历史组件；统一 Spec 037 已删除，"完成→查看最新"由灵动岛 completed pill + navigate 接管）
- `packaging/window_controls.py` — 窗口控制 Win32 助手：无边框窗口控制原语与最大化避让任务栏适配（036）
- `webui/src/components/WindowTitleBar.vue` — 自绘标题栏组件：桌面版窗口标题栏（文字+三按钮+拖拽区+主题配色，仅 EXE 渲染）（036）
- `webui/src/components/DynamicIsland.vue` — 顶栏胶囊灵动岛组件：live 仪表盘/转盘轮播/两色完成态/红光/未读通知面板/motion-v 动画与 collapse 暴露（统一 Spec 037）
- `webui/src/composables/useIslandCarousel.ts` — 灵动岛转盘轮播状态机：mainLaneState 直接读 roundStatus（永不冻结，硬不变式 FR-011）/打断队列 FIFO/只转一次/定时沉入 panel/badgeCount（037 新增）
- `webui/src/composables/useIslandNotices.ts` — 灵动岛通知池：roundStatus capsule 跃迁派生 completed/error/paused 通知（sync watch、同 kind 内容级替换、running 不清池/idle 清空、初始观察不产幽灵、interrupt 沉入、profile reset）
- `webui/src/composables/useReminderBadge.ts` — 提醒角标单源：服务端 /api/job-reminders/count total 与 seq 守卫/99+ 截断/aria（统一 Spec 037）
- `webui/src/composables/useIslandValueTransition.ts` — 灵动岛展示值切换：保留旧值短暂退场并在减少动态时直接替换（统一 Spec 037 修订）
- `webui/src/components/IslandNoticePanel.vue` — 灵动岛通知面板：胶囊下方弹出、error→paused→interrupt→completed 排序、未读高亮/已读淡化、行点击直达、interrupt 行 tone 染色、kaleido blur 6px（统一 Spec 037）

## 文件布局约束

```text
webui/
  app.py                    # 入口 + 路由注册
  api/                      # 路由与参数校验
  services/ 或现有业务模块   # 业务逻辑
  store/                    # 数据访问按域拆分（store.py + domain mixins）

scripts/
  boss/                     # 抓取脚本按 CDP/解析/存储/执行/CLI 拆分

webui/src/
  views/                    # 页面壳
  composables/              # 可复用逻辑
  components/               # 子组件
```

## 开发工作流

1. 用户述说完需求后，先执行 grill-me 边界质询并冻结需求。
2. 冻结后进入完整 Spec Kit 流程：`constitution（首次/原则变更）→ clarify（按需）→ specify → plan → tasks → implement → converge`。
3. 每个 Plan/Tasks 必须包含“文件边界”章节：允许修改、禁止修改、新增文件、引用方向、行数门禁。
4. 已有大文件未拆分完成前，新功能默认落入对应域的新模块，不得继续扩大 `app.py`、`store.py`、`boss_cdp_raw.py` 等超大文件。

## Governance

本宪法高于临时实现偏好；与根目录 `AGENTS.md` 冲突时，任务分类与验证矩阵优先，本宪法只约束功能/重构/拆分交付。修订必须更新版本号并同步模板与项目规则。违反文件边界或验证门禁的改动必须先修复再交付。

**Version**: 1.3.0 | **Ratified**: 2026-08-10 | **Last Amended**: 2026-08-30
