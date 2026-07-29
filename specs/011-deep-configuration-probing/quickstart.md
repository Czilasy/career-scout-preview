# Quickstart: 高级设置深度自动调优

**Date**: 2026-07-29

本文是实施后的验证指南，不是实际深度实验任务单。真实实验只能使用控制者签发的完整 manifest 和渲染任务文档；禁止根据本文示例自行选择参数。

## 1. Prerequisites

1. Windows local workspace at repository root.
2. `.venv` Python 3.10+ with project dependencies.
3. Node 20+ and WebUI dependencies installed.
4. Isolated test state directory and SQLite database for automated/injected tests.
5. Real Chrome CDP and user login only for explicitly marked real-chain scenarios.
6. No ordinary task or second experiment running during a real tuning round.

## 2. Automated Validation

### Python syntax and focused tests

```powershell
.\.venv\Scripts\python.exe -m py_compile `
  webui\execution_config.py `
  webui\tuning.py `
  webui\pipeline_exec.py `
  webui\ai.py `
  webui\source.py `
  webui\store.py `
  webui\app.py `
  scripts\boss_cdp_raw.py

.\.venv\Scripts\python.exe -m unittest `
  tests.test_execution_config `
  tests.test_tuning `
  tests.test_webui_store `
  tests.test_healthy_pipeline `
  tests.test_pipeline_tasks_cleanup `
  tests.test_chrome_setup
```

### Full Python regression

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

### Frontend tests and build

```powershell
Push-Location webui
npm test
npm run build
Pop-Location
```

All commands must finish with exit code 0. A focused pass does not replace the full regression before completion.

## 3. Service Replacement

Backend and frontend changes require replacing the old WebUI service.

```powershell
# 1. Find old listener
Get-NetTCPConnection -LocalPort 5000

# 2. Stop only the old WebUI PID
Stop-Process -Id <OLD_PID> -Force

# 3. Start the updated service
.\.venv\Scripts\python.exe webui\app.py

# 4. Confirm a new PID owns port 5000
Get-NetTCPConnection -LocalPort 5000

# 5. Verify build identity and page access
Invoke-RestMethod http://127.0.0.1:5000/api/version
```

The old and new PID must differ. Do not accept “start command returned” as proof that the new service is active.

## 4. Deterministic Validation Scenarios

### Scenario A - Scope normalization and size boundaries

Validate through pure tests and `POST /api/search-scope/preview`:

| Input | Expected |
|---|---|
| duplicate keyword differing only by spaces/case | one canonical keyword |
| semantically related but textually different keywords | both retained |
| `东莞` + explicit alias `东莞市` | one canonical city |
| unknown city | 422 + suggestions, no auto-replacement |
| nationwide + a city | 422 |
| planned pages 1, 9 | small |
| planned pages 10, 19 | medium |
| planned pages 20, 30 | large |

Expected: backend and frontend preview agree; backend remains authoritative.

### Scenario B - Frozen configuration

1. Save custom config A.
2. Start an isolated pipeline task and capture its config digest.
3. Save custom config B before detail/AI stages begin.
4. Complete the task.

Expected:

- every child stage reports config A digest;
- no stage reads config B;
- a new task uses B only after explicit selection/save;
- `pages` is unchanged by mode selection.

### Scenario C - Three modes and recent custom

1. Seed a complete mode version with distinct small/medium/large fixtures.
2. Preview tasks at 9, 10, 19, 20 and 30 pages.
3. Select stable, balanced and extreme.
4. Modify one speed field and save custom.
5. Move between a system mode and custom.

Expected:

- correct internal slot selected for each size;
- only four user-visible selections exist;
- custom values restore exactly;
- mode/custom changes never alter keywords, cities or pages.

### Scenario D - Exclusive execution lease

1. Create experiment A and issue one round.
2. Attempt to start experiment B and an ordinary pressure task.
3. Restart the isolated service while A is active.

Expected:

- only A starts;
- B/ordinary task receive conflict responses;
- restart reconciles the active round to uncertain;
- confirmed rounds remain confirmed;
- lease is not silently stolen before reconciliation.

### Scenario E - Manifest completeness

Attempt to issue manifests missing each required category in turn: exact config, frozen input, step timeout, stop action, write boundary, artifact or report field.

Expected: every incomplete manifest is rejected before execution. A manifest containing placeholders or “choose as appropriate” is also rejected.

### Scenario F - Clean-context executor

Provide one complete rendered task to an executor with no project conversation. The task uses an injected/fake child round rather than external calls.

Expected:

- executor completes only listed steps;
- report matches schema and digests;
- executor does not ask for parameter selection;
- an injected unknown condition produces a blocked report, not an improvised fix.

### Scenario G - Recoverable and hard errors

Inject:

1. one recoverable timeout followed by success;
2. repeated rate limit beyond manifest threshold;
3. login/verification block;
4. missing terminal item;
5. quality difference beyond confirmed reference range;
6. evidence persistence failure.

Expected:

- case 1 may confirm, with retry and delay included in total duration;
- cases 2-6 stop and cannot promote;
- the same hard-boundary config is not automatically repeated.

### Scenario H - Atomic round confirmation

Interrupt at each boundary:

- before program evidence exists;
- after evidence exists but before report;
- during report validation;
- after transaction commit.

Expected:

- first three recover as uncertain/invalid and do not count;
- only the committed round is skipped after restart;
- no metrics are counted twice.

### Scenario I - Stage input reuse

Use frozen fixture artifacts:

- detail round reuses list input but performs detail work;
- rough round reuses list fields but performs AI rough work;
- fine round reuses JD input but performs AI fine work;
- end-to-end round rejects reuse of intermediate output.

Expected: input/artifact digests prove the allowed reuse and reject cross-version mixing.

### Scenario J - Funnel and time projection

Feed deterministic round reports where candidates are slower, clearly faster, noisy, boundary-failing and statistically indistinguishable.

Expected:

- slow/no-gain candidates do not enter expensive validation;
- promising candidates receive required repeats;
- boundary failure is retained but never applied;
- near-24-hour projection removes only low-value exploration;
- indistinguishable modes may share the lower-pressure config.

### Scenario K - Complete apply and rollback

1. Attempt apply with small/medium complete but large incomplete.
2. Complete every required final round and create a candidate mode version.
3. Apply after explicit user confirmation.
4. Roll back to previous complete version.

Expected:

- partial apply is rejected;
- all nine slots switch atomically;
- rollback restores all nine prior slots;
- recent custom config never changes.

## 5. Frontend Render Validation

Use real rendered pages, not source-only review.

Minimum viewports:

- desktop: 1440 × 900;
- narrow mobile: 390 × 844.

Validate:

- stable/balanced/extreme/custom control fits without text clipping;
- scope summary shows canonical keywords/cities, planned pages and size;
- running task locks scope fields;
- tuning status shows current stage/candidate/round, remaining estimate and block reason;
- incomplete result has no apply action;
- summary and expandable evidence are readable;
- no horizontal page scroll, nested cards, overlapping controls or double scrollbars;
- keyboard focus and all icon tooltips are visible.

## 6. Real-Chain Gates

Real external testing starts only after all deterministic scenarios pass.

### Gate 1 - Baseline qualification

- Controller issues an exact low-pressure task manifest.
- Executor verifies environment and runs only that manifest.
- Program report must show complete terminal counts and valid evidence.
- Old experiment values are not accepted as baseline evidence.

### Gate 2 - Stage probes

For list, detail, rough and fine stages:

- controller issues each candidate separately;
- executor returns after every task;
- controller reads the report before issuing the next candidate;
- hard boundary stops that direction;
- allowed stage inputs are reused according to Scenario I.

### Gate 3 - Final profiles

Each selected stable/balanced/extreme slot must satisfy:

- small, medium and large coverage as applicable;
- at least two workload structures per size;
- at least three confirmed repetitions per structure;
- no missing or duplicate terminal item;
- quality within confirmed reference range;
- complete total-duration accounting.

### Gate 4 - End-to-end

Run final candidates from the start without intermediate reuse. Do not apply any version until all required final evidence is confirmed and the user approves the complete result.

## 7. Required Executor Deliverables

For each real task the controller must generate:

```text
tuning/<experiment-id>/
├── tasks/<task-id>.json       # immutable manifest
├── tasks/<task-id>.md         # self-contained worker instructions
├── reports/<task-id>.json     # executor report
├── evidence/<round-id>.json   # program report
└── artifacts/                 # allowlisted stage artifacts
```

Before handing work to a low-autonomy AI, verify the task document contains:

- all 9 exact speed values;
- exact keywords, cities/nationwide and pages;
- exact stage, task size, structure and repetition;
- resolved commands/actions and task identity;
- polling interval and timeout;
- recoverable retry count;
- every hard stop;
- exact allowed paths and required artifacts;
- fixed return format.

If any item is missing, do not dispatch the task.

## 8. Out of Scope

This quickstart does not execute or validate:

- single-combination page depth from 20 to 50;
- multi-combination cumulative safe page ceiling;
- automatic retuning after API/environment changes;
- concurrent experiment execution;
- recommendations for tasks over 30 planned pages.
