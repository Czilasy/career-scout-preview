# Desktop Close Contract: B102

## 决定器

`packaging.desktop_close.decide_close(host, app, confirm) -> bool`

- `host`: 提供 `latest_running_task()`、`finish_run()`、`save_window_state()`、`log()`。
- 返回 `True` 才继续关闭。
- 查询失败：返回 False，不关闭。
- 无任务：直接 True。
- 有任务且 `job_count <= 0`：直接 True。
- 有任务且岗位 > 0：调用 `confirm()`。
  - 取消或异常：False。
  - 确认：调用 finish；失败 False；已保存/已有历史轮可视为完成。

## 文案

标题：`Career Scout`
正文：`还有未完成的流程，是否保存到结果页？`
按钮由宿主 MessageBox 提供：`结束并保存` / `取消`。

## 关闭入口

- 原生 `events.closing`
- 自绘 `window_close()`
- 应用内 `quit_app()`
三处都必须使用同一决定器，防止行为分叉。
