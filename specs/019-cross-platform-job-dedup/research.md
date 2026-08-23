# Research: 跨平台岗位去重（019）

**调查时间**: 2026-08-23 | **方式**: 代码库事实核查（两轮专项探查 + 关键路径直读）

## R1. 流水线拓扑：按平台各跑一条（推翻初稿假设）

**Decision**: 去重生效点定为「后跑平台 AI 筛选任务的输入组装处」，而非初稿假设的「单 run 内跨平台合并层」。

**Rationale**（全部为代码事实）:

- 抓取任务单平台：`_run_pipeline_task` 的 `frozen_platform` 为单值（`app.py:2325`）；组合展开 `expand_combinations` 只有关键词×城市×筛选，无平台维度（`pipeline_exec.py:582-585`）。
- AI 筛选任务继承父抓取平台：`/api/ai-screen` 从父 run 读平台并校验一致性（`app.py:5238-5253`）；`_run_ai_screen_task` 的 `frozen_platform` 单值（`app.py:2952-2957`）。
- 历史轮身份 = 抓取任务 + 平台：`result_rounds._existing_round_for_flow(store, scrape_task_id, platform)`（`result_rounds.py:214-233`）；`save_pipeline_result` 每行的 platform 取轮次级参数（`store.py:846`）。
- 两平台结果仅在前端合并：`fetchMergedLatestResult` 分别拉 `/api/latest-pipeline-result?platform=boss|zhilian` 后 flatMap（`DiscoveryView.vue:2334-2392`），无任何去重。
- 因此：跨平台重复 = 「后跑平台轮」与「先跑平台已完成轮」之间的重复；单 run 内（`pipeline_exec.py:1131` 合并、`app.py:2496` 续跑合并）永远只出现单平台岗位，不是跨平台去重的生效位置。

**Alternatives considered**:

- 单 run 内合并层去重（初稿方案）——被 R1 事实推翻：该层见不到跨平台岗位。
- 前端合并视图 JS 端去重——需把归一化规则移植为 JS 双份维护；且不省 AI 额度与 JD 抓取（用户核心诉求之一）；仅作为展示映射的辅助（见 R5）。
- 新增跨平台合并查询端点——更大前端改造且同样不省额度；无必要。

## R2. 去重源：对端平台近 30 天全部可见轮（2026-08-23 用户确认修订）

**Decision**: 对端判定源 = 对端平台**近 30 天内全部可见轮**（done/partial/scraped_only）中的**非剔除岗位**（`is_dropped=0` 的结果行），逐轮做画像过滤；剔除记录追溯目标 = 最近一个包含该岗位的对端轮。

**Rationale**:

- 用户实际使用是两平台交替各跑、历史轮多：岗位 X 可能只出现在对端较早轮（最新轮被后续跑别的关键词的轮顶替）。仅比最新轮会漏掉 X → 后跑平台重复筛一遍，额度浪费随历史轮数量放大。
- **显示层无回归**（关键推演）：合并视图只显示两平台各自最新一轮；只存在于对端旧轮的岗位，在后跑平台开跑前就已不在当前视图。后跑平台将其剔除，可见性不变，反而剔除记录给出「它在哪个历史轮、链接是什么」的追溯。因此扩大比对范围对显示是严格无损的。
- 时间窗（30 天）兜底：避免拿很久以前的同名岗位（实际是重新招聘的新帖）当重复源误剔；窗口按轮定稿时间判断，超窗整轮不参与。
- 剔除行不作判定源（spec FR-009）：若对端把该岗标为「跨平台重复」剔除后又成为本端判定源，会让岗位在两平台视图同时消失。
- scraped_only 轮可作判定源：岗位身份与是否筛选无关；额度照省、可见性不受损（同上推演）。
- 同一岗位出现在对端多轮 → 追溯取最近包含轮（对端最新可见轮中它若存在，行为与「仅比最新轮」版本一致；不存在则指向最近的历史轮）。

**Alternatives considered**:

- **仅对端最新一轮**（初版方案，用户质疑后否决）——交替跑 + 历史轮多时漏判大量已筛岗位。
- 对端全部历史轮（无时间窗）——覆盖最大，但旧同名岗位（重新招聘）会被误当重复源；30 天窗以极小漏判代价换掉这类误合。
- 持久指纹身份库（新表）——DIRECTIONS.md「统一岗位身份数据落点」的正式做法，覆盖最全且为 B055 跨轮复用铺路；但需新表 + 迁移 + 与轮次清理联动的生命周期维护，本期工程量不成比例。现方案用「多轮读入 + 内存指纹索引」达到同等判定效果，零 schema 变更；身份库留给 B055 一起评估。

## R3. 剔除落点：复用粗筛剔除链路（verdict + checkpoint + 计数）

**Decision**: 跨平台重复岗以 verdict=dropped 落库、计入 ai_rough checkpoint、并入最终 `dropped` 列表；不进 `_rough_todo`（AI 输入）、不进 JD 抓取。

**Rationale**（018 修复链的约束，全部为代码事实）:

- 判定落库走 `save_verdict_and_checkpoint_atomic` / `save_screening_verdicts`（`store.py:2767/2725`，upsert 幂等）；续跑幸存者语义=「断点内岗位默认保留，仅判定链明确 dropped 才移除」（`app.py:3163-3170`）——dup 判定为显式 dropped，续跑自然不复活。
- ai_rough 完成快照 = kept ∪ dropped 的 job_id 集合（`app.py:3258-3262`）；dup 并入 dropped 后自动入快照。
- 终态校验（018）：剔除计数必须与判定行一致，dup 走同一 verdict 落库即可，无需新计数通道。
- 续跑/重启在 `raw_jobs` 组装点（`app.py:3049`）确定性重算去重（对端轮不变则结果不变）：重复岗每次重算都得到同一剔除结论 + extra 追溯（verdict 行不存 extra，靠重算恢复，见 R5）。

**Alternatives considered**:

- 在 `raw_jobs` 里直接删除重复岗——终态/续跑链看不到它们的 dropped 判定，会出现「岗位静默消失」（018 事故同类）；且计数口径（抓取数含重复）失真。
- 单独的跨平台剔除表——新表新查询，违反零 schema 变更约束，收益为零。

## R4. 画像过滤：画像摘要一致性

**Decision**: 对端轮参与判定源的条件 = 对端轮 `profile_summary` 与当前任务一致；**双非空才比较**，任一为空不过滤。

**Rationale**: 轮次无结构化画像 ID，只有 `profile_summary` 文本（`screening_runs.profile_summary`，`store.py:837`）；单用户主路径（同一画像先后跑两平台）摘要为冻结快照、文本一致。跨画像时摘要不同 → 不互为判定源，杜绝串台。空摘要（无画像跑筛选）不去滤，保持去重可用。

**Alternatives considered**:

- 完全不过滤——跨画像串台风险（用户可感知为「莫名少岗位」）。
- 用 profile_facts 哈希比对——更严但复杂；摘要文本对冻结快照已足够，且空值语义更简单。

## R5. 追溯与展示：剔除行 extra + 前端运行时映射

**Decision**:

- 剔除条目（后跑平台 dropped 行）的 `extra` 携带 `{"cross_platform_dup_of": {platform, platform_job_id, source_url, finished_at}}`（随 `save_pipeline_result` dropped 行 extra 落库，`store.py:897`；随 `_build_pipeline_result_rows` 读回，`store_helpers.py:112`）。
- 前端合并视图（`fetchMergedLatestResult`）按 dropped 的 extra 反查两平台 jobs 中 `(platform, platform_job_id)` 命中的保留条目，打运行时私有标记（复用 `_result_run_id` 惯例，`DiscoveryView.vue:2374`），`JobWorkspace` 渲染「双平台在招」标注。
- **不回改先跑平台已完成轮**（历史轮不可变是 017/018 语义）。

**Rationale**: verdict 行不存 extra（`store.py:2748-2765` 无 extra 列写入），续跑后 extra 靠组装点重算恢复；保留条目徽标无法持久化到先跑轮（不可回改），运行时映射是唯一既满足展示又不破坏历史轮不可变的方案。

**Alternatives considered**:

- 回写先跑轮 extra 加「双平台」标记——破坏历史轮不可变（017 一条流程一条轮、原地升级仅限同流程）。
- 前端 JS 重算指纹——归一化逻辑双语言维护，否决；映射用现成 extra 数据即可。

## R6. 指纹口径：严格归一化精确匹配（代定决策）

**Decision**: 指纹 =（归一化公司，归一化标题，归一化城市）；字符级归一（全半角、空白、大小写）+ 公司剥括号注释/组织后缀/城市名前缀 + 城市取市级；三元组任一为空 → 无指纹不参与。不做互含、不做相似度、不做 AI 兜底。

**Rationale**: 冻结原则「宁可漏判不可误合」；确定性纯函数可表驱动穷测；漏判后果=保持两条（无害），误合后果=丢信息。公司后缀剥离覆盖常见跨平台写法差异（「北京字节跳动科技有限公司」→「字节跳动」）；标题不做语义归并（「工程师」后缀差异属漏判容忍）。

**Alternatives considered**: 严格+模糊两档、归一化+AI 兜底——两者都引入误合通道或成本，与冻结原则冲突（质询推荐项已声明，用户未应答按推荐执行）。

## R7. 明确边界（不做的）

- 仅抓取（scraped_only）轮自身不去重：保留原始抓取视图；但可作对端判定源（R2）。
- 重抓/补筛回写（`apply_recrawl_writeback`）不触发跨平台判定：回写目标是既有轮，不重走输入组装。
- 旧数据不回溯：上线前轮次间的既有重复照旧。
- 同平台语义零变化（FR-007）：run 内合并、scrape_run_jobs 落库、收藏/反馈身份全部不动。
- `webui/pipeline_exec.py` 与 `webui/store.py` 零改动。
