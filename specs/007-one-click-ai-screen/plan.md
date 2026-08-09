# Implementation Plan: 一键筛选并 AI 优化与 P2 小项整修

**Feature Directory**: `specs/007-one-click-ai-screen` | **Date**: 2026-08-09 | **Spec**: [spec.md](./spec.md)

**Input**: 用户已确认的交付范围：B031 为主任务，B032 并入，B014、B018、B021、B022、B023、B024、B025、B026 为独立小项；边界以本轮 grill-me 与 Spec 复核结论为准。

## Summary

本计划把一次交付拆成两个层次：主任务 B031 在第二步新增“开始筛选并 AI 优化”大按钮，复用现有抓取与 AI 筛选两步任务，用最小 `auto_screen` 标记把两步串联；其余小项各自独立改动，不改变既有业务语义。实现原则是“拼接而不是重建”：现有报错、暂停、失败、空结果、AI 配置逻辑全部保持不变。

## Technical Context

**Language/Version**: Python 3.10+（后端）、TypeScript/Vue 3（前端）

**Primary Dependencies**: Flask、Vue 3、Vite、Vitest；无新增第三方依赖

**Storage**: SQLite / 本地状态文件；`auto_screen` 标记写入现有 `screening_runs.execution_params` 与内存任务字典，不新增数据表

**Testing**: 后端 `uv run python -m unittest`；前端 `npm test`（Vitest）；卫生门禁 `uv run python -m unittest tests.test_repo_hygiene`

**Target Platform**: Web 工作台与 Windows/macOS 桌面版共用同一前端；`start.bat` 仅 Windows

**Performance Goals**: 一键链路不新增额外轮询或组合百分比；进度继续由现有真实事件驱动

**Constraints**:

- 一键只执行当前草稿平台，不跨平台合并。
- 一键启动复用现有 `/api/execute-search`，AI 接续复用现有 `/api/ai-screen`，不新增抓取/筛选实现。
- 抓取无结果、AI 未配置、登录失败、风控暂停等异常行为与现有分步流程完全一致。
- `auto_screen` 只做最小串联标记，不引入组合任务状态机。
- 画像 10 字校验只拦截“一键启动”和“开始 AI 筛选”，不拦截“开始抓取”。
- 原分步流程、按钮、弹窗与错误反馈全部保留。

**Scale/Scope**: 1 个主功能 + 9 个小项，跨后端、前端、测试、文档、CI 与本地脚本；本 Plan 只生成设计工件，不进入实现。

## Constitution Check

- 公开仓库卫生：新增文件均为项目内公开文件，不引入密钥、Cookie、本地绝对路径或临时产物。
- 不伪造完成：一键接续、标记消费、小项验收全部以真实任务状态与测试证据为准。
- 不覆盖用户决定：B031/B032 及小项边界已按用户确认写入 spec.md。
- 无对外副作用：CI 工作流与 Release 模板只是仓库定义，不在本计划中执行发布或同步动作。
- 文档同步：CHANGELOG、README、打包手册与贡献说明随相关小项更新。

## Project Structure

```text
.github/
├── release-template.md          # B021 新增
└── workflows/
    ├── ci.yml                   # B022 新增
    └── release-macos.yml        # 既有，不改

tools/
└── start.bat                    # B026 改造

webui/
├── app.py                       # execute-search/latest-running-task/ai-screen 串联标记
├── src/
│   ├── App.vue                  # B024 页面标题
│   ├── views/DiscoveryView.vue  # B031 一键入口、B032 画像校验
│   ├── components/
│   │   ├── OneClickScreenDialog.vue   # B031 新增筛选确认弹窗
│   │   ├── EnvCheckDialog.vue         # B014、B018
│   │   ├── BrowserAccountsDialog.vue  # B018
│   │   └── __tests__/                 # 前端测试
│   └── styles/theme.css         # B025 远程字体
└── index.html                   # B024 标题

CHANGELOG.md                     # B023
CONTRIBUTING.md                  # B022 文档同步
README.md                        # 与本批用户可感知能力同步
packaging/README.md              # B021 模板引用
roadmap/BACKLOG.md               # 交付后状态同步（本地）
specs/007-one-click-ai-screen/   # Spec/Plan/Tasks
tests/                           # 后端测试
```

## Design Decisions

### D1 `auto_screen` 最小串联标记

- `POST /api/execute-search` 接受 `auto_screen: true`，写入内存任务字典与 `screening_runs.execution_params["auto_screen"]`。
- `GET /api/latest-running-task`（及刷新恢复路径）返回 `auto_screen` 布尔值，表示“该抓取任务完成时应自动接 AI 筛选”。
- 前端一键启动后正常轮询抓取任务；抓取状态为完成且 `auto_screen` 为真时，自动调用现有 `/api/ai-screen`。
- 自动调用 `/api/ai-screen` 时携带 `consume_auto_screen: true`；后端在进入现有校验前先消费标记，因此接口返回失败也不会在刷新后反复自动重试。
- 抓取取消、失败或“结束并保存部分结果”时清除标记；暂停/断点续抓保留标记。
- 页面刷新恢复时，若抓取已完成且标记未消费，前端自动接 AI 筛选；若已消费，则展示现有“继续 AI 筛选”入口，由用户手动继续。

### D2 一键入口交互

- 第二步操作区新增“开始筛选并 AI 优化”大号主按钮，放在左侧；原“开始抓取”等按钮缩小放在右侧，原功能不变。
- 点击顺序：先判断平台可用且无活任务，再检查搜索范围，再校验画像，最后打开筛选弹窗。
- 画像校验规则为 `profileSummary.trim().length >= 10`；不足时聚焦并高亮画像输入框，显示内联提示，不打开弹窗。
- 筛选弹窗展示当前平台 schema 的薪资、经验、学历、行业、规模及平台专属项；默认值来自 `filterValues[draftPlatform]`，确认后写回同一份草稿。
- 已存在旧结果时，弹窗内显示“将开始新一轮，当前结果会被替换”提示；确认后才启动。
- 一键确认后调用现有 `/api/execute-search` 并带 `auto_screen: true`；任务完成后自动进入 AI 筛选，进度区从抓取阶段切换到 AI 筛选阶段。

### D3 画像 10 字校验（B032）

- 抽取前端校验函数，一键启动与“开始 AI 筛选”共用。
- 输入框失焦或输入后显示内联提示，不实时打断输入。
- 简历分析自动生成的画像同样受校验约束，不因来源放行。
- “开始抓取”保持现状，不拦截空画像或短画像。

### D4 环境检查真实结果（B014）

- 移除 `EnvCheckDialog.vue` 中 130ms 逐项点亮的延时逻辑。
- 检查结果可用后一次性展示每项真实状态；检查中只显示真实进行中的检查项或整体加载状态。
- 失败项继续显示现有失败原因与下一步。

### D5 应用内确认弹窗（B018）

- `BrowserAccountsDialog.vue` 删除账号的 `window.confirm` 改为复用 `BaseDialog` 的应用内确认。
- `EnvCheckDialog.vue` 解除风控冷却的 `window.confirm` 改为复用 `BaseDialog` 的应用内确认。
- 取消不产生副作用；确认后沿用现有成功/失败反馈。

### D6 Release 模板（B021）

- 新增 `.github/release-template.md`，固定包含 Windows/macOS 安装包、SHA256、前置条件、已知限制、常见问题与排错入口。
- `packaging/README.md` 增加对模板的引用，避免发布手册与模板重复维护。

### D7 CI 质量门禁（B022）

- 新增 `.github/workflows/ci.yml`：代码进入远程分支或合并请求时运行后端 `unittest` 与前端 `vitest`，任一失败标记阻断。
- 更新 `CONTRIBUTING.md`，让文档描述与仓库实际 CI 一致，不再声称“没有 CI 的本地全绿要求”。

### D8 CHANGELOG 去重（B023）

- 按 git log 判断：更新检查缓存与更新文件已存在相关修复位于 2.8.5 bump 之前，实际发布版本为 2.8.5。
- 保留 2.8.5 条目，移除 2.8.4 中重复条目；不重写其它历史事实。
- 本批交付如有用户可感知变更，按现有简单列表格式追加记录。

### D9 页面标题平台无关（B024）

- `webui/index.html` 移除固定“BOSS 工作台”标题，改为通用初始标题。
- `App.vue` 根据当前平台与页面状态更新 `document.title`：BOSS、智联、双平台/结果页均不出现错误的平台独占文案。

### D10 移除远程字体（B025）

- `theme.css` 删除 Google Fonts `@import`。
- 字体族令牌改为系统字体栈；不引入新的远程资源。
- 执行前端构建，确认产物中无远程字体引用，离线样式正常。

### D11 启动脚本安全化（B026）

- `tools/start.bat` 不再对 5000 端口监听进程直接 `taskkill`。
- 通过命令行特征匹配识别 Career Scout 旧进程（源码模式匹配 `webui\app.py`，桌面模式匹配产物名），只关闭匹配进程；无关进程占用端口时提示端口占用。
- 服务启动后等待 `/api/session` 健康检查通过再打开浏览器；超时输出明确错误并退出。

## Test Strategy

- 后端：扩展 `tests/test_webui_app.py`，覆盖 `auto_screen` 持久化、`latest-running-task` 返回、`ai-screen` 消费标记、取消/失败/结束保存清除标记、刷新恢复路径。
- 前端：新增/扩展 `DiscoveryView` 与 `OneClickScreenDialog` 测试，覆盖画像不足、弹窗默认值写回、旧结果提示、自动接 AI 筛选、刷新恢复、活任务置灰。
- 前端小项：`EnvCheckDialog` 不再有逐项点亮阶段；删除账号与解除冷却无原生 confirm；页面标题平台场景正确。
- 文档/CI：新增或扩展卫生测试，校验 Release 模板必需项、CI 工作流存在、CHANGELOG 无重复、主题无远程字体引用、README 与打包手册一致。
- 最终：全量后端 `unittest`、全量前端 `vitest`、`npm run build`、`tests.test_repo_hygiene`。

## Deliverables

- `specs/007-one-click-ai-screen/spec.md`
- `specs/007-one-click-ai-screen/checklists/requirements.md`
- `specs/007-one-click-ai-screen/plan.md`
- `specs/007-one-click-ai-screen/tasks.md`（下一阶段生成）

按上一批的轻量约定，不生成 `research.md`、`data-model.md`、`contracts/`、`quickstart.md`。
