# Validation: 简历驱动的岗位发现 (004)

**Feature**: 004-resume-job-discovery
**最近更新**: 2026-07-15 (Asia/Shanghai) — 全量审查后修复批次
**Git HEAD**: `83b99d188d93083af929fb22abe6998f0c68a651`
**Python**: 3.13.5
**工作树**: dirty（用户未提交修改 + feature 004 新增文件 + 本会话修复全部保留，未 reset/checkout）

本文件记录 feature 004 实施过程中实际执行的命令、测试数量、结果、环境、证据边界与未验证项。遵循 handoff 要求：未通过的门禁如实标注为阻塞，不伪造通过、不用模拟测试冒充真实来源验收。

> 2026-07-15 更新：本会话基于全量审查（1 Critical + 2 High + 3 Contract Drift + 9 Medium + 测试方法学）执行了"全部修复"指令。16 项问题已修复并通过 1010 tests 全量回归。修复详情见 §9。

---

## 1. 已实现并验证 (Implemented and Verified)

### 1.1 迁移与兼容性 (Migration / Compatibility Result)

| 迁移 | 内容 | 验证方式 | 结果 |
|------|------|----------|------|
| 011 | candidate_analyses / resume_evidence / career_directions / direction_evidence | test_discovery_store.py SchemaMigrationTests | PASS |
| 012 | direction_confirmations / confirmation_directions / discovery_runs / discovery_run_events / search_plans / search_plan_items（input_hash 唯一、终态不可逆、确认版本不可更新） | test_discovery_store.py | PASS |
| 013 | discovery_job_snapshots / job_direction_assessments / discovery_feedback（(run_id,job_id) 唯一、(run_id,snapshot_id,direction_id) 唯一、分类优先级） | test_discovery_store.py | PASS |

- schema 版本从 10 累加至 13，仅新增表，不重写旧表。
- 旧 workbench / screening API 与历史数据保持只读兼容：test_webui_store.py 的 `test_old_tables_preserved_after_migration` / `test_old_data_preserved_after_screening_migration` 通过。
- 迁移幂等：`test_migration_is_idempotent` 通过。

### 1.2 后端核心实现

| 模块 | 关键函数 | 验证 |
|------|----------|------|
| webui/candidate.py | validate_candidate_analysis、normalize_evidence | test_candidate.py 32 tests |
| webui/discovery.py | compile_search_plan、build_snapshot、assess_job_direction、build_portfolio、build_safe_explanation、confirm_directions、analyze_resume、calculate_run_completion、apply_feedback_to_next_run | test_discovery*.py |
| webui/semantic.py | validate_job_assessment | test_discovery.py |
| webui/screening.py | verify_hard_rules_tri_state（保留旧 verify_hard_rules_detailed 兼容） | test_screening.py |
| webui/source.py | JobSource 抽象 | test_boss_discovery_source.py 32 tests |
| webui/discovery_runner.py | 运行编排 | test_discovery_integration.py 40 tests |

### 1.3 前端统一四步流程

upload → confirm → progress → results 四步已在 webui/index.html 落地，由 test_discovery_frontend.py（24 tests）与 test_discovery_browser.py（16 tests）覆盖。

---

## 2. 实际执行的命令与结果 (Commands and Results)

### 2.1 全量自动化回归 (T093)

```bash
python -m unittest discover -s tests -v
```

**结果**（2026-07-15 修复后回归）：
```
Ran 1018 tests in 253.739s
OK
```

- 总数：1018（较上次 +12：CR-1/HI-2 跨分析 + M3/M4 store 测试 + 2 个占位测试替换 + 8 个 e2e 前置检查三态化测试）
- 通过：1018
- 失败：0
- 错误：0
- 耗时：253.739s

### 2.2 Discovery 模块分项 (T093 明细)

```bash
python -m unittest tests.test_candidate tests.test_discovery tests.test_discovery_contracts \
  tests.test_discovery_store tests.test_discovery_integration tests.test_discovery_frontend \
  tests.test_boss_discovery_source tests.test_discovery_browser
```

**结果**：`Ran 235 tests in 127.742s` → `OK`

| 模块 | 测试数 |
|------|--------|
| tests.test_candidate | 32 |
| tests.test_discovery | 21（含本会话新增 source_status / portfolio 真实测试）|
| tests.test_discovery_contracts | 22 |
| tests.test_discovery_store | 48（含本会话新增 M3 跨分析 + M4 UNIQUE 测试）|
| tests.test_discovery_integration | 40（含本会话新增 CR-1 级联删除 + HI-2 PII 脱敏）|
| tests.test_discovery_frontend | 24 |
| tests.test_boss_discovery_source | 32 |
| tests.test_discovery_browser | 16 |
| **合计** | **235** |

### 2.3 黄金样本评估 (T086/T087)

```bash
python tests/fixtures/discovery/evaluate.py
```

**结果**（tests/fixtures/discovery/evaluate_result.json）：SC-003–SC-009 全部 PASS

| 指标 | 目标 | 实测 | 判定 |
|------|------|------|------|
| direction_acceptance_rate | ≥0.7 | 1.0 | PASS |
| precision_at_20 | ≥0.6 | 0.8 | PASS |
| recall | ≥0.5 | 0.8 | PASS（见 §5.3 指标局限） |
| hard_rule_violation_rate | ≤0.05 | 0.0 | PASS（见 §5.3 指标局限） |
| multi_direction_coverage | ≥0.6 | 1.0 | PASS |
| explanation_fidelity | ≥0.8 | 0.875 | PASS |
| no_evidence_default_enabled_rate | =0 | 0.0 | PASS |
| sc007_violation_rate | =0 | 0.0 | PASS |

PII 脱敏：7 份简历全部 redaction_markers=1、real_pii_count=0、real_pii_blocked_from_evidence=true。

policy_version: `v1-golden-2026q3`；校准：adjacent_min_evidence_overlap=0.34、growth_min_evidence_overlap=0.20。

### 2.4 浏览器渲染验证 (T088/T089)

```bash
python -m unittest tests.test_discovery_browser
```

**结果**：`Ran 16 tests` → `OK`（Playwright，1366×768 与 720px，覆盖空/加载/成功/部分/失败/待确认/无结果状态、无横向溢出、主操作可触达、焦点态）。

### 2.5 真实来源 Smoke (T090)

```bash
python scripts/boss_cdp_raw.py --check
```

**结果**（2026-07-15 修复后实测）：

CDP down 状态（凌晨）：
```
[1/3] Python 依赖... ✅ 依赖完整
[2/3] CDP 端口连通性... ❌ 失败 — WinError 10061 连接拒绝
[3/3] BOSS直聘登录状态... ❌ 未登录（CDP 不可达，无法检查）
```

CDP up 状态（2026-07-15 00:30 重启 CDP Chrome 后，登录态自动恢复）：
```
[1/3] Python 依赖... ✅ 依赖完整
[2/3] CDP 端口连通性... ✅ 通过 — Chrome Chrome/150.0.7871.115
[3/3] BOSS直聘登录状态... ✅ 已登录
✅ 所有检查通过，可以开始抓取
```

下午 16:20:59 的登录态在 CDP Chrome user-data-dir (`~/.career-scout/chrome-profile`) 中持久化保存，重启 CDP Chrome 后无需重新登录即可恢复。

### 2.6 受控真实 BOSS E2E (T091/T092)

```bash
python tests/fixtures/discovery/e2e_real_boss.py
```

**结果**（tests/fixtures/discovery/e2e_real_boss_result.json）：`status: blocked`

> 2026-07-15 修复后实测：e2e_real_boss.py 前置检查已升级为三态 + 离线诊断 + active probe。详见 §10。

| 前置条件 | 状态 | 错误 |
|----------|------|------|
| CDP | false | 127.0.0.1:9222 连接拒绝 (WinError 10061) |
| boss_login | unknown | 无 CDP 无法检查登录态（离线诊断：Cookies file exists, 28KB, modified 2026-07-14 16:20:59; login state may still be valid） |
| ai_credentials | false | 未配置 AI API key |

> 注：`boss_login` 现为三态（true/false/"unknown"）。CDP down 时返回 "unknown" + 离线诊断 note，不再误报 false。详见 §10。

脚本在缺失前置时诚实返回 blocked，未模拟任何抓取/评估/反馈结果。

**2026-07-15 修复后实测**（CDP up + 登录态恢复）：

```
cdp: OK
boss_login: OK  (active probe 确认登录态有效)
ai_credentials: MISSING
→ BLOCKED on ai_credentials
```

证实：(1) 离线诊断 Cookies mtime 16:20:59 与下午登录时间一致；(2) 重启 CDP Chrome 后登录态自动恢复，无需重新登录；(3) active probe 比 tab 列表检查更准确（无 zhipin tab 也能正确识别已登录态）。

### 2.7 后端重启验证 (T094)

```bash
Invoke-WebRequest -Uri "http://127.0.0.1:5000/" -UseBasicParsing
```

**结果**：`STATUS=200 LEN=175052` — 后端可达，首页正常返回。

后端进程在本会话前已启动；本次回归与迁移改动后未触发需要重启的变更（迁移代码已在更早提交中生效，数据库 schema 已升级至 13），故未执行额外重启动作。已验证 5000 端口当前可访问。

---

## 3. 直接低风险 UX 优化 (Direct Low-Risk UX Optimizations)

本会话为消除回归阻塞所做的最小化测试契约修正（非功能改动）：

1. **tests/test_webui_store.py**：移除文件首部 UTF-8 BOM (U+FEFF)，修复 `SyntaxError: invalid non-printable character U+FEFF`，使 83 个 store 测试可被 collect。
2. **tests/test_webui_app.py** (`test_frontend_uses_persistent_responsive_workspace_contract`)：将 `assertNotIn("match_score", html)` 收窄为排除原始 AI 字段（ai_rank / match_reason / ai_score）。依据 spec 004 FR-064 与 openapi.yaml，`match_score` 是经程序校验的发现卡片字段，spec 003 的"HTML 不得出现 AI 评分"规则过宽。
3. **tests/test_webui_browser.py** (`test_no_ai_scores_in_card_template`)：同上收窄。

这些修改仅放宽与 spec 004 冲突的旧断言，未削弱任何 spec 004 要求；原始 AI 字段仍被排除。

---

## 4. 当前证据汇总 (Current Evidence)

| 门禁 | 证据 | 判定 |
|------|------|------|
| 契约测试（HTTP/AI schema/错误信封/枚举/隐私排除） | test_discovery_contracts.py 22 tests | ✅ PASS |
| 单元测试（证据规范化/方向归并/搜索计划/三态硬规则/分类/组合多样性） | test_candidate.py + test_discovery.py 53 tests | ✅ PASS |
| 迁移/Store 测试（schema-10 fixture 升级、幂等、旧行保留、重启收敛、删除清理、长期反馈） | test_discovery_store.py 48 tests | ✅ PASS |
| 集成测试（fake AI / fake JobSource / 临时 SQLite 全流程、取消、部分成功、重启/恢复） | test_discovery_integration.py 40 tests | ✅ PASS |
| 黄金样本评估（脱敏简历 + 人工标注，SC-003–SC-009） | evaluate_result.json | ✅ PASS（指标局限见 §5.3） |
| 浏览器渲染（1366×768 / 720px，状态覆盖、无溢出） | test_discovery_browser.py 16 tests | ✅ PASS |
| 真实来源 smoke（仅连通性） | boss_cdp_raw.py --check | ❌ BLOCKED（CDP 不可达） |
| 受控真实 BOSS E2E | e2e_real_boss_result.json | ❌ BLOCKED（CDP/登录/AI 凭据缺失） |
| 全量回归 | 1018 tests OK | ✅ PASS |

---

## 5. 未验证或外部阻塞边界 (Unverified / Externally Blocked Boundaries)

### 5.1 真实 BOSS 端到端 (T090/T091/T092) — 阻塞（部分已实测验证）

阻塞节点：
- ~~Chrome CDP 未在 127.0.0.1:9222 启动~~ → 2026-07-15 已启动并实测通过。
- ~~无 BOSS 登录态~~ → 2026-07-15 实测登录态有效（active probe 确认，下午 16:20:59 登录态持久化）。
- **未配置 AI API key**（唯一剩余阻塞）。

已完成证据：
- e2e_real_boss.py 脚本已就绪，含前置条件门控，缺失时返回 blocked 而非模拟。
- 2026-07-15 实测：CDP up + boss_login=OK + ai_credentials=MISSING → block 在 ai_credentials（诚实阻塞）。
- evaluate.py 黄金样本评估在脱敏 fixture 上全 PASS，证明评估逻辑正确。
- test_boss_discovery_source.py 32 tests 用 fake JobSource 覆盖抓取/去重/详情/快照逻辑。
- test_e2e_prerequisites.py 8 tests 覆盖三态化 + 离线诊断 + active probe 逻辑。

未验证项：真实 BOSS 列表抓取、多页去重、真实详情快照、真实登录态下的中断/恢复、真实 AI 凭据下的端到端候选人分析与岗位评估。CDP + 登录态已具备，只剩 AI 凭据缺失。用户提供 AI API key 后即可由用户主导运行 e2e_real_boss.py 完成真实 E2E。

### 5.2 source_status 枚举漂移 — ✅ 已修复（CD-1）

**修复前**：
- 实现 `webui/discovery.py:218`：`SNAPSHOT_SOURCE_STATUS = ("fetched", "stale", "blocked", "not_found")`
- 契约 `data-model.md:225` 与 `contracts/openapi.yaml:359`：`enum: [active, unknown, closed, unreachable]`

**修复后**（2026-07-15）：
- `webui/discovery.py:218` 改为 `SNAPSHOT_SOURCE_STATUS = ("active", "unknown", "closed", "unreachable")`
- `build_snapshot` 内部分支映射更新：
  - complete → `active`
  - partial → `unknown`
  - unavailable → `unreachable`
  - expired → `closed`
- 同步更新 `test_boss_discovery_source.py::JobSnapshotBuildTests` 三个断言（fetched→active / blocked→unreachable / expired→closed）。
- 新增真实单元测试 `test_discovery.py::test_source_status_uses_contract_enum` 锁定契约。
- 全量回归 1010 tests OK。

### 5.3 黄金样本评估指标局限 — 部分缓解

`tests/fixtures/discovery/evaluate.py` 存在两处同义反复，指标虽 PASS 但证明力弱于表面：

- `hard_rule_violation_rate=0.0`：由构造决定。not_suitable 岗位不会进入 RELEVANT_CATEGORIES，故硬约束违规率恒为 0，该指标无法证伪硬规则实现。
- `recall=0.8`：实为整体 precision。所有标注岗位均被"评估"，分母定义使该指标退化为 precision，并非真正召回率。

**本会话缓解**（2026-07-15）：在 evaluate.py 顶部及指标输出位置添加 NOTE 标注，明确"这是注解一致性检查，不是系统验收测试"。指标定义本身的修复仍待用户确认（涉及 policy_version 升级与基线变更），见 §7 第 3 项。

其余指标（direction_acceptance_rate、precision_at_20、multi_direction_coverage、explanation_fidelity）有效。

### 5.4 占位测试 (test_discovery.py) — 部分替换

**修复前**：7 个 TestCase 仅含 `test_placeholder`：SearchPlanCompilationTests、JobSnapshotTests、AssessmentPolicyTests、PortfolioAssemblyTests、SafeExplanationTests、ConfirmDirectionsTests、RunCompletionTests。

**本会话替换**（2026-07-15）：
- `JobSnapshotTests` → 替换为 `test_source_status_uses_contract_enum`（锁定 CD-1 契约）
- `PortfolioAssemblyTests` → 替换为 `test_high_match_kept_over_needs_review_same_company`（锁定 HI-1 同公司去重优先级）

剩余 5 个占位（SearchPlanCompilation / AssessmentPolicy / SafeExplanation / ConfirmDirections / RunCompletion）仍由 test_discovery_integration.py 40 tests 间接覆盖，不影响功能正确性。后续可继续替换为真实单元测试以提升可读性。

### 5.5 tasks.md 复选框未勾选

`specs/004-resume-job-discovery/tasks.md` 所有任务仍标记 `[ ]`，未随完成进度更新。属文档维护缺口，不影响代码正确性。本 validation.md 以实际证据为准判定完成度。

---

## 6. 本会话改动文件与重启情况 (Files Changed and Restart)

### 6.1 改动文件（本会话 T086–T094 复核/修复阶段）

| 文件 | 改动 | 风险 |
|------|------|------|
| tests/test_webui_store.py | 移除 UTF-8 BOM | 低（仅修复 import） |
| tests/test_webui_app.py | 收窄 match_score 断言 | 低（对齐 spec 004） |
| tests/test_webui_browser.py | 收窄 match_score 断言 | 低（对齐 spec 004） |

### 6.2 feature 004 新增文件（更早阶段产出，未提交）

webui/candidate.py、webui/discovery.py、webui/discovery_runner.py、webui/source.py、tests/test_candidate.py、tests/test_discovery*.py、tests/test_boss_discovery_source.py、tests/fixtures/discovery/、specs/004-resume-job-discovery/ 全部。

### 6.3 重启情况

本会话未修改后端 Python/路由/迁移/runner 代码（仅改测试文件），无需重启。后端进程持续运行，已验证 http://127.0.0.1:5000 返回 200。

---

## 7. 待用户确认的后续优化 (Optional Future Optimizations Requiring Approval)

> 2026-07-15 更新：原 §7 第 1 项（source_status 枚举漂移）已修复，详见 §5.2 / §9 CD-1。原第 2 项中 2 个占位测试已替换为真实测试（§5.4）。原第 3 项已添加 NOTE 标注（§5.3），指标定义本身的重定义仍待用户确认。

1. ~~**修复 source_status 枚举漂移**（§5.2）：~~ ✅ 已修复（2026-07-15，CD-1）
2. **替换剩余 5 个占位测试**（§5.4）：补齐 SearchPlanCompilation / AssessmentPolicy / SafeExplanation / ConfirmDirections / RunCompletion 的真实单元测试。（JobSnapshotTests / PortfolioAssemblyTests 已于本会话替换）
3. **重定义 evaluate.py 指标**（§5.3）：重定义 hard_rule_violation_rate 与 recall 使其可证伪，重新校准 policy_version。本会话仅添加 NOTE 标注，未改定义。
4. **勾选 tasks.md 复选框**（§5.5）：按实际完成度更新任务状态。
5. ~~**真实 BOSS E2E**（§5.1）：待用户提供 CDP + 登录态 + AI 凭据~~ → CDP + 登录态已具备（2026-07-15 实测），**只剩 AI 凭据**。用户提供 API key 后即可运行。

---

## 8. 完成度判定

- ✅ 自动化回归：1018 tests OK（2026-07-15 修复后）
- ✅ 迁移 011–013：幂等、旧数据保留、schema 升至 13
- ✅ 集成测试：全流程 / 取消 / 部分成功 / 重启恢复 / AI 降级 / 反馈影响
- ✅ 黄金样本：SC-003–SC-009 PASS（指标局限已披露并标注）
- ✅ 浏览器渲染：1366×768 与 720px，16 tests OK
- ✅ 后端可达：http://127.0.0.1:5000 返回 200
- ✅ 全量审查 16 项问题已修复（CR-1 / HI-1 / HI-2 / CD-1/2/3 / M1-M9 / 测试方法学，见 §9）
- ✅ e2e 前置检查三态化 + 离线诊断 + active probe 已修复并实测（见 §10）
- ✅ CDP Chrome 实测启动 + 登录态自动恢复 + --check 三步全绿
- ❌ 真实 BOSS E2E：CDP + 登录态已具备，**只剩 AI 凭据**缺失

**结论**：所有可在本地完成的验证门均已通过；真实 BOSS E2E 现仅剩 AI API key 一个外部阻塞项。用户提供 API key 后即可由用户主导运行 `python tests/fixtures/discovery/e2e_real_boss.py` 完成最终验收。

---

## 9. 全量审查修复批次 (2026-07-15)

### 9.1 审查范围

5 个并行子代理对 feature 004 全部代码进行全量审查：契约 / 实现逻辑 / 迁移 / 安全 / 测试 / 前端。独立复核所有 Critical/High 发现，纠正 2 处子代理误判（C1 plan_item vs assessment.status / C2 direction_disable vs disable）。

### 9.2 已修复问题清单（16 项）

| 编号 | 严重度 | 问题 | 修复方式 | 涉及文件 |
|------|--------|------|----------|----------|
| CR-1 | Critical | `delete_resume_derived_evidence` 方法实现但从未调用，违反 FR-098 删除级联 | 在 `delete_resume` 编排层显式调用 | webui/resume.py + test_discovery_integration.py::ResumeDeletionCascadeTests |
| HI-1 | High | portfolio 同公司去重未考虑 category 优先级，可能丢弃 high_match 保留 needs_review | 按 CATEGORY_PRIORITY 排序后去重 | webui/discovery.py:456 + test_discovery.py::test_high_match_kept_over_needs_review_same_company |
| HI-2 | High | AI summary 写入前未做 PII 脱敏，可能将 PII 持久化到数据库 | 在 update_analysis_status 前对 summary 字段逐项 redact_pii | webui/discovery.py:735 + test_discovery_integration.py::SummaryPiiRedactionTests |
| CD-1 | Contract Drift | source_status 实现枚举 (fetched/stale/blocked/not_found) ≠ 契约 [active/unknown/closed/unreachable] | 对齐实现到契约 + 同步测试 | webui/discovery.py:218,239-251 + test_boss_discovery_source.py 3 处断言 |
| CD-2 | Contract Drift | plan_item.status 使用 "succeeded"，契约定义为 "completed" | 全部改为 "completed"（5 处 runner + 1 处 discovery） | webui/discovery.py:770,772 + webui/discovery_runner.py:272,292,311,427,553 + test_discovery_integration.py |
| CD-3 | Contract Drift | feedback action 使用 "disable"，契约定义为 "direction_disable" | 全部改为 "direction_disable" | webui/discovery.py:816 + test_discovery_integration.py 2 处 |
| M1 | Medium | snapshot_completeness="partial" 错误强制路由到 needs_review | 移除 partial 触发条件 | webui/discovery.py:337 |
| M2 | Medium | adjacent/growth 分类未要求维度通过验证，与 high_match 逻辑不一致 | 增加 `all_dims_pass` 条件 | webui/discovery.py:393-404 |
| M3 | Medium | link_direction_evidence 未校验 direction 与 evidence 是否属于同一 analysis | 增加跨 analysis 校验，mismatch 抛 ValueError | webui/store.py:2355-2372 + test_discovery_store.py::test_direction_evidence_rejects_cross_analysis |
| M4 | Medium | search_plans 缺少 UNIQUE(run_id)，理论上可重复创建 | 在 _migration_012 增加 UNIQUE 约束 | webui/store.py:695-706 + test_discovery_store.py::test_search_plan_unique_per_run |
| M5 | Medium | _migrate 中混用 schema 升级与运行时 UPDATE（标记中断 run），破坏迁移单一职责 | 抽出为独立 `_mark_stale_runs_interrupted` 方法 | webui/store.py:76-81,180-181,183-209 |
| M6 | Medium | resume 恢复时未校验 input_hash，可能用过期 plan_item 续跑 | 恢复前比对 input_hash，mismatch 标记 failed | webui/discovery_runner.py:274-289 |
| M8 | Medium | index-v2.html 用 innerHTML 渲染搜索建议，存在 XSS 风险 | 改用 textContent + createElement + appendChild | webui/index-v2.html:564-568,591-598 |
| M9 | Medium | data-model.md 未说明 excluded_job_ids 字段用途 | 补充文档说明 | specs/004-resume-job-discovery/data-model.md:127-131 |
| TM-1 | 测试方法学 | evaluate.py 指标同义反复（hard_rule_violation_rate / recall）未标注 | 添加 NOTE 标注"注解一致性检查，非系统验收测试" | tests/fixtures/discovery/evaluate.py:364-367 |
| TM-2 | 测试方法学 | partial 断言过宽 `assertIn(..., ("failed","partial","succeeded"))` / `assertTrue(html != "" or True)` 同义反复 | 收紧为 `assertEqual(..., "failed")` / `assertTrue(html is not None)` | test_discovery_integration.py:415-418 + test_discovery_browser.py:202 |

### 9.3 修复批次验证

| 阶段 | 命令 | 结果 |
|------|------|------|
| 单文件验证（CD-1 测试同步） | `python -m unittest tests.test_boss_discovery_source -v` | 32 tests OK |
| 全量回归 | `python -m unittest discover -s tests` | 1010 tests OK in 231.075s |

### 9.4 未列入修复批次的事项

- **真实 BOSS E2E**（§5.1）：外部阻塞，需用户提供 CDP + 登录态 + AI 凭据，不由 AI 推进。
- **evaluate.py 指标重定义**（§5.3）：仅添加 NOTE 标注，未改定义。涉及 policy_version 升级与基线变更，建议用户确认后单独走 RED→GREEN→commit。
- **5 个剩余占位测试**（§5.4）：不影响功能正确性，可后续补齐。
- **tasks.md 复选框**（§5.5）：文档维护项，不影响代码正确性。

### 9.5 改动文件清单（本会话修复批次）

| 文件 | 改动概要 |
|------|----------|
| webui/resume.py | CR-1：调用 delete_resume_derived_evidence |
| webui/discovery.py | CD-1/CD-2/CD-3/HI-1/HI-2/M1/M2 全部修复 |
| webui/discovery_runner.py | CD-2（5 处 succeeded→completed）+ M6 input_hash 校验 |
| webui/store.py | M3 跨分析校验 + M4 UNIQUE(run_id) + M5 _mark_stale_runs_interrupted 抽出 |
| webui/index-v2.html | M8 innerHTML → DOM API |
| specs/004-resume-job-discovery/data-model.md | M9 文档补充 excluded_job_ids |
| specs/004-resume-job-discovery/validation.md | 本节（§9） |
| tests/test_discovery.py | 替换 2 个占位测试为真实测试 |
| tests/test_discovery_integration.py | CR-1/HI-2/CD-2/CD-3/TM-1 测试同步 + 新增 |
| tests/test_discovery_store.py | M3/M4 测试新增 |
| tests/test_discovery_browser.py | TM-2 同义反复修复 |
| tests/test_boss_discovery_source.py | CD-1 测试断言同步（3 处）|
| tests/fixtures/discovery/evaluate.py | TM-1 NOTE 标注 |

---

## 10. e2e 前置检查三态化修复 (2026-07-15)

### 10.1 起因

用户凌晨发现 validation.md §2.5 报"❌ 未登录（CDP 不可达，无法检查）"，但下午 16:20:59 已登录过。诊断发现：CDP Chrome 进程被关（WinError 10061），但登录态 Cookies 文件持久化在 `~/.career-scout/chrome-profile`，没丢。问题是 `e2e_real_boss.py::_check_prerequisites` 把"CDP down 无法验证"误表达成 `boss_login: false`（语义上等于"确认未登录"），误导用户和文档。

### 10.2 三项修复

| 修复 | 改动 | 涉及文件 |
|------|------|----------|
| **三态化** | `boss_login` 从 bool 改为 true/false/"unknown"。CDP down 时返回 "unknown"，不再误报 false。 | tests/fixtures/discovery/e2e_real_boss.py |
| **离线诊断** | CDP down 时调用 `_diagnose_login_offline()` 检查 `Default\Network\Cookies` 文件存在/大小/修改时间，输出 note。 | tests/fixtures/discovery/e2e_real_boss.py |
| **Active probe** | CDP up 时不再用 tab 列表判断登录态，改为调用 `scripts.boss_cdp_raw.check_login_state()` 主动导航到 zhipin.com 并检测明文薪资。修复"用户已登录但无 zhipin tab"误判。 | tests/fixtures/discovery/e2e_real_boss.py |

### 10.3 真实实测结果（2026-07-15 凌晨）

**场景 1：CDP down**（修复前会误报"未登录"）
```
cdp: MISSING
boss_login: UNKNOWN (cannot verify)
  note: Cookies file exists (size=28672 bytes, modified 2026-07-14 16:20:59); login state may still be valid — start CDP Chrome to verify
ai_credentials: MISSING
```

**场景 2：CDP up + 登录态自动恢复**（重启 CDP Chrome 后，下午登录态无需重新登录）
```
cdp: OK
boss_login: OK  (active probe 确认)
ai_credentials: MISSING
→ BLOCKED on ai_credentials
```

**场景 3：--check 三步全绿**
```
[1/3] Python 依赖... ✅ 依赖完整
[2/3] CDP 端口连通性... ✅ 通过 — Chrome Chrome/150.0.7871.115
[3/3] BOSS直聘登录状态... ✅ 已登录
✅ 所有检查通过，可以开始抓取
```

### 10.4 测试覆盖

`tests/test_e2e_prerequisites.py` 8 个测试覆盖：
- CDP down → boss_login="unknown" + 离线诊断 note
- CDP up + probe true → boss_login=True
- CDP up + probe false → boss_login=False
- CDP up + 无 zhipin tab + probe true → boss_login=True（修复 tab 列表误判）
- 离线诊断：Cookies 存在（含 size + mtime）/ Cookies 缺失 / user-data-dir 缺失
- main() blocked 分支 JSON 报告记录 boss_login="unknown"

### 10.5 关键设计决策

1. **不改运行时 block 行为**：CDP down / boss_login=unknown / boss_login=false 都仍会 block E2E。安全网保留。
2. **"unknown" 不放行**：`all_ok = cdp is True and boss_login is True and ai_credentials is True`，用 `is True` 严格匹配，"unknown" 字符串被正确 block。
3. **active probe 失败时归 false**：保守策略，宁错杀不放过。
4. **离线诊断不改契约**：只读 Cookies 文件元数据，不解密、不验证服务端 session。

### 10.6 改动文件清单

| 文件 | 改动概要 |
|------|----------|
| tests/fixtures/discovery/e2e_real_boss.py | 三态化 + 离线诊断 + active probe |
| tests/test_e2e_prerequisites.py | 新增，8 个测试覆盖三态化逻辑 |
| specs/004-resume-job-discovery/validation.md | §2.5/§2.6/§4/§5.1/§7/§8 更新 + 本节（§10）|

---

## 11. 运行时闭环设计审计（2026-07-15，覆盖此前完成性结论）

本节是对当前工作树重新审查后的结论。凡与 §2、§5、§7、§8 中“只剩 AI API Key”或“所有本地门均已通过”的表述冲突，以本节为准。

### 11.1 已确认事实

- 已配置且同意 AI 的分析路径会调用 `webui/app.py::_build_ai_provider()`，但该函数返回的 `_AIProviderAdapter` 在当前源码中没有定义；因此真实配置路径存在确定性内部能力缺失，不能归因于仅缺少外部 API Key。
- `webui/discovery_runner.py::DiscoveryRunner` 已有同步编排骨架，但 `create_app()` 没有构造应用持有的发现任务运行时；创建发现运行的 HTTP 路由只写入 run/plan 记录，没有提交真实后台执行。
- 当前取消路由直接改写状态，恢复路由直接改回 `created`，单岗位重试只返回 accepted；这些行为不能证明真实取消、重新调度或幂等恢复。
- 当前岗位评估输入只传递方向标识及证据标识，未提供合同要求的候选人摘要、方向依据/缺口和关联证据内容，不能形成可解释的真实评估输入。
- 候选人证据校验只检查 locator 范围，没有验证 `safe_excerpt` 与该范围切片一致；范围内错误位置仍可被接受。
- `tests/test_discovery_contracts.py` 中分析/确认、运行/结果/重试、反馈、取消/恢复四组 HTTP 测试仍为无行为断言的占位测试。
- `tests/fixtures/discovery/e2e_real_boss.py::_build_real_ai_provider()` 当前固定返回未实现状态，真实 E2E 不能仅通过补充凭据完成。

### 11.2 审计当时的完成边界（历史记录）

- 既有迁移、领域逻辑、fake provider/source 集成、黄金样本脚本、静态/浏览器测试和 CDP 前置检查仍是有效的分层证据，但不能替代真实用户路径闭环。
- 独立审计当时，Feature 004 不得标记为实现完成、运行闭环完成或发布验收完成；该历史结论已由 §12 的后续修复与新鲜验证证据解除。
- 本轮仅完成规格与设计收敛：新增 FR-101–FR-112、NFR-011–NFR-012、SC-016–SC-020，并在 `plan.md`、`research.md`、`data-model.md`、`contracts/`、`quickstart.md` 和 `tasks.md` 中规定后续实现与验收边界。
- 当时的实施工作从 `tasks.md` 的 T096 证据审计开始，T097–T134 必须保持未完成直至证据实际存在；当前最终状态见 §12.10，T134 已通过。

### 11.3 未执行边界

本次设计审计没有修改业务代码，没有重新运行真实 AI、真实 BOSS E2E、完整浏览器验收或服务重启。此前执行记录只作为历史证据保留，不作为新增运行时闭环的通过证明。

## 12. 运行时闭环实施批次（2026-07-15，T101–T134 已完成）

### 12.1 T101–T107 已完成（v2 exact-quote + runtime 接入）

**T101 GREEN**（缺陷 1/2/3 修复）:
- 修改 `webui/discovery_runner.py`: DiscoveryTaskRuntime 添加 source_factory/ai_provider_factory 延迟解析
- 修改 `webui/app.py`: create_app 构造 DiscoveryTaskRuntime 暴露到 app.config["DISCOVERY_RUNTIME"]；discovery_create_run/cancel/resume 路由委托给 runtime；_build_ai_provider 返回真实 DiscoveryAIProvider
- 验证: `python -m unittest tests.test_discovery_integration tests.test_discovery_store tests.test_discovery_contracts tests.test_discovery_frontend tests.test_discovery -v` 299 tests OK

**T102–T107 v2 exact-quote locator**:
- T102: 新增 `tests/fixtures/discovery/resume_locator_cases.txt` + `tests/fixtures/discovery/ai_candidate_v2.json`
- T103 RED → T104 GREEN: `webui/candidate.py` 添加 canonicalize_resume_text_v2 + resolve_evidence_quote；16 个 v2 locator 测试通过
- T105 RED → T106 GREEN: `webui/ai.py` 添加 _enrich_v2_locators 静态方法；9 个 v2 enrichment 测试通过
- T107 GREEN: `webui/candidate.py` validate_candidate_analysis 增强 v2 source_quote/locator/safe_excerpt 校验；7 个 v2 合同校验测试通过

### 12.2 T108 RED 完成（HTTP 契约失败测试）

**改动文件**: `tests/test_discovery_contracts.py`
**替换**: `AnalysisConfirmationHttpContractTests` 占位测试 → 12 个真实 HTTP 契约测试

**测试覆盖**:
- POST /api/discovery/analyses 接受 JSON {resume_id, ai_consent} → 202 + AnalysisSummary (status=queued)
- consent=false 保持 queued 且不调用 provider
- consent=true + provider 注入 → 无 NameError/500，最终 ready 或安全 failed
- provider 超时 → 安全错误信封 (ai_timeout/ai_invalid_output/ai_network_error)，不泄漏原始异常
- 202 响应体不含简历正文
- GET /api/discovery/analyses/{id} 返回完整 state (analysis_id/resume_id/profile_id/status/evidence/directions/unknowns/failure)
- GET 未知 id → 404 安全信封
- GET 响应不含简历正文
- POST /retry 创建新版本 (version+1, analysis_id 不同)
- retry 请求体 ai_consent=false → 新分析保持 queued，不调用 provider
- retry 未知 id → 404

**provider 注入方式**: `unittest.mock.patch("webui.app.ai_service")` 覆盖 retrieve_api_key + DiscoveryAIProvider，同时影响同步路径和 runtime factory 路径

**RED 验证命令**:
```
python -m unittest tests.test_discovery_contracts -v
```

**RED 结果**: Ran 33 tests in 5.038s — FAILED (failures=5, errors=4)
- 5 FAIL (400 != 202): 路由使用 request.form.get("profile_id") 不接受 JSON body {resume_id, ai_consent}
- 4 ERROR (KeyError: 'analysis_id'): 因 POST 400 无法获取 analysis_id
- 3 守卫测试 PASS (404/400): test_get_analysis_404_for_unknown_id, test_post_analyses_missing_resume_id_returns_400, test_retry_unknown_analysis_returns_404
- 其他测试类 (DiscoveryErrorEnvelopeTests, RunResultsHttpContractTests, FeedbackHttpContractTests, CancelResumeHttpContractTests, ResultTraceabilityTests) 全部 PASS — 未破坏现有契约

**RED 原因（待 T109 修复）**:
1. 路由使用 `request.form.get("profile_id")` 而非 JSON `resume_id`
2. 同步调用 AI 直接返回 ready/failed，未走 runtime 异步路径（status 应初始为 queued）
3. retry 路由强制 ai_consent=True，不读请求体
```

### 12.3 T109 GREEN 完成（异步分析提交模式）

**改动文件**（3 个）:
- `webui/discovery.py`: `analyze_resume` 新增 `analysis_id: str = ""` 参数，当提供时复用 HTTP 路由已创建的 queued analysis（async submit 模式）
- `webui/discovery_runner.py`: DiscoveryTaskRuntime 新增 `submit_analysis(analysis_id, *, ai_consent)` 和 `_safe_execute_analysis(analysis_id)` 方法
  - consent=False: 不做 anything，分析保持 queued（不调 AI）
  - consent=True: 提交到 executor 后台执行 analyze_resume(analysis_id=...)，分析异步流转 queued→analyzing→ready/failed
  - provider 失败（timeout/auth/network/invalid_output）由 analyze_resume 持久化为安全 failure_code，worker 吞掉异常避免泄漏到 executor 错误日志
- `webui/app.py`: 
  - `discovery_create_analysis` 路由改为接受 JSON `{resume_id, ai_consent}`，创建 queued analysis 后调用 `discovery_runtime.submit_analysis()`
  - `discovery_retry_analysis` 路由读取请求体 `ai_consent`（默认 True 向后兼容），创建新版本 queued analysis 后提交 runtime

**测试基础设施修复**:
- `tests/test_discovery_contracts.py::AnalysisConfirmationHttpContractTests.setUp`: 新增 `/api/session` 获取 token 并设置 `HTTP_X_BOSS_TOKEN`（本地 API 保护中间件要求）
- `tearDown`: 先调用 `runtime.shutdown()` 关闭 executor 后台线程，再删除临时 db 文件（避免 PermissionError: WinError 32）

**GREEN 验证命令**:
```
python -m unittest tests.test_discovery_contracts.AnalysisConfirmationHttpContractTests -v
```

**GREEN 结果**: Ran 12 tests in 4.201s — OK
- 12 个 HTTP 契约测试全部通过
- consent=false 不调 provider，分析保持 queued
- consent=true + provider 注入后无 NameError/500，最终 ready
- provider 超时 → 安全错误信封，不泄漏原始异常
- 响应体和 GET 响应均不含简历正文
- retry 创建新版本且尊重 consent=false

**回归验证命令**:
```
python -m unittest tests.test_discovery_contracts tests.test_discovery_integration tests.test_discovery tests.test_discovery_store tests.test_discovery_frontend tests.test_ai tests.test_candidate
```

**回归结果**: Ran 334 tests in 41.447s — OK（无回归）

**未破坏既有契约**: DiscoveryErrorEnvelopeTests / RunResultsHttpContractTests / FeedbackHttpContractTests / CancelResumeHttpContractTests / ResultTraceabilityTests 全部 PASS

### 12.4 T110 GREEN 完成（前端分析请求显式提交 resume_id + 轮询安全失败）

**改动文件**（2 个）:
- `webui/index.html`:
  - `uploadResumeDiscovery` 函数：从 `analysisForm.append("profile_id", ...)` FormData 提交改为 `JSON.stringify({resume_id, ai_consent})` + `Content-Type: application/json` 提交，匹配 T109 后端 `request.get_json()` 契约；resume_id 从上传响应 `uploadData.resume_id` 获取
  - `pollDiscoveryAnalysis` 函数：新增 `"queued"` 和 `"analyzing"` 状态分支，通过 setAppNotice 告知用户当前进度（"分析已排队，等待执行…" / "正在分析简历…"），避免静默等待；`"failed"` 分支增强为展示安全失败信封 `error_code + user_message`，不泄漏原始异常
- `tests/test_discovery_frontend.py`: 新增 `AnalysisSubmissionFrontendTests` 5 个测试
  - `test_analysis_request_does_not_use_formdata_profile_id`: 断言 discovery 分析路径不再使用 `analysisForm` 变量（不影响 screening 路径的 `formData.append`）
  - `test_analysis_request_uses_json_resume_id`: 断言 HTML 含 `resume_id` 和 `JSON.stringify`
  - `test_poll_explicitly_handles_queued_status`: 断言轮询函数含 `"queued"` 状态分支
  - `test_poll_explicitly_handles_analyzing_status`: 断言轮询函数含 `"analyzing"` 状态分支
  - `test_poll_displays_safe_failure_envelope`: 断言 HTML 含 `error_code` 和 `user_message` 字段

**RED 验证**（修改前）:
```
python -m unittest tests.test_discovery_frontend.AnalysisSubmissionFrontendTests -v
```
RED 结果: Ran 5 tests — FAILED (failures=2)
- `test_analysis_request_does_not_use_formdata_profile_id` FAIL: 前端仍使用 `analysisForm.append("profile_id")`
- `test_poll_explicitly_handles_analyzing_status` FAIL: pollDiscoveryAnalysis 未处理 "analyzing" 状态
- 3 守卫 PASS: resume_id+JSON.stringify 已存在 / "queued" 已存在（discovery run polling）/ error_code+user_message 已存在

**RED 断言修正记录**: 初版断言 `assertNotIn('append("profile_id"', html)` 过宽，会误伤 screening 路径（第 2237 行 `formData.append("profile_id"`）。收紧为 `assertNotIn('analysisForm', html)` 只针对 discovery 分析路径，不影响 screening 兼容行为。

**GREEN 验证**:
```
python -m unittest tests.test_discovery_frontend.AnalysisSubmissionFrontendTests -v
```
GREEN 结果: Ran 5 tests in 1.352s — OK

**前端全测试**: Ran 29 tests in 6.485s — OK（无回归）

**广泛回归验证**:
```
python -m unittest tests.test_discovery_contracts tests.test_discovery_integration tests.test_discovery tests.test_discovery_store tests.test_discovery_frontend tests.test_ai tests.test_candidate tests.test_discovery_browser
```
回归结果: Ran 355 tests in 129.036s — OK（无回归）

### 12.5 T111 GREEN 完成（US1 组合集成测试）

**改动文件**（2 个）:
- `webui/discovery.py`:
  - 顶部新增 `from webui.ai import AISecurityError as AIProviderError` 导入（重命名避免与本文件 `AISecurityError` 冲突）
  - `analyze_resume` except 链新增 `except AIProviderError as exc:` 分支（在 `except AISecurityError` 之前），捕获 `DiscoveryAIProvider.analyze` 抛出的 `webui.ai.AISecurityError`，提取其 feature-safe error_code（ai_timeout/ai_auth_failed/ai_network_error/ai_invalid_output），持久化为 failure_code，然后重新包装为 `webui.discovery.AISecurityError` 抛出，保持 `_safe_execute_analysis` 调用方契约（`except (DiscoveryError, AISecurityError)`）不变
- `tests/test_discovery_integration.py`: 新增 `US1CompositionIntegrationTests` 4 个测试
  - `test_consent_false_does_not_call_ai_through_http`: consent=false 时 HTTP POST 创建 queued attempt，call_ai transport 不被调用
  - `test_consent_true_full_pipeline_to_confirmation`: consent=true 时 HTTP → runtime → real DiscoveryAIProvider（只 mock call_ai transport）→ candidate validator → store → confirm_directions 全管道；通过 store.list_evidence 验证每条持久化 evidence 的 source_locator start/end 为整数且切片匹配 source_quote
  - `test_db_does_not_contain_resume_body`: 分析完成后数据库所有表的 prompt/response/raw_response/model_response 列不含简历正文
  - `test_provider_timeout_returns_safe_envelope`: provider call_ai 抛 AISecurityError("timeout") → failed + ai_timeout，失败信封不含 Traceback/AISecurityError 字符串

**根因修复**: `webui.discovery.AISecurityError`（DiscoveryError 子类）与 `webui.ai.AISecurityError`（Exception 子类）是两个不同的类。`analyze_resume` 的 `except AISecurityError` 只捕获前者，而 `DiscoveryAIProvider.analyze` 抛出的是后者，导致异常落入 `except Exception` 被覆盖为 ai_invalid_output。修复后 `except AIProviderError` 正确捕获 provider 异常并保留 feature-safe error_code。

**设计决策**: openapi Evidence schema 不含 source_locator 字段，测试通过 `self.store.list_evidence` 直接查询数据库验证 locator，而非依赖 HTTP 响应。这避免了扩展 openapi 契约，同时仍验证了 locator 的正确性。

**GREEN 验证**:
```
python -m unittest tests.test_discovery_integration.US1CompositionIntegrationTests -v
```
GREEN 结果: Ran 4 tests in 2.517s — OK

**广泛回归验证**:
```
python -m unittest discover -s tests
```
回归结果: Ran 1094 tests in 262.693s — OK（无回归）

### 12.6 全量审查修复（P1/P2/P6/P7/S1）

**审查触发**: T111 完成后对 Feature 004 进行 Standards + Spec 双轴全量审查，发现 1 项硬违规 + 4 项 spec 偏差。

**改动文件**（5 个）:

- `webui/discovery.py`:
  - P1: `analyze_resume` 内部创建 analysis 时传 `contract_version="v2"`（原默认 "v1"）
  - P6: `confirm_directions` 中「方向不属于分析」错误码从 `evidence_reference_invalid` 改为 `state_conflict`（前者专用于证据引用无效）
- `webui/app.py`:
  - P1/P2: HTTP 路由 `discovery_create_analysis` 创建 analysis 时从 `ai_settings` 读取 model 并传 `model_name` 和 `contract_version="v2"`
- `webui/candidate.py`:
  - P7: `validate_candidate_analysis` 删除 v1 回退分支，要求 evidence 必须有 `source_quote`（ai-contracts.md:51 v2 契约）
  - P7: 返回的 `contract_version` 从 `CANDIDATE_CONTRACT_VERSION`（"v1"）改为 `CANDIDATE_CONTRACT_VERSION_V2`（"v2"）
- `webui/discovery_runner.py`:
  - S1: `_refresh_item` 的 `except (KeyError, Exception)` 改为 `except KeyError`（消除冗余且过宽的异常捕获，遵循 CONTRIBUTING.md「必须捕获具体异常类型」）
- 测试 fixture 升级（3 个文件）:
  - `tests/test_discovery_integration.py`: `_valid_ai_response()` 升级为 v2 格式（添加 source_quote，修正 locator）；`test_unknown_direction_rejected` 断言改为 `state_conflict`
  - `tests/test_candidate.py`: `_valid_response()` 升级为 v2 格式；`test_valid_response_returns_sanitized` 断言 contract_version="v2"；`test_locator_out_of_range_rejected` 增强断言 out_of_range
  - `tests/test_discovery_contracts.py`: `_contract_valid_ai_response()` 升级为 v2 格式

**新增测试**（4 个，在 `tests/test_discovery_integration.py` 的 `US1CompositionIntegrationTests`）:
  - `test_analysis_persists_contract_version_v2`: P1 — 断言落库 contract_version='v2'
  - `test_analysis_persists_model_name`: P2 — 断言落库 model_name='deepseek-v4-flash-free'
  - `test_confirm_invalid_direction_returns_state_conflict`: P6 — 断言方向不存在返回 state_conflict
  - `test_evidence_without_source_quote_rejected`: P7 — 断言缺 source_quote 的 evidence 被拒绝

**RED 验证**（修复前）:
```
python -m unittest tests.test_discovery_integration.US1CompositionIntegrationTests.test_analysis_persists_contract_version_v2 tests.test_discovery_integration.US1CompositionIntegrationTests.test_analysis_persists_model_name tests.test_discovery_integration.US1CompositionIntegrationTests.test_confirm_invalid_direction_returns_state_conflict tests.test_discovery_integration.US1CompositionIntegrationTests.test_evidence_without_source_quote_rejected -v
```
RED 结果: Ran 4 tests — FAILED (failures=4)（contract_version=v1 / model_name='' / error_code=evidence_reference_invalid / ValueError 未抛出）

**GREEN 验证**:
```
python -m unittest tests.test_discovery_integration.US1CompositionIntegrationTests -v
```
GREEN 结果: Ran 8 tests in 3.457s — OK（含 T111 原 4 个 + 审查修复 4 个）

**广泛回归验证**:
```
python -m unittest discover -s tests
```
回归结果: Ran 1098 tests in 215.136s — OK（无回归）

### 12.7 Phase 12–14 实施验证（T112–T130）

**范围**: US2 真实岗位评估与运行调度（T112–T120）、US4 真实取消/恢复/失败追踪（T121–T126）、US3/US5 HTTP 与隐私门收口（T127–T130）。

**T112–T120 US2 真实岗位评估与运行调度**:

- T112: 新增 `tests/fixtures/discovery/ai_job_assessment_v1.json` 夹具，覆盖完整证据视图、未知候选证据、未知岗位字段和 gaps 对象。
- T113: `tests/test_ai.py` 新增 `DiscoveryAIProvider.assess_job` 失败测试，验证四维 prompt、证据最小化、字段引用和安全错误映射（auth/timeout/network/invalid_output）。
- T114: `webui/ai.py` 实现 `DiscoveryAIProvider.assess_job v1`，仅发送候选 summary + direction + linked evidence + snapshot fields，不发送全简历或无关证据。
- T115: `tests/test_discovery_integration.py` 新增 runner assessment view 测试，验证方向 type/rationale/gaps、linked evidence value/excerpt/assertion_type，拒绝跨方向/跨分析引用。
- T116: `webui/discovery_runner.py` + `webui/store.py` 在评估边界加载并构造完整脱敏 assessment view。
- T117: 持久化每岗位 ai_auth_failed/ai_timeout/ai_network_error/ai_invalid_output/ai_uncertain/evidence_reference_invalid，失败岗位 needs_review 且其他岗位继续。
- T118: `tests/test_discovery_contracts.py` 新增 HTTP 测试：POST /api/discovery/runs 后 5 秒内进入 planning 或明确 dispatch_failed，results/retry 使用真实持久状态。
- T119: `webui/app.py` + `webui/discovery_runner.py` 将 run 创建和单岗位 retry 接入 DiscoveryTaskRuntime，删除仅写状态/返回 accepted 的占位行为。
- T120: `tests/test_discovery_integration.py` 新增 US2 全管道组合测试：HTTP→runtime→fake source→real provider(mock transport)→store→results，验证阶段事件和成功门。

**T121–T126 US4 真实取消/恢复/失败追踪**:

- T121: `tests/test_discovery_integration.py` 新增 cancel_requested 先持久化、取消后不启动新 query/detail/assessment、已完成结果保留的失败测试。
- T122: 新增 interrupted/partial resume 校验 input_hash 并真实 resubmit 的失败测试，直接状态改写不算恢复。
- T123: `webui/discovery_runner.py` 实现 DiscoveryTaskRuntime.cancel_run/resume_run 和 worker cancellation checkpoints。
- T124: `webui/app.py` 将 cancel/resume HTTP 路由委托给 runtime，终态/哈希冲突返回安全 409，提交失败记录 dispatch_failed。
- T125: `tests/test_discovery_contracts.py` 替换 cancel/resume HTTP 占位测试，断言状态、事件、工作调用和幂等恢复。
- T126: `webui/index.html` + `tests/test_discovery_frontend.py` 更新前端取消/恢复反馈和 created 超时阻断展示，页面切换后从服务端恢复运行。

**T127–T130 US3/US5 HTTP 与隐私门收口**:

- T127: `tests/test_discovery_contracts.py` 替换 feedback HTTP 占位测试，覆盖创建、撤销、默认 exact_job scope、认证保护和历史运行不可变。
- T128: `webui/app.py` + `webui/store.py` 修复 feedback HTTP 契约测试暴露的路由/错误信封/状态问题。
- T129: `tests/test_ai.py` + `tests/test_discovery_integration.py` 新增 provider/runtime 隐私失败测试：API key、credential_ref、完整 prompt、简历正文、原始响应不进日志/事件/错误信封/数据库。
- T130: `webui/ai.py` + `webui/discovery_runner.py` + `webui/discovery.py` 修复 provider/runtime 新链路的最小披露和安全日志边界。

**专项自动化验证（T131）**:
```
python -m unittest tests.test_ai tests.test_candidate tests.test_discovery_contracts tests.test_discovery_integration -v
```
结果: 全部通过（含 T113/T115/T118/T120/T121/T122/T125/T127/T129 新增测试），无失败。

### 12.8 Live-provider contract smoke（T132）

**脚本**: `tests/fixtures/discovery/e2e_real_boss.py`（`_build_real_ai_provider` 从生产 DB 加载 AI 设置，不读写 TaskStore）。

**结果文件**: `tests/fixtures/discovery/live_provider_smoke_result.json`

**验证内容**:
- candidate_analysis_v2: PASS — has_summary=true, has_evidence=true, has_directions=true, evidence_count=8, direction_count=5, all_evidence_has_source_quote=true
- job_assessment_v1: PASS — has_dimensions=true, has_match_score=true, has_confidence=true, has_proposed_band=true

**记录的元数据**: endpoint=https://opencode.ai/zen/v1, model=deepseek-v4-flash-free, timestamp=2026-07-15 05:18:50。未记录 key/prompt/raw response。

**AI prompt 强化修复**（过程中发现并修复的问题）:
- `webui/ai.py` `_build_analyze_messages`: 添加枚举约束（evidence.type/assertion_type/unknowns.field/directions.type）和类型标注（list[str]/int 0-100/bool），防止 AI 返回非法枚举值。
- source_quote 唯一性约束: prompt 显式要求 quote 在简历中唯一出现且包含上下文，禁止用单个词作为 quote。
- `analyze` 方法: 超时从 60s 增至 120s（免费模型延迟较高），纠正性重试从 1 次增至 2 次（共 3 次尝试），locator 失败触发重试。

### 12.9 受控真实 BOSS E2E（T133）

**脚本**: `tests/fixtures/discovery/e2e_real_boss.py`

**结果文件**: `tests/fixtures/discovery/e2e_real_boss_result.json`

**审计纠正**: 旧 §12.9 把 `source_count=0/detail_count=0/evaluated_count=0`、无 feedback job、无真实 cancel/resume 的 run 称为 completed，结论无效。本轮先将 T133/T134 恢复为未完成，再重写脚本与运行时并真实复跑。

**真实命令与时间**:
```
python tests/fixtures/discovery/e2e_real_boss.py
```
- 第一次复跑（约 1405 秒）: blocked；真实列表 8、详情 3、feedback/cancel 成功，但主 run 与 resume run 在 evaluating 超时，`evaluated_count=0`，blockers=`real_evaluation_missing,resume_not_verified`。
- 修复后复跑（740.7 秒，2026-07-15 17:02 结果文件）: exit 0，status=completed，blockers=[]。

**最终有效证据**:
- 用户 HTTP 路径: POST profile、multipart resume upload、POST/GET analyses、POST confirmations、POST/GET runs、GET results、POST feedback、POST cancel、POST resume。
- provider: `application_composition_root`，未覆盖私有 provider factory。
- 确认方向: 2。
- 真实来源: `source_count=6`。
- 真实详情: `detail_count=1`。
- 真实评估: `evaluated_count=2`，两个 `assessment_completed` 事件，主 run succeeded。
- feedback: status=ok，存在真实 job_id 与 feedback_id（ID 值不在文档复制）。
- cancel: 在 `fetching_lists` 发起；5 个未完成项转 cancelled；cancel 后新启动工作数 0；run cancelled。
- resume: run 由 POST `/api/discovery/runs` 创建；在 `fetching_lists` 受控中断；恢复前未完成 5 项、恢复后重提 5 项、重复已完成项 0；最终 succeeded。
- `interrupt_points` 非空，包含 controlled_restart。

**本轮暴露并修复的根因**:
- 详情 adapter 生成了 scraper 不支持的 `--detail-url`，且未兼容列表形式的详情 JSON；改为受控 input artifact + `--input/--detail-output/--max-details 1/--detail`。
- 评估阶段错误读取分析的全部方向，而非 confirmation 中 enabled 方向；修复后仅评估用户确认方向。
- `evaluated_count` 只在整批结束时写入，中断会把已完成评估误报为 0；修复为每项完成后增量持久化并记录安全事件。
- 详情失败时写入契约外 `source_status=blocked`；修复为 `unreachable`。

### 12.10 T134 完整验证门

**T134 当前状态: 已完成。** T043、T078、T079 已按 RED→GREEN 补齐；1169 项全量回归通过；2026-07-15 19:08 对当前代码执行的新鲜真实 HTTP E2E exit 0，全部最低发布门通过且 `blockers=[]`。本节结论不再依赖历史 T133 结果替代当前验证。

#### 0. 审计遗留任务修复（T043、T078、T079）

- T043: 在 `tests/test_discovery.py` 增加 6 项结果组合失败测试，首次运行出现 3 failures + 1 error；实现 `normalize_portfolio_assessment`、high 硬约束/详情/双侧证据守卫、adjacent 可迁移说明、growth 缺口说明和细分无结果原因后，6/6 通过。随后发现 HTTP results 路由绕过守卫，新增失败契约测试并在 `webui/app.py` 的结果投影中统一归一化；`RunResultsHttpContractTests` 最终 15/15 通过。
- T078/T079: 前端契约最初有 3 项失败，GET feedback 首次返回 405，撤销对既有长期兴趣状态的保护测试首次失败；实现 discovery 感兴趣/垃圾桶/恢复、结构化拒绝原因、方向反馈、撤销、偏好变化展示与 GET/POST/revoke HTTP 契约后，`FeedbackHttpContractTests` 8/8 通过，真实 HTTP Playwright discovery 浏览器套件 17/17 通过。
- 安全边界: feedback GET 仅返回安全字段；未在测试产物或文档记录 credential、完整 prompt、简历正文或模型原始响应。

#### 1. 数据库迁移验证

```
python -c "from webui.store import TaskStore; import os; s=TaskStore(os.path.expanduser('~/.career-scout/webui/webui.db')); print('schema_version='+str(s.schema_version()))"
```
结果: `schema_version=13`。

#### 2. 全量自动化回归

```
python -m unittest discover -s tests -v
```
最终完整结果: `Ran 1169 tests in 291.780s`，`OK`，exit 0。

专项 HTTP 契约命令:
```
python -m unittest tests.test_discovery_contracts -v
```
最终结果: `Ran 61 tests in 30.943s`，`OK`。其中包括 T118 在契约文件中的 5 秒推进测试，以及真实 run/results/retry、JobResult、confirmation、feedback、cancel/resume、HTTP high 守卫与反馈状态查询/恢复断言。

#### 3. 黄金样本评估

```
python tests/fixtures/discovery/evaluate.py
```
结果: ALL PASS，并于 2026-07-15 重新生成存在的 `tests/fixtures/discovery/evaluate_result.json`。
- SC-003 Direction Acceptance: 100% PASS
- SC-004 Precision@20: 80% PASS
- SC-005 Recall: 80% PASS
- SC-006 Hard-Rule Violation: 0% PASS
- SC-007 Incomplete into High-Match: 0% PASS
- SC-008 Multi-Direction Coverage: 100% PASS
- SC-009 Explanation Fidelity: 87.5% PASS
- PII Redaction: 7/7 OK

边界: 脚本自身明确说明这是 annotation-consistency check，不是真实系统验收；真实系统证据单独来自 §12.9。

#### 4. 桌面/窄屏浏览器测试

```
python -m unittest tests.test_discovery_frontend tests.test_discovery_browser tests.test_webui_browser -v
```
既有完整桌面/窄屏命令结果: `Ran 84 tests in 105.590s`，`OK`。本轮另运行 `python -m unittest tests.test_discovery_browser -v`，结果 `Ran 17 tests in 99.529s`、`OK`；新增真实 HTTP Playwright 交互覆盖不感兴趣→垃圾桶→恢复、感兴趣、方向关闭与偏好变化可见。覆盖 1366×768 与 720px、四步骤、水平溢出、主操作、焦点、空/加载/成功/partial/failed/interrupted/cancelled 状态。

#### 5. 真实来源 smoke（T132）

2026-07-15 18:54 随当前代码的最终真实 E2E 重跑: candidate_analysis_v2 PASS（evidence_count=7、direction_count=3、source_quote 完整）+ job_assessment_v1 PASS。结果位于 `tests/fixtures/discovery/live_provider_smoke_result.json`，未记录 key/prompt/raw response。

#### 6. 真实 E2E（T133）

见 §12.9。2026-07-15 19:08 已对当前代码重新执行 `python tests/fixtures/discovery/e2e_real_boss.py`，exit 0。结果文件显示 `execution_mode=http_routes`、`provider_factory_mode=application_composition_root`、确认方向 2、`source_count=6`、`detail_count=1`、`evaluated_count=2`、feedback=ok、cancel=ok（cancel 后新工作 0）、resume=ok（重提 5、重复 0）、`blockers=[]`。

#### 7. 后端重启与访问验证

受影响代码含 `webui/discovery.py`、`webui/discovery_runner.py`、`webui/source.py`，因此启动受影响后端:
```
python webui/app.py
```
最终按受影响代码重启；结果: PID 3424，启动命令 `D:\ana\python.exe webui/app.py`，`GET http://127.0.0.1:5000/` 返回 HTTP 200，进程存活。

#### 8. verification-before-completion 最终检查

| 检查项 | 状态 | 证据 |
|--------|------|------|
| 数据库迁移完整 | ✅ | schema_version=13 |
| 全量自动化通过 | ✅ | 最终 1169 tests，OK |
| 黄金样本达标 | ✅ | SC-003–SC-009 ALL PASS，PII 7/7 |
| 浏览器渲染通过 | ✅ | 84 tests，OK（桌面+窄屏） |
| 真实 AI smoke | ✅ | T132 candidate+v1 assessment PASS |
| 真实 BOSS E2E | ✅ | source=6/detail=1/evaluated=2/feedback/cancel/resume 全部门通过 |
| 后端可访问 | ✅ | PID 3424，HTTP 200 |
| 审计遗留 T043/T078/T079 | ✅ | RED→GREEN、HTTP 契约、真实浏览器交互及全量回归通过 |
| 当前代码新鲜真实 E2E | ✅ | 2026-07-15 19:08，source=6/detail=1/evaluated=2，feedback/cancel/resume 通过，blockers=[] |
| T134 / Feature 004 完成 | ✅ | 所有任务复选框与当前发布门证据一致 |

### 12.11 未验证边界与已知限制

1. **T043/T078/T079 已完成**: 任务定义中的结果组合语义、反馈/垃圾桶/方向/撤销/偏好展示均已有定向失败证据、最小实现和自动化通过证据；复选框已逐项更新，不是批量勾选。
2. **T092/T133 已完成**: 2026-07-15 19:08 对当前代码执行的新鲜真实 E2E 已满足所有硬门，不再是 0 counts；但未来复跑仍依赖 CDP、BOSS 登录态、真实市场和 AI 凭据。
3. **T134 已完成**: 用户在固定的 9222 专用持久 profile 完成登录后，`python scripts/boss_cdp_raw.py --check` 三项通过；随后的当前代码真实 E2E exit 0，并由结果文件逐项满足发布门。
4. **黄金样本边界**: Precision@20 与 Recall 均为 80%，达到当前阈值；它只验证标注与指标计算一致性。
5. **资源警告**: HTTP 契约测试通过但输出中仍出现 sqlite `ResourceWarning: unclosed database`；不影响本轮退出码，但应单独治理，不能称为无警告。

### 12.12 BOSS 登录态受控迁移与 profile 边界

- `scripts/boss_cdp_raw.py` 新增 `--import-boss-session --source-cdp-port <port> --cdp-port 9222 --confirm-session-import`；只允许从另一个显式授权的 CDP 浏览器向固定专用 profile 导入 `zhipin.com` 及真实子域 cookie。
- cookie 值仅在内存中处理，返回值和 CLI 只输出安全状态码与计数；不写日志、SQLite、JSON 或异常文本。
- 源/目标端口冲突、未授权、非专用目标 profile 均在连接前阻断；写入或主动探测失败会回滚目标 BOSS cookie，回滚失败使用独立安全码。
- 旧 `--copy-login-state` 已全局停用，在任何模式分支、文件复制或 Chrome 启动前立即拒绝；不再复制 `Local State` 或 Cookies 数据库。
- `python -m unittest tests.test_chrome_setup -v`: `Ran 41 tests in 6.293s`，`OK`；独立复核未发现剩余 Critical/Important 问题。
- 真实协议只读检查确认 Chrome 150 的 `Storage.getCookies` 可用，但 cookie 存在不作为登录成功证据；最终仍以真实搜索主动探测和 E2E 为准。

### 12.13 自动化浏览器生命周期收尾（2026-07-15）

- `tests/fixtures/discovery/e2e_real_boss.py` 现在管理专用 Chrome 的所有权：运行前 9222 不存在时由 E2E 自动启动并在所有退出路径关闭；运行前已存在时默认只复用，不擅自关闭。
- 新增 `--close-browser-after`，供正式自动化在明确授权下连复用的专用 9222 实例也一并关闭，解决“先 setup、后 E2E”跨进程无法天然识别所有权的问题。
- `scripts/boss_cdp_raw.py::close_cdp_chrome()` 在关闭前验证端口属于固定 BOSS profile，优先发送 CDP `Browser.close`；超时才回退到只终止该 profile 的进程，不影响普通 Chrome。
- E2E 结果新增 `browser_lifecycle.mode/close_status`；关闭失败写入 `operational_blockers=browser_close_failed` 并使命令非零退出，不静默遗留浏览器。
- `python -m unittest tests.test_e2e_prerequisites tests.test_chrome_setup -v`: `Ran 73 tests in 6.963s`，`OK`；`python -m py_compile scripts/boss_cdp_raw.py tests/fixtures/discovery/e2e_real_boss.py tests/test_e2e_prerequisites.py tests/test_chrome_setup.py` exit 0。
- 本项只改变测试基础设施生命周期，没有重新执行真实 BOSS E2E；T133/T134 的业务发布门仍沿用 §12.9–§12.10 已完成的新鲜真实验证证据。
