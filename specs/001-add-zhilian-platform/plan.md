# 技术计划：接入智联招聘平台

**分支**：`main`（本轮未创建功能分支） | **日期**：2026-08-03 | **规格**：[spec.md](spec.md)

**输入**：在现有 BOSS 岗位发现工作台中增加智联招聘平台，复用搜索、JD、AI 筛选、结果管理和断点续跑，并按平台隔离筛选字段、城市编码、登录态、任务和岗位身份。

## 摘要

本功能采用“平台注册表 + 统一 JobSource 契约 + 平台 adapter”的结构。BOSS 与智联共享任务编排、断点、AI 和结果层；每个平台只负责 AI 筛选 schema、规范城市到平台城市码的解析、浏览器运行配置、登录/风控识别、搜索列表、JD、岗位字段归一化及链接安全。

当前 BOSS 工作台的薪资、经验、学历、行业、公司规模和融资阶段只在 `/api/ai-screen` 中作为 AI 筛选条件使用，列表搜索只使用关键词、城市和页数。智联保持相同分层：公司性质替代融资阶段进入 AI 筛选，不下推为平台搜索参数。

公开 API 使用 `platform` 表示 `boss` 或 `zhilian`，避免与现有状态来源字段 `source` 混淆。平台在搜索范围预览时进入摘要，在任务创建时冻结，之后由父任务传递给 AI 筛选、补抓、单 JD、状态、取消、提前结束、恢复和结果，不再读取界面当前选择。列表组合的 non-empty、empty、failed、paused outcome 在状态推进前追加持久化，状态值只在 DB/API 边界按唯一表映射。会真实访问招聘平台的调优轮次同样冻结平台和浏览器运行配置；旧 BOSS-only 执行入口收到显式智联时明确拒绝。SQLite migration 27 使用增量列和受控列重命名消除平台原始 ID 与内部 UUID 的同名歧义，不改变已有内部 UUID 及其收藏、反馈关联。

## 技术背景

**语言/版本**：Python 3.10+（本机验证 3.11.15）；TypeScript 5.9；Node.js 20+（本机验证 25.5.0）

**主要依赖**：Flask 3、requests、websocket-client、原生 Chrome DevTools Protocol；Vue 3、Vite、Lucide Vue

**存储**：本地 SQLite；本地 JSON 配置；任务产物文件；按“平台 + 浏览器账号”确定性隔离的 Chrome profile

**测试**：Python `unittest`；Vitest + Vue Test Utils；`vue-tsc` + Vite 构建；真实 Chrome/CDP 冒烟；桌面与窄屏真实渲染检查

**目标平台**：单用户本地 Web 工作台；当前真实验收环境为 Windows + Chrome

**项目类型**：Flask 后端、Vue 前端和本地浏览器自动化组成的桌面式 Web 应用

**性能目标**：不扩大现有任务上限（总计划页数 1-200）；平台切换和 schema 渲染不触发页面跳转；单任务仍使用现有有界后台执行模型，不新增跨平台并行抓取

**硬约束**：不复制 `get_jobs` 源码；不自动投递或沟通；不绕过 EdgeOne/验证码/限流；不记录 Cookie、Key、JD 正文或简历到安全日志；平台级可人工解除阻断必须暂停并保留检查点；全局结构不兼容必须失败，不能伪装为空结果

**范围**：两个平台、单次任务或调优实验单平台；当前主链为 `screening_runs + scrape_run_jobs + pipeline_checkpoints`，并平台化所有会继续访问 source、改变 run 状态、关闭浏览器或删除结果快照的外围入口；跨平台聚合后置

## 规则门禁

*门禁在 Phase 0 前检查，并在设计完成后复核。项目没有 constitution 文件，因此以下门禁来自根目录 `AGENTS.md`、全局规则和冻结 Spec。*

| 门禁 | 当前状态 | 证据/处理 |
| --- | --- | --- |
| 先读项目参考，不复制受限源码 | 通过 | 已读 `roadmap/REFERENCE_GET_JOBS.md`；只借鉴历史 URL、字段和选择器线索，真实页面合同重新验证 |
| 平台差异限制在 adapter/schema/安全边界 | 通过 | 统一任务、岗位、状态与恢复合同，不复制 BOSS 主流程 |
| AI 筛选与列表搜索分层一致 | 通过 | 搜索仅冻结关键词、规范城市、页数；AI run 单独冻结平台筛选快照 |
| 存量 BOSS 数据兼容且迁移可回滚 | 待实施验证 | migration 27 前由应用 bootstrap 创建一致性备份、SHA-256、manifest 并验证可读，之后才构造 `TaskStore` |
| 岗位两类 ID 不再混用 | 通过 | source 和临时结果统一使用 `platform_job_id`；收藏/反馈只使用 `jobs.id` 对外的 `job_id` |
| 登录态和凭据不进入仓库/日志 | 通过 | profile 仅在忽略的本地目录；API 不返回路径、路径摘要或 Cookie |
| 风控真实暂停，结构异常真实失败 | 通过 | 错误矩阵在 [job-source.md](contracts/job-source.md) 冻结，不使用“失败或暂停”二选一描述 |
| 后台任务切页/重启后可恢复 | 通过 | `platform`、完整输入摘要、scope、筛选快照、浏览器账号、CDP 端口和 profile key 均被冻结 |
| 调优真实 source 轮次不串平台 | 设计已冻结，待实施验证 | experiment/workload/artifact/manifest 冻结平台、城市与运行配置；AI-only 轮次继承产物平台 |
| legacy BOSS-only 入口不静默回退 | 设计已冻结，待实施验证 | 创建与执行入口显式智联统一返回 `legacy_platform_not_supported` |
| 桌面/窄屏和真实登录态验收 | 待实施验证 | [quickstart.md](quickstart.md) 固定 1440×900、390×844 和智联一页主链路 |
| tasks 前全量审查 | 通过 | A-001~A-010、R-001~R-006 已关闭，Terra medium 复核 PASS；结论见 `checklists/pre-tasks-audit.md` |

## 项目结构

### 本功能工件

```text
specs/001-add-zhilian-platform/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md
│   ├── job-source.md
│   └── platform-schema.md
└── checklists/
    ├── requirements.md
    └── pre-tasks-audit.md
```

### 预计源码落点

```text
data/
└── zhilian_city_codes.json # 脱敏、版本化的规范城市到智联城市码映射

scripts/
├── boss_cdp_raw.py          # 保持 BOSS 行为；补齐显式端口透传契约
└── zhilian_cdp_raw.py       # 智联列表/JD/登录、风控与城市码解析

webui/
├── platforms.py             # 平台注册表、AI schema、城市目录、启用状态、运行配置和链接规则
├── source.py                # JobSource Protocol、公共执行壳、BOSS/智联 adapter
├── pipeline_exec.py         # 平台无关编排、浏览器账号/profile 解析、失败矩阵
├── tuning.py                # 调优 workload/manifest 平台冻结、产物继承与校验
├── execution_config.py      # platform 进入冻结 scope、任务输入摘要和 digest
├── core.py                  # 平台输入/schema/城市校验和兼容入口
├── store.py                 # migration 27、平台岗位身份、完整快照和 run 平台
├── workbench.py             # 按平台归一化链接和岗位去重
├── app.py                   # 启动前备份、平台 API、factory、任务传播、暂停/恢复
└── src/
    ├── types.ts
    ├── discovery.ts
    ├── App.vue
    ├── views/DiscoveryView.vue
    ├── components/BrowserAccountsDialog.vue
    ├── components/JobWorkspace.vue
    ├── components/TaskProgress.vue
    └── styles.css

tests/
├── fixtures/zhilian/        # 脱敏列表、详情、元数据、登录墙、EdgeOne 和空结果 fixture
├── test_platforms.py
├── test_source.py
├── test_webui_store.py
├── test_webui_app.py
├── test_healthy_pipeline.py
├── test_workbench.py
├── test_concurrency.py
├── test_tuning.py
└── test_chrome_setup.py

webui/src/**/__tests__/       # schema、切换、恢复、来源、链接和窄屏组件测试
```

**结构决定**：新增 `webui/platforms.py` 作为唯一平台注册边界，避免在 `app.py`、`core.py`、`source.py` 和 Vue 中重复硬编码平台能力。`scripts/zhilian_cdp_raw.py` 只处理真实页面；业务状态仍由现有 WebUI 管理。

## 数据与迁移策略

1. 应用 bootstrap 在构造 `TaskStore` 前只读检测数据库是否存在且 schema 低于 27。命中时用 SQLite backup API 创建一致性备份，写入 SHA-256 与 manifest，重新以只读连接执行 `PRAGMA quick_check` 并核对版本；任一步失败都阻止启动。
2. migration 27 在单事务中增加平台、岗位字段、完整筛选快照、任务输入摘要和 `interruption_kind`，创建追加式 `screening_source_attempts`，并把运行链三处历史外部 `job_id` 列重命名为 `platform_job_id`。旧 run 不猜造 source outcome。
3. 调优 experiment/manifest 增加独立平台列；`tuning_stage_artifacts` 增加 `platform/source_artifact_kind/scope_digest/task_input_digest` 外层列。仅按迁移前 BOSS 证明规则回填平台，不改写旧 manifest/artifact JSON 或其已签发摘要，也不猜填摘要列。
4. `jobs` 与 `screening_results` 增加经验、学历和 `extra_json`；结果快照还保存 `platform`、`platform_job_id`、规范链接及可空内部 `job_id`，刷新后不依赖内存补字段。
5. 新岗位按 `(platform, platform_job_id)` 与全局唯一 `canonical_url` 双索引查重；URL 必须先通过声明平台的 host/path 校验。两个索引分别命中不同记录或 URL 已归属另一平台时返回 `job_identity_conflict`，禁止静默合并。
6. 迁移成功前后对照旧内部 UUID、表行数、收藏和反馈关联计数；失败由事务回滚。已有智联数据后不得恢复旧备份覆盖，只能禁用智联新任务并保留 v27 schema 与数据。

完整字段、冲突算法和旧任务兼容见 [data-model.md](data-model.md)。

## 实施切片与门禁

### 切片 1：平台合同与 BOSS 基线

- 建立 `platform`、AI filter schema、城市目录、JobSource Protocol、统一链接/岗位身份合同。
- 将 BOSS 现有映射注册为 `boss`；逐类冻结 legacy API 矩阵，只有明确的旧 BOSS 请求可省略平台，显式智联不得静默回退。
- 搜索参数删除非空 AI filters；BOSS adapter 列表、单详情、批详情均显式透传冻结 CDP 端口。
- 补契约测试后运行 BOSS 聚焦回归；通过并独立审查后提交。

### 切片 2：迁移前备份与平台持久化

- 在 `TaskStore` 创建前实现 migration 27 bootstrap 备份、hash、manifest、可读性检查和失败阻断。
- migration 27 为 `jobs`、`tasks`、`search_runs`、`screening_runs`、`discovery_runs` 增加平台字段；补齐岗位快照、筛选快照、任务输入摘要、interruption kind 和追加式 source attempt 表。
- 将 `screening_results`、`screening_pending_results`、`scrape_run_jobs` 中语义为平台原始 ID 的物理列统一为 `platform_job_id`。
- 为调优 stage artifact 增加外层平台、source artifact kind 与摘要列，按客观迁移前 BOSS 证据回填，保持旧 JSON/digest 原样。
- 使用 v26 数据副本验证迁移、幂等、外键、全局 URL/双索引冲突、source attempt 约束、调优摘要守恒、备份和失败回滚；审查后提交。

### 切片 3：智联 schema、城市和浏览器空间

- 用真实登录页面验证公司性质、列表字段、城市映射和选择器，生成最小脱敏 fixture；不得猜值。
- 后端从版本化本地 fixture/注册表发布 AI schema 和规范城市目录；任务恢复不依赖当天在线元数据。
- BOSS profile 使用现有账号 `profile_dir`；智联 profile 固定派生为 `<boss_profile_dir>.zhilian`。BOSS 使用平台端口 9222，智联使用 9223；同平台切账号沿用受控替换，未知 profile 占用时拒绝。
- 验证智联全国 `jl0`、缺失城市映射阻断及两个平台 profile 目录不碰撞；审查后提交。

### 切片 4：智联抓取 adapter

- 实现智联 setup/preflight/list/detail/batch，保留 artifact、input hash、安全日志和熔断器合同。
- 列表输入只含 platform、关键词、规范城市、解析后的智联城市码和页数，不含 AI 筛选。
- 输出统一使用 `platform_job_id`，并保留标题、公司、薪资、地点、经验、学历、原链接及 `extra`。
- fixture 覆盖登录墙、EdgeOne、验证码、限流、真实空结果、缺字段、单详情异常和全局结构异常；验证 adapter outcome 可被编排层无损写入 source attempt；审查后提交。

### 切片 5：后台主链路与恢复

- source factory 从冻结 `platform + browser_account + cdp_port + profile_key` 创建 adapter。
- 搜索预览、创建、AI、进度查询、结果和继续接口传播平台；子任务从父任务继承。
- 每个列表组合在推进完成键、状态、进度或 result snapshot 前追加 source attempt；状态/历史结果按最新 attempt 汇总，重启后不从零岗位反推 empty。
- 取消和提前结束按目标 run 的平台、profile key 与端口释放资源；结果重置按显式 run 删除并返回实际清理的平台。
- 单 JD、单岗位补抓和批量补抓从来源 run 与 platform_job_id 继承平台，不使用 UI、最近结果或默认 BOSS。
- 搜索 run 不冻结 AI 筛选；`/api/ai-screen` 根据父平台和 schema 版本创建完整筛选快照，并写入 `task_input_digest`。
- 按冻结错误矩阵区分 paused、failed、partial 与单项失败；所有暂停必须经过 `queued -> running -> paused`。DB 只写 canonical 状态与 interruption kind，API 使用唯一状态映射。
- 验证切换界面后原任务不变、服务重启后恢复原平台、错配阻断、并发继续只成功一次；审查后提交。

### 切片 6：岗位落库、收藏与反馈

- 结果接口明确返回 `platform_job_id` 和可空内部 `job_id`。
- 收藏或反馈临时 pipeline 岗位时，后端在单事务内按双索引规则 upsert `jobs`，回写内部 UUID，再执行 `profile_jobs`/`feedback_events`。
- 历史结果、收藏、反馈、垃圾桶和补抓均以内部 UUID 关联，展示时保留平台来源与完整岗位字段。
- 覆盖 URL 变化、旧 BOSS URL 兼容、全局 URL 唯一、URL 平台归属、两个索引冲突和跨平台同裸 ID；审查后提交。

### 切片 7：前端平台工作台

- 在岗位发现流程顶部增加 BOSS/智联分段控件；当前选择只影响新任务草稿。
- schema 按平台加载并带版本；共通筛选保留，`boss.stage` 与 `zhilian.company_nature` 独立保存、渲染和 allowlist 提交。
- schema 与城市请求使用请求序号或取消机制，陈旧响应不得覆盖新选择；恢复任何非终态任务时先恢复任务平台，再加载 schema 与快照。
- 简历分析只返回按当前平台 schema 投影的建议；前端不以分析响应覆盖平台注册表标签，最近结果平台与新任务草稿平台分别管理。
- `/api/execute-search` 不提交 AI 筛选；`/api/ai-screen` 才提交当前平台筛选值和 schema 版本。
- 任务、岗位、收藏和详情显示平台来源；最近结果保持其自身平台，不被草稿切换重标。
- 完成 Vitest、构建和两档真实视口检查；审查后提交。

### 切片 8：调优实验与 legacy 边界

- 调优 experiment、workload、输入 artifact、manifest 和 program evidence 冻结并核验平台、城市解析、filter schema 与浏览器运行配置摘要。
- TuningRoundRunner 的五类 stage 保持现有值；list/detail/end_to_end 依据 manifest 创建对应平台 adapter，只有 list/detail 产出可复用 source artifact，rough/fine 分别继承 list/detail 并拒绝错配。
- migration 27 为旧 experiment/manifest/stage artifact 外层按客观规则回填 `boss`，但不修改已签发 JSON 与摘要、不猜填摘要；无法证明的旧 artifact 阻断。
- 对 `/api/tasks`、`/api/scrape`、`/api/setup-chrome`、`/api/results`、旧 task 子路由和 `/api/search-runs` create/detail/jobs/cancel 执行逐路由 legacy 矩阵测试；POST body、GET query 的显式智联均零副作用拒绝。

### 切片 9：集成、禁用路径与真实验收

- 为平台注册项实现 `enabled_for_new_tasks`；智联被禁用时阻止搜索、AI 和补抓新任务，但历史智联结果、收藏和反馈仍可读。
- 基于最终代码执行 Python 全量、前端全量、构建和仓库卫生检查。
- BOSS 执行回归冒烟；智联执行一个关键词、一个城市、一页列表、至少一个 JD、AI 筛选和结果展示。
- 主动验证登录失效和 EdgeOne/验证码暂停、结构异常失败、单详情异常继续以及最新结果三种查询语义。
- 主动验证取消/提前结束不关闭另一平台、结果重置精确作用于 run、快速平台切换无陈旧 schema 覆盖，以及五类调优轮次平台一致。
- 独立审查完整集成差异；只修阻断项并聚焦复查，最终验证通过后停止审查。

## 回滚与禁用策略

1. 尚未迁移的现有数据库：bootstrap 备份验证失败或 migration 27 失败时应用拒绝启动，原数据库不被部分升级。
2. 已迁移但尚未写入智联数据：可回退应用代码并由人工选择是否恢复经过验证的 v26 备份；系统不自动覆盖数据库。
3. 已写入智联数据：不得恢复旧备份。将平台注册项 `enabled_for_new_tasks=false`，阻止新的智联搜索、AI 和补抓任务；历史读取、来源展示、收藏和反馈继续使用 v27 数据。
4. 运行中任务不因 UI 开关被静默改成 BOSS。禁用发生后，未完成且需要重新进入 adapter 的智联任务返回 `platform_disabled`，保留原状态和数据，等待重新启用。
5. adapter 或页面失效只停用智联新执行能力，不删除 Chrome profile、岗位、任务或用户反馈，也不放宽 BOSS 链接和登录边界。

## 复杂度说明

| 增加项 | 必要原因 | 更简单方案未采用的原因 |
| --- | --- | --- |
| `webui/platforms.py` 注册表 | AI schema、城市、域名、profile、端口和显示名需要同一权威来源 | 在多个模块加 `if platform` 会复制规则并难以验证第三个平台 |
| run 根实体增加 `platform` 与输入摘要 | 任务必须在切页、排队和重启后仍绑定创建平台与冻结输入 | 只存于内存或读取 UI 当前值无法建立可靠恢复约束 |
| 运行链物理列改为 `platform_job_id` | 现有同名 `job_id` 同时指平台 ID 和内部 UUID | 仅写文档别名会继续让 API、收藏和反馈发生语义混用 |
| `jobs` 双索引冲突算法 | 平台 ID 稳定但 URL 可能变化，旧记录又可能只有 URL | 只选一个索引会遗漏冲突或破坏旧 BOSS 关联 |
| 追加式 source attempt | 真实空结果、列表失败和暂停需要在刷新/重启后可审计 | 只保存内存 outcome 或岗位数无法区分空结果与解析失败 |
| canonical DB/API 状态映射 | 取消、重启恢复和结果终态必须只有一种解释 | 继续沿用多套别名会让同一 run 的可恢复性不确定 |
| migration 前 bootstrap 备份 | `TaskStore.__init__()` 当前会立即迁移，迁移内备份已经太晚 | 仅在 migration 方法里写备份无法保证迁移前一致性快照 |
| 独立智联 scraper | 页面、登录和风控识别与 BOSS 完全不同 | 把智联分支塞进 BOSS 脚本会扩大耦合并增加回归面 |

## 设计后复核状态

当前 tasks 前全量审查已通过：A-001~A-010、R-001~R-006 全部关闭，Terra medium 复核 PASS，结论见 [pre-tasks-audit.md](checklists/pre-tasks-audit.md)。公司性质编码、智联城市码、页面选择器、空结果标记和风控 DOM 仍是实施时外部事实门禁，实施前必须用当前真实登录页面验证并生成脱敏 fixture；它们已被定义为阻断式实施门禁，不允许猜值。是否生成 tasks.md 等待用户决定。
