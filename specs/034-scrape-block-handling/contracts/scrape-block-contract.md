# Contract: 抓取阻断接线契约（脚本 ↔ webui）

**Created**: 2026-09-01 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

本文档描述本次接线涉及的既有契约与新增落点，供实现与测试对齐。**不新增对外接口。**

---

## C1: 脚本主入口异常 → 失败行 + 退出码（新增，scripts/boss_cdp_raw.py __main__）

| 异常 | emit_failure_line code | 退出码 | 语义 |
|---|---|---|---|
| `RiskControlError` | `exc.code or "source_status_unclear"` | 10 | 风控/限流/验证码/登录失效/无法确认 |
| `LoginRequiredError` | `source_login_required` | 1 | 登录态失效 |
| `RequestLimitExceededError` | `source_request_limit_exceeded` | 11 | 请求数上限 |

- 失败行格式（既有）：`__CAREERSCOUT_FAILED__ code=<code> hint=<hint>`
- 契约：**失败行是分类权威**（016 FR-006）；退出码仅作缺失败行时的兜底
- 与既有捕获（`CDPUnavailableError`→2、`ConnectionError`→2、`ResultFileWriteError`→4）同构，同落点 `__main__`

## C2: 详情批非零退出 → 事件文件逐岗位归类（新增，webui/source_boss_cdp_detail.py）

- 子进程 `returncode != 0` 时：
  1. 读 `.events.jsonl`（复用 `_read_events_file`，文件不存在/过大/解析失败 → 空列表）
  2. 逐岗位按事件归类：
     - `status=completed` 且 detail 记录存在 → `success`
     - `status=unavailable|failed` → `failed_code = event.safe_code`
     - `status=cancelled` → `safe_code` 或 `source_unknown_error`
     - 无事件记录 → `failed_code = _classify_failed_code(returncode, captured)`
  3. 账号级码（`SourceCircuitBreaker.SIGNAL_CODES`：login_required / verification_required / rate_limited / blocked）推进 `record_signal`（连续 2 次开闸）
- 已落盘产物抢救（`_read_combined_details`）保持既有行为：已抓标成功、缺失才标失败

## C3: 兜底（FR-007）

- 事件文件缺失/读不到 → 回退 `_classify_failed_code`，不崩溃、不伪造原因
- 事件隐私字段校验（`_validate_detail_event`）保持既有

## C4: 禁止改动面

- 错误码注册表（`webui/error_registry.py`）：既有码已完备，不新增、不改文案
- 熔断器（`webui/source_breaker.py`）、暂停/续跑（`task_pause_support.py`、`task_continue_api.py`）：既有机制，不动
- 前端（`webui/src/`）、数据库：不动
