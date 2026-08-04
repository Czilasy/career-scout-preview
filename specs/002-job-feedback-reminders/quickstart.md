# 验收手册：岗位反馈闭环与投递过期提醒

本手册用于实现后的聚焦验证、最终回归和真实 UI 验收。当前 Plan 阶段不执行这些尚不存在的测试。

## 前置条件

- 使用临时 SQLite 副本或测试数据库，不直接拿唯一正式数据做破坏性实验。
- 准备一个当前画像 A、另一个画像 B。
- 准备至少一个 BOSS 岗位和一个智联岗位，二者均有权威 `platform + platform_job_id + canonical_url`。
- 所有 fixture 不含真实 Cookie、Key、简历、用户名或本地绝对路径。
- 时间边界测试向领域函数注入固定 `now`，不能等待真实 30 天或修改系统时钟。

## 聚焦自动验证

### 1. migration 28 与存储

```powershell
uv run python -m unittest tests.test_webui_store tests.test_job_feedback
```

必须证明：

- v27 数据库先产生经过验证的本地备份和 manifest，再升级到 28。
- `last_follow_up_at`、事件/命令回执表和索引存在；重复初始化幂等。
- 迁移失败时源库保持 v27，无半张表或部分数据。
- profile_jobs、feedback_events 行数与关联守恒；没有猜填 applied_at 或生成伪历史事件。
- 所有状态/时间 action 的快照、命令回执和可选事件要么一起成功，要么一起回滚。

### 2. AI 建议

```powershell
uv run python -m unittest tests.test_job_advice tests.test_ai
```

覆盖：AI 正常、未配置、缺 Key、超时、网络失败、无效 JSON、非法 action、缺 JD。每项只返回 `follow_up/review`，并断言 profile_jobs 与事件计数前后相同。

### 3. pipeline 权威身份

```powershell
uv run python -m unittest tests.test_pipeline_job_identity tests.test_webui_store
```

覆盖 BOSS/智联完整三元组、跨平台同裸 ID、URL 与平台错配、两个索引命中不同记录、不完整身份和内部 ID/三元组冲突。所有失败路径断言 profile_jobs、feedback_events 和 events 零副作用。

### 4. HTTP 合同

```powershell
uv run python -m unittest tests.test_job_feedback_api tests.test_webui_app tests.test_workbench_api
```

覆盖 action allowlist、时间错误、幂等重放、异载荷冲突、提醒 total/list、事件轨迹、AI 兜底、legacy PATCH 门禁和偏好反馈兼容。

### 5. 前端组件与集成

```powershell
Set-Location webui
npm test -- src/components/__tests__/ReminderDrawer.spec.ts src/components/__tests__/JobLifecycleActions.spec.ts src/__tests__/jobFeedback.spec.ts src/__tests__/App.spec.ts src/views/__tests__/DiscoveryView.spec.ts
npm run build
```

必须证明 profile 切换丢弃旧响应、失败不乐观提交、提醒查看不清除、每条快捷动作独立 busy、同一不确定重试复用 request ID、明确第二次跟进生成新 ID。

## 核心数据矩阵

固定 `now = 2026-08-05T00:00:00+00:00`：

| 样本 | status | applied_at | last_follow_up_at | 预期 |
| --- | --- | --- | --- | --- |
| A | applied | `2026-07-06T00:00:01+00:00` | NULL | 不提醒，差 1 秒 |
| B | applied | `2026-07-06T00:00:00+00:00` | NULL | 提醒，恰好 720h |
| C | applied | `2026-07-05T23:59:59+00:00` | NULL | 提醒，超过 1 秒 |
| D | applied | 更早 | `2026-07-20T00:00:00+00:00` | 不提醒，以跟进为准 |
| E | applied | NULL | NULL | 不提醒，提示补投递时间 |
| F | stale | 更早 | NULL | 不提醒 |
| G | read | 更早 | NULL | 不提醒 |
| H | applied | 合法且更早 | `not-a-time` | 不提醒，报告数据无效，不回退 applied_at |

再加入与 B 同条件的 BOSS/智联岗位，两者必须同时出现；加入画像 B 的逾期岗位，不得出现在画像 A。创建 101 个逾期项，断言 `total=101`、items 长度 100、baseline 最早的 100 条先返回。

## 生命周期主链

对同一岗位按顺序执行：

1. `mark_read`：状态 read；刷新后不变；只打开详情不新增事件。
2. `mark_applied`：不传时间，保存服务器当前 UTC；重复同 request ID 不新增 receipt/event。
3. `correct_applied_at`：合法过去时成功；未来、无时区和晚于已有跟进时失败且原值不变。
4. `follow_up`：状态仍 applied，last_follow_up_at 更新，提醒退出；新 request ID 再跟进产生第二个事件。
5. `mark_stale`：状态 stale，时间保留，提醒退出。
6. `restore_applied`：原 applied_at 保留，恢复时刻成为 last_follow_up_at；同 request 重放不刷新。
7. `correct_status`：纠正为 read/new/interested/deleted 均允许；纠正回 applied 必须有真实 applied_at。
8. 读取事件：只看到真实变化的客观事件，顺序与快照形成过程一致；no-op receipt 不出现，feedback_events 内容和数量不变。

## 并发与回滚

- 两个线程同时提交同一 request ID/同载荷：一个实际提交，另一个 replay；只有一个 receipt，实际变化时只有一个 event。
- 同一 request ID/不同 applied_at：一个成功，另一个 `idempotency_conflict`。
- 两个不同 request ID 同时 follow-up：都代表明确现实动作，事务串行提交两个事件，最终时间为后提交者时间。
- 在 event/receipt insert 前注入异常：snapshot 不更新。
- 在 snapshot update 后注入异常：transaction rollback，snapshot、receipt 与 event 都不变。
- AI 请求与 follow-up 并发：AI 响应不覆盖状态，也不参与状态 transaction。

## 偏好反馈兼容

1. 先对岗位写 interested feedback，确认 `profile_jobs.status=interested`。
2. 再执行 `mark_applied`，确认当前状态为 applied。
3. 重新打开结果/详情，仍显示 applied，不能因历史反馈聚合回 interested。
4. feedback_events 原记录仍存在且偏好计数不变。
5. 用户再次显式点击收藏时，允许当前状态变为 interested；这是新操作，不是历史反馈隐式覆盖。

## 服务重启与持久化

实现包含 Python 后端和 migration，完成后必须由执行代理重启受影响 Flask 服务并检查健康接口。重启前记录一个 applied/last_follow_up 快照和事件序号；重启后：

- 快照与事件完全一致。
- reminder 由数据库重新计算，不依赖旧浏览器计时器。
- AI 建议没有从上次请求持久恢复。

## 真实 UI 验收

启动本地开发环境后，用 Playwright 或真实 Chrome 检查：

### 1440×900

1. 当前画像有 2 个逾期岗位，顶部徽标显示 2。
2. 打开抽屉，两个平台岗位混排，最长未活动在前。
3. 查看详情并关闭抽屉，徽标仍为 2。
4. 请求 AI 建议，显示 action/reason/source，徽标仍为 2。
5. 记录跟进，服务端刷新后总数变 1。
6. 标记另一项已荒废，总数变 0。
7. 检查 header、抽屉、详情、按钮和文字无重叠/横向溢出。

### 390×844

重复徽标、打开抽屉、进入岗位、跟进、荒废、恢复、纠正和建议主链。确认抽屉操作可达、日期表单可提交、错误文案完整、按钮点击区足够，页面无横向滚动或双滚动条。

截图和 console/network 结果作为验收证据；必要时做页面尺寸和 canvas/像素非空检查，但本功能不含 3D/canvas 主场景。

## 最终回归

在所有聚焦测试通过后，基于最终代码执行一次干净全量验证：

```powershell
uv run python -m unittest discover -s tests -p "test_*.py"
Set-Location webui
npm test
npm run build
Set-Location ..
uv run python -m unittest tests.test_repo_hygiene
git status --short
git diff --check
```

仓库卫生失败时禁止 commit。当前工作区已存在与本功能无关的构建产物和临时文件，实施时必须只暂存本功能文件，不还原或提交用户改动。

## 完成门禁

只有以下全部成立才可进入交付结论：

- Spec 的 27 个验收场景和 10 条成功标准都有自动或真实证据。
- Python 全量、前端全量、构建和 repo hygiene 基于最终代码通过。
- 数据迁移备份、失败回滚、跨重启和跨 profile 已验证。
- BOSS/智联无平台漏提醒，身份冲突零副作用。
- 1440×900 和 390×844 主链真实渲染通过。
- 严格档独立审查没有阻断项，或原阻断项已聚焦复查关闭。
