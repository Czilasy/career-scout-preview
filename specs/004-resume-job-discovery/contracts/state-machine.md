# State Machine Contract

## Candidate analysis

```text
queued → analyzing → ready
                 └→ failed
ready/failed → deleted   # resume deletion
```

- Only `ready` can create a confirmation.
- Retry creates a new analysis version; it does not mutate a failed result into a different historical result.
- `queued` means the immutable attempt exists and has been accepted for dispatch. Dispatch/provider-construction failure transitions to `failed` with a safe code; it may not remain queued indefinitely without an event.
- Only the application runtime advances `queued → analyzing`; the AI provider returns data/errors but never writes state.

## Discovery run

```text
created
  → planning
  → fetching_lists
  → fetching_details
  → evaluating
  → assembling
  → succeeded
```

- `created` is a transient persisted state while dispatch is being accepted. The application runtime must advance it to `planning` or persist `failed` with `failure_stage=planning` and a safe dispatch code within the configured acceptance window.
- Creating the run and search plan without submitting executable work is not a valid start.

Any active stage may transition to:

- `partial`: some independent work completed, at least one branch failed, usable results exist.
- `failed`: no usable result and the required stage cannot continue.
- `interrupted`: process/source was interrupted; checkpoints are retained and resume may be offered.
- `cancelled`: user requested cancellation; no new work may start, saved results remain.

Allowed resume transition:

```text
interrupted → last saved active stage
partial → last saved active stage  # only while retryable work remains
```

Resume is valid only after the runtime accepts the unfinished work for execution. Directly changing the persisted status without submitting work is forbidden.

Terminal `succeeded`, `failed`, and `cancelled` runs are immutable. A new user input creates a new run.

Cancellation sequence:

```text
active stage → cancel_requested → cancelled
```

- Persist `cancel_requested_at` first, signal the active worker, and skip every not-yet-started work unit.
- Saved completed work remains readable. No new query, detail fetch or assessment may begin after the cancellation checkpoint.
- If dispatch/cancellation signaling fails, persist the safe failure event; do not claim cancellation succeeded solely because a display status changed.

## Search item

```text
queued → fetching → completed
                 ├→ failed
                 └→ cancelled
failed → queued  # explicit bounded retry
```

Resume is allowed only when stored `input_hash` equals the current plan item hash. Page cursor advances after an atomic page import.

## Job detail snapshot

```text
queued → fetching → completed
                 ├→ failed
                 └→ cancelled
failed → queued  # explicit retry
```

One failed detail never changes another snapshot state. `completed` may still have `completeness=partial`.

## Direction assessment

```text
queued → evaluating → completed
                   ├→ pending
                   └→ failed
pending/failed → queued  # retry when input/provider becomes available
```

- Known hard violation may complete without AI and forces `not_suitable`.
- Unknown hard field, missing detail, AI timeout, invalid output or low confidence produces `pending/needs_review`.
- AI authentication, timeout, network, invalid output, uncertainty and evidence-reference failures retain distinct safe `failure_code` values.
- Only program policy writes the final category.

## Run completion calculation

- `succeeded`: every required plan item, selected detail and assessment is terminal; no required pending item remains.
- `partial`: at least one usable result exists and at least one planned branch could not complete.
- `failed`: no usable result exists and all remaining paths are blocked/non-retryable.
- “No matching jobs” may still be `succeeded` if all planned work completed normally.
- A run with no stage-entry/work-unit events after creation cannot be `succeeded`.
