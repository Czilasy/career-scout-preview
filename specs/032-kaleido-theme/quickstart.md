# Quickstart: 万花筒彩蛋主题模块验证指南

**前置**: 仓库根目录；`uv run` 可用；`webui/` 下 npm 依赖已装。

## 1. 启动与进入彩蛋

```powershell
uv run python webui/app.py   # 或现有启动方式，打开桌面窗口/浏览器页面
```

1. 顶栏点明暗切换按钮：确认亮↔暗切换与 ripple 表现与改动前一致（回归基线）。
2. **长按**主题按钮约 1 秒：蓄力动画（图标抖动＋发亮）→ 弹出主题选择框。
3. 确认选择框含三项：**亮 / 暗 / 万花筒**，当前主题有标识。
4. 点「万花筒」：整站换肤——四页均为暗膛＋光场＋切面＋流动色视觉。

**期望**：换肤后上传/抓取/筛选/结果四页与 `design/kaleido/page1.html~page4.html` 逐页对照一致；所有按钮、输入、列表行为与亮暗主题下完全相同。

## 2. 动效抽查

- 光轮持续自转、颜色轮回（约 3.5s 一循环）、点击页面空白处触发转筒、点击左上角 logo 光场静止约 3 秒。
- 鼠标移近页面中部光核：瞳孔苏醒、虹膜亮起；瞳孔随转筒一起转动。
- Windows 设置开启「动画效果关闭」（= prefers-reduced-motion）后刷新：页面全静态，功能正常。

## 3. 持久化与后端契约

```powershell
# 选定万花筒后：
curl http://127.0.0.1:<port>/api/theme          # {"ok": true, "mode": "kaleido"}
curl -X PUT http://127.0.0.1:<port>/api/theme -H "Content-Type: application/json" -d "{\"mode\":\"kaleido\"}"
curl -X PUT http://127.0.0.1:<port>/api/theme -H "Content-Type: application/json" -d "{\"mode\":\"bogus\"}"   # 400
```

- 重启应用：主题仍为万花筒（后端回读放行 kaleido）。
- 后端停止时切换主题：仍可用（localStorage 兜底），后端恢复后回读不覆盖用户选择。

## 4. 退出与降级

- 长按 → 选「暗」/「亮」：全站恢复对应主题，无残留万花筒样式；普通点击明暗互切表现不变。
- 万花筒主题下打开设置弹窗、收藏/历史抽屉：暗色降级样式，无破版。

## 5. 自动化门禁

```powershell
uv run python -m unittest tests.test_repo_hygiene          # 卫生
cd webui && npm run test                                   # 前端测试（含 useTheme/registry 用例）
cd webui && npm run build                                  # 构建
uv run python -m unittest discover -s tests                # 后端全量（实现批次门禁）
```

## 6. 视觉对照

逐页打开 `design/kaleido/page1.html ~ page4.html` 与应用内四页并排对照：布局骨架、模块位置、文字逐字、光场/眼睛/流动色是否一致。
