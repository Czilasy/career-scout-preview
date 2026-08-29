---
description: "Task list for 029 桌面壳窗口记忆修复批"
---

# Tasks: 桌面壳窗口记忆修复批（最大化记忆 + 首开默认 + 多浏览器）

**Input**: Design documents from `/specs/029-desktop-window-browsers/`

**Prerequisites**: plan.md（必须）、spec.md（必须）、research.md、data-model.md、contracts/、quickstart.md

**Tests**: 本 Spec 明确要求测试（SC-006、Verification Gate），故每个故事包含测试任务，先写用例定义行为再实现。

**Organization**: 按用户故事分组；US1/US2 共享窗口状态域（Foundational 承载），US3 自包含。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、不依赖未完成前置）
- **[Story]**: 所属用户故事（US1/US2/US3）
- 所有路径相对仓库根

## File Boundaries（源自 plan.md，已冻结）

- **Allowed files（修改）**: `packaging/desktop.py`、`scripts/boss/constants.py`、`scripts/boss/browser.py`、`webui/pipeline_exec_chrome.py`、`webui/pipeline_exec_accounts.py`、`webui/app.py`（仅一行注册）、`webui/src/components/AppSettingsMenu.vue`、`webui/src/App.vue`、`webui/src/api.ts`、`tests/test_desktop_shell.py`（仅迁出）、`tests/test_pipeline_exec_accounts.py`（仅追加 effective_data_dir 用例）、`.specify/memory/constitution.md`（仅模块地图登记）
- **Forbidden files**: `webui/store.py`、`webui/source.py`、`webui/ai.py`、`webui/tuning.py`、`webui/pipeline_exec.py`、`webui/settings_api.py`、`webui/browser_support.py`、`scripts/boss_cdp_raw.py`；任何数据库迁移
- **New files**: `packaging/window_state.py`、`scripts/boss/browser_registry.py`、`webui/browser_registry_api.py`、`webui/src/components/BrowserSettingsDialog.vue`、`tests/test_desktop_window_state.py`、`tests/test_browser_registry.py`、`webui/src/components/__tests__/BrowserSettingsDialog.spec.ts`（路径以现有前端测试约定为准）
- **Reference direction**: `desktop.py → window_state.py`；`webui → scripts/boss`（单向向下）；`browser_registry_api → browser_registry`；前端 `App.vue → 组件`、`api.ts → 端点`
- **Line gate**: `desktop.py` < 600；新 Python 模块 < 600；Vue < 900；`App.vue` < 900；`test_desktop_shell.py` 只减不增

## Verification Gate

- 功能交付门禁：聚焦测试（T029 列明模块）、后端全量、前端测试、`npm run build`、仓库卫生检查。
- 收口任务（版本提升/打包/提交/推送）：按根 `AGENTS.md` 收口边界，不在本清单生成全量测试项。
- 真机冒烟（Windows）为 US1/US2/US3 各自收尾任务的一部分，结果记录进 quickstart.md。

---

## Phase 1: Setup

- [x] T001 核对冻结共识：通读 `specs/029-desktop-window-browsers/`（spec/plan/research/data-model/contracts/quickstart），确认与本任务清单一致、`git status` 无与本批无关改动

## Phase 2: Foundational — 窗口状态域（US1/US2 共同前置）

- [x] T002 新建 `packaging/window_state.py`：迁移 `packaging/desktop.py` 的窗口状态实现（`_read_default_size`/`load_window_state`/`save_window_state`/常量）并按契约改造——`DEFAULT_WIDTH/HEIGHT` 改 1545/900、schema 3 读写、schema 2 升级规则（正常继承 / 污染作废，research D2）、读时工作区钳制（workarea_provider 注入，纯函数，research D3）、返回值含 `maximized`（签名变更为 5 元组，契约 desktop-window-state.md）
- [x] T003 [P] 新建 `tests/test_desktop_window_state.py`：schema 3 写读往返、schema 2 正常继承 / 全屏污染矩形作废、非法 JSON / 缺字段 / 越限值、读时钳制（含 1366×768 小屏）、`default_*` 覆盖与钳制、无记忆返回（默认普通矩形 + maximized=True）——先于 T002 实现完成后立即跑绿
- [x] T004 `packaging/desktop.py` 瘦身接兼容：窗口状态实现迁出后改为 `from packaging.window_state import ...` re-export（730 行降至 <600，宪法 II/VI 分流），确认 `tests/test_desktop_shell.py` 既有 import 不破
- [x] T005 `tests/test_desktop_shell.py` 用例迁移：窗口状态相关用例移入 `tests/test_desktop_window_state.py`（文件只减不增，line gate），迁移后 `uv run python -m unittest tests.test_desktop_window_state tests.test_desktop_shell` 全绿

**Checkpoint**: 聚焦测试绿；`desktop.py` < 600 行；旧调用面（re-export）无回归。

## Phase 3: User Story 1 — 窗口状态记忆可信（P1 · 核心 Bug）

**Story Goal**: 最大化关窗不再覆盖普通记忆；关窗→重启→还原全程记忆一致（spec US1 场景 1–7）。

**Independent Test**: 拖动→最大化→关→开（最大化）→还原（回拖好矩形）→关→开（一致）；纯编排层可单测，真机冒烟一次。

- [x] T006 [US1] `packaging/window_state.py` 新增 `WindowStateTracker`：`on_resized/on_moved/on_maximized/on_restored` 仅更新内存（最后普通矩形 + 当前最大化态），`snapshot_for_save(current_w, current_h, current_x, current_y) -> dict` 按 research D1 规则产出落盘内容（最大化 → 冻结普通矩形 + maximized:true；普通态 → 当前矩形）
- [x] T007 [P] [US1] `tests/test_desktop_window_state.py` 增加 Tracker 状态转移用例：NORMAL→MAXIMIZED→restored 回冻结矩形、最大化期间 resized 不得改普通矩形、snapshot 双分支、事件乱序（maximized 前无普通值时回退默认）
- [x] T008 [US1] `packaging/desktop.py` 事件接线：订阅 `resized/moved/maximized/restored`（pywebview 6.x events API）驱动 Tracker；`_on_closing` 改用 `snapshot_for_save` 落盘；`_quit_and_cleanup` 复用同一路径；事件 API 不可用 → 记日志退化为旧行为（research D1 兜底）
- [x] T009 [US1] `tests/test_desktop_shell.py` 编排层用例：伪造 window + events 验证 closing 落盘分支——最大化态写 `{普通矩形, maximized:true}`、全屏矩形永不写入、普通态写当前值
- [ ] T010 [US1] 真机冒烟：按 `quickstart.md` §2 场景 3/4 在 Windows 实机执行并记录结果（结果勾选写回 quickstart.md）

**Checkpoint**: US1 独立可验收（场景 1–7）；关窗写盘分支单测覆盖。

## Phase 4: User Story 2 — 首开默认最大化 + 1545×900（P2）

**Story Goal**: 全新安装/无记忆/污染记忆一律最大化开窗；普通默认 1545×900 居中，小屏钳制（spec US2 场景 1–5）。

**Independent Test**: 删除记忆文件启动 → 最大化；还原 → 1545×900 居中。依赖 Foundational 的读路径语义（T002/T003 已覆盖大部分）+ 启动接线。

- [x] T011 [US2] `packaging/desktop.py` 启动接线：`create_window` 依据 `load_window_state` 返回的 `maximized` 传 `maximized=True`（research D7，避免首帧闪烁）；无记忆路径驱动首开最大化
- [x] T012 [P] [US2] `tests/test_desktop_shell.py` 启动编排用例：记忆 `maximized:true` → create_window 收到 maximized=True；schema 2 污染记忆 → 同首开；`default_*` 覆盖进入普通默认
- [ ] T013 [US2] 真机冒烟：按 `quickstart.md` §2 场景 1/2/5/6/7 在 Windows 实机执行并记录结果（含更新重启路径与换小屏钳制）

**Checkpoint**: US2 独立可验收（场景 1–5）；US1 冒烟不回归。

## Phase 5: User Story 3 — 多 Chromium 浏览器支持（P3）

**Story Goal**: 注册表 8 家 + 探测 + 设置页选择 + 手动路径校验 + 启动链路接线 + 数据目录命名空间（spec US3 场景 1–7）。

**Independent Test**: 设置中选浏览器（或手填路径）→ 抓取专用实例以所选浏览器打开；与窗口记忆零耦合。

- [x] T014 [P] [US3] 新建 `scripts/boss/browser_registry.py`：`BROWSER_REGISTRY` 8 条（key/name/exe_names/path_candidates/data_dir_key，research D4 清单）、`detect_browsers()`（候选路径存在性）、`resolve_executable(selection)`（registry key / manual path / auto 缺省 → chrome→edge）、`validate_manual_path(path)`（`--version` 探活超时 10s + Chromium 家族判定，research D5）、`all_registry_exe_names()`；runner/存在性检查可注入
- [x] T015 [P] [US3] 新建 `tests/test_browser_registry.py`：注册表完整性断言（8 条、key/data_dir_key 唯一、字段合法——真机无浏览器时的兜底验收）、探测命中/未命中（tmp 假路径）、validate_manual_path（fake runner：可执行通过 / Firefox 输出 kernel_incompatible / 超时 / 不可执行）、resolve_executable 三模式、effective_data_dir 派生不变量
- [x] T016 [US3] `scripts/boss/constants.py`：`detect_chromium_browsers()` 改查注册表（chrome/edge 条目），`DEFAULT_CHROME_PATH` 语义保留（缺省回退），模块内 re-export 兼容旧符号
- [x] T017 [US3] `scripts/boss/browser.py`：`iter_chrome_process_commands` 的 PowerShell 进程名过滤器与 `is_chrome_command` 改接 `all_registry_exe_names()`（仅名单替换，行为不变）
- [x] T018 [US3] `webui/pipeline_exec_accounts.py` 新增 `effective_data_dir(profile_dir, browser_key)` 纯函数（data-model 实体 4：chrome/edge 恒等、其余 `<父>/chrome-profile-<key>/<名>`）；用例追加进 `tests/test_browser_registry.py`（或 `tests/test_pipeline_exec_accounts.py`，138 行余量充足）
- [x] T019 [US3] `webui/pipeline_exec_chrome.py`：启动命令 exe 改经 `resolve_executable`（读 `advanced_settings.json` 的 `browser`/`browser_manual_path` 键，经现有 load/save 通道，research D5）；CDP `/json/version` 的 `Browser` 字段 Chromium 判定失败 → 明确报错中止（不复用含糊失败码，契约 browser-registry-api.md）
- [x] T020 [P] [US3] 新建 `webui/browser_registry_api.py`：`register_*(app, ctx)` 三端点 GET/PUT `/api/browser-registry`、POST `/api/browser-registry/validate-path`（请求/响应/错误码按契约；manual 保存前强制探活）；用例（Flask test client，fake registry）追加进 `tests/test_browser_registry.py`
- [x] T021 [US3] `webui/app.py`：新增 `browser_registry_api` 注册调用（仅一行组装，门面职责内）
- [x] T022 [P] [US3] `webui/src/api.ts`：`fetchBrowserRegistry / saveBrowserSelection / validateBrowserPath` 客户端方法（类型补充 `webui/src/types.ts`）
- [x] T023 [US3] 新建 `webui/src/components/BrowserSettingsDialog.vue`：8 条单选（未安装置灰、展示探测路径）、手动路径分支（输入 + 即时校验按钮消费 validate-path + 错误文案直显）、当前生效路径展示、保存成功即生效提示（契约前端交互节）
- [x] T024 [US3] `webui/src/components/AppSettingsMenu.vue`：新增「浏览器」菜单项与 emit（仿浏览器账号项）
- [x] T025 [US3] `webui/src/App.vue`：挂载 BrowserSettingsDialog、接线 open-browser-settings 事件
- [x] T026 [P] [US3] 新建 `webui/src/components/__tests__/BrowserSettingsDialog.spec.ts`：清单渲染（未安装置灰）、选择保存调用、手动路径校验失败文案直显、kernel_incompatible 提示
- [ ] T027 [US3] 真机冒烟：按 `quickstart.md` §3 在 Windows 实机执行（Chrome/Edge）并记录结果

**Checkpoint**: US3 独立可验收（场景 1–7）；单测兜底覆盖未安装的 6 家。

## Phase 6: Polish & Cross-Cutting

- [x] T028 行数门禁复核：`wc -l` 校验 `desktop.py` < 600、新模块 < 600、Vue < 900、`App.vue` < 900、`test_desktop_shell.py` 不高于迁出前（828）
- [ ] T029 全量验证门禁（宪法 V）：聚焦测试（`tests.test_desktop_window_state`、`tests.test_browser_registry`、`tests.test_desktop_shell`、`tests.test_pipeline_exec_accounts`）→ 后端全量 `uv run python -m unittest discover -s tests` → 前端 `npm run test -- --run` → `npm run build` → `uv run python -m unittest tests.test_repo_hygiene`
- [x] T030 模块地图登记：`.specify/memory/constitution.md` 模块地图小节追加三行——`packaging/window_state.py`、`scripts/boss/browser_registry.py`、`webui/browser_registry_api.py`（路径 + 一句话职责，宪法原则 VI）
- [x] T031 冒烟记录归档：quickstart.md §2/§3 结果全部勾选完毕；`roadmap/BACKLOG.md` B082 状态改为「待验证」并附 quickstart 链接（版本提升/CHANGELOG 属收口任务，不在本批次）

---

## Dependencies & Execution Order

```text
Phase 1 (T001)
  └─ Phase 2 Foundational (T002→T004→T005；T003 与 T002 并行成对跑)
       ├─ Phase 3 US1 (T006→T008→T009→T010；T007 与 T006 并行)
       │    └─ Phase 4 US2 (T011→T012→T013)
       └─ Phase 5 US3 (T014/T015 并行 → T016→T017→T018→T019→T021；T020/T022 并行；T023→T024→T025→T026→T027)
            └─ Phase 6 Polish (T028→T029→T030→T031)
```

- US1/US2 共享窗口状态域，**必须先后执行**（同文件编排层）；US3 与 US1/US2 零耦合，Phase 5 可与 Phase 3/4 并行（不同文件集）。
- 真机冒烟（T010/T013/T027）依赖对应故事代码任务全部完成。

## Parallel Example: Phase 5 内部

```text
# 批次 A（后端域，与前端批次 B 无文件交集，可并行）：
Task: "T014 注册表域 scripts/boss/browser_registry.py"
Task: "T015 注册表域单测 tests/test_browser_registry.py"
Task: "T020 路由域 webui/browser_registry_api.py"
Task: "T022 api.ts 客户端 webui/src/api.ts"

# 批次 B（前端组件，依赖 T022 类型）：
Task: "T023 BrowserSettingsDialog.vue"
Task: "T026 组件测试 spec"
```

## Implementation Strategy

- **MVP = Phase 1–4**（Foundational + US1 + US2）：核心 Bug 修复 + 首开体验闭环，US3 未开始即可独立交付价值。
- **增量交付**：每个 Story Checkpoint 处可停验证；US3 整段后置不影响前两个故事。
- **单人执行顺序建议**：T001→…→T013（窗口记忆全链 + 冒烟）→ T014–T027（浏览器链 + 冒烟）→ T028–T031（收尾）。
- 提交节奏：每个任务或逻辑组提交一次（Conventional Commits）；实施阶段的提交不触发收口边界（不打包、不提版本）。

## Notes

- 行为变化先由失败测试定义（T003/T007/T009/T012/T015/T020/T026 均先于或伴随实现）。
- 禁改文件见 File Boundaries；`webui/app.py` 仅允许一行注册调用。
- 真机冒烟结果必须写回 quickstart.md，不留口头记录。
