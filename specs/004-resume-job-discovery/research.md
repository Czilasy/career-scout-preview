# Phase 0 Research: 简历驱动的岗位发现

**Date**: 2026-07-14  
**Revised**: 2026-07-15 — runtime closure research  
**Status**: Complete — no unresolved planning questions

## Evidence base

Research used the current repository, specs 001–004, existing tests and the installed Spec Kit templates. No current production behavior is inferred from historical pass counts alone. Real BOSS behavior cited by earlier specs remains historical evidence until the 004 E2E is run.

## Decision 1: New discovery aggregate

**Decision**: Add a distinct `discovery_runs` aggregate and route family. Keep legacy `search_runs` and `screening_runs` readable but do not make either the new source of truth.

**Rationale**: The two existing flows have different inputs, statuses, artifacts, result models and front-end state. Retrofitting one would either lose multi-direction semantics or reinterpret historical data.

**Alternatives considered**:

- Extend `screening_runs`: rejected because it freezes one keyword and flat filter set and stores only binary/pending verdicts.
- Extend `search_runs`: rejected because it lacks hard-rule/detail-assessment state and confirmation versions.
- Merge both old tables in place: rejected due to migration risk and ambiguous historical semantics.

## Decision 2: Immutable analysis and confirmation versions

**Decision**: A resume analysis is immutable. User confirmation creates an immutable version containing enabled directions, hard constraints, soft preferences and safe limits.

**Rationale**: Historical results must always reveal the exact evidence and intention snapshot used. Mutable profile fields cannot provide that guarantee.

**Alternatives considered**:

- Store only the latest profile JSON: rejected because old runs would silently change meaning.
- Copy a large JSON blob into each run: rejected because evidence relationships, deletion and validation become opaque.

## Decision 3: Normalized evidence with source references

**Decision**: Store evidence as rows with type, normalized value, sanitized excerpt/source locator, explicit/inferred status, confidence and sensitivity flag. Link directions to evidence explicitly.

**Rationale**: This supports faithful explanations, deduplication and deletion without persisting raw AI output.

**Alternatives considered**:

- Keep only `resumes.suggestions_json`: rejected because it cannot distinguish facts, inference, unknowns or evidence reuse.
- Store raw prompts/responses: rejected on privacy, traceability and schema-stability grounds.

## Decision 4: Bounded direction portfolio

**Decision**: Present 1–5 evidence-backed directions by default: core, adjacent and growth. Merge synonymous role names into search terms under one direction.

**Rationale**: Fewer directions miss transferable work; unbounded directions create noise. Five is the approved UX ceiling, not a quota.

**Alternatives considered**:

- One predicted role: rejected because it cannot satisfy controlled diversity.
- Always generate five: rejected because low-evidence resumes would force fabrication.

## Decision 5: Search-plan compiler, not user-authored keywords

**Decision**: Compile up to three terms per direction and 12 globally deduplicated search items by default. Allocate coverage before extra depth and enforce a global detail budget.

**Rationale**: The current maximum-three-keyword flow misses role synonyms, while unbounded expansion creates source pressure and low precision.

**Alternatives considered**:

- Keep manual keyword as required: rejected by the primary user goal.
- One query per direction: rejected because role naming varies significantly.
- Unlimited expansion: rejected due to relevance and source-safety risk.

## Decision 6: Broad retrieval, detail-first precision

**Decision**: Use only confirmed hard constraints for early rejection. Treat company size, funding and industry as soft by default. Do final recommendation only after a detail snapshot exists.

**Rationale**: Over-filtering list search loses valid adjacent jobs; title/list snippets are insufficient for high-confidence matching.

**Alternatives considered**:

- Apply every AI-inferred field to BOSS search: rejected because inference would become an accidental hard gate.
- Rank list summaries without details: rejected because it cannot support evidence explanations or accurate gaps.

## Decision 7: Three-state hard rules

**Decision**: Hard-rule evaluation returns `pass`, `violation`, or `unknown`. A known violation rejects; unknown prevents `high_match` and routes to `needs_review` when material.

**Rationale**: Existing lenient pass-on-missing behavior avoids false rejection but can falsely imply compliance. Three states preserve recall without fabricating satisfaction.

**Alternatives considered**:

- Missing equals pass: rejected because SC-007 forbids incomplete jobs in high match.
- Missing equals violation: rejected because it would over-reject jobs with incomplete source fields.

## Decision 8: Per-direction assessment and versioned policy

**Decision**: Store one assessment per job snapshot and direction. AI proposes structured dimensions/evidence; a versioned program policy assigns the category.

**Rationale**: A job can be high match for one direction and growth for another. Policy versioning makes historical results reproducible and enables golden-set calibration.

**Alternatives considered**:

- One score per job: rejected because it collapses direction semantics.
- AI free-form final category: rejected because AI cannot own workflow state and explanations would be hard to validate.

## Decision 9: Run-scoped detail snapshots

**Decision**: Keep canonical `jobs` identity but persist the detail used by each discovery run, including completeness, source status, fetch time and content hash.

**Rationale**: The global job row can change. Historical evaluation and explanation must retain what was actually assessed.

**Alternatives considered**:

- Evaluate directly from the mutable `jobs.jd`: rejected because old results could no longer be reproduced.
- Duplicate a new global job per observation: rejected because it breaks canonical identity and feedback continuity.

## Decision 10: Database-authoritative checkpoints

**Decision**: Persist stage, query page, detail item and assessment status in SQLite. Validate scraper artifacts against a full input hash before import.

**Rationale**: Current list resume checks only keyword, detail resume is incomplete, and file artifacts can disappear or mismatch. Database checkpoints allow reliable cancel/restart/partial success.

**Alternatives considered**:

- Continue file-only `last_completed_page`: rejected because it cannot represent multi-query/detail/assessment progress.
- Automatically resume every interrupted process on startup: rejected because login/source state may not be safe; mark interrupted and require controlled resume.

## Decision 11: Additive migration and compatibility UI

**Decision**: Add schema migrations 011–013. Do not rewrite legacy rows. New discovery becomes default only after acceptance; old flows remain accessible as labeled history/advanced compatibility.

**Rationale**: Existing user data and current uncommitted work must be preserved. A clean new aggregate is safer than semantic backfill.

**Alternatives considered**:

- Delete old flows immediately: rejected due to data and regression risk.
- Present both as equal default choices: rejected because it preserves the current confusing double journey.

## Decision 12: Feedback scope is explicit

**Decision**: Keep existing job interest/trash state. Add structured discovery feedback with target type, reason and explicit scope; default scope is the exact job.

**Rationale**: Feedback should improve future runs without silently excluding a company, industry or direction.

**Alternatives considered**:

- Reuse free-text reason only: rejected because impact cannot be validated or reversed safely.
- Automatically generalize every rejection: rejected by spec and user control requirements.

## Decision 13: Privacy deletion propagates to derived explanations

**Decision**: Deleting a resume removes extracted text, evidence, candidate models, directions and evidence-backed explanation payloads. Canonical jobs and durable user feedback remain, marked as having no available historical explanation.

**Rationale**: Derived evidence can reveal the resume even when the original file is deleted. Job identity and explicit user actions are separate long-term data.

**Alternatives considered**:

- Keep all derived analysis: rejected because deletion would be misleading.
- Delete all historical jobs/feedback: rejected because it violates existing long-term state expectations.

## Decision 14: Layered verification with a real E2E gate

**Decision**: Require contract, unit, migration, integration, golden-set, browser and bounded real-source E2E layers. Treat `--smoke-test` as connectivity only.

**Rationale**: Matching quality and source behavior cannot be proven by static or fixture tests, while real-source tests alone are too unstable for regression coverage.

**Alternatives considered**:

- Only full live E2E: rejected because it is slow, stateful and hard to reproduce.
- Only fixture/integration tests: rejected because they cannot validate real BOSS details or browser rendering.

## Decision 15: Keep the discovery provider behind `webui.ai`

**Decision**: Implement one public discovery provider in the existing AI boundary. The provider receives resolved endpoint, model and API key values, exposes candidate analysis and job assessment operations, and has no store or workflow-state dependency.

**Rationale**: The original plan already assigns provider variation, credentials and safe transport errors to `webui.ai`. Adding a parallel provider module would split one responsibility without a second provider architecture. Passing `TaskStore` into the provider would also let a transport adapter mutate workflow state.

**Alternatives considered**:

- New `webui/ai_provider.py`: rejected because it duplicates the planned AI boundary and complicates credential/error ownership.
- Private adapter nested in the HTTP module: rejected because it is not reusable by the discovery runner and is hard to test at the real composition seam.
- Provider owns analysis/assessment persistence: rejected because AI must not own final state advancement.

## Decision 16: Resolve evidence positions deterministically

**Decision**: Candidate-analysis v2 asks the model for a minimal exact source quote, not authoritative offsets. Program code canonicalizes the immutable extracted resume text, resolves a unique exact quote, generates `start/end`, and verifies the resolved slice before evidence validation.

**Rationale**: Model-generated numeric offsets are unreliable and the current range-only check can accept an unrelated in-range slice. Exact quote resolution is deterministic, testable and keeps the final locator tied to the text actually analyzed.

**Alternatives considered**:

- Trust model offsets after range checking: rejected because range validity does not prove evidence correspondence.
- Locate only by `normalized_value`: rejected because normalized values often do not appear as a contiguous original substring.
- Fuzzy-match and choose the highest score: rejected because ambiguous evidence would be converted into an unconfirmed fact.
- Persist evidence without a locator: rejected for explicit evidence because it cannot satisfy traceability; inferred/unknown information must stay separately typed.

## Decision 17: Use a complete sanitized assessment view

**Decision**: Build each job-direction assessment input from the persisted candidate summary, selected direction metadata, linked evidence IDs/normalized values/safe excerpts/assertion types, and one sanitized job snapshot. Job evidence references use the exact supplied snapshot field keys.

**Rationale**: IDs alone carry no meaning for the model. Loading only direction-linked evidence preserves minimum necessary disclosure while making returned references verifiable against the exact call input.

**Alternatives considered**:

- Send only direction name and evidence IDs: rejected because the model cannot interpret the IDs.
- Send the full resume again for every job: rejected due to privacy, cost and inability to constrain evidence references.
- Send every analysis evidence item: rejected because unrelated evidence exceeds the minimum necessary scope.

## Decision 18: One persisted application task runtime

**Decision**: Candidate analysis and discovery runs are submitted through an application-owned runtime in the discovery orchestration boundary. In-process futures and cancellation signals are ephemeral; database states, events and checkpoints remain authoritative. Restart converts abandoned active work to `interrupted` and requires controlled resume.

**Rationale**: AI and real-source work can outlive one request or page. Creating rows without dispatching work leaves the user in a false started state, while direct status edits on cancel/resume do not control execution.

**Alternatives considered**:

- Execute all AI/source work inside HTTP requests: rejected because long operations would not survive navigation and are difficult to cancel or recover.
- Let the AI provider advance states: rejected because program-owned checks must control progression.
- Automatically resume every task on restart: rejected because provider credentials and source login state may no longer be valid.

## Decision 19: Preserve feature-level failure identity

**Decision**: Translate transport/provider failures once into `ai_auth_failed`, `ai_timeout`, `ai_network_error`, `ai_invalid_output`, `ai_uncertain` or `evidence_reference_invalid`. Candidate analysis fails as one attempt; a job-assessment failure is persisted on that assessment and routes the job to `needs_review` while other jobs continue.

**Rationale**: Collapsing all provider failures into invalid output destroys retry policy and operator diagnosis. Returning raw exceptions risks leaking sensitive input.

**Alternatives considered**:

- Catch every exception as `ai_invalid_output`: rejected because auth/network/timeout have different retry and user-action boundaries.
- Persist raw exception text: rejected by the privacy and safe-error contract.
- Return partial candidate models as ready: rejected because cross-references and direction gates cannot be trusted after a failed response.

## Decision 20: Test the composition seam, not only injected fakes

**Decision**: Domain tests may use fake providers, but HTTP and runtime integration tests instantiate the real discovery provider and mock only the network transport. Release acceptance additionally requires an opt-in live-provider contract smoke and the bounded real-source E2E through the actual user route.

**Rationale**: A fake implementing the desired methods cannot detect a missing provider class, missing credential construction, incorrect error mapping or a run that was persisted but never dispatched.

**Alternatives considered**:

- Continue fake-provider-only integration: rejected because it bypasses the exact missing seam.
- Live endpoint in every CI run: rejected because it is stateful, externally billed/limited and non-deterministic.
- Live request with no assertions: rejected because connectivity alone does not prove either feature contract.

## Resolved risks

- Dual legacy run models: isolated behind compatibility boundaries.
- Wrong checkpoint reuse: full input hash and database cursor.
- Missing JD fields: explicit completeness and three-state rules.
- AI hallucinated evidence: reference validation against stored evidence and snapshot fields.
- Browser-only resume state: server-side current run lookup; local storage becomes a convenience, not authority.
- Historical result drift: immutable confirmation, snapshot and policy versions.

## Remaining implementation-time observations

These are not specification clarifications; they are tasks to verify during implementation:

- Confirm the actual deployed schema version before applying migration 011.
- Inspect the existing `jobs` self-referencing foreign key anomaly before relying on foreign-key introspection.
- Calibrate adjacent/growth thresholds against the golden set before declaring matching success.
- Re-run all current tests because historical counts are not current evidence.
- Re-check BOSS login and source behavior immediately before the real E2E.
