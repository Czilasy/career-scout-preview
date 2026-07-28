# API Contracts: 健康流程补救与优化

**Date**: 2026-07-28

## 新增接口

### 1. GET `/api/version`

返回当前运行版本，用于确认前后端一致（FR-039, FR-040, SC-014）。

**Response 200**:
```json
{
  "backend_version": "2.2.0",
  "build_hash": "abc123def456",
  "build_time": "2026-07-28T10:00:00+08:00",
  "career_scout_version": "2.2.0"
}
```

**用途**：前端页脚显示版本；继续任务时校验版本一致；旧服务返回旧版本时前端提示刷新。

---

### 2. GET `/api/task-state/<run_id>`

统一任务状态接口，返回完整的任务画面（FR-007, FR-008, FR-023, SC-003）。

**Response 200**:
```json
{
  "run_id": "e6250f0ed794492180269de050bfd41a",
  "status": "paused",
  "current_stage": "jd_detail",
  "progress": {
    "total": 1408,
    "processed": 762,
    "success": 762,
    "failed": 38,
    "unstarted": 608,
    "dropped": 518,
    "overall_percent": 54.1
  },
  "counts": {
    "match": 198,
    "mismatch": 514,
    "pending": 696,
    "dropped": 518
  },
  "pause_info": {
    "error_code": "captcha_required",
    "error_reason": "触发验证码/滑块，需手动完成",
    "paused_at": "2026-07-27T16:30:00+08:00",
    "resume_condition": "用户完成验证码后点继续"
  },
  "backend_version": "2.2.0",
  "can_continue": true,
  "can_cancel": true
}
```

**守恒约束**：`match + mismatch + pending + dropped == total(=source_count)`

---

### 3. POST `/api/task/continue/<run_id>`

统一继续接口，适用于所有阶段（scrape/ai_rough/jd_detail/ai_fine）和所有任务类型（主流程/批量补救/单条补救）（FR-019, FR-020, FR-022, FR-029）。

**Request**:
```json
{
  "resume_from_stage": "jd_detail"
}
```

**Response 200**:
```json
{
  "ok": true,
  "run_id": "e6250f...",
  "new_status": "running",
  "block_check": {
    "passed": true,
    "checked_at": "2026-07-28T10:05:00+08:00"
  }
}
```

**Response 409**（阻断未解除）:
```json
{
  "ok": false,
  "error": "阻断条件未解除",
  "error_code": "captcha_required",
  "error_reason": "验证码仍存在，请先完成验证码",
  "new_status": "paused"
}
```

**Response 409**（重复继续，防并发）:
```json
{
  "ok": false,
  "error": "任务已在运行中，无需重复继续",
  "current_status": "running"
}
```

---

### 4. GET `/api/recovery/preview/<run_id>`

只读预演接口，核对 696 条异常的分类守恒（FR-041, SC-011）。

**Response 200**:
```json
{
  "run_id": "e6250f...",
  "total": 1926,
  "matched": 198,
  "mismatched": 514,
  "dropped": 518,
  "pending_50_struct": {
    "total": 50,
    "match_in_json": 17,
    "mismatch_in_json": 33
  },
  "pending_646_no_jd": {
    "total": 646,
    "failed_code": "historical_reason_unavailable",
    "reason": "旧流程未保存具体失败原因",
    "evidence": "粗筛保留但未进入精筛，且没有可核验的岗位级失败记录",
    "next_action": "recrawl_jd"
  },
  "processed_762_jd": 762,
  "conservation_ok": true,
  "expected_after_fix": {
    "match": 215,
    "mismatch": 547,
    "pending": 646,
    "dropped": 518
  }
}
```

**Response 409**（数字不一致）:
```json
{
  "ok": false,
  "error": "预演数字与事故证据不一致",
  "details": {
    "expected_50_struct": 50,
    "actual_50_struct": 48,
    "delta": -2
  },
  "conservation_ok": false
}
```

**保证**：此接口不写入任何数据。

---

### 5. POST `/api/recovery/prepare/<run_id>`

由服务端使用 SQLite Backup API 创建备份，并在独立备份目录写入 manifest。prepare 不写正式库的 `recovery_audit`，客户端不能指定备份路径。

**Response 201**:
```json
{
  "ok": true,
  "backup_id": "8f6a...",
  "status": "prepared",
  "backup_sha256": "860370...",
  "source_fingerprint": "12ab34..."
}
```

---

### 6. POST `/api/recovery/execute/<run_id>`

正式恢复接口，需通过全部门禁（FR-042~FR-047, SC-012, SC-013）。

**Request**:
```json
{
  "backup_id": "8f6a..."
}
```

服务端根据 `backup_id` 重新读取 manifest，并复核备份 SHA256、source fingerprint、恢复门禁、维护锁与幂等状态。客户端提供的路径、哈希或“测试已通过”布尔值均不可信且不接受。

**Response 200**:
```json
{
  "ok": true,
  "fixed_50_struct": {
    "match": 17,
    "mismatch": 33
  },
  "preserved_762_jd": 762,
  "pending_646_to_new_flow": 646,
  "final_conservation": {
    "total": 1926,
    "match": 215,
    "mismatch": 547,
    "pending": 646,
    "dropped": 518,
    "ok": true
  },
  "no_duplicate": true,
  "no_loss": true,
  "no_extra_ai_call": true
}
```

**Response 412**（门禁未通过）:
```json
{
  "ok": false,
  "error": "门禁未通过",
  "gate_failures": [
    "备份文件不存在",
    "预演数字不一致"
  ]
}
```

## 修改的现有接口

### GET `/api/latest-running-task`（扩展）

新增返回 `paused` 状态任务（从 DB 读取，不仅限内存）。

**Response 200**（新增 paused 场景）:
```json
{
  "task_type": "ai_screen",
  "run_id": "e6250f...",
  "status": "paused",
  "error_code": "captcha_required",
  "error_reason": "触发验证码/滑块，需手动完成",
  "paused_at": "2026-07-27T16:30:00+08:00",
  "recovered_from_db": true
}
```

### POST `/api/pipeline/recrawl`（修改）

只处理 screening_pending_results 中的岗位（不再取 verdict="uncertain"）。

### POST `/api/pipeline/jobs/<id>/jd`（修改）

单条补抓新增暂停机制（命中阻断时暂停，保存 checkpoint）。
