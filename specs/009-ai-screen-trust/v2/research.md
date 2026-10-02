# Research: B094 领域相关岗位覆盖

**日期**：2026-10-02。依据为冻结 Spec 与本地源码静态核查；没有产品测试、真实模型验证或正式数据查询。当前不引入新技术和外部服务，无需重新选择平台或付费方案。

## R1：保留用户真正选择的业务含义

- Decision：复用 Track.confirmed_filters_snapshot。V2 无行业覆盖时使用 unifiedValues.industry；覆盖、清空或当前明确选择变化时以实际平台条件为准。旧格式使用平台注册标签。
- Rationale：`webui/src/parallelFilterMapping.ts` 多个不同组会映射到相同平台编码；游戏、电商等在智联可能包含互联网大类，只传编码不能还原原始组。`ConditionSnapshotV2` 已有 unifiedValues、platformValues、overrides；覆盖键存在与否有意义，空数组也可能为显式清空。
- Evidence：`useDiscoveryParallelFlow.ts` 将快照传到现有 Flow 入口；`store_flow_claims.py` 保存并公开返回 Track；`store_flow_core.py` 保留快照结构。`flow_task_coordinator.resolve_flow_binding` 验证准确身份。
- Alternatives：新增独立 AI 控件已被用户排除；新增 API 字段无必要；把所有平台码反向并成领域会扩大或缩小用户选择，拒绝。

## R2：解除编码误排而保留领域条件

- Decision：行业不参与编码不相交硬剔除；相关性由共享领域约束在现有粗精筛执行。粗筛信息不足不能据此丢弃待详情判断岗位，其他硬条件继续有效。
- Rationale / Evidence：`ai_filters._job_criteria_hard_mismatch` 遍历包含 industry 的字段；`ai_screening` 两阶段都先调用硬规则；粗筛内联文本和 `prompt_texts.py` 又把行业写为绝对条件。只改精筛提示词无法救回粗筛前被丢的岗位。
- Alternatives：只删硬规则会放弃领域条件；每个平台单独修补会复制通用规则；关键词词典会将偶然提词误当业务，均不采用。

## R3：让已有业务事实真正进入模型

- Decision：适配器只归一化原始列表公司/分类背景与完整 JD 中职责、产品、主营原文，按 data-model 来源表及真实函数协作测试保证可达；领域生效时精筛传完整可用 JD，不固定截取前 1500 字。公司名和行业标签只作上下文，不能当主营业务证明。
- Rationale / Evidence：精筛当前 JD 输入使用 `[:1500]`，后文业务证据可能被截掉。默认适配器详情字段为空，粗筛未充分提供公司信息。智联列表 search.py 返回 company 与 extra.industry；详情 JD 经 pipeline_exec_details、ai_screen_jd 保留到 enriched.jd，但详情返回的额外公司字段没有完整传递保证。两平台未证实有独立公司简介输入，不能拿中间脚本读取值当作已到模型的事实。
- Alternatives：增加公司查询或外部搜索超出冻结范围；凭公司名猜主营业务不可接受；按关键词选片需要新词典并可能丢掉否定语境。复用已有事实，不新增抓取。
- Limit：这只能保证输入可达，真实模型正确性仍需真实岗位验收；容量错误按现有链路处理，不伪称匹配。

## R4：防止旧判定绕过新口径

- Decision：在现有 run.execution_params JSON 保存内部版本和领域语义摘要；新规则只复用同版本、同语义且原条件/身份一致的判定。旧规则不自动成为新规则续跑候选；显式恢复不兼容旧 run 通过既有错误链阻断。新建筛选必须通过独立资料通道复用来源岗位 JD，重新判断，不以判定续跑标识作为资料开关。
- Rationale / Evidence：`screen_flow.find_resumable_screen_run` 只比条件和画像；`load_resume_verdicts_with_fallback` 可先返回自身判定或合并旧同源判定。`runners/ai_screen_rough.py`、`ai_screen_fine.py` 又直接读取 checkpoint。因此仅清空 resume_verdicts 不能保证重判，原地替换元数据也可能把未重判旧行标成新规则。
- Alternatives：迁移历史结果被禁止；依据理由文案识别旧规则不可靠；为原地混合重判增加逐岗位版本和清理流程超出本轮最小设计。采用兼容守卫与只读资料通道，保留历史，避免新状态和数据库迁移。
- Limit：无领域选择时不改变既有缓存逻辑；相同编码、不同原始组必须通过语义摘要区分。

## R4a：已有 JD 不随旧判定失效

- Decision：context 服务用 latest_screen_runs_for_source 无 statuses 模式得到全部非快照历史，用 load_resume_jd(..., include_dropped=True) 合并文件/结果表，按同来源、平台、画像归属、当前岗位 ID 校验并按当前/较新资料优先补缺；旧判定不兼容不妨碍资料复用。新建领域 run 即使无 resume_from_run_id 也加载资料。
- Evidence：原 runner 的 resume_jd 加载只在 resume_from_run_id 非空时执行；store_screen_resume_mixin 明确无 statuses 返回全部按 created_at 升序记录，而传 statuses 每状态仅最新一条。ai_screen_jd 在 todo_jd 计算前准备浏览器，因此仅传缓存仍可能卡在 CDP。load_screening_jd_map 原 SQL 过滤 is_dropped=0，资料模式还须补默认 False 的 include_dropped 内部可选参数，解除旧剔除标记对客观 JD 的限制；默认续跑行为不变。
- Decision 接点：ai_screen_jd 先算缺项再准备 CDP；全齐只回填原文、部分只抓缺项。其余身份、来源完整性、暂停/风控不变。
- Alternatives：继续复用旧判定不成立；因版本变化删除旧 JD 或强制重抓会丢掉可用事实，不采用。额外详情公司字段不在本轮资料来源保证内，不扩大到新采集。

## R4b：错误说明必须穿过公开失败链

- Decision：在 error_registry 登记平台无关 screening_policy_incompatible，context 以内部类型化异常传出；runner 独立捕获调用 persist_ai_worker_failure，ai_screen_failure/flow_task_state 白名单保留分类，以注册说明写已有对外错误字段。
- Evidence：原 runner 的通用异常分支换成 internal_error；ai_screen_failure 把不在白名单的码换成 internal_error；close_flow_task_state 优先固定消息、会忽略任意传入 reason。因此仅传自定义说明不够。
- Rationale：用现有错误容器说明规则变化、现有入口重新判断、资料保留；不新建 UI 或状态，不传播原异常。补偿失败仍走 FlowStateClosureError。
- Alternatives：借用账号/风控失败或在 694 行 flow_service 中加特例，会误分类或越过尺寸边界，不采用；不改变全局通用错误格式器。

## R5：职责与验证落位

- Decision：新增领域纯规则与上下文服务；复用适配器、提示词组装、screen_flow。超预警主文件只做必要接线且不增长；测试先定义变化。
- Rationale：ai_screening 626 行、ai_screen_task 640 行，不能继续累积领域解释逻辑。既有模块已承担平台/提示/恢复职责，不另建整套筛选流程。
- Alternatives：门面加业务、单文件追加、顺手改抓取或去重均超边界。
- Validation：聚焦自动化证明契约与传递；真实 E2E 才证明用户可见结果及真实 AI 判定。整条链一次最终全量，禁止文档阶段运行产品测试。

所有影响本方案的技术疑问已用本地源码解决。真实环境和样本是否就绪属于实施验收前置条件，不冒充当前已验证事实。
