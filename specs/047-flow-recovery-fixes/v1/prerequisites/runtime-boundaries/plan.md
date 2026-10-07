# 前置结构 Plan

当前只是计划。允许修改四个原文件与下列新增域；禁止任何业务/数据/接口/视觉改动。

| 原文件/当前行数 | 新文件与搬运责任 | 目标 |
|---|---|---|
| webui/pipeline_exec_search.py / 817 | webui/pipeline_search_preflight.py：_canonical_source_code、_is_source_hard_stop、_record_source_hard_stop_evidence 与 ensure_chrome/preflight 原编排，依赖经参数传入 | 原文件<=800，后续不得增长；新域约100–180行，不超过600 |
| webui/store_flow_state.py / 891 | webui/store_flow_cancel.py：cancel_task_atomic；webui/store_flow_failure.py：close_flow_task_state_atomic及closure辅助 | 原文件<600，原 StoreFlowStateMixin 组合两 mixin，不增加 store 门面业务 |
| webui/flow_service.py / 694 | webui/flow_track_operations.py：operate_track 原流程；必要的 resume_preflight 委托仍用公开服务入口 | 原文件<600，新域<300 |
| webui/flow_submission_service.py / 735 | webui/flow_preflight_recovery.py：_resume_scope/_resume_execution_config/_resume_login_space/_check_resume_block/_pause_preflight_resume/resume_preflight_track | 原文件<600，新域<600；compensation/create/submit 通过 self 公共入口复用 |
| webui/flow_service.py内共享错误定义 | webui/flow_errors.py：FLOW_ERROR_MESSAGES/public_flow_message/FlowResumeError/PlatformUnavailableError | 新域约70–120行，原service兼容re-export；recovery不反向import service |

search 保留 run_search；将现有 ensure/preflight 的失败 payload 返回或成功 None 封装为 run_source_preflight，传入 source、facade、evidence、combos、emit、stop_event、platform。调用方失败仍走原 _finish，不能把整个组合循环塞入新域。原辅助符号保留兼容 import，不拆无关 detail/账号轮询。

新增 tests/test_047_runtime_boundaries.py（约100–220行）：兼容 import、可替换 facade/ctx、mixin 方法存在、无循环依赖及行数检查。原相关测试只运行不改业务期望。

引用：search → preflight（参数注入 facade/evidence/emit）；state → cancel/failure mixin → store helper；service → operations（注入 self）；submission → recovery mixin（self 调既有 create/submit）。新域不得 import app 或反向 import 原 service 造成循环。

共享错误从flow_errors引用，FlowConflictError仍使用store_flow_core的原类；原flow_service导出同一对象以保留旧API/测试catch语义。提交lane函数仍原入口，recovery通过self.submit_scrape调用，不反向import service取调度函数。

原入口 patch 面必须仍在调用点被读取；不能在新模块 import 时捕获旧 facade 值使 tests/source 的注入失效。类属性/方法的兼容组装优先，不复制完整实现。

验证命令（各自 Task 仅跑直接受影响组合）：

```powershell
uv run python -m unittest tests.test_047_runtime_boundaries tests.test_047_split_compat tests.test_048_task_continue_split_compat tests.test_b096_flow_service tests.test_b096_quality_round4 tests.test_b096_instance_actions tests.test_whitebox_integration
```

行为任务前检查 diff：纯搬运与薄委托、函数签名/返回、SQL/事务语义均不变。六项修复允许修改的域见主 Plan；本前置不碰任何前端、迁移、正式数据或历史规格。
