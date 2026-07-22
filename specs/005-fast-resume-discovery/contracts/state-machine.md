# State Machine Contract: Discovery Policy v2

## Run States

### Nonterminal

- `created`
- `planning`
- `fetching_lists`
- `prioritizing`
- `processing_jobs`
- `assembling`
- `interrupted`

### Terminal

- `succeeded`
- `partial`
- `failed`
- `cancelled`

### Valid transitions

```text
created -> planning
planning -> fetching_lists | failed | cancelled | interrupted
fetching_lists -> prioritizing | partial | failed | cancelled | interrupted
prioritizing -> processing_jobs | succeeded | partial | failed | cancelled | interrupted
processing_jobs -> assembling | partial | failed | cancelled | interrupted
assembling -> succeeded | partial | failed
interrupted -> planning | fetching_lists | prioritizing | processing_jobs
```

- Terminal states never return to nonterminal.
- Resume is an explicit guarded action from `interrupted` or eligible `partial` and validates all input hashes.
- Legacy v1 `fetching_details`/`evaluating` states remain readable; only v2 runs enter new stages.

## Candidate Work-unit States

```text
discovered
  -> prechecked_pass | prechecked_unknown | excluded
prechecked_pass | prechecked_unknown
  -> selected | deferred
selected
  -> detail_fetching | detail_reused | cancelled
detail_fetching
  -> detail_ready | detail_failed | cancelled
detail_reused
  -> detail_ready
detail_ready
  -> evaluating | needs_review | unsuitable
evaluating
  -> recommended | needs_review | unsuitable | evaluation_failed | cancelled
recommended | needs_review | unsuitable
  -> reordered | withdrawn
```

Every mutation requires `expected_state` and `input_hash`. A rejected compare-and-set is a no-op plus safe conflict event, not a duplicate external call.

## Producer / Consumer Boundaries

### Detail producer

- One default source producer; batches contain at most 5 selected candidates.
- Each job emits one terminal safe event: completed, unavailable, failed or cancelled.
- Output event contains run candidate id/job id, artifact identity, duration and safe code; never JD body.
- The producer stops enqueueing when cancellation or circuit breaker is set.

### Parallel detail fetching (spec 007 ⑧)

- Opt-in via `--enable-parallel`; default off preserves the serial contract above.
- When enabled, a resident tab pool (default 3 tabs, configurable via `--tab-pool-size`) reuses attached tabs across jobs to avoid per-job `Target.createTarget` / `Target.closeTarget` overhead.
- Tabs start staggered (random delay within `--stagger-min..--stagger-max`, default 5-10s) to avoid simultaneous navigation spikes.
- Job order within a batch is shuffled before enqueueing to decorrelate request patterns.
- A `degrade_event` short-circuits remaining workers when any tab observes a login wall; workers stop claiming new jobs and the batch surfaces per-job outcomes (login_required / unavailable) without retry storm.
- The producer contract is unchanged: each job still emits exactly one terminal safe event; event fields, validation rules, privacy boundaries and the 5-job batch cap all hold identically in parallel mode.
- The circuit breaker feeds on per-job outcomes the same way regardless of mode; two login_required outcomes in the same parallel batch open the breaker as they would serially.

### Assessment consumer

- One default AI consumer runs concurrently with detail production.
- It accepts only persisted `detail_ready` units.
- One group contains one job and at most two relevant directions.
- Completed same-input assessment rows are skipped on resume.

### Recommendation projector

- Runs after each assessment transaction and feedback visibility change.
- If visible result snapshot changes, increments `result_revision` in the same transaction/event boundary.
- It does not own external work or terminal run transitions.

## Cancellation

1. API writes `cancel_requested_at` and sets shared cancellation signal.
2. No new list/detail/AI work may start after the request sequence.
3. Active source subprocess receives cancellation and is terminated through the existing process-tree mechanism.
4. Queued nonterminal candidates/assessments become cancelled.
5. Completed candidates/snapshots/assessments remain unchanged.
6. Run reaches `cancelled` after active unit termination or safe 30-second deadline handling.

## Circuit Breaker

Breaker opens after two consecutive source signals from:

- login wall / authentication loss
- verification page
- explicit rate-limit response
- repeated invalid navigation shell attributable to source blocking

When open:

- no new source job starts;
- queued work stays retryable/blocked rather than failed as user fault;
- active AI work for already completed details may finish unless user cancelled;
- run becomes partial when usable results exist, otherwise failed/blocked with safe source code;
- automatic restart requires preflight success and bounded cooldown; no unbounded retry loop.

## Resume

Resume rebuilds state from SQLite only:

1. validate run/confirmation/profile version/policy hashes;
2. rebuild list candidate pool from `discovery_run_candidates`, never infer run id from artifact filename;
3. skip candidate snapshot when same run + same input hash is terminal;
4. reuse cross-run snapshot only through freshness rules;
5. skip completed assessment with same input hash;
6. requeue retryable nonterminal/failed units once;
7. reconcile counters from persisted rows before dispatch.

Acceptance: completed detail and assessment external-call duplicates = 0.

## Completion

### succeeded

- all required selected candidates terminal;
- all required assessments terminal;
- no unresolved system/source blocker;
- zero recommendations is allowed only when all work completed normally and a typed no-result reason exists.

### partial

- at least one usable result exists;
- one or more selected candidates/directions cannot complete after bounded retry or source breaker;
- incomplete units and safe reasons remain visible/retryable.

### failed

- no usable result;
- required input invalid, candidate pool absent due system defect, or all work blocked/failed;
- failure stage/code persisted.

### cancelled

- user cancellation accepted;
- no new work after cancel sequence;
- completed work retained.

## Required Events

- `run_submitted`, `stage_entered`
- `list_candidate_upserted`, `candidate_prechecked`, `candidate_selected`, `candidate_deferred`
- `detail_batch_started`, `detail_started`, `detail_completed`, `detail_failed`, `detail_reused`
- `assessment_group_started`, `assessment_completed`, `assessment_failed`
- `recommendation_revision_changed`, `first_result_visible`, `first_batch_visible`
- `progress_reconciled`
- `source_breaker_opened`, `source_breaker_closed`
- `cancel_requested`, `work_skipped_after_cancel`, `run_cancelled`
- `run_interrupted`, `resume_accepted`, `resume_rejected`
- `run_completed`

Safe event payloads may include ids, counts, durations, concurrency, revisions and safe codes. They exclude resume/JD bodies, prompts, outputs and credentials.

