# Task 007：集成验证与全量回归

**所属 Wave**：3（串行，收尾） | **硬前置**：Task 006 完成 | **用户故事**：全部（EXE1-EXE6）

## 必读文件

- 仓库根 `AGENTS.md`
- `specs/003-desktop-exe/spec.md`（成功标准 SC-001 ~ SC-010）
- 全部已冻结契约（runtime-mode / inprocess-runner / desktop-shell）

## 写入范围

本包原则上**不写业务代码**，只做验证与阻断项修复。阻断项修复必须最小化并回报主会话；`git status` 核实产物零入库。

## 原子清单

- [ ] T047 [P] 本机执行 `packaging/build_exe.ps1` 构建 EXE：产物 `.release/CareerScout-v{version}.exe` 存在、体积合理（预期数十 MB）、版本号正确
- [ ] T048 启动 EXE：窗口出现、界面加载（工作台首页）、无控制台窗口；首启（含解压）≤10s（SC-001）
- [ ] T049 EXE 内环境检查（SC-003）：`deps` 项「内置运行时」恒 ok；`webview2` 项存在且状态正确（本机应已装）；browsers / 登录 / AI 项行为与源码版一致
- [ ] T050 EXE 内发起一次 BOSS 抓取任务（SC-004）：提交 → 运行 → 完成；日志流式；结果可查看；源码模式（`python webui/app.py`）可读同一数据（FR-012 数据互通）
- [ ] T051 任务运行中关闭窗口（SC-005）：无残留进程（任务管理器核实）；重启 EXE 后任务历史与数据完好、恢复机制正常
- [ ] T052 窗口体验（SC-007）：重复启动第二个实例被拦截提示（SC-006）；窗口缩放覆盖 1024×700 与 1440×900，无横向溢出/重叠；重启后窗口大小位置恢复
- [ ] T053 全量回归（SC-008）：Python 全量（`uv run python -m unittest discover`）、前端全量（`npm test`）、类型检查/构建（`vue-tsc` + `npm run build`）、卫生测试（`tests.test_repo_hygiene`）
- [ ] T054 修复阻断项后聚焦复查；`git status` 干净、构建产物零入库；如有修复按 Conventional Commits 小步提交并回报主会话

## 完成定义

SC-001 ~ SC-010 全部达成；向主会话回报：构建产物路径、真实 EXE 验收证据、全量回归结果、卫生测试结果、git 差异审计。主会话核对后向用户交付（发布动作由用户执行）。

## 提交纪律

验证过程不产生入库文件；阻断项修复先回报主会话确认再提交；commit email `czyooutzilas@gmail.com`。