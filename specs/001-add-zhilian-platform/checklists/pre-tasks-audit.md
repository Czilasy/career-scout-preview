# Tasks 前全量审查：智联招聘完整平台接入

**审查日期**：2026-08-03  
**审查对象**：`spec.md`、`plan.md`、`research.md`、`data-model.md`、`quickstart.md`、三个合同和需求质量清单  
**当前状态**：tasks 前全量审查通过；等待用户决定是否生成 `tasks.md`  
**准入规则**：全部阻断项关闭、独立审查无阻断、最终验证通过后，才允许生成 `tasks.md`

## 审查方法与边界

本审查发生在 `tasks.md` 生成前，按“产品目标 → 功能需求 → 数据模型/接口/平台合同 → 实施切片 → 可执行验收”逐向核对。它审查的是智联完整接入规格，不执行业务代码、不抓取真实岗位、不修改数据库，也不把外部页面临时观察写成永久事实。

本期平台化边界包含所有会创建平台访问、持有或改变任务状态、读写岗位/结果、恢复进度、关闭浏览器或展示来源的主链和外围入口。AI 提供商设置、简历文件管理和画像管理本身保持平台无关；旧 TaskRunner 与 `/api/search-runs` 不改造成新多平台主链。

## 审查发现与关闭记录

| ID | 严重度 | 初始问题 | 修复证据 | 状态 |
| --- | --- | --- | --- | --- |
| A-001 | 阻断 | “与 BOSS 相同”未覆盖进度、取消、提前结束、单 JD 和补抓等外围入口，可能在首次执行后串平台 | `spec.md` FR-034/035；`contracts/http-api.md` 状态、取消、结束、JD、补抓合同；`quickstart.md` 第 7 节 | 已关闭 |
| A-002 | 阻断 | 调优 list/detail/end-to-end 固定 BOSS source，rough/fine 来源产物无平台校验 | `spec.md` FR-038；`data-model.md` 调优实体；`contracts/job-source.md` 调优调用合同；`quickstart.md` 第 9 节 | 已关闭 |
| A-003 | 阻断 | 简历分析可返回 BOSS `stage/labels` 并覆盖智联 schema；异步 schema/城市请求可陈旧覆盖 | `spec.md` FR-032/033；`contracts/platform-schema.md` 建议投影和异步加载；`quickstart.md` 第 3 节 | 已关闭 |
| A-004 | 高 | 结果重置默认按全局最新，用户切换草稿平台后可能清错结果 | `spec.md` FR-036；`contracts/http-api.md` reset 合同；`quickstart.md` 第 7 节 | 已关闭 |
| A-005 | 阻断 | 旧 BOSS-only API 若接受智联可能静默创建 BOSS 任务或产物 | `spec.md` FR-039；`contracts/http-api.md` legacy 矩阵；`quickstart.md` 第 10 节 | 已关闭 |
| A-006 | 高 | 浏览器账号删除、取消和结束未建立双平台 profile/端口安全边界 | `spec.md` FR-034/037；`contracts/http-api.md` 浏览器与取消合同；`data-model.md` LoginSpace；`quickstart.md` 第 7 节 | 已关闭 |
| A-007 | 高 | 调优迁移若直接补写旧 manifest JSON 会破坏已签发摘要 | `plan.md` migration 27；`data-model.md` TuningTaskManifest/TuningStageArtifact；`quickstart.md` 第 2、9 节 | 已关闭 |
| A-008 | 高 | 智联选择器、公司性质编码和城市码属于可变外部事实，若当作固定事实会导致伪成功 | `research.md` 外部事实与有效期；`contracts/job-source.md` 真实页面基线；`plan.md` 切片 3/4；`quickstart.md` 前置条件 | 已关闭为实施门禁 |
| A-009 | 中 | 岗位平台 ID 与内部 UUID 同名，收藏/反馈和补抓存在身份混用风险 | `spec.md` FR-026；`data-model.md` 双 ID 与 migration 27；`contracts/http-api.md` 岗位对象/收藏合同；`quickstart.md` 第 5 节 | 已关闭 |
| A-010 | 中 | 平台禁用可能被误解为数据回滚或运行中任务切回 BOSS | `spec.md` FR-030/031；`plan.md` 回滚与禁用；`contracts/platform-schema.md` 启用语义；`quickstart.md` 第 8 节 | 已关闭 |

## 本轮独立审查驳回项的修订证据

上一轮独立只读审查提出 6 个会阻止 `tasks.md` 无歧义拆分的问题。本轮完成对应修订，并由独立 Terra medium reviewer 聚焦复核；结论为 PASS，以下问题全部关闭。

| ID | 原驳回问题 | 本轮修订落点 | 当前状态 |
| --- | --- | --- | --- |
| R-001 | 真实空结果只有内存 `JobSourceOutcome`，刷新/重启后无法审计 | `data-model.md` `ScreeningSourceAttempt`；`contracts/job-source.md` 编排持久化门槛；`contracts/http-api.md` 状态/结果响应；`quickstart.md` 第 2、4、6、7 节 | 已关闭 |
| R-002 | `/api/search-runs` 显式智联行为与“非本期”边界冲突 | `spec.md` FR-039；`research.md` 决策 17；`contracts/http-api.md` legacy 逐路由矩阵；`quickstart.md` 第 10 节 | 已关闭 |
| R-003 | legacy task 子路由没有冻结显式平台参数位置和拒绝范围 | `contracts/http-api.md` legacy 矩阵固定 POST body/GET query，并定义读取/事件/副作用边界；`quickstart.md` 第 10 节 | 已关闭 |
| R-004 | DB、API、结果快照和前端状态值没有唯一映射 | `data-model.md` 状态边界映射；`contracts/http-api.md` 公共状态映射；`quickstart.md` 第 6、7 节 | 已关闭 |
| R-005 | 调优 artifact 的五类轮次和两类可复用 source 阶段混用 | `spec.md` FR-038；`data-model.md` `source_artifact_kind`；`contracts/job-source.md`、`contracts/http-api.md` 调优合同；`quickstart.md` 第 9 节 | 已关闭 |
| R-006 | `canonical_url` 唯一性范围和 URL 变化冲突算法不明确 | `spec.md` FR-012；`research.md` 决策 20；`data-model.md` Job upsert 算法；`plan.md` migration；`quickstart.md` 第 2、5 节 | 已关闭 |

## 功能需求追踪矩阵

| 需求 | 设计/数据证据 | 接口/来源合同 | 实施切片 | 验收位置 |
| --- | --- | --- | --- | --- |
| FR-001 | `research.md` 决策 2/5 | `platform-schema.md` 注册表 | 1、7 | quickstart 3、12 |
| FR-002 | `data-model.md` ScreeningRun | `http-api.md` execute/AI/continue | 2、5 | 4、6、7 |
| FR-003 | `research.md` 决策 5 | `platform-schema.md` 请求投影/前端状态 | 7 | 3 |
| FR-004 | `data-model.md` PlatformFilterSchema | `platform-schema.md` AI schema | 1、3、7 | 3 |
| FR-005 | `research.md` 决策 3/4 | `platform-schema.md` 字段集合 | 1、7 | 3、11 |
| FR-006 | `research.md` 决策 3/4 | `platform-schema.md` 字段集合 | 3、7 | 3、4 |
| FR-007 | `data-model.md` 筛选快照 | `platform-schema.md` 投影规则 | 5、7 | 3、6 |
| FR-008 | `research.md` 决策 3/6 | `http-api.md` scope/execute；`job-source.md` fetch_list | 3、5 | 3、4 |
| FR-009 | `data-model.md` Job | `job-source.md` 列表岗位合同 | 4、6 | 4 |
| FR-010 | `data-model.md` 结果/待确认 | `job-source.md` detail/batch | 4、5 | 4、6 |
| FR-011 | `data-model.md` ScreeningResult | `http-api.md` latest result | 5、6 | 4、8 |
| FR-012 | `data-model.md` 持久化实体 | `http-api.md` 岗位/反馈接口 | 2、6 | 2、5 |
| FR-013 | `data-model.md` Migration 27 | `http-api.md` legacy 响应身份 | 2 | 2、11 |
| FR-014 | `data-model.md` LoginSpace | `platform-schema.md` 浏览器登录空间 | 3 | 4、7 |
| FR-015 | `research.md` 决策 10 | `job-source.md` preflight | 4、5 | 4、6 |
| FR-016 | `data-model.md` 状态转换 | `job-source.md` 错误矩阵 | 4、5 | 6 |
| FR-017 | `data-model.md` Checkpoint | `http-api.md` continue | 5 | 6 |
| FR-018 | `data-model.md` PipelineCheckpoint | `http-api.md` continue/identity conflict | 5 | 6、7 |
| FR-019 | `research.md` 决策 7/8 | `platform-schema.md` URL 规范化 | 1、4、6 | 4、5 |
| FR-020 | `data-model.md` platform 身份 | `http-api.md` 结果对象 | 6、7 | 4、8、12 |
| FR-021 | `plan.md` 前端结构 | `platform-schema.md` 前端状态 | 7、9 | 12 |
| FR-022 | `data-model.md` run/checkpoint | `http-api.md` 状态响应 | 2、5 | 6、7 |
| FR-023 | `research.md` 决策 10 | `job-source.md` 错误矩阵/空结果 | 4、5 | 4、6 |
| FR-024 | `spec.md` 范围外 | `job-source.md` 职责边界 | 全部 | 验收边界/安全检查 |
| FR-025 | `data-model.md` NormalizedCity | `platform-schema.md` 城市；`job-source.md` list 输入 | 1、3、5 | 3、4 |
| FR-026 | `data-model.md` Job/迁移 | `http-api.md` 岗位对象/收藏 | 2、6 | 5 |
| FR-027 | `data-model.md` Job/Result | `http-api.md` 结果对象 | 2、6 | 4、5 |
| FR-028 | `data-model.md` LoginSpace | `platform-schema.md` 登录空间 | 3、5 | 4、7 |
| FR-029 | `data-model.md` AI 快照 | `platform-schema.md` 完整冻结快照 | 5、7 | 3、6 |
| FR-030 | `research.md` 决策 11/18 | `platform-schema.md` 前端状态；`http-api.md` latest | 5、7 | 3、8 |
| FR-031 | `data-model.md` Migration 27 | `http-api.md` 迁移错误码 | 2、9 | 2、8 |
| FR-032 | `research.md` 决策 18 | `platform-schema.md` 建议投影；`http-api.md` analyze | 7 | 3 |
| FR-033 | `research.md` 决策 18 | `platform-schema.md` 异步加载 | 7 | 3 |
| FR-034 | `data-model.md` run 冻结配置 | `http-api.md` 状态/取消/finish | 5 | 7 |
| FR-035 | `data-model.md` run/岗位身份 | `http-api.md` job detail/recrawl/JD | 5 | 7 |
| FR-036 | `research.md` 决策 15 | `http-api.md` reset | 5 | 7 |
| FR-037 | `data-model.md` LoginSpace | `http-api.md` browser accounts | 3、5 | 7 |
| FR-038 | `data-model.md` 调优实体 | `job-source.md` 调优合同；`http-api.md` 调优接口 | 8 | 9 |
| FR-039 | `research.md` 决策 17 | `http-api.md` legacy 矩阵 | 1、8 | 10 |

## 成功标准追踪矩阵

| 成功标准 | 自动化/真实证据 | 主要验收位置 |
| --- | --- | --- |
| SC-001 | 智联一页真实主链，至少一个真实 JD 和 AI 结果 | quickstart 4 |
| SC-002 | schema allowlist、搜索/AI 分层、跨平台字段拒绝 | quickstart 3 |
| SC-003 | 登录、验证、限流、不可访问错误矩阵均为非成功 | quickstart 6 |
| SC-004 | 暂停恢复守恒、并发 claim、重启恢复 | quickstart 6 |
| SC-005 | migration 守恒与 BOSS 全量/主链回归 | quickstart 2、11 |
| SC-006 | HTTPS 与智联岗位 host/path allowlist | quickstart 4、5 |
| SC-007 | 1440×900 与 390×844 真实渲染 | quickstart 12 |
| SC-008 | 页面可见岗位字段抽查且缺失不编造 | quickstart 4 |
| SC-009 | 智联 run 的进度/JD/补抓/取消/结束与浏览器隔离 | quickstart 7 |
| SC-010 | 100 次交错响应与四类非终态任务恢复 | quickstart 3 |
| SC-011 | 调优五类 round、manifest/artifact/evidence 一致性 | quickstart 9 |
| SC-012 | legacy 智联请求零副作用拒绝与 BOSS 兼容 | quickstart 10 |

## 模块覆盖矩阵

| 模块/入口 | 平台权威来源 | 失败时禁止行为 | 规格证据 |
| --- | --- | --- | --- |
| 平台切换、schema、城市 | 新任务草稿 + 注册表 | 陈旧响应覆盖、跨平台字段提交 | FR-001~007、032~033 |
| 简历分析建议 | 请求草稿平台 + 服务端 schema | 模型发布 schema 或污染专属字段 | FR-032 |
| scope 预览/搜索创建 | 请求平台 + scope digest | AI 筛选下推、缺码回退 BOSS | FR-008、025 |
| 列表/JD/batch | run 冻结 runtime | 默认端口、伪空、伪 JD | FR-009~010、023 |
| AI 粗筛/精筛 | 父 run 平台 + schema 快照 | 客户端覆盖平台、丢弃旧值继续 | FR-002、007、029 |
| 进度/继续/取消/结束 | 目标 run | 关闭另一平台/未知 profile、伪终态 | FR-017~018、034 |
| 单 JD/单项/批量补抓 | source run + `platform_job_id` | 按 UI/URL/最近结果猜平台 | FR-035 |
| 最近结果/结果 reset | 结果 snapshot 或显式 run | 草稿重标、跨 run 删除 | FR-030、036 |
| 岗位/收藏/反馈 | `(platform, platform_job_id)` + 内部 UUID | 身份静默合并 | FR-012、026~027 |
| 浏览器账号 | 注册账号 + 显式 open 平台 + 全部锁 | 部分删除 profile、关闭未知进程 | FR-028、037 |
| 调优五阶段 | experiment/workload/manifest/artifact | 全局 source、跨平台产物复用 | FR-038 |
| legacy API | 既有 BOSS-only 合同 | 智联静默运行 BOSS | FR-039 |
| migration/禁用 | schema v26/v27 + 注册表启用状态 | 未备份迁移、已有智联数据回退覆盖 | FR-013、031 |
| 页面安全与 UI | 平台 URL 规则 + 真实视口 | 非官方链接、重叠/溢出 | FR-019~021 |

## API 平台权威来源矩阵

| API/动作 | 权威平台 | 客户端平台语义 |
| --- | --- | --- |
| `/api/platforms`、`/api/options`、`/api/filter-labels` | 平台注册表 | 显式选择；省略仅旧 BOSS 兼容 |
| `/api/search-scope/preview`、`/api/execute-search` | 请求 + scope digest 一致 | 新前端必填 |
| `/api/analyze-resume` | 请求平台 + 本地 schema | 新草稿建议，不影响任务/结果 |
| `/api/ai-screen` | 父搜索 run | 省略可继承；显式只校验 |
| task state/progress/continue/cancel/finish | 目标 run | 不选择平台 |
| job detail/单项/批量补抓 | source run + 岗位身份 | 只做一致性校验 |
| latest result | result snapshot | 可筛选，不能改写来源 |
| reset result | 显式 run；兼容时全局最近完成结果 | 可校验，不能选择扩大范围 |
| browser account activate/open/delete | activate 无平台；open 显式平台；delete 检查全部平台 | 不得影响已冻结任务 |
| `/api/check` | 请求平台 + 注册表登录空间 | 省略仅 BOSS 兼容 |
| tuning experiment/manifest/round | experiment 与已签发 manifest | 创建 experiment 必填，之后不可覆盖 |
| legacy BOSS-only | 既有任务/省略平台固定 BOSS | 显式智联零副作用拒绝 |

## 调优五阶段矩阵

| round | source 输入 | 是否创建 JobSource | 允许复用 | 必须校验 |
| --- | --- | --- | --- | --- |
| list | manifest 冻结 runtime | 是 | 无 | experiment/workload/manifest/platform/runtime/digest |
| detail | 同 workload 的 list artifact + manifest | 是 | list artifact | 平台、input version、workload、scope、artifact digest |
| rough | list artifact | 否 | list 字段 | 平台、schema、input version、artifact digest |
| fine | detail artifact | 否 | JD | 平台、schema、input version、artifact digest |
| end-to-end | manifest 冻结 runtime | 是 | 禁止中间复用 | 完整平台 runtime 与所有摘要 |

## Legacy 路由矩阵

| 类别 | 路由 | 智联合同 |
| --- | --- | --- |
| 旧创建/抓取 | `POST /api/tasks`、`POST /api/scrape` | `422 legacy_platform_not_supported`，零副作用 |
| 旧浏览器启动 | `POST /api/setup-chrome` | 明确拒绝；智联走账号 open |
| 旧任务子路由 | `/api/tasks/{id}` 及 cancel/retry/result/summary/export | 既有任务固定 BOSS；显式智联执行意图拒绝 |
| 旧结果 | `GET /api/results` | 只读 BOSS 命名产物并标识 BOSS |
| 废弃确认 | `POST /api/confirm-fields` | 显式智联拒绝 |
| 非本期 workbench | `/api/search-runs` 及子路由 | 只允许既有 BOSS，不作为智联主链 |

## 外部事实实施门禁

以下内容在 Spec 中刻意保留为“待当前真实页面核验”，不是未定义需求：

- 公司性质稳定值、标签及网页编码映射；
- `jl0` 以外的智联城市码；
- 列表、详情、明确空状态、登录墙、EdgeOne/验证码、限流和封禁 DOM；
- 允许的官方岗位详情 host/path 发生变更时的新 fixture。

实施切片 3/4 必须在用户当前登录态下重新核验并生成最小脱敏 fixture。任一关键事实无法确认时，`zhilian.enabled_for_new_tasks=false`，不得猜值、伪造空结果或把旧观察升级为永久合同。该门禁不改变已冻结的产品逻辑，只决定智联何时可以启用真实执行。

## 待完成准入项

- [x] 产品需求编号连续且无澄清占位符
- [x] 全部模块有平台权威来源、失败边界和验收位置
- [x] 外部可变事实有禁用式实施门禁
- [x] `tasks.md` 尚未生成
- [x] 独立只读 Terra medium reviewer 返回 PASS，6 个原驳回项全部关闭
- [x] 最终工件存在性、内链、FR/SC 编号、占位符和格式检查通过
- [ ] 仓库卫生测试留待提交前：当前因本轮 `specs/` 尚未提交及用户原有未跟踪构建文件按预期失败；未触碰、未删除、未提交用户改动

**审查结论**：规格内容、功能逻辑、数据与接口合同、状态与恢复边界、迁移方案、外部事实门禁和实施后验收已通过 tasks 前准入。仓库卫生是提交门禁，因用户明确要求暂不提交而延期，不阻塞后续在用户明确指令下生成 `tasks.md`。本轮停在 tasks 前，不生成任务文件。
