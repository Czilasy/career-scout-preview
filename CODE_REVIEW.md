# 代码全量审查报告

> 审查日期：2026-07-23
> 审查范围：`webui/`（后端 Python + 前端 Vue）、`scripts/`（抓取脚本）、配置与测试层
> 审查方法：并行派 3 路子代理覆盖后端 / 前端 / 脚本配置，主代理补查脚本层细节
> 分类原则：A = 代码优化（不动功能逻辑，只优化代码本身）；B = 逻辑优化（功能编排、流程合理性）

---

## 一、总体汇总

| 层 | 扫描范围 | 命中数 |
|---|---|---|
| 后端 Python | `app.py`(2660行) / `store.py`(3700+行) / `discovery*.py` / `pipeline_exec.py` / `ai.py` | 42 项 |
| 前端 Vue | `App.vue` / `DiscoveryView.vue`(741行) / 6 子组件 / `styles.css`(1310行) / `api.ts` | 100 项 |
| 脚本/配置/测试 | `boss_cdp_raw.py`(1900行) / `pyproject.toml` / `uv.lock` / `.gitignore` / `tests/`(25文件) | 6 项 |

去重聚类后核心问题约 50 个。前端 100 项里大量是 P2/P3 重复模式（硬编码颜色、死样式、魔法数字），按主题聚类而非逐条罗列。

| 严重程度 | A 代码优化 | B 逻辑优化 | 合计 |
|---|---|---|---|
| P0 阻断 | 3 | 2 | 5 |
| P1 重要 | 8 | 7 | 15 |
| P2 改进 | 10（聚类） | 11（聚类） | 21 |
| P3 锦上添花 | 聚类 | 聚类 | — |

---

## 二、A 部分 · 代码优化（不动逻辑）

### A-P0 阻断级（潜在 bug / 数据风险，必须先修）

#### A1. `MAX(seq)+1` / `MAX(version)+1` 并发竞态
- **问题**：`SELECT COALESCE(MAX(seq),0)+1` 与后续 INSERT 分两步非原子，并发追加会触发 PRIMARY KEY 冲突。三处同病：
  - `append_log`（task_logs 主键冲突）
  - `create_analysis`（analysis 版本号冲突）
  - `create_confirmation`（confirmation 版本号冲突）
- **位置**：
  - [webui/store.py#L1303](file:///d:/项目/boss/webui/store.py#L1303)
  - [webui/store.py#L2025](file:///d:/项目/boss/webui/store.py#L2025)
  - [webui/store.py#L2251](file:///d:/项目/boss/webui/store.py#L2251)
- **建议**：改单语句 `INSERT ... VALUES (?, (SELECT COALESCE(MAX(seq),0)+1 FROM task_logs WHERE task_id=?), ...)`，或将 seq 列改 AUTOINCREMENT。

#### A2. `BaseDialog.previousFocus` 模块作用域 `let` 串状态
- **问题**：`let previousFocus` 写在 `<script setup>` 顶层，Vue SFC 编译后成模块级单例。同时存在两个对话框（如 AiSettingsDialog 嵌套 BaseDialog）时，后开者覆盖前者的 previousFocus，关闭时焦点归还错乱。
- **位置**：[webui/src/components/BaseDialog.vue#L14](file:///d:/项目/boss/webui/src/components/BaseDialog.vue#L14)
- **建议**：改 `const previousFocus = ref<HTMLElement | null>(null)`，与其他 ref 一致。

#### A3. `save_job` 先 SELECT 再 INSERT/UPDATE 非原子
- **问题**：`SELECT id FROM jobs WHERE canonical_url=?` 与后续写操作分两步，高并发下同一 URL 可能走异常路径（虽 UNIQUE 会拦，但触发未预期分支）。
- **位置**：[webui/store.py#L1787](file:///d:/项目/boss/webui/store.py#L1787)
- **建议**：改 `INSERT ... ON CONFLICT(canonical_url) DO UPDATE SET ... RETURNING id` 单语句。

---

### A-P1 重要（性能 / 异常 / 一致性）

#### A4. 40 处 `except Exception` 静默吞异常
- **问题**：全仓 40 处过宽捕获，多数无日志、无 re-raise，掩盖真实错误。
  - 后端 `app.py` 7 处（`_execute` L283、`_make_discovery_source` L574、`profile_resume` L965、`discovery_run_results` L1519、`_run_pipeline_task` L2007、`_run_ai_screen_task` L2155 等）
  - `ai.py` 7 处（L126 / L231 / L444 / L602 / L619 / L705 / L1092）
  - `discovery_runner.py` 10+ 处（L1803-L2101，多数标 `# noqa: BLE001` 但仍无日志）
  - `pipeline_exec.py` 3 处（L82 / L111 / L300）
  - 脚本 `boss_cdp_raw.py` 15 处（L197 / L1655 / L1681 / L2235 / L2263 / L2291 / L2302 / L2309 / L2316 / L2369 / L2409 / L2434 / L2532 / L2539）
- **位置**：见上行行号
- **建议**：收窄为具体类型（`requests.ConnectionError`、`json.JSONDecodeError`、`OSError` 等）；边界处必须保留 `except Exception` 的，至少 `app.logger.exception(...)` 记录堆栈。

#### A5. N+1 查询三处
- **问题 a**：`list_analyses` 循环内对每行 `with self._connection() as lookup` 开新连接查 candidate_profile_version_id，N 条分析开 N 个连接。
  - 位置：[webui/store.py#L2114](file:///d:/项目/boss/webui/store.py#L2114)
- **问题 b**：`search_run_jobs` 排序回调里 `store.get_job(item["job_id"])` 每次比较都查一次 DB（O(n log n) 次查询），循环体又查一次 + feedback。
  - 位置：[webui/app.py#L1037](file:///d:/项目/boss/webui/app.py#L1037)
- **问题 c**：`latest_pipeline_result` 逐条 `store.get_job(pj["job_id"])` 做 URL 匹配。
  - 位置：[webui/app.py#L2405](file:///d:/项目/boss/webui/app.py#L2405)
- **建议**：批量 `list_jobs_by_ids([...])` 一次取回，内存 dict 匹配。

#### A6. 缺关键查询索引
- **问题**：`jobs.last_seen_at`、`jobs.expires_at`（cleanup 全表扫）、`discovery_job_snapshots(run_id, fetch_status)` 等高频过滤列缺索引。
- **位置**：[webui/store.py#L343](file:///d:/项目/boss/webui/store.py#L343)（建表）、[webui/store.py#L1981](file:///d:/项目/boss/webui/store.py#L1981)（cleanup 按 expires_at 全表扫）
- **建议**：新增 migration：`CREATE INDEX IF NOT EXISTS idx_jobs_expires_at ON jobs(expires_at) WHERE expires_at IS NOT NULL` 等。

#### A7. `uv.lock` 缺包
- **问题**：`uv.lock` 只锁了 flask / requests / websocket-client / blinker / certifi 等 15 个，缺 `keyring` / `pypdf` / `python-docx` 及其传递依赖，`uv sync` 冷装会 ModuleNotFoundError。
- **位置**：[uv.lock](file:///d:/项目/boss/uv.lock)
- **建议**：`uv sync` 让 lock 补全传递依赖；验证 `python -c "import flask, keyring, pypdf, docx"`。

#### A8. `ai_settings_models` 错误返回 HTTP 200
- **问题**：AI 模型拉取失败时 `return jsonify({"ok": False, ...}), 200`，违反 HTTP 语义，前端难区分成功失败。
- **位置**：[webui/app.py#L872](file:///d:/项目/boss/webui/app.py#L872)
- **建议**：失败返回 502 或 400，与 `ai_settings_test` 保持一致。

#### A9. `pollTask` 无最大重试 + 无退避
- **问题**：catch 分支固定 4000ms 后再调 `pollTask`，无重试计数，后端持续 5xx 或断网时无限轮询到组件卸载；且 `failed.status` 仍写成 `"running"` + "正在重试"文案，状态语义与字段命名不一致。
- **位置**：[webui/src/views/DiscoveryView.vue#L358](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L358)
- **建议**：引入 `retryCount` 参数，达上限（如 5）后真正标记 `failed` 并停轮询；失败状态用 `"retrying"` 区分。

#### A10. `JobItem` 类型有 `[key:string]: unknown` 索引签名
- **问题**：索引签名让任何拼写错误（`job.titel`）都不报错；同时 `verdict_reason`/`reason`、`interest_state`/`_marked`、`job_link`/`source_url`/`canonical_url` 三组字段语义重叠。
- **位置**：[webui/src/types.ts#L7](file:///d:/项目/boss/webui/src/types.ts#L7)
- **建议**：删索引签名，后端透传字段收进 `extra?: Record<string, unknown>`；语义重叠字段二选一并与后端约定。

#### A11. `JobWorkspace` 的 `selectedId`/`select` 死代码
- **问题**：子组件暴露 `selectedId` prop、`select` emit、`watch(() => props.selectedId)`，但父组件 `<JobWorkspace :jobs="..." :empty-message="...">` 既不传 `selectedId` 也不监听 `@select`，`localSelectedId` 与父脱钩，emit 出去无人接收。
- **位置**：[webui/src/components/JobWorkspace.vue#L6](file:///d:/项目/boss/webui/src/components/JobWorkspace.vue#L6)；调用方 [DiscoveryView.vue#L723](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L723)
- **建议**：要么父组件真正接入双向通信，要么删除 `selectedId`/`select`/相关 watch。

---

### A-P2 改进（聚类）

#### 死代码清理
- `append_json`（[scripts/boss_cdp_raw.py#L862](file:///d:/项目/boss/scripts/boss_cdp_raw.py#L862)）— 全仓无调用方，确认后删
- `App.vue` `currentProfile` computed（[App.vue#L54](file:///d:/项目/boss/webui/src/App.vue#L54)）— 模板从未引用
- `types.ts` `SelectOption` interface（[types.ts#L38](file:///d:/项目/boss/webui/src/types.ts#L38)）— 全工程无 import
- `styles.css` 未使用类：`.view-tabs` / `.compact-heading` / `.resume-suggest-row` / `.execution-row` / `.file-input-button` / `.compact-check` / `.execution-keyword` / `.compact-number` / `.execution-button` / `.filter-summary-line` / `.run-status-strip` / `.zone-group-label` / `.zone-toolbar` / `.confirm-copy` / `.inline-alert` 等 10+ 个（[styles.css#L714](file:///d:/项目/boss/webui/src/styles.css#L714) 区域）
- `DiscoveryView` `groupsForResult` 薄包装（[DiscoveryView.vue#L397](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L397)）— 仅转调 `partitionPipelineResult`，多余

#### DRY 重构
- **AI 凭据读取三步组合**（`get_ai_settings()` + `get_credential_ref()` + `ai_service.retrieve_api_key()`）在 `app.py` 出现 8+ 次（L822/L847/L862/L955/L1082/L2064/L2188/L2405）— 抽 `_get_ai_credentials(store) -> tuple | None`
- **`jobKey` / `getCompany` / `verdictLabel`** 在 DiscoveryView 与 JobWorkspace 各写一遍（[DiscoveryView.vue#L436](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L436)、[JobWorkspace.vue#L43](file:///d:/项目/boss/webui/src/components/JobWorkspace.vue#L43)）— 抽 `webui/src/utils/job.ts`
- **`notify(errorMessage(error, "xxx 失败"), "error")`** 在 DiscoveryView 重复 8 次（L257/L291/L305/L330/L354/L417/L492/L526）— 抽 `fail(error, fallback)` 工具
- **`TaskSnapshot` interface** 在 DiscoveryView 与 TaskProgress 重复定义且字段不一致 — 抽到 `types.ts` 统一
- **`FieldLabel` 元组类型**靠魔法索引 `meta?.[0]`/`meta?.[2]` 取值（[DiscoveryView.vue#L23](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L23)）— 改 interface
- **`VERDICT_LABELS`** 在 JobWorkspace 用 if 链、DiscoveryView 用 `resultTabs` label，文案重复 — 抽 Record 常量

#### 重复 import
- [app.py#L1886](file:///d:/项目/boss/webui/app.py#L1886) 函数内 `import threading as _threading`（顶部已有 `import threading`）
- [store.py#L1788](file:///d:/项目/boss/webui/store.py#L1788) 方法内 `from datetime import timedelta`（顶部已导入）

#### 动态拼 SET 子句
- `update_discovery_run`（[store.py#L2587](file:///d:/项目/boss/webui/store.py#L2587)）、`update_profile_job`（[store.py#L1845](file:///d:/项目/boss/webui/store.py#L1845)）字段名字符串拼接易埋雷 — 改白名单 dict 或拆语义化方法

#### 逐行 UPDATE
- `cleanup_expired_jobs`（[store.py#L1981](file:///d:/项目/boss/webui/store.py#L1981)）用 Python 循环逐条改 status — 一条 SQL 搞定

#### `_pipeline_tasks` 内存 dict 无清理
- [app.py#L1887](file:///d:/项目/boss/webui/app.py#L1887) 只增不删，长跑内存泄漏 — 加 LRU 或终态后延时清理

#### 前端样式硬编码
- [styles.css](file:///d:/项目/boss/webui/src/styles.css) 30+ 处硬编码颜色：`.brand-mark{background:#1d1911}` L102、`.file-drop{background:#0e131a}` L494、`.job-list-pane{background:#0f141c}` L876、`.job-row.selected{background:#1a202b}` L908、`.verdict-reason{background:#1b1914}` L997、`.notice-bar{background:#181e28}` L343 等
- `--danger-bg` fallback 用 `#ef4444`（Tailwind red-500）与项目 `--danger:#ff7e86` 色相不一致（L275）
- **建议**：补 `--surface-1-strong` / `--surface-accent-soft` / `--danger-bg` / `--danger-soft` 等变量并替换硬编码

#### 魔法数字散落
- `30`(天清理)、`60`(detail budget)、`12`(reuse hours)、`5`(feedback 阈值)、`100`(limit 上限)、`50`(日志尾部)、`8000`/`5000`/`3000`(notice 时长) 等
- **建议**：集中到 `webui/constants.py`

#### 其他
- `link_direction_evidence` 两次 SELECT 校验后 INSERT（[store.py#L2207](file:///d:/项目/boss/webui/store.py#L2207)）— 合并为带 `WHERE EXISTS` 的 INSERT
- `_copy_legacy_default_profile` 每次初始化都执行（[store.py#L244](file:///d:/项目/boss/webui/store.py#L244)）— 加 `if not exists` 短路
- `list_analyses` 重复 `get_analysis` 的解码逻辑（[store.py#L2114](file:///d:/项目/boss/webui/store.py#L2114)）— 抽 `_decode_analysis_row`
- `App.vue` 内联 `@click="favoritesOpen=!favoritesOpen; loadFavorites()"` 含副作用（[App.vue#L119](file:///d:/项目/boss/webui/src/App.vue#L119)）— 抽方法，关闭时不发请求
- `api.ts` 第 50 行 `.catch(()=>({}))` 静默吞错（[api.ts#L50](file:///d:/项目/boss/webui/src/api.ts#L50)）— 至少 console.warn
- `JobWorkspace.jobUrl` 校验冗余（`host==="zhipin.com"||host==="www.zhipin.com"||host.endsWith(".zhipin.com")`，前两项被第三项包含）

---

### A-P3 锦上添花（聚类，建议先不碰）

- 类型注解补全（`TaskRunner.create_scrape` 无返回注解、`list_profile_jobs(...) -> list` 改 `list[dict]`）
- `ERROR_CODE_MAP` / `DEFAULT_USER_MESSAGES` 改 `StrEnum`
- `styles.css` 分节注释（`/* === Tokens === */` 等）
- `TaskProgress :key` 用 log 内容无意义，`:key="index"` 即可
- `verdictLabel` 改 Record 查表
- `discovery.ts buildSearchScriptParams` 固定返回 `filters:{}` 无意义
- i18n、虚拟滚动、骨架屏等

---

## 三、B 部分 · 逻辑优化（功能编排）

### B-P0 阻断级（架构性，高风险高收益）

#### B1. `store.py` 3700+ 行 + `app.py` 2660 行单体
- **问题**：
  - `store.py` 一个文件包含 15 个 migration、tasks/profiles/resumes/ai_settings/search_runs/jobs/feedback/discovery_runs/snapshots/assessments/candidate_profiles 等十几个域的 CRUD，严重违反单一职责。
  - `app.py` 的 `create_app` 内部既定义 40+ 路由，又内嵌 `TaskRunner`/`WorkbenchRunner` 类、`_run_pipeline_task`/`_run_ai_screen_task` 业务逻辑、`_pipeline_tasks` 全局状态、`_save_latest_pipeline_result` 文件持久化，单函数 2100+ 行。
- **位置**：[webui/store.py](file:///d:/项目/boss/webui/store.py)、[webui/app.py](file:///d:/项目/boss/webui/app.py)
- **建议**：
  - `app.py` 用 Flask Blueprint 按域拆（`bp_tasks` / `bp_discovery` / `bp_pipeline` / `bp_ai_settings`），helper 提到模块级 — 机械迁移为主，风险可控
  - `store.py` 拆 `store/base.py`（连接 + migration 框架）+ 各域子模块，`TaskStore` 组合子 store — 牵动 import 多，建议第二轮
- **判断**：webui/ 无 scripts/ 的「单文件原则」硬约束，拆分被允许。**建议先做 app.py Blueprint 拆分，store.py 拆分推迟到功能稳定期**（近期仍在加收藏/抽屉等功能，过早拆分易被新需求冲散）。

#### B2. 两套并行任务系统互不感知
- **问题**：`TaskRunner`/`WorkbenchRunner`（基于 `store.create_task` + DB 持久化）与 `_pipeline_tasks` + `_pipeline_executor`（纯内存 + ThreadPoolExecutor）是两套独立任务跟踪系统，前端分别轮询 `/api/tasks` 和 `/api/search-progress`。
- **位置**：[app.py#L131](file:///d:/项目/boss/webui/app.py#L131)（TaskRunner）、[app.py#L1882](file:///d:/项目/boss/webui/app.py#L1882)（pipeline 内存任务）
- **建议**：统一到一套任务抽象（保留 DB 持久化那套，pipeline 任务也落 `tasks` 表，kind 区分），前端只轮询一个端点。

---

### B-P1 重要（功能编排缺陷）

#### B3. 错误响应三套结构
- **问题**：legacy 路由用 `{"error": "..."}` 或 `{"ok": False, "error": "..."}`；discovery 路由用 `{error_code, user_message, stage, retryable}`；pipeline 路由用 `{"ok": False, "error": "..."}`。前端要适配三套。
- **位置**：
  - [app.py#L604](file:///d:/项目/boss/webui/app.py#L604)（legacy）
  - [app.py#L1132](file:///d:/项目/boss/webui/app.py#L1132)（discovery envelope）
  - [app.py#L2302](file:///d:/项目/boss/webui/app.py#L2302)（pipeline）
- **建议**：统一到 discovery envelope 结构，legacy 路由用 errorhandler 包装。

#### B4. `discovery_run_results` 单路由混 v1/v2 投影
- **问题**：一个路由函数 `if policy_version == "discovery_v2":` 走 90 行 v2 投影，else 走 60 行 v1 投影，字段结构、排序、过滤完全不同。
- **位置**：[app.py#L1486](file:///d:/项目/boss/webui/app.py#L1486)
- **建议**：拆两个路由 `/api/discovery/runs/<id>/results/v2` 和 `/v1`，或 dispatcher 委托不同 handler。

#### B5. 收藏抽屉与 DiscoveryView 收藏操作无数据同步
- **问题**：`App.vue` 的 `favorites` 是本地 ref，每次打开抽屉调 `loadFavorites()` 重新拉。`DiscoveryView` 中 `toggleInterest` 标记收藏后并不通知父组件刷新，已打开抽屉看到的是旧数据。
- **位置**：[App.vue#L16](file:///d:/项目/boss/webui/src/App.vue#L16)、[DiscoveryView.vue#L471](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L471)
- **建议**：提升为全局 store（pinia 或简单 reactive 模块），或 DiscoveryView 通过 emit 通知 App 失效缓存。

#### B6. `setPipelineResult` 强行标记所有 step completed
- **问题**：`loadLatestResult` 在 `onMounted` 中调用，若后端有上次结果，会把 upload→search→screen 三步全标记 completed，`enabledSteps` 全亮；但 `scrapeTaskId` 为空，用户进 screen 步骤点"开始 AI 筛选"会因 `!scrapeTaskId` 报错"请先完成本轮抓取"。流程不自洽。
- **位置**：[DiscoveryView.vue#L397](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L397)
- **建议**：恢复 result 时同步恢复 `scrapeTaskId`，或恢复后强制跳 results 步骤、禁用前序步骤的"重新执行"按钮；至少在 `startAiScreen` 中区分"无 taskId 但有 result"的情况。

#### B7. `_save_pipeline_job_to_store` 存空 JD
- **问题**：pipeline 结果岗位落库时 `jd=""` 传空字符串，而 `_persist_complete_jobs` 同样调 `save_job` 却传真实 `detail.get("jd")`，两路径写入 jobs 表 JD 字段不一致。
- **位置**：[app.py#L2492](file:///d:/项目/boss/webui/app.py#L2492) vs [app.py#L395](file:///d:/项目/boss/webui/app.py#L395)
- **建议**：`_save_pipeline_job_to_store` 接收并传入 `job.get("jd", "")`，两路径一致。

#### B8. 无 CI/CD
- **问题**：仓库有 25 个测试文件但无 GitHub Actions / pre-commit hook，改代码后测试是否跑过全靠自觉。
- **位置**：仓库根无 `.github/workflows/`
- **建议**：加 `.github/workflows/ci.yml` 跑 `python -m unittest discover tests` + `python -m py_compile scripts/boss_cdp_raw.py` + 前端 `npm run build`。

#### B9. Chrome 生命周期各自启停争抢端口
- **问题**：`_run_ai_screen_task` 内调 `ensure_chrome_ready()` + `close_debug_chrome()`，但 `/api/job-detail` 和 `/api/pipeline/jobs/<id>/jd` 也各自调 `ensure_chrome_ready`，并发时争抢同一 CDP 端口。
- **位置**：[app.py#L2094](file:///d:/项目/boss/webui/app.py#L2094)、[app.py#L2469](file:///d:/项目/boss/webui/app.py#L2469)
- **建议**：Chrome 生命周期上提到应用层单例（`ChromeSessionManager` + 引用计数），各业务方借用而非各自启停。

---

### B-P2 改进（UX 流程 / 状态机，聚类）

#### DiscoveryView 741 行应按步骤拆
- **问题**：单 SFC 含 upload/search/screen/results 四个独立步骤的模板、状态、方法，30+ 个状态变量堆在 setup 顶层。
- **位置**：[DiscoveryView.vue](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue) 全文
- **建议**：抽 `UploadStep.vue` / `SearchStep.vue` / `ScreenStep.vue` / `ResultsStep.vue` 四子组件，`DiscoveryView` 仅保留 step 导航与全局编排；`advancedSettings` / `filterValues` / `pollTask` 等下沉到对应子组件或抽 `useDiscoveryWorkflow` composable。

#### 状态机竞态与流程缺陷
- **pollTimer 单变量 scrape/screen 共用**（[DiscoveryView.vue#L108](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L108)）— 后一次 `setTimeout` 覆盖前一次句柄，前一次无法 clearTimeout，内存泄漏 + 状态错乱。拆两个变量或 `Map<string, number>`。
- **`resetWorkflow` 无二次确认**（[DiscoveryView.vue#L421](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L421)）— 误点丢失全部状态。用 `BaseDialog` 弹确认。
- **抓取完成后仍需手动点"继续确认筛选条件"**（[DiscoveryView.vue#L666](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L666)）— 而 AI 筛选完成会自动跳 results，前后不一致。抓取 done 后自动 `activeStep="screen"`。
- **screen 步骤无"上一步"**（[DiscoveryView.vue#L672](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L672)）— 发现 filter 配错只能整体 reset。加"返回搜索"按钮。
- **`rejectedIds` 切换 profileId 不清空**（[DiscoveryView.vue#L96](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L96)）— 切到 profile B 看到 A 的"不感兴趣"标记。profileId 变化时清空本轮状态。
- **`startAiScreen` 无确认环节**（[DiscoveryView.vue#L334](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L334)）— 进 screen 步骤即可立即点击启动，即使没调整任何 filter。加 checkbox 或滚动一次后启用。
- **"跳过简历"路径不清状态**（[DiscoveryView.vue#L602](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L602)）— 直接置 `analysisReady=true` 跳 search，但未重置 `scrapeCompleted/resultLoaded/pipelineResult/rejectedIds`，可能看到旧结果；且 `aiConsent` 也不重置。抽 `skipResume()` 方法。
- **`watch(() => props.profileId)` 与 busy 竞态**（[DiscoveryView.vue#L178](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L178)）— busy 时跳过 `loadLatestResult`，busy 结束后不补加载。busy 从 true→false 时补一次。
- **`toggleRejected` 调 `toggleInterest` 失败仍继续标记**（[DiscoveryView.vue#L498](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L498)）— toggleInterest 失败时 toggleRejected 应中止。
- **`App.vue` brand 链接 `href="/"` 整页刷新**（[App.vue#L108](file:///d:/项目/boss/webui/src/App.vue#L108)）— 丢失 DiscoveryView 状态。改 `@click.prevent="resetView"`。
- **`App.vue` 收藏抽屉每次打开都重新请求**（[App.vue#L19](file:///d:/项目/boss/webui/src/App.vue#L19)）— 无缓存。加 stale 标记。
- **`App.vue` 收藏抽屉无 ESC / backdrop 关闭**（[App.vue#L136](file:///d:/项目/boss/webui/src/App.vue#L136)）— 只能点关闭按钮。
- **`App.vue` 初始化失败后无重试入口**（[App.vue#L58](file:///d:/项目/boss/webui/src/App.vue#L58)）— profiles 为空时 UI 无重试按钮。

#### 后端契约不对称
- **`mark_screening_interest` 与 `mark_screening_reject` 行为不对称**（[store.py#L1566](file:///d:/项目/boss/webui/store.py#L1566)）— interested 自动改 status，not_interested 要手动。统一 `create_feedback` 契约。
- **撤销收藏不清 feedback_events**（[store.py#L1596](file:///d:/项目/boss/webui/store.py#L1596)）— `count_effective_feedback` 仍统计已撤销反馈。撤销时同步 `UPDATE feedback_events SET revoked_at=?`。
- **数据库表缺部分外键**（[store.py#L373](file:///d:/项目/boss/webui/store.py#L373)）— `feedback_events.run_id` 无外键可指向已删除的 run。补 `FOREIGN KEY ... ON DELETE SET NULL`。

#### 路由 handler 承担业务编排
- **`discovery_create_run`**（[app.py#L1348](file:///d:/项目/boss/webui/app.py#L1348)）— 路由内拼 confirmation_view、调 `_discovery_compile_plan`、创建 run、提交 runtime。抽 `discovery_service.create_run_from_confirmation(...)`。
- **`profile_resume`**（[app.py#L912](file:///d:/项目/boss/webui/app.py#L912)）— POST handler 内做文件上传、格式校验、AI 解析、画像创建、字段合并。抽 `resume_service.upload_and_parse(...)`。

#### 前端组件状态缺陷
- **AiSettingsDialog 关闭不清理状态**（[AiSettingsDialog.vue#L17](file:///d:/项目/boss/webui/src/components/AiSettingsDialog.vue#L17)）— 关闭后 `busy`/`models` 残留；`watch(open)` 无 `immediate`，首次 open=true 时不触发加载。加 `reset()` + `immediate: true` + AbortController。
- **AiSettingsDialog 无表单校验**（[AiSettingsDialog.vue#L93](file:///d:/项目/boss/webui/src/components/AiSettingsDialog.vue#L93)）— 仅 HTML required，apiKey 空时依赖后端处理。前端做 url 格式校验。
- **AiSettingsDialog models 不持久化** — 每次打开都要重新点"拉取模型"。存 localStorage。
- **api.ts `sessionToken` 无 401 自动刷新**（[api.ts#L1](file:///d:/项目/boss/webui/src/api.ts#L1)）— 401 时无自动 `initializeSession` 重试，用户需刷新页面。
- **BaseDialog backdrop `mousedown.self` 关闭未防拖拽误关**（[BaseDialog.vue#L70](file:///d:/项目/boss/webui/src/components/BaseDialog.vue#L70)）— 选中文本拖到 backdrop 释放会误关。改监听 mousedown 记录起始位置。
- **BaseDialog 打开强制 focus 第一个元素**（[BaseDialog.vue#L53](file:///d:/项目/boss/webui/src/components/BaseDialog.vue#L53)）— 抢用户输入焦点，移动端可能弹键盘。focus panel 容器本身。
- **JobWorkspace 桌面端关闭详情后无法重开**（[JobWorkspace.vue#L22](file:///d:/项目/boss/webui/src/components/JobWorkspace.vue#L22)）— `detailOpen` 在桌面端事实上冗余。
- **TaskProgress 失败无"重试"按钮** — 失败仅显示 error，用户需返回 DiscoveryView 找按钮重试。
- **TaskProgress 进度条失败状态无视觉区分**（[TaskProgress.vue#L39](file:///d:/项目/boss/webui/src/components/TaskProgress.vue#L39)）— failed 时进度条仍 accent 黄色。
- **TaskProgress 只显示最后 6 条日志无"查看全部"**（[TaskProgress.vue#L44](file:///d:/项目/boss/webui/src/components/TaskProgress.vue#L44)）。

#### 表单与输入
- **`addCustomKeyword` 不 split 多关键词**（[DiscoveryView.vue#L269](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L269)）— 输入"Java,Python"作为单个关键词，与 cityText 逗号分隔逻辑不一致。
- **文件拖入未校验类型**（[DiscoveryView.vue#L199](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L199)）— 拖入 .exe 也显示文件名，提交后端才报错。
- **高级设置 details 默认折叠**（[DiscoveryView.vue#L646](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L646)）— 新用户不会展开，用默认 pages=3 抓取后才发现岗位太少。
- **高级设置参数无边界校验**（[DiscoveryView.vue#L649](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L649)）— 可粘贴超界值或字母。
- **`loadLatestResult` 失败提示对首次使用也触发**（[DiscoveryView.vue#L411](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L411)）— 404 与 5xx 都弹 notice，首次进来看到红/黄提示体验差。404 静默。
- **`loadAdvancedSettings` 失败 notify 但 `loadFilterLabels` 静默**（[DiscoveryView.vue#L170](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue#L170)）— 两策略不一致。

---

### B-P3 锦上添花（聚类，建议先不碰）

- 路由命名风格统一（`/api/tasks` 复数 vs `/api/profile` 单数 vs `/api/search-runs` kebab 混用）
- `POST /api/feedback/<id>/revoke` 改 PATCH（幂等更新）
- `discovery_get_analysis` 返回 `quality` 嵌套与顶层 `quality_status`/`quality_warnings` 冗余
- a11y：`result-tabs` 有 `role="tablist"` 但内容区无 `role="tabpanel"` 关联；`job-row` `role="listitem"` 在 button 上双重角色；JobWorkspace 列表项无键盘导航
- `index.html` 缺 noscript 提示、缺 PWA manifest
- `color-scheme: dark` 强制无亮色适配
- `keyword.recommended` 视觉提示对色弱用户不友好
- 全局 loading 骨架屏、虚拟滚动
- `ensureFeedbackProfile` 首次点收藏自动创建 profile 用户无感知

---

## 四、执行顺序建议

按「先代码优化、后逻辑优化」「先低风险、后高风险」交叉排序，分四波：

### 第 1 波 · 零风险清理（纯代码优化，跑测试即可验证）
- 修 `uv.lock`（`uv sync`）+ 加 CI workflow
- 删死代码：`append_json`、`currentProfile`、`SelectOption`、styles.css 死样式、`groupsForResult`
- 删重复 import（threading、datetime）
- `BaseDialog.previousFocus` 改 ref（A2）
- `_pipeline_tasks` 加清理机制

### 第 2 波 · 性能 + 竞态修复（代码优化，需测试）
- A1（三处 MAX+1 竞态）、A3（save_job UPSERT）
- N+1 查询三处批量化（A5）
- 加 DB 索引（A6）
- `ai_settings_models` HTTP 语义修正（A8）
- `pollTask` 退避（A9）

### 第 3 波 · 异常处理 + 前端状态机（代码+逻辑混合）
- 40 处 except Exception 收窄 + 加日志（A4）
- 错误响应结构统一（B3）
- 前端 pollTimer 拆分、收藏状态同步（B5）、setPipelineResult 流程修正（B6）
- `_save_pipeline_job_to_store` JD 一致（B7）
- AiSettingsDialog 状态清理、AbortController

### 第 4 波 · 架构拆分（逻辑优化，高风险高收益）
- `app.py` Blueprint 拆分（先做，机械迁移为主）
- `DiscoveryView.vue` 按步骤拆组件
- Chrome 生命周期单例（B9）
- 两套任务系统统一（B2）
- `store.py` 子模块拆分（第二轮，推迟到功能稳定期）

**不确定的点**：第 4 波的 `store.py` 拆分是否值得现在做——收益大但牵动面广，当前 webui 逻辑还在演进（近期还在加收藏/抽屉等功能），过早拆分可能被新需求冲散。建议 `store.py` 拆分推迟到功能稳定期，先做 `app.py` Blueprint 拆分。

---

## 五、涉及文件清单

### 后端 Python
- [webui/app.py](file:///d:/项目/boss/webui/app.py)（2660 行，主审查对象）
- [webui/store.py](file:///d:/项目/boss/webui/store.py)（3700+ 行，主审查对象）
- [webui/source.py](file:///d:/项目/boss/webui/source.py)
- [webui/discovery.py](file:///d:/项目/boss/webui/discovery.py)
- [webui/discovery_runner.py](file:///d:/项目/boss/webui/discovery_runner.py)
- [webui/pipeline_exec.py](file:///d:/项目/boss/webui/pipeline_exec.py)
- [webui/process_executor.py](file:///d:/项目/boss/webui/process_executor.py)
- [webui/ai.py](file:///d:/项目/boss/webui/ai.py)（7 处 except Exception）
- [webui/semantic.py](file:///d:/项目/boss/webui/semantic.py)

### 前端 Vue
- [webui/src/App.vue](file:///d:/项目/boss/webui/src/App.vue)
- [webui/src/views/DiscoveryView.vue](file:///d:/项目/boss/webui/src/views/DiscoveryView.vue)（741 行）
- [webui/src/components/BaseDialog.vue](file:///d:/项目/boss/webui/src/components/BaseDialog.vue)
- [webui/src/components/JobWorkspace.vue](file:///d:/项目/boss/webui/src/components/JobWorkspace.vue)
- [webui/src/components/TaskProgress.vue](file:///d:/项目/boss/webui/src/components/TaskProgress.vue)
- [webui/src/components/AiSettingsDialog.vue](file:///d:/项目/boss/webui/src/components/AiSettingsDialog.vue)
- [webui/src/components/NoticeBar.vue](file:///d:/项目/boss/webui/src/components/NoticeBar.vue)
- [webui/src/styles.css](file:///d:/项目/boss/webui/src/styles.css)（1310 行）
- [webui/src/api.ts](file:///d:/项目/boss/webui/src/api.ts)
- [webui/src/types.ts](file:///d:/项目/boss/webui/src/types.ts)
- [webui/src/discovery.ts](file:///d:/项目/boss/webui/src/discovery.ts)
- [webui/index.html](file:///d:/项目/boss/webui/index.html)

### 脚本与配置
- [scripts/boss_cdp_raw.py](file:///d:/项目/boss/scripts/boss_cdp_raw.py)（1900 行）
- [scripts/job_summary.py](file:///d:/项目/boss/scripts/job_summary.py)
- [pyproject.toml](file:///d:/项目/boss/pyproject.toml)
- [uv.lock](file:///d:/项目/boss/uv.lock)
- [requirements.txt](file:///d:/项目/boss/requirements.txt)
- [.gitignore](file:///d:/项目/boss/.gitignore)
- [tests/](file:///d:/项目/boss/tests/)（25 个测试文件）

---

> 本报告仅做研究分析，未修改任何文件。如需执行某一波优化，请明确指示，我会先呈现完整执行清单（涉及文件、每步预期结果、验证命令），确认后再动手第一步。
