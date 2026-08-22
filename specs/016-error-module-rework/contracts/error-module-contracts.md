# Contract: 统一错误码注册表（对外镜像 JSON）

注册表唯一来源 `webui/error_registry.py`，`to_json()` 投影供前端 `webui/src/errorCodes.ts` 镜像测试锁定。

## 字段契约（每码）

```json
{
  "code": "source_rate_limited",
  "category": "source",
  "blocking": true,
  "retryable": true,
  "user_message": "账号/操作频繁被限流",
  "impact": "systemic",
  "reason": "...",
  "resume_condition": "等待限流解除后点继续",
  "aliases": ["ip_risk_control"]
}
```

- 同一语义只允许一个正名码；其余历史码只能出现在 `aliases`。
- 派生集合契约：`SYSTEMIC_BLOCK_CODES == {c | blocking(c) && impact(c)=="systemic"}`（一致性测试断言）。
- 本 Spec 别名变更：`captcha_required→source_verification_required`、`login_expired→source_login_required`、
  `ip_risk_control→source_blocked`、`cdp_unavailable→source_cdp_unavailable`。
- 本 Spec 新码：`source_account_restricted`（blocking/systemic）、`source_status_unclear`（非阻断/independent）。
- 展示兜底：任何未知码经 `resolve_code` 落 `internal_error` 并告警，不静默。

---

# Contract: 结构化失败行（脚本 → webui）

## 格式

```text
__CAREERSCOUT_FAILED__ code=<code> hint=<text>
```

- 一行，stdout 末尾，整份输出中至多一行（重复时取最后一行）。
- `<code>` 必须是注册表正名码；`<hint>` 单行 ≤120 字符，UTF-8，不含换行。
- 产出方：`scripts/boss_cdp_raw.py`、`scripts/zhilian_cdp_raw.py` 一切失败退出路径（含 RiskControlError 退出码 10）。
- 解析方：`webui/source.py::_classify_failed_code` —— 命中即定类，输出其余内容不参与分类。
- 缺行兜底：退出码 2→`source_cdp_unavailable`、3→`source_invalid_output`、11→`source_request_limit_exceeded`、
  10→`source_status_unclear`、1→高置信登录短语→`source_login_required` 否则 `source_unknown_error`。

---

# Contract: 组合软失败记录（task_events）

- 事件类型：`combo_issue`；payload：`{combo_key, kind:"combo_failed", failed_code, reason, ts}`。
- task-state 响应新增 `combo_issues` 数组（最近 20 条倒序）；元素含 `combo_key/code_text(注册表文案)/reason/ts`。
- 前端仅展示，不提供重试单组合操作（本轮范围外）。

---

# Contract: 登录状态缓存（文件格式不变，状态值域收敛）

- `~/.career-scout/login-state.json` 结构不变；`state` 值域：`logged_in | not_logged_in | unknown`。
- 遗留 `restricted` 值读取时按无缓存处理（触发重探），不报错、不迁移。

---

# Contract: 冷却移除（负契约）

- 任何 API 响应不再包含 `cooldowns` 字段；`/api/cooldown/clear` 返回 404。
- 提交任务不因任何历史风控记录被拒绝；`~/.career-scout/cooldown.json` 不再被读写。
