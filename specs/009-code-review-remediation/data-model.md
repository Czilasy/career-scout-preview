# Data Model: 009 代码审查整改

**Plan**: [plan.md](plan.md) | **Date**: 2026-07-23

> Phase 1 输出。本 spec 不新增业务实体，仅对现有 SQLite schema 做增量变更（索引 + migration）。表结构本身不动。

---

## 现有表（不动，仅作引用）

- `task_logs(task_id, seq, created_at, line)` — FR-2.1 改写入事务
- `candidate_analyses(id, resume_id, profile_id, version, ...)` — FR-2.1 改写入事务
- `direction_confirmations(id, profile_id, resume_id, analysis_id, version, ...)` — FR-2.1 改写入事务
- `jobs(id, canonical_url UNIQUE, source_url, title, company, salary, location, jd, first_seen_at, last_seen_at, expires_at)` — FR-2.2 改 UPSERT，FR-2.4 加索引
- `profile_jobs(profile_id, job_id, status, ...)` — FR-X.5 cleanup 改单 SQL
- `discovery_job_snapshots(run_id, job_id, fetch_status, ...)` — FR-2.4 加复合索引

---

## 增量变更

### 新增索引（FR-2.4）

通过新 migration（编号接现有最大值 +1）创建，幂等用 `IF NOT EXISTS`：

```sql
-- partial index：只索引有 expires_at 的行，节省空间
CREATE INDEX IF NOT EXISTS idx_jobs_expires_at
  ON jobs(expires_at) WHERE expires_at IS NOT NULL;

-- last_seen_at 高频过滤（cleanup、list_jobs_by_ids 排序）
CREATE INDEX IF NOT EXISTS idx_jobs_last_seen_at
  ON jobs(last_seen_at);

-- discovery_job_snapshots 按 run + status 过滤
CREATE INDEX IF NOT EXISTS idx_discovery_job_snapshots_run_status
  ON discovery_job_snapshots(run_id, fetch_status);
```

**验证**：`EXPLAIN QUERY PLAN` 对以下查询输出包含 `USING INDEX`：

```sql
EXPLAIN QUERY PLAN
  SELECT pj.profile_id, pj.job_id FROM profile_jobs pj
  JOIN jobs j ON pj.job_id = j.id
  WHERE pj.status = 'new' AND j.expires_at IS NOT NULL AND j.expires_at < ?;
-- 期望：SEARCH jobs USING INDEX idx_jobs_expires_at

EXPLAIN QUERY PLAN
  SELECT * FROM discovery_job_snapshots WHERE run_id = ? AND fetch_status = ?;
-- 期望：SEARCH discovery_job_snapshots USING INDEX idx_discovery_job_snapshots_run_status
```

---

### 现有方法签名变更（不破坏调用方）

#### `TaskStore.list_jobs_by_ids(ids: list[str]) -> dict[str, dict]`（新增）

FR-2.3 引入的批量查询方法：

```python
def list_jobs_by_ids(self, ids: list[str]) -> dict[str, dict]:
    """Batch fetch jobs by id list. Returns {job_id: row_dict}."""
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    with self._connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM jobs WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
    return {row["id"]: dict(row) for row in rows}
```

调用方改为一次性取回 + 内存 dict 匹配，替代循环内 `get_job`。

#### `TaskStore.append_log(task_id, line)`（事务包裹）

FR-2.1 改为：

```python
def append_log(self, task_id, line):
    with self._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM task_logs WHERE task_id = ?",
            (str(task_id),),
        ).fetchone()
        seq = int(row["next_seq"])
        conn.execute(
            "INSERT INTO task_logs (task_id, seq, created_at, line) VALUES (?, ?, ?, ?)",
            (str(task_id), seq, _now(), str(line)),
        )
        # BEGIN IMMEDIATE 在 with 块退出时自动 COMMIT
```

#### `TaskStore.save_job(...)`（UPSERT）

FR-2.2 改为（SQLite ≥ 3.35 路径）：

```python
def save_job(self, canonical_url, source_url, title, company, salary, location, jd, *, expires_at=None):
    ts = _now()
    with self._connection() as conn:
        row = conn.execute(
            """
            INSERT INTO jobs (id, canonical_url, source_url, title, company, salary, location, jd,
                              first_seen_at, last_seen_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_url) DO UPDATE SET
                source_url = excluded.source_url,
                title = excluded.title,
                company = excluded.company,
                salary = excluded.salary,
                location = excluded.location,
                jd = excluded.jd,
                last_seen_at = excluded.last_seen_at
            RETURNING id
            """,
            (_uuid(), canonical_url, source_url, title, company, salary, location, jd, ts, ts, expires_at),
        ).fetchone()
        return row["id"]
```

退化路径（SQLite < 3.35）：用 `BEGIN IMMEDIATE` 包裹现有 SELECT-then-INSERT/UPDATE，与 `append_log` 同模式。

---

## 不在 data-model 范围

- 错误响应结构 → 见 [contracts/http-api.md](contracts/http-api.md)
- 任务统一抽象（tasks 表 + kind 字段）→ 第 4 波，本 spec 不细化
- 收藏状态前端 store → 第 3 波前端改动，不涉及 DB schema
