# Quickstart: 036 v2 验证指南

本指南用于实现阶段验证，不代表当前已经执行或通过。真实 E2E 必须经项目正式桌面入口，不能用空白窗体、mock 或单元测试替代。

## 0. 前置条件

1. 当前需求基线是 `specs/036-titlebar-dynamic-island/v2/spec.md`。
2. R0 独立拆分 Spec 已完成，`packaging/desktop.py` 不再拥有独立 WndProc 实现且既有行为测试通过。
3. 当前分支没有无关改动，Windows 测试机可运行项目正式桌面包。
4. 至少准备 Windows 10 与 Windows 11；DPI 覆盖 100%、125%、150%，尽量包含双屏混合 DPI。

R0 未完成或 Gate 1 失败时立即停止，不继续功能集成或最终验收。

## 1. Gate 1：精确桌面壳

使用当前项目的正式启动/打包方式，保留 pywebview 6.2.1、WinForms、WebView2、`frameless=True`、真实 WebUI、现有标题栏和三个窗口按钮。

逐项记录 Windows 版本、显示缩放、显示器布局、操作和结果：四边四角 8/8 拉伸；100%、125%、150% DPI 命中；普通移动；最大化从不同横向位置拖下还原；拖顶释放最大化；左右/四角释放无最终贴靠；窗口按钮和靠边页面控件可点击；连续启动关闭至少 10 次无崩溃、卡死或输入残留。

任何一项失败即记录为 `BLOCKED`，不得填写“其余功能通过”。

## 2. 聚焦自动化测试

```powershell
uv run python -m unittest tests.test_window_controls tests.test_desktop_window_state tests.test_desktop_shell
Set-Location webui
npm test -- WindowTitleBar.spec.ts
```

覆盖命中矩阵、最大化状态、DPI、工作区、1024×700 与小工作区例外、schema 3、普通矩形冻结、启动创建参数、hook 生命周期，以及标题栏桌面/浏览器渲染、按钮和旧 drag-region 移除。

## 3. 1024×700 页面走查

在真实桌面窗口调整到 1024×700，逐一检查现有主要页面、抽屉和对话框：核心按钮和表单可触达；没有阻断横向滚动、内容大面积裁切或嵌套双滚动条；标题栏和关闭按钮始终可见。

如果问题位于 `webui/src/styles.css`，可按 Plan 做最小修正；如果需要修改其他 Vue 文件，停止并先修订 Plan 文件边界。

## 4. 记忆与跨屏矩阵

1. 普通拉伸移动后关闭重开并还原。
2. 普通拉伸后最大化关闭，重开再还原。
3. 副屏调整后关闭重开。
4. 保存后断开副屏再重开。
5. 任务栏上/下/左/右布局。
6. 物理工作区小于 1024×700。

所有场景都必须恢复有效普通矩形且不覆盖任务栏。

## 5. 运行中任务与对话框

经项目真实入口启动实际任务，运行期间至少执行 20 次移动、拉伸、最大化和还原；任务不得暂停、重启或丢进度，结果必须可查看。打开现有对话框后重复窗口操作，确认对话框可见、可操作且业务状态不丢失。

## 6. 最终收敛门禁

R0、Gate 1、US1–US4 全部完成且聚焦测试通过后，才执行本节。R0 和各用户故事不得分别重复执行后端全量。

```powershell
uv run python -m unittest discover -s tests
Set-Location webui
npm test
npm run build
Set-Location ..
uv run python -m unittest tests.test_repo_hygiene
git diff --check
git status --short
```

自动化全部通过后，仍需完成 Windows 10 与 Windows 11 的真实桌面包 E2E，才能声称功能完成。

若后端全量失败，必须保留当前输出中的失败清单，随后只运行失败用例、直接受影响测试和必要相邻回归；不得为了重新获取失败名称或在没有相关改动时立即重跑全量。完成实际修复并重新收敛后，才允许进行下一次最终全量确认。

## 7. 失败报告

记录 Windows 版本与 DPI、屏幕布局和任务栏位置、精确操作、预期与实际结果、失败节点及日志时间，并明确写明 036 v2 已停止、未启用替代方案。

## 8. 执行记录

### 8.1 测试环境清单（T003，2026-09-14 实测）

| 项目 | 实际值 | 说明 |
|---|---|---|
| 操作系统 | Windows 10 家庭中文版，版本 10.0.19045（22H2） | 本机可执行 |
| Python | 3.11.15（`uv run` 项目环境） | 与锁文件一致 |
| pywebview | 6.2.1 | 项目声明 `>=6.0.0,<7.0.0` |
| 渲染后端 | WinForms + WebView2（`edgechromium`） | 由 pywebview 平台选择决定 |
| WebView2 Runtime | 150.0.4078.65 | 注册表 `EdgeUpdate\Clients\{F3017226-…}` 读取 |
| 显示器 | 单屏 1920×1080，工作区 1920×1040（任务栏在下） | 屏幕 1920×1080 |
| 系统 DPI | 96（100%） | 本机当前设置 |

### 8.2 目标文件行数测量（T004，2026-09-14）

| 文件 | 测量值 | 红线 | 结论 |
|---|---|---|---|
| `packaging/window_controls.py` | 431 | 800 | 通过（预计本功能后 ≤750） |
| `packaging/window_state.py` | 442 | 800 | 通过 |
| `packaging/desktop.py` | 787 | 800 | 通过（本功能只增 js_api 接线，完成后必须仍 <800） |
| `webui/src/components/WindowTitleBar.vue` | 258 | 1200 | 通过 |

### 8.3 Gate 1 候选变更与实测证据（2026-09-14）

原 Plan 候选（顶层窗体 WndProc 处理 `WM_NCHITTEST`）在真机实测中被否决，证据：

1. 无边框窗口下，鼠标所到之处的最深窗口是 WebView2 子窗口链 `Chrome_RenderWidgetHostHWND` → `Chrome_WidgetWin_1` → `Chrome_WidgetWin_0`，其矩形与整个窗口矩形一致（含四边四角）。
2. 顶层窗体在左/右/上边、右下角、标题带、中心六个采样位置收到的 `WM_NCHITTEST` 次数均为 **0**。
3. 直接对最深窗口调用 `SetWindowLongPtrW` 返回 **0**，`GetLastError` = 5（`ERROR_ACCESS_DENIED`）：该窗口属于 WebView2 运行时的独立进程，跨进程改窗口过程被系统拒绝。

据此按 Plan 自身规则修订 Plan/Research/Contracts/Tasks（不改冻结 Spec），候选改为「页面判定区域 + 一次调用 + 宿主 Win32 执行」，详见 `research.md` D9。上表证据来自临时探针脚本（放在系统临时目录，已删除，未进入仓库）。

### 8.5 Phase 2 自动化证据（T005–T009，2026-09-14）

| 命令 | 结果 |
|---|---|
| `uv run python -m unittest tests.test_window_controls tests.test_desktop_shell tests.test_desktop_window_state` | `Ran 127 tests ... OK`（退出码 0） |
| `cd webui && npm test -- --run WindowTitleBar.spec.ts` | 25 passed |
| `cd webui && npx vue-tsc --noEmit` | 退出码 0（类型检查通过） |

行数（2026-09-14）：`window_controls.py` 662、`window_interaction.py` 549、`desktop.py` 792、`WindowTitleBar.vue` 372；均低于 800/1200 红线。`desktop.py` 余量仅 8 行，已登记为风险：后续任何向该文件追加逻辑的改动都必须先拆分。

结构说明：交互引擎独立成 `packaging/window_interaction.py`（线门禁触发，见 Plan 修订）；js_api 入口由该模块的 `DesktopWindowInteractionApi` 基类提供，`desktop.py` 只保留接线。

### 8.6 Gate 1（T010）状态

自动化部分（T005–T009）已完成；真机 Gate 1 由用户按 §8.7 清单手动操作、观察器只读记录完成（不模拟任何输入），判定依据是「矩形变化 + 壳日志里的 `[interaction]` 行」。

证据（来自应用自身日志）：`[adapter] install workarea=(0,0,1920,1040)`、`[diag] shown: native=Maximized/{Width=1920, Height=1040}`——最大化尺寸等于工作区，未覆盖任务栏。

**首轮真机操作（2026-09-14 12:0x）发现并修复的缺陷**：用户实测「边缘能识别（指针会变）但拖不动」，观察器记录里没有任何窗口矩形变化、桌面壳日志也没有 `[interaction]` 行。定位过程：CDP 直连页面确认 `window.pywebview.api` 已暴露 `window_begin_resize`/`window_begin_move`、调用返回 `{"ok": true}`，但引擎无日志 → 进程内诊断显示 `_cursor_pos()` 返回 `None` → 根因是**拆分模块时漏了 `import ctypes`**，所有 Win32 调用抛 `NameError` 后被「绝不向上抛」的兜底静默吞掉，交互循环在第一步就退出（工作区仍正确是因为走了不依赖 ctypes 的主屏兜底路径）。
   修复：补 `import ctypes`；交互循环增加失败节点日志（`[interaction] <action> FAIL <reason>`）与起始行；`tests/test_window_controls.py` 增加 `NativePrimitiveTests`（指针读取/ctypes 存在/按键读取/非法矩形守卫）防止同类静默回归。
   修复后进程内验证：`begin_move` 在最大化态记录 `restore->(370,390,1400,800)`（横向抓取比例 0.711 回算正确）并落到 `move end released=True travel=0`；聚焦测试 131 用例 OK。

#### 8.6.1 用户验收（2026-09-14）

用户在自己机器上自由操作后明示「测试结束，我已经测试完通过了」。该轮操作留下的机器证据（桌面壳日志 + 观察器记录）：

- 最大化拖下还原（审查整改后的新阈值逻辑）：`move start rect=(0,0,1920,1080)` → `restore->(257,5,1400,800)` → `move end released=True travel=154`（点一下不还原、拖动才还原，抓取比例保持）。
- 顶栏拖动移窗：`move end released=True travel=167` → `top-release maximize`。
- **新尺寸下限（1024×700）首次真机生效**：向下缩到 `…×700`、向右缩到 `1024×700`；向左拖再放大到 `1158×700`。
- 指针反馈区域依次出现 `R / T / TL / L`；最大化/还原后页面图标 `iconCode` 相应切换。

### 8.8 交付链收口证据（2026-09-14）

| 检查 | 命令 | 结果 |
|---|---|---|
| 后端聚焦回归 | `uv run python -m unittest tests.test_window_controls tests.test_desktop_shell tests.test_desktop_window_state` | `Ran 139 tests ... OK` |
| 前端全量 | `cd webui && npm test` | 60 个文件 / **985 用例全过** |
| 前端构建 | `cd webui && npm run build` | 成功（仅既存的 chunk >500kB 提示，非本次引入） |
| 1024×700 页面走查（US4） | CDP 只读测量（实时库拷贝 + 任务运行器关闭，不模拟鼠标） | 主视图与历史/设置/提醒弹层：`docOverflowX=0`、无超宽元素、无嵌套横向滚动条 |
| 空白检查 | `git diff --check` | 无问题（仅 CRLF 提示） |
| 仓库卫生 | `uv run python -m unittest tests.test_repo_hygiene` | 全过（14/14） |
| 后端全量（第 1 轮） | `uv run python -m unittest discover -s tests` | `Ran 3159 tests in 1020.637s`，无代码失败 |
| 后端全量（第 2 轮，审查整改后） | 同上 | **2026-09-14 12:44:36 → 13:02:06**（1050.134s）：`Ran 3163 tests`，无代码失败 |

行数复核（2026-09-14，审查整改后）：`window_controls.py` 475、`window_interaction.py` 535、`window_metrics.py` 302、`window_state.py` 466、`desktop.py` 790、`WindowTitleBar.vue` 385 —— 除 `desktop.py` 外全部低于 600 行预警线；`desktop.py` 790 低于 800 红线。

尺寸语义（US1/US3）落地：`window_state.py` 改为默认 1400×800 / 常规下限 1024×700 / 上限=当前工作区；读时逐轴抬到下限并钳进工作区（小工作区例外）；`min_size` 契约更新为 1024×700。

### 8.9 审查记录（2026-09-14，两轴审查：标准轴 + Spec 轴）

**已修复（发现 → 修复 → 回归）**

| 发现 | 级别 | 修复 |
|---|---|---|
| 启动时 `fix_frameless_size` 固定按 1400×800 改写 `rcNormalPosition`，会把用户拉伸后的记忆改回默认尺寸（破 FR-013/FR-015） | 高 | 改为使用本次加载的普通矩形（记忆或默认）并保持记忆位置；`desktop.py` 传入 `width/height/x/y` |
| 在最大化标题带上点一下（未拖动）就立即还原窗口 | 中 | 新增 `DRAG_START_THRESHOLD_CSS=4` 与 `drag_started()`：越过阈值才还原，纯点击不做任何动作 |
| 首开最大化且无普通矩形记忆时，拖标题带会去移动一个最大化窗口 | 中 | 新增 `_fallback_normal_rect()`：无记忆时回退默认普通矩形，最大化态必定先还原 |
| 顶部 36px 被对话框遮罩/灵动岛等浮层覆盖时，按压会被当成标题带拖动 | 中 | 标题带移动只认真按在 `[data-testid="titlebar-drag-region"]` 上的按压；按钮/链接/表单控件统一排除 |
| macOS 桌面壳也走同一套页面判定（`preventDefault` + 光标类名），FR-019 要求保持原状 | 中 | 增加平台开关：仅 `window.pywebview.platform === "edgechromium"`（Windows 壳）启用 |
| 宪法原则 VI 75% 预警线：`window_controls.py` 661 行仍在增长 | 中 | 新增 `packaging/window_metrics.py`（工作区/DPI/尺寸换算），`window_controls.py` 回落到 475 行 |
| 死代码：`MAXIMIZE_WORKAREA_CLAMP` + `_clamp_to_workarea` 零产品调用方 | 低 | 连同其测试一并删除（职责由适配器的 `WM_GETMINMAXINFO` 承担） |
| 模块 docstring / 任务文本仍描述旧机制（`WM_NCHITTEST`）；042 quickstart 缺执行记录 | 低 | 已同步（`window_controls` 头注释、T015–T017 文本、042 quickstart §6） |

**复核后判定不成立（未改代码）**

- 「关闭落盘读 `window.width/x` 是构造快照」：pywebview 的 `width/height/x/y` 是**实时属性**（`gui.get_size/get_position`），落盘值准确。
- 「Win32 还原不触发 `restored` 事件导致图标/冻结状态不复位」：真机会话记录里最大化/还原后页面图标相应切换（iconCode 1↔2），事件链路已有机器证据。

**更新说明（写入本版 CHANGELOG 与 Release）**

增加：
- 窗口四条边和四个角都可以拖动调整大小
- 顶栏按住就能移动窗口，拖到屏幕顶端会最大化
- 最大化窗口从顶栏往下拖就能还原

修复：
- 调整过窗口大小或位置后重启，窗口会按上次的样子恢复

### 8.7 Gate 1 手动清单（用户操作 + 只读观察）

观察器把窗口摆成 1200×800（左上偏一点）并回到普通态后，按顺序操作：

| # | 操作 | 判定点 |
|---|---|---|
| 1 | 按住右边缘往右拖约 120px 松手 | 只变宽，高度不变 |
| 2 | 按住左边缘往左拖约 60px 松手 | 只变宽，右边不动（缩小方向受当前常规下限 1400×800 限制，待 US3 接入 1024×700 后复验） |
| 3 | 按住下边缘往下拖约 80px 松手 | 只变高 |
| 4 | 按住上边缘往上拖约 60px 松手 | 只变高，下边不动 |
| 5 | 按住右下角往右下拖 | 宽高同时变大 |
| 6 | 按住左上角往左上拖 | 宽高同时变大，位置往左上 |
| 7 | 按住标题栏中间拖动约 (+90,+70) | 整体平移，尺寸不变 |
| 8 | 按住标题栏拖到屏幕最顶部松手 | 最大化，且不盖任务栏 |
| 9 | 从最大化按住标题栏往下拖约 260px 松手 | 还原为普通窗口并跟随指针 |
| 10 | 普通态拖到屏幕最左松手；再拖到最右松手 | 均不出现半屏贴靠 |
| 11 | 点右上角最大化按钮两次 | 先最大化再还原，图标同步 |
| 12 | 在页面中间点两下页面控件 | 页面有反应，窗口不动 |
| 13 | 点右上角 X 关闭应用 | 进程退出（观察器自动结束） |

### 8.4 R0 拆分验收（T002，2026-09-14）

命令：`uv run python -m unittest tests.test_window_controls tests.test_desktop_shell tests.test_desktop_window_state`

结果：`Ran 94 tests ... OK`（退出码 0）。三项原生能力只在 `packaging/window_controls.py` 定义，`packaging/desktop.py` 只保留调用与兼容别名。
