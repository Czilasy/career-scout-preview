# Data Model: 大文件拆分重构（021）

本 Spec 为纯结构重构，**不改数据库 schema、不改持久化数据格式**。唯一新实体为运行时对象：

## PipelineContext（运行时上下文，非持久化）

| 字段 | 来源（原闭包捕获） | 用途 |
|---|---|---|
| tasks | `_pipeline_tasks` | 运行中任务表 |
| lock | `_pipeline_lock` | 任务表读写锁 |
| store | `store` | TaskStore 实例 |
| emit | `emit` | 前端事件推送 |
| write_run | `_write_run_unless_finished` | 终态安全的 run 写入助手 |

- **规则**: create_app 内组装一次，四个 runner 及其助手只通过 ctx 访问；字段集在各 runner 外迁批中按实际捕获清单补全（搬运时以"闭包捕获了什么"为准，不新增语义）。**上表 5 个字段仅为示意起点：B3 的验收物之一是四个 runner 的完整闭包捕获清单，ctx 字段集以该清单为准；可被 monkeypatch 的符号（`boss`、`ai_service`、`ScraperExecutor`、`_BossCdpSource` 等）由 ctx 持有或经 `webui.app` 模块属性调用时取用，保证门面补丁打在真实执行路径上。**
- **状态转移**: 无（纯引用容器）。

## 门面契约（兼容导出）

- `webui/app.py`、`webui/source.py`、`webui/store.py`、`scripts/boss_cdp_raw.py` 等旧路径 re-export 全部既有公开符号；符号集合以"拆分前可 import 到的"为准，只增不减。
