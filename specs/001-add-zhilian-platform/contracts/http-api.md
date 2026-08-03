# HTTP API 合同：多招聘平台

**版本**：`platform-api-v1`  
**兼容策略**：未传平台的旧 BOSS 请求只在本文明确入口默认 `boss`；新前端始终显式传 `platform`。未知或禁用平台不得回退为 BOSS。

统一错误体：

```json
{
  "ok": false,
  "error_code": "platform_validation_failed",
  "user_message": "不支持的招聘平台",
  "details": {}
}
```

历史入口迁移期可同时返回同文案的 `error`，但 `error_code` 是新客户端稳定判断字段。

## GET `/api/platforms`

禁用平台仍返回，以便历史任务显示来源：

```json
{
  "ok": true,
  "platforms": [
    {
      "key": "zhilian",
      "display_name": "智联招聘",
      "filter_schema_version": 1,
      "city_mapping_version": 1,
      "enabled_for_new_tasks": true,
      "availability_reason": ""
    }
  ],
  "default_platform": "boss"
}
```

不返回 profile 路径、Cookie、浏览器参数或本地用户名。

## GET `/api/options?platform={platform}`

升级现有 options 入口，只返回当前平台有已验证映射的规范城市：

```json
{
  "ok": true,
  "platform": "zhilian",
  "city_mapping_version": 1,
  "cities": [
    {"label": "全国", "value": "全国"},
    {"label": "上海", "value": "上海"}
  ]
}
```

前端不接收平台城市码；后端解析并冻结。省略平台只兼容旧 BOSS 前端。错误：`400 platform_validation_failed`、`503 city_mapping_unavailable`。

## GET `/api/filter-labels?platform={platform}`

这是平台 **AI 筛选 schema**，不是平台搜索参数。规范响应：

```json
{
  "ok": true,
  "platform": "zhilian",
  "schema_version": 1,
  "enabled_for_new_tasks": true,
  "fields": [
    {"key": "salary", "label": "薪资范围", "multiple": true, "options": []},
    {"key": "experience", "label": "经验要求", "multiple": true, "options": []},
    {"key": "degree", "label": "学历", "multiple": true, "options": []},
    {"key": "industry", "label": "行业", "multiple": true, "options": []},
    {"key": "scale", "label": "公司规模", "multiple": true, "options": []},
    {"key": "company_nature", "label": "公司性质", "multiple": true, "options": []}
  ]
}
```

示例空数组不定义实际选项。正式启用智联前，版本化本地 fixture/注册表必须提供真实验证的稳定 `value` 与 `label`。迁移期旧 `labels` 只能从同一 schema 派生。

## POST `/api/search-scope/preview`

请求：

```json
{
  "platform": "zhilian",
  "keywords": ["Python 后端"],
  "scope_kind": "cities",
  "cities": ["上海"],
  "pages_per_combination": 1
}
```

后端验证每个规范城市在当前平台映射中存在；`全国` 与具体城市互斥。响应 `200` 的 scope 明确包含 `schema_version=2`、`platform`、规范关键词/城市、页数、组合数、计划页数、任务规模和 `scope_digest`。摘要计算包含平台但不包含 AI 筛选。

错误：`422 city_mapping_missing`、`503 platform_disabled`。旧请求可默认 BOSS，但返回摘要必须显式含 `platform=boss`。

## POST `/api/execute-search`

搜索只使用关键词、规范城市和页数：

```json
{
  "platform": "zhilian",
  "script_params": {
    "keyword": "Python 后端",
    "city": ["上海"],
    "pages": 1,
    "filters": {}
  },
  "scope_digest": "sha256-hex"
}
```

`filter_schema_version`、`screening_fields`、`company_nature` 和 `stage` 不得出现在搜索请求。`filters` 可省略或为空以兼容现有 BOSS；非空返回 `422 search_filters_not_supported`，不能静默下推。

创建规则：

1. `platform` 与 scope 平台一致，且平台允许新任务。
2. script params 与 scope 的关键词、规范城市和页数一致。
3. 后端冻结平台城市码、映射版本、账号、平台 CDP 端口、`profile_key` 和搜索阶段 `task_input_digest`。
4. 搜索 run 的 `frozen_filters` 和筛选快照为空。
5. run 创建后先转 `running` 再执行浏览器启动/preflight；真实人工阻断才转 `paused`。
6. UI 切换不影响已创建 run。

保持现有成功状态 `200`：

```json
{
  "ok": true,
  "task_id": "run-id",
  "platform": "zhilian",
  "config_digest": "sha256-hex",
  "scope_digest": "sha256-hex",
  "task_input_digest": "sha256-hex",
  "task_size": "small"
}
```

同步错误：`409 scope_platform_mismatch`、`409 scope_preview_required`、`409 scope_request_mismatch`、`422 search_filters_not_supported`、`422 city_mapping_missing`、`503 platform_disabled`。

run 建立后的登录、验证、限流、封禁或 CDP 阻断通过任务状态返回。未知 profile 占用端口时进入 `paused/source_cdp_unavailable`，不能关闭未知浏览器。

## POST `/api/ai-screen`

AI 筛选才提交平台筛选值和 schema 版本：

```json
{
  "platform": "zhilian",
  "filter_schema_version": 1,
  "screening_fields": {
    "company_nature": ["verified-stable-value"]
  },
  "profile_summary": "候选人摘要",
  "scrape_task_id": "scrape-run-id"
}
```

权威平台来自父搜索 run；客户端省略平台只为兼容继承，显式不一致返回 `409 parent_platform_mismatch`。父 run 必须已进入允许 AI 的完成状态。筛选键和值按父平台 schema 校验，服务端根据稳定值补入当时标签，形成完整筛选快照。

AI run 继承父 run 的平台、scope、账号、端口和 profile key，生成 AI 阶段 `task_input_digest`。成功保持现有 `200`，增加 `platform`、`filter_schema_version` 和 `task_input_digest`。

错误：`409 parent_platform_mismatch`、`409 parent_run_not_ready`、`409 filter_schema_version_mismatch`、`422 filter_validation_failed`、`503 platform_disabled`。

## POST `/api/analyze-resume`

multipart form 新前端显式提交 `platform=boss|zhilian`；省略平台只兼容旧 BOSS 客户端。简历分析产生建议，不发布 schema。响应返回 `platform`、`filter_schema_version`，以及按该平台 schema 投影后的关键词、规范城市和 AI 筛选建议。

后端以请求平台的本地 schema 约束 AI 输出：只保留 schema 中存在且值可解释的 AI 筛选建议，城市只保留当前平台目录可解释的规范名。智联响应不含 `stage`，BOSS 不含 `company_nature`。若迁移期仍返回 `labels`，其内容必须从与响应版本相同的平台注册表 schema 派生；前端不得用分析响应替换已加载 schema。错误：`400 platform_validation_failed`、`503 platform_schema_unavailable`。

## GET `/api/task-state/{run_id}`

状态响应增加 `platform`、`scope_digest`、`task_input_digest`、`source_summary` 和每个搜索组合最新的安全 `source_outcomes`。暂停响应包含稳定 `error_code` 与用户可见 `error_reason`。内存任务与 DB run 的平台或输入摘要不一致时返回 `409 run_identity_conflict` 并停止推进。`source_outcomes` 必须来自持久化的 `screening_source_attempts`，不得由岗位数为 0 或内存返回值反推真实空结果。

## GET `/api/search-progress/{run_id}`

当前轮询入口与 `/api/task-state/{run_id}` 使用相同 run 身份。响应至少返回 `run_id`、`kind`、`status`、`platform`、`task_input_digest`、`source_summary`、`source_outcomes`、进度、日志安全尾部和可用结果。纯内存创建窗口必须在注册 task 时已经冻结平台和运行配置；若内存与 DB 不一致返回 `409 run_identity_conflict`。不得因内存 task 缺平台而补成 BOSS。

### 公共状态映射

所有 screening run 接口统一使用下表，不允许同一响应在 `completed/succeeded` 或 `cancelled/interrupted` 间任选其一：

| DB canonical | API `status` | 说明 |
| --- | --- | --- |
| `queued` | `queued` | 非终态 |
| `running` | `running` | 非终态 |
| `paused` | `paused` | 非终态，可继续 |
| `succeeded` | `completed` | 成功终态；旧结果文件/快照的 `done` 只在兼容边界保留 |
| `partial` | `completed_with_pending` | 部分成功终态 |
| `failed` | `failed` | 失败终态 |
| `interrupted + user_cancelled` | `cancelled` | 用户取消终态 |
| `interrupted + process_restart/operator_stop` | `interrupted` | 非终态，可恢复或提前结束 |

后端持久化只写 DB canonical 值及 `interruption_kind`；前端、`/api/latest-running-task`、状态/进度、取消、提前结束和最近结果都使用同一映射。`source` 字段若出现，只表示状态数据来自内存或数据库，不能表示招聘平台。

## GET `/api/latest-running-task`

所有 `has_task=true` 响应增加 `platform` 和 `task_input_digest`。数据库 paused/interrupted 从 `screening_runs` 读取；内存任务读取注册时冻结值。历史 `source: database` 继续表示状态数据来源，不能承载招聘平台。

恢复 UI 时先恢复任务自身平台和 schema，再投影筛选快照；当前草稿平台不参与。

## POST `/api/task/continue/{run_id}`

请求体为空，平台不由客户端选择：

1. 读取 `screening_runs.platform`。
2. 校验 execution params、`task_input_digest`、scope/城市映射快照、AI 筛选快照、checkpoint 与父任务一致。
3. 按冻结 `browser_account/cdp_port/profile_key` 创建原平台 adapter。
4. 被禁用的平台返回 `503 platform_disabled` 并保持原状态，不切换 BOSS。
5. 对原平台 preflight；阻断未解除返回 `409 block_not_resolved`，保持 paused。
6. 原子 claim 只允许一个请求从 paused 进入 running。

成功保持现有 `200`，响应增加 `platform` 和 `task_input_digest`。

## POST `/api/task/cancel/{run_id}` 与阶段取消入口

本合同同时约束 `/api/execute-search/{run_id}/cancel`、`/api/ai-screen/{run_id}/cancel` 和通用取消入口：

1. DB run 存在时以其 `platform` 和冻结运行配置为权威；仅处于 DB 创建前内存窗口时使用注册 task 的不可变平台快照。
2. 先提交 durable cancel，再发布内存终态；失败时不伪造已取消。
3. 只向目标 run 的 stop event 发信号；释放浏览器时显式传入该 run 的 `cdp_port` 并校验 `profile_key`。
4. 目标 profile 不明、端口由另一平台或未知 profile 占用时不关闭浏览器，返回 `409 login_space_conflict`；取消状态和已完成结果仍可保留。
5. 成功响应返回 `run_id`、`platform`、`status` 和已处理计数。

durable cancel 在同一事务写入 `status=interrupted` 与 `interruption_kind=user_cancelled`，公共响应 `status=cancelled`。客户端不提交平台；若迁移期提交，只做一致性校验。

## POST `/api/task/finish/{run_id}`

仅允许 paused，或 `status=interrupted` 且 `interruption_kind` 为 `process_restart/operator_stop` 的 run；`user_cancelled` 是终态，不能通过 finish 改写。部分结果从目标 run、父 run 和 checkpoint 继承平台，生成的 snapshot 必须保持同一 `platform`、两类岗位 ID 和完整岗位字段。保存结果后只关闭目标 run 冻结的已知 CDP/profile。响应返回原 `run_id`、`snapshot_run_id`、`platform` 和 `status=completed_with_pending`；任何来源平台、岗位 payload 或浏览器身份错配返回 `409 run_identity_conflict`，不得生成部分快照。

## POST `/api/reset-latest-result`

首选请求携带 `run_id`。目标必须是已完成或部分完成的 result snapshot；仅删除该 run 及数据库外键定义的临时结果/断点行，不删除 `jobs` 主体、收藏、反馈、其它 run、正在执行的任务或来源 search run 的 `screening_source_attempts` 审计记录。成功响应返回 `cleared`、实际 `run_id` 与 `platform`。

无 `run_id` 只兼容旧客户端，目标固定为全局最近一个已完成结果，仍返回实际 run 与平台。请求中的 `platform` 只能校验目标；不得按当前草稿平台选择或扩大清理范围。错误：`404 result_not_found`、`409 result_not_clearable`、`409 run_platform_conflict`。

## POST `/api/pipeline/recrawl` 与继续补抓

补抓平台从必填 `source_run_id` 和目标岗位继承。目标必须是该 run 的待确认岗位且属于同一平台；客户端平台只做一致性校验。混入不同平台返回 `409 mixed_platform_jobs`，不属于待确认集合返回 `409 non_pending_platform_job_ids`。新补抓受 `enabled_for_new_tasks` 约束，断点键和请求数组统一使用 `platform_job_id`，不得把内部 UUID 传给 source adapter。

`/api/recrawl/continue/{run_id}` 与通用 continue 使用补抓 run 自身冻结配置；禁止从当前 UI、全局活动账号或全局最近结果重建 source。

## POST `/api/job-detail`

按需加载结果卡片 JD 的请求必须含 `source_run_id` 与 `platform_job_id`；可携带 `canonical_url` 供一致性校验，但后端以来源 run 保存的岗位快照为权威。后端按 run 冻结的 `platform/browser_account/cdp_port/profile_key` 创建 adapter，并校验 URL、平台 ID 和结果快照一致。

缺少来源 run、岗位不属于该 run、平台或 URL 错配时返回 `409 run_identity_conflict` 或 `422 platform_url_mismatch`。不得只凭客户端 URL、当前草稿平台或最新结果调用默认 BOSS source。平台级阻断按 JobSource 错误矩阵返回并关联原 run；成功响应返回 `platform`、`platform_job_id` 和 JD。

## POST `/api/pipeline/jobs/{platform_job_id}/jd`

路径参数语义固定为平台岗位 ID，不是内部 UUID。请求必须含 `source_run_id`；若岗位已经落库可另带内部 `job_id`，后端校验其指向同一 `(platform, platform_job_id)`。单项补抓创建的子 run 从 source run 复制平台、scope digest、账号、端口、profile key 和任务输入摘要。成功回写目标 result snapshot；需要落库时按双索引合同写入，不扫描或改写另一平台最近结果。

## GET `/api/latest-pipeline-result`

查询语义：

- 无参数：保持现有全局最近一个单平台结果。
- `platform=boss|zhilian`：该平台最近结果。
- `run_id=<id>`：精确 result snapshot。
- 同时提供 run 与 platform 时必须相符，否则 `409 run_platform_conflict`。

无结果返回 `200` 且 `has_result=false`。有结果时返回来源 search run 汇总出的 `source_summary` 和 `source_outcomes`；若来源 run 已按保留策略删除，则显式返回 `source_evidence_available=false`，不得把缺证据解释成真实空结果。当前 UI 草稿平台不得改写响应来源。

岗位对象合同：

```json
{
  "platform": "zhilian",
  "platform_job_id": "platform-stable-id",
  "job_id": null,
  "title": "Python 后端工程师",
  "company": "示例公司",
  "salary": "20-30K",
  "location": "上海",
  "experience": "3-5年",
  "degree": "本科",
  "jd": "",
  "canonical_url": "https://www.zhaopin.com/jobdetail/platform-stable-id.htm",
  "extra": {"company_nature_label": "民营"}
}
```

未落库结果为 `job_id:null`；已落库后同时返回平台 ID 和内部 UUID。任何接口不得把平台 ID 填进 `job_id`。`dropped[]` 使用相同身份字段和可用岗位快照。

## Pipeline 收藏、拒绝和撤销

现有 `/api/pipeline/jobs/interest`、`/api/pipeline/jobs/reject` 和撤销入口继续存在。请求岗位必须含 `platform`、`platform_job_id`、规范链接和展示快照：

1. 后端按 [data-model.md](../data-model.md) 的双索引算法在一个事务内 upsert `jobs`。
2. 成功后使用 `jobs.id` 写 `profile_jobs`/`feedback_events`，响应 `job_id` 只表示内部 UUID。
3. 两个身份索引冲突返回 `409 job_identity_conflict`，不改变收藏/反馈。
4. 平台与 URL host 不一致返回 `422 platform_url_mismatch`。
5. 撤销优先使用已返回的内部 `job_id`；兼容旧请求时仍可按同一岗位快照落库解析，不能把 `platform_job_id` 当内部 UUID。

## 其它岗位与反馈接口

`GET /api/favorites`、profile 岗位卡片、任务结果、待确认、垃圾桶和补抓结果均返回 `platform`、`platform_job_id`、内部 `job_id` 及经验/学历/extra。反馈写入只接受内部 UUID；读取岗位后校验平台关系。前端只打开后端规范化的 `canonical_url`。

## 浏览器账号接口

### GET `/api/browser-accounts`

每个账号返回平台登录空间的非敏感投影：

```json
{
  "id": "a",
  "name": "账号 A",
  "platforms": {
    "boss": {"cdp_port": 9222},
    "zhilian": {"cdp_port": 9223}
  }
}
```

不得返回 profile 路径或路径摘要。

### POST `/api/browser-accounts/{account_id}/activate`

只更新新任务使用的 `browser_account` 草稿，不打开或关闭任何浏览器，也不修改运行中、暂停中、历史 run 或调优 experiment 的冻结账号。响应返回 `active_account`；平台不属于 activate 状态。

### POST `/api/browser-accounts/{account_id}/open`

请求 `{"platform":"zhilian"}`；省略平台只兼容旧 BOSS 客户端。同平台账号共享端口并按现有规则受控替换已知 profile；未知 profile 占用返回 `409 login_space_conflict`，不得自动关闭。运行/暂停任务锁定时继续使用 `409 browser_busy`，details 包含 `locked_platform`。

### DELETE `/api/browser-accounts/{account_id}`

删除前原子检查：所有 running/queued/paused run 与调优实验租约、BOSS 9222 上该账号基础 profile、智联 9223 上 `.zhilian` 派生 profile，以及两个端口上的未知 profile。任一命中返回 `409 browser_in_use`、`browser_busy` 或 `login_space_conflict`，不删除账号记录或任一目录。检查通过后才删除账号配置；profile 目录的保留/删除沿用现有账号删除合同，但两个平台必须作为一个原子账号生命周期处理。

## GET `/api/check?platform={platform}`

升级为当前主工作台的只读平台 preflight：显式平台解析对应登录空间，返回 `platform`、`connected` 和安全错误码，不返回原始命令输出中的敏感内容。省略平台只兼容 BOSS。智联检查不得调用旧 BOSS scraper。旧 `/api/setup-chrome` 仍属于下方 BOSS-only 矩阵；新前端通过 browser account open 打开登录空间。

## 调优实验接口

`POST /api/tuning/experiments` 的 `source_scope` 必须显式含 `platform`；服务端解析并冻结规范城市、平台城市码、映射版本、`browser_account/cdp_port/profile_key`、filter schema 版本和 `task_input_digest`。`quality_context.screening_fields` 按该平台 schema 校验。新 experiment 不允许省略平台；禁用平台返回 `503 platform_disabled`。

所有 experiment、round、manifest、evidence 查询响应增加 `platform`。签发 manifest 时，`fixed_fields` 与 `frozen_input` 必须同 experiment/workload 的完整平台快照一致；`manifest_digest` 覆盖这些字段。执行时：

1. `stage` 保持现有五种轮次值 `list/detail/rough/fine/end_to_end`；`list/detail/end_to_end` 从 manifest 冻结运行配置创建对应 adapter。
2. 数据库外层 artifact 与 artifact JSON 同时保存 `platform/stage/source_artifact_kind/scope_digest/task_input_digest`。只有 `stage=list, source_artifact_kind=list` 和 `stage=detail, source_artifact_kind=detail` 是可复用 source artifact；`end_to_end` 的 `source_artifact_kind` 必须为 NULL。
3. `rough` 只接受 list source artifact，`fine` 只接受 detail source artifact；二者校验平台和筛选 schema，不创建或读取全局 source。
4. experiment、workload、artifact、manifest 外层列、manifest JSON 和 program evidence 任一错配返回 `422 manifest_validation_failed` 或 `409 tuning_platform_mismatch`，在真实 source/AI 调用前阻断。
5. 平台被禁用后不签发或执行需要 source 的新轮次；历史证据保持可读。存量 artifact/manifest 只按 data model 的迁移前 BOSS 证明算法解释，不能猜填摘要或补写为智联。
6. 取消实验只关闭该 experiment 冻结的已知平台登录空间；共享租约与普通 run 的浏览器锁继续生效。

## Legacy BOSS-only 矩阵

legacy 平台参数位置固定：POST 使用 JSON body 的 `platform`；GET 使用 query 的 `platform`。省略或显式 `boss` 走既有 BOSS 行为；显式 `zhilian` 在任务/对象查找和任何副作用前返回 `422 legacy_platform_not_supported`；其它值返回 `400 platform_validation_failed`。不得从 URL、任务标题或当前 UI 猜平台。

| 路由 | 方法/参数位置 | `zhilian` 行为 | `boss` 或省略行为 |
| --- | --- | --- | --- |
| `/api/tasks` | GET query；POST body | GET/POST 均 422 | 保持列表/创建，响应任务标识 BOSS |
| `/api/scrape` | POST body | 422 | 保持旧创建别名 |
| `/api/setup-chrome` | POST body | 422；智联改走 browser account open | 保持旧 BOSS 启动 |
| `/api/tasks/{id}` | GET query | 422，且不读取任务 | 只读旧 BOSS TaskRunner 任务 |
| `/api/tasks/{id}/cancel` | POST body | 422，且不触发 cancel event/状态写入 | 操作旧 BOSS 任务 |
| `/api/tasks/{id}/retry` | POST body | 422，且不创建 retry 任务或 artifact | 操作旧 BOSS 任务 |
| `/api/tasks/{id}/result`、`summary`、`export` | GET query | 422，且不生成导出或 AI 摘要副作用 | 只读/生成旧 BOSS 响应，标识 BOSS |
| `/api/results` | GET query | 422 | 只列旧 BOSS 命名产物，标识 BOSS |
| `/api/confirm-fields` | POST body | 422 | 保持已废弃 BOSS-only 兼容 |
| `/api/search-runs` | POST body | 422，且不创建 workbench run/query/event | 保持现有 BOSS workbench 创建 |
| `/api/search-runs/{id}`、`jobs` | GET query | 422，且不读取 run/jobs | 只读既有 BOSS workbench 数据，标识 BOSS |
| `/api/search-runs/{id}/cancel` | POST body | 422，且不触发 stop event/状态写入 | 取消既有 BOSS workbench run |

零副作用检查至少覆盖相关数据库表行数与内容、任务/查询/事件、结果与导出 artifact、BOSS 结果文件、浏览器进程、两个平台 profile 的文件系统快照和后台任务注册表。错误响应不得读取或打开智联 profile。所有 legacy 成功响应中的任务、run、岗位或结果对象补充 `platform=boss`；该标识不把这些链路升级成多平台主链。

## HTTP 状态摘要

| 状态 | 用途 |
| --- | --- |
| `200` | 查询、预览、后台任务创建、继续同步接收，保持现有接口兼容 |
| `201` | 浏览器账号创建 |
| `400` | 请求结构或平台键错误 |
| `404` | run、账号或内部岗位不存在 |
| `409` | scope、父平台、版本、岗位身份、登录空间或恢复状态冲突 |
| `422` | scope、城市、搜索/AI 筛选或 URL 语义校验失败 |
| `503` | 平台新任务禁用、schema/城市映射、迁移或本地运行依赖不可用 |

## 稳定错误码补充

- `platform_validation_failed`
- `platform_disabled`
- `platform_schema_unavailable`
- `filter_schema_version_mismatch`
- `filter_snapshot_incompatible`
- `city_mapping_unavailable`
- `city_mapping_missing`
- `search_filters_not_supported`
- `job_identity_conflict`
- `platform_url_mismatch`
- `task_input_mismatch`
- `run_identity_conflict`
- `mixed_platform_jobs`
- `non_pending_platform_job_ids`
- `result_not_clearable`
- `login_space_conflict`
- `legacy_platform_not_supported`
- `tuning_platform_mismatch`
- `migration_backup_failed`
- `migration_failed`
