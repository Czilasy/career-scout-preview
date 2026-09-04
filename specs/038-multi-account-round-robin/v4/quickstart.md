# Quickstart: V4 实现与验证指引

本文件供实现代理在完成代码后验证。当前规格阶段不执行真实账号任务。

## 1. 前置检查

1. 阅读 `spec.md`、`plan.md`、`tasks.md`、`research.md`、`data-model.md` 和 `contracts/r2-runtime-events.md`。
2. 确认当前数据库不是通过测试命令误指向正式库；自动化测试只使用测试自带隔离数据。
3. 确认没有修改 `specs/033-log-whitebox/` 下用户现有未跟踪文件。
4. 确认未启动、停止或重启用户当前抓取任务。

## 2. 红灯基线

先运行 V4 新增聚焦测试，确认修复前至少稳定暴露以下问题：

- 11 个 20 条分块在配额 200 时错误地全部落到第一个账号。
- 克隆账号与原账号共享本地熔断状态。
- 局部成功后接力复用尝试产物。
- JD 后续成功但旧待处理记录未清理。

不得只写测试后直接实施；必须保存修复前失败证据。

本轮红灯命令：

```powershell
uv run python -m unittest tests.test_r2_rotation_v4 tests.test_detail_attempts_v4 tests.source.test_source_account_isolation_v4
```

结果：`Ran 6 tests ... FAILED (failures=6)`。失败证据：`$env:TEMP\career-scout-v4-red.txt`。

失败断言包括：跨 chunk 首账号收到 220 条而不是 200 条；浏览器恢复与跨账号接力沿用相同 artifact；成功 JD 未删除 `(pending-run, j1)`；BOSS 克隆共享 breaker 与 executor；Zhilian 克隆共享 breaker。

Phase 2 契约红灯命令同样已执行；结果：`Ran 6 tests ... FAILED (failures=2, errors=4)`。失败证据：`$env:TEMP\career-scout-v4-contract-red.txt`。其中快照模块尚不存在、请求事件接口尚不存在，且两处旧克隆断言在隔离契约下失败。

## 3. 聚焦验证

实现完成后运行 V4 新增测试文件，并至少证明：

- 六账号、配额 200、1048 条的无失败分布为 `200/200/200/200/200/48`。
- 20 条中 18 成功、2 条撞墙时，只把 2 条交给第二账号且第二账号真实执行。
- BOSS 与智联账号熔断互相隔离。
- 预留、请求开始、终态和汇总可核对。
- 每次尝试产物唯一。
- 暂停恢复不重复抓成功岗位，成功后旧 JD 待处理清零。

## 4. 项目验证门禁

按顺序执行：

```powershell
uv run python -m unittest discover -s tests
```

```powershell
Set-Location webui
npm test
npm run build
```

```powershell
Set-Location ..
uv run python -m unittest tests.test_repo_hygiene
git diff --check
git status --short
```

测试输出不得写入项目根目录；需要重定向时使用系统临时目录。

## 5. 真实账号端到端验收

只有在用户明确授权真实测试范围后执行。必须通过项目正式入口，不直接调用内部函数或修改正式数据库。

最小充分场景：

1. 选择至少两个已登录账号并配置足以发生一次正常配额轮换的小配额。
2. 执行一个详情数量超过首账号配额的任务。
3. 从真实请求终态确认第二账号确实启动请求，而不是只出现预留或切换事件。
4. 核对按账号唯一成功数之和等于详情抓取成功总数。
5. 如安全地遇到自然平台阻断，核对只隔离实际撞墙账号；不得主动制造或绕过平台风控。

未执行真实账号验收时，交付状态必须写为“自动化验证完成，真实账号端到端待验收”。

## 6. 本轮自动化验证记录

- V4 聚焦回归：`Ran 25 tests in 0.254s`，`OK`；证据：`$env:TEMP\career-scout-v4-focused-final-4.txt`。
- R1/R2 轮询与 BOSS、智联源回归：`Ran 214 tests in 20.825s`，`OK`；证据：`$env:TEMP\career-scout-v4-existing-source-final-2.txt`。
- 后端全量回归（最新源码）：`Ran 2908 tests in 936.242s`，`OK (skipped=4)`；证据：`$env:TEMP\career-scout-v4-backend-full-final-2.txt`。
- 前端测试：`Test Files 50 passed (50)`、`Tests 765 passed (765)`；证据：`$env:TEMP\career-scout-v4-frontend-test-final-2.txt`。
- 前端构建：`✓ built in 1.01s`；证据：`$env:TEMP\career-scout-v4-frontend-build-final.txt`。
- 真实浏览器用户烟测：使用项目正式入口和隔离 `test` 状态库，按“跳过简历 → Python × 上海 → 账号池两个 R2 配额均设为 2 → 单独抓取”操作，页面从运行中推进到 100%，显示抓取 90 条且去重 90 条；隔离库 `db_meta.env=test`，运行状态为 `scraped_only`。数据库摘要证据：`$env:TEMP\career-scout-v4-ui-smoke-db.txt`。
- AI 筛选入口烟测：点击“开始 AI 筛选”后，页面按真实前置条件阻断并显示“求职画像至少 10 个字”，未发起 AI 或详情请求。
- 本轮未执行真实账号端到端，交付状态：`自动化验证完成，真实账号端到端待验收`。
