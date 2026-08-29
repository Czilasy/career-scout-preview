# Contract: 浏览器注册表 HTTP 端点（/api/browser-registry）

**Spec**: [../spec.md](../spec.md) | **Date**: 2026-08-29 | 路由域 `webui/browser_registry_api.py`，`register_*(app, ctx)` 模式；响应风格与现有 settings 域一致（JSON dict，错误 `{ok: false, error}`）。

## GET /api/browser-registry

返回探测结果 + 当前选择。

**Response 200**:

```json
{
  "registry": [
    {"key": "chrome", "name": "Chrome", "installed": true, "path": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"},
    {"key": "edge", "name": "Edge", "installed": true, "path": "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"},
    {"key": "brave", "name": "Brave", "installed": false, "path": null}
  ],
  "selection": {"mode": "registry", "key": "chrome"},
  "effective_path": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
}
```

- `registry`：注册表全量 8 条（`installed=false` 也要返回，供前端展示全清单）；`path` 为探测命中路径或 null。
- `selection.mode`：`"registry"`（`key` 有效）| `"manual"`（`browser_manual_path` 有效）| `"auto"`（缺省 = 自动探测 chrome→edge，向后兼容现状）。
- `effective_path`：按当前选择解析出的可执行文件路径；解析失败为 `null`。

## PUT /api/browser-registry

保存选择。请求体二选一：

```json
{"mode": "registry", "key": "brave"}
{"mode": "manual", "path": "D:\\Browsers\\xx\\xx.exe"}
{"mode": "auto"}
```

**Response 200**: `{"ok": true, "selection": {...同 GET...}}`

**错误**：

| 状态 | 条件 | body |
|---|---|---|
| 400 | `key` 不在注册表 / `mode` 非法 / body 缺字段 | `{"ok": false, "error": "invalid_selection", "message": "<用户可读>"}` |
| 400 | manual 路径探活失败（不可执行 / 非浏览器版本输出） | `{"ok": false, "error": "path_validation_failed", "message": "<含失败原因>"}` |

- manual 模式保存前执行 `--version` 探活（超时 10s）；**探活不通过则不保存**（spec FR-012/FR-013）。
- 探活输出含 `Firefox`（或其他非 Chromium 标识）→ `error: "kernel_incompatible"`，message 指明内核不兼容。

## POST /api/browser-registry/validate-path

手动路径即时校验（保存前反馈用，不落盘）。

**Request**: `{"path": "..."}`  **Response 200**: `{"ok": true, "version": "126.0.6478.126"}` 或 `{"ok": false, "error": "path_validation_failed|kernel_incompatible", "message": "..."}`

## 启动链路内核二次校验（非 HTTP）

抓取启动后调试端点 `/json/version` 的 `Browser` 字段不含 `Chrome/` → 中止并报"浏览器内核不兼容"（现有失败码体系新增明确原因），不得进入无反馈等待（spec FR-013）。

## 数据目录派生（非 HTTP，账号维度联动）

切换浏览器后账号生效数据目录按 D6 规则派生（见 [../data-model.md](../data-model.md) 实体 4）；切换本身不产生 HTTP 副作用——派生发生在每次抓取启动时，因此"切走再切回"无需任何恢复操作。

## 前端交互契约

- 入口：设置菜单（AppSettingsMenu）新增「浏览器」项 → BrowserSettingsDialog。
- 对话框：注册表 8 条单选列表（未安装的置灰展示）+ "手动指定路径"分支（输入框 + 即时校验按钮，消费 validate-path）+ 当前生效路径展示。
- 保存成功后本地状态即时生效，无需重启应用（启动链路每次解析，无缓存）。
