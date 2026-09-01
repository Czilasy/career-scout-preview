# Research: 抓取阻断正确化技术决策

**Created**: 2026-09-01 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

本文档解决 plan.md Technical Context 中的全部待定项。每条给出 Decision / Rationale / Alternatives considered。

---

## R1: 脚本主入口补捕获三类异常的退出码与失败行契约

**Decision**:
- `scripts/boss_cdp_raw.py` 的 `__main__` 在既有 `CDPUnavailableError` / `ConnectionError` / `ResultFileWriteError` 捕获之后，追加三个 `except` 块（薄映射，同 026 先例）：
  - `RiskControlError as exc` → `code = exc.code or "source_status_unclear"`；`emit_failure_line(code, str(exc))`；`sys.exit(10)`（10 = 风控/限流/验证码，`_EXIT_REASONS` 既有语义）
  - `LoginRequiredError as exc` → `emit_failure_line("source_login_required", str(exc))`；`sys.exit(1)`（1 = 登录失效，既有语义）
  - `RequestLimitExceededError as exc` → `emit_failure_line("source_request_limit_exceeded", str(exc))`；`sys.exit(11)`（11 = 请求上限，既有语义）
- import 行从 `from scripts.boss.exceptions import CDPUnavailableError, ResultFileWriteError` 扩展为补入 `RiskControlError, LoginRequiredError, RequestLimitExceededError`。
- **失败行是分类权威**（016 FR-006）：webui `_classify_failed_code` 先 `parse_failure_line` 解析失败行，失败行优先于退出码；退出码只作缺失败行时的兜底。因此即使某异常 code 缺失，失败行也会带精确码，不会退回 `source_unknown_error`。

**Rationale**: 已核实 `RiskControlError` 自带 `code` 属性（016 后入口统一以结构化失败行输出）；`LoginRequiredError` 在 programmatic 入口等价 CLI exit 1；`RequestLimitExceededError` 在 in-process 映射为退出码 11。子进程模式此前缺这三个捕获，导致裸 traceback exit 1 → webui 缺失败行、退出码 1 不认 → `source_unknown_error`。补捕获即恢复"结构化失败行"权威通道。

**Alternatives considered**:
- 在 `scripts/boss/programmatic.py` 或 `cli.py` 内层捕获：子进程 CLI 的最终出口是 `boss_cdp_raw.py::__main__`，只有顶层捕获才覆盖所有入口；内层捕获会漏掉异常穿透路径。
- 让 webui 侧对退出码 1 做更宽泛的关键词扫描：016 已明确删除全文关键词扫描（误报回归），不回归。

---

## R2: `fetch_details_batch` 非零退出读事件文件的归类语义

**Decision**:
- 现非零退出分支（`source_boss_cdp_detail.py` 281-308 行）改为：先 `_read_events_file(events_output_path)` 读事件文件，对每个缺失岗位用事件中的真实 `safe_code` 归类；无事件记录的岗位沿用 `_classify_failed_code(returncode, captured)` 兜底。
- 事件文件成功路径（returncode==0）的逐岗位归类逻辑（status=completed→success；unavailable/failed→safe_code 作为 failed_code；cancelled→safe_code 或 source_unknown_error）**保持不变**，非零退出分支复用同样的 `_validate_detail_event` 校验。
- 账号级码（`source_verification_required` / `source_rate_limited` / `source_login_required` / `source_blocked`）出现在事件中时，对缺失岗位按该码标失败，并推进熔断器 `record_signal`（`SIGNAL_CODES` 已含这四类，连续 2 次开闸 → 暂停 + 友好提示 + 断点续跑，既有路径）。
- 事件文件缺失/读不到（`_read_events_file` 返回空列表，含文件不存在、超 `max_artifact_bytes`、解析失败）→ 回退 `_classify_failed_code`，不崩溃（FR-007）。

**Rationale**: 已核实详情脚本 `_emit_detail_safe_event` 在每个岗位 terminal 时写入 `{kind:"detail", status, job_id=job_link, duration_ms, safe_code}`，账号级阻断（如 `DetailRateLimitedError`）在脚本侧已映射为 `safe_code="source_rate_limited"`。非零退出分支此前不读事件文件、只用退出码粗分类，导致真实原因二次丢弃；读事件文件即恢复逐岗位真实分类。

**Alternatives considered**:
- 保持退出码粗分类（现状）：真实原因丢失，MOM 实证 48 个岗位全 `source_unknown_error`，不可接受。
- 全批统一用退出码分类、忽略事件文件：会丢失"同一批内单条软失败 vs 账号级阻断"的区分，违反用户"待确认只留带原因的单条软失败"的确认边界。

---

## R3: 行数门禁与迁出（`source_boss_cdp_detail.py` 800 行，贴线）

**Decision**:
- `fetch_details_batch` 非零退出分支内部改造，净增行数 ≤15，控制在 800 红线内；不新增独立方法（内联复用既有 `_read_events_file`，并复用既有 `_validate_detail_event` 的语义）。
- **已实施迁出**：事件归类纯函数（`index_events_by_url` / `event_outcome_code`）落在新模块 `webui/source_boss_detail_events.py`（等价搬运，不改行为，同 033 先例），`source_boss_cdp_detail.py` 保留调用入口；非零退出路径用新模块做索引与归类，`_validate_detail_event` 完整校验仍由成功路径（returncode==0）承担。

**Rationale**: 033 已验证该先例（行数紧贴红线时等价迁出）；事件归类属于 detail mixin 既有域，迁出到新模块不改变模块地图职责。实测 `source_boss_cdp_detail.py` 800 行贴线，迁出保证后续改动不破红线。

**Alternatives considered**:
- 不设上限直接改：违反宪法 II 单文件尺寸红线。
- 提前拆分：属于 031 工程还债范围，本功能不扩大拆分范围。

---

## R4: 测试策略（假样本回归，用户已确认）

**Decision**:
- 新增 `tests/test_scrape_block_classification.py`，覆盖：
  1. **脚本侧**：`RiskControlError(code="source_verification_required")` / `LoginRequiredError` / `RequestLimitExceededError` 经 `boss_cdp_raw.__main__` 薄映射（patch 或直接调用薄映射逻辑）→ 输出失败行 + 正确退出码；
  2. **webui 分类侧**：`_classify_failed_code` 对上述失败行解析出精确账号级码（而非 `source_unknown_error`）；
  3. **非零退出归类**：构造事件文件（含账号级码 + 单条软失败码）→ `fetch_details_batch` 非零退出时逐岗位归类正确、账号级码推进熔断信号、软失败码不进"待确认"语义；
  4. **兜底**：事件文件缺失 → 回退 `_classify_failed_code`，不崩溃。
- 真实账号冒烟：源码模式真实小抓取一次（正常场景），确认基本盘无回归（用户确认的验收第 4 条）。

**Rationale**: 用户质询 4 已确认：不去真实触发拦截（不可控、伤账号），用假样本验证"认得出→停得下→提示对" + 真实账号正常抓取冒烟。

**Alternatives considered**:
- 真实触发验证码/限流：不可控且可能伤账号，用户已明确排除。
- 只做单元测试不做冒烟：无法确认基本盘无回归，用户验收要求包含真实账号冒烟。

---

## 决策汇总

| 决策点 | 结论 |
|---|---|
| R1 脚本补捕获 | `__main__` 薄映射三类异常 → 失败行 + 退出码 10/1/11；失败行为分类权威 |
| R2 非零退出归类 | 读事件文件按真实 safe_code 逐岗位归类；无事件回退退出码；账号级码走熔断器 |
| R3 行数门禁 | ≤800；事件归类纯函数已迁出至 `webui/source_boss_detail_events.py`（等价搬运，实测主文件 800 贴线） |
| R4 测试策略 | 假样本回归（脚本+webui+非零退出+兜底）+ 真实账号正常冒烟 |
