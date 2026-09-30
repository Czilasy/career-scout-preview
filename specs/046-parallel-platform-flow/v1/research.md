# B096 研究与设计决策

## 决策 1：沿用现有双平台执行能力，新增共享流程协调

- **Decision**：不复制平台抓取器，不接入第三方聚合服务；B096 的流程、状态、结果归属放共享 WebUI 层。
- **Rationale**：B096 需要与本项目的 CDP 登录空间、现有筛选结果和历史契约一致。两个平台已有独立执行入口，缺口在调度和归属。
- **Alternatives considered**：外部 [`loks666/get_jobs`](https://github.com/loks666/get_jobs) 展示多站抓取思路，但许可/验证范围不适合直接复用；[`vvvsrx/get_jobs`](https://github.com/vvvsrx/get_jobs) 的 README 表示不再维护且架构不同；[`eatmoreduck/boss-zhipin-scraper`](https://github.com/eatmoreduck/boss-zhipin-scraper) 仅覆盖 BOSS。仅借鉴公开思路，不复制代码。

## 决策 2：用持久流程 ID 约束每次结果查询

- **Decision**：创建流程及每平台运行线，绑定既有抓取/筛选 run 与结果快照；当前页、刷新恢复、现有归档行为、历史均经同一流程 ID 定界。旧单平台轮次提供兼容投影，迁移后新建单平台轮次创建真实单轨 Flow。AI 结果尚未形成而该线已有持久抓取岗位时，按该线来源展示部分岗位，并明确标为失败且未完成 AI 筛选；无岗位的失败线仍保留历史内层状态项。
- **Rationale**：当前 `useDiscoveryResults.ts` 的“每平台最新结果”会误把上一流程数据拼进 04 页，浏览器本地快照也不足以在重启后作为权威事实。
- **Alternatives considered**：继续查每平台最新 run、按画像归档所有当前结果或仅用 sessionStorage 聚合；均无法证明流程归属或避免误作用其他流程。

## 决策 3：并行执行必须先隔离资源和身份

- **Decision**：后端先做并发约束与冻结身份安全检查，再把执行器扩到允许两平台同时运行；同平台/同浏览器资源冲突仍阻断，跨流程的新建门禁仍阻断。
- **Rationale**：`app_support.py` 只有一个 worker；`browser_support.py` 将任意 running/queued 视作所有账号占用；`pipeline_exec_accounts.py` 用进程级 `_ACTIVE_CDP_DATA_DIR`，直接增至两个 worker 会造成 profile 竞争。BOSS/智联默认不同 CDP 端口不等于整个调用链已隔离。
- **Alternatives considered**：只提高 `max_workers`（不安全）；保持单 worker 但在界面上显示两条线（不满足“谁先完成谁先用”）；全程全局大锁（仍串行）。候选实现可用显式任务身份向下传递或经过验证的任务局部绑定，最终以调用链和并发测试取舍。

## 决策 4：流程是一个，平台终态独立

- **Decision**：流程作为归属和新一轮门禁；每平台线独立保存抓取/AI 阶段、操作、错误和可用结果。流程整体是否还能新建下一轮由两条线是否仍运行/暂停导出，不用单一“完成”覆盖个体状态。
- **Rationale**：一方失败或暂停不得影响另一方；先完成方的结果仍需可整理。启动两个子任务时若第二个提交失败，应将第二平台记为失败，不抹掉已运行的第一平台，也不悄悄退化成单平台流程。
- **Alternatives considered**：一个合并任务状态或失败即中断全流程；与 B096 原话冲突。

## 决策 5：去重与单平台行为维持原契约

- **Decision**：B096 不改变既有跨平台去重开关、默认值和冲突取舍，也不改平台脚本。单平台仍有一键及单独抓取，只新增流程归属兼容。
- **Rationale**：B106 另行讨论。并发到达顺序可能暴露现有去重策略的时序性；将其作为已知限制记录和回归观察，不在 B096 制定新规则。
- **Alternatives considered**：顺便定义全新跨平台决胜规则；未经用户确认。

## 前置技术门禁

`exec_search_api.py` 与 `DiscoveryView.vue` 已超项目行数红线，不能用 B096 向内追加逻辑；需另立拆分 Spec 并完成兼容验证。浏览器身份、迁移调度和现场恢复所需接点超出原先文件清单；实施前须确定并确认最小扩边。本阶段只将门禁写入计划，不执行代码修改。
