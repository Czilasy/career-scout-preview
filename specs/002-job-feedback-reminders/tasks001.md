# Task 001：migration、生命周期事务、事件回执与提醒投影

## 新会话启动指令

你是 Wave 1 存储与领域内核执行会话。先读取本文件和仓库根 `AGENTS.md`，再读取所有必读文件。你只负责本任务，完成后停止，不要执行 Task 002-009，也不要宣布整项功能完成。工作区可能出现其它会话的改动，必须保留并在当前内容上合并。

## 目标

实现 schema 28、画像岗位生命周期领域规则、追加式事件、命令回执、权威岗位身份的事务内双索引 helper，以及平台无关的动态提醒投影。`profile_jobs.status` 是当前状态唯一来源；`feedback_events` 继续承担兴趣偏好语义，不得被生命周期操作写入或覆盖。

## 必读文件

- `AGENTS.md`
- `specs/002-job-feedback-reminders/spec.md`
- `specs/002-job-feedback-reminders/plan.md`
- `specs/002-job-feedback-reminders/research.md`
- `specs/002-job-feedback-reminders/data-model.md`
- `specs/002-job-feedback-reminders/contracts/http-api.md`
- `specs/002-job-feedback-reminders/quickstart.md`
- `webui/store.py`
- `tests/test_webui_store.py`
- `webui/platforms.py`

`webui/job_feedback.py`、`tests/test_job_feedback.py` 如不存在则按冻结合同新建；先确认不存在，不得把缺失文件当作既有实现。

## 前置与共享合同

- 本包是 Wave 1，可与 Task 002、003、004 并行。
- 本包是 schema 和事务实现的唯一所有者；其它会话不得修改 `webui/store.py`、`webui/job_feedback.py` 或本包测试。
- Task 003 只依赖本包公开的 connection-aware helper 协议；不要为了迎合它修改尚未确认的接口。
- 任何合同冲突先停止并向主会话报告，不能改 Spec 或 contracts 文件来“解决”冲突。

## 允许写入

- `webui/store.py`
- `webui/job_feedback.py`
- `tests/test_webui_store.py`
- `tests/test_job_feedback.py`

## 禁止写入与禁止行为

- 禁止修改 `webui/app.py`、`webui/platforms.py`、任何 Vue 文件、`webui/src/types.ts`、其它 Task 的测试或 Spec 合同。
- 禁止使用当前界面平台、标题、公司、JD 相似度或裸 `platform_job_id` 猜测岗位身份。
- 禁止在生命周期查询或提醒查询增加平台过滤。
- 禁止调用会自行开新连接的公开 upsert 来嵌套生命周期事务；必须抽出接收现有 `sqlite3.Connection` 的 helper。
- 禁止把 AI 调用放入状态写入事务。
- 禁止自动填充旧缺失投递时间、写入伪历史事件、修改 `feedback_events` 或删除生命周期事件。

## 工作区与节点门禁

1. 执行 `git status --short`，读取所有已有改动文件；不还原、不覆盖、不批量格式化。
2. 记录当前 migration 版本、备份常量、`profile_jobs` schema、`upsert_job`、`save_job`、清理逻辑和测试辅助函数。
3. 先为每个行为补失败测试，再实现；若现有测试约定与冻结合同冲突，保留证据并报告，不静默改变合同。
4. migration 28 必须证明 v27 非空库先过备份/hash/manifest/quick_check 门禁，迁移失败时源库仍为 v27。
5. 生命周期命令必须证明 snapshot、event、receipt、profile link 和 job identity 任一失败都会整笔回滚。
6. 本任务与 Wave 1 其它会话并行时，不运行 broad `git add` 或提交；只回报本任务改动，主会话在 Wave 1 汇合后统一执行 hygiene 和 commit。

## 执行清单

- [x] T001 读取并记录四个目标源码/测试文件的当前基线、既有改动、连接生命周期和可复用接口。
- [x] T002 在 `tests/test_webui_store.py` 先写 migration 27→28 备份、schema、重复初始化、失败回滚、外键和数据守恒测试。
- [x] T003 在 `webui/store.py` 实现 migration 28：`last_follow_up_at`、事件表、命令回执表、索引、备份目标 28、迁移完整性检查；不得猜填旧时间或生成旧事件。
- [x] T004 在 `tests/test_job_feedback.py` 先写 action 矩阵、时间解析、幂等、并发、冲突和 rollback 测试。
- [x] T005 在 `webui/job_feedback.py` 实现状态集合、七个 action、RFC 3339/UTC 规范化、命令 fingerprint、领域错误与快照投影。
- [x] T006 在 `webui/store.py` 抽出可接收现有 connection 的双索引岗位 upsert/解析 helper，并使公开 upsert 复用它；验证两个索引命中不同内部岗位时零副作用。
- [x] T007 在 `webui/store.py`/`webui/job_feedback.py` 实现单事务岗位解析、画像关联、状态/时间更新、receipt 写入和真实变化 event 追加；同 request 同 fingerprint replay，不同 fingerprint 返回冲突。
- [x] T008 实现当前 state/revision 与按 sequence 读取 events；no-op 仅有 `changed=0` receipt，不产生 event；响应 state 重新读取当前快照。
- [x] T009 先写提醒阈值矩阵：固定 now 下 720 小时前/恰好/后、last follow-up 优先、损坏 follow-up 不回退、缺时间排除、profile 隔离、101 条和稳定排序。
- [x] T010 实现 count/list 共用同一资格函数；只限当前 profile、只读 `status='applied'`，无任何平台过滤，count 全量、items 最多 100。
- [x] T011 验证生命周期不改变 `feedback_events`，清理仍只处理 `status='new'` 并保留明确状态和事件。
- [x] T012 运行聚焦测试、相关既有 store 测试，完成 RED→GREEN；检查 `git diff --check`。
- [x] T013 只检查本包变更并提交。提交前运行 repo hygiene；提交信息 `feat: add job lifecycle storage`，邮箱必须为项目规则指定值。

## 精确验证命令

```powershell
uv run python -m unittest tests.test_webui_store tests.test_job_feedback
uv run python -m unittest tests.test_repo_hygiene
git diff --check
git status --short
```

若需要验证并发，使用测试中的临时数据库和固定时钟，不触碰真实运行数据库。不得用单次 import 成功替代 migration、rollback、idempotency 和 reminder 边界证据。

## 完成证据格式

```text
Task: 001
Changed: <仅列允许写入路径>
Tests: <精确命令与通过/失败>
Evidence: migration 28 / rollback / action matrix / idempotency / reminders / feedback isolation
Git: commit hash and subject, or explain why commit was blocked
Blocked: <无则写 none；有则给出首个失败命令和错误>
```

不得只说“已完成”，不得把未运行的测试写成通过。完成后不要启动 Task 005 或其它任务。
