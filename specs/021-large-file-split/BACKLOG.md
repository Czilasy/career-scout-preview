# BACKLOG — 021 大文件拆分重构

本文件记录 021 Spec 拆分过程中发现的既有问题与范围外超标文件。
**纯搬运纪律**：拆分不修既有 bug；以下问题原样保留，另行立项处理。

## 范围外超标文件（不在 021 的 11 文件清单，未拆分）

| 文件 | 行数 | 超标 | 说明 |
|---|---|---|---|
| `webui/historical_recovery.py` | 990 | +190 | 历史恢复域；不在 021 允许清单 |
| `scripts/zhilian_cdp_raw.py` | 875 | +75 | 智联抓取脚本（boss_cdp_raw 的姊妹）；不在 021 允许清单 |
| `webui/task_runners.py` | 864 | +64 | 遗留 runner 模块；不在 021 允许清单 |

处置：T029 全仓行数终检判定不属本 Spec，记 BACKLOG 不改（若用户要求纳入拆分需另行立项）。

## 搬运中发现的既有问题（未修，原样保留）

1. **pyflakes 基线告警**（`scripts/boss_cdp_raw.py` 原文件即存在，非拆分引入）：
   - `redefinition of unused 'events_callback'`（run_search_programmatic 内嵌套函数重定义，原 3668/4066 行）
   - 若干 `boss_cdp_signals` 导入未使用（DETAIL_RATE_LIMIT_KEYWORDS / VERDICT_STOP / api_code_hint / is_risk_api_code / looks_like_rate_limited）
2. **`scripts/boss_cdp_raw.py` 的 `requests`/`websocket` 延迟全局**：运行时经 `require_runtime_dependencies` 注入（B044 既有设计），拆分后保持门面权威 + 子模块 `_facade().X` 动态取用；CLI 直跑/测试 exec 模式均经 `sys.modules["scripts.boss_cdp_raw"]` 自注册与 `__getattr__` 转发保持 patch 面互通——此为拆分适配而非 bug。
3. **T024 store_migrations 常量多份引用**：`_DDL_*` 常量定义在 `store_migrations_v1.py`，v2-v4 经 import 引用（原单文件一份定义变为 v1 定义 + 跨模块 import），值不变、语义不变。
4. **T027 let 变量 ref 化**：`pollRetryCount`/`scopePreviewReqId`/`recrawlRetryCount`/`pollTimer` 由模块级 `let` 改为 `ref`（跨 composable 共享必需），读写点统一 `.value`；行为等价。
5. **全量偶发测试污染**：`test_healthy_pipeline.Slice4ScrapePauseContinueTests.test_resume_survives_stale_pause_cleanup_timer` 在部分全量轮次失败（stage_complete 事件数 0≠1）。根因：状态与 stage_complete 事件分属两个独立事务，worker 先落状态再落事件，测试在状态轮询到 succeeded 后未等待事件落库即断言，高负载下命中可见窗口。**已修**：测试在断言前等待事件落库（产品代码零改动），连跑 5 次 + 类内全组验证通过。

## 待确认项

- Phase 9 quickstart 全流程验证是否运行全量测试（等待用户确认）。
- 环境变量 `ACC_PRODUCT_CONFIG_V3`（48 万字符，超 Windows 32767 上限，由 IDE 工具注入非项目）连累 `mock.patch.dict(os.environ)` 类测试；全量验证采用 `env -u ACC_PRODUCT_CONFIG_V3` 规避，非代码问题。
