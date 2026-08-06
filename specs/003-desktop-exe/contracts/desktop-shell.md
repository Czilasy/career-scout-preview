# 契约：桌面壳（desktop-shell）

**所属**：specs/003-desktop-exe

**状态**：冻结（2026-08-06）。实现必须遵循本契约；发现契约冲突回到主会话统一修订。

## 1. 定位与入口

`packaging/desktop.py` 是 EXE 的进程入口（PyInstaller `entry point`），职责：

```
启动（单实例检查）→ 选随机端口 → 后台启动 Flask（create_app, RUNTIME_MODE="exe"）
→ 等待后端就绪 → 打开 pywebview 窗口（恢复上次大小位置）→ 窗口事件驱动生命周期
→ 关闭时保存窗口状态、终止后端与抓取线程 → 进程退出
```

- 不承载业务逻辑：所有业务仍由 `webui/app.py` 及其模块提供；desktop.py 只做进程与窗口编排。
- 源码模式下也可运行（`python packaging/desktop.py`），用于本地调试壳；此时 `RUNTIME_MODE` 仍为 `"exe"`（壳语义），资源定位按源码规则。

## 2. 单实例

- Windows 实现：named mutex（`ctypes.windll.kernel32.CreateMutexW`，名称固定 `CareerScout-SingleInstance`，**不带版本号**——避免跨版本双开绕过互斥），`GetLastError() == ERROR_ALREADY_EXISTS(183)` 判定已有实例。
- 已有实例时：向用户显示一次性提示（`ctypes` MessageBox 或 `pywebview` 前置的 messagebox），随后以退出码 0 退出；**不启动第二个 Flask、不写入任何数据**。
- 非 Windows 平台：退化为 `socket` 绑定探测或直接放行（由实现会话定，本版本目标平台仅 Windows）。
- 单实例锁必须在 Flask 启动**之前**获取，释放顺序与进程退出一致。

## 3. 随机端口

- 选择策略：`socket` 绑定 `("127.0.0.1", 0)` 获取操作系统分配的空闲端口，关闭后供 Flask 绑定。
- 端口选择失败（竞争或异常）→ 重试 N 次（实现会话定，建议 ≥3），仍失败则显示明确错误并退出（spec FR-017：不得无提示卡死）。
- 窗口 URL：`http://127.0.0.1:{port}`；端口不得写入日志之外任何持久位置以外的敏感上下文。

## 4. Flask 启动与就绪等待

- Flask 在独立线程以 `app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)` 启动（源码模式直接 `create_app().run(...)` 的行为不变；壳模式必须 `use_reloader=False`，避免子进程重启）。
- 传入 `create_app` 的 config（EXE 模式）：

```python
create_app(config={
    "RUNTIME_MODE": "exe",
    "PYTHON_EXECUTABLE": sys.executable,   # 运行时自身（in-process 不再真正 spawn）
    "START_TASKS": True,
    # 其余键保持默认（数据目录 ~/.career-scout 不变）
})
```

- 就绪等待：轮询 `GET http://127.0.0.1:{port}/api/session` 直到 200（超时阈值由实现会话定，建议 ≤30s；超时 → 错误提示并退出）。
- 就绪前不创建 webview 窗口（避免白屏）。

## 5. 窗口

| 项 | 值 | 依据 |
|---|---|---|
| 标题 | `Career Scout v{version}`（版本号从 `pyproject.toml` 读取，打包时也可注入 `--version`） | FR-013 |
| 默认尺寸 | 1366 × 768（可配置，见下） | 用户决定（2026-08-06） |
| `min_size` | (1024, 700) | FR-005，避免进入手机断点布局 |
| `resizable` | True | FR-005 |

- 窗口状态记忆：文件 `~/.career-scout/desktop_window.json`，**schema 版本 2**：

```jsonc
{
  "schema": 2,
  "default_width": 1366,     // 用户自定义默认宽度（无记忆时打开此尺寸）
  "default_height": 768,     // 用户自定义默认高度
  "width": 1280,             // 上次记忆的宽度（用户拖拽后的实际尺寸）
  "height": 800,
  "x": 100,
  "y": 100
}
```

- 语义：
  - **无记忆**（文件缺失/损坏/schema 不匹配）→ 以 `default_width`/`default_height` 打开（字段非法/越界回退常量 1366×768）。
  - **有记忆** → 以记忆的 `width`/`height`/`x`/`y` 打开。
  - `save_window_state` **必须保留** `default_*` 字段不被覆盖（用户自定义默认尺寸是配置，不是运行状态）。
  - schema 1 旧文件：按无记忆处理（用 default 常量），下一次保存升级为 schema 2。
- 恢复校验：窗口位置必须位于任一可见显示器工作区内（越界时回退默认居中），防止显示器变更后窗口不可见。
- 首启（未显式设置 x/y 时窗口属性可能为 None）：closing 保存必须回退到本次启动尺寸，保证**首次关闭也写入状态文件**（FR-006 首启即生效）。

## 6. 关闭与进程终止（FR-007）

- `events.closing`：保存窗口状态 → 请求后端关闭（调用 Flask 停止或直接标记）→ 终止抓取执行（设置 `TaskRunner`/`WorkbenchRunner` 的取消事件；等待短暂宽限或直接退出）→ `webview.start()` 返回后 `os._exit(0)` 兜底，确保无残留线程/孤儿进程。
- 抓取中的任务中断语义由既有「取消 → interrupted → 断点续跑/历史恢复」机制兜底（spec 用户故事 4），壳不做额外数据修复。

## 7. 错误提示（FR-017）

| 场景 | 行为 |
|---|---|
| 单实例已存在 | MessageBox 提示「Career Scout 已在运行」，退出 0 |
| 端口选择失败 | MessageBox 提示，退出非 0 |
| WebView2 缺失（`webview.start()` 抛初始化异常） | MessageBox 提示安装 WebView2（给出微软下载指引），退出非 0 |
| 后端就绪超时 | MessageBox 提示，退出非 0 |
| 其他未预期异常 | 记录到 `~/.career-scout/desktop.log`（追加式，含时间戳），MessageBox 提示，退出非 0 |

## 8. 非目标

- 不做托盘常驻、菜单栏、自动更新。
- 不做 macOS/Linux 壳（desktop.py 的跨平台退化仅保证不崩溃，正式验收仅 Windows）。
- 不实现 JS↔Python 双向通信（项目前端走 HTTP API，无需 pywebview js_api）。