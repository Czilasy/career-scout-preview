# Research: B096 V2 分轨合流与统一条件映射

## R1. 并行进度不建立第二套组件体系

**Decision**：保留 `ParallelPlatformProgress.vue` 作为阶段列表壳，每条实际内容直接渲染既有 `TaskProgress.vue`。

**Rationale**：`TaskProgress.vue` 已拥有真实百分比、中文阶段、计数、失败原因、平台标识和无障碍播报。V1 并行组件用阶段猜测 35/65/100 并重做双列视觉，正是用户指出的问题。

**Alternatives rejected**：扩充 V1 并行卡片仍会维护第二套状态和视觉；复制 `TaskProgress.vue` 会产生漂移和重复修复。

## R2. 页面状态由持久 Track 事实投影，不改旧单平台状态机

**Decision**：新增 `useDiscoveryFlowPresentation.ts`，从 Flow Track 的 run id、stage、status、result id 及 `/api/task-state/{runId}` 快照派生：02 始终投影两条抓取 run；03 只投影已进入 AI 的运行线；04 在存在结果快照，或 Flow 结果确认终态运行线已有可展示岗位时解锁。

**Rationale**：V1 后台已经分轨，卡死来自旧单平台 `scrapeCompleted/resultLoaded` 没有接上 Flow。把 Flow 硬塞进 `useDiscoveryState.ts` 会扩大 1574 行超限文件并破坏单平台路径。

## R3. 自动前进采用“推进水位 + 手动停留”

**Decision**：每个 Flow 维护单调递增的解锁水位。首次观察/刷新只恢复和校正，不自动跳页；运行期间首次解锁 03 或 04 时自动前进。用户选择低于最高解锁页后标记手动停留，后续状态只更新和通知；主动回到最高页后解除停留。

**Rationale**：同时满足“领先平台推动流程”和“用户返回旧页后不抢页面”。刷新不能被误判为新跃迁。

## R4. 条件映射使用稳定统一 ID + 明确标签解析

**Decision**：`parallelFilterMapping.ts` 保存统一选项稳定 ID，以及冻结契约中每个平台对应的明确标签。运行时只做“标签精确匹配到当前 schema 稳定值”，不做相似度、区间推断或模糊兜底；缺契约标签时阻止“全部”启动并报告 schema 不兼容。

**Rationale**：平台 code 可能演进，但公开 schema 的标签和值是现有权威入口。精确解析既复用 schema，也不会把人工表退化为运行时猜测。

## R5. “全部”修改采用字段级覆盖，平台修改不回流

**Decision**：统一草稿与平台最终草稿分开保存。修改统一字段时，仅重新计算并替换两个平台的同名共同字段；其他共同字段、平台专属字段保持。平台页改动只更新该平台最终草稿。保存时比较映射结果和最终值生成 overrides。

## R6. 运行历史复用现有 JSON，常用配置单独迁移

**Decision**：

- `flow_tracks.confirmed_filters_snapshot` 保存 V2 条件快照信封；旧纯字段对象按 V1 兼容读取。
- `search_packages` 通过迁移 040 增加 `condition_snapshot_json TEXT NOT NULL DEFAULT '{}'`。
- 新保存配置使用 payload version 2；旧 version 1 仍可读取，条件视为空白且不回写原记录。

**Rationale**：Flow Track 已有冻结 JSON，新增列只会制造冗余；常用配置目前没有第三页条件，必须有独立字段才能完整恢复且不污染画像事实。

## R7. 删除确认状态要同时删除前后端门禁

**Decision**：前端删除 `platformConfirmed`、复选框、事件参数和缺失检查；`POST /api/flows` 删除 `confirmed` 参数及 `confirmations_required` 响应。主按钮提交的当前快照即最终值。

## R8. “全部”主题使用作用域覆盖

**Decision**：`useTheme.ts` 增加 `all` 作用域覆盖。现有单平台逻辑继续更新请求的平台品牌；当前流程/草稿为“全部”时，解析主题强制为中性 `all`，清除作用域后恢复最近单平台品牌。

**Rationale**：结果、恢复等旧路径仍会写平台品牌；作用域优先级避免互相抢写造成主题闪动。

## R9. 结果合流复用当前 Flow 结果端点

**Decision**：新平台结果签名出现时调用现有 Flow-scoped 结果加载，并传入 `preservePresentation`。保持当前结果分类和平台筛选；`JobWorkspace` 继续用同一 scene identity 保存筛选、排序、滚动、展开和选择。通知 id 使用 `flow_id + platform + result_run_id`，首次水合不通知。

## R10. 真实验收分源码与 EXE 两次执行

**Decision**：受控自动化后先以正式入口、正式库和真实登录态跑源码路径；再构建 EXE，以相同账号跑最小充分路径。源码通过不能替代 WebView、端口、持久目录和生命周期不同的 EXE。
