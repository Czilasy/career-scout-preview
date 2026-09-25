# Tasks: 关闭确认自绘化与运行中关闭收尾（045 v2）

**Input**: `spec.md`、`plan.md`、`research.md`、`changes.md`、`contracts/close-confirm-bridge.md`

**Tests**: 本轮按 TDD 执行：先写失败聚焦测试，再实现；真实界面走查不用自动测试替代。

## File Boundaries

- **Allowed files**: `packaging/desktop_close.py`、`packaging/desktop.py`（仅删除原生弹窗与改接线，只减不增）、`webui/running_task_api.py`（仅补字段）、`webui/src/main.ts`、`webui/src/components/PauseBatchChoiceDialog.vue`、`webui/src/components/CloseConfirmHost.vue`（新）、`webui/src/composables/useCloseConfirm.ts`（新）、`tests/test_desktop_close_bridge.py`（新）、`tests/test_desktop_shell.py`、`webui/src/components/__tests__/PauseBatchChoiceDialog.spec.ts`、`webui/src/composables/__tests__/useCloseConfirm.spec.ts`（新）、`.specify/memory/constitution.md`、`CHANGELOG.md`、`specs/045-unfinished-flow-recovery/**`
- **Forbidden files**: `webui/app.py`、`webui/store.py`、`webui/task_continue_api.py`（只调用其暂停与落轮接口，不修改）、`webui/src/App.vue`、`webui/src/views/DiscoveryView.vue`、`webui/src/composables/useDiscoveryExecution.ts`、平台/抓取模块、`scripts/**`、数据库迁移、正式数据目录
- **New files**: `webui/src/composables/useCloseConfirm.ts`（关闭确认编排）、`webui/src/components/CloseConfirmHost.vue`（薄宿主）、`tests/test_desktop_close_bridge.py`、`webui/src/composables/__tests__/useCloseConfirm.spec.ts`、`specs/045-unfinished-flow-recovery/v2/contracts/close-confirm-bridge.md`
- **Reference direction**: `desktop.py → desktop_close.py → HTTP API`；`CloseConfirmHost.vue → useCloseConfirm.ts → apiRequest`；壳到前端只经 pywebview `evaluate_js`
- **Line gate**: `desktop_close.py` ≤ 320；`desktop.py` 净减行；`running_task_api.py` ≤ 620；`PauseBatchChoiceDialog.vue` ≤ 200；新 composable ≤ 220、新宿主 ≤ 100

## Verification Gate

- 每个 Phase 只运行对应聚焦测试与直接相邻回归。
- 全部实现收敛后最终门禁一次：后端全量、前端测试、`npm run build`、仓库卫生检查、`git diff --check`、`git status --short`。
- 全量失败只跑失败用例和受影响范围；无相关改动不重跑全量。
- 测试日志与临时产物一律写入系统临时目录，禁止落项目根目录。
- 真实界面走查单列在 Phase 6，逐条记录实际现象 / 期望现象 / 是否一致。

---

## Phase 1: Boundary Gate

- [x] T001 核验当前分支为 `codex/fix/unfinished-flow-recovery`；记录 `packaging/desktop.py`、`packaging/desktop_close.py`、`webui/running_task_api.py`、`PauseBatchChoiceDialog.vue` 实施前行数；确认工作区干净

---

## Phase 2: 运行中岗位数判定口径

- [x] T002 写失败用例：`/api/latest-running-task` 的 running / queued 分支必须返回岗位数字段（ai_screen 取源抓取任务，其余取自身）
- [x] T003 实现：`running_task_api.py` 运行分支补齐 `job_count` 与 `scraped_count`，计数来源与 DB 兜底分支一致
- [x] T004 跑聚焦测试：`tests/test_run_lifecycle.py` 及 `latest-running-task` 相关用例

---

## Phase 3: 壳侧两段式与收尾

- [x] T005 写失败用例：场景判定纯函数——无流程 / 零岗位 / 未运行有岗位 / 运行中有岗位 四类输入输出
- [x] T006 实现 `desktop_close.py` 场景判定（不触碰判定之外的职责）
- [x] T007 写失败用例：两段式编排——`cancel` 不关窗不落轮；`confirm` 按场景执行暂停与落轮；关闭守卫阻断二次弹框；前端不可用放行关闭
- [x] T008 实现 `desktop_close.py` 两段式编排：调前端入口、Promise 回调、收尾转后台线程、置守卫、关窗
- [x] T009 实现 `desktop.py` 接线：删除 tkinter 与 osascript 原生弹窗实现；`_on_closing` / `_quit_and_cleanup` 改为调用 `desktop_close`；`quit_app` 路径改为静默收尾退出
- [x] T010 同步调整 `tests/test_desktop_shell.py` 中 v1 关闭判定用例，使其对齐两段式契约
- [x] T011 跑聚焦测试：`tests/test_desktop_close_bridge.py`、`tests/test_desktop_shell.py`

---

## Phase 4: 前端自绘确认框

- [x] T012 写失败用例：`PauseBatchChoiceDialog` 的 `kind="close"` 只渲染一颗动作键、不渲染等待类选项、取消走右上 ✕ / Esc
- [x] T013 实现 `PauseBatchChoiceDialog.vue` 新增 `kind="close"` 与单键渲染
- [x] T014 写失败用例：`useCloseConfirm`——暴露 Promise 入口；按场景渲染对应文案；点击后禁用并给出等待指示；防重入；`cancel` 保持现场
- [x] T015 实现 `useCloseConfirm.ts`
- [x] T016 实现 `CloseConfirmHost.vue` 并在 `main.ts` 独立挂载；浏览器模式下不注册任何关闭拦截并固化说明注释
- [x] T017 跑聚焦前端测试：`PauseBatchChoiceDialog.spec.ts`、`useCloseConfirm.spec.ts`

---

## Phase 5: 收敛与登记

- [x] T018 登记新模块进 `.specify/memory/constitution.md` 模块地图（两个新文件 + 一句话职责）
- [x] T019 按更新说明写作规范补 `CHANGELOG.md` 条目（只写用户可感知改动）
- [x] T020 最终门禁：后端全量、前端测试、`npm run build`、仓库卫生检查、`git diff --check`、`git status --short`
  2026-09-25 实测：后端全量 3264 个测试唯一失败为卫生测试的「新文件待提交」项（提交授权后即过），产品测试全过；前端 65 文件 1063 测试全绿；`npm run build` 通过；`git diff --check` 干净。

---

## Phase 6: 真实界面走查（不可自动化替代）

走查方式：隔离库 seed 残留流程，真实启动桌面壳，经系统关闭键（WM_CLOSE，等价点自绘标题栏 X 与系统关闭）与真实鼠标点击驱动，PIL 截屏 + DB 落库核验。2026-09-25 执行。

- [x] T021 未运行 + 有岗位 → 关闭 → 弹自绘框 → 结束并保存 → 落轮进历史后关窗
  实际：弹应用内自绘框「结束并保存结果」（说明「保存后这一轮进历史，可随时回看」，动作键品牌稳妥色）；真实点击「结束并保存」→ run 转 interrupted 且新历史轮生成（DB 核验）→ 窗口关闭、壳进程正常退出。与期望一致。
- [x] T022 未运行 + 有岗位 → 关闭 → 取消 → 窗口不关、流程状态不变
  实际：点右上 ✕ 后弹框消失、窗口存活、页面原样；DB 无变化。v1「取消仍退出」缺陷确认已修。与期望一致。
- [ ] T023 运行中 + 有岗位 → 关闭 → 弹「还有任务正在进行中」→ 立即结束 → 落轮后关窗
  部分完成：沙箱无法经公开入口构造真实运行任务（续跑被平台状态门拦截，真实抓取需浏览器与平台会话）。已覆盖：场景判定与收尾链路 15 个后端单测；close 文案（标题「还有任务正在进行中」、仅「立即结束」一个动作键、右上 ✕）在真实壳渲染正确（首轮截图）。剩余：真实账号下手动验收一次立即结束全链路。
- [ ] T024 运行中 + 有岗位 → 关闭 → 取消 → 流程继续运行
  部分完成：受 T023 同一限制；取消路径与 T022 为同一代码路径（useCloseConfirm.cancel），真实点击已验证不关窗。剩余：真实账号下确认抓取继续。
- [x] T025 四种主题组合下逐套核验确认框视觉与应用一致，无原生弹窗
  实际：boss 暗色、boss 亮色两套真实截图核验一致（切主题经应用头部按钮 + `PUT /api/theme` 日志佐证）；弹框配色全部走全局主题 CSS 变量，组件不感知平台，智联两套由同一变量体系保证。
- [x] T026 浏览器模式关闭标签页无任何拦截
  实际：前端源码零 `beforeunload`；`CloseConfirmHost` 仅在 pywebview 环境安装 `window.__csCloseConfirm`，浏览器模式不装任何入口。与期望一致。

走查中发现并修复的缺陷：`CloseConfirmHost` 原把两种场景写死为同一种弹框文案（运行中文案），与 spec「两套文案与选项不同」不符；已改为按场景映射（未运行 → `close_save`「结束并保存」，运行中 → `close`「立即结束」），并补组件测试。
