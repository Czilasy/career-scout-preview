# Tasks: 一键筛选并 AI 优化与 P2 小项整修

**Input**: Design documents from `specs/007-one-click-ai-screen/`

**Prerequisites**: `spec.md`、`plan.md`

**Tests**: 本任务清单包含测试任务；测试任务在各用户故事内与实现一起按聚焦验证推进，不强制先失败后实现。

**Organization**: 按用户故事分组；US1 是主任务，US2-US10 为独立小项；同一文件内的任务串行，不同文件任务可并行。

## Phase 1: Setup (Shared Baseline)

**Purpose**: 记录改动前测试基线，避免把既有失败误判为本任务引入。

- [x] T001 运行后端基线测试 `uv run python -m unittest tests.test_healthy_pipeline tests.test_webui_app tests.test_updater tests.test_workbench_api tests.test_repo_hygiene` 与前端基线测试 `cd webui && npm test`，记录失败/通过情况后再开始实现。

---

## Phase 2: User Story 1 - 开始筛选并 AI 优化一键入口 (Priority: P1)

**Goal**: 第二步新增“开始筛选并 AI 优化”主按钮；画像校验通过后弹窗确认筛选字段，确认后串联现有抓取与 AI 筛选。

**Independent Test**: 模拟正常一键、画像不足、搜索范围为空、旧结果、运行中任务、刷新恢复、暂停/取消、平台禁用、AI 筛选调用失败九类场景，断言按钮、弹窗、串联与标记行为符合 spec。

### Implementation for User Story 1

- [x] T002 [US1] 在 `webui/app.py` 的 `POST /api/execute-search` 接受 `auto_screen: true`：写入内存任务字典与 `screening_runs.execution_params["auto_screen"]`，不改变现有搜索参数校验。
- [x] T003 [US1] 在 `webui/app.py` 的 `GET /api/latest-running-task`、`GET /api/task-state/<run_id>` 与刷新恢复路径返回 `auto_screen` 布尔值；读取顺序为内存任务优先、DB execution_params 兜底。
- [x] T004 [US1] 在 `webui/app.py` 的 `POST /api/ai-screen` 支持 `consume_auto_screen: true`：进入现有校验前先清除来源抓取任务的 `auto_screen` 标记，保证接口返回失败后也不会在刷新时反复自动重试。
- [x] T005 [US1] 在 `webui/app.py` 的抓取取消、失败与“结束保存部分结果”终态路径清除 `auto_screen` 标记，落点包括 `/api/execute-search/<task_id>/cancel`、`/api/task/cancel/<run_id>`、`/api/task/finish/<run_id>` 与 `_run_pipeline_task` 终态提交处；暂停/断点续抓保留标记。
- [x] T006 [US1] 在 `tests/test_webui_app.py` 增加后端测试：`auto_screen` 持久化、`latest-running-task`/`task-state` 返回、`ai-screen` 消费标记（含失败响应场景）、取消/失败/结束保存清除、暂停/续抓保留。
- [x] T007 [P] [US1] 新建 `webui/src/components/OneClickScreenDialog.vue`：展示当前平台 schema 的薪资、经验、学历、行业、规模及平台专属项；默认值来自 `filterValues[draftPlatform]`，确认后写回同一份草稿；旧结果存在时显示“将开始新一轮，当前结果会被替换”。
- [x] T008 [P] [US1] 在 `webui/src/views/DiscoveryView.vue` 第二步操作区新增“开始筛选并 AI 优化”大号主按钮（左侧），原“开始抓取”等按钮缩小放右侧；活任务或平台禁用时置灰；关键词或城市为空时提示去第二步补齐且不打开弹窗；画像不足时聚焦并高亮画像输入框且不打开弹窗；条件满足后才打开 `OneClickScreenDialog`。
- [x] T009 [US1] 在 `webui/src/views/DiscoveryView.vue` 实现自动接续：一键确认后带 `auto_screen: true` 调用现有 `/api/execute-search`；抓取完成且标记为真时自动调用 `/api/ai-screen`（携带 `consume_auto_screen: true` 与当前筛选草稿/画像）；进度区从抓取阶段切换到 AI 筛选阶段。
- [x] T010 [US1] 在 `webui/src/views/DiscoveryView.vue` 的 `restoreRunningTask` 接入 `auto_screen`：刷新后若抓取已完成且标记未消费，自动接 AI 筛选；若已消费，只展示现有“继续 AI 筛选”入口。
- [x] T011 [US1] 在 `webui/src/views/__tests__/DiscoveryView.spec.ts` 与 `webui/src/components/__tests__/OneClickScreenDialog.spec.ts` 增加前端测试：按钮层级、置灰、搜索范围为空、画像不足、弹窗默认值写回、旧结果提示、自动接续、刷新恢复、取消/失败不接续、AI 筛选调用失败后标记消费且刷新不自动重试。

**Checkpoint**: US1 可独立验证，一键串联、标记生命周期与刷新恢复符合 spec。

---

## Phase 3: User Story 2 - 求职画像最少 10 字 (Priority: P2)

**Goal**: 画像少于 10 字时在“一键启动”和“开始 AI 筛选”拦截，普通抓取不拦截。

**Independent Test**: 构造 0 字、9 字、10 字、含首尾空格、仅标点样本，断言提示、拦截与放行边界。

### Implementation for User Story 2

- [x] T012 [US2] 在 `webui/src/views/DiscoveryView.vue` 增加画像校验函数 `profileSummary.trim().length >= 10`，一键启动与 `startAiScreen` 共用；输入框失焦或输入后显示内联提示，不实时打断输入；`startScrape` 不拦截。
- [x] T013 [US2] 在 `webui/src/views/__tests__/DiscoveryView.spec.ts` 增加画像校验测试：0/9 字拦截、10 字放行、首尾空格处理、自动生成画像同样受约束、开始抓取不受影响。

**Checkpoint**: US2 可独立验证，画像门槛只作用于需要 AI 画像的入口。

---

## Phase 4: User Story 3 - 环境检查真实逐项结果 (Priority: P2)

**Goal**: 环境检查不再用延时逐项点亮制造假进度。

**Independent Test**: 模拟检查接口一次性返回全部结果，断言不存在“已检查完仍在逐项点亮”阶段。

### Implementation for User Story 3

- [x] T014 [US3] 修改 `webui/src/components/EnvCheckDialog.vue`：移除 130ms 逐项点亮延时逻辑；结果可用后一次性展示每项真实状态，检查中只显示真实进行中的检查项或整体加载状态；失败项保留现有原因与下一步。
- [x] T015 [US3] 在 `webui/src/components/__tests__/EnvCheckDialog.spec.ts` 增加测试：全量结果一次性显示、无逐项点亮阶段、失败项展示原因。

**Checkpoint**: US3 可独立验证，环境检查不再出现假进度。

---

## Phase 5: User Story 4 - 破坏性操作使用应用内确认弹窗 (Priority: P2)

**Goal**: 删除账号与解除冷却不再使用浏览器原生 confirm。

**Independent Test**: 触发两个操作，断言原生 confirm 不出现、应用内弹窗取消/确认行为正确。

### Implementation for User Story 4

- [x] T016 [P] [US4] 修改 `webui/src/components/BrowserAccountsDialog.vue`：删除账号的 `window.confirm` 改为复用 `BaseDialog` 的应用内确认，取消无副作用，确认后沿用现有删除流程。
- [x] T017 [P] [US4] 修改 `webui/src/components/EnvCheckDialog.vue`：解除风控冷却的 `window.confirm` 改为复用 `BaseDialog` 的应用内确认，取消无副作用，确认后沿用现有解除流程。
- [x] T018 [US4] 在 `webui/src/components/__tests__/BrowserAccountsDialog.spec.ts` 与 `EnvCheckDialog.spec.ts` 增加测试：断言不调用 `window.confirm`、应用内弹窗出现、取消不执行动作。

**Checkpoint**: US4 可独立验证，两个破坏性操作均使用应用内确认。

---

## Phase 6: User Story 5 - GitHub Release 下载页引导模板 (Priority: P2)

**Goal**: 仓库提供固定 Release 模板，覆盖安装包、校验值、前置条件、已知限制与排错入口。

**Independent Test**: 按模板必需项清单逐项核对，并与打包手册交叉验证。

### Implementation for User Story 5

- [x] T019 [P] [US5] 新增 `.github/release-template.md`：包含 Windows/macOS 安装包、SHA256、前置条件、已知限制、常见问题与排错入口。
- [x] T020 [P] [US5] 更新 `packaging/README.md`：增加对 `.github/release-template.md` 的引用，发布流程按模板填写，避免重复维护。
- [x] T021 [US5] 新增或扩展独立静态测试（如 `tests/test_public_assets.py`）：断言 Release 模板必需项存在，且与打包手册引用一致；不把该检查堆进 `tests/test_repo_hygiene.py`。

**Checkpoint**: US5 可独立验证，发布说明模板齐备。

---

## Phase 7: User Story 6 - CI 质量门禁 (Priority: P2)

**Goal**: 代码进入远程分支或合并请求时自动运行后端与前端测试，失败阻断合并。

**Independent Test**: 检查工作流配置与文档，确认自动检查同时覆盖后端与前端，失败语义明确。

### Implementation for User Story 6

- [x] T022 [P] [US6] 新增 `.github/workflows/ci.yml`：远程分支/合并请求触发，安装项目依赖后运行后端 `uv run python -m unittest discover -s tests` 与前端 `cd webui && npm ci && npm test`，任一失败标记阻断。
- [x] T023 [P] [US6] 更新 `CONTRIBUTING.md`：测试要求与 CI 门禁描述改为与仓库实际配置一致。
- [x] T024 [US6] 在独立静态测试（如 `tests/test_public_assets.py`）中增加校验：断言 `.github/workflows/ci.yml` 存在并包含后端与前端测试步骤；不把该检查堆进 `tests/test_repo_hygiene.py`。

**Checkpoint**: US6 可独立验证，质量门禁定义存在且文档一致。

---

## Phase 8: User Story 7 - CHANGELOG 相邻版本重复条目清理 (Priority: P2)

**Goal**: 同一变更只出现在实际发布版本，2.8.5 与 2.8.4 不再重复。

**Independent Test**: 核对 2.8.5 与 2.8.4 条目，断言重复变更已合并到实际发布版本。

### Implementation for User Story 7

- [x] T025 [US7] 修改 `CHANGELOG.md`：按 git log 判断相关修复实际发布于 2.8.5，保留 2.8.5 条目，移除 2.8.4 中“关闭更新检查缓存”“修复应用内更新文件已存在”等重复条目，不重写其它历史。
- [x] T026 [US7] 在独立静态测试（如 `tests/test_public_assets.py`）中增加校验：断言 CHANGELOG 相邻版本无重复变更条目，新增条目继续使用简单列表格式；不把该检查堆进 `tests/test_repo_hygiene.py`。

**Checkpoint**: US7 可独立验证，发布历史不再重复。

---

## Phase 9: User Story 8 - 页面标题与平台无关 (Priority: P2)

**Goal**: BOSS、智联、双平台场景下页面标题都不出现错误平台独占文案。

**Independent Test**: 分别处于 BOSS、智联、双平台/结果页场景，断言标题正确。

### Implementation for User Story 8

- [x] T027 [P] [US8] 修改 `webui/index.html`：移除固定 `Career Scout · BOSS 工作台` 标题，改为通用初始标题。
- [x] T028 [P] [US8] 修改 `webui/src/App.vue`：根据当前平台与页面状态更新 `document.title`，BOSS/智联/双平台场景均不出现错误的“BOSS 工作台”文案。
- [x] T029 [US8] 在 `webui/src/__tests__/App.spec.ts` 增加测试：BOSS、智联、双平台/结果页场景的标题断言。

**Checkpoint**: US8 可独立验证，标题随平台正确变化。

---

## Phase 10: User Story 9 - 移除远程字体依赖 (Priority: P2)

**Goal**: 首屏不再请求远程 Google Fonts，离线样式正常。

**Independent Test**: 构建前端并检查产物无 `fonts.googleapis.com` 引用，离线打开界面样式正常。

### Implementation for User Story 9

- [x] T030 [US9] 修改 `webui/src/styles/theme.css`：删除 Google Fonts `@import`，字体族令牌改为系统字体栈，不引入新的远程资源。
- [x] T031 [US9] 执行 `cd webui && npm run build`，确认 `webui/dist` 产物中无远程字体引用；在独立静态测试（如 `tests/test_public_assets.py`）中增加校验断言源码与产物均不含 `fonts.googleapis.com`；不把该检查堆进 `tests/test_repo_hygiene.py`。

**Checkpoint**: US9 可独立验证，首屏无远程字体依赖。

---

## Phase 11: User Story 10 - 启动脚本只关闭本项目旧进程并等待就绪 (Priority: P2)

**Goal**: `start.bat` 不误杀无关 5000 端口进程，并等待健康接口就绪后打开浏览器。

**Independent Test**: 在 Windows 本机分别模拟无关进程占用、本项目旧进程占用、服务未就绪三种场景，验证脚本行为；静态测试作为回归防线。

### Implementation for User Story 10

- [x] T032 [US10] 修改 `tools/start.bat`：识别并只关闭匹配 Career Scout 命令行特征的旧进程；无关进程占用端口时提示端口占用；等待 `/api/session` 健康检查通过后再打开浏览器，超时输出明确错误并退出。
- [x] T033 [US10] 新增 `tests/test_start_bat.py` 静态校验：断言脚本不含对端口监听进程的无差别 `taskkill /F /PID`，包含命令行特征匹配、`/api/session` 就绪等待与超时退出。
- [x] T034 [US10] 在 Windows 本机做启动脚本运行冒烟：分别验证无关进程不被杀、本项目旧进程被杀、服务未就绪时不提前打开浏览器且超时提示明确；记录实际结果作为运行证据。

**Checkpoint**: US10 可独立验证，启动脚本安全且就绪判断正确。

---

## Phase 12: Polish & Cross-Cutting Concerns

**Purpose**: 收口 BACKLOG 状态、全量回归与真实渲染检查。

- [x] T035 更新 `roadmap/BACKLOG.md`：将 B031、B014、B018、B021、B022、B023、B024、B025、B026、B032 标记为已完成/归档，并同步 P2/P3 总览数量（本地文件）。
- [x] T036 [P] 运行全量后端 `uv run python -m unittest discover -s tests`、全量前端 `cd webui && npm test`、`cd webui && npm run build`；功能用例全部通过且 `webui/dist/index.html` 引用新产物。
- [ ] T037 运行 `uv run python -m unittest tests.test_repo_hygiene` 作为最终门禁；失败时只修复本批引入的卫生问题，不执行仓库同步动作。
- [x] T038 对 `DiscoveryView.vue` 一键按钮/弹窗、`EnvCheckDialog.vue`、`BrowserAccountsDialog.vue` 在桌面 1440×900 与窄屏 390×844 做真实渲染检查，确认无重叠、无横向溢出、无原生 confirm、标题正确。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，可立即执行。
- **US1 (Phase 2)**: 依赖基线通过；后端标记与前端自动接续完成后聚焦验证。
- **US2 (Phase 3)**: 依赖 US1（共用 `DiscoveryView.vue` 一键入口），串行执行。
- **US3-US10 (Phase 4-11)**: 依赖基线通过，彼此文件独立，可并行。
- **Polish (Phase 12)**: 依赖全部用户故事完成。

### User Story Dependencies

- **US1**: 无跨故事依赖。
- **US2**: 依赖 US1 完成后执行。
- **US3**: 无跨故事依赖。
- **US4**: 无跨故事依赖。
- **US5**: 无跨故事依赖。
- **US6**: 无跨故事依赖。
- **US7**: 无跨故事依赖。
- **US8**: 无跨故事依赖。
- **US9**: 无跨故事依赖。
- **US10**: 无跨故事依赖。

### Parallel Opportunities

- US1 内：`OneClickScreenDialog.vue` 与 `DiscoveryView.vue` 按钮/入口可并行；后端标记任务先于前端自动接续。
- US3、US4、US5、US6、US7、US8、US9、US10 文件互不重叠，可并行。
- 各小项的测试任务在其实现完成后执行，不同小项的测试可并行。
- Polish 阶段的全量验证必须基于最终代码执行，不提前复用早期聚焦测试结果。

## Implementation Strategy

### MVP First (US1 + US2)

1. 完成 Phase 1 基线记录。
2. 实现 US1：后端 `auto_screen` 标记（创建、返回、消费、清除）+ 一键按钮/弹窗 + 自动接续 + 刷新恢复。
3. 实现 US2：画像 10 字校验并入同一入口。
4. 聚焦验证 US1+US2，再进入小项批次。

### Incremental Delivery

1. US1 + US2 完成并独立验证。
2. US3-US10 按独立小项并行或顺序完成，各自聚焦验证。
3. Polish 做 BACKLOG 状态、全量回归、构建与真实渲染检查。

## Notes

- 本任务清单不含仓库同步动作。
- 一键链路只复用现有抓取与 AI 筛选接口，不新增组合任务状态机。
- 抓取无结果、AI 未配置、登录失败、风控暂停等异常沿用现有分步流程行为，不新增预检或特判。
- `auto_screen` 的消费与清除分属不同代码路径：消费在 `POST /api/ai-screen`，清除在抓取取消/失败/结束保存终态路径。
- 静态资产与文档校验集中在独立测试文件（如 `tests/test_public_assets.py`），不继续堆进 `tests/test_repo_hygiene.py`。
- 测试日志与临时产物写入系统临时目录，不写入项目根目录。
- T037 卫生门禁唯一未通过项是未跟踪文件；按“不执行 git 暂存/提交”约束未做 git 操作。
