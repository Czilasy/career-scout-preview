# Tasks: 未完成流程统一找回与关闭保存

**Input**: `spec.md`、`plan.md`、`research.md`、`data-model.md`、`contracts/`

**Tests**: 本轮按 TDD 执行：先写失败聚焦测试，再实现；B104 是真实界面验证，不用自动测试替代。

## File Boundaries

- **Allowed files**: `packaging/desktop_close.py`、`packaging/desktop.py`、`webui/running_task_api.py`、`webui/result_rounds.py`、`webui/run_notice.py`、`webui/store_result_history_mixin.py`、`webui/src/composables/useDiscoveryExecution.ts`、`webui/src/composables/useDiscoveryTasks.ts`、`tests/test_run_lifecycle.py`、`tests/test_result_rounds.py`、`tests/test_desktop_shell.py`、`webui/src/composables/__tests__/useDiscoveryExecution.spec.ts`、`webui/src/composables/__tests__/useDiscoveryTasks.spec.ts`、`.specify/memory/constitution.md`、`CHANGELOG.md`、`specs/045-unfinished-flow-recovery/**`
- **Forbidden files**: `webui/app.py`、`webui/store.py`、`webui/task_continue_api.py`、`webui/src/views/DiscoveryView.vue`、`webui/src/App.vue`、平台/抓取模块、迁移 v1–v6、正式数据目录
- **New files**: `packaging/desktop_close.py`
- **Reference direction**: `desktop.py → desktop_close.py → HTTP API`；`running_task_api → result_rounds/store`；前端只走 composable → `apiRequest`
- **Line gate**: 新 Python <600；`desktop.py`、`running_task_api.py`、`useDiscoveryExecution.ts`、`useDiscoveryTasks.ts` 只做接线/最小逻辑；无文件超过宪法红线

## Verification Gate

- 每个 Phase 只运行对应聚焦测试与直接相邻回归。
- B101–B103 全部收敛后最终门禁一次：后端全量、前端测试、前端构建、卫生测试、`git diff --check`、`git status --short`。
- B104 单独真实界面执行；每条单独记录“实际现象 / 期望现象 / 是否一致”。
- 全量失败只跑失败用例和受影响范围；无相关改动不重跑全量。

---

## Phase 1: Boundary Gate

- [x] T001 核验当前分支不是 `main`，记录 `packaging/desktop.py`、`webui/running_task_api.py`、`webui/src/composables/useDiscoveryExecution.ts`、`webui/src/composables/useDiscoveryTasks.ts` 实施前行数；确认工作区只有本 SPEC 与既有用户文件改动
- [x] T002 备份/确认测试数据库隔离方式；禁止在正式库上写入

---

## Phase 2: User Story 1 - 未完成流程进历史（B101, P1）

- [x] T003 在 `tests/test_result_rounds.py` 先补失败用例：有岗位未结束流程经 finish/save 链路落为历史可见轮；0 岗位返回 None/无轮；无 JD/AI 也能出现
- [x] T004 在 `webui/result_rounds.py` 做必要最小修正/辅助，保证列表岗位可直接成为结果轮且同流程幂等升级；不改动调用契约
- [x] T005 运行 `uv run python -m unittest tests.test_result_rounds`

**Checkpoint**: B101 数据链收敛。

---

## Phase 3: User Story 3 - 异常退出启动提示接回（B103, P1）

- [x] T006 在 `tests/test_run_lifecycle.py` 先写失败用例：无 worker 的 queued/running/paused 有岗位残留返回可恢复快照；零岗位不返回；已落轮/已保存不再提醒
- [x] T007 在 `webui/running_task_api.py` 扩展数据库兜底：识别启动后无 worker 的 queued/running/paused 有岗位残留，返回与 paused 等价恢复项；只查询已持久化岗位，不做抢救保存
- [x] T008 在 `webui/run_notice.py` 与必要的水位查询辅助中收紧候选口径：已正常落轮/保存不提醒；最新残留一次性提醒；水位画像隔离
- [x] T009 在 `webui/src/composables/useDiscoveryTasks.ts`/`useDiscoveryExecution.ts` 做必要接线，使数据库残留恢复项不因“不是内存任务”被丢弃，并触发 island notice
- [x] T010 运行 `uv run python -m unittest tests.test_run_lifecycle` 与前端两个聚焦 spec

**Checkpoint**: B103 启动接回收敛。

---

## Phase 4: User Story 2 - 关闭保存确认（B102, P1）

- [x] T011 在 `tests/test_desktop_shell.py` 先写失败用例：无任务关闭、零岗位静默关闭、有岗位确认后先 finish 再关闭、取消不关闭、保存失败不关闭、原生/自绘入口同一决策
- [x] T012 新增 `packaging/desktop_close.py`：状态查询、岗位判定、确认回调、finish 等待与文案；HTTP 与 MessageBox 可注入
- [x] T013 在 `packaging/desktop.py` 接线 `_on_closing` 与 `_quit_and_cleanup` 使用同一决定器；原生关闭取消语义不变
- [x] T014 运行 `uv run python -m unittest tests.test_desktop_shell`

**Checkpoint**: B102 关闭链收敛。

---

## Phase 5: Documentation & Module Map

- [x] T015 更新 `.specify/memory/constitution.md` 模块地图，登记 `packaging/desktop_close.py` 职责与引用方向；不改原则版本
- [x] T016 在 `CHANGELOG.md` 增加用户可感知条目：未完成流程可找回；关闭时可直接保存到结果页；强杀重启后可接回
- [x] T017 复核 spec/plan/contracts 与实际实现一致；不一致项立即修正

---

## Phase 6: Final Automated Gate

- [x] T018 运行 `uv run python -m unittest discover -s tests`；失败先记录清单，只跑失败与受影响范围
- [x] T019 在 `webui/` 运行 `npm test`
- [x] T020 在 `webui/` 运行 `npm run build`
- [x] T021 运行 `uv run python -m unittest tests.test_repo_hygiene`、`git diff --check`、`git status --short`

---

## Phase 7: B104 Real UI Regression

- [ ] T022 **抓完跳过 AI 直接看结果**：走分段入口，只抓取不筛选；抓完后不进 AI，直接点看结果进 04。记录实际/期望/是否一致。
- [ ] T023 **抓一半暂停后关闭**：中途暂停，点窗口关闭；验证只有“结束并保存/取消”两个按钮；分别保存后关闭和取消保留现场。记录实际/期望/是否一致。
- [ ] T024 **强杀进程后重启接回**：中途用任务管理器或 Alt+F4 强杀，不走关闭流程；重启后提示并接回；继续跑完最终进历史。记录实际/期望/是否一致。
- [ ] T025 汇总 B104 三条结论；任一不一致记录具体差异并返修，不宣称完成。

## Dependencies & Execution Order

```text
T001→T002→[T003–T005]→[T006–T010]→[T011–T014]→[T015–T017]→[T018–T021]→[T022–T025]
```

## Notes

- 只有一个实现负责人时按 Phase 串行；同一文件任务不得并行。
- B104 必须真实启动应用；涉及真实平台时使用项目已就绪账号，不得绕过对外入口。
- 若真实平台/浏览器环境不可用，如实记录阻断，不得以 mock 冒充。
