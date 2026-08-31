# 日志契约（Logging Contract）

**Created**: 2026-09-01 | **Spec**: [spec.md](../spec.md)

本契约约束"日志白箱"功能涉及的日志写入与读取行为，供实现与测试对齐。

## 1. 日志行格式

写盘格式（由 `logging_setup.py` 的 `RedactingFormatter` 定义，既有）：

```text
%(asctime)s %(levelname)s %(name)s task=%(task_id)s corr=%(correlation_id)s %(message)s
```

- `asctime`：本地时间戳
- `levelname`：DEBUG/INFO/WARNING/ERROR/CRITICAL
- `name`：logger 名（统一 `career_scout.<模块>` 树内）
- `task_id`：任务编号（`bind_task_context` 注入；无任务时为 `-`）
- `correlation_id`：关联编号（运行 run 级；无时为 `-`）
- `message`：内容，经脱敏（凭据形状值替换为 `[REDACTED]`）

## 2. 统一 logger 约定

- 业务代码一律通过 `webui.logging_setup.get_logger(...)` 取 logger，禁止 `logging.getLogger`。
- `get_logger(name)` 返回 `career_scout.<name>` 子 logger。
- 模块级默认约定 `_logger = get_logger(__name__)`。
- **懒初始化**：`get_logger` 首次调用且 `career_scout` 尚无 handler 时，自动 `configure_logging()`；测试上下文（`sys.modules` 含 pytest/unittest）写入系统临时目录。
- 懒初始化会从环境变量 `CAREER_SCOUT_CORRELATION_ID` 绑定 run 级上下文（子进程侧），使子进程日志行 `corr=` 关联到发起它的任务/运行。
- 已配置（`is_configured()` 为真）时懒初始化不重复配置。

## 3. 进程与级别

| 进程 | 日志级别 | 说明 |
|---|---|---|
| 主进程（webui） | DEBUG（默认） | 开发诊断完整 |
| 抓取子进程 | INFO | 经 `CAREER_SCOUT_LOG_LEVEL=INFO` 注入；关键现场（登录/限流/风控/详情结果）均可落盘，debug 噪音不刷爆日志 |

## 4. 多进程写安全

- 主进程与子进程各持独立 handler，追加写同一 `career-scout.log`（`mode='a'`）。
- 轮转（满 5MB）由 `SafeRotatingFileHandler.doRollover` 持有跨进程文件锁（`career-scout.log.lock`）。
- 任何写/轮转 `OSError` 降级：跳过本轮轮转继续追加，不崩溃；文件被删则重建。

## 5. 已识别的裸日志清单（本功能整改后须为 0）

| 位置 | 处置 |
|---|---|
| `webui/source_boss_cdp.py:35` | 删（死代码） |
| `webui/source_boss_cdp_detail.py:32` | 改 `get_logger` |
| `webui/source.py:98` | 删（死代码） |
| `webui/updater.py:41` | 改 `get_logger` |
| `scripts/boss/constants.py:393` | 改 `get_logger("boss_cdp")` |
| `webui/error_registry.py:30` | 改 `get_logger`（统一封装） |

## 6. 卫生检查强制项

- 禁止裸 `logging.getLogger`（豁免：`logging_setup.py` 自身、`ai_raw_log.py` 内部、`tests/` 整目录）。
- "except 不留痕"：ExceptHandler body ≤2 条且无 `Call`/`Raise`/`Return`/`Continue`/`Break`/属性赋值 → 失败。

## 7. 现场记录点（白箱要求）

| 位置 | 记录内容 |
|---|---|
| `scripts/boss/detail_scrape.py` `_emit_detail_safe_event` | 每岗位详情 terminal（job_id/status/safe_code），位于事件回调判断之前，单条详情同样留痕 |
| `scripts/boss/search.py` | 风控/限流判定，覆盖首次判定与重试后最终结论（verdict/code/hint） |
| `scripts/boss/login.py` | 登录成功（INFO）与失败（ERROR） |
| `scripts/zhilian/search.py` `_risk_signal` | 风险信号（verification/rate_limited/blocked/login_required/unreachable） |
| `webui/task_runners.py` 外层兜底 | 任务级未预期异常（异常类型+堆栈，FR-009） |
