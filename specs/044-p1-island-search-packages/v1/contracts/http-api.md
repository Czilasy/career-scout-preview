# HTTP Contract: Search Packages

所有端点返回 JSON。错误统一使用：

```json
{
  "error": {
    "code": "package_unusable",
    "message": "这套配置无法完整读取，请重新保存"
  }
}
```

`message` 可直接显示给用户，但不得包含 SQL、路径、堆栈或原始损坏数据。

## GET /api/search-packages

返回全部包的轻量列表，默认按最近更新优先。

```json
{
  "items": [
    {
      "id": "uuid",
      "name": "产品经理 · 上海",
      "createdAt": "2026-09-21T10:00:00Z",
      "updatedAt": "2026-09-21T10:00:00Z"
    }
  ]
}
```

## GET /api/search-packages/{id}

返回经服务层完整校验的包。成功为 `200`；不存在为 `404 package_not_found`；JSON 损坏、版本不支持或字段不完整为 `409 package_unusable`。

## POST /api/search-packages

保存当前第二页状态，每次都创建一套新的配置包。请求体：

```json
{
  "name": "可省略或为空，由服务端生成默认名称",
  "payloadVersion": 1,
  "keywords": {
    "candidates": [{ "word": "产品经理", "recommended": true }],
    "selected": ["产品经理"],
    "custom": ""
  },
  "city": { "text": "上海", "custom": "" },
  "profile": { "summary": "...", "facts": {} }
}
```

成功返回 `201` 和完整包。请求非法返回 `400 invalid_package`。名称为空时由服务端生成可编辑默认名。

## PATCH /api/search-packages/{id}/name

请求：`{ "name": "新名称" }`。只修改名称和 `updatedAt`，其它内容字节语义不变。空名称或超过 80 字符返回 `400 invalid_name`。

## DELETE /api/search-packages/{id}

仅在前端完成二次确认后调用。成功返回 `204`；不存在返回 `404`。后端不删除任何画像、任务或搜索结果。

## Non-Goals

- 无平台参数。
- 无第三页筛选字段。
- 无自动保存端点或分析完成回调。
- 无导入、导出、共享、历史版本或恢复端点。
