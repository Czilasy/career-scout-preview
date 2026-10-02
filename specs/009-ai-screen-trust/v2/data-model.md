# Data Model: B094

这是已有数据的使用契约与内存派生信息说明；不新增数据库表、列、迁移、用户输入字段或产品结果状态。

## 领域选择

来源为既有 screening_fields.industry 和 Track.confirmed_filters_snapshot。V2 已有 snapshotVersion、mappingVersion、unifiedValues、platformValues、overrides、exclusiveValues。

- 原始组：V2 没有当前平台 industry 覆盖，且实际条件与冻结 platformValues 一致时，取 unifiedValues.industry 的完整组选项标签。
- 平台覆盖：overrides 中当前平台含 industry 键时，以实际平台 industry 标签为准；空数组表示未选择，不与原始组求并集。
- 当前选择变化：实际筛选条件与快照该平台值不同，按当前明确条件解释，不能借旧快照复活已移除的选择。
- 旧格式：仅有平台条件时，使用平台注册 schema 标签，不逆向猜测曾选择哪个统一组。
- 验证：输入类型、组选项和平台值须符合已有 schema；声明 V2 却结构损坏进入既有异常链，不悄悄降级为不限。

## 岗位与业务信息

本轮只消费已有列表事实和 JD，不依赖当前链路未传递的额外详情字段。

| 信息 | 已核实来源 | 到精筛的路径 | 用途 |
| --- | --- | --- | --- |
| 职位/公司名称 | 原始抓取岗位 title、company；智联列表 search.py 返回 | source_result.jobs → survivors 副本 → enriched → 适配器 → 模型输入 | 背景，不能证明主营 |
| 公司分类 | 列表 company_industry 或 extra.industry；智联列表由 industryName 归一化 | 原始岗位字典保留 → 适配器别名处理 → 模型输入 | 背景，不能单独判相关或不相关 |
| 工作/产品/主营描述 | 现有详情 jd 原文，或旧运行 JD 检查点及 screening_results.jd | fetch_job_details/同源资料加载 → run_jd_stage.jd_map → enriched.jd → run_fine_stage → match_jds | 原文明确信息才可作相关性依据 |
| 独立公司简介、详情分类更新 | 尚无本轮完整传递保证 | 不假设存在，不新增采集/字段 | 不作为本轮依赖 |

pipeline_exec_details 与 ai_screen_jd 主要保留 JD，适配器不能读取中途已丢的额外详情字段。主营相关正例须在已获取 JD 中有实际主营描述；不存在时仍按原未知口径处理。平台别名仅由 ai_platform_adapter 处理。

已有公司名称、分类、宣传词不是主营业务证据。岗位职责、产品和业务描述的原文是可供判断的事实；不存在的公司简介不得新增假值。领域生效时完整可用 JD 到达精筛；无领域选择保持现有路径。个人画像和其他条件仍以已有来源和契约为准。

## 派生领域上下文（内部，非新持久化模型）

纯规则输出可用不可变对象或普通映射，名称由实现选择；必需信息为：是否存在选择、已选业务标签、来源类别（原始组/平台覆盖/旧格式）、稳定规则版本、语义摘要。

- 标签在一个组内按整个业务含义理解，多个标签取并集；不构造新增词库或将 slash 文本机械拆成关键词命中器。
- 语义摘要由规范化选择和规则版本派生；不包含岗位正文、画像原文、凭据或账号资料。
- 与当前 run 的准确来源、平台、profile、Flow/Track 身份一致；无 Flow 的旧格式按原有来源身份处理，不能向其他 Track 借条件。
- 不修改 frozen_filters 的业务键或 API 入参，避免影响现有全字典比较。

## 既有筛选运行记录

使用现有 execution_params JSON 的内部元数据记录规则版本与领域含义摘要；无需增加 store 写入口或公开输入输出结构；JD 只读助手的内部可选参数见下节，不涉及迁移。键名在内部实现中统一定义，不增用户配置。

判定恢复和 JD 资料恢复是独立通道；只有判定需要领域版本/语义一致。客观 JD 不依赖筛选条件或画像摘要，但必须满足来源、平台、画像归属与岗位身份一致。

新建领域 run：先解析真实上下文、读取允许复用的 JD，再记录元数据，后运行粗精筛。恢复：先读取旧运行元数据并检查兼容，后处理现有记录，禁止先覆盖版本再检查。缓存需同时满足既有来源、完整条件、画像和画像事实约束，以及领域版本/语义一致。

自动候选在返回前核对版本和当前预期领域语义，不同语义候选跳过，使既有入口可正常新建筛选。旧未版本化领域 run 不自动复用；显式续跑若不兼容，沿既有失败处理停止，保持原判定和 checkpoint。现有入口新建同源筛选重新判断，即使没有判定续跑标识，也读取允许的同源旧 JD；读取失败不能伪装为空，读取成功不能带入旧判定或粗精筛 checkpoint。已完成历史记录只读；不批量迁移或删除。无领域选择的恢复语义不变。

## JD 资料复用

用 `TaskStore.latest_screen_runs_for_source(source_id)` 无 statuses 模式获得全部非结果快照记录，按 created_at 升序返回；不能用每状态仅一条的模式代替。反向遍历，跳过当前 run、queued/running、来源/平台/profile 不一致记录；无画像旧记录仅在当前也无画像且来源可证明时复用，不能猜测跨画像归属。

候选只按当前来源岗位集合的平台注册 ID 取交集，不按标题或公司名匹配。使用 `screen_flow.load_resume_jd(store, ctx.jd_checkpoint_path(..., candidate_id), candidate_id, include_dropped=True)`，资料模式合并文件与结果表，同候选文件优先；文件缺失/为空用结果表，损坏文件仍按原异常处理。现有 store.load_screening_jd_map 过滤 is_dropped=0，须补内部 include_dropped=True 的只读模式来返回指定 run 所有有效 JD；不能让旧剔除标记限制客观资料。两个助手默认参数 False 保持旧续跑行为，不改表结构或写入。当前 JD > 兼容续跑 JD > 较新候选 JD > 较旧候选补缺；读取只读，不改写旧文件/结果，不带入 verdict 或旧粗精筛 checkpoint。缺失资料正常进入抓取；损坏资料按原加载失败处理，不伪装为空。资料不会因规则版本、行业条件或画像摘要变化而失效。

详情阶段按幸存岗位 ID 计算缺 JD 集合；资料齐全不启动该阶段浏览器/CDP/详情抓取，部分缺项只抓这些岗位。原身份、来源完整性、暂停/风控检查不豁免。

## 状态与失败

沿用现有粗筛 → 详情 → 精筛 → 结果阶段及暂停、失败、恢复语义；不增加领域状态或默认匹配。上下文错误继续原异常分类。规则兼容失败采用 error_registry 中登记的 `screening_policy_incompatible`；不是新状态或新结果字段，不自动重试同一旧判定。runner 独立捕获并调用现有 persist_ai_worker_failure，ai_screen_failure/flow_task_state 白名单保留分类，注册说明写入 run.error_reason、task.error、Track.reason。说明表达规则变化、现有入口重新筛选、资料保留，不能被 internal_error 泛化；缺少 JD、画像或模型调用失败继续原有口径，不能称为领域不相关或成功匹配。
