# 实施计划：抓取恢复链路修复

**功能目录**：`005-scrape-recovery-chain` | **创建日期**：2026-08-09 | **Spec**：[spec.md](spec.md)

## 风险档位

采用 Harness 严格档（3 级）：跨后端状态机、持久化、接口与前端交互，涉及正式本地数据；实现后需独立审查一次，再按驳回项聚焦复查。

## 目标与约束

修复 B027/B029/B030 并回归 B013：抓取异常后已抓内容可恢复、可结束保存、可从已抓岗位继续 AI 筛选；未跑 AI 筛选的保存结果带平台角标；全部视图重抓入口可见且不混合重抓；风控判定高置信；智联文案不出现 BOSS 内容。不做无关重构，不改抓取/AI 算法，不做版本提升与打包分发。

## 技术上下文

**语言/运行环境**：Python（Flask + SQLite，`webui/`），TypeScript + Vue 3（`webui/src/`）
**依赖**：现有 `webui/store.py`、`webui/app.py`、`webui/pipeline_exec.py`、`webui/source.py`、`webui/cooldown.py`、`scripts/boss_cdp_raw.py`；前端 `DiscoveryView.vue`、`JobWorkspace.vue`、`styles.css`
**存储**：SQLite `screening_runs` / `screening_results` / `scrape_run_jobs` / `screening_pending_results` / `pipeline_checkpoints`；JSON 冷却与登录态缓存；本轮不新增数据库迁移
**测试**：后端 `unittest`，前端 `vitest`，卫生测试 `tests.test_repo_hygiene`
**目标平台**：源码模式与桌面 EXE 模式共用同一 Web UI 与后端
**性能目标**：约 3000 岗位的任务恢复与结束保存应在秒级完成，不因全量岗位重复加载产生明显卡顿
**规模**：本地单用户，最大约 3000 岗位；恢复接口只返回计数与必要快照，不要求一次返回完整列表（列表由保存后的结果接口按平台加载）

## 阶段划分

### Wave 1 — 后端状态链修复（app.py + store.py）

**依赖**：无。**产出**：续跑接管释放、结束保存状态机、运行中结束保护、partial 快照平台与父任务来源。

- `store` 新增原子 `finish_screening_run(run_id)`：允许 `queued/running/paused/failed` 及 `interrupted(process_restart/operator_stop)` 进入 `interrupted + error_code=user_finished + interruption_kind=user_cancelled`；`failed → interrupted` 增加状态机许可；`user_cancelled` 拒绝改写
- `_run_pipeline_task` / `_run_ai_screen_task` / `_run_recrawl_task` 在终态与再次暂停时释放 `_resume_claims`；`api_task_finish` 对陈旧接管标记做兜底释放
- `api_task_finish` 放行 `queued/running/paused/failed/restart-interrupted`；运行中先 `stop_event.set()` 并关闭调试浏览器，再从持久化 `scrape_run_jobs`/verdicts/JD checkpoint 生成 partial 快照；快照边界以 finish 请求时已持久化数据为准，未落库批不保证进入
- 运行中结束后台 worker 的终态写入保护：finish 先原子标记 `user_finished`，worker 在写 DB 终态前检查该标记，已结束则不覆盖终态，只更新内存展示
- `_build_partial_pipeline_result` 增加 `platform` 参数并写入 jobs/dropped；`api_task_finish` 与 `_run_ai_screen_task` 保存快照时写入 `scrape_task_id`
- 测试：结束保存状态机、运行中结束、续跑失败后结束、接管释放、partial 平台字段、快照父任务来源

**完成定义**：失败/暂停/运行中均可结束保存；续跑失败后不再被接管拒绝；partial 结果带平台与父任务来源；worker 不覆盖已结束终态。

### Wave 2 — 恢复接口与真实计数（app.py + store.py）

**依赖**：Wave 1 的状态语义。**产出**：刷新/重启后恢复失败抓取任务，并返回真实数量。

- `latest_running_task` 增加“最近可恢复抓取”兜底：`kind=scrape`、状态为 `paused/failed/interrupted(restart/operator_stop)`、`scrape_run_jobs` 非空、未 `user_finished` 时返回，带 `scraped_count`、`source_total`、`platform`、`scrape_task_id`；`scraped_count` 以 `scrape_run_jobs` 行数为准
- 恢复优先级固定为 running > paused > restart-interrupted > failed；failed 兜底同样套用“已有更新结果快照则跳过”的过期保护
- 正常恢复路径（paused/interrupted）同样返回 `scraped_count` / `source_total`
- `api_task_state` 对恢复后的 failed/interrupted scrape 返回与 DB 一致的 `success_count/source_total/total`；02 页主数字用 `scraped_count` 岗位数，组合进度仅作辅助
- 前端恢复时先接任务、再拉 `task-state` 真实计数，禁止用空快照显示 0
- 测试：failed 抓取刷新恢复、interrupted 恢复计数、user_finished 不再恢复、真实 0 记录仍显示 0

**完成定义**：有已抓数据的失败/中断任务刷新后显示真实数量；无数据仍显示 0；已结束保存任务不再恢复。

### Wave 3 — 结果快照父任务回链（app.py + DiscoveryView.vue）

**依赖**：Wave 1。**产出**：latest-pipeline-result 可回链父抓取任务，03 页可启动 AI 筛选。

- `save_pipeline_result` 的 `script_params/execution_params` 记录 `platform` 与 `scrape_task_id`（AI 完成路径与 finish 路径都写）
- `latest_pipeline_result` 响应增加 `scrape_task_id`
- `DiscoveryView.loadLatestResult` 恢复 `scrapeTaskId`、`resultRunIds`，并防御性回填 `platform`
- `startAiScreen` 在 `scrapeTaskId` 为空时尝试从已加载结果快照恢复，仍为空则明确提示
- 测试：保存 partial 后刷新进入 03 启动筛选；AI 完成快照带父任务；旧快照缺字段不编造

**完成定义**：保存部分结果后刷新，03 页能找到父抓取任务并启动 AI 筛选，不报“缺少任务”。

### Wave 4 — 前端结束保存与继续筛选交互（DiscoveryView.vue + 样式/组件测试）

**依赖**：Wave 1/3。**产出**：运行中/失败可结束保存；保存后不强制跳结果页；提供“查看结果/继续 AI 筛选”。

- 02 页对 `paused/failed/running` 显示“结束并保存结果”（运行中与“停止抓取”并存）
- `finishPausedTask` 成功后可选择停留当前步骤：设置 `scrapeCompleted/scrapeTaskId/resultLoaded`，不自动 `activeStep=results`；页面显示“查看结果”“继续 AI 筛选”入口
- `finishPausedTask` 调用前先清 `pollTimer`，避免旧轮询结果覆盖保存后的新状态
- 结果页也提供“继续 AI 筛选”入口（从已保存结果回 03）
- 测试：running 结束保存、failed 结束保存、保存后停留、两个入口、刷新后恢复

**完成定义**：spec 用户故事 2/3 的前端验收场景全部覆盖。

### Wave 5 — 全部重抓入口与布局（DiscoveryView.vue + JobWorkspace.vue + styles.css）

**依赖**：无独立依赖。**产出**：三档视图入口可见；全部视图引导选择平台；滑块与按钮不重叠。

- 待确认分类下 `resultPlatformFilter !== "all"` 条件移除，三档都显示“全部重抓（N）”
- `recrawlUncertain` 在 `all` 视图打开平台选择引导（BOSS/智联），显示各平台待确认数量，数量为 0 的平台禁用或明确提示；确认后切到对应视图并启动该平台重抓；不发起混合任务
- 单平台视图沿用现有按当前平台 `source_run_id` 重抓逻辑
- `JobWorkspace` 头部布局改为稳定网格/换行策略；滑块不再绝对居中压住右侧按钮；窄屏 390px 与桌面 1440px 均无重叠、无横向溢出
- 测试：三档按钮可见、全部视图引导、单平台重抓、布局断点检查

**完成定义**：spec 用户故事 5 验收场景全部覆盖。

### Wave 6 — 风控判定收紧与文案平台隔离（app.py + source.py + pipeline_exec.py + EnvCheckDialog.vue）

**依赖**：无。**产出**：高置信风控才暂停/冷却；冷却展示来源；文案按平台隔离。

- 收紧 `_SCRAPE_BLOCK_PATTERNS`、`_RISK_CONTROL_REASON_PATTERNS`、`source._RATE_LIMIT_KEYWORDS/_VERIFICATION_KEYWORDS`、`_classify_failed_code`：裸词“频繁/解锁/冻结/登录/verify”不单独判受限；只有明确拦截文案、HTTP 429/403/412/418、验证码/滑块、解封时间等高置信信号才暂停
- `_record_risk_signals` 仅对 `source_rate_limited/source_verification_required` 写冷却与 restricted 缓存；通用 `source_blocked` 不再写冷却/restricted
- `env-check` 冷却记录返回 `from_run`，前端展示来源；既有“解除冷却”入口保留
- `_FAILED_CODE_LABELS` / `ERROR_TAXONOMY` 增加平台参数或平台化映射；列出全部消费点（`pipeline_exec` emit、`_run_recrawl_task`、`_pause_recrawl_source_unavailable`、`api_task_state` 等）统一走平台化入口，`api_task_state` 按 run 平台取文案
- 测试：普通词不判受限、翻到底不判、高置信判定、冷却写入边界、智联文案无 BOSS、环境检查来源展示

**完成定义**：spec 用户故事 6 验收场景全部覆盖；B013 回归通过。

### Wave 7 — 全量验证与回归

**依赖**：Wave 1-6。**产出**：最终交付证据。

- 后端：`uv run python -m unittest`（至少 `test_healthy_pipeline`、`test_webui_app`、`test_source`、`test_cooldown`、`test_repo_hygiene`）
- 前端：`npm test` + 新增组件测试全绿
- 构建同步：`webui/dist` 与源码一致（`npm run build` 后由仓库规则校验）
- 桌面与窄屏：Playwright/既有视口检查至少各一次，检查 0 状态、按钮、滑块、文案截断

**完成定义**：最终代码全量验证通过，无未收敛失败；Spec 验收条目可执行。

## 项目结构

```text
specs/005-scrape-recovery-chain/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/state-recovery.md
└── checklists/requirements.md

webui/app.py                  # 状态链、恢复接口、finish、风控分类、平台文案
webui/store.py                # finish 状态机、结果快照父任务来源
webui/source.py               # 失败码分类、冷却写入边界
webui/pipeline_exec.py        # 平台化文案
webui/src/views/DiscoveryView.vue
webui/src/components/JobWorkspace.vue
webui/src/components/EnvCheckDialog.vue
webui/src/styles.css
tests/                        # 后端回归
webui/src/__tests__/          # 前端回归
```

## 验证门禁

- 每个 Wave 完成时跑对应聚焦测试，不机械全量
- Wave 7 跑最终全量；出现新失败回到“定位 → 聚焦修复 → 聚焦验证 → 相关回归”
- 交付前输出：已改文件、测试证据、未验证边界；不做任何仓库同步或产物分发动作
