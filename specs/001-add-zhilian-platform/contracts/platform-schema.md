# 平台注册表与 AI 筛选 Schema 合同

**版本**：`platform-registry-v1`  
**适用范围**：后端平台注册、前端平台切换、AI 筛选校验、城市解析、链接安全和浏览器运行配置

## 注册表合同

`webui/platforms.py` 是平台能力的唯一权威来源。每个平台注册项必须提供：

```text
key                    稳定平台键：boss | zhilian
display_name           用户可见名称
filter_schema          版本化本地 AI 筛选 schema
city_catalog           规范城市到本平台城市码的版本化本地映射
enabled_for_new_tasks  新执行入口是否可用
availability_reason    禁用或不可用的稳定原因
default_cdp_port       平台默认 CDP 端口
resolve_login_space    (browser_account) -> 后端本地运行配置
normalize_job_url      (raw_url) -> normalized_url | None
source_factory         (frozen_runtime) -> JobSource
```

调用方不得在 `app.py`、`core.py`、Vue 或 pipeline 中维护第二套平台字段、城市码、域名、端口或 profile 派生规则。平台未知、能力缺失、schema 或城市目录不可用时，必须在创建执行前失败，不能回退成 BOSS。

## 平台列表与启用语义

`GET /api/platforms` 的注册表投影见 [http-api.md](http-api.md)。首期注册表恰好包含 `boss` 与 `zhilian`；禁用项仍需返回，以便历史数据显示来源。

| key | display_name | 默认端口 | AI 专属字段 |
| --- | --- | --- | --- |
| `boss` | BOSS直聘 | `9222` | `stage` |
| `zhilian` | 智联招聘 | `9223` | `company_nature` |

`enabled_for_new_tasks=false` 时：

- 阻止该平台的新搜索、AI 筛选、补抓和其它需要新建 run 的入口；
- 已有任务若继续时需要重新进入已禁用 adapter，返回 `platform_disabled` 并保留原状态和数据；
- 历史任务、结果、岗位、收藏、反馈和平台来源仍可读取；
- 不删除 profile，不把既有任务改成 BOSS，不允许旧数据库覆盖已有平台数据。

智联只有在公司性质 schema、城市映射、页面结构、空结果和风控 fixture 均完成真实核验后才能启用。

## AI 筛选 Schema

该 schema 只服务 `/api/ai-screen` 的显示、校验、冻结和恢复，不是列表搜索参数。`/api/execute-search` 不读取或下推其中任何字段。

规范投影：

```json
{
  "ok": true,
  "platform": "zhilian",
  "schema_version": 1,
  "enabled_for_new_tasks": true,
  "fields": [
    {
      "key": "salary",
      "label": "薪资范围",
      "multiple": true,
      "options": []
    },
    {
      "key": "company_nature",
      "label": "公司性质",
      "multiple": true,
      "options": []
    }
  ]
}
```

示例空数组不代表正式可用 schema。启用时每个需要选项的字段都必须由版本化本地 fixture/注册表返回非空、已验证的稳定值与标签。任务创建和恢复不得依赖当天在线元数据接口；在线侦察只用于实施时更新 fixture 和版本。

### 字段集合

| platform | 必须按顺序出现的字段 | 禁止字段 |
| --- | --- | --- |
| `boss` | `salary`, `experience`, `degree`, `industry`, `scale`, `stage` | `company_nature` |
| `zhilian` | `salary`, `experience`, `degree`, `industry`, `scale`, `company_nature` | `stage` |

### 字段与选项规则

- `schema_version` 为正整数；字段、稳定值语义或标签映射变化时递增。
- 字段 `key` 在单个 schema 内唯一；`label` 非空；`multiple` 明确。
- 每个 option 包含非空稳定 `value` 和当时用户可见 `label`；同一字段内 value 唯一。
- 稳定 value 是应用长期语义，不直接等于智联网页临时编码。网页编码只存在于 adapter/fixture 的内部映射中。
- 新 AI run 只接受父平台当前 schema 的键和值；跨平台字段返回 `422 filter_validation_failed`。
- 当前界面展示、请求 allowlist、服务端校验和冻结快照必须来自同一个 schema 对象。
- schema 不可用时返回 `platform_schema_unavailable`，不得显示一个可提交但无权威选项的字段。

### 简历分析建议投影

`/api/analyze-resume` 只生成新任务草稿建议，不发布或修改平台注册表：

1. 请求必须显式携带当前新任务草稿的 `platform`；省略只兼容旧 BOSS 客户端。
2. 后端取得该平台本地 schema，以字段 allowlist 和稳定 option value 投影 AI 建议。
3. 智联建议不得出现 `stage`，BOSS 建议不得出现 `company_nature`；未知字段和无法解释的值直接丢弃并记录安全计数。
4. 响应返回 `platform` 与 `schema_version`，前端只有在它们仍与当前草稿平台及已加载 schema 一致时才能应用建议。
5. 分析响应不得覆盖平台注册表发布的字段顺序、标签、选项或版本。迁移期返回的 `labels` 也必须从同一平台注册项派生，而不是相信模型输出。

分析建议只能更新关键词、规范城市和当前平台允许的 AI 筛选草稿；不能改写运行中任务、历史任务、最近结果或其它平台的专属草稿。

### 完整冻结快照

AI run 保存 schema 版本、平台以及每个已选字段的稳定值和当时标签：

```json
{
  "schema_version": 1,
  "platform": "zhilian",
  "fields": {
    "company_nature": {
      "values": ["stable-value"],
      "labels": ["当时标签"]
    }
  }
}
```

值与标签数组按同一顺序一一对应。恢复优先使用任务自身快照解释历史选择，再验证当前代码仍支持旧 schema；无法解释时返回 `filter_snapshot_incompatible` 并阻断，不能删除旧字段或替换标签继续。

## 请求投影规则

### 搜索任务

搜索创建只校验平台、关键词、规范城市和页数。AI 筛选 schema 不进入搜索 run，搜索筛选对象必须为空或省略。

### AI 筛选任务

AI 筛选任务按如下顺序创建：

1. 从父搜索 run 读取权威 `platform`，拒绝客户端平台错配。
2. 获取该平台当前本地 AI schema。
3. 校验客户端 `filter_schema_version`。
4. 按 schema allowlist 校验字段和值。
5. 服务端补入对应标签，形成完整冻结快照。
6. 从父 run 继承 scope、浏览器账号、端口和 profile key，写入 AI run 与输入摘要。

切换平台时保留关键词、城市、页数和公共 AI 筛选草稿；`stage` 与 `company_nature` 分平台保存。当前选择只影响新任务草稿，不改写运行中、暂停中或历史任务。

## 前端平台状态与异步加载

前端必须分别管理以下身份，不能复用一个“当前平台”变量互相改写：

| 身份 | 权威来源 | 可影响范围 |
| --- | --- | --- |
| 新任务草稿平台 | 用户分段控件 | 新 scope、简历建议和新任务表单 |
| 非终态任务平台 | 目标 run 状态 | 进度、取消、继续、提前结束和任务筛选快照 |
| 最近结果平台 | 结果 snapshot | 结果卡片、单 JD、补抓、收藏和反馈 |

`/api/filter-labels`、`/api/options` 和 `/api/analyze-resume` 的平台相关请求必须使用单调请求序号、`AbortController` 或等效机制：

1. 发出请求时捕获目标平台和请求版本。
2. 响应返回后同时校验请求仍为该资源的最新请求、响应平台与目标平台一致。
3. 任一校验失败时丢弃响应，不更新 schema、城市、筛选草稿、加载状态或错误状态。
4. 快速切换导致旧请求被取消不显示为当前平台错误。
5. 恢复 `running`、`queued`、`paused` 或 `interrupted` 任务时，先从任务响应设置任务平台，再加载对应 schema/城市并投影冻结快照；不得先按草稿平台加载后再修正。

最近结果加载使用结果自身返回的 `platform`，不触发新任务草稿切换。反过来，用户切换草稿平台也不重新标记已展示结果。

## 城市目录合同

每个平台维护独立、版本化的本地城市目录。目录条目包含：

```json
{
  "name": "上海",
  "label": "上海",
  "platform_code": "verified-city-code",
  "mapping_version": 1
}
```

- UI、scope、请求和数据库使用规范 `name`，不接触平台城市码。
- adapter 只从当前平台注册项解析 `platform_code`，并把规范名、标签、平台码和映射版本冻结到任务。
- 智联 `全国` 的平台码固定为 `jl0`；`全国` 与具体城市互斥。
- 其它智联城市码必须由当前真实页面或其真实元数据核验后进入脱敏 fixture。
- 当前平台缺少映射时返回 `city_mapping_missing`；整个目录不可用时返回 `city_mapping_unavailable`。
- 禁止将 BOSS 城市码发送给智联，或在恢复时用新版在线映射静默替换任务快照。

## 浏览器登录空间

现有浏览器账号配置中的 `profile_dir` 是 BOSS 基础 profile。平台注册表按下列规则确定性解析：

| platform | profile_dir | profile_key | 默认端口 |
| --- | --- | --- | --- |
| `boss` | 现有账号 `profile_dir` 原值 | `boss:<browser_account>` | `9222` |
| `zhilian` | 在 BOSS profile 路径字符串末尾追加 `.zhilian` | `zhilian:<browser_account>` | `9223` |

创建任务时必须校验两个解析后的绝对路径不同，并冻结 `platform`、账号、端口和 profile key。绝对路径只在后端运行时存在，不写数据库、日志或用户 API。

同平台多个账号共享该平台端口并沿用现有受控切换：仅允许替换注册表可确认的已知 profile；未知 profile 占用时返回 `login_space_conflict`，不得关闭未知浏览器。运行或暂停任务持有账号锁时继续返回 `browser_busy`。

## URL 规范化

### BOSS

保持现有 HTTPS-only BOSS 规则和 host 白名单，不因智联接入放宽。

### 智联

允许：

- host 恰为 `zhaopin.com` 或 `www.zhaopin.com`；
- path 符合岗位详情合同 `jobdetail/<id>.htm`；
- 输入为 `http` 时升级为 `https`；
- 移除 query 和 fragment。

拒绝：

- 用户信息、非标准端口或非 HTTP(S) scheme；
- 相似域名、任意其它子域或非岗位详情 path；
- 声明平台与 URL host 不匹配；
- 无法取得非空平台岗位 ID 的详情链接。

若真实页面重定向到新的官方详情域名或路径，必须先增加脱敏 fixture 和安全测试，再修改 allowlist。旧任务仍按其冻结平台解释，但打开链接时使用当前安全规则重新校验。

## 日志与错误

平台注册表和 adapter 可记录 `platform`、`stage`、计数、稳定错误码、是否存在平台岗位 ID 和 URL host。不得记录 JD 正文、Cookie、简历内容、API Key、本地 profile 路径或路径摘要。

稳定平台注册错误包括 `platform_validation_failed`、`platform_disabled`、`platform_schema_unavailable`、`city_mapping_unavailable`、`city_mapping_missing`、`filter_schema_version_mismatch`、`filter_snapshot_incompatible`、`login_space_conflict`。source 运行错误按 [job-source.md](job-source.md) 的冻结矩阵处理。
