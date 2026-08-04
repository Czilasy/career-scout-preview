# 技术计划：岗位反馈闭环与投递过期提醒

**分支**：`main`（本轮不创建功能分支） | **日期**：2026-08-04 | **规格**：[spec.md](spec.md)

**输入**：为已入库的 BOSS 与智联岗位增加平台无关的生命周期快照、客观事件轨迹、30 天无进展提醒和按需 AI 行动建议。

## 摘要

本功能以 `profile_jobs` 作为当前画像与岗位的唯一生命周期快照，新增 `read`、`stale` 和 `last_follow_up_at`；migration 28 新建追加式 `profile_job_events` 记录真实状态/时间变化，并用 `profile_job_command_receipts` 隔离幂等请求回执，三者在同一事务中保持一致。提醒不单独建表，而是按当前画像、`status='applied'` 和 `COALESCE(last_follow_up_at, applied_at)` 动态投影，精确经过 720 小时时进入提醒。

生命周期命令通过统一后端服务校验权威岗位身份、时间和动作语义。pipeline 岗位首次被操作时，必须按 `platform + platform_job_id + canonical_url` 调用现有双索引 upsert，返回内部 `jobs.id` 后再关联画像；不从当前页面平台、标题、公司或裸平台岗位 ID 猜测身份。提醒查询不包含任何平台谓词。

AI 建议是独立、只读、按单岗位调用的适配器，只接收 JD、投递时间、最后跟进时间和经过天数，输出受限为 `follow_up` 或 `review`。缺少 JD、AI 未配置、调用失败或返回无效时使用规则兜底，不持久化建议，也不改变提醒或状态。

交付按用户指定的多 AI、多会话并行方式组织：先冻结本计划中的数据与接口合同，再按互斥写入范围并行实现存储、AI、岗位身份解析和前端客户端；第二批并行实现 API 与两个前端组件；最后由单一集成所有者修改 `app.py`、`App.vue` 和 `DiscoveryView.vue` 等共享文件，并执行跨模块回归与真实视口验收。

## 技术背景

**语言/版本**：Python 3.10+；TypeScript 5.9；Node.js 20+

**主要依赖**：Flask 3、SQLite、requests；Vue 3、Vite、Lucide Vue

**存储**：本地 SQLite；migration 28 为 additive migration，复用现有迁移前一致性备份机制

**测试**：Python `unittest`；Vitest + Vue Test Utils；`vue-tsc` + Vite build；Playwright 桌面/窄屏真实渲染

**目标平台**：本地单用户 Web 工作台；Windows + Chrome 为真实验收环境

**项目类型**：Flask 后端、Vue 前端与本地 SQLite 组成的桌面式 Web 应用

**性能目标**：当前画像提醒查询在 10,000 条画像岗位数据下保持交互级响应；列表最多返回 100 条但总数准确；页面切换不启动批量 AI 调用

**硬约束**：提醒阈值固定 720 小时；所有新写入时间含明确时区并归一为 UTC；不猜测缺失投递时间；不自动标记已读或已荒废；AI 无状态推进权；不增加平台过滤；状态快照与事件必须原子提交

**范围**：当前画像的单岗位生命周期、事件、提醒、建议和现有结果工作台集成；不访问招聘平台核实下架，不做自动投递、自动联系、延后提醒、批量 AI 或跨平台相似岗位合并

## 规则门禁

*项目没有 constitution 文件。以下门禁来自全局/项目 `AGENTS.md`、冻结 Spec 和本地 roadmap；Phase 0 前已检查，Phase 1 设计后再次复核。*

| 门禁 | 状态 | 证据/处理 |
| --- | --- | --- |
| 设计前读取本地 roadmap | 通过 | 已读 `roadmap/DIRECTIONS.md` 与 `roadmap/REFERENCE_GET_JOBS.md`；本功能不复制参考项目源码 |
| 需求仍处于 Spec 流程，未经授权不实现 | 通过 | 本轮只生成 Plan 工件；不创建 `tasks.md`，不修改业务代码 |
| 平台无关且不增加平台筛选 | 通过 | reminder projection、生命周期命令和 AI 输入合同均无平台分支；平台只用于岗位身份/链接校验 |
| 数据迁移前可回溯 | 设计通过，待实现验证 | migration target 从 27 提升到 28；v27 数据库必须先完成 SQLite backup、SHA-256、manifest、只读 `quick_check` |
| 快照与事件原子一致 | 设计通过，待实现验证 | 单个 store transaction 完成岗位解析、画像关联、快照条件更新和事件追加 |
| 幂等重试不重复跟进或事件 | 设计通过，待实现验证 | 每条命令要求 `request_id`；独立 command receipt 保存请求指纹并关联可空事件，重复键同载荷复用、异载荷冲突 |
| 时间边界客观可测 | 设计通过，待实现验证 | 后端只接受含时区 RFC 3339，内部 UTC；资格用 `now - baseline >= 720h`，测试覆盖阈值前/等于/阈值后 |
| AI 失败不影响主链 | 设计通过，待实现验证 | 建议路由只读；规则兜底始终返回受限 action；不保存建议 |
| 岗位身份不靠 UI 猜测 | 设计通过，待实现验证 | 内部 ID 或权威三元组必须一致；legacy 不完整身份返回 `job_identity_incomplete` |
| 现有偏好反馈语义不被污染 | 设计通过，待实现验证 | `profile_job_events` 与 `feedback_events` 分表；生命周期动作不写偏好反馈 |
| 清理不删除明确用户状态/事件 | 设计通过，待实现验证 | 自动清理仍只匹配 `status='new'`；事件与非 new 快照均不在清理集合 |
| 多会话并行不产生写冲突 | 设计通过 | 下方固定依赖波次、共享合同和互斥文件所有权；共享入口只在集成波次修改 |
| 前端真实可用性验收 | 待实施验证 | 固定 1440×900 与 390×844，覆盖加载、空、成功、失败和操作反馈 |

## 项目结构

### 本功能工件

```text
specs/002-job-feedback-reminders/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md
│   └── ui-interaction.md
└── checklists/
    └── requirements.md
```

`tasks.md` 属于下一阶段，本轮不创建。

### 预计源码落点

```text
webui/
├── store.py                       # migration 28、生命周期事务、提醒查询、事件读取
├── job_feedback.py                # 命令语义、时间校验和提醒投影的领域服务
├── job_feedback_api.py            # 独立 Flask Blueprint/route registrar
├── job_advice.py                  # AI 提示、输出验证和规则兜底
├── pipeline_job_identity.py       # pipeline 权威三元组解析与统一双索引 upsert
├── app.py                         # 仅集成波次注册路由并接入 pipeline helper
└── src/
    ├── jobFeedback.ts             # 前端类型、API client、状态标签
    ├── App.vue                    # 顶部提醒入口、徽标、抽屉所有权
    ├── views/DiscoveryView.vue    # 详情动作接线和变更通知
    └── components/
        ├── ReminderDrawer.vue
        ├── JobLifecycleActions.vue
        └── JobWorkspace.vue       # 复用现有 actions slot；预计无需结构改造

tests/
├── test_webui_store.py
├── test_job_feedback.py
├── test_job_advice.py
├── test_pipeline_job_identity.py
├── test_job_feedback_api.py
├── test_webui_app.py
└── test_repo_hygiene.py

webui/src/**/__tests__/
├── jobFeedback.spec.ts
├── ReminderDrawer.spec.ts
├── JobLifecycleActions.spec.ts
├── App.spec.ts
└── DiscoveryView.spec.ts
```

**结构决定**：业务规则不继续堆入已经较大的 `app.py`。新增小型领域模块和路由注册模块，使存储、AI、API 与 UI 能按互斥文件并行实现；`app.py` 只在最终集成时完成装配。前端复用 `JobWorkspace` 已有 `actions` slot，生命周期控件独立成组件；提醒抽屉独立管理列表、详情和快捷动作。

## 数据与事务设计

1. migration 28 将迁移备份目标版本从 27 提升到 28，为 `profile_jobs` 增加 `last_follow_up_at TEXT`，创建 `profile_job_events`、`profile_job_command_receipts` 和查询索引；不猜填缺失 `applied_at`，不改写现有反馈事件。
2. `profile_job_events` 只保存真实变化前后的客观轨迹；`profile_job_command_receipts` 保存每个 `request_id` 的请求指纹、是否变化及可空 event 引用。同一请求重放读取原回执，不追加第二行，同时返回响应时的权威当前快照，避免旧重试覆盖后续操作。
3. 生命周期写入先在同一事务内解析内部岗位。已给内部 `job_id` 时校验其权威三元组；未给时必须提供 `platform + platform_job_id + canonical_url`。存储实现须抽出接收现有 `conn` 的双索引 helper，让公开 `upsert_job` 和 lifecycle transaction 共享算法而不嵌套第二个连接/事务。任何冲突均在创建画像关联或事件前终止。
4. action transaction 使用 SQLite 写锁串行化同一画像岗位的并发命令。快照、事件或岗位身份任一写入失败时整笔回滚；AI 不参与该事务。
5. 提醒是动态查询结果，不建 reminders 表。只读取当前 profile 的 `applied` 行和合法 `applied_at`，以最后跟进（字段非空时必须合法）或投递时刻为 baseline，在 Python timezone-aware datetime 上做精确 timedelta 运算，先计算全部总数再截取前 100 条；损坏的非空跟进时间不回退到投递时间制造误提醒。
6. 新写入统一存储 UTC RFC 3339。旧值只有在可解析且含/可安全解释时区时才参与提醒；缺失或无效值保持原样并排除，不自动修复成猜测日期。

完整字段、命令效果和派生规则见 [data-model.md](data-model.md)。

## HTTP 与 UI 设计

- `GET /api/profile-jobs/state` 只读解析当前画像岗位状态；查看详情不产生“已读”或其它写入。
- `POST /api/profile-jobs/actions` 接受权威岗位身份、`request_id` 和受限 action，在一个事务中解析/入库岗位、关联画像、更新快照、保存命令回执，并仅在实际变化时追加事件。
- `GET /api/profile-jobs/{profile_id}/{job_id}/events` 返回按序客观轨迹。
- `GET /api/job-reminders/count?profile_id=...` 只返回当前画像全量逾期总数；`GET /api/job-reminders?profile_id=...` 返回同一总数和最多 100 条排序后的提醒。
- `POST /api/profile-jobs/{profile_id}/{job_id}/advice` 是只读、按需、单岗位调用；非逾期岗位拒绝建议，所有 AI 故障返回规则兜底成功体。
- legacy `PATCH /api/profile-jobs/{profile_id}/{job_id}` 保留兼容，但状态/投递时间写入必须转入同一命令服务；不得继续绕过事件、时间校验和事务。

顶部导航由 `App.vue` 持有提醒徽标和抽屉。profile 初始化或切换时请求轻量 count，打开抽屉再加载列表；查看抽屉不清除提醒。`ReminderDrawer.vue` 负责加载/空/失败/列表/详情状态，以及跟进、荒废和 AI 建议；`JobLifecycleActions.vue` 通过现有详情 slot 提供已读、已投递、荒废、恢复、纠正和历史。成功写入后以服务端当前快照为准，并通知 App 刷新提醒；失败时保留原 UI 状态。

具体请求体、错误码和交互状态见 [http-api.md](contracts/http-api.md) 与 [ui-interaction.md](contracts/ui-interaction.md)。

## 并行交付拓扑

### 共享合同冻结门禁

进入 Tasks 前以本目录 `spec.md`、`data-model.md`、`contracts/http-api.md` 和 `contracts/ui-interaction.md` 为共享合同。后续任何会话不得自行改 action 枚举、字段名、错误码、时间规则或提醒资格；发现合同冲突时回到主会话统一修订，再重新派发受影响任务。

### Wave 1：基础实现，可四路并行

| 会话 | 允许写入 | 输出与门禁 |
| --- | --- | --- |
| 存储与领域内核 | `webui/store.py`、`webui/job_feedback.py`、`tests/test_webui_store.py`、`tests/test_job_feedback.py` | migration 28、备份、命令回执/事件事务、提醒边界测试通过 |
| AI 建议 | `webui/job_advice.py`、`tests/test_job_advice.py` | 输入最小化、输出 allowlist、六类兜底测试通过；不得改状态 store |
| pipeline 岗位身份 | `webui/pipeline_job_identity.py`、`tests/test_pipeline_job_identity.py` | BOSS/智联三元组、URL 校验、双索引冲突测试通过；不得改 `app.py` |
| 前端客户端合同 | `webui/src/jobFeedback.ts`、对应单测 | 类型、API 封装、request ID 生命周期和状态标签测试通过 |

Wave 1 各会话可以读取其它文件，但不得越过写入范围。存储会话是 schema 和事务实现的唯一所有者；其它会话只依赖冻结合同，不复制状态规则。

### Wave 2：模块实现，可三路并行

| 会话 | 前置依赖 | 允许写入 | 输出与门禁 |
| --- | --- | --- | --- |
| 后端 HTTP | 存储、AI、身份模块通过 | `webui/job_feedback_api.py`、`tests/test_job_feedback_api.py` | 正常/冲突/回滚/当前画像/无平台过滤合同测试通过 |
| 提醒抽屉 | 前端客户端通过 | `webui/src/components/ReminderDrawer.vue`、组件单测 | 计数、列表、详情、快捷动作、建议和错误状态通过 |
| 详情生命周期控件 | 前端客户端通过 | `webui/src/components/JobLifecycleActions.vue`、组件单测 | 状态动作、时间纠正、恢复、历史和失败保持原状态通过 |

两个前端组件使用 scoped CSS，不修改共享 `styles.css`；两个会话都只能读取 `App.vue`、`DiscoveryView.vue` 和 `JobWorkspace.vue`。

### Wave 3：共享入口集成，可前后端两路并行

| 会话 | 允许写入 | 集成内容 |
| --- | --- | --- |
| 后端集成所有者 | `webui/app.py`、`tests/test_webui_app.py` | 注册新路由；让 pipeline interest/reject/lifecycle 共用权威岗位解析；保护 legacy PATCH；执行 API 集成回归 |
| 前端集成所有者 | `webui/src/App.vue`、`webui/src/views/DiscoveryView.vue`、必要时 `webui/src/types.ts`/`styles.css` 及对应既有测试 | 接顶部入口、profile 刷新、详情 slot、跨组件刷新；合并并保留工作区已有改动 |

当前工作区的 `webui/store.py`、`webui/app.py`、`webui/src/types.ts`、`webui/src/views/DiscoveryView.vue` 及部分前端测试已有其它会话或用户改动。对应文件所有者在实际派发时必须先读当前版本后合并，任何其它并行会话不得修改、还原或覆盖。

后端集成还必须移除读取投影中的旧覆盖行为：`profile_jobs.status` 是唯一当前生命周期状态，历史 `feedback_events` 只用于偏好学习，不得再通过 `aggregate_feedback_state` 把已投递/已读/已荒废岗位展示回“感兴趣”。点击现有收藏/不感兴趣按钮仍可显式把当前状态改为 `interested/deleted`，但这是一条新的用户操作，不是历史反馈对快照的隐式覆盖。

### Wave 4：串行集成门禁

1. 主会话抽查每路差异和聚焦测试证据，确认没有越界写入或平台条件。
2. 运行最终 Python 全量、前端全量、类型检查/构建和仓库卫生测试。
3. 启动受影响服务，验证刷新、重启和当前画像切换后的持久化与提醒重算。
4. 在 1440×900 和 390×844 完成真实渲染及主链操作；检查横向溢出、重叠、不可达操作和失败反馈。
5. 对严格档任务执行一次完整独立审查；修复阻断项后只做聚焦复查，最终验证通过即停止。

## 验证策略

### 存储与领域测试

- v27 到 v28 备份、迁移、幂等、回滚、外键和事件顺序。
- 所有 action 的前后状态/时间矩阵；同 request ID 同载荷重放与异载荷冲突。
- 并发状态重试只产生一个逻辑变化；每次新的 follow-up request 产生新时间和事件。
- 投递时间无时区、非法、未来、缺失和合法边界。
- 29天23:59:59、恰好720小时、720小时+1秒；跨时区、闰日和 DST 输入。
- BOSS/智联混合样本同时提醒；相同标题/公司和相同裸平台 ID 不串状态。
- 101+ 提醒时总数准确、列表 100、排序稳定；切换 profile 完全隔离。

### API 与 AI 测试

- 权威三元组首次入库、内部 ID 一致性、URL 错配、双索引冲突和不完整身份零副作用。
- 当前 `profile_jobs.status` 优先于历史反馈聚合；已投递岗位存在旧 interested 事件时仍显示 `applied`。
- 快照与事件中途异常整笔回滚；legacy PATCH 不再绕过验证。
- 建议正常、缺 JD、未配置、超时、网络失败、无效 JSON、非法 action；响应始终受限且状态零变化。
- 只查看提醒、详情、事件和建议均不清除提醒；跟进、荒废和合法状态/时间纠正成功后才重算。

### 前端与真实验收

- App profile 初始化/切换、陈旧请求不覆盖新 profile、徽标显示全量 count。
- 抽屉加载、空、失败、100 条列表、详情、无安全 URL 时禁用跳转。
- 详情动作 busy 防重复，网络不确定时复用 request ID，失败不做虚假乐观提交。
- 桌面和窄屏完成徽标、抽屉、岗位详情、跟进、荒废、恢复、纠正和建议。

完整命令和验收数据见 [quickstart.md](quickstart.md)。

## 回滚与禁用策略

1. v28 迁移前，现有 bootstrap 对所有 schema `< 28` 的非空数据库生成一致性备份；备份或验证失败时 `TaskStore` 构造中止，源库不发生 v28 写入。
2. migration 28 在单事务内执行；任何 DDL/DML 守恒检查失败时回滚并阻止应用启动。
3. 尚未产生 v28 生命周期数据时，可在停止服务后人工恢复已验证 v27 备份；系统不自动覆盖数据库。
4. 已产生 `read`、`stale`、跟进或事件数据后，不得恢复旧备份覆盖新事实。故障时优先禁用新入口或前滚修复，保留 additive v28 schema 和数据。
5. AI 故障不需要数据回滚；服务自动使用规则建议。提醒是查询投影，不存在提醒表回滚或清理。

## 复杂度说明

| 增加项 | 必要原因 | 更简单方案未采用的原因 |
| --- | --- | --- |
| 追加式 `profile_job_events` | 状态可纠正，当前快照需要客观变化轨迹解释 | 只保存最终快照无法解释历史 |
| command receipt 与 `request_id + fingerprint` | follow-up 每次明确确认都更新时间，但同一次网络重试不能重复更新；无变化请求也需可靠重放 | 把 no-op 回执塞进事件表会污染客观轨迹，仅靠前端 busy 又保护不了超时/并发 |
| 独立 `job_feedback.py` | 命令、时间与提醒规则需要被 store、API 和测试共享 | 全写进 `app.py` 会让并行开发和事务边界难以审查 |
| 独立 `job_advice.py` | AI 输入/输出和兜底需与状态写入物理隔离 | 在路由里直接调用 AI 容易让失败路径误触状态或泄露原始响应 |
| 独立 pipeline 身份模块 | 生命周期和现有兴趣反馈必须共享同一权威落库规则 | 保留 BOSS 专用 `save_job` 会继续丢失平台岗位身份 |
| 多波次并行拓扑 | 用户明确要求多个 AI 会话并行，且仓库存在多个大共享文件 | 无依赖地全并行会在 `app.py`、`DiscoveryView.vue` 等文件产生冲突和合同漂移 |

## 设计后复核状态

Phase 1 设计已覆盖 37 条功能需求、10 条成功标准、权威岗位身份前置条件、迁移回滚和多会话并行边界。当前没有待澄清标记或未决技术选择。Plan 完成后应停在阶段门禁，是否生成并行 `tasks.md` 由用户下一次明确授权。
