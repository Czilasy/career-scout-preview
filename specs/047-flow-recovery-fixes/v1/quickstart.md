# Quickstart: 后续执行与验证

此指南供以后获得实现授权的AI使用。本轮不运行任何下列产品命令。

## 接手顺序

1. 读取本主体 INDEX、spec、plan、research、data-model、contracts、独立前置规格/Plan及tasks。不能按数字047选到旧文件拆分主体。
2. 确认实现授权、独立主题分支/工作目录授权和未提交改动。尚未获得实施授权时只允许读/讨论；不得把已有Tasks当成实施许可。
3. 重新测目标行数与源码签名，保护他人改动。执行T001–T007结构兼容前置，再按依赖完成六项。
4. 每个任务附聚焦结果/等级/数据类型/未验证项。竞争用Event/barrier/deferred promise，不以固定sleep当正确性证据。

## 聚焦命令

仓库根：

```powershell
uv run python -m unittest tests.test_047_runtime_boundaries tests.test_047_split_compat tests.test_048_task_continue_split_compat
uv run python -m unittest tests.test_047_login_diagnostics tests.chrome_setup.test_chrome_setup tests.source.test_source_boss tests.source.test_source_zhilian
uv run python -m unittest tests.test_047_preflight_outcome tests.test_whitebox_rules tests.test_whitebox_integration
uv run python -m unittest tests.test_047_flow_lifecycle tests.test_b096_quality_round4 tests.test_b096_instance_actions
uv run python -m unittest tests.test_047_track_retry tests.test_b096_flow_service tests.test_b096_flow_api
uv run python -m unittest tests.test_047_history_lifecycle tests.test_b096_flow_history tests.test_result_history
```

在webui工作目录：

```powershell
npm test -- src/__tests__/screenFlow.spec.ts src/composables/__tests__/useDiscoveryParallelFlow.spec.ts src/composables/__tests__/useDiscoveryInstanceActions.spec.ts
npm test -- src/composables/__tests__/useFlowOperationEpoch.spec.ts src/composables/__tests__/useDiscoveryFlowPresentation.spec.ts src/components/__tests__/TaskProgress.spec.ts
npm test -- src/composables/__tests__/useExecutionPanelCollapse.spec.ts src/components/__tests__/CollapsibleCard.spec.ts src/views/__tests__/DiscoveryView.spec.ts
npm test -- src/components/__tests__/ScreenRoundActions.spec.ts src/components/__tests__/ParallelPlatformProgress.spec.ts src/components/__tests__/ResultHistoryDrawer.spec.ts
```

新测试文件在执行对应Task创建后才可运行；本轮不声称命令已经通过。

## 真实用户入口验收矩阵

使用项目已就绪的正式启动方式、真实账号与项目入口，不另搭模拟页面，不直接操纵外部CDP/登录态。每项保存用户可见状态全过程，记录输入/Flow/run关联，产物仅系统临时目录且脱敏。

| 项 | 入口/动作 | 用户可感知预期 |
|---|---|---|
| 1 | 平台未登录/已登录/未知的实际入口运行；未知无真实环境条件时明确未测 | 登录错误与未知、CDP不可用分清；原始失败不被完整性覆盖 |
| 2 | 暂停处理后继续；failed单轨retry，分别在兄弟运行/已存结果时 | 入口有效；同Flow；兄弟原进度/结果不损失 |
| 3 | 单平台和全部进度出现，02→03自动与手动 | 对应抽屉收拢；原现场/手动能力保留 |
| 4 | 02/03操作区，桌面、明暗/已注册主题 | 按钮紧凑清楚、可读可点、无遮挡 |
| 5 | 分别终止本轨/立即结束保存/等本批；在批次结束前后操作 | 不误闪硬失败；真失败可见；旧回调不改新状态；兄弟继续 |
| 6 | 已结束failed+stopped的历史删除；仍活动历史 | 已结束不误拒绝；活动受保护；单轨删不伤兄弟 |

第6项包含删除数据，真实执行前确认目标环境、明确可删对象与写入边界；未授权正式删除时保留未验证，不能替用户点击正式历史删除。自动化临时库验证不等于真实E2E。

后端源码改动在未来实施结束需按项目要求重启受影响服务并验证入口；不能在规划阶段停止/重启当前服务。工具/环境缺失如实记录，不用fixture冒充真实账号。

## 最终一次交付链

六项及前置全部收敛后：仓库根运行后端全量 `uv run python -m unittest discover -s tests`；webui运行 `npm test`、`npm run build`；根运行 `uv run python -m unittest tests.test_repo_hygiene`、`git diff --check`、`git status`。所有日志写系统TEMP，不写根目录。

全量失败保存名称/输出，仅对原失败和相邻范围返修；没有相关修复与聚焦通过，不重复全量。历史保护/未提交卫生问题单列，不改基线或擅自提交以求绿。

最终报告逐项分已实现/自动化验证/真实未测/阻断，附文件与证据。不得因为Tasks勾完就称真实验收通过；不安排提交、推送、版本提升、打包或发布。

最新范围：按用户 2026-10-07 明确要求，仅验收桌面，不考虑手机端适配（项目 AGENTS.md）。
