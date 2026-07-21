# Implementation Plan: 快速简历驱动岗位推荐收口

**Branch**: `master`（当前仅规划；实现前按仓库规则创建 `codex/...` feature branch） | **Date**: 2026-07-20 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/005-fast-resume-discovery/spec.md`

## Summary

在 004 已有简历分析、方向确认、真实来源抓取、岗位评估和恢复状态机之上，新增一个可编辑且版本化的候选人画像，并把发现运行升级为“列表候选池 → 确定性预检与优先详情 → 详情完成即评估 → 结果渐进可见”的持久化流水线。

技术方案保持现有 Python/Flask/SQLite/原生前端栈和单文件抓取核心，不新增第三方依赖。通过 additive migration 015 增加候选人事实版本和 run-scoped 列表候选；通过 discovery policy v2 固定最低薪资语义、标准 15 个详情预算、方向覆盖和稳定排序；通过受控详情批次、页面就绪判定、结构化子进程进度和单元级 checkpoint 消除每岗位重复初始化与无意义末尾等待；通过 job-assessment v2 将一个岗位最多两个相关方向合并为一次 AI 请求，并让详情、评估和结果展示形成有界流水线。

## Technical Context

**Language/Version**: Python >=3.10；浏览器端原生 HTML/CSS/JavaScript  
**Primary Dependencies**: Flask 3.x、requests 2.x、websocket-client 1.x、keyring 24–25、pypdf 4–5、python-docx 1.x；不新增第三方运行依赖  
**Storage**: SQLite 状态数据库；受控 JSON/CSV 抓取产物；系统凭据存储保存 AI key  
**Testing**: Python `unittest`；fake AI/fake JobSource/temp SQLite；可选 Playwright 真实浏览器验收；受控真实 BOSS + 真实 AI E2E  
**Target Platform**: 用户本机 Windows 为首要平台；本地 Flask Web UI；用户本人已登录的 Chrome CDP 会话  
**Project Type**: 本地 Web 应用 + Python CLI 抓取器  
**Performance Goals**: 去重列表候选池 ≤90 秒；首批 5 个已评估岗位 ≤5 分钟；标准 15 个真实详情及所需评估 ≤10 分钟；工作单元完成后进度 ≤10 秒可见  
**Constraints**: 个人求职受控频率；不无限并发或绕过来源验证；用户硬约束违规推荐率 0%；刷新/取消/恢复不丢已完成工作；用户隐私与原始模型输出不进入普通日志或结果  
**Scale/Scope**: 单用户本地运行；每次 1–5 个方向、最多 12 个搜索项、约 100–200 个列表候选、默认 12–20 个详情（标准 15）、每岗位最多 2 个首轮语义方向  
**Current Schema**: migration 014；本功能只允许 additive migration 015  
**Current Baseline**: 捕获的真实运行 480 秒仅完成 9 个详情，约 53 秒/详情；详情峰值并发 1；最新真实 E2E 仍为 blocked  

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

项目没有 `.specify/memory/constitution.md`，因此使用根 `AGENTS.md` 和本会话全局规则作为治理门：

| Gate | Pre-design | Post-design evidence |
|---|---|---|
| 先开 issue、再创建 feature branch、再实现非平凡改动 | PASS for planning；实现前仍是阻断门 | `quickstart.md` 将其列为执行前置；plan 不声称已创建 issue/branch |
| 抓取核心逻辑保留在 `scripts/boss_cdp_raw.py`，不新增随意逻辑文件 | PASS | 设计只扩展现有 scraper、source、runner、store、app 和前端文件 |
| additive 数据迁移、保留旧运行与不可变快照 | PASS | data model 使用 migration 015，新字段/表均 additive，旧 profile version 可为空 |
| 程序掌握状态推进与硬规则，AI 只输出受校验语义判断 | PASS | salary policy、priority、category、sort 和 terminal state 均 program-owned |
| 后台任务必须可追踪、取消、恢复、失败隔离 | PASS | run candidate/work-unit 状态、单元 checkpoint、input hash 和 metrics 明确建模 |
| 文档不能替代运行时约束 | PASS | 所有硬约束、进度、产物和性能门均要求代码契约及测试，不依赖提示文字 |
| 用户可见行为同步 README 中英与 CHANGELOG；版本变更四处一致 | PASS | 作为最终集成切片的发布门，任务阶段执行 |
| 后端改动后由 Codex 重启受影响服务并验证；前端真实桌面/窄屏检查 | PASS | quickstart 规定重启、HTTP、1366×768 和 720px 验收 |
| 真实 E2E 不能由 fake、smoke 或历史文档替代 | PASS | 新建 005 独立结果与 validation；SC-014 要求 ≥5 details/evaluations 且无 blocker |
| 保留用户当前未提交改动，不回退无关文件 | PASS | plan 不触碰现有 E2E 脚本修改和临时诊断文件；实现切片需先审计工作区 |

**Gate result**: PASS。无未解释治理冲突；实现尚未获 issue/branch 门，不能直接从本 plan 跳到代码修改。

## Project Structure

### Documentation (this feature)

```text
specs/005-fast-resume-discovery/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md
│   ├── ai-contracts.md
│   └── state-machine.md
├── checklists/
│   └── requirements.md
└── tasks.md                 # 由 /speckit-tasks 创建，不在本阶段生成
```

### Source Code (repository root)

```text
scripts/
└── boss_cdp_raw.py          # 详情批次、就绪判定、安全结构化进度；保持单文件原则

webui/
├── candidate.py             # candidate-analysis v4、事实合同与事实归一化
├── resume.py                # 上传只存储；不重复触发 discovery AI 分析
├── ai.py                    # candidate v4 与 job-assessment v2 provider 合同
├── discovery.py             # min_salary、候选优先级、分类、排序、结果投影
├── discovery_runner.py      # 候选池、详情→评估流水、checkpoint、metrics
├── source.py                # 受控详情批次与结构化完成回调
├── process_executor.py      # 复用已有 on_output/on_poll，不新增执行器
├── screening.py             # 版本化三态硬规则及数值最低薪资
├── store.py                 # migration 015 与新增实体 CRUD
├── app.py                   # HTTP 契约、渐进 results/progress
└── index.html               # 画像编辑、四类进度、运行中结果与解释/JD

tests/
├── test_candidate.py
├── test_ai.py
├── test_discovery.py
├── test_discovery_store.py
├── test_discovery_integration.py
├── test_discovery_contracts.py
├── test_discovery_frontend.py
├── test_discovery_browser.py
├── test_boss_discovery_source.py
├── test_process_executor.py
├── test_screening.py
└── fixtures/discovery/
    ├── evaluate.py
    └── e2e_real_boss.py
```

**Structure Decision**: 不新增运行时业务模块或第三方依赖。候选人、发现、来源、执行器、存储和前端继续落在现有深模块内；新复杂度通过明确数据实体、版本化合同和单元状态边界隔离，而不是继续在 HTTP route 中复制组合逻辑。

## Phase 0 Research Decisions

完整决策和备选见 [research.md](research.md)。冻结以下结论：

1. 详情瓶颈按真实捕获链处理，不以“增加线程”作为第一修复。
2. 默认来源详情并发保持 1；通过单进程受控批次、就绪判定和与 AI 工作重叠获得首轮收益。只有真实稳定性验证通过后，policy 才允许上限 2。
3. 标准首轮详情预算为 15；候选选择使用确定性预检、方向相关性、信息完整性和方向覆盖，不新增一次 AI 预排序调用。
4. 详情抓取器以结构化安全事件报告每岗位完成；runner 单元级持久化并把评估提交到有界单 worker，形成 source 与 AI 重叠的渐进流水线。
5. candidate-analysis v4 在一次分析请求链内同时返回结构化 facts、证据、未知项和方向；旧上传解析不再在 discovery 默认路径重复调用。
6. job-assessment v2 一次评估一个岗位的最多两个相关方向；每方向独立校验、独立降级，程序仍负责分类和排序。
7. SQLite migration 015 增加候选人版本、事实、run candidates 和可重现指标；旧运行保持 policy v1 和兼容结果。
8. 结果使用一个 canonical recommendation projector；HTTP、前端和导出不得再各自实现不同排序/守卫。
9. 渐进更新继续使用现有 3 秒轮询，不引入 SSE/WebSocket 依赖；results 允许运行中查询并使用稳定 cursor/rank。
10. 跨运行详情复用采用完整详情快照、来源身份、内容身份和有限新鲜度；未知或过期必须重新核验。

## Phase 1 Design

### Data design

详见 [data-model.md](data-model.md)。migration 015 主要新增：

- `candidate_profile_versions`
- `candidate_fact_items`
- `candidate_fact_evidence`
- `discovery_run_candidates`
- 对 confirmation/run/snapshot/assessment 的 additive identity、progress、reuse 和 metric 字段

`RecommendationItem` 保持 program-owned projection，不先增加可漂移的第二份推荐事实表；稳定 rank 和可见性由当前 snapshots、assessments、feedback 与 policy v2 重建。

### Interface design

- [http-api.md](contracts/http-api.md)：画像版本编辑、确认、运行进度和渐进结果的兼容 HTTP 契约。
- [ai-contracts.md](contracts/ai-contracts.md)：candidate-analysis v4 与 job-assessment v2 的输入、输出、引用和降级规则。
- [state-machine.md](contracts/state-machine.md)：candidate、run candidate、详情/评估流水线、取消和恢复状态。

### Validation design

详见 [quickstart.md](quickstart.md)。验证分四层：

1. 秒级 contract/unit RED→GREEN。
2. 100 列表候选、20 详情、3 方向的 deterministic fake pipeline。
3. 真实 HTTP + 桌面/窄屏渐进结果。
4. 当前代码、当前 AI、当前登录态下的受控真实 BOSS E2E；独立生成 005 evidence，不复用 004 历史结论。

## Implementation Slices and Gates

### Slice 0 — Execution authorization and baseline

- 创建结构化 issue，说明问题、现状、根因、建议和影响边界。
- 从 `master` 创建 `codex/...` feature branch。
- 记录当前 384 项发现链回归、全量回归、真实 E2E blocker 和 53 秒/详情捕获基线。
- 建立性能 metric contract 和 deterministic red-capable harness。

**Gate**：没有 issue/branch、无法重现基线或用户工作区冲突未处理时停止。

### Slice 1 — Correctness contract closure

- 版本化 `min_salary` 数值下限，贯通确认、预检、详情硬规则和结果。
- 建立 canonical recommendation projector，统一分类守卫、稳定排序、多方向、JD 和解释。
- 统一四类进度字段，详情/列表计数按工作单元更新。
- discovery 上传路径只存储一次，候选分析由唯一入口发起。

**Gate**：薪资违规推荐率 0；HTTP/frontend 契约一致；旧 policy v1 回归通过。

### Slice 2 — Editable candidate profile v4

- migration 015 的 profile version/facts/evidence。
- candidate-analysis v4 与一次纠正重试。
- draft PATCH、确认冻结和用户值优先。
- 前端结构化事实编辑、来源和未知项。

**Gate**：事实修改 100% 进入新确认快照；旧分析/运行不改写；隐私测试通过。

### Slice 3 — Durable candidate pool and priority

- 列表结果写入 `discovery_run_candidates`，替代内存/文件作为恢复事实。
- canonical identity 去重、硬条件预检、确定性优先分和方向覆盖。
- standard policy v2 选择 15 个详情；保留 deferred 原因。

**Gate**：100→15 fixture 稳定、每方向有机会、列表返回顺序变化不改变同分 tie-break、恢复不丢候选。

### Slice 4 — Detail transport and checkpoint performance

- 抓取器受控批次与结构化安全事件。
- 页面就绪判定、条件式滚动、无最后一项空等。
- snapshot 单元即时保存；已完成同 run 详情恢复时跳过。
- 新鲜详情可复用，过期/未知重新核验；来源异常触发 circuit breaker。

**Gate**：重复详情 0；取消后无新工作；fake timing 门和真实小样本稳定性门通过，再考虑 source concurrency 2。

### Slice 5 — Progressive assessment and results

- job-assessment v2、每岗位最多两个相关方向、run-level candidate/evidence 缓存。
- 详情完成即提交评估，评估完成即进入 results。
- 运行中结果轮询、稳定 rank/cursor、岗位身份不闪烁。

**Gate**：首批 5 fake pipeline ≤门限；单 AI/单详情失败隔离；前端在非终态出现真实结果卡。

### Slice 6 — Integration, release and real acceptance

- 全量回归、迁移 014→015、黄金样本、隐私和恢复回归。
- 1366×768 与 720px 真实 HTTP 浏览器验收。
- 真实 BOSS 标准 E2E：≥5 details、≥5 evaluations、渐进结果、feedback、cancel/resume、无 blocker。
- README 中文/英文、CHANGELOG；如版本变化，四处同步。
- 重启受影响 Flask 服务并验证可访问。

**Gate**：SC-001–SC-014 有当前证据；validation 文档与结果产物一致；独立审查通过。

## Rollback Strategy

- migration 015 只新增表/列，不删除或重写 001–004 数据。
- 新运行通过 `policy_version` 选择 v2；已有运行继续走 v1 兼容投影。
- candidate v4 失败可以安全降级到人工补充，不能伪装成 v3 complete。
- progressive pipeline 可按运行策略回退为单 source worker，但不得回退最低薪资、排序、解释或单元 checkpoint 正确性。
- 详情复用可通过 freshness policy 关闭；关闭后重新抓取，不删除历史 snapshot。
- 真实来源出现验证/限流时自动停止新增 source work，保留已完成结果，不自动提高频率重试。

## Risks and Mitigations

| Risk | Mitigation | Release evidence |
|---|---|---|
| 缩短等待触发来源验证或登录失效 | readiness-driven wait、默认 source concurrency 1、连续异常 circuit breaker | 受控真实小样本 + 标准 E2E |
| 渐进线程导致计数竞争或重复工作 | run candidate CAS transition、input hash、DB authoritative counters | cancellation/resume/concurrency integration tests |
| v4 事实合同过大导致模型无效输出 | 字段独立 quarantine、bounded lists、单次纠正、用户 draft 补充 | AI contract fixtures + live provider smoke |
| 数值薪资误解年薪/日薪/面议 | versioned salary parser；不可比较为 unknown | salary matrix tests |
| 优先选择损失相邻/发展型召回 | per-direction floor + deferred pool + direction coverage metrics | 100→15 fixture、黄金样本 |
| 详情缓存展示过期岗位 | visible fetched_at/source_status、freshness limit、identity mismatch refetch | cache expiry/content drift tests |
| 结果渐进重排造成用户困惑 | stable recommendation identity、rank reason、仅可解释原因隐藏 | browser interaction tests |
| 旧接口/历史运行退化 | additive response fields、policy v1 fallback、schema migration fixtures | full regression + 014→015 migration test |

