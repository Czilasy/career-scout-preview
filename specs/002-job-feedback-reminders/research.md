# 技术研究：岗位反馈闭环与投递过期提醒

## 研究范围

本研究只解决冻结 Spec 的实现选择：生命周期快照与事件、精确时间、幂等命令、提醒投影、AI 兜底、权威岗位身份、前端所有权和并行交付。它不重新讨论产品边界，也不引入自动平台核实、自动投递或多级提醒。

## 决策 1：平台只参与身份与链接校验，不参与业务规则

**决定**：生命周期、提醒和 AI 建议不使用 `platform` 分支或过滤。`platform` 仅用于确认 `(platform, platform_job_id)` 身份与 `canonical_url` 的 host/path 合法性。

**依据**：`profile_jobs` 的隔离键是 `(profile_id, job_id)`；`jobs.id` 已由抓取入库阶段冻结来源。migration 27 和 `TaskStore.upsert_job` 已建立 `(platform, platform_job_id)` 与全局 `canonical_url` 双索引。

**否决方案**：先只对 BOSS 加 `WHERE platform='boss'`。该条件没有产品收益，会漏掉智联数据，并在后续删除时制造已知耦合。

## 决策 2：当前快照与追加事件并存

**决定**：`profile_jobs` 保存当前状态、投递时间和最后跟进时间；`profile_job_events` 只追加保存真实变化的前后值；`profile_job_command_receipts` 独立保存幂等请求回执。读取当前 UI 和提醒走快照，历史解释走事件，重放判定走命令回执。

**依据**：纯事件溯源会迫使每次列表/提醒重放历史，超出本地应用需要；纯快照又无法解释纠正、恢复和重复请求。两者在一个事务中更新可兼顾查询简单性和可追踪性。

**否决方案**：复用 `feedback_events`。该表服务“感兴趣/不感兴趣”偏好学习，包含撤销语义；生命周期是客观求职事实，两者混用会污染 AI 偏好和事件计数。

**兼容修正**：现有部分读取路径会用历史 `feedback_events` 的聚合结果覆盖 `profile_jobs.status`。实现时必须改为当前快照优先；反馈事件继续参与偏好学习和反馈历史，但不能把 `applied/read/stale` 重新投影为 `interested`。

## 决策 3：客观事件与幂等命令回执分表

**决定**：客户端每次明确操作生成 `request_id`。`profile_job_command_receipts` 保存规范化请求指纹、`changed` 和可空 event ID；`profile_job_events` 只在实际变化时保存前后快照：

- 同一 `request_id` + 同一指纹：返回原回执的 event identity/changed，不重复写快照、回执或事件；响应中的 state 始终重新读取权威当前快照，因此更晚的已提交命令不会被旧重试覆盖。
- 同一 `request_id` + 不同指纹：`409 idempotency_conflict`。
- 新 `request_id` 的 follow-up：视为新的现实动作，刷新时间并追加事件。
- 新 `request_id` 但状态已相同：只追加 `changed=false` 的 command receipt，不伪造客观事件，确保未来重放仍可识别。

**依据**：状态设置可以天然幂等，但 follow-up 和 restore 会写“当前时间”，仅靠条件更新不能区分用户第二次确认与第一次请求的网络重放。分表后事件轨迹仍严格等于真实变化。

**否决方案**：仅在前端按钮 busy 时防重复。刷新、超时重试、两个标签页和并发请求都能绕过前端锁。

## 决策 4：所有新时间归一为 UTC，阈值按 timedelta 计算

**决定**：API 只接受带 `Z` 或显式 offset 的 RFC 3339；后端解析后归一为 UTC 存储。提醒在 timezone-aware `datetime` 上计算 `now - baseline >= timedelta(hours=720)`，恰好阈值时包含。

**依据**：日历日期差、字符串截断和无时区本地时间无法正确处理跨午夜、闰日或 DST。Python datetime 计算可直接覆盖阈值前后 1 秒测试。

**旧数据处理**：缺失或无法可靠解析的 `applied_at` 不猜测、不回填、不提醒；用户通过投递时间纠正后才进入。非空但损坏的 `last_follow_up_at` 也使该项退出提醒并暴露数据无效状态，不回退到更早 applied_at 制造误提醒。

**否决方案**：SQLite `date(applied_at, '+30 days') <= date('now')`。它丢失时分秒并把 30×24 小时变成日历日。

## 决策 5：提醒是查询投影，不持久化

**决定**：不创建 reminders 表。count 与 list 使用同一个查询服务，按当前 profile 的 `applied` 快照和 baseline 动态计算；轻量 count 端点只返回总数，列表端点返回同一总数并按 baseline 最早优先截取 100。

**依据**：提醒资格完全由持久状态和时间决定。持久化提醒会引入同步、清除和重建问题，也容易让“查看”误变成状态。

**否决方案**：后台定时写提醒表。单用户本地应用无需调度器；进程关闭时会产生陈旧提醒，增加恢复复杂度。

## 决策 6：使用命令式 API，不允许任意字段 PATCH 绕过规则

**决定**：新写入口是 `POST /api/profile-jobs/actions`，action 为固定枚举。legacy PATCH 仅作为兼容适配器，必须映射到相同领域服务，不能直接更新 `status/applied_at`。

**依据**：`mark_applied`、`restore_applied`、`follow_up` 和 `correct_status` 对时间有不同副作用，通用 PATCH 无法表达事务和事件语义。

**否决方案**：继续允许任意 `status/note/applied_at` 组合。它能产生 applied 无时间、未来时间、恢复丢失投递时间和无事件快照。

## 决策 7：pipeline 操作统一走权威双索引 upsert

**决定**：抽出 pipeline 岗位身份解析模块，接收内部 `job_id` 或完整 `platform + platform_job_id + canonical_url`。存储层把现有双索引算法抽成可接收调用方 connection 的事务内 helper；公开 `TaskStore.upsert_job` 与 lifecycle/interest/reject 的原子事务共享该 helper。BOSS 专用 `save_job` 不再用于这些入口。

**依据**：当前 `_save_pipeline_job_to_store` 只按 BOSS 规范 URL 调用 legacy `save_job`，会丢失 `platform_job_id`。现有 `upsert_job` 已具备双索引冲突算法和平台 URL 验证。

**未做事项**：不扫描标题/公司/JD 合并历史岗位，不从 URL 路径猜一个缺失的平台岗位 ID。旧身份不完整行保持不可写，直到新结果提供权威三元组。

## 决策 8：AI 建议是独立只读适配器

**决定**：`job_advice.py` 构造只含 JD、applied_at、last_follow_up_at、elapsed_days 的输入，并验证 JSON action allowlist。缺 JD 固定 `review`；其它 AI 不可用/失败/无效时，逾期且有 JD 返回规则式 `follow_up`。

**依据**：提醒和状态在 AI 不可用时仍须完整可用。物理分离的只读模块使 AI 响应无法直接提交状态。

**否决方案**：保存建议或让建议调用与 follow-up 写入同一路由。建议会随时间/JD变化，持久化会变成不可信旧事实；合并事务会让外部调用影响本地状态可靠性。

## 决策 9：顶部提醒由 App 所有，详情动作复用 slot

**决定**：`App.vue` 持有当前 profile 的 reminder count、drawer open 和刷新；`ReminderDrawer.vue` 独立处理列表/详情/快捷动作。`DiscoveryView.vue` 在现有 `JobWorkspace` actions slot 中挂载 `JobLifecycleActions.vue`，无需复制岗位详情布局。

**依据**：提醒入口跨视图存在，适合顶层所有；生命周期按钮依赖当前详情岗位，已有 slot 是最小集成面。

**并发响应规则**：profile 切换使用请求序号/AbortController，旧 profile 的响应不能覆盖新 profile；任何写入成功后从服务端快照刷新，失败保留原状态。

## 决策 10：migration 28 复用并升级现有备份门禁

**决定**：将 `_MIGRATION_BACKUP_TARGET_VERSION` 提升到 28；v27 非空数据库先生成经过 hash、manifest、只读 quick_check 和版本核对的备份，再进入 additive migration。

**依据**：新增状态和事件会形成用户事实，属于持久化严格档。现有 v27 备份设施已具备所需机制，扩展比新建第二套备份流程更可靠。

**回滚边界**：尚未产生 v28 数据可人工恢复 v27 备份；产生新事实后不得用旧备份覆盖，优先前滚修复。

## 决策 11：并行按写入所有权分波次，而非按页面随意拆分

**决定**：第一波独立实现存储、AI、pipeline 身份和前端客户端；第二波实现 API、提醒抽屉和详情控件；第三波由单一后端/前端集成所有者修改共享入口；最后串行回归。

**依据**：`app.py`、`DiscoveryView.vue`、`types.ts` 是高冲突共享文件，且当前工作区后两者已有用户改动。把共享入口留给集成波次，才能让多个 AI 会话真实并行而不相互覆盖。

**否决方案**：每个用户故事分给一个会话并同时改全栈。故事之间共享 schema、路由、App header 和详情 slot，会产生重复实现、接口漂移和难以合并的冲突。
