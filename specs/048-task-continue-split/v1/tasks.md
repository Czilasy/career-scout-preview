# 048 任务清单

- [x] T001 增加续跑 facade 行数、路由注册与 service/coordinator 边界 characterization tests。
- [x] T002 提取结束保存路由到 `task_continue_finish.py`，保持 facade 注册入口和动态依赖兼容。
- [x] T003 将 Flow API 列表/结果查询接入 FlowService，并增加 finalizing 直接 API 状态守卫。
- [x] T004 增加状态感知关闭协调器，统一 recoverable paused 与 failed 的 task、screening run、search run、Flow Track 状态。
- [x] T005 将 B096 抓取提交与 AI 提交的 claim、run 创建/绑定、lane submit 和失败补偿移入协调模块。
- [x] T006 运行拆分兼容、B096 聚焦、直接相邻回归、py_compile 和 diff-check，并记录未验证边界。

## Definition of Done

- `task_continue_api.py` 与所有新增 Python 模块低于 800 行。
- 既有续跑/暂停/取消/结束保存 HTTP 契约及直接回归通过。
- Flow API 不直接访问 `service.store.*`；B096 提交协调通过 service/coordinator 到 store。
- recoverable browser/CDP 失败不依赖后续迁移改写，持久状态一致为 paused；不可恢复失败一致为 failed。
