# Tasks 005：后台主链、恢复与平台敏感 API

## 新会话启动提示词

```text
请在当前仓库根目录执行 specs\001-add-zhilian-platform\tasks005.md。先读取仓库根目录 AGENTS.md、智联功能目录全部冻结工件、tasks001.md、tasks002.md 和 tasks004.md；正式实施前输出【已查阅】。

本会话只负责 tasks005.md。先现场核验 migration 27/store API 与智联 JobSource adapter 的实际代码、聚焦测试和独立提交。若 store 已完成但 adapter 未完成，只允许基于 FakeJobSource 做独立编排工作，不得连接真实智联或宣称任务完成。每次首次写 run、推进 source outcome、创建 AI run、恢复状态或关闭浏览器前，都执行对应节点门禁；禁止从 UI 当前平台、最近结果或默认 BOSS 推断旧 run。保证 source attempt 先持久化再推进状态，所有外围操作从目标 run 继承冻结身份。完成后只提交本任务改动，不 push，不自动执行 tasks006 联调或 tasks008。
```

## 给独立执行 AI 的指令

本任务负责 screening_runs 主链和所有会继续访问 source、改变 run 状态、关闭浏览器或删除结果快照的外围入口。新会话必须读取根规则、全部冻结工件及 `tasks001.md`、`tasks002.md`、`tasks004.md`。

## 总前置门禁

现场核验 migration 27/store API 与 JobSource adapter 都存在，各自聚焦测试通过且有独立提交。若 store 已完成但 adapter 未完成，只允许使用 FakeJobSource 编写编排测试，不得连接真实智联或宣称本任务完成。

## 允许与禁止范围

允许修改：`webui/pipeline_exec.py`、`webui/app.py` 中新主链和平台敏感路由、`webui/process_executor.py`、直接的主链/API/并发测试。

禁止修改：migration DDL、智联选择器、Vue、调优 runner。禁止从 UI 当前平台、最近结果或默认 BOSS 推断既有 run 的平台。

## 节点门禁 A：创建 run 与 source attempt

首次写 run 前，程序化检查 store 能保存 platform、scope digest、task digest、账号、端口和 profile_key；首次推进组合前，检查 `append_source_attempt` 和最新 attempt 汇总真实存在。缺任一 API 时停止该节点，不得临时写 JSON artifact 替代。

- [ ] T401 为搜索预览/创建的平台注册、城市、scope digest、禁用状态和非空 filters 拒绝编写 API 测试
- [ ] T402 实现 `/api/search-scope/preview` 和 `/api/execute-search`，冻结单一平台和完整 runtime，搜索 run 的筛选快照保持空
- [ ] T403 从冻结 runtime 创建 source，禁止读取当前 UI、活动账号或默认端口
- [ ] T404 在任何完成键、run 进度、状态或 snapshot 更新前追加 source attempt；持久化失败时不得推进
- [ ] T405 按每个 combo 最新 attempt 汇总进度和历史，刷新/重启后不从岗位数为零推断 empty

## 节点门禁 B：AI run 与结果身份

首次创建 AI run 前，检查父 search run 平台、schema 版本和完整快照存储 API；首次返回结果前，检查结果模型能同时返回 platform_job_id 与可空内部 job_id。缺失时停止，禁止复用同名旧 job_id 字段。

- [ ] T406 为跨平台字段、schema 版本、旧快照不可解释和客户端覆盖父平台编写 `/api/ai-screen` 测试
- [ ] T407 实现 AI run 从父 run 继承平台/scope/runtime，并保存字段稳定值和当时标签的完整快照
- [ ] T408 将粗筛、精筛、匹配/不匹配/不确定/失败结果持久化为完整平台快照
- [ ] T409 实现 latest result 的全局最近、按平台最近、精确 run 三种查询及错配阻断

## 节点门禁 C：状态与恢复

首次恢复前，检查 canonical DB 状态与公共状态映射、interruption_kind、checkpoint 身份字段和原子 claim API 齐全。任一缺失时不得把 interrupted/paused 直接改 running。

- [ ] T410 覆盖 queued/running/paused/succeeded/partial/failed/interrupted 的唯一公共映射和四类非终态恢复测试
- [ ] T411 实现登录/验证/限流/封禁/CDP 为 paused，网络/超时有限重试后 failed，结构失效 failed，单详情异常 pending/partial
- [ ] T412 实现 continue 的平台、scope、城市、schema、runtime、digest 和进度一致性校验及单一原子 claim
- [ ] T413 实现服务重启把 running 持久化为 `interrupted/process_restart`，并按原平台显式恢复

## 节点门禁 D：平台敏感外围入口

每个入口第一次访问 source 或关闭浏览器之前，都必须重新从目标 run 读取 `platform/cdp_port/profile_key/task_input_digest` 并验证占用 profile。不能共用一次全局检查。

- [ ] T414 实现 task state/progress 返回目标 run 真实平台和 source outcome 摘要
- [ ] T415 实现阶段取消和通用取消：先 durable 写 `interrupted/user_cancelled`，再发目标 stop event，只处理已知登录空间
- [ ] T416 实现提前结束仅接受 paused 或可恢复 interrupted，生成同平台 partial snapshot，拒绝 user_cancelled
- [ ] T417 实现单 JD、单项补抓和批量补抓以 source_run_id + platform_job_id 为权威，并让子 run 继承完整 runtime
- [ ] T418 实现结果 reset 优先显式 run_id，仅清定义的 snapshot/临时行并保留 source attempts、岗位、收藏和其它 run
- [ ] T419 实现浏览器账号 activate/open/delete 与 `/api/check` 的平台语义和双平台资源保护接线

## 完成门禁

先运行原失败用例和直接回归，再运行：

```powershell
uv run python -m unittest tests.test_webui_app tests.test_healthy_pipeline tests.test_concurrency tests.test_process_executor tests.test_pipeline_tasks_cleanup tests.test_webui_browser
uv run python -m unittest tests.test_repo_hygiene
```

必须证明 source attempt 先于状态推进、状态映射唯一、并发 continue 只成功一次、草稿切平台不改变旧 run、未知 profile 不被关闭。检查 diff 后独立提交。

## 解锁条件

完成后解锁 `tasks006.md` 的真实 API 联调和 `tasks008.md` 的主链集成候选；只有 `tasks007.md` 也完成后才允许最终集成。
