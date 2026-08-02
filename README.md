# BOSS直聘爬虫 · 职位抓取工具 v2.3（Chrome CDP / 明文薪资）

> 🌐 English documentation: [README.en.md](./README.en.md)

![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)
![Version](https://img.shields.io/badge/version-2.3.0-orange.svg)

一个轻量的 **BOSS直聘爬虫（spider / crawler / scraper）**：通过 Chrome DevTools Protocol 连接本地已登录的 Chrome，复用真实登录态调用 zhipin.com 搜索 API，绕过前端字体反爬，输出含**明文薪资**的职位数据（JSON / CSV），并生成薪资分布、技能词频和求职材料优化提示词。同时作为 Career Scout Agent Skill 提供。

> 📌 **一句话介绍**：不用 Selenium/Playwright，直接通过 Chrome DevTools Protocol 连接本地已登录的 Chrome，复用真实登录态调搜索 API，输出含明文薪资的 JSON/CSV，并生成薪资分布、技能词频和求职材料优化提示词。

![cover](cover.png)

---

## ⚠️ 免责声明

本项目仅供学习和技术研究参考，旨在探讨 Chrome DevTools Protocol、前端反爬机制与数据采集技术。请勿用于任何违反 [BOSS直聘用户协议](https://www.zhipin.com/about/protocol.html) 或相关法律法规的用途，不得用于商业转售、恶意爬取或对目标网站造成负担的行为。使用本项目所产生的一切后果由使用者自行承担，作者不对任何滥用行为负责。

---

## 🚀 30 秒快速开始

```bash
# 1. 克隆 + 装依赖
git clone https://github.com/czyooutzilas-sketch/career-scout-preview.git
cd career-scout
pip install -r requirements.txt          # 或 uv sync

# 2. 启动隔离 Chrome 并登录（只需一次，登录态持久保存）
python3 scripts/boss_cdp_raw.py --setup-chrome

# 3. 抓取 + 分析
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --analysis

# 支持全国城市（含三四五线），例如：
python3 scripts/boss_cdp_raw.py --keyword "前端" --city 赣州 --pages 3
# 查看支持的城市：--list-cities [关键词]
python3 scripts/boss_cdp_raw.py --list-cities 江

# 4. 抓取后生成聚合摘要 + 提示词（默认读取最新结果）
python3 scripts/job_summary.py
```

抓完直接拿到：薪资分布、经验要求、高频技能词、求职材料优化提示词。CLI 提示词只基于岗位数据，不读取本地简历文件。可选的 AI 求职工作台会解析简历、生成关键词、流式展示岗位卡片并学习反馈偏好，但不会自动投递、联系招聘者或预测录用概率。

**抓取被风控/验证码拦截时**：脚本会当场停止（不跳过、不伪装完成），已抓数据保留在结果文件里，终端醒目提示停在第几页、为什么、建议怎么处理（手动过验证码/歇会儿/重新登录），恢复后可用 `--start-page` 从断点续抓。退出码区分原因：`10`=被风控，`2`=调试浏览器未就绪（会提示先跑 `--setup-chrome`），`1`=其他错误。连续 3 页无数据也会停止并说明（可能是软风控，也可能是搜索条件确实没职位）。

## AI 求职工作台

工作台的抓取任务使用统一的受控执行器：任务具有总超时和可追踪失败码，取消会终止子进程树，日志与产物大小受到限制，产物只能写入任务结果目录。岗位发现只会将具有有效 BOSS HTTPS 详情链接的岗位送入详情抓取、AI 评估和正式结果。

岗位发现开始抓取前会先检查一次 BOSS 专用浏览器：未启动或 CDP 端口不可用时明确提示“专用浏览器未连接”，浏览器已连接但尚未登录时明确提示“尚未登录 BOSS直聘”。预检失败会立即停止，不会对每个搜索词重复执行并最终只显示“未知错误”。

安装依赖后启动：

```bash
python3 webui/app.py
```

浏览器访问 `http://127.0.0.1:5000`。根路径 `/` 是唯一正式前端入口。前端使用 Vue 3 + TypeScript + Vite，顶部在“岗位发现 / 筛选工作台”两个业务模式间切换；两者共享画像选择、浏览器连接状态、AI 设置、提示反馈和岗位列表/详情组件。桌面端使用紧凑列表 + 详情面板，稀疏分类中的岗位卡保持紧凑高度并贴顶排列，不会被列表剩余空间拉伸；窄屏点击岗位后进入全屏详情，AI 设置按钮在移动端仍可直接访问。

仓库包含已构建的 `webui/dist/`，普通使用者无需安装 Node.js。修改前端源码时再运行：

```bash
cd webui
npm install
npm test
npm run build
```

桌面 `start.bat` 启动前会检查 `webui/dist` 与当前后端代码、前端源码的构建状态，任一发生变化就自动执行 `npm run build`，日常启动无需手动构建。

### 核心能力

1. **四步岗位发现**：严格按“上传简历并分析 → 确认关键词/城市并广泛抓取 → 确认六类条件并执行 Stage A 粗筛、抓 JD、Stage B 精筛 → 查看结果”推进。抓取与 AI 筛选是两个独立动作，不会合并为一次不可控的自动执行。
2. **失败不伪装成功**：Stage B 调用失败或漏回判定时，岗位进入“待确认”，不会默认归入“匹配”。求职画像为空时跳过 Stage B 精筛，所有岗位进入“待确认”并标注原因，不触发 AI 调用。AI 筛选请求绑定刚完成的抓取任务 ID，避免另一轮抓取覆盖全局结果后发生串线。AI 筛选可随时点“停止筛选”取消；AI 超时/服务端故障自动退避重试，额度用完立即停并明说，返回被截断时自动拆小批次重跑；抓 JD 途中 BOSS 登录失效会停止并如实报“已抓 X/Y 条”。系统性阻断暂停或服务重启后可从持久化断点继续，已抓 JD 与已筛判定不重复做；用户取消是终态，新任务不会暗中继承被取消任务的断点。
3. **大结果集工作区**：匹配、不匹配、待确认、已筛除直接通过 Tab 展示，不再重复显示顶部统计卡；桌面结果页固定在单个视口内，只让岗位列表和超长详情独立滚动。结果首次只渲染 30 条，按需继续加载，并且只创建一个详情面板。
服务重启导致的 interrupted 任务可通过“重新开始 AI 筛选”继承断点，用户主动取消或结束的任务仍是终态。
刷新恢复时会同步还原上次筛选条件与画像；重启中断任务也可直接“结束并保存结果”。
4. **明确的反馈边界**：岗位发现中的“感兴趣”持久化到当前画像；“不感兴趣”只在本轮内存中生效并可撤销。JD 补抓只更新 JD，不重跑 AI，也不改变原判定。
5. **筛选工作台**：保留七类条件、页数/详情数、简历 AI 建议、运行取消/恢复、符合/不符合/待核验临时区，以及感兴趣/垃圾桶长期区；普通临时结果可按 30 天规则清理。
6. **可暂停、可继续的健康流程**：列表抓取、JD 详情和 AI 筛选遇到验证码、登录过期、来源封禁或 AI 限流时立即暂停，并显示当前阶段、具体原因、成功/失败/未开始/总数；浏览器保持打开，刷新页面或重启服务后仍可从持久化断点继续，已抓岗位、JD 和 AI 判定不会重复。批量 JD 抓取若发生 CDP/WebSocket/会话级异常，会先保存每个受影响岗位的具体失败码和原因，再暂停任务，不再吞错后把整批当成空结果。点击继续时系统先复核阻断是否解除；AI 限流或网络故障会执行最小连接复核，未解除就保持暂停，并发重复点击或自动继承同一暂停断点时只允许一个后台工作取得执行权。AI 精筛每批判定与 checkpoint 原子落库，建任务、保存批次或提交终态失败都会停止而不是以内存结果伪装成功；少量独立失败会持久化为“完成，但有待确认”，服务重启后前端仍能识别该终态。未取得结果的岗位统一进入“待确认”，支持“全部重抓”和单条“补抓 JD”；补救再次遇到阻断时采用同一暂停与继续机制。真实短 JD 根据详情来源与内容性质判断，不依赖固定字数或有限关键词表。历史数据缺少具体失败证据时会明确显示“旧流程未保存具体失败原因”和下一步补抓动作，不猜造分类。页面构建与运行中的后端不一致时，所有写操作都会被拒绝并提示刷新，避免旧服务静默执行新流程。
暂停任务还提供“结束并保存结果”：列表、JD 详情或 AI 筛选任一阶段暂停时，无需继续等待解锁，直接把已完成列表、JD 与 AI 判定重建为 partial 结果快照，原任务结束并关闭专用浏览器，刷新后直接展示本次快照，不会被旧历史覆盖。账号级限流会单独识别为“账号/操作频繁被限流”，不再统一显示成验证码/滑块错误。JD 详情与精筛阶段的成功/失败/未开始/总数按该阶段实际处理的保留岗位统计，列表原始总数单独展示，避免阶段计数与列表总数混算。
服务重启中断的 AI 筛选同样可以直接结束保存；刷新后恢复中断任务时会保留上次筛选条件与画像，旧历史结果不会覆盖中断提示。
7. **高级设置与深度调优实验室**：高级设置使用稳定、平衡、极限、自定义四个入口，覆盖列表抓取、JD 抓取（每批数量、岗位间隔、并发 Tab 数，默认 5）和 AI 筛选速度参数；后端按规范化后的计划页数选择小/中/大内部配置（1-9 小、10-49 中、50-200 大），模式切换不会改变关键词、城市或页数。深度实验要求用户明确填写每个规模的两种代表性结构，使用不可变输入与速度字段配置摘要、独占租约、分阶段轮次、程序测量证据和跨重启恢复；不完整或阻断结果没有应用入口，完整候选只能在用户确认后整体应用，并可整体回退上一版本。当前 2.3.0 交付的是实验与验证框架，未运行正式深度实验，也不预置或宣称最终稳定/平衡/极限参数已经验证。
8. **顶部栏浏览器账号管理**：顶部“浏览器账号”按钮打开管理弹窗，内置 A/B，并可添加自定义账号（名称 1-30 字符且不可重名）。每个账号有独立 profile，可“打开浏览器登录”预存登录态，也可设为下一次任务账号。任务创建时冻结账号，开始、继续、补抓、取消和结束都会激活对应 profile；端口上跑着其他 scraper 账号的 Chrome 时会被替换，未知 Chrome 不会自动关闭。任务运行或暂停期间切换只影响下一次任务。

历史结果恢复时显示真实起止耗时，缺少起止时间的旧数据不再显示伪“用时 0秒”。暂停任务“结束并保存”生成的 partial 快照也会出现在历史结果中，状态显示“完成，但有待确认”。
删除自定义账号时，若该账号的自动化浏览器正在运行会被拒绝；重复使用同一资料目录也会被拒绝。
### AI URL 与 Key 配置

用户只需配置两项：AI 服务 URL 和 API Key。

- 点击顶部“AI 设置”，在语义化对话框中填写 AI 服务 URL（OpenAI 兼容的 `/v1/chat/completions` 端点）和 API Key；移动端入口不会被隐藏。
- 点击“测试连接”会使用内置虚构简历验证传输、JSON 生成和候选人 v3 提取契约；不会读取或发送已保存的真实简历。
- “拉取”和“测试”按钮会显示进行中状态，成功或失败由顶部提示反馈；提示可手动关闭，也会按严重程度自动消失。
- **Key 必须进入系统凭据库**（Windows Credential Manager / macOS Keychain / Linux Secret Service，通过 `keyring` 库），**绝不会明文写入 SQLite、日志、接口响应或导出文件**。
- 接口返回的 AI 设置只包含 URL、状态和最后错误码，不含 Key 或凭据引用。

### 隐私告知

- 简历原文、AI Key 只在本地处理，不上传到任何第三方（除你配置的 AI 服务外）。
- 所有简历读取、AI 设置读取和写操作均需本地会话令牌（`X-Boss-Token`）保护。
- 简历删除会原子地删除原文件、提取文本、内容哈希、文件名和未确认建议。
- 日志、历史、导出和错误信息中不会出现 Key 或简历正文。
- 只有 HTTPS 且属于预期 BOSS 域名（`zhipin.com`）的岗位链接才会被前端打开。

### 画像隔离

- 每份新简历默认创建新的求职画像，**不继承旧画像的 AI 负向偏好**。
- 兴趣反馈绑定当前画像，不感兴趣只影响当前画像的后续结果。
- 复制画像时只复制人工确认字段，AI 偏好不随复制迁移。

### 岗位结果交互

- 桌面端左侧是紧凑岗位列表，右侧只展示当前选中岗位的完整信息；移动端详情为可关闭的全屏层。
- “查看原岗位”只在**新标签页**打开经校验的 HTTPS BOSS 链接，并使用 `noopener noreferrer`。
- 岗位发现中的“感兴趣 / 不感兴趣”不会触发岗位跳转；感兴趣可再次点击撤销，不感兴趣明确标注为“仅本轮有效”。
- 缺少 JD 的岗位可单独补抓；补抓不会重跑 AI，原判定保持不变。
- AI 输出由程序校验，**AI 不能决定任务状态或绕过人工筛选**。

### 不自动投递边界

本项目**不会**实现以下功能：

- 自动投递简历
- 自动联系招聘者或发送消息
- 录用概率预测

AI 只负责 JSON 结构化的简历解析、JD 排序和偏好更新，所有任务状态和投递动作由用户决定。

### 数据保留

| 类型 | 保留期 |
|------|--------|
| 普通结果 | 30 天后自动清理 |
| 感兴趣岗位 | 保留到用户手动删除 |
| 已投递岗位 | 保留到用户手动删除 |

清理操作不会触及简历目录或不受控路径，也不会删除收藏或已投递岗位。

### 状态目录

- 状态数据库：`~/.career-scout/webui/webui.db`
- 岗位结果：`~/.career-scout/job-result/`
- 简历文件：`~/.career-scout/webui/resumes/`

如需让 WebUI 使用指定 Python，可在启动前设置 `BOSS_PYTHON` 环境变量。

### 简历驱动的两层筛选（002）

在 001 工作台基础上叠加的筛选功能，通过两层核验提升结果匹配度。整体流程：

1. **上传简历**：用户上传 TXT / PDF / DOCX 简历（AI 不可用时可跳过）。
2. **AI 读取并给出建议值**：AI 读取简历内容，同时程序获取 BOSS 筛选选项枚举（薪资段、经验、学历、公司规模、融资阶段、行业、城市），AI 判断哪些选项可从简历填入，返回建议值。
3. **用户确认**：前端展示建议值，用户可改可不动；**用户确认值优先，AI 不得覆盖**。某字段 AI 没给且用户没填则留空。
4. **第一层搜索**：用确认后的条件调 BOSS 搜索 API 抓回一批职位，全部进入第二层。城市留空时按全国搜索。
5. **第二层核验**：对每条职位做两层核验——硬规则字段核验 + AI 语义相似度判断。岗位发现流程使用固定四维度结构化评估；合同无效、证据引用无效、置信度不足或 AI 提供方失败时，岗位进入待确认，不使用默认分数。
6. **分流到符合/不符合临时区**：两条核验都过进符合区，任一不过进不符合区。符合区按抓回顺序排列，不使用相似度排序。不符合区混在一起展示，不标注被哪个字段排除。
7. **标记感兴趣/不感兴趣**：在符合区或不符合区任意岗位标记后进持久区。

字段无强制必填（含城市）：用户没选的字段不参与第一层搜索，也不参与第二层硬规则核验。

#### 两层核验

- **第一层**：用确认条件调 BOSS 搜索 API 抓回职位。
- **第二层**：对每条抓回职位做硬规则核验（确定性程序逻辑）+ AI 语义相似度判断（固定四维度结构化合同）。岗位只有在硬规则通过、详情完整、AI 合同有效、置信度和维度门槛满足且证据可追溯时，才可进入高匹配；合同/引用/提供方失败会进入待确认并保留安全失败码，不使用默认分数。

岗位发现评估会保留 `match_score`、`confidence`、证据数量和安全失败码（如 `ai_invalid_output`、`evidence_reference_invalid`、`ai_uncertain`、`ai_network_error`、`snapshot_unavailable`、`hard_rule_unknown`、`experience_level_conflict`），便于区分真实低匹配与评估失败。岗位级别与候选人经历明显冲突时，程序会阻止其进入高匹配或相邻匹配。

#### 区域生命周期

| 区域 | 类型 | 生命周期 |
|------|------|----------|
| 符合区 | 本次执行临时区域 | 开始下一次执行时清空 |
| 不符合区 | 本次执行临时区域 | 开始下一次执行时清空 |
| 感兴趣区 | 持久区域 | 不受区域清空影响，长期保留 |
| 垃圾桶区 | 持久区域 | 不受区域清空影响，长期保留 |

#### 感兴趣区与垃圾桶区

- **感兴趣区**：用户点击"感兴趣"后该岗位进入持久感兴趣区，可长期回看。感兴趣区卡片可点击在浏览器打开对应的 BOSS 原始岗位页面，仅允许 HTTPS 且属于预期 BOSS 域名（`zhipin.com`）。
- **垃圾桶区**：用户点击"不感兴趣"后该岗位进入持久垃圾桶区，可查看曾标记不感兴趣的岗位列表。
- **展示排除**：后续搜索结果在展示时，曾标记不感兴趣的具体岗位被排除，不再展示。排除**仅在展示阶段**，不修改抓取结果；排除**仅按具体岗位识别**，不扩展到同公司或相似特征的其他岗位。

#### AI 不可用降级

AI 服务不可用时，系统提示用户并退化为：

- **跳过简历**：上传简历步骤可跳过。
- **人工填筛**：筛选栏退化为人工填写，字段无必填，留空即不限。
- **仅硬规则核验**：第二层只执行硬规则核验，不执行 AI 语义相似度判断。
- 第一层仍正常用确认条件调 BOSS 搜索 API 抓取。

#### 不自动投递边界

本项目**不会**实现以下功能：

- 不自动投递简历
- 不自动联系招聘者或发送消息
- 不预测录用概率

AI 只负责读简历给筛选项建议（未来负责语义相似度结构化输出），所有任务状态、分流裁定与投递动作由程序逻辑与用户决定，AI 不得决定任务状态或绕过程序判定。

#### 状态目录与测试

- 状态目录：复用 001 的 `~/.career-scout/webui/`（筛选运行 `screening_runs`/`screening_results` 写入同一 `webui.db`，第一层抓取产物写入 `~/.career-scout/job-result/`）。
- 依赖：复用 001 既有依赖，无新增第三方库。
- 自动化测试：`python -m unittest discover -s tests -v`

Windows PowerShell 若希望终端完整显示 emoji，可选设置：

```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
python webui/app.py
```

### 岗位发现 v2 收口（005）

在 004 工作台基础上叠加的确定性收口：通过独立 `discovery_v2` policy、四类进度、渐进结果、取消/恢复、12h 详情复用、来源断路器和分级反馈，把"快速简历驱动岗位推荐"约束在可验证的性能与安全边界内。004 历史运行继续使用 `policy v1`，005 新运行使用 `discovery_v2`，迁移 015 是 additive，不重写 001–014。

#### 默认用户流程

1. **上传简历** → 2. **AI 分析（候选人 v4）+ 方向确认** → 3. **运行进度（四类计数 + 取消/恢复）** → 4. **渐进结果（3 秒轮询，revision-based 不重绘）** → 5. **岗位/方向/判断错误反馈（声明 scope + 可撤销）**。前端根路径 `/` 是唯一正式入口，扫描上传→纠正→确认→首批结果→取消/恢复→反馈全链路。

#### 性能与安全边界（自动化门 SC-001–SC-011）

| 门 | 边界 | 自动化验证位置 |
|---|---|---|
| SC-003 | 15 详情 + 所需评估 ≤ 600 模拟秒 | `tests/test_discovery_performance.py::Sc003DeterministicOrchestrationGateTests` |
| SC-004 | 工作单元完成后 ≤ 10 模拟秒内进度可见；刷新保留计数 | `tests/test_discovery_performance.py::Sc004Sc010Sc011PerformanceGateTests` |
| SC-010 | cancel 后 ≤ 30 wall-clock 秒进入 `cancelled` 终态；已完成 snapshots/assessments/candidates 100% 保留 | 同上 |
| SC-011 | 输入身份一致的 resume 不重复抓 detail、不重复调 AI | 同上 |
| 默认详情并发 | 保持 1（policy 上限 2 需真实小样本稳定性证据后才允许提升） | `webui/source.py` 默认值 |
| 12h 详情复用 | 同一 job 在 12h 内的运行命中复用，不重复抓 | `webui/store.py` 复用守卫 |
| 来源断路器 | 连续失败超阈值时 `source_rate_limited`/`source_verification_required`，阻止后续抓取 | `webui/source.py` breaker |
| 反馈作用域 | `exact_job` / `exact_direction` / `exact_assessment`；可撤销；仅影响后续运行或当前可见性，不改写历史 | `webui/store.py` + `webui/discovery.py::apply_feedback_to_next_run` |

#### 运行命令与兼容说明

```bash
# 启动 BOSS 专用 Chrome（首次）
python3 scripts/boss_cdp_raw.py --setup-chrome

# 启动 Web 工作台
python3 webui/app.py
# 浏览器访问 http://127.0.0.1:5000

# 自动化测试（无需真实 Chrome/网络，全 mock）
python3 -m unittest discover -s tests -v

# 黄金样本评估（SC-003–SC-009 标注一致性，不调真实 AI）
python3 tests/fixtures/discovery/evaluate.py
```

兼容说明：

- 004 历史运行继续使用 `policy v1`；005 新运行使用 `discovery_v2`；两套策略共存，不互相改写历史。
- Migration 015 additive：只新增列与表，不重写 001–014；老库可直接升级。
- 默认详情并发为 1；只有真实小样本稳定性证据通过后才允许 policy 上限 2（不在 feature 005 范围内）。
- 反馈只作用于声明 scope 和后续运行；历史 profile/confirmation/assessment 事实永不改写。

## ✨ 特性

- 明文薪资（API 模式，绕过字体反爬）
- JSON / CSV 双格式输出
- 详情页 JD 抓取 + 技能分析
- 抓取后聚合摘要 + 可复制提示词
- 增量写入（异常退出不丢数据）
- 一键环境检查 + 持久隔离 Chrome CDP profile
- 多维筛选（规模、融资、薪资、经验、学历、行业）
- macOS + Linux 支持；Windows 路径、进程解析和 WebUI 已有自动化测试，真实抓取仍需在本机 Chrome 登录态下验证

<details>
<summary>🔍 为什么不选 Selenium / Playwright 类爬虫？</summary>

- Selenium/Playwright 会启动完整的受控浏览器，体积大、指纹明显，容易触发 BOSS 的风控和验证码。
- 本工具直接连接你已经登录的真实 Chrome（CDP），复用真实指纹和登录态，调用的也是页面内合法的搜索 API，返回的 `salaryDesc` 本就是明文——不需要解析被字体反爬加密的 DOM 薪资。
- 因此比传统 DOM 抓取类爬虫更稳定，也更难被识别为自动化流量。

</details>

## 安装

### 方式 1：克隆到本地再安装（推荐）

由于 `hermes skills install` 的网络请求在某些环境下可能无法直接访问 GitHub，推荐先克隆仓库再本地安装：

```bash
# 1. 克隆仓库
git clone https://github.com/czyooutzilas-sketch/career-scout-preview.git
cd career-scout

# 2. 复制到 Career Scout skills 目录
mkdir -p ~/.hermes/skills/data-science/career-scout/scripts
cp SKILL.md ~/.hermes/skills/data-science/career-scout/
cp scripts/boss_cdp_raw.py ~/.hermes/skills/data-science/career-scout/scripts/
cp scripts/job_summary.py ~/.hermes/skills/data-science/career-scout/scripts/
```

### 方式 2：curl 一键安装

不需要克隆整个仓库，直接下载必要文件：

```bash
mkdir -p ~/.hermes/skills/data-science/career-scout/scripts && \
curl -sL https://raw.githubusercontent.com/czyooutzilas-sketch/career-scout-preview/master/SKILL.md \
  -o ~/.hermes/skills/data-science/career-scout/SKILL.md && \
curl -sL https://raw.githubusercontent.com/czyooutzilas-sketch/career-scout-preview/master/scripts/boss_cdp_raw.py \
  -o ~/.hermes/skills/data-science/career-scout/scripts/boss_cdp_raw.py && \
curl -sL https://raw.githubusercontent.com/czyooutzilas-sketch/career-scout-preview/master/scripts/job_summary.py \
  -o ~/.hermes/skills/data-science/career-scout/scripts/job_summary.py
```

### 方式 3：hermes skills install（需网络直连 GitHub）

```bash
hermes skills install https://raw.githubusercontent.com/czyooutzilas-sketch/career-scout-preview/master/SKILL.md --category data-science
```

> 注意：此方式依赖 hermes 进程能直接访问 GitHub，如果遇到超时或连接失败，请使用方式 1 或 2。

### 验证安装

```bash
# 检查文件是否存在
ls ~/.hermes/skills/data-science/career-scout/SKILL.md
ls ~/.hermes/skills/data-science/career-scout/scripts/boss_cdp_raw.py
ls ~/.hermes/skills/data-science/career-scout/scripts/job_summary.py
```

安装后直接在 Career Scout 对话中说"帮我搜一下 BOSS直聘 上上海的 AI Agent 岗位"。

## 作为命令行工具使用

不想装成 Skill 也可以直接当 CLI 用：

```bash
# 1. 克隆 + 安装依赖
git clone https://github.com/czyooutzilas-sketch/career-scout-preview.git
cd career-scout
pip install -r requirements.txt

# 2. 启动 Chrome CDP
python3 scripts/boss_cdp_raw.py --setup-chrome
# 首次使用也不会复制主 Chrome 登录态；请在弹出的 BOSS 专用浏览器中登录 zhipin.com
# setup 会等待登录完成，并确认接口能返回明文薪资

# 3. 检查环境
python3 scripts/boss_cdp_raw.py --check

# 可选：真实浏览器/API smoke test（不写结果文件）
python3 scripts/boss_cdp_raw.py --smoke-test

# 4. 抓取
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --format csv --analysis

# 5. 抓取后摘要和提示词
python3 scripts/job_summary.py --top 15
```

## 参数

| 参数 | 说明 |
|------|------|
| `--keyword` | 搜索关键词（默认 "AI Agent"） |
| `--city` | 城市（中文或代码，默认上海）。**支持全国城市**（一二三四五线全覆盖，共 300+ 个），运行时自动从 BOSS 同步最新城市码；码表见 [`data/city_codes.json`](data/city_codes.json)，或用 `--list-cities` 查看 |
| `--list-cities [关键词]` | 打印支持的城市列表，可选关键词过滤，如 `--list-cities 江` |
| `--pages` | 页数（上限 10） |
| `--format` | json / csv；csv 会同时导出列表和详情 CSV |
| `--detail` | 抓取详情页 JD（默认开启） |
| `--no-detail` | 不抓取详情页 |
| `--analysis` | 分析报告 |
| `--merge FILE` | 合并已有数据（按 job_id 去重） |
| `--allow-dom-fallback` | API 无数据时允许降级 DOM 提取；默认关闭，薪资可能不可信 |
| `--check` | 环境检查（CDP + 依赖 + 登录态） |
| `--smoke-test` | 用真实 Chrome/CDP 跑一次 BOSS 搜索 API smoke test，不写结果文件 |
| `--setup-chrome` | 一键启动 Chrome CDP（持久隔离 profile） |
| `--copy-login-state` | 手动导入主 Chrome 的 Local State + Cookie 相关文件到隔离 profile（默认、首次启动、重复启动都不复制） |
| `--reset-chrome-profile` | 重建 BOSS 专用 Chrome profile，会清除此专用浏览器内的登录态 |
| `--no-wait-login` | `--setup-chrome` 启动后不等待登录完成 |
| `--login-timeout` | `--setup-chrome` 等待登录完成的秒数（默认 300） |
| `--stop-chrome` | 关闭 BOSS 专用 CDP Chrome（按隔离 profile 精准匹配，不碰主 Chrome） |
| `--close-chrome` | 抓取正常结束后自动关闭专用 Chrome（默认不关；异常退出不触发，保留登录态） |
| `--output` | 列表输出路径（默认 `~/.career-scout/job-result/`） |
| `--detail-output` | 详情输出路径（默认 `~/.career-scout/job-result/`） |
| `--cdp-port` | CDP 端口（默认 9222） |
| `--scale/--salary/--experience/--degree` | 筛选条件 |

## 抓取后摘要与提示词

`scripts/job_summary.py` 只读取已抓取的 `boss_jobs_*.json` 和 `boss_details_*.json`，做简单聚合分析并生成一段可复制提示词。它不读取本地简历文件，不引入 PDF 依赖，也不给个人与岗位做分数判断。

```bash
# 读取默认结果目录下最新的 boss_jobs_*.json，并自动匹配同时间戳或最新详情文件
python3 scripts/job_summary.py

# 指定列表和详情文件
python3 scripts/job_summary.py \
  --input ~/.career-scout/job-result/boss_jobs_20260625_1200.json \
  --details ~/.career-scout/job-result/boss_details_20260625_1200.json \
  --top 15

# 只输出提示词
python3 scripts/job_summary.py --prompt-only
```

打包安装后也可以使用入口命令：

```bash
uv run career-summary --top 15
```

摘要会覆盖这些维度：薪资区间、经验要求、学历要求、地区分布、高频公司、技能标签、JD 高频词。提示词会要求模型基于这些统计去做简历关键词补齐、项目经历改写方向和面试准备清单，但明确要求不要虚构经历。

## 文件结构

```
career-scout/
├── SKILL.md              # Career Scout Skill 定义
├── README.md
├── CHANGELOG.md
├── LICENSE
├── pyproject.toml
├── scripts/
│   ├── boss_cdp_raw.py   # 抓取主脚本
│   └── job_summary.py    # 抓取后摘要 + 提示词
├── webui/
│   ├── app.py            # Flask API + 后台任务编排
│   ├── core.py           # 参数校验 + 可解释匹配
│   ├── store.py          # SQLite 任务/日志/画像/搜索运行/反馈
│   ├── workbench.py      # 关键词选择、去重、预算、卡片投影等纯校验
│   ├── resume.py         # 简历存储、提取、路径校验、原子删除
│   ├── ai.py             # AI 连接测试、凭据库引用、JSON 合同校验
│   ├── index.html        # Vite 开发入口
│   ├── src/              # Vue 3 + TypeScript 组件、视图与测试
│   ├── dist/             # Flask 实际托管的生产构建产物
│   ├── package.json
│   └── vite.config.ts
└── requirements.txt
```

## 工作原理

这是一个基于 Chrome CDP 的 BOSS直聘爬虫，核心流程：

1. 通过 Chrome DevTools Protocol (CDP) 连接到已打开的 Chrome
2. 在 BOSS直聘页面内注入 JS，用同步 XHR 调用搜索 API
3. API 返回明文 `salaryDesc`，绕过前端字体反爬
4. 列表 API 保留 `securityId` / `lid` 等上下文，进入详情页时带上这些参数
5. 每页抓完立即写入文件，按 `job_id` 去重

默认不会使用 DOM 提取列表，因为 DOM 薪资可能受字体反爬影响。只有明确传 `--allow-dom-fallback` 时，API 无数据才会降级 DOM。

详情页只从包含“职位描述”的详情区提取 JD，整页 `body` 仅用于识别登录墙和导航页，不会直接写入结果。若页面出现“登录查看完整内容”，抓取会明确报错并停止，避免把截断正文、招聘者信息、公司介绍和推荐职位当成完整 JD 保存。

`--input ... --analysis --no-detail` 会优先加载 `--detail-output`，其次加载与输入列表同目录、同时间戳的 `boss_details_*.json`，最后查找 `~/.career-scout/job-result` 下最新详情文件。

## Chrome profile 安全策略

`--setup-chrome` 默认使用持久隔离 profile，不软链接、不复制你的主 Chrome 数据。首次启动和后续重复启动都只是创建或复用这个专用 profile：

- `~/.career-scout/chrome-profile`

未显式指定 `--output` 或 `--detail-output` 时，抓取结果默认保存到：

- `~/.career-scout/job-result`

首次使用需要在这个专用 Chrome 中手动登录 BOSS直聘。`--setup-chrome` 会等待登录完成，并用搜索接口确认能拿到明文 `salaryDesc` 后再返回。登录态保存在专用 profile 内，重启机器后仍然保留；重复运行 `--setup-chrome` 不会清空它，也不会影响主 Chrome、Gmail、GitHub 等账号。

WebUI 顶部“浏览器账号”管理内置 A/B，自定义账号的浏览器资料自动创建在项目 `.chrome-profiles/account_<id>` 下。每个账号是完全独立的隔离 profile，首次使用需要在该 Chrome 中单独登录；命令行 `--setup-chrome` 仍固定管理账号 A 的默认 profile。账号切换只关闭已登记 profile 对应的 Chrome；端口被其他 Chrome 占用时不会自动关闭。

如确实需要从主 Chrome 手动导入 BOSS 登录态，可以显式运行：

```bash
python3 scripts/boss_cdp_raw.py --setup-chrome --copy-login-state
```

`--copy-login-state` 每次运行都会覆盖隔离 profile 内对应的 Cookie 相关文件；日常启动不要加这个参数。它只复制 `Local State` 和 `Default/Cookies*`、`Default/Network/Cookies*` 这类 Cookie 数据库相关文件，不复制密码库、历史记录、扩展或完整 profile。需要清空专用浏览器登录态时使用：

```bash
python3 scripts/boss_cdp_raw.py --setup-chrome --reset-chrome-profile
```

### 用完如何收尾

抓取/分析结束后，专用 Chrome 不会自动关闭（默认保留登录态，方便你接着跑下一条抓取）。确认不再使用时，可以手动收尾：

```bash
python3 scripts/boss_cdp_raw.py --stop-chrome
```

`--stop-chrome` 只关闭 scraper 隔离 profile（`--user-data-dir`）对应的 Chrome 进程，**绝不**按端口或进程名去 kill，因此不会误伤你正在用的主 Chrome、Gmail、GitHub 等账号。

如果你希望某次抓取正常结束后就顺手关掉 Chrome，可以加 `--close-chrome`：

```bash
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --close-chrome
```

`--close-chrome` 默认不开启；且只在抓取走完的**成功路径**上触发，登录失败、异常退出等情况不会关闭 Chrome，登录态得以保留。

## 📌 TODO

- [ ] 详情页抓取补强 Referer 与请求指纹，进一步降低风控触发概率

## License

MIT

## 友情链接

- [LINUX DO](https://linux.do/) — 真诚、友善、充满活力的技术社区，本项目认可并推荐。

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=czyooutzilas-sketch/career-scout-preview&type=Date)](https://star-history.com/#czyooutzilas-sketch/career-scout-preview&Date)
