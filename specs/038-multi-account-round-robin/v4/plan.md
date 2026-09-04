# Implementation Plan: 多账号轮询分摊抓取可靠性修复（B091 V4）

**Spec**: [spec.md](./spec.md)

**Research**: [research.md](./research.md)

**Data Model**: [data-model.md](./data-model.md)

**Runtime Contract**: [contracts/r2-runtime-events.md](./contracts/r2-runtime-events.md)

**Status**: 已冻结，待实施

## Technical Context

**Language/Runtime**: Python 3.10+；现有 Vue 前端不在本轮实现范围

**Primary Components**: WebUI 后台任务编排、CDP source 适配器、账号轮询域、任务事件与既有断点/待处理存储

**Storage**: 复用既有任务日志、JD 断点、`pipeline_checkpoints`/任务产物和待处理记录；不新增业务表，不执行正式数据库迁移

**Testing**: Python `unittest` 聚焦组合测试、后端全量；前端回归与构建作为项目交付门禁；真实账号端到端另行授权

**Execution Model**: 多账号串行；一次只有一个账号发起详情请求

**Scale Target**: 至少覆盖六账号、每账号配额 200、1048 个详情岗位，多轮总量不限于单轮账号池容量

**Constraints**:

- 不修改平台抓取节奏、页面解析和外部风控策略。
- 不改账号弹窗、账号池 schema、数据库迁移或正式运行数据。
- 不能通过把全部详情一次性塞入一个调用来绕过现有暂停、进度和卡死守护。
- `webui/account_round_robin.py` 当前 798 行，禁止继续堆入新领域逻辑；新增 R2 会话状态必须落新模块或通过净减行拆出。
- `webui/pipeline_exec_details.py` 当前 600 行，只允许接线和移除重复逻辑；尝试身份与事件组装落独立模块。

## Constitution Check

- **职责分层**: 通过。轮询会话、尝试事实和恢复状态分别形成明确接口，runner 与详情执行域只负责接线。
- **单文件尺寸**: 通过。两个临界文件不承载新增领域逻辑；新模块各自保持单一职责并控制在 250 行内。
- **依赖方向**: 通过。runner 创建任务级会话并传给详情执行域；详情执行域调用轮询会话和尝试记录，不反向依赖 runner。
- **错误与可观测性**: 通过。预留、实际开始、终态、接力和汇总分层记录，白箱失败不得伪造事实。
- **测试契约**: 通过。先建立能稳定复现正式事故组合的失败测试，再修复。
- **数据安全**: 通过。事件与断点只记录安全账号标识、计数和失败分类，不记录凭据或正文。
- **真实 E2E**: 受控。自动化完成后仍需用户明确授权正式账号场景，未执行不得称完整验收。

## Implementation Strategy

### 1. 建立任务级 R2 轮询会话

新增 `webui/r2_rotation_session.py`，包装现有轮询队列并提供：

- 在整个 R2 阶段连续领取配额，而不是每个外层分块重建。
- 导出与恢复版本化快照。
- 校验任务、平台、冻结账号顺序和配额。
- 记录当前轮次、当前账号、剩余配额和已阻断账号。
- 接受既有继续流程的显式账号覆盖，但不清空已完成事实。

`webui/runners/ai_screen_jd.py` 在进入外层详情循环前创建或恢复一次会话，并把同一实例传给每次 `fetch_job_details`。每个成功分块和暂停出口都同步保存 JD 与轮询快照。

`webui/pipeline_exec_details.py` 接受可选的任务级会话；正式 runner 必须传入，兼容旧直接调用时可在函数内部创建一次临时会话，但该兼容入口不得被正式多分块任务使用。

### 2. 隔离账号级可变状态

收紧 `clone_source`：

- 不复用原账号的熔断器实例。
- 不复用包含可变 spawn/output 探针的执行器实例。
- 继续共享任务级取消信号和安全的 runner/配置注入。
- 保持 BOSS、智联及测试替身构造兼容。

不改变熔断器阈值、冷却、信号集合或平台失败分类。限流标记只由该账号自身真实请求产生的硬阻断推进；绑定失败和未启动请求的本地短路走既有环境/跳号分类。

### 3. 建立真实尝试事实与唯一产物

新增 `webui/detail_attempts.py`，负责：

- 生成任务内唯一的分配段、尝试和产物身份。
- 生成不含敏感正文的请求开始与请求终态摘要。
- 校验输入数与成功、失败、短路、未完成数量恒等式。
- 为浏览器恢复重试和跨账号接力生成新产物路径。
- 按账号累计唯一成功并生成任务汇总。

扩展 `webui/account_round_robin_observability.py`，保留现有 `account_allocation` 兼容事件，但增加 `fact_kind=reservation`；新增请求开始、终态和账号汇总事件。`pipeline_exec_details.py` 只在真正调用 source 前记录开始，在返回后记录终态；本地熔断短路记录为未开始/短路，不伪造成平台请求。

### 4. 精确恢复与待处理清理

`webui/runners/ai_screen_jd.py` 在岗位取得有效 JD 后，除清理内存失败映射外，还清理该 JD 失败链路写入的待处理记录。清理范围由任务和岗位稳定标识限定，不删除仍未解决的 AI 精筛待处理。

R2 快照与 JD 断点必须按同一任务边界保存并可核对。若快照损坏或账号池身份不匹配，走明确可恢复暂停；不得静默新建会话并从第一个账号重抓。

### 5. 回归保护

新增独立 V4 测试文件，避免继续扩大已超过 900 行的 `tests/test_account_round_robin.py`。现有错误断言必须改为“克隆账号的熔断器与可变执行探针相互独立”。

组合测试必须穿过 `run_jd_stage → fetch_job_details → R2 会话 → source` 的最小真实协作边界，而不只测试纯队列。BOSS 与智联都要验证隔离；R1 至少运行既有回归，确认 clone 行为调整未破坏列表轮询。

## File Boundaries

### New files

- `webui/r2_rotation_session.py`：任务级 R2 会话、快照、恢复校验，预计不超过 250 行。
- `webui/detail_attempts.py`：尝试身份、唯一产物路径、终态核对和账号汇总，预计不超过 250 行。
- `tests/test_r2_rotation_v4.py`：跨分块配额、六账号 1048 条、暂停恢复组合测试。
- `tests/test_detail_attempts_v4.py`：真实请求事件、产物唯一和汇总核对测试。
- `tests/source/test_source_account_isolation_v4.py`：BOSS/智联账号克隆熔断及可变执行状态隔离测试。

### Modified files

- `webui/runners/ai_screen_jd.py`：在阶段级创建/恢复/保存一个 R2 会话，成功时精确清理旧 JD 待处理。
- `webui/pipeline_exec_details.py`：接收任务级会话，按实际调用记录尝试，重试改用唯一产物；只做接线，新增领域逻辑放新模块。
- `webui/account_round_robin.py`：停止在账号克隆间共享账号级可变状态；保持现有公开导入兼容，文件不得超过 800 行。
- `webui/account_round_robin_observability.py`：区分预留与实际请求，增加终态与汇总事件。
- `tests/test_account_round_robin.py`：只修正共享 breaker 的错误契约；不继续追加大段 V4 组合测试。
- `.specify/memory/constitution.md`：仅登记新增模块职责与引用方向。
- `CHANGELOG.md`：实施完成后按用户可感知口径记录“多账号详情抓取真实分摊与接力修复”。
- `specs/038-multi-account-round-robin/v4/*`：实施和验证后只更新任务勾选、验证证据与状态，不改变冻结需求。

### Forbidden files and state

- `webui/src/**`：本轮不改前端。
- `webui/pipeline_exec_accounts.py`、账号簿 schema 和账号配置文件：本轮不改账号池配置。
- `webui/store_migrations*.py` 和正式数据库：不新增迁移、不直接改正式数据。
- `scripts/boss/**`、`scripts/zhilian/**`：不修改平台请求、解析、节奏或风控逻辑。
- `webui/resume_identity.py`：优先复用其显式账号选择结果，不在本轮重写继续接口；只有实现证明现有返回值无法接线且不改变既有语义时，才允许在 Plan 修订后纳入。
- `specs/033-log-whitebox/**`：用户现有未跟踪内容，严禁修改、移动或删除。
- 提交、推送、发布、打包、重启服务和运行正式账号任务均不在本轮文档授权内。

### Reference direction

`ai_screen_jd.py → r2_rotation_session.py → account_round_robin.py`

`ai_screen_jd.py → pipeline_exec_details.py → detail_attempts.py`

`pipeline_exec_details.py → account_round_robin_observability.py → TaskStore task events`

`ai_screen_jd.py → TaskStore pending/checkpoint methods`

source 适配器不反向依赖 runner、详情执行域或任务存储。

## Verification Plan

1. 先新增并运行 V4 聚焦测试，保留修复前失败证据。
2. 实施任务级轮询会话，验证 11×20 与 6×200/1048 两个确定性分布。
3. 实施账号状态隔离，验证第一个账号打开熔断后第二个账号真实调用，BOSS/智联均覆盖。
4. 实施真实尝试、唯一产物和汇总核对，验证 18 成功 + 2 接力与浏览器恢复重试。
5. 实施恢复断点与待处理清理，验证成功项不重复、旧 JD 失败归零、AI 未解决项保留。
6. 运行后端聚焦和全量、前端测试与构建、仓库卫生、`git diff --check` 和 `git status`。
7. 用户另行授权后，通过正式入口执行最小真实账号端到端；未执行时明确保留验收边界。

## Risks and Controls

- **兼容调用风险**：`fetch_job_details` 可能被重抓任务等其他入口调用。保留可选会话兼容入口，并为每个调用方增加回归。
- **恢复状态漂移**：账号池或用户显式账号选择可能与快照不同。严格校验冻结身份；只允许既有继续流程的显式选择覆盖。
- **事件重复**：重试会增加尝试数。最终汇总按唯一成功岗位归属核对，预留和失败不进入成功数。
- **产物增长**：每次尝试独立保存会增加文件数量。沿用既有任务产物清理生命周期，不引入无界常驻缓存。
- **R1 回归**：clone 隔离同时影响 R1。保留 R1 既有测试并增加账号切换后真实 source 可用验证。
- **正式账号风险**：自动化不触发平台；真实 E2E 仅在用户授权后小规模执行，不主动制造风控。

## Post-Design Constitution Check

- 新增模块承担明确深层职责，临界文件仅接线，符合文件规模和引用方向约束。
- 不新增数据库迁移、前端范围或平台脚本修改，修复范围与已核实根因一致。
- Spec、Plan、Tasks 均以 V4 为唯一冻结版本，旧版本保持只读。
- 没有未解决的澄清项，可以进入实现。
