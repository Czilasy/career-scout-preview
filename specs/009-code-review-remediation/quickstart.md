# Quickstart: 009 代码审查整改

**Plan**: [plan.md](plan.md) | **Date**: 2026-07-23

> Phase 1 输出。端到端验证指南：每波完成后如何跑一遍证明功能没坏 + 新增能力生效。

---

## 前置条件

- Python 3.10+（含 sqlite3 模块）
- Node 18+、npm
- uv（Python 包管理）
- 本地 Chrome（仅第 3+4 波冒烟需要，第 1+2 波不需要）
- 工作目录：`d:\项目\boss`

---

## 第 1 波验证（零风险清理）

### 1. 依赖一致性（FR-1.1）

```powershell
uv sync
python -c "import flask, keyring, pypdf, docx; print('all imports ok')"
```

**期望**：`uv sync` 无报错；import 全部成功输出 `all imports ok`。

### 2. 死代码无残留（FR-1.2）

```powershell
# 后端死代码
Select-String -Path "scripts\boss_cdp_raw.py" -Pattern "def append_json" | Should -BeNullOrEmpty
Select-String -Path "webui\src\App.vue" -Pattern "currentProfile\b" | Where-Object { $_ -notmatch "currentProfileId" } | Should -BeNullOrEmpty
Select-String -Path "webui\src\types.ts" -Pattern "SelectOption" | Should -BeNullOrEmpty
Select-String -Path "webui\src\views\DiscoveryView.vue" -Pattern "groupsForResult" | Should -BeNullOrEmpty

# 前端死样式（应全部无命中）
foreach ($cls in @("view-tabs","compact-heading","resume-suggest-row","execution-row","file-input-button","compact-check","execution-keyword","compact-number","execution-button","filter-summary-line","run-status-strip","zone-group-label","zone-toolbar","confirm-copy","inline-alert")) {
  $hits = Select-String -Path "webui\src\*.vue","webui\src\**\*.vue" -Pattern $cls
  if ($hits) { Write-Error "dead class $cls still referenced: $hits" }
}
```

**期望**：全部命令无输出（无残留引用）。

### 3. 重复 import 清理（FR-1.3）

```powershell
python -m py_compile scripts\boss_cdp_raw.py webui\app.py webui\store.py
# 应无报错

Select-String -Path "webui\app.py" -Pattern "import threading as _threading" | Should -BeNullOrEmpty
Select-String -Path "webui\store.py" -Pattern "from datetime import" | Select-Object -First 5
# 顶层 L18 应保留，函数内 L1788/1983/2009/3304 应已删除
```

### 4. BaseDialog ref 修正（FR-1.4）

```powershell
Select-String -Path "webui\src\components\BaseDialog.vue" -Pattern "^let previousFocus" | Should -BeNullOrEmpty
Select-String -Path "webui\src\components\BaseDialog.vue" -Pattern "ref<HTMLElement" | Should -Not -BeNullOrEmpty
```

### 5. _pipeline_tasks 清理机制（FR-1.5）

```powershell
# 启动 webui，触发一个 pipeline 任务进入终态，等待 30 分钟后检查 dict 已清空
# （冒烟用：跑单元测试，新增测试用例验证终态后 30s 内清理）
python -m unittest tests.test_pipeline_tasks_cleanup -v
```

### 6. CI workflow（FR-1.6）

```powershell
# 本地模拟 CI 跑一遍
python -m unittest discover tests
python -m py_compile scripts\boss_cdp_raw.py
cd webui; npm ci; npm run build; cd ..
```

**期望**：三步全绿。PR 上 GitHub Actions 自动跑 `.github/workflows/ci.yml`。

### 7. 第 1 波整波回归

```powershell
python -m unittest discover tests
cd webui; npm run build; cd ..
```

**期望**：全绿。

---

## 第 2 波验证（性能 + 竞态修复）

### 1. 并发安全（FR-2.1, FR-2.2）

```powershell
python -m unittest tests.test_concurrency -v
```

**期望**：
- `test_append_log_concurrent`：2 线程 × 100 条并发追加，无 `sqlite3.IntegrityError`，最终 seq 1-200 全部存在
- `test_save_job_concurrent`：2 线程同时 save_job 同一 canonical_url，只 1 条记录，字段不丢

### 2. N+1 批量化（FR-2.3）

```powershell
python -m unittest tests.test_store_queries -v
# 新增测试：构造 1000 条 candidate_analyses，断言 list_analyses 调用 DB 次数从 N+1 降到 1
```

**期望**：测试通过；`list_analyses` 在 1000 条数据下耗时下降 ≥ 50%（可选：`timeit` 对比）。

### 3. 索引命中（FR-2.4）

```powershell
python -m unittest tests.test_indexes -v
```

**期望**：
- `EXPLAIN QUERY PLAN` 对 `cleanup_expired_jobs` 的 SQL 输出包含 `SEARCH jobs USING INDEX idx_jobs_expires_at`
- `EXPLAIN QUERY PLAN` 对 `discovery_job_snapshots WHERE run_id=? AND fetch_status=?` 输出包含 `USING INDEX idx_discovery_job_snapshots_run_status`

### 4. HTTP 语义修正（FR-2.5）

```powershell
# 启动 webui
python webui\app.py
# 另一终端：用错误 API key 触发 502
Invoke-WebRequest -Uri "http://127.0.0.1:5000/api/ai-settings/models" -Method GET | Select-Object StatusCode
# 期望：502（之前是 200）
```

### 5. pollTask 退避（FR-2.6）

```powershell
# 前端冒烟：启动 webui，开 discovery，让后端持续返回 5xx（如断网）
# 观察网络面板：重试间隔 4s → 8s → 16s → 32s → 64s，5 次后状态变 "failed"，停止轮询
```

### 6. 第 2 波整波回归

```powershell
python -m unittest discover tests
cd webui; npm run build; cd ..
```

**期望**：全绿。

---

## 第 3+4 波验证（激活时补）

第 3+4 波不在本 quickstart 范围内，激活时基于真实基线补验证指南。大致方向：

- 第 3 波：错误响应统一后，前端 `ApiError` 适配；收藏状态全局 store 后切 profile 不丢；状态机竞态修复后人工冒烟各 step
- 第 4 波：app.py Blueprint 拆分后逐路由冒烟；DiscoveryView 拆分后逐 step 冒烟；ChromeSessionManager 引用计数日志可观测

---

## 故障排查

### `uv sync` 失败

- 检查 `pyproject.toml` 主 dependencies 块是否含 keyring/pypdf/python-docx
- 删 `uv.lock` 重跑 `uv sync`（最后手段）

### `npm run build` 报 TypeScript 错

- 第 1 波删 `JobItem` 索引签名后，原本依赖 `[key: string]: unknown` 的字段访问会暴露
- 在 `webui/src/types.ts` 加 `extra?: Record<string, unknown>` 收容后端透传字段
- 语义重叠字段（`verdict_reason`/`reason` 等）二选一，全仓统一

### 并发测试偶发失败

- `BEGIN IMMEDIATE` 在 Windows SQLite 表现可能略有差异
- 检查 sqlite3 模块版本：`python -c "import sqlite3; print(sqlite3.sqlite_version)"`
- 若 < 3.35，FR-2.2 退化路径用 `BEGIN IMMEDIATE` 包裹 SELECT-then-UPDATE
