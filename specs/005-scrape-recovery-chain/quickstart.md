# 验证指南：抓取恢复链路修复

**创建日期**：2026-08-09 | **Plan**：[plan.md](plan.md) | **Contract**：[contracts/state-recovery.md](contracts/state-recovery.md)

## 前置条件

- 仓库根目录运行后端与前端测试工具可用：`uv`、`npm`。
- 数据库为本地测试库或临时库；不在真实账号上执行抓取验证。
- 真实风控日志缺失，风控验证使用构造样本与单元测试，不要求真实触发平台风控。

## 后端聚焦验证

```powershell
uv run python -m unittest tests.test_healthy_pipeline tests.test_webui_app tests.test_source tests.test_cooldown tests.test_repo_hygiene
```

**期望**：新增用例与既有用例全绿；卫生测试通过。

## 前端聚焦验证

```powershell
cd webui
npm test
```

**期望**：新增 `DiscoveryView`/`JobWorkspace` 组件测试全绿；既有测试无回归。

## 场景验证

### 场景 A — 失败/暂停/中断恢复不归零

1. 构造已写 `scrape_run_jobs` 的任务，分别置为 `failed/paused/interrupted(restart)`。
2. 调用 `/api/latest-running-task`，断言 `has_task=true`、`scraped_count` 等于真实岗位数。
3. 断言 `scraped_count` 等于 `scrape_run_jobs` 行数；已有更新结果快照时旧 failed 不恢复。
3. 调用 `/api/task-state/<run_id>`，断言 `source_total/total` 与 DB 一致。
4. 前端刷新后进入 02 页，断言显示真实数量，不显示 0。

### 场景 B — 续跑失败后结束保存

1. 暂停任务 → 续跑 → 再失败。
2. 调用 `/api/task/finish/<run_id>`，断言 200，不被“已被续跑接管”拒绝。
3. 断言返回 `result.jobs` 带 `platform`，`scrape_task_id` 与父任务一致。
4. 刷新后断言该任务不再出现在 `/api/latest-running-task`。

### 场景 C — 运行中结束保存

1. 构造 `running` 且已有 `scrape_run_jobs` 的任务。
2. 调用 `/api/task/finish/<run_id>`，断言 200，结果包含已抓岗位。
3. 等待 worker 退出后断言 DB 终态仍为 `interrupted/user_finished`，未被 worker 覆盖。
4. 断言快照只包含请求时已持久化岗位；未落库批不进入。

### 场景 D — 保存后继续 AI 筛选

1. 结束保存后进入结果页或 03 页。
2. 断言存在“继续 AI 筛选”入口。
3. 点击后断言请求携带父 `scrape_task_id`，不报“缺少任务/抓取任务不存在”。

### 场景 E — 全部重抓入口与布局

1. 构造混合平台待确认岗位。
2. 在“全部/BOSS/智联”三档断言“全部重抓（N）”可见。
3. 在“全部”点击后断言出现平台选择引导，且没有发起重抓请求。
4. 选择 BOSS 后断言按 BOSS 来源启动重抓。
5. 桌面 1440×900 与窄屏 390×844 检查滑块与按钮不重叠、无横向溢出。
6. 断言平台选择引导显示各平台待确认数量；数量为 0 的平台禁用或明确提示。

### 场景 F — 风控判定与文案隔离

1. 普通文案（如“登录解锁更多职位”）失败样本：断言不暂停、不写冷却。
2. 高置信文案/HTTP 429/验证码样本：断言暂停并写冷却，`from_run` 可展示。
3. 智联失败样本：断言 `pause_info.error_reason` 不含 BOSS 字样。
4. 智联重抓暂停样本：断言重抓路径生成的暂停原因同样不含 BOSS 字样。

## 最终全量

```powershell
uv run python -m unittest discover -s tests -p "test_*.py"
cd webui && npm test && npm run build
```

**期望**：全量通过；`webui/dist` 与源码同步；无未收敛失败。

## 实际验证记录（2026-08-09）

- 后端全量：2006 例，2003 通过、3 跳过，全部通过。
- 前端全量：`cd webui && npm test`，277 例全部通过。
- 构建同步：`cd webui && npm run build` 通过，`webui/dist` 与源码一致；两个新 hash 资产已纳入版本控制，卫生测试通过。
- 真实视口：`python tests/sc015_viewport_check.py` 在 375×812、390×844、768×1024、1440×900 全部 PASS；无横向溢出、滑块/按钮无重叠、按钮文字无截断。
- 场景 A-F：以自动化测试执行并记录于 tasks.md T043；对应测试全绿。
