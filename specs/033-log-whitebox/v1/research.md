# Research: 日志白箱技术决策

**Created**: 2026-09-01 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

本文档解决 plan.md Technical Context 中的全部待定项。每条给出 Decision / Rationale / Alternatives considered。

---

## R1: "自动开账本"（get_logger 懒初始化）与测试隔离

**Decision**:
- `webui/logging_setup.py` 的 `get_logger()` 首次被调用且 `is_configured()` 为假时，自动调用 `configure_logging()`，保证任何进程任何入口（源码/EXE/子进程/CLI 直跑）只要用了统一封装，日志必有落点。
- `configure_logging()` 的默认日志目录解析顺序不变：`CAREER_SCOUT_LOG_DIR` env → 默认 `~/.career-scout/logs`。
- 新增测试上下文检测：懒初始化时若 `sys.modules` 中存在 `pytest` 或 `unittest`（测试运行环境），日志目录落到系统临时目录（`tempfile.gettempdir()/career-scout-test-logs`），不污染正式用户目录，符合测试纪律"日志一律用系统临时目录"。
- 懒初始化不覆盖已有配置（沿用 `force=False` 语义）；测试代码已显式 `configure_logging(tmp, force=True)` 时不会被打扰（既有 handler 存在则直接返回）。

**Rationale**: 全仓库此前只有 `webui/app.py:210` 一处调用 `configure_logging`，导致子进程与部分脚本入口"日志裸奔"。懒初始化把"有落点"从"入口记得调"变成"用了就有"，是"永恒白箱"的运行时保证。测试隔离用 `sys.modules` 检测而非强制测试改基建，改动面最小；已有 `tests/test_log_api.py` 使用 `CAREER_SCOUT_LOG_DIR` 的先例保持兼容。

**Alternatives considered**:
- 在每个子进程/脚本入口手动加 `configure_logging()`：依赖人记得，漏一处就裸奔，不符合"永恒"目标。
- 要求测试基建统一设置 `CAREER_SCOUT_LOG_DIR`：侵入测试基建，且 `uv run python -m unittest` 与 pytest 双入口都要改，易漏。

**风险/局限**: `sys.modules` 检测非绝对可靠（个别场景可能误判），如误判为测试则日志写入临时目录（可感知但非正式目录）。实现时在懒初始化日志行里打出实际日志路径，便于察觉。

---

## R2: 子进程日志级别策略（避免 debug 刷爆主日志）

**Decision**:
- `configure_logging()` 增加 `level` 默认值从 env `CAREER_SCOUT_LOG_LEVEL` 读取（未设置时保持 DEBUG）。
- `webui/source_boss_cdp.py` 构造子进程 env 时注入 `"CAREER_SCOUT_LOG_LEVEL": "INFO"`（BOSS 与智联子进程同规则）。
- 主进程保持 DEBUG（开发诊断）；子进程默认 INFO：关键现场（登录检查 error、限流/风控/API 回退 warning）均 ≥INFO 可落盘，而 `cdp_session` 每帧消息等 debug 噪音不刷爆 5MB 日志。

**Rationale**: 脚本侧现状（已核实）`_logger.debug` 9 处、`log.warning` 7 处、`log.error` 1 处。DEBUG 噪音大（WebSocket 帧级），接通后若全量 DEBUG 会把关键错误淹没在 50MB 轮转池里；INFO 恰好覆盖全部关键事件。

**Alternatives considered**:
- 子进程保持 DEBUG：日志量不可控，关键错误被淹没。
- 子进程 WARNING：可能漏掉 INFO 级现场（详情页结果行），验收不达标。

---

## R3: 多进程写同一日志文件的轮转竞态防护

**Decision**:
- `webui/logging_setup.py` 新增 `SafeRotatingFileHandler(RotatingFileHandler)`：`doRollover` 期间持有跨进程文件锁（Windows `msvcrt.locking` 锁 `career-scout.log.lock`；POSIX `fcntl.flock`；两者均不可用时退化为无锁直接轮转）。
- 轮转/写入遇 `OSError`（文件被外部删除、被占用、权限变化）时捕获降级：跳过本轮轮转继续追加写，不崩溃、不丢已写内容；文件被删时尝试重建。
- 追加写采用 `mode='a'`：短日志行（<4KB）跨进程追加写冲突概率低，可接受；行级原子性由底层文件系统保证，不做额外锁。

**Rationale**: 用户已确认做法甲（子进程直接写同一文件）。两进程各有独立 handler 实例，平时追加写安全；真正的竞态只在 5MB 轮转瞬间（低频）。文件锁包住轮转即可消除主要风险，降级路径保证任何异常都不致崩溃——符合白箱"出事了不能没痕迹"。

**Alternatives considered**:
- 做法乙（stdout 汇聚，主进程统一转写）：主进程崩溃则子进程日志全丢，违背白箱根本目标（已在用户决策环节排除）。
- 无防护直接写：轮转竞态虽低频，但真发生时可能丢一次轮转或抛异常，不做防护违背"稳"的要求。

---

## R4: 卫生检查——禁止裸日志本子

**Decision**:
- `tests/test_repo_hygiene.py` 新增检查：AST 扫描 `webui/`、`scripts/`（排除 `__pycache__`），禁止 `logging.getLogger(...)` 调用（含 `from logging import getLogger` 后调用）。
- 豁免清单：`webui/logging_setup.py`（定义处自身）、`webui/ai_raw_log.py`（内部取 `career_scout.ai_raw`）、`tests/` 整目录（测试需直接操作 logger handler 校验）、`dist/`、`node_modules/`。
- 存量不合规（`source_boss_cdp_detail.py`、`source_boss_cdp.py`、`source.py`、`updater.py`、`scripts/boss/constants.py`、`webui/error_registry.py`）在本 feature 内全部整改后再启用检查，首日全绿，不设白名单。
- 复用现有 pass-only 基线的"注释 marker"机制处理未来合法例外（如 `webui/ai_raw_log.py` 已有豁免，新增例外必须带注释说明）。

**Rationale**: "禁止裸日志"是防"再次断线"的硬约束。已核实全仓库 `logging.getLogger` 仅上述 6 处定义 + tests 使用 + ai_raw_log 内部，全部处置后扫描为 0。

**Alternatives considered**:
- 只对新增代码生效（存量记账慢慢还）：会有长期豁免期，不符合用户"不允许再出现"的强要求。
- 运行时强制（logger 名不在 career_scout 树则自动挂 handler）：治标不治本，无法在开发期拦截。

---

## R5: 卫生检查——扩展"出错不留痕"

**Decision**:
- 在现有 `test_silent_except_pass_baseline`（pass-only 基线）基础上新增一条更宽的检查：AST 遍历 ExceptHandler，若 body 语句数 ≤2 **且**不含任何 `Call`（日志/函数调用）、`Raise`、`Return`、`Continue`、`Break`、以及"非局部变量赋值"（`Assign`/`AnnAssign` 到属性或实例），视为"吞异常不留痕"→ 检查失败。
- 该检查沿用 pass-only 基线的遍历框架与"注释 marker 白名单"机制。
- **启用前校准**：先全仓库跑一遍统计命中数，误伤处要么补日志/显式返回，要么带注释白名单；确保启用即全绿。

**Rationale**: 现有基线只抓"单 `pass`"，拦不住"`except: return None` 型"与"仅局部赋值后静默继续"的吞异常。更宽规则补齐缺口，与 031 已治理的"吞异常三档"（显式返回/留痕/白名单）口径一致。

**Alternatives considered**:
- 检查"无任何日志调用且无 raise"的所有 except：误伤大（清理类 except 合法），需更细规则。
- 仅靠现有 pass-only：缺口依旧（用户明确不满意）。

**风险/局限**: 规则无法 100% 判断"副作用"，用"语句类型白名单"逼近；误伤通过启用前校准与注释白名单收敛。

---

## R6: 子进程"抓取现场"覆盖边界（FR-003 落地口径）

**Decision**:
- 脚本关键现场补日志记录（最小增量，级别 INFO，随 R2 默认可落盘）：
  - `scripts/boss/detail_scrape.py`：每岗位详情 terminal 结果追加一行 `_logger.info("detail job_id=%s status=%s safe_code=%s", ...)`（位于 `_emit_detail_safe_event` 事件回调判断之前，单条详情抓取同样留痕）；
  - `scripts/boss/search.py`：风控/限流判定结果追加 `log.warning(...)`，覆盖"首次判定"与"重试后最终结论"两段（`classify_list_diagnosis` 返回 verdict 非 None 时）；
  - `scripts/boss/login.py`：登录成功补一行 `log.info`；
  - `scripts/zhilian/search.py`：`_risk_signal` 判定出风险信号（verification/rate_limited/blocked/login_required/unreachable）时补 `_logger.warning(...)`（智联为进程内调用，直接落主日志）；
  - `webui/task_runners.py`：任务级兜底 `except Exception` 补 `_logger.exception(...)`，异常类型与堆栈进主日志（FR-009）。
- 既有结构化通道（events.jsonl、`emit_failure_line` 的 stdout 失败行）保持不动：它们是"数据/分类权威"，日志是"现场留痕"，二者并存不互相替代。
- 验收口径：详情页结果、登录检查、风控判定以 logger 落盘为准；脚本内纯进度 print（如 CLI 进度条）不纳入验收依赖（现有 stdout→主进程缓冲机制保留，不新增转写）。

**Rationale**: 已核实脚本侧"详情页访问/风控判定"目前**只写 events.jsonl / print，不进 logger**，若不做这步，方案甲接通后日志里仍没有"每岗位现场"。补 3 类最小日志行即可满足验收"脚本子进程抓取日志出现在 career-scout.log"；级别 INFO 与 R2 一致。

**Alternatives considered**:
- 把 events.jsonl 内容复制进日志：结构化数据重复写，日志膨胀且破坏"events.jsonl 是数据通道"的既有设计。
- 主进程转写子进程 stdout：`_default_run` 需加 on_output 转写，涉及 1MB 输出上限与行数增长，收益低。

---

## 决策汇总

| 决策点 | 结论 |
|---|---|
| R1 懒初始化 | get_logger 首次调用自动 configure_logging；测试上下文写临时目录 |
| R2 子进程级别 | env `CAREER_SCOUT_LOG_LEVEL=INFO` 注入子进程；主进程 DEBUG |
| R3 多进程写安全 | SafeRotatingFileHandler + 跨进程文件锁 + OSError 降级 |
| R4 禁裸日志检查 | AST 全扫描 + 豁免清单 + 存量先改全绿 |
| R5 扩展不留痕检查 | ExceptHandler body 无行为（无 Call/Raise/Return/Continue/Break/属性赋值）即失败；启用前校准 |
| R6 现场覆盖 | detail/search/login 补 INFO/WARNING 最小日志行；事件文件通道保留 |
