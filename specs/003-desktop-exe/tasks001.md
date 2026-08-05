# Task 001：BOSS programmatic 执行入口

**所属 Wave**：1（并行） | **用户故事**：EXE4（任务照常执行）、EXE5（源码模式零回归）

## 必读文件

- 仓库根 `AGENTS.md`
- `specs/003-desktop-exe/spec.md`、`contracts/inprocess-runner.md`（冻结合同）
- `scripts/boss_cdp_raw.py`（本次写入对象）、`webui/process_executor.py`（只读，理解子进程语义）、`tests/` 下既有脚本相关测试（只读，回归基线）

## 写入范围（互斥）

`scripts/boss_cdp_raw.py`、`tests/test_boss_programmatic.py`（新增）。**禁止**修改 `webui/` 任何文件、`main()` 逻辑、`process_executor.py`。

## 原子清单

- [ ] T001 [P] 读取 `main()` / `scrape_list` / `scrape_details` / `check_login_state`，记录 CLI 参数全集、退出码语义（1/2/10）、可复用模块函数签名，作为实现基线
- [ ] T002 在 `tests/test_boss_programmatic.py` 添加**先失败**参数等价测试：programmatic 与 CLI 相同输入产生相同产物（用 fixture 或函数替身，不依赖真实 Chrome/CDP）
- [ ] T003 添加日志转发测试：`on_log` 收到与子进程 stdout 一致的日志行序列
- [ ] T003a 添加轮询回调测试：`on_poll` 在列表逐页与详情逐岗位检查点被调用；不传时零影响（`cancel_event=None` 与 `on_poll=None` 组合下行为与现状完全一致）
- [ ] T004 添加取消测试：`cancel_event` 置位后快速停止、已写产物保留
- [ ] T005 添加异常测试：CDPUnavailable / RiskControl / LoginRequired / SearchCancelled 原样抛出且携带必要信息
- [ ] T006 实现 `run_search_programmatic(**params, on_log=None, cancel_event=None)`：参数 dict 直传（不经过 argv 文本）、`contextlib.redirect_stdout` → `on_log`、返回 `{"list_data", "details"}`、失败抛异常；**`main()` 零改动**
- [ ] T007 在 `scrape_list` 逐页循环与 `scrape_details` 逐岗位循环增加可选 `cancel_event` 检查点（`cancel_event is not None and cancel_event.is_set()` 时抛 `SearchCancelled`）；不传时行为与现状完全一致
- [ ] T008 新增异常类：`SearchCancelled`、登录失效异常（命名与既有体系风格一致），与 `RiskControlError` / `CDPUnavailableError` 同级
- [ ] T009 运行聚焦测试 + 既有脚本相关测试全绿（证明 `cancel_event=None` 零影响）
- [ ] T010 提交：仅 `scripts/boss_cdp_raw.py`、`tests/test_boss_programmatic.py`，信息 `feat: add in-process search entry`

## 完成定义

聚焦测试全绿；`main()` 与 CLI 路径无 diff 语义变化（`git diff` 审查）；既有脚本测试零回归；卫生测试通过。

## 提交纪律

只暂存本包文件；commit email `czyooutzilas@gmail.com`；提交前 `git diff --check` 与 `git status --short`。