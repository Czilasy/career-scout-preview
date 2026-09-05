# Quickstart: 033 V2 验证指南

**Created**: 2026-09-05 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

本文供实施者和审查者验证“只有证据齐全才成功”。当前文件不证明实现已经完成。

## 安全前置

1. 运行 `uv run python scripts/db_info.py`，确认当前数据库环境。
2. 自动化测试必须使用临时数据库和系统临时目录。
3. 禁止对正式数据库做迁移实验、插入、更新或删除。
4. 禁止操作或使用任务 `45a2dcd730334002b21f30e18d5008e6`。
5. 真实端到端验证需要用户另行给出真实输入和允许写入边界。
6. 不得把 `tests/test_e2e_smoke.py` 称为真实端到端。

## 验证顺序

### 1. 纯规则

```powershell
uv run python -m unittest tests.test_whitebox_rules
```

必须覆盖：

- 全计划单元完成且有结果 → `succeeded`
- 全计划单元完成且均有明确空证据 → `empty`
- 有结果且一组失败 → `partial`
- 无结果且有明确失败 → `failed`
- 任一必需证据缺失 → `unverifiable`
- 取消或停止 → `interrupted`
- 降级不覆盖结论
- 低数量不改变结论
- 重复收口幂等

### 2. 存储与迁移

```powershell
uv run python -m unittest tests.webui_store.test_store_whitebox
```

必须覆盖：

- 临时数据库从旧版本迁移到 schema 33；
- 不回填或改写旧任务；
- 计划、单元和事件约束；
- 事件只追加且按任务排序；
- 幂等键防重复累计；
- `has_more=null` 原样保存；
- 应急记录脱敏、追加和幂等导入；
- 主存储写失败产生不完整标记；
- 两个落点均失败时显式报错。

### 3. 主抓取证据

```powershell
uv run python -m unittest tests.test_whitebox_integration
uv run python -m unittest tests.webui_app.test_webui_app_runtime
uv run python -m unittest tests.webui_app.test_webui_app_taskrun
uv run python -m unittest tests.webui_store.test_store_domains
uv run python -m unittest tests.source.test_source_boss tests.source.test_source_zhilian
```

#### 场景 A：0 条但无完成证据

- 计划：1 个组合，10 页。
- 事实：返回岗位为 0，但没有组合结束或明确空结果事实。
- 预期：单组与整体均为 `unverifiable`。

#### 场景 B：明确完成 10 页且确实为空

- 计划：1 个组合，10 页。
- 事实：10 个合法页面完成事实、范围完成、明确空结果。
- 预期：单组与整体均为 `empty`。

#### 场景 C：20 组中 1 组失败

- 计划：20 个组合。
- 事实：19 组完整成功，1 组有明确错误码。
- 预期：整体 `partial`，失败组合可查询；不能出现完整成功。

#### 场景 D：翻页中途停止

- 计划：10 页。
- 事实：只完成 6 页，并有浏览器丢失或明确停止原因。
- 预期：有可用结果则 `partial`；无可用结果且原因明确则 `failed`。

#### 场景 E：页面证据丢失

- 计划：10 页。
- 事实：岗位表有数据，但页面或组合结束证据缺失。
- 预期：`unverifiable`；岗位数不能升级状态。

#### 场景 F：数量解释

- 构造页面实际返回数大于组合内唯一数，多个组合间还有重复。
- 预期：报告分别显示 `returned_total_count`、`unit_unique_count`、`unit_output_sum`、`run_unique_count`，四者不互相覆盖。

#### 场景 G：字段质量

- 构造薪资来源 `api_empty`。
- 预期：单组和整体质量计数均出现对应数量，但不单独改变完成结论。

### 4. 恢复、AI 和运行异常

```powershell
uv run python -m unittest tests.test_screen_flow
uv run python -m unittest tests.test_pipeline_guard
uv run python -m unittest tests.test_pipeline_exec_accounts
```

#### 场景 H：恢复失败任务

- 原尝试为失败或部分完成，并已有一些岗位。
- 恢复只开始但未补齐缺口。
- 预期：不得升级成功；报告能追到原尝试。

#### 场景 I：AI 全部保留

- 粗筛请求失败并走全部保留。
- 预期：存在 `ai_request_failed` 和 `ai_keep_all_fallback`；明确正常筛选未完成；整体不是完整成功。

#### 场景 J：浏览器与账号恢复

- 记录浏览器重启或账号切换并成功补齐工作。
- 预期：事件绑定任务、阶段、单元和尝试；最终可完成但 `degraded=true`。
- 若未补齐，预期为 `partial` 或 `unverifiable`。

#### 场景 K：后台提交失败

- 让执行器拒绝抓取、AI、继续、复抓、调参和旧任务提交。
- 预期：接口返回错误；对应任务立即可查询为失败，不能留在 queued/running。

#### 场景 L：白箱写入失败

- 主存储失败、应急文件可写。
- 预期：写应急记录，任务不能成功。
- 主存储与应急文件同时失败。
- 预期：调用显式失败并留主日志/标准错误，不能成功。

### 5. 旧流程与调参

```powershell
uv run python -m unittest tests.test_workbench tests.test_workbench_api
uv run python -m unittest tests.tuning.test_tuning_manifest
uv run python -m unittest tests.webui_app.test_webui_app_tuning
```

必须验证：

- 旧工作台部分子查询失败 → `partial`
- 旧任务提交失败 → 明确终态
- 任务化职位复抓部分失败 → 单元可查、整体 `partial`
- 调参缺少测量但有结果对象 → 不补推成功

### 6. 查询与前端一致性

后端聚焦测试应对同一任务同时请求任务状态、结果历史和开发者报告，断言：

- `conclusion` 一致；
- `primary_code` 一致；
- `revision` 一致；
- 旧历史任务返回 `legacy_evidence_missing`，数据库未被写入。

前端：

```powershell
Set-Location webui
npm test
npm run build
```

必须覆盖：

- `partial` 和 `unverifiable` 不使用完整成功样式；
- “无法确认”显示重新执行建议；
- 普通界面不加载完整事件；
- 动态岛、任务进度和结果页使用同一结论。

## 全量门禁

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

报告必须逐项写明：

- 测试等级；
- 实际命令；
- 通过/失败数量；
- 使用临时还是真实数据；
- 未测试范围；
- 是否执行了真实端到端。

## 真实端到端

只有用户另行授权后才执行：

1. 再次运行 `scripts/db_info.py` 确认正式库和活动任务；
2. 选择全新的最小任务，不复用任何历史 run；
3. 经项目公开界面或接口启动；
4. 验证普通提示和开发者报告；
5. 不直接修改数据库、事件表或外部平台状态。

无法满足这些条件时，明确记录“真实端到端未执行”。

## 本轮审查返修实施报告（2026-09-05）

### 范围与边界

本轮只处理 033 白箱 V2 的审查返修：统一结论、生命周期优先级、分页唯一岗位数、白箱写入失败、账号轮询可观测性、AI 失败归属、恢复尝试号和读取端一致性。正式任务和正式数据库保持不动；未实施 038 V4；未提交、推送、发布或删除文件。

真实端到端测试没有执行。用户明确限定真实 E2E 需另行授权，且本轮不具备新的真实输入与写入边界；`tests/test_e2e_smoke.py` 仍只是使用临时数据库、测试客户端和边界桩的跨层自动化冒烟，不能作为真实浏览器、真实账号或真实数据验收。

### 主要返修

- 最终白箱结论现在会把未修复的 `whitebox_incomplete` 作为阻断事实；主库写入、备用落点写入以及两者同时失败都有测试覆盖。
- 明确的 `failed`、`cancelled`、`interrupted` 生命周期优先于证据完整性降级，不再被 `legacy_evidence_missing` 改写成部分完成。
- 分页 `new_unique_count` 按页累加，并保留尚未产生 `scope_completed` 就中断时的准确数量。
- 账号轮询只把字符串、字节或路径对象作为 `db_path`，Mock/代理 Store 使用安全默认落点。
- AI 失败、检查点失败和后台提交失败绑定实际计划单元，保持失败终态；恢复与复抓使用连续的白箱尝试号。
- 任务状态、结果、历史和开发者报告复用同一完整性结论；同步了用户文档、变更说明、模块地图和异常留痕门禁。

### 测试证据

| 等级 | 命令/范围 | 结果 | 数据与边界 |
| --- | --- | --- | --- |
| 聚焦回归 | `StatusMappingTests`、`ConvergencePendingPersistenceTests`、`tests.test_account_round_robin` | 87 项通过 | 临时数据库、测试客户端、Mock/代理 Store |
| 白箱聚焦 | `tests.test_whitebox_rules`、`tests.webui_store.test_store_whitebox`、`tests.test_whitebox_integration` | 32 项通过 | 临时 SQLite、模拟主库/备用落点失败 |
| 直接受影响回归 | 白箱集成、位置范围、WebUI runner、跨层冒烟、登录重试、复抓暂停恢复 | 52 项通过 | 模拟源、执行器、CDP 边界，不是真实外部平台 |
| 相邻后端回归 | WebUI platform | 61 项通过 | 测试客户端和临时数据库 |
| 相邻后端回归 | WebUI taskrun | 65 项通过 | 测试客户端和临时数据库 |
| 相邻后端回归 | healthy pipeline pause/resume | 45 项通过 | 模拟暂停、恢复和检查点 |
| 后端全量 | `uv run python -m unittest discover -s tests` | 2926 项通过，0 失败，4 跳过 | 项目测试夹具、临时库和边界桩 |
| 前端测试 | `npm test -- --run` | 50 个测试文件、777 项通过 | Vitest/JSDOM 测试环境 |
| 前端构建 | `npm run build` | 通过 | 本地源码构建；仅有 chunk 体积提示 |
| 卫生测试 | `uv run python -m unittest tests.test_repo_hygiene` | 14 项通过 | 仓库文件、引用方向、行数和异常留痕 |

### 失败与返修收敛

第一次后端全量为 2926 项、4 个失败、4 个跳过，失败集中在暂停/重启白箱尝试号、卫生测试发现意图加入索引的新文件和异常空 `pass`。修复这些问题后第二次全量为 2926 项、1 个失败、4 个跳过，剩余问题是公开 Spec 含本机绝对路径。替换为用户数据目录的抽象表述后，最终后端全量为 2926 项通过、0 失败、4 跳过。

本轮没有为消除失败而机械改写真实失败、取消或中断场景的测试期待；当前仍未勾选的是没有新增测试证据的 T053，以及没有形成独立逐项核对记录的 T080。T068 的前端类型/发现页测试已在本轮补齐并通过。真实 E2E 也按边界明确记为未执行。

### 最终核对

文档更新后的最终门禁已执行：`uv run python -m unittest tests.test_repo_hygiene` 为 14 项通过，`git diff --check` 通过，`git status --short` 仅显示现有改动、并行工作区文件及本轮新增文件的意图加入索引状态；未发现临时产物或凭据。所有内容均未提交。

## 本轮审查阻断返修增量报告（2026-09-05）

### 返修范围

本轮只针对审查指出的五个阻断项返修：应急白箱事实在收口前导入、浏览器恢复不得冒充白箱修复、汇总与最终事件原子写入、中断任务不得被直接升级成功，以及手动保存部分结果必须形成白箱终态。同时把历史证据降级接口改为公开接口，并将新增逻辑从预警文件薄提取到白箱辅助模块；没有扩大用户功能范围，没有修改 038 V4、正式数据库或受保护任务。

### 增量测试证据

| 等级 | 命令/范围 | 结果 | 数据与边界 |
| --- | --- | --- | --- |
| 白箱与任务结束回归 | `tests.test_whitebox_rules tests.webui_store.test_store_whitebox tests.test_whitebox_integration tests.webui_app.test_webui_app_taskrun` | 101 项通过；首次过滤回归 1 项失败，修复后重跑通过 | 临时 SQLite、应急 JSONL、事务触发器、测试客户端 |
| 五个阻断复现 | 应急导入、浏览器恢复、原子收口、中断升级、应急导入兼容性 | 5 项通过 | 模拟主库/应急落点/浏览器恢复，不触碰正式数据 |
| 生命周期/账号/恢复回归 | `StatusMappingTests`、`ConvergencePendingPersistenceTests`、`tests.test_account_round_robin`、`tests.healthy_pipeline.test_pipeline_pause_resume` | 132 项通过 | 临时数据库、Mock/代理 Store、模拟暂停恢复 |
| 旧工作台回归 | `tests.test_workbench tests.test_workbench_api` | 73 项通过 | 测试客户端和模拟执行器 |
| 前端 T068 聚焦 | `npm test -- --run src/__tests__/types.spec.ts src/__tests__/discovery.spec.ts` | 2 个文件、51 项通过 | Vitest/JSDOM；覆盖六类完整性结论 |
| 卫生与差异 | `uv run python -m unittest tests.test_repo_hygiene`、`git diff --check` | 14 项通过；差异检查通过 | 工作区文件、行数、引用方向和临时产物 |

白箱回归过程中仍观察到非致命 `ResourceWarning`：个别测试结束时子进程或 `localhost:9222` socket 尚未及时关闭。该告警没有导致本轮测试失败，但说明这些测试不应与正式抓取并行运行；本轮未启动正式抓取。

### 文件门禁

`webui/whitebox.py` 为 451 行，`webui/store_whitebox.py` 为 477 行，`webui/whitebox_evidence.py` 为 317 行，`webui/task_finish_whitebox.py` 为 60 行，`webui/exec_search_whitebox.py` 为 43 行，`webui/exec_search_api.py` 为 630 行，`webui/workbench_runner.py` 为 400 行。预警文件本轮没有净增长；辅助模块只承载既有逻辑的薄提取。

### 当前未执行范围

- 没有重新运行后端全量 `uv run python -m unittest discover -s tests`；上方旧报告中的全量结果仅作为历史基线，不作为本轮返修后的全量证明。
- 没有重新运行前端全量 `npm test` 或 `npm run build`。
- 没有执行真实浏览器、真实账号、真实数据 E2E；用户没有提供单独授权。
- T053 的新增旧工作台测试证据和 T080 的独立合同逐项核对记录仍未完成，因此保持未勾选。
