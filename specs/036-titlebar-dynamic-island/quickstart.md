# Quickstart: 桌面壳自绘标题栏 + 顶栏胶囊灵动岛

**Spec**: [spec.md](./spec.md) | **Date**: 2026-09-01

## 前置

- Windows + WebView2 Runtime（桌面版）；前端构建依赖 Node 环境。
- 依赖安装：`uv sync`（后端）与 `webui/` 下 `npm install`（前端）。

## 验证命令

```bash
# 后端聚焦测试（窗口控制/无边框接线/窗口状态回归）
uv run python -m unittest tests.test_desktop_shell tests.test_desktop_shell_wiring tests.test_desktop_window_state

# 前端聚焦测试（标题栏/胶囊组件 + 顶栏相关）
cd webui && npx vitest run src/components/__tests__/WindowTitleBar.spec.ts src/components/__tests__/DynamicIsland.spec.ts src/__tests__/App.spec.ts

# 全量门禁（交付前）
uv run python -m unittest discover   # 后端全量
npx vitest run                        # 前端全量
npm run build                         # vue-tsc + vite 构建
uv run python -m unittest tests.test_repo_hygiene  # 仓库卫生
```

## 用户端到端验证（Windows 真实 EXE，交付后由用户真跑）

### B084 自绘标题栏
1. 启动 EXE：窗口顶部无系统白色标题栏，显示与应用主题融合的自绘标题栏（默认暗色）。
2. 按住标题栏空白处拖动窗口移动。
3. 双击标题栏最大化/还原；点最小化/最大化/关闭按钮功能正确。
4. 边缘拖拽调整大小（最小 1024×700 生效）；最大化不覆盖任务栏。
5. 切换浅色主题→标题栏白；暗色→暗色；长按主题入口选万花筒→标题栏透明、按钮半透明磨砂、悬停 X 红底、最小化/最大化线条变深。
6. 调整位置/大小或最大化后关闭，重开恢复（窗口记忆不回归）；跨屏/副屏场景抽查。
7. 浏览器模式（非 EXE）打开页面：不显示自绘标题栏。

### B088 顶栏胶囊灵动岛
1. 空闲：胶囊常驻显示当前平台名，点它回主页。
2. 任务抓取/筛选中：胶囊显示实时进度数字（如"抓取 128/300"）与呼吸点，数字随进度跳动；点它回正在跑的任务。
3. 任务跑完：胶囊显示"匹配 N · 待确认 M"；待确认>0 时数字标亮；点它去结果页。
4. 任务暂停→胶囊橙色提醒、点去暂停现场；出错→红色提醒、点去处理。
5. 任务后台跑、用户在看历史/其它页：胶囊仍显示运行态，点击回到该任务。
6. 状态切换有动画；系统开启"减少动态"时动画退化为静态。
7. 特殊主题下胶囊半透明可见。
8. 提醒按钮显示各类提醒数量，点开提醒抽屉内容正确。

## 预期结果

- 聚焦测试 + 后端全量 + 前端全量 + `npm run build` + 仓库卫生全部通过。
- 上述用户端到端项在真实 EXE 下全部符合预期（spec SC-001~SC-010）。
