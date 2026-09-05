# Data Model: 日志记录

**Created**: 2026-09-01 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

本功能不新增业务数据实体；涉及对象为日志记录与日志文件。

## 日志记录（Log Record）

一条日志记录由 Python logging 框架产生，经过 `TaskContextFilter` 与 `RedactingFormatter` 后写入文件。字段契约见 [contracts/logging-contract.md](contracts/logging-contract.md)。

## 日志文件

| 文件 | 路径 | 轮转 | 写入者 | 变更 |
|---|---|---|---|---|
| `career-scout.log` | `~/.career-scout/logs/`（或 `CAREER_SCOUT_LOG_DIR`） | 5MB × 10 备份 | 主进程 + 抓取子进程（新增） | 本次核心 |
| `ai_raw.log` | 同目录 | 5MB × 10 备份 | 主进程 AI 通道 | 不动 |
| `career-scout.log.lock` | 同目录 | 无 | 跨进程轮转锁（新增） | 新增锁文件 |

## 日志级别与环境变量

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `CAREER_SCOUT_LOG_DIR` | `~/.career-scout/logs` | 日志目录覆盖（既有） |
| `CAREER_SCOUT_LOG_LEVEL` | 无（默认 DEBUG） | 懒初始化时的日志级别；子进程被注入 `INFO` |
| `CAREER_SCOUT_TESTING` | 无 | （保留既有语义，非本功能新增） |

## 状态流转

本功能无业务状态流转。日志文件状态：正常追加 → 满 5MB 轮转（锁保护）→ 被外部删除/只读时降级（跳过轮转继续写或重建）。
