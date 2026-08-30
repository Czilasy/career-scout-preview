# Contract: `/api/theme` 主题偏好接口（值域扩展）

**Date**: 2026-08-30 | **变更性质**: 既有接口值域扩展，路径/方法/响应结构不变

## GET /api/theme

响应（不变）：

```json
{ "ok": true, "mode": "light" | "dark" | "kaleido" }
```

- 读取主题偏好文件；`mode` 值域由二值扩展为三值
- 文件缺失/损坏/值非法时回落 `"light"`（现状行为不变）

## PUT /api/theme

请求：

```json
{ "mode": "light" | "dark" | "kaleido" }
```

- 校验：`mode` 必须在三值集合内；否则 `400 {"ok": false, "error": "mode 必须为 light、dark 或 kaleido"}`
- 成功：写盘并回显 `{"ok": true, "mode": "<写入值>"}`；写盘失败 `500`（现状行为不变）

## 不变量

- 路径、方法、鉴权、错误结构均不变；仅校验值域扩展
- 前端 `loadFromBackend` 的回读校验与本契约同步接受三值
- 兼容性：旧客户端（只认识 light/dark）收到 kaleido 模式文件时按各自回落规则处理（前端启动回读校验放行 kaleido；旧版本回落 light——与"后端不可达时本地兜底"同级别降级，可接受）
