# 048 实施计划

## 设计

保留 `task_continue_api.py` 作为路由注册 facade，将结束保存及部分结果快照编排提取至 `task_continue_finish.py`。提取模块不反向 import API；facade 在注册时传入自身模块对象，提取模块在请求执行时读取该对象上的依赖，保持既有测试替换和 Flask 组装契约。

B096 的 Flow API 查询通过 `FlowService.list_flows()` 与 `FlowService.get_flow_results()` 暴露；Flow Track 暂停前由 service 检查真实运行 task 的 `finalizing` 标记。B096 抓取提交与 AI 提交分别经 `FlowSubmissionService`、`FlowAiCoordinator`，API 只保留输入校验和响应收口。

## 文件边界

允许修改：

- `webui/task_continue_api.py`
- `webui/task_continue_finish.py`
- `webui/flow_api.py`、`webui/flow_service.py`
- `webui/flow_submission_service.py`、`webui/flow_ai_coordinator.py`、`webui/flow_task_state.py`、`webui/store_flow_state.py`
- 对应兼容、Flow API、状态一致性测试和 B096 工件

禁止修改：

- `scripts/boss/`、`scripts/zhilian/`
- `webui/cross_platform_dedupe.py`
- `specs/019-cross-platform-job-dedup/`

## 验证门禁

1. 先运行拆分/调用边界 characterization tests，观察 829 行和缺少 service/coordinator 接口时失败。
2. 运行续跑/暂停/取消/finish 直接回归、Flow API finalizing 回归和状态一致性测试。
3. 核对所有新增/修改 Python 行数、`py_compile`、`git diff --check` 和禁区差异。
4. 真实平台账号 E2E 不属于本 Spec；若环境没有正式账号，只记录未验证。

## 当前证据（2026-09-27）

- `task_continue_api.py`：829 → **550** 行。
- `task_continue_finish.py`：**296** 行；`flow_task_state.py`：**192** 行；`store_flow_state.py`：**242** 行；`flow_submission_service.py`：**195** 行；`flow_ai_coordinator.py`：**472** 行。
- `uv run python -m unittest tests.test_048_task_continue_split_compat tests.test_b096_quality_round4 tests.test_b096_production_flow tests.test_b096_round2_review tests.test_b096_round3_review`：33/33 通过；包含真实 Flask Flow HTTP 路由边界 spy 与 SQLite 故障触发器原子回滚测试。
