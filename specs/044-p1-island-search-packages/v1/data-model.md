# Data Model: P1 灵动岛分析态与常用搜索配置包

## UI State: Island Analysis State

本项不持久化，只扩充既有灵动岛状态投影：

| 字段 | 语义 | 约束 |
|---|---|---|
| `contentKey` | 当前胶囊可见内容的稳定身份 | “分析中”必须有独立值，进入和离开均发生变化 |
| `tone/class` | 状态点视觉类别 | 必须有静态可见颜色，不依赖动画才能辨认 |

不新增状态机、不改变分析任务生命周期，也不改变通知数据模型。

## Entity: SearchPackage

本地 SQLite 中的一套平台无关搜索身份快照。

| 字段 | SQLite 类型 | API 类型 | 规则 |
|---|---|---|---|
| `id` | `TEXT PRIMARY KEY` | `string` | 服务端生成 UUID，不复用 |
| `name` | `TEXT NOT NULL` | `string` | trim 后 1–80 字符；可重名 |
| `payload_version` | `INTEGER NOT NULL` | `number` | 当前仅接受 `1` |
| `keywords_json` | `TEXT NOT NULL` | `SearchPackageKeywords` | 合法 JSON，见下文 |
| `city_json` | `TEXT NOT NULL` | `SearchPackageCity` | 合法 JSON，见下文 |
| `profile_summary` | `TEXT NOT NULL` | `string` | 第二页画像文本，可为空但字段必须存在 |
| `profile_facts_json` | `TEXT NOT NULL` | `Record<string, unknown>` | 合法 JSON object，不接受数组或 null |
| `created_at` | `TEXT NOT NULL` | ISO 8601 string | 创建时写入 |
| `updated_at` | `TEXT NOT NULL` | ISO 8601 string | 内容或名称变化时更新 |

索引：`(updated_at DESC, created_at DESC)`，用于默认列表顺序。

## Value Object: SearchPackageKeywords

```text
{
  candidates: [{ word: string, recommended: boolean }],
  selected: string[],
  custom: string
}
```

规则：

- `candidates`、`selected` 必须为数组；字符串 trim 后去空项，保持顺序并去重。
- `selected` 可以包含自定义关键词；不得在读取时替用户补选。
- `custom` 保留输入框中尚未提交的文本。

## Value Object: SearchPackageCity

```text
{
  text: string,
  custom: string
}
```

规则：

- `text` 是第二页通用城市文本，不包含平台区县或商圈码。
- `custom` 保留城市输入框中尚未提交的文本。

## Value Object: SearchPackageProfile

API 组合表示：

```text
{
  summary: string,
  facts: Record<string, unknown>
}
```

规则：

- `facts` 是简历分析后用于恢复画像能力的结构化事实。
- 不允许保存 `filterValues`、平台筛选 schema、平台名称、区县/商圈码或整个原始 `resumeAnalysis`。

## API Projection

列表项：

```text
{ id, name, createdAt, updatedAt }
```

完整包：

```text
{
  id,
  name,
  payloadVersion,
  keywords,
  city,
  profile,
  createdAt,
  updatedAt
}
```

## State Transitions

```text
不存在 --POST--> 已保存
已保存 --POST(再次保存)--> 原包保留 + 新 ID
已保存 --PATCH name--> 仅名称与 updated_at 改变
已保存 --DELETE(confirm 后)--> 不存在
已保存 --GET+validate--> 可应用 | 不可用
```

## Invariants

- 表中没有 `platform` 或 `profile_id` 字段。
- 一个写请求在单一事务内完成；失败不留下半包。
- 读取必须解析并校验所有快照字段后才返回完整包。
- 删除只删除目标 ID；无级联到画像、任务或搜索结果。
- 配置包从不自动创建，数据库层也没有分析完成/搜索开始触发器。
