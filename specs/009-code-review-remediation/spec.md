# spec · 009 · 代码审查整改（CODE_REVIEW.md 全量整改）

> 关联文档：`CODE_REVIEW.md`（2026-07-23 全量审查报告）
> 适用范围：本 spec 覆盖 CODE_REVIEW.md 中识别的全部 28 类问题（A 代码优化 + B 逻辑优化），并按四波次序组织落地。
> 本 spec 是「问题清单 + 范围 + 验收」的总账，详细实施路径见 `plan.md`，有序任务见 `tasks.md`。

---

## 一、Problem Context（为什么做这件事）

项目（BOSS直聘职位抓取工具，Chrome CDP + Flask 后端 + Vue 前端）经过多轮迭代后，`webui/` 已积累 `store.py`(3708行) / `app.py`(2660行) 两个单体文件，`DiscoveryView.vue`(741行) 单组件堆 30+ 状态变量，并存在并发竞态、异常吞没、N+1 查询、两套任务系统并存、错误响应三套结构等系统性问题。`CODE_REVIEW.md` 对全仓做了 56 条核查（47 条完全属实、6 条部分属实、3 条论断错误），去重聚类为核心 28 个问题，按「先代码后逻辑、先低风险后高风险」排成四波。

本 spec 的目标：**把审查报告里的"问题清单"转化为"可执行、可验证、有依赖次序的工程动作"**，避免散落修复造成回归或局部优化被新需求冲散。

---

## 二、Actors & Triggers

- **Actor**：开发者（单人维护此仓库）
- **触发条件**：CODE_REVIEW.md 评审通过后启动；本 spec 完成后，CODE_REVIEW.md 列出的所有 P0/P1 问题应处于已修复或显式推迟状态
- **依赖前置**：审查报告已完成、spec 评审已通过

---

## 三、Scope

### In Scope（本 spec 覆盖）

CODE_REVIEW.md 列出的全部 28 类问题，按四波组织：

- **第 1 波 · 零风险清理**：uv.lock 修复、死代码删除、重复 import 清理、A2（BaseDialog.previousFocus 改 ref）、`_pipeline_tasks` 加清理机制、加 CI workflow
- **第 2 波 · 性能 + 竞态修复**：A1（MAX+1 竞态，三处）、A3（save_job UPSERT）、A5（N+1 查询三处）、A6（DB 索引）、A8（HTTP 语义）、A9（pollTask 退避）
- **第 3 波 · 异常处理 + 前端状态机**：A4（except Exception 收窄，区分库/子进程代码）、B3（错误响应统一）、B5（收藏状态同步）、B6（setPipelineResult 流程修正）、B7（JD 字段一致性）、AiSettingsDialog 状态清理
- **第 4 波 · 架构拆分**：app.py Blueprint 拆分、DiscoveryView.vue 按步骤拆组件、B9（Chrome 生命周期单例）、B2（两套任务系统统一）
- **跨波约束**：A10（JobItem 索引签名）、A11（JobWorkspace 死代码）、A-P2 聚类（DRY 重构、硬编码颜色、魔法数字）、A-P3 聚类、B1（store.py 拆分推迟）、B4/B8 及 B-P2/B-P3 聚类，按依赖次序编入对应波次或显式推迟

### Out of Scope（本 spec 不做）

- 新功能开发（spec 001-008 已规划的功能不在此重复）
- AGENTS.md 规则修订
- 脚本 `boss_cdp_raw.py` 的内部抓取逻辑重构（AGENTS.md 单文件约束，仅做异常处理收窄）
- 国际化、虚拟滚动、骨架屏等 P3 锦上添花项

### 推迟项（spec 阶段明确标记）

- **B1 store.py 子模块拆分**：推迟到功能稳定期。触发条件（满足任一即启动）：
  1. 连续 2 个迭代周期未新增 store 方法
  2. store.py 突破 4500 行
  3. 新增功能时反复出现"方法该放哪个域"的困扰
- 第 4 波架构拆分的 store.py 部分按上述触发条件激活，本 spec 的 plan/tasks 只细化到 app.py Blueprint 拆分

---

## 四、Functional Requirements

> 要求必须可测。每条标注所属波次与对应 CODE_REVIEW.md 条目编号。

### FR-1（第 1 波 · 零风险清理）

- **FR-1.1** `uv.lock` 必须与 `pyproject.toml` 主依赖一致，包含 keyring / pypdf / python-docx 及其全部传递依赖。验证命令 `uv sync && python -c "import flask, keyring, pypdf, docx"` 成功。（对应 A7）
- **FR-1.2** 以下死代码必须删除且全仓无残留引用：`append_json`（scripts/boss_cdp_raw.py:862）、`currentProfile` computed（App.vue:54）、`SelectOption` interface（types.ts:38）、styles.css 15 个死样式类（.view-tabs / .compact-heading / .resume-suggest-row / .execution-row / .file-input-button / .compact-check / .execution-keyword / .compact-number / .execution-button / .filter-summary-line / .run-status-strip / .zone-group-label / .zone-toolbar / .confirm-copy / .inline-alert）、`groupsForResult` 薄包装（DiscoveryView.vue:397）。删除后 `npm run build` 与 `python -m unittest discover tests` 通过。（对应 A-P2 死代码）
- **FR-1.3** 函数内重复 import 必须删除：app.py:1886 `import threading as _threading`、store.py:1788/1983/2009/3304 `from datetime import ...`。删除后 py_compile 通过。（对应 A-P2 重复 import）
- **FR-1.4** `BaseDialog.vue` 的 `let previousFocus` 改为 `const previousFocus = ref<HTMLElement | null>(null)`，访问处同步改为 `.value`。两个对话框同时存在时焦点归还不再互相覆盖。（对应 A2）
- **FR-1.5** `_pipeline_tasks` 内存 dict 必须有清理机制：任务进入终态（succeeded/failed/cancelled）后延时（建议 30 分钟）自动移除条目，dict 大小不再单调增长。（对应 A-P2 `_pipeline_tasks` 无清理）
- **FR-1.6** 仓库根新增 `.github/workflows/ci.yml`，触发条件 push/PR 到 master；步骤包含 `python -m unittest discover tests`、`python -m py_compile scripts/boss_cdp_raw.py`、前端 `npm run build`（前端失败不阻断 Python 提交，单独 job）。（对应 B8）

### FR-2（第 2 波 · 性能 + 竞态修复）

- **FR-2.1** `append_log`、`create_analysis`、`create_confirmation` 三处的 `MAX(seq)+1` / `MAX(version)+1` 必须用 `conn.execute("BEGIN IMMEDIATE")` 包裹 SELECT+INSERT（与同文件 `create_confirmation_v2` 一致），或改单语句 `INSERT ... VALUES (?, (SELECT COALESCE(MAX(seq),0)+1 FROM ... WHERE ...), ...)`。新增并发集成测试：2 线程同时对同一 task_id 追加 100 条日志，无主键冲突。（对应 A1）
- **FR-2.2** `save_job` 改为 `INSERT ... ON CONFLICT(canonical_url) DO UPDATE SET ... RETURNING id` 单语句。（对应 A3）
- **FR-2.3** 三处 N+1 查询批量化：
  - `list_analyses`（store.py:2114）：一次 JOIN 或 `IN (...)` 批查 candidate_profile_version_id
  - `search_run_jobs` 排序 key（app.py:1037）：先 `list_jobs_by_ids([...])` 一次取回，内存 dict 匹配
  - `latest_pipeline_result`（app.py:2405）：同上批量化
  （对应 A5）
- **FR-2.4** 新增 migration 创建索引：`idx_jobs_expires_at`（partial, WHERE expires_at IS NOT NULL）、`idx_jobs_last_seen_at`、`idx_discovery_job_snapshots_run_status`（run_id, fetch_status）。`cleanup_expired_jobs` 不再全表扫。（对应 A6）
- **FR-2.5** `ai_settings_models` 失败返回 HTTP 502（Bad Gateway），与 `ai_settings_test` 一致。前端调用方已读 `ok` 字段，无须额外适配。（对应 A8）
- **FR-2.6** `pollTask` 引入 `retryCount` 参数，上限 5；指数退避（4s/8s/16s/32s/64s）；达上限后状态写为 `"failed"` 而非 `"running"`，停止轮询；失败中态用 `"retrying"` 区分。（对应 A9）

### FR-3（第 3 波 · 异常处理 + 状态机）

- **FR-3.1** `except Exception` 收窄规则：
  - 库代码（store.py / app.py / ai.py / pipeline_exec.py）：收窄为具体类型（requests.ConnectionError、json.JSONDecodeError、OSError、sqlite3.Error 等）
  - 子进程入口代码（boss_cdp_raw.py / discovery_runner.py）：保留宽捕获作为边界防御，但每处必须 `logger.exception(...)` 记录堆栈
  - 收窄后全仓 `grep -r "except Exception" webui/ scripts/ | wc -l` 库代码侧下降 ≥ 80%
  （对应 A4，含审查意见中的库/子进程区分）
- **FR-3.2** 错误响应统一到 discovery envelope 结构 `{error_code, user_message, stage, retryable}`；legacy 路由用 Flask `errorhandler` 包装；pipeline 路由从 `{ok, error}` 迁移到 envelope。（对应 B3）
- **FR-3.3** 收藏状态提升到全局 store（pinia 或简单 reactive 模块 `webui/src/stores/favorites.ts`）。DiscoveryView 的 `toggleInterest` 成功后通知父组件失效缓存；App.vue 收藏抽屉打开时如 stale 才请求。（对应 B5）
- **FR-3.4** `setPipelineResult` 恢复 result 时一次性恢复全部运行时标识符（scrapeTaskId / screenTaskId / rejectedIds），而非只恢复 result。`startAiScreen` 区分"无 taskId 但有 result"情况给出明确提示，不报"请先完成本轮抓取"。（对应 B6，含审查意见中的根因修正）
- **FR-3.5** `_save_pipeline_job_to_store` 接收并传入 `job.get("jd", "")`，与 `_persist_complete_jobs` 一致。（对应 B7）
- **FR-3.6** `AiSettingsDialog` 关闭时调用 `reset()` 清理 busy/models；`watch(open)` 加 `immediate: true`；网络请求用 AbortController 在关闭时取消。（对应 B-P2 AiSettingsDialog）

### FR-4（第 4 波 · 架构拆分，不含 store.py）

- **FR-4.1** `app.py` 用 Flask Blueprint 按域拆分：`bp_tasks` / `bp_profiles` / `bp_resumes` / `bp_ai_settings` / `bp_discovery` / `bp_pipeline` / `bp_favorites` / `bp_health`。helper 函数（`_get_ai_credentials` 等）提到模块级。`create_app` 仅保留 app 工厂 + blueprint 注册 + 全局 errorhandler。机械迁移为主，路由 URL 不变。（对应 B1 app.py 部分）
- **FR-4.2** `DiscoveryView.vue` 拆为 `UploadStep.vue` / `SearchStep.vue` / `ScreenStep.vue` / `ResultsStep.vue` 四子组件，DiscoveryView 仅保留 step 导航与全局编排；`advancedSettings` / `filterValues` / `pollTask` 下沉到对应子组件或抽 `useDiscoveryWorkflow` composable。拆分后各文件 ≤ 300 行。（对应 B-P2 DiscoveryView 拆分）
- **FR-4.3** Chrome 生命周期提到应用层单例 `ChromeSessionManager` + 引用计数。`_run_ai_screen_task`、`/api/job-detail`、`/api/pipeline/jobs/<id>/jd` 等所有调用方借用而非各自启停 `ensure_chrome_ready` / `close_debug_chrome`。引用计数归零时才真正关闭 Chrome。（对应 B9，含审查意见中漏列的 L2594/L2604 调用点）
- **FR-4.4** 两套任务系统统一：pipeline 任务也落 `tasks` 表（用 `kind` 字段区分 `pipeline` / `task`），删除 `_pipeline_tasks` 内存 dict 与独立 `_pipeline_executor`。前端只轮询 `/api/tasks` 一个端点。（对应 B2）

### 跨波次约束（不单独成波，按依赖编入对应波次）

- **FR-X.1** `JobItem` 删 `[key: string]: unknown` 索引签名，后端透传字段收进 `extra?: Record<string, unknown>`；语义重叠字段（`verdict_reason`/`reason`、`interest_state`/`_marked`、`job_link`/`source_url`/`canonical_url`）二选一并与后端约定。编入第 1 波（types.ts 改动小，可与死代码清理同批）。（对应 A10）
- **FR-X.2** `JobWorkspace` 的 `selectedId` / `select` / `watch` 死代码删除（父组件不接入，删除比接双向通信更简单）。编入第 1 波。（对应 A11）
- **FR-X.3** DRY 重构（`_get_ai_credentials` 工具、`jobKey`/`jobId` 合并、`fail(error, fallback)` 工具、`TaskSnapshot` 统一、`FieldLabel` interface、`VERDICT_LABELS` Record 常量）编入第 3 波（与异常处理同批做）。**注意：审查报告原 DRY 第 2 条「`getCompany`/`verdictLabel` 重复」论断错误（JobWorkspace 实际函数名是 `company`，DiscoveryView 中无 `verdictLabel`/`company`），不执行该子项。**
- **FR-X.4** 动态拼 SET 子句（`update_discovery_run` / `update_profile_job`）：**升级为 P1**（审查报告原 P2），先核查字段名来源是否可信，若来自用户输入则改白名单 dict；若全部来自内部调用方可保持但加注释。（对应 A-P2 动态拼 SET，含审查意见升级）
- **FR-X.5** 逐行 UPDATE（`cleanup_expired_jobs`）改一条 SQL。编入第 2 波（与索引同批）。
- **FR-X.6** 硬编码颜色替换为 `--surface-1-strong` / `--surface-accent-soft` / `--danger-bg` / `--danger-soft` 等主题变量。审查报告未列的硬编码颜色（L182/L305/L312/L500/L568/L766/L970/L1187 等）一并替换。编入第 3 波。
- **FR-X.7** 魔法数字集中到 `webui/constants.py`。编入第 1 波（与死代码同批）。
- **FR-X.8** `link_direction_evidence` 合并为带 `WHERE EXISTS` 的 INSERT；`_copy_legacy_default_profile` 加 `if not exists` 短路；`list_analyses` 抽 `_decode_analysis_row`；App.vue 内联副作用抽方法；api.ts `.catch(()=>({}))` 至少 `console.warn`；JobWorkspace host 校验冗余简化。编入第 1 波。
- **FR-X.9** B-P2 状态机竞态 12 条（pollTimer 拆变量、resetWorkflow 二次确认、抓取完成自动跳 screen、screen 加上一步、rejectedIds 切 profile 清空、startAiScreen 确认环节、跳过简历清状态、watch busy 补加载、toggleRejected 失败中止、App.vue brand 改 @click.prevent、收藏抽屉 stale 标记、收藏抽屉 ESC/backdrop）编入第 3 波。**其中 pollTimer 单变量共用一条升级为 P1（内存泄漏 + 状态错乱）。**
- **FR-X.10** B-P2 后端契约不对称 3 条、路由 handler 业务编排 2 条、前端组件状态缺陷 8 条、表单与输入 6 条，编入第 3 波。

---

## 五、User Scenarios & Testing

### 场景 1：开发者冷启动部署（验证第 1 波）

- 开发者 clone 仓库后 `uv sync` 能成功安装全部依赖（keyring/pypdf/python-docx 及传递依赖）
- `python -c "import flask, keyring, pypdf, docx"` 全部成功
- `npm run build` 与 `python -m unittest discover tests` 全绿
- CI workflow 在 PR 上自动跑上述检查

### 场景 2：高并发抓取不崩（验证第 2 波）

- 模拟 2 个并发请求同时往同一 task 追加 100 条日志 → 无主键冲突，所有日志按 seq 严格递增
- 同一 canonical_url 并发保存 → 只产生一条 jobs 记录，UPDATE 不丢字段
- `list_analyses` 在 1000 条数据下响应时间下降 ≥ 50%（批量化前后对比）
- `cleanup_expired_jobs` 在 100 万行 jobs 表上执行时间从全表扫改为索引扫，EXPLAIN QUERY PLAN 不再出现 SCAN TABLE jobs

### 场景 3：失败可恢复（验证第 2+3 波）

- pollTask 在后端持续 5xx 时重试 5 次后标记 failed 并停止，不再无限轮询
- AI 模型拉取失败时前端收到 502 + error_code，UI 显示明确错误
- 任意 `except Exception` 触发时日志中有完整堆栈（库代码）或至少 logger.exception 记录（子进程代码）
- 错误响应三套结构合并后，前端只维护一个 ApiError 适配逻辑

### 场景 4：状态恢复一致（验证第 3 波）

- 用户刷新页面后 loadLatestResult 恢复 result，scrapeTaskId / screenTaskId / rejectedIds 同步恢复，进入 screen 步骤不报"请先完成本轮抓取"
- 切换 profile 后 rejectedIds 清空，不再看到上一 profile 的"不感兴趣"标记
- 在 DiscoveryView 标记收藏后，App.vue 收藏抽屉立即同步（无须先关闭再打开）

### 场景 5：架构可演进（验证第 4 波）

- 新增路由时只需在对应 blueprint 文件加一行，不动 create_app
- DiscoveryView 拆分后单文件 ≤ 300 行，新增步骤时只加一个子组件
- Chrome 生命周期单例后，并发抓取详情不再出现端口争抢日志

---

## 六、Success Criteria

### 量化指标

- **SC-1** `uv.lock` 与 `pyproject.toml` 主依赖差异为 0（`uv sync --frozen` 成功）
- **SC-2** 全仓 `grep -r "except Exception" webui/app.py webui/store.py webui/ai.py webui/pipeline_exec.py | wc -l` ≤ 5（库代码侧，第 3 波后）
- **SC-3** `webui/app.py` 行数从 2660 降至 ≤ 600（Blueprint 拆分后，create_app 仅保留工厂）；`webui/src/views/DiscoveryView.vue` 从 741 行降至 ≤ 300
- **SC-4** `python -m unittest discover tests` 全绿；新增的并发集成测试（A1 验证）通过
- **SC-5** `npm run build` 0 error、0 warning（TypeScript strict）
- **SC-6** `EXPLAIN QUERY PLAN` 对 `cleanup_expired_jobs` 的 SQL 输出包含 `SEARCH jobs USING INDEX idx_jobs_expires_at`
- **SC-7** CODE_REVIEW.md 中 P0 问题（5 项）全部修复或显式推迟（store.py 拆分）；P1 问题（15 项）全部修复
- **SC-8** 硬编码颜色 `grep -rn "#[0-9a-fA-F]\{6\}" webui/src/styles.css | wc -l` 从 30+ 降至 ≤ 5（仅保留无法主题化的字面色）

### 质性指标

- **SC-9** 新增路由 / 新增步骤时，开发者无需修改 create_app 或 DiscoveryView 主体（仅加文件 + 注册一行）
- **SC-10** 任意失败路径用户都能看到明确的 error_code + user_message，不再有静默失败
- **SC-11** 同一 canonical_url 在任意并发下只产生一条 jobs 记录且字段不丢
- **SC-12** CODE_REVIEW.md 评审意见中指出的 3 条论断错误（boss_cdp_raw.py 行数、pipeline_exec except 计数、JobWorkspace DRY 论断）已在 plan.md/tasks.md 中修正，不再以错误形态执行

---

## 七、Key Entities

- **JobItem**：前端职位类型，删索引签名后字段语义明确
- **TaskEnvelope**：统一任务抽象（DB 持久化 + kind 区分 pipeline/task），取代 _pipeline_tasks 内存 dict
- **DiscoveryError**：统一错误响应结构 `{error_code, user_message, stage, retryable}`
- **ChromeSessionManager**：应用层单例，引用计数管理 Chrome 生命周期
- **FavoriteStore**：前端全局收藏状态（pinia 或 reactive 模块）
- **BlueprintBundles**：app.py 按域拆分的 Flask Blueprint 集合

---

## 八、Assumptions

- **A-1** webui/ 仍在演进（近期加过收藏/抽屉），所以 store.py 拆分推迟到触发条件成立，不在本 spec 第 4 波内执行
- **A-2** SQLite 版本 ≥ 3.24（支持 `ON CONFLICT ... DO UPDATE` 与 `RETURNING`，3.35+）。若部署环境不满足，FR-2.2 退化为 `BEGIN IMMEDIATE` 包裹的 SELECT-then-UPDATE
- **A-3** 前端使用 Vue 3 + `<script setup>` + TypeScript strict，已具备 pinia 或可引入简单 reactive 模块
- **A-4** 第 4 波架构拆分以"机械迁移、URL 不变、行为不变"为硬约束，不做接口重设计
- **A-5** CI 跑在 GitHub Actions ubuntu-latest runner，预装 Python 3.10+ 与 Node 18+
- **A-6** 本 spec 完成后 CODE_REVIEW.md 标记为「已落地」并归档至 `docs/` 之外（CODE_REVIEW.md 本身是审查产物，不在版本控制中作为活文档维护）
- **A-7** 审查报告未列出的 7 条该列未列问题（AGENTS.md 合规审计、CDP visibility override、SQL 注入核查、tests 全 mock 盲区、本地服务重启纪律脚本化、Flask debug 显式 False、requirements.txt 未 pin 版本）作为本 spec 第 5 部分「后续待办」记录，不在四波内执行，留作下一轮审查输入

---

## 九、Risks & Mitigations

- **R-1** 第 2 波 SQL/事务改动可能引入回归 → 每处改动配真实 SQLite 并发集成测试，不只靠 mock 单测
- **R-2** 第 3 波异常收窄可能暴露原本被吞的真实 bug → 收窄时同步补 logger.exception，先观察日志一周再判断是否修复根因
- **R-3** 第 4 波 Blueprint 拆分可能漏移全局状态（_pipeline_tasks / app.config）→ 拆分前先画依赖图，全局状态统一迁到 `app.extensions` 或 `current_app` 上下文
- **R-4** 第 4 波 DiscoveryView 拆分可能破坏 emit/props 链 → 拆分前先用 vue-devtools 记录现有状态流转，拆分后逐 step 跑端到端冒烟测试
- **R-5** 第 4 波 ChromeSessionManager 引用计数实现错误可能导致 Chrome 永不关闭或提前关闭 → 加超时兜底（引用计数保持 ≥ 10 分钟无活动则强制关闭）+ 日志记录每次 acquire/release
- **R-6** 本 spec 跨四波周期长，期间新需求可能插入 → 每波完成后做一次 spec 回顾，新需求若冲突则更新本 spec 的 Out of Scope 与推迟项

---

## 十、后续待办（非本 spec 执行范围）

记录 CODE_REVIEW.md 未列但审查意见指出的 7 条问题，留作下一轮审查输入：

1. AGENTS.md 合规审计（版本号四处一致、README 双语同步、commit 规范、webui 重启纪律）
2. `boss_cdp_raw.py` CDP `background:true` visibility override是否被破坏
3. 动态拼 SET 子句的 SQL 注入核查（字段名来源审计）—— 已升级为 FR-X.4 编入第 1 波
4. tests 全 mock 无真实 Chrome 集成测试的盲区
5. AGENTS.md 本地服务重启纪律脚本化 / pre-commit hook
6. Flask `create_app().run()` 显式 `debug=False`
7. `requirements.txt` 依赖 pin 版本

---

## Done When

- [ ] 第 1 波全部 FR-1.x 与编入第 1 波的 FR-X.x 完成，测试全绿
- [ ] 第 2 波全部 FR-2.x 与编入第 2 波的 FR-X.x 完成，并发集成测试通过
- [ ] 第 3 波全部 FR-3.x 与编入第 3 波的 FR-X.x 完成，错误响应统一、状态机竞态修复
- [ ] 第 4 波全部 FR-4.x 完成，app.py Blueprint 拆分、DiscoveryView 拆分、Chrome 单例、两套任务系统统一
- [ ] store.py 拆分按触发条件显式推迟，不在本 spec 内执行
- [ ] SC-1 ~ SC-12 全部可验证通过
- [ ] CODE_REVIEW.md 标记为已落地并归档
