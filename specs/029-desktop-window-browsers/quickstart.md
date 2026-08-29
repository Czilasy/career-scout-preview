# Quickstart: 029 桌面壳窗口记忆修复批 — 验证指南

**Date**: 2026-08-29 | 前置：Windows 实机；Python 依赖就绪（`uv sync`）；前端依赖就绪（`webui/` 下 `npm ci`）

## 1. 自动化验证（交付门禁主体）

```powershell
# 聚焦测试（窗口状态域 + 注册表域 + 桌面壳存量回归）
uv run python -m unittest tests.test_desktop_window_state tests.test_browser_registry tests.test_desktop_shell -v

# 后端全量 + 仓库卫生（收口边界见 plan.md Verification Gate）
uv run python -m unittest discover -s tests -v
uv run python -m unittest tests.test_repo_hygiene

# 前端测试 + 构建（webui/ 下）
npm run test -- --run
npm run build
```

预期：全部通过；`npm run build` 后 dist 同步（pre-push 钩子会复核）。

## 2. 窗口记忆真机冒烟（Windows 实机，一次性，记录结果）

> **状态（2026-08-29）**：自动化验证全部通过；真机冒烟待执行（实施在无 GUI 交互环境完成）。

源码方式启动桌面壳：`uv run python packaging/desktop.py`（或构建 EXE 后验证）。按序执行并核对：

1. **首开最大化**：备份后删除 `~/.career-scout/desktop_window.json` → 启动 → 窗口应直接最大化。
2. **普通默认**：从最大化还原 → 窗口应为 1545×900、屏幕居中（工作区装得下的前提下）。
3. **拖拽记忆**：拖到随意位置、缩放成随意大小 → 关闭 → 重开 → 应最大化（若关窗时是最大化）或与关闭前一致（普通态关闭时）。
4. **核心 Bug 回归**：拖好普通矩形 → 最大化 → 关闭 → 重开（最大化）→ 还原 → **应回到拖好的矩形**（而非全屏大小的普通窗口）；再关闭重开确认记忆保持。
5. **钳制**：手改记忆文件 `width: 5000` → 启动 → 窗口不超屏（钳回工作区）；`x: -9999` → 主屏居中。
6. **旧记忆升级**：写入一份 schema 2 全屏污染矩形（如 `{"schema":2,"width":1936,"height":1056,"x":-8,"y":-8,...}`）→ 启动 → 按首开处理（最大化），无错乱窗口。
7. **更新重启路径**：触发一次应用内更新重启（或调用退出 API 重启），确认记忆在重启路径下同样正确。

## 3. 多浏览器冒烟（真机仅 Chrome/Edge；其余 6 家以单测兜底）

> **状态（2026-08-29）**：自动化验证全部通过；真机冒烟待执行。

1. 打开工作台 → 设置菜单 → 「浏览器」→ 应看到注册表清单，Chrome/Edge 标记已安装（含探测路径），未安装项置灰。
2. 选择 Edge → 保存 → 触发一次抓取登录流程 → 调试实例应由 msedge.exe 启动、使用独立数据目录。
3. 切回 Chrome → 抓取登录流程正常，Chrome 侧登录态仍在（chrome/edge 共用现状目录，免重登）。
4. **手动路径**：填入一个非浏览器 exe（如 notepad.exe）→ 校验应失败且提示明确；填入 Firefox（如有）→ 报"内核不兼容"。
5. 检查 `advanced_settings.json` 中选择键已持久化；重启工作台后选择保持。

> 未安装 Brave/Vivaldi/Opera/360/QQ/夸克的机器上，其条目正确性由 `tests/test_browser_registry.py` 的注册表完整性断言与路径候选正确性覆盖；夸克等魔改内核若实测 CDP 不兼容，预期行为为启动阶段明确报错（非卡死）。

## 4. 验收对照

- SC-001 ↔ 冒烟 3/4；SC-002 ↔ 冒烟 1/2；SC-003 ↔ 冒烟 5；SC-004 ↔ 单测 + 冒烟 3.1；SC-005 ↔ 冒烟 3.2/3.4；SC-006 ↔ 自动化验证全绿。
- 详细场景定义见 [spec.md](./spec.md)（US1 场景 1–7、US2 场景 1–5、US3 场景 1–7）。
