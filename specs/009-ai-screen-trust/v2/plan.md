# Implementation Plan: 垂直领域相关岗位覆盖（B094）

**Branch**: `codex/spec/b094-vertical-domain-match` | **Date**: 2026-10-02 | **Spec**: [spec.md](spec.md)

**Status**: 2026-10-03 按用户“开始执行修复和优化达到可以交付的条件”授权处理交付缺口；业务需求保持冻结，仅新增以下测试维护范围，最终验证状态以 Tasks 最新记录为准。

**Delivery Base**: `f43ed37`；当前交付范围门禁同时检查该基准到 HEAD 的改动及未提交层。用户最新确认：领域效果达到 90% 即可；暂停原失败用例及相关回归未复现超时则本轮通过；修复原失败测试并聚焦验证，不重复全量，不自动提交、合并或推送。

## Summary

沿用行业组选项，按岗位工作、产品、公司实际主营业务判断相关性；多选取并集，其他条件保持原规则。修正粗筛和精筛前的行业码误排，保留原始组选项含义，将已获得的业务事实交给现有 AI 调用。防止续跑复用旧规则判定绕过新口径。

## Technical Context

- Language/Version：仓库声明 Python >=3.10；前端声明 Node >=20、Vue 3.5、TypeScript 5.9。均为本地配置事实，不是外部最新版本结论。
- Primary Dependencies：复用 Flask、现有 AI 客户端和平台注册适配器；无新依赖、模型、外部服务或关键词词典。
- Storage：复用 TaskStore 的岗位事实、Flow/Track 条件快照及 run.execution_params JSON；无建表、迁移、新列或正式数据批处理。
- Testing：unittest 聚焦及最终后端全量，前端现有测试与构建；真实浏览器验收需已就绪账号、配置与真实岗位。
- Target Platform / Project Type：现有桌面应用及浏览器 WebUI；本次只改变后台 AI 筛选行为。
- Performance Goals：未新增耗时、费用或召回率数值承诺。领域选择生效时传递完整可用 JD，可能增加单次输入量；不新增判断阶段或调用类型，必要重判仍使用现有粗精筛预算；不削减现有失败、重试与安全约束。
- Constraints：不得以行业码或通用职位名提前排除；不得编造公司业务；无法取得业务事实时遵循既有未知口径；保持画像、其他条件、状态和结果格式。
- Scale/Scope：岗位 AI 筛选主体 V2 的增量；三条用户故事全部属于交付范围，不包含抓取、去重、搜索条件前置、前端改版。

## A 组与 B 组的执行边界

A 组为 [spec.md](spec.md) 中七条已确认边界，保持冻结。以下是本轮自审选定的内部实现：读取现有 Track 快照、派生领域上下文、使用现有 JSON 容器记录规则版本、分别校验判定与 JD 资料复用、通过共享错误分类反馈不兼容。它们不增加产品字段、持久化模型、选择入口或结果状态，不将用户软约束升级为硬约束。

原文件落位草稿需要补入 `ai_domain_context.py`、`screen_flow.py` 和对应测试：原始条件已经存于 Track，旧判定复用发生于 screen_flow。用户本轮明确要求自行审查后做到 Tasks，因此在设计内补齐这些必要接点；未据此实施任何代码。

## Constitution Check

| 原则 | 设计核对 |
| --- | --- |
| I 职责分层 | 领域纯规则、上下文读取、平台适配、提示词组装、任务编排各自负责；门面不追加逻辑 |
| II / VI 文件尺寸和地图 | 筛选与领域职责继续分流；ai_screening 不增长，runner 仅既有失败初始化/补偿接线允许至 650 行；新模块已登记地图 |
| III 引用方向 | runner → context → 现有绑定服务/公开 store；筛选与提示词 → policy；不反向引用 API/app 或私有符号 |
| IV 行为变更 | 自动化失败测试先定义；只调整本功能接点，不混入独立拆分或重构 |
| V 验证门禁 | 开发聚焦；整条链最终一次后端全量，随后前端、真实 E2E、构建、卫生 |
| VII 异常可观测 | 快照损坏、绑定冲突、旧判定不兼容进入既有异常链；不吞错、不默认为不限、不写凭据 |

Phase 0 前已核对架构原则；Phase 1 后复核无需要原则豁免的设计冲突。该结论仅表示设计静态自审，不表示代码或功能通过。

## File Boundaries

### 新增产品与测试文件

| 路径 | 职责 | 行数预算 |
| --- | --- | --- |
| `webui/ai_domain_policy.py` | 领域条件来源选择、并集与整组语义、提示约束、规则版本与兼容性纯函数；无平台名或平台码 | 120–240 |
| `webui/ai_domain_context.py` | 通过既有公开绑定和 store 服务读取准确 Track；派生并冻结上下文，提供兼容恢复信息及同源 JD 只读复用（不写终态） | 180–300 |
| `tests/ai/test_ai_domain_context.py` | 原始组保留、覆盖/清空、身份隔离、损坏快照、版本兼容及资料来源隔离测试 | ≤750；保留现有 699 行，不继续追加测试 |
| `tests/ai/test_ai_domain_recall.py` | 两阶段输入和漏岗回归、资料复用/完整调用链、通用职能、偶然提词、多选、其他条件 | ≤750；保留现有 715 行，不继续追加测试 |
| `tests/ai/test_ai_domain_repair.py` | 独立审查的失败链、实际续跑签名、JD 暂停/重启、无行业旧输入、旧调用与粗筛证据回归；复用现有假件 | ≤320 |

### 允许修改的已有文件

| 路径 | 核查行数 | 允许改动 / 目标上限 |
| --- | --- | --- |
| `webui/ai_filters.py` | 325 | 行业编码不再硬剔除；其他条件保留，接共享领域解释；校验粗筛证据及精筛虚构已选区间的无效回答 / 450 |
| `webui/ai_platform_adapter.py` | 392 | 平台标签、事实归一化、硬字段描述；差异留在适配器 / 550 |
| `webui/ai_prompts.py` | 72 | 复用组装入口承接粗筛提示词、统一粗精筛领域约束 / 220 |
| `webui/prompt_texts.py` | 161 | 领域与其他条件提示口径 / 250 |
| `webui/ai_screening.py` | 626 | 仅必要规则/输入接线；现有粗筛及重试说明交已有组装模块，判断助手留在 ai_filters；本次无效回答一次重试及安全诊断接线 / ≤650 |
| `webui/runners/ai_screen_task.py` | 640 | 调用领域上下文服务、保留元数据、在恢复前检查兼容性；初始化失败状态、类型化异常补偿及证据失败保护，不增加状态机 / ≤650 |
| `webui/screen_flow.py` | 258 | 判定版本守卫；JD 加载增加仅内部可选 include_dropped 参数，默认行为保留，独立资料模式合并文件/结果表 / 350 |
| `webui/store_screen_resume_mixin.py` | 71 | 仅 JD 读取助手增加 include_dropped 可选参数，默认仍读非剔除行；新资料通道可取指定 run 的所有有效 JD，不改写入/迁移 / 100 |
| `webui/runners/ai_screen_jd.py` | 526 | 在浏览器准备前计算真正缺 JD 的幸存岗位；资料齐全时只回填，部分缺失只抓缺项；不改抓取机制 / <600 |
| `webui/error_registry.py` | 476 | 登记平台无关的规则不兼容错误及安全说明、恢复语义 / <600 |
| `webui/ai_screen_failure.py` | 72 | 让现有失败入口保留该已注册错误分类 / 90 |
| `webui/flow_task_state.py` | 230 | 仅补错误分类透传，确保 run/task/Track 说明一致；不改状态转换或身份规则 / 250 |
| `tests/ai/test_ai_platform_filters.py` | 274 | 行业契约、两平台标签/事实与其他条件回归 / 400 |
| `tests/test_ai_prompts.py` | 161 | 粗精筛共享领域口径、无选择及画像约束回归 / 300 |
| `tests/test_screen_flow.py` | 448 | 自身与同源旧缓存、兼容恢复、无领域选择原行为 / ≤650；保留现有 632 行，新增回归放 repair 文件 |
| `tests/test_error_registry.py` | 172 | 规则不兼容错误注册、平台无关和不得自动重试原判定 / 230 |
| `tests/test_b096_production_flow.py` | 212 | 用既有临时 store 验证失败入口到任务/run/Track 对外字段说明一致 / 350 |

- 设计阶段允许写：本主体 `INDEX.md` 与 `v2/` 设计工件；`spec.md` 继续冻结，历史根目录 V1 只读。
- 后续实施允许同步：`README.md`、`CHANGELOG.md`；只写真实落地的用户可感知变化。
- 后续实施允许修改 `.specify/memory/constitution.md` 的模块地图，登记两个新模块，不改变原则或门禁。
- 本轮修复额外允许：`webui/src/errorCodes.ts` 仅同步登记错误说明；`tests/healthy_pipeline/test_pipeline_convergence_pending.py` 仅调整原失败用例为部分 JD 缺失，保留持久化断言；`tests/ai/test_ai_match.py` 仅将原计数测试的无证据城市剔除假件改为有标题和画像证据的实习/全职冲突，保留全部计数断言。两份超长既有测试不增长、不新增逻辑。以上是用户全权修复授权下的必要契约接点，不扩展业务需求。
- 交付修复追加范围（用户 2026-10-03 最新执行授权）：`webui/src/components/__tests__/ResultHistoryDrawer.spec.ts`、`webui/src/views/__tests__/DiscoveryHistoryMode.spec.ts`、`webui/src/views/__tests__/DiscoveryRecovery.spec.ts`、`webui/src/views/__tests__/DiscoveryScrapeOnly.spec.ts`、`webui/src/views/__tests__/DiscoveryView.spec.ts`。仅修正历史平台导航前提、轨道行选择器，以及已确认的聚合/平台分页和移除冻结提示契约；保留历史只读、计数、删除、日志、详情、键盘和原始数据不泄露的业务断言，新增默认聚合页守卫。不改前端产品代码、历史 Spec 或冻结哈希，不新增文件。五文件负责当前前端聚焦回归，不等同前端全量。
- 门禁维护追加范围（用户最新明确允许修改并要求测试通过）：`tests/test_b096_final_review.py`。历史冻结清单原字节及旧 Spec 保持只读；旧产品字节冻结和跨所有后续功能的旧范围检查改为历史清单完整性及本次精确交付范围检查。不得删除业务守卫、跳过用例或放宽暂停超时；新增清单损坏和越界文件必须失败的负向检查。
- `.specify/feature.json` 已指向本版本，不重复改写。`.trae/rules/project_rules.md` 不存在，以本索引和现有 feature 状态提供代理定位，不建立平行项目规则。

### 禁止修改

白名单外文件；历史 Spec；门面 `webui/app.py`、`webui/store.py`、`webui/ai.py`、`scripts/boss_cdp_raw.py`；`scripts/**` 抓取及风控；`webui/src/**` 选择界面和映射（仅上述错误说明镜像例外）；API 路由、Flow 状态转换与身份编排（白名单内仅允许错误分类透传）、store 写入/迁移及白名单外 store（唯一允许上述 JD 只读助手）、账号凭据、正式数据库及已完成历史结果；`pipeline_exec_filters.py` 无产品调用过滤函数；B106 去重和 B071 条件前置；构建产物、全局规则与 `AGENTS.md`。

实施若必须越过边界，报告具体阻断后调整设计，不以 Tasks 自动增权。

### 引用方向与落位理由

- `runners/ai_screen_task → ai_domain_context → flow_task_coordinator.resolve_flow_binding / TaskStore.get_flow`，只使用公开接口，不改变绑定服务。
- `ai_domain_context / screen_flow / ai_filters / ai_prompts → ai_domain_policy`；纯规则不依赖流程、store、具体平台。
- `ai_screening → ai_filters / ai_prompts / ai_platform_adapter`；平台编码和事实别名仅在适配器。
- `ai_prompts → prompt_texts`；粗精筛共用领域选择，各用与事实阶段对应的说明，其他条件口径继续保留。
- `ai_domain_context → screen_flow.load_resume_jd / TaskStore.latest_screen_runs_for_source` 只取资料；`screen_flow → ai_domain_policy` 不反向 import context，自动候选与 context 共用 screen_flow.read_domain_selection 的只读语义入口，避免循环引用；旧直接筛选调用由平台注册适配器按实际行业标签派生，不反向猜测统一组。
- 错误码与安全说明唯一登记于 `error_registry`；context 产生带已注册分类的内部异常，runner 单独捕获并调用 `ai_screen_failure.persist_ai_worker_failure → flow_task_state`。不经过通用异常分支，不修改 694 行的 flow_service 或 API。
- 既有筛选域接近预警线，新增职责必须分流；已有提示词模块复用，不创建旁路调度或新筛选器。

## Project Structure

本版本设计：`spec.md`、`plan.md`、`research.md`、`data-model.md`、`contracts/domain-screening.md`、`quickstart.md`、`tasks.md`、`checklists/requirements.md`。主体索引为上一层 `INDEX.md`。

产品落点：`webui/` AI 筛选域，`webui/runners/ai_screen_task.py` 为编排接点，`tests/ai/` 与既有筛选流程测试为验证落点。具体文件以白名单为准。

## Phase 0: Research

已完成静态核查并记录 [research.md](research.md)。确定复用原始 Track 快照，不增加前端或 API；确认行业前置误排、业务信息未充分传入及旧缓存旁路。无尚未解决的技术选型或新服务依赖。未执行真实模型或岗位验证。

## Phase 1: Design

设计详见 [data-model.md](data-model.md)、[contracts/domain-screening.md](contracts/domain-screening.md) 与 [quickstart.md](quickstart.md)。

1. 用当前 run 的绑定定位自己的来源 Track；V2 无覆盖时采用原始 unifiedValues 行业组，覆盖时以实际平台选择为准，显式清空不复活原组选项；旧格式按实际平台标签解释，不猜测反向映射。
2. 粗筛不凭缺少领域信息、行业码或通用职位名排除，保留进入详情判断的机会；其他已证实冲突仍生效。
3. 事实来源严格按 data-model 的表：列表中的公司名称/行业只作背景，工作、产品和主营业务证据来自完整 JD 原文；不假设独立公司简介字段。领域生效时不再将 JD 固定裁成前 1500 字；无领域选择保持既有输入行为。测试须经过真实 runner/JD/精筛函数协作到模型边界，不能直接给 match_jds 塞一个生产链路没有的公司业务字段。
4. 粗筛与精筛共享准确领域标签；粗筛只防误排，完整业务判定口径只交给有 JD 的精筛。实习/全职冲突在程序侧复核标题与画像的必要证据；薪资、经验、学历等硬字段理由复用既有条件/列表冲突校验，防止模型把画像事实当作新条件，不改变已有筛选规则。精筛回答虚构已选区间或重复按薪资高低拒绝时，经现有单项路径最多重试一次并携带规则纠正说明；生产默认漏回预算为 0 时也执行这一次矛盾回答重试，普通漏回与传输错误的原预算不变。未纠正则待确认，真实 JD 硬要求及高危证据仍生效。
5. 新领域 run 在现有 execution_params 记录规则版本及领域含义摘要。只复用同版本、同语义、同来源及既有身份/条件/画像一致的判定；先检查自身记录再做同源回退，不能在提前返回路径跳过守卫。
6. 旧规则未完成 run 不自动接续为新规则 run；显式要求续跑不兼容旧记录时，在读取或覆盖元数据前经既有失败链说明不兼容，不悄悄复用判定或仅清空内存后继续使用旧 checkpoint。通过现有筛选入口对同一来源建立新 run 时重判；即使 resume_from_run_id 为空，也必须独立读取经来源/平台/画像/岗位身份校验的旧 JD，不能把判定不兼容等同资料不兼容；已完成历史结果不迁移、不改写。无领域选择保留既有恢复行为。
7. JD 资料读取：调用 `latest_screen_runs_for_source(source_id)` 不传 statuses，得到全部非快照运行，再过滤活跃/身份不一致记录。当前岗位已有 JD 优先，其次本次兼容续跑 JD，最后按创建顺序新到旧补齐同源 JD。`load_resume_jd(..., include_dropped=True)` 的资料模式合并检查点与结果表（同候选文件原文优先）；`load_screening_jd_map` 的对应只读模式不按 is_dropped 排除已有 JD，默认参数保持旧续跑行为；不删除旧文件或结果，不加载旧粗精筛 checkpoint；空资料才进入正常详情抓取，损坏资料按已有加载异常处理。
8. `ai_screen_jd` 在浏览器准备前计算真正缺 JD 的岗位；全部已有时跳过本阶段 CDP 准备与抓取、回填完整原文，部分缺项只抓缺项。原任务身份、来源完整性、暂停与风控门禁保留。
9. 显式旧规则续跑失败采用共享 `screening_policy_incompatible` 分类，安全说明表达规则已变化、需从现有筛选入口重新判断、已抓资料保留，不冻结额外界面文案。快照异常同时保留 filter_snapshot_incompatible 的安全说明。在 runner 的已初始化保护边界独立捕获，通过公开失败入口收口为既有 failed；共享白名单保留分类，现有 run.error_reason、task.error、Track.reason 返回同一安全说明。不要借用 internal_error，或把异常正文传给界面；状态持久化失败继续既有 FlowStateClosureError 路径。

## Phase 2: Tasks

任务由 [tasks.md](tasks.md) 记录；共享规则、提示词和测试默认串行修改。先定义失败回归，再实现，随后分故事验证；不把单个故事当作完整 B094 交付。

## Verification Gate

- 原设计阶段仅做文档检查。本次已获产品修复授权，执行失败先行的聚焦回归、相邻流程、构建与卫生；真实验收按已就绪样本记录，不以桩替代。
- 后续聚焦覆盖 7 条 FR、5 条 SC；测试数据与假模型明确标注，验证代码传递和契约，不能证明真实 AI 语义正确。
- 真实 E2E 按 quickstart 从现有行业入口到结果内容验收三条故事；缺少真实业务证据、账号或配置时列未验证，不能造数据补齐。
- 全链收敛后：一次后端全量 → 前端测试 → 真实 E2E → 构建 → 卫生检查。失败保存清单，聚焦修复并重新收敛后才做必要最终确认。
- 无提交、推送、合并、版本提升或发布任务；当前产品修复已授权，冻结 Spec 不改。

## Risks and Limits

行业选择在平台搜索阶段可能已经限制搜索结果；本次保证已进入筛选链路的岗位不因分类误排，不承诺改变搜索侧候选全集（B071 独立）。公司真实主营业务证据可能未取得，不能用行业、公司名或关键词补造。完整 JD 输入可能触发现有模型容量限制，使用现有错误处理，不静默截断到丢失领域证据。不兼容旧判定必须重新筛选，不能把旧结果迁移当作实现捷径。

## Complexity Tracking

无原则豁免、新基础设施或数据迁移。两个内部模块分别承接纯领域规则与上下文读取，避免超预警主文件增长；复杂性来自已确认组选项和既有恢复契约，非新增产品功能。
