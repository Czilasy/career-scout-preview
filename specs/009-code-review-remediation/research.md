# Research: 009 代码审查整改

**Plan**: [plan.md](plan.md) | **Date**: 2026-07-23

> Phase 0 输出。聚焦第 1+2 波的技术决策与备选方案。第 3+4 波的决策留待激活时补。

---

## 决策 1：uv.lock 修复方式

**Decision**：直接 `uv sync` 让 lock 补全传递依赖。

**Rationale**：
- `pyproject.toml` 已正确声明 keyring/pypdf/python-docx，只是 uv.lock 没锁
- `uv sync` 不改 pyproject.toml，只重算 lock 文件
- 验证命令 `python -c "import flask, keyring, pypdf, docx"` 直接覆盖三个缺失包

**Alternatives considered**：
- 手动编辑 uv.lock 加 package 条目 → 易错，传递依赖（lxml / typing_extensions / jeepney/secretstorage 等）手算易漏
- 删 uv.lock 让 uv 重新生成全量 → 风险大，可能改变已有包的版本约束

---

## 决策 2：A1 三处 MAX+1 竞态的修复路径

**Decision**：首选 `BEGIN IMMEDIATE` 包裹（与同文件 `create_confirmation_v2` L2289 一致），不选 AUTOINCREMENT。

**Rationale**：
- AUTOINCREMENT 只解决主键冲突的"不报错"，不解决"两个并发都拿到 next_seq=5、一个成功一个失败"的语义层竞态
- `BEGIN IMMEDIATE` 让 SELECT 和 INSERT 在同一事务内原子执行，并立即获取写锁，第二个并发会等待
- 同文件 `create_confirmation_v2` 已用此模式，可作参照，避免引入第二种风格
- 退路：若 `BEGIN IMMEDIATE` 在某些 SQLite 版本表现不佳，改为单语句 `INSERT ... VALUES (?, (SELECT COALESCE(MAX(seq),0)+1 FROM task_logs WHERE task_id=?), ...)`，把两步压成一步

**Alternatives considered**：
- AUTOINCREMENT 列 → 见上，治标不治本
- 应用层加锁（threading.Lock）→ 单进程内有效，但若 webui 多 worker（gunicorn -w 4）就失效，不通用

**验证**：新增 `tests/test_concurrency.py`，2 线程同时对同一 task_id 追加 100 条日志，断言无 `sqlite3.IntegrityError` 且最终 seq 连续 1-200。

---

## 决策 3：A3 save_job UPSERT 的 SQLite 版本兼容

**Decision**：先查 SQLite 版本，≥ 3.35 用 `INSERT ... ON CONFLICT(canonical_url) DO UPDATE SET ... RETURNING id`；< 3.35 退化为 `BEGIN IMMEDIATE` 包裹的现有 SELECT-then-UPDATE。

**Rationale**：
- `RETURNING` 在 SQLite 3.35（2021-03-12）引入，Windows Python 3.10+ 自带的 sqlite3 模块通常 ≥ 3.35
- 但 GitHub Actions ubuntu-latest 的 sqlite 版本需在 CI 中确认
- 退化路径与决策 2 一致，无新风格

**Alternatives considered**：
- 一律用 `INSERT ... ON CONFLICT DO UPDATE` 不带 RETURNING，然后再 SELECT id → 多一次查询，不如 RETURNING 干净
- 用 `cursor.lastrowid` → 只对 INSERT 有效，UPDATE 路径拿不到

**验证**：`tests/test_concurrency.py` 加用例：2 线程同时 save_job 同一 canonical_url，断言只产生 1 条 jobs 记录且字段不丢。

---

## 决策 4：A5 N+1 三处批量化的统一模式

**Decision**：新增 `TaskStore.list_jobs_by_ids(ids: list[str]) -> dict[str, dict]`，返回 `{job_id: row}` 内存 dict，三处调用方一次性取回再内存匹配。

**Rationale**：
- 三处 N+1 模式一致（每行查一次 get_job），统一一个批量方法避免重复
- 内存 dict 匹配复杂度 O(1)，比 JOIN 或 IN 子查询更易读
- `list_analyses` 的 candidate_profile_version_id 查询用一次 `WHERE analysis_id IN (...)` 批查替代循环内单查

**Alternatives considered**：
- JOIN 查询 → 一次 SQL 拿全部，但 `list_analyses` 的解码逻辑复杂（含 JSON 解码），JOIN 后行结构变深，可读性差
- 在循环内开连接复用同一 connection → 仍 N 次查询，只省了连接开销，治标不治本

**验证**：构造 1000 条 candidate_analyses 数据，对比 `list_analyses` 改造前后耗时，断言下降 ≥ 50%。

---

## 决策 5：A6 索引创建策略

**Decision**：新增 migration（编号接现有最大值 +1），创建 3 个索引：
- `idx_jobs_expires_at`（partial: `WHERE expires_at IS NOT NULL`）
- `idx_jobs_last_seen_at`
- `idx_discovery_job_snapshots_run_status`（复合: `run_id, fetch_status`）

**Rationale**：
- partial index 避免索引行数为 NULL 的数据，节省空间且查询更准
- `(run_id, fetch_status)` 复合索引覆盖按 run 过滤 + 按 status 过滤两种查询模式
- 用 `CREATE INDEX IF NOT EXISTS` 幂等，重跑 migration 不报错

**Alternatives considered**：
- 全表索引（非 partial）→ expires_at 为 NULL 的行也进索引，浪费空间
- 应用层缓存 → 单进程有效，多 worker 失效；且引入缓存一致性问题

**验证**：新增 `tests/test_indexes.py`，用 `EXPLAIN QUERY PLAN` 断言查询计划包含 `SEARCH ... USING INDEX idx_xxx`，而非 `SCAN`。

---

## 决策 6：A8 ai_settings_models 失败状态码

**Decision**：返回 **502 Bad Gateway**。

**Rationale**：
- 失败原因是上游 AI 服务（OpenAI 兼容端点）拉取模型失败，属"上游网关错误"语义
- 与同文件 `ai_settings_test` 失败时的状态码保持一致（核查时确认）
- 前端已读 `ok` 字段区分成功失败，状态码修正后前端无须额外适配，但调试时 HTTP 状态码语义更清晰

**Alternatives considered**：
- 400 Bad Request → 客户端错误语义不对，请求本身没语法错误
- 500 Internal Server Error → 服务端错误语义过重，本服务没崩，只是上游拉取失败
- 保持 200 + ok=False → 违反 HTTP 语义，前端难用标准拦截器区分

---

## 决策 7：A9 pollTask 退避策略

**Decision**：引入 `retryCount` 参数，上限 5，指数退避（4s/8s/16s/32s/64s），达上限后状态写 `"failed"` 停止轮询；失败中态用 `"retrying"`。

**Rationale**：
- 5 次重试 + 指数退避覆盖大多数临时网络故障，避免后端持续 5xx 时空轮询耗资源
- `"retrying"` 中态与 `"failed"` 终态分离，UI 可显示"重试中…"与"失败，请手动重试"两种文案
- 64s 上限避免退避过长影响用户体验

**Alternatives considered**：
- 固定 4s 重试无上限 → 现状，断网时无限轮询
- 线性退避（4s/8s/12s/...）→ 退避不够快，5 次才到 20s，仍可能压垮后端
- 指数退避 + 抖动（jitter）→ 更优但实现复杂度高，单用户本地应用场景过度设计

---

## 决策 8：第 1 波 _pipeline_tasks 清理机制

**Decision**：任务进入终态（succeeded/failed/cancelled）后启动 30 分钟定时器，到期自动 `_pipeline_tasks.pop(task_id, None)`；进程退出时整体清理。

**Rationale**：
- 30 分钟覆盖用户查看任务结果 + 刷新页面的典型窗口
- 终态判定清晰（status in {"succeeded", "failed", "cancelled"}），无歧义
- 用 `threading.Timer` 实现，单进程内有效，不引入新依赖

**Alternatives considered**：
- LRU 淘汰 → 需引入 OrderedDict 或 functools.lru_cache，复杂度高；且 LRU 按访问淘汰，不看任务状态，可能淘汰正在查看的结果
- 引用计数 + 立即清理 → 前端轮询期间引用不为 0，清理时机难定；30 分钟定时器更简单
- 不清理（保持现状）→ 内存单调增长，长跑必崩

---

## 决策 9：第 1 波 CI workflow 设计

**Decision**：`.github/workflows/ci.yml` 拆 2 个 job：
- `python-tests`：`python -m unittest discover tests` + `python -m py_compile scripts/boss_cdp_raw.py`
- `frontend-build`：`npm ci && npm run build`（前端失败不阻断 Python 提交，单独 job）

**Rationale**：
- Python 与前端独立 job，失败互不影响，PR 检查状态更清晰
- `npm ci` 而非 `npm install`，确保 lockfile 一致
- 不跑真实 Chrome 集成测试（tests/ 全 mock，且 CI 环境装 Chrome 复杂）

**Alternatives considered**：
- 单 job 串行跑 → 前端失败会阻断 Python 测试结果，反之亦然
- 加 pre-commit hook → 本地提交时跑，但用户单人维护，CI 已足够
- 加 lint job（ruff / eslint）→ 本 spec 范围外，留作后续待办

---

## 决策 10：第 1 波 constants.py 的内容边界

**Decision**：`webui/constants.py` 集中魔法数字，只收录后端 Python 侧（30 天清理、60 detail budget、12 reuse hours、5 feedback 阈值、100 limit 上限、50 日志尾部等）；前端魔法数字留作第 3 波处理（与硬编码颜色同批）。

**Rationale**：
- 后端魔法数字散落在 store.py / app.py / discovery_runner.py 多处，集中收益高
- 前端魔法数字多为 UI 时长（8000/5000/3000ms notice），与硬编码颜色同批做更连贯
- 拆两波避免第 1 波范围过大

**Alternatives considered**：
- 一次性收前后端 → 第 1 波过重，且前端常量与 CSS 变量耦合，需一起设计
- 不收，留作 P3 → 魔法数字散落是可读性问题，但每次改动都要查含义，值得在第 1 波清掉

---

## 决策 11：FR-X.4 动态拼 SET 子句的 SQL 注入核查

**Decision**：第 1 波先核查 `update_discovery_run`（store.py:2587）和 `update_profile_job`（store.py:1845）的字段名来源；若全部来自内部调用方（hardcoded 字段名字符串），加注释说明可信边界即可；若有任何字段名来自用户输入或外部数据，改白名单 dict 校验。

**Rationale**：
- spec 已把这条升级为 P1（审查报告原 P2）
- 第 1 波先核查再定级，避免无依据地改写代码
- 若可信，加注释比改白名单更小代价；若不可信，白名单是必须的

**验证**：核查后在 plan.md / tasks.md 更新结论；若不可信则补白名单单元测试。

### T022 核查结论（2026-07-23 落地）

**核查范围**：Grep 全仓 `update_discovery_run(` / `update_profile_job(` 的所有调用方（共 7 个文件：`webui/store.py`、`webui/discovery_runner.py`、`webui/app.py` 及 4 个测试文件）。

**结论**：两方法的所有调用方均使用 hardcoded Python 关键字参数（如 `status=`、`stage=`、`failure_code=`、`failure_stage=`、`counters={...}`、`started=`、`completed=`、`cancel_requested=` 等），**无任何字段名来自用户输入或外部数据**。`update_discovery_run` 的 `counters` dict 的 key 也已在方法内白名单校验（L2622-2625：13 个允许的 counter key 显式枚举）。`update_profile_job` 的 `status` 已通过 `PROFILE_JOB_STATUSES` 集合校验。

**处理**：在 `webui/store.py` L1848（`update_profile_job`）和 L2586（`update_discovery_run`）分别加注释 `# 字段名来自内部调用方（hardcoded），非用户输入，无需白名单` / `# 字段名来自内部调用方（hardcoded），非用户输入；counters key 有白名单`。**无需改白名单 dict**，可信边界已明确。

---

## 决策 12：FR-X.3 DRY 重构的边界修正

**Decision**：执行 DRY 重构时**跳过**审查报告原 DRY 第 2 条「`getCompany`/`verdictLabel` 在 DiscoveryView 与 JobWorkspace 各写一遍」，因为核查证实该论断错误（JobWorkspace 实际函数名是 `company`，DiscoveryView 中无 `verdictLabel`/`company` 函数，只有 `jobKey`/`jobId` 一对部分重复）。

**Rationale**：
- 审查报告该论断基于错误前提，执行会引入不存在的问题
- 只保留 `jobKey`/`jobId` 合并这一子项（编入第 3 波）
- 其余 DRY 子项（`_get_ai_credentials`、`fail(error, fallback)`、`TaskSnapshot` 统一、`FieldLabel` interface、`VERDICT_LABELS` Record 常量）按原计划执行

**Alternatives considered**：
- 强行执行原论断 → 会"修复"不存在的问题，引入噪音
- 把 JobWorkspace 的 `company`/`verdictLabel` 也搬到 DiscoveryView → DiscoveryView 根本不用，反而是死代码搬家

---

## 待激活决策（第 3+4 波，激活时补）

- 收藏状态用 pinia 还是简单 reactive 模块（plan 倾向简单 reactive，避免新依赖）
- 错误响应统一时 legacy 路由的 errorhandler 包装粒度
- app.py Blueprint 拆分的子模块切分边界（按域 vs 按资源）
- DiscoveryView 拆分时 composable 与子组件的状态切分
- ChromeSessionManager 引用计数的具体实现（计数器 + Timer 还是 weakref）
- 两套任务系统统一时 _pipeline_tasks 的迁移过渡策略

这些决策等第 2 波合并后基于真实基线再补，不在本 research 范围内。
