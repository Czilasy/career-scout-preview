# Data Model: 当前尝试、闭包与删除资格

## 现有实体

| 实体 | 关系与处理 |
|---|---|
| flows | id/profile_id/selection/start_key；由 tracks 派生状态。retry 不重建 |
| flow_tracks | 固定 id/flow_id/platform；status/stage 与当前 scrape/screen/result id、submission snapshot。只换目标尝试 |
| screening_runs | 执行/结果 record_kind 分开；原失败原因与 interruption_kind 保留，旧执行不改成功 |
| search_runs | 同 id 抓取 execution 的账本；状态收口一致，不能残留 queued |
| tasks/live task | logging 审计锚点不是 worker；真实 active、finalizing、stop mode 参与保护 |
| 白箱 | owner id/units/events/revision；retry 新 owner，resume 原有重开，迟到不改既定结论 |
| result snapshot | 不可变快照及来源身份；claim 不删除，提交成功才换当前指针，兄弟不变 |
| scene | profileId/runEpoch/platform/cardOpenStates；继续现有 schema 和三抽屉现场 |

不新增迁移/表/status。新增关联 `retry_of_run_id` 写现有 execution_params JSON，必须指向同轨旧 execution，不能指向别画像、别轨或结果快照。

## 恢复转换

| 输入 | 动作 | 结果 |
|---|---|---|
| paused，无 run，有有效 snapshot | resume | 真实预检后原轨新提交；仍阻断留 paused |
| paused，有运行/断点身份 | resume | 原 continuation，不重置兄弟 |
| failed，抓取失败/无 run | retry | 精确 failed 版本 CAS，新抓取 run，同 Flow/Track |
| failed，AI 且抓取输入可读 | retry | 新 AI run，复用 source scrape，不重新抓兄弟 |
| failed，缺快照/归属冲突 | retry | 真实错误，原记录和结果不损失 |
| done/succeeded/stopped/cancelled | resume/retry | 保留原终态保护，本轮仅扩 failed retry |

专用 retry CAS 不修改普通 `_TRACK_STATUS_TRANSITIONS` 的 failed 保护。校验旧 run id/Track updated_at；无 run 校验 Track 失败版本。新 run 与当前绑定同事务，成功后才 submit worker。失败补偿只针对该新尝试。

同事务核对目标仍为该画像当前Flow及其他Flow活动门禁；新轮创建/旧轮retry竞争不得产生两个活动流程或由历史轨道抢当前Flow。

## 当前写权

每次操作/回调匹配 `(profile_id, flow_id, track_id, platform, stage, current_run_id)`；execution JSON 只能证明曾归属，不能赋予旧 run 当前写权。

完成、暂停、取消、保存、失败、retry 均在写事务再查。新绑定之后旧 worker 可写安全迟到诊断，不能改当前 Track/run/result，也不能改旧既定终态。

前端 epoch 只防陈旧响应，不持有 durable 推进权；刷新/冷启动从库恢复。

## 正常收尾

task-state 兼容扩展 `closure: {kind:'finish'|'stop', phase:'pending'|'committed'} | null`。

- finish pending：明确 user_finished claim，结果尚未提交绑定；显示收尾进行中，不声称成功。
- finish committed：精确 run 正常收尾、Track 终结与结果绑定一致；正常保存，仍是实际部分完整性。
- stop pending/committed：既有停止请求/取消事实；不暗示保存成功，不创建新结果。
- 真保存、白箱、清理错误仍独立显示；不能仅凭本地 busy 推 committed。
- 缺正面证据为 null；process_restart/operator_stop 不能冒充 user_finished。

具体执行原因优先展示，完整性并存；没有具体原因才用完整性主因。正常停止不升级完整成功。

## 闭包与删除

抓取 execution/search 终态与 Track 阶段在统一事务一致发布；AI 失败不能把已成功 source scrape 改失败。每段保留自己的事实。

旧 queued search 可证明残留需：Track 终结、同 id screening execution 终结、精确归属、无 active task/子任务、无 retry/finish pending。partial 表示结束但不表示成功；interrupted 要核对原因，不能一概可删除。

删除目标 result id / 空结果 track id → 同轨 execution（含 retry 旧尝试）→ source/child → 快照/白箱/日志。不越入兄弟、别 Flow/画像或用户岗位资产。

GET 只读 can_delete/delete_block_reason。DELETE BEGIN IMMEDIATE 再查权限/共享/活动/pending，局部处理已证明旧 queue 再删除；失败整事务回滚。
