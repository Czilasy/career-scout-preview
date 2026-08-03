# 技术研究：接入智联招聘平台

**日期**：2026-08-03  
**状态**：tasks 前全量审查通过；本文件区分已核实的代码事实、已冻结的设计决策和实施时必须重新核验的外部页面事实。

## 已核实的现有实现事实

1. `webui/src/views/DiscoveryView.vue` 的 `filterValues` 当前只提交给 `/api/ai-screen`；搜索参数构造当前固定使用 `filters: {}`。
2. `webui/pipeline_exec.py` 当前创建 `plan_item` 时使用空 `source_filters`，BOSS 的薪资、经验、学历、行业、规模和融资阶段属于 AI 筛选层。
3. `/api/execute-search` 当前成功响应使用 HTTP `200`；智联接入不得在没有必要时改成 `202`。
4. `jobs.id` 是内部 UUID，收藏和反馈通过 `profile_jobs.job_id`、`feedback_events.job_id` 关联它；当前 pipeline 临时岗位的 `job_id` 是平台原始岗位 ID。
5. `screening_results.job_id`、`screening_pending_results.job_id` 和 `scrape_run_jobs.job_id` 当前按运行链保存平台原始岗位 ID；这些列名会在 migration 27 中消除歧义。
6. `RUN_TRANSITIONS` 不允许 `queued -> paused`；真实平台阻断必须先持久化 `running`，再转为 `paused`。
7. 当前城市 scope 保存规范城市名，BOSS adapter 有自己的城市映射；智联不能复用 BOSS 城市码。
8. `TaskStore.__init__()` 当前先执行数据库迁移；迁移前备份必须落在构造 `TaskStore` 之前的 bootstrap 层。
9. 当前 `/api/latest-pipeline-result` 查询全局最近的 `result_snapshot`；前端当前草稿平台不能改写这个历史结果的来源。
10. 当前浏览器账号记录只有一个 BOSS `profile_dir`，同一端口切账号时会受控替换已知 profile，未知 profile 占用时拒绝自动关闭。
11. `TuningRoundRunner.execute()` 的 `list`、`detail`、`end_to_end` 会调用真实 `JobSource`；当前 `source_factory(artifact_root=...)` 没有平台输入，固定返回 BOSS source。
12. 当前调优 workload、输入 artifact 和 manifest `fixed_fields` 未冻结平台、城市解析或登录空间；rough/fine 会复用 source artifact，但也未校验 artifact 平台。
13. `/api/search-progress`、`/api/task/cancel`、`/api/task/finish`、`/api/job-detail`、单 JD 补抓和批量补抓当前都可能触达任务状态、source 或浏览器资源，属于平台敏感外围入口。
14. `/api/reset-latest-result` 当前无请求目标，只删除全局最新结果；多平台后若仍由草稿切换触发，可能清理与当前页面意图不一致的平台结果。
15. `/api/analyze-resume` 当前把 BOSS labels 和 `stage` 返回给前端，`DiscoveryView.vue` 又直接用分析响应覆盖筛选标签；多平台后会污染智联 schema。
16. 前端当前 `/api/filter-labels` 加载没有请求序号或取消机制，快速切换平台时存在旧响应覆盖新平台 schema 的竞态。
17. 旧 `/api/tasks`、`/api/scrape`、`/api/setup-chrome` 与 `/api/results` 通过旧 TaskRunner 和 BOSS 文件命名运行；当前 Vue 岗位发现主链不使用它们。`/api/search-runs` 是另一条现有工作台执行链，不是本期 `screening_runs` 主链。

## 决策 1：公开身份使用 `platform`

**决定**：公开 API、SQLite run 根实体和前端类型统一使用 `platform`，允许值为 `boss`、`zhilian`。adapter 层保留 JobSource 命名。

**理由**：现有 `source` 已用于状态来源、来源 URL 和其它历史语义，继续重载会混淆。

**边界**：未知平台在 API、注册表、数据库写入和恢复校验处都拒绝，不得默认成 BOSS。

## 决策 2：统一主流程，差异下沉到平台注册表和 adapter

**决定**：复用现有列表/JD 编排、AI 粗筛/精筛、checkpoint、artifact/input-hash、结果管理和状态机。新增 JobSource Protocol 与平台注册表；BOSS、智联各自实现页面、登录/风控、字段映射、城市编码和链接规则。

**理由**：平台差异应局限在可替换边界，避免产生两套恢复和结果逻辑。

## 决策 3：列表搜索与 AI 筛选分层

**决定**：搜索任务只冻结关键词、规范城市名、页数、平台和平台城市解析结果；`/api/execute-search` 的 `script_params.filters` 和 `source_filters` 固定为空或不出现。AI 筛选任务从父搜索任务继承岗位，单独冻结当前平台 schema、选中值和标签。

**理由**：这是现有 BOSS 的真实语义，也避免多选 AI 值与 scraper 单值/不透明平台参数冲突。

**平台差异**：BOSS AI 额外使用 `stage`；智联 AI 额外使用 `company_nature`。智联公司性质不下推为智联列表 URL/API 参数。

## 决策 4：schema 保存稳定值、当时标签和版本

**决定**：平台注册表/版本化本地 fixture 发布 `field.key`、稳定 `option.value`、`option.label`、`multiple` 和 `schema_version`。平台网页临时编码只存在于 adapter 的映射层，不作为任务长期语义。

**理由**：旧任务恢复不能依赖当天在线页面；标签变化也不能让历史筛选失去可解释性。

**恢复规则**：恢复读取冻结快照。若当前 schema 无法解释某个旧值或字段，返回明确的 schema 不兼容阻断；不清空字段继续。

**实施门禁**：智联公司性质选项必须由当前真实登录页面核验并保存脱敏 fixture；未核验时智联新任务能力保持禁用，不猜编码。

## 决策 5：平台草稿隔离

**决定**：关键词、城市、页数及公共筛选在平台切换时保留；BOSS `stage` 和智联 `company_nature` 分别保存为平台草稿。创建任务只提交当前平台 schema 允许的字段。

**理由**：满足“逻辑与 BOSS 一样，只换平台”的连续使用体验，同时消除专属字段串用。

## 决策 6：城市使用规范名，adapter 解析平台码

**决定**：UI、scope、数据库和任务输入保存规范城市名；每个 adapter 只读取自己的版本化城市映射，将规范名解析成平台码。智联全国固定为 `jl0`，其它城市码必须由真实页面/官方页面数据核验并脱敏保存。

**阻断**：当前平台缺少城市映射时，预览或创建任务返回城市映射错误；禁止把 BOSS 码发送给智联。任务中保存规范名、平台码、映射版本和映射标签，以便恢复不依赖在线元数据。

## 决策 7：岗位身份区分平台 ID 与内部 UUID

**决定**：`platform_job_id` 表示平台原始稳定 ID；`job_id` 表示 `jobs.id` 内部 UUID。列表、详情、source artifact 和临时结果只使用 `platform_job_id`；收藏、反馈、profile 关系和已落库接口只使用内部 `job_id`。

**理由**：当前同名字段在不同层表达不同身份，已经导致最近结果按 URL 做额外对齐；新合同必须消除这种隐式转换。

**落库边界**：未落库岗位返回 `platform_job_id` 且 `job_id: null`。收藏/反馈动作若收到临时岗位，后端先按双索引规则落库并返回内部 UUID；两个身份索引分别命中不同记录时返回 `job_identity_conflict`，禁止静默合并。

## 决策 8：岗位字段进入 jobs 和结果快照

**决定**：`jobs` 与 `screening_results` 都保存 `platform`、`platform_job_id`、标题、公司、薪资、地点、经验、学历、JD、规范链接和 `extra_json`。`extra_json` 只保存经过归一化的非敏感平台字段，例如公司性质标签；不保存 Cookie、页面私密令牌或无界原始 HTML。

**理由**：只写 `jobs` 仍可能让结果刷新时丢失临时岗位字段；只写结果快照又会让收藏和反馈缺少持久主体。两层都写入同一次结果落库合同。

## 决策 9：浏览器登录空间为“平台 + 账号”

**决定**：现有账号 `profile_dir` 继续代表 BOSS；智联 profile 固定确定性派生为该 BOSS profile 路径加 `.zhilian` 后缀。逻辑 `profile_key` 为 `<platform>:<browser_account>`，仅用于恢复错配检测，不返回绝对路径。

**端口**：BOSS 默认 `9222`，智联默认 `9223`；同一平台的多个账号按现有受控切换规则共享平台端口并替换已知 profile。两个平台的 profile 目录必须不同；未知 profile 占用端口时拒绝关闭或切换。

**冻结**：`platform`、`browser_account`、`cdp_port`、`profile_key` 和解析结果在任务创建时冻结，所有 list/detail/batch subprocess 显式接收端口；恢复不得读取当前 UI 或全局活动账号。

## 决策 10：错误矩阵固定到状态机

| 情况 | 错误码 | 状态/范围 |
| --- | --- | --- |
| 未登录、登录墙 | `source_login_required` | 平台级 `paused` |
| EdgeOne、验证码、人机验证 | `source_verification_required` | 平台级 `paused` |
| 明确限流 | `source_rate_limited` | 平台级 `paused` |
| 平台封禁/拒绝访问 | `source_blocked` | 平台级 `paused` |
| CDP 不可用 | `source_cdp_unavailable` | 平台级 `paused` |
| preflight 连接失败/超时 | `source_unreachable` / `source_timeout` | 有限重试后 `failed`；若同时确认是可人工解除的 CDP/平台阻断，按对应 `paused` 处理 |
| 全局关键结构不兼容 | `source_invalid_output` | `failed` |
| 单岗位详情不存在、详情超时或可归属解析失败 | `source_not_found` / `source_timeout` / `source_invalid_output` | 单项失败/待确认，继续其它岗位 |
| 批量详情连续出现平台级失败并触发熔断 | 对应 source 码 | `paused`，保留已完成项 |
| 新任务入口被配置禁用 | `platform_disabled` | 创建前 `503`，历史读取不受影响 |

空结果只有在已确认登录有效、搜索页面结构有效且页面存在明确空状态证据时才是成功空结果。关键结构缺失不能被解释为空。

## 决策 11：最新结果按真实来源查询

**决定**：`/api/latest-pipeline-result` 默认返回最近的一个单平台结果，保持现有全局最近语义；增加 `platform` 过滤和 `run_id` 精确查询。接口返回结果自身的 `platform`，不能由当前草稿平台改写。

**收藏/反馈**：结果卡片先使用内部 `job_id`；未落库临时岗位由后端先落库。禁止用裸平台 ID 直接查询 `profile_jobs` 或 `feedback_events`。

## 决策 12：迁移前备份与禁用回滚

**决定**：在 `TaskStore` 构造前的 bootstrap 层执行 SQLite 一致性备份、SHA-256、manifest、只读可读性和 `quick_check` 验证。备份失败或迁移失败阻断应用启动；不在迁移过程中先改库再补备份。

**回滚**：尚无智联数据时，人工可从经过验证的旧备份回退；已有智联数据后不覆盖数据库，只将 `zhilian.enabled_for_new_tasks` 设为 false，保留 v27 数据并继续读历史。

## 决策 13：平台注册项支持启用状态

**决定**：平台注册项包含 `enabled_for_new_tasks` 和 `availability_reason`。智联公司性质/城市 fixture 或页面合同未验证时为禁用；禁用只阻止新搜索、AI 筛选和补抓创建，不影响历史任务读取、结果展示、收藏和反馈。

**运行中任务**：已有任务仍显示原平台；需要重新调用已禁用 adapter 时返回 `platform_disabled` 并保留数据，不静默切换 BOSS。

## 决策 14：平台敏感外围入口从实体继承平台

**决定**：进度、取消、提前结束、单 JD、单岗位补抓、批量补抓和继续操作都先读取目标 run 与岗位身份，再使用冻结的 `platform/browser_account/cdp_port/profile_key`。客户端当前平台只用于一致性校验。

**浏览器边界**：取消和提前结束只能关闭目标 run 的已知平台端口/profile；未知 profile 占用时不关闭。浏览器账号删除同时检查 BOSS 和智联派生 profile、两个平台端口及运行/暂停锁。

## 决策 15：结果重置以 run 为目标

**决定**：`/api/reset-latest-result` 接受首选 `run_id`，仅删除该已完成结果快照及级联临时行。无 `run_id` 只作为旧客户端兼容，删除全局最近已完成结果，并返回被删除的 `run_id/platform`。不按草稿平台选择目标，也不删除岗位主体、收藏、反馈、运行中任务或来源 search run 的 source attempt 审计记录。

## 决策 16：调优实验使用与普通任务相同的平台合同

**决定**：调优 `source_scope`、workload、输入 artifact、manifest `frozen_input/fixed_fields` 和 program evidence 显式保存平台、解析城市快照、filter schema 版本、浏览器账号、CDP 端口、profile key 与输入摘要。现有 `stage` 保留 `list/detail/rough/fine/end_to_end` 五类轮次值；仅 list/detail 轮次产生可复用 source artifact，rough/fine 分别继承 list/detail 并校验平台，AI-only 轮次不调用 source。

**存量合同**：migration 27 在调优 experiment/manifest 记录的外层数据库列回填 `boss`，但不修改旧 manifest JSON 或摘要。旧 manifest 只可作为 BOSS 记录执行；缺失或冲突时阻断，不把它升级为智联。

## 决策 17：legacy 执行入口显式 BOSS-only

**决定**：旧 `/api/tasks`、`/api/scrape`、`/api/setup-chrome`、`/api/tasks/{id}/...`、`/api/results` 以及 `/api/search-runs` 的 create/detail/jobs/cancel 子路由全部保留 BOSS-only。legacy POST 从 JSON body、GET 从 query 读取 `platform`；省略或显式 `boss` 延续 BOSS，显式 `zhilian` 在查找任务或产生副作用前返回 `422 legacy_platform_not_supported`，未知平台返回 `400 platform_validation_failed`。拒绝响应不读取智联 profile，不创建任务/查询/事件，不改变状态、不启动浏览器、不写结果或 artifact。逐路由参数位置和零副作用范围见 [http-api.md](contracts/http-api.md)。

**理由**：把第二套旧编排同时平台化会扩大本期范围并产生两套恢复合同；明确拒绝比静默 BOSS 或含糊兼容更可靠。

## 决策 18：简历建议和异步响应服从平台注册表

**决定**：简历分析返回平台键、schema 版本和按该 schema 投影的建议值，但 schema 字段、标签和稳定值仍以平台注册表为权威。前端使用请求序号或取消机制丢弃陈旧平台 schema/城市响应；恢复任务先恢复任务平台，再加载并投影快照。

**隔离**：当前新任务草稿平台、非终态任务平台和最近结果平台是三个不同状态，不得互相改写。

## 决策 19：列表结果必须先落审计记录

**决定**：`JobSourceOutcome` 仍为 adapter 的安全内存返回值；编排层在推进组合完成、run 状态、进度或 result snapshot 前，必须追加持久化 `screening_source_attempts`。记录按 `run_id/combo_key/attempt_no` 保存 input hash、non-empty/empty/failed/paused 分类、岗位数量和脱敏空状态证据。历史、刷新和重启只信任该记录，不从零岗位或临时 artifact 反推真实空结果；结果 reset 不删除来源审计记录。

**理由**：真实空结果是业务事实，若只存在 adapter 返回值，重启后无法区分真实空状态、选择器失效和网络空响应。

## 决策 20：岗位规范 URL 的唯一性范围

**决定**：保留 `jobs.canonical_url` 的全局唯一约束，不改为平台内唯一。平台注册表的 host/path allowlist 先决定 URL 的唯一平台归属；不匹配的平台返回 `platform_url_mismatch`，同一规范 URL 被其它平台记录占用时返回 `job_identity_conflict`，两行和关联均不改动。

**理由**：不同平台允许的官方岗位 host/path 不应产生同一规范 URL；全局唯一约束能继续保护存量 BOSS 关联，并让 URL 变化的 upsert 只有一个确定冲突边界。

## 决策 21：公共状态值只在边界翻译

**决定**：数据库只保存 canonical `queued/running/paused/succeeded/partial/failed/interrupted`，取消原因通过 `interruption_kind` 区分。公共 API 固定映射 `succeeded→completed`、`partial→completed_with_pending`、`interrupted+user_cancelled→cancelled`，服务重启或 operator stop 的 interrupted 保持可恢复；`done` 仅限旧结果兼容值。

**理由**：状态机、接口和前端若各自使用 completed/succeeded 或 cancelled/interrupted，会使恢复和终态验收出现多种解释。

## 真实页面侦察结果与有效期

以下是此前真实页面侦察形成的 fixture 起点，不是永久合同：

- 搜索 URL 曾观察为 `https://www.zhaopin.com/sou/jl{city}/kw{encoded}/p{page}`，全国为 `jl0`；关键词编码必须由页面行为产生，不自行复刻不透明算法。
- 曾观察到列表卡片 `div.joblist-box__item`、标题 `a.jobinfo__name`、薪资 `p.jobinfo__salary`、地点/经验/学历 `div.jobinfo__other-info-item`、公司 `a.companyinfo__name`、分页 `a.soupager__btn`。
- 详情路径曾包含 `jobdetail/<id>.htm`，JD 容器曾观察为 `div.describtion-card__detail-content`。
- 筛选元数据入口曾观察到 `https://fe-api.zhaopin.com/c/i/search/base/data`；公司性质编码必须在实施时读取、脱敏并冻结 fixture。
- 未登录时列表不可用并提示登录；无头访问曾触发腾讯 EdgeOne。

若真实页面不再满足这些起点，更新 fixture 和 adapter 合同；不得用空列表、默认值或猜测编码推进。
