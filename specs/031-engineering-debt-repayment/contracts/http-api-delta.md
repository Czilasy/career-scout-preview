# 契约：HTTP API 变更

**适用**：批次 7（事故代码退场）。这是本单唯一的生产 API 面变更。

## 删除的端点

| 端点 | 方法 | 现址 | 删除后行为 |
|---|---|---|---|
| `/api/recovery/preview/<run_id>` | GET | webui/task_state_api.py:331 | 404（路由不存在） |
| `/api/recovery/prepare/<run_id>` | POST | webui/task_state_api.py:353 | 404 |
| `/api/recovery/execute/<run_id>` | POST | webui/task_state_api.py:366 | 404 |

## 影响面声明

- **前端**：webui/src 无任何调用（已核检）——用户界面零影响。
- **已知调用方**：无第三方；仅事故期间人工调用（curl），能力由手动工具等价承接（见 recovery-cli.md）。
- **遗留设计瑕疵一并消除**：preview 接口收 `run_id` 形参却自认"仅作占位"无视之——接口删除后此问题不存在。
- **响应格式契约**（contracts/http-api.md 既有文档）不受影响；其余端点请求/响应零变化，唯提示语 `_MSG_TASK_NOT_FOUND` 文案统一为"任务不存在或已被移除"（7 处 404/错误体的 user_message/error 字段），字段名与状态码不变。
