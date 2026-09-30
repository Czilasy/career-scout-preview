# Contract: 条件草稿、提交与冻结快照

## 1. 映射版本

V2 固定版本标识：`b096-v2-2026-09-27`。版本只说明保存时采用哪张人工表；恢复不得据此重新计算。

## 2. 前端快照结构

```json
{
  "snapshotVersion": 2,
  "mappingVersion": "b096-v2-2026-09-27",
  "unifiedValues": {
    "salary": [], "experience": [], "degree": [],
    "industry": [], "scale": [], "recruiter_activity": []
  },
  "platformValues": { "boss": {}, "zhilian": {} },
  "overrides": { "boss": {}, "zhilian": {} },
  "exclusiveValues": {
    "boss": { "stage": [] },
    "zhilian": { "company_nature": [] }
  }
}
```

数组必须去重；字段不存在与空数组统一归一为空数组。

## 3. `POST /api/flows`

```json
{
  "profile_id": "...",
  "selection": "all",
  "start_key": "...",
  "confirmed_filters": {
    "boss": { "snapshotVersion": 2, "...": "ConditionSnapshotV2" },
    "zhilian": { "snapshotVersion": 2, "...": "ConditionSnapshotV2" }
  }
}
```

- 不再发送或接收 `confirmed`。
- 不再返回 `confirmations_required`。
- 主启动按钮提交即表示按当前快照启动。
- `selection=boss|zhilian` 的既有单平台入口继续允许旧纯字段对象。

## 4. `POST /api/execute-search`

`auto_screen_fields` 只发送当前平台的 `platformValues[platform]`，不得把快照元数据送入平台筛选校验。

暂停恢复依次读取：`submission_snapshot.auto_screen_fields`；V2 信封的 `platformValues[platform]`；V1 纯字段对象。

## 5. 常用配置 API

`POST /api/search-packages` version 2 增加：

```json
{
  "payloadVersion": 2,
  "conditions": { "snapshotVersion": 2, "...": "ConditionSnapshotV2" }
}
```

version 1 不要求 `conditions`，前端恢复为空白条件且不改写原包。

## 6. 映射错误

如果冻结表指定的平台标签在当前 schema 中不存在：不做模糊匹配、不静默丢选项；“全部”主按钮不可启动；提示“平台筛选条件已变化，请更新后重试”并标明平台；单平台仍可手动使用当前 schema。
