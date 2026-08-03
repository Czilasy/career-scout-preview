# Tasks 007：调优实验与 Legacy BOSS-only 边界

## 给独立执行 AI 的指令

本任务处理两个独立但边界明确的横向能力：调优五类 round 的平台守恒，以及旧 BOSS-only 路由对显式智联的零副作用拒绝。开始时读取根规则、全部冻结工件和 `tasks001.md`；到相应节点再核验 `tasks002.md`、`tasks004.md`。

## 总前置门禁

必须确认平台注册和 legacy 平台解析助手已经存在。若 migration/store 或智联 adapter 尚未完成，可先编写不依赖它们的失败测试和纯校验器，但不得执行相应接线或宣称整个任务完成。

## 允许与禁止范围

允许修改：`webui/tuning.py`、调优相关 app 路由的最小接线、legacy BOSS-only 路由前置校验、`tests/test_tuning.py`、`tests/test_webui_app.py` 中专用参数化测试。

禁止修改：migration DDL、source 选择器、主链状态编排、Vue。禁止为兼容旧 artifact 改写已签发 JSON/digest 或猜填未知摘要。

## 节点门禁 A：Legacy 零副作用拒绝

每类路由第一次修改前，读取 `contracts/http-api.md` 的完整 Legacy 矩阵并对现有副作用做前后快照。拒绝必须发生在任务/对象查询、事件、artifact、浏览器或 profile 操作之前。

- [ ] T601 为全部 legacy GET query 和 POST body 的显式 zhilian/未知平台编写参数化测试 `tests/test_webui_app.py`
- [ ] T602 覆盖 `/api/tasks`、`/api/scrape`、`/api/setup-chrome`、旧 task 子路由、`/api/results`、`/api/confirm-fields` 和 `/api/search-runs` 全部矩阵
- [ ] T603 实现 zhilian 返回 `legacy_platform_not_supported`、未知平台返回 `platform_validation_failed`，并保证数据库、事件、artifact、浏览器、profile 和注册表零变化
- [ ] T604 回归显式 boss 与省略平台的既有行为，并让成功对象明确标识 `platform=boss`

## 节点门禁 B：调优持久身份

首次写 experiment/workload/manifest/artifact 前，现场核验 `tasks002.md` 的 v27 外层列、store 方法和迁移测试通过。缺任一字段或方法时停止本节点，不得把平台只塞进 artifact JSON。

- [ ] T605 为 experiment、workload、source scope、input artifact、manifest、stage artifact 和 program evidence 的平台/runtime/digest 守恒编写测试
- [ ] T606 实现新 experiment 显式冻结 platform、规范城市解析、schema、账号、端口、profile_key、scope/task digest `webui/tuning.py`
- [ ] T607 实现 manifest fixed_fields/frozen_input 和数据库外层身份一致性校验，并让 digest 覆盖全部冻结字段
- [ ] T608 实现旧 BOSS manifest/artifact 的客观证明算法；证据不足时阻断，原 JSON/digest 保持不变

## 节点门禁 C：五类 round

首次创建 JobSource 前，现场核验 `tasks004.md` 的 factory 能按 manifest runtime 创建 boss/zhilian adapter，且 adapter 测试通过。缺失时只保留测试，不得回退全局 BOSS source。

- [ ] T609 固定 stage 仅为 list/detail/rough/fine/end_to_end，并固定 source_artifact_kind 只有 list/detail 可复用
- [ ] T610 实现 list 从 manifest 创建对应 adapter并生成 list source artifact
- [ ] T611 实现 detail 只接受同平台同 workload 的 list artifact，并生成 detail source artifact
- [ ] T612 实现 rough 只读取 list artifact、fine 只读取 detail artifact，二者不创建 JobSource且继承平台/schema
- [ ] T613 实现 end_to_end 从 manifest 创建 adapter、source_artifact_kind 为 NULL 且不能被 rough/fine 复用
- [ ] T614 对 experiment/workload/artifact/manifest 外层与 JSON/program evidence 任一错配在 source 或 AI 前阻断
- [ ] T615 实现禁用平台不签发或执行新的 source round、历史证据保持可读、取消只处理已知平台登录空间

## 完成门禁

```powershell
uv run python -m unittest tests.test_tuning tests.test_webui_app tests.test_repo_hygiene
```

必须证明五类 round、两类 source artifact、legacy 全矩阵和零副作用快照全部通过。检查 diff 不含 migration、scraper 或主链重写后提交。

## 解锁条件

只有 Legacy 与调优两部分均完整通过才解锁 `tasks008.md`。只完成其中一部分时不得把本文件标记完成；新会话应从未通过的节点继续。
