# Research: 016-error-module-rework

日期：2026-08-22。所有结论来自本仓库代码实查（行号为当日快照）。

## R1 组合软失败落库机制（已存在，可复用）

- `webui/pipeline_exec.py:958-968` 已有 `_notify_combo_issue(entry)` → 调用方 `on_issue` 回调；
  当前仅在登录失效二次复核时使用（app.py:2686 `_record_combo_issue`）。
- `webui/app.py:2686-2695` `_record_combo_issue` 已把问题写进 `store.append_task_event(task_id, "combo_issue", {...})`。
- `webui/store.py:2696` 已有 `list_task_events(run_id, after_seq)` 读取接口。
- **决定**：软失败留痕复用 `combo_issue` 事件通道（pipeline 在 `combo_failed` 分支追加 `_notify_combo_issue`），不建新表、不加新端点；任务详情展示由既有 task-state 响应附带最近软失败记录（修改既有响应字段，不在 app.py 新增路由）。

## R2 错误码现状与重复清单

- `webui/error_registry.py`：`_SOURCE_CODES` + `_TAXONOMY_CODES` 双套并存。语义重复四对：
  `captcha_required`↔`source_verification_required`、`login_expired`↔`source_login_required`、
  `ip_risk_control`↔`source_blocked`、`cdp_unavailable`↔`source_cdp_unavailable`。
- `SYSTEMIC_BLOCK_CODES`（registry:329）手工并集两套码。
- `webui/task_runners.py:34-46` `_SCRAPE_BLOCK_PATTERNS` 是第三处关键词表（按 error 字符串反查码），
  含裸词 `429`/`滑块`/`geetest` 等，与 B069 误报同类。
- 前端镜像 `webui/src/errorCodes.ts` + `webui/src/__tests__/errorCodes.spec.ts` 与 `tests/test_error_registry.py` 锁定注册表 JSON。
- **决定**：删除四个重复 taxonomy 码，注册为别名（`resolve_code` 已支持别名机制需扩展：别名表驱动）；
  `SYSTEMIC_BLOCK_CODES` 改由 `blocking and impact=="systemic"` 推导；
  `_SCRAPE_BLOCK_PATTERNS` 删除，`_classify_scrape_block` 只在 `hard_stop_code` 缺失时兜底（run_search 现总返回 hard_stop_code）。

## R3 BOSS 判定链与误报点（已实查确认）

- 探测：`scripts/boss_cdp_raw.py:948-996` `probe_login_state_tri` —— 关键词判定（:990）在 logged_in 判定（:996）之前，
  `RISK_CONTROL_KEYWORDS`（:1302）含裸词 `滑块/captcha/waf`，正常岗位 JSON 可误中 → restricted。
- 分类：`webui/source.py:1862-1893` `_classify_failed_code` 对**全输出**（含岗位标题/薪资打印行）扫裸词
  `429/滑块/slider/稍后再试` → 误判 rate_limited/verification → 写 restricted 缓存 + 4h 冷却。
- 漏判：BOSS `code:31`（请求受限）文案"请求受限"不在 `_RATE_LIMIT_KEYWORDS`（source.py:1829-1834，只有"访问受限/账号受限"）→ 落 unknown。
- 空页：`boss_cdp_raw.py:1776-1826` 已有正常空/翻完哨兵；遗留问题：结构异常空页无原地重试、连续异常空页仍报"大概率被风控限制"（:1482-1487 文案）。
- 详情页：`DETAIL_RATE_LIMIT_KEYWORDS`（:1314）已收紧，保留。
- **决定**：实锤分档落在 `scripts/boss_cdp_signals.py`（49 行，现成纯分类模块）；
  结构化失败行 `__CAREERSCOUT_FAILED__ code=<code> hint=<text>` 由该模块定义/解析，脚本打印、webui 只认此行。

## R4 持久副作用链（冷却与缓存）

- 冷却：`webui/cooldown.py`（188 行，删）、`app.py:2171` `_submit_cooldown_guard`、`app.py:4655` clear 端点、
  env-check `cooldowns` 字段（app.py:1123-1144）、`_restricted_cache_detail` 读冷却原因（app.py:2140）。
  前端：`EnvCheckDialog.vue`、`DiscoveryView.vue`、`types.ts`。
- 登录缓存：`scripts/login_state_cache.py` 四态含 `restricted`（TTL 15 分钟）；
  BOSS preflight 命中缓存 restricted 直接失败不再探测（source.py:434-438），并把探测结果原样回写（:454-456）；
  智联 `_STATE_TO_SIGNAL`（source.py:1994-2005）反向映射丢原始信号。
- **决定**：缓存收敛两态 + unknown；`LOGIN_STATE_STATES` 去掉 restricted，读取旧文件里的 restricted 视为无缓存（触发重探）。

## R5 暂停/续跑与进度链路

- 硬停判定：`webui/pipeline_exec.py:1096` `outcome.failed_code in _HARD_STOP_CODES`（= SYSTEMIC_BLOCK_CODES）；
  JD 批次 `_jd_hard_stop_codes`（:1285）手工维护另一份 → 统一由注册表推导。
- 暂停落库：app.py:2749-2775 hard_stop → paused + checkpoint + `pause` 事件；软失败终态哨兵在 pipeline_exec:1185-1197（保留）。
- 进度：后端 8295adc 已做"task-state 以 checkpoint 为持久下限"；用户仍见"归零再跳变"，
  说明前端恢复路径（继续/刷新）本地把进度重置 0，未用 task-state 断点初始化。
  涉及 `DiscoveryView.vue`（resume 处理与轮询）、`TaskProgress.vue`（overall_percent 权威值）。
- **决定**：前端恢复时以 task-state 返回的 overall/processed 初始化，禁止本地 0 起步；后端补 resume 首拍即带断点进度。

## R6 受影响测试清单

删除：`tests/test_cooldown.py`、`tests/test_cooldown_api.py`。
修改：`test_error_registry.py`、`test_source.py`、`test_boss_cdp_signals.py`、`test_zhilian_risk_signal.py`、
`test_rate_limit_stop.py`、`test_env_check.py`、`test_login_state_cache.py`、`test_webui_app.py`、
`test_healthy_pipeline.py`、`test_webui_store.py`、`test_resume*.py`（按实际断言）、
前端 `errorCodes.spec.ts`、`EnvCheckDialog.spec.ts`、`DiscoveryView.spec.ts`、`discovery.spec.ts`。
新增：`tests/test_risk_signal_tiers.py`（分档、失败行、分类优先级、误报回归样本）。

## R7 AI 域边界

`webui/ai.py` / `ai_retry.py` 的重试退避不动；AI 内部码已在注册表（`_AI_INTERNAL_CODES`），
仅随统一别名/推导机制同步，`AI_TAXONOMY_TARGETS` 目标码中的重复 taxonomy 码改为统一码。

## R8 结论汇总（Decision / Rationale / Alternatives）

- 失败信号载体：结构化失败行（唯一权威）+ 高置信短语兜底。
  备选（否）：退出码细分 100-104 —— 退出码语义已被 CLI 生态占用，扩展脆弱。
- 分档位置：`scripts/boss_cdp_signals.py`（BOSS）+ 智联 marker 留在 `zhilian_cdp_raw.py`（平台词表各一份，语义层共用注册表标记）。
  备选（否）：新建平台无关分档模块 —— 分档语义即码语义，注册表已承载，避免多一层间接。
- 软失败留痕：复用 combo_issue 事件通道。备选（否）：新表新端点 —— 违反 app.py 禁增约束且机制已存在。
- 进度修复：前后端两侧（后端补首拍断点、前端禁止 0 起步）。
  备选（否）：仅前端 —— 若后端首拍不带断点，前端只能猜。
