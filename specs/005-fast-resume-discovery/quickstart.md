# Quickstart Validation: 快速简历驱动岗位推荐收口

本文件是实现后的可运行验收指南，不是实现步骤或历史完成声明。任何命令只有在当前工作区实际执行并产生当前产物后，才能作为 005 的通过证据。

## 1. Execution Prerequisites

正式实现前：

1. 按 `CONTRIBUTING.md` 创建结构化 issue，包含问题、现状、根因、建议和影响范围。
2. 从 `master` 创建 `codex/fast-resume-discovery` 或 issue 对应 feature branch。
3. 检查 `git status --short --branch`，保留并隔离用户已有修改。
4. 使用仓库 Python >=3.10 环境；Windows 推荐：

```powershell
uv sync --frozen
$python = '.venv\Scripts\python.exe'
& $python --version
```

如果现有 `.venv` 缺依赖，先明确修复环境；不得用导入失败的测试输出声称业务失败或通过。

## 2. Baseline Before Implementation

```powershell
& $python -m unittest tests.test_candidate tests.test_discovery tests.test_discovery_integration tests.test_discovery_contracts tests.test_boss_discovery_source tests.test_discovery_frontend -v
& $python -m unittest discover -s tests -v
```

记录：

- 测试数量、耗时、通过/失败；
- 当前 migration 版本；
- 当前真实 E2E blocker；
- 捕获基线约 53 秒/详情仅为历史基线，不能替代新测量。

## 3. Contract and Unit Gate

```powershell
& $python -m unittest `
  tests.test_candidate `
  tests.test_ai `
  tests.test_screening `
  tests.test_discovery `
  tests.test_discovery_store `
  tests.test_discovery_contracts `
  tests.test_process_executor `
  tests.test_boss_discovery_source -v
```

Expected:

- candidate-analysis v4 facts/evidence/directions contract passes；
- job-assessment v2 one-job/two-direction partial quarantine passes；
- numeric min_salary matrix passes；
- migration 014→015 idempotent and old rows unchanged；
- canonical recommendation sort/explanation/multi-direction projection passes；
- structured detail events reject malformed/unknown/job-mismatched payloads；
- no raw resume/JD/model output in events/logs。

## 4. Deterministic Pipeline and Performance Gate

The implementation phase adds `tests.test_discovery_performance` with a fake clock and delayed fake source. Run:

```powershell
& $python -m unittest `
  tests.test_discovery_performance `
  tests.test_discovery_integration `
  tests.test_discovery_frontend -v
```

Required fixture:

- 100 unique list candidates + duplicates；
- 20 accessible details；
- 3 enabled directions；
- pass/unknown/violation salary and city cases；
- slow/failing/cancelled detail；
- valid/invalid/timeout AI groups；
- interrupted run with completed detail/assessment checkpoints。

Expected gates:

| Gate | Expected |
|---|---|
| List pool | ≤90 simulated seconds |
| Priority selection | 15 selected, stable after input reorder, direction floor preserved |
| First five results | ≤5 simulated minutes |
| Standard run | 15 details + required assessments ≤10 simulated minutes |
| Progress | each completed unit visible ≤10 simulated seconds |
| Cancellation | no new source/AI work after cancel; terminal ≤30 simulated seconds |
| Resume | repeated completed detail calls = 0; repeated completed assessment calls = 0 |
| Hard rules | explicit violation recommendations = 0 |

Fake timing proves orchestration and budget behavior only. It cannot prove real BOSS speed.

## 5. Full Regression and Syntax

```powershell
& $python -m py_compile `
  scripts\boss_cdp_raw.py `
  webui\candidate.py `
  webui\ai.py `
  webui\discovery.py `
  webui\discovery_runner.py `
  webui\source.py `
  webui\store.py `
  webui\app.py `
  tests\fixtures\discovery\e2e_real_boss.py

& $python -m unittest discover -s tests -v
```

Expected: all tests pass; no ResourceWarning/new thread/process leak accepted without explanation; no unrelated fixture or historical 004 artifact rewrite.

## 6. Local HTTP and Browser Validation

Restart the affected Flask service from the feature worktree:

```powershell
& $python webui\app.py
```

In another terminal:

```powershell
Invoke-WebRequest http://127.0.0.1:5000/ -UseBasicParsing
```

Run browser validation:

```powershell
& $python -m unittest tests.test_discovery_browser -v
```

Required real HTTP scenarios at 1366×768 and 720px:

1. Upload one resume and start v4 analysis once.
2. Edit one fact, reject one inferred fact, add one user fact.
3. Confirm city/min_salary/two directions.
4. Start run; while status = processing_jobs, verify four progress counters update.
5. Verify first recommendation card appears before terminal status.
6. Verify rank update keeps stable card identity and focus/read order.
7. Verify card displays JD/excerpt, positive evidence, gaps, source and fetched_at.
8. Cancel and resume states remain reachable without horizontal overflow or duplicate controls.

Static DOM injection alone does not satisfy this gate.

## 7. Real Source Preflight

Real E2E prerequisites:

- dedicated Chrome CDP on `127.0.0.1:9222`；
- user personally logged into BOSS；
- configured AI endpoint/model/key in system keyring；
- healthy network；
- de-identified complete resume fixture；
- user-authorized temporary DB/result write boundary。

Check source:

```powershell
& $python scripts\boss_cdp_raw.py --check
```

All source checks must pass. Cookie existence alone is not login proof; active source probe is required.

## 8. Real Performance Sample

Before full E2E, run a bounded current-code sample added during implementation:

```powershell
& $python tests\fixtures\discovery\e2e_real_boss.py `
  --performance-only `
  --details 5 `
  --report specs\005-fast-resume-discovery\validation\real_performance_5.json
```

Required report fields:

- list query/job counts and duration；
- selected/deferred counts and reasons；
- per-detail total/wait duration, p50/p95, batch and concurrency；
- AI groups/calls/duration；
- first result/first five/all completion timestamps；
- reuse/failure/cancel counts；
- source breaker events and blocker list。

If verification/rate-limit/login signals appear, stop and report blocked. Do not increase concurrency to force a pass.

## 9. Full Real E2E Gate

```powershell
& $python tests\fixtures\discovery\e2e_real_boss.py `
  --policy discovery_v2 `
  --close-browser-after `
  --report specs\005-fast-resume-discovery\validation\real_e2e_result.json
```

Hard gates:

- execution through real HTTP routes/application runtime/provider composition；
- candidate v4 ready with valid facts/evidence/directions；
- at least 2 confirmed directions；
- list pool completes and selected details are explainable；
- ≥5 real complete/partial usable details；
- ≥5 real terminal assessments；
- at least one result becomes visible before run terminal；
- feedback works；
- cancel starts no new work after request；
- resume duplicates zero completed details/assessments；
- report has no unexplained blocker；
- performance gates SC-001–SC-004 pass or explicitly block release。

Real market having zero high_match jobs is not implementation failure if all work completed and typed no-result reasons are correct. Fewer than 5 accessible details/evaluations remains blocked for SC-014 and must not be relabeled PASS.

## 10. Golden Sample and Privacy

```powershell
& $python tests\fixtures\discovery\evaluate.py
```

Expected:

- hard-rule violation rate = 0；
- direction coverage and explanation fidelity do not fall below 004 recorded baseline；
- no sensitive fixture marker reaches facts, prompts for job assessment, events or results；
- recommendation explanations reference only current profile version and current snapshot evidence。

## 11. Release Gate

Before reporting completion:

1. Create `specs/005-fast-resume-discovery/validation.md` with current commands, timestamps, environment, results, artifact links and unverified boundaries.
2. Ensure validation text matches JSON artifacts byte-for-byte on counts/status/blockers.
3. Update README.md and README.en.md consistently.
4. Add CHANGELOG entry.
5. If version changes, synchronize `scripts/boss_cdp_raw.py`, `pyproject.toml`, `SKILL.md`, `README.md`.
6. Restart only the affected backend and verify HTTP 200.
7. Run independent review against spec, actual diff and current evidence.
8. `git diff --check` and `git status --short --branch` must show only intended work plus explicitly preserved user changes.

No fake test, AI smoke, source check, historical 004 result or manually edited validation text can substitute for the full real E2E gate.

