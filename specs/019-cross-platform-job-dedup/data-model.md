# Data Model: 跨平台岗位去重（019）

**零数据库结构变更**。本功能引入的实体为运行时/载荷层实体：岗位指纹（纯计算）、跨平台剔除记录（复用既有 dropped 行的 extra 载荷）、双平台标注映射（前端运行时）。

## 实体

### 1. 岗位指纹（Job Fingerprint）— 纯计算，不落库

| 字段 | 类型 | 说明 |
|---|---|---|
| company | str | 归一化公司名（剥括号注释/组织后缀/城市名前缀，全半角、去空白、小写） |
| title | str | 归一化标题（全半角、去空白、小写；不做语义归并） |
| city | str | 市级城市（从 location 取首段并去「市」等后缀） |

- **验证规则**: 三元组任一为空 → 指纹为 None，岗位不参与跨平台判定。
- **相等语义**: 三元组完全相等且**两侧平台不同** → 判定跨平台重复。
- **数据来源**: 当前平台取筛选输入岗位 dict（title/company/location）；对端取最新可见轮非剔除结果行。

### 2. 跨平台剔除记录（Cross-Platform Drop Record）— 复用 screening_results dropped 行

落库形态（既有列 + extra 载荷，无新列）：

| 载体 | 内容 |
|---|---|
| verdict | `"dropped"` |
| verdict_reason | `跨平台重复：已在 <对端平台名> 保留` |
| is_dropped | 1 |
| extra_json → `cross_platform_dup_of` | `{platform, platform_job_id, source_url, finished_at}`（对端保留条目身份 + 最近包含轮定稿时间） |
| 其余列（title/company/...） | 照常来自被剔除的当前平台岗位 dict |

- **状态迁移**: 生成于筛选输入组装点（每次进入 `_run_ai_screen_task` 确定性重算）→ verdict 落库（upsert 幂等）→ 并入最终剔除列表 → 随 `save_finished_round` 落轮（dropped 行 extra 落 `extra_json`）。
- **约束**: 不修改对端轮任何数据；同一岗位在同轮内至多一条跨平台剔除记录（按指纹首个命中定源）。

### 3. 跨平台重复簇（Cross-Platform Cluster）— 前端运行时，不落库

| 字段 | 类型 | 说明 |
|---|---|---|
| `_also_on_copies` | `Array<{platform, salary, source_url, platform_job_id}>` | 合并视图挂在簇主（对端保留条目）上的运行时簇数据；成员字段取自本平台剔除行自身 |

- **构建规则**: 前端合并视图遍历两平台 dropped 记录的 `extra.cross_platform_dup_of`，按 `(platform, platform_job_id)` 反查合并 jobs 中的保留条目挂载；查无目标（对端轮已被更新轮取代等）则静默跳过（退化为剔除台账条目）。
- **展示**: 列表一行（簇主 + 「双平台在招」徽标）；详情成组区并排展示各平台副本（平台名、薪资、链接），薪资不合并、如实展示各自写法。

### 4. 去重台账事件（Dedupe Ledger Event）— 既有任务事件流

| 字段 | 类型 | 说明 |
|---|---|---|
| kind | `"cross_platform_dedup"` | 任务事件类型 |
| dropped | `Array<{job_id, title, dup_of}>` | 本次剔除的岗位清单及各自对端指向（platform/platform_job_id/source_url/finished_at） |
| counts | `{scraped, deduped}` | 抓取总数与剔除数（对账用） |

- 一次筛选一条（剔除数 >0 时写入）；任务详情事件流可查。

### 5. 去重开关（请求字段 + 执行参数冻结）— 不新增存储

`POST /api/ai-screen` 请求体可选布尔 `cross_platform_dedupe`（缺省=true）；取值随 run 执行参数冻结，续跑/重启/一键接续沿用；前端记忆于 localStorage。关闭时：不剔除、无簇、无台账，行为与现状一致。

## 与既有数据的交互

- 读取（对端判定源）: 逐轮读取对端平台近 30 天内全部可见轮（`store.list_history_rounds(platform)` + 按轮加载结果），取各轮 `result.jobs`（非剔除行）；逐轮过滤——轮画像摘要非空且与当前任务不同 → 跳过该轮；轮全部岗位均为剔除行 → 跳过该轮；轮定稿时间超 30 天 → 跳过该轮。
- 写入: 仅当前平台筛选 run 的 verdict/checkpoint（既有方法）与最终轮 dropped 行（既有 `save_finished_round` 路径，extra 随 job dict 透传）。
- 不触碰: `jobs` 全局表、`scrape_run_jobs`、`profile_jobs`、收藏/反馈表、对端平台任何轮次。
