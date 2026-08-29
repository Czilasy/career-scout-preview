# Contract: desktop_window.json 窗口状态文件（schema 3）

**Spec**: [../spec.md](../spec.md) | **Date**: 2026-08-29 | **修订对象**: `specs/003-desktop-exe/contracts/desktop-shell.md` §5（窗口状态记忆），本文件为该节在 029 批次后的现行契约；冲突处以本文件为准。

## 文件位置

`~/.career-scout/desktop_window.json`（可用 `state_dir` 注入替换，测试用）。

## Schema 3

```json
{
  "schema": 3,
  "width": 1545,
  "height": 900,
  "x": 187,
  "y": 70,
  "maximized": false,
  "default_width": 1545,
  "default_height": 900
}
```

| 字段 | 必填 | 语义 |
|---|---|---|
| `schema` | 是 | 恒为 `3` |
| `width` / `height` | 是 | **普通态矩形**尺寸（非"上次关窗矩形"）。最大化关窗时 = 最后一次普通矩形 |
| `x` / `y` | 是 | 普通矩形位置 |
| `maximized` | 是 | 上次关窗时是否处于最大化；实现按 data-model 口径：字段缺失时按 `false`（普通态）解释 |
| `default_width` / `default_height` | 否 | 用户自定义普通默认尺寸（覆盖内置 1545×900）；沿用 schema 2 既有机制，语义不变 |

## 写入规则（closing 时刻，单一写入口）

1. 运行时为最大化（`maximized` 事件后的内存态）→ 写 `{最后普通矩形, maximized: true}`。**全屏矩形禁止写入普通字段。**
2. 运行时为普通态 → 写 `{当前矩形, maximized: false}`（与旧版行为一致）。
3. `window.width/height/x/y` 任一不可读 → 回退本次启动使用的值（保持现有兜底）。
4. 写入失败（OSError）静默忽略（现有约定）。

## 读取与升级规则（启动时）

| 输入 | 解释 |
|---|---|
| 文件缺失 / 非法 JSON / 非 dict | 无记忆 → 首开处理 |
| `schema != 2 且 != 3` | 无记忆 → 首开处理 |
| schema 3，记忆字段缺失/非法/超 MIN/MAX 限 | 无记忆 → 首开处理 |
| schema 2，`width/height` 超出任一显示器工作区（污染记忆） | 无记忆 → 首开处理（research D2） |
| schema 2，`width/height` 在工作区内 | 继承为普通矩形，`maximized: false` |
| schema 3，`maximized: true` | 按普通矩形开窗后立即最大化（`create_window(maximized=True)` 路径） |
| 记忆位置越出全部工作区 | 尺寸保留，位置改为主工作区居中（读时钳制，不回写） |
| 记忆尺寸超出任一工作区（schema 3 自身被外力污染，如换小屏） | 尺寸钳到主工作区内（读时钳制，不回写） |

**"首开处理"** = `{默认普通矩形(1545×900，过工作区钳制；有 default_* 用 default_*，同样钳制), maximized: true}`。

## 不变量

- 普通字段里的值**永远**是某个屏幕工作区装得下的合法普通矩形（"记忆值永不超屏"）。
- 读文件无写副作用；钳制只作用于返回值。
- schema 2 正常记忆升级零丢失（位置尺寸原样继承）。
- 旧调用面兼容：`packaging/desktop.py` 继续导出 `load_window_state / save_window_state / DEFAULT_WIDTH / DEFAULT_HEIGHT` 等既有符号（实现迁至 `packaging/window_state.py`，语义按本契约变化）。
