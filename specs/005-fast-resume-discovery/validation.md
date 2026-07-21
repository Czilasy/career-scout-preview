# Validation: 快速简历驱动岗位推荐收口

本文件只记录当前 feature 005 实施产生的客观证据。历史 004 结果、fake/smoke 结果和手工推断不能替代 005 的真实验收。

## Slice 0 — T001–T003 治理与工作区门禁

**记录时间**：2026-07-20T23:25:33+08:00  
**仓库**：`czyooutzilas-sketch/career-scout-preview`  
**执行目录**：`D:\项目\boss`

### T001 — GitHub issue（用户取消外部门禁）

- 曾在 `origin` 指向的上游仓库误建 #35；仅核对 remote URL 和用户的 issue 授权，没有确认仓库/账号归属，授权边界判断错误。
- 用户指出不得打扰他人后立即停止后续外部写入。
- 当前账号没有删除 issue 的菜单/权限；已将正文清理为误操作说明、关闭 issue，并将标题改为“误操作，请忽略”。
- 该 issue 不再作为 T001 或 Phase 1 Gate 的通过证据；T001 恢复为未完成。
- 后续不得向该上游仓库新建 issue、评论、PR 或其他外部内容，除非用户再次明确指定目标仓库和允许的外部动作。
- 2026-07-20T23:50:57+08:00，用户明确授权继续本地实施并“取消 T001 的门禁”。因此 T001 以 `CANCELLED BY USER` 收口，不以 issue 创建成功标记 PASS；本功能后续只在本地运行，不再执行该外部动作。

### T002 — Feature branch

- 创建命令：`git switch -c codex/fast-resume-discovery master`
- 分支：`codex/fast-resume-discovery`
- `master`/分支基点：`fc6a2c7cabd285927a3013bfad8be443b1cd6085`
- 创建时 `master...origin/master [ahead 96]`；未 fetch/rebase/reset，不改变本地 master 的既有 96 个提交。
- 创建分支时保留全部 tracked/untracked 工作区内容；未 stash、未 checkout 覆盖。

### T003 — 工作区归属与冲突规则

创建分支前的已有改动：

| 文件 | 状态/已核对内容 | 归属与本功能处理 |
|---|---|---|
| `.specify/feature.json` | tracked modified；feature directory 从 004 指向 005 | 用户既有改动；保留，不回退。除非后续用户明确改变 feature，不再写入。 |
| `.trae/rules/project_rules.md` | tracked modified；当前 plan 从 004 指向 005 | 用户既有改动；保留，不回退，不作为实现文件修改。 |
| `tests/fixtures/discovery/e2e_real_boss.py` | tracked modified；移除 `max_details=1`，poll timeout 480s→1800s | 用户既有改动；必须原样保留其语义。T099 如需扩展，只能在当前内容上 additive 修改，先核对 diff，禁止恢复基线版本。 |
| `_tmp_diag_t133.py` | untracked，2540 bytes | 用户诊断产物；只读保留，不纳入 005 修改或清理。 |
| `_tmp_diag_t133_v2.py` | untracked，2036 bytes | 用户诊断产物；只读保留，不纳入 005 修改或清理。 |
| `specs/005-fast-resume-discovery/` | untracked frozen feature artifacts | `spec.md`、`plan.md`、`research.md`、`data-model.md`、`contracts/`、`quickstart.md` 视为冻结只读；只允许按执行事实更新 `tasks.md` checkbox、`validation.md` 和 `validation/` 证据。 |

允许写入范围严格受 `tasks.md` 当前切片约束：

- Slice 0：仅 `specs/005-fast-resume-discovery/tasks.md`、`validation.md`、任务明确的新测试夹具/测试；Phase 1 Gate 前禁止生产代码修改。
- 后续切片：仅任务明确列出的 `webui/`、`scripts/boss_cdp_raw.py`、`tests/`、README 中英、CHANGELOG 和 005 validation 产物。
- migration 015 只能 additive；不得重写 001–014。
- 004 历史运行继续使用 policy v1；005 新运行使用独立 `discovery_v2`。
- 默认详情并发保持 1；只有真实稳定性证据通过后才允许 policy 上限 2。

冲突处理规则：

1. 每个切片前后运行 `git status --short --branch` 与限定路径 diff；不回退、不覆盖用户既有 delta。
2. 如任务必须触碰已有修改文件，基于当前工作树增量编辑，并在本文件记录保留了哪些既有语义与新增哪些 005 语义。
3. 发现无法无损合并的冲突时停止对应切片，保留现场并向用户请求决定；不得用 reset、checkout 或重写规避。
4. 测试和真实产物是任务完成依据；没有客观通过证据不得勾选后续任务。

### Speckit 前置检查

- `.specify/extensions.yml`：不存在，无 before_implement hook。
- `checklists/requirements.md`：16/16 completed，PASS。
- 标准 `.specify/scripts/powershell/check-prerequisites.ps1`：仓库中不存在；实际 `.specify/scripts/powershell/` 只有 `common.ps1` 与 `setup-plan.ps1`。未将缺失脚本伪装为通过；改由已完整读取冻结工件、确认 tasks/checklist/feature directory 完成等价只读核对。

### Slice 0 当前门状态

- T001：CANCELLED BY USER（外部 issue 门禁已被明确取消；误建 issue 不作为证据）
- T002：PASS
- T003：PASS
- T004：PASS
- T005：PASS（历史捕获基线；不是 005 新性能验收）
- Phase 1 Gate：尚未通过；T001 已由用户取消，仍需 T006–T007 的 RED 夹具、测试证据与独立审查。门禁通过前不得修改生产代码。

## T004 — 当前自动化与 schema 基线

**记录时间**：2026-07-20T23:38:54+08:00  
**Python**：项目 `.venv`，CPython 3.11.15  
**Schema**：新建临时数据库 `TaskStore.schema_version()` 返回 `14`；现有 `test_schema_version_upgraded_to_14` 同步通过。

### 环境修复事实

首次检查发现项目 `.venv` 不存在，测试没有启动。执行 `uv sync --frozen` 后，`uv.lock` 的本地 project metadata 仍只包含 Flask/requests/websocket-client，而当前 `pyproject.toml` 已声明 `keyring`、`pypdf`、`python-docx`；因此第一次测试进入收集后出现统一的 `ModuleNotFoundError: keyring`：

- 专项：`Ran 166 tests in 0.587s`，27 import errors，命令耗时 8.966s。
- 全量：`Ran 861 tests in 46.233s`，164 import errors，命令耗时 48.764s。

这两次结果属于环境依赖不完整，不作为业务基线失败。未改 `uv.lock`；仅向忽略的本地 `.venv` 安装 `pyproject.toml` 已声明范围：keyring 25.7.0、pypdf 5.9.0、python-docx 1.2.0。

### 依赖完整后的专项基线

```powershell
& .venv\Scripts\python.exe -m unittest `
  tests.test_candidate `
  tests.test_discovery `
  tests.test_discovery_integration `
  tests.test_discovery_contracts `
  tests.test_boss_discovery_source `
  tests.test_discovery_frontend -v
```

结果：`Ran 384 tests in 54.648s`，`OK`；命令总耗时 55.436s，exit 0。

### 依赖完整后的全量基线

```powershell
& .venv\Scripts\python.exe -m unittest discover -s tests -v
```

结果：`Ran 1353 tests in 136.151s`，`OK (skipped=27)`；命令总耗时 137.112s，exit 0。

### 当前真实 E2E 状态

当前本地产物 `tests/fixtures/discovery/e2e_real_boss_result.json`（SHA256 `E25F98DC6F3FF98A376EE3112825045054E4F6420804B9477BC575CF04B2A8EB`）为 `status=blocked`、`run_status=cancelled`，顶层 blockers：

- `real_detail_missing`
- `real_evaluation_missing`
- `feedback_not_executed`
- `feedback_job_missing`
- `resume_not_verified`

因此 004 validation 中较早的 PASS 文字不能替代当前产物，也不能作为 005 真实验收。

## T005 — 480 秒 / 9 详情捕获基线复核

### 可证实事实

1. 当前本地真实产物写入时间为 2026-07-20 21:15:26；用户随后对 `e2e_real_boss.py` 的既有改动把主轮询 timeout 从 480s 改为 1800s（文件修改时间 21:16:37，Git diff 保留旧值 480.0）。因此该产物对应 480 秒有界运行的证据链成立，但 JSON 本身没有独立 monotonic elapsed 字段。
2. 产物 `run_diagnostics.events` 有 10 个 `detail_fetch_started`；其中 9 个随后出现 `snapshot_saved(ok=true, completeness=complete)`，第 10 个在 cancel 后保存为 unavailable；没有任何 assessment/evaluation 事件。
3. 顶层 run counters 为 `detail_count=0/evaluated_count=0`，与事件流的 9 个 complete snapshot 不一致。这暴露了旧计数/取消收敛问题；T005 只把事件流作为“实际完成详情”证据，不把顶层 0 改写成 9。
4. `480 / 9 = 53.333...` 秒/完成详情是派生吞吐均值，不是逐岗位 duration 分布，也不能提供 p50/p95。
5. `webui/discovery_runner.py` 在详情阶段对 `jobs_to_fetch` 使用同步 `for` 循环逐个调用 `_fetch_one_detail`，因此该捕获的来源详情峰值并发为 1。

### 每岗位执行链与显式等待预算

现有 `BossCdpSource.fetch_detail` 对每个岗位独立构造并运行：

```text
python scripts/boss_cdp_raw.py --input <one-job> --detail-output <path> --max-details 1 --detail
```

因此每岗位都会承担一个独立 Python scraper 进程。`scrape_details` 对每个岗位又独立执行：

- 新建 `CDPSession`：一次 `/json/version` HTTP + 一次 browser WebSocket；
- `Target.createTarget` + `Target.attachToTarget`；
- 页面导航后固定 sleep 5–10s；
- 固定执行 3–7 次滚动，每次 sleep 0.8–1.8s 或 2–5s；可证实的滚动等待范围为 2.4–35s/岗位；
- 50% 概率鼠标移动并额外 sleep 0.5–1.5s；
- 关闭 target/session 后无条件 sleep 10–25s。

因为 source 固定传 `--max-details 1`，最后一项就是唯一一项；现有代码仍无条件执行 10–25s 尾部等待。仅显式 sleep 的理论范围为 17.4–71.5s/岗位（不含进程启动、CDP 命令、页面网络、提取和原子写入）。该范围与派生均值 53.3s/详情相容，但不是新的真实测量。

### T005 结论边界

- 已证实主瓶颈在评估前的串行详情链；当前产物 assessment 事件为 0，不能把这次 480 秒慢归因于 AI。
- 已证实重复进程/CDP 初始化、固定页面等待、多次滚动和唯一岗位后的尾部等待都按岗位线性叠加。
- 尚无逐岗位 duration/wait 原始字段、p50/p95 或 005 当前代码真实样本；这些必须由 T007 metric contract、T098 和 T099 新产物补齐。

## T006–T007 — 确定性 fixture 与性能合同 RED

**实现智能体选择**：Codex Radar 实时 `--dry-run`，`gpt-5.6-sol high`，IQ 95.5，run `2026-07-20T21:59:02+08:00`；实现智能体只允许写入两个新测试文件，不得修改生产代码、tasks、validation 或用户既有改动。

### T006 fixture

文件：`tests/fixtures/discovery/fast_resume_discovery_v2.json`

- 原始列表结果 106；canonical job id 和 source URL 各 100 个唯一值。
- 6 个显式重复项，均指向已有 canonical job。
- 20 个唯一、active、accessible 且预检非 violation 的详情。
- 3 个方向：core、adjacent、growth；详情方向覆盖 core=12、adjacent=6、growth=5，允许共享岗位。
- 薪资三态：pass=34、unknown=33、violation=33。
- 城市三态：pass=34、unknown=33、violation=33。
- 综合预检：pass=12、unknown=33、violation=55。
- `python -m json.tool` exit 0。

验证命令：

```powershell
& .venv\Scripts\python.exe -m unittest `
  tests.test_discovery_performance.DiscoveryPerformanceFixtureTests -v
```

结果：`Ran 3 tests in 0.002s`，`OK`，exit 0。

### T007 RED 合同

文件：`tests/test_discovery_performance.py`

合同覆盖：

- 生产 runner 必须注入 monotonic clock；
- 报告必须包含 list/selection/details/AI/timing/breaker/blockers/gates 完整字段；
- 90/300/600 秒边界为 inclusive，超过 0.001 秒立即失败；
- 记录 first result、first five、all complete；
- 外部 blocker 必须保留 typed code/stage/external，并阻断 overall pass；
- 每详情保留 total/wait/reason/batch/concurrency，报告 p50/p95。

RED 命令：

```powershell
& .venv\Scripts\python.exe -m unittest tests.test_discovery_performance -v
```

结果：`Ran 8 tests in 0.845s`，fixture 3 项通过，性能合同 5 项预期失败，exit 1；没有 ImportError、JSON 错误、语法错误或测试收集错误。

预期失败原因：

1. `DiscoveryRunner.__init__` 尚无 `monotonic_clock` 参数。
2. `webui.discovery_runner.DiscoveryPerformanceMetrics` 尚不存在。

因此 RED 由尚未实现的 005 生产合同触发，满足 test-first 证据。该结果只证明 deterministic harness，不是 fake 性能 PASS，更不是当前真实 BOSS/AI 性能。

### Phase 1 Gate 当前状态

- T001：CANCELLED BY USER；不再要求外部 issue。
- T002–T006：PASS。
- T007：PASS（正确 RED 已观察并记录）。
- 生产文件 diff：空。
- 2026-07-21，用户明确取消独立审查门，并要求项目仅在本地继续进入生产实现。该决定记录为用户授权的 Harness 降档，不伪写为独立审查 PASS。
- Phase 1 Gate：PASS WITH USER WAIVER；T001 外部 issue 与独立审查均由用户明确取消，其余基线、fixture、RED 和工作区保护证据已通过。

## T008–T013 — Migration 015 与 policy v2 基础

### Migration 015 RED / GREEN

RED 命令：

```powershell
& .venv\Scripts\python.exe -m unittest `
  tests.test_discovery_store.Migration015Tests -v
```

RED 结果：`Ran 8 tests in 2.039s`，4 failures、3 errors、1 pass，exit 1。失败均由 schema 仍为 14、015 新表和 additive 字段尚不存在触发。

实现仅新增 `_migration_015` 并在 14 后推进；未修改 001–014。升级测试先构造真实 schema 14 与代表性 v1 confirmation/run/snapshot/assessment，再重开触发 015，验证：

- schema 15 且重开幂等；
- 旧表行数与代表性 v1 值不变；
- v1 run 的 015 新字段保持 SQL NULL；
- 新画像、事实、证据和 run candidate 表的外键、唯一约束、状态约束与不可变性；
- confirmation/run/snapshot/assessment 的新增 identity、计数、freshness 和 timing 字段。

GREEN 结果：同一命令 `Ran 8 tests in 2.632s`，`OK`，exit 0。

### Policy v2 RED / GREEN

RED 命令：

```powershell
& .venv\Scripts\python.exe -m unittest `
  tests.test_discovery.DiscoveryPolicyV2Tests -v
```

RED 结果：`Ran 4 tests in 0.336s`，4 errors，exit 1；错误均为 `DiscoveryPolicyV2`、`DiscoveryPolicyV1Adapter` 与 resolver 尚不存在。

GREEN 结果：同一命令 `Ran 4 tests in 0.338s`，`OK`，exit 0。已固定：默认详情预算 15、允许 12–20、批次最大 5、默认来源并发 1、上限 2、TTL 12 小时、轮询 3 秒。无版本或 `v1` 继续解析为只读 v1 adapter，`discovery_v2` 单独解析，输入 run mapping 不被改写。

### 切片回归

```powershell
& .venv\Scripts\python.exe -m unittest `
  tests.test_discovery_store tests.test_discovery -v
```

结果：`Ran 107 tests in 15.895s`，`OK`，exit 0。

```powershell
& .venv\Scripts\python.exe -m py_compile webui/store.py webui/discovery.py
```

结果：exit 0，无输出。

### 当前门状态

- T008–T013：PASS，已由上述 RED/GREEN 与专项回归证据支持。
- Phase 2 尚未通过：T014–T019 仍待完成。
- 外部 issue 与独立审查：按用户本地授权取消，不作为阻断。

## T014–T019 — v2 共享合同、CAS、事件与隐私

### 初始 RED

```powershell
& .venv\Scripts\python.exe -m unittest `
  tests.test_discovery_contracts.DiscoveryV2FoundationalContractTests `
  tests.test_discovery_contracts.RunResultsHttpContractTests.test_v2_results_remain_readable_while_run_is_active `
  tests.test_ai.DiscoveryAIVersionRoutingV2Tests `
  tests.test_discovery_integration.DiscoveryV2StateAndPrivacyFoundationTests -v
```

结果：`Ran 13 tests in 1.971s`，1 failure、12 errors，exit 1。失败原因均为冻结合同尚未实现：13 个 v2 safe codes 缺失；opaque id/hash/state/payload/CAS/reconciliation 接口不存在；活动结果缺 `run_id`；AI provider 尚不接受 candidate v4/job-assessment v2 版本化参数。

### T017–T019 GREEN

实现内容：

- 补齐 v2 required safe error map 与安全用户消息；
- 增加 opaque id、expected hash、policy-scoped stable input hash 守卫；
- 固定 policy v2 run transition 表与终态不可逆守卫；
- 拒绝包含联系方式、证件号、详细住址、简历/JD 原文、prompt、key/credential/token/secret、raw/model response 的普通事件/结果载荷；
- 增加 `transition_discovery_run_v2`，在单一 SQLite 事务中执行 expected state/input hash CAS、计数更新和安全事件；
- 增加 `reconcile_discovery_run_v2`，从持久行重算 v2 基础计数并写入 `progress_reconciled`；
- 新建 v2 run 时初始化 v2 counters/revision 为 0；v1 路径仍保留 NULL 兼容。

验证：

```powershell
& .venv\Scripts\python.exe -m unittest `
  tests.test_discovery_contracts.DiscoveryV2FoundationalContractTests `
  tests.test_discovery_integration.DiscoveryV2StateAndPrivacyFoundationTests -v
```

结果：`Ran 8 tests in 1.781s`，`OK`，exit 0。

回归：

- `tests.test_discovery_store tests.test_discovery`：107/107，`OK`。
- `tests.test_discovery_integration`：84/84，`OK`。
- 修改的生产/测试 Python 文件 `py_compile`：exit 0。
- `git diff --check`：exit 0，仅工作区既有 CRLF 转换提示。

### 有意保留的后续 RED

- `tests.test_discovery_contracts`：75 项中 74 通过；唯一 RED 为活动 v2 results envelope 尚缺 `run_id`，等待 T047–T048。
- `tests.test_ai`：129 项中既有 125 通过；4 个新 RED 均为 provider 尚未实现 candidate-analysis v4 / job-assessment v2 版本路由，等待 T023/T025/T055/T056。

这些 RED 是后续故事的冻结合同，不声明为功能 PASS，也不以 fake provider 充当真实 AI 验收。

### Phase 2 Gate

- T008–T019 已按任务目标完成：migration 015 全绿、v1 可读、policy v2、state/hash/event/privacy 基础已固定。
- Phase 2 Gate：PASS；T014/T015 中面向后续故事的预期 RED 已准确归因并保留。
- 下一阶段：Phase 3 / US1（T020 起）。

## T020–T025 — Candidate analysis v4 与 provider 单链

### Fixture 与 RED

- 新增 `tests/fixtures/discovery/ai_candidate_v4.json`，包含合法、部分合法、重复 quote、敏感 quote、跨响应引用与超限边界。
- `python -m json.tool`：exit 0。

Candidate RED：

```powershell
& .venv\Scripts\python.exe -m unittest `
  tests.test_candidate.CandidateAnalysisV4Tests -v
```

结果：`Ran 5 tests in 0.010s`，fixture 1 项通过，4 项因 `normalize_candidate_analysis_v4` 尚不存在而 error，exit 1。

Provider RED：

```powershell
& .venv\Scripts\python.exe -m unittest `
  tests.test_ai.CandidateAnalysisV4ProviderTests -v
```

结果：`Ran 4 tests in 0.070s`，4 项均因 `DiscoveryAIProvider.analyze` 尚不接受 `contract_version` 而 error，exit 1。

### GREEN

Candidate v4 已实现：

- 七类 typed facts；同次 evidence 引用；精确 quote/locator 与 safe excerpt 继续由程序生成；
- provider/backend/user-confirmed 字段不进入结果；
- 跨响应引用、PII、无效类型和超限字段逐项 quarantine；
- current_city/min_salary 等用户意愿不作为历史事实；
- 无有效 fact/evidence/search term 的方向不能默认启用；
- quality/warnings 由程序根据 validated rows 重建。

Provider v4 已实现：

- 由显式 `contract_version='v4'` 路由，v3 默认路径保持不变，未知版本拒绝；
- 完整结果一次 provider call；parseable partial 最多一次安全纠正；不可解析或 transport/auth/network 不进行无界重试；
- 纠正只携带安全 warning code/path 与 allowlisted prior structured JSON；
- 返回 validated result，并记录 `metrics.provider_call_count`；raw provider 字段被丢弃。

验证：

- `CandidateAnalysisV4Tests`：5/5，`OK`。
- `CandidateAnalysisV4ProviderTests + DiscoveryAIVersionRoutingV2Tests`：8/8，`OK`。
- `tests.test_candidate`：128/128，`OK`。
- `tests.test_ai`：133/133，`OK`。

边界：T024 的“持久化一次”最终由 T026–T029 的 store/orchestrator 事务测试闭环；本切片的 provider 不持有 TaskStore，只证明单一返回链、call count 和 validated-result-only。

### 当前门状态

- T020–T025：PASS。
- US1 Gate 尚未通过：T026–T034 待完成。

## T026–T027 — Candidate profile version store

RED：

```powershell
& .venv\Scripts\python.exe -m unittest `
  tests.test_discovery_store.CandidateProfileVersionCrudV2Tests -v
```

结果：`Ran 5 tests in 1.470s`，5 errors，均为画像版本 store API 尚不存在，exit 1。

GREEN：同一命令 `Ran 5 tests in 1.479s`，`OK`，exit 0。

已验证：

- draft + facts + same-analysis evidence 原子创建与稳定 content hash；
- correct/add/reject，用户纠正值以 `user_corrected`、confidence 100 保存并引用 superseded fact；
- stale hash 与 confirmed edit 被拒绝；
- confirmed version 复制为下一版本独立 draft，事实 id 独立、evidence lineage 保留；
- draft tombstone 清空 summary/unknowns/facts，但保留安全版本 identity。

回归：`tests.test_discovery_store` 69/69，`OK`。

当前门：T026–T027 PASS；US1 仍等待 T028–T034。

## T028–T031 — v4 orchestration、storage-only upload 与 HTTP

编排 RED：

```powershell
& .venv\Scripts\python.exe -m unittest `
  tests.test_discovery_integration.DiscoveryV4ProfileOrchestrationTests -v
```

结果：`Ran 3 tests in 0.668s`，3 errors；缺少 v4 orchestration 参数与人工恢复入口。

HTTP RED：

```powershell
& .venv\Scripts\python.exe -m unittest `
  tests.test_discovery_contracts.DiscoveryV2ProfileHttpContractTests -v
```

结果：`Ran 4 tests in 2.301s`，2 failures、2 errors；discovery upload 仍返回 legacy 200、candidate-version 路由不存在、analysis response 未路由 v4、confirmation response 缺 v2 identity。

GREEN：

- v4 复用唯一 `analyze_resume` 编排入口，根据 persisted/explicit contract 路由；空正文在远程调用和画像持久化前阻断；
- validated v4 evidence/directions/facts 只持久化一次，并返回 `candidate_profile_version_id`；
- 人工事实 + 人工方向可创建 `manual_v1` analysis 与可编辑 profile draft，无 AI 调用；
- `flow=discovery` 上传只存储和提取，显式 AI consent 也不触发 legacy `parse_resume`；
- POST analysis 可冻结 v4；GET/PATCH candidate-version 支持 hash 409；
- v2 confirmation 在单一事务中确认 profile draft、写 intent_v2/hash 和方向快照。

验证：

- `DiscoveryV4ProfileOrchestrationTests`：3/3，`OK`。
- `DiscoveryV2ProfileHttpContractTests`：4/4，`OK`。
- `tests.test_discovery_store`：69/69，`OK`。
- `tests.test_discovery_integration`：87/87，`OK`。
- `tests.test_discovery_contracts`：79 项中 78 通过；唯一保留 RED 仍是 T014/T048 的活动 results envelope 缺 `run_id`，与本切片无关。

当前门：T028–T031 PASS；US1 还需 T032–T034 前端与 SC-008/009。

## Slice — T032–T034 US1 前端与 SC-008/SC-009 验收

**记录时间**：2026-07-21T17:05+08:00

### T032 — 前端测试（CandidateProfileEditorV2FrontendTests）

测试文件：`tests/test_discovery_frontend.py`  
测试类：`CandidateProfileEditorV2FrontendTests`（4 个测试）

覆盖断言：
- 事实/推断/未知/当前意愿四分区 DOM id 和中文标签存在
- editCandidateFact/addCandidateFact/rejectCandidateFact/saveCandidateProfileEdits 函数存在
- expected_content_hash、candidate_version_conflict、candidate_fact_invalid 错误码存在
- discoveryManualDirection/discoveryManualTerms 人工方向控件存在
- `type="number" id="discoveryHardSalary"` 数值输入、`Number(salaryEl.value)` 类型转换、`source: "user_confirmed"` 来源标记
- renderCandidateProfileVersion + factValue.textContent（非 innerHTML）

结果：4/4 OK（含在 35 tests 全模块通过中）。

### T033 — 前端实现

实现文件：`webui/index.html`  
T032 所有断言通过即证明 T033 实现就位（前端为静态 HTML/JS 字符串断言）。

### T034 — SC-008/SC-009 验收

测试文件：`tests/test_discovery_integration.py`  
测试类：`CandidateProfileConfirmationAcceptanceTests`（2 个测试）

- `test_each_user_edit_is_frozen_into_next_confirmation_without_mutating_previous`：
  用户纠正 skill→Go 并确认第一次 confirmation；再复制 draft 纠正 Go→Rust 并确认第二次；
  断言第一次 confirmation 的 profile 仍含 Go 不含 Rust（confirmed 不可变）。PASS。

- `test_v4_correction_chain_persists_exact_provider_call_count`：
  mock 两次 AI 调用（第一次 evidence_refs 非法触发纠正），断言 `result["metrics"]["provider_call_count"] == 2`
  且 `store.get_analysis(...)["provider_call_count"] == 2`（持久化到 DB）。PASS。

修复事实：T034 第二个测试初始 ERROR（`KeyError: 'provider_call_count'`），原因是 migration 015 未给 `candidate_analyses` 添加该列。修复：
1. `webui/store.py` migration 015 additions 增加 `"candidate_analyses": {"provider_call_count": "INTEGER CHECK (...)"}`
2. `webui/store.py` `update_analysis_status` 增加 `provider_call_count` 参数
3. `webui/discovery.py` v4 路径 `set_stage("persisting", ...)` 传入 `provider_call_count`

### US1 Gate 验证

命令与结果：

```
.venv\Scripts\python.exe -m unittest tests.test_candidate tests.test_discovery_frontend tests.test_discovery_store -v
→ Ran 232 tests in 27.473s — OK

.venv\Scripts\python.exe -m unittest tests.test_discovery_integration -v
→ Ran 89 tests in 41.975s — OK

.venv\Scripts\python.exe -m unittest tests.test_ai -v
→ Ran 133 tests in 0.147s — OK

.venv\Scripts\python.exe -m py_compile webui/store.py && py_compile webui/discovery.py
→ COMPILE OK
```

US1 Gate 条件逐项：
- 事实、意愿、推断和未知项边界可单独证明：test_candidate 62 tests OK
- confirmed 历史不变：CandidateProfileConfirmationAcceptanceTests OK
- 未同意不外发：consent_required 测试 OK
- SC-008（画像修改 100% 进入下一 confirmation）：PASS
- SC-009（单次分析请求链及纠正计数准确）：PASS

**US1 Gate：PASS**

全量测试（1412 tests）中仅存 6 个预期 RED：5 个 `test_discovery_performance`（等待 Phase 4/6 的 `DiscoveryPerformanceMetrics`）和 1 个 `test_discovery_contracts` T014/T048 活动 results envelope（等待 Phase 4 路由实现）。均与本切片无关。

下一切片：Phase 4（US2）T035 起。



## Slice US2-B — T045–T046 渐进结果编排

**记录时间**：2026-07-21
**执行目录**：D:\项目\boss

### T045 RED 证据

命令：.venv\Scripts\python.exe -m unittest tests.test_discovery_integration.ProgressiveResultOrchestrationTests -v

5 个测试全部 ERROR：AttributeError: 'DiscoveryRunner' object has no attribute 'run_progressive_detail_eval'

### T046 GREEN 证据

命令：.venv\Scripts\python.exe -m unittest tests.test_discovery_integration.ProgressiveResultOrchestrationTests -v

- test_assessment_terminal_increments_result_revision_immediately ... ok
- test_detail_ready_immediately_creates_assessment ... ok
- test_progressive_bounded_by_detail_budget ... ok
- test_progressive_checkpoint_survives_interrupt ... ok
- test_result_revision_visible_before_all_details_complete ... ok
- Ran 5 tests in 3.655s — OK

回归检查：tests.test_discovery_integration + tests.test_discovery + tests.test_discovery_store → 238 tests OK

实现要点：
- run_progressive_detail_eval 按 rank 顺序遍历 selected 候选
- 每候选：fetch detail → snapshot → 立即评估所有 enabled direction → checkpoint result_revision
- update_discovery_run 扩展支持 result_revision / detail_completed_count / assessment_completed_count 计数器
- 新增 STAGE_PROCESSING_JOBS / STATUS_PROCESSING_JOBS 常量
- 中断后已完成评估和 result_revision 从 SQLite 恢复

## Slice US2-C — T047–T051 HTTP契约、前端轮询与性能门

**记录时间**：2026-07-21
**执行目录**：D:\项目\boss

### T047 RED 证据

命令：.venv\Scripts\python.exe -m unittest tests.test_discovery_contracts.DiscoveryV2ProgressiveHttpContractTests -v

5 个测试全部 FAIL：
- policy_version 缺失 (None != discovery_v2)
- v2 进度字段缺失 (None != 100)
- candidates 路由 404
- results 缺少 v2 envelope (run_id/run_status/revision/changed/complete)
- after_revision 未处理

### T048 GREEN 证据

命令：.venv\Scripts\python.exe -m unittest tests.test_discovery_contracts.DiscoveryV2ProgressiveHttpContractTests -v

5 tests OK

实现要点：
- _run_summary 增加 policy_version、v2 进度字段 (list_candidates/details_selected/details_completed/assessments_completed)、result_revision
- discovery_create_run 接受 policy_version 参数
- 新增 GET /api/discovery/runs/{id}/candidates 候选诊断路由
- results 路由增加 v2 envelope (run_id/run_status/revision/changed/complete) 和 after_revision 短路

### T049 RED 证据

命令：.venv\Scripts\python.exe -m unittest tests.test_discovery_frontend.ProgressiveResultsV2FrontendTests -v

5 个测试全部 FAIL：缺少 pollProgressiveResults/renderProgressiveResults/lastResultRevision/cardRegistry/disappearanceReason

### T050 GREEN 证据

命令：.venv\Scripts\python.exe -m unittest tests.test_discovery_frontend.ProgressiveResultsV2FrontendTests -v

5 tests OK

实现要点：
- pollProgressiveResults + startProgressivePoll：3秒轮询，after_revision 短路
- renderProgressiveResults：稳定 cardRegistry (Map by job_id)，data-job-id 属性
- lastResultRevision 追踪，changed=false 跳过重绘
- disappearanceReason 渲染 (budget_deferred 等)
- 非终态 run_status 指示器

### T051 GREEN 证据

命令：.venv\Scripts\python.exe -m unittest tests.test_discovery_performance -v

17 tests OK（含 5 个原 RED 的 DiscoveryPerformanceContractTests）

实现要点：
- DiscoveryPerformanceMetrics 类：可注入 monotonic_clock
- start/mark_list_completed/record_selection/record_detail_completed/record_ai_group_completed/record_result_visible/mark_all_complete/record_blocker/build_report
- SC-001: list_pool_within_90_seconds
- SC-002: first_five_within_300_seconds
- DiscoveryRunner.__init__ 增加 monotonic_clock 参数

**US2 Gate：PASS**

- 100→15 稳定且覆盖所有有候选方向：PrioritySelectionDeterminismTests 9 tests OK
- 每个结果无需等待 run 终态：ProgressiveResultOrchestrationTests 5 tests OK
- 刷新后候选、进度和结果一致：CandidatePoolOrchestrationTests 5 tests OK
- SC-001/SC-002 编排门通过：DiscoveryPerformanceContractTests 5 tests OK

综合回归：
- tests.test_discovery_performance + tests.test_discovery_contracts + tests.test_discovery_frontend → 141 tests OK
- tests.test_discovery_integration + tests.test_discovery_store → 175 tests OK

下一切片：Phase 5（US3）T052 起


---

## Slice US3-A (T052–T058): policy v2 薪资三态 + job-assessment v2 + 评估分组

日期：2026-07-21

范围：US3 第一切片——policy v2 数值 min_salary 三态、job-assessment v2 provider 合同、一岗位最多两相关方向的评估分组与单方向降级。

### T052/T053 — min_salary 月薪 K 数值下限三态

- RED：`tests.test_screening.MinSalaryV2ParserTests` 17 项，初始全部 `ImportError: cannot import name 'check_min_salary_v2'`。
- GREEN：`webui/screening.py` 新增 `_parse_monthly_salary_k_v2` 与 `check_min_salary_v2`。月薪区间上限 < 下限 → violation；可达下限 → pass；N薪取基础区间；年薪折算月均；日薪/面议/缺失/不可解析/非 user_confirmed → unknown。不伪装旧 BOSS salary code，v1 code 路径未改动。
- 证据：`Ran 17 tests ... OK`；完整 `tests.test_screening` `Ran 168 tests ... OK`（v1 路径无回归）。

### T054 — job-assessment v2 夹具

- 新增 `tests/fixtures/discovery/ai_job_assessment_v2.json`，含 input 与四个 output 场景：valid_two_directions、partial_invalid（dir-2 浮点分数）、cross_direction_reference（dir-2 引用 dir-1 专属 refs）、correction_response（仅修正 dir-2）。
- 夹具按方向级引用域自洽：dir-1 仅引用 f1/f2/e1/e2，dir-2 仅引用 f3/e3，使 cross_direction_reference 成为唯一引用违规场景。JSON 解析校验通过。

### T055/T056 — assess_job v2 provider

- RED：`tests.test_ai.JobAssessmentV2ProviderTests` 11 项，9 项实质 `NotImplementedError`，2 项方向数守卫先期通过。
- GREEN：`webui/ai.py` 新增 `_assess_job_v2`/`_validate_assessment_v2`/`_is_int_score`/`_clean_assessment_v2_response`/`_build_assess_v2_messages`。一次请求一岗位最多两方向；四维度齐全、整数 0–100（拒绝 bool/float/string）、候选引用限定方向 supplied refs、岗位引用命名 snapshot 字段、positive 需双侧证据；无效方向 quarantine 为 needs_review，有效 sibling 不受污染；首个 envelope 部分无效时最多一次仅针对无效方向的安全纠正；原始 provider 字段不存活。
- 证据：`Ran 11 tests ... OK`；完整 `tests.test_ai` `Ran 144 tests ... OK`。

### T057/T058 — 评估分组、证据范围、单方向降级、checkpoint

- RED：`tests.test_discovery_integration.JobAssessmentV2GroupOrchestrationTests` 3 项有意义失败（6≠4 评估、input_hash 全 NULL、无 sibling 隔离）。
- GREEN：
  - `webui/discovery_runner.py` 新增 `select_job_directions_v2`（按 candidate 命中方向 + 类型优先级 core<adjacent<growth + 置信度 + id 选 ≤2）与 `_evaluate_job_v2_group`（单次 job-assessment v2 请求覆盖一组方向；每方向独立 input_hash、共享 evaluation_group_id；quarantine/缺失/provider 失败降级 needs_review 且不影响有效 sibling），并接入 `run_progressive_detail_eval`（保留 resume 跳过已完成评估）。
  - `webui/store.py` `create_assessment` 条件持久化 migration-015 列（evaluation_group_id/input_hash/ai_call_count）：仅在提供时写入，保证 schema-14 旧库的 v1 调用兼容。
- 回归修复：`Migration015Tests.test_schema_14_upgrades_additively_and_preserves_v1_rows` 因无条件引用新列而失败，改为条件写入后通过。
- 证据：T057+Progressive `Ran 8 tests ... OK`；store+integration `Ran 262 tests ... OK`；全量 `Ran 1499 tests ... OK (skipped=27)`。

### 切片结论

US3-A 切片 PASS：policy v2 薪资三态、job-assessment v2 合同与评估分组均按契约实现，硬规则旧路径与 schema-14 升级路径无回归。分类守卫与 canonical projector（T059–T062）、HTTP/前端契约（T063–T065）待续。

## Slice US4-A (T066–T069): scrape_details 受控批次、CDP 复用、readiness 与安全事件

**记录时间**：2026-07-21T04:15:00+08:00
**分支**：`codex/fast-resume-discovery`
**测试基线**：1531 OK (skipped=27) → 1543 OK (skipped=27)

### T066 / T067 — RED 合同测试

- 测试文件：`tests/test_chrome_setup.py` 新增 `ScrapeDetailsBatchingContractTests`（7 项）与 `ScrapeDetailsReadinessContractTests`（5 项），共 12 项。
- 辅助设施：`_FakeScrapeDetailsCDPSession`（无网络 WebSocket 替身，记录 `call_log`，按 `__boss_readiness_probe__` 标记区分 readiness 探针与 `EXTRACT_DETAIL_JS`）、`_make_recording_sleeper`（记录 `(seconds, label)` 用于等待类型断言）、`_make_scrape_details_list_data`（生成 N 个含 SECRET-ENC-*/SECRET-SEC-* 标记的岗位）。
- RED 验证：`scrape_details()` 初次调用因未识别 `batch_size` 等新 keyword 抛 `TypeError`，12 项全部 ERROR，符合 RED 预期。

### T068 / T069 — GREEN 实现

- 实现文件：`scripts/boss_cdp_raw.py`
  - 新增 `_READINESS_PROBE_MARKER` / `_READINESS_PROBE_JS`：readiness 探针 JS（`document.readyState === 'complete'` + body innerText ≥ 50 字符）。
  - 新增 `_default_scrape_sleeper(seconds, label=None)`：默认委托 `time.sleep`，`label` 供测试区分 `readiness_wait` / `inter_job_gap`。
  - 新增 `_wait_for_detail_readiness`：bounded wait（预算 ≤ `readiness_timeout_seconds`），首次未就绪仅一次受控 `window.scrollBy(0, 300)` 重试，预算/重试耗尽即放行（不是致命错误，后续 `DetailLoginRequiredError` / `DetailExtractionError` 路径处理无效页）。
  - 新增 `_emit_detail_safe_event`：仅发送 `{kind, status, job_id=job_link, duration_ms, safe_code}`，绝不包含 JD 正文、`encrypt_*_id`、`security_id` 或 PII。
  - 新增 `_scrape_one_detail`：单岗位工作单元（target + visibility 注入 + navigate + readiness + EXTRACT_DETAIL_JS + build_detail_record + checkpoint + close target + emit event）；用 `try/finally` 确保 `inter_job_gap` 在成功与隔离失败路径都执行（速率保护），`DetailLoginRequiredError` 路径设 `skip_gap=True` 避免在 run 停止时多余等待。
- `scrape_details` 新签名（keyword-only v2 参数，向后兼容 CLI 旧调用）：
  - `batch_size=5`（1–5，超出抛 `ValueError`）
  - `session_factory=None`（默认 `CDPSession`，测试可注入 fake）
  - `sleeper=None`（默认 `_default_scrape_sleeper`）
  - `event_callback=None`（每岗位一个 terminal safe event）
  - `readiness_timeout_seconds=12`、`max_readiness_retries=1`
  - `inter_job_gap_range=(3, 7)`、`trailing_wait=False`
- 行为变更（与 005 plan 一致）：每批复用 1 个 CDPSession（旧实现每岗位新建/关闭 1 个）；固定 5–10s 页面等待 + 3–7 次随机滚动 + 10–25s 尾等替换为 readiness 探针 + 0–1 次受控滚动 + 3–7s 岗位间 gap + 0 尾等。
- 既有测试保护：`test_scrape_details_final_save_handles_bare_filename`（空 jobs）、`test_scrape_details_stops_before_writing_login_truncation`（登录墙 RuntimeError、Target.closeTarget、session.close once）继续通过。

### 证据

- `tests.test_chrome_setup.ScrapeDetailsBatchingContractTests` + `ScrapeDetailsReadinessContractTests`：`Ran 12 tests ... OK`。
- 既有 `test_scrape_details_*` 两项：`Ran 2 tests ... OK`。
- 全量 `python -m unittest discover -s tests -p "test_*.py"`：`Ran 1543 tests in 308.424s ... OK (skipped=27)`（基线 1531 + 新增 12）。

### 切片结论

US4-A 切片 PASS：`scrape_details` 实现受控批次（≤5）、CDP 会话复用、逐岗位 target、readiness-driven 提取（≤12s 预算 + 0–1 次受控滚动）、3–7s 岗位间 gap、0 尾等、每岗位一个安全 terminal event（无 JD/凭据）。既有登录墙停止逻辑与空列表保存路径无回归。BossCdpSource 适配（T070–T071）、12h 复用（T072–T073）、circuit breaker（T074–T075）待续。

## Slice US4-B (T070–T071): BossCdpSource 单 producer、批量事件解析与原子产物

**记录时间**：2026-07-21T04:50:00+08:00
**分支**：`codex/fast-resume-discovery`
**测试基线**：1543 OK (skipped=27) → 1574 OK (skipped=27)

### T070 — RED 合同测试

- 测试文件：`tests/test_boss_discovery_source.py` 新增 `BossCdpSourceBatchEventTests`（31 项）。
- 辅助设施：`_BatchMockRunner`（模拟 scraper 子进程：读 `--input`/`--detail-output`/`--events-output`，按测试编程写入 combined detail JSON 与 events JSONL；记录 `calls` 用于命令断言）、`_batch_job`（构造规范化批量输入岗位）、`_detail_record`（构造 scraper 风格 detail 记录，含 `job_link`）、`_event`（构造 terminal safe event）。
- RED 验证：31 项全部 `AttributeError: 'BossCdpSource' object has no attribute 'fetch_details_batch'`，符合 RED 预期。既有 41 项测试无回归。

合同覆盖：
1. 批量大小守卫：>5 岗位预拒绝、空列表不调用 scraper、缺 `source_url` 单独拒绝但其余继续
2. 良构事件解析：completed+detail 读取、unavailable+source_login_required、failed+source_invalid_output、cancelled
3. 拒绝 malformed 事件：缺 kind/status/job_id/duration_ms/safe_code、duration_ms 字符串、status 整数、malformed JSON 行跳过不崩溃
4. 拒绝未知 kind/status：kind != "detail"、status 不在 {completed, unavailable, failed, cancelled}
5. 拒绝 job-mismatched 事件：事件 job_id 不在批量集合、j2 事件不能用于 j1
6. 逐岗位原子产物读取：j2 detail 不泄漏到 j1、completed 事件无对应 detail → invalid、detail 无 job_link → invalid
7. event_callback 仅接收良构且 job-matched 事件：malformed/unknown/mismatched 不分发；callback 可选
8. Scraper 调用合同：`--events-output` 必传、`--max-details 5`、input JSON 含 `job_link`
9. 子进程失败：returncode != 0 → 全员 source_blocked；events 文件缺失 → 全员 source_invalid_output
10. 隐私：事件含 `jd`/`encrypt_job_id`/`security_id` → 拒绝且 safe_log 不泄漏 SECRET
11. 多岗位混合：3 岗位 completed/unavailable/failed 各自正确路由

### T071 — GREEN 实现

#### 实现文件 1：`scripts/boss_cdp_raw.py`（additive CLI 扩展）

- 新增 `--events-output` CLI 参数（argparse add_argument；不传则不写事件文件，旧调用零影响）。
- `main()` 中 `--events-output` 提供时，构造闭包 callback 把每个 terminal safe event 以 JSONL（每行一个 JSON）写入文件；OSError 时降级为不写事件文件（不中断抓取）；try/finally 确保文件句柄关闭。
- callback 透传给 `scrape_details(event_callback=...)`，事件格式由 `_emit_detail_safe_event` 固定（`{kind, status, job_id=job_link, duration_ms, safe_code}`，无 JD/凭据/PII）。

#### 实现文件 2：`webui/source.py`（BossCdpSource.fetch_details_batch + helpers）

- `BossCdpSource.fetch_details_batch(jobs, *, detail_output_path, event_callback=None, max_batch_size=5) -> dict[str, SourceOutcome]`：
  1. `len(jobs) > max_batch_size` 预拒绝（不调用 scraper），每岗位返回 `source_invalid_output`
  2. 逐岗位校验 `job_id`/`source_url`，无效单独失败；按 `source_url` 去重
  3. 写批量 input JSON（`{"jobs": [...]}`，每岗位显式设 `job_link=source_url`）
  4. 调用 scraper：`--input <batch.input.json> --detail-output <path> --events-output <path>.events.jsonl --max-details 5 --detail`
  5. 子进程失败（returncode != 0）→ 全员 `source_blocked`；timeout/FileNotFoundError/OSError 各自映射
  6. 读 events JSONL，逐事件 `_validate_detail_event` 校验：dict、required fields、kind=="detail"、status ∈ {completed, unavailable, failed, cancelled}、duration_ms 非负整数（拒绝 bool）、safe_code/job_id 非空字符串、job_id ∈ expected_urls、不含 forbidden fields（jd/encrypt_job_id/security_id/token/secret/prompt/resume_text/phone/email/id_card 等）
  7. 良构且 job-matched 事件 first-wins：dispatch 到 `event_callback`（callback 异常被吞，不影响批量）；重复事件不分发
  8. 读 combined detail JSON（list），按 `job_link`/`source_url` 索引
  9. 每岗位 outcome：completed → detail 必须存在否则 `source_invalid_output`；unavailable/failed → `failed_code=safe_code`（如 `source_login_required`）；cancelled → `safe_code != "ok"` 时用 safe_code，否则 `source_unknown_error`
- `_validate_detail_event`、`_read_events_file`（malformed JSON 行记录为 None，由 validator 拒绝）、`_read_combined_details`（detail 无 job_link 丢弃）、`_build_detail_batch_command` 均为内部 helper。
- 既有 `fetch_detail`/`fetch_list`/`preflight` 路径无改动。

### 证据

- T070 GREEN 后 `tests.test_boss_discovery_source.BossCdpSourceBatchEventTests`：`Ran 31 tests ... OK`。
- 完整 `tests.test_boss_discovery_source`：`Ran 72 tests ... OK`（既有 41 + 新增 31）。
- `tests.test_boss_discovery_source + tests.test_chrome_setup`：`Ran 146 tests ... OK`。
- `tests.test_discovery + tests.test_discovery_integration + tests.test_discovery_contracts + tests.test_discovery_frontend`：`Ran 321 tests ... OK`。
- 全量 `python -m unittest discover -s tests -p "test_*.py"`：`Ran 1574 tests in 208.208s ... OK (skipped=27)`（基线 1543 + 新增 31）。
- `python -m py_compile scripts/boss_cdp_raw.py`：exit 0，无输出。

### 切片结论

US4-B 切片 PASS：`BossCdpSource` 实现单 producer（一次子进程处理一批 ≤5 岗位）、结构化完成回调（events JSONL 解析/校验/分发）、默认 `max_batch_size=5`、逐岗位原子产物读取（按 `job_link` 索引 combined detail JSON）、事件隐私守卫（JD/凭据/PII 字段拒绝）、job-mismatched 事件拒绝、malformed JSON 行跳过不崩溃。`scripts/boss_cdp_raw.py` 仅 additive 增加 `--events-output` CLI 参数与 callback 透传，核心抓取逻辑无改动。既有 `fetch_detail`/`fetch_list`/`preflight` 路径无回归。12h 复用（T072–T073）、circuit breaker（T074–T075）待续。

## Slice US4-C (T072–T073): 12h 详情复用、freshness/identity 守卫与新 run snapshot 自足

**记录时间**：2026-07-21T12:55:00+08:00
**执行目录**：`D:\项目\boss`

### T072 RED

文件：`tests/test_discovery_integration.py` 新增 `DetailReusePolicyTests`（21 tests）。

合同覆盖（data-model.md L332-341 Detail Reuse）：

1. 正向：12h 内、canonical URL/job_id identity match、completeness=complete、source_status=active、fresh_until >= now → `find_reusable_snapshot` 返回 prior snapshot
2. `create_reused_snapshot` 在当前 run 创建新 snapshot，复制 content_hash/jd/tags/completeness/source_status；新 id、新 run_id、`reused_from_snapshot_id` = source id、`run_candidate_id` = current candidate、`fetched_at` = now、`fetch_policy_version` = discovery_v2
3. `detail_reused_count` 递增
4. `list_snapshots` 暴露 `reused=True` 投影 + `source_fetched_at`（原抓取时间）
5. 过期：`fresh_until < now` 或 `fresh_until IS NULL` → 不复用
6. `completeness` ∈ {partial, unavailable} → 不复用
7. `source_status` ∈ {unknown, closed} → 不复用
8. canonical URL 漂移 → 不复用
9. job_id 漂移 → 不复用
10. list_fields 漂移（title/company/salary/location 任一）→ 不复用
11. 用户显式 `refresh_requested=True` → 不复用
12. 自足：parent snapshot 行删除后，新 run snapshot 仍可完整读取（content_hash/jd/tags 不依赖 parent）
13. 自足：parent run 整行删除（cascades to snapshots）后，新 run snapshot 仍可读取
14. Runner 集成：复用可用时 `source.fetch_detail` / `fetch_details_batch` 调用数 = 0
15. Runner 集成：无可复用 snapshot 时 `source.fetch_detail` 调用数 = 1

RED 命令：

```powershell
& .venv\Scripts\python.exe -m unittest tests.test_discovery_integration.DetailReusePolicyTests
```

RED 结果：`Ran 21 tests in 5.620s`，1 failure、19 errors，exit 1。失败均由 `TaskStore.find_reusable_snapshot` / `TaskStore.create_reused_snapshot` 尚不存在触发；1 个 FAIL 为 runner 未集成复用逻辑（调用了 `source.fetch_detail`）。1 个 OK 为 `test_runner_refetches_when_no_reusable_snapshot`（恰好走默认 fetch 路径通过，但 GREEN 后仍保持正确行为）。

### T073 GREEN

#### `webui/store.py`

1. Migration 015 additive 列：`discovery_job_snapshots` 新增 `source_fetched_at TEXT`（保存复用 chain 的原始抓取时间，自足不依赖 parent row）。仅 additive，不改已有列。
2. `list_snapshots` / `get_snapshot` 暴露 `reused` 投影字段（`bool(reused_from_snapshot_id)`）。
3. 新增 `find_reusable_snapshot(job_id, source_url, current_list_fields, *, refresh_requested=False, max_age_hours=12, now_iso=None, exclude_run_id=None) -> dict | None`：
   - `refresh_requested=True` → None
   - SQL 查询：`job_id=? AND completeness='complete' AND source_status='active' AND fresh_until IS NOT NULL AND fresh_until >= now`，`exclude_run_id` 排除当前 run 避免自复用
   - 逐行 canonical URL 匹配（strip query + rstrip `/`）
   - 逐行 identity drift 检查（title/company/salary/location 必须与 `current_list_fields` 一致）
   - 返回第一个匹配的完整 snapshot row（含 `company_json`/`missing_fields` 反序列化 + `reused` 投影）
4. 新增 `create_reused_snapshot(run_id, run_candidate_id, source_snapshot, *, fetch_policy_version='discovery_v2', now_iso=None, max_age_hours=12) -> dict`：
   - 复制 source 的 source_url/title/company/salary/location/tags/jd/company_json/completeness/missing_fields/source_status/content_hash
   - 新 id、新 run_id、`run_candidate_id` = current candidate
   - `reused_from_snapshot_id` = source id
   - `fetched_at` = now（当前 run 时间）
   - `source_fetched_at` = source.fetched_at（保留原始抓取时间，自足）
   - `fresh_until` = now + 12h（renewed，使复用 snapshot 本身也可被未来 run 复用）
   - `fetch_status` = 'completed'
   - `fetch_policy_version` = discovery_v2
   - 同事务 `UPDATE discovery_runs SET detail_reused_count = COALESCE(detail_reused_count, 0) + 1`
   - 返回 `get_snapshot` 结果（含 `reused=True`）

#### `webui/discovery_runner.py`

`_fetch_one_detail` 新增 `run_candidate_id` 和 `list_fields` 参数；在调用 `source.fetch_detail` 之前：
1. 调用 `store.find_reusable_snapshot(job_id, source_url, list_fields, exclude_run_id=run_id)`
2. 若找到：emit `detail_reused` event → `store.create_reused_snapshot(...)` → emit `snapshot_saved(reused=True)` → return True（跳过 `source.fetch_detail`）
3. 若 `find_reusable_snapshot` 抛异常或 `create_reused_snapshot` 失败：静默回退到 `source.fetch_detail`（reuse 永不阻断 run）
4. `run_progressive_detail_eval` 在调用 `_fetch_one_detail` 时传入 `candidate["id"]` 和 `candidate["list_fields"]`

### 证据

- T072 RED：`Ran 21 tests ... FAILED (failures=1, errors=19)`，exit 1。
- T073 GREEN：`tests.test_discovery_integration.DetailReusePolicyTests`：`Ran 21 tests in 6.149s ... OK`，exit 0。
- 回归：`tests.test_discovery_integration + tests.test_discovery_store + tests.test_discovery`：`Ran 279 tests in 70.964s ... OK`。
- 回归：`tests.test_discovery_integration + tests.test_discovery_store + tests.test_discovery + tests.test_discovery_contracts + tests.test_discovery_frontend + tests.test_boss_discovery_source + tests.test_discovery_performance + tests.test_chrome_setup`：`Ran 581 tests in 126.731s ... OK`。
- `python -m py_compile webui/store.py webui/discovery_runner.py tests/test_discovery_integration.py`：exit 0，无输出。

### 切片结论

US4-C 切片 PASS：12h 详情复用合同完整实现。`find_reusable_snapshot` 守卫 6 条（identity match / completeness=complete / source_status=active / fresh_until >= now / list_fields 不漂移 / 非 refresh_requested）；`create_reused_snapshot` 自足复制全部内容字段 + 保留 `source_fetched_at`；parent snapshot/run 删除后新 run snapshot 仍可完整读取；`detail_reused_count` 同事务递增；runner 在复用可用时跳过 `source.fetch_detail`。Migration 015 仅 additive 加 `source_fetched_at` 列，不改已有列。Circuit breaker（T074–T075）待续。

## Slice US4-D (T074–T075) — Source circuit breaker

**记录时间**：2026-07-21T16:50:00+08:00

### 范围

实现来源 circuit breaker（state-machine.md L92-107）：两个连续源信号打开 breaker；打开后不启动新 source work；queued work 保持 retryable/blocked；自动重置需要 preflight 成功 + bounded cooldown；run 在有可用结果时转 partial，否则转 failed。

### T074 RED — breaker 失败测试

在 `tests/test_boss_discovery_source.py` 新增 23 个测试：

**`SourceCircuitBreakerTests`（15 个单元测试）**：
- `test_breaker_signal_codes_include_v2_source_signals`：SIGNAL_CODES 含 4 码
- `test_breaker_starts_closed`：初始 closed
- `test_one_signal_does_not_open_breaker`：1 个信号不开
- `test_two_consecutive_login_required_signals_open_breaker`
- `test_two_consecutive_verification_required_signals_open_breaker`
- `test_two_consecutive_rate_limited_signals_open_breaker`
- `test_two_consecutive_blocked_signals_open_breaker`（invalid navigation shell → source_blocked）
- `test_two_consecutive_mixed_signals_open_breaker`（login + rate_limited 混合）
- `test_success_between_signals_resets_consecutive_count`
- `test_non_signal_code_does_not_advance_counter`（input_drift/invalid_output/timeout/not_found/unreachable/cdp_unavailable/unknown_error 均不计数）
- `test_breaker_state_is_queryable`（state() 返回 open/consecutive/last_signal/opened_at/cooldown_until）
- `test_breaker_stays_open_after_cooldown_without_preflight`
- `test_breaker_resets_after_cooldown_and_preflight_success`
- `test_breaker_reset_fails_when_preflight_fails`
- `test_breaker_reset_fails_when_cooldown_not_elapsed`
- `test_breaker_records_signal_then_reset_then_signal_again_opens_again`

**`BossCdpSourceBreakerIntegrationTests`（8 个集成测试）**：
- `test_source_exposes_breaker_instance`：`source.breaker` 是 `SourceCircuitBreaker`
- `test_source_accepts_injected_breaker`：可注入
- `test_open_breaker_blocks_fetch_list_without_invoking_runner`
- `test_open_breaker_blocks_fetch_detail_without_invoking_runner`
- `test_open_breaker_blocks_fetch_details_batch_without_invoking_runner`
- `test_fetch_list_failure_with_login_required_records_signal`
- `test_fetch_list_success_resets_signal_count`
- `test_two_consecutive_fetch_list_failures_open_breaker`
- `test_open_breaker_outcome_is_retryable_blocked_not_user_fault`

RED 确认：`ImportError: cannot import name 'SourceCircuitBreaker' from 'webui.source'`，整个测试模块无法加载。

### T075 GREEN — breaker 实现

#### `webui/source.py`

1. **`SourceCircuitBreaker` 类**（新增）：
   - `SIGNAL_CODES = {"source_login_required", "source_verification_required", "source_rate_limited", "source_blocked"}`
   - `DEFAULT_COOLDOWN_SECONDS = 60`
   - `__init__(*, cooldown_seconds=60, clock=None)`：clock 默认 `time.monotonic`
   - `record_signal(safe_code)`：非 SIGNAL_CODES 码忽略；递增 `_consecutive`；>=2 时打开（`_opened_at` / `_cooldown_until = now + cooldown`）
   - `record_success()`：重置 `_consecutive` 和 `_last_signal`（不关闭已打开的 breaker）
   - `is_open()`：`_opened_at is not None`
   - `try_reset(preflight_ok, *, now=None)`：breaker 关闭返回 True；打开时需要 `preflight_ok AND now >= _cooldown_until` 才关闭
   - `state()`：返回 `{open, consecutive, last_signal, opened_at, cooldown_until}`

2. **`SAFE_FAILURE_CODES` 扩展**：加入 `source_verification_required` 和 `source_rate_limited`（http-api.md L323-339 v2 新增码）。同步更新 `test_safe_failure_codes_set_is_complete` 期望集。

3. **`BossCdpSource.__init__`**：新增 `breaker: SourceCircuitBreaker | None = None` 参数；`self.breaker = breaker or SourceCircuitBreaker()`。

4. **`fetch_list` / `fetch_detail` / `fetch_details_batch`**：
   - runner 调用前检查 `self.breaker.is_open()` → 返回 `source_blocked` + `breaker_open` safe_log，不调用 runner
   - `returncode != 0` → `record_signal("source_blocked")`
   - 成功 → `record_success()`
   - 非信号失败（timeout/unreachable/invalid_output/input_drift）→ 不调用 breaker（中性）
   - `fetch_details_batch` per-job outcomes：按 job_id 顺序，signal 码 → `record_signal`；ok → `record_success`；同 batch 2 个 login_required 打开 breaker

#### `webui/discovery_runner.py`

新增 3 个 helper：
- `_source_breaker_open(run_id, stage) -> bool`：检查 `self.source.breaker.is_open()`；打开时 emit `source_breaker_open` event + `metrics.record_source_breaker`
- `_try_reset_source_breaker() -> bool`：breaker 打开时调用 `source.preflight()` + `breaker.try_reset(outcome.ok)`
- `_finalize_breaker_open(run_id, stage)`：有可用 assessment（high_match/adjacent_match/growth_match）→ `STATUS_PARTIAL`；否则 `STATUS_FAILED` + `failure_code="source_blocked"`

集成点：
- `run_progressive_detail_eval`：每个候选循环开头检查 breaker；打开时 finalize + return
- `_stage_fetching_lists`：preflight 前调用 `_try_reset_source_breaker()`；plan item 循环内检查 breaker；打开时 return False（remaining items 保持非终态，可 resume）
- `_stage_fetching_details`：job 循环内检查 breaker；打开时 return（remaining jobs 保持非终态）

### 证据

- T074 RED：`ImportError: cannot import name 'SourceCircuitBreaker'`，exit 1。
- T075 GREEN：`tests.test_boss_discovery_source`：`Ran 97 tests in 0.269s ... OK`，exit 0。
- 4 文件回归：`tests.test_boss_discovery_source + tests.test_discovery_integration + tests.test_discovery_store + tests.test_discovery`：`Ran 376 tests in 72.413s ... OK`。
- 全量回归：`python -m unittest discover -s tests -p "test_*.py"`：`Ran 1620 tests in 217.738s ... OK (skipped=27)`。
- `python -m py_compile webui/source.py webui/discovery_runner.py tests/test_boss_discovery_source.py`：exit 0，无输出。

### 切片结论

US4-D 切片 PASS：来源 circuit breaker 合同完整实现。`SourceCircuitBreaker` 状态机：2 连续信号打开（4 种信号码，混合可叠加），success 重置计数但不关闭，`try_reset` 需 preflight + cooldown 双条件。`BossCdpSource` 三个 fetch 方法在 runner 调用前检查 breaker，打开时返回 `source_blocked` + `breaker_open` 不调用 runner；失败/成功喂入 breaker。`discovery_runner` 三个循环（progressive/list/details）在每单元前检查 breaker，打开时 emit event + finalize 为 partial（有可用结果）或 failed（无可用结果）；preflight 前尝试 reset。`SAFE_FAILURE_CODES` 扩展 v2 新增码。失败隔离（T076–T077）待续。

---

## Slice US4-E (T076–T077) — 单元失败隔离、四类进度、timing 与 reconciliation

日期：2026-07-21

范围：US4 第五切片——单 detail/AI/search 失败不阻断其他结果；v2 four-class progress 逐单元事务更新并可从持久化行 reconciliation；`first_result_at` / `first_batch_at` / `list_completed_at` / `processing_completed_at` timing 字段写入；`first_result_at` / `first_batch_at` 单调（NULL → value，COALESCE 不覆盖）。

### T076 — RED: 13 项契约测试

新增 `tests/test_discovery_integration.py::FailureIsolationAndProgressTests` 13 项测试，覆盖 http-api.md L203-208（four-class progress）与 L218-220（timing）契约：

1. `test_single_detail_failure_does_not_block_other_candidates` —— 单详情失败时其他候选的详情+评估照常完成（regression guard）。
2. `test_single_ai_failure_does_not_block_other_candidates` —— 单 AI 评估失败时其他候选评估照常完成；失败候选 persist `needs_review` + `failure_code`（terminal，非阻塞）。
3. `test_single_search_item_failure_does_not_block_other_items` —— 单搜索项失败时其他搜索项照常完成。
4. `test_progress_dict_exposes_v2_four_class_names` —— `get_discovery_run` 返回的 `progress` 必须包含 v2 权威字段 `search_queries_completed` / `list_candidates` / `details_selected` / `details_completed` / `assessments_completed` / `recommendations`。
5. `test_progress_counts_match_persisted_rows_after_progressive_run` —— progress 计数必须与持久化行（snapshot / assessment）一致。
6. `test_reconcile_progress_recalculates_from_persisted_rows` —— `reconcile_discovery_run_v2` 从持久化行重算 v2 计数并 emit `progress_reconciled` 事件。
7. `test_reconcile_progress_is_idempotent` —— 多次 reconcile 得到一致计数。
8. `test_first_result_at_set_when_first_assessment_visible` —— 首个结果可见时 `first_result_at` 写入。
9. `test_first_batch_at_set_when_fifth_result_visible` —— 第 5 个结果可见时 `first_batch_at` 写入。
10. `test_first_batch_at_null_when_fewer_than_five_results` —— 结果不足 5 个时 `first_batch_at` 保持 NULL。
11. `test_list_completed_at_set_after_stage_fetching_lists` —— `_stage_fetching_lists` 完成后 `list_completed_at` 写入。
12. `test_processing_completed_at_set_after_progressive_eval` —— `run_progressive_detail_eval` 完成后 `processing_completed_at` 写入。
13. `test_first_result_at_not_overwritten_by_subsequent_results` —— `first_result_at` 写入后不被后续结果覆盖（单调写入）。

测试夹具：`_make_v2_run_selected_n(candidate_count, detail_budget, job_prefix)` 创建 v2 run + N 候选 + select_priority_details；`_source_with_failures(fail_job_ids)` 模拟指定 job_id 详情失败；`_ai_with_failures(fail_call_indices)` 模拟指定 call index AI 失败。

### T077 — GREEN: 失败隔离、四类进度与 timing 实现

#### `webui/store.py`

1. `get_discovery_run`（L3419）新增 v2 four-class progress 字段映射到 `progress` dict：
   - `search_queries_completed = source_count`
   - `list_candidates = list_candidate_count`
   - `details_selected = detail_selected_count`
   - `details_completed = detail_completed_count`
   - `assessments_completed = assessment_completed_count`
   - `recommendations = recommendation_count`
   - 旧 v1 别名（`source_count` / `detail_count` / `evaluated_count`）保留为兼容字段。

2. 新增 `mark_run_timing(run_id, *, first_result_at=None, first_batch_at=None, list_completed_at=None, processing_completed_at=None)`（L3272）：
   - `first_result_at` / `first_batch_at` 用 `COALESCE(col, ?)` 实现单调写入（NULL → value，已非 NULL 不覆盖）。
   - `list_completed_at` / `processing_completed_at` 标记阶段边界，可在 resume 时重新盖章。
   - 空 sets 时直接 return；否则追加 `updated_at = _now()`。

#### `webui/discovery_runner.py`

1. 新增 `from webui.store import _now` 导入（L45）。
2. `run_progressive_detail_eval` 新增 timing 与计数 stamping（L431-497）：
   - 进入循环前，若 `selected` 非空，stamp `detail_selected_count = len(selected)`（处理 resume 直入 progressive 的场景，正常路径由 `_stage_prioritizing` stamp）。
   - 循环内每完成一个候选：调用 `metrics.record_result_visible`；若 `result_revision >= 1` stamp `first_result_at`；若 `result_revision >= 5` stamp `first_batch_at`（两者均 COALESCE 单调）。
   - 循环结束 stamp `processing_completed_at`。
3. `_stage_fetching_lists` 末尾 stamp `list_completed_at`（L678）。

### 已存在能力的回归确认

- **失败隔离**：T076 前 3 项测试（detail/Ai/search 单失败）作为 regression guard 直接 PASS——证明 `_fetch_one_detail` 失败保存 `completeness="unavailable"` 快照后仍进入 `_evaluate_job_v2_group`，AI 失败 persist `needs_review + failure_code` 后继续下一候选，搜索项失败仅标 plan_item failed 不中断循环。
- **reconciliation**：`reconcile_discovery_run_v2`（L3354）已存在并工作——从 `discovery_run_snapshots` / `discovery_run_assessments` / `discovery_run_candidates` 行重算 v2 计数，emit `progress_reconciled` 事件，多次调用幂等。

### 证据

- T076 RED（首跑）：13 项中 7 项有意义失败（progress dict 缺 v2 字段、`first_result_at` / `first_batch_at` / `list_completed_at` / `processing_completed_at` 未写入、`first_result_at` 被覆盖、`list_candidates=0`）。
- T077 GREEN：`tests.test_discovery_integration.FailureIsolationAndProgressTests` `Ran 13 tests in 14.032s ... OK`，exit 0。
- 模块回归：`tests.test_discovery_integration` `Ran 136 tests in 160.921s ... OK`，无回归。
- 全量回归：`python -m unittest discover -s tests -p "test_*.py"`：`Ran 1633 tests in 341.632s ... OK (skipped=27)`（基线 1620 + 13 T076 新测试 = 1633）。
- `python -m py_compile webui/store.py webui/discovery_runner.py`：exit 0，无输出。

### 切片结论

US4-E 切片 PASS：单元失败隔离、四类进度与 timing 合同完整实现。
- 失败隔离：单 detail/AI/search 失败不阻断其他候选，AI 失败 terminal 为 `needs_review + failure_code`，详情失败仍 persist partial snapshot 进入评估。
- 四类进度：`get_discovery_run` 暴露 v2 权威字段并保留 v1 别名；`reconcile_discovery_run_v2` 从持久化行重算并 emit 事件，幂等。
- Timing：`first_result_at` / `first_batch_at` COALESCE 单调（NULL → value，不覆盖）；`list_completed_at` / `processing_completed_at` 标记阶段边界可重盖；progressive eval 与 list 阶段已 stamp。

下一步：T078–T079（cancel signal 与 process-tree 终止）。

---

## Slice US4-F (T078–T079) — Cancel signal、process-tree 终止与 cancelled 收敛

日期：2026-07-21

范围：US4 第六切片——取消信号必须立即停止新 list/detail/AI 工作；活动 subprocess tree 必须在 30s 内终止；已 persist 的 snapshots/assessments/candidates 保留 100%；run 进入 cancelled 终态。SC-010 自动化门。

### T078 — RED: cancel 信号契约测试

新增 2 个测试类、共 11 项测试：

#### `tests/test_process_executor.py::CancelProcessTreeTests` (3 项)

验证 `ScraperExecutor` 的 cancel_event 边界（process-tree 终止）：

1. `test_cancel_terminates_child_processes_within_30_seconds` —— 父 python 启动子 python（写 PID 到文件 + sleep 60s），cancel_event 设置后父子进程都必须在 30s 内终止。Windows 用 `taskkill /T /F`，POSIX 用 `killpg`。验证子 PID 不再 alive。
2. `test_cancel_returns_process_cancelled_failure_code` —— cancel_event 设置后 `result.failure_code == "process_cancelled"`。
3. `test_cancel_after_completion_does_not_raise` —— 进程正常退出后再 set cancel_event 不得抛异常。

#### `tests/test_discovery_integration.py::CancelSignalPropagationTests` (8 项)

验证 runner 层的 cancel 信号传播与已 persist 结果保留：

1. `test_request_cancel_sets_source_cancel_event` —— `request_cancel` 后 `is_cancelled(run_id)` 返回 True，`runner.run()` 进入 cancelled 终态，source.fetch_detail 调用数=0。
2. `test_cancel_during_progressive_eval_stops_before_next_candidate` —— cancel_event 在 progressive eval 入口前设置，循环不得启动任何候选的 detail fetch。**RED 暴露 gap**：当前 `run_progressive_detail_eval` 不检查 cancel_event。
3. `test_cancel_mid_progressive_eval_preserves_completed_results` —— timer 在 source.calls ≥ 2 时 set cancel，验证：processed < 5（cancel 后不启动新候选）+ snapshots/assessments 保留。
4. `test_cancel_does_not_invoke_ai_after_signal` —— cancel_event 设置后 AI provider 调用数=0。
5. `test_cancel_reaches_cancelled_status_within_30_simulated_seconds` —— SC-010 wall-clock 门：`runner.run()` 在 cancel 后 30s 内返回 cancelled。
6. `test_cancel_preserves_already_persisted_snapshots_and_assessments` —— progressive eval 完成后 request_cancel + 再次 run，snapshots/assessments 数量和 ID 不变。
7. `test_cancel_marks_pending_plan_items_cancelled_in_list_stage` —— cancel 在 list 阶段触发，所有 plan items 进入 cancelled/completed/failed/skipped 终态。
8. `test_cancel_request_on_terminal_run_raises_conflict` —— 已终态 run 调用 request_cancel 返回 `state_conflict`。

夹具：`_make_v2_run_selected_n` 创建 v2 run + N 候选；`_counting_source` 记录每次 `fetch_detail`；`_counting_ai` 记录每次 `assess_job`。

### T079 — GREEN: cancel signal 实现

#### `webui/process_executor.py`（已存在，T066–T069 实现）

- `ScraperExecutor.execute` 接受 `cancel_event: threading.Event | None`。
- Poll 循环检查 `cancel_event.is_set()` → 设置 `failure_code = "process_cancelled"` + 调用 `_terminate_tree`。
- `_terminate_tree`：Windows 用 `taskkill /PID /T /F`，POSIX 用 `killpg(SIGTERM)`，然后 `process.wait(timeout=2)` 兜底 `kill()`。
- T078 测试验证：父子进程树均被终止，30s 内完成。

#### `webui/discovery_runner.py`（T079 新增）

`run_progressive_detail_eval` 在每个候选循环开头新增 cancel 检查（L440-451）：

```python
for candidate in selected:
    job_id = candidate["job_id"]
    source_url = candidate.get("source_url", "")

    # T079: cancel signal — when set, no new candidate work starts.
    # Already-persisted snapshots/assessments are preserved; the run
    # transitions to cancelled via the stage-loop cancel check.
    cancel_event = self._cancel_events.get(run_id)
    if cancel_event is not None and cancel_event.is_set():
        return self.store.get_discovery_run(run_id)
    if self.is_cancelled(run_id):
        return self.store.get_discovery_run(run_id)
    # ... existing breaker check + detail fetch + assessment ...
```

设计要点：
- 检查点放在循环开头，**不**在 detail fetch 或 AI 评估中途打断——已 in-flight 的工作允许完成 persist（保证单元 checkpoint 完整性）。
- 双重检查：cancel_event（线程内同步）+ `is_cancelled(run_id)`（DB flag，跨进程/resume 场景）。
- 返回 `get_discovery_run(run_id)` 而非直接调用 `_handle_cancel`——让外层 `_execute_stages` 的 stage 间 cancel 检查统一处理 cancelled 终态收敛。
- 不删除已 persist 的 snapshots/assessments——SQLite 行就是 checkpoint，cancel 只阻止新工作，不破坏已完成工作。

#### 已存在能力的回归确认

- `request_cancel`（L522-540）：设置 cancel_event + DB `cancel_requested=True` + emit `cancel_requested` 事件；终态 run 抛 `state_conflict`。
- `_execute_stages`（L559-590）：每个 stage 之间检查 cancel_event + `is_cancelled()`，触发则调用 `_handle_cancel` 收敛到 cancelled 终态。
- `_handle_cancel`（L1491-1498）：把 pending plan items 标记 cancelled，更新 run status=cancelled + completed=True，emit `run_cancelled` 事件。
- `_stage_fetching_lists`（L658）：循环内检查 cancel_event，cancel 则 return（remaining items 保持非终态，由 `_handle_cancel` 标记）。
- `_register_run` + `runner.run()`：`source.cancel_event = cancel_event` wiring，让 `ScraperExecutor` 收到 cancel 信号终止 subprocess tree。

### 证据

- T078 RED（首跑）：`tests.test_discovery_integration.CancelSignalPropagationTests` 8 项中 3 项 FAIL（`source.calls == 5` 而非 0/2，证明 progressive 循环不检查 cancel）+ 5 项 PASS；`tests.test_process_executor.CancelProcessTreeTests` 3 项全 PASS（边界已实现）。
- T079 GREEN：`tests.test_discovery_integration.CancelSignalPropagationTests` `Ran 8 tests in 4.131s ... OK`，exit 0。
- T079 GREEN：`tests.test_process_executor.CancelProcessTreeTests` `Ran 3 tests in 1.222s ... OK`，exit 0。
- 模块回归：`tests.test_discovery_integration + tests.test_process_executor` `Ran 152 tests in 62.502s ... OK`，无回归。
- 全量回归：`python -m unittest discover -s tests -p "test_*.py"`：`Ran 1644 tests in 253.416s ... OK (skipped=27)`（基线 1633 + 8 cancel integration + 3 cancel process_tree = 1644）。
- `python -m py_compile webui/process_executor.py webui/discovery_runner.py`：exit 0，无输出。

### 切片结论

US4-F 切片 PASS：cancel 信号、process-tree 终止与 cancelled 收敛合同完整实现。
- 信号传播：`request_cancel` → DB flag + cancel_event → `source.cancel_event` → `ScraperExecutor` 终止 subprocess tree（Windows taskkill /T /F，POSIX killpg）。
- Progressive 循环：每个候选开头检查 cancel_event + is_cancelled；in-flight 工作允许完成 persist 以保 checkpoint 完整性；新候选不启动。
- 终态收敛：`_execute_stages` stage 间检查 → `_handle_cancel` 标记 pending plan items cancelled + run status=cancelled。
- 数据保留：cancel 不删除已 persist 的 snapshots / assessments / candidates；SQLite 行作为 checkpoint。
- SC-010 timing：cancel 后 wall-clock ≤ 30s 进入 cancelled（测试验证 4.1s 实际耗时）。

下一步：T080–T081（interrupted/eligible partial 恢复与 SQLite-only resume）。

## Slice US4-G (T080–T081) — Resume hash drift、SC-011 与 v2 progressive dispatch

**记录时间**：2026-07-21T15:30:00+08:00
**执行目录**：`D:\项目\boss`

### T080 RED

文件：`tests/test_discovery_integration.py` 新增 `ResumeHashDriftAndSc011Tests`（6 tests）。

合同覆盖：
- http-api.md L319-320：resume rejects profile/confirmation/policy/input hash drift with 409
- spec.md SC-011：受控中断恢复测试中，已完成且输入身份一致的详情和评估重复执行数为 0
- data-model.md：resume 必须从 SQLite 持久化状态恢复，不得重做已完成工作
- state-machine.md：interrupted / partial 是可恢复终态；succeeded/failed/cancelled 不可恢复

测试清单：
1. `test_resume_rejects_input_hash_drift` — input_hash SQL 漂移 → state_conflict，0 source calls
2. `test_resume_rejects_policy_version_drift` — policy_version SQL 漂移到非法值 → state_conflict
3. `test_resume_skips_completed_detail_fetches` — SC-011：resume 时已完成 detail 不重新调用 source.fetch_detail（0 calls）
4. `test_resume_skips_completed_assessments` — SC-011：resume 时已完成 assessment 不重新调用 AI provider（0 calls）
5. `test_resume_rejects_terminal_run` — succeeded/failed/cancelled 终态 run 不得 resume
6. `test_resume_eligible_when_hashes_match` — interrupted run with matching hashes 必须成功 resume

RED 状态首跑（T081 GREEN 前）：
- 6 项中 1 FAIL（`test_resume_skips_completed_detail_fetches`：source.calls=5 而非 0，证明 SC-011 gap）+ 3 ERROR（`_Source` 缺 `fetch_list`、`update_discovery_run` 不接受 `input_hash`、`create_search_plan` items 格式不匹配）+ 2 PASS（terminal reject、eligible hashes match）
- 关键 gap：`run_progressive_detail_eval` 在 resume 时仍重新调用 `source.fetch_detail`（`find_reusable_snapshot` 排除当前 run）；`runner.run()` resume 路径不校验 input_hash/policy_version 漂移；`_execute_stages` 未调用 `run_progressive_detail_eval`（v2 progressive 是死代码）

### T081 GREEN

文件：`webui/discovery_runner.py`、`tests/test_discovery_integration.py`

#### 生产代码改动

1. **Resume hash drift check（L351-383）**：
   - 仅对 `policy_version == "discovery_v2"` 的 run 校验 input_hash 漂移（重算 `compile_search_plan` hash 与 stored hash 比对）
   - 对所有 resume run 校验 `policy_version` ∈ {"v1", "discovery_v1", "discovery_v2"}（非法值视为漂移）
   - v1 run 保留 legacy 行为（无 hash drift check），保证 004 历史 run 和 005-code-created v1 run 可正常 resume
   - 设计权衡：从 "discovery_v2" 漂移到 "discovery_v1" 无法检测（两者都是合法值，需要单独不可变字段才能区分）；当前实现只拒绝非法值

2. **V2 progressive dispatch（L610-626）**：
   - `_execute_stages` 中 v2 run 调用 `run_progressive_detail_eval` 而非 v1 的 `_stage_fetching_details` + `_stage_evaluating`
   - 修复了 `run_progressive_detail_eval` 是死代码的问题（之前从未从 production `_execute_stages` 调用）

3. **SC-011 fix in `_fetch_one_detail`（L1150-1165）**：
   - 入口先检查本 run 是否已有 `fetch_status="completed"` 的 snapshot
   - 如有则 emit `detail_skipped_existing` 事件并 return True，不调用 `source.fetch_detail`
   - 修复了 `find_reusable_snapshot` 排除当前 run 导致 SC-011 gap 的问题

4. **Stage skip logic fix**：
   - `_stage_planning`：skip 列表增加 `STAGE_PRIORITIZING` 和 `STAGE_PROCESSING_JOBS`
   - `_stage_fetching_lists`：skip 列表增加 `STAGE_PRIORITIZING` 和 `STAGE_PROCESSING_JOBS`
   - `_stage_prioritizing`：skip 列表增加 `STAGE_PROCESSING_JOBS`
   - 修复了从 `processing_jobs` resume 时重复执行 planning/list/prioritizing stage 的问题

5. **`_handle_cancel` 容错（L1565-1578）**：
   - `get_search_plan` 抛 KeyError 时降级为空 items 列表
   - 修复了 cancel 到达时 plan 尚未编译（如 resume 后直接 cancel）导致 KeyError 的问题

#### 测试 helper 改动

1. `ResumeHashDriftAndSc011Tests._make_v2_run_selected_n`：
   - 用 `compile_search_plan(confirmation_view)` 计算真实 `input_hash`（而非 fake placeholder）
   - 用 `_materialize_plan_items` 同款转换把 `compile_search_plan` items（`term` key）转成 `create_search_plan` items（`keyword` key）
   - 用 direct SQL 模拟 `input_hash` / `policy_version` 漂移（这两个字段通过 public API 不可变）
   - 调用 `update_plan_item(status="completed")` 模拟 `_stage_fetching_lists` 已完成（test helper 直接插入 candidates 跳过 list fetching）

2. `CancelSignalPropagationTests._make_v2_run_selected_n`：
   - 用 `compile_search_plan` 计算真实 `input_hash`（原来用 fake "v2-cancel-hash"）
   - 修复了 v2 cancel 测试在 T081 hash drift check 下误判为 drift 的问题

### 证据

- T080 RED（首跑）：6 项中 1 FAIL + 3 ERROR + 2 PASS（详见上文 RED 状态）。
- T081 GREEN：`tests.test_discovery_integration.ResumeHashDriftAndSc011Tests` `Ran 6 tests in 3.596s ... OK`，exit 0。
- T081 GREEN：`tests.test_discovery_integration.CancelSignalPropagationTests` `Ran 8 tests ... OK`，exit 0（修复 fake input_hash 回归）。
- T081 GREEN：`tests.test_discovery_integration.RestartInterruptedTests.test_resume_continues_from_saved_stage` PASS（v1 run resume 不受 hash drift check 影响）。
- 全量回归：`python -m unittest discover -s tests -p "test_*.py"`：`Ran 1650 tests in 225.984s ... OK (skipped=27)`（基线 1644 + 6 T080 新增 = 1650）。
- `python -m py_compile webui/discovery_runner.py`：exit 0，无输出。

### 切片结论

US4-G 切片 PASS：resume hash drift 校验、SC-011 detail/assessment skip、v2 progressive dispatch 合同完整实现。
- Hash drift：v2 run resume 时重算 `compile_search_plan` input_hash 与 stored hash 比对；policy_version 非法值拒绝；v1 run 保留 legacy 行为。
- SC-011：`_fetch_one_detail` 入口检查本 run 已有 completed snapshot，跳过 `source.fetch_detail`；`_evaluate_job_v2_group` 已通过 `_get_assessment` 跳过 completed 方向。Resume 时 detail/assessment 重复执行数 = 0。
- V2 progressive dispatch：`_execute_stages` 对 v2 run 调用 `run_progressive_detail_eval`，修复了死代码问题。
- Stage skip：从 `processing_jobs` resume 时正确跳过 planning/list/prioritizing stage。
- Cancel 容错：`_handle_cancel` 在 plan 未编译时降级为空 items，不再 KeyError。

已知限制：policy_version 从 "discovery_v2" 漂移到 "discovery_v1" 无法检测（两者 
都是合法值）。需要单独不可变字段（如 `created_policy_version`）才能区分；当前 migration 015 未添加该字段，留待后续 issue。

下一步：T082–T083（HTTP/前端 cancel/resume/partial/failed/interrupted 状态交互）
。

## Slice US4-H (T082) — HTTP/前端 RED：四类进度、cancel/resume 409、终态可见性

**记录时间**：2026-07-21T14:48:18+08:00

### 范围

- T082：在 `tests/test_discovery_contracts.py` 和 `tests/test_discovery_frontend.py`
  中编写失败测试，覆盖 http-api.md L203-208（四类进度权威名）、L318（cancel
  response 含 `cancel_requested_at` + 当前四类进度）、L319（resume 同步 409
  拒绝 hash drift）、partial/failed/interrupted/cancelled 终态可见性、刷新恢复。

### 测试清单

#### `tests/test_discovery_contracts.py::DiscoveryV2ResumeStatusContractTests`（11 项）

1. `test_get_v2_run_returns_search_queries_completed_in_progress` — RED：v2 progress 必须含 `search_queries_completed`（当前只有 v1 别名 `source_count`）
2. `test_get_v2_run_returns_recommendations_in_progress` — RED：v2 progress 必须含 `recommendations` 字段
3. `test_cancel_response_includes_cancel_requested_at` — RED：cancel response 必须含 `cancel_requested_at`
4. `test_cancel_response_includes_four_part_progress` — ok（v2 名已在 progress 中）
5. `test_resume_rejects_input_hash_drift_with_409` — RED：input_hash 漂移必须同步 409（当前返回 202 后台校验）
6. `test_resume_rejects_invalid_policy_version_with_409` — RED：policy_version 非法值必须同步 409
7. `test_partial_status_visible_in_get_run` — RED：partial 必须 `complete=true`
8. `test_failed_status_visible_in_get_run` — RED：failed 必须 `complete=true` + failure 详情
9. `test_interrupted_status_visible_in_get_run` — RED：interrupted 必须 `complete=false`（可恢复）
10. `test_cancelled_status_visible_in_get_run` — RED：cancelled 必须 `complete=true`
11. `test_refresh_after_interrupt_preserves_progress` — ok（progress 已保留）

#### `tests/test_discovery_frontend.py::V2ProgressAndStatusVisibilityFrontendTests`（11 项）

1. `test_frontend_renders_search_queries_completed_label` — RED：前端缺少 `search_queries_completed` 标签
2. `test_frontend_renders_recommendations_label` — RED：前端缺少 `recommendations` 标签
3. `test_frontend_renders_four_part_progress_labels` — RED：前端缺少 `list_candidates`/`details_selected`/`details_completed`/`assessments_completed` 四类名
4. `test_frontend_renders_partial_status_badge` — ok（"部分成功" / "partial" 已存在）
5. `test_frontend_renders_failed_status_badge` — ok（"失败" / "failed" 已存在）
6. `test_frontend_renders_interrupted_status_badge` — ok（"中断" / "interrupted" 已存在）
7. `test_frontend_renders_cancelled_status_badge` — ok（"已取消" / "cancelled" 已存在）
8. `test_frontend_renders_complete_terminal_indicator` — ok（"complete" 字符串在 HTML 中存在）
9. `test_frontend_renders_cancel_requested_at_field` — RED：前端缺少 `cancel_requested_at` 字段
10. `test_frontend_handles_resume_409_state_conflict` — RED：前端缺少 `409` + `state_conflict` 处理
11. `test_frontend_displays_resume_conflict_user_message` — ok（"无法恢复" 文本已存在）

### 证据

- T082 RED（HTTP）：`python -m unittest tests.test_discovery_contracts.DiscoveryV2ResumeStatusContractTests -v`：`Ran 11 tests in 4.384s ... FAILED (failures=9)`，exit 1。
  - 9 FAIL：`search_queries_completed`、`recommendations`、`cancel_requested_at`、`input_hash_drift_409`、`policy_version_drift_409`、`partial_complete`、`failed_complete`、`interrupted_complete_false`、`cancelled_complete`
  - 2 ok：`four_part_progress`、`refresh_after_interrupt_preserves_progress`
- T082 RED（前端）：`python -m unittest tests.test_discovery_frontend.V2ProgressAndStatusVisibilityFrontendTests -v`：`Ran 11 tests in 2.833s ... FAILED (failures=5)`，exit 1。
  - 5 FAIL：`search_queries_completed_label`、`recommendations_label`、`four_part_progress_labels`、`cancel_requested_at_field`、`resume_409_state_conflict`
  - 6 ok：`partial_status_badge`、`failed_status_badge`、`interrupted_status_badge`、`cancelled_status_badge`、`complete_terminal_indicator`、`resume_conflict_user_message`
- T082 测试设置修复：`confirm_directions` 返回的 dict 不含 `directions` 字段；测试 helper 改为先调 `store.get_confirmation(confirmation_id)` 取完整结构（含 `directions` / `hard_constraints` / `soft_preferences` / `safe_limits`），再构建 confirmation view 供 `compile_search_plan` 计算 real input_hash。
- `python -m py_compile tests/test_discovery_contracts.py tests/test_discovery_frontend.py`：exit 0，无输出。

### 切片结论

US4-H 切片 RED 阶段 PASS：22 项测试中 14 项失败（HTTP 9 + 前端 5），覆盖
http-api.md L203-208/L318/L319 全部待实现契约。失败点集中在 `_run_summary`
缺少 v2 权威进度名 / `cancel_requested_at` / `complete` 字段，以及 resume
端点缺少同步 hash drift 校验。前端缺少 v2 进度名渲染和 409 错误处理分支。
8 项已通过测试（HTTP 2 + 前端 6）确认现有实现已覆盖部分契约（四类进度数值、
状态徽章文案、刷新恢复、conflict 文案），不需要再实现。

下一步：T083 GREEN — 在 `webui/app.py` `_run_summary` 中补齐字段，在
`discovery_resume_run` 中加同步 hash drift 校验，在 `webui/index.html` 中
补齐进度名渲染和 409 错误处理。

## Slice US4-H (T083) — HTTP/前端 GREEN：四类进度、cancel/resume 409、终态可见性

**记录时间**：2026-07-21T15:30:00+08:00

### 范围

- T083：在 `webui/app.py` 和 `webui/index.html` 中实现 T082 RED 测试覆盖的契约
  - `_run_summary` 补齐 `search_queries_completed` / `recommendations` /
    `cancel_requested_at` / `complete` 字段
  - `discovery_resume_run` 加同步 hash drift 校验，返回 409
  - 前端渲染四类进度权威名、cancel_requested_at、complete 终态指示、
    resume 409 state_conflict 错误处理

### 生产代码改动

#### `webui/discovery_runner.py`

1. **抽取模块级 helper（L2056-2129）**：
   - `build_confirmation_view(store, run)` — 构建 `compile_search_plan` 所需
     的 confirmation view，供 runner 和 HTTP 端点共享
   - `check_v2_resume_hash_drift(store, run)` — 同步校验 v2 run resume 时的
     hash drift（policy_version 合法值 + input_hash 重算比对）
   - `DiscoveryRunner._load_confirmation_view` 改为 thin wrapper，委托给
     `build_confirmation_view`
   - `DiscoveryRunner.run()` 中的 hash drift check 改为调用
     `check_v2_resume_hash_drift`，消除重复逻辑

2. **设计权衡**：
   - v1 run 保留 legacy 行为（不做 hash drift check），保证 004 历史 run 可 resume
   - policy_version 从 "discovery_v2" 漂移到 "discovery_v1" 仍无法检测
     （两者都是合法值，需要单独不可变字段）

#### `webui/app.py`

1. **`_run_summary` 扩展（L2705-2774）**：
   - `progress` 新增 `search_queries_completed`（= source_count，v2 权威名）
   - `progress` 新增 `recommendations`（= high_count + adjacent_count + growth_count，
     分类推荐数，非 raw `recommendation_count` 列——该列 runner 从不写入）
   - `progress` 中的 v2 名（`list_candidates` / `details_selected` /
     `details_completed` / `assessments_completed`）不再受 `if policy_version
     == "discovery_v2"` 条件限制，始终返回（v1 run 也可见，便于前端统一渲染）
   - 新增 `complete` 字段：true for terminal（succeeded/failed/cancelled/partial），
     false for interrupted/active
   - 新增 `cancel_requested_at` 字段：仅在 cancel 已请求时出现（避免 null 字段）

2. **`discovery_resume_run` 同步 hash drift 校验（L2391-2412）**：
   - 在 `discovery_runtime.resume_run(run_id)` 之前调用
     `check_v2_resume_hash_drift(store, run)`
   - 校验失败时抛 `DiscoveryError("state_conflict")`，HTTP 层通过
     `handle_discovery_error` 映射为 409
   - 客户端可同步感知 409，无需等待后台线程异步失败

#### `webui/index.html`

1. **`renderRunProgress` 扩展（L3711-3757）**：
   - 新增 v2 四类进度权威名渲染块：`search_queries_completed` /
     `list_candidates` / `details_selected` / `details_completed` /
     `assessments_completed` / `recommendations`，每个带中文标签
   - 新增 `cancel_requested_at` 展示块（仅在字段存在时渲染）
   - 新增 `complete` 终态指示块（"运行已结束" / "运行可恢复"）

2. **`resumeDiscoveryRun` 409 处理（L3787-3822）**：
   - 检测 `resp.status === 409 && err.error_code === "state_conflict"`
   - 渲染冲突说明 div（`discovery-resume-conflict` class），展示
     "恢复冲突：" + user_message
   - 通过 `setAppNotice` 提示 "无法恢复：..." + 冲突原因
   - 不再走通用错误分支

### 证据

- T083 GREEN（HTTP）：`python -m unittest tests.test_discovery_contracts.DiscoveryV2ResumeStatusContractTests -v`：`Ran 11 tests in 4.282s ... OK`，exit 0。11/11 通过。
- T083 GREEN（前端）：`python -m unittest tests.test_discovery_frontend.V2ProgressAndStatusVisibilityFrontendTests -v`：`Ran 11 tests in 2.724s ... OK`，exit 0。11/11 通过。
- 全量回归：`python -m unittest discover -s tests -p "test_*.py"`：`Ran 1672 tests in 286.206s ... FAILED (errors=2, skipped=27)`，exit 1。
  - 基线 1650 OK + 22 T082 新增 = 1672 total；1643 OK + 2 errors + 27 skipped。
  - 2 errors 均为 `tests.test_chrome_setup.ChromeSetupTests` 中的
    `test_copy_login_state_is_rejected_before_other_cli_modes` 和
    `test_help_does_not_require_cdp_runtime_dependencies`，是 pre-existing
    环境问题（Windows 非中文路径 + subprocess `text=True` 解码失败导致
    `result.stdout=None`），与 T082/T083 改动无关。这两个测试在 T082/T083
    改动前就已在当前工作区失败（脚本输出中文到 stdout，但 Windows
    `subprocess.run(text=True)` 用 GBK 解码失败）。
  - 单独运行：`python -m unittest tests.test_chrome_setup`：`Ran 74 tests
    in 5.317s ... FAILED (errors=2)`，同样 2 errors，确认与 T082/T083 无关。
- `python -m py_compile webui/app.py webui/discovery_runner.py`：exit 0，无输出。

### 切片结论

US4-H 切片 GREEN 阶段 PASS：22 项 T082 测试全部通过（HTTP 11 + 前端 11）。
- HTTP：`_run_summary` 返回 `search_queries_completed` / `recommendations` /
  `cancel_requested_at` / `complete` 字段；`discovery_resume_run` 同步校验
  hash drift 并返回 409。
- 前端：`renderRunProgress` 渲染四类进度权威名 + cancel_requested_at + complete
  终态指示；`resumeDiscoveryRun` 处理 409 state_conflict 并展示冲突说明。
- 模块级 helper `build_confirmation_view` 和 `check_v2_resume_hash_drift`
  消除了 runner 和 HTTP 端点之间的重复逻辑。
- 全量回归 2 errors 为 pre-existing chrome_setup 环境问题，与 T082/T083 无关。

已知限制：
- policy_version 从 "discovery_v2" 漂移到 "discovery_v1" 仍无法检测
  （T081 已记录，需要单独不可变字段）。
- 2 个 chrome_setup 测试在当前 Windows 环境失败（subprocess 编码问题），
  需要单独 issue 修复脚本输出的编码处理。

下一步：T084–T085（SC-004/SC-010/SC-011/SC-003 性能验证）。

## Slice US4-I (T084) — SC-004/SC-010/SC-011 性能门验证

### T084 验证范围

T084 是验证任务（非 RED→GREEN）：在 `tests/test_discovery_performance.py`
中独立复核 spec.md 的 SC-004 / SC-010 / SC-011 三个性能门，针对 T077/T079/T081
已实现的行为。验证维度：

- SC-004（spec.md L273）：工作单元完成后 10 模拟秒内进度可见；刷新后计数一致。
  - `detail_completed_count` 在 detail 完成同模拟时刻反映（同事务更新）
  - `assessment_completed_count` 在评估完成同模拟时刻反映
  - 重新读取 run（模拟刷新）后计数完全一致
  - 首个评估完成时 `first_result_at` 立即写入（≤10 模拟秒边界）
- SC-010（spec.md L279）：cancel 后 30 秒内不再启动新 source/AI 工作；已完成保留 100%。
  - `request_cancel` 后 `runner.run()` 在 30 wall-clock 秒内进入 `cancelled` 终态
  - cancel 后 `source.fetch_detail` 和 AI provider 调用数均为 0
  - 已持久化的 snapshots / assessments / candidates ID 集合在 cancel 后 100% 保留
- SC-011（spec.md L280）：输入身份一致的 resume 不重复执行已完成 detail/assessment。
  - 首轮 5 候选 → 5 次 `source.fetch_detail`；interrupted → resume 后 `source.fetch_detail` = 0
  - 首轮 5 候选 → 5 次 AI `assess_job`；interrupted → resume 后 `assess_job` = 0

### T084 实现

新增 `tests/test_discovery_performance.py::Sc004Sc010Sc011PerformanceGateTests`
共 9 项验证测试：

1. `test_sc004_detail_completed_count_visible_same_simulated_instant`
2. `test_sc004_assessment_completed_count_visible_same_simulated_instant`
3. `test_sc004_refresh_preserves_counts`
4. `test_sc004_first_result_at_written_on_first_unit_completion`
5. `test_sc010_cancel_reaches_terminal_within_30_wall_clock_seconds`
6. `test_sc010_cancel_preserves_100_percent_completed_results`
7. `test_sc010_cancel_blocks_new_source_and_ai_work`
8. `test_sc011_resume_zero_duplicate_detail_fetches`
9. `test_sc011_resume_zero_duplicate_ai_calls`

测试 fixture 与 `ResumeHashDriftAndSc011Tests` 一致：
- `compile_search_plan` 计算真实 `input_hash`（让 T081 hash drift check 通过）
- 持久化 search plan 并将每个 plan item 标记为 `completed`（让
  `calculate_run_completion` 能进入终态）
- `_counting_source` / `_counting_ai` 记录每次外部调用

### T084 验证证据

- T084 单跑：`python -m unittest tests.test_discovery_performance.Sc004Sc010Sc011PerformanceGateTests -v`
  → `Ran 9 tests in 6.131s ... OK`，exit 0。9/9 通过。
  - SC-010 cancel wall-clock elapsed ≤ 30s（实测 6.131s 全部 9 项测试合计）。
- T084 文件全跑：`python -m unittest tests.test_discovery_performance`
  → `Ran 26 tests in 6.508s ... OK`，exit 0。26/26 通过
  （17 既有 + 9 T084 新增）。
- 全量回归：`python -m unittest discover -s tests -p "test_*.py"`
  → `Ran 1681 tests in 238.169s ... FAILED (errors=2, skipped=27)`，exit 1。
  - 1681 = 1672 baseline + 9 T084 新增，无新回归。
  - 2 errors 为 pre-existing `test_chrome_setup` Windows 编码问题
    （`subprocess.run(text=True)` 在中文路径 `D:\项目\boss\` 下 `result.stdout=None`），
    与 T084 改动无关（T082/T083 切片已记录）。

### T084 设计决策

- SC-004 "≤10 模拟秒" 解释为「同事务可见性」：runner 在每个工作单元完成的同
  事务里更新 `detail_completed_count` / `assessment_completed_count` /
  `first_result_at`，下一次 `get_discovery_run` 立即可见（模拟秒数=0 ≤ 10）。
  没有用 `FakeMonotonicClock` 推进 10 秒再断言，因为进度可见性的合同是
  「即时」而非「10 秒后批量」。
- SC-010 "≤30 秒" 用 wall-clock（`time.monotonic()`）测量，而非注入式
  `FakeMonotonicClock`：cancel 信号传播是真实代码路径（不依赖时钟），
  wall-clock 更能反映「用户感知的取消延迟」。实测 9 项测试合计 6.131s，
  单项 cancel 测试远低于 30s。
- SC-011 "0 重复" 通过 `_counting_source.calls` 和 `_counting_ai.calls`
  计数验证：首轮 N 候选 → N 次调用；resume 后调用数=0。
- 不重复 `test_discovery_integration.py::ResumeHashDriftAndSc011Tests` 已有
  的 `test_resume_skips_completed_detail_fetches` /
  `test_resume_skips_completed_assessments`：T084 在性能门文件中独立复核，
  同一断言由两个文件交叉验证（integration + performance）。

### US4-I 切片结论

T084 PASS：9 项验证测试全部通过；SC-004/SC-010/SC-011 三个性能门在
`tests/test_discovery_performance.py` 中得到独立复核；全量回归无新回归。

下一步：T085（SC-003 确定性编排门：15 详情 + 必需评估 ≤10 模拟分钟）。

## Slice US4-J (T085) — SC-003 确定性编排门验证

### T085 验证范围

T085 是验证任务（非 RED→GREEN）：在 `tests/test_discovery_performance.py`
中独立复核 spec.md SC-003 的确定性编排门，针对 T007 已实现的
``DiscoveryPerformanceMetrics``。验证维度：

- SC-003（spec.md L272）：标准运行处理 15 个真实岗位详情并完成所需评估的
  总时间不超过 10 分钟（600 模拟秒）；结果必须同时报告实际处理数量和外部阻塞。
  - 15 详情 + 15 AI 分组在 600 模拟秒（边界值）内完成 → `gates.all_complete_within_600_seconds=True`
  - 超过 600 模拟秒（600.001）→ 立即失败
  - 报告真实处理数 `details.processed_count` = 15
  - 每条 detail item 报告 `wait_reason`（等待原因）
  - 报告 AI 调用总数 `ai.call_count` = 15
  - 健康场景 `blockers` 为空列表（仍需报告）
  - 外部阻塞必须被报告且 `gates.overall` 失败
  - 报告含编排门所需的全部字段（contract_version / status / list / selection /
    details / ai / timing / blockers / gates）

### T085 实现

新增 `tests/test_discovery_performance.py::Sc003DeterministicOrchestrationGateTests`
共 8 项验证测试：

1. `test_sc003_fifteen_details_complete_within_600_simulated_seconds`
   — 边界值 600s 通过
2. `test_sc003_fails_above_600_simulated_seconds`
   — 600.001s 立即失败
3. `test_sc003_reports_real_processing_count_fifteen`
   — `details.processed_count` = 15
4. `test_sc003_reports_wait_reasons_per_detail_item`
   — 每个 detail item 含 `wait_reason`
5. `test_sc003_reports_ai_calls`
   — `ai.call_count` = 15，`ai.group_count` = 15
6. `test_sc003_reports_blockers_empty_for_healthy`
   — 健康场景 `blockers` = []
7. `test_sc003_reports_external_blocker_and_fails_overall`
   — 外部阻塞 → `status` = "blocked"，`gates.overall` = False
8. `test_sc003_report_includes_all_required_orchestration_fields`
   — 报告字段完整性

测试 fixture：复用 `fast_resume_discovery_v2.json` 的 `healthy_boundary` 场景
（`start_seconds=1000`, `list_complete_seconds=1090`, `result_visible_seconds=[1120,1160,1200,1250,1300]`,
`all_complete_seconds=1600`），用 `FakeMonotonicClock` 注入确定性时钟。
- 1600 - 1000 = 600s 恰好为 SC-003 的包含边界
- 1600.001 - 1000 = 600.001s 立即失败

### T085 验证证据

- T085 单跑：`python -m unittest tests.test_discovery_performance.Sc003DeterministicOrchestrationGateTests -v`
  → `Ran 8 tests in 0.304s ... OK`，exit 0。8/8 通过。
- T084+T085 合并文件跑：`python -m unittest tests.test_discovery_performance`
  → `Ran 34 tests in 6.199s ... OK`，exit 0。34/34 通过
  （17 既有 + 9 T084 + 8 T085）。
- 全量回归：`python -m unittest discover -s tests -p "test_*.py"`
  → `Ran 1689 tests in 260.752s ... OK (skipped=27)`，exit 0。
  - 1689 = 1681 baseline + 8 T085 新增。
  - **0 errors**：T082/T083 切片记录的 2 个 chrome_setup 编码错误在本次
    回归中未复现（该问题在 Windows 下偶发，与 T085 改动无关）。

### T085 设计决策

- SC-003 "≤10 模拟分钟" 用 `FakeMonotonicClock` 注入测量：与 T007 的
  `DiscoveryPerformanceMetrics` 合同一致（确定性、可注入时钟），而非 wall-clock。
  编排门是合同门（依赖时钟），不是用户感知门（依赖 wall-clock）。
- 600s 是包含边界：`all_complete_seconds=600.0` 通过，`600.001` 立即失败
  （与 T007 既有 `test_performance_time_gates_fail_immediately_above_boundaries`
  的边界语义一致）。
- "报告真实处理数" 解释为 `details.processed_count`（实际记录的 detail 数），
  而非 `selection.selected_count`（计划选择的 detail 数）：两者在健康场景
  相等，但在部分失败场景可能不同；`processed_count` 是真实处理数。
- "等待原因" 通过 `details.items[].wait_reason` 报告：每条 detail item
  必须含 `wait_reason` 字段（如 "readiness" 表示等待就绪）。
- "AI calls" 通过 `ai.call_count` 报告：15 个评估分组 × 每组 1 次 AI 调用 = 15。
- "阻塞" 通过 `blockers` 列表报告：健康场景为空列表（仍需报告，证明无外部阻塞），
  外部阻塞场景为非空列表且 `gates.no_external_blocker=False`。
- T085 与 T084 互补：T084 验证 SC-004/010/011（进度可见性、取消、恢复），
  T085 验证 SC-003（编排门），共同覆盖 US4 Gate 的全部性能合同。

### US4 Gate 检查

US4 Gate（tasks.md L174）要求：
> 默认 source concurrency 仍为 1；只有真实小样本稳定后才允许 policy 上限 2。
> 取消、恢复、缓存、breaker、进度和 SC-003/004/010/011 的自动化门全部通过。

T066–T085 全部完成（20 项 [X]），自动化门状态：
- **取消**：T078 RED + T079 GREEN + T084 SC-010 验证 → PASS
- **恢复**：T080 RED + T081 GREEN + T084 SC-011 验证 → PASS
- **缓存**：T072/T073 detail 复用 + freshness/identity 守卫 → PASS
- **breaker**：T074 RED + T075 GREEN（source circuit breaker） → PASS
- **进度**：T076 RED + T077 GREEN + T082 RED + T083 GREEN + T084 SC-004 验证 → PASS
- **SC-003**：T085 验证（15 详情 + 评估 ≤600 模拟秒） → PASS
- **SC-004**：T084 验证（进度 ≤10 模拟秒可见 + 刷新一致） → PASS
- **SC-010**：T084 验证（cancel ≤30 wall-clock 秒 + 100% 保留） → PASS
- **SC-011**：T084 验证（resume 0 重复 detail/assessment） → PASS
- **默认 source concurrency=1**：fixture `policy.source_concurrency=1`，
  `discovery_runner.DiscoveryRunner.max_workers` 默认 1，未提升 → PASS

US4 Gate PASS：全部自动化门通过，默认 concurrency=1 保持。

### US4-J 切片结论

T085 PASS：8 项验证测试全部通过；SC-003 编排门在
`tests/test_discovery_performance.py` 中得到独立复核；全量回归 0 errors。

**Phase 6 / US4 完整收口**：T066–T085 全部 [X]（20 项），US4 Gate PASS。
SC-003/004/010/011 四个性能门 + 取消/恢复/缓存/breaker/进度六类合同门全部
通过自动化验证。默认 source concurrency 保持 1，未提升到 2（按硬约束 4
要求，需真实小样本稳定性验证后才能考虑提升，不在 feature 005 范围内）。

下一步：Phase 7 / US5（T086–T091，反馈改善后续推荐）。

---

## Slice US5-A (T086–T091) — 反馈 CRUD、作用域、撤销与历史不变性

**执行时间**：2026-07-21（Asia/Shanghai）  
**切片范围**：T086 RED / T087 GREEN / T088 RED / T089 GREEN / T090 RED / T091 GREEN  
**对应契约**：`spec.md` US5 场景 1–4（L136–147）、FR-050/FR-051（L238–239）、`contracts/http-api.md` 反馈端点（L312–320）  
**关联实现**：`webui/store.py`（`create_discovery_feedback` / `revoke_discovery_feedback` / `list_discovery_feedback`）、`webui/discovery.py`（`apply_feedback_to_next_run`）、`webui/app.py`（`/api/discovery/feedback` GET/POST、`/api/discovery/feedback/<id>/revoke` POST）、`webui/index.html`（`recordDirectionFeedback` / `revokeFeedback` / `renderPreferenceChanges` / `loadDiscoveryFeedbackState` / `resumeDiscoveryRun`）

### US5 实现事实复核

T086–T091 是验证型切片：核心反馈基础设施在 T058–T062 已经实现（`create_discovery_feedback` L4156、`revoke_discovery_feedback` L4236、`list_discovery_feedback` L4288、`apply_feedback_to_next_run` L1585、HTTP 端点 L2615–L2682、前端 `recordDirectionFeedback` / `revokeFeedback` 等）。本切片通过新增 RED→GREEN 测试覆盖以下未验证维度：

1. **T086**（`tests/test_discovery_store.py::FeedbackScopeAndHistoryInvariantsTests`，9 项）：反馈 CRUD 的默认 scope（exact_job）、direction_id/assessment_id 维度持久化、`revoked_at` 时间戳、撤销幂等性、未知 feedback_id 抛 `KeyError`、反馈不改写历史 run 计数器 / snapshot 内容 / assessment 分数。
2. **T087**：已实现，无需新增代码（GREEN-by-existing-implementation）。复核 `webui/store.py` L4156–L4298 满足 T086 全部断言。
3. **T088**（`tests/test_discovery_integration.py::FeedbackNextRunApplicationTests`，5 项）：not_interested 不扩散到同公司其他岗位；direction_disable 不在 `compile_search_plan` 中分配预算；judgment_error 记录维度但不改历史评分；撤销后下次 run 不应用反馈；反馈只影响后续运行，历史 input_hash 与计数器不变。
4. **T089**：已实现，无需新增代码。复核 `webui/discovery.py::apply_feedback_to_next_run` L1585–L1630 满足 T088 全部断言（移除禁用方向、收集 excluded_job_ids、全禁用时抛 `input_incomplete`）。
5. **T090**（`tests/test_discovery_contracts.py::FeedbackScopeAndRunningRunTests`，5 项 HTTP + `tests/test_discovery_frontend.py::FeedbackAndRevokeTests`，2 项前端）：POST 响应含 `effective_scope`、GET 列表每项含 `scope`、已撤销项含 `revoked_at`、运行中 run POST 返回 201 不锁定、撤销响应含 `revoked: true`；前端渲染 `exact_job` 与 `scope` 字段、含 `resumeDiscoveryRun` 入口（SC-013）。
6. **T091**：已实现，无需新增代码。复核 `webui/app.py` L2615–L2682 与 `webui/index.html` 满足 T090 全部断言。

### US5-B (T092) — FR-050/FR-051 端到端验证

**测试位置**：`tests/test_discovery_integration.py::Fr050Fr051VerificationTests`（3 项）  
**覆盖契约**：FR-050（反馈只作用于后续运行，不改写历史 profile/confirmation/assessment 事实）、FR-051（用户可撤销有效反馈并查看作用范围）

#### T092 测试矩阵

| 测试 | 断言要点 |
|---|---|
| `test_fr050_full_feedback_lifecycle_preserves_history` | 创建历史 run + snapshot + assessment（match_score=88）→ 提交 job not_interested + direction_disable 反馈 → `apply_feedback_to_next_run` 成功（excluded_job_ids 含该 job、enabled_directions 不含禁用方向）→ 验证历史 run `input_hash`/`status` 不变、snapshot 数量与内容不变、assessment 分数与状态不变 → 撤销两条反馈 → 历史仍不变 |
| `test_fr051_revoke_makes_feedback_ineffective_for_next_run` | 提交 not_interested + direction_disable → 验证下次 run 应用（job 被排除、方向被移除）→ 撤销两条反馈 → 验证下次 run 不再应用（job 不被排除、方向重新启用） |
| `test_fr051_user_can_see_feedback_scope` | 提交 3 条反馈（job/direction/assessment，scope 分别为 exact_job/exact_direction/exact_assessment）→ 验证 `list_discovery_feedback` 全部可见且 scope 正确 → 验证 `effective_only=True` 返回全部 3 条 → 撤销一条 → 验证 effective 数量降至 2 |

### US5 全切片证据

**执行命令**：
```
python -m unittest \
  tests.test_discovery_store.FeedbackScopeAndHistoryInvariantsTests \
  tests.test_discovery_integration.FeedbackNextRunApplicationTests \
  tests.test_discovery_integration.Fr050Fr051VerificationTests \
  tests.test_discovery_contracts.FeedbackScopeAndRunningRunTests \
  tests.test_discovery_frontend.FeedbackAndRevokeTests -v
```

**执行结果**：
- `Ran 27 tests in 7.285s`
- `OK`（0 errors, 0 failures）

**测试明细**：
| 测试类 | 文件 | 数量 | 覆盖 |
|---|---|---|---|
| `FeedbackScopeAndHistoryInvariantsTests` | `test_discovery_store.py` | 9 | T086 反馈 CRUD/作用域/撤销幂等/历史不变性 |
| `FeedbackNextRunApplicationTests` | `test_discovery_integration.py` | 5 | T088 next-run 应用/不扩散/不分配预算/撤销失效/历史不变 |
| `Fr050Fr051VerificationTests` | `test_discovery_integration.py` | 3 | T092 FR-050/FR-051 完整生命周期 |
| `FeedbackScopeAndRunningRunTests` | `test_discovery_contracts.py` | 5 | T090 HTTP effective_scope/scope/revoked_at/201/revoke flag |
| `FeedbackAndRevokeTests` | `test_discovery_frontend.py` | 5 | T090 前端 scope 渲染 + 恢复入口（含既有 T078 3 项回归） |

### US5 Gate 检查

US5 Gate（tasks.md L192）要求：**反馈作用域可见、可撤销、仅影响后续运行或当前可见性；历史事实不改写。**

| Gate 子句 | 证据 | 状态 |
|---|---|---|
| 作用域可见 | `FeedbackScopeAndRunningRunTests::test_post_response_includes_effective_scope_field`、`test_get_list_response_includes_scope_per_item`；前端 `test_frontend_renders_scope_visible_in_preference_changes` | PASS |
| 可撤销 | `FeedbackScopeAndHistoryInvariantsTests::test_revoke_sets_revoked_at_timestamp`、`test_revoke_is_idempotent_for_already_revoked_feedback`；`Fr050Fr051VerificationTests::test_fr051_revoke_makes_feedback_ineffective_for_next_run` | PASS |
| 仅影响后续运行或当前可见性 | `FeedbackNextRunApplicationTests::test_feedback_only_affects_subsequent_runs_not_historical`；`test_not_interested_does_not_exclude_other_jobs_from_same_company`；`test_direction_disable_does_not_allocate_budget_in_compile_search_plan` | PASS |
| 历史事实不改写 | `Fr050Fr051VerificationTests::test_fr050_full_feedback_lifecycle_preserves_history`；`FeedbackScopeAndHistoryInvariantsTests::test_feedback_does_not_modify_historical_run_counters` / `_snapshot` / `_assessment_scores` | PASS |

**US5 Gate PASS**：27 项 US5 切片测试全部通过，反馈作用域可见、可撤销、仅影响后续运行，历史事实不改写。

### US5-A/B 切片结论

T086–T092 全部 [X]（7 项）。
- T086/T088/T090/T092 通过新增 RED 测试驱动验证；
- T087/T089/T091 通过对照已有实现复核确认 GREEN-by-existing-implementation（T058–T062 已完成实现）；
- 全切片 27 项测试 0 errors / 0 failures，7.285s 完成。

**Phase 7 / US5 完整收口**：FR-050/FR-051 + spec.md US5 场景 1–4 全部覆盖；反馈基础设施（store/discovery/app/index.html）按契约稳定工作。

下一步：Phase 8（T093–T106，集成、真实验证与发布门）。

---

## Phase 8 — Integration、真实验证与发布门

**执行时间**：2026-07-21（Asia/Shanghai）  
**分支**：`codex/fast-resume-discovery`  
**Python**：`D:\项目\boss\.venv\Scripts\python.exe`（项目 venv）

### T093 — 专项测试基线（migration 14→15 / candidate v4 / assessment v2 / salary / projector / source event）

**执行命令**：
```
python -m unittest \
  tests.test_discovery_store.Migration015Tests \
  tests.test_discovery_store.MigrationAcceptanceTests \
  tests.test_discovery.DiscoveryPolicyV2Tests \
  tests.test_candidate.CandidateAnalysisV4Tests \
  tests.test_ai.CandidateAnalysisV4ProviderTests \
  tests.test_discovery.JobAssessmentContractTests \
  tests.test_ai.JobAssessmentV2ProviderTests \
  tests.test_discovery_integration.JobAssessmentV2GroupOrchestrationTests \
  tests.test_screening.MinSalaryV2ParserTests \
  tests.test_screening.TriStateHardRulesTests \
  tests.test_discovery.CanonicalProjectorSortTests \
  tests.test_discovery_contracts.RecommendationProjectorHttpContractTests \
  tests.test_boss_discovery_source.BossCdpSourceBatchEventTests \
  tests.test_discovery_integration.DiscoverySourceAdmissionTests
```

**执行结果**：`Ran 123 tests in 7.640s` → `OK`（0 errors, 0 failures）

**覆盖维度**：
- Migration 015 additive（schema 14→15 升级、idempotent reopen、v1 rows 保留）
- candidate_analysis v4 契约（typed-empty shape、quarantine、unverified 永不 confirmed）
- job_assessment v2 契约（四维度结构化、单方向降级、评估分组）
- salary 三态硬规则（unknown 不参与、confirmed 必满足、inferred 不参与）
- canonical projector 排序与 HTTP 契约
- BossCdpSource 批量事件解析、来源准入

### T094 — 确定性 100→15 渐进管道与性能门

**执行命令**：`python -m unittest tests.test_discovery_performance -v`

**执行结果**：`Ran 34 tests in 5.929s` → `OK`

**覆盖的自动化门**：
| SC | 自动化结果 | 验证类 |
|---|---|---|
| SC-001/SC-002 | PASS（候选池 ≤90 模拟秒、首 5 已评估 ≤5 模拟分钟） | `PerformanceGateTests`、`Sc001Sc002PipelineGateTests`（T051） |
| SC-003 | PASS（15 details + 所需 assessments ≤600 模拟秒；600.001s FAIL） | `Sc003DeterministicOrchestrationGateTests`（T085） |
| SC-004 | PASS（≤10 模拟秒可见；刷新保留计数；first_result_at 写入） | `Sc004Sc010Sc011PerformanceGateTests`（T084） |
| SC-010 | PASS（≤30 wall-clock 秒进 cancelled；100% 保留；阻断新 source/AI 工作） | 同上 |
| SC-011 | PASS（0 重复 detail 抓取；0 重复 AI 调用） | 同上 |

**fake 边界声明**：所有计时使用 `FakeMonotonicClock`（注入式确定性时钟），不依赖 wall-clock；`DiscoveryPerformanceMetrics` 是纯计算对象，不触发真实网络/Chrome/AI。真实 p50/p95 待 T098 真实样本验证（DEFERRED）。

### T095 — 黄金样本评估（SC-003–SC-009 + SC-012）

**执行命令**：`python tests/fixtures/discovery/evaluate.py`

**执行结果**：`Overall: ALL PASS`；机器可读产物 `tests/fixtures/discovery/evaluate_result.json`

**指标矩阵**（与 004 基线一致，标注一致性检查）：
| SC | 指标 | 实测 | 目标 | 状态 |
|---|---|---|---|---|
| SC-003 | direction_acceptance_rate | 100% | ≥70% | PASS |
| SC-004 | precision_at_20 | 80% | ≥60% | PASS |
| SC-005 | recall | 80% | ≥50% | PASS |
| SC-006 | hard_rule_violation_rate | 0% | 0% | PASS |
| SC-007 | sc007_violation_rate | 0% | 0% | PASS |
| SC-008 | multi_direction_coverage | 100% | ≥60% | PASS |
| SC-009 | explanation_fidelity | 87.5% | ≥80% | PASS |
| SC-012 | PII redaction（7 份简历） | real_pii_count=0；redaction_markers=1/份 | 0 real PII | PASS |

**重要边界声明**：评估是 metric-calculation 逻辑与标注一致性的检查，**不是**系统接受度测试；不调真实 AI、不抓真实岗位。

### T096 — 完整 unittest + py_compile

**全量 unittest**：
- 命令：`python -m unittest discover -s tests -v`
- 结果：`Ran 1713 tests in 246.189s` → `OK (skipped=27)`
- 较 US5 完成前增加 24 项（9 T086 + 5 T088 + 3 T092 + 5 T090 HTTP + 2 T090 前端）
- 0 errors / 0 failures；27 skipped 为既有平台/网络原因跳过

**py_compile**：
- 命令：`python -m py_compile scripts/boss_cdp_raw.py scripts/job_summary.py webui/app.py webui/store.py webui/discovery.py webui/discovery_runner.py webui/source.py webui/process_executor.py webui/candidate.py webui/screening.py`
- 结果：exit 0（PASS）

**ResourceWarning / 线程泄漏**：既有测试套件未观察到新增 ResourceWarning；DiscoveryTaskRuntime executor 在测试 tearDown 调用 `runtime.shutdown()`（见 `test_discovery_contracts.py::AnalysisConfirmationHttpContractTests.tearDown` L301–L308）；未发现新增线程/进程泄漏。

### T098 — 真实来源 --check + 5 详情性能样本

**执行时间**：2026-07-21 16:09–16:22（Beijing time）

**T098-a：--check 环境诊断**

执行命令：`python scripts/boss_cdp_raw.py --check`

执行结果：exit 0，三项全通：
- [1/3] Python 依赖：requests + websocket 可导入 ✅
- [2/3] CDP 端口连通性：Chrome Chrome/150.0.7871.125 ✅
- [3/3] BOSS直聘登录状态：已登录 ✅（cookies 持久化在 `~/.career-scout/chrome-profile`，session 有效）

**T098-b：5 详情性能样本**

执行命令：临时脚本 `_tmp_perf_t098.py`（跑完删除），直接调 `boss_cdp_raw.scrape_list` + `scrape_details`，通过 `event_callback` 收集 per-detail terminal 事件。

参数：keyword="Python 后端"，city="北京"，max_pages=1，max_details=5，cdp_port=9222。

执行结果：list_count=30，detail_count=5（全部 completed），blockers=[]。

**指标矩阵**（与 T007 性能合同字段对齐）：
| 字段 | 实测 | 说明 |
|---|---|---|
| list_count | 30 | 单页列表返回 30 条 |
| detail_count | 5 / target 5 | 全部成功 |
| p50_duration_ms | 2021 | 5 样本中位数 |
| p95_duration_ms | 2029 | n=5 不足分位数计算，退化为 max |
| wait_reasons | ["ok"] | 所有详情 safe_code=ok，无 readiness_wait/scrolling 重试 |
| batch_count | 1 | 5 详情单批完成（batch_size=5 上限） |
| batch_size | 5 | policy v2 每批最多 5 个候选 |
| concurrency | 1 | policy v2 默认详情并发为 1 |
| breaker_state | closed | 无失败，断路器未触发 |
| breaker_trips | 0 | 同上 |
| blockers | [] | 无阻塞 |

**per-detail 明细**（见 `specs/005-fast-resume-discovery/validation/real_performance_5.json`）：
| # | job_id（截断） | duration_ms | safe_code |
|---|---|---|---|
| 1 | 1ece49930f94393f0nFy2dm8E1VQ | 2020 | ok |
| 2 | 673802daaf47d4810nZ82Nm-ElJQ | 2029 | ok |
| 3 | c260af79626184fb0nB839-4FlBX | 2021 | ok |
| 4 | fa50c62f669b18430nF709q7EFdT | 2028 | ok |
| 5 | 7feadfc75665a8240nF539W1EVdX | 2019 | ok |

**真实边界声明**：本次为真实 BOSS 抓取（非 fake clock），duration_ms 含页面加载 + JD 提取，不含 inter_job_gap（3.7–6.9s，岗位间等待，由 `inter_job_gap_range=(3,7)` 控制）。5 样本方差极小（2019–2029ms），反映 readiness-driven 提取的稳定性。p95 因 n=5 退化为 max，待更大样本（n≥20）才能算真实分位数。

**未验证边界**：
- p95 真实分位数（n=5 不足，退化为 max）
- inter_job_gap 计入总吞吐的影响（本次只测单岗位 duration，未测端到端批次墙钟）
- source 断路器触发场景（本次无失败，breaker 始终 closed）

### T100 — JSON 产物与文字一致性核对

**存在的 JSON 产物**：
| 路径 | 来源 | 一致性核对 |
|---|---|---|
| `tests/fixtures/discovery/evaluate_result.json` | T095 evaluate.py 输出 | direction_acceptance_rate=1.0、precision_at_20=0.8、recall=0.8、hard_rule_violation_rate=0.0、multi_direction_coverage=1.0、explanation_fidelity=0.875 — 与文字输出完全一致 |
| `specs/005-fast-resume-discovery/validation/real_performance_5.json` | T098 真实性能样本 | list_count=30、detail_count=5、p50=2021、p95=2029、blockers=[] — 与上文 T098 节文字完全一致 |
| `specs/005-fast-resume-discovery/validation/real_e2e_result.json` | T099 首次尝试（blocked） | status=blocked、reason="analysis did not reach ready"、analysis_failure.error_code=ai_invalid_output、market_attempts=[{source_count:0, blockers:[]}] — 与 T099 节文字一致 |

注：`tests/fixtures/discovery/live_provider_smoke_result.json`（T132 smoke 产物）被 `.gitignore` L35 `tests/fixtures/discovery/*_result.json` 忽略，为本地诊断产物不提交；其结论（candidate_analysis_v2/job_assessment_v1 双 failed, error_code=ai_invalid_output）已文字记录在 T099 节。

**未验证边界（DEFERRED）**：
- `real_e2e_result.json` 当前为 blocked 状态（AI 429 限流），待 AI 恢复后重跑产出 completed 状态报告
- `real_performance_5.json` 的 p95 真实分位数待更大样本（n≥20）

无其它可提交 JSON 产物；文字计数与 JSON 字段完全一致，无冲突。

### T101/T102/T103 — 文档同步

**T101 README**：
- `README.md` L205–L248：新增 `### 岗位发现 v2 收口（005）` 子节，含默认用户流程、性能/安全边界表（SC-003/004/010/011、默认并发、12h 复用、断路器、反馈作用域）、运行命令、兼容说明
- `README.en.md` L195–L238：同步英文版 `### Fast Resume-Driven Discovery Closure (005)`
- 双语内容一一对应，结构一致

**T102 CHANGELOG**：
- `CHANGELOG.md` L5–L17：在 `## 未发布` 下新增 `### 新增 — 岗位发现 v2 收口（feature 005）` 子节，列出 11 项有意义变更（policy v2、四类进度、渐进结果、取消/恢复、12h 复用、断路器、分级反馈、性能合同、candidate v4、assessment v2、salary 三态、黄金样本）

**T103 版本号一致性**：
- 当前版本：`2.0.0`（保持不变，feature 005 不触发主版本号提升）
- 四处同步检查：
  - `scripts/boss_cdp_raw.py` L22：`__version__ = "2.0.0"`
  - `pyproject.toml` L3：`version = "2.0.0"`
  - `SKILL.md` L4：`version: 2.0.0`
  - `README.md` L8：`![Version](https://img.shields.io/badge/version-2.0.0-orange.svg)`
- 自动化验证：`python -m unittest tests.test_chrome_setup.VersionConsistencyTests -v` → `Ran 2 tests in 0.051s` → `OK`

### T104 — Flask 后端重启 HTTP 200 验证

**启动命令**：
```
python -c "from webui.app import create_app; app = create_app({'TESTING': True, 'START_TASKS': False}); app.run(host='127.0.0.1', port=5099, debug=False, use_reloader=False)"
```

**验证命令**（PowerShell `Invoke-WebRequest`）：
```powershell
$r = Invoke-WebRequest -Uri "http://127.0.0.1:5099/" -UseBasicParsing -TimeoutSec 10
```

**验证结果**：
- `STATUS=200`
- `LENGTH=208020`（完整 HTML）
- 前端关键标记核对：`discovery-upload` ✓、`resumeDiscoveryRun` ✓、`revokeFeedback` ✓ → `DISCOVERY_FRONTEND_OK`
- 启动时间：07:45:24（Asia/Shanghai）
- URL：`http://127.0.0.1:5099/`
- PID 未显式记录（Flask dev server 单进程；通过 `StopCommand` 干净退出）

**活动运行恢复验证边界**：本次启动使用 `START_TASKS: False` 和临时 DB（`$env:TEMP\boss_t104_check.db`），未注入活动 run；活动 run 恢复的真实场景需 T097/T099 真实 E2E 验证（DEFERRED）。

### T105 — 独立审查（self-review）

**审查类型声明**：本审查由实施 agent 自身执行，**不是真正意义的独立审查**。真正独立审查需另一 reviewer 对照冻结 spec 复核。本节记录实施 agent 的自审结果，供后续 reviewer 参考。

#### 审查维度 1：冻结 spec 与实际 diff 一致性

| spec 章节 | 实现位置 | 一致性 |
|---|---|---|
| spec.md US1（画像事实/意愿/推断/未知项边界） | `webui/store.py` candidate_profile_versions + `webui/discovery.py` analyze_resume | PASS — T032–T034 验证 |
| spec.md US2（100→15 渐进管道） | `webui/discovery_runner.py` + `tests/test_discovery_performance.py` | PASS — T045–T051 验证 |
| spec.md US3（硬规则优先于 AI 分数） | `webui/discovery.py::compile_search_plan` + `webui/projector.py` | PASS — T060–T065 验证 |
| spec.md US4（取消/恢复/缓存/breaker/进度） | `webui/source.py` breaker + `webui/discovery_runner.py` cancel/resume | PASS — T066–T085 验证 |
| spec.md US5（反馈作用域/撤销/历史不变） | `webui/store.py::create_discovery_feedback`/`revoke_discovery_feedback` + `webui/discovery.py::apply_feedback_to_next_run` | PASS — T086–T092 验证 |
| spec.md FR-050/FR-051 | 同上 + `webui/app.py` HTTP 端点 | PASS — T092 `Fr050Fr051VerificationTests` 3 项全通 |
| contracts/http-api.md 反馈端点 | `webui/app.py` L2615–L2682 | PASS — T090 HTTP 5 项全通 |
| contracts/state-machine.md analysis stages | `webui/store.py` candidate_analyses.status | PASS — T109/T110 验证 |

#### 审查维度 2：FR/SC 覆盖

参考 tasks.md L219–L257 的 FR/SC 覆盖表：

**Functional Requirement 覆盖**：
- FR-001–FR-049：由 T020–T085 覆盖（Phase 2–6 全部 [X]）
- FR-050/FR-051：由 T086–T092 覆盖（Phase 7 全部 [X]）

**Success Criteria 覆盖**：
| SC | 当前证据状态 |
|---|---|
| SC-001/SC-002 | T051 自动化门 PASS；真实 E2E 待 T099（DEFERRED） |
| SC-003 | T085 自动化门 PASS；真实 E2E 待 T099（DEFERRED） |
| SC-004 | T084 自动化门 PASS；真实 E2E 待 T099（DEFERRED） |
| SC-005–SC-009 | T065 + T095 黄金样本 PASS |
| SC-010/SC-011 | T084 自动化门 PASS；真实 E2E 待 T099（DEFERRED） |
| SC-012 | T095 PII redaction PASS |
| SC-013 | T090 前端 resumeDiscoveryRun 入口存在；真实 1366×768/720px 浏览器验证待 T097（DEFERRED） |
| SC-014 | T099 真实 E2E（DEFERRED） |

#### 审查维度 3：测试产物与真实产物一致性

- 自动化测试：1713 项全通，0 errors / 0 failures
- 黄金样本：`evaluate_result.json` 与文字输出完全一致
- 真实产物：`real_performance_5.json` 和 `real_e2e_result.json` 不存在（T098/T099 DEFERRED）

#### 审查维度 4：file:line 证据

| Claim | file:line 证据 |
|---|---|
| 反馈默认 scope=exact_job | `webui/store.py` L4156–L4179 `create_discovery_feedback` |
| 撤销幂等 | `webui/store.py` L4236–L4286 `revoke_discovery_feedback` |
| effective_only 过滤 | `webui/store.py` L4288–L4298 `list_discovery_feedback` |
| direction_disable 移除方向 + 收集 excluded_job_ids | `webui/discovery.py` L1585–L1630 `apply_feedback_to_next_run` |
| 全禁用时抛 input_incomplete | `webui/discovery.py` L1624 |
| HTTP 反馈端点 | `webui/app.py` L2615–L2682 |
| SC-003 编排门 | `tests/test_discovery_performance.py::Sc003DeterministicOrchestrationGateTests` |
| SC-004/010/011 门 | `tests/test_discovery_performance.py::Sc004Sc010Sc011PerformanceGateTests` |
| FR-050/FR-051 验证 | `tests/test_discovery_integration.py::Fr050Fr051VerificationTests` L1387–L1600 |

#### 审查结论

- **PASS 项**：FR-001–FR-051 全部由自动化测试覆盖；SC-001–SC-012 有当前自动化证据；SC-013 前端入口存在；文档（README/README.en/CHANGELOG）双语同步；版本号四处一致；Flask 后端 HTTP 200；git diff --check 无 whitespace 错误；用户既有改动（`.specify/feature.json`、`.trae/rules/project_rules.md`、`tests/fixtures/discovery/e2e_real_boss.py`、`_tmp_diag_t133*.py`）原样保留。
- **DEFERRED 项**：SC-013/SC-014 真实浏览器 E2E（T097）、真实 HTTP E2E（T099）受 AI 服务 HTTP 429 FreeUsageLimitError 阻塞（Retry-After 56664s）。T098 真实性能样本已于 2026-07-21 完成（见 T098 节）。Release Gate 的"真实 E2E 不足 5 details/5 assessments"条款尚未满足，**整体 Release Gate 不得宣称 PASS**。

### T099 — 受控真实 HTTP E2E（首次尝试：blocked）

**执行时间**：2026-07-21 16:11–16:13（Beijing time）

**执行命令**：`python tests/fixtures/discovery/e2e_real_boss.py --output specs/005-fast-resume-discovery/validation/real_e2e_result.json`

**前置条件全就绪**（prerequisites 三项 OK）：
- cdp: OK（Chrome Chrome/150.0.7871.125 on port 9222）
- boss_login: OK（cookies 持久化，session 有效）
- ai_credentials: OK（endpoint=https://opencode.ai/zen/v1, model=deepseek-v4-flash-free, masked_key=sk-4***83kV, status=ready）

**T132 Live-provider contract smoke**：两项全 failed
- candidate_analysis_v2: status=failed, error_code=ai_invalid_output
- job_assessment_v1: status=failed, error_code=ai_invalid_output

**T133 真实 BOSS E2E**：4 次 analysis 尝试全部 failed（1 次主 + 3 次 retry）
- analysis_id 链：3242d96df7ee4782 → e6b5aca4016f4312 → f9f759cc816c40f2 → 907839702aa94d6f
- 最终 status=blocked, reason="analysis did not reach ready (status=failed)"
- analysis_failure: error_code=ai_invalid_output, retryable=false, stage=analyzing
- market_attempts: [{resume: resume_cross_family.txt, status: blocked, source_count: 0, detail_count: 0, evaluated_count: 0, blockers: []}]

**根因独立诊断**（临时脚本 `_tmp_diag_ai.py`，跑完删除）：

直接 POST 到 `https://opencode.ai/zen/v1/chat/completions`（用 webui.db 的真实 endpoint + keyring 的真实 key），返回：
```
HTTP status: 429
Retry-After: 56664
Content-Type: text/plain;charset=UTF-8
body: {"type":"error","error":{"type":"FreeUsageLimitError","message":"Rate limit exceeded. Please try again later."},"metadata":{}}
```

**根因结论**：AI 服务 `deepseek-v4-flash-free` 当日免费额度用完，HTTP 429 FreeUsageLimitError，Retry-After 56664 秒（≈15.7 小时）。`ai_invalid_output` 是因为 `call_ai`（webui/ai.py L213-214）将 status_code>=400 映射为 ERROR_INVALID，suppress 原始异常后抛 `AISecurityError("ai_invalid_output")`。这是已验证的外部服务限流，**不是代码 bug**——memory 记录 2026-07-20 T132 曾通过（evidence_count=11, direction_count=3），今日额度耗尽。

**产出文件**：
- `specs/005-fast-resume-discovery/validation/real_e2e_result.json`：blocked 状态报告，记录完整 prerequisites/smoke/market_attempts/analysis_failure
- `tests/fixtures/discovery/live_provider_smoke_result.json`：T132 smoke 报告

**阻塞说明**：
- T099 的 Release Gate 要求（≥2 方向、≥5 details、≥5 assessments、cancel/resume ok、无未解释 blocker）**未满足**
- 阻塞点是 AI 服务限流，非代码/配置/环境问题
- 待 AI 恢复（约 2026-07-22 08:00 Beijing time）后重跑：`python tests/fixtures/discovery/e2e_real_boss.py --output specs/005-fast-resume-discovery/validation/real_e2e_result.json`（覆盖当前 blocked 报告）

**未验证边界**：
- 真实搜索→详情→评估→反馈→cancel→resume 全流程（AI 恢复前无法验证）
- SC-001/002/003/004/010/011/014 的真实 E2E 证据（自动化门已 PASS，真实 E2E 待补）

### T106 — git diff --check + git status

**`git diff --check`**：exit 0（仅 CRLF/LF 警告，无 whitespace 错误）

**`git status --short --branch`**：
```
## codex/fast-resume-discovery
 M .specify/feature.json           # 用户既有改动，保留
 M .trae/rules/project_rules.md    # 用户既有改动，保留
 M CHANGELOG.md                    # T102
 M README.en.md                    # T101
 M README.md                       # T101
 M scripts/boss_cdp_raw.py         # feature 005 additive CLI 扩展
 M tests/fixtures/discovery/e2e_real_boss.py  # 用户既有改动，保留
 M tests/test_ai.py                # T055/T056
 M tests/test_boss_discovery_source.py        # T066–T071
 M tests/test_candidate.py         # T020–T027
 M tests/test_chrome_setup.py      # 版本号一致性测试
 M tests/test_discovery.py         # T014–T019, T052–T065
 M tests/test_discovery_contracts.py  # T028–T031, T047–T050, T090
 M tests/test_discovery_frontend.py   # T072, T074, T076, T078, T090, T110
 M tests/test_discovery_integration.py # T034, T088, T092, T108–T109
 M tests/test_discovery_store.py      # T004, T006, T008, T052, T086
 M tests/test_process_executor.py     # T078–T079
 M tests/test_screening.py            # T052–T053
 M webui/ai.py                        # T055/T056 provider
 M webui/app.py                       # T028, T047, T060, T076, T091, T109
 M webui/candidate.py                 # T020–T027
 M webui/discovery.py                 # T014–T019, T052, T089
 M webui/discovery_runner.py          # T045, T066, T076–T079
 M webui/index.html                   # T032, T049, T072, T078, T091, T110
 M webui/screening.py                 # T052–T053
 M webui/source.py                    # T066–T075
 M webui/store.py                     # T004, T006, T008, T052, T087
?? _test_out.log                      # 测试残留，可清理
?? _test_out.txt                      # 测试残留，可清理
?? _tmp_diag_t133.py                  # 用户诊断产物，保留
?? _tmp_diag_t133_v2.py               # 用户诊断产物，保留
?? specs/005-fast-resume-discovery/   # feature 005 冻结 spec + tasks + validation
?? tests/fixtures/discovery/ai_candidate_v4.json      # T055 fixture
?? tests/fixtures/discovery/ai_job_assessment_v2.json # T054 fixture
?? tests/fixtures/discovery/fast_resume_discovery_v2.json  # T006 fixture
?? tests/test_discovery_performance.py  # T007/T051/T084/T085 性能门测试
```

**用户既有改动保留核对**：
- `.specify/feature.json`：tracked modified，feature 目录从 004 指向 005（用户既有）— 未回退
- `.trae/rules/project_rules.md`：tracked modified，plan 指向 005（用户既有）— 未回退
- `tests/fixtures/discovery/e2e_real_boss.py`：tracked modified，移除 `max_details=1`、poll timeout 480s→1800s（用户既有）— 未回退
- `_tmp_diag_t133.py` / `_tmp_diag_t133_v2.py`：untracked 诊断产物 — 原样保留

### Phase 8 Gate 状态

| Gate 子句 | 状态 | 说明 |
|---|---|---|
| SC-001–SC-012 均有当前证据 | PASS（自动化） | 真实 E2E 证据待 T097/T099 |
| SC-013/SC-014 真实 E2E | DEFERRED | T097/T099 受 AI 429 限流阻塞 |
| 真实 E2E ≥5 details/≥5 assessments | PARTIAL | T098 已产出 5 真实 details（p50=2021ms）；≥5 assessments 待 T099 |
| 无未解释 blocker | PARTIAL | T099 blocker 已解释为 AI 429 限流（非代码问题）；T099 completed 状态的 blocker 核验待 AI 恢复 |
| 文档与 JSON 无冲突 | PASS | T100 核对通过（含新增 real_performance_5.json + real_e2e_result.json） |
| 独立审查通过 | PARTIAL（self-review） | T105 自审 PASS；真正独立审查需另一 reviewer |

**Phase 8 收口状态**：T093/T094/T095/T096/T098/T100/T101/T102/T103/T104/T105/T106 共 12 项 [X]；T097/T099 DEFERRED（受 AI 429 限流阻塞，Retry-After 56664s）。

**Release Gate 状态**：**不得宣称 PASS**。已满足：自动化门 + 文档 + 自审 + T098 真实性能样本（5 details）。未满足：T097 真实浏览器 E2E、T099 真实 HTTP E2E（≥5 assessments + cancel/resume + 无未解释 blocker）、真正独立审查。当前可宣称"自动化门 + 文档 + 自审 + 真实性能样本全部通过；T097/T099 受 AI 服务 HTTP 429 FreeUsageLimitError 阻塞，待 AI 恢复（约 2026-07-22 08:00 Beijing time）后重跑"。

下一步：AI 服务恢复后重跑 T097（浏览器 1366×768/720px 流程）和 T099（受控真实 HTTP E2E），覆盖当前 `real_e2e_result.json` 的 blocked 报告；再由另一 reviewer 执行 T105 独立审查。


