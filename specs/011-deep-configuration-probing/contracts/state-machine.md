# State Machine Contract: 高级设置深度自动调优

**Date**: 2026-07-29

## 1. Experiment States

| State | Meaning | New pressure work allowed |
|---|---|---|
| `draft` | input is editable and unconfirmed | No |
| `preflight` | frozen input and environment are being checked | No |
| `awaiting_instruction` | controller may issue exactly one manifest | No |
| `queued` | issued manifest waits for exclusive executor | No |
| `running` | one round owns the lease | Only that round |
| `evaluating` | program report accepted; controller decision pending | No |
| `blocked` | known blocker or unknown executor condition | No |
| `completed` | all final gates passed; candidate version available | No |
| `failed` | unrecoverable internal experiment failure | No |
| `cancelled` | user ended experiment | No |

Legal transitions:

```text
draft → preflight → awaiting_instruction → queued → running → evaluating
          │               ↑                           │          │
          └→ blocked ─────┘                           │          ├→ awaiting_instruction
                                                     │          └→ completed
                                                     ├→ blocked → awaiting_instruction
                                                     ├→ failed
                                                     └→ cancelled

draft/preflight/awaiting_instruction/queued/blocked → cancelled
```

Rules:

- `running` requires one issued manifest, one round and the exclusive lease.
- `completed` requires a complete nine-slot candidate version and all required final rounds confirmed.
- `failed`, `cancelled` and `completed` are terminal.
- A restart never changes an accepted round to unconfirmed.

## 2. Round States

| State | Meaning | Counts toward candidate metrics |
|---|---|---|
| `planned` | round identity reserved | No |
| `issued` | immutable manifest issued | No |
| `running` | child execution active | No |
| `reported` | child terminal, executor report pending validation | No |
| `confirmed` | program evidence and executor report accepted | Yes |
| `uncertain` | interrupted before atomic confirmation | No |
| `blocked` | known external/unknown executor blocker | No |
| `invalid` | contract/evidence mismatch | No |
| `cancelled` | user cancellation | No |

```text
planned → issued → running → reported → confirmed
                      │          ├→ invalid
                      │          └→ blocked
                      ├→ uncertain → planned (new repetition attempt)
                      ├→ blocked
                      └→ cancelled
```

An uncertain round is never overwritten. A replacement attempt receives a new round/manifest identity or an incremented repetition-attempt identity while preserving audit history.

## 3. Error Handling

### Immediate hard stop

- login expired or interactive verification required;
- source-wide block/risk control;
- input/config/reference digest mismatch;
- missing, duplicated or incorrectly mapped terminal items;
- result persistence/checkpoint failure;
- quality difference beyond confirmed reference tolerance;
- measurement/report artifact missing or invalid;
- lease ownership loss or state corruption.

Action: stop starting new work, settle in-flight work where possible, persist program evidence, set round/experiment blocked or invalid, return a blocked report. Do not retry the same dangerous configuration automatically.

### Recoverable condition

- transient timeout/network/server response;
- rate-limit signal classified as recoverable by the exact manifest;
- one allowed transport retry;
- one output truncation split prescribed by the existing health policy.

Action: only the manifest's named retry may run. Retry, backoff and wait durations count toward total duration. When the numeric/window threshold is exceeded, transition to blocked.

## 4. Lease Rules

1. Preflight checks no conflicting active ordinary task or tuning round.
2. Issue transaction claims the singleton lease for experiment/round.
3. Heartbeat extends a bounded lease while child work is active.
4. No other task may enqueue pressure work while lease is owned.
5. Normal completion/report confirmation releases the round ownership.
6. On restart, stale `running` rounds become `uncertain`; lease is reclaimed only after state reconciliation.
7. A worker process or executor AI never owns the database lease directly; the application owns it on behalf of the round.

## 5. Runtime Mode Switch

Task scope is immutable for the whole task. A mode switch after repeated recoverable errors follows:

```text
pause new work
→ persist current segment and completed items
→ user selects keep/retry, another mode, or end
→ validate which fields may change at next safe node
→ create a new config segment snapshot for remaining work
→ resume without reprocessing completed items
```

The application never auto-downgrades. In-flight batches retain their original config digest. Cumulative fields that require a session reset become effective only after the named reset/stage boundary.

## 6. Mode Version States

```text
candidate → active → superseded
               ↑          │
               └──────────┘  (explicit whole-version rollback)
```

- A candidate cannot become active unless its source experiment is completed.
- Activation switches all nine slots in one transaction.
- Rollback selects a prior complete version; it does not create a mixed version.
- Recent custom config is independent and never changes during mode activation/rollback.
