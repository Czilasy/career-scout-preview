# Tasks 002：Migration 27、持久化与岗位身份

## 新会话启动提示词

```text
请在当前仓库根目录执行 specs\001-add-zhilian-platform\tasks002.md。先读取仓库根目录 AGENTS.md、智联功能目录全部冻结工件和 tasks001.md；正式实施前输出【已查阅】。

本会话只负责 tasks002.md。首先现场核验 tasks001 的平台内核、测试证据和独立提交是否真实完整；前置门禁不通过就停止并列出缺失项，不要自行补写或重做 tasks001。只修改 tasks002 允许范围，首次使用前序符号、执行 migration 27、实现双身份 API 时分别执行文件内节点门禁。必须使用临时 v26 数据副本，不得在唯一正式数据库试迁移。逐项实施、测试和勾选；完成后运行聚焦验证与仓库卫生检查，只提交本任务改动，不 push，不自动执行 tasks005 或 tasks007。
```

## 给独立执行 AI 的指令

本任务可由全新 AI 会话执行。先读取根 `AGENTS.md`、本功能全部冻结工件和 `tasks001.md`。本任务独占 migration 与 `webui/store.py` 的平台持久化改造；不得实现 source、Vue 或调优 runner。

## 总前置门禁

不要相信其它会话的完成声明。必须现场确认：`webui/platforms.py` 存在且可解析 `boss/zhilian`；`JobSource` 已有 `platform` 合同；scope/task digest 已包含 platform；`tasks001.md` 的完成测试通过；git 历史存在对应独立提交。任一项缺失时停止本任务并报告缺失的符号、测试或提交。

## 允许与禁止范围

允许修改：`webui/store.py`、启动前数据库 bootstrap 的独立辅助模块、`webui/app.py` 中 TaskStore 构造前的最小调用点，以及 `tests/test_webui_store.py`、migration 专用测试。

禁止修改：`webui/source.py`、智联 scraper、Vue、主链状态编排、调优 runner 业务逻辑。禁止在唯一正式数据库上首次试迁移，禁止修改旧内部 UUID 或猜造旧平台岗位 ID。

## 节点门禁 A：备份 bootstrap

首次改 TaskStore 构造前，确认当前应用在哪里创建 TaskStore、何时自动 migration。若无法证明备份发生在任何 v27 写入之前，禁止进入 migration 实现。测试必须使用临时 v26 副本。

- [ ] T101 盘点数据库初始化、schema version、migration、收藏和反馈外键，记录 v26 基线与构造顺序
- [ ] T102 先在 `tests/test_webui_store.py` 添加迁移前 SQLite backup、manifest、SHA-256、只读 quick_check、源版本一致和失败阻断测试
- [ ] T103 实现 TaskStore 构造前 bootstrap 备份与验证，产物仅写入本地忽略目录且日志不含绝对路径
- [ ] T104 验证备份失败时应用拒绝构造 TaskStore、源库未产生 v27 部分写入，并验证重复启动幂等

## 节点门禁 B：执行 migration 27 前

必须通过 T102-T104；必须保存 v26 表行数、全部旧 `jobs.id`、收藏/反馈关联计数。若旧 schema 与 `data-model.md` 假设不同，停止并报告实际 DDL，不得用宽松 SQL 跳过约束。

- [ ] T105 为 jobs、各类 run、结果、待确认、调优外层身份和筛选快照编写 migration 27 失败优先测试 `tests/test_webui_store.py`
- [ ] T106 实现新增 platform、platform_job_id、经验、学历、extra、筛选快照、task digest 和 interruption kind 字段 `webui/store.py`
- [ ] T107 将 `scrape_run_jobs`、`screening_pending_results` 和 `screening_results` 的平台原始 ID 物理语义统一为 `platform_job_id`，必要时使用新表复制与原子替换
- [ ] T108 创建追加式 `screening_source_attempts` 及枚举、计数、空证据、外键和 `(run_id, combo_key, attempt_no)` 约束
- [ ] T109 为调优 experiment、manifest、stage artifact 增加冻结规定的外层列，只按客观迁移前证据回填 BOSS，不改旧 JSON/digest
- [ ] T110 在同一事务内执行外键、重复身份、URL 归属、旧 UUID、收藏/反馈计数和调优摘要守恒检查，失败整笔回滚

## 节点门禁 C：双身份存储 API

只有 migration 27 的升级、回滚、幂等和守恒测试全部通过后才能实现新写入 API。必须现场确认 `tasks001.md` 的 URL 平台校验函数存在；不存在时停止此节点，不得在 store 内复制另一套 host/path 规则。

- [ ] T111 为 Job 双索引冲突算法八个分支编写事务测试，覆盖跨平台同裸 ID、URL 变化和两个索引命中不同 UUID
- [ ] T112 实现先 URL 平台校验、再 `(platform, platform_job_id)` 与全局 canonical_url 双索引查询的原子 upsert `webui/store.py`
- [ ] T113 实现结果快照同时保存 platform、platform_job_id、可空内部 job_id 和完整岗位字段的读写 API
- [ ] T114 实现 source attempt 追加及按 run/combo 最新 attempt 汇总 API，禁止从零岗位反推 empty
- [ ] T115 实现筛选快照、task digest、interruption kind 和 checkpoint 身份一致性的持久化读写 API
- [ ] T116 实现收藏/反馈所需的原子“岗位 upsert + 内部 UUID 关联”存储操作，不把 platform_job_id 当内部 UUID

## 完成门禁

运行 migration 聚焦测试和完整 store 回归；至少包含 v26 正常升级、备份失败、中途失败回滚、二次启动、外键、旧 UUID/关联守恒、source attempt 约束和双索引冲突。然后运行仓库卫生测试。

```powershell
uv run python -m unittest tests.test_webui_store tests.test_indexes tests.test_repo_hygiene
```

检查 diff 仅含本任务范围，使用仓库规定邮箱提交。不得提交临时数据库、备份、manifest 或本地绝对路径。

## 解锁条件

完成后解锁 `tasks005.md` 的持久化接线和 `tasks007.md` 的调优存储接线。两者在第一次调用新 API 前必须重新运行对应 store 聚焦测试并核验字段/方法真实存在。
