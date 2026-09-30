# B096 数据模型（设计契约）

下列为满足已确认行为的内部设计选择，不构成新增用户字段或页面文案。实际列名可在实施时按现有 SQLite 命名微调，但关系、不变量和验收不得退化。

## Flow（流程）

`id`、`profile_id`、`selection`（`all`/单平台）、`created_at`、`updated_at`。一次启动只产生一个 ID；“全部”恰有 BOSS、智联两条 Track，B096 上线后新建的单平台轮次恰有一条对应平台 Track。`profile_id` 是查询、操作、历史的访问边界。客户端提交重试需用一次启动键或等价幂等约束，避免重复流程。

## FlowTrack（平台运行线）

`flow_id`、`platform`、`scrape_run_id`、`screen_run_id`、`result_run_id`、`confirmed_filters_snapshot`、`submission_snapshot_json`、`status/stage`、`error_code/reason`、时间戳。唯一键 `(flow_id, platform)`；只引用本平台、同画像、同 Flow 的 run。状态以既有持久 run/任务事实为准，Track 是归属和对外投影，不得与实际执行矛盾。确认快照与重试快照只含平台筛选值、范围和去重开关等无凭据参数，不含账号、Cookie、Key 或本地用户数据。

`submission_snapshot_json` 由迁移 039 增加，用于记录尚未产生外部 run 的本轮安全重试输入。只有 queued/paused/interrupted 且 `scrape_run_id`、`screen_run_id`、`result_run_id` 均为空时，preflight 失败才可在 SQLite `BEGIN IMMEDIATE` 中原子写入该快照并收口状态；已 claim 或已运行 Track 的重复 preflight 不得覆盖运行状态。无 run 的 paused Track 恢复时必须重新经 execute-search 的校验、claim、lane 提交和绑定边界，不能仅把状态改回 running。

可观察阶段：待启动 → 抓取排队/运行/暂停 → 抓取完成 → AI 排队/运行/暂停 → 完成；各阶段可进入失败/停止。失败线已保存的岗位/结果保留；即使 AI 结果快照尚未生成，也应从该 Track 已持久化的抓取岗位形成只属于本流程的平台结果视图，并同时标注平台失败与“未完成 AI 筛选”，不将岗位计入已筛选结果口径，也不重复显示同一岗位。没有岗位时仅显示失败。一个平台终态不改变另一平台状态；流程整体“不可新建”的判据是任一线运行、排队或暂停。服务重启后的 `interrupted` 按既有恢复语义显示，不伪称完成。

## 现有 run 与结果

`screening_runs`/结果快照按 `flow_id + platform` 或等价关系绑定，抓取父 run、AI 子 run 和结果 run 保持可追溯。当前 04 页查询必须以显式 flow ID 为边界；不能使用“每平台最新”的宽查询。每个可整理岗位保留来源平台与来源 run，单岗位动作不得误用另一平台或上一流程的 run。历史外层 Flow 的两个内层 Track 分别呈现状态与结果；没有结果快照的失败 Track 也要形成内层状态投影。结果归档沿用现有新一轮生命周期的触发时机和用户操作，但写入范围必须限定指定 Flow，不波及同画像的其他 Flow；不新增用户归档入口，不擅自增加外层删除/清空语义。

旧库既有单平台轮次不倒灌成新的双平台流程；可作只读的单轨虚拟 Flow 投影，保留原 ID 与历史可访问性。迁移完成后新建的单平台轮次必须创建真实单轨 Flow，不能继续依靠“平台最新结果”推断归属。迁移 038 是增量迁移：建表/索引及必要关系，不重写旧结果；重复执行幂等，失败不留下半成品。实现前需检查当前迁移驱动对冻结旧版测试库的保护规则。

## 不变量

1. “全部”创建前，两平台均允许新建、两套七类条件都已确认，且没有仍运行/暂停的当前流程；单平台创建前同样校验当前流程门禁。服务端再次校验，不信任客户端确认标记。
2. Flow/Track 关系原子建立；启动后的单线提交失败记入该线，另一线保持独立。
3. 任何状态/结果/历史/操作读写均校验 `profile_id` 与 `flow_id`；平台操作还校验 `platform` 属于该 Flow。
4. 一个 run/结果不能归属两个流程；重复请求不能重复启动同一平台线。
5. B106 去重策略不因 Flow 数据模型改变。

6. AI run 的 claim、来源校验和 `screen_run_id` 绑定在同一事务内完成；新 Flow 的 run 必须携带匹配的 `flow_id`、`platform`、`profile_id` 和 Track，legacy 缺失 `flow_id` 只能走明确的只读兼容投影。
7. `result_run_id` 只有与 done/succeeded 终态迁移同一事务提交；failed/stopped/cancelled Track 不得留下结果绑定。Flow 抓取、AI、Future、任务注册和外部提交失败均写入安全错误码与可恢复/终态事实，不把原始异常字符串公开。
