# 任务续跑 API 门面薄化（048）

## 背景

第三轮 B096 返修后，`webui/task_continue_api.py` 实测 829 行，超过项目 Python 800 行硬红线。该文件同时承载续跑、暂停、取消和结束保存路由；本次只做内部职责拆分，不改变任何公开路径、请求、响应、状态语义或用户行为。

## 兼容目标

1. 保留 `register_task_continue_routes(app, ctx)` 作为唯一公开注册入口。
2. 保留 `/api/task/continue/<run_id>`、`/api/task/pause/<run_id>`、`/api/task/cancel/<run_id>` 和 `/api/task/finish/<run_id>` 的 HTTP 方法、状态码、响应字段、错误码与终态行为。
3. 结束保存路由提取后仍从 facade 动态读取既有可替换依赖，使测试注入和运行时兼容 monkeypatch 不失效。
4. facade 与所有新增 Python 模块均低于 800 行；不得把业务逻辑堆进另一个超限文件。

## 非目标

- 不增加续跑、暂停、取消、结束保存的产品行为。
- 不改变 B096 Flow/Track 状态机、B106 去重或平台脚本。
- 不修改 `scripts/boss/`、`scripts/zhilian/`、`webui/cross_platform_dedupe.py` 或 `specs/019-cross-platform-job-dedup/`。
