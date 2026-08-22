# Data Model: 016-error-module-rework

## 1. 错误码注册表（唯一来源 `webui/error_registry.py`）

### 1.1 码语义分层（本 Spec 新增/调整）

| 码 | 语义 | blocking | impact | 变化 |
|---|---|---|---|---|
| `source_verification_required` | 实锤验证码/滑块页 | 是 | systemic | 保留；吸收 `captcha_required` 为别名 |
| `source_login_required` | 登录失效 | 是 | systemic | 保留；吸收 `login_expired` 为别名 |
| `source_rate_limited` | 实锤限流（限流提示页 / code:31 / 连续两次拦截复现） | 是 | systemic | 保留 |
| `source_account_restricted` | 确认账号/平台受限（探测实锤，来源未细分） | 是 | systemic | **新增**；preflight restricted 探测结果用它，不再借用 source_blocked |
| `source_blocked` | IP 级风控页/平台封禁（实锤） | 是 | systemic | 语义收窄；吸收 `ip_risk_control` 为别名 |
| `source_status_unclear` | 暂时无法判断（单次拦截/环境异常码/空响应/结构异常/连接抖动） | **否** | independent | **新增**；软失败专用，绝不提示"限流/账号受限" |
| `source_cdp_unavailable` | 调试浏览器/CDP 不可用 | 是 | systemic | 保留；吸收 `cdp_unavailable` 为别名 |
| `source_request_limit_exceeded` | 本轮请求数达上限 | 是 | systemic | 保留（应用内自我保护，与平台风控无关） |
| 其余 source_* / ai_* / internal / control 码 | 现状 | — | — | 保留 |

### 1.2 派生规则（替代手工集合）

- `SYSTEMIC_BLOCK_CODES` ≡ `{code | entry.blocking and entry.impact == "systemic"}`。
- `_jd_hard_stop_codes`、resume 阻断码集合、`_HARD_STOP_CODES` 全部改引用派生集合；删除三处手工清单。
- 别名机制：`ALIAS_TO_CODE` 表（旧码 → 唯一码）；`resolve_code` 先查正名再查别名；
  注册表 JSON 导出含 `aliases` 字段供前端镜像同步。
- 历史兼容：DB 中已存的四个旧 taxonomy 码展示时经别名解析；不迁移数据。

## 2. 结构化失败行（脚本 ↔ webui 契约）

- 格式：`__CAREERSCOUT_FAILED__ code=<registry code> hint=<≤120 字符可读原因>`
- 产出：BOSS/智联抓取脚本以失败退出（退出码 10 及一切非零失败）前，stdout 末尾打印该行（唯一一行）。
- 解析：`boss_cdp_signals.parse_failure_line(captured) -> (code, hint) | None`；
  webui `_classify_failed_code` 优先用它；无该行时按退出码粗分（2/3/11 精确，10→`source_status_unclear`，1→高置信登录短语或 unknown）；
  全文关键词扫描主路径删除。

## 3. 实锤分档（`scripts/boss_cdp_signals.py`，BOSS；智联 marker 对齐同语义）

| 档 | 判定输入 | 结果 |
|---|---|---|
| confirmed_restricted | 验证码/滑块页文本（错误样本，非岗位正文）；明确限流提示页；API code:31；同页 403/429 重试一次后复现 | `source_verification_required` / `source_rate_limited` / `source_account_restricted` |
| uncertain | 单次 403/412/418/429（重试后恢复或仅一次）；API code:37；空响应；结构异常；js 异常；CDP 抖动 | `source_status_unclear`（软失败） |
| normal_empty | API 正常应答无职位 / hasMore=false / totalCount=0 / 全部正常空页 | 空成功（现有哨兵保留） |

- 登录探测顺序：HTTP 401 → not_logged_in；JSON 结构=已登录 → logged_in（先于一切关键词）；
  结构完整无工资 → not_logged_in；其余文本 → 高置信风控短语 → restricted；解析失败/超时 → unknown。
- 连续空页：结构异常空页先原地重试一次；连续异常达阈值 → 停止翻页 + `source_status_unclear`，
  "大概率被风控限制"文案与该判定路径删除。

## 4. 登录状态缓存（收敛两态）

- 合法状态：`logged_in` / `not_logged_in` / `unknown`（unknown 仅运行内使用，不构成拦截依据）。
- `restricted` 不再写入；读取旧文件中遗留的 restricted 视为无缓存（触发重探）。
- BOSS preflight：缓存命中 logged_in → 就绪；命中 not_logged_in → 提示登录；其余 → 真实探测。
  探测结果 restricted → 当次任务 `source_account_restricted` 失败（复探后仍受限才算），不写缓存。
- 智联：`_SIGNAL_TO_STATE`/`_STATE_TO_SIGNAL` 相应收敛；verification/rate_limited/blocked 信号当次失败、不缓存。

## 5. 组合软失败记录（复用任务事件）

- 载体：现有 `task_events` 表，事件类型 `combo_issue`。
- payload 字段：`combo_key`、`kind="combo_failed"`（区别于登录复核 issue）、`failed_code`（统一码）、
  `reason`（可读原因，≤200 字符）、`ts`（ISO 毫秒）。
- 产出：pipeline `combo_failed` 分支调用 `_notify_combo_issue`；读取：task-state 响应附带
  `combo_issues`（最近 20 条，按 seq 倒序）；前端任务进度区展示失败组合摘要（原因文案来自注册表 `user_message`）。
- 全组合软失败且零结果 → 任务失败收场（既有哨兵，文案保留"无法确认状态"中性表述）。

## 6. 删除项（冷却）

- `webui/cooldown.py` 整文件；`cooldown.json` 不再读写（遗留文件允许存在）。
- 相关：提交守卫、clear 端点、env-check `cooldowns` 字段、`mark_cooldown` 调用、前端冷却展示与解除入口、
  `tests/test_cooldown*.py`。

## 7. 任务断点与进度（无跳变）

- 后端：resume 提交后的首次 task-state 响应必须携带 checkpoint 推导的 processed/overall（不得等首事件）。
- 前端：恢复路径（继续、报错后继续、刷新）初始化进度一律取 task-state 断点值；禁止本地重置为 0。
- 三条进度线：列表（combo/page 级）、JD（completed_job_ids 基数）、AI 筛选（已判定量）。

## 8. 状态转换（任务级）

```text
running ──硬阻断(4类)──▶ paused(保留断点+checkpoint) ──用户继续──▶ running(断点起步)
running ──软失败(组合级)──▶ running(记 combo_issue，不停)
running ──全部软失败且零结果──▶ failed(中性文案)
running ──正常完成──▶ succeeded
```
