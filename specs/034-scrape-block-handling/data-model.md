# Data Model: 失败分类 / 阻断信号 / 事件文件契约

**Created**: 2026-09-01 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

本文档定义抓取阻断正确化涉及的既有数据/契约，及本次接线复用的语义。**不新增任何数据库表**。

---

## 1. 实体与字段

### 1.1 结构化失败行（既有，scripts/boss_cdp_signals.py）

- 格式：`__CAREERSCOUT_FAILED__ code=<code> hint=<hint>`（stdout 单行）
- `code`：`error_registry.py` 注册表错误码（如 `source_verification_required`、`source_rate_limited`、`source_login_required`、`source_request_limit_exceeded`、`source_status_unclear`、`source_unknown_error`）
- `hint`：可读原因，≤120 字符
- 权威性：webui `_classify_failed_code` 优先 `parse_failure_line` 解析失败行（016 FR-006）

### 1.2 退出码（既有语义，scripts/boss_cdp_raw.py + source_boss_helpers._EXIT_REASONS）

| 退出码 | 语义 | 本次状态 |
|---|---|---|
| 1 | 登录态失效或环境异常（LoginRequiredError） | 补捕获后显式 `emit_failure_line` + exit(1) |
| 2 | 连不上调试浏览器（CDPUnavailableError） | 既有，不动 |
| 3 | 抓取参数错误 | 既有，不动 |
| 4 | 结果文件写入失败（ResultFileWriteError） | 既有（026），不动 |
| 10 | 风控/限流/验证码/登录失效（RiskControlError） | 补捕获后显式 `emit_failure_line` + exit(10) |
| 11 | 请求数上限（RequestLimitExceededError） | 补捕获后显式 `emit_failure_line` + exit(11) |

### 1.3 终端安全事件（既有，.events.jsonl）

```json
{"kind": "detail", "status": "completed|unavailable|failed|cancelled",
 "job_id": "<job_link>", "duration_ms": 123, "safe_code": "<source_* 错误码>"}
```

- 写入：`scripts/boss/detail_scrape.py::_emit_detail_safe_event`
- 读取/校验：`webui/source_boss_cdp_detail.py::_read_events_file` / `_validate_detail_event`（隐私字段拒绝：JD body、凭据、PII）
- `safe_code` 允许值：`source_login_required`、`source_rate_limited`、`source_verification_required`、`source_invalid_output`、`ok`（cancelled 时的语义标记）等

### 1.4 错误码注册表（既有，webui/error_registry.py）

- `_SOURCE_CODES`：全部 `source_*` 码，含 `blocking` / `retryable` / `impact`（systemic/independent）/ `user_message`（对外中文提示）/ `resume_condition`
- `SYSTEMIC_BLOCK_CODES`：由 `blocking && impact==systemic` 自动推导的硬阻断集合
- 账号级阻断码（本次相关）：`source_verification_required`、`source_rate_limited`、`source_account_restricted`、`source_login_required`、`source_blocked`、`source_request_limit_exceeded`、`source_cdp_unavailable`、`source_unreachable`

### 1.5 熔断器（既有，webui/source_breaker.py）

- `SIGNAL_CODES = {source_login_required, source_verification_required, source_rate_limited, source_blocked}`
- 连续 2 次信号开闸 → `open_failure_code()` 透传 `last_signal` → 暂停 + 友好提示 + 断点续跑（既有路径）

---

## 2. 状态流转

### 2.1 脚本子进程退出（列表/详情通用）

```
RiskControlError/LoginRequiredError/RequestLimitExceededError
  → [新增] __main__ 薄映射捕获
  → emit_failure_line(<精确码>, hint) + sys.exit(10|1|11)
  → webui _classify_failed_code 解析失败行 → 精确账号级码（非 source_unknown_error）
  → 熔断器 record_signal（连续 2 次开闸）→ 暂停 + 友好提示 + 断点续跑
```

### 2.2 详情批非零退出（webui 侧）

```
returncode != 0
  → 读 .events.jsonl（复用 _read_events_file / _validate_detail_event）
  → 逐岗位：
     事件 status=completed 且有 detail → success
     事件 status=unavailable/failed → failed_code = 事件 safe_code
     事件 status=cancelled → safe_code 或 source_unknown_error
     无事件记录 → 回退 _classify_failed_code(returncode, captured)
  → 账号级码（SIGNAL_CODES）岗位推进 record_signal
  → 单条软失败码岗位 → 带原因落"待确认"
```

### 2.3 账号级 vs 单条软失败分流（用户确认边界）

| 类型 | 错误码示例 | 处置 |
|---|---|---|
| 账号级阻断 | verification_required / rate_limited / login_required / blocked / request_limit_exceeded | 熔断信号 → 暂停；不进"待确认" |
| 单条软失败 | invalid_output / timeout / 状态 unclear 类 | 带原因进"待确认"，任务不中断 |

---

## 3. 校验规则

- 事件 `safe_code` 必须是已注册码；未知码经 `resolve_code` 兜底为 `internal_error`（既有逻辑）
- 事件文件缺失/读不到 → 回退退出码分类，不崩溃（FR-007）
- 事件隐私字段（JD body、凭据、PII）→ `_validate_detail_event` 拒绝（既有）
