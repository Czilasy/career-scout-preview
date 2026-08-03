# 数据模型：招聘平台与智联任务

**日期**：2026-08-03  
**规格**：[spec.md](spec.md)  
**技术计划**：[plan.md](plan.md)

## 设计原则

- `platform` 是岗位来源和任务来源的不可变身份，当前只允许 `boss`、`zhilian`。
- 任务创建后，平台、scope、AI schema 版本、浏览器账号、CDP 端口和 profile key 都从任务快照读取，不再读取界面当前选择。
- `jobs.id` 继续作为内部 UUID；招聘平台原始岗位身份统一保存为 `platform_job_id`。
- 存量记录迁移为 `platform='boss'`；无法可靠提取的存量平台 ID 保持 `NULL`，不猜造。
- 平台专属 AI 筛选值保存在版本化筛选快照；岗位专属展示字段保存在 `extra_json`，结果和岗位主体都保存完整快照。

## 枚举和值对象

### Platform

| 值 | 显示名 | AI 专属筛选字段 | 默认 CDP 端口 | 新任务状态 |
| --- | --- | --- | --- | --- |
| `boss` | BOSS直聘 | `stage` | `9222` | `enabled_for_new_tasks=true` |
| `zhilian` | 智联招聘 | `company_nature` | `9223` | 真实 schema/城市/页面合同验证后启用，否则 `false` |

平台项还包含 `availability_reason`。未知平台在 API、schema、数据库写入和恢复边界返回校验错误，不得回退成 BOSS。

### PlatformFilterSchema

平台注册表发布的只读值对象：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `platform` | Platform | 必填，与请求平台一致 |
| `schema_version` | 正整数 | 字段、选项稳定值或标签映射变化时递增 |
| `enabled_for_new_tasks` | boolean | false 时只禁用新任务创建/补抓，不影响历史读取 |
| `fields` | FilterField[] | 键唯一，按界面顺序排列 |

`FilterField` 包含 `key`、`label`、`multiple`、`options`。每个 option 包含稳定 `value` 和当时用户可见 `label`。临时网页编码只在 adapter 映射层存在，不进入快照。

公共 AI 筛选字段：`salary`、`experience`、`degree`、`industry`、`scale`。BOSS 追加 `stage`；智联追加 `company_nature`。搜索任务不携带这些字段。

### NormalizedCity

任务只保存规范城市名及平台解析快照：

```json
{
  "name": "上海",
  "platform": "zhilian",
  "platform_code": "<verified-code>",
  "mapping_version": 1,
  "mapping_label": "上海"
}
```

`全国` 在智联的 `platform_code` 固定为 `jl0`，但仍保存规范名、映射版本和标签。其它 code 必须来自经验证的脱敏 fixture；缺少映射时任务创建或执行前阻断。

## 持久化实体

### Job（`jobs`）

| 字段 | 类型 | 空值 | 规则 |
| --- | --- | --- | --- |
| `id` | TEXT UUID | 否 | 现有内部主键；收藏、反馈和 profile 关系只引用此值 |
| `platform` | TEXT | 否 | 新增，存量默认 `boss`，应用校验为平台注册值 |
| `platform_job_id` | TEXT | 是 | 新增；新抓取岗位必填，存量未知时为 `NULL` |
| `canonical_url` | TEXT | 否 | 保留现有全局唯一约束；参与查重前必须按声明平台完成 host/path 校验与规范化 |
| `source_url` | TEXT | 否 | 官方原链接或规范化前官方链接；非允许域名拒绝 |
| `title` | TEXT | 否 | 缺失时为空字符串，不编造 |
| `company` | TEXT | 否 | 缺失时为空字符串，不编造 |
| `salary` | TEXT | 否 | 页面展示文本；不伪造数值 |
| `location` | TEXT | 否 | 页面展示文本 |
| `experience` | TEXT | 否 | 页面可见文本；缺失为空 |
| `degree` | TEXT | 否 | 页面可见文本；缺失为空 |
| `jd` | TEXT | 否 | 详情未取得时为空，失败原因由任务/待确认实体保存 |
| `extra_json` | JSON object | 否 | 非敏感、已归一化的平台专属字段；默认 `{}` |
| 现有时间字段 | TEXT | 按现状 | 保持不变 |

索引：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_platform_job_id
ON jobs(platform, platform_job_id)
WHERE platform_job_id IS NOT NULL;
```

### Job upsert 冲突算法

输入至少包含 `platform`、`platform_job_id`、规范 `canonical_url`。`canonical_url` 继续是全局唯一键，不改为 `(platform, canonical_url)`；允许 host/path 已由平台注册规则唯一归属平台，因此同一规范 URL 不得落入两个平台。

1. 在任何数据库查询或写入前，先校验 URL host/path 属于声明平台；不属于时返回 `platform_url_mismatch`。
2. 查询 `(platform, platform_job_id)` 命中行 `by_platform_id`，再按全局唯一 `canonical_url` 查询 `by_url`。若 `by_url.platform != input.platform`，返回 `job_identity_conflict`，不得跨平台认领。
3. 两者都没有：创建新 `jobs.id` 内部 UUID。
4. 只命中平台 ID：仅当新 URL 未被任何其它行占用时，在同一事务中更新原行并释放旧 URL；否则返回 `job_identity_conflict`。
5. 只命中 URL：仅当该行平台与输入平台一致，且其 `platform_job_id` 为 NULL 或等于输入值时补写平台 ID；已有不同平台 ID 时返回 `job_identity_conflict`。
6. 两者命中同一行：更新可变岗位字段和最新快照。
7. 两者分别命中不同内部 UUID：返回 `job_identity_conflict`，禁止静默合并、覆盖或改写任一行。
8. 任一冲突均保持原 URL、内部 UUID、收藏和反馈关联不变。

所有 upsert、结果快照写入以及收藏/反馈关联在同一数据库事务中完成。

### ScreeningRun（`screening_runs`）

这是本期主执行根实体。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `id` | TEXT | 现有任务 ID |
| `platform` | TEXT | 新增，非空，创建后不可修改 |
| `frozen_filters_json` | JSON object | AI run 才使用；搜索 run 必须为空对象 |
| `filter_schema_version` | INTEGER | AI run 的 schema 版本；搜索 run 为 NULL |
| `filter_snapshot_json` | JSON object | AI run 的完整快照；搜索 run 为 `{}` |
| `task_input_digest` | SHA-256 | 覆盖平台、scope digest、当前阶段冻结筛选、schema version、账号、CDP 端口和 profile key |
| `execution_params_json` | JSON object | 见下方；不得含 profile 绝对路径或凭据 |
| `interruption_kind` | TEXT/null | 仅 `status=interrupted` 使用：`user_cancelled`、`process_restart` 或 `operator_stop`；决定公共状态和可恢复性 |
| 现有状态、计数、阶段、错误与版本字段 | 按现状 | 保持现有状态机和守恒规则 |

AI 筛选快照格式：

```json
{
  "schema_version": 1,
  "platform": "zhilian",
  "fields": {
    "company_nature": {
      "values": ["verified_value"],
      "labels": ["当时标签"]
    }
  }
}
```

快照必须保存字段键、稳定值和当时标签；值与标签数组按同一选项顺序对应。恢复以稳定值执行校验，以标签提供历史解释。

`execution_params_json` 的平台相关合同：

```json
{
  "platform": "zhilian",
  "filter_schema_version": 1,
  "browser_account": "a",
  "cdp_port": 9223,
  "profile_key": "zhilian:a",
  "task_input_digest": "sha256-hex",
  "search_params": {
    "keyword": "Python 后端",
    "city": ["上海"],
    "pages": 1,
    "filters": {}
  },
  "resolved_cities": [{"name":"上海","platform_code":"<verified-code>","mapping_version":1}],
  "frozen_scope": {},
  "execution_config": {}
}
```

`profile_key` 是非敏感逻辑标识；API 和日志不得返回本地 profile 绝对路径。AI run、补抓 run 如创建子 run，必须从父 run 复制平台、scope digest、账号、端口和 profile key，不得接受客户端覆盖。进度、取消、提前结束、单 JD 和补抓入口均先读取这些冻结值；浏览器关闭只允许使用目标 run 的 `cdp_port/profile_key`，缺失或与实际占用 profile 不一致时报告冲突，不回退默认端口。

### ScrapeRunJob（`scrape_run_jobs`）

该表物理列在 migration 27 中从 `job_id` 重命名为 `platform_job_id`，因为它保存平台原始 ID；`run_id` 对应的 `screening_runs.platform` 是权威平台。

| 字段 | 规则 |
| --- | --- |
| `run_id` | 外键指向搜索/筛选 run |
| `platform_job_id` | 平台原始 ID；同一 run 内唯一 |
| `combo_key` | 规范关键词与城市组合键 |
| `job_payload_json` | 必须含 `platform`、`platform_job_id`、统一岗位字段和 `extra` |

旧 payload 缺平台时按父 run 的 `platform` 解释；显式冲突拒绝恢复。

### ScreeningSourceAttempt（`screening_source_attempts`）

这是 source 列表组合结果的追加式持久化审计记录。`JobSourceOutcome` 仍是内存返回值，但编排层必须在推进 run、发布进度或写结果快照前，先把每次列表尝试的最终分类写入本表。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `id` | INTEGER | 自增主键 |
| `run_id` | TEXT | 外键指向 source 搜索 run，删除 run 时级联 |
| `platform` | TEXT | 必须等于父 run 平台 |
| `combo_key` | TEXT | 规范关键词与规范城市组合键 |
| `attempt_no` | INTEGER | 同一 run/combo 从 1 递增 |
| `input_hash` | SHA-256 | 覆盖平台、关键词、完整城市解析快照和页数 |
| `outcome_kind` | TEXT | 仅 `non_empty`、`empty`、`failed`、`paused` |
| `job_count` | INTEGER | 非负；`empty` 必须为 0，`non_empty` 必须大于 0 |
| `empty_evidence_json` | JSON/null | `empty` 必填，且只含 fixture 版本、证据种类、稳定 marker 与脱敏标记；其它分类必须为 NULL |
| `error_code` / `error_reason` | TEXT/null | `failed/paused` 必填稳定安全错误；成功分类为空 |
| `created_at` | TEXT | 追加时间 |

唯一约束为 `UNIQUE(run_id, combo_key, attempt_no)`；同一 run/combo 的当前结果取最大 `attempt_no`，旧尝试不覆盖。写入时重新计算 `input_hash` 并与冻结 scope 校验；冲突时不推进 run。真实空结果只有在 `outcome_kind=empty`、证据可由冻结 fixture 版本解释且持久化提交成功后才算完成组合。

进度、任务状态和最近结果按来源 search run 汇总每个 combo 的最新尝试，返回安全的 `source_summary` 和 `source_outcomes`。刷新或重启不得从“岗位数为 0”反推 empty；AI 子 run/result snapshot 必须保存或可确定追溯其 `source_run_id`。结果 reset 不删除本表审计记录，除非另一个显式保留策略操作删除整个 source run。

### ScreeningResult（`screening_results`）

该表同时承担 AI 结果和结果快照行。migration 27 将其平台原始身份列重命名为 `platform_job_id`，并新增可空内部 `job_id`：

- 未落库的 pipeline 结果可保存 `job_id=NULL`，但必须有 `platform_job_id`；
- 写入收藏、反馈或对外已落库结果前，先 upsert `jobs` 并回填内部 `job_id`；
- `platform`、标题、公司、薪资、地点、经验、学历、JD、规范链接和 `extra_json` 作为结果快照保存，即使之后岗位主体更新，历史 run 仍可解释；
- `UNIQUE(run_id, platform_job_id)` 保持同一 run 内一条结果。

### ScreeningPendingResult（`screening_pending_results`）

其 `job_id` 物理列同样重命名为 `platform_job_id`；内部 UUID 不用于 AI 断点键。单项失败保存失败阶段、错误码、可重试性、次数和平台岗位 ID，继续时只跳过已完成的同一平台岗位。

### PipelineCheckpoint（`pipeline_checkpoints`）

平台通过 `run_id` 继承。继续前联合校验：

- run 的 `platform`；
- `execution_params.platform`、`task_input_digest`；
- `frozen_scope.scope_digest` 及平台城市映射快照；
- AI run 的 schema version 和完整筛选快照可解释性；
- `browser_account`、`cdp_port`、`profile_key`；
- 已保存 payload 的平台、`platform_job_id` 和 canonical URL 不冲突。

任何错配返回 `409`，不得把不同平台的完成键或岗位混入。

### TuningExperiment（`tuning_experiments`）

调优实验会执行真实 source，因此平台是实验根身份：

| 字段 | 规则 |
| --- | --- |
| `platform` | migration 27 新增、非空、创建后不可修改；存量回填 `boss` |
| `source_scope_json` | 必须含同一 `platform`、规范城市、解析城市快照、映射版本和 scope digest |
| `input_version_id` | 指向冻结输入版本；其 scope 与实验平台一致 |
| 现有状态与候选字段 | 保持既有调优状态机和租约合同 |

新 experiment 创建时还冻结 `browser_account`、`cdp_port`、`profile_key` 与 `filter_schema_version` 到 source scope。AI 设置仍按现有安全引用读取，不进入平台快照。

### TuningWorkload（`tuning_workloads`）与输入 artifact

不新增重复平台列；`frozen_scope_json` 和 `artifact_manifest_json` 必须保存同一完整快照：

```json
{
  "platform": "zhilian",
  "keywords": ["Python 后端"],
  "cities": ["上海"],
  "resolved_cities": [{"name":"上海","platform_code":"verified-code","mapping_version":1}],
  "browser_account": "a",
  "cdp_port": 9223,
  "profile_key": "zhilian:a",
  "filter_schema_version": 1,
  "scope_digest": "sha256-hex",
  "task_input_digest": "sha256-hex"
}
```

artifact digest 覆盖上述字段及现有 workload/quality context。`quality_context.screening_fields` 按平台 schema 投影，并保存平台和 schema 版本；不能把 BOSS `stage` 带入智联 artifact。

### TuningTaskManifest（`tuning_task_manifests`）

| 字段 | 规则 |
| --- | --- |
| `platform` | migration 27 新增的外层权威列；存量回填 `boss` |
| `manifest_json` | 新签发 manifest 的 `frozen_input` 与 `fixed_fields` 都显式包含平台、城市解析、运行配置和摘要 |
| `manifest_digest` | 继续覆盖签发时 manifest；任何平台字段变化都使摘要失效 |

签发、执行与报告按 experiment、workload、source artifact、manifest 外层列、manifest JSON 和 program evidence 做一致性校验。`list/detail/end_to_end` 依据 manifest 创建对应平台 adapter；`rough/fine` 不创建 source，但必须从 source artifact 继承并校验平台。阶段 artifact 只返回安全平台键和摘要，不含 profile 路径。

存量已签发 manifest 不修改 JSON 和摘要；外层列仅将可证明为迁移前记录的条目回填 `boss`。执行旧记录时固定 BOSS，若 artifact 或 experiment 身份无法证明一致则阻断，绝不重标为智联。

### TuningStageArtifact（`tuning_stage_artifacts`）

阶段产物继续沿用现有五类 `stage` 记录，不要求为存量 artifact 重写内容。migration 27 为外层记录增加 `platform`、`source_artifact_kind`、`scope_digest` 和 `task_input_digest`；新产物的数据库外层列与 manifest/payload 必须保存同一身份，并由 artifact digest 覆盖：

| 字段 | 规则 |
| --- | --- |
| `platform` | 与 experiment、workload 和签发 manifest 一致 |
| `input_version_id` / `workload_id` | 与产生该产物的冻结输入一致 |
| `stage` | 现有轮次类型：`list`、`detail`、`rough`、`fine` 或 `end_to_end` |
| `source_artifact_kind` | `stage=list` 时为 `list`，`stage=detail` 时为 `detail`；其它 stage 为 NULL。只有非空值可作为 rough/fine 的 source 输入 |
| `scope_digest` | 与 workload 冻结 scope 一致 |
| `task_input_digest` | 覆盖平台、城市解析和浏览器运行配置 |
| `artifact_digest` | 覆盖安全身份字段与规范化产物内容 |

新 `list/detail/end_to_end` 轮次写入产物前校验平台；`rough` 只接受 `source_artifact_kind=list`，`fine` 只接受 `source_artifact_kind=detail`，并在读取前逐项比对数据库外层列、artifact JSON、experiment、workload 与 manifest。`end_to_end` 的输出可作为该轮证据，但 `source_artifact_kind=NULL`，不得被 rough/fine 复用。

存量 artifact 不修改 JSON 或 digest。仅当记录创建时间早于 migration 27、所属 experiment/input version/workload/manifest 均可证明为迁移前 BOSS、原摘要有效，且 `stage` 为 `list/detail` 时，运行时才可将其分别解释为 BOSS list/detail source artifact；数据库只回填外层 `platform=boss`，摘要列未知则保持 NULL。无法完成上述客观证明时阻断，不猜填摘要、不重标智联。

### LoginSpace（本地配置，不入 SQLite 业务表）

由 `browser_account + platform` 唯一确定：

| 字段 | 规则 |
| --- | --- |
| `browser_account` | 复用现有账号 ID，如 `a`、`b` 或自定义 ID |
| `platform` | `boss` 或 `zhilian` |
| `profile_dir` | BOSS 使用现有 profile；智联固定为该路径加 `.zhilian` 后缀；只在后端解析 |
| `profile_key` | 固定为 `<platform>:<browser_account>`；可持久化，不含绝对路径 |
| `cdp_port` | BOSS 默认 9222，智联默认 9223；同平台账号共享端口并受控替换 |

同一账号的两个平台 profile 必须不同；运行或暂停任务期间沿用现有规则阻断删除/切换造成的破坏。
`activate` 只更新新任务账号草稿。删除账号前同时检查两个派生 profile、9222/9223 及所有运行/暂停任务的 `profile_key`；任何命中均原子阻断删除，不先删除其中一个目录。

## 非持久化执行实体

### JobSourceOutcome

沿用 `SourceOutcome`，新增平台上下文只进入安全事件，不把 JD、Cookie 或本地路径放进日志：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `ok` | boolean | 有可用结果时为真；真实空列表也是成功 |
| `jobs` / `detail` | object | 必须是统一岗位合同 |
| `empty_result` | boolean | 仅列表使用；真实空结果为 true，其它成功和全部失败为 false |
| `empty_evidence` | object/null | 真实空结果必须保存 fixture 版本、证据种类和脱敏标记；零卡片本身不是证据 |
| `failed_code` | string | 仅允许 `SAFE_FAILURE_CODES` |
| `failed_reason` | string | 可展示的简短原因 |
| `safe_log` | string | 只含平台、阶段、计数、是否存在 ID、URL host 等安全信息 |
| `input_hash` | string | 列表输入的规范摘要，含平台、规范城市和平台码 |

## 状态转换

保持现有 `screening_runs` 状态机：

```text
queued -> running -> succeeded | partial | failed | paused | interrupted
paused -> running              (仅一次原子 claim 成功)
```

所有平台级人工阻断都必须经过 `queued -> running -> paused`；创建 run 后先写 `running` 再执行 preflight。

### 状态边界映射

`screening_runs.status` 只保存 canonical DB 值，不保存 `completed`、`completed_with_pending`、`done` 或 `cancelled`。公共映射固定如下：

| DB canonical | task/progress API | result snapshot 兼容值 | 终态与恢复 |
| --- | --- | --- | --- |
| `queued` | `queued` | 不适用 | 非终态；服务恢复后可重新调度 |
| `running` | `running` | 不适用 | 非终态；服务重启后先转 `interrupted/process_restart` 再恢复 |
| `paused` | `paused` | 不适用 | 非终态；阻断解除后可原子 continue |
| `succeeded` | `completed` | `done` | 成功终态；`done` 仅为既有结果响应/文件兼容值 |
| `partial` | `completed_with_pending` | `partial` | 部分成功终态，可由显式补抓创建子 run |
| `failed` | `failed` | `failed` | 失败终态，不伪装为完成 |
| `interrupted` + `user_cancelled` | `cancelled` | `cancelled` | 用户取消终态，不自动恢复 |
| `interrupted` + `process_restart/operator_stop` | `interrupted` | 不适用 | 非终态；按冻结身份显式恢复或提前结束 |

API 输入别名只在边界翻译，不能直接写入 DB。所有状态接口、最近任务、最近结果、取消、提前结束和前端判断必须使用此表；`source` 字段仍只表示内存/数据库等状态数据来源，不表示招聘平台。

## Migration 27

### 迁移前 bootstrap

1. 进程发现数据库文件存在且 schema 版本低于 27 时，先在 `TaskStore` 构造前使用 SQLite backup API 生成带时间戳的备份文件。
2. 同时生成 manifest，至少含源库路径的安全标识、源库字节大小、源库修改时间、schema version、备份文件大小、SHA-256、创建时间和工具版本；manifest 不把本地绝对路径写入公开日志。
3. 关闭备份连接后用只读连接验证 SHA-256、`PRAGMA quick_check`、`schema_migrations` 可读和目标版本等于源版本。验证失败阻止构造 `TaskStore`。
4. 备份文件、manifest 和状态日志位于本地忽略目录，不进入仓库。

### 迁移事务

在一个事务内：

1. 为 `jobs` 增加 `platform`、`platform_job_id`、`experience`、`degree`、`extra_json`。
2. 为 `tasks`、`search_runs`、`screening_runs`、`discovery_runs` 和 `tuning_experiments` 增加 `platform`，为 `screening_runs` 增加 `filter_schema_version`、`filter_snapshot_json`、`task_input_digest`、`interruption_kind`；为 `tuning_task_manifests` 增加外层 `platform`。
3. 为 `screening_results` 增加 `platform`、`platform_job_id`、可空内部 `job_id`、经验、学历、`extra_json`；为 `screening_pending_results` 增加 `platform` 并把物理 `job_id` 重命名为 `platform_job_id`。
4. 将 `scrape_run_jobs.job_id` 重命名为 `platform_job_id`，更新其主键、读取/写入 SQL 和 payload 校验。
5. 对 `screening_results` 的原 `job_id` 复制到 `platform_job_id`，新增内部 `job_id` 初始为 NULL；已有结果不猜造 `jobs.id`。
6. 全部新增平台字段对存量记录回填 `boss`；无法确认的 `platform_job_id` 保持 NULL。调优存量仅回填外层列，不改写已签发 manifest JSON、artifact 或摘要。
7. 创建 `screening_source_attempts` 及其外键、枚举检查和 `UNIQUE(run_id, combo_key, attempt_no)`；旧 run 不猜造空结果记录。
8. 保留 `jobs.canonical_url` 的全局唯一约束并创建 `(platform, platform_job_id)` 部分唯一索引；结果和运行链创建各自以平台岗位 ID 为键的唯一约束。迁移前若规范 URL 重复或 URL host 与回填平台不一致则整笔阻断，不自动合并。
9. 为 `tuning_stage_artifacts` 增加外层 `platform`、`source_artifact_kind`、`scope_digest`、`task_input_digest`；仅按上述存量证明规则回填 `platform=boss`，不修改 artifact JSON/digest，不猜填摘要。
10. 执行外键检查、重复身份检查、BOSS 行数/UUID/收藏/反馈计数、source outcome 约束、调优 manifest 摘要及 artifact 摘要对照，任一失败整笔回滚。
11. 写入 `schema_migrations.version=27`。

迁移不得解析旧 URL 猜造平台岗位 ID，不得重写现有 `jobs.id`、profile 关系、反馈事件或收藏状态。SQLite 不支持直接安全重命名所有带外键的旧列时，使用“新表复制 + 校验 + 原子替换”的受控实现；禁止留下同一物理列两个语义的兼容别名。
