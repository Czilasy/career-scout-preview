# Changelog

## 未发布

### 重构 — Vue 3 求职工作台
- 将 200KB 级内联 `webui/index.html` 重构为 Vue 3 + TypeScript + Vite；Flask 根路径托管带哈希的 `webui/dist`，源码拆分为共享外壳、语义化对话框、四步智能选岗、高级筛选和岗位列表/详情组件。
- 保留现有业务顺序：上传简历并提取条件 → 用户确认关键词/城市 → 广泛抓取 → 用户确认六类筛选条件 → Stage A 字段排除 → 抓取 JD → Stage B 四维严格评估 → 查看优先投递/可以考虑/待确认/不推荐/已筛除结果；抓取与 AI 筛选仍是两个独立动作，没有增加第三次筛选。
- Stage B 改用方向、技能、经验、行业四维结构化合同，并由程序校验总匹配分、置信度和维度门槛；严格通过才进入“优先投递”，AI 建议匹配但未通过严格门槛进入“可以考虑”，失败或合同无效进入“待确认”。结果详情展示安全的分数与确定性推荐依据，不持久化 AI 原始理由。
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
