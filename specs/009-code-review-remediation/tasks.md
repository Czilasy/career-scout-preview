# Tasks: 009 代码审查整改

**Plan**: [plan.md](plan.md) | **Date**: 2026-07-23

> 由 `/speckit-tasks` 生成。第 1+2 波为有序可执行任务；第 3+4 波在末尾作为「后续波次（待激活）」段，等第 2 波合并后基于真实基线补 tasks。
> 每个任务遵循格式：`- [ ] T### [P?] [波次标签] 描述 + 文件路径`
> - `[P]` 标记表示可与同波次内其他 [P] 任务并行（不同文件、无依赖）
> - 波次标签：[W1] 第 1 波 / [W2] 第 2 波
> - 第 1 波内部任务大多可并行（不同文件零依赖），第 2 波任务多数串行（store.py 改动需有序）
> - 项目本地运行，不开 GitHub issue、不开 PR（见 AGENTS.md「提交流程」）

---

## Phase 1: Setup（前置）

- [x] T001 创建分支 `feat/009-code-review-remediation`，从 master 拉取

---

## Phase 2: 第 1 波 · 零风险清理

### 2.1 依赖修复

- [x] T002 [P] [W1] 修复 uv.lock：运行 `uv sync` 让 lock 补全 keyring/pypdf/python-docx 及传递依赖。验证 `python -c "import flask, keyring, pypdf, docx; print('ok')"` 输出 ok。文件：`uv.lock`

### 2.2 死代码删除（后端）

- [x] T003 [P] [W1] 删除 `append_json` 函数（全仓无调用方）。用 Grep 确认 `append_json` 在 `scripts/` 与 `webui/` 下无调用后删除函数体。文件：`scripts/boss_cdp_raw.py`（L862 附近）
- [x] T004 [P] [W1] 删除 `currentProfile` computed（模板未引用，仅 L54 定义处）。删除 L54-56 三行。文件：`webui/src/App.vue`
- [x] T005 [P] [W1] 删除 `SelectOption` interface（全工程无 import）。删除 L38-41 四行。文件：`webui/src/types.ts`

### 2.3 死代码删除（前端样式）

- [x] T006 [P] [W1] 删除 styles.css 15 个死样式类及其规则块。用 Grep 确认每个类名在 `webui/src/**/*.vue` 与 `webui/src/**/*.ts` 下无引用后删除规则块：`.view-tabs` / `.compact-heading` / `.resume-suggest-row` / `.execution-row` / `.file-input-button` / `.compact-check` / `.execution-keyword` / `.compact-number` / `.execution-button` / `.filter-summary-line` / `.run-status-strip` / `.zone-group-label` / `.zone-toolbar` / `.confirm-copy` / `.inline-alert`。文件：`webui/src/styles.css`

### 2.4 死代码删除（前端组件）

- [x] T007 [P] [W1] 删除 `groupsForResult` 薄包装函数（仅转调 `partitionPipelineResult`），调用方改为直接调 `partitionPipelineResult`。文件：`webui/src/views/DiscoveryView.vue`（L397 附近）
- [x] T008 [P] [W1] 删除 JobWorkspace 的 `selectedId` prop、`select` emit、`watch(() => props.selectedId)` 死代码（父组件 DiscoveryView L723 不传也不监听）。文件：`webui/src/components/JobWorkspace.vue`（L6-18、L39-41）
- [x] T009 [P] [W1] 简化 JobWorkspace `jobUrl` 的 host 校验冗余（`host==="zhipin.com"||host==="www.zhipin.com"` 被第三项 `host.endsWith(".zhipin.com")` 包含，前两项删）。文件：`webui/src/components/JobWorkspace.vue`

### 2.5 重复 import 清理

- [x] T010 [P] [W1] 删除 app.py L1886 函数内 `import threading as _threading`（顶层 L14 已 import threading），函数体内 `_threading.Lock()` 改为 `threading.Lock()`。文件：`webui/app.py`
- [x] T011 [P] [W1] 删除 store.py 函数内 4 处 `from datetime import ...` 重复 import（L1788、L1983、L2009、L3304），顶层 L18 已导入 `datetime, timedelta, timezone`。文件：`webui/store.py`

### 2.6 BaseDialog ref 修正

- [x] T012 [P] [W1] `BaseDialog.vue` 的 `let previousFocus: HTMLElement | null = null` 改为 `const previousFocus = ref<HTMLElement | null>(null)`，所有读写处改为 `.value`（赋值 `previousFocus.value = ...`，读取 `previousFocus.value`）。文件：`webui/src/components/BaseDialog.vue`（L14 及所有引用处）

### 2.7 _pipeline_tasks 清理机制

- [x] T013 [W1] 在 `_pipeline_tasks` 写入终态（succeeded/failed/cancelled）后启动 `threading.Timer(30 * 60, ...)` 自动 `pop(task_id, None)`；进程退出时整体清理。在 `app.py` 的 `_run_pipeline_task` / `_run_ai_screen_task` 终态写入处加 hook。新增单元测试 `tests/test_pipeline_tasks_cleanup.py`（用 mock Timer 验证终态后定时器被注册 + pop 被调用）。文件：`webui/app.py`、`tests/test_pipeline_tasks_cleanup.py`

### 2.8 constants.py 提取

- [x] T014 [P] [W1] 新建 `webui/constants.py`，集中后端魔法数字：`CLEANUP_EXPIRED_DAYS=30`、`DETAIL_BUDGET=60`、`REUSE_HOURS=12`、`FEEDBACK_THRESHOLD=5`、`LIST_LIMIT=100`、`LOG_TAIL_LINES=50` 等。把 store.py / app.py / discovery_runner.py 中散落的对应字面量替换为常量引用。文件：`webui/constants.py`（新增）、`webui/store.py`、`webui/app.py`、`webui/discovery_runner.py`

### 2.9 store.py 小重构

- [x] T015 [P] [W1] `_copy_legacy_default_profile`（L244 调用、L1257 方法体）加 `if not exists` 短路：先查 `candidate_profiles WHERE name='default'`，若已存在直接 return，避免每次初始化都跑两次 SELECT。文件：`webui/store.py`
- [x] T016 [P] [W1] `link_direction_evidence`（L2207）合并两次 SELECT 校验 + INSERT 为带 `WHERE EXISTS` 的单条 INSERT（或 `INSERT ... SELECT ... WHERE (SELECT ... = SELECT ...)`）。文件：`webui/store.py`
- [x] T017 [P] [W1] `list_analyses`（L2114）抽 `_decode_analysis_row(row)` helper 复用解码逻辑（含 JSON 解码），消除与 `get_analysis` 的重复。文件：`webui/store.py`

### 2.10 App.vue / api.ts 小重构

- [x] T018 [P] [W1] App.vue L119 内联 `@click="favoritesOpen=!favoritesOpen; loadFavorites()"` 抽为 `toggleFavorites()` 方法，关闭（`favoritesOpen=false`）时不发 `loadFavorites()` 请求。文件：`webui/src/App.vue`
- [x] T019 [P] [W1] api.ts L50 `.catch(()=>({}))` 改为 `.catch((err) => { console.warn('response.json parse failed', err); return {}; })`。文件：`webui/src/api.ts`

### 2.11 types.ts 索引签名修正

- [x] T020 [W1] `JobItem` 删 `[key: string]: unknown` 索引签名（types.ts L30），后端透传字段收进 `extra?: Record<string, unknown>`。语义重叠字段（`verdict_reason`/`reason`、`interest_state`/`_marked`、`job_link`/`source_url`/`canonical_url`）二选一并与后端约定（核查后端实际写入字段后定）。文件：`webui/src/types.ts`、`webui/src/views/DiscoveryView.vue`、`webui/src/components/JobWorkspace.vue`（适配访问处）

### 2.12 CI workflow

- [x] T021 [P] [W1] 新建 `.github/workflows/ci.yml`，两个独立 job：(1) `python-tests` 跑 `python -m unittest discover tests` + `python -m py_compile scripts/boss_cdp_raw.py`；(2) `frontend-build` 跑 `cd webui && npm ci && npm run build`。触发条件 push/PR 到 master。文件：`.github/workflows/ci.yml`（新增）

### 2.13 FR-X.4 SQL 注入核查

- [x] T022 [W1] 核查 `update_discovery_run`（store.py L2587）和 `update_profile_job`（store.py L1845）的字段名来源：Grep 调用方，确认字段名是 hardcoded 字符串还是来自用户输入/外部数据。若全部可信加注释 `# 字段名来自内部调用方，非用户输入，无需白名单`；若不可信改白名单 dict 校验。结论更新到 plan.md。文件：`webui/store.py`

### 2.14 第 1 波验证

- [x] T023 [W1] 第 1 波回归：`python -m unittest discover tests` + `python -m py_compile scripts/boss_cdp_raw.py webui/app.py webui/store.py` + `cd webui && npm run build`，全部通过。验证清单见 [quickstart.md](quickstart.md) 第 1 波章节
  - **回归结论（2026-07-23）**：语法检查 ✓、前端 `npm run build` ✓、`python -m unittest discover tests` 1142 项中 1136 通过、4 errors + 2 failures。**6 项失败全部为预先存在**（已用 `git stash` 隔离本次修改后复跑确认）：①`test_revoke_not_interested_restores_job_and_removes_active_trash`（`list_trash_with_origin` 方法不存在）；②`test_semantic` 模块 import 错误（`assess_semantic_similarity` 不存在）；③`test_webui_browser` 找不到已删除的 `ScreeningView.vue`（先前 91d984a 删除筛选工作台遗留）；④⑤`test_375px_layout_keeps_header_actions_and_tabs_reachable` / `test_desktop_navigation_and_four_gated_steps_render_without_overflow` 浏览器测试断言与已删除的"筛选工作台"标签页相关。本次修改未引入新的失败。
- [x] T024 [W1] 第 1 波 commit：拆多个 conventional commits（`chore: fix uv.lock` / `refactor: remove dead code` / `refactor: extract constants` / `ci: add workflow` / `fix: BaseDialog previousFocus ref` 等），不 push（本地运行）
  - **执行结论（2026-07-23）**：拆为 6 个 commit，未 push：
    - `c57c67b docs(spec): 009 代码审查整改 spec 与审查报告`
    - `a693a5d chore(deps): 补全 uv.lock 缺失依赖`
    - `77e8f41 refactor(webui): 第 1 波零风险清理`（含 BaseDialog ref / types 索引签名 / constants 提取 / _pipeline_tasks 清理机制 等多个子项）
    - `b010322 ci: 添加 GitHub Actions 测试与构建工作流`
    - `11de186 docs(agents): 删除 issue 强制要求，改为本地运行说明`
    - `7397732 build(webui): 重新构建前端产物`

---

## Phase 3: 第 2 波 · 性能 + 竞态修复

### 3.1 测试先行（TDD）

- [ ] T025 [W2] 新建 `tests/test_concurrency.py`，先写红测试：(a) `test_append_log_concurrent`：2 线程 × 100 条并发追加同一 task_id，断言无 `sqlite3.IntegrityError` 且 seq 1-200 全部存在；(b) `test_save_job_concurrent`：2 线程同时 save_job 同一 canonical_url 不同字段值，断言只 1 条记录且字段非空。跑测试确认 RED（当前实现会 fail）。文件：`tests/test_concurrency.py`（新增）
- [ ] T026 [W2] 新建 `tests/test_indexes.py`，先写红测试：用 `EXPLAIN QUERY PLAN` 断言 `cleanup_expired_jobs` 的 SQL 与 `discovery_job_snapshots WHERE run_id=? AND fetch_status=?` 都使用索引（当前无索引会 fail）。文件：`tests/test_indexes.py`（新增）

### 3.2 A1 三处事务包裹

- [ ] T027 [W2] `append_log`（store.py L1303）：`with self._connection() as conn:` 块内首行加 `conn.execute("BEGIN IMMEDIATE")`，保持 SELECT MAX(seq)+1 + INSERT 在同一事务。参照同文件 `create_confirmation_v2`（L2289）模式。跑 T025 的 `test_append_log_concurrent` 确认 GREEN。文件：`webui/store.py`
- [ ] T028 [W2] `create_analysis`（store.py L2025）：同 T027 模式，`with` 块内加 `BEGIN IMMEDIATE` 包裹 SELECT MAX(version)+1 + INSERT。文件：`webui/store.py`
- [ ] T029 [W2] `create_confirmation`（store.py L2251）：同 T027 模式。注意此为旧版，`create_confirmation_v2`（L2289）已用 BEGIN IMMEDIATE，本任务只改旧版。文件：`webui/store.py`

### 3.3 A3 save_job UPSERT

- [ ] T030 [W2] `save_job`（store.py L1787）：先 `python -c "import sqlite3; print(sqlite3.sqlite_version)"` 确认版本。≥ 3.35 改为单语句 `INSERT ... ON CONFLICT(canonical_url) DO UPDATE SET ... RETURNING id`；< 3.35 退化为 `BEGIN IMMEDIATE` 包裹现有 SELECT-then-INSERT/UPDATE。跑 T025 的 `test_save_job_concurrent` 确认 GREEN。文件：`webui/store.py`

### 3.4 A5 N+1 批量化

- [ ] T031 [W2] 新增 `TaskStore.list_jobs_by_ids(ids: list[str]) -> dict[str, dict]` 方法，一次 `SELECT * FROM jobs WHERE id IN (...)` 取回，返回 `{job_id: row_dict}`。文件：`webui/store.py`
- [ ] T032 [W2] `list_analyses`（store.py L2114）：循环内 `with self._connection() as lookup` 单查 candidate_profile_version_id 改为一次 `WHERE analysis_id IN (...)` 批查，内存 dict 匹配。文件：`webui/store.py`
- [ ] T033 [W2] `search_run_jobs` 排序 key（app.py L1037）：先 `list_jobs_by_ids([...])` 一次取回，排序 key 改为内存 dict 查找，消除 sort key 内调 DB。注意：Python `list.sort(key=...)` 用 Schwartzian 变换每元素只调一次，本任务把这一次也省掉。文件：`webui/app.py`
- [ ] T034 [W2] `latest_pipeline_result`（app.py L2405）：逐条 `store.get_job(pj["job_id"])` 改为 `list_jobs_by_ids([pj["job_id"] for pj in ...])` 一次取回 + 内存 dict 匹配。文件：`webui/app.py`

### 3.5 A6 索引创建

- [ ] T035 [W2] 新增 migration（编号接现有最大值 +1），创建 3 个索引：`idx_jobs_expires_at`（partial: `WHERE expires_at IS NOT NULL`）、`idx_jobs_last_seen_at`、`idx_discovery_job_snapshots_run_status`（复合: `run_id, fetch_status`）。用 `CREATE INDEX IF NOT EXISTS` 幂等。跑 T026 确认 GREEN。文件：`webui/store.py`

### 3.6 FR-X.5 cleanup 改单 SQL

- [ ] T036 [W2] `cleanup_expired_jobs`（store.py L1981）：Python 循环逐行 `UPDATE profile_jobs SET status='deleted'` 改为单条 `UPDATE profile_jobs SET status='deleted' WHERE profile_id IN (SELECT pj.profile_id FROM profile_jobs pj JOIN jobs j ON pj.job_id=j.id WHERE pj.status='new' AND j.expires_at IS NOT NULL AND j.expires_at < ?)`，返回影响行数。文件：`webui/store.py`

### 3.7 A8 HTTP 语义修正

- [ ] T037 [W2] `ai_settings_models`（app.py L860）：`except ai_service.AISecurityError` 分支的 `return jsonify({...}), 200` 改为 `return jsonify({...}), 502`。确认前端 `fetchModels` 通过 `response.ok` 判断，状态码改 502 不影响前端。文件：`webui/app.py`

### 3.8 A9 pollTask 退避

- [ ] T038 [W2] `pollTask`（DiscoveryView.vue L358）：引入 `retryCount` 参数（默认 0），catch 分支按指数退避 `4000 * 2**retryCount`（上限 64s，5 次后停）；达上限后 `scrapeSnapshot/screenSnapshot.value = { status: "failed", ... }` 而非 `status: "running"`；失败中态用 `status: "retrying"`。调用处 `pollTask(taskId, kind, retryCount+1)`。文件：`webui/src/views/DiscoveryView.vue`

### 3.9 第 2 波验证

- [ ] T039 [W2] 第 2 波回归：`python -m unittest discover tests` 全绿（含新增 test_concurrency / test_indexes）；`cd webui && npm run build` 通过；前端冒烟 pollTask 退避行为（断网模拟 5 次重试后 failed）。验证清单见 [quickstart.md](quickstart.md) 第 2 波章节
- [ ] T040 [W2] 第 2 波 commit：拆多个 conventional commits（`perf: batch N+1 queries` / `fix: wrap MAX+1 in transaction` / `fix: save_job UPSERT` / `perf: add DB indexes` / `fix: ai_settings_models return 502` / `fix: pollTask exponential backoff` 等），不 push（本地运行）
- [ ] T041 [W2] 第 2 波合并：本地 merge `feat/009-code-review-remediation` 到 master；合并后回到 plan.md 更新「后续波次」段，标注第 3 波激活时机

---

## Phase 4: 后续波次（待激活，不执行）

> 第 3+4 波在第 2 波合并后基于真实基线补 tasks。本段仅记录激活时的方向，不细化到任务级。

### 第 3 波 · 异常处理 + 前端状态机（待激活）

- except Exception 收窄（按 spec FR-3.1 区分库代码 / 子进程代码）
- 错误响应统一到 discovery envelope（B3）
- 收藏状态提升到 `webui/src/stores/favorites.ts`（B5）
- setPipelineResult 恢复全部运行时标识符（B6）
- _save_pipeline_job_to_store JD 一致（B7）
- AiSettingsDialog 状态清理 + AbortController
- 硬编码颜色替换为主题变量（FR-X.6）
- B-P2 状态机竞态 12 条（含 pollTimer 单变量升级 P1）
- DRY 重构（跳过审查报告原 DRY 第 2 条错误论断，仅执行 `_get_ai_credentials` / `fail(error, fallback)` / `TaskSnapshot` 统一 / `FieldLabel` interface / `VERDICT_LABELS` Record / `jobKey`-`jobId` 合并）

### 第 4 波 · 架构拆分（待激活，不含 store.py）

- app.py Blueprint 拆分（FR-4.1）
- DiscoveryView.vue 按步骤拆 4 子组件（FR-4.2）
- ChromeSessionManager 单例 + 引用计数（FR-4.3，补漏 L2594/L2604 调用点）
- 两套任务系统统一到 DB（FR-4.4）
- store.py 拆分按 spec 推迟触发条件挂起（不在第 4 波内）

---

## Dependencies（执行顺序）

```
T001 → Phase 2 全部任务（直接进入第 1 波，本地运行不开 issue）

Phase 2 内部：
- T002 / T003 / T004 / T005 / T006 / T007 / T008 / T009 / T010 / T011 / T012 / T014 / T015 / T016 / T017 / T018 / T019 / T021 全部 [P] 可并行（不同文件零依赖）
- T013 依赖 T010（_pipeline_tasks 改动在同一文件 app.py）
- T020 依赖 T005（types.ts 改动）+ 触发 DiscoveryView/JobWorkspace 适配
- T022 独立（核查任务，不阻塞）
- T023 依赖 Phase 2 全部完成
- T024 依赖 T023 验证通过

T024 → Phase 3 全部任务

Phase 3 内部（多数串行，store.py 改动需有序）：
- T025 / T026 先行（红测试）
- T027 → T028 → T029（三处事务包裹，串行避免冲突）
- T030（save_job UPSERT，独立于 T027-T029）
- T031（新增 list_jobs_by_ids）→ T032 / T033 / T034（三处调用方批量化，可并行）
- T035（索引）独立
- T036（cleanup）独立
- T037（HTTP 502）独立
- T038（pollTask）独立
- T039 依赖 Phase 3 全部完成
- T040 依赖 T039 验证通过
- T041 依赖 T040 本地合并

Phase 4 待激活，无依赖
```

---

## Parallel Opportunities（并行机会）

- **Phase 2**：18 个 [P] 任务可并行（不同文件），建议分批 commit：① 死代码删除批（T003-T009）② 重复 import + ref 修正批（T010-T012）③ constants + 小重构批（T014-T019）④ CI + types 批（T020-T021）
- **Phase 3**：T030 / T035 / T036 / T037 / T038 互相独立可并行；T032/T033/T034 在 T031 完成后可并行

---

## Independent Test Criteria（每波独立可验证）

- **第 1 波**：`uv sync` 成功 + `python -c "import flask, keyring, pypdf, docx"` 成功 + 死代码 Grep 无残留 + `python -m unittest discover tests` 全绿 + `npm run build` 通过
- **第 2 波**：新增 `test_concurrency.py` 与 `test_indexes.py` 全绿 + `EXPLAIN QUERY PLAN` 命中索引 + `ai_settings_models` 失败返回 502 + pollTask 5 次重试后 failed

---

## MVP Scope

第 1 波即可作为 MVP 合并（零风险清理，行为不变，测试全绿）。第 2 波在并发测试通过后作为增量合并。第 3+4 波在功能稳定期激活。

---

## Format Validation

- 所有任务格式：`- [ ] T### [P?] [W1|W2] 描述 + 文件路径` ✓
- Setup phase（T001）无波次标签 ✓
- 波次标签 [W1]/[W2] 用于区分执行波次 ✓
- 第 3+4 波作为占位段，不细化任务 ✓
- 每个任务都有明确文件路径 ✓
- 总任务数：41（Phase 1: 1，Phase 2: 23，Phase 3: 17，Phase 4 占位）✓
- AGENTS.md「提交流程」已明确本地运行不开 issue/PR，T001 直接创建分支进入第 1 波 ✓
