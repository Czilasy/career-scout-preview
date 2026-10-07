# Research: 047 六项修复的源码依据

日期：2026-10-06。只读源码、当前规格及界面基准；未运行产品测试、探测账号或修改正式数据。可编码的架构选择已明确；未取证的历史瞬间不冒充已重现。

## R1 登录识别及安全诊断

- 已读 `scripts/boss/login.py:53-165`、`webui/source_boss_cdp.py:85-161`，chrome_setup 四态与 source preflight 测试。
- background target → attach → 导航 → XHR 探测；空响应、解析异常、code 37、CDP 异常可返回 unknown。非预期 JSON 结构当前存在落入 not_logged_in 的风险。
- source 先检查 CDP version，再缓存/probe；明确未登录给 source_login_required，受限给 source_account_restricted，未知给 source_status_unclear。
- 决策：四态按证据分开；平台判定留树枝；仅白名单 HTTP/结构类别/已知码/阶段摘要，不存正文或凭据；公开 bool/四态入口兼容。
- 不采用：CDP 启动等于登录、unknown 兜底未登录、未查证业务码映射、原始响应全文日志。
- 历史线索：故障时缓存为 unknown，具体响应未留存。后续新诊断与真实入口收证，不能补造历史响应。

## R2 具体预检原因被完整性覆盖

- 已读 `pipeline_exec_status.py:87-100`、`pipeline_exec_search.py:232-244`、`pipeline_task_outcome.py:166-214`、`runners/pipeline_task.py:41-87`。
- 非 SYSTEMIC 预检只留下 error 文本；搜索只对 hard_stop 写证据；未执行单元形成 unit_evidence_missing，outcome 又把它作为当前原因。
- 决策：失败载荷始终带规范原始码；预检未执行事实如实记录；执行原因与完整性并存，不降低 033 成功标准。
- 不采用：所有错误一律 hard_stop、填造 completed 单元、只修前端文案。

## R3 继续已有，失败单轨重试缺少全链路

- 已读 FlowService operate_track/resume_preflight_track，FlowSubmissionService，store_flow_claims，FlowAiCoordinator，screenFlow/InstanceActions/ParallelPlatformProgress。
- no-run paused 可复用 snapshot 重新预检/提交；有 run paused 走 continuation。failed 被后端终态保护与前端 none 同时挡住。
- 额外错误：submission `_stable_probe_code` 将 source_status_unclear 映射为 source_cdp_unavailable。
- 决策：明确 retry action；failed 专用 CAS，新 run，旧记录保留；抓取/AI 各用已有提交域。resume 不放开 failed。
- 不采用：只有按钮、修改全部终态转换、重启整个 Flow、重抓兄弟、清除旧失败证据。

## R4 收拢入口未统一

- 已读 `useDiscoveryExecution.ts:621-622,966`、`useDiscoveryFlowCoordinator.ts:581-606`、CollapsibleCard restore/persist、scene store。
- 单平台启动收拢 refs；全部直接 flow.start，自动 AI 也不经手动 screen start；scene 恢复可能再次写展开。
- 决策：共享阶段进度首次出现 helper，边沿收拢，现有 refs/持久化，不新增 schema 或跨轮偏好。
- 不采用：每个平台各写一套、所有抽屉默认关闭、每次 poll 关闭手动展开。

## R5 按钮密度未被组合动作复用

- 已看用户截图和 `design/dark/dark-2-status.jpg`、`dark-3-bottom.jpg`；已读 design README/CURRENT。图片仅本地参照，不复制个人/岗位内容。
- 全局按钮高 44，CTA 50；screen-card-actions 和 recrawl 已有局部紧凑规则，组合卡没继承；保存与终止同为 danger。
- 决策：共享操作区局部密度、层级；复用现有主题，不改全站按钮或冻结候选像素。
- 不采用：平台组件复制动作判断、新主题、通过不可点击的小尺寸求紧凑。

## R6 收尾和投影有分段发布窗口

- 已读 store claim/finish/cancel、task_continue_finish:548-565、whitebox/evidence、FlowPresentation:643-654、ParallelFlow refresh/operate、InstanceActions。
- claim 先写 run user_finished/interrupted，结果绑定后 Track 才 stopped；白箱先终结。前端两阶段 items 在两次 await 后分别发布；operate 直接写响应，refresh 有另一个 generation。
- TaskProgress 有 userFinished/roundClosed，组合只传 roundClosed，兄弟仍运行时无法解释本轨正常收尾。
- 决策：closure pending/committed；当前绑定和 epoch；两段一次发布；迟到回调只留诊断，不改终结。stop/finish 分开测。
- 不采用：延迟隐藏红色、停止整轮轮询、吞错、未保存先宣布成功。
- 历史线索：曾见结束后旧批次 failure 写入；无完整浏览器瞬间序列，不能声称闪红已重现。

## R7 删除保护读到残留 search queue

- 已读 store_result_history_mixin:158-242、result_history/api、store_flow_results、ResultHistoryDrawer、test_b096_flow_history:236-330。
- 前端只查 Track 活动态，后端事务检查 screening/search/tasks；历史线索 Track 已 failed/stopped，同 id search 仍 queued，因此被拒绝。
- 决策：新终结统一账本；查询/删除共同资格；旧 queued search 仅在归属、真实 execution 终结和无实际活动 task 等证据齐备时局部处理，DELETE 事务重查。
- 不采用：忽略所有 search 状态、批量改正式库、让用户重复结束、默认整 Flow 删除。

## R8 文件边界和复用

- 行数实测：pipeline_exec_search 817、store_flow_state 891；flow_service 694、flow_submission_service 735。Python 红线 800、预警 600。DiscoveryView 1178（Vue 预警）；useDiscoveryExecution 1355（TS，不错误套用 Python/Vue 红线，但避免继续扩张）。
- 决策：独立结构前置 Spec/Plan，只搬 preflight、cancel/failure、operations、preflight recovery；新增 retry/lifecycle/history/panel/epoch 域；不做全项目拆分。
- 现成零件：FlowSubmissionService、FlowAiCoordinator、claim/finish/cancel、registry、WhiteboxService、TaskProgress、ScreenRoundActions、scene 和现有代次。缺陷组合无需新外部框架/付费服务。

## 查阅与限制

追加查阅 `webui/store_runs.py:158-250`：create_screening_run使用INSERT OR REPLACE；update_screening_run将succeeded/partial都视为terminal_success并清空error字段，partial传error_code也不写。因此新store_run_lifecycle保留有具体失败的partial原因并同步ledger，不能只给旧write_run加error参数。

retry事务内创建新记录严格INSERT新id，禁止REPLACE旧attempt；claim后复用提交/过滤快照/worker初始化时跳过已创建主记录和重复Track绑定。AI/抓取同理；后续初始化失败补偿当前新尝试。

已读本主体工件、宪法、046 INDEX/v2 页面投影契约、033 INDEX/v2 规格、roadmap 入口/平台知识、design 基准、上述源码与相关测试。实际 033 路径为 specs/033-log-whitebox；未找到的猜测路径未作为方案依据。

历史登录响应与毫秒级闪错仍待以后用户入口取证；这不允许执行 AI猜需求或降低验收。所有测试/实测均未执行。
