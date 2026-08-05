# Task 002：AI 建议适配器与规则兜底

## 新会话启动指令

你是 Wave 1 AI 建议执行会话。只执行本文件，完成后停止并把证据交回主会话。AI 建议是只读解释，不拥有状态推进权；不要修改存储、路由或前端。

## 目标与边界

创建 `webui/job_advice.py`，提供单岗位、按需、非持久化的建议适配器。输入只能包含 JD、投递时间、最后跟进时间和经过天数；输出 action 只能是 `follow_up` 或 `review`，并带安全的用户解释和 `source=ai|rule`。缺 JD 固定 `review`；AI 未配置、Key 缺失、超时、网络失败、非法 JSON 或非法 action 时使用规则兜底。建议调用前后不得写生命周期表或 `feedback_events`。

## 必读文件

- `AGENTS.md`
- `specs/002-job-feedback-reminders/spec.md`
- `specs/002-job-feedback-reminders/research.md`
- `specs/002-job-feedback-reminders/contracts/http-api.md`
- `specs/002-job-feedback-reminders/data-model.md`
- `webui/ai.py`
- `tests/test_ai.py`

先确认目标文件是否存在；若不存在才新建。

## 允许写入

- `webui/job_advice.py`
- `tests/test_job_advice.py`

## 禁止写入与行为

- 禁止修改 `webui/ai.py`、`webui/store.py`、`webui/app.py`、任何前端文件、Spec 合同和其它测试。
- 禁止把 `platform`、`platform_job_id`、canonical URL、标题、公司或用户界面字段传给建议决策函数。
- 禁止让 advice 模块调用 lifecycle action、更新数据库、保存建议或自动标记 stale/deleted。
- 禁止将原始 AI 响应、Key、endpoint、prompt 或异常堆栈返回给前端。
- 禁止用固定 AI 成功结果掩盖未调用、解析失败或未配置；应明确 `source=rule`。

## 前置与工作区门禁

1. `git status --short` 后读取现有改动；不还原、不覆盖、不暂存其它会话文件。
2. 从 `webui/ai.py` 提取实际可复用调用接口，不根据文件名猜 API。
3. 先写失败测试，尤其断言平台字段不在 AI input，以及数据库状态/事件计数前后相同。
4. advice 模块使用纯 DTO/fake provider 保持独立；若发现冻结合同缺少输入表达，只回报主会话。
5. 本任务与 Wave 1 其它会话并行时，不运行 broad `git add` 或提交；只回报本任务改动，主会话在 Wave 1 汇合后统一执行 hygiene 和 commit。

## 执行清单

- [x] T014 读取 `webui/ai.py`、`tests/test_ai.py` 与 advice 合同，记录调用边界；不得修改既有文件。
- [x] T015 在 `tests/test_job_advice.py` 写正常 AI、缺 JD、未配置、缺 Key、超时、网络失败、无效 JSON、非法 action 的失败测试。
- [x] T016 写 input 最小化、平台字段缺席、解释文本清洗、原始异常不泄露和状态/事件零变化测试。
- [x] T017 在 `webui/job_advice.py` 实现最小输入 DTO 与 elapsed days 计算/接收边界；不自行决定提醒资格。
- [x] T018 实现 AI 输出解析、`follow_up|review` allowlist、reason 清洗和 `source` 投影；非法或空 reason 进入规则兜底。
- [x] T019 实现缺 JD 固定 `review`；其它 AI 故障在有 JD 时返回规则 `follow_up`，无 JD 时返回规则 `review`；保证无写入副作用。
- [x] T020 运行 advice 与既有 AI 测试，检查只改允许路径并提交 `feat: add job advice fallback`。

## 精确验证命令

```powershell
uv run python -m unittest tests.test_job_advice tests.test_ai
uv run python -m unittest tests.test_repo_hygiene
git diff --check
git status --short
```

如果既有 AI 测试暴露环境依赖，保留原失败证据并区分本包失败；不得改既有测试绕过它。

## 完成证据与解锁

```text
Task: 002
Changed: webui/job_advice.py; tests/test_job_advice.py
Tests: exact commands and result
Evidence: minimum input / allowlist / all fallback classes / zero persistence mutation
Git: commit hash and subject, or blocked reason
Blocked: none or reproducible blocker
```

完成后只解锁依赖本模块的 Task 005/008；不要执行或修改它们。
