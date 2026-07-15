# Implementation Plan: 简历驱动的岗位发现

**Branch**: 未创建（未配置自动 feature 分支钩子） | **Date**: 2026-07-14 | **Revised**: 2026-07-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/004-resume-job-discovery/spec.md`

## Summary

在现有 Flask + SQLite 单机 Web 应用中新增一个独立的“岗位发现”深模块，把简历证据、候选人模型、求职方向、一次确认、搜索计划、真实岗位详情、按方向评估和结果组合统一到一条可恢复运行链。现有简历保存、AI 适配、BOSS CDP 来源、岗位实体、硬规则、语义校验、兴趣/垃圾桶和运行恢复能力继续复用；旧 workbench 与 screening API、表和历史数据保持可读，但不再作为默认用户旅程。

设计采用新增表和新增接口，不把旧 `search_runs` 与 `screening_runs` 强行合并或改写。新运行以不可变的方向确认版本为输入，以数据库中的搜索项、岗位详情快照和岗位方向评估为恢复事实；文件产物仅作来源交换和诊断，不作为唯一状态来源。AI 负责提取、建议和结构化判读，程序负责证据引用校验、硬约束、分类、状态推进和失败阻断。

2026-07-15 运行时闭环审查确认：现有 feature 004 实现尚未接通真实 AI provider，用户分析路径存在悬空构建引用；发现运行入口只创建记录和计划，未提交持久化 runner；岗位评估输入也缺少可解释证据内容。本修订不改变既有产品架构，而是把真实 provider、确定性证据定位、完整评估输入、后台调度和不可绕过的验收接缝补为发布前硬门。

## Technical Context

**Language/Version**: Python `>=3.10`; browser UI uses existing HTML/CSS/JavaScript without a frontend framework  
**Primary Dependencies**: Flask `>=3,<4`, requests `>=2.28,<3`, websocket-client `>=1.6,<2`, keyring `>=24,<26`, pypdf `>=4,<6`, python-docx `>=1.1,<2`; existing Chrome CDP source script  
**Storage**: SQLite through `webui.store.TaskStore`; resume and scraper artifacts remain in controlled state directories  
**Testing**: `unittest` as the declared repeatable baseline; Flask test client, deterministic fixtures, migration tests, contract tests, browser rendering/interaction checks, and a separately gated real BOSS E2E  
**Target Platform**: Local single-user web application; primary verified environment is Windows with an isolated, logged-in Chrome CDP profile; unit and integration logic remains platform-neutral where existing code permits  
**Project Type**: Single Python web application plus a local browser UI and a subprocess-driven source adapter  
**Performance Goals**: User can upload and confirm within the spec's 3-minute goal; progress is persisted after every completed query page and every completed detail/assessment; partial results become visible without waiting for the whole run; ordinary UI status reads do not block on scraper or AI work  
**Constraints**: No automatic application/contact; no hiring-probability claims; no unverified fallback scores; no resume text, credentials, or raw model output in ordinary logs/results; BOSS access must reuse the controlled logged-in browser and bounded request budgets; confirmed hard constraints must have zero known violations in recommendations  
**Scale/Scope**: One local user, one active discovery worker by default, 1–5 enabled directions, at most 3 generated search terms per direction and at most 12 distinct search items per default run after deduplication; default detail budget 60 jobs, configurable only within existing safe limits  
**Runtime Closure**: Candidate analysis and discovery execution are submitted to one persisted task runtime; accepted work must leave `created/queued` promptly or persist an explicit dispatch failure. The configured provider is resolved through the existing AI settings/keyring boundary and never owns workflow state.  

## Constitution Check

No project-specific `.specify/memory/constitution.md` exists. The active project rules and specs therefore form the planning gates.

### Pre-research gate

| Gate | Result | Design response |
|---|---|---|
| Facts, inference and unknowns remain distinct | PASS | Evidence rows carry source type; AI directions and user confirmations are separate records. |
| AI cannot advance final workflow state alone | PASS | Program-owned state machine validates every AI contract and owns classification/status transitions. |
| Existing user data and old flows are not silently rewritten | PASS | Additive migrations and new discovery tables; old workbench/screening rows remain historical. |
| Runtime rules have code/data enforcement, not documentation only | PASS | Hard constraints, checkpoint input hashes, evidence-reference validation and deletion cascades are persisted invariants. |
| Long-running work is observable, cancellable and recoverable | PASS | Query-, detail- and assessment-level checkpoints are first-class database records. |
| Privacy and credential boundaries are preserved | PASS | Existing keyring stays; derived resume evidence is deleted with the resume; raw prompts/responses are not persisted. |
| Real E2E is not replaced by fixtures or smoke tests | PASS | Quickstart defines separate unit, integration, browser and real-source gates. |
| Scope stays focused | PASS | No new job source, auto-application, resume rewriting or cross-user learning. |

### Runtime-audit baseline

These are implementation blockers discovered after the original plan was written. They are not design waivers and must be closed before the post-implementation gate may pass.

| Runtime gate | Current result | Required plan response |
|---|---|---|
| Configured AI analysis can construct a real provider | FAIL | Implement the versioned provider behind `webui.ai` and cover the real HTTP composition seam. |
| Evidence locator proves excerpt correspondence | FAIL | Resolve locators deterministically against canonical resume text and compare the resolved slice. |
| Job assessment receives interpretable candidate evidence | FAIL | Build a sanitized assessment view containing summary, direction metadata and evidence content, not IDs alone. |
| Accepted discovery run actually executes | FAIL | Submit the persisted run to the runtime; cancel/resume must control work, not only mutate status. |
| HTTP and completion gates are red-capable | FAIL | Replace placeholders and require route-level, runtime-level and real-E2E evidence. |

### Post-design gate

The revised design satisfies all planning gates without a project split or constitution waiver. The implementation gate remains FAIL until the runtime-audit blockers above have red-capable tests and passing evidence.

## Architecture and Module Boundaries

### External module interface

The default UI and HTTP routes call one discovery application interface:

```text
analyze_resume(resume_id, consent)
get_analysis(analysis_id)
confirm_directions(analysis_id, directions, hard_constraints, soft_preferences)
start_discovery(confirmation_version_id, optional_safe_limits)
get_run(run_id)
list_results(run_id, category, direction_id)
cancel_run(run_id)
resume_run(run_id)
retry_job(run_id, job_id)
record_feedback(target, reason, scope)
```

The interface hides provider prompts, BOSS filter codes, query pages, detail budgets and internal checkpoint mechanics from the default user journey.

### Internal modules

| Module | Responsibility | Reused implementation | New responsibility |
|---|---|---|---|
| `webui/resume.py` | Safe file validation, extraction and storage boundary | Existing extraction/save flow | Return stable source locators suitable for evidence references. |
| `webui/ai.py` | AI provider adapter and security-safe errors | `call_ai`, credentials, timeout/error mapping | New versioned analysis and job-evaluation calls; no workflow state mutation. |
| `webui/candidate.py` | Candidate evidence and direction domain logic | Existing response validators as patterns | Validate evidence references, merge duplicate evidence, normalize/dedupe directions, enforce 1–5 default directions. |
| `webui/discovery.py` | Deep application module | Hard-rule and semantic utilities | Confirm immutable inputs, compile search plans, apply classification policy, build safe result explanations. |
| `webui/discovery_runner.py` | Persisted orchestration | Cancellation/process patterns from `ScreeningRunner` | Stage-driven run loop, query/detail/assessment checkpoints, resume and partial-success calculation. |
| `webui/source.py` | Source adapter boundary | Existing command construction and `boss_cdp_raw.py` | Bind checkpoints to full query input hash; isolate list/detail failures; return typed source outcomes. |
| `webui/screening.py` | Deterministic source-field parsing and hard-rule primitives | Salary, degree, city and other parsers | Expose three-state `pass/violation/unknown` results instead of treating unknown as pass for discovery. |
| `webui/semantic.py` | Program-governed AI output validation | Existing dimensions and confidence gate | Validate per-direction evidence references and emit sanitized assessment proposals. |
| `webui/store.py` | SQLite persistence and additive migrations | Existing jobs, resume, feedback, migration helpers | New analysis/direction/confirmation/run/plan/snapshot/evaluation/checkpoint methods. |
| `webui/app.py` | HTTP composition root | Session/token protection and error envelope | Thin discovery routes only; runner classes move out rather than making `app.py` deeper. |
| `webui/index.html` | Default four-step journey | Existing visual tokens, result interactions and responsive rules | Unified upload, analysis confirmation, progress and direction-grouped results. |

### Seams

- `AIProvider`: existing configured OpenAI-compatible call boundary; all provider variation remains behind `webui.ai`.
- `JobSource`: BOSS CDP is the only implementation in this feature; the seam exists to make integration tests deterministic and to prevent source file artifacts from owning business state.
- `EvaluationPolicy`: versioned program policy converts validated hard/semantic outcomes into categories. It is calibrated with the golden set and stored on each run.
- No repository abstraction is introduced over `TaskStore`; that would add indirection without a second storage implementation.

## Core Design Decisions

1. Create a new `discovery_runs` aggregate rather than reusing either legacy run table as the new source of truth.
2. Keep `jobs` as the canonical long-term job identity, but persist a run-scoped `discovery_job_snapshots` row before evaluation.
3. Store candidate analyses and confirmation versions immutably. Re-analysis creates a new version; editing after confirmation creates a new confirmation.
4. Represent hard-rule outcomes as `pass`, `violation`, or `unknown`. Only `violation` rejects deterministically; `unknown` prevents `high_match`.
5. Store one assessment per `(run, job snapshot, direction)` so one job can be strong for one direction and developmental for another.
6. Store only sanitized evidence excerpts/references needed for explanation. Raw model responses and complete prompts are not durable data.
7. Make database checkpoints authoritative. Scraper JSON is validated against an input hash before import and is never sufficient by itself to resume.
8. Preserve old APIs and history during migration, but make the discovery UI the `/` default after browser and real E2E gates pass.
9. Add one public `DiscoveryAIProvider` behind `webui.ai`. It receives endpoint/model/key values only, exposes `analyze` and `assess_job`, maps low-level transport failures to feature-safe codes, and never reads or writes `TaskStore`.
10. Treat model-provided character offsets as untrusted hints. Candidate analysis v2 asks for a minimal exact source quote; program code resolves the quote against canonical resume text, rejects ambiguity/mismatch, and generates the persisted locator.
11. Build the job-assessment view at the runner boundary from persisted sanitized data: candidate summary, selected direction metadata, linked evidence IDs/values/excerpts/assertion types, and one job snapshot. References outside that view are invalid.
12. Introduce one application-owned discovery task runtime in `webui.discovery_runner`. It submits candidate analyses and discovery runs, retains cancellation handles, reloads provider/source dependencies per task, and leaves database checkpoints authoritative.
13. `webui.app` constructs the runtime once, submits work after creating immutable input records, and returns persisted state. Cancel/resume routes delegate to the runtime and then return the resulting state; they do not manufacture progress by direct status edits.
14. Use the real provider class in route/integration tests while mocking only its HTTP transport. A `FakeAIProvider` remains valid for domain-unit tests but cannot satisfy HTTP composition or release completion gates.

## Real AI Provider and Evidence Resolution

### Provider construction

- Resolve configured endpoint, model and credential reference at the composition boundary.
- Retrieve the API key through the existing keyring helper; never place the key, credential reference, prompt or raw response in store records, events or error envelopes.
- Verify endpoint path behavior, JSON-object support and the selected model with the current configured service before live validation. A successful legacy AI call is not proof that the new contracts are supported.
- Map transport errors once: authentication → `ai_auth_failed`, timeout → `ai_timeout`, network → `ai_network_error`, parse/schema failure → `ai_invalid_output`, uncertain valid response → `ai_uncertain`, and invalid evidence linkage → `evidence_reference_invalid`.
- Permit at most one corrective retry for a structurally invalid but successfully returned model response. Do not retry authentication failures; do not invent missing values, scores or references.

### Candidate-analysis v2 flow

1. Derive canonical resume text with a versioned normalization rule and keep the immutable resume content hash as its source identity.
2. Ask the model for summary, unknowns, directions and evidence carrying a minimal exact `source_quote`; do not trust model offsets.
3. Reject sensitive quotes before evidence use. Resolve each quote exactly against canonical text; when duplicated, require sufficient surrounding quote/context to become unique.
4. Generate `start/end` from the unique match, then assert the canonical slice equals the accepted quote.
5. Validate cross-references and direction policy. Persist only sanitized normalized evidence, safe excerpt and generated locator; never persist raw provider output.

### Job-assessment v1 flow

1. Load the run analysis summary and the selected direction's type, rationale and gaps.
2. Load only evidence linked to that direction and include each permitted ID, normalized value, safe excerpt and assertion type.
3. Include one sanitized snapshot and define job evidence IDs as supplied field keys such as `title`, `jd`, `salary` and `location`.
4. Validate all returned candidate/job references against the exact supplied sets before policy evaluation.
5. Persist per-job provider failure codes. One failed assessment becomes `pending/needs_review`; unrelated jobs continue.

## Runtime Composition and Dispatch

- Candidate-analysis submission creates a `queued` immutable attempt and returns promptly; the runtime advances it to `analyzing`, then `ready` or `failed`.
- Discovery-run submission creates the immutable run and plan, then submits the run. The worker must advance `created → planning` or persist a dispatch failure within the SC-018 window.
- The runtime owns in-process futures/cancellation signals; SQLite owns durable status, events, cursors and completed work.
- Cancellation first persists the request, then prevents every not-yet-started work unit. Resume validates input hashes and resubmits only retryable unfinished work.
- On process restart, non-terminal in-flight work converges to `interrupted`; it is never silently marked succeeded or automatically resumed against an unverified source/login state.
- Dispatch submission failure is persisted with a safe failure code and stage. A run may not remain indefinitely `created` without an event explaining why.

## Classification Policy

The first policy version uses deterministic precedence:

1. Known hard-constraint violation → `not_suitable`.
2. Missing required resume/JD input, unknown hard constraint, invalid/uncertain AI output, or unavailable detail → `needs_review`.
3. Valid assessment is categorized as `high_match`, `adjacent_match`, `growth_match`, or `not_suitable` using a versioned policy.
4. The existing confidence 70, dimension floor 50 and match score 70 form the initial high-match baseline; adjacent/growth boundaries must be calibrated against the golden set before release and saved as the policy version.
5. A job shown for multiple directions keeps each assessment, while the portfolio selects a primary direction and preserves alternate direction badges.

No category is produced from a default score. Missing assessment data always remains explicit.

## Search and Source Plan

- Generate at most three search terms per enabled direction; normalize whitespace/case and dedupe globally.
- Merge identical terms while retaining all originating direction IDs.
- Bind each search item to `input_hash(keyword, city, source_filters, direction_version, policy_version)`.
- Default run cap: 12 unique search items, one page per item initially, global detail cap 60. Advanced limits may increase pages only within existing source caps.
- Allocate at least one search item to every enabled direction before distributing remaining budget.
- Persist page completion and source failure after every page.
- Select details using round-robin direction coverage plus dedupe, not first-query-wins ordering.
- Persist detail completion/failure per job and continue after a single job failure.

## UI Transition Plan

1. Replace the default two-view decision with one discovery home: upload → analysis/confirmation → progress → results.
2. Reuse current design tokens, compact job rows, cancel/resume/retry interactions and safe link behavior.
3. Move manual keywords, seven BOSS filters, pages and detail limits into a collapsed “高级设置”.
4. Preserve old workbench and screening views behind a clearly labeled compatibility entry until migration acceptance is complete; do not maintain two default resume states.
5. New result navigation uses direction plus category. Interested and trash remain persistent cross-run destinations.
6. Render all dynamic status and explanations with text-safe DOM APIs; do not inject source or AI strings as HTML.
7. Re-validate 1366×768 and 720px; the old five equal-width tabs may become a direction selector plus horizontally safe category controls.

## Migration and Compatibility

- Add migrations after current schema version 10. Do not rename/drop existing tables in this feature.
- Migration 011: analysis, evidence, directions and direction-evidence links.
- Migration 012: confirmation versions, confirmation directions, discovery runs, search plans/items and run events.
- Migration 013: job snapshots, per-direction assessments and structured discovery feedback.
- Existing `candidate_profiles`, `resumes`, `jobs`, `profile_jobs`, `feedback_events`, `screening_trash_records`, AI settings and keyring references remain reusable.
- Legacy `search_runs` and `screening_runs` are not backfilled as discovery runs. UI labels them “历史搜索/历史筛选”.
- Existing interested/trash job state remains visible in the new UI. New feedback uses explicit scope and does not broaden old not-interested events.
- Resume deletion removes evidence, candidate model, directions and explanation references; job identity and durable feedback remain, with historical explanations marked unavailable.
- Migration tests use a version-10 fixture and verify idempotence, row preservation, foreign-key behavior and restart convergence.

## Test Strategy

| Layer | Purpose | Release claim allowed |
|---|---|---|
| Contract | AI schemas, HTTP envelopes, enums, state transitions, privacy exclusions | Contract-valid only |
| Unit | Evidence/direction normalization, plan compilation, tri-state hard rules, classification, portfolio diversity | Component correctness |
| Migration/store | Upgrade from schema 10, immutable snapshots, checkpoints, deletion and long-term state | Data compatibility |
| Integration | Real provider class with mocked transport + fake JobSource + temp SQLite full pipeline, cancellation, restart and partial success | Local composition/pipeline correctness |
| Golden set | Human-labeled resumes/directions/jobs; precision, recall, hard violations, diversity, explanation fidelity | Matching-quality evidence |
| Browser | Real local browser at 1366×768 and 720px; loading/empty/success/partial/failure states | UI usability evidence |
| Real source smoke | CDP login and one list response | Connectivity only |
| Real source E2E | Sanitized resume, two directions, multi-query/multi-page list, details, feedback and one interruption/resume | End-to-end acceptance |

The release gate requires all layers. A historical test count, static DOM test, fixture pipeline or `--smoke-test` cannot substitute for the real E2E.

Additional closure rules:

- HTTP contract tests instantiate the real application composition root with configured AI settings and assert the consented analysis path reaches `queued/analyzing/ready` or a mapped safe failure—never a missing-component 500.
- Provider tests use `DiscoveryAIProvider` with mocked `call_ai`; they assert prompt input boundaries, canonical evidence resolution, safe error mapping and one-retry limits.
- Runtime integration uses the real provider class plus fake network transport and fake `JobSource`, proving that `app → runtime → provider/runner → store` is connected.
- Candidate locator tests include in-range-but-wrong slices, repeated quotes, Unicode text, surrounding whitespace normalization and sensitive quotes.
- Existing placeholder tests for analysis/confirmation, run/results/retry, feedback and cancel/resume do not count toward a completed gate and must be replaced with red-capable requests.
- Live provider smoke is opt-in and asserts both candidate-analysis and job-assessment contract compliance on synthetic, sanitized input; “request returned” alone is not a pass.

## Project Structure

### Documentation (this feature)

```text
specs/004-resume-job-discovery/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── README.md
│   ├── openapi.yaml
│   ├── ai-contracts.md
│   └── state-machine.md
├── checklists/
│   └── requirements.md
└── tasks.md                 # created by /speckit-tasks, not this phase
```

### Source Code (repository root)

```text
webui/
├── app.py                   # thin routes and composition root
├── ai.py                    # AI provider adapter
├── candidate.py             # new: evidence/model/direction validation
├── discovery.py             # new: deep discovery application module
├── discovery_runner.py      # new: persisted orchestration
├── source.py                # new: JobSource adapter around BOSS CDP
├── resume.py                # existing resume boundary
├── screening.py             # deterministic parsers/hard-rule primitives
├── semantic.py              # validated semantic proposal
├── store.py                 # additive schema and persistence
└── index.html               # unified default journey

scripts/
└── boss_cdp_raw.py          # source implementation; add validated checkpoints/detail isolation

tests/
├── fixtures/
│   └── discovery/           # sanitized resumes, JDs and human labels
├── test_candidate.py
├── test_discovery.py
├── test_discovery_contracts.py
├── test_discovery_integration.py
├── test_discovery_store.py
├── test_discovery_frontend.py
├── test_discovery_browser.py
└── test_boss_discovery_source.py
```

**Structure Decision**: Keep the existing single-project layout. Add three cohesive modules around discovery and one source adapter instead of creating separate backend/frontend projects or continuing to grow `app.py`.

## Delivery Sequence

1. Add red route tests for configured consented analysis and accepted-run dispatch; confirm both fail on the current missing seams.
2. Add candidate-analysis v2 and assessment input/output contract fixtures, including wrong/ambiguous locator and unknown-reference cases.
3. Implement `DiscoveryAIProvider`, safe error mapping and bounded retry behind `webui.ai`; keep it independent of workflow storage.
4. Implement canonical quote resolution and strengthen final candidate evidence validation.
5. Enrich the runner's assessment input view with persisted sanitized summary, direction metadata and linked evidence content.
6. Connect the application-owned runtime to analysis creation, run creation, cancellation, resume and per-job retry routes.
7. Replace HTTP placeholders and add composition integration tests using the real provider class with mocked transport.
8. Re-run migration/store, domain, integration, full regression, golden-set and browser gates; do not reuse historical counts.
9. Run opt-in live provider smoke for both contracts and record model/endpoint capability evidence without exposing credentials or prompts.
10. Restart the affected backend, verify accessibility, then run the bounded real BOSS E2E through the actual user route including interruption/resume.
11. Rewrite `validation.md` from current evidence and complete T096 only when every required gate passes.

## Complexity Tracking

No constitution violation or additional project split is introduced. The runtime closure reuses the planned `webui.ai`, `webui.discovery_runner`, `TaskStore` and route composition boundaries. Candidate-analysis contract v2 and stronger runtime tests add controlled complexity because the v1 model-offset assumption and fake-provider-only seam cannot satisfy evidence fidelity or real-path acceptance.
