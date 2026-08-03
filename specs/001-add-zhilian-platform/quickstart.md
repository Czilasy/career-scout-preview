# 验收指南：智联招聘完整链路

**目的**：基于最终实现证明智联单平台主链可用、平台阻断可恢复、数据身份可信、BOSS 行为不回归。  
**当前状态**：这是实施后的验收合同；tasks 前阶段只审查其完整性，不执行真实抓取。

## 验收边界

真实主链固定使用一个关键词、一个有结果的规范城市和一页列表。允许写入独立本地测试数据库、忽略目录内的任务 artifact 和平台专属 Chrome profile；禁止自动投递、沟通、绕过验证或对招聘平台执行任何求职写操作。

成功必须同时满足：

- 平台 AI schema、城市目录和提交字段正确；
- 搜索请求只含关键词、规范城市和页数，AI 请求才含筛选快照；
- 智联列表岗位与页面一致，真实空结果按搜索组合持久化明确证据，刷新和重启后仍可审计；
- 有结果时至少一个可访问岗位取得真实 JD；
- AI 粗筛、精筛和结果分组完成；
- 任务、结果和岗位都标识 `platform=zhilian`，平台 ID 与内部 UUID 不混用；
- 登录/验证/限流/封禁/CDP 阻断进入 paused，网络与结构错误按固定矩阵失败；
- 阻断解除后从原平台断点继续，不重复已完成工作；
- 存量 BOSS 数据与主流程回归通过；
- 进度、取消、提前结束、单 JD、单项补抓、批量补抓和结果清理始终继承目标 run 平台；
- 平台 schema/城市异步响应不会覆盖当前选择，四类非终态任务按自身平台恢复；
- 调优五类轮次、来源产物与 manifest 平台一致，legacy BOSS-only 入口不回退；
- 桌面和窄屏真实渲染无重叠、溢出和不可达操作。

选择器整体失效、公司性质或城市码未验证、平台/输入摘要错配、平台阻断被记为成功、伪空结果和岗位身份冲突被静默合并，均属于阻断失败。

## 前置条件

1. Python 3.10+、Node.js 20+、Chrome 可用。
2. 依赖已按仓库 README 安装。
3. 使用独立测试数据库或 migration 27 前数据库副本，不拿唯一正式数据库做首次迁移试验。
4. BOSS 使用现有账号 profile；智联使用其确定性 `.zhilian` 派生 profile，且用户已人工登录。
5. 智联默认 CDP 端口 9223 可访问；BOSS 默认端口 9222 保持原状。
6. AI 设置有效，或使用项目既有的受控测试替身完成自动化测试。
7. 当前真实登录页面已核验公司性质、城市、列表、详情、空结果和风控 fixture；未核验时 `zhilian.enabled_for_new_tasks=false`。

## 1. 静态合同与自动化测试

从仓库根目录运行：

```powershell
uv run python -m unittest discover -s tests
npm --prefix webui test
npm --prefix webui run build
uv run python -m unittest tests.test_repo_hygiene
```

预期：全部命令退出码为 0。局部返修先跑原失败用例和直接回归；已知问题收敛后，使用最终代码重新执行一次干净全量。

必须包含的聚焦覆盖：

- 平台注册表、AI schema allowlist、独立城市映射、智联 URL 安全；
- migration 27 的 v26 备份、升级、幂等、外键、source attempt、全局 URL/双索引冲突、调优摘要守恒和失败回滚；
- BOSS/智联 JobSource 契约、显式 CDP 端口，以及真实空结果在状态推进前追加持久化；
- scope digest 与任务输入摘要包含平台；
- 搜索、AI、补抓、状态、结果和继续接口传播冻结平台；
- 简历建议按平台 schema 投影，智联不出现 `stage`，BOSS 不出现 `company_nature`；
- schema/城市请求竞态丢弃陈旧响应，任务平台、草稿平台与结果平台互不改写；
- 进度、取消、提前结束、单 JD、单项补抓、批量补抓和结果 reset 从目标 run 继承平台；
- 浏览器账号 activate/open/delete 的双平台 profile、端口和任务锁边界；
- 调优五类 stage 的平台冻结、list/detail source artifact 分类、rough/fine 继承和执行前错配阻断；
- legacy BOSS-only 创建与执行入口的智联零副作用拒绝；
- 同平台同岗位去重、不同平台同裸 ID 共存、平台 ID 与内部 UUID 分离；
- Vue 平台切换、专属字段草稿隔离和分层提交；
- 错误矩阵的 paused、failed、单项失败与熔断分支；
- 平台禁用后阻止新执行、保留历史读取。

## 2. migration 27 备份与迁移验收

1. 准备一份 schema v26 数据库，记录源库字节大小、修改时间和 schema version。
2. 记录 `jobs/tasks/search_runs/screening_runs/discovery_runs/tuning_experiments/tuning_task_manifests` 行数、全部旧 `jobs.id`，以及收藏和反馈关联计数。
3. 启动新代码，确认构造 `TaskStore` 前已用 SQLite backup API 生成备份和 manifest。
4. 核对 manifest 含源库安全标识、源版本、源/备份大小、创建时间、工具版本和 SHA-256。
5. 对备份执行 SHA-256 复核、只读打开、`PRAGMA quick_check` 和源版本一致性检查。
6. 确认上述任一备份步骤失败都会阻止应用启动，且源数据库未进入部分迁移状态。
7. 正常启动并检查 `schema_migrations` 记录 27，所有存量平台为 `boss`。
8. 检查旧 `jobs.id`、收藏和反馈外键未变化，无法确认的 `platform_job_id` 为 NULL；`jobs.canonical_url` 仍为全局唯一。
9. 检查三处历史平台 ID 列已成为 `platform_job_id`，不存在同一物理字段双重语义。
10. 检查 `screening_source_attempts` 的外键、枚举、岗位数/空证据约束和 `(run_id, combo_key, attempt_no)` 唯一约束；旧 run 不生成猜测记录。
11. 检查存量调优 experiment、manifest 与 stage artifact 外层平台按客观规则回填为 BOSS；原 JSON/digest 均未改写，未知摘要保持 NULL，无法证明的旧 artifact 不可执行。
12. 检查规范 URL 重复或 URL host 与回填平台冲突时迁移整笔阻断，不自动合并岗位。
13. 再次启动，确认备份/迁移幂等且不会覆盖已有备份证据。
14. 注入备份校验失败和迁移中途失败，分别确认启动阻断与事务回滚。

预期：行数、旧内部 UUID 和旧关联守恒；无猜造平台岗位 ID。数据库已有智联数据后禁止恢复旧备份覆盖，只能禁用智联新执行并保留 v27 数据。

## 3. 平台、城市与 AI 筛选合同验收

启动后端：

```powershell
uv run python -m webui.app
```

打开 `http://127.0.0.1:5000`。检查：

1. `/api/platforms` 返回 BOSS 与智联、各自 schema/城市版本和启用状态，但不返回 profile 路径。
2. 默认显示 BOSS，AI 筛选含“融资阶段”且不含“公司性质”。
3. 切到智联，关键词、规范城市、页数和公共 AI 筛选草稿仍在，专属字段变为“公司性质”。
4. 给两个平台分别选择专属值，来回切换后各自草稿仍在。
5. `/api/filter-labels` 带平台，选项来自版本化本地 fixture，稳定值与标签均非空。
6. `/api/options` 只返回当前平台已映射的规范城市；智联全国解析为 `jl0`。
7. 缺少智联某城市映射时预览/创建返回明确错误，且绝不发送 BOSS 城市码。
8. `/api/execute-search` 只提交关键词、规范城市和页数，`filters` 为空或省略。
9. `/api/ai-screen` 才提交 schema 版本与筛选：智联可含 `company_nature`，BOSS 可含 `stage`。
10. 手工向搜索请求加入非空 AI 筛选，返回 `422 search_filters_not_supported`。
11. 手工向 AI 请求加入跨平台字段，返回 `422 filter_validation_failed`。
12. 创建智联任务后立即把草稿切回 BOSS，智联任务状态与结果仍显示智联。
13. 对 `/api/analyze-resume` 分别提交 BOSS 与智联，确认智联建议不含 `stage`、BOSS 建议不含 `company_nature`，且分析响应不能替换已加载 schema 的标签和选项。
14. 将 BOSS 与智联的 `/api/filter-labels`、`/api/options` 响应各延迟并快速交错 100 次，确认界面只应用最后所选平台的响应，取消的旧请求不显示成当前平台错误。
15. 分别恢复 `running`、`queued`、`paused`、`interrupted` 任务，确认先使用任务平台加载 schema/城市和筛选快照；新任务草稿平台与已展示最近结果平台保持原值。

## 4. 智联真实登录态主链

使用有结果的测试关键词和规范城市，仅设一页：

1. 在浏览器账号对话框选择智联并打开对应登录空间。
2. 确认运行时 profile 是 BOSS profile 的 `.zhilian` 派生目录，端口为冻结的 9223；不输出绝对路径。
3. 用户确认页面已登录；系统不自动处理 EdgeOne 或验证码。
4. 创建搜索任务，记录 `task_id`、`platform`、`scope_digest` 和 `task_input_digest`。
5. 检查 run 冻结规范城市、智联城市码、映射版本、账号、端口和 profile key。
6. 观察列表抓取；抽查全部返回岗位的标题、公司、薪资、地点、经验和学历与页面可见内容。
7. 至少抽查一个详情，确认 JD 与页面一致，链接为允许域名的 HTTPS 且无 query/fragment。
8. 完成 AI 粗筛和精筛，打开匹配、不匹配、不确定和失败区域。
9. 检查 AI run 的完整快照含 schema 版本、稳定值和当时标签；搜索 run 不含 AI 筛选。
10. 刷新页面，确认经验、学历和 `extra` 仍存在于结果快照，不依赖内存。
11. 收藏一个智联岗位并提交一次现有反馈，确认先产生内部 UUID，再写入收藏/反馈关系。
12. 再次刷新，确认收藏和反馈仍关联同一内部 UUID，且原平台 ID 仍单独展示。

若搜索真实为空，只有 adapter 返回 `empty_result=true`、当前 fixture 可识别 `empty_evidence`，且编排层在推进状态前成功追加 `outcome_kind=empty` 的 source attempt 才可成功为空。刷新页面并重启后端后，状态与历史结果必须继续返回同一 combo 的 `input_hash`、脱敏空证据和零岗位数；删除结果 snapshot 后该来源审计记录仍存在。该次不满足“至少一个 JD”的 SC-001，必须换关键词重跑主链。

## 5. 岗位身份与 upsert 冲突验收

按 [data-model.md](data-model.md) 的双索引算法逐项验证：

1. 两个索引均不命中时创建新内部 UUID。
2. 同平台同 `platform_job_id`、URL 更新且新 URL 全局未占用时更新原行并释放旧 URL，不产生第二个 UUID。
3. 仅 URL 命中同平台旧 BOSS 行、且旧平台 ID 为空或相同时补写可确认的平台 ID，不改内部 UUID。
4. BOSS 与智联使用相同裸平台 ID 时保存为两个岗位。
5. 平台 ID 索引和全局 URL 索引分别命中不同内部 UUID 时返回 `409 job_identity_conflict`，两行均不变。
6. URL 已由另一平台岗位占用时返回 `409 job_identity_conflict`；平台与 URL host/path 不一致时返回 `422 platform_url_mismatch`，任何查询或写入均不发生。
7. 未落库结果返回 `platform_job_id` 且 `job_id=null`；落库后 `job_id` 只为内部 UUID。
8. 收藏、反馈、profile 关系、垃圾桶和撤销不接受裸 `platform_job_id` 充当内部 UUID。

## 6. 状态、暂停与恢复

分别用 fixture 或受控真实状态验证：

| 场景 | 预期错误码 | 预期结果 |
| --- | --- | --- |
| 智联未登录 | `source_login_required` | run `paused` |
| EdgeOne/验证码 | `source_verification_required` | run `paused` |
| 明确限流 | `source_rate_limited` | run `paused` |
| 平台封禁/拒绝 | `source_blocked` | run `paused` |
| CDP 9223 不可用 | `source_cdp_unavailable` | run `paused` |
| preflight 连接失败且无阻断证据 | `source_unreachable` | 有限重试后 run `failed` |
| preflight 超时且无阻断证据 | `source_timeout` | 有限重试后 run `failed` |
| 列表关键结构失效 | `source_invalid_output` | run `failed` |
| 单岗位下架/超时/解析异常 | 对应 source 码 | 单项失败或待确认，其它岗位继续 |
| 批详情连续平台级 signal 熔断 | 对应 source 码 | run `paused`，保留完成项 |

每个场景检查：

1. DB 与公共 API 严格按状态映射：`succeeded→completed`、`partial→completed_with_pending`、`interrupted+user_cancelled→cancelled`；响应不得在 completed/succeeded 或 cancelled/interrupted 间任选。
2. 所有暂停的状态路径为 `queued -> running -> paused`。
3. `current_stage/error_code/error_reason/platform` 已持久化；interrupted 同时保存确定的 `interruption_kind`。
4. 阻断前的组合、岗位、JD 和 verdict 仍在。
5. 切换草稿平台后点击继续，仍按原任务的智联 profile key 和 9223 preflight。
6. 阻断未解除时返回 `409 block_not_resolved` 并保持 paused。
7. 用户人工解除后继续，已完成组合与岗位不重复计数。
8. 同时发两个继续请求，只允许一个原子 claim 成功。
9. 重启后端后，原 running 先持久化为 `interrupted/process_restart`，公共状态为 `interrupted`，并按 `platform=zhilian`、原筛选快照与输入摘要恢复。
10. 对用户取消的 run 检查 DB 为 `interrupted/user_cancelled`、API 为 `cancelled`，且不能 continue 或 finish；operator stop 的 interrupted 仍可显式恢复或提前结束。
11. 对每个列表组合检查最新 source attempt 的 non-empty/empty/failed/paused 分类；刷新和重启后内容不变，零岗位不会被自行解释为 empty。
12. 任一平台、scope、城市、schema、profile 或输入摘要错配均返回 `409`，不混合结果。

## 7. 平台敏感外围操作

准备一个 BOSS run 和一个智联 run，分别冻结 9222/`boss:<account>` 与 9223/`zhilian:<account>`，然后把新任务草稿切到相反平台：

1. 查询 `/api/search-progress/{run_id}` 与 `/api/task-state/{run_id}`，确认响应平台和输入摘要来自目标 run。
2. 对智联 run 执行阶段取消和通用取消，确认只触发智联 stop event；DB 原子写 `interrupted/user_cancelled`，API 返回 `cancelled`；需要关闭浏览器时只检查 9223 与智联 profile，BOSS 9222 保持可用。
3. 对 paused 或 `interrupted/process_restart|operator_stop` 智联 run 执行提前结束，确认 API 返回 `completed_with_pending`、部分 snapshot 保持智联身份和双 ID，并且只释放该 run 的已知登录空间；`user_cancelled` 必须拒绝 finish。
4. 让目标端口被未知 profile 占用，确认取消/提前结束不关闭该浏览器并返回 `login_space_conflict`；durable 任务状态和已完成结果不被伪造或丢失。
5. 从智联结果调用 `/api/job-detail`，确认必填 `source_run_id + platform_job_id`，adapter 使用来源 run 的智联运行配置。
6. 对智联待确认岗位执行单项 JD 补抓和 `/api/pipeline/recrawl` 批量补抓，确认子 run 继承来源平台、scope、账号、端口、profile key 和任务输入摘要。
7. 分别提交缺失来源 run、跨平台岗位数组、错误内部 UUID 和 URL host 错配，确认在 source 调用前返回固定 `409/422`，不按 UI 或最近结果猜平台。
8. 对 `/api/reset-latest-result` 提交显式 `run_id`，确认只删除目标结果 snapshot 和定义的临时行，不删除 `jobs`、收藏、反馈、其它平台结果、运行任务或来源 search run 的 source attempts。
9. 对无 `run_id` 兼容请求，确认只清全局最近已完成结果并返回实际 `run_id/platform`；请求平台只做一致性校验。
10. 激活浏览器账号后确认只改变新任务账号草稿；打开登录空间必须指定平台。删除账号时分别制造 BOSS 端口占用、智联端口占用、running/queued/paused run 锁和调优租约，任一命中均原子阻断且两个 profile 都保留。
11. 调用 `/api/check?platform=zhilian`，确认只做智联登录空间的安全 preflight，不调用旧 BOSS scraper，也不返回 profile 路径或原始敏感输出。

## 8. 最近结果与平台禁用

1. 同时准备较新的 BOSS result snapshot 和较旧的智联 snapshot。
2. 无参数查询 `/api/latest-pipeline-result`，确认返回全局最新的一个单平台结果。
3. 使用 `platform=zhilian`，确认返回智联最近结果。
4. 使用 `run_id`，确认精确返回该 run；同时传入错误平台时返回 `409 run_platform_conflict`。
5. 在 UI 切换草稿平台，确认已显示的最近结果不会被重标。
6. 设置 `zhilian.enabled_for_new_tasks=false`，确认新智联搜索、AI 和补抓均返回 `platform_disabled`。
7. 确认历史智联任务、结果、岗位、收藏和反馈仍可读取，平台来源不变。
8. 确认禁用不会删除智联 profile、覆盖数据库或自动切换 BOSS。
9. 重新启用并确认暂停的原智联任务仍需通过自身冻结输入和 preflight 才能继续。

## 9. 调优实验平台合同

分别创建 BOSS 与智联调优 experiment，并对智联完整验证五类 round：

1. `source_scope`、workload、输入 artifact、experiment 外层列均保存同一智联平台、规范城市解析、schema 版本、账号、9223、profile key、scope digest 和 task input digest；round `stage` 仅允许 `list/detail/rough/fine/end_to_end`。
2. 签发 list manifest，确认 `fixed_fields/frozen_input/manifest_digest` 覆盖上述身份；执行时创建智联 adapter 并在 round 专属 artifact 根目录运行。
3. list artifact 的 DB 外层和 JSON 均为 `stage=list/source_artifact_kind=list`；detail round 只复用同一智联 list artifact，校验平台、input version、workload、scope 和 artifact digest 后才调用智联 detail。
4. detail artifact 的 DB 外层和 JSON 均为 `stage=detail/source_artifact_kind=detail`；rough 只接受 list artifact，继承平台和 schema，不创建 JobSource、不读取当前 UI 或活动账号。
5. fine 只接受 detail artifact 并继承平台和 schema，不创建 JobSource；交换 rough/fine 的 artifact 类型必须在 AI 前阻断。
6. end-to-end round 不复用中间 artifact，从 manifest 冻结 runtime 创建智联 adapter 并完整运行，其 `source_artifact_kind` 为 NULL，不得被 rough/fine 复用。
7. 依次篡改 experiment、workload、artifact、manifest 外层列、manifest JSON 和 program evidence 的平台或摘要，确认每次都在 source/AI 调用前以 `tuning_platform_mismatch` 或 `manifest_validation_failed` 阻断。
8. 禁用智联后，确认不能签发或执行新的 source round，历史 experiment、round、manifest 和 evidence 仍可读取。
9. 对迁移前旧 manifest/artifact 验证只在创建时间、experiment/input/workload/manifest 与原摘要均能客观证明时按 BOSS 执行；原 JSON/摘要不被改写，未知摘要不猜填，身份无法证明时阻断。
10. 取消智联 experiment 只作用于其租约和已知智联登录空间，不关闭 BOSS 或未知 profile。

## 10. Legacy BOSS-only 边界

对 [http-api.md](contracts/http-api.md) 的 Legacy BOSS-only 矩阵执行参数化测试：

1. 对每个 legacy POST 在 JSON body 提交 `platform=zhilian`：`/api/tasks`、`/api/scrape`、`/api/setup-chrome`、`/api/confirm-fields`、旧 task cancel/retry、`/api/search-runs` create/cancel。
2. 对每个 legacy GET 在 query 提交 `platform=zhilian`：任务列表/detail/result/summary/export、`/api/results`、search-run detail/jobs。
3. 每个请求均应在任务或对象查找前返回 `422 legacy_platform_not_supported`；数据库任务/query/event/状态、后台注册表、产物目录、导出、BOSS 结果文件、浏览器进程和两个 profile 的前后快照完全相同。
4. 显式 `boss` 与省略平台均回归既有 BOSS 行为；所有成功任务、run、岗位和结果对象明确 `platform=boss`，不得混入智联结果。
5. 对 body/query 中未知平台确认返回 `400 platform_validation_failed`，不得按省略平台的 BOSS 兼容分支处理。
6. 特别验证 `/api/search-runs` create 不创建 run/query/event，cancel 不触发 stop event/状态写入，detail/jobs 不读取 BOSS workbench 数据；该链不作为智联抓取入口。

## 11. BOSS 回归

1. 使用迁移前 BOSS 数据副本启动。
2. 确认历史岗位、任务、收藏和反馈显示 `boss`，旧内部 UUID 与关联不变。
3. 创建一页 BOSS 搜索，走列表、JD、AI 和结果。
4. 确认 BOSS 搜索同样不下推 AI 筛选；AI run 仍含融资阶段快照。
5. 暂停/恢复一个 BOSS fixture 任务。
6. 确认 BOSS subprocess list/detail/batch 使用任务冻结端口，而不是隐式默认端口。
7. 确认 BOSS URL 白名单没有因智联放宽。

## 12. 真实视口验收

使用 Playwright 或浏览器设备模式，至少检查：

- 桌面：1440×900
- 窄屏：390×844

在两种尺寸完成平台切换、筛选展开/收起、启动任务、查看暂停原因、继续任务、浏览四个结果区域、收藏/反馈、打开岗位链接和浏览器账号对话框。

预期：无页面横向滚动、控件重叠、逐字竖排、文字遮挡、双滚动条或不可达主操作；平台分段控件具有可见焦点、正确选中状态和清晰平台来源。

## 13. 安全与产物检查

检查最终差异和生成产物：

```powershell
git status --short
git diff --check
git diff --cached
```

确认没有 Cookie、Key、密码、本地绝对路径、真实简历、JD 正文 fixture、运行数据库、Chrome profile、任务 artifact 或意外前端构建文件进入提交。脱敏 fixture 只保留解析合同所需的最小 DOM/JSON。

## 验收停止条件

自动化全量、migration 备份与迁移、双 ID 冲突矩阵、BOSS 回归、智联一页真实主链、错误矩阵、四类非终态恢复、全部平台敏感外围入口、调优五类轮次、legacy 零副作用拒绝、禁用路径、最近结果三种查询和两个真实视口均通过，且严格档独立审查没有阻断项时，功能才可交付。优化建议不阻塞当前切片；任何平台身份混用、伪空结果、伪成功、跨平台浏览器关闭、legacy 静默回退或数据关联破坏都必须修复并重新执行受影响回归。
