# 前置结构 Spec：047 执行与运行状态域分流

**主体**：六项流程修复必经执行/状态域的结构边界，独立于业务行为变更。
**授权阶段**：只规划，不实施；不创建另一编号目录，不改既有 047-large-file-followup。
**依据**：宪法 I/II/IV/VI。当前 search/state 超红线；service/submission 达预警。为后续修复提供内聚入口。

## 冻结结构范围

只搬运既有实现，保留函数、类、公开导入、上下文注入、patch 面、返回数据、状态优先关系、路由与事务顺序。不改行为、错误码、SQL 语义、数据库结构或界面；不解决六项问题。

1. pipeline_exec_search 的源失败辅助与预检编排搬到 pipeline_search_preflight；原 run_search 保持入口。
2. StoreFlowStateMixin 中 cancel_task_atomic 搬到 store_flow_cancel；close_flow_task_state_atomic 及其 closure 辅助搬到 store_flow_failure；原类组合这两个 mixin，保留 finish 方法及旧导入。
3. FlowService 的普通 operate_track 细节搬到 flow_track_operations，原方法薄委托；公共入口及错误类型不变。
4. FlowSubmissionService 的 scope/config/login/preflight resume 编排搬到 flow_preflight_recovery，原实例通过 mixin/公开委托兼容；记录创建/submit/compensation 仍使用现有方法。
5. 原FlowService的FLOW_ERROR_MESSAGES/public_flow_message/FlowResumeError/PlatformUnavailableError搬到flow_errors，原模块兼容re-export；恢复新域从flow_errors引用，避免反向依赖service。只搬运，不改码/文案/异常类型身份。

## 验收

- 拆分前后已列聚焦兼容行为一致；增加结构检查，不修改旧断言以掩盖差异。
- 原公开 import、patch 注入和 StoreFlowStateMixin 组装可用；无循环引用、无重复复制两份实现。
- search 降至800以内且后续不增长；state、service/submission 分流后低于600；新 Python 域低于600。search 的行为新增全部落新域，遵守预警线分流。
- 只完成结构前置，不运行独立后端全量；最后随六项同一交付链验证。

## 文件与交付

精确白名单、引用及验证见同目录 plan.md；执行任务为主 tasks.md 的 T003–T007。以后先报告搬运前后行数与兼容结果，之后才执行行为任务，不能同一步混改。
