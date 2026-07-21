# Research: 快速简历驱动岗位推荐收口

**Feature**: `005-fast-resume-discovery`  
**Date**: 2026-07-20  
**Method**: 当前代码、004 工件、当前真实 E2E/AI smoke 产物、受控串行 harness 和三个独立只读研究任务

## R1 — 性能基线与主要瓶颈

**Decision**：以捕获的真实运行作为性能基线：480 秒完成 9 个详情，约 53 秒/详情；详情并发 1；最新运行没有进入评估。首个优化对象是详情执行链和工作量，而不是先提高 AI 并发。

**Rationale**：当前每岗位独立启动 scraper、重新建立 CDP 会话/标签页、固定等待页面、执行多次滚动，并在单岗位命令的最后仍等待 10–25 秒。详情成本按岗位线性叠加；评估尚未开始，不能把本次慢归因于 AI。

**Alternatives considered**：

- 直接提高全局线程数：会把重复初始化和来源风险并行放大，拒绝作为首修。
- 只缩短前端轮询：只能改善观感，不能降低真实耗时。
- 只减少 AI prompt：本次真实慢发生在 AI 之前，不能解决主因。

## R2 — 运行结构

**Decision**：policy v2 使用持久化渐进流水线：`列表候选池 → 预检/优先详情 → 详情完成事件 → 单岗位评估 → canonical recommendation projection`。详情生产和 AI 评估可以重叠，但每个 work unit 仍独立持久化。

**Rationale**：当前“全部详情完成后才评估”导致首批结果等待整个详情阶段。渐进流水线可在不提高来源频率的情况下显著缩短用户等待，并保留取消、恢复和单岗位失败隔离。

**Alternatives considered**：

- 保留阶段栅栏，只在终态展示：实现简单但无法满足首批 5 分钟门。
- 完全事件化重写所有 004 状态机：风险和迁移范围过大；采用 policy v2 增量状态和 v1 兼容。

## R3 — 详情来源执行方式

**Decision**：使用单个受控详情 producer、每批最多 5 个岗位、一个可复用 CDP 会话并为每岗位创建独立 target。scraper 通过 stdout 输出不含 JD 的结构化安全完成/失败事件，`ScraperExecutor.on_output` 只把事件放入有界队列；runner 消费事件并读取原子详情产物。默认 source worker 为 1。

**Rationale**：仓库已有可取消、可限时、支持 `on_output/on_poll` 的 `ScraperExecutor`，无需新执行器或第三方依赖。批次减少 Python 和 CDP 初始化成本，单岗位事件又保留 checkpoint 与渐进评估。

**Alternatives considered**：

- 每岗位一个子进程：隔离好但已证明线性成本过高。
- 直接在 Flask 进程调用 scraper 函数：取消和进程隔离退化，拒绝。
- 长期常驻独立服务：引入部署、协议和生命周期复杂度，当前规模不需要。
- 多个详情子进程并行：保留为真实稳定性通过后的受控 policy 选项，不作为默认。

## R4 — 页面等待与来源安全

**Decision**：将固定 5–10 秒加载和完整模拟滚动改为 readiness-driven 处理：最多 12 秒轮询有效 JD 条件；初次未满足时执行一次受控滚动/重试；岗位间保留 3–7 秒抖动；批次最后一项不再等待。连续两个验证、登录墙或明显限流信号触发 circuit breaker，停止新增来源工作。

**Rationale**：等待应服务于页面准备和来源稳定，而不是在单岗位进程退出前无条件空等。默认 source concurrency 1 加 circuit breaker 比盲目并发更符合个人求职和账号安全边界。

**Alternatives considered**：

- 完全取消等待和滚动：可能降低详情完整率或触发来源限制，拒绝。
- 保留 10–25 秒末尾等待：在 `--max-details 1` 下没有节流后续同进程工作的意义，拒绝。
- 默认并发 2：外部阈值未验证；真实小样本达标后才允许开启，最大仍为 2。

## R5 — 列表候选优先级与详情预算

**Decision**：不用额外 AI 预排序。先持久化全部去重列表候选，执行三态硬条件预检，再按每方向队列选择详情：每个有候选方向先获得最多 2 个名额，剩余名额按以下确定性顺序分配：预检 pass 优先 unknown、方向/搜索词直接关联、标题相关性、列表信息完整性、软偏好匹配、来源顺序，最后以 canonical job id 稳定 tie-break。标准预算 15，可在 12–20 内调整。

**Rationale**：确定性选择快速、可解释、可测试，不增加远程调用。方向 floor 防止核心方向占满预算；deferred 候选保留，可在后台扩展而非丢弃。

**Alternatives considered**：

- 按来源顺序取前 60：当前行为，慢且不代表最匹配，拒绝。
- 额外调用 AI 给 100–200 条列表排序：成本、延迟和不可重复性高，拒绝首轮使用。
- 固定 60/25/15 百分比：方向数量变化时不稳定；使用每方向 floor + 剩余确定性排序。

## R6 — 候选人画像合同

**Decision**：新增 `candidate-analysis v4`，一次远程分析请求链同时返回摘要、结构化 facts、证据、未知项和方向。facts 覆盖 work/project/skill/industry/education/achievement/seniority，必须引用同次 evidence。程序负责 locator、PII 隔离、字段 quarantine 和质量状态；最多一次携带安全 warning 的纠正请求。上传端点默认只存文件，discovery 分析入口是唯一候选分析入口。

**Rationale**：当前 v3 只有摘要和通用 evidence，不能支持字段级纠正；旧上传又执行一次 legacy parse，造成重复远程调用和结果分叉。v4 在同一合同内形成事实与方向，可保持一次分析链和统一证据。

**Alternatives considered**：

- 从现有 evidence 本地猜测结构化工作经历：信息不足且会把推断伪装成事实，拒绝。
- 保留 legacy parse + v3 analysis 两次调用：成本和一致性问题继续存在，拒绝。
- 直接修改已确认画像：破坏历史运行复现；采用 draft/confirmed 版本。

## R7 — 岗位 AI 评估合同

**Decision**：新增 `job-assessment v2`：一次请求只评估一个岗位，但包含最多两个最相关方向；返回按 direction id 分隔的 assessments。每个方向独立校验证据、维度、分数和差距；一个方向无效不污染另一个。首个 envelope 无效时允许一次仅针对无效方向的安全纠正。旧运行继续使用 v1。

**Rationale**：当前调用数约为岗位×方向×1–2。一个岗位共享同一 JD，将最多两个方向放入同一请求可减少重复 token/网络等待，同时保持每方向独立持久化和分类。

**Alternatives considered**：

- 一次批量评估多个岗位：失败隔离和证据引用更复杂，首轮不采用。
- 继续每岗位每方向单独调用：简单但不能控制方向放大。
- 完全取消 AI：无法满足职责、技能和行业迁移的语义判断。

## R8 — 最低薪资语义

**Decision**：policy v2 将用户 `min_salary` 规范化为人民币月薪 K 的数值下限，且只由用户明确确认。岗位月薪区间上限明确低于下限时为 violation；区间能够达到下限时为 pass；无法比较时为 unknown。`x-yK·N薪` 使用基础月薪区间；明确年薪可折算为月均区间；日薪、面议、缺失或无法识别格式为 unknown。`min_salary` 不伪装成旧 BOSS `salary` code；旧 advanced salary code 继续由 v1 处理。

**Rationale**：UI 当前发送 `min_salary`，但 source allowlist 和硬规则只处理离散 `salary` code。数值 floor 与离散搜索 bucket 语义不同，必须版本化并由程序精确判断。

**Alternatives considered**：

- 将 20K 自动映射某个来源 salary code：可能过度过滤或放过明确低薪，不作为真值。
- 广告下限低于 20K 就全部淘汰：会误杀 15–25K 等可满足下限的岗位；仅上限低于才 violation。

## R9 — 持久化与迁移

**Decision**：使用单一 additive migration 015，新增候选画像版本/事实/事实证据和 run candidate 表；对 confirmation、run、snapshot、assessment 增加 nullable identity、progress、freshness 和 timing 字段。`discovery_run_candidates` 是列表恢复和详情优先决策的事实来源，废除从临时文件名推断 run id 的恢复依赖。旧运行新字段允许为空并走 v1 adapter。

**Rationale**：当前列表候选只在内存/临时文件，恢复路径缺 run id；详情恢复也不跳过已完成 snapshot。持久化 work unit 才能证明零重复、单调进度和渐进结果。

**Alternatives considered**：

- 继续依赖 artifact 文件：路径身份脆弱，无法安全查询优先级和恢复状态。
- destructive 重建 004 表：违反历史保护和回滚边界，拒绝。
- 新建第二个数据库：增加一致性和部署问题，拒绝。

## R10 — 推荐投影、排序和渐进传输

**Decision**：不新增第二份可漂移的 recommendation table。由一个 canonical projector 从 run candidates、snapshots、assessments、feedback 和 policy v2 重建 `RecommendationItem`。稳定 `recommendation_id = run_id + job_id`；排序 tuple 为类别优先级、match score、confidence、详情完整度、soft preference score、canonical job id。结果接口在运行中可调用，返回完整当前快照和 `result_revision`；客户端每 3 秒轮询，revision 未变化时不重绘。

**Rationale**：当前 `build_portfolio` 与 HTTP route 规则分叉。一个 projector 可让 HTTP、前端和导出共享守卫与排序。单次结果规模最多约 20，无需引入 SSE 或复杂 cursor 流。

**Alternatives considered**：

- 持久化 recommendation rows：容易与 assessment/feedback 状态漂移；当前无需。
- SSE/WebSocket：引入生命周期、测试和依赖复杂度，3 秒轮询已满足 10 秒可见门。
- 增量 append-only 结果列表：重排和撤回语义复杂；使用完整 revision 快照和稳定 identity。

## R11 — 详情复用与新鲜度

**Decision**：默认只复用 12 小时内、source status active、completeness complete、canonical URL 一致且内容身份可验证的最新详情。用户明确刷新、列表字段发生关键变化、来源状态未知/关闭或超过 12 小时时重新抓取。复用生成新的 run snapshot，记录 `reused_from_snapshot_id` 和原抓取时间，不直接引用可被清理的旧解释。

**Rationale**：同日重复运行不应重复支付详情成本，但岗位变化快，过长 TTL 会降低可信度。复制为 run snapshot 保持运行输入不可变和 30 天清理边界。

**Alternatives considered**：

- 永不复用：正确但复跑慢。
- 24 小时或更久：对关闭/修改岗位过于宽松。
- 直接共享同一 snapshot row：破坏 run-specific 输入身份和删除语义。

## R12 — 验证证据

**Decision**：005 创建独立 validation 证据，不修改 004 历史结论来制造一致。自动化 fake 性能门使用注入单调时钟和延迟；真实 E2E 通过 HTTP/runtime/provider composition，要求 ≥5 details、≥5 evaluations、渐进结果、feedback、cancel/resume 和 timing 字段。外部条件不足时保持 blocked。

**Rationale**：004 validation 内存在历史 blocked 与后续低计数 PASS 的冲突，且 detail=1/evaluation=2 不满足 005。fake/smoke 可以证明合同，不能证明真实来源性能。

**Alternatives considered**：

- 复用 004 最新文字结论：与当前产物冲突，拒绝。
- 降低真实 E2E 数量门：不能证明用户核心结果，拒绝。
- 在 CI 访问真实 BOSS：登录、市场和账号边界不稳定；真实 E2E 保持受控人工前置、agent 可运行。

## Resolved Unknowns

| Topic | Resolution |
|---|---|
| 默认详情数量 | 15，policy 允许 12–20 |
| 默认来源并发 | 1；真实稳定性通过后最多 2 |
| 详情批次 | 每批最多 5，逐岗位安全事件/checkpoint |
| 岗位间等待 | readiness-driven；3–7 秒受控抖动；最后一项不等 |
| AI 评估粒度 | 1 job × 最多 2 directions / request |
| 画像版本粒度 | 整份 snapshot version + 行级 facts/source links |
| 详情复用 TTL | 默认 12 小时，complete + active + identity match |
| 渐进传输 | 3 秒 HTTP polling + result_revision 完整快照 |
| 最低薪资 | 用户确认的人民币月薪 K floor；无法比较为 unknown |
| 旧运行兼容 | policy v1 adapter；migration 015 nullable/additive |

