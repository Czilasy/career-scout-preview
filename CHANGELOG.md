# Changelog

## 未发布

### 修复 — AI 续跑误判与错误完成快照
- 续跑时只继承精筛判定（match/not_match/mismatch/uncertain），粗筛 kept 不再被当成已精筛，会重新进入精筛。
- 续跑时重建粗筛 dropped 明细，不再丢失已移除岗位。
- 存在待确认岗位时结果快照状态改为 partial，不再出现“已完成”与“全部待确认”并存的矛盾画面。
- 补抓/单条 JD 回写也能定位 partial 快照，不再只认 done。
- 暂停继续时清空旧的 error_code/error_reason，避免刷新后被上一次限流原因误拦。

### 修复 — 服务重启中断任务可继续与账号管理边界
- 服务重启标记的 interrupted（error_code=restart）任务可通过“重新开始 AI 筛选”继承已保存断点；用户主动取消或结束的任务仍为终态。
- /api/latest-running-task 对重启中断任务返回 interrupted 与可续跑标记，前端能正确提示而不是误当成后台运行。
- 自定义账号删除前检查该账号浏览器是否正在运行，占用时拒绝删除，避免 CDP 端口死锁。
- 自定义账号写入/删除加锁并使用唯一临时文件，避免并发写坏 browser_accounts.json；重复资料目录被拒绝。

### 新增 — 顶部栏浏览器账号管理
- 顶部栏新增“浏览器账号”按钮，账号管理从高级执行设置移出；可查看当前账号、设为下一次任务账号、打开对应自动化浏览器登录。
- 支持添加自定义浏览器账号：名称 1-30 字符且不可重名，浏览器资料自动创建在项目 `.chrome-profiles/account_<id>` 下；自定义账号可删除，内置 A/B 不可删除。
- 账号配置持久化到本地 `browser_accounts.json`，任务创建仍冻结账号，运行/暂停期间打开其他账号浏览器会被拒绝。

### 修复 — 审查回归：结束保存与账号切换边界
- 列表抓取阶段暂停的任务现在也能“结束并保存结果”，不再因缺少岗位快照返回 409。
- 用户主动结束的任务不再被刷新恢复逻辑误判为服务重启或后台运行，partial 结果可正常加载。
- 列表抓取限流分类改为限流优先，避免“频繁 + 滑块”文案继续显示成验证码。
- 账号切换只关闭 A/B 已知 profile 的 Chrome；未知 Chrome 占用 CDP 端口时提示并拒绝自动关闭，避免误伤主浏览器。
- partial 快照将历史 mismatch 归一为 not_match，避免待确认计数膨胀。

### 新增 — 暂停任务结束并保存部分结果
- 暂停中的列表/JD/AI 任务可点“结束并保存结果”，把已完成列表、JD 与 AI 判定重建为 partial 快照，原任务结束并关闭专用浏览器；刷新与历史恢复仍可查看“完成，但有待确认”。
- 修复刷新页面时旧历史结果覆盖暂停任务的问题：页面挂载先恢复运行/暂停任务，再读取历史结果。

### 新增 — 双浏览器账号 A/B
- 高级执行设置新增浏览器账号 A/B：A 使用默认 `~/.career-scout/chrome-profile`，B 使用项目 `.chrome-profiles/account_b`。
- 任务创建时冻结账号，开始、继续、补抓、取消和结束都会激活对应 profile；CDP 端口上跑错账号的 Chrome 会被替换。任务运行或暂停期间锁定切换。

### 修复 — 账号限流分类与 JD 阶段计数
- 详情来源先识别“限流/频繁/解锁/冻结”等账号级限流文案，再识别验证码/滑块，不再把账号限流统一显示成验证码错误。
- JD 详情/精筛阶段的成功/失败/未开始/总数按该阶段保留岗位统计，列表原始总数单独返回，避免阶段计数与列表总数混算。

### 修复 — 高级执行设置字段换行
- “每组合翻页数”超过 10 时的警告提示并入字段右侧 `?` 提示，不再以行内文字撑宽布局，避免“组合间延迟”被挤到第二行。

### 变更 — 计划总页数上限与任务分档
- 计划总页数上限从 30 调整为 200，任务分档改为 1-9 小、10-49 中、50-200 大；5 个关键词 × 10 页等组合现在可以正常创建。

### 新增 — JD 并发 Tab 数
- 高级设置新增“并发 Tab 数”（1-10，默认 5），通过执行配置快照、详情抓取适配器和 `--tab-pool-size` 全链路生效；旧 9 字段配置自动补默认值。
- 执行配置 schema 由九字段扩展为速度字段集合，摘要与持久化兼容旧配置。
- 同步底层适配器与抓取 CLI 的并发 tab 默认值为 5，并修复详情批量命令未接收/未透传 `tab_pool_size` 的缺陷。

### 优化 — 启动自动同步前后端版本
- 桌面 `start.bat` 启动前调用 `webui/ensure_frontend_sync.py`，检测到后端代码或前端源码与 `webui/dist` 构建状态不一致时自动执行 `npm run build`，避免“页面版本与当前后端不一致”需要手动构建。

### 修复 — WebUI 搜索范围展示
- “哪些词用于广泛抓取”中关键词与城市合并为“关键词 × 城市”单一区域，关键词与城市方块样式保持不变。
- 两个自定义输入框标签改为“关键词”“城市”；移除“全国（与具体城市互斥）”复选框，城市留空即按全国搜索，并用占位文字“不输入则不指定城市”提示。
- 高级执行设置不再展示“小任务”等任务大小前端标识。
- 调整搜索范围区域与“求职画像”之间的垂直间距，避免上下内容贴在一起。


### 修复 — 历史结果真实耗时
- `result_snapshot` 落库真实起止时间；历史结果恢复时前端只显示真实“用时”，缺少起止时间的旧数据不再显示伪“用时 0秒”。

### 修复 — 已结束任务错误锁定搜索范围
- WebUI 只在抓取、AI 筛选正在执行或任务暂停待继续时锁定关键词、城市、求职画像和翻页数；完成、失败、取消任务以及仅恢复历史结果时重新允许编辑，避免“开始抓取”尚未触发却无法创建新搜索范围。

### 修复 — SPEC011 第二阶段真实实验诊断与证据门禁
- 真实端到端轮次在详情来源阻断时保留实际输入规模和缺失计数，不再生成误导性的 `0/0` 完成证据；`captcha_required` 与来源验证信号会立即停止后续详情批次，并保留现场用于 blocked 报告。
- executor report 现在校验必需文件或目录的真实 SHA-256，拒绝目录摘要占位值；零输入 completed 报告沿完整接收链进入 invalid，避免无工作轮次被确认。
- tuning AI 的网络、超时、限流和服务端重试严格服从 manifest 的命名预算，`max_retries=N` 最多执行 `1+N` 次请求；显式来源硬错误不会被历史 AI 请求错误覆盖。

### 新增 — SPEC011 高级设置深度自动调优第一阶段
- 新增速度字段执行配置 schema（含 JD 并发 Tab 数）、规范摘要与不可变任务快照；后端权威校验关键词、城市别名、全国互斥、1–30 计划页和小/中/大规模，模式与自定义设置不再覆盖页数或运行中范围。
- 新增 SQLite 模式版本、最近自定义、实验输入/工作负载、质量参考、候选、轮次、任务单、报告、测量事件和独占租约；支持整版应用/回退、跨重启不确定轮次恢复、程序证据守恒与严格 executor manifest/report 门禁。
- 新增列表、详情、粗筛、精筛、端到端五类轮次 adapter，以及动态步长、漏斗晋级、危险边界、有限重试、硬停止和剩余时间预测；本次仅交付确定性代码实验室，未运行正式深度实验、未生成或应用最终三档参数。
- WebUI 将旧“保守/普通/激进”前端预设替换为稳定/平衡/极限/自定义；显示后端规范范围与任务规模，任务开始后锁定范围，并提供六结构实验创建、进度、阻断、证据、跨刷新恢复、完整应用与上一整版回退。
- 版本同步升级到 2.3.0。

### 新增 — 失败策略：抓取风控哨兵 + AI 调用错误处理 + 筛选可取消/可续跑
- **CLI 抓取风控哨兵**（`scripts/boss_cdp_raw.py`）：列表抓取命中风控/验证码当场停止（不再当空页跳过、不再伪装完成）——识别 HTTP 错误码（401/403/412/418/429）、验证码特征词（"安全验证""滑动验证"等）、200 但返回非 JSON/结构对不上、连续 3 页无数据熔断。停止时已抓数据保留（每页本就会落盘），终端醒目报错说明第几页挂的、为什么、已存多少条、建议怎么处理，并提示可用 `--start-page` 续抓。退出码区分：风控 `10`、调试浏览器未就绪 `2`、通用错误 `1`。Chrome 没开/端口不通从裸栈 trace 改为明确提示"请先运行 --setup-chrome"。
- **AI 调用错误处理**（`webui/ai.py`）：超时/连接错误/500/502/503/504 纳入退避重试（429 退避 5/15/30s 不变，5xx 用 2/5/10s，超时/网络用 3/8/20s，退避等待累计不超单次 timeout）；429 响应体识别配额耗尽特征（insufficient_quota）立即停并报"额度已用完"（错误码 `quota_exhausted`），不再空撞；AI 返回被截断单独识别（`finish_reason=length` 或 JSON 尾部不闭合，错误码 `truncated`），粗筛/精筛自动拆半重跑到单条为止；5xx 耗尽后报 `server_error`（区别于"返回无效"）。401/403 与返回格式错行为不变。
- **网页 AI 筛选可取消**：筛选按钮运行中变为"停止筛选"，后端在粗筛/抓 JD/精筛各阶段边界响应取消（新增 `POST /api/ai-screen/<task_id>/cancel`），取消不会把结果覆盖成完成。
- **登录墙接通**：抓 JD 途中 BOSS 登录失效（`source_login_required`）时立即停止后续批次，任务如实报"登录已失效：已抓 X/Y 条"，不再继续抓空气还显示完成。
- **断点续跑**：AI 筛选任务进度落库（启用既有 `screening_runs`/`screening_results` 表）+ 已抓 JD 分段落盘；任务失败/取消/服务重启后，同一抓取任务用同一条件再次筛选时自动接着上次进度（已抓 JD、已筛判定不重复做）；进程重启后首页如实提示"上次筛选被中断"而不是假装还在跑。
- **全流程暂停与继续**：列表抓取、JD 详情、AI 粗筛/精筛和待确认重抓统一识别系统性阻断，暂停时保留浏览器和持久化断点，页面持续显示阶段、具体原因及成功/失败/未开始/总数；继续前先复核阻断是否解除，未解除保持暂停，已解除才从原任务断点恢复，已完成工作零重复。三类任务统一从 `/api/task-state` 获取状态，由后端合并持久化计数与实时进度、日志和结果；运行中与暂停中的取消也统一走 `/api/task/cancel`，避免刷新前后出现两套状态源。
- **健康流程审查收口**：统一继续接口的 AI 分支增加原子执行权，真实并发请求只提交一次后台工作；`screening_runs` 状态校验与迁移合并到 `BEGIN IMMEDIATE` 事务，取消与成功终态不能并发覆盖，自动继承同一 paused 断点也通过条件更新只允许一个请求取得 claim。AI 主流程必须先建立持久化 run，精筛 verdict 与 checkpoint 同事务保存，批次或终态写库失败立即停止；独立失败写入准确 pending 计数并以 `partial/completed_with_pending` 结束，前端兼容 legacy `done` 和两个 canonical 完成态。JD 批调用的 CDP/WebSocket/会话异常不再被吞成空结果，而是先把每岗失败码与可读原因写入 pending/event 再暂停，`source_cdp_unavailable` 同样立即 hard-stop。AI 限流/网络暂停在继续前执行连接复核；取消、失败和中断均为终态，旧继续路由与新 AI 任务不会复活其断点。短 JD 删除 120 字及有限词表兜底门槛，改由详情来源和内容性质排除导航/占位壳。历史恢复的幂等返回也会重新绑定 committed audit 并复核 manifest/备份 SHA-256；元数据补正只接受具有 50/50/762/646 精确 action 统计、且 recovery key、committed manifest 与备份完整对应的正式恢复 audit，并在补正审计中记录父 recovery key 与 backup id。恢复事务只捕获已知运行错误，未知编程错误在回滚后向上抛。抓取契约测试的 12 个详情产物全部写入临时目录，不再污染用户 `job-result`；SC-015 脚本默认只验收隔离 5050，并在 375/768/1440 三种视口对横向溢出、元素样式/边界/遮挡、暂停原因、继续操作和待确认原因执行硬断言。
- **最终复审加固**：暂停继续统一沿用原 task/run 身份，不再引入非 canonical `resumed` 状态；AI 粗筛保存真实已处理数，JD/AI/补抓的暂停、checkpoint、verdict、pending 删除和终态写入失败均阻断推进。JD 断点缺失与损坏被明确区分，损坏或落盘失败不再退化为空进度后重复抓取；重启任务状态读取失败返回明确 503，取消写库失败不会先把内存发布为 `cancelled`。SC-015 验收脚本改用项目已声明的 `websocket-client`，恢复与验收公开入口补齐 docstring。
- **最终窄修复**：单条补抓后台提交失败会同步写入失败状态和事件，不再留下 DB `running` / 页面 `queued` 分裂；主列表与主 AI JD 阶段的 Chrome/CDP 不可用统一持久化为可继续暂停并保存断点；补抓缺少 AI 配置时保持 pending 并暂停，不再伪装成功；状态机拒绝未开始任务直接进入 paused。
- **Spec 010 验收收口**：退役对无活动写入者的隔离数据库执行 24 小时静态轮询，改由 6 项确定性持久化、真实重启、刷新恢复和断点继续测试验收；既有多轮独立审查后的最终窄修复通过全仓回归，并显式记录不再发起第 6 次低收益全量审查的决策边界。
- **待确认补救**：无结果岗位保留具体失败原因并统一进入“待确认”，支持全部重抓和单条补抓；重抓途中再次遇到验证码、登录过期或 AI 限流时仍暂停并可继续。历史异常数据提供独立备份、指纹校验、原子维护锁、事务恢复和审计能力；恢复后岗位集合、JD、pending 和分类守恒在提交前再次校验，提交后的诊断失败只告警、不篡改已提交事实。历史证据不足的 646 条明确标记 `historical_reason_unavailable` 并给出补抓动作，不猜造 30/8/608。HTTP 恢复必须先 `prepare` 取得服务端 `backup_id`，再执行事务恢复，正式恢复不会自动执行。
- **构建身份门禁**：前端构建产物内嵌预期后端哈希，启动后与 `/api/version` 和 `/api/session` 返回值核对；不一致时前端阻止写请求，后端也对全部状态变更请求返回明确的版本冲突，避免旧 Flask 进程静默执行新页面逻辑。
- **资源生命周期收口**：`ScraperExecutor` 在读取线程退出时关闭子进程输出管道，Chrome 启动后关闭父进程持有的 stderr 日志句柄；测试 profile 使用单一临时目录生命周期。全仓测试可在 `ResourceWarning` 升级为错误时通过。

### 优化 — 求职画像为空时跳过精筛 + tooltip 浮层 + 城市输入交互
- 求职画像（profile_summary）为空时跳过 Stage B（AI 精筛），所有岗位标记 `uncertain`（"未填写求职画像，跳过 AI 精筛"），避免空画像触发精筛报错；前端求职画像字段同步加"未填写将跳过精筛"提示。
- 工作台高级设置的 `?` 提示从浏览器原生 `title` 改为自定义 `::after` 浮层（`data-tip` 属性驱动），样式可控、定位更稳定。
- 城市输入框增加"添加"按钮和 `confirmCities` 确认逻辑，避免误触发；搜索估算行移到关键词区下方。

### 优化 — 第 2 波性能与并发修复
- 前端 `pollTask` 轮询失败采用指数退避（4s→8s→16s→32s→64s，7 次后停止并标记 failed），避免网络异常时无限重试，提升异常场景下的用户体验。
- `ai_settings_models` 接口在 `AISecurityError` 时返回 502 状态码（原误返回 200），修正 HTTP 语义，前端通过 `response.ok` 判断不受影响。
- `append_log` / `create_analysis` / `create_confirmation` 三处并发写入点加 `BEGIN IMMEDIATE` 事务包裹，消除并发 `MAX(seq)+1` 竞态冲突。
- `save_job` 改用 `INSERT ... ON CONFLICT(canonical_url) DO UPDATE ... RETURNING id` UPSERT，解决并发同 URL 写入冲突。
- 新增 `list_jobs_by_ids` 批量查询方法，消除 `list_analyses` / `search_run_jobs` / `latest_pipeline_result` 的 N+1 查询。
- 新增 migration 016 三个索引：`idx_jobs_expires_at`（partial）、`idx_jobs_last_seen_at`、`idx_discovery_job_snapshots_run_status`（复合），提升清理任务与发现查询性能。
- `cleanup_expired_jobs` 从 Python 循环逐行 UPDATE 改为单条 SQL 批量更新，减少锁竞争与往返开销。
- 新增 `tests/test_concurrency.py`（并发红测试）与 `tests/test_indexes.py`（`EXPLAIN QUERY PLAN` 索引命中验证），回归覆盖以上改动。

### 重构 — Vue 3 求职工作台
- 将 200KB 级内联 `webui/index.html` 重构为 Vue 3 + TypeScript + Vite；Flask 根路径托管带哈希的 `webui/dist`，源码拆分为共享外壳、语义化对话框、四步岗位发现、筛选工作台和岗位列表/详情组件。
- 保留现有业务顺序：上传简历并提取条件 → 用户确认关键词/城市 → 广泛抓取 → 用户确认六类筛选条件 → Stage A 粗筛 → 抓取 JD → Stage B 精筛 → 查看匹配/不匹配/待确认/已筛除结果；抓取与 AI 筛选仍是两个独立动作。
- 结果区改为紧凑列表 + 单详情面板，移除与分类 Tab 重复的顶部统计卡；桌面结果页固定为单视图，只让岗位列表和超长详情独立滚动，首次只渲染 30 条并按需加载；375px 窄屏继续使用全屏详情。AI 设置入口、语义化控件、焦点锁定、Escape、44px 触控目标、通知自动关闭、BOSS 外链校验和 reduced-motion 均有显式契约。
- 修复结果分类只有少量岗位时 CSS Grid 把卡片平均拉满列表高度的问题；岗位行现在保持紧凑基准高度并从顶部连续排列，文字放大时仍可自然增高。
- 修复 Stage B AI 失败默认判为匹配的问题，失败或缺失判定改为“待确认”；恢复置信度门控的简历筛选建议及旧路由依赖的简历解析兼容函数，低置信度继续留空且不覆盖用户确认值。
- 修复 pipeline 感兴趣撤销把外部 BOSS ID 当内部 UUID 的问题；AI 筛选改为绑定并读取明确的已完成抓取任务，降低多轮任务结果串线风险。
- 新增 Vitest 组件/行为测试、Vite 类型检查与生产构建门；Python 前端契约改为检查 Vue 源码和构建产物，不再依赖已删除的内联函数名。

### 变更 — 候选人分析协议从 v4 降回 v3
- 移除 `webui/ai.py` 的 `_analyze_v4` / `_build_analyze_v4_messages` 方法与 v4 分支；`webui/discovery.py` 的 v4 持久化路径（facts / evidence_refs / profile_version 整段）；`webui/candidate.py` 的 `normalize_candidate_analysis_v4` 函数、`_safe_fact_value`、v4 常量与注释；前端 `index.html` 的 `contract_version` 改回 `"v3"`。
- v4 的事实关联证据结构对 AI 负担过重、分析失败率高、接不住，属于过度设计；v3 扁平化管理已够用。
- 保留不动：DB 历史 v4 分析记录、migration 015 的 candidate_profile_versions / candidate_fact_items / candidate_fact_evidence 表结构（v3 不写入，留空）、job_assessment_v2 协议、manual_v1 手动分析路径。
- 测试：删除 v4 专属测试类与方法（`CandidateAnalysisV4Tests` / `CandidateAnalysisV4ProviderTests` / v4 持久化与 correction chain 测试），`CandidateProfileConfirmationAcceptanceTests` 改用 manual 路径造数，store / HTTP 契约测试的 setUp `contract_version` 由 `"v4"` 改 `"v3"`；删除 fixture `tests/fixtures/discovery/ai_candidate_v4.json`。608 项测试全绿。

### 新增 — 岗位发现 v2 收口（feature 005）
- 独立 `discovery_v2` policy：005 新运行使用 `discovery_v2`，004 历史运行继续使用 `policy v1`；迁移 015 additive（只新增列与表，不重写 001–014），老库可直接升级。
- 四类进度计数：`search_queries_completed` / `list_candidates` / `details_selected` / `details_completed` / `assessments_completed` / `recommendations`；工作单元完成后 ≤10 模拟秒可见，刷新保留计数（SC-004）。
- 渐进结果：3 秒轮询，revision-based 不重绘；非终态结果可见；卡片稳定身份；消失原因可解释。
- 取消/恢复：cancel ≤30 wall-clock 秒进入 `cancelled` 终态，已完成 snapshots/assessments/candidates 100% 保留（SC-010）；输入身份一致的 resume 0 重复 detail 抓取、0 重复 AI 调用（SC-011）。
- 12h 详情复用：同一 job 在 12h 内的运行命中复用，不重复抓；freshness/identity 守卫防止误用陈旧快照。
- 来源断路器：连续失败超阈值时 `source_rate_limited` / `source_verification_required`，阻止后续抓取并保留已完成结果。
- 分级反馈：`exact_job` / `exact_direction` / `exact_assessment` 三类作用域；可撤销；仅影响后续运行或当前可见性，不改写历史 profile/confirmation/assessment 事实（FR-050/FR-051）。
- 确定性性能合同：`DiscoveryPerformanceMetrics` + `FakeMonotonicClock` 注入式时钟；SC-003 编排门（15 详情 + 所需评估 ≤600 模拟秒）、SC-004/010/011 自动化门全部通过。
- 候选人分析升级到 v4：后端拥有 typed-empty shape、quarantine 无效 evidence、unverified 搜索字段永不成为 confirmed 约束。
- 岗位评估升级到 v2：固定四维度结构化合同、单方向降级、评估分组、证据范围绑定。
- 三态薪资硬规则（月薪 K 数值下限）：unknown 不参与硬规则，confirmed 必须满足，inferred 不参与硬规则。
- 黄金样本评估：`tests/fixtures/discovery/evaluate.py` 验证 SC-003–SC-009 标注一致性（不调真实 AI）。

### 新增
- 新增 `--stop-chrome` 命令：抓取/分析完成后关闭 BOSS 专用 CDP Chrome（按 user-data-dir 精准匹配隔离 profile，不碰主 Chrome）；抓取命令新增 `--close-chrome` 选项，正常结束后自动收尾（默认关闭，异常退出不触发以保留登录态）。复用已有 `stop_cdp_chrome` 的安全匹配逻辑，补齐进程关闭/收尾链路的单元测试。（#26）
- 城市码表外置为 `data/city_codes.json`（全量 300+ 城市，覆盖一二三四五线），新增 `--list-cities [关键词]` 命令查看支持的城市；`resolve_city` 查询链改为「本地静态码表 → 运行时拉 BOSS 接口 → 原样兜底」。城市码表打进 wheel，`pip install` 用户也可用。（#24）

### 修复
- 候选人分析 v3 契约与岗位评估 v1 契约的 evidence 引用边界对齐：当 AI 返回空 evidence 或所有 evidence 被静默丢弃但有可确认 direction 时，`normalize_candidate_analysis` 主动降级 `manual_required` 并加 `missing_required:evidence` warning，`analyze_resume` 抛 `AISecurityError("ai_invalid_output")`，避免下游 `allowed_candidate_refs=∅` 导致所有评估被 `evidence_reference_invalid` 全量降级为 `needs_review`；T132 smoke 的 `v2_ok` 判定增加 `len(evidence) > 0` 检查关闭测试盲区。真实 BOSS E2E 验证：`failure_code` 从 `evidence_reference_invalid` 变为 `null`，`evidence_count` 从 0 变为 6。

- 移除已落后于正式工作台的 `index-v2.html` 备用页及 `/v2` 路由，并明确根路径 `/` 是唯一正式前端入口，避免版本命名误导
- 岗位发现开始抓取前增加一次 BOSS 专用浏览器连接与登录预检；分别保存并展示“专用浏览器未连接”和“尚未登录 BOSS直聘”，预检失败立即终止，避免逐搜索词重复失败后只显示“未知错误”
- 岗位发现的手动方向区域增加明确字段标签、搜索词实时计数和有效状态反馈；兼容“方向，搜索词”逗号简写并自动拆分，有效输入会清除旧警告，同时将未知项并入同一补全区域并优化桌面/窄屏间距
- 候选人分析升级为 `candidate_analysis_v3` 适配链：统一规范化完整、部分和需人工补充三种质量状态，隔离无效敏感证据，原子领取后台任务，并只把用户确认的搜索字段及受限页数/详情数交给抓取器
- 候选人分析页面现在展示安全的质量提示、缺失信息和人工求职方向补充入口；AI 连接测试改用内置虚构简历验证真实候选人提取能力，不读取或发送已保存的真实简历
- AI 模型“拉取”和连接“测试”按钮增加进行中、绿色成功及红色失败重试状态；全局提示按严重程度自动消失，顶层入口改名为“岗位发现 / 搜索工作台 / 筛选工作台”
- 未勾选 AI 同意时，简历上传现在明确停在“上传成功（未分析）”，不再创建永远停留在 `queued` 的分析并最终误报超时
- 简历“上传并分析”按钮现在原位展示分析中、绿色成功和红色失败重试状态，重新选择文件时恢复初始状态，不再在请求结束后无条件抹掉结果反馈
- 岗位发现评估现在保留语义合同、证据引用和 AI 提供方的安全失败码；提示词与程序门禁共同约束合法评估档位、证据 ID 及经验级别冲突，避免把实习/校招岗位仅因关键词重合误判为高匹配
- 真实 BOSS E2E 前置检查增加分阶段超时、状态日志和管道行缓冲，登录探测或 keyring 阻塞时不再表现为无限挂起
- Web UI 抓取子进程统一增加总超时、取消后的进程树终止、输出上限和受控产物路径检查；SQLite 启用 WAL 与 busy timeout，降低后台任务锁库风险
- 岗位发现仅允许具有有效 BOSS HTTPS 详情链接的岗位进入长期岗位库、详情抓取和 AI 评估；未知岗位反馈不再创建 `boss://` / `discovery://` 伪来源
- 详情页 JD 改为只提取“职位描述”区，并在登录墙、导航页或过短正文出现时拒绝写入，不再把整页 `body`、招聘者信息、公司介绍和推荐职位当作 JD
- 同步 BOSS 当前 `city.json` / `condition.json` 映射，修正城市码以及薪资、经验、学历筛选枚举漂移，并在内置城市表未命中时自动加载 BOSS `cityGroup.json` 支持更多城市中文名
- `scrape_details` 最终保存改用 `os.path.dirname(path) or "."`，`--detail-output` 传不带目录的裸文件名时不再抛 `FileNotFoundError`（与循环内及其它写文件处保持一致）
- 修正城市码：天津 `101030100`、沈阳 `101070100`（原均误用 `101060100`）
- `require_runtime_dependencies` 缺失依赖时同时提示 uv 和 pip 安装方式
- `--merge` 现在会合并旧详情并落盘到 `--detail-output`（之前只合并列表，详情丢失）
- API URL filter 改用 `urlencode`（原字符串拼接，filter 值含特殊字符会出错）

### 变更
- 平台支持声明改为 macOS + Linux（Windows 代码分支保留但未经实测，不再声称支持，避免过度承诺）
- `pyproject.toml` 删除空的 `[csv]` extra（csv 是标准库）
- SKILL.md 脚本路径解析改用 Python `os.path.realpath`（macOS 自带 `readlink` 无 `-f`）

### 新增
- `scripts/job_summary.py` 抓取后摘要脚本：读取已有 JSON，输出岗位聚合摘要和求职材料优化提示词
- `career-summary` 命令行入口，便于打包安装后直接运行摘要脚本
- 抓取后摘要测试：覆盖 JSON 加载、聚合维度、提示词输出和项目边界
- 版本号一致性测试：校验脚本、pyproject.toml、SKILL.md、README.md 四处版本同步
- CONTRIBUTING.md 贡献指南

## v2.0.0 (2026-06)

### 新功能
- `--check` 环境检查（CDP 连通性、依赖、登录态）
- `--setup-chrome` 一键启动 Chrome CDP（持久隔离 profile）
- `--copy-login-state` 手动导入主 Chrome 的 Local State + Cookie 相关文件到隔离 profile
- `--reset-chrome-profile` 重建 BOSS 专用 Chrome profile
- `--setup-chrome` 默认等待 BOSS 登录完成，并确认接口返回明文薪资
- `--no-wait-login` / `--login-timeout` 控制 setup 登录等待
- 默认抓取结果保存到 `~/.career-scout/job-result`
- 未传 `--city` 时默认搜索上海
- `--format csv` 同时导出列表 CSV 和详情 CSV
- `--merge` 合并多次抓取结果（去重）
- `--cdp-port` 自定义 CDP 端口（默认 9222）
- `--smoke-test` 用真实 Chrome/CDP 跑一次搜索 API smoke test，不写结果文件
- `--allow-dom-fallback` 显式允许 API 失败时降级 DOM 提取
- `--version` 查看版本号
- 登录态检测：未登录时给出明确提示
- 分析报告技术词动态提取（不再硬编码）
- 进度显示：`[2/3 页, 45/90 条]`

### 改进
- CDP WebSocket 消息过滤 + 超时重试（不再无限卡死）
- 详情页写入去重（中断重跑不重复）
- 请求频率保护（最多 10 页，全局 500 次上限）
- 清除所有 bare except，改为具体异常类型
- API 路径提取为常量，方便维护
- DOM fallback 标记为 deprecated
- DOM fallback 默认关闭，避免把字体反爬后的薪资写进结果
- API 错误行不再被当成职位数据处理
- 详情输出保留 `job_id`、`job_link` 和 `salary_source`
- 详情页访问会带上列表 API 返回的 `securityId` / `lid` 上下文
- `--input ... --analysis --no-detail` 会从 `--detail-output`、同目录同时间戳详情文件、默认结果目录最新详情文件中加载详情
- 登录态检测改为多关键词、多城市 probe，但仍要求接口返回明文薪资
- Linux / Windows 平台支持（Chrome 路径 + 隔离 profile）
- pyproject.toml 版本锁定依赖

### 安全
- 默认不软链接、不复制主 Chrome profile；首次启动也不自动导入主 Chrome 登录态，避免影响 Gmail/GitHub 等主浏览器登录态
- API URL 可配置（`API_JOB_LIST_PATH` 常量）

## v1.0.0 (2026-06)

### 初始版本
- Chrome CDP 抓取 BOSS直聘职位列表
- API 明文薪资（绕过字体反爬）
- 详情页 JD 抓取 + 技能标签提取
- 增量写入（异常退出不丢数据）
- 分析报告（薪资分布、经验要求、简历建议）
- 多维筛选（规模、融资、薪资、经验、学历、行业）
